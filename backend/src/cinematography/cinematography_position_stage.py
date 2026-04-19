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
                        layout, lookat}], signals: [{position_id, character, region,
                        neartarget, lookat}]}
"""

import copy
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from .coordinate_skill import CoordinateSkill

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
    ):
        self.script_json = script_json
        self.scene_info_json = scene_info_json
        self.layout_lib_json = layout_lib_json
        self.llm_client = llm_client
        self.stage_output_dir = Path(stage_output_dir)
        self.stage_output_dir.mkdir(parents=True, exist_ok=True)

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
        logger.info("[Stage2][grouping] groups=%d singles=%d",
                    len(self.grouping_result.get("groups", [])),
                    len(self.grouping_result.get("singles", [])))

        # Substage 2: planning (region/neartarget/lookat)
        self.planning_result = self._run_planning(where, self.grouping_result)
        self._save(PLANNING_FILENAME, self.planning_result)
        logger.info("[Stage2][planning] done")

        # Substage 3: coordinate calculation
        self.coordinate_result = self._run_coordinates(self.planning_result)
        self._save(COORDINATE_FILENAME, self.coordinate_result)
        logger.info("[Stage2][coordinates] positions resolved=%d",
                    len(self.coordinate_result.get("positions", {})))

        # Build position_detail by flattening position_plan
        position_detail = self._build_position_detail(where, self.planning_result)

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
        system_prompt = (
            "You are a position grouping agent for a cinematography pipeline.\n"
            "Given a list of positions (each with position_id and character), group them into:\n"
            "  - groups: 2+ positions used together for interaction; pick a layout from LayoutLib\n"
            "  - singles: positions used alone or as background observers\n\n"
            "Rules:\n"
            "  1. Every position_id must appear in exactly one group or single.\n"
            "  2. Only use layout names that exist in layout_lib.layout_library[*].layout.\n"
            "  3. Match layout min_people/max_people constraints to group size.\n"
            "  4. Preserve the character field from input in every positions entry.\n"
            "  5. Output a single JSON object, no extra text.\n\n"
            "Output schema:\n"
            "{\n"
            "  \"groups\": [\n"
            "    {\"group_id\": \"G1\", \"layout\": \"two_person\",\n"
            "     \"positions\": [{\"position_id\": \"Position 1\", \"character\": \"CharA\"},\n"
            "                    {\"position_id\": \"Position 2\", \"character\": \"CharB\"}],\n"
            "     \"rationale\": \"main dialogue pair\"}\n"
            "  ],\n"
            "  \"singles\": [\n"
            "    {\"position_id\": \"Position 3\", \"character\": \"CharC\", \"rationale\": \"observer\"}\n"
            "  ]\n"
            "}"
        )
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
        system_prompt = (
            "You are a position planning agent for a cinematography pipeline.\n"
            "For each group and single from grouping, assign placement information.\n\n"
            "Rules:\n"
            "  1. region must be one of scene_info.regions[*].name.\n"
            "  2. neartarget must be an anchor name or scene_marker name within that region.\n"
            "  3. Spread groups across different regions for spatial variety.\n"
            "  4. Respect spatial_relations: avoid placing frequently-interacting groups\n"
            "     in regions with relation='far' unless the story requires it.\n"
            "  5. lookat for groups: {\"mode\": \"center\"} or {\"mode\": \"target\", \"target_character\": \"...\"}.\n"
            "  6. lookat for singles: anchor name or character name to face.\n"
            "  7. Preserve positions[*].character from input.\n"
            "  8. Output a single JSON object, no extra text.\n\n"
            "Output schema:\n"
            "{\n"
            "  \"where\": \"SceneName\",\n"
            "  \"groups\": [\n"
            "    {\"group_id\": \"G1\", \"layout\": \"two_person\", \"region\": \"河边走廊\",\n"
            "     \"neartarget\": \"中央锚点\",\n"
            "     \"positions\": [{\"position_id\": \"Position 1\", \"character\": \"CharA\"},\n"
            "                    {\"position_id\": \"Position 2\", \"character\": \"CharB\"}],\n"
            "     \"lookat\": {\"mode\": \"center\"}}\n"
            "  ],\n"
            "  \"singles\": [\n"
            "    {\"position_id\": \"Position 3\", \"character\": \"CharC\", \"region\": \"神坛\",\n"
            "     \"neartarget\": \"中央锚点\", \"lookat\": \"center\"}\n"
            "  ]\n"
            "}"
        )
        user_payload = {
            "grouping": grouping,
            "scene_info": self.scene_info_json,
        }
        try:
            result = self.llm_client.complete_json(system_prompt, user_payload)
            result.setdefault("where", where)
            result.setdefault("groups", grouping.get("groups", []))
            result.setdefault("singles", grouping.get("singles", []))
            return result
        except Exception as exc:
            logger.warning("[Stage2][planning] LLM failed (%s), using fallback", exc)
            return self._fallback_planning(where, grouping)

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

    def _build_position_detail(self, where: str, planning: Dict) -> Dict:
        """
        Flatten position_plan into the per-position detail format expected by Stage 3.
        groups: one entry per position_id (flat, with group_id/character/layout/region/lookat)
        signals: singles list (matches position_plan.singles format)
        """
        flat_groups = []
        for group in planning.get("groups", []):
            group_id = group.get("group_id", "")
            layout = group.get("layout", "")
            region = group.get("region", "")
            lookat_obj = group.get("lookat", {})
            lookat_str = (
                lookat_obj.get("mode", "center")
                if isinstance(lookat_obj, dict)
                else str(lookat_obj)
            )
            for item in group.get("positions", []):
                if not isinstance(item, dict):
                    continue
                flat_groups.append({
                    "position_id": item.get("position_id", ""),
                    "group_id": group_id,
                    "region": region,
                    "character": item.get("character", ""),
                    "layout": layout,
                    "lookat": lookat_str,
                })

        signals = [
            {
                "position_id": s.get("position_id", ""),
                "character": s.get("character", ""),
                "region": s.get("region", ""),
                "neartarget": s.get("neartarget", ""),
                "lookat": s.get("lookat", ""),
            }
            for s in planning.get("singles", [])
            if isinstance(s, dict)
        ]

        return {"where": where, "groups": flat_groups, "signals": signals}

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
        for region in self.scene_info_json.get("regions", []):
            first_region = region.get("name", "")
            anchors = region.get("anchors", [])
            if anchors:
                first_anchor = anchors[0].get("name", "")
            break

        planned_groups = [
            {**g, "region": first_region, "neartarget": first_anchor,
             "lookat": {"mode": "center"}}
            for g in grouping.get("groups", [])
        ]
        planned_singles = [
            {**s, "region": first_region, "neartarget": first_anchor, "lookat": "center"}
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
