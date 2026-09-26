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

//! Data models for publication ingestion, deduplication and sync.
//!
//! A port of `bmlib/publications/models.py`: the `Publication` record, its
//! child rows (`FullTextSource`, `Grant`, `AuthorAffiliation`), the download
//! ledger (`DownloadDay`, `PartCheckpoint`), the fetcher contract
//! (`FetchedRecord`, `FetchResult`, `SourceDescriptor`), the sync report, and
//! the Retraction Watch notice.
//!
//! # Three validators, and why they are the important part of this module
//!
//! `_require_text`, `_require_count` and `_require_datetime` exist because a
//! stored row is **read back on a path that decides whether work may be
//! skipped**. The Python docstrings say it better than a summary can:
//!
//! - `_require_text` exists because `str(value)` accepts everything, and a
//!   null `part_key` becomes the literal `"None"`, which matches no planned
//!   part — so resume degrades to re-fetching every unfinished day in full, a
//!   cost with no error.
//! - `_require_count` exists because `int(value)` reports neither the column
//!   nor the row, and raises `TypeError` where a caller catches `ValueError`
//!   (#99). `bool` is refused explicitly: it is an `int` subclass.
//! - `_require_datetime` exists because substituting *now* for a missing
//!   `downloaded_at` is **the single most durable-looking value** the
//!   day-selection rule can be handed, so the day is never fetched again
//!   (#98). It is the strict counterpart to the lenient parse used elsewhere.
//!
//! In Python these rules are enforced by convention on a `@dataclass` whose
//! fields are freely assignable. Here the same three rules are the only way to
//! build the types that carry them, and every message is reproduced verbatim
//! so a caller porting an `except ValueError` handler keeps working.

use serde_json::Value;
use std::collections::BTreeMap;

/// A stored row that cannot be read as the model it claims to be.
///
/// The messages match `ValueError` from the Python validators word for word:
/// this error type is what a caller catches where Python catches
/// `ValueError`, and a bulk deserialiser reports `to_string()`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ModelError {
    /// A `NOT NULL` column arrived absent or null.
    ///
    /// This is the one that matters: it is the shape that silently degrades
    /// resume or re-fetch behaviour rather than failing loudly.
    Required {
        /// The column or field being read.
        field: &'static str,
    },
    /// A value arrived of the wrong type.
    ///
    /// Carries the whole sentence, because Python's three validators word
    /// this differently and a caller matching on the message sees the
    /// difference: `_require_text` and `_require_count` say "must be a
    /// string"/"must be an integer", while `_require_datetime` says "must be
    /// an ISO 8601 string or a datetime".
    WrongType {
        /// The column or field being read.
        field: &'static str,
        /// What the validator expected, in its own words.
        expected: &'static str,
        /// The Python type name, so a ported message still reads the same.
        got: String,
    },
    /// A text column arrived blank.
    Blank {
        /// The column or field being read.
        field: &'static str,
    },
    /// A count could not be read as an integer.
    UnreadableInt {
        /// The column or field being read.
        field: &'static str,
        /// The offending value, rendered as Python's `repr` would.
        value: String,
    },
    /// A count was below its floor.
    BelowMinimum {
        /// The column or field being read.
        field: &'static str,
        /// The floor it had to meet.
        minimum: i64,
        /// What it actually was.
        got: i64,
    },
    /// A timestamp could not be read as ISO 8601.
    UnreadableTimestamp {
        /// The column or field being read.
        field: &'static str,
        /// The offending value, rendered as Python's `repr` would.
        value: String,
    },
    /// A `RetractionNature` string is not one of the enum's values.
    UnknownNature {
        /// The offending value.
        value: String,
    },
    /// A required JSON key was absent.
    ///
    /// Python reaches these through `data["key"]`, so the message is a
    /// `KeyError`'s payload: the bare key and nothing else. Reproduced
    /// literally, because a caller matching on it — or logging it beside a
    /// Python implementation's — sees the difference.
    MissingKey {
        /// The key that was absent.
        key: String,
    },
    /// A JSON value was not of the type the field requires.
    JsonType {
        /// The key whose value was wrong.
        key: String,
        /// What was expected.
        expected: &'static str,
    },
}

impl std::fmt::Display for ModelError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ModelError::Required { field } => write!(
                f,
                "{field} is required and must not be None; it is NOT NULL in the schema, \
                 so a row lacking it is malformed"
            ),
            ModelError::WrongType {
                field,
                expected,
                got,
            } => write!(f, "{field} must be {expected}, got {got}"),
            ModelError::Blank { field } => write!(f, "{field} must not be blank"),
            ModelError::UnreadableInt { field, value } => {
                write!(f, "{field} is not a readable integer: {value}")
            }
            ModelError::BelowMinimum {
                field,
                minimum,
                got,
            } => write!(f, "{field} must be at least {minimum}, got {got}"),
            ModelError::UnreadableTimestamp { field, value } => {
                write!(f, "{field} is not a readable ISO 8601 timestamp: {value}")
            }
            ModelError::UnknownNature { value } => {
                write!(f, "'{value}' is not a valid RetractionNature")
            }
            // `KeyError("record_id").args[0]` is `"record_id"`.
            ModelError::MissingKey { key } => write!(f, "{key}"),
            ModelError::JsonType { key, expected } => {
                write!(f, "{key} must be {expected}")
            }
        }
    }
}

impl std::error::Error for ModelError {}

/// The Python type name for a JSON value, so a ported message still reads the
/// same.
#[must_use]
pub fn json_type_name(value: &Value) -> String {
    match value {
        Value::Null => "NoneType",
        Value::Bool(_) => "bool",
        Value::Number(n) if n.is_f64() => "float",
        Value::Number(_) => "int",
        Value::String(_) => "str",
        Value::Array(_) => "list",
        Value::Object(_) => "dict",
    }
    .to_string()
}

