import unittest
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from src.autogen_pipeline import (
    _canonicalize_character_references,
    _direct_batch_matches_source,
    _direct_batch_rows,
    _fit_direct_act_count,
    _normalize_direct_scene,
    _parse_plaintext_script,
    _render_plaintext_screenplay,
)


class DirectModeTests(unittest.TestCase):
    def test_known_character_short_name_is_canonicalized(self):
        scene = {
            "scene information": {"who": ["艾莉 (F-01)"], "what": "测试"},
            "initial position": [{"character": "艾莉", "position": "Position 1"}],
            "scene": [{
                "speaker": "艾莉",
                "content": "继续追查。",
                "actions": [{"character": "艾莉", "action": "Standing Normal"}],
                "current position": [{"character": "艾莉", "position": "Position 1"}],
            }],
        }

        normalized = _normalize_direct_scene(
            scene,
            fallback_names=["艾莉 (F-01)"],
            scene_name="Space Station",
            what_snippet="测试",
        )

        self.assertEqual(["艾莉 (F-01)"], normalized["scene information"]["who"])
        self.assertEqual("艾莉 (F-01)", normalized["initial position"][0]["character"])
        self.assertEqual("艾莉 (F-01)", normalized["scene"][0]["speaker"])
        self.assertEqual("艾莉 (F-01)", normalized["scene"][0]["actions"][0]["character"])
        self.assertEqual("继续追查。", normalized["scene"][0]["content"])

    def test_story_ir_move_characters_are_canonicalized(self):
        story_ir = [{"speaker": "艾莉", "move_characters": ["艾莉", "艾莉"]}]

        _canonicalize_character_references(story_ir, ["艾莉 (F-01)"])

        self.assertEqual("艾莉 (F-01)", story_ir[0]["speaker"])
        self.assertEqual(["艾莉 (F-01)"], story_ir[0]["move_characters"])

    def test_long_explicit_rows_are_batchable_without_rewriting_dialogue(self):
        text = "\n".join(f"角色：第{i}句。" for i in range(1, 18))
        acts = _direct_batch_rows(text, 1)
        self.assertEqual(17, len(acts[0]))
        records = [{"source_event_id": "A01E0001", "raw": acts[0][0]}]
        events = [{"source_event_id": "A01E0001", "speaker": "角色", "content": "第1句。"}]
        self.assertTrue(_direct_batch_matches_source(events, records))
        events[0]["content"] = "被改写"
        self.assertFalse(_direct_batch_matches_source(events, records))

    def test_free_form_multiline_text_is_not_split_blindly(self):
        text = "\n".join(["镜头说明", "这一段没有明确的逐行边界"] * 8)
        self.assertIsNone(_direct_batch_rows(text, 1))

    def test_local_direct_fallback_preserves_requested_act_count(self):
        scenes = _parse_plaintext_script("\n".join(f"旁白行{i}" for i in range(9)))
        fitted = _fit_direct_act_count(scenes, 3)
        self.assertEqual(3, len(fitted))
        self.assertEqual(9, sum(len(act["scene"]) for act in fitted))

    def test_plaintext_screenplay_format_keeps_dialogue_action_and_empty_shot(self):
        text = _render_plaintext_screenplay([{
            "scene information": {"where": "月台", "who": ["阿海"], "what": "深夜候车"},
            "scene": [
                {"speaker": "阿海", "content": "你欲去佗位？", "shot_description": "缓慢推近", "actions": [{"motion_detail": "抬头"}]},
                {"speaker": "", "content": "", "duration": "4s", "shot_description": "末班车驶入"},
            ],
        }])

        self.assertIn("第1幕｜月台", text)
        self.assertIn("【动作】抬头", text)
        self.assertIn("【镜头】缓慢推近", text)
        self.assertIn("阿海：你欲去佗位？", text)
        self.assertIn("【空镜】末班车驶入（4s）", text)

    def test_plaintext_direct_scene_gets_required_runtime_fields(self):
        scenes = _parse_plaintext_script("陈屿：醒醒。\n林静：警报还在响。")
        normalized = _normalize_direct_scene(
            scenes[0],
            fallback_names=[],
            scene_name="太空站",
            what_snippet="测试直接模式",
        )

        self.assertEqual(["陈屿", "林静"], normalized["scene information"]["who"])
        self.assertEqual("太空站", normalized["scene information"]["where"])
        self.assertEqual(2, len(normalized["initial position"]))
        self.assertTrue(all(item["state"] == "standing" for item in normalized["initial position"]))
        self.assertIn("Position 1", normalized["position_descriptions"])
        self.assertIn("Position 2", normalized["position_descriptions"])

        for beat in normalized["scene"]:
            self.assertEqual("character", beat["shot"])
            self.assertEqual("Cut", beat["shot_blend"])
            self.assertEqual("中景", beat["shot_type"])
            self.assertEqual(0, beat["Follow"])
            self.assertEqual("", beat["shot_description"])
            self.assertTrue(beat["current position"])

    def test_intermediate_event_id_is_removed_before_runtime_contract(self):
        normalized = _normalize_direct_scene(
            {"scene": [{"event_id": "A01E0001", "speaker": "陈屿", "content": "收到。"}]},
            fallback_names=["陈屿"],
            scene_name="太空站",
            what_snippet="测试 IR ID",
        )
        self.assertNotIn("event_id", normalized["scene"][0])

    def test_json_direct_move_keeps_scene_shot_and_camera(self):
        normalized = _normalize_direct_scene(
            {
                "scene": [
                    {
                        "move": {"character": "陈屿", "destination": "Position 2"},
                    },
                    {
                        "speaker": "陈屿",
                        "content": "我到了。",
                    },
                ],
            },
            fallback_names=["陈屿"],
            scene_name="太空站",
            what_snippet="测试移动",
        )

        move_beat = normalized["scene"][0]
        dialogue_beat = normalized["scene"][1]
        self.assertEqual("scene", move_beat["shot"])
        self.assertEqual(1, move_beat["camera"])
        self.assertEqual([{"character": "陈屿", "destination": "Position 2"}], move_beat["move"])
        self.assertEqual("Position 1", move_beat["current position"][0]["position"])
        self.assertEqual("Position 2", dialogue_beat["current position"][0]["position"])

    def test_walk_and_talk_direct_beat_is_scene_shot(self):
        normalized = _normalize_direct_scene(
            {
                "scene": [
                    {
                        "speaker": "陈屿",
                        "content": "边走边说。",
                        "move": {"character": "陈屿", "destination": "Position 2"},
                    },
                ],
            },
            fallback_names=["陈屿"],
            scene_name="太空站",
            what_snippet="测试边走边说",
        )

        beat = normalized["scene"][0]
        self.assertEqual("scene", beat["shot"])
        self.assertEqual(1, beat["camera"])
        self.assertNotIn("shot_type", beat)
        self.assertNotIn("Follow", beat)

    def test_explicit_object_shot_keeps_target_and_anchor(self):
        normalized = _normalize_direct_scene(
            {"scene": [{
                "speaker": "", "content": "香炉里的香灰轻轻震动。", "duration": 3,
                "shot": "object", "target": "香炉 (2)", "shot_type": "物体特写", "actions": [],
            }]},
            fallback_names=[], scene_name="MinNan", what_snippet="测试物体镜头",
        )

        beat = normalized["scene"][0]
        self.assertEqual("object", beat["shot"])
        self.assertEqual("香炉 (2)", beat["target"])
        self.assertEqual("center", beat["target_anchor"])
        self.assertNotIn("camera", beat)

    def test_move_timeline_repairs_collisions_and_removes_actions(self):
        normalized = _normalize_direct_scene(
            {
                "scene information": {"who": ["陈阿嫲", "林阿公", "阿福", "许砚辰"]},
                "initial position": [
                    {"character": "陈阿嫲", "position": "Position 1"},
                    {"character": "林阿公", "position": "Position 2"},
                    {"character": "阿福", "position": "Position 3"},
                    {"character": "许砚辰", "position": "Position 4"},
                ],
                "scene": [
                    {"move": {"character": "陈阿嫲", "destination": "Position 2"}},
                    {
                        "speaker": "阿福", "content": "阿嫲！村里又来了歹人！",
                        "actions": [{"character": "阿福", "action": "Run 1"}],
                        "move": {"character": "阿福", "destination": "Position 3"},
                    },
                    {
                        "speaker": "陈阿嫲", "content": "紧出来！",
                        "actions": [{"character": "陈阿嫲", "action": "Run 1"}],
                        "move": {"character": "陈阿嫲", "destination": "Position 4"},
                    },
                ],
            },
            fallback_names=[],
            scene_name="MinNan",
            what_snippet="测试移动冲突",
        )

        beats = normalized["scene"]
        self.assertEqual(["Position 5", "Position 6", "Position 7"], [
            beat["move"][0]["destination"] for beat in beats
        ])
        self.assertEqual("Position 5", beats[1]["current position"][0]["position"])
        self.assertEqual("Position 6", beats[2]["current position"][2]["position"])
        for beat in beats:
            self.assertNotIn("actions", beat)
            self.assertEqual("scene", beat["shot"])
        self.assertEqual("阿嫲！村里又来了歹人！", beats[1]["content"])

    def test_collapsed_initial_positions_are_spread_by_character(self):
        normalized = _normalize_direct_scene(
            {
                "scene information": {"who": ["陈屿", "林静", "老赵"]},
                "initial position": [
                    {"character": "陈屿", "position": "Position 1"},
                    {"character": "林静", "position": "Position 1"},
                    {"character": "老赵", "position": "Position 1"},
                ],
                "scene": [
                    {"speaker": "陈屿", "content": "都别挤在一起。"},
                    {"speaker": "林静", "content": "站位重新分开。"},
                ],
            },
            fallback_names=[],
            scene_name="太空站",
            what_snippet="测试塌缩站位",
        )

        self.assertTrue(normalized.pop("_direct_position_repair_applied"))
        self.assertEqual(
            [
                {"character": "陈屿", "position": "Position 1", "state": "standing"},
                {"character": "林静", "position": "Position 2", "state": "standing"},
                {"character": "老赵", "position": "Position 3", "state": "standing"},
            ],
            normalized["initial position"],
        )
        for beat in normalized["scene"]:
            self.assertEqual(
                ["Position 1", "Position 2", "Position 3"],
                [entry["position"] for entry in beat["current position"]],
            )

    def test_collapsed_current_positions_are_spread_by_character(self):
        normalized = _normalize_direct_scene(
            {
                "scene information": {"who": ["陈屿", "林静"]},
                "initial position": [
                    {"character": "陈屿", "position": "Position 1"},
                    {"character": "林静", "position": "Position 2"},
                ],
                "scene": [
                    {
                        "speaker": "陈屿",
                        "content": "当前位置塌了。",
                        "current position": [
                            {"character": "陈屿", "position": "Position 1"},
                            {"character": "林静", "position": "Position 1"},
                        ],
                    }
                ],
            },
            fallback_names=[],
            scene_name="太空站",
            what_snippet="测试 current position 塌缩",
        )

        self.assertTrue(normalized.pop("_direct_position_repair_applied"))
        self.assertEqual(
            [
                {"character": "陈屿", "position": "Position 1"},
                {"character": "林静", "position": "Position 2"},
            ],
            normalized["scene"][0]["current position"],
        )

    def test_initial_state_is_preserved_or_inferred_from_first_action(self):
        normalized = _normalize_direct_scene(
            {
                "scene information": {"who": ["尚博勒", "贤者"]},
                "initial position": [
                    {"character": "尚博勒", "position": "Position 15", "state": "sitting"},
                    {"character": "贤者", "position": "Position 16"},
                ],
                "scene": [
                    {
                        "speaker": "贤者",
                        "content": "躲什么？",
                        "actions": [
                            {"character": "贤者", "state": "standing", "action": "Sit Down"}
                        ],
                    }
                ],
            },
            fallback_names=[],
            scene_name="LotusTown",
            what_snippet="测试初始姿态",
        )

        self.assertEqual(
            [
                {"character": "尚博勒", "position": "Position 15", "state": "sitting"},
                {"character": "贤者", "position": "Position 16", "state": "standing"},
            ],
            normalized["initial position"],
        )


if __name__ == "__main__":
    unittest.main()
