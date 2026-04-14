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

from autogen_agentchat.messages import TextMessage, ToolCallExecutionEvent, ModelClientStreamingChunkEvent
from autogen_core import CancellationToken

from .autogen_bridge import AutoGenStreamBridge
from .autogen_agents import (
    create_concept_agent,
    create_synopsis_agent,
    create_character_bios_agent,
    create_treatment_agent,
    create_director_agent,
    create_critic_agent,
    create_dialogue_agent,
    create_validation_agent,
    create_position_agent,
)
from .autogen_tools import validate_script_constraints, validate_json_spec, auto_fix_script
from .position_agent_wrapper import run_position_agent
from .resource_loader import ResourceLoader, Character, Scene
from .json_generator import ScriptJSONGenerator


# 最大审查轮次（超限后强制进入验证阶段）
MAX_REVIEW_ROUNDS = 3
# PositionAgent 映射失败后最大重试次数
MAX_POSITION_FIX_ROUNDS = 3


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
            })
        filtered.append(filtered_scene)
    return json.dumps(filtered, ensure_ascii=False, indent=2)


async def _run_stage_agent_json_object(agent, prompt: str) -> Optional[dict]:
    """执行阶段 Agent 并提取 JSON 对象结果。"""
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


def _fallback_fix_positions(script: list, scene) -> list:
    """
    保底点位修正：将剧本中仍为抽象（Position N）或不在场景可用点位内的位置，
    轮询替换为场景真实点位 ID。场景无可用点位时直接返回原剧本。
    """
    valid_ids = [pos['id'] for pos in scene.valid_positions]
    if not valid_ids:
        return script

    # 轮询索引，让不同片段尽量分散到不同点位
    _counter = [0]

    def _next_valid() -> str:
        pos = valid_ids[_counter[0] % len(valid_ids)]
        _counter[0] += 1
        return pos

    def _fix_pos(pos_id: str) -> str:
        """若 pos_id 不在可用点位中则返回保底点位，否则原样返回。"""
        if pos_id and scene.get_position(pos_id):
            return pos_id
        return _next_valid()

    def _fix_pos_list(pos_list: list) -> list:
        return [
            {"character": entry.get("character", ""), "position": _fix_pos(entry.get("position", ""))}
            for entry in (pos_list or [])
        ]

    import copy
    result = copy.deepcopy(script)
    for scene_obj in result:
        # initial position
        if "initial position" in scene_obj:
            scene_obj["initial position"] = _fix_pos_list(scene_obj["initial position"])

        for seg in scene_obj.get("scene", []):
            # current position
            if "current position" in seg:
                seg["current position"] = _fix_pos_list(seg["current position"])

            # 移动片段 destination
            for move in seg.get("move", []):
                move["destination"] = _fix_pos(move.get("destination", ""))

    return result


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
    treatment = create_treatment_agent()
    director = create_director_agent(characters, scene, resource_loader, required_character_count)
    critic = create_critic_agent()
    dialogue = create_dialogue_agent()
    validator = create_validation_agent(resource_loader, scene) if model_supports_tools else None
    position_agent = create_position_agent(scene)

    _emit_stage_log(bridge, 'success', 'setup', 'ready', '✅ Agents 初始化完成（概念、梗概、人物、大纲、导演、审查、验证、位置）')

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

    user_prompt = "请开始生成剧本，直接输出 JSON 格式，不要有其他说明文字。"
    if plot_outline:
        user_prompt = (
            f"创作想法：{plot_outline}\n\n"
            f"阶段化上下文：\n{json.dumps(stage_context, ensure_ascii=False, indent=2)}\n\n"
            "请根据以上阶段结果生成剧本，直接输出 JSON 格式，不要有其他说明文字。"
        )

    draft_script = None
    thinking_started = False

    raw_content = None
    async for event in director.on_messages_stream(
        [TextMessage(content=user_prompt, source="user")],
        cancellation_token=CancellationToken()
    ):
        logger.debug("[DirectorAgent] event type=%s", type(event).__name__)
        if hasattr(event, 'inner_messages'):
            for msg in (event.inner_messages or []):
                if isinstance(msg, ModelClientStreamingChunkEvent):
                    if not thinking_started:
                        thinking_started = True
                    bridge.put_event({'type': 'thinking_chunk', 'agent': 'DirectorAgent', 'text': msg.content})
        if hasattr(event, 'chat_message') and event.chat_message:
            msg = event.chat_message
            raw_content = msg.content
            logger.info("[DirectorAgent] 原始输出（前500字）: %s", raw_content[:500])
            if thinking_started:
                bridge.put_event({'type': 'thinking_done'})
                thinking_started = False
            draft_script = _extract_json_from_text(raw_content)

    if thinking_started:
        bridge.put_event({'type': 'thinking_done'})

    if draft_script is None:
        logger.error("[DirectorAgent] JSON 提取失败，原始输出: %s", raw_content)
        bridge.put_event({'type': 'error', 'message': '[DirectorAgent] 未能生成有效的 JSON 剧本'})
        return

    logger.info("[DirectorAgent] 初稿生成完成，场景数=%d", len(draft_script))
    _emit_stage_log(bridge, 'success', 'draft', 'summary', '✅ [剧本起草期] 剧本初稿生成完成')
    _emit_output(bridge, 'DirectorAgent', draft_script)

    # ── 阶段二 后半：文学审查（CriticAgent + DialogueAgent，循环修改）──
    for review_round in range(MAX_REVIEW_ROUNDS):
        _emit_stage_log(
            bridge, 'info', 'review', 'start',
            f'🔍 [审核与迭代期] 审查轮次 {review_round + 1}/{MAX_REVIEW_ROUNDS}：启动批评家与对白专家...'
        )

        filtered_script_str = _filter_script_for_review(draft_script)

        # CriticAgent 审查
        critic_feedback = None
        async for event in critic.on_messages_stream(
            [TextMessage(content=f"以下是需要审查的剧本：\n\n{filtered_script_str}", source="user")],
            cancellation_token=CancellationToken()
        ):
            if hasattr(event, 'chat_message') and event.chat_message:
                critic_feedback = _extract_feedback_json(event.chat_message.content)

        if critic_feedback:
            _emit_output(bridge, 'CriticAgent', critic_feedback, fmt='feedback')

        # DialogueAgent 审查
        dialogue_feedback = None
        async for event in dialogue.on_messages_stream(
            [TextMessage(content=f"以下是需要审查对白的剧本：\n\n{filtered_script_str}", source="user")],
            cancellation_token=CancellationToken()
        ):
            if hasattr(event, 'chat_message') and event.chat_message:
                dialogue_feedback = _extract_feedback_json(event.chat_message.content)

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
            + f"\n\n当前剧本：\n```json\n{json.dumps(draft_script, ensure_ascii=False, indent=2)}\n```"
        )

        _emit_stage_log(
            bridge, 'info', 'review', 'revise',
            f'✏️  [审核与迭代期] DirectorAgent 根据审查意见修改剧本（轮次{review_round + 1}）...'
        )

        revised_script = None
        thinking_started = False
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
    # 阶段三：位置映射与坐标生成
    # ════════════════════════════════════════════════

    # ── 阶段三 前半：PositionAgent 位置映射（抽象 Position N → 真实点位 ID）──
    _emit_stage_log(bridge, 'info', 'position_mapping', 'start', '📍 [位置映射期] PositionAgent 开始位置映射...')

    for pos_round in range(MAX_POSITION_FIX_ROUNDS):
        mapping_prompt = (
            f"请将以下剧本中的抽象位置（Position 1/2/3...）映射到真实点位：\n\n"
            f"```json\n{json.dumps(draft_script, ensure_ascii=False, indent=2)}\n```"
        )
        mapped_script = None
        unresolved = []
        pos_raw_content = None

        async for event in position_agent.on_messages_stream(
            [TextMessage(content=mapping_prompt, source="user")],
            cancellation_token=CancellationToken()
        ):
            if hasattr(event, 'chat_message') and event.chat_message:
                pos_raw_content = event.chat_message.content
                unresolved = re.findall(r'POSITION_UNRESOLVED:\s*(.+)', pos_raw_content)
                mapped_script = _extract_json_from_text(pos_raw_content)

        if mapped_script and not unresolved:
            draft_script = mapped_script
            _emit_stage_log(bridge, 'success', 'position_mapping', 'result', '✅ [位置映射期] 位置映射完成')
            _emit_output(bridge, 'PositionAgent', mapped_script)
            break

        if unresolved:
            logger.warning("[PositionAgent] 无法解析的位置（轮次%d）: %s", pos_round + 1, unresolved)
            for u in unresolved:
                _emit_stage_log(bridge, 'warning', 'position_mapping', 'unresolved', f'⚠️  [PositionAgent] 无法映射: {u}')

            if mapped_script:
                # 部分映射成功，仍更新 draft
                draft_script = mapped_script

            if pos_round < MAX_POSITION_FIX_ROUNDS - 1:
                # 请 DirectorAgent 修改无法映射的位置戏剧意图
                fix_prompt = (
                    "以下站位在场景中找不到合理匹配，请修改剧本，"
                    "调整这些位置的 position_descriptions（换用场景实际存在的空间特征描述）：\n\n"
                    + "\n".join(f"- {u}" for u in unresolved)
                    + f"\n\n当前剧本：\n```json\n{json.dumps(draft_script, ensure_ascii=False, indent=2)}\n```"
                    + "\n\n直接输出修改后的完整 JSON，不要有其他说明文字。"
                )
                _emit_stage_log(
                    bridge, 'info', 'position_mapping', 'revise',
                    f'✏️  [位置映射期] DirectorAgent 根据位置反馈修改剧本（轮次{pos_round + 1}）...'
                )
                async for event in director.on_messages_stream(
                    [TextMessage(content=fix_prompt, source="user")],
                    cancellation_token=CancellationToken()
                ):
                    if hasattr(event, 'chat_message') and event.chat_message:
                        revised = _extract_json_from_text(event.chat_message.content)
                        if revised:
                            draft_script = revised
            else:
                _emit_stage_log(bridge, 'warning', 'position_mapping', 'force_continue', '⚠️  [PositionAgent] 已达映射上限，使用当前最优结果继续')
                break
        else:
            # mapped_script 为 None，解析失败
            logger.warning("[PositionAgent] JSON 解析失败（轮次%d），原始输出: %s",
                           pos_round + 1, pos_raw_content[:300] if pos_raw_content else "None")
            if pos_round >= MAX_POSITION_FIX_ROUNDS - 1:
                _emit_stage_log(bridge, 'warning', 'position_mapping', 'parse_failed', '⚠️  [PositionAgent] 解析失败，跳过位置映射，使用抽象位置继续')
            # 不更新 draft_script，继续重试

    # ── 阶段三 中：保底点位修正（将仍为抽象/无效的位置替换为场景真实点位）──
    draft_script = _fallback_fix_positions(draft_script, scene)

    # ── 阶段三 后半：position_agent_standalone 坐标生成（可选，需 scene_export + template 文件）──
    position_filename = None
    import asyncio as _asyncio
    timestamp = int(time.time())
    output_dir = Path('outputs')
    output_dir.mkdir(exist_ok=True)
    temp_script_path = None
    try:
        temp_script_path = output_dir / f"_temp_script_{timestamp}.json"
        with open(temp_script_path, 'w', encoding='utf-8') as _f:
            json.dump(draft_script, _f, ensure_ascii=False, indent=2)

        position_output_filename = f"position_{timestamp}.json"
        pos_result = await _asyncio.get_event_loop().run_in_executor(
            None,
            run_position_agent,
            str(temp_script_path),
            scene.id,
            str(output_dir),
            position_output_filename,
        )

        if pos_result.get("ok"):
            position_filename = position_output_filename
            _emit_stage_log(bridge, 'success', 'position_generation', 'result', f'✅ [位置映射期] 坐标文件生成完成：{position_output_filename}')
        elif pos_result.get("skip"):
            _emit_stage_log(bridge, 'info', 'position_generation', 'skip', f'⏭️  [位置映射期] 跳过坐标生成（{pos_result.get("error", "缺少资源文件")}）')
        else:
            _emit_stage_log(bridge, 'warning', 'position_generation', 'failed', f'⚠️  [位置映射期] 坐标生成失败：{pos_result.get("error", "未知错误")}')
    except Exception as _e:
        logger.exception("[PositionAgent] 坐标生成异常")
        _emit_stage_log(bridge, 'warning', 'position_generation', 'exception', f'⚠️  [位置映射期] 坐标生成异常：{_e}')
    finally:
        if temp_script_path and temp_script_path.exists():
            temp_script_path.unlink(missing_ok=True)

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

    logger.info("Pipeline 完成 | 剧本=%s 角色档案=%s 坐标=%s",
                filename, actors_profile_filename, position_filename or "（未生成）")
    bridge.put_event({
        'type': 'success',
        'filename': filename,
        'actors_profile_filename': actors_profile_filename,
        'position_filename': position_filename,
        'warnings': validation_result.get('warnings', []) if validation_result else []
    })