/// Render a JSON value the way Python's `repr` would, for an error message.
///
/// Only the distinction that matters is reproduced: a string is single-quoted
/// and `null` is `None`. The three validators interpolate `{value!r}` into
/// their messages, and the oracle compares those messages.
#[must_use]
pub fn python_repr(value: &Value) -> String {
    match value {
        Value::Null => "None".to_string(),
        Value::Bool(true) => "True".to_string(),
        Value::Bool(false) => "False".to_string(),
        Value::String(s) => format!("'{s}'"),
        other => other.to_string(),
    }
}

// ---------------------------------------------------------------------------
// The three validators
// ---------------------------------------------------------------------------

/// Read a `TEXT NOT NULL` column a stored row must carry.
///
/// # Errors
///
/// [`ModelError::Required`] when absent or null, [`ModelError::WrongType`] for
/// a non-string, [`ModelError::Blank`] for whitespace only.
pub fn require_text(value: Option<&Value>, field: &'static str) -> Result<String, ModelError> {
    let Some(value) = value else {
        return Err(ModelError::Required { field });
    };
    if value.is_null() {
        return Err(ModelError::Required { field });
    }
    let Some(text) = value.as_str() else {
        return Err(ModelError::WrongType {
            field,
            expected: "a string",
            got: json_type_name(value),
        });
    };
    if text.trim().is_empty() {
        return Err(ModelError::Blank { field });
    }
    Ok(text.to_string())
}

/// Read an `INTEGER NOT NULL` column a stored row must carry.
///
/// A numeric string is read (Python's `int("12")` is 12); a `bool` is refused,
/// being an `int` subclass that nothing else here would catch; a float is
/// refused rather than truncated.
///
/// # Errors
///
/// As [`require_text`], plus [`ModelError::UnreadableInt`] and
/// [`ModelError::BelowMinimum`].
pub fn require_count(
    value: Option<&Value>,
    field: &'static str,
    minimum: i64,
) -> Result<i64, ModelError> {
    let Some(value) = value else {
        return Err(ModelError::Required { field });
    };
    if value.is_null() {
        return Err(ModelError::Required { field });
    }
    // Python accepts `int | str` and refuses everything else, `bool` first
    // because its `isinstance` check would otherwise pass it through.
    let number: i64 = match value {
        Value::Bool(_) => {
            return Err(ModelError::WrongType {
                field,
                expected: "an integer",
                got: "bool".to_string(),
            })
        }
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                i
            } else {
                return Err(ModelError::WrongType {
                    field,
                    expected: "an integer",
                    got: "float".to_string(),
                });
            }
        }
        Value::String(s) => match s.trim().parse::<i64>() {
            Ok(i) => i,
            Err(_) => {
                return Err(ModelError::UnreadableInt {
                    field,
                    value: python_repr(value),
                })
            }
        },
        other => {
            return Err(ModelError::WrongType {
                field,
                expected: "an integer",
                got: json_type_name(other),
            })
        }
    };

    if number < minimum {
        return Err(ModelError::BelowMinimum {
            field,
            minimum,
            got: number,
        });
    }
    Ok(number)
}

/// Parse a timestamp a stored row must carry, rather than inventing one.
///
/// Returns the canonical `±HH:MM` form, which is what
/// `datetime.fromisoformat(...).isoformat()` produces for the inputs the
/// schemas carry. The parsing is deliberately small and strict: it accepts the
/// ISO 8601 date, time and offset components Python's `fromisoformat` accepts
/// for this data, and refuses a `date` where a `datetime` is required — the
/// trap the Python docstring names, since `isinstance(dt, date)` is true but
/// the converse is not.
///
/// # Errors
///
/// As [`require_text`], plus [`ModelError::UnreadableTimestamp`].
pub fn require_datetime(value: Option<&Value>, field: &'static str) -> Result<String, ModelError> {
    let Some(value) = value else {
        return Err(ModelError::Required { field });
    };
    if value.is_null() {
        return Err(ModelError::Required { field });
    }
    let Some(text) = value.as_str() else {
        // Python names this branch's expectation differently from
        // `require_text`: a `datetime` object is also acceptable there, and
        // the message says so.
        return Err(ModelError::WrongType {
            field,
            expected: "an ISO 8601 string or a datetime",
            got: json_type_name(value),
        });
    };
    parse_iso8601(text).ok_or_else(|| ModelError::UnreadableTimestamp {
        field,
        value: python_repr(value),
    })
}

/// Read an optional timestamp, strict about a value that is present.
///
/// Python guards these reads with truthiness and then calls `_parse_datetime`,
/// which **raises** on an unparseable string. So an absent, null or empty
/// value is `None`, and a present-but-unreadable one is an error — the two are
/// not the same answer, and collapsing them would drop the error.
///
/// Python's `_parse_datetime` substitutes *now* for `None`; this port does not,
/// for the reason recorded on [`require_datetime`] — the substitution invents
/// a timestamp on a path that never had one.
///
/// # Errors
///
/// [`ModelError::UnreadableTimestamp`] when the value is present, truthy and
/// unparseable.
pub fn parse_optional_datetime(
    value: Option<&Value>,
    field: &'static str,
) -> Result<Option<String>, ModelError> {
    let Some(value) = value.filter(|v| !v.is_null()) else {
        return Ok(None);
    };
    let Some(text) = value.as_str() else {
        return Err(ModelError::WrongType {
            field,
            expected: "an ISO 8601 string or a datetime",
            got: json_type_name(value),
        });
    };
    if text.is_empty() {
        return Ok(None);
    }
    parse_iso8601(text)
        .map(Some)
        .ok_or_else(|| ModelError::UnreadableTimestamp {
            field,
            value: python_repr(value),
        })
}

/// The current UTC time in the canonical `±HH:MM` form.
#[must_use]
pub fn now_utc() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    format_epoch(secs as i64)
}

