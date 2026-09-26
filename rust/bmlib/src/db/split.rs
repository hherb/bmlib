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

//! Split a multi-statement SQL script into individual statements.
//!
//! A port of `db/operations._split_sql_statements`, the one piece of `db/`
//! that is pure string scanning.
//!
//! It scans over a `Vec<char>`, not over byte offsets — the Python original
//! indexes by code point, and transliterating that arithmetic onto a `&str`
//! makes every slice a potential panic on a non-ASCII boundary. A DDL script
//! may hold non-ASCII in a comment, a default, or a `CHECK` constraint, and
//! one allocation buys exact equivalence.
//!
//! # A defect this port fixes rather than reproduces
//!
//! The Rust spike this file is derived from ended a block comment by
//! searching for the first `*` **or** `/` at or after the opening `/*`, then
//! skipping two characters. That is correct only for `/* plain */`. For
//! `/* note / still comment */ SELECT 1;` it resumed at the `/` inside the
//! comment, so the comment's own text was handed to the driver as SQL and the
//! statement after it was mangled. `/* a * b */` diverged the same way.
//!
//! The Python original searches for the two-character sequence `*/`, which is
//! what a block comment actually ends with. The port does the same, and
//! `tests/split.rs` pins both shapes.

/// Split `script` on semicolons that terminate statements.
///
/// Semicolons inside string literals, line comments, block comments and
/// compound statement bodies do not terminate a statement. Nesting is tracked
/// by counting `BEGIN`/`CASE` against `END`, and only once `TRIGGER` has been
/// seen in the current statement — so a bare `BEGIN` used for transaction
/// control does not open a body.
#[must_use]
pub fn split_sql_statements(script: &str) -> Vec<String> {
    let chars: Vec<char> = script.chars().collect();
    let n = chars.len();

    let mut statements: Vec<String> = Vec::new();
    let mut buf = String::new();
    let mut word = String::new();
    let mut quote: Option<char> = None;
    let mut depth: i32 = 0;
    let mut in_trigger = false;
    let mut i = 0usize;

    // Consume the just-scanned word and update nesting state.
    fn flush_word(word: &mut String, depth: &mut i32, in_trigger: &mut bool) {
        if word.is_empty() {
            return;
        }
        let w = word.to_uppercase();
        word.clear();
        if w == "TRIGGER" && *depth == 0 {
            *in_trigger = true;
        } else if (w == "BEGIN" || w == "CASE") && *in_trigger {
            *depth += 1;
        } else if w == "END" && *depth > 0 {
            *depth -= 1;
        }
    }

    while i < n {
        let ch = chars[i];

        if let Some(q) = quote {
            buf.push(ch);
            if ch == q {
                // A doubled quote is an escaped quote, not a terminator.
                if i + 1 < n && chars[i + 1] == q {
                    buf.push(chars[i + 1]);
                    i += 2;
                    continue;
                }
                quote = None;
            }
            i += 1;
            continue;
        }

        if ch.is_alphanumeric() || ch == '_' {
            word.push(ch);
            buf.push(ch);
            i += 1;
            continue;
        }

        // Any other character ends the word currently being scanned.
        flush_word(&mut word, &mut depth, &mut in_trigger);

        if ch == '\'' || ch == '"' {
            quote = Some(ch);
            buf.push(ch);
            i += 1;
        } else if ch == '-' && i + 1 < n && chars[i + 1] == '-' {
            // Line comment: skip to the newline, which is then scanned
            // normally and kept — as in the Python original.
            i = find_char_from(&chars, i, '\n').unwrap_or(n);
        } else if ch == '/' && i + 1 < n && chars[i + 1] == '*' {
            // Block comment: skip past the closing `*/`.
            i = find_pair_from(&chars, i + 2, '*', '/').map_or(n, |j| j + 2);
        } else if ch == ';' && depth == 0 {
            let stmt = buf.trim().to_string();
            if !stmt.is_empty() {
                statements.push(stmt);
            }
            buf.clear();
            in_trigger = false;
            i += 1;
        } else {
            buf.push(ch);
            i += 1;
        }
    }

    flush_word(&mut word, &mut depth, &mut in_trigger);
    let tail = buf.trim().to_string();
    if !tail.is_empty() {
        statements.push(tail);
    }
    statements
}

/// Index of `needle` in `chars` at or after `start`.
fn find_char_from(chars: &[char], start: usize, needle: char) -> Option<usize> {
    if start >= chars.len() {
        return None;
    }
    (start..chars.len()).find(|&i| chars[i] == needle)
}

/// Index of the first `first` immediately followed by `second`, at or after
/// `start`.
fn find_pair_from(chars: &[char], start: usize, first: char, second: char) -> Option<usize> {
    if start + 1 >= chars.len() {
        return None;
    }
    (start..chars.len() - 1).find(|&i| chars[i] == first && chars[i + 1] == second)
}
