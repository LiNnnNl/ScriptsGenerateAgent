#!/usr/bin/env python3
"""
Standalone PositionAgent CLI.

Example:
python position_agent_standalone.py ^
  --scene-export-path "D:\\work\\SceneExport.json" ^
  --script-file-path "D:\\work\\scene_script.json" ^
  --positions-template-path "D:\\work\\positions_template.json" ^
  --output-path "D:\\work\\position.json"
"""

import argparse
import copy
import json
import os
import re
import socket
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set


def log(message: str) -> None:
    print(f"[PositionAgentPy] {message}", file=sys.stderr, flush=True)


def json_pretty(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def truncate(text: str, max_chars: int) -> str:
    if not text or len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...[truncated]"


def parse_json_object(text: str, label: str) -> Dict[str, Any]:
    try:
        value = json.loads(text)
    except Exception as exc:
        raise RuntimeError(f"Failed to parse {label} as JSON object.") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"Failed to parse {label} as JSON object.")
    return value


def parse_json_array(text: str, label: str) -> List[Any]:
    try:
        value = json.loads(text)
    except Exception as exc:
        raise RuntimeError(f"Failed to parse {label} as JSON array.") from exc
    if not isinstance(value, list):
        raise RuntimeError(f"Failed to parse {label} as JSON array.")
    return value


def normalize_position_id(raw_position_id: Optional[str]) -> str:
    if not raw_position_id or not raw_position_id.strip():
        return ""
    match = re.search(r"(\d+)", raw_position_id)
    if not match:
        return raw_position_id.strip()
    return f"Position {match.group(1)}"


def extract_position_number(position_id: Optional[str]) -> int:
    match = re.search(r"(\d+)", position_id or "")
    if not match:
        return sys.maxsize
    try:
        return int(match.group(1))
    except ValueError:
        return sys.maxsize


def add_non_empty(target: Set[str], value: Optional[str]) -> None:
    if value and value.strip():
        target.add(value.strip())


def strip_code_fence(content: str) -> str:
    trimmed = (content or "").strip()
    if not trimmed.startswith("```"):
        return trimmed

    first_line_end = trimmed.find("\n")
    if first_line_end < 0:
        return trimmed.strip("`")

    without_header = trimmed[first_line_end + 1 :]
    closing_fence = without_header.rfind("```")
    if closing_fence >= 0:
        without_header = without_header[:closing_fence]
    return without_header.strip()


def extract_assistant_json(content: str, stage_name: str) -> Dict[str, Any]:
    cleaned = strip_code_fence(content).strip()
    try:
        return parse_json_object(cleaned, f"{stage_name} assistant content")
    except Exception:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            return parse_json_object(cleaned[start : end + 1], f"{stage_name} assistant content")
        raise


