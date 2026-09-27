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

//! Python's `bool(value)`, `str(value)` and `repr(value)` for a decoded JSON
//! value.
//!
//! One home for `bool()` and `str()`, because the crate had grown **three** copies of the first
//! — `truthy` in `fulltext::service` and `publications::fetchers::biorxiv`,
//! `is_truthy` in `quality::cochrane_assessor` — and **three** of the second:
//! `python_str` in `fulltext::service` and `quality::cochrane_assessor`, and
//! `render_value` in `templates`. The copies had already drifted twice (#350):
//!
//! * the `Number` arm of `bool()` read `as_f64().is_some_and(|value| value !=
//!   0.0)` in two copies and `as_f64().is_none_or(|f| f != 0.0)` in the third —
//!   the same answer only while `serde_json` is built without
//!   `arbitrary_precision`, and the *majority* spelling is the wrong one; and
//! * `render_value` wrote a list or object as Python does (`[1, 2]`,
//!   `{'b': 'deep'}`) where both `python_str` copies wrote its JSON text
//!   (`[1,2]`, `{"b":"deep"}`).
//!
//! **Both disagreements are settled toward Python.** `as_f64()` answers `None`
//! when a number's magnitude will not fit an `f64`; Python parses such a literal
//! as a large finite integer or as `inf`, both truthy, so the `None` arm is
//! `true` — `is_none_or`. And `str()` on a container *is* its `repr`, so the
//! container spelling is `render_value`'s. That second choice moves output: a
//! container arriving as the ID Converter's `errmsg` or `pmcid` is now logged in
//! Python's spelling rather than as JSON text (see [`python_str`] for where else
//! the string goes, and why nothing is decided by it).
//!
//! [`python_repr`] is here because `str()` on a container is built from it, and
//! because the ID Converter's unusable-`pmcid` warning is Python's `%r`. The
//! crate's older `repr()` copies (`publications::models`,
//! `publications::fetchers::biorxiv`, `publications::sync`) and its five
//! `json_type_name`s are not folded in yet: `publications::models::python_repr`
//! writes a container as JSON text into messages the oracle compares, so moving
//! it is its own change, #365.

use serde_json::Value;

/// Whether a JSON number is truthy, from what `as_f64()` answered.
///
/// Split out from [`truthy`] so the `None` arm can be **tested**: `serde_json`
/// cannot produce it as the crate is built (see
/// `serde_json_is_built_without_arbitrary_precision` below), and two of the three
/// copies this module replaced read `None` as *zero*. Python reads such a
/// literal as a large finite integer or as `inf`, and both are truthy, so `None`
/// is `true`.
fn number_is_truthy(as_f64: Option<f64>) -> bool {
    as_f64.is_none_or(|value| value != 0.0)
}

/// Python's `bool(value)` for a decoded JSON value.
///
/// Callers use it for Python's `raw.get(key) or default`: a **present falsy
/// value is treated like an absent one**, so `Some(value) if truthy(value)` uses
/// the value and everything else takes the default. `""`, `0`, `{}` and `[]`
/// are the present falsy values; tested on the value's JSON text instead, where
/// `"0"`, `"{}"` and `"[]"` are non-empty strings, three of the four would read
/// as sent.
///
/// The `Number` arm goes through [`number_is_truthy`], and that delegation is
/// what protects it: its `None` arm is unreachable as the crate is built, so an
/// `is_some_and` written inline here would pass every test in the crate.
#[must_use]
pub(crate) fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(flag) => *flag,
        Value::Number(number) => number_is_truthy(number.as_f64()),
        Value::String(text) => !text.is_empty(),
        Value::Array(items) => !items.is_empty(),
        Value::Object(map) => !map.is_empty(),
    }
}

