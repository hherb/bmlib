# bmlib — shared library for biomedical literature tools
# Copyright (C) 2024-2026 Dr Horst Herb
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""Tests for streamed chat (StudyAnalyzer item 6).

The reducer is held to the shared contract ``tests/fixtures/chat_stream.json``,
which the Rust port's ``tests/llm_stream_contract.rs`` reads too. The provider
and client tests use fake SDK clients; nothing touches the network.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from bmlib.llm import (
    LLMClient,
    LLMMessage,
    StreamDelta,
    StreamError,
    StreamState,
    get_token_tracker,
    reset_token_tracker,
    run_stream,
)
from bmlib.llm.providers.anthropic import AnthropicProvider
from bmlib.llm.providers.base import BaseProvider
from bmlib.llm.providers.deepseek import DeepSeekProvider
from bmlib.llm.providers.gemini import GeminiProvider
from bmlib.llm.providers.mistral import MistralProvider
from bmlib.llm.providers.ollama import OllamaProvider
from bmlib.llm.providers.openai_provider import OpenAIProvider
from bmlib.llm.stream import CANCELLED_STOP_REASON, STREAM_PROTOCOLS

FIXTURE = Path(__file__).parent / "fixtures" / "chat_stream.json"
CASES = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]

MESSAGES = [LLMMessage(role="user", content="Was it blinded?")]


def _drive(case: dict) -> tuple[list[StreamDelta], object]:
    """Run one fixture case; return the deltas seen and the response or error."""
    seen: list[StreamDelta] = []
    stop_after = case.get("stop_after")

    def on_delta(delta: StreamDelta) -> bool:
        seen.append(delta)
        return stop_after is None or len(seen) < stop_after

    try:
        result: object = run_stream(case["protocol"], case["events"], "model-x", on_delta)
    except StreamError as exc:
        result = exc
    return seen, result


class TestTheSharedContract:
    def test_every_protocol_has_cases(self):
        assert {case["protocol"] for case in CASES} == set(STREAM_PROTOCOLS)

    @pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
    def test_case(self, case):
        seen, result = _drive(case)
        assert [{"kind": d.kind, "text": d.text} for d in seen] == case["deltas"]
        if "error" in case:
            assert isinstance(result, StreamError)
            assert case["error"] in str(result)
            return
        assert not isinstance(result, Exception), result
        want = case["response"]
        got = {
            "content": result.content,
            "thinking": result.thinking,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "stop_reason": result.stop_reason,
        }
        assert got == want
        assert result.model == "model-x"
        assert result.total_tokens == want["input_tokens"] + want["output_tokens"]
        assert result.tool_calls is None


class TestRunStream:
    def test_an_unknown_protocol_is_refused(self):
        with pytest.raises(ValueError, match="unknown stream protocol"):
            StreamState("gemini")

    @pytest.mark.parametrize("returned", [None, True, 0, ""])
    def test_only_false_stops(self, returned):
        case = next(c for c in CASES if c["name"] == "openai/text-and-a-usage-chunk")
        response = run_stream("openai", case["events"], "m", lambda _d: returned)
        assert response.stop_reason == "stop"
        assert response.content == "Forty patients."

    def test_only_a_boolean_done_ends_an_ollama_stream(self):
        events = [{"message": {"content": "Cut"}, "done": "false"}]
        with pytest.raises(StreamError, match="ended before done"):
            run_stream("ollama", events, "m", lambda _d: None)

    def test_no_event_is_read_after_a_stop(self):
        read: list[dict] = []

        def events():
            for event in (
                {"choices": [{"delta": {"content": "a"}}]},
                {"choices": [{"delta": {"content": "b"}}]},
            ):
                read.append(event)
                yield event

        response = run_stream("openai", events(), "m", lambda _d: False)
        assert len(read) == 1
        assert response.stop_reason == CANCELLED_STOP_REASON


class TestTheTypesRefuseWhatCannotBe:
    @pytest.mark.parametrize(("kind", "text"), [("reasoning", "x"), ("text", "")])
    def test_a_delta_is_text_or_thinking_and_never_empty(self, kind, text):
        with pytest.raises(ValueError):
            StreamDelta(kind, text)  # type: ignore[arg-type]

    def test_completeness_cannot_be_set_from_outside(self):
        state = StreamState("openai")
        with pytest.raises(AttributeError):
            state.complete = True  # type: ignore[misc]
        with pytest.raises(StreamError, match="ended before a finish_reason"):
            state.finish("m")

    def test_a_state_is_spent_by_finish(self):
        state = StreamState("openai")
        state.apply({"choices": [{"delta": {"content": "a"}, "finish_reason": "stop"}]})
        assert state.finish("m").content == "a"
        with pytest.raises(RuntimeError, match="already built"):
            state.apply({"choices": [{"delta": {"content": "b"}}]})
        with pytest.raises(RuntimeError, match="already built"):
            state.finish("m")


