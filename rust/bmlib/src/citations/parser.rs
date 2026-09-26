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

//! Pure functions over the `[@id:N:Label]` citation-marker format.
//!
//! A port of `bmlib/citations/parser.py`. The stateless class was dissolved
//! into module functions in the Python port; the validation fix (`fullmatch`
//! where upstream anchored only the start) is inherited.
//!
//! # Hand-rolled rather than regex
//!
//! The marker grammar is `[@id:` + digits + `:` + any run without `]` + `]`,
//! which Python spells as one regex. The dependency policy in the port plan
//! says to hand-roll what is small and bmlib's own, and this qualifies on both
//! counts: the scanner is about forty lines, it avoids a regex dependency in a
//! module that otherwise needs none, and it removes the question of whether a
//! crate's `\d` is Unicode-aware where Python's is (`\d` matches Arabic-Indic
//! digits, so a marker `[@id:١٢:x]` parses in Python). This scanner requires
//! ASCII digits, which is the documented grammar.
//!
//! **Positions are character offsets, not byte offsets**, matching Python's
//! `match.start()` on a `str`. A label carrying non-ASCII text would otherwise
//! report byte positions and [`citations_in_range`] would disagree with the
//! Python original.

use crate::citations::models::Citation;

/// The `[@id:` prefix every marker starts with.
const PREFIX: &str = "[@id:";

/// Maximum label length [`validate_marker`] accepts.
const MAX_LABEL_LENGTH: usize = 100;

/// One citation marker: `[@id:<digits>:<label without ']'>]`.
///
/// Exposed because Python exposes it as `CITATION_PATTERN`. In Rust it is a
/// plain string — the scanner is in [`parse_citations`] — but keeping the name
/// makes a mechanical port of a call site read the same.
pub const CITATION_PATTERN: &str = r"\[@id:(\d+):([^\]]+)\]";

/// A marker found at a character offset.
struct RawMatch {
    document_id: i64,
    label: String,
    position: usize,
    text: String,
}

/// Scan `chars` for the next marker at or after `start`.
///
/// The label must be non-empty, since Python's `[^\]]+` is one-or-more; a
/// marker with an empty label is not a marker.
fn scan(chars: &[char], start: usize) -> Option<RawMatch> {
    let prefix: Vec<char> = PREFIX.chars().collect();
    let mut i = start;
    while i + prefix.len() <= chars.len() {
        if chars[i] != '[' {
            i += 1;
            continue;
        }
        if chars[i..i + prefix.len()] != prefix[..] {
            i += 1;
            continue;
        }
        // Digits up to the separating colon.
        let digits_start = i + prefix.len();
        let mut j = digits_start;
        while j < chars.len() && chars[j].is_ascii_digit() {
            j += 1;
        }
        if j == digits_start || j >= chars.len() || chars[j] != ':' {
            i += 1;
            continue;
        }
        // Label runs to the first `]`, and must be non-empty.
        let label_start = j + 1;
        let mut k = label_start;
        while k < chars.len() && chars[k] != ']' {
            k += 1;
        }
        if k >= chars.len() || k == label_start {
            i += 1;
            continue;
        }
        let document_id: i64 = chars[digits_start..j]
            .iter()
            .collect::<String>()
            .parse()
            .unwrap_or(0);
        return Some(RawMatch {
            document_id,
            label: chars[label_start..k].iter().collect(),
            position: i,
            text: chars[i..=k].iter().collect(),
        });
    }
    None
}

fn char_vec(text: &str) -> Vec<char> {
    text.chars().collect()
}

/// Extract every citation marker from `text`, in order of appearance.
#[must_use]
pub fn parse_citations(text: &str) -> Vec<Citation> {
    let chars = char_vec(text);
    let mut out = Vec::new();
    let mut at = 0usize;
    while let Some(m) = scan(&chars, at) {
        let next = m.position + m.text.chars().count();
        out.push(Citation {
            document_id: m.document_id,
            label: m.label,
            position: m.position,
            text: m.text,
        });
        at = next;
    }
    out
}

/// Like [`scan`], but for internal callers wanting the raw match.
fn scan_all(text: &str) -> Vec<RawMatch> {
    let chars = char_vec(text);
    let mut out = Vec::new();
    let mut at = 0usize;
    while let Some(m) = scan(&chars, at) {
        let next = m.position + m.text.chars().count();
        out.push(m);
        at = next;
    }
    out
}