/// Python's `str(value)` for a decoded JSON value.
///
/// A string is its own text, and the scalars take Python's spellings — `None`,
/// `True`, `False` — where JSON writes `null`, `true`, `false`. A list or object
/// takes Python's `repr`, which is what `str()` shows for a container: `[1, 2]`
/// and `{'b': 'deep'}`, comma-space separated, a string inside it single-quoted.
///
/// **Where this is not Python's answer**, each pinned by a test below so a
/// `serde_json` upgrade that moves one is seen rather than inherited:
///
/// * **A float below `1e-4` in magnitude.** Python switches to exponent form
///   below `1e-4` and pads the exponent to two digits (`1e-05`, `1.5e-07`);
///   `serde_json` 1.0.151 writes `0.00001` down to `1e-5` and pads nothing
///   (`1.5e-7`). The two agree again from an exponent of `-10`, and at every
///   positive exponent (`1e+16`, `1e+100`).
/// * **An integer outside `i64`/`u64`.** `serde_json` parses it as a float
///   (`18446744073709551616` renders `1.8446744073709552e+19`); Python keeps the
///   exact integer. The literal `-0` is the same shape: `-0.0` against Python's
///   `0`.
/// * **A string inside a container** is always single-quoted and never escaped;
///   Python's `repr` switches to double quotes for a string holding `'` and not
///   `"`, and escapes control characters (`["it's"]` against `['it's']`).
/// * **An object's keys** come out sorted, `serde_json` being built without
///   `preserve_order`; Python keeps the document's order.
///
/// **Where the string goes, and why none of these decides anything today:**
///
/// * a risk-of-bias judgement (`quality::cochrane_assessor`) is matched
///   *exactly* against a fixed vocabulary (`low`, `high risk`, …), which no
///   number or container spelling in either language belongs to — so every such
///   value is *Unclear risk* in both;
/// * the ID Converter's `live` flag (`fulltext::service`) is compared,
///   lowercased, to `"false"`, which no number or container spelling in either
///   language lowercases to; `False` and `"false"` agreeing there is the `Bool`
///   arm, not an approximation;
/// * the ID Converter's `errmsg` (DEBUG, through this function) and an unusable
///   `pmcid` (WARNING, through [`python_repr`]) reach log lines that
///   `FullTextService::log_lines()` and `warnings()` return to a caller — the
///   approximations change the wording there (a `pmcid` of `0.00001` logs
///   `0.00001` where Python logs `1e-05`), not an outcome. The `pmcid`'s fault
///   message goes nowhere: `TierFailures::record` keeps a fault's name only; and
/// * a template variable (`templates`) is rendered into a prompt, which is why
///   the container spelling is Python's: a prompt comparing against one is the
///   place a JSON spelling would be a real difference.
#[must_use]
pub(crate) fn python_str(value: &Value) -> String {
    match value {
        Value::Null => "None".to_string(),
        Value::Bool(true) => "True".to_string(),
        Value::Bool(false) => "False".to_string(),
        Value::Number(number) => number.to_string(),
        Value::String(text) => text.clone(),
        Value::Array(items) => {
            let parts: Vec<String> = items.iter().map(python_repr).collect();
            format!("[{}]", parts.join(", "))
        }
        Value::Object(map) => {
            let parts: Vec<String> = map
                .iter()
                .map(|(key, item)| format!("'{key}': {}", python_repr(item)))
                .collect();
            format!("{{{}}}", parts.join(", "))
        }
    }
}

/// Python's `repr(value)` for a decoded JSON value — what `%r` and `{value!r}`
/// print, and what `str()` shows for each item of a container.
///
/// It differs from [`python_str`] only for a string, which is quoted: the
/// distinction between `str(x)` and `repr(x)`. The quoting is the one listed
/// under [`python_str`] — always single quotes, never escaped — and every other
/// approximation there applies here too.
#[must_use]
pub(crate) fn python_repr(value: &Value) -> String {
    match value {
        Value::String(text) => format!("'{text}'"),
        other => python_str(other),
    }
}

#[cfg(test)]
mod tests {
    use super::{number_is_truthy, python_repr, python_str, truthy};
    use serde_json::{json, Value};

    /// A value the way a remote body delivers it: parsed from JSON text, not
    /// built with `json!`, so the number is whatever `serde_json`'s parser made
    /// of the literal.
    fn parsed(text: &str) -> Value {
        serde_json::from_str(text).expect("valid JSON")
    }

