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

Works on both backends :mod:`bmlib.db` supports.  Placeholders come from
:func:`bmlib.db.placeholder`, and the one genuinely dialect-specific need —
reading back the id of a freshly inserted row — is handled by
:func:`_insert_publication` (``cur.lastrowid`` on SQLite, ``RETURNING id`` on
PostgreSQL).  All other SQL here is written in the intersection of the two
dialects.

Commit semantics: the public write functions (:func:`store_publication`,
:func:`add_fulltext_source`) each run inside a :func:`bmlib.db.transaction`
block. Called with no transaction open on the connection they commit exactly
once; when the connection is already inside a transaction — a caller-managed
``transaction(conn)`` block, or pending auto-begun writes from an earlier
bare ``execute()`` — they join it, leaving the commit (and the batch
boundary) to the caller. Bulk ingestion (see :mod:`bmlib.publications.sync`)
relies on this to batch a whole day of records into a single commit.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from bmlib.db import (
    execute,
    fetch_all,
    fetch_one,
    is_sqlite,
    placeholder,
    placeholders,
    transaction,
)
from bmlib.publications.models import AuthorAffiliation, FullTextSource, Grant, Publication


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


def _optional_column(row: Any, name: str) -> Any:
    """Read a column that may be absent, returning None if it is.

    Columns added after a release only reach an existing database via
    :func:`~bmlib.publications.schema.ensure_schema`. Reads should not fall
    over on a database whose owner has upgraded bmlib but not yet re-run it —
    sqlite3.Row raises IndexError for an unknown key, dicts raise KeyError.
    """
    try:
        return row[name]
    except (IndexError, KeyError):
        return None


def _row_to_publication(row: Any) -> Publication:
    """Convert a DB row (sqlite3.Row or dict-like) to a Publication."""
    return Publication(
        id=row["id"],
        title=row["title"],
        doi=row["doi"],
        pmid=row["pmid"],
        pmcid=_optional_column(row, "pmcid"),
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
    """Insert a new publication and return the row id.

    SQLite reports the new id on the cursor; PostgreSQL has no ``lastrowid``,
    so the id is asked for with ``RETURNING`` instead.
    """
    # One mapping, so the column list and the values cannot drift apart — a
    # hand-counted placeholder run has to be edited in three places whenever a
    # column is added, and only the database notices when it isn't.
    values = {
        "doi": pub.doi,
        "pmid": pub.pmid,
        "pmcid": pub.pmcid,
        "title": pub.title,
        "abstract": pub.abstract,
        "authors": json.dumps(pub.authors),
        "journal": pub.journal,
        "publication_date": pub.publication_date,
        "publication_types": json.dumps(pub.publication_types),
        "keywords": json.dumps(pub.keywords),
        "is_open_access": bool(pub.is_open_access),
        "license": pub.license,
        "sources": json.dumps(pub.sources),
        "first_seen_source": pub.first_seen_source,
        "created_at": now,
        "updated_at": now,
    }
    sqlite = is_sqlite(conn)
    columns = ", ".join(values)
    sql = f"INSERT INTO publications ({columns}) VALUES ({placeholders(conn, len(values))})"
    if not sqlite:
        sql += " RETURNING id"

    cur = execute(conn, sql, tuple(values.values()))
    if sqlite:
        return cur.lastrowid
    row = cur.fetchone()
    return row["id"] if isinstance(row, dict) else row[0]


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
    ph = placeholder(conn)

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
        f"  doi = COALESCE(doi, {ph}),"
        f"  pmid = COALESCE(pmid, {ph}),"
        f"  pmcid = COALESCE(pmcid, {ph}),"
        f"  abstract = COALESCE(abstract, {ph}),"
        f"  authors = {ph},"
        f"  journal = COALESCE(journal, {ph}),"
        f"  publication_date = COALESCE(publication_date, {ph}),"
        f"  publication_types = {ph},"
        f"  keywords = {ph},"
        # Open access is a one-way latch: once any source reports it, keep it.
        # Written as OR rather than a CASE on ``= 0`` because PostgreSQL stores
        # this as a real BOOLEAN, which does not compare against an integer.
        f"  is_open_access = (is_open_access OR {ph}),"
        f"  license = COALESCE(license, {ph}),"
        f"  sources = {ph},"
        f"  updated_at = {ph}"
        f" WHERE id = {ph}",
        (
            incoming.doi,
            incoming.pmid,
            incoming.pmcid,
            incoming.abstract,
            merged_authors,
            incoming.journal,
            incoming.publication_date,
            merged_pub_types,
            merged_keywords,
            bool(incoming.is_open_access),
            incoming.license,
            merged_sources,
            now,
            existing["id"],
        ),
    )


