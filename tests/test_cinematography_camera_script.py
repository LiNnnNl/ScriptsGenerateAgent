import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from src.cinematography import _build_camera_script
from src.schema import validate_camera_script


class CinematographyCameraScriptTests(unittest.TestCase):
    def test_environment_beat_uses_current_position_as_target_fallback(self):
        script = [
            {
                "scene information": {"where": "太空站"},
                "initial position": [{"character": "陈屿", "position": "Position 1"}],
                "scene": [
                    {
                        "content": "警报声在舱内回荡。",
                        "shot": "character",
                        "shot_type": "中景",
                        "shot_blend": "cut",
                        "Follow": 0,
                        "shot_description": "Alarm lights sweep across the cabin.",
                        "current position": [{"character": "陈屿", "position": "Position 1"}],
                    }
                ],
            }
        ]
        camera_lib = {"中景": {"DefaultMotionPreset": "none"}}

        result = _build_camera_script(script, camera_lib)
        event = result["scenes"][0]["events"][0]

        self.assertEqual("陈屿", event["target"])
        self.assertEqual("Position 1", event["target_position"])

    def test_object_beat_uses_marker_anchor_without_target_position(self):
        script = [{
            "scene information": {"where": "MinNan"},
            "initial position": [],
            "scene": [{
                "speaker": "",
                "content": "香炉里的香灰微微震动。",
                "duration": 3.0,
                "actions": [],
                "shot": "object",
                "target": "香炉 (2)",
                "target_anchor": "center",
                "shot_type": "物体特写",
                "shot_blend": "cut",
                "shot_description": "Close-up of ash trembling inside the incense burner.",
            }],
        }]
        camera_lib = {"物体特写": {"DefaultMotionPreset": "slow_push"}}

        event = _build_camera_script(script, camera_lib)["scenes"][0]["events"][0]

        self.assertEqual("object", event["shot"])
        self.assertEqual("香炉 (2)", event["target"])
        self.assertEqual("center", event["target_anchor"])
        self.assertNotIn("target_position", event)
        self.assertTrue(validate_camera_script({"scenes": [{"shot_index": 0, "events": [event]}]})["valid"])


if __name__ == "__main__":
    unittest.main()
