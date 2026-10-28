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

"""Write ``tests/fixtures/canonical_chunks.json`` from the Python chunker.

The shared chunker contract, which Python and Rust both assert against. The
expected chunks are the Python chunker's output, so a case is only as good as
the eye that checked it: every chunk's text is printed on stderr for that.

    python scripts/make_canonical_chunks.py [tests/fixtures/canonical_chunks.json]

``tests/test_canonical_chunks_contract.py`` checks that this script still
builds the committed fixture, so add a case here and re-run it rather than
editing the JSON by hand.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path
from typing import Any

from bmlib.fulltext.canonical import (
    CHUNKER_VERSION,
    DEFAULT_MAX_CHARS,
    DEFAULT_OVERLAP_CHARS,
    BlockDraft,
    CanonicalText,
    SectionDraft,
    SourceFormat,
    assemble,
    canonical_from_jats,
    chunk_canonical,
)
from bmlib.fulltext.jats_parser import JATSParser

DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "tests/fixtures/canonical_chunks.json"

LONG = " ".join(
    f"Sentence {i} reports that the hazard ratio was 0.{i % 9 + 1} "
    f"(95% CI 0.5 to 1.2; Fig. {i % 4 + 1})."
    for i in range(40)
)

JATS = """<article><front><article-meta><title-group><article-title>Trial</article-title>
</title-group><abstract><p>Background. We tested it.</p></abstract></article-meta></front>
<body><sec><title>Results</title><p>It worked in 𝛽-cells. See Table 1.</p>
<table-wrap><table><tr><th>Arm</th><th>n</th></tr><tr><td>A</td><td>12</td></tr></table></table-wrap>
</sec></body></article>"""

# The random cases' alphabet: whitespace, every sentence mark, an abbreviation's
# letters, an opening mark, and non-ASCII. The last is the Kelvin sign, which
# str.lower maps to an ASCII "k"; escaped, since it looks like a capital K.
ALPHABET = "ab .!?\tFig(é𝛽K"


def doc(title: str, *sections: SectionDraft) -> CanonicalText:
    """Assemble *sections* as plain text."""
    return assemble(title, list(sections), SourceFormat.PLAIN_TEXT, "plain_text-1")


def sec(*paras: str, heading: str = "", level: int = 1) -> SectionDraft:
    """A section of paragraphs."""
    return SectionDraft(heading, level, [BlockDraft.paragraph(p) for p in paras])


def written_cases() -> list[tuple[str, CanonicalText, int, int]]:
    """The hand-written cases: (name, text, max_chars, overlap_chars)."""
    return [
        ("one-short-section", doc("Title", sec("A short paragraph.", heading="Intro")), 1000, 100),
        ("blocks-joined-sections-not", doc("", sec("A.", "B."), sec("C.")), 1000, 100),
        ("heading-only-section", doc("", SectionDraft("Empty", 1, []), sec("Body.")), 1000, 100),
        ("packed-no-overlap", doc("", sec("Aaaa. Bbbb. Cccc. Dddd.")), 11, 0),
        ("overlap-whole-sentences", doc("", sec("Aaaa. Bbbb. Cccc. Dddd.")), 11, 5),
        ("overlap-sentence-too-long", doc("", sec("Aa. Bbbbbbbb. Cc.")), 13, 5),
        ("overlap-leaves-no-room", doc("", sec("Aaaa. Bbbb. Ccccccccc.")), 11, 5),
        (
            "abbreviations",
            doc(
                "",
                sec(
                    "As in Fig. 2 and (Fig. 3) shown. Smith et al. found, e.g. this. "
                    "Dr. X vs. FIG. Y! Ok? Done."
                ),
            ),
            20,
            0,
        ),
        ("marks-without-whitespace", doc("", sec("p = 0.05 and 1.5-fold. Next.")), 25, 0),
        ("cut-at-whitespace", doc("", sec("one two three four")), 9, 0),
        ("cut-at-cap", doc("", sec("abcdefgh ij")), 3, 0),
        ("code-points", doc("", sec("𝛽 é. Next. Ünïcödé 𝛼𝛽𝛾 here.")), 5, 0),
        (
            "table-rows",
            doc(
                "",
                SectionDraft("Table", 1, [BlockDraft.row(["", "a"]), BlockDraft.row(["b", "c"])]),
            ),
            3,
            0,
        ),
        (
            "cut-row-at-empty-cell",
            doc("", SectionDraft("", 1, [BlockDraft.row(["abc", "", "de"])])),
            5,
            0,
        ),
        ("empty", doc(""), 1000, 100),
        (
            "defaults-long-paragraph",
            doc("Paper", sec(LONG, heading="Results"), sec("Short.", heading="Funding")),
            DEFAULT_MAX_CHARS,
            DEFAULT_OVERLAP_CHARS,
        ),
        ("jats", canonical_from_jats(JATSParser(JATS.encode()).parse()), 30, 10),
        # The PR #554 review's four, each pinning a mutant that survived every test.
        ("overlap-carries-several-sentences", doc("", sec("A. B. C. D. E. F.")), 8, 5),
        ("cut-lands-on-the-cap", doc("", sec("ab cd ef")), 5, 0),
        (
            "row-ends-in-an-empty-cell",
            doc("", SectionDraft("", 1, [BlockDraft.row(["a", ""])])),
            5,
            0,
        ),
        (
            # U+001C is whitespace to str.isspace and not to Rust or is_white_space.
            "information-separator-is-not-whitespace",
            doc("", sec("Total.\x1cNext sentence here.")),
            10,
            0,
        ),
    ]


def random_cases() -> list[tuple[str, CanonicalText, int, int]]:
    """Twenty seeded random texts, with random sizes."""
    cases = []
    for seed in range(20):
        rng = random.Random(seed)

        def para(rng: random.Random = rng) -> str:
            return "".join(rng.choice(ALPHABET) for _ in range(rng.randrange(0, 60)))

        sections = [
            sec(*(para() for _ in range(rng.randrange(0, 4))), heading=rng.choice(["", "H"]))
            for _ in range(rng.randrange(1, 4))
        ]
        max_chars = rng.randrange(1, 30)
        cases.append(
            (f"random-{seed}", doc("T", *sections), max_chars, rng.randrange(0, max_chars))
        )
    return cases


def build_contract(show: bool = False) -> dict[str, Any]:
    """The whole contract; with *show*, each case's chunk texts on stderr."""
    contract: dict[str, Any] = {
        "chunker_version": CHUNKER_VERSION,
        "default_max_chars": DEFAULT_MAX_CHARS,
        "default_overlap_chars": DEFAULT_OVERLAP_CHARS,
        "cases": [],
        "refusals": [
            {"name": "zero-max", "max_chars": 0, "overlap_chars": 0},
            {"name": "overlap-equals-max", "max_chars": 5, "overlap_chars": 5},
            {"name": "overlap-above-max", "max_chars": 5, "overlap_chars": 6},
        ],
    }
    for name, canonical, max_chars, overlap in written_cases() + random_cases():
        chunks = chunk_canonical(canonical, max_chars, overlap)
        contract["cases"].append(
            {
                "name": name,
                "canonical": canonical.to_dict(),
                "max_chars": max_chars,
                "overlap_chars": overlap,
                "expected": [c.to_dict() for c in chunks],
            }
        )
        if show:
            print(f"== {name} ({max_chars}/{overlap})", file=sys.stderr)
            for c in chunks:
                print(f"  [{c.section_index}] {canonical.span_text(c.span)!r}", file=sys.stderr)
    return contract


def main() -> None:
    """Write the contract to the path given, or to the committed fixture."""
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT
    text = json.dumps(build_contract(show=True), ensure_ascii=False, indent=1) + "\n"
    output.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
