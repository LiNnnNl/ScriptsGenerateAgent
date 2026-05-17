"""
cinematography package — post-processing pipeline for shot/camera planning.

Entry point: run_cinematography_pipeline()
"""
import copy
import json
import logging
from pathlib import Path

from .client import LLMJsonClient
from .shot_planning_stage import ShotPlanningStage
from .cinematography_position_stage import CinematographyPositionStage
from .camera_planning_stage import CameraPlanningStage

# Lazy import to avoid circular dependency — schema lives outside this package
def _get_camera_validators():
    import sys, importlib
    mod = importlib.import_module("src.schema")
    return mod.validate_camera_script, mod.format_camera_script_errors

logger = logging.getLogger(__name__)

# ───────────────��──────────────────────────────
# shot_blend mapping: Stage 3 → current framework
# ─────────────────���────────────────────────────
# Current framework only recognises "cut" / "blend" / "easein".
# Stage 3 uses a richer set; map them back so downstream Unity stays compatible.
_SHOT_BLEND_MAP = {
    "Cut":        "cut",
    "Hard In":    "cut",
    "Hard Out":   "cut",
    "Ease In":    "easein",
    "Ease In Out":"blend",
    "Ease Out":   "blend",
    "Linear":     "blend",
    "Custom":     "blend",
}


# ──────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────��

