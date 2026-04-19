import copy
import json
import re
from collections import OrderedDict
from pathlib import Path
import time


class ShotPlanningStage:
    STAGE_FILENAME = "director_stage1_shot_planning.json"
    ANALYSIS_STAGE_FILENAME = "director_stage1_substage1_interaction_analysis.json"
    DESCRIPTION_STAGE_FILENAME = "director_stage1_substage2_shot_description_generation.json"
    COMPILATION_STAGE_FILENAME = "director_stage1_substage3_script_compilation.json"
    OUTPUT_FILENAME = "script_with_shot_description.json"
    WINDOW_SIZE = 4
    CONTEXT_WINDOW_RADIUS = 3

    VALID_INTERACTION_TYPES = {"dialogue", "monologue", "confrontation", "presentation", "observation", "movement", "idle"}
    VALID_VISIBILITY = {"foreground", "midground", "background", "offscreen"}
    VALID_PARTICIPATION = {"primary", "secondary", "observer", "inactive"}
    VALID_SPATIAL_ROLES = {"leader", "responder", "co_actor", "observer", "background", "target"}
    VALID_GROUP_STRUCTURES = {"one_to_one", "one_to_many", "many_equal", "one_to_one_plus_observer", "isolated"}
    VALID_TRANSITIONS = {"none", "enter", "exit", "regroup", "approach", "disperse"}

    def __init__(self, script_json, llm_client=None, output_path=None, stage_output_dir=None):
        self.raw_script_json = self._load_json_like(script_json)
        self.llm_client = llm_client
        self.output_path = Path(output_path) if output_path else Path("Assets") / "Json" / self.OUTPUT_FILENAME
        self.stage_output_dir = Path(stage_output_dir) if stage_output_dir else Path("Assets") / "Json" / "AgentStage"
        self.stage_output_dir.mkdir(parents=True, exist_ok=True)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

        self.script_payload = copy.deepcopy(self.raw_script_json)
        self.timeline_root = self._extract_timeline_root(self.script_payload)
        if self.timeline_root is None:
            raise ValueError("shot planning currently requires a timeline-style script json with a scene array.")

        self.where = self._resolve_where(self.timeline_root)
        self.initial_position_state = self._build_initial_position_state(self.timeline_root)
        self.analysis_results = []
        self.description_results = []
        self.compilation_result = None
        self.enriched_script = None

    def run(self):
        scene = self.timeline_root.get("scene", [])
        if not isinstance(scene, list):
            raise ValueError("script_json.scene must be a list.")

        started_at = time.time()
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
        if not beat_entries:
            self.enriched_script = self.script_payload
            self._write_json_file(self.output_path, self.enriched_script)
            self.compilation_result = {
                "where": self.where,
                "output_script_path": str(self.output_path).replace("\\", "/"),
                "script_with_shot_description": self.enriched_script,
            }
            self._write_stage_files()
            result = self._build_stage_result()
            self._write_json_file(self.stage_output_dir / self.STAGE_FILENAME, result)
            print("[Director][Stage 1] Completed 0 beats in 0.0s.", flush=True)
            return copy.deepcopy(result)

        total_windows = (total_beats + self.WINDOW_SIZE - 1) // self.WINDOW_SIZE
        for window_index, start in enumerate(range(0, total_beats, self.WINDOW_SIZE), start=1):
            window_entries = beat_entries[start : start + self.WINDOW_SIZE]
            beat_start = window_entries[0]["beat_index"]
            beat_end = window_entries[-1]["beat_index"]
            print(
                f"[Director][Stage 1][Window {window_index}/{total_windows}] beats {beat_start}-{beat_end} interaction_analysis + shot_description",
                flush=True,
            )

            request_entries = []
            for entry in window_entries:
                line_payload = self._build_line_payload(
                    scene,
                    entry["beat_index"],
                    entry["beat"],
                    entry["current_positions"],
                )
                fallback_analysis = self._fallback_line_analysis(entry["beat"], line_payload)
                request_entries.append(
                    {
                        "beat_index": entry["beat_index"],
                        "beat": entry["beat"],
                        "line_payload": line_payload,
                        "current_positions": entry["current_positions"],
                        "fallback_analysis": fallback_analysis,
                    }
                )

            batch_raw = {"status": "offline_fallback", "used_llm": False}
            batch_result_map = {}
            if self.llm_client is not None and self.llm_client.enabled:
                try:
                    batch_raw = self.llm_client.complete_json(
                        self._combined_batch_system_prompt(),
                        self._combined_batch_user_prompt_payload(request_entries),
                    )
                    batch_result_map = self._extract_batch_result_map(batch_raw)
                except RuntimeError as exc:
                    batch_raw = {
                        "status": "llm_failed_or_unavailable",
                        "used_fallback": True,
                        "error": str(exc),
                    }

            for request in request_entries:
                combined_raw = batch_result_map.get(request["beat_index"], {})
                analysis_raw = {"status": "offline_fallback", "used_llm": False}
                description_raw = {"status": "offline_fallback", "used_llm": False}
                if isinstance(combined_raw, dict) and combined_raw:
                    analysis_raw = combined_raw.get("interaction_analysis")
                    if not isinstance(analysis_raw, dict):
                        analysis_raw = combined_raw
                    description_raw = {
                        "shot_description": combined_raw.get("shot_description") or combined_raw.get("description")
                    }

                line_analysis = self._normalize_line_analysis(
                    analysis_raw,
                    request["line_payload"],
                    request["fallback_analysis"],
                )
                fallback_description = self._fallback_shot_description(request["line_payload"], line_analysis)
                shot_description = self._normalize_shot_description(
                    description_raw,
                    request["current_positions"],
                    fallback_description,
                )

                request["beat"]["shot_description"] = shot_description
                self.analysis_results.append(
                    {
                        "beat_index": request["beat_index"],
                        "input": request["line_payload"],
                        "raw_llm_output": analysis_raw,
                        "shared_raw_llm_output": batch_raw,
                        "normalized_output": line_analysis,
                    }
                )
                self.description_results.append(
                    {
                        "beat_index": request["beat_index"],
                        "input": {"line_context": request["line_payload"], "interaction_analysis": line_analysis},
                        "raw_llm_output": description_raw,
                        "shared_raw_llm_output": batch_raw,
                        "normalized_output": {"shot_description": shot_description},
                    }
                )

            self._write_json_file(self.output_path, self.script_payload)
            self._write_stage_files()

        self.enriched_script = self.script_payload
        self._write_json_file(self.output_path, self.enriched_script)
        self.compilation_result = {
            "where": self.where,
            "output_script_path": str(self.output_path).replace("\\", "/"),
            "script_with_shot_description": self.enriched_script,
        }
        self._write_stage_files()
        result = self._build_stage_result()
        self._write_json_file(self.stage_output_dir / self.STAGE_FILENAME, result)
        print(
            f"[Director][Stage 1] Completed {len(self.analysis_results)} beats in {time.time() - started_at:.1f}s.",
            flush=True,
        )
        return copy.deepcopy(result)

    def _write_stage_files(self):
        self._write_json_file(
            self.stage_output_dir / self.ANALYSIS_STAGE_FILENAME,
            {"where": self.where, "substage": "interaction_analysis", "results": self.analysis_results},
        )
        self._write_json_file(
            self.stage_output_dir / self.DESCRIPTION_STAGE_FILENAME,
            {"where": self.where, "substage": "shot_description_generation", "results": self.description_results},
        )
        self._write_json_file(
            self.stage_output_dir / self.COMPILATION_STAGE_FILENAME,
            {
                "where": self.where,
                "substage": "script_compilation",
                "output_script_path": str(self.output_path).replace("\\", "/"),
                "script_with_shot_description": self.enriched_script,
            },
        )

    def _build_stage_result(self):
        return {
            "where": self.where,
            "stage": "shot",
            "description": "Director stage 1 shot planning completed with interaction analysis, shot description generation, and script compilation.",
            "substage_sequence": [
                {
                    "name": "interaction_analysis",
                    "description": "Infer interaction state, visibility, participation, and temporal transition for each script line.",
                    "stage_output_path": str(self.stage_output_dir / self.ANALYSIS_STAGE_FILENAME).replace("\\", "/"),
                    "result_count": len(self.analysis_results),
                },
                {
                    "name": "shot_description_generation",
                    "description": "Convert the interaction analysis into one or two sentences of visual composition for each script line.",
                    "stage_output_path": str(self.stage_output_dir / self.DESCRIPTION_STAGE_FILENAME).replace("\\", "/"),
                    "result_count": len(self.description_results),
                },
                {
                    "name": "script_compilation",
                    "description": "Write the final script json with shot_description filled in for each line.",
                    "stage_output_path": str(self.stage_output_dir / self.COMPILATION_STAGE_FILENAME).replace("\\", "/"),
                    "output_script_path": str(self.output_path).replace("\\", "/"),
                },
            ],
            "outputs": {
                "analysis_stage_output_path": str(self.stage_output_dir / self.ANALYSIS_STAGE_FILENAME).replace("\\", "/"),
                "description_stage_output_path": str(self.stage_output_dir / self.DESCRIPTION_STAGE_FILENAME).replace("\\", "/"),
                "compilation_stage_output_path": str(self.stage_output_dir / self.COMPILATION_STAGE_FILENAME).replace("\\", "/"),
                "script_output_path": str(self.output_path).replace("\\", "/"),
            },
            "script_with_shot_description": self.enriched_script,
        }

    def _analysis_system_prompt(self):
        return "You are a cinematic director. Return a valid JSON object only. Infer the interaction state for the current script line."

    def _description_system_prompt(self):
        return "You are a cinematic director. Return a valid JSON object only. Generate a one-or-two-sentence shot_description from the interaction analysis."

    def _combined_system_prompt(self):
        return (
            "You are a cinematic director. Return one valid JSON object only. "
            "For the current script line, first infer the interaction state, then generate a one-or-two-sentence shot_description "
            "that is fully consistent with that interaction analysis."
        )

    def _combined_batch_system_prompt(self):
        return (
            "You are a cinematic director. Return one valid JSON object only. "
            "For each beat in the provided local sequence window, first infer the interaction state, then generate a one-or-two-sentence "
            "shot_description that is fully consistent with that interaction analysis. "
            "Keep every beat grounded in its own current line while also using the local window for continuity."
        )

    def _combined_user_prompt_payload(self, line_payload):
        return {
            "task": "interaction_analysis_and_shot_description",
            "instructions": [
                "First infer the full interaction state for the current script line.",
                "Then generate a one-or-two-sentence shot_description that strictly follows that analysis.",
                "Use previous_line and next_line as the immediate neighboring beats.",
                "Use context_window_before and context_window_after as short continuity summaries covering up to three additional beats on each side beyond the immediate neighbors.",
                "Current_line is still the primary source of truth. The short context windows are only auxiliary evidence for continuity, transition, and upcoming participation.",
                "Use focus_character = speaker when a speaker exists. If there is no speaker, infer focus from move or actions.",
                "Choose interaction_type from: dialogue, monologue, confrontation, presentation, observation, movement, idle.",
                "Assume all current_position characters are physically present unless strongly implied otherwise.",
                "For each present character assign exactly one visibility: foreground, midground, background, offscreen.",
                "For each present character assign exactly one participation: primary, secondary, observer, inactive.",
                "Only primary and secondary characters belong to the main interaction.",
                "Observer and background characters must stay outside the main interaction composition.",
                "A character is secondary only if there is direct interaction evidence in the current beat.",
                "Direct interaction evidence includes: being explicitly addressed, being directly responded to, taking part in the same action exchange, being on the current dialogue axis, or being the immediate movement target in a movement beat.",
                "A character is observer if they are only present, only visible, only approaching, only waiting, or only reacting weakly without direct exchange in the current beat.",
                "Physical presence alone is never enough to classify a character as secondary.",
                "If a character will speak in the next few beats but is not yet engaged in the current beat, classify them as observer and put them in upcoming_active_characters instead of secondary.",
                "Do not promote a character to secondary only because context_window_after shows that they become active later.",
                "For dialogue or confrontation, do not label multiple secondary characters unless the line clearly addresses multiple people at once.",
                "Use interaction_evidence and observer_evidence strings to justify each non-primary classification.",
                "For each present character assign one spatial_role from: leader, responder, co_actor, observer, background, target.",
                "Choose group_structure from: one_to_one, one_to_many, many_equal, one_to_one_plus_observer, isolated.",
                "Analyze temporal continuity using move, previous line, and next line.",
                "Use context_window_before and context_window_after to decide whether the current beat is continuing a local exchange, transitioning into one, or breaking away from one.",
                "Choose transition_type from: none, enter, exit, regroup, approach, disperse.",
                "If a character is about to join interaction, keep them out of the main interaction but mark them in upcoming_active_characters.",
                "The shot_description must include all present characters.",
                "The shot_description must clearly separate interacting characters from observers or background characters.",
                "The shot_description must include visible spatial arrangement such as left, right, center, foreground, midground, or background.",
                "The shot_description must reflect the hierarchy between primary, secondary, observer, and inactive characters.",
                "If transition_type is not none, reflect that change spatially with words such as approaching, shifting, leaving, or regrouping.",
                "Do not explain story meaning, emotion, or plot logic.",
                "Do not group all characters unless the interaction analysis says they are all active participants.",
                "Observers and background characters must not be described as part of the main interaction composition.",
                "Output only the requested JSON structure.",
            ],
            "line_context": line_payload,
            "output_schema": {
                "interaction_analysis": {
                    "focus_character": "CharacterName",
                    "interaction_type": "dialogue",
                    "present_characters": ["A", "B", "C"],
                    "main_interaction_characters": ["A", "B"],
                    "character_states": [
                        {
                            "character": "A",
                            "visibility": "foreground",
                            "participation": "primary",
                            "spatial_role": "leader",
                            "interaction_evidence": [],
                            "observer_evidence": [],
                        }
                    ],
                    "group_structure": "one_to_one_plus_observer",
                    "transition_type": "approach",
                    "entering_characters": [],
                    "exiting_characters": [],
                    "upcoming_active_characters": [],
                },
                "shot_description": "<one or two sentences>",
            },
        }

    def _combined_batch_user_prompt_payload(self, request_entries):
        return {
            "task": "interaction_analysis_and_shot_description_batch",
            "window_size": self.WINDOW_SIZE,
            "instructions": [
                "You will receive a local sequence window containing up to four beats.",
                "Return one result for every beat in the same order and with the same beat_index.",
                "For each beat, first infer the full interaction state for the current script line.",
                "Then generate a one-or-two-sentence shot_description that strictly follows that analysis.",
                "Use previous_line and next_line as the immediate neighboring beats for that beat.",
                "Use context_window_before and context_window_after as short continuity summaries covering up to three additional beats on each side beyond the immediate neighbors.",
                "Current_line is still the primary source of truth for each beat. The short context windows are only auxiliary evidence for continuity, transition, and upcoming participation.",
                "Use focus_character = speaker when a speaker exists. If there is no speaker, infer focus from move or actions.",
                "Choose interaction_type from: dialogue, monologue, confrontation, presentation, observation, movement, idle.",
                "Assume all current_position characters are physically present unless strongly implied otherwise.",
                "For each present character assign exactly one visibility: foreground, midground, background, offscreen.",
                "For each present character assign exactly one participation: primary, secondary, observer, inactive.",
                "Only primary and secondary characters belong to the main interaction.",
                "Observer and background characters must stay outside the main interaction composition.",
                "A character is secondary only if there is direct interaction evidence in the current beat.",
                "Direct interaction evidence includes: being explicitly addressed, being directly responded to, taking part in the same action exchange, being on the current dialogue axis, or being the immediate movement target in a movement beat.",
                "A character is observer if they are only present, only visible, only approaching, only waiting, or only reacting weakly without direct exchange in the current beat.",
                "Physical presence alone is never enough to classify a character as secondary.",
                "If a character will speak in the next few beats but is not yet engaged in the current beat, classify them as observer and put them in upcoming_active_characters instead of secondary.",
                "Do not promote a character to secondary only because context_window_after shows that they become active later.",
                "For dialogue or confrontation, do not label multiple secondary characters unless the line clearly addresses multiple people at once.",
                "Use interaction_evidence and observer_evidence strings to justify each non-primary classification.",
                "For each present character assign one spatial_role from: leader, responder, co_actor, observer, background, target.",
                "Choose group_structure from: one_to_one, one_to_many, many_equal, one_to_one_plus_observer, isolated.",
                "Analyze temporal continuity using move, previous line, and next line.",
                "Use context_window_before and context_window_after to decide whether the current beat is continuing a local exchange, transitioning into one, or breaking away from one.",
                "Choose transition_type from: none, enter, exit, regroup, approach, disperse.",
                "If a character is about to join interaction, keep them out of the main interaction but mark them in upcoming_active_characters.",
                "The shot_description must include all present characters.",
                "The shot_description must clearly separate interacting characters from observers or background characters.",
                "The shot_description must include visible spatial arrangement such as left, right, center, foreground, midground, or background.",
                "The shot_description must reflect the hierarchy between primary, secondary, observer, and inactive characters.",
                "If transition_type is not none, reflect that change spatially with words such as approaching, shifting, leaving, or regrouping.",
                "Do not explain story meaning, emotion, or plot logic.",
                "Do not group all characters unless the interaction analysis says they are all active participants.",
                "Observers and background characters must not be described as part of the main interaction composition.",
                "Output only the requested JSON structure.",
            ],
            "beats": [
                {
                    "beat_index": request["beat_index"],
                    "line_context": request["line_payload"],
                }
                for request in request_entries
            ],
            "output_schema": {
                "results": [
                    {
                        "beat_index": 1,
                        "interaction_analysis": {
                            "focus_character": "CharacterName",
                            "interaction_type": "dialogue",
                            "present_characters": ["A", "B", "C"],
                            "main_interaction_characters": ["A", "B"],
                            "character_states": [
                                {
                                    "character": "A",
                                    "visibility": "foreground",
                                    "participation": "primary",
                                    "spatial_role": "leader",
                                    "interaction_evidence": [],
                                    "observer_evidence": [],
                                }
                            ],
                            "group_structure": "one_to_one_plus_observer",
                            "transition_type": "approach",
                            "entering_characters": [],
                            "exiting_characters": [],
                            "upcoming_active_characters": [],
                        },
                        "shot_description": "<one or two sentences>",
                    }
                ]
            },
        }

    def _analysis_user_prompt_payload(self, line_payload):
        return {
            "task": "interaction_analysis",
            "instructions": [
                "Infer the full interaction state before any shot_description is written.",
                "Use previous_line and next_line as the immediate neighboring beats.",
                "Use context_window_before and context_window_after as short continuity summaries covering up to three additional beats on each side beyond the immediate neighbors.",
                "Current_line is still the primary source of truth. The short context windows are only auxiliary evidence for continuity, transition, and upcoming participation.",
                "Use focus_character = speaker when a speaker exists. If there is no speaker, infer focus from move or actions.",
                "Choose interaction_type from: dialogue, monologue, confrontation, presentation, observation, movement, idle.",
                "Assume all current_position characters are physically present unless strongly implied otherwise.",
                "For each present character assign exactly one visibility: foreground, midground, background, offscreen.",
                "For each present character assign exactly one participation: primary, secondary, observer, inactive.",
                "Only primary and secondary characters belong to the main interaction.",
                "Observer and background characters must stay outside the main interaction composition.",
                "A character is secondary only if there is direct interaction evidence in the current beat.",
                "Direct interaction evidence includes: being explicitly addressed, being directly responded to, taking part in the same action exchange, being on the current dialogue axis, or being the immediate movement target in a movement beat.",
                "A character is observer if they are only present, only visible, only approaching, only waiting, or only reacting weakly without direct exchange in the current beat.",
                "Physical presence alone is never enough to classify a character as secondary.",
                "If a character will speak in the next few beats but is not yet engaged in the current beat, classify them as observer and put them in upcoming_active_characters instead of secondary.",
                "Do not promote a character to secondary only because context_window_after shows that they become active later.",
                "For dialogue or confrontation, do not label multiple secondary characters unless the line clearly addresses multiple people at once.",
                "Use interaction_evidence and observer_evidence strings to justify each non-primary classification.",
                "Examples: if A talks to B while C only watches, then B is secondary and C is observer. If A and B talk now and C speaks next, C is still observer for the current beat.",
                "For each present character assign one spatial_role from: leader, responder, co_actor, observer, background, target.",
                "Choose group_structure from: one_to_one, one_to_many, many_equal, one_to_one_plus_observer, isolated.",
                "Analyze temporal continuity using move, previous line, and next line.",
                "Use context_window_before and context_window_after to decide whether the current beat is continuing a local exchange, transitioning into one, or breaking away from one.",
                "Choose transition_type from: none, enter, exit, regroup, approach, disperse.",
                "If a character is about to join interaction, keep them out of the main interaction but mark them in upcoming_active_characters.",
                "Output only the requested JSON structure.",
            ],
            "line_context": line_payload,
            "output_schema": {
                "focus_character": "CharacterName",
                "interaction_type": "dialogue",
                "present_characters": ["A", "B", "C"],
                "main_interaction_characters": ["A", "B"],
                "character_states": [
                    {
                        "character": "A",
                        "visibility": "foreground",
                        "participation": "primary",
                        "spatial_role": "leader",
                        "interaction_evidence": [],
                        "observer_evidence": [],
                    }
                ],
                "group_structure": "one_to_one_plus_observer",
                "transition_type": "approach",
                "entering_characters": [],
                "exiting_characters": [],
                "upcoming_active_characters": [],
            },
        }

    def _description_user_prompt_payload(self, line_payload, line_analysis):
        return {
            "task": "shot_description_generation",
            "instructions": [
                "Generate one or two sentences describing only visible composition.",
                "Use previous_line and next_line as the immediate neighboring beats.",
                "Use context_window_before and context_window_after only as continuity hints beyond those immediate neighbors.",
                "The shot_description must still describe the current beat's frame, not summarize the whole local sequence.",
                "You MUST include all present characters.",
                "You MUST clearly separate interacting characters from observers or background characters.",
                "You MUST include spatial arrangement such as left, right, center, foreground, midground, or background.",
                "You MUST reflect the hierarchy between primary, secondary, observer, and inactive characters.",
                "If transition_type is not none, reflect that change spatially with words such as approaching, shifting, leaving, or regrouping.",
                "Do not explain story meaning, emotion, or plot logic.",
                "Do not group all characters unless the interaction analysis says they are all active participants.",
                "Observers and background characters must not be described as part of the main interaction composition.",
                "Output only the requested JSON structure.",
            ],
            "line_context": line_payload,
            "interaction_analysis": line_analysis,
            "output_schema": {"shot_description": "<one or two sentences>"},
        }

    def _build_line_payload(self, scene, beat_index, beat, current_positions):
        previous_beat = scene[beat_index - 2] if beat_index - 2 >= 0 else None
        next_beat = scene[beat_index] if beat_index < len(scene) else None
        return {
            "where": self.where,
            "beat_index": beat_index,
            "context_window_radius": self.CONTEXT_WINDOW_RADIUS,
            "previous_line": self._summarize_neighbor_beat(previous_beat),
            "context_window_before": self._summarize_context_window(scene, beat_index, before=True),
            "current_line": {
                "speaker": self._stringify(beat.get("speaker")),
                "content": self._stringify(beat.get("content")),
                "actions": self._normalize_actions(beat.get("actions")),
                "move": self._normalize_moves(beat.get("move")),
                "current_position": current_positions,
                "existing_shot_description": self._stringify(beat.get("shot_description")),
            },
            "next_line": self._summarize_neighbor_beat(next_beat),
            "context_window_after": self._summarize_context_window(scene, beat_index, before=False),
        }

    def _normalize_line_analysis(self, raw_output, line_payload, fallback):
        present_characters = [item["character"] for item in line_payload["current_line"]["current_position"]]
        candidate = raw_output if isinstance(raw_output, dict) else {}
        focus_character = self._coerce_character_name(candidate.get("focus_character"), present_characters, fallback["focus_character"])
        interaction_type = self._coerce_enum(candidate.get("interaction_type"), self.VALID_INTERACTION_TYPES, fallback["interaction_type"])
        group_structure = self._coerce_enum(candidate.get("group_structure"), self.VALID_GROUP_STRUCTURES, fallback["group_structure"])
        transition_type = self._coerce_enum(candidate.get("transition_type"), self.VALID_TRANSITIONS, fallback["transition_type"])
        present_list = self._coerce_character_list(candidate.get("present_characters"), present_characters, present_characters)
        main_interaction = self._coerce_character_list(candidate.get("main_interaction_characters"), present_list, fallback["main_interaction_characters"])
        if not main_interaction and focus_character:
            main_interaction = [focus_character]

        raw_states = candidate.get("character_states")
        state_map = OrderedDict()
        if isinstance(raw_states, list):
            for item in raw_states:
                if not isinstance(item, dict):
                    continue
                character = self._coerce_character_name(item.get("character"), present_list, "")
                if not character or character in state_map:
                    continue
                state_map[character] = {
                    "character": character,
                    "visibility": self._coerce_enum(item.get("visibility"), self.VALID_VISIBILITY, ""),
                    "participation": self._coerce_enum(item.get("participation"), self.VALID_PARTICIPATION, ""),
                    "spatial_role": self._coerce_enum(item.get("spatial_role"), self.VALID_SPATIAL_ROLES, ""),
                    "interaction_evidence": self._normalize_reason_list(item.get("interaction_evidence")),
                    "observer_evidence": self._normalize_reason_list(item.get("observer_evidence")),
                }

        fallback_map = OrderedDict((item["character"], item) for item in fallback["character_states"])
        normalized_states = []
        for character in present_list:
            base = fallback_map[character]
            current = state_map.get(character, {})
            computed_interaction_evidence = self._compute_interaction_evidence(
                line_payload,
                focus_character,
                character,
                interaction_type,
                transition_type,
            )
            computed_observer_evidence = self._compute_observer_evidence(
                line_payload,
                focus_character,
                character,
                interaction_type,
                transition_type,
            )
            normalized_states.append(
                {
                    "character": character,
                    "visibility": current.get("visibility") or base["visibility"],
                    "participation": current.get("participation") or base["participation"],
                    "spatial_role": current.get("spatial_role") or base["spatial_role"],
                    "interaction_evidence": self._dedupe_preserve_order(
                        current.get("interaction_evidence", []) + computed_interaction_evidence
                    ),
                    "observer_evidence": self._dedupe_preserve_order(
                        current.get("observer_evidence", []) + computed_observer_evidence
                    ),
                }
            )

        upcoming_active = self._coerce_character_list(
            candidate.get("upcoming_active_characters"),
            present_list,
            fallback["upcoming_active_characters"],
        )
        normalized_states = self._apply_participation_rules(
            line_payload=line_payload,
            focus_character=focus_character,
            interaction_type=interaction_type,
            group_structure=group_structure,
            upcoming_active_characters=upcoming_active,
            states=normalized_states,
        )
        main_interaction = [
            item["character"]
            for item in normalized_states
            if item["participation"] in {"primary", "secondary"}
        ]
        group_structure = self._infer_group_structure_from_states(normalized_states, interaction_type, group_structure)

        return {
            "focus_character": focus_character,
            "interaction_type": interaction_type,
            "present_characters": present_list,
            "main_interaction_characters": main_interaction,
            "character_states": normalized_states,
            "group_structure": group_structure,
            "transition_type": transition_type,
            "entering_characters": self._coerce_character_list(candidate.get("entering_characters"), present_list, fallback["entering_characters"]),
            "exiting_characters": self._coerce_character_list(candidate.get("exiting_characters"), present_list, fallback["exiting_characters"]),
            "upcoming_active_characters": upcoming_active,
        }

    def _normalize_shot_description(self, raw_output, current_positions, fallback):
        candidate = ""
        if isinstance(raw_output, dict):
            candidate = raw_output.get("shot_description") or raw_output.get("description") or ""
        candidate = self._clean_description(candidate)
        if not self._is_valid_shot_description(candidate, current_positions):
            candidate = self._clean_description(fallback)
        return candidate

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

    def _is_valid_shot_description(self, text, current_positions):
        if not text or "\n" in text:
            return False
        if self._count_sentences(text) < 1 or self._count_sentences(text) > 2:
            return False
        present_characters = [item["character"] for item in current_positions if item.get("character")]
        if any(character not in text for character in present_characters):
            return False
        spatial_keywords = ("left", "right", "center", "foreground", "background", "midground", "前景", "背景", "中景", "左", "右", "中间")
        lowered = text.lower()
        return any(keyword in lowered or keyword in text for keyword in spatial_keywords)

    def _fallback_line_analysis(self, beat, line_payload):
        current_positions = line_payload["current_line"]["current_position"]
        present_characters = [item["character"] for item in current_positions]
        speaker = self._stringify(beat.get("speaker"))
        moves = self._normalize_moves(beat.get("move"))
        future_window_speakers = self._context_window_speakers(line_payload.get("context_window_after", []))
        moving_characters = [item["character"] for item in moves]
        action_characters = [item["character"] for item in self._normalize_actions(beat.get("actions")) if item.get("character") in present_characters]
        content = self._stringify(beat.get("content"))

        focus_character = speaker or (moving_characters[0] if moving_characters else "") or (action_characters[0] if action_characters else "")
        if not focus_character and present_characters:
            focus_character = present_characters[0]

        interaction_type = "idle"
        group_structure = "isolated"
        transition_type = "none"
        entering_characters = []
        exiting_characters = []
        upcoming_active_characters = []
        main_interaction = []

        mentioned_characters = [character for character in present_characters if character != focus_character and character and character in content]
        secondary_candidates = []
        for character in mentioned_characters + action_characters + present_characters:
            if character == focus_character or character in secondary_candidates:
                continue
            secondary_candidates.append(character)

        if moves:
            interaction_type = "movement"
            transition_type = "regroup" if len(moves) >= 2 else ("approach" if len(present_characters) > 1 else "enter")
            entering_characters = list(moving_characters)

        if speaker:
            if len(present_characters) == 1:
                interaction_type = "monologue"
                main_interaction = [focus_character]
                group_structure = "isolated"
            else:
                if not secondary_candidates:
                    for character in present_characters:
                        if character != focus_character:
                            secondary_candidates.append(character)
                            break
                active_count = 1 + min(len(secondary_candidates), max(1, len(present_characters) - 1))
                main_interaction = [focus_character] + secondary_candidates[: active_count - 1]
                if active_count == 2 and len(present_characters) > 2:
                    group_structure = "one_to_one_plus_observer"
                elif active_count == 2:
                    group_structure = "one_to_one"
                else:
                    group_structure = "one_to_many"
                if interaction_type == "idle":
                    interaction_type = "dialogue" if active_count <= 2 else "presentation"
        elif moves:
            stationary = [character for character in present_characters if character not in moving_characters]
            main_interaction = list(moving_characters)
            if stationary:
                main_interaction.append(stationary[0])
            if len(main_interaction) >= 3:
                group_structure = "one_to_many"
            elif len(main_interaction) == 2:
                group_structure = "one_to_one_plus_observer" if len(present_characters) > 2 else "one_to_one"
            else:
                group_structure = "isolated"
            upcoming_active_characters = [character for character in moving_characters if character not in main_interaction[:1]]
        elif len(action_characters) >= 2:
            interaction_type = "observation"
            main_interaction = action_characters[:2]
            group_structure = "one_to_one_plus_observer" if len(present_characters) > 2 else "one_to_one"
        elif present_characters:
            main_interaction = [focus_character]
            group_structure = "isolated" if len(present_characters) == 1 else "one_to_one_plus_observer"

        for character in future_window_speakers:
            if character in present_characters and character not in main_interaction and character not in upcoming_active_characters:
                upcoming_active_characters.append(character)

        if not main_interaction and focus_character:
            main_interaction = [focus_character]

        character_states = []
        for index, character in enumerate(present_characters):
            if character == focus_character:
                participation = "primary"
                visibility = "foreground"
                spatial_role = "leader"
            elif character in main_interaction:
                participation = "secondary"
                visibility = "midground"
                spatial_role = "responder" if index == 1 else "co_actor"
            elif character in upcoming_active_characters:
                participation = "observer"
                visibility = "background"
                spatial_role = "observer"
            else:
                participation = "observer" if len(present_characters) > 1 else "inactive"
                visibility = "background" if len(present_characters) > 1 else "foreground"
                spatial_role = "observer" if participation == "observer" else "background"
            character_states.append(
                {
                    "character": character,
                    "visibility": visibility,
                    "participation": participation,
                    "spatial_role": spatial_role,
                    "interaction_evidence": self._compute_interaction_evidence(
                        line_payload,
                        focus_character,
                        character,
                        interaction_type,
                        transition_type,
                    ),
                    "observer_evidence": self._compute_observer_evidence(
                        line_payload,
                        focus_character,
                        character,
                        interaction_type,
                        transition_type,
                    ),
                }
            )

        character_states = self._apply_participation_rules(
            line_payload=line_payload,
            focus_character=focus_character,
            interaction_type=interaction_type,
            group_structure=group_structure,
            upcoming_active_characters=upcoming_active_characters,
            states=character_states,
        )
        main_interaction = [
            item["character"]
            for item in character_states
            if item["participation"] in {"primary", "secondary"}
        ]
        group_structure = self._infer_group_structure_from_states(character_states, interaction_type, group_structure)

        return {
            "focus_character": focus_character,
            "interaction_type": interaction_type,
            "present_characters": list(present_characters),
            "main_interaction_characters": list(OrderedDict((character, True) for character in main_interaction).keys()),
            "character_states": character_states,
            "group_structure": group_structure,
            "transition_type": transition_type,
            "entering_characters": entering_characters,
            "exiting_characters": exiting_characters,
            "upcoming_active_characters": upcoming_active_characters,
        }

    def _fallback_shot_description(self, line_payload, line_analysis):
        states = line_analysis["character_states"]
        if not states:
            return "The frame stays centered on the empty space with no visible characters."

        primary = [item["character"] for item in states if item["participation"] == "primary"]
        secondary = [item["character"] for item in states if item["participation"] == "secondary"]
        observers = [
            item["character"]
            for item in states
            if item["participation"] in {"observer", "inactive"} and item["character"] not in primary + secondary
        ]
        focus = primary[0] if primary else states[0]["character"]

        if len(states) == 1:
            return f"{focus} stands centered in the foreground as the only visible subject."

        if secondary:
            if len(secondary) == 1:
                sentence_one = f"{focus} stands on the left facing {secondary[0]} on the right in the foreground"
            else:
                sentence_one = f"{focus} stands centered in the foreground addressing {self._format_name_list(secondary)} across the midground"
        else:
            sentence_one = f"{focus} remains centered in the foreground while the frame stays open around {focus}"
        if observers:
            sentence_one += f" while {self._format_name_list(observers)} remain visible in the background observing"
        sentence_one = self._ensure_sentence(sentence_one)

        transition_type = line_analysis["transition_type"]
        if transition_type == "none":
            return sentence_one

        moving = line_analysis["entering_characters"] or line_analysis["upcoming_active_characters"]
        if moving:
            sentence_two = f"{self._format_name_list(moving)} continue {self._transition_verb(transition_type)} through the midground."
        else:
            sentence_two = f"The composition continues {self._transition_verb(transition_type)} across the frame."
        return sentence_one + " " + sentence_two

    def _resolve_current_positions(self, beat, current_position_state):
        raw_positions = beat.get("current position")
        positions = []
        if isinstance(raw_positions, list) and raw_positions:
            for item in raw_positions:
                if not isinstance(item, dict):
                    continue
                character = self._stringify(item.get("character"))
                position_id = self._stringify(item.get("position") or item.get("position_id"))
                if character and position_id:
                    positions.append({"character": character, "position_id": position_id})
        else:
            for character, position_id in current_position_state.items():
                positions.append({"character": character, "position_id": position_id})

        seen = set()
        deduped = []
        for item in positions:
            key = (item["character"], item["position_id"])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    def _advance_position_state(self, current_position_state, beat):
        updated = OrderedDict(current_position_state)
        for move in self._normalize_moves(beat.get("move")):
            updated[move["character"]] = move["destination"]

        raw_positions = beat.get("current position")
        if isinstance(raw_positions, list):
            for item in raw_positions:
                if not isinstance(item, dict):
                    continue
                character = self._stringify(item.get("character"))
                position_id = self._stringify(item.get("position") or item.get("position_id"))
                if character and position_id:
                    updated[character] = position_id
        return updated

    def _summarize_neighbor_beat(self, beat):
        if not isinstance(beat, dict):
            return {}
        return {
            "speaker": self._stringify(beat.get("speaker")),
            "content": self._compact_text(self._stringify(beat.get("content")), 180),
            "actions": self._normalize_actions(beat.get("actions")),
            "move": self._normalize_moves(beat.get("move")),
        }

    def _summarize_context_window(self, scene, beat_index, before):
        if not isinstance(scene, list):
            return []
        current_index = beat_index - 1
        if before:
            immediate_previous_index = current_index - 1
            end = max(0, immediate_previous_index)
            start = max(0, end - self.CONTEXT_WINDOW_RADIUS)
            indices = range(start, end)
        else:
            start = min(len(scene), current_index + 2)
            end = min(len(scene), start + self.CONTEXT_WINDOW_RADIUS)
            indices = range(start, end)

        summarized = []
        for index in indices:
            beat = scene[index]
            if not isinstance(beat, dict):
                continue
            summary = self._summarize_neighbor_beat(beat)
            summary["beat_index"] = index + 1
            summary["relative_offset"] = (index + 1) - beat_index
            summarized.append(summary)
        return summarized

    def _context_window_speakers(self, window):
        speakers = []
        seen = set()
        for item in window if isinstance(window, list) else []:
            if not isinstance(item, dict):
                continue
            speaker = self._stringify(item.get("speaker"))
            if not speaker or speaker in seen:
                continue
            seen.add(speaker)
            speakers.append(speaker)
        return speakers

    def _normalize_actions(self, actions):
        result = []
        for action in actions if isinstance(actions, list) else []:
            if not isinstance(action, dict):
                continue
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
            character = self._stringify(move.get("character"))
            destination = self._stringify(move.get("destination") or move.get("position") or move.get("position_id"))
            if character and destination:
                result.append({"character": character, "destination": destination})
        return result

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
            character = self._stringify(item.get("character"))
            position_id = self._stringify(item.get("position") or item.get("position_id"))
            if character and position_id:
                state[character] = position_id
        return state

    def _coerce_character_name(self, value, candidates, fallback):
        if isinstance(value, str) and value.strip() in candidates:
            return value.strip()
        return fallback

    def _coerce_character_list(self, value, candidates, fallback):
        if not isinstance(value, list):
            return list(fallback)
        normalized = []
        seen = set()
        for item in value:
            if not isinstance(item, str):
                continue
            item = item.strip()
            if not item or item not in candidates or item in seen:
                continue
            seen.add(item)
            normalized.append(item)
        return normalized or list(fallback)

    def _coerce_enum(self, value, valid_values, fallback):
        if isinstance(value, str) and value.strip() in valid_values:
            return value.strip()
        return fallback

    def _clean_description(self, text):
        text = self._compact_text(self._stringify(text), 360)
        text = text.strip().strip("\"'“”‘’")
        text = re.sub(r"\s+", " ", text)
        sentences = [segment.strip() for segment in re.split(r"(?<=[.!?。！？])\s+", text) if segment.strip()]
        return " ".join(sentences[:2]).strip() if sentences else ""

    def _count_sentences(self, text):
        sentences = [segment.strip() for segment in re.split(r"(?<=[.!?。！？])\s+", text) if segment.strip()]
        return len(sentences) if sentences else 1

    def _ensure_sentence(self, text):
        text = self._stringify(text)
        if not text:
            return ""
        return text if text[-1] in ".!?。！？" else text + "."

    def _format_name_list(self, values):
        values = [value for value in values if value]
        if not values:
            return ""
        if len(values) == 1:
            return values[0]
        if len(values) == 2:
            return f"{values[0]} and {values[1]}"
        return ", ".join(values[:-1]) + f", and {values[-1]}"

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

    def _stringify(self, value):
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        return str(value).strip()

    def _compact_text(self, text, limit=320):
        text = self._stringify(text)
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 3)].rstrip() + "..."

    def _transition_verb(self, transition_type):
        mapping = {
            "none": "staying stable",
            "enter": "entering",
            "exit": "leaving",
            "regroup": "regrouping",
            "approach": "approaching",
            "disperse": "dispersing",
        }
        return mapping.get(transition_type, "shifting")

    def _normalize_reason_list(self, value):
        if not isinstance(value, list):
            return []
        normalized = []
        seen = set()
        for item in value:
            if not isinstance(item, str):
                continue
            item = item.strip()
            if not item or item in seen:
                continue
            seen.add(item)
            normalized.append(item)
        return normalized

    def _dedupe_preserve_order(self, values):
        normalized = []
        seen = set()
        for item in values:
            if not isinstance(item, str):
                continue
            item = item.strip()
            if not item or item in seen:
                continue
            seen.add(item)
            normalized.append(item)
        return normalized

    def _compute_interaction_evidence(self, line_payload, focus_character, character, interaction_type, transition_type):
        if not character or character == focus_character:
            return []
        evidence = []
        current_line = line_payload.get("current_line", {})
        content = self._stringify(current_line.get("content"))
        speaker = self._stringify(current_line.get("speaker"))
        next_speaker = self._stringify(line_payload.get("next_line", {}).get("speaker"))
        previous_speaker = self._stringify(line_payload.get("previous_line", {}).get("speaker"))
        actions = current_line.get("actions", [])
        moves = current_line.get("move", [])
        current_positions = current_line.get("current_position", [])
        action_characters = {item["character"] for item in actions if item.get("character")}
        moving_characters = {item["character"] for item in moves if item.get("character")}
        present_characters = [item["character"] for item in current_positions if item.get("character")]
        stationary_characters = [item for item in present_characters if item not in moving_characters]

        if character and character in content:
            evidence.append("named_in_current_dialogue")
        if focus_character and focus_character in content and character in content:
            evidence.append("shares_dialogue_reference_with_focus")
        if character in action_characters and focus_character in action_characters and len(action_characters) >= 2:
            evidence.append("shares_current_action_exchange")
        if speaker and next_speaker == character and character != speaker:
            evidence.append("immediate_reply_target")
        if speaker and previous_speaker == character and character != speaker:
            evidence.append("reply_chain_from_previous_beat")
        if interaction_type == "movement" and character in moving_characters:
            evidence.append("actively_moving_in_current_beat")
        if (
            interaction_type == "movement"
            and transition_type in {"approach", "regroup"}
            and character not in moving_characters
            and len(moving_characters) > 0
            and len(stationary_characters) == 1
            and character == stationary_characters[0]
        ):
            evidence.append("movement_destination_target")
        return self._dedupe_preserve_order(evidence)

    def _compute_observer_evidence(self, line_payload, focus_character, character, interaction_type, transition_type):
        if not character or character == focus_character:
            return []
        evidence = []
        current_line = line_payload.get("current_line", {})
        content = self._stringify(current_line.get("content"))
        speaker = self._stringify(current_line.get("speaker"))
        next_speaker = self._stringify(line_payload.get("next_line", {}).get("speaker"))
        future_window_speakers = self._context_window_speakers(line_payload.get("context_window_after", []))
        actions = current_line.get("actions", [])
        moves = current_line.get("move", [])
        action_characters = {item["character"] for item in actions if item.get("character")}
        moving_characters = {item["character"] for item in moves if item.get("character")}

        if character not in content and character not in action_characters and character not in moving_characters:
            evidence.append("only_present_in_frame")
        if next_speaker == character and character != speaker:
            evidence.append("upcoming_but_not_currently_active")
        elif character in future_window_speakers and character != speaker:
            evidence.append("upcoming_within_short_context_window")
        if interaction_type == "movement" and character not in moving_characters and transition_type in {"approach", "regroup"}:
            evidence.append("watching_current_relocation")
        return self._dedupe_preserve_order(evidence)

    def _apply_participation_rules(self, line_payload, focus_character, interaction_type, group_structure, upcoming_active_characters, states):
        corrected = []
        for item in states:
            state = copy.deepcopy(item)
            character = state["character"]
            direct_score = len(state.get("interaction_evidence", []))
            is_upcoming = character in upcoming_active_characters

            if character == focus_character:
                state["participation"] = "primary"
                state["visibility"] = "foreground"
                state["spatial_role"] = "leader"
            elif is_upcoming:
                state["participation"] = "observer"
                if state["visibility"] == "foreground":
                    state["visibility"] = "background"
                if state["spatial_role"] not in {"observer", "background"}:
                    state["spatial_role"] = "observer"
            elif state["participation"] == "secondary" and direct_score == 0:
                state["participation"] = "observer"
                if state["visibility"] == "foreground":
                    state["visibility"] = "background"
                if state["spatial_role"] not in {"observer", "background"}:
                    state["spatial_role"] = "observer"
            elif state["participation"] in {"observer", "inactive"} and direct_score > 0:
                state["participation"] = "secondary"
                if state["visibility"] == "background":
                    state["visibility"] = "midground"
                if state["spatial_role"] in {"observer", "background"}:
                    state["spatial_role"] = "responder"

            corrected.append(state)

        if interaction_type in {"dialogue", "confrontation", "monologue"} or group_structure in {"one_to_one", "one_to_one_plus_observer", "isolated"}:
            secondaries = [item for item in corrected if item["participation"] == "secondary"]
            if len(secondaries) > 1:
                ranked = sorted(
                    secondaries,
                    key=lambda item: (
                        -len(item.get("interaction_evidence", [])),
                        item["character"] != self._stringify(line_payload.get("next_line", {}).get("speaker")),
                        item["character"],
                    ),
                )
                keep = ranked[0]["character"]
                for item in corrected:
                    if item["participation"] == "secondary" and item["character"] != keep:
                        item["participation"] = "observer"
                        item["visibility"] = "background"
                        item["spatial_role"] = "observer"
                        item["observer_evidence"] = self._dedupe_preserve_order(
                            item.get("observer_evidence", []) + ["not_on_current_main_dialogue_axis"]
                        )

        return corrected

    def _infer_group_structure_from_states(self, states, interaction_type, fallback):
        active = [item for item in states if item["participation"] in {"primary", "secondary"}]
        observers = [item for item in states if item["participation"] in {"observer", "inactive"}]
        secondary_count = sum(1 for item in active if item["participation"] == "secondary")
        if len(active) <= 1:
            return "isolated"
        if secondary_count == 1:
            return "one_to_one_plus_observer" if observers else "one_to_one"
        if interaction_type == "presentation":
            return "one_to_many"
        if secondary_count >= 2 and observers:
            return "one_to_many"
        if secondary_count >= 2:
            return "many_equal"
        return fallback
