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

//! Reading a PRISMA 2020 part's reply, under the strict-evidence rule.
//!
//! The model judges each checklist item of a part and quotes the paper for it.
//! Every quote is located by [`verify_quotes`], and the judgement shown is the
//! model's only when a quote backs it: a claim of `reported`,
//! `partly_reported`, `reported_elsewhere` or `not_applicable` without a
//! located quote is shown as `not_reported` and flagged `unbacked`
//! ([`shown_judgement`]). The rule is deliberately one-way. A `not_reported`
//! item is never upgraded because a quote was located (PICO's #577 does
//! upgrade): a reviewer sees the model's own word beside the paper's span.
//!
//! A port of `bmlib/quality/prisma2020_models.py`.

use std::collections::HashMap;

use serde_json::{json, Value};

use super::cochrane_assessor::clamped_confidence;
use super::pico_agent::verify_quotes;
use super::pico_models::{PicoCitation, PicoContradictedQuote};
use super::prisma2020_checklist::item;

/// How fully the paper reports a checklist item (the reply's vocabulary).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Judgement {
    /// Fully reported.
    Reported,
    /// Partly reported.
    PartlyReported,
    /// Not reported.
    NotReported,
    /// Reported in another document (a protocol, a registry entry).
    ReportedElsewhere,
    /// The item does not apply to this review.
    NotApplicable,
}

impl Judgement {
    /// The reply's string for this judgement.
    pub fn as_str(self) -> &'static str {
        match self {
            Judgement::Reported => "reported",
            Judgement::PartlyReported => "partly_reported",
            Judgement::NotReported => "not_reported",
            Judgement::ReportedElsewhere => "reported_elsewhere",
            Judgement::NotApplicable => "not_applicable",
        }
    }

    /// The judgement a reply string names, or `None`.
    pub fn parse(value: &str) -> Option<Self> {
        [
            Judgement::Reported,
            Judgement::PartlyReported,
            Judgement::NotReported,
            Judgement::ReportedElsewhere,
            Judgement::NotApplicable,
        ]
        .into_iter()
        .find(|judgement| judgement.as_str() == value)
    }
}

/// One checklist item as shown: the model's claim, and what the evidence allows.
///
/// `model_judgement` is what the model said, `judgement` what is shown;
/// `unbacked` is true when they differ because no quote was located.
#[derive(Debug, Clone, PartialEq)]
pub struct ItemAssessment {
    /// The checklist id, from the checklist row.
    pub id: &'static str,
    /// The model's claim.
    pub model_judgement: Judgement,
    /// The judgement shown.
    pub judgement: Judgement,
    /// Whether the claim was shown as `not_reported` for want of a quote.
    pub unbacked: bool,
    /// The model's explanation.
    pub explanation: String,
    /// The paper's spans for the quotes found.
    pub citations: Vec<PicoCitation>,
    /// Quotes not found, verbatim.
    pub quotes_not_found: Vec<String>,
    /// Quotes the paper contradicts.
    pub quotes_contradicted: Vec<PicoContradictedQuote>,
    /// Quotes dropped as unusable.
    pub quotes_dropped: usize,
}

impl ItemAssessment {
    /// Python's `to_dict()`.
    pub fn to_json(&self) -> Value {
        json!({
            "id": self.id,
            "model_judgement": self.model_judgement.as_str(),
            "judgement": self.judgement.as_str(),
            "unbacked": self.unbacked,
            "explanation": self.explanation,
            "citations": self.citations.iter().map(PicoCitation::to_json).collect::<Vec<_>>(),
            "quotes_not_found": self.quotes_not_found,
            "quotes_contradicted": self
                .quotes_contradicted
                .iter()
                .map(PicoContradictedQuote::to_json)
                .collect::<Vec<_>>(),
            "quotes_dropped": self.quotes_dropped,
        })
    }
}

