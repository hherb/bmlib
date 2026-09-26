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

//! Boundary-aware text chunking, and the shallow map-reduce built on it.
//!
//! A port of `bmlib/llm/text_utils.py`. This is the *pure* half of `llm/` —
//! nothing here needs a provider, a client or a network — and it is what
//! `context_processor` uses when it splits an oversized item.
//!
//! # Offsets are character offsets
//!
//! Python slices `str` by code point, so `text[start:end]` counts characters.
//! Rust's `&str` slicing counts bytes and panics on a non-boundary. Every
//! offset in this module is a **character** offset, and the slice helper
//! converts once — a chunk boundary falling inside a multi-byte character is
//! otherwise a panic, and scientific text is full of them.

use std::collections::BTreeMap;

/// Default maximum characters in one chunk.
pub const DEFAULT_CHUNK_SIZE: usize = 10_000;
/// Default characters of overlap between consecutive chunks.
pub const DEFAULT_CHUNK_OVERLAP: usize = 250;
/// Default minimum offset at which a boundary break may occur.
pub const DEFAULT_MIN_CHUNK_SIZE: usize = 500;

/// A chunk of text with positional metadata.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TextChunk {
    /// The chunk text.
    pub content: String,
    /// Start offset (inclusive) in the source text, in characters.
    pub start_pos: usize,
    /// End offset (exclusive) in the source text, in characters.
    pub end_pos: usize,
    /// Zero-based index of this chunk.
    pub chunk_index: usize,
    /// Total number of chunks the source was split into.
    pub total_chunks: usize,
}

impl TextChunk {
    /// Size of this chunk in characters.
    #[must_use]
    pub fn size(&self) -> usize {
        self.content.chars().count()
    }
}

/// Why a [`TextChunker`] could not be built.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ChunkerError {
    /// `chunk_size` was zero.
    NonPositiveChunkSize,
    /// `overlap` was negative — unrepresentable in Rust, so this variant
    /// exists only for a mechanical port of Python's error surface.
    NegativeOverlap,
    /// `overlap` was not less than `chunk_size`.
    OverlapNotLessThanChunkSize {
        /// The overlap supplied.
        overlap: usize,
        /// The chunk size supplied.
        chunk_size: usize,
    },
}

impl std::fmt::Display for ChunkerError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ChunkerError::NonPositiveChunkSize => {
                write!(f, "chunk_size must be positive, got 0")
            }
            ChunkerError::NegativeOverlap => write!(f, "overlap must be non-negative"),
            ChunkerError::OverlapNotLessThanChunkSize {
                overlap,
                chunk_size,
            } => write!(
                f,
                "overlap ({overlap}) must be less than chunk_size ({chunk_size})"
            ),
        }
    }
}

impl std::error::Error for ChunkerError {}

/// Slice `chars` by character offsets, clamped to the slice's bounds.
fn slice_chars(chars: &[char], start: usize, end: usize) -> String {
    if start >= chars.len() || end <= start {
        return String::new();
    }
    chars[start..end.min(chars.len())].iter().collect()
}

/// Index of the last occurrence of `needle` in `chars`, as a character offset.
fn rfind(chars: &[char], needle: &[char]) -> Option<usize> {
    if needle.is_empty() || needle.len() > chars.len() {
        return None;
    }
    (0..=chars.len() - needle.len())
        .rev()
        .find(|&i| chars[i..i + needle.len()] == *needle)
}

/// Sliding-window chunker with optional boundary awareness.
///
/// Overlapping chunks ensure no information is lost at chunk boundaries —
/// important for citation extraction and question answering. When
/// `boundary_aware` is set, chunk ends are pulled back to the nearest
/// paragraph or sentence break (beyond `min_chunk_size`) so sentences are not
/// split mid-way; the full text is always preserved.
#[derive(Debug, Clone)]
pub struct TextChunker {
    /// Maximum size of each chunk in characters.
    pub chunk_size: usize,
    /// Characters of overlap between consecutive chunks.
    pub overlap: usize,
    /// Prefer paragraph/sentence breaks over hard cuts.
    pub boundary_aware: bool,
    /// Minimum offset a boundary break may occur at.
    pub min_chunk_size: usize,
}

impl Default for TextChunker {
    fn default() -> Self {
        TextChunker {
            chunk_size: DEFAULT_CHUNK_SIZE,
            overlap: DEFAULT_CHUNK_OVERLAP,
            boundary_aware: true,
            min_chunk_size: DEFAULT_MIN_CHUNK_SIZE,
        }
    }
}

