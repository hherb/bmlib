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

//! Retraction Watch: parsing the export, and deciding what a notice means.
//!
//! A port of `bmlib/publications/retractions.py`. The module is deliberately
//! two halves — a pure rule ([`is_retracted`]) and the import that feeds it —
//! because the rule is the part that must be re-derivable without re-importing
//! 71,306 rows.
//!
//! # The one rule worth reading before the code
//!
//! [`is_retracted`] scans newest first and the **first Retraction or
//! Reinstatement decides**; a Correction or an Expression of Concern is not
//! evidence either way. That is not "latest notice wins": a paper retracted in
//! 2011 and corrected in 2017 is still retracted, and 52 papers in the live
//! export have exactly that shape. A flat last-wins rule answers "not
//! retracted" for all 52.
//!
//! # Sentinels
//!
//! [`ABSENT_IDENTIFIER_VALUES`] holds the two values the export writes to mean
//! "there is no identifier here". Neither is falsy, so a plain truthiness test
//! accepts both: over the 2026-08-03 export `"0"` appears in **46.04%** of
//! PubMed ID cells and `"unavailable"` (two casings) in **4.80%** of DOI cells.
//! Storing them collapses tens of thousands of unrelated notices onto one key.
//! The same set guards the *lookup* path, not just parsing.

use crate::publications::csv::{CsvError, Reader};
use crate::publications::models::{RetractionNature, RetractionNotice};
use crate::publications::storage::{normalize_doi, normalize_pmid};

// ---------------------------------------------------------------------------
// Column resolution
// ---------------------------------------------------------------------------

/// A Retraction Watch row describes two papers, and the export carries a
/// column pair for each. These lists are ordered most-specific-first and
/// deliberately exclude a bare `"DOI"` / `"PMID"`: such a column could mean
/// either paper, and guessing is what let upstream's resolution return the
/// notice's identifier for the retracted paper.
pub const RECORD_ID_COLUMNS: [&str; 3] = ["Record ID", "RecordID", "Record Id"];
/// Columns holding the **retracted paper's** DOI.
pub const DOI_COLUMNS: [&str; 2] = ["OriginalPaperDOI", "Original Paper DOI"];
/// Columns holding the **retracted paper's** PMID.
pub const PMID_COLUMNS: [&str; 3] = [
    "OriginalPaperPubMedID",
    "Original Paper PubMedID",
    "OriginalPaperPMID",
];
/// Columns holding the retraction notice's DOI.
pub const NOTICE_DOI_COLUMNS: [&str; 2] = ["RetractionDOI", "Retraction DOI"];
/// Columns holding the retraction notice's PMID.
pub const NOTICE_PMID_COLUMNS: [&str; 2] = ["RetractionPubMedID", "Retraction PubMedID"];
/// Columns holding the notice's nature.
pub const NATURE_COLUMNS: [&str; 2] = ["RetractionNature", "Nature"];
/// Columns holding the reasons.
pub const REASON_COLUMNS: [&str; 3] = ["Reason", "Reasons", "Reason(s)"];
/// Columns holding the retracted paper's title.
pub const TITLE_COLUMNS: [&str; 2] = ["Title", "OriginalPaperTitle"];
/// Columns holding the journal.
pub const JOURNAL_COLUMNS: [&str; 1] = ["Journal"];
/// Columns holding the retraction date.
pub const RETRACTION_DATE_COLUMNS: [&str; 2] = ["RetractionDate", "Retraction Date"];
/// Columns holding the original paper's date.
pub const ORIGINAL_DATE_COLUMNS: [&str; 2] = ["OriginalPaperDate", "Original Paper Date"];

/// Values the export writes to mean "there is no identifier here".
///
/// Only these two are listed — each was measured; adding an unmeasured guess is
/// how the list stops being answerable to the data.
pub const ABSENT_IDENTIFIER_VALUES: [&str; 2] = ["0", "unavailable"];

