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

//! Bibliographic reference and inline-citation formatters.
//!
//! A port of `bmlib/citations/formatter.py`. Four styles: Vancouver
//! (numbered — the default for medical journals), APA, Harvard, and Chicago
//! (author–date). Output is preserved exactly as upstream's **code** produced
//! it — upstream's docstring examples disagree with its code in places, and
//! `docs/DECISIONS.md` says the code wins — except the doubled terminal period
//! in APA/Chicago author blocks, which the Python port fixed.
//!
//! # Deliberately odd truncation, pinned by tests
//!
//! Each style truncates a long author list *differently*, and three of the
//! four look wrong until you know they are upstream-faithful:
//!
//! | Style | With more than six authors |
//! |---|---|
//! | Vancouver | six names, then `et al` |
//! | APA | six names, then `...`, then the **last** name |
//! | Harvard | six names, then `and et al` |
//! | Chicago | inverted first author, then five natural names, then `and et al` |
//!
//! These are in `docs/DECISIONS.md`'s upstream-faithful list (or pinned by
//! named Python tests: `test_eight_authors_elide_the_middle`,
//! `test_seven_authors_truncate_to_et_al`). They are **kept**, not tidied.
//!
//! # The defect this module's caller fixes
//!
//! Python's inline formatters branch on `len(metadata.authors)` — the raw list
//! — while the reference formatters filter blanks first, so a whitespace-only
//! author made the two disagree and rendered `(Unknown & Johnson, 2023)`
//! (issue #296). Here every count and surname comes from
//! [`DocumentMetadata::named_authors`], so the reference and the inline
//! citation cannot disagree about how many authors there are.

use crate::citations::models::{
    author_surname, CitationStyle, DocumentMetadata, FormattedReference,
};

/// Author-list length beyond which each style applies its truncation.
pub const MAX_AUTHORS_BEFORE_ET_AL: usize = 6;

/// Append the terminal period unless the block already ends with one.
fn terminated(author_block: &str) -> String {
    if author_block.ends_with('.') {
        author_block.to_string()
    } else {
        format!("{author_block}.")
    }
}

/// The italic markdown form of a journal name, or empty.
fn format_journal(journal: Option<&str>) -> String {
    match journal {
        Some(j) if !j.is_empty() => format!("*{j}*"),
        _ => String::new(),
    }
}

/// Title with a guaranteed trailing period, or `"Untitled"` when empty.
fn format_title(title: &str) -> String {
    if title.is_empty() {
        return "Untitled".to_string();
    }
    let title = title.trim();
    if title.ends_with('.') {
        title.to_string()
    } else {
        format!("{title}.")
    }
}

/// Author names, each `"Surname, Firstname"` or `"Firstname Surname"`.
///
/// A thin alias to the type's own filter, kept so the call sites below read
/// like the Python they came from.
fn named(authors: &[String]) -> Vec<&str> {
    authors
        .iter()
        .map(String::as_str)
        .filter(|a| !a.trim().is_empty())
        .collect()
}

/// The `"Surname Initials"` form Vancouver uses.
fn surname_and_initials_compact(author: &str) -> String {
    let author = author.trim();
    if let Some((surname, _, given)) = split_inverted(author) {
        let initials: String = given
            .split_whitespace()
            .filter_map(|n| n.chars().next())
            .map(|c| c.to_ascii_uppercase())
            .collect();
        return format!("{} {}", surname.trim(), initials);
    }
    let parts: Vec<&str> = author.split_whitespace().collect();
    if parts.len() == 1 {
        return parts[0].to_string();
    }
    let initials: String = parts[..parts.len() - 1]
        .iter()
        .filter_map(|n| n.chars().next())
        .map(|c| c.to_ascii_uppercase())
        .collect();
    format!("{} {}", parts[parts.len() - 1], initials)
}

