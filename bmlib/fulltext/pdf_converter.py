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
from typing import Any

logger = logging.getLogger(__name__)

# Supported converter names.
CONVERTER_PYMUPDF = "pymupdf"
DEFAULT_CONVERTER = CONVERTER_PYMUPDF

# A line recurring on this share of pages is furniture — a running head,
# footer, or watermark — rather than article text. Publisher watermarks
# (medRxiv stamps seven fragments onto every page) are stripped by this
# frequency rule alone, so no per-publisher rules are needed.
REPEATED_LINE_RATIO = 0.6
# Below this many pages the rule cannot tell furniture from a short article
# that simply repeats a phrase, so nothing is stripped.
REPEATED_LINE_MIN_PAGES = 3
# A line shorter than this fraction of the body's typical width ends a
# paragraph — PDF text wraps hard at the column edge, so a short line is
# where the author, not the layout, stopped.
PARAGRAPH_BREAK_RATIO = 0.85


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
    # Per-page text, kept alongside the joined ``text`` because page
    # boundaries are what let :func:`render_html` recognise repeated
    # furniture. Empty when a backend cannot report pages separately.
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
        no extractable text are still counted as processed.
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


def _group_paragraphs(lines: list[str]) -> list[str]:
    """Join hard-wrapped PDF lines back into paragraphs.

    A PDF carries no paragraph marks: text wraps at the column edge, so
    every line but the last of a paragraph runs nearly the full width. A
    line falling well short of that width is therefore where the paragraph
    ended.
    """
    if not lines:
        return []

    # The typical full-width line, measured from the longest lines present so
    # that headings and stub lines do not drag the estimate down.
    widths = sorted((len(line) for line in lines), reverse=True)
    typical_width = widths[len(widths) // 10] if len(widths) >= 10 else widths[0]
    break_below = typical_width * PARAGRAPH_BREAK_RATIO

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
