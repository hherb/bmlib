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

//! The MuPDF backend (#590), against PDFs built here and against the rules it
//! takes from PyMuPDF.
//!
//! MuPDF is compiled in and linked statically, so these tests always run with
//! the `pdf` feature: there is no library to bind and nothing to skip. The
//! byte-level agreement with Python is `pdf_extraction_contract.rs`'s.

#![cfg(feature = "pdf")]

use bmlib::fulltext::mupdf_backend::{pymupdf_font_name, MupdfExtractor};
use bmlib::fulltext::pdf_text::{PdfError, PdfErrorKind, PdfTextExtractor};

/// PyMuPDF's `JM_font_name`: a six-letter subset tag goes, nothing else does.
#[test]
fn a_subset_tag_is_removed_from_the_font_name() {
    assert_eq!(
        pymupdf_font_name(b"ABCDEF+Helvetica-Bold"),
        "Helvetica-Bold"
    );
    assert_eq!(pymupdf_font_name(b"Helvetica"), "Helvetica");
    // A `+` anywhere but at byte 6 is part of the name.
    assert_eq!(pymupdf_font_name(b"ABC+Helvetica"), "ABC+Helvetica");
    assert_eq!(pymupdf_font_name(b"CIDFont+F6"), "CIDFont+F6");
    assert_eq!(pymupdf_font_name(b""), "");
}

/// A name that is not UTF-8 arrives as its bytes read as Latin-1, as PyMuPDF's
/// `raw-unicode-escape` gives it — the EUC-KR name a corpus PDF carries.
#[test]
fn a_font_name_in_another_encoding_reads_as_latin_1() {
    let euc_kr = [0xB8, 0xBC, 0xC0, 0xBA, 0xB0, 0xED, 0xB5, 0xF1];
    assert_eq!(
        pymupdf_font_name(&euc_kr),
        "\u{b8}\u{bc}\u{c0}\u{ba}\u{b0}\u{ed}\u{b5}\u{f1}"
    );
    // The tag is found in the bytes: a non-ASCII byte before it does not hide it.
    assert_eq!(pymupdf_font_name(b"AB\xC0DEF+Name"), "Name");
}

/// A minimal single-page PDF with one text line, built here so the tests need no
/// fixture file and no Python. `font` is written into `/BaseFont` as given, so a
/// `#xx` escape names a byte.
fn tiny_pdf(text: &str, font: &str, size: u32) -> Vec<u8> {
    pdf_with(text, font, size, "", &[])
}

