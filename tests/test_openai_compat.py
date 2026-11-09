# bmlib — shared library for biomedical literature tools
# Copyright (C) 2024-2026 Dr Horst Herb
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""Tests for OpenAI-compatible base provider."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from bmlib.llm.data_types import LLMMessage
from bmlib.llm.providers.base import ModelMetadata, ModelPricing


class _StubProvider:
    """Minimal concrete subclass for testing the base class."""

    PROVIDER_NAME = "stub"
    DISPLAY_NAME = "Stub"
    DESCRIPTION = "Test stub"
    WEBSITE_URL = "https://stub.test"
    SETUP_INSTRUCTIONS = "N/A"
    API_KEY_ENV_VAR = "STUB_API_KEY"
    DEFAULT_BASE_URL = "https://api.stub.test/v1"
    DEFAULT_MODEL = "stub-model"
    FALLBACK_MODELS = [
        ModelMetadata(
            model_id="stub-model",
            display_name="Stub Model",
            context_window=128_000,
            pricing=ModelPricing(1.0, 2.0),
        ),
    ]
    MODEL_PRICING = {
        "stub-model": ModelPricing(1.0, 2.0),
    }


@pytest.fixture
def StubProvider():
    from bmlib.llm.providers.openai_compat import OpenAICompatibleProvider

    class _Impl(_StubProvider, OpenAICompatibleProvider):
        pass

    return _Impl


class TestProperties:
    def test_is_not_local(self, StubProvider):
        p = StubProvider(api_key="test-key")
        assert p.is_local is False
        assert p.is_free is False
        assert p.requires_api_key is True

    def test_api_key_env_var(self, StubProvider):
        p = StubProvider(api_key="k")
        assert p.api_key_env_var == "STUB_API_KEY"

    def test_default_model(self, StubProvider):
        p = StubProvider(api_key="k")
        assert p.default_model == "stub-model"

    def test_default_base_url(self, StubProvider):
        p = StubProvider(api_key="k")
        assert p.default_base_url == "https://api.stub.test/v1"


class TestChat:
    def test_chat_routes_to_openai_sdk(self, StubProvider):
        p = StubProvider(api_key="test-key")

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Hello back"
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        mock_response.choices[0].finish_reason = "stop"

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        p._client = mock_client

        msgs = [LLMMessage(role="user", content="Hello")]
        result = p.chat(msgs, model="stub-model")

        assert result.content == "Hello back"
        assert result.input_tokens == 10
        assert result.output_tokens == 5

        call_kwargs = mock_client.chat.completions.create.call_args
        assert call_kwargs.kwargs["model"] == "stub-model"
        # What chat_json's temperature-0 shortcut asks matches the request (#708).
        assert "temperature" in call_kwargs.kwargs
        assert p.honours_temperature("stub-model") is True

    def test_reasoning_model_uses_max_completion_tokens(self, StubProvider):
        from bmlib.llm.providers.openai_compat import OpenAICompatibleProvider

        class _Reasoning(_StubProvider, OpenAICompatibleProvider):
            def _is_reasoning_model(self, model):
                return True

        p = _Reasoning(api_key="k")
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "ok"
        mock_response.choices[0].message.tool_calls = None
        mock_response.usage.prompt_tokens = 1
        mock_response.usage.completion_tokens = 1
        mock_response.choices[0].finish_reason = "stop"
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        p._client = mock_client

        p.chat([LLMMessage(role="user", content="hi")], temperature=0.7, max_tokens=99)

        kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert kwargs["max_completion_tokens"] == 99
        assert "max_tokens" not in kwargs
        assert "temperature" not in kwargs
        assert p.honours_temperature("any") is False  # #708

    def test_openai_provider_detects_reasoning_models(self):
        from bmlib.llm.providers.openai_provider import OpenAIProvider

        p = OpenAIProvider(api_key="k")
        assert p._is_reasoning_model("o1") is True
        assert p._is_reasoning_model("o3-mini") is True
        assert p._is_reasoning_model("gpt-4o") is False

    def test_chat_separates_system_message(self, StubProvider):
        p = StubProvider(api_key="test-key")

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "OK"
        mock_response.usage.prompt_tokens = 20
        mock_response.usage.completion_tokens = 1
        mock_response.choices[0].finish_reason = "stop"

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        p._client = mock_client

        msgs = [
            LLMMessage(role="system", content="Be helpful"),
            LLMMessage(role="user", content="Hi"),
        ]
        p.chat(msgs)

        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        sent_messages = call_kwargs["messages"]
        assert any(m["role"] == "system" for m in sent_messages)


