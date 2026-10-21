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

//! Embedding constants and the pure vector rules shared with the Python library.

use std::fmt;

/// Number of values kept from a model output.
pub const EMBEDDING_DIM: usize = 256;
/// Prefix the model expects on a search query.
pub const QUERY_PREFIX: &str = "query: ";
/// Longest input, in tokens, the embedder accepts.
pub const MAX_TOKENS: usize = 8192;
/// A norm below this is treated as this, so an all-zero vector stays zeros.
const NORM_FLOOR: f64 = 1e-12;

/// A model output had fewer than [`EMBEDDING_DIM`] values.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct VectorTooShort {
    /// How many values the vector had.
    pub len: usize,
}

impl fmt::Display for VectorTooShort {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "vector has {} values, need at least {EMBEDDING_DIM}",
            self.len
        )
    }
}

impl std::error::Error for VectorTooShort {}

/// A text was empty or whitespace-only.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct EmptyText {
    /// Position of the first such text.
    pub index: usize,
}

impl fmt::Display for EmptyText {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "text at index {} is empty or whitespace-only",
            self.index
        )
    }
}

impl std::error::Error for EmptyText {}

/// Keep the first [`EMBEDDING_DIM`] values and scale them to unit length.
///
/// The norm is summed in `f64`, as Python does. A norm below `1e-12` is
/// replaced by it, so an all-zero head gives zeros.
///
/// # Errors
///
/// [`VectorTooShort`] if `vector` has fewer than [`EMBEDDING_DIM`] values.
pub fn truncate_and_normalize(vector: &[f32]) -> Result<Vec<f32>, VectorTooShort> {
    if vector.len() < EMBEDDING_DIM {
        return Err(VectorTooShort { len: vector.len() });
    }
    let head = &vector[..EMBEDDING_DIM];
    let norm = head
        .iter()
        .map(|&x| f64::from(x) * f64::from(x))
        .sum::<f64>()
        .sqrt()
        .max(NORM_FLOOR);
    Ok(head.iter().map(|&x| (f64::from(x) / norm) as f32).collect())
}

/// The text the model embeds for a search query.
#[must_use]
pub fn query_text(query: &str) -> String {
    format!("{QUERY_PREFIX}{query}")
}

/// Python's `str.strip()` whitespace: Unicode whitespace plus U+001C to U+001F,
/// which `char::is_whitespace` omits.
fn is_blank(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// Refuse empty or whitespace-only texts.
///
/// # Errors
///
/// [`EmptyText`] carrying the index of the first such text.
pub fn check_texts(texts: &[&str]) -> Result<(), EmptyText> {
    match texts.iter().position(|t| t.chars().all(is_blank)) {
        Some(index) => Err(EmptyText { index }),
        None => Ok(()),
    }
}
