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

from bmlib.context_processor import (
    LLMChunkProcessor,
    ModelCallError,
    ProcessingConfig,
    ProcessingResult,
    ProcessingStatus,
    summarise_batch_errors,
)
from bmlib.context_processor.llm_processor import _BUG_TYPES, _is_refusal

if TYPE_CHECKING:
    from bmlib.agents.base import BaseAgent

logger = logging.getLogger(__name__)

#: What a request may raise that is no failed request: a bmlib defect and a
#: provider SDK that will not import.  The PICO agent's
#: ``_NOT_A_REQUEST_FAILURE``, restated rather than imported (that module
#: imports this one), and held to agreeing by ``tests/test_pico_agent.py``.
_NOT_A_REQUEST_FAILURE: tuple[type[BaseException], ...] = (*_BUG_TYPES, ImportError)


def is_retryable_request(exc: BaseException) -> bool:
    """Whether a retry may cure the failed request *exc* (#646, #659).

    A refusal or an unreadable answer (``ValueError``, as Rust's
    ``ChatError::Malformed``) fails again; anything else may not.
    """
    return not isinstance(exc, ValueError) and not _is_refusal(exc)


def _failure_cause(result: ProcessingResult) -> BaseException | None:
    """What a failed run's report should name as its cause (#616), or ``None``.

    Worst first, since several batches can fail differently and the last is
    no more the cause than the first: a bmlib defect raised anywhere in the
    run, then a request no retry cures, then one a retry may; among equals,
    the last.  ``None`` when no request or defect failed it (a truncated
    pass, an item too large for its strategy).
    """
    candidates = list(result.batch_exceptions)
    if result.error is not None and not any(result.error is exc for exc in candidates):
        candidates.append(result.error)
    worst: BaseException | None = None
    worst_rank = 3
    for exc in candidates:
        cause = exc.__cause__ if isinstance(exc, ModelCallError) else exc
        if isinstance(cause, _NOT_A_REQUEST_FAILURE):
            rank = 0
        elif isinstance(exc, ModelCallError) and cause is not None:
            rank = 2 if is_retryable_request(cause) else 1
        else:
            continue
        if rank <= worst_rank:
            worst, worst_rank = cause, rank
    return worst


class CondensationRefusedError(Exception):
    """The map-reduce produced no digest an agent may judge; the message says why.

    The reason is the caller's to report: a provider failure and a digest
    that does not fit call for different remedies.  When a failed request or
    a bmlib defect failed the run, the error is chained (``__cause__``) to
    what was raised (#616), the worst when several were, so the caller can
    report it as it reports the same failure on a short paper.
    """


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
) -> tuple[str, list[str], ProcessingStatus]:
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
            carries (e.g. ``"assessment"``, ``"extraction"``, ``"the
            suitability check"``), so an agent's stored note keeps its wording.

    Returns:
        ``(digest, notes, status)``.

    Raises:
        CondensationRefusedError: The run failed (a provider failure during the
            map-reduce included, chained to what was raised), produced
            nothing to judge, or produced a digest that still does not fit
            ``config.max_context_chars``.
        ProcessingStoppedError: The agent's stop was set; the run ended there.
    """
    processor = LLMChunkProcessor(
        agent=agent,
        extraction_prompt=extraction_prompt,
        consolidation_prompt=consolidation_prompt,
        config=config,
    )
    result = processor.process([text], query=query)

    if result.status is ProcessingStatus.FAILED:
        reason = f"the map-reduce failed: {result.error_message}"
        logger.warning("Could not condense the text of %s: %s", label, reason)
        raise CondensationRefusedError(reason) from _failure_cause(result)

    digest = result.final_result.content.strip()
    if not digest:
        # A prompt over an empty string returns a confident judgement of no
        # paper at all.
        reason = "the map-reduce produced an empty digest"
        logger.warning("Could not condense the text of %s: %s", label, reason)
        raise CondensationRefusedError(reason)

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
        reason = (
            f"the digest is {len(digest)} characters, over the "
            f"{config.max_context_chars}-character budget (status: {result.status.value})"
        )
        logger.warning("Could not condense the text of %s: %s", label, reason)
        raise CondensationRefusedError(reason)

    notes: list[str] = []
    if result.status is not ProcessingStatus.COMPLETED:
        # The cause is named (#658): a bad key and an outage call for different remedies.
        causes = summarise_batch_errors(result.batch_errors)
        notes.append(
            f"Source text was condensed before {stage}; the condensation "
            f"finished with status {result.status.value}, so the digest may be "
            f"incomplete{f' ({causes})' if causes else ''}."
        )
    return digest, notes, result.status
