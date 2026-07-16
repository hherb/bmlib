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

"""Storage layer for publications — pure functions over DB-API connections.

Provides de-duplicating insert, lookup by DOI/PMID, and full-text source
management.  Merging fills NULL fields from incoming records and appends
new sources, but never overwrites existing non-NULL values.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from bmlib.db import execute, fetch_one
from bmlib.publications.models import FullTextSource, Publication


def _now_iso() -> str:
    """Return the current UTC datetime as an ISO string."""
    return datetime.now(tz=UTC).isoformat()


# ---------------------------------------------------------------------------
# Identifier normalization
# ---------------------------------------------------------------------------

# Prefixes that sources sometimes prepend to a DOI. Stripped so the same
# work fetched from different sources dedups to a single canonical key.
_DOI_PREFIXES = (
    "https://doi.org/",
    "http://doi.org/",
    "https://dx.doi.org/",
    "http://dx.doi.org/",
    "doi:",
)


def _normalize_doi(doi: str | None) -> str | None:
    """Return a canonical, case-folded DOI, or ``None``.

    DOIs are case-insensitive (per the DOI handbook), but different sources
    disagree on case: PubMed preserves the registered form (often mixed
    case), while OpenAlex lower-cases everything. Storing and looking up a
    single canonical form (lower-case, prefix- and whitespace-stripped) is
    what makes cross-source deduplication actually work.
    """
    if not doi:
        return None
    d = doi.strip()
    lowered = d.lower()
    for prefix in _DOI_PREFIXES:
        if lowered.startswith(prefix):
            d = d[len(prefix) :]
            break
    d = d.strip().lower()
    return d or None


def _normalize_pmid(pmid: str | None) -> str | None:
    """Return a whitespace-stripped PMID, or ``None`` for an empty value."""
    if not pmid:
        return None
    return pmid.strip() or None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _row_to_publication(row: Any) -> Publication:
    """Convert a DB row (sqlite3.Row or dict-like) to a Publication."""
    return Publication(
        id=row["id"],
        title=row["title"],
        doi=row["doi"],
        pmid=row["pmid"],
        abstract=row["abstract"],
        authors=json.loads(row["authors"]) if row["authors"] else [],
        journal=row["journal"],
        publication_date=row["publication_date"],
        publication_types=json.loads(row["publication_types"]) if row["publication_types"] else [],
        keywords=json.loads(row["keywords"]) if row["keywords"] else [],
        is_open_access=bool(row["is_open_access"]),
        license=row["license"],
        sources=json.loads(row["sources"]) if row["sources"] else [],
        first_seen_source=row["first_seen_source"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _insert_publication(conn: Any, pub: Publication, now: str) -> int:
    """Insert a new publication and return the row id."""
    cur = execute(
        conn,
        "INSERT INTO publications"
        " (doi, pmid, title, abstract, authors, journal, publication_date,"
        "  publication_types, keywords, is_open_access, license,"
        "  sources, first_seen_source, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            pub.doi,
            pub.pmid,
            pub.title,
            pub.abstract,
            json.dumps(pub.authors),
            pub.journal,
            pub.publication_date,
            json.dumps(pub.publication_types),
            json.dumps(pub.keywords),
            int(pub.is_open_access),
            pub.license,
            json.dumps(pub.sources),
            pub.first_seen_source,
            now,
            now,
        ),
    )
    conn.commit()
    return cur.lastrowid


def _merge_publication(
    conn: Any,
    existing: Any,
    incoming: Publication,
    now: str,
) -> None:
    """Merge an incoming publication into an existing DB row.

    - Appends new sources from incoming to existing sources list.
    - Fills NULL fields from incoming via COALESCE-style logic.
    - Never overwrites existing non-NULL fields.
    """
    # Merge sources lists
    existing_sources = json.loads(existing["sources"]) if existing["sources"] else []
    for src in incoming.sources:
        if src not in existing_sources:
            existing_sources.append(src)
    merged_sources = json.dumps(existing_sources)

    # Merge authors: keep existing if non-empty, else take incoming
    existing_authors = existing["authors"]
    if not existing_authors or existing_authors == "[]":
        merged_authors = json.dumps(incoming.authors)
    else:
        merged_authors = existing_authors

    # Merge publication_types: keep existing if non-empty
    existing_pub_types = existing["publication_types"]
    if not existing_pub_types or existing_pub_types == "[]":
        merged_pub_types = json.dumps(incoming.publication_types)
    else:
        merged_pub_types = existing_pub_types

    # Merge keywords: keep existing if non-empty
    existing_keywords = existing["keywords"]
    if not existing_keywords or existing_keywords == "[]":
        merged_keywords = json.dumps(incoming.keywords)
    else:
        merged_keywords = existing_keywords

    execute(
        conn,
        "UPDATE publications SET"
        "  doi = COALESCE(doi, ?),"
        "  pmid = COALESCE(pmid, ?),"
        "  abstract = COALESCE(abstract, ?),"
        "  authors = ?,"
        "  journal = COALESCE(journal, ?),"
        "  publication_date = COALESCE(publication_date, ?),"
        "  publication_types = ?,"
        "  keywords = ?,"
        "  is_open_access = CASE WHEN is_open_access = 0 THEN ? ELSE is_open_access END,"
        "  license = COALESCE(license, ?),"
        "  sources = ?,"
        "  updated_at = ?"
        " WHERE id = ?",
        (
            incoming.doi,
            incoming.pmid,
            incoming.abstract,
            merged_authors,
            incoming.journal,
            incoming.publication_date,
            merged_pub_types,
            merged_keywords,
            int(incoming.is_open_access),
            incoming.license,
            merged_sources,
            now,
            existing["id"],
        ),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _consolidate_rows(conn: Any, keep: Any, drop: Any, now: str) -> None:
    """Merge the *drop* row into the *keep* row, then delete *drop*.

    Used when an incoming record carries both a DOI and a PMID that currently
    point at two different existing rows (a "split identity" that arises when a
    work is indexed by one identifier before its cross-reference to the other
    exists). Without this, the subsequent ``COALESCE`` merge would try to write
    the drop row's identifier onto the keep row and hit the UNIQUE constraint,
    aborting the write and leaving the duplicates stranded forever.

    Ordering matters: the drop row is deleted *before* its identifier is merged
    onto the keep row, so the unique index is free when the merge runs (SQLite
    enforces UNIQUE per statement, so no intermediate commit is needed). The
    whole consolidation stays uncommitted until the merge's own commit, so a
    failure part-way through is fully rollback-able and cannot lose the drop
    row's data.
    """
    keep_id = keep["id"]
    drop_id = drop["id"]

    # Move the drop row's full-text sources onto the keep row. UPDATE OR IGNORE
    # skips any (publication_id, url) pair that already exists on the keep row;
    # the leftover duplicates on the drop row are then removed.
    execute(
        conn,
        "UPDATE OR IGNORE fulltext_sources SET publication_id = ? WHERE publication_id = ?",
        (keep_id, drop_id),
    )
    execute(conn, "DELETE FROM fulltext_sources WHERE publication_id = ?", (drop_id,))

    # Snapshot the drop row's data, delete the row (freeing its unique
    # identifier), then fold its data into the keep row.
    drop_pub = _row_to_publication(drop)
    execute(conn, "DELETE FROM publications WHERE id = ?", (drop_id,))
    _merge_publication(conn, keep, drop_pub, now)


def store_publication(
    conn: Any,
    pub: Publication,
    fulltext_sources: Sequence[FullTextSource] | None = None,
) -> str:
    """Store a publication, de-duplicating by DOI then PMID.

    DOIs and PMIDs are normalized (see :func:`_normalize_doi` /
    :func:`_normalize_pmid`) before lookup and storage so the same work fetched
    from different sources — which disagree on DOI case and prefixes — resolves
    to a single row. ``pub`` is mutated in place to hold the canonical forms.

    Returns ``"added"`` for a new record or ``"merged"`` if an existing
    record was found and updated.
    """
    now = _now_iso()

    pub.doi = _normalize_doi(pub.doi)
    pub.pmid = _normalize_pmid(pub.pmid)

    # Look up by each identifier independently so we can detect a split
    # identity (DOI and PMID pointing at two different existing rows).
    row_by_doi = (
        fetch_one(conn, "SELECT * FROM publications WHERE doi = ?", (pub.doi,)) if pub.doi else None
    )
    row_by_pmid = (
        fetch_one(conn, "SELECT * FROM publications WHERE pmid = ?", (pub.pmid,))
        if pub.pmid
        else None
    )

    if row_by_doi is not None and row_by_pmid is not None and row_by_doi["id"] != row_by_pmid["id"]:
        # Split identity: consolidate the two rows into one (keep the DOI row),
        # then re-read it before merging the incoming record.
        _consolidate_rows(conn, row_by_doi, row_by_pmid, now)
        existing = fetch_one(conn, "SELECT * FROM publications WHERE id = ?", (row_by_doi["id"],))
    else:
        existing = row_by_doi if row_by_doi is not None else row_by_pmid

    if existing is not None:
        _merge_publication(conn, existing, pub, now)
        pub_id = existing["id"]
        result = "merged"
    else:
        pub_id = _insert_publication(conn, pub, now)
        result = "added"

    # Store any fulltext sources
    if fulltext_sources:
        for fts in fulltext_sources:
            add_fulltext_source(conn, pub_id, fts.source, fts.url, fts.format, fts.version)

    return result


def get_publication_by_doi(conn: Any, doi: str) -> Publication | None:
    """Look up a publication by DOI, or return None.

    The DOI is normalized before lookup so a query using any case or prefix
    variant matches the canonical stored form.
    """
    row = fetch_one(conn, "SELECT * FROM publications WHERE doi = ?", (_normalize_doi(doi),))
    if row is None:
        return None
    return _row_to_publication(row)


def get_publication_by_pmid(conn: Any, pmid: str) -> Publication | None:
    """Look up a publication by PMID, or return None."""
    row = fetch_one(conn, "SELECT * FROM publications WHERE pmid = ?", (_normalize_pmid(pmid),))
    if row is None:
        return None
    return _row_to_publication(row)


def add_fulltext_source(
    conn: Any,
    publication_id: int,
    source: str,
    url: str,
    fmt: str,
    version: str | None = None,
) -> bool:
    """Add a full-text source for a publication.

    Returns ``True`` if the record was inserted, ``False`` if the
    (publication_id, url) pair already exists.
    """
    now = _now_iso()
    cur = execute(
        conn,
        "INSERT INTO fulltext_sources"
        " (publication_id, source, url, format, version, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)"
        " ON CONFLICT (publication_id, url) DO NOTHING",
        (publication_id, source, url, fmt, version, now),
    )
    conn.commit()
    return cur.rowcount > 0
