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

"""Anthropic Claude API provider."""

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

# Default context window for Anthropic models (tokens)
DEFAULT_CONTEXT_WINDOW = 200_000

# How long to cache the model list (seconds)
CACHE_TTL_SECONDS = 3600

# Rough chars-per-token ratio for fallback estimation
CHARS_PER_TOKEN_ESTIMATE = 4

# Extended thinking: API-imposed minimum budget, and the budgets the
# cross-provider ``think`` effort levels map to. ``think=True`` uses the
# "medium" budget. Budgets are clamped to max_tokens - 1 because the API
# requires budget_tokens < max_tokens.
THINKING_BUDGET_MIN = 1024
THINKING_EFFORT_BUDGETS = {"low": 2048, "medium": 8192, "high": 16384}
DEFAULT_THINKING_BUDGET = THINKING_EFFORT_BUDGETS["medium"]


class AnthropicProvider(BaseProvider):
    """Anthropic Claude API provider."""

    PROVIDER_NAME = "anthropic"
    DISPLAY_NAME = "Anthropic"
    DESCRIPTION = "Claude models via Anthropic API"
    WEBSITE_URL = "https://console.anthropic.com"
    SETUP_INSTRUCTIONS = "Get API key from console.anthropic.com/account/keys"

    MODEL_PRICING: dict[str, ModelPricing] = {
        "claude-opus-4-20250514": ModelPricing(input_cost=15.0, output_cost=75.0),
        "claude-sonnet-4-20250514": ModelPricing(input_cost=3.0, output_cost=15.0),
        "claude-sonnet-4-5-20250929": ModelPricing(input_cost=3.0, output_cost=15.0),
        "claude-3-5-sonnet-20241022": ModelPricing(input_cost=3.0, output_cost=15.0),
        "claude-3-5-haiku-20241022": ModelPricing(input_cost=1.0, output_cost=5.0),
        "claude-3-opus-20240229": ModelPricing(input_cost=15.0, output_cost=75.0),
        "claude-3-sonnet-20240229": ModelPricing(input_cost=3.0, output_cost=15.0),
        "claude-3-haiku-20240307": ModelPricing(input_cost=0.25, output_cost=1.25),
    }

    # Fallback pricing when a model ID is not in MODEL_PRICING
    _FALLBACK_PRICING = ModelPricing(input_cost=3.0, output_cost=15.0)

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        **kwargs: object,
    ) -> None:
        resolved_api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        super().__init__(api_key=resolved_api_key, base_url=base_url, **kwargs)
        self._models_cache: list[ModelMetadata] | None = None
        self._cache_timestamp: float = 0.0
        # Model ids already warned about for estimated pricing (warn once each).
        self._pricing_warned: set[str] = set()

    # --- Properties ---

    @property
    def is_local(self) -> bool:
        """Whether this provider runs locally."""
        return False

    @property
    def is_free(self) -> bool:
        """Whether this provider is free to use."""
        return False

    @property
    def requires_api_key(self) -> bool:
        """Whether an API key is required."""
        return True

    @property
    def api_key_env_var(self) -> str:
        """Environment variable name for the API key."""
        return "ANTHROPIC_API_KEY"

    @property
    def default_base_url(self) -> str:
        """Default API base URL."""
        return "https://api.anthropic.com"

    @property
    def default_model(self) -> str:
        """Default model to use when none is specified."""
        return "claude-sonnet-4-20250514"

    # --- Client ---

    def _get_client(self) -> Any:
        """Lazily initialise and return the ``anthropic.Anthropic`` client."""
        if self._client is None:
            try:
                import anthropic

                kwargs: dict[str, object] = {"api_key": self._api_key}
                if self._base_url and self._base_url != self.default_base_url:
                    kwargs["base_url"] = self._base_url
                self._client = anthropic.Anthropic(**kwargs)
            except ImportError:
                raise ImportError(
                    "anthropic package not installed. Install with: pip install anthropic"
                )
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
        """Send a chat request to the Anthropic API.

        Args:
            messages: Conversation messages. Supports tool-result
                messages (``role="tool"``) and assistant messages with
                ``tool_calls`` for multi-turn tool conversations.
            model: Model identifier (e.g. ``"claude-sonnet-4-20250514"``).
            temperature: Sampling temperature.
            max_tokens: Maximum tokens to generate.
            **kwargs: Extra options (``top_p``, ``json_mode``,
                ``tools``, ``tool_choice``, ``think``). A truthy
                ``think`` enables extended thinking: ``True`` uses the
                default budget, an ``int`` sets ``budget_tokens``, and
                ``"low"``/``"medium"``/``"high"`` map to preset budgets.
                With thinking enabled the API only accepts default
                sampling parameters, so ``temperature`` and ``top_p``
                are omitted from the request.

        Raises:
            ValueError: If *think* is truthy but *max_tokens* leaves no
                room for the minimum thinking budget, or *think* is an
                unrecognised effort-level string.
        """
        model = model or self.default_model
        client = self._get_client()

        top_p: float | None = kwargs.get("top_p")  # type: ignore[assignment]
        json_mode: bool = kwargs.get("json_mode", False)  # type: ignore[assignment]
        tools: list[LLMToolDefinition] | None = kwargs.get("tools")  # type: ignore[assignment]
        tool_choice: str = kwargs.get("tool_choice", "auto")  # type: ignore[assignment]
        think: bool | str | int | None = kwargs.get("think")  # type: ignore[assignment]

        # Separate the system message and convert the rest into Anthropic
        # message format. Anthropic uses content blocks for everything,
        # so we route through a converter that handles both plain-text
        # turns and tool-related turns (assistant tool_use, tool results).
        system_content, chat_messages = _convert_messages_to_anthropic(messages)

        request_kwargs: dict[str, object] = {
            "model": model,
            "messages": chat_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system_content:
            request_kwargs["system"] = system_content
        if top_p is not None:
            request_kwargs["top_p"] = top_p

        if think:
            request_kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": _resolve_thinking_budget(think, max_tokens),
            }
            # The API rejects non-default sampling parameters while
            # thinking is enabled — omit them rather than error out.
            request_kwargs.pop("temperature", None)
            request_kwargs.pop("top_p", None)

        # Tool calling: convert OpenAI-style tool defs to Anthropic format
        # and forward tool_choice if explicitly set.
        if tools is not None:
            request_kwargs["tools"] = [_convert_tool_def_to_anthropic(t) for t in tools]
            anth_tool_choice = _convert_tool_choice_to_anthropic(tool_choice)
            if anth_tool_choice is not None:
                request_kwargs["tool_choice"] = anth_tool_choice

        response = client.messages.create(**request_kwargs)

        # Anthropic returns content blocks. Walk them and split into
        # text content, thinking, and tool calls.
        content = ""
        thinking_parts: list[str] = []
        tool_calls: list[LLMToolCall] = []
        if response.content:
            for block in response.content:
                btype = getattr(block, "type", None)
                if btype == "thinking":
                    part = getattr(block, "thinking", "")
                    if isinstance(part, str) and part:
                        thinking_parts.append(part)
                elif btype == "redacted_thinking":
                    # Encrypted reasoning — nothing human-readable to expose.
                    continue
                elif btype == "text" or hasattr(block, "text"):
                    content += getattr(block, "text", "") or ""
                elif btype == "tool_use":
                    tool_calls.append(
                        LLMToolCall(
                            id=getattr(block, "id", ""),
                            name=getattr(block, "name", ""),
                            arguments=dict(getattr(block, "input", {}) or {}),
                        )
                    )

        if json_mode and content:
            try:
                json.loads(content)
            except json.JSONDecodeError:
                content = extract_json(content)

        return LLMResponse(
            content=content,
            model=model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            stop_reason=response.stop_reason,
            tool_calls=tool_calls if tool_calls else None,
            thinking="\n\n".join(thinking_parts) if thinking_parts else None,
        )

    # --- Model listing ---

    def list_models(self, force_refresh: bool = False) -> list[ModelMetadata]:
        """List available models from the Anthropic API (cached).

        Args:
            force_refresh: Bypass the cache and re-query the API.
        """
        if (
            not force_refresh
            and self._models_cache is not None
            and time.time() - self._cache_timestamp < CACHE_TTL_SECONDS
        ):
            # Copy so caller mutation cannot corrupt the cache (issue #12).
            return list(self._models_cache)

        try:
            client = self._get_client()
            api_models = client.models.list()
            models = []
            for model in api_models:
                model_id = model.id
                pricing = self.MODEL_PRICING.get(model_id, self._FALLBACK_PRICING)
                display_name = getattr(model, "display_name", model_id)
                models.append(
                    ModelMetadata(
                        model_id=model_id,
                        display_name=display_name,
                        context_window=DEFAULT_CONTEXT_WINDOW,
                        pricing=pricing,
                        capabilities=ProviderCapabilities(
                            supports_vision=True,
                            supports_function_calling=True,
                            supports_system_messages=True,
                            max_context_window=DEFAULT_CONTEXT_WINDOW,
                        ),
                    )
                )
            self._models_cache = models
            self._cache_timestamp = time.time()
            return list(models)
        except Exception as e:
            logger.warning("Failed to fetch models from Anthropic API: %s", e)
            return [
                ModelMetadata(
                    model_id=mid,
                    display_name=mid,
                    context_window=DEFAULT_CONTEXT_WINDOW,
                    pricing=p,
                )
                for mid, p in self.MODEL_PRICING.items()
            ]

    # --- Connection test ---

    def test_connection(self) -> tuple[bool, str]:
        """Test connectivity to the Anthropic API."""
        try:
            client = self._get_client()
            models = list(client.models.list())
            return True, f"Connected. {len(models)} models available."
        except Exception as e:
            return False, f"Connection failed: {e}"

    # --- Tokens ---

    def count_tokens(self, text: str, model: str | None = None) -> int:
        """Count tokens in *text* using the Anthropic token-counting API.

        Falls back to a rough character-based estimate on failure.
        """
        try:
            client = self._get_client()
            result = client.messages.count_tokens(
                model=model or self.default_model,
                messages=[{"role": "user", "content": text}],
            )
            return result.input_tokens
        except Exception:
            return len(text) // CHARS_PER_TOKEN_ESTIMATE

    def get_model_pricing(self, model: str) -> ModelPricing:
        """Return pricing for *model*, falling back to default rates.

        Unknown model ids (typically models newer than the pricing table)
        are billed at the fallback (Sonnet) rates; a warning is logged once
        per model per provider instance so estimated cost accounting is
        visible rather than silent. The warned-set is not lock-guarded: a
        concurrent first call may log the warning twice, which is harmless.
        """
        pricing = self.MODEL_PRICING.get(model)
        if pricing is None:
            if model not in self._pricing_warned:
                self._pricing_warned.add(model)
                logger.warning(
                    "No pricing entry for Anthropic model %s; cost estimates use "
                    "fallback rates ($%.2f/$%.2f per Mtok)",
                    model,
                    self._FALLBACK_PRICING.input_cost,
                    self._FALLBACK_PRICING.output_cost,
                )
            return self._FALLBACK_PRICING
        return pricing


