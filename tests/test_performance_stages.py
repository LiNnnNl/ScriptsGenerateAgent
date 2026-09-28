import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace


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
    enabled = True

    def __init__(self, responses):
        self.responses = list(responses)
        self.payloads = []

    def complete_json(self, _system, payload):
        self.payloads.append(payload)
        return self.responses.pop(0)


class PerformanceStagesTest(unittest.TestCase):
    def test_action_stage_sends_compact_context_and_replaces_only_actions(self):
        from src.performance.action import ActionSelectionStage

        actions = [
            SimpleNamespace(action_id="Talk", description="温和说话", compatible_states=["standing"]),
            SimpleNamespace(action_id="Wave", description="挥手", compatible_states=["standing"]),
        ]
        client = FakeClient([{"results": [{
            "event_index": 0,
            "actions": [{"character": "A", "action": "Wave", "motion_detail": "Wave briefly"}],
        }]}])
        script = [{
            "scene information": {"who": ["A"], "what": "A 向朋友道别"},
            "initial position": [{"character": "A", "position": "P1", "state": "standing"}],
            "scene": [{
                "speaker": "A", "content": "再见。", "actions": [{"character": "A", "action": "Talk"}],
                "shot_description": "A 在门口挥手。", "current position": [{"character": "A", "position": "P1"}],
            }],
        }]

        result = ActionSelectionStage(script, actions, client).run()["script"]

        self.assertEqual("Wave", result[0]["scene"][0]["actions"][0]["action"])
        event = client.payloads[0]["events"][0]
        self.assertEqual({"event_index", "characters", "current_actions", "locked_action_characters", "script_description", "visual_description"}, set(event))
        self.assertNotIn("current position", event)

    def test_expression_stage_sends_only_expression_and_text_context(self):
        from src.performance.expression import ExpressionSelectionStage
        from src.resource_loader import ResourceLoader

        loader = ResourceLoader()
        client = FakeClient([{"results": [{
            "event_index": 0,
            "emotion": [{"character": "阿福", "emotion": "happy_soft", "emotionStyle": "steady"}],
            "confidence": 0.9,
            "reason": "重逢时克制地高兴",
        }]}])
        script = [{
            "scene information": {"who": ["阿福"], "what": "阿福与家人重逢"},
            "initial position": [],
            "scene": [{
                "speaker": "阿福", "content": "你回来了。", "actions": [{"character": "阿福", "action": "Standing Talking 1"}],
                "emotion": "normal", "shot_description": "阿福望向门口，笑意渐起。",
            }],
        }]

        result = ExpressionSelectionStage(script, loader, client).run()["script"]

        self.assertEqual("pixar_cartoon", result[0]["scene information"]["emotionLibrary"])
        self.assertEqual("happy_soft", result[0]["scene"][0]["emotion"][0]["emotion"])
        event = client.payloads[0]["events"][0]
        self.assertEqual({"event_index", "current_emotion", "script_description", "visual_description"}, set(event))
        self.assertNotIn("actions", event)

    def test_action_stage_clears_stale_actions_from_empty_shots(self):
        from src.performance.action import ActionSelectionStage

        actions = [SimpleNamespace(action_id="Wave", description="挥手", compatible_states=["standing"])]
        client = FakeClient([{"results": [{"event_index": 1, "actions": []}]}])
        script = [{
            "scene information": {"who": ["A"]},
            "initial position": [{"character": "A", "state": "standing"}],
            "scene": [
                {"speaker": "", "content": "窗帘晃动", "actions": [{"character": "A", "action": "Wave"}]},
                {"speaker": "A", "content": "进来了。", "actions": []},
            ],
        }]

        result = ActionSelectionStage(script, actions, client).run()["script"]

        self.assertEqual([], result[0]["scene"][0]["actions"])
        self.assertEqual([1], [event["event_index"] for event in client.payloads[0]["events"]])

    def test_expression_stage_skips_environment_only_empty_shots(self):
        from src.performance.expression import ExpressionSelectionStage
        from src.resource_loader import ResourceLoader

        loader = ResourceLoader()
        client = FakeClient([{"results": [{
            "event_index": 1,
            "emotion": "happy_soft",
            "confidence": 0.8,
            "reason": "人物镜头中露出笑意",
        }]}])
        script = [{
            "scene information": {"who": ["阿福"], "what": "阿福进门"},
            "initial position": [],
            "scene": [
                {"speaker": "", "content": "门缓缓打开。", "actions": [], "emotion": "normal"},
                {"speaker": "阿福", "content": "我回来了。", "actions": [], "emotion": "normal"},
            ],
        }]

        result = ExpressionSelectionStage(script, loader, client).run()["script"]

        self.assertEqual([1], [event["event_index"] for event in client.payloads[0]["events"]])
        self.assertEqual("normal", result[0]["scene"][0]["emotion"])
        self.assertEqual(1.0, result[0]["scene"][0]["confidence"])
        self.assertEqual("happy_soft", result[0]["scene"][1]["emotion"])


if __name__ == "__main__":
    unittest.main()