# ---------------------------------------------------------------------------
# Providers, over fake SDK clients
# ---------------------------------------------------------------------------


class _FakeStream:
    """An SDK stream: iterable, closable, yielding objects with ``to_dict``."""

    def __init__(self, events: list[dict], dump: str = "to_dict") -> None:
        self._events = events
        self._dump = dump
        self.closed = False

    def __iter__(self):
        for event in self._events:
            yield SimpleNamespace(**{self._dump: lambda event=event: event})

    def close(self) -> None:
        self.closed = True


def _events(name: str) -> list[dict]:
    return next(c for c in CASES if c["name"] == name)["events"]


class TestAnthropicProvider:
    def _provider(self, stream: _FakeStream) -> AnthropicProvider:
        provider = AnthropicProvider(api_key="test-key")
        provider._client = MagicMock()
        provider._client.messages.create.return_value = stream
        return provider

    def test_streams_the_request_chat_would_send(self):
        stream = _FakeStream(_events("anthropic/thinking-then-text"))
        provider = self._provider(stream)
        seen: list[StreamDelta] = []
        response = provider.chat_stream(
            [LLMMessage(role="system", content="Be brief."), *MESSAGES],
            seen.append,
            model="claude-x",
            max_tokens=4096,
            think=2048,
        )
        kwargs = provider._client.messages.create.call_args.kwargs
        assert kwargs["stream"] is True
        assert kwargs["system"] == "Be brief."
        assert kwargs["thinking"] == {"type": "enabled", "budget_tokens": 2048}
        assert "temperature" not in kwargs
        assert [d.kind for d in seen] == ["thinking", "thinking", "text"]
        assert response.content == "Yes."
        assert response.model == "claude-x"
        assert stream.closed

    def test_the_stream_is_closed_when_the_caller_stops(self):
        stream = _FakeStream(_events("anthropic/text"))
        response = self._provider(stream).chat_stream(MESSAGES, lambda _d: False)
        assert response.stop_reason == CANCELLED_STOP_REASON
        assert stream.closed

    def test_the_stream_is_closed_when_it_ends_early(self):
        stream = _FakeStream(_events("anthropic/no-message-stop-is-an-error"))
        with pytest.raises(StreamError, match="ended before message_stop"):
            self._provider(stream).chat_stream(MESSAGES, lambda _d: None)
        assert stream.closed

    def test_the_provider_itself_refuses_tools(self):
        from bmlib.llm import LLMToolDefinition

        provider = self._provider(_FakeStream([]))
        with pytest.raises(NotImplementedError, match="tool calls"):
            provider.chat_stream(
                MESSAGES,
                lambda _d: None,
                tools=[LLMToolDefinition(name="f", description="", parameters={})],
            )
        provider._client.messages.create.assert_not_called()


class TestOpenAICompatibleProvider:
    def _provider(self, cls, stream: _FakeStream):
        provider = cls(api_key="test-key")
        provider._client = MagicMock()
        provider._client.chat.completions.create.return_value = stream
        return provider

    def test_openai_is_asked_for_usage(self):
        stream = _FakeStream(_events("openai/text-and-a-usage-chunk"))
        provider = self._provider(OpenAIProvider, stream)
        response = provider.chat_stream(MESSAGES, lambda _d: None, model="gpt-x", top_p=0.5)
        kwargs = provider._client.chat.completions.create.call_args.kwargs
        assert kwargs["stream"] is True
        assert kwargs["stream_options"] == {"include_usage": True}
        assert kwargs["top_p"] == 0.5
        assert (response.input_tokens, response.output_tokens) == (40, 4)
        assert stream.closed

    def test_a_provider_outside_the_list_is_not_asked_for_usage(self):
        stream = _FakeStream(_events("openai/reasoning-field"))
        provider = self._provider(MistralProvider, stream)
        provider.chat_stream(MESSAGES, lambda _d: None)
        kwargs = provider._client.chat.completions.create.call_args.kwargs
        assert kwargs["stream"] is True
        assert "stream_options" not in kwargs

    @pytest.mark.parametrize("cls", [OpenAIProvider, MistralProvider])
    def test_the_provider_itself_refuses_tools(self, cls):
        from bmlib.llm import LLMToolDefinition

        provider = self._provider(cls, _FakeStream([]))
        with pytest.raises(NotImplementedError, match="tool calls"):
            provider.chat_stream(
                MESSAGES,
                lambda _d: None,
                tools=[LLMToolDefinition(name="f", description="", parameters={})],
            )
        provider._client.chat.completions.create.assert_not_called()


