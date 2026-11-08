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

import functools
import logging
import os
import time
from collections.abc import Callable
from typing import Any, Literal

from bmlib.llm.context_window import ModelLimits, as_dict, fetch_model_limits
from bmlib.llm.data_types import (
    LLMMessage,
    LLMResponse,
    LLMToolCall,
    LLMToolDefinition,
)
from bmlib.llm.give_up import install_give_up
from bmlib.llm.providers.base import (
    BaseProvider,
    ModelMetadata,
    ModelPricing,
    ProviderCapabilities,
    _sdk_import_failed,
    too_deep_body,
)
from bmlib.llm.stream import (
    Stop,
    StreamDelta,
    read_stream,
    refuse_tools,
    sdk_events,
)
from bmlib.llm.utils import json_mode_reply

logger = logging.getLogger(__name__)

# Default context window for Anthropic models (tokens)
DEFAULT_CONTEXT_WINDOW = 200_000

# How long to cache the model list (seconds)
CACHE_TTL_SECONDS = 3600

# Rough chars-per-token ratio for fallback estimation
CHARS_PER_TOKEN_ESTIMATE = 4

# Extended thinking with a budget (#707): the API-imposed minimum budget, and
# the budgets ``True`` and the cross-provider effort levels map to on a model
# reporting only ``enabled`` thinking (``think=True`` uses the "medium"
# budget); an ``int`` is a budget wherever ``enabled`` is reported. Budgets are
# clamped to max_tokens - 1 because the API requires budget_tokens <
# max_tokens. A model reporting ``adaptive`` thinking is sent ``True`` as
# adaptive and an effort level itself, as ``output_config.effort``.
THINKING_BUDGET_MIN = 1024
THINKING_EFFORT_BUDGETS = {"low": 2048, "medium": 8192, "high": 16384}
DEFAULT_THINKING_BUDGET = THINKING_EFFORT_BUDGETS["medium"]

# Adaptive thinking asks for the summarized trace (#710): Opus 4.7+, Sonnet 5+,
# Haiku 5.5 and Fable default ``display`` to omitted, which returns empty
# thinking text and leaves ``LLMResponse.thinking`` None though the model thought.
ADAPTIVE_THINKING_DISPLAY = "summarized"


ThinkingRefusalReason = Literal[
    "no-capabilities",
    "not-supported",
    "budget-not-supported",
    "effort-not-supported",
    "negative-budget",
    "max-tokens-too-small",
]


