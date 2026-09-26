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

//! Reading what an LLM answered into a [`QualityAssessment`].
//!
//! Extracted from the two agent classes as **pure functions of the parsed JSON**,
//! because that is where the rules live and it is the part a test can pin without
//! a model.
//!
//! # The defect this fixes
//!
//! `dict.get(k, default)` returns its default only for an **absent** key, so a key
//! present with JSON `null` yielded `None` and the next call raised:
//!
//! | payload | the Python's Tier 3 |
//! |---|---|
//! | `"design_characteristics": null` | `AttributeError: 'NoneType' object has no attribute 'get'` |
//! | `"bias_risk": null` | `AttributeError: 'NoneType' object has no attribute 'get'` |
//! | `"study_design": null` | `AttributeError: 'NoneType' object has no attribute 'lower'` |
//!
//! And the prompt **sanctions that input** — *"If information is unclear or not
//! mentioned, use null or `"unclear"`"* — so a model following its instructions is
//! what triggered it. The failure was worse than a degraded score: the broad
//! `except` turned it into `unclassified()` *after* `chat_json` had returned a
//! parseable dict, so no retry ran and the log misfiled it as a model failure —
//! and `QualityManager` lets Tier 3 **replace** Tier 1, so a paper the metadata
//! classified conclusively as an RCT came back `UNKNOWN` at score 0.
//!
//! The package already stated the rule one module over
//! (`cochrane_assessor._as_dict`: *"A model that answers `null` or a bare string
//! for a whole section must not take the assessment down with it"*), and the
//! top-level version of this was already fixed with `require_dict=True`. A null
//! *inside* the object is the residual. Filed as
//! [#295](https://github.com/hherb/bmlib/issues/295).
//!
//! Every read below treats **absent, null and wrong-typed alike** as "not
//! answered", which is the one rule that closes all three sites at once.

use crate::quality::data_models::{
    study_design_from_str, BiasRisk, QualityAssessment, StudyDesign,
};
use serde_json::Value;

/// The blinding values the assessment recognises.
const VALID_BLINDING: &[&str] = &["none", "single", "double", "triple"];

/// The rule this module exists for: an object, or an empty one.
///
/// A key that is absent, `null`, a string, an array or a number all read as **no
/// mapping** rather than raising. Named because the same coercion serves four
/// read sites, and a second spelling of it is the second place to get it wrong.
#[must_use]
pub fn as_mapping(value: Option<&Value>) -> Value {
    match value {
        Some(value @ Value::Object(_)) => value.clone(),
        _ => Value::Object(serde_json::Map::new()),
    }
}

/// A string field, or `None` when it is absent or not a string.
///
/// A `null` and a number both read as "not answered". `.lower().strip()` on
/// `None` is what raised, and on a number it would have raised too.
#[must_use]
pub fn as_text(value: Option<&Value>) -> Option<String> {
    value
        .and_then(Value::as_str)
        .map(|s| s.trim().to_lowercase())
}

/// An integer field, or `None`.
///
/// **A float is truncated toward zero**, because Python's `int(100.5)` is `100`
/// and does not raise. Reading it as absent would record no sample size for a
/// model that answered one.
///
/// A value that is not a number at all is **not** an error: `int("many")`
/// raising was caught and swallowed in the Python, so the observable behaviour
/// is `None` either way.
#[must_use]
pub fn as_int(value: Option<&Value>) -> Option<i64> {
    match value {
        Some(Value::Number(n)) => n.as_i64().or_else(|| n.as_f64().map(|f| f.trunc() as i64)),
        Some(Value::String(s)) => s
            .trim()
            .parse::<i64>()
            .ok()
            .or_else(|| s.trim().parse::<f64>().ok().map(|f| f.trunc() as i64)),
        _ => None,
    }
}

/// A float field, or the default.
#[must_use]
pub fn as_float(value: Option<&Value>, default: f64) -> f64 {
    match value {
        Some(Value::Number(n)) => n.as_f64().unwrap_or(default),
        Some(Value::String(s)) => s.trim().parse::<f64>().unwrap_or(default),
        _ => default,
    }
}

/// A list of strings, or empty.
///
/// A non-string member is **skipped rather than coerced**: a model that put a
/// number in `strengths` has not stated a strength, and `"12"` in a list of
/// findings is a claim nobody made.
#[must_use]
pub fn as_string_list(value: Option<&Value>) -> Vec<String> {
    match value {
        Some(Value::Array(items)) => items
            .iter()
            .filter_map(Value::as_str)
            .map(str::to_string)
            .collect(),
        _ => Vec::new(),
    }
}