def run_cinematography_pipeline(script, scene, resource_dir, output_dir, timestamp):
    """
    Synchronous three-stage cinematography post-processing pipeline.
    Designed to run inside asyncio.get_event_loop().run_in_executor().

    Args:
        script: list of scene-objects (draft_script from autogen pipeline)
        scene: Scene object from resource_loader
        resource_dir: Path to backend/resources/
        output_dir: str path for output files
        timestamp: int timestamp used for naming

    Returns:
        {"ok": True, "enriched_script": [...], "filename": "cinematography_*.json"}
        {"ok": False, "error": "..."}
    """
    try:
        resource_dir = Path(resource_dir)
        output_dir = Path(output_dir)

        camera_lib_path = resource_dir / "cinematography" / "CameraLib.json"
        if not camera_lib_path.exists():
            return {"ok": False, "error": f"CameraLib.json not found at {camera_lib_path}"}

        with open(camera_lib_path, "r", encoding="utf-8-sig") as f:
            camera_lib = json.load(f)

        layout_lib_path = resource_dir / "cinematography" / "LayoutLib.json"
        if not layout_lib_path.exists():
            return {"ok": False, "error": f"LayoutLib.json not found at {layout_lib_path}"}

        with open(layout_lib_path, "r", encoding="utf-8-sig") as f:
            layout_lib = json.load(f)

        # Base scene_info built from resource (where = scene.name placeholder)
        base_scene_info = get_scene_info_json(scene, resource_dir)

        stage_output_dir = output_dir / "CinematographyStages"
        stage_output_dir.mkdir(parents=True, exist_ok=True)

        client = LLMJsonClient()

        enriched_script = []
        stage1_all = []
        stage2_all = []
        stage3_all = []
        last_position_plan = None
        last_position_detail = None
        # Per-scene data kept for the Stage-3 retry loop
        _scene_retry_data = []

        for scene_obj in (script if isinstance(script, list) else [script]):
            if not isinstance(scene_obj, dict):
                enriched_script.append(scene_obj)
                continue

            # ── Stage 1: ShotPlanningStage ────────────���────────────────
            try:
                stage1 = ShotPlanningStage(
                    script_json=scene_obj,
                    llm_client=client,
                    output_path=stage_output_dir / f"stage1_script_{timestamp}.json",
                    stage_output_dir=stage_output_dir,
                )
                result1 = stage1.run()
                enriched_scene = result1.get("script_with_shot_description") or scene_obj
                if isinstance(enriched_scene, list) and enriched_scene:
                    enriched_scene = enriched_scene[0]
                stage1_all.append(result1)
                logger.info("[Cinematography] Stage 1 done: %s",
                            scene_obj.get("scene information", {}).get("where", "?"))
            except Exception as e:
                logger.warning("[Cinematography] Stage 1 failed, skipping: %s", e)
                enriched_scene = scene_obj

            # ── 冲突修复1: save "scene" beats before Stage 3 overwrites them ──
            scene_shot_backup = _backup_scene_shots(enriched_scene)

            # ── 冲突修复3: build per-scene scene_info with correct `where` ──
            # CameraPlanningStage._build_region_map validates scene_info.where == script.where
            # The script's where comes from DirectorAgent (AI-generated text), which may differ
            # from scene.name in the resource. Override scene_info.where with the script's value.
            script_where = enriched_scene.get("scene information", {}).get("where", "")
            scene_info = copy.deepcopy(base_scene_info)
            if script_where:
                scene_info["where"] = script_where

            # ── Stage 2: CinematographyPositionStage ──────────────────
            position_plan_json = None
            position_detail_json = None
            try:
                stage2 = CinematographyPositionStage(
                    script_json=enriched_scene,
                    scene_info_json=scene_info,
                    layout_lib_json=layout_lib,
                    llm_client=client,
                    stage_output_dir=stage_output_dir,
                )
                result2 = stage2.run()
                position_plan_json = result2.get("position_plan")
                position_detail_json = result2.get("position_detail")
                if position_plan_json:
                    last_position_plan = position_plan_json
                if position_detail_json:
                    last_position_detail = position_detail_json
                stage2_all.append(result2)
                logger.info("[Cinematography] Stage 2 done: %s",
                            scene_obj.get("scene information", {}).get("where", "?"))
            except Exception as e:
                logger.warning("[Cinematography] Stage 2 failed, skipping position plan: %s", e)

            # ── Stage 3: CameraPlanningStage ────────────────────────
            try:
                stage3 = CameraPlanningStage(
                    script_json=enriched_scene,
                    scene_info_json=scene_info,
                    camera_lib_json=camera_lib,
                    position_plan_json=position_plan_json,
                    position_detail_json=position_detail_json,
                    llm_client=client,
                    output_dir=stage_output_dir,
                    stage_output_dir=stage_output_dir,
                )
                result3 = stage3.run()
                final_scene = result3.get("script_with_camera_plan") or enriched_scene
                if isinstance(final_scene, list) and final_scene:
                    final_scene = final_scene[0]
                stage3_all.append(result3)
                logger.info("[Cinematography] Stage 3 done: %s",
                            scene_obj.get("scene information", {}).get("where", "?"))
            except Exception as e:
                logger.warning("[Cinematography] Stage 3 failed, using Stage 1 result: %s", e)
                final_scene = enriched_scene

            # ── 冲突修复1: restore shot:"scene" + camera fields for move nodes ──
            _restore_scene_shots(final_scene, scene_shot_backup)

            # ── 冲突修复2: normalise shot_blend to current framework format ──
            _normalise_shot_blend(final_scene)

            enriched_script.append(final_scene)
            _scene_retry_data.append({
                "scene_obj": final_scene,
                "scene_info": scene_info,
                "position_plan": position_plan_json,
                "position_detail": position_detail_json,
            })

        # Save intermediate results
        inter_path = output_dir / f"cinematography_{timestamp}.json"
        with open(inter_path, "w", encoding="utf-8") as f:
            json.dump(
                {"stage1_results": stage1_all, "stage2_results": stage2_all, "stage3_results": stage3_all},
                f, ensure_ascii=False, indent=2,
            )

        # Build camera_script and validate; retry Stage 3 once if invalid
        camera_script = _build_camera_script(enriched_script, camera_lib)
        try:
            _validate_camera_script, _format_camera_script_errors = _get_camera_validators()
            validation = _validate_camera_script(camera_script)
            if not validation["valid"]:
                error_desc = _format_camera_script_errors(validation["errors"])
                logger.warning("[Cinematography] camera_script validation failed:\n%s", error_desc)

                failed_scene_indices = {err["scene_index"] for err in validation["errors"]}
                for retry_si in failed_scene_indices:
                    if retry_si >= len(_scene_retry_data):
                        continue
                    data = _scene_retry_data[retry_si]
                    logger.info("[Cinematography] Retrying Stage 3 for scene %d", retry_si)
                    try:
                        stage3_retry = CameraPlanningStage(
                            script_json=data["scene_obj"],
                            scene_info_json=data["scene_info"],
                            camera_lib_json=camera_lib,
                            position_plan_json=data["position_plan"],
                            position_detail_json=data["position_detail"],
                            llm_client=client,
                            output_dir=stage_output_dir,
                            stage_output_dir=stage_output_dir,
                        )
                        retry_result = stage3_retry.run()
                        new_scene = retry_result.get("script_with_camera_plan")
                        if new_scene:
                            if isinstance(new_scene, list) and new_scene:
                                new_scene = new_scene[0]
                            backup = _backup_scene_shots(data["scene_obj"])
                            _restore_scene_shots(new_scene, backup)
                            _normalise_shot_blend(new_scene)
                            enriched_script[retry_si] = new_scene
                            logger.info("[Cinematography] Stage 3 retry done for scene %d", retry_si)
                    except Exception as retry_exc:
                        logger.warning("[Cinematography] Stage 3 retry failed for scene %d: %s", retry_si, retry_exc)

                # Rebuild camera_script with (possibly corrected) enriched_script
                camera_script = _build_camera_script(enriched_script, camera_lib)
                retry_validation = _validate_camera_script(camera_script)
                if retry_validation["valid"]:
                    logger.info("[Cinematography] camera_script validation passed after Stage 3 retry")
                else:
                    logger.warning(
                        "[Cinematography] camera_script still invalid after retry (using best-effort):\n%s",
                        _format_camera_script_errors(retry_validation["errors"]),
                    )
            else:
                logger.info("[Cinematography] camera_script validation passed")
        except Exception as val_exc:
            logger.warning("[Cinematography] camera_script validation error (skipped): %s", val_exc)

        camera_script_filename = f"camera_script_{timestamp}.json"
        with open(output_dir / camera_script_filename, "w", encoding="utf-8") as f:
            json.dump(camera_script, f, ensure_ascii=False, indent=2)

        # Always save position_plan and position_detail as standalone downloadable files
        position_plan_filename = f"position_plan_{timestamp}.json"
        position_detail_filename = f"position_detail_{timestamp}.json"
        with open(output_dir / position_plan_filename, "w", encoding="utf-8") as f:
            json.dump(last_position_plan or {"where": "", "groups": [], "singles": []}, f, ensure_ascii=False, indent=2)
        with open(output_dir / position_detail_filename, "w", encoding="utf-8") as f:
            json.dump(last_position_detail or {"where": "", "groups": [], "singles": []}, f, ensure_ascii=False, indent=2)

        return {
            "ok": True,
            "enriched_script": enriched_script,
            "filename": inter_path.name,
            "camera_script_filename": camera_script_filename,
            "position_plan_filename": position_plan_filename,
            "position_detail_filename": position_detail_filename,
        }

    except Exception as e:
        logger.exception("[Cinematography] pipeline exception")
        return {"ok": False, "error": str(e)}