class ThinkingRefusedError(ValueError):
    """A ``think`` request the model cannot take, refused before it is sent (#707).

    ``reason`` names the rule, as ``tests/fixtures/anthropic_thinking.json``
    spells it: ``no-capabilities`` (the model's metadata says nothing readable
    about thinking), ``not-supported``, ``budget-not-supported`` (an ``int``
    budget for a model that thinks only adaptively), ``effort-not-supported``,
    ``negative-budget`` or ``max-tokens-too-small``.
    """

    def __init__(self, message: str, *, reason: ThinkingRefusalReason) -> None:
        super().__init__(message)
        self.reason: ThinkingRefusalReason = reason

    def __reduce__(self) -> tuple[Any, ...]:
        # ``reason`` is keyword-only, which pickle's default (``cls(*args)``)
        # cannot pass, so the error could not cross a process pool.
        return (functools.partial(type(self), reason=self.reason), self.args)


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
        # ``models.retrieve`` bodies by model id, read for ``think`` (#707).
        self._model_bodies: dict[str, Any] = {}
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

                kwargs: dict[str, Any] = {"api_key": self._api_key}
                if self._base_url and self._base_url != self.default_base_url:
                    kwargs["base_url"] = self._base_url
                self._client = anthropic.Anthropic(**kwargs)
            except ImportError as exc:
                raise ImportError(_sdk_import_failed("anthropic", exc)) from exc
            # A cancelled stream gives its request up at the socket (#649).
            install_give_up(self._client)
        return self._client

    # --- Core operations ---

    def model_limits(self, model: str) -> ModelLimits:
        """The model's ``max_input_tokens`` and ``max_tokens``, from ``models.retrieve``.

        The SDK retries transient failures itself, so no backoff here.

        Raises:
            NoSuchModelError: The API has no such model.
            ContextWindowError: The request failed.
        """
        import anthropic

        client = self._get_client()
        return fetch_model_limits(
            self.PROVIDER_NAME,
            model,
            lambda: as_dict(client.models.retrieve(model)),
            not_found=(anthropic.NotFoundError,),
        )

    def _model_body(self, model: str) -> Any:
        """*model*'s ``models.retrieve`` body as a dict, fetched once and kept.

        A failed lookup raises the SDK's own error, as a failed chat does, with
        a note naming the lookup (the SDK's message names no URL, so it would
        read as the chat failing), and is not kept, so the next call asks again.
        """
        body = self._model_bodies.get(model)
        if body is None:
            try:
                body = as_dict(self._get_client().models.retrieve(model))
            except RecursionError as exc:
                raise too_deep_body(exc, "anthropic", "anthropic") from exc
            except Exception as exc:
                exc.add_note(
                    f"raised by the lookup of {model!r}'s thinking capabilities "
                    f"(GET /v1/models/{{id}}, #707), before the chat was sent"
                )
                raise
            # A refusal names the model the body describes; a body naming none
            # (a proxy's stub) is the model asked for.
            if isinstance(body, dict) and not body.get("id"):
                body = {**body, "id": model}
            self._model_bodies[model] = body
        return body

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
            temperature: Accepted for the common provider signature and
                **not sent** (#705): anthropic >= 1.0's ``create()`` takes no
                such keyword, API version ``2026-10-21``, which anthropic
                2.x sends, refuses a sampling parameter on every
                model, and Claude Opus 4.7 and later refuse one on
                ``2023-06-01`` too, so every request samples at the API's
                defaults.
            max_tokens: Maximum tokens to generate.
            **kwargs: Extra options (``json_mode``, ``tools``,
                ``tool_choice``, ``think``; ``top_p`` is not sent, as
                *temperature* is not). A truthy ``think`` enables thinking in
                the shape the model's own ``models.retrieve`` reports (#707,
                looked up once per model and kept): adaptive where it is
                supported, ``True`` at the model's default effort and an
                effort level as ``output_config.effort``; an ``int`` is a
                ``budget_tokens`` and needs a model taking budgets, as does
                ``True`` or ``"low"``/``"medium"``/``"high"`` on a model that
                takes nothing else (a preset budget). See
                :func:`resolve_thinking`.

        Raises:
            ThinkingRefusedError: A ``ValueError``: *think* is truthy and the
                model cannot take it (its ``reason`` says which rule), before
                the chat request is sent.
            ValueError: If *think* is not a ``bool``, ``int`` or ``str``, or
                the response is nested too deeply to decode (#698).
            anthropic.APIError: The SDK's own error, from the chat or, for a
                truthy *think*, from the model lookup before it (with a note
                naming the lookup).
        """
        model = model or self.default_model
        client = self._get_client()
        request_kwargs = self._request_kwargs(messages, model, max_tokens, kwargs)
        json_mode: bool = kwargs.get("json_mode", False)  # type: ignore[assignment]

        try:
            response = client.messages.create(**request_kwargs)
        except RecursionError as exc:
            raise too_deep_body(exc, "anthropic", "anthropic") from exc

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
            content = json_mode_reply(content)

        return LLMResponse(
            content=content,
            model=model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            stop_reason=response.stop_reason,
            tool_calls=tool_calls if tool_calls else None,
            thinking="\n\n".join(thinking_parts) if thinking_parts else None,
        )

    def _request_kwargs(
        self,
        messages: list[LLMMessage],
        model: str,
        max_tokens: int,
        kwargs: dict[str, object],
    ) -> dict[str, object]:
        """Build the ``messages.create`` arguments :meth:`chat` and
        :meth:`chat_stream` send, from their arguments and extra options.

        Raises:
            ThinkingRefusedError: As :meth:`chat` documents for *think*, and
                the ``ValueError`` and the lookup's SDK error it documents.
        """
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
        }
        # No temperature or top_p (#705): see chat()'s docstring.
        if system_content:
            request_kwargs["system"] = system_content

        if think:
            # Checked ahead of the lookup, so a wrong type costs no request
            # and is not hidden behind a failed one.
            _require_think_type(think)
            fields = resolve_thinking(think, max_tokens, self._model_body(model))
            request_kwargs["thinking"] = fields["thinking"]
            if "output_config" in fields:
                # extra_body: create() takes output_config only from about 0.80,
                # and the extra's floor is 0.41.
                request_kwargs["extra_body"] = {"output_config": fields["output_config"]}

        # Tool calling: convert OpenAI-style tool defs to Anthropic format
        # and forward tool_choice if explicitly set.
        if tools is not None:
            request_kwargs["tools"] = [_convert_tool_def_to_anthropic(t) for t in tools]
            anth_tool_choice = _convert_tool_choice_to_anthropic(tool_choice)
            if anth_tool_choice is not None:
                request_kwargs["tool_choice"] = anth_tool_choice

        return request_kwargs

    def chat_stream(
        self,
        messages: list[LLMMessage],
        on_delta: Callable[[StreamDelta], object],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        cancel: Stop | None = None,
        **kwargs: object,
    ) -> LLMResponse:
        """Stream a chat answer from the Anthropic API.

        Sends the request :meth:`chat` would, with ``stream=True``, and reads
        the events through :func:`~bmlib.llm.stream.read_stream`, which
        *cancel* stops at once (#635). The SDK retries opening the stream
        itself. The request is built where the stream is opened, so a
        thinking model's lookup (#707) is covered by *cancel* too: a cancel
        already set makes no lookup.

        Raises:
            NotImplementedError: If *tools* is passed.
            StreamError: If the stream fails after it opens or ends early.
            ThinkingRefusedError: As :meth:`chat` documents for *think*, and
                the ``ValueError`` and the lookup's SDK error it documents.
        """
        refuse_tools(kwargs)
        model = model or self.default_model
        client = self._get_client()
        return read_stream(
            "anthropic",
            lambda: client.messages.create(
                **self._request_kwargs(messages, model, max_tokens, kwargs), stream=True
            ),
            lambda stream: sdk_events(stream, lambda e: e.to_dict()),
            model,
            on_delta,
            cancel,
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
                            supports_streaming=True,
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
                    capabilities=ProviderCapabilities(supports_streaming=True),
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
# Thinking helpers (pure functions, unit-testable)
# ---------------------------------------------------------------------------


def _supported(node: Any, *path: str) -> bool:
    """Whether the leaf at *path* under *node* has ``supported`` exactly ``True``."""
    for key in path:
        node = node.get(key) if isinstance(node, dict) else None
    return isinstance(node, dict) and node.get("supported") is True


def _budget_fields(budget: int, max_tokens: int) -> dict[str, object]:
    """``thinking`` with *budget* clamped to ``[THINKING_BUDGET_MIN, max_tokens - 1]``."""
    if max_tokens <= THINKING_BUDGET_MIN:
        raise ThinkingRefusedError(
            f"Anthropic extended thinking requires max_tokens > {THINKING_BUDGET_MIN} "
            f"(the API minimum thinking budget); got max_tokens={max_tokens}. "
            f"Increase max_tokens or disable think.",
            reason="max-tokens-too-small",
        )
    budget = max(THINKING_BUDGET_MIN, min(budget, max_tokens - 1))
    return {"thinking": {"type": "enabled", "budget_tokens": budget}}


def _require_think_type(think: object) -> None:
    """Refuse a *think* that is not a ``bool``, ``int`` or ``str``.

    Raises:
        ValueError: *think* is of another type.
    """
    if not isinstance(think, (bool, int, str)):
        raise ValueError(f"Unsupported think value {think!r}; expected bool, int, or str.")


def _adaptive_thinking() -> dict[str, object]:
    """A fresh adaptive ``thinking`` field, asking for the summarized trace (#710)."""
    return {"type": "adaptive", "display": ADAPTIVE_THINKING_DISPLAY}


def resolve_thinking(
    think: bool | str | int, max_tokens: int, model_body: Any
) -> dict[str, object]:
    """The request fields *think* becomes for the model *model_body* describes (#707).

    *model_body* is the model's ``GET /v1/models/{id}`` body; its
    ``capabilities.thinking.types`` says whether the model thinks
    ``adaptive``-ly, with an ``enabled`` budget, or both. Adaptive wins for
    ``True`` (the model's default effort) and for an effort level the model
    reports under ``capabilities.effort`` (sent as ``output_config.effort``);
    on an adaptive model a level it does not report is refused, never a preset
    budget, even where it also reports ``enabled``. Adaptive thinking is sent
    ``display: "summarized"`` (#710). A falsy *think* (thinking off) is no
    fields. An ``int`` is a budget and needs ``enabled``; with ``enabled``
    alone, ``True`` and ``"low"``/``"medium"``/``"high"`` are preset budgets.
    Held to Rust's ``anthropic_thinking`` by
    ``tests/fixtures/anthropic_thinking.json``.

    Raises:
        ThinkingRefusedError: The model cannot take *think*, or its body says
            nothing readable about thinking (refused, never guessed).
        ValueError: *think* is not a ``bool``, ``int`` or ``str``.
    """
    if not think:
        return {}
    _require_think_type(think)
    caps = model_body.get("capabilities") if isinstance(model_body, dict) else None
    thinking = caps.get("thinking") if isinstance(caps, dict) else None
    types = thinking.get("types") if isinstance(thinking, dict) else None
    model = model_body.get("id") if isinstance(model_body, dict) else None
    if not isinstance(types, dict):
        raise ThinkingRefusedError(
            f"Anthropic reports no thinking capabilities for {model!r}, so think= "
            f"cannot be sent in a shape it takes",
            reason="no-capabilities",
        )
    adaptive = _supported(types, "adaptive")
    enabled = _supported(types, "enabled")
    if not (adaptive or enabled):
        raise ThinkingRefusedError(
            f"Anthropic model {model!r} reports no thinking support", reason="not-supported"
        )
    if isinstance(think, bool):
        if adaptive:
            return {"thinking": _adaptive_thinking()}
        return _budget_fields(DEFAULT_THINKING_BUDGET, max_tokens)
    if isinstance(think, int):
        if think < 0:
            raise ThinkingRefusedError(
                f"Negative thinking budget {think}; expected a positive token count.",
                reason="negative-budget",
            )
        if not enabled:
            raise ThinkingRefusedError(
                f"Anthropic model {model!r} takes no thinking budget (think={think}); "
                f"pass an effort level or True",
                reason="budget-not-supported",
            )
        return _budget_fields(think, max_tokens)
    level = think.lower()
    if adaptive:
        if _supported(caps, "effort", level):
            return {"thinking": _adaptive_thinking(), "output_config": {"effort": level}}
        raise ThinkingRefusedError(
            f"Anthropic model {model!r} does not report think effort level {think!r}",
            reason="effort-not-supported",
        )
    if level in THINKING_EFFORT_BUDGETS:
        return _budget_fields(THINKING_EFFORT_BUDGETS[level], max_tokens)
    raise ThinkingRefusedError(
        f"Anthropic model {model!r} takes only a thinking budget, and think={think!r} "
        f"has none (expected {', '.join(THINKING_EFFORT_BUDGETS)}, an int or True)",
        reason="effort-not-supported",
    )


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
        parameter (Anthropic API requirement); several are joined by a
        blank line, wherever they sit in the transcript
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
    system_parts: list[str] = []
    out: list[dict[str, Any]] = []

    for msg in messages:
        if msg.role == "system":
            # Anthropic takes one ``system`` string, so every system message
            # is kept and joined; assigning kept the last alone and dropped
            # the rest in silence, on this provider only (#315).
            if msg.content:
                system_parts.append(msg.content)
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

    return "\n\n".join(system_parts), out
