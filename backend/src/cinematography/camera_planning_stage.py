import copy
import json
import re
from collections import OrderedDict
from pathlib import Path
import time


class CameraPlanningStage:
    STAGE_FILENAME = "director_stage3_camera_planning.json"
    ANALYSIS_STAGE_FILENAME = "director_stage3_substage1_camera_shot_analysis.json"
    ASSIGNMENT_STAGE_FILENAME = "director_stage3_substage2_camera_assignment.json"
    OUTPUT_FILENAME = "script_with_camera_plan.json"
    WINDOW_SIZE = 4

    VALID_SHOT_BLEND = {
        "Cut",
        "Ease In Out",
        "Ease In",
        "Ease Out",
        "Hard In",
        "Hard Out",
        "Linear",
        "Custom",
    }
    SHOT_BLEND_GUIDE = OrderedDict(
        [
            ("Cut", "瞬间从当前镜头切换到目标镜头，无任何过渡。"),
            ("Ease In Out", "镜头以平滑方式过渡，先慢慢加速再减速，整体自然流畅。"),
            ("Ease In", "镜头从静止缓慢开始，并逐渐加速进入目标状态。"),
            ("Ease Out", "镜头快速开始，并在接近目标时逐渐减速停下。"),
            ("Hard In", "镜头以突兀且快速的方式直接进入过渡，强调突然启动的冲击感。"),
            ("Hard Out", "镜头在结束时突然停止过渡，强调强烈的收尾或停顿感。"),
            ("Linear", "镜头以恒定速度从起点均匀过渡到终点，没有加速或减速。"),
            ("Custom", "使用自定义曲线控制过渡节奏，可实现特殊或复杂的镜头效果。"),
        ]
    )
    VALID_INTERACTION_CONTEXTS = {
        "dialogue_two_person",
        "dialogue_group",
        "monologue",
        "movement",
        "observation",
    }
    VALID_EMOTIONAL_INTENSITIES = {"low", "medium", "high"}
    VALID_EMOTIONAL_TONES = {
        "neutral",
        "joyful",
        "tense",
        "sad",
        "powerful",
        "vulnerable",
        "confused",
    }

    def __init__(
        self,
        script_json,
        scene_info_json,
        camera_lib_json,
        position_plan_json=None,
        position_detail_json=None,
        llm_client=None,
        output_dir=None,
        stage_output_dir=None,
    ):
        self.raw_script_json = self._load_json_like(script_json)
        self.scene_info_json = self._load_json_like(scene_info_json)
        self.raw_camera_lib_json = self._load_json_like(camera_lib_json)
        self.position_plan_json = self._load_json_like(position_plan_json) if position_plan_json is not None else None
        self.position_detail_json = self._load_json_like(position_detail_json) if position_detail_json is not None else None

        self.llm_client = llm_client
        self.output_dir = Path(output_dir) if output_dir else Path("Assets") / "Json"
        self.stage_output_dir = Path(stage_output_dir) if stage_output_dir else Path("Assets") / "Json" / "AgentStage"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.stage_output_dir.mkdir(parents=True, exist_ok=True)
        self.output_path = self.output_dir / self.OUTPUT_FILENAME

        self.script_payload = copy.deepcopy(self.raw_script_json)
        self.timeline_root = self._extract_timeline_root(self.script_payload)
        if self.timeline_root is None:
            raise ValueError("camera planning requires a timeline-style script json with a scene array.")

        self.where = self._resolve_where(self.timeline_root)
        self.initial_position_state = self._build_initial_position_state(self.timeline_root)
        self.region_map = self._build_region_map(self.scene_info_json)
        self.camera_lib_map = self._build_camera_lib_map(self.raw_camera_lib_json)
        self.valid_shot_types = set(self.camera_lib_map.keys())
        self.position_context_by_id, self.group_context_by_id = self._build_position_contexts()

        self.analysis_results = []
        self.assignment_results = []
        self.enriched_script = None

    def run(self):
        scene = self.timeline_root.get("scene", [])
        if not isinstance(scene, list):
            raise ValueError("script_json.scene must be a list.")

        beat_entries = []
        current_position_state = OrderedDict(self.initial_position_state)
        for beat_index, beat in enumerate(scene, start=1):
            if not isinstance(beat, dict):
                continue
            current_positions = self._resolve_current_positions(beat, current_position_state)
            beat_entries.append(
                {
                    "beat_index": beat_index,
                    "beat": beat,
                    "current_positions": current_positions,
                }
            )
            current_position_state = self._advance_position_state(current_position_state, beat)

        total_beats = len(beat_entries)
        started_at = time.time()
        if not beat_entries:
            self.enriched_script = self.script_payload
            self._write_json_file(self.output_path, self.enriched_script)
            self._write_stage_files()
            result = self._build_stage_result()
            self._write_json_file(self.stage_output_dir / self.STAGE_FILENAME, result)
            print("[Director][Stage 3] Completed 0 beats in 0.0s.", flush=True)
            return copy.deepcopy(result)

        total_windows = (total_beats + self.WINDOW_SIZE - 1) // self.WINDOW_SIZE
        for window_index, start in enumerate(range(0, total_beats, self.WINDOW_SIZE), start=1):
            window_entries = beat_entries[start : start + self.WINDOW_SIZE]
            beat_start = window_entries[0]["beat_index"]
            beat_end = window_entries[-1]["beat_index"]
            print(
                f"[Director][Stage 3][Window {window_index}/{total_windows}] beats {beat_start}-{beat_end} camera_shot_analysis + assignment",
                flush=True,
            )
            request_entries = []
            for entry in window_entries:
                line_payload = self._build_line_payload(scene, entry["beat_index"], entry["beat"], entry["current_positions"])
                fallback = self._fallback_camera_analysis(entry["beat"], line_payload)
                request_entries.append(
                    {
                        "beat_index": entry["beat_index"],
                        "beat": entry["beat"],
                        "line_payload": line_payload,
                        "fallback": fallback,
                    }
                )

            batch_raw = {"status": "offline_fallback", "used_llm": False}
            batch_result_map = {}
            if self.llm_client is not None and self.llm_client.enabled:
                try:
                    batch_raw = self.llm_client.complete_json(
                        self._analysis_batch_system_prompt(),
                        self._analysis_batch_user_prompt_payload(request_entries),
                    )
                    batch_result_map = self._extract_batch_result_map(batch_raw)
                except RuntimeError as exc:
                    batch_raw = {
                        "status": "llm_failed_or_unavailable",
                        "used_fallback": True,
                        "error": str(exc),
                    }

            for request in request_entries:
                analysis_raw = batch_result_map.get(request["beat_index"], {})
                analysis = self._normalize_camera_analysis(analysis_raw, request["line_payload"], request["fallback"])
                assignment = self._build_camera_assignment(analysis)
                beat = request["beat"]
                beat["shot_blend"] = assignment["shot_blend"]
                beat["shot"] = assignment["shot"]
                beat["shot_type"] = assignment["shot_type"]
                beat["Follow"] = assignment["Follow"]

                self.analysis_results.append(
                    {
                        "beat_index": request["beat_index"],
                        "input": request["line_payload"],
                        "raw_llm_output": analysis_raw,
                        "shared_raw_llm_output": batch_raw,
                        "normalized_output": analysis,
                    }
                )
                self.assignment_results.append(
                    {
                        "beat_index": request["beat_index"],
                        "input": {"line_context": request["line_payload"], "camera_analysis": analysis},
                        "normalized_output": assignment,
                    }
                )

            self._write_json_file(self.output_path, self.script_payload)
            self._write_stage_files()

        self.enriched_script = self.script_payload
        self._write_json_file(self.output_path, self.enriched_script)
        self._write_stage_files()
        result = self._build_stage_result()
        self._write_json_file(self.stage_output_dir / self.STAGE_FILENAME, result)
        print(
            f"[Director][Stage 3] Completed {len(self.assignment_results)} beats in {time.time() - started_at:.1f}s.",
            flush=True,
        )
        return copy.deepcopy(result)

    # ------------------------------------------------------------------
    # Prompting
    # ------------------------------------------------------------------

    def _analysis_system_prompt(self):
        return (
            "You are a cinematic director of photography. Return one valid JSON object only. "
            "Choose a practical shot plan for the current beat using shot_description, blocking, region context, and camera library. "
            "All shot semantics are centered on the designated camera_subject."
        )

    def _analysis_batch_system_prompt(self):
        return (
            "You are a cinematic director of photography. Return one valid JSON object only. "
            "Plan a practical shot analysis for each beat in the provided local sequence window. "
            "Use shot_description, blocking, region context, camera history, and camera library. "
            "Keep every beat decision grounded in its own line content, but also consider the local sequence rhythm across the window. "
            "All shot semantics are centered on the designated camera_subject for each beat."
        )

    def _analysis_user_prompt_payload(self, line_payload, fallback):
        return {
            "task": "camera_shot_analysis",
            "instructions": [
                "shot is fixed by the system as 'character'. Do not plan any other shot category.",
                "camera_subject is the default subject that every shot type refers to.",
                "If speaker exists, camera_subject is the speaker. For example: 中景 means a medium shot of the speaker; 中近景 means a medium close-up of the speaker; 近景 means a close shot of the speaker; 第一人称镜头 means the speaker's POV; 仰拍镜头 means a low-angle shot of the speaker; 俯拍镜头 means a high-angle shot of the speaker.",
                "If there is no speaker and the beat is a movement beat, camera_subject defaults to the moving character. In that case, the chosen shot type is still centered on that moving character.",
                "Choose recommended_shot_type strictly from camera_library keys only.",
                "When reading camera_library, pay attention to the shot name, framing scope, and primary usage only. Ignore any low-level transform rules.",
                "Prefer a richer mix of valid shot types when the beat supports it, so the sequence does not become visually flat.",
                "Use recent_camera_history as soft reference only. Repeating the same shot type is allowed when the scene rhythm or dramatic need clearly supports it.",
                "For sustained conversations, consider relation shots, speaker-emphasis shots, and wider re-establishing shots when justified, but do not force variation if repetition is the better choice.",
                "Choose recommended_shot_blend strictly from the provided valid values.",
                "Prefer static framing. recommended_follow must be 0 unless the beat is an explicit move beat or a subject is clearly traveling through frame.",
                "Movement beats usually use 全景 or 侧跟镜头. Dialogue beats usually stay in 中景 or 中近景 unless spatial relationships must stay visible.",
                "Use 肩后镜头 only when the current blocking truly contains a two-person target relationship.",
                "Use 仰拍镜头 or 俯拍镜头 sparingly and only when visual dominance or weakness is clearly supported by the current beat.",
                "Use scene region context: corridor-like regions favor tighter framing, plaza-like regions favor wider framing.",
                "Use shot_blend_guide to understand the intended transition feel of each blend type before choosing one.",
                "If the fallback recommendation already fits the blocking, stay close to it.",
                "Return only the requested JSON structure.",
            ],
            "line_context": line_payload,
            "camera_library": self._summarize_camera_lib(),
            "shot_blend_guide": copy.deepcopy(self.SHOT_BLEND_GUIDE),
            "fallback_reference": fallback,
            "output_schema": {
                "focus_character": "CharacterName",
                "interaction_context": "dialogue_two_person|dialogue_group|monologue|movement|observation",
                "emotional_intensity": "low|medium|high",
                "emotional_tone": "neutral|joyful|tense|sad|powerful|vulnerable|confused",
                "recommended_shot_type": "中景",
                "recommended_shot_blend": "Cut",
                "recommended_follow": 0,
                "reasoning": "Short practical justification.",
            },
        }

    def _analysis_batch_user_prompt_payload(self, request_entries):
        return {
            "task": "camera_shot_analysis_batch",
            "window_size": self.WINDOW_SIZE,
            "instructions": [
                "You will receive a local sequence window containing up to four beats.",
                "Return one result for every beat in the same order and with the same beat_index.",
                "Plan each beat independently, but use neighboring beats in the same window to maintain a coherent local shot rhythm.",
                "shot is fixed by the system as 'character'. Do not plan any other shot category.",
                "camera_subject is the default subject that every shot type refers to.",
                "If speaker exists, camera_subject is the speaker. A medium shot means a medium shot of the speaker. A medium close-up means a medium close-up of the speaker. A close shot means a close shot of the speaker. A first-person shot means the speaker's POV. A low-angle shot means a low-angle shot of the speaker. A high-angle shot means a high-angle shot of the speaker.",
                "If there is no speaker and the beat is a movement beat, camera_subject defaults to the moving character. In that case, the chosen shot type is still centered on that moving character.",
                "Choose recommended_shot_type strictly from camera_library keys only.",
                "When reading camera_library, pay attention to the shot name, framing scope, and primary usage only. Ignore any low-level transform rules.",
                "Prefer a richer mix of valid shot types when the beats support it, so the sequence does not become visually flat.",
                "Repetition is allowed when the local sequence rhythm or dramatic need clearly supports it.",
                "For sustained conversations, consider relation shots, speaker-emphasis shots, and wider re-establishing shots when justified, but do not force variation if repetition is the better choice.",
                "Choose recommended_shot_blend strictly from the provided valid values.",
                "Prefer static framing. recommended_follow must be 0 unless the beat is an explicit move beat or a subject is clearly traveling through frame.",
                "Movement beats usually use wider or tracking-oriented shots. Dialogue beats usually stay in medium or medium-close framing unless spatial relationships must stay visible.",
                "Use over-the-shoulder only when the current blocking truly contains a two-person target relationship.",
                "Use low-angle or high-angle shots sparingly and only when visual dominance or weakness is clearly supported by the current beat.",
                "Use scene region context: corridor-like regions favor tighter framing, plaza-like regions favor wider framing.",
                "Use shot_blend_guide to understand the intended transition feel of each blend type before choosing one.",
                "If a fallback recommendation already fits the blocking, stay close to it.",
                "Return only the requested JSON structure.",
            ],
            "camera_library": self._summarize_camera_lib(),
            "shot_blend_guide": copy.deepcopy(self.SHOT_BLEND_GUIDE),
            "beats": [
                {
                    "beat_index": request["beat_index"],
                    "line_context": request["line_payload"],
                    "fallback_reference": request["fallback"],
                }
                for request in request_entries
            ],
            "output_schema": {
                "results": [
                    {
                        "beat_index": 1,
                        "focus_character": "CharacterName",
                        "interaction_context": "dialogue_two_person|dialogue_group|monologue|movement|observation",
                        "emotional_intensity": "low|medium|high",
                        "emotional_tone": "neutral|joyful|tense|sad|powerful|vulnerable|confused",
                        "recommended_shot_type": "中景",
                        "recommended_shot_blend": "Cut",
                        "recommended_follow": 0,
                        "reasoning": "Short practical justification.",
                    }
                ]
            },
        }

    # ------------------------------------------------------------------
    # Data assembly
    # ------------------------------------------------------------------

    def _build_camera_lib_map(self, raw_camera_lib):
        if not isinstance(raw_camera_lib, dict):
            raise ValueError("CameraLib.json must be a dictionary keyed by shot type names.")
        result = OrderedDict()
        for shot_type, entry in raw_camera_lib.items():
            if not isinstance(entry, dict):
                continue
            normalized_shot_type = self._stringify(shot_type)
            result[normalized_shot_type] = {
                "shot_name": normalized_shot_type,
                "framing": self._stringify(self._first_present(entry, ("画面范围", "framing"))),
                "purpose": self._stringify(self._first_present(entry, ("主要用途", "purpose"))),
            }
        if not result:
            raise ValueError("CameraLib.json does not contain any valid shot type definitions.")
        return result

    def _summarize_camera_lib(self):
        return {
            shot_type: {
                "shot_name": entry.get("shot_name", shot_type),
                "framing": entry.get("framing", ""),
                "purpose": entry.get("purpose", ""),
            }
            for shot_type, entry in self.camera_lib_map.items()
        }

    def _build_region_map(self, scene_info_json):
        if not isinstance(scene_info_json, dict):
            raise ValueError("scene_info_json must be an object.")
        scene_where = self._stringify(scene_info_json.get("where") or scene_info_json.get("scene_name"))
        if scene_where and self.where and scene_where != self.where:
            raise ValueError(f"scene_info.where {scene_where!r} does not match script where {self.where!r}.")

        result = OrderedDict()
        for region in scene_info_json.get("regions", []):
            if not isinstance(region, dict):
                continue
            name = self._stringify(region.get("name"))
            if not name:
                continue
            anchors = region.get("anchors", []) if isinstance(region.get("anchors"), list) else []
            markers = self._first_present(region, ("scene_markers", "semantic_anchors", "objects"), [])
            markers = markers if isinstance(markers, list) else []
            description = self._stringify(region.get("description"))
            result[name] = {
                "name": name,
                "description": self._compact_text(description, 220),
                "anchor_count": len(anchors),
                "marker_names": self._ordered_unique(
                    [self._stringify(item.get("name")) for item in markers if isinstance(item, dict)]
                ),
                "scale_hint": self._infer_region_scale(description, len(anchors)),
            }
        if not result:
            raise ValueError("scene_info_json.regions must contain valid regions.")
        return result

    def _build_position_contexts(self):
        pos_ctx = OrderedDict()
        group_ctx = OrderedDict()
        self._merge_position_plan(pos_ctx, group_ctx)
        self._merge_position_detail(pos_ctx, group_ctx)

        for group_id, group in group_ctx.items():
            seen = set()
            positions = []
            for item in group.get("positions", []):
                if not isinstance(item, dict):
                    continue
                pos_id = self._stringify(item.get("position_id"))
                char = self._stringify(item.get("character"))
                if not pos_id or not char or pos_id in seen:
                    continue
                seen.add(pos_id)
                positions.append({"position_id": pos_id, "character": char})
            group["positions"] = positions
            group["characters"] = [item["character"] for item in positions]
            group["size"] = len(positions)
            if not group.get("lookat_mode"):
                self._infer_group_lookat(group, pos_ctx)
            target_char = self._stringify(group.get("target_character"))
            group["target_position_id"] = ""
            for item in positions:
                if item["character"] == target_char:
                    group["target_position_id"] = item["position_id"]
                    break
            for item in positions:
                ctx = pos_ctx.setdefault(item["position_id"], {})
                ctx["characters_in_group"] = list(group["characters"])
                ctx["group_size"] = group["size"]
                ctx["group_lookat_mode"] = group.get("lookat_mode", "")
                ctx["group_target_character"] = group.get("target_character", "")
                ctx["group_target_object"] = group.get("target_object", "")
                ctx["group_target_position_id"] = group.get("target_position_id", "")
        return pos_ctx, group_ctx

    def _infer_region_scale(self, description, anchor_count):
        text = self._stringify(description)
        if self._contains_any(text, ("广场", "露天", "宽阔", "开阔")) or anchor_count >= 4:
            return "wide"
        if self._contains_any(text, ("走廊", "角落", "隐蔽", "通道")) or anchor_count <= 2:
            return "tight"
        return "medium"

    def _merge_position_plan(self, pos_ctx, group_ctx):
        source = self.position_plan_json
        if not isinstance(source, dict):
            return
        where = self._stringify(source.get("where"))
        if where and self.where and where != self.where:
            raise ValueError(f"position_plan.where {where!r} does not match script where {self.where!r}.")

        for group in source.get("groups", []):
            if not isinstance(group, dict):
                continue
            group_id = self._stringify(group.get("group_id"))
            if not group_id:
                continue
            lookat = group.get("lookat", {})
            lookat = lookat if isinstance(lookat, dict) else {}
            summary = group_ctx.setdefault(
                group_id,
                {
                    "group_id": group_id,
                    "layout": self._stringify(group.get("layout")),
                    "region": self._stringify(group.get("region")),
                    "positions": [],
                    "characters": [],
                    "lookat_mode": self._stringify(lookat.get("mode")),
                    "target_character": self._stringify(lookat.get("target_character")),
                    "target_object": self._stringify(lookat.get("target_object")),
                },
            )
            for item in group.get("positions", []):
                if not isinstance(item, dict):
                    continue
                pos_id = self._stringify(item.get("position_id"))
                char = self._stringify(item.get("character"))
                if not pos_id or not char:
                    continue
                summary["positions"].append({"position_id": pos_id, "character": char})
                pos_ctx.setdefault(pos_id, {}).update(
                    {
                        "position_id": pos_id,
                        "character": char,
                        "group_id": group_id,
                        "role": "group",
                        "region": summary["region"],
                        "layout": summary["layout"],
                        "group_lookat_mode": summary["lookat_mode"],
                        "group_target_character": summary["target_character"],
                        "group_target_object": summary["target_object"],
                    }
                )

        for item in source.get("singles", []):
            if not isinstance(item, dict):
                continue
            pos_id = self._stringify(item.get("position_id"))
            char = self._stringify(item.get("character"))
            if not pos_id or not char:
                continue
            pos_ctx.setdefault(pos_id, {}).update(
                {
                    "position_id": pos_id,
                    "character": char,
                    "group_id": "",
                    "role": "single",
                    "region": self._stringify(item.get("region")),
                    "layout": "single",
                    "neartarget": self._stringify(item.get("neartarget")),
                    "detail_lookat": self._stringify(item.get("lookat")),
                }
            )

    def _merge_position_detail(self, pos_ctx, group_ctx):
        source = self.position_detail_json
        if not isinstance(source, dict):
            return
        where = self._stringify(source.get("where"))
        if where and self.where and where != self.where:
            raise ValueError(f"position_detail.where {where!r} does not match script where {self.where!r}.")

        for item in source.get("groups", []):
            if not isinstance(item, dict):
                continue
            pos_id = self._stringify(item.get("position_id"))
            char = self._stringify(item.get("character"))
            group_id = self._stringify(item.get("group_id"))
            if not pos_id or not char or not group_id:
                continue
            group_ctx.setdefault(
                group_id,
                {
                    "group_id": group_id,
                    "layout": self._stringify(item.get("layout")),
                    "region": self._stringify(item.get("region")),
                    "positions": [],
                    "characters": [],
                    "lookat_mode": "",
                    "target_character": "",
                    "target_object": "",
                },
            )["positions"].append({"position_id": pos_id, "character": char})
            pos_ctx.setdefault(pos_id, {}).update(
                {
                    "position_id": pos_id,
                    "character": char,
                    "group_id": group_id,
                    "role": "group",
                    "region": self._stringify(item.get("region")),
                    "layout": self._stringify(item.get("layout")),
                    "detail_lookat": self._stringify(item.get("lookat")),
                }
            )

        for item in source.get("signals", []):
            if not isinstance(item, dict):
                continue
            pos_id = self._stringify(item.get("position_id"))
            char = self._stringify(item.get("character"))
            if not pos_id or not char:
                continue
            pos_ctx.setdefault(pos_id, {}).update(
                {
                    "position_id": pos_id,
                    "character": char,
                    "group_id": "",
                    "role": "single",
                    "region": self._stringify(item.get("region")),
                    "layout": "single",
                    "neartarget": self._stringify(item.get("neartarget")),
                    "detail_lookat": self._stringify(item.get("lookat")),
                }
            )

    def _infer_group_lookat(self, group, pos_ctx):
        items = []
        by_position = {}
        for member in group.get("positions", []):
            pos_id = member["position_id"]
            char = member["character"]
            by_position[pos_id] = char
            items.append((char, pos_id, self._stringify(pos_ctx.get(pos_id, {}).get("detail_lookat"))))
        if items and all(lookat == "center" for _, _, lookat in items):
            group["lookat_mode"] = "center"
            return
        for char, _, lookat in items:
            if lookat == "center":
                group["lookat_mode"] = "target"
                group["target_character"] = char
                return
        non_empty = [lookat for _, _, lookat in items if lookat]
        if non_empty and all(lookat == non_empty[0] for lookat in non_empty):
            target = non_empty[0]
            group["lookat_mode"] = "target"
            if target in by_position:
                group["target_character"] = by_position[target]
            else:
                group["target_object"] = target

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def _fallback_camera_analysis(self, beat, line_payload):
        speaker = self._stringify(beat.get("speaker"))
        move_entries = line_payload["current_line"]["move"]
        current_positions = line_payload["current_line"]["current_position"]
        primary_group = self._select_primary_group(line_payload, speaker, move_entries)
        primary_size = int(primary_group.get("current_member_count", 0)) if primary_group else 0
        present_count = len(current_positions)
        focus_character = speaker or self._first_moving_character(move_entries) or (current_positions[0]["character"] if current_positions else "")

        if line_payload["beat_type"] == "movement":
            interaction = "movement"
        elif speaker and primary_size <= 1:
            interaction = "monologue"
        elif speaker and primary_size == 2:
            interaction = "dialogue_two_person"
        elif speaker:
            interaction = "dialogue_group"
        else:
            interaction = "observation"

        tone, intensity = self._infer_emotional_profile(beat)
        observers = max(0, present_count - max(primary_size, 1 if speaker else 0))
        shot_type = self._select_fallback_shot_type(beat, line_payload, interaction, tone, intensity, primary_group, observers)
        follow = 1 if line_payload["beat_type"] == "movement" else 0
        shot_blend = self._select_fallback_shot_blend(beat, line_payload, interaction, tone, follow)
        return {
            "focus_character": focus_character,
            "interaction_context": interaction,
            "emotional_intensity": intensity,
            "emotional_tone": tone,
            "recommended_shot_type": shot_type,
            "recommended_shot_blend": shot_blend,
            "recommended_follow": follow,
            "reasoning": f"fallback interaction={interaction} tone={tone} group_size={primary_size} observers={observers}",
        }

    def _normalize_camera_analysis(self, raw_output, line_payload, fallback):
        candidate = raw_output if isinstance(raw_output, dict) else {}
        present_characters = [
            item["character"]
            for item in line_payload["current_line"]["current_position"]
            if item.get("character")
        ]
        result = {
            "focus_character": self._coerce_character_name(candidate.get("focus_character"), present_characters, fallback["focus_character"]),
            "interaction_context": self._coerce_enum(candidate.get("interaction_context"), self.VALID_INTERACTION_CONTEXTS, fallback["interaction_context"]),
            "emotional_intensity": self._coerce_enum(candidate.get("emotional_intensity"), self.VALID_EMOTIONAL_INTENSITIES, fallback["emotional_intensity"]),
            "emotional_tone": self._coerce_enum(candidate.get("emotional_tone"), self.VALID_EMOTIONAL_TONES, fallback["emotional_tone"]),
            "recommended_shot_type": self._coerce_shot_type(candidate.get("recommended_shot_type"), fallback["recommended_shot_type"]),
            "recommended_shot_blend": self._coerce_enum_ci(candidate.get("recommended_shot_blend"), self.VALID_SHOT_BLEND, fallback["recommended_shot_blend"]),
            "recommended_follow": self._coerce_follow(candidate.get("recommended_follow"), fallback["recommended_follow"]),
            "reasoning": self._stringify(candidate.get("reasoning")) or fallback["reasoning"],
        }
        if line_payload["beat_type"] != "movement":
            result["recommended_follow"] = 0
        if result["recommended_shot_type"] == "侧跟镜头" and line_payload["beat_type"] != "movement":
            result["recommended_shot_type"] = fallback["recommended_shot_type"]
        if result["recommended_shot_type"] == "肩后镜头" and not self._check_over_the_shoulder_opportunity(line_payload):
            result["recommended_shot_type"] = fallback["recommended_shot_type"]
        if result["recommended_shot_type"] in ("仰拍镜头", "俯拍镜头") and not self._allow_angle_shot(line_payload, result["emotional_tone"]):
            result["recommended_shot_type"] = fallback["recommended_shot_type"]
        return result

    def _extract_batch_result_map(self, raw_output):
        result = {}
        if not isinstance(raw_output, dict):
            return result
        raw_results = raw_output.get("results")
        if not isinstance(raw_results, list):
            return result
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            beat_index = item.get("beat_index")
            try:
                beat_index = int(beat_index)
            except (TypeError, ValueError):
                continue
            if beat_index <= 0 or beat_index in result:
                continue
            result[beat_index] = item
        return result

    def _select_fallback_shot_type(self, beat, line_payload, interaction, tone, intensity, primary_group, observers):
        desc = self._stringify(line_payload["current_line"].get("shot_description"))
        content = self._stringify(line_payload["current_line"].get("content"))
        region_name = self._stringify(line_payload["scene_context"].get("primary_region"))
        region_scale = self.region_map.get(region_name, {}).get("scale_hint", "medium")
        is_move = line_payload["beat_type"] == "movement"
        group_size = int(primary_group.get("current_member_count", 0)) if primary_group else 0
        has_ots = self._check_over_the_shoulder_opportunity(line_payload)
        layered = self._contains_any(desc, ("background", "observ", "observer"))
        side_composition = self._contains_any(desc, ("left", "right")) and self._contains_any(desc, ("foreground", "background"))
        speaker = self._stringify(line_payload["current_line"].get("speaker"))
        target_character = self._stringify(primary_group.get("target_character")) if primary_group else ""

        if is_move:
            if len(line_payload["current_line"]["move"]) == 1:
                return self._pick_shot_type("侧跟镜头", "全景", "中景")
            return self._pick_shot_type("全景", "侧跟镜头", "中景")
        if interaction == "monologue":
            if tone in ("vulnerable", "sad", "confused") and self._has_fragility_cue(beat):
                return self._pick_shot_type("俯拍镜头", "中近景", "近景", "中景")
            if tone in ("powerful", "joyful") and self._has_authority_cue(beat):
                return self._pick_shot_type("仰拍镜头", "中近景", "中景")
            return self._pick_shot_type("中近景", "近景", "中景")
        if interaction == "dialogue_two_person":
            if has_ots and observers == 0:
                return self._pick_shot_type("肩后镜头", "中景", "中近景")
            if tone == "tense":
                return self._pick_shot_type("中近景", "中景", "全景")
            if self._contains_any(content, ("？", "?", "！", "!")):
                return self._pick_shot_type("中近景", "中景", "全景")
            return self._pick_shot_type("中景", "中近景", "全景")
        if interaction == "dialogue_group":
            if group_size >= 4 or (region_scale == "wide" and (observers > 0 or layered or side_composition)):
                return self._pick_shot_type("全景", "中景", "中近景")
            if group_size <= 3 and observers == 0 and speaker and target_character and speaker == target_character:
                return self._pick_shot_type("中近景", "中景", "全景")
            if intensity == "high" and group_size <= 3 and observers == 0:
                return self._pick_shot_type("中近景", "中景", "全景")
            if self._contains_any(content, ("？", "?", "！", "!")) and group_size <= 3:
                return self._pick_shot_type("中近景", "中景", "全景")
            return self._pick_shot_type("中景", "中近景", "全景")
        if observers > 0 or (region_scale == "wide" and (layered or side_composition)):
            return self._pick_shot_type("全景", "中景")
        return self._pick_shot_type("中景", "中近景", "全景")

    def _select_fallback_shot_blend(self, beat, line_payload, interaction, tone, follow):
        content = self._stringify(beat.get("content"))
        previous = line_payload.get("previous_line", {})
        if line_payload["beat_type"] == "movement":
            return "Linear" if follow else "Ease In Out"
        if interaction == "observation" and previous.get("beat_type") == "movement":
            return "Ease Out"
        if tone == "tense" and self._contains_any(content, ("!", "！", "?", "？")):
            return "Hard In"
        if tone in ("sad", "vulnerable", "confused") and self._contains_any(content, ("……", "...")):
            return "Ease In Out"
        return "Cut"

    def _infer_emotional_profile(self, beat):
        actions = " ".join(self._stringify(item.get("action")) for item in self._normalize_actions(beat.get("actions")))
        content = self._stringify(beat.get("content"))
        blob = f"{actions} {content}"
        if self._contains_any(blob, ("Cry", "Crying", "Upset", "被骗", "完了")):
            return "sad", "high" if self._contains_any(content, ("!", "！")) else "medium"
        if self._contains_any(blob, ("Puzzled", "Thinking", "Confused", "不靠谱", "人呢", "钱呢", "……", "...")):
            return "confused", "low" if self._contains_any(content, ("……", "...")) else "medium"
        if self._contains_any(blob, ("Deny", "Fight", "Angry", "Confront")):
            return "tense", "high"
        if self._contains_any(blob, ("Joyful", "Happy", "Jump", "Surprise", "Dance", "Laugh")):
            return "joyful", "high"
        if self._has_authority_cue(beat):
            return "powerful", "medium"
        if self._has_fragility_cue(beat):
            return "vulnerable", "low"
        return "neutral", "medium"

    def _has_authority_cue(self, beat):
        blob = self._stringify(beat.get("content")) + " " + " ".join(
            self._stringify(item.get("action")) for item in self._normalize_actions(beat.get("actions"))
        )
        return self._contains_any(blob, ("必须", "立刻", "快", "终极", "官方", "正规", "Speech"))

    def _has_fragility_cue(self, beat):
        blob = self._stringify(beat.get("content")) + " " + " ".join(
            self._stringify(item.get("action")) for item in self._normalize_actions(beat.get("actions"))
        )
        return self._contains_any(blob, ("……", "...", "Thinking", "Puzzled", "Crying", "Upset", "被骗"))

    def _allow_angle_shot(self, line_payload, tone):
        primary_group = self._select_primary_group(
            line_payload,
            self._stringify(line_payload["current_line"].get("speaker")),
            line_payload["current_line"].get("move", []),
        )
        group_size = int(primary_group.get("current_member_count", 0)) if primary_group else 1
        observers = max(0, len(line_payload["current_line"]["current_position"]) - max(group_size, 1))
        return group_size <= 2 and observers == 0 and tone in ("powerful", "joyful", "vulnerable", "sad", "confused")

    def _build_camera_assignment(self, analysis):
        return {
            "shot_blend": analysis["recommended_shot_blend"],
            "shot": "character",
            "shot_type": analysis["recommended_shot_type"],
            "Follow": analysis["recommended_follow"],
        }

    # ------------------------------------------------------------------
    # Payload / output helpers
    # ------------------------------------------------------------------

    def _build_line_payload(self, scene, beat_index, beat, current_positions):
        speaker = self._stringify(beat.get("speaker"))
        move_entries = self._normalize_moves(beat.get("move"))
        camera_subject = speaker or self._first_moving_character(move_entries) or (current_positions[0]["character"] if current_positions else "")
        recent_camera_history = self._build_recent_camera_history(scene, beat_index)
        active_groups = self._summarize_active_groups(current_positions)
        scene_context = self._build_scene_context(current_positions, move_entries, speaker, active_groups)
        previous = scene[beat_index - 2] if beat_index - 2 >= 0 else None
        next_beat = scene[beat_index] if beat_index < len(scene) else None
        return {
            "where": self.where,
            "beat_index": beat_index,
            "beat_type": "dialogue" if speaker else ("movement" if move_entries else "other"),
            "previous_line": self._summarize_neighbor_beat(previous),
            "current_line": {
                "speaker": speaker,
                "camera_subject": camera_subject,
                "content": self._stringify(beat.get("content")),
                "actions": self._normalize_actions(beat.get("actions")),
                "move": move_entries,
                "current_position": current_positions,
                "shot_description": self._stringify(beat.get("shot_description")),
                "existing_shot_blend": self._stringify(beat.get("shot_blend")),
                "existing_shot": self._stringify(beat.get("shot")),
                "existing_shot_type": self._stringify(beat.get("shot_type")),
                "existing_follow": beat.get("Follow"),
            },
            "scene_context": scene_context,
            "recent_camera_history": recent_camera_history,
            "next_line": self._summarize_neighbor_beat(next_beat),
        }

    def _write_stage_files(self):
        self._write_json_file(self.stage_output_dir / self.ANALYSIS_STAGE_FILENAME, {"where": self.where, "results": self.analysis_results})
        self._write_json_file(self.stage_output_dir / self.ASSIGNMENT_STAGE_FILENAME, {"where": self.where, "results": self.assignment_results})

    def _build_stage_result(self):
        return {
            "where": self.where,
            "stage": "camera",
            "description": "Director stage 3 camera planning completed with camera shot analysis and camera assignment.",
            "substage_sequence": [
                {
                    "name": "camera_shot_analysis",
                    "description": "Use shot_description, blocking, scene info, and CameraLib to recommend shot_blend, shot_type, and Follow.",
                    "stage_output_path": str(self.stage_output_dir / self.ANALYSIS_STAGE_FILENAME).replace("\\", "/"),
                    "result_count": len(self.analysis_results),
                },
                {
                    "name": "camera_assignment",
                    "description": "Fill the final shot_blend, shot, shot_type, and Follow fields into the script json.",
                    "stage_output_path": str(self.stage_output_dir / self.ASSIGNMENT_STAGE_FILENAME).replace("\\", "/"),
                    "result_count": len(self.assignment_results),
                },
            ],
            "outputs": {
                "camera_plan_output_path": str(self.output_path).replace("\\", "/"),
                "analysis_stage_output_path": str(self.stage_output_dir / self.ANALYSIS_STAGE_FILENAME).replace("\\", "/"),
                "assignment_stage_output_path": str(self.stage_output_dir / self.ASSIGNMENT_STAGE_FILENAME).replace("\\", "/"),
            },
            "script_with_camera_plan": copy.deepcopy(self.enriched_script),
        }

    def _summarize_active_groups(self, current_positions):
        result = []
        seen = set()
        for pos in current_positions:
            group_id = self._stringify(pos.get("group_id"))
            if not group_id or group_id in seen:
                continue
            seen.add(group_id)
            summary = self.group_context_by_id.get(group_id)
            if not summary:
                continue
            members = [item for item in current_positions if self._stringify(item.get("group_id")) == group_id]
            result.append(
                {
                    "group_id": group_id,
                    "region": summary.get("region", ""),
                    "layout": summary.get("layout", ""),
                    "size": summary.get("size", 0),
                    "current_member_count": len(members),
                    "characters": list(summary.get("characters", [])),
                    "lookat_mode": summary.get("lookat_mode", ""),
                    "target_character": summary.get("target_character", ""),
                    "target_object": summary.get("target_object", ""),
                    "target_position_id": summary.get("target_position_id", ""),
                    "current_members": [
                        {
                            "position_id": item.get("position_id"),
                            "character": item.get("character"),
                            "lookat": item.get("detail_lookat", ""),
                        }
                        for item in members
                    ],
                }
            )
        return result

    def _build_scene_context(self, current_positions, move_entries, speaker, active_groups):
        current_regions = self._ordered_unique([item.get("region") for item in current_positions])
        move_regions = []
        movement_pairs = []
        for move in move_entries:
            dest_id = self._stringify(move.get("destination"))
            dest_region = self._stringify(self.position_context_by_id.get(dest_id, {}).get("region"))
            src_region = ""
            for pos in current_positions:
                if self._stringify(pos.get("character")) == self._stringify(move.get("character")):
                    src_region = self._stringify(pos.get("region"))
                    break
            if dest_region:
                move_regions.append(dest_region)
            movement_pairs.append(
                {
                    "character": self._stringify(move.get("character")),
                    "source_region": src_region,
                    "destination_region": dest_region,
                    "destination_position_id": dest_id,
                }
            )
        primary_region = ""
        if speaker:
            for pos in current_positions:
                if self._stringify(pos.get("character")) == speaker:
                    primary_region = self._stringify(pos.get("region"))
                    break
        if not primary_region and active_groups:
            primary_region = self._stringify(active_groups[0].get("region"))
        if not primary_region and move_regions:
            primary_region = move_regions[0]
        if not primary_region and current_regions:
            primary_region = current_regions[0]
        relevant_regions = self._ordered_unique(current_regions + move_regions)
        return {
            "primary_region": primary_region,
            "active_groups": active_groups,
            "movement_pairs": movement_pairs,
            "relevant_region_context": [
                {
                    "name": self.region_map[name]["name"],
                    "anchor_count": self.region_map[name]["anchor_count"],
                    "marker_names": list(self.region_map[name]["marker_names"]),
                    "scale_hint": self.region_map[name]["scale_hint"],
                    "description": self.region_map[name]["description"],
                }
                for name in relevant_regions
                if name in self.region_map
            ],
        }

    def _build_recent_camera_history(self, scene, beat_index, max_items=3):
        history = []
        start = max(0, beat_index - 1 - max_items)
        for idx in range(start, beat_index - 1):
            beat = scene[idx]
            if not isinstance(beat, dict):
                continue
            history.append(
                {
                    "beat_index": idx + 1,
                    "beat_type": "dialogue" if beat.get("speaker") else ("movement" if beat.get("move") else "other"),
                    "speaker": self._stringify(beat.get("speaker")),
                    "shot_type": self._stringify(beat.get("shot_type")),
                    "shot_blend": self._stringify(beat.get("shot_blend")),
                    "follow": beat.get("Follow"),
                }
            )
        return history

    # ------------------------------------------------------------------
    # Script state / utility helpers
    # ------------------------------------------------------------------

    def _resolve_current_positions(self, beat, current_position_state):
        raw_positions = beat.get("current position")
        positions = []
        if isinstance(raw_positions, list) and raw_positions:
            for item in raw_positions:
                if not isinstance(item, dict):
                    continue
                char = self._stringify(item.get("character"))
                pos_id = self._stringify(item.get("position") or item.get("position_id"))
                if char and pos_id:
                    positions.append({"character": char, "position_id": pos_id})
        else:
            for char, pos_id in current_position_state.items():
                positions.append({"character": char, "position_id": pos_id})
        result = []
        seen = set()
        for item in positions:
            key = (item["character"], item["position_id"])
            if key in seen:
                continue
            seen.add(key)
            ctx = self.position_context_by_id.get(item["position_id"], {})
            result.append(
                {
                    "character": item["character"],
                    "position_id": item["position_id"],
                    "group_id": self._stringify(ctx.get("group_id")),
                    "region": self._stringify(ctx.get("region")),
                    "layout": self._stringify(ctx.get("layout")),
                    "detail_lookat": self._stringify(ctx.get("detail_lookat")),
                }
            )
        return result

    def _advance_position_state(self, current_position_state, beat):
        updated = OrderedDict(current_position_state)
        for move in self._normalize_moves(beat.get("move")):
            updated[move["character"]] = move["destination"]
        raw_positions = beat.get("current position")
        if isinstance(raw_positions, list):
            for item in raw_positions:
                if not isinstance(item, dict):
                    continue
                char = self._stringify(item.get("character"))
                pos_id = self._stringify(item.get("position") or item.get("position_id"))
                if char and pos_id:
                    updated[char] = pos_id
        return updated

    def _summarize_neighbor_beat(self, beat):
        if not isinstance(beat, dict):
            return {}
        return {
            "beat_type": "dialogue" if beat.get("speaker") else ("movement" if beat.get("move") else "other"),
            "speaker": self._stringify(beat.get("speaker")),
            "content": self._compact_text(self._stringify(beat.get("content")), 100),
            "shot_description": self._compact_text(self._stringify(beat.get("shot_description")), 140),
        }

    def _select_primary_group(self, line_payload, speaker, move_entries):
        active_groups = line_payload["scene_context"]["active_groups"]
        if speaker:
            for group in active_groups:
                if speaker in group.get("characters", []):
                    return group
        for move in move_entries if isinstance(move_entries, list) else []:
            dest = self.position_context_by_id.get(self._stringify(move.get("destination")), {})
            group_id = self._stringify(dest.get("group_id"))
            for group in active_groups:
                if self._stringify(group.get("group_id")) == group_id:
                    return group
        return active_groups[0] if active_groups else {}

    def _check_over_the_shoulder_opportunity(self, line_payload):
        for group in line_payload["scene_context"]["active_groups"]:
            if int(group.get("current_member_count", 0)) != 2:
                continue
            if self._stringify(group.get("lookat_mode")) == "target":
                return True
            member_positions = {item.get("position_id") for item in group.get("current_members", []) if isinstance(item, dict)}
            for item in group.get("current_members", []):
                lookat = self._stringify(item.get("lookat"))
                if lookat and lookat in member_positions:
                    return True
        return False

    def _first_moving_character(self, move_entries):
        for move in move_entries if isinstance(move_entries, list) else []:
            char = self._stringify(move.get("character"))
            if char:
                return char
        return ""

    def _extract_timeline_root(self, payload):
        root = payload[0] if isinstance(payload, list) and payload else payload
        if not isinstance(root, dict) or not isinstance(root.get("scene"), list):
            return None
        return root

    def _resolve_where(self, timeline_root):
        scene_information = timeline_root.get("scene information", {})
        where = timeline_root.get("where")
        if not isinstance(where, str) or not where.strip():
            where = scene_information.get("where")
        return where.strip() if isinstance(where, str) and where.strip() else ""

    def _build_initial_position_state(self, timeline_root):
        state = OrderedDict()
        for item in timeline_root.get("initial position", []):
            if not isinstance(item, dict):
                continue
            char = self._stringify(item.get("character"))
            pos_id = self._stringify(item.get("position") or item.get("position_id"))
            if char and pos_id:
                state[char] = pos_id
        return state

    def _normalize_actions(self, actions):
        result = []
        for action in actions if isinstance(actions, list) else []:
            if isinstance(action, dict):
                result.append(
                    {
                        "character": self._stringify(action.get("character")),
                        "state": self._stringify(action.get("state")),
                        "action": self._stringify(action.get("action")),
                        "motion_detail": self._stringify(action.get("motion_detail")),
                    }
                )
        return result

    def _normalize_moves(self, moves):
        result = []
        for move in moves if isinstance(moves, list) else []:
            if not isinstance(move, dict):
                continue
            char = self._stringify(move.get("character"))
            dest = self._stringify(move.get("destination") or move.get("position") or move.get("position_id"))
            if char and dest:
                result.append({"character": char, "destination": dest})
        return result

    def _coerce_character_name(self, value, candidates, fallback):
        return value.strip() if isinstance(value, str) and value.strip() in candidates else fallback

    def _coerce_enum(self, value, valid_values, fallback):
        return value.strip() if isinstance(value, str) and value.strip() in valid_values else fallback

    def _coerce_enum_ci(self, value, valid_values, fallback):
        if not isinstance(value, str):
            return fallback
        lowered = value.strip().lower()
        for valid in valid_values:
            if valid.lower() == lowered:
                return valid
        return fallback

    def _coerce_follow(self, value, fallback):
        if isinstance(value, int) and value in (0, 1):
            return value
        if isinstance(value, str):
            try:
                ivalue = int(value.strip())
            except ValueError:
                return fallback
            return ivalue if ivalue in (0, 1) else fallback
        return fallback

    def _coerce_shot_type(self, value, fallback):
        if isinstance(value, str):
            value = value.strip()
            if value in self.valid_shot_types:
                return value
        return fallback

    def _pick_shot_type(self, *candidates):
        for candidate in candidates:
            if candidate in self.valid_shot_types:
                return candidate
        for fallback in ("中景", "中近景", "全景", "近景"):
            if fallback in self.valid_shot_types:
                return fallback
        return next(iter(self.camera_lib_map))

    def _first_present(self, mapping, keys, default=None):
        if not isinstance(mapping, dict):
            return default
        for key in keys:
            if key in mapping:
                return mapping[key]
        return default

    def _ordered_unique(self, items):
        seen = set()
        result = []
        for item in items:
            value = self._stringify(item)
            if not value or value in seen:
                continue
            seen.add(value)
            result.append(value)
        return result

    def _contains_any(self, text, keywords):
        haystack = self._stringify(text).lower()
        return any(self._stringify(keyword).lower() in haystack for keyword in keywords)

    def _stringify(self, value):
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        return str(value).strip()

    def _compact_text(self, text, limit=320):
        compact = re.sub(r"\s+", " ", self._stringify(text))
        return compact if len(compact) <= limit else compact[: max(0, limit - 1)].rstrip() + "…"

    def _load_json_like(self, value):
        if isinstance(value, (dict, list)):
            return copy.deepcopy(value)
        if isinstance(value, Path):
            return self._read_json_file(value)
        if isinstance(value, str):
            possible_path = Path(value)
            if possible_path.exists():
                return self._read_json_file(possible_path)
            return json.loads(value)
        raise TypeError(f"Unsupported JSON input type: {type(value).__name__}")

    def _read_json_file(self, path):
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))

    def _write_json_file(self, path, payload):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
