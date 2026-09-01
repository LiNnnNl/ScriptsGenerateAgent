"""Lightweight Director-only pipeline for Word storyboard generation."""

import json
import logging
import re
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import registry as _registry
from .autogen_agents import create_director_word_agent, create_shot_plan_agent
from .autogen_bridge import AutoGenStreamBridge
from .autogen_pipeline import (
    _emit_output,
    _emit_stage_log,
    _generate_script_title,
    _run_director_agent,
    _run_stage_agent_json_object,
)
from .autogen_tools import auto_fix_script, validate_script_constraints
from .json_generator import ScriptJSONGenerator
from .position_metadata import normalize_position_metadata
from .prompt_files.director_word_user import director_word_user_prompt
from .prompt_utils import render_prompt
from .resource_loader import ResourceLoader, Scene
from .script_style_skill import ScriptStyleSkill
from .script_tone_skill import ScriptToneSkill
from .word_exporter import export_script_to_word

logger = logging.getLogger(__name__)

_SHOT_ROW_RE = re.compile(r"^\s*(S\d{1,4})\s+(.+)$", re.IGNORECASE)
_SINGLE_REQUEST_SHOT_LIMIT = 12
_SHOT_BATCH_FORMAT_RETRIES = 2


def _parse_shot_rows(text: str) -> tuple[str, List[Dict[str, str]]]:
    """Extract rows with stable shot IDs while retaining the original row text verbatim."""
    context_lines: List[str] = []
    rows: List[Dict[str, str]] = []
    found_first_row = False
    for raw_line in (text or "").splitlines():
        match = _SHOT_ROW_RE.match(raw_line)
        if match:
            found_first_row = True
            rows.append({"id": match.group(1).upper(), "raw": raw_line.strip()})
        elif not found_first_row:
            context_lines.append(raw_line)
    return "\n".join(context_lines).strip(), rows


def _build_balanced_shot_plan(shot_ids: List[str], act_count: int) -> List[List[str]]:
    base_size, remainder = divmod(len(shot_ids), act_count)
    plan: List[List[str]] = []
    start = 0
    for act_index in range(act_count):
        size = base_size + (1 if act_index < remainder else 0)
        plan.append(shot_ids[start:start + size])
        start += size
    return plan


def _validate_shot_plan(plan_data: Any, shot_ids: List[str], act_count: int) -> Optional[List[List[str]]]:
    acts = plan_data.get("acts") if isinstance(plan_data, dict) else None
    if not isinstance(acts, list) or len(acts) != act_count:
        return None
    grouped: List[List[str]] = []
    for act in acts:
        ids = act.get("shot_ids") if isinstance(act, dict) else None
        if not isinstance(ids, list) or not ids:
            return None
        grouped.append([str(shot_id).upper().strip() for shot_id in ids])
    flattened = [shot_id for group in grouped for shot_id in group]
    return grouped if flattened == shot_ids else None


def _split_batches(rows: List[Dict[str, str]], batch_size: int) -> List[List[Dict[str, str]]]:
    return [rows[index:index + batch_size] for index in range(0, len(rows), batch_size)]


def _merge_batch_scene(target: Optional[Dict[str, Any]], batch_scene: Dict[str, Any], expected_ids: List[str]) -> Optional[Dict[str, Any]]:
    segments = [dict(segment) for segment in (batch_scene.get("scene") or []) if isinstance(segment, dict)]
    if len(segments) != len(expected_ids):
        return None

    returned_ids = [str(segment.pop("source_shot_id", "")).upper().strip() for segment in segments]
    if returned_ids != expected_ids:
        return None

    if target is None:
        target = {
            "position_metadata": normalize_position_metadata(batch_scene),
            "scene information": dict(batch_scene.get("scene information") or {}),
            "initial position": deepcopy(batch_scene.get("initial position") or []),
            "scene": [],
        }
    else:
        target.setdefault("position_metadata", {}).update(
            normalize_position_metadata(batch_scene)
        )
    target["scene"].extend(segments)
    return target


def _extract_user_constraints(text: str) -> List[str]:
    pattern = re.compile(
        r'不要[^\n。，、]{2,60}?(?:[，。]|\n|$)|'
        r'必须[^\n。，、]{2,60}?(?:[，。]|\n|$)|'
        r'不能[^\n。，、]{2,60}?(?:[，。]|\n|$)|'
        r'应当[^\n。，、]{2,60}?(?:[，。]|\n|$)',
        re.IGNORECASE,
    )
    constraints: List[str] = []
    seen = set()
    for match in pattern.finditer(text or ""):
        item = match.group().strip()
        if len(item) >= 4 and item not in seen:
            seen.add(item)
            constraints.append(item)
    return constraints


