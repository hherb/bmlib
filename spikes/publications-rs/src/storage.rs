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

//! The store path: dedup by DOI then PMID, merge, and the child-row rules.
//!
//! A slice of `publications/storage.py`, chosen for invariant density rather
//! than size. Everything here is pinned by a test ported from
//! `tests/test_backends.py`, and the comments record which `DECISIONS.md` rule
//! each piece is carrying.

use std::collections::BTreeMap;

use bmlib_db::operations::{execute, executemany, fetch_all, fetch_one};
use bmlib_db::{placeholders, transaction_with, Db, Dialect, Row, Value};

use crate::error::{PublicationError, Result};
use crate::identifiers::{normalize_doi, normalize_pmid};
use crate::models::{AuthorAffiliation, Grant, Publication};

/// Whether a store inserted a new record or merged into an existing one.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Stored {
    /// No existing record matched; a row was inserted.
    Added,
    /// An existing record matched and was updated.
    Merged,
}

fn now_iso() -> String {
    chrono::Utc::now().to_rfc3339()
}

fn text(row: &Row, name: &str) -> Result<Option<String>> {
    Ok(row.get(name)?.as_str().map(String::from))
}

/// Read a column that may be absent, returning `None` if it is.
///
/// Columns added after a release only reach an existing database via a schema
/// upgrade. Reads must not fall over on a database whose owner has upgraded
/// the library but not re-run it — the Python port needs the same helper,
/// catching `IndexError` on `sqlite3.Row` and `KeyError` on a dict.
fn optional_column(row: &Row, name: &str) -> Option<String> {
    row.get(name)
        .ok()
        .and_then(|v| v.as_str())
        .map(String::from)
}

fn required_text(row: &Row, name: &str) -> Result<String> {
    row.get(name)?
        .as_str()
        .map(String::from)
        .ok_or_else(|| PublicationError::Malformed(format!("{name} is not text")))
}

fn json_list(row: &Row, name: &str) -> Result<Vec<String>> {
    match row.get(name)?.as_str() {
        None | Some("") => Ok(Vec::new()),
        Some(s) => Ok(serde_json::from_str(s)?),
    }
}

fn row_to_publication(row: &Row) -> Result<Publication> {
    Ok(Publication {
        id: Some(row.get_i64("id")?),
        doi: text(row, "doi")?,
        pmid: text(row, "pmid")?,
        pmcid: optional_column(row, "pmcid"),
        title: required_text(row, "title")?,
        r#abstract: text(row, "abstract")?,
        authors: json_list(row, "authors")?,
        journal: text(row, "journal")?,
        publication_date: text(row, "publication_date")?,
        publication_types: json_list(row, "publication_types")?,
        keywords: json_list(row, "keywords")?,
        is_open_access: row.get_i64("is_open_access").unwrap_or(0) != 0,
        license: text(row, "license")?,
        sources: json_list(row, "sources")?,
        first_seen_source: required_text(row, "first_seen_source")?,
        created_at: text(row, "created_at")?,
        updated_at: text(row, "updated_at")?,
    })
}

