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

"""Reading a PRISMA 2020 part's reply, under the strict-evidence rule.

The model judges each checklist item of a part and quotes the paper for it.
Every quote is located by :func:`~bmlib.quality.pico_agent.verify_quotes`, and
the judgement shown is the model's only when a quote backs it: a claim of
``reported``, ``partly_reported``, ``reported_elsewhere`` or ``not_applicable``
without a located quote is shown as ``not_reported`` and flagged ``unbacked``
(:func:`shown_judgement`).  The rule is deliberately one-way.  A
``not_reported`` item is never upgraded because a quote was located (PICO's
#577 does upgrade): a reviewer reading the item sees the model's own word
beside the paper's span.

Rust's ``rust/bmlib/src/quality/prisma2020_models.rs`` is the same.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from bmlib.quality.cochrane_assessor import clamped_confidence
from bmlib.quality.pico_agent import verify_quotes
from bmlib.quality.pico_models import PicoCitation, PicoContradictedQuote
from bmlib.quality.prisma2020_checklist import item as checklist_item

logger = logging.getLogger(__name__)


class Judgement(StrEnum):
    """How fully the paper reports a checklist item (the reply's vocabulary)."""

    REPORTED = "reported"
    PARTLY_REPORTED = "partly_reported"
    NOT_REPORTED = "not_reported"
    REPORTED_ELSEWHERE = "reported_elsewhere"
    NOT_APPLICABLE = "not_applicable"


@dataclass
class ItemAssessment:
    """One checklist item as shown: the model's claim, and what the evidence allows.

    ``model_judgement`` is what the model said, ``judgement`` what is shown;
    ``unbacked`` is true when they differ because no quote was located.
    """

    id: str
    model_judgement: Judgement
    judgement: Judgement
    unbacked: bool
    explanation: str
    citations: list[PicoCitation] = field(default_factory=list)
    quotes_not_found: list[str] = field(default_factory=list)
    quotes_contradicted: list[PicoContradictedQuote] = field(default_factory=list)
    quotes_dropped: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-ready dict."""
        return {
            "id": self.id,
            "model_judgement": self.model_judgement.value,
            "judgement": self.judgement.value,
            "unbacked": self.unbacked,
            "explanation": self.explanation,
            "citations": [c.to_dict() for c in self.citations],
            "quotes_not_found": list(self.quotes_not_found),
            "quotes_contradicted": [q.to_dict() for q in self.quotes_contradicted],
            "quotes_dropped": self.quotes_dropped,
        }


@dataclass
class PartAssessment:
    """The items of one part, the asked ids the reply left out, and run notes."""

    items: list[ItemAssessment] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    condensed_from_chars: int | None = None
    condensation_status: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-ready dict."""
        return {
            "items": [i.to_dict() for i in self.items],
            "missing": list(self.missing),
            "condensed_from_chars": self.condensed_from_chars,
            "condensation_status": self.condensation_status,
            "notes": list(self.notes),
        }


@dataclass
class Suitability:
    """Whether the paper is a systematic review or meta-analysis PRISMA applies to.

    ``variant`` is the model's own string, kept as given.
    """

    is_systematic_review: bool
    is_meta_analysis: bool
    is_suitable: bool
    document_type: str
    variant: str | None
    rationale: str
    confidence: float
    condensed_from_chars: int | None = None
    condensation_status: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-ready dict."""
        return {
            "is_systematic_review": self.is_systematic_review,
            "is_meta_analysis": self.is_meta_analysis,
            "is_suitable": self.is_suitable,
            "document_type": self.document_type,
            "variant": self.variant,
            "rationale": self.rationale,
            "confidence": self.confidence,
            "condensed_from_chars": self.condensed_from_chars,
            "condensation_status": self.condensation_status,
            "notes": list(self.notes),
        }


def shown_judgement(
    model: Judgement, located: int, may_be_not_applicable: bool
) -> tuple[Judgement, bool]:
    """The judgement to show, and whether the model's claim was unbacked.

    ``not_reported`` stands as it is.  Any other claim with no located quote
    (``located == 0``), or ``not_applicable`` on an item that may not be, is
    shown as ``not_reported`` and flagged.
    """
    if model is Judgement.NOT_REPORTED:
        return model, False
    if located == 0 or (model is Judgement.NOT_APPLICABLE and not may_be_not_applicable):
        return Judgement.NOT_REPORTED, True
    return model, False


def _text(value: object, field_name: str) -> str:
    """A reply's text field, ``""`` when absent; anything else is unreadable."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} is a {type(value).__name__}, not text")
    return value


def read_part_reply(
    data: dict[str, Any], asked: list[str], text: str, min_similarity: float
) -> PartAssessment:
    """Read a part's reply and verify every item's quotes against *text*.

    Items come back in the order of *asked*.  An item the reply lacks is listed
    in ``missing``; one it adds (not in *asked*) is dropped with a WARNING.  An
    integer id is read as its decimal digits (``7`` as ``"7"``).

    Raises:
        ValueError: When ``items`` is not a list, an entry is not an object, an
            id is absent or repeated, or a judgement is not one of the five
            strings; the message names it, so a re-ask can.
    """
    entries = data.get("items")
    if not isinstance(entries, list):
        raise ValueError(f"items is a {type(entries).__name__}, not a list")
    by_id: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError(f"an item is a {type(entry).__name__}, not an object")
        item_id = entry.get("id")
        # ``"id": 7`` reads as "7" (ruling 8): a re-ask for that slip would
        # cost the whole part.  ``bool`` is an ``int`` to Python, not an id.
        if isinstance(item_id, int) and not isinstance(item_id, bool):
            item_id = str(item_id)
        if not isinstance(item_id, str):
            raise ValueError(f"an item's id is a {type(item_id).__name__}, not text")
        if item_id in by_id:
            raise ValueError(f"duplicate item id {item_id!r}")
        by_id[item_id] = entry
    for extra in by_id.keys() - set(asked):
        logger.warning("PRISMA reply has item %r, which was not asked; ignored", extra)

    part = PartAssessment()
    for item_id in asked:
        row = checklist_item(item_id)
        if row is None:
            raise ValueError(f"item {item_id!r} is not on the checklist")
        entry = by_id.get(item_id)
        if entry is None:
            part.missing.append(item_id)
            continue
        raw = str(entry.get("judgement"))
        try:
            claimed = Judgement(raw)
        except ValueError:
            raise ValueError(f"item {item_id}: unknown judgement {raw!r}") from None
        citations, not_found, contradicted, dropped = verify_quotes(
            entry.get("quotes"), text, min_similarity
        )
        judgement, unbacked = shown_judgement(claimed, len(citations), row.may_be_not_applicable)
        part.items.append(
            ItemAssessment(
                id=row.id,
                model_judgement=claimed,
                judgement=judgement,
                unbacked=unbacked,
                explanation=_text(entry.get("explanation"), f"item {item_id}: explanation"),
                citations=citations,
                quotes_not_found=not_found,
                quotes_contradicted=contradicted,
                quotes_dropped=dropped,
            )
        )
    return part


def _flag(data: dict[str, Any], name: str) -> bool:
    """A required boolean of the suitability reply."""
    value = data.get(name)
    if not isinstance(value, bool):
        raise ValueError(f"{name} is a {type(value).__name__}, not a boolean")
    return value


def read_suitability(data: dict[str, Any]) -> Suitability:
    """Read a suitability reply; suitable means a systematic review or a meta-analysis.

    Raises:
        ValueError: When ``is_systematic_review`` or ``is_meta_analysis`` is not
            a boolean, or a text field is of another type.
    """
    systematic = _flag(data, "is_systematic_review")
    meta = _flag(data, "is_meta_analysis")
    variant = data.get("variant")
    return Suitability(
        is_systematic_review=systematic,
        is_meta_analysis=meta,
        is_suitable=systematic or meta,
        document_type=_text(data.get("document_type"), "document_type"),
        variant=None if variant is None else _text(variant, "variant"),
        rationale=_text(data.get("rationale"), "rationale"),
        confidence=clamped_confidence(data.get("confidence")) or 0.0,
    )
