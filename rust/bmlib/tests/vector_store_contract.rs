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

//! The vector store's pure half, held to the shared
//! `tests/fixtures/vector_store.json` that Python asserts too
//! (`tests/test_vector_store_contract.py`).

use bmlib::embed::store::{
    decode_vector, encode_vector, rank, ChunkSetLabel, LabelField, StoreError, INDEX_BATCH_SIZE,
    NORM_TOLERANCE,
};
use bmlib::embed::{EMBEDDING_DIM, EMBEDDING_RECIPE_VERSION};
use serde_json::Value;

const CONTRACT: &str = include_str!("../../../tests/fixtures/vector_store.json");

fn contract() -> Value {
    serde_json::from_str(CONTRACT).expect("the fixture is JSON")
}

/// A binary64 from the 16 hex digits of its bits (never a decimal).
fn f64_of(hex: &Value) -> f64 {
    f64::from_bits(u64::from_str_radix(hex.as_str().unwrap(), 16).unwrap())
}

fn bytes_of(hex: &Value) -> Vec<u8> {
    let s = hex.as_str().unwrap();
    (0..s.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&s[i..i + 2], 16).unwrap())
        .collect()
}

fn hex_of(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// Values as the store receives them: binary32, rounded to nearest as
/// Python's `struct.pack("<f")` rounds.
#[allow(clippy::cast_possible_truncation)]
fn f32s_of(values: &Value) -> Vec<f32> {
    values
        .as_array()
        .unwrap()
        .iter()
        .map(|v| f64_of(v) as f32)
        .collect()
}

fn cases(section: &str) -> Vec<Value> {
    contract()[section].as_array().unwrap().clone()
}

#[test]
fn every_section_is_read() {
    let c = contract();
    let mut keys: Vec<&str> = c.as_object().unwrap().keys().map(String::as_str).collect();
    keys.sort_unstable();
    assert_eq!(
        keys,
        [
            "_readme",
            "decode",
            "embedding_dim",
            "encode",
            "index_batch_size",
            "label_fields",
            "labels",
            "norm_tolerance",
            "rank",
            "recipe_version"
        ]
    );
}

#[test]
fn the_constants_are_the_contracts() {
    let c = contract();
    assert_eq!(c["embedding_dim"].as_u64(), Some(EMBEDDING_DIM as u64));
    assert_eq!(
        c["recipe_version"].as_u64(),
        Some(u64::from(EMBEDDING_RECIPE_VERSION))
    );
    assert_eq!(
        f64_of(&c["norm_tolerance"]).to_bits(),
        NORM_TOLERANCE.to_bits()
    );
    assert_eq!(
        c["index_batch_size"].as_u64(),
        Some(INDEX_BATCH_SIZE as u64)
    );
    let names: Vec<&str> = LabelField::ALL.iter().map(|f| f.as_str()).collect();
    let wire: Vec<&str> = c["label_fields"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap())
        .collect();
    assert_eq!(names, wire);
}

#[test]
fn encode() {
    for case in cases("encode") {
        let name = case["name"].as_str().unwrap();
        let result = encode_vector(&f32s_of(&case["values"]));
        if case.get("refused").is_some() {
            assert!(
                matches!(result, Err(StoreError::Vector(_))),
                "{name}: {result:?}"
            );
        } else {
            let blob = result.unwrap_or_else(|e| panic!("{name}: {e}"));
            assert_eq!(hex_of(&blob), case["blob"].as_str().unwrap(), "{name}");
        }
    }
}

#[test]
fn decode() {
    for case in cases("decode") {
        let name = case["name"].as_str().unwrap();
        let result = decode_vector(&bytes_of(&case["blob"]));
        if case.get("refused").is_some() {
            assert!(
                matches!(result, Err(StoreError::Corrupt(_))),
                "{name}: {result:?}"
            );
        } else {
            let values = result.unwrap_or_else(|e| panic!("{name}: {e}"));
            let bits: Vec<u64> = values.iter().map(|&v| f64::from(v).to_bits()).collect();
            let expected: Vec<u64> = case["values"]
                .as_array()
                .unwrap()
                .iter()
                .map(|v| f64_of(v).to_bits())
                .collect();
            assert_eq!(bits, expected, "{name}");
        }
    }
}

#[test]
fn rank_matches_python_bit_for_bit() {
    for case in cases("rank") {
        let name = case["name"].as_str().unwrap();
        let vectors: Vec<Vec<f32>> = case["vectors"]
            .as_array()
            .unwrap()
            .iter()
            .map(|b| decode_vector(&bytes_of(b)).unwrap())
            .collect();
        let k = usize::try_from(case["k"].as_u64().unwrap()).unwrap();
        let hits = rank(&f32s_of(&case["query"]), &vectors, k).unwrap();
        let got: Vec<(usize, u64)> = hits.iter().map(|&(i, s)| (i, s.to_bits())).collect();
        let expected: Vec<(usize, u64)> = case["expected"]
            .as_array()
            .unwrap()
            .iter()
            .map(|e| {
                let index = usize::try_from(e["chunk_index"].as_u64().unwrap()).unwrap();
                (index, f64_of(&e["score"]).to_bits())
            })
            .collect();
        assert_eq!(got, expected, "{name}");
    }
}

fn label(v: &Value) -> ChunkSetLabel {
    let version = |name: &str| u32::try_from(v[name].as_u64().unwrap()).unwrap();
    ChunkSetLabel {
        model_sha256: v["model_sha256"].as_str().unwrap().to_string(),
        recipe_version: version("recipe_version"),
        chunker_version: version("chunker_version"),
        converter_version: v["converter_version"].as_str().unwrap().to_string(),
        text_sha256: v["text_sha256"].as_str().unwrap().to_string(),
    }
}

#[test]
fn labels() {
    for case in cases("labels") {
        let name = case["name"].as_str().unwrap();
        let differs: Vec<&str> = label(&case["stored"])
            .differences(&label(&case["current"]))
            .iter()
            .map(|f| f.as_str())
            .collect();
        let expected: Vec<&str> = case["differs"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_str().unwrap())
            .collect();
        assert_eq!(differs, expected, "{name}");
    }
}

#[test]
fn a_zero_k_is_refused() {
    assert!(matches!(
        rank(&[], &[], 0),
        Err(StoreError::InvalidArgument(_))
    ));
}
