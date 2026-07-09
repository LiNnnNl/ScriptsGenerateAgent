import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


class ScriptToneSkillTest(unittest.TestCase):
    def test_button_tone_renders_fixed_guidance(self):
        from src.script_tone_skill import ScriptToneSkill

        result = ScriptToneSkill().resolve("comedy")
        context = ScriptToneSkill().render_context("comedy")

        self.assertTrue(result["explicit"])
        self.assertEqual("comedy", result["selected"]["tone_id"])
        self.assertIn("喜剧", context)
        self.assertIn("所有 Agent 必须遵守本节固定规则", context)

    def test_unselected_tone_does_not_force_bias(self):
        from src.script_tone_skill import ScriptToneSkill

        result = ScriptToneSkill().resolve("")
        context = ScriptToneSkill().render_context("")

        self.assertFalse(result["explicit"])
        self.assertEqual("unspecified", result["selected"]["tone_id"])
        self.assertIn("不要额外强加温情、伤感、喜剧", context)


if __name__ == "__main__":
    unittest.main()
