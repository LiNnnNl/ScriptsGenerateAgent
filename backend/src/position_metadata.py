"""Canonical position metadata shared by script and cinematography outputs."""

from __future__ import annotations

import re
from typing import Any, Dict, Mapping, Optional


_POSITION_NUMBER_RE = re.compile(r"(\d+)")
_NAME_SPLIT_RE = re.compile(r"\s*(?:\s[-—]\s|[，,。；;：:\n])\s*")


def parse_position_number(position_id: Any, fallback: int = 0) -> int:
    """Return the legacy Position N number without changing the stable ID."""
    match = _POSITION_NUMBER_RE.search(str(position_id or ""))
    if not match:
        return fallback
    try:
        value = int(match.group(1))
    except (TypeError, ValueError):
        return fallback
    return value if value >= 0 else fallback


def derive_position_name(description: Any, number: int = 0) -> str:
    """Derive a concise legacy fallback; new generations should provide a name."""
    text = " ".join(str(description or "").split()).strip()
    if text:
        parts = [part.strip(" -—") for part in _NAME_SPLIT_RE.split(text) if part.strip(" -—")]
        if parts:
            candidate = parts[1] if len(parts) > 1 and ("区域" in parts[0] or "场景" in parts[0]) else parts[0]
            candidate = re.sub(r"^(?:适合|用于)", "", candidate).strip()
            candidate = re.sub(r"的(?:独立)?初始站位$", "初始位", candidate)
            if candidate:
                return candidate[:18]
    return f"点位{number}" if number > 0 else "未命名点位"


def collect_position_ids(scene_obj: Mapping[str, Any]) -> list[str]:
    """Collect every referenced position ID in deterministic first-seen order."""
    result: list[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        position_id = str(value or "").strip()
        if position_id and position_id not in seen:
            seen.add(position_id)
            result.append(position_id)

    raw_metadata = scene_obj.get("position_metadata")
    if isinstance(raw_metadata, Mapping):
        for position_id in raw_metadata:
            add(position_id)
    legacy = scene_obj.get("position_descriptions")
    if isinstance(legacy, Mapping):
        for position_id in legacy:
            add(position_id)
    for entry in scene_obj.get("initial position", []) or []:
        if isinstance(entry, Mapping):
            add(entry.get("position"))
    for beat in scene_obj.get("scene", []) or []:
        if not isinstance(beat, Mapping):
            continue
        for entry in beat.get("current position", []) or []:
            if isinstance(entry, Mapping):
                add(entry.get("position"))
        moves = beat.get("move", []) or []
        if isinstance(moves, Mapping):
            moves = [moves]
        for entry in moves:
            if isinstance(entry, Mapping):
                add(entry.get("destination"))
    return result


def normalize_position_metadata(
    scene_obj: Mapping[str, Any],
    scene: Optional[Any] = None,
) -> Dict[str, Dict[str, Any]]:
    """Return the canonical number/name/description map, accepting legacy input."""
    raw_metadata = scene_obj.get("position_metadata")
    raw_metadata = raw_metadata if isinstance(raw_metadata, Mapping) else {}
    legacy = scene_obj.get("position_descriptions")
    legacy = legacy if isinstance(legacy, Mapping) else {}

    result: Dict[str, Dict[str, Any]] = {}
    for ordinal, position_id in enumerate(collect_position_ids(scene_obj), start=1):
        raw = raw_metadata.get(position_id)
        raw = raw if isinstance(raw, Mapping) else {}
        scene_position = None
        if scene is not None and hasattr(scene, "get_position"):
            scene_position = scene.get_position(position_id)
        scene_position = scene_position if isinstance(scene_position, Mapping) else {}

        description = str(
            raw.get("description")
            or legacy.get(position_id)
            or scene_position.get("description")
            or f"{position_id} 的场景站位设置与演出需求"
        ).strip()
        parsed_number = parse_position_number(position_id, ordinal)
        try:
            number = int(raw.get("number", scene_position.get("number", parsed_number)))
        except (TypeError, ValueError):
            number = parsed_number
        if number < 0:
            number = parsed_number
        name = str(raw.get("name") or scene_position.get("name") or "").strip()
        if not name:
            name = derive_position_name(description, number)

        result[position_id] = {
            "number": number,
            "name": name,
            "description": description,
        }
    return result


def attach_position_metadata(
    position_document: Optional[Dict[str, Any]],
    metadata: Mapping[str, Mapping[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Copy canonical metadata onto every position_plan/detail entry."""
    if not isinstance(position_document, dict):
        return position_document

    def enrich(entry: Any) -> None:
        if not isinstance(entry, dict):
            return
        position_id = str(entry.get("position_id") or "").strip()
        value = metadata.get(position_id)
        if not isinstance(value, Mapping):
            return
        entry["number"] = value.get("number", parse_position_number(position_id))
        entry["name"] = str(value.get("name") or "")
        entry["description"] = str(value.get("description") or "")

    for group in position_document.get("groups", []) or []:
        if not isinstance(group, dict):
            continue
        if isinstance(group.get("positions"), list):
            for entry in group["positions"]:
                enrich(entry)
        else:
            enrich(group)
    for entry in position_document.get("singles", []) or []:
        enrich(entry)
    return position_document


def validate_position_metadata(
    scene_obj: Mapping[str, Any],
) -> list[str]:
    """Validate canonical fields and full coverage of script references."""
    errors: list[str] = []
    raw = scene_obj.get("position_metadata")
    if not isinstance(raw, Mapping):
        return ["缺少 position_metadata 对象"]
    number_owners: Dict[int, str] = {}
    for position_id in collect_position_ids(scene_obj):
        item = raw.get(position_id)
        if not isinstance(item, Mapping):
            errors.append(f"position_metadata 缺少 {position_id}")
            continue
        number = item.get("number")
        if isinstance(number, bool) or not isinstance(number, int) or number < 0:
            errors.append(f"{position_id}.number 必须是非负整数")
        elif number in number_owners:
            errors.append(
                f"{position_id}.number 与 {number_owners[number]} 重复（{number}）"
            )
        else:
            number_owners[number] = position_id
        if not str(item.get("name") or "").strip():
            errors.append(f"{position_id}.name 不得为空")
        if not str(item.get("description") or "").strip():
            errors.append(f"{position_id}.description 不得为空")
    return errors
