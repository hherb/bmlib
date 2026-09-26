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

//! What a call cost: the price tables and the arithmetic, against Python's.
//!
//! The oracle diffs 27 models over five providers and 192 cost cases, including
//! the fallback path for a model no table names. The named tests state the rules
//! the diff cannot: why a local server is free where an unknown hosted model is
//! not, and that a call is recorded only when it succeeded.

use bmlib::llm::pricing::{
    calculate_cost, fallback_pricing, model_pricing, priced_models, provider_is_local,
};
use serde_json::Value;

const EXPECTED: &str = include_str!("data/cost_expected.json");

/// Compare two money values, since the two languages need not round alike.
///
/// A relative tolerance, because the arithmetic is `tokens / 1_000_000 * rate` and
/// an absolute epsilon would be wrong at both ends of the range: the smallest case
/// here is 6e-06 and a one-dollar call would tolerate an absolute 1e-12 carelessly.
fn money_eq(a: f64, b: f64) -> bool {
    if a == b {
        return true;
    }
    let scale = a.abs().max(b.abs());
    (a - b).abs() <= scale * 1e-12
}

#[test]
fn the_port_agrees_with_python_on_every_price_and_cost() {
    let expected: Value = serde_json::from_str(EXPECTED).expect("expected parse");
    let tables = expected["tables"].as_object().expect("tables");
    let mut failures: Vec<String> = Vec::new();

    // --- the tables themselves -------------------------------------------------
    for (provider, models) in tables {
        let models = models.as_object().expect("a model map");
        for (model, rates) in models {
            let want_in = rates[0].as_f64().expect("input rate");
            let want_out = rates[1].as_f64().expect("output rate");
            let got = model_pricing(provider, model);
            if !money_eq(got.input_cost, want_in) || !money_eq(got.output_cost, want_out) {
                failures.push(format!(
                    "  rate {provider}/{model}: python ({want_in}, {want_out}) rust ({}, {})",
                    got.input_cost, got.output_cost
                ));
            }
        }
    }

    // The port must not price a model Python does not, which is how a typo in a
    // generated row would show up as an extra model rather than a wrong rate.
    let priced = priced_models().len();
    let expected_total: usize = tables
        .values()
        .map(|m| m.as_object().map_or(0, serde_json::Map::len))
        .sum();
    assert_eq!(
        priced, expected_total,
        "the port prices {priced} models where Python prices {expected_total}"
    );

    // --- the arithmetic --------------------------------------------------------
    for case in expected["costs"].as_array().expect("costs") {
        let provider = case["provider"].as_str().expect("provider");
        let model = case["model"].as_str().expect("model");
        let input = case["input_tokens"].as_i64().expect("input");
        let output = case["output_tokens"].as_i64().expect("output");
        let want = case["cost"].as_f64().expect("cost");
        let got = calculate_cost(provider, model, input, output);
        if !money_eq(got, want) {
            failures.push(format!(
                "  cost {provider}/{model} in={input} out={output}: python {want} rust {got}"
            ));
        }
    }

    assert!(
        failures.is_empty(),
        "{} divergences:\n{}",
        failures.len(),
        failures.join("\n")
    );
}

/// **A model the table does not name is billed at its provider's fallback**, not at
/// zero. Zero for an unknown model is indistinguishable from a free one, so a
/// stale table would silently report paid calls as costing nothing — which is
/// worse than a slightly wrong estimate.
#[test]
fn an_unknown_hosted_model_takes_the_provider_fallback() {
    let unknown = model_pricing("anthropic", "claude-does-not-exist-yet");
    let fallback = fallback_pricing("anthropic");
    assert_eq!(unknown, fallback);
    assert_eq!(fallback.input_cost, 3.0);
    assert_eq!(fallback.output_cost, 15.0);
    // Anthropic's fallback is its Sonnet rate, which is *not* the shared one.
    assert_ne!(fallback, fallback_pricing("openai"));
    assert_eq!(fallback_pricing("openai").input_cost, 1.0);

    // And the cost is non-zero, which is the property: an unpriced *hosted* model
    // must not look free.
    let cost = calculate_cost("anthropic", "claude-does-not-exist-yet", 1_000_000, 0);
    assert!(
        cost > 0.0,
        "an unknown hosted model is not free, got {cost}"
    );
}

