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

use super::vectors::{
    check_texts, query_text, truncate_and_normalize, VectorError, EMBEDDING_DIM, MAX_TOKENS,
};

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
    /// The model output for the text at `index` is not an embedding (NaN,
    /// infinite or zero; see [`VectorError`]).
    Vector {
        /// Position of the text.
        index: usize,
        /// Why its output was refused.
        error: VectorError,
    },
    /// The embedder could not be made: llama.cpp's backend would not start,
    /// the model file is missing or will not load, or the model embeds to
    /// fewer than [`EMBEDDING_DIM`] values.
    Load(String),
    /// llama.cpp failed while embedding: no context, or a batch would not
    /// decode. A context is made per call here; llama-cpp-python makes it with
    /// the model, so Python reports a context failure as `ModelLoadError`.
    Decode(String),
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
            Self::Vector { index, error } => write!(f, "text at index {index}: {error}"),
            Self::Load(message) | Self::Decode(message) => write!(f, "llama.cpp: {message}"),
        }
    }
}

impl std::error::Error for EmbedError {}

fn decode(e: impl fmt::Display) -> EmbedError {
    EmbedError::Decode(e.to_string())
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
        .map_err(|e| EmbedError::Load(e.clone()))
}

/// Embeds documents and queries to [`EMBEDDING_DIM`](super::EMBEDDING_DIM)-value
/// unit vectors.
///
/// Drop it before the process exits. On Metal, llama.cpp aborts at `exit()`
/// while a model is loaded, and that covers `std::process::exit`, a `static`,
/// and a Tauri/tao event loop (which exits without dropping managed state).
/// Keep it in an `Option` (e.g. `Mutex<Option<Embedder>>` in app state) and
/// take it out on `RunEvent::ExitRequested` / `RunEvent::Exit`.
///
/// Flash attention is left to llama.cpp's AUTO policy, which enables it on
/// CPU and Metal after a probe; a backend that fails the probe falls back to
/// reserving the full non-causal attention matrix (about 4.5 GiB at 8192
/// tokens).
#[derive(Debug)]
pub struct Embedder {
    backend: &'static LlamaBackend,
    model: LlamaModel,
}

// Tauri's managed state (`Mutex<Option<Embedder>>`) needs `Embedder: Send`.
const _: () = {
    const fn assert_send<T: Send>() {}
    assert_send::<Embedder>();
};

impl Embedder {
    /// Load the GGUF model, with every layer on the GPU when llama.cpp was
    /// built with a GPU backend (Metal on Apple platforms, with or without
    /// `embed-metal`).
    ///
    /// # Errors
    ///
    /// [`EmbedError::Load`] if the file is missing, llama.cpp's backend will
    /// not start or cannot load the file, or the model embeds to fewer than
    /// [`EMBEDDING_DIM`] values.
    pub fn load(model_path: &Path) -> Result<Self, EmbedError> {
        // llama-cpp-2 only debug-asserts the path, so a missing file would panic.
        if !model_path.is_file() {
            return Err(EmbedError::Load(format!(
                "model file not found: {}",
                model_path.display()
            )));
        }
        let backend = backend()?;
        let params = LlamaModelParams::default().with_n_gpu_layers(ALL_LAYERS_ON_GPU);
        let model = LlamaModel::load_from_file(backend, model_path, &params)
            .map_err(|e| EmbedError::Load(format!("cannot load {}: {e}", model_path.display())))?;
        refuse_narrow(model.n_embd_out())?;
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
        self.model.new_context(self.backend, params).map_err(decode)
    }

