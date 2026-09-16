"""CharacterVoiceAgent public API."""

from autogen_agentchat.agents import AssistantAgent

from .prompts import SYSTEM_PROMPT


def build_system_message(script_style_guide: str) -> str:
    return SYSTEM_PROMPT.replace("{video_style_guide}", script_style_guide)


def create_agent(model_client, system_message: str, assistant_agent_cls=AssistantAgent):
    return assistant_agent_cls(
        name="CharacterVoiceAgent",
        model_client=model_client,
        system_message=system_message,
    )


__all__ = ["build_system_message", "create_agent"]
