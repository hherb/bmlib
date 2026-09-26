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

//! The real PDF backend, over PDFium.
//!
//! This is the port's **one non-Rust dependency**, behind the `pdf` feature, and
//! the plan's §5 prices it. The source uses PyMuPDF; PDFium is the closest
//! equivalent, because both expose the font attributes per span that
//! [`crate::fulltext::pdf_text::line_to_block`] needs to pick the dominant span.
//! A pure-Rust extractor (`lopdf`) would give the text and no font information, so
//! a heading numbered in a different weight would lose its styling and the
//! segmenter's heading tests would read the wrong size — which is the whole reason
//! `pdf_text.rs` exists.
//!
//! # Where this differs from PyMuPDF, and why it is recorded rather than hidden
//!
//! PyMuPDF's `get_text("dict")` hands back a **line** whose `spans` are runs of
//! uniform style, and `line_to_block` concatenates them. PDFium exposes the text
//! as **segments** (a rectangle of text, with its own bounding box) and, per
//! character, the font name, size, weight and italic flag. So this module builds
//! the equivalent line by taking each segment's text and deriving the style from
//! the segment's characters, then lets `line_to_block`'s rules apply unchanged.
//!
//! Three consequences are worth naming, because they are observable:
//!
//! * **Dominance is measured over the segment's characters, not over its spans.**
//!   PyMuPDF's dominant span is the one with most non-whitespace characters;
//!   here the dominant *font* is the one covering most characters. For a line that
//!   is one style, the two agree exactly. For a mixed line they can disagree, and
//!   the character measure is the finer one.
//! * **Bold and italic are read from the font _name_, because PDFium's own
//!   accessors do not work for a base-14 face.** `font_weight()` and
//!   `font_is_italic()` were measured (2026-09-26) over a document built with
//!   `Helvetica-Bold`, `Helvetica-Oblique` and `Helvetica-BoldOblique`: the weight
//!   accessor returned `None` for every one and the italic accessor `false`, while
//!   PyMuPDF reports `bold=True`/`italic=True` on the same file. The **font name**
//!   is correct in both readers (`"Helvetica-Bold"`, `"Helvetica-BoldOblique"`), so
//!   a `Bold`/`Black`/`Heavy` name — and an `Italic`/`Oblique` one — is what
//!   decides. The weight is still consulted, and only as a **fallback** for a face
//!   whose name says nothing (`HelveticaNeue-Medium` at weight 700).
//! * **A face whose name says neither and whose weight is unreadable reads as
//!   regular.** That is the safe direction: a bold heading read as regular loses a
//!   heading a style-based rule would find, while the reverse promotes body text.

use crate::fulltext::models::TextBlock;
use crate::fulltext::pdf_text::{normalize_line, PdfTextExtractor};

/// The weight at or above which a face reads as bold, **as a fallback** only.
///
/// 700 is the usual "Bold" and 600 "Semibold"; the floor is 600 rather than 700 so
/// a semibold heading is not lost. It is consulted only when the font's *name*
/// says nothing, because [`font_name_is_bold`] is what works on a base-14 face —
/// see the module docs for the measurement.
pub const BOLD_WEIGHT_FLOOR: u32 = 600;

/// Font-name fragments that mean a bold or heavier face.
pub const BOLD_NAME_FRAGMENTS: &[&str] = &["bold", "black", "heavy", "semibold", "demibold"];

/// Font-name fragments that mean an oblique or italic face.
pub const ITALIC_NAME_FRAGMENTS: &[&str] = &["italic", "oblique"];

/// Whether a font name says the face is bold or heavier.
///
/// Case-insensitive, since a subset font's name may be `HELVETICA-BOLD` or
/// `ArialMT,Bold`. `BoldOblique` matches, which is right: it is both.
#[must_use]
pub fn font_name_is_bold(name: &str) -> bool {
    let lowered = name.to_ascii_lowercase();
    BOLD_NAME_FRAGMENTS.iter().any(|f| lowered.contains(f))
}

