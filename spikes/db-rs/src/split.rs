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
//! A direct port of `operations._split_sql_statements`, kept because it is the
//! one piece of `bmlib.db` that is pure string scanning — a fair sample of how
//! the rest of the library's text handling will port.
//!
//! It scans over a `Vec<char>`, not over byte offsets. The Python original
//! indexes by code point, and transliterating that arithmetic onto a `&str`
//! means every slice is a potential panic on a non-ASCII boundary — and a DDL
//! script may hold non-ASCII in a comment, a default, or a `CHECK` constraint.
//! One allocation buys exact equivalence, which for a port is the right trade;
//! `char_indices()` discipline is the alternative where the allocation matters.

/// Split `script` on semicolons that terminate statements.
///
/// Semicolons inside string literals, line comments, block comments and
/// compound statement bodies do not terminate a statement. Nesting is tracked
/// by counting `BEGIN`/`CASE` against `END`, and only once `TRIGGER` has been
/// seen in the current statement — so a bare `BEGIN` for transaction control
/// does not open a body.
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
        } else if w == "BEGIN" || w == "CASE" {
            if *in_trigger {
                *depth += 1;
            }
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
            i = find_from(&chars, i, &['\n']).unwrap_or(n);
        } else if ch == '/' && i + 1 < n && chars[i + 1] == '*' {
            i = find_from(&chars, i + 2, &['*', '/']).map_or(n, |j| j + 2);
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
fn find_from(chars: &[char], start: usize, needle: &[char]) -> Option<usize> {
    if needle.is_empty() || start >= chars.len() || needle.len() > chars.len() {
        return None;
    }
    (start..=chars.len() - needle.len()).find(|&i| chars[i..i + needle.len()] == *needle)
}