/// The `"Surname, I."` form APA uses.
fn surname_and_initials_dotted(author: &str) -> String {
    let author = author.trim();
    if let Some((surname, _, given)) = split_inverted(author) {
        let mut initials = given
            .split_whitespace()
            .filter_map(|n| n.chars().next())
            .map(|c| c.to_ascii_uppercase().to_string())
            .collect::<Vec<_>>()
            .join(". ");
        if !initials.is_empty() {
            initials.push('.');
        }
        return format!("{}, {}", surname.trim(), initials);
    }
    let parts: Vec<&str> = author.split_whitespace().collect();
    if parts.len() == 1 {
        return parts[0].to_string();
    }
    let mut initials = parts[..parts.len() - 1]
        .iter()
        .filter_map(|n| n.chars().next())
        .map(|c| c.to_ascii_uppercase().to_string())
        .collect::<Vec<_>>()
        .join(". ");
    if !initials.is_empty() {
        initials.push('.');
    }
    format!("{}, {}", parts[parts.len() - 1], initials)
}

/// The `"Surname, I."` form Harvard uses.
///
/// # The asymmetry that is easy to get wrong
///
/// Harvard's two branches build initials **the same way**, and a
/// transliteration that separates them is wrong in a way only the differential
/// oracle catches:
///
/// | Input | Branch | Result |
/// |---|---|---|
/// | `"Smith, John A."` | inverted | `"Smith, J.A."` |
/// | `"John A. Smith"` | natural | `"Smith, J.A."` |
/// | `"John Smith"` | natural | `"Smith, J."` |
///
/// So `"John A. Smith"` keeps the period of the given middle initial — it is
/// neither stripped nor doubled. Writing the natural branch as a `String`
/// collect (as a first cut here did) produces `JA.` and diverges from Python
/// on every author with a middle initial.
///
/// This differs from APA, whose natural branch joins with `". "` and so
/// yields `"Smith, J. A."`, and from Vancouver, which yields `"Smith JA"`.
fn surname_and_initials_run(author: &str) -> String {
    let author = author.trim();
    if let Some((surname, _, given)) = split_inverted(author) {
        let mut initials = given
            .split_whitespace()
            .filter_map(|n| n.chars().next())
            .map(|c| c.to_ascii_uppercase().to_string())
            .collect::<Vec<_>>()
            .join(".");
        if !initials.is_empty() {
            initials.push('.');
        }
        return format!("{}, {}", surname.trim(), initials);
    }
    let parts: Vec<&str> = author.split_whitespace().collect();
    if parts.len() == 1 {
        return parts[0].to_string();
    }
    // One `.` between initials, then one terminal `.`: `["J","A"]` → `"J.A."`,
    // `["J"]` → `"J."`.
    let mut initials = parts[..parts.len() - 1]
        .iter()
        .filter_map(|n| n.chars().next())
        .map(|c| c.to_ascii_uppercase().to_string())
        .collect::<Vec<_>>()
        .join(".");
    if !initials.is_empty() {
        initials.push('.');
    }
    format!("{}, {}", parts[parts.len() - 1], initials)
}

/// `"Surname, Firstname"` split when the name is inverted, else `None`.
fn split_inverted(author: &str) -> Option<(&str, char, &str)> {
    author
        .split_once(',')
        .map(|(surname, given)| (surname, ',', given))
}

/// Render one reference in one style.
///
/// A trait rather than a `match` so a new style is a new impl, which is how
/// Python's `BaseFormatter` subclasses read.
pub trait StyleFormatter {
    /// Format a full bibliographic reference.
    fn format_reference(&self, metadata: &DocumentMetadata, number: Option<usize>) -> String;

    /// Format an inline citation for running text.
    fn format_inline_citation(&self, metadata: &DocumentMetadata, number: Option<usize>) -> String;
}

/// Vancouver — numbered references, surname-plus-initials authors.
pub struct VancouverFormatter;

impl StyleFormatter for VancouverFormatter {
    fn format_reference(&self, m: &DocumentMetadata, number: Option<usize>) -> String {
        let mut parts: Vec<String> = Vec::new();
        if let Some(n) = number {
            parts.push(format!("{n}."));
        }
        parts.push(format_authors_vancouver(&m.authors));
        parts.push(format_title(&m.title));
        if let Some(journal) = m.journal.as_deref().filter(|j| !j.is_empty()) {
            let mut journal_part = format_journal(Some(journal));
            let mut year_and_volume: Vec<String> = Vec::new();
            if let Some(year) = m.year.filter(|y| *y != 0) {
                year_and_volume.push(year.to_string());
            }
            if let Some(volume) = m.volume.as_deref() {
                let mut volume = volume.to_string();
                if let Some(issue) = m.issue.as_deref() {
                    volume.push_str(&format!("({issue})"));
                }
                year_and_volume.push(volume);
            }
            if !year_and_volume.is_empty() {
                journal_part.push_str(&format!(". {}", year_and_volume.join(";")));
            }
            if let Some(pages) = m.pages.as_deref() {
                journal_part.push_str(&format!(":{pages}"));
            }
            parts.push(format!("{journal_part}."));
        }
        if let Some(doi) = m.doi.as_deref() {
            parts.push(format!("doi:{doi}"));
        } else if let Some(pmid) = m.pmid.as_deref() {
            parts.push(format!("PMID:{pmid}"));
        }
        parts.join(" ")
    }