    /// Every arm of `bool()`, each JSON type including the two that separate
    /// Python from the usual idiom.
    #[test]
    fn truthiness_is_pythons_for_every_json_type() {
        for falsy in [
            json!(null),
            json!(false),
            json!(0),
            json!(0.0),
            json!(""),
            json!([]),
            json!({}),
        ] {
            assert!(!truthy(&falsy), "{falsy} is falsy in Python");
        }
        for truthy_value in [
            json!(true),
            json!(1),
            json!(-1),
            json!(0.5),
            json!(-0.5),
            json!("x"),
            json!("0"),
            json!("false"),
            // A container's truth is its length, not its contents': `[0]` and
            // `{"a": false}` are both truthy in Python.
            json!([0]),
            json!({"a": false}),
        ] {
            assert!(truthy(&truthy_value), "{truthy_value} is truthy in Python");
        }
    }

    /// Both spellings of a negative zero are falsy, in Python as here. The
    /// integer literal `-0` is where the two languages part company on `str()`:
    /// `serde_json` makes it the float `-0.0`, Python the integer `0`.
    #[test]
    fn negative_zero_is_falsy_in_both_spellings() {
        assert!(!truthy(&parsed("-0")));
        assert!(!truthy(&parsed("-0.0")));
        assert_eq!(python_str(&parsed("-0.0")), "-0.0");
        // The documented approximation: Python writes `0`.
        assert_eq!(python_str(&parsed("-0")), "-0.0");
    }

    /// Every scalar arm of `str()`, with Python's spellings for the three JSON
    /// spells differently.
    #[test]
    fn str_is_pythons_for_every_scalar() {
        let cases: [(Value, &str); 8] = [
            (json!(null), "None"),
            (json!(true), "True"),
            (json!(false), "False"),
            (json!(0), "0"),
            (json!(45), "45"),
            (json!(0.0), "0.0"),
            (json!(-1.5), "-1.5"),
            // A top-level string is its own text, unquoted.
            (json!("text"), "text"),
        ];
        for (value, want) in cases {
            assert_eq!(python_str(&value), want, "{value}");
        }
    }

    /// A container is its Python `repr`: comma-space separated, strings inside
    /// it single-quoted, and every scalar inside it in Python's spelling.
    #[test]
    fn a_container_is_its_python_repr() {
        let cases: [(Value, &str); 6] = [
            (json!([]), "[]"),
            (json!({}), "{}"),
            (json!([1, 2]), "[1, 2]"),
            (json!({"b": "deep"}), "{'b': 'deep'}"),
            (json!(["x"]), "['x']"),
            (
                json!([true, null, "a", 1.5, {"k": [false]}]),
                "[True, None, 'a', 1.5, {'k': [False]}]",
            ),
        ];
        for (value, want) in cases {
            assert_eq!(python_str(&value), want, "{value}");
        }
    }

    /// `repr()` quotes a top-level string and is `str()` for everything else.
    #[test]
    fn repr_quotes_a_string_and_is_str_otherwise() {
        assert_eq!(python_repr(&json!("garbage")), "'garbage'");
        assert_eq!(python_repr(&json!("")), "''");
        for value in [
            json!(null),
            json!(true),
            json!(5),
            json!(1.5),
            json!(["x"]),
            json!({"k": "v"}),
        ] {
            assert_eq!(python_repr(&value), python_str(&value), "{value}");
        }
    }

