"""Shared scene-segment semantics used across the generation pipeline."""

from __future__ import annotations

from typing import Any


DEFAULT_EMPTY_SHOT_DURATION = "5s"


def is_empty_shot(segment: Any) -> bool:
    """Return whether *segment* is an environment-only empty shot."""
    if not isinstance(segment, dict) or "move" in segment:
        return False
    return (
        "speaker" in segment
        and "content" in segment
        and not str(segment.get("speaker") or "").strip()
        and not str(segment.get("content") or "").strip()
    )


def protect_empty_shot(segment: dict, *, ensure_camera: bool = False) -> bool:
    """Normalize the invariant fields of an empty shot in place."""
    if not is_empty_shot(segment):
        return False

    segment["speaker"] = ""
    segment["content"] = ""
    segment["duration"] = segment.get("duration") or DEFAULT_EMPTY_SHOT_DURATION
    segment["shot"] = "scene"
    segment["actions"] = []
    # These fields belong only to character shots.
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
