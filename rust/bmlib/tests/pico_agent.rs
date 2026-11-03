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

//! The Rust PICO agent's own rules. Its agreement with Python is
//! `tests/pico_oracle.rs`; these are the paths that oracle cannot reach.

mod common;

use bmlib::context_processor::llm_processor::ContextModel;
use bmlib::context_processor::ProcessingConfig;
use bmlib::quality::pico_agent::PicoAgent;
use bmlib::quality::pico_models::{PicoError, PicoPrepared};
use common::scripted_chat::ScriptedChat;
use serde_json::Value;

const REPLY: &str = r#"{"is_suitable": true}"#;

#[test]
fn oversized_text_without_a_condenser_is_condensation_failed() {
    let mut chat = ScriptedChat::answering(REPLY, Some("stop"));
    let text = "x".repeat(60_000);
    let result = PicoAgent::new(&mut chat).check_suitability("A trial", &text);

    assert!(
        matches!(result, Err(PicoError::CondensationFailed(_))),
        "{result:?}"
    );
    assert!(chat.seen.is_empty());
}

#[test]
fn invalid_similarity_makes_no_call() {
    for bad in [0.0, 1.5, -0.1, f64::NAN] {
        let mut chat = ScriptedChat::answering(REPLY, Some("stop"));
        let result = PicoAgent::new(&mut chat).extract("A trial", "Some text.", bad);

        assert!(
            matches!(result, Err(PicoError::InvalidSimilarity(_))),
            "{bad}: {result:?}"
        );
        assert!(chat.seen.is_empty());
    }
}

#[test]
fn a_paper_prepared_for_a_larger_budget_is_refused() {
    let text = "Filler sentence. ".repeat(20);
    let mut wide = ScriptedChat::answering(REPLY, Some("stop"));
    let prepared = PicoAgent::new(&mut wide)
        .prepare("A trial", &text)
        .expect("sent whole under the default budget");

    let mut chat = ScriptedChat::answering(REPLY, Some("stop"));
    let mut agent = PicoAgent::new(&mut chat)
        .with_condense_config(ProcessingConfig::default().with_max_context_chars(100));
    let over = PicoError::PreparedOverBudget {
        chars: prepared.sent().chars().count(),
        budget: 100,
    };
    assert_eq!(
        agent.check_suitability_prepared(&prepared),
        Err(over.clone())
    );
    assert_eq!(agent.extract_prepared(&prepared, 0.9), Err(over.clone()));
    assert_eq!(over.kind(), "prepared_over_budget");
    assert!(over
        .to_string()
        .contains("over this agent's 100-character budget"));
    drop(agent);
    assert!(chat.seen.is_empty());
}

#[test]
fn a_paper_condensed_for_a_smaller_budget_is_refused() {
    // At the budget exactly: `prepare` condenses only a text over it.
    let data = serde_json::json!({
        "title": "A trial",
        "text": format!(" {}", "x".repeat(100)),
        "digest": "a digest",
        "condensation_status": "completed",
        "notes": [],
    });
    let prepared = PicoPrepared::from_json(&data).expect("a stored condensed paper");

    let mut chat = ScriptedChat::answering(REPLY, Some("stop"));
    let mut agent = PicoAgent::new(&mut chat)
        .with_condense_config(ProcessingConfig::default().with_max_context_chars(100));
    let under = PicoError::PreparedUnderBudget {
        chars: 100,
        budget: 100,
    };
    assert_eq!(
        agent.check_suitability_prepared(&prepared),
        Err(under.clone())
    );
    assert_eq!(agent.extract_prepared(&prepared, 0.9), Err(under.clone()));
    assert_eq!(under.kind(), "prepared_under_budget");
    assert_eq!(
        under.to_string(),
        "the prepared text was condensed from 100 characters, which fit this agent's \
         100-character budget whole: prepare it with this agent"
    );
    drop(agent);
    assert!(chat.seen.is_empty());
}

#[test]
fn a_prepared_extraction_refuses_an_invalid_similarity() {
    let mut chat = ScriptedChat::answering(REPLY, Some("stop"));
    let mut agent = PicoAgent::new(&mut chat);
    let prepared = agent.prepare("A trial", "Some text.").expect("prepared");
    for bad in [0.0, 1.5, f64::NAN] {
        let result = agent.extract_prepared(&prepared, bad);
        assert!(
            matches!(result, Err(PicoError::InvalidSimilarity(_))),
            "{bad}: {result:?}"
        );
    }
    drop(agent);
    assert!(chat.seen.is_empty());
}

#[test]
fn empty_input_makes_no_call() {
    let mut chat = ScriptedChat::answering(REPLY, Some("stop"));
    let result = PicoAgent::new(&mut chat).check_suitability("  ", "\u{1c} ");

    assert_eq!(result, Err(PicoError::EmptyInput));
    assert!(chat.seen.is_empty());
}

#[test]
fn a_transport_failure_is_request_failed() {
    let mut chat = ScriptedChat::new(common::scripted_chat::Script::new(vec![Err(
        "connection refused".to_string(),
    )]));
    let result = PicoAgent::new(&mut chat).check_suitability("A trial", "Some text.");

    assert_eq!(
        result,
        Err(PicoError::RequestFailed(
            "failed after 1 attempt(s): connection refused".to_string()
        ))
    );
}

struct NoModel;

impl ContextModel for NoModel {
    fn complete(&self, _: &str, _: f64, _: i64) -> Result<String, String> {
        Err("unused".to_string())
    }

    fn complete_json(&self, _: &str, _: f64, _: i64) -> Result<Value, String> {
        Err("unused".to_string())
    }
}

/// The PICO condensation prompts carry both placeholders, so the condenser a
/// long paper needs can be built: nothing else constructs it.
#[test]
fn the_pico_condenser_builds() {
    PicoAgent::pico_condenser(&NoModel, 0.1, 4096).expect("both prompts carry both placeholders");
}
