# bmlib — shared library for biomedical literature tools
# Copyright (C) 2024-2026 Dr Horst Herb
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""Tests for thinking/reasoning support across the LLM abstraction.

The canonical ``think`` kwarg travels through ``LLMClient.chat(**kwargs)``
to each provider, which maps it to its native parameter. Reasoning output
comes back in the new optional ``LLMResponse.thinking`` field. All tests
use mocked SDK clients — no network access.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from bmlib.llm.data_types import LLMMessage, LLMResponse
from bmlib.llm.providers.base import ModelMetadata, ModelPricing

# ---------------------------------------------------------------------------
# LLMResponse dataclass
# ---------------------------------------------------------------------------


class TestLLMResponseThinkingField:
    def test_thinking_defaults_to_none(self):
        resp = LLMResponse(content="hi")
        assert resp.thinking is None

    def test_positional_construction_unchanged(self):
        # The pre-existing eight positional slots must keep their meaning.
        resp = LLMResponse("text", "model-x", 1, 2, 0, "stop", 0.5, None)
        assert resp.content == "text"
        assert resp.model == "model-x"
        assert resp.input_tokens == 1
        assert resp.output_tokens == 2
        assert resp.total_tokens == 3
        assert resp.stop_reason == "stop"
        assert resp.duration_seconds == 0.5
        assert resp.tool_calls is None
        assert resp.thinking is None

    def test_thinking_keyword(self):
        resp = LLMResponse(content="answer", thinking="chain of thought")
        assert resp.thinking == "chain of thought"


# ---------------------------------------------------------------------------
# Ollama provider
# ---------------------------------------------------------------------------


class _RecordingOllamaClient:
    """Fake ollama client that records the chat() kwargs it was called with."""

    def __init__(self, response):
        self._response = response
        self.last_kwargs: dict = {}

    def chat(self, **kwargs):
        self.last_kwargs = kwargs
        return self._response


def _ollama_provider(response):
    from bmlib.llm.providers.ollama import OllamaProvider

    provider = OllamaProvider()
    provider._client = _RecordingOllamaClient(response)
    return provider


class TestOllamaThinking:
    def test_extracts_thinking_from_dict_response(self):
        response = {
            "message": {"content": "final answer", "thinking": "step by step"},
            "prompt_eval_count": 3,
            "eval_count": 4,
        }
        provider = _ollama_provider(response)
        resp = provider.chat([LLMMessage(role="user", content="q")], think=True)
        assert resp.thinking == "step by step"
        assert resp.content == "final answer"

    def test_extracts_thinking_from_object_response(self):
        # ollama >= 0.4 returns pydantic models (attribute access, no .get()).
        response = SimpleNamespace(
            message=SimpleNamespace(content="answer", thinking="pondering"),
            prompt_eval_count=1,
            eval_count=1,
            done_reason="stop",
        )
        provider = _ollama_provider(response)
        resp = provider.chat([LLMMessage(role="user", content="q")], think=True)
        assert resp.thinking == "pondering"

    def test_no_thinking_field_yields_none(self):
        response = {"message": {"content": "plain"}}
        provider = _ollama_provider(response)
        resp = provider.chat([LLMMessage(role="user", content="q")])
        assert resp.thinking is None

    def test_empty_thinking_yields_none(self):
        response = {"message": {"content": "plain", "thinking": ""}}
        provider = _ollama_provider(response)
        resp = provider.chat([LLMMessage(role="user", content="q")], think=True)
        assert resp.thinking is None

    def test_think_true_forwarded(self):
        provider = _ollama_provider({"message": {"content": "x"}})
        provider.chat([LLMMessage(role="user", content="q")], think=True)
        assert provider._client.last_kwargs["think"] is True

    def test_think_false_forwarded(self):
        # Explicit disable must reach the server (models that think by default).
        provider = _ollama_provider({"message": {"content": "x"}})
        provider.chat([LLMMessage(role="user", content="q")], think=False)
        assert provider._client.last_kwargs["think"] is False

    def test_think_effort_string_forwarded(self):
        # gpt-oss models accept "low"/"medium"/"high" effort levels.
        provider = _ollama_provider({"message": {"content": "x"}})
        provider.chat([LLMMessage(role="user", content="q")], think="high")
        assert provider._client.last_kwargs["think"] == "high"

    def test_think_int_budget_coerced_to_true(self):
        # Ollama has no token-budget concept; a positive int means "on".
        provider = _ollama_provider({"message": {"content": "x"}})
        provider.chat([LLMMessage(role="user", content="q")], think=4096)
        assert provider._client.last_kwargs["think"] is True

    def test_think_zero_budget_coerced_to_false(self):
        # A falsy int must not switch thinking ON — it degrades by
        # truthiness, consistent with the other providers.
        provider = _ollama_provider({"message": {"content": "x"}})
        provider.chat([LLMMessage(role="user", content="q")], think=0)
        assert provider._client.last_kwargs["think"] is False

    def test_absent_think_not_sent(self):
        provider = _ollama_provider({"message": {"content": "x"}})
        provider.chat([LLMMessage(role="user", content="q")])
        assert "think" not in provider._client.last_kwargs