/// Format a Unix timestamp as ISO 8601 with a `+00:00` offset.
fn format_epoch(secs: i64) -> String {
    let days = secs.div_euclid(86_400);
    let rem = secs.rem_euclid(86_400);
    let (hour, minute, second) = (rem / 3600, (rem % 3600) / 60, rem % 60);
    let (y, m, d) = civil_from_days(days);
    format!("{y:04}-{m:02}-{d:02}T{hour:02}:{minute:02}:{second:02}+00:00")
}

/// Days since 1970-01-01 → civil date, by Howard Hinnant's algorithm.
fn civil_from_days(days: i64) -> (i64, u32, u32) {
    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    (y, m as u32, d as u32)
}

/// Parse an ISO 8601 date-time, returning `datetime.isoformat()`'s shape.
///
/// Mirrors Python's `datetime.fromisoformat`, which is what the model layer
/// calls — so a bare date gains `T00:00:00`, a time without an offset stays
/// naive (no offset is invented), and an offset is echoed as written. The
/// oracle pins all three, and each is a place a hand-written date parser
/// normally diverges.
///
/// Accepts `YYYY-MM-DD`, `YYYY-MM-DDTHH:MM`, `…:SS`, a fractional second, and
/// a `±HH:MM`, `±HHMM`, `±HH` or `Z` offset.
#[must_use]
pub fn parse_iso8601(text: &str) -> Option<String> {
    let bytes = text.as_bytes();
    if bytes.len() < 10 {
        return None;
    }
    let (year, month, day) = parse_date(&text[0..10])?;

    if bytes.len() == 10 {
        // `fromisoformat("2024-01-02")` is midnight that day.
        return Some(format!("{year:04}-{month:02}-{day:02}T00:00:00"));
    }
    // A separator must follow the date, and it must be `T` or a space.
    if bytes[10] != b'T' && bytes[10] != b't' && bytes[10] != b' ' {
        return None;
    }
    let rest = &text[11..];
    if rest.len() < 5 {
        return None;
    }
    let hour: u32 = rest.get(0..2)?.parse().ok()?;
    if rest.as_bytes()[2] != b':' {
        return None;
    }
    let minute: u32 = rest.get(3..5)?.parse().ok()?;
    if hour > 23 || minute > 59 {
        return None;
    }

    let mut idx = 5;
    let mut second = 0u32;
    let mut fraction = String::new();
    if rest.len() > idx && rest.as_bytes()[idx] == b':' {
        second = rest.get(idx + 1..idx + 3)?.parse().ok()?;
        if second > 59 {
            return None;
        }
        idx += 3;
        if rest.len() > idx && rest.as_bytes()[idx] == b'.' {
            let start = idx + 1;
            let mut end = start;
            while end < rest.len() && rest.as_bytes()[end].is_ascii_digit() {
                end += 1;
            }
            if end == start {
                return None;
            }
            fraction = rest[start..end].to_string();
            idx = end;
        }
    }

    let tail = rest.get(idx..)?;
    let mut out = format!("{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}");
    if !fraction.is_empty() {
        out.push('.');
        out.push_str(&fraction);
    }
    // An absent offset is absent, not UTC: `fromisoformat` returns a naive
    // datetime, and inventing `+00:00` would claim the source stated a zone.
    if !tail.is_empty() {
        out.push_str(&parse_offset(tail)?);
    }
    Some(out)
}

fn parse_date(text: &str) -> Option<(i64, u32, u32)> {
    let b = text.as_bytes();
    if b.len() != 10 || b[4] != b'-' || b[7] != b'-' {
        return None;
    }
    let year: i64 = text.get(0..4)?.parse().ok()?;
    let month: u32 = text.get(5..7)?.parse().ok()?;
    let day: u32 = text.get(8..10)?.parse().ok()?;
    if !(1..=12).contains(&month) || day < 1 || day > days_in_month(year, month) {
        return None;
    }
    Some((year, month, day))
}

fn days_in_month(year: i64, month: u32) -> u32 {
    let leap = (year % 4 == 0 && year % 100 != 0) || year % 400 == 0;
    match month {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        2 if leap => 29,
        2 => 28,
        _ => 0,
    }
}

fn parse_offset(text: &str) -> Option<String> {
    if text == "Z" || text == "z" {
        return Some("+00:00".to_string());
    }
    let b = text.as_bytes();
    let sign = match b[0] {
        b'+' => '+',
        b'-' => '-',
        _ => return None,
    };
    let digits = &text[1..];
    let (hh, mm) = match digits.len() {
        2 => (digits.get(0..2)?.parse::<u32>().ok()?, 0),
        4 => (
            digits.get(0..2)?.parse::<u32>().ok()?,
            digits.get(2..4)?.parse::<u32>().ok()?,
        ),
        5 if digits.as_bytes()[2] == b':' => (
            digits.get(0..2)?.parse::<u32>().ok()?,
            digits.get(3..5)?.parse::<u32>().ok()?,
        ),
        _ => return None,
    };
    if hh > 23 || mm > 59 {
        return None;
    }
    Some(format!("{sign}{hh:02}:{mm:02}"))
}

// ---------------------------------------------------------------------------
// JSON helpers
// ---------------------------------------------------------------------------

fn get_str(data: &Value, key: &str) -> Option<String> {
    data.get(key).and_then(Value::as_str).map(str::to_string)
}

fn get_opt_i64(data: &Value, key: &str) -> Option<i64> {
    data.get(key).and_then(Value::as_i64)
}

fn get_str_list(data: &Value, key: &str) -> Vec<String> {
    data.get(key)
        .and_then(Value::as_array)
        .map(|a| {
            a.iter()
                .filter_map(Value::as_str)
                .map(str::to_string)
                .collect()
        })
        .unwrap_or_default()
}

fn required_key<'a>(data: &'a Value, key: &str) -> Result<&'a Value, ModelError> {
    data.get(key).ok_or_else(|| ModelError::MissingKey {
        key: key.to_string(),
    })
}

