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

//! One Europe PMC search record as a [`FetchedRecord`] — port of
//! `bmlib/publications/fetchers/europepmc.py`.
//!
//! Not a sync fetcher: [`crate::publications::lookup::lookup_record`] asks Europe
//! PMC for a preprint accession (`PPR` + digits), which neither PubMed nor
//! OpenAlex knows, and this reads the `resultType=core` hit it gets back. Every
//! field is read type-checked, so a value of the wrong type costs that field and
//! not the record.

use std::sync::LazyLock;

use regex::{Captures, Regex};
use serde_json::{json, Value};

use crate::publications::fetchers::pubmed::escape_markdown;
use crate::publications::models::FetchedRecord;

/// Restated, as `transparency` and `fulltext` each keep their own literal.
pub const EUROPEPMC_REST_BASE: &str = "https://www.ebi.ac.uk/europepmc/webservices/rest";

// `fullTextXML` addresses a PMC or PPR accession, never a PMID (#188).
static PPR_ID: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^PPR[0-9]+$").expect("valid"));
static PMC_ID: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^PMC[0-9]+$").expect("valid"));

// The abstract is JATS-like markup that is not XML — the measured record
// carries a raw `&` — so tags are stripped by rule rather than parsed.
static BLOCK_TAG: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"</?(?:title|p)\b[^>]*>").expect("valid"));
static ANY_TAG: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"<[^>]*>").expect("valid"));
static ENTITY: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"&(lt|gt|amp|quot|apos|#[0-9]+|#x[0-9A-Fa-f]+);").expect("valid"));
// ASCII whitespace only, as the Python's: its `\s` and `str.strip` also take
// U+001C-U+001F, which Rust's do not (#557's divergence).
const ASCII_SPACE: &[char] = &[' ', '\t', '\n', '\r', '\x0C', '\x0B'];
static SPACE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[ \t\n\r\x0C\x0B]+").expect("valid"));

fn text(value: &Value, key: &str) -> Option<String> {
    value
        .get(key)
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
}

fn object<'a>(value: &'a Value, key: &str) -> Option<&'a Value> {
    value.get(key).filter(|v| v.is_object())
}

fn list<'a>(value: Option<&'a Value>, key: &str) -> &'a [Value] {
    value
        .and_then(|v| v.get(key))
        .and_then(Value::as_array)
        .map_or(&[], Vec::as_slice)
}

/// One XML entity or character reference, decoded; an unusable one kept.
fn entity(caps: &Captures<'_>) -> String {
    let name = &caps[1];
    let named = match name {
        "lt" => Some('<'),
        "gt" => Some('>'),
        "amp" => Some('&'),
        "quot" => Some('"'),
        "apos" => Some('\''),
        _ => None,
    };
    let decoded = named.or_else(|| {
        let code = match name.strip_prefix("#x") {
            Some(hex) => u32::from_str_radix(hex, 16).ok(),
            None => name[1..].parse::<u32>().ok(),
        };
        // A surrogate or a value past U+10FFFF names no character.
        code.and_then(char::from_u32)
    });
    decoded.map_or_else(|| caps[0].to_string(), String::from)
}

/// Europe PMC's `abstractText` as Markdown paragraphs, or `None`: each
/// `<title>` and `<p>` a paragraph, every other tag removed, the five XML
/// entities and numeric references decoded (any other entity kept), whitespace
/// collapsed and the prose escaped as the PubMed parser escapes it.
fn abstract_text(raw: &str) -> Option<String> {
    let blocks: Vec<String> = BLOCK_TAG
        .split(raw)
        .filter_map(|block| {
            let untagged = ANY_TAG.replace_all(block, "");
            let decoded = ENTITY.replace_all(&untagged, |caps: &Captures<'_>| entity(caps));
            let collapsed = SPACE.replace_all(&decoded, " ");
            let plain = collapsed.trim_matches(ASCII_SPACE);
            (!plain.is_empty()).then(|| escape_markdown(plain))
        })
        .collect();
    (!blocks.is_empty()).then(|| blocks.join("\n\n"))
}

/// `"Last, First"` as the PubMed parser writes it, else the full name.
fn author(value: &Value) -> Option<String> {
    let last = text(value, "lastName");
    match (last, text(value, "firstName")) {
        (Some(last), Some(first)) => Some(format!("{last}, {first}")),
        (last, _) => text(value, "fullName").or(last),
    }
}

/// Convert one Europe PMC `resultType=core` search hit to a record, or `None`
/// when the hit is not a JSON object.
///
/// `source="europepmc"`. A `PPR` accession is kept in `extras["ppr_id"]`, and a
/// record Europe PMC holds the text of (`inEPMC` `"Y"`) gains an `xml` source
/// for its `fullTextXML`, addressed by its PMC ID, else its `PPR` accession —
/// the only way `FullTextService` reaches a preprint's text.
#[must_use]
pub fn normalize(raw: &Value) -> Option<FetchedRecord> {
    raw.as_object()?;
    let ppr_id = text(raw, "id").filter(|id| PPR_ID.is_match(id));
    let pmc_id = text(raw, "pmcid").filter(|id| PMC_ID.is_match(id));
    let is_open_access = text(raw, "isOpenAccess").as_deref() == Some("Y");

    let mut record = FetchedRecord::new(text(raw, "title").unwrap_or_default(), "europepmc");
    if let Some(address) = pmc_id.as_ref().or(ppr_id.as_ref()) {
        if text(raw, "inEPMC").as_deref() == Some("Y") {
            record.fulltext_sources.push(json!({
                "url": format!("{EUROPEPMC_REST_BASE}/{address}/fullTextXML"),
                "format": "xml",
                "source": "europepmc",
                "open_access": is_open_access,
            }));
        }
    }
    record.authors = list(object(raw, "authorList"), "author")
        .iter()
        .filter_map(author)
        .collect();
    record.journal = object(raw, "journalInfo")
        .and_then(|info| object(info, "journal"))
        .and_then(|journal| text(journal, "title"))
        .or_else(|| object(raw, "bookOrReportDetails").and_then(|d| text(d, "publisher")));
    record.publication_types = list(object(raw, "pubTypeList"), "pubType")
        .iter()
        .filter_map(Value::as_str)
        .filter(|t| !t.is_empty())
        .map(str::to_string)
        .collect();
    record.doi = text(raw, "doi");
    // Held to a caller's PMID rule (#470): it enters a lookup's record
    // whenever PubMed supplied none.
    record.pmid = text(raw, "pmid")
        .filter(|p| p.bytes().all(|b| b.is_ascii_digit()))
        .map(|p| p.trim_start_matches('0').to_string())
        .filter(|p| !p.is_empty());
    record.pmc_id = pmc_id;
    record.abstract_text = raw
        .get("abstractText")
        .and_then(Value::as_str)
        .and_then(abstract_text);
    record.publication_date = text(raw, "firstPublicationDate");
    record.is_open_access = is_open_access;
    record.license = text(raw, "license");
    if let Some(ppr_id) = ppr_id {
        record.extras.insert("ppr_id".to_string(), json!(ppr_id));
    }
    Some(record)
}
