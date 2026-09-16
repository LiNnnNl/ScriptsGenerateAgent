import json
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


class _ScriptedDirector:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.prompts = []
        self.reset = AsyncMock()
        self._model_client = SimpleNamespace(_create_args={"model": "test", "max_tokens": 8000})

    async def on_messages_stream(self, messages, cancellation_token):
        self.prompts.append(messages[0].content)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        content, finish_reason = response
        self._model_client.last_create_result = SimpleNamespace(
            content=content, finish_reason=finish_reason,
        )
        yield SimpleNamespace(chat_message=SimpleNamespace(content=content))


class ThoughtEvent:
    def __init__(self, content):
        self.content = content


class _ReasoningOnlyDirector:
    def __init__(self, payload):
        self.payload = payload
        self.reset = AsyncMock()
        self._model_client = SimpleNamespace(_create_args={"model": "test", "max_tokens": 8000})

    async def on_messages_stream(self, messages, cancellation_token):
        self._model_client.last_create_result = SimpleNamespace(
            content="", thought=self.payload, finish_reason="stop",
        )
        yield ThoughtEvent(self.payload)
        yield SimpleNamespace(chat_message=SimpleNamespace(content="", models_usage=None))


class ModelResilienceTests(unittest.IsolatedAsyncioTestCase):
    def test_first_batch_agents_use_simple_model_only(self):
        import src.autogen_agents as agents

        factories = (
            agents.create_title_agent,
            agents.create_meeting_summary_agent,
            agents.create_story_ir_agent,
            agents.create_shot_plan_agent,
        )
        with patch.object(agents, "_SIMPLE_MODEL", "simple-model"), \
             patch.object(agents, "make_model_client", return_value=object()) as make_client, \
             patch.object(agents, "AssistantAgent", return_value=object()):
            for factory in factories:
                make_client.reset_mock()
                factory()
                make_client.assert_called_once_with("simple-model", structured_json=True)

            make_client.reset_mock()
            agents.create_title_agent("explicit-model")
            make_client.assert_called_once_with("explicit-model", structured_json=True)

    def test_director_keeps_primary_model_route(self):
        import src.autogen_agents as agents

        with patch.object(agents, "build_director_system_message", return_value="prompt"), \
             patch.object(agents, "make_model_client", return_value=object()) as make_client, \
             patch.object(agents, "AssistantAgent", return_value=object()):
            agents.create_director_agent([], None, None)
        make_client.assert_called_once_with(None, structured_json=True)

    async def test_structured_ark_client_disables_deep_thinking(self):
        from src.autogen_agents import make_model_client

        with patch.dict("os.environ", {
            "API_KEY": "test-key",
            "BASE_URL": "https://ark.cn-beijing.volces.com/api/v3",
            "MODEL": "test-model",
        }, clear=False):
            client = make_model_client(structured_json=True)
        try:
            self.assertEqual(
                {"thinking": {"type": "disabled"}},
                client._create_args["extra_body"],
            )
        finally:
            await client.close()

    async def test_director_recovers_standalone_json_from_reasoning_channel(self):
        from src.autogen_bridge import AutoGenStreamBridge
        from src.autogen_pipeline import _run_director_agent

        script = [{"scene": [{"content": "完整 JSON"}]}]
        raw = json.dumps(script, ensure_ascii=False)
        result = await _run_director_agent(_ReasoningOnlyDirector(raw), "prompt", AutoGenStreamBridge())

        self.assertEqual(script, result)

    async def test_single_act_does_not_repeat_truncated_whole_request(self):
        from src.autogen_bridge import AutoGenStreamBridge
        from src.autogen_pipeline import _run_director_per_act_fallback

        bridge = AutoGenStreamBridge()
        bridge.last_error_details = {"model_response": {"finish_reason": "length"}, "continuations": 3}
        with patch("src.autogen_pipeline.create_director_agent") as factory:
            result = await _run_director_per_act_fallback(
                "prompt", bridge, [], None, None, 0, 1, [], None, None,
            )
        self.assertIsNone(result)
        factory.assert_not_called()

    async def test_director_continues_without_changing_existing_content(self):
        from src.autogen_bridge import AutoGenStreamBridge
        from src.autogen_pipeline import _run_director_agent

        script = [{"scene": [{"content": '台词 空格 "引号"', "duration": 12.5,
                              "actions": [1, 2], "move": [3, 4]}]}]
        raw = json.dumps(script, ensure_ascii=False)
        # String whitespace, half escape sequence, number and nested structure.
        cuts = [raw.index(" 空格") + 1, raw.index('\\"') + 1,
                raw.index("12.5") + 2, raw.index('"move"')]
        for cut in cuts:
            with self.subTest(cut=cut):
                director = _ScriptedDirector([(raw[:cut], "length"), (raw[cut:], "stop")])
                result = await _run_director_agent(director, "保留全部镜头", AutoGenStreamBridge())
                self.assertEqual(script, result)
                self.assertEqual(2, len(director.prompts))
                self.assertIn(json.dumps(raw[:cut], ensure_ascii=False), director.prompts[1])
                self.assertEqual(2, director.reset.await_count)

    async def test_director_continuation_keeps_prefix_after_network_retry(self):
        from src.autogen_bridge import AutoGenStreamBridge
        from src.autogen_pipeline import _run_director_agent

        director = _ScriptedDirector([
            ('[{"scene":[', "length"), RuntimeError("connection timeout"),
            ('{"content":"原文"}]}]', "stop"),
        ])
        with patch("src.autogen_pipeline._reset_agent_after_failed_request", new=AsyncMock()), \
             patch("src.autogen_pipeline.asyncio.sleep", new=AsyncMock()):
            result = await _run_director_agent(director, "prompt", AutoGenStreamBridge())
        self.assertEqual([{"scene": [{"content": "原文"}]}], result)
        self.assertEqual(director.prompts[1], director.prompts[2])

    async def test_director_multiple_continuations_and_incomplete_output_limit(self):
        from src.autogen_bridge import AutoGenStreamBridge
        from src.autogen_pipeline import _run_director_agent, _DIRECTOR_MAX_CONTINUATIONS

        director = _ScriptedDirector([('[{"scene":[', "length"),
                                      ('{"content":"原', "length"), ('文"}]}]', "stop")])
        self.assertEqual([{"scene": [{"content": "原文"}]}],
                         await _run_director_agent(director, "prompt", AutoGenStreamBridge()))
        bridge = AutoGenStreamBridge()
        director = _ScriptedDirector([('[{"content":"', "length")]
                                    + [("未完", "length")] * _DIRECTOR_MAX_CONTINUATIONS)
        self.assertIsNone(await _run_director_agent(director, "prompt", bridge))
        self.assertEqual(_DIRECTOR_MAX_CONTINUATIONS + 1, len(director.prompts))
        self.assertEqual(_DIRECTOR_MAX_CONTINUATIONS, bridge.last_error_details["continuations"])

    async def test_director_repeated_prefix_is_not_accepted_as_partial_success(self):
        from src.autogen_bridge import AutoGenStreamBridge
        from src.autogen_pipeline import _run_director_agent

        director = _ScriptedDirector([('[{"scene":[', "length"), ('[{"scene":[]}]', "stop")])
        self.assertIsNone(await _run_director_agent(director, "prompt", AutoGenStreamBridge()))

    async def test_director_complete_and_non_truncated_invalid_responses_do_not_continue(self):
        from src.autogen_bridge import AutoGenStreamBridge
        from src.autogen_pipeline import _run_director_agent

        for raw, expected in [('```json\n[{"scene":[]}]\n```', [{"scene": []}]), ('broken JSON', None)]:
            director = _ScriptedDirector([(raw, "stop")])
            self.assertEqual(expected, await _run_director_agent(director, "prompt", AutoGenStreamBridge()))
            self.assertEqual(1, len(director.prompts))

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