# ---------------------------------------------------------------------------
# Anthropic provider
# ---------------------------------------------------------------------------


def _anthropic_provider(blocks=None, stop_reason="end_turn"):
    from bmlib.llm.providers.anthropic import AnthropicProvider

    provider = AnthropicProvider(api_key="test-key")
    response = SimpleNamespace(
        content=blocks if blocks is not None else [SimpleNamespace(type="text", text="ok")],
        usage=SimpleNamespace(input_tokens=3, output_tokens=4),
        stop_reason=stop_reason,
    )
    mock_client = MagicMock()
    mock_client.messages.create.return_value = response
    provider._client = mock_client
    return provider


def _anthropic_request(provider):
    return provider._client.messages.create.call_args.kwargs


class TestAnthropicThinkingRequest:
    def test_think_true_enables_thinking(self):
        provider = _anthropic_provider()
        provider.chat([LLMMessage(role="user", content="q")], max_tokens=32_000, think=True)
        req = _anthropic_request(provider)
        assert req["thinking"] == {"type": "enabled", "budget_tokens": 8192}

    def test_think_int_sets_budget(self):
        provider = _anthropic_provider()
        provider.chat([LLMMessage(role="user", content="q")], max_tokens=32_000, think=4096)
        req = _anthropic_request(provider)
        assert req["thinking"]["budget_tokens"] == 4096

    def test_effort_levels_map_to_budgets(self):
        for level, budget in (("low", 2048), ("medium", 8192), ("high", 16384)):
            provider = _anthropic_provider()
            provider.chat([LLMMessage(role="user", content="q")], max_tokens=64_000, think=level)
            req = _anthropic_request(provider)
            assert req["thinking"]["budget_tokens"] == budget, level

    def test_budget_clamped_below_max_tokens(self):
        provider = _anthropic_provider()
        provider.chat([LLMMessage(role="user", content="q")], max_tokens=2000, think=50_000)
        req = _anthropic_request(provider)
        assert req["thinking"]["budget_tokens"] == 1999

    def test_small_max_tokens_raises(self):
        provider = _anthropic_provider()
        with pytest.raises(ValueError, match="max_tokens"):
            provider.chat([LLMMessage(role="user", content="q")], max_tokens=1024, think=True)

    def test_unknown_effort_level_raises(self):
        provider = _anthropic_provider()
        with pytest.raises(ValueError, match="think"):
            provider.chat([LLMMessage(role="user", content="q")], max_tokens=8000, think="turbo")

    def test_negative_budget_raises(self):
        # A negative budget is a caller bug — fail loudly instead of
        # silently clamping it up to the API minimum.
        provider = _anthropic_provider()
        with pytest.raises(ValueError, match="egative"):
            provider.chat([LLMMessage(role="user", content="q")], max_tokens=8000, think=-100)

    def test_temperature_and_top_p_omitted_when_thinking(self):
        # The API rejects non-default sampling params with thinking enabled.
        provider = _anthropic_provider()
        provider.chat(
            [LLMMessage(role="user", content="q")],
            temperature=0.2,
            max_tokens=8000,
            top_p=0.9,
            think=True,
        )
        req = _anthropic_request(provider)
        assert "temperature" not in req
        assert "top_p" not in req

    def test_no_think_request_unchanged(self):
        provider = _anthropic_provider()
        provider.chat([LLMMessage(role="user", content="q")], temperature=0.2)
        req = _anthropic_request(provider)
        assert "thinking" not in req
        assert req["temperature"] == 0.2

    def test_think_false_request_unchanged(self):
        provider = _anthropic_provider()
        provider.chat([LLMMessage(role="user", content="q")], think=False)
        req = _anthropic_request(provider)
        assert "thinking" not in req
        assert "temperature" in req


