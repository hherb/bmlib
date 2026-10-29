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

use bmlib::quality::pico_agent::PicoAgent;
use bmlib::quality::pico_models::PicoError;
use common::scripted_chat::ScriptedChat;

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
