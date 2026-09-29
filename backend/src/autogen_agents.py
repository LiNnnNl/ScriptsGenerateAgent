"""
AutoGen Agent 定义模块

定义各个专业化 Agent 及其 system_message 构建函数。
DirectorAgent 的提示词逻辑从 director_ai.py 的 _build_context_prompt 迁移而来。
"""

import os
import re
from typing import Dict, List, Optional
import httpx
from autogen_agentchat.agents import AssistantAgent
from autogen_core.models import CreateResult
from autogen_ext.models.openai import OpenAIChatCompletionClient
from .resource_loader import ResourceLoader, Character, Scene
from .agents.character_voice import create_agent as create_character_voice_package_agent
from .agents.concept_pitch import create_agent as create_concept_pitch_package_agent
from .agents.meeting_summary import create_agent as create_meeting_summary_package_agent
from .agents.narrative_arch import create_agent as create_narrative_arch_package_agent
from .agents.position import create_agent as create_position_package_agent
from .agents.contract_repair import create_agent as create_contract_repair_package_agent
from .agents.critic import create_agent as create_critic_package_agent
from .agents.dialogue import create_agent as create_dialogue_package_agent
from .agents.director import create_agent as create_director_package_agent
from .agents.director_word import create_agent as create_director_word_package_agent
from .agents.revision import create_agent as create_revision_package_agent
from .agents.story_ir import create_agent as create_story_ir_package_agent
from .agents.shot_plan import create_agent as create_shot_plan_package_agent
from .agents.title import create_agent as create_title_package_agent
from .agents.treatment import create_agent as create_treatment_package_agent


# 额度耗尽时的备用模型（同 API Key，同 BASE_URL）
_FALLBACK_MODEL = os.getenv("FALLBACK_MODEL", "doubao-seed-2-0-mini-260215")
_SIMPLE_MODEL = os.getenv("SIMPLE_MODEL", "doubao-seed-2-0-lite-260428")

# 触发切换的错误关键词（ARK / OpenAI 额度相关）
_QUOTA_ERR_KEYWORDS = (
    "rate_limit", "ratelimit", "429", "quota", "insufficient",
    "arrearage", "exceeded", "billing", "account_quota",
    "setlimitexceeded", "set inference limit", "safe experience mode",
    "too many requests",
)


