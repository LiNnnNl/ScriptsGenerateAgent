"""Authoritative prompts and render helpers for live camera generation."""

from pathlib import Path

from ....prompt_utils import prompt_lines
from ....scene_segments import is_empty_shot
from .camera_analysis_batch_system import camera_planning_analysis_batch_system_prompt
from .camera_analysis_batch_user import camera_planning_analysis_batch_user_instructions_prompt
from .camera_analysis_system import camera_planning_analysis_system_prompt
from .camera_analysis_user import camera_planning_analysis_user_instructions_prompt
from .shot_analysis_system import shot_planning_analysis_system_prompt
from .shot_analysis_user import shot_planning_analysis_user_instructions_prompt
from .shot_combined_batch_system import shot_planning_combined_batch_system_prompt
from .shot_combined_batch_user import shot_planning_combined_batch_user_instructions_prompt
from .shot_combined_system import shot_planning_combined_system_prompt
from .shot_combined_user import shot_planning_combined_user_instructions_prompt
from .shot_description_system import shot_planning_description_system_prompt

SHOT_TYPE_RULE_FILES = {
    "character": "character_shot.md",
    "scene": "scene_shot.md",
    "object": "object_shot.md",
}


def build_shot_type_rules(entries) -> str:
    """Return only the shot-category rules needed by this model batch."""
    shot_types = set()
    for entry in entries:
        beat = entry.get("beat", entry) if isinstance(entry, dict) else None
        if not isinstance(beat, dict):
            continue
        if beat.get("shot") == "object":
            shot_types.add("object")
        elif is_empty_shot(beat):
            shot_types.add("scene")
        else:
            shot_types.add("character")

    return "\n\n".join(
        (Path(__file__).parent / filename).read_text(encoding="utf-8").strip()
        for shot_type, filename in SHOT_TYPE_RULE_FILES.items()
        if shot_type in shot_types
    )
from .shot_description_user import shot_planning_description_user_instructions_prompt


def build_analysis_system_prompt() -> str:
    return camera_planning_analysis_system_prompt


def build_analysis_batch_system_prompt() -> str:
    return camera_planning_analysis_batch_system_prompt


def build_shot_analysis_system_prompt() -> str:
    return shot_planning_analysis_system_prompt


def build_description_system_prompt() -> str:
    return shot_planning_description_system_prompt


def build_combined_system_prompt() -> str:
    return shot_planning_combined_system_prompt


def build_combined_batch_system_prompt() -> str:
    return shot_planning_combined_batch_system_prompt


camera_analysis_user_instructions = prompt_lines(camera_planning_analysis_user_instructions_prompt)
camera_analysis_batch_user_instructions = prompt_lines(camera_planning_analysis_batch_user_instructions_prompt)
shot_analysis_user_instructions = prompt_lines(shot_planning_analysis_user_instructions_prompt)
shot_combined_batch_user_instructions = prompt_lines(shot_planning_combined_batch_user_instructions_prompt)
shot_combined_user_instructions = prompt_lines(shot_planning_combined_user_instructions_prompt)
shot_description_user_instructions = prompt_lines(shot_planning_description_user_instructions_prompt)
