"""DirectorWordAgent public API."""

from autogen_agentchat.agents import AssistantAgent

from .prompts import SYSTEM_PROMPT, USER_PROMPT


def build_system_message(**values) -> str:
    rendered = SYSTEM_PROMPT
    for key, value in values.items():
        rendered = rendered.replace("{" + key + "}", str(value))
    return rendered


def build_user_prompt(creative_idea: str) -> str:
    return USER_PROMPT.replace("{creative_idea}", creative_idea)


def create_agent(model_client, system_message: str, assistant_agent_cls=AssistantAgent):
    return assistant_agent_cls(
        name="DirectorAgent_Word",
        model_client=model_client,
        system_message=system_message,
    )


__all__ = ["build_system_message", "build_user_prompt", "create_agent"]
