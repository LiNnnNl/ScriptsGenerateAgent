import importlib.util
import tempfile
import unittest
from pathlib import Path


_SPEC = importlib.util.spec_from_file_location(
    "registry", Path(__file__).parents[1] / "backend" / "src" / "registry.py"
)
registry = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(registry)

_BRIDGE_SPEC = importlib.util.spec_from_file_location(
    "autogen_bridge", Path(__file__).parents[1] / "backend" / "src" / "autogen_bridge.py"
)
autogen_bridge = importlib.util.module_from_spec(_BRIDGE_SPEC)
_BRIDGE_SPEC.loader.exec_module(autogen_bridge)


class RegistryFormDataTest(unittest.TestCase):
    def test_session_keeps_only_refill_fields(self):
        original_path = registry.REGISTRY_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                registry.REGISTRY_PATH = Path(tmp) / "registry.json"
                form_data = registry.snapshot_form_data({
                    "creative_idea": "测试构想",
                    "scene_pool": ["SpaceStation"],
                    "direct_mode": True,
                    "api_key": "must-not-be-saved",
                })
                registry.register_session("1", {"script": "script.json"}, form_data=form_data)
                saved = registry.load_registry()["sessions"]["1"]["form_data"]

                self.assertEqual(saved["creative_idea"], "测试构想")
                self.assertEqual(saved["scene_pool"], ["SpaceStation"])
                self.assertTrue(saved["direct_mode"])
                self.assertNotIn("api_key", saved)
        finally:
            registry.REGISTRY_PATH = original_path

    def test_failed_session_keeps_form_data_and_can_finish_successfully(self):
        original_path = registry.REGISTRY_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                registry.REGISTRY_PATH = Path(tmp) / "registry.json"
                form = registry.snapshot_form_data({"creative_idea": "失败后复填"})
                registry.register_session(
                    "2", {}, label="生成中", form_data=form, status="running"
                )
                created_at = registry.load_registry()["sessions"]["2"]["created_at"]

                self.assertTrue(registry.update_session_status("2", "failed", "摄影规划失败"))
                failed = registry.load_registry()["sessions"]["2"]
                self.assertEqual(failed["status"], "failed")
                self.assertEqual(failed["form_data"]["creative_idea"], "失败后复填")

                registry.register_session(
                    "2", {"script": "script.json"}, label="完成", form_data=form
                )
                finished = registry.load_registry()["sessions"]["2"]
                self.assertEqual(finished["status"], "success")
                self.assertEqual(finished["error"], "")
                self.assertEqual(finished["created_at"], created_at)
        finally:
            registry.REGISTRY_PATH = original_path

    def test_bridge_reports_only_first_error(self):
        errors = []
        bridge = autogen_bridge.AutoGenStreamBridge(on_error=errors.append)
        bridge.put_event({"type": "error", "message": "第一次失败"})
        bridge.put_event({"type": "error", "message": "重复失败"})
        self.assertEqual(errors, ["第一次失败"])

    def test_bridge_reports_model_account_overdue_clearly(self):
        error = RuntimeError(
            "Error code: 403 - {'error': {'code': 'AccountOverdueError', "
            "'message': 'The request failed because your account has an overdue balance.'}}"
        )
        self.assertEqual(
            "模型服务账户欠费，请充值后重试。",
            autogen_bridge._user_facing_pipeline_error(error),
        )


if __name__ == "__main__":
    unittest.main()
