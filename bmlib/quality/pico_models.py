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

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from bmlib.citations import PassageContradiction, PassageMatch


class PicoPartState(Enum):
    """What the paper was found to say about one PICO part."""

    #: At least one of the model's quotes was found in the paper.
    VERIFIED = "verified"
    #: The model described the part, but none of its quotes was found (or it
    #: gave none).  The description is kept for a reader to judge.
    UNVERIFIED = "unverified"
    #: The model described the part, none of its quotes was found, and at
    #: least one is a near-verbatim copy of the paper with a number, a
    #: negation or a direction changed (#647): the model misreported the
    #: paper rather than failing to quote it.
    CONTRADICTED = "contradicted"
    #: The model says the paper does not state this part.  An honest absence,
    #: which needs no quote.
    NOT_REPORTED = "not_reported"


class PicoErrorKind(Enum):
    """Why a PICO call failed: each calls for a different remedy."""

    #: The title and text were both empty, so no model call was made.
    EMPTY_INPUT = "empty_input"
    #: The request raised: the provider was unreachable or refused it.
    #: :attr:`PicoError.retryable` says whether retrying may help (#659).  A
    #: request the condensation of a long text sent is this too when it failed
    #: the map-reduce (#616), the message saying so.  A bmlib defect, an
    #: unknown provider or a missing SDK is not this: those propagate
    #: unwrapped (#573).
    REQUEST_FAILED = "request_failed"
    #: The reply hit the output ceiling; raise ``max_tokens``.
    TRUNCATED = "truncated"
    #: The reply was empty, did not parse, was not a JSON object, or lacked a
    #: required field; another model may do better.
    UNUSABLE_REPLY = "unusable_reply"
    #: The text was too long for one context and the map-reduce condensing it
    #: produced no digest that fits, though no request failed it (that is
    #: ``REQUEST_FAILED``, #616).  The message says why: a truncated pass, an
    #: item too large for the oversized strategy, every item skipped, an empty
    #: digest, or one still over the budget (a larger budget or
    #: ``max_tokens``, or another model).
    CONDENSATION_FAILED = "condensation_failed"
    #: The caller's stop was set: the call ended before its next request
    #: (#642).  A request under way that failed, or a condensation that
    #: finished (its digest may be partial), is reported as this instead, the
    #: failure chained as the cause; a request that succeeded returns its result.
    STOPPED = "stopped"


class PicoError(Exception):
    """A PICO call that could not produce a result.

    Attributes:
        kind: Why, as a :class:`PicoErrorKind`.
        retryable: Whether sending the request again may succeed (#659): a
            :attr:`~PicoErrorKind.REQUEST_FAILED` whose cause was a connection
            failure, a timeout or an HTTP status a retry may cure (408, 429,
            500, 502, 503, 504, 529).  ``False`` for a 401, a 404, a reply
            that could not be read, and every other kind.
        status: The HTTP status the server answered a ``REQUEST_FAILED``
            request with (a 401 or 403 is a refused key, a 404 no such model),
            ``None`` where no HTTP answer came, and for every other kind
            (#662).

    Raises:
        ValueError: ``retryable`` or ``status`` given for a kind other than
            ``REQUEST_FAILED``, which Rust's ``PicoError`` cannot express.
    """

    def __init__(
        self,
        kind: PicoErrorKind,
        message: str,
        retryable: bool = False,
        status: int | None = None,
    ) -> None:
        if kind is not PicoErrorKind.REQUEST_FAILED and (retryable or status is not None):
            raise ValueError(
                f"only a request_failed PicoError carries retryable or status, not {kind.value}"
            )
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable
        self.status = status

    def __reduce__(self) -> tuple[Any, ...]:
        # Exception's default rebuilds with ``cls(*self.args)`` — here
        # ``PicoError(message)``, which lacks ``kind`` and raises TypeError.
        return (type(self), (self.kind, str(self), self.retryable, self.status))


def _read(data: object, key: str, kinds: type | tuple[type, ...], *, nullable: bool = False) -> Any:
    """Read one field of a stored dict as its annotated type, or refuse it.

    ``from_dict`` reads what ``to_dict`` wrote, so anything else is a corrupt
    record: refused, naming the field, rather than coerced into a value the
    record never held (``bool("no")`` is ``True``; ``list("abc")`` is three
    quotes).  A ``bool`` is never a number here, though Python counts it one.
    """
    if not isinstance(data, dict):
        raise ValueError(f"expected an object holding {key!r}, got {type(data).__name__}")
    value = data.get(key)
    if value is None and nullable:
        return None
    wanted = kinds if isinstance(kinds, tuple) else (kinds,)
    if isinstance(value, bool) and bool not in wanted or not isinstance(value, wanted):
        raise ValueError(
            f"{key!r} is a {type(value).__name__}, not {' or '.join(k.__name__ for k in wanted)}"
        )
    return value


