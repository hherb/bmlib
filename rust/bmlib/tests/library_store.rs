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

//! The request's identifiers as library identifiers, from
//! `tests/fixtures/fulltext_library_ids.json` — the file Python's
//! `tests/test_fulltext_library_store.py` asserts against.

use bmlib::fulltext::library_ids;
use serde_json::Value;

const FIXTURE: &str = include_str!("../../../tests/fixtures/fulltext_library_ids.json");

#[test]
fn library_ids_match_the_fixture() {
    let root: Value = serde_json::from_str(FIXTURE).expect("fixture is JSON");
    for case in root["cases"].as_array().expect("cases") {
        let name = case["name"].as_str().unwrap();
        let (ids, dropped) = library_ids(
            case["pmid"].as_str().unwrap(),
            case["doi"].as_str(),
            case["pmc_id"].as_str(),
        );
        let got = serde_json::json!({"pmid": ids.pmid, "doi": ids.doi, "pmcid": ids.pmcid});
        assert_eq!(got, case["ids"], "{name}");
        let want: Vec<&str> = case["dropped"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_str().unwrap())
            .collect();
        assert_eq!(dropped, want, "{name}");
    }
}

// ---- Task 2: finding, keeping and noting sources ----

use std::fs;

use bmlib::fulltext::jats_reader::parse;
use bmlib::fulltext::{paper_info, LibraryStore};
use bmlib::library::{
    parse_record, render_record, Installation, Library, PaperIds, PaperInfo, SourceKind,
};

const SAMPLE: &str = include_str!("../../../tests/fixtures/sample_article.xml");

fn me() -> Installation {
    Installation::new("studyanalyzer", "aaaa", "Mac").unwrap()
}

fn scratch(tag: &str) -> (std::path::PathBuf, LibraryStore) {
    let dir = std::env::temp_dir().join(format!("bmlib-store-{tag}-{}", std::process::id()));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();
    let store = LibraryStore::new(Library::open(&dir).unwrap(), me());
    (dir, store)
}

fn info(pmid: Option<&str>, doi: Option<&str>) -> PaperInfo {
    PaperInfo {
        ids: PaperIds::canonical(pmid, doi, None).unwrap(),
        title: Some("A title".into()),
        ..PaperInfo::default()
    }
}

#[test]
fn find_source_is_none_for_an_unknown_paper() {
    let (_d, store) = scratch("unknown");
    let ids = PaperIds::canonical(Some("1"), None, None).unwrap();
    assert!(store.find_source(&ids, SourceKind::Jats).unwrap().is_none());
}

#[test]
fn keep_then_find_source_round_trips_by_doi_and_by_linked_pmid() {
    let (_d, store) = scratch("round");
    let by_doi = info(None, Some("10.1/x"));
    store.keep(&by_doi, b"<a/>", SourceKind::Jats, "t").unwrap();
    // A second save links the PMID to the same folder.
    store
        .keep(
            &info(Some("5"), Some("10.1/x")),
            b"%PDF-1",
            SourceKind::Pdf,
            "t",
        )
        .unwrap();
    let held = store
        .find_source(&by_doi.ids, SourceKind::Jats)
        .unwrap()
        .unwrap();
    assert_eq!(held.bytes, b"<a/>");
    let by_pmid = PaperIds::canonical(Some("5"), None, None).unwrap();
    let held = store
        .find_source(&by_pmid, SourceKind::Jats)
        .unwrap()
        .unwrap();
    assert_eq!(held.bytes, b"<a/>");
    assert!(store
        .find_source(&by_pmid, SourceKind::Html)
        .unwrap()
        .is_none());
}

#[test]
fn find_source_returns_the_later_of_two_pdfs() {
    let (dir, store) = scratch("later");
    let paper = info(Some("7"), None);
    let old = store
        .keep(&paper, b"%PDF-old", SourceKind::Pdf, "t")
        .unwrap();
    let new = store
        .keep(&paper, b"%PDF-new", SourceKind::Pdf, "t")
        .unwrap();
    // Two saves in one second tie on the clock, so set the times by hand.
    let path = dir
        .join("papers")
        .join(&new.folder)
        .join("records")
        .join(me().record_file_name());
    let mut rec = parse_record(&fs::read_to_string(&path).unwrap()).unwrap();
    for f in &mut rec.files {
        f.saved_at = if f.sha256 == new.sha256 {
            "2026-02-01T00:00:00Z".into()
        } else {
            "2026-01-01T00:00:00Z".into()
        };
    }
    fs::write(&path, render_record(&rec)).unwrap();
    let held = store
        .find_source(&paper.ids, SourceKind::Pdf)
        .unwrap()
        .unwrap();
    assert_eq!(held.file.sha256, new.sha256);
    assert_ne!(held.file.sha256, old.sha256);
}

#[test]
fn a_file_overwritten_on_disk_is_an_error_naming_the_digest() {
    let (_d, store) = scratch("tamper");
    let paper = info(Some("8"), None);
    let file = store.keep(&paper, b"%PDF-1", SourceKind::Pdf, "t").unwrap();
    fs::write(store.path_of(&file).unwrap(), b"other").unwrap();
    let err = store.find_source(&paper.ids, SourceKind::Pdf).unwrap_err();
    assert!(err.contains("digest"), "{err}");
    assert!(err.contains("pmid=8"), "{err}");
    assert!(err.contains(&file.name), "{err}");
}

#[test]
fn an_unreadable_root_is_an_error() {
    let (dir, store) = scratch("gone");
    fs::remove_dir_all(&dir).unwrap();
    let ids = PaperIds::canonical(Some("1"), None, None).unwrap();
    assert!(store.find_source(&ids, SourceKind::Pdf).is_err());
}

