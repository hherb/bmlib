# bmlib — shared library for biomedical literature tools
# Copyright (C) 2024-2026 Dr Horst Herb
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Pluggable PDF-to-text conversion.

A small registry of PDF converters behind a stable :class:`ConversionResult`,
prioritising completeness of extracted text over formatting. The only built-in
backend is :class:`PyMuPDFConverter`, which requires the optional ``pymupdf``
dependency (``pip install bmlib[pdf]``); it is loaded lazily so importing this
module never requires PyMuPDF.

Example::

    from bmlib.fulltext.pdf_converter import get_converter

    result = get_converter("pymupdf").convert(Path("paper.pdf"))
    if result.success and result.is_complete:
        print(result.text)
"""

from __future__ import annotations

import logging
import math
import re
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass, field
from html import escape as html_escape
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from bmlib.fulltext.models import TextBlock

logger = logging.getLogger(__name__)

# Supported converter names.
CONVERTER_PYMUPDF = "pymupdf"
DEFAULT_CONVERTER = CONVERTER_PYMUPDF

# A line recurring on at least this share of pages is furniture — a running
# head, footer, or watermark — rather than article text. Publisher watermarks
# are stripped by this frequency rule alone, so no per-publisher rules are
# needed. Note this is a floor, not the test: ``ceil()`` and the MIN_PAGES
# floor below push the effective share higher on short documents (100% at
# 3 pages, 75% at 4), converging on 0.6 as the page count grows.
REPEATED_LINE_RATIO = 0.6
# Below this many pages the rule cannot tell furniture from a short article
# that simply repeats a phrase, so nothing is stripped. Doubles as the floor
# on the occurrence count itself: nothing counts as furniture on fewer than
# this many pages, whatever the ratio works out to.
REPEATED_LINE_MIN_PAGES = 3
# A line shorter than this fraction of the body's typical width ends a
# paragraph — PDF text wraps hard at the column edge, so a short line is
# where the author, not the layout, stopped.
PARAGRAPH_BREAK_RATIO = 0.85
# Reflow needs the column's wrap width, and takes it from the widest lines so
# that headings and stubs do not drag the estimate down. Below this many lines
# there is no distribution worth a percentile, so the longest line is used.
PARAGRAPH_WIDTH_MIN_LINES = 10

# PyMuPDF span-flag bits (get_text("dict") -> span["flags"]).
_SPAN_BOLD_FLAG = 1 << 4
_SPAN_ITALIC_FLAG = 1 << 1


@dataclass
class ConversionResult:
    """Result of a PDF-to-text conversion — a stable interface across backends."""

    success: bool
    text: str
    format: str  # 'plaintext' or 'markdown'
    page_count: int
    converted_pages: int
    char_count: int
    warnings: list[str] = field(default_factory=list)
    converter_name: str = ""
    converter_version: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    error_message: str | None = None
    # Text of each page that yielded any, in order — kept alongside the joined
    # ``text`` because page boundaries are what let :func:`render_html`
    # recognise repeated furniture. A page with no extractable text (an
    # image-only scan) contributes no entry, so this is NOT indexable by page
    # number and its length can be less than ``page_count``. Empty when a
    # backend cannot report pages separately.
    page_texts: list[str] = field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        """Whether every page was converted and some text was extracted."""
        return self.success and self.page_count == self.converted_pages and self.char_count > 0

    @property
    def completion_ratio(self) -> float:
        """Ratio of converted pages to total pages (0.0 when no pages)."""
        if self.page_count == 0:
            return 0.0
        return self.converted_pages / self.page_count

    def __str__(self) -> str:
        """Return a human-readable summary."""
        status = "SUCCESS" if self.success else "FAILED"
        completeness = "complete" if self.is_complete else "incomplete"
        return (
            f"ConversionResult({status}, {completeness}, "
            f"{self.converted_pages}/{self.page_count} pages, "
            f"{self.char_count} chars, converter={self.converter_name})"
        )


class PDFConverter(ABC):
    """Abstract base class for PDF converters."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Converter name identifier."""
        ...

    @property
    @abstractmethod
    def version(self) -> str:
        """Converter version string."""
        ...

    @abstractmethod
    def convert(self, pdf_path: Path) -> ConversionResult:
        """Convert a PDF to text.

        Args:
            pdf_path: Path to the PDF file.

        Returns:
            A :class:`ConversionResult` with text and metadata.

        Raises:
            FileNotFoundError: If the PDF file does not exist.
            ValueError: If the path is not a PDF file.
        """
        ...

    def validate_pdf_path(self, pdf_path: Path) -> None:
        """Validate that *pdf_path* exists, is a file, and has a ``.pdf`` suffix.

        Raises:
            FileNotFoundError: If the path does not exist.
            ValueError: If the path is not a file or not a PDF.
        """
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")
        if not pdf_path.is_file():
            raise ValueError(f"Path is not a file: {pdf_path}")
        if pdf_path.suffix.lower() != ".pdf":
            raise ValueError(f"File is not a PDF: {pdf_path}")