# ─────────────────────────────��────────────────
# Conflict-fix helpers
# ────────────────────────────────────────────��─

def _backup_scene_shots(scene_obj):
    """
    Return a dict mapping beat_index → {"shot": "scene", "camera": ...} for every
    move beat.  Move beats use a fixed Unity camera index; Stage 3 would wrongly
    overwrite shot to "character".  Detection is by "move" key presence, not by
    shot value (director no longer pre-fills shot).
    """
    backup = {}
    for idx, beat in enumerate(scene_obj.get("scene", [])):
        if not isinstance(beat, dict):
            continue
        if "move" in beat:
            entry = {"shot": "scene"}
            if "camera" in beat:
                entry["camera"] = beat["camera"]
            backup[idx] = entry
    return backup


def _restore_scene_shots(scene_obj, backup):
    """Restore shot/camera fields saved by _backup_scene_shots."""
    if not backup:
        return
    for idx, beat in enumerate(scene_obj.get("scene", [])):
        if not isinstance(beat, dict):
            continue
        if idx in backup:
            beat["shot"] = backup[idx]["shot"]
            if "camera" in backup[idx]:
                beat["camera"] = backup[idx]["camera"]
            # Stage 3 assigns shot_type based on "character" context;
            # for "scene" beats use a wide shot if Stage 3 picked a close one.
            if beat.get("shot_type") in ("近景", "中近景"):
                beat["shot_type"] = "全景"


def _normalise_shot_blend(scene_obj):
    """
    Map Stage 3 shot_blend values ("Cut", "Ease In Out", ...) back to the
    three values the current framework and Unity runtime recognise:
    "cut" / "blend" / "easein".
    """
    for beat in scene_obj.get("scene", []):
        if not isinstance(beat, dict):
            continue
        raw = beat.get("shot_blend", "")
        if raw in _SHOT_BLEND_MAP:
            beat["shot_blend"] = _SHOT_BLEND_MAP[raw]
        elif isinstance(raw, str) and raw.lower() in ("cut", "blend", "easein"):
            beat["shot_blend"] = raw.lower()
        # If value is already correct (lowercase), leave it; unknown values left as-is.