fn required_str(data: &Value, key: &str) -> Result<String, ModelError> {
    let value = required_key(data, key)?;
    value
        .as_str()
        .map(str::to_string)
        .ok_or_else(|| ModelError::JsonType {
            key: key.to_string(),
            expected: "a string",
        })
}

fn required_i64(data: &Value, key: &str) -> Result<i64, ModelError> {
    let value = required_key(data, key)?;
    value.as_i64().ok_or_else(|| ModelError::JsonType {
        key: key.to_string(),
        expected: "an integer",
    })
}

// ---------------------------------------------------------------------------
// Publication
// ---------------------------------------------------------------------------

/// A biomedical publication record.
#[derive(Debug, Clone, PartialEq)]
pub struct Publication {
    /// The row id, when stored.
    pub id: Option<i64>,
    /// The article title.
    pub title: String,
    /// Digital object identifier.
    pub doi: Option<String>,
    /// PubMed id.
    pub pmid: Option<String>,
    /// PubMed Central id.
    pub pmcid: Option<String>,
    /// The abstract.
    pub abstract_text: Option<String>,
    /// Author names.
    pub authors: Vec<String>,
    /// Journal name.
    pub journal: Option<String>,
    /// ISO publication date.
    pub publication_date: Option<String>,
    /// PubMed publication types.
    pub publication_types: Vec<String>,
    /// Keywords.
    pub keywords: Vec<String>,
    /// Whether the record is open access.
    pub is_open_access: bool,
    /// The licence, when stated.
    pub license: Option<String>,
    /// Every source that asserted this record.
    pub sources: Vec<String>,
    /// The source that first delivered it.
    pub first_seen_source: String,
    /// When the row was created.
    pub created_at: String,
    /// When the row was last written.
    pub updated_at: String,
}

impl Publication {
    /// A new record, stamped with the current time.
    #[must_use]
    pub fn new(title: impl Into<String>, first_seen_source: impl Into<String>) -> Self {
        let source = first_seen_source.into();
        let now = now_utc();
        Publication {
            id: None,
            title: title.into(),
            doi: None,
            pmid: None,
            pmcid: None,
            abstract_text: None,
            authors: Vec::new(),
            journal: None,
            publication_date: None,
            publication_types: Vec::new(),
            keywords: Vec::new(),
            is_open_access: false,
            license: None,
            sources: vec![source.clone()],
            first_seen_source: source,
            created_at: now.clone(),
            updated_at: now,
        }
    }

    /// Serialise to a plain JSON object.
    #[must_use]
    pub fn to_json(&self) -> Value {
        serde_json::json!({
            "id": self.id,
            "title": self.title,
            "doi": self.doi,
            "pmid": self.pmid,
            "pmcid": self.pmcid,
            "abstract": self.abstract_text,
            "authors": self.authors,
            "journal": self.journal,
            "publication_date": self.publication_date,
            "publication_types": self.publication_types,
            "keywords": self.keywords,
            "is_open_access": self.is_open_access,
            "license": self.license,
            "sources": self.sources,
            "first_seen_source": self.first_seen_source,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        })
    }

    /// Deserialise from [`Self::to_json`] output.
    ///
    /// `created_at` and `updated_at` are lenient here, unlike
    /// [`DownloadDay::from_json`]: Python's `Publication.from_dict` calls
    /// `_parse_datetime`, which substitutes *now*. That substitution is
    /// harmless for a publication — no rule decides whether to re-fetch a
    /// paper from its `created_at` — so the port keeps the lenient read and
    /// substitutes now, as Python does.
    ///
    /// # Errors
    ///
    /// If `title`, `sources` or `first_seen_source` is absent or of the wrong
    /// type.
    pub fn from_json(data: &Value) -> Result<Self, ModelError> {
        let now = now_utc();
        Ok(Publication {
            id: get_opt_i64(data, "id"),
            title: required_str(data, "title")?,
            doi: get_str(data, "doi"),
            pmid: get_str(data, "pmid"),
            pmcid: get_str(data, "pmcid"),
            abstract_text: get_str(data, "abstract"),
            authors: get_str_list(data, "authors"),
            journal: get_str(data, "journal"),
            publication_date: get_str(data, "publication_date"),
            publication_types: get_str_list(data, "publication_types"),
            keywords: get_str_list(data, "keywords"),
            is_open_access: data
                .get("is_open_access")
                .and_then(Value::as_bool)
                .unwrap_or(false),
            license: get_str(data, "license"),
            sources: get_str_list(data, "sources"),
            first_seen_source: required_str(data, "first_seen_source")?,
            created_at: parse_optional_datetime(data.get("created_at"), "created_at")?
                .unwrap_or(now.clone()),
            updated_at: parse_optional_datetime(data.get("updated_at"), "updated_at")?
                .unwrap_or(now),
        })
    }
}

/// A full-text source for a publication (e.g. PMC XML, publisher PDF).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FullTextSource {
    /// The row id, when stored.
    pub id: Option<i64>,
    /// The owning publication.
    pub publication_id: i64,
    /// The source name.
    pub source: String,
    /// Where the text lives.
    pub url: String,
    /// `xml`, `pdf`, `html`, …
    pub format: String,
    /// The version, when stated.
    pub version: Option<String>,
    /// When it was fetched.
    pub retrieved_at: Option<String>,
    /// When the row was created.
    pub created_at: String,
}

impl FullTextSource {
    /// A new source, stamped with the current time.
    #[must_use]
    pub fn new(
        publication_id: i64,
        source: impl Into<String>,
        url: impl Into<String>,
        format: impl Into<String>,
    ) -> Self {
        FullTextSource {
            id: None,
            publication_id,
            source: source.into(),
            url: url.into(),
            format: format.into(),
            version: None,
            retrieved_at: None,
            created_at: now_utc(),
        }
    }

    /// Serialise to a plain JSON object.
    #[must_use]
    pub fn to_json(&self) -> Value {
        serde_json::json!({
            "id": self.id,
            "publication_id": self.publication_id,
            "source": self.source,
            "url": self.url,
            "format": self.format,
            "version": self.version,
            "retrieved_at": self.retrieved_at,
            "created_at": self.created_at,
        })
    }

