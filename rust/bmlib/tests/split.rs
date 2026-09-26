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

//! Statement splitting — `db/operations._split_sql_statements`.
//!
//! The first group here is the regression suite for a defect **in this port's
//! own Rust lineage**: the spike this file derives from ended a block comment
//! by searching for the first `*` *or* `/` and skipping two characters, which
//! is right only for `/* plain */`. `/* note / still comment */` resumed at
//! the `/` inside the comment, so the comment's text was handed to the driver
//! as SQL. The Python original searches for the two-character sequence `*/`,
//! and so does the port — see the module docstring in `src/db/split.rs`.
//!
//! The spike's own 62 tests passed with that defect in place, because none of
//! them put a `*` or a `/` inside a block comment. That is worth stating: it is
//! the reason these cases are written out individually rather than as one
//! "handles comments" test.

use bmlib::db::split::split_sql_statements;

// ---------------------------------------------------------------------------
// Block comments containing `*` or `/` — the fixed defect
// ---------------------------------------------------------------------------

#[test]
fn a_slash_inside_a_block_comment_does_not_end_it() {
    // The spike resumed at the `/` in "note / still", yielding the garbled
    // statement `still comment */ SELECT 1`.
    let out = split_sql_statements("/* note / still comment */ SELECT 1;");
    assert_eq!(out, vec!["SELECT 1"]);
}

#[test]
fn a_star_inside_a_block_comment_does_not_end_it() {
    // The spike resumed at the `*` in "a * b", yielding `b */ SELECT 1`.
    let out = split_sql_statements("/* a * b */ SELECT 1;");
    assert_eq!(out, vec!["SELECT 1"]);
}

#[test]
fn a_semicolon_inside_a_block_comment_does_not_split() {
    let out = split_sql_statements("/* one; two; three */ SELECT 1;");
    assert_eq!(out, vec!["SELECT 1"]);
}

#[test]
fn back_to_back_stars_are_not_a_terminator() {
    // `**` is not `*/`, so this comment runs to the real terminator.
    let out = split_sql_statements("/* ** still open **/ SELECT 2;");
    assert_eq!(out, vec!["SELECT 2"]);
}

#[test]
fn a_plain_block_comment_is_skipped() {
    let out = split_sql_statements("/* plain */ SELECT 1;");
    assert_eq!(out, vec!["SELECT 1"]);
}

#[test]
fn an_unterminated_block_comment_swallows_the_rest() {
    // Matches Python: `find` returns -1 and the index jumps to the end.
    let out = split_sql_statements("/* never closed\nSELECT 1;");
    assert!(out.is_empty(), "got {out:?}");
}

#[test]
fn a_block_comment_between_statements_is_skipped() {
    let out = split_sql_statements("SELECT 1; /* note / here */ SELECT 2;");
    assert_eq!(out, vec!["SELECT 1", "SELECT 2"]);
}

// ---------------------------------------------------------------------------
// String literals
// ---------------------------------------------------------------------------

#[test]
fn a_semicolon_inside_a_string_does_not_split() {
    let out = split_sql_statements("INSERT INTO t VALUES ('a;b');");
    assert_eq!(out, vec!["INSERT INTO t VALUES ('a;b')"]);
}

#[test]
fn a_doubled_quote_is_an_escape_not_a_terminator() {
    let out = split_sql_statements("INSERT INTO t VALUES ('it''s; fine');");
    assert_eq!(out, vec!["INSERT INTO t VALUES ('it''s; fine')"]);
}

#[test]
fn a_double_quoted_identifier_may_contain_a_semicolon() {
    let out = split_sql_statements("CREATE TABLE \"odd;name\" (a TEXT);");
    assert_eq!(out, vec!["CREATE TABLE \"odd;name\" (a TEXT)"]);
}

#[test]
fn a_block_comment_opener_inside_a_string_is_literal() {
    let out = split_sql_statements("CREATE TABLE t (a TEXT DEFAULT '/*');");
    assert_eq!(out, vec!["CREATE TABLE t (a TEXT DEFAULT '/*')"]);
}

// ---------------------------------------------------------------------------
// Line comments
// ---------------------------------------------------------------------------

#[test]
fn a_semicolon_in_a_line_comment_does_not_split() {
    let out = split_sql_statements("SELECT 1; -- a; b\nSELECT 2;");
    assert_eq!(out, vec!["SELECT 1", "SELECT 2"]);
}

#[test]
fn a_line_comment_at_end_of_input_ends_the_script() {
    let out = split_sql_statements("SELECT 1;\n-- trailing note");
    assert_eq!(out, vec!["SELECT 1"]);
}

