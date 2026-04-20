"""
Pydantic schema — shot 字段校验

两阶段：
  validate_script_shot_structure  初稿后调用，只查字段是否存在（不校验值）
  validate_script_shots           摄影管线完成后调用，校验字段值是否合法
"""

from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, field_validator


VALID_SHOT_BLEND = {
    "Cut", "Ease In Out", "Ease In", "Ease Out",
    "Hard In", "Hard Out", "Linear", "Custom",
}

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


# ─────────────────────────────────────────────
# 阶段一：结构校验（初稿后，只查字段是否存在）
# ─────────────────────────────────────────────

def _check_beat_structure(beat: dict) -> list[str]:
    shot = beat.get("shot", "")
    if not shot:
        return ["shot 字段缺失或为空"]
    if shot not in ("character", "scene"):
        return [f"shot 非法值 {shot!r}，必须是 'character' 或 'scene'"]

    required = _CHARACTER_REQUIRED if shot == "character" else _SCENE_REQUIRED
    missing = required - beat.keys()
    return [f"缺少字段: {', '.join(sorted(missing))}"] if missing else []


def validate_script_shot_structure(script: list[dict]) -> dict:
    """
    初稿后调用。只校验 shot 字段存在性与类型合法性，不校验具体值。
    """
    all_errors = []
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
    if not shot:
        return ["shot 字段缺失或为空"]

    try:
        if shot == "character":
            CharacterBeat(**{k: beat[k] for k in CharacterBeat.model_fields if k in beat})
        elif shot == "scene":
            SceneBeat(**{k: beat[k] for k in SceneBeat.model_fields if k in beat})
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
    all_errors = []
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
