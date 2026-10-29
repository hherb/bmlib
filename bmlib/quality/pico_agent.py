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


"""PICO extraction with quotes verified against the paper.

A port of bmlibrarian's ``agents/pico_agent.py`` (StudyAnalyzer item 7; the
design is ``docs/superpowers/specs/2026-10-30-pico-agent-design.md``).  It
answers two questions, as two independent calls:

* :meth:`PicoAgent.check_suitability` — is this an intervention study with a
  PICO at all?  Reviews, case reports and editorials are not.
* :meth:`PicoAgent.extract` — the Population, Intervention, Comparison and
  Outcome, plus study type and sample size.

Unlike upstream, every part carries the passages that back it.  The model
copies quotes from the paper; each is located by
:func:`bmlib.citations.find_passage` in the text the caller passed, and the
**paper's** span is kept.  A part none of whose quotes is found is
:attr:`~PicoPartState.UNVERIFIED`, its description kept for a reader to judge;
a part the paper does not state is :attr:`~PicoPartState.NOT_REPORTED`, which
needs no quote.

Nothing is truncated: text too long for one context is condensed first by the
Cochrane assessor's map-reduce, and the quotes are still verified against the
original text, never the digest.  Nothing is discarded for low confidence.
"""

from __future__ import annotations

import logging
from typing import Any

from bmlib.citations import find_passage
from bmlib.quality._json_fields import as_bool, as_text
from bmlib.quality.cochrane_assessor import clamped_confidence
from bmlib.quality.pico_models import (
    PicoCitation,
    PicoExtraction,
    PicoPart,
    PicoPartState,
    PicoSuitability,
)

logger = logging.getLogger(__name__)

#: The four parts, in the order they are read, reported and averaged.
PICO_PARTS: tuple[str, ...] = ("population", "intervention", "comparison", "outcome")


# --- Reading a reply ---


def verify_quotes(
    quotes: object, text: str, min_similarity: float
) -> tuple[list[PicoCitation], list[str], int]:
    """Locate the model's quotes in *text*.

    Args:
        quotes: The reply's ``quotes`` value, as it arrived.
        text: The text the quotes were copied from; offsets index it.
        min_similarity: :func:`~bmlib.citations.find_passage`'s threshold.

    Returns:
        ``(citations, not_found, dropped)``: the paper's spans for the quotes
        found, in the order the quotes came and each span once; the quotes not
        found, verbatim; and how many were not usable text at all.  An absent
        ``quotes`` (``None``) drops nothing; any other value that is not a list
        counts as one dropped, as does a member that is not a string or is
        blank.
    """
    if quotes is None:
        return [], [], 0
    if not isinstance(quotes, list):
        logger.debug("Reading quotes as none: the answer was a %s", type(quotes).__name__)
        return [], [], 1

    citations: list[PicoCitation] = []
    seen: set[tuple[int, int]] = set()
    not_found: list[str] = []
    dropped = 0
    for quote in quotes:
        if not isinstance(quote, str) or not quote.strip():
            dropped += 1
            continue
        match = find_passage(quote, text, min_similarity)
        if match.start is None or match.end is None or match.text is None:
            not_found.append(quote)
            continue
        if (match.start, match.end) in seen:
            continue
        seen.add((match.start, match.end))
        citations.append(
            PicoCitation(
                start=match.start,
                end=match.end,
                text=match.text,
                similarity=match.similarity,
                exact=match.exact,
            )
        )
    return citations, not_found, dropped


def read_part(value: object, text: str, min_similarity: float) -> PicoPart:
    """Read one PICO part and verify its quotes.

    Raises:
        ValueError: When *value* is not an object carrying a string
            ``description`` — a part that cannot be shown at all.
    """
    if not isinstance(value, dict):
        raise ValueError(f"expected an object, got {type(value).__name__}")
    description = value.get("description")
    if not isinstance(description, str):
        raise ValueError(f"description is a {type(description).__name__}, not text")

    confidence = clamped_confidence(value.get("confidence"))
    # Only an explicit ``false`` is an absence: ``bool("no")`` is ``True``, and
    # a flag the model garbled must not hide a part it described.
    if as_bool(value.get("reported"), "reported") is False:
        return PicoPart(description, PicoPartState.NOT_REPORTED, confidence)

    citations, not_found, dropped = verify_quotes(value.get("quotes"), text, min_similarity)
    state = PicoPartState.VERIFIED if citations else PicoPartState.UNVERIFIED
    return PicoPart(description, state, confidence, citations, not_found, dropped)


def read_extraction(data: dict[str, Any], text: str, min_similarity: float) -> PicoExtraction:
    """Read an extraction reply, verifying every part's quotes against *text*.

    Raises:
        ValueError: When any of the four parts is missing or unusable; the
            message names it.
    """
    parts: dict[str, PicoPart] = {}
    for name in PICO_PARTS:
        try:
            parts[name] = read_part(data.get(name), text, min_similarity)
        except ValueError as exc:
            raise ValueError(f"{name}: {exc}") from None

    # A correctly identified absence must not pull the mean down.  The sum is
    # taken in PICO order so the Rust port reproduces it bit for bit.
    values = [
        part.confidence
        for part in parts.values()
        if part.state is not PicoPartState.NOT_REPORTED and part.confidence is not None
    ]
    overall = sum(values) / len(values) if values else None

    return PicoExtraction(
        population=parts["population"],
        intervention=parts["intervention"],
        comparison=parts["comparison"],
        outcome=parts["outcome"],
        study_type=as_text(data.get("study_type"), "study_type"),
        sample_size=as_text(data.get("sample_size"), "sample_size"),
        overall_confidence=overall,
    )


def read_suitability(data: dict[str, Any]) -> PicoSuitability:
    """Read a suitability reply.

    Raises:
        ValueError: When ``is_suitable`` is not a boolean — the one answer
            the check exists to give.
    """
    is_suitable = data.get("is_suitable")
    if not isinstance(is_suitable, bool):
        raise ValueError(f"is_suitable is a {type(is_suitable).__name__}, not a boolean")
    return PicoSuitability(
        is_suitable=is_suitable,
        is_intervention_study=as_bool(data.get("is_intervention_study"), "is_intervention_study"),
        has_comparison=as_bool(data.get("has_comparison"), "has_comparison"),
        study_type=as_text(data.get("study_type"), "study_type"),
        confidence=clamped_confidence(data.get("confidence")),
        rationale=as_text(data.get("rationale"), "rationale"),
    )
