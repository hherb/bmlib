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
//! chain (design §4.5). Python's `tests/test_fulltext_service_library.py` runs
//! the same six cases.

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
    assert_eq!(result.library_failure, None);
    assert!(
        svc.warnings()
            .iter()
            .any(|w| w.contains("999") && w.contains(SAMPLE_PMID)),
        "{:?}",
        svc.warnings()
    );
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