# ---------------------------------------------------------------------------
# Grants and affiliations
# ---------------------------------------------------------------------------

# The two child tables carry no UNIQUE constraint on their natural key, and
# that is deliberate. ``UNIQUE(publication_id, source, agency, grant_id)``
# looks like the obvious guard against a re-sync duplicating rows, but every
# column of a grant proper is nullable and both backends treat NULL as
# *distinct* in a unique index — so ``(1, 'pubmed', NULL, 'R01')`` inserts
# twice and the constraint protects nothing while appearing to. An expression
# index over ``COALESCE``d columns would work on both backends, but there is
# nothing left for it to catch: the fetcher collapses PubMed's verbatim repeats
# at parse time, and :func:`_replace_child_rows` below is idempotent per
# source. Both of those are reachable from a test; a unique index that has to
# be written around three nullable columns is not obviously correct at a
# glance, which is how the original trap looked too.


def _replace_child_rows(
    conn: Any,
    table: str,
    publication_id: int,
    columns: Sequence[str],
    rows: Sequence[tuple[Any, ...]],
    now: str,
) -> None:
    """Replace rows in *table* for each source present in *rows*.

    Each element of *rows* is ``(source, *values)``, with *values* matching
    *columns*. Rows are grouped by source, and each group replaces only that
    source's existing rows — every other source's are left alone. This is what
    lets PubMed's grants and OpenAlex's coexist; scoping by publication alone
    made the stored set depend on whichever source synced last, flip-flopping
    on every sync with no error and no warning.

    Delete-then-insert rather than insert-if-absent, so re-syncing one source
    is both idempotent and self-correcting: a corrected grant supersedes the
    stale one instead of accumulating beside it.

    Does nothing when *rows* is empty — there is no source to scope the delete
    to, and an absent ``<GrantList>`` means the record did not carry the data
    rather than that the funding was withdrawn.
    """
    if not rows:
        return

    # Not ``str(source)``: coercing here would turn a ``None`` source — a
    # NOT NULL violation the database would reject loudly — into the literal
    # string "None", a source name that looks real, matches nothing, and can
    # never be replaced by a later sync because no record will ever name it.
    by_source: dict[Any, list[tuple[Any, ...]]] = {}
    for source, *values in rows:
        by_source.setdefault(source, []).append(tuple(values))

    ph = placeholder(conn)
    named = ("publication_id", "source", *columns, "created_at")
    sql = f"INSERT INTO {table} ({', '.join(named)}) VALUES ({placeholders(conn, len(named))})"

    for source, group in by_source.items():
        execute(
            conn,
            f"DELETE FROM {table} WHERE publication_id = {ph} AND source = {ph}",
            (publication_id, source),
        )
        for row in group:
            execute(conn, sql, (publication_id, source, *row, now))


def _relocate_child_rows(conn: Any, table: str, keep_id: int, drop_id: int) -> None:
    """Move *drop_id*'s rows in *table* onto *keep_id*, per source.

    Called before the drop row is deleted. Both backends enforce foreign keys
    (:func:`~bmlib.db.connect_sqlite` sets ``PRAGMA foreign_keys=ON``), so a row
    still pointing at the doomed publication makes the ``DELETE`` raise and
    aborts the whole store — every child must be off the drop row first.

    A source the keep row already has wins, so the drop row's rows for that
    source are discarded; sources the keep row lacks move across. That is
    :func:`_merge_publication`'s "fill, never overwrite" rule at source
    granularity — merging two rows' accounts of what PubMed said would produce
    a set PubMed never asserted, while a source only the drop row saw is real
    information the keep row should gain.

    Returns immediately when the two ids are equal. The caller only reaches
    here having established they differ, but the whole method rests on that:
    the DELETE's subquery reads the keep row's sources while the DELETE itself
    removes the drop row's, and those sets are disjoint *only* because the ids
    are. Were they ever the same, the subquery would match every row it is
    about to delete, the DELETE would wipe the publication's entire set and the
    UPDATE would find nothing left to move — total loss, silently. Two lines to
    make that unreachable by construction rather than by a caller's invariant.
    """
    if keep_id == drop_id:
        return

    ph = placeholder(conn)
    # Discard first, so the surviving rows can move in one unconditional
    # statement. (An anti-join UPDATE would work too, but "delete what loses,
    # move what is left" needs no correlated subquery on either backend.)
    execute(
        conn,
        f"DELETE FROM {table} WHERE publication_id = {ph}"
        f" AND source IN (SELECT source FROM {table} WHERE publication_id = {ph})",
        (drop_id, keep_id),
    )
    execute(
        conn,
        f"UPDATE {table} SET publication_id = {ph} WHERE publication_id = {ph}",
        (keep_id, drop_id),
    )


