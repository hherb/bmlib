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

"""Unified LLM client with provider routing.

Routes requests to the appropriate provider based on model strings of
the form ``"provider:model_name"`` (e.g. ``"ollama:medgemma4B_it_q8"``
or ``"anthropic:claude-3-haiku-20240307"``).

Usage::

    from bmlib.llm import LLMClient, LLMMessage

    client = LLMClient(default_provider="ollama")
    resp = client.chat(
        messages=[LLMMessage(role="user", content="Summarise this paper.")],
        model="ollama:medgemma4B_it_q8",
        json_mode=True,
    )
"""

from __future__ import annotations

import logging
import threading

from bmlib.llm.data_types import (
    EmbeddingResponse,
    LLMMessage,
    LLMResponse,
    LLMToolDefinition,
)
from bmlib.llm.providers import (
    BaseProvider,
    ModelMetadata,
    get_provider,
    list_providers,
)
from bmlib.llm.token_tracker import get_token_tracker

logger = logging.getLogger(__name__)

DEFAULT_PROVIDER = "anthropic"


class LLMClient:
    """Unified LLM client that delegates to provider implementations.

    Automatically routes requests to the appropriate provider based on
    the model string format ``"provider:model_name"``.
    """

    def __init__(
        self,
        default_provider: str = DEFAULT_PROVIDER,
        ollama_host: str | None = None,
        anthropic_api_key: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        # Provider keys in the registry are lowercase; normalise here so a
        # mixed-case default ("Anthropic") routes and aggregates consistently.
        self.default_provider = default_provider.lower()
        self._provider_config: dict[str, dict[str, object]] = {
            "anthropic": {"api_key": anthropic_api_key or api_key},
            "ollama": {"base_url": ollama_host},
        }
        # OpenAI-compatible providers share the generic api_key / base_url
        for name in ("openai", "deepseek", "mistral", "gemini"):
            self._provider_config[name] = {"api_key": api_key, "base_url": base_url}
        self._providers: dict[str, BaseProvider] = {}

    def _get_provider(self, name: str) -> BaseProvider:
        """Return a cached provider instance, creating it on first access."""
        if name not in self._providers:
            config = self._provider_config.get(name, {})
            self._providers[name] = get_provider(name, **config)
        return self._providers[name]

    def _parse_model_string(self, model: str | None) -> tuple[str, str]:
        """Split ``"provider:model_name"`` into (provider, model_name)."""
        if model and ":" in model:
            provider, model_name = model.split(":", 1)
            return provider.lower(), model_name
        provider = self.default_provider.lower()
        provider_instance = self._get_provider(provider)
        model_name = model or provider_instance.default_model
        return provider, model_name

    def chat(
        self,
        messages: list[LLMMessage],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        top_p: float | None = None,
        json_mode: bool = False,
        tools: list[LLMToolDefinition] | None = None,
        tool_choice: str = "auto",
        **kwargs: object,
    ) -> LLMResponse:
        """Send a chat request, routing to the appropriate provider.

        Extra *kwargs* are forwarded to the provider's ``chat()`` method.
        Ollama-specific parameters (e.g. ``think=True``) are passed this way.

        Tool calling
        ------------
        Pass *tools* to allow the model to invoke functions you define.
        Each :class:`~bmlib.llm.data_types.LLMToolDefinition` is forwarded
        to the provider in its native format. The model's response will
        contain :attr:`LLMResponse.tool_calls` with parsed
        :class:`LLMToolCall` objects when the model decides to invoke a
        tool. To send the tool result back, append a ``role="tool"``
        message with ``tool_call_id`` referencing the call's id.

        *tool_choice* controls how the model selects tools:

        * ``"auto"`` (default) — the model decides
        * ``"required"`` / ``"any"`` — the model must call at least one tool
        * ``"none"`` — disable tool calling for this turn

        Providers that do not support tool calling will raise
        :class:`NotImplementedError` if *tools* is not ``None``.

        Args:
            messages: Conversation messages.
            model: Model identifier in ``"provider:model_name"`` form.
            temperature: Sampling temperature.
            max_tokens: Maximum tokens to generate.
            top_p: Nucleus sampling threshold.
            json_mode: Force JSON-formatted output where supported.
            tools: Optional list of tool definitions the model can call.
            tool_choice: Tool selection strategy (see above).
            **kwargs: Provider-specific extras.

        Raises:
            NotImplementedError: If *tools* is provided but the resolved
                provider does not declare ``supports_function_calling``.
        """
        provider_name, model_name = self._parse_model_string(model)

        logger.debug("Chat request: provider=%s, model=%s", provider_name, model_name)

        provider = self._get_provider(provider_name)

        # Pre-flight capability check: fail fast with a clear error if
        # the caller passed tools= to a provider that does not support
        # tool calling. We check at the LLMClient level so the error
        # path is consistent across providers and so we don't waste a
        # provider round-trip on a request that cannot succeed.
        if tools is not None and not _provider_supports_tools(provider):
            raise NotImplementedError(
                f"Provider {provider_name!r} does not support tool calling. "
                f"Pass tools=None or use a tool-capable provider "
                f"(anthropic, openai, ollama, deepseek, mistral, gemini)."
            )

        # Forward tools/tool_choice to the provider via kwargs. The
        # provider's chat() pulls them out of **kwargs the same way it
        # currently pulls top_p / json_mode / think. We do this rather
        # than passing them as named parameters because BaseProvider.chat()
        # does not declare them — that keeps the abstract contract
        # backwards compatible for any third-party subclass.
        if tools is not None:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice

        response = provider.chat(
            messages=messages,
            model=model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
            json_mode=json_mode,
            **kwargs,
        )

        # Track token usage
        tracker = get_token_tracker()
        cost = provider.calculate_cost(model_name, response.input_tokens, response.output_tokens)
        tracker.record_usage(
            model=f"{provider_name}:{model_name}",
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cost=cost,
        )

        return response

    def generate(
        self,
        prompt: str,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: object,
    ) -> LLMResponse:
        """Convenience wrapper: generate a response from a single prompt.

        Wraps *prompt* as a single user message and delegates to :meth:`chat`.
        """
        messages = [LLMMessage(role="user", content=prompt)]
        return self.chat(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )

    def embed(
        self,
        text: str,
        model: str | None = None,
        **kwargs: object,
    ) -> EmbeddingResponse:
        """Generate an embedding vector for *text*.

        Routes to the appropriate provider based on the *model* string.
        Not all providers support embeddings — the call will raise
        :class:`NotImplementedError` for providers that do not.

        Args:
            text: The text to embed.
            model: Model string (``"provider:model_name"`` format).
                   Defaults to the default provider's default model.
            **kwargs: Extra provider-specific arguments.
        """
        provider_name, model_name = self._parse_model_string(model)
        provider = self._get_provider(provider_name)
        return provider.embed(text=text, model=model_name, **kwargs)

    def test_connection(
        self,
        provider: str | None = None,
    ) -> bool | dict[str, tuple[bool, str]]:
        """Test connectivity to one or all providers."""
        if provider:
            try:
                p = self._get_provider(provider)
                success, _ = p.test_connection()
                return success
            except Exception:
                return False

        results = {}
        for name in list_providers():
            try:
                p = self._get_provider(name)
                results[name] = p.test_connection()
            except Exception as e:
                results[name] = (False, str(e))
        return results

    def list_models(
        self,
        provider: str | None = None,
    ) -> list[str] | list[ModelMetadata]:
        """List available models for one or all providers."""
        if provider:
            try:
                p = self._get_provider(provider)
                return [m.model_id for m in p.list_models()]
            except Exception:
                return []

        all_models: list[ModelMetadata] = []
        for name in list_providers():
            try:
                p = self._get_provider(name)
                all_models.extend(p.list_models())
            except Exception:
                pass
        return all_models

    def get_model_metadata(
        self,
        model: str,
        provider: str | None = None,
    ) -> ModelMetadata | None:
        """Return metadata for *model*, or ``None`` if unavailable."""
        if provider is None and ":" in model:
            provider, model = model.split(":", 1)
        provider = (provider or self.default_provider).lower()
        try:
            p = self._get_provider(provider)
            return p.get_model_metadata(model)
        except Exception:
            return None

    def get_provider_info(self, provider: str) -> dict[str, object]:
        """Return a dict of provider metadata (name, URLs, capabilities)."""
        p = self._get_provider(provider)
        return {
            "name": p.PROVIDER_NAME,
            "display_name": p.DISPLAY_NAME,
            "description": p.DESCRIPTION,
            "website_url": p.WEBSITE_URL,
            "setup_instructions": p.SETUP_INSTRUCTIONS,
            "is_local": p.is_local,
            "is_free": p.is_free,
            "requires_api_key": p.requires_api_key,
            "api_key_env_var": p.api_key_env_var,
            "default_base_url": p.default_base_url,
            "default_model": p.default_model,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Allowlist of providers known to support OpenAI-style tool calling.
# Add new providers here when their tool-calling implementation lands.
_TOOL_CAPABLE_PROVIDERS = {
    "anthropic",
    "openai",
    "deepseek",
    "mistral",
    "gemini",
    "ollama",
}


def _provider_supports_tools(provider: BaseProvider) -> bool:
    """Return True if *provider* is in the tool-calling allowlist.

    A static allowlist rather than a per-model capability query: some
    providers (e.g. Ollama) report capabilities per model, and querying
    every model just to answer "does this provider support tools?" is
    wasteful. Providers in :data:`_TOOL_CAPABLE_PROVIDERS` have been
    verified to support OpenAI-style tool calling on at least one
    current model.
    """
    name = getattr(provider, "PROVIDER_NAME", "").lower()
    return name in _TOOL_CAPABLE_PROVIDERS


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_global_client: LLMClient | None = None
# Non-reentrant lock, held while LLMClient() is constructed — nothing reached
# from LLMClient.__init__ may call get_llm_client(), or first use deadlocks.
_client_lock = threading.Lock()


def get_llm_client() -> LLMClient:
    """Return the global :class:`LLMClient` singleton (created on first call).

    Thread-safe: concurrent first calls create exactly one client.
    """
    global _global_client
    with _client_lock:
        if _global_client is None:
            _global_client = LLMClient()
        return _global_client


def reset_llm_client() -> None:
    """Discard the global :class:`LLMClient` singleton so it is re-created on next use."""
    global _global_client
    with _client_lock:
        _global_client = None
