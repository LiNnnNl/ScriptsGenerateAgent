"""
AutoGen Pipeline 编排模块

实现完整的多 Agent 剧本生成流程：
DirectorAgent → 审查层（CriticAgent + DialogueAgent）→ ValidationAgent → OutputAgent

通过 AutoGenStreamBridge 将 Agent 对话事件实时推送给 Flask NDJSON 流。
"""

import asyncio
import json
import logging
import os
import re
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 网络错误关键词（用于区分连接抖动 vs 业务错误）
_NETWORK_ERR_KEYWORDS = (
    "connection", "timeout", "timed out", "network", "remotedisconnected",
    "connect error", "server disconnected", "remote protocol", "unexpected eof",
    "bad gateway", "service unavailable", "gateway timeout", "502", "503", "504",
)
_STAGE_MAX_RETRIES = 3       # 前置阶段 Agent 最大重试次数
_STAGE_RETRY_BASE_DELAY = 3  # 秒，每次翻倍
_DIRECTOR_MAX_CONTINUATIONS = 3  # 每次续写独立享有网络重试预算
_DIRECTOR_EVENT_BATCH_SIZE = 8
_DIRECTOR_BATCH_RETRIES = 2
_DIRECTOR_DIRECT_BATCH_SIZE = _DIRECTOR_EVENT_BATCH_SIZE
_DIRECTOR_DEFAULT_EVENTS_PER_ACT = 8
_DIRECTOR_MAX_EVENTS_TOTAL = 300
_DIRECTOR_MAX_SOURCE_EVENT_CHARS = 1200
_DIRECTOR_BATCH_SOURCE_CHAR_BUDGET = 3600
_CONTRACT_REPAIR_EVENT_BATCH_SIZE = 6

from autogen_agentchat.messages import TextMessage, ToolCallExecutionEvent, ModelClientStreamingChunkEvent
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_agentchat.conditions import MaxMessageTermination, TextMentionTermination
from autogen_core import CancellationToken

from .autogen_bridge import AutoGenStreamBridge
from . import registry as _registry
from .autogen_agents import (
    create_concept_agent,
    create_synopsis_agent,
    create_character_bios_agent,
    create_meeting_summary_agent,
    create_treatment_agent,
    create_title_agent,
    create_story_ir_agent,
    create_director_agent,
    create_critic_agent,
    create_dialogue_agent,
    create_revision_agent,
    create_concept_pitch_agent,
    create_character_voice_agent,
    create_contract_repair_agent,
    create_narrative_arch_agent,
    is_quota_error,
    make_model_client,
    make_fallback_model_client,
)
from .agents.title import build_user_prompt as build_title_generation_user_prompt
from .autogen_tools import validate_script_constraints, validate_json_spec, auto_fix_script
from .resource_loader import POSTURE_TRANSITION_TARGETS, ResourceLoader, Character, Scene
from .script_style_skill import ScriptStyleSkill
from .script_tone_skill import ScriptToneSkill
from .json_generator import ScriptJSONGenerator, normalize_initial_position_states
from .script_contract import normalize_script, validate_script, validate_bundle, normalize_camera_resources
from .position_metadata import attach_position_metadata, normalize_position_metadata
from .scene_segments import is_empty_shot, protect_empty_shot, protect_empty_shots
from .cinematography import run_cinematography_pipeline
# 最大审查轮次（超限后强制进入验证阶段）
MAX_REVIEW_ROUNDS = 3


_CONTRACT_EVENT_PATH_RE = re.compile(r"^\$\[(\d+)]\.scene\[(\d+)]")
_CONTRACT_ACT_PATH_RE = re.compile(r"^\$\[(\d+)]\.(scene information|initial position)")


def _contract_repair_payload(script: list, errors: List[dict]) -> Optional[dict]:
    event_keys, act_keys = set(), set()
    for error in errors:
        path = str(error.get("path") or "")
        event_match = _CONTRACT_EVENT_PATH_RE.match(path)
        if event_match:
            event_keys.add(tuple(map(int, event_match.groups())))
            continue
        act_match = _CONTRACT_ACT_PATH_RE.match(path)
        if act_match:
            act_keys.add(int(act_match.group(1)))
            continue
        return None

    event_targets = []
    for act_index, event_index in sorted(event_keys):
        if act_index >= len(script) or event_index >= len(script[act_index].get("scene", [])):
            return None
        act = script[act_index]
        event_targets.append({
            "act_index": act_index,
            "event_index": event_index,
            "scene information": act.get("scene information"),
            "initial position": act.get("initial position"),
            "previous_event": act.get("scene", [])[event_index - 1] if event_index else None,
            "event": act["scene"][event_index],
        })
    act_targets = [
        {
            "act_index": act_index,
            "scene information": script[act_index].get("scene information"),
            "initial position": script[act_index].get("initial position"),
        }
        for act_index in sorted(act_keys) if act_index < len(script)
    ]
    return {"errors": errors, "event_targets": event_targets, "act_targets": act_targets}


def _apply_contract_repairs(script: list, repair: Any, payload: Optional[dict] = None) -> list:
    result = deepcopy(script)
    if not isinstance(repair, dict):
        return result
    allowed_events = {
        (item["act_index"], item["event_index"])
        for item in (payload or {}).get("event_targets", [])
    }
    allowed_acts = {item["act_index"] for item in (payload or {}).get("act_targets", [])}
    for item in repair.get("event_repairs", []) if isinstance(repair.get("event_repairs"), list) else []:
        if not isinstance(item, dict) or type(item.get("act_index")) is not int or type(item.get("event_index")) is not int:
            continue
        act_index, event_index = item["act_index"], item["event_index"]
        event = item.get("event")
        if ((not payload or (act_index, event_index) in allowed_events)
                and isinstance(event, dict) and 0 <= act_index < len(result)
                and 0 <= event_index < len(result[act_index].get("scene", []))):
            event = deepcopy(event)
            for move in event.get("move", []) if isinstance(event.get("move"), list) else []:
                if isinstance(move, dict):
                    move.pop("state", None)
            result[act_index]["scene"][event_index] = event
    for item in repair.get("act_repairs", []) if isinstance(repair.get("act_repairs"), list) else []:
        if not isinstance(item, dict) or type(item.get("act_index")) is not int:
            continue
        act_index, fields = item["act_index"], item.get("fields")
        if not ((not payload or act_index in allowed_acts) and 0 <= act_index < len(result) and isinstance(fields, dict)):
            continue
        for key in ("scene information", "initial position"):
            if key in fields:
                result[act_index][key] = fields[key]
    return result


def _contract_repair_batches(payload: dict) -> List[dict]:
    """Bound semantic contract repair output even when many events are invalid."""
    batches = []
    events = payload.get("event_targets", [])
    for start in range(0, len(events), _CONTRACT_REPAIR_EVENT_BATCH_SIZE):
        targets = events[start:start + _CONTRACT_REPAIR_EVENT_BATCH_SIZE]
        keys = {(item["act_index"], item["event_index"]) for item in targets}
        errors = []
        for error in payload["errors"]:
            match = _CONTRACT_EVENT_PATH_RE.match(str(error.get("path") or ""))
            if match and tuple(map(int, match.groups())) in keys:
                errors.append(error)
        batches.append({"errors": errors, "event_targets": targets, "act_targets": []})
    acts = payload.get("act_targets", [])
    for start in range(0, len(acts), _CONTRACT_REPAIR_EVENT_BATCH_SIZE):
        targets = acts[start:start + _CONTRACT_REPAIR_EVENT_BATCH_SIZE]
        keys = {item["act_index"] for item in targets}
        errors = []
        for error in payload["errors"]:
            match = _CONTRACT_ACT_PATH_RE.match(str(error.get("path") or ""))
            if match and int(match.group(1)) in keys:
                errors.append(error)
        batches.append({"errors": errors, "event_targets": [], "act_targets": targets})
    return batches


async def _enforce_contract(script, loader, bridge, scene_map, act_count,
                            required_names, character_count, *, final=False, preserve_story=True):
    """At most three model repairs; no invalid script can pass this gate."""
    import copy
    candidate = copy.deepcopy(script)
    locked_text = [[(e.get('speaker'), e.get('content')) for e in a.get('scene', [])]
                   for a in script] if isinstance(script, list) else None
    seen_errors = set()
    for attempt in range(4):
        if isinstance(candidate, list):
            for i, act in enumerate(candidate):
                if isinstance(act, dict) and isinstance(act.get('scene information'), dict) and i in scene_map:
                    act['scene information']['where'] = scene_map[i].id
                if isinstance(act, dict):
                    info = act.get('scene information') or {}
                    if not preserve_story:
                        _stabilize_generated_posture_timeline(act)
                    _stabilize_position_timeline(act, str(info.get('where') or '场景'))
        normalized, empty_warnings = normalize_script(candidate, loader)
        report = validate_script(normalized, loader, final=final, act_count=act_count,
                                 required_names=required_names, character_count=character_count)
        report['warnings'] = list({json.dumps(w, sort_keys=True, ensure_ascii=False): w
                                   for w in empty_warnings + report['warnings']}.values())
        if report['valid']:
            # Preserve only the camera/position intermediates needed by cinematography.
            for act, clean in zip(candidate, normalized):
                act['scene information'] = clean['scene information']
                act['initial position'] = clean['initial position']
                for beat, clean_beat in zip(act['scene'], clean['scene']):
                    beat.update(clean_beat)
            _emit_output(bridge, 'ValidationAgent', report, fmt='validation')
            return candidate, report
        fingerprint = json.dumps(report['errors'], sort_keys=True, ensure_ascii=False)
        _emit_output(bridge, 'ValidationAgent', report, fmt='validation')
        if attempt == 3 or fingerprint in seen_errors:
            raise ValueError('剧本格式校验失败，未发布最终文件：' + fingerprint)
        seen_errors.add(fingerprint)
        _emit_stage_log(bridge, 'warning', 'validation', 'repair', f'格式修复 {attempt + 1}/3：{len(report["errors"])} 个错误')
        logger.warning(
            '[ContractRepair] attempt=%s errors=%s',
            attempt + 1,
            json.dumps(report['errors'], ensure_ascii=False),
        )
        story_rule = ('保留幕数、事件数、顺序及每个事件的 speaker/content 原文。' if preserve_story else
                      '保留幕数和各幕事件数、已有非空对白及其说话人、动作和移动数量。'
                      '只有当 errors 明确指出姿态连续性时，才可在同一幕调整事件顺序；只有当 errors 明确指出空台词时，才可补写短句；必要时同步更新 event_index 和位置快照。')
        repair_payload = _contract_repair_payload(candidate, report['errors'])
        if repair_payload is None:
            raise ValueError('剧本格式校验包含无法安全局部返修的全局错误：' + fingerprint)
        repaired = deepcopy(candidate)
        for repair_batch in _contract_repair_batches(repair_payload):
            repair_batch["rules"] = story_rule
            repair_result = await _run_stage_agent_json_object(
                create_contract_repair_agent(),
                json.dumps(repair_batch, ensure_ascii=False, separators=(',', ':')),
            )
            repaired = _apply_contract_repairs(repaired, repair_result, repair_batch)
        repair_diffs = []
        for act_index, (old_act, new_act) in enumerate(zip(candidate, repaired)):
            old_beats, new_beats = old_act.get('scene', []), new_act.get('scene', [])
            if len(old_beats) != len(new_beats):
                repair_diffs.append({'act': act_index, 'scene_count': [len(old_beats), len(new_beats)]})
            for event_index, (old, new) in enumerate(zip(old_beats, new_beats)):
                changed = [key for key in set(old) | set(new) if old.get(key) != new.get(key)]
                counts = {
                    key: [len(old.get(key, [])), len(new.get(key, []))]
                    for key in ('actions', 'move')
                    if isinstance(old.get(key), list) or isinstance(new.get(key), list)
                }
                if changed or any(pair[0] != pair[1] for pair in counts.values()):
                    repair_diffs.append({'act': act_index, 'event': event_index, 'changed_fields': changed, 'counts': counts})
        if len(candidate) != len(repaired):
            repair_diffs.append({'act_count': [len(candidate), len(repaired)]})
        logger.info('[ContractRepair] attempt=%s diff=%s', attempt + 1, json.dumps(repair_diffs, ensure_ascii=False))
        repaired_text = [[(e.get('speaker'), e.get('content')) for e in a.get('scene', [])] for a in repaired]
        if preserve_story and repaired_text != locked_text:
            raise ValueError('格式修复改变了事件数量、顺序或对白，已拒绝发布')
        if not preserve_story:
            from collections import Counter
            if len(repaired) != len(candidate):
                raise ValueError('格式修复改变了幕数，已拒绝发布')
            for old_act, new_act in zip(candidate, repaired):
                old_beats, new_beats = old_act['scene'], new_act['scene']
                lines = lambda beats: Counter((e.get('speaker'), e.get('content')) for e in beats if e.get('content'))
                if len(old_beats) != len(new_beats) or lines(old_beats) - lines(new_beats):
                    raise ValueError('格式修复删除了事件或改写了已有对白，已拒绝发布')
                for key in ('actions', 'move'):
                    if sum(len(e.get(key, [])) for e in old_beats) != sum(len(e.get(key, [])) for e in new_beats):
                        raise ValueError('格式修复增删了动作或移动，已拒绝发布；详情见 ContractRepair diff 日志')
            candidate = repaired
            continue
        for old_act, new_act in zip(candidate, repaired):
            for old, new in zip(old_act['scene'], new_act['scene']):
                for key in ('actions', 'move'):
                    if isinstance(old.get(key), list) and len(new.get(key, [])) != len(old[key]):
                        raise ValueError('格式修复增删了动作或移动，已拒绝发布；详情见 ContractRepair diff 日志')
                for key in ('content_variants', 'language_track', 'then-interact'):
                    if key in old and key not in new:
                        raise ValueError('格式修复删除了可选字段，已拒绝发布')
        candidate = repaired



def _emit_output(bridge: "AutoGenStreamBridge", agent: str, content, fmt: str = 'script') -> None:
    """将 agent 输出以结构化事件推送到前端"""
    bridge.put_event({'type': 'log', 'level': 'output', 'format': fmt, 'agent': agent, 'data': content})


def _emit_stage_log(
    bridge: "AutoGenStreamBridge",
    level: str,
    stage: str,
    phase: str,
    message: str,
) -> None:
    """输出带 stage/phase 的结构化日志事件（兼容现有日志字段）。"""
    bridge.put_event({
        'type': 'log',
        'level': level,
        'message': message,
        'stage': stage,
        'phase': phase,
    })


def _repair_json_string_escapes(text: str) -> str:
    """Repair only malformed JSON string escapes without changing semantic content."""
    repaired: list[str] = []
    in_string = False
    escaped = False
    length = len(text)

    for index, char in enumerate(text):
        if not in_string:
            repaired.append(char)
            if char == '"':
                in_string = True
            continue

        if escaped:
            repaired.append(char)
            escaped = False
            continue
        if char == "\\":
            repaired.append(char)
            escaped = True
            continue
        if char == '"':
            next_index = index + 1
            while next_index < length and text[next_index].isspace():
                next_index += 1
            next_char = text[next_index] if next_index < length else ""
            # A closing quote is followed by a JSON delimiter. Any other quote
            # inside a string is dialogue/content and must be escaped.
            if next_char in {",", "]", "}", ":"} or not next_char:
                repaired.append(char)
                in_string = False
            else:
                repaired.append('\\"')
            continue
        if char == "\n":
            repaired.append("\\n")
            continue
        if char == "\r":
            repaired.append("\\r")
            continue
        if char == "\t":
            repaired.append("\\t")
            continue
        repaired.append(char)
    return "".join(repaired)


def _load_json_with_safe_repair(json_str: str) -> Optional[Any]:
    """Parse JSON, then retry a narrowly-scoped syntax repair for LLM output."""
    try:
        return json.loads(json_str)
    except json.JSONDecodeError as original_error:
        repaired = _repair_json_string_escapes(json_str)
        if repaired == json_str:
            logger.debug("JSON parsing failed without a safe repair candidate: %s", original_error)
            return None
        try:
            parsed = json.loads(repaired)
        except json.JSONDecodeError as repair_error:
            logger.debug("JSON parsing failed after safe repair: %s", repair_error)
            return None
        logger.info("Recovered malformed LLM JSON by escaping string content.")
        return parsed


def _extract_json_from_text(text: str) -> Optional[list]:
    """从 Agent 输出文本中提取 JSON 数组，并保守修复常见字符串转义错误。"""
    # 尝试提取 markdown 代码块
    match = re.search(r'```json\s*([\s\S]*?)\s*```', text, re.DOTALL)
    if match:
        json_str = match.group(1).strip()
    else:
        json_str = text.strip()

    # 有些模型输出会在 JSON 外包一层额外说明/换行，做一次兜底裁剪：
    # 取第一个 '[' 或 '{' 到最后一个 ']' 或 '}'。
    try:
        start_candidates = [i for i in (json_str.find('['), json_str.find('{')) if i != -1]
        if not start_candidates:
            return None
        start = min(start_candidates)
        end_candidates = []
        for c in (']', '}'):
            j = json_str.rfind(c)
            if j != -1:
                end_candidates.append(j)
        if not end_candidates:
            return None
        end = max(end_candidates)
        json_str = json_str[start : end + 1]

        result = _load_json_with_safe_repair(json_str)
        return result if isinstance(result, list) else None
    except json.JSONDecodeError:
        return None


def _extract_json_object_from_text(text: str) -> Optional[dict]:
    """从 Agent 输出中提取 JSON 对象。"""
    match = re.search(r'```json\s*([\s\S]*?)\s*```', text, re.DOTALL)
    json_str = match.group(1).strip() if match else text.strip()
    try:
        start = json_str.find('{')
        end = json_str.rfind('}')
        if start == -1 or end == -1:
            return None
        parsed = _load_json_with_safe_repair(json_str[start:end + 1])
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _extract_feedback_json(text: str) -> Optional[dict]:
    """从 CriticAgent / DialogueAgent 输出中提取反馈 JSON"""
    match = re.search(r'```json\s*([\s\S]*?)\s*```', text, re.DOTALL)
    json_str = match.group(1).strip() if match else text.strip()
    try:
        result = json.loads(json_str)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass
    return None