    /// Deserialise from [`Self::to_json`] output.
    ///
    /// # Errors
    ///
    /// If `publication_id`, `source`, `url` or `format` is absent or mismatched.
    pub fn from_json(data: &Value) -> Result<Self, ModelError> {
        Ok(FullTextSource {
            id: get_opt_i64(data, "id"),
            publication_id: required_i64(data, "publication_id")?,
            source: required_str(data, "source")?,
            url: required_str(data, "url")?,
            format: required_str(data, "format")?,
            version: get_str(data, "version"),
            retrieved_at: parse_optional_datetime(data.get("retrieved_at"), "retrieved_at")?,
            created_at: parse_optional_datetime(data.get("created_at"), "created_at")?
                .unwrap_or_else(now_utc),
        })
    }
}

/// A funding award backing a publication, from PubMed's `<GrantList>`.
///
/// Every field is optional because PubMed's own records are. A grant naming
/// neither an agency nor an id carries no information and is not stored.
///
/// `source` scopes storage: re-storing a source's grants replaces that
/// source's rows and leaves every other source's alone, so two sources'
/// funding data coexist instead of overwriting each other on alternate syncs.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct Grant {
    /// The row id, when stored.
    pub id: Option<i64>,
    /// The owning publication; **ignored on the way in**.
    pub publication_id: i64,
    /// The source that asserted this grant.
    pub source: String,
    /// The funding agency.
    pub agency: Option<String>,
    /// The award id.
    pub grant_id: Option<String>,
    /// The agency's country.
    pub country: Option<String>,
}

impl Grant {
    /// Serialise to a plain JSON object.
    #[must_use]
    pub fn to_json(&self) -> Value {
        serde_json::json!({
            "id": self.id,
            "publication_id": self.publication_id,
            "source": self.source,
            "agency": self.agency,
            "grant_id": self.grant_id,
            "country": self.country,
        })
    }

    /// Deserialise from [`Self::to_json`] output.
    #[must_use]
    pub fn from_json(data: &Value) -> Self {
        Grant {
            id: get_opt_i64(data, "id"),
            publication_id: get_opt_i64(data, "publication_id").unwrap_or(0),
            source: get_str(data, "source").unwrap_or_default(),
            agency: get_str(data, "agency"),
            grant_id: get_str(data, "grant_id"),
            country: get_str(data, "country"),
        }
    }

    /// Whether this grant carries anything worth storing.
    ///
    /// A grant naming neither an agency nor an award id carries no
    /// information; `store_publication` drops it.
    #[must_use]
    pub fn is_informative(&self) -> bool {
        self.agency.is_some() || self.grant_id.is_some()
    }
}

/// One author's stated affiliation, from PubMed's `<AffiliationInfo>`.
///
/// One row per *(author, affiliation)* pair — an author listing three
/// institutions produces three of these. `position` is the author's 0-based
/// index in the `<AuthorList>`, carried because first- and senior-author
/// affiliations are what a conflict-of-interest check cares about.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct AuthorAffiliation {
    /// The row id, when stored.
    pub id: Option<i64>,
    /// The owning publication; **ignored on the way in**.
    pub publication_id: i64,
    /// The source that asserted this row.
    pub source: String,
    /// The author's name, formatted `"Last, Fore"`.
    pub author: String,
    /// The affiliation, as Markdown.
    pub affiliation: String,
    /// The author's 0-based index in the `<AuthorList>`.
    pub position: i64,
}

impl AuthorAffiliation {
    /// Serialise to a plain JSON object.
    #[must_use]
    pub fn to_json(&self) -> Value {
        serde_json::json!({
            "id": self.id,
            "publication_id": self.publication_id,
            "source": self.source,
            "author": self.author,
            "affiliation": self.affiliation,
            "position": self.position,
        })
    }

    /// Deserialise from [`Self::to_json`] output.
    ///
    /// # Errors
    ///
    /// If `author` or `affiliation` is absent or not a string.
    pub fn from_json(data: &Value) -> Result<Self, ModelError> {
        Ok(AuthorAffiliation {
            id: get_opt_i64(data, "id"),
            publication_id: get_opt_i64(data, "publication_id").unwrap_or(0),
            source: get_str(data, "source").unwrap_or_default(),
            author: required_str(data, "author")?,
            affiliation: required_str(data, "affiliation")?,
            position: get_opt_i64(data, "position").unwrap_or(0),
        })
    }
}

/// The two values `download_days.status` may hold.
///
/// Deliberately *not* the type of [`FetchResult::status`]. That field takes
/// whatever a fetcher returns, including a third-party one registered through
/// `register_source`, so it stays a `String` and is validated at the boundary.
/// A status the table does not recognise is not cosmetic:
/// `days_needing_fetch` treats anything that is not `"completed"` as needing a
/// re-fetch, so an unrecognised value silently changes which days are ever
/// fetched again.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum DayStatus {
    /// The day was delivered and reconciled.
    Completed,
    /// The day failed.
    Failed,
}

impl DayStatus {
    /// The value written to the `status` column.
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            DayStatus::Completed => "completed",
            DayStatus::Failed => "failed",
        }
    }
}

impl std::str::FromStr for DayStatus {
    type Err = String;

    /// Read a stored status, refusing anything the table does not recognise.
    ///
    /// # Errors
    ///
    /// Naming the unrecognised value: `days_needing_fetch` would treat it as
    /// needing a re-fetch, which is a behaviour change and not a typo.
    fn from_str(raw: &str) -> Result<Self, Self::Err> {
        match raw {
            "completed" => Ok(DayStatus::Completed),
            "failed" => Ok(DayStatus::Failed),
            other => Err(format!(
                "unrecognised download_days.status {other:?}; the table holds \
                 \"completed\" or \"failed\", and any other value is re-fetched \
                 for ever"
            )),
        }
    }
}

