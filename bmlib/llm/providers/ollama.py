# bmlib — shared library for biomedical literature tools
# Copyright (C) 2024-2026 Dr Horst Herb
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Ollama local model provider — native API.

Uses the ``ollama`` Python package which talks to the Ollama server's
native REST API (not the OpenAI-compatible endpoint).  This gives access
to Ollama-specific features such as model discovery via ``ollama.show()``
and native parameters (e.g. thinking mode toggles) that are not reliably
exposed through the OpenAI compatibility layer.
"""

from __future__ import annotations

import logging
import os
import re
import time
from collections.abc import Callable
from functools import partial
from typing import Any

from bmlib.llm.data_types import (
    BatchEmbeddingResponse,
    EmbeddingResponse,
    LLMMessage,
    LLMResponse,
    LLMToolCall,
    LLMToolDefinition,
)
from bmlib.llm.providers.base import (
    BaseProvider,
    ModelMetadata,
    ModelPricing,
    ProviderCapabilities,
)

logger = logging.getLogger(__name__)

# Rough chars-per-token ratio for fallback estimation
CHARS_PER_TOKEN_ESTIMATE = 4

# Default context window when model metadata is unavailable (tokens)
FALLBACK_CONTEXT_WINDOW = 8192

# Sentinel written into the lazy context-window fields at construction time.
# ``ModelMetadata`` and ``ProviderCapabilities`` are dataclasses, so their
# generated ``__init__`` always assigns these fields — the lazy subclasses
# cannot tell "caller supplied nothing" from "caller supplied a real value"
# without a sentinel distinct from any legitimate context window.
_UNRESOLVED = -1

# Seconds a cached model list stays fresh.
#
# Deliberately far shorter than the 3600 used by anthropic.py and
# openai_compat.py (each module declares its own; the constant is not
# shared).  The expensive call was never ``list()`` — that is ~47ms
# against localhost — it was ``show()``, and those results live in
# ``_model_info_cache``, which survives this TTL entirely.  So this cache
# exists only to absorb bursts of repeated calls, and buying that with an
# hour of staleness is a bad trade for a local server: an ``ollama pull``
# would leave the new model invisible for an hour.
CACHE_TTL_SECONDS = 60

# Pricing for local models (always free)
_FREE_PRICING = ModelPricing(0.0, 0.0)

# Texts per /api/embed request when the caller does not specify.  Bounds
# request size and server-side memory for large corpora; callers wanting a
# single round-trip regardless of size can pass ``max_batch_size=len(texts)``.
DEFAULT_EMBED_BATCH_SIZE = 256


def _safe_get(obj: Any, key: str, default: Any = None) -> Any:
    """Extract a field from a dict or Pydantic-model response.

    The ``ollama`` SDK (>=0.4) returns Pydantic models with subscript
    access but without ``.get()``.  Older versions returned plain dicts.
    This helper handles both transparently.
    """
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


class _LazyOllamaCapabilities(ProviderCapabilities):
    """:class:`ProviderCapabilities` whose ``max_context_window`` is lazy.

    Every other capability flag is derived from Ollama's ``/api/tags``
    payload and stays eager, so reading ``supports_vision`` or
    ``supports_function_calling`` costs nothing.  Only the context window
    requires a per-model ``show()`` call, and only that field defers.

    Args:
        resolver: Zero-argument callable returning the context window.
            Called at most once; the result is memoised.
        **kwargs: Forwarded to :class:`ProviderCapabilities`.  Passing
            ``max_context_window`` seeds the memo and prevents any fetch.
    """

    def __init__(self, resolver: Callable[[], int], **kwargs: Any) -> None:
        self._resolver = resolver
        self._resolved: int | None = None
        kwargs.setdefault("max_context_window", _UNRESOLVED)
        super().__init__(**kwargs)

    @property
    def max_context_window(self) -> int:
        """Context window in tokens, fetched on first read."""
        if self._resolved is None:
            self._resolved = self._resolver()
        return self._resolved

    @max_context_window.setter
    def max_context_window(self, value: int) -> None:
        self._resolved = None if value == _UNRESOLVED else value

    def __repr__(self) -> str:
        """Render without triggering a fetch (see class docstring)."""
        ctx = "<unresolved>" if self._resolved is None else self._resolved
        return (
            f"{type(self).__name__}("
            f"supports_streaming={self.supports_streaming!r}, "
            f"supports_function_calling={self.supports_function_calling!r}, "
            f"supports_vision={self.supports_vision!r}, "
            f"supports_system_messages={self.supports_system_messages!r}, "
            f"max_context_window={ctx})"
        )


class _LazyOllamaModelMetadata(ModelMetadata):
    """:class:`ModelMetadata` whose ``context_window`` is lazy.

    Returned by :meth:`OllamaProvider.list_models`.  ``model_id``,
    ``display_name``, ``pricing`` and the capability flags all come from
    ``/api/tags`` and are eager; ``context_window`` triggers one
    ``show()`` call on first read and memoises the result.

    ``__repr__`` deliberately does **not** resolve — otherwise logging a
    model list would silently fire one HTTP request per model, which is
    the exact cost this class exists to avoid.  ``__eq__`` is left as the
    dataclass default and *does* resolve, on the grounds that comparing
    two metadata objects means wanting their real values.

    Args:
        resolver: Zero-argument callable returning the context window.
            Called at most once; the result is memoised.
        **kwargs: Forwarded to :class:`ModelMetadata`.  Passing
            ``context_window`` seeds the memo and prevents any fetch.
    """

    def __init__(self, resolver: Callable[[], int], **kwargs: Any) -> None:
        self._resolver = resolver
        self._resolved: int | None = None
        kwargs.setdefault("context_window", _UNRESOLVED)
        super().__init__(**kwargs)

    @property
    def context_window(self) -> int:
        """Context window in tokens, fetched on first read."""
        if self._resolved is None:
            self._resolved = self._resolver()
        return self._resolved

    @context_window.setter
    def context_window(self, value: int) -> None:
        self._resolved = None if value == _UNRESOLVED else value

    def __repr__(self) -> str:
        """Render without triggering a fetch (see class docstring)."""
        ctx = "<unresolved>" if self._resolved is None else self._resolved
        return (
            f"{type(self).__name__}("
            f"model_id={self.model_id!r}, "
            f"display_name={self.display_name!r}, "
            f"context_window={ctx}, "
            f"pricing={self.pricing!r}, "
            f"capabilities={self.capabilities!r}, "
            f"is_deprecated={self.is_deprecated!r})"
        )


class OllamaProvider(BaseProvider):
    """Ollama local model provider (native API)."""

    PROVIDER_NAME = "ollama"
    DISPLAY_NAME = "Ollama"
    DESCRIPTION = "Local models via Ollama server (free)"
    WEBSITE_URL = "https://ollama.ai"
    SETUP_INSTRUCTIONS = "Install from ollama.ai, then run 'ollama pull <model>'"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        **kwargs: object,
    ) -> None:
        resolved_base_url = base_url or os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        super().__init__(api_key=None, base_url=resolved_base_url, **kwargs)
        self._model_info_cache: dict[str, ModelMetadata] = {}
        self._models_cache: list[ModelMetadata] | None = None
        self._cache_timestamp: float = 0.0

    # --- Properties ---

    @property
    def is_local(self) -> bool:
        """Whether this provider runs locally."""
        return True

    @property
    def is_free(self) -> bool:
        """Whether this provider is free to use."""
        return True

    @property
    def requires_api_key(self) -> bool:
        """Whether an API key is required."""
        return False

    @property
    def default_base_url(self) -> str:
        """Default Ollama server URL."""
        return "http://localhost:11434"

    @property
    def default_model(self) -> str:
        """Default model to use when none is specified."""
        return "medgemma4B_it_q8"

    # --- Client ---

    def _get_client(self) -> Any:
        """Lazily initialise and return the ``ollama.Client``."""
        if self._client is None:
            try:
                import ollama

                self._client = ollama.Client(host=self._base_url)
            except ImportError:
                raise ImportError("ollama package not installed. Install with: pip install ollama")
        return self._client

    # --- Core operations ---

    def chat(
        self,
        messages: list[LLMMessage],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: object,
    ) -> LLMResponse:
        """Send a chat request to the local Ollama server.

        Args:
            messages: Conversation messages. Supports tool-result
                messages (``role="tool"``) and assistant messages with
                ``tool_calls`` for multi-turn tool conversations.
            model: Model identifier.
            temperature: Sampling temperature.
            max_tokens: Maximum tokens to generate.
            **kwargs: Extra options (``top_p``, ``json_mode``, ``think``,
                ``tools``, ``tool_choice``).
        """
        model = model or self.default_model
        client = self._get_client()

        top_p: float | None = kwargs.get("top_p")  # type: ignore[assignment]
        json_mode: bool = kwargs.get("json_mode", False)  # type: ignore[assignment]
        think: bool | str | int | None = kwargs.get("think")  # type: ignore[assignment]
        tools: list[LLMToolDefinition] | None = kwargs.get("tools")  # type: ignore[assignment]
        # Note: Ollama's native API does not yet expose a tool_choice
        # parameter the way OpenAI does. The model decides when to call
        # a tool based on the conversation. We accept the kwarg for API
        # symmetry with the other providers but do not forward it.
        _ = kwargs.get("tool_choice", "auto")

        ollama_messages = _convert_messages_to_ollama(messages)

        options: dict[str, object] = {
            "temperature": temperature,
            "num_predict": max_tokens,
        }
        if top_p is not None:
            options["top_p"] = top_p

        request_kwargs: dict[str, object] = {
            "model": model,
            "messages": ollama_messages,
            "options": options,
        }

        if json_mode:
            request_kwargs["format"] = "json"

        if think is not None:
            # Ollama accepts a bool or an effort-level string ("low"/
            # "medium"/"high", gpt-oss models). An int is the cross-provider
            # token-budget form (Anthropic) — Ollama has no budget concept,
            # so it degrades to on/off by truthiness (0 stays off).
            if isinstance(think, (bool, str)):
                request_kwargs["think"] = think
            else:
                request_kwargs["think"] = bool(think)

        # Tool calling: ollama-python (>=0.3) accepts an OpenAI-style
        # tools list directly. Convert our LLMToolDefinition into the
        # standard {"type": "function", "function": {...}} shape.
        if tools is not None:
            request_kwargs["tools"] = [_convert_tool_def_to_ollama(t) for t in tools]

        response = client.chat(**request_kwargs)

        # ollama >=0.4 returns Pydantic models; older versions return dicts.
        message = _safe_get(response, "message")
        content: str = _safe_get(message, "content", "") if message else ""

        # When thinking is enabled Ollama separates the reasoning trace
        # into message.thinking (content stays clean).
        raw_thinking = _safe_get(message, "thinking") if message else None
        thinking: str | None = (
            raw_thinking if isinstance(raw_thinking, str) and raw_thinking else None
        )

        # Parse tool calls from the response message. ollama-python
        # exposes them as message.tool_calls — a list of objects with
        # .function.name and .function.arguments (already parsed dict).
        tool_calls: list[LLMToolCall] = []
        if message:
            raw_calls = _safe_get(message, "tool_calls") or []
            for idx, raw in enumerate(raw_calls):
                # Ollama doesn't always provide an id; synthesise one
                # from the position so callers can correlate the
                # tool result back in the next turn.
                call_id = _safe_get(raw, "id") or f"call_{idx}"
                fn = _safe_get(raw, "function") or raw
                name = _safe_get(fn, "name") or ""
                args = _safe_get(fn, "arguments") or {}
                # Some Ollama models return arguments as a JSON string
                # rather than a parsed object — handle both.
                if isinstance(args, str):
                    try:
                        import json as _json

                        args = _json.loads(args)
                    except (ValueError, TypeError):
                        args = {"_raw": args}
                if not isinstance(args, dict):
                    args = {}
                tool_calls.append(LLMToolCall(id=str(call_id), name=str(name), arguments=args))

        # Use ``is None`` rather than truthiness: a real count of 0 (e.g. a
        # fully cached prompt, or an empty/tool-only completion) is valid and
        # must not be replaced by an estimate.
        prompt_eval_count = _safe_get(response, "prompt_eval_count")
        eval_count = _safe_get(response, "eval_count")
        input_tokens: int = (
            prompt_eval_count if prompt_eval_count is not None else self._estimate_tokens(messages)
        )
        output_tokens: int = (
            eval_count if eval_count is not None else len(content) // CHARS_PER_TOKEN_ESTIMATE
        )

        # Ollama reports done_reason="length" when generation hit num_predict
        # (the max_tokens ceiling) — surface it so callers can tell truncation
        # from a normal stop.
        done_reason = _safe_get(response, "done_reason")

        if tool_calls:
            stop_reason = "tool_calls"
        elif done_reason == "length":
            stop_reason = "length"
        else:
            stop_reason = "stop"

        return LLMResponse(
            content=content,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            stop_reason=stop_reason,
            tool_calls=tool_calls if tool_calls else None,
            thinking=thinking,
        )

    def embed(
        self,
        text: str,
        model: str | None = None,
        **kwargs: Any,
    ) -> EmbeddingResponse:
        """Generate an embedding vector for *text*.

        Delegates to :meth:`embed_batch` with a single-element batch, so
        single and batch embedding share one code path and one endpoint
        (``/api/embed``). That endpoint returns L2-normalised vectors —
        unlike the deprecated ``/api/embeddings`` endpoint this method
        used before, which returned raw vectors. Cosine similarity is
        unaffected; unnormalised dot-product or L2 comparisons against
        vectors stored from the old endpoint will differ in scale.

        Args:
            text: The text to embed.
            model: Embedding model identifier.  Defaults to
                :pyattr:`default_model` (not ideal for embeddings — callers
                should pass an embedding-specific model).
            **kwargs: Extra arguments forwarded verbatim to the ollama
                SDK's ``embed()`` (e.g. ``truncate``, ``options``,
                ``keep_alive``).  ``max_batch_size`` is accepted and
                ignored — a single text is always one request.

        Returns:
            An :class:`EmbeddingResponse` with the embedding vector.

        Raises:
            ConnectionError: If the request to the Ollama server fails.
            ValueError: If the server returns no vector (protocol violation).
        """
        # Dropped rather than forwarded: leaving it in **kwargs would
        # collide with embed_batch's own parameter of that name.
        kwargs.pop("max_batch_size", None)
        batch = self.embed_batch([text], model=model, **kwargs)
        embedding = batch.embeddings[0]
        return EmbeddingResponse(
            embedding=embedding,
            model=batch.model,
            dimensions=len(embedding),
            input_tokens=batch.input_tokens,
        )

    def embed_batch(
        self,
        texts: list[str],
        model: str | None = None,
        max_batch_size: int | None = None,
        **kwargs: object,
    ) -> BatchEmbeddingResponse:
        """Generate embedding vectors for *texts*, batched into few API calls.

        Sends texts to Ollama's ``/api/embed`` endpoint in groups of at
        most *max_batch_size* — for bulk workloads this is several times
        faster than looping :meth:`embed` (one HTTP request and one model
        load per group instead of per text).

        Batching is bounded rather than unlimited so that a large corpus
        does not become one enormous request: the default
        :pydata:`DEFAULT_EMBED_BATCH_SIZE` caps request size and
        server-side memory.  Pass ``max_batch_size=len(texts)`` to force a
        single round-trip regardless of size.

        This is **not** atomic across groups.  If a later group fails, the
        vectors already computed for earlier groups are discarded along
        with the raised exception; the caller must retry the whole batch.

        Args:
            texts: The texts to embed. An empty list returns an empty
                response without contacting the server.
            model: Embedding model identifier.  Defaults to
                :pyattr:`default_model` (not ideal for embeddings — callers
                should pass an embedding-specific model).
            max_batch_size: Maximum texts per request.  Defaults to
                :pydata:`DEFAULT_EMBED_BATCH_SIZE` when ``None``.
            **kwargs: Extra arguments forwarded verbatim to the ollama
                SDK's ``embed()`` (e.g. ``truncate``, ``options``,
                ``keep_alive``).

        Returns:
            A :class:`BatchEmbeddingResponse` with one vector per input
            text, in input order.  ``input_tokens`` is summed across all
            requests made.

        Raises:
            ConnectionError: If a request to the Ollama server fails.
            ValueError: If *max_batch_size* is less than 1, or if the
                server returns a different number of vectors than texts
                sent (protocol violation).
        """
        model = model or self.default_model
        if max_batch_size is not None and max_batch_size < 1:
            raise ValueError(f"max_batch_size must be >= 1, got {max_batch_size}")
        if not texts:
            return BatchEmbeddingResponse(embeddings=[], model=model)

        batch_size = max_batch_size or DEFAULT_EMBED_BATCH_SIZE
        client = self._get_client()

        embeddings: list[list[float]] = []
        input_tokens: int = 0

        for start in range(0, len(texts), batch_size):
            group = texts[start : start + batch_size]

            try:
                response = client.embed(model=model, input=group, **kwargs)
            except Exception as e:
                logger.error("Ollama embedding error: %s", e)
                raise ConnectionError(f"Ollama embedding failed: {e}") from e

            group_embeddings: list[list[float]] = _safe_get(response, "embeddings", []) or []
            if len(group_embeddings) != len(group):
                raise ValueError(
                    f"Ollama returned {len(group_embeddings)} embeddings "
                    f"for {len(group)} input texts"
                )
            embeddings.extend(group_embeddings)
            input_tokens += _safe_get(response, "prompt_eval_count", 0) or 0

        # texts is non-empty and every group's count was validated above,
        # so embeddings[0] is guaranteed to exist.
        return BatchEmbeddingResponse(
            embeddings=embeddings,
            model=model,
            dimensions=len(embeddings[0]),
            input_tokens=input_tokens,
        )

    # --- Model discovery (native API) ---

    def _metadata_from_tags_entry(self, name: str, entry: Any) -> ModelMetadata:
        """Build lazy metadata for one ``/api/tags`` entry.

        Every field except the context window is available in the tags
        payload, so only that field defers to a ``show()`` call.

        Ollama servers older than the capabilities feature omit the
        ``capabilities`` key entirely; a missing or null value is treated
        as an empty list, which yields the same ``False`` flags this
        provider reported before capabilities existed.

        Args:
            name: Model identifier from the entry's ``model`` field.
            entry: One element of the ``models`` list, either a dict or a
                Pydantic model depending on the ``ollama`` SDK version.

        Returns:
            A :class:`_LazyOllamaModelMetadata` instance.
        """
        details = _safe_get(entry, "details") or {}
        parameter_size = _safe_get(details, "parameter_size", "") or ""
        display_name = f"{name} ({parameter_size})" if parameter_size else name

        raw_capabilities = _safe_get(entry, "capabilities") or []
        capabilities = {str(c).lower() for c in raw_capabilities}

        resolver = partial(self._resolve_context_window, name)

        return _LazyOllamaModelMetadata(
            resolver,
            model_id=name,
            display_name=display_name,
            pricing=_FREE_PRICING,
            capabilities=_LazyOllamaCapabilities(
                resolver,
                supports_system_messages=True,
                supports_function_calling="tools" in capabilities,
                supports_vision="vision" in capabilities,
            ),
        )

    def list_models(self, force_refresh: bool = False) -> list[ModelMetadata]:
        """List models currently available on the Ollama server.

        Costs exactly one HTTP request on a cache miss, and none on a
        hit.  Every field except the context window comes from
        ``/api/tags``; ``context_window`` and
        ``capabilities.max_context_window`` resolve via a memoised
        ``show()`` call the first time they are read, so callers that only
        need names or display labels never pay for them.

        Args:
            force_refresh: Bypass the cache and re-query the server.  Also
                clears the per-model ``show()`` cache, so a model re-pulled
                with a different ``num_ctx`` reports its new value.

        Returns:
            One :class:`_LazyOllamaModelMetadata` per installed model, or
            an empty list if the server is unreachable.  A copy is
            returned, so caller mutation cannot corrupt the cache.
        """
        if (
            not force_refresh
            and self._models_cache is not None
            and time.time() - self._cache_timestamp < CACHE_TTL_SECONDS
        ):
            # Copy so caller mutation cannot corrupt the cache (issue #12).
            return list(self._models_cache)

        if force_refresh:
            self._model_info_cache.clear()

        try:
            client = self._get_client()
            response = client.list()
        except Exception as e:
            # Transient failure: do not cache, so the next call retries.
            logger.warning("Failed to list Ollama models: %s", e)
            return []

        models: list[ModelMetadata] = []
        for entry in getattr(response, "models", []) or []:
            name = _safe_get(entry, "model", "") or ""
            if name:
                models.append(self._metadata_from_tags_entry(name, entry))

        self._models_cache = models
        self._cache_timestamp = time.time()
        return list(models)

    def _get_model_info(self, model_name: str) -> ModelMetadata:
        """Fetch model metadata using ``ollama.show()`` (cached)."""
        if model_name in self._model_info_cache:
            return self._model_info_cache[model_name]

        try:
            client = self._get_client()
            info = client.show(model_name)
            context_window = _extract_context_window(info)
            details = _safe_get(info, "details") or {}
            parameter_size = _safe_get(details, "parameter_size", "")
            display_name = f"{model_name} ({parameter_size})" if parameter_size else model_name

            metadata = ModelMetadata(
                model_id=model_name,
                display_name=display_name,
                context_window=context_window,
                pricing=_FREE_PRICING,
                capabilities=ProviderCapabilities(
                    supports_system_messages=True,
                    max_context_window=context_window,
                ),
            )
            self._model_info_cache[model_name] = metadata
            return metadata

        except Exception as e:
            logger.debug("Failed to get model info for %s: %s", model_name, e)
            return ModelMetadata(
                model_id=model_name,
                display_name=model_name,
                context_window=FALLBACK_CONTEXT_WINDOW,
                pricing=_FREE_PRICING,
            )

    def _resolve_context_window(self, model_name: str) -> int:
        """Return the context window for *model_name*, fetching if needed.

        Delegates to :meth:`_get_model_info`, which performs one
        ``show()`` call and memoises the result in ``_model_info_cache``.
        Sharing that cache means a lazy read and a
        :meth:`get_model_metadata` call never fetch the same model twice.

        Never raises.  :meth:`_get_model_info` already swallows every
        failure and returns metadata carrying
        :data:`FALLBACK_CONTEXT_WINDOW`, which matters here because this
        runs behind attribute access — an exception from reading
        ``.context_window`` would be badly surprising.

        Thread safety: concurrent first-touch of the same model may issue
        duplicate ``show()`` calls.  Both write the same value and dict
        assignment is atomic under the GIL, so this is left unlocked,
        consistent with the rest of ``_model_info_cache``.

        Args:
            model_name: Ollama model identifier, e.g. ``"qwen3:8b"``.

        Returns:
            Context window in tokens.
        """
        return self._get_model_info(model_name).context_window

    # --- Connection test ---

    def test_connection(self) -> tuple[bool, str]:
        """Test connectivity to the local Ollama server."""
        try:
            client = self._get_client()
            response = client.list()
            model_list = getattr(response, "models", []) or []
            if model_list:
                return True, f"Connected. {len(model_list)} models available."
            return True, "Connected. No models installed."
        except ImportError:
            return False, "ollama package not installed"
        except Exception as e:
            return False, f"Connection failed: {e}"

    # --- Tokens ---

    def count_tokens(self, text: str, model: str | None = None) -> int:
        """Estimate token count (character-based heuristic)."""
        return len(text) // CHARS_PER_TOKEN_ESTIMATE

    def _estimate_tokens(self, messages: list[LLMMessage]) -> int:
        """Estimate total token count across all messages."""
        total_chars = sum(len(m.content) for m in messages)
        return total_chars // CHARS_PER_TOKEN_ESTIMATE

    def get_model_pricing(self, model: str) -> ModelPricing:
        """Return pricing for *model* (always free for Ollama)."""
        return _FREE_PRICING

    def get_model_metadata(self, model: str) -> ModelMetadata | None:
        """Return metadata for *model*, fetching via ``ollama.show()``."""
        return self._get_model_info(model)


# ---------------------------------------------------------------------------
# Tool-calling format converters (pure functions, unit-testable)
# ---------------------------------------------------------------------------
#
# Ollama-specific notes:
#  * Ollama's native API uses OpenAI-style tool definitions:
#    [{"type": "function", "function": {"name", "description", "parameters"}}]
#  * Ollama supports a "tool" role natively (since ollama-python >= 0.3),
#    so we can pass tool result messages through as-is. The role goes in
#    the message dict alongside content. Some models also use a
#    "tool_call_id" field for correlation; we forward it when present.
#  * Ollama does not have an explicit tool_choice parameter at this
#    layer — the model decides. We accept tool_choice in the kwargs
#    for API symmetry but do not forward it.
#  * Multi-turn assistant tool_use re-emission: when re-sending an
#    assistant turn, ollama-python expects the message to have a
#    "tool_calls" field of the same shape it returns. We construct
#    that from LLMMessage.tool_calls.


def _convert_tool_def_to_ollama(tool: LLMToolDefinition) -> dict[str, Any]:
    """Convert an :class:`LLMToolDefinition` to Ollama's tool format.

    Ollama uses OpenAI-style tool definitions:
        {
            "type": "function",
            "function": {
                "name": "...",
                "description": "...",
                "parameters": {<JSON Schema>}
            }
        }
    """
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters or {"type": "object", "properties": {}},
        },
    }


def _convert_messages_to_ollama(messages: list[LLMMessage]) -> list[dict[str, Any]]:
    """Convert a list of LLMMessage to Ollama's message format.

    Handles:
      * Plain user/assistant/system messages → simple role+content dict
      * ``role="tool"`` messages → forwarded with the same role plus
        tool_call_id (when ollama-python recognises it). Ollama treats
        tool result content as the result of the tool call referenced
        by tool_call_id (if provided), or as a generic tool turn
        otherwise.
      * Assistant messages with ``tool_calls`` → re-emitted with the
        OpenAI-style ``tool_calls`` field on the message dict so the
        model can correlate the next turn's tool result.
    """
    out: list[dict[str, Any]] = []
    for msg in messages:
        if msg.role == "tool":
            entry: dict[str, Any] = {
                "role": "tool",
                "content": msg.content,
            }
            if msg.tool_call_id:
                entry["tool_call_id"] = msg.tool_call_id
            out.append(entry)
            continue

        if msg.role == "assistant" and msg.tool_calls:
            entry = {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": call.arguments,
                        },
                    }
                    for call in msg.tool_calls
                ],
            }
            out.append(entry)
            continue

        out.append({"role": msg.role, "content": msg.content})
    return out


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _extract_context_window(info: Any) -> int:
    """Extract context window size from an ``ollama.show()`` response.

    Checks (in order): ``model_info`` keys, ``parameters.num_ctx``,
    and ``modelfile`` text.  Falls back to :data:`FALLBACK_CONTEXT_WINDOW`.
    """
    model_info = _safe_get(info, "model_info") or {}
    if isinstance(model_info, dict):
        for key, value in model_info.items():
            if "context" in key.lower() and isinstance(value, int):
                return value
    else:
        # Pydantic model — iterate via items if available
        items = getattr(model_info, "items", None)
        if callable(items):
            for key, value in items():
                if "context" in str(key).lower() and isinstance(value, int):
                    return value

    parameters = _safe_get(info, "parameters") or {}
    if isinstance(parameters, dict) and "num_ctx" in parameters:
        return int(parameters["num_ctx"])

    modelfile = _safe_get(info, "modelfile", "")
    if modelfile and "num_ctx" in modelfile:
        match = re.search(r"num_ctx\s+(\d+)", modelfile)
        if match:
            return int(match.group(1))

    return FALLBACK_CONTEXT_WINDOW
