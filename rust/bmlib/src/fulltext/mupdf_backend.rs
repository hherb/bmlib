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

//! The real PDF backend, over MuPDF.
//!
//! Python extracts with PyMuPDF, which binds MuPDF; this binds the same engine
//! through the `mupdf` crate, so a PDF gives the same lines in both languages
//! (#590). The engine is a C library compiled from source and linked
//! statically, behind the `pdf` feature.
//!
//! # Reading a line as `pdf_converter.py` does
//!
//! `PyMuPDFConverter.extract_blocks` reads `page.get_text("dict")`, whose
//! **spans** are runs of characters sharing one style, and
//! [`line_to_block`](crate::fulltext::pdf_text::line_to_block)'s rule takes the
//! font from the **dominant** span (most non-whitespace characters, ties to the
//! first). The crate exposes characters, not spans, so [`line_spans`] rebuilds
//! them with PyMuPDF's own grouping (`JM_make_spanlist`): a span ends where the
//! size, the font flags, the character flags less `SYNTHETIC`, the colour or the
//! font name changes. The pieces it takes from PyMuPDF:
//!
//! * **The flags**: [`DICT_FLAGS`] is PyMuPDF's `TEXTFLAGS_DICT` and
//!   [`TEXT_FLAGS`] its `TEXTFLAGS_TEXT` (199 and 195). Both clip to the
//!   mediabox, which is what drops a bioRxiv margin line set outside the page.
//! * **Bold and italic** are MuPDF's `fz_font_is_bold`/`fz_font_is_italic`, the
//!   span's `flags` bits 4 and 1, not a guess from the font's name.
//! * **The font name** is MuPDF's bytes read as Latin-1, which is what PyMuPDF's
//!   `raw-unicode-escape` decoding gives for a name with no backslash: a name in
//!   another encoding (EUC-KR, in one corpus PDF) arrives as Latin-1 characters
//!   rather than as an error. It loses a six-letter subset tag
//!   (`ABCDEF+Helvetica`), as `JM_font_name` does.
//! * **The superscript bit** (`detect_super_script`) splits a span as any other
//!   flag does: on a line written left to right, a character whose baseline sits
//!   above the line's first by more than a tenth of its size.
//!
//! Two of `JM_make_spanlist`'s split keys are not read, the crate exposing
//! neither: the bidirectional level (right-to-left text) and the line's `dir`,
//! for which the first character's quad stands in (left to right where its top
//! edge is level). PyMuPDF also sets a rotated page's rotation to 0 before
//! extracting; this reads the page as MuPDF lays it out.

use crate::fulltext::models::TextBlock;
use crate::fulltext::pdf_text::{normalize_line, PdfTextExtractor};
use mupdf::text_page::{TextCharFlags, TextLine, TextPage, TextPageFlags};
use mupdf::{Device, Document, Matrix, Page, Quad, Rect};

/// PyMuPDF's `TEXTFLAGS_DICT` (199), the flags `get_text("dict")` reads with.
pub const DICT_FLAGS: TextPageFlags = TextPageFlags::PRESERVE_LIGATURES
    .union(TextPageFlags::PRESERVE_WHITESPACE)
    .union(TextPageFlags::PRESERVE_IMAGES)
    .union(TextPageFlags::CLIP)
    .union(TextPageFlags::USE_CID_FOR_UNKNOWN_UNICODE);

/// PyMuPDF's `TEXTFLAGS_TEXT` (195), the flags `get_text()` reads with.
pub const TEXT_FLAGS: TextPageFlags = TextPageFlags::PRESERVE_LIGATURES
    .union(TextPageFlags::PRESERVE_WHITESPACE)
    .union(TextPageFlags::CLIP)
    .union(TextPageFlags::USE_CID_FOR_UNKNOWN_UNICODE);

/// PyMuPDF's `TEXT_FONT_SUPERSCRIPT` span-flag bit.
const SUPERSCRIPT_FLAG: i32 = 1;

/// One span of a line: characters sharing one style, as PyMuPDF groups them.
#[derive(Debug, Clone, PartialEq)]
pub struct Span {
    /// The span's text.
    pub text: String,
    /// The font size.
    pub size: f64,
    /// The font name, less a subset tag.
    pub font: String,
    /// PyMuPDF's span `flags`: superscript 1, italic 2, serif 4, monospaced 8,
    /// bold 16.
    pub flags: i32,
}

