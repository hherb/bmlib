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
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Supported converter names.
CONVERTER_PYMUPDF = "pymupdf"
DEFAULT_CONVERTER = CONVERTER_PYMUPDF


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
            doc = self._fitz.open(str(pdf_path))
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

            doc.close()

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
