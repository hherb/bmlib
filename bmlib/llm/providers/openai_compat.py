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

"""Base class for providers that expose an OpenAI-compatible chat API.

Subclasses set class-level constants for provider metadata, base URL,
API key env var, default model, fallback model list, and pricing dict.
The ``openai`` Python SDK handles the actual HTTP calls.

Usage::

    class MyProvider(OpenAICompatibleProvider):
        PROVIDER_NAME = "myprovider"
        DISPLAY_NAME = "My Provider"
        DESCRIPTION = "My LLM provider"
        WEBSITE_URL = "https://myprovider.ai"
        SETUP_INSTRUCTIONS = "Get API key at myprovider.ai"
        API_KEY_ENV_VAR = "MY_API_KEY"
        DEFAULT_BASE_URL = "https://api.myprovider.ai/v1"
        DEFAULT_MODEL = "my-model"
        FALLBACK_MODELS = [...]
        MODEL_PRICING = {...}
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from bmlib.llm.data_types import (
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
from bmlib.llm.utils import extract_json

logger = logging.getLogger(__name__)

CHARS_PER_TOKEN_ESTIMATE = 4
CACHE_TTL_SECONDS = 3600


class OpenAICompatibleProvider(BaseProvider):
    """Base for providers that support the OpenAI chat completions API."""

    # --- Subclass MUST override these ---
    API_KEY_ENV_VAR: str = ""
    DEFAULT_BASE_URL: str = ""
    DEFAULT_MODEL: str = ""
    FALLBACK_MODELS: list[ModelMetadata] = []
    MODEL_PRICING: dict[str, ModelPricing] = {}

    _FALLBACK_PRICING = ModelPricing(input_cost=1.0, output_cost=3.0)

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        **kwargs: object,
    ) -> None:
        resolved_key = api_key or os.environ.get(self.API_KEY_ENV_VAR, "")
        resolved_url = base_url or self.DEFAULT_BASE_URL
        super().__init__(api_key=resolved_key or None, base_url=resolved_url, **kwargs)
        self._models_cache: list[ModelMetadata] | None = None
        self._cache_timestamp: float = 0.0

    # --- Properties ---

    @property
    def is_local(self) -> bool:
        return False

    @property
    def is_free(self) -> bool:
        return False

    @property
    def requires_api_key(self) -> bool:
        return True

    @property
    def api_key_env_var(self) -> str:
        return self.API_KEY_ENV_VAR

    @property
    def default_base_url(self) -> str:
        return self.DEFAULT_BASE_URL

    @property
    def default_model(self) -> str:
        return self.DEFAULT_MODEL

    # --- Client ---

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError:
                raise ImportError(
                    "openai package not installed. Install with: pip install openai"
                )
            self._client = OpenAI(
                api_key=self._api_key or "unused",
                base_url=self._base_url,
            )
        return self._client

    # --- Chat ---

    def _is_reasoning_model(self, model: str) -> bool:
        """Whether *model* is a reasoning model with restricted parameters.

        Reasoning models (e.g. OpenAI's o-series) use ``max_completion_tokens``
        instead of ``max_tokens`` and reject non-default ``temperature``.
        Overridden by providers that expose such models; defaults to ``False``.
        """
        return False

    def chat(
        self,
        messages: list[LLMMessage],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: object,
    ) -> LLMResponse:
        model = model or self.default_model
        client = self._get_client()

        top_p: float | None = kwargs.get("top_p")  # type: ignore[assignment]
        json_mode: bool = kwargs.get("json_mode", False)  # type: ignore[assignment]
        tools: list[LLMToolDefinition] | None = kwargs.get("tools")  # type: ignore[assignment]
        tool_choice: str = kwargs.get("tool_choice", "auto")  # type: ignore[assignment]

        openai_messages = _convert_messages_to_openai(messages)

        request_kwargs: dict[str, object] = {
            "model": model,
            "messages": openai_messages,
        }
        # Reasoning models (e.g. OpenAI o1/o3) reject ``max_tokens`` (they
        # require ``max_completion_tokens``) and only accept the default
        # temperature. Branch on a provider-overridable hook.
        if self._is_reasoning_model(model):
            request_kwargs["max_completion_tokens"] = max_tokens
        else:
            request_kwargs["max_tokens"] = max_tokens
            request_kwargs["temperature"] = temperature
            if top_p is not None:
                request_kwargs["top_p"] = top_p
        if json_mode:
            request_kwargs["response_format"] = {"type": "json_object"}

        # Tool calling: convert LLMToolDefinition to OpenAI's nested
        # function-spec format and pass tools + tool_choice through to
        # the SDK. tool_choice maps from the bmlib canonical strings
        # ("auto"/"required"/"none"/"any" or a specific tool name) to
        # OpenAI's expected shape.
        if tools is not None:
            request_kwargs["tools"] = [_convert_tool_def_to_openai(t) for t in tools]
            request_kwargs["tool_choice"] = _convert_tool_choice_to_openai(tool_choice)

        response = client.chat.completions.create(**request_kwargs)

        choice = response.choices[0]
        content = choice.message.content or ""

        # Parse tool calls from the response. OpenAI returns them as
        # choice.message.tool_calls with .id, .function.name,
        # .function.arguments (a JSON string).
        tool_calls: list[LLMToolCall] = []
        raw_calls = getattr(choice.message, "tool_calls", None) or []
        for raw in raw_calls:
            call_id = getattr(raw, "id", "") or ""
            fn = getattr(raw, "function", None)
            if fn is None:
                continue
            name = getattr(fn, "name", "") or ""
            args_raw = getattr(fn, "arguments", "") or ""
            # OpenAI sends arguments as a JSON string. Parse to dict.
            args: dict[str, Any] = {}
            if isinstance(args_raw, dict):
                args = args_raw
            elif isinstance(args_raw, str) and args_raw:
                try:
                    parsed = json.loads(args_raw)
                    if isinstance(parsed, dict):
                        args = parsed
                except (ValueError, TypeError):
                    args = {"_raw": args_raw}
            tool_calls.append(LLMToolCall(id=call_id, name=name, arguments=args))

        if json_mode and content:
            try:
                json.loads(content)
            except json.JSONDecodeError:
                content = extract_json(content)

        return LLMResponse(
            content=content,
            model=model,
            input_tokens=response.usage.prompt_tokens if response.usage else 0,
            output_tokens=response.usage.completion_tokens if response.usage else 0,
            stop_reason=choice.finish_reason,
            tool_calls=tool_calls if tool_calls else None,
        )

    # --- Model listing ---

    def _known_context_window(self, model_id: str) -> int:
        """Look up context window from FALLBACK_MODELS for known model IDs."""
        for fb in self.FALLBACK_MODELS:
            if fb.model_id == model_id:
                return fb.context_window
        return 128_000

    def list_models(self, force_refresh: bool = False) -> list[ModelMetadata]:
        if (
            not force_refresh
            and self._models_cache is not None
            and time.time() - self._cache_timestamp < CACHE_TTL_SECONDS
        ):
            return self._models_cache

        try:
            client = self._get_client()
            api_response = client.models.list()
            model_list = api_response.data if hasattr(api_response, "data") else []
            models = []
            for m in model_list:
                model_id = m.id
                pricing = self.MODEL_PRICING.get(model_id, self._FALLBACK_PRICING)
                ctx = self._known_context_window(model_id)
                models.append(
                    ModelMetadata(
                        model_id=model_id,
                        display_name=model_id,
                        context_window=ctx,
                        pricing=pricing,
                        capabilities=ProviderCapabilities(
                            supports_system_messages=True,
                            max_context_window=ctx,
                        ),
                    )
                )
            if models:
                self._models_cache = models
                self._cache_timestamp = time.time()
                return models
        except Exception as e:
            logger.warning(
                "Failed to fetch models from %s API: %s", self.DISPLAY_NAME, e
            )

        return list(self.FALLBACK_MODELS)

    # --- Connection test ---

    def test_connection(self) -> tuple[bool, str]:
        try:
            client = self._get_client()
            result = client.models.list()
            data = result.data if hasattr(result, "data") else []
            return True, f"Connected. {len(data)} models available."
        except Exception as e:
            return False, f"Connection failed: {e}"

    # --- Tokens ---

    def count_tokens(self, text: str, model: str | None = None) -> int:
        return len(text) // CHARS_PER_TOKEN_ESTIMATE

    def get_model_pricing(self, model: str) -> ModelPricing:
        return self.MODEL_PRICING.get(model, self._FALLBACK_PRICING)


# ---------------------------------------------------------------------------
# Tool-calling format converters (pure functions, unit-testable)
# ---------------------------------------------------------------------------
#
# OpenAI is the canonical wire format for bmlib's LLMToolDefinition,
# so this converter is essentially the identity transform — it wraps
# the LLMToolDefinition into the {"type": "function", "function": {...}}
# envelope that the OpenAI SDK expects.
#
# OpenAI-specific notes:
#  * Tool definitions: {"type": "function", "function": {name, description, parameters}}
#    where parameters is a JSON Schema object.
#  * Tool results are sent as messages with role="tool" and a
#    tool_call_id field. content is the result string. This matches
#    bmlib's LLMMessage(role="tool", ...) shape directly.
#  * Multi-turn assistant tool_use re-emission: assistant messages
#    with previous tool_calls must include the tool_calls field on
#    the message dict.
#  * tool_choice values: "auto" / "required" / "none" / {"type":"function","function":{"name":...}}.
#    bmlib's "any" alias maps to "required".


def _convert_tool_def_to_openai(tool: LLMToolDefinition) -> dict[str, Any]:
    """Convert an :class:`LLMToolDefinition` to OpenAI's tool format.

    OpenAI expects::

        {
            "type": "function",
            "function": {
                "name": "...",
                "description": "...",
                "parameters": {<JSON Schema>}
            }
        }

    bmlib's LLMToolDefinition.parameters is already in JSON Schema
    form, so this is a near-identity wrap.
    """
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters or {"type": "object", "properties": {}},
        },
    }


def _convert_tool_choice_to_openai(tool_choice: str) -> Any:
    """Convert bmlib canonical tool_choice to OpenAI's format.

    Mapping:
        ``"auto"`` (default)     → ``"auto"``
        ``"required"``/``"any"`` → ``"required"``
        ``"none"``               → ``"none"``
        anything else            → forced specific tool by name
    """
    if tool_choice in ("required", "any"):
        return "required"
    if tool_choice in ("auto", "none"):
        return tool_choice
    if not tool_choice:
        return "auto"
    # Specific tool name forced
    return {"type": "function", "function": {"name": tool_choice}}


def _convert_messages_to_openai(messages: list[LLMMessage]) -> list[dict[str, Any]]:
    """Convert bmlib LLMMessage list to OpenAI message format.

    Handles:
      * Plain user/assistant/system → simple {role, content} dict
      * ``role="tool"`` → {role: "tool", content, tool_call_id}
        (OpenAI requires tool_call_id on tool result messages so the
        model can correlate the result to the original tool call)
      * Assistant messages with ``tool_calls`` → re-emitted with the
        tool_calls field on the message dict, which OpenAI requires
        for multi-turn tool conversations
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
                "content": msg.content or None,
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            # OpenAI expects arguments as a JSON string
                            "arguments": json.dumps(call.arguments),
                        },
                    }
                    for call in msg.tool_calls
                ],
            }
            out.append(entry)
            continue

        out.append({"role": msg.role, "content": msg.content})
    return out