/// The style key `JM_make_spanlist` splits a span on.
#[derive(Debug, Clone, PartialEq)]
struct Style {
    size: f32,
    flags: i32,
    char_flags: u16,
    argb: u32,
    font: String,
}

/// A font name as PyMuPDF reports it, from MuPDF's bytes: a six-letter subset
/// tag and its `+` removed (`ABCDEF+Helvetica`), and every byte read as one
/// Latin-1 character (see the module docs). The tag is found in the bytes, as
/// `JM_font_name` finds it.
#[must_use]
pub fn pymupdf_font_name(bytes: &[u8]) -> String {
    let name = match bytes.iter().position(|&byte| byte == b'+') {
        Some(6) => &bytes[7..],
        _ => bytes,
    };
    name.iter().map(|&byte| char::from(byte)).collect()
}

/// A font's name as [`pymupdf_font_name`] reports it.
///
/// Read from `fz_font.name` directly, because the crate's `Font::name` unwraps a
/// UTF-8 decode and so panics on a name in any other encoding.
fn font_name(font: &mupdf::Font) -> String {
    use mupdf_sys::fz_font;
    // `Font` is one `*mut fz_font` (`mupdf` 0.8.0, pinned exactly with
    // `mupdf-sys`); a crate that added a field would fail this assertion.
    const _: () =
        assert!(std::mem::size_of::<mupdf::Font>() == std::mem::size_of::<*mut fz_font>());
    // SAFETY: the copy is the pointer `font` holds, valid while `font` is
    // borrowed; it is only read, and the copy is not dropped (`*mut` has no
    // destructor), so the font's reference count is untouched. `name` is a
    // NUL-terminated array MuPDF writes with `fz_strlcpy`.
    let name = unsafe {
        let raw: *mut fz_font = std::mem::transmute_copy(font);
        &(*raw).name
    };
    let bytes: Vec<u8> = name
        .iter()
        .take_while(|&&byte| byte != 0)
        .map(|&byte| byte as u8)
        .collect();
    pymupdf_font_name(&bytes)
}

/// The smallest rectangle holding a quad.
fn quad_rect(quad: &Quad) -> Rect {
    let xs = [quad.ul.x, quad.ur.x, quad.ll.x, quad.lr.x];
    let ys = [quad.ul.y, quad.ur.y, quad.ll.y, quad.lr.y];
    let min = |v: [f32; 4]| v.into_iter().fold(f32::INFINITY, f32::min);
    let max = |v: [f32; 4]| v.into_iter().fold(f32::NEG_INFINITY, f32::max);
    Rect::new(min(xs), min(ys), max(xs), max(ys))
}

/// Whether two rectangles share area, as `JM_rects_overlap` decides: an empty
/// rectangle overlaps nothing.
fn overlaps(a: &Rect, b: &Rect) -> bool {
    !(a.x0 >= b.x1 || a.y0 >= b.y1 || a.x1 <= b.x0 || a.y1 <= b.y0)
}