/// Whether a font name says the face is italic or oblique.
#[must_use]
pub fn font_name_is_italic(name: &str) -> bool {
    let lowered = name.to_ascii_lowercase();
    ITALIC_NAME_FRAGMENTS.iter().any(|f| lowered.contains(f))
}

/// A PDF backend over PDFium.
pub struct PdfiumExtractor {
    pdfium: pdfium_render::prelude::Pdfium,
}

impl std::fmt::Debug for PdfiumExtractor {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("PdfiumExtractor")
    }
}

impl PdfiumExtractor {
    /// Bind the bundled PDFium.
    ///
    /// # Errors
    ///
    /// The binding failure, as text: the library may be absent, or its cache
    /// directory unwritable. `PDFIUM_BUNDLED_CACHE_DIR` relocates that cache, which
    /// matters in a sandbox that denies the platform default.
    pub fn bundled() -> Result<Self, String> {
        match pdfium_bundled::bind_bundled() {
            Ok(pdfium) => Ok(PdfiumExtractor { pdfium }),
            Err(error) => Err(format!("could not bind PDFium: {error}")),
        }
    }

    /// Bind a PDFium library already on the system.
    ///
    /// # Errors
    ///
    /// The binding failure, as text.
    pub fn system() -> Result<Self, String> {
        use pdfium_render::prelude::Pdfium;
        match Pdfium::bind_to_system_library() {
            Ok(bindings) => Ok(PdfiumExtractor {
                pdfium: Pdfium::new(bindings),
            }),
            Err(error) => Err(format!("could not bind the system PDFium: {error}")),
        }
    }
}

impl PdfTextExtractor for PdfiumExtractor {
    fn name(&self) -> &'static str {
        "pdfium"
    }

    fn extract_blocks(&self, path: &std::path::Path) -> Result<Vec<TextBlock>, String> {
        let document = self
            .pdfium
            .load_pdf_from_file(path, None)
            .map_err(|e| format!("could not open {}: {e}", path.display()))?;

        let mut blocks = Vec::new();
        for (page_index, page) in document.pages().iter().enumerate() {
            let text = page
                .text()
                .map_err(|e| format!("could not read the text of page {page_index}: {e}"))?;

            for segment in text.segments().iter() {
                let raw = segment.text();
                let normalized = normalize_line(&raw);
                if normalized.is_empty() {
                    continue;
                }

                // The segment's own characters carry the font attributes. Copied
                // into owned values because the iterator borrows the page's text,
                // which is dropped at the end of this iteration.
                let characters: Vec<CharacterStyle> = match segment.chars() {
                    Ok(chars) => chars
                        .iter()
                        .map(|c| CharacterStyle {
                            font_name: c.font_name(),
                            font_size: c.scaled_font_size().value as f64,
                            weight: c.font_weight().map(weight_value),
                            is_italic: c.font_is_italic(),
                        })
                        .collect(),
                    Err(_) => Vec::new(),
                };
                // Dominance over the font **name**, ties to the first seen — the
                // same rule the source applies to spans.
                let font_name = dominant_font(&characters).unwrap_or_default();
                let font_size = characters.first().map(|c| c.font_size).unwrap_or(12.0);
                // The **name decides**, with the weight as a fallback: PDFium's
                // weight and italic accessors return nothing usable for a base-14
                // face, which the module docs record with the measurement.
                let is_bold = font_name_is_bold(&font_name)
                    || characters
                        .iter()
                        .find_map(|c| c.weight)
                        .is_some_and(|w| w >= BOLD_WEIGHT_FLOOR);
                let is_italic =
                    font_name_is_italic(&font_name) || characters.iter().any(|c| c.is_italic);

                let bounds = segment.bounds();

                // A paragraph mark inside a segment would otherwise carry into the
                // next block, so each is its own block — which is also what the
                // source's one-block-per-line rule does.
                for line in normalized.split('\n') {
                    let line = line.trim();
                    if line.is_empty() {
                        continue;
                    }
                    blocks.push(TextBlock {
                        text: line.to_string(),
                        page_num: page_index as i64,
                        font_size,
                        font_name: font_name.clone(),
                        is_bold,
                        is_italic,
                        x: bounds.left().value as f64,
                        y: bounds.top().value as f64,
                        width: bounds.width().value as f64,
                        height: bounds.height().value as f64,
                    });
                }
            }
        }
        Ok(blocks)
    }

    fn page_texts(&self, path: &std::path::Path) -> Result<Vec<String>, String> {
        let document = self
            .pdfium
            .load_pdf_from_file(path, None)
            .map_err(|e| format!("could not open {}: {e}", path.display()))?;
        let mut pages = Vec::new();
        for (index, page) in document.pages().iter().enumerate() {
            let text = page
                .text()
                .map_err(|e| format!("could not read the text of page {index}: {e}"))?;
            pages.push(text.all());
        }
        Ok(pages)
    }
}

