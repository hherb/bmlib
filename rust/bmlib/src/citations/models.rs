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

//! Citation markers, styles, and document metadata.
//!
//! A port of `bmlib/citations/models.py`. Two behaviour changes are
//! inherited from the Python port rather than from upstream: ordinary field
//! equality on [`Citation`], and the author-string split in
//! [`DocumentMetadata::from_json`].
//!
//! # A defect this port fixes rather than reproduces
//!
//! Python's `get_first_author_surname()` reads `authors[0]` unfiltered, while
//! every reference path filters whitespace-only entries first. So a blank
//! first author renders a reference naming the *second* author and an inline
//! citation naming `Unknown` — see issue #296. That is the same
//! "blank entry" class the port already fixed for references, applied to one
//! path of four.
//!
//! Here the blank filter lives **on the type**, in [`DocumentMetadata::named_authors`],
//! and every consumer goes through it: references, inline citations and
//! [`DocumentMetadata::generate_label`]. Making it a method rather than a
//! private formatter helper is the point — the defect was one consumer
//! forgetting to call the helper.
//!
//! # Known limit, deliberately kept
//!
//! [`author_surname`] is a naive split: `"Firstname Surname"` yields the last
//! whitespace-separated word, so `author_surname("Jan van der Berg")` is
//! `"Berg"`. That is one of the five upstream-faithful oddities
//! `docs/DECISIONS.md` records as **kept**, pinned by a Python test naming it.
//! It is not a defect to fix.

use serde::{Deserialize, Serialize};

/// Supported citation formatting styles.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum CitationStyle {
    /// Numbered — the style medical journals use.
    Vancouver,
    /// Author–date.
    Apa,
    /// Author–date variant.
    Harvard,
    /// Author–date, humanities.
    Chicago,
}

/// Vancouver — the numbered style medical journals use.
pub const DEFAULT_CITATION_STYLE: CitationStyle = CitationStyle::Vancouver;

impl CitationStyle {
    /// The style's wire spelling, as Python's `StrEnum` value.
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            CitationStyle::Vancouver => "vancouver",
            CitationStyle::Apa => "apa",
            CitationStyle::Harvard => "harvard",
            CitationStyle::Chicago => "chicago",
        }
    }

    /// A one-line human-readable description.
    #[must_use]
    pub fn description(self) -> &'static str {
        match self {
            CitationStyle::Vancouver => "Vancouver (numbered, common in medical journals)",
            CitationStyle::Apa => "APA (Author-Date, common in psychology and social sciences)",
            CitationStyle::Harvard => "Harvard (Author-Date variant)",
            CitationStyle::Chicago => "Chicago (Author-Date, common in humanities)",
        }
    }

    /// All supported styles, in the order Python's registry holds them.
    #[must_use]
    pub fn all() -> [CitationStyle; 4] {
        [
            CitationStyle::Vancouver,
            CitationStyle::Apa,
            CitationStyle::Harvard,
            CitationStyle::Chicago,
        ]
    }
}

impl std::fmt::Display for CitationStyle {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.as_str())
    }
}

/// Extract the surname from one author name in either common format.
///
/// `"Surname, Firstname"` yields everything before the comma;
/// `"Firstname Surname"` yields the last whitespace-separated word — a naive,
/// upstream-faithful split (particles like `van der` are kept only in the
/// inverted format).
///
/// Returns `"Unknown"` for a blank name.
#[must_use]
pub fn author_surname(author: &str) -> String {
    let author = author.trim();
    if let Some((before, _)) = author.split_once(',') {
        return before.trim().to_string();
    }
    author
        .split_whitespace()
        .next_back()
        .map_or_else(|| "Unknown".to_string(), str::to_string)
}

/// A citation marker found in document text.
///
/// Unlike upstream, two citations compare equal only when *all* fields match —
/// upstream's equality by `document_id` alone made markers of one document at
/// different positions collapse in sets.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Citation {
    /// Database id of the cited document.
    pub document_id: i64,
    /// Human-readable label (e.g. `"Smith2023"`).
    pub label: String,
    /// Character offset of the marker in the source text.
    pub position: usize,
    /// The full marker text (e.g. `"[@id:12345:Smith2023]"`).
    pub text: String,
}

/// Bibliographic metadata for one cited document.
///
/// `authors` may arrive as a JSON array or as a single string; see
/// [`DocumentMetadata::from_json`].
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DocumentMetadata {
    /// Database id of the document.
    pub document_id: i64,
    /// Document title.
    pub title: String,
    /// Author names, each `"Surname, Firstname"` or `"Firstname Surname"`.
    #[serde(default)]
    pub authors: Vec<String>,
    /// Journal name.
    #[serde(default)]
    pub journal: Option<String>,
    /// Publication year.
    #[serde(default)]
    pub year: Option<i64>,
    /// PubMed id, if any.
    #[serde(default)]
    pub pmid: Option<String>,
    /// DOI, if any.
    #[serde(default)]
    pub doi: Option<String>,
    /// Journal volume.
    #[serde(default)]
    pub volume: Option<String>,
    /// Journal issue.
    #[serde(default)]
    pub issue: Option<String>,
    /// Page range (e.g. `"123-134"`).
    #[serde(default)]
    pub pages: Option<String>,
    /// Full publication date as text.
    #[serde(default)]
    pub publication_date: Option<String>,
}

impl DocumentMetadata {
    /// Build metadata with only the two required fields set.
    #[must_use]
    pub fn new(document_id: i64, title: impl Into<String>) -> Self {
        DocumentMetadata {
            document_id,
            title: title.into(),
            authors: Vec::new(),
            journal: None,
            year: None,
            pmid: None,
            doi: None,
            volume: None,
            issue: None,
            pages: None,
            publication_date: None,
        }
    }

