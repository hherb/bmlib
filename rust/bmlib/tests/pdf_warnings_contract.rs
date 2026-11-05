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

//! The shared MuPDF-messages contract (#599).
//!
//! The same files Python's `tests/test_pdf_warnings_contract.py` asserts
//! against, written by `scripts/make_pdf_warnings_fixture.py`: [`MupdfExtractor`]
//! reads each PDF as the expected lines and returns exactly the messages
//! PyMuPDF does as `engine_messages`, apart from bmlib's own `warnings` (#619)
//! — a repaired xref, a page cut short after too many syntax errors (#605),
//! and nothing for a sound PDF — and a PDF that cannot be read fails carrying
//! what MuPDF said first (#619).

#![cfg(feature = "pdf")]

use bmlib::fulltext::pdf_text::{PdfLimit, PdfTextExtractor};
use bmlib::fulltext::service::{PdfExtractError, PdfExtractor};
use bmlib::fulltext::{is_about_the_file, MupdfExtractor, MupdfPdfExtractor};
use serde_json::Value;

const FIXTURE: &str = include_str!("../../../tests/fixtures/pdf_warnings.json");

fn contract() -> Value {
    serde_json::from_str(FIXTURE).expect("the fixture is JSON")
}

fn path(row: &Value) -> std::path::PathBuf {
    std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../tests/fixtures")
        .join(row["pdf"].as_str().expect("the PDF's name"))
}

fn strings(value: &Value) -> Vec<String> {
    serde_json::from_value(value.clone()).expect("a list of strings")
}

#[test]
fn each_pdf_reads_as_its_lines_with_its_messages() {
    for row in contract()["rows"].as_array().expect("rows") {
        let read = MupdfExtractor::new()
            .extract_blocks_with_warnings(&path(row), &PdfLimit::default())
            .expect("extracts");
        let texts: Vec<String> = read.blocks.into_iter().map(|b| b.text).collect();
        assert_eq!(texts, strings(&row["texts"]), "{}", row["pdf"]);
        assert_eq!(
            read.engine_messages,
            strings(&row["engine_messages"]),
            "{}",
            row["pdf"]
        );
        assert_eq!(read.warnings, strings(&row["warnings"]), "{}", row["pdf"]);
    }
}

/// Every way of reading a PDF that fails carries what MuPDF said first.
#[test]
fn a_failed_read_carries_what_mupdf_said() {
    for row in contract()["failed"].as_array().expect("failed rows") {
        let expected = strings(&row["engine_messages"]);
        let kind = row["kind"].as_str().expect("a kind");
        let blocks = MupdfExtractor::new()
            .extract_blocks_with_warnings(&path(row), &PdfLimit::default())
            .expect_err("fails");
        let pages = MupdfExtractor::new()
            .page_texts_with_warnings(&path(row), &PdfLimit::default())
            .expect_err("fails");
        let Err(PdfExtractError::Conversion(service)) =
            MupdfPdfExtractor::new().extract(&path(row))
        else {
            panic!("{}: the adapter did not fail as a conversion", row["pdf"]);
        };
        for error in [blocks, pages, service] {
            assert_eq!(error.kind.as_str(), kind, "{}", row["pdf"]);
            assert_eq!(error.engine_messages, expected, "{}", row["pdf"]);
        }
    }
}

#[test]
fn the_page_texts_carry_the_same_messages() {
    for row in contract()["rows"].as_array().expect("rows") {
        let (_, engine_messages) = MupdfExtractor::new()
            .page_texts_with_warnings(&path(row), &PdfLimit::default())
            .expect("extracts");
        assert_eq!(
            engine_messages,
            strings(&row["engine_messages"]),
            "{}",
            row["pdf"]
        );
    }
}

#[test]
fn the_service_adapter_carries_them_too() {
    for row in contract()["rows"].as_array().expect("rows") {
        let text = MupdfPdfExtractor::new()
            .extract(&path(row))
            .expect("extracts");
        assert_eq!(
            text.engine_messages,
            strings(&row["engine_messages"]),
            "{}",
            row["pdf"]
        );
    }
}

/// A second read on the same thread reports what the first did. A stability
/// check: a successful read flushes MuPDF's count at its end, so the flush at
/// the start is pinned by the unit test `a_warning_left_counted_does_not_swallow_the_next`.
#[test]
fn a_second_read_on_one_thread_reports_the_same() {
    let contract = contract();
    let row = &contract["rows"][0];
    let read = || {
        MupdfExtractor::new()
            .extract_blocks_with_warnings(&path(row), &PdfLimit::default())
            .expect("extracts")
            .engine_messages
    };
    let first = read();
    assert!(!first.is_empty());
    assert_eq!(read(), first);
}

/// The text MuPDF never reached is not in the blocks: the messages are the
/// only sign the page was cut short.
#[test]
fn the_contract_carries_the_shapes_it_is_for() {
    let contract = contract();
    let rows = contract["rows"].as_array().expect("rows");
    let lost = contract["lost_line"].as_str().expect("the lost line");
    let cut_short = strings(&rows[1]["engine_messages"]);
    assert!(cut_short
        .iter()
        .any(|m| m == "too many syntax errors; ignoring rest of page"));
    assert!(!strings(&rows[1]["texts"]).iter().any(|t| t == lost));
    assert!(strings(&rows[0]["engine_messages"])
        .iter()
        .any(|m| m == "repairing PDF document"));
    assert!(rows[2]["engine_messages"]
        .as_array()
        .expect("a list")
        .is_empty());
    let failed = contract["failed"].as_array().expect("failed rows");
    assert_eq!(failed.len(), 1);
    assert!(strings(&failed[0]["engine_messages"])
        .iter()
        .any(|m| m == "trying to repair broken xref"));
}

#[test]
fn only_the_store_race_is_set_aside() {
    let contract = contract();
    for message in strings(&contract["set_aside"]) {
        assert!(!is_about_the_file(&message), "{message}");
    }
    for message in strings(&contract["kept"]) {
        assert!(is_about_the_file(&message), "{message}");
    }
}
