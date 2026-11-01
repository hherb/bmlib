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
shapes the contract is for (not a subset-tagged name, ``ABCDEF+Helvetica-Bold``:
the crate's MuPDF 1.27.2 sets that unembedded face's line 0.2 pt lower than
PyMuPDF 1.28.5 does, and PyMuPDF 1.27.2 agrees with the crate, so it measures
the version gap and not either backend): unembedded base-14 faces (Helvetica, its bold,
Times-Italic), whose glyphs MuPDF supplies from its own copies; a word broken
at a soft hyphen (U+00AD, through an ``/Encoding`` ``/Differences`` entry
naming ``uni00AD``); a hard hyphen at a line's end, which is kept; and a line
of five spans whose font is decided by the superscript bit — read as one span
with the run after it, the raised ``1`` would make Helvetica the line's
dominant font, where split off it leaves the italic run dominant; and on every
page a running head and a page number, which the segmenter sets aside (#591),
so a sentence crossing the last page break reads on. And the layout of a
journal set without space between paragraphs: a ``/Title`` in the document
information, which the blocks carry as ``metadata_title``; names whose
accents are spacing glyphs of their own (``Nicol\\264as``, WinAnsi's 0xB4
acute), drawn over their letters as TeX sets them, beside one typed as an
apostrophe (``Alzheimer\\264s``), which is not; a numbered italic subheading at body size that
wraps onto a second line; a paragraph begun by a first-line indent; and a
heading at body size in a face MuPDF does not flag bold but whose name says
it is (``AdvOTtest.B``, #628), as the five-span line's italic run is italic by
its name alone (``AdvOTtest.I``). The
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
    # Advent 3B2's bold and italic suffixes, which MuPDF does not flag (#628):
    # unembedded faces of no base-14 name, so MuPDF substitutes a serif and
    # keeps the name.
    "F4": ("AdvOTtest.B", False),
    "F5": ("AdvOTtest.I", False),
}

#: A run of one line: (font, size, text rise, text). A list for the text is
#: shown with ``TJ``: its numbers move the next string back by that many
#: thousandths of an em, which is how TeX draws an accent over its letter.
Run = tuple[str, int, int, str | list[str | int]]

#: Helvetica's acute (WinAnsi 0xB4) is 333 thousandths of an em wide, so moving
#: back by it starts the letter where the accent starts.
ACUTE_WIDTH = 333

#: Each page's lines: (x, y, runs). ``\255`` is byte 173, which F1's encoding
#: maps to U+00AD.
PAGES: list[list[tuple[int, int, list[Run]]]] = [
    [
        (72, 720, [("F2", 18, 0, "A soft hyphen trial")]),
        (72, 680, [("F1", 10, 0, r"We found the condition preva\255")]),
        (72, 668, [("F1", 10, 0, "lent in adults, with a dose-")]),
        (72, 656, [("F1", 10, 0, "response curve that rose steadily.")]),
        (72, 620, [("F2", 12, 0, "Methods")]),
        (
            72,
            600,
            [
                ("F1", 10, 0, "Cultures of "),
                ("F5", 10, 0, "Escherichia coli"),
                ("F1", 10, 0, " were grown"),
                ("F1", 10, 4, "1"),
                ("F1", 10, 0, " overnight."),
            ],
        ),
        (72, 580, [("F3", 10, 0, "2.1. Study population and")]),
        (72, 568, [("F3", 10, 0, "setting")]),
        (
            72,
            550,
            [
                (
                    "F1",
                    10,
                    0,
                    [
                        r"Patients were enrolled by Nicol\264",
                        ACUTE_WIDTH,
                        r"as P\264",
                        ACUTE_WIDTH,
                        r"erez with Alzheimer\264s.",
                    ],
                )
            ],
        ),
    ],
    [
        (72, 720, [("F2", 12, 0, "Results")]),
        (72, 700, [("F1", 10, 0, r"Growth was spe\255")]),
        (72, 688, [("F1", 10, 0, "cific to the treated arm.")]),
        (72, 660, [("F4", 10, 0, "Discussion")]),
        (72, 640, [("F1", 10, 0, "The effect held across")]),
    ],
    [
        (72, 720, [("F1", 10, 0, "every site we visited.")]),
        (84, 708, [("F1", 10, 0, "A second paragraph starts")]),
        (72, 696, [("F1", 10, 0, "here and ends.")]),
    ],
]

#: Page furniture on every page (#591): a running head above the text and the
#: page number below it, which the segmenter sets aside, so the sentence the
#: break between the last two pages interrupts reads on.
#: The document information's ``/Title``, which page 1 prints as its title.
TITLE = "A soft hyphen trial"

RUNNING_HEAD: Run = ("F3", 8, 0, "Smith et al. A soft hyphen trial")
for _number, _lines in enumerate(PAGES, start=1):
    _lines.insert(0, (72, 760, [RUNNING_HEAD]))
    _lines.append((300, 40, [("F1", 8, 0, str(_number))]))


def _shown(text: str | list[str | int]) -> str:
    if isinstance(text, str):
        return f"({text}) Tj"
    return "[" + " ".join(f"({p})" if isinstance(p, str) else str(p) for p in text) + "] TJ"


def _line(x: int, y: int, runs: list[Run]) -> str:
    shown = " ".join(
        f"/{font} {size} Tf {rise} Ts {_shown(text)}" for font, size, rise, text in runs
    )
    return f"BT {x} {y} Td {shown} ET"


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
    info_id = 3 + len(FONTS) + 2 * len(PAGES)
    objects += [_font_object(*FONTS[name]) for name in FONTS]
    fonts = " ".join(f"/{name} {font_ids[name]} 0 R" for name in FONTS)
    page_ids = []
    for index, lines in enumerate(PAGES):
        page_id = first_page + 2 * index
        page_ids.append(page_id)
        stream = "\n".join(_line(*line) for line in lines)
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << {fonts} >> >> /Contents {page_id + 1} 0 R >>"
        )
        objects.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
    kids = " ".join(f"{i} 0 R" for i in page_ids)
    objects[1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>"
    objects.append(f"<< /Title ({TITLE}) >>")

    out = "%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    out += "".join(f"{offset:010} 00000 n \n" for offset in offsets)
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info {info_id} 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n"
    )
    return out.encode("latin-1")


def build_contract(pdf_path: Path) -> dict[str, Any]:
    """The expected blocks and canonical text, from PyMuPDF and the segmenter."""
    read = PyMuPDFConverter().extract_blocks_with_warnings(pdf_path)
    document = SectionSegmenter().segment_document(read.blocks, {"title": read.metadata_title})
    canonical = canonical_from_segmented(document)
    return {
        "pdf": PDF_NAME,
        "pdf_sha256": hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
        "geometry_tolerance": GEOMETRY_TOLERANCE,
        "metadata_title": read.metadata_title,
        "blocks": [b.to_dict() for b in read.blocks],
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
