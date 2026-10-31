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

//! The shared PDF-extraction contract, from a PDF's bytes (#590).
//!
//! The same files Python's `tests/test_pdf_extraction_contract.py` asserts
//! against, written by `scripts/make_pdf_extraction_fixture.py`:
//! [`MupdfExtractor`] reads `tests/fixtures/pdf_extraction.pdf` as exactly the
//! expected blocks (geometry within the fixture's tolerance, since the crate's
//! MuPDF is not PyMuPDF's), and the segmenter and `canonical_from_segmented`
//! lay those blocks out as exactly the expected canonical text — a soft hyphen
//! joined, a hard one kept.

#![cfg(feature = "pdf")]

use bmlib::fulltext::canonical::canonical_from_segmented;
use bmlib::fulltext::models::TextBlock;
use bmlib::fulltext::pdf_text::PdfTextExtractor;
use bmlib::fulltext::segmenter::SectionSegmenter;
use bmlib::fulltext::MupdfExtractor;
use serde_json::Value;

const FIXTURE: &str = include_str!("../../../tests/fixtures/pdf_extraction.json");

fn contract() -> Value {
    serde_json::from_str(FIXTURE).expect("the fixture is JSON")
}

fn pdf_path() -> std::path::PathBuf {
    std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../tests/fixtures")
        .join(contract()["pdf"].as_str().expect("the PDF's name"))
}

#[test]
fn the_blocks_are_the_expected_ones() {
    let contract = contract();
    let tolerance = contract["geometry_tolerance"]
        .as_f64()
        .expect("a tolerance");
    let expected: Vec<TextBlock> =
        serde_json::from_value(contract["blocks"].clone()).expect("blocks");
    let blocks = MupdfExtractor::new()
        .extract_blocks(&pdf_path())
        .expect("extracts");
    assert_eq!(blocks.len(), expected.len(), "{blocks:#?}");
    for (got, want) in blocks.iter().zip(&expected) {
        assert_eq!(
            (
                &got.text,
                got.page_num,
                got.font_size,
                &got.font_name,
                got.is_bold,
                got.is_italic
            ),
            (
                &want.text,
                want.page_num,
                want.font_size,
                &want.font_name,
                want.is_bold,
                want.is_italic
            )
        );
        for (axis, a, b) in [
            ("x", got.x, want.x),
            ("y", got.y, want.y),
            ("width", got.width, want.width),
            ("height", got.height, want.height),
        ] {
            assert!(
                (a - b).abs() <= tolerance,
                "{axis} of {:?}: {a} against {b}",
                got.text
            );
        }
    }
}

#[test]
fn the_segmented_blocks_are_the_expected_canonical_text() {
    let contract = contract();
    let blocks: Vec<TextBlock> =
        serde_json::from_value(contract["blocks"].clone()).expect("blocks");
    let document =
        SectionSegmenter::default().segment_document(&blocks, "", None, serde_json::json!({}));
    let canonical = canonical_from_segmented(&document);
    assert_eq!(
        serde_json::to_value(&canonical).expect("serialisable"),
        contract["canonical"]
    );
    assert!(canonical.text.contains("prevalent") && canonical.text.contains("specific"));
    assert!(canonical.text.contains("dose- response") && !canonical.text.contains('\u{ad}'));
}

/// The PDF is the one the generator writes (its digest is in the fixture), so
/// a Rust run cannot pass against a PDF Python never read.
#[test]
fn the_pdf_is_the_contracts() {
    use sha2::{Digest, Sha256};
    let digest = Sha256::digest(std::fs::read(pdf_path()).expect("the PDF"));
    let hex: String = digest.iter().map(|byte| format!("{byte:02x}")).collect();
    assert_eq!(hex, contract()["pdf_sha256"].as_str().expect("a digest"));
}
