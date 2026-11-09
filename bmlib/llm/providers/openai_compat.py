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
import re
import time
from collections.abc import Callable
from typing import Any

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
    CANCELLED_STOP_REASON,
    STREAM_USAGE_PROVIDERS,
    Stop,
    StreamDelta,
    read_stream,
    refuse_tools,
    sdk_events,
    stop_reader,
)
from bmlib.llm.utils import json_mode_reply

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
            except ImportError as exc:
                raise ImportError(_sdk_import_failed("openai", exc)) from exc
            self._client = OpenAI(
                api_key=self._api_key or "unused",
                base_url=self._base_url,
            )
            # A cancelled stream gives its request up at the socket (#649).
            install_give_up(self._client)
        return self._client

    # --- Chat ---

    def _is_reasoning_model(self, model: str) -> bool:
        """Whether *model* is a reasoning model with restricted parameters.

        Reasoning models (e.g. OpenAI's o-series) use ``max_completion_tokens``
        instead of ``max_tokens`` and reject non-default ``temperature``.
        Overridden by providers that expose such models; defaults to ``False``.
        """
        return False

    def honours_temperature(self, model: str) -> bool:
        """``False`` for a reasoning model, which is sent no ``temperature`` (#708)."""
        return not self._is_reasoning_model(model)

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
        request_kwargs = self._request_kwargs(messages, model, temperature, max_tokens, kwargs)
        json_mode: bool = kwargs.get("json_mode", False)  # type: ignore[assignment]
        think: bool | str | int | None = kwargs.get("think")  # type: ignore[assignment]

        try:
            response = client.chat.completions.create(**request_kwargs)
        except RecursionError as exc:
            raise too_deep_body(exc, "openai", self.PROVIDER_NAME) from exc

        # An error-shaped 200 is the server's failure, not bmlib's (#617), so it
        # is a ValueError naming what arrived.
        choices = getattr(response, "choices", None)
        if not isinstance(choices, list) or not choices:
            raise ValueError(f"{self.PROVIDER_NAME}: {_no_choices(response)}")
        choice = choices[0]
        finish_reason = getattr(choice, "finish_reason", None)
        stop_reason = finish_reason if isinstance(finish_reason, str) else None
        # Every field is read as Rust reads it: a value of the wrong type is
        # absent, never a TypeError from bmlib (#617). A choice with no message,
        # or content that is not text, reads as empty content, as in Rust, and
        # says so, being otherwise a model that said nothing.
        message = getattr(choice, "message", None)
        raw_content = getattr(message, "content", None)
        content = raw_content if isinstance(raw_content, str) else ""
        if message is None or not isinstance(raw_content, (str, type(None))):
            what = "no message" if message is None else f"{type(raw_content).__name__} content"
            logger.warning(
                "%s: the response's choice carries %s (finish_reason %r); read as empty content",
                self.PROVIDER_NAME,
                what,
                stop_reason,
            )

        # Separated reasoning output: DeepSeek / vLLM / SGLang use
        # ``reasoning_content``; OpenRouter-style servers use ``reasoning``.
        # Fall back to splitting a leading <think>…</think> block out of the
        # content — but only when the caller opted in with a truthy ``think``,
        # so existing callers see byte-identical content.
        thinking: str | None = None
        for attr in ("reasoning_content", "reasoning"):
            value = getattr(message, attr, None)
            if isinstance(value, str) and value:
                thinking = value
                break
        if thinking is None and think and content:
            thinking, content = _split_think_tags(content)

        # Parse tool calls from the response. OpenAI returns them as
        # choice.message.tool_calls with .id, .function.name,
        # .function.arguments (a JSON string).
        tool_calls: list[LLMToolCall] = []
        raw_calls = getattr(message, "tool_calls", None)
        for raw in raw_calls if isinstance(raw_calls, list) else []:
            fn = getattr(raw, "function", None)
            name = getattr(fn, "name", None)
            # A call naming no function is dropped, as in Rust.
            if not isinstance(name, str):
                continue
            call_id = getattr(raw, "id", None)
            call_id = call_id if isinstance(call_id, str) else ""
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
                except (ValueError, TypeError, RecursionError):
                    args = {"_raw": args_raw}
            tool_calls.append(LLMToolCall(id=call_id, name=name, arguments=args))

        if json_mode and content:
            content = json_mode_reply(content)

        return LLMResponse(
            content=content,
            model=model,
            input_tokens=_token_count(response, "prompt_tokens"),
            output_tokens=_token_count(response, "completion_tokens"),
            stop_reason=stop_reason,
            tool_calls=tool_calls if tool_calls else None,
            thinking=thinking,
        )

    def _request_kwargs(
        self,
        messages: list[LLMMessage],
        model: str,
        temperature: float,
        max_tokens: int,
        kwargs: dict[str, object],
    ) -> dict[str, object]:
        """Build the ``chat.completions.create`` arguments :meth:`chat` and
        :meth:`chat_stream` send, from their arguments and extra options."""
        top_p: float | None = kwargs.get("top_p")  # type: ignore[assignment]
        json_mode: bool = kwargs.get("json_mode", False)  # type: ignore[assignment]
        tools: list[LLMToolDefinition] | None = kwargs.get("tools")  # type: ignore[assignment]
        tool_choice: str = kwargs.get("tool_choice", "auto")  # type: ignore[assignment]
        think: bool | str | int | None = kwargs.get("think")  # type: ignore[assignment]
        extra_body: dict[str, Any] | None = kwargs.get("extra_body")  # type: ignore[assignment]

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

        # Cross-provider ``think`` kwarg: an effort-level string maps to
        # ``reasoning_effort`` on reasoning models. Anything else has no
        # OpenAI-compatible request parameter — it is deliberately NOT
        # forwarded (unknown parameters make many servers return 400).
        # Server-specific knobs go through ``extra_body`` instead.
        if think is not None:
            if isinstance(think, str) and self._is_reasoning_model(model):
                request_kwargs["reasoning_effort"] = think
            elif think:
                logger.debug(
                    "%s: no native request mapping for think=%r; not forwarded "
                    "(reasoning output is still extracted from the response; "
                    "use extra_body for server-specific thinking parameters)",
                    self.PROVIDER_NAME,
                    think,
                )

        # Escape hatch for server-specific parameters (e.g. vLLM's
        # chat_template_kwargs); the OpenAI SDK merges it into the JSON body.
        if extra_body is not None:
            request_kwargs["extra_body"] = extra_body

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
        """Stream a chat answer from the OpenAI-compatible endpoint.

        Sends the request :meth:`chat` would, with ``stream=True``, and reads
        the chunks through :func:`~bmlib.llm.stream.read_stream`, which
        *cancel* stops at once (#635). Usage is
        asked for only from :data:`~bmlib.llm.stream.STREAM_USAGE_PROVIDERS`;
        elsewhere it is read if the server sends it unasked, else 0. Usage
        asked for and not sent by the end is logged at WARNING, since it is
        then recorded as 0. The SDK retries opening the stream itself.

        Raises:
            NotImplementedError: If *tools* is passed.
            StreamError: If the stream fails after it opens or ends early.
        """
        refuse_tools(kwargs)
        model = model or self.default_model
        client = self._get_client()
        request_kwargs = self._request_kwargs(messages, model, temperature, max_tokens, kwargs)
        request_kwargs["stream"] = True
        if self.PROVIDER_NAME in STREAM_USAGE_PROVIDERS:
            request_kwargs["stream_options"] = {"include_usage": True}
        response = read_stream(
            "openai",
            lambda: client.chat.completions.create(**request_kwargs),
            lambda stream: sdk_events(stream, lambda c: c.to_dict()),
            model,
            on_delta,
            cancel,
        )
        # Every prompt costs at least one input token, so 0 is "not sent". A
        # cancel can land between the finish_reason chunk and the usage chunk
        # after it, which keeps the server's reason, so it is asked too.
        if (
            "stream_options" in request_kwargs
            and response.input_tokens == 0
            and response.stop_reason != CANCELLED_STOP_REASON
            and not stop_reader(cancel)()
        ):
            logger.warning(
                "%s was asked for usage on a stream of %s and sent none; recorded as 0 tokens",
                self.PROVIDER_NAME,
                model,
            )
        return response

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
            # Copy so caller mutation cannot corrupt the cache (issue #12).
            return list(self._models_cache)

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
                            supports_streaming=True,
                            supports_system_messages=True,
                            max_context_window=ctx,
                        ),
                    )
                )
        except Exception as e:
            # Transient failure: serve the fallback list WITHOUT caching it,
            # so the next call retries the API.
            logger.warning("Failed to fetch models from %s API: %s", self.DISPLAY_NAME, e)
            return list(self.FALLBACK_MODELS)

        # The API answered. An empty list is a valid (if unhelpful) answer —
        # cache the fallback for the TTL rather than re-hitting the API on
        # every call.
        if not models:
            models = list(self.FALLBACK_MODELS)
        self._models_cache = models
        self._cache_timestamp = time.time()
        return list(models)

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


