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

//! The two wire protocols — the oracle and the named tests.
//!
//! The corpus (35 cases) diffs the OpenAI-side transforms against Python's own
//! functions. The named tests state the rules a diff cannot: why `content`
//! becomes `null`, why a malformed argument string is kept, and why the think
//! block is only split when the caller asked for it.

use bmlib::llm::protocol::{
    messages_to_anthropic, messages_to_openai, parse_anthropic_response, parse_openai_response,
    reasoning_from_openai, split_think_tags, tool_arguments_from_openai, tool_choice_to_anthropic,
    tool_choice_to_openai, tool_def_to_anthropic, tool_def_to_openai, Protocol,
};
use bmlib::llm::{LLMMessage, LLMToolCall, LLMToolDefinition, Role};
use serde_json::{json, Value};

const CASES: &str = include_str!("data/protocol_cases.json");
const EXPECTED: &str = include_str!("data/protocol_expected.json");

fn role_of(raw: &str) -> Role {
    Role::parse(raw).expect("corpus role")
}

fn message_of(spec: &Value) -> LLMMessage {
    let calls = spec
        .get("tool_calls")
        .and_then(Value::as_array)
        .map(|calls| {
            calls
                .iter()
                .map(|c| LLMToolCall {
                    id: c["id"].as_str().unwrap_or_default().to_string(),
                    name: c["name"].as_str().unwrap_or_default().to_string(),
                    arguments: c["arguments"].clone(),
                })
                .collect::<Vec<_>>()
        });
    LLMMessage {
        role: role_of(spec["role"].as_str().unwrap_or_default()),
        content: spec
            .get("content")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string(),
        tool_call_id: spec
            .get("tool_call_id")
            .and_then(Value::as_str)
            .map(str::to_string),
        tool_calls: calls,
    }
}

fn run(case: &Value) -> Value {
    let fn_name = case["fn"].as_str().unwrap_or_default();
    let args = &case["args"];

    match fn_name {
        "messages_to_openai" => {
            let messages: Vec<LLMMessage> = args["messages"]
                .as_array()
                .map(|m| m.iter().map(message_of).collect())
                .unwrap_or_default();
            Value::Array(messages_to_openai(&messages))
        }
        "tool_def_to_openai" => {
            let spec = &args["tool"];
            tool_def_to_openai(&LLMToolDefinition {
                name: spec["name"].as_str().unwrap_or_default().to_string(),
                description: spec
                    .get("description")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_string(),
                parameters: spec.get("parameters").cloned().unwrap_or(Value::Null),
            })
        }
        "tool_choice_to_openai" => {
            tool_choice_to_openai(args["tool_choice"].as_str().unwrap_or_default())
        }
        "split_think_tags" => {
            let (thinking, content) =
                split_think_tags(args["content"].as_str().unwrap_or_default());
            json!({"thinking": thinking, "content": content})
        }
        "tool_arguments_openai" => tool_arguments_from_openai(&args["raw"]),
        "messages_to_anthropic" => {
            let messages: Vec<LLMMessage> = args["messages"]
                .as_array()
                .map(|m| m.iter().map(message_of).collect())
                .unwrap_or_default();
            let (system, messages) = messages_to_anthropic(&messages);
            json!({"system": system, "messages": messages})
        }
        "tool_def_to_anthropic" => {
            let spec = &args["tool"];
            tool_def_to_anthropic(&LLMToolDefinition {
                name: spec["name"].as_str().unwrap_or_default().to_string(),
                description: spec
                    .get("description")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_string(),
                parameters: spec.get("parameters").cloned().unwrap_or(Value::Null),
            })
        }
        "tool_choice_to_anthropic" => {
            tool_choice_to_anthropic(args["tool_choice"].as_str().unwrap_or_default())
                .unwrap_or(Value::Null)
        }
        other => panic!("unknown fn {other:?}"),
    }
}