/// The items of one part, the asked ids the reply left out, and run notes.
///
/// `unasked` lists the ids the reply answered although they were not asked,
/// in the reply's order and as it spelled them (an integer read as its
/// digits); their answers are not read (#807).
#[derive(Debug, Clone, PartialEq, Default)]
pub struct PartAssessment {
    /// The items read, in the order asked.
    pub items: Vec<ItemAssessment>,
    /// Asked ids the reply did not contain.
    pub missing: Vec<&'static str>,
    /// Ids the reply answered that were not asked, in the reply's order.
    pub unasked: Vec<String>,
    /// The paper's length before condensing, when it was condensed.
    pub condensed_from_chars: Option<usize>,
    /// How condensing went.
    pub condensation_status: Option<String>,
    /// Notes for the reader.
    pub notes: Vec<String>,
}

impl PartAssessment {
    /// Python's `to_dict()`.
    pub fn to_json(&self) -> Value {
        json!({
            "items": self.items.iter().map(ItemAssessment::to_json).collect::<Vec<_>>(),
            "missing": self.missing,
            "unasked": self.unasked,
            "condensed_from_chars": self.condensed_from_chars,
            "condensation_status": self.condensation_status,
            "notes": self.notes,
        })
    }
}

/// Whether the paper is a systematic review or meta-analysis PRISMA applies to.
///
/// `variant` is the model's own string, kept as given.
#[derive(Debug, Clone, PartialEq)]
pub struct Suitability {
    /// The paper is a systematic review.
    pub is_systematic_review: bool,
    /// The paper is a meta-analysis.
    pub is_meta_analysis: bool,
    /// A systematic review or a meta-analysis.
    pub is_suitable: bool,
    /// The document type, as the model named it.
    pub document_type: String,
    /// The variant, as the model named it.
    pub variant: Option<String>,
    /// The model's explanation.
    pub rationale: String,
    /// The model's confidence, clamped to 0-1 (0 when unstated).
    pub confidence: f64,
    /// The paper's length before condensing, when it was condensed.
    pub condensed_from_chars: Option<usize>,
    /// How condensing went.
    pub condensation_status: Option<String>,
    /// Notes for the reader.
    pub notes: Vec<String>,
}

impl Suitability {
    /// Python's `to_dict()`.
    pub fn to_json(&self) -> Value {
        json!({
            "is_systematic_review": self.is_systematic_review,
            "is_meta_analysis": self.is_meta_analysis,
            "is_suitable": self.is_suitable,
            "document_type": self.document_type,
            "variant": self.variant,
            "rationale": self.rationale,
            "confidence": self.confidence,
            "condensed_from_chars": self.condensed_from_chars,
            "condensation_status": self.condensation_status,
            "notes": self.notes,
        })
    }
}

/// The judgement to show, and whether the model's claim was unbacked.
///
/// `NotReported` stands as it is. Any other claim with no located quote
/// (`located == 0`), or `NotApplicable` on an item that may not be, is shown as
/// `NotReported` and flagged.
pub fn shown_judgement(
    model: Judgement,
    located: usize,
    may_be_not_applicable: bool,
) -> (Judgement, bool) {
    if model == Judgement::NotReported {
        return (model, false);
    }
    if located == 0 || (model == Judgement::NotApplicable && !may_be_not_applicable) {
        return (Judgement::NotReported, true);
    }
    (model, false)
}

/// A reply's text field, empty when absent; anything else is unreadable.
fn text_field(value: Option<&Value>, name: &str) -> Result<String, String> {
    match value {
        None | Some(Value::Null) => Ok(String::new()),
        Some(Value::String(text)) => Ok(text.clone()),
        Some(other) => Err(format!(
            "{name} is a {}, not text",
            crate::agents::json_type_name(other)
        )),
    }
}

