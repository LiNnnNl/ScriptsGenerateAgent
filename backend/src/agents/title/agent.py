from autogen_agentchat.agents import AssistantAgent

from .prompts import SYSTEM_PROMPT, USER_PROMPT


def create_agent(model_client, assistant_agent_cls=AssistantAgent):
    return assistant_agent_cls(
        name="TitleAgent",
        model_client=model_client,
        system_message=SYSTEM_PROMPT,
    )


def build_system_message() -> str:
    return SYSTEM_PROMPT


def build_user_prompt(title_input: str) -> str:
    return USER_PROMPT.replace("{title_input}", title_input)