/// Tracks download status for a single source on a single date.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DownloadDay {
    /// The row id, when stored.
    pub id: Option<i64>,
    /// The source name.
    pub source: String,
    /// The date, `yyyy-mm-dd`.
    pub date: String,
    /// The stored status string.
    pub status: String,
    /// How many records the day delivered.
    pub record_count: i64,
    /// When the day was downloaded.
    pub downloaded_at: String,
    /// When the day was last verified.
    pub last_verified_at: Option<String>,
}

impl DownloadDay {
    /// A new ledger row, stamped with the current time.
    #[must_use]
    pub fn new(
        source: impl Into<String>,
        date: impl Into<String>,
        status: impl Into<String>,
        record_count: i64,
    ) -> Self {
        DownloadDay {
            id: None,
            source: source.into(),
            date: date.into(),
            status: status.into(),
            record_count,
            downloaded_at: now_utc(),
            last_verified_at: None,
        }
    }

    /// Serialise to a plain JSON object.
    #[must_use]
    pub fn to_json(&self) -> Value {
        serde_json::json!({
            "id": self.id,
            "source": self.source,
            "date": self.date,
            "status": self.status,
            "record_count": self.record_count,
            "downloaded_at": self.downloaded_at,
            "last_verified_at": self.last_verified_at,
        })
    }

    /// Deserialise from a stored row.
    ///
    /// `downloaded_at` is **required**, unlike the constructor's stamp: that
    /// describes a fetch which has just happened, whereas here the row was
    /// already stored, and inventing a timestamp fails open against the
    /// durability rule that reads the column (#98).
    ///
    /// `last_verified_at` cannot fail — an absent or unreadable value there
    /// means "recheck this day", which fails closed.
    ///
    /// # Errors
    ///
    /// If `source`, `date` or `status` is absent, or `record_count` or
    /// `downloaded_at` is unreadable.
    pub fn from_json(data: &Value) -> Result<Self, ModelError> {
        Ok(DownloadDay {
            id: get_opt_i64(data, "id"),
            source: required_str(data, "source")?,
            date: required_str(data, "date")?,
            status: required_str(data, "status")?,
            record_count: required_i64(data, "record_count")?,
            downloaded_at: require_datetime(data.get("downloaded_at"), "downloaded_at")?,
            last_verified_at: parse_optional_datetime(
                data.get("last_verified_at"),
                "last_verified_at",
            )?,
        })
    }
}

/// Result of fetching records from a source for a given date.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FetchResult {
    /// The source name.
    pub source: String,
    /// The date fetched.
    pub date: String,
    /// How many records arrived.
    pub record_count: i64,
    /// The fetcher's status string.
    pub status: String,
    /// The error, when the fetch failed.
    pub error: Option<String>,
    /// Something the caller should know about a day that still completed.
    ///
    /// A shortfall too small to fail on is the case this exists for: the day
    /// is recorded as done, and without a returned value the only trace was
    /// one log line, so no caller could answer "which of my completed days
    /// came up short?" afterwards.
    pub note: Option<String>,
}

/// Progress report during a sync operation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SyncProgress {
    /// The source being synced.
    pub source: String,
    /// The date being fetched.
    pub date: String,
    /// Records processed so far.
    pub records_processed: i64,
    /// Records expected.
    pub records_total: i64,
    /// The current status.
    pub status: String,
    /// A human-readable message.
    pub message: Option<String>,
}

/// Summary report after completing a sync operation.
///
/// `sources_synced` lists every source whose sync loop ran to completion,
/// including sources where individual days failed. `notes` carries what went
/// imperfectly on days that nevertheless completed — kept apart from `errors`
/// because an error names a day that will be retried, a note names a day that
/// will not be.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct SyncReport {
    /// Every source whose sync loop ran to completion.
    pub sources_synced: Vec<String>,
    /// How many days were processed.
    pub days_processed: i64,
    /// How many records were inserted.
    pub records_added: i64,
    /// How many records were merged into an existing row.
    pub records_merged: i64,
    /// How many records failed to store.
    pub records_failed: i64,
    /// Per-day failures.
    pub errors: Vec<String>,
    /// Completed days that came up short.
    pub notes: Vec<String>,
}

/// Canonical record format returned by all source fetchers.
#[derive(Debug, Clone, PartialEq)]
pub struct FetchedRecord {
    /// The article title.
    pub title: String,
    /// The source that produced this record.
    pub source: String,
    /// Digital object identifier.
    pub doi: Option<String>,
    /// PubMed id.
    pub pmid: Option<String>,
    /// PubMed Central id.
    pub pmc_id: Option<String>,
    /// The abstract.
    pub abstract_text: Option<String>,
    /// Author names.
    pub authors: Vec<String>,
    /// Journal name.
    pub journal: Option<String>,
    /// ISO publication date.
    pub publication_date: Option<String>,
    /// Keywords.
    pub keywords: Vec<String>,
    /// Publication types.
    pub publication_types: Vec<String>,
    /// Whether the record is open access.
    pub is_open_access: bool,
    /// The licence, when stated.
    pub license: Option<String>,
    /// Where the full text can be fetched.
    pub fulltext_sources: Vec<FullTextSourceEntry>,
    /// Source-specific extras.
    pub extras: BTreeMap<String, Value>,
    /// Funding awards.
    pub grants: Vec<Grant>,
    /// Author affiliations.
    pub author_affiliations: Vec<AuthorAffiliation>,
}

impl FetchedRecord {
    /// A new record from its two required fields.
    #[must_use]
    pub fn new(title: impl Into<String>, source: impl Into<String>) -> Self {
        FetchedRecord {
            title: title.into(),
            source: source.into(),
            doi: None,
            pmid: None,
            pmc_id: None,
            abstract_text: None,
            authors: Vec::new(),
            journal: None,
            publication_date: None,
            keywords: Vec::new(),
            publication_types: Vec::new(),
            is_open_access: false,
            license: None,
            fulltext_sources: Vec::new(),
            extras: BTreeMap::new(),
            grants: Vec::new(),
            author_affiliations: Vec::new(),
        }
    }
}