class DiagnosticOpenAIChatCompletionClient(OpenAIChatCompletionClient):
    """保留最近一次底层 CreateResult，供流水线记录 finish_reason 和 token 用量。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.last_create_result: Optional[CreateResult] = None

    async def create(self, *args, **kwargs):
        self.last_create_result = None
        result = await super().create(*args, **kwargs)
        self.last_create_result = result
        return result

    async def create_stream(self, *args, **kwargs):
        self.last_create_result = None
        async for chunk in super().create_stream(*args, **kwargs):
            if isinstance(chunk, CreateResult):
                self.last_create_result = chunk
            yield chunk


def is_quota_error(exc: BaseException) -> bool:
    """判断异常是否为额度耗尽 / 限流错误。"""
    msg = str(exc).lower()
    # openai Python SDK 专有异常类
    try:
        import openai
        if isinstance(exc, openai.RateLimitError):
            return True
    except ImportError:
        pass
    return any(k in msg for k in _QUOTA_ERR_KEYWORDS)


def make_model_client(
    model: Optional[str] = None,
    *,
    structured_json: bool = False,
) -> OpenAIChatCompletionClient:
    """创建 OpenAI 兼容的模型客户端（支持 DeepSeek / 火山引擎 ARK）"""
    api_key = os.getenv("API_KEY")
    base_url = os.getenv("BASE_URL", "https://api.deepseek.com")
    model_name = model or os.getenv("MODEL", "doubao-seed-2-0-lite-260215")
    max_tokens = max(1, int(os.getenv("MODEL_MAX_TOKENS", "8000")))

    if not api_key:
        raise ValueError("需要提供 API_KEY，请在 .env 文件中设置")

    # 是否支持 function calling，默认 False（火山引擎 code plan 不支持）
    # 如需开启，在 .env 中设置 MODEL_FUNCTION_CALLING=true
    function_calling = os.getenv("MODEL_FUNCTION_CALLING", "false").lower() == "true"

    create_args = {}
    if structured_json and "volces.com" in base_url.lower():
        thinking_type = os.getenv("STRUCTURED_MODEL_THINKING", "disabled").strip().lower()
        if thinking_type in {"enabled", "disabled", "auto"}:
            create_args["extra_body"] = {"thinking": {"type": thinking_type}}

    return DiagnosticOpenAIChatCompletionClient(
        model=model_name,
        api_key=api_key,
        base_url=base_url,
        max_tokens=max_tokens,
        temperature=0.7,
        timeout=300,
        # Avoid proxy first-byte timeouts for long-running model responses.
        http_client=httpx.AsyncClient(trust_env=False),
        # Pipeline-level retries can report progress to the browser and recreate
        # a failed connection. Keep the SDK from retrying invisibly in place.
        max_retries=0,
        model_info={
            "vision": False,
            "function_calling": function_calling,
            "json_output": True,
            "family": "unknown",
            "structured_output": False,
        },
        **create_args,
    )


def make_fallback_model_client(*, structured_json: bool = False) -> OpenAIChatCompletionClient:
    """额度耗尽后使用的备用模型客户端（同 API Key，同 BASE_URL）。"""
    return make_model_client(model=_FALLBACK_MODEL, structured_json=structured_json)


from .prompt_renderers.autogen_agent_prompts import (
    build_character_voice_system_message,
    build_meeting_summary_system_message,
    build_concept_pitch_system_message,
    build_critic_system_message,
    build_dialogue_system_message,
    build_director_system_message,
    build_director_word_system_message,
    build_narrative_arch_system_message,
    build_position_agent_system_message,
    build_shot_plan_system_message,
    build_treatment_system_message,
)


# ────────────────────────────────────────────────────────────────────────────
# Agent 工厂函数
# ────────────────────────────────────────────────────────────────────────────

def create_director_agent(
    characters: List[Character],
    scene: Scene,
    resource_loader: ResourceLoader,
    required_character_count: int = 0,
    act_count: int = 3,
    model: Optional[str] = None,
    user_constraints: Optional[List[str]] = None,
    direct_mode: bool = False,
    act_scene_map: Optional[Dict[int, Scene]] = None,
    script_style_guide: Optional[str] = None,
) -> AssistantAgent:
    system_message = build_director_system_message(
        characters, scene, resource_loader, required_character_count, act_count,
        user_constraints, direct_mode=direct_mode, act_scene_map=act_scene_map,
        script_style_guide=script_style_guide,
    )
    return create_director_package_agent(
        make_model_client(model, structured_json=True),
        system_message,
        direct_mode=direct_mode,
        assistant_agent_cls=AssistantAgent,
    )


def create_director_word_agent(
    characters: List[Character],
    scene: Scene,
    resource_loader: ResourceLoader,
    required_character_count: int = 0,
    act_count: int = 3,
    model: Optional[str] = None,
    user_constraints: Optional[List[str]] = None,
    act_scene_map: Optional[Dict[int, Scene]] = None,
    script_style_guide: Optional[str] = None,
) -> AssistantAgent:
    system_message = build_director_word_system_message(
        characters, scene, resource_loader, required_character_count, act_count,
        user_constraints=user_constraints, act_scene_map=act_scene_map,
        script_style_guide=script_style_guide,
    )
    return create_director_word_package_agent(
        make_model_client(model),
        system_message,
        assistant_agent_cls=AssistantAgent,
    )




def create_critic_agent(
    model: Optional[str] = None,
    user_constraints: Optional[List[str]] = None,
    fixed_dialogues: Optional[List[dict]] = None,
    script_style_guide: Optional[str] = None,
) -> AssistantAgent:
    return create_critic_package_agent(
        make_model_client(model),
        build_critic_system_message(
            user_constraints=user_constraints,
            fixed_dialogues=fixed_dialogues,
            script_style_guide=script_style_guide,
        ),
        assistant_agent_cls=AssistantAgent,
    )


def create_treatment_agent(
    act_count: int = 3,
    model: Optional[str] = None,
    script_style_guide: Optional[str] = None,
) -> AssistantAgent:
    return create_treatment_package_agent(
        make_model_client(model),
        build_treatment_system_message(act_count, script_style_guide=script_style_guide),
        assistant_agent_cls=AssistantAgent,
    )


def create_meeting_summary_agent(
    model: Optional[str] = None,
    script_style_guide: Optional[str] = None,
) -> AssistantAgent:
    return create_meeting_summary_package_agent(
        make_model_client(model or _SIMPLE_MODEL, structured_json=True),
        build_meeting_summary_system_message(script_style_guide=script_style_guide),
        assistant_agent_cls=AssistantAgent,
    )


def create_shot_plan_agent(model: Optional[str] = None) -> AssistantAgent:
    return create_shot_plan_package_agent(
        make_model_client(model or _SIMPLE_MODEL, structured_json=True),
        assistant_agent_cls=AssistantAgent,
    )


def create_story_ir_agent(model: Optional[str] = None) -> AssistantAgent:
    return create_story_ir_package_agent(
        make_model_client(model or _SIMPLE_MODEL, structured_json=True),
        assistant_agent_cls=AssistantAgent,
    )


def create_title_agent(model: Optional[str] = None) -> AssistantAgent:
    return create_title_package_agent(
        make_model_client(model or _SIMPLE_MODEL, structured_json=True),
        assistant_agent_cls=AssistantAgent,
    )


def create_dialogue_agent(
    model: Optional[str] = None,
    user_constraints: Optional[List[str]] = None,
    fixed_dialogues: Optional[List[dict]] = None,
    script_style_guide: Optional[str] = None,
) -> AssistantAgent:
    return create_dialogue_package_agent(
        make_model_client(model),
        build_dialogue_system_message(
            user_constraints=user_constraints,
            fixed_dialogues=fixed_dialogues,
            script_style_guide=script_style_guide,
        ),
        assistant_agent_cls=AssistantAgent,
    )


def create_revision_agent(model: Optional[str] = None) -> AssistantAgent:
    return create_revision_package_agent(
        make_model_client(model, structured_json=True),
        assistant_agent_cls=AssistantAgent,
    )


def create_contract_repair_agent(model: Optional[str] = None) -> AssistantAgent:
    return create_contract_repair_package_agent(
        make_model_client(model, structured_json=True),
        assistant_agent_cls=AssistantAgent,
    )


def create_concept_pitch_agent(
    characters: List[Character],
    scene: Scene,
    required_character_count: int = 0,
    model: Optional[str] = None,
    script_style_guide: Optional[str] = None,
) -> AssistantAgent:
    return create_concept_pitch_package_agent(
        make_model_client(model),
        build_concept_pitch_system_message(
            characters, scene, required_character_count,
            script_style_guide=script_style_guide,
        ),
        assistant_agent_cls=AssistantAgent,
    )


def create_character_voice_agent(
    model: Optional[str] = None,
    script_style_guide: Optional[str] = None,
) -> AssistantAgent:
    return create_character_voice_package_agent(
        make_model_client(model),
        build_character_voice_system_message(script_style_guide=script_style_guide),
        assistant_agent_cls=AssistantAgent,
    )


def create_narrative_arch_agent(
    model: Optional[str] = None,
    script_style_guide: Optional[str] = None,
) -> AssistantAgent:
    return create_narrative_arch_package_agent(
        make_model_client(model),
        build_narrative_arch_system_message(script_style_guide=script_style_guide),
        assistant_agent_cls=AssistantAgent,
    )


def create_position_agent(scene: Scene, model: Optional[str] = None) -> AssistantAgent:
    return create_position_package_agent(
        make_model_client(model),
        build_position_agent_system_message(scene),
        assistant_agent_cls=AssistantAgent,
    )
