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

//! Repair malformed JSON emitted by LLMs.
//!
//! A port of `bmlib/llm/json_repair.py`. LLMs produce predictable syntax
//! errors — missing commas, trailing commas, single quotes, unescaped
//! newlines, truncation, unquoted keys — and this fixes them before parsing.
//! It complements [`crate::llm::utils::extract_json`], which only *locates*
//! JSON.
//!
//! # A defect this port fixes rather than reproduces — issue #299
//!
//! Python's `_fix_truncated_json` appends every closing bracket and **then**
//! every closing brace:
//!
//! ```text
//! result += "]" * open_brackets
//! result += "}" * open_braces
//! ```
//!
//! The correct closer sequence is the reverse of the order the openers
//! appeared, which is the same thing only when every `[` precedes every `{`.
//! When they interleave the result is not valid JSON, so repair fails — and
//! the caller falls through to the fragment extractor, which returns the first
//! object and **drops every sibling without an error**:
//!
//! ```text
//! '[{"a": 1}, {"b": 2'   ->  repair fails, then the fragment {"a": 1}
//! ```
//!
//! [truncation_closers] walks the text with a **stack** instead, so
//! `[{"a": 1}, {"b": 2` closes to `[{"a": 1}, {"b": 2}]` and both objects are
//! recovered. The Python comment above its fragment fallback names this exact
//! input and outcome — repair cannot reach it, which is the defect.
//!
//! # Deliberate limits, kept from Python
//!
//! - `MAX_SALVAGE_MATCHES` bounds the fast pass of [`salvage_json_fields`],
//!   because each failed decode scans forward to the end of the document and
//!   an unbounded pass is quadratic in the response length.
//! - [`salvage_json_fields`] is **not** wired into any automatic path:
//!   returning partial data automatically would turn a loud failure into a
//!   quiet wrong answer.

use std::collections::BTreeMap;

use serde::Deserialize;
use serde_json::Value;

use crate::llm::utils::iter_json_spans;

/// Maximum repair iterations.
pub const MAX_REPAIR_ATTEMPTS: usize = 3;
/// 1 MB maximum input.
pub const MAX_JSON_LENGTH: usize = 1_000_000;
/// How many textual matches of one key [`salvage_json_fields`] decodes before
/// giving up on the fast pass.
pub const MAX_SALVAGE_MATCHES: usize = 200;

/// Raised when JSON cannot be repaired.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct JsonRepairError(pub String);

impl std::fmt::Display for JsonRepairError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for JsonRepairError {}

/// Why a repair could not be attempted or completed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RepairError {
    /// `repair_json` was given an empty or whitespace-only string.
    EmptyRepair,
    /// `safe_json_loads` was given an empty or whitespace-only string.
    EmptyParse,
    /// `extract_and_repair_json` was given an empty or whitespace-only response.
    EmptyExtract,
    /// The input exceeded [`MAX_JSON_LENGTH`].
    TooLarge,
    /// Repair was attempted and failed.
    Unrepairable(String),
}

impl std::fmt::Display for RepairError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            RepairError::EmptyRepair => write!(f, "Cannot repair empty JSON string"),
            RepairError::EmptyParse => write!(f, "Cannot parse empty JSON string"),
            RepairError::EmptyExtract => write!(f, "Cannot extract JSON from empty response"),
            RepairError::TooLarge => {
                write!(f, "JSON string too large (max {MAX_JSON_LENGTH} bytes)")
            }
            RepairError::Unrepairable(m) => f.write_str(m),
        }
    }
}

impl std::error::Error for RepairError {}

