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

//! PICO extraction with quotes verified against the paper.
//!
//! A port of `bmlib/quality/pico_agent.py` (StudyAnalyzer item 7; the design
//! is `docs/superpowers/specs/2026-10-30-pico-agent-design.md`).

use serde_json::Value;

use crate::citations::{find_passage, InvalidSimilarity};
use crate::pyvalue::python_strip;
use crate::quality::cochrane_assessor::clamped_confidence;
use crate::quality::pico_models::{
    PicoCitation, PicoExtraction, PicoPart, PicoPartState, PicoSuitability,
};

/// The four parts, in the order they are read, reported and averaged.
pub const PICO_PARTS: [&str; 4] = ["population", "intervention", "comparison", "outcome"];

/// The quotes found, the quotes lost, and how many were not usable text.
pub type QuoteCheck = (Vec<PicoCitation>, Vec<String>, usize);

/// Locate the model's quotes in `text`. Python's `verify_quotes`.
///
/// # Errors
///
/// `min_similarity` outside (0, 1], as [`find_passage`] refuses it.
pub fn verify_quotes(
    quotes: Option<&Value>,
    text: &str,
    min_similarity: f64,
) -> Result<QuoteCheck, InvalidSimilarity> {
    let Some(quotes) = quotes.filter(|value| !value.is_null()) else {
        return Ok((Vec::new(), Vec::new(), 0));
    };
    let Some(quotes) = quotes.as_array() else {
        return Ok((Vec::new(), Vec::new(), 1));
    };

    let mut citations: Vec<PicoCitation> = Vec::new();
    let mut not_found: Vec<String> = Vec::new();
    let mut dropped = 0usize;
    for quote in quotes {
        let Some(quote) = quote.as_str().filter(|q| !python_strip(q).is_empty()) else {
            dropped += 1;
            continue;
        };
        let found = find_passage(quote, text, min_similarity)?;
        let Some(span) = found.span else {
            not_found.push(quote.to_string());
            continue;
        };
        // Each span once, in the order the quotes came.
        if citations
            .iter()
            .any(|c| c.start == span.start && c.end == span.end)
        {
            continue;
        }
        citations.push(PicoCitation {
            start: span.start,
            end: span.end,
            text: span.text,
            similarity: found.similarity,
            exact: found.exact,
        });
    }
    Ok((citations, not_found, dropped))
}

/// Read one PICO part and verify its quotes. Python's `read_part`.
///
/// # Errors
///
/// A message when `value` is not an object carrying a string `description`.
pub fn read_part(
    value: Option<&Value>,
    text: &str,
    min_similarity: f64,
) -> Result<PicoPart, String> {
    let Some(object) = value.and_then(Value::as_object) else {
        return Err(format!(
            "expected an object, got {}",
            value.map_or("NoneType", crate::agents::json_type_name)
        ));
    };
    let Some(description) = object.get("description").and_then(Value::as_str) else {
        return Err(format!(
            "description is a {}, not text",
            object
                .get("description")
                .map_or("NoneType", crate::agents::json_type_name)
        ));
    };
    let description = description.to_string();
    let confidence = clamped_confidence(object.get("confidence"));
    // Only an explicit `false` is an absence: a garbled flag must not hide a
    // part the model described.
    if object.get("reported").and_then(Value::as_bool) == Some(false) {
        return Ok(PicoPart {
            description,
            state: PicoPartState::NotReported,
            confidence,
            citations: Vec::new(),
            quotes_not_found: Vec::new(),
            quotes_dropped: 0,
        });
    }

    let (citations, quotes_not_found, quotes_dropped) =
        verify_quotes(object.get("quotes"), text, min_similarity).map_err(|e| e.to_string())?;
    let state = if citations.is_empty() {
        PicoPartState::Unverified
    } else {
        PicoPartState::Verified
    };
    Ok(PicoPart {
        description,
        state,
        confidence,
        citations,
        quotes_not_found,
        quotes_dropped,
    })
}

