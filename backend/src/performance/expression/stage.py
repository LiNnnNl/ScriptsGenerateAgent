import copy
import json
import math
import re

from ...resource_loader import load_emotion_libraries
from ...scene_segments import is_empty_shot
from ..context import build_event_context
from .rules import SYSTEM_PROMPT


class ExpressionSelectionStage:
    """Select final expressions from a compatible library using filtered beat views."""

    WINDOW_SIZE = 8
    _WEIGHTED = re.compile(r"\s*([^:,\s]+)\s*:\s*(\d+(?:\.\d+)?)\s*")

    def __init__(self, script, resource_loader, llm_client, progress_callback=None):
        self.script = copy.deepcopy(script)
        self.client = llm_client
        self.progress_callback = progress_callback
        self.libraries = load_emotion_libraries(resource_loader.resource_dir)
        context_path = resource_loader.resource_dir / "pixar_emotions_context.md"
        self.emotion_context = context_path.read_text(encoding="utf-8-sig") if context_path.exists() else ""

    def run(self):
        for act_index, scene_obj in enumerate(self.script):
            if not isinstance(scene_obj, dict):
                continue
            who = list((scene_obj.get("scene information") or {}).get("who") or [])
            library_name, library = self._compatible_library(scene_obj, who)
            scene_obj.setdefault("scene information", {})["emotionLibrary"] = library_name
            if not library_name:
                for beat in scene_obj.get("scene", []):
                    if isinstance(beat, dict):
                        beat.update(emotion="", confidence=0.0, reason="无兼容表情库")
                continue
            beats = scene_obj.get("scene", [])
            neutral_emotion = "normal" if "normal" in library.get("emotions", []) else library["emotions"][0]
            for start in range(0, len(beats), self.WINDOW_SIZE):
                for index in range(start, min(start + self.WINDOW_SIZE, len(beats))):
                    if isinstance(beats[index], dict) and is_empty_shot(beats[index]):
                        beats[index].update(
                            emotion=neutral_emotion,
                            confidence=1.0,
                            reason="环境空镜，无人物表演",
                        )
                event_indices = [
                    index for index in range(start, min(start + self.WINDOW_SIZE, len(beats)))
                    if isinstance(beats[index], dict) and not is_empty_shot(beats[index])
                ]
                if event_indices:
                    self._run_window(act_index, scene_obj, event_indices, who, library_name, library)
        return {"script": self.script}

    def _compatible_library(self, scene_obj, who):
        compatible = []
        who_set = set(who)
        for name, library in sorted(self.libraries.items()):
            targets = set(library.get("target_characters", []))
            if library.get("emotions") and library.get("styles") and (not targets or who_set <= targets):
                compatible.append((name, library))
        current = (scene_obj.get("scene information") or {}).get("emotionLibrary")
        return next(
            ((name, library) for name, library in compatible if name == current),
            compatible[0] if compatible else ("", {}),
        )

    def _run_window(self, act_index, scene_obj, event_indices, who, library_name, library):
        events = []
        for event_index in event_indices:
            beat = scene_obj["scene"][event_index]
            script_description, visual_description = build_event_context(scene_obj, event_index)
            events.append({
                "event_index": event_index,
                "current_emotion": {
                    "emotion": copy.deepcopy(beat.get("emotion", "")),
                    "confidence": beat.get("confidence", 0.0),
                    "reason": beat.get("reason", ""),
                },
                "script_description": script_description,
                "visual_description": visual_description,
            })
        payload = {
            "task": "select_expressions",
            "act_index": act_index,
            "characters": who,
            "emotion_library": library_name,
            "emotion_candidates": list(library.get("emotions", [])),
            "style_candidates": list(library.get("styles", [])),
            "emotion_context": self.emotion_context,
            "events": events,
        }
        raw = self.client.complete_json(SYSTEM_PROMPT, payload)
        result_map = self._result_map(raw, event_indices)
        for event_index in event_indices:
            result = result_map[event_index]
            self._validate_emotion(result.get("emotion"), who, library)
            confidence = result.get("confidence")
            reason = result.get("reason")
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
                raise ValueError("ExpressionSelectionAgent confidence must be between 0 and 1")
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError("ExpressionSelectionAgent reason must be non-empty")
            scene_obj["scene"][event_index].update(
                emotion=copy.deepcopy(result["emotion"]),
                confidence=float(confidence),
                reason=reason.strip(),
            )
        self._emit("success", "expression_selection", f"已完成第 {act_index + 1} 幕 {len(event_indices)} 个镜头的表情选择")

    @staticmethod
    def _result_map(raw, event_indices):
        results = raw.get("results") if isinstance(raw, dict) else None
        if not isinstance(results, list):
            raise ValueError("ExpressionSelectionAgent must return results[]")
        mapped = {}
        for item in results:
            if not isinstance(item, dict):
                raise ValueError("ExpressionSelectionAgent results must be objects")
            event_index = item.get("event_index")
            if event_index in mapped or event_index not in event_indices:
                raise ValueError("ExpressionSelectionAgent returned invalid event coverage")
            mapped[event_index] = item
        if set(mapped) != set(event_indices):
            raise ValueError("ExpressionSelectionAgent must return every requested event exactly once")
        return mapped

    def _validate_emotion(self, value, who, library):
        allowed_emotions = set(library.get("emotions", []))
        allowed_styles = set(library.get("styles", []))
        if isinstance(value, str):
            self._validate_expression(value, allowed_emotions)
            return
        if not isinstance(value, list) or not value:
            raise ValueError("ExpressionSelectionAgent emotion must be a string or non-empty list")
        seen = set()
        for item in value:
            if not isinstance(item, dict):
                raise ValueError("ExpressionSelectionAgent emotion assignments must be objects")
            character = item.get("character")
            if character not in who or character in seen:
                raise ValueError(f"ExpressionSelectionAgent returned invalid character: {character}")
            if item.get("emotionStyle") not in allowed_styles:
                raise ValueError(f"ExpressionSelectionAgent returned invalid emotionStyle: {item.get('emotionStyle')}")
            self._validate_expression(item.get("emotion"), allowed_emotions)
            seen.add(character)

    def _validate_expression(self, value, allowed):
        if not isinstance(value, str) or not value:
            raise ValueError("ExpressionSelectionAgent returned an empty emotion")
        if ":" not in value:
            if value not in allowed:
                raise ValueError(f"ExpressionSelectionAgent returned invalid emotion: {value}")
            return
        names, weights = set(), []
        for part in value.split(","):
            match = self._WEIGHTED.fullmatch(part)
            if not match or match.group(1) not in allowed or match.group(1) in names:
                raise ValueError(f"ExpressionSelectionAgent returned invalid weighted emotion: {value}")
            names.add(match.group(1))
            weights.append(float(match.group(2)))
        if not 1 <= len(weights) <= 3:
            raise ValueError("ExpressionSelectionAgent emotion must contain 1 to 3 emotions")
        if any(weight < 0 or weight > 1 for weight in weights) or not math.isclose(sum(weights), 1, abs_tol=1e-6):
            raise ValueError(f"ExpressionSelectionAgent weights must sum to 1: {value}")

    def _emit(self, level, phase, message):
        if self.progress_callback:
            self.progress_callback(level, phase, message)
