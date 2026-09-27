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

//! Python's value semantics for a decoded JSON value.
//!
//! One home for `bool(value)` and `str(value)`, because the crate had grown
//! **three** copies of the first and **two** of the second, and the copies had
//! already drifted: the `Number` arm read
//! `as_f64().is_some_and(|value| value != 0.0)` in `fulltext::service` and
//! `publications::fetchers::biorxiv`, and
//! `as_f64().is_none_or(|value| value != 0.0)` in `quality::cochrane_assessor`
//! (#350). The two spellings answer the same thing only while `serde_json` is
//! built without `arbitrary_precision`, so turning that feature on would have
//! made the crate disagree with itself silently — and the *majority* spelling
//! is the wrong arm.
//!
//! **`is_none_or` is the arm kept, and it is the correct one.** `as_f64()`
//! answers `None` when the number's magnitude will not fit an `f64`; Python
//! parses such a literal as a large finite number or as `inf`, both of which
//! are truthy, where `is_some_and` called it *zero*. `serde_json` cannot reach
//! that case as it is built, which is why the difference was unobservable
//! rather than absent. `serde_json_is_built_without_arbitrary_precision` below
//! states the premise, so enabling the feature is a deliberate act with a test
//! to change rather than a silent change of answer.
//!
//! `truthy` is written out arm by arm rather than delegated to `is_empty` or to
//! a language idiom because the two disagree where it matters: `{}` and `[]` are
//! **falsy** in Python and truthy in almost every other convention.

use serde_json::Value;

/// Whether a JSON number is truthy, from what `as_f64()` answered.
///
/// Split out from [`truthy`] so the `None` arm can be **tested**: `serde_json`
/// cannot produce it as the crate is built (see the feature-set test below), and
/// the three copies this module replaced disagreed about it — the majority
/// spelling read `None` as *zero*. Python reads such a literal as a large finite
/// number or as `inf`, and both are truthy, so `None` is `true`.
fn number_is_truthy(as_f64: Option<f64>) -> bool {
    as_f64.is_none_or(|value| value != 0.0)
}

/// Python's `bool(value)` for a decoded JSON value.
///
/// A **present falsy value is not an absent one**, which is what callers use
/// this to tell apart: `""`, `0`, `{}` and `[]` are falsy here, so
/// `raw.get(key).filter(truthy)` reads them as *not sent* where an
/// `is_empty()`-style test on a string would read three of the four as sent.
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
/// `None`, `True` and `False` are Python's spellings, not JSON's
/// `null`/`true`/`false`. A number renders as `serde_json` renders it, which
/// agrees with Python for every integer and ordinary decimal a remote sends.
/// Two shapes differ, and neither is papered over:
///
/// * a **list or object** is its JSON text where Python writes its `repr`
///   (`{"a":1}` against `{'a': 1}`); and
/// * a float's exponent is spelled `1e100` where Python writes `1e+100`.
///
/// Where that can be seen, so a caller knows what it is relying on: a
/// risk-of-bias judgement (`quality::cochrane_assessor`) looks the string up in
/// a fixed vocabulary, where an unrecognised spelling and a recognised one both
/// land on *Unclear risk*; the ID Converter's `live` flag is compared after
/// lowercasing, where `False` and `"false"` already agree; and its `errmsg` and
/// an unusable `pmcid` reach a log line and a fault message, where the
/// difference is the wording a human reads and not the answer.
#[must_use]
pub(crate) fn python_str(value: &Value) -> String {
    match value {
        Value::Null => "None".to_string(),
        Value::Bool(true) => "True".to_string(),
        Value::Bool(false) => "False".to_string(),
        Value::Number(number) => number.to_string(),
        Value::String(text) => text.clone(),
        other => other.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::{number_is_truthy, python_str, truthy};
    use serde_json::{json, Value};

    /// Every arm of `bool()`, each JSON type including the two that separate
    /// Python from the usual idiom.
    #[test]
    fn truthiness_is_pythons_for_every_json_type() {
        for falsy in [
            json!(null),
            json!(false),
            json!(0),
            json!(0.0),
            json!(-0.0),
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

    /// `-0.0` is a number that is *equal* to zero, and Python agrees it is
    /// falsy. Called out on its own because it is the one `f64` where a
    /// `to_string()`-based test and a `!= 0.0` test would disagree.
    #[test]
    fn negative_zero_is_falsy() {
        assert!(!truthy(&json!(-0.0)));
        assert_eq!(python_str(&json!(-0.0)), "-0.0");
        assert!(!truthy(&json!(-0.0_f64)));
    }

    /// Every arm of `str()`, with Python's spellings for the three JSON has no
    /// word for.
    #[test]
    fn str_is_pythons_for_every_json_type() {
        let cases: [(Value, &str); 9] = [
            (json!(null), "None"),
            (json!(true), "True"),
            (json!(false), "False"),
            (json!(0), "0"),
            (json!(45), "45"),
            (json!(0.0), "0.0"),
            (json!(-1.5), "-1.5"),
            (json!("text"), "text"),
            // The documented approximation: JSON text, not Python's `repr`.
            (json!({"a": 1}), "{\"a\":1}"),
        ];
        for (value, want) in cases {
            assert_eq!(python_str(&value), want, "{value}");
        }
        // A list is the same approximation, and an empty one is `[]` in both.
        assert_eq!(python_str(&json!([1, 2])), "[1,2]");
        assert_eq!(python_str(&json!([])), "[]");
    }

    /// **The `None` arm of the number rule, which no parseable JSON reaches.**
    ///
    /// `as_f64()` answers `None` when the literal's magnitude will not fit an
    /// `f64`; Python parses such a literal as a large finite number or as `inf`,
    /// and both are truthy, so the answer is `true`. The spelling is what
    /// matters: `is_some_and` would answer `false`, which is Python's answer
    /// only for a literal zero — and that is the arm two of the crate's three
    /// former copies used. `serde_json_is_built_without_arbitrary_precision`
    /// below says why this cannot be reached through `truthy` today, which is
    /// why the decision is a function with this test rather than a line.
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
    /// premise the `Number` arm's `is_none_or` rests on.
    ///
    /// With the feature on, a literal whose magnitude will not fit an `f64`
    /// keeps its text and `as_f64()` answers `None` — reachable, and truthy,
    /// which is the arm this module keeps. Without it the literal does not
    /// parse at all, so the arm is unreachable rather than wrong. Either way
    /// the arm is written for the case; this test is what makes turning the
    /// feature on a decision rather than a surprise, because the crate's three
    /// former copies would have answered *falsy* for it.
    #[test]
    fn serde_json_is_built_without_arbitrary_precision() {
        assert!(
            serde_json::from_str::<Value>("1e400").is_err(),
            "arbitrary_precision is enabled: `as_f64()` can now answer None for a \
             reachable number, and the ternary this module replaced would have \
             called it falsy"
        );
    }
}
