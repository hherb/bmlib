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

"""Base agent class for LLM-powered tasks.

Provides shared infrastructure for agents that call LLMs:
- Model/provider resolution from externally-supplied configuration
- Helper methods for building messages
- JSON response parsing

Unlike the bmlibrarian_lite ``LiteBaseAgent``, this class does **not**
read config from a hardcoded path.  The calling application passes in
the model string and LLM client explicitly.

Usage::

    class ScoringAgent(BaseAgent):
        def score(self, title: str, abstract: str, interests: list[str]) -> dict:
            prompt = self.render_template("scoring.txt", ...)
            response = self.chat(
                [self.system_msg("You are ..."), self.user_msg(prompt)],
                json_mode=True,
            )
            # require_dict, because a model that answered with a top-level
            # array would otherwise hand this method a list: parse_json()
            # returns whatever the response parsed to.
            return self.parse_json(response.content, require_dict=True)
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Literal, overload

from bmlib.agents.metrics import PerformanceMetrics
from bmlib.llm import LLMClient, LLMMessage, LLMResponse
from bmlib.llm.json_repair import extract_and_repair_json
from bmlib.llm.utils import extract_json
from bmlib.templates import TemplateEngine

logger = logging.getLogger(__name__)

# Provider stop_reason values that mean the output hit the max_tokens ceiling:
# Anthropic reports "max_tokens", OpenAI-compatible providers "length".
_TRUNCATION_STOP_REASONS = ("max_tokens", "length")


class BaseAgent:
    """Base class for LLM-powered agents.

    Args:
        llm: The LLM client to use.
        model: Full model string (``"provider:model_name"``).
        template_engine: Template engine for loading prompt files.
        temperature: Default sampling temperature.
        max_tokens: Default max tokens.
        embedding_model: Default model string for :meth:`embed`. ``None``
            lets the client pick its default provider's default. Declared
            last so positional construction stays stable across versions.
    """

    def __init__(
        self,
        llm: LLMClient,
        model: str,
        template_engine: TemplateEngine | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        embedding_model: str | None = None,
    ) -> None:
        self.llm = llm
        self.model = model
        self.templates = template_engine
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.embedding_model = embedding_model
        self._metrics = PerformanceMetrics()

    # --- Message helpers ---

    @staticmethod
    def system_msg(content: str) -> LLMMessage:
        return LLMMessage(role="system", content=content)

    @staticmethod
    def user_msg(content: str) -> LLMMessage:
        return LLMMessage(role="user", content=content)

    @staticmethod
    def assistant_msg(content: str) -> LLMMessage:
        return LLMMessage(role="assistant", content=content)

    # --- LLM interaction ---

    def chat(
        self,
        messages: list[LLMMessage],
        *,
        json_mode: bool = False,
        temperature: float | None = None,
        max_tokens: int | None = None,
        # `Any` rather than `object`: splatted into `LLMClient.chat()`'s
        # typed signature below (see `llm.providers.get_provider`).
        **kwargs: Any,
    ) -> LLMResponse:
        """Send a chat request through the LLM client.

        Records the call into :attr:`metrics` on success; a request that
        raises records nothing.
        """
        start = time.monotonic()
        response = self.llm.chat(
            messages=messages,
            model=self.model,
            temperature=temperature if temperature is not None else self.temperature,
            max_tokens=max_tokens if max_tokens is not None else self.max_tokens,
            json_mode=json_mode,
            **kwargs,
        )
        self._metrics.add_request(
            response.input_tokens, response.output_tokens, time.monotonic() - start
        )
        return response

    @overload
    def chat_json(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        max_retries: int = 3,
        retry_context: str = "",
        require_dict: Literal[True],
        **kwargs: object,
    ) -> dict: ...

    @overload
    def chat_json(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        max_retries: int = 3,
        retry_context: str = "",
        require_dict: Literal[False] = False,
        **kwargs: object,
    ) -> dict | list: ...

    # A caller holding a runtime bool — ``require_dict=self.strict`` — matches
    # neither literal overload, and mypy does not expand ``bool`` into
    # ``Literal[True] | Literal[False]`` to find one.  Without this variant it
    # reports "no overload variant matches" with no way to satisfy it.
    @overload
    def chat_json(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        max_retries: int = 3,
        retry_context: str = "",
        require_dict: bool,
        **kwargs: object,
    ) -> dict | list: ...

    def chat_json(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        max_retries: int = 3,
        retry_context: str = "",
        require_dict: bool = False,
        **kwargs: object,
    ) -> dict | list:
        """Chat with JSON mode, retry on empty/unparseable responses.

        Combines :meth:`chat` with :meth:`parse_json` and exponential
        backoff retry.  Empty responses are treated as transport/model
        errors (WARNING).  Unparseable responses are logged at ERROR
        with the full model output for diagnosis.

        A response whose JSON is a top-level array yields that array whole.
        Pass *require_dict* to demand an object instead.

        A response that stopped because it hit the ``max_tokens``
        ceiling is reported as truncation, not "unparseable response".
        At temperature 0 the retry is provably futile (greedy sampling
        reproduces the identical truncation), so it raises immediately;
        at temperature > 0 a retry may sample a shorter completion that
        fits, so truncation still gets the normal retries, but the
        final error names the real cause.

        Args:
            messages: Conversation turns to send.
            temperature: Sampling temperature override.
            max_tokens: Max output token override.
            max_retries: Maximum number of attempts before giving up.
            retry_context: Label naming the task, folded into the retry and
                failure log lines so a failure says what was being attempted.
            require_dict: Demand a JSON object.  A response that parses to
                anything else is retried like any other bad response — and,
                at temperature 0, fails immediately for the same reason
                truncation does.  The shape failure is reported separately
                from "unparseable response": they are different diagnoses.
            **kwargs: Passed through to :meth:`chat`.

        Returns:
            The parsed dict, or the parsed list when *require_dict* is unset
            and the model answered with a top-level array.

        Raises:
            ValueError: On truncation, on a wrong shape under
                *require_dict*, or after all retries are exhausted.
        """
        context = f" for {retry_context}" if retry_context else ""
        last_error: str | None = None
        effective_temperature = temperature if temperature is not None else self.temperature

        def reject_shape(parsed: dict | list, attempt: int) -> str:
            """Name the wrong shape; raise when a retry is provably futile.

            Defined once and used on both return paths — the normal one and
            the truncation path's :meth:`_try_parse` shortcut — so the two
            cannot drift apart.
            """
            message = f"expected a JSON object, got {type(parsed).__name__}{context}"
            # The attempt marker already carries *context*, so the log line
            # spells the reason out rather than reusing *message* — which ends
            # in the same context and would print it twice.
            logger.error(
                "LLM returned the wrong JSON shape (attempt %d/%d%s): expected a JSON "
                "object, got %s",
                attempt + 1,
                max_retries,
                context,
                type(parsed).__name__,
            )
            if effective_temperature == 0.0:
                # Greedy sampling returns the same array from the same
                # messages, exactly as it reproduces a truncation.
                raise ValueError(message) from None
            return message

        for attempt in range(max_retries):
            if attempt > 0:
                delay = 2 ** (attempt - 1)  # 1s, 2s, 4s …
                logger.warning(
                    "Retry %d/%d%s after %.0fs (previous: %s)",
                    attempt + 1,
                    max_retries,
                    context,
                    delay,
                    last_error,
                )
                time.sleep(delay)
                self._metrics.add_retry()

            response = self.chat(
                messages,
                json_mode=True,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            )

            content = response.content.strip()

            if response.stop_reason in _TRUNCATION_STOP_REASONS:
                parsed = self._try_parse(content)
                if parsed is not None:
                    if not require_dict or isinstance(parsed, dict):
                        # The JSON happens to be complete despite hitting the
                        # ceiling — usable as-is.
                        return parsed
                    last_error = reject_shape(parsed, attempt)
                    continue
                budget = max_tokens if max_tokens is not None else self.max_tokens
                truncated = (
                    f"response truncated at max_tokens={budget} "
                    f"(stop_reason={response.stop_reason!r}) — raise max_tokens "
                    f"or request less output{context}"
                )
                logger.error(
                    "LLM response truncated (attempt %d/%d%s), full response: %s",
                    attempt + 1,
                    max_retries,
                    context,
                    content,
                )
                if effective_temperature == 0.0:
                    # Greedy sampling reproduces the identical truncation;
                    # retrying only pays for it again.
                    raise ValueError(truncated) from None
                # A retry at temperature > 0 may sample a shorter completion
                # that fits.
                last_error = truncated
                continue

            if not content:
                last_error = "empty response from model"
                logger.warning(
                    "LLM returned empty response (attempt %d/%d%s)",
                    attempt + 1,
                    max_retries,
                    context,
                )
                continue

            try:
                parsed = self.parse_json(content)
            except ValueError:
                last_error = "unparseable response"
                logger.error(
                    "LLM returned unparseable response (attempt %d/%d%s), full response: %s",
                    attempt + 1,
                    max_retries,
                    context,
                    content,
                )
                continue

            if require_dict and not isinstance(parsed, dict):
                last_error = reject_shape(parsed, attempt)
                continue

            return parsed

        raise ValueError(f"Failed after {max_retries} attempts{context}: {last_error}")

    # --- Performance metrics ---

    @property
    def metrics(self) -> PerformanceMetrics:
        """An independent snapshot of this agent's cumulative statistics."""
        return self._metrics.snapshot()

    def reset_metrics(self) -> None:
        """Clear all accumulated metrics."""
        self._metrics.reset()

    def start_metrics(self) -> None:
        """Mark the start of a metrics collection period."""
        self._metrics.mark_start()

    def stop_metrics(self) -> None:
        """Mark the end of a metrics collection period."""
        self._metrics.mark_end()

    def format_metrics_report(self) -> str:
        """Render this agent's metrics as a human-readable report."""
        return self._metrics.snapshot().format_report(title=type(self).__name__)

    # --- Embeddings ---

    def embed(self, text: str, model: str | None = None) -> list[float]:
        """Embed *text*, returning the raw vector.

        Args:
            text: The text to embed.
            model: Model string, overriding :attr:`embedding_model` for this
                call.  ``None`` falls back to the agent's default, then to
                the client's.

        Returns:
            The embedding vector.

        Raises:
            ValueError: If the provider returns an empty vector.

        Note:
            Embedding calls are not recorded into :attr:`metrics`: mixing
            them into ``tokens_per_second`` would distort a figure that is
            about generation.
        """
        response = self.llm.embed(text=text, model=model or self.embedding_model)
        if not response.embedding:
            raise ValueError(f"Empty embedding returned by model {response.model!r}")
        return response.embedding

    def embed_batch(
        self,
        texts: list[str],
        model: str | None = None,
        max_batch_size: int | None = None,
    ) -> list[list[float]]:
        """Embed *texts* in as few provider requests as possible.

        Several times faster than looping :meth:`embed` on bulk corpora.

        Args:
            texts: The texts to embed.  An empty list returns ``[]`` without
                contacting the provider.
            model: Model string, overriding :attr:`embedding_model`.
            max_batch_size: Maximum texts per provider request; ``None`` lets
                the provider choose.

        Returns:
            One vector per input text, in input order.

        Raises:
            ValueError: If the provider returns a different number of vectors
                than texts given.
        """
        if not texts:
            return []
        response = self.llm.embed_batch(
            texts=texts,
            model=model or self.embedding_model,
            max_batch_size=max_batch_size,
        )
        if len(response.embeddings) != len(texts):
            raise ValueError(
                f"Provider returned {len(response.embeddings)} embeddings for {len(texts)} texts"
            )
        return response.embeddings

    # --- Connectivity ---

    def test_connection(self) -> bool:
        """Report whether this agent's provider is reachable.

        Reachability only — whether *this* model is installed is a separate
        question, answered by ``llm.list_models(provider)``.

        A model string with no provider before the colon (``":model"``)
        splits to an empty provider, which is falsy — fall back to the
        client's default rather than passing it through: an empty string
        makes ``LLMClient.test_connection()`` take its all-providers branch
        and return a non-empty dict, which is truthy, silently reporting a
        non-existent provider as reachable.
        """
        provider = self.model.split(":", 1)[0] if ":" in self.model else self.llm.default_provider
        return bool(self.llm.test_connection(provider or self.llm.default_provider))

    # --- Template rendering ---

    def render_template(self, template_name: str, **variables: Any) -> str:
        """Render a prompt template.  Raises if no template engine configured."""
        if self.templates is None:
            raise RuntimeError(f"No template engine configured — cannot render {template_name!r}")
        return self.templates.render(template_name, **variables)

    # --- JSON parsing ---

    @classmethod
    def _try_parse(cls, text: str) -> dict | list | None:
        """:meth:`parse_json`, but ``None`` instead of ``ValueError`` on failure."""
        if not text:
            return None
        try:
            return cls.parse_json(text)
        except ValueError:
            return None

    @overload
    @staticmethod
    def parse_json(text: str, *, require_dict: Literal[True]) -> dict: ...

    @overload
    @staticmethod
    def parse_json(text: str, *, require_dict: Literal[False] = False) -> dict | list: ...

    # See the note on :meth:`chat_json`'s third overload: a runtime bool needs
    # its own variant, because mypy will not expand ``bool`` into the two
    # literals to match one of those above.
    @overload
    @staticmethod
    def parse_json(text: str, *, require_dict: bool) -> dict | list: ...

    @staticmethod
    def parse_json(text: str, *, require_dict: bool = False) -> dict | list:
        """Extract and parse JSON from LLM response text.

        Handles responses wrapped in markdown code blocks.

        Returns whatever the response parsed to, so a model that answered
        with a top-level array yields that array **whole** — the annotation
        is ``dict | list`` because both really happen.  Callers that need an
        object should say so with *require_dict* rather than assuming.

        ``dict | list`` is the whole of the contract, and it is enforced: a
        response that parses to a bare scalar — ``42``, ``"done"``, ``true``,
        ``null`` — is reported as :class:`ValueError` rather than handed back
        past an annotation that excludes it.  A scalar is not a structured
        answer to a ``json_mode`` request, and returning one only defers the
        failure to the caller's first subscript.

        ``RecursionError`` is caught at every stage alongside the decode
        errors: :func:`json.loads` descends recursively, so text nested past
        the interpreter's stack limit — ``'{"j": ' * 20000``, the shape a
        repetition-looping model emits — blows the stack instead of failing to
        decode.  Such a response is unparseable, and this method's contract is
        to report that as :class:`ValueError`.

        Args:
            text: The model output to parse.
            require_dict: Raise instead of returning a non-object result.

        Raises:
            ValueError: If no JSON can be parsed out of *text*, if the result
                is a bare scalar, or if *require_dict* is set and the result
                is not a dict.
        """
        parsed = BaseAgent._parse_json_any(text)
        if not isinstance(parsed, (dict, list)):
            raise ValueError(f"expected a JSON object or array, got {type(parsed).__name__}")
        if require_dict and not isinstance(parsed, dict):
            raise ValueError(f"expected a JSON object, got {type(parsed).__name__}")
        return parsed

    @staticmethod
    def _parse_json_any(text: str) -> Any:
        """:meth:`parse_json` without the shape checks.

        Returns any JSON value, scalars included — the ``dict | list``
        contract is enforced by :meth:`parse_json`, not here.
        """
        # Try direct parse
        try:
            return json.loads(text)
        except (json.JSONDecodeError, RecursionError):
            pass

        # Fall back to the shared extractor (code-block aware + balanced-brace
        # scanning that picks the first parseable object).  Fragments are held
        # back for the stage below: for a *truncated* array of objects —
        # '[{"a": 1}, {"b": 2}' — extraction can only offer the first object,
        # while repair closes the bracket and recovers both.  Taking the
        # fragment here would drop the sibling and skip the truncation warning,
        # which is the same silent loss the whole-span preference exists to
        # prevent.
        candidate = extract_json(text, allow_fragments=False)
        if candidate != text:
            try:
                return json.loads(candidate)
            except (json.JSONDecodeError, RecursionError):
                pass

        # Repair common LLM JSON defects (single quotes, trailing commas,
        # truncation, unquoted keys) after extracting the JSON span.
        try:
            repaired, was_repaired = extract_and_repair_json(text)
            parsed = json.loads(repaired)
        except (ValueError, json.JSONDecodeError, RecursionError):
            pass
        else:
            if was_repaired:
                # Repair closes brackets, so a truncated response can parse
                # into a valid but incomplete object.  Say so.
                logger.warning(
                    "LLM JSON needed repair — the response may be truncated: %s",
                    text[:200],
                )
            return parsed

        # Last resort: an object dug out of the inside of a span that neither
        # parsed nor repaired — '[{"a": 1}, invalid junk]'.  Nothing whole is
        # recoverable, so a fragment beats reporting the response unparseable.
        fragment = extract_json(text)
        if fragment != text:
            try:
                return json.loads(fragment)
            except (json.JSONDecodeError, RecursionError):
                pass

        raise ValueError(f"Could not parse JSON from LLM response: {text[:200]!r}")
