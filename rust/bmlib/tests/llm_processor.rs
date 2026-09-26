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

//! The LLM chunk processor — the oracle and the named tests.
//!
//! The corpus (30 cases) diffs the pure half against Python's. The named tests
//! state the rules the oracle cannot: why the render is one pass, and why an
//! unusable confidence is not zero.

use bmlib::context_processor::data_types::ConsolidatedItem;
use bmlib::context_processor::llm_processor::{
    extract_from_batch, format_consolidated_item, format_item, is_scored_chunk, read_confidence,
    read_findings, render_template, validate_template, BatchRequest, ContextModel, PromptTemplates,
    DEFAULT_EXTRACTION_CONFIDENCE,
};
use serde_json::{json, Value};
use std::collections::BTreeMap;

const CASES: &str = include_str!("data/llm_processor_cases.json");
const EXPECTED: &str = include_str!("data/llm_processor_expected.json");

fn run(case: &Value) -> Value {
    let a = &case["args"];
    match case["fn"].as_str().unwrap_or_default() {
        "is_scored_chunk" => json!(is_scored_chunk(&a["item"])),
        "render" => json!(render_template(
            a["template"].as_str().unwrap_or_default(),
            a["query"].as_str().unwrap_or_default(),
            a["content"].as_str().unwrap_or_default(),
        )),
        "validate_template" => match validate_template(
            a["template"].as_str().unwrap_or_default(),
            a["name"].as_str().unwrap_or_default(),
        ) {
            Ok(()) => Value::Null,
            Err(message) => json!(message),
        },
        "format_item" => json!(format_item(
            &a["item"],
            a["index"].as_u64().unwrap_or(0) as usize
        )),
        "format_consolidated_item" => {
            let mut item = ConsolidatedItem::new(
                a["item"]["content"]
                    .as_str()
                    .unwrap_or_default()
                    .to_string(),
                BTreeMap::new(),
            );
            if let Some(metadata) = a["item"].get("metadata").and_then(Value::as_object) {
                for (key, value) in metadata {
                    item.metadata.insert(key.clone(), value.clone());
                }
            }
            json!(format_consolidated_item(
                &item,
                a["index"].as_u64().unwrap_or(0) as usize
            ))
        }
        other => panic!("unknown fn {other:?}"),
    }
}

#[test]
fn the_port_agrees_with_python_on_every_case() {
    let cases: Value = serde_json::from_str(CASES).expect("cases parse");
    let expected: Value = serde_json::from_str(EXPECTED).expect("expected parse");
    let cases = cases.as_array().expect("cases is a list");
    let wants = expected["cases"].as_array().expect("cases");

    let mut failures: Vec<String> = Vec::new();
    for (case, want) in cases.iter().zip(wants.iter()) {
        let name = case["name"].as_str().unwrap_or_default();
        assert_eq!(name, want["name"].as_str().unwrap_or_default());
        assert!(
            want["ok"].as_bool().unwrap_or(false),
            "{name}: {}",
            want["error"]
        );
        // A `corrected` block marks a case where the port deliberately differs —
        // see the corpus's own `divergence` note.
        let expected_value = match case.get("corrected") {
            Some(_) => run_divergent(case),
            None => want["value"].clone(),
        };
        let got = run(case);
        if got != expected_value {
            failures.push(format!(
                "  {name}\n    expected: {}\n    rust:     {}",
                serde_json::to_string(&expected_value).unwrap_or_default(),
                serde_json::to_string(&got).unwrap_or_default()
            ));
        }
    }
    assert!(
        failures.is_empty(),
        "{} of {} diverge:\n{}",
        failures.len(),
        cases.len(),
        failures.join("\n")
    );
}

/// The value this port produces for a case it deliberately diverges on.
fn run_divergent(case: &Value) -> Value {
    match case["name"].as_str().unwrap_or_default() {
        // `[string, number]` reads as a scored chunk here; Python requires a
        // tuple, which JSON cannot carry — so the oracle reconstructs one for the
        // `format_item` cases and reports `false` for the predicate ones.
        "is_scored_chunk/scored" | "is_scored_chunk/scored-int" => json!(true),
        // The header is identical; the body's repr is not, and the body is the
        // caller's own value rather than a bmlib claim. The corpus carries this
        // port's rendering in a `corrected` block.
        "format_item/unexpected-type" => case["corrected"].clone(),
        other => panic!("no divergence declared for {other}"),
    }
}