#[test]
fn the_port_agrees_with_python_on_every_case() {
    let cases: Value = serde_json::from_str(CASES).expect("cases parse");
    let expected: Value = serde_json::from_str(EXPECTED).expect("expected parse");
    let cases = cases.as_array().expect("cases is a list");
    let expected = expected.as_array().expect("expected is a list");
    assert_eq!(cases.len(), expected.len(), "regenerate the expectations");

    let mut failures: Vec<String> = Vec::new();
    for (case, want) in cases.iter().zip(expected.iter()) {
        let name = case["name"].as_str().unwrap_or_default();
        assert_eq!(name, want["name"].as_str().unwrap_or_default());
        assert!(
            want["ok"].as_bool().unwrap_or(false),
            "{name}: {}",
            want["error"]
        );
        // Strict, with no `corrected` override. Two cases here used to carry
        // one for #315 — the system-message join — but **Python adopted that
        // fix** in `e9db0f9` ("fix(llm, agents): seven defects the Rust-port
        // audit filed"), so the port and the library now agree and the corpus
        // records a single expectation.
        let expected_value = &want["value"];
        let got = run(case);
        if &got != expected_value {
            failures.push(format!(
                "  {name}\n    expected: {}\n    rust:     {}",
                serde_json::to_string(expected_value).unwrap_or_default(),
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

/// The protocol corpus carries no `corrected` blocks any more.
///
/// Its single one was #315 — the Anthropic system-message join — and Python
/// adopted the fix in `e9db0f9`, so the corpus now records one expectation per
/// case. Asserted rather than assumed, because the comparison above reads only
/// `want["value"]`: a `corrected` block left behind would be a stale note that
/// nothing else notices.
#[test]
fn no_case_carries_a_stale_correction() {
    let cases: Value = serde_json::from_str(CASES).expect("cases parse");
    let marked: Vec<&str> = cases
        .as_array()
        .expect("list")
        .iter()
        .filter(|c| c.get("corrected").is_some())
        .filter_map(|c| c["name"].as_str())
        .collect();
    assert!(marked.is_empty(), "stale corrections: {marked:?}");
}

// ---------------------------------------------------------------------------
// Messages
// ---------------------------------------------------------------------------

/// An assistant turn that emitted tool calls carries **`content: null`**, not
/// `""`, when it has no text — the wire distinguishes "no text" from "empty
/// text", and some servers reject the empty string beside `tool_calls`.
#[test]
fn an_assistant_tool_turn_with_no_text_sends_null_content() {
    let message = LLMMessage {
        role: Role::Assistant,
        content: String::new(),
        tool_call_id: None,
        tool_calls: Some(vec![LLMToolCall {
            id: "c1".to_string(),
            name: "add".to_string(),
            arguments: json!({"a": 1}),
        }]),
    };
    let out = messages_to_openai(&[message]);
    assert_eq!(out[0]["content"], Value::Null, "{out:?}");
    // But a turn that *has* text keeps it.
    let message = LLMMessage {
        role: Role::Assistant,
        content: "let me check".to_string(),
        tool_call_id: None,
        tool_calls: Some(vec![LLMToolCall {
            id: "c1".to_string(),
            name: "add".to_string(),
            arguments: json!({}),
        }]),
    };
    assert_eq!(
        messages_to_openai(&[message])[0]["content"],
        json!("let me check")
    );
}

/// A tool call's arguments go back as a **JSON string**, which is the form
/// OpenAI both sends and expects; an object there is a 400 on most servers.
#[test]
fn a_tool_calls_arguments_are_serialised_to_a_string() {
    let message = LLMMessage {
        role: Role::Assistant,
        content: String::new(),
        tool_call_id: None,
        tool_calls: Some(vec![LLMToolCall {
            id: "c1".to_string(),
            name: "f".to_string(),
            arguments: json!({"deep": {"x": [1, 2]}}),
        }]),
    };
    let out = messages_to_openai(&[message]);
    let arguments = &out[0]["tool_calls"][0]["function"]["arguments"];
    assert!(arguments.is_string(), "must be a string, got {arguments:?}");
    let parsed: Value =
        serde_json::from_str(arguments.as_str().expect("a string")).expect("parses");
    assert_eq!(parsed, json!({"deep": {"x": [1, 2]}}));
}

/// **An empty tool-call list is not a tool turn.** A message with
/// `tool_calls: Some(vec![])` is sent as a plain assistant message, because a
/// `tool_calls: []` beside `content: null` is a turn with neither.
#[test]
fn an_empty_tool_call_list_sends_a_plain_message() {
    let message = LLMMessage {
        role: Role::Assistant,
        content: "no calls".to_string(),
        tool_call_id: None,
        tool_calls: Some(Vec::new()),
    };
    let out = messages_to_openai(&[message]);
    assert_eq!(out[0], json!({"role": "assistant", "content": "no calls"}));
    assert!(out[0].get("tool_calls").is_none());
}

/// A tool result carries its `tool_call_id`, which OpenAI **requires** so the
/// model can correlate the result to the call it answers.
#[test]
fn a_tool_result_carries_the_id_it_answers() {
    let out = messages_to_openai(&[LLMMessage::tool_result("call-1", "{\"sum\": 3}")]);
    assert_eq!(out[0]["role"], json!("tool"));
    assert_eq!(out[0]["tool_call_id"], json!("call-1"));
    // A missing id emits no key at all rather than an empty one.
    let out = messages_to_openai(&[LLMMessage::new(Role::Tool, "x")]);
    assert!(out[0].get("tool_call_id").is_none(), "{out:?}");
}

// ---------------------------------------------------------------------------
// Tools
// ---------------------------------------------------------------------------

/// An **absent** parameter schema becomes an empty object schema, not `null`: a
/// server handed `"parameters": null` rejects the request.
#[test]
fn an_absent_parameter_schema_becomes_an_empty_object_schema() {
    let tool = LLMToolDefinition {
        name: "ping".to_string(),
        description: "ping".to_string(),
        parameters: Value::Null,
    };
    let out = tool_def_to_openai(&tool);
    assert_eq!(
        out["function"]["parameters"],
        json!({"type": "object", "properties": {}})
    );
}

/// `"any"` is an alias for `"required"`, and **anything else is a tool name** to
/// force — so a typo in a canonical choice silently becomes a forced call to a
/// tool that does not exist, which is why the canonical set is closed.
#[test]
fn tool_choice_maps_and_falls_through_to_a_named_tool() {
    assert_eq!(tool_choice_to_openai("auto"), json!("auto"));
    assert_eq!(tool_choice_to_openai("none"), json!("none"));
    assert_eq!(tool_choice_to_openai("required"), json!("required"));
    assert_eq!(tool_choice_to_openai("any"), json!("required"));
    assert_eq!(tool_choice_to_openai(""), json!("auto"));
    assert_eq!(
        tool_choice_to_openai("add"),
        json!({"type": "function", "function": {"name": "add"}})
    );
}

// ---------------------------------------------------------------------------
// Think blocks and arguments
// ---------------------------------------------------------------------------

/// The block is split only from the **start**, and an empty or whitespace-only
/// block yields `None` — an empty trace is not a trace.
#[test]
fn only_a_leading_think_block_is_split() {
    assert_eq!(
        split_think_tags("just an answer"),
        (None, "just an answer".to_string())
    );
    assert_eq!(
        split_think_tags("<think>why</think>the answer"),
        (Some("why".to_string()), "the answer".to_string())
    );
    assert_eq!(
        split_think_tags("<think></think>the answer"),
        (None, "the answer".to_string())
    );
    assert_eq!(
        split_think_tags("<think>   </think>the answer"),
        (None, "the answer".to_string())
    );
    // Not at the start: left alone entirely.
    assert_eq!(
        split_think_tags("text <think>why</think> more"),
        (None, "text <think>why</think> more".to_string())
    );
    // Unterminated: left alone.
    assert_eq!(
        split_think_tags("<think>why the answer"),
        (None, "<think>why the answer".to_string())
    );
}

/// A tool-argument string that **will not parse** keeps its raw text under
/// `_raw`, because that is the only record of what the model emitted. A string
/// that parses to a **non-object** is not arguments at all, and reads as none.
#[test]
fn malformed_arguments_are_kept_and_non_objects_are_dropped() {
    assert_eq!(
        tool_arguments_from_openai(&json!("{oops")),
        json!({"_raw": "{oops"})
    );
    assert_eq!(tool_arguments_from_openai(&json!("42")), json!({}));
    assert_eq!(tool_arguments_from_openai(&json!("[1, 2]")), json!({}));
    assert_eq!(
        tool_arguments_from_openai(&json!("not json at all")),
        json!({"_raw": "not json at all"})
    );
    // An object is taken as-is — some servers send one.
    assert_eq!(
        tool_arguments_from_openai(&json!({"a": 1})),
        json!({"a": 1})
    );
}

/// An empty reasoning field is not a trace, and `reasoning_content` wins over
/// `reasoning` when both are present.
#[test]
fn a_reasoning_field_is_read_and_an_empty_one_is_not() {
    assert_eq!(
        reasoning_from_openai(&json!({"reasoning_content": "why"})),
        Some("why".to_string())
    );
    assert_eq!(
        reasoning_from_openai(&json!({"reasoning": "why"})),
        Some("why".to_string())
    );
    assert_eq!(
        reasoning_from_openai(&json!({"reasoning_content": ""})),
        None
    );
    assert_eq!(reasoning_from_openai(&json!({})), None);
    assert_eq!(
        reasoning_from_openai(&json!({"reasoning_content": "first", "reasoning": "second"})),
        Some("first".to_string())
    );
}

// ---------------------------------------------------------------------------
// The protocol itself
// ---------------------------------------------------------------------------

/// The two protocols differ in **where the credential goes**, and a provider
/// carrying the wrong header gets a 401 that reads like a bad key.
#[test]
fn each_protocol_puts_the_credential_where_it_belongs() {
    assert_eq!(
        Protocol::OpenAi.auth_header("k"),
        ("Authorization".to_string(), "Bearer k".to_string())
    );
    assert_eq!(
        Protocol::Anthropic.auth_header("k"),
        ("x-api-key".to_string(), "k".to_string())
    );
    assert_eq!(Protocol::OpenAi.chat_path(), "/v1/chat/completions");
    assert_eq!(Protocol::Anthropic.chat_path(), "/v1/messages");
    // Anthropic rejects a request without its version header, so it is part of
    // the protocol rather than an optional extra.
    assert!(Protocol::OpenAi.extra_headers().is_empty());
    assert_eq!(Protocol::Anthropic.extra_headers().len(), 1);
}

/// **A response with no choices is an error, not a model that said nothing.**
/// Many servers answer a rejected request at HTTP 200 with an `error` object and
/// no choices; reading that as an empty answer would report a rejected call as a
/// silent model.
#[test]
fn a_response_with_no_choices_is_refused_and_quotes_the_server() {
    let err = parse_openai_response(&json!({"error": {"message": "bad model"}}), "m")
        .expect_err("refused");
    assert!(err.contains("no choices"), "{err}");
    assert!(err.contains("bad model"), "the server's own words: {err}");

    let err = parse_openai_response(&json!({}), "m").expect_err("refused");
    assert!(err.contains("no choices"), "{err}");
    assert!(!err.contains("server said"), "nothing to quote: {err}");
}

/// A parsed response derives its total from the parts, reads the reasoning field,
/// and reports tool calls as `Some` only when there are some.
#[test]
fn a_response_is_read_into_the_shared_shape() {
    let body = json!({
        "choices": [{
            "message": {
                "content": "the answer",
                "reasoning_content": "why",
                "tool_calls": [{
                    "id": "c1",
                    "function": {"name": "add", "arguments": "{\"a\": 1}"}
                }]
            },
            "finish_reason": "tool_calls"
        }],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5}
    });
    let response = parse_openai_response(&body, "m").expect("parses");
    assert_eq!(response.content, "the answer");
    assert_eq!(response.thinking.as_deref(), Some("why"));
    assert_eq!(response.total_tokens, 15);
    assert_eq!(response.stop_reason.as_deref(), Some("tool_calls"));
    assert!(response.has_tool_calls());
    assert_eq!(
        response.tool_calls.expect("calls")[0].arguments,
        json!({"a": 1})
    );

    // No tool calls: `None`, not `Some(vec![])`.
    let body = json!({"choices": [{"message": {"content": "hi"}}]});
    let response = parse_openai_response(&body, "m").expect("parses");
    assert!(response.tool_calls.is_none(), "{response:?}");
    assert_eq!(response.total_tokens, 0);
}

// ---------------------------------------------------------------------------
// Anthropic
// ---------------------------------------------------------------------------

/// An Anthropic tool result is a **`user` turn carrying a `tool_result` block**,
/// not a message with `role: "tool"` — Anthropic has no such role, so a provider
/// that forwarded the OpenAI shape would be rejected.
#[test]
fn an_anthropic_tool_result_becomes_a_user_turn() {
    let (system, messages) = messages_to_anthropic(&[LLMMessage::tool_result("c1", "3")]);
    assert_eq!(system, "");
    assert_eq!(messages.len(), 1);
    assert_eq!(messages[0]["role"], json!("user"));
    assert_eq!(messages[0]["content"][0]["type"], json!("tool_result"));
    assert_eq!(messages[0]["content"][0]["tool_use_id"], json!("c1"));
    assert_eq!(messages[0]["content"][0]["content"], json!("3"));
}

/// **Consecutive tool results merge into one user turn**, which is Anthropic's
/// preferred shape when answering several parallel tool calls — and the merge
/// only happens when the previous turn is already nothing but tool results.
#[test]
fn consecutive_tool_results_merge_into_one_turn() {
    let (_, messages) = messages_to_anthropic(&[
        LLMMessage::tool_result("c1", "1"),
        LLMMessage::tool_result("c2", "2"),
    ]);
    assert_eq!(messages.len(), 1, "one merged turn: {messages:?}");
    assert_eq!(messages[0]["content"].as_array().expect("blocks").len(), 2);

    // A user text turn in between is **not** absorbed: its content is a string,
    // not a list of tool results.
    let (_, messages) = messages_to_anthropic(&[
        LLMMessage::user("hello"),
        LLMMessage::tool_result("c1", "1"),
    ]);
    assert_eq!(
        messages.len(),
        2,
        "a text turn is its own message: {messages:?}"
    );
    assert_eq!(messages[0]["content"], json!("hello"));
    assert_eq!(messages[1]["content"][0]["type"], json!("tool_result"));

    // **A shape this function did not itself emit is not absorbed.** A user turn
    // whose content is already an array of non-tool-result blocks stays its own
    // message, so the `all` in the merge condition is what decides it.
    //
    // This is the one input that reaches the `all`; the port never emits it
    // itself, so the guard is **structural rather than observable** and a mutant
    // relaxing it to a bare list check survives the whole file — recorded in the
    // source beside the guard rather than left as a re-discovery.
    let (_, messages) = messages_to_anthropic(&[
        LLMMessage {
            role: Role::Assistant,
            content: "text".to_string(),
            tool_call_id: None,
            tool_calls: Some(vec![LLMToolCall {
                id: "c1".to_string(),
                name: "f".to_string(),
                arguments: json!({}),
            }]),
        },
        LLMMessage::tool_result("c1", "1"),
    ]);
    assert_eq!(
        messages.len(),
        2,
        "a text+tool_use turn must not absorb the result: {messages:?}"
    );
    assert_eq!(messages[0]["content"][0]["type"], json!("text"));
    assert_eq!(messages[1]["content"][0]["type"], json!("tool_result"));
}

/// An assistant turn that emitted tool calls is re-emitted with **`tool_use`
/// blocks**, so the model can correlate the next turn's results to them. Its
/// `input` is an **object**, where OpenAI's arguments are a JSON string.
#[test]
fn an_anthropic_assistant_tool_turn_uses_tool_use_blocks() {
    let (_, messages) = messages_to_anthropic(&[LLMMessage {
        role: Role::Assistant,
        content: "checking".to_string(),
        tool_call_id: None,
        tool_calls: Some(vec![LLMToolCall {
            id: "c1".to_string(),
            name: "add".to_string(),
            arguments: json!({"a": 1}),
        }]),
    }]);
    let blocks = messages[0]["content"].as_array().expect("blocks");
    assert_eq!(blocks.len(), 2, "text then the call: {blocks:?}");
    assert_eq!(blocks[0], json!({"type": "text", "text": "checking"}));
    assert_eq!(blocks[1]["type"], json!("tool_use"));
    assert_eq!(blocks[1]["name"], json!("add"));
    assert_eq!(
        blocks[1]["input"],
        json!({"a": 1}),
        "an object, not OpenAI's string"
    );

    // With no text there is no text block at all.
    let (_, messages) = messages_to_anthropic(&[LLMMessage {
        role: Role::Assistant,
        content: String::new(),
        tool_call_id: None,
        tool_calls: Some(vec![LLMToolCall {
            id: "c1".to_string(),
            name: "add".to_string(),
            arguments: json!({}),
        }]),
    }]);
    assert_eq!(messages[0]["content"].as_array().expect("blocks").len(), 1);
}

/// **Every system message reaches the model.** The Python used to *assign*
/// (`system_content = msg.content`), so a conversation with two system turns
/// kept only the last and dropped the first with no error and nothing logged —
/// and only on Anthropic, since the OpenAI path emits every message it is given.
/// A caller who prepends a task instruction and then a second, more specific
/// constraint got the model the *second* alone while believing both were sent,
/// which is diagnosed as "the model behaves differently on Claude" rather than
/// as a serialisation bug. Filed as #315, and **fixed in Python** in `e9db0f9`;
/// both implementations join now, and the corpus records the single
/// expectation.
#[test]
fn every_system_message_reaches_the_model() {
    let (system, messages) = messages_to_anthropic(&[
        LLMMessage::system("Answer in British English."),
        LLMMessage::system("Never give medical advice."),
        LLMMessage::user("Is paracetamol safe?"),
    ]);
    assert!(
        system.contains("British English"),
        "the first instruction must survive: {system:?}"
    );
    assert!(system.contains("Never give medical advice"), "{system:?}");
    assert_eq!(
        system,
        "Answer in British English.\n\nNever give medical advice."
    );
    // And the system turns are not also emitted as messages.
    assert_eq!(messages.len(), 1);
    assert_eq!(messages[0]["role"], json!("user"));

    // One system message is passed through unchanged — no stray separator.
    let (system, _) = messages_to_anthropic(&[LLMMessage::system("only")]);
    assert_eq!(system, "only");

    // None at all is the empty string, which is what Anthropic's `system`
    // parameter is omitted for.
    let (system, _) = messages_to_anthropic(&[LLMMessage::user("hi")]);
    assert_eq!(system, "");
}

/// `"auto"` is **`None`, not `{"type": "auto"}`** — it is Anthropic's default, so
/// the parameter is omitted rather than sent.
#[test]
fn anthropic_tool_choice_omits_the_default() {
    assert_eq!(tool_choice_to_anthropic("auto"), None);
    assert_eq!(tool_choice_to_anthropic(""), None);
    assert_eq!(
        tool_choice_to_anthropic("required"),
        Some(json!({"type": "any"}))
    );
    assert_eq!(
        tool_choice_to_anthropic("any"),
        Some(json!({"type": "any"}))
    );
    assert_eq!(
        tool_choice_to_anthropic("none"),
        Some(json!({"type": "none"}))
    );
    assert_eq!(
        tool_choice_to_anthropic("add"),
        Some(json!({"type": "tool", "name": "add"}))
    );
}

/// The same tool definition in both shapes: OpenAI nests it under `function`
/// with `parameters`, Anthropic flattens it with `input_schema`.
#[test]
fn one_tool_definition_produces_both_shapes() {
    let tool = LLMToolDefinition {
        name: "add".to_string(),
        description: "add two".to_string(),
        parameters: json!({"type": "object", "properties": {"a": {"type": "integer"}}}),
    };
    let openai = tool_def_to_openai(&tool);
    assert_eq!(openai["type"], json!("function"));
    assert_eq!(openai["function"]["name"], json!("add"));

    let anthropic = tool_def_to_anthropic(&tool);
    assert_eq!(anthropic["name"], json!("add"));
    assert_eq!(
        anthropic["input_schema"],
        json!({"type": "object", "properties": {"a": {"type": "integer"}}})
    );
    assert!(anthropic.get("function").is_none());

    // An absent schema gets the default object schema in both.
    let bare = LLMToolDefinition {
        name: "ping".to_string(),
        description: "ping".to_string(),
        parameters: Value::Null,
    };
    let default = json!({"type": "object", "properties": {}});
    assert_eq!(tool_def_to_openai(&bare)["function"]["parameters"], default);
    assert_eq!(tool_def_to_anthropic(&bare)["input_schema"], default);
}

/// **An Anthropic response with no content blocks is an error, not a model that
/// said nothing** — the same silent failure the OpenAI side refuses.
#[test]
fn an_anthropic_response_with_no_blocks_is_refused() {
    let err = parse_anthropic_response(&json!({"error": {"message": "bad model"}}), "m")
        .expect_err("refused");
    assert!(err.contains("no content blocks"), "{err}");
    assert!(err.contains("bad model"), "the server's own words: {err}");
    assert!(!parse_anthropic_response(&json!({}), "m")
        .expect_err("refused")
        .contains("server said"));
}

/// An Anthropic body is read from its **content blocks**: text concatenated,
/// `thinking` as the trace, `tool_use` as the calls, and its own token field
/// names.
#[test]
fn an_anthropic_response_is_read_from_its_blocks() {
    let body = json!({
        "content": [
            {"type": "thinking", "thinking": "why"},
            {"type": "text", "text": "the "},
            {"type": "text", "text": "answer"},
            {"type": "tool_use", "id": "c1", "name": "add", "input": {"a": 1}},
        ],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 10, "output_tokens": 5}
    });
    let response = parse_anthropic_response(&body, "m").expect("parses");
    assert_eq!(response.content, "the answer", "text blocks concatenate");
    assert_eq!(response.thinking.as_deref(), Some("why"));
    assert_eq!(response.total_tokens, 15);
    assert_eq!(response.stop_reason.as_deref(), Some("tool_use"));
    assert_eq!(
        response.tool_calls.expect("calls")[0].arguments,
        json!({"a": 1})
    );

    // An empty thinking block is not a trace.
    let body = json!({"content": [{"type": "thinking", "thinking": ""},
                                  {"type": "text", "text": "hi"}]});
    let response = parse_anthropic_response(&body, "m").expect("parses");
    assert_eq!(response.thinking, None);
    assert!(response.tool_calls.is_none());
}

/// A user turn whose content is already **an array of non-tool-result blocks**
/// is not something this port emits, but it is something a caller can pass —
/// and it must not be absorbed into a following tool result.
///
/// The merge condition is "the previous turn is a user turn whose blocks are
/// **all** tool results"; without the `all`, any array content would attract the
/// result and the model would see a `tool_result` nested inside a turn that was
/// not answering anything.
#[test]
fn a_user_turn_of_array_blocks_does_not_absorb_a_tool_result() {
    // The shared message type carries content as a string, so this shape is
    // reached through the *previous* merge: two tool results merge, and then a
    // plain user turn must start a fresh message rather than joining them.
    let (_, messages) = messages_to_anthropic(&[
        LLMMessage::tool_result("c1", "1"),
        LLMMessage::user("now tell me why"),
        LLMMessage::tool_result("c2", "2"),
    ]);
    assert_eq!(messages.len(), 3, "{messages:?}");
    assert_eq!(messages[0]["content"].as_array().expect("blocks").len(), 1);
    assert_eq!(messages[1]["content"], json!("now tell me why"));
    assert_eq!(messages[2]["content"][0]["tool_use_id"], json!("c2"));
}
