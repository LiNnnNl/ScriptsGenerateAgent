"""
Stage 2 of the cinematography pipeline: Position Planning.

Three substages
---------------
Substage 1 (grouping)     : LLM reads LayoutLib + script positions →
                            assigns positions (with characters) to groups (2+) or singles.
Substage 2 (planning)     : LLM reads groups + scene_info →
                            assigns region / neartarget / layout / lookat.
Substage 3 (coordinates)  : CoordinateSkill computes x/y/z for each position
                            (stored in position_coordinates, separate from position_detail).

Outputs
-------
position_plan_json   – {where, groups: [{group_id, layout, region, neartarget,
                        positions: [{position_id, character}], lookat}],
                        singles: [{position_id, character, region, neartarget, lookat}]}
position_detail_json – {where, groups: [{position_id, group_id, region, character,
                        layout, lookat}], singles: [{position_id, character, region,
                        neartarget, lookat}]}
"""

import copy
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from .coordinate_skill import CoordinateSkill
from .position_detail_converter import PositionDetailConverter
from ..position_metadata import attach_position_metadata, normalize_position_metadata
from ..schema import validate_position_plan, format_position_plan_errors
from ..scene_segments import is_empty_shot
from ..prompt_files.cinematography_position_grouping import cinematography_position_grouping_prompt
from ..prompt_files.cinematography_position_planning import cinematography_position_planning_prompt

logger = logging.getLogger(__name__)

STAGE_FILENAME = "director_stage2_position_planning.json"
GROUPING_FILENAME = "position_stage1_grouping.json"
PLANNING_FILENAME = "position_stage2_planning.json"
COORDINATE_FILENAME = "position_stage3_compilation.json"