def _read_text_list(data: dict[str, Any], key: str) -> list[str]:
    """Read a stored list of strings, refusing any other shape."""
    items = _read(data, key, list)
    if not all(isinstance(item, str) for item in items):
        raise ValueError(f"{key!r} holds a member that is not text")
    return list(items)


def _check_confidence(confidence: object, what: str) -> None:
    """Refuse a confidence no reader produces: not ``None`` nor finite in 0–1."""
    if confidence is None:
        return
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, int | float)
        or not math.isfinite(confidence)
        or not 0.0 <= confidence <= 1.0
    ):
        raise ValueError(
            f"{what} confidence must be None or a number in [0, 1], got {confidence!r}"
        )


@dataclass(frozen=True)
class PicoCitation:
    """A passage of the paper backing a PICO part.

    Offsets are code points into the ``text`` the caller passed; ``text`` here
    is the paper's own wording of the span.  An accepted
    :class:`~bmlib.citations.PassageMatch`, so its rules hold: a non-empty
    span as long as ``text``, a similarity in [0, 1], and ``exact`` only at
    1.0.

    Raises:
        ValueError: On construction, when one of those rules is broken.
    """

    start: int
    end: int
    text: str
    similarity: float
    exact: bool

    def __post_init__(self) -> None:
        PassageMatch(self.similarity, self.start, self.end, self.text, self.exact)

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
        """Rebuild from :meth:`to_dict` output.

        Raises:
            ValueError: A field is absent or of the wrong type, naming it, or
                the span breaks a rule above.
        """
        return cls(
            start=_read(data, "start", int),
            end=_read(data, "end", int),
            text=_read(data, "text", str),
            similarity=float(_read(data, "similarity", (int, float))),
            exact=_read(data, "exact", bool),
        )


