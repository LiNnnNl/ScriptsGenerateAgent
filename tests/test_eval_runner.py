import json
import unittest
from pathlib import Path

from eval.run_eval import DEFAULT_CASES, build_payload


class EvalRunnerTests(unittest.TestCase):
    def test_eval_cases_are_valid_payloads(self):
        cases = json.loads(Path(DEFAULT_CASES).read_text(encoding="utf-8"))

        self.assertEqual(4, len(cases))
        for case in cases:
            with self.subTest(case=case["id"]):
                payload = build_payload(case)
                self.assertEqual(case["creative_idea"], payload["creative_idea"])
                self.assertEqual(case["act_count"], payload["act_count"])
                self.assertEqual(case["character_count"], payload["required_character_count"])
                self.assertEqual(case["character_count"], len(payload["custom_characters"]))
                self.assertTrue(payload["scene_id"])
                self.assertEqual(case["scene_pool"], payload["scene_pool"])
                self.assertEqual(case["act_scenes"], payload["act_scenes"])

    def test_case_ids_are_unique(self):
        cases = json.loads(Path(DEFAULT_CASES).read_text(encoding="utf-8"))
        ids = [case["id"] for case in cases]

        self.assertEqual(len(ids), len(set(ids)))


if __name__ == "__main__":
    unittest.main()
