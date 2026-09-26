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

//! Backend-neutral parameter and column values.
//!
//! Python hands the driver a tuple of whatever it likes and lets duck typing
//! sort it out. Rust needs one concrete type both backends can accept, so
//! every call site gains a conversion; [`params!`] keeps it out of sight.
//!
//! This is the boundary type every ported module passes through, which is why
//! it is written first and deliberately: changing it later touches everything
//! above it. See `spikes/db-rs/FINDINGS.md`, "What costs more".

use std::sync::Arc;

use crate::db::error::{DbError, Result};

/// A value crossing the driver boundary in either direction.
#[derive(Debug, Clone, PartialEq)]
pub enum Value {
    /// SQL NULL.
    Null,
    /// Integer.
    Int(i64),
    /// Floating point.
    Real(f64),
    /// Text.
    Text(String),
    /// Binary blob.
    Blob(Vec<u8>),
}

impl Value {
    /// Read as an integer, if it is one.
    #[must_use]
    pub fn as_i64(&self) -> Option<i64> {
        match self {
            Value::Int(i) => Some(*i),
            _ => None,
        }
    }

    /// Read as text, if it is text.
    #[must_use]
    pub fn as_str(&self) -> Option<&str> {
        match self {
            Value::Text(s) => Some(s),
            _ => None,
        }
    }

    /// Read as a float, accepting an integer.
    #[must_use]
    pub fn as_f64(&self) -> Option<f64> {
        match self {
            Value::Real(r) => Some(*r),
            Value::Int(i) => Some(*i as f64),
            _ => None,
        }
    }

    /// Read as a blob, if it is one.
    #[must_use]
    pub fn as_blob(&self) -> Option<&[u8]> {
        match self {
            Value::Blob(b) => Some(b),
            _ => None,
        }
    }

    /// True if this is SQL NULL.
    #[must_use]
    pub fn is_null(&self) -> bool {
        matches!(self, Value::Null)
    }
}

impl From<&str> for Value {
    fn from(v: &str) -> Self {
        Value::Text(v.to_string())
    }
}
impl From<String> for Value {
    fn from(v: String) -> Self {
        Value::Text(v)
    }
}
impl From<&String> for Value {
    fn from(v: &String) -> Self {
        Value::Text(v.clone())
    }
}
impl From<i64> for Value {
    fn from(v: i64) -> Self {
        Value::Int(v)
    }
}
impl From<i32> for Value {
    fn from(v: i32) -> Self {
        Value::Int(i64::from(v))
    }
}
impl From<usize> for Value {
    fn from(v: usize) -> Self {
        Value::Int(v as i64)
    }
}
impl From<f64> for Value {
    fn from(v: f64) -> Self {
        Value::Real(v)
    }
}
/// SQLite has no boolean type and stores 0/1, exactly as the Python port does.
impl From<bool> for Value {
    fn from(v: bool) -> Self {
        Value::Int(i64::from(v))
    }
}
impl From<Vec<u8>> for Value {
    fn from(v: Vec<u8>) -> Self {
        Value::Blob(v)
    }
}
impl<T> From<Option<T>> for Value
where
    Value: From<T>,
{
    fn from(v: Option<T>) -> Self {
        match v {
            Some(x) => Value::from(x),
            None => Value::Null,
        }
    }
}

/// Build a `Vec<Value>` from heterogeneous arguments.
///
/// The Rust stand-in for Python's parameter tuple.
#[macro_export]
macro_rules! params {
    () => { ::std::vec::Vec::<$crate::Value>::new() };
    ($($v:expr),+ $(,)?) => { ::std::vec![$($crate::Value::from($v)),+] };
}

/// One result row, addressable by column name or position.
///
/// This is the single type replacing both `sqlite3.Row` and psycopg2's
/// `RealDictRow`. Because it keeps values in column order *and* by name, the
/// `fetch_scalar` special case those two types forced in Python — "first
/// column" meaning `row[0]` on one backend and `list(row.values())[0]` on the
/// other — does not arise here.
#[derive(Debug, Clone)]
pub struct Row {
    columns: Arc<Vec<String>>,
    values: Vec<Value>,
}

impl Row {
    /// Build a row from its column names and values.
    #[must_use]
    pub fn new(columns: Arc<Vec<String>>, values: Vec<Value>) -> Self {
        Row { columns, values }
    }

    /// Value of the named column.
    ///
    /// # Errors
    ///
    /// If no column carries that name.
    pub fn get(&self, name: &str) -> Result<&Value> {
        self.columns
            .iter()
            .position(|c| c == name)
            .map(|i| &self.values[i])
            .ok_or_else(|| DbError::Column(format!("no column named {name:?}")))
    }

    /// Text of the named column.
    ///
    /// # Errors
    ///
    /// If the column is absent or is not text.
    pub fn get_str(&self, name: &str) -> Result<&str> {
        self.get(name)?
            .as_str()
            .ok_or_else(|| DbError::Column(format!("column {name:?} is not text")))
    }

    /// Integer of the named column.
    ///
    /// # Errors
    ///
    /// If the column is absent or is not an integer.
    pub fn get_i64(&self, name: &str) -> Result<i64> {
        self.get(name)?
            .as_i64()
            .ok_or_else(|| DbError::Column(format!("column {name:?} is not an integer")))
    }

    /// Value at a position, if the row is that wide.
    #[must_use]
    pub fn at(&self, index: usize) -> Option<&Value> {
        self.values.get(index)
    }

    /// Column names, in order.
    #[must_use]
    pub fn columns(&self) -> &[String] {
        &self.columns
    }

    /// Values, in column order.
    #[must_use]
    pub fn values(&self) -> &[Value] {
        &self.values
    }
}
