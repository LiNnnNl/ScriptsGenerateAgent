"""Lightweight Director-only pipeline for Word storyboard generation."""

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List

from . import registry as _registry
from .autogen_agents import create_director_word_agent
from .autogen_bridge import AutoGenStreamBridge
from .autogen_pipeline import _emit_output, _emit_stage_log, _generate_script_title, _run_director_agent
from .json_generator import ScriptJSONGenerator
from .prompt_files.director_word_user import director_word_user_prompt
from .prompt_utils import render_prompt
from .resource_loader import ResourceLoader, Scene
from .script_style_skill import ScriptStyleSkill
from .script_tone_skill import ScriptToneSkill
from .word_exporter import export_script_to_word

logger = logging.getLogger(__name__)


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
    prompt = render_prompt(director_word_user_prompt, creative_idea=creative_idea)
    draft_script = await _run_director_agent(director, prompt, bridge, "DirectorAgent_Word")

    if not draft_script:
        bridge.put_event({"type": "error", "message": "DirectorAgent 未能生成有效的 JSON 分镜剧本"})
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