def _resolve_scene_pool(resource_loader: ResourceLoader, request_params: Dict[str, Any], bridge: AutoGenStreamBridge) -> tuple[List[Scene], Scene]:
    scene_id = request_params.get("scene_id")
    scene_pool_ids = request_params.get("scene_pool") or []
    if not isinstance(scene_pool_ids, list):
        scene_pool_ids = []
    scene_pool_ids = [str(s).strip() for s in scene_pool_ids if str(s).strip()]
    if not scene_pool_ids and scene_id:
        scene_pool_ids = [scene_id]
    if not scene_pool_ids:
        bridge.put_event({"type": "error", "message": "未提供场景：请至少选择一个场景"})
        return [], None

    scenes: List[Scene] = []
    for sid in scene_pool_ids:
        scene = resource_loader.get_scene_by_id(sid)
        if not scene:
            bridge.put_event({"type": "error", "message": f"场景不存在: {sid}"})
            return [], None
        scenes.append(scene)
    return scenes, scenes[0]


def _build_act_scene_map(request_params: Dict[str, Any], scene_pool_objs: List[Scene], default_scene: Scene, act_count: int) -> Dict[int, Scene]:
    act_scenes_ids = request_params.get("act_scenes") or []
    if not isinstance(act_scenes_ids, list):
        act_scenes_ids = []
    pool_by_id = {scene.id: scene for scene in scene_pool_objs}
    act_scene_map: Dict[int, Scene] = {}
    for index in range(act_count):
        sid = str(act_scenes_ids[index]).strip() if index < len(act_scenes_ids) and act_scenes_ids[index] else ""
        act_scene_map[index] = pool_by_id.get(sid, default_scene)
    return act_scene_map