/// One character's style, copied out of the page.
///
/// A struct rather than a borrowed `PdfPageTextChar` because the character
/// iterator borrows the page's text, which does not outlive one segment.
#[derive(Debug, Clone)]
struct CharacterStyle {
    font_name: String,
    font_size: f64,
    weight: Option<u32>,
    is_italic: bool,
}

/// The font name covering most of the characters, ties to the first seen.
fn dominant_font(characters: &[CharacterStyle]) -> Option<String> {
    let mut counts: Vec<(String, usize)> = Vec::new();
    for character in characters {
        let name = &character.font_name;
        match counts.iter_mut().find(|(existing, _)| existing == name) {
            Some((_, count)) => *count += 1,
            None => counts.push((name.clone(), 1)),
        }
    }
    counts
        .into_iter()
        .fold(
            None,
            |best: Option<(String, usize)>, candidate| match best {
                // Strictly greater, so a tie keeps the earlier font.
                Some((_, count)) if candidate.1 <= count => best,
                _ => Some(candidate),
            },
        )
        .map(|(name, _)| name)
}

/// A font weight as a number, for the bold floor.
fn weight_value(weight: pdfium_render::prelude::PdfFontWeight) -> u32 {
    use pdfium_render::prelude::PdfFontWeight as W;
    match weight {
        W::Weight100 => 100,
        W::Weight200 => 200,
        W::Weight300 => 300,
        W::Weight400Normal => 400,
        W::Weight500 => 500,
        W::Weight600 => 600,
        W::Weight700Bold => 700,
        W::Weight800 => 800,
        W::Weight900 => 900,
        W::Custom(value) => value,
    }
}

// ---------------------------------------------------------------------------
// The adapter the service uses
// ---------------------------------------------------------------------------

/// The PDFium backend as [`FullTextService`](crate::fulltext::service::FullTextService)
/// consumes one.
///
/// The service declares its own `PdfExtractor` trait so the conversion is
/// injectable, which is what makes its tests run without a PDF library. This is the
/// production implementation of that trait, and it is a separate type from
/// [`PdfiumExtractor`] because the two answer different questions: the extractor
/// reports **blocks** for the segmenter, the service wants **HTML for the whole
/// document**.
#[derive(Debug)]
pub struct PdfiumPdfExtractor {
    extractor: PdfiumExtractor,
}

impl PdfiumPdfExtractor {
    /// Bind the bundled PDFium.
    ///
    /// # Errors
    ///
    /// As [`PdfiumExtractor::bundled`].
    pub fn bundled() -> Result<Self, String> {
        Ok(PdfiumPdfExtractor {
            extractor: PdfiumExtractor::bundled()?,
        })
    }

    /// Bind a system PDFium.
    ///
    /// # Errors
    ///
    /// As [`PdfiumExtractor::system`].
    pub fn system() -> Result<Self, String> {
        Ok(PdfiumPdfExtractor {
            extractor: PdfiumExtractor::system()?,
        })
    }
}

impl crate::fulltext::service::PdfExtractor for PdfiumPdfExtractor {
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

        // **Every page counts as converted, and that is honest here rather than
        // optimistic**: PDFium either reads a page's text or fails the call, so
        // there is no per-page partial outcome to report. What can be empty is the
        // document — a scanned PDF has pages and no text, which `char_count` says
        // and `is_complete` reads.
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
