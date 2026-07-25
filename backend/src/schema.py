"""
Pydantic schema — 剧本站位/shot 字段校验 + position plan 字段校验 + camera_script 字段校验

剧本站位：
  validate_script_position_structure  强制 initial position.state，且同一数组内不同人物站位唯一

shot 两阶段：
  validate_script_shot_structure  初稿后调用，只查字段是否存在（不校验值）
  validate_script_shots           摄影管线完成后调用，校验字段值是否合法

position plan：
  validate_position_plan          Stage 2 规划完成后调用，校验关键字段不为空

camera_script：
  validate_camera_script          _build_camera_script 后调用，校验每个 event 字段合法性
"""

from __future__ import annotations

from typing import Any, List, Literal, Optional, Union
from pydantic import BaseModel, field_validator, model_validator
from .scene_segments import is_empty_shot


VALID_SHOT_BLEND = {
    "Cut", "Ease In Out", "Ease In", "Ease Out",
    "Hard In", "Hard Out", "Linear", "Custom",
}

VALID_LAYOUTS = Literal[
    "two_person",
    "L_shape",
    "triangle",
    "line",
    "square",
    "arc",
    "cluster",
    "layered",
]

VALID_SHOT_TYPE = {
    "全景", "中景", "中近景", "近景",
    "第一人称镜头", "肩后镜头", "侧跟镜头", "环绕镜头",
    "仰拍镜头", "俯拍镜头",
}

# character beat 必须存在的字段
_CHARACTER_REQUIRED = {"shot", "shot_blend", "shot_type", "Follow"}
# scene beat 必须存在的字段
_SCENE_REQUIRED = {"shot", "shot_blend", "camera"}


# ─────────────────────────────────────────────
# Pydantic 模型（用于内容校验）
# ─────────────────────────────────────────────