    fn format_inline_citation(&self, m: &DocumentMetadata, number: Option<usize>) -> String {
        match number {
            Some(n) => format!("[{n}]"),
            None => format!("[{}]", m.document_id),
        }
    }
}

fn format_authors_vancouver(authors: &[String]) -> String {
    let authors = named(authors);
    if authors.is_empty() {
        return "Unknown author.".to_string();
    }
    let mut formatted: Vec<String> = Vec::new();
    for (i, author) in authors.iter().enumerate() {
        if i >= MAX_AUTHORS_BEFORE_ET_AL {
            formatted.push("et al".to_string());
            break;
        }
        formatted.push(surname_and_initials_compact(author));
    }
    format!("{}.", formatted.join(", "))
}

/// APA style — author–date, `Surname, I.` authors.
pub struct ApaFormatter;

impl StyleFormatter for ApaFormatter {
    fn format_reference(&self, m: &DocumentMetadata, _number: Option<usize>) -> String {
        let mut parts: Vec<String> = vec![format_authors_apa(&m.authors)];
        parts.push(match m.year {
            Some(y) => format!("({y})"),
            None => "(n.d.)".to_string(),
        });
        parts.push(format_title(&m.title));
        if let Some(journal) = m.journal.as_deref().filter(|j| !j.is_empty()) {
            let mut journal_part = format_journal(Some(journal));
            if let Some(volume) = m.volume.as_deref() {
                journal_part.push_str(&format!(", *{volume}*"));
                if let Some(issue) = m.issue.as_deref() {
                    journal_part.push_str(&format!("({issue})"));
                }
            }
            if let Some(pages) = m.pages.as_deref() {
                journal_part.push_str(&format!(", {pages}"));
            }
            parts.push(format!("{journal_part}."));
        }
        if let Some(doi) = m.doi.as_deref() {
            parts.push(format!("https://doi.org/{doi}"));
        }
        parts.join(" ")
    }

    fn format_inline_citation(&self, m: &DocumentMetadata, _number: Option<usize>) -> String {
        let surname = m.first_author_surname();
        let year = m.year.map_or_else(|| "n.d.".to_string(), |y| y.to_string());
        let authors = m.named_authors();
        if authors.len() > 2 {
            return format!("({surname} et al., {year})");
        }
        if authors.len() == 2 {
            return format!("({surname} & {}, {year})", author_surname(authors[1]));
        }
        format!("({surname}, {year})")
    }
}

fn format_authors_apa(authors: &[String]) -> String {
    let authors = named(authors);
    if authors.is_empty() {
        return "Unknown author.".to_string();
    }
    let mut formatted: Vec<String> = Vec::new();
    for (i, author) in authors.iter().enumerate() {
        // Upstream guarded `i >= MAX + 1: break` before the `i == MAX`
        // ellipsis branch, which always broke first; `>` here is the same
        // behaviour for every input length.
        if i > MAX_AUTHORS_BEFORE_ET_AL {
            break;
        }
        if i == MAX_AUTHORS_BEFORE_ET_AL {
            formatted.push("...".to_string());
            formatted.push(surname_and_initials_dotted(authors[authors.len() - 1]));
            break;
        }
        formatted.push(surname_and_initials_dotted(author));
    }
    if formatted.len() == 1 {
        return terminated(&formatted[0]);
    }
    if formatted.len() == 2 {
        return terminated(&format!("{} & {}", formatted[0], formatted[1]));
    }
    let head = formatted[..formatted.len() - 1].join(", ");
    terminated(&format!("{head}, & {}", formatted[formatted.len() - 1]))
}