    /// Embed texts as documents (no prefix), in input order.
    ///
    /// # Errors
    ///
    /// [`EmbedError::EmptyText`] or [`EmbedError::TooLong`] for the first
    /// offending text, before anything is embedded; [`EmbedError::Vector`]
    /// for the first text whose model output is not an embedding;
    /// [`EmbedError::Decode`] if llama.cpp fails.
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
        let lens: Vec<usize> = ids.iter().map(Vec::len).collect();
        refuse_too_long(&lens)?;
        let mut ctx = self.context()?;
        let mut order: Vec<usize> = (0..ids.len()).collect();
        order.sort_by_key(|&i| std::cmp::Reverse(lens[i]));
        let mut out = vec![Ok(Vec::new()); ids.len()];
        let mut batch = LlamaBatch::new(MAX_TOKENS, MAX_SEQUENCES as i32);
        for group in pack(&order, &lens) {
            batch.clear();
            for (seq, &i) in (0..).zip(&group) {
                // Every token an output, as pooled embeddings need: otherwise
                // llama.cpp overrides the flags and logs a WARN on each decode.
                batch.add_sequence(&ids[i], seq, true).map_err(decode)?;
            }
            ctx.clear_kv_cache();
            ctx.decode(&mut batch).map_err(decode)?;
            for (seq, &i) in (0..).zip(&group) {
                out[i] = truncate_and_normalize(ctx.embeddings_seq_ith(seq).map_err(decode)?);
            }
        }
        embeddings(out)
    }

    /// Embed a search query, with the model's query prefix.
    ///
    /// # Errors
    ///
    /// As [`embed_documents`](Self::embed_documents); an empty query is
    /// [`EmbedError::EmptyText`] at index 0.
    pub fn embed_query(&mut self, query: &str) -> Result<Vec<f32>, EmbedError> {
        Ok(self.embed_documents(&[&query_document(query)?])?.remove(0))
    }
}

/// The document text for `query`, refusing an empty one, which the prefix
/// would otherwise make look valid.
fn query_document(query: &str) -> Result<String, EmbedError> {
    check_texts(&[query]).map_err(|e| EmbedError::EmptyText { index: e.index })?;
    Ok(query_text(query))
}

/// Every text's embedding, in input order, or the first text whose model
/// output was refused (texts are decoded longest first, so not in that order).
fn embeddings(results: Vec<Result<Vec<f32>, VectorError>>) -> Result<Vec<Vec<f32>>, EmbedError> {
    results
        .into_iter()
        .enumerate()
        .map(|(index, r)| r.map_err(|error| EmbedError::Vector { index, error }))
        .collect()
}

/// Refuse a model whose output is narrower than [`EMBEDDING_DIM`]: the width is
/// the model's, so it would fail every text alike.
fn refuse_narrow(width: i32) -> Result<(), EmbedError> {
    if usize::try_from(width).unwrap_or(0) < EMBEDDING_DIM {
        return Err(EmbedError::Load(format!(
            "the model embeds to {width} values, need at least {EMBEDDING_DIM}"
        )));
    }
    Ok(())
}

/// Refuse the first text of more than [`MAX_TOKENS`] tokens; `lens[i]` is text
/// `i`'s token count.
fn refuse_too_long(lens: &[usize]) -> Result<(), EmbedError> {
    match lens.iter().position(|&n| n > MAX_TOKENS) {
        Some(index) => Err(EmbedError::TooLong {
            index,
            tokens: lens[index],
        }),
        None => Ok(()),
    }
}

