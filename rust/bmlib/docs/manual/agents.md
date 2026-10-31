# bmlib::agents — JSON Answers and Per-Agent Metrics

Asking a model for JSON with retries, knowing when the answer was cut off, and
counting what one agent did. The Python page is
[agents.md](../../../../docs/manual/agents.md); this one describes the Rust API
and where it differs.

| Need | Feature |
|---|---|
| `chat_json`, `ChatSource`, `JsonAttempt`, `PerformanceMetrics` | none |
| `quality::LlmChat`, the binding to a real `LlmClient` | none (an `HttpClient` from you) |

**There is no `BaseAgent` class.** Python's agent bundles a client, a model,
sampling defaults, templates, metrics and `chat_json` into one object. Here
those are separate pieces: the retry loop is the function `chat_json` over a
`ChatSource` (where its responses come from), `quality::JsonChat` /
`quality::LlmChat` bind it to an `LlmClient`, prompts are rendered by
[`bmlib::templates`](templates.md) or `quality::format_template`, and
`PerformanceMetrics` is a value you own and feed. Nothing records into it for
you, and there are no `embed`, `test_connection` or message-helper methods
(use `LLMMessage::system` / `user` / `assistant` from [`bmlib::llm`](llm.md)).

## The retry loop

`chat_json(source, max_retries, temperature, max_tokens, default_max_tokens,
require_dict)` calls `source.chat(attempt)` until one answer is usable or
`max_retries` attempts (total, not extra; at least one) are spent, calling
`source.backoff(attempt)` before every attempt after the first. A test
implements `ChatSource` with a script and a backoff that does not sleep:

