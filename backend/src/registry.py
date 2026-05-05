"""
会话注册表模块

管理 outputs/registry.json，记录每次生成会话的文件、标签等信息。
"""

import json
import os
from datetime import datetime
from pathlib import Path

REGISTRY_PATH = Path("outputs/registry.json")


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
) -> None:
    data = load_registry()
    data["sessions"][ts] = {
        "label": label,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "scene_id": scene_id,
        "act_count": act_count,
        "files": files,
        "word_export": None,
    }
    save_registry(data)


def update_label(session_id: str, label: str) -> bool:
    data = load_registry()
    if session_id not in data["sessions"]:
        return False
    data["sessions"][session_id]["label"] = label
    save_registry(data)
    return True


def update_word_export(session_id: str, docx_filename: str) -> None:
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