#[test]
fn note_use_adds_this_installation_once() {
    let (dir, store) = scratch("use");
    let other = Installation::new("medicalfactchecker", "bbbb", "Mac").unwrap();
    let paper = info(Some("9"), None);
    let file = store
        .library
        .save(&other, &paper, b"%PDF-1", SourceKind::Pdf, "t")
        .unwrap();
    let mine = dir
        .join("papers")
        .join(&file.folder)
        .join("records")
        .join(me().record_file_name());
    assert!(!mine.exists());
    store.note_use(&paper, &file).unwrap();
    let first = fs::read(&mine).unwrap();
    store.note_use(&paper, &file).unwrap();
    assert_eq!(fs::read(&mine).unwrap(), first);
    let scan = store.library.scan().unwrap();
    assert_eq!(scan.folders[0].used_by(&file.sha256).len(), 2);
}

#[test]
fn path_of_reads_back_the_bytes_and_refuses_a_bad_component() {
    let (_d, store) = scratch("path");
    let file = store
        .keep(&info(Some("3"), None), b"%PDF-3", SourceKind::Pdf, "t")
        .unwrap();
    assert_eq!(fs::read(store.path_of(&file).unwrap()).unwrap(), b"%PDF-3");
    let mut bad = file.clone();
    bad.name = "../x".into();
    assert!(store.path_of(&bad).is_err());
}

#[test]
fn paper_info_comes_from_the_jats() {
    let article = parse(SAMPLE).unwrap();
    let ids = PaperIds::canonical(Some("111"), None, None).unwrap();
    let got = paper_info(ids, Some(&article));
    assert_eq!(got.title.as_deref(), Some(article.title.as_str()));
    assert_eq!(got.year, Some(2024));
    assert_eq!(got.first_author.as_deref(), Some("Smith"));
    // The request's PMID wins; the JATS fills the DOI and PMCID.
    assert_eq!(got.ids.pmid.as_deref(), Some("111"));
    assert_eq!(got.ids.doi.as_deref(), Some("10.1234/jbr.2024.001"));
    assert_eq!(got.ids.pmcid.as_deref(), Some("PMC7614751"));
    let bare = paper_info(PaperIds::default(), None);
    assert_eq!(bare, PaperInfo::default());
}

/// Give each digest in `times` that `saved_at` in this installation's record.
fn set_saved_at(dir: &std::path::Path, folder: &str, times: &[(&str, &str)]) {
    let path = dir
        .join("papers")
        .join(folder)
        .join("records")
        .join(me().record_file_name());
    let mut rec = parse_record(&fs::read_to_string(&path).unwrap()).unwrap();
    for f in &mut rec.files {
        if let Some((_, at)) = times.iter().find(|(d, _)| *d == f.sha256) {
            f.saved_at = (*at).into();
        }
    }
    fs::write(&path, render_record(&rec)).unwrap();
}

#[test]
fn a_malformed_saved_at_does_not_beat_a_well_formed_one() {
    for bad in [
        "9999",
        "zzz",
        "2026-13-99T00:00:00+00:00",
        "\u{662}026-01-01T00:00:00Z",
    ] {
        let (dir, store) = scratch("malformed");
        let paper = info(Some("20"), None);
        let a = store.keep(&paper, b"%PDF-a", SourceKind::Pdf, "t").unwrap();
        let b = store.keep(&paper, b"%PDF-b", SourceKind::Pdf, "t").unwrap();
        set_saved_at(
            &dir,
            &a.folder,
            &[(&a.sha256, "2026-01-01T00:00:00Z"), (&b.sha256, bad)],
        );
        let held = store
            .find_source(&paper.ids, SourceKind::Pdf)
            .unwrap()
            .unwrap();
        assert_eq!(held.file.sha256, a.sha256, "{bad}");
    }
}

#[test]
fn a_tie_goes_to_the_lower_digest() {
    let (dir, store) = scratch("tie");
    let paper = info(Some("21"), None);
    let a = store.keep(&paper, b"%PDF-a", SourceKind::Pdf, "t").unwrap();
    let b = store.keep(&paper, b"%PDF-b", SourceKind::Pdf, "t").unwrap();
    let same = "2026-01-01T00:00:00Z";
    set_saved_at(&dir, &a.folder, &[(&a.sha256, same), (&b.sha256, same)]);
    let held = store
        .find_source(&paper.ids, SourceKind::Pdf)
        .unwrap()
        .unwrap();
    assert_eq!(held.file.sha256, a.sha256.clone().min(b.sha256.clone()));
}

#[test]
fn a_listed_file_not_on_disk_is_skipped() {
    let (dir, store) = scratch("missing");
    let paper = info(Some("22"), None);
    let a = store.keep(&paper, b"%PDF-a", SourceKind::Pdf, "t").unwrap();
    let b = store.keep(&paper, b"%PDF-b", SourceKind::Pdf, "t").unwrap();
    set_saved_at(
        &dir,
        &a.folder,
        &[
            (&a.sha256, "2026-01-01T00:00:00Z"),
            (&b.sha256, "2026-02-01T00:00:00Z"),
        ],
    );
    fs::remove_file(store.path_of(&b).unwrap()).unwrap();
    let held = store
        .find_source(&paper.ids, SourceKind::Pdf)
        .unwrap()
        .unwrap();
    assert_eq!(held.file.sha256, a.sha256);
}

#[test]
fn a_year_beyond_i32_is_none() {
    let mut article = parse(SAMPLE).unwrap();
    article.year = "99999999999".into();
    assert_eq!(paper_info(PaperIds::default(), Some(&article)).year, None);
    article.year = "2147483647".into();
    assert_eq!(
        paper_info(PaperIds::default(), Some(&article)).year,
        Some(i32::MAX)
    );
}