def get_grants(conn: Any, publication_id: int) -> list[Grant]:
    """Return the funding awards stored for a publication.

    Args:
        conn: An open DB-API connection.
        publication_id: The publication's row id.

    Returns:
        Every :class:`~bmlib.publications.models.Grant` on record, in insertion
        order; an empty list when none were stored.
    """
    ph = placeholder(conn)
    rows = fetch_all(
        conn,
        "SELECT id, publication_id, source, agency, grant_id, country FROM publication_grants"
        f" WHERE publication_id = {ph} ORDER BY id",
        (publication_id,),
    )
    return [
        Grant(
            id=row["id"],
            publication_id=row["publication_id"],
            source=row["source"],
            agency=row["agency"],
            grant_id=row["grant_id"],
            country=row["country"],
        )
        for row in rows
    ]


def get_author_affiliations(conn: Any, publication_id: int) -> list[AuthorAffiliation]:
    """Return the author affiliations stored for a publication.

    Args:
        conn: An open DB-API connection.
        publication_id: The publication's row id.

    Returns:
        Every :class:`~bmlib.publications.models.AuthorAffiliation` on record,
        ordered by author position so the first and senior authors are found at
        the ends; an empty list when none were stored.
    """
    ph = placeholder(conn)
    rows = fetch_all(
        conn,
        "SELECT id, publication_id, source, author, affiliation, position"
        f" FROM publication_affiliations WHERE publication_id = {ph} ORDER BY position, id",
        (publication_id,),
    )
    return [
        AuthorAffiliation(
            id=row["id"],
            publication_id=row["publication_id"],
            source=row["source"],
            author=row["author"],
            affiliation=row["affiliation"],
            position=row["position"],
        )
        for row in rows
    ]


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
    onto the keep row, so the unique index is free when the merge runs (both
    backends enforce UNIQUE per statement, so no intermediate commit is
    needed). The whole consolidation runs inside :func:`store_publication`'s
    transaction, so a failure part-way through is fully rollback-able and
    cannot lose the drop row's data.
    """
    ph = placeholder(conn)
    keep_id = keep["id"]
    drop_id = drop["id"]

    # Move the drop row's full-text sources onto the keep row, skipping any URL
    # the keep row already has — moving those would violate
    # UNIQUE(publication_id, url). The leftovers on the drop row are then
    # removed. (SQLite could say this as UPDATE OR IGNORE, but PostgreSQL has
    # no such form and the explicit anti-join reads the same on both.)
    execute(
        conn,
        f"UPDATE fulltext_sources SET publication_id = {ph}"
        f" WHERE publication_id = {ph}"
        f"   AND url NOT IN (SELECT url FROM fulltext_sources WHERE publication_id = {ph})",
        (keep_id, drop_id, keep_id),
    )
    execute(conn, f"DELETE FROM fulltext_sources WHERE publication_id = {ph}", (drop_id,))

    # Same for the grant and affiliation rows — every child must be off the drop
    # row before it is deleted, or the foreign key stops the DELETE.
    _relocate_child_rows(conn, "publication_grants", keep_id, drop_id)
    _relocate_child_rows(conn, "publication_affiliations", keep_id, drop_id)

    # Snapshot the drop row's data, delete the row (freeing its unique
    # identifier), then fold its data into the keep row.
    drop_pub = _row_to_publication(drop)
    execute(conn, f"DELETE FROM publications WHERE id = {ph}", (drop_id,))
    _merge_publication(conn, keep, drop_pub, now)


def store_publication(
    conn: Any,
    pub: Publication,
    fulltext_sources: Sequence[FullTextSource] | None = None,
    *,
    grants: Sequence[Grant] | None = None,
    affiliations: Sequence[AuthorAffiliation] | None = None,
) -> str:
    """Store a publication, de-duplicating by DOI then PMID.

    DOIs and PMIDs are normalized (see :func:`_normalize_doi` /
    :func:`_normalize_pmid`) before lookup and storage so the same work fetched
    from different sources — which disagree on DOI case and prefixes — resolves
    to a single row. ``pub`` is mutated in place to hold the canonical forms.

    The whole store (row consolidation, insert/merge, full-text sources, grants
    and affiliations) is one atomic transaction. Standalone calls commit on
    return; calls made inside a caller's ``transaction(conn)`` block join it,
    deferring the commit to the caller (see the module docstring).

    Args:
        conn: An open DB-API connection.
        pub: The publication to store; mutated in place to hold canonical
            identifiers.
        fulltext_sources: Full-text locations to record alongside it. These
            accumulate — every source's URLs are kept.
        grants: Funding awards. Supplying any **replaces** whatever is stored
            for this publication; supplying none leaves it untouched (see
            :func:`_replace_child_rows`).
        affiliations: Author affiliations, with the same replace-or-leave rule.

    Returns:
        ``"added"`` for a new record, or ``"merged"`` if an existing record was
        found and updated.
    """
    now = _now_iso()
    ph = placeholder(conn)

    pub.doi = _normalize_doi(pub.doi)
    pub.pmid = _normalize_pmid(pub.pmid)

    with transaction(conn):
        # Look up by each identifier independently so we can detect a split
        # identity (DOI and PMID pointing at two different existing rows).
        row_by_doi = (
            fetch_one(conn, f"SELECT * FROM publications WHERE doi = {ph}", (pub.doi,))
            if pub.doi
            else None
        )
        row_by_pmid = (
            fetch_one(conn, f"SELECT * FROM publications WHERE pmid = {ph}", (pub.pmid,))
            if pub.pmid
            else None
        )

        if (
            row_by_doi is not None
            and row_by_pmid is not None
            and row_by_doi["id"] != row_by_pmid["id"]
        ):
            # Split identity: consolidate the two rows into one (keep the DOI
            # row), then re-read it before merging the incoming record.
            _consolidate_rows(conn, row_by_doi, row_by_pmid, now)
            existing = fetch_one(
                conn, f"SELECT * FROM publications WHERE id = {ph}", (row_by_doi["id"],)
            )
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

        _replace_child_rows(
            conn,
            "publication_grants",
            pub_id,
            ("agency", "grant_id", "country"),
            [(g.source, g.agency, g.grant_id, g.country) for g in grants or ()],
            now,
        )
        _replace_child_rows(
            conn,
            "publication_affiliations",
            pub_id,
            ("author", "affiliation", "position"),
            [(a.source, a.author, a.affiliation, a.position) for a in affiliations or ()],
            now,
        )

    return result


def get_publication_by_doi(conn: Any, doi: str) -> Publication | None:
    """Look up a publication by DOI, or return None.

    The DOI is normalized before lookup so a query using any case or prefix
    variant matches the canonical stored form.
    """
    ph = placeholder(conn)
    row = fetch_one(conn, f"SELECT * FROM publications WHERE doi = {ph}", (_normalize_doi(doi),))
    if row is None:
        return None
    return _row_to_publication(row)


def get_publication_by_pmid(conn: Any, pmid: str) -> Publication | None:
    """Look up a publication by PMID, or return None."""
    ph = placeholder(conn)
    row = fetch_one(conn, f"SELECT * FROM publications WHERE pmid = {ph}", (_normalize_pmid(pmid),))
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

    Commits when called standalone; joins the caller's open transaction
    otherwise (see the module docstring).

    Returns ``True`` if the record was inserted, ``False`` if the
    (publication_id, url) pair already exists.
    """
    now = _now_iso()
    params = (publication_id, source, url, fmt, version, now)
    with transaction(conn):
        cur = execute(
            conn,
            "INSERT INTO fulltext_sources"
            " (publication_id, source, url, format, version, created_at)"
            f" VALUES ({placeholders(conn, len(params))})"
            " ON CONFLICT (publication_id, url) DO NOTHING",
            params,
        )
    return cur.rowcount > 0