def _served(body: bytes, content_type: str = "application/json"):
    """A real openai client whose server answers every request 200 with *body*."""
    import httpx
    from openai import OpenAI

    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=body, headers={"content-type": content_type})
    )
    return OpenAI(
        api_key="k",
        base_url="https://api.stub.test/v1",
        http_client=httpx.Client(transport=transport),
        max_retries=0,
    )


class TestAJsonModeReplyIsNotPickedAroundARefusal:
    """A ``json_mode`` reply whose JSON holds a ``NaN`` is not swapped for the
    object beside it, which ``chat_json`` would read as a clean answer (#572)."""

    @staticmethod
    def _completion(content: str) -> bytes:
        import json

        choice = {"index": 0, "message": {"role": "assistant", "content": content}}
        return json.dumps({"id": "x", "model": "m", "choices": [choice]}).encode()

    @pytest.mark.parametrize(
        ("content", "expected"),
        [
            ('Sure: {"a": 1}', '{"a": 1}'),
            ('Sure: [{"a": 1}, {"b": NaN}]', 'Sure: [{"a": 1}, {"b": NaN}]'),
        ],
        ids=["clean", "refused"],
    )
    def test_the_content(self, StubProvider, content, expected):
        p = StubProvider(api_key="k")
        p._client = _served(self._completion(content))
        result = p.chat([LLMMessage(role="user", content="Hi")], json_mode=True)
        assert result.content == expected


