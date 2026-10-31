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

"""Tests for bmlib.fulltext.pdf_converter — pluggable PDF→text conversion."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from bmlib.fulltext.models import SectionType
from bmlib.fulltext.pdf_converter import (
    ConversionResult,
    LayoutExtractor,
    PDFConverter,
    PDFErrorKind,
    PDFExtractionError,
    PDFLimit,
    PyMuPDFConverter,
    _line_to_block,
    get_converter,
    list_converters,
    render_html,
)
from bmlib.fulltext.segmenter import SectionSegmenter

_HAS_PYMUPDF = importlib.util.find_spec("pymupdf") is not None


def _write_encrypted_pdf(path: Path, *, user_pw: str = "", owner_pw: str = "") -> Path:
    """Write a one-page AES-256 encrypted PDF and return its path.

    A *user* password is required to open the file at all; an *owner*
    password only restricts permissions, leaving the document readable.
    """
    import pymupdf

    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Confidential trial results")
    doc.save(
        str(path),
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        user_pw=user_pw,
        owner_pw=owner_pw,
    )
    doc.close()
    return path


class _StubConverter(PDFConverter):
    """Minimal concrete converter to exercise the shared ABC methods."""

    @property
    def name(self) -> str:
        return "stub"

    @property
    def version(self) -> str:
        return "0"

    def convert(self, pdf_path: Path) -> ConversionResult:  # pragma: no cover - unused
        raise NotImplementedError


class TestConversionResult:
    def test_is_complete_true(self):
        result = ConversionResult(
            success=True,
            text="abc",
            format="plaintext",
            page_count=3,
            converted_pages=3,
            char_count=3,
        )
        assert result.is_complete is True

    def test_is_complete_false_when_pages_missing(self):
        result = ConversionResult(
            success=True,
            text="abc",
            format="plaintext",
            page_count=3,
            converted_pages=2,
            char_count=3,
        )
        assert result.is_complete is False

    def test_is_complete_false_when_no_chars(self):
        result = ConversionResult(
            success=True,
            text="",
            format="plaintext",
            page_count=1,
            converted_pages=1,
            char_count=0,
        )
        assert result.is_complete is False

    def test_completion_ratio(self):
        result = ConversionResult(
            success=True,
            text="x",
            format="plaintext",
            page_count=4,
            converted_pages=1,
            char_count=1,
        )
        assert result.completion_ratio == 0.25

    def test_completion_ratio_zero_pages(self):
        result = ConversionResult(
            success=False,
            text="",
            format="plaintext",
            page_count=0,
            converted_pages=0,
            char_count=0,
        )
        assert result.completion_ratio == 0.0

    def test_str_summary(self):
        result = ConversionResult(
            success=True,
            text="x",
            format="plaintext",
            page_count=1,
            converted_pages=1,
            char_count=1,
            converter_name="pymupdf",
        )
        text = str(result)
        assert "SUCCESS" in text
        assert "pymupdf" in text


class TestPDFExtractionError:
    """The typed failure both PDF paths report (#598)."""

    def test_it_pickles_with_its_kind(self):
        import pickle

        error = PDFExtractionError(PDFErrorKind.ENGINE, "the PDF engine failed: x")
        copy = pickle.loads(pickle.dumps(error))
        assert copy.kind is PDFErrorKind.ENGINE
        assert str(copy) == str(error)

    def test_the_kinds_are_the_shared_fixtures(self):
        # Rust's PdfErrorKind asserts the same file, member for member.
        import json

        fixture = Path(__file__).parent / "fixtures" / "pdf_error_kinds.json"
        rows = json.loads(fixture.read_text(encoding="utf-8"))["kinds"]
        assert [(k.value, k.is_the_files) for k in PDFErrorKind] == [
            (row["name"], row["the_files"]) for row in rows
        ]

    def test_a_bare_string_kind_is_converted_and_a_wrong_one_refused(self):
        assert PDFExtractionError("engine", "x").kind is PDFErrorKind.ENGINE
        with pytest.raises(ValueError):
            PDFExtractionError("the-engine", "x")


class TestConversionResultKind:
    def test_a_success_carrying_a_failure_kind_is_refused(self):
        with pytest.raises(ValueError, match="no error kind"):
            ConversionResult(
                success=True,
                text="t",
                format="plaintext",
                page_count=1,
                converted_pages=1,
                char_count=1,
                error_kind=PDFErrorKind.ENGINE,
            )

    def test_a_failure_without_a_kind_is_allowed(self):
        # A third-party backend may not be able to place its cause.
        result = ConversionResult(
            success=False,
            text="",
            format="plaintext",
            page_count=0,
            converted_pages=0,
            char_count=0,
        )
        assert result.error_kind is None


class TestPDFLimit:
    """When an extraction should stop (#600)."""

    def test_the_default_never_stops(self):
        assert PDFLimit().reached() is None

    def test_a_cancel_outranks_a_passed_deadline(self):
        import threading
        import time

        cancel = threading.Event()
        cancel.set()
        stop = PDFLimit(cancel=cancel, deadline=time.monotonic() - 1).reached()
        assert stop is not None and stop.kind is PDFErrorKind.CANCELLED

    def test_a_deadline_stops_once_passed(self):
        import threading
        import time

        assert PDFLimit(cancel=threading.Event(), deadline=time.monotonic() + 60).reached() is None
        stop = PDFLimit(deadline=time.monotonic() - 1).reached()
        assert stop is not None and stop.kind is PDFErrorKind.TIMED_OUT
        assert str(stop) == "the PDF extraction ran past its deadline"

    @pytest.mark.parametrize(
        ("kwargs", "error"),
        [
            pytest.param({"deadline": float("nan")}, ValueError, id="nan"),
            pytest.param({"deadline": float("inf")}, ValueError, id="inf"),
            pytest.param({"deadline": True}, TypeError, id="bool"),
            pytest.param({"deadline": "soon"}, TypeError, id="str"),
            pytest.param({"cancel": True}, TypeError, id="cancel-bool"),
        ],
    )
    def test_a_limit_that_could_not_work_is_refused(self, kwargs, error):
        # Each would stop nothing silently, or raise mid-extraction as an
        # engine fault.
        with pytest.raises(error):
            PDFLimit(**kwargs)

    def test_a_wall_clock_deadline_is_refused(self):
        import time

        with pytest.raises(ValueError, match="time.monotonic"):
            PDFLimit(deadline=time.time() + 60)
        assert PDFLimit(deadline=time.monotonic() + 3600).reached() is None


class TestRegistry:
    def test_list_converters_includes_pymupdf(self):
        assert "pymupdf" in list_converters()

    def test_unknown_converter_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown converter"):
            get_converter("does-not-exist")

    @pytest.mark.skipif(_HAS_PYMUPDF, reason="PyMuPDF is installed")
    def test_pymupdf_requires_dependency(self):
        with pytest.raises(ImportError, match="PyMuPDF"):
            get_converter("pymupdf")


class TestValidatePdfPath:
    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            _StubConverter().validate_pdf_path(tmp_path / "nope.pdf")

    def test_directory_raises(self, tmp_path):
        with pytest.raises(ValueError, match="not a file"):
            _StubConverter().validate_pdf_path(tmp_path)

    def test_non_pdf_raises(self, tmp_path):
        f = tmp_path / "notes.txt"
        f.write_text("hello")
        with pytest.raises(ValueError, match="not a PDF"):
            _StubConverter().validate_pdf_path(f)

    def test_valid_pdf_path_passes(self, tmp_path):
        f = tmp_path / "paper.pdf"
        f.write_bytes(b"%PDF-1.4 stub")
        # Should not raise (content is not validated here, only the path).
        _StubConverter().validate_pdf_path(f)


@pytest.mark.skipif(not _HAS_PYMUPDF, reason="PyMuPDF not installed")
class TestPyMuPDFConversion:
    def test_converts_generated_pdf(self, tmp_path):
        import pymupdf

        pdf_path = tmp_path / "gen.pdf"
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Hello bmlib PDF")
        doc.save(str(pdf_path))
        doc.close()

        result = get_converter("pymupdf").convert(pdf_path)
        assert result.success is True
        assert result.page_count == 1
        assert "Hello bmlib PDF" in result.text

    def test_corrupt_pdf_returns_failure(self, tmp_path):
        pdf_path = tmp_path / "corrupt.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 this is not a valid pdf body")

        result = get_converter("pymupdf").convert(pdf_path)
        assert result.success is False
        assert result.text == ""
        assert result.error_kind is PDFErrorKind.UNREADABLE
        assert result.error_message.startswith("the PDF could not be read: ")
        assert "corrupt.pdf" not in result.error_message

    def test_a_password_protected_pdf_is_a_failed_conversion(self, tmp_path):
        # PyMuPDF opens an encrypted file without the password, then fails
        # every page's get_text() inside the per-page except. Reported as
        # success, that reads as "this paper has no text" rather than "this
        # file could not be read".
        pdf_path = _write_encrypted_pdf(tmp_path / "locked.pdf", user_pw="user")

        result = get_converter("pymupdf").convert(pdf_path)
        assert result.success is False
        assert result.text == ""
        assert result.error_kind is PDFErrorKind.ENCRYPTED
        assert result.error_message == (
            "the PDF is password-protected; remove the password and try again"
        )

    def test_an_owner_password_alone_does_not_block_conversion(self, tmp_path):
        # Negative control isolating the guard from `is_encrypted`. An
        # owner password restricts permissions but not reading: the
        # document opens and its text extracts, so this file is encrypted
        # and perfectly convertible.
        pdf_path = _write_encrypted_pdf(tmp_path / "restricted.pdf", owner_pw="owner")

        result = get_converter("pymupdf").convert(pdf_path)
        assert result.success is True
        assert result.error_kind is None
        assert "Confidential trial results" in result.text