/// A design name from JSON, defaulting to `"unknown"`.
///
/// The `"unknown"` default is **not observable**: `study_design_from_str` maps
/// both `"unknown"` and any unrecognised name — an empty string included — to
/// `StudyDesign::Unknown`, so a mutant replacing the fallback with `""` survives
/// the file. Kept as the Python spells it, and named here so the survivor is a
/// recorded fact.
#[must_use]
pub fn as_design(value: Option<&Value>) -> StudyDesign {
    let name = as_text(value).unwrap_or_else(|| "unknown".to_string());
    study_design_from_str(&name)
}

/// A blinding value, or `None` when it is not one the model may claim.
///
/// **Case-sensitive**, matching the Python: it tests the raw value against the
/// four words, so `"Triple"` and `"Double"` are *not* recognised and read as
/// unstated. That is a quirk rather than a rule — but a port that folded case
/// would record a blinding the Python dropped, which is a new claim about a
/// study rather than a tidier spelling of an old one.
#[must_use]
pub fn as_blinding(value: Option<&Value>) -> Option<String> {
    let raw = value.and_then(Value::as_str)?;
    VALID_BLINDING.contains(&raw).then(|| raw.to_string())
}

/// Parse a **Tier 2** classifier answer.
///
/// The design is read with `"unknown"` as the fallback, so a model that answers
/// `null` — which the prompt permits — classifies as unknown rather than taking
/// the caller's batch down.
#[must_use]
pub fn parse_classification(data: &Value) -> QualityAssessment {
    let design = as_design(data.get("study_design"));
    let confidence = as_float(data.get("confidence"), 0.5).clamp(0.0, 1.0);
    let sample_size = as_int(data.get("sample_size"));
    let blinding = as_blinding(data.get("blinding"));

    QualityAssessment::from_classification(design, confidence, sample_size, blinding)
}

/// Parse a **Tier 3** deep-assessment answer.
///
/// Three of its reads are the ones #295 describes, and all three go through the
/// coercions above: the design, the `design_characteristics` mapping and the
/// `bias_risk` mapping.
#[must_use]
pub fn parse_assessment(data: &Value) -> QualityAssessment {
    let design = as_design(data.get("study_design"));
    let chars = as_mapping(data.get("design_characteristics"));
    let bias_data = as_mapping(data.get("bias_risk"));

    // The four design flags read as **booleans or not at all**.
    //
    // The Python passes `chars.get(k)` straight through, so a model answering
    // `"true"` stores the *string* — in a field annotated `bool | None`, where
    // `is_randomized != Some(true)` is what the quality filter tests. A string
    // there silently fails `require_randomization` while reading as an answer.
    // This narrows to the annotated type: a non-boolean is not answered.
    //
    // What is preserved is the distinction that matters: an **absent** flag is
    // `None`, not `false`, so a model that said nothing is not recorded as having
    // denied it.
    let flag = |key: &str| chars.get(key).and_then(Value::as_bool);

    let quality_score = as_float(data.get("quality_score"), 0.0).clamp(0.0, 10.0);
    let confidence = as_float(data.get("confidence"), 0.5).clamp(0.0, 1.0);
    let sample_size = as_int(data.get("sample_size"));

    QualityAssessment {
        assessment_tier: 3,
        extraction_method: "llm_deep_assessment".to_string(),
        study_design: design,
        quality_tier: crate::quality::data_models::design_to_tier(design),
        quality_score,
        evidence_level: data
            .get("evidence_level")
            .and_then(Value::as_str)
            .map(str::to_string),
        is_randomized: flag("randomized"),
        is_controlled: flag("controlled"),
        is_blinded: as_blinding(chars.get("blinded")),
        is_prospective: flag("prospective"),
        is_multicenter: flag("multicenter"),
        sample_size,
        confidence,
        strengths: as_string_list(data.get("strengths")),
        limitations: as_string_list(data.get("limitations")),
        extraction_details: vec!["Detailed assessment via LLM".to_string()],
        transparency_adjusted: false,
        // `BiasRisk::from_json` maps an unrecognised domain to `"unclear"`, so
        // an all-null `bias_risk` yields the five-unclear record rather than
        // `None` — which is what "the model did not answer" reads as, and what
        // the Python produced for an absent one.
        bias_risk: Some(BiasRisk::from_json(&bias_data)),
        cochrane_assessment: None,
    }
}
