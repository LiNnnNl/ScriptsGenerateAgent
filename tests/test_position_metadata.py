import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


class PositionMetadataTest(unittest.TestCase):
    def setUp(self):
        from src.resource_loader import ResourceLoader

        self.loader = ResourceLoader()
        self.scene = self.loader.get_scene_by_id("scene_school_rooftop")

    @staticmethod
    def _legacy_scene():
        return {
            "position_descriptions": {
                "Position 1": "天台中央 - 林静面对入口等待陈屿，需保留双人对话取景空间",
                "Position 2": "栏杆边 - 陈屿停步眺望城市，随后转身回应林静",
            },
            "scene information": {
                "who": ["林静", "陈屿"],
                "where": "scene_school_rooftop",
                "what": "两人在天台谈话",
            },
            "initial position": [
                {"character": "林静", "position": "Position 1", "state": "standing"},
                {"character": "陈屿", "position": "Position 2", "state": "standing"},
            ],
            "scene": [],
        }

    def test_normalizer_upgrades_legacy_descriptions(self):
        from src.position_metadata import normalize_position_metadata

        metadata = normalize_position_metadata(self._legacy_scene(), self.scene)

        self.assertEqual(1, metadata["Position 1"]["number"])
        self.assertTrue(metadata["Position 1"]["name"])
        self.assertIn("林静", metadata["Position 1"]["description"])

    def test_generator_emits_only_canonical_metadata(self):
        from src.json_generator import ScriptJSONGenerator

        result = ScriptJSONGenerator([], self.scene).generate_final_json(
            [self._legacy_scene()],
            "测试",
        )

        self.assertIn("position_metadata", result[0])
        self.assertNotIn("position_descriptions", result[0])
        self.assertEqual(
            {"number", "name", "description"},
            set(result[0]["position_metadata"]["Position 1"]),
        )

    def test_scene_resource_positions_have_number_name_description(self):
        position = self.scene.valid_positions[0]

        self.assertIsInstance(position["number"], int)
        self.assertTrue(position["name"])
        self.assertTrue(position["description"])

    def test_position_plan_entries_receive_metadata(self):
        from src.position_metadata import attach_position_metadata, normalize_position_metadata

        metadata = normalize_position_metadata(self._legacy_scene(), self.scene)
        plan = {
            "where": self.scene.id,
            "groups": [],
            "singles": [{"position_id": "Position 1", "character": "林静"}],
        }
        attach_position_metadata(plan, metadata)

        self.assertEqual(1, plan["singles"][0]["number"])
        self.assertTrue(plan["singles"][0]["name"])
        self.assertIn("林静", plan["singles"][0]["description"])

    def test_validator_rejects_duplicate_numbers(self):
        from src.position_metadata import validate_position_metadata

        scene = self._legacy_scene()
        scene["position_metadata"] = {
            "Position 1": {"number": 1, "name": "等待位", "description": "林静等待的位置"},
            "Position 2": {"number": 1, "name": "回应位", "description": "陈屿回应的位置"},
        }

        self.assertTrue(any("重复" in error for error in validate_position_metadata(scene)))


if __name__ == "__main__":
    unittest.main()