class TestRenderHTML:
    """Rendering extracted PDF text as readable HTML."""

    @staticmethod
    def _result(pages: list[str]) -> ConversionResult:
        text = "\n\n".join(pages)
        return ConversionResult(
            success=True,
            text=text,
            format="plaintext",
            page_count=len(pages),
            converted_pages=len(pages),
            char_count=len(text),
            page_texts=pages,
        )

    def test_strips_repeated_page_furniture(self):
        """Watermarks and running heads repeat on every page; article text does not."""
        pages = [
            f"medRxiv preprint\nCC-BY 4.0 International license\nUnique sentence number {n}."
            for n in range(5)
        ]
        html = render_html(self._result(pages))

        assert "medRxiv preprint" not in html
        assert "CC-BY 4.0" not in html
        for n in range(5):
            assert f"Unique sentence number {n}." in html

    def test_keeps_repeats_in_short_documents(self):
        """Under three pages, a repeat is as likely to be prose as furniture."""
        pages = ["Repeated line.\nFirst.", "Repeated line.\nSecond."]
        html = render_html(self._result(pages))

        assert "Repeated line." in html

    def test_reflows_hard_wrapped_lines(self):
        """Lines wrapped at the column edge rejoin; a short line ends the paragraph."""
        page = (
            "This opening line runs the full width of the column and wraps\n"
            "onward across a second line of the very same paragraph here\n"
            "and stops.\n"
            "A new full-width line begins the following separate paragraph\n"
            "and ends.\n"
        )
        html = render_html(self._result([page]))

        assert "wraps onward across a second line" in html
        assert html.count("<p>") == 2

    def test_escapes_markup(self):
        html = render_html(self._result(["Effect of <T> & <U> on outcome measured here."]))

        assert "&lt;T&gt; &amp; &lt;U&gt;" in html
        assert "<T>" not in html

    def test_empty_for_failed_conversion(self):
        failed = ConversionResult(
            success=False,
            text="",
            format="plaintext",
            page_count=0,
            converted_pages=0,
            char_count=0,
        )
        assert render_html(failed) == ""

    def test_falls_back_to_joined_text_without_page_texts(self):
        """A backend that cannot report pages still renders, just unstripped."""
        result = ConversionResult(
            success=True,
            text="Only the joined text is available here for rendering.",
            format="plaintext",
            page_count=1,
            converted_pages=1,
            char_count=53,
        )
        assert "Only the joined text" in render_html(result)

    def test_furniture_needs_a_majority_of_pages(self):
        """Pins REPEATED_LINE_RATIO from both sides.

        Over ten pages the threshold is six. A running head on six pages is
        furniture; a sentence that happens to recur on five is not.
        """
        pages = []
        for n in range(10):
            lines = []
            if n < 6:  # six of ten — at the threshold, so furniture
                lines.append("Running head on most pages")
            if n < 5:  # five of ten — below it, so prose
                lines.append("Recurring methods note.")
            lines.append(f"Unique sentence number {n}.")
            pages.append("\n".join(lines))
        html = render_html(self._result(pages))

        assert "Running head on most pages" not in html
        assert "Recurring methods note." in html

    def test_three_pages_is_enough_to_strip_furniture(self):
        """Pins REPEATED_LINE_MIN_PAGES at exactly its boundary.

        Two pages are covered by ``test_keeps_repeats_in_short_documents``;
        three is where the rule starts applying.
        """
        pages = [f"Watermark line\nUnique sentence number {n}." for n in range(3)]
        html = render_html(self._result(pages))

        assert "Watermark line" not in html
        for n in range(3):
            assert f"Unique sentence number {n}." in html

    def test_an_overlong_line_does_not_set_the_wrap_width(self):
        """Pins the 90th-percentile estimate against using the maximum.

        One merged or unwrapped line is an outlier. Taken as the column
        width it would put every real line "short", breaking each onto its
        own paragraph.
        """
        wide = "W" * 200
        full = "F" * 70
        short = "S" * 30
        lines = [wide] + [full] * 4 + [short] + [full] * 4 + [short]
        html = render_html(self._result(["\n".join(lines)]))

        assert html.count("<p>") == 2

    def test_a_minority_of_full_width_lines_still_reflows(self):
        """Regression: the document used to collapse into one paragraph.

        A reference list or table leaves few full-width lines, so the
        percentile estimate lands on a stub, nothing falls short of it, and
        every line was joined into a single block.
        """
        lines = ["W" * 90] * 5 + ["short entry"] * 95
        html = render_html(self._result(["\n".join(lines)]))

        assert html.count("<p>") > 50

    def test_a_line_well_short_of_the_column_ends_a_paragraph(self):
        """Pins PARAGRAPH_BREAK_RATIO.

        A last line rarely stops just a character or two early, so the
        threshold has to sit well below the column width — but high enough
        that a line at 70% of it still reads as the end of a paragraph.
        """
        full = "F" * 100
        seventy_percent = "S" * 70
        lines = [full] * 4 + [seventy_percent] + [full] * 4 + [seventy_percent]
        html = render_html(self._result(["\n".join(lines)]))

        assert html.count("<p>") == 2

    def test_a_page_of_pure_furniture_renders_nothing(self):
        """Every line stripped leaves no prose to render, not a stray tag."""
        pages = ["Watermark\nRunning head"] * 3
        assert render_html(self._result(pages)) == ""