    /// Authors excluding whitespace-only entries — **a blank string is no
    /// author**.
    ///
    /// Every consumer of `authors` goes through this: the four reference
    /// formatters, the four inline formatters, and [`Self::generate_label`].
    /// Python applied the equivalent filter in a private formatter helper, so
    /// the inline paths and `generate_label` missed it (issue #296).
    #[must_use]
    pub fn named_authors(&self) -> Vec<&str> {
        self.authors
            .iter()
            .map(String::as_str)
            .filter(|a| !a.trim().is_empty())
            .collect()
    }

    /// The first author's surname, or `"Unknown"` without a named author.
    #[must_use]
    pub fn first_author_surname(&self) -> String {
        self.named_authors()
            .first()
            .map_or_else(|| "Unknown".to_string(), |a| author_surname(a))
    }

    /// How many authors a citation should count, blanks excluded.
    #[must_use]
    pub fn author_count(&self) -> usize {
        self.named_authors().len()
    }

    /// A citation label like `"Smith2023"` (`"Smithn.d."` without a year).
    #[must_use]
    pub fn generate_label(&self) -> String {
        let year = self
            .year
            .map_or_else(|| "n.d.".to_string(), |y| y.to_string());
        format!("{}{}", self.first_author_surname(), year)
    }

    /// Deserialise from a plain JSON object.
    ///
    /// `authors` may be a list or a single string. A string splits on `";"`
    /// when one is present, else on `","` — semicolons are how inverted names
    /// (`"Smith, John; Doe, Jane"`) stay whole, which upstream broke by
    /// treating both separators alike.
    ///
    /// `id` is accepted as an alias for `document_id`, matching Python's
    /// `data.get("id") or data.get("document_id", 0)`.
    ///
    /// # Errors
    ///
    /// If the value is not a JSON object, or fails to deserialise.
    pub fn from_json(value: &serde_json::Value) -> Result<Self, serde_json::Error> {
        let mut obj = value.clone();
        if let Some(map) = obj.as_object_mut() {
            if !map.contains_key("document_id") {
                if let Some(id) = map.remove("id") {
                    map.insert("document_id".to_string(), id);
                }
            }
            // Python coerces a non-empty pmid to str and empties to None.
            if let Some(pmid) = map.get("pmid") {
                if pmid.is_null() {
                    map.remove("pmid");
                } else if !pmid.is_string() {
                    let s = pmid.to_string();
                    map.insert("pmid".to_string(), serde_json::Value::String(s));
                }
            }
            if let Some(authors) = map.get("authors").cloned() {
                if let Some(text) = authors.as_str() {
                    let separator = if text.contains(';') { ';' } else { ',' };
                    let list: Vec<serde_json::Value> = text
                        .split(separator)
                        .map(str::trim)
                        .filter(|a| !a.is_empty())
                        .map(|a| serde_json::Value::String(a.to_string()))
                        .collect();
                    map.insert("authors".to_string(), serde_json::Value::Array(list));
                }
            }
            if map.get("title").is_none() {
                map.insert(
                    "title".to_string(),
                    serde_json::Value::String(String::new()),
                );
            }
            if map.get("document_id").is_none() {
                map.insert("document_id".to_string(), serde_json::Value::from(0));
            }
        }
        serde_json::from_value(obj)
    }

    /// Serialise to a plain JSON object.
    ///
    /// # Errors
    ///
    /// If the value cannot be serialised, which for these field types cannot
    /// happen — the signature exists so callers need not unwrap.
    pub fn to_json(&self) -> Result<serde_json::Value, serde_json::Error> {
        serde_json::to_value(self)
    }
}

/// One formatted bibliography entry.
#[derive(Debug, Clone, PartialEq)]
pub struct FormattedReference {
    /// Sequential reference number (1-based, order of first appearance).
    pub number: usize,
    /// Database id of the referenced document.
    pub document_id: i64,
    /// The full formatted bibliographic entry.
    pub formatted_text: String,
    /// The source metadata, or `None` for a placeholder entry whose document
    /// the caller could not supply.
    pub metadata: Option<DocumentMetadata>,
}

impl FormattedReference {
    /// Serialise to a plain JSON object, metadata nested or `null`.
    ///
    /// # Errors
    ///
    /// If the value cannot be serialised, which for these field types cannot
    /// happen.
    pub fn to_json(&self) -> Result<serde_json::Value, serde_json::Error> {
        Ok(serde_json::json!({
            "number": self.number,
            "document_id": self.document_id,
            "formatted_text": self.formatted_text,
            "metadata": match &self.metadata {
                Some(m) => m.to_json()?,
                None => serde_json::Value::Null,
            },
        }))
    }

    /// Deserialise from [`Self::to_json`] output.
    ///
    /// # Errors
    ///
    /// If a required field is absent or of the wrong type.
    pub fn from_json(value: &serde_json::Value) -> Result<Self, serde_json::Error> {
        let number = value
            .get("number")
            .and_then(serde_json::Value::as_u64)
            .unwrap_or(0) as usize;
        let document_id = value
            .get("document_id")
            .and_then(serde_json::Value::as_i64)
            .unwrap_or(0);
        let formatted_text = value
            .get("formatted_text")
            .and_then(serde_json::Value::as_str)
            .unwrap_or_default()
            .to_string();
        let metadata = match value.get("metadata") {
            Some(serde_json::Value::Null) | None => None,
            Some(m) => Some(DocumentMetadata::from_json(m)?),
        };
        Ok(FormattedReference {
            number,
            document_id,
            formatted_text,
            metadata,
        })
    }
}