# ---------------------------------------------------------------------------
# Extended-thinking helpers (pure functions, unit-testable)
# ---------------------------------------------------------------------------


def _resolve_thinking_budget(think: bool | str | int, max_tokens: int) -> int:
    """Resolve a truthy ``think`` value into an Anthropic ``budget_tokens``.

    ``True`` → the default budget; an ``int`` → that budget; a string
    effort level (``"low"``/``"medium"``/``"high"``) → its preset budget.
    The result is clamped to ``[THINKING_BUDGET_MIN, max_tokens - 1]``
    because the API requires ``budget_tokens >= 1024`` and
    ``budget_tokens < max_tokens``.

    Raises:
        ValueError: If *max_tokens* leaves no room for the minimum
            budget, or the effort-level string is unrecognised.
    """
    if max_tokens <= THINKING_BUDGET_MIN:
        raise ValueError(
            f"Anthropic extended thinking requires max_tokens > {THINKING_BUDGET_MIN} "
            f"(the API minimum thinking budget); got max_tokens={max_tokens}. "
            f"Increase max_tokens or disable think."
        )
    if isinstance(think, bool):
        budget = DEFAULT_THINKING_BUDGET
    elif isinstance(think, int):
        budget = think
    elif isinstance(think, str):
        try:
            budget = THINKING_EFFORT_BUDGETS[think.lower()]
        except KeyError:
            raise ValueError(
                f"Unknown think effort level {think!r}; expected one of "
                f"{sorted(THINKING_EFFORT_BUDGETS)}, a token budget (int), or a bool."
            ) from None
    else:
        raise ValueError(f"Unsupported think value {think!r}; expected bool, int, or str.")
    return max(THINKING_BUDGET_MIN, min(budget, max_tokens - 1))


