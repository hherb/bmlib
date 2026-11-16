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
        P::BadFolderName {
            folder: "folder-i".into(),
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
fn sample(tag: &str) -> (PathBuf, Arc<PaperLibrary>) {
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
    (
        base,
        PaperLibrary::open(root.display().to_string()).unwrap(),
    )
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
    for (folder, field) in [(Some(f()), "pmid"), (None, "doi"), (Some(f()), "pmcid")] {
        errors.push(E::ForeignJats {
            folder,
            name: n(),
            field,
            theirs: "t".into(),
            ours: "o".into(),
        });
    }
    for field in ["pmid", "doi", "pmcid", "sha256"] {
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

/// A scan problem as the fixture spells it, every field kept.
fn problem_json(p: &LibraryProblem) -> Value {
    match p {
        LibraryProblem::UnreadableRecord {
            folder,
            file,
            reason,
        } => json!({"kind": "unreadable_record", "folder": folder, "file": file, "reason": reason}),
        LibraryProblem::UnreadableFolder { folder, reason } => {
            json!({"kind": "unreadable_folder", "folder": folder, "reason": reason})
        }
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
        } => json!({"kind": "newer_record", "folder": folder, "file": file, "version": version}),
        LibraryProblem::MisnamedRecord { folder, file } => {
            json!({"kind": "misnamed_record", "folder": folder, "file": file})
        }
        LibraryProblem::UnrecordedFile { folder, name } => {
            json!({"kind": "unrecorded_file", "folder": folder, "name": name})
        }
        LibraryProblem::StrayEntry { name } => json!({"kind": "stray_entry", "name": name}),
        LibraryProblem::BadFolderName { folder } => {
            json!({"kind": "bad_folder_name", "folder": folder})
        }
    }
}

#[test]
fn scan_matches_the_fixture() {
    let (base, library) = sample("scan");
    let scan = library.scan().unwrap();
    let want = &section("scan")[0]["expect"];
    // Each entry's kind and saved_at against the record file's own JSON, which
    // the fixture's expectation does not list.
    for f in scan.folders() {
        for r in &f.records {
            let path = Path::new(&library.root())
                .join("papers")
                .join(&f.folder)
                .join("records")
                .join(format!("{}-{}.json", r.installation.app, r.installation.id));
            let json: Value =
                serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
            let files = json["files"].as_array().unwrap();
            assert_eq!(files.len(), r.files.len(), "{}", f.folder);
            for (e, raw) in r.files.iter().zip(files) {
                assert_eq!(e.kind, kind(raw["kind"].as_str().unwrap()), "{}", e.name);
                assert_eq!(e.saved_at, raw["saved_at"].as_str().unwrap(), "{}", e.name);
            }
        }
    }
    let folders: Vec<Value> = scan
        .folders()
        .iter()
        .map(|f| {
            let used_by: serde_json::Map<String, Value> = f
                .records
                .iter()
                .flat_map(|r| &r.files)
                .map(|e| {
                    let users = scan
                        .used_by(f.folder.clone(), e.sha256.clone())
                        .unwrap()
                        .unwrap();
                    // Spelled out: Lee's folder has uncounted records, and
                    // Smith's PMID folder a record temp; the other two neither.
                    let complete = !matches!(
                        f.folder.as_str(),
                        "2023 Lee \u{2013} Statins (PMID 111)"
                            | "2024 Smith \u{2013} Metformin and cognition (PMID 38012345)"
                    );
                    assert_eq!(users.complete, complete, "{}", f.folder);
                    let who: Vec<String> = users
                        .installations
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
    // The sample's reasons are serde's wording, which the fixture leaves out.
    let problems: Vec<Value> = scan
        .problems()
        .iter()
        .map(|p| {
            let mut v = problem_json(p);
            if let Some(reason) = v.as_object_mut().unwrap().remove("reason") {
                assert!(!reason.as_str().unwrap().is_empty());
            }
            v
        })
        .collect();
    assert_eq!(Value::Array(problems), want["problems"]);
    let pdf = "9b101e6bb96ee2bf1c988c1a42aae95917d261999d9ba33388b403a385fb05f1";
    assert_eq!(scan.used_by("no such folder".into(), pdf.into()), Ok(None));
    let smith = "2024 Smith \u{2013} Metformin and cognition (PMID 38012345)";
    // A mis-stored digest of a file two installations use must not read as
    // "nobody uses it".
    for bad in [pdf.to_uppercase(), format!(" {}", &pdf[1..])] {
        assert_eq!(
            scan.used_by(smith.into(), bad.clone()),
            Err(LibraryError::BadIdentifier {
                field: "sha256".into(),
                value: bad,
            })
        );
    }
    let _ = std::fs::remove_dir_all(base);
}

#[test]
fn every_problem_variant_decides_whether_uses_are_complete() {
    use lib::Problem as P;
    let problems = every_core_problem();
    let uncounted: Vec<Option<&str>> = problems.iter().map(uncounted_record).collect();
    // In `every_core_problem`'s order; its `LeftoverTemp` is outside `records/`.
    let a = Some("folder-a");
    assert_eq!(uncounted, [a, a, a, a, None, a, None, None, None, None]);
    let temp = |name: &str| P::LeftoverTemp {
        folder: "f".into(),
        name: name.into(),
    };
    assert_eq!(
        uncounted_record(&temp("records/.a-b.json.x.tmp")),
        Some("f")
    );
    assert_eq!(uncounted_record(&temp("recordsx/.a.tmp")), None);
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
        let got = scan.find_digest(sha.clone(), None);
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
            scan.find_digest(bad, None),
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
            PaperLibrary::open(root.display().to_string()),
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
    let library = PaperLibrary::open(base.display().to_string()).unwrap();
    let (sa, mfc) = (who("studyanalyzer"), who("medicalfactchecker"));
    // Spelled out, so `record.installation == sa` below is not one conversion
    // checked against itself.
    assert_eq!(
        sa,
        Installation {
            app: "studyanalyzer".into(),
            id: "aaaa".into(),
            label: "Test Mac".into(),
        }
    );
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
        scan.find_digest(saved.sha256.clone(), None),
        Ok(Some(saved.clone()))
    );
    // The kind goes in: the bytes are kept as a PDF only.
    assert_eq!(
        scan.find_digest(saved.sha256.clone(), Some(SourceKind::Pdf)),
        Ok(Some(saved.clone()))
    );
    assert_eq!(
        scan.find_digest(saved.sha256.clone(), Some(SourceKind::Text)),
        Ok(None)
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
    let users = |l: &PaperLibrary| {
        l.scan()
            .unwrap()
            .used_by(saved.folder.clone(), saved.sha256.clone())
            .unwrap()
            .unwrap()
    };
    let complete = |installations| FileUsers {
        installations,
        complete: true,
    };
    assert_eq!(users(&library), complete(vec![mfc.clone(), sa.clone()]));
    // A mis-stored digest is refused, not "released" while the record keeps it.
    let mut upper = saved.clone();
    upper.sha256 = upper.sha256.to_uppercase();
    assert!(matches!(
        library.release(mfc.clone(), upper),
        Err(LibraryError::BadIdentifier { ref field, .. }) if field == "sha256"
    ));
    assert_eq!(users(&library), complete(vec![mfc.clone(), sa.clone()]));
    library.release(mfc, saved.clone()).unwrap();
    assert_eq!(users(&library), complete(vec![sa]));

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
fn another_apps_jats_file_can_be_used() {
    // `record_use` checks the name against the kind, so this pins the kind
    // going in for a kind other than the round trip's PDF.
    let (base, library) = sample("use-jats");
    let jats = "354be1cb35ec8788619e2bc3dba7943f8d722b85a3d684e09b0a1eea539e8cca";
    let file = library
        .scan()
        .unwrap()
        .find_digest(jats.into(), None)
        .unwrap()
        .unwrap();
    assert_eq!(file.kind, SourceKind::Jats);
    let mut info = paper("38012345");
    info.ids.doi = Some("10.1/x".into());
    library.record_use(who("x"), info, file.clone()).unwrap();
    let users = library
        .scan()
        .unwrap()
        .used_by(file.folder, jats.into())
        .unwrap()
        .unwrap();
    assert!(users.installations.contains(&who("x")));
    let _ = std::fs::remove_dir_all(base);
}

#[test]
fn a_refused_save_writes_nothing() {
    let base = scratch("refused");
    let library = PaperLibrary::open(base.display().to_string()).unwrap();
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
    // A JATS naming another paper (#766): its own PMID is 2, the paper's 1.
    let jats = br#"<article><front><article-meta><article-id pub-id-type="pmid">2</article-id></article-meta></front></article>"#;
    let got = library.save(
        who("studyanalyzer"),
        paper("1"),
        jats.to_vec(),
        SourceKind::Jats,
        String::new(),
    );
    assert!(
        matches!(got, Err(LibraryError::ForeignJats { folder: None, ref field, ref theirs, ref ours, .. })
            if field == "pmid" && theirs == "2" && ours == "1"),
        "{got:?}"
    );
    assert!(library.scan().unwrap().folders().is_empty());
    let _ = std::fs::remove_dir_all(base);
}

#[test]
fn library_ids_match_the_fixture() {
    // #784: the service's `library_ids` fixture, through the FFI.
    let root: Value = serde_json::from_str(include_str!(
        "../../../tests/fixtures/fulltext_library_ids.json"
    ))
    .unwrap();
    for case in root["cases"].as_array().unwrap() {
        let got = library_ids(opt(case, "pmid"), opt(case, "doi"), opt(case, "pmc_id"));
        let want = &case["ids"];
        assert_eq!(got.ids.pmid, opt(want, "pmid"), "{}", case["name"]);
        assert_eq!(got.ids.doi, opt(want, "doi"), "{}", case["name"]);
        assert_eq!(got.ids.pmcid, opt(want, "pmcid"), "{}", case["name"]);
        let dropped: Vec<String> = serde_json::from_value(case["dropped"].clone()).unwrap();
        assert_eq!(got.dropped, dropped, "{}", case["name"]);
    }
}

#[test]
fn newest_source_and_lists_use_follow_the_service() {
    // #784: what an app's own lookup chain reads by.
    let base = scratch("newest");
    let library = PaperLibrary::open(base.display().to_string()).unwrap();
    let (mfc, sa) = (who("medicalfactchecker"), who("studyanalyzer"));
    let saved = library
        .save(
            sa.clone(),
            paper("7"),
            b"%PDF-1".to_vec(),
            SourceKind::Pdf,
            "t".into(),
        )
        .unwrap();
    let scan = library.scan().unwrap();
    let ids = paper("7").ids;
    assert_eq!(
        scan.newest_source(ids.clone(), SourceKind::Pdf).unwrap(),
        Some(saved.clone())
    );
    assert_eq!(
        scan.newest_source(ids.clone(), SourceKind::Jats).unwrap(),
        None
    );
    // Another paper's request is served nothing from this paper's record.
    let other = PaperIds {
        pmid: Some("7".into()),
        doi: Some("10.9/other".into()),
        pmcid: None,
    };
    let mut known = paper("7");
    known.ids.doi = Some("10.1/mine".into());
    library
        .record_use(sa.clone(), known, saved.clone())
        .unwrap();
    let scan = library.scan().unwrap();
    assert_eq!(scan.newest_source(other, SourceKind::Pdf).unwrap(), None);
    assert!(scan.lists_use(sa, saved.clone()).unwrap());
    assert!(!scan.lists_use(mfc.clone(), saved.clone()).unwrap());
    let bad = FileRef {
        sha256: "ABC".into(),
        ..saved
    };
    assert_eq!(
        error_field(&scan.lists_use(mfc, bad).unwrap_err()),
        "sha256"
    );
    let bad_ids = PaperIds {
        pmid: Some("x".into()),
        doi: None,
        pmcid: None,
    };
    assert_eq!(
        error_field(&scan.newest_source(bad_ids, SourceKind::Pdf).unwrap_err()),
        "pmid"
    );
}

#[test]
fn paper_ids_contradiction_names_the_field() {
    let ids = |pmid: &str, doi: Option<&str>| PaperIds {
        pmid: Some(pmid.into()),
        doi: doi.map(str::to_owned),
        pmcid: None,
    };
    assert_eq!(
        paper_ids_contradiction(ids("01", Some("10.1/A")), ids("1", Some("10.1/b"))).unwrap(),
        Some(IdContradiction {
            field: "doi".into(),
            theirs: "10.1/a".into(),
            ours: "10.1/b".into(),
        })
    );
    assert_eq!(
        paper_ids_contradiction(ids("1", None), ids("1", Some("10.1/b"))).unwrap(),
        None
    );
    assert_eq!(
        error_field(&paper_ids_contradiction(ids("x", None), ids("1", None)).unwrap_err()),
        "pmid"
    );
}

/// Every file and folder under `root` (folders with a trailing slash), sorted.
fn tree(root: &Path) -> Vec<String> {
    fn walk(root: &Path, dir: &Path, out: &mut Vec<String>) {
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

/// A scenario step's paper, its identifiers raw: the FFI canonicalises them.
fn step_paper(p: &Value) -> PaperInfo {
    PaperInfo {
        ids: PaperIds {
            pmid: opt(p, "pmid"),
            doi: opt(p, "doi"),
            pmcid: opt(p, "pmcid"),
        },
        title: opt(p, "title"),
        year: p["year"].as_i64().map(|y| i32::try_from(y).unwrap()),
        first_author: opt(p, "first_author"),
    }
}

/// A scenario step's installation.
fn step_who(step: &Value) -> Installation {
    installation_new(
        step["app"].as_str().unwrap().into(),
        step["id"].as_str().unwrap().into(),
        "Test Mac".into(),
    )
    .unwrap()
}

/// `got`'s value, or, when `step` expects an error, the check that it is that one.
/// An `io` error names its `path` relative to the library `root`; its reason is
/// not compared.
fn step_refused<T>(
    name: &str,
    step: &Value,
    root: &str,
    got: Result<T, LibraryError>,
) -> Option<T> {
    let want = &step["expect"];
    if !want.is_object() {
        assert!(want.is_null(), "{name}: unknown expect {want}");
        return Some(got.unwrap_or_else(|e| panic!("{name}: {e}")));
    }
    let got = match got {
        Err(LibraryError::ForeignJats {
            folder,
            name: file,
            field,
            theirs,
            ours,
        }) => json!({
            "error": "foreign_jats", "folder": folder, "name": file, "field": field,
            "theirs": theirs, "ours": ours,
        }),
        Err(LibraryError::FileClash { folder, name: file }) => {
            json!({"error": "file_clash", "folder": folder, "name": file})
        }
        Err(LibraryError::BadName { name: refused }) => {
            json!({"error": "bad_name", "name": refused})
        }
        Err(LibraryError::Io { path, .. }) => {
            let rel = Path::new(&path)
                .strip_prefix(root)
                .unwrap_or(Path::new(&path));
            json!({"error": "io", "path": rel.to_string_lossy().replace('\\', "/")})
        }
        Err(e) => panic!("{name}: {e}"),
        Ok(_) => panic!("{name}: not refused"),
    };
    assert_eq!(&got, want, "{name}");
    None
}

#[test]
fn save_scenarios_match_the_fixture() {
    // #765: the fixture's saves, uses, releases and removals, through the FFI,
    // checked by the files on disk and every record's JSON.
    let cases = section("save_scenarios");
    assert!(!cases.is_empty());
    for case in cases {
        let name = case["name"].as_str().unwrap();
        let base = scratch(&format!(
            "save-{}",
            name.replace(|c: char| !c.is_ascii_alphanumeric(), "_")
        ));
        let library = PaperLibrary::open(base.display().to_string()).unwrap();
        let mut saved: Vec<Option<FileRef>> = Vec::new();
        for step in case["steps"].as_array().unwrap() {
            let of = |s: &Value| saved[s["of"].as_u64().unwrap() as usize].clone().unwrap();
            let mut made = None;
            match step["op"].as_str().unwrap() {
                "save" => {
                    made = step_refused(
                        name,
                        step,
                        &library.root(),
                        library.save(
                            step_who(step),
                            step_paper(&step["paper"]),
                            step["text"].as_str().unwrap().as_bytes().to_vec(),
                            kind(step["kind"].as_str().unwrap()),
                            step["origin"].as_str().unwrap().into(),
                        ),
                    );
                }
                "plant" => {
                    let path = base.join(step["path"].as_str().unwrap());
                    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
                    std::fs::write(path, step["text"].as_str().unwrap()).unwrap();
                }
                "scan" => {
                    let scan = library.scan().unwrap();
                    let folders: Vec<String> =
                        scan.folders().into_iter().map(|f| f.folder).collect();
                    assert_eq!(json!(folders), step["expect"]["folders"], "{name}");
                    let problems: Vec<Value> = scan.problems().iter().map(problem_json).collect();
                    assert_eq!(Value::Array(problems), step["expect"]["problems"], "{name}");
                }
                "release" => library.release(step_who(step), of(step)).unwrap(),
                "record_use" => {
                    let got =
                        library.record_use(step_who(step), step_paper(&step["paper"]), of(step));
                    step_refused(name, step, &library.root(), got);
                }
                "remove_file" => {
                    let got = library.remove_file(of(step));
                    if step["expect"] == "missing" {
                        assert!(matches!(got, Err(LibraryError::Missing { .. })), "{name}");
                    } else {
                        got.unwrap();
                    }
                }
                "remove_folder" => {
                    let got = library.remove_folder(step["folder"].as_str().unwrap().into());
                    if step["expect"] == "bad_name" {
                        assert!(matches!(got, Err(LibraryError::BadName { .. })), "{name}");
                    } else if step["expect"] == "missing" {
                        assert!(matches!(got, Err(LibraryError::Missing { .. })), "{name}");
                    } else {
                        got.unwrap();
                    }
                }
                "read" => {
                    assert_eq!(step["expect"], "missing", "{name}");
                    assert!(
                        matches!(library.read(of(step)), Err(LibraryError::Missing { .. })),
                        "{name}"
                    );
                }
                "used_by" => {
                    let file = of(step);
                    let users = library
                        .scan()
                        .unwrap()
                        .used_by(file.folder.clone(), file.sha256.clone())
                        .unwrap()
                        .unwrap_or_else(|| panic!("{name}: no folder {}", file.folder));
                    assert!(users.complete, "{name}");
                    let mut got: Vec<String> = users
                        .installations
                        .iter()
                        .map(|i| format!("{}-{}", i.app, i.id))
                        .collect();
                    got.sort();
                    assert_eq!(json!(got), step["expect"], "{name}");
                }
                "find_digest" => {
                    let want = step["expect"]
                        .as_u64()
                        .map(|i| saved[i as usize].clone().unwrap());
                    let got = library
                        .scan()
                        .unwrap()
                        .find_digest(of(step).sha256, step["kind"].as_str().map(kind))
                        .unwrap();
                    assert_eq!(got, want, "{name}");
                }
                other => panic!("unknown step {other}"),
            }
            saved.push(made);
        }
        let want = &case["expect"];
        assert_eq!(json!(tree(&base)), want["paths"], "{name}");
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
    let base = scratch("component");
    let library = PaperLibrary::open(base.display().to_string()).unwrap();
    std::fs::create_dir_all(base.join("papers/x")).unwrap();
    std::fs::write(base.join("papers/x/keep"), "planted").unwrap();
    let before = tree(&base);
    let cases = section("component");
    assert!(!cases.is_empty());
    for case in cases {
        let (name, value) = (&case["name"], case["value"].as_str().unwrap());
        let file = FileRef {
            folder: value.into(),
            name: "keep".into(),
            sha256: lib::sha256_hex(b"planted"),
            kind: SourceKind::Text,
        };
        let got = (
            library.remove_folder(value.into()),
            library.read(file).map(|_| ()),
        );
        let refused = match case["expect"].as_str().unwrap() {
            "bad_name" => matches!(
                got,
                (
                    Err(LibraryError::BadName { .. }),
                    Err(LibraryError::BadName { .. })
                )
            ),
            "ok" => matches!(
                got,
                (
                    Err(LibraryError::Missing { .. }),
                    Err(LibraryError::Missing { .. })
                )
            ),
            other => panic!("{name}: unknown expect {other}"),
        };
        assert!(refused, "{name}: {got:?}");
    }
    assert_eq!(tree(&base), before);
    let _ = std::fs::remove_dir_all(&base);
}

/// Every entry under `dir` with its bytes (`None` for a folder).
#[cfg(unix)]
fn outside(dir: &Path) -> std::collections::BTreeMap<String, Option<Vec<u8>>> {
    tree(dir)
        .into_iter()
        .map(|rel| {
            let bytes = (!rel.ends_with('/')).then(|| std::fs::read(dir.join(&rel)).unwrap());
            (rel, bytes)
        })
        .collect()
}

/// One paper is saved, then one of its entries is replaced by a FIFO, a file,
/// or a link to a copy outside the library (#761). Unix only: Windows needs
/// Developer Mode for a link and has no FIFOs.
#[cfg(unix)]
#[test]
fn special_entries_match_the_fixture() {
    let cases = section("special_entries");
    assert!(!cases.is_empty());
    for case in cases {
        let name = case["name"].as_str().unwrap();
        let base = scratch(&format!(
            "special-{}",
            name.replace(|c: char| !c.is_ascii_alphanumeric(), "_")
        ));
        let (root, out) = (base.join("lib"), base.join("outside"));
        std::fs::create_dir_all(&root).unwrap();
        std::fs::create_dir_all(&out).unwrap();
        let library = PaperLibrary::open(root.display().to_string()).unwrap();
        let sa = who("studyanalyzer");
        let mfc = installation_new(
            "medicalfactchecker".into(),
            "bbbb".into(),
            "Test Mac".into(),
        )
        .unwrap();
        let paper = paper("111");
        let save = || {
            library.save(
                sa.clone(),
                paper.clone(),
                b"pdf bytes one".to_vec(),
                SourceKind::Pdf,
                "unit test".into(),
            )
        };
        let file = save().unwrap();
        let target = root.join(case["replace"].as_str().unwrap());
        match case["with"].as_str().unwrap() {
            with @ ("symlink" | "dangling_symlink") => {
                let moved = out.join(target.file_name().unwrap());
                std::fs::rename(&target, &moved).unwrap();
                std::os::unix::fs::symlink(&moved, &target).unwrap();
                if with == "dangling_symlink" {
                    std::fs::remove_dir_all(&moved).unwrap();
                }
            }
            "file" => {
                std::fs::remove_dir_all(&target).unwrap();
                std::fs::write(&target, b"not a folder").unwrap();
            }
            "fifo" => {
                std::fs::remove_file(&target).unwrap();
                let made = std::process::Command::new("mkfifo")
                    .arg(&target)
                    .status()
                    .unwrap();
                assert!(made.success(), "{name}: mkfifo");
            }
            other => panic!("{name}: unknown entry {other}"),
        }
        let before = outside(&out);
        let want = &case["expect"];
        let got: Result<Option<String>, LibraryError> = match case["op"].as_str().unwrap() {
            "scan" => {
                let scan = library.scan().unwrap();
                let folders: Vec<String> = scan.folders().into_iter().map(|f| f.folder).collect();
                assert_eq!(json!(folders), want["folders"], "{name}");
                let problems: Vec<Value> = scan.problems().iter().map(problem_json).collect();
                assert_eq!(Value::Array(problems), want["problems"], "{name}");
                Ok(None)
            }
            "read" => library.read(file.clone()).map(|_| None),
            "record_use" => library
                .record_use(mfc.clone(), paper.clone(), file.clone())
                .map(|()| None),
            "save" => save().map(|f| Some(f.folder)),
            "release" => library.release(sa.clone(), file.clone()).map(|()| None),
            "remove_file" => library.remove_file(file.clone()).map(|()| None),
            "remove_folder" => library.remove_folder(file.folder.clone()).map(|()| None),
            "open" => PaperLibrary::open(root.display().to_string()).map(|_| None),
            other => panic!("{name}: unknown op {other}"),
        };
        match (want["error"].as_str(), got) {
            (Some("io"), Err(LibraryError::Io { reason, .. })) => {
                if let Some(part) = want["reason"].as_str() {
                    assert!(reason.contains(part), "{name}: {reason}");
                }
            }
            (Some("missing"), Err(LibraryError::Missing { .. })) => {}
            (None, Ok(folder)) => assert_eq!(folder.as_deref(), want["folder"].as_str(), "{name}"),
            (_, got) => panic!("{name}: got {got:?}"),
        }
        assert_eq!(outside(&out), before, "{name}");
        let _ = std::fs::remove_dir_all(&base);
    }
}
