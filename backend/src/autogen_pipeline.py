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
from typing import List, Optional

logger = logging.getLogger(__name__)

from autogen_agentchat.messages import TextMessage, ToolCallExecutionEvent, ModelClientStreamingChunkEvent
from autogen_core import CancellationToken

from .autogen_bridge import AutoGenStreamBridge
from .autogen_agents import (
    create_director_agent,
    create_critic_agent,
    create_dialogue_agent,
    create_validation_agent,
    create_position_agent,
)
from .autogen_tools import validate_script_constraints, validate_json_spec
from .position_agent_wrapper import run_position_agent
from .resource_loader import ResourceLoader, Character, Scene
from .json_generator import ScriptJSONGenerator


# 最大审查轮次（超限后强制进入验证阶段）
MAX_REVIEW_ROUNDS = 3
# 技术验证失败后最大修复次数
MAX_FIX_ROUNDS = 3
# PositionAgent 映射失败后最大重试次数
MAX_POSITION_FIX_ROUNDS = 3


def _emit_output(bridge: "AutoGenStreamBridge", agent: str, content, fmt: str = 'script') -> None:
    """将 agent 输出以结构化事件推送到前端"""
    bridge.put_event({'type': 'log', 'level': 'output', 'format': fmt, 'agent': agent, 'data': content})


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
        bridge.put_event({
            'type': 'log', 'level': 'success',
            'message': f'✅ 已构建 {len(characters)} 个自定义角色'
        })
    else:
        characters = []
        bridge.put_event({
            'type': 'log', 'level': 'info',
            'message': '💭 未指定角色，AI 将自由创作'
        })

    # ── 初始化 Agents ──
    model_supports_tools = os.getenv("MODEL_FUNCTION_CALLING", "false").lower() == "true"
    bridge.put_event({'type': 'log', 'level': 'info', 'message': '🤖 初始化多 Agent 系统...'})

    director = create_director_agent(characters, scene, resource_loader, required_character_count)
    critic = create_critic_agent()
    dialogue = create_dialogue_agent()
    validator = create_validation_agent(resource_loader, scene) if model_supports_tools else None
    position_agent = create_position_agent(scene)

    bridge.put_event({'type': 'log', 'level': 'success', 'message': '✅ Agents 初始化完成（导演、批评家、对白专家、技术验证、位置映射）'})

    # ════════════════════════════════════════════════
    # 阶段①：DirectorAgent 生成初稿
    # ════════════════════════════════════════════════
    bridge.put_event({
        'type': 'log', 'level': 'info',
        'message': '🎬 [DirectorAgent] 开始生成剧本初稿...'
    })

    user_prompt = "请开始生成剧本，直接输出 JSON 格式，不要有其他说明文字。"
    if plot_outline:
        user_prompt = f"创作想法：{plot_outline}\n\n请根据以上创作想法生成剧本，直接输出 JSON 格式，不要有其他说明文字。"

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
    bridge.put_event({'type': 'log', 'level': 'success', 'message': '✅ [DirectorAgent] 剧本初稿生成完成'})
    _emit_output(bridge, 'DirectorAgent', draft_script)

    # ════════════════════════════════════════════════
    # 阶段②：审查层（CriticAgent + DialogueAgent，循环修改）
    # ════════════════════════════════════════════════
    for review_round in range(MAX_REVIEW_ROUNDS):
        bridge.put_event({
            'type': 'log', 'level': 'info',
            'message': f'🔍 审查轮次 {review_round + 1}/{MAX_REVIEW_ROUNDS}：启动批评家与对白专家...'
        })

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
            bridge.put_event({
                'type': 'log', 'level': 'success',
                'message': f'✅ 审查通过（轮次{review_round + 1}），无需修改'
            })
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

        bridge.put_event({
            'type': 'log', 'level': 'info',
            'message': f'✏️  [DirectorAgent] 根据审查意见修改剧本（轮次{review_round + 1}）...'
        })

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
            bridge.put_event({'type': 'log', 'level': 'success', 'message': f'✅ 修改完成（轮次{review_round + 1}）'})
            _emit_output(bridge, 'DirectorAgent（修改稿）', revised_script)
        else:
            bridge.put_event({'type': 'log', 'level': 'warning', 'message': f'⚠️  修改结果解析失败，保留上一版本'})
            break

    # ════════════════════════════════════════════════
    # 阶段③：技术约束验证
    # ════════════════════════════════════════════════
    bridge.put_event({'type': 'log', 'level': 'info', 'message': '🔧 开始技术约束验证...'})

    draft_json_str = json.dumps(draft_script, ensure_ascii=False)
    validation_result = None

    for fix_round in range(MAX_FIX_ROUNDS + 1):
        if model_supports_tools:
            # 模型支持工具调用：由 ValidationAgent 调用 FunctionTool 验证
            validation_result = None
            async for event in validator.on_messages_stream(
                [TextMessage(content=f"请验证以下剧本 JSON 字符串：\n{draft_json_str}", source="user")],
                cancellation_token=CancellationToken()
            ):
                if hasattr(event, 'inner_messages'):
                    for msg in (event.inner_messages or []):
                        if isinstance(msg, ToolCallExecutionEvent):
                            bridge.put_event({
                                'type': 'log', 'level': 'info',
                                'message': '🔍 [ValidationAgent] 正在执行技术验证...'
                            })
                elif hasattr(event, 'chat_message') and event.chat_message:
                    validation_result = _extract_validation_json(event.chat_message.content)

        if validation_result is None:
            # 直接用 Python 函数验证（不支持工具调用时的主路径，或 agent 输出解析失败时的兜底）
            logger.info("使用 Python 直接验证（fix_round=%d）", fix_round)
            constraints_result = validate_script_constraints(draft_script, scene, resource_loader)
            spec_result = validate_json_spec(draft_script)
            validation_result = {
                'valid': constraints_result['valid'] and spec_result['valid'],
                'errors': constraints_result['errors'] + spec_result['errors'],
                'warnings': constraints_result['warnings'] + spec_result['warnings'],
            }

        if validation_result.get('valid', False):
            bridge.put_event({'type': 'log', 'level': 'success', 'message': '✅ 技术约束验证通过'})
            for w in validation_result.get('warnings', []):
                bridge.put_event({'type': 'log', 'level': 'warning', 'message': f'⚠️  {w}'})
            _emit_output(bridge, 'ValidationAgent', validation_result, fmt='validation')
            break

        errors = validation_result.get('errors', [])
        warnings = validation_result.get('warnings', [])
        logger.warning("验证未通过 errors=%d warnings=%d", len(errors), len(warnings))

        for w in warnings:
            bridge.put_event({'type': 'log', 'level': 'warning', 'message': f'⚠️  {w}'})

        if fix_round >= MAX_FIX_ROUNDS:
            for e in errors:
                bridge.put_event({'type': 'log', 'level': 'warning', 'message': f'⚠️  验证错误（已达修复上限，强制输出）: {e}'})
            break

        # 让 DirectorAgent 修复技术错误
        bridge.put_event({
            'type': 'log', 'level': 'info',
            'message': f'🔄 [DirectorAgent] 正在修复技术约束错误（第{fix_round + 1}次）...'
        })

        errors_str = "\n".join(f"- {e}" for e in errors)
        fix_prompt = (
            f"以下剧本存在技术约束错误，请修复后输出完整 JSON，不要有其他说明文字：\n\n"
            f"**错误列表：**\n{errors_str}\n\n"
            f"**注意**：只修复上述错误，不要改动其他内容。\n\n"
            f"当前剧本：\n```json\n{draft_json_str}\n```"
        )

        fixed_script = None
        async for event in director.on_messages_stream(
            [TextMessage(content=fix_prompt, source="user")],
            cancellation_token=CancellationToken()
        ):
            if hasattr(event, 'chat_message') and event.chat_message:
                fixed_script = _extract_json_from_text(event.chat_message.content)

        if fixed_script:
            draft_script = fixed_script
            draft_json_str = json.dumps(draft_script, ensure_ascii=False)
        else:
            bridge.put_event({'type': 'log', 'level': 'warning', 'message': '⚠️  修复结果解析失败，保留上一版本'})
            break

    # ════════════════════════════════════════════════
    # 阶段④：PositionAgent 位置映射（抽象 Position N → 真实点位 ID）
    # ════════════════════════════════════════════════
    bridge.put_event({'type': 'log', 'level': 'info', 'message': '📍 [PositionAgent] 开始位置映射...'})

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
            bridge.put_event({'type': 'log', 'level': 'success', 'message': '✅ [PositionAgent] 位置映射完成'})
            _emit_output(bridge, 'PositionAgent', mapped_script)
            break

        if unresolved:
            logger.warning("[PositionAgent] 无法解析的位置（轮次%d）: %s", pos_round + 1, unresolved)
            for u in unresolved:
                bridge.put_event({'type': 'log', 'level': 'warning', 'message': f'⚠️  [PositionAgent] 无法映射: {u}'})

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
                bridge.put_event({
                    'type': 'log', 'level': 'info',
                    'message': f'✏️  [DirectorAgent] 根据位置反馈修改剧本（轮次{pos_round + 1}）...'
                })
                async for event in director.on_messages_stream(
                    [TextMessage(content=fix_prompt, source="user")],
                    cancellation_token=CancellationToken()
                ):
                    if hasattr(event, 'chat_message') and event.chat_message:
                        revised = _extract_json_from_text(event.chat_message.content)
                        if revised:
                            draft_script = revised
            else:
                bridge.put_event({
                    'type': 'log', 'level': 'warning',
                    'message': '⚠️  [PositionAgent] 已达映射上限，使用当前最优结果继续'
                })
                break
        else:
            # mapped_script 为 None，解析失败
            logger.warning("[PositionAgent] JSON 解析失败（轮次%d），原始输出: %s",
                           pos_round + 1, pos_raw_content[:300] if pos_raw_content else "None")
            if pos_round >= MAX_POSITION_FIX_ROUNDS - 1:
                bridge.put_event({
                    'type': 'log', 'level': 'warning',
                    'message': '⚠️  [PositionAgent] 解析失败，跳过位置映射，使用抽象位置继续'
                })
            # 不更新 draft_script，继续重试

    # ════════════════════════════════════════════════
    # 阶段④b：position_agent_standalone 坐标生成（可选，需 scene_export + template 文件）
    # ════════════════════════════════════════════════
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
            bridge.put_event({
                'type': 'log', 'level': 'success',
                'message': f'✅ [PositionAgent] 坐标文件生成完成：{position_output_filename}'
            })
        elif pos_result.get("skip"):
            bridge.put_event({
                'type': 'log', 'level': 'info',
                'message': f'⏭️  [PositionAgent] 跳过坐标生成（{pos_result.get("error", "缺少资源文件")}）'
            })
        else:
            bridge.put_event({
                'type': 'log', 'level': 'warning',
                'message': f'⚠️  [PositionAgent] 坐标生成失败：{pos_result.get("error", "未知错误")}'
            })
    except Exception as _e:
        logger.exception("[PositionAgent] 坐标生成异常")
        bridge.put_event({'type': 'log', 'level': 'warning', 'message': f'⚠️  [PositionAgent] 坐标生成异常：{_e}'})
    finally:
        if temp_script_path and temp_script_path.exists():
            temp_script_path.unlink(missing_ok=True)

    # ════════════════════════════════════════════════
    # 阶段⑤：OutputAgent（纯 Python，生成最终 JSON 文件）
    # ════════════════════════════════════════════════
    bridge.put_event({'type': 'log', 'level': 'info', 'message': '💾 正在生成最终 JSON 并保存文件...'})

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

    actors_profile = []
    for name in actor_names:
        if name in char_map:
            actors_profile.append(char_map[name])
        elif name in custom_char_map:
            item = custom_char_map[name]
            actors_profile.append({
                "name": name,
                "gender": item.get('gender') or '未知',
                "ip": item.get('ip') or '自定义',
                "manufacturer": "用户创建",
                "background": item.get('background') or item.get('description') or f"用户自定义角色：{name}",
                "Faction": item.get('Faction') or '未知',
                "personality_traits": item.get('personality_traits') or item.get('description') or '性格由AI自由发挥',
                "role_position": item.get('role_position') or '未知',
                "important_relationships": item.get('important_relationships') or []
            })
        else:
            actors_profile.append({
                "name": name, "gender": "未知", "ip": "AI创作",
                "manufacturer": "AI生成", "background": f"AI自由创作角色：{name}",
                "Faction": "未知", "personality_traits": "由AI自由发挥",
                "role_position": "未知", "important_relationships": []
            })

    actors_profile_filename = f"actors_profile_{timestamp}.json"
    actors_filepath = output_dir / actors_profile_filename
    with open(actors_filepath, 'w', encoding='utf-8') as f:
        _json.dump(actors_profile, f, ensure_ascii=False, indent=2)

    bridge.put_event({
        'type': 'log', 'level': 'success',
        'message': f'✅ 已生成角色档案：{len(actors_profile)} 位演员'
    })

    logger.info("Pipeline 完成 | 剧本=%s 角色档案=%s 坐标=%s",
                filename, actors_profile_filename, position_filename or "（未生成）")
    bridge.put_event({
        'type': 'success',
        'filename': filename,
        'actors_profile_filename': actors_profile_filename,
        'position_filename': position_filename,
        'warnings': validation_result.get('warnings', []) if validation_result else []
    })