#[test]
fn the_constants_are_pythons() {
    let expected: Value = serde_json::from_str(EXPECTED).expect("expected");
    assert_eq!(
        DEFAULT_EXTRACTION_CONFIDENCE,
        expected["tables"]["DEFAULT_EXTRACTION_CONFIDENCE"]
            .as_f64()
            .expect("confidence")
    );
}

// ---------------------------------------------------------------------------
// Templates
// ---------------------------------------------------------------------------

/// **Substitution is one pass, not two chained replacements.** A second pass runs
/// over what the first substituted, so a query containing the literal `{content}`
/// would have the whole batch spliced into it — doubling a prompt that was sized
/// to fit exactly, which is the context overflow this module exists to prevent.
#[test]
fn the_render_is_one_pass() {
    let rendered = render_template("Q {query} C {content}", "{content}", "REAL");
    assert_eq!(
        rendered, "Q {content} C REAL",
        "the substituted query is not re-scanned"
    );
    // And a template holding literal braces keeps them.
    assert_eq!(
        render_template(r#"{"a": 1} {query} {content}"#, "q", "c"),
        r#"{"a": 1} q c"#
    );
    // A brace that is not one of the two placeholders is untouched.
    assert_eq!(
        render_template("{other} {query} {content}", "q", "c"),
        "{other} q c"
    );
    // Every occurrence is filled, not just the first.
    assert_eq!(render_template("{query}{query}{content}", "q", "c"), "qqc");
}

/// A template missing a placeholder is **refused at construction**, naming which
/// parameter is short — a caller with two templates needs to know which to fix.
#[test]
fn a_template_missing_a_placeholder_is_named() {
    assert!(validate_template("{query} {content}", "extraction_prompt").is_ok());
    let err = validate_template("only {query}", "consolidation_prompt").expect_err("refused");
    assert!(err.contains("{content}"), "{err}");
    assert!(err.contains("consolidation_prompt"), "{err}");
    let err = validate_template("only {content}", "extraction_prompt").expect_err("refused");
    assert!(err.contains("{query}"), "{err}");

    // And the pair validates together.
    let bad = PromptTemplates {
        extraction_prompt: "no placeholders".to_string(),
        consolidation_prompt: "{query} {content}".to_string(),
    };
    assert!(bad.validate().is_err());
}

/// A scored chunk is rendered with its score; a plain string with an `[Item N]`
/// header. **A JSON boolean score is rejected although it is a number** — a
/// caller's mistake, and rendering it as `score 1.00` would hide that.
#[test]
fn a_scored_chunk_shows_its_score_and_a_boolean_score_is_refused() {
    assert!(is_scored_chunk(&json!(["text", 0.5])));
    assert!(
        !is_scored_chunk(&json!(["text", true])),
        "a bool is not a score"
    );
    assert!(
        !is_scored_chunk(&json!(["a", 1, 2])),
        "three members is not a pair"
    );
    assert!(
        !is_scored_chunk(&json!([1, 0.5])),
        "the text must be a string"
    );
    assert!(!is_scored_chunk(&json!("text")));
    assert!(!is_scored_chunk(&json!(null)));

    assert_eq!(
        format_item(&json!(["chunk text", 0.5]), 0),
        "[Chunk 1, score 0.50]\nchunk text"
    );
    assert_eq!(
        format_item(&json!("plain chunk"), 0),
        "[Item 1]\nplain chunk"
    );
    // An unexpected type gets the `[Item N]` header too, as the source renders it.
    assert!(format_item(&json!({"a": 1}), 4).starts_with("[Item 5]"));
}

/// The consolidated header names the level **as the metadata spells it** — the
/// source's f-string coerces, so an integer `3` and the string `"3"` both print
/// as `3`, and a non-numeric value prints as itself.
#[test]
fn the_consolidated_level_is_printed_as_depicted() {
    let with_level = |value: Value| {
        let mut item = ConsolidatedItem::new("summary", BTreeMap::new());
        item.metadata.insert("recursion_level".to_string(), value);
        format_consolidated_item(&item, 0)
    };
    assert_eq!(
        format_consolidated_item(&ConsolidatedItem::new("s", BTreeMap::new()), 0),
        "[Consolidated level 0, item 1]\ns"
    );
    assert_eq!(
        with_level(json!(2)),
        "[Consolidated level 2, item 1]\nsummary"
    );
    assert_eq!(
        with_level(json!("3")),
        "[Consolidated level 3, item 1]\nsummary"
    );
}

// ---------------------------------------------------------------------------
// Extraction
// ---------------------------------------------------------------------------

/// A scripted model.
struct Scripted {
    plain: Result<String, String>,
    json: Result<Value, String>,
    /// Every prompt it was asked, so a test can assert which template was used.
    prompts: std::sync::Mutex<Vec<String>>,
}

impl Scripted {
    fn plain(content: &str) -> Self {
        Scripted {
            plain: Ok(content.to_string()),
            json: Ok(json!({})),
            prompts: std::sync::Mutex::new(Vec::new()),
        }
    }

    fn structured(value: Value) -> Self {
        Scripted {
            plain: Ok(String::new()),
            json: Ok(value),
            prompts: std::sync::Mutex::new(Vec::new()),
        }
    }

    fn asked(&self) -> Vec<String> {
        self.prompts.lock().expect("lock").clone()
    }
}

impl ContextModel for Scripted {
    fn complete(&self, prompt: &str, _t: f64, _m: i64) -> Result<String, String> {
        self.prompts.lock().expect("lock").push(prompt.to_string());
        self.plain.clone()
    }

    fn complete_json(&self, prompt: &str, _t: f64, _m: i64) -> Result<Value, String> {
        self.prompts.lock().expect("lock").push(prompt.to_string());
        self.json.clone()
    }
}

fn templates() -> PromptTemplates {
    PromptTemplates {
        extraction_prompt: "EXTRACT {query} FROM {content}".to_string(),
        consolidation_prompt: "MERGE {query} FROM {content}".to_string(),
    }
}

fn metadata(level: u64) -> BTreeMap<String, Value> {
    let mut map = BTreeMap::new();
    map.insert("recursion_level".to_string(), json!(level));
    map.insert("batch_index".to_string(), json!(7));
    map
}

/// **Level 0 uses the extraction prompt; every level above uses the consolidation
/// prompt**, which asks the model to merge rather than extract.
#[test]
fn the_prompt_depends_on_the_level() {
    let model = Scripted::plain("answer");
    let result = extract_from_batch(
        &model,
        &BatchRequest {
            templates: &templates(),
            batch_content: "BODY",
            query: "Q",
            batch_metadata: &metadata(0),
            temperature: 0.2,
            max_tokens: 100,
            use_structured_output: false,
        },
    )
    .expect("extracts");
    assert_eq!(result.content, "answer");
    assert!(
        model.asked()[0].starts_with("EXTRACT"),
        "{:?}",
        model.asked()
    );

    let model = Scripted::plain("answer");
    extract_from_batch(
        &model,
        &BatchRequest {
            templates: &templates(),
            batch_content: "BODY",
            query: "Q",
            batch_metadata: &metadata(2),
            temperature: 0.2,
            max_tokens: 100,
            use_structured_output: false,
        },
    )
    .expect("extracts");
    assert!(model.asked()[0].starts_with("MERGE"), "{:?}", model.asked());
}

/// A **plain** answer carries the default confidence, and the batch's index and
/// level are copied onto the result.
#[test]
fn a_plain_answer_carries_the_default_confidence() {
    let model = Scripted::plain("  padded answer  ");
    let result = extract_from_batch(
        &model,
        &BatchRequest {
            templates: &templates(),
            batch_content: "B",
            query: "Q",
            batch_metadata: &metadata(1),
            temperature: 0.2,
            max_tokens: 100,
            use_structured_output: false,
        },
    )
    .expect("extracts");
    assert_eq!(result.content, "padded answer", "the answer is trimmed");
    assert_eq!(result.confidence, DEFAULT_EXTRACTION_CONFIDENCE);
    assert_eq!(result.recursion_level, 1);
    assert_eq!(result.batch_index, Some(7));
    // No findings means no `key_findings` key at all.
    assert!(!result.metadata.contains_key("key_findings"));
}

/// Structured output is used **only at level 0**, and it reads the model's own
/// confidence and findings.
#[test]
fn structured_output_is_read_at_level_zero() {
    let model = Scripted::structured(json!({
        "extracted_content": "  the content  ",
        "confidence": 0.42,
        "key_findings": ["one", "two"],
    }));
    let result = extract_from_batch(
        &model,
        &BatchRequest {
            templates: &templates(),
            batch_content: "B",
            query: "Q",
            batch_metadata: &metadata(0),
            temperature: 0.2,
            max_tokens: 100,
            use_structured_output: true,
        },
    )
    .expect("extracts");
    assert_eq!(result.content, "the content");
    assert_eq!(result.confidence, 0.42);
    assert_eq!(
        result.metadata.get("key_findings"),
        Some(&json!(["one", "two"]))
    );

    // At level 1 the same flag is ignored: the answer is a plain completion.
    let model = Scripted::plain("merged");
    let result = extract_from_batch(
        &model,
        &BatchRequest {
            templates: &templates(),
            batch_content: "B",
            query: "Q",
            batch_metadata: &metadata(1),
            temperature: 0.2,
            max_tokens: 100,
            use_structured_output: true,
        },
    )
    .expect("extracts");
    assert_eq!(result.content, "merged");
}

/// **An unusable confidence falls back to the default, not to zero.** A model that
/// reported `"high"` has not said it is unsure, and recording zero would let
/// `min_confidence_threshold` discard a good extraction for a type error.
#[test]
fn an_unusable_confidence_is_the_default() {
    assert_eq!(read_confidence(&json!({})), DEFAULT_EXTRACTION_CONFIDENCE);
    assert_eq!(
        read_confidence(&json!({"confidence": "high"})),
        DEFAULT_EXTRACTION_CONFIDENCE
    );
    assert_eq!(
        read_confidence(&json!({"confidence": null})),
        DEFAULT_EXTRACTION_CONFIDENCE
    );
    // A reported number is clamped, not discarded.
    assert_eq!(read_confidence(&json!({"confidence": 0.25})), 0.25);
    assert_eq!(read_confidence(&json!({"confidence": 1.5})), 1.0);
    assert_eq!(read_confidence(&json!({"confidence": -0.5})), 0.0);
    // And a numeric string is read, as Python's `float()` reads it.
    assert_eq!(read_confidence(&json!({"confidence": "0.3"})), 0.3);
}

/// A findings list that is **not a list** yields nothing rather than being coerced
/// — the source's `list(...)` over a string would split it into characters, which
/// is a shape no caller asked for.
#[test]
fn findings_must_be_a_list() {
    assert_eq!(read_findings(&json!({"key_findings": ["a", "b"]})).len(), 2);
    assert!(read_findings(&json!({"key_findings": "abc"})).is_empty());
    assert!(read_findings(&json!({"key_findings": null})).is_empty());
    assert!(read_findings(&json!({})).is_empty());
}

/// **A transport failure is an `Err`, not an empty extraction**, so the harness can
/// record it against the batch it came from rather than losing the batch's index.
#[test]
fn a_transport_failure_is_reported() {
    let model = Scripted {
        plain: Err("connection refused".to_string()),
        json: Ok(json!({})),
        prompts: std::sync::Mutex::new(Vec::new()),
    };
    let error = extract_from_batch(
        &model,
        &BatchRequest {
            templates: &templates(),
            batch_content: "B",
            query: "Q",
            batch_metadata: &metadata(0),
            temperature: 0.2,
            max_tokens: 100,
            use_structured_output: false,
        },
    )
    .expect_err("fails");
    assert!(error.contains("connection refused"), "{error}");
}