/// A line's spans, grouped as PyMuPDF's `get_text("dict")` groups them, and the
/// rectangle round the characters kept.
///
/// A character whose box does not overlap `page` is dropped, as
/// `JM_make_spanlist` drops it — which is also what drops a glyph with an empty
/// box. The line's rectangle is the union of the kept characters' boxes, which
/// is how PyMuPDF builds a line's `bbox`; `None` when none was kept.
#[must_use]
pub fn line_spans(line: &TextLine<'_>, page: &Rect) -> (Vec<Span>, Option<Rect>) {
    let mut spans: Vec<Span> = Vec::new();
    let mut bounds: Option<Rect> = None;
    let mut current: Option<Style> = None;
    let mut first: Option<(f32, bool)> = None;
    for ch in line.chars() {
        let quad = ch.quad();
        let rect = quad_rect(&quad);
        if !overlaps(page, &rect) {
            continue;
        }
        bounds = Some(match bounds {
            Some(b) => Rect::new(
                b.x0.min(rect.x0),
                b.y0.min(rect.y0),
                b.x1.max(rect.x1),
                b.y1.max(rect.y1),
            ),
            None => rect,
        });
        let origin_y = ch.origin().y;
        // The line's direction, taken from its first character (see the module
        // docs): left to right where the character's top edge is level.
        let (first_y, horizontal) =
            *first.get_or_insert((origin_y, quad.ul.y == quad.ur.y && quad.ur.x > quad.ul.x));
        let size = ch.size();
        let superscript = horizontal && origin_y < first_y - size * 0.1;
        let (font, font_flags) = match ch.font() {
            Some(font) => {
                let flags = i32::from(font.is_italic()) * 2
                    + i32::from(font.is_serif()) * 4
                    + i32::from(font.is_monospaced()) * 8
                    + i32::from(font.is_bold()) * 16;
                (font_name(&font), flags)
            }
            None => (String::new(), 0),
        };
        let style = Style {
            size,
            flags: font_flags + if superscript { SUPERSCRIPT_FLAG } else { 0 },
            char_flags: (ch.flags() - TextCharFlags::SYNTHETIC).bits(),
            argb: ch.argb(),
            font,
        };
        if current.as_ref() != Some(&style) {
            spans.push(Span {
                text: String::new(),
                size: f64::from(style.size),
                font: style.font.clone(),
                flags: style.flags,
            });
            current = Some(style);
        }
        if let Some(span) = spans.last_mut() {
            span.text
                .push(ch.char().unwrap_or(char::REPLACEMENT_CHARACTER));
        }
    }
    (spans, bounds)
}

/// Collapse one line into a [`TextBlock`], by `line_to_block`'s rules.
///
/// Returns `None` for a line with no non-whitespace text.
#[must_use]
pub fn line_block(line: &TextLine<'_>, page: &Rect, page_num: i64) -> Option<TextBlock> {
    let (spans, bounds) = line_spans(line, page);
    let bounds = bounds?;
    let joined: String = spans.iter().map(|s| s.text.as_str()).collect();
    let text = normalize_line(&joined);
    if text.is_empty() {
        return None;
    }
    let weight = |span: &Span| span.text.chars().filter(|c| !c.is_whitespace()).count();
    // Strictly greater, so a tie keeps the earlier span.
    let dominant = spans.iter().fold(None::<&Span>, |best, span| match best {
        Some(previous) if weight(span) <= weight(previous) => Some(previous),
        _ => Some(span),
    })?;
    Some(TextBlock {
        text,
        page_num,
        font_size: dominant.size,
        font_name: dominant.font.clone(),
        is_bold: dominant.flags & 16 != 0,
        is_italic: dominant.flags & 2 != 0,
        x: f64::from(bounds.x0),
        y: f64::from(bounds.y0),
        width: f64::from(bounds.x1 - bounds.x0),
        height: f64::from(bounds.y1 - bounds.y0),
    })
}

/// A PDF backend over MuPDF.
///
/// It holds nothing: MuPDF's context is the crate's, one per thread, and each
/// call opens the document afresh.
#[derive(Debug, Default, Clone, Copy)]
pub struct MupdfExtractor;

impl MupdfExtractor {
    /// The backend.
    #[must_use]
    pub fn new() -> Self {
        MupdfExtractor
    }
}

/// Open a PDF, refusing one that needs a password, as `extract_blocks` does.
fn open(path: &std::path::Path) -> Result<Document, String> {
    let document =
        Document::open(path).map_err(|e| format!("could not open {}: {e}", path.display()))?;
    if document
        .needs_password()
        .map_err(|e| format!("could not open {}: {e}", path.display()))?
    {
        return Err(format!(
            "could not open {}: the PDF is password-protected",
            path.display()
        ));
    }
    Ok(document)
}

