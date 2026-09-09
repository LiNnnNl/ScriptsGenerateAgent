import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from src.cinematography.cinematography_position_stage import CinematographyPositionStage


class PositionCoverageTests(unittest.TestCase):
    def test_coordinates_do_not_overlap_when_singles_share_anchor(self):
        planning = {'where': 'Auditorium', 'groups': [], 'singles': [
            {'position_id': 'Position 1', 'region': 'Judges', 'neartarget': 'Desk'},
            {'position_id': 'Position 2', 'region': 'Judges', 'neartarget': 'Desk'},
        ]}
        scene_info = {'regions': [{'name': 'Judges', 'anchors': [
            {'name': 'Desk', 'position': {'x': 0.0, 'y': 0.0, 'z': 4.0}},
        ]}]}
        with tempfile.TemporaryDirectory() as directory:
            stage = CinematographyPositionStage({}, scene_info, {}, Mock(), directory)
            positions = stage._run_coordinates(planning)['positions']
        self.assertEqual(2, len({tuple(value.values()) for value in positions.values()}))

    def test_missing_return_position_retries_at_both_stages(self):
        script = {'initial position': [{'character': 'A', 'position': 'Position 1'},
                                       {'character': 'B', 'position': 'Position 2'}],
                  'scene': [{'move': [{'character': 'B', 'destination': 'Position 3'}]},
                            {'move': [{'character': 'B', 'destination': 'Position 2'}]}]}
        complete = {'where': 'Auditorium', 'groups': [], 'singles': [
            {'position_id': f'Position {i}', 'character': 'A' if i == 1 else 'B', 'region': '舞台'}
            for i in (1, 2, 3)]}
        missing = copy.deepcopy(complete)
        del missing['singles'][1]
        with tempfile.TemporaryDirectory() as directory:
            client = Mock()
            stage = CinematographyPositionStage(script, {}, {}, client, directory)
            for method in ('grouping', 'planning'):
                with self.subTest(method=method):
                    client.complete_json.reset_mock()
                    client.complete_json.side_effect = [copy.deepcopy(missing), copy.deepcopy(complete)]
                    if method == 'grouping':
                        result = stage._run_grouping('Auditorium', stage._collect_position_ids(),
                                                     stage._collect_position_characters(), {})
                    else:
                        result = stage._run_planning('Auditorium', complete)
                    self.assertEqual(complete, result)
                    self.assertEqual(2, client.complete_json.call_count)
                    self.assertIn('Position 2', client.complete_json.call_args.args[1]['correction_required'])
            client.complete_json.side_effect = [copy.deepcopy(missing) for _ in range(3)]
            with self.assertRaisesRegex(ValueError, 'Position 2'):
                stage._run_planning('Auditorium', complete)
            client.complete_json.side_effect = [copy.deepcopy(missing) for _ in range(3)]
            with self.assertRaisesRegex(ValueError, 'Position 2'):
                stage._run_grouping('Auditorium', stage._collect_position_ids(), stage._collect_position_characters(), {})
        duplicate = copy.deepcopy(complete)
        duplicate['singles'].append(copy.deepcopy(duplicate['singles'][0]))
        self.assertTrue(stage._position_coverage_errors(duplicate, ['Position 1', 'Position 2', 'Position 3']))


if __name__ == '__main__':
    unittest.main()
