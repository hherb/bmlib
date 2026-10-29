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


"""Data models for the PICO agent (:mod:`bmlib.quality.pico_agent`).

A PICO breakdown names a study's Population, Intervention, Comparison and
Outcome.  Each of the four is a :class:`PicoPart` carrying the model's
description and the passages of the paper that back it — located by
:func:`bmlib.citations.find_passage`, so a citation is the paper's own span
and wording, never the model's copy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PicoPartState(Enum):
    """What the paper was found to say about one PICO part."""

    #: At least one of the model's quotes was found in the paper.
    VERIFIED = "verified"
    #: The model described the part, but none of its quotes was found (or it
    #: gave none).  The description is kept for a reader to judge.
    UNVERIFIED = "unverified"
    #: The model says the paper does not state this part.  An honest absence,
    #: which needs no quote.
    NOT_REPORTED = "not_reported"


class PicoErrorKind(Enum):
    """Why a PICO call failed: each calls for a different remedy."""

    #: The title and text were both empty, so no model call was made.
    EMPTY_INPUT = "empty_input"
    #: The provider was unreachable or refused the request; retrying may help.
    REQUEST_FAILED = "request_failed"
    #: The reply hit the output ceiling; raise ``max_tokens``.
    TRUNCATED = "truncated"
    #: The reply was empty, did not parse, or lacked a required field; another
    #: model may do better.
    UNUSABLE_REPLY = "unusable_reply"
    #: The text was too long for one context and could not be condensed to fit.
    CONDENSATION_FAILED = "condensation_failed"


class PicoError(Exception):
    """A PICO call that could not produce a result.

    Attributes:
        kind: Why, as a :class:`PicoErrorKind`.
    """

    def __init__(self, kind: PicoErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind

    def __reduce__(self) -> tuple[Any, ...]:
        # Exception's default pickles ``args`` alone, which would drop ``kind``.
        return (type(self), (self.kind, str(self)))


@dataclass(frozen=True)
class PicoCitation:
    """A passage of the paper backing a PICO part.

    Offsets are code points into the ``text`` the caller passed; ``text`` here
    is the paper's own wording of the span.
    """

    start: int
    end: int
    text: str
    similarity: float
    exact: bool

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-ready dict."""
        return {
            "start": self.start,
            "end": self.end,
            "text": self.text,
            "similarity": self.similarity,
            "exact": self.exact,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PicoCitation:
        """Rebuild from :meth:`to_dict` output."""
        return cls(
            start=data["start"],
            end=data["end"],
            text=data["text"],
            similarity=data["similarity"],
            exact=data["exact"],
        )


@dataclass
class PicoPart:
    """One of the four PICO parts.

    Attributes:
        description: The model's description, kept whatever the state.
        state: Whether the paper was found to back it.
        confidence: The model's confidence, clamped to 0–1; ``None`` when it
            gave none it could be read as.
        citations: The quotes found in the paper, as the paper's spans.
        quotes_not_found: The model's quotes that were not found, verbatim.
        quotes_dropped: Quotes that were not usable text at all (not a
            string, blank, or a ``quotes`` value that was not a list).
    """

    description: str
    state: PicoPartState
    confidence: float | None
    citations: list[PicoCitation] = field(default_factory=list)
    quotes_not_found: list[str] = field(default_factory=list)
    quotes_dropped: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-ready dict."""
        return {
            "description": self.description,
            "state": self.state.value,
            "confidence": self.confidence,
            "citations": [citation.to_dict() for citation in self.citations],
            "quotes_not_found": list(self.quotes_not_found),
            "quotes_dropped": self.quotes_dropped,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PicoPart:
        """Rebuild from :meth:`to_dict` output."""
        return cls(
            description=data["description"],
            state=PicoPartState(data["state"]),
            confidence=data.get("confidence"),
            citations=[PicoCitation.from_dict(c) for c in data.get("citations", [])],
            quotes_not_found=list(data.get("quotes_not_found", [])),
            quotes_dropped=data.get("quotes_dropped", 0),
        )


@dataclass
class PicoExtraction:
    """A study's PICO breakdown.

    Attributes:
        population: Who was studied.
        intervention: What was done to them.
        comparison: What it was compared against.
        outcome: What was measured.
        study_type: The design, as the model named it; not verified.
        sample_size: The number of participants, as the model gave it; not
            verified.
        overall_confidence: The mean confidence of the parts that are
            reported and carry one; ``None`` when none does.
        condensed_from_chars: The text's length when it was condensed before
            extraction; ``None`` when it went to the model whole.
        condensation_status: The ``ProcessingStatus`` value the condensation
            finished with; ``None`` when the text was not condensed.
        notes: Notes on how the extraction was made (a degraded condensation).
    """

    population: PicoPart
    intervention: PicoPart
    comparison: PicoPart
    outcome: PicoPart
    study_type: str | None
    sample_size: str | None
    overall_confidence: float | None
    condensed_from_chars: int | None = None
    condensation_status: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-ready dict."""
        return {
            "population": self.population.to_dict(),
            "intervention": self.intervention.to_dict(),
            "comparison": self.comparison.to_dict(),
            "outcome": self.outcome.to_dict(),
            "study_type": self.study_type,
            "sample_size": self.sample_size,
            "overall_confidence": self.overall_confidence,
            "condensed_from_chars": self.condensed_from_chars,
            "condensation_status": self.condensation_status,
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PicoExtraction:
        """Rebuild from :meth:`to_dict` output."""
        return cls(
            population=PicoPart.from_dict(data["population"]),
            intervention=PicoPart.from_dict(data["intervention"]),
            comparison=PicoPart.from_dict(data["comparison"]),
            outcome=PicoPart.from_dict(data["outcome"]),
            study_type=data.get("study_type"),
            sample_size=data.get("sample_size"),
            overall_confidence=data.get("overall_confidence"),
            condensed_from_chars=data.get("condensed_from_chars"),
            condensation_status=data.get("condensation_status"),
            notes=list(data.get("notes", [])),
        )


@dataclass
class PicoSuitability:
    """Whether a paper is an intervention study with a PICO to extract.

    Only ``is_suitable`` is required of the model; every other field is
    ``None`` when it gave none that could be read.

    Attributes:
        is_suitable: Whether a PICO extraction applies.
        is_intervention_study: Whether the study tests an intervention.
        has_comparison: Whether it has a control or comparison group.
        study_type: The design, as the model named it.
        confidence: The model's confidence, clamped to 0–1.
        rationale: The model's explanation.
        condensed_from_chars: As for :class:`PicoExtraction`.
        condensation_status: As for :class:`PicoExtraction`.
        notes: As for :class:`PicoExtraction`.
    """

    is_suitable: bool
    is_intervention_study: bool | None
    has_comparison: bool | None
    study_type: str | None
    confidence: float | None
    rationale: str | None
    condensed_from_chars: int | None = None
    condensation_status: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-ready dict."""
        return {
            "is_suitable": self.is_suitable,
            "is_intervention_study": self.is_intervention_study,
            "has_comparison": self.has_comparison,
            "study_type": self.study_type,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "condensed_from_chars": self.condensed_from_chars,
            "condensation_status": self.condensation_status,
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PicoSuitability:
        """Rebuild from :meth:`to_dict` output."""
        return cls(
            is_suitable=data["is_suitable"],
            is_intervention_study=data.get("is_intervention_study"),
            has_comparison=data.get("has_comparison"),
            study_type=data.get("study_type"),
            confidence=data.get("confidence"),
            rationale=data.get("rationale"),
            condensed_from_chars=data.get("condensed_from_chars"),
            condensation_status=data.get("condensation_status"),
            notes=list(data.get("notes", [])),
        )