# ---------------------------------------------------------------------------
# Tool-calling format converters (pure functions, unit-testable)
# ---------------------------------------------------------------------------
#
# bmlib's public LLMToolDefinition / LLMMessage / LLMToolCall types use
# the OpenAI-style schema as the canonical wire format. Each provider
# converts to/from its native format inside the provider module so
# callers don't have to know provider-specific quirks.
#
# Anthropic-specific notes:
#  * Tool definitions use ``input_schema`` (not ``parameters``).
#  * Tool results are sent as user-role messages with content blocks
#    of type ``tool_result``, NOT as a separate "tool" role.
#  * Assistant messages that previously emitted a tool_use must be
#    re-sent with the tool_use blocks intact in the next turn so the
#    model can correlate the tool result.
#  * tool_choice values map differently: OpenAI's "auto"/"required"/
#    "none" become Anthropic's {"type": "auto"}, {"type": "any"},
#    or omission.


def _convert_tool_def_to_anthropic(tool: LLMToolDefinition) -> dict[str, Any]:
    """Convert an :class:`LLMToolDefinition` to Anthropic's tool schema.

    OpenAI's ``parameters`` becomes Anthropic's ``input_schema``.
    Other fields pass through unchanged.
    """
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.parameters or {"type": "object", "properties": {}},
    }