impl TextChunker {
    /// Build a chunker.
    ///
    /// # Errors
    ///
    /// If `chunk_size` is zero, or `overlap` is not less than `chunk_size`.
    pub fn new(
        chunk_size: usize,
        overlap: usize,
        boundary_aware: bool,
        min_chunk_size: usize,
    ) -> Result<Self, ChunkerError> {
        if chunk_size == 0 {
            return Err(ChunkerError::NonPositiveChunkSize);
        }
        if overlap >= chunk_size {
            return Err(ChunkerError::OverlapNotLessThanChunkSize {
                overlap,
                chunk_size,
            });
        }
        Ok(TextChunker {
            chunk_size,
            overlap,
            boundary_aware,
            min_chunk_size,
        })
    }

    /// Split `text` into overlapping [`TextChunk`]s.
    #[must_use]
    pub fn chunk_text(&self, text: &str) -> Vec<TextChunk> {
        if text.is_empty() {
            return Vec::new();
        }
        let chars: Vec<char> = text.chars().collect();
        let text_length = chars.len();
        if text_length <= self.chunk_size {
            return vec![TextChunk {
                content: text.to_string(),
                start_pos: 0,
                end_pos: text_length,
                chunk_index: 0,
                total_chunks: 1,
            }];
        }

        let mut chunks: Vec<TextChunk> = Vec::new();
        let mut start = 0usize;
        while start < text_length {
            let mut end = (start + self.chunk_size).min(text_length);
            if self.boundary_aware && end < text_length {
                end = self.adjust_to_boundary(&chars, start, end);
            }
            chunks.push(TextChunk {
                content: slice_chars(&chars, start, end),
                start_pos: start,
                end_pos: end,
                chunk_index: chunks.len(),
                total_chunks: 0,
            });
            if end >= text_length {
                break;
            }
            let next_start = end.saturating_sub(self.overlap);
            start = if next_start > start { next_start } else { end };
        }

        let total = chunks.len();
        for chunk in &mut chunks {
            chunk.total_chunks = total;
        }
        chunks
    }

    /// Pull `end` back to the nearest paragraph/sentence break, if any.
    ///
    /// Returns the original `end` when no suitable break exists beyond
    /// `min_chunk_size`.
    #[must_use]
    pub fn adjust_to_boundary(&self, chars: &[char], start: usize, end: usize) -> usize {
        let window: Vec<char> = chars[start..end].to_vec();

        if let Some(para_break) = rfind(&window, &['\n', '\n']) {
            if para_break > self.min_chunk_size {
                return start + para_break + 2;
            }
        }

        let sentence_needles: [&[char]; 4] = [&['.', ' '], &['.', '\n'], &['?', ' '], &['!', ' ']];
        let candidates: Vec<usize> = sentence_needles
            .iter()
            .filter_map(|n| rfind(&window, n))
            .filter(|b| *b > self.min_chunk_size)
            .collect();
        if let Some(best) = candidates.iter().max() {
            return start + best + 2;
        }

        end
    }

    /// Chunk `text` and summarise the result.
    #[must_use]
    pub fn chunk_info(&self, text: &str) -> ChunkInfo {
        let chunks = self.chunk_text(text);
        if chunks.is_empty() {
            return ChunkInfo {
                text_length: 0,
                num_chunks: 0,
                chunk_size: self.chunk_size,
                overlap: self.overlap,
                avg_chunk_size: 0,
                last_chunk_size: 0,
            };
        }
        let sizes: Vec<usize> = chunks.iter().map(TextChunk::size).collect();
        ChunkInfo {
            text_length: text.chars().count(),
            num_chunks: sizes.len(),
            chunk_size: self.chunk_size,
            overlap: self.overlap,
            avg_chunk_size: sizes.iter().sum::<usize>() / sizes.len(),
            last_chunk_size: *sizes.last().unwrap_or(&0),
        }
    }
}

/// Summary of a chunking pass.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ChunkInfo {
    /// Length of the source text in characters.
    pub text_length: usize,
    /// Number of chunks produced.
    pub num_chunks: usize,
    /// The configured chunk size.
    pub chunk_size: usize,
    /// The configured overlap.
    pub overlap: usize,
    /// Mean chunk size, integer division.
    pub avg_chunk_size: usize,
    /// Size of the final chunk.
    pub last_chunk_size: usize,
}

