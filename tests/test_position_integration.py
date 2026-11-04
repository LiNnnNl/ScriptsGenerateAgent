import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from src import position_integration as position
from src import generation_jobs as jobs


def bundle():
    def state(sid, pid):
        return {'blocking_state_id': sid, 'actors': [{'character': '甲', 'presence': 'present', 'placement_id': pid, 'pose': 'stand'}]}
    script = [{'script_id': 'S1', 'scene information': {'where': 'MinNan', 'who': ['甲']}, 'scene': [
        {'event_index': 0, 'speaker': '甲', 'content': '我来了。', 'shot_description': '甲走近后说话。',
         'blocking_state_id': 'BS1', 'blocking_transitions': [{'from_state_id': 'BS1', 'to_state_id': 'BS2', 'start_when': 'before_event'}]},
        {'event_index': 1, 'speaker': '甲', 'content': '我在这里。', 'shot_description': '甲站立。', 'blocking_state_id': 'BS2'}]}]
    common = {'scene_id': 'MinNan', 'parent_script': {'script_id': 'S1'}}
    plan = {**common, 'plan_id': 'BP1', 'blocking_states': [state('BS1', 'PL1'), state('BS2', 'PL2')]}
    spatial = {**common, 'solution_id': 'SP1', 'parent_blocking_plan': {'plan_id': 'BP1'}, 'placements': [
        {'placement_id': pid, 'character': '甲', 'position': [i, 0, 0], 'rotation': [0, 90, 0],
         'root_rotation_space': 'body_facing_world', 'model_yaw_correction': 0}
        for i, pid in enumerate(['PL1', 'PL2'])]}
    attention = {**common, 'parent_blocking_plan': {'plan_id': 'BP1'}, 'parent_spatial_solution': {'solution_id': 'SP1'},
                 'events': [{'event_index': 0, 'actors': [{'character': '甲', 'target': {'type': 'forward'}}]}]}
    return dict(zip(position.FILES, [script, plan, spatial, attention]))


