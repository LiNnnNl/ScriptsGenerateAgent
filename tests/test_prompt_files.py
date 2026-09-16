import sys
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


class PromptFilesTest(unittest.TestCase):
    def test_character_context_preserves_full_reference_names(self):
        from src.prompt_renderers.autogen_agent_prompts import _render_character_info, _build_stage_common_context
        from src.resource_loader import ResourceLoader

        loader = ResourceLoader()
        character = next(c for c in loader.characters if c.name == '艾莉 (F-01)')
        context = _render_character_info([character], 1, 0)
        self.assertIn('艾莉 (F-01)', context)
        self.assertIn('包括空格和括号内编号', context)
        self.assertIn('不要为补全姓名改写对白', context)
        common = _build_stage_common_context([character], loader.get_scene_by_id('Auditorium'))
        self.assertIn('不得缩写', common)

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

    def test_title_agent_package_owns_factory_and_prompts(self):
        from src.agents.title import build_system_message, build_user_prompt, create_agent

        class FakeAgent:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        agent = create_agent(object(), assistant_agent_cls=FakeAgent)

        self.assertEqual("TitleAgent", agent.kwargs["name"])
        self.assertIn('"title"', build_system_message())
        self.assertEqual("请为以下剧本摘要命名：\n测试摘要", build_user_prompt("测试摘要"))

    def test_creative_agent_packages_own_factories(self):
        from importlib import import_module

        class FakeAgent:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        for package, agent_name in {
            "concept_pitch": "ConceptPitchAgent",
            "character_voice": "CharacterVoiceAgent",
            "narrative_arch": "NarrativeArchAgent",
            "meeting_summary": "MeetingSummaryAgent",
            "treatment": "TreatmentAgent",
            "critic": "CriticAgent",
            "dialogue": "DialogueAgent",
            "director_word": "DirectorAgent_Word",
            "concept": "ConceptAgent",
            "position": "PositionAgent",
        }.items():
            module = import_module(f"src.agents.{package}")
            agent = module.create_agent(object(), "测试提示词", assistant_agent_cls=FakeAgent)
            self.assertEqual(agent_name, agent.kwargs["name"])
            self.assertEqual("测试提示词", agent.kwargs["system_message"])

        for package, agent_name in {
            "story_ir": "StoryIRAgent",
            "revision": "RevisionAgent",
            "contract_repair": "ContractRepairAgent",
            "shot_plan": "ShotPlanAgent",
            "synopsis": "SynopsisAgent",
            "character_bios": "CharacterBiosAgent",
        }.items():
            module = import_module(f"src.agents.{package}")
            agent = module.create_agent(object(), assistant_agent_cls=FakeAgent)
            self.assertEqual(agent_name, agent.kwargs["name"])
            self.assertEqual(module.build_system_message(), agent.kwargs["system_message"])

        director = import_module("src.agents.director")
        direct_agent = director.create_agent(
            object(), "测试提示词", direct_mode=True, assistant_agent_cls=FakeAgent
        )
        self.assertEqual("DirectorAgent_Direct", direct_agent.kwargs["name"])
        self.assertEqual("测试提示词", direct_agent.kwargs["system_message"])

        validation = import_module("src.agents.validation")
        validation_agent = validation.create_agent(
            object(), ["tool"], assistant_agent_cls=FakeAgent
        )
        self.assertEqual("ValidationAgent", validation_agent.kwargs["name"])
        self.assertEqual(["tool"], validation_agent.kwargs["tools"])

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

    def test_director_prompt_uses_compact_intermediate_contract(self):
        from src.prompt_renderers.autogen_agent_prompts import (
            build_director_system_message,
            build_story_ir_system_message,
        )
        from src.resource_loader import ResourceLoader

        loader = ResourceLoader()
        scene = loader.get_scene_by_id("Auditorium") or loader.get_all_scenes()[0]
        prompt = build_director_system_message([], scene, loader, 2, 1)

        self.assertIn("精简剧本 JSON", prompt)
        self.assertIn("event_id", prompt)
        self.assertIn("`current position`", prompt)
        self.assertNotIn("## 最终格式合同", prompt)
        self.assertNotIn("当前资源取值库", prompt)
        self.assertIn('shot="object"', prompt)
        self.assertIn("scene_markers", prompt)
        self.assertIn('"events"', build_story_ir_system_message())

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
