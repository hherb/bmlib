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

//! The library through the FFI, held to `tests/fixtures/library_cases.json`,
//! the contract bmlib's Rust and Python suites run.

use std::path::{Path, PathBuf};

use serde_json::{json, Value};

use super::*;

const FIXTURE: &str = include_str!("../../../tests/fixtures/library_cases.json");

/// One section of the fixture.
fn section(name: &str) -> Vec<Value> {
    let root: Value = serde_json::from_str(FIXTURE).expect("fixture is JSON");
    root[name].as_array().expect("section").clone()
}

/// The case's string at `key`, or `None`.
fn opt(case: &Value, key: &str) -> Option<String> {
    case[key].as_str().map(str::to_owned)
}

/// The kind named `name` in the fixture, spelled out rather than converted, so
/// the conversion under test cannot vouch for itself.
fn kind(name: &str) -> SourceKind {
    match name {
        "pdf" => SourceKind::Pdf,
        "jats" => SourceKind::Jats,
        "html" => SourceKind::Html,
        "markdown" => SourceKind::Markdown,
        "text" => SourceKind::Text,
        other => panic!("unknown kind {other}"),
    }
}

#[test]
fn every_kind_converts_to_its_own_name_both_ways() {
    for name in ["pdf", "jats", "html", "markdown", "text"] {
        let core = lib::SourceKind::parse(name).unwrap();
        assert_eq!(SourceKind::from(core), kind(name), "{name} going out");
        assert_eq!(lib::SourceKind::from(kind(name)), core, "{name} going in");
    }
}

/// One bmlib scan problem of every variant, with distinct values in every field.
fn every_core_problem() -> Vec<lib::Problem> {
    use lib::Problem as P;
    let f = || "folder-a".to_owned();
    vec![
        P::UnreadableRecord {
            folder: f(),
            file: "file-b".into(),
            reason: "r1".into(),
        },
        P::UnreadableFolder {
            folder: f(),
            reason: "r2".into(),
        },
        P::NewerRecord {
            folder: f(),
            file: "file-c".into(),
            version: 9,
        },
        P::RecordNotDownloaded {
            folder: f(),
            file: "file-d".into(),
        },
        P::LeftoverTemp {
            folder: f(),
            name: "name-e".into(),
        },
        P::MisnamedRecord {
            folder: f(),
            file: "file-f".into(),
        },
        P::ConflictingIds {
            folder: f(),
            field: "doi",
        },
        P::UnrecordedFile {
            folder: f(),
            name: "name-g".into(),
        },
        P::StrayEntry {
            name: "name-h".into(),
        },
    ]
}

#[test]
fn problems_keep_every_field() {
    for core in every_core_problem() {
        // Field by name: a swap or a wrong variant changes the text.
        assert_eq!(
            format!("{:?}", LibraryProblem::from(&core)),
            format!("{core:?}")
        );
    }
}

