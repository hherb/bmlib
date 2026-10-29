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


"""Condense a paper too long for one context, for an agent that judges it once.

Taken out of :class:`~bmlib.quality.cochrane_assessor.CochraneAssessor` so the
PICO agent runs the same map-reduce with its own prompts.  Private: the rules
are the two agents', not a public API.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from bmlib.context_processor import LLMChunkProcessor, ProcessingConfig, ProcessingStatus

if TYPE_CHECKING:
    from bmlib.agents.base import BaseAgent

logger = logging.getLogger(__name__)


def condense_text(
    agent: BaseAgent,
    text: str,
    label: str,
    config: ProcessingConfig,
    *,
    extraction_prompt: str,
    consolidation_prompt: str,
    query: str,
    stage: str,
) -> tuple[str, list[str], ProcessingStatus] | None:
    """Reduce oversized text to an evidence digest that fits one context.

    Runs :class:`~bmlib.context_processor.LLMChunkProcessor` with *agent*, so
    token accounting, retries and JSON repair are the ones the rest of bmlib
    uses.  The judgement is made once, afterwards, over the digest — no
    per-chunk judgements are made and none are merged.

    Args:
        agent: The agent whose model condenses.
        text: The oversized text.
        label: Names the paper in log lines.
        config: The budget; ``max_context_chars`` is what the digest must fit.
        extraction_prompt: The map prompt.
        consolidation_prompt: The reduce prompt.
        query: What the evidence is gathered for.
        stage: Names what the digest is for in the note a degraded run
            carries (``"assessment"``, ``"extraction"``), so an agent's
            stored note keeps its wording.

    Returns:
        ``(digest, notes, status)``, or ``None`` when the run failed,
        produced nothing to judge, or produced a digest that still does
        not fit ``config.max_context_chars``.
    """
    processor = LLMChunkProcessor(
        agent=agent,
        extraction_prompt=extraction_prompt,
        consolidation_prompt=consolidation_prompt,
        config=config,
    )
    result = processor.process([text], query=query)

    if result.status is ProcessingStatus.FAILED:
        logger.error("Could not condense the text of %s: %s", label, result.error_message)
        return None

    digest = result.final_result.content.strip()
    if not digest:
        # A prompt over an empty string returns a confident judgement of no
        # paper at all.
        logger.error("Condensing the text of %s produced an empty digest", label)
        return None

    # ``result.status`` says how the harness's own run ended — TRUNCATED
    # means the recursion ceiling was hit, PARTIAL means a batch failed —
    # it does not say whether the digest that came out the other end
    # actually fits.  In particular TRUNCATED names the recursion
    # ceiling, not the content size: ``_merge_results()`` returns
    # whatever the last level held once ``max_recursion_depth`` is
    # reached, oversized or not.  So this checks the same thing
    # ``context_processor`` checks about itself — the measured length of
    # what is about to be sent — rather than trusting the status to
    # imply it; a status check would not have caught a digest that
    # overflows the budget it was supposed to fit.
    if len(digest) > config.max_context_chars:
        logger.error(
            "Condensing the text of %s produced a %d-char digest that still "
            "exceeds the %d-char budget (status: %s); refusing to judge it",
            label,
            len(digest),
            config.max_context_chars,
            result.status.value,
        )
        return None

    notes: list[str] = []
    if result.status is not ProcessingStatus.COMPLETED:
        notes.append(
            f"Source text was condensed before {stage}; the condensation "
            f"finished with status {result.status.value}, so the digest may be "
            f"incomplete."
        )
    return digest, notes, result.status
