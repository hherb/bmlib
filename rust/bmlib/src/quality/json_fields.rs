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

//! The numeric half of Python's `bmlib/quality/_json_fields.py`.
//!
//! Every numeric read of a model's JSON reply in this package goes through
//! these, so the rule is stated once: **absent, `null` and wrong-typed are the
//! same answer — unstated**. A `bool` is refused by every numeric reader
//! (`int(True)` is 1 and `float(True)` is 1.0, the most confident answer there
//! is), and a non-finite float is refused because it is no measurement.
//!
//! A numeric **string** is parsed, with Python's `int()` / `float()` rules:
//! surrounding whitespace is stripped, a sign is accepted, and an underscore is
//! accepted between two digits (`"1_000"`). Python's `int()` and `float()` also
//! accept non-ASCII decimal digits; these do not, and no model reply is known to
//! carry one.
//!
//! Python logs a refused value at DEBUG; the port has no logging facade and
//! does not.

use serde_json::Value;

/// Python's `as_int`: `value` as an integer, or `None`.
///
/// An integer verbatim, a finite float truncated toward zero (`int(100.5)` is
/// 100), and a string parsed with `int()` — so `"45.5"` is **refused**, as
/// `int("45.5")` raises. A `bool`, a list, an object and a non-finite float are
/// refused.
///
/// One divergence, stated rather than hidden: Python's `int` is unbounded, so a
/// count beyond `i64` survives there exactly. Here it is refused, because a
/// saturating cast would record `i64::MAX`, a count nobody reported.
#[must_use]
pub(crate) fn as_int(value: Option<&Value>) -> Option<i64> {
    match value? {
        Value::Number(number) => number.as_i64().or_else(|| {
            let float = number.as_f64()?;
            truncated_i64(float)
        }),
        Value::String(text) => strip_digit_underscores(text.trim())?.parse::<i64>().ok(),
        _ => None,
    }
}

/// Python's `as_float`: `value` as a **finite** float, or `None`.
///
/// A number verbatim and a numeric string parsed with `float()`; a `bool` and
/// anything non-finite (`"nan"`, `"inf"`, `"1e400"`) are refused.
#[must_use]
pub(crate) fn as_float(value: Option<&Value>) -> Option<f64> {
    let float = match value? {
        Value::Number(number) => number.as_f64()?,
        Value::String(text) => strip_digit_underscores(text.trim())?.parse::<f64>().ok()?,
        _ => return None,
    };
    float.is_finite().then_some(float)
}

/// Python's `max(low, min(high, x))` — equally `min(high, max(low, x))` — for
/// a finite `x`.
///
/// Not [`f64::clamp`], which differs on a signed zero: Python's two-argument
/// `max` returns its **first** argument unless the second is greater, so
/// `max(0.0, -0.0)` is `0.0`, where `(-0.0f64).clamp(0.0, 1.0)` is `-0.0` and
/// a formatter renders it as `-0%`.
#[must_use]
pub(crate) fn py_clamp(x: f64, low: f64, high: f64) -> f64 {
    if x > low {
        if x < high {
            x
        } else {
            high
        }
    } else {
        low
    }
}

/// A finite float truncated toward zero, or `None` outside `i64`.
fn truncated_i64(float: f64) -> Option<i64> {
    // `i64::MIN` is exactly representable; `i64::MAX` is not, so the upper
    // bound is exclusive at 2^63.
    const LIMIT: f64 = 9_223_372_036_854_775_808.0;
    let truncated = float.trunc();
    (float.is_finite() && (-LIMIT..LIMIT).contains(&truncated)).then_some(truncated as i64)
}

/// `text` with Python's digit-separator underscores removed, or `None` when an
/// underscore is anywhere else.
///
/// Python accepts `"1_000"` and `"1.000_1"` in `int()` / `float()`, and refuses
/// `"_1"`, `"1_"`, `"1__0"` and `"1_.5"`: an underscore must sit between two
/// digits.
fn strip_digit_underscores(text: &str) -> Option<std::borrow::Cow<'_, str>> {
    if !text.contains('_') {
        return Some(std::borrow::Cow::Borrowed(text));
    }
    let bytes = text.as_bytes();
    for (index, byte) in bytes.iter().enumerate() {
        if *byte == b'_' {
            let before = index.checked_sub(1).map(|i| bytes[i]);
            let after = bytes.get(index + 1).copied();
            if !(before.is_some_and(|b| b.is_ascii_digit())
                && after.is_some_and(|b| b.is_ascii_digit()))
            {
                return None;
            }
        }
    }
    Some(std::borrow::Cow::Owned(text.replace('_', "")))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn an_integer_string_is_read_and_a_decimal_one_refused() {
        assert_eq!(as_int(Some(&json!("45"))), Some(45));
        assert_eq!(as_int(Some(&json!(" +45 "))), Some(45));
        assert_eq!(as_int(Some(&json!("1_000"))), Some(1000));
        // `int("45.5")` raises, so it is unstated — not 45.
        assert_eq!(as_int(Some(&json!("45.5"))), None);
        assert_eq!(as_int(Some(&json!("1e3"))), None);
        assert_eq!(as_int(Some(&json!("nan"))), None);
        assert_eq!(as_int(Some(&json!("inf"))), None);
        assert_eq!(as_int(Some(&json!("1__0"))), None);
    }

    #[test]
    fn a_float_is_truncated_and_a_bool_refused() {
        assert_eq!(as_int(Some(&json!(100.5))), Some(100));
        assert_eq!(as_int(Some(&json!(-2.7))), Some(-2));
        assert_eq!(as_int(Some(&json!(true))), None);
        assert_eq!(as_int(Some(&json!(null))), None);
        assert_eq!(as_int(None), None);
    }

    #[test]
    fn a_count_beyond_i64_is_refused_rather_than_saturated() {
        assert_eq!(as_int(Some(&json!(u64::MAX))), None);
        assert_eq!(as_int(Some(&json!(1e300))), None);
    }

    #[test]
    fn a_float_string_is_read_and_a_non_finite_one_refused() {
        assert_eq!(as_float(Some(&json!("0.8"))), Some(0.8));
        assert_eq!(as_float(Some(&json!(" 0.8 "))), Some(0.8));
        assert_eq!(as_float(Some(&json!("1_0"))), Some(10.0));
        assert_eq!(as_float(Some(&json!(7))), Some(7.0));
        for refused in ["nan", "inf", "-inf", "Infinity", "1e400", "1_", "abc", ""] {
            assert_eq!(as_float(Some(&json!(refused))), None, "{refused:?}");
        }
        assert_eq!(as_float(Some(&json!(false))), None);
        assert_eq!(as_float(Some(&json!([0.5]))), None);
    }

    #[test]
    fn py_clamp_keeps_pythons_signed_zero() {
        assert!(py_clamp(-0.0, 0.0, 1.0).is_sign_positive());
        assert_eq!(py_clamp(-0.5, 0.0, 1.0), 0.0);
        assert_eq!(py_clamp(1.4, 0.0, 1.0), 1.0);
        assert_eq!(py_clamp(0.7, 0.0, 1.0), 0.7);
        assert_eq!(py_clamp(12.0, 0.0, 10.0), 10.0);
    }
}