/// The US-first form the export actually uses, then its day-first twin, then
/// the two ISO-ish shapes.
///
/// The `%m/%d/%Y` / `%d/%m/%Y` ambiguity is real and is **not** resolved: for
/// any day ≤ 12 both parse and disagree, and nothing in the row says which was
/// meant. US-first is kept because Retraction Watch is a US publication — and
/// it must stay **immediately ahead** of the day-first form, since that
/// relative order *is* the ambiguity resolution rather than an optimisation.
/// The two ISO shapes cannot be confused with either slash form, so their
/// position costs nothing.
pub const DATE_FORMATS: [&str; 4] = ["%m/%d/%Y", "%d/%m/%Y", "%Y-%m-%d", "%Y/%m/%d"];

/// The stripped value of the first candidate column that has one.
#[must_use]
pub fn find_column<'a>(row: &'a [(String, String)], candidates: &[&str]) -> Option<&'a str> {
    for name in candidates {
        if let Some((_, value)) = row.iter().find(|(k, _)| k == name) {
            if !value.trim().is_empty() {
                return Some(value.trim());
            }
        }
    }
    None
}

/// A usable identifier, or `None` for a blank or sentinel value.
///
/// A truthiness test is not enough: see [`ABSENT_IDENTIFIER_VALUES`].
#[must_use]
pub fn clean_identifier(value: Option<&str>) -> Option<String> {
    let text = value?.trim();
    if text.is_empty() || ABSENT_IDENTIFIER_VALUES.contains(&text.to_lowercase().as_str()) {
        return None;
    }
    Some(text.to_string())
}

/// Split a `Reason` cell into individual reasons.
///
/// Reasons are semicolon-separated, and every populated row of the export ends
/// with a trailing `;` — so empties are dropped rather than yielding a blank
/// final reason. A single leading `+` is stripped: that prefix belongs to
/// Retraction Watch's own export rather than Crossref's, and costs nothing to
/// accommodate.
#[must_use]
pub fn split_reasons(value: Option<&str>) -> Vec<String> {
    let Some(value) = value.filter(|v| !v.is_empty()) else {
        return Vec::new();
    };
    let mut reasons = Vec::new();
    for part in value.split(';') {
        let item = part.trim();
        let item = item.strip_prefix('+').map_or(item, str::trim);
        if !item.is_empty() {
            reasons.push(item.to_string());
        }
    }
    reasons
}

/// Parse an export date into an ISO `yyyy-mm-dd` string, or `None`.
///
/// The export writes `M/D/YYYY H:MM` (a time component is present on every
/// dated row), so a trailing time is tolerated. An unparseable value returns
/// `None` rather than failing the row — a missing date is worth less than a
/// lost retraction.
///
/// The date-only candidate is tried **before** the full text: every real dated
/// row has a time component and none of [`DATE_FORMATS`] matches one, so trying
/// the full text first burns one wasted parse per format on every single row.
#[must_use]
pub fn parse_date(value: Option<&str>) -> Option<String> {
    let text = value?.trim();
    if text.is_empty() {
        return None;
    }
    let candidates: Vec<&str> = if text.contains(' ') {
        vec![text.split(' ').next().unwrap_or(text), text]
    } else {
        vec![text]
    };
    for candidate in candidates {
        for format in DATE_FORMATS {
            if let Some(iso) = parse_with_format(candidate, format) {
                return Some(iso);
            }
        }
    }
    None
}

/// Parse one date with one explicit format, returning `yyyy-mm-dd`.
///
/// Hand-rolled rather than pulling a date library for four formats: the shapes
/// are fixed, the output is textual, and the only ordering that matters is the
/// caller's.
fn parse_with_format(text: &str, format: &str) -> Option<String> {
    let (month, day, year) = match format {
        "%m/%d/%Y" => {
            let (a, b, c) = split_three(text, '/')?;
            (a, b, c)
        }
        "%d/%m/%Y" => {
            let (a, b, c) = split_three(text, '/')?;
            (b, a, c)
        }
        "%Y-%m-%d" => {
            let (y, m, d) = split_three(text, '-')?;
            return iso(y, m, d);
        }
        "%Y/%m/%d" => {
            let (y, m, d) = split_three(text, '/')?;
            return iso(y, m, d);
        }
        _ => return None,
    };
    iso(year, month, day)
}

fn split_three(text: &str, sep: char) -> Option<(u32, u32, u32)> {
    let mut parts = text.split(sep);
    let a: u32 = parts.next()?.parse().ok()?;
    let b: u32 = parts.next()?.parse().ok()?;
    let c: u32 = parts.next()?.parse().ok()?;
    if parts.next().is_some() {
        return None;
    }
    Some((a, b, c))
}

