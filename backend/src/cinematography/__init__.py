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

        # Save intermediate results
        inter_path = output_dir / f"cinematography_{timestamp}.json"
        with open(inter_path, "w", encoding="utf-8") as f:
            json.dump(
                {"stage1_results": stage1_all, "stage2_results": stage2_all, "stage3_results": stage3_all},
                f, ensure_ascii=False, indent=2,
            )

        # Always save position_plan and position_detail as standalone downloadable files
        position_plan_filename = f"position_plan_{timestamp}.json"
        position_detail_filename = f"position_detail_{timestamp}.json"
        with open(output_dir / position_plan_filename, "w", encoding="utf-8") as f:
            json.dump(last_position_plan or {"where": "", "groups": [], "singles": []}, f, ensure_ascii=False, indent=2)
        with open(output_dir / position_detail_filename, "w", encoding="utf-8") as f:
            json.dump(last_position_detail or {"where": "", "groups": [], "signals": []}, f, ensure_ascii=False, indent=2)

        return {
            "ok": True,
            "enriched_script": enriched_script,
            "filename": inter_path.name,
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