class TestLayoutExtractorProtocol:
    def test_pymupdf_converter_implements_the_protocol(self):
        assert issubclass(PyMuPDFConverter, LayoutExtractor)

    def test_a_converter_without_extract_blocks_does_not(self):
        # Negative control: shows the check actually discriminates rather
        # than trivially passing for any PDFConverter subclass.
        assert not issubclass(_StubConverter, LayoutExtractor)


class TestLineToBlock:
    """_line_to_block over the dict shape PyMuPDF's get_text("dict") emits."""

    @staticmethod
    def _line(spans, bbox=(72.0, 100.0, 300.0, 112.0)):
        return {"spans": spans, "bbox": bbox}

    @staticmethod
    def _span(text, size=10.0, font="Helvetica", flags=0):
        return {"text": text, "size": size, "font": font, "flags": flags}

    def test_a_heading_split_across_spans_is_one_block(self):
        # PyMuPDF starts a new span at every font change; upstream flattened
        # to spans, so "2." and "Materials and Methods" were separate blocks
        # and the anchored heading pattern could never match.
        line = self._line(
            [
                self._span("2. ", size=12.0),
                self._span("Materials and Methods", size=12.0, font="Helvetica-Bold", flags=16),
            ]
        )
        result = _line_to_block(line, page_num=0)
        assert result is not None
        assert result.text == "2. Materials and Methods"

    def test_font_attributes_come_from_the_dominant_span(self):
        line = self._line(
            [
                self._span("2. ", size=12.0),
                self._span("Materials and Methods", size=14.0, font="Helvetica-Bold", flags=16),
            ]
        )
        result = _line_to_block(line, page_num=0)
        assert result.is_bold is True
        assert result.font_name == "Helvetica-Bold"
        assert result.font_size == 14.0

    def test_a_superscript_marker_does_not_restyle_the_line(self):
        line = self._line(
            [
                self._span("Aspirin reduced mortality", size=10.0),
                self._span("1", size=6.0, flags=1),  # superscript reference marker
            ]
        )
        result = _line_to_block(line, page_num=0)
        assert result.font_size == 10.0
        assert result.is_bold is False

    def test_span_text_is_concatenated_not_double_spaced(self):
        # Span text carries its own trailing spaces; joining with " " would
        # double them, and the whitespace collapse must repair any that
        # PyMuPDF already carries.
        line = self._line([self._span("word "), self._span("next")])
        assert _line_to_block(line, page_num=0).text == "word next"

    def test_an_empty_line_is_none(self):
        assert _line_to_block(self._line([]), page_num=0) is None
        assert _line_to_block(self._line([self._span("   ")]), page_num=0) is None

    def test_geometry_comes_from_the_line_bbox(self):
        result = _line_to_block(
            self._line([self._span("text")], bbox=(72.0, 100.0, 300.0, 112.0)), page_num=3
        )
        assert (result.x, result.y) == (72.0, 100.0)
        assert (result.width, result.height) == (228.0, 12.0)
        assert result.page_num == 3


