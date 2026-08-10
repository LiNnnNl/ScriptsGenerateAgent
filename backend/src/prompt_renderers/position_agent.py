"""Render legacy PositionAgent prompts from pure prompt files."""

from ..prompt_files.position_agent_fallback_system import position_agent_fallback_system_prompt
from ..prompt_files.position_agent_stage1 import position_agent_stage1_prompt
from ..prompt_files.position_agent_stage2 import position_agent_stage2_prompt
from ..prompt_files.position_agent_stage2_repair import position_agent_stage2_repair_prompt
from ..prompt_files.position_agent_stage3 import position_agent_stage3_prompt
from ..prompt_files.position_agent_system import position_agent_system_prompt
from ..prompt_utils import prompt_lines, render_prompt


def build_position_agent_stage1_prompt_text(max_layout_people: int) -> str:
    return render_prompt(position_agent_stage1_prompt, max_layout_people=max_layout_people)


def build_position_agent_stage2_prompt_text() -> str:
    return position_agent_stage2_prompt


def build_position_agent_stage3_prompt_text() -> str:
    return position_agent_stage3_prompt


position_agent_stage2_repair_instructions = prompt_lines(position_agent_stage2_repair_prompt)