def _no_choices(response: object) -> str:
    """Say what arrived in place of a choice (#617).

    The server's own words where it sent them — ``error.message``, which is
    what Rust's ``parse_openai_response`` quotes, or an ``error`` that is a
    bare string — else the body's first 200 characters, as Rust's
    ``http_error`` quotes a non-2xx body. The SDK hands back a
    ``ChatCompletion`` it built without validating, the parsed JSON when the
    body is not an object, or the text when it is not JSON, so the body is
    re-serialised from what it handed back: the bytes are not kept.
    """
    error = getattr(response, "error", None)
    said = error if isinstance(error, str) else _dict_text(error, "message")
    if said.strip():
        return f"no choices in the response (server said: {said})"
    body = (
        response
        if isinstance(response, str)
        else json.dumps(response, default=_sdk_object_as_json, ensure_ascii=False)
    )
    return f"no choices in the response (body: {body[:200]})"


def _dict_text(obj: object, key: str) -> str:
    """Return ``obj[key]`` when *obj* is a dict holding text there, else ``""``."""
    value = obj.get(key) if isinstance(obj, dict) else None
    return value if isinstance(value, str) else ""


def _sdk_object_as_json(obj: object) -> object:
    """The JSON an SDK model was built from: the fields the server sent alone.

    Anything else serialises as its ``repr``, so an object of unknown type
    still quotes.
    """
    to_dict = getattr(type(obj), "to_dict", None)
    if callable(to_dict):
        return to_dict(obj, warnings=False)
    return repr(obj)


def _token_count(response: object, key: str) -> int:
    """Return ``response.usage.<key>`` when it is a token count, else 0, as Rust.

    A ``bool`` is not a count, though Python reads it as an ``int``.
    """
    value = getattr(getattr(response, "usage", None), key, None)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


# Leading <think>…</think> block emitted inline by some local
# OpenAI-compatible servers (llama.cpp, LM Studio) for reasoning models.
_THINK_TAG_RE = re.compile(r"^\s*<think>(.*?)</think>\s*", re.DOTALL)


def _split_think_tags(content: str) -> tuple[str | None, str]:
    """Split a leading ``<think>…</think>`` block off *content*.

    Returns ``(thinking, remainder)``. When *content* has no leading
    think block, returns ``(None, content)`` unchanged. An empty think
    block yields ``thinking=None``.
    """
    match = _THINK_TAG_RE.match(content)
    if not match:
        return None, content
    thinking = match.group(1).strip()
    return (thinking or None, content[match.end() :])


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