def looks_like_positions_map(obj: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(obj, dict):
        return False
    for value in obj.values():
        if not isinstance(value, dict):
            continue
        if "position" in value or "region" in value or "neartarget" in value:
            return True
    return False


def extract_positions_node(root: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(root, dict):
        return None
    positions = root.get("positions")
    if isinstance(positions, dict):
        return positions
    # New template style: { "SceneName": { "Position 1": {...} } }
    # If root is wrapped by exactly one scene key, unwrap it.
    if len(root) == 1:
        only_value = next(iter(root.values()))
        if isinstance(only_value, dict) and looks_like_positions_map(only_value):
            return only_value
    return root if looks_like_positions_map(root) else None


def find_scene_document(root: Optional[Dict[str, Any]], scene_name: Optional[str]) -> Optional[Dict[str, Any]]:
    if not isinstance(root, dict):
        return None

    if scene_name and root.get("where") == scene_name:
        return root

    direct_scene = root.get(scene_name) if scene_name else None
    if isinstance(direct_scene, dict):
        return direct_scene

    scenes = root.get("scenes")
    if isinstance(scenes, list):
        for scene in scenes:
            if isinstance(scene, dict) and scene.get("where") == scene_name:
                return scene

    return None


@dataclass
class AgentPaths:
    scene_export_path: str
    script_path: str
    template_path: str
    output_directory: str


@dataclass
class AgentInputFiles:
    scene_export_text: str
    script_text: str
    template_text: str


@dataclass
class StageArtifact:
    stage_name: str
    raw_response: str
    json_content: Dict[str, Any]


@dataclass
class SceneRequirement:
    scene_name: str = ""
    scene_description: str = ""
    event_count: int = 0
    characters: Set[str] = field(default_factory=set)
    speakers: Set[str] = field(default_factory=set)
    position_ids: Set[str] = field(default_factory=set)


@dataclass
class ScriptDigest:
    scenes: List[SceneRequirement] = field(default_factory=list)
    raw_token: List[Any] = field(default_factory=list)
    prompt_payload: Dict[str, Any] = field(default_factory=dict)

    @property
    def scene_count(self) -> int:
        return len(self.scenes)


@dataclass
class PositionAgentConfig:
    deepseek_api_key: str
    api_url: str
    model: str
    temperature: float
    timeout_seconds: int
    force_json_response: bool
    scene_export_path: str
    script_file_path: str
    positions_template_path: str
    output_directory: str
    output_file_name: str
    save_stage_artifacts: bool
    max_distinct_objects_per_region: int
    max_sample_objects_per_region: int
    max_scene_export_fallback_characters: int
    include_looktarget_field: bool


class PositionAgentRunner:
    def __init__(self, config: PositionAgentConfig) -> None:
        self.config = config

    def run(self) -> str:
        api_key = self.resolve_api_key()
        paths = self.resolve_agent_paths()

        log("Loading input files...")
        input_files = self.load_input_files(paths)

        log("Building local digests...")
        script_digest = self.build_script_digest(input_files.script_text)
        scene_digest = self.build_scene_export_digest(input_files.scene_export_text)
        template_root = parse_json_object(input_files.template_text, "positions template")
        expanded_template = self.build_expanded_template(template_root, script_digest)

        log("Running stage 1/3: screenplay parsing...")
        stage1 = self.run_stage(
            "stage1_script_parse",
            self.build_script_parse_system_prompt(),
            self.build_script_parse_user_prompt(input_files.script_text, script_digest),
            api_key,
            paths,
        )

        log("Running stage 2/3: position planning...")
        stage2 = self.run_stage(
            "stage2_position_plan",
            self.build_position_planning_system_prompt(),
            self.build_position_planning_user_prompt(stage1.json_content, scene_digest, expanded_template),
            api_key,
            paths,
        )

        log("Running stage 3/3: final JSON generation...")
        stage3 = self.run_stage(
            "stage3_json_generate",
            self.build_json_generation_system_prompt(script_digest.scene_count > 1),
            self.build_json_generation_user_prompt(stage1.json_content, stage2.json_content, expanded_template),
            api_key,
            paths,
        )

        log("Normalizing final output...")
        final_document = self.normalize_final_document(stage3.json_content, expanded_template, script_digest)

        os.makedirs(paths.output_directory, exist_ok=True)
        output_path = os.path.join(paths.output_directory, self.config.output_file_name)
        self.write_text_file(output_path, json_pretty(final_document))
        return output_path

    def resolve_api_key(self) -> str:
        api_key = (self.config.deepseek_api_key or "").strip()
        if not api_key:
            api_key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
        if not api_key:
            raise RuntimeError("DeepSeek API key is empty. Fill the CLI argument or set DEEPSEEK_API_KEY.")
        return api_key

    def resolve_agent_paths(self) -> AgentPaths:
        paths = AgentPaths(
            scene_export_path=self.resolve_existing_file_path(self.config.scene_export_path),
            script_path=self.resolve_existing_file_path(self.config.script_file_path),
            template_path=self.resolve_existing_file_path(self.config.positions_template_path),
            output_directory=self.config.output_directory,
        )

        if not os.path.isfile(paths.scene_export_path):
            raise FileNotFoundError(f"Scene export file not found: {paths.scene_export_path}")
        if not os.path.isfile(paths.script_path):
            raise FileNotFoundError(f"Script file not found: {paths.script_path}")
        if not os.path.isfile(paths.template_path):
            raise FileNotFoundError(f"Position template file not found: {paths.template_path}")
        return paths

    @staticmethod
    def resolve_existing_file_path(configured_path: str) -> str:
        if not configured_path or not configured_path.strip():
            return configured_path
        if os.path.isfile(configured_path):
            return configured_path

        directory = os.path.dirname(configured_path)
        file_name = os.path.basename(configured_path)
        file_stem, _ = os.path.splitext(file_name)
        if directory and file_stem:
            fallback_path = os.path.join(directory, file_stem, file_name)
            if os.path.isfile(fallback_path):
                log(f"Using fallback path: {fallback_path}")
                return fallback_path
        return configured_path

    @staticmethod
    def load_input_files(paths: AgentPaths) -> AgentInputFiles:
        return AgentInputFiles(
            scene_export_text=PositionAgentRunner.read_text_file(paths.scene_export_path),
            script_text=PositionAgentRunner.read_text_file(paths.script_path),
            template_text=PositionAgentRunner.read_text_file(paths.template_path),
        )

    @staticmethod
    def read_text_file(path: str) -> str:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()

    @staticmethod
    def write_text_file(path: str, content: str) -> None:
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)

    def build_script_digest(self, script_text: str) -> ScriptDigest:
        script_array = parse_json_array(script_text, "screenplay")
        digest = ScriptDigest()
        digest.raw_token = script_array

        scene_map: Dict[str, SceneRequirement] = {}
        for scene_token in script_array:
            if not isinstance(scene_token, dict):
                continue

            scene_info = scene_token.get("scene information")
            if not isinstance(scene_info, dict):
                scene_info = {}

            scene_name = scene_info.get("where") or "Unknown"
            requirement = scene_map.get(scene_name)
            if requirement is None:
                requirement = SceneRequirement(scene_name=scene_name)
                scene_map[scene_name] = requirement
                digest.scenes.append(requirement)

            requirement.scene_description = scene_info.get("what") or ""

            who_array = scene_info.get("who")
            if isinstance(who_array, list):
                for who in who_array:
                    add_non_empty(requirement.characters, str(who))

            self.add_positions_from_array(scene_token.get("initial position"), requirement)

            events = scene_token.get("scene")
            if isinstance(events, list):
                requirement.event_count += len(events)
                for event in events:
                    if not isinstance(event, dict):
                        continue
                    add_non_empty(requirement.speakers, event.get("speaker"))
                    self.add_positions_from_array(event.get("current position"), requirement)
                    self.add_positions_from_move_token(event.get("move"), requirement)

        digest.prompt_payload = {
            "scene_count": len(digest.scenes),
            "scenes": [
                self.build_scene_requirement_json(scene)
                for scene in sorted(digest.scenes, key=lambda value: value.scene_name)
            ],
        }
        return digest

    @staticmethod
    def add_positions_from_array(positions: Any, requirement: SceneRequirement) -> None:
        if not isinstance(positions, list):
            return

        for item in positions:
            if not isinstance(item, dict):
                continue
            position_id = normalize_position_id(item.get("position"))
            if position_id:
                requirement.position_ids.add(position_id)

    @staticmethod
    def add_positions_from_move_token(move_token: Any, requirement: SceneRequirement) -> None:
        if isinstance(move_token, list):
            for move in move_token:
                if not isinstance(move, dict):
                    continue
                position_id = normalize_position_id(move.get("destination"))
                if position_id:
                    requirement.position_ids.add(position_id)
            return

        if isinstance(move_token, dict):
            position_id = normalize_position_id(move_token.get("destination"))
            if position_id:
                requirement.position_ids.add(position_id)

    @staticmethod
    def build_scene_requirement_json(requirement: SceneRequirement) -> Dict[str, Any]:
        used_positions = sorted(
            requirement.position_ids,
            key=lambda value: (extract_position_number(value), value),
        )
        return {
            "where": requirement.scene_name,
            "what": requirement.scene_description or "",
            "event_count": requirement.event_count,
            "characters": sorted(requirement.characters),
            "speakers": sorted(requirement.speakers),
            "used_positions": used_positions,
        }

    def build_scene_export_digest(self, scene_export_text: str) -> Dict[str, Any]:
        try:
            scene_export_root = parse_json_object(scene_export_text, "scene export")
            raw_regions = scene_export_root.get("regions")
            digest_regions: List[Dict[str, Any]] = []
            all_object_names: Set[str] = set()

            if isinstance(raw_regions, list):
                for region in raw_regions:
                    if not isinstance(region, dict):
                        continue
                    self.collect_region_object_names(region, all_object_names)
                    digest_regions.append(self.build_region_digest(region))

            return {
                "scene_name": scene_export_root.get("sceneName") or "",
                "exported_at": scene_export_root.get("exportedAt") or "",
                "region_count": len(digest_regions),
                "all_object_names": sorted(all_object_names),
                "regions": digest_regions,
            }
        except Exception as exc:
            return {
                "parse_error": str(exc),
                "raw_preview": truncate(scene_export_text, self.config.max_scene_export_fallback_characters),
            }

    @staticmethod
    def collect_region_object_names(region: Dict[str, Any], all_object_names: Set[str]) -> None:
        objects = region.get("objects")
        if not isinstance(objects, list):
            return
        for item in objects:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if isinstance(name, str) and name.strip():
                all_object_names.add(name)

    def build_region_digest(self, region: Dict[str, Any]) -> Dict[str, Any]:
        objects = region.get("objects")
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        if isinstance(objects, list):
            for item in objects:
                if not isinstance(item, dict):
                    continue
                name = item.get("name") or ""
                if not isinstance(name, str) or not name.strip():
                    continue
                grouped.setdefault(name, []).append(item)

        grouped_items = sorted(
            grouped.items(),
            key=lambda pair: (-len(pair[1]), pair[0]),
        )[: self.config.max_distinct_objects_per_region]

        candidate_names = [name for name, _ in grouped_items]
        sample_objects: List[Dict[str, Any]] = []
        for name, group in grouped_items[: self.config.max_sample_objects_per_region]:
            first = group[0]
            sample_objects.append(
                {
                    "name": name,
                    "count": len(group),
                    "sample_position": copy.deepcopy(first.get("position")) if first.get("position") is not None else {},
                }
            )

        return {
            "name": region.get("name") or "",
            "bounds_center": copy.deepcopy(region.get("boundsCenter")) if region.get("boundsCenter") is not None else {},
            "bounds_extents": copy.deepcopy(region.get("boundsExtents")) if region.get("boundsExtents") is not None else {},
            "object_count": region.get("objectCount") or 0,
            "candidate_object_names": candidate_names,
            "sample_objects": sample_objects,
        }

    def build_expanded_template(self, template_root: Dict[str, Any], script_digest: ScriptDigest) -> Dict[str, Any]:
        multi_scene = script_digest.scene_count > 1

        if not multi_scene:
            scene = script_digest.scenes[0] if script_digest.scenes else SceneRequirement()
            scene_name = scene.scene_name or template_root.get("where") or "Scene"
            scene_template_root = find_scene_document(template_root, scene_name) or template_root
            template_positions = extract_positions_node(scene_template_root) or extract_positions_node(template_root)
            prototype = self.get_template_prototype(template_positions)
            return {
                scene_name: self.build_scene_positions_template(scene.position_ids, template_positions, prototype),
            }

        template_positions = extract_positions_node(template_root)
        prototype = self.get_template_prototype(template_positions)
        multi_scene_document: Dict[str, Any] = {}
        for scene in sorted(script_digest.scenes, key=lambda value: value.scene_name):
            scene_template_root = find_scene_document(template_root, scene.scene_name) or template_root
            scene_template_positions = extract_positions_node(scene_template_root) or template_positions
            multi_scene_document[scene.scene_name] = self.build_scene_positions_template(
                scene.position_ids,
                scene_template_positions,
                prototype,
            )
        return multi_scene_document

    def get_template_prototype(self, template_positions: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if isinstance(template_positions, dict):
            for value in template_positions.values():
                if isinstance(value, dict):
                    return self.ensure_position_entry_shape(copy.deepcopy(value))
        return self.ensure_position_entry_shape({})

    def build_scene_positions_template(
        self,
        scene_position_ids: Iterable[str],
        template_positions: Optional[Dict[str, Any]],
        prototype: Dict[str, Any],
    ) -> Dict[str, Any]:
        ids: Set[str] = set()

        if scene_position_ids is not None:
            for position_id in scene_position_ids:
                normalized = normalize_position_id(position_id)
                if normalized:
                    ids.add(normalized)

        if not ids and isinstance(template_positions, dict):
            for key in template_positions.keys():
                normalized = normalize_position_id(key)
                if normalized:
                    ids.add(normalized)

        if not ids:
            ids.add("Position 1")

        result: Dict[str, Any] = {}
        for position_id in sorted(ids, key=lambda value: (extract_position_number(value), value)):
            source = template_positions.get(position_id) if isinstance(template_positions, dict) else None
            entry = copy.deepcopy(source) if isinstance(source, dict) else copy.deepcopy(prototype)
            result[position_id] = self.ensure_position_entry_shape(entry)
        return result

    def ensure_position_entry_shape(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        normalized = entry if isinstance(entry, dict) else {}

        if not isinstance(normalized.get("position"), list):
            normalized["position"] = []
        if not isinstance(normalized.get("sit_angle"), list):
            normalized["sit_angle"] = []
        if not isinstance(normalized.get("fixed_angle"), list):
            normalized["fixed_angle"] = []
        if normalized.get("region") is None:
            normalized["region"] = ""
        if normalized.get("neartarget") is None:
            normalized["neartarget"] = ""

        if self.config.include_looktarget_field:
            # Canonical output field is `lookat`, but keep backward compatibility
            # with old templates/model outputs that may still use `looktarget`.
            lookat_val = normalized.get("lookat")
            if lookat_val is None and normalized.get("looktarget") is not None:
                lookat_val = normalized.get("looktarget")
            normalized["lookat"] = lookat_val if lookat_val is not None else ""
            normalized.pop("looktarget", None)
        else:
            normalized.pop("looktarget", None)
            normalized.pop("lookat", None)

        return normalized

    @staticmethod
    def build_script_parse_system_prompt() -> str:
        return (
            "You are PositionAgent stage 1 for a Unity production pipeline.\n"
            "Read the screenplay JSON and return JSON only.\n"
            "Your job is to extract scene-level intent, characters, used position ids, and concise notes that help later position planning.\n"
            "Do not generate coordinates.\n"
            "Top-level schema:\n"
            "{\n"
            '  "script_summary": "string",\n'
            '  "scenes": [\n'
            "    {\n"
            '      "where": "string",\n'
            '      "story_context": "string",\n'
            '      "characters": ["name"],\n'
            '      "used_positions": ["Position N"],\n'
            '      "position_usage_notes": ["short note"]\n'
            "    }\n"
            "  ]\n"
            "}"
        )

    @staticmethod
    def build_script_parse_user_prompt(raw_script_text: str, script_digest: ScriptDigest) -> str:
        return (
            "Read the screenplay JSON and summarize the position requirements.\n"
            "Return JSON only.\n\n"
            "Local pre-digest:\n"
            + json_pretty(script_digest.prompt_payload)
            + "\n\nRaw screenplay JSON:\n"
            + raw_script_text
        )

    @staticmethod
    def build_position_planning_system_prompt() -> str:
        return (
            "You are PositionAgent stage 2 for a Unity production pipeline.\n"
            "Your job is to read the screenplay semantics, understand the dialogue and scene context, and plan standing positions for characters.\n"
            "Use the screenplay parse, scene export digest and template skeleton to plan position metadata.\n"
            "Return JSON only.\n"
            "Rules:\n"
            "- Plan exactly the positions that appear in the provided template skeleton.\n"
            "- Do not add positions that are not used by the screenplay.\n"
            "- Fill region for every position. Region must be selected from the provided regions.\n"
            "- Fill neartarget for every position. neartarget must be selected from the provided scene object names.\n"
            "- If looktarget is present in the template, fill it only when this position should face a specific object or character.\n"
            "- looktarget can be either a scene object name or a screenplay character name.\n"
            "- Leave looktarget as an empty string when there is no clear explicit facing target.\n"
            "- region should match the spatial meaning of the screenplay scene.\n"
            "- neartarget should be the most relevant nearby reference object for that position.\n"
            "- Prefer stable, reusable anchors such as walls, consoles, windows, seats, doors, railings or other clear environment objects.\n"
            "- Do not invent coordinates.\n"
            "- Do not rename position ids.\n"
            "Top-level schema:\n"
            "{\n"
            '  "scenes": [\n'
            "    {\n"
            '      "where": "string",\n'
            '      "positions": [\n'
            "        {\n"
            '          "position_id": "Position N",\n'
            '          "region": "string",\n'
            '          "neartarget": "string",\n'
            '          "looktarget": "string",\n'
            '          "reason": "short explanation"\n'
            "        }\n"
            "      ]\n"
            "    }\n"
            "  ]\n"
            "}"
        )

    @staticmethod
    def build_position_planning_user_prompt(
        stage1_json: Dict[str, Any],
        scene_digest: Dict[str, Any],
        expanded_template: Dict[str, Any],
    ) -> str:
        return (
            "Plan region, neartarget and optional looktarget for every screenplay position.\n"
            "Return JSON only.\n\n"
            "Task constraints:\n"
            "- Read the screenplay meaning, character interactions and dialogue focus.\n"
            "- Use only the positions already present in the template skeleton.\n"
            "- Do not create any extra Position entries.\n"
            "- region must come from scene regions.\n"
            "- neartarget must come from scene object names.\n"
            "- looktarget may be a scene object name or a screenplay character name when explicit facing is needed.\n"
            "- If no explicit facing target is needed, set looktarget to an empty string.\n\n"
            "Stage 1 screenplay parse:\n"
            + json_pretty(stage1_json)
            + "\n\nScene export digest:\n"
            + json_pretty(scene_digest)
            + "\n\nTemplate skeleton that stage 3 must follow:\n"
            + json_pretty(expanded_template)
        )

    @staticmethod
    def build_json_generation_system_prompt(multi_scene_document: bool) -> str:
        mode = "multi-scene" if multi_scene_document else "single-scene"
        return (
            "You are PositionAgent stage 3 for a Unity production pipeline.\n"
            f"Generate the final {mode} position JSON document.\n"
            "Return JSON only. No markdown fences.\n"
            "Rules:\n"
            "- Follow the provided template skeleton exactly.\n"
            "- Preserve every position id from the skeleton.\n"
            "- Fill region and neartarget.\n"
            "- Keep position, fixed_angle and sit_angle as arrays.\n"
            "- Leave position empty unless you have explicit coordinates from input.\n"
            "- Do not add commentary fields such as reason or notes.\n"
            "- If looktarget exists in the skeleton, keep it as a string.\n"
        )

    @staticmethod
    def build_json_generation_user_prompt(
        stage1_json: Dict[str, Any],
        stage2_json: Dict[str, Any],
        expanded_template: Dict[str, Any],
    ) -> str:
        return (
            "Generate the final JSON document now.\n"
            "Return JSON only.\n\n"
            "Stage 1 screenplay parse:\n"
            + json_pretty(stage1_json)
            + "\n\nStage 2 position plan:\n"
            + json_pretty(stage2_json)
            + "\n\nFinal template skeleton to fill:\n"
            + json_pretty(expanded_template)
        )

    def run_stage(
        self,
        stage_name: str,
        system_prompt: str,
        user_prompt: str,
        api_key: str,
        paths: AgentPaths,
    ) -> StageArtifact:
        raw_response = self.call_deepseek_chat_completion(system_prompt, user_prompt, api_key)
        json_response = extract_assistant_json(raw_response, stage_name)

        if self.config.save_stage_artifacts:
            self.save_stage_artifacts(stage_name, system_prompt, user_prompt, raw_response, paths)

        return StageArtifact(stage_name=stage_name, raw_response=raw_response, json_content=json_response)

    def call_deepseek_chat_completion(self, system_prompt: str, user_prompt: str, api_key: str) -> str:
        payload: Dict[str, Any] = {
            "model": self.config.model,
            "temperature": self.config.temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }

        if self.config.force_json_response:
            payload["response_format"] = {"type": "json_object"}

        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.config.api_url,
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

        timeout_seconds = max(10, int(self.config.timeout_seconds))
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                response_text = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            response_text = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"DeepSeek API error {exc.code}: {truncate(response_text, 2000)}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"DeepSeek request failed: {exc.reason}") from exc
        except socket.timeout as exc:
            raise TimeoutError(f"DeepSeek request timed out after {self.config.timeout_seconds} seconds.") from exc

        response_json = parse_json_object(response_text, "DeepSeek response")
        choices = response_json.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("DeepSeek response content is empty.")

        first_choice = choices[0] if isinstance(choices[0], dict) else {}
        message = first_choice.get("message") if isinstance(first_choice, dict) else {}
        content = message.get("content") if isinstance(message, dict) else ""
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("DeepSeek response content is empty.")

        return content

    def save_stage_artifacts(
        self,
        stage_name: str,
        system_prompt: str,
        user_prompt: str,
        raw_response: str,
        paths: AgentPaths,
    ) -> None:
        artifact_directory = os.path.join(paths.output_directory, "PositionAgentStages")
        os.makedirs(artifact_directory, exist_ok=True)

        prompt_path = os.path.join(artifact_directory, f"{stage_name}.prompt.txt")
        response_path = os.path.join(artifact_directory, f"{stage_name}.response.json")

        prompt_text = f"[SYSTEM]\n{system_prompt}\n\n[USER]\n{user_prompt}\n"
        self.write_text_file(prompt_path, prompt_text)
        self.write_text_file(response_path, raw_response)

    def normalize_final_document(
        self,
        model_output: Dict[str, Any],
        expanded_template: Dict[str, Any],
        script_digest: ScriptDigest,
    ) -> Dict[str, Any]:
        normalized = copy.deepcopy(expanded_template)
        multi_scene = script_digest.scene_count > 1

        if not multi_scene:
            scene_name = script_digest.scenes[0].scene_name if script_digest.scenes else None
            if not scene_name and isinstance(normalized, dict) and normalized:
                scene_name = next(iter(normalized.keys()))
            target_positions = normalized.get(scene_name) if scene_name else None
            source_scene = find_scene_document(model_output, scene_name)
            source_positions = extract_positions_node(source_scene or model_output)
            self.merge_positions(target_positions, source_positions)
            return normalized

        for scene_name, target_positions in normalized.items():
            if not isinstance(target_positions, dict):
                continue
            source_scene = find_scene_document(model_output, scene_name)
            source_positions = extract_positions_node(source_scene)
            self.merge_positions(target_positions, source_positions)

        return normalized

    def merge_positions(
        self,
        target_positions: Optional[Dict[str, Any]],
        source_positions: Optional[Dict[str, Any]],
    ) -> None:
        if not isinstance(target_positions, dict) or not isinstance(source_positions, dict):
            return

        for key, source_entry in source_positions.items():
            if not isinstance(source_entry, dict):
                continue

            target_entry = target_positions.get(key)
            if not isinstance(target_entry, dict):
                target_entry = self.ensure_position_entry_shape({})
                target_positions[key] = target_entry

            self.copy_whitelisted_field(source_entry, target_entry, "position")
            self.copy_whitelisted_field(source_entry, target_entry, "sit_angle")
            self.copy_whitelisted_field(source_entry, target_entry, "fixed_angle")
            self.copy_whitelisted_field(source_entry, target_entry, "region")
            self.copy_whitelisted_field(source_entry, target_entry, "neartarget")
            if self.config.include_looktarget_field:
                if "lookat" in source_entry:
                    self.copy_whitelisted_field(source_entry, target_entry, "lookat")
                elif "looktarget" in source_entry:
                    target_entry["lookat"] = copy.deepcopy(source_entry["looktarget"])

    @staticmethod
    def copy_whitelisted_field(source_entry: Dict[str, Any], target_entry: Dict[str, Any], field_name: str) -> None:
        if field_name not in source_entry:
            return
        target_entry[field_name] = copy.deepcopy(source_entry[field_name])


def add_bool_argument(parser: argparse.ArgumentParser, name: str, default: bool, help_text: str) -> None:
    dest = name.replace("-", "_")
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument(f"--{name}", dest=dest, action="store_true", help=help_text)
    group.add_argument(f"--no-{name}", dest=dest, action="store_false")
    parser.set_defaults(**{dest: default})


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Standalone Python PositionAgent pipeline.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--deepseek-api-key", default="sk-0638eec01b5f4064830d1594d1ab3857", help="If empty, DEEPSEEK_API_KEY will be used.")
    parser.add_argument("--api-url", default="https://api.deepseek.com/chat/completions")
    parser.add_argument("--model", default="deepseek-chat")
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    add_bool_argument(parser, "force-json-response", True, "Request json_object response format from DeepSeek.")

    parser.add_argument("--scene-export-path", required=True, help="Absolute or relative path to SceneExport.json")
    parser.add_argument("--script-file-path", required=True, help="Absolute or relative path to screenplay JSON")
    parser.add_argument("--positions-template-path", required=True, help="Absolute or relative path to positions template JSON")

    parser.add_argument("--output-path", default="position.json", help="Final output JSON path. Default: ./position.json")
    add_bool_argument(parser, "save-stage-artifacts", True, "Save prompt and response artifacts for each stage.")

    parser.add_argument("--max-distinct-objects-per-region", type=int, default=120)
    parser.add_argument("--max-sample-objects-per-region", type=int, default=16)
    parser.add_argument("--max-scene-export-fallback-characters", type=int, default=60000)
    add_bool_argument(parser, "include-looktarget-field", True, "Include looktarget in output entries.")
    return parser


def build_config(args: argparse.Namespace) -> PositionAgentConfig:
    output_path = os.path.abspath(args.output_path)
    output_directory = os.path.dirname(output_path) or os.getcwd()
    output_file_name = os.path.basename(output_path)

    return PositionAgentConfig(
        deepseek_api_key=args.deepseek_api_key,
        api_url=args.api_url,
        model=args.model,
        temperature=args.temperature,
        timeout_seconds=args.timeout_seconds,
        force_json_response=args.force_json_response,
        scene_export_path=args.scene_export_path,
        script_file_path=args.script_file_path,
        positions_template_path=args.positions_template_path,
        output_directory=output_directory,
        output_file_name=output_file_name,
        save_stage_artifacts=args.save_stage_artifacts,
        max_distinct_objects_per_region=args.max_distinct_objects_per_region,
        max_sample_objects_per_region=args.max_sample_objects_per_region,
        max_scene_export_fallback_characters=args.max_scene_export_fallback_characters,
        include_looktarget_field=args.include_looktarget_field,
    )


def main() -> int:
    parser = build_argument_parser()
    args = parser.parse_args()
    config = build_config(args)

    try:
        runner = PositionAgentRunner(config)
        output_path = runner.run()
        print(json.dumps({"ok": True, "output_path": output_path}, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