/// `strptime` accepts a 1- or 2-digit month and day, and a 4-digit year, then
/// validates the calendar — so `"2/30/2024"` is refused rather than rolled
/// forward into March.
fn iso(year: u32, month: u32, day: u32) -> Option<String> {
    if !(1..=12).contains(&month) || day < 1 || day > days_in_month(year, month) {
        return None;
    }
    // A two-digit year is not one of the formats, and `%Y` requires four.
    if !(1000..=9999).contains(&year) {
        return None;
    }
    Some(format!("{year:04}-{month:02}-{day:02}"))
}

fn days_in_month(year: u32, month: u32) -> u32 {
    let leap = (year % 4 == 0 && year % 100 != 0) || year % 400 == 0;
    match month {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        2 if leap => 29,
        2 => 28,
        _ => 0,
    }
}

// ---------------------------------------------------------------------------
// Row → notice
// ---------------------------------------------------------------------------

/// Why a row was skipped.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SkipReason {
    /// The row carried no `Record ID`.
    NoRecordId,
    /// The row carried no usable identifier for the retracted paper.
    NoIdentifier,
    /// The row could not be parsed as a CSV record.
    Malformed,
}

impl SkipReason {
    /// The message Python hands `on_skip`, verbatim.
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            SkipReason::NoRecordId => "no Record ID",
            SkipReason::NoIdentifier => "no usable DOI or PMID for the retracted paper",
            SkipReason::Malformed => "malformed CSV record",
        }
    }
}

/// A row that was skipped, with the physical line it ended on.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Skipped {
    /// The physical line the row ended on.
    pub line: usize,
    /// Why it was skipped.
    pub reason: SkipReason,
}

/// A nature string this version could not map, reported once per distinct
/// value.
///
/// Mapping an unknown nature to `Other` rather than raising is deliberate, but
/// silence is not: [`is_retracted`] treats `Other` as evidence of nothing, so
/// if Retraction Watch ever rewords `"Retraction"`, an import would succeed,
/// store all 66,062 of them as `Other`, and answer "not retracted" for every
/// paper in the file. That is this feature's worst failure and it must not be
/// silent. Reported once per distinct value rather than once per row: the
/// vocabulary is small, so this is bounded at a handful of lines even when
/// every row is affected.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct UnknownNature {
    /// The raw string from the export.
    pub raw: String,
}

/// Build a notice from one CSV row, or `None` if the row is unusable.
///
/// `unknown_natures` collects distinct unrecognised nature strings so the
/// caller can report each once.
pub fn row_to_notice(
    row: &[(String, String)],
    unknown_natures: &mut Vec<UnknownNature>,
) -> Result<RetractionNotice, SkipReason> {
    let Some(record_id) = find_column(row, &RECORD_ID_COLUMNS) else {
        return Err(SkipReason::NoRecordId);
    };

    let doi = clean_identifier(find_column(row, &DOI_COLUMNS));
    let pmid = clean_identifier(find_column(row, &PMID_COLUMNS));
    if doi.is_none() && pmid.is_none() {
        return Err(SkipReason::NoIdentifier);
    }

    let raw_nature = find_column(row, &NATURE_COLUMNS);
    let nature = RetractionNature::from_raw(raw_nature);
    if nature == RetractionNature::Other {
        if let Some(raw) = raw_nature {
            let key = raw.trim().to_lowercase();
            if !unknown_natures
                .iter()
                .any(|u| u.raw.trim().to_lowercase() == key)
            {
                unknown_natures.push(UnknownNature {
                    raw: raw.to_string(),
                });
            }
        }
    }

    let mut notice = RetractionNotice::new(record_id, nature);
    notice.doi = doi;
    notice.pmid = pmid;
    notice.notice_doi = clean_identifier(find_column(row, &NOTICE_DOI_COLUMNS));
    notice.notice_pmid = clean_identifier(find_column(row, &NOTICE_PMID_COLUMNS));
    notice.title = find_column(row, &TITLE_COLUMNS).map(str::to_string);
    notice.journal = find_column(row, &JOURNAL_COLUMNS).map(str::to_string);
    notice.retraction_date = parse_date(find_column(row, &RETRACTION_DATE_COLUMNS));
    notice.original_paper_date = parse_date(find_column(row, &ORIGINAL_DATE_COLUMNS));
    notice.reasons = split_reasons(find_column(row, &REASON_COLUMNS));
    notice.raw_nature = raw_nature.map(str::to_string);
    Ok(notice)
}

