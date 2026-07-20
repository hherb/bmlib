# bmlib — shared library for biomedical literature tools
# Copyright (C) 2024-2026 Dr Horst Herb
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""Tests for embedding support (single and batch).

Three layers of tests:

1. Data type tests — construction of BatchEmbeddingResponse and
   backwards compatibility of EmbeddingResponse.
2. OllamaProvider tests with a mocked SDK client — verify embed() and
   embed_batch() both go through the batch-capable ``client.embed()``
   endpoint, and that a batch of N texts costs exactly one API call
   (the performance contract that motivated the feature).
3. LLMClient routing tests — verify embed_batch() routes on the
   ``"provider:model"`` string and that providers without embedding
   support raise NotImplementedError.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from bmlib.llm import (
    BatchEmbeddingResponse,
    EmbeddingResponse,
    LLMClient,
)
from bmlib.llm.providers.base import BaseProvider
from bmlib.llm.providers.ollama import OllamaProvider

# ===========================================================================
# 1. DATA TYPE TESTS
# ===========================================================================


class TestBatchEmbeddingResponse:
    def test_construction(self):
        resp = BatchEmbeddingResponse(
            embeddings=[[0.1, 0.2], [0.3, 0.4]],
            model="nomic-embed-text",
            dimensions=2,
            input_tokens=12,
        )
        assert resp.embeddings == [[0.1, 0.2], [0.3, 0.4]]
        assert resp.model == "nomic-embed-text"
        assert resp.dimensions == 2
        assert resp.input_tokens == 12

    def test_defaults(self):
        resp = BatchEmbeddingResponse(embeddings=[])
        assert resp.embeddings == []
        assert resp.model == ""
        assert resp.dimensions == 0
        assert resp.input_tokens == 0

    def test_embedding_response_unchanged(self):
        # Backwards compatibility: the single-embedding response type
        # keeps its original shape.
        resp = EmbeddingResponse(embedding=[0.5, 0.5], model="m", dimensions=2)
        assert resp.embedding == [0.5, 0.5]
        assert resp.dimensions == 2


# ===========================================================================
# 2. OLLAMA PROVIDER TESTS (mocked SDK client)
# ===========================================================================


def _mock_embed_response(embeddings: list[list[float]], tokens: int = 0) -> MagicMock:
    """Build a mock of the ollama ``embed()`` response (Pydantic-shaped)."""
    response = MagicMock(spec=["embeddings", "prompt_eval_count"])
    response.embeddings = embeddings
    response.prompt_eval_count = tokens
    return response


class TestOllamaEmbedBatch:
    def test_batch_returns_vectors_in_order(self):
        p = OllamaProvider()
        p._client = MagicMock()
        p._client.embed.return_value = _mock_embed_response(
            [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]], tokens=7
        )

        resp = p.embed_batch(["first chunk", "second chunk"], model="nomic-embed-text")

        assert isinstance(resp, BatchEmbeddingResponse)
        assert resp.embeddings == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
        assert resp.model == "nomic-embed-text"
        assert resp.dimensions == 3
        assert resp.input_tokens == 7

    def test_batch_is_a_single_api_call(self):
        # The whole point of the feature: N texts must not cost N calls.
        p = OllamaProvider()
        p._client = MagicMock()
        texts = [f"chunk {i}" for i in range(32)]
        p._client.embed.return_value = _mock_embed_response([[0.0, 1.0]] * 32)

        p.embed_batch(texts, model="nomic-embed-text")

        assert p._client.embed.call_count == 1
        p._client.embed.assert_called_once_with(model="nomic-embed-text", input=texts)
        p._client.embeddings.assert_not_called()

    def test_empty_batch_makes_no_api_call(self):
        p = OllamaProvider()
        p._client = MagicMock()

        resp = p.embed_batch([], model="nomic-embed-text")

        assert resp.embeddings == []
        assert resp.dimensions == 0
        p._client.embed.assert_not_called()

    def test_count_mismatch_raises_value_error(self):
        p = OllamaProvider()
        p._client = MagicMock()
        p._client.embed.return_value = _mock_embed_response([[0.1, 0.2]])

        with pytest.raises(ValueError, match="2 .*1|1 .*2"):
            p.embed_batch(["a", "b"], model="nomic-embed-text")

    def test_api_failure_raises_connection_error(self):
        p = OllamaProvider()
        p._client = MagicMock()
        p._client.embed.side_effect = RuntimeError("server down")

        with pytest.raises(ConnectionError, match="server down"):
            p.embed_batch(["a"], model="nomic-embed-text")

    def test_dict_shaped_response(self):
        # ollama < 0.4 returned plain dicts rather than Pydantic models.
        p = OllamaProvider()
        p._client = MagicMock()
        p._client.embed.return_value = {
            "embeddings": [[1.0, 0.0]],
            "prompt_eval_count": 3,
        }

        resp = p.embed_batch(["a"], model="nomic-embed-text")

        assert resp.embeddings == [[1.0, 0.0]]
        assert resp.input_tokens == 3