/// Read a part's reply and verify every item's quotes against `text`.
///
/// Items come back in the order of `asked`. An item the reply lacks is listed
/// in `missing`; one it adds (not in `asked`) is listed in `unasked`, its
/// answer unread, with a WARN. An integer id is read as its decimal digits
/// (`7` as `"7"`).
///
/// # Errors
///
/// A message naming the problem: `items` is not a list, an entry is not an
/// object, an id is absent or repeated, a judgement is not one of the five
/// strings, or `min_similarity` is refused. The message lets a re-ask name it.
pub fn read_part_reply(
    data: &Value,
    asked: &[&'static str],
    text: &str,
    min_similarity: f64,
) -> Result<PartAssessment, String> {
    let entries = data.get("items").and_then(Value::as_array).ok_or_else(|| {
        format!(
            "items is a {}, not a list",
            data.get("items")
                .map_or("NoneType", crate::agents::json_type_name)
        )
    })?;
    let mut by_id: HashMap<String, &Value> = HashMap::new();
    let mut unasked = Vec::new();
    for entry in entries {
        if !entry.is_object() {
            return Err(format!(
                "an item is a {}, not an object",
                crate::agents::json_type_name(entry)
            ));
        }
        let id = match entry.get("id") {
            Some(Value::String(id)) => id.clone(),
            // `"id": 7` reads as "7" (ruling 8): a re-ask for that slip would
            // cost the whole part.
            Some(Value::Number(number)) if number.is_i64() || number.is_u64() => number.to_string(),
            _ => return Err("an item's id is not text".to_string()),
        };
        if by_id.contains_key(&id) {
            return Err(format!("duplicate item id {id:?}"));
        }
        if !asked.contains(&id.as_str()) {
            unasked.push(id.clone());
        }
        by_id.insert(id, entry);
    }
    for id in &unasked {
        log::warn!(
            target: "bmlib.quality.prisma2020_models",
            "PRISMA reply has item {id:?}, which was not asked; ignored"
        );
    }

    let mut part = PartAssessment {
        unasked,
        ..PartAssessment::default()
    };
    for &asked_id in asked {
        let row =
            item(asked_id).ok_or_else(|| format!("item {asked_id:?} is not on the checklist"))?;
        let Some(entry) = by_id.get(asked_id) else {
            part.missing.push(row.id);
            continue;
        };
        let claimed = entry
            .get("judgement")
            .and_then(Value::as_str)
            .and_then(Judgement::parse)
            .ok_or_else(|| {
                format!(
                    "item {asked_id}: unknown judgement {}",
                    entry.get("judgement").unwrap_or(&Value::Null)
                )
            })?;
        let (citations, quotes_not_found, quotes_contradicted, quotes_dropped) =
            verify_quotes(entry.get("quotes"), text, min_similarity).map_err(|e| e.to_string())?;
        let (judgement, unbacked) =
            shown_judgement(claimed, citations.len(), row.may_be_not_applicable);
        part.items.push(ItemAssessment {
            id: row.id,
            model_judgement: claimed,
            judgement,
            unbacked,
            explanation: text_field(
                entry.get("explanation"),
                &format!("item {asked_id}: explanation"),
            )?,
            citations,
            quotes_not_found,
            quotes_contradicted,
            quotes_dropped,
        });
    }
    Ok(part)
}

/// A required boolean of the suitability reply.
fn flag(data: &Value, name: &str) -> Result<bool, String> {
    data.get(name).and_then(Value::as_bool).ok_or_else(|| {
        format!(
            "{name} is a {}, not a boolean",
            data.get(name)
                .map_or("NoneType", crate::agents::json_type_name)
        )
    })
}

/// Read a suitability reply; suitable means a systematic review or a meta-analysis.
///
/// # Errors
///
/// A message naming the flag that is not a boolean, or the text field of
/// another type.
pub fn read_suitability(data: &Value) -> Result<Suitability, String> {
    let is_systematic_review = flag(data, "is_systematic_review")?;
    let is_meta_analysis = flag(data, "is_meta_analysis")?;
    let variant = match data.get("variant") {
        None | Some(Value::Null) => None,
        other => Some(text_field(other, "variant")?),
    };
    Ok(Suitability {
        is_systematic_review,
        is_meta_analysis,
        is_suitable: is_systematic_review || is_meta_analysis,
        document_type: text_field(data.get("document_type"), "document_type")?,
        variant,
        rationale: text_field(data.get("rationale"), "rationale")?,
        confidence: clamped_confidence(data.get("confidence")).unwrap_or(0.0),
        condensed_from_chars: None,
        condensation_status: None,
        notes: Vec::new(),
    })
}