/// Unique cited document ids, in order of first appearance.
#[must_use]
pub fn unique_document_ids(text: &str) -> Vec<i64> {
    let mut seen = std::collections::HashSet::new();
    let mut ordered = Vec::new();
    for citation in parse_citations(text) {
        if seen.insert(citation.document_id) {
            ordered.push(citation.document_id);
        }
    }
    ordered
}

/// Number of citation markers in `text` (repeats count every time).
#[must_use]
pub fn count_citations(text: &str) -> usize {
    parse_citations(text).len()
}

/// Number of distinct documents cited in `text`.
#[must_use]
pub fn count_unique_citations(text: &str) -> usize {
    unique_document_ids(text).len()
}

/// Character positions of every marker, grouped by document id.
///
/// Insertion order follows first appearance. Python returns a `dict`; a
/// `BTreeMap` here would reorder by id, so this returns pairs and keeps the
/// documented order. Callers wanting a lookup can collect into a map.
#[must_use]
pub fn citation_positions(text: &str) -> Vec<(i64, Vec<usize>)> {
    let mut out: Vec<(i64, Vec<usize>)> = Vec::new();
    for citation in parse_citations(text) {
        match out.iter_mut().find(|(id, _)| *id == citation.document_id) {
            Some((_, positions)) => positions.push(citation.position),
            None => out.push((citation.document_id, vec![citation.position])),
        }
    }
    out
}

/// Citations whose marker starts in `[start, end)`.
#[must_use]
pub fn citations_in_range(text: &str, start: usize, end: usize) -> Vec<Citation> {
    parse_citations(text)
        .into_iter()
        .filter(|c| start <= c.position && c.position < end)
        .collect()
}

/// Build the marker string for a document id and label.
#[must_use]
pub fn create_citation_marker(document_id: i64, label: &str) -> String {
    format!("[@id:{document_id}:{label}]")
}

/// Replace every marker of one document with `[number]`.
#[must_use]
pub fn replace_citation_with_number(text: &str, document_id: i64, number: usize) -> String {
    let chars = char_vec(text);
    let mut out = String::with_capacity(text.len());
    let mut at = 0usize;
    while let Some(m) = scan(&chars, at) {
        // Copy the run before this marker verbatim.
        out.extend(chars[at..m.position].iter());
        if m.document_id == document_id {
            out.push_str(&format!("[{number}]"));
        } else {
            out.push_str(&m.text);
        }
        at = m.position + m.text.chars().count();
    }
    if at < chars.len() {
        out.extend(chars[at..].iter());
    }
    out
}

/// Replace each mapped marker with `[number]`; unmapped markers stay.
#[must_use]
pub fn replace_all_citations_with_numbers(
    text: &str,
    id_to_number: &std::collections::HashMap<i64, usize>,
) -> String {
    let chars = char_vec(text);
    let mut out = String::with_capacity(text.len());
    let mut at = 0usize;
    while let Some(m) = scan(&chars, at) {
        out.extend(chars[at..m.position].iter());
        match id_to_number.get(&m.document_id) {
            Some(number) => out.push_str(&format!("[{number}]")),
            None => out.push_str(&m.text),
        }
        at = m.position + m.text.chars().count();
    }
    if at < chars.len() {
        out.extend(chars[at..].iter());
    }
    out
}

/// Group markers separated only by whitespace and/or commas.
///
/// Adjacent groups are what a numbered style renders as a combined reference
/// such as `[1-3]`.
#[must_use]
pub fn find_adjacent_citations(text: &str) -> Vec<Vec<Citation>> {
    let citations = parse_citations(text);
    let Some(first) = citations.first() else {
        return Vec::new();
    };
    let chars = char_vec(text);
    let mut groups: Vec<Vec<Citation>> = Vec::new();
    let mut current: Vec<Citation> = vec![first.clone()];
    for pair in citations.windows(2) {
        let (previous, citation) = (&pair[0], &pair[1]);
        let gap_start = previous.position + previous.text.chars().count();
        let gap: String = chars[gap_start..citation.position].iter().collect();
        if gap.chars().all(|c| c.is_whitespace() || c == ',') {
            current.push(citation.clone());
        } else {
            groups.push(std::mem::take(&mut current));
            current = vec![citation.clone()];
        }
    }
    groups.push(current);
    groups
}

