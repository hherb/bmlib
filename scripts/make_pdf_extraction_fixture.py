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

"""Write ``tests/fixtures/pdf_extraction.pdf`` and its expected extraction.

The shared contract that starts at a PDF's bytes (#590): Python's
``PyMuPDFConverter.extract_blocks`` and Rust's ``MupdfExtractor`` must read the
committed PDF as the same blocks, and the segmenter and
``canonical_from_segmented`` must lay those out as the same canonical text.
The PDF is written here by hand, byte for byte, so it carries exactly the
shapes the contract is for: unembedded base-14 faces (Helvetica, its bold,
Times-Italic), whose glyphs MuPDF supplies from its own copies; a word broken
at a soft hyphen (U+00AD, through an ``/Encoding`` ``/Differences`` entry
naming ``uni00AD``); and a hard hyphen at a line's end, which is kept. The
expected values are PyMuPDF's output, printed on stderr to be checked by eye.

    python scripts/make_pdf_extraction_fixture.py [tests/fixtures]

``tests/test_pdf_extraction_contract.py`` checks that this script still builds
both committed files, so change a line here and re-run it rather than editing
either by hand.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from bmlib.fulltext.canonical import canonical_from_segmented
from bmlib.fulltext.pdf_converter import PyMuPDFConverter
from bmlib.fulltext.segmenter import SectionSegmenter

PDF_NAME = "pdf_extraction.pdf"
JSON_NAME = "pdf_extraction.json"

#: Line geometry may differ by this much between the two languages' MuPDF
#: builds (PyMuPDF's bundled MuPDF is not the crate's); every other field of a
#: block is compared exactly.
GEOMETRY_TOLERANCE = 0.01

#: The fonts, by resource name: (base font, whether its encoding maps 173 to
#: the soft hyphen).
FONTS = {
    "F1": ("Helvetica", True),
    "F2": ("Helvetica-Bold", False),
    "F3": ("Times-Italic", False),
}

#: Each page's lines: (font, size, x, y, text). ``\255`` is byte 173, which F1's
#: encoding maps to U+00AD.
PAGES: list[list[tuple[str, int, int, int, str]]] = [
    [
        ("F2", 18, 72, 720, "A soft hyphen trial"),
        ("F1", 10, 72, 680, r"We found the condition preva\255"),
        ("F1", 10, 72, 668, "lent in adults, with a dose-"),
        ("F1", 10, 72, 656, "response curve that rose steadily."),
        ("F2", 12, 72, 620, "Methods"),
        ("F1", 10, 72, 600, "Cultures of"),
        ("F3", 10, 130, 600, "Escherichia coli"),
        ("F1", 10, 72, 588, "were grown overnight."),
    ],
    [
        ("F2", 12, 72, 720, "Results"),
        ("F1", 10, 72, 700, r"Growth was spe\255"),
        ("F1", 10, 72, 688, "cific to the treated arm."),
    ],
]


def _font_object(base_font: str, soft_hyphen: bool) -> str:
    encoding = (
        "<< /Type /Encoding /BaseEncoding /WinAnsiEncoding /Differences [173 /uni00AD] >>"
        if soft_hyphen
        else "/WinAnsiEncoding"
    )
    return f"<< /Type /Font /Subtype /Type1 /BaseFont /{base_font} /Encoding {encoding} >>"


def build_pdf() -> bytes:
    """The contract's PDF, written object by object with a correct xref table."""
    font_ids = {name: 3 + i for i, name in enumerate(FONTS)}
    first_page = 3 + len(FONTS)
    objects: list[str] = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "",  # the page tree, once the page ids are known
    ]
    objects += [_font_object(*FONTS[name]) for name in FONTS]
    fonts = " ".join(f"/{name} {font_ids[name]} 0 R" for name in FONTS)
    page_ids = []
    for index, lines in enumerate(PAGES):
        page_id = first_page + 2 * index
        page_ids.append(page_id)
        stream = "\n".join(
            f"BT /{font} {size} Tf {x} {y} Td ({text}) Tj ET" for font, size, x, y, text in lines
        )
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << {fonts} >> >> /Contents {page_id + 1} 0 R >>"
        )
        objects.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
    kids = " ".join(f"{i} 0 R" for i in page_ids)
    objects[1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>"

    out = "%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    out += "".join(f"{offset:010} 00000 n \n" for offset in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    return out.encode("latin-1")


def build_contract(pdf_path: Path) -> dict[str, Any]:
    """The expected blocks and canonical text, from PyMuPDF and the segmenter."""
    blocks = PyMuPDFConverter().extract_blocks(pdf_path)
    canonical = canonical_from_segmented(SectionSegmenter().segment_document(blocks))
    return {
        "pdf": PDF_NAME,
        "pdf_sha256": hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
        "geometry_tolerance": GEOMETRY_TOLERANCE,
        "blocks": [b.to_dict() for b in blocks],
        "canonical": canonical.to_dict(),
    }


def main(directory: Path) -> None:
    """Write both files into *directory*."""
    pdf_path = directory / PDF_NAME
    pdf_path.write_bytes(build_pdf())
    contract = build_contract(pdf_path)
    for block in contract["blocks"]:
        print(f"{block['page_num']} {block['font_name']:<16} {block['text']!r}", file=sys.stderr)
    print(f"\n{contract['canonical']['text']}", file=sys.stderr)
    text = json.dumps(contract, indent=2, ensure_ascii=False) + "\n"
    (directory / JSON_NAME).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main(
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else Path(__file__).parent.parent / "tests" / "fixtures"
    )