/// Attempt to repair malformed JSON from an LLM response.
///
/// If the JSON is already valid, returns it unchanged.
///
/// # Errors
///
/// [`RepairError::Empty`] for an empty input, [`RepairError::TooLarge`] past
/// the size cap, or [`RepairError::Unrepairable`] when no attempt parses.
pub fn repair_json(json_str: &str, max_attempts: usize) -> Result<String, RepairError> {
    if json_str.trim().is_empty() {
        return Err(RepairError::EmptyRepair);
    }
    if json_str.len() > MAX_JSON_LENGTH {
        return Err(RepairError::TooLarge);
    }

    let original = json_str.to_string();
    if serde_json::from_str::<Value>(json_str).is_ok() {
        return Ok(original);
    }

    let mut current = json_str.to_string();
    let mut last_error = String::new();
    for _ in 0..max_attempts {
        let repaired = apply_repairs(&current);
        match serde_json::from_str::<Value>(&repaired) {
            Ok(_) => return Ok(repaired),
            Err(e) => {
                // Feed the partially-repaired version into the next iteration.
                last_error = e.to_string();
                current = repaired;
            }
        }
    }
    Err(RepairError::Unrepairable(format!(
        "Cannot repair JSON after {max_attempts} attempts: {last_error}"
    )))
}

/// [`repair_json`] with the default attempt count.
///
/// # Errors
///
/// As [`repair_json`].
pub fn repair_json_default(json_str: &str) -> Result<String, RepairError> {
    repair_json(json_str, MAX_REPAIR_ATTEMPTS)
}

/// Apply all repair strategies in dependency order.
///
/// One repair failing must not abort the rest, so each is infallible by
/// construction here — unlike Python, where a raise was caught per strategy.
#[must_use]
pub fn apply_repairs(json_str: &str) -> String {
    let mut result = json_str.to_string();
    result = fix_single_quotes(&result);
    result = fix_unescaped_newlines(&result);
    result = fix_unescaped_tabs(&result);
    result = fix_unescaped_control_chars(&result);
    result = fix_trailing_commas(&result);
    result = fix_missing_commas(&result);
    result = fix_truncated_json(&result);
    result = fix_unquoted_keys(&result);
    result
}

/// Whether the character at `i` is escaped by an odd run of backslashes.
fn is_escaped(chars: &[char], i: usize) -> bool {
    if i == 0 {
        return false;
    }
    let mut backslashes = 0usize;
    let mut j = i;
    while j > 0 && chars[j - 1] == '\\' {
        backslashes += 1;
        j -= 1;
    }
    backslashes % 2 == 1
}

/// Convert single-quote string delimiters to double quotes.
///
/// A state machine, so apostrophes inside double-quoted strings are left
/// untouched — only quotes that actually delimit a value convert.
#[must_use]
pub fn fix_single_quotes(json_str: &str) -> String {
    let chars: Vec<char> = json_str.chars().collect();
    let mut result = String::with_capacity(json_str.len());
    let mut in_double = false;
    let mut in_single = false;
    // Last non-whitespace character seen, tracked incrementally so the opener
    // check stays O(1) per character.
    let mut prev_nonspace = '\0';
    let mut i = 0usize;

    while i < chars.len() {
        let ch = chars[i];
        let prev = if i > 0 { chars[i - 1] } else { '\0' };

        // Pass escape sequences straight through.
        if prev == '\\' && !is_escaped(&chars, i) {
            result.push(ch);
            if !ch.is_whitespace() {
                prev_nonspace = ch;
            }
            i += 1;
            continue;
        }

        if ch == '"' && !in_single {
            in_double = !in_double;
            result.push(ch);
        } else if ch == '\'' && !in_double {
            if in_single {
                result.push('"');
                in_single = false;
            } else if prev_nonspace != '\0' && ":,[{'\"".contains(prev_nonspace) {
                // A string opener only in a value/key position; otherwise it is
                // an apostrophe.
                result.push('"');
                in_single = true;
            } else {
                result.push(ch);
            }
        } else {
            result.push(ch);
        }

        if !ch.is_whitespace() {
            prev_nonspace = ch;
        }
        i += 1;
    }
    result
}