@pytest.mark.skipif(not _HAS_PYMUPDF, reason="PyMuPDF not installed")
class TestExtractBlocks:
    @staticmethod
    def _write_pdf(path, pages):
        """Write a PDF; *pages* is a list of (x, y, text, fontname, fontsize) lists."""
        import pymupdf

        doc = pymupdf.open()
        for page_items in pages:
            page = doc.new_page()
            for x, y, text, fontname, fontsize in page_items:
                page.insert_text((x, y), text, fontname=fontname, fontsize=fontsize)
        doc.save(str(path))
        doc.close()

    def test_each_line_is_one_block_on_its_page(self, tmp_path):
        pdf = tmp_path / "two_pages.pdf"
        self._write_pdf(
            pdf,
            [
                [
                    (72, 100, "Methods", "hebo", 14),
                    (72, 130, "We randomised patients.", "helv", 10),
                ],
                [(72, 100, "Results", "hebo", 14)],
            ],
        )
        blocks = get_converter("pymupdf").extract_blocks(pdf)
        texts = [(b.text, b.page_num) for b in blocks]
        assert ("Methods", 0) in texts
        assert ("We randomised patients.", 0) in texts
        assert ("Results", 1) in texts

    def test_a_real_pdf_heading_in_mixed_fonts_is_one_line(self, tmp_path):
        import pymupdf

        pdf = tmp_path / "mixed.pdf"
        doc = pymupdf.open()
        page = doc.new_page()
        # Same baseline, two fonts — PyMuPDF reports two spans in one line.
        page.insert_text((72, 100), "2. ", fontname="helv", fontsize=14)
        page.insert_text((92, 100), "Materials and Methods", fontname="hebo", fontsize=14)
        doc.save(str(pdf))
        doc.close()

        blocks = get_converter("pymupdf").extract_blocks(pdf)
        assert any(b.text == "2. Materials and Methods" for b in blocks)

    def test_bold_is_read_from_the_font_flags(self, tmp_path):
        pdf = tmp_path / "bold.pdf"
        self._write_pdf(pdf, [[(72, 100, "Methods", "hebo", 14), (72, 130, "Body.", "helv", 10)]])
        blocks = get_converter("pymupdf").extract_blocks(pdf)
        by_text = {b.text: b for b in blocks}
        assert by_text["Methods"].is_bold is True
        assert by_text["Body."].is_bold is False

    def test_an_empty_page_contributes_no_blocks(self, tmp_path):
        pdf = tmp_path / "empty.pdf"
        self._write_pdf(pdf, [[]])
        assert get_converter("pymupdf").extract_blocks(pdf) == []

    def test_a_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            get_converter("pymupdf").extract_blocks(tmp_path / "nope.pdf")

    def test_a_non_pdf_suffix_raises(self, tmp_path):
        f = tmp_path / "notes.txt"
        f.write_text("hello")
        with pytest.raises(ValueError, match="not a PDF"):
            get_converter("pymupdf").extract_blocks(f)

    def test_a_corrupt_pdf_raises_rather_than_degrading(self, tmp_path):
        # Unlike convert(), which returns a failed result because partial
        # text is useful, a partial block list is indistinguishable from a
        # sparse PDF — so this path raises.
        pdf = tmp_path / "corrupt.pdf"
        pdf.write_bytes(b"%PDF-1.4 this is not a valid pdf body")
        with pytest.raises(PDFExtractionError) as caught:
            get_converter("pymupdf").extract_blocks(pdf)
        # Still a ValueError, which is what this path raised before #598.
        assert isinstance(caught.value, ValueError)
        assert caught.value.kind is PDFErrorKind.UNREADABLE
        assert str(caught.value).startswith("the PDF could not be read: ")
        assert "corrupt.pdf" not in str(caught.value)

    @pytest.mark.parametrize(
        "body",
        [
            pytest.param(b"not a pdf at all", id="garbage"),
            pytest.param(b"%PDF-1.4\n1 0 obj\n<<", id="truncated"),
            # PyMuPDF's own EmptyFileError, which chains no MuPDF code and
            # names the path.
            pytest.param(b"", id="empty"),
        ],
    )
    def test_a_damaged_file_is_the_files_fault_on_both_paths(self, tmp_path, body):
        # MuPDF's FORMAT and SYNTAX, the codes Rust's classify reads as the
        # file's too.
        pdf = tmp_path / "damaged.pdf"
        pdf.write_bytes(body)
        converter = get_converter("pymupdf")
        result = converter.convert(pdf)
        assert result.error_kind is PDFErrorKind.UNREADABLE
        assert "damaged.pdf" not in result.error_message
        with pytest.raises(PDFExtractionError) as caught:
            converter.extract_blocks(pdf)
        assert caught.value.kind is PDFErrorKind.UNREADABLE
        assert "damaged.pdf" not in str(caught.value)

    @pytest.mark.parametrize(
        "make_error",
        [
            pytest.param(lambda m: MemoryError(), id="memory"),
            pytest.param(lambda m: m.mupdf.FzErrorSystem("out of memory"), id="fz-system"),
            pytest.param(lambda m: m.mupdf.FzErrorArgument("bad argument"), id="fz-argument"),
            pytest.param(lambda m: RuntimeError("unplaced"), id="unknown"),
            pytest.param(lambda m: KeyError("size"), id="bmlib-defect"),
        ],
    )
    def test_an_engine_fault_is_not_the_files(self, tmp_path, monkeypatch, make_error):
        # An out-of-memory on a large PDF must not tell the user to pick
        # another file; a cause the backend cannot place is the engine's too.
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4\n")
        converter = get_converter("pymupdf")
        error = make_error(converter._pymupdf)

        def fail(*_args, **_kwargs):
            raise error

        monkeypatch.setattr(converter._pymupdf, "open", fail)
        result = converter.convert(pdf)
        assert result.error_kind is PDFErrorKind.ENGINE
        assert result.error_message.startswith("the PDF engine failed: ")
        with pytest.raises(PDFExtractionError) as caught:
            converter.extract_blocks(pdf)
        assert caught.value.kind is PDFErrorKind.ENGINE
        # What was raised is named, when it is not MuPDF's, so a defect in
        # bmlib does not read as a claim about MuPDF.
        if not isinstance(error, converter._pymupdf.mupdf.FzErrorBase):
            assert type(error).__name__ in result.error_message

    def test_every_mupdf_code_is_placed(self, tmp_path, monkeypatch):
        # Exactly FORMAT, SYNTAX and UNSUPPORTED are the file's, as in Rust's
        # classify; a code added later is the engine's by default.
        import inspect

        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4\n")
        converter = get_converter("pymupdf")
        mupdf = converter._pymupdf.mupdf
        codes = [
            cls
            for name, cls in inspect.getmembers(mupdf, inspect.isclass)
            if name.startswith("FzError")
            and issubclass(cls, mupdf.FzErrorBase)
            and cls is not mupdf.FzErrorBase
        ]
        assert len(codes) >= 10
        placed = {}
        for cls in codes:

            def fail(*_args, _cls=cls, **_kwargs):
                raise _cls("probe")

            monkeypatch.setattr(converter._pymupdf, "open", fail)
            placed[cls.__name__] = converter.convert(pdf).error_kind
        file_codes = {"FzErrorFormat", "FzErrorSyntax", "FzErrorUnsupported"}
        assert {n for n, k in placed.items() if k is PDFErrorKind.UNREADABLE} == file_codes
        assert all(k is PDFErrorKind.ENGINE for n, k in placed.items() if n not in file_codes)

    def test_a_suppressed_mupdf_context_does_not_decide(self, tmp_path, monkeypatch):
        # Raised while a FORMAT error was handled, but with the context
        # suppressed: the file is not blamed for what followed.
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4\n")
        converter = get_converter("pymupdf")
        mupdf = converter._pymupdf.mupdf

        def suppressed(*_args, **_kwargs):
            try:
                raise mupdf.FzErrorFormat("no objects found")
            except Exception:
                raise RuntimeError("something else") from None

        def implicit(*_args, **_kwargs):
            try:
                raise mupdf.FzErrorFormat("no objects found")
            except Exception:
                raise RuntimeError("raised while handling it")  # noqa: B904

        monkeypatch.setattr(converter._pymupdf, "open", suppressed)
        assert converter.convert(pdf).error_kind is PDFErrorKind.ENGINE
        monkeypatch.setattr(converter._pymupdf, "open", implicit)
        assert converter.convert(pdf).error_kind is PDFErrorKind.UNREADABLE

    def test_a_chained_mupdf_code_decides_over_the_wrapper(self, tmp_path, monkeypatch):
        # PyMuPDF raises FileDataError for any open failure and chains
        # MuPDF's own error as the cause; the cause's code is what decides.
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4\n")
        converter = get_converter("pymupdf")
        pymupdf = converter._pymupdf

        def fail(*_args, **_kwargs):
            try:
                raise pymupdf.mupdf.FzErrorSystem("cannot allocate")
            except Exception as cause:
                raise pymupdf.FileDataError(f"Failed to open file {str(pdf)!r}") from cause

        monkeypatch.setattr(pymupdf, "open", fail)
        result = converter.convert(pdf)
        assert result.error_kind is PDFErrorKind.ENGINE
        assert result.error_message == "the PDF engine failed: cannot allocate"

    def test_a_password_protected_pdf_names_the_password(self, tmp_path):
        # This already raised, but only because get_text() happened to fail:
        # the message named two causes ("document closed or encrypted") and
        # an extraction that ever stopped raising would return [], which is
        # what an image-only scan looks like. The check is explicit instead.
        pdf = _write_encrypted_pdf(tmp_path / "locked.pdf", user_pw="user")
        with pytest.raises(PDFExtractionError, match="remove the password") as caught:
            get_converter("pymupdf").extract_blocks(pdf)
        assert caught.value.kind is PDFErrorKind.ENCRYPTED

    def test_a_limit_reached_between_pages_stops_both_paths(self, tmp_path, caplog):
        # Python checks before each page (PyMuPDF's get_text takes no cookie):
        # cancelled after page 1 of 3, neither path returns a partial result.
        import threading

        class SetOnSecondCheck(threading.Event):
            def __init__(self) -> None:
                super().__init__()
                self.checks = 0

            def is_set(self) -> bool:
                self.checks += 1
                return self.checks >= 2

        pdf = tmp_path / "three.pdf"
        self._write_pdf(pdf, [[(72, 72, f"Page {n}", "helv", 12)] for n in ("one", "two", "three")])
        converter = get_converter("pymupdf")

        cancel = SetOnSecondCheck()
        with caplog.at_level("INFO", logger="bmlib.fulltext.pdf_converter"):
            result = converter.convert(pdf, limit=PDFLimit(cancel=cancel))
        assert result.success is False
        assert result.text == ""
        assert result.error_kind is PDFErrorKind.CANCELLED
        assert result.converted_pages == 1
        # The caller asked for the stop, so it is not reported as a fault.
        assert [r.levelname for r in caplog.records] == ["INFO"]

        cancel = SetOnSecondCheck()
        with pytest.raises(PDFExtractionError) as caught:
            converter.extract_blocks(pdf, limit=PDFLimit(cancel=cancel))
        assert caught.value.kind is PDFErrorKind.CANCELLED
        assert cancel.checks == 2

        unset = PDFLimit(cancel=threading.Event())
        assert len(converter.extract_blocks(pdf, limit=unset)) == 3
        assert converter.convert(pdf, limit=unset).converted_pages == 3

    def test_a_passed_deadline_stops_both_paths(self, tmp_path, caplog):
        import time

        pdf = tmp_path / "one.pdf"
        self._write_pdf(pdf, [[(72, 72, "Page one", "helv", 12)]])
        converter = get_converter("pymupdf")
        past = PDFLimit(deadline=time.monotonic() - 1)
        with caplog.at_level("INFO", logger="bmlib.fulltext.pdf_converter"):
            result = converter.convert(pdf, limit=past)
        assert (result.success, result.text, result.error_kind) == (
            False,
            "",
            PDFErrorKind.TIMED_OUT,
        )
        assert [r.levelname for r in caplog.records] == ["INFO"]
        with pytest.raises(PDFExtractionError) as caught:
            converter.extract_blocks(pdf, limit=past)
        assert caught.value.kind is PDFErrorKind.TIMED_OUT

    def test_an_owner_password_alone_does_not_block_extraction(self, tmp_path):
        # Negative control: encrypted, but readable, so blocks come back.
        pdf = _write_encrypted_pdf(tmp_path / "restricted.pdf", owner_pw="owner")
        blocks = get_converter("pymupdf").extract_blocks(pdf)
        assert [b.text for b in blocks] == ["Confidential trial results"]

    def test_a_pdf_paper_segments_end_to_end(self, tmp_path):
        pdf = tmp_path / "paper.pdf"
        self._write_pdf(
            pdf,
            [
                [
                    (72, 90, "Aspirin and Mortality: A Trial", "helv", 20),
                    (72, 120, "J Smith, R Jones", "helv", 10),
                    (72, 160, "Abstract", "hebo", 14),
                    (72, 180, "We tested aspirin against placebo.", "helv", 10),
                    (72, 220, "Methods", "hebo", 14),
                    (72, 240, "Participants were randomised by coin toss.", "helv", 10),
                ],
                [
                    (72, 90, "Results", "hebo", 14),
                    (72, 110, "Mortality fell.", "helv", 10),
                ],
            ],
        )
        blocks = get_converter("pymupdf").extract_blocks(pdf)
        doc = SectionSegmenter().segment_document(blocks, {"file_path": str(pdf)})

        assert doc.sections[0].section_type is SectionType.FRONT_MATTER
        assert doc.title == "Aspirin and Mortality: A Trial"
        methods = doc.get_section(SectionType.METHODS)
        assert methods is not None
        assert "randomised" in methods.content
        assert doc.get_section(SectionType.RESULTS) is not None
        assert doc.file_path == str(pdf)