/// [`tiny_pdf`], with `page_extra` added to the page dictionary and `extra`
/// appended as objects 6, 7, ….
fn pdf_with(text: &str, font: &str, size: u32, page_extra: &str, extra: &[String]) -> Vec<u8> {
    let stream = format!("BT /F1 {size} Tf 72 700 Td ({text}) Tj ET");
    let mut objects = vec![
        "1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n".to_string(),
        "2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n".to_string(),
        format!(
            "3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R \
             /Resources << /Font << /F1 5 0 R >> >> {page_extra}>>\nendobj\n"
        ),
        format!(
            "4 0 obj\n<< /Length {} >>\nstream\n{stream}\nendstream\nendobj\n",
            stream.len()
        ),
        format!("5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /{font} >>\nendobj\n"),
    ];
    for (index, body) in extra.iter().enumerate() {
        objects.push(format!("{} 0 obj\n{body}\nendobj\n", 6 + index));
    }
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

/// A real PDF yields a block whose text, size and font are the ones written.
#[test]
fn a_pdf_yields_a_block_with_its_font() {
    let extractor = MupdfExtractor::new();
    let (_guard, path) = write_pdf("simple", &tiny_pdf("Hello PDF World", "Helvetica", 24));
    let blocks = extractor.extract_blocks(&path).expect("extracts");
    assert_eq!(blocks.len(), 1, "{blocks:?}");
    assert_eq!(blocks[0].text, "Hello PDF World");
    assert_eq!(blocks[0].font_size, 24.0);
    assert_eq!(blocks[0].font_name, "Helvetica");
    assert!(!blocks[0].is_bold);
    assert_eq!(blocks[0].page_num, 0);
    assert!(
        blocks[0].width > 0.0 && blocks[0].height > 0.0,
        "the bounding box is read: {blocks:?}"
    );
    // MuPDF's coordinates run down the page: a line 700 pt up sits near the top.
    assert!(blocks[0].y < 100.0, "{blocks:?}");
    assert_eq!(extractor.name(), "mupdf");

    // And the page text agrees with the block.
    let pages = extractor.page_texts(&path).expect("page texts");
    assert_eq!(pages.len(), 1);
    assert!(pages[0].contains("Hello PDF World"), "{pages:?}");
}

/// Bold and italic are MuPDF's own reading of the face, which for an unembedded
/// base-14 font it takes from its own copy — PDFium's accessors returned
/// nothing there, which is why the old backend guessed from the name.
#[test]
fn a_base_14_face_reads_as_bold_or_italic() {
    let extractor = MupdfExtractor::new();
    for (font, bold, italic) in [
        ("Helvetica-Bold", true, false),
        ("Helvetica-Oblique", false, true),
        ("Times-BoldItalic", true, true),
        ("Courier", false, false),
    ] {
        let (_guard, path) = write_pdf("face", &tiny_pdf("A heading", font, 16));
        let blocks = extractor.extract_blocks(&path).expect("extracts");
        assert_eq!(blocks.len(), 1, "{font}: {blocks:?}");
        assert_eq!(
            (
                blocks[0].font_name.as_str(),
                blocks[0].is_bold,
                blocks[0].is_italic
            ),
            (font, bold, italic)
        );
    }
}

/// A font whose name is not UTF-8 is read, not a panic: the crate's own
/// `Font::name` unwraps a UTF-8 decode, and a corpus PDF carries such a name.
#[test]
fn a_font_name_that_is_not_utf_8_does_not_panic() {
    let extractor = MupdfExtractor::new();
    let (_guard, path) = write_pdf("euc-kr", &tiny_pdf("Body text", "#B8#BC#C0#BA", 10));
    let blocks = extractor.extract_blocks(&path).expect("extracts");
    assert_eq!(blocks.len(), 1, "{blocks:?}");
    assert_eq!(blocks[0].text, "Body text");
    assert_eq!(
        blocks[0].font_name, "\u{b8}\u{bc}\u{c0}\u{ba}",
        "{blocks:?}"
    );
}

/// A `FreeText` annotation's text is read, as PyMuPDF reads it: PyMuPDF runs
/// the whole page, and a figure label can be one (three, in one corpus PDF).
#[test]
fn an_annotations_text_is_read() {
    let label = "BT /F1 12 Tf 2 5 Td (Figure 1) Tj ET";
    let extra = [
        "<< /Type /Annot /Subtype /FreeText /Rect [100 600 300 620] /Contents (Figure 1) \
         /DA (/F1 12 Tf 0 g) /AP << /N 7 0 R >> >>"
            .to_string(),
        format!(
            "<< /Type /XObject /Subtype /Form /BBox [0 0 200 20] \
             /Resources << /Font << /F1 5 0 R >> >> /Length {} >>\nstream\n{label}\nendstream",
            label.len()
        ),
    ];
    let (_guard, path) = write_pdf(
        "annot",
        &pdf_with("Page text", "Helvetica", 10, "/Annots [6 0 R] ", &extra),
    );
    let extractor = MupdfExtractor::new();
    let texts: Vec<String> = extractor
        .extract_blocks(&path)
        .expect("extracts")
        .into_iter()
        .map(|b| b.text)
        .collect();
    assert_eq!(texts, ["Page text", "Figure 1"]);
    let pages = extractor.page_texts(&path).expect("page texts");
    assert!(pages[0].contains("Figure 1"), "{pages:?}");
}

/// An unreadable file is an error of the file's kind, not an empty extraction —
/// an empty result for a corrupt PDF would read as a PDF with no text — and its
/// message does not name the path, which the caller knows (#598).
#[test]
fn a_corrupt_pdf_is_an_unreadable_file() {
    let extractor = MupdfExtractor::new();
    let tiny = tiny_pdf("Page text", "Helvetica", 12);
    // Garbage and empty files are MuPDF's FORMAT, a truncated one its SYNTAX.
    for (label, bytes) in [
        ("garbage", b"not a pdf at all".as_slice()),
        ("empty", b"".as_slice()),
        ("truncated", b"%PDF-1.4\n1 0 obj\n<<".as_slice()),
        ("cut", &tiny[..tiny.len() / 3]),
    ] {
        let (_guard, path) = write_pdf(label, bytes);
        for error in [
            extractor.extract_blocks(&path).expect_err(label),
            extractor.page_texts(&path).expect_err(label),
        ] {
            assert_eq!(error.kind, PdfErrorKind::Unreadable, "{label}: {error}");
            assert!(
                error.message.starts_with("the PDF could not be read: "),
                "{label}: {error}"
            );
            assert!(!error.message.contains("probe.pdf"), "{label}: {error}");
        }
    }
}

/// A file MuPDF has no handler for is MuPDF's `UNSUPPORTED`, the file's side,
/// and that message names the path, which is removed.
#[test]
fn a_file_of_no_known_type_is_unreadable_without_its_path() {
    let guard = TempPdf::new("unknown-type");
    let path = guard.path().join("probe.bin");
    std::fs::write(&path, b"not a pdf at all").expect("write");
    let error = MupdfExtractor::new()
        .extract_blocks(&path)
        .expect_err("refuses");
    assert_eq!(error.kind, PdfErrorKind::Unreadable, "{error}");
    assert!(!error.message.contains("probe.bin"), "{error}");
    assert!(error.message.contains("the file"), "{error}");
}

/// A file that cannot be opened at all is not the file's fault as a PDF: MuPDF
/// reports a missing file as `SYSTEM`, the engine's side, and names the path,
/// which is removed.
#[test]
fn a_missing_file_is_an_engine_error_without_its_path() {
    let guard = TempPdf::new("missing");
    let path = guard.path().join("probe.pdf");
    let error = MupdfExtractor::new()
        .extract_blocks(&path)
        .expect_err("refuses");
    assert_eq!(error.kind, PdfErrorKind::Engine, "{error}");
    assert!(
        error.message.starts_with("the PDF engine failed: "),
        "{error}"
    );
    assert!(!error.message.contains("probe.pdf"), "{error}");
    assert!(error.message.contains("the file"), "{error}");
}

/// The crate's own errors are the engine's: a path holding a NUL byte cannot
/// reach MuPDF at all (`Error::Nul`), which says nothing about the file.
#[test]
fn a_crate_error_is_an_engine_error() {
    let error = MupdfExtractor::new()
        .extract_blocks(std::path::Path::new("probe\0.pdf"))
        .expect_err("refuses");
    assert_eq!(error.kind, PdfErrorKind::Engine, "{error}");
}

/// A PDF needing a password to open is its own kind, and the message says what
/// to do; an owner password alone restricts permissions and reads.
#[test]
fn a_password_protected_pdf_is_encrypted() {
    use mupdf::pdf::document::Encryption;
    use mupdf::pdf::{PdfDocument, PdfWriteOptions};

    let locked = |user: &str| {
        let document =
            PdfDocument::from_bytes(&tiny_pdf("Locked text", "Helvetica", 12)).expect("parses");
        let mut options = PdfWriteOptions::default();
        options
            .set_encryption(Encryption::Aes256)
            .set_owner_password("owner")
            .set_user_password(user);
        let mut bytes = Vec::new();
        document
            .write_to_with_options(&mut bytes, options)
            .expect("writes");
        bytes
    };
    let extractor = MupdfExtractor::new();

    let (_guard, path) = write_pdf("locked", &locked("user"));
    for error in [
        extractor.extract_blocks(&path).expect_err("refuses"),
        extractor.page_texts(&path).expect_err("refuses"),
    ] {
        assert_eq!(error, PdfError::encrypted());
        assert_eq!(
            error.to_string(),
            "the PDF is password-protected; remove the password and try again"
        );
    }

    let (_guard, path) = write_pdf("owner-only", &locked(""));
    let pages = extractor.page_texts(&path).expect("reads");
    assert!(pages[0].contains("Locked text"), "{pages:?}");
}

/// The blocks feed `render_html`, so a real PDF's text reaches an `<p>` — the shape
/// `FullTextResult.html` carries for an extracted article.
#[test]
fn a_pdf_reaches_the_html_renderer() {
    let extractor = MupdfExtractor::new();
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
    use bmlib::fulltext::MupdfPdfExtractor;

    let adapter = MupdfPdfExtractor::new();
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
