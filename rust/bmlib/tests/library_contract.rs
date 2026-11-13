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
    find, find_digest, folder_name, folder_name_numbered, group, parse_record, render_record,
    sha256_hex, FileRef, Installation, Library, LibraryError, PaperIds, PaperInfo, Problem,
    RecordProblem, SourceKind, CORRUPT_SUFFIX, MAX_FOLDER_NAME_BYTES,
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

/// The case's name: numbered when it has an `n`, else the plain one.
fn plain_or_numbered(case: &Value, info: &PaperInfo) -> String {
    match case["n"].as_u64() {
        Some(n) => folder_name_numbered(info, n as u32),
        None => folder_name(info),
    }
}

#[test]
fn numbered_one_is_the_plain_name() {
    let (_, info) = folder_cases().remove(0);
    assert_eq!(folder_name_numbered(&info, 1), folder_name(&info));
}

#[test]
fn folder_names_match_the_fixture() {
    for (case, info) in folder_cases() {
        assert_eq!(
            plain_or_numbered(&case, &info),
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
            plain_or_numbered(&case, &info).len() <= MAX_FOLDER_NAME_BYTES,
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

/// The sample library copied to a fresh temporary directory (nothing writes into the fixture).
/// Returns the base directory to remove and the opened library.
fn sample_library(tag: &str) -> (std::path::PathBuf, Library) {
    fn copy(from: &std::path::Path, to: &std::path::Path) {
        std::fs::create_dir_all(to).unwrap();
        for entry in std::fs::read_dir(from).unwrap() {
            let entry = entry.unwrap();
            let target = to.join(entry.file_name());
            if entry.file_type().unwrap().is_dir() {
                copy(&entry.path(), &target);
            } else {
                std::fs::copy(entry.path(), target).unwrap();
            }
        }
    }
    let base = std::env::temp_dir().join(format!("bmlib-sample-{tag}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&base);
    let root = base.join("sample");
    copy(
        &std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../tests/fixtures/library/sample"),
        &root,
    );
    let lib = Library::open(&root).unwrap();
    (base, lib)
}

fn id_value(ids: &PaperIds) -> Value {
    serde_json::json!({"pmid": ids.pmid, "doi": ids.doi, "pmcid": ids.pmcid})
}

#[test]
fn scan_matches_the_fixture() {
    let (base, lib) = sample_library("scan");
    let scan = lib.scan().unwrap();
    let want = &section("scan")[0]["expect"];
    let folders: Vec<Value> = scan
        .folders
        .iter()
        .map(|f| {
            let used_by: serde_json::Map<String, Value> = f
                .records
                .iter()
                .flat_map(|r| r.files.iter())
                .map(|e| {
                    let who: Vec<String> = f
                        .used_by(&e.sha256)
                        .iter()
                        .map(|i| format!("{}-{}", i.app, i.id))
                        .collect();
                    (e.sha256.clone(), serde_json::json!(who))
                })
                .collect();
            serde_json::json!({
                "folder": f.folder,
                "ids": id_value(&f.ids()),
                "records": f.records.iter().map(|r| serde_json::json!({
                    "app": r.installation.app,
                    "id": r.installation.id,
                    "files": r.files.iter().map(|e| e.name.clone()).collect::<Vec<_>>(),
                })).collect::<Vec<_>>(),
                "files_on_disk": f.files_on_disk,
                "used_by": used_by,
            })
        })
        .collect();
    assert_eq!(Value::Array(folders), want["folders"]);
    let problems: Vec<Value> = scan
        .problems
        .iter()
        .map(|p| match p {
            Problem::UnreadableRecord {
                folder,
                file,
                reason,
            } => {
                assert!(!reason.is_empty());
                serde_json::json!({"kind": "unreadable_record", "folder": folder, "file": file})
            }
            Problem::UnreadableFolder { .. } => panic!("no unreadable folder in the sample"),
            Problem::LeftoverTemp { folder, name } => {
                serde_json::json!({"kind": "leftover_temp", "folder": folder, "name": name})
            }
            Problem::RecordNotDownloaded { folder, file } => {
                serde_json::json!({"kind": "record_not_downloaded", "folder": folder, "file": file})
            }
            Problem::ConflictingIds { folder, field } => {
                serde_json::json!({"kind": "conflicting_ids", "folder": folder, "field": field})
            }
            Problem::NewerRecord {
                folder,
                file,
                version,
            } => serde_json::json!(
                {"kind": "newer_record", "folder": folder, "file": file, "version": version}
            ),
            Problem::MisnamedRecord { folder, file } => {
                serde_json::json!({"kind": "misnamed_record", "folder": folder, "file": file})
            }
            Problem::UnrecordedFile { folder, name } => {
                serde_json::json!({"kind": "unrecorded_file", "folder": folder, "name": name})
            }
            Problem::StrayEntry { name } => {
                serde_json::json!({"kind": "stray_entry", "name": name})
            }
        })
        .collect();
    assert_eq!(Value::Array(problems), want["problems"]);
    let _ = std::fs::remove_dir_all(base);
}

#[test]
fn find_matches_the_fixture() {
    let (base, lib) = sample_library("find");
    let scan = lib.scan().unwrap();
    for case in section("find") {
        let ids = PaperIds::canonical(
            case["pmid"].as_str(),
            case["doi"].as_str(),
            case["pmcid"].as_str(),
        )
        .unwrap();
        let got: Vec<&str> = find(&scan, &ids)
            .iter()
            .map(|f| f.folder.as_str())
            .collect();
        let want: Vec<&str> = case["expect"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_str().unwrap())
            .collect();
        assert_eq!(got, want, "{}", case["name"]);
    }
    let _ = std::fs::remove_dir_all(base);
}

#[test]
fn find_digest_matches_the_fixture() {
    let (base, lib) = sample_library("find-digest");
    let scan = lib.scan().unwrap();
    for case in section("find_digest") {
        let sha = case["sha256"].as_str().unwrap();
        let got = find_digest(&scan, sha);
        let want = case["expect"].as_object().map(|w| FileRef {
            folder: w["folder"].as_str().unwrap().to_owned(),
            name: w["name"].as_str().unwrap().to_owned(),
            sha256: sha.to_owned(),
            kind: SourceKind::parse(w["kind"].as_str().unwrap()).unwrap(),
        });
        assert_eq!(got, want, "{}", case["name"]);
    }
    let _ = std::fs::remove_dir_all(base);
}

#[test]
fn group_matches_the_fixture() {
    let (base, lib) = sample_library("group");
    let scan = lib.scan().unwrap();
    for case in section("group") {
        let got: Value = serde_json::json!(group(&scan));
        assert_eq!(got, case["expect"], "{}", case["name"]);
    }
    let _ = std::fs::remove_dir_all(base);
}

#[test]
fn read_matches_the_fixture() {
    let (base, lib) = sample_library("read");
    for case in section("read") {
        let f = &case["file"];
        let file = FileRef {
            folder: f["folder"].as_str().unwrap().to_owned(),
            name: f["name"].as_str().unwrap().to_owned(),
            sha256: f["sha256"].as_str().unwrap().to_owned(),
            kind: SourceKind::parse(f["kind"].as_str().unwrap()).unwrap(),
        };
        let name = &case["name"];
        match (case["expect"].as_str().unwrap(), lib.read(&file)) {
            ("ok", Ok(bytes)) => {
                assert_eq!(sha256_hex(&bytes), case["bytes_sha256"], "{name}");
            }
            (
                "digest_mismatch",
                Err(LibraryError::DigestMismatch {
                    expected, found, ..
                }),
            ) => {
                assert_eq!(expected, case["expected"].as_str().unwrap(), "{name}");
                assert_eq!(found, case["found"].as_str().unwrap(), "{name}");
            }
            ("not_downloaded", Err(LibraryError::NotDownloaded { .. }))
            | ("missing", Err(LibraryError::Missing { .. }))
            | ("bad_name", Err(LibraryError::BadName { .. })) => {}
            (want, got) => panic!("{name}: wanted {want}, got {got:?}"),
        }
    }
    let _ = std::fs::remove_dir_all(base);
}

/// Every file and folder under `root` (folders with a trailing slash), sorted.
fn tree(root: &std::path::Path) -> Vec<String> {
    fn walk(root: &std::path::Path, dir: &std::path::Path, out: &mut Vec<String>) {
        for entry in std::fs::read_dir(dir).unwrap() {
            let path = entry.unwrap().path();
            let rel = path
                .strip_prefix(root)
                .unwrap()
                .to_string_lossy()
                .replace('\\', "/");
            if path.is_dir() {
                out.push(format!("{rel}/"));
                walk(root, &path, out);
            } else {
                out.push(rel);
            }
        }
    }
    let mut out = Vec::new();
    walk(root, root, &mut out);
    out.sort();
    out
}

fn paper_info(p: &Value) -> PaperInfo {
    PaperInfo {
        ids: PaperIds::canonical(p["pmid"].as_str(), p["doi"].as_str(), p["pmcid"].as_str())
            .unwrap(),
        title: opt(p, "title"),
        year: p["year"].as_i64().map(|y| y as i32),
        first_author: opt(p, "first_author"),
    }
}

fn installation(step: &Value) -> Installation {
    Installation::new(
        step["app"].as_str().unwrap(),
        step["id"].as_str().unwrap(),
        "Test Mac",
    )
    .unwrap()
}

/// `YYYY-MM-DDTHH:MM:SSZ`.
fn is_saved_at(s: &str) -> bool {
    let b = s.as_bytes();
    b.len() == 20
        && b[19] == b'Z'
        && [4, 7].iter().all(|&i| b[i] == b'-')
        && b[10] == b'T'
        && [13, 16].iter().all(|&i| b[i] == b':')
        && b.iter()
            .enumerate()
            .all(|(i, c)| [4, 7, 10, 13, 16, 19].contains(&i) || c.is_ascii_digit())
}

#[test]
fn save_scenarios_match_the_fixture() {
    for case in section("save_scenarios") {
        let name = case["name"].as_str().unwrap();
        let base = std::env::temp_dir().join(format!(
            "bmlib-save-{}-{}",
            std::process::id(),
            name.replace(|c: char| !c.is_ascii_alphanumeric(), "_")
        ));
        let _ = std::fs::remove_dir_all(&base);
        std::fs::create_dir_all(&base).unwrap();
        let lib = Library::open(&base).unwrap();
        let mut saved: Vec<Option<FileRef>> = Vec::new();
        for step in case["steps"].as_array().unwrap() {
            let of = |s: &Value| saved[s["of"].as_u64().unwrap() as usize].clone().unwrap();
            let mut made = None;
            match step["op"].as_str().unwrap() {
                "save" => {
                    made = Some(
                        lib.save(
                            &installation(step),
                            &paper_info(&step["paper"]),
                            step["text"].as_str().unwrap().as_bytes(),
                            SourceKind::parse(step["kind"].as_str().unwrap()).unwrap(),
                            step["origin"].as_str().unwrap(),
                        )
                        .unwrap_or_else(|e| panic!("{name}: {e}")),
                    );
                }
                "release" => lib.release(&installation(step), &of(step)).unwrap(),
                "record_use" => lib
                    .record_use(&installation(step), &paper_info(&step["paper"]), &of(step))
                    .unwrap_or_else(|e| panic!("{name}: {e}")),
                "remove_file" => {
                    let got = lib.remove_file(&of(step));
                    if step["expect"] == "missing" {
                        assert!(matches!(got, Err(LibraryError::Missing { .. })), "{name}");
                    } else {
                        got.unwrap();
                    }
                }
                "remove_folder" => {
                    let got = lib.remove_folder(step["folder"].as_str().unwrap());
                    if step["expect"] == "bad_name" {
                        assert!(matches!(got, Err(LibraryError::BadName { .. })), "{name}");
                    } else if step["expect"] == "missing" {
                        assert!(matches!(got, Err(LibraryError::Missing { .. })), "{name}");
                    } else {
                        got.unwrap();
                    }
                }
                "read" => assert!(
                    matches!(lib.read(&of(step)), Err(LibraryError::Missing { .. })),
                    "{name}"
                ),
                "used_by" => {
                    let file = of(step);
                    let scan = lib.scan().unwrap();
                    let mut users: Vec<String> = scan
                        .folders
                        .iter()
                        .filter(|f| f.folder == file.folder)
                        .flat_map(|f| f.used_by(&file.sha256))
                        .map(|i| format!("{}-{}", i.app, i.id))
                        .collect();
                    users.sort();
                    assert_eq!(serde_json::json!(users), step["expect"], "{name}");
                }
                other => panic!("unknown step {other}"),
            }
            saved.push(made);
        }
        let want = &case["expect"];
        assert_eq!(serde_json::json!(tree(&base)), want["paths"], "{name}");
        for (rel, record) in want["records"].as_object().unwrap() {
            let mut got: Value =
                serde_json::from_str(&std::fs::read_to_string(base.join(rel)).unwrap()).unwrap();
            for entry in got["files"].as_array_mut().unwrap() {
                let at = entry.as_object_mut().unwrap().remove("saved_at").unwrap();
                assert!(is_saved_at(at.as_str().unwrap()), "{name}: {at}");
            }
            assert_eq!(&got, record, "{name}: {rel}");
        }
        let _ = std::fs::remove_dir_all(&base);
    }
}

#[test]
fn component_matches_the_fixture() {
    // A folder the bad names could reach on Windows (`x.` is `x` there, `...` is
    // `papers` itself) is planted; no bad name may remove or read anything.
    let base = std::env::temp_dir().join(format!("bmlib-component-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&base);
    std::fs::create_dir_all(&base).unwrap();
    let lib = Library::open(&base).unwrap();
    std::fs::create_dir_all(base.join("papers/x")).unwrap();
    std::fs::write(base.join("papers/x/keep"), "planted").unwrap();
    let before = tree(&base);
    for case in section("component") {
        let (name, value) = (&case["name"], case["value"].as_str().unwrap());
        let file = FileRef {
            folder: value.to_owned(),
            name: "keep".to_owned(),
            sha256: sha256_hex(b"planted"),
            kind: SourceKind::Text,
        };
        let got = (lib.remove_folder(value), lib.read(&file).map(|_| ()));
        match case["expect"].as_str().unwrap() {
            "bad_name" => assert!(
                matches!(
                    got,
                    (
                        Err(LibraryError::BadName { .. }),
                        Err(LibraryError::BadName { .. })
                    )
                ),
                "{name}: {got:?}"
            ),
            _ => assert!(
                matches!(
                    got,
                    (
                        Err(LibraryError::Missing { .. }),
                        Err(LibraryError::Missing { .. })
                    )
                ),
                "{name}: {got:?}"
            ),
        }
    }
    assert_eq!(tree(&base), before);
    let _ = std::fs::remove_dir_all(&base);
}
