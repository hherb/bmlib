// bmlib — shared library for biomedical literature tools
// Copyright (C) 2024-2026 Dr Horst Herb
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with this program.  If not, see <https://www.gnu.org/licenses/>.

//! Publication storage: deduplication by DOI/PMID, merge-on-upsert.
//!
//! A port of `bmlib/publications/storage.py`.
//!
//! # What is pure here, and why it is separated
//!
//! The Python module is a set of functions over a DB-API connection, so its
//! *rules* — how a DOI canonicalises, which fields a re-sync may fill, what
//! happens to two rows that turn out to be the same paper — are only reachable
//! through a database. In Rust the rules are plain functions and the SQL sits
//! on top of them, so each can be tested for what it decides rather than for
//! what it wrote. Every one of them is a place where a plausible alternative
//! silently changes which papers exist:
//!
//! | Rule | A plausible wrong version |
//! |---|---|
//! | [`normalize_doi`] | preserve case, or keep the `https://doi.org/` prefix — and the same paper dedups to two rows |
//! | [`merge_sources`] | replace instead of union — and a paper's provenance shrinks on every sync |
//! | [`merge_json_list`] | overwrite — and a re-sync from a thinner source erases the abstract's authors |
//! | [`group_by_source`] | group by publication — and two sources' funding data overwrite each other on alternate syncs |
//!
//! The last is the one the Python docstring spends the most words on, and its
//! failure is invisible: no error, no warning, and a set that flip-flops
//! depending on which source synced last.

use std::collections::BTreeMap;

use crate::db::operations::{execute, executemany, fetch_all, fetch_one};
use crate::db::{Db, DbError, Value as DbValue};
use crate::publications::models::{now_utc, AuthorAffiliation, FullTextSource, Grant, Publication};

/// Prefixes sources sometimes prepend to a DOI.
///
/// Stripped so the same work fetched from different sources dedups to a single
/// canonical key.
pub const DOI_PREFIXES: [&str; 5] = [
    "https://doi.org/",
    "http://doi.org/",
    "https://dx.doi.org/",
    "http://dx.doi.org/",
    "doi:",
];

/// A canonical, case-folded DOI, or `None`.
///
/// DOIs are case-insensitive per the DOI handbook, but different sources
/// disagree on case: PubMed preserves the registered form (often mixed case),
/// while OpenAlex lower-cases everything. Storing and looking up a single
/// canonical form is what makes cross-source deduplication actually work.
///
/// The prefix is stripped **once** — Python's loop breaks — so
/// `"doi:https://doi.org/10.1/X"` becomes `"10.1/x"` and not `"doi:10.1/x"`.
#[must_use]
pub fn normalize_doi(doi: Option<&str>) -> Option<String> {
    let doi = doi.filter(|d| !d.is_empty())?;
    let mut d = doi.trim();
    let lowered = d.to_lowercase();
    for prefix in DOI_PREFIXES {
        if lowered.starts_with(prefix) {
            d = &d[prefix.len()..];
            break;
        }
    }
    let d = d.trim().to_lowercase();
    if d.is_empty() {
        None
    } else {
        Some(d)
    }
}

/// A whitespace-stripped PMID, or `None` for an empty value.
#[must_use]
pub fn normalize_pmid(pmid: Option<&str>) -> Option<String> {
    let pmid = pmid.filter(|p| !p.is_empty())?;
    let trimmed = pmid.trim();
    if trimmed.is_empty() {
        None
    } else {
        Some(trimmed.to_string())
    }
}

/// Union two source lists, preserving the existing order and appending only
/// what is new.
///
/// Appends rather than replaces: provenance accumulates, and a source that has
/// already been seen must not be duplicated by a re-sync.
#[must_use]
pub fn merge_sources(existing: &[String], incoming: &[String]) -> Vec<String> {
    let mut merged = existing.to_vec();
    for src in incoming {
        if !merged.contains(src) {
            merged.push(src.clone());
        }
    }
    merged
}

