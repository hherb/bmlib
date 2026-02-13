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


def store_publication(
    conn: Any,
    pub: Publication,
    fulltext_sources: Sequence[FullTextSource] | None = None,
) -> str:
    """Store a publication, de-duplicating by DOI then PMID.

    Returns ``"added"`` for a new record or ``"merged"`` if an existing
    record was found and updated.
    """
    now = _now_iso()

    # Try to find existing by DOI first, then PMID
    existing = None
    if pub.doi:
        existing = fetch_one(conn, "SELECT * FROM publications WHERE doi = ?", (pub.doi,))
    if existing is None and pub.pmid:
        existing = fetch_one(conn, "SELECT * FROM publications WHERE pmid = ?", (pub.pmid,))

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
    """Look up a publication by DOI, or return None."""
    row = fetch_one(conn, "SELECT * FROM publications WHERE doi = ?", (doi,))
    if row is None:
        return None
    return _row_to_publication(row)


def get_publication_by_pmid(conn: Any, pmid: str) -> Publication | None:
    """Look up a publication by PMID, or return None."""
    row = fetch_one(conn, "SELECT * FROM publications WHERE pmid = ?", (pmid,))
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