/// What one parse produced.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct ParseOutcome {
    /// The notices that were usable, in file order.
    pub notices: Vec<RetractionNotice>,
    /// The rows that were skipped, in file order.
    pub skipped: Vec<Skipped>,
    /// Distinct unrecognised nature strings, in first-seen order.
    pub unknown_natures: Vec<UnknownNature>,
}

/// Parse a Retraction Watch CSV **already decoded to text**.
///
/// The encoding scan is the caller's: it needs random access to the raw file,
/// and this function's job is the record grammar and the row rules.
///
/// # Errors
///
/// [`CsvError`] when the document is malformed or a record is wider than its
/// header.
pub fn parse_retraction_watch_csv(bytes: &[u8]) -> Result<ParseOutcome, CsvError> {
    let mut reader = Reader::from_bytes(bytes)?;
    let mut outcome = ParseOutcome::default();
    while let Some(record) = reader.next_record() {
        let (fields, line) = record?;
        match row_to_notice(&fields, &mut outcome.unknown_natures) {
            Ok(notice) => outcome.notices.push(notice),
            Err(reason) => outcome.skipped.push(Skipped { line, reason }),
        }
    }
    Ok(outcome)
}

// ---------------------------------------------------------------------------
// The retraction rule
// ---------------------------------------------------------------------------

/// Order notices newest first, with undated ones last.
///
/// `""` sorts below any ISO date, so a notice with no date never displaces a
/// dated one. The sort is **stable**, so the order a lookup returned is
/// preserved within a tie.
#[must_use]
pub fn newest_first(notices: &[RetractionNotice]) -> Vec<RetractionNotice> {
    let mut sorted = notices.to_vec();
    // `sort_by` is stable, so equal dates keep their input order.
    sorted.sort_by(|a, b| {
        let (a_date, b_date) = (
            a.retraction_date.as_deref().unwrap_or(""),
            b.retraction_date.as_deref().unwrap_or(""),
        );
        b_date.cmp(a_date)
    });
    sorted
}

/// Decide whether a paper is currently retracted, from all its notices.
///
/// Scans newest first; the first [`RetractionNature::Retraction`] or
/// [`RetractionNature::Reinstatement`] decides. A Correction or an Expression
/// of Concern is **not** evidence either way, which is what makes this
/// different from a flat "latest notice wins": a paper retracted in 2011 and
/// corrected in 2017 is still retracted, and 52 papers in the live export have
/// exactly that shape.
///
/// Pure by design — it takes the notices, not a connection — so the rule is
/// testable without a database and re-derivable without re-importing 71,306
/// rows if it ever changes.
#[must_use]
pub fn is_retracted(notices: &[RetractionNotice]) -> bool {
    for notice in newest_first(notices) {
        if notice.nature == RetractionNature::Retraction {
            return true;
        }
        if notice.nature == RetractionNature::Reinstatement {
            return false;
        }
    }
    false
}

/// Whether a count/identifier pair is usable for a lookup.
///
/// The same sentinel screen the parse path applies, exposed because a caller
/// holding a PMID column that stores `"0"` for "absent" — the shape 46.04% of
/// this very export has — would otherwise query for a paper that cannot exist
/// and read the empty result as "not retracted".
#[must_use]
pub fn unusable_lookup_identifier(doi: Option<&str>, pmid: Option<&str>) -> bool {
    let d = clean_identifier(doi);
    let p = clean_identifier(pmid);
    d.is_none() && p.is_none()
}

// ---------------------------------------------------------------------------
// Storage
// ---------------------------------------------------------------------------

/// The columns a notice occupies, in the order the upsert writes them.
///
/// `created_at` and `updated_at` carry the same value on insert; the upsert's
/// `excluded` list excludes `created_at` so a re-import refreshes a notice without
/// rewriting when it was first seen.
pub const NOTICE_COLUMNS: [&str; 14] = [
    "record_id",
    "doi",
    "pmid",
    "notice_doi",
    "notice_pmid",
    "nature",
    "raw_nature",
    "title",
    "journal",
    "retraction_date",
    "original_paper_date",
    "reasons",
    "created_at",
    "updated_at",
];