/// Harvard style — author–date, quoted title, `pp.` pages.
pub struct HarvardFormatter;

impl StyleFormatter for HarvardFormatter {
    fn format_reference(&self, m: &DocumentMetadata, _number: Option<usize>) -> String {
        let mut parts: Vec<String> = vec![format_authors_harvard(&m.authors)];
        parts.push(match m.year {
            Some(y) => format!("({y})"),
            None => "(n.d.)".to_string(),
        });
        let mut title = m.title.trim().to_string();
        if title.ends_with('.') {
            title.pop();
        }
        parts.push(format!("'{title}',"));
        if let Some(journal) = m.journal.as_deref().filter(|j| !j.is_empty()) {
            let mut journal_part = format_journal(Some(journal));
            if let Some(volume) = m.volume.as_deref() {
                journal_part.push_str(&format!(", {volume}"));
                if let Some(issue) = m.issue.as_deref() {
                    journal_part.push_str(&format!("({issue})"));
                }
            }
            if let Some(pages) = m.pages.as_deref() {
                journal_part.push_str(&format!(", pp. {pages}"));
            }
            parts.push(format!("{journal_part}."));
        }
        if let Some(doi) = m.doi.as_deref() {
            parts.push(format!("doi: {doi}."));
        }
        parts.join(" ")
    }

    fn format_inline_citation(&self, m: &DocumentMetadata, _number: Option<usize>) -> String {
        let surname = m.first_author_surname();
        let year = m.year.map_or_else(|| "n.d.".to_string(), |y| y.to_string());
        let authors = m.named_authors();
        if authors.len() > 2 {
            return format!("({surname} et al., {year})");
        }
        if authors.len() == 2 {
            return format!("({surname} and {}, {year})", author_surname(authors[1]));
        }
        format!("({surname}, {year})")
    }
}

fn format_authors_harvard(authors: &[String]) -> String {
    let authors = named(authors);
    if authors.is_empty() {
        return "Unknown author".to_string();
    }
    let mut formatted: Vec<String> = Vec::new();
    for (i, author) in authors.iter().enumerate() {
        if i >= MAX_AUTHORS_BEFORE_ET_AL {
            formatted.push("et al.".to_string());
            break;
        }
        formatted.push(surname_and_initials_run(author));
    }
    if formatted.len() == 1 {
        return formatted[0].clone();
    }
    if formatted.len() == 2 {
        return format!("{} and {}", formatted[0], formatted[1]);
    }
    let head = formatted[..formatted.len() - 1].join(", ");
    format!("{head} and {}", formatted[formatted.len() - 1])
}

/// Chicago author–date style — first author inverted, title in quotes.
pub struct ChicagoFormatter;

impl StyleFormatter for ChicagoFormatter {
    fn format_reference(&self, m: &DocumentMetadata, _number: Option<usize>) -> String {
        let mut parts: Vec<String> = vec![format_authors_chicago(&m.authors)];
        parts.push(match m.year {
            Some(y) => format!("{y}."),
            None => "n.d.".to_string(),
        });
        let mut title = m.title.trim().to_string();
        if title.ends_with('.') {
            title.pop();
        }
        parts.push(format!("\"{title}.\""));
        if let Some(journal) = m.journal.as_deref().filter(|j| !j.is_empty()) {
            let mut journal_part = format_journal(Some(journal));
            if let Some(volume) = m.volume.as_deref() {
                journal_part.push_str(&format!(" {volume}"));
                if let Some(issue) = m.issue.as_deref() {
                    journal_part.push_str(&format!(" ({issue})"));
                }
            }
            if let Some(pages) = m.pages.as_deref() {
                journal_part.push_str(&format!(": {pages}"));
            }
            parts.push(format!("{journal_part}."));
        }
        if let Some(doi) = m.doi.as_deref() {
            parts.push(format!("https://doi.org/{doi}."));
        }
        parts.join(" ")
    }

    fn format_inline_citation(&self, m: &DocumentMetadata, _number: Option<usize>) -> String {
        let surname = m.first_author_surname();
        let year = m.year.map_or_else(|| "n.d.".to_string(), |y| y.to_string());
        let authors = m.named_authors();
        if authors.len() > 2 {
            return format!("({surname} et al. {year})");
        }
        if authors.len() == 2 {
            return format!("({surname} and {} {year})", author_surname(authors[1]));
        }
        format!("({surname} {year})")
    }
}

