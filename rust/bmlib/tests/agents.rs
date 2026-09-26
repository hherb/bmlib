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

//! `chat_json`'s retry and truncation rules, and the metrics report.
//!
//! The chat loop is driven by a scripted [`ChatSource`], so every rule here is
//! exercised without a model and without spending the backoff.

use bmlib::agents::{
    chat_json, classify_stop_reason, group, json_type_name, round_to, ChatJsonError, ChatSource,
    JsonAttempt, MetricsSnapshot, Monotonic, PerformanceMetrics, StopReasonClass,
};
use bmlib::llm::LLMResponse;
use serde_json::{json, Value};

/// A scripted source: one response per call, and a record of the backoffs.
struct Script {
    responses: Vec<Result<LLMResponse, String>>,
    calls: usize,
    backoffs: Vec<usize>,
}

impl Script {
    fn new(responses: Vec<Result<LLMResponse, String>>) -> Self {
        Script {
            responses,
            calls: 0,
            backoffs: Vec::new(),
        }
    }

    /// A source answering with `content` and an optional stop reason, forever.
    fn answering(content: &str, stop_reason: Option<&str>) -> Self {
        Script::new(vec![Ok(response(content, stop_reason))])
    }
}

fn response(content: &str, stop_reason: Option<&str>) -> LLMResponse {
    LLMResponse {
        content: content.to_string(),
        stop_reason: stop_reason.map(str::to_string),
        ..Default::default()
    }
}

impl ChatSource for Script {
    fn chat(&mut self, _attempt: usize) -> Result<LLMResponse, String> {
        let index = self.calls.min(self.responses.len().saturating_sub(1));
        self.calls += 1;
        self.responses[index].clone()
    }

    fn backoff(&mut self, attempt: usize) {
        self.backoffs.push(attempt);
    }
}

fn succeed(source: &mut Script, temperature: f64) -> Result<Value, ChatJsonError> {
    chat_json(source, 3, temperature, Some(100), 4096, false).map(|o| o.value)
}

// ---------------------------------------------------------------------------
// The defect #300
// ---------------------------------------------------------------------------

/// **A truncated response that only parsed because repair fabricated the closing
/// brackets is truncation, not success.**
///
/// Each row is a case the Python returned as a usable value. The second is the
/// clearest: a value cut mid-sentence came back as a complete string field. The
/// first fabricates the number `12` from a stream that stopped mid-token.
#[test]
fn a_repaired_truncated_response_is_truncation_not_success() {
    for content in [
        r#"{"n": 12"#,
        r#"{"summary": "The study found that metformin"#,
        r#"{"a": 1, "b": [1, 2"#,
    ] {
        let mut source = Script::answering(content, Some("max_tokens"));
        let error = succeed(&mut source, 0.0).expect_err("must refuse");
        match error {
            ChatJsonError::Truncated { stop_reason, .. } => {
                assert_eq!(stop_reason, "max_tokens", "the provider's own word");
            }
            other => panic!("{content:?} must be truncation, got {other:?}"),
        }
    }
}