class NativePositionTests(unittest.TestCase):
    def test_authored_distance_audit_checks_opening_before_movement(self):
        docs = bundle()
        docs['script.json'][0]['scene information']['who'].append('乙')
        for state in docs['blocking_plan.json']['blocking_states']:
            state['actors'].append({'character': '乙', 'presence': 'present', 'placement_id': 'PL3', 'pose': 'stand'})
        third = copy.deepcopy(docs['spatial_solution.json']['placements'][0])
        third.update(placement_id='PL3', character='乙', position=[1.5, 0, 0])
        docs['spatial_solution.json']['placements'].append(third)
        source = copy.deepcopy(docs['script.json'])
        source[0]['scene information']['what'] = '摘要中两人相距八米。\n原始本幕叙事与约束：\n开场两人相距约三米。\n镜头1：甲走近。'
        native = {'documents': docs, 'source': source}
        warnings = position.audit_authored_distances(native)
        self.assertEqual(1, len(warnings))
        self.assertEqual('AUTHORED_DISTANCE_MISMATCH', warnings[0]['code'])
        self.assertEqual(1.5, warnings[0]['actual_m'])
        third['position'] = [3, 0, 0]
        self.assertEqual([], position.audit_authored_distances(native))
        source[0]['scene'][1].update(speaker='', content='两人相距一米半。')
        self.assertEqual(2, position.audit_authored_distances(native)[0]['actual_m'])

    def test_completed_job_replays_latest_audit(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(jobs, 'JOBS', Path(temp)):
            root = jobs.directory('audit')
            root.mkdir()
            result = {'type': 'success', 'warnings': [{'code': 'AUTHORED_DISTANCE_MISMATCH'}]}
            position.save(root/'state.json', {'state': 'completed', 'result': result})
            (root/'events.jsonl').write_text('{"type":"success","warnings":[]}\n')
            self.assertEqual([result], jobs.status('audit')['events'])

    def test_new_distance_policy_is_forwarded_and_release_is_recorded(self):
        with tempfile.TemporaryDirectory() as temp:
            config = {'home': temp, 'release': 'releases/new', 'env_file': 'private.env', 'model': 'test',
                      'max_calls': 64, 'max_total_tokens': 5000000, 'max_run_seconds': 3600,
                      'max_turns': 32, 'distance_policy': {'approx_tolerance_m': 0.3}}
            root = Path(temp) / 'jobs/test/run'
            def finish(**kwargs):
                position.save(root / 'final_result.json', {})
                return 2  # A published bundle waiting for Unity is a valid delivery.
            with patch.object(position, 'settings', return_value=config), \
                 patch.object(position, 'scene_directory', return_value=Path(temp)), \
                 patch.object(position.subprocess, 'Popen') as process, \
                 patch.object(position.subprocess, 'run') as verify, \
                 patch.object(position, 'verify_delivery', return_value=({}, bundle())):
                process.return_value.pid = 123
                process.return_value.wait.side_effect = finish
                verify.return_value.returncode = 0
                position.run_position([], 'MinNan', 'test')
                argv = process.call_args.args[0]
                self.assertEqual(config['distance_policy'], json.loads(argv[argv.index('--distance-policy') + 1]))
                self.assertEqual('32', argv[argv.index('--max-turns') + 1])
                self.assertEqual(Path(temp) / 'releases/new', verify.call_args.kwargs['cwd'])
            record = position.read(root.parent / 'worker.json')
            self.assertEqual('releases/new', record['release'])
            self.assertEqual(2, record['exit_code'])

    def test_authored_distance_uses_bound_policy_not_old_warning_tolerance(self):
        docs = bundle()
        docs['script.json'][0]['scene information']['who'].append('乙')
        for state in docs['blocking_plan.json']['blocking_states']:
            state['actors'].append({'character': '乙', 'presence': 'present', 'placement_id': 'PL3', 'pose': 'stand'})
        third = copy.deepcopy(docs['spatial_solution.json']['placements'][0])
        third.update(placement_id='PL3', character='乙', position=[2.6, 0, 0])
        docs['spatial_solution.json']['placements'].append(third)
        source = copy.deepcopy(docs['script.json'])
        source[0]['scene information']['what'] = '开场两人相距约三米。\n镜头1：甲走近。'
        native = {'documents': docs, 'source': source}
        self.assertEqual([], position.audit_authored_distances(native))
        docs['blocking_plan.json']['distance_policy'] = {'approx_tolerance_m': 0.3, 'exact_tolerance_m': 0.05}
        warning = position.audit_authored_distances(native)[0]
        self.assertEqual(0.3, warning['tolerance_m'])
        self.assertEqual(2.6, warning['actual_m'])
        third['position'] = [3.3000004291534424, 0, 0]
        self.assertEqual([], position.audit_authored_distances(native))
        third['position'] = [3.300002, 0, 0]
        self.assertEqual(1, len(position.audit_authored_distances(native)))

    def test_glm53_request_uses_supported_reasoning_mode(self):
        from src import autogen_agents
        with patch.dict(os.environ, {'API_KEY': 'test-only', 'BASE_URL': 'https://open.bigmodel.cn/api/paas/v4', 'MODEL': 'glm-5.3-flash'}), \
             patch.object(autogen_agents, 'DiagnosticOpenAIChatCompletionClient') as client, \
             patch.object(autogen_agents.httpx, 'AsyncClient'):
            autogen_agents.make_model_client(structured_json=True)
        self.assertEqual({'thinking': {'type': 'enabled'}, 'reasoning_effort': 'low'}, client.call_args.kwargs['extra_body'])

    def test_silent_movement_normalizes_without_a_dialogue_repair(self):
        from src.autogen_pipeline import _normalize_direct_scene
        source = {'scene information': {'where': 'MinNan', 'who': ['甲']},
                  'scene': [{'speaker': '', 'content': '甲走近桌边。', 'actions': [],
                             'move': [{'character': '甲', 'destination': 'Position 2'}]}]}
        result = _normalize_direct_scene(source, ['甲'], 'MinNan', '甲走近桌边。')
        event = result['scene'][0]
        self.assertNotIn('speaker', event)
        self.assertNotIn('content', event)
        self.assertIn('甲走近桌边。', event['shot_description'])
        self.assertEqual('Position 2', event['move'][0]['destination'])

    def test_camera_uses_post_before_event_and_pre_after_dialogue_state(self):
        docs = bundle()
        states, _ = position.validate_native_documents(docs)
        event = docs['script.json'][0]['scene'][0]
        self.assertEqual('BS2', position.event_state(event, states)['blocking_state_id'])
        event['blocking_transitions'][0]['start_when'] = 'after_dialogue'
        self.assertEqual('BS1', position.event_state(event, states)['blocking_state_id'])

    def test_unplanned_motion_rejected_even_when_upstream_gates_pass(self):
        native = {'documents': bundle()}
        editorial = {'scene': [{'speaker': '甲'}, {'speaker': '甲'}]}
        with self.assertRaisesRegex(ValueError, '未被安排移动'):
            position.validate_motion_continuity(native, editorial)
        editorial['scene'][0]['move'] = [{'character': '甲', 'destination': 'Position 2'}]
        position.validate_motion_continuity(native, editorial)

    def test_silent_move_can_finish_at_immediately_following_before_event(self):
        docs = bundle()
        first, second = docs['script.json'][0]['scene']
        second['blocking_state_id'] = 'BS1'
        second['blocking_transitions'] = first.pop('blocking_transitions')
        first.update(speaker='', content='甲走近。')
        editorial = {'scene': [{'move': [{'character': '甲'}]}, {'speaker': '甲'}]}
        result = position.validate_motion_continuity({'documents': docs}, editorial)
        self.assertEqual([{'source_event_index': 0, 'transition_event_index': 1,
                           'characters': ['甲'], 'start_when': 'before_event'}], result)
        for mutation in ('late', 'dialogue', 'duplicate', 'extra'):
            changed, original = copy.deepcopy(docs), copy.deepcopy(editorial)
            event = changed['script.json'][0]['scene'][1]
            if mutation == 'late':
                event['blocking_transitions'][0]['start_when'] = 'after_dialogue'
            elif mutation == 'dialogue':
                original['scene'][0]['speaker'] = '甲'
            elif mutation == 'duplicate':
                original['scene'][1]['move'] = [{'character': '甲'}]
            else:
                event['blocking_transitions'].append({'from_state_id': 'BS2', 'to_state_id': 'BS1', 'start_when': 'after_dialogue'})
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                position.validate_motion_continuity({'documents': changed}, original)

    def test_opening_source_and_no_movement_rules_reach_planner(self):
        scene = {'scene information': {'where': 'MinNan', 'who': ['甲', '乙']},
                 'scene': [{'speaker': '甲', 'content': '你好。', 'shot_description': '反打镜头。', 'actions': []}]}
        result = position.narrative_input(scene, 'S1', '开场：两人相距三米。甲全程站在原处。\n镜头1：甲站立向乙问好。')[0]
        self.assertIn('相距三米', result['scene information']['what'])
        self.assertTrue(any(p['character'] == '甲' and '不发生平移' in p['motion_detail'] for p in result['scene'][0]['performance']))
        self.assertNotIn('反打镜头', str(result['scene'][0]['performance']))
        self.assertTrue(any(p['character'] == '乙' and '相距三米' in p['motion_detail'] for p in result['scene'][0]['performance']))

    def test_rejects_discontinuous_timeline_wrong_owner_and_old_rotation(self):
        for mutate in (
            lambda d: d['script.json'][0]['scene'][1].update(blocking_state_id='BS1'),
            lambda d: d['spatial_solution.json']['placements'][0].update(character='乙'),
            lambda d: d['spatial_solution.json']['placements'][0].update(model_yaw_correction=180),
            lambda d: d['spatial_solution.json']['placements'][0].update(position=[0, float('nan'), 0]),
        ):
            docs = bundle()
            mutate(docs)
            with self.assertRaises(ValueError):
                position.validate_native_documents(docs)

    def test_source_dialogue_is_immutable(self):
        docs = bundle()
        source = copy.deepcopy(docs['script.json'])
        docs['script.json'][0]['scene'][0]['content'] = '被修改的对白'
        with self.assertRaisesRegex(ValueError, 'content'):
            position.validate_native_documents(docs, source)

    def test_legacy_move_becomes_narrative_and_slots_are_removed(self):
        source = {'scene information': {'where': 'MinNan', 'who': ['甲']},
                  'initial position': [{'character': '甲', 'position': 'Position 1'}], 'scene': [
                      {'move': [{'character': '甲', 'destination': 'Position 2'}], 'shot_description': '甲走到门口。', 'current position': []},
                      {'speaker': '甲', 'content': '你好。', 'shot_description': '甲面向乙。', 'actions': []}]}
        result = position.narrative_input(source, 'test')[0]
        self.assertNotIn('initial position', result)
        self.assertNotIn('move', result['scene'][0])
        self.assertEqual('甲走到门口。', result['scene'][0]['content'])
        self.assertEqual('你好。', result['scene'][1]['content'])
        self.assertEqual('Position 2', source['scene'][0]['move'][0]['destination'])

    def test_rejects_action_pose_conflict(self):
        docs = bundle()
        docs['script.json'][0]['scene'][0]['actions'] = [{'character': '甲', 'state': 'sitting', 'action': ''}]
        with self.assertRaisesRegex(ValueError, '动作姿态'):
            position.validate_native_documents(docs)

    def test_delivery_hash_and_capability_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            docs = bundle()
            refs = {}
            for name, value in docs.items():
                position.save(root/'delivery'/name, value)
                refs[name] = {'path': 'delivery/'+name, 'sha256': hashlib.sha256((root/'delivery'/name).read_bytes()).hexdigest()}
            final = {'delivery': {'available': True, 'files': refs}, 'capabilities': {'can_validate_in_unity': True}, 'status': 'completed_pending_unity'}
            position.save(root/'final_result.json', final)
            position.verify_delivery(root)
            position.save(root/'delivery/script.json', [])
            with self.assertRaisesRegex(ValueError, '校验失败'):
                position.verify_delivery(root)
            final['capabilities']['can_validate_in_unity'] = False
            position.save(root/'final_result.json', final)
            with self.assertRaisesRegex(ValueError, '未通过交付'):
                position.verify_delivery(root)

    def test_job_polling_does_not_consume_or_duplicate_events(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(jobs, 'JOBS', Path(temp)):
            root = jobs.directory('test')
            root.mkdir()
            position.save(root/'state.json', {'state': 'completed', 'job_id': 'test'})
            (root/'events.jsonl').write_bytes(b'{"type":"log"}\n{"type":"success"}\n')
            first = jobs.status('test')
            self.assertEqual(first, jobs.status('test'))
            self.assertEqual([], jobs.status('test', first['cursor'])['events'])
            with self.assertRaises(ValueError):
                jobs.directory('../test')

    def test_partial_event_is_not_returned_or_skipped(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(jobs, 'JOBS', Path(temp)):
            root = jobs.directory('test')
            root.mkdir()
            position.save(root/'state.json', {'state': 'completed', 'job_id': 'test'})
            (root/'events.jsonl').write_bytes(b'{"type":"log"}\n{"type":')
            result = jobs.status('test')
            self.assertEqual([{'type': 'log'}], result['events'])
            self.assertEqual(len(b'{"type":"log"}\n'), result['cursor'])
            self.assertFalse(result['has_more'])


if __name__ == '__main__':
    unittest.main()
