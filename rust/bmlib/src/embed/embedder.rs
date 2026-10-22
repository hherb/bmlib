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

//! In-process embedder over llama-cpp-2.
//!
//! A port of `bmlib/embed/embedder.py`. Nothing is truncated silently: a text
//! longer than [`MAX_TOKENS`] or an empty one is an [`EmbedError`] naming it.

use std::fmt;
use std::num::NonZeroU32;
use std::path::Path;
use std::sync::OnceLock;

use llama_cpp_2::context::params::{LlamaContextParams, LlamaPoolingType};
use llama_cpp_2::context::LlamaContext;
use llama_cpp_2::llama_backend::LlamaBackend;
use llama_cpp_2::llama_batch::LlamaBatch;
use llama_cpp_2::model::params::LlamaModelParams;
use llama_cpp_2::model::LlamaModel;
use llama_cpp_2::token::LlamaToken;
use llama_cpp_2::{send_logs_to_tracing, LogOptions};

use super::vectors::{check_texts, query_text, truncate_and_normalize, MAX_TOKENS};

/// Layers offloaded to the GPU: more than the model has, so all of them. A
/// build without a GPU backend ignores it.
const ALL_LAYERS_ON_GPU: u32 = 999;
/// Most texts packed into one batch (llama.cpp's `n_seq_max`).
const MAX_SEQUENCES: usize = 16;

/// An embedding request that cannot be honoured.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum EmbedError {
    /// The text at `index` is empty or whitespace-only.
    EmptyText {
        /// Position of the first such text.
        index: usize,
    },
    /// The text at `index` has more than [`MAX_TOKENS`] tokens. It is refused,
    /// never cut short.
    TooLong {
        /// Position of the first such text.
        index: usize,
        /// How many tokens it has.
        tokens: usize,
    },
    /// llama.cpp failed: the model would not load, or a batch would not decode.
    Llama(String),
}

impl fmt::Display for EmbedError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::EmptyText { index } => {
                write!(f, "text at index {index} is empty or whitespace-only")
            }
            Self::TooLong { index, tokens } => write!(
                f,
                "text at index {index} has {tokens} tokens, over the {MAX_TOKENS} limit"
            ),
            Self::Llama(message) => write!(f, "llama.cpp: {message}"),
        }
    }
}

impl std::error::Error for EmbedError {}

fn llama(e: impl fmt::Display) -> EmbedError {
    EmbedError::Llama(e.to_string())
}

/// llama.cpp's backend, initialised once per process: a second
/// `LlamaBackend::init` fails while the first is alive, so two embedders share it.
fn backend() -> Result<&'static LlamaBackend, EmbedError> {
    static BACKEND: OnceLock<Result<LlamaBackend, String>> = OnceLock::new();
    BACKEND
        .get_or_init(|| {
            // Off stderr, as Python's verbose=False; a `tracing` subscriber still sees them.
            send_logs_to_tracing(LogOptions::default());
            LlamaBackend::init().map_err(|e| e.to_string())
        })
        .as_ref()
        .map_err(|e| EmbedError::Llama(e.clone()))
}

/// Embeds documents and queries to [`EMBEDDING_DIM`](super::EMBEDDING_DIM)-value
/// unit vectors.
///
/// Drop it before the process exits rather than keeping it in a `static`: on
/// Metal, llama.cpp aborts at exit while a model is still loaded.
#[derive(Debug)]
pub struct Embedder {
    backend: &'static LlamaBackend,
    model: LlamaModel,
}

impl Embedder {
    /// Load the GGUF model, with every layer on the GPU when llama.cpp was
    /// built with a GPU backend (Metal on Apple platforms, with or without
    /// `embed-metal`).
    ///
    /// # Errors
    ///
    /// [`EmbedError::Llama`] if the file is missing or llama.cpp cannot load it.
    pub fn load(model_path: &Path) -> Result<Self, EmbedError> {
        // llama-cpp-2 only debug-asserts the path, so a missing file would panic.
        if !model_path.is_file() {
            return Err(EmbedError::Llama(format!(
                "model file not found: {}",
                model_path.display()
            )));
        }
        let backend = backend()?;
        let params = LlamaModelParams::default().with_n_gpu_layers(ALL_LAYERS_ON_GPU);
        let model = LlamaModel::load_from_file(backend, model_path, &params)
            .map_err(|e| EmbedError::Llama(format!("cannot load {}: {e}", model_path.display())))?;
        Ok(Self { backend, model })
    }

