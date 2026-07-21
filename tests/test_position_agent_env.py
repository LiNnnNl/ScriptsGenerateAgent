import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import position_agent_standalone as standalone


class PositionAgentEnvironmentTest(unittest.TestCase):
    def test_argument_parser_has_no_bundled_api_key(self):
        with patch.dict(os.environ, {}, clear=True):
            args = standalone.build_argument_parser().parse_args([
                "--scene-export-path", "scene.json",
                "--script-file-path", "script.json",
                "--positions-template-path", "positions.json",
            ])

        self.assertIsNone(args.deepseek_api_key)

    def test_runner_prefers_api_key_environment_variable(self):
        runner = standalone.PositionAgentRunner.__new__(standalone.PositionAgentRunner)
        runner.config = SimpleNamespace(deepseek_api_key=None)

        with patch.dict(
            os.environ,
            {"API_KEY": "project-env-key", "DEEPSEEK_API_KEY": "legacy-env-key"},
            clear=True,
        ):
            self.assertEqual("project-env-key", runner.resolve_api_key())

    def test_runner_supports_legacy_environment_variable(self):
        runner = standalone.PositionAgentRunner.__new__(standalone.PositionAgentRunner)
        runner.config = SimpleNamespace(deepseek_api_key=None)

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "legacy-env-key"}, clear=True):
            self.assertEqual("legacy-env-key", runner.resolve_api_key())


if __name__ == "__main__":
    unittest.main()
