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

//! A real PDF read and segmented end to end: the benchmark for layout work.
//!
//! The PDF is named by `BMLIB_BENCHMARK_PDF` and is not committed (a journal's
//! PDF is not ours to publish), so the test is ignored; run it with
//! `BMLIB_BENCHMARK_PDF=paper.pdf cargo test --all-features --test pdf_benchmark -- --ignored`.
//! With `BMLIB_BENCHMARK_OUT` set it also writes the canonical text there, to
//! diff against Python's `tests/test_pdf_benchmark.py`, which writes the same.

#![cfg(feature = "pdf")]

use bmlib::fulltext::canonical::canonical_from_segmented;
use bmlib::fulltext::pdf_text::{PdfLimit, PdfTextExtractor, SPACING_ACCENTS};
use bmlib::fulltext::segmenter::SectionSegmenter;
use bmlib::fulltext::MupdfExtractor;

#[test]
#[ignore = "needs BMLIB_BENCHMARK_PDF, a PDF not in the repository"]
fn a_benchmark_pdf_reads_as_a_segmented_paper() {
    let Some(pdf) = std::env::var_os("BMLIB_BENCHMARK_PDF") else {
        panic!("set BMLIB_BENCHMARK_PDF to the PDF to read");
    };
    let read = MupdfExtractor::new()
        .extract_blocks_with_warnings(std::path::Path::new(&pdf), &PdfLimit::default())
        .expect("extracts");
    let document = SectionSegmenter::default().segment_document(
        &read.blocks,
        "",
        read.metadata_title.as_deref(),
        serde_json::json!({}),
    );
    let canonical = canonical_from_segmented(&document);
    if let Some(out) = std::env::var_os("BMLIB_BENCHMARK_OUT") {
        std::fs::write(out, &canonical.text).expect("writes the text");
    }
    // Headings were found, and no accent is left standing before a letter.
    assert!(document.sections.len() > 1, "{:#?}", document.sections);
    let chars: Vec<char> = canonical.text.chars().collect();
    let stranded = chars.windows(2).any(|pair| {
        SPACING_ACCENTS.iter().any(|(accent, _)| *accent == pair[0]) && pair[1].is_alphabetic()
    });
    assert!(!stranded, "a spacing accent stands before a letter");
}
