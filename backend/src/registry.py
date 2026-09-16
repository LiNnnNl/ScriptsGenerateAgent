"""
会话注册表模块

管理 outputs/registry.json，记录每次生成会话的文件、标签等信息。
"""

import json
import os
import threading
from datetime import datetime
from pathlib import Path

REGISTRY_PATH = Path("outputs/registry.json")
_LOCK = threading.Lock()
FORM_DATA_KEYS = (
    "creative_idea", "scene_id", "scene_pool", "act_scenes",
    "custom_characters", "required_character_count", "act_count",
    "script_style_id", "script_tone_id", "dialogue_language",
    "shot_style_reference", "direct_mode",
)


def snapshot_form_data(params: dict) -> dict:
    """Keep only fields needed to refill the generation form."""
    return {key: params.get(key) for key in FORM_DATA_KEYS}


def load_registry() -> dict:
    if REGISTRY_PATH.exists():
        try:
            with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"sessions": {}}


def save_registry(data: dict) -> None:
    REGISTRY_PATH.parent.mkdir(exist_ok=True)
    tmp = REGISTRY_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, REGISTRY_PATH)


def register_session(
    ts: str,
    files: dict,
    scene_id: str = "",
    act_count: int = 3,
    label: str = "",
    form_data: dict | None = None,
    status: str = "success",
    error: str = "",
) -> None:
    with _LOCK:
        data = load_registry()
        previous = data["sessions"].get(ts, {})
        data["sessions"][ts] = {
            "label": label,
            "created_at": previous.get("created_at") or datetime.now().isoformat(timespec="seconds"),
            "scene_id": scene_id,
            "act_count": act_count,
            "files": files,
            "word_export": previous.get("word_export"),
            "form_data": form_data if form_data is not None else previous.get("form_data"),
            "status": status,
            "error": error,
        }
        save_registry(data)


def update_session_status(session_id: str, status: str, error: str = "") -> bool:
    with _LOCK:
        data = load_registry()
        session = data["sessions"].get(session_id)
        if not session:
            return False
        if session.get("status") == "success":
            return True
        session["status"] = status
        session["error"] = str(error)[:2000]
        save_registry(data)
        return True


def update_label(session_id: str, label: str) -> bool:
    with _LOCK:
        data = load_registry()
        if session_id not in data["sessions"]:
            return False
        data["sessions"][session_id]["label"] = label
        save_registry(data)
        return True


def update_word_export(session_id: str, docx_filename: str) -> None:
    with _LOCK:
        data = load_registry()
        if session_id in data["sessions"]:
            data["sessions"][session_id]["word_export"] = docx_filename
            save_registry(data)


def list_sessions_desc() -> list:
    data = load_registry()
    sessions = []
    for sid, info in data["sessions"].items():
        sessions.append({"session_id": sid, **info})
    sessions.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return sessions