/// Insert a new publication and return its row id.
///
/// One list of `(column, value)` pairs, so the column list and the values
/// cannot drift apart — the Python port makes the same choice with a dict, for
/// the same reason.
///
/// This is the one irreducibly dialect-specific place: SQLite reports the new
/// id on the connection, PostgreSQL has no `lastrowid` and must be asked with
/// `RETURNING`. **Only the SQLite arm is exercised** — see FINDINGS.md.
fn insert_publication(db: &mut dyn Db, pub_: &Publication, now: &str) -> Result<i64> {
    let values: Vec<(&str, Value)> = vec![
        ("doi", Value::from(pub_.doi.as_deref())),
        ("pmid", Value::from(pub_.pmid.as_deref())),
        ("pmcid", Value::from(pub_.pmcid.as_deref())),
        ("title", Value::from(pub_.title.as_str())),
        ("abstract", Value::from(pub_.r#abstract.as_deref())),
        (
            "authors",
            Value::from(serde_json::to_string(&pub_.authors)?),
        ),
        ("journal", Value::from(pub_.journal.as_deref())),
        (
            "publication_date",
            Value::from(pub_.publication_date.as_deref()),
        ),
        (
            "publication_types",
            Value::from(serde_json::to_string(&pub_.publication_types)?),
        ),
        (
            "keywords",
            Value::from(serde_json::to_string(&pub_.keywords)?),
        ),
        ("is_open_access", Value::from(pub_.is_open_access)),
        ("license", Value::from(pub_.license.as_deref())),
        (
            "sources",
            Value::from(serde_json::to_string(&pub_.sources)?),
        ),
        (
            "first_seen_source",
            Value::from(pub_.first_seen_source.as_str()),
        ),
        ("created_at", Value::from(now)),
        ("updated_at", Value::from(now)),
    ];
    let columns: Vec<&str> = values.iter().map(|(c, _)| *c).collect();
    let args: Vec<Value> = values.iter().map(|(_, v)| v.clone()).collect();
    let sql = format!(
        "INSERT INTO publications ({}) VALUES ({})",
        columns.join(", "),
        placeholders(columns.len())
    );

    match db.dialect() {
        Dialect::Sqlite => {
            execute(db, &sql, &args)?;
            db.last_insert_rowid()
                .ok_or_else(|| PublicationError::Malformed("no rowid after insert".into()))
        }
        Dialect::Postgres => {
            let rows = fetch_all(db, &format!("{sql} RETURNING id"), &args)?;
            rows.first()
                .ok_or_else(|| PublicationError::Malformed("RETURNING gave no row".into()))?
                .get_i64("id")
                .map_err(PublicationError::from)
        }
    }
}

/// Merge an incoming publication into an existing row.
///
/// Fills NULLs, never overwrites a non-NULL field, appends unseen sources, and
/// latches open access on. The latch is written as `OR` rather than a `CASE` on
/// `= 0` because PostgreSQL stores that column as a real BOOLEAN, which does
/// not compare against an integer.
fn merge_publication(
    db: &mut dyn Db,
    existing: &Row,
    incoming: &Publication,
    now: &str,
) -> Result<()> {
    let mut sources = json_list(existing, "sources")?;
    for s in &incoming.sources {
        if !sources.contains(s) {
            sources.push(s.clone());
        }
    }

    // Keep the existing list when it has anything in it, else take incoming.
    let keep_or_take = |existing_json: Option<&str>, incoming: &Vec<String>| -> Result<String> {
        match existing_json {
            Some(s) if !s.is_empty() && s != "[]" => Ok(s.to_string()),
            _ => Ok(serde_json::to_string(incoming)?),
        }
    };
    let authors = keep_or_take(existing.get("authors")?.as_str(), &incoming.authors)?;
    let pub_types = keep_or_take(
        existing.get("publication_types")?.as_str(),
        &incoming.publication_types,
    )?;
    let keywords = keep_or_take(existing.get("keywords")?.as_str(), &incoming.keywords)?;

    execute(
        db,
        "UPDATE publications SET \
           doi = COALESCE(doi, ?), \
           pmid = COALESCE(pmid, ?), \
           pmcid = COALESCE(pmcid, ?), \
           abstract = COALESCE(abstract, ?), \
           authors = ?, \
           journal = COALESCE(journal, ?), \
           publication_date = COALESCE(publication_date, ?), \
           publication_types = ?, \
           keywords = ?, \
           is_open_access = (is_open_access OR ?), \
           license = COALESCE(license, ?), \
           sources = ?, \
           updated_at = ? \
         WHERE id = ?",
        &[
            Value::from(incoming.doi.as_deref()),
            Value::from(incoming.pmid.as_deref()),
            Value::from(incoming.pmcid.as_deref()),
            Value::from(incoming.r#abstract.as_deref()),
            Value::from(authors),
            Value::from(incoming.journal.as_deref()),
            Value::from(incoming.publication_date.as_deref()),
            Value::from(pub_types),
            Value::from(keywords),
            Value::from(incoming.is_open_access),
            Value::from(incoming.license.as_deref()),
            Value::from(serde_json::to_string(&sources)?),
            Value::from(now),
            Value::Int(existing.get_i64("id")?),
        ],
    )?;
    Ok(())
}

/// Replace rows in `table` for each source present in `rows`.
///
/// Each element is `(source, values...)`. Rows are grouped by source, and each
/// group replaces **only that source's** existing rows. Scoping by publication
/// alone made PubMed's grants replace OpenAlex's and then OpenAlex's replace
/// PubMed's, so the stored answer depended on whichever source synced last,
/// with no error and no warning.
///
/// Does nothing when `rows` is empty: there is no source to scope a delete to,
/// and an absent `<GrantList>` means the record did not carry the data, not
/// that the funding was withdrawn.
///
/// # Errors
///
/// [`PublicationError::UnnamedSource`] if any row names no source. An unnamed
/// row is not merely unlabelled but unreachable — nothing can ever name it, so
/// no later sync can replace it and each one stacks a duplicate beside it.
///
/// Rust narrows the Python guard rather than removing it: `Grant.source` is a
/// `String`, not `Option<String>`, so `None` is already unrepresentable. What
/// survives is the empty string — which is exactly the value the Python
/// dataclass default produced and the `NOT NULL` column accepted happily.
pub fn replace_child_rows(
    db: &mut dyn Db,
    table: &'static str,
    publication_id: i64,
    columns: &[&str],
    rows: &[(String, Vec<Value>)],
    now: &str,
) -> Result<()> {
    if rows.is_empty() {
        return Ok(());
    }

    // BTreeMap rather than HashMap so the insert order is deterministic across
    // runs — a test asserting on stored order should not depend on hashing.
    let mut by_source: BTreeMap<&str, Vec<&Vec<Value>>> = BTreeMap::new();
    for (source, values) in rows {
        if source.is_empty() {
            return Err(PublicationError::UnnamedSource { table });
        }
        by_source.entry(source).or_default().push(values);
    }

    let mut named = vec!["publication_id", "source"];
    named.extend_from_slice(columns);
    named.push("created_at");
    let sql = format!(
        "INSERT INTO {table} ({}) VALUES ({})",
        named.join(", "),
        placeholders(named.len())
    );

    for (source, group) in by_source {
        execute(
            db,
            &format!("DELETE FROM {table} WHERE publication_id = ? AND source = ?"),
            &[Value::Int(publication_id), Value::from(source)],
        )?;
        // One round trip for the group rather than one per row: a PubMed day
        // is thousands of records each carrying an affiliation per author.
        let batch: Vec<Vec<Value>> = group
            .into_iter()
            .map(|values| {
                let mut row = vec![Value::Int(publication_id), Value::from(source)];
                row.extend(values.iter().cloned());
                row.push(Value::from(now));
                row
            })
            .collect();
        executemany(db, &sql, &batch)?;
    }
    Ok(())
}

/// Move `drop_id`'s rows in `table` onto `keep_id`, per source.
///
/// Called before the drop row is deleted. Both backends enforce foreign keys,
/// so one row still pointing at the doomed publication makes the `DELETE` raise
/// and aborts the whole store.
///
/// A source the keep row already has wins; a source only the drop row saw moves
/// across. Merging two rows' accounts of what PubMed said would yield a set
/// PubMed never asserted.
///
/// Returns immediately when the ids are equal. The caller only reaches here
/// having established they differ, but the whole method rests on it: the
/// DELETE's subquery reads the keep row's sources while the DELETE removes the
/// drop row's, and those sets are disjoint *only* because the ids are.
fn relocate_child_rows(db: &mut dyn Db, table: &str, keep_id: i64, drop_id: i64) -> Result<()> {
    if keep_id == drop_id {
        return Ok(());
    }
    execute(
        db,
        &format!(
            "DELETE FROM {table} WHERE publication_id = ? \
             AND source IN (SELECT source FROM {table} WHERE publication_id = ?)"
        ),
        &[Value::Int(drop_id), Value::Int(keep_id)],
    )?;
    execute(
        db,
        &format!("UPDATE {table} SET publication_id = ? WHERE publication_id = ?"),
        &[Value::Int(keep_id), Value::Int(drop_id)],
    )?;
    Ok(())
}

/// Merge the `drop` row into the `keep` row, then delete `drop`.
///
/// Used when an incoming record carries a DOI and a PMID pointing at two
/// different existing rows — a split identity, which arises when a work is
/// indexed by one identifier before its cross-reference to the other exists.
///
/// Ordering matters: the drop row is deleted *before* its identifier is merged
/// onto the keep row, so the unique index is free when the merge runs.
fn consolidate_rows(db: &mut dyn Db, keep: &Row, drop: &Row, now: &str) -> Result<()> {
    let keep_id = keep.get_i64("id")?;
    let drop_id = drop.get_i64("id")?;

    // Move the drop row's full-text sources across, skipping any URL the keep
    // row already has — moving those would violate UNIQUE(publication_id, url).
    execute(
        db,
        "UPDATE fulltext_sources SET publication_id = ? WHERE publication_id = ? \
         AND url NOT IN (SELECT url FROM fulltext_sources WHERE publication_id = ?)",
        &[
            Value::Int(keep_id),
            Value::Int(drop_id),
            Value::Int(keep_id),
        ],
    )?;
    execute(
        db,
        "DELETE FROM fulltext_sources WHERE publication_id = ?",
        &[Value::Int(drop_id)],
    )?;

    relocate_child_rows(db, "publication_grants", keep_id, drop_id)?;
    relocate_child_rows(db, "publication_affiliations", keep_id, drop_id)?;

    let drop_pub = row_to_publication(drop)?;
    execute(
        db,
        "DELETE FROM publications WHERE id = ?",
        &[Value::Int(drop_id)],
    )?;
    merge_publication(db, keep, &drop_pub, now)
}

/// Record a full-text location, ignoring one already stored for that URL.
pub fn add_fulltext_source(
    db: &mut dyn Db,
    publication_id: i64,
    source: &str,
    url: &str,
    now: &str,
) -> Result<bool> {
    let existing = fetch_one(
        db,
        "SELECT id FROM fulltext_sources WHERE publication_id = ? AND url = ?",
        &[Value::Int(publication_id), Value::from(url)],
    )?;
    if existing.is_some() {
        return Ok(false);
    }
    execute(
        db,
        "INSERT INTO fulltext_sources (publication_id, source, url, created_at) \
         VALUES (?, ?, ?, ?)",
        &[
            Value::Int(publication_id),
            Value::from(source),
            Value::from(url),
            Value::from(now),
        ],
    )?;
    Ok(true)
}

/// Store a publication, de-duplicating by DOI then PMID.
///
/// Identifiers are normalised before lookup and storage, so the same work from
/// different sources resolves to one row. The whole store — consolidation,
/// insert or merge, full-text sources, grants and affiliations — is one atomic
/// transaction, and joins a caller's block if one is already open.
///
/// `grants` and `affiliations`: supplying any **replaces** the stored rows for
/// each source those rows name, leaving every other source's alone. Supplying
/// none leaves everything untouched.
pub fn store_publication(
    db: &mut dyn Db,
    pub_: &mut Publication,
    fulltext_urls: &[(String, String)],
    grants: &[Grant],
    affiliations: &[AuthorAffiliation],
) -> Result<Stored> {
    let now = now_iso();
    pub_.doi = normalize_doi(pub_.doi.as_deref());
    pub_.pmid = normalize_pmid(pub_.pmid.as_deref());

    transaction_with(db, |tx| {
        let row_by_doi = match &pub_.doi {
            Some(doi) => fetch_one(
                tx,
                "SELECT * FROM publications WHERE doi = ?",
                &[Value::from(doi.as_str())],
            )?,
            None => None,
        };
        let row_by_pmid = match &pub_.pmid {
            Some(pmid) => fetch_one(
                tx,
                "SELECT * FROM publications WHERE pmid = ?",
                &[Value::from(pmid.as_str())],
            )?,
            None => None,
        };

        let existing = match (&row_by_doi, &row_by_pmid) {
            (Some(d), Some(p)) if d.get_i64("id")? != p.get_i64("id")? => {
                consolidate_rows(tx, d, p, &now)?;
                fetch_one(
                    tx,
                    "SELECT * FROM publications WHERE id = ?",
                    &[Value::Int(d.get_i64("id")?)],
                )?
            }
            _ => row_by_doi.clone().or_else(|| row_by_pmid.clone()),
        };

        let (pub_id, outcome) = match &existing {
            Some(row) => {
                merge_publication(tx, row, pub_, &now)?;
                (row.get_i64("id")?, Stored::Merged)
            }
            None => (insert_publication(tx, pub_, &now)?, Stored::Added),
        };

        for (source, url) in fulltext_urls {
            add_fulltext_source(tx, pub_id, source, url, &now)?;
        }

        let grant_rows: Vec<(String, Vec<Value>)> = grants
            .iter()
            .map(|g| {
                (
                    g.source.clone(),
                    vec![
                        Value::from(g.agency.as_deref()),
                        Value::from(g.grant_id.as_deref()),
                        Value::from(g.country.as_deref()),
                    ],
                )
            })
            .collect();
        replace_child_rows(
            tx,
            "publication_grants",
            pub_id,
            &["agency", "grant_id", "country"],
            &grant_rows,
            &now,
        )?;

        let affiliation_rows: Vec<(String, Vec<Value>)> = affiliations
            .iter()
            .map(|a| {
                (
                    a.source.clone(),
                    vec![
                        Value::from(a.author.as_str()),
                        Value::from(a.affiliation.as_str()),
                        Value::Int(a.position),
                    ],
                )
            })
            .collect();
        replace_child_rows(
            tx,
            "publication_affiliations",
            pub_id,
            &["author", "affiliation", "position"],
            &affiliation_rows,
            &now,
        )?;

        Ok(outcome)
    })
}

/// Look up a publication by DOI, normalising it first.
pub fn get_publication_by_doi(db: &mut dyn Db, doi: &str) -> Result<Option<Publication>> {
    let Some(canonical) = normalize_doi(Some(doi)) else {
        return Ok(None);
    };
    match fetch_one(
        db,
        "SELECT * FROM publications WHERE doi = ?",
        &[Value::from(canonical)],
    )? {
        Some(row) => Ok(Some(row_to_publication(&row)?)),
        None => Ok(None),
    }
}

/// Look up a publication by PMID.
pub fn get_publication_by_pmid(db: &mut dyn Db, pmid: &str) -> Result<Option<Publication>> {
    let Some(canonical) = normalize_pmid(Some(pmid)) else {
        return Ok(None);
    };
    match fetch_one(
        db,
        "SELECT * FROM publications WHERE pmid = ?",
        &[Value::from(canonical)],
    )? {
        Some(row) => Ok(Some(row_to_publication(&row)?)),
        None => Ok(None),
    }
}

/// Funding awards on record for a publication, in insertion order.
pub fn get_grants(db: &mut dyn Db, publication_id: i64) -> Result<Vec<Grant>> {
    let rows = fetch_all(
        db,
        "SELECT id, publication_id, source, agency, grant_id, country FROM publication_grants \
         WHERE publication_id = ? ORDER BY id",
        &[Value::Int(publication_id)],
    )?;
    rows.iter()
        .map(|row| {
            Ok(Grant {
                id: Some(row.get_i64("id")?),
                publication_id: Some(row.get_i64("publication_id")?),
                source: required_text(row, "source")?,
                agency: text(row, "agency")?,
                grant_id: text(row, "grant_id")?,
                country: text(row, "country")?,
            })
        })
        .collect()
}

/// Author affiliations on record for a publication, in insertion order.
pub fn get_author_affiliations(
    db: &mut dyn Db,
    publication_id: i64,
) -> Result<Vec<AuthorAffiliation>> {
    let rows = fetch_all(
        db,
        "SELECT id, publication_id, source, author, affiliation, position \
         FROM publication_affiliations WHERE publication_id = ? ORDER BY id",
        &[Value::Int(publication_id)],
    )?;
    rows.iter()
        .map(|row| {
            Ok(AuthorAffiliation {
                id: Some(row.get_i64("id")?),
                publication_id: Some(row.get_i64("publication_id")?),
                source: required_text(row, "source")?,
                author: required_text(row, "author")?,
                affiliation: required_text(row, "affiliation")?,
                position: row.get_i64("position")?,
            })
        })
        .collect()
}
