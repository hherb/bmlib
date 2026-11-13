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

//! The shared library-module contract: canonical identifiers, installations
//! and folder names, from `tests/fixtures/library_cases.json` — the file
//! Python's `tests/test_library_contract.py` asserts against.

use bmlib::library::{
    folder_name, parse_record, render_record, Installation, Library, LibraryError, PaperIds,
    PaperInfo, RecordProblem, SourceKind, CORRUPT_SUFFIX, MAX_FOLDER_NAME_BYTES,
};
use serde_json::Value;

const FIXTURE: &str = include_str!("../../../tests/fixtures/library_cases.json");

fn section(name: &str) -> Vec<Value> {
    let root: Value = serde_json::from_str(FIXTURE).expect("fixture is JSON");
    root[name]
        .as_array()
        .unwrap_or_else(|| panic!("section {name}"))
        .clone()
}

fn opt(case: &Value, key: &str) -> Option<String> {
    case[key].as_str().map(str::to_owned)
}

/// The `field` of an identifier or installation error, to compare with the fixture.
fn error_field(err: &LibraryError) -> &'static str {
    match err {
        LibraryError::BadIdentifier { field, .. } | LibraryError::BadInstallation { field, .. } => {
            field
        }
        other => panic!("unexpected error {other}"),
    }
}

#[test]
fn canonical_ids_match_the_fixture() {
    for case in section("canonical_ids") {
        let got = PaperIds::canonical(
            case["pmid"].as_str(),
            case["doi"].as_str(),
            case["pmcid"].as_str(),
        );
        match case["error"].as_str() {
            Some(field) => {
                assert_eq!(error_field(&got.unwrap_err()), field, "{}", case["name"]);
            }
            None => {
                let ids = got.unwrap_or_else(|e| panic!("{}: {e}", case["name"]));
                let want = &case["expect"];
                assert_eq!(ids.pmid, opt(want, "pmid"), "{}", case["name"]);
                assert_eq!(ids.doi, opt(want, "doi"), "{}", case["name"]);
                assert_eq!(ids.pmcid, opt(want, "pmcid"), "{}", case["name"]);
            }
        }
    }
}

#[test]
fn installations_match_the_fixture() {
    for case in section("installations") {
        let got = Installation::new(
            case["app"].as_str().unwrap(),
            case["id"].as_str().unwrap(),
            case["label"].as_str().unwrap(),
        );
        match case["error"].as_str() {
            Some(field) => {
                assert_eq!(error_field(&got.unwrap_err()), field, "{}", case["name"]);
            }
            None => {
                let inst = got.unwrap_or_else(|e| panic!("{}: {e}", case["name"]));
                assert_eq!(inst.record_file_name(), case["expect"].as_str().unwrap());
            }
        }
    }
}

fn folder_cases() -> Vec<(Value, PaperInfo)> {
    section("folder_names")
        .into_iter()
        .map(|case| {
            let info = PaperInfo {
                ids: PaperIds {
                    pmid: opt(&case, "pmid"),
                    doi: opt(&case, "doi"),
                    pmcid: opt(&case, "pmcid"),
                },
                title: opt(&case, "title"),
                year: case["year"].as_i64().map(|y| y as i32),
                first_author: opt(&case, "first_author"),
            };
            (case, info)
        })
        .collect()
}

#[test]
fn folder_names_match_the_fixture() {
    for (case, info) in folder_cases() {
        assert_eq!(
            folder_name(&info),
            case["expect"].as_str().unwrap(),
            "{}",
            case["name"]
        );
    }
}

#[test]
fn folder_name_is_at_most_150_bytes() {
    assert_eq!(MAX_FOLDER_NAME_BYTES, 150);
    for (case, info) in folder_cases() {
        assert!(
            folder_name(&info).len() <= MAX_FOLDER_NAME_BYTES,
            "{}",
            case["name"]
        );
    }
}

