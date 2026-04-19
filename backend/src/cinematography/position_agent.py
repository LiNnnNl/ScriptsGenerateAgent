import argparse
import copy
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import OrderedDict, defaultdict
from itertools import combinations
from pathlib import Path

_THIS_FILE = Path(__file__).resolve()
for _candidate_root in (_THIS_FILE.parent, *_THIS_FILE.parents):
    if (_candidate_root / "position_detail_converter.py").exists():
        if str(_candidate_root) not in sys.path:
            sys.path.insert(0, str(_candidate_root))
        break

from position_detail_converter import PositionDetailConverter


class PositionAgent:
    API_URL = "https://api.deepseek.com/chat/completions"
    MODEL = "deepseek-chat"
    DEFAULT_TIMEOUT = 120
    DEFAULT_MAX_TOKENS = 4096
    DEFAULT_RETRIES = 3

    def __init__(
        self,
        script_json,
        scene_info_json,
        template_json,
        position_lib_json,
        api_key=None,
        api_url=None,
        model=None,
        output_dir=None,
        stage_output_dir=None,
    ):
        self.raw_script_json = self._load_json_like(script_json)
        self.raw_scene_info_json = self._load_json_like(scene_info_json)
        self.template_json = self._load_json_like(template_json)
        self.raw_position_lib_json = self._load_json_like(position_lib_json)

        self.script_json = self._normalize_script_json(self.raw_script_json)
        self.scene_info_json = self._normalize_scene_info_json(self.raw_scene_info_json)
        self.position_lib_json = self._normalize_position_lib_json(self.raw_position_lib_json)

        self.where = self.script_json["where"]
        self.scene_where = self.scene_info_json["where"]
        if self.where != self.scene_where:
            raise ValueError(
                "script_json.where must exactly match scene_info_json.where: "
                f"{self.where!r} != {self.scene_where!r}"
            )

        self.positions = self.script_json["positions"]
        self.position_order = [item["position_id"] for item in self.positions]
        self.position_map = OrderedDict((item["position_id"], copy.deepcopy(item)) for item in self.positions)
        self.timeline_analysis = self._build_timeline_analysis(self.raw_script_json)
        self.position_active_episode_map, self.position_visible_episode_map = self._index_timeline_episodes(self.timeline_analysis)
        self.required_dialogue_groups = self._build_required_dialogue_groups()

        self.region_names = [region["name"] for region in self.scene_info_json["regions"]]
        self.region_map = OrderedDict((region["name"], copy.deepcopy(region)) for region in self.scene_info_json["regions"])
        self.region_targets = OrderedDict()
        self.all_targets = []
        seen_targets = set()
        for region_name, region in self.region_map.items():
            targets = []
            for target in region["targets"]:
                name = target["name"]
                if name not in targets:
                    targets.append(name)
                if name not in seen_targets:
                    seen_targets.add(name)
                    self.all_targets.append(name)
            self.region_targets[region_name] = targets
        self.region_relationships = self._build_region_relationships()
        self.move_transitions = self._build_move_transitions()

        self.layouts = self.position_lib_json["layout_library"]
        self.layout_map = OrderedDict((layout["layout"], copy.deepcopy(layout)) for layout in self.layouts)
        self.max_layout_people = max((layout["max_people"] for layout in self.layouts), default=1)

        self.api_key = (api_key if api_key is not None else os.environ.get("DEEPSEEK_API_KEY", "")).strip()
        self.api_url = (api_url or self.API_URL).strip()
        self.model = (model or self.MODEL).strip()
        self.output_dir = Path(output_dir) if output_dir else Path("Assets") / "Json"
        self.stage_output_dir = Path(stage_output_dir) if stage_output_dir else self.output_dir / "AgentStage"
        if not self.api_url.startswith("http://") and not self.api_url.startswith("https://"):
            raise ValueError(
                "api_url must be a valid HTTP(S) endpoint. "
                "It looks like you may have put the API key into --api-url by mistake."
            )
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.stage_output_dir.mkdir(parents=True, exist_ok=True)
        self.stage1_result = None
        self.stage2_result = None
        self.final_plan = None

    def call_llm(self, prompt):
        if not self.api_key:
            raise RuntimeError("DEEPSEEK_API_KEY environment variable is required.")

        system_prompt = (
            "You are PositionAgent. "
            "Return a valid JSON object only. "
            "Follow every constraint exactly. "
            "Never invent characters, position_ids, regions, objects, layouts, or fields. "
            "If uncertain, prefer a conservative valid answer."
        )

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
            "top_p": 1,
            "stream": False,
            "max_tokens": self.DEFAULT_MAX_TOKENS,
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
        }

        last_error = None
        for attempt in range(1, self.DEFAULT_RETRIES + 1):
            try:
                request = urllib.request.Request(
                    self.api_url,
                    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": "Bearer " + self.api_key,
                    },
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=self.DEFAULT_TIMEOUT) as response:
                    response_payload = json.loads(response.read().decode("utf-8"))
                choice = response_payload["choices"][0]
                finish_reason = choice.get("finish_reason")
                message = choice.get("message", {})
                content = message.get("content")
                if not content or not str(content).strip():
                    raise RuntimeError("DeepSeek returned empty content.")
                if finish_reason == "length":
                    raise RuntimeError("DeepSeek response was truncated (finish_reason=length).")
                return self._parse_json_object(content)
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError, KeyError, RuntimeError) as exc:
                last_error = exc
                if attempt >= self.DEFAULT_RETRIES:
                    break
                time.sleep(min(2 ** (attempt - 1), 4))

        raise RuntimeError(f"DeepSeek API call failed after {self.DEFAULT_RETRIES} attempts: {last_error}")

    def stage1_grouping(self):
        print("[Director][Stage 2][Substage 1/3] grouping started.", flush=True)
        if not self.api_key:
            fallback_groups, fallback_singles = self._fallback_grouping_for_positions(self.position_order)
            for index, group in enumerate(fallback_groups, start=1):
                group["group_id"] = f"G{index}"
            self.stage1_result = {
                "groups": self._sort_groups_by_position_order(fallback_groups),
                "singles": self._sort_singles_by_position_order(fallback_singles),
            }
            self._save_stage_json(
                "position_stage1_grouping.json",
                {"status": "offline_fallback", "used_llm": False},
                self.stage1_result,
            )
            print("[Director][Stage 2][Substage 1/3] grouping finished with offline fallback.", flush=True)
            return copy.deepcopy(self.stage1_result)

        raw = self.call_llm(self._build_stage1_prompt())
        self.stage1_result = self._normalize_stage1_result(raw)
        self._save_stage_json("position_stage1_grouping.json", raw, self.stage1_result)
        print("[Director][Stage 2][Substage 1/3] grouping finished.", flush=True)
        return copy.deepcopy(self.stage1_result)

    def stage2_planning(self):
        if self.stage1_result is None:
            raise RuntimeError("stage1_grouping must run before stage2_planning.")
        print("[Director][Stage 2][Substage 2/3] planning started.", flush=True)

        if not self.api_key:
            groups = [self._sanitize_group_plan(group, {}) for group in self.stage1_result["groups"]]
            singles = [self._sanitize_single_plan(single, {}) for single in self.stage1_result["singles"]]
            groups, singles = self._enforce_move_region_continuity(groups, singles)
            self.stage2_result = {"groups": groups, "singles": singles}
            self._save_stage_json(
                "position_stage2_planning.json",
                {"status": "offline_fallback", "used_llm": False},
                self.stage2_result,
            )
            print("[Director][Stage 2][Substage 2/3] planning finished with offline fallback.", flush=True)
            return copy.deepcopy(self.stage2_result)

        raw = self.call_llm(self._build_stage2_prompt())
        raw = self._repair_stage2_raw_output(raw)
        self.stage2_result = self._normalize_stage2_result(raw)
        self._save_stage_json("position_stage2_planning.json", raw, self.stage2_result)
        print("[Director][Stage 2][Substage 2/3] planning finished.", flush=True)
        return copy.deepcopy(self.stage2_result)

    def stage3_compile(self):
        if self.stage1_result is None or self.stage2_result is None:
            raise RuntimeError("stage1_grouping and stage2_planning must run before stage3_compile.")
        print("[Director][Stage 2][Substage 3/3] compilation started.", flush=True)

        if not self.api_key:
            self.final_plan = self._deterministic_compile_plan()
            self._save_stage_json(
                "position_stage3_compilation.json",
                {"status": "offline_fallback", "used_llm": False},
                self.final_plan,
            )
            print("[Director][Stage 2][Substage 3/3] compilation finished with offline fallback.", flush=True)
            return copy.deepcopy(self.final_plan)

        try:
            raw = self.call_llm(self._build_stage3_prompt())
            self.final_plan = self._normalize_final_plan(raw)
            self._save_stage_json("position_stage3_compilation.json", raw, self.final_plan)
            print("[Director][Stage 2][Substage 3/3] compilation finished.", flush=True)
        except RuntimeError:
            self.final_plan = self._deterministic_compile_plan()
            self._save_stage_json(
                "position_stage3_compilation.json",
                {"status": "llm_failed_or_unavailable", "used_fallback": True},
                self.final_plan,
            )
            print("[Director][Stage 2][Substage 3/3] compilation fell back to deterministic mode.", flush=True)
        return copy.deepcopy(self.final_plan)

    def validate(self):
        if self.final_plan is None:
            raise RuntimeError("No final plan to validate.")

        plan = self.final_plan
        if not isinstance(plan, dict):
            raise ValueError("Final plan must be a JSON object.")
        if plan.get("where") != self.where:
            raise ValueError("where mismatch in final plan.")

        used_positions = []
        grouped_ids = set()
        single_ids = set()

        for group in plan.get("groups", []):
            if "reason" in group:
                raise ValueError("Final groups must not contain reason.")
            group_id = group.get("group_id")
            if not group_id:
                raise ValueError("Each group requires group_id.")
            region = group.get("region")
            if region not in self.region_map:
                raise ValueError(f"Invalid region in group {group_id}: {region!r}")
            layout = group.get("layout")
            if layout not in self.layout_map:
                raise ValueError(f"Invalid layout in group {group_id}: {layout!r}")
            positions = group.get("positions")
            if not isinstance(positions, list) or len(positions) < 2:
                raise ValueError(f"Group {group_id} must contain at least two positions.")
            if not self._layout_supports_size(layout, len(positions)):
                raise ValueError(f"Layout {layout!r} does not support group {group_id} size {len(positions)}.")

            seen_group_positions = set()
            seen_group_characters = set()
            valid_group_characters = []
            for item in positions:
                position_id = item.get("position_id")
                if position_id not in self.position_map:
                    raise ValueError(f"Invalid position_id in group {group_id}: {position_id!r}")
                if position_id in seen_group_positions:
                    raise ValueError(f"Duplicate position_id in group {group_id}: {position_id!r}")
                expected_character = self.position_map[position_id]["character"]
                if item.get("character") != expected_character:
                    raise ValueError(
                        f"Character mismatch for {position_id!r} in group {group_id}: "
                        f"{item.get('character')!r} != {expected_character!r}"
                    )
                if expected_character in seen_group_characters:
                    raise ValueError(f"Duplicate character in group {group_id}: {expected_character!r}")
                seen_group_positions.add(position_id)
                seen_group_characters.add(expected_character)
                valid_group_characters.append(expected_character)
                used_positions.append(position_id)
                grouped_ids.add(position_id)

            lookat = group.get("lookat")
            if not isinstance(lookat, dict):
                raise ValueError(f"Group {group_id} lookat must be an object.")
            mode = lookat.get("mode")
            if mode not in ("center", "target"):
                raise ValueError(f"Invalid group lookat mode in {group_id}: {mode!r}")
            if mode == "target":
                target_character = lookat.get("target_character")
                target_object = lookat.get("target_object")
                has_target_character = target_character in valid_group_characters
                has_target_object = isinstance(target_object, str) and target_object.strip() in self.all_targets
                if has_target_character == has_target_object:
                    raise ValueError(
                        f"Group {group_id} target lookat must specify exactly one valid target_character or target_object."
                    )

        for single in plan.get("singles", []):
            if "reason" in single:
                raise ValueError("Final singles must not contain reason.")
            position_id = single.get("position_id")
            if position_id not in self.position_map:
                raise ValueError(f"Invalid single position_id: {position_id!r}")
            expected_character = self.position_map[position_id]["character"]
            if single.get("character") != expected_character:
                raise ValueError(
                    f"Character mismatch for single {position_id!r}: "
                    f"{single.get('character')!r} != {expected_character!r}"
                )
            region = single.get("region")
            if region not in self.region_map:
                raise ValueError(f"Invalid region for single {position_id!r}: {region!r}")
            neartarget = single.get("neartarget")
            if neartarget not in self.region_targets.get(region, []):
                raise ValueError(f"Invalid neartarget for single {position_id!r}: {neartarget!r}")
            lookat = single.get("lookat")
            if not isinstance(lookat, str) or not lookat.strip():
                raise ValueError(f"Invalid lookat for single {position_id!r}: {lookat!r}")
            if lookat not in self.all_targets:
                raise ValueError(f"Single lookat must come from scene_info_json targets: {lookat!r}")
            used_positions.append(position_id)
            single_ids.add(position_id)

        if grouped_ids & single_ids:
            raise ValueError("A position_id cannot appear in both groups and singles.")

        expected_positions = list(self.position_map.keys())
        if sorted(used_positions) != sorted(expected_positions):
            raise ValueError(
                "Final plan must use every position_id exactly once. "
                f"Expected {expected_positions!r}, got {used_positions!r}"
            )
        if len(used_positions) != len(expected_positions):
            raise ValueError("Final plan contains duplicate position_ids.")
        self._validate_move_region_continuity(plan)
        return True

    def run(self):
        self.stage1_grouping()
        self.stage2_planning()
        self.stage3_compile()
        self.validate()
        plan_root_path = Path("position_plan.json")
        plan_output_path = self.output_dir / "position_plan.json"
        self._write_json_file(plan_root_path, self.final_plan)
        self._write_json_file(plan_output_path, self.final_plan)
        PositionDetailConverter(self.final_plan, Path("position_detail.json")).run()
        PositionDetailConverter(self.final_plan, self.output_dir / "position_detail.json").run()
        return copy.deepcopy(self.final_plan)

    def _load_json_like(self, value):
        if isinstance(value, (dict, list)):
            return copy.deepcopy(value)
        if isinstance(value, Path):
            return self._read_json_file(value)
        if isinstance(value, str):
            possible_path = Path(value)
            if possible_path.exists():
                return self._read_json_file(possible_path)
            try:
                return json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError(f"String input is neither an existing path nor valid JSON: {value!r}") from exc
        raise TypeError(f"Unsupported JSON input type: {type(value).__name__}")

    def _read_json_file(self, path):
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))

    def _parse_json_object(self, content):
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", content, flags=re.DOTALL)
            if not match:
                raise ValueError("DeepSeek response did not contain a valid JSON object.")
            parsed = json.loads(match.group(0))
        if not isinstance(parsed, dict):
            raise ValueError("DeepSeek response must be a JSON object.")
        return parsed

    def _write_json_file(self, path, payload):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _save_stage_json(self, filename, raw_payload, normalized_payload):
        self._write_json_file(
            self.stage_output_dir / filename,
            {
                "where": self.where,
                "raw_llm_output": raw_payload,
                "normalized_output": normalized_payload,
            },
        )

    def _normalize_script_json(self, payload):
        if isinstance(payload, dict) and isinstance(payload.get("positions"), list):
            where = self._require_non_empty_string(payload.get("where"), "script_json.where")
            positions = []
            seen_position_ids = set()
            for index, item in enumerate(payload["positions"], start=1):
                if not isinstance(item, dict):
                    raise ValueError(f"script_json.positions[{index}] must be an object.")
                position_id = self._extract_position_id(item, f"script_json.positions[{index}]")
                character = self._require_non_empty_string(item.get("character"), f"script_json.positions[{index}].character")
                if position_id in seen_position_ids:
                    raise ValueError(f"Duplicate position_id in script_json: {position_id!r}")
                seen_position_ids.add(position_id)
                positions.append(
                    {
                        "position_id": position_id,
                        "character": character,
                        "context": self._compact_text(self._build_standard_position_context(item)),
                        "raw": copy.deepcopy(item),
                    }
                )
            if not positions:
                raise ValueError("script_json.positions must not be empty.")
            return {"where": where, "positions": positions}

        timeline_root = payload
        if isinstance(payload, list):
            if not payload:
                raise ValueError("Timeline script_json list must not be empty.")
            timeline_root = payload[0]
        if not isinstance(timeline_root, dict):
            raise ValueError("Unsupported script_json format.")

        scene_information = timeline_root.get("scene information", {})
        where = self._require_non_empty_string(
            timeline_root.get("where") or scene_information.get("where"),
            "script_json.where",
        )

        position_character = OrderedDict()
        position_contexts = defaultdict(list)
        for item in timeline_root.get("initial position", []):
            if not isinstance(item, dict):
                continue
            character = self._require_non_empty_string(item.get("character"), "initial position.character")
            position_id = self._extract_position_id(item, "initial position")
            self._record_position_binding(position_character, position_id, character)
            position_contexts[position_id].append(f"initial position for {character}")

        for beat in timeline_root.get("scene", []):
            if not isinstance(beat, dict):
                continue
            beat_context = self._build_timeline_beat_context(beat)
            for current in beat.get("current position", []):
                if not isinstance(current, dict):
                    continue
                character = self._require_non_empty_string(current.get("character"), "current position.character")
                position_id = self._extract_position_id(current, "current position")
                self._record_position_binding(position_character, position_id, character)
                position_contexts[position_id].append(beat_context)
            for movement in beat.get("move", []):
                if not isinstance(movement, dict):
                    continue
                character = self._require_non_empty_string(movement.get("character"), "move.character")
                position_id = self._extract_position_id(movement, "move.destination")
                self._record_position_binding(position_character, position_id, character)
                position_contexts[position_id].append(beat_context)
                position_contexts[position_id].append(f"{character} moves to {position_id}")

        if not position_character:
            raise ValueError("Could not extract any positions from script_json.")

        positions = []
        for position_id, character in position_character.items():
            context = self._compact_text(" | ".join(position_contexts.get(position_id, [])))
            positions.append(
                {
                    "position_id": position_id,
                    "character": character,
                    "context": context,
                    "raw": {"source": "timeline"},
                }
            )
        return {"where": where, "positions": positions}

    def _normalize_scene_info_json(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("scene_info_json must be an object.")
        scene_where = payload.get("where")
        if not isinstance(scene_where, str) or not scene_where.strip():
            scene_where = payload.get("scene_name")
        scene_where = self._require_non_empty_string(scene_where, "scene_info_json.where")
        regions_payload = payload.get("regions")
        if not isinstance(regions_payload, list) or not regions_payload:
            raise ValueError("scene_info_json.regions must be a non-empty list.")

        regions = []
        seen_region_names = set()
        pending_relation_targets = []
        for index, region in enumerate(regions_payload, start=1):
            if not isinstance(region, dict):
                raise ValueError(f"scene_info_json.regions[{index}] must be an object.")
            region_name = self._require_non_empty_string(region.get("name"), f"scene_info_json.regions[{index}].name")
            if region_name in seen_region_names:
                raise ValueError(f"Duplicate region name: {region_name!r}")
            seen_region_names.add(region_name)
            region_description = self._compact_text(
                self._stringify_context_value(region.get("description")),
                500,
            )

            raw_anchors = region.get("anchors")
            anchor_names = []
            if isinstance(raw_anchors, list):
                for anchor in raw_anchors:
                    if not isinstance(anchor, dict):
                        continue
                    anchor_name = anchor.get("name")
                    if not isinstance(anchor_name, str) or not anchor_name.strip():
                        continue
                    anchor_name = anchor_name.strip()
                    if anchor_name not in anchor_names:
                        anchor_names.append(anchor_name)
            anchor_count = len(anchor_names)

            raw_scene_markers = region.get("scene_markers")
            scene_marker_names = []
            if isinstance(raw_scene_markers, list):
                for marker in raw_scene_markers:
                    if not isinstance(marker, dict):
                        continue
                    marker_name = marker.get("name")
                    if not isinstance(marker_name, str) or not marker_name.strip():
                        continue
                    marker_name = marker_name.strip()
                    if marker_name not in scene_marker_names:
                        scene_marker_names.append(marker_name)

            raw_targets = []
            for key in ("scene_markers", "objects", "semantic_anchors", "anchors"):
                value = region.get(key)
                if isinstance(value, list):
                    raw_targets.extend(value)

            targets = []
            seen_targets = set()
            for item in raw_targets:
                if not isinstance(item, dict):
                    continue
                name = item.get("name")
                if not isinstance(name, str) or not name.strip():
                    continue
                name = name.strip()
                if name in seen_targets:
                    continue
                seen_targets.add(name)
                targets.append({"name": name})

            raw_spatial_relations = region.get("spatial_relations")
            spatial_relations = []
            seen_relation_regions = set()
            if isinstance(raw_spatial_relations, list):
                for relation_index, relation in enumerate(raw_spatial_relations, start=1):
                    if not isinstance(relation, dict):
                        continue
                    target_region = relation.get("region")
                    if not isinstance(target_region, str) or not target_region.strip():
                        continue
                    target_region = target_region.strip()
                    if target_region in seen_relation_regions:
                        continue
                    seen_relation_regions.add(target_region)

                    relation_label = self._normalize_region_relation_label(relation.get("relation"))
                    connected = relation.get("connected")
                    connected = bool(connected) if isinstance(connected, bool) else None

                    distance_m = relation.get("distance_m")
                    try:
                        distance_m = int(distance_m) if distance_m is not None else None
                    except (TypeError, ValueError):
                        distance_m = None

                    if relation_label == "unknown" and distance_m is not None:
                        relation_label = self._relation_from_distance(distance_m)
                    if relation_label == "unknown" and connected:
                        relation_label = "connected"

                    spatial_relations.append(
                        {
                            "region": target_region,
                            "relation": relation_label,
                            "connected": connected,
                            "distance_m": distance_m,
                        }
                    )
                    pending_relation_targets.append((region_name, target_region, relation_index))

            if not targets:
                raise ValueError(f"Region {region_name!r} must expose at least one target object or anchor.")
            regions.append(
                {
                    "name": region_name,
                    "description": region_description,
                    "spatial_relations": spatial_relations,
                    "targets": targets,
                    "anchor_count": anchor_count,
                    "anchor_names": anchor_names,
                    "scene_marker_count": len(scene_marker_names),
                    "scene_marker_names": scene_marker_names,
                    "target_count": len(targets),
                }
            )

        for source_region, target_region, relation_index in pending_relation_targets:
            if target_region not in seen_region_names:
                raise ValueError(
                    f"scene_info_json.regions[{source_region!r}].spatial_relations[{relation_index}] "
                    f"references unknown region {target_region!r}."
                )

        return {"where": scene_where, "regions": regions}

    def _normalize_region_relation_label(self, value):
        if not isinstance(value, str) or not value.strip():
            return "unknown"
        lowered = value.strip().lower()
        alias_map = {
            "same": "same",
            "adjacent": "adjacent",
            "adjoining": "adjacent",
            "neighbor": "adjacent",
            "neighbour": "adjacent",
            "near": "near",
            "close": "near",
            "connected": "connected",
            "link": "connected",
            "linked": "connected",
            "medium": "medium",
            "mid": "medium",
            "moderate": "medium",
            "far": "far",
            "distant": "far",
            "\u76f8\u90bb": "adjacent",
            "\u7d27\u90bb": "adjacent",
            "\u6bd7\u90bb": "adjacent",
            "\u8f83\u8fd1": "near",
            "\u5f88\u8fd1": "near",
            "\u6781\u8fd1": "near",
            "\u76f8\u8fde": "connected",
            "\u76f4\u63a5\u8fde\u901a": "connected",
            "\u8fde\u901a": "connected",
            "\u8fde\u63a5": "connected",
            "\u9002\u4e2d": "medium",
            "\u4e2d\u7b49": "medium",
            "\u8f83\u8fdc": "far",
            "\u5f88\u8fdc": "far",
            "\u6700\u8fdc": "far",
            "\u9065\u8fdc": "far",
        }
        return alias_map.get(lowered, "unknown")

    def _relation_from_distance(self, distance_m):
        try:
            distance = int(distance_m)
        except (TypeError, ValueError):
            return "unknown"
        if distance >= 65:
            return "far"
        if distance <= 35:
            return "near"
        if distance <= 55:
            return "medium"
        return "unknown"

    def _normalize_position_lib_json(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("position_lib_json must be an object.")
        layouts_payload = payload.get("layout_library")
        if not isinstance(layouts_payload, list) or not layouts_payload:
            raise ValueError("position_lib_json.layout_library must be a non-empty list.")

        layouts = []
        seen_layouts = set()
        for index, layout in enumerate(layouts_payload, start=1):
            if not isinstance(layout, dict):
                raise ValueError(f"layout_library[{index}] must be an object.")
            name = self._require_non_empty_string(layout.get("layout"), f"layout_library[{index}].layout")
            if name in seen_layouts:
                raise ValueError(f"Duplicate layout name: {name!r}")
            seen_layouts.add(name)
            try:
                min_people = int(layout.get("min_people"))
                max_people = int(layout.get("max_people"))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"layout {name!r} requires integer min_people and max_people.") from exc
            if min_people < 2 or max_people < min_people:
                raise ValueError(f"Invalid layout size range for {name!r}: {min_people}..{max_people}")
            layouts.append(
                {
                    "layout": name,
                    "min_people": min_people,
                    "max_people": max_people,
                    "description": str(layout.get("description", "")),
                    "use_case": copy.deepcopy(layout.get("use_case", [])),
                }
            )
        return {"layout_library": layouts}

    def _extract_timeline_root(self, payload):
        timeline_root = payload
        if isinstance(payload, list):
            if not payload:
                return None
            timeline_root = payload[0]
        if not isinstance(timeline_root, dict):
            return None
        if not isinstance(timeline_root.get("scene"), list):
            return None
        return timeline_root

    def _build_timeline_analysis(self, payload):
        timeline_root = self._extract_timeline_root(payload)
        if timeline_root is None:
            return None

        current_positions = OrderedDict()
        for item in timeline_root.get("initial position", []):
            if not isinstance(item, dict):
                continue
            character = item.get("character")
            position_id = item.get("position") or item.get("position_id")
            if not isinstance(character, str) or not character.strip():
                continue
            if not isinstance(position_id, str) or not position_id.strip():
                continue
            current_positions[character.strip()] = position_id.strip()

        episodes = []
        episode_index = 1
        current_episode = self._new_timeline_episode(episode_index, "initial", current_positions)

        for beat_index, beat in enumerate(timeline_root.get("scene", []), start=1):
            if not isinstance(beat, dict):
                continue

            move_entries = self._parse_timeline_moves(beat)
            if move_entries:
                if current_episode is not None and (current_episode["dialogue_flow"] or current_episode["positions_map"]):
                    episodes.append(self._finalize_timeline_episode(current_episode))
                    episode_index += 1

                moved_characters = set()
                for move in move_entries:
                    current_positions[move["character"]] = move["position_id"]
                    moved_characters.add(move["character"])

                for current in beat.get("current position", []):
                    if not isinstance(current, dict):
                        continue
                    character = current.get("character")
                    position_id = current.get("position") or current.get("position_id")
                    if not isinstance(character, str) or not character.strip():
                        continue
                    if not isinstance(position_id, str) or not position_id.strip():
                        continue
                    character = character.strip()
                    if character in moved_characters:
                        continue
                    current_positions[character] = position_id.strip()

                current_episode = self._new_timeline_episode(
                    episode_index,
                    "move: " + ", ".join(f"{item['character']}->{item['position_id']}" for item in move_entries),
                    current_positions,
                )
            else:
                for current in beat.get("current position", []):
                    if not isinstance(current, dict):
                        continue
                    character = current.get("character")
                    position_id = current.get("position") or current.get("position_id")
                    if not isinstance(character, str) or not character.strip():
                        continue
                    if not isinstance(position_id, str) or not position_id.strip():
                        continue
                    current_positions[character.strip()] = position_id.strip()

            if current_episode is None:
                current_episode = self._new_timeline_episode(episode_index, "initial", current_positions)

            active_characters = OrderedDict()
            speaker = beat.get("speaker")
            if isinstance(speaker, str) and speaker.strip():
                active_characters[speaker.strip()] = True

            for action in beat.get("actions", []) if isinstance(beat.get("actions"), list) else []:
                if not isinstance(action, dict):
                    continue
                action_character = action.get("character")
                if isinstance(action_character, str) and action_character.strip():
                    active_characters[action_character.strip()] = True

            content = self._stringify_context_value(beat.get("content"))
            shot_description = self._compact_text(self._stringify_context_value(beat.get("shot_description")), 240)
            for character in list(current_positions.keys()):
                if content and character in content:
                    active_characters[character] = True

            beat_positions = []
            for character, position_id in current_positions.items():
                beat_positions.append({"position_id": position_id, "character": character})
                current_episode["positions_map"][position_id] = character

            for character in active_characters:
                position_id = current_positions.get(character)
                if position_id:
                    current_episode["active_positions_map"][position_id] = character

            if speaker or content or active_characters:
                current_episode["dialogue_flow"].append(
                    {
                        "beat_index": beat_index,
                        "speaker": speaker.strip() if isinstance(speaker, str) and speaker.strip() else "",
                        "speaker_position": current_positions.get(speaker.strip(), "") if isinstance(speaker, str) and speaker.strip() else "",
                        "content": self._compact_text(content, 280),
                        "shot_description": shot_description,
                        "active_positions": [
                            {"position_id": current_positions[character], "character": character}
                            for character in active_characters
                            if character in current_positions
                        ],
                        "current_positions": beat_positions,
                    }
                )

        if current_episode is not None and (current_episode["dialogue_flow"] or current_episode["positions_map"]):
            episodes.append(self._finalize_timeline_episode(current_episode))

        return {"episodes": episodes}

    def _new_timeline_episode(self, episode_index, trigger, current_positions):
        episode = {
            "episode_id": f"E{episode_index}",
            "trigger": trigger,
            "positions_map": OrderedDict(),
            "active_positions_map": OrderedDict(),
            "dialogue_flow": [],
        }
        for character, position_id in current_positions.items():
            episode["positions_map"][position_id] = character
        return episode

    def _finalize_timeline_episode(self, episode):
        positions = [
            {"position_id": position_id, "character": character}
            for position_id, character in episode["positions_map"].items()
        ]
        active_positions = [
            {"position_id": position_id, "character": character}
            for position_id, character in episode["active_positions_map"].items()
        ]
        active_ids = {item["position_id"] for item in active_positions}
        passive_positions = [item for item in positions if item["position_id"] not in active_ids]
        return {
            "episode_id": episode["episode_id"],
            "trigger": episode["trigger"],
            "positions": positions,
            "active_positions": active_positions,
            "passive_positions": passive_positions,
            "dialogue_flow": episode["dialogue_flow"],
        }

    def _parse_timeline_moves(self, beat):
        moves = []
        for move in beat.get("move", []) if isinstance(beat.get("move"), list) else []:
            if not isinstance(move, dict):
                continue
            character = move.get("character")
            position_id = move.get("destination") or move.get("position") or move.get("position_id")
            if not isinstance(character, str) or not character.strip():
                continue
            if not isinstance(position_id, str) or not position_id.strip():
                continue
            moves.append({"character": character.strip(), "position_id": position_id.strip()})
        return moves

    def _index_timeline_episodes(self, timeline_analysis):
        active_map = defaultdict(set)
        visible_map = defaultdict(set)
        if not timeline_analysis:
            return active_map, visible_map

        for episode in timeline_analysis.get("episodes", []):
            episode_id = episode.get("episode_id")
            if not episode_id:
                continue
            for item in episode.get("positions", []):
                position_id = item.get("position_id")
                if position_id in self.position_map:
                    visible_map[position_id].add(episode_id)
            for item in episode.get("active_positions", []):
                position_id = item.get("position_id")
                if position_id in self.position_map:
                    active_map[position_id].add(episode_id)
        return active_map, visible_map

    def _build_required_dialogue_groups(self):
        if not self.timeline_analysis:
            return []

        candidate_groups = []
        for episode in self.timeline_analysis.get("episodes", []):
            episode_id = episode.get("episode_id")
            if not episode_id:
                continue

            speaker_positions = []
            seen_positions = set()
            seen_characters = set()
            for line in episode.get("dialogue_flow", []):
                position_id = line.get("speaker_position")
                if position_id not in self.position_map:
                    continue
                character = self.position_map[position_id]["character"]
                if position_id in seen_positions or character in seen_characters:
                    continue
                speaker_positions.append(position_id)
                seen_positions.add(position_id)
                seen_characters.add(character)

            if len(speaker_positions) < 2:
                continue

            candidate_groups.append(
                {
                    "episode_id": episode_id,
                    "trigger": episode.get("trigger", ""),
                    "positions": speaker_positions,
                }
            )

        candidate_groups.sort(
            key=lambda item: (
                -len(item["positions"]),
                int(str(item["episode_id"]).lstrip("E") or "0"),
            )
        )

        required_groups = []
        used_positions = set()
        for candidate in candidate_groups:
            ordered_positions = [position_id for position_id in candidate["positions"] if position_id not in used_positions]
            if len(ordered_positions) < 2:
                continue
            required_groups.append(
                {
                    "episode_id": candidate["episode_id"],
                    "trigger": candidate["trigger"],
                    "positions": [self._position_ref(position_id) for position_id in ordered_positions],
                    "reason": "These positions speak within the same dialogue episode and must stay in one group.",
                }
            )
            used_positions.update(ordered_positions)

        return required_groups

    def _build_standard_position_context(self, item):
        context_parts = []
        ordered_keys = [
            "dialogue",
            "dialogue_context",
            "action",
            "action_context",
            "shot_description",
            "context",
            "description",
            "note",
            "notes",
            "interaction",
            "interaction_context",
            "emotion",
            "intent",
            "semantic_target",
        ]
        for key in ordered_keys:
            if key not in item:
                continue
            rendered = self._stringify_context_value(item.get(key))
            if rendered:
                context_parts.append(f"{key}: {rendered}")
        for key, value in item.items():
            if key in {"position_id", "position", "id", "character"} or key in ordered_keys:
                continue
            rendered = self._stringify_context_value(value)
            if rendered:
                context_parts.append(f"{key}: {rendered}")
        return " | ".join(context_parts)

    def _build_timeline_beat_context(self, beat):
        parts = []
        speaker = beat.get("speaker")
        content = beat.get("content")
        if speaker and content:
            parts.append(f"speaker={speaker}; content={content}")
        elif speaker:
            parts.append(f"speaker={speaker}")

        for key in ("shot", "shot_type", "shot_blend", "shot_description", "motion_description"):
            rendered = self._stringify_context_value(beat.get(key))
            if rendered:
                parts.append(f"{key}={rendered}")

        actions = beat.get("actions")
        if isinstance(actions, list):
            action_fragments = []
            for action in actions:
                if not isinstance(action, dict):
                    continue
                character = self._stringify_context_value(action.get("character"))
                state = self._stringify_context_value(action.get("state"))
                name = self._stringify_context_value(action.get("action"))
                detail = self._stringify_context_value(action.get("motion_detail"))
                segment = ":".join(part for part in (character, state, name, detail) if part)
                if segment:
                    action_fragments.append(segment)
            if action_fragments:
                parts.append("actions=" + "; ".join(action_fragments))

        moves = beat.get("move")
        if isinstance(moves, list):
            move_fragments = []
            for move in moves:
                if not isinstance(move, dict):
                    continue
                character = self._stringify_context_value(move.get("character"))
                destination = self._stringify_context_value(move.get("destination"))
                if character and destination:
                    move_fragments.append(f"{character}->{destination}")
            if move_fragments:
                parts.append("moves=" + "; ".join(move_fragments))
        return self._compact_text(" | ".join(parts))

    def _record_position_binding(self, position_character, position_id, character):
        existing = position_character.get(position_id)
        if existing is None:
            position_character[position_id] = character
            return
        if existing != character:
            raise ValueError(
                f"position_id {position_id!r} is bound to multiple characters: "
                f"{existing!r} and {character!r}"
            )

    def _extract_position_id(self, item, label):
        value = item.get("position_id")
        if value is None:
            value = item.get("position")
        if value is None:
            value = item.get("destination")
        if value is None:
            value = item.get("id")
        return self._require_non_empty_string(value, f"{label}.position_id")

    def _stringify_context_value(self, value):
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, (int, float, bool)):
            return str(value)
        if isinstance(value, list):
            rendered_parts = [self._stringify_context_value(item) for item in value]
            rendered_parts = [item for item in rendered_parts if item]
            return ", ".join(rendered_parts)
        if isinstance(value, dict):
            fragments = []
            for key, item in value.items():
                rendered = self._stringify_context_value(item)
                if rendered:
                    fragments.append(f"{key}={rendered}")
            return ", ".join(fragments)
        return str(value).strip()

    def _compact_text(self, text, max_length=1600):
        if not isinstance(text, str):
            return ""
        compacted = re.sub(r"\s+", " ", text).strip()
        if len(compacted) <= max_length:
            return compacted
        return compacted[: max_length - 3].rstrip() + "..."

    def _require_non_empty_string(self, value, label):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} must be a non-empty string.")
        return value.strip()

    def _build_stage1_prompt(self):
        standardized_script = {
            "where": self.where,
            "positions": [
                {
                    "position_id": item["position_id"],
                    "character": item["character"],
                    "context": item.get("context", ""),
                }
                for item in self.positions
            ],
        }
        derived_hints = self._build_stage1_hints()
        prompt = {
            "task": "stage1_grouping",
            "must_output_json": True,
            "IMPORTANT": [
                "You MUST NOT group all characters in the same scene together.",
                "You MUST detect interaction pairs (who talks to whom).",
                "You MUST detect isolated characters.",
                "You MUST split groups when a move action happens.",
                "You MUST prefer smaller groups over larger groups unless all characters are actively interacting.",
                "If positions are active participants in the same dialogue interaction, they MUST be placed in the same group.",
                "You MUST use shot_description as an additional hard hint for shot composition, grouping, and region choice.",
            ],
            "instructions": [
                "Group screenplay positions into multi-character groups and singles.",
                "Use only the provided position_id values and their exact matching characters.",
                "Do not invent or rename characters or position_ids.",
                "Every position_id must appear exactly once across groups and singles.",
                "Prefer singles when interaction evidence is weak.",
                f"A group must contain at least 2 positions, 2 distinct characters, and at most {self.max_layout_people} positions.",
                "Group direct dialogue or obvious interaction together.",
                "Read the full dialogue chronology, not only the merged per-position summary.",
                "Read shot_description carefully. It may explicitly describe who is framed together, who is foreground/background, and which area the shot is built around.",
                "Use beat order, speaker order, current positions, and move transitions together.",
                "A move action creates a new blocking episode, so moved positions should usually be regrouped separately from earlier positions.",
                "An unmoved character can still belong to the new post-move group if later dialogue beats show that character actively talking with the moved characters in the same episode.",
                "Any positions listed under must_group_dialogue_positions are hard constraints: they must end up in the same group.",
                "If shot_description says multiple characters share the same shot composition or are framed in one interaction, strongly prefer putting them in one group.",
                "If shot_description indicates separation, edge framing, background observer, or isolated composition, use that as evidence for singles or smaller groups.",
                "Do not split a dialogue chain across a group and a single when the characters are still speaking to each other in the same episode.",
                "Do not group a character into a larger cluster only because the character appears in the same scene or current-position list.",
                "When only one pair is clearly interacting and another character is merely present, output one small group plus one single.",
                "For groups of 3 or more, keep the whole group together only if all members are actively interacting with each other in the same exchange.",
                "If multiple outputs are plausible, prefer the one with smaller groups.",
            ],
            "script_json": standardized_script,
            "derived_hints": derived_hints,
            "timeline_dialogue_analysis": self.timeline_analysis,
            "must_group_dialogue_positions": self.required_dialogue_groups,
            "decision_checklist": [
                "Step 1: identify move-created positions and treat them as new grouping candidates.",
                "Step 2: identify the strongest interaction pairs from dialogue, reply, response, and directed action evidence.",
                "Step 2.5: use the full dialogue timeline to decide which positions are active in the same post-move episode.",
                "Step 2.55: use shot_description to validate whether the same characters are visually composed together or separated.",
                "Step 2.6: enforce must_group_dialogue_positions before finalizing output.",
                "Step 3: keep isolated or weakly connected characters as singles.",
                "Step 4: only output a large group when every member is actively interacting.",
            ],
            "output_schema": {
                "groups": [
                    {
                        "group_id": "G1",
                        "positions": [
                            {"position_id": "P1", "character": "A"},
                            {"position_id": "P2", "character": "B"},
                        ],
                        "reason": "Brief reason",
                    }
                ],
                "singles": [
                    {
                        "position_id": "P3",
                        "character": "C",
                        "reason": "Brief reason",
                    }
                ],
            },
        }
        return json.dumps(prompt, ensure_ascii=False, indent=2)

    def _build_stage2_prompt(self):
        compact_scene = {
            "where": self.where,
            "regions": [
                {
                    "name": region["name"],
                    "description": region.get("description", ""),
                    "spatial_relations": copy.deepcopy(region.get("spatial_relations", [])),
                    "anchor_count": region.get("anchor_count", 0),
                    "anchor_names": list(region.get("anchor_names", [])),
                    "scene_marker_count": region.get("scene_marker_count", 0),
                    "target_count": region.get("target_count", len(region["targets"])),
                    "targets": [target["name"] for target in region["targets"]],
                }
                for region in self.scene_info_json["regions"]
            ],
            "region_relationships": self._summarize_region_relationships(),
        }
        compact_layouts = [
            {
                "layout": layout["layout"],
                "min_people": layout["min_people"],
                "max_people": layout["max_people"],
                "description": layout["description"],
            }
            for layout in self.layouts
        ]
        group_contexts = []
        for group in self.stage1_result["groups"]:
            group_contexts.append(
                {
                    "group_id": group["group_id"],
                    "positions": [
                        {
                            "position_id": item["position_id"],
                            "character": item["character"],
                            "context": self.position_map[item["position_id"]].get("context", ""),
                        }
                        for item in group["positions"]
                    ],
                    "merged_context": self._group_context(group["positions"]),
                    "active_episode_ids": self._collect_position_episode_ids(
                        [item["position_id"] for item in group["positions"]],
                        active_only=True,
                    ),
                    "visible_episode_ids": self._collect_position_episode_ids(
                        [item["position_id"] for item in group["positions"]],
                        active_only=False,
                    ),
                }
            )
        single_contexts = []
        for single in self.stage1_result["singles"]:
            position_id = single["position_id"]
            single_contexts.append(
                {
                    "position_id": position_id,
                    "character": single["character"],
                    "context": self.position_map[position_id].get("context", ""),
                    "active_episode_ids": self._collect_position_episode_ids([position_id], active_only=True),
                    "visible_episode_ids": self._collect_position_episode_ids([position_id], active_only=False),
                }
            )
        prompt = {
            "task": "stage2_planning",
            "must_output_json": True,
            "instructions": [
                "Plan regions, layouts, and lookat values for every group and single.",
                "Use only regions and targets from scene_info_json.",
                "Use only layouts from position_lib_json.",
                "Use shot_description and dialogue context as region-selection hints when they describe shot composition or spatial focus.",
                "If scene_info_json.regions[*].spatial_relations is present, treat it as the primary source of truth for region adjacency, connectivity, and travel distance.",
                "Region selection priority is: 1) move_position_links and spatial_relations, 2) geographic continuity across connected beats, 3) anchor_count / staging capacity, 4) region.description as a semantic reference, 5) shot_description and dialogue focus as supporting composition hints.",
                "Do not choose a region mainly because its description sounds semantically perfect if another same / adjacent / near / medium region is more spatially plausible for the current move and blocking continuity.",
                "Use region.description after spatial plausibility, continuity, and anchor_count are satisfied, but before shot_description and dialogue semantics.",
                "You MUST consider each region's anchor_count when planning regions. Regions with more anchors can support more staging flexibility; regions with only one anchor should be used more conservatively unless the script strongly requires them.",
                "You MUST consider the relationships between regions described in scene_info_json, with spatial_relations taking priority over any natural-language cues inside region.description.",
                "Any move-linked source/destination pair is invalid if the direct spatial_relations label between the source region and destination region is far.",
                "For any move transition, the source position and destination position MUST map to the same region, or to adjacent / near / medium / directly connected regions only.",
                "You MUST NOT choose a source region and destination region that are labeled as far apart in scene_info_json as the two ends of a move.",
                "connected does not mean adjacent or near. If the direct spatial_relations label between the source region and destination region is far, that move is invalid even if connected=true.",
                "move_position_links lists the exact source_position_id and destination_position_id pairs parsed from script_json move beats. Treat each pair as a hard planning constraint when selecting regions.",
                "move_group_links lists any group or single in stage1 that receives moved destination positions. For any move-linked group, you must consider every before/after position change inside that group. If any moved member's source region to the candidate destination region is far, that candidate region is invalid for the whole owner.",
                "For example, if G2 contains moved destinations from G1, then G1 region and G2 region must not be far. A move-linked group can never use a far destination region.",
                "When checking move legality, use only the direct source-region -> candidate-destination-region relation for the moved positions. Do not justify a move destination by referencing a third region that happens to be adjacent or semantically suitable.",
                "The final region field and the final reason must match exactly. Before returning, re-check that the region field is the actually selected legal region.",
                "Do not leave a rejected draft candidate in the region field after revising your reasoning.",
                "The reason must justify only the final selected region. Do not narrate a chain of rejected candidates such as 'A is invalid, so choose B' while still returning region=A.",
                "If you revise your candidate during reasoning, update the region field, layout field, and reason so they all point to the same final choice.",
                "If two positions belong to the same character before and after a move, preserve geographic continuity when selecting their regions.",
                "However, if a moved destination becomes an isolated single who is explicitly leaving the scene, withdrawing from the main interaction, or remaining only as a distant background observer, you may choose an adjacent or medium-distance region that better fits that exit / withdrawal composition.",
                "For exit-like singles, prefer plausibly reachable secluded, edge, hideaway, or background-supporting regions over forcing them back into the same interaction region, but still keep the destination within adjacent or medium-distance reach. Never use a far-apart region pair for that move.",
                "If multiple moved positions remain grouped because they are jointly leaving, dispersing, withdrawing, or transitioning together, you may also choose an adjacent or medium-distance retreat region for that whole group when the shot_description and dialogue continuity support it.",
                "Do not force same-region continuity when the screenplay clearly depicts a coordinated departure or regroup-away movement. Instead, choose the most spatially plausible adjacent or medium region from scene_info_json, but never a far region.",
                "Group lookat.mode must be 'center' or 'target'.",
                "When group lookat.mode is 'target', provide exactly one of: target_character or target_object.",
                "If target_character is used, it must be one of the group's characters.",
                "If target_object is used, it must be a target string from scene_info_json.",
                "Single lookat must be a target string from scene_info_json.",
                "Every group_id and single position_id from stage1 must appear exactly once.",
            ],
            "where": self.where,
            "scene_info_json": compact_scene,
            "position_lib_json": {"layout_library": compact_layouts},
            "stage1_grouping": self.stage1_result,
            "stage2_context": {
                "group_contexts": group_contexts,
                "single_contexts": single_contexts,
                "timeline_dialogue_analysis": self.timeline_analysis or {},
                "move_position_links": self._build_move_position_links(),
                "move_group_links": self._build_move_group_links(),
                "move_region_hints": self._build_move_region_hints(),
                "region_planning_rules": [
                    "Use scene_info_json.regions[*].spatial_relations as the strongest signal for whether two regions are adjacent, near, medium, or far.",
                    "Use move_position_links as exact move-pair constraints from script_json. Every listed source_position_id -> destination_position_id pair must avoid far-apart region assignments.",
                    "Use move_group_links to enforce group-level move continuity. If a group contains moved destination positions, the group's candidate region must be non-far relative to every linked source position and source owner.",
                    "First satisfy move distance plausibility and spatial_relations, then continuity, then anchor_count. After those are satisfied, use region.description before shot_description and dialogue as the next semantic fit check.",
                    "Do not pick a region mainly because its description matches the scene semantics if that region is less spatially plausible than another same / adjacent / near / medium option.",
                    "Prefer regions with more anchors for larger groups or for scenes that likely need multiple distinct staging options.",
                    "If a move creates a new blocking beat, the destination region should still remain spatially plausible relative to the source region based on spatial_relations first, then description.",
                    "Do not map any move to two regions whose direct spatial_relations label is far.",
                    "connected does not override far. If the direct pair is far, treat it as far even if the regions are still connected inside the larger scene graph.",
                    "Same-region, adjacent, near, or medium destination planning is allowed for move-linked owners; far destination planning is forbidden.",
                    "Do not justify a move destination by citing adjacency to some third region. Only the direct source-region -> candidate-region relation of the moved positions matters for move legality.",
                    "The returned region field must already be the final accepted region, not an earlier rejected draft.",
                    "The reason must explain only the returned region. Do not include long self-corrections that conclude with a different region than the one stored in the region field.",
                    "Use region.description to distinguish between multiple already-plausible candidates after spatial_relations, continuity, and anchor_count; only then use shot_description and dialogue as the final supporting composition check.",
                    "If the destination becomes an isolated single with shot_description cues like exiting the scene, distant background, passive observer, or outside main interaction, adjacent or medium-distance retreat regions are acceptable and may be better than staying in the same region.",
                    "If multiple moved positions form a coordinated exit or withdrawal group, adjacent or medium-distance retreat regions are acceptable when they better match the shot_description than the original interaction region, but far regions are not allowed.",
                ],
            },
            "output_schema": {
                "groups": [
                    {
                        "group_id": "G1",
                        "region": "RegionName",
                        "layout": "triangle",
                        "lookat": {"mode": "target", "target_character": "A"},
                        "reason": "Brief reason",
                    },
                    {
                        "group_id": "G2",
                        "region": "RegionName",
                        "layout": "triangle",
                        "lookat": {"mode": "target", "target_object": "ObjectName"},
                        "reason": "Brief reason",
                    }
                ],
                "singles": [
                    {
                        "position_id": "P3",
                        "region": "RegionName",
                        "neartarget": "ObjectName",
                        "lookat": "ObjectName",
                        "reason": "Brief reason",
                    }
                ],
            },
        }
        return json.dumps(prompt, ensure_ascii=False, indent=2)

    def _build_stage3_prompt(self):
        prompt = {
            "task": "stage3_compilation",
            "must_output_json": True,
            "instructions": [
                "Compile the final position plan.",
                "Remove all reason fields.",
                "Use where exactly as script_json.where and scene_info_json.where.",
                "Preserve every position_id exactly once.",
                "Do not invent any extra fields.",
                "For group lookat target mode, preserve exactly one of target_character or target_object.",
            ],
            "where": self.where,
            "template_json": self.template_json,
            "stage1_grouping": self.stage1_result,
            "stage2_planning": self.stage2_result,
            "output_schema": {
                "where": self.where,
                "groups": [
                    {
                        "group_id": "G1",
                        "layout": "triangle",
                        "region": "RegionName",
                        "positions": [
                            {"position_id": "P1", "character": "A"},
                            {"position_id": "P2", "character": "B"},
                        ],
                        "lookat": {"mode": "center"},
                    },
                    {
                        "group_id": "G2",
                        "layout": "triangle",
                        "region": "RegionName",
                        "positions": [
                            {"position_id": "P4", "character": "D"},
                            {"position_id": "P5", "character": "E"},
                        ],
                        "lookat": {"mode": "target", "target_object": "ObjectName"},
                    }
                ],
                "singles": [
                    {
                        "position_id": "P3",
                        "character": "C",
                        "region": "RegionName",
                        "neartarget": "ObjectName",
                        "lookat": "ObjectName",
                    }
                ],
            },
        }
        return json.dumps(prompt, ensure_ascii=False, indent=2)

    def _normalize_stage1_result(self, raw):
        groups_payload = raw.get("groups", [])
        singles_payload = raw.get("singles", [])

        assigned = set()
        groups = []
        leftover = []

        if isinstance(groups_payload, list):
            for group in groups_payload:
                if not isinstance(group, dict):
                    continue
                positions = []
                group_seen = set()
                group_characters = set()
                for item in group.get("positions", []):
                    normalized = self._normalize_position_ref(item)
                    if normalized is None:
                        continue
                    position_id = normalized["position_id"]
                    character = normalized["character"]
                    if position_id in assigned or position_id in group_seen:
                        continue
                    if character in group_characters:
                        leftover.append(
                            {
                                "position_id": position_id,
                                "character": character,
                                "reason": "Position duplicated the same character inside one group and was isolated.",
                            }
                        )
                        continue
                    group_seen.add(position_id)
                    group_characters.add(character)
                    positions.append(normalized)
                if len(positions) > self.max_layout_people:
                    extra = positions[self.max_layout_people :]
                    positions = positions[: self.max_layout_people]
                    for item in extra:
                        leftover.append(
                            {
                                "position_id": item["position_id"],
                                "character": item["character"],
                                "reason": "Group exceeded maximum supported layout size and was split.",
                            }
                        )
                if len(positions) >= 2:
                    positions = sorted(positions, key=lambda item: self.position_order.index(item["position_id"]))
                    groups.append(
                        {
                            "positions": positions,
                            "reason": self._safe_reason(group.get("reason"), "Grouped by model output."),
                        }
                    )
                    assigned.update(item["position_id"] for item in positions)
                else:
                    for item in positions:
                        leftover.append(
                            {
                                "position_id": item["position_id"],
                                "character": item["character"],
                                "reason": "Insufficient evidence for a valid group; converted to single.",
                            }
                        )

        singles = []
        if isinstance(singles_payload, list):
            for item in singles_payload:
                normalized = self._normalize_position_ref(item)
                if normalized is None:
                    continue
                position_id = normalized["position_id"]
                if position_id in assigned:
                    continue
                singles.append(
                    {
                        "position_id": position_id,
                        "character": normalized["character"],
                        "reason": self._safe_reason(item.get("reason"), "Isolated by model output."),
                    }
                )
                assigned.add(position_id)

        for item in leftover:
            if item["position_id"] in assigned:
                continue
            singles.append(copy.deepcopy(item))
            assigned.add(item["position_id"])

        remaining_positions = [position_id for position_id in self.position_order if position_id not in assigned]
        if remaining_positions:
            fallback_groups, fallback_singles = self._fallback_grouping_for_positions(remaining_positions)
            for group in fallback_groups:
                groups.append(group)
                assigned.update(item["position_id"] for item in group["positions"])
            for single in fallback_singles:
                if single["position_id"] not in assigned:
                    singles.append(single)
                    assigned.add(single["position_id"])

        groups, singles = self._enforce_conservative_grouping(groups, singles)
        groups, singles = self._enforce_required_dialogue_grouping(groups, singles)
        groups = self._sort_groups_by_position_order(groups)
        for index, group in enumerate(groups, start=1):
            group["group_id"] = f"G{index}"

        singles = self._sort_singles_by_position_order(singles)
        return {"groups": groups, "singles": singles}

    def _normalize_stage2_result(self, raw):
        group_plan_map = {}
        for item in raw.get("groups", []) if isinstance(raw.get("groups"), list) else []:
            if isinstance(item, dict) and isinstance(item.get("group_id"), str) and item["group_id"].strip():
                group_plan_map[item["group_id"].strip()] = item

        single_plan_map = {}
        for item in raw.get("singles", []) if isinstance(raw.get("singles"), list) else []:
            if isinstance(item, dict) and isinstance(item.get("position_id"), str) and item["position_id"].strip():
                single_plan_map[item["position_id"].strip()] = item

        groups = []
        for group in self.stage1_result["groups"]:
            groups.append(self._sanitize_group_plan(group, group_plan_map.get(group["group_id"], {})))

        singles = []
        for single in self.stage1_result["singles"]:
            singles.append(self._sanitize_single_plan(single, single_plan_map.get(single["position_id"], {})))

        groups, singles = self._enforce_move_region_continuity(groups, singles)
        return {"groups": groups, "singles": singles}

    def _repair_stage2_raw_output(self, raw):
        violations = self._collect_stage2_raw_violations(raw)
        if not violations or not self.api_key:
            return raw
        repaired = self.call_llm(self._build_stage2_repair_prompt(raw, violations))
        repaired_violations = self._collect_stage2_raw_violations(repaired)
        return repaired if not repaired_violations else raw

    def _collect_stage2_raw_violations(self, raw):
        violations = []
        if not isinstance(raw, dict):
            return ["stage2 raw output must be a JSON object."]

        group_plan_map = {}
        for item in raw.get("groups", []) if isinstance(raw.get("groups"), list) else []:
            if isinstance(item, dict) and isinstance(item.get("group_id"), str) and item["group_id"].strip():
                group_plan_map[item["group_id"].strip()] = item
        single_plan_map = {}
        for item in raw.get("singles", []) if isinstance(raw.get("singles"), list) else []:
            if isinstance(item, dict) and isinstance(item.get("position_id"), str) and item["position_id"].strip():
                single_plan_map[item["position_id"].strip()] = item

        for group in self.stage1_result["groups"]:
            group_id = group["group_id"]
            raw_group = group_plan_map.get(group_id, {})
            candidate_region = raw_group.get("region")
            if candidate_region in self.region_map:
                far_sources = self._owner_far_move_sources(
                    [item["position_id"] for item in group["positions"]],
                    candidate_region,
                )
                if far_sources:
                    violations.append(
                        f"group {group_id} uses region {candidate_region}, but move-linked sources {far_sources} are far from it."
                    )
                reason = self._stringify_context_value(raw_group.get("reason"))
                reason_selected_region = self._extract_reason_selected_region(reason)
                if reason_selected_region and reason_selected_region != candidate_region:
                    violations.append(
                        f"group {group_id} region field is {candidate_region}, but reason concludes {reason_selected_region}."
                    )
                if self._reason_marks_region_invalid(reason, candidate_region):
                    violations.append(
                        f"group {group_id} reason marks its own region {candidate_region} as invalid/far."
                    )

        for single in self.stage1_result["singles"]:
            position_id = single["position_id"]
            raw_single = single_plan_map.get(position_id, {})
            candidate_region = raw_single.get("region")
            if candidate_region in self.region_map:
                far_sources = self._owner_far_move_sources([position_id], candidate_region)
                if far_sources:
                    violations.append(
                        f"single {position_id} uses region {candidate_region}, but move-linked sources {far_sources} are far from it."
                    )
                reason = self._stringify_context_value(raw_single.get("reason"))
                reason_selected_region = self._extract_reason_selected_region(reason)
                if reason_selected_region and reason_selected_region != candidate_region:
                    violations.append(
                        f"single {position_id} region field is {candidate_region}, but reason concludes {reason_selected_region}."
                    )
                if self._reason_marks_region_invalid(reason, candidate_region):
                    violations.append(
                        f"single {position_id} reason marks its own region {candidate_region} as invalid/far."
                    )
        return violations

    def _build_stage2_repair_prompt(self, raw, violations):
        compact_scene = {
            "where": self.where,
            "region_relationships": self._summarize_region_relationships(),
            "regions": [
                {
                    "name": region["name"],
                    "anchor_count": region.get("anchor_count", 0),
                    "spatial_relations": copy.deepcopy(region.get("spatial_relations", [])),
                    "description": region.get("description", ""),
                }
                for region in self.scene_info_json["regions"]
            ],
        }
        compact_layouts = [
            {
                "layout": layout["layout"],
                "min_people": layout["min_people"],
                "max_people": layout["max_people"],
            }
            for layout in self.layouts
        ]
        prompt = {
            "task": "stage2_planning_repair",
            "must_output_json": True,
            "instructions": [
                "Repair the provided stage2 planning JSON so that every region choice obeys move constraints and every reason matches the final selected region field.",
                "Do not rewrite valid items unnecessarily. Only fix invalid or inconsistent groups/singles.",
                "Any move-linked source/destination pair is invalid if the direct spatial_relations label is far.",
                "Any move-linked group is invalid if any moved member's source region is far from the group's candidate region.",
                "connected does not override far.",
                "The final region field and the final reason must match exactly.",
                "The reason must justify only the selected region. Do not include rejected candidates or self-correction chains.",
                "Return only the corrected final JSON object with groups and singles.",
            ],
            "where": self.where,
            "scene_info_json": compact_scene,
            "position_lib_json": {"layout_library": compact_layouts},
            "stage1_grouping": self.stage1_result,
            "move_position_links": self._build_move_position_links(),
            "move_group_links": self._build_move_group_links(),
            "current_invalid_output": raw,
            "violations": violations,
            "output_schema": {
                "groups": [
                    {
                        "group_id": "G1",
                        "region": "RegionName",
                        "layout": "triangle",
                        "lookat": {"mode": "target", "target_character": "A"},
                        "reason": "Brief reason about the final selected region only.",
                    }
                ],
                "singles": [
                    {
                        "position_id": "P3",
                        "region": "RegionName",
                        "neartarget": "TargetName",
                        "lookat": "TargetName",
                        "reason": "Brief reason about the final selected region only.",
                    }
                ],
            },
        }
        return json.dumps(prompt, ensure_ascii=False, indent=2)

    def _normalize_final_plan(self, raw):
        candidate = raw if isinstance(raw, dict) else {}
        plan = {"where": self.where, "groups": [], "singles": []}

        raw_group_map = {}
        for group in candidate.get("groups", []) if isinstance(candidate.get("groups"), list) else []:
            if isinstance(group, dict) and isinstance(group.get("group_id"), str) and group["group_id"].strip():
                raw_group_map[group["group_id"].strip()] = group

        raw_single_map = {}
        for single in candidate.get("singles", []) if isinstance(candidate.get("singles"), list) else []:
            if isinstance(single, dict) and isinstance(single.get("position_id"), str) and single["position_id"].strip():
                raw_single_map[single["position_id"].strip()] = single

        stage2_group_map = OrderedDict((group["group_id"], group) for group in self.stage2_result["groups"])
        stage2_single_map = OrderedDict((single["position_id"], single) for single in self.stage2_result["singles"])

        for group in self.stage1_result["groups"]:
            group_id = group["group_id"]
            raw_group = raw_group_map.get(group_id, {})
            stage2_group = stage2_group_map[group_id]
            plan["groups"].append(
                {
                    "group_id": group_id,
                    "layout": self._coerce_valid_layout(raw_group.get("layout"), stage2_group["layout"], len(group["positions"])),
                    "region": self._coerce_valid_region(raw_group.get("region"), stage2_group["region"]),
                    "positions": self._normalize_final_group_positions(raw_group.get("positions"), group["positions"]),
                    "lookat": self._coerce_valid_group_lookat(raw_group.get("lookat"), group, stage2_group["lookat"]),
                }
            )

        for single in self.stage1_result["singles"]:
            position_id = single["position_id"]
            raw_single = raw_single_map.get(position_id, {})
            stage2_single = stage2_single_map[position_id]
            final_region = self._coerce_valid_region(raw_single.get("region"), stage2_single["region"])
            context = self.position_map[position_id].get("context", "")
            fallback_neartarget = stage2_single["neartarget"]
            if fallback_neartarget not in self.region_targets.get(final_region, []):
                fallback_neartarget = self._choose_neartarget(final_region, context)
            final_neartarget = self._coerce_valid_neartarget(
                raw_single.get("neartarget"),
                final_region,
                fallback_neartarget,
            )
            fallback_lookat = stage2_single["lookat"]
            if not isinstance(fallback_lookat, str) or fallback_lookat.strip() not in self.all_targets:
                fallback_lookat = self._choose_single_lookat(final_region, context, final_neartarget)
            plan["singles"].append(
                {
                    "position_id": position_id,
                    "character": self.position_map[position_id]["character"],
                    "region": final_region,
                    "neartarget": final_neartarget,
                    "lookat": self._coerce_valid_single_lookat(raw_single.get("lookat"), fallback_lookat),
                }
            )

        plan["groups"], plan["singles"] = self._enforce_move_region_continuity(plan["groups"], plan["singles"])
        plan["groups"] = self._sort_final_groups(plan["groups"])
        plan["singles"] = self._sort_final_singles(plan["singles"])
        return plan

    def _deterministic_compile_plan(self):
        plan = {"where": self.where, "groups": [], "singles": []}
        for group in self.stage1_result["groups"]:
            stage2_group = self._find_by_key(self.stage2_result["groups"], "group_id", group["group_id"])
            plan["groups"].append(
                {
                    "group_id": group["group_id"],
                    "layout": stage2_group["layout"],
                    "region": stage2_group["region"],
                    "positions": copy.deepcopy(group["positions"]),
                    "lookat": copy.deepcopy(stage2_group["lookat"]),
                }
            )
        for single in self.stage1_result["singles"]:
            stage2_single = self._find_by_key(self.stage2_result["singles"], "position_id", single["position_id"])
            plan["singles"].append(
                {
                    "position_id": single["position_id"],
                    "character": single["character"],
                    "region": stage2_single["region"],
                    "neartarget": stage2_single["neartarget"],
                    "lookat": stage2_single["lookat"],
                }
            )
        return plan

    def _normalize_position_ref(self, item):
        if not isinstance(item, dict):
            return None
        position_id = item.get("position_id")
        if not isinstance(position_id, str) or not position_id.strip():
            return None
        position_id = position_id.strip()
        if position_id not in self.position_map:
            return None
        return {"position_id": position_id, "character": self.position_map[position_id]["character"]}

    def _build_stage1_hints(self):
        pair_hints = []
        for left_id, right_id in combinations(self.position_order, 2):
            score = self._pair_interaction_score(left_id, right_id)
            if score <= 0:
                continue
            pair_hints.append(
                {
                    "position_ids": [left_id, right_id],
                    "characters": [
                        self.position_map[left_id]["character"],
                        self.position_map[right_id]["character"],
                    ],
                    "interaction_score": score,
                }
            )
        pair_hints.sort(
            key=lambda item: (
                -item["interaction_score"],
                self.position_order.index(item["position_ids"][0]),
                self.position_order.index(item["position_ids"][1]),
            )
        )

        move_split_hints = []
        isolated_candidates = []
        for position_id in self.position_order:
            character = self.position_map[position_id]["character"]
            if self._position_has_move_signal(position_id):
                move_split_hints.append(
                    {
                        "position_id": position_id,
                        "character": character,
                        "hint": "This position is associated with a move action and should usually start a new grouping episode.",
                    }
                )
            has_interaction = any(
                self._pair_interaction_score(position_id, other_id) > 0
                for other_id in self.position_order
                if other_id != position_id
            )
            if not has_interaction:
                isolated_candidates.append({"position_id": position_id, "character": character})

        return {
            "position_order": list(self.position_order),
            "pairwise_interaction_hints": pair_hints[:20],
            "move_split_hints": move_split_hints,
            "isolated_candidates": isolated_candidates,
            "episode_group_hints": self._build_episode_group_hints(),
            "required_dialogue_groups": copy.deepcopy(self.required_dialogue_groups),
        }

    def _build_episode_group_hints(self):
        if not self.timeline_analysis:
            return []
        hints = []
        for episode in self.timeline_analysis.get("episodes", []):
            active_positions = [
                item
                for item in episode.get("active_positions", [])
                if item.get("position_id") in self.position_map
            ]
            if not active_positions:
                continue
            hints.append(
                {
                    "episode_id": episode.get("episode_id"),
                    "trigger": episode.get("trigger"),
                    "active_positions": active_positions,
                    "passive_positions": episode.get("passive_positions", []),
                    "dialogue_lines": [
                        {
                            "speaker": line.get("speaker", ""),
                            "speaker_position": line.get("speaker_position", ""),
                            "content": line.get("content", ""),
                            "shot_description": line.get("shot_description", ""),
                        }
                        for line in episode.get("dialogue_flow", [])
                    ],
                }
            )
        return hints

    def _enforce_conservative_grouping(self, groups, singles):
        refined_groups = []
        refined_singles = [copy.deepcopy(single) for single in singles]

        for group in groups:
            position_ids = [item["position_id"] for item in group.get("positions", [])]
            fragments = self._split_candidate_position_ids(position_ids)
            if len(fragments) == 1 and len(fragments[0]) == len(position_ids):
                refined_groups.append(copy.deepcopy(group))
                continue

            for fragment in fragments:
                if len(fragment) >= 2:
                    refined_groups.append(
                        {
                            "positions": [self._position_ref(position_id) for position_id in fragment],
                            "reason": "Refined into a smaller interaction group by conservative grouping rules.",
                        }
                    )
                else:
                    position_id = fragment[0]
                    refined_singles.append(
                        {
                            "position_id": position_id,
                            "character": self.position_map[position_id]["character"],
                            "reason": "Refined into a single because interaction evidence was too weak for a larger group.",
                        }
                    )

        deduped_groups = []
        used_group_positions = set()
        for group in self._sort_groups_by_position_order(refined_groups):
            ordered_positions = []
            seen_group_positions = set()
            seen_group_characters = set()
            for item in group.get("positions", []):
                position_id = item["position_id"]
                character = self.position_map[position_id]["character"]
                if position_id in used_group_positions or position_id in seen_group_positions or character in seen_group_characters:
                    continue
                ordered_positions.append(self._position_ref(position_id))
                seen_group_positions.add(position_id)
                seen_group_characters.add(character)
            if len(ordered_positions) >= 2:
                deduped_groups.append(
                    {
                        "positions": ordered_positions,
                        "reason": self._safe_reason(group.get("reason"), "Grouped by conservative refinement."),
                    }
                )
                used_group_positions.update(item["position_id"] for item in ordered_positions)

        deduped_singles = []
        used_single_positions = set(used_group_positions)
        for single in self._sort_singles_by_position_order(refined_singles):
            position_id = single["position_id"]
            if position_id in used_single_positions:
                continue
            deduped_singles.append(
                {
                    "position_id": position_id,
                    "character": self.position_map[position_id]["character"],
                    "reason": self._safe_reason(single.get("reason"), "Isolated by conservative refinement."),
                }
            )
            used_single_positions.add(position_id)

        return deduped_groups, deduped_singles

    def _enforce_required_dialogue_grouping(self, groups, singles):
        if not self.required_dialogue_groups:
            return groups, singles

        clusters = []
        for group in groups:
            clusters.append(
                {
                    "positions": [item["position_id"] for item in group.get("positions", [])],
                    "reason": self._safe_reason(group.get("reason"), "Grouped by model output."),
                }
            )
        for single in singles:
            clusters.append(
                {
                    "positions": [single["position_id"]],
                    "reason": self._safe_reason(single.get("reason"), "Isolated by model output."),
                }
            )

        for required_group in self.required_dialogue_groups:
            required_ids = [item["position_id"] for item in required_group.get("positions", []) if item.get("position_id") in self.position_map]
            if len(required_ids) < 2:
                continue

            required_set = set(required_ids)
            next_clusters = []
            merged_positions = []

            for cluster in clusters:
                cluster_positions = cluster["positions"]
                overlap = [position_id for position_id in cluster_positions if position_id in required_set]
                if not overlap:
                    next_clusters.append(cluster)
                    continue

                merged_positions.extend(overlap)
                leftovers = [position_id for position_id in cluster_positions if position_id not in required_set]
                if leftovers:
                    next_clusters.append(
                        {
                            "positions": leftovers,
                            "reason": cluster["reason"],
                        }
                    )

            ordered_merged = [position_id for position_id in self.position_order if position_id in set(merged_positions)]
            for position_id in required_ids:
                if position_id not in ordered_merged:
                    ordered_merged.append(position_id)

            if len(ordered_merged) >= 2:
                next_clusters.append(
                    {
                        "positions": ordered_merged,
                        "reason": self._safe_reason(
                            required_group.get("reason"),
                            "Merged by hard dialogue-group constraint.",
                        ),
                    }
                )

            clusters = next_clusters

        merged_groups = []
        merged_singles = []
        consumed = set()
        for cluster in clusters:
            ordered_positions = []
            seen_characters = set()
            for position_id in cluster["positions"]:
                if position_id in consumed or position_id not in self.position_map:
                    continue
                character = self.position_map[position_id]["character"]
                if character in seen_characters:
                    continue
                ordered_positions.append(position_id)
                seen_characters.add(character)
                consumed.add(position_id)

            if len(ordered_positions) >= 2:
                merged_groups.append(
                    {
                        "positions": [self._position_ref(position_id) for position_id in ordered_positions],
                        "reason": self._safe_reason(cluster.get("reason"), "Merged by dialogue-group constraint."),
                    }
                )
            elif len(ordered_positions) == 1:
                position_id = ordered_positions[0]
                merged_singles.append(
                    {
                        "position_id": position_id,
                        "character": self.position_map[position_id]["character"],
                        "reason": self._safe_reason(cluster.get("reason"), "Isolated after dialogue-group enforcement."),
                    }
                )

        for position_id in self.position_order:
            if position_id in consumed:
                continue
            merged_singles.append(
                {
                    "position_id": position_id,
                    "character": self.position_map[position_id]["character"],
                    "reason": "Recovered after dialogue-group enforcement.",
                }
            )

        return self._sort_groups_by_position_order(merged_groups), self._sort_singles_by_position_order(merged_singles)

    def _split_candidate_position_ids(self, position_ids):
        remaining = []
        for position_id in position_ids:
            if position_id not in self.position_map or position_id in remaining:
                continue
            remaining.append(position_id)

        fragments = []
        while len(remaining) >= 2:
            best_subset = self._find_best_interactive_subset(remaining)
            if not best_subset:
                break
            fragments.append(best_subset)
            chosen_set = set(best_subset)
            remaining = [position_id for position_id in remaining if position_id not in chosen_set]

        for position_id in remaining:
            fragments.append([position_id])
        return fragments

    def _find_best_interactive_subset(self, position_ids):
        ordered = sorted(position_ids, key=self.position_order.index)
        max_size = min(len(ordered), self.max_layout_people)
        best_subset = None
        best_score = -1

        for subset_size in range(max_size, 1, -1):
            for subset in combinations(ordered, subset_size):
                if not self._subset_has_distinct_characters(subset):
                    continue
                if not self._subset_is_fully_interactive(subset):
                    continue
                score = self._subset_interaction_score(subset)
                if best_subset is None or score > best_score or (
                    score == best_score and self._subset_sort_key(subset) < self._subset_sort_key(best_subset)
                ):
                    best_subset = list(subset)
                    best_score = score
            if best_subset is not None:
                return best_subset
        return []

    def _subset_has_distinct_characters(self, position_ids):
        characters = [self.position_map[position_id]["character"] for position_id in position_ids]
        return len(characters) == len(set(characters))

    def _subset_is_fully_interactive(self, position_ids):
        if len(position_ids) < 2:
            return False
        for left_id, right_id in combinations(position_ids, 2):
            if self._pair_interaction_score(left_id, right_id) <= 0:
                return False
        return True

    def _subset_interaction_score(self, position_ids):
        score = 0
        for left_id, right_id in combinations(position_ids, 2):
            score += self._pair_interaction_score(left_id, right_id)
        return score

    def _subset_sort_key(self, position_ids):
        ordered = sorted(position_ids, key=self.position_order.index)
        return [self.position_order.index(position_id) for position_id in ordered]

    def _position_has_move_signal(self, position_id):
        context = self.position_map[position_id].get("context", "")
        lowered = context.lower()
        return f"moves to {position_id.lower()}" in lowered

    def _extract_move_signatures(self, text):
        if not text:
            return []
        signatures = []
        for fragment in text.split("|"):
            cleaned = fragment.strip()
            if cleaned.startswith("moves="):
                signatures.append(cleaned)
        return signatures

    def _pair_interaction_score(self, left_id, right_id):
        left_character = self.position_map[left_id]["character"]
        right_character = self.position_map[right_id]["character"]
        if left_character == right_character:
            return 0

        left_context = self.position_map[left_id].get("context", "")
        right_context = self.position_map[right_id].get("context", "")
        shared_active_episodes = self.position_active_episode_map.get(left_id, set()) & self.position_active_episode_map.get(right_id, set())
        shared_visible_episodes = self.position_visible_episode_map.get(left_id, set()) & self.position_visible_episode_map.get(right_id, set())

        if self.timeline_analysis and not shared_active_episodes:
            return 0

        score = 0
        if shared_active_episodes:
            score += 4 * len(shared_active_episodes)
        elif shared_visible_episodes:
            score += 1

        if self._mentions_character_or_position(left_context, right_character, right_id):
            score += 1
        if self._mentions_character_or_position(right_context, left_character, left_id):
            score += 1
        if self._texts_indicate_interaction(left_context, left_character, right_character):
            score += 2
        if self._texts_indicate_interaction(right_context, right_character, left_character):
            score += 2
        return max(score, 0)

    def _mentions_character_or_position(self, text, other_character, other_position_id):
        if not text:
            return False
        return other_character in text or other_position_id in text

    def _fallback_grouping_for_positions(self, position_ids):
        interaction_groups = self._build_interaction_groups(position_ids)
        groups = []
        singles = []

        for component in interaction_groups:
            ordered = [position_id for position_id in self.position_order if position_id in component]
            fragments = self._split_candidate_position_ids(ordered)
            for fragment in fragments:
                if len(fragment) >= 2:
                    groups.append(
                        {
                            "positions": [self._position_ref(position_id) for position_id in fragment],
                            "reason": "Grouped by deterministic interaction fallback.",
                        }
                    )
                else:
                    position_id = fragment[0]
                    singles.append(
                        {
                            "position_id": position_id,
                            "character": self.position_map[position_id]["character"],
                            "reason": "Isolated by deterministic fallback.",
                        }
                    )

        return self._enforce_conservative_grouping(
            self._sort_groups_by_position_order(groups),
            self._sort_singles_by_position_order(singles),
        )

    def _build_interaction_groups(self, position_ids):
        graph = {position_id: set() for position_id in position_ids}
        position_ids = list(position_ids)
        for index, position_id in enumerate(position_ids):
            context = self.position_map[position_id].get("context", "")
            character = self.position_map[position_id]["character"]
            for mentioned_id in self._extract_explicit_position_mentions(context):
                if mentioned_id in graph and mentioned_id != position_id:
                    graph[position_id].add(mentioned_id)
                    graph[mentioned_id].add(position_id)
            for other_id in position_ids[index + 1 :]:
                other_character = self.position_map[other_id]["character"]
                if character == other_character:
                    continue
                other_context = self.position_map[other_id].get("context", "")
                if self._texts_indicate_interaction(context, character, other_character) or self._texts_indicate_interaction(other_context, other_character, character):
                    graph[position_id].add(other_id)
                    graph[other_id].add(position_id)

        components = []
        seen = set()
        for position_id in self.position_order:
            if position_id not in graph or position_id in seen:
                continue
            stack = [position_id]
            component = []
            while stack:
                current = stack.pop()
                if current in seen:
                    continue
                seen.add(current)
                component.append(current)
                for neighbor in graph[current]:
                    if neighbor not in seen:
                        stack.append(neighbor)
            components.append(component)
        return components

    def _extract_explicit_position_mentions(self, text):
        if not text:
            return []
        return [position_id for position_id in self.position_order if position_id in text]

    def _texts_indicate_interaction(self, text, self_character, other_character):
        if not text:
            return False
        if other_character not in text:
            return False
        lowered = text.lower()
        actor_markers = (
            f"speaker={self_character}",
            f"{self_character}:",
            f"{self_character} moves to",
        )
        if not any(marker in text for marker in actor_markers):
            return False
        if f"speaker={self_character}" in text:
            return True
        keywords = (
            "dialogue",
            "talk",
            "speak",
            "conversation",
            "interaction",
            "argue",
            "respond",
            "reply",
            "问",
            "说",
            "回应",
            "对话",
            "交互",
            "看向",
        )
        lowered = text.lower()
        return other_character in text and any(keyword in lowered or keyword in text for keyword in keywords)

    def _sanitize_group_plan(self, stage1_group, raw_plan):
        combined_context = self._group_context(stage1_group["positions"])
        default_region = self._choose_region(combined_context)
        default_layout = self._choose_layout(len(stage1_group["positions"]), combined_context)
        default_lookat = self._choose_group_lookat(stage1_group)
        region = self._coerce_valid_region(raw_plan.get("region"), default_region)
        return {
            "group_id": stage1_group["group_id"],
            "positions": copy.deepcopy(stage1_group["positions"]),
            "region": region,
            "layout": self._coerce_valid_layout(raw_plan.get("layout"), default_layout, len(stage1_group["positions"])),
            "lookat": self._coerce_valid_group_lookat(raw_plan.get("lookat"), stage1_group, default_lookat),
            "reason": self._sanitize_reason_for_region(raw_plan.get("reason"), region, "Planned deterministically."),
        }

    def _sanitize_single_plan(self, stage1_single, raw_plan):
        context = self.position_map[stage1_single["position_id"]].get("context", "")
        default_region = self._choose_region(context)
        region = self._coerce_valid_region(raw_plan.get("region"), default_region)
        neartarget = self._coerce_valid_neartarget(raw_plan.get("neartarget"), region, self._choose_neartarget(region, context))
        default_lookat = self._choose_single_lookat(region, context, neartarget)
        return {
            "position_id": stage1_single["position_id"],
            "region": region,
            "neartarget": neartarget,
            "lookat": self._coerce_valid_single_lookat(raw_plan.get("lookat"), default_lookat),
            "reason": self._sanitize_reason_for_region(raw_plan.get("reason"), region, "Planned deterministically."),
        }

    def _coerce_valid_region(self, candidate, fallback):
        if isinstance(candidate, str) and candidate.strip() in self.region_map:
            return candidate.strip()
        return fallback

    def _coerce_valid_layout(self, candidate, fallback, people_count):
        if isinstance(candidate, str):
            candidate = candidate.strip()
            if candidate in self.layout_map and self._layout_supports_size(candidate, people_count):
                return candidate
        return fallback

    def _coerce_valid_group_lookat(self, candidate, stage1_group, fallback):
        group_characters = [item["character"] for item in stage1_group["positions"]]
        if isinstance(candidate, dict):
            mode = candidate.get("mode")
            if mode == "center":
                return {"mode": "center"}
            if mode == "target":
                target_character = candidate.get("target_character")
                target_object = candidate.get("target_object")
                if target_character in group_characters:
                    return {"mode": "target", "target_character": target_character}
                if isinstance(target_object, str) and target_object.strip() in self.all_targets:
                    return {"mode": "target", "target_object": target_object.strip()}
        return copy.deepcopy(fallback)

    def _coerce_valid_neartarget(self, candidate, region, fallback):
        if isinstance(candidate, str) and candidate.strip() in self.region_targets.get(region, []):
            return candidate.strip()
        return fallback

    def _coerce_valid_single_lookat(self, candidate, fallback):
        if isinstance(candidate, str) and candidate.strip() in self.all_targets:
            return candidate.strip()
        return fallback

    def _owner_far_move_sources(self, position_ids, candidate_region):
        far_sources = []
        for transition in self.move_transitions:
            destination_position_id = transition.get("destination_position_id", "")
            if destination_position_id not in position_ids:
                continue
            source_position_id = transition.get("source_position_id", "")
            source_region = self._position_source_region_for_transition(transition)
            if source_region and candidate_region and self._regions_are_far(source_region, candidate_region):
                label = source_position_id or source_region
                if label not in far_sources:
                    far_sources.append(label)
        return far_sources

    def _position_source_region_for_transition(self, transition):
        source_position_id = transition.get("source_position_id", "")
        if not source_position_id:
            return ""
        for group in self.stage1_result.get("groups", []) if self.stage1_result else []:
            if any(item.get("position_id") == source_position_id for item in group.get("positions", [])):
                stage1_group_context = self._group_context(group.get("positions", []))
                return self._choose_region(stage1_group_context)
        if self.stage1_result:
            for single in self.stage1_result.get("singles", []):
                if single.get("position_id") == source_position_id:
                    context = self.position_map.get(source_position_id, {}).get("context", "")
                    return self._choose_region(context)
        context = self.position_map.get(source_position_id, {}).get("context", "")
        return self._choose_region(context) if context else ""

    def _extract_reason_selected_region(self, reason):
        text = self._stringify_context_value(reason)
        if not text:
            return ""
        patterns = [
            r"(?:choose|selected|select|final selected region is)\s*([^\s，。,:：;；]+)",
            r"(?:选择|选定|最终选择(?:的)?区域(?:是)?|最终区域(?:是)?)\s*([^\s，。,:：;；]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if not match:
                continue
            candidate = self._stringify_context_value(match.group(1))
            if candidate in self.region_map:
                return candidate
        for region_name in self.region_names:
            if f"{region_name} is selected" in text or f"choose {region_name}" in text or f"选择{region_name}" in text:
                return region_name
        return ""

    def _reason_marks_region_invalid(self, reason, region_name):
        text = self._stringify_context_value(reason)
        if not text or not region_name:
            return False
        lowered = text.lower()
        invalid_patterns = (
            f"{region_name} is invalid",
            f"{region_name} invalid",
            f"{region_name} is far",
            f"{region_name}无效",
            f"{region_name}是无效",
            f"{region_name}是 far",
            f"{region_name} 太远",
            f"{region_name}较远",
        )
        return any(pattern.lower() in lowered for pattern in invalid_patterns)

    def _sanitize_reason_for_region(self, reason, selected_region, fallback):
        normalized = self._safe_reason(reason, fallback)
        if not selected_region or not normalized:
            return normalized

        text = normalized.strip()
        if selected_region not in text:
            return normalized

        invalid_patterns = (
            f"{selected_region} is invalid",
            f"{selected_region} invalid",
            f"{selected_region} is far",
            f"{selected_region}是无效",
            f"{selected_region}无效",
            f"{selected_region} 太远",
            f"{selected_region}较远",
        )
        lowered = text.lower()
        if any(pattern.lower() in lowered for pattern in invalid_patterns):
            return f"Final selected region is {selected_region}. {fallback}"

        sentences = [segment.strip() for segment in re.split(r"(?<=[.!?。！？])\s+", text) if segment.strip()]
        if not sentences:
            return normalized

        filtered = []
        for sentence in sentences:
            if selected_region in sentence:
                filtered.append(sentence)
        if filtered:
            return " ".join(filtered[:3]).strip()
        return normalized

    def _normalize_final_group_positions(self, candidate_positions, fallback_positions):
        fallback_by_id = OrderedDict((item["position_id"], item) for item in fallback_positions)
        normalized = []
        seen = set()
        if isinstance(candidate_positions, list):
            for item in candidate_positions:
                ref = self._normalize_position_ref(item)
                if ref is None:
                    continue
                position_id = ref["position_id"]
                if position_id not in fallback_by_id or position_id in seen:
                    continue
                normalized.append(self._position_ref(position_id))
                seen.add(position_id)
        if len(normalized) != len(fallback_positions):
            return [self._position_ref(item["position_id"]) for item in fallback_positions]
        return sorted(normalized, key=lambda item: self.position_order.index(item["position_id"]))

    def _choose_region(self, context):
        best_region = self.region_names[0]
        best_score = -1
        for region_name in self.region_names:
            score = self._score_region_for_context(region_name, context)
            if score > best_score:
                best_score = score
                best_region = region_name
        return best_region

    def _score_region_for_context(self, region_name, context):
        region = self.region_map.get(region_name, {})
        lowered = context.lower()
        score = 0
        if region_name.lower() in lowered or region_name in context:
            score += 10
        description = region.get("description", "")
        if description:
            description_tokens = [
                token.strip()
                for token in re.split(r"[，。、“”‘’：:；;（）()、\\s]+", description)
                if len(token.strip()) >= 2
            ]
            for token in description_tokens[:16]:
                if token and (token.lower() in lowered or token in context):
                    score += 1
        for target in self.region_targets.get(region_name, []):
            if target.lower() in lowered or target in context:
                score += 3
        score += min(int(region.get("anchor_count", 0)), 4) * 0.1
        return score

    def _build_region_relationships(self):
        relationships = OrderedDict()
        for source_region in self.region_names:
            relationships[source_region] = OrderedDict()
            for target_region in self.region_names:
                relationships[source_region][target_region] = self._infer_region_relationship(source_region, target_region)
        return relationships

    def _summarize_region_relationships(self):
        summary = []
        for source_region in self.region_names:
            relation_map = self.region_relationships.get(source_region, {})
            summary.append(
                {
                    "region": source_region,
                    "adjacent_regions": [
                        target_region
                        for target_region, relation in relation_map.items()
                        if target_region != source_region and relation.get("relation") == "adjacent"
                    ],
                    "near_or_connected_regions": [
                        target_region
                        for target_region, relation in relation_map.items()
                        if target_region != source_region and relation.get("relation") in {"adjacent", "near", "connected"}
                    ],
                    "medium_regions": [
                        target_region
                        for target_region, relation in relation_map.items()
                        if target_region != source_region and relation.get("relation") == "medium"
                    ],
                    "far_regions": [
                        target_region
                        for target_region, relation in relation_map.items()
                        if target_region != source_region and relation.get("relation") == "far"
                    ],
                    "relation_details": [
                        {
                            "region": target_region,
                            "relation": relation.get("relation", "unknown"),
                            "distance_m": relation.get("distance_hint"),
                            "connected": relation.get("connected"),
                        }
                        for target_region, relation in relation_map.items()
                        if target_region != source_region
                    ],
                }
            )
        return summary

    def _infer_region_relationship(self, source_region, target_region):
        if source_region == target_region:
            return {"relation": "same", "evidence": "same region", "distance_hint": 0, "connected": True}

        source_structured = self._scan_structured_region_relation(source_region, target_region)
        target_structured = self._scan_structured_region_relation(target_region, source_region)

        if self._combine_region_relations(
            source_structured.get("relation", "unknown"),
            target_structured.get("relation", "unknown"),
        ) != "unknown":
            source_observation = source_structured
            target_observation = target_structured
        else:
            source_description = self.region_map.get(source_region, {}).get("description", "")
            target_description = self.region_map.get(target_region, {}).get("description", "")
            source_observation = self._scan_description_region_relation(source_description, target_region)
            target_observation = self._scan_description_region_relation(target_description, source_region)

        relation = self._combine_region_relations(
            source_observation.get("relation", "unknown"),
            target_observation.get("relation", "unknown"),
        )
        distance_hints = [
            value
            for value in (source_observation.get("distance_hint"), target_observation.get("distance_hint"))
            if isinstance(value, int)
        ]
        evidence = source_observation.get("evidence") or target_observation.get("evidence") or ""
        connected_values = [
            value
            for value in (source_observation.get("connected"), target_observation.get("connected"))
            if isinstance(value, bool)
        ]
        return {
            "relation": relation,
            "evidence": evidence,
            "distance_hint": min(distance_hints) if distance_hints else None,
            "connected": any(connected_values) if connected_values else None,
        }

    def _scan_structured_region_relation(self, source_region, target_region):
        if not source_region or not target_region:
            return {"relation": "unknown", "evidence": "", "distance_hint": None, "connected": None}

        source_region_payload = self.region_map.get(source_region, {})
        for relation in source_region_payload.get("spatial_relations", []):
            if relation.get("region") != target_region:
                continue
            relation_label = self._normalize_region_relation_label(relation.get("relation"))
            distance_hint = relation.get("distance_m")
            if relation_label == "unknown" and distance_hint is not None:
                relation_label = self._relation_from_distance(distance_hint)
            connected = relation.get("connected")
            if relation_label == "unknown" and connected:
                relation_label = "connected"
            evidence = (
                f"{source_region}->{target_region}: relation={relation_label}, "
                f"distance={distance_hint}, connected={connected}"
            )
            return {
                "relation": relation_label,
                "distance_hint": distance_hint,
                "evidence": evidence,
                "connected": connected,
            }

        return {"relation": "unknown", "evidence": "", "distance_hint": None, "connected": None}

    def _scan_description_region_relation(self, description, other_region):
        if not description or not other_region or other_region not in description:
            return {"relation": "unknown", "evidence": "", "distance_hint": None, "connected": None}

        adjacent_keywords = (
            "\u76f8\u90bb",
            "\u7d27\u90bb",
            "\u6bd7\u90bb",
        )
        near_keywords = (
            "\u6700\u8fd1",
            "\u5f88\u8fd1",
            "\u6781\u8fd1",
            "\u90bb\u8fd1",
            "\u76f8\u8fde",
            "\u7d27\u5bc6\u76f8\u8fde",
            "\u9644\u8fd1",
            "\u5feb\u901f\u5230\u8fbe",
        )
        medium_keywords = (
            "\u9002\u4e2d",
            "\u4e2d\u7b49",
            "\u4e0d\u8fdc",
        )
        far_keywords = (
            "\u6700\u8fdc",
            "\u8f83\u8fdc",
            "\u5f88\u8fdc",
            "\u9065\u8fdc",
        )
        connected_keywords = (
            "\u8fde\u901a",
            "\u8fde\u63a5",
            "\u53ef\u8fbe",
            "navmesh",
            "\u5bfc\u822a",
        )

        best_relation = "unknown"
        best_distance = None
        best_evidence = ""
        start = 0
        lowered_description = description.lower()
        while True:
            match_index = description.find(other_region, start)
            if match_index < 0:
                break
            window_start = max(0, match_index - 24)
            window_end = min(len(description), match_index + len(other_region) + 24)
            window = description[window_start:window_end]
            lowered_window = lowered_description[window_start:window_end]
            numbers = [int(item) for item in re.findall(r"\d{1,3}", window)]
            distance_hint = min(numbers) if numbers else None
            window_relation = "unknown"
            if any(keyword in window for keyword in far_keywords):
                window_relation = "far"
            elif any(keyword in window for keyword in adjacent_keywords):
                window_relation = "adjacent"
            elif any(keyword in window for keyword in near_keywords):
                window_relation = "near"
            elif any(keyword in window for keyword in medium_keywords):
                window_relation = "medium"
            elif any(keyword in lowered_window for keyword in connected_keywords):
                window_relation = "connected"

            if distance_hint is not None:
                if distance_hint >= 65:
                    window_relation = "far"
                elif distance_hint <= 20 and window_relation not in {"far"}:
                    window_relation = "adjacent" if window_relation in {"adjacent", "connected", "unknown"} else window_relation
                elif distance_hint <= 35 and window_relation not in {"far", "adjacent"}:
                    window_relation = "near"
                elif distance_hint <= 55 and window_relation not in {"far", "near"}:
                    window_relation = "medium"

            if self._combine_region_relations(window_relation, best_relation) == window_relation:
                best_relation = window_relation
                best_distance = distance_hint
                best_evidence = window
            start = match_index + len(other_region)

        return {
            "relation": best_relation,
            "distance_hint": best_distance,
            "evidence": self._compact_text(best_evidence, 120),
            "connected": any(keyword in lowered_description for keyword in connected_keywords),
        }

    def _combine_region_relations(self, left_relation, right_relation):
        priority = {"far": 5, "adjacent": 4, "near": 3, "medium": 2, "connected": 1, "unknown": 0, "same": 6}
        left_priority = priority.get(left_relation, 0)
        right_priority = priority.get(right_relation, 0)
        return left_relation if left_priority >= right_priority else right_relation

    def _region_relation(self, source_region, target_region):
        if not source_region or not target_region:
            return "unknown"
        if source_region == target_region:
            return "same"
        return self.region_relationships.get(source_region, {}).get(target_region, {}).get("relation", "unknown")

    def _regions_are_far(self, source_region, target_region):
        return self._region_relation(source_region, target_region) == "far"

    def _build_move_transitions(self):
        timeline_root = self._extract_timeline_root(self.raw_script_json)
        if timeline_root is None:
            return []

        current_positions = OrderedDict()
        for item in timeline_root.get("initial position", []):
            if not isinstance(item, dict):
                continue
            character = item.get("character")
            position_id = item.get("position") or item.get("position_id")
            if not isinstance(character, str) or not character.strip():
                continue
            if not isinstance(position_id, str) or not position_id.strip():
                continue
            current_positions[character.strip()] = position_id.strip()

        scene = timeline_root.get("scene", [])
        transitions = []
        for beat_index, beat in enumerate(scene, start=1):
            if not isinstance(beat, dict):
                continue
            shot_description = self._compact_text(self._stringify_context_value(beat.get("shot_description")), 240)
            content = self._compact_text(self._stringify_context_value(beat.get("content")), 160)
            move_text = " ".join(
                self._stringify_context_value(action.get("motion_detail"))
                for action in beat.get("actions", [])
                if isinstance(action, dict)
            )
            for move in self._parse_timeline_moves(beat):
                source_position = current_positions.get(move["character"], "")
                destination_position = move["position_id"]
                transition_text = " | ".join([content, shot_description, move_text])
                next_beat = scene[beat_index] if beat_index < len(scene) and isinstance(scene[beat_index], dict) else {}
                next_speaker = self._stringify_context_value(next_beat.get("speaker"))
                next_content = self._compact_text(self._stringify_context_value(next_beat.get("content")), 160)
                next_shot_description = self._compact_text(
                    self._stringify_context_value(next_beat.get("shot_description")),
                    240,
                )
                transitions.append(
                    {
                        "beat_index": beat_index,
                        "character": move["character"],
                        "source_position_id": source_position,
                        "destination_position_id": destination_position,
                        "shot_description": shot_description,
                        "move_text": self._compact_text(move_text, 200),
                        "has_following_beat": beat_index < len(scene),
                        "next_beat_speaker": next_speaker,
                        "next_beat_content": next_content,
                        "next_beat_shot_description": next_shot_description,
                        "explicit_long_relocation": self._text_signals_long_relocation(transition_text),
                        "exit_like_transition": self._text_signals_exit_or_withdrawal(transition_text),
                        "prefer_new_local_region": self._move_prefers_new_local_region(
                            transition_text,
                            next_content,
                            next_shot_description,
                            beat_index < len(scene),
                            self._text_signals_long_relocation(transition_text),
                            self._text_signals_exit_or_withdrawal(transition_text),
                        ),
                    }
                )
                current_positions[move["character"]] = destination_position

            for current in beat.get("current position", []) if isinstance(beat.get("current position"), list) else []:
                if not isinstance(current, dict):
                    continue
                character = current.get("character")
                position_id = current.get("position") or current.get("position_id")
                if not isinstance(character, str) or not character.strip():
                    continue
                if not isinstance(position_id, str) or not position_id.strip():
                    continue
                current_positions[character.strip()] = position_id.strip()

        return transitions

    def _text_signals_long_relocation(self, text):
        lowered = text.lower()
        long_move_markers = (
            "after a while",
            "later",
            "long walk",
            "long travel",
            "cross the whole scene",
            "\u8d70\u5f88\u8fdc",
            "\u8d70\u4e86\u5f88\u4e45",
            "\u7a7f\u8fc7\u6574\u4e2a",
            "\u4e00\u6bb5\u65f6\u95f4\u540e",
            "\u7a0d\u540e",
            "\u8d76\u5230",
            "\u8fdc\u5904",
        )
        return any(marker in lowered or marker in text for marker in long_move_markers)

    def _text_signals_exit_or_withdrawal(self, text):
        lowered = text.lower()
        markers = (
            "exit",
            "exiting",
            "leave the scene",
            "leaving the scene",
            "withdraw",
            "withdraws",
            "retreat",
            "retreats",
            "slip away",
            "back away",
            "distant background",
            "background observer",
            "passive observer",
            "outside main interaction",
            "hideaway",
            "secluded",
            "\u79bb\u5f00",
            "\u9000\u573a",
            "\u9000\u51fa",
            "\u64a4\u79bb",
            "\u9000\u5230\u80cc\u666f",
            "\u8fdc\u5904\u80cc\u666f",
            "\u80cc\u666f\u89c2\u5bdf",
            "\u88ab\u52a8\u89c2\u5bdf",
            "\u65c1\u89c2",
            "\u8131\u79bb\u4e3b\u4ea4\u4e92",
            "\u9690\u853d",
            "\u9690\u853d\u5904",
        )
        return any(marker in lowered or marker in text for marker in markers)

    def _move_prefers_new_local_region(
        self,
        transition_text,
        next_content,
        next_shot_description,
        has_following_beat,
        explicit_long_relocation,
        exit_like_transition,
    ):
        if not has_following_beat or explicit_long_relocation or exit_like_transition:
            return False
        combined = " | ".join(
            part for part in (transition_text, next_content, next_shot_description) if isinstance(part, str) and part.strip()
        )
        lowered = combined.lower()
        markers = (
            "approach",
            "approaches",
            "arrive",
            "arrives",
            "regroup",
            "regroups",
            "join",
            "joins",
            "toward",
            "towards",
            "near",
            "gather",
            "gathers",
            "presentation",
            "centered",
            "in front of",
            "public",
            "foreground",
            "move closer",
            "走向",
            "来到",
            "来到面前",
            "靠近",
            "接近",
            "汇合",
            "聚到",
            "聚集",
            "来到中央",
            "来到前景",
            "公开",
            "在前景",
            "站到",
            "面对",
        )
        return any(marker in lowered or marker in combined for marker in markers)

    def _move_requires_local_continuity(self, transition):
        return bool(transition)

    def _enforce_move_region_continuity(self, groups, singles):
        if not self.move_transitions:
            return groups, singles

        adjusted_groups = copy.deepcopy(groups)
        adjusted_singles = copy.deepcopy(singles)

        for transition in self.move_transitions:
            if not self._move_requires_local_continuity(transition):
                continue
            owner_by_position, region_by_position = self._build_stage2_owner_maps(adjusted_groups, adjusted_singles)
            source_position_id = transition.get("source_position_id")
            destination_position_id = transition.get("destination_position_id")
            source_region = region_by_position.get(source_position_id, "")
            destination_region = region_by_position.get(destination_position_id, "")
            if not source_region or not destination_region or source_region == destination_region:
                continue
            if not self._regions_are_far(source_region, destination_region):
                continue

            owner = owner_by_position.get(destination_position_id)
            if owner is None:
                continue

            replacement_region = self._choose_continuous_owner_region(
                owner,
                source_region,
                adjusted_groups,
                adjusted_singles,
                region_by_position,
            )
            if not replacement_region or replacement_region == destination_region:
                continue

            self._apply_owner_region(owner, replacement_region, adjusted_groups, adjusted_singles)

        return adjusted_groups, adjusted_singles

    def _build_stage2_owner_maps(self, groups, singles):
        owner_by_position = {}
        region_by_position = {}
        for index, group in enumerate(groups):
            region = group.get("region", "")
            for item in group.get("positions", []):
                position_id = item.get("position_id")
                if not position_id:
                    continue
                owner_by_position[position_id] = ("group", index)
                region_by_position[position_id] = region
        for index, single in enumerate(singles):
            position_id = single.get("position_id")
            if not position_id:
                continue
            owner_by_position[position_id] = ("single", index)
            region_by_position[position_id] = single.get("region", "")
        return owner_by_position, region_by_position

    def _choose_continuous_owner_region(self, owner, anchor_region, groups, singles, region_by_position):
        owner_type, owner_index = owner
        if owner_type == "group":
            group = groups[owner_index]
            position_ids = [item["position_id"] for item in group.get("positions", [])]
            context = self._group_context(group.get("positions", []))
        else:
            single = singles[owner_index]
            position_ids = [single["position_id"]]
            context = self.position_map.get(single["position_id"], {}).get("context", "")

        source_regions = []
        relevant_transitions = []
        for transition in self.move_transitions:
            if transition.get("destination_position_id") not in position_ids:
                continue
            relevant_transitions.append(transition)
            source_region = region_by_position.get(transition.get("source_position_id"), "")
            if source_region and source_region not in source_regions:
                source_regions.append(source_region)
        if anchor_region and anchor_region not in source_regions:
            source_regions.append(anchor_region)

        candidates = []
        for region_name in self.region_names:
            if any(self._regions_are_far(source_region, region_name) for source_region in source_regions):
                continue
            candidates.append(region_name)
        if not candidates:
            candidates = [region_name for region_name in self.region_names if not self._regions_are_far(anchor_region, region_name)]
        if not candidates:
            candidates = list(self.region_names)

        prefer_new_local_region = any(
            transition.get("prefer_new_local_region") and not transition.get("exit_like_transition")
            for transition in relevant_transitions
        )
        exit_like_transition = any(transition.get("exit_like_transition") for transition in relevant_transitions)

        best_region = candidates[0]
        best_score = None
        for region_name in candidates:
            score = self._score_region_for_context(region_name, context)
            for source_region in source_regions:
                relation = self._region_relation(source_region, region_name)
                if prefer_new_local_region:
                    if region_name == source_region:
                        score += 6
                    elif relation == "adjacent":
                        score += 7
                    elif relation in {"near", "connected"}:
                        score += 6
                    elif relation == "medium":
                        score += 3
                    elif relation == "unknown":
                        score += 0.5
                    elif relation == "far":
                        score -= 50
                elif exit_like_transition:
                    if region_name == source_region:
                        score += 2
                    elif relation == "adjacent":
                        score += 6
                    elif relation in {"near", "connected"}:
                        score += 5
                    elif relation == "medium":
                        score += 4
                    elif relation == "unknown":
                        score += 0.5
                    elif relation == "far":
                        score -= 50
                elif region_name == source_region:
                    score += 8
                elif relation == "adjacent":
                    score += 6
                elif relation in {"near", "connected"}:
                    score += 5
                elif relation == "medium":
                    score += 2
                elif relation == "unknown":
                    score += 0.5
                elif relation == "far":
                    score -= 50
            if best_score is None or score > best_score:
                best_score = score
                best_region = region_name
        return best_region

    def _apply_owner_region(self, owner, new_region, groups, singles):
        owner_type, owner_index = owner
        if owner_type == "group":
            groups[owner_index]["region"] = new_region
            groups[owner_index]["reason"] = f"Final selected region is {new_region}. Updated to satisfy move-linked non-far constraints."
            return

        single = singles[owner_index]
        single["region"] = new_region
        context = self.position_map.get(single["position_id"], {}).get("context", "")
        single["neartarget"] = self._choose_neartarget(new_region, context)
        single["lookat"] = self._choose_single_lookat(new_region, context, single["neartarget"])
        single["reason"] = f"Final selected region is {new_region}. Updated to satisfy move-linked non-far constraints."

    def _collect_position_episode_ids(self, position_ids, active_only):
        position_ids = [position_id for position_id in position_ids if position_id in self.position_map]
        if not position_ids:
            return []
        mapping = self.position_active_episode_map if active_only else self.position_visible_episode_map
        collected = []
        seen = set()
        for position_id in position_ids:
            for episode_id in mapping.get(position_id, []):
                if episode_id not in seen:
                    seen.add(episode_id)
                    collected.append(episode_id)
        return collected

    def _build_move_position_links(self):
        links = []
        for transition in self.move_transitions:
            source_position_id = transition.get("source_position_id", "")
            destination_position_id = transition.get("destination_position_id", "")
            links.append(
                {
                    "beat_index": transition.get("beat_index"),
                    "character": transition.get("character", ""),
                    "source_position_id": source_position_id,
                    "source_character": self.position_map.get(source_position_id, {}).get("character", ""),
                    "destination_position_id": destination_position_id,
                    "destination_character": self.position_map.get(destination_position_id, {}).get("character", ""),
                    "next_beat_speaker": transition.get("next_beat_speaker", ""),
                    "next_beat_content": transition.get("next_beat_content", ""),
                    "next_beat_shot_description": transition.get("next_beat_shot_description", ""),
                    "constraint": (
                        "These two position_ids are directly linked by a script move. "
                        "They must not end up in far-apart regions."
                    ),
                }
            )
        return links

    def _build_move_group_links(self):
        if self.stage1_result is None:
            return []
        owner_by_position = {}
        for group in self.stage1_result.get("groups", []):
            group_id = group.get("group_id", "")
            for item in group.get("positions", []):
                position_id = item.get("position_id")
                if position_id:
                    owner_by_position[position_id] = {"owner_type": "group", "owner_id": group_id}
        for single in self.stage1_result.get("singles", []):
            position_id = single.get("position_id")
            if position_id:
                owner_by_position[position_id] = {"owner_type": "single", "owner_id": position_id}

        aggregated = OrderedDict()
        for transition in self.move_transitions:
            destination_position_id = transition.get("destination_position_id", "")
            source_position_id = transition.get("source_position_id", "")
            owner = owner_by_position.get(destination_position_id)
            if not owner:
                continue
            key = (owner["owner_type"], owner["owner_id"])
            entry = aggregated.setdefault(
                key,
                {
                    "owner_type": owner["owner_type"],
                    "owner_id": owner["owner_id"],
                    "destination_positions": [],
                    "source_positions": [],
                    "source_owners": [],
                    "constraint": (
                        "This owner contains moved destination positions. Its candidate region must not be far from any linked "
                        "source position or source owner region."
                    ),
                },
            )
            if destination_position_id and destination_position_id not in entry["destination_positions"]:
                entry["destination_positions"].append(destination_position_id)
            if source_position_id and source_position_id not in entry["source_positions"]:
                entry["source_positions"].append(source_position_id)
            source_owner = owner_by_position.get(source_position_id)
            source_owner_id = source_owner["owner_id"] if source_owner else source_position_id
            if source_owner_id and source_owner_id not in entry["source_owners"]:
                entry["source_owners"].append(source_owner_id)
        return list(aggregated.values())

    def _build_move_region_hints(self):
        hints = []
        for transition in self.move_transitions:
            hints.append(
                {
                    "beat_index": transition["beat_index"],
                    "character": transition["character"],
                    "source_position_id": transition["source_position_id"],
                    "destination_position_id": transition["destination_position_id"],
                    "shot_description": transition.get("shot_description", ""),
                    "move_text": transition.get("move_text", ""),
                    "has_following_beat": bool(transition.get("has_following_beat")),
                    "explicit_long_relocation": bool(transition.get("explicit_long_relocation")),
                    "exit_like_transition": bool(transition.get("exit_like_transition")),
                    "next_beat_speaker": transition.get("next_beat_speaker", ""),
                    "next_beat_content": transition.get("next_beat_content", ""),
                    "next_beat_shot_description": transition.get("next_beat_shot_description", ""),
                    "instruction": (
                        "Because the move finishes before the next beat starts, active post-move dialogue should stay in a "
                        "spatially plausible same / adjacent / near / medium region relative to the source. "
                        "Staying in the same region is valid whenever it best serves the screenplay. "
                        "If this move instead creates an isolated exit-like single, or a coordinated exit / withdrawal group, you may "
                        "choose an adjacent or medium-distance retreat region that better fits that composition. "
                        "You must not use a far-apart region pair as the source and destination of this move. "
                        "connected=true does not override a direct far relation."
                    ),
                }
            )

        return hints

    def _validate_move_region_continuity(self, plan):
        owner_by_position, region_by_position = self._build_stage2_owner_maps(
            plan.get("groups", []),
            plan.get("singles", []),
        )
        for transition in self.move_transitions:
            if not self._move_requires_local_continuity(transition):
                continue
            source_position_id = transition.get("source_position_id", "")
            destination_position_id = transition.get("destination_position_id", "")
            source_region = region_by_position.get(source_position_id, "")
            destination_region = region_by_position.get(destination_position_id, "")
            if not source_region or not destination_region:
                continue
            if self._regions_are_far(source_region, destination_region):
                owner = owner_by_position.get(destination_position_id)
                raise ValueError(
                    "Move-linked positions must not end up in far-apart regions: "
                    f"{source_position_id!r} ({source_region}) -> {destination_position_id!r} ({destination_region}), "
                    f"owner={owner!r}, beat_index={transition.get('beat_index')}"
                )

    def _choose_neartarget(self, region, context):
        targets = self.region_targets.get(region, [])
        if not targets:
            raise ValueError(f"Region {region!r} has no targets.")
        lowered = context.lower()
        best_target = None
        best_score = -1
        for target in targets:
            score = 0
            if target.lower() in lowered or target in context:
                score += 10
            if target == self.where:
                score -= 5
            if "center" in target.lower():
                score -= 1
            if score > best_score:
                best_score = score
                best_target = target
        return best_target or targets[0]

    def _choose_single_lookat(self, region, context, neartarget):
        targets = self.region_targets.get(region, [])
        if neartarget in targets:
            return neartarget
        return targets[0]

    def _choose_layout(self, people_count, context):
        candidates = [layout for layout in self.layouts if layout["min_people"] <= people_count <= layout["max_people"]]
        if not candidates:
            raise ValueError(f"No layout supports {people_count} people.")
        if people_count == 2:
            for candidate in candidates:
                if candidate["layout"] == "two_person":
                    return "two_person"
        if people_count == 3:
            for preferred in ("triangle", "L_shape", "line"):
                for candidate in candidates:
                    if candidate["layout"] == preferred:
                        return preferred
        if people_count == 4:
            for preferred in ("square", "arc", "line", "layered", "cluster"):
                for candidate in candidates:
                    if candidate["layout"] == preferred:
                        return preferred

        lowered = context.lower()
        best_layout = candidates[0]["layout"]
        best_score = -1
        for candidate in candidates:
            score = 0
            if candidate["layout"].lower() in lowered:
                score += 5
            for use_case in candidate.get("use_case", []):
                if isinstance(use_case, str) and use_case.lower() in lowered:
                    score += 2
            score -= (candidate["max_people"] - candidate["min_people"]) * 0.01
            if score > best_score:
                best_score = score
                best_layout = candidate["layout"]
        return best_layout

    def _choose_group_lookat(self, stage1_group):
        if len(stage1_group["positions"]) <= 2:
            return {"mode": "center"}
        return {"mode": "target", "target_character": stage1_group["positions"][0]["character"]}

    def _layout_supports_size(self, layout_name, people_count):
        layout = self.layout_map[layout_name]
        return layout["min_people"] <= people_count <= layout["max_people"]

    def _group_context(self, positions):
        return self._compact_text(" | ".join(self.position_map[item["position_id"]].get("context", "") for item in positions))

    def _position_ref(self, position_id):
        return {"position_id": position_id, "character": self.position_map[position_id]["character"]}

    def _texts_indicate_interaction(self, text, self_character, other_character):
        if not text:
            return False
        if other_character in text and self_character in text:
            return True
        keywords = (
            "dialogue",
            "talk",
            "speak",
            "conversation",
            "interaction",
            "argue",
            "respond",
            "reply",
            "ask",
            "answer",
            "face",
            "speaker=",
            "content=",
            "对话",
            "交谈",
            "回应",
            "回答",
            "说",
            "问",
            "看向",
        )
        return other_character in text and any(keyword in lowered or keyword in text for keyword in keywords)

    def _safe_reason(self, value, fallback):
        if isinstance(value, str) and value.strip():
            return value.strip()
        return fallback

    def _sort_groups_by_position_order(self, groups):
        def key_fn(group):
            indices = [self.position_order.index(item["position_id"]) for item in group["positions"]]
            return min(indices), indices
        return sorted(groups, key=key_fn)

    def _sort_singles_by_position_order(self, singles):
        return sorted(singles, key=lambda item: self.position_order.index(item["position_id"]))

    def _sort_final_groups(self, groups):
        return sorted(groups, key=lambda item: int(item["group_id"][1:]) if item["group_id"][1:].isdigit() else item["group_id"])

    def _sort_final_singles(self, singles):
        return sorted(singles, key=lambda item: self.position_order.index(item["position_id"]))

    def _find_by_key(self, items, key, value):
        for item in items:
            if item.get(key) == value:
                return item
        raise KeyError(f"Could not find item with {key}={value!r}")


def _default_json_path(filename):
    for candidate in (Path(filename), Path("Assets") / "Json" / filename):
        if candidate.exists():
            return str(candidate)
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Generate position_plan.json using a DeepSeek-powered PositionAgent.")
    parser.add_argument("--script", default=_default_json_path("script.json"), help="Path to script_json.")
    parser.add_argument("--scene-info", default=_default_json_path("LotusTown_scene_info.json"), help="Path to scene_info_json.")
    parser.add_argument("--template", default=_default_json_path("template.json"), help="Path to position_template_json.")
    parser.add_argument("--position-lib", default=_default_json_path("PositionLib.json"), help="Path to position_lib_json.")
    parser.add_argument(
        "--api-key",
        default=os.environ.get("DEEPSEEK_API_KEY", ""),
        help="DeepSeek API key. You can also hardcode a default value here for local testing.",
    )
    parser.add_argument(
        "--api-url",
        default="https://api.deepseek.com/chat/completions",
        help="DeepSeek chat completions endpoint.",
    )
    parser.add_argument(
        "--model",
        default="deepseek-chat",
        help="DeepSeek model name.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(Path("Assets") / "Json"),
        help="Directory used to save position_plan.json and position_detail.json.",
    )
    parser.add_argument(
        "--stage-output-dir",
        default=str(Path("Assets") / "Json" / "AgentStage"),
        help="Directory used to save staged LLM JSON outputs.",
    )
    args = parser.parse_args(argv)

    missing = []
    if not args.script:
        missing.append("--script")
    if not args.scene_info:
        missing.append("--scene-info")
    if not args.template:
        missing.append("--template")
    if not args.position_lib:
        missing.append("--position-lib")
    if missing:
        raise SystemExit("Missing required input paths: " + ", ".join(missing))

    agent = PositionAgent(
        script_json=args.script,
        scene_info_json=args.scene_info,
        template_json=args.template,
        position_lib_json=args.position_lib,
        api_key=args.api_key,
        api_url=args.api_url,
        model=args.model,
        output_dir=args.output_dir,
        stage_output_dir=args.stage_output_dir,
    )
    agent.run()
    sys.stdout.write("[PositionAgent] Completed.\n")
    sys.stdout.write(f"[PositionAgent] position_plan: {Path(args.output_dir) / 'position_plan.json'}\n")
    sys.stdout.write(f"[PositionAgent] position_detail: {Path(args.output_dir) / 'position_detail.json'}\n")


if __name__ == "__main__":
    main()
