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

"""Write the PDFs of the shared MuPDF-messages contract and what each reports (#599).

MuPDF reads past damage with only a message, which Python's
``PyMuPDFConverter.extract_blocks_with_warnings`` and Rust's
``MupdfExtractor::extract_blocks_with_warnings`` must both return. Two
one-page PDFs, written by hand byte for byte:

* ``pdf_warnings_repaired.pdf``: its ``startxref`` points at nothing, so
  MuPDF repairs the xref and reads the page whole.
* ``pdf_warnings_cut_short.pdf``: a line, then more syntax errors than MuPDF
  reads past (its ``MAX_SYNTAX_ERRORS``, 100), then a line MuPDF never reaches
  (#605). Its messages are the only sign the page was cut short.

``pdf_extraction.pdf`` (#590's contract) is the control: it reports nothing.
The expected values are PyMuPDF's, printed on stderr to be checked by eye.

    python scripts/make_pdf_warnings_fixture.py [tests/fixtures]

``tests/test_pdf_warnings_contract.py`` checks that this script still builds
every committed file, so change a line here and re-run it rather than editing
any by hand.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from bmlib.fulltext.pdf_converter import PyMuPDFConverter

JSON_NAME = "pdf_warnings.json"
REPAIRED = "pdf_warnings_repaired.pdf"
CUT_SHORT = "pdf_warnings_cut_short.pdf"
#: #590's contract PDF, which MuPDF reads without a message.
CONTROL = "pdf_extraction.pdf"

#: More syntax errors than MuPDF's ``MAX_SYNTAX_ERRORS`` (100) reads past.
SYNTAX_ERRORS = 120
#: The line MuPDF reads before the errors, and the one it never reaches.
READ_LINE = "Read before the damage."
LOST_LINE = "Never reached."


def _line(y: int, text: str) -> str:
    return f"BT /F1 12 Tf 72 {y} Td ({text}) Tj ET"


def build_pdf(stream: str, *, xref_points_at_nothing: bool = False) -> bytes:
    """A one-page PDF showing *stream* in Helvetica, with a correct xref unless asked."""
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        "/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    out = "%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    out += "".join(f"{offset:010} 00000 n \n" for offset in offsets)
    start = len(out) + 1000 if xref_points_at_nothing else xref
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n"
    return out.encode("latin-1")


#: Each PDF this script writes, by name.
PDFS: dict[str, bytes] = {
    REPAIRED: build_pdf(_line(700, READ_LINE), xref_points_at_nothing=True),
    CUT_SHORT: build_pdf(
        "\n".join([_line(700, READ_LINE), *(["] ) >> Tj"] * SYNTAX_ERRORS), _line(600, LOST_LINE)])
    ),
}


def _row(directory: Path, name: str) -> dict[str, Any]:
    path = directory / name
    read = PyMuPDFConverter().extract_blocks_with_warnings(path)
    return {
        "pdf": name,
        "pdf_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "texts": [block.text for block in read.blocks],
        "warnings": read.warnings,
    }


def build_contract(directory: Path) -> dict[str, Any]:
    """What PyMuPDF reads from each PDF in *directory*, and what it reports."""
    return {
        "description": (
            "What MuPDF reports while reading a damaged PDF, which Python's and Rust's "
            "extract_blocks_with_warnings both return (#599). Written by "
            "scripts/make_pdf_warnings_fixture.py."
        ),
        "lost_line": LOST_LINE,
        # is_about_the_file: MuPDF's store race is set aside, nothing else.
        "set_aside": [
            "found duplicate fz_icc_link in the store",
            "found duplicate fz_image in the store",
        ],
        "kept": [
            "repairing PDF document",
            "found duplicate fz_icc_link in the store!",
            "found duplicate  in the store",
            "found duplicate fz icc in the store",
            "found duplicate fz_ícc in the store",
            "found duplicate fz_icc\u001c in the store",
        ],
        "rows": [_row(directory, name) for name in (REPAIRED, CUT_SHORT, CONTROL)],
    }


def main(directory: Path) -> None:
    """Write every PDF and the contract into *directory*."""
    for name, data in PDFS.items():
        (directory / name).write_bytes(data)
    contract = build_contract(directory)
    for row in contract["rows"]:
        print(f"{row['pdf']}: {row['texts']!r}", file=sys.stderr)
        for message in row["warnings"]:
            print(f"    {message}", file=sys.stderr)
    text = json.dumps(contract, indent=2, ensure_ascii=False) + "\n"
    (directory / JSON_NAME).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main(
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else Path(__file__).parent.parent / "tests" / "fixtures"
    )
