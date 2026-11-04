import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

from src import performance
from src.cinematography.camera import CameraPlanningStage
from src.cinematography.camera_planning_stage import CameraPlanningStage as LegacyCameraStage
from src.cinematography.client import LLMJsonClient as LegacyClient
from src.llm_json_client import LLMJsonClient


class HandoffMergeTests(unittest.TestCase):
    def request_payload(self, base_url):
        with patch.dict(os.environ, {'API_KEY': 'test-only', 'BASE_URL': base_url,
                                    'MODEL': 'test-model'}, clear=True):
            client = LLMJsonClient()
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            'choices': [{'finish_reason': 'stop', 'message': {'content': '{"ok": true}'}}]
        }).encode()
        with patch('src.llm_json_client.urllib.request.urlopen', return_value=response) as request:
            self.assertEqual({'ok': True}, client.complete_json('system', {}))
        return json.loads(request.call_args.args[0].data)

    def test_shared_glm_client_keeps_required_thinking_parameters(self):
        self.assertIs(LegacyClient, LLMJsonClient)
        payload = self.request_payload('https://open.bigmodel.cn/api/paas/v4')
        self.assertEqual({'type': 'enabled'}, payload['thinking'])
        self.assertEqual('low', payload['reasoning_effort'])
        self.assertGreaterEqual(payload['max_tokens'], 16384)

    def test_other_providers_do_not_receive_glm_parameters(self):
        payload = self.request_payload('https://example.invalid/v1')
        self.assertNotIn('thinking', payload)
        self.assertNotIn('reasoning_effort', payload)
        self.assertEqual(4096, payload['max_tokens'])

    def test_native_release_is_not_mutated_or_sent_for_paid_reselection(self):
        script = [{'scene': [{'content': '原对白', 'actions': [{'action': 'approved'}]}]}]
        native = [{'documents': {'script.json': copy.deepcopy(script)}}]
        original = copy.deepcopy((script, native))
        with patch.object(performance, 'LLMJsonClient') as client, patch.object(performance, 'ActionSelectionStage') as action, patch.object(performance, 'ExpressionSelectionStage') as expression:
            result = performance.run_performance_pipeline(script, object(), native_scenes=native)
        self.assertEqual('native_release_immutable', result['skipped'])
        self.assertEqual(script, result['enriched_script'])
        self.assertEqual(original, (script, native))
        client.assert_not_called()
        action.assert_not_called()
        expression.assert_not_called()

    def test_legacy_performance_still_runs_both_stages(self):
        with patch.object(performance, 'LLMJsonClient'), patch.object(performance, 'ActionSelectionStage') as action, patch.object(performance, 'ExpressionSelectionStage') as expression:
            action.return_value.run.return_value = {'script': ['selected-actions']}
            expression.return_value.run.return_value = {'script': ['selected-expressions']}
            result = performance.run_performance_pipeline(['draft'], object())
        self.assertTrue(result['ok'])
        self.assertNotIn('skipped', result)
        self.assertEqual(['selected-expressions'], result['enriched_script'])
        self.assertEqual(['selected-actions'], expression.call_args.args[0])

    def test_moved_camera_stage_preserves_native_event_position_snapshot(self):
        self.assertIs(LegacyCameraStage, CameraPlanningStage)
        script = {'scene information': {'where': 'MinNan'},
                  'initial position': [{'character': '甲', 'position': 'PL1'}],
                  'scene': [{'speaker': '', 'content': '无台词', 'shot': 'scene',
                             'duration': 3, 'actions': [],
                             'current position': [{'character': '甲', 'position': 'PL2'}]}]}
        with tempfile.TemporaryDirectory() as directory:
            scene_info = {'where': 'MinNan', 'regions': [{'name': 'Main', 'anchors': []}]}
            camera_lib = {'中景': {'framing': 'medium', 'purpose': 'dialogue'}}
            stage = CameraPlanningStage(script, scene_info, camera_lib,
                                        output_dir=directory, stage_output_dir=directory,
                                        authoritative_snapshots=True)
            with patch.object(stage, '_resolve_current_positions', return_value=[]):
                stage.run()
            self.assertEqual('PL2', stage.script_payload['scene'][0]['current position'][0]['position'])
        self.assertEqual('PL1', script['initial position'][0]['position'])


if __name__ == '__main__':
    unittest.main()