/// Keep the existing JSON list unless it is empty, in which case take the
/// incoming one.
///
/// "Fill, never overwrite": a re-sync from a source that carries no authors
/// must not erase the authors a previous source supplied. The emptiness test is
/// on the **serialised** form, because that is what the column holds — `"[]"`
/// and `""` both mean nothing was stored.
#[must_use]
pub fn merge_json_list(existing: Option<&str>, incoming: &[String]) -> String {
    let existing_is_empty = match existing {
        None => true,
        Some(s) => s.is_empty() || s == "[]",
    };
    if existing_is_empty {
        serde_json::to_string(incoming).unwrap_or_else(|_| "[]".to_string())
    } else {
        existing.unwrap_or("[]").to_string()
    }
}

/// Why a child-row batch could not be scoped.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MissingSource {
    /// The table the rows were destined for.
    pub table: &'static str,
}

impl std::fmt::Display for MissingSource {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "{}: every row must name the source that asserted it, got ''. \
             Set Grant.source / AuthorAffiliation.source, or let sync() stamp it.",
            self.table
        )
    }
}

impl std::error::Error for MissingSource {}

/// Group child rows by the source that asserted them.
///
/// Each group replaces only that source's existing rows, leaving every other
/// source's alone. This is what lets PubMed's grants and OpenAlex's coexist;
/// scoping by publication alone made the stored set depend on whichever source
/// synced last, flip-flopping on every sync with no error and no warning.
///
/// A row naming no source is **refused**, not stored. Scoping is the whole
/// mechanism, so an unnamed row is one no later sync can ever replace: it is
/// not merely unlabelled, it is permanently stuck, accumulating a duplicate
/// beside the correctly-labelled row on every subsequent sync. `None` would be
/// caught by the `NOT NULL` column, but `""` is what the dataclass defaults to
/// and the column accepts it happily — so the check lives here, where both
/// fail the same way and the message can say what to do.
///
/// # Errors
///
/// [`MissingSource`] when any row's source is absent or empty.
pub fn group_by_source<'a, T: 'a>(
    table: &'static str,
    rows: &'a [(String, T)],
) -> Result<BTreeMap<&'a str, Vec<&'a T>>, MissingSource> {
    let mut by_source: BTreeMap<&str, Vec<&T>> = BTreeMap::new();
    for (source, value) in rows {
        if source.is_empty() {
            return Err(MissingSource { table });
        }
        by_source.entry(source.as_str()).or_default().push(value);
    }
    Ok(by_source)
}

// ---------------------------------------------------------------------------
// Row conversion
// ---------------------------------------------------------------------------

/// Read a column that may be absent, returning `None` if it is.
///
/// Columns added after a release only reach an existing database via
/// `ensure_schema`. Reads should not fall over on a database whose owner has
/// upgraded bmlib but not yet re-run it.
#[must_use]
pub fn optional_column(row: &crate::db::Row, name: &str) -> Option<DbValue> {
    row.get(name).ok().cloned()
}

/// Read a column as text, `None` when it is absent or NULL.
///
/// **`None` for both, deliberately**: a column that does not exist and one holding
/// SQL NULL are the same answer to "what does this row say here?", and a caller
/// distinguishing them would be reading the schema rather than the data. Shared
/// with `publications/retractions.rs` rather than copied, since a second spelling
/// is where a NULL-handling difference would hide.
pub fn text(row: &crate::db::Row, name: &str) -> Option<String> {
    row.get(name)
        .ok()
        .and_then(DbValue::as_str)
        .map(str::to_string)
}

/// Read a JSON-encoded list column, empty when it is absent, NULL or unparseable.
///
/// A malformed value yields an empty list rather than an error: these columns are
/// written by this library, and a row that will not decode is not worth failing a
/// whole read for — the alternative is that one bad row makes a publication
/// unfetchable.
pub fn json_list(row: &crate::db::Row, name: &str) -> Vec<String> {
    match text(row, name) {
        Some(raw) if !raw.is_empty() => serde_json::from_str(&raw).unwrap_or_default(),
        _ => Vec::new(),
    }
}

