"""
AutoGen Pipeline 编排模块

实现完整的多 Agent 剧本生成流程：
DirectorAgent → 审查层（CriticAgent + DialogueAgent）→ ValidationAgent → OutputAgent

通过 AutoGenStreamBridge 将 Agent 对话事件实时推送给 Flask NDJSON 流。
"""

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 网络错误关键词（用于区分连接抖动 vs 业务错误）
_NETWORK_ERR_KEYWORDS = ("connection", "timeout", "network", "remotedisconnected", "connect error")
_STAGE_MAX_RETRIES = 2       # 前置阶段 Agent 最大重试次数
_STAGE_RETRY_BASE_DELAY = 3  # 秒，每次翻倍

from autogen_agentchat.messages import TextMessage, ToolCallExecutionEvent, ModelClientStreamingChunkEvent
from autogen_core import CancellationToken

from .autogen_bridge import AutoGenStreamBridge
from . import registry as _registry
from .autogen_agents import (
    create_concept_agent,
    create_synopsis_agent,
    create_character_bios_agent,
    create_treatment_agent,
    create_director_agent,
    create_critic_agent,
    create_dialogue_agent,
    create_validation_agent,
    is_quota_error,
    make_fallback_model_client,
)
from .autogen_tools import validate_script_constraints, validate_json_spec, auto_fix_script
from .resource_loader import ResourceLoader, Character, Scene
from .json_generator import ScriptJSONGenerator
from .cinematography import run_cinematography_pipeline
from .schema import (
    validate_script_shot_structure, validate_script_shots,
    format_shot_structure_errors, format_shot_content_errors,
)


# 最大审查轮次（超限后强制进入验证阶段）
MAX_REVIEW_ROUNDS = 3


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


def _extract_json_from_text(text: str) -> Optional[list]:
    """从 Agent 输出文本中提取 JSON 数组"""
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

        result = json.loads(json_str)
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
        parsed = json.loads(json_str[start:end + 1])
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
    for scene_obj in script:
        filtered_scene = {
            "scene information": scene_obj.get("scene information", {}),
            "scene": []
        }
        for seg in scene_obj.get("scene", []):
            if "move" in seg:
                continue  # 移动片段不需要审查
            filtered_scene["scene"].append({
                "speaker": seg.get("speaker", ""),
                "content": seg.get("content", ""),
                "actions": [{"character": a.get("character", ""), "motion_detail": a.get("motion_detail", "")} for a in seg.get("actions", [])],
            })
        filtered.append(filtered_scene)
    return json.dumps(filtered, ensure_ascii=False, indent=2)


async def _run_stage_agent_json_object(agent, prompt: str) -> Optional[dict]:
    """执行阶段 Agent 并提取 JSON 对象结果。连接错误时指数退避重试；额度耗尽时换用备用模型。"""
    delay = _STAGE_RETRY_BASE_DELAY
    _quota_switched = False
    for attempt in range(_STAGE_MAX_RETRIES + 1):
        try:
            raw_content = None
            async for event in agent.on_messages_stream(
                [TextMessage(content=prompt, source="user")],
                cancellation_token=CancellationToken()
            ):
                if hasattr(event, 'chat_message') and event.chat_message:
                    raw_content = event.chat_message.content
            if not raw_content:
                return None
            return _extract_json_object_from_text(raw_content)
        except Exception as e:
            if is_quota_error(e) and not _quota_switched:
                logger.warning("[StageAgent] 额度耗尽，切换备用模型重试: %s", e)
                agent._model_client = make_fallback_model_client()
                _quota_switched = True
                continue
            is_network = any(k in str(e).lower() for k in _NETWORK_ERR_KEYWORDS)
            if is_network and attempt < _STAGE_MAX_RETRIES:
                logger.warning("[StageAgent] 连接错误，%.0fs后重试（%d/%d）: %s",
                               delay, attempt + 1, _STAGE_MAX_RETRIES, e)
                await asyncio.sleep(delay)
                delay *= 2
            else:
                raise
    return None