@runtime_checkable
class LayoutExtractor(Protocol):
    """A converter that can report a PDF's text lines with their layout.

    A protocol rather than an abstract method on :class:`PDFConverter`: a
    backend that cannot report line geometry — an OCR or LLM-based
    converter — is not forced to fake it, and third-party converters
    registered before this protocol existed keep working. Ask
    ``isinstance(converter, LayoutExtractor)``.
    """

    def extract_blocks(self, pdf_path: Path) -> list[TextBlock]:
        """Extract one :class:`TextBlock` per text line, in reading order."""
        ...


def _span_text_weight(span: dict[str, Any]) -> int:
    """Non-whitespace characters a span contributes to its line."""
    return sum(not c.isspace() for c in span.get("text", ""))


def _line_to_block(raw_line: dict[str, Any], page_num: int) -> TextBlock | None:
    """Collapse one PyMuPDF line dict into a :class:`TextBlock`.

    One block per *line*, not per span: PyMuPDF starts a new span at every
    font change, so a heading numbered in a different weight ("2." +
    "Materials and Methods") or a sentence holding an italic gene name
    would otherwise shatter into fragments no anchored heading pattern can
    match. Span text is concatenated, not joined with spaces — spans carry
    their own trailing spaces, and joining would double them. Font
    attributes come from the dominant span (most non-whitespace characters,
    ties to the first), so a superscript reference marker or an inline
    formula cannot restyle the line.

    Returns None for a line with no non-whitespace text.
    """
    spans = raw_line.get("spans", [])
    text = _normalize("".join(span.get("text", "") for span in spans))
    if not text:
        return None
    dominant = max(spans, key=_span_text_weight)
    flags = int(dominant.get("flags", 0))
    x0, y0, x1, y1 = raw_line.get("bbox", (0.0, 0.0, 0.0, 0.0))
    return TextBlock(
        text=text,
        page_num=page_num,
        font_size=float(dominant.get("size", 12.0)),
        font_name=str(dominant.get("font", "")),
        is_bold=bool(flags & _SPAN_BOLD_FLAG),
        is_italic=bool(flags & _SPAN_ITALIC_FLAG),
        x=float(x0),
        y=float(y0),
        width=float(x1 - x0),
        height=float(y1 - y0),
    )