/// A full-text location a fetcher found, as the fetcher contract carries it.
///
/// The port keeps this as the JSON shape `fulltext.models.FullTextSourceEntry`
/// serialises to, rather than importing that module: `publications` does not
/// depend on `fulltext` in Python either, and the field is pass-through data.
pub type FullTextSourceEntry = Value;

/// Describes one configurable parameter for a source fetcher.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SourceParam {
    /// The parameter name.
    pub name: String,
    /// What it does.
    pub description: String,
    /// Whether it must be supplied.
    pub required: bool,
    /// Its default, when it has one.
    pub default: Option<String>,
    /// Whether it is a secret, and so never logged.
    pub secret: bool,
}

impl SourceParam {
    /// A required-or-not parameter with no default.
    #[must_use]
    pub fn new(name: impl Into<String>, description: impl Into<String>, required: bool) -> Self {
        SourceParam {
            name: name.into(),
            description: description.into(),
            required,
            default: None,
            secret: false,
        }
    }
}

/// Metadata describing a registered publication source.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SourceDescriptor {
    /// The registry key.
    pub name: String,
    /// The human-readable name.
    pub display_name: String,
    /// What the source covers.
    pub description: String,
    /// Its configurable parameters.
    pub params: Vec<SourceParam>,
    /// Whether `sync` may pass this fetcher the per-part resume keywords.
    ///
    /// Defaults to `false` because `register_source` is public: a fetcher
    /// written against an earlier bmlib does not accept them, and passing an
    /// unexpected keyword would raise inside the per-day handler and record a
    /// working source's day as failed.
    pub resumable: bool,
}

impl SourceDescriptor {
    /// A descriptor for a non-resumable source with no parameters.
    #[must_use]
    pub fn new(
        name: impl Into<String>,
        display_name: impl Into<String>,
        description: impl Into<String>,
    ) -> Self {
        SourceDescriptor {
            name: name.into(),
            display_name: display_name.into(),
            description: description.into(),
            params: Vec::new(),
            resumable: false,
        }
    }
}

/// One completed partition of a day, so a re-run can skip it.
///
/// A day too large for one history session is fetched as parts. Each part's
/// records and its checkpoint are written in one transaction, so a checkpoint
/// never attests to records a rollback discarded.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PartCheckpoint {
    /// Which partitioning scheme wrote `part_key`.
    ///
    /// Stored on every row and read back, but never branched on — that is what
    /// it is for. A scheme whose keys change spelling matches nothing rather
    /// than matching wrongly.
    pub part_scheme: String,
    /// This part's identity, opaque to storage and compared with `==`.
    ///
    /// A second spelling of the same range matches no checkpoint, so resume
    /// degrades to a full re-fetch with nothing raised.
    pub part_key: String,
    /// The count the part's own session reported when it was walked.
    pub promised: i64,
    /// Records parsed and stored for this part; never the delivered count.
    pub record_count: i64,
}

impl PartCheckpoint {
    /// Build a checkpoint, refusing one that cannot describe a finished part.
    ///
    /// A planned part always promises at least one record, and a checkpointed
    /// one stored what it walked. There is deliberately no
    /// `record_count <= promised` rule: the two count different things, and
    /// `reconcile_delivery` treats delivery at or above the promise as clean,
    /// so the relation is normal but not guaranteed.
    ///
    /// # Errors
    ///
    /// If either name is blank, `promised` is below 1, or `record_count` is
    /// negative.
    pub fn new(
        part_scheme: impl Into<String>,
        part_key: impl Into<String>,
        promised: i64,
        record_count: i64,
    ) -> Result<Self, ModelError> {
        let part_scheme = part_scheme.into();
        let part_key = part_key.into();
        if part_scheme.trim().is_empty() {
            return Err(ModelError::Blank {
                field: "part_scheme",
            });
        }
        if part_key.trim().is_empty() {
            return Err(ModelError::Blank { field: "part_key" });
        }
        if promised < 1 {
            return Err(ModelError::BelowMinimum {
                field: "promised",
                minimum: 1,
                got: promised,
            });
        }
        if record_count < 0 {
            return Err(ModelError::BelowMinimum {
                field: "record_count",
                minimum: 0,
                got: record_count,
            });
        }
        Ok(PartCheckpoint {
            part_scheme,
            part_key,
            promised,
            record_count,
        })
    }

    /// The four columns that describe the part itself, not the whole row.
    ///
    /// `source` and `date` are the caller's context and `completed_at` is
    /// stamped by the writer and never read back, so a round trip through this
    /// pair does not reproduce a `download_day_parts` row.
    #[must_use]
    pub fn to_json(&self) -> Value {
        serde_json::json!({
            "part_scheme": self.part_scheme,
            "part_key": self.part_key,
            "promised": self.promised,
            "record_count": self.record_count,
        })
    }

    /// Build a checkpoint from a stored row, refusing to guess a column.
    ///
    /// Read strictly, for the reason [`DownloadDay::from_json`] is (#98, #99):
    /// this is the reader on the day-selection path, and it runs before
    /// `sync` enters the handler that owns a day — so anything it raises that
    /// a caller cannot catch takes the whole multi-source run's report with
    /// it.
    ///
    /// # Errors
    ///
    /// If any of the four columns is absent, null, of the wrong type, or
    /// outside the range a finished part can have.
    pub fn from_json(data: &Value) -> Result<Self, ModelError> {
        PartCheckpoint::new(
            require_text(data.get("part_scheme"), "part_scheme")?,
            require_text(data.get("part_key"), "part_key")?,
            require_count(data.get("promised"), "promised", 1)?,
            require_count(data.get("record_count"), "record_count", 0)?,
        )
    }
}