def _extract_validation_json(text: str) -> Optional[dict]:
    """从 ValidationAgent 输出中提取验证结果 JSON"""
    return _extract_feedback_json(text)


def _filter_script_for_review(script: list) -> str:
    """
    过滤剧本 JSON，只保留 CriticAgent / DialogueAgent 需要的字段，
    避免将完整 JSON（含所有技术字段）传入审查 Agent 导致 token 浪费。
    """
    filtered = []
    for act_index, scene_obj in enumerate(script):
        filtered_scene = {
            "act_index": act_index,
            "scene information": scene_obj.get("scene information", {}),
            "scene": []
        }
        for event_index, seg in enumerate(scene_obj.get("scene", [])):
            if "move" in seg:
                continue  # 移动片段不需要审查
            if is_empty_shot(seg):
                continue  # 空镜不是对白，文学/对白 Agent 不得改写
            filtered_scene["scene"].append({
                "event_index": event_index,
                "speaker": seg.get("speaker", ""),
                "content": seg.get("content", ""),
                "actions": [{"character": a.get("character", ""), "motion_detail": a.get("motion_detail", "")} for a in seg.get("actions", [])],
            })
        filtered.append(filtered_scene)
    return json.dumps(filtered, ensure_ascii=False, indent=2)


_REVIEW_LOCATION_RE = re.compile(r"act\[(\d+)]\.scene\[(\d+)]", re.IGNORECASE)


def _review_target_events(script: list, feedbacks: List[Optional[dict]]) -> List[dict]:
    """Resolve reviewer locations to small, immutable event references."""
    targets = []
    seen = set()
    for feedback in feedbacks:
        for issue in (feedback or {}).get("issues", []):
            match = _REVIEW_LOCATION_RE.search(str(issue.get("location") or ""))
            if not match:
                continue
            act_index, event_index = map(int, match.groups())
            key = (act_index, event_index)
            if key in seen or act_index >= len(script):
                continue
            beats = script[act_index].get("scene", [])
            if event_index >= len(beats) or not beats[event_index].get("speaker"):
                continue
            seen.add(key)
            targets.append({
                "act_index": act_index,
                "event_index": event_index,
                "speaker": beats[event_index].get("speaker"),
                "content": beats[event_index].get("content"),
            })
    return targets[:6]


def _apply_review_changes(
    script: list,
    changes: Any,
    fixed_dialogues: List[dict],
    allowed_targets: Optional[List[dict]] = None,
) -> tuple[list, int]:
    """Apply content-only review patches without changing event identity or shape."""
    result = deepcopy(script)
    fixed = {(item.get("speaker"), item.get("content")) for item in fixed_dialogues}
    allowed = {
        (item["act_index"], item["event_index"])
        for item in (allowed_targets or [])
    }
    applied = 0
    for change in changes if isinstance(changes, list) else []:
        if not isinstance(change, dict) or type(change.get("act_index")) is not int or type(change.get("event_index")) is not int:
            continue
        act_index, event_index = change["act_index"], change["event_index"]
        if allowed_targets is not None and (act_index, event_index) not in allowed:
            continue
        if not (0 <= act_index < len(result)):
            continue
        beats = result[act_index].get("scene", [])
        if not (0 <= event_index < len(beats)):
            continue
        beat = beats[event_index]
        content = change.get("content")
        if not beat.get("speaker") or (beat.get("speaker"), beat.get("content")) in fixed:
            continue
        if not isinstance(content, str) or not content.strip() or content == beat.get("content"):
            continue
        beat["content"] = content
        applied += 1
    return result, applied


def _render_plaintext_screenplay(script: list) -> str:
    """将结构化初稿转成适合人读的中间剧本，不引入第二次 AI 改写。"""
    lines = ["《中间剧本》", "格式：幕 / 场景 / 人物 / 画面、动作与台词"]
    for scene_index, scene in enumerate(script or [], 1):
        info = scene.get("scene information", {}) or {}
        lines.extend([
            "",
            f"第{scene_index}幕｜{info.get('where') or '未指定场景'}",
            f"人物：{'、'.join(info.get('who') or []) or '未指定'}",
        ])
        if info.get("what"):
            lines.append(f"场景：{info['what']}")
        for beat in scene.get("scene", []) or []:
            if beat.get("move"):
                moves = beat["move"] if isinstance(beat["move"], list) else [beat["move"]]
                text = "；".join(
                    f"{move.get('character', '角色')}走向{move.get('destination', '指定位置')}"
                    for move in moves if isinstance(move, dict)
                )
                if text:
                    lines.append(f"【动作】{text}")
            if "speaker" not in beat:
                continue
            speaker = (beat.get("speaker") or "").strip()
            content = (beat.get("content") or "").strip()
            description = (beat.get("shot_description") or beat.get("motion_description") or "").strip()
            if is_empty_shot(beat):
                duration = beat.get('duration') or 5
                duration_text = f'{duration}秒' if isinstance(duration, (int, float)) else str(duration)
                lines.append(f"【空镜】{description or '环境画面'}（{duration_text}）")
                continue
            if description:
                lines.append(f"【镜头】{description}")
            actions = "；".join(
                action.get("motion_detail") or action.get("action") or ""
                for action in beat.get("actions", []) or [] if isinstance(action, dict)
            )
            if actions:
                lines.append(f"【动作】{actions}")
            lines.append(f"{speaker or '旁白'}：{content}")
    return "\n".join(lines)


def _build_title_input(script: list) -> str:
    """压缩剧本为标题 Agent 所需的最小剧情摘要，限制额外 token 消耗。"""
    scenes = []
    for scene_obj in (script or [])[:10]:
        info = scene_obj.get("scene information", {})
        dialogues = []
        for beat in scene_obj.get("scene", []):
            speaker = str(beat.get("speaker", "")).strip()
            content = str(beat.get("content", "")).strip()
            if speaker and content:
                dialogues.append(f"{speaker}：{content[:80]}")
            if len(dialogues) >= 4:
                break
        scenes.append({
            "where": info.get("where", ""),
            "what": str(info.get("what", ""))[:180],
            "dialogues": dialogues,
        })
    return json.dumps(scenes, ensure_ascii=False)


def _fallback_script_title(script: list) -> str:
    """标题 Agent 不可用时，从首个核心事件提取稳定、可读的标题。"""
    for scene_obj in (script or []):
        info = scene_obj.get("scene information", {})
        for key in ("title", "what", "where"):
            text = str(info.get(key, "") or "").strip()
            if not text:
                continue
            text = re.sub(r"[《》\"'“”‘’]", "", text)
            text = re.split(r"[，。！？；：,.!?;:\n]", text)[0].strip()
            if text:
                return text[:12]
    return "新剧本"


def _clean_script_title(value: Any, script: list) -> str:
    title = str(value or "").strip()
    title = re.sub(r"^[《\"'“”‘’]+|[》\"'“”‘’]+$", "", title).strip()
    title = re.split(r"[\n\r]", title)[0].strip()
    return title[:24] if title else _fallback_script_title(script)


async def _generate_script_title(script: list, bridge: "AutoGenStreamBridge") -> str:
    fallback = _fallback_script_title(script)
    try:
        result = await _run_stage_agent_json_object(
            create_title_agent(),
            build_title_generation_user_prompt(_build_title_input(script)),
        )
        title = _clean_script_title((result or {}).get("title"), script)
        _emit_stage_log(bridge, "success", "output", "title", f"🎬 自动片名：《{title}》")
        return title
    except Exception as exc:
        logger.warning("[TitleAgent] 标题生成失败，使用规则兜底：%s", exc)
        _emit_stage_log(bridge, "warning", "output", "title", f"⚠️ 标题生成失败，已使用：《{fallback}》")
        return fallback