fn format_authors_chicago(authors: &[String]) -> String {
    let authors = named(authors);
    if authors.is_empty() {
        return "Unknown author.".to_string();
    }
    let mut formatted: Vec<String> = Vec::new();
    for (i, author) in authors.iter().enumerate() {
        if i >= MAX_AUTHORS_BEFORE_ET_AL {
            formatted.push("et al".to_string());
            break;
        }
        formatted.push(if i == 0 {
            inverted(author)
        } else {
            natural(author)
        });
    }
    if formatted.len() == 1 {
        return terminated(&formatted[0]);
    }
    if formatted.len() == 2 {
        return terminated(&format!("{}, and {}", formatted[0], formatted[1]));
    }
    let head = formatted[..formatted.len() - 1].join(", ");
    terminated(&format!("{head}, and {}", formatted[formatted.len() - 1]))
}

/// First author: `"Surname, Firstname"`.
fn inverted(author: &str) -> String {
    let author = author.trim();
    if author.contains(',') {
        return author.to_string();
    }
    let parts: Vec<&str> = author.split_whitespace().collect();
    if parts.len() == 1 {
        return parts[0].to_string();
    }
    format!(
        "{}, {}",
        parts[parts.len() - 1],
        parts[..parts.len() - 1].join(" ")
    )
}

/// Subsequent authors: `"Firstname Surname"`.
fn natural(author: &str) -> String {
    let author = author.trim();
    match split_inverted(author) {
        Some((surname, _, given)) => format!("{} {}", given.trim(), surname.trim())
            .trim()
            .to_string(),
        None => author.to_string(),
    }
}

/// Formats references and inline citations in a selectable style.
pub struct CitationFormatter {
    style: CitationStyle,
    inner: Box<dyn StyleFormatter>,
}

impl CitationFormatter {
    /// Create a formatter for `style`.
    #[must_use]
    pub fn new(style: CitationStyle) -> Self {
        CitationFormatter {
            style,
            inner: Self::make(style),
        }
    }

    fn make(style: CitationStyle) -> Box<dyn StyleFormatter> {
        match style {
            CitationStyle::Vancouver => Box::new(VancouverFormatter),
            CitationStyle::Apa => Box::new(ApaFormatter),
            CitationStyle::Harvard => Box::new(HarvardFormatter),
            CitationStyle::Chicago => Box::new(ChicagoFormatter),
        }
    }

    /// The active citation style.
    #[must_use]
    pub fn style(&self) -> CitationStyle {
        self.style
    }

    /// Switch style, replacing the inner formatter as Python's setter does.
    pub fn set_style(&mut self, style: CitationStyle) {
        self.style = style;
        self.inner = Self::make(style);
    }

    /// Format a full bibliographic reference in the active style.
    #[must_use]
    pub fn format_reference(&self, metadata: &DocumentMetadata, number: Option<usize>) -> String {
        self.inner.format_reference(metadata, number)
    }

    /// Format an inline citation in the active style.
    #[must_use]
    pub fn format_inline_citation(
        &self,
        metadata: &DocumentMetadata,
        number: Option<usize>,
    ) -> String {
        self.inner.format_inline_citation(metadata, number)
    }

    /// Render a complete markdown reference list.
    #[must_use]
    pub fn format_reference_list(&self, references: &[FormattedReference]) -> String {
        let mut lines: Vec<String> = vec![
            String::new(),
            "---".to_string(),
            String::new(),
            "## References".to_string(),
            String::new(),
        ];
        for reference in references {
            lines.push(reference.formatted_text.clone());
            lines.push(String::new());
        }
        lines.join("\n")
    }

    /// All supported citation styles.
    #[must_use]
    pub fn available_styles() -> Vec<CitationStyle> {
        CitationStyle::all().to_vec()
    }

    /// A one-line human-readable description of `style`.
    #[must_use]
    pub fn style_description(style: CitationStyle) -> &'static str {
        style.description()
    }
}

impl Default for CitationFormatter {
    fn default() -> Self {
        Self::new(crate::citations::models::DEFAULT_CITATION_STYLE)
    }
}
