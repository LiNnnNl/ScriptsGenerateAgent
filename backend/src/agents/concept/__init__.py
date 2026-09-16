"""ConceptAgent public API."""
from autogen_agentchat.agents import AssistantAgent
from .prompts import SYSTEM_PROMPT

def build_system_message(common_context: str) -> str:
    return SYSTEM_PROMPT.replace("{common_context}", common_context)

def create_agent(model_client, system_message: str, assistant_agent_cls=AssistantAgent):
    return assistant_agent_cls(name="ConceptAgent", model_client=model_client, system_message=system_message)

__all__ = ["build_system_message", "create_agent"]