class TestAnErrorShaped200IsTheServers:
    """A 200 carrying no choice is the server's failure, not a bmlib defect (#617).

    Driven through the real SDK, since what it hands back for each body
    (a ``ChatCompletion`` built unvalidated, or the body itself) is the input.
    """

    @pytest.mark.parametrize(
        ("body", "content_type", "message"),
        [
            (b'{"choices": []}', "application/json", '(body: {"choices": []})'),
            (
                b'{"choices": null, "error": {"message": "upstream down", "code": 502}}',
                "application/json",
                "(server said: upstream down)",
            ),
            (b'{"error": "rate limited"}', "application/json", "(server said: rate limited)"),
            (
                b'{"error": {"code": 429, "metadata": {"raw": "upstream x"}}}',
                "application/json",
                '(body: {"error": {"code": 429, "metadata": {"raw": "upstream x"}}})',
            ),
            (
                b'{"error": {"message": " "}}',
                "application/json",
                '(body: {"error": {"message": " "}})',
            ),
            (
                b'{"error": {"message": 42}}',
                "application/json",
                '(body: {"error": {"message": 42}})',
            ),
            (b"<html>bad gateway</html>", "text/html", "(body: <html>bad gateway</html>)"),
            (b"[1, 2]", "application/json", "(body: [1, 2])"),
            # Each object element is built into a ChatCompletion; the quote is
            # what the server sent, not that model's repr.
            (
                b'[{"error": {"message": "quota"}}]',
                "application/json",
                '(body: [{"error": {"message": "quota"}}])',
            ),
            (b"null", "application/json", "(body: null)"),
            (b'{"choices": "x"}', "application/json", '(body: {"choices": "x"})'),
            (b'{"choices": {"0": {}}}', "application/json", '(body: {"choices": {"0": {}}})'),
            (b"x" * 500, "text/plain", "(body: " + "x" * 200 + ")"),
        ],
        ids=[
            "empty",
            "error-object",
            "error-string",
            "error-without-message",
            "blank-message",
            "message-not-text",
            "not-json",
            "array",
            "array-of-objects",
            "json-null",
            "choices-a-string",
            "choices-an-object",
            "long-body",
        ],
    )
    def test_it_is_a_value_error_naming_what_arrived(
        self, StubProvider, body, content_type, message
    ):
        p = StubProvider(api_key="k")
        p._client = _served(body, content_type)
        with pytest.raises(ValueError) as caught:
            p.chat([LLMMessage(role="user", content="Hi")])
        assert type(caught.value) is ValueError
        assert str(caught.value) == f"stub: no choices in the response {message}"

    @pytest.mark.parametrize(
        ("body", "stop_reason", "what"),
        [
            (b'{"choices": [{"index": 0, "finish_reason": "stop"}]}', "stop", "no message"),
            (b'{"choices": ["x"]}', None, "no message"),
            (b'{"choices": [{"message": {"content": 5}}]}', None, "int content"),
            (
                b'{"choices": [{"message": {"content": [{"type": "text", "text": "x"}]}}]}',
                None,
                "list content",
            ),
        ],
        ids=["no-message", "not-an-object", "content-a-number", "content-a-list"],
    )
    def test_a_choice_carrying_no_text_is_empty_content_and_says_so(
        self, StubProvider, caplog, body, stop_reason, what
    ):
        # Rust's parse_openai_response reads each as empty content; json_mode
        # is chat_json's, and once fed a number to json.loads.
        p = StubProvider(api_key="k")
        p._client = _served(body)
        with caplog.at_level("WARNING", logger="bmlib.llm.providers.openai_compat"):
            result = p.chat([LLMMessage(role="user", content="Hi")], json_mode=True)
        assert result.content == ""
        assert result.tool_calls is None
        assert result.stop_reason == stop_reason
        assert caplog.messages == [
            f"stub: the response's choice carries {what} "
            f"(finish_reason {stop_reason!r}); read as empty content"
        ]

    @pytest.mark.parametrize(
        "usage",
        [b'"x"', b'{"prompt_tokens": null, "completion_tokens": "7"}', b'{"prompt_tokens": true}'],
        ids=["a-string", "not-counts", "a-bool"],
    )
    def test_usage_that_is_not_a_count_reads_as_zero(self, StubProvider, usage):
        p = StubProvider(api_key="k")
        p._client = _served(
            b'{"choices": [{"message": {"content": "hi"}}], "usage": ' + usage + b"}"
        )
        result = p.chat([LLMMessage(role="user", content="Hi")])
        assert (result.content, result.input_tokens, result.output_tokens) == ("hi", 0, 0)

    def test_a_finish_reason_that_is_not_text_is_none(self, StubProvider):
        p = StubProvider(api_key="k")
        p._client = _served(b'{"choices": [{"message": {"content": "hi"}, "finish_reason": 5}]}')
        assert p.chat([LLMMessage(role="user", content="Hi")]).stop_reason is None

    def test_tool_calls_that_are_not_a_list_are_none(self, StubProvider):
        p = StubProvider(api_key="k")
        p._client = _served(b'{"choices": [{"message": {"content": "hi", "tool_calls": 5}}]}')
        assert p.chat([LLMMessage(role="user", content="Hi")]).tool_calls is None

    def test_a_tool_call_naming_no_function_is_dropped(self, StubProvider):
        # As Rust; a non-text id reads as "".
        p = StubProvider(api_key="k")
        p._client = _served(
            b'{"choices": [{"message": {"tool_calls": ['
            b'{"id": "a", "function": "f"},'
            b'{"id": "b", "function": {"arguments": "{}"}},'
            b'{"id": 7, "function": {"name": "g", "arguments": "{\\"q\\": 1}"}}]}}]}'
        )
        result = p.chat([LLMMessage(role="user", content="Hi")])
        assert [(c.id, c.name, c.arguments) for c in result.tool_calls] == [("", "g", {"q": 1})]