#[test]
fn a_double_dash_inside_a_string_is_not_a_comment() {
    let out = split_sql_statements("INSERT INTO t VALUES ('a--b');");
    assert_eq!(out, vec!["INSERT INTO t VALUES ('a--b')"]);
}

// ---------------------------------------------------------------------------
// Compound statement bodies
// ---------------------------------------------------------------------------

#[test]
fn a_trigger_body_is_one_statement() {
    let script = "CREATE TRIGGER trg AFTER INSERT ON t BEGIN\n  UPDATE t SET a = 1;\n  \
                  UPDATE t SET b = 2;\nEND;";
    let out = split_sql_statements(script);
    assert_eq!(out.len(), 1, "got {out:?}");
    assert!(out[0].starts_with("CREATE TRIGGER"));
    assert!(out[0].ends_with("END"));
}

#[test]
fn a_case_inside_a_trigger_body_does_not_close_it_early() {
    // Counting CASE against END is what keeps the body open.
    let script = "CREATE TRIGGER trg AFTER INSERT ON t BEGIN\n  \
                  UPDATE t SET a = CASE WHEN 1 THEN 1 ELSE 2 END;\n  \
                  UPDATE t SET b = 3;\nEND;";
    let out = split_sql_statements(script);
    assert_eq!(out.len(), 1, "got {out:?}");
}

#[test]
fn a_bare_begin_does_not_open_a_body() {
    // Transaction control, not a compound body: the two statements split.
    let out = split_sql_statements("BEGIN; SELECT 1;");
    assert_eq!(out, vec!["BEGIN", "SELECT 1"]);
}

#[test]
fn a_trigger_after_a_bare_begin_still_opens_a_body() {
    let script = "BEGIN; CREATE TRIGGER trg AFTER INSERT ON t BEGIN\n  \
                  UPDATE t SET a = 1;\nEND;";
    let out = split_sql_statements(script);
    assert_eq!(out.len(), 2, "got {out:?}");
    assert_eq!(out[0], "BEGIN");
    assert!(out[1].starts_with("CREATE TRIGGER"));
}

// ---------------------------------------------------------------------------
// Boundaries
// ---------------------------------------------------------------------------

#[test]
fn an_empty_script_yields_nothing() {
    assert!(split_sql_statements("").is_empty());
}

#[test]
fn whitespace_only_yields_nothing() {
    assert!(split_sql_statements("  \n\t  ").is_empty());
}

#[test]
fn a_trailing_statement_without_a_semicolon_is_kept() {
    let out = split_sql_statements("SELECT 1");
    assert_eq!(out, vec!["SELECT 1"]);
}

#[test]
fn empty_statements_between_semicolons_are_dropped() {
    let out = split_sql_statements("SELECT 1;;; SELECT 2;");
    assert_eq!(out, vec!["SELECT 1", "SELECT 2"]);
}

#[test]
fn statements_are_trimmed() {
    let out = split_sql_statements("  SELECT 1  ;\n\n  SELECT 2  ;");
    assert_eq!(out, vec!["SELECT 1", "SELECT 2"]);
}

#[test]
fn non_ascii_text_does_not_panic() {
    // The reason the scan is over `Vec<char>` and not byte offsets.
    let out = split_sql_statements("INSERT INTO t VALUES ('Grüße; 日本');");
    assert_eq!(out, vec!["INSERT INTO t VALUES ('Grüße; 日本')"]);
}

#[test]
fn a_realistic_schema_splits_into_its_statements() {
    let script = "\
-- publications
CREATE TABLE publications (
    id INTEGER PRIMARY KEY,
    doi TEXT UNIQUE
);
/* one row per source */
CREATE INDEX idx_pub_doi ON publications (doi);
CREATE TRIGGER pub_touch AFTER UPDATE ON publications BEGIN
  UPDATE publications SET id = id;
END;";
    let out = split_sql_statements(script);
    assert_eq!(out.len(), 3, "got {out:?}");
    assert!(out[0].starts_with("CREATE TABLE publications"));
    assert!(out[1].starts_with("CREATE INDEX"));
    assert!(out[2].starts_with("CREATE TRIGGER"));
}

#[test]
fn known_limit_dollar_quoting_is_not_understood() {
    // Documented rather than handled: a `$$` body is not a construct this
    // splitter knows, and bmlib's schema has none. Pinned so that the day one
    // appears, the failure is named rather than mysterious.
    let out = split_sql_statements("SELECT $$a;b$$;");
    assert_eq!(out.len(), 2, "the body is split, as documented: {out:?}");
}
