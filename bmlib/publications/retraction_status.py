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

"""A paper's retraction status from three sources, under one rule (#504).

Neither Retraction Watch nor Crossref contains the other (measured
2026-10-18: 8.7% of the export's retractions carry no DOI, and 77 of 197
sampled publisher-deposited retractions are absent from the export), so the
status takes evidence from three places:

1. the Retraction Watch export, looked up by DOI and by PMID
   (:func:`~bmlib.publications.retractions.lookup_retractions`, then
   :func:`evidence_from_notices`);
2. Crossref's ``updated-by`` relations, by DOI
   (:func:`crossref_retraction_requests` builds the URLs, the caller sends
   them, :func:`parse_crossref_retraction_response` reads the answers);
3. PubMed's ``Retracted Publication`` publication type (D016441).

:func:`retraction_status` turns them into one of four statuses. The rule is
pinned by ``tests/fixtures/retraction_status.json``, which the Rust port and
bmlibrarian_lite's three platforms assert against too. **"Could not check" is
never "clear"**: clear needs the export consulted *and* Crossref to have
served this DOI.

No transport lives here: the apps bring their own, and so does the desktop.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any
from urllib.parse import quote

from bmlib.publications.models import RetractionNature, RetractionNotice
from bmlib.publications.storage import _normalize_doi

logger = logging.getLogger(__name__)


class RetractionSource(StrEnum):
    """Where a piece of evidence came from.

    Crossref is two sources because its relations carry their own
    ``source``: ``retraction-watch`` relations are Retraction Watch's curated
    records, ``publisher`` relations are deposited by the publisher — and only
    the second is ever inverted (see :func:`retraction_status`).
    """

    RETRACTION_WATCH_EXPORT = "retraction_watch_export"
    CROSSREF_RETRACTION_WATCH = "crossref_retraction_watch"
    CROSSREF_PUBLISHER = "crossref_publisher"
    PUBMED_LABEL = "pubmed_label"


class RetractionKind(StrEnum):
    """What a notice says about the paper.

    Crossref's ``type`` vocabulary, which the export's natures map onto. A
    value outside it is :attr:`OTHER`, with the original kept in
    :attr:`RetractionEvidence.raw_kind`, and decides nothing.
    """

    RETRACTION = "retraction"
    WITHDRAWAL = "withdrawal"
    REMOVAL = "removal"
    PARTIAL_RETRACTION = "partial_retraction"
    EXPRESSION_OF_CONCERN = "expression_of_concern"
    CORRECTION = "correction"
    ERRATUM = "erratum"
    REINSTATEMENT = "reinstatement"
    OTHER = "other"


class RetractionStatus(StrEnum):
    """The verdict. ``UNCHECKED`` is never a softer ``CLEAR``."""

    RETRACTED = "retracted"
    CONCERN = "concern"
    CLEAR = "clear"
    UNCHECKED = "unchecked"


class RetractionLookup(StrEnum):
    """A source that has to answer before a paper can be called clear.

    The PubMed label is not one: it is read off a record the caller already
    holds, and a record restored without its publication types is still
    checkable against the other two.
    """

    RETRACTION_WATCH_EXPORT = "retraction_watch_export"
    CROSSREF = "crossref"


class CrossrefOutcome(StrEnum):
    """What one Crossref batch said about one requested DOI."""

    SERVED = "served"
    #: Absent from a successful answer: Crossref does not know this DOI (a
    #: DataCite DOI, say). Asking again will not change it.
    NOT_KNOWN = "not_known"
    #: The batch failed — a non-200, an unreadable body — or this DOI's own
    #: relations could not be read. Never read as "not known".
    UNREACHABLE = "unreachable"


#: Kinds that retract. The first of these or a reinstatement, newest first,
#: decides; that is :func:`~bmlib.publications.retractions.is_retracted`'s rule
#: extended to Crossref's withdrawal and removal.
RETRACTION_FAMILY = frozenset(
    {RetractionKind.RETRACTION, RetractionKind.WITHDRAWAL, RetractionKind.REMOVAL}
)
#: Kinds that warn without retracting, until a later reinstatement.
CONCERN_KINDS = frozenset({RetractionKind.EXPRESSION_OF_CONCERN, RetractionKind.PARTIAL_RETRACTION})

#: PubMed's publication type, by name and by MeSH UI, case-folded.
PUBMED_RETRACTED_LABELS = frozenset({"retracted publication", "d016441"})

CROSSREF_WORKS_URL = "https://api.crossref.org/works"
#: DOIs per request. Measured 2026-10-18: batches of 25, 50, 100, 150 and 200
#: each served every DOI asked for; 50 is lite's planned rate (one request per
#: 50 papers) and kept 2.2 s per request.
CROSSREF_BATCH_MAX_DOIS = 50
#: A URL is cut before it passes this length. A 200-DOI URL of 6,002
#: characters was served on 2026-10-18, so this sits well inside a measured
#: success rather than at a guessed server limit.
CROSSREF_URL_MAX_CHARS = 4000

_CROSSREF_SOURCES = {
    "retraction-watch": RetractionSource.CROSSREF_RETRACTION_WATCH,
    "publisher": RetractionSource.CROSSREF_PUBLISHER,
}

_EXPORT_KINDS = {
    RetractionNature.RETRACTION: RetractionKind.RETRACTION,
    RetractionNature.CORRECTION: RetractionKind.CORRECTION,
    RetractionNature.EXPRESSION_OF_CONCERN: RetractionKind.EXPRESSION_OF_CONCERN,
    RetractionNature.REINSTATEMENT: RetractionKind.REINSTATEMENT,
    RetractionNature.OTHER: RetractionKind.OTHER,
}

# Which copy of one retraction is shown as deciding, on equal dates: Retraction
# Watch's record is curated, so it is preferred over the publisher's.
_SOURCE_PREFERENCE = {
    RetractionSource.RETRACTION_WATCH_EXPORT: 3,
    RetractionSource.CROSSREF_RETRACTION_WATCH: 2,
    RetractionSource.CROSSREF_PUBLISHER: 1,
    RetractionSource.PUBMED_LABEL: 0,
}


@dataclass(frozen=True)
class RetractionEvidence:
    """One source's statement about one paper, in a source-neutral form.

    ``date`` is ISO: ``yyyy-mm-dd``, or ``yyyy-mm`` / ``yyyy`` where Crossref
    deposited only that much. ``notice_doi`` is the notice's DOI,
    lower-cased. ``raw_kind`` is the source's own spelling of the kind.
    """

    source: RetractionSource
    kind: RetractionKind
    date: str | None = None
    notice_doi: str | None = None
    record_id: str | None = None
    raw_kind: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dictionary (the fixture's shape)."""
        return {
            "source": self.source.value,
            "kind": self.kind.value,
            "date": self.date,
            "notice_doi": self.notice_doi,
            "record_id": self.record_id,
            "raw_kind": self.raw_kind,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RetractionEvidence:
        """Inverse of :meth:`to_dict`. An unknown source or kind raises."""
        return cls(
            source=RetractionSource(data["source"]),
            kind=RetractionKind(data["kind"]),
            date=data.get("date"),
            notice_doi=data.get("notice_doi"),
            record_id=data.get("record_id"),
            raw_kind=data.get("raw_kind"),
        )


@dataclass(frozen=True)
class RetractionVerdict:
    """What :func:`retraction_status` decided, and on what.

    ``deciding`` is the newest retraction for ``RETRACTED``, the newest
    concern for ``CONCERN``, and otherwise the reinstatement that undid a
    retraction, if there was one. ``unanswered`` names, in declaration
    order, every lookup that did not answer — beside any status, since a
    concern found while Crossref was down may yet be a retraction.
    """

    status: RetractionStatus
    deciding: RetractionEvidence | None = None
    unanswered: tuple[RetractionLookup, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dictionary (the fixture's shape)."""
        return {
            "status": self.status.value,
            "deciding": self.deciding.to_dict() if self.deciding else None,
            "unanswered": [lookup.value for lookup in self.unanswered],
        }


@dataclass(frozen=True)
class CrossrefResult:
    """Crossref's answer about one DOI.

    ``evidence`` (the record's ``updated-by``, in document order) and
    ``retraction_targets`` (the DOIs its own retraction-family ``update-to``
    names) are set only when ``outcome`` is ``SERVED``; ``reason`` only when
    it is ``UNREACHABLE``, and its wording is not part of the contract.
    """

    outcome: CrossrefOutcome
    evidence: tuple[RetractionEvidence, ...] = ()
    retraction_targets: tuple[str, ...] = ()
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dictionary (the fixture's shape)."""
        data: dict[str, Any] = {"outcome": self.outcome.value}
        if self.outcome is CrossrefOutcome.SERVED:
            data["evidence"] = [e.to_dict() for e in self.evidence]
            data["retraction_targets"] = list(self.retraction_targets)
        if self.outcome is CrossrefOutcome.UNREACHABLE:
            data["reason"] = self.reason
        return data


@dataclass(frozen=True)
class CrossrefBatch:
    """One request URL and the normalised DOIs it asks about."""

    url: str
    dois: tuple[str, ...]


@dataclass(frozen=True)
class CrossrefRequests:
    """The batches to send, and the DOIs no batch can carry.

    A DOI containing a comma cannot be put in a ``filter=doi:a,doi:b`` list
    (none of the export's 69,318 DOIs does, 2026-03 copy). It is never asked,
    so Crossref has not answered for it and the paper cannot be clear.
    """

    batches: tuple[CrossrefBatch, ...] = ()
    unsendable: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# The export
# ---------------------------------------------------------------------------


def evidence_from_notices(notices: Iterable[RetractionNotice]) -> list[RetractionEvidence]:
    """Turn Retraction Watch notices (one paper's) into evidence."""
    return [
        RetractionEvidence(
            source=RetractionSource.RETRACTION_WATCH_EXPORT,
            kind=_EXPORT_KINDS[notice.nature],
            date=notice.retraction_date,
            notice_doi=_normalize_doi(notice.notice_doi),
            record_id=notice.record_id,
            raw_kind=notice.raw_nature,
        )
        for notice in notices
    ]


# ---------------------------------------------------------------------------
# Crossref
# ---------------------------------------------------------------------------


def _crossref_url(dois: Sequence[str], mailto: str | None) -> str:
    filters = ",".join("doi:" + quote(doi, safe="/()") for doi in dois)
    url = f"{CROSSREF_WORKS_URL}?filter={filters}&select=DOI,update-to,updated-by&rows={len(dois)}"
    if mailto:
        url += "&mailto=" + quote(mailto, safe="@")
    return url


def crossref_retraction_requests(
    dois: Iterable[str], mailto: str | None = None
) -> CrossrefRequests:
    """Build the Crossref requests that ask about *dois*.

    DOIs are normalised (prefix stripped, lower-cased) and de-duplicated, then
    packed in order into batches of at most :data:`CROSSREF_BATCH_MAX_DOIS`
    whose URL stays within :data:`CROSSREF_URL_MAX_CHARS` (a single DOI longer
    than that still gets a batch of its own). Hand each batch's ``dois`` back
    to :func:`parse_crossref_retraction_response` with its answer.
    """
    batches: list[CrossrefBatch] = []
    unsendable: list[str] = []
    current: list[str] = []
    seen: set[str] = set()
    for raw in dois:
        doi = _normalize_doi(raw)
        if not doi or doi in seen:
            continue
        seen.add(doi)
        if "," in doi:
            unsendable.append(doi)
            continue
        candidate = [*current, doi]
        if current and (
            len(candidate) > CROSSREF_BATCH_MAX_DOIS
            or len(_crossref_url(candidate, mailto)) > CROSSREF_URL_MAX_CHARS
        ):
            batches.append(CrossrefBatch(_crossref_url(current, mailto), tuple(current)))
            candidate = [doi]
        current = candidate
    if current:
        batches.append(CrossrefBatch(_crossref_url(current, mailto), tuple(current)))
    return CrossrefRequests(tuple(batches), tuple(unsendable))


def _crossref_date(entry: Mapping[str, Any]) -> str | None:
    """An ISO date from ``updated.date-parts``, as precise as deposited."""
    updated = entry.get("updated")
    parts = updated.get("date-parts") if isinstance(updated, dict) else None
    first = parts[0] if isinstance(parts, list) and parts else None
    if not isinstance(first, list) or not first:
        return None
    values = first[:3]
    if any(type(value) is not int for value in values):
        return None
    year, *rest = values
    if not 1000 <= year <= 9999:
        return None
    if not rest:
        return f"{year:04d}"
    month = rest[0]
    if not 1 <= month <= 12:
        return None
    if len(rest) == 1:
        return f"{year:04d}-{month:02d}"
    try:
        return date(year, month, rest[1]).isoformat()
    except ValueError:
        return None


class _UnreadableItemError(Exception):
    """One record's relations cannot be read; carries the reason."""


def _relations(item: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    value = item.get(key, [])
    if not isinstance(value, list) or not all(
        isinstance(entry, dict) and isinstance(entry.get("type"), str) for entry in value
    ):
        raise _UnreadableItemError(f"{key} is not a list of typed relations")
    return value


def _optional_text(entry: Mapping[str, Any], key: str) -> str | None:
    value = entry.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, str | int):
        raise _UnreadableItemError(f"a relation's {key} is a {type(value).__name__}")
    return str(value)


def _kind(raw: str, unknown_kinds: set[str]) -> RetractionKind:
    try:
        return RetractionKind(raw.strip().lower())
    except ValueError:
        pass
    key = raw.strip().lower()
    if key not in unknown_kinds:
        unknown_kinds.add(key)
        logger.warning(
            "Unrecognised Crossref relation type %r; kept as %r, which decides nothing."
            " The original string is kept in raw_kind.",
            raw,
            RetractionKind.OTHER.value,
        )
    return RetractionKind.OTHER


def _read_item(item: Mapping[str, Any], unknown_kinds: set[str]) -> CrossrefResult:
    try:
        evidence = []
        for entry in _relations(item, "updated-by"):
            raw_source = entry.get("source")
            source = _CROSSREF_SOURCES.get(raw_source) if isinstance(raw_source, str) else None
            if source is None:
                # Not guessed: which source it is decides whether the guard
                # in retraction_status may discard it.
                raise _UnreadableItemError(f"unknown relation source {raw_source!r}")
            evidence.append(
                RetractionEvidence(
                    source=source,
                    kind=_kind(entry["type"], unknown_kinds),
                    date=_crossref_date(entry),
                    notice_doi=_normalize_doi(_optional_text(entry, "DOI")),
                    record_id=_optional_text(entry, "record-id"),
                    raw_kind=entry["type"],
                )
            )
        targets: list[str] = []
        for entry in _relations(item, "update-to"):
            target = _normalize_doi(_optional_text(entry, "DOI"))
            if (
                target
                and _kind(entry["type"], unknown_kinds) in RETRACTION_FAMILY
                and target not in targets
            ):
                targets.append(target)
    except _UnreadableItemError as exc:
        return CrossrefResult(CrossrefOutcome.UNREACHABLE, reason=str(exc))
    return CrossrefResult(CrossrefOutcome.SERVED, tuple(evidence), tuple(targets))


def parse_crossref_retraction_response(
    status: int, body: bytes | str, requested_dois: Iterable[str]
) -> dict[str, CrossrefResult]:
    """Read one batch's answer into a result per requested DOI.

    Keyed by the normalised DOI. **A failed batch makes every DOI in it
    unreachable**, never "not known": a non-200 (429 and 5xx included — the
    caller's pacing decides the retry), a body that is not JSON, or one
    carrying no list of ``items``, or an item with no ``DOI`` (which leaves
    absence meaning nothing). A DOI whose own relations cannot be read is
    unreachable alone. A DOI absent from a well-formed answer is
    ``NOT_KNOWN``.
    """
    requested = list(dict.fromkeys(d for d in map(_normalize_doi, requested_dois) if d))

    def failed(reason: str) -> dict[str, CrossrefResult]:
        return {
            doi: CrossrefResult(CrossrefOutcome.UNREACHABLE, reason=reason) for doi in requested
        }

    if status != 200:
        return failed(f"HTTP {status}")
    try:
        # Decoded as UTF-8 explicitly: json.loads would also sniff UTF-16 and
        # UTF-32 from bytes, which the Rust port does not.
        data = json.loads(body.decode("utf-8") if isinstance(body, bytes) else body)
    except (ValueError, RecursionError) as exc:
        return failed(f"body is not JSON: {exc}")
    message = data.get("message") if isinstance(data, dict) else None
    items = message.get("items") if isinstance(message, dict) else None
    if not isinstance(items, list):
        return failed("body carries no list of items")
    by_doi: dict[str, Mapping[str, Any]] = {}
    for item in items:
        raw_doi = item.get("DOI") if isinstance(item, dict) else None
        doi = _normalize_doi(raw_doi) if isinstance(raw_doi, str) else None
        if doi is None:
            return failed("an item carries no DOI")
        by_doi[doi] = item

    unknown_kinds: set[str] = set()
    return {
        doi: (
            _read_item(by_doi[doi], unknown_kinds)
            if doi in by_doi
            else CrossrefResult(CrossrefOutcome.NOT_KNOWN)
        )
        for doi in requested
    }


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------


def _label_evidence(publication_types: Iterable[str]) -> list[RetractionEvidence]:
    for raw in publication_types:
        if raw.strip().lower() in PUBMED_RETRACTED_LABELS:
            return [
                RetractionEvidence(
                    RetractionSource.PUBMED_LABEL, RetractionKind.RETRACTION, raw_kind=raw
                )
            ]
    return []


def _tie_break(evidence: RetractionEvidence) -> tuple[int, str, str]:
    # Retraction Watch's curated copy is shown first; past that the order is
    # fixed by the notice itself, so the deciding entry does not depend on the
    # order a database or an index returned the evidence in.
    return (
        _SOURCE_PREFERENCE[evidence.source],
        evidence.notice_doi or "",
        evidence.record_id or "",
    )


def _decisive_key(evidence: RetractionEvidence) -> tuple[str, int, tuple[int, str, str]]:
    # Newest first; undated is "" and so oldest. On equal dates the label
    # beats a reinstatement (it can only be undone by a *later* one) and a
    # reinstatement beats a retraction (it can only follow it).
    if evidence.source is RetractionSource.PUBMED_LABEL:
        rank = 2
    elif evidence.kind is RetractionKind.REINSTATEMENT:
        rank = 1
    else:
        rank = 0
    return (evidence.date or "", rank, _tie_break(evidence))


def retraction_status(
    *,
    doi: str | None,
    export: Sequence[RetractionEvidence] | None,
    crossref: CrossrefResult | None,
    publication_types: Iterable[str] = (),
) -> RetractionVerdict:
    """Decide one paper's retraction status from everything known about it.

    Args:
        doi: The paper's DOI, or ``None``. Without one Crossref cannot have
            answered, so the paper is never clear.
        export: The paper's Retraction Watch evidence — ``[]`` when the
            export was consulted and holds nothing — or ``None`` when it was
            not consulted (no copy, an unreadable one, no DOI or PMID to look
            up).
        crossref: This DOI's result from :func:`parse_crossref_retraction_response`,
            or ``None`` when Crossref was not asked.
        publication_types: The record's PubMed / Europe PMC publication
            types, if the caller holds them.

    The rules, each a case of ``tests/fixtures/retraction_status.json``:

    * **Retracted**: newest first over retractions, withdrawals, removals,
      reinstatements and the label, the first decides; anything but a
      reinstatement means retracted. Corrections and errata are evidence of
      nothing. Undated entries are the oldest. The label settles retraction
      on its own unless a reinstatement *dated* later undoes it.
    * **Concern**: not retracted, and an expression of concern or a partial
      retraction exists that no reinstatement on or after its date follows.
    * **Clear** needs the export consulted and Crossref to have served this
      DOI; anything less is **unchecked**. A DOI Crossref does not know is
      unchecked too (the maintainer's choice, 2026-10-18).
    * **A notice never flags itself**: a ``publisher`` relation saying the
      paper is retracted by X is ignored when the paper's own retraction
      ``update-to`` names X and X is not the paper itself. Publishers deposit
      these relations both ways round (the Surgisphere Lancet notice is
      ``updated-by`` the paper it retracts), while Retraction Watch's are
      directed correctly — and the paper itself lists its notices in its own
      ``update-to`` too, so applying the guard to every source would discard
      the Lancet paper's real retraction (the maintainer's choice). Elsevier's
      self-referencing retraction stays a retraction.
    """
    own = _normalize_doi(doi)
    served = crossref is not None and crossref.outcome is CrossrefOutcome.SERVED
    targets = set(crossref.retraction_targets) - {own} if served and crossref else set()

    evidence = [*(export or ()), *(crossref.evidence if served and crossref else ())]
    evidence = [
        e
        for e in evidence
        if not (
            e.source is RetractionSource.CROSSREF_PUBLISHER
            and e.kind in RETRACTION_FAMILY
            and e.notice_doi in targets
        )
    ]
    evidence += _label_evidence(publication_types)

    answered = set()
    if export is not None:
        answered.add(RetractionLookup.RETRACTION_WATCH_EXPORT)
    if served and own:
        answered.add(RetractionLookup.CROSSREF)
    unanswered = tuple(lookup for lookup in RetractionLookup if lookup not in answered)

    decisive = sorted(
        (
            e
            for e in evidence
            if e.kind in RETRACTION_FAMILY or e.kind is RetractionKind.REINSTATEMENT
        ),
        key=_decisive_key,
        reverse=True,
    )
    first = decisive[0] if decisive else None
    if first is not None and first.kind is not RetractionKind.REINSTATEMENT:
        return RetractionVerdict(RetractionStatus.RETRACTED, first, unanswered)

    reinstated = max(
        (e.date for e in evidence if e.kind is RetractionKind.REINSTATEMENT and e.date),
        default=None,
    )
    concerns = sorted(
        (
            e
            for e in evidence
            if e.kind in CONCERN_KINDS and not (reinstated and (e.date or "") <= reinstated)
        ),
        key=lambda e: (e.date or "", _tie_break(e)),
        reverse=True,
    )
    if concerns:
        return RetractionVerdict(RetractionStatus.CONCERN, concerns[0], unanswered)
    status = RetractionStatus.UNCHECKED if unanswered else RetractionStatus.CLEAR
    return RetractionVerdict(status, first, unanswered)
