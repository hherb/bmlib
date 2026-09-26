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

//! Publication storage rules — the oracle and the named tests.
//!
//! The rules are diffed against Python (38 cases) because each one decides
//! which papers exist: a DOI that does not canonicalise splits one work into
//! two rows, and a source list that replaces instead of uniting loses
//! provenance on every sync.

use bmlib::publications::storage::{
    group_by_source, merge_json_list, merge_sources, normalize_doi, normalize_pmid, DOI_PREFIXES,
};
use serde_json::Value;

const CASES: &str = include_str!("data/storage_cases.json");
const EXPECTED: &str = include_str!("data/storage_expected.json");

fn run(case: &Value) -> Value {
    let fn_name = case["fn"].as_str().unwrap_or_default();
    let args = &case["args"];
    let opt_str = |key: &str| args.get(key).and_then(Value::as_str);

    match fn_name {
        "prefixes" => serde_json::json!(DOI_PREFIXES),
        "normalize_doi" => serde_json::json!(normalize_doi(opt_str("value"))),
        "normalize_pmid" => serde_json::json!(normalize_pmid(opt_str("value"))),
        "merge_sources" => {
            let existing: Vec<String> = args["existing"]
                .as_array()
                .map(|a| {
                    a.iter()
                        .filter_map(Value::as_str)
                        .map(str::to_string)
                        .collect()
                })
                .unwrap_or_default();
            let incoming: Vec<String> = args["incoming"]
                .as_array()
                .map(|a| {
                    a.iter()
                        .filter_map(Value::as_str)
                        .map(str::to_string)
                        .collect()
                })
                .unwrap_or_default();
            serde_json::json!(merge_sources(&existing, &incoming))
        }
        "merge_json_list" => {
            let incoming: Vec<String> = args["incoming"]
                .as_array()
                .map(|a| {
                    a.iter()
                        .filter_map(Value::as_str)
                        .map(str::to_string)
                        .collect()
                })
                .unwrap_or_default();
            serde_json::json!(merge_json_list(opt_str("existing"), &incoming))
        }
        other => panic!("unknown fn {other:?}"),
    }
}

#[test]
fn the_rules_agree_with_python_on_every_case() {
    let cases: Value = serde_json::from_str(CASES).expect("cases parse");
    let expected: Value = serde_json::from_str(EXPECTED).expect("expected parse");
    let cases = cases.as_array().expect("cases is a list");
    let expected = expected.as_array().expect("expected is a list");
    assert_eq!(cases.len(), expected.len(), "regenerate the expectations");

    let mut failures: Vec<String> = Vec::new();
    for (case, want) in cases.iter().zip(expected.iter()) {
        let name = case["name"].as_str().unwrap_or_default();
        assert_eq!(name, want["name"].as_str().unwrap_or_default());
        assert!(
            want["ok"].as_bool().unwrap_or(false),
            "{name}: {}",
            want["error"]
        );
        let got = run(case);
        if got != want["value"] {
            failures.push(format!(
                "  {name}\n    python: {}\n    rust:   {}",
                serde_json::to_string(&want["value"]).unwrap_or_default(),
                serde_json::to_string(&got).unwrap_or_default()
            ));
        }
    }
    assert!(
        failures.is_empty(),
        "{} of {} diverge:\n{}",
        failures.len(),
        cases.len(),
        failures.join("\n")
    );
}

// ---------------------------------------------------------------------------
// DOI canonicalisation
// ---------------------------------------------------------------------------

/// Case and prefix are both canonicalised, and that is the whole point: PubMed
/// preserves the registered mixed case while OpenAlex lower-cases everything,
/// so without this the same paper dedups to two rows.
#[test]
fn a_doi_canonicalises_case_and_prefix_together() {
    let want = Some("10.1000/xyz".to_string());
    for input in [
        "10.1000/XYZ",
        "https://doi.org/10.1000/XYZ",
        "http://doi.org/10.1000/XYZ",
        "https://dx.doi.org/10.1000/XYZ",
        "http://dx.doi.org/10.1000/XYZ",
        "doi:10.1000/XYZ",
        "HTTPS://DOI.ORG/10.1000/XYZ",
        "  10.1000/XYZ  ",
    ] {
        assert_eq!(normalize_doi(Some(input)), want, "{input:?}");
    }
}

/// The prefix is stripped **once**, matching Python's `break`. Stripping
/// repeatedly would turn `"doi:https://doi.org/10.1000/x"` into `"10.1000/x"`,
/// which is a different canonical form from what a single-prefix source
/// produces — so the two would dedup apart.
#[test]
fn only_one_prefix_is_stripped() {
    assert_eq!(
        normalize_doi(Some("doi:https://doi.org/10.1000/XYZ")),
        Some("https://doi.org/10.1000/xyz".to_string())
    );
}

/// A value that is only a prefix canonicalises to nothing, not to an empty
/// string: the column is nullable and an empty string would be a third state.
#[test]
fn an_empty_result_is_none_not_an_empty_string() {
    assert_eq!(normalize_doi(Some("")), None);
    assert_eq!(normalize_doi(Some("   ")), None);
    assert_eq!(normalize_doi(Some("https://doi.org/")), None);
    assert_eq!(normalize_doi(None), None);
    assert_eq!(normalize_pmid(Some("")), None);
    assert_eq!(normalize_pmid(Some("  ")), None);
    assert_eq!(normalize_pmid(None), None);
}