/// The case the Python's own test pinned — and the **only** one its shortcut was
/// right about: JSON complete despite hitting the ceiling, parsing without
/// repair. That is usable as-is.
#[test]
fn a_truncated_response_that_parses_strictly_is_usable() {
    let mut source = Script::answering(r#"{"ok": true}"#, Some("max_tokens"));
    let value = succeed(&mut source, 0.0).expect("usable");
    assert_eq!(value, json!({"ok": true}));

    // And it is reported as **not** repaired, so a caller can tell.
    let mut source = Script::answering(r#"{"ok": true}"#, Some("max_tokens"));
    let outcome = chat_json(&mut source, 3, 0.0, Some(100), 4096, false).expect("usable");
    assert!(!outcome.repaired);
    assert_eq!(outcome.attempts, 1);
}

/// **At temperature 0 a retry is provably futile** for truncation: greedy
/// sampling reproduces the identical truncation, so the call fails immediately
/// rather than paying for it three times.
#[test]
fn truncation_at_zero_temperature_does_not_retry() {
    let mut source = Script::new(vec![Ok(response(r#"{"n": 12"#, Some("max_tokens"))); 3]);
    let error = succeed(&mut source, 0.0).expect_err("fails");
    assert_eq!(source.calls, 1, "one attempt, not three");
    assert!(source.backoffs.is_empty(), "and no backoff");
    match error {
        ChatJsonError::Truncated {
            attempts, budget, ..
        } => {
            assert_eq!(attempts, 1);
            assert_eq!(budget, 100, "the ceiling that was hit");
        }
        other => panic!("expected Truncated, got {other:?}"),
    }
}

/// Above zero a retry may sample a shorter completion that fits, so truncation
/// **is** retried — and the backoff is 1s, 2s, … by attempt number.
#[test]
fn truncation_above_zero_retries_with_backoff() {
    let mut source = Script::new(vec![
        Ok(response(r#"{"n": 12"#, Some("max_tokens"))),
        Ok(response(r#"{"n": 3}"#, Some("stop"))),
    ]);
    let value = succeed(&mut source, 0.7).expect("second attempt fits");
    assert_eq!(value, json!({"n": 3}));
    assert_eq!(source.calls, 2);
    assert_eq!(source.backoffs, vec![1], "1s before the first retry");
}

/// Anthropic and Ollama call the ceiling `length`; a provider reporting it is
/// truncated just the same.
#[test]
fn both_spellings_of_the_ceiling_are_truncation() {
    assert_eq!(
        classify_stop_reason(Some("max_tokens")),
        StopReasonClass::Truncated
    );
    assert_eq!(
        classify_stop_reason(Some("length")),
        StopReasonClass::Truncated
    );
    assert_eq!(
        classify_stop_reason(Some("stop")),
        StopReasonClass::Finished
    );
    assert_eq!(
        classify_stop_reason(Some("end_turn")),
        StopReasonClass::Finished
    );
    // **Absent is Finished**: a provider that reports no reason has not said the
    // output was cut off, and treating silence as truncation would refuse every
    // answer from a server that omits the field.
    assert_eq!(classify_stop_reason(None), StopReasonClass::Finished);
}

// ---------------------------------------------------------------------------
// The other diagnoses
// ---------------------------------------------------------------------------

/// A response that stopped **normally** but needed repair is usable — the model
/// finished and its JSON was simply malformed — but it is **reported as
/// repaired**, so a caller is not told a mended document was whole.
#[test]
fn a_repaired_response_that_finished_normally_is_usable_and_reported() {
    let mut source = Script::answering(r#"{"a": 1,}"#, Some("stop"));
    let outcome = chat_json(&mut source, 3, 0.0, Some(100), 4096, false).expect("usable");
    assert_eq!(outcome.value, json!({"a": 1}));
    assert!(outcome.repaired, "a caller must be able to tell");
    assert!(!outcome.attempts > 1);
}

/// An empty response is its own diagnosis, always retried: it can be sampling
/// noise, and it is not the caller's shape to fix.
#[test]
fn an_empty_response_is_retried_and_reported_as_empty() {
    let mut source = Script::new(vec![
        Ok(response("", Some("stop"))),
        Ok(response("{}", Some("stop"))),
    ]);
    let value = succeed(&mut source, 0.0).expect("second attempt answers");
    assert_eq!(value, json!({}));

    let mut source = Script::new(vec![Ok(response("   ", Some("stop")))]);
    let error = chat_json(&mut source, 1, 0.0, Some(100), 4096, false).expect_err("fails");
    assert_eq!(source.calls, 1, "one attempt only: max_retries was 1");
    assert!(matches!(error, ChatJsonError::Empty { .. }), "{error:?}");
}

/// An unparseable answer is retried, and its diagnosis is **separate** from
/// truncation: they call for different remedies.
#[test]
fn an_unparseable_answer_is_retried_and_reported_separately() {
    let mut source = Script::new(vec![
        Ok(response("not json at all", Some("stop"))),
        Ok(response(r#"{"ok": 1}"#, Some("stop"))),
    ]);
    let value = succeed(&mut source, 0.5).expect("second attempt answers");
    assert_eq!(value, json!({"ok": 1}));

    let mut source = Script::new(vec![Ok(response("((((", Some("stop")))]);
    let error = chat_json(&mut source, 1, 0.5, Some(100), 4096, false).expect_err("fails");
    assert!(
        matches!(error, ChatJsonError::Unparseable { .. }),
        "{error:?}"
    );
}

/// **A transport failure is its own diagnosis**, not "empty response from
/// model": reporting a refused connection as an empty answer points the operator
/// at the model.
#[test]
fn a_transport_failure_is_reported_as_such() {
    let mut source = Script::new(vec![Err("connection refused".to_string())]);
    let error = chat_json(&mut source, 3, 0.7, Some(100), 4096, false).expect_err("fails");
    match &error {
        ChatJsonError::Transport { message, .. } => assert!(message.contains("connection refused")),
        other => panic!("expected Transport, got {other:?}"),
    }
    assert!(
        !error.to_string().contains("empty response"),
        "must not name the wrong cause: {error}"
    );
}

/// `require_dict` demands an object, and **at temperature 0 the same messages
/// return the same array**, so it fails at once rather than retrying.
#[test]
fn requiring_a_dict_rejects_an_array_and_says_what_arrived() {
    let mut source = Script::answering("[1, 2]", Some("stop"));
    let error = chat_json(&mut source, 3, 0.0, Some(100), 4096, true).expect_err("fails");
    assert_eq!(source.calls, 1, "greedy sampling repeats itself");
    match &error {
        ChatJsonError::WrongShape { got, .. } => assert_eq!(got, "list"),
        other => panic!("expected WrongShape, got {other:?}"),
    }
    let message = error.to_string();
    assert!(message.contains("expected a JSON object"), "{message}");
    assert!(message.contains("list"), "{message}");

    // Without the demand, the array is the answer.
    let mut source = Script::answering("[1, 2]", Some("stop"));
    let value = succeed(&mut source, 0.0).expect("an array is fine");
    assert_eq!(value, json!([1, 2]));
}

/// The type names match Python's `type(x).__name__`, because the message is
/// reproduced verbatim.
#[test]
fn the_shape_names_match_pythons() {
    assert_eq!(json_type_name(&json!(null)), "NoneType");
    assert_eq!(json_type_name(&json!(true)), "bool");
    assert_eq!(json_type_name(&json!(1)), "int");
    assert_eq!(json_type_name(&json!(1.5)), "float");
    assert_eq!(json_type_name(&json!("s")), "str");
    assert_eq!(json_type_name(&json!([])), "list");
    assert_eq!(json_type_name(&json!({})), "dict");
}

/// The three parse outcomes are distinct, and `Repaired` is not `Clean` — the
/// distinction the whole fix rests on.
#[test]
fn a_parse_reports_whether_it_needed_repair() {
    assert!(matches!(
        JsonAttempt::parse(r#"{"a": 1}"#, 1),
        JsonAttempt::Clean(_)
    ));
    assert!(matches!(
        JsonAttempt::parse(r#"{"a": 1,}"#, 1),
        JsonAttempt::Repaired(_)
    ));
    assert!(matches!(
        JsonAttempt::parse("not json", 1),
        JsonAttempt::Unparseable
    ));
    // A truncated value parses only by repair — which is exactly why it must not
    // be read as complete.
    assert!(matches!(
        JsonAttempt::parse(r#"{"a": 1"#, 1),
        JsonAttempt::Repaired(_)
    ));
}

// ---------------------------------------------------------------------------
// Metrics
// ---------------------------------------------------------------------------

/// The report reproduces the Python's three choices: retries appear only when
/// non-zero, elapsed only when positive, and separators are commas.
#[test]
fn the_metrics_report_matches_the_python_shape() {
    let metrics = PerformanceMetrics::new();
    metrics.add_request(1_000, 500, 2.5);
    let snapshot = metrics.snapshot();
    let report = snapshot.format_report(Some("Extractor"));
    assert_eq!(
        report,
        "=== Extractor Performance Metrics ===\n\
         Requests:     1\n\
         Tokens:       1,500 total (1,000 prompt + 500 completion)\n\
         Time:         2.50s in requests\n\
         Speed:        200.0 tokens/sec\n\
         Avg/Request:  1500 tokens"
    );

    // A retry adds its marker, and a *started* period adds the elapsed figure.
    metrics.add_retry();
    metrics.mark_start(0.0, Monotonic::from_seconds(100.0));
    metrics.mark_end(0.0, Monotonic::from_seconds(102.25));
    let report = metrics.snapshot().format_report(None);
    assert!(report.contains("(1 retries)"), "{report}");
    assert!(report.contains("2.25s elapsed"), "{report}");
    assert!(!report.contains("==="), "no title, no heading: {report}");
}

/// A snapshot is **independent and consistent**: it reads every counter at one
/// instant, so a caller comparing two of its fields cannot see a ratio that
/// never existed.
#[test]
fn a_snapshot_is_independent_and_keeps_its_clock_marks() {
    let metrics = PerformanceMetrics::new();
    metrics.mark_start(0.0, Monotonic::from_seconds(10.0));
    metrics.add_request(5, 5, 1.0);

    let snapshot = metrics.snapshot();
    // Later activity does not reach the snapshot.
    metrics.add_request(100, 100, 1.0);
    assert_eq!(snapshot.total_requests, 1);
    assert_eq!(snapshot.total_tokens, 10);

    // The monotonic marks come along, or elapsed time silently drops to the
    // wall-clock fallback.
    metrics.mark_end(0.0, Monotonic::from_seconds(12.0));
    assert_eq!(snapshot.elapsed_time_seconds(None), 0.0, "not ended yet");
    assert_eq!(
        metrics.snapshot().elapsed_time_seconds(None),
        2.0,
        "the live one ended"
    );
}

/// A snapshot rebuilt from serialised output has **no** monotonic marks — they
/// are not meaningful across processes — and falls back to the timestamps it
/// does have.
#[test]
fn a_rebuilt_snapshot_falls_back_to_wall_clock() {
    let rebuilt = MetricsSnapshot::from_json(&json!({
        "total_requests": 2,
        "total_tokens": 30,
        "total_wall_time_seconds": 1.5,
        "start_time": 100.0,
        "end_time": 104.5,
        // Present, and deliberately ignored.
        "monotonic_start": 0.0,
    }));
    assert_eq!(rebuilt.total_requests, 2);
    assert_eq!(rebuilt.elapsed_time_seconds(None), 4.5);
    assert_eq!(rebuilt.tokens_per_second(), 0.0, "no completion tokens");
}

/// The derived figures a caller reads off a snapshot are the Python's formulas,
/// including the guard against dividing by zero.
#[test]
fn derived_figures_guard_against_division_by_zero() {
    let empty = MetricsSnapshot::default();
    assert_eq!(empty.tokens_per_second(), 0.0);
    assert_eq!(empty.average_tokens_per_request(), 0.0);
    assert_eq!(empty.elapsed_time_seconds(None), 0.0);

    let metrics = PerformanceMetrics::new();
    metrics.add_request(10, 30, 2.0);
    metrics.add_request(10, 10, 2.0);
    let snapshot = metrics.snapshot();
    assert_eq!(snapshot.tokens_per_second(), 40.0 / 4.0);
    assert_eq!(snapshot.average_tokens_per_request(), 60.0 / 2.0);
}

/// `to_json` rounds each derived figure to the Python's stated places, so two
/// runs are compared on what the library published rather than division noise.
#[test]
fn to_json_rounds_where_the_python_rounds() {
    let metrics = PerformanceMetrics::new();
    metrics.add_request(1, 1, 3.0); // 1/3 tokens per second
    let value = metrics.snapshot().to_json();
    assert_eq!(value["tokens_per_second"], json!(0.33));
    assert_eq!(value["total_wall_time_seconds"], json!(3.0));
    assert_eq!(round_to(1.0 / 3.0, 3), 0.333);
    assert_eq!(round_to(2.0 / 3.0, 1), 0.7);
}

/// Group separators are **commas**, which is Python's `:,` and not the locale's:
/// a report that changes shape with `LC_NUMERIC` is a report two runs cannot be
/// compared against each other.
#[test]
fn group_separators_are_commas() {
    assert_eq!(group(0), "0");
    assert_eq!(group(999), "999");
    assert_eq!(group(1_000), "1,000");
    assert_eq!(group(999_999), "999,999");
    assert_eq!(group(1_000_000), "1,000,000");
    assert_eq!(group(-1_234_567), "-1,234,567");
}

/// **The reported attempt count is the attempts actually made**, not the retry
/// budget and not one. It is what lets a caller tell "this answered first time"
/// from "this needed three goes and only just worked" — which is the difference
/// between a healthy pipeline and one sampling at the edge of its ceiling.
#[test]
fn the_reported_attempt_count_is_what_was_made() {
    // First attempt answers.
    let mut source = Script::answering(r#"{"a": 1}"#, Some("stop"));
    let outcome = chat_json(&mut source, 5, 0.7, Some(100), 4096, false).expect("answers");
    assert_eq!(outcome.attempts, 1);
    assert_eq!(source.calls, 1);
    assert!(source.backoffs.is_empty());

    // Two failures then an answer.
    let mut source = Script::new(vec![
        Ok(response("nonsense", Some("stop"))),
        Ok(response("", Some("stop"))),
        Ok(response(r#"{"a": 1}"#, Some("stop"))),
    ]);
    let outcome = chat_json(&mut source, 5, 0.7, Some(100), 4096, false).expect("answers");
    assert_eq!(outcome.attempts, 3);
    assert_eq!(source.backoffs, vec![1, 2], "1s then 2s");

    // A failure reports the attempts made, which is the whole budget.
    let mut source = Script::new(vec![Ok(response("nonsense", Some("stop"))); 5]);
    let error = chat_json(&mut source, 3, 0.7, Some(100), 4096, false).expect_err("fails");
    match error {
        ChatJsonError::Unparseable { attempts } => assert_eq!(attempts, 3),
        other => panic!("expected Unparseable, got {other:?}"),
    }
    assert_eq!(source.calls, 3);

    // And the message names it, so an operator reading a log line knows whether
    // to raise the ceiling or look at the model.
    let mut source = Script::new(vec![Ok(response("nonsense", Some("stop"))); 5]);
    let error = chat_json(&mut source, 3, 0.7, Some(100), 4096, false).expect_err("fails");
    assert!(error.to_string().contains("3 attempt"), "{error}");
}