class TestAnthropicThinkingResponse:
    def test_thinking_blocks_extracted(self):
        blocks = [
            SimpleNamespace(type="thinking", thinking="let me reason", signature="sig"),
            SimpleNamespace(type="text", text="the answer"),
        ]
        provider = _anthropic_provider(blocks)
        resp = provider.chat([LLMMessage(role="user", content="q")], max_tokens=8000, think=True)
        assert resp.thinking == "let me reason"
        assert resp.content == "the answer"

    def test_multiple_thinking_blocks_joined(self):
        blocks = [
            SimpleNamespace(type="thinking", thinking="part one", signature="s1"),
            SimpleNamespace(type="thinking", thinking="part two", signature="s2"),
            SimpleNamespace(type="text", text="answer"),
        ]
        provider = _anthropic_provider(blocks)
        resp = provider.chat([LLMMessage(role="user", content="q")], max_tokens=8000, think=True)
        assert resp.thinking == "part one\n\npart two"

    def test_redacted_thinking_skipped(self):
        blocks = [
            SimpleNamespace(type="redacted_thinking", data="opaque-bytes"),
            SimpleNamespace(type="text", text="answer"),
        ]
        provider = _anthropic_provider(blocks)
        resp = provider.chat([LLMMessage(role="user", content="q")], max_tokens=8000, think=True)
        assert resp.thinking is None
        assert resp.content == "answer"

    def test_no_thinking_blocks_yields_none(self):
        provider = _anthropic_provider()
        resp = provider.chat([LLMMessage(role="user", content="q")])
        assert resp.thinking is None


# ---------------------------------------------------------------------------
# OpenAI-compatible base provider
# ---------------------------------------------------------------------------


class _StubProvider:
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
    MODEL_PRICING = {"stub-model": ModelPricing(1.0, 2.0)}


def _compat_provider(message=None, reasoning_model=False):
    from bmlib.llm.providers.openai_compat import OpenAICompatibleProvider

    class _Impl(_StubProvider, OpenAICompatibleProvider):
        def _is_reasoning_model(self, model):
            return reasoning_model

    provider = _Impl(api_key="k")
    if message is None:
        message = SimpleNamespace(content="plain answer")
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=2, completion_tokens=3),
    )
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = response
    provider._client = mock_client
    return provider


def _compat_request(provider):
    return provider._client.chat.completions.create.call_args.kwargs


