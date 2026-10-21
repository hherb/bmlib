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

//! The embedder's pure rules and constants, held to the shared contract.

use bmlib::embed::{
    check_texts, query_text, truncate_and_normalize, ARCTIC_EMBED_L_V2_F16, ARCTIC_EMBED_L_V2_Q8_0,
    EMBEDDING_DIM, MAX_TOKENS, QUERY_PREFIX,
};
use serde_json::Value;

const CONTRACT: &str = include_str!("../../../tests/fixtures/embedding_contract.json");
const MANIFEST: &str = include_str!("../../../tests/fixtures/embedding_model_manifest.json");
const TOLERANCE: f64 = 1e-6;

fn contract() -> Value {
    serde_json::from_str(CONTRACT).unwrap()
}

fn floats(v: &Value) -> Vec<f32> {
    v.as_array()
        .unwrap()
        .iter()
        .map(|x| x.as_f64().unwrap() as f32)
        .collect()
}

#[test]
fn constants_are_the_contracts() {
    assert_eq!(EMBEDDING_DIM, 256);
    assert_eq!(QUERY_PREFIX, "query: ");
    assert_eq!(MAX_TOKENS, 8192);
}

#[test]
fn truncate_and_normalize_cases() {
    let c = contract();
    let cases = c["truncate_and_normalize"].as_array().unwrap();
    assert!(!cases.is_empty());
    for case in cases {
        let name = case["name"].as_str().unwrap();
        let result = truncate_and_normalize(&floats(&case["input"]));
        if case["expected"] == "error" {
            assert!(result.is_err(), "{name}");
            continue;
        }
        let got = result.unwrap_or_else(|e| panic!("{name}: {e}"));
        let want = floats(&case["expected"]);
        assert_eq!(got.len(), want.len(), "{name}");
        for (i, (g, w)) in got.iter().zip(&want).enumerate() {
            assert!(
                (f64::from(*g) - f64::from(*w)).abs() <= TOLERANCE,
                "{name}[{i}]: {g} vs {w}"
            );
        }
    }
}

#[test]
fn query_text_cases() {
    let c = contract();
    let cases = c["query_text"].as_array().unwrap();
    assert!(!cases.is_empty());
    for case in cases {
        let got = query_text(case["input"].as_str().unwrap());
        assert_eq!(got, case["expected"].as_str().unwrap(), "{}", case["name"]);
    }
}

#[test]
fn check_texts_cases() {
    let c = contract();
    let cases = c["check_texts"].as_array().unwrap();
    assert!(!cases.is_empty());
    for case in cases {
        let texts: Vec<&str> = case["texts"]
            .as_array()
            .unwrap()
            .iter()
            .map(|t| t.as_str().unwrap())
            .collect();
        let got = check_texts(&texts).err().map(|e| e.index);
        let want = case["error_index"].as_u64().map(|i| i as usize);
        assert_eq!(got, want, "{}", case["name"]);
    }
}

#[test]
fn model_urls_are_the_contracts() {
    let c = contract();
    for case in c["model_files"].as_array().unwrap() {
        let model = match case["name"].as_str().unwrap() {
            "F16" => ARCTIC_EMBED_L_V2_F16,
            "Q8_0" => ARCTIC_EMBED_L_V2_Q8_0,
            other => panic!("unknown model {other}"),
        };
        assert_eq!(model.url(), case["url"].as_str().unwrap());
    }
}

#[test]
fn model_constants_are_the_manifest() {
    let m: Value = serde_json::from_str(MANIFEST).unwrap();
    let files = m["files"].as_array().unwrap();
    assert_eq!(files.len(), 2);
    for (model, entry) in [ARCTIC_EMBED_L_V2_F16, ARCTIC_EMBED_L_V2_Q8_0]
        .iter()
        .zip(files)
    {
        assert_eq!(model.repo, m["repo"].as_str().unwrap());
        assert_eq!(model.revision, m["revision"].as_str().unwrap());
        assert_eq!(model.file, entry["file"].as_str().unwrap());
        assert_eq!(model.size, entry["size"].as_u64().unwrap());
        assert_eq!(model.sha256, entry["sha256"].as_str().unwrap());
    }
}
