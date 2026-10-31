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

"""The shared MuPDF-messages contract (#599).

The same files Rust's ``rust/bmlib/tests/pdf_warnings_contract.rs`` asserts
against, written by ``scripts/make_pdf_warnings_fixture.py``, which must still
build every PDF byte for byte: ``PyMuPDFConverter`` reads each as the expected
lines and returns exactly the expected MuPDF messages — a repaired xref, a page
cut short after too many syntax errors (#605), and nothing for a sound PDF.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import sys
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("pymupdf")

from bmlib.fulltext.pdf_converter import (  # noqa: E402
    PyMuPDFConverter,
    _engine_messages,
    _reset_engine_messages,
    is_about_the_file,
)

FIXTURES = Path(__file__).parent / "fixtures"
CONTRACT: dict[str, Any] = json.loads((FIXTURES / "pdf_warnings.json").read_text(encoding="utf-8"))
ROWS = CONTRACT["rows"]
_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "make_pdf_warnings_fixture.py"


def _load_generator() -> Any:
    spec = importlib.util.spec_from_file_location("bmlib_make_pdf_warnings", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    make = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = make
    spec.loader.exec_module(make)
    return make


@pytest.mark.parametrize("row", ROWS, ids=[row["pdf"] for row in ROWS])
def test_each_pdf_reads_as_its_lines_with_its_messages(row: dict[str, Any]) -> None:
    read = PyMuPDFConverter().extract_blocks_with_warnings(FIXTURES / row["pdf"])
    assert [block.text for block in read.blocks] == row["texts"]
    assert read.warnings == row["warnings"]


@pytest.mark.parametrize("row", ROWS, ids=[row["pdf"] for row in ROWS])
def test_convert_ends_its_warnings_with_them(row: dict[str, Any]) -> None:
    result = PyMuPDFConverter().convert(FIXTURES / row["pdf"])
    assert result.success
    assert result.warnings[len(result.warnings) - len(row["warnings"]) :] == row["warnings"]
    assert len(result.warnings) == len(row["warnings"])


def test_extract_blocks_returns_the_same_blocks() -> None:
    path = FIXTURES / ROWS[1]["pdf"]
    converter = PyMuPDFConverter()
    assert converter.extract_blocks(path) == converter.extract_blocks_with_warnings(path).blocks


def test_each_message_is_logged_at_warning_without_the_path_in_the_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = FIXTURES / ROWS[0]["pdf"]
    with caplog.at_level(logging.WARNING, logger="bmlib.fulltext.pdf_converter"):
        read = PyMuPDFConverter().extract_blocks_with_warnings(path)
    logged = [r.args[1] for r in caplog.records if r.msg.startswith("MuPDF reported")]
    assert logged == read.warnings == ROWS[0]["warnings"]
    assert all(str(path) not in message for message in read.warnings)


def test_a_second_read_reports_the_same() -> None:
    # MuPDF counts a warning repeating the one before rather than reporting
    # it, and that count outlives a read unless it is flushed.
    path = FIXTURES / ROWS[0]["pdf"]
    converter = PyMuPDFConverter()
    first = converter.extract_blocks_with_warnings(path).warnings
    assert first
    assert converter.extract_blocks_with_warnings(path).warnings == first


def test_a_warning_left_counted_does_not_swallow_the_next() -> None:
    # As a failed read leaves one: a plain reset of PyMuPDF's store keeps
    # MuPDF's count, and this read would report only "... repeated 4 times...".
    import pymupdf

    pymupdf.mupdf.fz_warn("left over")
    _reset_engine_messages(pymupdf)
    for _ in range(3):
        pymupdf.mupdf.fz_warn("left over")
    assert _engine_messages(pymupdf, Path("/nowhere.pdf")) == [
        "left over",
        "... repeated 3 times...",
    ]


def test_a_failed_read_logs_what_mupdf_reported(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    garbage = tmp_path / "garbage.pdf"
    garbage.write_bytes(b"%PDF-1.4\nnot a pdf at all\n")
    with (
        caplog.at_level(logging.WARNING, logger="bmlib.fulltext.pdf_converter"),
        pytest.raises(ValueError),
    ):
        PyMuPDFConverter().extract_blocks_with_warnings(garbage)
    assert any(r.msg.startswith("MuPDF reported") for r in caplog.records)


def test_only_the_store_race_is_set_aside() -> None:
    assert not any(is_about_the_file(m) for m in CONTRACT["set_aside"])
    assert all(is_about_the_file(m) for m in CONTRACT["kept"])


def test_the_contract_carries_the_shapes_it_is_for() -> None:
    repaired, cut_short, control = ROWS
    assert "repairing PDF document" in repaired["warnings"]
    assert "too many syntax errors; ignoring rest of page" in cut_short["warnings"]
    assert CONTRACT["lost_line"] not in cut_short["texts"]
    assert cut_short["texts"]
    assert control["warnings"] == []


def test_the_generator_still_builds_the_committed_files() -> None:
    make = _load_generator()
    for name, data in make.PDFS.items():
        assert (FIXTURES / name).read_bytes() == data, name
    for row in ROWS:
        digest = hashlib.sha256((FIXTURES / row["pdf"]).read_bytes()).hexdigest()
        assert row["pdf_sha256"] == digest, row["pdf"]
    assert make.build_contract(FIXTURES) == CONTRACT
