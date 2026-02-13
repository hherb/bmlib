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
from typing import Optional, Union

from bmlib.llm.data_types import LLMMessage, LLMResponse
from bmlib.llm.token_tracker import get_token_tracker
from bmlib.llm.providers import (
    BaseProvider,
    ModelMetadata,
    get_provider,
    list_providers,
)

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
        ollama_host: Optional[str] = None,
        anthropic_api_key: Optional[str] = None,
    ) -> None:
        self.default_provider = default_provider
        self._provider_config: dict[str, dict[str, object]] = {
            "anthropic": {"api_key": anthropic_api_key},
            "ollama": {"base_url": ollama_host},
        }
        self._providers: dict[str, BaseProvider] = {}

    def _get_provider(self, name: str) -> BaseProvider:
        if name not in self._providers:
            config = self._provider_config.get(name, {})
            self._providers[name] = get_provider(name, **config)
        return self._providers[name]

    def _parse_model_string(self, model: Optional[str]) -> tuple[str, str]:
        if model and ":" in model:
            provider, model_name = model.split(":", 1)
            return provider.lower(), model_name
        provider = self.default_provider
        provider_instance = self._get_provider(provider)
        model_name = model or provider_instance.default_model
        return provider, model_name

    def chat(
        self,
        messages: list[LLMMessage],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        top_p: Optional[float] = None,
        json_mode: bool = False,
        **kwargs: object,
    ) -> LLMResponse:
        """Send a chat request, routing to the appropriate provider.

        Extra *kwargs* are forwarded to the provider's ``chat()`` method.
        Ollama-specific parameters (e.g. ``think=True``) are passed this way.
        """
        provider_name, model_name = self._parse_model_string(model)

        logger.debug("Chat request: provider=%s, model=%s", provider_name, model_name)

        provider = self._get_provider(provider_name)
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
        cost = provider.calculate_cost(
            model_name, response.input_tokens, response.output_tokens
        )
        tracker.record_usage(
            model=f"{provider_name}:{model_name}",
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cost=cost,
        )

        return response

    def test_connection(
        self, provider: Optional[str] = None,
    ) -> Union[bool, dict[str, tuple[bool, str]]]:
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
        self, provider: Optional[str] = None,
    ) -> Union[list[str], list[ModelMetadata]]:
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
        self, model: str, provider: Optional[str] = None,
    ) -> Optional[ModelMetadata]:
        if provider is None and ":" in model:
            provider, model = model.split(":", 1)
        provider = provider or self.default_provider
        try:
            p = self._get_provider(provider)
            return p.get_model_metadata(model)
        except Exception:
            return None

    def get_provider_info(self, provider: str) -> dict[str, object]:
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
# Global singleton
# ---------------------------------------------------------------------------

_global_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    global _global_client
    if _global_client is None:
        _global_client = LLMClient()
    return _global_client


def reset_llm_client() -> None:
    global _global_client
    _global_client = None