class PyMuPDFConverter(PDFConverter):
    """PDF converter backed by PyMuPDF (``fitz``).

    Prioritises reliability and completeness over formatting. Requires the
    optional ``pymupdf`` dependency.
    """

    def __init__(self) -> None:
        """Load PyMuPDF, raising a helpful ImportError if it is missing."""
        try:
            import fitz
        except ImportError as e:
            raise ImportError(
                "PyMuPDF (fitz) is required for PDF conversion. "
                "Install with: pip install bmlib[pdf]"
            ) from e
        self._fitz = fitz

    @property
    def name(self) -> str:
        """Converter name."""
        return CONVERTER_PYMUPDF

    @property
    def version(self) -> str:
        """PyMuPDF version."""
        return self._fitz.version[0]

    def convert(self, pdf_path: Path) -> ConversionResult:
        """Convert a PDF to plaintext, extracting every page's text.

        All text is extracted even where formatting is imperfect; pages with
        no extractable text are still counted as processed. A PDF needing a
        password to open is a failed result rather than an empty successful
        one — a file that cannot be read is not a paper without text.
        """
        self.validate_pdf_path(pdf_path)

        warnings: list[str] = []
        text_parts: list[str] = []
        converted_pages = 0
        page_count = 0
        metadata: dict[str, Any] = {}

        try:
            # Context manager guarantees the document is closed even when a
            # non-fitz error escapes mid-extraction.
            with self._fitz.open(str(pdf_path)) as doc:
                page_count = len(doc)

                if doc.needs_pass:
                    # PyMuPDF opens an encrypted document without its
                    # password and only fails on use, so every page's
                    # get_text() raises inside the per-page except below and
                    # the file returns as a success with no text — an
                    # unreadable paper indistinguishable from an image-only
                    # scan. Tested on ``needs_pass`` rather than
                    # ``is_encrypted`` because an owner password restricts
                    # permissions without blocking reads: such a file is
                    # encrypted and extracts perfectly.
                    error_msg = "PDF is password-protected"
                    logger.error("PDF conversion failed for %s: %s", pdf_path, error_msg)
                    return ConversionResult(
                        success=False,
                        text="",
                        format="plaintext",
                        page_count=page_count,
                        converted_pages=0,
                        char_count=0,
                        warnings=warnings,
                        converter_name=self.name,
                        converter_version=self.version,
                        error_message=error_msg,
                    )

                try:
                    pdf_metadata = doc.metadata
                    metadata = {
                        "title": pdf_metadata.get("title", ""),
                        "author": pdf_metadata.get("author", ""),
                        "subject": pdf_metadata.get("subject", ""),
                        "keywords": pdf_metadata.get("keywords", ""),
                        "creator": pdf_metadata.get("creator", ""),
                        "producer": pdf_metadata.get("producer", ""),
                        "creation_date": pdf_metadata.get("creationDate", ""),
                        "modification_date": pdf_metadata.get("modDate", ""),
                    }
                except Exception as e:  # noqa: BLE001 — metadata is best-effort
                    warnings.append(f"Failed to extract metadata: {e}")
                    logger.warning("Metadata extraction failed for %s: %s", pdf_path, e)

                for page_num in range(page_count):
                    try:
                        page_text = doc[page_num].get_text()
                        if page_text.strip():
                            text_parts.append(page_text)
                            converted_pages += 1
                        else:
                            # Page processed but has no extractable text (image-only?).
                            warnings.append(f"Page {page_num + 1}: No extractable text")
                            converted_pages += 1
                    except Exception as e:  # noqa: BLE001 — one bad page must not abort the rest
                        warnings.append(f"Page {page_num + 1}: Extraction failed - {e}")
                        logger.warning("Page %d extraction failed: %s", page_num + 1, e)

            full_text = "\n\n".join(text_parts)
            return ConversionResult(
                success=True,
                text=full_text,
                format="plaintext",
                page_count=page_count,
                converted_pages=converted_pages,
                char_count=len(full_text),
                warnings=warnings,
                converter_name=self.name,
                converter_version=self.version,
                metadata=metadata,
                page_texts=list(text_parts),
            )

        except self._fitz.FileDataError as e:
            error_msg = f"Invalid or corrupted PDF: {e}"
            logger.error("PDF conversion failed for %s: %s", pdf_path, error_msg)
            return ConversionResult(
                success=False,
                text="",
                format="plaintext",
                page_count=page_count,
                converted_pages=0,
                char_count=0,
                warnings=warnings,
                converter_name=self.name,
                converter_version=self.version,
                error_message=error_msg,
            )

        except Exception as e:  # noqa: BLE001 — surface any backend failure as a result
            error_msg = f"PDF conversion failed: {e}"
            logger.error("PDF conversion failed for %s: %s", pdf_path, error_msg)
            return ConversionResult(
                success=False,
                text="",
                format="plaintext",
                page_count=page_count,
                converted_pages=converted_pages,
                char_count=0,
                warnings=warnings,
                converter_name=self.name,
                converter_version=self.version,
                error_message=error_msg,
            )

    def extract_blocks(self, pdf_path: Path) -> list[TextBlock]:
        """Extract one :class:`TextBlock` per text line, in reading order.

        Unlike :meth:`convert`, which returns a failed result because
        partial text is still useful, this raises: a partial block list is
        indistinguishable from a sparse PDF, so degradation would be
        silent. A page with no extractable text (an image-only scan)
        contributes no blocks — that is not an error.

        Args:
            pdf_path: Path to the PDF file.

        Returns:
            One block per text line, pages in order, lines in the PDF's
            content-stream order — usually reading order, but a
            multi-column layout whose stream interleaves columns will
            interleave here too, and section boundaries drawn from these
            blocks inherit that ordering.

        Raises:
            FileNotFoundError: If the file does not exist.
            ValueError: If the path is not a PDF file, or the file cannot
                be parsed (corrupt or encrypted).
        """
        self.validate_pdf_path(pdf_path)
        blocks: list[TextBlock] = []
        try:
            with self._fitz.open(str(pdf_path)) as doc:
                if doc.needs_pass:
                    raise ValueError("PDF is password-protected")
                for page_num in range(len(doc)):
                    page_dict = doc[page_num].get_text("dict")
                    for raw_block in page_dict.get("blocks", []):
                        if raw_block.get("type") != 0:  # not a text block (image)
                            continue
                        for raw_line in raw_block.get("lines", []):
                            text_block = _line_to_block(raw_line, page_num)
                            if text_block is not None:
                                blocks.append(text_block)
        except Exception as e:  # noqa: BLE001 — any parse failure becomes ValueError
            raise ValueError(f"Failed to extract text blocks from {pdf_path}: {e}") from e
        return blocks


