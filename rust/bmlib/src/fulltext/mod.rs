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

//! Full-text retrieval, JATS parsing and PDF conversion.
//!
//! | Python | Rust | Status |
//! |---|---|---|
//! | `fulltext/jats_parser.py`'s text primitives | [`jats_text`] | ported |
//! | `fulltext/jats_parser.py`'s reader | [`jats_reader`] | ported |

pub mod cache;
pub mod jats_reader;
pub mod jats_text;
pub mod models;
pub mod parse_audit;
pub mod pdf_text;
#[cfg(feature = "pdf")]
pub mod pdfium_backend;
#[cfg(feature = "pdf")]
pub use pdfium_backend::{PdfiumExtractor, PdfiumPdfExtractor};
pub mod segmenter;
pub mod service;
pub mod titles;

pub use cache::{
    default_cache_dir, is_readable, remove_entry, safe_filename, sanitize_identifier,
    FullTextCache, CORRUPT_SUFFIX, MAX_PREFIX_CHARS, PDF_MAGIC_BYTES, TEMP_ROOM,
};
pub use jats_reader::{
    author_full_name, author_is_named, parse, parse_audited, parse_with_pmc_id, JatsError,
    JatsReport,
};
pub use jats_text::{
    delimiter_pair, elocation_part_continues, latex_expression, normalize_whitespace,
    pad_as_deposited, pad_row, render_formula, without_whitespace, LATEX_DELIMITERS,
};
pub use models::{
    ContentKind, FullTextResult, FullTextSourceEntry, JATSAbstractSection, JATSArticle,
    JATSAuthorInfo, JATSBodySection, JATSFigureInfo, JATSFundingAward, JATSFundingSource,
    JATSReferenceInfo, JATSTableInfo, Section, SectionType, SegmentedDocument, TextBlock,
};
pub use parse_audit::{unwind_diagnostics, ParseUnwindState};
pub use pdf_text::{
    converted_content_kind, group_paragraphs, line_to_block, normalize_line, render_html,
    repeated_lines, span_text_weight, split_on_short_lines, ConversionResult, PdfTextExtractor,
    PARAGRAPH_BREAK_RATIO, PARAGRAPH_WIDTH_MIN_LINES, REPEATED_LINE_MIN_PAGES, REPEATED_LINE_RATIO,
    SPAN_BOLD_FLAG, SPAN_ITALIC_FLAG,
};
pub use segmenter::{
    extract_sections, extract_title, identify_section_markers, is_potential_header, join_blocks,
    match_section_type, median_font_size, Marker, SectionSegmenter, DEFAULT_FONT_SIZE,
    FALLBACK_CONFIDENCE, MAX_HEADING_CHARS, PARAGRAPH_GAP_RATIO, PARTIAL_MATCH_CONFIDENCE,
    SECTION_PATTERNS, TITLE_SIZE_RATIO,
};
pub use service::{
    default_cache, entry_is_free, extract_free_pdf_url, html_escape, normalise_pmc_id,
    pick_oa_pdf_url, plural, quote, render_jats_html, FaultKind, FullTextError, FullTextRequest,
    FullTextService, LogLevel, LogLine, PdfExtractError, PdfExtractor, PdfText, TierFailures,
    TierFault, BUG_TYPE_NAMES, DOI_BASE, EUROPE_PMC_BASE, EUTILS_EFETCH_URL, EUTILS_TOOL_NAME,
    FREE_PDF_AVAILABILITY_CODES, FREE_PDF_AVAILABILITY_LABELS, NCBI_IDCONV_URL, PUBMED_BASE,
    TIMEOUT, UNPAYWALL_BASE,
};
pub use titles::{
    accepted_metadata_title, accepted_metadata_title_why, looks_like_junk, normalise,
    page_text_for_matching, strip_combining_marks, TitleRefusal, MAX_LINE_NUMBER_DIGITS,
    MIN_TITLE_WORDS,
};
