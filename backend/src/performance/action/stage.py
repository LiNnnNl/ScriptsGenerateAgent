import copy

from ...resource_loader import POSTURE_TRANSITION_TARGETS
from ..context import build_event_context
from .rules import SYSTEM_PROMPT


class ActionSelectionStage:
    """Select final action IDs without exposing the full script to the model."""

    WINDOW_SIZE = 8

    def __init__(self, script, resource_loader, llm_client, progress_callback=None):
        self.script = copy.deepcopy(script)
        self.actions = list(resource_loader.actions if hasattr(resource_loader, "actions") else resource_loader)
        self.client = llm_client
        self.progress_callback = progress_callback
        self.actions_by_id = {action.action_id: action for action in self.actions}

    def run(self):
        for act_index, scene_obj in enumerate(self.script):
            if not isinstance(scene_obj, dict):
                continue
            states = {
                item.get("character"): item.get("state", "standing")
                for item in scene_obj.get("initial position", [])
                if isinstance(item, dict) and item.get("character")
            }
            beats = scene_obj.get("scene", [])
            for beat in beats:
                if isinstance(beat, dict) and "move" not in beat and beat.get("speaker") == "":
                    beat["actions"] = []
            selectable = [
                index for index, beat in enumerate(beats)
                if isinstance(beat, dict) and "move" not in beat and beat.get("speaker") != ""
            ]
            cursor = 0
            while cursor < len(beats):
                window = [index for index in selectable if cursor <= index < cursor + self.WINDOW_SIZE]
                if window:
                    self._run_window(act_index, scene_obj, window, states)
                for event_index in range(cursor, min(cursor + self.WINDOW_SIZE, len(beats))):
                    self._advance_states(beats[event_index], states)
                cursor += self.WINDOW_SIZE
        return {"script": self.script}

    def _run_window(self, act_index, scene_obj, event_indices, states):
        who = list((scene_obj.get("scene information") or {}).get("who") or states)
        events = []
        for event_index in event_indices:
            beat = scene_obj["scene"][event_index]
            script_description, visual_description = build_event_context(scene_obj, event_index)
            current_actions = beat.get("actions", []) if isinstance(beat.get("actions"), list) else []
            locked = [
                self._public_action(action) for action in current_actions
                if isinstance(action, dict) and action.get("action") in POSTURE_TRANSITION_TARGETS
            ]
            events.append({
                "event_index": event_index,
                "current_actions": [self._public_action(action) for action in current_actions if isinstance(action, dict)],
                "locked_action_characters": [action.get("character") for action in locked if action.get("character")],
                "script_description": script_description,
                "visual_description": visual_description,
                "characters": who,
            })

        relevant_states = set(states.values())
        for event in events:
            original = scene_obj["scene"][event["event_index"]].get("actions", [])
            relevant_states.update(
                POSTURE_TRANSITION_TARGETS[action.get("action")]
                for action in original if isinstance(action, dict) and action.get("action") in POSTURE_TRANSITION_TARGETS
            )
        payload = {
            "task": "select_actions",
            "act_index": act_index,
            "initial_character_states": dict(states),
            "action_candidates": [
                {
                    "action": action.action_id,
                    "description": action.description,
                    "compatible_states": list(action.compatible_states),
                }
                for action in self.actions
                if action.action_id not in POSTURE_TRANSITION_TARGETS
                and relevant_states.intersection(action.compatible_states)
            ],
            "events": events,
        }
        raw = self.client.complete_json(SYSTEM_PROMPT, payload)
        result_map = self._result_map(raw, event_indices)
        simulated_states = dict(states)
        for event_index in event_indices:
            beat = scene_obj["scene"][event_index]
            original = beat.get("actions", []) if isinstance(beat.get("actions"), list) else []
            locked = [copy.deepcopy(action) for action in original if action.get("action") in POSTURE_TRANSITION_TARGETS]
            locked_characters = {action.get("character") for action in locked}
            interaction_by_character = {
                action.get("character"): copy.deepcopy(action.get("then-interact"))
                for action in original if action.get("then-interact") is not None
            }
            selected = []
            seen = set()
            for item in result_map[event_index].get("actions", []):
                if not isinstance(item, dict):
                    raise ValueError("ActionSelectionAgent actions must be objects")
                character = item.get("character")
                action_id = item.get("action")
                if character not in who or character in seen or character in locked_characters:
                    raise ValueError(f"ActionSelectionAgent returned invalid or duplicate character: {character}")
                action = self.actions_by_id.get(action_id)
                state = simulated_states.get(character, "standing")
                if action is None or not self._is_compatible(action, state) or action_id in POSTURE_TRANSITION_TARGETS:
                    raise ValueError(f"ActionSelectionAgent returned incompatible action {action_id!r} for {character!r} in {state!r}")
                entry = {
                    "character": character,
                    "state": state,
                    "action": action_id,
                    "motion_detail": str(item.get("motion_detail") or "").strip(),
                }
                if character in interaction_by_character:
                    entry["then-interact"] = interaction_by_character[character]
                selected.append(entry)
                seen.add(character)
            missing_interactions = set(interaction_by_character) - seen - locked_characters
            if missing_interactions:
                raise ValueError(
                    "ActionSelectionAgent must select an action for interaction characters: "
                    + ", ".join(sorted(missing_interactions))
                )
            for entry in locked:
                character = entry.get("character")
                entry["state"] = simulated_states.get(character, entry.get("state", "standing"))
                selected.append(entry)
                target = POSTURE_TRANSITION_TARGETS.get(entry.get("action"))
                if character and target:
                    simulated_states[character] = target
            beat["actions"] = selected
        self._emit("success", "action_selection", f"已完成第 {act_index + 1} 幕 {len(event_indices)} 个镜头的动作选择")

    @staticmethod
    def _public_action(action):
        return {
            key: action.get(key)
            for key in ("character", "action", "motion_detail", "then-interact")
            if key in action
        }

    @staticmethod
    def _result_map(raw, event_indices):
        results = raw.get("results") if isinstance(raw, dict) else None
        if not isinstance(results, list):
            raise ValueError("ActionSelectionAgent must return results[]")
        mapped = {}
        for item in results:
            if not isinstance(item, dict):
                raise ValueError("ActionSelectionAgent results must be objects")
            event_index = item.get("event_index")
            if event_index in mapped or event_index not in event_indices or not isinstance(item.get("actions"), list):
                raise ValueError("ActionSelectionAgent returned invalid event coverage")
            mapped[event_index] = item
        if set(mapped) != set(event_indices):
            raise ValueError("ActionSelectionAgent must return every requested event exactly once")
        return mapped

    @staticmethod
    def _advance_states(beat, states):
        for action in beat.get("actions", []) if isinstance(beat, dict) and isinstance(beat.get("actions"), list) else []:
            if not isinstance(action, dict):
                continue
            target = POSTURE_TRANSITION_TARGETS.get(action.get("action"))
            if action.get("character") and target:
                states[action["character"]] = target

    @staticmethod
    def _is_compatible(action, state):
        checker = getattr(action, "is_compatible_with_state", None)
        return checker(state) if callable(checker) else state in getattr(action, "compatible_states", [])

    def _emit(self, level, phase, message):
        if self.progress_callback:
            self.progress_callback(level, phase, message)
