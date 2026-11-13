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

//! Source kinds, canonical identifiers and installations.

use super::error::LibraryError;
use super::MAX_LABEL_CHARS;
use crate::publications::storage::normalize_doi;

/// What a stored source file is. The names are the hand-off format's.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum SourceKind {
    /// A PDF.
    Pdf,
    /// JATS XML.
    Jats,
    /// An HTML page.
    Html,
    /// Markdown.
    Markdown,
    /// Plain text.
    Text,
}

impl SourceKind {
    /// The name used in records and hand-off files.
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Pdf => "pdf",
            Self::Jats => "jats",
            Self::Html => "html",
            Self::Markdown => "markdown",
            Self::Text => "text",
        }
    }

    /// The file extension, without a dot.
    #[must_use]
    pub fn extension(self) -> &'static str {
        match self {
            Self::Pdf => "pdf",
            Self::Jats => "xml",
            Self::Html => "html",
            Self::Markdown => "md",
            Self::Text => "txt",
        }
    }

    /// The kind named `name` (exactly, lower-case), or `None`.
    #[must_use]
    pub fn parse(name: &str) -> Option<Self> {
        [
            Self::Pdf,
            Self::Jats,
            Self::Html,
            Self::Markdown,
            Self::Text,
        ]
        .into_iter()
        .find(|k| k.as_str() == name)
    }
}

/// A paper's identifiers, each in canonical form or absent.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct PaperIds {
    /// PubMed ID: digits without leading zeros, or `PPR` and digits.
    pub pmid: Option<String>,
    /// DOI: lower-case, starting `10.` and holding a `/`.
    pub doi: Option<String>,
    /// PubMed Central ID: `PMC` and digits without leading zeros.
    pub pmcid: Option<String>,
}

/// Strips leading zeros from ASCII digits; `None` when there are none or all are zeros.
fn digits_without_zeros(s: &str) -> Option<&str> {
    if s.is_empty() || !s.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    let t = s.trim_start_matches('0');
    (!t.is_empty()).then_some(t)
}

/// [`LibraryError::BadIdentifier`] for `field`, quoting the raw `value` refused.
fn bad(field: &'static str, value: &str) -> LibraryError {
    LibraryError::BadIdentifier {
        field,
        value: value.to_owned(),
    }
}

/// A PMID in canonical form: ASCII digits without leading zeros, or a preprint's
/// `PPR` (any case in, upper case out) and digits. Surrounding space is trimmed.
fn canonical_pmid(raw: &str) -> Result<String, LibraryError> {
    let s = raw.trim();
    if let Some(rest) = s
        .get(..3)
        .filter(|p| p.eq_ignore_ascii_case("ppr"))
        .map(|_| &s[3..])
    {
        if !rest.is_empty() && rest.bytes().all(|b| b.is_ascii_digit()) {
            return Ok(format!("PPR{rest}"));
        }
        return Err(bad("pmid", raw));
    }
    digits_without_zeros(s)
        .map(str::to_owned)
        .ok_or_else(|| bad("pmid", raw))
}

/// A PMCID in canonical form, `PMC` and digits without leading zeros; the `PMC`
/// prefix may be missing or in any case. Surrounding space is trimmed.
fn canonical_pmcid(raw: &str) -> Result<String, LibraryError> {
    let s = raw.trim();
    let digits = match s.get(..3) {
        Some(p) if p.eq_ignore_ascii_case("pmc") => &s[3..],
        _ => s,
    };
    digits_without_zeros(digits)
        .map(|d| format!("PMC{d}"))
        .ok_or_else(|| bad("pmcid", raw))
}

/// A DOI as publications' `normalize_doi` gives it (lower case, no resolver
/// prefix), which must then start `10.` and hold a `/`; `None` when it is blank.
fn canonical_doi(raw: &str) -> Result<Option<String>, LibraryError> {
    match normalize_doi(Some(raw)) {
        None => Ok(None),
        Some(d) if d.starts_with("10.") && d.contains('/') => Ok(Some(d)),
        Some(_) => Err(bad("doi", raw)),
    }
}

