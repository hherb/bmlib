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
import threading
import time
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum
from html import escape as html_escape
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from bmlib.fulltext._titles import accepted_metadata_title
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


class PDFErrorKind(StrEnum):
    """Which side a PDF extraction failure is on (#598).

    ``ENCRYPTED`` and ``UNREADABLE`` are the **file's** (remove the password,
    or pick another file); ``ENGINE`` is **not** (out of memory, a system
    call, a library fault — the same file may read elsewhere or later). A
    failure the backend cannot place is ``ENGINE``, so an unknown cause is
    reported loudly rather than blamed on the file. Rust's ``PdfErrorKind``,
    member for member.
    """

    ENCRYPTED = "encrypted"
    UNREADABLE = "unreadable"
    ENGINE = "engine"
    #: The caller's :attr:`PDFLimit.cancel` was set (#600).
    CANCELLED = "cancelled"
    #: The caller's :attr:`PDFLimit.deadline` passed (#600).
    TIMED_OUT = "timed-out"

    @property
    def is_the_files(self) -> bool:
        """Whether the file is at fault, so another file — not a retry — is the remedy."""
        return self in _FILE_KINDS


_FILE_KINDS = frozenset({PDFErrorKind.ENCRYPTED, PDFErrorKind.UNREADABLE})
_LIMIT_KINDS = frozenset({PDFErrorKind.CANCELLED, PDFErrorKind.TIMED_OUT})

# A deadline further ahead than this (about 31 years) is not a monotonic
# reading meant for one extraction: it is a time.time() one, ~1.7e9 s ahead.
_MONOTONIC_HORIZON = 1e9

_ENCRYPTED_MESSAGE = "the PDF is password-protected; remove the password and try again"


class PDFExtractionError(ValueError):
    """A PDF could not be extracted: its :class:`PDFErrorKind` and a message.

    A ``ValueError``, which is what :meth:`PyMuPDFConverter.extract_blocks`
    raised before it was typed. The message never carries the path read,
    which the caller knows and the user may never have chosen. Rust's
    ``PdfError`` is the same pair.
    """

    def __init__(self, kind: PDFErrorKind, message: str) -> None:
        """Hold the kind beside the message."""
        super().__init__(message)
        # Converted, so a bare string is refused here rather than held as one.
        self.kind = PDFErrorKind(kind)

    @classmethod
    def encrypted(cls) -> PDFExtractionError:
        """The PDF needs a password, and the message says what to do."""
        return cls(PDFErrorKind.ENCRYPTED, _ENCRYPTED_MESSAGE)

    def __reduce__(self) -> tuple[Any, ...]:
        """Pickle with both arguments, which ``BaseException`` would drop."""
        return (type(self), (self.kind, str(self)))