# ──────────────────────────────────────────────
# scene_info_json builders
# ──────────────────────────────────────────────

def get_scene_info_json(scene, resource_dir):
    """
    Priority: hand-crafted JSON > auto-generated from Scene object.
    Hand-crafted files: resources/cinematography/scene_info/{scene.id}.json
    """
    resource_dir = Path(resource_dir)
    custom_path = resource_dir / "cinematography" / "scene_info" / f"{scene.id}.json"
    if custom_path.exists():
        with open(custom_path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    return _build_scene_info_from_scene(scene)


def _build_camera_script(enriched_script, camera_lib):
    """
    Extract camera fields from the enriched script into a standalone camera_script structure.
    Each event maps 1-to-1 with a scene beat via event_index.
    Motion settings are derived from CameraLib DefaultMotionPreset for the chosen shot_type.
    """
    scenes = []
    script_list = enriched_script if isinstance(enriched_script, list) else [enriched_script]
    for scene_index, scene_obj in enumerate(script_list):
        if not isinstance(scene_obj, dict):
            continue
        events = []
        for event_index, beat in enumerate(scene_obj.get("scene", [])):
            if not isinstance(beat, dict):
                continue

            shot_type = beat.get("shot_type", "")
            cam_def = camera_lib.get(shot_type, {})
            default_preset = cam_def.get("DefaultMotionPreset", "none")
            motion_enabled = default_preset != "none"

            # Camera subject: speaker for dialogue beats, first mover for move beats
            target = beat.get("speaker") or ""
            if not target:
                moves = beat.get("move") or []
                if moves and isinstance(moves, list):
                    target = moves[0].get("character", "")

            # Find target's current position
            target_position = ""
            if target:
                for pos_entry in beat.get("current position", []):
                    if isinstance(pos_entry, dict) and pos_entry.get("character") == target:
                        target_position = pos_entry.get("position", "")
                        break

            event = {
                "event_index": event_index,
                "shot": beat.get("shot", "character"),
                "target": target,
                "target_position": target_position,
                "shot_type": shot_type,
                "shot_blend": beat.get("shot_blend", "cut"),
                "follow": beat.get("Follow", 0),
                "camera": beat.get("camera", None),
                "shot_description": beat.get("shot_description", ""),
                "motion_enabled": motion_enabled,
                "motion_preset": default_preset,
            }
            if motion_enabled:
                event["play_motion_on_activate"] = True
                event["motion_start_delay"] = 0.0
                event["motion_reset_on_replay"] = True

            events.append(event)

        scenes.append({"scene_index": scene_index, "events": events})

    return {"scenes": scenes}


def _build_scene_info_from_scene(scene):
    """
    Auto-generate scene_info_json from a Scene object (scenes_resource.json format).
    The `where` field is set to scene.name here; run_cinematography_pipeline overrides
    it per-scene with the script's actual `scene information.where` value before
    passing to CameraPlanningStage, avoiding the where-mismatch ValueError.
    """
    regions = []

    if scene.camera_groups:
        pos_desc = {p["id"]: p for p in scene.valid_positions}
        for group in scene.camera_groups:
            group_name = group.get("name", group.get("id", ""))
            anchors = [
                {
                    "name": pid,
                    "description": pos_desc.get(pid, {}).get("description", ""),
                    "is_sittable": pos_desc.get(pid, {}).get("is_sittable", False),
                }
                for pid in group.get("position_ids", [])
            ]
            regions.append({
                "name": group_name,
                "description": scene.description,
                "anchors": anchors,
                "spatial_relations": {},
            })
    else:
        anchors = [
            {
                "name": pos["id"],
                "description": pos.get("description", ""),
                "is_sittable": pos.get("is_sittable", False),
            }
            for pos in scene.valid_positions
        ]
        regions.append({
            "name": scene.name,
            "description": scene.description,
            "anchors": anchors,
            "spatial_relations": {},
        })

    return {
        "where": scene.name,
        "scene_id": scene.id,
        "description": scene.description,
        "regions": regions,
    }