/// Chunk `text` using a one-off [`TextChunker`].
///
/// # Errors
///
/// If the chunker cannot be built; see [`TextChunker::new`].
pub fn chunk_text(
    text: &str,
    chunk_size: usize,
    overlap: usize,
    boundary_aware: bool,
    min_chunk_size: usize,
) -> Result<Vec<TextChunk>, ChunkerError> {
    Ok(TextChunker::new(chunk_size, overlap, boundary_aware, min_chunk_size)?.chunk_text(text))
}

/// Map each chunk to an intermediate, then reduce the intermediates.
///
/// Short text bypasses chunking and is passed straight to `map_fn`.
pub fn process_with_map_reduce<T, M, R>(
    text: &str,
    map_fn: M,
    reduce_fn: R,
    max_chunk_size: usize,
) -> Result<T, ChunkerError>
where
    M: Fn(&str) -> T,
    R: Fn(Vec<T>) -> T,
{
    if text.chars().count() <= max_chunk_size {
        return Ok(map_fn(text));
    }
    let chunks = chunk_text(
        text,
        max_chunk_size,
        DEFAULT_CHUNK_OVERLAP,
        true,
        DEFAULT_MIN_CHUNK_SIZE,
    )?;
    let intermediates: Vec<T> = chunks.iter().map(|c| map_fn(&c.content)).collect();
    Ok(reduce_fn(intermediates))
}

/// Process long text while carrying a rolling summary between chunks.
///
/// Each chunk is processed with the previous chunk's summary as context. The
/// rolling summary is truncated to `summary_max_length` to bound growth.
/// Short text bypasses chunking, called with `None` context.
pub fn process_with_rolling_summary<T, F>(
    text: &str,
    process_fn: F,
    max_chunk_size: usize,
    summary_max_length: usize,
) -> Result<Option<T>, ChunkerError>
where
    F: Fn(&str, Option<&str>) -> (T, String),
{
    if text.chars().count() <= max_chunk_size {
        let (result, _) = process_fn(text, None);
        return Ok(Some(result));
    }
    let chunks = chunk_text(
        text,
        max_chunk_size,
        DEFAULT_CHUNK_OVERLAP,
        true,
        DEFAULT_MIN_CHUNK_SIZE,
    )?;
    let mut rolling_summary: Option<String> = None;
    let mut result: Option<T> = None;
    for chunk in &chunks {
        let (r, summary) = process_fn(&chunk.content, rolling_summary.as_deref());
        let mut summary = summary;
        if summary.chars().count() > summary_max_length {
            summary = format!(
                "{}...",
                slice_chars(&summary.chars().collect::<Vec<_>>(), 0, summary_max_length)
            );
        }
        rolling_summary = Some(summary);
        result = Some(r);
    }
    Ok(result)
}

/// Return the best available text from `document` and its source field name.
///
/// Never truncates. Checks `full_text`, `abstract`, `content` and `text` in
/// priority order.
#[must_use]
pub fn get_text_with_priority(
    document: &BTreeMap<String, String>,
    prefer_full_text: bool,
) -> (String, String) {
    let get = |k: &str| document.get(k).cloned().unwrap_or_default();
    let full_text = get("full_text");
    let abstract_ = get("abstract");
    let content = get("content");
    let text_field = get("text");

    if prefer_full_text && !full_text.is_empty() {
        return (full_text, "full_text".to_string());
    }
    if !abstract_.is_empty() {
        return (abstract_, "abstract".to_string());
    }
    if !full_text.is_empty() {
        return (full_text, "full_text".to_string());
    }
    if !content.is_empty() {
        return (content, "content".to_string());
    }
    if !text_field.is_empty() {
        return (text_field, "text".to_string());
    }
    (String::new(), "none".to_string())
}

/// Prefix `text` with `title`, truncating the title at `max_title_length`.
#[must_use]
pub fn combine_title_and_text(title: &str, text: &str, max_title_length: usize) -> String {
    let chars: Vec<char> = title.chars().collect();
    let title = if chars.len() > max_title_length {
        slice_chars(&chars, 0, max_title_length)
    } else {
        title.to_string()
    };

    if !title.is_empty() && !text.is_empty() {
        return format!("Title: {title}\n\n{text}");
    }
    if !title.is_empty() {
        return format!("Title: {title}");
    }
    text.to_string()
}