/// Escape raw newlines/carriage returns that appear inside strings.
#[must_use]
pub fn fix_unescaped_newlines(json_str: &str) -> String {
    let chars: Vec<char> = json_str.chars().collect();
    let mut result = String::with_capacity(json_str.len());
    let mut in_string = false;
    for (i, ch) in chars.iter().enumerate() {
        let escaped = is_escaped(&chars, i);
        if *ch == '"' && !escaped {
            in_string = !in_string;
            result.push(*ch);
        } else if *ch == '\n' && in_string {
            result.push_str("\\n");
        } else if *ch == '\r' && in_string {
            result.push_str("\\r");
        } else {
            result.push(*ch);
        }
    }
    result
}

/// Escape raw tabs that appear inside strings.
#[must_use]
pub fn fix_unescaped_tabs(json_str: &str) -> String {
    let chars: Vec<char> = json_str.chars().collect();
    let mut result = String::with_capacity(json_str.len());
    let mut in_string = false;
    for (i, ch) in chars.iter().enumerate() {
        let escaped = is_escaped(&chars, i);
        if *ch == '"' && !escaped {
            in_string = !in_string;
            result.push(*ch);
        } else if *ch == '\t' && in_string {
            result.push_str("\\t");
        } else {
            result.push(*ch);
        }
    }
    result
}

/// Escape control characters (except tab/newline/cr) inside strings.
#[must_use]
pub fn fix_unescaped_control_chars(json_str: &str) -> String {
    let chars: Vec<char> = json_str.chars().collect();
    let mut result = String::with_capacity(json_str.len());
    let mut in_string = false;
    for (i, ch) in chars.iter().enumerate() {
        let escaped = is_escaped(&chars, i);
        if *ch == '"' && !escaped {
            in_string = !in_string;
            result.push(*ch);
        } else if in_string && is_control_to_escape(*ch) {
            result.push_str(&format!("\\u{:04x}", *ch as u32));
        } else {
            result.push(*ch);
        }
    }
    result
}

/// The control characters Python escapes: `[\x00-\x08\x0b\x0c\x0e-\x1f]`.
fn is_control_to_escape(ch: char) -> bool {
    matches!(ch as u32, 0x00..=0x08 | 0x0b | 0x0c | 0x0e..=0x1f)
}

/// Remove commas that immediately precede a closing bracket/brace.
#[must_use]
pub fn fix_trailing_commas(json_str: &str) -> String {
    let chars: Vec<char> = json_str.chars().collect();
    let mut result = String::with_capacity(json_str.len());
    let mut in_string = false;
    for (i, ch) in chars.iter().enumerate() {
        let escaped = is_escaped(&chars, i);
        if *ch == '"' && !escaped {
            in_string = !in_string;
            result.push(*ch);
        } else if *ch == ',' && !in_string {
            let rest: String = chars[i + 1..].iter().collect();
            let trimmed = rest.trim_start();
            if trimmed.starts_with(']') || trimmed.starts_with('}') {
                // Skip the trailing comma.
            } else {
                result.push(*ch);
            }
        } else {
            result.push(*ch);
        }
    }
    result
}

/// Insert missing commas between adjacent values, objects, or arrays.
///
/// Handles `"a" "b"` → `"a", "b"`, `} {` → `}, {`, `] [` → `], [`, and
/// `"k": "v" "next":` → `"k": "v", "next":`.
#[must_use]
pub fn fix_missing_commas(json_str: &str) -> String {
    let chars: Vec<char> = json_str.chars().collect();
    let mut result = String::with_capacity(json_str.len());
    let mut in_string = false;
    let mut prev_nonspace = '\0';
    let mut i = 0usize;

    while i < chars.len() {
        let ch = chars[i];
        let escaped = is_escaped(&chars, i);

        if ch == '"' && !escaped {
            if !in_string && prev_nonspace != '\0' && "\"}]0123456789".contains(prev_nonspace) {
                if let Some(close_quote) = find_closing_quote(&chars, i) {
                    // Whether this quoted token is a key or a value, a
                    // separator is missing before it as long as anything
                    // follows its closing quote.
                    let mut j = close_quote + 1;
                    while j < chars.len() && chars[j].is_whitespace() {
                        j += 1;
                    }
                    if j < chars.len() {
                        result.push(',');
                    }
                }
            }
            in_string = !in_string;
            result.push(ch);
        } else if (ch == '{' || ch == '[') && !in_string {
            if prev_nonspace != '\0' && "\"}]0123456789".contains(prev_nonspace) {
                result.push(',');
            }
            result.push(ch);
        } else {
            result.push(ch);
        }

        if !ch.is_whitespace() {
            prev_nonspace = ch;
        }
        i += 1;
    }
    result
}

