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
//! BMLIB_EMBED_MODEL=/path/to/file.gguf cargo test -p bmlib --features embed --test embed_live -- --test-threads=1
//! ```
//!
//! One thread: each test loads its own model, and several at once multiply
//! the memory.

#![cfg(feature = "embed")]

use std::path::Path;

use bmlib::embed::{
    query_text, EmbedError, Embedder, ModelFile, ARCTIC_EMBED_L_V2_F16, ARCTIC_EMBED_L_V2_Q8_0,
    EMBEDDING_DIM, MAX_TOKENS,
};
use serde_json::Value;

const REFERENCE: &str = include_str!("../../../tests/fixtures/embedding_reference.json");

fn model() -> Option<String> {
    std::env::var("BMLIB_EMBED_MODEL").ok()
}

/// The pinned model file `path` is, by name: claiming F16 for any file stored
/// another model's vectors under F16's sha256.
fn pinned(path: &Path) -> ModelFile {
    [ARCTIC_EMBED_L_V2_F16, ARCTIC_EMBED_L_V2_Q8_0]
        .into_iter()
        .find(|m| path.file_name().is_some_and(|n| n == m.file))
        .unwrap_or_else(|| panic!("{} is not a pinned model file", path.display()))
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

/// All 20 items, twice, in one call (past 16 texts, so the length sort and
/// several batches run): each result must still be its own text's.
#[test]
fn batch_keeps_input_order() {
    let Some(mut embedder) = embedder() else {
        return;
    };
    let floor = floor();
    let items = reference();
    let texts: Vec<String> = items
        .iter()
        .map(|(_, kind, text, _)| {
            if kind == "query" {
                query_text(text)
            } else {
                text.clone()
            }
        })
        .collect();
    let twice: Vec<&str> = texts.iter().chain(&texts).map(String::as_str).collect();
    let got = embedder.embed_documents(&twice).unwrap();
    assert_eq!(got.len(), 2 * items.len());
    for (k, vec) in got.iter().enumerate() {
        let (name, _, _, want) = &items[k % items.len()];
        let single = embedder.embed_documents(&[twice[k]]).unwrap().remove(0);
        let same = cosine(vec, &as_f64(&single));
        let cos = cosine(vec, want);
        // Batching moves the numerics a little on the CPU (min 0.99968 seen);
        // another reference text is at most 0.795 away, so a swap is caught.
        assert!(same >= 0.999, "{name}: batch vs single cosine {same:.6}");
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
            assert!(tokens > MAX_TOKENS, "{tokens}");
        }
        other => panic!("expected TooLong, got {other:?}"),
    }
}

/// A text the model's tokenizer counts as exactly `count` tokens. The count
/// is read off `TooLong` for two texts over the limit (tokens = a·words + b),
/// then checked: the `count + 1` text must be refused with that exact count.
fn text_of_tokens(embedder: &mut Embedder, count: usize) -> String {
    let words = |n: usize| vec!["word"; n].join(" ");
    let mut tokens = |n: usize| match embedder.embed_documents(&[&words(n)]) {
        Err(EmbedError::TooLong { tokens, .. }) => tokens,
        other => panic!("expected TooLong, got {other:?}"),
    };
    let (t1, t2) = (tokens(10_000), tokens(10_001));
    let per_word = t2 - t1;
    let fixed = t1 - per_word * 10_000;
    assert_eq!(
        (count - fixed) % per_word,
        0,
        "{count} tokens not reachable"
    );
    words((count - fixed) / per_word)
}

#[test]
fn text_at_max_tokens_is_accepted() {
    let Some(mut embedder) = embedder() else {
        return;
    };
    let exact = text_of_tokens(&mut embedder, MAX_TOKENS);
    let over = text_of_tokens(&mut embedder, MAX_TOKENS + 1);
    // The arithmetic holds at the boundary: one word more is MAX_TOKENS + 1.
    match embedder.embed_documents(&[&over]) {
        Err(EmbedError::TooLong { tokens, .. }) => assert_eq!(tokens, MAX_TOKENS + 1),
        other => panic!("expected TooLong, got {other:?}"),
    }
    assert_eq!(
        embedder.embed_documents(&["short", &exact]).unwrap().len(),
        2
    );
}

