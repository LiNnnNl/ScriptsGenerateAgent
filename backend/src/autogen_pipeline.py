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
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 网络错误关键词（用于区分连接抖动 vs 业务错误）
_NETWORK_ERR_KEYWORDS = ("connection", "timeout", "network", "remotedisconnected", "connect error")
_STAGE_MAX_RETRIES = 2       # 前置阶段 Agent 最大重试次数
_STAGE_RETRY_BASE_DELAY = 3  # 秒，每次翻倍

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
    create_treatment_agent,
    create_title_agent,
    create_director_agent,
    create_critic_agent,
    create_dialogue_agent,
    create_validation_agent,
    create_concept_pitch_agent,
    create_character_voice_agent,
    create_narrative_arch_agent,
    is_quota_error,
    make_fallback_model_client,
)
from .prompt_renderers.title_generation import build_title_generation_user_prompt
from .autogen_tools import validate_script_constraints, validate_json_spec, auto_fix_script
from .resource_loader import ResourceLoader, Character, Scene
from .json_generator import ScriptJSONGenerator
from .cinematography import run_cinematography_pipeline
from .schema import (
    validate_script_shot_structure,
    format_shot_structure_errors,
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

    singles = [{"position_id": p, "character": c, "region": "", "lookat": ""}
               for p, c in char_pos.items() if p]
    plan = {"where": scene_id, "groups": [], "singles": singles}
    detail_signals = [{"position_id": p, "character": c, "region": "", "lookat": ""}
                      for p, c in char_pos.items() if p]
    detail = {"where": scene_id, "groups": [], "singles": detail_signals}
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


async def _build_direct_draft(creative_idea: str, characters, scene, bridge, director_agent) -> List[Dict]:
    """直接模式：把用户剧本转成 draft_script（场景数组），并补齐 scene information / initial position。

    解析优先级：规范 JSON（不调 LLM）→ DirectorAgent 结构化（含站位/Position N）→ 本地规则解析（兜底）。
    返回结构与 DirectorAgent 产出一致，可直接喂给后续的验证 / 输出 / 摄影阶段。
    """
    text = (creative_idea or "").strip()

    scenes = _try_parse_json_scenes(text)
    if scenes is not None:
        _emit_stage_log(bridge, 'info', 'direct', 'json',
                        '🧩 [直接模式] 检测到规范 JSON，直接采用，跳过导演结构化')
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
            scenes = _parse_plaintext_script(text)
            _emit_stage_log(bridge, 'warning', 'direct', 'fallback',
                            '⚠️ [直接模式] 导演结构化失败，改用本地规则解析（对白可能不如 LLM 准确）')

    if not scenes:
        _emit_stage_log(bridge, 'warning', 'direct', 'empty',
                        '⚠️ [直接模式] 未解析到有效剧本内容，将输出空场景')
        scenes = [{"scene": []}]

    default_names = [c.name for c in characters]
    what_snippet = (creative_idea[:60] + ("…" if len(creative_idea) > 60 else "")) if creative_idea else scene.name

    normalized: List[Dict] = []
    for sc in scenes:
        sc = dict(sc) if isinstance(sc, dict) else {"scene": []}
        beats = sc.get("scene") or []

        # 收集本场景出现的角色（按出场顺序去重）
        speakers: List[str] = []
        seen = set()
        for b in beats:
            sp = (b.get("speaker") or "").strip() if isinstance(b, dict) else ""
            if sp and sp not in seen:
                seen.add(sp)
                speakers.append(sp)
        who = speakers or default_names

        info = dict(sc.get("scene information") or {})
        info.setdefault("who", who)
        info.setdefault("where", scene.name)
        info.setdefault("what", what_snippet)
        sc["scene information"] = info

        if "initial position" not in sc:
            sc["initial position"] = [{"character": n, "position": ""} for n in who]
        sc["scene"] = beats
        normalized.append(sc)

    total_beats = sum(len(s.get("scene", [])) for s in normalized)
    _emit_stage_log(bridge, 'success', 'direct', 'parsed',
                    f'✅ [直接模式] 已解析用户剧本：{len(normalized)} 个场景 / {total_beats} 个片段，'
                    f'跳过头脑风暴与剧本起草')
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

    dialogue_target_hint = (
        f'目标对白行数：至少 {target_dialogue_lines} 行台词。'
        f'确保每个角色有足够的说话机会，不要让对话戛然而止。'
        if target_dialogue_lines else ''
    )

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
    model_supports_tools = os.getenv("MODEL_FUNCTION_CALLING", "false").lower() == "true"
    _emit_stage_log(bridge, 'info', 'setup', 'init', '🤖 初始化多 Agent 系统...')

    treatment = create_treatment_agent(act_count=act_count)
    director = create_director_agent(characters, scene, resource_loader, required_character_count, act_count, user_constraints=user_constraints, act_scene_map=act_scene_map if multi_scene else None)
    critic = create_critic_agent(user_constraints=user_constraints, fixed_dialogues=fixed_dialogues)
    dialogue = create_dialogue_agent(user_constraints=user_constraints, fixed_dialogues=fixed_dialogues)
    validator = create_validation_agent(resource_loader, scene) if model_supports_tools else None

    _emit_stage_log(bridge, 'success', 'setup', 'ready', '✅ Agents 初始化完成（创意会议、大纲、导演、审查、验证）')

    # 阶段化上下文（内存态，不落盘）
    stage_context: Dict[str, Any] = {
        "meeting_minutes": "",
        "treatment": {},
    }

    # ════════════════════════════════════════════════
    # 创意阶段：创意会议 → 分场规划 → 起草 → 文学审查
    # 直接模式下整体跳过，改用用户提供的剧本
    # ════════════════════════════════════════════════
    if direct_mode:
        _emit_stage_log(bridge, 'info', 'direct', 'start',
                        '⚡ [直接模式] 已开启：跳过头脑风暴/分场规划，由导演把你的剧本结构化（保留剧情台词）')
        direct_director = create_director_agent(
            characters, scene, resource_loader, required_character_count, act_count,
            user_constraints=user_constraints, direct_mode=True,
        )
        draft_script = await _build_direct_draft(creative_idea, characters, scene, bridge, direct_director)
    else:
        # ════════════════════════════════════════════════
        # 阶段一：创意会议（ConceptPitch / CharacterVoice / NarrativeArch 轮流发言）
        # ════════════════════════════════════════════════
        _emit_stage_log(bridge, 'info', 'meeting', 'start',
                        '🎭 [创意会议] 三位创作顾问开始头脑风暴（每人最多发言 2 轮）...')

        concept_pitch = create_concept_pitch_agent(characters, scene, required_character_count)
        character_voice = create_character_voice_agent()
        narrative_arch = create_narrative_arch_agent()

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

        # ════════════════════════════════════════════════
        # 阶段一后半：TreatmentAgent 将会议记录转化为分场大纲
        # ════════════════════════════════════════════════
        _emit_stage_log(bridge, 'info', 'treatment', 'start',
                        '🗂️ [分场规划期] TreatmentAgent 根据会议记录生成分场大纲...')
        treatment_prompt = (
            f"以下是创意会议的讨论记录，请据此生成分场大纲：\n\n{meeting_transcript}\n\n"
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

        treatment_summary = json.dumps(stage_context.get("treatment", {}), ensure_ascii=False, indent=2)
        fixed_dlg_section = ""
        if fixed_dialogues:
            lines = "\n".join(f"- **{d['speaker']}**：{d['content']}" for d in fixed_dialogues)
            fixed_dlg_section = (
                f"\n## 📌 用户提供的固定对白（必须原样保留，不得修改）\n{lines}\n\n"
                "以上对白必须出现在剧本中，内容一字不改。\n\n"
            )
        base_user_prompt = (
            f"创作想法：{plot_outline or '（AI 自由创作）'}{fixed_dlg_section}\n\n"
            f"## 创意会议纪要\n{stage_context.get('meeting_minutes', '（无）')}\n\n"
            f"## 分场大纲\n{treatment_summary}\n\n" \
            + (f"{duration_hint}\n" if duration_hint else "")
            + (f"{dialogue_target_hint}\n" if dialogue_target_hint else "")
            + "请根据以上创意会议纪要和分场大纲生成剧本，直接输出 JSON 格式，不要有其他说明文字。"
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
                + "\n\n重要：\n"
                "- 每个角色动作的 `motion_detail` 字段必须保留原有内容，不得将其置为空字符串。\n"
                + ("".join(f"- 用户约束：{c}（不得违背）\n" for c in user_constraints) if user_constraints else "")
                + ("".join(f"- 固定对白（不得修改）：{d['speaker']}：{d['content']}\n" for d in fixed_dialogues) if fixed_dialogues else "")
                + (f"- 目标对白行数：至少 {target_dialogue_lines} 行，当前不足请扩充。\n" if target_dialogue_lines else "")
                + "\n当前剧本：\n```json\n{json.dumps(draft_script, ensure_ascii=False, indent=2)}\n```"
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
    timestamp = time.time_ns()
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

    final_json = generator.generate_final_json(draft_script, plot_summary, act_scene_ids=act_scene_ids)

    # ── 阶段三后：对白行数校验 + 不足时触发补写 ──
    if target_dialogue_lines and not direct_mode:
        actual_lines = sum(
            1 for act in final_json
            for line in act.get('scene', [])
            if line.get('speaker') and line.get('content')
        )
        deficit = target_dialogue_lines - actual_lines
        if deficit > 0:
            _emit_stage_log(bridge, 'info', 'dialogue_fill', 'start',
                            f'📝 [对白补写] 当前 {actual_lines} 行，目标 {target_dialogue_lines} 行，'
                            f'不足 {deficit} 行，正在触发补写...')
            fill_prompt = (
                f"当前剧本对白行数不足。当前共 {actual_lines} 行，目标至少 {target_dialogue_lines} 行。\n"
                f"请在不修改已有对白的前提下，"
                f"在每个幕中**增加自然的对白**，让对话更丰富、更符合现实主义风格。\n"
                f"新增的对白必须：\n"
                f"1. 口语化、有停顿感、有角色个人特征\n"
                f"2. 推动情节或揭示人物关系\n"
                f"3. 不添加任何禁止AI腔红线中的词汇\n\n"
                f"当前剧本：\n```json\n{json.dumps(final_json, ensure_ascii=False, indent=2)}\n```\n\n"
                f"输出完整修改后的 JSON，不要有其他说明文字。"
            )
            filled_script = None
            try:
                async for event in director.on_messages_stream(
                    [TextMessage(content=fill_prompt, source="user")],
                    cancellation_token=CancellationToken()
                ):
                    if hasattr(event, 'chat_message') and event.chat_message:
                        filled_script = _extract_json_from_text(event.chat_message.content)
            except Exception as _e:
                logger.warning("[对白补写] 请求失败: %s", _e)
                _emit_stage_log(bridge, 'warning', 'dialogue_fill', 'error',
                                f'⚠️ [对白补写] 补写请求失败，保留原始剧本: {_e}')
            if filled_script:
                # 补写结果仍需经过 generator 规范化（补充 emotion 字段等）
                final_json = generator.generate_final_json(filled_script, plot_summary, act_scene_ids=act_scene_ids)
                new_lines = sum(
                    1 for act in final_json
                    for line in act.get('scene', [])
                    if line.get('speaker') and line.get('content')
                )
                _emit_stage_log(bridge, 'success', 'dialogue_fill', 'done',
                                f'✅ [对白补写] 补写完成：{actual_lines} → {new_lines} 行')
                _emit_output(bridge, 'DirectorAgent（对白补写）', final_json)

    filename = f"script_{timestamp}.json"
    filepath = output_dir / filename
    generator.export_to_file(final_json, str(filepath))

    script_title = await _generate_script_title(final_json, bridge)

    # 计算剧本估算时长（基于对白长度）
    def _calc_duration(script_json: list) -> dict:
        total_chars = 0
        total_lines = 0
        for scene in script_json:
            for line in scene.get('scene', []):
                speaker = line.get('speaker', '') or ''
                content = line.get('content', '') or ''
                if speaker and content:
                    total_lines += 1
                    total_chars += len(content)
        # 按每字5字符/秒估算，附加场景/动作描述用时
        dialogue_secs = total_chars / 5
        # 场景切换、动作描述额外时间（按行数*3秒估算）
        scene_secs = total_lines * 3
        estimated_secs = dialogue_secs + scene_secs
        return {
            'estimated_duration_seconds': round(estimated_secs),
            'dialogue_lines': total_lines,
            'dialogue_chars': total_chars,
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
    if True:
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
                camera_script_filename = cine_result.get("camera_script_filename")
                position_plan_filename = cine_result.get("position_plan_filename")
                position_detail_filename = cine_result.get("position_detail_filename")
                # 摄影管线返回的 position_plan/position_detail 可能已被 Stage3 部分覆写；
                # 重新从 Stage2 的原始产出读取（未被 Stage3 破坏的版本）以确保 region/lookat 正确
                _stage2_plan_path = output_dir / "CinematographyStages" / "position_stage2_planning.json"
                _stage2_detail_path = output_dir / "CinematographyStages" / "director_stage2_position_planning.json"
                if _stage2_plan_path.exists():
                    with open(_stage2_plan_path, "r", encoding="utf-8") as _f:
                        _stage2_plan = json.load(_f)
                    with open(output_dir / position_plan_filename, "w", encoding="utf-8") as _f:
                        json.dump(_stage2_plan, _f, ensure_ascii=False, indent=2)
                if _stage2_detail_path.exists():
                    with open(_stage2_detail_path, "r", encoding="utf-8") as _f:
                        _raw = json.load(_f)
                        _stage2_detail = _raw.get("position_detail") or _raw
                    with open(output_dir / position_detail_filename, "w", encoding="utf-8") as _f:
                        json.dump(_stage2_detail, _f, ensure_ascii=False, indent=2)
                # 用摄影指导结果重新生成并覆写 script_*.json（保留已填写的摄影字段）
                final_json = generator.generate_final_json(draft_script, plot_summary, preserve_shot_fields=True, act_scene_ids=act_scene_ids)
                generator.export_to_file(final_json, str(filepath))
                _emit_stage_log(bridge, 'success', 'cinematography', 'result',
                                f'✅ [摄影指导期] 摄影规划完成，镜头参数已写入 camera_script')
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

    _emit_stage_log(bridge, 'success', 'output', 'actors_profile', f'✅ 已生成角色档案：{len(actors_profile)} 位演员')

    session_id = str(timestamp)
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
        },
        scene_id=session_scene_id,
        act_count=act_count,
        label=script_title,
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
        'title': script_title,
        'estimated_duration': duration_info,
        'warnings': validation_result.get('warnings', []) if validation_result else []
    })
