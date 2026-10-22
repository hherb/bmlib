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
/// The smallest norm the kept values may have; below it there is no direction
/// to scale to unit length.
const NORM_FLOOR: f64 = 1e-12;

/// A model output that cannot be made into an embedding.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum VectorError {
    /// The vector had fewer than [`EMBEDDING_DIM`] values.
    TooShort {
        /// How many values the vector had.
        len: usize,
    },
    /// A kept value is NaN or infinite, or the kept values' norm is below
    /// `1e-12`. A healthy model produces neither; a corrupt GGUF or a backend
    /// fault can, and such a vector stored silently would sit at a fixed end of
    /// every cosine ranking.
    NotScalable,
}

impl fmt::Display for VectorError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::TooShort { len } => {
                write!(f, "vector has {len} values, need at least {EMBEDDING_DIM}")
            }
            Self::NotScalable => write!(
                f,
                "the kept values are NaN, infinite or of norm below {NORM_FLOOR:e}, \
                 so cannot be scaled to unit length"
            ),
        }
    }
}

impl std::error::Error for VectorError {}

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
/// The norm is summed in `f64`, as Python does. Values past
/// [`EMBEDDING_DIM`] are dropped unread.
///
/// # Errors
///
/// [`VectorError::TooShort`] if `vector` has fewer than [`EMBEDDING_DIM`]
/// values; [`VectorError::NotScalable`] if a kept value is NaN or infinite or
/// the kept values' norm is below `1e-12`.
pub fn truncate_and_normalize(vector: &[f32]) -> Result<Vec<f32>, VectorError> {
    if vector.len() < EMBEDDING_DIM {
        return Err(VectorError::TooShort { len: vector.len() });
    }
    let head = &vector[..EMBEDDING_DIM];
    let norm = head
        .iter()
        .map(|&x| f64::from(x) * f64::from(x))
        .sum::<f64>()
        .sqrt();
    // A NaN or infinite value carries into the norm; an f32's square cannot
    // overflow an f64.
    if !(norm.is_finite() && norm >= NORM_FLOOR) {
        return Err(VectorError::NotScalable);
    }
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
