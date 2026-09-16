"""DialogueAgent public API."""

from autogen_agentchat.agents import AssistantAgent

from .prompts import SYSTEM_PROMPT


def build_system_message(constraints: str, script_style_guide: str) -> str:
    return SYSTEM_PROMPT.replace("{constraints}", constraints).replace(
        "{video_style_guide}", script_style_guide
    )


def create_agent(model_client, system_message: str, assistant_agent_cls=AssistantAgent):
    return assistant_agent_cls(
        name="DialogueAgent",
        model_client=model_client,
        system_message=system_message,
    )


__all__ = ["build_system_message", "create_agent"]
