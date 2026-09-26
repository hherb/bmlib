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

//! The PDFium backend — the font-name rules always, extraction when PDFium binds.
//!
//! The font-name arms run unconditionally because they are the part that was
//! **wrong first**: PDFium's `font_weight()` and `font_is_italic()` return nothing
//! usable for a base-14 face, so the name decides. They need no PDF.
//!
//! The extraction tests need a bound PDFium, which means a downloaded library.
//! They **skip loudly** when it is unavailable — printing why, and asserting
//! nothing — rather than passing quietly, because a silent skip is how a backend
//! that never worked reports success. Set `PDFIUM_BUNDLED_CACHE_DIR` to a writable
//! path (a sandbox denies the platform default) and run
//! `cargo test --features pdf --test pdfium_backend`.

#![cfg(feature = "pdf")]

use bmlib::fulltext::pdf_text::PdfTextExtractor;
use bmlib::fulltext::pdfium_backend::{
    font_name_is_bold, font_name_is_italic, PdfiumExtractor, BOLD_NAME_FRAGMENTS,
    BOLD_WEIGHT_FLOOR, ITALIC_NAME_FRAGMENTS,
};

/// The names PyMuPDF reports as bold, and the names it does not.
#[test]
fn a_bold_face_is_recognised_from_its_name() {
    for name in [
        "Helvetica-Bold",
        "Helvetica-BoldOblique",
        "Arial-BoldMT",
        "HELVETICA-BOLD",
        "Times-BoldItalic",
        "Roboto-Black",
        "Roboto-BlackItalic",
        "OpenSans-Semibold",
        "NotoSans-DemiBold",
        "Helvetica-Heavy",
        "ArialMT,Bold",
    ] {
        assert!(font_name_is_bold(name), "{name} should read as bold");
    }
    for name in [
        "Helvetica",
        "Helvetica-Oblique",
        "Times-Roman",
        "ArialMT",
        "DejaVuSans",
        "",
    ] {
        assert!(!font_name_is_bold(name), "{name} should not read as bold");
    }
    // `Bold` beats the weight floor's own reason: a name that says it needs no
    // weight at all.
    assert_eq!(BOLD_WEIGHT_FLOOR, 600);
    assert!(BOLD_NAME_FRAGMENTS.contains(&"bold"));
}

/// Likewise for italic, and `BoldOblique` is both.
#[test]
fn an_italic_face_is_recognised_from_its_name() {
    for name in [
        "Helvetica-Oblique",
        "Helvetica-BoldOblique",
        "Times-Italic",
        "Arial-ItalicMT",
        "HELVETICA-OBLIQUE",
    ] {
        assert!(font_name_is_italic(name), "{name} should read as italic");
    }
    for name in ["Helvetica", "Helvetica-Bold", "Times-Roman", ""] {
        assert!(
            !font_name_is_italic(name),
            "{name} should not read as italic"
        );
    }
    // Both, which is the case a rule checking one arm would get wrong.
    assert!(font_name_is_bold("Helvetica-BoldOblique"));
    assert!(font_name_is_italic("Helvetica-BoldOblique"));
    assert!(ITALIC_NAME_FRAGMENTS.contains(&"oblique"));
}

/// A minimal single-page PDF with one text line, built here so the tests need no
/// fixture file and no Python.
fn tiny_pdf(text: &str, font: &str, size: u32) -> Vec<u8> {
    let stream = format!("BT /F1 {size} Tf 72 700 Td ({text}) Tj ET");
    let objects = [
        "1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n".to_string(),
        "2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n".to_string(),
        "3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R \
         /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n"
            .to_string(),
        format!(
            "4 0 obj\n<< /Length {} >>\nstream\n{stream}\nendstream\nendobj\n",
            stream.len()
        ),
        format!("5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /{font} >>\nendobj\n"),
    ];
    let mut out = String::from("%PDF-1.4\n");
    let mut offsets = Vec::new();
    for object in &objects {
        offsets.push(out.len());
        out.push_str(object);
    }
    let xref = out.len();
    out.push_str(&format!(
        "xref\n0 {}\n0000000000 65535 f \n",
        objects.len() + 1
    ));
    for offset in offsets {
        out.push_str(&format!("{offset:010} 00000 n \n"));
    }
    out.push_str(&format!(
        "trailer\n<< /Size {} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n",
        objects.len() + 1
    ));
    out.into_bytes()
}

/// Write a PDF into a temporary file and hand back the guard and its path.
fn write_pdf(label: &str, bytes: &[u8]) -> (TempPdf, std::path::PathBuf) {
    let dir = TempPdf::new(label);
    let path = dir.path().join("probe.pdf");
    std::fs::write(&path, bytes).expect("write pdf");
    (dir, path)
}

struct TempPdf(std::path::PathBuf);

impl TempPdf {
    fn new(label: &str) -> Self {
        let unique = format!(
            "bmlib-pdf-{label}-{}-{:x}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_nanos())
                .unwrap_or_default()
        );
        let path = std::env::temp_dir().join(unique);
        std::fs::create_dir_all(&path).expect("temp dir");
        TempPdf(path)
    }
    fn path(&self) -> &std::path::Path {
        &self.0
    }
}

