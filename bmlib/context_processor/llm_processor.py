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
of bmlib uses.
"""

from __future__ import annotations

import logging
from typing import Any

from bmlib.agents import BaseAgent
from bmlib.context_processor.base import IterativeContextProcessor, ProgressCallback
from bmlib.context_processor.data_types import (
    ConsolidatedItem,
    ExtractionResult,
    ProcessingConfig,
)

logger = logging.getLogger(__name__)

#: A chunk of text with the relevance score a search assigned it.
ScoredChunk = tuple[str, float]

#: Confidence recorded when the model does not report one of its own.
DEFAULT_EXTRACTION_CONFIDENCE = 0.9

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
        """
        return template.replace("{query}", query).replace("{content}", content)

    # --- Item rendering ---

    def format_item(self, item: Any, index: int) -> str:
        """Render a chunk, showing its search score when it has one.

        Args:
            item: A ``(text, score)`` tuple or a plain string.
            index: Position within the batch.

        Returns:
            The chunk's text under a one-line header.
        """
        if isinstance(item, tuple) and len(item) == 2:
            text, score = item
            if isinstance(text, str) and isinstance(score, (int, float)):
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
        if isinstance(item, tuple) and len(item) == 2:
            text, score = item
            if isinstance(text, str) and isinstance(score, (int, float)):
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
            RuntimeError: If the model call fails. The base class records it
                against the batch.
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
                response = self.agent.chat(
                    [self.agent.user_msg(prompt)],
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                )
                content = response.content.strip()
                confidence = DEFAULT_EXTRACTION_CONFIDENCE
                findings = []
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

    def _extract_structured(self, prompt: str, label: str) -> tuple[str, float, list[Any]]:
        """Run a JSON-mode extraction and read the model's own confidence.

        Args:
            prompt: The rendered prompt.
            label: Retry context, so a failure names the batch it came from.

        Returns:
            ``(content, confidence, key_findings)``.
        """
        parsed = self.agent.chat_json(
            [self.agent.user_msg(prompt)],
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            retry_context=label,
            require_dict=True,
        )
        content = str(parsed.get("extracted_content", "")).strip()
        try:
            confidence = float(parsed.get("confidence", DEFAULT_EXTRACTION_CONFIDENCE))
        except (TypeError, ValueError):
            logger.warning("Model reported an unusable confidence in %s; using the default", label)
            confidence = DEFAULT_EXTRACTION_CONFIDENCE
        # A model that reports 1.4 or -0.2 must not defeat
        # ``min_confidence_threshold`` or skew a weighted merge.
        confidence = min(1.0, max(0.0, confidence))
        findings = parsed.get("key_findings") or []
        return content, confidence, list(findings) if isinstance(findings, list) else []
