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

//! The shared `FullTextResult::jats_xml` and `pdf_download_failure` contract —
//! the same file Python's `tests/test_fulltext_source_contract.py` asserts
//! against: the service hands back the JATS it rendered, byte for byte as
//! served, and the cache keeps it beside the HTML; a PDF download that leaves
//! no file says why (#594).

use std::collections::VecDeque;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use bmlib::fulltext::{
    sanitize_identifier, FullTextCache, FullTextRequest, FullTextResult, FullTextService,
    FullTextSourceEntry,
};
use bmlib::publications::fetchers::{FetchError, HttpClient, HttpResponse};
use serde_json::Value;

const FIXTURE: &str = include_str!("../../../tests/fixtures/fulltext_source.json");

/// The directory `body_file` names are relative to.
fn fixtures_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../tests/fixtures")
}

/// The bytes of fixture file `name`, or `None` for a JSON null.
fn body(name: &Value) -> Option<Vec<u8>> {
    name.as_str().map(|name| {
        std::fs::read(fixtures_dir().join(name)).unwrap_or_else(|e| panic!("{name}: {e}"))
    })
}

/// A transport answering a script in order and counting what it was asked.
struct ScriptedClient {
    script: Mutex<VecDeque<Result<HttpResponse, FetchError>>>,
    asked: Mutex<Vec<String>>,
}

impl HttpClient for ScriptedClient {
    fn get(&self, url: &str) -> Result<HttpResponse, FetchError> {
        self.asked.lock().expect("lock").push(url.to_string());
        self.script
            .lock()
            .expect("lock")
            .pop_front()
            .unwrap_or_else(|| {
                Err(FetchError::Transport(format!(
                    "transport exhausted at {url}"
                )))
            })
    }
}

/// A temporary cache directory that cleans up after itself.
struct TempDir(PathBuf);

impl TempDir {
    fn new(label: &str) -> Self {
        let path = std::env::temp_dir().join(format!(
            "bmlib-fulltext-source-{label}-{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&path);
        // Only the root: the cache's saves create `html/` and `pdfs/` themselves.
        std::fs::create_dir_all(&path).expect("cache dir");
        TempDir(path)
    }
}

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

/// Run `fetch_fulltext` for `request` against a transport answering `answers`
/// in order, and assert every answer was asked for and no more.
fn fetch(cache: &FullTextCache, name: &str, request: &Value, answers: &[Value]) -> FullTextResult {
    let script = answers
        .iter()
        .map(|answer| {
            if answer.get("raise").is_some() {
                return Err(FetchError::Transport("scripted transport failure".into()));
            }
            let status = u16::try_from(answer["status"].as_u64().expect("status")).expect("u16");
            Ok(HttpResponse::from_bytes(
                status,
                body(&answer["body_file"]).unwrap_or_default(),
            ))
        })
        .collect();
    let client = Arc::new(ScriptedClient {
        script: Mutex::new(script),
        asked: Mutex::new(Vec::new()),
    });
    let service =
        FullTextService::new(client.clone(), "test@example.com").with_cache(Some(cache.clone()));
    let fulltext_sources: Vec<FullTextSourceEntry> = request
        .get("fulltext_sources")
        .map(|s| serde_json::from_value(s.clone()).expect("sources"))
        .unwrap_or_default();
    let request = FullTextRequest {
        fulltext_sources,
        pmc_id: request["pmc_id"].as_str().map(str::to_string),
        identifier: request["identifier"].as_str().map(str::to_string),
        ..FullTextRequest::default()
    };
    let result = service
        .fetch_fulltext(&request)
        .unwrap_or_else(|e| panic!("{name}: {e}"));
    let asked = client.asked.lock().expect("lock").clone();
    assert_eq!(asked.len(), answers.len(), "{name}: asked {asked:?}");
    result
}

/// Assert `result` carries what the contract's `expected` block names.
fn check(name: &str, result: &FullTextResult, expected: &Value) {
    assert_eq!(
        result.source,
        expected["source"].as_str().unwrap(),
        "{name}"
    );
    assert_eq!(
        result.content_kind.as_str(),
        expected["content_kind"].as_str().unwrap(),
        "{name}"
    );
    assert_eq!(result.jats_xml, body(&expected["jats_xml"]), "{name}");
    assert_eq!(
        result.pdf_download_failure.as_deref(),
        expected["pdf_download_failure"].as_str(),
        "{name}"
    );
    assert_eq!(
        result.file_path.is_some(),
        expected["pdf_cached"].as_bool().unwrap_or(false),
        "{name}"
    );
}

#[test]
fn contract_case() {
    let fixture: Value = serde_json::from_str(FIXTURE).expect("the fixture is JSON");
    for case in fixture["cases"].as_array().expect("cases") {
        let name = case["name"].as_str().unwrap();
        let dir = TempDir::new(name);
        let cache = FullTextCache::new(Some(dir.0.clone())).expect("an explicit directory");
        let key = sanitize_identifier(case["request"]["identifier"].as_str().unwrap_or(""));
        let jats_entry = cache.jats_dir().join(format!("{key}.xml"));
        if let Some(html) = case["seed_html"].as_str() {
            cache.save_html(html, &key).expect("seeded");
        }
        if case["seed_unreadable_jats"].as_bool() == Some(true) {
            std::fs::create_dir_all(&jats_entry).expect("an unreadable entry");
        }
        if case["seed_pdfs_dir_as_file"].as_bool() == Some(true) {
            std::fs::write(cache.pdf_dir(), b"").expect("a file where pdfs/ belongs");
        }
        let answers = case["responses"].as_array().expect("responses");
        check(
            name,
            &fetch(&cache, name, &case["request"], answers),
            &case["expected"],
        );
        if case["moved_aside"].as_bool() == Some(true) {
            assert!(!jats_entry.exists(), "{name}");
            assert!(
                cache.jats_dir().join(format!("{key}.xml.corrupt")).exists(),
                "{name}"
            );
        }
        if let Some(then) = case.get("then") {
            check(name, &fetch(&cache, name, &case["request"], &[]), then);
        }
    }
}