/// Convert a database row into a [`Publication`].
///
/// # Errors
///
/// If a required column is absent or of the wrong type.
pub fn row_to_publication(row: &crate::db::Row) -> Result<Publication, DbError> {
    let required = |name: &str| -> Result<String, DbError> {
        text(row, name).ok_or_else(|| DbError::Column(format!("publications.{name} is not text")))
    };
    Ok(Publication {
        id: row.get("id").ok().and_then(DbValue::as_i64),
        title: required("title")?,
        doi: text(row, "doi"),
        pmid: text(row, "pmid"),
        pmcid: text(row, "pmcid"),
        abstract_text: text(row, "abstract"),
        authors: json_list(row, "authors"),
        journal: text(row, "journal"),
        publication_date: text(row, "publication_date"),
        publication_types: json_list(row, "publication_types"),
        keywords: json_list(row, "keywords"),
        is_open_access: row
            .get("is_open_access")
            .ok()
            .map(|v| v.as_i64().unwrap_or(0) != 0)
            .unwrap_or(false),
        license: text(row, "license"),
        sources: json_list(row, "sources"),
        first_seen_source: required("first_seen_source")?,
        created_at: text(row, "created_at").unwrap_or_default(),
        updated_at: text(row, "updated_at").unwrap_or_default(),
    })
}

// ---------------------------------------------------------------------------
// Insert and merge
// ---------------------------------------------------------------------------

/// The publication columns an `INSERT` names, in order.
///
/// One list, so the column names and the values cannot drift apart — a
/// hand-counted placeholder run has to be edited in three places whenever a
/// column is added, and only the database notices when it is not.
pub const INSERT_COLUMNS: [&str; 16] = [
    "doi",
    "pmid",
    "pmcid",
    "title",
    "abstract",
    "authors",
    "journal",
    "publication_date",
    "publication_types",
    "keywords",
    "is_open_access",
    "license",
    "sources",
    "first_seen_source",
    "created_at",
    "updated_at",
];

/// The values matching [`INSERT_COLUMNS`], in the same order.
#[must_use]
pub fn insert_values(pub_: &Publication, now: &str) -> Vec<DbValue> {
    let opt = |v: &Option<String>| v.clone().map_or(DbValue::Null, DbValue::Text);
    vec![
        opt(&pub_.doi),
        opt(&pub_.pmid),
        opt(&pub_.pmcid),
        DbValue::Text(pub_.title.clone()),
        opt(&pub_.abstract_text),
        DbValue::Text(serde_json::to_string(&pub_.authors).unwrap_or_else(|_| "[]".into())),
        opt(&pub_.journal),
        opt(&pub_.publication_date),
        DbValue::Text(
            serde_json::to_string(&pub_.publication_types).unwrap_or_else(|_| "[]".into()),
        ),
        DbValue::Text(serde_json::to_string(&pub_.keywords).unwrap_or_else(|_| "[]".into())),
        // PostgreSQL stores a real BOOLEAN; SQLite has none, so `0`/`1`.
        DbValue::Int(i64::from(pub_.is_open_access)),
        opt(&pub_.license),
        DbValue::Text(serde_json::to_string(&pub_.sources).unwrap_or_else(|_| "[]".into())),
        DbValue::Text(pub_.first_seen_source.clone()),
        DbValue::Text(now.to_string()),
        DbValue::Text(now.to_string()),
    ]
}

/// The merged value of each field a re-sync may touch.
///
/// Separated from the `UPDATE` so the *decision* is testable without a
/// database: "fill, never overwrite" is the rule, and the two exceptions are
/// called out below.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MergedFields {
    /// Authors, publication types, keywords and sources, serialised.
    pub authors: String,
    /// Publication types, serialised.
    pub publication_types: String,
    /// Keywords, serialised.
    pub keywords: String,
    /// Sources, serialised.
    pub sources: String,
}

/// Decide the merged value of the four list-valued columns.
///
/// The three JSON lists follow "keep the existing unless it is empty". `sources`
/// is the exception: it **unions**, because provenance is additive — every
/// source that has ever asserted the record stays named.
#[must_use]
pub fn merged_fields(existing: &crate::db::Row, incoming: &Publication) -> MergedFields {
    MergedFields {
        authors: merge_json_list(text(existing, "authors").as_deref(), &incoming.authors),
        publication_types: merge_json_list(
            text(existing, "publication_types").as_deref(),
            &incoming.publication_types,
        ),
        keywords: merge_json_list(text(existing, "keywords").as_deref(), &incoming.keywords),
        sources: serde_json::to_string(&merge_sources(
            &json_list(existing, "sources"),
            &incoming.sources,
        ))
        .unwrap_or_else(|_| "[]".to_string()),
    }
}