/// A fresh directory under the system's temporary one.
fn scratch(tag: &str) -> PathBuf {
    let base = std::env::temp_dir().join(format!("bmlib-ffi-{tag}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&base);
    std::fs::create_dir_all(&base).unwrap();
    base
}

/// The sample library copied into a scratch directory, opened.
fn sample(tag: &str) -> (PathBuf, Arc<Library>) {
    fn copy(from: &Path, to: &Path) {
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
    let base = scratch(tag);
    let root = base.join("sample");
    copy(
        &Path::new(env!("CARGO_MANIFEST_DIR")).join("../../tests/fixtures/library/sample"),
        &root,
    );
    (base, Library::open(root.display().to_string()).unwrap())
}

/// The `field` of an identifier or installation error.
fn error_field(e: &LibraryError) -> &str {
    match e {
        LibraryError::BadIdentifier { field, .. } | LibraryError::BadInstallation { field, .. } => {
            field
        }
        other => panic!("unexpected error {other}"),
    }
}

#[test]
fn canonical_ids_match_the_fixture() {
    for case in section("canonical_ids") {
        let got = paper_ids_canonical(opt(&case, "pmid"), opt(&case, "doi"), opt(&case, "pmcid"));
        match case["error"].as_str() {
            Some(field) => assert_eq!(error_field(&got.unwrap_err()), field, "{}", case["name"]),
            None => {
                let ids = got.unwrap();
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
        let s = |k: &str| case[k].as_str().unwrap().to_owned();
        let got = installation_new(s("app"), s("id"), s("label"));
        match case["error"].as_str() {
            Some(field) => assert_eq!(error_field(&got.unwrap_err()), field, "{}", case["name"]),
            None => {
                let inst: lib::Installation = got.unwrap().try_into().unwrap();
                assert_eq!(inst.record_file_name(), s("expect"), "{}", case["name"]);
            }
        }
    }
}

/// One bmlib error of every variant, with distinct values in every field.
fn every_core_error() -> Vec<lib::LibraryError> {
    use lib::LibraryError as E;
    let f = || "folder-a".to_owned();
    let n = || "name-b".to_owned();
    let p = || PathBuf::from("/lib/path-c");
    let mut errors = vec![
        E::Unreachable {
            path: p(),
            reason: "r1".into(),
        },
        E::NotALibrary {
            path: p(),
            reason: "r2".into(),
        },
        E::NewerLibrary { version: 7 },
        E::ReadOnly { path: p() },
        E::BadName { name: n() },
        E::Missing {
            folder: f(),
            name: n(),
        },
        E::NotDownloaded {
            folder: f(),
            name: n(),
        },
        E::DigestMismatch {
            folder: f(),
            name: n(),
            expected: "e".into(),
            found: "g".into(),
        },
        E::FileClash {
            folder: f(),
            name: n(),
        },
        E::NewerRecord {
            folder: f(),
            file: "x.json".into(),
            version: 3,
        },
        E::Io {
            path: p(),
            reason: "r3".into(),
        },
    ];
    for field in ["pmid", "doi", "pmcid"] {
        errors.push(E::BadIdentifier {
            field,
            value: "v".into(),
        });
    }
    for field in ["app", "id", "label"] {
        errors.push(E::BadInstallation {
            field,
            reason: "r4".into(),
        });
    }
    errors
}

#[test]
fn errors_keep_every_field_and_bmlibs_wording() {
    for core in every_core_error() {
        let ffi = LibraryError::from(core.clone());
        assert_eq!(ffi.to_string(), core.to_string());
        assert_eq!(ffi.to_core(), core, "every field survives the round trip");
        // Field by name, so a swap made in both directions does not cancel out.
        assert_eq!(format!("{ffi:?}"), format!("{core:?}"));
    }
}

#[test]
fn scan_matches_the_fixture() {
    let (base, library) = sample("scan");
    let scan = library.scan().unwrap();
    let want = &section("scan")[0]["expect"];
    let folders: Vec<Value> = scan
        .folders()
        .iter()
        .map(|f| {
            let used_by: serde_json::Map<String, Value> = f
                .records
                .iter()
                .flat_map(|r| &r.files)
                .map(|e| {
                    let who: Vec<String> = scan
                        .used_by(f.folder.clone(), e.sha256.clone())
                        .unwrap()
                        .iter()
                        .map(|i| format!("{}-{}", i.app, i.id))
                        .collect();
                    (e.sha256.clone(), json!(who))
                })
                .collect();
            json!({
                "folder": f.folder,
                "ids": {"pmid": f.ids.pmid, "doi": f.ids.doi, "pmcid": f.ids.pmcid},
                "records": f.records.iter().map(|r| json!({
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
        .problems()
        .into_iter()
        .map(|p| match p {
            LibraryProblem::UnreadableRecord {
                folder,
                file,
                reason,
            } => {
                assert!(!reason.is_empty());
                json!({"kind": "unreadable_record", "folder": folder, "file": file})
            }
            LibraryProblem::UnreadableFolder { .. } => panic!("no unreadable folder in the sample"),
            LibraryProblem::LeftoverTemp { folder, name } => {
                json!({"kind": "leftover_temp", "folder": folder, "name": name})
            }
            LibraryProblem::RecordNotDownloaded { folder, file } => {
                json!({"kind": "record_not_downloaded", "folder": folder, "file": file})
            }
            LibraryProblem::ConflictingIds { folder, field } => {
                json!({"kind": "conflicting_ids", "folder": folder, "field": field})
            }
            LibraryProblem::NewerRecord {
                folder,
                file,
                version,
            } => {
                json!({"kind": "newer_record", "folder": folder, "file": file, "version": version})
            }
            LibraryProblem::MisnamedRecord { folder, file } => {
                json!({"kind": "misnamed_record", "folder": folder, "file": file})
            }
            LibraryProblem::UnrecordedFile { folder, name } => {
                json!({"kind": "unrecorded_file", "folder": folder, "name": name})
            }
            LibraryProblem::StrayEntry { name } => json!({"kind": "stray_entry", "name": name}),
        })
        .collect();
    assert_eq!(Value::Array(problems), want["problems"]);
    assert_eq!(scan.used_by("no such folder".into(), "x".into()), None);
    let _ = std::fs::remove_dir_all(base);
}

#[test]
fn find_find_digest_and_group_match_the_fixture() {
    let (base, library) = sample("find");
    let scan = library.scan().unwrap();
    for case in section("find") {
        let ids = PaperIds {
            pmid: opt(&case, "pmid"),
            doi: opt(&case, "doi"),
            pmcid: opt(&case, "pmcid"),
        };
        let got: Vec<String> = scan
            .find(ids)
            .unwrap()
            .into_iter()
            .map(|f| f.folder)
            .collect();
        assert_eq!(json!(got), case["expect"], "{}", case["name"]);
    }
    for case in section("find_digest") {
        let sha = case["sha256"].as_str().unwrap().to_owned();
        let got = scan.find_digest(sha.clone());
        if sha.len() != 64 {
            // bmlib finds nothing for it; the FFI refuses it, so an app's
            // mis-stored digest does not read as "not in the library".
            assert_eq!(
                got,
                Err(LibraryError::BadIdentifier {
                    field: "sha256".into(),
                    value: sha,
                }),
                "{}",
                case["name"]
            );
            continue;
        }
        let want = case["expect"].as_object().map(|w| FileRef {
            folder: w["folder"].as_str().unwrap().to_owned(),
            name: w["name"].as_str().unwrap().to_owned(),
            sha256: sha.clone(),
            kind: kind(w["kind"].as_str().unwrap()),
        });
        assert_eq!(got, Ok(want), "{}", case["name"]);
    }
    for bad in ["A".repeat(64), format!(" {}", "a".repeat(63))] {
        assert!(matches!(
            scan.find_digest(bad),
            Err(LibraryError::BadIdentifier { ref field, .. }) if field == "sha256"
        ));
    }
    for case in section("group") {
        assert_eq!(json!(scan.group()), case["expect"], "{}", case["name"]);
    }
    let bad = PaperIds {
        pmid: Some("12x".into()),
        doi: None,
        pmcid: None,
    };
    assert!(matches!(
        scan.find(bad),
        Err(LibraryError::BadIdentifier { .. })
    ));
    let _ = std::fs::remove_dir_all(base);
}

#[test]
fn read_matches_the_fixture() {
    let (base, library) = sample("read");
    for case in section("read") {
        let f = &case["file"];
        let file = FileRef {
            folder: f["folder"].as_str().unwrap().to_owned(),
            name: f["name"].as_str().unwrap().to_owned(),
            sha256: f["sha256"].as_str().unwrap().to_owned(),
            kind: kind(f["kind"].as_str().unwrap()),
        };
        let name = &case["name"];
        match (case["expect"].as_str().unwrap(), library.read(file)) {
            ("ok", Ok(bytes)) => {
                assert_eq!(lib::sha256_hex(&bytes), case["bytes_sha256"], "{name}")
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

#[test]
fn open_matches_the_fixture() {
    for case in section("open") {
        let name = case["name"].as_str().unwrap();
        let base = scratch(&format!("open-{name}"));
        let root = base.join("root");
        if let Some(entries) = case["entries"].as_array() {
            std::fs::create_dir_all(&root).unwrap();
            for e in entries {
                match e["dir"].as_str() {
                    Some(d) => std::fs::create_dir_all(root.join(d)).unwrap(),
                    None => std::fs::write(
                        root.join(e["file"].as_str().unwrap()),
                        e["text"].as_str().unwrap(),
                    )
                    .unwrap(),
                }
            }
        }
        match (
            case["expect"].as_str().unwrap(),
            Library::open(root.display().to_string()),
        ) {
            ("created" | "opened", Ok(library)) => {
                assert_eq!(library.root(), root.display().to_string(), "{name}");
            }
            ("newer", Err(LibraryError::NewerLibrary { version })) => {
                assert_eq!(version, case["version"].as_u64().unwrap(), "{name}");
            }
            ("not_a_library", Err(LibraryError::NotALibrary { .. }))
            | ("unreachable", Err(LibraryError::Unreachable { .. })) => {}
            (want, got) => panic!("{name}: wanted {want}, got {got:?}"),
        }
        let _ = std::fs::remove_dir_all(base);
    }
}

/// A valid installation of `app`.
fn who(app: &str) -> Installation {
    installation_new(app.into(), "aaaa".into(), "Test Mac".into()).unwrap()
}

/// A paper known by its PMID.
fn paper(pmid: &str) -> PaperInfo {
    PaperInfo {
        ids: PaperIds {
            pmid: Some(pmid.into()),
            doi: None,
            pmcid: None,
        },
        title: Some("Metformin".into()),
        year: Some(2024),
        first_author: Some("Smith".into()),
    }
}

#[test]
fn save_use_release_and_remove_round_trip() {
    let base = scratch("round-trip");
    let library = Library::open(base.display().to_string()).unwrap();
    let (sa, mfc) = (who("studyanalyzer"), who("medicalfactchecker"));
    let bytes = b"%PDF-1.7 a paper".to_vec();
    // Raw identifiers come back canonical.
    let saved = library
        .save(
            sa.clone(),
            paper("00111"),
            bytes.clone(),
            SourceKind::Pdf,
            "unit test".into(),
        )
        .unwrap();
    assert_eq!(saved.sha256, lib::sha256_hex(&bytes));
    assert_eq!(saved.kind, SourceKind::Pdf);
    // The folder's name pins title, author and year going in; the file's name
    // pins the kind going in (fixture `save_scenarios`' first case).
    assert_eq!(saved.folder, "2024 Smith \u{2013} Metformin (PMID 111)");
    assert_eq!(saved.name, format!("pdf-{}.pdf", &saved.sha256[..12]));
    let scan = library.scan().unwrap();
    assert_eq!(
        scan.find_digest(saved.sha256.clone()),
        Ok(Some(saved.clone()))
    );
    assert_eq!(scan.folders()[0].ids.pmid.as_deref(), Some("111"));
    let record = &scan.folders()[0].records[0];
    assert_eq!(record.installation, sa);
    assert_eq!(record.paper, paper("111"), "the paper going out, canonical");
    let entry = &record.files[0];
    assert_eq!(
        (
            &entry.sha256,
            entry.kind,
            &entry.name,
            &entry.origin,
            entry.size
        ),
        (
            &saved.sha256,
            SourceKind::Pdf,
            &saved.name,
            &"unit test".to_owned(),
            bytes.len() as u64
        )
    );
    assert_eq!(library.read(saved.clone()).unwrap(), bytes);

    library
        .record_use(mfc.clone(), paper("111"), saved.clone())
        .unwrap();
    let users = |l: &Library| {
        l.scan()
            .unwrap()
            .used_by(saved.folder.clone(), saved.sha256.clone())
            .unwrap()
    };
    assert_eq!(users(&library), vec![mfc.clone(), sa.clone()]);
    library.release(mfc, saved.clone()).unwrap();
    assert_eq!(users(&library), vec![sa]);

    library.remove_file(saved.clone()).unwrap();
    assert!(matches!(
        library.read(saved.clone()),
        Err(LibraryError::Missing { .. })
    ));
    library.remove_folder(saved.folder.clone()).unwrap();
    assert!(library.scan().unwrap().folders().is_empty());
    let _ = std::fs::remove_dir_all(base);
}

#[test]
fn a_refused_save_writes_nothing() {
    let base = scratch("refused");
    let library = Library::open(base.display().to_string()).unwrap();
    let mut bad_doi = paper("1");
    bad_doi.ids.doi = Some("not a doi".into());
    let got = library.save(
        who("studyanalyzer"),
        bad_doi,
        b"x".to_vec(),
        SourceKind::Text,
        String::new(),
    );
    assert!(matches!(got, Err(LibraryError::BadIdentifier { ref field, .. }) if field == "doi"));
    let bad_who = Installation {
        app: "Study Analyzer".into(),
        id: "aaaa".into(),
        label: "Mac".into(),
    };
    let got = library.save(
        bad_who,
        paper("1"),
        b"x".to_vec(),
        SourceKind::Text,
        String::new(),
    );
    assert!(matches!(got, Err(LibraryError::BadInstallation { ref field, .. }) if field == "app"));
    assert!(library.scan().unwrap().folders().is_empty());
    let _ = std::fs::remove_dir_all(base);
}
