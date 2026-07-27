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

from bmlib.fulltext.pdf_converter import (
    ConversionResult,
    PDFConverter,
    get_converter,
    list_converters,
    render_html,
)

_HAS_FITZ = importlib.util.find_spec("fitz") is not None


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


class TestRegistry:
    def test_list_converters_includes_pymupdf(self):
        assert "pymupdf" in list_converters()

    def test_unknown_converter_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown converter"):
            get_converter("does-not-exist")

    @pytest.mark.skipif(_HAS_FITZ, reason="PyMuPDF is installed")
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


@pytest.mark.skipif(not _HAS_FITZ, reason="PyMuPDF not installed")
class TestPyMuPDFConversion:
    def test_converts_generated_pdf(self, tmp_path):
        import fitz

        pdf_path = tmp_path / "gen.pdf"
        doc = fitz.open()
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
        assert result.error_message


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