    // shortcut: a fresh context per call (~15 ms on Metal) because LlamaContext
    // borrows the model; hold one (e.g. self_cell) if query latency matters.
    // `&mut self` on the callers keeps two contexts from being built at once,
    // which llama-cpp-2 documents as unsound.
    fn context(&self) -> Result<LlamaContext<'_>, EmbedError> {
        let tokens = MAX_TOKENS as u32;
        let params = LlamaContextParams::default()
            .with_n_ctx(NonZeroU32::new(tokens))
            .with_n_batch(tokens)
            .with_n_ubatch(tokens)
            .with_n_seq_max(MAX_SEQUENCES as u32)
            .with_embeddings(true)
            .with_pooling_type(LlamaPoolingType::Cls);
        self.model.new_context(self.backend, params).map_err(llama)
    }

    /// Embed texts as documents (no prefix), in input order.
    ///
    /// # Errors
    ///
    /// [`EmbedError::EmptyText`] or [`EmbedError::TooLong`] for the first
    /// offending text, before anything is embedded; [`EmbedError::Llama`] if
    /// llama.cpp fails.
    pub fn embed_documents(&mut self, texts: &[&str]) -> Result<Vec<Vec<f32>>, EmbedError> {
        check_texts(texts).map_err(|e| EmbedError::EmptyText { index: e.index })?;
        if texts.is_empty() {
            return Ok(Vec::new());
        }
        let vocab = self.model.vocab();
        let ids: Vec<Vec<LlamaToken>> = texts
            .iter()
            .map(|t| vocab.tokenize(t.as_bytes(), true, false))
            .collect();
        if let Some((index, t)) = ids.iter().enumerate().find(|(_, t)| t.len() > MAX_TOKENS) {
            return Err(EmbedError::TooLong {
                index,
                tokens: t.len(),
            });
        }
        let mut ctx = self.context()?;
        let mut order: Vec<usize> = (0..ids.len()).collect();
        order.sort_by_key(|&i| std::cmp::Reverse(ids[i].len()));
        let mut out = vec![Vec::new(); ids.len()];
        let mut batch = LlamaBatch::new(MAX_TOKENS, MAX_SEQUENCES as i32);
        for group in pack(&order, &ids) {
            batch.clear();
            for (seq, &i) in (0..).zip(&group) {
                batch.add_sequence(&ids[i], seq, false).map_err(llama)?;
            }
            ctx.clear_kv_cache();
            ctx.decode(&mut batch).map_err(llama)?;
            for (seq, &i) in (0..).zip(&group) {
                let raw = ctx.embeddings_seq_ith(seq).map_err(llama)?;
                out[i] = truncate_and_normalize(raw).map_err(llama)?;
            }
        }
        Ok(out)
    }

    /// Embed a search query, with the model's query prefix.
    ///
    /// # Errors
    ///
    /// As [`embed_documents`](Self::embed_documents); an empty query is
    /// [`EmbedError::EmptyText`] at index 0.
    pub fn embed_query(&mut self, query: &str) -> Result<Vec<f32>, EmbedError> {
        // the prefix would otherwise make an empty query look valid
        check_texts(&[query]).map_err(|e| EmbedError::EmptyText { index: e.index })?;
        Ok(self.embed_documents(&[&query_text(query)])?.remove(0))
    }
}

/// Greedy packing of the length-sorted `order` into batches of at most
/// [`MAX_TOKENS`] tokens and [`MAX_SEQUENCES`] texts.
fn pack(order: &[usize], ids: &[Vec<LlamaToken>]) -> Vec<Vec<usize>> {
    let mut batches = Vec::new();
    let mut cur = Vec::new();
    let mut used = 0;
    for &i in order {
        let len = ids[i].len();
        if !cur.is_empty() && (used + len > MAX_TOKENS || cur.len() == MAX_SEQUENCES) {
            batches.push(std::mem::take(&mut cur));
            used = 0;
        }
        cur.push(i);
        used += len;
    }
    if !cur.is_empty() {
        batches.push(cur);
    }
    batches
}