class _RecordingOllamaClient:
    def __init__(self, stream) -> None:
        self.stream = stream
        self.last_kwargs: dict = {}

    def chat(self, **kwargs):
        self.last_kwargs = kwargs
        return self.stream


class TestOllamaProvider:
    def test_streams_the_native_chunks(self):
        stream = _FakeStream(_events("ollama/thinking-text-and-counts"), dump="model_dump")
        provider = OllamaProvider()
        provider._client = _RecordingOllamaClient(stream)
        seen: list[StreamDelta] = []
        response = provider.chat_stream(MESSAGES, seen.append, model="llama3.2", think=True)
        assert provider._client.last_kwargs["stream"] is True
        assert provider._client.last_kwargs["think"] is True
        assert provider._client.last_kwargs["options"]["num_predict"] == 4096
        assert response.content == "It was an RCT."
        assert response.thinking == "Read it."
        assert len(seen) == 3
        assert stream.closed

    def test_the_provider_itself_refuses_tools(self):
        from bmlib.llm import LLMToolDefinition

        provider = OllamaProvider()
        provider._client = _RecordingOllamaClient(_FakeStream([]))
        with pytest.raises(NotImplementedError, match="tool calls"):
            provider.chat_stream(
                MESSAGES,
                lambda _d: None,
                tools=[LLMToolDefinition(name="f", description="", parameters={})],
            )
        assert provider._client.last_kwargs == {}

    def test_an_older_sdk_yielding_dicts_is_read_too(self):
        events = _events("ollama/length")

        class _DictStream(list):
            closed = False

            def close(self):
                self.closed = True

        stream = _DictStream(events)
        provider = OllamaProvider()
        provider._client = _RecordingOllamaClient(stream)
        response = provider.chat_stream(MESSAGES, lambda _d: None)
        assert response.stop_reason == "length"
        assert stream.closed


_PROVIDER_CASES = {
    "anthropic": ("anthropic/text", "to_dict"),
    "openai": ("openai/text-and-a-usage-chunk", "to_dict"),
    "ollama": ("ollama/thinking-text-and-counts", "model_dump"),
}


def _over(kind: str, stream):
    """A provider of *kind* whose SDK client hands back *stream*."""
    if kind == "anthropic":
        provider = AnthropicProvider(api_key="test-key")
        provider._client = MagicMock()
        provider._client.messages.create.return_value = stream
    elif kind == "openai":
        provider = OpenAIProvider(api_key="test-key")
        provider._client = MagicMock()
        provider._client.chat.completions.create.return_value = stream
    else:
        provider = OllamaProvider()
        provider._client = _RecordingOllamaClient(stream)
    return provider


class _OverloadedError(Exception):
    pass


class _BreakingStream(_FakeStream):
    """Delivers two events, then the SDK raises."""

    def __iter__(self):
        yield from list(super().__iter__())[:2]
        raise _OverloadedError("Overloaded")


class _CloseFails(_FakeStream):
    def close(self) -> None:
        self.closed = True
        raise OSError("close failed")