/// The index of the closing quote for the string opened at `start`.
///
/// `None` if no unescaped closing quote is found.
fn find_closing_quote(chars: &[char], start: usize) -> Option<usize> {
    let mut i = start + 1;
    while i < chars.len() {
        if chars[i] == '"' {
            let mut backslashes = 0usize;
            let mut j = i;
            while j > start + 1 && chars[j - 1] == '\\' {
                backslashes += 1;
                j -= 1;
            }
            if backslashes % 2 == 0 {
                return Some(i);
            }
        }
        i += 1;
    }
    None
}

/// Close truncated JSON by appending the missing closers.
///
/// # The fix for issue #299
///
/// A **stack** of openers, so the closers come out in reverse-open order.
/// Python appended every `]` then every `}`, which is equivalent only when
/// every `[` precedes every `{`. For `[{"a": 1}, {"b": 2` Python produced
/// `[{"a": 1}, {"b": 2]}` — not JSON — so repair failed and the caller fell
/// through to a fragment extractor that returned `{"a": 1}` and dropped the
/// sibling silently. The stack yields `[{"a": 1}, {"b": 2}]`.
#[must_use]
pub fn fix_truncated_json(json_str: &str) -> String {
    let chars: Vec<char> = json_str.chars().collect();
    let mut stack: Vec<char> = Vec::new();
    let mut in_string = false;

    for (i, ch) in chars.iter().enumerate() {
        let escaped = is_escaped(&chars, i);
        if *ch == '"' && !escaped {
            in_string = !in_string;
        } else if !in_string {
            match ch {
                '{' | '[' => stack.push(*ch),
                '}' if stack.last() == Some(&'{') => {
                    stack.pop();
                }
                ']' if stack.last() == Some(&'[') => {
                    stack.pop();
                }
                _ => {}
            }
        }
    }

    let mut result = json_str.trim_end().to_string();

    // Close an unterminated string first.
    if in_string {
        result.push('"');
    }

    // Drop a dangling comma before appending closers.
    while result.ends_with(',') {
        result.pop();
    }
    result = result.trim_end().to_string();

    // Outermost last, so the sequence is the reverse of the opening order.
    for opener in stack.iter().rev() {
        result.push(match opener {
            '{' => '}',
            '[' => ']',
            _ => continue,
        });
    }

    result
}

/// Span of a quoted string, inclusive of both quotes.
struct StringSpan {
    start: usize,
    end: usize,
}

/// Locate every quoted-string span, so a substitution can skip their contents.
fn string_spans(chars: &[char]) -> Vec<StringSpan> {
    let mut out = Vec::new();
    let mut i = 0usize;
    while i < chars.len() {
        if chars[i] == '"' && !is_escaped(chars, i) {
            if let Some(end) = find_closing_quote(chars, i) {
                out.push(StringSpan { start: i, end });
                i = end + 1;
                continue;
            }
        }
        i += 1;
    }
    out
}

/// Quote JavaScript-style unquoted object keys, skipping string contents.
#[must_use]
pub fn fix_unquoted_keys(json_str: &str) -> String {
    let chars: Vec<char> = json_str.chars().collect();
    let spans = string_spans(&chars);
    let mut result = String::with_capacity(json_str.len());
    let mut last = 0usize;
    for span in &spans {
        result.push_str(&quote_keys_in(&chars[last..span.start]));
        result.extend(chars[span.start..=span.end].iter());
        last = span.end + 1;
    }
    if last < chars.len() {
        result.push_str(&quote_keys_in(&chars[last..]));
    }
    result
}