@dataclass(frozen=True)
class PDFLimit:
    """When a PDF extraction should stop, so a caller can bound it (#600).

    The default stops never. :class:`PyMuPDFConverter` checks it **before
    each page**: PyMuPDF's ``get_text()`` takes no MuPDF cookie, so one long
    page runs to its end, where Rust's ``PdfLimit`` stops a page while it
    runs. A stopped extraction is a failure (``CANCELLED`` or ``TIMED_OUT``),
    never a partial result.

    Attributes:
        cancel: Set, from any thread, to cancel.
        deadline: A :func:`time.monotonic` reading past which extraction stops.
    """

    cancel: threading.Event | None = None
    deadline: float | None = None

    def __post_init__(self) -> None:
        """Refuse a limit that would never stop, or would fail as the engine's fault.

        A ``nan`` deadline never compares as passed, and a :func:`time.time`
        reading sits about 1.7e9 s past :func:`time.monotonic`, so either
        would silently stop nothing; a ``cancel`` that is not an ``Event``
        would raise inside the extraction, reported as an engine failure.
        """
        if self.cancel is not None and not isinstance(self.cancel, threading.Event):
            raise TypeError(
                f"PDFLimit.cancel must be a threading.Event, not {type(self.cancel).__name__}"
            )
        if self.deadline is None:
            return
        if isinstance(self.deadline, bool) or not isinstance(self.deadline, (int, float)):
            raise TypeError(
                f"PDFLimit.deadline must be a time.monotonic() reading, "
                f"not {type(self.deadline).__name__}"
            )
        if not math.isfinite(self.deadline):
            raise ValueError(f"PDFLimit.deadline must be finite, not {self.deadline}")
        if self.deadline - time.monotonic() > _MONOTONIC_HORIZON:
            raise ValueError(
                "PDFLimit.deadline is decades away, which is a time.time() reading; "
                "it must be a time.monotonic() one"
            )

    def reached(self) -> PDFExtractionError | None:
        """Why the extraction must stop now, if it must; a cancel outranks a deadline."""
        if self.cancel is not None and self.cancel.is_set():
            return PDFExtractionError(PDFErrorKind.CANCELLED, "the PDF extraction was cancelled")
        if self.deadline is not None and time.monotonic() >= self.deadline:
            return PDFExtractionError(
                PDFErrorKind.TIMED_OUT, "the PDF extraction ran past its deadline"
            )
        return None


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
    # The document's title where page 1 corroborates the metadata's claim to
    # it, else None (issue #56). ``metadata["title"]`` keeps the verbatim
    # original beside this, junk and all — the dict is a record of what the
    # PDF says, and a caller debugging provenance needs it unaltered.
    # Declared after the rest so positional construction stays stable.
    title: str | None = None
    # Which side a failure is on (#598); None when the conversion succeeded.
    # Declared last for the reason ``title`` is.
    error_kind: PDFErrorKind | None = None

    def __post_init__(self) -> None:
        """Refuse a successful result carrying a failure's kind.

        Only that direction: a third-party backend's failed result may not be
        able to place its cause, and ``None`` is then the honest answer.
        """
        if self.success and self.error_kind is not None:
            raise ValueError(f"a successful conversion has no error kind, not {self.error_kind}")

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

            ``ConversionResult.title`` is the metadata title **only where the
            document corroborates it** (issue #56) — callers are told to read
            it in preference to ``metadata["title"]``, which stays verbatim.
            A backend that does not perform that check must leave it ``None``
            rather than copying the raw value in; ``None`` means "no judged
            title", so a caller may then fall back to the raw dict itself.
            See :mod:`bmlib.fulltext._titles`.

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


@dataclass
class PDFBlocks:
    """A PDF's text blocks, and what MuPDF reported while reading them (#599).

    ``warnings`` are MuPDF's own messages, as it words them: a broken xref it
    repaired, a content stream it stopped reading (``too many syntax errors;
    ignoring rest of page``), a font with bogus metrics. MuPDF reads past each
    without failing, so the blocks may be short of the page's text and only
    these say so. The path read is replaced by "the file". Rust's
    ``PdfBlocks`` is the same pair.
    """

    blocks: list[TextBlock]
    warnings: list[str] = field(default_factory=list)


#: MuPDF's ``fz_store_item`` warning when two threads cache the same item at
#: once; it keeps one and reads on, so the message says nothing about the PDF.
#: The item's type is a C identifier (``fz_icc_link``), matched as ASCII.
_STORE_RACE_RE = re.compile(r"found duplicate [A-Za-z0-9_]+ in the store")


def is_about_the_file(message: str) -> bool:
    """Whether a MuPDF message is about the PDF read, not the engine's own cache.

    Every message is, but the store's report of a race between two threads
    (``found duplicate fz_icc_link in the store``), which costs no text.
    Rust's ``is_about_the_file`` is the same rule, and the shared fixture
    ``tests/fixtures/pdf_warnings.json`` pins both.
    """
    return _STORE_RACE_RE.fullmatch(message) is None


def _reset_engine_messages(pymupdf: Any) -> None:
    """Forget what MuPDF has reported so far, before reading one PDF.

    Read rather than only reset, since reading flushes a warning MuPDF has
    counted, which would otherwise swallow this PDF's first identical one.
    """
    pymupdf.TOOLS.mupdf_warnings(reset=True)


def _engine_messages(pymupdf: Any, pdf_path: Path) -> list[str]:
    """What MuPDF reported since :func:`_reset_engine_messages`, each logged at WARNING.

    PyMuPDF keeps MuPDF's warnings and the errors MuPDF reads past in one
    store, which this empties. A repeated warning arrives once, then as
    ``... repeated N times...``, as MuPDF reports it; a repeated error arrives
    each time. A message not :func:`is_about_the_file` is set aside.
    """
    # shortcut: PyMuPDF's store is one per process, so two PDFs read at once
    # on two threads share it, and each may get the other's messages or lose
    # its own; a per-thread store would need PyMuPDF's callbacks replaced.
    # A message holding a newline arrives as two, since the store is read
    # joined by newlines; MuPDF's are one line each.
    messages = [
        message.replace(str(pdf_path), "the file")
        for message in pymupdf.TOOLS.mupdf_warnings().split("\n")
        if message and is_about_the_file(message)
    ]
    for message in messages:
        logger.warning("MuPDF reported while reading %s: %s", pdf_path, message)
    return messages


def _file_codes(mupdf: Any) -> tuple[type, ...]:
    """MuPDF's codes for a fault in the file: ``FORMAT``, ``SYNTAX``, ``UNSUPPORTED``."""
    return (mupdf.FzErrorFormat, mupdf.FzErrorSyntax, mupdf.FzErrorUnsupported)


def _mupdf_cause(e: BaseException, mupdf: Any) -> BaseException | None:
    """The MuPDF error under *e*: itself, its ``__cause__``, or a context not suppressed.

    A ``__context__`` Python marked suppressed (``raise ... from``) is not
    followed, so an error raised while an unrelated MuPDF error was being
    handled does not take that error's code.
    """
    seen: set[int] = set()
    link: BaseException | None = e
    while link is not None and id(link) not in seen:
        seen.add(id(link))
        if isinstance(link, mupdf.FzErrorBase):
            return link
        link = link.__cause__ if link.__suppress_context__ else link.__context__
    return None


def _engine_fault(e: BaseException, pymupdf: Any) -> bool:
    """Whether *e* says positively that the engine failed, not one page.

    A ``MemoryError``, or a MuPDF code that is not the file's. An exception
    carrying neither is not placed here: on one page it costs that page.
    """
    cause = _mupdf_cause(e, pymupdf.mupdf)
    if cause is None:
        return isinstance(e, MemoryError)
    return not isinstance(cause, _file_codes(pymupdf.mupdf))


def _classify(e: BaseException, pymupdf: Any, pdf_path: Path) -> PDFExtractionError:
    """A PyMuPDF failure as a :class:`PDFExtractionError`.

    PyMuPDF chains MuPDF's own error as the cause, and its class is the code:
    ``FzErrorFormat``, ``FzErrorSyntax`` and ``FzErrorUnsupported`` are the
    file's — a garbage file is ``FORMAT``, a truncated one ``SYNTAX``,
    measured — and every other code the engine's, as Rust's ``classify``
    decides. A ``FileDataError`` with no MuPDF cause is the file's: it is
    PyMuPDF's own refusal of an empty file (``EmptyFileError``), which MuPDF,
    and so Rust, reports as ``FORMAT``. Anything else, a ``MemoryError`` or a
    defect in bmlib included, is the engine's, named by its type. PyMuPDF
    names the path in an open failure, and it is removed. A
    :class:`PDFExtractionError` is its own answer, a limit reached among them.
    """
    if isinstance(e, PDFExtractionError):
        return e
    fz_error = _mupdf_cause(e, pymupdf.mupdf)
    if fz_error is not None:
        the_file = isinstance(fz_error, _file_codes(pymupdf.mupdf))
        # MuPDF's own text, as Rust reports it ("no objects found"), rather
        # than PyMuPDF's wrapper naming the file.
        detail = str(getattr(fz_error, "m_text", "") or fz_error)
    elif isinstance(e, pymupdf.FileDataError):
        the_file = True
        detail = str(e)
    else:
        # Not MuPDF's: the type is the one thing that says what was raised.
        the_file = False
        detail = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
    # shortcut: MuPDF cuts a message at 256 bytes, so a path long enough to be
    # cut survives the replace; check the path's length first if one ever does.
    detail = detail.replace(str(pdf_path), "the file")
    if the_file:
        return PDFExtractionError(PDFErrorKind.UNREADABLE, f"the PDF could not be read: {detail}")
    return PDFExtractionError(PDFErrorKind.ENGINE, f"the PDF engine failed: {detail}")


class PyMuPDFConverter(PDFConverter):
    """PDF converter backed by PyMuPDF (``pymupdf``).

    Prioritises reliability and completeness over formatting. Requires the
    optional ``pymupdf`` dependency.
    """

    def __init__(self) -> None:
        """Load PyMuPDF, raising a helpful ImportError if it is missing."""
        try:
            # `pymupdf`, not the legacy `fitz` alias: the `py.typed` marker
            # PyMuPDF added in 1.27.1 is written only into this package, and
            # `setup.py` copies three untyped modules into `fitz/` without
            # it. Importing the alias therefore costs an
            # `# type: ignore[import-untyped]` that no PyMuPDF release can
            # ever retire, and suppresses this module's type checking with
            # it. The `pymupdf` spelling exists from 1.24.3; the extra's
            # floor is well above both.
            import pymupdf
        except ImportError as e:
            raise ImportError(
                f"PyMuPDF is required for PDF conversion ({e}). "
                "Install with: pip install bmlib[pdf]"
            ) from e
        self._pymupdf = pymupdf

    @property
    def name(self) -> str:
        """Converter name."""
        return CONVERTER_PYMUPDF

    @property
    def version(self) -> str:
        """PyMuPDF version."""
        return self._pymupdf.version[0]

    def convert(self, pdf_path: Path, *, limit: PDFLimit | None = None) -> ConversionResult:
        """Convert a PDF to plaintext, extracting every page's text.

        All text is extracted even where formatting is imperfect; pages with
        no extractable text are still counted as processed. A PDF needing a
        password to open is a failed result rather than an empty successful
        one — a file that cannot be read is not a paper without text. A
        ``limit`` reached before a page is a failed result too (#600).
        What MuPDF reported while reading, a page it stopped reading
        included, ends ``warnings`` and is logged at WARNING (#599).
        """
        self.validate_pdf_path(pdf_path)

        warnings: list[str] = []
        text_parts: list[str] = []
        converted_pages = 0
        page_count = 0
        metadata: dict[str, Any] = {}

        _reset_engine_messages(self._pymupdf)
        try:
            # Context manager guarantees the document is closed even when a
            # non-PyMuPDF error escapes mid-extraction.
            with self._pymupdf.open(str(pdf_path)) as doc:
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
                    error = PDFExtractionError.encrypted()
                    logger.error("PDF conversion failed for %s: %s", pdf_path, error)
                    warnings += _engine_messages(self._pymupdf, pdf_path)
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
                        error_message=str(error),
                        error_kind=error.kind,
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

                # Page 1's text, for the title check below. Read here rather
                # than from `page_texts[0]`, which omits a page that yielded
                # nothing and so is page 1 only when page 1 had text — exactly
                # the case `accepted_metadata_title` treats specially.
                #
                # `None` until page 1 is actually read, so a page whose
                # extraction raised — and a document with no pages at all —
                # stays distinguishable from a page that was read and carried
                # no text. The rule accepts an uncorroborated title for the
                # second (an image-only scan, where the metadata is the only
                # title signal) and rejects for the first: a fault is not
                # evidence, and `""` for both would have promoted a junk title
                # on exactly the documents least worth trusting.
                first_page_text: str | None = None
                for page_num in range(page_count):
                    stop = limit.reached() if limit is not None else None
                    if stop is not None:
                        raise stop
                    try:
                        page_text = doc[page_num].get_text()
                        if page_num == 0:
                            first_page_text = page_text
                        if page_text.strip():
                            text_parts.append(page_text)
                            converted_pages += 1
                        else:
                            # Page processed but has no extractable text (image-only?).
                            warnings.append(f"Page {page_num + 1}: No extractable text")
                            converted_pages += 1
                    except Exception as e:  # noqa: BLE001 — one bad page must not abort the rest
                        # An engine fault is not one bad page: out of memory on
                        # page 3 fails the conversion, as Rust's does (#598).
                        if _engine_fault(e, self._pymupdf):
                            raise
                        warnings.append(f"Page {page_num + 1}: Extraction failed - {e}")
                        logger.warning("Page %d extraction failed: %s", page_num + 1, e)

            full_text = "\n\n".join(text_parts)

            # Guarded on its own, so a fault in the title rule can only cost
            # the title. Evaluated inside the `ConversionResult(...)` call it
            # sat in the outer `try`, where anything it raised returned
            # `success=False, text=""` — discarding a complete and correct
            # extraction, and reporting it to the caller as a PDF conversion
            # failure. The metadata block above is guarded for the same reason.
            try:
                judged_title = accepted_metadata_title(metadata, first_page_text)
            except Exception as e:  # noqa: BLE001 — the title must not cost the text
                judged_title = None
                warnings.append(f"Title corroboration failed: {e}")
                logger.warning("Title corroboration failed for %s: %s", pdf_path, e, exc_info=True)

            warnings += _engine_messages(self._pymupdf, pdf_path)
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
                title=judged_title,
            )

        except Exception as e:  # noqa: BLE001 — surface any backend failure as a result
            error = _classify(e, self._pymupdf, pdf_path)
            # A reached limit is what the caller asked for, not a fault.
            level = logging.INFO if error.kind in _LIMIT_KINDS else logging.ERROR
            logger.log(
                level,
                "PDF conversion failed for %s: %s",
                pdf_path,
                error,
                exc_info=error.kind is PDFErrorKind.ENGINE,
            )
            warnings += _engine_messages(self._pymupdf, pdf_path)
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
                error_message=str(error),
                error_kind=error.kind,
            )

    def extract_blocks(self, pdf_path: Path, *, limit: PDFLimit | None = None) -> list[TextBlock]:
        """Extract one :class:`TextBlock` per text line, in reading order.

        Unlike :meth:`convert`, which returns a failed result because
        partial text is still useful, this raises: a partial block list is
        indistinguishable from a sparse PDF, so degradation would be
        silent. A page with no extractable text (an image-only scan)
        contributes no blocks — that is not an error.

        Args:
            pdf_path: Path to the PDF file.
            limit: When to stop, checked before each page (#600).

        Returns:
            One block per text line, pages in order, lines in the PDF's
            content-stream order — usually reading order, but a
            multi-column layout whose stream interleaves columns will
            interleave here too, and section boundaries drawn from these
            blocks inherit that ordering.

        Raises:
            FileNotFoundError: If the file does not exist.
            ValueError: If the path is not a PDF file.
            PDFExtractionError: If the file cannot be read; its ``kind``
                says whether the file (encrypted, unreadable) or the engine
                is at fault, or that ``limit`` was reached (cancelled, timed
                out). A ``ValueError`` too.
        """
        return self.extract_blocks_with_warnings(pdf_path, limit=limit).blocks

    def extract_blocks_with_warnings(
        self, pdf_path: Path, *, limit: PDFLimit | None = None
    ) -> PDFBlocks:
        """:meth:`extract_blocks`, with what MuPDF reported while reading (#599).

        A damaged PDF can read without failing and still lose text: MuPDF
        repairs a broken xref, and stops a page after too many syntax errors,
        reporting each only as a message. Those messages are returned beside
        the blocks and logged at WARNING; on a failure they are logged only.

        Raises:
            As :meth:`extract_blocks`.
        """
        self.validate_pdf_path(pdf_path)
        blocks: list[TextBlock] = []
        _reset_engine_messages(self._pymupdf)
        try:
            with self._pymupdf.open(str(pdf_path)) as doc:
                if doc.needs_pass:
                    raise PDFExtractionError.encrypted()
                for page_num in range(len(doc)):
                    stop = limit.reached() if limit is not None else None
                    if stop is not None:
                        raise stop
                    page_dict = doc[page_num].get_text("dict")
                    for raw_block in page_dict.get("blocks", []):
                        if raw_block.get("type") != 0:  # not a text block (image)
                            continue
                        for raw_line in raw_block.get("lines", []):
                            text_block = _line_to_block(raw_line, page_num)
                            if text_block is not None:
                                blocks.append(text_block)
        except PDFExtractionError:
            _engine_messages(self._pymupdf, pdf_path)
            raise
        except Exception as e:  # noqa: BLE001 — any failure is classified, never lost
            _engine_messages(self._pymupdf, pdf_path)
            raise _classify(e, self._pymupdf, pdf_path) from e
        return PDFBlocks(blocks, _engine_messages(self._pymupdf, pdf_path))


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