impl PaperIds {
    /// Canonical identifiers from raw ones; blank values become `None`.
    ///
    /// # Errors
    /// [`LibraryError::BadIdentifier`] naming the first field that is not valid.
    pub fn canonical(
        pmid: Option<&str>,
        doi: Option<&str>,
        pmcid: Option<&str>,
    ) -> Result<Self, LibraryError> {
        /// `v` unless it is absent or only whitespace.
        fn present(v: Option<&str>) -> Option<&str> {
            v.filter(|s| !s.trim().is_empty())
        }
        Ok(Self {
            pmid: present(pmid).map(canonical_pmid).transpose()?,
            doi: present(doi).map(canonical_doi).transpose()?.flatten(),
            pmcid: present(pmcid).map(canonical_pmcid).transpose()?,
        })
    }

    /// True when no identifier is known.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.pmid.is_none() && self.doi.is_none() && self.pmcid.is_none()
    }

    /// True when both hold the same value for at least one identifier.
    #[must_use]
    pub fn shares_any(&self, other: &PaperIds) -> bool {
        let same = |a: &Option<String>, b: &Option<String>| a.is_some() && a == b;
        same(&self.pmid, &other.pmid)
            || same(&self.doi, &other.doi)
            || same(&self.pmcid, &other.pmcid)
    }

    /// `self` with its gaps filled from `other`. Where both hold different
    /// values `self`'s is kept and the field's name is in the second item.
    #[must_use]
    pub fn merged(&self, other: &PaperIds) -> (PaperIds, Vec<&'static str>) {
        let mut conflicts = Vec::new();
        let mut pick = |field: &'static str, a: &Option<String>, b: &Option<String>| match (a, b) {
            (Some(x), Some(y)) if x != y => {
                conflicts.push(field);
                a.clone()
            }
            (None, _) => b.clone(),
            _ => a.clone(),
        };
        let ids = PaperIds {
            pmid: pick("pmid", &self.pmid, &other.pmid),
            doi: pick("doi", &self.doi, &other.doi),
            pmcid: pick("pmcid", &self.pmcid, &other.pmcid),
        };
        (ids, conflicts)
    }
}

/// What a folder name is built from.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct PaperInfo {
    /// The paper's identifiers.
    pub ids: PaperIds,
    /// Its title.
    pub title: Option<String>,
    /// Its publication year.
    pub year: Option<i32>,
    /// Its first author's name.
    pub first_author: Option<String>,
}

/// One app on one machine, which writes its own record per paper.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Installation {
    /// The app, `[a-z0-9]{1,40}`.
    pub app: String,
    /// This installation, `[a-z0-9-]{1,64}`.
    pub id: String,
    /// A name a person recognises, such as the machine's.
    pub label: String,
}

/// True for the bidirectional control characters that could disguise a label.
pub(super) fn is_bidi_control(c: char) -> bool {
    matches!(c, '\u{200E}' | '\u{200F}' | '\u{202A}'..='\u{202E}' | '\u{2066}'..='\u{2069}')
}

/// [`LibraryError::BadInstallation`] for `field`, saying why.
fn bad_inst(field: &'static str, reason: &str) -> LibraryError {
    LibraryError::BadInstallation {
        field,
        reason: reason.to_owned(),
    }
}

impl Installation {
    /// A validated installation.
    ///
    /// # Errors
    /// [`LibraryError::BadInstallation`] naming the field that is not acceptable.
    pub fn new(app: &str, id: &str, label: &str) -> Result<Self, LibraryError> {
        let ok = |s: &str, max: usize, hyphen: bool| {
            (1..=max).contains(&s.len())
                && s.bytes()
                    .all(|b| b.is_ascii_digit() || b.is_ascii_lowercase() || (hyphen && b == b'-'))
        };
        if !ok(app, 40, false) {
            return Err(bad_inst("app", "1-40 characters of a-z and 0-9"));
        }
        if !ok(id, 64, true) {
            return Err(bad_inst("id", "1-64 characters of a-z, 0-9 and -"));
        }
        let n = label.chars().count();
        if n == 0 || n > MAX_LABEL_CHARS {
            return Err(bad_inst("label", "must be 1 to 200 characters"));
        }
        if label.chars().any(|c| c.is_control() || is_bidi_control(c)) {
            return Err(bad_inst("label", "must not hold control characters"));
        }
        Ok(Self {
            app: app.to_owned(),
            id: id.to_owned(),
            label: label.to_owned(),
        })
    }

    /// The name of this installation's record file in a paper's folder.
    #[must_use]
    pub fn record_file_name(&self) -> String {
        format!("{}-{}.json", self.app, self.id)
    }
}