    /// The two container approximations, pinned so the day either stops being
    /// true is seen: a string holding `'` keeps single quotes (Python switches
    /// to double), and an object's keys are sorted (Python keeps the
    /// document's order).
    #[test]
    fn the_container_approximations_are_what_the_doc_says() {
        // Python: `["it's"]`.
        assert_eq!(python_str(&json!(["it's"])), "['it's']");
        // Python: `{'b': 1, 'a': 2}`.
        assert_eq!(
            python_str(&parsed(r#"{"b": 1, "a": 2}"#)),
            "{'a': 2, 'b': 1}"
        );
    }

    /// Numbers as a remote sends them, where `serde_json`'s rendering **agrees**
    /// with Python's — including the positive exponent, which a former comment
    /// here claimed was spelled differently.
    #[test]
    fn number_rendering_agrees_with_python_where_the_doc_says_it_does() {
        for (literal, python) in [
            ("1e100", "1e+100"),
            ("1e16", "1e+16"),
            ("1e15", "1000000000000000.0"),
            ("0.0001", "0.0001"),
            ("1e-10", "1e-10"),
            ("0.50", "0.5"),
            ("1E5", "100000.0"),
            ("18446744073709551615", "18446744073709551615"),
            ("-9223372036854775808", "-9223372036854775808"),
        ] {
            assert_eq!(python_str(&parsed(literal)), python, "{literal}");
        }
    }

    /// Numbers where `serde_json` 1.0.151 and Python part company, each with
    /// Python's answer beside it. These are the documented approximations; a
    /// `serde_json` upgrade that closes one fails here, which is the prompt to
    /// correct [`python_str`]'s doc.
    #[test]
    fn number_rendering_differs_from_python_exactly_where_the_doc_says() {
        for (literal, ours, python) in [
            ("0.00001", "0.00001", "1e-05"),
            ("0.000015", "0.000015", "1.5e-05"),
            ("1e-6", "1e-6", "1e-06"),
            ("1.5e-7", "1.5e-7", "1.5e-07"),
            (
                "18446744073709551616",
                "1.8446744073709552e+19",
                "18446744073709551616",
            ),
            (
                "-9223372036854775809",
                "-9.223372036854776e+18",
                "-9223372036854775809",
            ),
        ] {
            assert_ne!(ours, python, "{literal} is listed as a difference");
            assert_eq!(python_str(&parsed(literal)), ours, "{literal}");
        }
    }

    /// **The `None` arm of the number rule, which no parseable JSON reaches.**
    ///
    /// `as_f64()` answers `None` when the literal's magnitude will not fit an
    /// `f64`; Python parses such a literal as a large finite integer or as
    /// `inf`, and both are truthy, so the answer is `true`. The spelling is what
    /// matters: `is_some_and` would answer `false`, which is Python's answer
    /// only for a zero — and that is the arm two of the crate's three former
    /// copies used. `serde_json_is_built_without_arbitrary_precision` below says
    /// why this cannot be reached through `truthy` today, which is why the
    /// decision is a function with this test rather than a line.
    #[test]
    fn an_unrepresentable_number_is_truthy() {
        assert!(number_is_truthy(None));
        assert!(number_is_truthy(Some(f64::INFINITY)));
        assert!(number_is_truthy(Some(f64::NEG_INFINITY)));
        // `nan != 0.0` is true, and `bool(float('nan'))` is true in Python too.
        assert!(number_is_truthy(Some(f64::NAN)));
        assert!(number_is_truthy(Some(1.0)));
        assert!(number_is_truthy(Some(-1.0)));
        assert!(!number_is_truthy(Some(0.0)));
        assert!(!number_is_truthy(Some(-0.0)));
    }

    /// **`serde_json` is built without `arbitrary_precision`**, which is the
    /// premise of [`python_str`]'s number rendering, and was the premise that
    /// let the replaced `is_some_and` spelling go unnoticed.
    ///
    /// With the feature on, a number keeps its literal text: `1e400` parses
    /// (and `as_f64()` answers `None` for it, which [`number_is_truthy`] already
    /// reads correctly), and a number's `Display` writes the text it arrived as,
    /// so `0.50` would render `0.50` where Python writes `0.5`. The number
    /// tests above would then describe a different crate. Cargo unifies
    /// features across a build, so a downstream binary enabling the feature
    /// changes this crate's answer without this test running there; what the
    /// test protects is this crate's own build.
    #[test]
    fn serde_json_is_built_without_arbitrary_precision() {
        let message = "arbitrary_precision is enabled: numbers now keep their literal text, \
                       so python_str's documented number rendering no longer holds";
        assert!(serde_json::from_str::<Value>("1e400").is_err(), "{message}");
        assert_eq!(parsed("0.50").to_string(), "0.5", "{message}");
    }
}