class TestListModels:
    def test_list_models_from_api(self, StubProvider):
        p = StubProvider(api_key="test-key")

        mock_model = MagicMock()
        mock_model.id = "stub-model"

        mock_client = MagicMock()
        mock_client.models.list.return_value.data = [mock_model]
        p._client = mock_client

        models = p.list_models()
        assert len(models) >= 1
        assert models[0].model_id == "stub-model"

    def test_list_models_fallback_on_error(self, StubProvider):
        p = StubProvider(api_key="test-key")

        mock_client = MagicMock()
        mock_client.models.list.side_effect = Exception("API error")
        p._client = mock_client

        models = p.list_models()
        assert len(models) == 1
        assert models[0].model_id == "stub-model"


class TestTokenCounting:
    def test_count_tokens_estimation(self, StubProvider):
        p = StubProvider(api_key="k")
        count = p.count_tokens("Hello world, this is a test.")
        assert count > 0
        assert isinstance(count, int)


class TestConnectionTest:
    def test_connection_success(self, StubProvider):
        p = StubProvider(api_key="test-key")

        mock_model = MagicMock()
        mock_model.id = "m1"
        mock_client = MagicMock()
        mock_client.models.list.return_value.data = [mock_model]
        p._client = mock_client

        ok, msg = p.test_connection()
        assert ok is True

    def test_connection_failure(self, StubProvider):
        p = StubProvider(api_key="test-key")

        mock_client = MagicMock()
        mock_client.models.list.side_effect = Exception("Connection refused")
        p._client = mock_client

        ok, msg = p.test_connection()
        assert ok is False
        assert "Connection refused" in msg


class TestEmptyModelListCaching:
    """A successful-but-empty API model list must not be re-queried every call."""

    def _empty_client(self):
        calls = {"n": 0}
        mock_client = MagicMock()

        def _list():
            calls["n"] += 1
            resp = MagicMock()
            resp.data = []
            return resp

        mock_client.models.list.side_effect = lambda: _list()
        return mock_client, calls

    def test_empty_list_result_is_cached(self, StubProvider):
        p = StubProvider(api_key="k")
        mock_client, calls = self._empty_client()
        p._client = mock_client

        first = p.list_models()
        second = p.list_models()

        assert first == list(p.FALLBACK_MODELS)
        assert second == first
        assert calls["n"] == 1  # second call served from cache

    def test_force_refresh_bypasses_cached_empty_result(self, StubProvider):
        p = StubProvider(api_key="k")
        mock_client, calls = self._empty_client()
        p._client = mock_client

        p.list_models()
        p.list_models(force_refresh=True)
        assert calls["n"] == 2

    def test_api_error_is_not_cached(self, StubProvider):
        # A transient failure must be retried on the next call, not cached.
        p = StubProvider(api_key="k")
        mock_client = MagicMock()
        mock_client.models.list.side_effect = Exception("API down")
        p._client = mock_client

        assert p.list_models() == list(p.FALLBACK_MODELS)
        assert p.list_models() == list(p.FALLBACK_MODELS)
        assert mock_client.models.list.call_count == 2


class TestListModelsCacheIsolation:
    """Mutating a returned model list must not corrupt the cache (issue #12)."""

    def _stub_api_client(self):
        mock_model = MagicMock()
        mock_model.id = "stub-model"
        mock_client = MagicMock()
        mock_client.models.list.return_value.data = [mock_model]
        return mock_client

    def test_mutating_first_result_does_not_corrupt_cache(self, StubProvider):
        # The list returned at the cache-store point must not alias the cache.
        p = StubProvider(api_key="k")
        p._client = self._stub_api_client()

        first = p.list_models()
        first.clear()

        second = p.list_models()  # served from cache
        assert [m.model_id for m in second] == ["stub-model"]

    def test_mutating_cache_hit_result_does_not_corrupt_cache(self, StubProvider):
        # The list returned at the cache-hit point must not alias the cache either.
        p = StubProvider(api_key="k")
        p._client = self._stub_api_client()

        p.list_models()
        second = p.list_models()  # cache hit
        second.append("bogus")

        third = p.list_models()  # cache hit again
        assert [m.model_id for m in third] == ["stub-model"]