def _standalone_json(value: Any, expected_type: type) -> Optional[tuple[Any, str]]:
    """Accept a reasoning-channel fallback only when the whole value is JSON."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    try:
        parsed = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return None
    return (parsed, text) if isinstance(parsed, expected_type) else None


async def _run_stage_agent_json_object(agent, prompt: str) -> Optional[dict]:
    """执行阶段 Agent 并提取 JSON 对象结果。连接错误时指数退避重试；额度耗尽时换用备用模型。"""
    await _clear_agent_context(agent)
    delay = _STAGE_RETRY_BASE_DELAY
    _quota_switched = False
    for attempt in range(_STAGE_MAX_RETRIES + 1):
        try:
            raw_content = None
            thought_candidates: List[str] = []
            async for event in agent.on_messages_stream(
                [TextMessage(content=prompt, source="user")],
                cancellation_token=CancellationToken()
            ):
                if type(event).__name__ == "ThoughtEvent":
                    thought_candidates.append(str(getattr(event, "content", "") or ""))
                if hasattr(event, 'chat_message') and event.chat_message:
                    raw_content = event.chat_message.content
            if not raw_content:
                result = getattr(getattr(agent, "_model_client", None), "last_create_result", None)
                thought_candidates.append(str(getattr(result, "thought", "") or ""))
                for candidate in reversed(thought_candidates):
                    recovered = _standalone_json(candidate, dict)
                    if recovered:
                        logger.warning("[StageAgent] 最终 JSON 位于 reasoning channel，已按完整对象恢复")
                        return recovered[0]
                if attempt < _STAGE_MAX_RETRIES:
                    logger.warning("[StageAgent] 收到空响应，%.0fs后重试（%d/%d）",
                                   delay, attempt + 1, _STAGE_MAX_RETRIES)
                    await _reset_agent_after_failed_request(agent)
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                return None
            return _extract_json_object_from_text(raw_content)
        except Exception as e:
            if is_quota_error(e) and not _quota_switched:
                logger.warning("[StageAgent] 额度耗尽，切换备用模型重试: %s", e)
                await _reset_agent_after_failed_request(agent, use_fallback=True)
                _quota_switched = True
                continue
            is_network = _is_transient_network_error(e)
            if is_network and attempt < _STAGE_MAX_RETRIES:
                logger.warning("[StageAgent] 连接错误，%.0fs后重试（%d/%d）: %s",
                               delay, attempt + 1, _STAGE_MAX_RETRIES, e)
                await _reset_agent_after_failed_request(agent)
                await asyncio.sleep(delay)
                delay *= 2
            else:
                raise
    return None


def _is_transient_network_error(exc: BaseException) -> bool:
    """Recognize common tunnel/proxy disconnects and retryable gateway errors."""
    message = str(exc).lower()
    return any(keyword in message for keyword in _NETWORK_ERR_KEYWORDS)


def _agent_model_name(agent) -> Optional[str]:
    client = getattr(agent, "_model_client", None)
    create_args = getattr(client, "_create_args", {})
    return create_args.get("model") if isinstance(create_args, dict) else None


async def _clear_agent_context(agent) -> None:
    """Clear AutoGen history across supported agent API versions."""
    on_reset = getattr(agent, "on_reset", None)
    if callable(on_reset):
        await on_reset(CancellationToken())
        return
    reset = getattr(agent, "reset", None)
    if callable(reset):
        await reset()


async def _reset_agent_after_failed_request(agent, *, use_fallback: bool = False) -> None:
    """Clear failed request context and replace a possibly broken HTTP client."""
    current_model = _agent_model_name(agent)
    old_client = getattr(agent, "_model_client", None)
    create_args = getattr(old_client, "_create_args", {})
    structured_json = bool(
        isinstance(create_args, dict)
        and (create_args.get("extra_body") or {}).get("thinking", {}).get("type") in {"enabled", "disabled", "auto"}
    )
    try:
        if old_client is not None:
            await old_client.close()
    except Exception as close_error:
        logger.debug("关闭失败的模型客户端时出现异常: %s", close_error)
    try:
        await _clear_agent_context(agent)
    except Exception as reset_error:
        logger.debug("清理 Agent 失败请求上下文时出现异常: %s", reset_error)
    agent._model_client = (
        make_fallback_model_client(structured_json=structured_json)
        if use_fallback
        else make_model_client(current_model, structured_json=structured_json)
    )


def _director_response_diagnostics(
    director,
    *,
    event_counts: Dict[str, int],
    last_event_type: str,
    streamed_chars: int,
    thought_chars: int,
    final_usage=None,
) -> dict:
    """提取不含密钥和正文的模型响应诊断信息。"""
    client = getattr(director, "_model_client", None)
    create_args = getattr(client, "_create_args", {})
    result = getattr(client, "last_create_result", None)
    usage = getattr(result, "usage", None) or final_usage
    content = getattr(result, "content", None) if result is not None else None
    thought = getattr(result, "thought", None) if result is not None else None

    return {
        "model": create_args.get("model", "unknown") if isinstance(create_args, dict) else "unknown",
        "finish_reason": getattr(result, "finish_reason", None),
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "cached": getattr(result, "cached", None),
        "result_captured": result is not None,
        "result_content_type": type(content).__name__ if content is not None else None,
        "result_content_chars": len(content) if isinstance(content, str) else None,
        "result_thought_chars": len(thought) if isinstance(thought, str) else 0,
        "streamed_chars": streamed_chars,
        "thought_event_chars": thought_chars,
        "last_event_type": last_event_type or None,
        "event_counts": dict(event_counts),
    }


def _explain_empty_director_response(diagnostics: dict, max_output_tokens: int) -> str:
    finish_reason = diagnostics.get("finish_reason")
    completion_tokens = diagnostics.get("completion_tokens")
    thought_chars = max(
        diagnostics.get("result_thought_chars") or 0,
        diagnostics.get("thought_event_chars") or 0,
    )

    if finish_reason == "length":
        return "模型因达到输出长度限制而停止，最终正文可能尚未开始或未能形成。"
    if finish_reason == "content_filter":
        return "模型响应被内容安全过滤器终止，因此没有可用正文。"
    if thought_chars > 0:
        return f"模型产生了 {thought_chars} 字符的 reasoning 内容但正文为空；若 reasoning 不是独立合法 JSON，则视为上游响应通道异常。"
    if finish_reason == "stop" and completion_tokens == 0:
        return "模型正常停止但 completion_tokens 为 0，倾向于模型或上游接口返回了空响应。"
    if (
        isinstance(completion_tokens, int)
        and max_output_tokens > 0
        and completion_tokens >= int(max_output_tokens * 0.95)
    ):
        return "模型输出 token 已接近配置上限，但最终正文为空，可能是输出预算被内部推理消耗。"
    if diagnostics.get("result_captured"):
        return "底层模型请求已正常结束，但返回的正文是空字符串；当前数据不足以进一步区分模型空响应与上游兼容接口异常。"
    return "未捕获到底层模型结束结果，可能是客户端事件结构异常或响应未完整传递到 Agent。"


def _format_director_diagnostics(diagnostics: dict) -> str:
    event_counts = diagnostics.get("event_counts") or {}
    events = ", ".join(f"{name}={count}" for name, count in sorted(event_counts.items())) or "无"
    return (
        f"model={diagnostics.get('model')}，finish_reason={diagnostics.get('finish_reason')}，"
        f"prompt_tokens={diagnostics.get('prompt_tokens')}，completion_tokens={diagnostics.get('completion_tokens')}，"
        f"底层正文={diagnostics.get('result_content_chars')}字符，底层思考={diagnostics.get('result_thought_chars')}字符，"
        f"流式正文={diagnostics.get('streamed_chars')}字符，最后事件={diagnostics.get('last_event_type')}，"
        f"事件统计=[{events}]"
    )


async def _run_director_agent(
    director,
    prompt: str,
    bridge: "AutoGenStreamBridge",
    label: str = "DirectorAgent",
    *,
    _json_prefix: str = "",
    _continuations: int = 0,
    _source_prompt: Optional[str] = None,
) -> Optional[list]:
    """运行导演请求；连接失败重试，长度截断保留前缀续写，最终只返回完整 JSON。"""
    # Every request owns its complete input. Never let previous drafts or
    # retries accumulate in the AssistantAgent conversation history.
    await _clear_agent_context(director)
    source_prompt = prompt if _source_prompt is None else _source_prompt
    if not _json_prefix:
        prompt += (
            "\n\n输出排版要求：使用紧凑 JSON，不要缩进、空行、Markdown 或说明文字。"
            "仅减少排版空白，不得删减镜头、对白、动作、move 或必需字段。"
        )
    system_chars = sum(
        len(str(getattr(message, "content", "")))
        for message in getattr(director, "_system_messages", [])
    )
    model_args = getattr(getattr(director, "_model_client", None), "_create_args", {})
    max_output_tokens = model_args.get("max_tokens", 8000) if isinstance(model_args, dict) else 8000
    request_metrics = {
        "system_prompt_chars": system_chars,
        "dynamic_prompt_chars": len(prompt),
        "total_input_chars": system_chars + len(prompt),
        "max_output_tokens": max_output_tokens,
    }
    delay = _STAGE_RETRY_BASE_DELAY
    _quota_switched = False
    for attempt in range(_STAGE_MAX_RETRIES + 1):
        thinking_started = False
        raw_content = None
        event_counts: Dict[str, int] = {}
        last_event_type = ""
        streamed_chars = 0
        thought_chars = 0
        thought_candidates: List[str] = []
        final_usage = None
        try:
            _emit_stage_log(
                bridge, 'info', 'direct' if '直接' in label else 'draft', 'director_attempt',
                f'🤖 [{label}] 请求模型中（第 {attempt + 1}/{_STAGE_MAX_RETRIES + 1} 次）...'
            )
            async for event in director.on_messages_stream(
                [TextMessage(content=prompt, source="user")],
                cancellation_token=CancellationToken()
            ):
                event_type = type(event).__name__
                last_event_type = event_type
                event_counts[event_type] = event_counts.get(event_type, 0) + 1
                logger.debug("[%s] event type=%s", label, event_type)
                if isinstance(event, ModelClientStreamingChunkEvent):
                    streamed_chars += len(event.content or "")
                elif event_type == "ThoughtEvent":
                    thought_text = str(getattr(event, "content", "") or "")
                    thought_chars += len(thought_text)
                    thought_candidates.append(thought_text)
                if hasattr(event, 'inner_messages'):
                    for msg in (event.inner_messages or []):
                        if isinstance(msg, ModelClientStreamingChunkEvent):
                            streamed_chars += len(msg.content or "")
                            if not thinking_started:
                                thinking_started = True
                            bridge.put_event({'type': 'thinking_chunk', 'agent': label, 'text': msg.content})
                        elif type(msg).__name__ == "ThoughtEvent":
                            thought_candidates.append(str(getattr(msg, "content", "") or ""))
                if hasattr(event, 'chat_message') and event.chat_message:
                    raw_content = event.chat_message.content or ""
                    final_usage = getattr(event.chat_message, "models_usage", None)
                    diagnostics = _director_response_diagnostics(
                        director,
                        event_counts=event_counts,
                        last_event_type=last_event_type,
                        streamed_chars=streamed_chars,
                        thought_chars=thought_chars,
                        final_usage=final_usage,
                    )
                    logger.info("[%s] 原始输出（前500字）: %s", label, raw_content[:500])
                    _emit_stage_log(
                        bridge, 'info', 'direct' if '直接' in label else 'draft', 'director_response',
                        f'📥 [{label}] 已收到模型输出（{len(raw_content)} 字符），正在提取 JSON。'
                        f'响应诊断：{_format_director_diagnostics(diagnostics)}。'
                    )
                    if thinking_started:
                        bridge.put_event({'type': 'thinking_done'})
                        thinking_started = False
            if thinking_started:
                bridge.put_event({'type': 'thinking_done'})
            if not raw_content:
                result = getattr(getattr(director, "_model_client", None), "last_create_result", None)
                thought_candidates.append(str(getattr(result, "thought", "") or ""))
                for candidate in reversed(thought_candidates):
                    recovered = _standalone_json(candidate, list)
                    if recovered:
                        raw_content = recovered[1]
                        _emit_stage_log(
                            bridge, 'warning', 'direct' if '直接' in label else 'draft',
                            'director_reasoning_json',
                            f'⚠️ [{label}] 正文为空，但 reasoning channel 是完整 JSON；已恢复并继续严格校验。'
                        )
                        break
            if not raw_content:
                diagnostics = _director_response_diagnostics(
                    director,
                    event_counts=event_counts,
                    last_event_type=last_event_type,
                    streamed_chars=streamed_chars,
                    thought_chars=thought_chars,
                    final_usage=final_usage,
                )
                explanation = _explain_empty_director_response(diagnostics, max_output_tokens)
                bridge.last_error_details = {
                    "agent": label,
                    "error_type": "EmptyModelResponse",
                    **request_metrics,
                    **diagnostics,
                    "likely_explanation": explanation,
                }
                _emit_stage_log(
                    bridge, 'warning', 'direct' if '直接' in label else 'draft', 'director_empty',
                    f'⚠️ [{label}] 模型未返回最终正文。请求规模：系统 {system_chars} 字符，动态输入 {len(prompt)} 字符，'
                    f'输出上限 {max_output_tokens} tokens。响应诊断：{_format_director_diagnostics(diagnostics)}。'
                    f'初步判断：{explanation}'
                )
                retryable_empty = diagnostics.get("finish_reason") not in {"length", "content_filter"}
                if retryable_empty and attempt < _STAGE_MAX_RETRIES:
                    _emit_stage_log(
                        bridge, 'warning', 'direct' if '直接' in label else 'draft', 'director_empty_retry',
                        f'🔄 [{label}] 空响应可能由上游连接未完整返回导致，{delay:.0f}s 后使用新连接重试'
                        f'（{attempt + 1}/{_STAGE_MAX_RETRIES}）。'
                    )
                    await _reset_agent_after_failed_request(director)
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                return None
            finish_reason = diagnostics.get("finish_reason")
            if _json_prefix or finish_reason == "length":
                # Preserve every byte at the join, including spaces inside a
                # truncated string. Never close brackets or salvage partial acts.
                if _json_prefix:
                    json_candidate = _json_prefix + raw_content
                else:
                    json_candidate = re.sub(r'^\s*```(?:json)?\s*', '', raw_content, count=1, flags=re.IGNORECASE).lstrip()
                if finish_reason != "length":
                    json_candidate = re.sub(r'\s*```\s*$', '', json_candidate)
                try:
                    parsed = json.loads(json_candidate)
                    if not isinstance(parsed, list):
                        parsed = None
                except json.JSONDecodeError:
                    parsed = None
                if parsed is None and finish_reason == "length" and json_candidate.startswith("["):
                    if _continuations < _DIRECTOR_MAX_CONTINUATIONS:
                        logger.warning("[%s] length 截断，续写 %d/%d，保留前缀 %d 字符",
                                       label, _continuations + 1, _DIRECTOR_MAX_CONTINUATIONS, len(json_candidate))
                        _emit_stage_log(
                            bridge, 'warning', 'direct' if '直接' in label else 'draft', 'director_continue',
                            f'✍️ [{label}] 输出达到长度上限，保留已有 {len(json_candidate)} 字符，'
                            f'从截断处继续生成（第 {_continuations + 1}/{_DIRECTOR_MAX_CONTINUATIONS} 次续写）。'
                        )
                        continuation_prompt = (
                            source_prompt
                            + "\n\n## 截断恢复（本次仅输出剩余后缀）\n"
                            "下面 JSON 字符串的解码值是已经保存的输出前缀，必须逐字保留。"
                            "从它的最后一个字符之后继续，只输出可直接拼接的后缀。"
                            "可能截在字符串、转义或数字中间，请接完该值；不得重写开头或重复末尾。"
                            "完成原请求中全部剩余镜头/幕/对白/动作/move和字段，不得缩减、改写、重排或提前结束。"
                            "只输出原始后缀，不要把后缀包成字符串，不要 Markdown 或说明。"
                            "使用紧凑 JSON 排版，最后正常闭合整个数组。\n已保存前缀（JSON 字符串）：\n"
                            + json.dumps(json_candidate, ensure_ascii=False)
                        )
                        return await _run_director_agent(
                            director, continuation_prompt, bridge, label,
                            _json_prefix=json_candidate,
                            _continuations=_continuations + 1,
                            _source_prompt=source_prompt,
                        )
                    _emit_stage_log(
                        bridge, 'warning', 'direct' if '直接' in label else 'draft', 'director_continue_exhausted',
                        f'⚠️ [{label}] {_DIRECTOR_MAX_CONTINUATIONS} 次续写后 JSON 仍不完整，未将残缺内容作为成功结果。'
                    )
            else:
                parsed = _extract_json_from_text(raw_content)
            if parsed is None:
                code_block = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', raw_content, re.DOTALL | re.IGNORECASE)
                if not (_json_prefix or finish_reason == "length"):
                    json_candidate = code_block.group(1) if code_block else raw_content
                bridge.last_error_details = {
                    "agent": label,
                    "error_type": "InvalidOrTruncatedJson",
                    "response_chars": len(raw_content),
                    "assembled_chars": len(json_candidate),
                    "continuations": _continuations,
                    "response_ends_with_array": json_candidate.rstrip().endswith("]"),
                    "response_is_markdown_code_block": bool(code_block),
                    "max_output_tokens": max_output_tokens,
                    "model_response": _director_response_diagnostics(
                        director,
                        event_counts=event_counts,
                        last_event_type=last_event_type,
                        streamed_chars=streamed_chars,
                        thought_chars=thought_chars,
                        final_usage=final_usage,
                    ),
                }
                _emit_stage_log(
                    bridge, 'warning', 'direct' if '直接' in label else 'draft', 'director_parse_failed',
                    f'⚠️ [{label}] 未能从模型输出中提取合法 JSON。已收到 {len(raw_content)} 字符，'
                    f'输出是否以数组结束：{raw_content.rstrip().endswith("]")}；可能是 JSON 被截断或夹带非 JSON 内容。'
                )
            else:
                bridge.last_error_details = None
                if _continuations:
                    logger.info("[%s] 续写完成，%d 次续写，完整 JSON %d 字符",
                                label, _continuations, len(json_candidate))
                _emit_stage_log(
                    bridge, 'success', 'direct' if '直接' in label else 'draft', 'director_parse_ok',
                    f'✅ [{label}] JSON 提取成功'
                )
            return parsed
        except Exception as e:
            if thinking_started:
                bridge.put_event({'type': 'thinking_done'})
            if is_quota_error(e) and not _quota_switched:
                logger.warning("[%s] 额度耗尽，切换备用模型重试: %s", label, e)
                bridge.put_event({'type': 'thinking_chunk', 'agent': label,
                                  'text': f'\n⚠️ 主模型额度耗尽，已切换备用模型重试...\n'})
                await _reset_agent_after_failed_request(director, use_fallback=True)
                _quota_switched = True
                continue
            is_network = _is_transient_network_error(e)
            if is_network and attempt < _STAGE_MAX_RETRIES:
                logger.warning("[%s] 连接错误，%.0fs后重试（%d/%d）: %s",
                               label, delay, attempt + 1, _STAGE_MAX_RETRIES, e)
                bridge.put_event({'type': 'thinking_chunk', 'agent': label,
                                  'text': f'\n⚠️ 连接错误，{delay:.0f}s 后重试（{attempt + 1}/{_STAGE_MAX_RETRIES}）...\n'})
                await _reset_agent_after_failed_request(director)
                await asyncio.sleep(delay)
                delay *= 2
            elif is_network:
                error_details = {
                    "agent": label,
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "attempts": _STAGE_MAX_RETRIES + 1,
                    **request_metrics,
                }
                bridge.last_error_details = error_details
                logger.error("[%s] 网络重试耗尽，详情=%s", label, error_details)
                _emit_stage_log(
                    bridge, 'error', 'direct' if '直接' in label else 'draft', 'director_network_exhausted',
                    f'❌ [{label}] 连接重试已耗尽。系统 {system_chars} 字符 + 动态输入 {len(prompt)} 字符，'
                    f'输出上限 {max_output_tokens} tokens；异常 {type(e).__name__}: {e}。'
                    '远端在返回响应前断开，无法从本次请求直接判定是输入还是预期输出过大。'
                )
                return None
            else:
                raise
    return None


_EVENT_COUNT_PATTERNS = (
    re.compile(r"(?:总共|共|一共)\s*(\d+)\s*(?:个|条)?\s*(?:镜头|分镜|事件)"),
    re.compile(r"(?:镜头|分镜|事件)(?:数量|数)?\s*[:：为]?\s*(\d+)"),
)
_QUOTED_TEXT_RE = re.compile(r"“([^”\n]+)”|\"([^\"\n]+)\"")
_INLINE_DIALOGUE_END_RE = re.compile(r"\s*(?:【|景别\s*[:：])")
_SHOT_METADATA_RE = re.compile(r"\s*景别\s*[:：].*$", re.DOTALL)
_NO_DIALOGUE_MARKER_RE = re.compile(r"\s*台词\s*[:：]\s*(?:—+|-+|无台词|无)\s*[。.]?")


def _requested_event_count(text: str) -> Optional[int]:
    for pattern in _EVENT_COUNT_PATTERNS:
        match = pattern.search(text or "")
        if match:
            return int(match.group(1))
    return None


def _distribute_event_count(total: int, act_count: int) -> List[int]:
    total = max(total, act_count)
    base, remainder = divmod(total, act_count)
    return [base + (1 if index < remainder else 0) for index in range(act_count)]


def _source_dialogue(source: str, character_names: List[str]) -> dict:
    quotes = list(_QUOTED_TEXT_RE.finditer(source))
    names = sorted({name for name in character_names if name}, key=len, reverse=True)
    if len(quotes) == 1:
        match = quotes[0]
        content = next(group for group in match.groups() if group is not None)
        prefix = source[:match.start()]
        # In prose such as "A looks at B and says", the first named character in
        # the current sentence is the grammatical subject, not the nearest name.
        clause = re.split(r"[\n。！？!?]", prefix)[-1]
        present = [(clause.find(name), name) for name in names if name in clause]
        speaker = min(present)[1] if present else ""
        return {"speaker": speaker, "content": content} if speaker else {"content": content}

    if quotes or not names:
        return {}
    name_pattern = "|".join(re.escape(name) for name in names)
    labels = list(re.finditer(
        rf"(?P<speaker>{name_pattern})(?:[（(][^）)\n]{{0,20}}[）)])?\s*[:：]\s*(?P<content>.+)",
        source,
    ))
    if len(labels) != 1:
        return {}
    label = labels[0]
    content = _INLINE_DIALOGUE_END_RE.split(label.group("content"), maxsplit=1)[0].strip()
    if not content or content in {"—", "-", "无", "无台词"}:
        return {}
    return {"speaker": label.group("speaker"), "content": content}


def _source_visual_content(source: str) -> str:
    """Separate visual prose from source-only shot and no-dialogue annotations."""
    content = _SHOT_METADATA_RE.sub("", source).strip()
    content = _NO_DIALOGUE_MARKER_RE.sub("", content).strip()
    return content or source.strip()


def _storyboard_event_rows(text: str, act_count: int) -> Optional[List[List[str]]]:
    """Read explicit numbered shots without asking a model to rediscover boundaries."""
    groups: List[List[str]] = []
    current_group: List[str] = []
    current_row = ""
    saw_marker = False
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if _ACT_MARKER_RE.match(line):
            saw_marker = True
            if current_row:
                current_group.append(current_row)
                current_row = ""
            if current_group:
                groups.append(current_group)
                current_group = []
            continue
        if _NUMBERED_SHOT_RE.match(line):
            if current_row:
                current_group.append(current_row)
            current_row = line
            continue
        if current_row and not re.search(r"(?:总共|共|一共)\s*\d+\s*(?:个|条)?\s*(?:镜头|分镜)", line):
            current_row += "\n" + line
    if current_row:
        current_group.append(current_row)
    if current_group:
        groups.append(current_group)
    rows = [row for group in groups for row in group]
    if not rows:
        return None
    if saw_marker and len(groups) == act_count:
        return groups
    counts = _distribute_event_count(len(rows), act_count)
    balanced, start = [], 0
    for count in counts:
        balanced.append(rows[start:start + count])
        start += count
    return balanced


def _event_manifest(
    creative_brief: str,
    act_count: int,
    target_dialogue_lines: Optional[int],
    fixed_dialogues: List[dict],
    character_names: List[str],
) -> tuple[Optional[List[List[dict]]], bool, Optional[str]]:
    explicit_rows = _storyboard_event_rows(creative_brief, act_count)
    if explicit_rows:
        total = sum(len(rows) for rows in explicit_rows)
        if any(not rows for rows in explicit_rows):
            return None, True, f"显式镜头数 {total} 少于幕数 {act_count}，无法保证每幕至少一个事件。"
        if total > _DIRECTOR_MAX_EVENTS_TOTAL:
            return None, True, f"显式镜头共 {total} 个，超过系统上限 {_DIRECTOR_MAX_EVENTS_TOTAL}；请拆成多个剧本。"
        acts: List[List[dict]] = []
        for act_index, rows in enumerate(explicit_rows):
            events = []
            for event_index, raw in enumerate(rows):
                source = _NUMBERED_SHOT_RE.sub("", raw, count=1).lstrip("：: \t")
                if len(source) > _DIRECTOR_MAX_SOURCE_EVENT_CHARS:
                    return None, True, (
                        f"第 {act_index + 1} 幕第 {event_index + 1} 个镜头有 {len(source)} 字符，"
                        f"超过单镜头上限 {_DIRECTOR_MAX_SOURCE_EVENT_CHARS}；请拆分该镜头。"
                    )
                event = {
                    "event_id": f"A{act_index + 1:02d}E{event_index + 1:04d}",
                    "source_text": source,
                }
                event.update(_source_dialogue(source, character_names))
                events.append(event)
            acts.append(events)
        return acts, True, None

    requested = _requested_event_count(creative_brief)
    if requested:
        total = requested
    elif target_dialogue_lines:
        total = target_dialogue_lines + max(act_count, (target_dialogue_lines + 2) // 3)
    else:
        total = act_count * _DIRECTOR_DEFAULT_EVENTS_PER_ACT
    total = max(total, len(fixed_dialogues), act_count)
    if total > _DIRECTOR_MAX_EVENTS_TOTAL:
        return None, False, f"计划生成 {total} 个事件，超过系统上限 {_DIRECTOR_MAX_EVENTS_TOTAL}；请降低时长或拆分剧本。"
    counts = _distribute_event_count(total, act_count)
    dialogue_counts = (
        _distribute_event_count(target_dialogue_lines, act_count)
        if target_dialogue_lines else [0] * act_count
    )
    fixed_queue = list(fixed_dialogues)
    acts = []
    for act_index, count in enumerate(counts):
        events = []
        for event_index in range(count):
            event = {"event_id": f"A{act_index + 1:02d}E{event_index + 1:04d}"}
            if event_index < dialogue_counts[act_index]:
                event["required_kind"] = "dialogue"
            if fixed_queue:
                event.update(fixed_queue.pop(0))
                event["required_kind"] = "dialogue"
            events.append(event)
        acts.append(events)
    return acts, False, None


def _event_batches(events: List[dict]) -> List[List[dict]]:
    batches, current, current_chars = [], [], 0
    for event in events:
        source_chars = len(str(event.get("source_text") or event.get("intent") or ""))
        if current and (
            len(current) >= _DIRECTOR_EVENT_BATCH_SIZE
            or current_chars + source_chars > _DIRECTOR_BATCH_SOURCE_CHAR_BUDGET
        ):
            batches.append(current)
            current, current_chars = [], 0
        current.append(event)
        current_chars += source_chars
    if current:
        batches.append(current)
    return batches


def _freeze_explicit_story_ir(manifest: List[List[dict]]) -> List[List[dict]]:
    result = []
    for events in manifest:
        frozen = []
        for event in events:
            dialogue = bool(event.get("content"))
            speaker = event.get("speaker", "")
            frozen.append({
                "event_id": event["event_id"],
                "kind": "dialogue" if dialogue else "action",
                "speaker": speaker,
                "speaker_locked": not dialogue or bool(speaker),
                "content": event.get("content") if dialogue else _source_visual_content(event.get("source_text", "")),
                "intent": event.get("source_text", ""),
                "shot_intent": event.get("source_text", "")[:80],
            })
        result.append(frozen)
    return result


def _story_ir_errors(payload: Any, slots: List[dict]) -> List[str]:
    events = payload.get("events") if isinstance(payload, dict) else None
    expected_ids = [slot["event_id"] for slot in slots]
    if not isinstance(events, list):
        return ["events 必须是数组"]
    received_ids = [event.get("event_id") if isinstance(event, dict) else None for event in events]
    errors = []
    if received_ids != expected_ids:
        errors.append(f"event_id 必须严格等于 {expected_ids}")
    for slot, event in zip(slots, events):
        if not isinstance(event, dict):
            errors.append(f"{slot['event_id']} 不是对象")
            continue
        required_fields = {"event_id", "kind", "speaker", "content", "intent", "shot_intent"}
        missing = required_fields - event.keys()
        if missing:
            errors.append(f"{slot['event_id']} 缺少字段 {sorted(missing)}")
        if event.get("kind") not in {"dialogue", "action", "move", "empty_shot"}:
            errors.append(f"{slot['event_id']} kind 非法")
        if slot.get("required_kind") and event.get("kind") != slot["required_kind"]:
            errors.append(f"{slot['event_id']} 必须是 {slot['required_kind']}")
        if event.get("kind") == "dialogue" and (not event.get("speaker") or not event.get("content")):
            errors.append(f"{slot['event_id']} 对白缺少 speaker/content")
        if event.get("kind") == "move" and not (
            isinstance(event.get("move_characters"), list)
            and event["move_characters"]
            and all(isinstance(name, str) and name.strip() for name in event["move_characters"])
        ):
            errors.append(f"{slot['event_id']} 移动事件缺少 move_characters")
        if slot.get("speaker") and event.get("speaker") != slot["speaker"]:
            errors.append(f"{slot['event_id']} speaker 未逐字保留")
        if slot.get("content") and event.get("content") != slot["content"]:
            errors.append(f"{slot['event_id']} content 未逐字保留")
        if (
            len(str(event.get("content") or "")) > 500
            or len(str(event.get("intent") or "")) > 240
            or len(str(event.get("shot_intent") or "")) > 160
        ):
            errors.append(f"{slot['event_id']} IR 字段过长")
    return errors


async def _build_story_ir(
    bridge: "AutoGenStreamBridge",
    manifest: List[List[dict]],
    explicit: bool,
    creative_brief: str,
    meeting_summary: Dict,
    treatment: Dict,
    character_names: List[str] = None,
) -> Optional[List[List[dict]]]:
    if explicit:
        _emit_stage_log(
            bridge, 'info', 'draft', 'story_ir_classifying',
            f'🔒 已从用户分镜冻结 {sum(map(len, manifest))} 个事件及稳定 event_id，'
            '正在识别每个镜头内的对白、动作与移动组合。'
        )

    treatment_beats = treatment.get("treatment") if isinstance(treatment, dict) else []
    treatment_beats = treatment_beats if isinstance(treatment_beats, list) else []
    result: List[List[dict]] = []
    for act_index, slots in enumerate(manifest):
        frozen: List[dict] = []
        batches = _event_batches(slots)
        for batch_index, batch_slots in enumerate(batches):
            act_treatment = treatment_beats[act_index] if act_index < len(treatment_beats) else treatment
            prompt_payload = {
                "act": act_index + 1,
                "batch": batch_index + 1,
                "batch_count": len(batches),
                "input_mode": "explicit_storyboard" if explicit else "free_creation",
                "slots": batch_slots,
                "creative_brief": creative_brief[:1200],
                "meeting_summary": meeting_summary,
                "act_treatment": act_treatment,
                "previous_events": frozen[-2:],
            }
            base_prompt = json.dumps(prompt_payload, ensure_ascii=False, separators=(",", ":"))
            accepted = None
            errors: List[str] = []
            for attempt in range(_DIRECTOR_BATCH_RETRIES + 1):
                prompt = base_prompt
                if errors:
                    prompt += "\n上次错误：" + "；".join(errors) + "。只重做当前批。"
                candidate = await _run_stage_agent_json_object(create_story_ir_agent(), prompt)
                for event in candidate.get("events", []) if isinstance(candidate, dict) else []:
                    if not isinstance(event, dict) or event.get("kind") != "move" or event.get("move_characters"):
                        continue
                    clauses = re.split(r"[，,。；;]", str(event.get("intent") or ""))
                    inferred = [
                        name for name in character_names or []
                        if any(name in clause and re.search(r"走|跑|冲|移动|靠近|离开|进入|来到|上前|退后", clause) for clause in clauses)
                    ]
                    if inferred:
                        event["move_characters"] = inferred
                errors = _story_ir_errors(candidate, batch_slots)
                if not errors:
                    accepted = candidate["events"]
                    break
            if accepted is None:
                bridge.put_event({
                    'type': 'error',
                    'message': f'StoryIRAgent 第 {act_index + 1} 幕第 {batch_index + 1} 批未通过事件 ID 或冻结对白校验。',
                    'details': errors,
                })
                return None
            for slot, event in zip(batch_slots, accepted):
                event["speaker_locked"] = bool(slot.get("speaker")) if explicit else True
            frozen.extend(accepted)
        result.append(frozen)
    _emit_stage_log(
        bridge, 'success', 'draft', 'story_ir_complete',
        f'🔒 Story IR 已冻结，共 {sum(map(len, result))} 个事件；后续只做逐批语义映射。'
    )
    return result


def _director_batch_errors(
    scene_obj: Any,
    story_ir: List[dict],
    required_names: set[str],
    required_character_count: int,
) -> List[str]:
    if not isinstance(scene_obj, dict):
        return ["scene_obj 必须是对象"]
    beats = scene_obj.get("scene")
    expected_ids = [event["event_id"] for event in story_ir]
    if not isinstance(beats, list):
        return ["scene 必须是数组"]
    received_ids = [beat.get("event_id") if isinstance(beat, dict) else None for beat in beats]
    errors = []
    if len(beats) > _DIRECTOR_EVENT_BATCH_SIZE:
        errors.append(f"单批事件数不得超过 {_DIRECTOR_EVENT_BATCH_SIZE}")
    if received_ids != expected_ids:
        errors.append(f"event_id 必须严格等于 {expected_ids}")
    for frozen, beat in zip(story_ir, beats):
        if not isinstance(beat, dict):
            continue
        if frozen.get("speaker_locked", True) and beat.get("speaker", "") != frozen.get("speaker", ""):
            errors.append(f"{frozen['event_id']} speaker 被修改")
        if beat.get("content", "") != frozen.get("content", ""):
            errors.append(f"{frozen['event_id']} content 被修改")
        if frozen.get("kind") == "move" and not beat.get("move"):
            errors.append(f"{frozen['event_id']} 已识别为移动事件，必须输出非空 move")
        if frozen.get("kind") == "move" and beat.get("move"):
            mover_names = [
                move.get("character") for move in beat["move"] if isinstance(move, dict)
            ]
            actual_movers = set(mover_names)
            missing_movers = set(frozen.get("move_characters") or []) - actual_movers
            if missing_movers:
                errors.append(f"{frozen['event_id']} move 遗漏角色 {sorted(missing_movers)}")
            if len(mover_names) != len(actual_movers):
                errors.append(f"{frozen['event_id']} move 中同一角色只能出现一次")
            unknown_movers = actual_movers - set((scene_obj.get("scene information") or {}).get("who") or [])
            if unknown_movers:
                errors.append(f"{frozen['event_id']} move 包含未登场角色 {sorted(unknown_movers)}")
    who = set((scene_obj.get("scene information") or {}).get("who") or [])
    if required_names and not required_names <= who:
        errors.append("scene information.who 遗漏锁定角色")
    if required_character_count and len(who) != required_character_count:
        errors.append(f"scene information.who 必须恰好 {required_character_count} 人")
    # Missing runtime shot fields and position defaults are deterministic compiler
    # work handled by _normalize_direct_scene after all batches are merged.
    for frozen, beat in zip(story_ir, beats):
        if isinstance(beat, dict) and beat.get("shot") not in {None, "", "character", "scene", "object"}:
            errors.append(f"{frozen['event_id']} shot 值非法")
    return errors


def _restore_frozen_event_fields(scene_obj: Any, story_ir: List[dict]) -> Any:
    """Make frozen story fields code-owned instead of trusting model echoing."""
    if not isinstance(scene_obj, dict) or not isinstance(scene_obj.get("scene"), list):
        return scene_obj
    beats = scene_obj["scene"]
    expected_ids = [event["event_id"] for event in story_ir]
    received_ids = [beat.get("event_id") if isinstance(beat, dict) else None for beat in beats]
    if received_ids != expected_ids:
        return scene_obj
    for index, (frozen, beat) in enumerate(zip(story_ir, beats)):
        speaker = frozen.get("speaker", "")
        content = frozen.get("content", "")
        if frozen.get("kind") == "move":
            moves = beat.get("move") if isinstance(beat.get("move"), list) else []
            actual_movers = {move.get("character") for move in moves if isinstance(move, dict)}
            for character in frozen.get("move_characters") or []:
                if character not in actual_movers:
                    moves.append({"character": character, "destination": ""})
            beat["move"] = moves
            stand_actions = [
                action for action in beat.get("actions", []) if isinstance(action, dict)
                and action.get("character") in set(frozen.get("move_characters") or [])
                and action.get("action") == "Stand Up"
            ]
            if stand_actions and index and "move" not in beats[index - 1]:
                beats[index - 1].setdefault("actions", []).extend(deepcopy(stand_actions))
                beat["actions"] = [action for action in beat.get("actions", []) if action not in stand_actions]
        if frozen.get("kind") == "move" and not speaker and not content:
            beat.pop("speaker", None)
            beat.pop("content", None)
        else:
            beat["content"] = content
            if frozen.get("speaker_locked", True):
                beat["speaker"] = speaker
    return scene_obj


def _merge_director_batch(target: Optional[Dict], batch_scene: Dict) -> Dict:
    if target is None:
        return deepcopy(batch_scene)
    descriptions = target.setdefault("position_descriptions", {})
    for key, value in (batch_scene.get("position_descriptions") or {}).items():
        descriptions.setdefault(key, value)
    target.setdefault("scene", []).extend(deepcopy(batch_scene.get("scene") or []))
    return target


def _director_error_window(story_ir: List[dict], errors: List[str]) -> List[dict]:
    for index, event in enumerate(story_ir):
        if any(error.startswith(f"{event['event_id']} ") for error in errors):
            return story_ir[max(0, index - 2):index + 3]
    return []


def _replace_director_window(scene_obj: Dict, replacement: Dict) -> None:
    replacements = {event["event_id"]: event for event in replacement.get("scene", [])}
    scene_obj["scene"] = [
        deepcopy(replacements.get(event.get("event_id"), event))
        for event in scene_obj.get("scene", [])
    ]
    for key, value in (replacement.get("position_descriptions") or {}).items():
        scene_obj.setdefault("position_descriptions", {}).setdefault(key, value)


async def _run_director_batched_draft(
    bridge: "AutoGenStreamBridge",
    characters: List[Character],
    scene: Scene,
    resource_loader: ResourceLoader,
    required_character_count: int,
    act_count: int,
    user_constraints: List[str],
    act_scene_map: Optional[Dict[int, Scene]],
    script_style_guide: Optional[str],
    creative_brief: str,
    meeting_summary: Dict,
    treatment: Dict,
    target_dialogue_lines: Optional[int],
    fixed_dialogues: List[dict],
) -> Optional[list]:
    """Freeze Story IR, then map at most eight exact event IDs per request."""
    specified_names = {character.name for character in characters}
    expected_character_count = required_character_count or len(characters) or 2
    locked_names: set[str] = set(specified_names)
    cast_established = len(locked_names) == expected_character_count
    manifest, explicit, manifest_error = _event_manifest(
        creative_brief,
        act_count,
        target_dialogue_lines,
        fixed_dialogues,
        [character.name for character in characters],
    )
    if manifest_error or manifest is None:
        bridge.put_event({'type': 'error', 'message': manifest_error or '无法建立事件清单。'})
        return None
    story_ir = await _build_story_ir(
        bridge, manifest, explicit, creative_brief, meeting_summary, treatment,
        [character.name for character in characters],
    )
    if story_ir is None:
        return None
    _canonicalize_character_references(story_ir, [character.name for character in characters])
    result: List[Dict] = []

    batch_constraints = [
        constraint for constraint in user_constraints
        if not re.search(r"\d+\s*(?:个|条)?\s*(?:镜头|分镜|事件|对白)", constraint)
    ]
    for act_index, act_ir in enumerate(story_ir):
        act_scene = (act_scene_map or {}).get(act_index, scene)
        merged: Optional[Dict] = None
        batches = _event_batches(act_ir)
        for batch_index, frozen_batch in enumerate(batches):
            continuity = {}
            if merged:
                continuity = {
                    "locked_characters": sorted(locked_names),
                    "initial_position": merged.get("initial position", []),
                    "position_descriptions": merged.get("position_descriptions", {}),
                    "previous_events": merged.get("scene", [])[-2:],
                }
            prompt = (
                f"当前是第 {act_index + 1}/{act_count} 幕、第 {batch_index + 1}/{len(batches)} 批。"
                f"代码已把整幕冻结为 {len(act_ir)} 个事件；本次只映射下面 {len(frozen_batch)} 个 story_ir 事件。"
                "输出 scene 必须与清单 ID 一一对应，不得添加移动过场、空镜或其他事件；"
                "必要的移动、动作与镜头意图必须写进对应事件；同一事件内每个角色最多一条 move。"
                "输出数组只能包含一个 scene_obj。\n"
                "story_ir=" + json.dumps(frozen_batch, ensure_ascii=False, separators=(",", ":"))
            )
            if not cast_established:
                prompt += f"\n首批须在 scene information.who 一次列出全剧全部 {expected_character_count} 位角色，后续锁定这些姓名。"
            else:
                prompt += f"\n全剧角色已锁定为：{', '.join(sorted(locked_names))}；不得新增、改名或遗漏。"
            if continuity:
                prompt += "\n承接状态：" + json.dumps(continuity, ensure_ascii=False, separators=(',', ':'))

            _emit_stage_log(
                bridge, 'info', 'draft', 'batch_start',
                f'📦 [剧本起草期] 第 {act_index + 1}/{act_count} 幕，第 {batch_index + 1}/{len(batches)} 批'
                f'（冻结事件 {len(frozen_batch)} 个）'
            )
            accepted = None
            candidate = None
            repair_window: List[dict] = []
            errors: List[str] = []
            for attempt in range(_DIRECTOR_BATCH_RETRIES + 1):
                def director_factory():
                    return create_director_agent(
                        characters, act_scene, resource_loader, required_character_count,
                        act_count=1, user_constraints=batch_constraints,
                        act_scene_map={0: act_scene} if act_scene_map else None,
                        script_style_guide=script_style_guide,
                    )

                director = director_factory()
                if attempt and not repair_window:
                    repair_window = _director_error_window(frozen_batch, errors)
                if repair_window and isinstance(candidate, dict):
                    repair_ids = {event["event_id"] for event in repair_window}
                    current_events = [
                        event for event in candidate.get("scene", [])
                        if event.get("event_id") in repair_ids
                    ]
                    attempt_prompt = (
                        "局部返修：只修改错误镜头及其前后各两个镜头。"
                        "输出数组只能包含一个 scene_obj，scene 必须严格返回下面这些 event_id；"
                        "不得返回本批其他镜头。未报错镜头保持原意，只作保证衔接所需的最小调整。\n"
                        "repair_story_ir=" + json.dumps(repair_window, ensure_ascii=False, separators=(",", ":"))
                        + "\ncurrent_events=" + json.dumps(current_events, ensure_ascii=False, separators=(",", ":"))
                        + "\nscene_context=" + json.dumps({
                            "scene information": candidate.get("scene information", {}),
                            "initial position": candidate.get("initial position", []),
                            "position_descriptions": candidate.get("position_descriptions", {}),
                        }, ensure_ascii=False, separators=(",", ":"))
                        + "\n需要修复：" + "；".join(errors)
                    )
                else:
                    attempt_prompt = prompt
                if errors and not repair_window:
                    attempt_prompt += "\n上次错误：" + "；".join(errors) + "。请从头返回本批，不能返回整幕。"
                batch_script = await _run_director_agent(
                    director, attempt_prompt, bridge,
                    f'DirectorAgent（第{act_index + 1}幕第{batch_index + 1}批）',
                )
                response = batch_script[0] if isinstance(batch_script, list) and len(batch_script) == 1 else None
                if (
                    repair_window
                    and isinstance(batch_script, list)
                    and not (len(batch_script) == 1 and isinstance(batch_script[0], dict) and "scene" in batch_script[0])
                    and all(isinstance(event, dict) for event in batch_script)
                ):
                    response = {"scene": batch_script}
                if repair_window:
                    response = _restore_frozen_event_fields(response, repair_window)
                    _canonicalize_character_references(response, list(locked_names))
                    repair_errors = _director_batch_errors(response, repair_window, set(), 0)
                    if repair_errors:
                        errors = repair_errors
                        continue
                    _replace_director_window(candidate, response)
                    repair_window = []
                else:
                    candidate = _restore_frozen_event_fields(response, frozen_batch)
                    _canonicalize_character_references(candidate, list(locked_names))
                errors = _director_batch_errors(
                    candidate, frozen_batch, locked_names, expected_character_count,
                )
                if not errors:
                    accepted = candidate
                    break
            if accepted is None:
                bridge.put_event({
                    'type': 'error',
                    'message': f'DirectorAgent 第 {act_index + 1} 幕第 {batch_index + 1} 批未通过冻结事件校验。',
                    'details': errors,
                })
                return None
            if not cast_established:
                locked_names = set((accepted.get("scene information") or {}).get("who") or [])
                cast_established = True
            merged = _merge_director_batch(merged, accepted)
            _emit_stage_log(
                bridge, 'success', 'draft', 'batch_complete',
                f'✅ 第 {act_index + 1} 幕第 {batch_index + 1}/{len(batches)} 批完成'
            )
        if merged is not None:
            merged_ids = [event.get("event_id") for event in merged.get("scene", [])]
            expected_ids = [event["event_id"] for event in act_ir]
            if merged_ids != expected_ids:
                bridge.put_event({'type': 'error', 'message': f'第 {act_index + 1} 幕合并后 event_id 不连续。'})
                return None
            result.append(merged)
    return result


async def _run_director_per_act_fallback(
    base_prompt: str,
    bridge: "AutoGenStreamBridge",
    characters: List[Character],
    scene: Scene,
    resource_loader: ResourceLoader,
    required_character_count: int,
    act_count: int,
    user_constraints: List[str],
    act_scene_map: Optional[Dict[int, Scene]],
    script_style_guide: Optional[str],
) -> Optional[list]:
    """Generate one act per request when the full-script request cannot stay connected."""
    details = dict(getattr(bridge, "last_error_details", None) or {})
    finish_reason = (details.get("model_response") or {}).get("finish_reason") or details.get("finish_reason")
    if act_count == 1 and (finish_reason == "length" or details.get("continuations", 0)):
        bridge.put_event({
            'type': 'error',
            'message': '[DirectorAgent] 长度截断恢复未成功，未获得完整 JSON；当前仅一幕，无法通过按幕拆分缩小请求。',
            'details': details,
        })
        return None
    scenes: list = []
    for act_index in range(act_count):
        act_scene = (act_scene_map or {}).get(act_index, scene)
        per_act_director = create_director_agent(
            characters,
            act_scene,
            resource_loader,
            required_character_count,
            act_count=1,
            user_constraints=user_constraints,
            act_scene_map={0: act_scene} if act_scene_map else None,
            script_style_guide=script_style_guide,
        )
        label = f"DirectorAgent（单幕降级，第{act_index + 1}/{act_count}幕）"
        _emit_stage_log(
            bridge, 'warning', 'draft', 'per_act_fallback',
            f'⚠️ [剧本起草期] 完整剧本请求失败，改为单幕生成：第 {act_index + 1}/{act_count} 幕。'
        )
        per_act_prompt = (
            f"{base_prompt}\n\n"
            f"## 单幕生成约束\n"
            f"现在只生成第 {act_index + 1} 幕，场景为 {act_scene.name}。"
            "输出 JSON 数组且只能包含 1 个 scene_obj；不要生成或复述其他幕。"
        )
        per_act_script = await _run_director_agent(per_act_director, per_act_prompt, bridge, label)
        if not per_act_script or len(per_act_script) != 1:
            details = dict(getattr(bridge, "last_error_details", None) or {})
            details.update({
                'act': act_index + 1,
                'expected_scene_count': 1,
                'received_scene_count': len(per_act_script) if per_act_script else 0,
                'fallback': 'per_act_json',
            })
            bridge.put_event({
                'type': 'error',
                'message': f'[DirectorAgent] 单幕降级在第 {act_index + 1} 幕仍未生成有效 JSON。',
                'details': details,
            })
            return None
        scenes.extend(per_act_script)
        _emit_stage_log(
            bridge, 'success', 'draft', 'per_act_complete',
            f'✅ [剧本起草期] 第 {act_index + 1}/{act_count} 幕单独生成完成。'
        )
    return scenes


_CHARACTER_SUFFIX_RE = re.compile(r"\s*[（(][^（）()]+[）)]\s*$")


def _canonicalize_character_references(scene_obj: Dict, known_names: List[str]) -> None:
    """Expand an unambiguous short name such as ``艾莉`` to ``艾莉 (F-01)``."""
    exact = {name.casefold(): name for name in known_names if name}
    short_names: Dict[str, List[str]] = {}
    for name in exact.values():
        short = _CHARACTER_SUFFIX_RE.sub("", name).strip().casefold()
        if short:
            short_names.setdefault(short, []).append(name)

    def canonical(value: Any) -> Any:
        if not isinstance(value, str) or not value.strip():
            return value
        key = value.strip().casefold()
        if key in exact:
            return exact[key]
        matches = short_names.get(key, [])
        return matches[0] if len(matches) == 1 else value

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"speaker", "character"}:
                    value[key] = canonical(item)
                elif key in {"who", "move_characters"} and isinstance(item, list):
                    value[key] = list(dict.fromkeys(canonical(name) for name in item))
                else:
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(scene_obj)


def _direct_collect_scene_characters(scene_obj: Dict, beats: List[Dict], fallback_names: List[str]) -> List[str]:
    names: List[str] = []
    seen = set()

    def add(name: Any) -> None:
        value = str(name or "").strip()
        if value and value not in seen and value.lower() not in _NARRATION_LABELS:
            seen.add(value)
            names.append(value)

    for name in (scene_obj.get("scene information") or {}).get("who", []) or []:
        add(name)
    for entry in scene_obj.get("initial position", []) or []:
        if isinstance(entry, dict):
            add(entry.get("character"))
    for beat in beats:
        if not isinstance(beat, dict):
            continue
        add(beat.get("speaker"))
        for action in beat.get("actions", []) or []:
            if isinstance(action, dict):
                add(action.get("character"))
        moves = beat.get("move", [])
        if isinstance(moves, dict):
            moves = [moves]
        for move in moves or []:
            if isinstance(move, dict):
                add(move.get("character"))
        for entry in beat.get("current position", []) or []:
            if isinstance(entry, dict):
                add(entry.get("character"))

    if not names:
        for name in fallback_names:
            add(name)
    return names


def _direct_current_position_map(entries: Any) -> Dict[str, str]:
    if not isinstance(entries, list):
        return {}
    result: Dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        character = str(entry.get("character") or "").strip()
        position = str(entry.get("position") or "").strip()
        if character and position:
            result[character] = position
    return result


def _direct_positions_are_collapsed(scene_obj: Dict, who: List[str]) -> bool:
    """Detect duplicate character positions in initial or event snapshots."""
    if len(who) < 2:
        return False
    who_set = set(who)

    initial_positions = [
        str(entry.get("position") or "").strip()
        for entry in scene_obj.get("initial position", []) or []
        if isinstance(entry, dict)
        and str(entry.get("character") or "").strip() in who_set
        and str(entry.get("position") or "").strip()
    ]
    if len(initial_positions) >= 2 and len(set(initial_positions)) < len(initial_positions):
        return True

    total_current_beats = 0
    collapsed_current_beats = 0
    for beat in scene_obj.get("scene", []) or []:
        if not isinstance(beat, dict):
            continue
        current_map = _direct_current_position_map(beat.get("current position"))
        positions = [current_map[name] for name in who if current_map.get(name)]
        if len(positions) >= 2:
            total_current_beats += 1
            if len(set(positions)) < len(positions):
                collapsed_current_beats += 1

    return collapsed_current_beats > 0


def _spread_direct_collapsed_positions(scene_obj: Dict, who: List[str], scene_name: str) -> None:
    """Spread collapsed Position 1/1/1 style assignments into stable per-character slots."""
    state_by_character = {
        str(item.get("character") or "").strip(): str(item.get("state") or "").strip()
        for item in scene_obj.get("initial position", []) or []
        if isinstance(item, dict) and str(item.get("character") or "").strip()
    }
    slot_by_character = {name: f"Position {index}" for index, name in enumerate(who, start=1)}
    scene_obj["initial position"] = [
        {
            "character": name,
            "position": slot_by_character[name],
            "state": state_by_character.get(name) or "standing",
        }
        for name in who
    ]

    position_descriptions = dict(scene_obj.get("position_descriptions") or {})
    for name, position in slot_by_character.items():
        position_descriptions[position] = position_descriptions.get(
            position,
            f"{scene_name} - {name} 的独立初始站位",
        )
    scene_obj["position_descriptions"] = position_descriptions

    current_positions = dict(slot_by_character)
    for beat in scene_obj.get("scene", []) or []:
        if not isinstance(beat, dict):
            continue
        raw_map = _direct_current_position_map(beat.get("current position"))
        raw_positions = [raw_map[name] for name in who if raw_map.get(name)]
        should_replace = not raw_map or (len(raw_positions) >= 2 and len(set(raw_positions)) < len(raw_positions))
        if should_replace:
            beat["current position"] = [
                {"character": name, "position": current_positions[name]}
                for name in who if current_positions.get(name)
            ]
        else:
            for name, position in raw_map.items():
                current_positions[name] = position
            for name in who:
                if name not in raw_map and current_positions.get(name):
                    beat.setdefault("current position", []).append({
                        "character": name,
                        "position": current_positions[name],
                    })

        moves = beat.get("move", []) or []
        if isinstance(moves, dict):
            moves = [moves]
        for move in moves:
            if isinstance(move, dict) and move.get("character") and move.get("destination"):
                current_positions[str(move["character"]).strip()] = str(move["destination"]).strip()


def _rebuild_move_timeline(scene_obj: Dict, scene_name: str) -> None:
    """用初始站位重建事件快照，并消除无效移动目的地。"""
    beats = scene_obj.get("scene", []) or []
    current = {
        str(item["character"]).strip(): str(item["position"]).strip()
        for item in (scene_obj.get("initial position") or [])
        if isinstance(item, dict) and item.get("character") and item.get("position")
    }
    descriptions = scene_obj.setdefault("position_descriptions", {})
    used_numbers = []
    for value in [*current.values(), *descriptions.keys()]:
        match = re.fullmatch(r"Position ([1-9]\d*)", str(value).strip())
        if match:
            used_numbers.append(int(match.group(1)))
    next_position = max(used_numbers, default=0) + 1

    for beat in beats:
        if not isinstance(beat, dict):
            continue
        beat["current position"] = [
            {"character": character, "position": position}
            for character, position in current.items()
        ]
        if "move" not in beat:
            continue

        moves = beat.get("move") or []
        if isinstance(moves, dict):
            moves = [moves]
        moves = [move for move in moves if isinstance(move, dict)]
        beat["move"] = moves
        beat.pop("actions", None)
        beat["shot"] = "scene"
        beat["camera"] = beat.get("camera") or 1
        beat.pop("shot_type", None)
        beat.pop("Follow", None)

        movers = {
            str(move.get("character", "")).strip()
            for move in moves
            if str(move.get("character", "")).strip() in current
        }
        blocked = {position for character, position in current.items() if character not in movers}
        claimed = set()
        for move in moves:
            character = str(move.get("character", "")).strip()
            if character not in current:
                continue
            destination = str(move.get("destination", "")).strip()
            invalid = (
                not re.fullmatch(r"Position [1-9]\d*", destination)
                or destination == current[character]
                or destination in blocked
                or destination in claimed
            )
            if invalid:
                destination = f"Position {next_position}"
                next_position += 1
                move["destination"] = destination
            descriptions.setdefault(destination, f"{scene_name} - {character} 的移动目标站位")
            claimed.add(destination)

        for move in moves:
            character = str(move.get("character", "")).strip()
            destination = str(move.get("destination", "")).strip()
            if character in current and destination:
                current[character] = destination


def _stabilize_position_timeline(scene_obj: Dict, scene_name: str) -> None:
    who = list((scene_obj.get("scene information") or {}).get("who") or [])
    if _direct_positions_are_collapsed(scene_obj, who):
        _spread_direct_collapsed_positions(scene_obj, who, scene_name)
        scene_obj["_direct_position_repair_applied"] = True
    _rebuild_move_timeline(scene_obj, scene_name)


def _stabilize_generated_posture_timeline(scene_obj: Dict) -> None:
    """Ensure movers stand first without changing the frozen event order."""
    beats = scene_obj.get("scene") or []
    initial_positions = scene_obj.get("initial position", []) or []
    initial_states = {
        item.get("character"): item.get("state", "standing")
        for item in initial_positions
        if isinstance(item, dict) and item.get("character")
    }

    while True:
        states = dict(initial_states)
        changed = False
        for index, beat in enumerate(beats):
            if not isinstance(beat, dict):
                continue
            movers = {
                move.get("character")
                for move in beat.get("move", []) if isinstance(move, dict)
            } if isinstance(beat.get("move"), list) else set()
            blocked = {character for character in movers if states.get(character) != "standing"}
            for character in blocked:
                target = next((
                    prior for prior in reversed(beats[:index])
                    if isinstance(prior, dict) and prior.get("speaker") and "move" not in prior
                ), None)
                future_stand = None
                for later in beats[index + 1:]:
                    if not isinstance(later, dict):
                        continue
                    if any(
                        isinstance(move, dict) and move.get("character") == character
                        for move in later.get("move", []) if isinstance(later.get("move"), list)
                    ):
                        break
                    for action in later.get("actions", []) if isinstance(later.get("actions"), list) else []:
                        if isinstance(action, dict) and action.get("character") == character and action.get("action") == "Stand Up":
                            later["actions"].remove(action)
                            future_stand = action
                            break
                    if future_stand:
                        break
                if target is not None:
                    target.setdefault("actions", []).append(future_stand or {
                        "character": character,
                        "state": states.get(character, "sitting"),
                        "action": "Stand Up",
                        "motion_detail": "Stand up before moving",
                    })
                else:
                    for item in initial_positions:
                        if isinstance(item, dict) and item.get("character") == character:
                            item["state"] = "standing"
                    initial_states[character] = "standing"
                changed = True
            if changed:
                break
            for action in beat.get("actions", []) if isinstance(beat.get("actions"), list) else []:
                if isinstance(action, dict):
                    character = action.get("character")
                    target_state = POSTURE_TRANSITION_TARGETS.get(action.get("action"))
                    if character and target_state:
                        states[character] = target_state
        if not changed:
            break

    states = dict(initial_states)
    for event_index, beat in enumerate(beats):
        if not isinstance(beat, dict):
            continue
        beat["event_index"] = event_index
        for action in beat.get("actions", []) if isinstance(beat.get("actions"), list) else []:
            if not isinstance(action, dict):
                continue
            character = action.get("character")
            if character in states:
                action["state"] = states[character]
            target_state = POSTURE_TRANSITION_TARGETS.get(action.get("action"))
            if character and target_state:
                states[character] = target_state


def _normalize_direct_scene(scene_obj: Dict, fallback_names: List[str], scene_name: str, what_snippet: str) -> Dict:
    """直接模式本地兜底规范化：只补技术字段，不改写用户对白。"""
    scene_obj = dict(scene_obj) if isinstance(scene_obj, dict) else {"scene": []}
    _canonicalize_character_references(scene_obj, fallback_names)
    raw_beats = scene_obj.get("scene") or []
    beats = [dict(item) for item in raw_beats if isinstance(item, dict)]
    who = _direct_collect_scene_characters(scene_obj, beats, fallback_names)

    info = dict(scene_obj.get("scene information") or {})
    info.setdefault("who", who)
    info.setdefault("where", scene_name)
    info.setdefault("what", what_snippet)
    scene_obj["scene information"] = info

    initial_positions = [
        dict(item) for item in (scene_obj.get("initial position") or [])
        if isinstance(item, dict) and item.get("character")
    ]
    existing_pos = {
        item.get("character"): item.get("position")
        for item in initial_positions
        if item.get("character") and item.get("position")
    }
    for index, name in enumerate(who, start=1):
        if name not in existing_pos:
            pos_id = f"Position {index}"
            initial_positions.append({"character": name, "position": pos_id})
            existing_pos[name] = pos_id
    scene_obj["initial position"] = initial_positions

    position_descriptions = dict(scene_obj.get("position_descriptions") or {})
    for name, pos_id in existing_pos.items():
        if pos_id and pos_id not in position_descriptions:
            position_descriptions[pos_id] = f"{scene_name} - {name} 的初始站位"
    scene_obj["position_descriptions"] = position_descriptions

    current_positions = dict(existing_pos)
    normalized_beats: List[Dict] = []
    for beat in beats:
        # Stable IDs belong to the compact IR; the final strict contract uses event_index.
        for ir_field in (
            "event_id", "source_event_id", "kind", "intent", "shot_intent",
            "speaker_locked", "required_kind", "source_text",
        ):
            beat.pop(ir_field, None)
        has_move = "move" in beat
        empty_shot = is_empty_shot(beat)
        if has_move and isinstance(beat.get("move"), dict):
            beat["move"] = [beat["move"]]

        if not beat.get("shot"):
            beat["shot"] = "scene" if has_move or empty_shot else "character"
        if empty_shot:
            protect_empty_shot(beat, ensure_camera=True)
        if not beat.get("shot_blend"):
            beat["shot_blend"] = "Cut"
        if beat["shot"] == "scene":
            if "camera" not in beat or beat.get("camera") is None:
                beat["camera"] = 1
        elif beat["shot"] == "object":
            beat["shot_type"] = beat.get("shot_type") or "物体中景"
            beat["target_anchor"] = beat.get("target_anchor") or "center"
            beat.pop("camera", None)
            beat.pop("Follow", None)
            beat.setdefault("actions", [])
        else:
            if not beat.get("shot_type"):
                beat["shot_type"] = "中景"
            if "Follow" not in beat or beat.get("Follow") is None:
                beat["Follow"] = 0
            beat.setdefault("actions", [])
        beat.setdefault("shot_description", "")

        if not beat.get("current position"):
            beat["current position"] = [
                {"character": name, "position": pos}
                for name, pos in current_positions.items() if pos
            ]
        else:
            for entry in beat.get("current position", []) or []:
                if isinstance(entry, dict) and entry.get("character") and entry.get("position"):
                    current_positions[entry["character"]] = entry["position"]

        normalized_beats.append(beat)

        moves = beat.get("move", []) or []
        if isinstance(moves, dict):
            moves = [moves]
        for move in moves:
            if isinstance(move, dict) and move.get("character") and move.get("destination"):
                current_positions[move["character"]] = move["destination"]

    scene_obj["scene"] = normalized_beats
    scene_obj["initial position"] = normalize_initial_position_states(scene_obj)
    _stabilize_position_timeline(scene_obj, scene_name)
    return scene_obj


def _extract_position_files(final_json: list, scene_id: str):
    """
    从剧本直接提取位置规划和位置详情（无需 LLM）。
    用于在摄影流水线未开启时也能提供可下载的位置文件。
    """
    char_pos: dict = {}  # position_id -> character (first-seen wins)
    position_metadata: dict = {}
    for scene_obj in (final_json or []):
        position_metadata.update(normalize_position_metadata(scene_obj))
        for entry in scene_obj.get("initial position", []):
            pos, char = entry.get("position", ""), entry.get("character", "")
            if pos and pos not in char_pos:
                char_pos[pos] = char
        for beat in scene_obj.get("scene", []):
            for entry in beat.get("current position", []):
                pos, char = entry.get("position", ""), entry.get("character", "")
                if pos and pos not in char_pos:
                    char_pos[pos] = char
            for move in beat.get("move", []):
                pos, char = move.get("destination", ""), move.get("character", "")
                if pos and pos not in char_pos:
                    char_pos[pos] = char

    singles = [{"position_id": p, "character": c, "region": "", "lookat": ""}
               for p, c in char_pos.items() if p]
    plan = {"where": scene_id, "groups": [], "singles": singles}
    detail_signals = [{"position_id": p, "character": c, "region": "", "lookat": ""}
                      for p, c in char_pos.items() if p]
    detail = {"where": scene_id, "groups": [], "singles": detail_signals}
    attach_position_metadata(plan, position_metadata)
    attach_position_metadata(detail, position_metadata)
    return plan, detail


# ── 直接模式：解析用户提供的剧本 ──────────────────────────────────
# 幕/场分隔标记（纯文本剧本）：第N幕 / 幕N / 场N / Act N / Scene N
_ACT_MARKER_RE = re.compile(
    r'^\s*(?:第\s*[0-9一二三四五六七八九十百]+\s*幕'
    r'|幕\s*[0-9一二三四五六七八九十百]+'
    r'|场景?\s*[0-9一二三四五六七八九十百]+'
    r'|Act\s*\d+|Scene\s*\d+)\b',
    re.IGNORECASE,
)
# 对白行：角色名: 内容（中英文冒号，角色名可被括号包裹）
_DLG_LINE_RE = re.compile(r'^\s*[（(]?([A-Za-z一-鿿·]{1,12})[)）]?\s*[:：]\s*(.+)$')
_NUMBERED_SHOT_RE = re.compile(r'^\s*(?:S\d{1,4}|SHOT\s*\d+|镜头\s*\d+|\d+[.)、])\s*', re.IGNORECASE)
# 旁白/画外音类标签：识别为旁白（不计入角色），避免生成幽灵演员
_NARRATION_LABELS = {'旁白', '独白', '画外音', '画外', '内心', '内心独白', '字幕', 'os', 'v.o.', 'vo', 'narration', 'narrator'}


def _parse_plaintext_script(text: str) -> List[Dict]:
    """将纯文本剧本解析为场景数组（每个场景含 beats 列表）。

    规则：
    - 幕/场标记行 → 切分场景（标记行本身不作为 beat）
    - "角色名: 对白" → 对白 beat
    - 其它非空行 → 旁白/动作描述 beat（speaker 为空）
    """
    acts: List[List[Dict]] = []
    cur: List[Dict] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if _ACT_MARKER_RE.match(line):
            if cur:
                acts.append(cur)
                cur = []
            continue
        m = _DLG_LINE_RE.match(line)
        if m and m.group(1).strip().lower() not in _NARRATION_LABELS:
            cur.append({"speaker": m.group(1).strip(), "content": m.group(2).strip(), "actions": []})
        elif m:
            # 旁白类标签 → 旁白 beat（去掉标签前缀，speaker 留空）
            cur.append({"speaker": "", "content": m.group(2).strip(), "actions": []})
        else:
            cur.append({"speaker": "", "content": line, "actions": []})
    if cur:
        acts.append(cur)
    if not acts:
        acts = [[]]
    return [{"scene": beats} for beats in acts]


def _fit_direct_act_count(scenes: List[Dict], act_count: int) -> List[Dict]:
    if len(scenes) == act_count:
        return scenes
    beats = [beat for scene_obj in scenes for beat in scene_obj.get("scene", [])]
    if len(beats) < act_count:
        return scenes
    base, remainder = divmod(len(beats), act_count)
    result, start = [], 0
    for act_index in range(act_count):
        size = base + (1 if act_index < remainder else 0)
        result.append({"scene": beats[start:start + size]})
        start += size
    return result


def _try_parse_json_scenes(text: str) -> Optional[List[Dict]]:
    """若用户输入本身就是规范 JSON（场景数组 / 单场景 / beats 数组），直接返回场景数组；否则 None。"""
    text = (text or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except Exception:
        return None

    if isinstance(parsed, list):
        if parsed and all(isinstance(x, dict) and ("scene" in x or "scene information" in x) for x in parsed):
            return parsed
        if parsed and all(isinstance(x, dict) and ("speaker" in x or "content" in x or "move" in x) for x in parsed):
            return [{"scene": parsed}]
        return None
    if isinstance(parsed, dict):
        if "scene" in parsed or "scene information" in parsed:
            return [parsed]
    return None


def _direct_batch_rows(text: str, act_count: int) -> Optional[List[List[str]]]:
    """Split only formats whose event boundaries are explicit and lossless."""
    groups: List[List[str]] = []
    current: List[str] = []
    saw_act_marker = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if _ACT_MARKER_RE.match(line):
            saw_act_marker = True
            if current:
                groups.append(current)
                current = []
            continue
        current.append(line)
    if current:
        groups.append(current)
    rows = [line for group in groups for line in group]
    if len(rows) <= _DIRECTOR_DIRECT_BATCH_SIZE or len(rows) < act_count:
        return None
    boundaries_are_explicit = all(_NUMBERED_SHOT_RE.match(line) or _DLG_LINE_RE.match(line) for line in rows)
    if not boundaries_are_explicit:
        return None
    if saw_act_marker and len(groups) == act_count:
        return groups
    base, remainder = divmod(len(rows), act_count)
    balanced, start = [], 0
    for act_index in range(act_count):
        size = base + (1 if act_index < remainder else 0)
        balanced.append(rows[start:start + size])
        start += size
    return balanced


def _direct_batch_matches_source(events: Any, records: List[dict]) -> bool:
    if not isinstance(events, list) or len(events) != len(records):
        return False
    for event, record in zip(events, records):
        if not isinstance(event, dict) or event.get("source_event_id") != record["source_event_id"]:
            return False
        source = _NUMBERED_SHOT_RE.sub("", record["raw"], count=1).strip()
        match = _DLG_LINE_RE.match(source)
        if match and match.group(1).strip().lower() not in _NARRATION_LABELS:
            if event.get("speaker") != match.group(1).strip() or event.get("content") != match.group(2).strip():
                return False
        elif event.get("content") != source and event.get("shot_description") != source:
            return False
    return True


async def _run_direct_text_batches(act_rows: List[List[str]], bridge, director_factory) -> Optional[List[Dict]]:
    scenes: List[Dict] = []
    for act_index, rows in enumerate(act_rows):
        merged = None
        all_records = [
            {
                "source_event_id": f"A{act_index + 1:02d}E{offset + 1:04d}",
                "raw": raw,
                "source_text": raw,
            }
            for offset, raw in enumerate(rows)
        ]
        if any(len(record["source_text"]) > _DIRECTOR_MAX_SOURCE_EVENT_CHARS for record in all_records):
            return None
        batches = _event_batches(all_records)
        for batch_index, records in enumerate(batches):
            prompt = (
                "只结构化下面这一批原始事件。每条输入恰好对应一个输出事件，逐字保留对白和顺序；"
                "每个事件额外返回 source_event_id。输出数组只能包含一个 scene_obj。\n"
                + json.dumps(
                    [{"source_event_id": item["source_event_id"], "raw": item["raw"]} for item in records],
                    ensure_ascii=False,
                    separators=(',', ':'),
                )
            )
            accepted = None
            for attempt in range(_DIRECTOR_BATCH_RETRIES + 1):
                candidate_script = await _run_director_agent(
                    director_factory(),
                    prompt + ("\n上次未通过 ID、数量或原文校验，请从头返回本批。" if attempt else ""),
                    bridge,
                    f'DirectorAgent（直接结构化，第{act_index + 1}幕第{batch_index + 1}批）',
                )
                candidate = candidate_script[0] if isinstance(candidate_script, list) and len(candidate_script) == 1 else None
                if candidate and _direct_batch_matches_source(candidate.get("scene"), records):
                    accepted = candidate
                    break
            if accepted is None:
                return None
            for event in accepted.get("scene", []):
                event.pop("source_event_id", None)
            merged = _merge_director_batch(merged, accepted)
        if merged is not None:
            scenes.append(merged)
    return scenes


async def _build_direct_draft(
    creative_idea: str,
    characters,
    scene,
    bridge,
    director_agent,
    *,
    act_count: int = 1,
    director_factory=None,
) -> List[Dict]:
    """直接模式：把用户剧本转成 draft_script（场景数组），并补齐 scene information / initial position。

    解析优先级：规范 JSON（不调 LLM）→ DirectorAgent 结构化（含站位/Position N）→ 本地规则解析（兜底）。
    返回结构与 DirectorAgent 产出一致，可直接喂给后续的验证 / 输出 / 摄影阶段。
    """
    text = (creative_idea or "").strip()
    _emit_stage_log(
        bridge, 'info', 'direct', 'input',
        f'📄 [直接模式] 收到用户剧本 {len(text)} 个字符，开始解析'
    )

    scenes = _try_parse_json_scenes(text)
    if scenes is not None:
        _emit_stage_log(bridge, 'info', 'direct', 'json',
                        f'🧩 [直接模式] 检测到 JSON 输入，解析到 {len(scenes)} 个场景，跳过导演结构化')
    elif text and director_factory and (act_rows := _direct_batch_rows(text, act_count)):
        _emit_stage_log(
            bridge, 'info', 'direct', 'batching',
            f'📦 [直接模式] 检测到明确事件边界，按每批最多 {_DIRECTOR_DIRECT_BATCH_SIZE} 个事件结构化'
        )
        scenes = await _run_direct_text_batches(act_rows, bridge, director_factory)
        if not scenes:
            scenes = _fit_direct_act_count(_parse_plaintext_script(text), act_count)
            _emit_stage_log(bridge, 'warning', 'direct', 'batch_fallback',
                            '⚠️ [直接模式] 分批结构化校验失败，改用本地规则解析以保留原文')
    elif text and len([line for line in text.splitlines() if line.strip() and not _ACT_MARKER_RE.match(line)]) > _DIRECTOR_DIRECT_BATCH_SIZE:
        scenes = _fit_direct_act_count(_parse_plaintext_script(text), act_count)
        _emit_stage_log(
            bridge, 'warning', 'direct', 'local_long_input',
            '⚠️ [直接模式] 长文本缺少可靠逐行镜头边界，改用本地规则解析；未向模型请求无界完整 JSON。'
        )
    elif text:
        _emit_stage_log(bridge, 'info', 'direct', 'parsing',
                        '🎬 [直接模式] DirectorAgent 将用户剧本结构化（保留对白与镜头、分配站位，不创作）...')
        import_prompt = (
            "以下是用户提供的完整剧本/分镜表，请严格按系统指令把它**结构化**为规范 JSON："
            "保留所有对白原文与每一个镜头，按用户「位置」为在场角色分配站位，不要创作或改写。\n\n"
            + text
        )
        scenes = await _run_director_agent(director_agent, import_prompt, bridge, 'DirectorAgent（直接结构化）')
        if scenes:
            _emit_stage_log(bridge, 'success', 'direct', 'parsed_llm',
                            f'✅ [直接模式] 导演结构化完成，共 {len(scenes)} 个场景')
        else:
            # LLM 失败 → 本地规则解析兜底
            scenes = _fit_direct_act_count(_parse_plaintext_script(text), act_count)
            _emit_stage_log(bridge, 'warning', 'direct', 'fallback',
                            '⚠️ [直接模式] 导演结构化失败，改用本地规则解析（对白可能不如 LLM 准确）')

    if not scenes:
        _emit_stage_log(bridge, 'warning', 'direct', 'empty',
                        '⚠️ [直接模式] 未解析到有效剧本内容，将输出空场景')
        scenes = [{"scene": []}]

    default_names = [c.name for c in characters]
    what_snippet = (creative_idea[:60] + ("…" if len(creative_idea) > 60 else "")) if creative_idea else scene.name

    normalized: List[Dict] = []
    for index, sc in enumerate(scenes, start=1):
        normalized_scene = _normalize_direct_scene(sc, default_names, scene.name, what_snippet)
        repaired_positions = bool(normalized_scene.pop("_direct_position_repair_applied", False))
        normalized.append(normalized_scene)
        beat_count = len(normalized_scene.get("scene", []))
        who_count = len(normalized_scene.get("scene information", {}).get("who", []))
        _emit_stage_log(
            bridge, 'info', 'direct', 'normalize_scene',
            f'🧱 [直接模式] 场景 {index} 已补齐基础字段：{who_count} 个角色 / {beat_count} 个片段'
        )
        if repaired_positions:
            _emit_stage_log(
                bridge, 'warning', 'direct', 'position_repair',
                f'📍 [直接模式] 场景 {index} 检测到多个角色被分到同一 Position，已按角色拆成独立站位'
            )

    total_beats = sum(len(s.get("scene", [])) for s in normalized)
    position_count = sum(
        len(s.get("position_metadata", {}) or s.get("position_descriptions", {}) or {})
        for s in normalized
    )
    _emit_stage_log(bridge, 'success', 'direct', 'parsed',
                    f'✅ [直接模式] 已解析用户剧本：{len(normalized)} 个场景 / {total_beats} 个片段，'
                    f'补齐 {position_count} 个站位描述，跳过头脑风暴与剧本起草')
    return normalized


async def run_autogen_pipeline(
    bridge: AutoGenStreamBridge,
    resource_loader: ResourceLoader,
    request_params: dict,
):
    """
    AutoGen 多 Agent 剧本生成主流程（协程）。
    通过 bridge.put_event() 向 Flask NDJSON 流发送事件。
    """

    # ── 解析参数 ──
    custom_characters_input = request_params.get('custom_characters', [])
    scene_id = request_params.get('scene_id')
    creative_idea = (request_params.get('creative_idea') or '').strip()
    requested_script_style_id = str(request_params.get('script_style_id') or '').strip()
    requested_script_tone_id = str(request_params.get('script_tone_id') or '').strip()
    dialogue_language = str(request_params.get('dialogue_language') or 'mandarin').strip()
    shot_style_reference = str(request_params.get('shot_style_reference') or '').strip()[:500]
    required_character_count = int(request_params.get('required_character_count', 0) or 0)
    act_count = max(1, min(10, int(request_params.get('act_count', 3) or 3)))
    # 直接模式：跳过创意会议/起草/审查，直接用用户提供的剧本（creative_idea），只补必要字段
    direct_mode = bool(request_params.get('direct_mode', False))

    plot_outline = creative_idea

    # ── 从 creative_idea 中自动提取用户约束 ──
    # 匹配 "不要..."、"必须..."、"不要修改..."、"不能..." 等显式约束语句
    _constraint_pattern = re.compile(
        r'不要[^\n。，、anger。]{2,50}?(?:[，。]|公司|\n|$)|'
        r'必须[^\n。，、anger。]{2,50}?(?:[，。]|公司|\n|$)|'
        r'不能[^\n。，、anger。]{2,50}?(?:[，。]|公司|\n|$)|'
        r'应当[^\n。，、anger。]{2,50}?(?:[，。]|公司|\n|$)|'
        r'必须[^\n]{2,30}?(?:。|\n)|'
        r'不要[^\n]{2,30}?(?:。|\n)',
        re.IGNORECASE
    )
    user_constraints = []
    if creative_idea and not direct_mode:
        for match in _constraint_pattern.finditer(creative_idea):
            phrase = match.group().strip()
            # 过滤掉太短的误匹配
            if len(phrase) >= 4:
                user_constraints.append(phrase)
    # 去重，保持顺序
    seen = set()
    user_constraints = [c for c in user_constraints if not (tuple(c) in seen or seen.add(tuple(c)))]
    if dialogue_language == 'minnan' and not direct_mode:
        user_constraints.append(
            '所有人物台词使用自然、口语化的闽南语（优先台湾闽南语常用汉字表达），'
            '必要时可在生僻词后用括号补普通话释义；场景、动作和镜头说明仍使用普通话。'
        )
    if shot_style_reference and not direct_mode:
        user_constraints.append(
            f'镜头风格参考：{shot_style_reference}。仅参考其节奏、构图、运镜和剪辑气质，'
            '不得照搬受版权保护的具体画面或内容。'
        )

    # ── 剧本风格 Skill：由导演入口统一锁定一次，供所有 Agent 共享 ──
    style_skill = ScriptStyleSkill()
    style_result = style_skill.resolve(creative_idea, requested_style_id=requested_script_style_id)
    script_style_guide = style_skill.render_context(creative_idea, requested_style_id=requested_script_style_id)
    selected_style = style_result.get("selected", {})
    style_source = '用户按钮选择' if style_result.get('source') == 'user_button' else '创作灵感自动识别'
    _emit_stage_log(
        bridge, 'info', 'setup', 'script_style',
        f"🎞️ 导演已锁定剧本风格: {selected_style.get('name', '未明确指定')}"
        f"（{style_source}）"
    )

    tone_skill = ScriptToneSkill()
    tone_result = tone_skill.resolve(requested_script_tone_id)
    script_style_guide = script_style_guide + "\n\n" + tone_skill.render_context(requested_script_tone_id)
    selected_tone = tone_result.get("selected", {})
    if tone_result.get("explicit"):
        _emit_stage_log(
            bridge, 'info', 'setup', 'script_tone',
            f"🎭 已锁定剧情倾向: {selected_tone.get('name', '未指定')}（用户按钮选择）"
        )

    # ── 从 creative_idea 中提取用户提供的固定对白（格式：角色名: 对白内容）──
    _fixed_dlg_pattern = re.compile(r'^([A-Za-z\u4e00-\u9fff]{1,8})\s*[:：]\s*([^\n]{1,200})', re.MULTILINE)
    fixed_dialogues = []
    fixed_dialogue_texts = set()
    if creative_idea and not direct_mode:
        for m in _fixed_dlg_pattern.finditer(creative_idea):
            speaker = m.group(1).strip()
            content = m.group(2).strip()
            if speaker and content and len(content) >= 2:
                key = f"{speaker}: {content}"
                if key not in fixed_dialogue_texts:
                    fixed_dialogue_texts.add(key)
                    fixed_dialogues.append({'speaker': speaker, 'content': content})
    if fixed_dialogues:
        logger.info("检测到 %d 句用户固定对白", len(fixed_dialogues))
        _emit_stage_log(bridge, 'info', 'setup', 'fixed_dialogue',
                        f'📌 检测到 {len(fixed_dialogues)} 句用户固定对白，将原样保留')

    # ── 从 creative_idea 中解析目标时长，反推 act_count ──
    # 匹配：5分钟、5min、时长5秒、5秒钟等
    _duration_pattern = re.compile(
        r'(\d+(?:\.\d+)?)\s*(分钟|min|mins?|分|秒(?:钟)?|seconds?|sec|s)',
        re.IGNORECASE
    )
    target_duration_secs = None
    if creative_idea and not direct_mode:
        m = _duration_pattern.search(creative_idea)
        if m:
            value = float(m.group(1))
            unit = m.group(2).lower()
            # 统一转为秒
            if unit in ('分钟', 'min', 'mins', 'm', '分'):
                target_duration_secs = value * 60
            else:
                target_duration_secs = value
            target_duration_secs = max(10, min(target_duration_secs, 3600))  # 10秒~1小时

    # 若用户未显式指定 act_count（API param 中没有传），则由时长反推
    # 经验值：1幕 ≈ 1~1.5 分钟剧情量，取 1幕/分钟，上限10
    user_passed_act_count = 'act_count' in request_params
    if target_duration_secs and not user_passed_act_count:
        derived_act_count = max(1, min(10, round(target_duration_secs / 60)))
        logger.info("时长反推 act_count: %.0f秒 → %d幕（原请求无 act_count 参数）",
                    target_duration_secs, derived_act_count)
        _emit_stage_log(bridge, 'info', 'setup', 'duration',
                        f'⏱️ 从时长反推：{target_duration_secs:.0f}秒 → 建议 {derived_act_count} 幕')
        act_count = derived_act_count

    duration_hint = (f'目标时长：约 {target_duration_secs:.0f}秒（{target_duration_secs/60:.1f}分钟）。'
                      if target_duration_secs else '')

    # ── 从时长换算目标对白行数 ──
    # 经验值：现实类对白约 10行/分钟能撑满1分钟影片（留白+沉默+动作间隙）
    # 目标行数 = 分钟数 × 10，上限300行
    target_dialogue_lines = None
    if target_duration_secs:
        target_dialogue_lines = max(6, min(300, round(target_duration_secs / 60 * 10)))
        logger.info("目标对白行数: %d行（%.1f分钟）", target_dialogue_lines, target_duration_secs / 60)

    logger.info("Pipeline 启动 | scene_id=%s characters=%d 约束数=%d 固定对白=%d 时长=%.0f秒 act_count=%d 目标行数=%s",
                scene_id, len(custom_characters_input), len(user_constraints),
                len(fixed_dialogues), target_duration_secs or 0, act_count,
                target_dialogue_lines)

    # ── 解析场景池（多场景一期；缺省回退单 scene_id 旧行为） ──
    scene_pool_ids = request_params.get('scene_pool') or []
    if not isinstance(scene_pool_ids, list):
        scene_pool_ids = []
    scene_pool_ids = [str(s).strip() for s in scene_pool_ids if str(s).strip()]
    if not scene_pool_ids and scene_id:
        scene_pool_ids = [scene_id]
    if not scene_pool_ids:
        bridge.put_event({'type': 'error', 'message': '未提供场景：请至少选择一个场景'})
        return

    # 预加载池内所有场景对象
    scene_pool_objs: List[Scene] = []
    for sid in scene_pool_ids:
        sc = resource_loader.get_scene_by_id(sid)
        if not sc:
            logger.error("场景不存在: %s", sid)
            bridge.put_event({'type': 'error', 'message': f'场景不存在: {sid}'})
            return
        scene_pool_objs.append(sc)

    multi_scene = len(scene_pool_objs) > 1

    # 多场景：每个场景都必须有坐标锚点（scene_info），否则摄影无法算坐标
    if multi_scene:
        for sc in scene_pool_objs:
            if resource_loader.load_scene_info(sc.id) is None:
                bridge.put_event({'type': 'error',
                                  'message': f'场景「{sc.name}」暂无坐标锚点，无法用于多场景生成，请改选其他场景'})
                return

    scene = scene_pool_objs[0]  # 默认场景，保留所有现有单 scene 引用

    # 构建 幕→场景 映射：act_scenes[i] 解析为池内 Scene；缺失/越界/无效 → 回退默认场景
    act_scenes_ids = request_params.get('act_scenes') or []
    if not isinstance(act_scenes_ids, list):
        act_scenes_ids = []
    _pool_by_id = {sc.id: sc for sc in scene_pool_objs}
    act_scene_map: Dict[int, Scene] = {}
    for i in range(act_count):
        sid = str(act_scenes_ids[i]).strip() if i < len(act_scenes_ids) and act_scenes_ids[i] else ''
        act_scene_map[i] = _pool_by_id.get(sid, scene)

    # 每幕场景 id 列表（下标=幕序号）；交给 generator 逐幕写 where，摄影按幕取锚点。单场景为 None
    act_scene_ids = [act_scene_map[i].id for i in range(act_count)] if multi_scene else None

    if multi_scene:
        _map_desc = "、".join(f"第{i + 1}幕={act_scene_map[i].name}" for i in range(act_count))
        _emit_stage_log(bridge, 'info', 'setup', 'multi_scene',
                        f'🎬 多场景模式：场景池 {len(scene_pool_objs)} 个，幕-场景分配：{_map_desc}')

    # ── 构建角色列表 ──
    if custom_characters_input:
        characters = resource_loader.build_custom_characters(custom_characters_input)
        _emit_stage_log(bridge, 'success', 'setup', 'characters', f'✅ 已构建 {len(characters)} 个自定义角色')
    else:
        characters = []
        _emit_stage_log(bridge, 'info', 'setup', 'characters', '💭 未指定角色，AI 将自由创作')

    # ── 初始化 Agents ──
    _emit_stage_log(bridge, 'info', 'setup', 'init', '🤖 初始化多 Agent 系统...')

    treatment = create_treatment_agent(act_count=act_count, script_style_guide=script_style_guide)
    meeting_summary_agent = create_meeting_summary_agent(script_style_guide=script_style_guide)
    critic = create_critic_agent(
        user_constraints=user_constraints,
        fixed_dialogues=fixed_dialogues,
        script_style_guide=script_style_guide,
    )
    dialogue = create_dialogue_agent(
        user_constraints=user_constraints,
        fixed_dialogues=fixed_dialogues,
        script_style_guide=script_style_guide,
    )
    _emit_stage_log(bridge, 'success', 'setup', 'ready', '✅ Agents 初始化完成（创意会议、大纲、导演、审查、验证）')

    # 阶段化上下文（内存态，不落盘）
    stage_context: Dict[str, Any] = {
        "meeting_minutes": "",
        "meeting_summary": {},
        "treatment": {},
    }

    # ════════════════════════════════════════════════
    # 创意阶段：创意会议 → 分场规划 → 起草 → 文学审查
    # 直接模式下整体跳过，改用用户提供的剧本
    # ════════════════════════════════════════════════
    if direct_mode:
        _emit_stage_log(bridge, 'info', 'direct', 'start',
                        '⚡ [直接模式] 已开启：跳过头脑风暴/分场规划，由导演把你的剧本结构化（保留剧情台词）')
        direct_director_factory = lambda: create_director_agent(
            characters, scene, resource_loader, required_character_count, 1,
            user_constraints=user_constraints, direct_mode=True,
            script_style_guide=script_style_guide,
        )
        direct_director = create_director_agent(
            characters, scene, resource_loader, required_character_count, act_count,
            user_constraints=user_constraints, direct_mode=True,
            script_style_guide=script_style_guide,
        )
        draft_script = await _build_direct_draft(
            creative_idea,
            characters,
            scene,
            bridge,
            direct_director,
            act_count=act_count,
            director_factory=direct_director_factory,
        )
        protect_empty_shots(draft_script, ensure_camera=True)
    else:
        # ════════════════════════════════════════════════
        # 阶段一：创意会议（ConceptPitch / CharacterVoice / NarrativeArch 轮流发言）
        # ════════════════════════════════════════════════
        _emit_stage_log(bridge, 'info', 'meeting', 'start',
                        '🎭 [创意会议] 三位创作顾问开始头脑风暴（每人最多发言 2 轮）...')

        concept_pitch = create_concept_pitch_agent(
            characters, scene, required_character_count,
            script_style_guide=script_style_guide,
        )
        character_voice = create_character_voice_agent(script_style_guide=script_style_guide)
        narrative_arch = create_narrative_arch_agent(script_style_guide=script_style_guide)

        # 每位最多 2 轮 = 最多 6 条消息；任意一位写出 [AGREE] 则提前终止
        _meeting_termination = MaxMessageTermination(6) | TextMentionTermination("[AGREE]")
        meeting_room = RoundRobinGroupChat(
            [concept_pitch, character_voice, narrative_arch],
            termination_condition=_meeting_termination,
        )

        if multi_scene:
            _pool_lines = "\n".join(f"  - {sc.name}：{sc.description}" for sc in scene_pool_objs)
            scene_brief_line = (
                f"场景池（剧情需分布到这些场景，每幕发生在其中一个）：\n{_pool_lines}\n"
            )
        else:
            scene_brief_line = f"场景：{scene.name} — {scene.description}\n"

        meeting_brief = (
            "创意会议开始，请各位从自己的专业角度展开讨论。\n\n"
            f"创作想法：{plot_outline or '（AI 自由创作）'}\n"
            f"{scene_brief_line}"
            f"角色数量：{required_character_count or len(characters) or 2} 位"
            + (f"\n已指定角色：{', '.join(c.name for c in characters)}" if characters else "")
            + "\n\n请 ConceptPitchAgent 先行发言，提出你的创意概念。"
        )

        meeting_messages: list = []
        try:
            meeting_result = await meeting_room.run(task=meeting_brief)
            for msg in meeting_result.messages:
                src = getattr(msg, 'source', '')
                content = getattr(msg, 'content', '')
                if not src or src == 'user' or not content:
                    continue
                meeting_messages.append({"agent": src, "content": content})
                _emit_stage_log(bridge, 'info', 'meeting', src,
                                f'💬 [{src}]\n{content}')
                _emit_output(bridge, src, content, fmt='meeting')
            _emit_stage_log(bridge, 'success', 'meeting', 'done',
                            f'✅ [创意会议] 完成，共 {len(meeting_messages)} 条发言')
        except Exception as _meet_exc:
            logger.warning("[Meeting] 会议异常：%s", _meet_exc)
            _emit_stage_log(bridge, 'warning', 'meeting', 'error',
                            f'⚠️ [创意会议] 出现异常，跳过会议阶段：{_meet_exc}')

        meeting_transcript = "\n\n".join(
            f"【{m['agent']}】{m['content']}" for m in meeting_messages
        ) if meeting_messages else f"创作方向：{plot_outline or '自由创作'}"
        stage_context["meeting_minutes"] = meeting_transcript

        _emit_stage_log(bridge, 'info', 'meeting_summary', 'start',
                        '📝 [创意摘要期] 正在提炼会议共识，后续阶段将只使用摘要...')
        summary_prompt = (
            f"创作构思：{plot_outline or '（AI 自由创作）'}\n"
            f"需要生成的幕数：{act_count}\n\n"
            f"以下是创意会议原文，请压缩为执行简报：\n{meeting_transcript}"
        )
        meeting_summary = await _run_stage_agent_json_object(meeting_summary_agent, summary_prompt)
        if meeting_summary:
            stage_context["meeting_summary"] = meeting_summary
            _emit_output(bridge, 'MeetingSummaryAgent', meeting_summary, fmt='stage')
            _emit_stage_log(bridge, 'success', 'meeting_summary', 'done',
                            '✅ [创意摘要期] 已生成精简执行简报')
        else:
            # Never put the full meeting transcript back into later prompts.
            stage_context["meeting_summary"] = {
                "core_premise": (plot_outline or '保持角色动机一致并逐步升级冲突。')[:1200],
                "director_brief": "根据创作构思生成连贯剧本，并遵守场景与角色约束。",
            }
            _emit_stage_log(bridge, 'warning', 'meeting_summary', 'fallback',
                            '⚠️ [创意摘要期] 摘要生成失败，已使用简短创作构思继续')

        # ════════════════════════════════════════════════
        # 阶段一后半：TreatmentAgent 将会议摘要转化为分场大纲
        # ════════════════════════════════════════════════
        _emit_stage_log(bridge, 'info', 'treatment', 'start',
                        '🗂️ [分场规划期] TreatmentAgent 根据创意摘要生成分场大纲...')
        meeting_summary_text = json.dumps(stage_context["meeting_summary"], ensure_ascii=False, indent=2)
        treatment_prompt = (
            f"以下是创意会议的执行摘要，请据此生成分场大纲：\n\n{meeting_summary_text}\n\n"
            f"指定角色约束：{json.dumps(custom_characters_input, ensure_ascii=False)}\n"
            f"幕数要求：恰好生成 {act_count} 个节拍（beat），JSON 数组长度严格为 {act_count}。\n" \
            + (f"{duration_hint}\n" if duration_hint else "")
            + "请生成分场大纲。"
        )
        treatment_result = await _run_stage_agent_json_object(treatment, treatment_prompt)
        if treatment_result:
            stage_context["treatment"] = treatment_result
            _emit_output(bridge, 'TreatmentAgent', treatment_result, fmt='stage')
            _emit_stage_log(bridge, 'success', 'treatment', 'summary', '✅ [分场规划期] Treatment 已生成')
        else:
            _emit_stage_log(bridge, 'warning', 'treatment', 'fallback',
                            '⚠️ [分场规划期] 输出解析失败，使用最小上下文继续')
            stage_context["treatment"] = {"draft_guidance": "保持冲突递进，保证角色动机一致。"}

        # ════════════════════════════════════════════════
        # 阶段二：剧本起草与文学审查
        # ════════════════════════════════════════════════
        _emit_stage_log(bridge, 'info', 'draft', 'start', '🎬 [剧本起草期] DirectorAgent 开始生成剧本初稿...')

        draft_script = await _run_director_batched_draft(
            bridge,
            characters,
            scene,
            resource_loader,
            required_character_count,
            act_count,
            user_constraints,
            act_scene_map if multi_scene else None,
            script_style_guide,
            plot_outline,
            stage_context["meeting_summary"],
            stage_context["treatment"],
            target_dialogue_lines,
            fixed_dialogues,
        )
        if draft_script is None:
            return

        draft_script = [
            _normalize_direct_scene(
                act,
                [character.name for character in characters],
                (act_scene_map.get(index, scene) if multi_scene else scene).name,
                plot_outline[:60] or "剧本生成",
            )
            for index, act in enumerate(draft_script)
        ]
        protect_empty_shots(draft_script, ensure_camera=True)
        logger.info("[DirectorAgent] 分批生成完成，场景数=%d", len(draft_script))
        _emit_output(bridge, 'DirectorAgent', draft_script)
        _emit_output(bridge, 'DirectorAgent（自然语言中间稿）', _render_plaintext_screenplay(draft_script), fmt='screenplay')
        _emit_stage_log(bridge, 'success', 'draft', 'shot_check', '✅ [shot结构] 所有分批结果已逐批校验')

        _emit_stage_log(bridge, 'success', 'draft', 'summary', '✅ [剧本起草期] 剧本初稿生成完成')

        # ── 阶段二 后半：文学审查（CriticAgent + DialogueAgent，循环修改）──
        for review_round in range(MAX_REVIEW_ROUNDS):
            _emit_stage_log(
                bridge, 'info', 'review', 'start',
                f'🔍 [审核与迭代期] 审查轮次 {review_round + 1}/{MAX_REVIEW_ROUNDS}：启动批评家与对白专家...'
            )

            filtered_script_str = _filter_script_for_review(draft_script)

            # CriticAgent 审查
            critic_feedback = None
            try:
                await _clear_agent_context(critic)
                async for event in critic.on_messages_stream(
                    [TextMessage(content=f"以下是需要审查的剧本：\n\n{filtered_script_str}", source="user")],
                    cancellation_token=CancellationToken()
                ):
                    if hasattr(event, 'chat_message') and event.chat_message:
                        critic_feedback = _extract_feedback_json(event.chat_message.content)
            except Exception as _e:
                logger.warning("[CriticAgent] 请求失败，跳过本轮审查: %s", _e)
                _emit_stage_log(bridge, 'warning', 'review', 'critic_error',
                                f'⚠️ [审核与迭代期] CriticAgent 请求失败，跳过本轮: {_e}')

            if critic_feedback:
                _emit_output(bridge, 'CriticAgent', critic_feedback, fmt='feedback')

            # DialogueAgent 审查
            dialogue_feedback = None
            try:
                await _clear_agent_context(dialogue)
                async for event in dialogue.on_messages_stream(
                    [TextMessage(content=f"以下是需要审查对白的剧本：\n\n{filtered_script_str}", source="user")],
                    cancellation_token=CancellationToken()
                ):
                    if hasattr(event, 'chat_message') and event.chat_message:
                        dialogue_feedback = _extract_feedback_json(event.chat_message.content)
            except Exception as _e:
                logger.warning("[DialogueAgent] 请求失败，跳过本轮审查: %s", _e)
                _emit_stage_log(bridge, 'warning', 'review', 'dialogue_error',
                                f'⚠️ [审核与迭代期] DialogueAgent 请求失败，跳过本轮: {_e}')

            if dialogue_feedback:
                _emit_output(bridge, 'DialogueAgent', dialogue_feedback, fmt='feedback')

            # 判断是否需要修改
            critic_has_issues = critic_feedback and critic_feedback.get('has_issues', False)
            dialogue_has_issues = dialogue_feedback and dialogue_feedback.get('has_issues', False)

            if not critic_has_issues and not dialogue_has_issues:
                _emit_stage_log(
                    bridge, 'success', 'review', 'result',
                    f'✅ [审核与迭代期] 审查通过（轮次{review_round + 1}），无需修改'
                )
                break

            # 汇总反馈，仅返修审查明确定位的对白。
            revision_parts = []
            if critic_has_issues:
                issues_str = '; '.join(i.get('description', '') for i in critic_feedback.get('issues', []))
                revision_parts.append(f"【剧情问题】{critic_feedback.get('revision_instruction', issues_str)}")
            if dialogue_has_issues:
                issues_str = '; '.join(i.get('description', '') for i in dialogue_feedback.get('issues', []))
                revision_parts.append(f"【对白问题】{dialogue_feedback.get('revision_instruction', issues_str)}")

            target_events = _review_target_events(draft_script, [critic_feedback, dialogue_feedback])
            if not target_events:
                _emit_stage_log(
                    bridge, 'warning', 'review', 'location_missing',
                    '⚠️ 审查意见没有合法的 act/event 定位，为避免重写整剧本，本轮不执行返修。'
                )
                break
            target_pairs = {(item["speaker"], item["content"]) for item in target_events}
            revision_prompt = json.dumps({
                "user_requirement": plot_outline[:2000],
                "revision_instructions": revision_parts,
                "target_events": target_events,
                "fixed_dialogues": [
                    item for item in fixed_dialogues
                    if (item.get("speaker"), item.get("content")) in target_pairs
                ],
            }, ensure_ascii=False, separators=(',', ':'))

            _emit_stage_log(
                bridge, 'info', 'review', 'revise',
                f'✏️  [审核与迭代期] DirectorAgent 根据审查意见修改剧本（轮次{review_round + 1}）...'
            )

            revision_result = None
            try:
                revision_result = await _run_stage_agent_json_object(create_revision_agent(), revision_prompt)
            except Exception as _e:
                logger.warning("[RevisionAgent] 请求失败，保留上一版本: %s", _e)
                _emit_stage_log(bridge, 'warning', 'review', 'revise_error',
                                f'⚠️ [审核与迭代期] 修改请求失败，保留上一版本: {_e}')

            revised_script, applied_changes = _apply_review_changes(
                draft_script,
                (revision_result or {}).get("changes"),
                fixed_dialogues,
                target_events,
            )
            if applied_changes:
                draft_script = revised_script
                _emit_stage_log(
                    bridge, 'success', 'review', 'revise_result',
                    f'✅ [审核与迭代期] 修改完成（轮次{review_round + 1}，{applied_changes} 处对白）'
                )
                _emit_output(bridge, 'RevisionAgent（对白补丁）', revision_result, fmt='feedback')
            else:
                _emit_stage_log(
                    bridge, 'warning', 'review', 'revise_result',
                    '⚠️ [审核与迭代期] 未收到可安全应用的对白补丁，保留上一版本'
                )
                break

    # ════════════════════════════════════════════════
    # 阶段四：总装与引擎合规验证
    # ════════════════════════════════════════════════

    # ── 阶段四 前半：技术约束验证 + Python 自动修复（基于真实点位 ID）──
    _emit_stage_log(bridge, 'info', 'validation', 'start', '🔧 [技术验证期] 开始技术约束验证...')

    validation_result = None

    # ValidationAgent is advisory; Python is always the authoritative gate.
    draft_script, validation_result = await _enforce_contract(
        draft_script, resource_loader, bridge, act_scene_map,
        act_count, [c.name for c in characters],
        required_character_count or len(characters) or 2,
        preserve_story=direct_mode,
    )

    # ── 阶段四 后半：最终封包输出（纯 Python）──
    import asyncio as _asyncio
    _running_loop = _asyncio.get_running_loop()
    timestamp = time.time_ns()
    output_dir = Path('outputs') / '.pending' / str(timestamp)
    output_dir.mkdir(parents=True, exist_ok=True)

    _emit_stage_log(bridge, 'info', 'output', 'start', '💾 [输出阶段] 正在生成最终 JSON 并保存文件...')

    generator = ScriptJSONGenerator(characters, scene)

    if creative_idea:
        plot_summary = creative_idea[:100] + ("..." if len(creative_idea) > 100 else "")
    elif characters:
        plot_summary = f"{len(characters)}个角色在{scene.name}的场景"
    else:
        plot_summary = f"AI自由创作：{scene.name}"

    final_json = generator.generate_final_json(draft_script, plot_summary, act_scene_ids=act_scene_ids)

    filename = f"script_{timestamp}.json"
    filepath = output_dir / filename

    script_title = await _generate_script_title(final_json, bridge)

    # 计算剧本估算时长（基于对白长度）
    def _calc_duration(script_json: list) -> dict:
        def _parse_duration_secs(value) -> float:
            if isinstance(value, (int, float)):
                return max(0.0, float(value))
            text = str(value or "").strip().lower()
            m = re.match(r'^(\d+(?:\.\d+)?)\s*(分钟|min|mins?|分|秒(?:钟)?|seconds?|sec|s)?$', text)
            if not m:
                return 5.0
            amount = float(m.group(1))
            unit = m.group(2) or "s"
            return amount * 60 if unit in ("分钟", "min", "mins", "分") else amount

        total_chars = 0
        total_lines = 0
        empty_shot_secs = 0.0
        for scene in script_json:
            for line in scene.get('scene', []):
                speaker = line.get('speaker', '') or ''
                content = line.get('content', '') or ''
                if speaker and content:
                    total_lines += 1
                    total_chars += len(content)
                elif is_empty_shot(line):
                    empty_shot_secs += _parse_duration_secs(line.get('duration', '5s'))
        # 按每字5字符/秒估算，附加场景/动作描述用时
        dialogue_secs = total_chars / 5
        # 场景切换、动作描述额外时间（按行数*3秒估算）
        scene_secs = total_lines * 3
        estimated_secs = dialogue_secs + scene_secs + empty_shot_secs
        return {
            'estimated_duration_seconds': round(estimated_secs),
            'dialogue_lines': total_lines,
            'dialogue_chars': total_chars,
            'empty_shot_seconds': round(empty_shot_secs),
        }

    duration_info = _calc_duration(final_json)

    # ── 阶段五前：从剧本直接提取位置文件（兜底，始终生成） ──
    camera_script_filename = None
    position_plan_filename = f"position_plan_{timestamp}.json"
    position_detail_filename = f"position_detail_{timestamp}.json"
    _base_plan, _base_detail = _extract_position_files(final_json, scene.id)
    with open(output_dir / position_plan_filename, 'w', encoding='utf-8') as _pf:
        json.dump(_base_plan, _pf, ensure_ascii=False, indent=2)
    with open(output_dir / position_detail_filename, 'w', encoding='utf-8') as _pdf:
        json.dump(_base_detail, _pdf, ensure_ascii=False, indent=2)

    # ── 阶段五：摄影指导后处理（默认启用） ──
    for cine_attempt in range(3):
        _emit_stage_log(bridge, 'info', 'cinematography', 'start', '🎥 [摄影指导期] 摄影指导智能体开始规划画面和镜头...')

        try:
            def _emit_cinematography_progress(level: str, phase: str, message: str) -> None:
                _emit_stage_log(bridge, level, 'cinematography', phase, message)

            cine_result = await _running_loop.run_in_executor(
                None,
                run_cinematography_pipeline,
                draft_script,
                scene,
                resource_loader.resource_dir,
                str(output_dir),
                timestamp,
                act_scene_map if multi_scene else None,
                _emit_cinematography_progress,
            )
            if cine_result.get("ok"):
                draft_script = cine_result["enriched_script"]
                protect_empty_shots(draft_script, ensure_camera=True)
                camera_script_filename = cine_result.get("camera_script_filename")
                position_plan_filename = cine_result.get("position_plan_filename")
                position_detail_filename = cine_result.get("position_detail_filename")
                # Per-act position files from this run are authoritative; never read shared stale stage files.
                checked, _ = normalize_script(draft_script, resource_loader)
                final_check = validate_script(checked, resource_loader, act_count=act_count,
                    required_names=[c.name for c in characters], character_count=required_character_count or len(characters) or 2)
                if not final_check['valid']:
                    if cine_attempt == 2:
                        raise ValueError(json.dumps(final_check['errors'], ensure_ascii=False))
                    draft_script, validation_result = await _enforce_contract(
                        draft_script, resource_loader, bridge, act_scene_map,
                        act_count, [c.name for c in characters], required_character_count or len(characters) or 2,
                        final=True,
                        preserve_story=direct_mode,
                    )
                    continue
                _emit_stage_log(bridge, 'success', 'cinematography', 'result',
                                f'✅ [摄影指导期] 摄影规划完成，镜头参数已写入 camera_script')
                break
            else:
                raise ValueError(f'摄影规划失败：{cine_result.get("error")}')
        except Exception as _cine_exc:
            logger.exception("[Cinematography] 阶段五异常")
            raise ValueError(f'摄影规划未通过，未发布最终文件：{_cine_exc}') from _cine_exc

    final_json, empty_warnings = normalize_script(draft_script, resource_loader)
    validation_result = validate_script(final_json, resource_loader, act_count=act_count,
        required_names=[c.name for c in characters], character_count=required_character_count or len(characters) or 2)
    validation_result['warnings'].extend(empty_warnings)
    if not validation_result['valid']:
        raise ValueError('摄影后剧本校验失败：' + json.dumps(validation_result['errors'], ensure_ascii=False))
    duration_info = _calc_duration(final_json)
    generator.export_to_file(final_json, str(filepath), resource_loader)

    # 提取出现的角色，生成 actors_profile.json
    actor_names = []
    seen: set = set()
    for scene_obj in draft_script:
        for name in scene_obj.get('scene information', {}).get('who', []):
            if name and name not in seen:
                seen.add(name)
                actor_names.append(name)

    char_file_path = resource_loader.resource_dir / "characters_resource.json"
    import json as _json
    with open(char_file_path, 'r', encoding='utf-8-sig') as f:
        all_chars_raw = _json.load(f)
    char_map = {c['name']: c for c in all_chars_raw}
    custom_char_map = {
        (item.get('name') or '').strip(): item
        for item in custom_characters_input
        if (item.get('name') or '').strip()
    }

    def _find_fallback_gameobject_name(target_name: str, target_gender: str = '') -> str:
        """
        当角色不在 characters_resource.json 中时，按相似度选取最近的角色的 gameobject_name。
        优先级：名称子串匹配 > 性别匹配 > 列表第一个
        """
        # 1. 名称子串匹配
        for cname, cdata in char_map.items():
            if target_name in cname or cname in target_name:
                logger.warning("角色 '%s' 不在资源库中，使用近似角色 '%s' 的 gameobject_name", target_name, cname)
                return cdata['gameobject_name']
        # 2. 性别匹配
        if target_gender:
            for cdata in all_chars_raw:
                if cdata.get('gender') == target_gender and cdata.get('gameobject_name'):
                    logger.warning("角色 '%s' 不在资源库中，按性别匹配使用 '%s' 的 gameobject_name", target_name, cdata['name'])
                    return cdata['gameobject_name']
        # 3. 兜底：取列表第一个
        for cdata in all_chars_raw:
            if cdata.get('gameobject_name'):
                logger.warning("角色 '%s' 不在资源库中，使用兜底角色 '%s' 的 gameobject_name", target_name, cdata['name'])
                return cdata['gameobject_name']
        return ''

    # 构建 character_bios 查找表（用于 AI 创作角色补充信息）
    bios_lookup = {}
    bios_data = stage_context.get("character_bios", {})
    if isinstance(bios_data, dict) and "character_bios" in bios_data:
        for bio in bios_data["character_bios"]:
            bios_lookup[bio.get("name", "")] = bio

    actors_profile = []
    for name in actor_names:
        if name in char_map:
            # 直接使用 characters_resource.json 中的完整数据
            actors_profile.append(char_map[name])
        elif name in custom_char_map:
            item = custom_char_map[name]
            # gameobject_name 必须来自 characters_resource.json，不足时 fallback
            gameobject_name = (char_map.get(name) or {}).get('gameobject_name') or item.get('gameobject_name') or ''
            if not gameobject_name:
                gameobject_name = _find_fallback_gameobject_name(name, item.get('gender') or '')
            # 兼容旧格式：personality_traits -> traits
            traits = item.get('traits') or []
            if not traits and item.get('personality_traits'):
                traits = [t.strip() for t in item['personality_traits'].split(',') if t.strip()]
            appearance = item.get('appearance') or {"height": "", "body_type": "", "hair": "", "face": ""}
            actors_profile.append({
                "name": name,
                "age": item.get('age'),
                "gender": item.get('gender') or '未知',
                "gameobject_name": gameobject_name,
                "appearance": appearance,
                "acting_style": item.get('acting_style') or '',
                "traits": traits,
                "background": item.get('background') or item.get('description') or f"用户自定义角色：{name}"
            })
        else:
            # AI 创作角色：优先从 character_bios 提取信息，否则 fallback
            bio = bios_lookup.get(name, {})
            gameobject_name = _find_fallback_gameobject_name(name, bio.get('gender') or '')
            actors_profile.append({
                "name": name,
                "age": bio.get('age'),
                "gender": bio.get('gender') or '未知',
                "gameobject_name": gameobject_name,
                "appearance": bio.get('appearance') or {"height": "", "body_type": "", "hair": "", "face": ""},
                "acting_style": '',
                "traits": bio.get('traits') or [],
                "background": bio.get('background') or f"AI自由创作角色：{name}"
            })

    actors_profile_filename = f"actors_profile_{timestamp}.json"
    actors_filepath = output_dir / actors_profile_filename
    with open(actors_filepath, 'w', encoding='utf-8') as f:
        _json.dump(actors_profile, f, ensure_ascii=False, indent=2)

    def read_output(name):
        return json.loads((output_dir / name).read_text(encoding='utf-8'))
    camera_document = normalize_camera_resources(read_output(camera_script_filename), final_json, resource_loader)
    (output_dir / camera_script_filename).write_text(json.dumps(camera_document, ensure_ascii=False, indent=2), encoding='utf-8')
    bundle_report = validate_bundle(final_json, camera_document, actors_profile,
        resource_loader, read_output(position_plan_filename), read_output(position_detail_filename))
    validation_result['errors'].extend(bundle_report['errors'])
    validation_result['warnings'].extend(bundle_report['warnings'])
    validation_result['valid'] = not validation_result['errors']
    report_filename = f'validation_{timestamp}.json'
    (output_dir / report_filename).write_text(json.dumps(validation_result, ensure_ascii=False, indent=2), encoding='utf-8')
    if not validation_result['valid']:
        raise ValueError('最终产物交叉校验失败：' + json.dumps(validation_result['errors'], ensure_ascii=False))
    # Only validated artifacts are made downloadable and registered as successful.
    for name in (filename, camera_script_filename, actors_profile_filename,
                 position_plan_filename, position_detail_filename, report_filename):
        os.replace(output_dir / name, Path('outputs') / name)

    _emit_stage_log(bridge, 'success', 'output', 'actors_profile', f'✅ 已生成角色档案：{len(actors_profile)} 位演员')

    session_id = str(request_params.get('_history_session_id') or timestamp)
    # 多场景：history 记录逗号拼接整池场景 id；单场景仍是单个 id
    session_scene_id = ",".join(sc.id for sc in scene_pool_objs)
    _registry.register_session(
        ts=session_id,
        files={
            "script": filename,
            "camera_script": camera_script_filename,
            "actors_profile": actors_profile_filename,
            "position_plan": position_plan_filename,
            "position_detail": position_detail_filename,
            "validation": report_filename,
        },
        scene_id=session_scene_id,
        act_count=act_count,
        label=script_title,
        form_data=_registry.snapshot_form_data(request_params),
    )

    logger.info("Pipeline 完成 | 剧本=%s 角色档案=%s 位置规划=%s 位置详情=%s",
                filename, actors_profile_filename,
                position_plan_filename or "（未生成）", position_detail_filename or "（未生成）")
    bridge.put_event({
        'type': 'success',
        'filename': filename,
        'camera_script_filename': camera_script_filename,
        'actors_profile_filename': actors_profile_filename,
        'position_plan_filename': position_plan_filename,
        'position_detail_filename': position_detail_filename,
        'session_id': session_id,
        'validation_filename': report_filename,
        'title': script_title,
        'estimated_duration': duration_info,
        'warnings': validation_result.get('warnings', []) if validation_result else []
    })
