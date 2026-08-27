import sys
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


class PromptFilesTest(unittest.TestCase):
    def test_resource_loader_default_path_is_backend_resources(self):
        from src.resource_loader import ResourceLoader

        loader = ResourceLoader()

        self.assertEqual(BACKEND / "resources", loader.resource_dir)
        self.assertGreater(len(loader.characters), 0)
        self.assertGreater(len(loader.scenes), 0)

    def test_character_generation_prompt_keeps_dynamic_inputs(self):
        from src.prompt_renderers.character_generation import (
            build_character_generation_prompt,
            character_generation_system_prompt,
        )

        prompt = build_character_generation_prompt(
            character_count=2,
            scene_desc="测试场景：一座安静的空间站",
            creative_idea="两人发现失联信号",
            char_instructions="\n\n已指定角色（必须包含，完善其档案）：\n- 林静\n",
            model_instruction="\n\n## 可用角色模型列表\n- gameobject_name: \"F01\"",
        )

        self.assertIn("测试场景：一座安静的空间站", prompt)
        self.assertIn("两人发现失联信号", prompt)
        self.assertIn("输出恰好 2 位角色", prompt)
        self.assertIn("gameobject_name", prompt)
        self.assertIn("角色设计师", character_generation_system_prompt)

    def test_autogen_prompt_builders_are_importable(self):
        from src.prompt_renderers.autogen_agent_prompts import (
            build_character_bios_system_message,
            build_director_word_system_message,
            build_synopsis_system_message,
            build_title_system_message,
            build_validation_system_message,
        )
        from src.resource_loader import ResourceLoader

        loader = ResourceLoader()
        scene = loader.get_scene_by_id("Auditorium") or loader.get_all_scenes()[0]
        director_word_prompt = build_director_word_system_message([], scene, loader, 2, 2)

        self.assertIn("剧本导演AI", director_word_prompt)
        self.assertIn("shot_description", director_word_prompt)
        self.assertIn("不要把 `shot_description` 留空", director_word_prompt)
        self.assertIn("character_bios", build_character_bios_system_message())
        self.assertIn("synopsis", build_synopsis_system_message())
        self.assertIn('"title"', build_title_system_message())
        self.assertIn("_validate_constraints", build_validation_system_message())

    def test_action_prompt_is_compact_without_losing_action_ids(self):
        from src.prompt_renderers.action_info import render_action_info
        from src.resource_loader import ACTION_POSTURE_CATEGORIES, ResourceLoader

        loader = ResourceLoader()
        actions = [
            action
            for state, _ in ACTION_POSTURE_CATEGORIES
            for action in loader.get_actions_by_state(state)
        ]
        expanded = "\n".join(
            f"- **{action.action_id}**: {action.description}"
            for action in actions
        )
        compact = render_action_info(loader)

        self.assertTrue(actions)
        self.assertTrue(all(action.action_id in compact for action in actions))
        self.assertLess(len(compact), len(expanded) * 0.75)

    def test_cinematography_prompt_modules_are_importable(self):
        from src.prompt_files.cinematography_position_grouping import cinematography_position_grouping_prompt
        from src.prompt_files.cinematography_position_planning import cinematography_position_planning_prompt
        from src.prompt_renderers.camera_planning_stage import camera_analysis_user_instructions
        from src.prompt_renderers.position_agent import build_position_agent_stage1_prompt_text
        from src.prompt_renderers.shot_planning_stage import shot_combined_user_instructions

        self.assertIn("分组", cinematography_position_grouping_prompt)
        self.assertIn("区域规划", cinematography_position_planning_prompt)
        self.assertIn("7 个位置", build_position_agent_stage1_prompt_text(7))
        self.assertIn("Output only the requested JSON structure.", shot_combined_user_instructions)
        self.assertTrue(any("camera_subject" in item for item in camera_analysis_user_instructions))

    def test_prompt_files_are_single_text_variables(self):
        prompt_dir = ROOT / "backend" / "src" / "prompt_files"
        offenders = []
        for path in sorted(prompt_dir.glob("*.py")):
            if path.name == "__init__.py":
                continue
            text = path.read_text(encoding="utf-8")
            if re.search(r"^(from|import|def|class)\b", text, flags=re.M):
                offenders.append(f"{path.name} contains code/imports")
                continue
            matches = re.findall(r"^[a-zA-Z_][a-zA-Z0-9_]*_prompt\s*=\s*\"\"\".*\"\"\"\s*$", text, flags=re.S)
            if len(matches) != 1:
                offenders.append(f"{path.name} must contain exactly one *_prompt triple-quoted variable")
        self.assertEqual([], offenders)


if __name__ == "__main__":
    unittest.main()