@pytest.mark.skipif(not _HAS_PYMUPDF, reason="PyMuPDF not installed")
class TestTheConvertedResultCarriesAJudgedTitle:
    """Issue #56. Real PDFs carry filenames and "untitled" in ``/Title``, so
    the raw value is not the article's title and must not be read as one."""

    @staticmethod
    def _write_pdf(path: Path, metadata_title: str) -> Path:
        import pymupdf

        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Effects of aspirin on outcomes", fontname="hebo", fontsize=18)
        page.insert_text((72, 110), "We randomised 400 adults.", fontname="helv", fontsize=10)
        doc.set_metadata({"title": metadata_title})
        doc.save(str(path))
        doc.close()
        return path

    def test_a_corroborated_metadata_title_reaches_result_title(self, tmp_path):
        pdf = self._write_pdf(tmp_path / "good.pdf", "Effects of aspirin on outcomes")
        assert get_converter("pymupdf").convert(pdf).title == "Effects of aspirin on outcomes"

    def test_a_junk_metadata_title_leaves_result_title_none(self, tmp_path):
        pdf = self._write_pdf(tmp_path / "junk.pdf", "Microsoft Word - ms.docx")
        assert get_converter("pymupdf").convert(pdf).title is None

    def test_metadata_title_stays_verbatim_either_way(self, tmp_path):
        """``metadata`` is what the PDF says. A caller debugging provenance
        needs the raw string, and ``creator``/``producer`` sit beside it
        unmodified — sanitising one key of a verbatim dict would make the dict
        lie about its neighbours."""
        pdf = self._write_pdf(tmp_path / "junk2.pdf", "Microsoft Word - ms.docx")
        result = get_converter("pymupdf").convert(pdf)
        assert result.metadata["title"] == "Microsoft Word - ms.docx"
        assert result.title is None

    def test_a_pdf_with_no_metadata_title_has_no_judged_title(self, tmp_path):
        pdf = self._write_pdf(tmp_path / "blank.pdf", "")
        result = get_converter("pymupdf").convert(pdf)
        assert result.title is None

    def test_the_title_is_judged_against_page_one_not_the_first_page_with_text(self, tmp_path):
        """``page_texts`` omits a page that yielded nothing, so its first entry
        is page 1's text only when page 1 had any.

        Here page 1 is an image-only scan and page 2 carries prose that does
        *not* contain the metadata title. Judging against page 1 finds nothing
        to check against and accepts; judging against ``page_texts[0]`` finds
        page 2's prose, fails to match, and rejects. The two answers differ,
        which is what makes this test able to tell the implementations apart.
        """
        import pymupdf

        doc = pymupdf.open()
        doc.new_page()  # page 1: no text at all
        doc.new_page().insert_text((72, 72), "Effects of aspirin on outcomes", fontsize=12)
        doc.set_metadata({"title": "A study of coffee"})
        pdf = tmp_path / "blank_first_page.pdf"
        doc.save(str(pdf))
        doc.close()

        result = get_converter("pymupdf").convert(pdf)
        assert result.page_texts and "aspirin" in result.page_texts[0]
        assert result.title == "A study of coffee"

    @staticmethod
    def _open_returning(
        converter, monkeypatch, *, page_zero_raises=False, no_pages=False, page_zero_error=None
    ):
        """Drive ``convert()`` over a real document that misbehaves in one way.

        A page object comes fresh from ``doc[n]`` on every access, so patching
        the one PyMuPDF hands back does not stick — hence a proxy.
        """
        real_open = converter._pymupdf.open

        class _Page:
            def __init__(self, page, fail):
                self._page, self._fail = page, fail

            def get_text(self, *a, **k):
                if self._fail:
                    raise page_zero_error or RuntimeError("damaged content stream")
                return self._page.get_text(*a, **k)

        class _Doc:
            def __init__(self, doc):
                self._doc = doc

            def __len__(self):
                return 0 if no_pages else len(self._doc)

            def __getitem__(self, i):
                return _Page(self._doc[i], page_zero_raises and i == 0)

            def __getattr__(self, name):
                return getattr(self._doc, name)

            def __enter__(self):
                self._doc.__enter__()
                return self

            def __exit__(self, *a):
                return self._doc.__exit__(*a)

        monkeypatch.setattr(converter._pymupdf, "open", lambda *a, **k: _Doc(real_open(*a, **k)))

    def test_an_unreadable_page_one_rejects_rather_than_accepts(self, tmp_path, monkeypatch):
        """A page that *raises* is not an image-only scan.

        Both leave page 1 with no text to check against, but they mean
        opposite things: an empty scan makes corroboration inapplicable (and
        the metadata is then the only title signal there is), while a page
        whose extraction failed is the case with the *least* reason to trust
        what the file claims about itself.

        The metadata title here is one page 1 really does print, so the *only*
        thing that can reject it is the read failure — with ``""`` passed for
        both cases, as before, this returns the title instead.
        """
        pdf = self._write_pdf(tmp_path / "raises.pdf", "Effects of aspirin on outcomes")
        converter = get_converter("pymupdf")
        self._open_returning(converter, monkeypatch, page_zero_raises=True)

        result = converter.convert(pdf)

        assert result.title is None
        assert any("Extraction failed" in w for w in result.warnings)

    @pytest.mark.parametrize(
        "make_error",
        [
            pytest.param(lambda m: MemoryError(), id="memory"),
            pytest.param(lambda m: m.mupdf.FzErrorSystem("out of memory"), id="fz-system"),
        ],
    )
    def test_an_engine_fault_on_a_page_fails_the_conversion(
        self, tmp_path, monkeypatch, make_error
    ):
        # Out of memory on a page is not one bad page: the conversion fails as
        # the engine's, as Rust's does, rather than returning success short a
        # page with no kind (#598).
        pdf = self._write_pdf(tmp_path / "oom.pdf", "Effects of aspirin on outcomes")
        converter = get_converter("pymupdf")
        error = make_error(converter._pymupdf)
        self._open_returning(converter, monkeypatch, page_zero_raises=True, page_zero_error=error)
        result = converter.convert(pdf)
        assert (result.success, result.text, result.error_kind) == (False, "", PDFErrorKind.ENGINE)

    def test_a_file_fault_on_a_page_costs_that_page_alone(self, tmp_path, monkeypatch):
        # The #57 rule: one bad page does not abort the rest.
        pdf = self._write_pdf(tmp_path / "bad-page.pdf", "Effects of aspirin on outcomes")
        converter = get_converter("pymupdf")
        error = converter._pymupdf.mupdf.FzErrorSyntax("bad content stream")
        self._open_returning(converter, monkeypatch, page_zero_raises=True, page_zero_error=error)
        result = converter.convert(pdf)
        assert result.success is True and result.error_kind is None
        assert any("Extraction failed" in w for w in result.warnings)

    def test_a_document_with_no_pages_rejects_its_metadata_title(self, tmp_path, monkeypatch):
        """Zero pages is the same shape: the loop never runs, so there is no
        page 1 to corroborate against — a broken file rather than a scanned
        one. (PyMuPDF will not *save* a zero-page document, so the count is
        driven rather than written to disk.)"""
        pdf = self._write_pdf(tmp_path / "nopages.pdf", "Effects of aspirin on outcomes")
        converter = get_converter("pymupdf")
        self._open_returning(converter, monkeypatch, no_pages=True)

        result = converter.convert(pdf)

        assert result.page_count == 0
        assert result.title is None

    def test_a_fault_in_the_title_rule_costs_the_title_and_not_the_text(
        self, tmp_path, monkeypatch
    ):
        """The title is judged inside ``convert()``'s outer ``try``. Evaluated
        in the ``ConversionResult(...)`` call, anything it raised returned
        ``success=False, text=""`` — throwing away a complete, correct
        extraction and blaming *PDF conversion* for a fault in a heuristic
        that had nothing to do with the text.
        """
        import bmlib.fulltext.pdf_converter as pdf_converter_module

        def boom(metadata, page_one_text):
            raise RuntimeError("a defect in the title rule")

        monkeypatch.setattr(pdf_converter_module, "accepted_metadata_title", boom)
        pdf = self._write_pdf(tmp_path / "ok.pdf", "Effects of aspirin on outcomes")
        result = get_converter("pymupdf").convert(pdf)

        assert result.success is True
        assert "aspirin" in result.text
        assert result.title is None
        assert any("Title corroboration failed" in w for w in result.warnings)
