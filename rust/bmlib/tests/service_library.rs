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

//! The full-text service reading the shared library before its cache and its
//! chain, and keeping what the chain downloads in it (design §4.5). Python's
//! `tests/test_fulltext_service_library.py` runs the same cases and the same
//! shared scenarios (`tests/fixtures/fulltext_library_scenarios.json`).

#[path = "common/scripted_http.rs"]
mod scripted_http;

use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use bmlib::fulltext::jats_reader::parse;
use bmlib::fulltext::{
    paper_info, sanitize_identifier, FullTextCache, FullTextRequest, FullTextService, LibraryStore,
    LIBRARY_SOURCE,
};
use bmlib::library::{FileRef, Installation, Library, PaperIds, PaperInfo, SourceKind};
use bmlib::publications::fetchers::HttpResponse;
use scripted_http::ScriptedClient;

const SAMPLE: &str = include_str!("../../../tests/fixtures/sample_article.xml");
/// The sample's own PMID.
const SAMPLE_PMID: &str = "34567890";
/// Part of the sample's title, which its rendering carries.
const SAMPLE_TITLE: &str = "Effects of Novel Compound X";

fn me() -> Installation {
    Installation::new("studyanalyzer", "aaaa", "Mac").unwrap()
}

fn other() -> Installation {
    Installation::new("medicalfactchecker", "bbbb", "Mac").unwrap()
}

/// A fresh directory for one test, holding a library at `lib` and a cache at
/// `cache`.
fn scratch(tag: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("bmlib-svclib-{tag}-{}", std::process::id()));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(dir.join("lib")).unwrap();
    dir
}

/// The sample JATS's paper as another app would save it, under `ids`.
fn sample_info(ids: PaperIds) -> PaperInfo {
    paper_info(ids, Some(&parse(SAMPLE).unwrap()))
}

fn pmid_ids(pmid: &str) -> PaperIds {
    PaperIds::canonical(Some(pmid), None, None).unwrap()
}

/// Another installation saves `bytes` of `kind` for `paper` in the library at `dir/lib`.
fn saved_by_other(dir: &Path, paper: &PaperInfo, bytes: &[u8], kind: SourceKind) -> FileRef {
    let lib = Library::open(&dir.join("lib")).unwrap();
    lib.save(&other(), paper, bytes, kind, "test").unwrap()
}

/// A service over `client` reading the library at `dir/lib` as [`me`].
fn service(dir: &Path, client: Arc<ScriptedClient>) -> FullTextService {
    FullTextService::new(client, "test@example.com")
        .with_library(Library::open(&dir.join("lib")).unwrap(), me())
}

fn by_pmid(pmid: &str) -> FullTextRequest {
    FullTextRequest {
        pmid: pmid.into(),
        ..FullTextRequest::default()
    }
}