/// Insert a new publication and return the row id.
///
/// SQLite reports the new id on the connection; PostgreSQL has no `lastrowid`,
/// so the id is asked for with `RETURNING` instead.
///
/// # Errors
///
/// Propagates the driver's error.
pub fn insert_publication(db: &mut dyn Db, pub_: &Publication, now: &str) -> Result<i64, DbError> {
    let columns = INSERT_COLUMNS.join(", ");
    let sql = format!(
        "INSERT INTO publications ({columns}) VALUES ({})",
        crate::db::backend::placeholders(INSERT_COLUMNS.len())
    );
    if db.dialect() == crate::db::backend::Dialect::Sqlite {
        execute(db, &sql, &insert_values(pub_, now))?;
        db.last_insert_rowid()
            .ok_or_else(|| DbError::Column("SQLite reported no lastrowid".into()))
    } else {
        let row = fetch_one(
            db,
            &format!("{sql} RETURNING id"),
            &insert_values(pub_, now),
        )?;
        row.ok_or_else(|| DbError::Column("RETURNING id produced no row".into()))?
            .get_i64("id")
    }
}

/// Merge an incoming publication into an existing database row.
///
/// - Unions the source lists.
/// - Fills `NULL` fields from the incoming record.
/// - **Never overwrites an existing non-`NULL` field.**
/// - Latches `is_open_access` on: once any source reports it, it stays.
///   Written as `OR` rather than a `CASE ON = 0` because PostgreSQL stores a
///   real `BOOLEAN`, which does not compare against an integer.
///
/// # Errors
///
/// Propagates the driver's error.
pub fn merge_publication(
    db: &mut dyn Db,
    existing: &crate::db::Row,
    incoming: &Publication,
    now: &str,
) -> Result<(), DbError> {
    let merged = merged_fields(existing, incoming);
    let id = existing.get_i64("id")?;
    let sql = "UPDATE publications SET\
         \n  doi = COALESCE(doi, ?),\
         \n  pmid = COALESCE(pmid, ?),\
         \n  pmcid = COALESCE(pmcid, ?),\
         \n  abstract = COALESCE(abstract, ?),\
         \n  authors = ?,\
         \n  journal = COALESCE(journal, ?),\
         \n  publication_date = COALESCE(publication_date, ?),\
         \n  publication_types = ?,\
         \n  keywords = ?,\
         \n  is_open_access = (is_open_access OR ?),\
         \n  license = COALESCE(license, ?),\
         \n  sources = ?,\
         \n  updated_at = ?\
         \n WHERE id = ?";
    execute(
        db,
        sql,
        &[
            incoming.doi.clone().map_or(DbValue::Null, DbValue::Text),
            incoming.pmid.clone().map_or(DbValue::Null, DbValue::Text),
            incoming.pmcid.clone().map_or(DbValue::Null, DbValue::Text),
            incoming
                .abstract_text
                .clone()
                .map_or(DbValue::Null, DbValue::Text),
            DbValue::Text(merged.authors),
            incoming
                .journal
                .clone()
                .map_or(DbValue::Null, DbValue::Text),
            incoming
                .publication_date
                .clone()
                .map_or(DbValue::Null, DbValue::Text),
            DbValue::Text(merged.publication_types),
            DbValue::Text(merged.keywords),
            DbValue::Int(i64::from(incoming.is_open_access)),
            incoming
                .license
                .clone()
                .map_or(DbValue::Null, DbValue::Text),
            DbValue::Text(merged.sources),
            DbValue::Text(now.to_string()),
            DbValue::Int(id),
        ],
    )?;
    Ok(())
}

