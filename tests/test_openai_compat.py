# bmlib — shared library for biomedical literature tools
# Copyright (C) 2024-2026 Dr Horst Herb
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""Tests for OpenAI-compatible base provider."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from bmlib.llm.data_types import LLMMessage
from bmlib.llm.providers.base import ModelMetadata, ModelPricing, ProviderCapabilities


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
