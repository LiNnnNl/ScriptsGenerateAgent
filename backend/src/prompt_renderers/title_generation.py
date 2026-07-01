"""Render title generation prompts from pure prompt files."""

from ..prompt_files.title_generation_user import title_generation_user_prompt
from ..prompt_utils import render_prompt


def build_title_generation_user_prompt(title_input: str) -> str:
    return render_prompt(title_generation_user_prompt, title_input=title_input)