/// Replace rows in `table` for each source present in `rows`.
///
/// Rows are grouped by source, and each group replaces only that source's
/// existing rows — every other source's are left alone.
///
/// Delete-then-insert rather than insert-if-absent, so re-syncing one source is
/// both idempotent and self-correcting: a corrected grant supersedes the stale
/// one instead of accumulating beside it.
///
/// Does nothing when `rows` is empty — there is no source to scope the delete
/// to, and an absent `<GrantList>` means the record did not carry the data
/// rather than that the funding was withdrawn.
///
/// # Errors
///
/// [`MissingSource`] if any row names no source; otherwise the driver's.
pub fn replace_child_rows<T>(
    db: &mut dyn Db,
    table: &'static str,
    publication_id: i64,
    columns: &[&str],
    rows: &[(String, T)],
    values_of: impl Fn(&T) -> Vec<DbValue>,
    now: &str,
) -> Result<(), ReplaceError> {
    if rows.is_empty() {
        return Ok(());
    }
    let by_source = group_by_source(table, rows)?;

    let mut named: Vec<&str> = vec!["publication_id", "source"];
    named.extend_from_slice(columns);
    named.push("created_at");
    let sql = format!(
        "INSERT INTO {table} ({}) VALUES ({})",
        named.join(", "),
        crate::db::backend::placeholders(named.len())
    );

    for (source, group) in by_source {
        execute(
            db,
            &format!("DELETE FROM {table} WHERE publication_id = ? AND source = ?"),
            &[
                DbValue::Int(publication_id),
                DbValue::Text(source.to_string()),
            ],
        )
        .map_err(ReplaceError::Db)?;

        // One batch for the group rather than one round trip per row: a PubMed
        // day is thousands of records each carrying an affiliation per author.
        let batch: Vec<Vec<DbValue>> = group
            .iter()
            .map(|row| {
                let mut params = vec![
                    DbValue::Int(publication_id),
                    DbValue::Text(source.to_string()),
                ];
                params.extend(values_of(row));
                params.push(DbValue::Text(now.to_string()));
                params
            })
            .collect();
        executemany(db, &sql, &batch).map_err(ReplaceError::Db)?;
    }
    Ok(())
}

/// Why a child-row batch was refused.
#[derive(Debug)]
pub enum ReplaceError {
    /// A row named no source.
    MissingSource(MissingSource),
    /// The driver failed.
    Db(DbError),
}

impl std::fmt::Display for ReplaceError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ReplaceError::MissingSource(e) => write!(f, "{e}"),
            ReplaceError::Db(e) => write!(f, "{e}"),
        }
    }
}

impl std::error::Error for ReplaceError {}

impl From<DbError> for ReplaceError {
    fn from(e: DbError) -> Self {
        ReplaceError::Db(e)
    }
}

impl From<MissingSource> for ReplaceError {
    fn from(e: MissingSource) -> Self {
        ReplaceError::MissingSource(e)
    }
}

/// Move `drop_id`'s rows in `table` onto `keep_id`, per source.
///
/// A source the keep row already has wins, so the drop row's rows for that
/// source are discarded; sources the keep row lacks move across. That is
/// [`merge_publication`]'s "fill, never overwrite" rule at source granularity —
/// merging two rows' accounts of what PubMed said would produce a set PubMed
/// never asserted, while a source only the drop row saw is real information the
/// keep row should gain.
///
/// Returns immediately when the two ids are equal. The caller only reaches here
/// having established they differ, but the whole method rests on that: the
/// `DELETE`'s subquery reads the keep row's sources while the `DELETE` itself
/// removes the drop row's, and those sets are disjoint *only* because the ids
/// are. Were they ever the same, the subquery would match every row it is about
/// to delete, the `DELETE` would wipe the publication's entire set and the
/// `UPDATE` would find nothing left to move — total loss, silently.
///
/// # Errors
///
/// Propagates the driver's error.
pub fn relocate_child_rows(
    db: &mut dyn Db,
    table: &'static str,
    keep_id: i64,
    drop_id: i64,
) -> Result<(), DbError> {
    if keep_id == drop_id {
        return Ok(());
    }
    // Discard first, so the surviving rows can move in one unconditional
    // statement.
    execute(
        db,
        // `concat!`, not a `\` continuation: a trailing backslash eats the next
        // line's leading whitespace, so Python's adjacent literals — which keep
        // it — would meet as `?AND`. See `publications/schema.rs`'s
        // `existing_columns` for the form that actually failed on PostgreSQL.
        &format!(
            concat!(
                "DELETE FROM {table} WHERE publication_id = ?",
                " AND source IN (SELECT source FROM {table} WHERE publication_id = ?)"
            ),
            table = table
        ),
        &[DbValue::Int(drop_id), DbValue::Int(keep_id)],
    )?;
    execute(
        db,
        &format!("UPDATE {table} SET publication_id = ? WHERE publication_id = ?"),
        &[DbValue::Int(keep_id), DbValue::Int(drop_id)],
    )?;
    Ok(())
}