def _convert_tool_choice_to_anthropic(tool_choice: str) -> dict[str, Any] | None:
    """Convert OpenAI-style tool_choice to Anthropic's tool_choice format.

    Returns ``None`` for ``"auto"`` (Anthropic's default — omit the
    parameter rather than send ``{"type": "auto"}``, equivalent
    behaviour and slightly cleaner request payloads).

    Mapping:
        ``"auto"``               → ``None`` (default)
        ``"required"``/``"any"`` → ``{"type": "any"}``
        ``"none"``               → ``{"type": "none"}``
        anything else            → treated as a specific tool name
    """
    if tool_choice == "auto" or not tool_choice:
        return None
    if tool_choice in ("required", "any"):
        return {"type": "any"}
    if tool_choice == "none":
        return {"type": "none"}
    # Any other value is interpreted as a specific tool name to force
    return {"type": "tool", "name": tool_choice}


def _convert_messages_to_anthropic(
    messages: list[LLMMessage],
) -> tuple[str, list[dict[str, Any]]]:
    """Convert bmlib LLMMessage list to (system_content, anthropic_messages).

    Handles:
      * ``role="system"`` — extracted into the separate ``system``
        parameter (Anthropic API requirement)
      * ``role="user"`` / ``role="assistant"`` — passed through as
        plain content blocks
      * ``role="assistant"`` with ``tool_calls`` — re-emitted with
        tool_use content blocks alongside any text content
      * ``role="tool"`` — converted to a user-role message with a
        ``tool_result`` content block referencing ``tool_call_id``

    Consecutive ``role="tool"`` messages are merged into a single
    user-role message with multiple ``tool_result`` blocks, which is
    Anthropic's preferred shape when responding to multiple parallel
    tool calls in one assistant turn.
    """
    system_content = ""
    out: list[dict[str, Any]] = []

    for msg in messages:
        if msg.role == "system":
            system_content = msg.content
            continue

        if msg.role == "tool":
            # Tool result — Anthropic represents this as a user message
            # containing one or more tool_result content blocks. If the
            # previous message we emitted is already a user message
            # composed of tool_result blocks, append to it; otherwise
            # start a new one.
            block = {
                "type": "tool_result",
                "tool_use_id": msg.tool_call_id or "",
                "content": msg.content,
            }
            if (
                out
                and out[-1]["role"] == "user"
                and isinstance(out[-1]["content"], list)
                and all(
                    isinstance(c, dict) and c.get("type") == "tool_result"
                    for c in out[-1]["content"]
                )
            ):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
            continue

        if msg.role == "assistant" and msg.tool_calls:
            # Re-emit a previous assistant turn that included tool_use
            # blocks. Anthropic requires the original tool_use content
            # blocks to be present so the model can correlate the next
            # turn's tool_result blocks back to the original calls.
            blocks: list[dict[str, Any]] = []
            if msg.content:
                blocks.append({"type": "text", "text": msg.content})
            for call in msg.tool_calls:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call.id,
                        "name": call.name,
                        "input": call.arguments,
                    }
                )
            out.append({"role": "assistant", "content": blocks})
            continue

        # Plain text message (user or assistant without tool calls)
        out.append({"role": msg.role, "content": msg.content})

    return system_content, out
