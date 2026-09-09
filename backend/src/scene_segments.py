"""Shared scene-segment semantics used across the generation pipeline."""

from __future__ import annotations

from typing import Any


DEFAULT_EMPTY_SHOT_DURATION = 5.0


def is_empty_shot(segment: Any) -> bool:
    """Return whether *segment* is an environment-only empty shot."""
    if not isinstance(segment, dict) or "move" in segment:
        return False
    return (
        "speaker" in segment
        and isinstance(segment.get("speaker"), str)
        and not segment["speaker"].strip()
    )


def protect_empty_shot(segment: dict, *, ensure_camera: bool = False) -> bool:
    """Normalize the invariant fields of an empty shot in place."""
    if not is_empty_shot(segment):
        return False

    segment["speaker"] = ""
    segment.setdefault("content", "无台词")
    segment["duration"] = segment.get("duration") or DEFAULT_EMPTY_SHOT_DURATION
    segment["shot"] = 'object' if segment.get('shot') == 'object' else 'scene'
    segment["actions"] = []
    # These fields belong only to character shots.
    if segment['shot'] != 'object':
        segment.pop("shot_type", None)
    segment.pop("Follow", None)
    if ensure_camera and segment.get("camera") is None:
        segment["camera"] = 1
    return True


def protect_empty_shots(script: Any, *, ensure_camera: bool = False) -> Any:
    """Apply the empty-shot invariant to every beat in a script payload."""
    scenes = script if isinstance(script, list) else [script]
    for scene_obj in scenes:
        if not isinstance(scene_obj, dict):
            continue
        for segment in scene_obj.get("scene", []):
            if isinstance(segment, dict):
                protect_empty_shot(segment, ensure_camera=ensure_camera)
    return script
