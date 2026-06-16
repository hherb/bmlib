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
from typing import Any

from bmlib.llm.data_types import (
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

# Pricing for local models (always free)
_FREE_PRICING = ModelPricing(0.0, 0.0)


def _safe_get(obj: Any, key: str, default: Any = None) -> Any:
    """Extract a field from a dict or Pydantic-model response.

    The ``ollama`` SDK (>=0.4) returns Pydantic models with subscript
    access but without ``.get()``.  Older versions returned plain dicts.
    This helper handles both transparently.
    """
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


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
        think: bool | None = kwargs.get("think")  # type: ignore[assignment]
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
            request_kwargs["think"] = think

        # Tool calling: ollama-python (>=0.3) accepts an OpenAI-style
        # tools list directly. Convert our LLMToolDefinition into the
        # standard {"type": "function", "function": {...}} shape.
        if tools is not None:
            request_kwargs["tools"] = [_convert_tool_def_to_ollama(t) for t in tools]

        response = client.chat(**request_kwargs)

        # ollama >=0.4 returns Pydantic models; older versions return dicts.
        message = _safe_get(response, "message")
        content: str = _safe_get(message, "content", "") if message else ""

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

        return LLMResponse(
            content=content,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            stop_reason="tool_calls" if tool_calls else "stop",
            tool_calls=tool_calls if tool_calls else None,
        )

    def embed(
        self,
        text: str,
        model: str | None = None,
        **kwargs: object,
    ) -> EmbeddingResponse:
        """Generate an embedding vector for *text*.

        Args:
            text: The text to embed.
            model: Embedding model identifier.  Defaults to
                :pyattr:`default_model` (not ideal for embeddings — callers
                should pass an embedding-specific model).
            **kwargs: Reserved for future use.

        Returns:
            An :class:`EmbeddingResponse` with the embedding vector.
        """
        model = model or self.default_model
        client = self._get_client()

        try:
            response = client.embeddings(model=model, prompt=text)
        except Exception as e:
            logger.error("Ollama embedding error: %s", e)
            raise ConnectionError(f"Ollama embedding failed: {e}") from e

        embedding: list[float] = _safe_get(response, "embedding", [])
        input_tokens: int = _safe_get(response, "prompt_eval_count", 0) or 0

        return EmbeddingResponse(
            embedding=embedding,
            model=model,
            dimensions=len(embedding),
            input_tokens=input_tokens,
        )

    # --- Model discovery (native API) ---

    def list_models(self, force_refresh: bool = False) -> list[ModelMetadata]:
        """List models currently available on the Ollama server."""
        try:
            client = self._get_client()
            response = client.list()
            models = []
            model_list = getattr(response, "models", []) or []
            for model_info in model_list:
                name = getattr(model_info, "model", "") or ""
                if name:
                    metadata = self._get_model_info(name)
                    models.append(metadata)
            return models
        except Exception as e:
            logger.warning("Failed to list Ollama models: %s", e)
            return []

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