class TestOllamaSingleEmbed:
    def test_single_embed_uses_batch_endpoint(self):
        # embed() must route through the modern /api/embed endpoint,
        # not the deprecated client.embeddings().
        p = OllamaProvider()
        p._client = MagicMock()
        p._client.embed.return_value = _mock_embed_response([[0.6, 0.8]], tokens=4)

        resp = p.embed("some text", model="nomic-embed-text")

        assert isinstance(resp, EmbeddingResponse)
        assert resp.embedding == [0.6, 0.8]
        assert resp.model == "nomic-embed-text"
        assert resp.dimensions == 2
        assert resp.input_tokens == 4
        p._client.embed.assert_called_once_with(model="nomic-embed-text", input=["some text"])
        p._client.embeddings.assert_not_called()

    def test_single_embed_matches_batch_of_one(self):
        p = OllamaProvider()
        p._client = MagicMock()
        p._client.embed.return_value = _mock_embed_response([[0.6, 0.8]])

        single = p.embed("t", model="m")
        batch = p.embed_batch(["t"], model="m")

        assert single.embedding == batch.embeddings[0]

    def test_single_embed_empty_response_raises(self):
        # A 200 response with no vectors is a protocol violation, not a
        # valid empty embedding — fail loud instead of returning [].
        p = OllamaProvider()
        p._client = MagicMock()
        p._client.embed.return_value = _mock_embed_response([])

        with pytest.raises(ValueError):
            p.embed("some text", model="nomic-embed-text")


# ===========================================================================
# 3. CLIENT ROUTING / BASE PROVIDER TESTS
# ===========================================================================


class _NoEmbeddingProvider(BaseProvider):
    """Minimal concrete provider that does not override embed_batch()."""

    PROVIDER_NAME = "dummy"
    DISPLAY_NAME = "Dummy"
    DESCRIPTION = "test stub"
    WEBSITE_URL = ""
    SETUP_INSTRUCTIONS = ""

    @property
    def is_local(self) -> bool:
        return True

    @property
    def is_free(self) -> bool:
        return True

    @property
    def requires_api_key(self) -> bool:
        return False

    @property
    def default_base_url(self) -> str:
        return ""

    @property
    def default_model(self) -> str:
        return "dummy-model"

    def chat(self, messages, model=None, temperature=0.7, max_tokens=4096, **kwargs):
        raise NotImplementedError

    def list_models(self, force_refresh=False):
        return []

    def test_connection(self):
        return True, "ok"

    def count_tokens(self, text, model=None):
        return 0


class TestBaseProviderDefaults:
    def test_embed_batch_default_raises(self):
        p = _NoEmbeddingProvider()
        with pytest.raises(NotImplementedError, match="dummy"):
            p.embed_batch(["a", "b"])


class TestLLMClientEmbedBatch:
    def test_routes_on_model_string(self):
        client = LLMClient(default_provider="ollama")
        provider = MagicMock()
        provider.embed_batch.return_value = BatchEmbeddingResponse(
            embeddings=[[0.1]], model="nomic-embed-text", dimensions=1
        )
        client._providers["ollama"] = provider

        resp = client.embed_batch(["a"], model="ollama:nomic-embed-text")

        assert resp.embeddings == [[0.1]]
        provider.embed_batch.assert_called_once_with(texts=["a"], model="nomic-embed-text")

    def test_unsupported_provider_raises(self):
        client = LLMClient(default_provider="dummy")
        client._providers["dummy"] = _NoEmbeddingProvider()

        with pytest.raises(NotImplementedError):
            client.embed_batch(["a"], model="dummy:whatever")
