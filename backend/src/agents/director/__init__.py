"""DirectorAgent public API."""

from autogen_agentchat.agents import AssistantAgent

from .prompts import DIRECT_SYSTEM_PROMPT, SYSTEM_PROMPT


def build_system_message(*, direct_mode: bool = False, **values) -> str:
    rendered = DIRECT_SYSTEM_PROMPT if direct_mode else SYSTEM_PROMPT
    for key, value in values.items():
        rendered = rendered.replace("{" + key + "}", str(value))
    return rendered


def create_agent(model_client, system_message: str, *, direct_mode=False, assistant_agent_cls=AssistantAgent):
    return assistant_agent_cls(
        name="DirectorAgent_Direct" if direct_mode else "DirectorAgent",
        model_client=model_client,
        system_message=system_message,
    )


__all__ = ["build_system_message", "create_agent"]