/// The kind of notice a Retraction Watch row records.
///
/// `Other` is forward-compatibility, not a case the current export exercises.
/// The vocabulary belongs to Retraction Watch, so a value this enum does not
/// know must cost one row of fidelity rather than abort the import — the raw
/// string is kept in [`RetractionNotice::raw_nature`].
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum RetractionNature {
    /// The paper was retracted.
    Retraction,
    /// A correction was issued.
    Correction,
    /// An expression of concern was issued.
    ExpressionOfConcern,
    /// The paper was reinstated.
    Reinstatement,
    /// A value this enum does not know.
    Other,
}

impl RetractionNature {
    /// The value written to the `nature` column.
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            RetractionNature::Retraction => "retraction",
            RetractionNature::Correction => "correction",
            RetractionNature::ExpressionOfConcern => "expression_of_concern",
            RetractionNature::Reinstatement => "reinstatement",
            RetractionNature::Other => "other",
        }
    }

    /// Map an export's `RetractionNature` cell onto this enum.
    ///
    /// Matching is case-insensitive on a stripped value: the export writes
    /// `"Expression of concern"` with a lower-case `c`. An unrecognised or
    /// empty value maps to [`Self::Other`].
    ///
    /// This reads the **file's** wording (spaces, any case), a different
    /// vocabulary from the enum's own values (underscores). Conflating the two
    /// silently maps every expression of concern to `Other`.
    #[must_use]
    pub fn from_raw(value: Option<&str>) -> Self {
        match value.unwrap_or_default().trim().to_lowercase().as_str() {
            "retraction" => RetractionNature::Retraction,
            "correction" => RetractionNature::Correction,
            "expression of concern" => RetractionNature::ExpressionOfConcern,
            "reinstatement" => RetractionNature::Reinstatement,
            _ => RetractionNature::Other,
        }
    }
}

impl std::str::FromStr for RetractionNature {
    type Err = ModelError;

    /// Read the enum's **own** spelling, which is what `to_json` writes.
    ///
    /// # Errors
    ///
    /// Naming an unknown value. Note this reads the underscore vocabulary;
    /// [`RetractionNature::from_raw`] reads the export file's.
    fn from_str(raw: &str) -> Result<Self, Self::Err> {
        match raw {
            "retraction" => Ok(RetractionNature::Retraction),
            "correction" => Ok(RetractionNature::Correction),
            "expression_of_concern" => Ok(RetractionNature::ExpressionOfConcern),
            "reinstatement" => Ok(RetractionNature::Reinstatement),
            "other" => Ok(RetractionNature::Other),
            other => Err(ModelError::UnknownNature {
                value: other.to_string(),
            }),
        }
    }
}

impl std::fmt::Display for RetractionNature {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.as_str())
    }
}

/// One Retraction Watch notice about one paper.
///
/// A row of the export describes **two** papers, so both identifier pairs are
/// carried under names that say which is which: [`Self::doi`] and
/// [`Self::pmid`] are always the **retracted paper** (the export's
/// `OriginalPaper*` columns), and [`Self::notice_doi`] and
/// [`Self::notice_pmid`] are the retraction notice itself (its `Retraction*`
/// columns). They are sometimes equal.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RetractionNotice {
    /// Retraction Watch's own row id.
    pub record_id: String,
    /// The kind of notice.
    pub nature: RetractionNature,
    /// DOI of the retracted paper.
    pub doi: Option<String>,
    /// PMID of the retracted paper.
    pub pmid: Option<String>,
    /// DOI of the notice.
    pub notice_doi: Option<String>,
    /// PMID of the notice.
    pub notice_pmid: Option<String>,
    /// Title of the retracted paper.
    pub title: Option<String>,
    /// Journal of the retracted paper.
    pub journal: Option<String>,
    /// ISO date of the retraction.
    pub retraction_date: Option<String>,
    /// ISO date of the original paper.
    pub original_paper_date: Option<String>,
    /// Why it was retracted.
    pub reasons: Vec<String>,
    /// The export's own wording, kept for a value the enum does not know.
    pub raw_nature: Option<String>,
}

impl RetractionNotice {
    /// A notice from its two required fields.
    #[must_use]
    pub fn new(record_id: impl Into<String>, nature: RetractionNature) -> Self {
        RetractionNotice {
            record_id: record_id.into(),
            nature,
            doi: None,
            pmid: None,
            notice_doi: None,
            notice_pmid: None,
            title: None,
            journal: None,
            retraction_date: None,
            original_paper_date: None,
            reasons: Vec::new(),
            raw_nature: None,
        }
    }

    /// Serialise to a plain JSON object.
    #[must_use]
    pub fn to_json(&self) -> Value {
        serde_json::json!({
            "record_id": self.record_id,
            "nature": self.nature.as_str(),
            "doi": self.doi,
            "pmid": self.pmid,
            "notice_doi": self.notice_doi,
            "notice_pmid": self.notice_pmid,
            "title": self.title,
            "journal": self.journal,
            "retraction_date": self.retraction_date,
            "original_paper_date": self.original_paper_date,
            "reasons": self.reasons,
            "raw_nature": self.raw_nature,
        })
    }

    /// Deserialise from [`Self::to_json`] output.
    ///
    /// # Errors
    ///
    /// If `record_id` is absent, or `nature` is not one of the enum's values.
    pub fn from_json(data: &Value) -> Result<Self, ModelError> {
        Ok(RetractionNotice {
            record_id: required_str(data, "record_id")?,
            nature: required_str(data, "nature")?.parse::<RetractionNature>()?,
            doi: get_str(data, "doi"),
            pmid: get_str(data, "pmid"),
            notice_doi: get_str(data, "notice_doi"),
            notice_pmid: get_str(data, "notice_pmid"),
            title: get_str(data, "title"),
            journal: get_str(data, "journal"),
            retraction_date: get_str(data, "retraction_date"),
            original_paper_date: get_str(data, "original_paper_date"),
            reasons: get_str_list(data, "reasons"),
            raw_nature: get_str(data, "raw_nature"),
        })
    }
}
