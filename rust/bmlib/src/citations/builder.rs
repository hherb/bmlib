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

//! Build numbered reference lists from citation markers in text.
//!
//! A port of `bmlib/citations/builder.py`. Upstream's `ReferenceBuilder`
//! fetched document metadata from bmlibrarian's PostgreSQL `document` table;
//! the Python port took a `Mapping[int, DocumentMetadata]` and made every
//! function pure, and so does this. The author–date inline-citation fix
//! (upstream numbered every style) is inherited.
//!
//! # Example
//!
//! ```
//! use bmlib::citations::{build_references, CitationStyle, DocumentMetadata};
//! use std::collections::HashMap;
//!
//! let text = "Statins lower LDL [@id:1:Smith2021] [@id:2:Doe2022].";
//! let mut metadata = HashMap::new();
//! metadata.insert(1, DocumentMetadata::new(1, "A study"));
//! metadata.insert(2, DocumentMetadata::new(2, "Another study"));
//!
//! let (formatted, references) = build_references(text, &metadata, CitationStyle::Vancouver, true);
//! assert_eq!(formatted, "Statins lower LDL [1,2].");
//! assert_eq!(references.len(), 2);
//! ```

use std::collections::HashMap;

use crate::citations::formatter::CitationFormatter;
use crate::citations::models::{
    Citation, CitationStyle, DocumentMetadata, FormattedReference, DEFAULT_CITATION_STYLE,
};
use crate::citations::parser::{
    find_adjacent_citations, format_citation_group, marker_spans, parse_citations, scan_replace,
    unique_document_ids,
};

/// Number, format, and inline every citation in `text`.
///
/// Documents are numbered by order of first appearance. Each unique cited
/// document yields one [`FormattedReference`]; a document id missing from
/// `metadata` yields a visible `[Document N not found]` placeholder rather
/// than disappearing. Markers in the text are replaced with `[N]` (Vancouver,
/// adjacent markers combined to e.g. `[1-3]`) or with the style's author–date
/// inline citation (APA/Harvard/Chicago; a marker whose document is missing
/// stays verbatim, since an author–date citation needs the metadata's
/// surname).
#[must_use]
pub fn build_references(
    text: &str,
    metadata: &HashMap<i64, DocumentMetadata>,
    style: CitationStyle,
    combine_sequential: bool,
) -> (String, Vec<FormattedReference>) {
    let document_ids = unique_document_ids(text);
    if document_ids.is_empty() {
        return (text.to_string(), Vec::new());
    }

    let formatter = CitationFormatter::new(style);
    let mut id_to_number: HashMap<i64, usize> = HashMap::new();
    for (i, document_id) in document_ids.iter().enumerate() {
        id_to_number.insert(*document_id, i + 1);
    }

    let mut references = Vec::new();
    for document_id in &document_ids {
        let number = id_to_number[document_id];
        match metadata.get(document_id) {
            Some(document) => references.push(FormattedReference {
                number,
                document_id: *document_id,
                formatted_text: formatter.format_reference(document, Some(number)),
                metadata: Some(document.clone()),
            }),
            None => references.push(FormattedReference {
                number,
                document_id: *document_id,
                formatted_text: format!("{number}. [Document {document_id} not found]"),
                metadata: None,
            }),
        }
    }

    let replaced = replace_citations(
        text,
        metadata,
        &id_to_number,
        &formatter,
        combine_sequential,
    );
    (replaced, references)
}

/// Format `text` and, by default, append the markdown reference list.
#[must_use]
pub fn format_document(
    text: &str,
    metadata: &HashMap<i64, DocumentMetadata>,
    style: CitationStyle,
    include_reference_list: bool,
    combine_sequential: bool,
) -> String {
    let (mut formatted_text, references) =
        build_references(text, metadata, style, combine_sequential);
    if include_reference_list && !references.is_empty() {
        formatted_text.push_str(&CitationFormatter::new(style).format_reference_list(&references));
    }
    formatted_text
}

/// Citations in `text` whose document id has no entry in `metadata`.
///
/// One [`Citation`] per marker, so a document cited twice is reported twice,
/// each with its own position.
#[must_use]
pub fn find_missing_documents(
    text: &str,
    metadata: &HashMap<i64, DocumentMetadata>,
) -> Vec<Citation> {
    parse_citations(text)
        .into_iter()
        .filter(|c| !metadata.contains_key(&c.document_id))
        .collect()
}

/// [`build_references`] with the default style.
#[must_use]
pub fn build_references_default(
    text: &str,
    metadata: &HashMap<i64, DocumentMetadata>,
) -> (String, Vec<FormattedReference>) {
    build_references(text, metadata, DEFAULT_CITATION_STYLE, true)
}

/// Replace markers per the style: numbered groups or author–date.
fn replace_citations(
    text: &str,
    metadata: &HashMap<i64, DocumentMetadata>,
    id_to_number: &HashMap<i64, usize>,
    formatter: &CitationFormatter,
    combine_sequential: bool,
) -> String {
    if formatter.style() == CitationStyle::Vancouver {
        let groups = find_adjacent_citations(text);
        // Reverse order keeps the earlier groups' positions valid while later
        // spans are being replaced.
        let spans = marker_spans(text);
        let mut text = text.to_string();
        for group in groups.iter().rev() {
            let replacement = if group.len() == 1 {
                match id_to_number.get(&group[0].document_id) {
                    Some(n) => format!("[{n}]"),
                    None => group[0].text.clone(),
                }
            } else {
                format_citation_group(group, id_to_number, combine_sequential)
            };
            let start = group[0].position;
            let end = group[group.len() - 1].position + group[group.len() - 1].text.chars().count();
            let _ = &spans;
            text = replace_char_range(&text, start, end, &replacement);
        }
        return text;
    }

    scan_replace(
        text,
        &mut |document_id, marker| match metadata.get(&document_id) {
            Some(document) => formatter.format_inline_citation(document, None),
            None => marker.to_string(),
        },
    )
}

/// Replace the character range `[start, end)` of `text` with `replacement`.
fn replace_char_range(text: &str, start: usize, end: usize, replacement: &str) -> String {
    let chars: Vec<char> = text.chars().collect();
    let mut out = String::with_capacity(text.len() + replacement.len());
    out.extend(chars[..start].iter());
    out.push_str(replacement);
    out.extend(chars[end..].iter());
    out
}