async def _run_director_agent(
    director,
    prompt: str,
    bridge: "AutoGenStreamBridge",
    label: str = "DirectorAgent",
) -> Optional[list]:
    """运行 DirectorAgent，处理 streaming 事件，返回解析后的 JSON 列表。连接错误时指数退避重试；额度耗尽时换用备用模型。"""
    delay = _STAGE_RETRY_BASE_DELAY
    _quota_switched = False
    for attempt in range(_STAGE_MAX_RETRIES + 1):
        thinking_started = False
        raw_content = None
        try:
            async for event in director.on_messages_stream(
                [TextMessage(content=prompt, source="user")],
                cancellation_token=CancellationToken()
            ):
                logger.debug("[%s] event type=%s", label, type(event).__name__)
                if hasattr(event, 'inner_messages'):
                    for msg in (event.inner_messages or []):
                        if isinstance(msg, ModelClientStreamingChunkEvent):
                            if not thinking_started:
                                thinking_started = True
                            bridge.put_event({'type': 'thinking_chunk', 'agent': label, 'text': msg.content})
                if hasattr(event, 'chat_message') and event.chat_message:
                    raw_content = event.chat_message.content
                    logger.info("[%s] 原始输出（前500字）: %s", label, raw_content[:500])
                    if thinking_started:
                        bridge.put_event({'type': 'thinking_done'})
                        thinking_started = False
            if thinking_started:
                bridge.put_event({'type': 'thinking_done'})
            if not raw_content:
                return None
            return _extract_json_from_text(raw_content)
        except Exception as e:
            if thinking_started:
                bridge.put_event({'type': 'thinking_done'})
            if is_quota_error(e) and not _quota_switched:
                logger.warning("[%s] 额度耗尽，切换备用模型重试: %s", label, e)
                bridge.put_event({'type': 'thinking_chunk', 'agent': label,
                                  'text': f'\n⚠️ 主模型额度耗尽，已切换备用模型重试...\n'})
                director._model_client = make_fallback_model_client()
                _quota_switched = True
                continue
            is_network = any(k in str(e).lower() for k in _NETWORK_ERR_KEYWORDS)
            if is_network and attempt < _STAGE_MAX_RETRIES:
                logger.warning("[%s] 连接错误，%.0fs后重试（%d/%d）: %s",
                               label, delay, attempt + 1, _STAGE_MAX_RETRIES, e)
                bridge.put_event({'type': 'thinking_chunk', 'agent': label,
                                  'text': f'\n⚠️ 连接错误，{delay:.0f}s 后重试（{attempt + 1}/{_STAGE_MAX_RETRIES}）...\n'})
                await asyncio.sleep(delay)
                delay *= 2
            else:
                raise
    return None


def _patch_shot_fields(target: list, source: list) -> None:
    """将 source 中每个 beat 的 shot 相关字段更新到 target（in-place）。"""
    shot_keys = ("shot", "shot_blend", "shot_type", "Follow", "camera")
    for t_scene, s_scene in zip(target, source):
        for t_beat, s_beat in zip(t_scene.get("scene", []), s_scene.get("scene", [])):
            for key in shot_keys:
                if key in s_beat:
                    t_beat[key] = s_beat[key]


