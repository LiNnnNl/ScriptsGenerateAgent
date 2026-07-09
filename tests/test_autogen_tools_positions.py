import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


class AutogenToolsPositionTest(unittest.TestCase):
    def setUp(self):
        from src.resource_loader import ResourceLoader

        self.loader = ResourceLoader()
        self.scene = self.loader.get_scene_by_id("scene_school_rooftop")

    def _script_with_shared_positions(self):
        return [
            {
                "position_descriptions": {
                    "Position 1": "天台中央",
                },
                "scene information": {
                    "who": ["林静", "陈屿"],
                    "where": "学校天台",
                    "what": "两人在天台对话",
                },
                "initial position": [
                    {"character": "林静", "position": "Position 1"},
                    {"character": "陈屿", "position": "Position 1"},
                ],
                "scene": [
                    {
                        "speaker": "林静",
                        "content": "你也听见了吗？",
                        "shot_blend": "Cut",
                        "shot": "character",
                        "shot_type": "中景",
                        "shot_description": "",
                        "Follow": 0,
                        "actions": [],
                        "current position": [
                            {"character": "林静", "position": "Position 1"},
                            {"character": "陈屿", "position": "Position 1"},
                        ],
                    }
                ],
            }
        ]

    def test_validate_rejects_shared_positions_in_one_beat(self):
        from src.autogen_tools import validate_script_constraints

        result = validate_script_constraints(
            self._script_with_shared_positions(),
            self.scene,
            self.loader,
        )

        self.assertFalse(result["valid"])
        self.assertTrue(any("多个角色共用同一站位" in item for item in result["errors"]))

    def test_auto_fix_spreads_shared_positions(self):
        from src.autogen_tools import auto_fix_script, validate_script_constraints

        fixed = auto_fix_script(self._script_with_shared_positions(), self.scene, self.loader)
        initial_positions = {
            item["character"]: item["position"]
            for item in fixed[0]["initial position"]
        }
        current_positions = {
            item["character"]: item["position"]
            for item in fixed[0]["scene"][0]["current position"]
        }

        self.assertEqual(len(set(initial_positions.values())), 2)
        self.assertEqual(len(set(current_positions.values())), 2)
        self.assertTrue(validate_script_constraints(fixed, self.scene, self.loader)["valid"])


if __name__ == "__main__":
    unittest.main()