/// Rows handed to one `executemany`.
///
/// **A bound and not an optimisation detail.** The export is 66,117 rows, and an
/// unbounded batch would materialise all of them before writing — which is what
/// the streaming parse exists to avoid. Both drivers run the statement once per
/// parameter set, so `ON CONFLICT` still resolves row by row and two notices
/// sharing a `record_id` *within* one chunk behave as they did one at a time,
/// unlike a single multi-row `VALUES`, which PostgreSQL rejects with *"ON CONFLICT
/// DO UPDATE command cannot affect row a second time"*.
pub const UPSERT_CHUNK_ROWS: usize = 1000;

/// One notice's parameters, in [`NOTICE_COLUMNS`] order.
#[must_use]
fn notice_values(notice: &RetractionNotice, now: &str) -> Vec<crate::db::Value> {
    use crate::db::Value as V;
    let opt = |value: Option<String>| value.map_or(V::Null, V::Text);
    vec![
        V::Text(notice.record_id.clone()),
        opt(normalize_doi(notice.doi.as_deref())),
        opt(normalize_pmid(notice.pmid.as_deref())),
        opt(normalize_doi(notice.notice_doi.as_deref())),
        opt(normalize_pmid(notice.notice_pmid.as_deref())),
        V::Text(notice.nature.as_str().to_string()),
        opt(notice.raw_nature.clone()),
        opt(notice.title.clone()),
        opt(notice.journal.clone()),
        opt(notice.retraction_date.clone()),
        opt(notice.original_paper_date.clone()),
        V::Text(serde_json::to_string(&notice.reasons).unwrap_or_else(|_| "[]".to_string())),
        V::Text(now.to_string()),
        V::Text(now.to_string()),
    ]
}

/// Insert or refresh retraction notices, keyed by `record_id`.
///
/// Re-importing the monthly export is **idempotent**: `record_id` is Retraction
/// Watch's own primary key and carries a `UNIQUE` constraint, so a second import of
/// the same file updates rather than duplicates.
///
/// Identifiers are normalised with the same functions
/// [`store_publication`](crate::publications::storage::store_publication) uses, so
/// a DOI stored here matches one looked up in any case or prefix variant.
///
/// The whole batch is **one transaction**. `Db::begin` is the port's equivalent of
/// the Python's `transaction()` context manager, and it nests by refusing rather
/// than by counting depth.
///
/// Returns the number of notices **processed**, not the rows left behind: two
/// notices sharing a `record_id` within one call count as 2, even though the
/// second's `ON CONFLICT` update leaves only one row.
///
/// # Errors
///
/// A database failure, naming the statement's table rather than leaving a bare
/// driver message.
pub fn store_retraction_notices(
    db: &mut dyn crate::db::Db,
    notices: &[RetractionNotice],
) -> Result<usize, String> {
    let now = crate::publications::models::now_utc();
    let columns = NOTICE_COLUMNS.join(", ");
    let placeholders = crate::db::placeholders(NOTICE_COLUMNS.len());
    let updates: Vec<String> = NOTICE_COLUMNS
        .iter()
        .filter(|column| **column != "record_id" && **column != "created_at")
        .map(|column| format!("{column} = excluded.{column}"))
        .collect();
    let statement = format!(
        "INSERT INTO retraction_notices ({columns}) VALUES ({placeholders}) \
         ON CONFLICT (record_id) DO UPDATE SET {}",
        updates.join(", ")
    );

    let mut transaction = db.begin().map_err(|e| e.to_string())?;
    let mut processed = 0usize;
    for chunk in notices.chunks(UPSERT_CHUNK_ROWS) {
        let rows: Vec<Vec<crate::db::Value>> = chunk
            .iter()
            .map(|notice| notice_values(notice, &now))
            .collect();
        crate::db::executemany(&mut *transaction, &statement, &rows).map_err(|e| e.to_string())?;
        processed += chunk.len();
    }
    transaction.commit().map_err(|e| e.to_string())?;
    Ok(processed)
}

