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

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

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

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dictionary."""
        return {
            "id": self.id,
            "title": self.title,
            "doi": self.doi,
            "pmid": self.pmid,
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
        """Deserialise from a dictionary produced by :meth:`to_dict`."""
        return cls(
            id=data.get("id"),
            source=data["source"],
            date=data["date"],
            status=data["status"],
            record_count=data["record_count"],
            downloaded_at=_parse_datetime(data.get("downloaded_at")),
            last_verified_at=(
                _parse_datetime(data["last_verified_at"]) if data.get("last_verified_at") else None
            ),
        )


# ---------------------------------------------------------------------------
# Simple status / result types (no to_dict needed)
# ---------------------------------------------------------------------------


@dataclass
class FetchResult:
    """Result of fetching records from a source for a given date."""

    source: str
    date: str
    record_count: int
    status: str
    error: str | None = None


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
    """Summary report after completing a sync operation."""

    sources_synced: list[str]
    days_processed: int
    records_added: int
    records_merged: int
    records_failed: int
    errors: list[str] = field(default_factory=list)


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
