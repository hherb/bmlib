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

//! Locating JSON in text that may carry prose or fenced code blocks.
//!
//! A port of `bmlib/llm/utils.py`. Two layers, deliberately separated:
//! [`iter_json_spans`] **locates** candidate spans without judging them, and
//! [`extract_json`] is one *acceptance policy* over those candidates. The
//! repair path is a different policy over the same locator, which is why the
//! split exists.
//!
//! # Why the policy is not "the first thing that parses"
//!
//! The non-dict fallback is **ranked**: a span that is a list holding at least
//! one object beats any other non-dict span. Without the ranking, an
//! incidental parseable span earlier in a response — an empty `[]`, a list of
//! strings — would be accepted, and the payload nested in a later array would
//! be substituted by unrelated data that parses cleanly and survives every
//! downstream shape check. That is the defect the ranking was added for.
//!
//! # Why a fence wins on parse alone
//!
//! A fenced candidate outranks the dict preference: a fence is the model's own
//! delimitation of its answer, so a fenced JSON array must not be reduced to an
//! object plucked from inside it by a later, unfenced stage.

use serde_json::Value;

/// Which closer ends a span opened by which opener.
fn closer_for(opener: char) -> Option<char> {
    match opener {
        '{' => Some('}'),
        '[' => Some(']'),
        _ => None,
    }
}

/// A fenced code block: its language tag and its body.
struct Fence {
    lang: String,
    body: String,
}

/// Find every triple-backtick fenced block, in document order.
///
/// Mirrors Python's ``_FENCE_RE = r"```(\w*)[ \t]*\r?\n?(.*?)```"`` with
/// `DOTALL`: a fence opener, an optional word of language tag, optional
/// trailing spaces/tabs, an optional newline, then the non-greedy body up to
/// the next fence. Non-greedy matters — consecutive fences must not merge
/// into one block.
fn fences(text: &str) -> Vec<Fence> {
    let chars: Vec<char> = text.chars().collect();
    let n = chars.len();
    let mut out = Vec::new();
    let mut i = 0usize;

    while i + 2 < n {
        if chars[i] == '`' && chars[i + 1] == '`' && chars[i + 2] == '`' {
            let mut j = i + 3;
            // `\w*` — the language tag.
            let lang_start = j;
            while j < n && (chars[j].is_alphanumeric() || chars[j] == '_') {
                j += 1;
            }
            let lang: String = chars[lang_start..j].iter().collect();
            // `[ \t]*`
            while j < n && (chars[j] == ' ' || chars[j] == '\t') {
                j += 1;
            }
            // `\r?\n?`
            if j < n && chars[j] == '\r' {
                j += 1;
            }
            if j < n && chars[j] == '\n' {
                j += 1;
            }
            // `(.*?)` up to the next fence.
            let body_start = j;
            let mut k = j;
            let mut body_end = None;
            while k + 2 < n + 1 {
                if k + 2 < n && chars[k] == '`' && chars[k + 1] == '`' && chars[k + 2] == '`' {
                    body_end = Some(k);
                    break;
                }
                if k >= n {
                    break;
                }
                k += 1;
            }
            if let Some(end) = body_end {
                let body: String = chars[body_start..end].iter().collect();
                out.push(Fence {
                    lang,
                    body: body.trim().to_string(),
                });
                i = end + 3;
                continue;
            }
            // An unterminated fence: no match, and no later fence can match
            // either, since a closer after this point would have been found.
            break;
        }
        i += 1;
    }
    out
}

/// Index of the first `{` or `[` outside any quoted string, or `None`.
fn first_opener(text: &str) -> Option<usize> {
    let mut in_str = false;
    let mut escape = false;
    for (i, ch) in text.char_indices() {
        if in_str {
            if escape {
                escape = false;
            } else if ch == '\\' {
                escape = true;
            } else if ch == '"' {
                in_str = false;
            }
            continue;
        }
        if ch == '"' {
            in_str = true;
        } else if ch == '{' || ch == '[' {
            return Some(i);
        }
    }
    None
}

/// Yield each outermost balanced span, in document order.
///
/// Only the pair type of a span's **own** opener is counted, so a `[` span is
/// not disturbed by the braces nested inside it. Quoted strings — and escapes
/// within them — are honoured, so a brace inside a string value never affects
/// nesting. A span that never balances ends the scan: any later opener is
/// nested inside it, not a sibling.
fn iter_balanced(text: &str, openers: &[char]) -> Vec<String> {
    let mut out = Vec::new();
    let mut in_str = false;
    let mut escape = false;
    let mut depth = 0i32;
    let mut start = 0usize;
    let mut opener = '\0';

    for (i, ch) in text.char_indices() {
        if in_str {
            if escape {
                escape = false;
            } else if ch == '\\' {
                escape = true;
            } else if ch == '"' {
                in_str = false;
            }
            continue;
        }
        if ch == '"' {
            in_str = true;
        } else if depth == 0 {
            if openers.contains(&ch) {
                opener = ch;
                start = i;
                depth = 1;
            }
        } else if ch == opener {
            depth += 1;
        } else if Some(ch) == closer_for(opener) {
            depth -= 1;
            if depth == 0 {
                out.push(text[start..i + ch.len_utf8()].to_string());
            }
        }
    }
    out
}

