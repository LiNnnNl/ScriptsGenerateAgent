import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


class ScriptStyleSkillTest(unittest.TestCase):
    def test_all_four_styles_have_actionable_and_distinct_guidance(self):
        from src.script_style_skill import ScriptStyleSkill

        skill = ScriptStyleSkill()
        styles = skill.available_styles()

        self.assertEqual(4, len(styles))
        expected_rules = {
            "douyin_short": "开篇第一个节拍",
            "vertical_drama": "幕尾钩子",
            "film": "人物弧光通过选择及其后果呈现",
            "standup": "铺垫—升级—兑现—回扣",
        }
        for style_id, expected in expected_rules.items():
            context = skill.render_context("", requested_style_id=style_id)
            self.assertIn("#### 执行规则", context)
            self.assertIn(expected, context)

    def test_detects_explicit_standup_style(self):
        from src.script_style_skill import ScriptStyleSkill

        result = ScriptStyleSkill().detect("写一个关于打工人的脱口秀，语言要犀利。")

        self.assertTrue(result["explicit"])
        self.assertEqual("standup", result["selected"]["style_id"])
        self.assertIn("脱口秀", result["selected"]["guidance"])

    def test_unspecified_style_uses_common_anti_bias(self):
        from src.script_style_skill import ScriptStyleSkill

        context = ScriptStyleSkill().render_context("两个机器人在空间站争论一个秘密。")

        self.assertIn("ScriptStyleSkill 剧本风格规则", context)
        self.assertIn("剧本风格已由导演在流程入口统一识别并锁定", context)
        self.assertIn("未明确指定", context)
        self.assertIn("不为追求“完整”擅自添加大团圆", context)
        self.assertIn("用户明确剧情与硬约束 > 用户选择的剧本风格 > 剧情倾向", context)
        self.assertNotIn("get_script_style_rules", context)

    def test_button_style_overrides_idea_detection(self):
        from src.script_style_skill import ScriptStyleSkill

        result = ScriptStyleSkill().resolve("我想写一个脱口秀", requested_style_id="film")
        context = ScriptStyleSkill().render_context("我想写一个脱口秀", requested_style_id="film")

        self.assertEqual("user_button", result["source"])
        self.assertEqual("film", result["selected"]["style_id"])
        self.assertIn("来源: 用户按钮选择", context)

    def test_auto_style_uses_idea_detection(self):
        from src.script_style_skill import ScriptStyleSkill

        result = ScriptStyleSkill().resolve("写一个抖音爆款短视频", requested_style_id="auto")

        self.assertEqual("idea_auto", result["source"])
        self.assertEqual("douyin_short", result["selected"]["style_id"])


if __name__ == "__main__":
    unittest.main()
