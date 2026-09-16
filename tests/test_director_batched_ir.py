import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


class DirectorBatchedIRTests(unittest.TestCase):
    def test_numbered_storyboard_is_frozen_into_bounded_event_batches(self):
        from src.autogen_pipeline import _event_batches, _event_manifest

        text = "\n".join(f"镜头{i}：画面{i}" for i in range(1, 28))
        manifest, explicit, error = _event_manifest(text, 1, None, [], [])

        self.assertIsNone(error)
        self.assertTrue(explicit)
        self.assertEqual(27, len(manifest[0]))
        self.assertEqual([8, 8, 8, 3], [len(batch) for batch in _event_batches(manifest[0])])
        self.assertEqual("A01E0027", manifest[0][-1]["event_id"])

    def test_numbered_storyboard_keeps_dialogue_and_ignores_trailing_count_instruction(self):
        from src.autogen_pipeline import _event_manifest, _freeze_explicit_story_ir

        text = "\n".join([
            '镜头1：陈阿嫲说道：“原句，不准改。”',
            "镜头2：许砚辰转身。",
            "【请保留全部台词，总共2条镜头】",
        ])
        manifest, explicit, error = _event_manifest(text, 1, None, [], ["陈阿嫲", "许砚辰"])
        frozen = _freeze_explicit_story_ir(manifest)

        self.assertIsNone(error)
        self.assertTrue(explicit)
        self.assertEqual(2, len(frozen[0]))
        self.assertEqual("陈阿嫲", frozen[0][0]["speaker"])
        self.assertEqual("原句，不准改。", frozen[0][0]["content"])

    def test_dialogue_speaker_is_not_confused_with_later_addressee(self):
        from src.autogen_pipeline import _event_manifest, _freeze_explicit_story_ir

        text = '镜头1：陈阿嫲上下打量许砚辰，随后说道：“你要好好问。”'
        manifest, _, _ = _event_manifest(text, 1, None, [], ["陈阿嫲", "许砚辰"])
        frozen = _freeze_explicit_story_ir(manifest)
        self.assertEqual("陈阿嫲", frozen[0][0]["speaker"])
        self.assertTrue(frozen[0][0]["speaker_locked"])

    def test_inline_colon_dialogue_is_frozen_without_shot_metadata(self):
        from src.autogen_pipeline import _event_manifest, _freeze_explicit_story_ir

        text = "\n".join([
            "镜头1：阿福从巷口飞跑而来。阿福：阿嫲！村里又来了歹人！【音效】拖鞋拍地声。景别：全景 · 固定。",
            "镜头2：陈阿嫲拿起扫帚。陈阿嫲（普通话）：紧出来！紧出来！【环境音】脚步声。景别：中景。",
        ])
        manifest, _, error = _event_manifest(text, 1, None, [], ["陈阿嫲", "阿福"])
        frozen = _freeze_explicit_story_ir(manifest)

        self.assertIsNone(error)
        self.assertEqual("阿福", frozen[0][0]["speaker"])
        self.assertEqual("阿嫲！村里又来了歹人！", frozen[0][0]["content"])
        self.assertEqual("陈阿嫲", frozen[0][1]["speaker"])
        self.assertEqual("紧出来！紧出来！", frozen[0][1]["content"])

    def test_silent_visual_content_excludes_source_only_annotations(self):
        from src.autogen_pipeline import _event_manifest, _freeze_explicit_story_ir

        text = "\n".join([
            "镜头1：阿福从门后探头进来。台词：—。景别：中景 · 固定。",
            "镜头2：许砚辰点香手掌摇灭火苗。【环境音】烛火噼啪。景别：特写 · 固定。",
        ])
        manifest, _, error = _event_manifest(text, 1, None, [], ["阿福", "许砚辰"])
        frozen = _freeze_explicit_story_ir(manifest)

        self.assertIsNone(error)
        self.assertEqual("阿福从门后探头进来。", frozen[0][0]["content"])
        self.assertEqual("许砚辰点香手掌摇灭火苗。【环境音】烛火噼啪。", frozen[0][1]["content"])
        self.assertIn("景别：中景", frozen[0][0]["intent"])

    def test_batch_validation_defers_runtime_shot_defaults_to_compiler(self):
        from src.autogen_pipeline import _director_batch_errors

        story_ir = [{
            "event_id": "A01E0001", "speaker": "阿福", "speaker_locked": True,
            "content": "好耶！",
        }]
        candidate = {
            "scene information": {"who": ["阿福"], "what": "test"},
            "scene": [{"event_id": "A01E0001", "speaker": "阿福", "content": "好耶！"}],
        }

        self.assertEqual([], _director_batch_errors(candidate, story_ir, {"阿福"}, 1))

    def test_batch_validation_rejects_duplicate_mover_before_contract_repair(self):
        from src.autogen_pipeline import _director_batch_errors

        story_ir = [{
            "event_id": "A01E0001", "kind": "move", "move_characters": ["阿福"],
            "speaker": "阿福", "speaker_locked": True, "content": "走。",
        }]
        candidate = {
            "scene information": {"who": ["阿福"], "what": "test"},
            "scene": [{
                "event_id": "A01E0001", "speaker": "阿福", "content": "走。",
                "move": [
                    {"character": "阿福", "destination": "Position 2"},
                    {"character": "阿福", "destination": "Position 3"},
                ],
            }],
        }

        errors = _director_batch_errors(candidate, story_ir, {"阿福"}, 1)
        self.assertIn("A01E0001 move 中同一角色只能出现一次", errors)

    def test_frozen_move_is_restored_when_director_only_outputs_stand_up(self):
        from src.autogen_pipeline import _director_batch_errors, _restore_frozen_event_fields

        story_ir = [
            {"event_id": "A01E0001", "kind": "action", "speaker": "", "content": ""},
            {
                "event_id": "A01E0002", "kind": "move", "move_characters": ["林阿公"],
                "speaker": "", "speaker_locked": False, "content": "",
            },
        ]
        candidate = {
            "scene information": {"who": ["林阿公"], "what": "test"},
            "scene": [
                {"event_id": "A01E0001", "speaker": "", "content": "", "actions": []},
                {
                    "event_id": "A01E0002", "speaker": "", "content": "",
                    "actions": [{"character": "林阿公", "action": "Stand Up"}],
                },
            ],
        }

        restored = _restore_frozen_event_fields(candidate, story_ir)
        self.assertEqual([{"character": "林阿公", "destination": ""}], restored["scene"][1]["move"])
        self.assertEqual("Stand Up", restored["scene"][0]["actions"][0]["action"])
        self.assertEqual([], restored["scene"][1]["actions"])
        self.assertEqual([], _director_batch_errors(restored, story_ir, {"林阿公"}, 1))

    def test_frozen_story_fields_are_restored_by_event_id(self):
        from src.autogen_pipeline import _restore_frozen_event_fields

        story_ir = [{
            "event_id": "A01E0001", "speaker": "阿福", "speaker_locked": True,
            "content": "原台词",
        }]
        candidate = {"scene": [{
            "event_id": "A01E0001", "speaker": "别人", "content": "改写台词",
            "actions": [{"action": "keep"}],
        }]}

        restored = _restore_frozen_event_fields(candidate, story_ir)
        self.assertEqual("阿福", restored["scene"][0]["speaker"])
        self.assertEqual("原台词", restored["scene"][0]["content"])
        self.assertEqual([{"action": "keep"}], restored["scene"][0]["actions"])

    def test_free_creation_has_a_finite_default_event_manifest(self):
        from src.autogen_pipeline import _DIRECTOR_DEFAULT_EVENTS_PER_ACT, _event_manifest

        manifest, explicit, error = _event_manifest("一个自由创作构想", 2, None, [], [])
        self.assertIsNone(error)
        self.assertFalse(explicit)
        self.assertEqual(
            _DIRECTOR_DEFAULT_EVENTS_PER_ACT * 2,
            sum(len(events) for events in manifest),
        )

    def test_duration_dialogue_target_allocates_bounded_story_slots(self):
        from src.autogen_pipeline import _event_batches, _event_manifest

        manifest, explicit, error = _event_manifest("自由创作", 1, 10, [], [])
        self.assertIsNone(error)
        self.assertFalse(explicit)
        self.assertEqual(14, len(manifest[0]))
        self.assertEqual(10, sum(event.get("required_kind") == "dialogue" for event in manifest[0]))
        self.assertTrue(all(len(batch) <= 8 for batch in _event_batches(manifest[0])))

    def test_director_rejects_unassigned_whole_act_events(self):
        from src.autogen_pipeline import _director_batch_errors

        story_ir = [
            {"event_id": f"A01E{index:04d}", "speaker": "", "content": f"画面{index}"}
            for index in range(1, 9)
        ]
        candidate = {
            "scene information": {"who": ["A"], "what": "test"},
            "initial position": [{"character": "A", "position": "Position 1", "state": "standing"}],
            "scene": [
                {
                    "event_id": f"A01E{index:04d}", "speaker": "", "content": f"画面{index}",
                    "shot": "scene", "shot_blend": "Cut", "camera": 1, "duration": 5, "actions": [],
                }
                for index in range(1, 28)
            ],
        }
        errors = _director_batch_errors(candidate, story_ir, {"A"}, 1)
        self.assertTrue(any("单批事件数" in error for error in errors))
        self.assertTrue(any("event_id" in error for error in errors))

    def test_review_patch_changes_only_target_content(self):
        from src.autogen_pipeline import _apply_review_changes

        script = [{"scene": [
            {"speaker": "A", "content": "固定", "actions": []},
            {"speaker": "B", "content": "旧台词", "actions": [{"action": "Keep"}]},
        ]}]
        revised, count = _apply_review_changes(script, [
            {"act_index": 0, "event_index": 0, "content": "不能改"},
            {"act_index": 0, "event_index": 1, "content": "新台词", "actions": []},
        ], [{"speaker": "A", "content": "固定"}])

        self.assertEqual(1, count)
        self.assertEqual("固定", revised[0]["scene"][0]["content"])
        self.assertEqual("新台词", revised[0]["scene"][1]["content"])
        self.assertEqual([{"action": "Keep"}], revised[0]["scene"][1]["actions"])

    def test_frozen_move_fields_keep_speech_only_for_walk_and_talk(self):
        from src.autogen_pipeline import _restore_frozen_event_fields

        pure_move = _restore_frozen_event_fields(
            {"scene": [{"event_id": "A01E0001", "speaker": "", "content": ""}]},
            [{"event_id": "A01E0001", "kind": "move", "speaker": "", "content": ""}],
        )["scene"][0]
        walk_and_talk = _restore_frozen_event_fields(
            {"scene": [{"event_id": "A01E0002", "move": [{"character": "阿福"}]}]},
            [{
                "event_id": "A01E0002", "kind": "move", "speaker": "阿福",
                "speaker_locked": True, "content": "阿嫒！",
            }],
        )["scene"][0]

        self.assertNotIn("speaker", pure_move)
        self.assertNotIn("content", pure_move)
        self.assertEqual("阿福", walk_and_talk["speaker"])
        self.assertEqual("阿嫒！", walk_and_talk["content"])

    def test_contract_repair_payload_contains_only_affected_event(self):
        from src.autogen_pipeline import _contract_repair_payload

        script = [{
            "scene information": {"who": ["A"]},
            "initial position": [{"character": "A", "position": "Position 1"}],
            "scene": [{"content": "one"}, {"content": "two"}, {"content": "three"}],
        }]
        payload = _contract_repair_payload(script, [{
            "path": "$[0].scene[1].actions[0].action",
            "code": "RESOURCE_VALUE",
            "candidates": ["Standing Normal"],
        }])

        self.assertEqual(1, len(payload["event_targets"]))
        self.assertEqual("two", payload["event_targets"][0]["event"]["content"])
        self.assertNotIn("three", str(payload))

    def test_contract_repair_is_split_into_bounded_batches(self):
        from src.autogen_pipeline import _contract_repair_batches

        payload = {
            "errors": [
                {"path": f"$[0].scene[{index}].content", "code": "REQUIRED"}
                for index in range(7)
            ],
            "event_targets": [
                {"act_index": 0, "event_index": index, "event": {}}
                for index in range(7)
            ],
            "act_targets": [],
        }
        batches = _contract_repair_batches(payload)
        self.assertEqual([6, 1], [len(batch["event_targets"]) for batch in batches])


class DirectorBatchedIRAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_storyboard_uses_semantic_move_classification(self):
        from src.autogen_pipeline import _build_story_ir, _event_manifest

        text = "镜头3：阿福从巷口飞跑而来。阿福：阿嫒！村里又来了歹人！景别：全景。"
        manifest, explicit, error = _event_manifest(text, 1, None, [], ["阿福"])
        classified = {"events": [{
            "event_id": "A01E0001", "kind": "move", "speaker": "阿福",
            "move_characters": ["阿福"],
            "content": "阿嫒！村里又来了歹人！",
            "intent": "阿福从巷口飞跑而来并喊话", "shot_intent": "全景固定",
        }]}
        run_stage = AsyncMock(return_value=classified)

        with patch("src.autogen_pipeline.create_story_ir_agent", return_value=object()), \
             patch("src.autogen_pipeline._run_stage_agent_json_object", run_stage):
            result = await _build_story_ir(
                SimpleNamespace(put_event=lambda event: None), manifest, explicit, text, {}, {},
            )

        self.assertIsNone(error)
        self.assertEqual("move", result[0][0]["kind"])
        self.assertEqual(["阿福"], result[0][0]["move_characters"])
        self.assertEqual("阿福", result[0][0]["speaker"])
        self.assertTrue(result[0][0]["speaker_locked"])
        self.assertIn('"input_mode":"explicit_storyboard"', run_stage.await_args.args[1])

    async def test_long_single_act_is_generated_as_multiple_requests(self):
        from src.autogen_pipeline import _freeze_explicit_story_ir, _run_director_batched_draft

        prompts = []

        async def run_batch(_director, prompt, _bridge, _label):
            prompts.append(prompt)
            frozen = __import__("json").loads(prompt.split("story_ir=", 1)[1].split("\n", 1)[0])
            return [{
                "position_descriptions": {"Position 1": "left", "Position 2": "right"},
                "scene information": {"who": ["A", "B"], "what": "test"},
                "initial position": [
                    {"character": "A", "position": "Position 1", "state": "standing"},
                    {"character": "B", "position": "Position 2", "state": "standing"},
                ],
                "scene": [
                    {
                        "event_id": event["event_id"],
                        "speaker": event["speaker"],
                        "content": event["content"],
                        "shot": "scene",
                        "shot_blend": "Cut",
                        "camera": 1,
                        "duration": 5,
                        "actions": [],
                    }
                    for event in frozen
                ],
            }]

        run = AsyncMock(side_effect=run_batch)
        creative_brief = "\n".join(f"镜头{i}：画面{i}" for i in range(1, 18))
        frozen_ir = _freeze_explicit_story_ir(
            __import__("src.autogen_pipeline", fromlist=["_event_manifest"])
            ._event_manifest(creative_brief, 1, None, [], [])[0]
        )
        with patch("src.autogen_pipeline.create_director_agent", return_value=object()), \
             patch("src.autogen_pipeline._run_director_agent", run), \
             patch("src.autogen_pipeline._build_story_ir", AsyncMock(return_value=frozen_ir)):
            result = await _run_director_batched_draft(
                bridge=SimpleNamespace(put_event=lambda event: None),
                characters=[],
                scene=SimpleNamespace(name="scene"),
                resource_loader=None,
                required_character_count=2,
                act_count=1,
                user_constraints=[],
                act_scene_map=None,
                script_style_guide=None,
                creative_brief=creative_brief,
                meeting_summary={},
                treatment={},
                target_dialogue_lines=None,
                fixed_dialogues=[],
            )

        self.assertEqual(3, run.await_count)
        self.assertEqual(17, len(result[0]["scene"]))
        parsed_batches = [
            __import__("json").loads(prompt.split("story_ir=", 1)[1].split("\n", 1)[0])
            for prompt in prompts
        ]
        self.assertEqual([8, 8, 1], [len(batch) for batch in parsed_batches])
        self.assertIn("画面8", prompts[0])
        self.assertNotIn("画面9", prompts[0])

    async def test_director_repairs_only_error_event_plus_two_neighbors(self):
        from src.autogen_pipeline import _run_director_batched_draft

        prompts = []
        story_ir = [{
            "event_id": f"A01E{index:04d}", "kind": "action", "speaker": "", "content": f"画面{index}",
        } for index in range(1, 9)]

        async def run_batch(_director, prompt, _bridge, _label):
            prompts.append(prompt)
            events = story_ir if len(prompts) == 1 else story_ir[:5]
            return [{
                "scene information": {"who": ["A", "B"], "what": "test"},
                "initial position": [],
                "scene": [{
                    "event_id": event["event_id"],
                    "speaker": "",
                    "content": event["content"],
                    "shot": "invalid" if len(prompts) == 1 and index == 2 else "scene",
                    "marker": event["event_id"],
                } for index, event in enumerate(events)],
            }]

        with patch("src.autogen_pipeline.create_director_agent", return_value=object()), \
             patch("src.autogen_pipeline._run_director_agent", AsyncMock(side_effect=run_batch)), \
             patch("src.autogen_pipeline._build_story_ir", AsyncMock(return_value=[story_ir])):
            result = await _run_director_batched_draft(
                bridge=SimpleNamespace(put_event=lambda event: None),
                characters=[], scene=SimpleNamespace(name="scene"), resource_loader=None,
                required_character_count=2, act_count=1, user_constraints=[], act_scene_map=None,
                script_style_guide=None, creative_brief="test", meeting_summary={}, treatment={},
                target_dialogue_lines=None, fixed_dialogues=[],
            )

        self.assertEqual(2, len(prompts))
        self.assertIn("repair_story_ir=", prompts[1])
        self.assertIn("current_events=", prompts[1])
        self.assertIn("A01E0005", prompts[1])
        self.assertNotIn("A01E0006", prompts[1])
        self.assertEqual([f"A01E{index:04d}" for index in range(1, 9)], [
            event["marker"] for event in result[0]["scene"]
        ])


if __name__ == "__main__":
    unittest.main()