#[test]
fn text_one_over_max_tokens_is_an_error() {
    let Some(mut embedder) = embedder() else {
        return;
    };
    let over = text_of_tokens(&mut embedder, MAX_TOKENS + 1);
    match embedder.embed_documents(&["short", &over]) {
        Err(EmbedError::TooLong { index, tokens }) => {
            assert_eq!((index, tokens), (1, MAX_TOKENS + 1));
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
        Err(EmbedError::Load(message)) => assert!(message.contains("nonexistent.gguf")),
        other => panic!("expected Load, got {:?}", other.err()),
    }
}

/// Runs without a model: a file llama.cpp will not load is a `Load` error
/// naming it.
#[test]
fn unloadable_model_file_is_a_load_error() {
    let path = std::env::temp_dir().join(format!("bmlib-not-a-model-{}.gguf", std::process::id()));
    std::fs::write(&path, b"not a GGUF file").unwrap();
    let result = Embedder::load(&path);
    std::fs::remove_file(&path).unwrap();
    match result {
        Err(EmbedError::Load(message)) => assert!(message.contains("bmlib-not-a-model-")),
        other => panic!("expected Load, got {:?}", other.err()),
    }
}

#[test]
fn load_leaves_no_model() {
    let Some(embedder) = embedder() else { return };
    assert!(embedder.model().is_none());
}

#[test]
fn load_model_carries_the_model() {
    let Some(path) = model() else {
        eprintln!("skipped: BMLIB_EMBED_MODEL is not set");
        return;
    };
    let pinned = pinned(Path::new(&path));
    let embedder =
        Embedder::load_model(&pinned, Path::new(&path)).unwrap_or_else(|e| panic!("{e}"));
    assert_eq!(embedder.model(), Some(&pinned));
}

#[test]
fn load_model_refuses_a_missing_file_as_load_does() {
    let error = Embedder::load_model(&ARCTIC_EMBED_L_V2_F16, Path::new("/nonexistent/model.gguf"))
        .expect_err("a missing file must not load");
    assert!(matches!(error, EmbedError::Load(_)), "{error}");
}

// Needs no model: the F16 file loaded as Q8_0 stored F16 vectors under Q8_0's
// sha256, and the size check refuses it before anything is loaded.
#[test]
fn load_model_refuses_a_file_of_another_size() {
    let dir = std::env::temp_dir().join(format!("bmlib-size-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join("model.gguf");
    let file = std::fs::File::create(&path).unwrap();
    file.set_len(ARCTIC_EMBED_L_V2_F16.size).unwrap(); // sparse: no gigabyte is written
    drop(file);
    let error = Embedder::load_model(&ARCTIC_EMBED_L_V2_Q8_0, &path)
        .expect_err("a file of another size must not load");
    std::fs::remove_dir_all(&dir).ok();
    let message = error.to_string();
    assert!(matches!(error, EmbedError::Load(_)), "{message}");
    assert!(
        message.contains(&format!("is {} bytes, not the", ARCTIC_EMBED_L_V2_F16.size)),
        "{message}"
    );
}

#[test]
fn the_store_indexes_and_searches_with_the_real_embedder() {
    use bmlib::embed::store::{ensure_schema, index_paper, search};
    let Some(path) = model() else {
        eprintln!("skipped: BMLIB_EMBED_MODEL is not set");
        return;
    };
    let mut embedder = Embedder::load_model(&pinned(Path::new(&path)), Path::new(&path))
        .unwrap_or_else(|e| panic!("{e}"));
    let mut db = bmlib::db::open_memory().unwrap();
    ensure_schema(&mut db).unwrap();
    let text = bmlib::fulltext::canonical::canonical_from_plain_text(
        "Aspirin reduced strokes in older adults.\n\nThe weather in Paris was mild.",
        "",
    );
    index_paper(&mut db, "k", &text, &mut embedder, None).unwrap();
    let hits = search(
        &mut db,
        "k",
        &text,
        "Does aspirin prevent stroke?",
        &mut embedder,
        2,
    )
    .unwrap();
    assert!(!hits.is_empty());
    assert!(text.span_text(hits[0].span).unwrap().contains("Aspirin"));
}