/// Merge the `drop` row into the `keep` row, then delete `drop`.
///
/// Used when an incoming record carries both a DOI and a PMID that currently
/// point at two different existing rows — a "split identity" that arises when a
/// work is indexed by one identifier before its cross-reference to the other
/// exists. Without this, the subsequent `COALESCE` merge would try to write the
/// drop row's identifier onto the keep row and hit the `UNIQUE` constraint,
/// aborting the write and leaving the duplicates stranded forever.
///
/// Ordering matters: the drop row is deleted **before** its identifier is
/// merged onto the keep row, so the unique index is free when the merge runs.
///
/// # Errors
///
/// Propagates the driver's error.
pub fn consolidate_rows(
    db: &mut dyn Db,
    keep: &crate::db::Row,
    drop: &crate::db::Row,
    now: &str,
) -> Result<(), DbError> {
    let keep_id = keep.get_i64("id")?;
    let drop_id = drop.get_i64("id")?;

    // Move the drop row's full-text sources onto the keep row, skipping any URL
    // the keep row already has — moving those would violate
    // `UNIQUE(publication_id, url)`. The leftovers on the drop row are then
    // removed.
    execute(
        db,
        // The spaces are load-bearing; see the note on the `DELETE` above.
        concat!(
            "UPDATE fulltext_sources SET publication_id = ?",
            " WHERE publication_id = ?",
            "   AND url NOT IN (SELECT url FROM fulltext_sources WHERE publication_id = ?)"
        ),
        &[
            DbValue::Int(keep_id),
            DbValue::Int(drop_id),
            DbValue::Int(keep_id),
        ],
    )?;
    execute(
        db,
        "DELETE FROM fulltext_sources WHERE publication_id = ?",
        &[DbValue::Int(drop_id)],
    )?;

    relocate_child_rows(db, "publication_grants", keep_id, drop_id)?;
    relocate_child_rows(db, "publication_affiliations", keep_id, drop_id)?;

    // Snapshot the drop row's data, delete the row (freeing its unique
    // identifier), then fold its data into the keep row.
    let drop_pub = row_to_publication(drop)?;
    execute(
        db,
        "DELETE FROM publications WHERE id = ?",
        &[DbValue::Int(drop_id)],
    )?;
    merge_publication(db, keep, &drop_pub, now)
}

/// What `store_publication` did.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StoreOutcome {
    /// A new row was inserted.
    Added,
    /// An existing row was found and updated.
    Merged,
}

impl StoreOutcome {
    /// The word Python returns.
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            StoreOutcome::Added => "added",
            StoreOutcome::Merged => "merged",
        }
    }
}

