import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if "openai" not in sys.modules:
    openai_stub = types.ModuleType("openai")
    openai_stub.OpenAI = object
    sys.modules["openai"] = openai_stub
if "dotenv" not in sys.modules:
    dotenv_stub = types.ModuleType("dotenv")
    dotenv_stub.load_dotenv = lambda: None
    sys.modules["dotenv"] = dotenv_stub


class FakeClient:
    def __init__(self, responder):
        self.responder = responder
        self.calls = []

    def complete_json(self, system_prompt, payload):
        self.calls.append((system_prompt, payload))
        return self.responder(payload)


def sample_script(character="阿福"):
    return [{
        "scene information": {
            "who": [character],
            "where": "Auditorium",
            "what": "角色发现真相后试图保持镇定",
            "emotionLibrary": "",
        },
        "initial position": [{"character": character, "position": "Position 1", "state": "standing"}],
        "scene": [{
            "event_index": 0,
            "current position": [{"character": character, "position": "Position 1"}],
            "speaker": character,
            "content": "原来你早就知道。",
            "actions": [],
            "emotion": "normal",
            "confidence": 0.5,
            "reason": "旧判断",
            "shot_description": "中景拍摄角色压住怒意，手指微微收紧。",
        }],
    }]


class PerformanceAgentsTest(unittest.TestCase):
    def test_action_agent_receives_filtered_event_and_only_replaces_actions(self):
        from src.performance.action import ActionSelectionStage
        from src.resource_loader import POSTURE_TRANSITION_TARGETS, ResourceLoader

        loader = ResourceLoader()
        candidate = next(
            action for action in loader.get_actions_by_state("standing")
            if action.action_id not in POSTURE_TRANSITION_TARGETS
        )
        client = FakeClient(lambda payload: {
            "results": [{
                "event_index": event["event_index"],
                "actions": [{
                    "character": "阿福",
                    "action": candidate.action_id,
                    "motion_detail": "Clenches one hand while speaking",
                }],
            } for event in payload["events"]]
        })

        original = sample_script()
        result = ActionSelectionStage(original, loader, client).run()
        event_payload = client.calls[0][1]["events"][0]
        self.assertEqual({
            "event_index", "current_actions", "locked_action_characters",
            "script_description", "visual_description", "characters",
        }, set(event_payload))
        self.assertNotIn("speaker", event_payload)
        self.assertNotIn("content", event_payload)
        self.assertNotIn("current position", event_payload)
        self.assertEqual("原来你早就知道。", result["script"][0]["scene"][0]["content"])
        self.assertEqual(candidate.action_id, result["script"][0]["scene"][0]["actions"][0]["action"])
        self.assertEqual([], original[0]["scene"][0]["actions"])

    def test_expression_rejects_more_than_three_weighted_emotions(self):
        from src.performance.expression import ExpressionSelectionStage
        from src.resource_loader import ResourceLoader

        stage = ExpressionSelectionStage(sample_script(), ResourceLoader(), FakeClient(lambda _: {}))
        with self.assertRaises(ValueError):
            stage._validate_expression(
                "happy_soft:0.25,sad_soft:0.25,angry_soft:0.25,normal:0.25",
                {"happy_soft", "sad_soft", "angry_soft", "normal"},
            )

    def test_expression_agent_receives_only_expression_and_descriptions(self):
        from src.performance.expression import ExpressionSelectionStage
        from src.resource_loader import ResourceLoader

        loader = ResourceLoader()
        client = FakeClient(lambda payload: {
            "results": [{
                "event_index": event["event_index"],
                "emotion": [{
                    "character": "阿福",
                    "emotion": "angry_soft",
                    "emotionStyle": "steady",
                }],
                "confidence": 0.86,
                "reason": "台词克制但画面显示怒意",
            } for event in payload["events"]]
        })

        result = ExpressionSelectionStage(sample_script(), loader, client).run()
        event_payload = client.calls[0][1]["events"][0]
        self.assertIn("## Emotions", client.calls[0][1]["emotion_context"])
        self.assertEqual({
            "event_index", "current_emotion", "script_description", "visual_description",
        }, set(event_payload))
        self.assertNotIn("actions", event_payload)
        self.assertNotIn("speaker", event_payload)
        self.assertNotIn("content", event_payload)
        self.assertEqual("pixar_cartoon", result["script"][0]["scene information"]["emotionLibrary"])
        self.assertEqual("angry_soft", result["script"][0]["scene"][0]["emotion"][0]["emotion"])
        self.assertEqual("原来你早就知道。", result["script"][0]["scene"][0]["content"])

    def test_expression_agent_is_skipped_without_compatible_library(self):
        from src.performance.expression import ExpressionSelectionStage
        from src.resource_loader import ResourceLoader

        client = FakeClient(lambda payload: self.fail("agent should not be called"))
        result = ExpressionSelectionStage(sample_script("未入库角色"), ResourceLoader(), client).run()

        self.assertEqual([], client.calls)
        self.assertEqual("", result["script"][0]["scene information"]["emotionLibrary"])
        self.assertEqual("", result["script"][0]["scene"][0]["emotion"])


if __name__ == "__main__":
    unittest.main()
