import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


class EmptyShotFlowTest(unittest.TestCase):
    def test_shared_guard_repairs_agent_overwrite(self):
        from src.scene_segments import is_empty_shot, protect_empty_shot

        beat = {
            "speaker": " ",
            "content": "",
            "shot": "character",
            "shot_type": "近景",
            "Follow": 1,
            "actions": [{"character": "A", "action": "Speech"}],
        }

        self.assertTrue(is_empty_shot(beat))
        protect_empty_shot(beat, ensure_camera=True)

        self.assertEqual("scene", beat["shot"])
        self.assertEqual("5s", beat["duration"])
        self.assertEqual([], beat["actions"])
        self.assertEqual(1, beat["camera"])
        self.assertNotIn("shot_type", beat)
        self.assertNotIn("Follow", beat)

    def test_shot_schema_rejects_character_empty_shot(self):
        from src.schema import validate_script_shot_structure

        result = validate_script_shot_structure(
            [{"scene": [{
                "speaker": "",
                "content": "",
                "duration": "5s",
                "actions": [],
                "shot": "character",
                "shot_blend": "Cut",
                "shot_type": "中景",
                "Follow": 0,
            }]}]
        )

        self.assertFalse(result["valid"])
        self.assertIn("空镜的 shot 必须为 'scene'", result["errors"][0]["errors"])

    def test_camera_stage_skips_empty_shot_assignment(self):
        from src.cinematography.camera_planning_stage import CameraPlanningStage

        script = {
            "scene information": {"where": "TestScene"},
            "initial position": [{"character": "A", "position": "Position 1"}],
            "scene": [{
                "speaker": "",
                "content": "",
                "duration": "7s",
                "shot": "character",
                "shot_type": "近景",
                "Follow": 1,
                "shot_blend": "Cut",
                "shot_description": "空间站外部全景。",
                "actions": [],
            }],
        }
        scene_info = {
            "where": "TestScene",
            "regions": [{"name": "Main", "description": "open plaza", "anchors": [{}]}],
        }
        camera_lib = {"中景": {"画面范围": "medium", "主要用途": "dialogue"}}

        with tempfile.TemporaryDirectory() as tmp:
            stage = CameraPlanningStage(
                script_json=script,
                scene_info_json=scene_info,
                camera_lib_json=camera_lib,
                output_dir=tmp,
                stage_output_dir=tmp,
            )
            result = stage.run()

        beat = result["script_with_camera_plan"]["scene"][0]
        self.assertEqual("scene", beat["shot"])
        self.assertEqual("7s", beat["duration"])
        self.assertEqual("空间站外部全景。", beat["shot_description"])
        self.assertNotIn("shot_type", beat)
        self.assertNotIn("Follow", beat)
        self.assertEqual([], stage.assignment_results)

    def test_final_generator_preserves_empty_shot_camera_contract(self):
        from src.json_generator import ScriptJSONGenerator
        from src.resource_loader import Scene

        scene = Scene({
            "id": "TestScene",
            "name": "TestScene",
            "description": "test",
            "valid_positions": [],
        })
        generator = ScriptJSONGenerator([], scene)
        result = generator.generate_final_json(
            [{
                "scene information": {"where": "TestScene"},
                "initial position": [],
                "scene": [{
                    "speaker": "",
                    "content": "",
                    "duration": "5s",
                    "shot": "scene",
                    "shot_blend": "Cut",
                    "camera": 1,
                    "shot_description": "空间站外部全景。",
                    "actions": [],
                }],
            }],
            "test",
            preserve_shot_fields=True,
        )

        beat = result[0]["scene"][0]
        self.assertEqual("scene", beat["shot"])
        self.assertEqual(1, beat["camera"])
        self.assertEqual("空间站外部全景。", beat["shot_description"])


if __name__ == "__main__":
    unittest.main()