/// Non-ASCII lowercasing is Unicode-aware, as Python's `str.lower()` is. A
/// byte-wise ASCII fold would leave these unchanged.
#[test]
fn lowercasing_is_unicode_aware() {
    assert_eq!(
        normalize_doi(Some("10.1000/ÜNÏCÖDÉ")),
        Some("10.1000/ünïcödé".to_string())
    );
}

// ---------------------------------------------------------------------------
// Source union
// ---------------------------------------------------------------------------

/// Provenance accumulates and never shrinks. A replace-instead-of-union would
/// drop a source that had already been recorded, so the record's history
/// depends on sync order.
#[test]
fn sources_union_and_keep_their_order() {
    assert_eq!(
        merge_sources(&["pubmed".into()], &["biorxiv".into()]),
        vec!["pubmed".to_string(), "biorxiv".to_string()]
    );
    assert_eq!(
        merge_sources(&["z".into(), "a".into()], &["m".into()]),
        vec!["z".to_string(), "a".to_string(), "m".to_string()],
        "existing order is preserved"
    );
    assert_eq!(
        merge_sources(&["a".into(), "b".into()], &["b".into(), "c".into()]),
        vec!["a".to_string(), "b".to_string(), "c".to_string()],
        "a duplicate is not appended twice"
    );
}

// ---------------------------------------------------------------------------
// Fill, never overwrite
// ---------------------------------------------------------------------------

/// A re-sync from a source that carries no authors must not erase the authors a
/// previous source supplied — so the empty forms are the only ones that yield.
#[test]
fn an_empty_stored_list_is_filled_and_a_full_one_is_kept() {
    assert_eq!(merge_json_list(None, &["a".into()]), r#"["a"]"#);
    assert_eq!(merge_json_list(Some(""), &["a".into()]), r#"["a"]"#);
    assert_eq!(merge_json_list(Some("[]"), &["a".into()]), r#"["a"]"#);
    assert_eq!(merge_json_list(Some(r#"["a"]"#), &["b".into()]), r#"["a"]"#);
    assert_eq!(
        merge_json_list(Some(r#"["a"]"#), &[]),
        r#"["a"]"#,
        "an incoming empty list does not erase"
    );
}

/// The emptiness test is on the **serialised** form, so `"[ ]"` and `"null"`
/// are treated as non-empty content and kept. That is not tidiness: the column
/// holds whatever was written, and only the two exact empty spellings mean
/// "nothing stored".
#[test]
fn the_emptiness_test_is_on_the_stored_text() {
    assert_eq!(merge_json_list(Some("[ ]"), &["a".into()]), "[ ]");
    assert_eq!(merge_json_list(Some("null"), &["a".into()]), "null");
    assert_eq!(merge_json_list(Some("[\"\"]"), &["a".into()]), r#"[""]"#);
}

// ---------------------------------------------------------------------------
// Per-source scoping
// ---------------------------------------------------------------------------

/// Rows are grouped by the source that asserted them, so each source's batch
/// replaces only its own rows. Scoping by publication instead made the stored
/// set depend on whichever source synced last — flip-flopping with no error.
#[test]
fn child_rows_group_by_source() {
    let rows = vec![
        ("pubmed".to_string(), "R01".to_string()),
        ("openalex".to_string(), "X".to_string()),
        ("pubmed".to_string(), "R02".to_string()),
    ];
    let grouped = group_by_source("publication_grants", &rows).expect("named");
    assert_eq!(grouped.len(), 2);
    assert_eq!(grouped["pubmed"].len(), 2);
    assert_eq!(grouped["openalex"].len(), 1);
    assert_eq!(grouped["pubmed"][0], &"R01");
}

/// An unnamed row is **refused**, because scoping is the whole mechanism: a row
/// that names no source can never be replaced by a later sync, so it would
/// accumulate a duplicate beside the correctly-labelled row for ever.
#[test]
fn a_row_naming_no_source_is_refused() {
    let rows = vec![(String::new(), "R01".to_string())];
    let err = group_by_source("publication_grants", &rows).expect_err("refused");
    assert!(err.to_string().contains("publication_grants"));
    assert!(err.to_string().contains("must name the source"));
    // A named row in the same batch does not rescue it.
    let mixed = vec![
        ("pubmed".to_string(), "R01".to_string()),
        (String::new(), "R02".to_string()),
    ];
    assert!(group_by_source("publication_grants", &mixed).is_err());
}

/// An empty batch is not an error — there is no source to scope a delete to,
/// and an absent `<GrantList>` means the record did not carry the data rather
/// than that the funding was withdrawn.
#[test]
fn an_empty_batch_groups_to_nothing_without_failing() {
    let rows: Vec<(String, String)> = Vec::new();
    assert!(group_by_source("publication_grants", &rows)
        .expect("no rows, no error")
        .is_empty());
}
