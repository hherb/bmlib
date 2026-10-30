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

"""The shared PDF-extraction contract, from a PDF's bytes (#590).

The same files Rust's ``rust/bmlib/tests/pdf_extraction_contract.rs`` asserts
against: ``PyMuPDFConverter.extract_blocks`` reads the committed PDF as exactly
the expected blocks (geometry within the fixture's tolerance), and the segmenter
and ``canonical_from_segmented`` lay them out as exactly the expected canonical
text. Both are written by ``scripts/make_pdf_extraction_fixture.py``, which
must still build the PDF byte for byte.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("pymupdf")

from bmlib.fulltext.canonical import canonical_from_segmented  # noqa: E402
from bmlib.fulltext.models import TextBlock  # noqa: E402
from bmlib.fulltext.pdf_converter import PyMuPDFConverter  # noqa: E402
from bmlib.fulltext.segmenter import SectionSegmenter  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
CONTRACT: dict[str, Any] = json.loads(
    (FIXTURES / "pdf_extraction.json").read_text(encoding="utf-8")
)
PDF = FIXTURES / CONTRACT["pdf"]
_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "make_pdf_extraction_fixture.py"
_GEOMETRY = ("x", "y", "width", "height")


def _load_generator() -> Any:
    spec = importlib.util.spec_from_file_location("bmlib_make_pdf_extraction", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    make = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = make
    spec.loader.exec_module(make)
    return make


def test_the_blocks_are_the_expected_ones() -> None:
    blocks = [b.to_dict() for b in PyMuPDFConverter().extract_blocks(PDF)]
    expected = CONTRACT["blocks"]
    tolerance = CONTRACT["geometry_tolerance"]
    assert [{k: v for k, v in b.items() if k not in _GEOMETRY} for b in blocks] == [
        {k: v for k, v in b.items() if k not in _GEOMETRY} for b in expected
    ]
    for got, want in zip(blocks, expected, strict=True):
        for key in _GEOMETRY:
            assert got[key] == pytest.approx(want[key], abs=tolerance), (key, got["text"])


def test_the_contract_carries_the_shapes_it_is_for() -> None:
    texts = [b["text"] for b in CONTRACT["blocks"]]
    assert sum(t.endswith("­") for t in texts) == 2
    assert any(t.endswith("dose-") for t in texts)
    fonts = {(b["font_name"], b["is_bold"], b["is_italic"]) for b in CONTRACT["blocks"]}
    assert fonts == {
        ("Helvetica", False, False),
        ("Helvetica-Bold", True, False),
        ("Times-Italic", False, True),
    }
    assert {b["page_num"] for b in CONTRACT["blocks"]} == {0, 1, 2}
    # A running head and a page number on every page, set aside by the
    # segmenter (#591), and the sentence the last page break interrupts.
    assert sum(t == "Smith et al. A soft hyphen trial" for t in texts) == 3
    assert [t for t in texts if t.isdigit()] == ["1", "2", "3"]
    # The five-span line, whose font only the superscript split makes italic.
    assert "Cultures of Escherichia coli were grown1 overnight." in texts


def test_the_segmented_blocks_are_the_expected_canonical_text() -> None:
    blocks = [TextBlock.from_dict(b) for b in CONTRACT["blocks"]]
    canonical = canonical_from_segmented(SectionSegmenter().segment_document(blocks))
    assert canonical.to_dict() == CONTRACT["canonical"]
    assert "prevalent" in canonical.text and "specific" in canonical.text
    assert "dose- response" in canonical.text and "­" not in canonical.text
    assert "Smith et al." not in canonical.text
    assert "The effect held across every site we visited." in canonical.text


def test_the_generator_still_builds_the_pdf() -> None:
    """A PDF edited by hand, not in the script, is lost on regeneration."""
    pdf = _load_generator().build_pdf()
    assert pdf == PDF.read_bytes()
    assert hashlib.sha256(pdf).hexdigest() == CONTRACT["pdf_sha256"]