class CinematographyPositionStage:

    def __init__(
        self,
        script_json: Dict,
        scene_info_json: Dict,
        layout_lib_json: Dict,
        llm_client,
        stage_output_dir,
        progress_callback=None,
    ):
        self.script_json = script_json
        self.scene_info_json = scene_info_json
        self.layout_lib_json = layout_lib_json
        self.llm_client = llm_client
        self.stage_output_dir = Path(stage_output_dir)
        self.stage_output_dir.mkdir(parents=True, exist_ok=True)
        self.progress_callback = progress_callback

        self.grouping_result: Optional[Dict] = None
        self.planning_result: Optional[Dict] = None
        self.coordinate_result: Optional[Dict] = None

    # ──────────────────────────────────────────────
    # Public
    # ──────────────────────────────────────────────

    def run(self) -> Dict:
        where = self._resolve_where()
        position_ids = self._collect_position_ids()
        char_map = self._collect_position_characters()
        shot_descriptions = self._collect_shot_descriptions()

        # Substage 1: grouping (with character assignments)
        self.grouping_result = self._run_grouping(where, position_ids, char_map, shot_descriptions)
        self._save(GROUPING_FILENAME, self.grouping_result)
        self._emit_progress("success", "position_grouping", self._format_grouping_summary(where, self.grouping_result))
        logger.info("[Stage2][grouping] groups=%d singles=%d",
                    len(self.grouping_result.get("groups", [])),
                    len(self.grouping_result.get("singles", [])))

        # Substage 2: planning (region/neartarget/lookat)
        self.planning_result = self._run_planning(where, self.grouping_result)
        position_metadata = normalize_position_metadata(self.script_json)
        attach_position_metadata(self.planning_result, position_metadata)
        self._save(PLANNING_FILENAME, self.planning_result)
        self._emit_progress("success", "position_planning", self._format_planning_summary(where, self.planning_result))
        logger.info("[Stage2][planning] done")

        # Substage 3: coordinate calculation
        self.coordinate_result = self._run_coordinates(self.planning_result)
        self._save(COORDINATE_FILENAME, self.coordinate_result)
        self._emit_progress("success", "position_coordinates", self._format_coordinate_summary(where, self.coordinate_result))
        logger.info("[Stage2][coordinates] positions resolved=%d",
                    len(self.coordinate_result.get("positions", {})))

        # Build position_detail via PositionDetailConverter (same as Unity)
        position_detail = PositionDetailConverter(self.planning_result).convert()
        attach_position_metadata(position_detail, position_metadata)

        stage_result = {
            "where": where,
            "stage": "position_planning",
            "substages": {
                "grouping": copy.deepcopy(self.grouping_result),
                "planning": copy.deepcopy(self.planning_result),
                "coordinates": copy.deepcopy(self.coordinate_result),
            },
            "position_plan": copy.deepcopy(self.planning_result),
            "position_detail": copy.deepcopy(position_detail),
            "position_coordinates": copy.deepcopy(self.coordinate_result),
        }
        self._save(STAGE_FILENAME, stage_result)
        return stage_result

    def _emit_progress(self, level: str, phase: str, message: str) -> None:
        if not self.progress_callback:
            return
        try:
            self.progress_callback(level, phase, message)
        except Exception:
            pass

    def _format_grouping_summary(self, where: str, grouping: Dict) -> str:
        lines = [f"📍 [摄影指导期][Stage2]《{where}》站位分组完成"]
        for group in grouping.get("groups", [])[:6]:
            chars = ", ".join(
                item.get("character") or item.get("position_id", "")
                for item in group.get("positions", [])
            )
            lines.append(f"- {group.get('group_id', 'Group')}: {chars} / {group.get('layout', 'layout')}")
        for single in grouping.get("singles", [])[:6]:
            lines.append(f"- 单人: {single.get('character') or single.get('position_id', '')}")
        return "\n".join(lines)

    def _format_planning_summary(self, where: str, planning: Dict) -> str:
        lines = [f"📍 [摄影指导期][Stage2]《{where}》区域与朝向规划完成"]
        for group in planning.get("groups", [])[:6]:
            chars = ", ".join(
                item.get("character") or item.get("position_id", "")
                for item in group.get("positions", [])
            )
            lines.append(f"- {group.get('group_id', 'Group')}: {chars} → {group.get('region', '')}")
        for single in planning.get("singles", [])[:6]:
            lines.append(
                f"- {single.get('character') or single.get('position_id', '')}: {single.get('region', '')} / {single.get('neartarget', '')}"
            )
        return "\n".join(lines)

    def _format_coordinate_summary(self, where: str, coordinates: Dict) -> str:
        positions = coordinates.get("positions", {})
        lines = [f"📍 [摄影指导期][Stage2]《{where}》坐标计算完成：{len(positions)} 个位置"]
        for position_id, value in list(positions.items())[:8]:
            if isinstance(value, dict):
                x = value.get("x", value.get("X", ""))
                y = value.get("y", value.get("Y", ""))
                z = value.get("z", value.get("Z", ""))
                lines.append(f"- {position_id}: ({x}, {y}, {z})")
            else:
                lines.append(f"- {position_id}: {value}")
        return "\n".join(lines)

    # ──────────────────────────────────────────────
    # Substage implementations
    # ──────────────────────────────────────────────

    def _run_grouping(
        self,
        where: str,
        position_ids: List[str],
        char_map: Dict[str, str],
        shot_descriptions: Dict,
    ) -> Dict:
        # Build enriched list with character info for the LLM
        positions_with_chars = [
            {"position_id": pid, "character": char_map.get(pid, "")}
            for pid in position_ids
        ]
        system_prompt = cinematography_position_grouping_prompt
        user_payload = {
            "where": where,
            "positions": positions_with_chars,
            "shot_descriptions": shot_descriptions,
            "layout_lib": self.layout_lib_json,
        }
        try:
            result = self.llm_client.complete_json(system_prompt, user_payload)
            result.setdefault("groups", [])
            result.setdefault("singles", [])
            # Backfill any missing character fields from char_map
            self._backfill_characters(result, char_map)
            return result
        except Exception as exc:
            logger.warning("[Stage2][grouping] LLM failed (%s), using fallback", exc)
            return self._fallback_grouping(position_ids, char_map)

    def _run_planning(self, where: str, grouping: Dict) -> Dict:
        system_prompt = cinematography_position_planning_prompt

        MAX_RETRIES = 2
        correction = ""
        for attempt in range(MAX_RETRIES + 1):
            user_payload: Dict = {
                "grouping": grouping,
                "scene_info": self.scene_info_json,
            }
            if correction:
                user_payload["correction_required"] = correction
            try:
                result = self.llm_client.complete_json(system_prompt, user_payload)
                result.setdefault("where", where)
                result.setdefault("groups", grouping.get("groups", []))
                result.setdefault("singles", grouping.get("singles", []))

                plan_validation = validate_position_plan(result)
                if plan_validation["valid"]:
                    # 写入 outputs 前强制丢弃 group 层的 neartarget
                    for g in result.get("groups", []):
                        g.pop("neartarget", None)
                    return result

                error_msg = format_position_plan_errors(plan_validation["errors"])
                logger.warning(
                    "[Stage2][planning] schema 校验失败（尝试 %d/%d）:\n%s",
                    attempt + 1, MAX_RETRIES + 1, error_msg,
                )
                if attempt < MAX_RETRIES:
                    correction = (
                        f"上次输出存在以下字段问题，请修正后重新输出完整 JSON：\n{error_msg}"
                    )
                else:
                    logger.warning("[Stage2][planning] 已达最大重试次数，使用当前结果继续")
                    return result

            except Exception as exc:
                logger.warning("[Stage2][planning] LLM failed (%s), using fallback", exc)
                result = self._fallback_planning(where, grouping)
                for g in result.get("groups", []):
                    g.pop("neartarget", None)
                return result

        result = self._fallback_planning(where, grouping)
        for g in result.get("groups", []):
            g.pop("neartarget", None)
        return result

    def _run_coordinates(self, planning: Dict) -> Dict:
        """Substage 3: call CoordinateSkill to compute x/y/z (stored separately)."""
        skill = CoordinateSkill()
        positions: Dict[str, Dict[str, float]] = {}

        for group in planning.get("groups", []):
            region = group.get("region", "")
            neartarget = group.get("neartarget", "")
            layout = group.get("layout", "two_person")
            pos_ids = [item.get("position_id", "") for item in group.get("positions", [])]

            coords = skill.calculate(
                region_name=region,
                anchor_name=neartarget,
                layout=layout,
                layout_lib=self.layout_lib_json,
                scene_info=self.scene_info_json,
                group_positions=pos_ids,
            )
            positions.update(coords)

        for single in planning.get("singles", []):
            pos_id = single.get("position_id", "")
            region = single.get("region", "")
            neartarget = single.get("neartarget", "")

            coords = skill.calculate(
                region_name=region,
                anchor_name=neartarget,
                layout="single",
                layout_lib=self.layout_lib_json,
                scene_info=self.scene_info_json,
                group_positions=[pos_id],
            )
            positions.update(coords)

        return {
            "where": planning.get("where", ""),
            "positions": positions,
        }

    # ──────────────────────────────────────────────
    # Fallbacks
    # ──────────────────────────────────────────────

    def _fallback_grouping(self, position_ids: List[str], char_map: Dict[str, str]) -> Dict:
        """All positions become singles when LLM grouping fails."""
        return {
            "groups": [],
            "singles": [
                {"position_id": pid, "character": char_map.get(pid, ""), "rationale": "fallback"}
                for pid in position_ids
            ],
        }

    def _fallback_planning(self, where: str, grouping: Dict) -> Dict:
        """Assign everything to the first available region/anchor."""
        first_region = ""
        first_anchor = ""
        first_layout = "two_person"
        for region in self.scene_info_json.get("regions", []):
            first_region = region.get("name", "")
            anchors = region.get("anchors", [])
            if anchors:
                first_anchor = anchors[0].get("name", "")
            break

        planned_groups = [
            {**g, "region": first_region, "layout": first_layout,
             "lookat": {"mode": "center"}}
            for g in grouping.get("groups", [])
        ]
        planned_singles = [
            {**s, "region": first_region, "neartarget": first_anchor,
             "layout": first_layout, "lookat": "center"}
            for s in grouping.get("singles", [])
        ]
        return {"where": where, "groups": planned_groups, "singles": planned_singles}

    # ──────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────

    def _resolve_where(self) -> str:
        return (self.script_json.get("scene information", {}).get("where")
                or self.script_json.get("where", ""))

    def _collect_position_ids(self) -> List[str]:
        """All unique Position IDs used in the script, in first-seen order."""
        ids: List[str] = []
        seen: set = set()

        def add(pos_id: str):
            if pos_id and pos_id not in seen:
                seen.add(pos_id)
                ids.append(pos_id)

        for entry in self.script_json.get("initial position", []):
            add(entry.get("position", ""))
        for beat in self.script_json.get("scene", []):
            for entry in beat.get("current position", []):
                add(entry.get("position", ""))
            for move in beat.get("move", []):
                add(move.get("destination", ""))
        return ids

    def _collect_position_characters(self) -> Dict[str, str]:
        """
        Build position_id → character mapping from the script.
        First assignment wins (initial position has priority).
        """
        char_map: Dict[str, str] = {}

        def assign(pos_id: str, character: str):
            if pos_id and character and pos_id not in char_map:
                char_map[pos_id] = character

        for entry in self.script_json.get("initial position", []):
            assign(entry.get("position", ""), entry.get("character", ""))
        for beat in self.script_json.get("scene", []):
            for entry in beat.get("current position", []):
                assign(entry.get("position", ""), entry.get("character", ""))
            for move in beat.get("move", []):
                assign(move.get("destination", ""), move.get("character", ""))
        return char_map

    def _collect_shot_descriptions(self) -> Dict[int, str]:
        """beat_index → shot_description (non-empty only)."""
        result: Dict[int, str] = {}
        for i, beat in enumerate(self.script_json.get("scene", []), start=1):
            if is_empty_shot(beat):
                continue
            desc = beat.get("shot_description", "")
            if desc:
                result[i] = desc
        return result

    def _backfill_characters(self, grouping: Dict, char_map: Dict[str, str]):
        """Fill missing character fields in grouping output from char_map."""
        for group in grouping.get("groups", []):
            for item in group.get("positions", []):
                if isinstance(item, dict) and not item.get("character"):
                    item["character"] = char_map.get(item.get("position_id", ""), "")
        for single in grouping.get("singles", []):
            if isinstance(single, dict) and not single.get("character"):
                single["character"] = char_map.get(single.get("position_id", ""), "")

    def _save(self, filename: str, data: Any):
        path = self.stage_output_dir / filename
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