/// Store a publication, de-duplicating by DOI then PMID.
///
/// DOIs and PMIDs are normalized before lookup and storage so the same work
/// fetched from different sources — which disagree on DOI case and prefixes —
/// resolves to a single row. `pub_` is mutated in place to hold the canonical
/// forms, as Python's is.
///
/// The whole store is one atomic transaction: row consolidation, insert/merge,
/// full-text sources, grants and affiliations.
///
/// # Errors
///
/// [`ReplaceError::MissingSource`] if a grant or affiliation names no source;
/// otherwise the driver's.
pub fn store_publication(
    db: &mut dyn Db,
    pub_: &mut Publication,
    fulltext_sources: &[FullTextSource],
    grants: &[Grant],
    affiliations: &[AuthorAffiliation],
) -> Result<StoreOutcome, ReplaceError> {
    let now = now_utc();
    pub_.doi = normalize_doi(pub_.doi.as_deref());
    pub_.pmid = normalize_pmid(pub_.pmid.as_deref());

    let mut tx = db.begin().map_err(ReplaceError::Db)?;

    // Look up by each identifier independently so a split identity (DOI and
    // PMID pointing at two different existing rows) can be detected.
    let row_by_doi = match &pub_.doi {
        Some(doi) => fetch_one(
            &mut *tx,
            "SELECT * FROM publications WHERE doi = ?",
            &[DbValue::Text(doi.clone())],
        )
        .map_err(ReplaceError::Db)?,
        None => None,
    };
    let row_by_pmid = match &pub_.pmid {
        Some(pmid) => fetch_one(
            &mut *tx,
            "SELECT * FROM publications WHERE pmid = ?",
            &[DbValue::Text(pmid.clone())],
        )
        .map_err(ReplaceError::Db)?,
        None => None,
    };

    let existing = match (&row_by_doi, &row_by_pmid) {
        (Some(by_doi), Some(by_pmid)) if by_doi.get_i64("id")? != by_pmid.get_i64("id")? => {
            // Split identity: consolidate, keeping the DOI row.
            consolidate_rows(&mut *tx, by_doi, by_pmid, &now).map_err(ReplaceError::Db)?;
            let keep_id = by_doi.get_i64("id")?;
            fetch_one(
                &mut *tx,
                "SELECT * FROM publications WHERE id = ?",
                &[DbValue::Int(keep_id)],
            )
            .map_err(ReplaceError::Db)?
        }
        (Some(by_doi), _) => Some(by_doi.clone()),
        (None, Some(by_pmid)) => Some(by_pmid.clone()),
        (None, None) => None,
    };

    let (pub_id, outcome) = match &existing {
        Some(row) => {
            merge_publication(&mut *tx, row, pub_, &now).map_err(ReplaceError::Db)?;
            (row.get_i64("id")?, StoreOutcome::Merged)
        }
        None => (
            insert_publication(&mut *tx, pub_, &now).map_err(ReplaceError::Db)?,
            StoreOutcome::Added,
        ),
    };

    for fts in fulltext_sources {
        add_fulltext_source(
            &mut *tx,
            pub_id,
            &fts.source,
            &fts.url,
            &fts.format,
            fts.version.as_deref(),
        )
        .map_err(ReplaceError::Db)?;
    }

    let grant_rows: Vec<(String, Grant)> = grants
        .iter()
        .map(|g| (g.source.clone(), g.clone()))
        .collect();
    replace_child_rows(
        &mut *tx,
        "publication_grants",
        pub_id,
        &["agency", "grant_id", "country"],
        &grant_rows,
        |g| {
            vec![
                g.agency.clone().map_or(DbValue::Null, DbValue::Text),
                g.grant_id.clone().map_or(DbValue::Null, DbValue::Text),
                g.country.clone().map_or(DbValue::Null, DbValue::Text),
            ]
        },
        &now,
    )?;

    let affiliation_rows: Vec<(String, AuthorAffiliation)> = affiliations
        .iter()
        .map(|a| (a.source.clone(), a.clone()))
        .collect();
    replace_child_rows(
        &mut *tx,
        "publication_affiliations",
        pub_id,
        &["author", "affiliation", "position"],
        &affiliation_rows,
        |a| {
            vec![
                DbValue::Text(a.author.clone()),
                DbValue::Text(a.affiliation.clone()),
                DbValue::Int(a.position),
            ]
        },
        &now,
    )?;

    tx.commit().map_err(ReplaceError::Db)?;
    Ok(outcome)
}