def _extract_position_files(final_json: list, scene_id: str):
    """
    从剧本直接提取位置规划和位置详情（无需 LLM）。
    用于在摄影流水线未开启时也能提供可下载的位置文件。
    """
    char_pos: dict = {}  # position_id -> character (first-seen wins)
    for scene_obj in (final_json or []):
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

    singles = [{"position_id": p, "character": c, "region": "", "neartarget": "", "lookat": ""}
               for p, c in char_pos.items() if p]
    plan = {"where": scene_id, "groups": [], "singles": singles}
    detail_signals = [{"position_id": p, "character": c, "region": "", "neartarget": "", "lookat": ""}
                      for p, c in char_pos.items() if p]
    detail = {"where": scene_id, "groups": [], "signals": detail_signals}
    return plan, detail


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
    required_character_count = int(request_params.get('required_character_count', 0) or 0)
    act_count = max(1, min(10, int(request_params.get('act_count', 3) or 3)))

    plot_outline = creative_idea

    logger.info("Pipeline 启动 | scene_id=%s characters=%d", scene_id, len(custom_characters_input))

    # ── 验证场景 ──
    scene = resource_loader.get_scene_by_id(scene_id)
    if not scene:
        logger.error("场景不存在: %s", scene_id)
        bridge.put_event({'type': 'error', 'message': f'场景不存在: {scene_id}'})
        return

    # ── 构建角色列表 ──
    if custom_characters_input:
        characters = resource_loader.build_custom_characters(custom_characters_input)
        _emit_stage_log(bridge, 'success', 'setup', 'characters', f'✅ 已构建 {len(characters)} 个自定义角色')
    else:
        characters = []
        _emit_stage_log(bridge, 'info', 'setup', 'characters', '💭 未指定角色，AI 将自由创作')

    # ── 初始化 Agents ──
    model_supports_tools = os.getenv("MODEL_FUNCTION_CALLING", "false").lower() == "true"
    _emit_stage_log(bridge, 'info', 'setup', 'init', '🤖 初始化多 Agent 系统...')

    concept = create_concept_agent(characters, scene, required_character_count)
    synopsis = create_synopsis_agent()
    bios = create_character_bios_agent()
    treatment = create_treatment_agent(act_count=act_count)
    director = create_director_agent(characters, scene, resource_loader, required_character_count, act_count=act_count)
    critic = create_critic_agent()
    dialogue = create_dialogue_agent()
    validator = create_validation_agent(resource_loader, scene) if model_supports_tools else None

    _emit_stage_log(bridge, 'success', 'setup', 'ready', '✅ Agents 初始化完成（概念、梗概、人物、大纲、导演、审查、验证）')

    # 阶段化上下文（内存态，不落盘）
    stage_context: Dict[str, Dict[str, Any]] = {
        "concept": {},
        "synopsis": {},
        "character_bios": {},
        "treatment": {},
    }

    # ════════════════════════════════════════════════
    # 阶段一：前置统筹（概念链路 Logline → Synopsis → Bios → Treatment）
    # ════════════════════════════════════════════════
    _emit_stage_log(bridge, 'info', 'concept', 'start', '🧠 [概念孵化期] ConceptAgent 生成 Logline...')
    concept_prompt = (
        f"创作想法：{plot_outline or '（无）'}\n"
        "请产出 Logline 结果。"
    )
    concept_result = await _run_stage_agent_json_object(concept, concept_prompt)
    if concept_result:
        stage_context["concept"] = concept_result
        _emit_output(bridge, 'ConceptAgent', concept_result, fmt='stage')
        _emit_stage_log(bridge, 'success', 'concept', 'summary', '✅ [概念孵化期] Logline 已生成')
    else:
        _emit_stage_log(bridge, 'warning', 'concept', 'fallback', '⚠️ [概念孵化期] 输出解析失败，使用最小上下文继续')
        stage_context["concept"] = {"logline": plot_outline or scene.description}

    _emit_stage_log(bridge, 'info', 'synopsis', 'start', '📚 [故事梗概期] SynopsisAgent 扩展梗概...')
    synopsis_prompt = (
        f"创作想法：{plot_outline or '（无）'}\n\n"
        f"Concept 结果：\n{json.dumps(stage_context['concept'], ensure_ascii=False, indent=2)}\n\n"
        "请输出故事梗概。"
    )
    synopsis_result = await _run_stage_agent_json_object(synopsis, synopsis_prompt)
    if synopsis_result:
        stage_context["synopsis"] = synopsis_result
        _emit_output(bridge, 'SynopsisAgent', synopsis_result, fmt='stage')
        _emit_stage_log(bridge, 'success', 'synopsis', 'summary', '✅ [故事梗概期] Synopsis 已生成')
    else:
        _emit_stage_log(bridge, 'warning', 'synopsis', 'fallback', '⚠️ [故事梗概期] 输出解析失败，使用最小上下文继续')
        stage_context["synopsis"] = {"synopsis": plot_outline or scene.description}

    _emit_stage_log(bridge, 'info', 'character_bios', 'start', '👥 [人物塑形期] CharacterBiosAgent 生成人物小传...')
    bios_prompt = (
        f"创作想法：{plot_outline or '（无）'}\n\n"
        f"Concept：\n{json.dumps(stage_context['concept'], ensure_ascii=False, indent=2)}\n\n"
        f"Synopsis：\n{json.dumps(stage_context['synopsis'], ensure_ascii=False, indent=2)}\n\n"
        f"指定角色：\n{json.dumps(custom_characters_input, ensure_ascii=False, indent=2)}\n\n"
        f"角色总数要求：{required_character_count or len(characters) or 2}"
    )
    bios_result = await _run_stage_agent_json_object(bios, bios_prompt)
    if bios_result:
        stage_context["character_bios"] = bios_result
        _emit_output(bridge, 'CharacterBiosAgent', bios_result, fmt='stage')
        _emit_stage_log(bridge, 'success', 'character_bios', 'summary', '✅ [人物塑形期] Character Bios 已生成')
    else:
        _emit_stage_log(bridge, 'warning', 'character_bios', 'fallback', '⚠️ [人物塑形期] 输出解析失败，使用最小上下文继续')
        stage_context["character_bios"] = {"character_bios": custom_characters_input}

    _emit_stage_log(bridge, 'info', 'treatment', 'start', '🗂️ [分场规划期] TreatmentAgent 生成分场大纲...')
    treatment_prompt = (
        f"Concept：\n{json.dumps(stage_context['concept'], ensure_ascii=False, indent=2)}\n\n"
        f"Synopsis：\n{json.dumps(stage_context['synopsis'], ensure_ascii=False, indent=2)}\n\n"
        f"Character Bios：\n{json.dumps(stage_context['character_bios'], ensure_ascii=False, indent=2)}\n\n"
        f"幕数要求：恰好生成 {act_count} 个节拍（beat），JSON 数组长度严格为 {act_count}。\n"
        "请生成分场大纲。"
    )
    treatment_result = await _run_stage_agent_json_object(treatment, treatment_prompt)
    if treatment_result:
        stage_context["treatment"] = treatment_result
        _emit_output(bridge, 'TreatmentAgent', treatment_result, fmt='stage')
        _emit_stage_log(bridge, 'success', 'treatment', 'summary', '✅ [分场规划期] Treatment 已生成')
    else:
        _emit_stage_log(bridge, 'warning', 'treatment', 'fallback', '⚠️ [分场规划期] 输出解析失败，使用最小上下文继续')
        stage_context["treatment"] = {"draft_guidance": "保持冲突递进，保证角色动机一致。"}

    # ════════════════════════════════════════════════
    # 阶段二：剧本起草与文学审查
    # ════════════════════════════════════════════════
    _emit_stage_log(bridge, 'info', 'draft', 'start', '🎬 [剧本起草期] DirectorAgent 开始生成剧本初稿...')

    base_user_prompt = "请开始生成剧本，直接输出 JSON 格式，不要有其他说明文字。"
    if plot_outline:
        base_user_prompt = (
            f"创作想法：{plot_outline}\n\n"
            f"阶段化上下文：\n{json.dumps(stage_context, ensure_ascii=False, indent=2)}\n\n"
            "请根据以上阶段结果生成剧本，直接输出 JSON 格式，不要有其他说明文字。"
        )

    draft_script = None
    MAX_SHOT_STRUCT_RETRIES = 2
    current_prompt = base_user_prompt

    for shot_attempt in range(MAX_SHOT_STRUCT_RETRIES + 1):
        label = 'DirectorAgent' if shot_attempt == 0 else f'DirectorAgent（shot修正第{shot_attempt}次）'
        draft_script = await _run_director_agent(director, current_prompt, bridge, label)

        if draft_script is None:
            bridge.put_event({'type': 'error', 'message': '[DirectorAgent] 未能生成有效的 JSON 剧本'})
            return

        logger.info("[DirectorAgent] 生成完成，场景数=%d（尝试%d）", len(draft_script), shot_attempt + 1)
        _emit_output(bridge, label, draft_script)

        shot_struct = validate_script_shot_structure(draft_script)
        if shot_struct["valid"]:
            _emit_stage_log(bridge, 'success', 'draft', 'shot_check', '✅ [shot结构] 所有片段 shot 字段结构正确')
            break

        error_desc = format_shot_structure_errors(shot_struct["errors"])
        _emit_stage_log(bridge, 'warning', 'draft', 'shot_check',
                        f'⚠️ [shot结构] 字段有问题，正在修正...\n{error_desc}')

        if shot_attempt >= MAX_SHOT_STRUCT_RETRIES:
            _emit_stage_log(bridge, 'warning', 'draft', 'shot_check', '⚠️ 已达最大重试次数，继续使用当前版本')
            break

        current_prompt = (
            f"上一版本剧本 shot 字段有以下问题，请修正后重新输出完整剧本 JSON：\n\n"
            f"{error_desc}\n\n"
            f"原剧本：\n```json\n{json.dumps(draft_script, ensure_ascii=False, indent=2)}\n```"
        )

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

        # 汇总反馈，请 DirectorAgent 修改
        revision_parts = []
        if critic_has_issues:
            issues_str = '; '.join(i.get('description', '') for i in critic_feedback.get('issues', []))
            revision_parts.append(f"【剧情问题】{critic_feedback.get('revision_instruction', issues_str)}")
        if dialogue_has_issues:
            issues_str = '; '.join(i.get('description', '') for i in dialogue_feedback.get('issues', []))
            revision_parts.append(f"【对白问题】{dialogue_feedback.get('revision_instruction', issues_str)}")

        revision_prompt = (
            f"请根据以下审查意见修改剧本，输出完整的修改后 JSON，不要有其他说明文字：\n\n"
            + "\n".join(revision_parts)
            + "\n\n重要：每个角色动作的 `motion_detail` 字段必须保留原有内容，不得将其置为空字符串。\n\n当前剧本：\n```json\n{json.dumps(draft_script, ensure_ascii=False, indent=2)}\n```"
        )

        _emit_stage_log(
            bridge, 'info', 'review', 'revise',
            f'✏️  [审核与迭代期] DirectorAgent 根据审查意见修改剧本（轮次{review_round + 1}）...'
        )

        revised_script = None
        thinking_started = False
        try:
            async for event in director.on_messages_stream(
                [TextMessage(content=revision_prompt, source="user")],
                cancellation_token=CancellationToken()
            ):
                if hasattr(event, 'inner_messages'):
                    for msg in (event.inner_messages or []):
                        if isinstance(msg, ModelClientStreamingChunkEvent):
                            if not thinking_started:
                                thinking_started = True
                            bridge.put_event({'type': 'thinking_chunk', 'agent': 'DirectorAgent', 'text': msg.content})
                if hasattr(event, 'chat_message') and event.chat_message:
                    if thinking_started:
                        bridge.put_event({'type': 'thinking_done'})
                        thinking_started = False
                    revised_script = _extract_json_from_text(event.chat_message.content)
        except Exception as _e:
            logger.warning("[DirectorAgent-revision] 请求失败，保留上一版本: %s", _e)
            _emit_stage_log(bridge, 'warning', 'review', 'revise_error',
                            f'⚠️ [审核与迭代期] 修改请求失败，保留上一版本: {_e}')

        if thinking_started:
            bridge.put_event({'type': 'thinking_done'})

        if revised_script:
            draft_script = revised_script
            _emit_stage_log(
                bridge, 'success', 'review', 'revise_result',
                f'✅ [审核与迭代期] 修改完成（轮次{review_round + 1}）'
            )
            _emit_output(bridge, 'DirectorAgent（修改稿）', revised_script)
        else:
            _emit_stage_log(
                bridge, 'warning', 'review', 'revise_result',
                '⚠️ [审核与迭代期] 修改结果解析失败，保留上一版本'
            )
            break

    # ════════════════════════════════════════════════
    # 阶段四：总装与引擎合规验证
    # ════════════════════════════════════════════════

    # ── 阶段四 前半：技术约束验证 + Python 自动修复（基于真实点位 ID）──
    _emit_stage_log(bridge, 'info', 'validation', 'start', '🔧 [技术验证期] 开始技术约束验证...')

    validation_result = None

    if model_supports_tools:
        # 模型支持工具调用：由 ValidationAgent 调用 FunctionTool 验证
        draft_json_str = json.dumps(draft_script, ensure_ascii=False)
        async for event in validator.on_messages_stream(
            [TextMessage(content=f"请验证以下剧本 JSON 字符串：\n{draft_json_str}", source="user")],
            cancellation_token=CancellationToken()
        ):
            if hasattr(event, 'inner_messages'):
                for msg in (event.inner_messages or []):
                    if isinstance(msg, ToolCallExecutionEvent):
                        _emit_stage_log(bridge, 'info', 'validation', 'tool', '🔍 [技术验证期] 正在执行技术验证...')
            elif hasattr(event, 'chat_message') and event.chat_message:
                validation_result = _extract_validation_json(event.chat_message.content)

    if validation_result is None:
        # 直接用 Python 函数验证（主路径，或 Agent 输出解析失败时的兜底）
        logger.info("使用 Python 直接验证")
        constraints_result = validate_script_constraints(draft_script, scene, resource_loader)
        spec_result = validate_json_spec(draft_script)
        validation_result = {
            'valid': constraints_result['valid'] and spec_result['valid'],
            'errors': constraints_result['errors'] + spec_result['errors'],
            'warnings': constraints_result['warnings'] + spec_result['warnings'],
        }

    for w in validation_result.get('warnings', []):
        _emit_stage_log(bridge, 'warning', 'validation', 'warning', f'⚠️  {w}')

    if not validation_result.get('valid', False):
        errors = validation_result.get('errors', [])
        logger.warning("验证未通过 errors=%d，执行 Python 自动修复", len(errors))
        _emit_stage_log(bridge, 'info', 'validation', 'autofix', '🔧 [技术验证期] 执行自动修复...')

        draft_script = auto_fix_script(draft_script, scene, resource_loader)

        # 修复后二次验证，确认结果
        constraints_result = validate_script_constraints(draft_script, scene, resource_loader)
        spec_result = validate_json_spec(draft_script)
        validation_result = {
            'valid': constraints_result['valid'] and spec_result['valid'],
            'errors': constraints_result['errors'] + spec_result['errors'],
            'warnings': constraints_result['warnings'] + spec_result['warnings'],
        }
        for w in validation_result.get('warnings', []):
            _emit_stage_log(bridge, 'warning', 'validation', 'warning', f'⚠️  {w}')
        for e in validation_result.get('errors', []):
            _emit_stage_log(bridge, 'warning', 'validation', 'remaining_error', f'⚠️  自动修复后仍存在错误（将强制输出）: {e}')

    if validation_result.get('valid', False):
        _emit_stage_log(bridge, 'success', 'validation', 'result', '✅ [技术验证期] 技术约束验证通过')
    else:
        _emit_stage_log(bridge, 'warning', 'validation', 'result', '⚠️  [技术验证期] 部分技术错误无法自动修复，强制输出')

    _emit_output(bridge, 'ValidationAgent', validation_result, fmt='validation')

    # ── 阶段四 后半：最终封包输出（纯 Python）──
    import asyncio as _asyncio
    _running_loop = _asyncio.get_running_loop()
    timestamp = int(time.time())
    output_dir = Path('outputs')
    output_dir.mkdir(exist_ok=True)

    _emit_stage_log(bridge, 'info', 'output', 'start', '💾 [输出阶段] 正在生成最终 JSON 并保存文件...')

    generator = ScriptJSONGenerator(characters, scene)

    if creative_idea:
        plot_summary = creative_idea[:100] + ("..." if len(creative_idea) > 100 else "")
    elif characters:
        plot_summary = f"{len(characters)}个角色在{scene.name}的场景"
    else:
        plot_summary = f"AI自由创作：{scene.name}"

    final_json = generator.generate_final_json(draft_script, plot_summary)

    filename = f"script_{timestamp}.json"
    filepath = output_dir / filename
    generator.export_to_file(final_json, str(filepath))

    # ── 阶段五前：从剧本直接提取位置文件（兜底，始终生成） ──
    position_plan_filename = f"position_plan_{timestamp}.json"
    position_detail_filename = f"position_detail_{timestamp}.json"
    _base_plan, _base_detail = _extract_position_files(final_json, scene.id)
    with open(output_dir / position_plan_filename, 'w', encoding='utf-8') as _pf:
        json.dump(_base_plan, _pf, ensure_ascii=False, indent=2)
    with open(output_dir / position_detail_filename, 'w', encoding='utf-8') as _pdf:
        json.dump(_base_detail, _pdf, ensure_ascii=False, indent=2)

    # ── 阶段五（可选）：摄影指导后处理 ──
    if os.getenv("ENABLE_CINEMATOGRAPHY", "false").lower() == "true":
        _emit_stage_log(bridge, 'info', 'cinematography', 'start', '🎥 [摄影指导期] 摄影指导智能体开始规划画面和镜头...')
        try:
            cine_result = await _running_loop.run_in_executor(
                None,
                run_cinematography_pipeline,
                draft_script,
                scene,
                resource_loader.resource_dir,
                str(output_dir),
                timestamp,
            )
            if cine_result.get("ok"):
                draft_script = cine_result["enriched_script"]
                position_plan_filename = cine_result.get("position_plan_filename")
                position_detail_filename = cine_result.get("position_detail_filename")
                # 用摄影指导结果重新生成并覆写 script_*.json（保留已填写的摄影字段）
                final_json = generator.generate_final_json(draft_script, plot_summary, preserve_shot_fields=True)
                generator.export_to_file(final_json, str(filepath))
                _emit_stage_log(bridge, 'success', 'cinematography', 'result',
                                f'✅ [摄影指导期] 摄影规划完成，已更新剧本镜头参数')

                MAX_SHOT_CONTENT_RETRIES = 1
                for content_attempt in range(MAX_SHOT_CONTENT_RETRIES + 1):
                    shot_content = validate_script_shots(final_json)
                    if shot_content["valid"]:
                        _emit_stage_log(bridge, 'success', 'cinematography', 'shot_check',
                                        '✅ [shot内容] 所有镜头字段值合规')
                        break

                    error_desc = format_shot_content_errors(shot_content["errors"])
                    _emit_stage_log(bridge, 'warning', 'cinematography', 'shot_check',
                                    f'⚠️ [shot内容] 字段值有问题，正在修正...\n{error_desc}')

                    if content_attempt >= MAX_SHOT_CONTENT_RETRIES:
                        _emit_stage_log(bridge, 'warning', 'cinematography', 'shot_check',
                                        '⚠️ 已达最大重试次数')
                        break

                    fix_prompt = (
                        f"以下 shot 字段值不符合规范，请修正后输出完整剧本 JSON：\n\n"
                        f"{error_desc}\n\n"
                        f"当前剧本：\n```json\n{json.dumps(draft_script, ensure_ascii=False, indent=2)}\n```"
                    )
                    _emit_stage_log(bridge, 'info', 'cinematography', 'shot_retry',
                                    '✏️ DirectorAgent 修正 shot 字段值...')
                    corrected_draft = await _run_director_agent(
                        director, fix_prompt, bridge, "DirectorAgent（shot内容修正）"
                    )
                    if corrected_draft:
                        _patch_shot_fields(final_json, corrected_draft)
                        generator.export_to_file(final_json, str(filepath))
                        _emit_stage_log(bridge, 'info', 'cinematography', 'shot_retry',
                                        '✅ shot 字段已修正，重新校验...')
                    else:
                        _emit_stage_log(bridge, 'warning', 'cinematography', 'shot_retry',
                                        '⚠️ DirectorAgent 输出解析失败，跳过修正')
                        break
            else:
                _emit_stage_log(bridge, 'warning', 'cinematography', 'failed',
                                f'⚠️ [摄影指导期] 摄影规划失败（{cine_result.get("error")}），使用基础镜头参数继续')
        except Exception as _cine_exc:
            logger.exception("[Cinematography] 阶段五异常")
            _emit_stage_log(bridge, 'warning', 'cinematography', 'exception',
                            f'⚠️ [摄影指导期] 摄影规划异常：{_cine_exc}，继续使用基础镜头参数')

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
            # AI 创作角色：先精确匹配，匹配不到则 fallback 选近似角色
            char_data = char_map.get(name)
            if char_data:
                actors_profile.append(char_data)
            else:
                gameobject_name = _find_fallback_gameobject_name(name)
                actors_profile.append({
                    "name": name,
                    "age": None,
                    "gender": "未知",
                    "gameobject_name": gameobject_name,
                    "appearance": {"height": "", "body_type": "", "hair": "", "face": ""},
                    "acting_style": '',
                    "traits": [],
                    "background": f"AI自由创作角色：{name}"
                })

    actors_profile_filename = f"actors_profile_{timestamp}.json"
    actors_filepath = output_dir / actors_profile_filename
    with open(actors_filepath, 'w', encoding='utf-8') as f:
        _json.dump(actors_profile, f, ensure_ascii=False, indent=2)

    _emit_stage_log(bridge, 'success', 'output', 'actors_profile', f'✅ 已生成角色档案：{len(actors_profile)} 位演员')

    session_id = str(timestamp)
    _registry.register_session(
        ts=session_id,
        files={
            "script": filename,
            "actors_profile": actors_profile_filename,
            "position_plan": position_plan_filename,
            "position_detail": position_detail_filename,
        },
        scene_id=scene_id or "",
        act_count=act_count,
    )

    logger.info("Pipeline 完成 | 剧本=%s 角色档案=%s 位置规划=%s 位置详情=%s",
                filename, actors_profile_filename,
                position_plan_filename or "（未生成）", position_detail_filename or "（未生成）")
    bridge.put_event({
        'type': 'success',
        'filename': filename,
        'actors_profile_filename': actors_profile_filename,
        'position_plan_filename': position_plan_filename,
        'position_detail_filename': position_detail_filename,
        'session_id': session_id,
        'warnings': validation_result.get('warnings', []) if validation_result else []
    })
