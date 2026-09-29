"""Authoritative prompts for live point generation."""

from .grouping import cinematography_position_grouping_prompt
from .planning import cinematography_position_planning_prompt

__all__ = [
    "cinematography_position_grouping_prompt",
    "cinematography_position_planning_prompt",
]