```rust
use bmlib::agents::{chat_json, ChatJsonError, ChatSource, JsonAttempt};
use bmlib::llm::LLMResponse;
use serde_json::json;

struct Script {
    replies: Vec<(&'static str, Option<&'static str>)>, // content, stop reason
    calls: usize,
    waits: Vec<usize>,
}

impl ChatSource for Script {
    fn chat(&mut self, _attempt: usize) -> Result<LLMResponse, String> {
        let (content, stop) = self.replies[self.calls.min(self.replies.len() - 1)];
        self.calls += 1;
        Ok(LLMResponse {
            content: content.into(),
            stop_reason: stop.map(str::to_string),
            ..Default::default()
        })
    }
    fn backoff(&mut self, attempt: usize) {
        self.waits.push(attempt); // a real source sleeps 2^(attempt-1) seconds
    }
}

let script = |replies| Script { replies, calls: 0, waits: Vec::new() };

// Empty, then a clean answer: retried once.
let mut source = script(vec![("", Some("stop")), (r#"{"design": "RCT"}"#, Some("stop"))]);
let outcome = chat_json(&mut source, 3, 0.0, None, 512, true)?;
assert_eq!(outcome.value, json!({"design": "RCT"}));
assert_eq!((outcome.attempts, outcome.repaired), (2, false));
assert_eq!(source.waits, vec![1]);

// Malformed but finished: repaired, used, and reported as repaired.
let mut source = script(vec![(r#"{"n": 12,}"#, Some("stop"))]);
assert!(chat_json(&mut source, 3, 0.0, None, 512, true)?.repaired);

// Cut off at the output ceiling, at temperature 0: one attempt, no retry.
let mut source = script(vec![(r#"{"summary": "The study found that metformin"#, Some("length"))]);
let error = chat_json(&mut source, 3, 0.0, Some(256), 512, true).unwrap_err();
assert_eq!(error, ChatJsonError::Truncated {
    stop_reason: "length".into(), budget: 256, attempts: 1,
});
assert_eq!(source.calls, 1);

// An array where an object was required, above temperature 0: retried, then reported.
let mut source = script(vec![("[1, 2]", Some("stop"))]);
let error = chat_json(&mut source, 3, 0.7, None, 512, true).unwrap_err();
assert_eq!(error, ChatJsonError::WrongShape { got: "list".into(), attempts: 3 });

// No extraction: JSON wrapped in a code fence does not parse.
assert_eq!(JsonAttempt::parse("```json\n{\"a\": 1}\n```", 1), JsonAttempt::Unparseable);
# Ok::<(), ChatJsonError>(())
```

Each attempt is classified, and the class decides whether a retry could help:

| Answer | Temperature 0 | Above 0 |
|---|---|---|
| Stopped at the ceiling (`max_tokens` or `length`, `TRUNCATION_STOP_REASONS`), and the JSON parses **strictly** | used as-is | used as-is |
| Stopped at the ceiling, and parses only after repair (or not at all) | `Truncated` at once | retried |
| A shape other than an object, under `require_dict` | `WrongShape` at once | retried |
| Empty, or unparseable even after repair | retried | retried |
| `source.chat` returned `Err` | `Transport` at once | `Transport` at once |

At temperature 0 greedy sampling reproduces the same answer, so retrying a
truncation or a wrong shape only pays for it again. A truncated answer that
parses only because the repairer closed its brackets is truncation, never
success: `{"n": 12` is not the number 12. `ChatJsonError` names the real cause
and the attempts made (`Truncated` carries the provider's own stop-reason word
and the budget in force); `got` uses Python's type names (`"list"`, `"str"`,
`"int"`, …, from `json_type_name`).

Differences from Python's `BaseAgent.chat_json`:

- Parsing is `JsonAttempt::parse`: a strict parse, else `safe_json_loads` with
  repair. There is no extraction step, so prose or a code fence around the
  JSON is unparseable here where Python's `parse_json` digs it out. Ask for
  bare JSON (the binding below sets `json_mode`), or run
  [`llm::extract_json`](llm.md) inside your own `ChatSource`.
- A transport failure is its own variant and is not retried; Python lets the
  provider's exception propagate.
- `ChatJsonOutcome` reports `attempts` and `repaired` in the return value,
  where Python only logged them. There is no `retry_context` label: the crate
  has no logger.

`JsonAttempt::parse(content, max_attempts)` is public on its own:
`Clean(value)`, `Repaired(value)` or `Unparseable`.

## Against a real client

`quality::LlmChat` is the production `JsonChat`: it builds a `ChatRequest`
with `json_mode` on, runs it through `chat_json`, and backs off 1 s, 2 s, 4 s
between attempts (real sleeps).

```rust
use std::collections::BTreeMap;
use std::sync::Arc;
use bmlib::llm::{LLMMessage, LlmClient};
use bmlib::publications::fetchers::{FetchError, HttpClient, HttpResponse};
use bmlib::quality::{JsonChat, LlmChat};
use serde_json::{json, Value};

struct Canned;
impl HttpClient for Canned {
    fn get(&self, url: &str) -> Result<HttpResponse, FetchError> {
        Err(FetchError::Transport(format!("no GET here: {url}")))
    }
    fn post_json(&self, _url: &str, body: &Value, _h: &BTreeMap<String, String>)
        -> Result<HttpResponse, FetchError> {
        assert_eq!(body["response_format"], json!({"type": "json_object"}));
        Ok(HttpResponse::ok_text(
            r#"{"choices": [{"message": {"content": "{\"score\": 7}"}, "finish_reason": "stop"}]}"#,
        ))
    }
}

let mut client = LlmClient::new(Arc::new(Canned));
client.api_key = Some("sk-test".into());
let mut chat = LlmChat::new(&client, "openai:gpt-4o-mini");

let messages = [
    LLMMessage::system("Reply with a JSON object."),
    LLMMessage::user("Score this abstract from 1 to 10."),
];
//                                      temperature, max_tokens, max_retries, require_dict
let outcome = chat.chat_json(&messages, 0.0, 512, 3, true)?;
assert_eq!(outcome.value["score"], 7);
# Ok::<(), bmlib::agents::ChatJsonError>(())
```

A `ChatError` from the client arrives as `ChatJsonError::Transport` with its
message. The quality tiers take a `&mut dyn JsonChat`, so a test hands them a
scripted one instead.

## Counting what an agent did

`PerformanceMetrics` is thread-safe (`&self` methods behind a `Mutex`), so one
value can be shared by every worker of an agent. It is independent of the
process-wide [`TokenTracker`](llm.md), which `LlmClient` feeds: that one is
what the process spent, this one is what this agent did. Record each
successful call with `add_request` and each retry with `add_retry`. The time
marks take the readings as arguments, so a test passes fixed ones:

```rust
use bmlib::agents::{Monotonic, PerformanceMetrics};

let metrics = PerformanceMetrics::new();
metrics.mark_start(1_760_000_000.0, Monotonic::from_seconds(100.0));
metrics.add_request(1_500, 300, 2.0); // prompt, completion, seconds in the request
metrics.add_retry();
metrics.mark_end(1_760_000_004.0, Monotonic::from_seconds(104.0));

let snapshot = metrics.snapshot(); // an independent copy, read under the lock
assert_eq!(snapshot.total_tokens, 1_800);
assert_eq!(snapshot.tokens_per_second(), 150.0);
assert_eq!(
    snapshot.format_report(Some("Scorer")),
    "=== Scorer Performance Metrics ===\n\
     Requests:     1 (1 retries)\n\
     Tokens:       1,800 total (1,500 prompt + 300 completion)\n\
     Time:         4.00s elapsed (2.00s in requests)\n\
     Speed:        150.0 tokens/sec\n\
     Avg/Request:  1800 tokens"
);

let json = snapshot.to_json(); // Python's to_dict(), derived figures rounded
assert_eq!(json["elapsed_time_seconds"], 4.0);
metrics.reset();
assert_eq!(metrics.snapshot().total_requests, 0);
```

In a real run pass `std::time::SystemTime` seconds for the wall-clock mark and
`Monotonic::now()` for the duration. The report is Python's byte for byte:
commas group digits whatever the locale, the retry count shows only when
non-zero, and the elapsed figure only when positive. `MetricsSnapshot::from_json`
rebuilds a snapshot without monotonic marks, so its elapsed time falls back to
`end_time - start_time`. The derived figures are methods
(`elapsed_time_seconds(now)`, `tokens_per_second()`,
`average_tokens_per_request()`), not properties.