_WS_RE = re.compile(r"\s+")


def _normalize(line: str) -> str:
    """Collapse runs of whitespace so lines compare on their words alone."""
    return _WS_RE.sub(" ", line).strip()


def _repeated_lines(page_texts: list[str]) -> set[str]:
    """Return the normalised lines that recur across most pages.

    These are running heads, footers and watermarks — layout furniture that
    reads as noise once the pages are concatenated. Counting each line once
    per page keeps a phrase that merely repeats within one page from being
    mistaken for furniture.
    """
    page_count = len(page_texts)
    if page_count < REPEATED_LINE_MIN_PAGES:
        return set()

    counts: Counter[str] = Counter()
    for text in page_texts:
        counts.update({_normalize(line) for line in text.splitlines() if line.strip()})

    threshold = max(REPEATED_LINE_MIN_PAGES, math.ceil(page_count * REPEATED_LINE_RATIO))
    return {line for line, seen_on in counts.items() if seen_on >= threshold}


def _split_on_short_lines(lines: list[str], break_below: float) -> list[str]:
    """Join consecutive lines, ending a paragraph after each short line."""
    paragraphs: list[str] = []
    current: list[str] = []
    for line in lines:
        current.append(line)
        if len(line) < break_below:
            paragraphs.append(" ".join(current))
            current = []
    if current:
        paragraphs.append(" ".join(current))
    return paragraphs


def _group_paragraphs(lines: list[str]) -> list[str]:
    """Join hard-wrapped PDF lines back into paragraphs.

    A PDF carries no paragraph marks: text wraps at the column edge, so
    every line but the last of a paragraph runs nearly the full width. A
    line falling well short of that width is therefore where the paragraph
    ended.
    """
    if not lines:
        return []

    # Estimate the column's wrap width. Sorted descending, index n//10 is the
    # 90th percentile: it discards the longest 10% so that a merged line or a
    # long URL cannot set the width on its own.
    widths = sorted((len(line) for line in lines), reverse=True)
    estimate = widths[len(widths) // 10] if len(widths) >= PARAGRAPH_WIDTH_MIN_LINES else widths[0]
    paragraphs = _split_on_short_lines(lines, estimate * PARAGRAPH_BREAK_RATIO)

    # That estimate assumes at least a tenth of the lines run full width. When
    # they do not — a reference list, a table, a two-column extraction — it
    # lands on a stub line, no line falls short of it, and the document
    # collapses into a single paragraph. The longest line is the better width
    # in that case, so retry with it rather than return one giant block.
    if len(paragraphs) == 1 and estimate < widths[0]:
        paragraphs = _split_on_short_lines(lines, widths[0] * PARAGRAPH_BREAK_RATIO)
    return paragraphs


def render_html(result: ConversionResult) -> str:
    """Render extracted PDF text as readable HTML.

    Strips repeated page furniture, reflows hard-wrapped lines into
    paragraphs and escapes the text. Intended for displaying a PDF-only
    article inline; it recovers the prose, not the layout, so a caller
    should still offer the original PDF for figures and tables.

    Args:
        result: A conversion produced by a :class:`PDFConverter`.

    Returns:
        An HTML fragment of ``<p>`` elements, or an empty string when the
        conversion failed or yielded no text.
    """
    if not result.success or not result.text.strip():
        return ""

    pages = result.page_texts or [result.text]
    furniture = _repeated_lines(pages)

    lines = [
        normalized
        for page in pages
        for line in page.splitlines()
        if (normalized := _normalize(line)) and normalized not in furniture
    ]

    paragraphs = _group_paragraphs(lines)
    return "\n".join(f"<p>{html_escape(p)}</p>" for p in paragraphs if p)


# Registry of available converters. Future backends (pymupdf4llm, docling,
# marker) can register here.
_CONVERTER_REGISTRY: dict[str, type[PDFConverter]] = {
    CONVERTER_PYMUPDF: PyMuPDFConverter,
}


def get_converter(name: str = DEFAULT_CONVERTER) -> PDFConverter:
    """Return an initialised converter by name.

    Args:
        name: Converter name (currently "pymupdf").

    Returns:
        An initialised :class:`PDFConverter`.

    Raises:
        ValueError: If the name is not recognised.
        ImportError: If the converter's optional dependency is missing.
    """
    if name not in _CONVERTER_REGISTRY:
        available = ", ".join(_CONVERTER_REGISTRY.keys())
        raise ValueError(f"Unknown converter: '{name}'. Available converters: {available}")

    return _CONVERTER_REGISTRY[name]()


def list_converters() -> list[str]:
    """Return the names of all registered converters."""
    return list(_CONVERTER_REGISTRY.keys())