async def _run_director_word_shot_batches(
    bridge: AutoGenStreamBridge,
    resource_loader: ResourceLoader,
    characters: list,
    required_character_count: int,
    act_count: int,
    act_scene_map: Dict[int, Scene],
    user_constraints: List[str],
    script_style_guide: str,
    source_context: str,
    shot_rows: List[Dict[str, str]],
) -> Optional[list]:
    """Plan acts from stable shot IDs, enrich small batches, and merge in source order."""
    shot_ids = [row["id"] for row in shot_rows]
    planner = create_shot_plan_agent()
    plan_prompt = (
        f"目标幕数：{act_count}\n"
        "以下是用户原始镜号表。只规划每一幕包含哪些镜号：\n\n"
        + "\n".join(f"{row['id']}\t{row['raw']}" for row in shot_rows)
    )
    _emit_stage_log(bridge, "info", "director_word", "shot_plan", "🧭 正在按镜号规划幕结构...")
    plan_result = await _run_stage_agent_json_object(planner, plan_prompt)
    act_shot_ids = _validate_shot_plan(plan_result, shot_ids, act_count)
    if act_shot_ids is None:
        act_shot_ids = _build_balanced_shot_plan(shot_ids, act_count)
        _emit_stage_log(
            bridge, "warning", "director_word", "shot_plan_fallback",
            "⚠️ 镜号规划未通过校验，已按原始顺序均衡分配到各幕。",
        )
    else:
        _emit_stage_log(bridge, "success", "director_word", "shot_plan_done", "✅ 镜号幕结构规划完成。")

    rows_by_id = {row["id"]: row for row in shot_rows}
    merged_acts: List[Optional[Dict[str, Any]]] = [None] * act_count
    completed_shots = 0
    batch_size = 8 if len(shot_rows) > 30 else 6

    for act_index, act_ids in enumerate(act_shot_ids):
        act_rows = [rows_by_id[shot_id] for shot_id in act_ids]
        batches = _split_batches(act_rows, batch_size)
        act_scene = act_scene_map[act_index]
        for batch_index, batch_rows in enumerate(batches, start=1):
            expected_ids = [row["id"] for row in batch_rows]
            batch_prompt = (
                "用户已提供原始镜号表。请只结构化并补全下列镜头，不得新增、删除、改写、合并或调整镜号顺序。\n"
                "每个输入镜头必须对应输出 scene 数组中的一个片段；保留原始台词与画面含义。\n"
                "每个片段都额外写入 source_shot_id，值必须等于对应镜号。\n"
                "直接输出一个 JSON 数组，且只能包含 1 个 scene_obj；不要输出解释、思考过程或 Markdown 代码块。\n"
                "输出必须能被标准 JSON 解析：所有字符串使用双引号；原台词中的双引号必须写成 \\\"，不能直接嵌入字符串。\n\n"
                f"当前为第 {act_index + 1}/{act_count} 幕，第 {batch_index}/{len(batches)} 批，场景为：{act_scene.name}\n"
                + (f"文档说明：\n{source_context}\n\n" if source_context else "")
                + "本批原始镜头：\n"
                + "\n".join(row["raw"] for row in batch_rows)
            )
            label = f"DirectorAgent_Word（第{act_index + 1}幕，第{batch_index}/{len(batches)}批）"
            _emit_stage_log(
                bridge, "info", "director_word", "shot_batch_start",
                f"🎬 正在处理第 {act_index + 1} 幕，第 {batch_index}/{len(batches)} 批镜头（{', '.join(expected_ids)}）。",
            )
            batch_scene = None
            last_candidate = None
            merged = None
            for format_attempt in range(_SHOT_BATCH_FORMAT_RETRIES + 1):
                attempt_label = label if format_attempt == 0 else f"{label}（格式重试 {format_attempt}/{_SHOT_BATCH_FORMAT_RETRIES}）"
                attempt_prompt = batch_prompt
                if format_attempt:
                    attempt_prompt += (
                        "\n\n上一次输出未通过 JSON 或镜号校验。请从头重新输出完整结果。"
                        "只返回 JSON 数组，不要 Markdown；尤其要把台词内容中的双引号转义为 \\\"。"
                    )
                    _emit_stage_log(
                        bridge, "warning", "director_word", "shot_batch_retry",
                        f"⚠️ 第 {act_index + 1} 幕，第 {batch_index}/{len(batches)} 批格式校验未通过，正在自动重试（{format_attempt}/{_SHOT_BATCH_FORMAT_RETRIES}）。",
                    )
                batch_director = create_director_word_agent(
                    characters,
                    act_scene,
                    resource_loader,
                    required_character_count=required_character_count,
                    act_count=1,
                    user_constraints=user_constraints,
                    act_scene_map={0: act_scene},
                    script_style_guide=script_style_guide,
                )
                batch_result = await _run_director_agent(batch_director, attempt_prompt, bridge, attempt_label)
                candidate = batch_result[0] if isinstance(batch_result, list) and len(batch_result) == 1 else None
                last_candidate = candidate
                merged = _merge_batch_scene(merged_acts[act_index], candidate or {}, expected_ids)
                if merged is not None:
                    batch_scene = candidate
                    break
            if merged is None:
                details = dict(getattr(bridge, "last_error_details", None) or {})
                received_segments = (last_candidate or {}).get("scene") or []
                received_ids = [
                    str(segment.get("source_shot_id", "")).upper().strip()
                    for segment in received_segments
                    if isinstance(segment, dict)
                ]
                if last_candidate is not None:
                    details.update({
                        "error_type": "BatchShotValidationFailed",
                        "received_shot_ids": received_ids,
                    })
                details.update({
                    "act": act_index + 1,
                    "batch": batch_index,
                    "batch_total": len(batches),
                    "expected_shot_ids": expected_ids,
                    "received_segment_count": len(received_segments),
                })
                bridge.put_event({
                    "type": "error",
                    "message": f"第 {act_index + 1} 幕第 {batch_index} 批镜头未能通过镜号与数量校验。",
                    "details": details,
                })
                return None
            merged_acts[act_index] = merged
            completed_shots += len(expected_ids)
            partial_scenes = [deepcopy(item) for item in merged_acts if item is not None]
            bridge.put_event({
                "type": "partial_result",
                "mode": "director_word",
                "act": act_index + 1,
                "batch": batch_index,
                "batch_total": len(batches),
                "completed_shots": completed_shots,
                "total_shots": len(shot_rows),
                "scenes": partial_scenes,
                "latest_scenes": [deepcopy(batch_scene)],
            })
            _emit_stage_log(
                bridge, "success", "director_word", "shot_batch_complete",
                f"✅ 第 {act_index + 1} 幕，第 {batch_index}/{len(batches)} 批完成；已处理 {completed_shots}/{len(shot_rows)} 镜。",
            )

    return [item for item in merged_acts if item is not None]


