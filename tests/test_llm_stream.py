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

    def test_the_stream_is_closed_when_on_delta_raises(self):
        stream = _FakeStream(_events("anthropic/text"))

        def on_delta(_delta):
            raise KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            self._provider(stream).chat_stream(MESSAGES, on_delta)
        assert stream.closed


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