/// Format one adjacent group as `[1,2]`, `[1-3]`, or `[1-3,5]`.
///
/// Citations whose id has no number are skipped; an entirely unnumbered group
/// formats as the empty string.
#[must_use]
pub fn format_citation_group(
    citations: &[Citation],
    id_to_number: &std::collections::HashMap<i64, usize>,
    combine_sequential: bool,
) -> String {
    if citations.is_empty() {
        return String::new();
    }
    let mut numbers: Vec<usize> = citations
        .iter()
        .filter_map(|c| id_to_number.get(&c.document_id).copied())
        .collect();
    numbers.sort_unstable();
    numbers.dedup();
    if numbers.is_empty() {
        return String::new();
    }
    if !combine_sequential || numbers.len() <= 2 {
        let joined = numbers
            .iter()
            .map(usize::to_string)
            .collect::<Vec<_>>()
            .join(",");
        return format!("[{joined}]");
    }
    let mut runs: Vec<String> = Vec::new();
    let mut start = numbers[0];
    let mut end = numbers[0];
    for &number in &numbers[1..] {
        if number == end + 1 {
            end = number;
        } else {
            runs.push(format_run(start, end));
            start = number;
            end = number;
        }
    }
    runs.push(format_run(start, end));
    format!("[{}]", runs.join(","))
}

/// One maximal consecutive run: `"1-3"`, `"1,2"`, or `"1"`.
fn format_run(start: usize, end: usize) -> String {
    if end > start + 1 {
        return format!("{start}-{end}");
    }
    if end > start {
        return format!("{start},{end}");
    }
    start.to_string()
}

/// Check that `marker` is exactly one well-formed citation marker.
///
/// Returns `Ok(())`, or `Err(reason)`. The whole string must be the marker —
/// upstream anchored only the start, so trailing text validated.
///
/// # Errors
///
/// With the same message strings Python returns.
pub fn validate_marker(marker: &str) -> Result<(), String> {
    let chars = char_vec(marker);
    match scan(&chars, 0) {
        Some(m) if m.position == 0 && m.text.chars().count() == chars.len() => {
            if m.document_id <= 0 {
                return Err("Document ID must be a positive integer".to_string());
            }
            if m.label.chars().count() > MAX_LABEL_LENGTH {
                return Err(format!("Label must be 1-{MAX_LABEL_LENGTH} characters"));
            }
            Ok(())
        }
        _ => Err("Invalid citation format. Expected: [@id:NUMBER:LABEL]".to_string()),
    }
}

/// The label of a marker string, or `None` if it is not one marker.
#[must_use]
pub fn extract_label_from_citation(marker: &str) -> Option<String> {
    let chars = char_vec(marker);
    match scan(&chars, 0) {
        Some(m) if m.position == 0 && m.text.chars().count() == chars.len() => Some(m.label),
        _ => None,
    }
}

/// The document id of a marker string, or `None` if it is not one marker.
#[must_use]
pub fn extract_document_id_from_citation(marker: &str) -> Option<i64> {
    let chars = char_vec(marker);
    match scan(&chars, 0) {
        Some(m) if m.position == 0 && m.text.chars().count() == chars.len() => Some(m.document_id),
        _ => None,
    }
}

/// Every marker's raw span, for callers that need offsets rather than models.
#[must_use]
pub fn marker_spans(text: &str) -> Vec<(usize, usize)> {
    scan_all(text)
        .into_iter()
        .map(|m| (m.position, m.position + m.text.chars().count()))
        .collect()
}

/// Rewrite every marker in `text` through `f`.
///
/// `f` receives the marker's document id and its original text, so a caller
/// can keep the marker verbatim by returning its second argument. Runs of
/// non-marker text are copied unchanged, which is what makes this safe on text
/// carrying anything at all.
#[must_use]
pub fn scan_replace<F>(text: &str, f: &mut F) -> String
where
    F: FnMut(i64, &str) -> String,
{
    let chars = char_vec(text);
    let mut out = String::with_capacity(text.len());
    let mut at = 0usize;
    while let Some(m) = scan(&chars, at) {
        out.extend(chars[at..m.position].iter());
        out.push_str(&f(m.document_id, &m.text));
        at = m.position + m.text.chars().count();
    }
    if at < chars.len() {
        out.extend(chars[at..].iter());
    }
    out
}
