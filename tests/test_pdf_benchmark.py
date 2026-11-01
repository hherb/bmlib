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

"""A real PDF read and segmented end to end: the benchmark for layout work.

The PDF is named by ``BMLIB_BENCHMARK_PDF`` and is not committed (a journal's
PDF is not ours to publish), so the test is skipped without it. With
``BMLIB_BENCHMARK_OUT`` set it also writes the canonical text there, to diff
against Rust's ``tests/pdf_benchmark.rs``, which writes the same.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("pymupdf")

from bmlib.fulltext.canonical import canonical_from_segmented  # noqa: E402
from bmlib.fulltext.pdf_converter import SPACING_ACCENTS, PyMuPDFConverter  # noqa: E402
from bmlib.fulltext.segmenter import SectionSegmenter  # noqa: E402

PDF = os.environ.get("BMLIB_BENCHMARK_PDF")


@pytest.mark.skipif(not PDF, reason="needs BMLIB_BENCHMARK_PDF, a PDF not in the repository")
def test_a_benchmark_pdf_reads_as_a_segmented_paper() -> None:
    read = PyMuPDFConverter().extract_blocks_with_warnings(Path(str(PDF)))
    document = SectionSegmenter().segment_document(read.blocks, {"title": read.metadata_title})
    canonical = canonical_from_segmented(document)
    if out := os.environ.get("BMLIB_BENCHMARK_OUT"):
        Path(out).write_text(canonical.text, encoding="utf-8")
    # Headings were found, and no accent is left standing before a letter.
    assert len(document.sections) > 1, document.sections
    text = canonical.text
    assert not any(a in SPACING_ACCENTS and b.isalpha() for a, b in zip(text, text[1:]))