async def _run_director_word_per_act_fallback(
    base_prompt: str,
    bridge: AutoGenStreamBridge,
    characters: list,
    scene: Scene,
    resource_loader: ResourceLoader,
    required_character_count: int,
    act_count: int,
    user_constraints: List[str],
    act_scene_map: Optional[Dict[int, Scene]],
    script_style_guide: str,
) -> Optional[list]:
    """Keep long Word storyboards within the model output budget by generating one act at a time."""
    scenes: list = []
    for act_index in range(act_count):
        act_scene = (act_scene_map or {}).get(act_index, scene)
        director = create_director_word_agent(
            characters,
            act_scene,
            resource_loader,
            required_character_count=required_character_count,
            act_count=1,
            user_constraints=user_constraints,
            act_scene_map={0: act_scene} if act_scene_map else None,
            script_style_guide=script_style_guide,
        )
        label = f"DirectorAgent_Word（单幕降级，第{act_index + 1}/{act_count}幕）"
        _emit_stage_log(
            bridge, "warning", "director_word", "per_act_fallback",
            f"⚠️ [导演 Word 模式] 完整输出未形成有效 JSON，改为单幕生成：第 {act_index + 1}/{act_count} 幕。",
        )
        per_act_prompt = (
            f"{base_prompt}\n\n"
            "## 单幕生成约束\n"
            f"现在只生成第 {act_index + 1} 幕，场景为 {act_scene.name}。"
            "必须直接输出 JSON 数组，且数组只能包含 1 个 scene_obj；"
            "不要解释、规划、复述约束或生成其他幕。"
        )
        act_script = await _run_director_agent(director, per_act_prompt, bridge, label)
        if not act_script or len(act_script) != 1:
            details = dict(getattr(bridge, "last_error_details", None) or {})
            details.update({
                "act": act_index + 1,
                "expected_scene_count": 1,
                "received_scene_count": len(act_script) if act_script else 0,
                "fallback": "director_word_per_act_json",
            })
            bridge.put_event({
                "type": "error",
                "message": f"DirectorAgent Word 单幕降级在第 {act_index + 1} 幕仍未生成有效 JSON。",
                "details": details,
            })
            return None
        scenes.extend(act_script)
        _emit_stage_log(
            bridge, "success", "director_word", "per_act_complete",
            f"✅ [导演 Word 模式] 第 {act_index + 1}/{act_count} 幕已单独生成并加入合并结果。",
        )
    return scenes


