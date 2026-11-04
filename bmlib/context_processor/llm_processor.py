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

"""A context processor that extracts through an LLM.

Ready-made subclass of :class:`~bmlib.context_processor.IterativeContextProcessor`
for the common case: text chunks — optionally carrying a relevance score
from a semantic search — summarised by a model, batch by batch, and
consolidated until the answer fits one context.

Every model call goes through a :class:`~bmlib.agents.BaseAgent`, so token
accounting, retries, JSON repair and provider routing are the ones the rest
of bmlib uses.  A model request that fails is retried with backoff
(:data:`CONTEXT_MODEL_ATTEMPTS`), with the attempts, backoff and retried HTTP
statuses of Rust's ``LlmContextModel``; which other failures are retried
differs (``_NOT_RETRIED``).
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from typing import Any, TypeVar

from bmlib.agents import BaseAgent, ChatStoppedError
from bmlib.agents.base import _TRUNCATION_STOP_REASONS
from bmlib.context_processor.base import IterativeContextProcessor, ProgressCallback
from bmlib.context_processor.data_types import (
    ConsolidatedItem,
    ExtractionResult,
    ProcessingConfig,
    ProcessingStoppedError,
)

logger = logging.getLogger(__name__)

#: A chunk of text with the relevance score a search assigned it.
ScoredChunk = tuple[str, float]

#: Confidence recorded when the model does not report one of its own.
DEFAULT_EXTRACTION_CONFIDENCE = 0.9

#: Tries per model call: one and two retries, as Rust's ``LlmContextModel``
#: makes and as many as the Anthropic and OpenAI SDKs make by default
#: (ollama-python retries nothing, #641).
CONTEXT_MODEL_ATTEMPTS = 3

#: Seconds before the first retry of a model call; each later one doubles it.
CONTEXT_MODEL_RETRY_BASE_DELAY = 1.0

#: Exceptions that can only mean bmlib is wrong — ``transparency``'s
#: ``_BUG_TYPES``, restated rather than imported, and held to agreeing by
#: ``tests/test_transparency.py``.
_BUG_TYPES: tuple[type[BaseException], ...] = (
    TypeError,
    AttributeError,
    NameError,
    KeyError,
    IndexError,
)

#: What a retry cannot cure: a stop, a ``ValueError`` (an unknown provider, a
#: bad argument, and every :class:`~bmlib.agents.ChatJsonError`, which
#: ``chat_json`` has already re-asked for), a missing SDK, an unsupported
#: feature, and a bmlib defect.  Anything else a provider raises is taken for
#: a failed request, except an HTTP status outside :data:`_RETRIED_STATUSES`
#: (#646), where Rust's ``LlmContextModel`` retries only
#: ``ChatError::is_retryable``.
_NOT_RETRIED: tuple[type[BaseException], ...] = (
    ChatStoppedError,
    ValueError,
    ImportError,
    NotImplementedError,
    *_BUG_TYPES,
)

#: The HTTP statuses a retry may cure: a request timeout, rate limiting,
#: server failures and Anthropic's 529 "overloaded" — Rust's
#: ``RETRYABLE_STATUSES``, held to agreeing by
#: ``tests/fixtures/pico_stop_num_ctx.json``.  A 401 (bad key), 404 (no such
#: model) or 400 (prompt too long) will be refused again (#646).
_RETRIED_STATUSES = frozenset({408, 429, 500, 502, 503, 504, 529})


def _is_refusal(exc: BaseException) -> bool:
    """Whether *exc* is an HTTP answer no retry changes (#646).

    Every built-in provider's SDK exception for an HTTP answer carries its
    ``status_code`` (``anthropic``/``openai`` ``APIStatusError``,
    ``ollama.ResponseError``); a connection failure or timeout carries none
    and stays retried.  ollama-python's ``-1`` (no HTTP status) is no refusal.
    """
    status = getattr(exc, "status_code", None)
    return isinstance(status, int) and status >= 400 and status not in _RETRIED_STATUSES


_T = TypeVar("_T")

DEFAULT_EXTRACTION_PROMPT = """Extract the key information relevant to this query.

Query: {query}

Content:
{content}

INSTRUCTIONS:
- Focus on information directly addressing the query
- Preserve important details, facts, and evidence
- Be concise but do not lose relevant information
- Return the extracted information as plain text

Extracted Information:"""

DEFAULT_CONSOLIDATION_PROMPT = """Consolidate and synthesise these extracted passages.

Query: {query}

Previously Extracted Information:
{content}

INSTRUCTIONS:
- Merge overlapping or redundant information
- Preserve all unique relevant details
- Maintain logical organisation
- Return consolidated information as plain text

Consolidated Information:"""

STRUCTURED_EXTRACTION_PROMPT = """Extract the key information relevant to this query.

Query: {query}

Content:
{content}

INSTRUCTIONS:
- Focus on information directly addressing the query
- Preserve important details, facts, and evidence
- Assess your confidence in the extraction (0.0 to 1.0)

Response format (JSON only):
{
    "extracted_content": "The extracted information...",
    "confidence": 0.9,
    "key_findings": ["finding 1", "finding 2"]
}

Respond ONLY with valid JSON."""

_REQUIRED_PLACEHOLDERS = ("{query}", "{content}")

#: The placeholders :meth:`LLMChunkProcessor._render` fills, in one pass.
_PLACEHOLDER_PATTERN = re.compile(r"\{(query|content)\}")


def _is_scored_chunk(item: Any) -> bool:
    """True when *item* is a ``(text, score)`` pair.

    ``bool`` is rejected although it is an ``int``: ``("text", True)`` is a
    caller's mistake, and rendering it as ``score 1.00`` hides that.
    """
    if not (isinstance(item, tuple) and len(item) == 2):
        return False
    text, score = item
    return isinstance(text, str) and isinstance(score, (int, float)) and not isinstance(score, bool)


class LLMChunkProcessor(IterativeContextProcessor):
    """Summarise batches of text chunks with an LLM, consolidating recursively.

    Accepts plain strings or ``(text, score)`` tuples — the shape a semantic
    search returns. Chunks are rendered with their score so the model can
    weigh them, batched to fit, and summarised; if the summaries together
    still exceed one context they are summarised again, with a prompt that
    says so.

    Args:
        agent: The agent every model call runs through. It carries the
            model, the token accounting and the retry behaviour.
        extraction_prompt: Template used at level 0. Must contain
            ``{query}`` and ``{content}``. Defaults to
            :data:`DEFAULT_EXTRACTION_PROMPT`, or
            :data:`STRUCTURED_EXTRACTION_PROMPT` when *use_structured_output*.
        consolidation_prompt: Template used at every level above 0. Same
            requirement; defaults to :data:`DEFAULT_CONSOLIDATION_PROMPT`.
        config: Processing configuration.
        progress_callback: Called with each progress update.
        use_structured_output: Ask for JSON at level 0 and read the model's
            own confidence and key findings from it, through the agent's
            JSON repair and retry.
        temperature: Overrides the agent's temperature for these calls.
        max_tokens: Overrides the agent's token ceiling for these calls.

    Raises:
        ValueError: If either prompt template lacks a required placeholder.
    """

    def __init__(
        self,
        agent: BaseAgent,
        extraction_prompt: str | None = None,
        consolidation_prompt: str | None = None,
        config: ProcessingConfig | None = None,
        progress_callback: ProgressCallback | None = None,
        use_structured_output: bool = False,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> None:
        super().__init__(config=config, progress_callback=progress_callback)
        self.agent = agent
        self.use_structured_output = use_structured_output
        self.extraction_prompt = extraction_prompt or (
            STRUCTURED_EXTRACTION_PROMPT if use_structured_output else DEFAULT_EXTRACTION_PROMPT
        )
        self.consolidation_prompt = consolidation_prompt or DEFAULT_CONSOLIDATION_PROMPT
        self.temperature = temperature
        self.max_tokens = max_tokens

        self._validate_template(self.extraction_prompt, "extraction_prompt")
        self._validate_template(self.consolidation_prompt, "consolidation_prompt")

    @staticmethod
    def _validate_template(template: str, name: str) -> None:
        """Check a prompt template carries the placeholders that get filled.

        Args:
            template: The template to check.
            name: The parameter name, for the error message.

        Raises:
            ValueError: If a placeholder is missing.
        """
        for placeholder in _REQUIRED_PLACEHOLDERS:
            if placeholder not in template:
                raise ValueError(f"{name} must contain the {placeholder} placeholder")

    @staticmethod
    def _render(template: str, query: str, content: str) -> str:
        """Fill a prompt template.

        Substitution is by replacement, not :meth:`str.format`, so a
        template may contain literal braces — a JSON example, a regex, a
        LaTeX fragment — without doubling them.

        One pass, not two chained :meth:`str.replace` calls: a second pass
        runs over what the first substituted, so a query containing the
        literal ``{content}`` would have the whole batch spliced into it —
        doubling a prompt that was sized to fit exactly, which is the
        context overflow this module exists to prevent.
        """
        values = {"query": query, "content": content}
        return _PLACEHOLDER_PATTERN.sub(lambda match: values[match.group(1)], template)

    # --- Item rendering ---

    def format_item(self, item: Any, index: int) -> str:
        """Render a chunk, showing its search score when it has one.

        Args:
            item: A ``(text, score)`` tuple or a plain string.
            index: Position within the batch.

        Returns:
            The chunk's text under a one-line header.
        """
        if _is_scored_chunk(item):
            text, score = item
            return f"[Chunk {index + 1}, score {float(score):.2f}]\n{text}"

        if isinstance(item, str):
            return f"[Item {index + 1}]\n{item}"

        logger.warning("Rendering an item of unexpected type %s", type(item).__name__)
        return f"[Item {index + 1}]\n{item}"

    def format_consolidated_item(self, item: ConsolidatedItem, index: int) -> str:
        """Render a summary from the level below, naming the level it came from."""
        level = item.metadata.get("recursion_level", 0)
        return f"[Consolidated level {level}, item {index + 1}]\n{item.content}"

    def split_oversized_item(self, item: Any, max_chars: int, overlap: int = 0) -> list[Any]:
        """Split a chunk too long to fit, keeping its score on every piece.

        Args:
            item: The chunk to split.
            max_chars: Maximum characters of content per piece.
            overlap: Characters of overlap between pieces.

        Returns:
            Pieces of the same shape as the input.
        """
        if _is_scored_chunk(item):
            text, score = item
            return [(piece, score) for piece in self._split_string(text, max_chars, overlap)]

        return super().split_oversized_item(item, max_chars, overlap)

    # --- Extraction ---

    def extract_from_batch(
        self,
        batch_content: str,
        query: str,
        batch_metadata: dict[str, Any],
    ) -> ExtractionResult:
        """Summarise one batch with the model.

        Level 0 uses the extraction prompt; every level above uses the
        consolidation prompt, which asks the model to merge rather than
        extract.

        Args:
            batch_content: The batch's formatted content.
            query: The question guiding extraction.
            batch_metadata: Carries ``recursion_level`` and ``batch_index``.

        Returns:
            The model's answer, with its confidence when it reported one.

        Raises:
            RuntimeError: If the model call fails, or a plain completion
                stopped at the output ceiling. The base class records it
                against the batch.
            ProcessingStoppedError: The agent's stop is set (#642); the
                request was not sent.  The base class ends the run.
        """
        level = batch_metadata.get("recursion_level", 0)
        structured = self.use_structured_output and level == 0
        template = self.extraction_prompt if level == 0 else self.consolidation_prompt
        prompt = self._render(template, query, batch_content)
        label = f"context extraction (level {level}, batch {batch_metadata.get('batch_index', 0)})"

        try:
            if structured:
                content, confidence, findings = self._extract_structured(prompt, label)
            else:
                response = self._with_retries(
                    lambda: self.agent.chat(
                        [self.agent.user_msg(prompt)],
                        temperature=self.temperature,
                        max_tokens=self.max_tokens,
                    ),
                    label,
                )
                # A completion cut at the output ceiling is a lost passage, not
                # an answer; ``chat_json`` refuses it on the structured path.
                if response.stop_reason in _TRUNCATION_STOP_REASONS:
                    raise RuntimeError(
                        f"the response was truncated (stop_reason={response.stop_reason!r})"
                    )
                content = response.content.strip()
                confidence = DEFAULT_EXTRACTION_CONFIDENCE
                findings = []
        except ProcessingStoppedError:
            raise
        except ChatStoppedError as exc:
            raise ProcessingStoppedError(str(exc)) from exc
        except Exception as exc:
            raise RuntimeError(f"LLM extraction failed: {exc}") from exc

        metadata = dict(batch_metadata)
        if findings:
            metadata["key_findings"] = findings

        logger.debug(
            "Extracted %d chars from batch %s at level %d",
            len(content),
            batch_metadata.get("batch_index", 0),
            level,
        )
        return ExtractionResult(content=content, metadata=metadata, confidence=confidence)

    def _with_retries(self, call: Callable[[], _T], label: str) -> _T:
        """Run one model request, retrying a failed one with backoff (rule 7).

        Up to :data:`CONTEXT_MODEL_ATTEMPTS` tries, waiting
        :data:`CONTEXT_MODEL_RETRY_BASE_DELAY` seconds before the first retry
        and doubling after.  What no retry cures (``_NOT_RETRIED``, or an
        HTTP status outside :data:`_RETRIED_STATUSES`) is raised at once, and
        a failure while the agent's stop is set ends the run with no wait, as
        Rust's ``LlmContextModel`` does.

        Args:
            call: The model call.
            label: Names the batch in the log line.

        Returns:
            What *call* returned.

        Raises:
            Exception: The last failure, once the tries run out, or the first
                one no retry cures.
            ProcessingStoppedError: A request failed while the stop was set.
        """
        for attempt in range(1, CONTEXT_MODEL_ATTEMPTS + 1):
            try:
                return call()
            except _NOT_RETRIED:
                raise
            except Exception as exc:
                if _is_refusal(exc):
                    raise
                if self.agent.stop_requested():
                    raise ProcessingStoppedError(f"{label} failed after the stop: {exc}") from exc
                if attempt == CONTEXT_MODEL_ATTEMPTS:
                    raise
                delay = CONTEXT_MODEL_RETRY_BASE_DELAY * 2 ** (attempt - 1)
                logger.warning(
                    "Model call for %s failed (attempt %d/%d), retrying in %.0fs: %s",
                    label,
                    attempt,
                    CONTEXT_MODEL_ATTEMPTS,
                    delay,
                    exc,
                )
                time.sleep(delay)
        raise AssertionError("unreachable: the last attempt returns or raises")

    def _extract_structured(self, prompt: str, label: str) -> tuple[str, float, list[Any]]:
        """Run a JSON-mode extraction and read the model's own confidence.

        Only the request is retried, never the reading of its reply.

        Args:
            prompt: The rendered prompt.
            label: Retry context, so a failure names the batch it came from.

        Returns:
            ``(content, confidence, key_findings)``. An ``extracted_content``
            that is absent or not a string is ``("", 0.0, [])``, logged at
            WARNING.
        """
        parsed = self._with_retries(
            lambda: self.agent.chat_json(
                [self.agent.user_msg(prompt)],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                retry_context=label,
                require_dict=True,
            ),
            label,
        )
        raw_content = parsed.get("extracted_content")
        if not isinstance(raw_content, str):
            # ``str()`` stored ``null`` as the literal "None" at the model's
            # confidence (#570); empty content is dropped before consolidation.
            logger.warning(
                "Model reported extracted_content that is not text in %s (%s); "
                "reading it as no content",
                label,
                type(raw_content).__name__,
            )
            return "", 0.0, []
        content = raw_content.strip()
        try:
            confidence = float(parsed.get("confidence", DEFAULT_EXTRACTION_CONFIDENCE))
        except (TypeError, ValueError, OverflowError):  # OverflowError: float(10**400)
            logger.warning("Model reported an unusable confidence in %s; using the default", label)
            confidence = DEFAULT_EXTRACTION_CONFIDENCE
        # A model that reports 1.4 or -0.2 must not defeat
        # ``min_confidence_threshold`` or skew a weighted merge.
        confidence = min(1.0, max(0.0, confidence))
        findings = parsed.get("key_findings") or []
        return content, confidence, list(findings) if isinstance(findings, list) else []
