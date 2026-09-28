"""Compatibility exports; live camera rules are package-owned."""

from ..cinematography.camera.rules import (
    build_combined_batch_system_prompt,
    build_combined_system_prompt,
    build_description_system_prompt,
    build_shot_analysis_system_prompt as build_analysis_system_prompt,
    shot_analysis_user_instructions,
    shot_combined_batch_user_instructions,
    shot_combined_user_instructions,
    shot_description_user_instructions,
)

__all__ = [
    "build_analysis_system_prompt",
    "build_combined_batch_system_prompt",
    "build_combined_system_prompt",
    "build_description_system_prompt",
    "shot_analysis_user_instructions",
    "shot_combined_batch_user_instructions",
    "shot_combined_user_instructions",
    "shot_description_user_instructions",
]