impl Drop for TempPdf {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

/// Bind PDFium, or say why the extraction tests are skipped.
fn extractor() -> Option<PdfiumExtractor> {
    match PdfiumExtractor::bundled() {
        Ok(extractor) => Some(extractor),
        Err(error) => {
            eprintln!(
                "SKIPPING the PDFium extraction tests: {error}\n\
                 Set PDFIUM_BUNDLED_CACHE_DIR to a writable directory (a sandbox denies the \
                 platform default) and re-run with --features pdf."
            );
            None
        }
    }
}

/// A real PDF yields a block whose text, size and font are the ones written.
#[test]
fn a_pdf_yields_a_block_with_its_font() {
    let Some(extractor) = extractor() else { return };
    let (_guard, path) = write_pdf("simple", &tiny_pdf("Hello PDF World", "Helvetica", 24));
    let blocks = extractor.extract_blocks(&path).expect("extracts");
    assert_eq!(blocks.len(), 1, "{blocks:?}");
    assert_eq!(blocks[0].text, "Hello PDF World");
    assert_eq!(blocks[0].font_size, 24.0);
    assert_eq!(blocks[0].font_name, "Helvetica");
    assert!(!blocks[0].is_bold);
    assert_eq!(blocks[0].page_num, 0);
    assert!(
        blocks[0].width > 0.0,
        "the bounding box is read: {blocks:?}"
    );
    assert_eq!(extractor.name(), "pdfium");

    // And the page text agrees with the block.
    let pages = extractor.page_texts(&path).expect("page texts");
    assert_eq!(pages.len(), 1);
    assert!(pages[0].contains("Hello PDF World"), "{pages:?}");
}

/// **Bold is read from the font name**, because PDFium's weight accessor returns
/// nothing usable for a base-14 face. This is the arm that was wrong first: a
/// `Helvetica-Bold` heading came back `is_bold = false`.
#[test]
fn a_bold_base_font_reads_as_bold() {
    let Some(extractor) = extractor() else { return };
    let (_guard, path) = write_pdf("bold", &tiny_pdf("Bold heading", "Helvetica-Bold", 16));
    let blocks = extractor.extract_blocks(&path).expect("extracts");
    assert_eq!(blocks.len(), 1, "{blocks:?}");
    assert!(
        blocks[0].is_bold,
        "a Helvetica-Bold face must read as bold: {blocks:?}"
    );
    assert_eq!(blocks[0].font_name, "Helvetica-Bold");
    assert!(!blocks[0].is_italic);
}

/// An oblique face reads as italic, from the same rule.
#[test]
fn an_oblique_base_font_reads_as_italic() {
    let Some(extractor) = extractor() else { return };
    let (_guard, path) = write_pdf("italic", &tiny_pdf("Italic face", "Helvetica-Oblique", 14));
    let blocks = extractor.extract_blocks(&path).expect("extracts");
    assert_eq!(blocks.len(), 1, "{blocks:?}");
    assert!(blocks[0].is_italic, "{blocks:?}");
    assert!(!blocks[0].is_bold, "italic is not bold: {blocks:?}");
}

/// An unreadable file is an error naming the path, not an empty extraction — an
/// empty result for a corrupt PDF would read as a PDF with no text.
#[test]
fn a_corrupt_pdf_is_an_error() {
    let Some(extractor) = extractor() else { return };
    let (_guard, path) = write_pdf("corrupt", b"not a pdf at all");
    let error = extractor.extract_blocks(&path).expect_err("refuses");
    assert!(error.contains("could not open"), "{error}");
    assert!(
        error.contains("probe.pdf"),
        "the error names the file: {error}"
    );
}

/// The blocks feed `render_html`, so a real PDF's text reaches an `<p>` — the shape
/// `FullTextResult.html` carries for an extracted article.
#[test]
fn a_pdf_reaches_the_html_renderer() {
    let Some(extractor) = extractor() else { return };
    let (_guard, path) = write_pdf("html", &tiny_pdf("Extracted sentence.", "Helvetica", 12));
    let pages = extractor.page_texts(&path).expect("page texts");
    let html = bmlib::fulltext::pdf_text::render_html(true, &pages.join("\n"), &pages);
    assert!(html.contains("<p>"), "{html}");
    assert!(html.contains("Extracted sentence."), "{html}");
}

/// The service's adapter produces **HTML and a page count**, which is what
/// `FullTextResult.html` carries for an extracted article.
///
/// This is the path `FullTextService` takes for a retrieved PDF, so it is the one
/// that has to work end to end rather than only the block extraction underneath it.
#[test]
fn the_service_adapter_produces_html() {
    use bmlib::fulltext::service::{PdfExtractor, PdfText};
    use bmlib::fulltext::PdfiumPdfExtractor;

    let Ok(adapter) = PdfiumPdfExtractor::bundled() else {
        eprintln!("SKIPPING the service-adapter test: PDFium did not bind");
        return;
    };
    let (_guard, path) = write_pdf("adapter", &tiny_pdf("Adapter sentence.", "Helvetica", 11));
    let PdfText {
        html,
        success,
        page_count,
        converted_pages,
        char_count,
        error_message,
        ..
    } = adapter.extract(&path).expect("extracts");

    assert!(success);
    assert_eq!(error_message, None);
    assert_eq!(page_count, 1);
    assert_eq!(converted_pages, page_count);
    assert!(char_count > 0);
    assert!(html.contains("<p>"), "{html}");
    assert!(html.contains("Adapter sentence."), "{html}");

    // A corrupt file is a conversion failure, not an empty success.
    let (_guard2, bad) = write_pdf("adapter-corrupt", b"not a pdf");
    assert!(adapter.extract(&bad).is_err());
}