/// **A local server is free, and that is a different rule from the fallback.**
/// A model on the caller's own machine genuinely costs nothing; reporting an
/// estimate for it would invent a charge. Collapsing the two rules makes one of
/// them wrong whichever way it is collapsed.
#[test]
fn a_local_provider_is_free_rather_than_estimated() {
    // Ollama is the port's local provider.
    assert!(provider_is_local("ollama"));
    assert!(!provider_is_local("anthropic"));

    let free = model_pricing("ollama", "llama3:70b");
    assert_eq!(free.input_cost, 0.0);
    assert_eq!(free.output_cost, 0.0);
    assert_eq!(
        calculate_cost("ollama", "llama3:70b", 1_000_000, 1_000_000),
        0.0
    );

    // Note it is free even though `ollama` has no row in the fallback table — the
    // local test runs first, which is what keeps an unpriced local model from
    // picking up a hosted provider's estimate.
    assert_eq!(fallback_pricing("ollama").input_cost, 0.0);
}

/// The arithmetic is the Python's unit: **dollars per million tokens**, so a
/// million input tokens cost exactly the input rate.
#[test]
fn a_million_tokens_costs_the_stated_rate() {
    let rates = model_pricing("openai", "gpt-4o");
    assert_eq!(rates.input_cost, 2.5);
    assert_eq!(rates.output_cost, 10.0);
    assert_eq!(calculate_cost("openai", "gpt-4o", 1_000_000, 0), 2.5);
    assert_eq!(calculate_cost("openai", "gpt-4o", 0, 1_000_000), 10.0);
    // Both directions at once.
    assert_eq!(
        calculate_cost("openai", "gpt-4o", 1_000_000, 1_000_000),
        12.5
    );
    // Nothing costs nothing.
    assert_eq!(calculate_cost("openai", "gpt-4o", 0, 0), 0.0);
    // A fraction of a cent is still a fraction, not rounded to zero.
    let tiny = calculate_cost("openai", "gpt-4o", 1, 0);
    assert!(tiny > 0.0 && tiny < 1e-5, "got {tiny}");
}

/// A free model is priced at zero in the table rather than being absent from it —
/// `gemini-2.0-flash-lite` is the case, and an absent entry would take the
/// provider's fallback and report a charge for a free model.
#[test]
fn a_zero_rated_model_is_priced_not_missing() {
    let rates = model_pricing("gemini", "gemini-2.0-flash-lite");
    assert_eq!(rates.input_cost, 0.0);
    assert_eq!(rates.output_cost, 0.0);
    // It is in the table, so it is not the fallback.
    assert_ne!(rates, fallback_pricing("gemini"));
    assert_eq!(
        calculate_cost("gemini", "gemini-2.0-flash-lite", 1_000_000, 0),
        0.0
    );
}

/// Every provider the port registers with a hosted model has a fallback, so an
/// unknown model cannot silently fall through to zero.
#[test]
fn every_hosted_provider_states_a_fallback() {
    for provider in ["anthropic", "openai", "deepseek", "mistral", "gemini"] {
        let rates = fallback_pricing(provider);
        assert!(
            rates.input_cost > 0.0 && rates.output_cost > 0.0,
            "{provider} has no usable fallback: {rates:?}"
        );
    }
    // And a provider the crate does not know is zero rather than a panic, since a
    // caller may name a server this port has no spec for.
    assert_eq!(fallback_pricing("some-custom-server").input_cost, 0.0);
}

// ---------------------------------------------------------------------------
// The recording path
// ---------------------------------------------------------------------------

/// A client that answers one canned chat response.
struct OneShot {
    body: String,
    status: u16,
}

impl bmlib::publications::fetchers::HttpClient for OneShot {
    fn get(
        &self,
        url: &str,
    ) -> Result<
        bmlib::publications::fetchers::HttpResponse,
        bmlib::publications::fetchers::FetchError,
    > {
        Ok(bmlib::publications::fetchers::HttpResponse::ok(format!(
            "GET {url}"
        )))
    }

    fn post_json(
        &self,
        _url: &str,
        _body: &serde_json::Value,
        _headers: &std::collections::BTreeMap<String, String>,
    ) -> Result<
        bmlib::publications::fetchers::HttpResponse,
        bmlib::publications::fetchers::FetchError,
    > {
        Ok(bmlib::publications::fetchers::HttpResponse::from_bytes(
            self.status,
            self.body.clone().into_bytes(),
        ))
    }
}

/// Serialises the recording tests.
///
/// **The tracker they exercise is process-wide, so this lock is not optional.**
/// Two tests recording concurrently interleave their `before`/`after` snapshots and
/// each sees the other's call, which reads as a wrong count in whichever one loses
/// the race. Taken rather than reset: resetting a global while another test records
/// would corrupt *its* totals, which is the same defect moved rather than removed.
fn recording_lock() -> std::sync::MutexGuard<'static, ()> {
    static LOCK: std::sync::OnceLock<std::sync::Mutex<()>> = std::sync::OnceLock::new();
    LOCK.get_or_init(|| std::sync::Mutex::new(()))
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
}