/// Quote unquoted keys in a stretch of *structural* text.
///
/// Python spells this as one regex, `([{,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)(\s*:)`.
/// Hand-rolled here for the reason the dependency policy gives: the pattern is
/// bmlib's own, small, and needs no backtracking. An anchored matcher reproduces
/// the regex's left-to-right scan exactly.
fn quote_keys_in(chars: &[char]) -> String {
    let mut out = String::with_capacity(chars.len());
    let mut i = 0usize;
    while i < chars.len() {
        if let Some((consumed, replacement)) = match_unquoted_key(chars, i) {
            out.push_str(&replacement);
            i += consumed;
        } else {
            out.push(chars[i]);
            i += 1;
        }
    }
    out
}

/// Try to match `([{,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)(\s*:)` at `start`.
///
/// Returns `(characters consumed, replacement)`.
fn match_unquoted_key(chars: &[char], start: usize) -> Option<(usize, String)> {
    let n = chars.len();
    let mut i = start;
    if i >= n || !"{,}".contains(chars[i]) {
        return None;
    }
    i += 1;
    while i < n && chars[i].is_whitespace() {
        i += 1;
    }
    // `[a-zA-Z_]`
    if i >= n || !(chars[i].is_ascii_alphabetic() || chars[i] == '_') {
        return None;
    }
    let name_start = i;
    // `[a-zA-Z0-9_]*`
    while i < n && (chars[i].is_ascii_alphanumeric() || chars[i] == '_') {
        i += 1;
    }
    let name: String = chars[name_start..i].iter().collect();
    let after_name = i;
    while i < n && chars[i].is_whitespace() {
        i += 1;
    }
    if i >= n || chars[i] != ':' {
        return None;
    }
    let prefix: String = chars[start..name_start].iter().collect();
    let suffix: String = chars[after_name..=i].iter().collect();
    Some((i + 1 - start, format!("{prefix}\"{name}\"{suffix}")))
}

/// Parse JSON, optionally repairing malformed input first.
///
/// # Errors
///
/// A [`RepairError`] if the input is empty, too large, or unparseable even
/// after repair.
pub fn safe_json_loads(
    json_str: &str,
    repair: bool,
    max_attempts: usize,
) -> Result<Value, RepairError> {
    if json_str.trim().is_empty() {
        return Err(RepairError::EmptyParse);
    }
    let immediate_error = match serde_json::from_str::<Value>(json_str) {
        Ok(v) => return Ok(v),
        Err(e) => e.to_string(),
    };
    if !repair {
        // Carries the decoder's detail, as Python's does. `serde_json` and
        // Python's `json` word a syntax error differently, so the detail
        // itself cannot match; the "Invalid JSON:" prefix and the error kind
        // do, and the oracle compares those.
        return Err(RepairError::Unrepairable(format!(
            "Invalid JSON: {immediate_error}"
        )));
    }
    let repaired = repair_json(json_str, max_attempts)?;
    serde_json::from_str::<Value>(&repaired)
        .map_err(|e| RepairError::Unrepairable(format!("Cannot parse JSON even after repair: {e}")))
}

/// Extract a JSON string from an LLM response and optionally repair it.
///
/// Walks candidate spans in priority order; the first that validates (or
/// repairs, when enabled) is returned.
///
/// Unlike [`crate::llm::utils::extract_json`] this applies **no dict
/// preference** — the first candidate that parses wins, object or array. The
/// two share a locator but not an acceptance policy, so on
/// `text [1,2] then {"a": 1}` `extract_json` returns the object and this
/// returns `[1,2]`. Deliberate: dict preference exists to serve `json_mode`
/// callers who asked for an object, whereas this is a general-purpose
/// extractor whose caller may well want the array.
///
/// # Errors
///
/// [`RepairError::Empty`] for an empty response, or
/// [`RepairError::Unrepairable`] naming the last candidate's failure.
pub fn extract_and_repair_json(
    response: &str,
    repair: bool,
) -> Result<(String, bool), RepairError> {
    if response.trim().is_empty() {
        return Err(RepairError::EmptyExtract);
    }

    let mut last_error: Option<String> = None;

    for candidate in iter_json_spans(response.trim(), false) {
        match serde_json::from_str::<Value>(&candidate) {
            Ok(_) => return Ok((candidate, false)),
            // Recorded even when repair is off, so a caller that disabled
            // repair still learns *why* the last candidate was rejected
            // rather than being told no JSON was found. Python does the same.
            Err(e) => last_error = Some(e.to_string()),
        }
        if !repair {
            continue;
        }
        match repair_json_default(&candidate) {
            Ok(repaired) => {
                if serde_json::from_str::<Value>(&repaired).is_ok() {
                    return Ok((repaired, true));
                }
            }
            Err(e) => last_error = Some(e.to_string()),
        }
    }

    Err(match last_error {
        Some(e) => RepairError::Unrepairable(format!("Cannot parse extracted JSON: {e}")),
        None => RepairError::Unrepairable("No JSON found in response".to_string()),
    })
}

