import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


class _FakeCompletions:
    def __init__(self):
        self.models = []

    def create(self, *, model, **kwargs):
        self.models.append(model)
        if len(self.models) == 1:
            raise RuntimeError("429 SetLimitExceeded: set inference limit reached")
        return {"ok": True, "kwargs": kwargs}


class _FakeClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=_FakeCompletions())


class _EmptyThenJsonAgent:
    def __init__(self):
        self.calls = 0

    async def on_messages_stream(self, messages, cancellation_token):
        self.calls += 1
        if self.calls == 2:
            yield SimpleNamespace(
                chat_message=SimpleNamespace(content='{"ok": true}')
            )


class ModelResilienceTests(unittest.IsolatedAsyncioTestCase):
    def test_character_generation_switches_to_fallback_on_set_limit(self):
        from app import _create_chat_completion_with_quota_fallback

        client = _FakeClient()
        response, used_model = _create_chat_completion_with_quota_fallback(
            client,
            "primary-model",
            "fallback-model",
            messages=[],
        )

        self.assertEqual("fallback-model", used_model)
        self.assertEqual(["primary-model", "fallback-model"], client.chat.completions.models)
        self.assertTrue(response["ok"])

    def test_common_tunnel_errors_are_retryable(self):
        from src.autogen_pipeline import _is_transient_network_error

        self.assertTrue(_is_transient_network_error(RuntimeError("503 Service Unavailable")))
        self.assertTrue(_is_transient_network_error(RuntimeError("unexpected EOF from proxy")))
        self.assertFalse(_is_transient_network_error(RuntimeError("invalid JSON schema")))

    async def test_stage_agent_retries_an_empty_response(self):
        from src.autogen_pipeline import _run_stage_agent_json_object

        agent = _EmptyThenJsonAgent()
        with patch(
            "src.autogen_pipeline._reset_agent_after_failed_request",
            new=AsyncMock(),
        ) as reset_mock, patch(
            "src.autogen_pipeline.asyncio.sleep",
            new=AsyncMock(),
        ):
            result = await _run_stage_agent_json_object(agent, "prompt")

        self.assertEqual({"ok": True}, result)
        self.assertEqual(2, agent.calls)
        reset_mock.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