class CharacterBeat(BaseModel):
    shot: Literal["character"]
    shot_blend: str
    shot_type: str
    Follow: int
    motion_detail: str = ""
    shot_description: str = ""

    @field_validator("shot_blend")
    @classmethod
    def check_shot_blend(cls, v: str) -> str:
        if v not in VALID_SHOT_BLEND:
            raise ValueError(f"shot_blend 非法值 {v!r}，可选: {sorted(VALID_SHOT_BLEND)}")
        return v

    @field_validator("shot_type")
    @classmethod
    def check_shot_type(cls, v: str) -> str:
        if v not in VALID_SHOT_TYPE:
            raise ValueError(f"shot_type 非法值 {v!r}，可选: {sorted(VALID_SHOT_TYPE)}")
        return v

    @field_validator("Follow")
    @classmethod
    def check_follow(cls, v: int) -> int:
        if v not in (0, 1):
            raise ValueError(f"Follow 必须是 0 或 1，得到 {v!r}")
        return v

    @field_validator("motion_detail")
    @classmethod
    def check_motion_detail(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("motion_detail 不得为空，必须填写角色动作细节英文描述")
        return v


class SceneBeat(BaseModel):
    shot: Literal["scene"]
    shot_blend: str
    camera: int

    @field_validator("shot_blend")
    @classmethod
    def check_shot_blend(cls, v: str) -> str:
        if v not in VALID_SHOT_BLEND:
            raise ValueError(f"shot_blend 非法值 {v!r}，可选: {sorted(VALID_SHOT_BLEND)}")
        return v


class InitialPositionEntry(BaseModel):
    character: str
    position: str
    state: str

    @field_validator("character", "position", "state")
    @classmethod
    def check_non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("不得为空")
        return v


class CurrentPositionEntry(BaseModel):
    character: str
    position: str

    @field_validator("character", "position")
    @classmethod
    def check_non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("不得为空")
        return v


def _check_unique_character_positions(entries: Any, label: str) -> list[str]:
    """Reject one Position occupied by two or more distinct characters."""
    if not isinstance(entries, list):
        return [f"{label} 必须是数组"]

    occupants: dict[str, list[str]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        character = str(entry.get("character") or "").strip()
        position = str(entry.get("position") or "").strip()
        if character and position:
            characters = occupants.setdefault(position, [])
            if character not in characters:
                characters.append(character)

    errors = []
    for position, characters in occupants.items():
        if len(characters) > 1:
            errors.append(
                f"{label} 中不同人物的 position 必须互不相同；"
                f"{position} 被 {', '.join(characters)} 共用"
            )
    return errors


def validate_script_position_structure(script: list[dict]) -> dict:
    """Validate initial states and per-shot character-position uniqueness."""
    all_errors = []
    if not isinstance(script, list):
        return {
            "valid": False,
            "errors": [{
                "scene": -1,
                "beat": -1,
                "speaker": "position schema",
                "errors": ["剧本必须是数组"],
            }],
        }

    for si, scene_obj in enumerate(script):
        if not isinstance(scene_obj, dict):
            continue

        initial_errors = []
        initial_positions = scene_obj.get("initial position")
        if initial_positions is None:
            initial_errors.append("缺少 initial position 字段")
        elif not isinstance(initial_positions, list):
            initial_errors.append("initial position 必须是数组")
        else:
            for ii, entry in enumerate(initial_positions):
                try:
                    InitialPositionEntry.model_validate(entry)
                except Exception as exc:
                    if hasattr(exc, "errors"):
                        for error in exc.errors():
                            field = " -> ".join(str(part) for part in error["loc"])
                            initial_errors.append(
                                f"initial position[{ii}].{field}: {error['msg']}"
                            )
                    else:
                        initial_errors.append(f"initial position[{ii}]: {exc}")
            initial_errors.extend(
                _check_unique_character_positions(initial_positions, "initial position")
            )

        if initial_errors:
            all_errors.append({
                "scene": si,
                "beat": -1,
                "speaker": "initial position",
                "errors": initial_errors,
            })

        for bi, beat in enumerate(scene_obj.get("scene", []) or []):
            if not isinstance(beat, dict):
                continue
            current_positions = beat.get("current position")
            if current_positions is None:
                continue  # 必填性由 JSON spec 校验；此处专注内容与唯一性。

            current_errors = []
            if not isinstance(current_positions, list):
                current_errors.append("current position 必须是数组")
            else:
                for pi, entry in enumerate(current_positions):
                    try:
                        CurrentPositionEntry.model_validate(entry)
                    except Exception as exc:
                        if hasattr(exc, "errors"):
                            for error in exc.errors():
                                field = " -> ".join(str(part) for part in error["loc"])
                                current_errors.append(
                                    f"current position[{pi}].{field}: {error['msg']}"
                                )
                        else:
                            current_errors.append(f"current position[{pi}]: {exc}")
                current_errors.extend(
                    _check_unique_character_positions(current_positions, "current position")
                )

            if current_errors:
                all_errors.append({
                    "scene": si,
                    "beat": bi,
                    "speaker": beat.get("speaker", str(beat.get("move", ""))),
                    "errors": current_errors,
                })

    return {"valid": len(all_errors) == 0, "errors": all_errors}


# ─────────────────────────────────────────────
# 阶段一：结构校验（初稿后，只查字段是否存在）
# ─────────────────────────────────────────────

def _check_beat_structure(beat: dict) -> list[str]:
    shot = beat.get("shot", "")
    if is_empty_shot(beat):
        errors = []
        if shot != "scene":
            errors.append("空镜的 shot 必须为 'scene'")
        missing = {"shot", "shot_blend", "camera", "duration", "actions"} - beat.keys()
        if missing:
            errors.append(f"空镜缺少字段: {', '.join(sorted(missing))}")
        return errors
    if not shot:
        return ["shot 字段缺失或为空"]
    if shot not in ("character", "scene"):
        return [f"shot 非法值 {shot!r}，必须是 'character' 或 'scene'"]

    required = _CHARACTER_REQUIRED if shot == "character" else _SCENE_REQUIRED
    missing = required - beat.keys()
    return [f"缺少字段: {', '.join(sorted(missing))}"] if missing else []


def validate_script_shot_structure(script: list[dict]) -> dict:
    """
    初稿后调用。校验站位结构，以及 shot 字段存在性与类型合法性。
    """
    all_errors = list(validate_script_position_structure(script)["errors"])
    for si, scene_obj in enumerate(script):
        for bi, beat in enumerate(scene_obj.get("scene", [])):
            errs = _check_beat_structure(beat)
            if errs:
                all_errors.append({
                    "scene": si, "beat": bi,
                    "speaker": beat.get("speaker", str(beat.get("move", ""))),
                    "errors": errs,
                })
    return {"valid": len(all_errors) == 0, "errors": all_errors}


# ─────────────────────────────────────────────
# 阶段二：内容校验（摄影管线完成后，校验字段值）
# ─────────────────────────────────────────────

def _check_beat_content(beat: dict) -> list[str]:
    shot = beat.get("shot", "")
    errors: list[str] = []
    if is_empty_shot(beat):
        if shot != "scene":
            errors.append("空镜的 shot 必须为 'scene'")
        if not beat.get("duration"):
            errors.append("空镜的 duration 不得为空")
        if beat.get("actions"):
            errors.append("空镜的 actions 必须为空数组")
        if "shot_type" in beat or "Follow" in beat:
            errors.append("空镜不得包含人物镜头字段 shot_type/Follow")
        if errors:
            return errors
    if not shot:
        return ["shot 字段缺失或为空"]

    try:
        if shot == "character":
            CharacterBeat(**beat)
        elif shot == "scene":
            SceneBeat(**beat)
        else:
            errors.append(f"shot 非法值 {shot!r}")
    except Exception as exc:
        if hasattr(exc, "errors"):
            for e in exc.errors():
                loc = " -> ".join(str(x) for x in e["loc"])
                errors.append(f"{loc}: {e['msg']}")
        else:
            errors.append(str(exc))

    return errors


def validate_script_shots(script: list[dict]) -> dict:
    """
    摄影管线完成后调用。校验所有 beat 的 shot 字段值是否合法。
    """
    all_errors = list(validate_script_position_structure(script)["errors"])
    for si, scene_obj in enumerate(script):
        for bi, beat in enumerate(scene_obj.get("scene", [])):
            errs = _check_beat_content(beat)
            if errs:
                all_errors.append({
                    "scene": si, "beat": bi,
                    "speaker": beat.get("speaker", str(beat.get("move", ""))),
                    "errors": errs,
                })
    return {"valid": len(all_errors) == 0, "errors": all_errors}


# ─────────────────────────────────────────────
# Position Plan 校验（Stage 2 完成后）
# ─────────────────────────────────────────────

class _PositionEntry(BaseModel):
    position_id: str
    character: str

    @field_validator("position_id", "character")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("不能为空")
        return v


class _PositionGroup(BaseModel):
    group_id: str
    layout: VALID_LAYOUTS
    region: str
    positions: List[_PositionEntry]
    # neartarget 只存在于 single，group 用锚点采样点位，不需要此字段

    @field_validator("group_id", "region")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("不能为空")
        return v

    @model_validator(mode="before")
    @classmethod
    def drop_neartarget(cls, data: dict) -> dict:
        # neartarget 只在 single 点位有效，group 点位强制丢弃
        data.pop("neartarget", None)
        return data


class _PositionSingle(BaseModel):
    position_id: str
    character: str
    region: str
    neartarget: str = ""
    lookat: str = ""

    @field_validator("position_id", "character", "region")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("不能为空")
        return v


class _PositionPlan(BaseModel):
    where: str
    groups: List[_PositionGroup] = []
    singles: List[_PositionSingle] = []

    @field_validator("where")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("不能为空")
        return v


def _fmt_errors(exc: Exception) -> list[str]:
    if hasattr(exc, "errors"):
        result = []
        for e in exc.errors():
            loc = " -> ".join(str(x) for x in e["loc"])
            result.append(f"{loc}: {e['msg']}")
        return result
    return [str(exc)]


def validate_position_plan(plan: dict) -> dict:
    """
    Stage 2 规划完成后调用。校验 position plan 关键字段均不为空。
    返回 {"valid": bool, "errors": [...]}
    """
    all_errors: list[str] = []
    try:
        _PositionPlan(**{k: plan[k] for k in _PositionPlan.model_fields if k in plan})
    except Exception as exc:
        all_errors.extend(_fmt_errors(exc))
    return {"valid": len(all_errors) == 0, "errors": all_errors}


# ─────────────────────────────────────────────
# 自然语言错误格式化（供 prompt 反馈使用）
# ─────────────────────────────────────────────

def format_shot_structure_errors(errors: list[dict]) -> str:
    """将 validate_script_shot_structure 错误列表格式化为自然语言"""
    lines = []
    for item in errors:
        label = item.get("speaker") or f"第{item['beat']}幕"
        for err in item["errors"]:
            lines.append(f"场景{item['scene']} {label}：{err}")
    return "\n".join(lines)


def format_shot_content_errors(errors: list[dict]) -> str:
    """将 validate_script_shots 错误列表格式化为自然语言"""
    lines = []
    for item in errors:
        label = item.get("speaker") or f"第{item['beat']}幕"
        for err in item["errors"]:
            lines.append(f"场景{item['scene']} {label}：{err}")
    return "\n".join(lines)


def format_position_plan_errors(errors: list[str]) -> str:
    """将 validate_position_plan 错误列表格式化为自然语言"""
    return "\n".join(errors) if errors else ""


# ─────────────────────────────────────────────
# camera_script 校验（cinematography pipeline 完成后）
# ─────────────────────────────────────────────

# camera_script 中 shot_blend 已经过 _normalise_shot_blend 规范化为小写三值
VALID_CAMERA_SHOT_BLEND = {"cut", "blend", "easein"}


class CameraScriptEvent(BaseModel):
    event_index: int
    shot: str
    target: str
    target_position: str
    shot_type: str
    shot_blend: str
    follow: int
    camera: Optional[int] = None
    shot_description: str
    motion_enabled: bool
    motion_preset: str
    play_motion_on_activate: Optional[bool] = None
    motion_start_delay: Optional[float] = None
    motion_reset_on_replay: Optional[bool] = None

    @field_validator("shot_type")
    @classmethod
    def check_shot_type(cls, v: str) -> str:
        if v not in VALID_SHOT_TYPE:
            raise ValueError(f"shot_type 非法值 {v!r}，可选: {sorted(VALID_SHOT_TYPE)}")
        return v

    @field_validator("shot_blend")
    @classmethod
    def check_shot_blend(cls, v: str) -> str:
        if v not in VALID_CAMERA_SHOT_BLEND:
            raise ValueError(f"shot_blend 非法值 {v!r}，可选: {sorted(VALID_CAMERA_SHOT_BLEND)}")
        return v

    @field_validator("follow")
    @classmethod
    def check_follow(cls, v: int) -> int:
        if v not in (0, 1):
            raise ValueError(f"follow 必须是 0 或 1，得到 {v!r}")
        return v

    @field_validator("shot_description")
    @classmethod
    def check_shot_description(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("shot_description 不得为空，必须填写镜头英文描述")
        return v

    @field_validator("target_position")
    @classmethod
    def check_target_position(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("target_position 不得为空，必须对应角色的当前锚点位置")
        return v


class CameraScriptScene(BaseModel):
    scene_index: int
    events: List[CameraScriptEvent]


class CameraScriptModel(BaseModel):
    scenes: List[CameraScriptScene]


def validate_camera_script(camera_script: dict) -> dict:
    """
    _build_camera_script 完成后调用。校验每个 event 的字段值是否合法。
    返回 {"valid": bool, "errors": [...]}
    """
    all_errors = []
    for scene in camera_script.get("scenes", []):
        scene_index = scene.get("scene_index", 0)
        for event in scene.get("events", []):
            event_index = event.get("event_index", 0)
            try:
                CameraScriptEvent(**event)
            except Exception as exc:
                errors: list[str] = []
                if hasattr(exc, "errors"):
                    for e in exc.errors():
                        loc = " -> ".join(str(x) for x in e["loc"])
                        errors.append(f"{loc}: {e['msg']}")
                else:
                    errors.append(str(exc))
                all_errors.append({
                    "scene_index": scene_index,
                    "event_index": event_index,
                    "errors": errors,
                })
    return {"valid": len(all_errors) == 0, "errors": all_errors}


def format_camera_script_errors(errors: list[dict]) -> str:
    """将 validate_camera_script 错误列表格式化为自然语言（供日志和 LLM prompt 使用）"""
    lines = []
    for item in errors:
        prefix = f"scene {item['scene_index']} event {item['event_index']}"
        for err in item["errors"]:
            lines.append(f"{prefix}: {err}")
    return "\n".join(lines)
