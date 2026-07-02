import unittest
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from src.autogen_pipeline import _normalize_direct_scene, _parse_plaintext_script


class DirectModeTests(unittest.TestCase):
    def test_plaintext_direct_scene_gets_required_runtime_fields(self):
        scenes = _parse_plaintext_script("陈屿：醒醒。\n林静：警报还在响。")
        normalized = _normalize_direct_scene(
            scenes[0],
            fallback_names=[],
            scene_name="太空站",
            what_snippet="测试直接模式",
        )

        self.assertEqual(["陈屿", "林静"], normalized["scene information"]["who"])
        self.assertEqual("太空站", normalized["scene information"]["where"])
        self.assertEqual(2, len(normalized["initial position"]))
        self.assertIn("Position 1", normalized["position_descriptions"])
        self.assertIn("Position 2", normalized["position_descriptions"])

        for beat in normalized["scene"]:
            self.assertEqual("character", beat["shot"])
            self.assertEqual("Cut", beat["shot_blend"])
            self.assertEqual("中景", beat["shot_type"])
            self.assertEqual(0, beat["Follow"])
            self.assertEqual("", beat["shot_description"])
            self.assertTrue(beat["current position"])

    def test_json_direct_move_keeps_scene_shot_and_camera(self):
        normalized = _normalize_direct_scene(
            {
                "scene": [
                    {
                        "move": {"character": "陈屿", "destination": "Position 2"},
                    },
                    {
                        "speaker": "陈屿",
                        "content": "我到了。",
                    },
                ],
            },
            fallback_names=["陈屿"],
            scene_name="太空站",
            what_snippet="测试移动",
        )

        move_beat = normalized["scene"][0]
        dialogue_beat = normalized["scene"][1]
        self.assertEqual("scene", move_beat["shot"])
        self.assertEqual(1, move_beat["camera"])
        self.assertEqual([{"character": "陈屿", "destination": "Position 2"}], move_beat["move"])
        self.assertEqual("Position 1", move_beat["current position"][0]["position"])
        self.assertEqual("Position 2", dialogue_beat["current position"][0]["position"])

    def test_walk_and_talk_direct_beat_is_scene_shot(self):
        normalized = _normalize_direct_scene(
            {
                "scene": [
                    {
                        "speaker": "陈屿",
                        "content": "边走边说。",
                        "move": {"character": "陈屿", "destination": "Position 2"},
                    },
                ],
            },
            fallback_names=["陈屿"],
            scene_name="太空站",
            what_snippet="测试边走边说",
        )

        beat = normalized["scene"][0]
        self.assertEqual("scene", beat["shot"])
        self.assertEqual(1, beat["camera"])
        self.assertNotIn("shot_type", beat)
        self.assertNotIn("Follow", beat)

    def test_collapsed_initial_positions_are_spread_by_character(self):
        normalized = _normalize_direct_scene(
            {
                "scene information": {"who": ["陈屿", "林静", "老赵"]},
                "initial position": [
                    {"character": "陈屿", "position": "Position 1"},
                    {"character": "林静", "position": "Position 1"},
                    {"character": "老赵", "position": "Position 1"},
                ],
                "scene": [
                    {"speaker": "陈屿", "content": "都别挤在一起。"},
                    {"speaker": "林静", "content": "站位重新分开。"},
                ],
            },
            fallback_names=[],
            scene_name="太空站",
            what_snippet="测试塌缩站位",
        )

        self.assertTrue(normalized.pop("_direct_position_repair_applied"))
        self.assertEqual(
            [
                {"character": "陈屿", "position": "Position 1"},
                {"character": "林静", "position": "Position 2"},
                {"character": "老赵", "position": "Position 3"},
            ],
            normalized["initial position"],
        )
        for beat in normalized["scene"]:
            self.assertEqual(
                ["Position 1", "Position 2", "Position 3"],
                [entry["position"] for entry in beat["current position"]],
            )

    def test_collapsed_current_positions_are_spread_by_character(self):
        normalized = _normalize_direct_scene(
            {
                "scene information": {"who": ["陈屿", "林静"]},
                "initial position": [
                    {"character": "陈屿", "position": "Position 1"},
                    {"character": "林静", "position": "Position 2"},
                ],
                "scene": [
                    {
                        "speaker": "陈屿",
                        "content": "当前位置塌了。",
                        "current position": [
                            {"character": "陈屿", "position": "Position 1"},
                            {"character": "林静", "position": "Position 1"},
                        ],
                    }
                ],
            },
            fallback_names=[],
            scene_name="太空站",
            what_snippet="测试 current position 塌缩",
        )

        self.assertTrue(normalized.pop("_direct_position_repair_applied"))
        self.assertEqual(
            [
                {"character": "陈屿", "position": "Position 1"},
                {"character": "林静", "position": "Position 2"},
            ],
            normalized["scene"][0]["current position"],
        )


if __name__ == "__main__":
    unittest.main()