@pytest.mark.parametrize("kind", list(_PROVIDER_CASES))
class TestEveryProviderWrapsOnlyItsSDK:
    def _stream(self, kind: str, cls=_FakeStream):
        name, dump = _PROVIDER_CASES[kind]
        return cls(_events(name), dump=dump)

    def test_an_sdk_failure_mid_stream_is_a_stream_error(self, kind):
        stream = self._stream(kind, _BreakingStream)
        seen: list[StreamDelta] = []
        with pytest.raises(StreamError, match="the stream failed: Overloaded") as caught:
            _over(kind, stream).chat_stream(MESSAGES, seen.append)
        assert isinstance(caught.value.__cause__, _OverloadedError)
        assert stream.closed

    def test_an_exception_from_on_delta_is_not_wrapped(self, kind):
        stream = self._stream(kind)

        def on_delta(_delta):
            raise ValueError("the caller's own")

        with pytest.raises(ValueError, match="the caller's own"):
            _over(kind, stream).chat_stream(MESSAGES, on_delta)
        assert stream.closed

    def test_a_close_that_fails_does_not_hide_the_failure(self, kind):
        stream = self._stream(kind, _CloseFails)

        def on_delta(_delta):
            raise ValueError("the caller's own")

        with pytest.raises(ValueError, match="the caller's own"):
            _over(kind, stream).chat_stream(MESSAGES, on_delta)
        assert stream.closed

    def test_a_close_that_fails_after_a_whole_answer_is_reported(self, kind):
        with pytest.raises(OSError, match="close failed"):
            _over(kind, self._stream(kind, _CloseFails)).chat_stream(MESSAGES, lambda _d: None)


@pytest.mark.parametrize("kind", ["anthropic", "openai"])
def test_a_failure_in_bmlibs_own_dump_is_not_a_stream_error(kind):
    stream = _FakeStream(_events(_PROVIDER_CASES[kind][0]), dump="not_to_dict")
    with pytest.raises(AttributeError, match="to_dict"):
        _over(kind, stream).chat_stream(MESSAGES, lambda _d: None)


class TestOllamaOpensAsChatDoes:
    """ollama-python sends a streamed request only on the first ``next()``."""

    @staticmethod
    def _lazy(exc: Exception):
        def stream():
            raise exc
            yield  # pragma: no cover - makes this a generator

        return stream()

    def test_a_missing_model_raises_what_chat_raises(self):
        ollama = pytest.importorskip("ollama")

        provider = OllamaProvider()
        provider._client = _RecordingOllamaClient(
            self._lazy(ollama.ResponseError("model 'x' not found", 404))
        )
        with pytest.raises(ollama.ResponseError, match="not found") as caught:
            provider.chat_stream(MESSAGES, lambda _d: None)
        assert not isinstance(caught.value, StreamError)

    def test_a_server_that_is_not_running_is_a_connection_error(self):
        httpx = pytest.importorskip("httpx")

        provider = OllamaProvider()
        provider._client = _RecordingOllamaClient(
            self._lazy(httpx.ConnectError("connection refused"))
        )
        with pytest.raises(ConnectionError, match="failed to connect to Ollama"):
            provider.chat_stream(MESSAGES, lambda _d: None)

    def test_an_empty_stream_is_incomplete(self):
        provider = OllamaProvider()
        provider._client = _RecordingOllamaClient(_FakeStream([], dump="model_dump"))
        with pytest.raises(StreamError, match="ended before done"):
            provider.chat_stream(MESSAGES, lambda _d: None)


class TestUsageAskedForAndNotSent:
    _LOGGER = "bmlib.llm.providers.openai_compat"
    _NO_USAGE = "openai/both-reasoning-fields-are-read-once"

    def test_is_a_warning(self, caplog):
        provider = _over("openai", _FakeStream(_events(self._NO_USAGE)))
        with caplog.at_level("WARNING", logger=self._LOGGER):
            response = provider.chat_stream(MESSAGES, lambda _d: None, model="gpt-x")
        assert response.input_tokens == 0
        assert [r.getMessage() for r in caplog.records] == [
            "openai was asked for usage on a stream of gpt-x and sent none; recorded as 0 tokens"
        ]

    def test_a_provider_not_asked_is_not_warned_about(self, caplog):
        provider = MistralProvider(api_key="test-key")
        provider._client = MagicMock()
        provider._client.chat.completions.create.return_value = _FakeStream(_events(self._NO_USAGE))
        with caplog.at_level("WARNING", logger=self._LOGGER):
            provider.chat_stream(MESSAGES, lambda _d: None)
        assert caplog.records == []

    def test_a_stopped_stream_is_not_warned_about(self, caplog):
        provider = _over("openai", _FakeStream(_events(self._NO_USAGE)))
        with caplog.at_level("WARNING", logger=self._LOGGER):
            provider.chat_stream(MESSAGES, lambda _d: False)
        assert caplog.records == []

    def test_usage_that_arrives_is_not_warned_about(self, caplog):
        provider = _over("openai", _FakeStream(_events("openai/text-and-a-usage-chunk")))
        with caplog.at_level("WARNING", logger=self._LOGGER):
            provider.chat_stream(MESSAGES, lambda _d: None)
        assert caplog.records == []