/// Every funding award stored for a publication, in insertion order.
///
/// # Errors
///
/// Propagates the driver's error.
pub fn get_grants(db: &mut dyn Db, publication_id: i64) -> Result<Vec<Grant>, DbError> {
    let rows = fetch_all(
        db,
        "SELECT id, publication_id, source, agency, grant_id, country FROM publication_grants \
         WHERE publication_id = ? ORDER BY id",
        &[DbValue::Int(publication_id)],
    )?;
    Ok(rows
        .iter()
        .map(|row| Grant {
            id: row.get("id").ok().and_then(DbValue::as_i64),
            publication_id: row
                .get("publication_id")
                .ok()
                .and_then(DbValue::as_i64)
                .unwrap_or(0),
            source: text(row, "source").unwrap_or_default(),
            agency: text(row, "agency"),
            grant_id: text(row, "grant_id"),
            country: text(row, "country"),
        })
        .collect())
}

/// Every author affiliation stored for a publication, ordered by position.
///
/// Ordered by author position so the first and senior authors are found at the
/// ends.
///
/// # Errors
///
/// Propagates the driver's error.
pub fn get_author_affiliations(
    db: &mut dyn Db,
    publication_id: i64,
) -> Result<Vec<AuthorAffiliation>, DbError> {
    let rows = fetch_all(
        db,
        "SELECT id, publication_id, source, author, affiliation, position \
         FROM publication_affiliations WHERE publication_id = ? ORDER BY position, id",
        &[DbValue::Int(publication_id)],
    )?;
    Ok(rows
        .iter()
        .map(|row| AuthorAffiliation {
            id: row.get("id").ok().and_then(DbValue::as_i64),
            publication_id: row
                .get("publication_id")
                .ok()
                .and_then(DbValue::as_i64)
                .unwrap_or(0),
            source: text(row, "source").unwrap_or_default(),
            author: text(row, "author").unwrap_or_default(),
            affiliation: text(row, "affiliation").unwrap_or_default(),
            position: row
                .get("position")
                .ok()
                .and_then(DbValue::as_i64)
                .unwrap_or(0),
        })
        .collect())
}

/// Look up a publication by DOI, or `None`.
///
/// The DOI is normalized before lookup so a query using any case or prefix
/// variant matches the canonical stored form.
///
/// # Errors
///
/// Propagates the driver's error.
pub fn get_publication_by_doi(db: &mut dyn Db, doi: &str) -> Result<Option<Publication>, DbError> {
    let row = fetch_one(
        db,
        "SELECT * FROM publications WHERE doi = ?",
        &[normalize_doi(Some(doi)).map_or(DbValue::Null, DbValue::Text)],
    )?;
    row.as_ref().map(row_to_publication).transpose()
}

/// Look up a publication by PMID, or `None`.
///
/// # Errors
///
/// Propagates the driver's error.
pub fn get_publication_by_pmid(
    db: &mut dyn Db,
    pmid: &str,
) -> Result<Option<Publication>, DbError> {
    let row = fetch_one(
        db,
        "SELECT * FROM publications WHERE pmid = ?",
        &[normalize_pmid(Some(pmid)).map_or(DbValue::Null, DbValue::Text)],
    )?;
    row.as_ref().map(row_to_publication).transpose()
}

/// Add a full-text source for a publication.
///
/// Returns `true` if the record was inserted, `false` if the
/// `(publication_id, url)` pair already exists.
///
/// # Errors
///
/// Propagates the driver's error.
pub fn add_fulltext_source(
    db: &mut dyn Db,
    publication_id: i64,
    source: &str,
    url: &str,
    format: &str,
    version: Option<&str>,
) -> Result<bool, DbError> {
    let now = now_utc();
    let params = [
        DbValue::Int(publication_id),
        DbValue::Text(source.to_string()),
        DbValue::Text(url.to_string()),
        DbValue::Text(format.to_string()),
        version.map_or(DbValue::Null, |v| DbValue::Text(v.to_string())),
        DbValue::Text(now),
    ];
    let mut tx = db.begin()?;
    let affected = execute(
        &mut *tx,
        // The spaces are load-bearing; see the note on the `DELETE` above.
        concat!(
            "INSERT INTO fulltext_sources",
            " (publication_id, source, url, format, version, created_at)",
            " VALUES (?, ?, ?, ?, ?, ?)",
            " ON CONFLICT (publication_id, url) DO NOTHING"
        ),
        &params,
    )?;
    tx.commit()?;
    Ok(affected > 0)
}