/// Greedy packing of the length-sorted `order` into batches of at most
/// [`MAX_TOKENS`] tokens and [`MAX_SEQUENCES`] texts; `lens[i]` is text `i`'s
/// token count.
fn pack(order: &[usize], lens: &[usize]) -> Vec<Vec<usize>> {
    let mut batches = Vec::new();
    let mut cur = Vec::new();
    let mut used = 0;
    for &i in order {
        let len = lens[i];
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

#[cfg(test)]
mod tests {
    use super::{
        decode, embeddings, pack, query_document, refuse_narrow, refuse_too_long,
        truncate_and_normalize, EmbedError, VectorError, EMBEDDING_DIM, MAX_SEQUENCES, MAX_TOKENS,
    };

    #[test]
    fn an_unusable_output_names_the_first_such_text() {
        for bad in [f32::NAN, f32::INFINITY, 0.0] {
            let results = vec![
                truncate_and_normalize(&[1.0; EMBEDDING_DIM]),
                truncate_and_normalize(&[bad; EMBEDDING_DIM]),
                truncate_and_normalize(&[bad; 1]),
            ];
            assert_eq!(
                embeddings(results),
                Err(EmbedError::Vector {
                    index: 1,
                    error: VectorError::NotScalable
                }),
                "{bad}"
            );
        }
    }

    #[test]
    fn embeddings_keep_input_order() {
        let mut second = [0.0; EMBEDDING_DIM];
        second[1] = 2.0;
        let got = embeddings(vec![
            truncate_and_normalize(&[3.0; EMBEDDING_DIM]),
            truncate_and_normalize(&second),
        ])
        .unwrap();
        assert_eq!(got.len(), 2);
        assert_eq!((got[0][0], got[1][0], got[1][1]), (0.0625, 0.0, 1.0));
    }

    #[test]
    fn an_embedding_failure_is_a_decode_error() {
        assert_eq!(decode("no batch"), EmbedError::Decode("no batch".into()));
    }

    #[test]
    fn a_model_narrower_than_the_embedding_is_refused() {
        assert!(refuse_narrow(EMBEDDING_DIM as i32).is_ok());
        for width in [EMBEDDING_DIM as i32 - 1, 0, -1] {
            assert!(
                matches!(refuse_narrow(width), Err(EmbedError::Load(m)) if m.contains("embeds to")),
                "{width}"
            );
        }
    }

    #[test]
    fn max_tokens_is_accepted_and_one_more_is_refused() {
        assert_eq!(refuse_too_long(&[MAX_TOKENS, 1]), Ok(()));
        assert_eq!(
            refuse_too_long(&[5, MAX_TOKENS + 1, MAX_TOKENS + 9]),
            Err(EmbedError::TooLong {
                index: 1,
                tokens: MAX_TOKENS + 1
            })
        );
    }

    #[test]
    fn a_query_is_prefixed_and_an_empty_one_refused() {
        assert_eq!(query_document("aspirin").unwrap(), "query: aspirin");
        assert_eq!(
            query_document(" \u{1c} "),
            Err(EmbedError::EmptyText { index: 0 })
        );
    }

    /// Packs `lens` as `embed_documents` does and checks every group.
    fn packed(lens: &[usize]) -> Vec<Vec<usize>> {
        let mut order: Vec<usize> = (0..lens.len()).collect();
        order.sort_by_key(|&i| std::cmp::Reverse(lens[i]));
        let groups = pack(&order, lens);
        for g in &groups {
            assert!(!g.is_empty() && g.len() <= MAX_SEQUENCES, "{g:?}");
            assert!(
                g.iter().map(|&i| lens[i]).sum::<usize>() <= MAX_TOKENS,
                "{g:?}"
            );
        }
        // Each index once, in `order`'s sequence: results map back by index.
        assert_eq!(groups.concat(), order);
        groups
    }

    #[test]
    fn pack_caps_texts_per_group() {
        assert_eq!(packed(&[3; 40]).len(), 3); // 16 + 16 + 8
    }

    #[test]
    fn pack_caps_tokens_per_group() {
        assert_eq!(packed(&[3000; 5]).len(), 3); // 2 + 2 + 1
    }

    #[test]
    fn pack_mixed_lengths_past_both_caps() {
        let lens: Vec<usize> = (0..37)
            .map(|i| [MAX_TOKENS, 5000, 900, 40, 7][i % 5])
            .collect();
        assert!(lens.iter().sum::<usize>() > MAX_TOKENS && lens.len() > MAX_SEQUENCES);
        assert!(packed(&lens).len() > 2);
    }
}
