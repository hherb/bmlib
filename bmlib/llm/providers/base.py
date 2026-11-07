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

"""Abstract base class for LLM providers.

All LLM providers must inherit from :class:`BaseProvider` and implement the
abstract methods.  This ensures a consistent interface across Anthropic,
Ollama, OpenAI-compatible servers, and any future providers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from types import FrameType
from typing import TYPE_CHECKING

from bmlib.llm.context_window import ModelLimits, known_context_window

if TYPE_CHECKING:
    from collections.abc import Callable

    from bmlib.llm.data_types import (
        BatchEmbeddingResponse,
        EmbeddingResponse,
        LLMMessage,
        LLMResponse,
    )
    from bmlib.llm.stream import Stop, StreamDelta


@dataclass
class ProviderCapabilities:
    """Describes what a provider can do."""

    supports_streaming: bool = False
    supports_function_calling: bool = False
    supports_vision: bool = False
    supports_system_messages: bool = True
    max_context_window: int = 128_000


@dataclass
class ModelPricing:
    """Cost per million tokens (USD)."""

    input_cost: float = 0.0
    output_cost: float = 0.0


@dataclass
class ModelMetadata:
    """Information about a specific model."""

    model_id: str
    display_name: str
    context_window: int
    pricing: ModelPricing
    capabilities: ProviderCapabilities = field(default_factory=ProviderCapabilities)
    is_deprecated: bool = False


class BaseProvider(ABC):
    """Abstract base class for LLM providers.

    Class attributes to override:
        PROVIDER_NAME   – short identifier (e.g. ``"anthropic"``).
        DISPLAY_NAME    – human-readable label.
        DESCRIPTION     – one-liner.
        WEBSITE_URL     – provider's website.
        SETUP_INSTRUCTIONS – how to get started.
    """

    PROVIDER_NAME: str
    DISPLAY_NAME: str
    DESCRIPTION: str
    WEBSITE_URL: str
    SETUP_INSTRUCTIONS: str

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        **kwargs: object,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url or self.default_base_url
        self._client: object = None
        self._extra_config = kwargs

    # --- Provider characteristics (abstract properties) ---

    @property
    @abstractmethod
    def is_local(self) -> bool: ...

    @property
    @abstractmethod
    def is_free(self) -> bool: ...

    @property
    @abstractmethod
    def requires_api_key(self) -> bool: ...

    @property
    def api_key_env_var(self) -> str:
        return ""

    @property
    @abstractmethod
    def default_base_url(self) -> str: ...

    @property
    @abstractmethod
    def default_model(self) -> str: ...

    # --- Core operations ---

    @abstractmethod
    def chat(
        self,
        messages: list[LLMMessage],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: object,
    ) -> LLMResponse: ...

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
        """Stream a chat answer, calling *on_delta* for each piece.

        Takes :meth:`chat`'s arguments and returns what it would, once the
        stream ends; see :meth:`bmlib.llm.client.LLMClient.chat_stream`.
        *cancel* stops it at once, even before its first event (#635); a
        provider that streams should read its events through
        :func:`~bmlib.llm.stream.read_stream`, which does that. Not
        abstract, so a third-party provider written before streaming keeps
        working; the default raises :class:`NotImplementedError` rather than
        falling back to :meth:`chat`, which would make a caller's progress
        display wait for the whole answer without saying why.
        """
        raise NotImplementedError(f"{self.PROVIDER_NAME} does not support streaming")

    def embed(
        self,
        text: str,
        model: str | None = None,
        **kwargs: object,
    ) -> EmbeddingResponse:
        """Generate an embedding vector for *text*.

        Not all providers support embeddings.  The default raises
        :class:`NotImplementedError`; providers that support embeddings
        (e.g. Ollama) override this method.
        """
        raise NotImplementedError(f"{self.PROVIDER_NAME} does not support embeddings")

    def embed_batch(
        self,
        texts: list[str],
        model: str | None = None,
        max_batch_size: int | None = None,
        **kwargs: object,
    ) -> BatchEmbeddingResponse:
        """Generate embedding vectors for *texts* in few API requests.

        Providers that support batch embedding (e.g. Ollama) override
        this to send texts in groups of at most *max_batch_size* per API
        round-trip, which is far faster than looping :meth:`embed` for
        bulk workloads.  *max_batch_size* of ``None`` lets the provider
        choose its own default bound.  The default implementation raises
        :class:`NotImplementedError`.
        """
        raise NotImplementedError(f"{self.PROVIDER_NAME} does not support embeddings")

    @abstractmethod
    def list_models(self, force_refresh: bool = False) -> list[ModelMetadata]: ...

    @abstractmethod
    def test_connection(self) -> tuple[bool, str]: ...

    @abstractmethod
    def count_tokens(self, text: str, model: str | None = None) -> int: ...

    # --- Cost helpers ---

    def get_model_pricing(self, model: str) -> ModelPricing:
        return ModelPricing(input_cost=0.0, output_cost=0.0)

    def calculate_cost(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> float:
        pricing = self.get_model_pricing(model)
        return (input_tokens / 1_000_000) * pricing.input_cost + (
            output_tokens / 1_000_000
        ) * pricing.output_cost

    # --- Context window ---

    def context_window(self, model: str) -> int | None:
        """The context window of *model* in tokens, or ``None`` if unknown.

        Input and answer together (see :meth:`LLMClient.context_window`):
        :meth:`model_limits`'s ``window``.

        Raises:
            ContextWindowError: An override could not fetch the metadata.
            NoSuchModelError: The provider has no such model.
        """
        return self.model_limits(model).window

    def model_limits(self, model: str) -> ModelLimits:
        """The context window and the largest reply of *model*, from one lookup.

        The default asks bmlib's table of known windows, makes no request and
        states no output limit; providers whose API states the figures
        override it.

        Raises:
            ContextWindowError: An override could not fetch the metadata.
            NoSuchModelError: The provider has no such model.
        """
        return ModelLimits(window=known_context_window(self.PROVIDER_NAME, model))

    # --- Utility ---

    def get_model_metadata(self, model: str) -> ModelMetadata | None:
        for m in self.list_models():
            if m.model_id == model:
                return m
        return None

    def validate_model(self, model: str) -> bool:
        return any(m.model_id == model for m in self.list_models())

    def format_model_string(self, model: str) -> str:
        return f"{self.PROVIDER_NAME}:{model}"


def _sdk_import_failed(package: str, exc: ImportError) -> str:
    """The message for a provider SDK whose import raised *exc*.

    Reports what was raised rather than asserting the cause: registration
    already skips a provider whose SDK is absent (#303), so an import that
    fails here is usually an SDK that is present and broken — a missing
    dependency of its own, or a version skew — for which "not installed"
    prescribes a ``pip install`` that answers "Requirement already
    satisfied" and changes nothing.
    """
    return (
        f"Could not import the {package!r} package ({exc}). "
        f"If it is missing, install it with: pip install bmlib[{package}]"
    )


def traceback_frames(exc: BaseException) -> list[FrameType]:
    """The frames *exc*'s traceback passed through, outermost first."""
    frames = []
    tb = exc.__traceback__
    while tb is not None:
        frames.append(tb.tb_frame)
        tb = tb.tb_next
    return frames


def _module(frame: FrameType) -> str:
    name = frame.f_globals.get("__name__")
    return name if isinstance(name, str) else ""


def _package(frame: FrameType) -> str:
    return _module(frame).partition(".")[0]


# ``Response.json``: ollama-python is on httpx, anthropic 2.x and openai 3.x on httpx2.
_RESPONSE_DECODERS = frozenset({"httpx._models", "httpx2._models"})

# ``json.scanner`` is the pure-Python scanner, used where the C one is missing.
_JSON_DECODERS = frozenset({"json.decoder", "json.scanner"})


def too_deep_body(exc: RecursionError, sdk: str, provider: str) -> ValueError:
    """*exc* as the server's unreadable body if *sdk* raised it decoding one, else re-raised (#698).

    ``json`` decodes recursively, so a 200 nested past the interpreter's
    recursion limit raises ``RecursionError`` from the provider SDK's own
    decode, where a body that is not JSON raises ``json.JSONDecodeError``, a
    ``ValueError``. Only that raise is recognised, by its frames: the
    innermost is the ``json`` decoder's, under the rest of the ``json`` package,
    called by httpx's (or httpx2's) ``Response.json`` or directly, from a frame
    of *sdk*'s own package. A recursion bug in bmlib, a deep ``json.loads``
    bmlib makes itself, and a request too deep for the SDK to *encode* are
    re-raised.

    Args:
        exc: The ``RecursionError`` the SDK call raised.
        sdk: The SDK's top-level package (``"anthropic"``, ``"openai"``, ``"ollama"``).
        provider: The provider the message names (``"deepseek"`` over the ``openai`` SDK).

    Returns:
        ``ValueError("<provider>: the response is nested too deeply to decode")``.

    Raises:
        RecursionError: *exc* itself, if it was not raised by *sdk*'s own decode.
    """
    frames = traceback_frames(exc)
    # The decoder, not json.encoder: both SDKs json.dumps the request themselves.
    if not frames or _module(frames[-1]) not in _JSON_DECODERS:
        raise exc
    while frames and _package(frames[-1]) == "json":
        frames.pop()
    if frames and _module(frames[-1]) in _RESPONSE_DECODERS:
        frames.pop()
    if not frames or _package(frames[-1]) != sdk:
        raise exc
    return ValueError(f"{provider}: the response is nested too deeply to decode")