async def run_director_word_pipeline(
    bridge: AutoGenStreamBridge,
    resource_loader: ResourceLoader,
    request_params: Dict[str, Any],
) -> None:
    """Generate a readable storyboard Word document using only DirectorAgent."""

    creative_idea = (request_params.get("creative_idea") or "").strip()
    if not creative_idea:
        bridge.put_event({"type": "error", "message": "请先输入剧本构想、文档内容或 idea"})
        return

    act_count = max(1, min(10, int(request_params.get("act_count", 3) or 3)))
    requested_script_style_id = str(request_params.get("script_style_id") or "").strip()
    requested_script_tone_id = str(request_params.get("script_tone_id") or "").strip()
    required_character_count = int(request_params.get("required_character_count", 0) or 0)
    custom_characters_input = request_params.get("custom_characters", []) or []

    scene_pool_objs, scene = _resolve_scene_pool(resource_loader, request_params, bridge)
    if not scene:
        return

    multi_scene = len(scene_pool_objs) > 1
    act_scene_map = _build_act_scene_map(request_params, scene_pool_objs, scene, act_count)
    act_scene_ids = [act_scene_map[i].id for i in range(act_count)] if multi_scene else None

    if custom_characters_input:
        characters = resource_loader.build_custom_characters(custom_characters_input)
        _emit_stage_log(bridge, "success", "director_word", "characters", f"✅ 已使用 {len(characters)} 个现有角色档案")
    else:
        characters = []
        _emit_stage_log(bridge, "info", "director_word", "characters", "💭 未提供角色档案，导演将按角色数量要求创作角色")

    user_constraints = _extract_user_constraints(creative_idea)
    if user_constraints:
        _emit_stage_log(bridge, "info", "director_word", "constraints", f"📌 检测到 {len(user_constraints)} 条显式约束")

    style_skill = ScriptStyleSkill()
    style_result = style_skill.resolve(creative_idea, requested_style_id=requested_script_style_id)
    script_style_guide = style_skill.render_context(creative_idea, requested_style_id=requested_script_style_id)
    selected_style = style_result.get("selected", {})
    style_source = "用户按钮选择" if style_result.get("source") == "user_button" else "创作灵感自动识别"
    _emit_stage_log(
        bridge,
        "info",
        "director_word",
        "script_style",
        f"🎞️ 导演已锁定剧本风格: {selected_style.get('name', '未明确指定')}"
        f"（{style_source}）",
    )

    tone_skill = ScriptToneSkill()
    tone_result = tone_skill.resolve(requested_script_tone_id)
    script_style_guide = script_style_guide + "\n\n" + tone_skill.render_context(requested_script_tone_id)
    selected_tone = tone_result.get("selected", {})
    if tone_result.get("explicit"):
        _emit_stage_log(
            bridge,
            "info",
            "director_word",
            "script_tone",
            f"🎭 已锁定剧情倾向: {selected_tone.get('name', '未指定')}（用户按钮选择）",
        )

    if multi_scene:
        desc = "、".join(f"第{i + 1}幕={act_scene_map[i].name}" for i in range(act_count))
        _emit_stage_log(bridge, "info", "director_word", "multi_scene", f"🎬 导演 Word 模式多场景分配：{desc}")

    _emit_stage_log(bridge, "info", "director_word", "start", "🎬 [导演 Word 模式] 只调用 DirectorAgent 生成可读分镜剧本...")

    prompt = render_prompt(director_word_user_prompt, creative_idea=creative_idea)
    source_context, shot_rows = _parse_shot_rows(creative_idea)
    if len(shot_rows) > _SINGLE_REQUEST_SHOT_LIMIT:
        batch_size = 8 if len(shot_rows) > 30 else 6
        _emit_stage_log(
            bridge, "info", "director_word", "shot_batch_mode",
            f"📦 检测到 {len(shot_rows)} 个镜头，启用镜头批处理（每批 {batch_size} 镜）。",
        )
        draft_script = await _run_director_word_shot_batches(
            bridge,
            resource_loader,
            characters,
            required_character_count,
            act_count,
            act_scene_map,
            user_constraints,
            script_style_guide,
            source_context,
            shot_rows,
        )
        if not draft_script:
            return
    else:
        director = create_director_word_agent(
            characters,
            scene,
            resource_loader,
            required_character_count=required_character_count,
            act_count=act_count,
            user_constraints=user_constraints,
            act_scene_map=act_scene_map if multi_scene else None,
            script_style_guide=script_style_guide,
        )
        draft_script = await _run_director_agent(director, prompt, bridge, "DirectorAgent_Word")

    if not draft_script and len(shot_rows) <= _SINGLE_REQUEST_SHOT_LIMIT:
        _emit_stage_log(
            bridge, "warning", "director_word", "per_act_fallback_start",
            "⚠️ [导演 Word 模式] 完整输出未形成有效 JSON，开始按幕生成并合并。",
        )
        draft_script = await _run_director_word_per_act_fallback(
            prompt,
            bridge,
            characters,
            scene,
            resource_loader,
            required_character_count,
            act_count,
            user_constraints,
            act_scene_map if multi_scene else None,
            script_style_guide,
        )
        if not draft_script:
            return

    # Enforce one character per logical slot before exposing the draft.
    draft_script = auto_fix_script(draft_script, scene, resource_loader)
    position_validation = validate_script_constraints(draft_script, scene, resource_loader)
    position_errors = [
        error for error in position_validation["errors"]
        if "共用同一站位" in error
    ]
    if position_errors:
        bridge.put_event({
            "type": "error",
            "message": "站位校验失败：同一镜头内多个角色不能使用同一个 Position。",
            "details": {"errors": position_errors},
        })
        return

    _emit_output(bridge, "DirectorAgent_Word", draft_script)

    timestamp = int(time.time())
    output_dir = Path("outputs")
    output_dir.mkdir(exist_ok=True)
    filename = f"script_director_{timestamp}.json"
    docx_filename = f"script_director_{timestamp}.docx"

    plot_summary = creative_idea[:120]
    generator = ScriptJSONGenerator(characters, scene)
    final_json = generator.generate_final_json(
        draft_script,
        plot_summary,
        preserve_shot_fields=True,
        act_scene_ids=act_scene_ids,
    )

    json_path = output_dir / filename
    generator.export_to_file(final_json, str(json_path))

    docx_path = output_dir / docx_filename
    export_script_to_word(final_json, docx_path)

    script_title = await _generate_script_title(final_json, bridge)
    session_id = str(timestamp)
    _registry.register_session(
        ts=session_id,
        files={"script": filename},
        scene_id=",".join(scene.id for scene in scene_pool_objs),
        act_count=act_count,
        label=script_title or "导演 Word 分镜",
    )
    _registry.update_word_export(session_id, docx_filename)

    logger.info("Director Word pipeline 完成 | json=%s word=%s", filename, docx_filename)
    bridge.put_event({
        "type": "success",
        "mode": "director_word",
        "filename": filename,
        "word_filename": docx_filename,
        "session_id": session_id,
        "title": script_title,
    })
