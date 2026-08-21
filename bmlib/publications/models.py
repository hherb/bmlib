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

"""Data models for the publications module.

Defines dataclasses for publications, full-text sources, download tracking,
and sync status reporting.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from bmlib.fulltext.models import FullTextSourceEntry


def _now_utc() -> datetime:
    """Return the current UTC datetime."""
    return datetime.now(tz=UTC)


def _parse_datetime(value: str | datetime | None) -> datetime:
    """Parse an ISO datetime string, or return UTC now if None."""
    if value is None:
        return _now_utc()
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


def _require_datetime(value: object, field_name: str) -> datetime:
    """Parse a timestamp a stored row must carry, rather than inventing one.

    The strict counterpart to :func:`_parse_datetime`, for a column that is
    ``NOT NULL`` in both DDLs *and* is read by a rule that decides whether
    work may be skipped. Substituting *now* there is not a neutral default:
    it is the single most durable-looking value
    :func:`~bmlib.publications.sync._day_was_over_when_fetched` can be handed,
    so the day is never fetched again (#98). The selection path guards the
    same column in the opposite direction — a value it cannot read, or one in
    the future, fails closed with a warning — and the model must not disagree
    with the rule about what an absent value means.

    A dict lacking the key did not come from the database, so this is
    malformed input rather than a state to paper over.

    Every rejection is a ``ValueError`` naming *field_name*, which is the
    whole contract this function offers. Delegating straight to
    :func:`_parse_datetime` did not deliver it: a non-string escaped as
    ``TypeError`` from ``fromisoformat``, so a caller writing the documented
    ``except ValueError`` got an uncaught crash, and an unreadable string
    raised ``Invalid isoformat string: ''`` — which names neither the column
    nor the row, leaving a bulk deserialiser nothing to report. A plain
    ``date`` is the trap worth naming: ``isinstance(datetime_value, date)``
    is true but the converse is not, so it looks accepted and is not.

    Parameters
    ----------
    value:
        The raw value from the dict. Typed ``object`` because refusing what
        it may turn out to be is this function's entire job.
    field_name:
        The column being read, interpolated into every message.

    Raises
    ------
    ValueError
        If *value* is absent, ``None``, not a timestamp, or unreadable.
    """
    if value is None:
        raise ValueError(
            f"{field_name} is required and must not be None;"
            " it is NOT NULL in the schema, so a row lacking it is malformed"
        )
    if not isinstance(value, str | datetime):
        raise ValueError(
            f"{field_name} must be an ISO 8601 string or a datetime, got {type(value).__name__}"
        )
    try:
        return _parse_datetime(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} is not a readable ISO 8601 timestamp: {value!r}") from exc


# ---------------------------------------------------------------------------
# Core publication model
# ---------------------------------------------------------------------------


@dataclass
class Publication:
    """A biomedical publication record."""

    title: str
    sources: list[str]
    first_seen_source: str

    doi: str | None = None
    pmid: str | None = None
    abstract: str | None = None
    authors: list[str] = field(default_factory=list)
    journal: str | None = None
    publication_date: str | None = None
    publication_types: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    is_open_access: bool = False
    license: str | None = None
    created_at: datetime = field(default_factory=_now_utc)
    updated_at: datetime = field(default_factory=_now_utc)
    id: int | None = None

    # New fields go last, never next to the field they read best beside.
    # Downstream projects construct this positionally; inserting ``pmcid``
    # after ``pmid`` — where it belongs on grounds of taste — silently shifts
    # every later argument, so a caller's abstract lands in ``pmcid``. Appending
    # cannot disturb an existing call.
    pmcid: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dictionary."""
        return {
            "id": self.id,
            "title": self.title,
            "doi": self.doi,
            "pmid": self.pmid,
            "pmcid": self.pmcid,
            "abstract": self.abstract,
            "authors": self.authors,
            "journal": self.journal,
            "publication_date": self.publication_date,
            "publication_types": self.publication_types,
            "keywords": self.keywords,
            "is_open_access": self.is_open_access,
            "license": self.license,
            "sources": self.sources,
            "first_seen_source": self.first_seen_source,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Publication:
        """Deserialise from a dictionary produced by :meth:`to_dict`."""
        return cls(
            id=data.get("id"),
            title=data["title"],
            doi=data.get("doi"),
            pmid=data.get("pmid"),
            pmcid=data.get("pmcid"),
            abstract=data.get("abstract"),
            authors=data.get("authors", []),
            journal=data.get("journal"),
            publication_date=data.get("publication_date"),
            publication_types=data.get("publication_types", []),
            keywords=data.get("keywords", []),
            is_open_access=data.get("is_open_access", False),
            license=data.get("license"),
            sources=data["sources"],
            first_seen_source=data["first_seen_source"],
            created_at=_parse_datetime(data.get("created_at")),
            updated_at=_parse_datetime(data.get("updated_at")),
        )


# ---------------------------------------------------------------------------
# Full-text source
# ---------------------------------------------------------------------------


@dataclass
class FullTextSource:
    """A full-text source for a publication (e.g. PMC XML, publisher PDF)."""

    publication_id: int
    source: str
    url: str
    format: str

    version: str | None = None
    retrieved_at: datetime | None = None
    created_at: datetime = field(default_factory=_now_utc)
    id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dictionary."""
        return {
            "id": self.id,
            "publication_id": self.publication_id,
            "source": self.source,
            "url": self.url,
            "format": self.format,
            "version": self.version,
            "retrieved_at": self.retrieved_at.isoformat() if self.retrieved_at else None,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FullTextSource:
        """Deserialise from a dictionary produced by :meth:`to_dict`."""
        return cls(
            id=data.get("id"),
            publication_id=data["publication_id"],
            source=data["source"],
            url=data["url"],
            format=data["format"],
            version=data.get("version"),
            retrieved_at=(
                _parse_datetime(data["retrieved_at"]) if data.get("retrieved_at") else None
            ),
            created_at=_parse_datetime(data.get("created_at")),
        )


# ---------------------------------------------------------------------------
# Funding and affiliation (child rows of a publication)
# ---------------------------------------------------------------------------


@dataclass
class Grant:
    """A funding award backing a publication, from PubMed's ``<GrantList>``.

    Every field is optional because PubMed's own records are: a grant may name
    an agency with no award id, or an award id with no country. A grant naming
    neither an agency nor an id carries no information and is not stored.

    ``source`` names the publication source that asserted this grant (e.g.
    ``"pubmed"``). It is what scopes storage: re-storing a source's grants
    replaces that source's rows and leaves every other source's alone, so two
    sources' funding data coexist instead of overwriting each other on
    alternate syncs. :func:`~bmlib.publications.sync.sync` fills it in from the
    record's own source, so a fetcher cannot forget it; a caller reaching
    :func:`~bmlib.publications.storage.store_publication` directly must set it.

    ``publication_id`` defaults to 0 so a fetcher can build one before the
    publication has a row. On the way in it is **ignored** — the stored row
    takes its id from the publication, and the object you passed is *not*
    mutated (unlike ``store_publication``'s ``pub`` argument, which is). Read
    the persisted form back with
    :func:`~bmlib.publications.storage.get_grants`, whose results carry both
    ``publication_id`` and ``id``.
    """

    agency: str | None = None
    grant_id: str | None = None
    country: str | None = None
    source: str = ""
    publication_id: int = 0
    id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dictionary."""
        return {
            "id": self.id,
            "publication_id": self.publication_id,
            "source": self.source,
            "agency": self.agency,
            "grant_id": self.grant_id,
            "country": self.country,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Grant:
        """Deserialise from a dictionary produced by :meth:`to_dict`."""
        return cls(
            id=data.get("id"),
            publication_id=data.get("publication_id", 0),
            source=data.get("source", ""),
            agency=data.get("agency"),
            grant_id=data.get("grant_id"),
            country=data.get("country"),
        )


@dataclass
class AuthorAffiliation:
    """One author's stated affiliation, from PubMed's ``<AffiliationInfo>``.

    One row per *(author, affiliation)* pair — an author listing three
    institutions produces three of these. That is the relational shape, and it
    makes "which papers have an author at this institution?" a single join
    rather than a scan through nested JSON.

    Only ``publication_id`` is indexed, which serves the read this module
    performs (a publication's own rows). A search *by* institution is a table
    scan until a consumer adds its own index — deliberately left to them,
    because an affiliation is long free text for which the useful index is
    trigram or full-text, and both are backend-specific and costly to maintain
    for callers who never run that query.

    ``position`` is the author's 0-based index in the ``<AuthorList>``. It is
    carried because first-author and senior-author affiliations are the ones a
    conflict-of-interest check cares about, and the name alone cannot recover
    the ordering.

    ``author`` is formatted exactly as
    :attr:`~bmlib.publications.models.Publication.authors` formats it
    (``"Last, Fore"``), so the two can be matched **by name**. Not by index:
    ``position`` counts every ``<Author>`` element, including the
    ``<CollectiveName>`` consortia that ``authors`` omits, so the two lists
    differ in length whenever one is present.

    ``affiliation`` is Markdown, read by the same walker as the title and
    abstract — an ``<Affiliation>`` shares ``<ArticleTitle>``'s content model
    and can carry a superscript footnote marker. It is therefore escaped like
    any other prose, which matters here in a way it does not for a title:
    matching this column against an institution name obtained elsewhere must
    compare against the escaped form.

    ``source`` and ``publication_id`` behave exactly as :class:`Grant`'s do —
    the first scopes storage to the source that asserted the row, the second is
    ignored on the way in. Read the persisted form back with
    :func:`~bmlib.publications.storage.get_author_affiliations`.
    """

    author: str
    affiliation: str
    position: int = 0
    source: str = ""
    publication_id: int = 0
    id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dictionary."""
        return {
            "id": self.id,
            "publication_id": self.publication_id,
            "source": self.source,
            "author": self.author,
            "affiliation": self.affiliation,
            "position": self.position,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AuthorAffiliation:
        """Deserialise from a dictionary produced by :meth:`to_dict`."""
        return cls(
            id=data.get("id"),
            publication_id=data.get("publication_id", 0),
            source=data.get("source", ""),
            author=data["author"],
            affiliation=data["affiliation"],
            position=data.get("position", 0),
        )


# ---------------------------------------------------------------------------
# Download tracking
# ---------------------------------------------------------------------------


@dataclass
class DownloadDay:
    """Tracks download status for a single source on a single date."""

    source: str
    date: str
    status: str
    record_count: int

    downloaded_at: datetime = field(default_factory=_now_utc)
    last_verified_at: datetime | None = None
    id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dictionary."""
        return {
            "id": self.id,
            "source": self.source,
            "date": self.date,
            "status": self.status,
            "record_count": self.record_count,
            "downloaded_at": self.downloaded_at.isoformat(),
            "last_verified_at": (
                self.last_verified_at.isoformat() if self.last_verified_at else None
            ),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DownloadDay:
        """Deserialise from a dictionary produced by :meth:`to_dict`.

        ``downloaded_at`` is required, unlike the dataclass default that
        stamps *now* for a freshly constructed row: that default describes a
        fetch which has just happened, whereas here the row was already
        stored, and inventing a timestamp for it fails open against the
        durability rule that reads the column (#98). See
        :func:`_require_datetime`.

        Raises
        ------
        KeyError
            If ``source``, ``date``, ``status`` or ``record_count`` is absent.
            ``last_verified_at`` cannot raise it — the read is guarded by
            ``.get()``, and an absent or unreadable value there means
            "recheck this day", which fails closed.
        ValueError
            If ``downloaded_at`` is absent, ``None``, not a timestamp, or
            unreadable. Every rejection names the field; see
            :func:`_require_datetime`.
        """
        return cls(
            id=data.get("id"),
            source=data["source"],
            date=data["date"],
            status=data["status"],
            record_count=data["record_count"],
            downloaded_at=_require_datetime(data.get("downloaded_at"), "downloaded_at"),
            last_verified_at=(
                _parse_datetime(data["last_verified_at"]) if data.get("last_verified_at") else None
            ),
        )


# ---------------------------------------------------------------------------
# Simple status / result types (no to_dict needed)
# ---------------------------------------------------------------------------


DayStatus = Literal["completed", "failed"]
"""The two values ``download_days.status`` may hold.

Deliberately *not* the type of :attr:`FetchResult.status`. That field takes
whatever a fetcher returns, including a third-party one registered through
``register_source()``, so it stays a bare ``str`` and is validated at the
boundary; this alias types the value only after that validation, so that
writing an unrecognised status into the table is a type error. A status the
table does not recognise is not a cosmetic problem: ``_days_needing_fetch()``
treats anything that is not ``"completed"`` as needing a re-fetch, so an
unrecognised value silently changes which days are ever fetched again.
"""


@dataclass
class FetchResult:
    """Result of fetching records from a source for a given date."""

    source: str
    date: str
    record_count: int
    status: str
    error: str | None = None
    note: str | None = None
    """Something the caller should know about a day that still completed.

    A shortfall too small to fail on is the case this exists for: the day is
    recorded as done, and without a returned value the only trace was one log
    line, so no caller could answer "which of my completed days came up
    short?" afterwards. Declared last, for positional stability.
    """


@dataclass
class SyncProgress:
    """Progress report during a sync operation."""

    source: str
    date: str
    records_processed: int
    records_total: int
    status: str
    message: str | None = None


@dataclass
class SyncReport:
    """Summary report after completing a sync operation.

    ``sources_synced`` lists every source whose sync loop ran to completion —
    including sources where individual days failed (a fetcher error records a
    failed day and moves on). Check ``errors`` for per-day failures; a source
    is only absent from this list when no fetcher was found for it.

    ``notes`` carries what went imperfectly on days that nevertheless
    completed — chiefly a shortfall below the failure floor. It is kept apart
    from ``errors`` because the two call for different responses: an error
    names a day that will be retried, a note names a day that will not be.
    """

    sources_synced: list[str]
    days_processed: int
    records_added: int
    records_merged: int
    records_failed: int
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Canonical fetcher output
# ---------------------------------------------------------------------------


@dataclass
class FetchedRecord:
    """Canonical record format returned by all source fetchers.

    Core fields are guaranteed present (may be None/empty).
    Source-specific data goes in ``extras``.
    """

    # -- Identifiers --
    title: str
    source: str
    doi: str | None = None
    pmid: str | None = None
    pmc_id: str | None = None

    # -- Content --
    abstract: str | None = None
    authors: list[str] = field(default_factory=list)
    journal: str | None = None
    publication_date: str | None = None
    keywords: list[str] = field(default_factory=list)
    publication_types: list[str] = field(default_factory=list)

    # -- Access --
    is_open_access: bool = False
    license: str | None = None
    fulltext_sources: list[FullTextSourceEntry] = field(default_factory=list)

    # -- Source-specific extras --
    extras: dict[str, Any] = field(default_factory=dict)

    # New fields go last — downstream projects construct this positionally, so
    # placing these beside the content fields they read best beside would shift
    # every later argument silently. See ``Publication.pmcid`` for the same
    # rule and the test that pins it.
    grants: list[Grant] = field(default_factory=list)
    author_affiliations: list[AuthorAffiliation] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Source registry metadata
# ---------------------------------------------------------------------------


@dataclass
class SourceParam:
    """Describes one configurable parameter for a source fetcher."""

    name: str
    description: str
    required: bool = False
    default: str | None = None
    secret: bool = False


@dataclass
class SourceDescriptor:
    """Metadata describing a registered publication source."""

    name: str
    display_name: str
    description: str
    params: list[SourceParam] = field(default_factory=list)
    resumable: bool = False
    """Whether ``sync()`` may pass this fetcher the per-part resume keywords.

    Defaults to ``False`` because :func:`register_source` is public: a fetcher
    written against an earlier bmlib does not accept them, and passing an
    unexpected keyword would raise inside the per-day handler and record a
    working source's day as failed.
    """


@dataclass
class PartCheckpoint:
    """One completed partition of a day, so a re-run can skip it.

    A day too large for one history session is fetched as parts (see
    ``fetchers/pubmed.py``). Each part's records and its checkpoint are written
    in one transaction, so a checkpoint never attests to records a rollback
    discarded. Not every finished part produces one: a part that reconciled
    short of its own promise, or that held a record which would not store, has
    its records written with no checkpoint beside them, so nothing here ever
    describes a part that was not fully delivered.

    ``part_key`` is opaque to storage: the partitioning scheme belongs to the
    fetcher, so a second scheme needs no schema change. ``part_scheme`` names
    which scheme wrote the key, so a scheme that changes is visible in the data
    rather than silently matching nothing.
    """

    part_scheme: str
    part_key: str
    promised: int
    record_count: int

    def to_dict(self) -> dict[str, Any]:
        """Return a plain-dict form of this checkpoint."""
        return {
            "part_scheme": self.part_scheme,
            "part_key": self.part_key,
            "promised": self.promised,
            "record_count": self.record_count,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PartCheckpoint:
        """Build a checkpoint from its plain-dict form."""
        return cls(
            part_scheme=str(data["part_scheme"]),
            part_key=str(data["part_key"]),
            promised=int(data["promised"]),
            record_count=int(data["record_count"]),
        )


# ---------------------------------------------------------------------------
# Retraction notices
# ---------------------------------------------------------------------------


class RetractionNature(StrEnum):
    """The kind of notice a Retraction Watch row records.

    ``OTHER`` is forward-compatibility, not a case the current export
    exercises: every one of the 71,306 real rows in the 2026-08-03 Crossref
    export carries one of the four named values. The vocabulary belongs to
    Retraction Watch, so a value this enum does not know must cost one row of
    fidelity rather than abort the import — the raw string is kept in
    :attr:`RetractionNotice.raw_nature`.
    """

    RETRACTION = "retraction"
    CORRECTION = "correction"
    EXPRESSION_OF_CONCERN = "expression_of_concern"
    REINSTATEMENT = "reinstatement"
    OTHER = "other"

    @classmethod
    def from_raw(cls, value: str | None) -> RetractionNature:
        """Map an export's ``RetractionNature`` cell onto this enum.

        Matching is case-insensitive on a stripped value: the export writes
        ``"Expression of concern"`` with a lower-case ``c``. An unrecognised
        or empty value maps to :attr:`OTHER`.
        """
        return _NATURE_BY_RAW.get((value or "").strip().lower(), cls.OTHER)


# Keyed by the *file's* wording (spaces, any case), which is a different
# vocabulary from the enum's own values (underscores). ``from_dict`` reads the
# latter, ``from_raw`` the former; conflating them silently maps every
# expression of concern to OTHER.
_NATURE_BY_RAW: dict[str, RetractionNature] = {
    "retraction": RetractionNature.RETRACTION,
    "correction": RetractionNature.CORRECTION,
    "expression of concern": RetractionNature.EXPRESSION_OF_CONCERN,
    "reinstatement": RetractionNature.REINSTATEMENT,
}


@dataclass
class RetractionNotice:
    """One Retraction Watch notice about one paper.

    A row of the export describes **two** papers, so both identifier pairs are
    carried under names that say which is which: :attr:`doi`/:attr:`pmid` are
    always the **retracted paper** (the export's ``OriginalPaper*`` columns),
    and :attr:`notice_doi`/:attr:`notice_pmid` are the retraction notice
    itself (its ``Retraction*`` columns). They are sometimes equal.

    Dates are ISO ``yyyy-mm-dd`` strings, matching
    :attr:`Publication.publication_date`.
    """

    record_id: str
    nature: RetractionNature

    doi: str | None = None
    pmid: str | None = None
    notice_doi: str | None = None
    notice_pmid: str | None = None
    title: str | None = None
    journal: str | None = None
    retraction_date: str | None = None
    original_paper_date: str | None = None
    reasons: list[str] = field(default_factory=list)
    raw_nature: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dictionary."""
        return {
            "record_id": self.record_id,
            "nature": self.nature.value,
            "doi": self.doi,
            "pmid": self.pmid,
            "notice_doi": self.notice_doi,
            "notice_pmid": self.notice_pmid,
            "title": self.title,
            "journal": self.journal,
            "retraction_date": self.retraction_date,
            "original_paper_date": self.original_paper_date,
            "reasons": list(self.reasons),
            "raw_nature": self.raw_nature,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RetractionNotice:
        """Deserialise from a dictionary produced by :meth:`to_dict`."""
        return cls(
            record_id=data["record_id"],
            nature=RetractionNature(data["nature"]),
            doi=data.get("doi"),
            pmid=data.get("pmid"),
            notice_doi=data.get("notice_doi"),
            notice_pmid=data.get("notice_pmid"),
            title=data.get("title"),
            journal=data.get("journal"),
            retraction_date=data.get("retraction_date"),
            original_paper_date=data.get("original_paper_date"),
            reasons=list(data.get("reasons", [])),
            raw_nature=data.get("raw_nature"),
        )