/// Yield candidate JSON spans from `text`, best first, without validating.
///
/// Callers apply their own acceptance policy by walking the candidates in
/// order. Stages, in priority order:
///
/// 1. ` ```json ` fenced bodies, in document order.
/// 2. Other fenced bodies that start with `{` or `[`.
/// 3. The remaining fenced bodies.
/// 4. Balanced `{...}`/`[...]` spans, in document order.
/// 5. Brace-only balanced spans not already yielded, so an object nested in an
///    array is still offered. Skipped when `nested_objects` is false.
/// 6. The text from the first opener to the end — only when nothing balanced,
///    which is what truncated model output looks like.
///
/// No span is yielded twice, compared by text rather than by position. The
/// stages overlap heavily — stages 4 and 5 rescan fence interiors as plain
/// text — and a repeated candidate is pure waste: an identical string parses
/// and repairs identically, so re-offering it only buys a second run of the
/// repair attempt loop on a span that has already failed.
#[must_use]
pub fn iter_json_spans(text: &str, nested_objects: bool) -> Vec<String> {
    if text.is_empty() {
        return Vec::new();
    }

    let fence_list = fences(text);
    let mut taken: Vec<usize> = Vec::new();
    let mut yielded: Vec<String> = Vec::new();
    let mut out: Vec<String> = Vec::new();

    for stage in ["json", "jsonish", "rest"] {
        for (index, fence) in fence_list.iter().enumerate() {
            if taken.contains(&index) || fence.body.is_empty() {
                continue;
            }
            if stage == "json" && fence.lang != "json" {
                continue;
            }
            if stage == "jsonish" && !(fence.body.starts_with('{') || fence.body.starts_with('[')) {
                continue;
            }
            taken.push(index);
            if !yielded.contains(&fence.body) {
                yielded.push(fence.body.clone());
                out.push(fence.body.clone());
            }
        }
    }

    let mut balanced_found = false;
    let passes: Vec<Vec<char>> = if nested_objects {
        vec![vec!['{', '['], vec!['{']]
    } else {
        vec![vec!['{', '[']]
    };
    for openers in &passes {
        for span in iter_balanced(text, openers) {
            // Set before the dedup check: a span that repeats one already
            // yielded still means the text balanced, so stage 6 must not fire.
            balanced_found = true;
            if !yielded.contains(&span) {
                yielded.push(span.clone());
                out.push(span);
            }
        }
    }

    if !balanced_found {
        if let Some(first) = first_opener(text) {
            let tail = text[first..].to_string();
            if !yielded.contains(&tail) {
                out.push(tail);
            }
        }
    }

    out
}

/// Extract a JSON span from text that may contain prose or code blocks.
///
/// Applies [`first_acceptable`] to the candidates from [`iter_json_spans`]
/// twice: once over whole spans only, and — if nothing there parsed — once
/// more with the nested-object stage enabled. Returns `text` unchanged when
/// nothing parses at all.
///
/// `allow_fragments` false skips the second walk, so `text` comes back
/// unchanged rather than an object dug out of the inside of a span. A caller
/// that can *repair* has something better to try than a fragment: for a
/// truncated `[{"a": 1}, {"b": 2` the fragment is only the first object, while
/// repair closes the bracket and recovers both.
#[must_use]
pub fn extract_json(text: &str, allow_fragments: bool) -> String {
    let fenced: Vec<String> = fences(text).into_iter().map(|f| f.body).collect();

    if let Some(whole) = first_acceptable(text, &fenced, false) {
        return whole;
    }
    if !allow_fragments {
        return text.to_string();
    }
    // Nothing at the top level parsed. Only now is an object dug out of the
    // inside of a span worth having.
    match first_acceptable(text, &fenced, true) {
        Some(fragment) => fragment,
        None => text.to_string(),
    }
}

/// Fence, then dict, then the best non-dict span; `None` when nothing parses.
fn first_acceptable(text: &str, fenced: &[String], nested_objects: bool) -> Option<String> {
    let mut fallback: Option<String> = None;
    let mut with_objects: Option<String> = None;

    for candidate in iter_json_spans(text, nested_objects) {
        let Ok(parsed) = serde_json::from_str::<Value>(&candidate) else {
            continue;
        };
        if parsed.is_object() {
            return Some(candidate);
        }
        if fenced.contains(&candidate) {
            return Some(candidate);
        }
        if with_objects.is_none() && parsed.is_array() {
            if let Some(items) = parsed.as_array() {
                if items.iter().any(Value::is_object) {
                    with_objects = Some(candidate.clone());
                }
            }
        }
        if fallback.is_none() {
            fallback = Some(candidate);
        }
    }

    with_objects.or(fallback)
}
