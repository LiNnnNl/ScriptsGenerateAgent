import asyncio
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from src.resource_loader import ResourceLoader
from src.script_contract import MANDARIN, normalize_camera_resources, normalize_script, validate_script, validate_bundle


def sample():
    return [{'scene information': {'who': ['A'], 'where': 'Auditorium', 'what': 'A等候朋友',
                'emotionLibrary': ''},
             'initial position': [{'character': 'A', 'position': 'Position 1', 'state': 'standing'}],
             'scene': [{'event_index': 0, 'speaker': 'A', 'content': '你来了。', 'actions': [],
                 'current position': [{'character': 'A', 'position': 'Position 1'}],
                 'emotion': '', 'confidence': 0.0, 'reason': '无情绪库', 'shot_description': 'A stands in the foreground.'}]}]


class ScriptContractTests(unittest.TestCase):
    def setUp(self):
        self.loader = ResourceLoader()

    def test_missing_fields_and_wrong_types_are_errors(self):
        for field in list(sample()[0]['scene'][0]):
            if field in ('speaker', 'content', 'actions', 'event_index', 'current position', 'emotion', 'confidence', 'reason', 'shot_description'):
                data = sample()
                del data[0]['scene'][0][field]
                self.assertFalse(validate_script(data, self.loader)['valid'], field)
        for field, value in [('confidence', True), ('confidence', float('nan')), ('event_index', True),
                             ('actions', None), ('move', {}), ('unexpected', 1), ('content', 1)]:
            data = sample()
            data[0]['scene'][0][field] = value
            self.assertFalse(validate_script(data, self.loader)['valid'], (field, value))

    def test_empty_catalogs_clear_names_and_warn(self):
        empty_root = tempfile.TemporaryDirectory()
        self.addCleanup(empty_root.cleanup)
        self.loader.resource_dir = Path(empty_root.name)
        data = sample()
        data[0]['scene information']['emotionLibrary'] = 'invented'
        data[0]['scene'][0]['emotion'] = [{'character': 'A', 'emotion': 'fake:1', 'emotionStyle': 'fake'}]
        data[0]['scene'][0]['then-interact'] = {'target': 'FakeDoor', 'action': 'Open', 'delay': 1}
        normalized, warnings = normalize_script(data, self.loader)
        self.assertEqual('', normalized[0]['scene information']['emotionLibrary'])
        self.assertEqual('', normalized[0]['scene'][0]['emotion'][0]['emotion'])
        self.assertEqual('fake:1', data[0]['scene'][0]['emotion'][0]['emotion'])
        self.assertTrue(warnings)
        self.assertTrue(validate_script(normalized, self.loader)['valid'])

    def test_resource_candidates_and_positions(self):
        data = sample()
        data[0]['scene'][0]['actions'] = [{'character': 'A', 'state': 'standing', 'action': 'Invented'}]
        result = validate_script(data, self.loader)
        self.assertFalse(result['valid'])
        self.assertTrue(any(e.get('candidates') for e in result['errors']))
        data = sample()
        data[0]['scene'][0]['current position'][0]['position'] = 'Position 2'
        self.assertFalse(validate_script(data, self.loader)['valid'])

    def test_real_catalog_emotion_language_and_interaction_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'emotion_libraries.json').write_text(json.dumps({'libraries': {'test': {
                'emotions': ['normal', 'sad', 'happy'], 'styles': ['build_to_second']}}}), encoding='utf-8')
            (root / 'interactions').mkdir()
            (root / 'interactions' / 'Auditorium.json').write_text(json.dumps({'scene': 'Auditorium', 'objects': [
                {'id': 'timer', 'actions': [{'name': 'set', 'parameters': {'seconds': {'type': 'number', 'required': True, 'min': 0}}}]}]}), encoding='utf-8')
            self.loader.resource_dir = root
            data = sample()
            data[0]['scene information']['emotionLibrary'] = 'test'
            data[0]['scene information']['language_config'] = copy.deepcopy(MANDARIN)
            event = data[0]['scene'][0]
            event['emotion'] = [{'character': 'A', 'emotion': 'sad:0.3,happy:0.7', 'emotionStyle': 'build_to_second'}]
            event['then-interact'] = {'target': 'timer', 'action': 'set', 'seconds': 3}
            event['language_track'] = 'zh_hans_cmn'
            event['content_variants'] = {'zh_hans_cmn': '你来了。'}
            self.assertTrue(validate_script(data, self.loader)['valid'])
            event['emotion'][0]['emotion'] = 'sad:0.2,happy:0.7'
            self.assertFalse(validate_script(data, self.loader)['valid'])
            event['emotion'][0]['emotion'] = 'sad:0.3,happy:0.7'
            event['then-interact']['seconds'] = True
            self.assertFalse(validate_script(data, self.loader)['valid'])
            event['then-interact']['seconds'] = 1
            event['content_variants']['zh_hans_cmn'] = '其他文字'
            self.assertFalse(validate_script(data, self.loader)['valid'])

    def test_multilingual_extension_is_optional_but_strict_when_enabled(self):
        data = sample()
        self.assertTrue(validate_script(data, self.loader)['valid'])
        data[0]['scene information']['language_config'] = copy.deepcopy(MANDARIN)
        self.assertFalse(validate_script(data, self.loader)['valid'])
        normalized, _ = normalize_script(data, self.loader)
        self.assertTrue(validate_script(normalized, self.loader)['valid'])
        event = normalized[0]['scene'][0]
        event['content_variants']['extra'] = 'Extra'
        self.assertFalse(validate_script(normalized, self.loader)['valid'])
        event['content_variants'].pop('extra')
        normalized.append(copy.deepcopy(normalized[0]))
        normalized[1]['scene information']['language_config']['tracks'][0]['label'] = '不同'
        self.assertFalse(validate_script(normalized, self.loader)['valid'])
        bad = copy.deepcopy(MANDARIN)
        bad['tracks'][0]['id'] = '中文'
        data = sample()
        data[0]['scene information']['language_config'] = bad
        self.assertFalse(validate_script(data, self.loader)['valid'])
        bad = copy.deepcopy(MANDARIN)
        bad['tracks'][0]['tts_prompt_suffix'] = 'https://example.com'
        data[0]['scene information']['language_config'] = bad
        self.assertFalse(validate_script(data, self.loader)['valid'])

    def test_real_pixar_emotion_registry(self):
        from src.script_contract import read_catalogs
        library = read_catalogs(self.loader)['emotions']['pixar_cartoon']
        self.assertEqual(78, len(library['emotions']))
        self.assertEqual(8, len(library['styles']))
        self.assertIn('happy_soft', library['emotions'])
        self.assertIn('quick_reaction', library['styles'])
        data = sample()
        for field in ('who',):
            data[0]['scene information'][field] = ['阿福']
        data[0]['initial position'][0]['character'] = '阿福'
        data[0]['scene'][0]['speaker'] = '阿福'
        data[0]['scene'][0]['current position'][0]['character'] = '阿福'
        data[0]['scene information']['emotionLibrary'] = 'pixar_cartoon'
        data[0]['scene'][0]['emotion'] = [{'character': '阿福', 'emotion': 'sad_soft:0.25,crying_strong:0.75',
                                          'emotionStyle': 'conflict'}]
        self.assertTrue(validate_script(data, self.loader)['valid'])
        data = sample()
        data[0]['scene information']['emotionLibrary'] = 'pixar_cartoon'
        data[0]['scene'][0]['emotion'] = 'happy_soft'
        result = validate_script(data, self.loader)
        self.assertFalse(result['valid'])
        self.assertTrue(any(e['code'] == 'EMOTION_CHARACTER' for e in result['errors']))
    def test_silent_numeric_duration_and_action_state(self):
        data = sample()
        data[0]['scene'][0].update(speaker='', content='无台词', duration='3s')
        normalized, _ = normalize_script(data, self.loader)
        self.assertEqual(3.0, normalized[0]['scene'][0]['duration'])
        self.assertEqual('无台词', normalized[0]['scene'][0]['content'])
        self.assertTrue(validate_script(normalized, self.loader)['valid'])
        data = sample()
        data[0]['scene'][0]['actions'] = [{'character': 'A', 'action': 'Sit Down'}]
        normalized, _ = normalize_script(data, self.loader)
        self.assertEqual('standing', normalized[0]['scene'][0]['actions'][0]['state'])
        self.assertTrue(validate_script(normalized, self.loader)['valid'])

    def test_model_repair_receives_errors_and_cannot_change_dialogue(self):
        from src.autogen_pipeline import _enforce_contract
        class Bridge:
            def put_event(self, event):
                pass
        data = sample()
        data[0]['scene'][0]['actions'] = [{'character': 'A', 'state': 'standing', 'action': 'Invented'}]
        async def repair(agent, prompt):
            self.assertIn('candidates', prompt)
            event = copy.deepcopy(data[0]['scene'][0])
            event['content'] = 'changed'
            return {'event_repairs': [{'act_index': 0, 'event_index': 0, 'event': event}]}
        with patch('src.autogen_pipeline._run_stage_agent_json_object', repair):
            with self.assertRaisesRegex(ValueError, '对白'):
                asyncio.run(_enforce_contract(data, self.loader, Bridge(), {0: self.loader.get_scene_by_id('Auditorium')}, 1, ['A'], 1))

    def test_normal_mode_repair_can_fill_only_invalid_empty_dialogue(self):
        from src.autogen_pipeline import _enforce_contract
        class Bridge:
            def put_event(self, event):
                pass
        data = sample()
        data[0]['scene'][0]['content'] = ''
        async def repair(agent, prompt):
            self.assertIn('只有当 errors 明确指出空台词时，才可补写短句', prompt)
            event = copy.deepcopy(data[0]['scene'][0])
            event['content'] = '补全台词。'
            return {'event_repairs': [{'act_index': 0, 'event_index': 0, 'event': event}]}
        with patch('src.autogen_pipeline._run_stage_agent_json_object', repair):
            fixed, report = asyncio.run(_enforce_contract(
                data, self.loader, Bridge(), {0: self.loader.get_scene_by_id('Auditorium')},
                1, ['A'], 1, preserve_story=False))
        self.assertTrue(report['valid'])
        self.assertEqual('补全台词。', fixed[0]['scene'][0]['content'])

    def test_contract_rebuilds_partial_position_collisions_without_model_repair(self):
        from src.autogen_pipeline import _enforce_contract
        class Bridge:
            def put_event(self, event):
                pass
        data = sample()
        data[0]['scene information']['who'] = ['A', 'B']
        data[0]['initial position'].append({'character': 'B', 'position': 'Position 1', 'state': 'standing'})
        data[0]['scene'][0]['current position'].append({'character': 'B', 'position': 'Position 1'})
        fixed, report = asyncio.run(_enforce_contract(
            data, self.loader, Bridge(), {0: self.loader.get_scene_by_id('Auditorium')},
            1, ['A', 'B'], 2,
        ))
        self.assertTrue(report['valid'])
        self.assertEqual(['Position 1', 'Position 2'], [
            item['position'] for item in fixed[0]['initial position']
        ])
        self.assertEqual(['Position 1', 'Position 2'], [
            item['position'] for item in fixed[0]['scene'][0]['current position']
        ])

    def test_contract_inserts_stand_up_before_move_without_reordering(self):
        from src.autogen_pipeline import _apply_contract_repairs, _enforce_contract
        class Bridge:
            def put_event(self, event):
                pass

        data = sample()
        data[0]['initial position'][0]['state'] = 'squatting'
        move = copy.deepcopy(data[0]['scene'][0])
        move.pop('speaker')
        move.pop('content')
        move.pop('actions')
        move['move'] = [{'character': 'A', 'destination': 'Position 2'}]
        before = copy.deepcopy(data[0]['scene'][0])
        data[0]['scene'] = [before, move]

        fixed, report = asyncio.run(_enforce_contract(
            data, self.loader, Bridge(), {0: self.loader.get_scene_by_id('Auditorium')},
            1, ['A'], 1, preserve_story=False,
        ))

        self.assertTrue(report['valid'])
        self.assertEqual('Stand Up', fixed[0]['scene'][0]['actions'][0]['action'])
        self.assertEqual('squatting', fixed[0]['scene'][0]['actions'][0]['state'])
        self.assertEqual('Position 2', fixed[0]['scene'][1]['move'][0]['destination'])

        repaired = _apply_contract_repairs(data, {'event_repairs': [{
            'act_index': 0, 'event_index': 0,
            'event': {**move, 'move': [{
                'character': 'A', 'destination': 'Position 2', 'state': 'standing',
            }]},
        }]})
        self.assertNotIn('state', repaired[0]['scene'][0]['move'][0])

    def test_walk_and_talk_stands_mover_even_when_someone_else_speaks(self):
        from src.autogen_pipeline import _stabilize_generated_posture_timeline

        scene = {
            'initial position': [
                {'character': 'A', 'state': 'sitting'},
                {'character': 'B', 'state': 'standing'},
            ],
            'scene': [
                {'speaker': 'B', 'content': '先听我说。', 'actions': []},
                {'speaker': 'B', 'content': '边走边说。', 'move': [
                    {'character': 'A', 'destination': 'Position 2'},
                ]},
            ],
        }

        _stabilize_generated_posture_timeline(scene)

        self.assertEqual('Stand Up', scene['scene'][0]['actions'][0]['action'])
        self.assertEqual('A', scene['scene'][0]['actions'][0]['character'])
        self.assertEqual('边走边说。', scene['scene'][1]['content'])

    def test_bundle_camera_references_and_editor_gate(self):
        from src.cinematography import _build_camera_script
        from app import app
        data = sample()
        intermediate = copy.deepcopy(data)
        intermediate[0]['scene'][0].update(shot='character', shot_type='中景', shot_blend='cut', Follow=0)
        camera = _build_camera_script(intermediate, self.loader.camera_list)
        actor = {'name': 'A', 'age': None, 'gender': '未知',
                 'gameobject_name': self.loader.characters[0].gameobject_name,
                 'appearance': dict.fromkeys(('height', 'body_type', 'hair', 'face'), ''),
                 'acting_style': '', 'traits': [], 'background': ''}
        self.assertTrue(validate_bundle(data, camera, [actor], self.loader)['valid'])
        regions = self.loader.load_scene_info('Auditorium')['regions']
        own, other = regions[:2]
        target = (other.get('anchors', []) + other.get('scene_markers', []))[0]['name']
        detail = {'where': 'Auditorium', 'groups': [], 'singles': [
            {'position_id': 'Position 1', 'character': 'A', 'region': own['name'], 'lookat': target}]}
        self.assertTrue(validate_bundle(data, camera, [actor], self.loader, details=detail)['valid'])
        detail['singles'][0]['lookat'] = '不存在的锚点'
        self.assertFalse(validate_bundle(data, camera, [actor], self.loader, details=detail)['valid'])
        plan = {'where': 'Auditorium', 'groups': [{
            'group_id': 'G1', 'layout': 'two_person', 'region': own['name'],
            'positions': [
                {'position_id': 'Position 1', 'character': 'A'},
                {'position_id': 'Position 2', 'character': 'B'},
            ],
            'lookat': {'mode': 'target', 'target_object': target},
        }], 'singles': []}
        self.assertTrue(validate_bundle(data, camera, [actor], self.loader, plans=plan)['valid'])
        camera['scenes'][0]['events'][0]['target_position'] = 'Position 99'
        self.assertFalse(validate_bundle(data, camera, [actor], self.loader)['valid'])
        with app.test_client() as client:
            self.assertEqual(200, client.post('/api/validate_script', json={'script': data}).status_code)
            data[0]['scene'][0]['confidence'] = True
            self.assertEqual(422, client.post('/api/validate_script', json={'script': data}).status_code)

    def test_bundle_uses_scene_markers_as_object_targets(self):
        from src.cinematography import _build_camera_script

        data = sample()
        data[0]['scene information']['where'] = 'MinNan'
        intermediate = copy.deepcopy(data)
        intermediate[0]['scene'][0].update(
            shot='object', target='香炉 (2)', target_anchor='center',
            shot_type='物体特写', shot_blend='cut',
        )
        camera = _build_camera_script(intermediate, self.loader.camera_list)
        actor = {
            'name': 'A', 'age': None, 'gender': '未知',
            'gameobject_name': self.loader.characters[0].gameobject_name,
            'appearance': dict.fromkeys(('height', 'body_type', 'hair', 'face'), ''),
            'acting_style': '', 'traits': [], 'background': '',
        }

        self.assertTrue(validate_bundle(data, camera, [actor], self.loader)['valid'])
        self.assertNotIn('target_position', camera['scenes'][0]['events'][0])

        camera['scenes'][0]['events'][0]['target'] = '红龟稞'
        normalized = normalize_camera_resources(camera, data, self.loader)
        self.assertEqual('红龟稞 (3)', normalized['scenes'][0]['events'][0]['target'])
        self.assertTrue(validate_bundle(data, normalized, [actor], self.loader)['valid'])

    def test_unknown_object_camera_downgrades_to_scene(self):
        data = sample()
        camera = {'scenes': [{'shot_index': 0, 'events': [{
            'event_index': 0, 'shot': 'object', 'target': '', 'target_anchor': 'center',
            'shot_type': '物体中景', 'shot_blend': 'cut', 'follow': 0,
            'shot_description': data[0]['scene'][0]['shot_description'],
            'motion_enabled': True, 'motion_preset': 'slow_push',
            'play_motion_on_activate': True, 'motion_start_delay': 0.0,
            'motion_reset_on_replay': True,
        }]}]}

        event = normalize_camera_resources(camera, data, self.loader)['scenes'][0]['events'][0]

        self.assertEqual('scene', event['shot'])
        self.assertEqual(1, event['camera'])
        self.assertFalse(event['motion_enabled'])
        self.assertNotIn('target_anchor', event)

    def test_bundle_position_coverage_allows_later_character_to_reuse_slot(self):
        from src.cinematography import _build_camera_script

        data = sample()
        data[0]['scene information']['who'] = ['A', 'B']
        data[0]['initial position'].append({
            'character': 'B', 'position': 'Position 2', 'state': 'standing',
        })
        event = data[0]['scene'][0]
        event.pop('speaker')
        event.pop('content')
        event.pop('actions')
        event['current position'].append({'character': 'B', 'position': 'Position 2'})
        event['move'] = [
            {'character': 'A', 'destination': 'Position 2'},
            {'character': 'B', 'destination': 'Position 3'},
        ]
        intermediate = copy.deepcopy(data)
        intermediate[0]['scene'][0].update(shot='scene', camera=1, shot_blend='cut')
        camera = _build_camera_script(intermediate, self.loader.camera_list)
        gameobject = self.loader.characters[0].gameobject_name
        actors = [{
            'name': name, 'age': None, 'gender': '未知', 'gameobject_name': gameobject,
            'appearance': dict.fromkeys(('height', 'body_type', 'hair', 'face'), ''),
            'acting_style': '', 'traits': [], 'background': '',
        } for name in ('A', 'B')]
        region = self.loader.load_scene_info('Auditorium')['regions'][0]['name']
        plan = {'where': 'Auditorium', 'groups': [], 'singles': [
            {'position_id': 'Position 1', 'character': 'A', 'region': region, 'lookat': 'center'},
            {'position_id': 'Position 2', 'character': 'B', 'region': region, 'lookat': 'center'},
            {'position_id': 'Position 3', 'character': 'B', 'region': region, 'lookat': 'center'},
        ]}

        self.assertTrue(validate_script(data, self.loader)['valid'])
        self.assertTrue(validate_bundle(data, camera, actors, self.loader, plans=plan)['valid'])

    def test_null_nested_option_is_rejected(self):
        data = sample()
        data[0]['scene'][0]['actions'] = [{'character': 'A', 'state': 'standing', 'action': 'Sit Down', 'then-interact': None}]
        self.assertFalse(validate_script(data, self.loader)['valid'])

    def test_empty_library_does_not_hide_wrong_types(self):
        data = sample()
        data[0]['scene information']['emotionLibrary'] = []
        data[0]['scene'][0]['then-interact'] = {'target': [], 'action': 2}
        normalized, _ = normalize_script(data, self.loader)
        self.assertFalse(validate_script(normalized, self.loader)['valid'])


if __name__ == '__main__':
    unittest.main()
