"""StoryIRAgent public API."""

from autogen_agentchat.agents import AssistantAgent

from .prompts import SYSTEM_PROMPT


def build_system_message() -> str:
    return SYSTEM_PROMPT


def create_agent(model_client, assistant_agent_cls=AssistantAgent):
    return assistant_agent_cls(
        name="StoryIRAgent",
        model_client=model_client,
        system_message=SYSTEM_PROMPT,
    )


__all__ = ["build_system_message", "create_agent"]