@dataclass(frozen=True)
class PicoContradictedQuote:
    """A quote of the model's that the paper contradicts (#647).

    Its best window in the paper reached the similarity threshold but differs
    in a number, a negation or a direction, so
    :func:`~bmlib.citations.find_passage` rejected it.

    Raises:
        ValueError: On construction, when ``contradiction`` is not a
            :class:`~bmlib.citations.PassageContradiction`.
    """

    quote: str
    contradiction: PassageContradiction

    def __post_init__(self) -> None:
        if not isinstance(self.contradiction, PassageContradiction):
            raise ValueError(
                f"contradiction must be a PassageContradiction, got {self.contradiction!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-ready dict."""
        return {"quote": self.quote, "contradiction": self.contradiction.value}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PicoContradictedQuote:
        """Rebuild from :meth:`to_dict` output.

        Raises:
            ValueError: A field is absent or of the wrong type, naming it, or
                the contradiction is not one :class:`PassageContradiction` names.
        """
        return cls(
            quote=_read(data, "quote", str),
            contradiction=PassageContradiction(_read(data, "contradiction", str)),
        )


@dataclass(frozen=True)
class PicoPart:
    """One of the four PICO parts.

    The state agrees with the quotes: a part is ``VERIFIED`` exactly when it
    carries a citation, and a reported part without one is ``CONTRADICTED``
    exactly when it carries a contradicted quote.  A ``NOT_REPORTED`` part may
    still carry the quotes the model gave beside ``reported: false`` that were
    not found or contradicted (#577).

    Attributes:
        description: The model's description, kept whatever the state.
        state: Whether the paper was found to back it.
        confidence: The model's confidence, clamped to 0–1; ``None`` when it
            gave none it could be read as.
        citations: The quotes found in the paper, as the paper's spans.
        quotes_not_found: The model's quotes that were not found, verbatim.
        quotes_contradicted: The model's quotes the paper contradicts, each
            with the reason; never also in ``quotes_not_found`` (#647).
        quotes_dropped: Quotes that were not usable text at all (not a
            string, blank, or a ``quotes`` value that was not a list).
    """

    description: str
    state: PicoPartState
    confidence: float | None
    citations: list[PicoCitation] = field(default_factory=list)
    quotes_not_found: list[str] = field(default_factory=list)
    quotes_dropped: int = 0
    quotes_contradicted: list[PicoContradictedQuote] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Refuse a part no reader produces.

        Raises:
            ValueError: The state is not a :class:`PicoPartState`, disagrees
                with the citations or the contradicted quotes, the confidence
                is not ``None`` or a number in [0, 1], or ``quotes_dropped`` is
                negative.
        """
        if not isinstance(self.state, PicoPartState):
            raise ValueError(f"state must be a PicoPartState, got {self.state!r}")
        if (self.state is PicoPartState.VERIFIED) != bool(self.citations):
            raise ValueError(
                f"a {self.state.value} part carries {len(self.citations)} citation(s): "
                "a part is verified exactly when one of its quotes was found"
            )
        contradicted = bool(self.quotes_contradicted)
        if (self.state is PicoPartState.CONTRADICTED and not contradicted) or (
            self.state is PicoPartState.UNVERIFIED and contradicted
        ):
            raise ValueError(
                f"a {self.state.value} part carries {len(self.quotes_contradicted)} "
                "contradicted quote(s): a reported part without a citation is "
                "contradicted exactly when one of its quotes was"
            )
        _check_confidence(self.confidence, "a part's")
        if self.quotes_dropped < 0:
            raise ValueError(f"quotes_dropped must not be negative, got {self.quotes_dropped}")

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-ready dict."""
        return {
            "description": self.description,
            "state": self.state.value,
            "confidence": self.confidence,
            "citations": [citation.to_dict() for citation in self.citations],
            "quotes_not_found": list(self.quotes_not_found),
            "quotes_contradicted": [quote.to_dict() for quote in self.quotes_contradicted],
            "quotes_dropped": self.quotes_dropped,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PicoPart:
        """Rebuild from :meth:`to_dict` output.

        A record without ``quotes_contradicted`` reads it as empty: one
        written before #647, by a ``find_passage`` that rejected no quote as
        a contradiction, so it held none.

        Raises:
            ValueError: A field is absent or of the wrong type, naming it, or
                the part breaks a rule above.
        """
        confidence = _read(data, "confidence", (int, float), nullable=True)
        contradicted = (
            _read(data, "quotes_contradicted", list) if "quotes_contradicted" in data else []
        )
        return cls(
            description=_read(data, "description", str),
            state=PicoPartState(_read(data, "state", str)),
            confidence=None if confidence is None else float(confidence),
            citations=[PicoCitation.from_dict(c) for c in _read(data, "citations", list)],
            quotes_not_found=_read_text_list(data, "quotes_not_found"),
            quotes_dropped=_read(data, "quotes_dropped", int),
            quotes_contradicted=[PicoContradictedQuote.from_dict(q) for q in contradicted],
        )


def mean_confidence(parts: list[PicoPart]) -> float | None:
    """The mean confidence of the parts that are reported and carry one.

    A correctly identified absence must not pull the mean down, so a
    ``NOT_REPORTED`` part is left out; ``None`` when no part counts.  Summed
    left to right, by hand: ``sum()`` compensates its rounding since Python
    3.12 and does not on 3.11, so it would answer differently across the
    versions bmlib supports — and differently from the Rust port's fold.
    """
    total = 0.0
    count = 0
    for part in parts:
        if part.state is not PicoPartState.NOT_REPORTED and part.confidence is not None:
            total += part.confidence
            count += 1
    return total / count if count else None


@dataclass
class PicoExtraction:
    """A study's PICO breakdown.

    Attributes:
        population: Who was studied.
        intervention: What was done to them.
        comparison: What it was compared against.
        outcome: What was measured.
        study_type: The design, as the model named it; not verified.
        sample_size: The number of participants, as the model gave it (an
            integer reply as its digits, ``"150"``); not verified.
        overall_confidence: The mean confidence of the parts that are
            reported and carry one; ``None`` when none does.
        condensed_from_chars: The stripped text's length in characters when
            it was condensed before extraction; ``None`` when it went to the
            model whole.
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
        """Rebuild from :meth:`to_dict` output.

        Raises:
            ValueError: A field is absent or of the wrong type, naming it, a
                part breaks :class:`PicoPart`'s rules, or the stored
                ``overall_confidence`` is not the mean of its parts.
        """
        parts = {
            name: PicoPart.from_dict(_read(data, name, dict))
            for name in ("population", "intervention", "comparison", "outcome")
        }
        stored = _read(data, "overall_confidence", (int, float), nullable=True)
        overall = mean_confidence(list(parts.values()))
        if stored != overall:
            raise ValueError(
                f"overall_confidence {stored!r} is not the mean of its parts ({overall!r})"
            )
        return cls(
            **parts,
            study_type=_read(data, "study_type", str, nullable=True),
            sample_size=_read(data, "sample_size", str, nullable=True),
            overall_confidence=overall,
            condensed_from_chars=_read(data, "condensed_from_chars", int, nullable=True),
            condensation_status=_read(data, "condensation_status", str, nullable=True),
            notes=_read_text_list(data, "notes"),
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
        """Rebuild from :meth:`to_dict` output.

        Raises:
            ValueError: A field is absent or of the wrong type, naming it —
                ``is_suitable`` above all, the one answer the check gives.
        """
        confidence = _read(data, "confidence", (int, float), nullable=True)
        _check_confidence(confidence, "the suitability")
        return cls(
            is_suitable=_read(data, "is_suitable", bool),
            is_intervention_study=_read(data, "is_intervention_study", bool, nullable=True),
            has_comparison=_read(data, "has_comparison", bool, nullable=True),
            study_type=_read(data, "study_type", str, nullable=True),
            confidence=None if confidence is None else float(confidence),
            rationale=_read(data, "rationale", str, nullable=True),
            condensed_from_chars=_read(data, "condensed_from_chars", int, nullable=True),
            condensation_status=_read(data, "condensation_status", str, nullable=True),
            notes=_read_text_list(data, "notes"),
        )


#: The ``ProcessingStatus`` values a condensation that produced a digest can
#: report: all but ``"failed"``, which produces none.
_DIGEST_STATUSES = frozenset({"pending", "in_progress", "completed", "truncated", "partial"})


@dataclass(frozen=True)
class PicoPrepared:
    """A paper made ready for the PICO calls, condensed at most once (#638).

    :meth:`~bmlib.quality.pico_agent.PicoAgent.prepare` builds it, and
    :meth:`~bmlib.quality.pico_agent.PicoAgent.check_suitability_prepared` and
    :meth:`~bmlib.quality.pico_agent.PicoAgent.extract_prepared` take it, so a
    long paper's map-reduce runs once for both calls and every retry.
    :meth:`to_dict` lets it be stored; it holds the paper, since quotes are
    verified against the paper and never the digest.

    Attributes:
        title: The title, stripped.
        text: The text as passed, unstripped: citation offsets index it.
        digest: What is sent in place of the text when it was condensed;
            ``None`` when the text is sent whole.
        condensation_status: The ``ProcessingStatus`` value the condensation
            finished with; ``None`` when not condensed.
        notes: Notes on the condensation (a degraded run), carried into each
            result.  A tuple, so the value stays as it was checked.
    """

    title: str
    text: str
    digest: str | None = None
    condensation_status: str | None = None
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Refuse a value :meth:`~bmlib.quality.pico_agent.PicoAgent.prepare` cannot make."""
        # A list passed in would stay the caller's to change after the checks.
        object.__setattr__(self, "notes", tuple(self.notes))
        if self.title != self.title.strip():
            raise ValueError(f"the title {self.title!r} is not stripped")
        if not self.title and not self.text.strip():
            raise ValueError("both the title and the text are empty")
        if self.digest is None:
            if self.condensation_status is not None or self.notes:
                raise ValueError("a text sent whole carries no condensation status or notes")
        elif not self.digest.strip():
            raise ValueError("the digest is blank")
        elif not self.text.strip():
            # Every quote would be checked against nothing, so none verifies.
            raise ValueError("a digest stands for an empty text")
        elif self.condensation_status not in _DIGEST_STATUSES:
            raise ValueError(
                f"condensation_status {self.condensation_status!r} is not one a digest "
                f"finishes with ({', '.join(sorted(_DIGEST_STATUSES))})"
            )

    @property
    def sent(self) -> str:
        """What the model is sent: the digest, or the stripped text."""
        return self.text.strip() if self.digest is None else self.digest

    @property
    def condensed_from_chars(self) -> int | None:
        """The stripped text's length when it was condensed; ``None`` when not."""
        return None if self.digest is None else len(self.text.strip())

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-ready dict."""
        return {
            "title": self.title,
            "text": self.text,
            "digest": self.digest,
            "condensation_status": self.condensation_status,
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PicoPrepared:
        """Rebuild from :meth:`to_dict` output.

        Raises:
            ValueError: A field is absent or of the wrong type, naming it, or
                the fields break the rules above.
        """
        return cls(
            title=_read(data, "title", str),
            text=_read(data, "text", str),
            digest=_read(data, "digest", str, nullable=True),
            condensation_status=_read(data, "condensation_status", str, nullable=True),
            notes=tuple(_read_text_list(data, "notes")),
        )
