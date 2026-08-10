import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from src.cinematography import _build_camera_script


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


if __name__ == "__main__":
    unittest.main()