#[test]
fn source_kinds_round_trip() {
    for (kind, name, ext) in [
        (SourceKind::Pdf, "pdf", "pdf"),
        (SourceKind::Jats, "jats", "xml"),
        (SourceKind::Html, "html", "html"),
        (SourceKind::Markdown, "markdown", "md"),
        (SourceKind::Text, "text", "txt"),
    ] {
        assert_eq!(kind.as_str(), name);
        assert_eq!(kind.extension(), ext);
        assert_eq!(SourceKind::parse(name), Some(kind));
    }
    assert_eq!(SourceKind::parse("PDF"), None);
    assert_eq!(CORRUPT_SUFFIX, ".corrupt");
}

#[test]
fn merging_fills_gaps_and_names_conflicts() {
    let a = PaperIds::canonical(Some("1"), None, Some("PMC5")).unwrap();
    let b = PaperIds::canonical(Some("2"), Some("10.1/x"), Some("5")).unwrap();
    assert!(a.shares_any(&b));
    let (m, conflicts) = a.merged(&b);
    assert_eq!(m.pmid.as_deref(), Some("1"));
    assert_eq!(m.doi.as_deref(), Some("10.1/x"));
    assert_eq!(conflicts, vec!["pmid"]);
    assert!(!a.shares_any(&PaperIds::default()));
    assert!(PaperIds::default().is_empty());
}

#[test]
fn records_match_the_fixture() {
    for case in section("records") {
        let name = case["name"].as_str().unwrap();
        let got = parse_record(case["text"].as_str().unwrap());
        match (case["expect"].as_str().unwrap(), got) {
            ("ok", Ok(rec)) => {
                assert_eq!(
                    rec.files.len() as u64,
                    case["files"].as_u64().unwrap(),
                    "{name}"
                );
                assert_eq!(
                    render_record(&rec),
                    case["rendered"].as_str().unwrap(),
                    "{name}"
                );
            }
            ("newer", Err(RecordProblem::Newer(v))) => {
                assert_eq!(u64::from(v), case["version"].as_u64().unwrap(), "{name}");
            }
            ("unreadable", Err(RecordProblem::Unreadable(why))) => {
                assert!(!why.is_empty(), "{name}: a reason is given");
            }
            (want, got) => panic!("{name}: wanted {want}, got {got:?}"),
        }
    }
}

#[test]
fn render_then_parse_round_trips() {
    for case in section("records") {
        if case["expect"] == "ok" {
            let rec = parse_record(case["text"].as_str().unwrap()).unwrap();
            assert_eq!(parse_record(&render_record(&rec)).unwrap(), rec);
        }
    }
}

#[test]
fn open_matches_the_fixture() {
    for case in section("open") {
        let name = case["name"].as_str().unwrap();
        let base = std::env::temp_dir().join(format!("bmlib-open-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        let root = base.join("root");
        if let Some(entries) = case["entries"].as_array() {
            std::fs::create_dir_all(&root).unwrap();
            for e in entries {
                if let Some(d) = e["dir"].as_str() {
                    std::fs::create_dir_all(root.join(d)).unwrap();
                } else {
                    std::fs::write(
                        root.join(e["file"].as_str().unwrap()),
                        e["text"].as_str().unwrap(),
                    )
                    .unwrap();
                }
            }
        }
        let got = Library::open(&root);
        match (case["expect"].as_str().unwrap(), got) {
            ("created" | "opened", Ok(lib)) => {
                assert_eq!(lib.root(), root, "{name}");
                assert!(root.join("papers").is_dir(), "{name}: papers/");
                if let Some(want) = case["marker"].as_str() {
                    let text = std::fs::read_to_string(root.join("bmlib-library.json")).unwrap();
                    assert_eq!(text, want, "{name}");
                }
            }
            ("newer", Err(LibraryError::NewerLibrary { version })) => {
                assert_eq!(version, case["version"].as_u64().unwrap(), "{name}");
            }
            ("not_a_library", Err(LibraryError::NotALibrary { .. }))
            | ("unreachable", Err(LibraryError::Unreachable { .. })) => {}
            (want, got) => panic!("{name}: wanted {want}, got {got:?}"),
        }
        let _ = std::fs::remove_dir_all(&base);
    }
}