class TestOpenAICompatThinkingResponse:
    def test_reasoning_content_extracted(self):
        # DeepSeek / vLLM / SGLang style separated reasoning field.
        message = SimpleNamespace(content="answer", reasoning_content="deep thought")
        provider = _compat_provider(message)
        resp = provider.chat([LLMMessage(role="user", content="q")])
        assert resp.thinking == "deep thought"
        assert resp.content == "answer"

    def test_reasoning_attr_extracted(self):
        # OpenRouter-style field name.
        message = SimpleNamespace(content="answer", reasoning="quick thought")
        provider = _compat_provider(message)
        resp = provider.chat([LLMMessage(role="user", content="q")])
        assert resp.thinking == "quick thought"

    def test_no_reasoning_field_yields_none(self):
        provider = _compat_provider(SimpleNamespace(content="answer"))
        resp = provider.chat([LLMMessage(role="user", content="q")])
        assert resp.thinking is None

    def test_think_tags_split_when_think_requested(self):
        message = SimpleNamespace(content="<think>hmm, let me see</think>\nthe answer")
        provider = _compat_provider(message)
        resp = provider.chat([LLMMessage(role="user", content="q")], think=True)
        assert resp.thinking == "hmm, let me see"
        assert resp.content == "the answer"

    def test_think_tags_untouched_without_think(self):
        # Backwards compatibility: content is never rewritten unless the
        # caller opted in by passing a truthy think.
        raw = "<think>hmm</think>answer"
        provider = _compat_provider(SimpleNamespace(content=raw))
        resp = provider.chat([LLMMessage(role="user", content="q")])
        assert resp.content == raw
        assert resp.thinking is None

    def test_empty_think_block_yields_none_thinking(self):
        # An empty <think></think> block is stripped from content but
        # must not produce an empty-string thinking value.
        message = SimpleNamespace(content="<think></think>answer")
        provider = _compat_provider(message)
        resp = provider.chat([LLMMessage(role="user", content="q")], think=True)
        assert resp.thinking is None
        assert resp.content == "answer"

    def test_explicit_reasoning_field_wins_over_think_tags(self):
        message = SimpleNamespace(
            content="<think>inline</think>answer",
            reasoning_content="separated",
        )
        provider = _compat_provider(message)
        resp = provider.chat([LLMMessage(role="user", content="q")], think=True)
        assert resp.thinking == "separated"
        # Content is left alone when a separated field is present.
        assert resp.content == "<think>inline</think>answer"


class TestOpenAICompatThinkingRequest:
    def test_reasoning_effort_sent_for_reasoning_model(self):
        provider = _compat_provider(reasoning_model=True)
        provider.chat([LLMMessage(role="user", content="q")], think="high")
        req = _compat_request(provider)
        assert req["reasoning_effort"] == "high"
        assert "think" not in req

    def test_think_not_sent_for_non_reasoning_model(self):
        provider = _compat_provider()
        provider.chat([LLMMessage(role="user", content="q")], think=True)
        req = _compat_request(provider)
        assert "think" not in req
        assert "reasoning_effort" not in req

    def test_bool_think_not_sent_as_reasoning_effort(self):
        provider = _compat_provider(reasoning_model=True)
        provider.chat([LLMMessage(role="user", content="q")], think=True)
        req = _compat_request(provider)
        assert "reasoning_effort" not in req

    def test_effort_string_not_sent_for_non_reasoning_model(self):
        # reasoning_effort is gated on the model, not just the value type.
        provider = _compat_provider(reasoning_model=False)
        provider.chat([LLMMessage(role="user", content="q")], think="high")
        req = _compat_request(provider)
        assert "reasoning_effort" not in req
        assert "think" not in req

    def test_extra_body_forwarded(self):
        provider = _compat_provider()
        extra = {"chat_template_kwargs": {"enable_thinking": True}}
        provider.chat([LLMMessage(role="user", content="q")], extra_body=extra)
        req = _compat_request(provider)
        assert req["extra_body"] == extra

    def test_extra_body_absent_by_default(self):
        provider = _compat_provider()
        provider.chat([LLMMessage(role="user", content="q")])
        assert "extra_body" not in _compat_request(provider)


# ---------------------------------------------------------------------------
# LLMClient end-to-end kwarg routing
# ---------------------------------------------------------------------------


class TestClientThinkPassthrough:
    def test_think_kwarg_reaches_provider_and_thinking_comes_back(self):
        from bmlib.llm.client import LLMClient

        class _FakeProvider:
            PROVIDER_NAME = "fake"

            def __init__(self):
                self.last_kwargs = {}

            def chat(self, messages, model=None, temperature=0.7, max_tokens=4096, **kwargs):
                self.last_kwargs = kwargs
                return LLMResponse(content="answer", thinking="trace")

            def calculate_cost(self, model, input_tokens, output_tokens):
                return 0.0

        client = LLMClient()
        fake = _FakeProvider()
        client._providers["fake"] = fake

        resp = client.chat(
            messages=[LLMMessage(role="user", content="q")],
            model="fake:some-model",
            think=True,
        )
        assert fake.last_kwargs["think"] is True
        assert resp.thinking == "trace"