#[test]
fn jats_in_library_ends_the_lookup() {
    let dir = scratch("jats");
    let jats = saved_by_other(
        &dir,
        &sample_info(pmid_ids(SAMPLE_PMID)),
        SAMPLE.as_bytes(),
        SourceKind::Jats,
    );
    let client = ScriptedClient::new(vec![]);
    let svc = service(&dir, client.clone());
    let result = svc.fetch_fulltext(&by_pmid(SAMPLE_PMID)).unwrap();
    assert_eq!(client.call_count(), 0, "{:?}", client.requests());
    assert_eq!(result.source, LIBRARY_SOURCE);
    assert!(result.html.as_deref().unwrap().contains(SAMPLE_TITLE));
    assert_eq!(result.jats_xml.as_deref(), Some(SAMPLE.as_bytes()));
    assert_eq!(result.library_files, vec![jats.clone()]);
    assert_eq!(result.library_failure, None);
    assert_eq!(result.file_path, None);
    // This installation's record now lists the JATS.
    let scan = svc.library().unwrap().library.scan().unwrap();
    let folder = scan
        .folders
        .iter()
        .find(|f| f.folder == jats.folder)
        .unwrap();
    assert!(folder
        .records
        .iter()
        .any(|r| r.installation.app == "studyanalyzer"
            && r.files.iter().any(|e| e.sha256 == jats.sha256)));
    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn jats_and_pdf_in_library() {
    let dir = scratch("pdf");
    let paper = sample_info(pmid_ids(SAMPLE_PMID));
    let jats = saved_by_other(&dir, &paper, SAMPLE.as_bytes(), SourceKind::Jats);
    let pdf = saved_by_other(&dir, &paper, b"%PDF-1.4 sample", SourceKind::Pdf);
    let client = ScriptedClient::new(vec![]);
    let svc = service(&dir, client.clone());
    let result = svc.fetch_fulltext(&by_pmid(SAMPLE_PMID)).unwrap();
    assert_eq!(client.call_count(), 0);
    assert_eq!(result.source, LIBRARY_SOURCE);
    let want = LibraryStore::new(Library::open(&dir.join("lib")).unwrap(), me())
        .path_of(&pdf)
        .unwrap();
    assert_eq!(result.file_path, Some(want.display().to_string()));
    assert_eq!(result.library_files, vec![jats, pdf]);
    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn tampered_jats_is_not_served() {
    let dir = scratch("tamper");
    let jats = saved_by_other(
        &dir,
        &sample_info(pmid_ids(SAMPLE_PMID)),
        SAMPLE.as_bytes(),
        SourceKind::Jats,
    );
    let store = LibraryStore::new(Library::open(&dir.join("lib")).unwrap(), me());
    fs::write(store.path_of(&jats).unwrap(), b"<article/>").unwrap();
    // Europe PMC's XML for the PMC ID is the chain's first request.
    let client = ScriptedClient::new(vec![Ok(HttpResponse::ok_text(SAMPLE))]);
    let svc = service(&dir, client.clone());
    let request = FullTextRequest {
        pmc_id: Some("PMC7614751".into()),
        ..by_pmid(SAMPLE_PMID)
    };
    let result = svc.fetch_fulltext(&request).unwrap();
    assert_eq!(result.source, "europepmc");
    assert_eq!(client.call_count(), 1);
    assert!(
        result
            .library_failure
            .as_deref()
            .unwrap()
            .contains("digest"),
        "{:?}",
        result.library_failure
    );
    assert!(result.library_files.is_empty());
    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn jats_of_another_paper_is_not_served() {
    let dir = scratch("other");
    // The record claims PMID 999; the JATS itself says 34567890.
    saved_by_other(
        &dir,
        &sample_info(pmid_ids("999")),
        SAMPLE.as_bytes(),
        SourceKind::Jats,
    );
    let client = ScriptedClient::always(HttpResponse::from_bytes(404, Vec::new()));
    let svc = service(&dir, client.clone());
    let result = svc.fetch_fulltext(&by_pmid("999")).unwrap();
    assert!(client.call_count() > 0, "the chain ran");
    assert_ne!(result.source, LIBRARY_SOURCE);
    // Another paper's JATS is the lookup's library failure: nothing is kept.
    let failure = result.library_failure.unwrap();
    assert!(
        failure.contains("999") && failure.contains(SAMPLE_PMID),
        "{failure}"
    );
    let named: Vec<String> = svc
        .warnings()
        .into_iter()
        .filter(|w| w.contains("999") && w.contains(SAMPLE_PMID))
        .collect();
    assert_eq!(named.len(), 1, "one warning: {:?}", svc.warnings());
    let _ = fs::remove_dir_all(&dir);
}

/// A cache at `dir/cache` holding HTML for `identifier`.
fn cache_with(dir: &Path, identifier: &str) -> FullTextCache {
    let cache = FullTextCache::new(Some(dir.join("cache"))).unwrap();
    cache
        .save_html("<h1>Cached</h1>", &sanitize_identifier(identifier))
        .unwrap();
    cache
}

#[test]
fn identifier_only_request_skips_the_library() {
    let dir = scratch("idonly");
    let cache = cache_with(&dir, "10.1/test");
    let client = ScriptedClient::new(vec![]);
    let svc = service(&dir, client.clone()).with_cache(Some(cache));
    // Unreachable: a library looked up would set library_failure.
    fs::remove_dir_all(dir.join("lib")).unwrap();
    let request = FullTextRequest {
        identifier: Some("10.1/test".into()),
        ..FullTextRequest::default()
    };
    let result = svc.fetch_fulltext(&request).unwrap();
    assert_eq!(result.source, "cached");
    assert_eq!(result.library_failure, None);
    assert_eq!(client.call_count(), 0);
    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn unreachable_library_falls_back_to_the_cache() {
    let dir = scratch("gone");
    let cache = cache_with(&dir, "10.1/test");
    let client = ScriptedClient::new(vec![]);
    let svc = service(&dir, client.clone()).with_cache(Some(cache));
    fs::remove_dir_all(dir.join("lib")).unwrap();
    let request = FullTextRequest {
        identifier: Some("10.1/test".into()),
        ..by_pmid(SAMPLE_PMID)
    };
    let result = svc.fetch_fulltext(&request).unwrap();
    assert_eq!(result.source, "cached");
    assert!(result.library_failure.is_some());
    assert_eq!(client.call_count(), 0);
    assert!(
        svc.warnings().iter().any(|w| w.contains("library")),
        "{:?}",
        svc.warnings()
    );
    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn a_pmc_id_given_as_the_pmid_reads_the_library() {
    let dir = scratch("pmcpmid");
    let ids = PaperIds::canonical(None, None, Some("PMC7614751")).unwrap();
    saved_by_other(&dir, &sample_info(ids), SAMPLE.as_bytes(), SourceKind::Jats);
    let client = ScriptedClient::new(vec![]);
    let svc = service(&dir, client.clone());
    let result = svc.fetch_fulltext(&by_pmid(" PMC7614751 ")).unwrap();
    assert_eq!(client.call_count(), 0, "{:?}", client.requests());
    assert_eq!(result.source, LIBRARY_SOURCE);
    let _ = fs::remove_dir_all(&dir);
}

/// Whether `svc` logged that it holds a library PDF for the chain.
fn holds_a_library_pdf(svc: &FullTextService) -> bool {
    svc.log_lines()
        .iter()
        .any(|line| line.message.starts_with("Holding the library's PDF"))
}

#[test]
fn the_pdf_beside_a_jats_of_another_paper_is_not_held() {
    let dir = scratch("otherpdf");
    let paper = sample_info(pmid_ids("999"));
    saved_by_other(&dir, &paper, SAMPLE.as_bytes(), SourceKind::Jats);
    saved_by_other(&dir, &paper, b"%PDF-1.4 sample", SourceKind::Pdf);
    let client = ScriptedClient::always(HttpResponse::from_bytes(404, Vec::new()));
    let svc = service(&dir, client);
    let result = svc.fetch_fulltext(&by_pmid("999")).unwrap();
    assert_ne!(result.source, LIBRARY_SOURCE);
    assert!(!holds_a_library_pdf(&svc), "{:?}", svc.log_lines());
    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn a_library_pdf_without_jats_is_held_for_the_chain() {
    let dir = scratch("pdfonly");
    saved_by_other(
        &dir,
        &sample_info(pmid_ids("999")),
        b"%PDF-1.4 sample",
        SourceKind::Pdf,
    );
    let client = ScriptedClient::always(HttpResponse::from_bytes(404, Vec::new()));
    let svc = service(&dir, client);
    svc.fetch_fulltext(&by_pmid("999")).unwrap();
    assert!(holds_a_library_pdf(&svc), "{:?}", svc.log_lines());
    let _ = fs::remove_dir_all(&dir);
}

// ---------------------------------------------------------------------------
// The shared scenarios (tests/fixtures/fulltext_library_scenarios.json)
// ---------------------------------------------------------------------------

/// The contract fixtures' directory.
fn fixtures() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../tests/fixtures")
}

/// How many source files the library at `lib` holds, over all folders.
fn source_count(lib: &Path) -> usize {
    Library::open(lib)
        .unwrap()
        .scan()
        .unwrap()
        .folders
        .iter()
        .map(|f| f.files_on_disk.len())
        .sum()
}

/// Every directory under `root`, `root` included, set to `mode`; nothing
/// where permissions are not Unix modes.
fn chmod_dirs(root: &Path, mode: u32) {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if !root.is_dir() {
            return;
        }
        for entry in fs::read_dir(root).unwrap() {
            chmod_dirs(&entry.unwrap().path(), mode);
        }
        fs::set_permissions(root, fs::Permissions::from_mode(mode)).unwrap();
    }
    #[cfg(not(unix))]
    let _ = (root, mode);
}

/// Opens the library at its path up again when dropped, so a panic in the
/// lookup leaves no read-only scratch directory behind.
struct Unlock<'a>(&'a Path);

impl Drop for Unlock<'_> {
    fn drop(&mut self) {
        chmod_dirs(self.0, 0o755);
    }
}

/// Whether this process is refused a write into a read-only directory (not
/// root, and a filesystem that enforces permissions).
fn permissions_enforced(dir: &Path) -> bool {
    if !cfg!(unix) {
        return false;
    }
    let probe = dir.join("probe");
    fs::create_dir_all(&probe).unwrap();
    chmod_dirs(&probe, 0o555);
    let refused = fs::write(probe.join("x"), b"x").is_err();
    chmod_dirs(&probe, 0o755);
    let _ = fs::remove_dir_all(&probe);
    refused
}

/// A transport answering by URL pattern (the scenario's `network`), 404 for
/// any other URL, recording each URL. With `read_only` set, the library at its
/// path turns read-only at the first request after that many saves.
struct PatternClient {
    answers: Vec<(String, HttpResponse)>,
    requests: std::sync::Mutex<Vec<String>>,
    read_only: Option<(PathBuf, usize)>,
}

impl bmlib::publications::fetchers::HttpClient for PatternClient {
    fn get(&self, url: &str) -> Result<HttpResponse, bmlib::publications::fetchers::FetchError> {
        self.requests.lock().unwrap().push(url.to_string());
        if let Some((lib, after)) = &self.read_only {
            if source_count(lib) >= *after {
                chmod_dirs(lib, 0o555);
            }
        }
        Ok(self
            .answers
            .iter()
            .find(|(pattern, _)| url.contains(pattern.as_str()))
            .map_or_else(
                || HttpResponse::from_bytes(404, Vec::new()),
                |(_, response)| response.clone(),
            ))
    }
}

/// A JSON value as an optional string.
fn opt(value: &serde_json::Value) -> Option<String> {
    value.as_str().map(str::to_string)
}

/// Each folder's file kinds (sorted), the folders sorted, and the folder names.
fn folder_kinds(lib: &Path) -> (Vec<Vec<String>>, Vec<String>) {
    let scan = Library::open(lib).unwrap().scan().unwrap();
    let mut kinds: Vec<Vec<String>> = scan
        .folders
        .iter()
        .map(|folder| {
            let mut kinds: Vec<String> = folder
                .files_on_disk
                .iter()
                .filter_map(|name| {
                    folder
                        .records
                        .iter()
                        .flat_map(|r| &r.files)
                        .find(|e| &e.name == name)
                        .map(|e| e.kind.as_str().to_string())
                })
                .collect();
            kinds.sort();
            kinds
        })
        .collect();
    kinds.sort();
    (
        kinds,
        scan.folders.iter().map(|f| f.folder.clone()).collect(),
    )
}

/// Run one scenario and check its `expect`. Returns `false` when skipped.
fn run_scenario(scenario: &serde_json::Value, enforced: bool) -> bool {
    let name = scenario["name"].as_str().unwrap();
    let read_only_after = scenario["read_only_after"].as_u64().map(|n| n as usize);
    if read_only_after.is_some() && !enforced {
        eprintln!("skipped (permissions not enforced): {name}");
        return false;
    }
    let dir = scratch(&format!("scenario{}", &name[..1]));
    let lib = dir.join("lib");
    for saved in scenario["library"].as_array().unwrap() {
        let ids = &saved["ids"];
        let paper = PaperInfo {
            ids: PaperIds::canonical(
                ids["pmid"].as_str(),
                ids["doi"].as_str(),
                ids["pmc_id"].as_str(),
            )
            .unwrap(),
            ..PaperInfo::default()
        };
        let who = Installation::new(saved["app"].as_str().unwrap(), "bbbb", "Mac").unwrap();
        let kind = match saved["kind"].as_str().unwrap() {
            "pdf" => SourceKind::Pdf,
            "jats" => SourceKind::Jats,
            other => panic!("kind {other}"),
        };
        let bytes = fs::read(fixtures().join(saved["file"].as_str().unwrap())).unwrap();
        Library::open(&lib)
            .unwrap()
            .save(&who, &paper, &bytes, kind, "test")
            .unwrap();
    }
    let answers = scenario["network"]
        .as_array()
        .unwrap()
        .iter()
        .map(|answer| {
            let response = match answer["file"].as_str() {
                Some(file) => {
                    HttpResponse::from_bytes(200, fs::read(fixtures().join(file)).unwrap())
                }
                None => HttpResponse::from_bytes(answer["status"].as_u64().unwrap() as u16, vec![]),
            };
            (answer["pattern"].as_str().unwrap().to_string(), response)
        })
        .collect();
    let client = Arc::new(PatternClient {
        answers,
        requests: std::sync::Mutex::new(Vec::new()),
        read_only: read_only_after.map(|n| (lib.clone(), source_count(&lib) + n)),
    });
    let cache = FullTextCache::new(Some(dir.join("cache"))).unwrap();
    let svc = FullTextService::new(client.clone(), "test@example.com")
        .with_cache(Some(cache.clone()))
        .with_fetch_pdf_with_jats(
            scenario["service"]["fetch_pdf_with_jats"]
                .as_bool()
                .unwrap(),
        )
        .with_pdf_retry_delay(std::time::Duration::ZERO)
        .with_library(Library::open(&lib).unwrap(), me());
    let req = &scenario["request"];
    let request = FullTextRequest {
        pmid: opt(&req["pmid"]).unwrap_or_default(),
        doi: opt(&req["doi"]),
        pmc_id: opt(&req["pmc_id"]),
        identifier: opt(&req["identifier"]),
        fulltext_sources: req["fulltext_sources"]
            .as_array()
            .map(|entries| {
                entries
                    .iter()
                    .map(|e| serde_json::from_value(e.clone()).unwrap())
                    .collect()
            })
            .unwrap_or_default(),
    };
    let result = {
        let _unlock = Unlock(&lib);
        svc.fetch_fulltext(&request)
    };
    let result = result.unwrap_or_else(|e| panic!("{name}: {e}"));

    let want = &scenario["expect"];
    assert_eq!(result.source, want["source"].as_str().unwrap(), "{name}");
    assert_eq!(
        result.content_kind.as_str(),
        want["content_kind"].as_str().unwrap(),
        "{name}"
    );
    let kinds: Vec<&str> = result
        .library_files
        .iter()
        .map(|f| f.kind.as_str())
        .collect();
    let want_kinds: Vec<&str> = want["library_files"]
        .as_array()
        .unwrap()
        .iter()
        .map(|k| k.as_str().unwrap())
        .collect();
    assert_eq!(kinds, want_kinds, "{name}");
    match want["library_failure"].as_str() {
        None => assert_eq!(result.library_failure, None, "{name}"),
        Some(part) => assert!(
            result
                .library_failure
                .as_deref()
                .unwrap_or_default()
                .contains(part),
            "{name}: {:?}",
            result.library_failure
        ),
    }
    let requests = client.requests.lock().unwrap().clone();
    let patterns = want["requests"].as_array().unwrap();
    assert_eq!(requests.len(), patterns.len(), "{name}: {requests:?}");
    for (url, pattern) in requests.iter().zip(patterns) {
        assert!(
            url.contains(pattern.as_str().unwrap()),
            "{name}: {requests:?}"
        );
    }
    let (folders, names) = folder_kinds(&lib);
    let want_folders: Vec<Vec<String>> = serde_json::from_value(want["folders"].clone()).unwrap();
    assert_eq!(folders, want_folders, "{name}");
    if let Some(part) = want["folder_name_contains"].as_str() {
        assert!(names.iter().all(|n| n.contains(part)), "{name}: {names:?}");
    }
    let id = request.identifier.as_deref().map(sanitize_identifier);
    let mut entries = Vec::new();
    if let Some(id) = id.as_deref() {
        if cache.get_jats(id).unwrap().is_some() {
            entries.push("jats");
        }
        if cache.has_html(id) {
            entries.push("html");
        }
        if cache.get_pdf(id).is_some() {
            entries.push("pdf");
        }
    }
    let want_cache: Vec<&str> = want["cache"]
        .as_array()
        .unwrap()
        .iter()
        .map(|k| k.as_str().unwrap())
        .collect();
    assert_eq!(entries, want_cache, "{name}");
    let _ = fs::remove_dir_all(&dir);
    true
}

#[test]
fn library_scenarios() {
    let fixture: serde_json::Value = serde_json::from_slice(
        &fs::read(fixtures().join("fulltext_library_scenarios.json")).unwrap(),
    )
    .unwrap();
    let probe_dir = scratch("probe");
    let enforced = permissions_enforced(&probe_dir);
    let _ = fs::remove_dir_all(&probe_dir);
    let mut failed = Vec::new();
    for scenario in fixture["scenarios"].as_array().unwrap() {
        let name = scenario["name"].as_str().unwrap().to_string();
        if std::panic::catch_unwind(|| run_scenario(scenario, enforced)).is_err() {
            failed.push(name);
        }
    }
    assert!(failed.is_empty(), "failed: {failed:?}");
}

#[test]
fn a_library_jats_hit_seeks_no_pdf_after_a_final_failure() {
    let dir = scratch("seekonce");
    let other = fs::read(fixtures().join("fulltext_library_other_article.xml")).unwrap();
    saved_by_other(
        &dir,
        &paper_info(pmid_ids("999"), None),
        &other,
        SourceKind::Jats,
    );
    let answer =
        |file: &str| HttpResponse::from_bytes(200, fs::read(fixtures().join(file)).unwrap());
    // Anything else is a 404, a final failure.
    let client = Arc::new(PatternClient {
        answers: vec![
            (
                "/search?".into(),
                answer("europepmc_search_pmc123_pdf.json"),
            ),
            ("list-type=2".into(), answer("pmc_open_data_absent.xml")),
        ],
        requests: std::sync::Mutex::new(Vec::new()),
        read_only: None,
    });
    let svc = FullTextService::new(client.clone(), "test@example.com")
        .with_cache(FullTextCache::new(Some(dir.join("cache"))))
        .with_fetch_pdf_with_jats(true)
        .with_pdf_retry_delay(std::time::Duration::ZERO)
        .with_library(Library::open(&dir.join("lib")).unwrap(), me());
    let request = FullTextRequest {
        identifier: Some("t3".into()),
        ..by_pmid("999")
    };
    let first = svc.fetch_fulltext(&request).unwrap();
    assert_eq!(first.source, LIBRARY_SOURCE);
    assert_eq!(first.file_path, None);
    // The first hit seeks: the search, Open Data (absent), Europe PMC's PDF (404, final).
    let want = ["/search?", "list-type=2", "render/PMC123.pdf"];
    let requests = std::mem::take(&mut *client.requests.lock().unwrap());
    assert_eq!(requests.len(), want.len(), "{requests:?}");
    assert!(
        requests.iter().zip(want).all(|(u, p)| u.contains(p)),
        "{requests:?}"
    );
    let second = svc.fetch_fulltext(&request).unwrap();
    assert_eq!(second.source, LIBRARY_SOURCE);
    assert!(client.requests.lock().unwrap().is_empty());
    let _ = fs::remove_dir_all(&dir);
}
