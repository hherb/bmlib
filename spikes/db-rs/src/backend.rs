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

//! Dialect detection and parameter placeholders.
//!
//! Python answers "which backend is this?" by sniffing the connection's module
//! name (`is_sqlite(conn)`), because that is the only thing both drivers agree
//! on without importing the optional `psycopg2`. Here the question is answered
//! by the implementation itself, so there is nothing to sniff.

/// Which SQL dialect a connection speaks.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Dialect {
    /// SQLite: `?` placeholders.
    Sqlite,
    /// PostgreSQL: `$1`, `$2`, ... placeholders.
    Postgres,
}

/// The placeholder a call site should write.
///
/// Always `"?"`. Kept only so a mechanical port of a Python call site
/// (`ph = placeholder(conn)`) compiles unchanged; new code should write `?`
/// directly. PostgreSQL's numbered form is produced by
/// [`rewrite_placeholders`] inside the backend, not by the caller — see
/// FINDINGS.md, "Numbered placeholders".
pub fn placeholder() -> &'static str {
    "?"
}

/// `count` comma-separated placeholders for an `IN (...)` list.
///
/// Returns an empty string for `count == 0`; callers must skip the clause
/// entirely in that case, since neither backend accepts an empty list. Same
/// contract as the Python original.
pub fn placeholders(count: usize) -> String {
    if count == 0 {
        return String::new();
    }
    vec!["?"; count].join(", ")
}

/// Rewrite `?` placeholders to PostgreSQL's numbered `$n` form.
///
/// Only `?` outside string literals, quoted identifiers and comments is
/// rewritten, so a `?` inside `'a ? b'` survives. The scanning rules are the
/// ones `_split_sql_statements` already needed in Python.
///
/// Known limit, stated rather than handled: PostgreSQL's `jsonb` operators are
/// spelled `?`, `?|` and `?&`, and this would rewrite them. No bmlib SQL uses
/// them; a port that starts to must escape them as `??` or move to a builder.
pub fn rewrite_placeholders(sql: &str) -> String {
    let mut out = String::with_capacity(sql.len() + 8);
    let mut n = 0usize;
    let mut chars = sql.char_indices().peekable();
    let mut quote: Option<char> = None;

    while let Some((_, ch)) = chars.next() {
        if let Some(q) = quote {
            out.push(ch);
            if ch == q {
                // A doubled quote is an escaped quote, not a terminator.
                if chars.peek().map(|(_, c)| *c) == Some(q) {
                    out.push(q);
                    chars.next();
                } else {
                    quote = None;
                }
            }
            continue;
        }
        match ch {
            '\'' | '"' => {
                quote = Some(ch);
                out.push(ch);
            }
            '-' if chars.peek().map(|(_, c)| *c) == Some('-') => {
                out.push(ch);
                for (_, c) in chars.by_ref() {
                    out.push(c);
                    if c == '\n' {
                        break;
                    }
                }
            }
            '/' if chars.peek().map(|(_, c)| *c) == Some('*') => {
                out.push(ch);
                let mut prev = '\0';
                for (_, c) in chars.by_ref() {
                    out.push(c);
                    if prev == '*' && c == '/' {
                        break;
                    }
                    prev = c;
                }
            }
            '?' => {
                n += 1;
                out.push('$');
                out.push_str(&n.to_string());
            }
            _ => out.push(ch),
        }
    }
    out
}

/// Apply [`rewrite_placeholders`] only where the dialect needs it.
pub fn adapt_sql(sql: &str, dialect: Dialect) -> std::borrow::Cow<'_, str> {
    match dialect {
        Dialect::Sqlite => std::borrow::Cow::Borrowed(sql),
        Dialect::Postgres => std::borrow::Cow::Owned(rewrite_placeholders(sql)),
    }
}