/// Every stored notice about one paper, newest first.
///
/// A paper may have several notices — 2,354 papers in the live export do — so this
/// returns a list; pass it to [`is_retracted`] for the boolean.
///
/// Identifiers are normalised before the lookup, so any case or prefix variant of a
/// DOI matches the canonical stored form. Supplying both a DOI and a PMID matches a
/// notice on **either**.
///
/// The ordering is the Python's, including its subtlety: `(retraction_date IS
/// NULL)` sorts undated notices **last** rather than first, because a NULL would
/// otherwise sort before every date. `id DESC` breaks a tie so the newest import
/// wins.
///
/// # Errors
///
/// A database failure, or an unusable request: neither identifier given, or the
/// ones given reduce to nothing — blank, whitespace, a bare `https://doi.org/`
/// prefix, or the export's own "no identifier here" sentinels. **A programming
/// error, not an empty result**, which is why it is an `Err` rather than an empty
/// `Vec`.
pub fn lookup_retractions(
    db: &mut dyn crate::db::Db,
    doi: Option<&str>,
    pmid: Option<&str>,
) -> Result<Vec<RetractionNotice>, String> {
    use crate::db::Value as V;

    if doi.is_none() && pmid.is_none() {
        return Err("lookup_retractions() needs a doi or a pmid".to_string());
    }

    let mut clauses: Vec<String> = Vec::new();
    let mut params: Vec<V> = Vec::new();
    if let Some(cleaned) = clean_identifier(normalize_doi(doi).as_deref()) {
        clauses.push("doi = ?".to_string());
        params.push(V::Text(cleaned));
    }
    if let Some(cleaned) = clean_identifier(normalize_pmid(pmid).as_deref()) {
        clauses.push("pmid = ?".to_string());
        params.push(V::Text(cleaned));
    }
    if clauses.is_empty() {
        return Err(format!(
            "lookup_retractions() needs a usable doi or pmid; got doi={doi:?}, pmid={pmid:?}, \
             neither of which is an identifier"
        ));
    }

    let sql = format!(
        "SELECT {} FROM retraction_notices WHERE {} \
         ORDER BY (retraction_date IS NULL), retraction_date DESC, id DESC",
        NOTICE_COLUMNS.join(", "),
        clauses.join(" OR ")
    );
    let rows = crate::db::fetch_all(db, &sql, &params).map_err(|e| e.to_string())?;
    rows.iter().map(row_to_stored_notice).collect()
}

/// Build a [`RetractionNotice`] from a stored row.
///
/// **`RetractionNature::from_str` is the strict read here**, deliberately, where
/// the CSV path's [`RetractionNature::from_raw`] is forgiving. The asymmetry is the
/// point: a value in the CSV comes from a vocabulary this library does not own, so
/// an unknown one must cost a row rather than the import — but a value in this
/// column was written by this library, so an unknown one means the database was
/// written by a version that knows a notice type this one does not. Mapping it to
/// `Other` would make [`is_retracted`] read it as evidence of nothing and answer
/// *"not retracted"* — a silent wrong answer where an error is a loud, accurate
/// one.
///
/// # Errors
///
/// An absent required column, or a nature this version does not know.
pub fn row_to_stored_notice(row: &crate::db::Row) -> Result<RetractionNotice, String> {
    use crate::publications::storage::{json_list, text};

    let required = |name: &str| -> Result<String, String> {
        text(row, name).ok_or_else(|| format!("retraction_notices.{name} is not text"))
    };
    // **The enum's own spelling**, via `FromStr` — the underscore vocabulary
    // `to_json` writes, not the export file's spaced one. A value this version does
    // not know is an error rather than `Other`: see this function's own docs.
    let nature_raw = required("nature")?;
    let nature: RetractionNature = nature_raw
        .parse()
        .map_err(|e| format!("unknown retraction nature stored in the database: {e}"))?;

    Ok(RetractionNotice {
        record_id: required("record_id")?,
        nature,
        doi: text(row, "doi"),
        pmid: text(row, "pmid"),
        notice_doi: text(row, "notice_doi"),
        notice_pmid: text(row, "notice_pmid"),
        title: text(row, "title"),
        journal: text(row, "journal"),
        retraction_date: text(row, "retraction_date"),
        original_paper_date: text(row, "original_paper_date"),
        reasons: json_list(row, "reasons"),
        raw_nature: text(row, "raw_nature"),
    })
}