/// Recover individual top-level fields from a document that will not parse.
///
/// A last resort. A long structured answer is often malformed in only one
/// place — typically a truncated array at the tail — while the fields the
/// caller actually needs are intact. This locates each requested key and
/// decodes just the value that follows it.
///
/// **Deliberately not wired into any automatic path**: returning partial data
/// automatically would turn a loud failure into a quiet wrong answer.
///
/// Matching is textual, so a key name appearing inside a string value can be
/// matched; the first occurrence that decodes wins.
///
/// Work is bounded: the fast pass decodes at most [`MAX_SALVAGE_MATCHES`]
/// occurrences of a key, and repair is attempted at most once, at the *last*
/// occurrence, since repair closes a truncated tail and there is only one tail.
#[must_use]
pub fn salvage_json_fields(text: &str, keys: &[String]) -> BTreeMap<String, Value> {
    let mut recovered: BTreeMap<String, Value> = BTreeMap::new();
    if text.is_empty() {
        return recovered;
    }

    for key in keys {
        let matches = find_key_positions(text, key);

        let mut found = false;
        for end in matches.iter().take(MAX_SALVAGE_MATCHES) {
            if let Some(value) = decode_value_at(text, *end, false) {
                recovered.insert(key.clone(), value);
                found = true;
                break;
            }
        }
        if !found {
            if let Some(last) = matches.last() {
                if let Some(value) = decode_value_at(text, *last, true) {
                    recovered.insert(key.clone(), value);
                }
            }
        }
    }

    recovered
}

/// Character offsets just past `"key"\s*:\s*` for every textual occurrence.
fn find_key_positions(text: &str, key: &str) -> Vec<usize> {
    let chars: Vec<char> = text.chars().collect();
    let needle: Vec<char> = format!("\"{key}\"").chars().collect();
    let mut out = Vec::new();
    if needle.is_empty() || needle.len() > chars.len() {
        return out;
    }
    let mut i = 0usize;
    while i + needle.len() <= chars.len() {
        if chars[i..i + needle.len()] == needle[..] {
            let mut j = i + needle.len();
            while j < chars.len() && chars[j].is_whitespace() {
                j += 1;
            }
            if j < chars.len() && chars[j] == ':' {
                j += 1;
                while j < chars.len() && chars[j].is_whitespace() {
                    j += 1;
                }
                out.push(j);
                i = j;
                continue;
            }
        }
        i += 1;
    }
    out
}

/// Decode one JSON value starting at character `index`.
///
/// With `allow_repair`, a value that fails to decode is retried against
/// `repair_json` of the remainder — the value runs to the end of a truncated
/// document, so closing it and retrying recovers it.
fn decode_value_at(text: &str, index: usize, allow_repair: bool) -> Option<Value> {
    let rest: String = text.chars().skip(index).collect();
    let mut de = serde_json::Deserializer::from_str(&rest);
    if let Ok(value) = Value::deserialize(&mut de) {
        return Some(value);
    }
    if !allow_repair {
        return None;
    }
    let repaired = repair_json_default(&rest).ok()?;
    let mut de = serde_json::Deserializer::from_str(&repaired);
    Value::deserialize(&mut de).ok()
}
