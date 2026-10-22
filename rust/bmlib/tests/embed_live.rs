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

//! The [`Embedder`] against the reference vectors.
//!
//! A port of `tests/test_embed_live.py`, with the same names and tolerances.
//! Each live test returns immediately unless `BMLIB_EMBED_MODEL` names a GGUF
//! file, so CI runs them without a model. Run them with:
//!
//! ```text
//! BMLIB_EMBED_MODEL=/path/to/file.gguf cargo test -p bmlib --features embed --test embed_live
//! ```

#![cfg(feature = "embed")]

use std::path::Path;

use bmlib::embed::{EmbedError, Embedder, EMBEDDING_DIM};
use serde_json::Value;

const REFERENCE: &str = include_str!("../../../tests/fixtures/embedding_reference.json");

fn model() -> Option<String> {
    std::env::var("BMLIB_EMBED_MODEL").ok()
}

/// The model, loaded afresh for each test. Not shared through a static: a
/// static is never dropped, and on Metal llama.cpp aborts at exit while a
/// model is still loaded.
fn embedder() -> Option<Embedder> {
    let Some(path) = model() else {
        eprintln!("skipped: BMLIB_EMBED_MODEL is not set");
        return None;
    };
    Some(Embedder::load(Path::new(&path)).unwrap_or_else(|e| panic!("{e}")))
}

fn cosine(a: &[f32], b: &[f64]) -> f64 {
    assert_eq!(a.len(), b.len());
    let dot: f64 = a.iter().zip(b).map(|(&x, &y)| f64::from(x) * y).sum();
    let na = a.iter().map(|&x| f64::from(x).powi(2)).sum::<f64>().sqrt();
    let nb = b.iter().map(|&y| y * y).sum::<f64>().sqrt();
    dot / (na * nb)
}

fn as_f64(v: &[f32]) -> Vec<f64> {
    v.iter().map(|&x| f64::from(x)).collect()
}

/// The cosine floor for the file under test, by its name.
fn floor() -> f64 {
    if model().unwrap().contains("Q8_0") {
        0.998
    } else {
        0.999
    }
}

/// The reference items: (name, kind, text, vector).
fn reference() -> Vec<(String, String, String, Vec<f64>)> {
    let reference: Value = serde_json::from_str(REFERENCE).unwrap();
    let items = reference["items"].as_array().unwrap();
    assert!(!items.is_empty());
    items
        .iter()
        .map(|item| {
            let field = |k: &str| item[k].as_str().unwrap().to_string();
            let vector = item["vector"]
                .as_array()
                .unwrap()
                .iter()
                .map(|x| x.as_f64().unwrap())
                .collect();
            (field("name"), field("kind"), field("text"), vector)
        })
        .collect()
}

#[test]
fn reference_vectors_within_tolerance() {
    let Some(mut embedder) = embedder() else {
        return;
    };
    let floor = floor();
    for (name, kind, text, want) in reference() {
        let got = if kind == "query" {
            embedder.embed_query(&text).unwrap()
        } else {
            embedder.embed_documents(&[&text]).unwrap().remove(0)
        };
        let cos = cosine(&got, &want);
        println!("cosine {name}: {cos:.6}");
        assert!(cos >= floor, "{name}: cosine {cos:.6} < {floor}");
    }
}

/// Rust only: the documents of mixed lengths in one call, so the length sort
/// and batch packing run, and each result must still be its own text's.
#[test]
fn batch_keeps_input_order() {
    let Some(mut embedder) = embedder() else {
        return;
    };
    let floor = floor();
    let docs: Vec<_> = reference()
        .into_iter()
        .filter(|(_, kind, _, _)| kind == "document")
        .collect();
    let texts: Vec<&str> = docs.iter().map(|(_, _, text, _)| text.as_str()).collect();
    let got = embedder.embed_documents(&texts).unwrap();
    assert_eq!(got.len(), docs.len());
    for ((name, _, _, want), vec) in docs.iter().zip(&got) {
        let cos = cosine(vec, want);
        assert!(cos >= floor, "{name}: cosine {cos:.6} < {floor}");
    }
    assert!(embedder.embed_documents(&[]).unwrap().is_empty());
}

#[test]
fn query_is_prefixed_document() {
    let Some(mut embedder) = embedder() else {
        return;
    };
    let query = embedder.embed_query("what treats malaria").unwrap();
    let prefixed = embedder
        .embed_documents(&["query: what treats malaria"])
        .unwrap()
        .remove(0);
    assert!(
        cosine(&query, &as_f64(&prefixed)) > 0.9999,
        "query is not the prefixed document"
    );
}

#[test]
fn overlong_text_is_an_error() {
    let Some(mut embedder) = embedder() else {
        return;
    };
    let long_text = "word ".repeat(40_000);
    match embedder.embed_documents(&["short", &long_text]) {
        Err(EmbedError::TooLong { index, tokens }) => {
            assert_eq!(index, 1);
            assert!(tokens > bmlib::embed::MAX_TOKENS, "{tokens}");
        }
        other => panic!("expected TooLong, got {other:?}"),
    }
}

#[test]
fn empty_text_is_an_error() {
    let Some(mut embedder) = embedder() else {
        return;
    };
    match embedder.embed_documents(&["fine", "  "]) {
        Err(EmbedError::EmptyText { index }) => assert_eq!(index, 1),
        other => panic!("expected EmptyText, got {other:?}"),
    }
}

#[test]
fn empty_query_is_an_error() {
    let Some(mut embedder) = embedder() else {
        return;
    };
    match embedder.embed_query("   ") {
        Err(EmbedError::EmptyText { index }) => assert_eq!(index, 0),
        other => panic!("expected EmptyText, got {other:?}"),
    }
}

#[test]
fn vectors_are_unit_length() {
    let Some(mut embedder) = embedder() else {
        return;
    };
    let vectors = embedder
        .embed_documents(&["aspirin", "insulin resistance"])
        .unwrap();
    assert_eq!(vectors.len(), 2);
    for (i, vec) in vectors.iter().enumerate() {
        assert_eq!(vec.len(), EMBEDDING_DIM, "item {i}");
        let norm = vec
            .iter()
            .map(|&x| f64::from(x).powi(2))
            .sum::<f64>()
            .sqrt();
        assert!((norm - 1.0).abs() <= 1e-6, "item {i}: norm {norm}");
    }
}

/// Runs without a model: a missing file is an error, never a panic.
#[test]
fn missing_model_file_is_an_error() {
    match Embedder::load(Path::new("nonexistent.gguf")) {
        Err(EmbedError::Llama(message)) => assert!(message.contains("nonexistent.gguf")),
        other => panic!("expected Llama, got {:?}", other.err()),
    }
}