/// Read an extraction reply. Python's `read_extraction`.
///
/// # Errors
///
/// A message naming the first part that is missing or unusable.
pub fn read_extraction(
    data: &Value,
    text: &str,
    min_similarity: f64,
) -> Result<PicoExtraction, String> {
    let mut parts: Vec<PicoPart> = Vec::with_capacity(PICO_PARTS.len());
    for name in PICO_PARTS {
        let part = read_part(data.get(name), text, min_similarity)
            .map_err(|error| format!("{name}: {error}"))?;
        parts.push(part);
    }

    // A correctly identified absence must not pull the mean down. Summed in
    // PICO order, as Python's `sum()` does, so the two agree bit for bit.
    let values: Vec<f64> = parts
        .iter()
        .filter(|part| part.state != PicoPartState::NotReported)
        .filter_map(|part| part.confidence)
        .collect();
    let overall_confidence = if values.is_empty() {
        None
    } else {
        Some(values.iter().fold(0.0, |sum, value| sum + value) / values.len() as f64)
    };

    let mut parts = parts.into_iter();
    let mut next = || parts.next().expect("four parts were read");
    Ok(PicoExtraction {
        population: next(),
        intervention: next(),
        comparison: next(),
        outcome: next(),
        study_type: as_text(data.get("study_type")),
        sample_size: as_text(data.get("sample_size")),
        overall_confidence,
        condensed_from_chars: None,
        condensation_status: None,
        notes: Vec::new(),
    })
}

/// Read a suitability reply. Python's `read_suitability`.
///
/// # Errors
///
/// A message when `is_suitable` is not a boolean.
pub fn read_suitability(data: &Value) -> Result<PicoSuitability, String> {
    let Some(is_suitable) = data.get("is_suitable").and_then(Value::as_bool) else {
        return Err(format!(
            "is_suitable is a {}, not a boolean",
            data.get("is_suitable")
                .map_or("NoneType", crate::agents::json_type_name)
        ));
    };
    Ok(PicoSuitability {
        is_suitable,
        is_intervention_study: data.get("is_intervention_study").and_then(Value::as_bool),
        has_comparison: data.get("has_comparison").and_then(Value::as_bool),
        study_type: as_text(data.get("study_type")),
        confidence: clamped_confidence(data.get("confidence")),
        rationale: as_text(data.get("rationale")),
        condensed_from_chars: None,
        condensation_status: None,
        notes: Vec::new(),
    })
}

/// `_json_fields.as_text`: a string, verbatim; anything else is unstated.
fn as_text(value: Option<&Value>) -> Option<String> {
    value.and_then(Value::as_str).map(str::to_string)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    const TEXT: &str = "Adults aged 40-65 were randomised to metformin or placebo.";
    const SIM: f64 = 0.9;

    fn part(overrides: Value) -> Value {
        let mut value = json!({
            "description": "Adults aged 40 to 65",
            "reported": true,
            "confidence": 0.9,
            "quotes": ["Adults aged 40-65"],
        });
        for (key, item) in overrides.as_object().expect("an object") {
            value[key] = item.clone();
        }
        value
    }

    #[test]
    fn a_found_quote_is_the_papers_span() {
        let result = read_part(
            Some(&part(json!({"quotes": ["adults aged 40–65"]}))),
            TEXT,
            SIM,
        )
        .expect("a part");
        assert_eq!(result.state, PicoPartState::Verified);
        assert_eq!(result.citations[0].text, "Adults aged 40-65");
        assert_eq!(result.citations[0].start, 0);
    }

    #[test]
    fn quotes_as_a_bare_string() {
        let result = read_part(
            Some(&part(json!({"quotes": "Adults aged 40-65"}))),
            TEXT,
            SIM,
        )
        .expect("a part");
        assert_eq!(result.state, PicoPartState::Unverified);
        assert_eq!(result.quotes_dropped, 1);
    }

    #[test]
    fn overall_confidence_skips_unreported_parts() {
        let data = json!({
            "population": part(json!({"confidence": 0.9})),
            "intervention": part(json!({"confidence": 0.7})),
            "comparison": part(json!({"reported": false, "confidence": 0.1})),
            "outcome": part(json!({"confidence": null})),
        });
        let extraction = read_extraction(&data, TEXT, SIM).expect("an extraction");
        assert_eq!(extraction.overall_confidence, Some((0.9 + 0.7) / 2.0));
    }

    #[test]
    fn a_missing_part_names_itself() {
        let data = json!({
            "population": part(json!({})),
            "intervention": part(json!({})),
            "outcome": part(json!({})),
        });
        let error = read_extraction(&data, TEXT, SIM).expect_err("comparison is missing");
        assert!(error.starts_with("comparison: "), "{error}");
    }

    #[test]
    fn suitability_requires_a_bool_is_suitable() {
        assert!(read_suitability(&json!({"is_suitable": "yes"})).is_err());
    }
}