/// The tracker's totals, read under its lock.
fn totals() -> (i64, i64, i64, f64) {
    bmlib::llm::with_token_tracker(|tracker| {
        let s = tracker.get_summary();
        (
            s.call_count,
            s.total_input_tokens,
            s.total_output_tokens,
            s.total_cost_usd,
        )
    })
}

/// What the tracker gained across one closure.
///
/// **A delta and not an absolute**, because the tracker is process-wide and these
/// tests run concurrently: asserting `call_count == 1` asserts what the *process*
/// has seen, which another test's call can change between the read and the
/// assertion. The property here is what this call added.
fn recorded_during<R>(f: impl FnOnce() -> R) -> (R, (i64, i64, i64, f64)) {
    let before = totals();
    let result = f();
    let after = totals();
    (
        result,
        (
            after.0 - before.0,
            after.1 - before.1,
            after.2 - before.2,
            after.3 - before.3,
        ),
    )
}

/// The OpenAI wire shape for one answer.
fn openai_body(input: i64, output: i64) -> String {
    serde_json::json!({
        "choices": [{"message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": input, "completion_tokens": output},
    })
    .to_string()
}

/// A client that answers with one canned body, keyed so no environment is read.
fn client(body: String, status: u16) -> bmlib::llm::LlmClient {
    use std::sync::Arc;
    let mut client = bmlib::llm::LlmClient::new(Arc::new(OneShot { body, status }));
    // Explicit rather than from the environment, as the other LLM tests do: a test
    // that reads a developer's real key depends on their shell.
    client.api_key = Some("k".to_string());
    client
}

fn chat_request() -> bmlib::llm::ChatRequest {
    bmlib::llm::ChatRequest {
        messages: vec![bmlib::llm::LLMMessage::user("hello")],
        // The **explicit `provider:model` form**, so the test does not depend on
        // the client default — the model this is priced against must be the one
        // that answered.
        model: Some("openai:gpt-4o".to_string()),
        ..bmlib::llm::ChatRequest::default()
    }
}

/// **A successful call is recorded**, which is the whole point of wiring the
/// tracker in: before this it was ported, tested and fed by nothing, so the
/// library could report no usage or cost at all.
#[test]
fn a_successful_call_is_recorded_with_its_cost() {
    let _guard = recording_lock();
    let client = client(openai_body(1_000_000, 0), 200);
    let (response, (calls, input, output, cost)) =
        recorded_during(|| client.chat(&chat_request()).expect("answers"));
    assert_eq!(response.input_tokens, 1_000_000);

    assert_eq!(calls, 1, "the call was recorded");
    assert_eq!(input, 1_000_000);
    assert_eq!(output, 0);
    // `gpt-4o` is $2.50 per million input tokens, so a million costs exactly that.
    // This is the assertion that ties the recording to the *pricing*, not merely
    // to a counter.
    assert!(money_eq(cost, 2.5), "cost was {cost}");
    // The model is recorded as `provider:model`, the Python's own spelling.
    assert!(
        bmlib::llm::with_token_tracker(|t| t.get_summary().by_model.contains_key("openai:gpt-4o")),
        "the model is recorded under the provider-qualified name"
    );
}

/// **A failed call is not recorded.** A rejected request produced no output
/// tokens, and counting it would inflate the totals with calls that returned
/// nothing — which is why the Python records after the response is parsed.
#[test]
fn a_failed_call_is_not_recorded() {
    let _guard = recording_lock();
    let client = client(r#"{"error": {"message": "bad key"}}"#.to_string(), 401);
    let (result, (calls, input, output, cost)) = recorded_during(|| client.chat(&chat_request()));
    assert!(result.is_err(), "the call failed");
    assert_eq!(calls, 0, "a failed call is not usage");
    assert_eq!(input + output, 0);
    assert_eq!(cost, 0.0);
}

/// A body that will not parse is likewise not recorded, for the same reason.
#[test]
fn an_unparseable_response_is_not_recorded() {
    let _guard = recording_lock();
    let client = client("not json at all".to_string(), 200);
    let (result, (calls, ..)) = recorded_during(|| client.chat(&chat_request()));
    assert!(result.is_err());
    assert_eq!(calls, 0, "an unparseable answer is not usage");
}

/// Two calls accumulate, and the per-model breakdown keeps them apart.
#[test]
fn calls_accumulate_and_are_broken_out_by_model() {
    let _guard = recording_lock();
    let client = client(openai_body(500_000, 250_000), 200);
    let (_, (calls, input, output, cost)) = recorded_during(|| {
        client.chat(&chat_request()).expect("first");
        client.chat(&chat_request()).expect("second");
    });

    assert_eq!(calls, 2);
    assert_eq!(input, 1_000_000);
    assert_eq!(output, 500_000);
    // 1M input at $2.50 + 0.5M output at $10.00 = $2.50 + $5.00.
    assert!(money_eq(cost, 7.5), "cost was {cost}");
}