class TestSupportsStreamingSaysWhatTheProviderDoes:
    """Every metadata entry a provider hands out says whether it streams."""

    @staticmethod
    def _streams(cls) -> bool:
        return cls.chat_stream is not BaseProvider.chat_stream

    @pytest.mark.parametrize(
        "cls", [OpenAIProvider, MistralProvider, DeepSeekProvider, GeminiProvider]
    )
    def test_every_fallback_model(self, cls):
        assert self._streams(cls)
        assert [m.capabilities.supports_streaming for m in cls.FALLBACK_MODELS] == [True] * len(
            cls.FALLBACK_MODELS
        )

    def test_anthropics_models_when_listing_fails(self):
        provider = AnthropicProvider(api_key="test-key")
        provider._client = MagicMock()
        provider._client.models.list.side_effect = RuntimeError("down")
        models = provider.list_models(force_refresh=True)
        assert models
        assert all(m.capabilities.supports_streaming for m in models)

    def test_ollamas_model_when_show_fails(self):
        provider = OllamaProvider()
        provider._client = MagicMock()
        provider._client.show.side_effect = RuntimeError("down")
        metadata = provider.get_model_metadata("llama3.2")
        assert metadata is not None
        assert metadata.capabilities.supports_streaming

    def test_a_provider_that_cannot_stream_does_not_claim_to(self):
        assert not self._streams(_NonStreamingProvider)


# ---------------------------------------------------------------------------
# LLMClient
# ---------------------------------------------------------------------------


class _NonStreamingProvider(BaseProvider):
    """A third-party provider written before streaming existed."""

    PROVIDER_NAME = "legacy"
    is_local = True
    is_free = True
    requires_api_key = False
    default_base_url = ""
    default_model = "m"

    def chat(self, messages, model=None, temperature=0.7, max_tokens=4096, **kwargs):
        raise AssertionError("chat_stream must not fall back to chat")

    def list_models(self, force_refresh=False):
        return []

    def test_connection(self):
        return True, ""

    def count_tokens(self, text, model=None):
        return 0


class TestLLMClient:
    def _client_over(self, provider) -> LLMClient:
        client = LLMClient()
        client._providers[provider.PROVIDER_NAME] = provider
        return client

    def test_routes_streams_and_records_usage(self):
        reset_token_tracker()
        stream = _FakeStream(_events("anthropic/text"))
        provider = AnthropicProvider(api_key="test-key")
        provider._client = MagicMock()
        provider._client.messages.create.return_value = stream
        seen: list[StreamDelta] = []
        response = self._client_over(provider).chat_stream(
            MESSAGES, seen.append, model="anthropic:claude-x"
        )
        assert "".join(d.text for d in seen) == response.content == "The trial was blinded."
        summary = get_token_tracker().get_summary()
        assert summary.total_input_tokens == 12
        assert summary.total_output_tokens == 7
        reset_token_tracker()

    def test_a_stopped_stream_records_the_usage_sent_so_far(self):
        reset_token_tracker()
        provider = _over("anthropic", _FakeStream(_events("anthropic/text")))
        response = self._client_over(provider).chat_stream(
            MESSAGES, lambda _d: False, model="anthropic:claude-x"
        )
        assert response.stop_reason == CANCELLED_STOP_REASON
        summary = get_token_tracker().get_summary()
        assert (summary.total_input_tokens, summary.total_output_tokens) == (12, 1)
        reset_token_tracker()

    def test_tools_are_refused_not_dropped(self):
        from bmlib.llm import LLMToolDefinition

        provider = AnthropicProvider(api_key="test-key")
        provider._client = MagicMock()
        with pytest.raises(NotImplementedError, match="tool calls"):
            self._client_over(provider).chat_stream(
                MESSAGES,
                lambda _d: None,
                model="anthropic:claude-x",
                tools=[LLMToolDefinition(name="f", description="", parameters={})],
            )
        provider._client.messages.create.assert_not_called()

    def test_a_provider_that_cannot_stream_says_so(self):
        with pytest.raises(NotImplementedError, match="legacy does not support streaming"):
            self._client_over(_NonStreamingProvider()).chat_stream(
                MESSAGES, lambda _d: None, model="legacy:m"
            )