/// Each page's bounds and text, in order, read with `flags`.
///
/// PyMuPDF runs the whole page into its text device (`fz_run_page`), where the
/// crate's `Page::to_text_page` runs the page's contents alone — and a figure
/// label can be a `FreeText` annotation (three, in one corpus PDF). So the
/// annotations and widgets are run into a second device over the same text
/// page, which is `fz_run_page`'s order. (Reading a display list of the whole
/// page instead regroups lines and loses spaces: 43 corpus PDFs moved.) A page
/// is loaded only when it is reached, so a long document is not held whole.
fn text_pages(
    document: &Document,
    flags: TextPageFlags,
) -> Result<impl Iterator<Item = Result<(Rect, TextPage), String>> + '_, String> {
    let count = document
        .page_count()
        .map_err(|e| format!("could not count the pages: {e}"))?;
    Ok((0..count).map(move |index| {
        let read = |page: Page| -> Result<(Rect, TextPage), mupdf::Error> {
            let bounds = page.bounds()?;
            let text_page = page.to_text_page(flags)?;
            // Dropping the device closes it, which flushes its last line.
            let device = Device::from_text_page(&text_page, flags)?;
            page.run_annotations(&device, &Matrix::IDENTITY)?;
            page.run_widgets(&device, &Matrix::IDENTITY)?;
            drop(device);
            Ok((bounds, text_page))
        };
        document
            .load_page(index)
            .and_then(read)
            .map_err(|e| format!("could not read the text of page {index}: {e}"))
    }))
}

impl PdfTextExtractor for MupdfExtractor {
    fn name(&self) -> &'static str {
        "mupdf"
    }

    fn extract_blocks(&self, path: &std::path::Path) -> Result<Vec<TextBlock>, String> {
        let document = open(path)?;
        let mut blocks = Vec::new();
        for (page_index, page) in text_pages(&document, DICT_FLAGS)?.enumerate() {
            let (bounds, text_page) = page?;
            // `lines()` yields nothing for an image block, which is the
            // `type != 0` skip `extract_blocks` makes.
            for block in text_page.blocks() {
                for line in block.lines() {
                    blocks.extend(line_block(&line, &bounds, page_index as i64));
                }
            }
        }
        Ok(blocks)
    }

    fn page_texts(&self, path: &std::path::Path) -> Result<Vec<String>, String> {
        let document = open(path)?;
        let pages = text_pages(&document, TEXT_FLAGS)?
            .map(|page| {
                page.and_then(|(_, text_page)| {
                    text_page
                        .to_text()
                        .map_err(|e| format!("could not read a page's text: {e}"))
                })
            })
            .collect();
        pages
    }
}

// ---------------------------------------------------------------------------
// The adapter the service uses
// ---------------------------------------------------------------------------

/// The MuPDF backend as [`FullTextService`](crate::fulltext::service::FullTextService)
/// consumes one.
///
/// The service declares its own `PdfExtractor` trait so the conversion is
/// injectable, which is what makes its tests run without a PDF library. This is the
/// production implementation of that trait, and it is a separate type from
/// [`MupdfExtractor`] because the two answer different questions: the extractor
/// reports **blocks** for the segmenter, the service wants **HTML for the whole
/// document**.
#[derive(Debug, Default, Clone, Copy)]
pub struct MupdfPdfExtractor {
    extractor: MupdfExtractor,
}

impl MupdfPdfExtractor {
    /// The adapter.
    #[must_use]
    pub fn new() -> Self {
        MupdfPdfExtractor::default()
    }
}

impl crate::fulltext::service::PdfExtractor for MupdfPdfExtractor {
    fn extract(
        &self,
        pdf_path: &std::path::Path,
    ) -> Result<crate::fulltext::service::PdfText, crate::fulltext::service::PdfExtractError> {
        use crate::fulltext::pdf_text::render_html;
        use crate::fulltext::service::{PdfExtractError, PdfText};

        let pages = self
            .extractor
            .page_texts(pdf_path)
            .map_err(PdfExtractError::Conversion)?;
        let joined = pages.join("\n");
        let html = render_html(true, &joined, &pages);
        let char_count = joined.chars().count();
        let page_count = pages.len();

        // **Every page counts as converted**: a page's text is read or the call
        // fails, so there is no per-page partial outcome to report. What can be
        // empty is the document — a scanned PDF has pages and no text, which
        // `char_count` says and `is_complete` reads.
        Ok(PdfText {
            html,
            success: true,
            error_message: None,
            page_count,
            converted_pages: page_count,
            char_count,
            warnings: Vec::new(),
        })
    }
}
