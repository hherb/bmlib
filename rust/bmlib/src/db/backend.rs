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

//! Which dialect a connection speaks, and the placeholders it wants.
//!
//! Python answers "which backend is this?" by sniffing the connection's module
//! name (`is_sqlite(conn)`), because that is the only thing both drivers agree
//! on without importing the optional `psycopg2`. Here the question is answered
//! by the implementation itself, so there is nothing to sniff and
//! `is_sqlite(conn)` has no counterpart.

use std::borrow::Cow;

/// Which SQL dialect a connection speaks.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Dialect {
    /// SQLite: `?` placeholders.
    Sqlite,
    /// PostgreSQL: `$1`, `$2`, … placeholders.
    Postgres,
}

/// The placeholder a call site should write.
///
/// Always `"?"`. Kept so a mechanical port of a Python call site
/// (`ph = placeholder(conn)`) compiles unchanged; new code writes `?`
/// directly. PostgreSQL's numbered form is produced inside the backend by
/// [`rewrite_placeholders`], not by the caller.
#[must_use]
pub fn placeholder() -> &'static str {
    "?"
}

/// `count` comma-separated placeholders for an `IN (…)` list.
///
/// Returns an empty string for `count == 0`; callers must skip the clause
/// entirely in that case, since neither backend accepts an empty list. Same
/// contract as the Python original.
#[must_use]
pub fn placeholders(count: usize) -> String {
    if count == 0 {
        return String::new();
    }
    vec!["?"; count].join(", ")
}

/// Rewrite `?` placeholders to PostgreSQL's numbered `$n` form.
///
/// Only a `?` outside string literals, quoted identifiers and comments is
/// rewritten, so one inside `'a ? b'` survives. The scanning rules are the
/// ones the SQL splitter already needed.
///
/// **Known limit, stated rather than handled:** PostgreSQL's `jsonb` operators
/// are spelled `?`, `?|` and `?&`, and this rewrites them. No bmlib SQL uses
/// them; a port that starts to must escape them or move to a query builder.
#[must_use]
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
#[must_use]
pub fn adapt_sql(sql: &str, dialect: Dialect) -> Cow<'_, str> {
    match dialect {
        Dialect::Sqlite => Cow::Borrowed(sql),
        Dialect::Postgres => Cow::Owned(rewrite_placeholders(sql)),
    }
}
