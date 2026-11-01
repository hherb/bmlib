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

"""Section segmentation for biomedical publications.

Heading-driven segmentation of a PDF's text lines into the standard
sections of a biomedical paper. The input is the ``list[TextBlock]``
produced by
:meth:`bmlib.fulltext.pdf_converter.PyMuPDFConverter.extract_blocks`; the
output is a :class:`bmlib.fulltext.models.SegmentedDocument`.

Ported from bmlibrarian's ``pdf_processor.segmenter``. The behaviour
changes from upstream — line-granularity blocks, front matter kept, the
partial matcher searching the compiled pattern instead of comparing regex
source text — are argued in
``docs/superpowers/specs/2026-08-05-pdf-section-segmenter-design.md``.

Example::

    from pathlib import Path
    from bmlib.fulltext import SectionSegmenter, SectionType, get_converter

    blocks = get_converter("pymupdf").extract_blocks(Path("paper.pdf"))
    document = SectionSegmenter().segment_document(blocks)
    methods = document.get_section(SectionType.METHODS)
"""

from __future__ import annotations

import itertools
import math
import re
import statistics
import unicodedata
from dataclasses import dataclass
from typing import Any

from bmlib.fulltext._titles import accepted_metadata_title
from bmlib.fulltext.models import Section, SectionType, SegmentedDocument, TextBlock

# Confidence for sections that contain rather than classify: front matter,
# and the whole-document fallback when no heading was detected. If the first
# real heading was missed, the container has swallowed it.
FALLBACK_CONFIDENCE = 0.5
# Confidence for a heading matched by an unanchored, word-bounded search
# rather than the anchored pattern ("Supplementary materials online").
PARTIAL_MATCH_CONFIDENCE = 0.7

# Assumed body size when no block carries a positive font size.
_DEFAULT_FONT_SIZE = 12.0
# A heading is short; a line longer than this is prose whatever its font.
_MAX_HEADING_CHARS = 100
# The title fallback must exceed the body median by this factor before the
# largest first-page line is believed to be the title.
_TITLE_SIZE_RATIO = 1.5
# A vertical gap larger than this multiple of the line height separates
# paragraphs; the leading within a paragraph is smaller.
_PARAGRAPH_GAP_RATIO = 1.5
# A line set in from the line above by at least this many ems (its own font
# size), and at most PARAGRAPH_INDENT_MAX_EM, starts a paragraph when the line
# after it returns to the left edge: a first-line indent, the only paragraph
# signal in a journal set without space between paragraphs.
PARAGRAPH_INDENT_MIN_EM = 0.5
# Further in than this is another column or a centred line, not an indent.
PARAGRAPH_INDENT_MAX_EM = 4.0
# How far, in ems, two lines' left edges may differ and still be one edge.
LEFT_EDGE_TOLERANCE_EM = 0.25
# The most lines one heading may wrap over ("3.2. Metformin modulated ..." /
# "function in mice ..." / "mellitus").
MAX_HEADING_LINES = 3

# A section number opening a heading: "2.", "2.1." or "2.1.3" and then a
# letter. Each part has at most two digits, so a year ("2025 Elsevier") is not
# one, and the first does not start with 0, so a quantity ("0.5 mM") is not.
# Group 1 is the number, whose parts give the heading's depth; group 2 the
# character after it, which must be a letter (Unicode category L, the test
# Rust's ``\p{L}`` makes: ``[^\W\d_]`` admits "½" here and a combining mark
# there).
_HEADING_NUMBER_RE = re.compile(r"^([1-9][0-9]?(?:\.[0-9]{1,2})*)\.?\s+(.)")

# "1.2  Introduction" -> "introduction"; "Discussion:" -> "discussion".
_LEADING_NUMBERING_RE = re.compile(r"^[\d.\s)\]]+")
_TRAILING_PUNCTUATION_RE = re.compile(r"[:.?!]+$")


# Page furniture (#591): a line whose text, digits aside, recurs at one height
# on at least this share of the document's pages, and on at least
# FURNITURE_MIN_PAGES of them, is a running head, a footer, a page number or a
# licence stamp, if it also sits at its page's top or bottom edge (see
# page_furniture). Measured over 231 PDFs (bioRxiv/medRxiv and Unpaywall journal
# PDFs): the furniture found recurs on 0.35 to 1.0 of the pages (an odd/even
# running head at 0.43-0.46; below 0.4 it is kept, e.g. bioRxiv's stamp on a
# document's landscape pages), content repeating at one height on at most 0.27
# (list numbering, "Figure 2", table cells), and the threshold moves the lines
# set aside by 2% between 0.3 and 0.6.
FURNITURE_PAGE_RATIO = 0.4
FURNITURE_MIN_PAGES = 3
# How far apart, in points, two lines' tops may be and still be one height.
FURNITURE_Y_TOLERANCE = 2.0
# More furniture lines than this at one edge of one page are the page's own
# content (a table filling it), not furniture: the corpus's longest edge run of
# furniture is 13 lines, bioRxiv's stamp.
FURNITURE_MAX_EDGE_LINES = 20
# A page number has at most this many digits.
_PAGE_NUMBER_MAX_DIGITS = 6

_ASCII_DIGITS_RE = re.compile(r"[0-9]+")


def _is_bare_number(text: str) -> bool:
    return 0 < len(text) <= _PAGE_NUMBER_MAX_DIGITS and text.isascii() and text.isdigit()


def page_furniture(blocks: list[TextBlock]) -> set[int]:
    """The indexes of *blocks* that are page furniture.

    A line is furniture when its text with every run of ASCII digits replaced
    by ``#`` (so "Page 3 of 12" and "Page 4 of 12" agree) recurs on at least
    ``max(FURNITURE_MIN_PAGES, ceil(FURNITURE_PAGE_RATIO * pages))`` pages at
    one height: lines with the same masked text are taken in order of their
    ``y``, a run of them whose tops lie within ``FURNITURE_Y_TOLERANCE`` of the
    previous one is one height, and every line in a run reaching that many
    pages is a candidate. The pages are ``max(page_num) + 1``. A run of bare
    numbers is a candidate only if each number is its page's index plus one
    constant, which is what a page number is and a manuscript's line numbers
    and a table's values are not. A run can drift: each top is compared with
    the previous one, not with the first.

    A candidate is furniture only at its page's edge: the page's lines in order
    of ``y``, the candidates from the top down to the first other line and from
    the bottom up to the last, so a table whose cells repeat at one height
    across pages ("0.12" and "3.4" both read ``#.#``) is kept wherever it has a
    line above and below it. More than ``FURNITURE_MAX_EDGE_LINES`` at one edge
    are the page's content and are kept. A line whose ``y`` is not finite is
    never furniture and is passed over.
    """
    lines = [i for i, b in enumerate(blocks) if math.isfinite(b.y)]
    if not lines:
        return set()
    pages = max(blocks[i].page_num for i in lines) + 1
    needed = max(FURNITURE_MIN_PAGES, math.ceil(FURNITURE_PAGE_RATIO * pages))
    by_text: dict[str, list[int]] = {}
    by_page: dict[int, list[int]] = {}
    for i in lines:
        by_text.setdefault(_ASCII_DIGITS_RE.sub("#", blocks[i].text), []).append(i)
        by_page.setdefault(blocks[i].page_num, []).append(i)
    candidates: set[int] = set()
    for members in by_text.values():
        members.sort(key=lambda i: (blocks[i].y, i))
        run: list[int] = []
        for i in members:
            if run and blocks[i].y - blocks[run[-1]].y > FURNITURE_Y_TOLERANCE:
                candidates.update(_furniture_run(blocks, run, needed))
                run = []
            run.append(i)
        candidates.update(_furniture_run(blocks, run, needed))
    furniture: set[int] = set()
    for on_page in by_page.values():
        on_page.sort(key=lambda i: (blocks[i].y, i))
        for edge in (on_page, on_page[::-1]):
            run = list(itertools.takewhile(candidates.__contains__, edge))
            if len(run) <= FURNITURE_MAX_EDGE_LINES:
                furniture.update(run)
    return furniture


def _furniture_run(blocks: list[TextBlock], run: list[int], needed: int) -> list[int]:
    """*run* if it is furniture (see :func:`page_furniture`), else nothing."""
    if len({blocks[i].page_num for i in run}) < needed:
        return []
    numbers = [_is_bare_number(blocks[i].text) for i in run]
    if any(numbers):
        offsets = {
            int(blocks[i].text) - blocks[i].page_num if n else None for i, n in zip(run, numbers)
        }
        # A non-number in the run is a second offset (None) beside the numbers'.
        if len(offsets) != 1:
            return []
    return run


def drop_page_furniture(blocks: list[TextBlock]) -> list[TextBlock]:
    """*blocks* without their :func:`page_furniture`, in order."""
    furniture = page_furniture(blocks)
    return [b for i, b in enumerate(blocks) if i not in furniture]


def _median_font_size(blocks: list[TextBlock]) -> float:
    """Median font size of *blocks*, ignoring non-positive sizes.

    The median, not the mean, so headings and footnotes cannot drag the
    body-text estimate. Returns 12.0 when no block carries a usable size.
    """
    sizes = [b.font_size for b in blocks if b.font_size > 0]
    if not sizes:
        return _DEFAULT_FONT_SIZE
    return float(statistics.median(sizes))


def heading_number(text: str) -> str | None:
    """The section number opening a heading ("2.1" for "2.1. Animals"), else None."""
    match = _HEADING_NUMBER_RE.match(text)
    if match is None or not unicodedata.category(match.group(2)).startswith("L"):
        return None
    return match.group(1)


def heading_depth(text: str) -> int | None:
    """How deep a numbered heading sits ("2." is 1, "2.1." is 2), else None."""
    number = heading_number(text)
    return number.count(".") + 1 if number is not None else None


def _starts_indented(blocks: list[TextBlock], i: int) -> bool:
    """Whether ``blocks[i]`` is a paragraph's indented first line.

    It sits below the line before it, set in from that line's left edge by
    PARAGRAPH_INDENT_MIN_EM to PARAGRAPH_INDENT_MAX_EM ems; the line before it
    ends short of its own right edge, as a paragraph's last line does; and the
    line after it, if any, returns to the left edge. The last two keep a
    reference list's hanging indent from reading as paragraphs: a reference's
    wrapped first line runs to the margin, and its further wrapped lines are
    all set in.
    """
    if i == 0:
        return False
    previous, block = blocks[i - 1], blocks[i]
    em = block.font_size
    indent = block.x - previous.x
    if block.y <= previous.y or not (
        PARAGRAPH_INDENT_MIN_EM * em <= indent <= PARAGRAPH_INDENT_MAX_EM * em
    ):
        return False
    tolerance = LEFT_EDGE_TOLERANCE_EM * em
    if previous.x + previous.width >= block.x + block.width - tolerance:
        return False
    if i + 1 == len(blocks):
        return True
    return abs(blocks[i + 1].x - previous.x) <= LEFT_EDGE_TOLERANCE_EM * em


def _numbers_from(title: str, parent_number: str | None) -> bool:
    """Whether a heading titled *title* numbers from a parent numbered *parent_number*.

    An unnumbered parent (None) contradicts no number.
    """
    return parent_number is None or title.startswith(parent_number + ".")


def _first_word_fits(above: TextBlock, line: TextBlock) -> bool:
    """Whether *line*'s first word would have fitted at the end of *above*.

    A wrapped line holds what did not fit on the line before, so its first word
    and a space added to *above* reach past the column; *line* is at most the
    column's width, so if they do not reach past *line*'s own width the word
    fitted and *line* is not a wrap. The word's width is *line*'s width shared
    out per character. A line with no width (a degenerate bbox) gives no
    evidence either way, so the wrap is not refused for it.
    """
    if not line.text or above.width <= 0 or line.width <= 0:
        return False
    word = len(line.text.split(" ", 1)[0]) + 1
    return above.width + line.width * word / len(line.text) <= line.width


def _join_blocks(blocks: list[TextBlock]) -> str:
    """Join lines, inserting a blank line at each paragraph boundary.

    A vertical gap larger than 1.5x the line's height is a paragraph
    boundary — the leading within a paragraph is smaller. So is a first-line
    indent (:func:`_starts_indented`), which journals set without space
    between paragraphs use instead. A column or page boundary sends the gap
    negative, so no break is inserted there: a paragraph continuing across the
    boundary stays one paragraph, and a PDF gives no signal that would
    distinguish it from one that ends at it.
    """
    lines: list[str] = []
    previous_bottom: float | None = None
    for i, block in enumerate(blocks):
        # A degenerate bbox (height == 0) makes gap_threshold 0, so any
        # positive gap ahead of this block reads as a paragraph break. Left
        # as-is: a degenerate bbox gives no usable leading to derive a real
        # threshold from, and guessing a floor would be an assumed size —
        # against this module's measured-not-assumed rule.
        gap_threshold = block.height * _PARAGRAPH_GAP_RATIO
        gap = previous_bottom is not None and block.y - previous_bottom > gap_threshold
        if gap or _starts_indented(blocks, i):
            lines.append("")
        lines.append(block.text)
        previous_bottom = block.y + block.height
    return "\n".join(lines)


@dataclass(frozen=True)
class _Marker:
    """A heading found in the block list.

    ``index`` is its first line and ``end`` one past its last (a heading may
    wrap); ``depth`` is 1 at top level, else its section number's depth.
    """

    index: int
    end: int
    section_type: SectionType
    title: str
    confidence: float
    depth: int


class SectionSegmenter:
    """Segment a biomedical publication's text lines into standard sections.

    Headings are detected by font size against the document's median — with
    bold or italic as the rescue for body-sized headings — and classified against an
    anchored pattern table, with an unanchored word-bounded search as the
    lower-confidence fallback.
    """

    #: Anchored heading patterns per section type. Every
    #: :class:`SectionType` member except the reserved ``TITLE``,
    #: ``FRONT_MATTER`` and ``UNKNOWN`` must have an entry here —
    #: ``test_every_section_type_is_produced_or_declared_reserved`` enforces
    #: it, so a member added without a producer fails the build.
    SECTION_PATTERNS: dict[SectionType, list[str]] = {
        SectionType.ABSTRACT: [
            r"^abstract$",
            r"^summary$",
        ],
        SectionType.INTRODUCTION: [
            r"^introduction$",
            r"^background\s+and\s+introduction$",
        ],
        SectionType.BACKGROUND: [
            r"^background$",
            r"^literature\s+review$",
        ],
        SectionType.METHODS: [
            r"^methods$",
            r"^methodology$",
            r"^materials\s+and\s+methods$",
            r"^methods\s+and\s+materials$",
            r"^experimental\s+procedures?$",
            r"^experimental\s+methods$",
        ],
        SectionType.RESULTS: [
            r"^results$",
            r"^findings$",
            r"^results\s+and\s+discussion$",
        ],
        SectionType.DISCUSSION: [
            r"^discussion$",
            r"^discussion\s+and\s+conclusion$",
        ],
        SectionType.CONCLUSION: [
            r"^conclusion$",
            r"^conclusions$",
            r"^concluding\s+remarks$",
            r"^summary\s+and\s+conclusions?$",
        ],
        SectionType.ACKNOWLEDGMENTS: [
            r"^acknowledgments?$",
            r"^acknowledgements?$",
        ],
        SectionType.REFERENCES: [
            r"^references$",
            r"^bibliography$",
            r"^literature\s+cited$",
            r"^works\s+cited$",
        ],
        SectionType.SUPPLEMENTARY: [
            r"^supplementary\s+materials?$",
            r"^supplementary\s+information$",
            r"^supporting\s+information$",
        ],
        SectionType.APPENDIX: [
            r"^appendix$",
            r"^appendices$",
        ],
        SectionType.FUNDING: [
            # "Financial disclosure(s)" is deliberately absent: CONFLICTS
            # owns the disclosure family, in both numbers. Listing the
            # singular here once sent "Financial disclosure" to FUNDING and
            # "Financial disclosures" to CONFLICTS, decided only by dict
            # iteration order.
            r"^funding$",
            r"^funding\s+sources?$",
            r"^financial\s+support$",
            r"^grant\s+support$",
            r"^funding\s+and\s+acknowledgments?$",
            r"^funding\s+and\s+acknowledgements?$",
            r"^funding\s+information$",
            r"^funding\s+statement$",
            r"^source\s+of\s+funding$",
            r"^sources?\s+of\s+support$",
        ],
        SectionType.CONFLICTS: [
            r"^conflicts?\s+of\s+interest$",
            r"^competing\s+interests?$",
            r"^disclosures?$",
            r"^declaration\s+of\s+interests?$",
            r"^financial\s+disclosures?$",
            r"^conflict\s+of\s+interest\s+statement$",
            r"^declaration\s+of\s+competing\s+interests?$",
            r"^potential\s+conflicts?\s+of\s+interest$",
        ],
        SectionType.DATA_AVAILABILITY: [
            r"^data\s+availability$",
            r"^data\s+sharing$",
            r"^data\s+access$",
            r"^availability\s+of\s+data$",
            r"^data\s+availability\s+statement$",
            r"^data\s+and\s+materials?\s+availability$",
            r"^code\s+and\s+data\s+availability$",
        ],
        SectionType.AUTHOR_CONTRIBUTIONS: [
            # Covers "Author contributions", "Authors contributions",
            # "Author's contributions" (singular possessive), and both
            # apostrophe renderings of "Authors' contributions" — ASCII '
            # and U+2019 (’), which is what InDesign/Word actually produce.
            r"^authors?['’]?s?\s+contributions?$",
            r"^contributors?$",
            r"^credit\s+authorship$",
            # Elsevier's wording: "CRediT authorship contribution statement".
            r"^credit\s+authorship\s+contributions?(?:\s+statement)?$",
            r"^authorship\s+contributions?$",
        ],
    }

    def __init__(self, font_size_threshold: float = 1.2, min_heading_size: float = 10.0) -> None:
        """Initialise the segmenter.

        Args:
            font_size_threshold: Multiplier over the document's median font
                size above which a line is heading-sized without being bold or
                italic.
            min_heading_size: Font-size floor for headings, capped at the
                body text's median size, so a paper set small still has its
                bold body-size headings.
        """
        self.font_size_threshold = font_size_threshold
        self.min_heading_size = min_heading_size
        self._exact_patterns = {
            section_type: [re.compile(p, re.IGNORECASE) for p in patterns]
            for section_type, patterns in self.SECTION_PATTERNS.items()
        }
        # The partial pass searches the same pattern, unanchored and
        # word-bounded. Upstream compared the regex *source* against the
        # heading as literal text, which made every multi-word pattern
        # unmatchable (r"\s+" never occurs in prose) and let the reverse
        # containment classify a heading "A" as ABSTRACT. The non-capturing
        # group keeps the \b anchors binding the whole pattern, not the
        # first and last branch, should a pattern ever gain a top-level
        # alternation.
        self._partial_patterns = {
            section_type: [re.compile(rf"\b(?:{p.strip('^$')})\b", re.IGNORECASE) for p in patterns]
            for section_type, patterns in self.SECTION_PATTERNS.items()
        }

    def _match_section_type(self, text: str) -> tuple[SectionType, float]:
        """Classify a heading, returning ``(section_type, confidence)``.

        Exact anchored matches win at 1.0; an unanchored word-bounded search
        is the 0.7 fallback; ``(UNKNOWN, 0.0)`` means no pattern claimed it.
        """
        normalized = text.lower().strip()
        normalized = _LEADING_NUMBERING_RE.sub("", normalized)
        normalized = _TRAILING_PUNCTUATION_RE.sub("", normalized)

        for section_type, patterns in self._exact_patterns.items():
            for pattern in patterns:
                if pattern.match(normalized):
                    return (section_type, 1.0)

        for section_type, patterns in self._partial_patterns.items():
            for pattern in patterns:
                if pattern.search(normalized):
                    return (section_type, PARTIAL_MATCH_CONFIDENCE)

        return (SectionType.UNKNOWN, 0.0)

    def _is_potential_header(self, block: TextBlock, median_font_size: float) -> bool:
        """Whether *block* looks like a section heading.

        Heading-sized (or body-sized but bold or italic), short, and carrying
        at least one alphabetic character — a bare "3." is numbering, not a
        heading. The floor is ``min_heading_size`` or the body size, whichever
        is smaller: a journal set in 8 pt sets its headings in 8 pt bold.
        """
        if block.font_size < min(self.min_heading_size, median_font_size):
            return False
        heading_sized = block.font_size >= median_font_size * self.font_size_threshold
        if not heading_sized and not (block.is_bold or block.is_italic):
            return False
        if len(block.text) > _MAX_HEADING_CHARS:
            return False
        if not any(c.isalpha() for c in block.text):
            return False
        return True

    def segment_document(
        self, blocks: list[TextBlock], metadata: dict[str, Any] | None = None
    ) -> SegmentedDocument:
        """Segment *blocks* into a :class:`SegmentedDocument`.

        Args:
            blocks: Text lines in reading order, as produced by
                :meth:`PyMuPDFConverter.extract_blocks
                <bmlib.fulltext.pdf_converter.PyMuPDFConverter.extract_blocks>`.
            metadata: Optional document metadata; only ``title`` and
                ``file_path`` are read. Without a ``title`` (from ``convert()``
                or ``extract_blocks_with_warnings().metadata_title``) only the
                metadata title is lost. Stored on the result as-is.

        Returns:
            The segmented document. With no blocks, a document with no
            sections; with blocks but no detected headings, one ``UNKNOWN``
            section titled "Full Text" at 0.5 confidence. Page furniture
            (:func:`page_furniture`) is set aside first, so a running head
            or a page number is in no section and a paragraph a page break
            interrupted reads on.
        """
        metadata = metadata or {}
        blocks = drop_page_furniture(blocks)
        median_size = _median_font_size(blocks)
        markers = self._identify_section_markers(blocks, median_size)
        return SegmentedDocument(
            file_path=str(metadata.get("file_path") or ""),
            title=self._extract_title(blocks, metadata, median_size),
            sections=self._extract_sections(blocks, markers),
            metadata=metadata,
        )

    def _identify_section_markers(
        self, blocks: list[TextBlock], median_font_size: float
    ) -> list[_Marker]:
        """Find the headings, each with the lines it wraps over and its depth.

        A heading-sized line is classified by the anchored patterns, then the
        word-bounded search. A body-sized one, set apart only by bold or
        italic, must match a pattern exactly or carry a section number: the
        search would read a bold credit line, "Methodology. A. Smith: ...", as
        Methods. A numbered heading's depth is its number's ("2.1." is 2); an
        unnumbered one is top level. A heading that names no section is kept
        only as a subsection under an earlier, shallower heading, whose type
        and confidence it takes, unless that heading's own number does not
        open this one's: at top level there is no type to give it, and under a
        heading it does not number from, the heading it does belong to was not
        found ("3.1." after an unnamed "3. Metformin lowers glucose" is not
        Methods' because "2." was). A bold quantity numbered like a subsection
        ("2.5 mg/kg" under "2.", "1.5 h" under an unnumbered "Results") still
        reads as one.
        """
        markers: list[_Marker] = []
        # The headings enclosing the current one, outermost first.
        enclosing: list[_Marker] = []
        i = 0
        while i < len(blocks):
            block = blocks[i]
            if not self._is_potential_header(block, median_font_size):
                i += 1
                continue
            end = self._heading_end(blocks, i)
            title = " ".join(b.text for b in blocks[i:end])
            numbered = heading_depth(title)
            depth = numbered or 1
            section_type, confidence = self._match_section_type(title)
            heading_sized = block.font_size >= median_font_size * self.font_size_threshold
            if not heading_sized and confidence < 1.0 and numbered is None:
                section_type = SectionType.UNKNOWN
            # Left as is until a marker is made: a line that is not one
            # closes nothing.
            parents = [m for m in enclosing if m.depth < depth]
            if section_type is SectionType.UNKNOWN:
                parent_number = heading_number(parents[-1].title) if parents else None
                if depth == 1 or not parents or not _numbers_from(title, parent_number):
                    i += 1
                    continue
                section_type, confidence = parents[-1].section_type, parents[-1].confidence
            marker = _Marker(i, end, section_type, title, confidence, depth)
            markers.append(marker)
            enclosing = [*parents, marker]
            i = end
        return markers

    def _heading_end(self, blocks: list[TextBlock], i: int) -> int:
        """One past the last line of the heading that starts at ``blocks[i]``.

        A heading wraps onto the lines after it that share its font, size,
        weight, slant and page, start below it by at most 1.5 times its
        height (top to top), on its left edge, carry no section number of
        their own, and open with a word that would not have fitted on the line
        above — at most MAX_HEADING_LINES lines in all. The last test keeps a
        short italic subheading ("2.1. Hypothesis") from taking the italic
        body lines under it. A first line that names a section exactly is a
        whole heading, so "Methods" over a same-styled line is titled
        "Methods" alone.
        """
        first = blocks[i]
        if self._match_section_type(first.text)[1] == 1.0:
            return i + 1

        def style(b: TextBlock) -> tuple[str, float, bool, bool, int]:
            return (b.font_name, b.font_size, b.is_bold, b.is_italic, b.page_num)

        end = i + 1
        while end < len(blocks) and end - i < MAX_HEADING_LINES:
            line, above = blocks[end], blocks[end - 1]
            if style(line) != style(first):
                break
            if not 0 < line.y - above.y <= above.height * _PARAGRAPH_GAP_RATIO:
                break
            if abs(line.x - first.x) > LEFT_EDGE_TOLERANCE_EM * first.font_size:
                break
            if heading_depth(line.text) is not None:
                break
            if _first_word_fits(above, line):
                break
            end += 1
        return end

    def _extract_sections(
        self,
        blocks: list[TextBlock],
        markers: list[_Marker],
    ) -> list[Section]:
        """Slice *blocks* into sections at the markers, nested by depth."""
        if not blocks:
            return []

        if not markers:
            return [
                Section(
                    section_type=SectionType.UNKNOWN,
                    title="Full Text",
                    content=_join_blocks(blocks),
                    page_start=blocks[0].page_num,
                    page_end=blocks[-1].page_num,
                    confidence=FALLBACK_CONFIDENCE,
                )
            ]

        sections: list[Section] = []

        # Everything before the first marker is the front matter — title,
        # authors, an abstract whose heading was not detected. Upstream
        # dropped these blocks silently.
        front_blocks = blocks[: markers[0].index]
        if front_blocks:
            sections.append(
                Section(
                    section_type=SectionType.FRONT_MATTER,
                    title="Front Matter",
                    content=_join_blocks(front_blocks),
                    page_start=front_blocks[0].page_num,
                    page_end=front_blocks[-1].page_num,
                    confidence=FALLBACK_CONFIDENCE,
                )
            )

        # Each section under the nearest earlier one that is shallower.
        path: list[tuple[int, Section]] = []
        for k, marker in enumerate(markers):
            stop = markers[k + 1].index if k + 1 < len(markers) else len(blocks)
            section_blocks = blocks[marker.end : stop]
            # A heading with no body is still a heading. Dropping it — as
            # upstream did — says the paper has no such section when it has
            # an (empty) one, and loses the heading text with it.
            heading = blocks[marker.index]
            section = Section(
                section_type=marker.section_type,
                title=marker.title,
                content=_join_blocks(section_blocks),
                page_start=section_blocks[0].page_num if section_blocks else heading.page_num,
                page_end=section_blocks[-1].page_num if section_blocks else heading.page_num,
                confidence=marker.confidence,
            )
            while path and path[-1][0] >= marker.depth:
                path.pop()
            (path[-1][1].subsections if path else sections).append(section)
            path.append((marker.depth, section))
        return sections

    def _extract_title(
        self, blocks: list[TextBlock], metadata: dict[str, Any], median_font_size: float
    ) -> str | None:
        """Document title from corroborated metadata, else the largest first-page line.

        The metadata title is believed only where page 1 prints it. Real PDFs
        carry filenames, ``"untitled"`` and typesetters' job numbers in
        ``/Title``, and such a value used to beat a perfectly good large-font
        line (issue #56); a title the document itself never states is not the
        document's title. See :mod:`bmlib.fulltext._titles` for the rule, and
        for why a page with no text at all accepts rather than rejects.

        The fallback is believed only when it exceeds the body median by
        half again — otherwise an ordinary line would become the title of
        every PDF whose metadata is blank.
        """
        first_page = [b for b in blocks if b.page_num == 0]
        title = accepted_metadata_title(metadata, "\n".join(b.text for b in first_page))
        if title:
            return title
        if not first_page:
            return None
        candidate = max(first_page, key=lambda b: b.font_size)
        if candidate.font_size > median_font_size * _TITLE_SIZE_RATIO:
            return candidate.text
        return None


#: The segmenter :func:`match_section_type` classifies with. Its patterns do
#: not depend on the constructor's font thresholds, so one instance serves.
_CLASSIFIER = SectionSegmenter()


def match_section_type(text: str) -> tuple[SectionType, float]:
    """Classify a heading's text against :attr:`SectionSegmenter.SECTION_PATTERNS`.

    The segmenter's own rule, for a heading that did not come from a PDF (the
    Rust port's ``segmenter::match_section_type``): an anchored match is
    ``(type, 1.0)``, an unanchored word-bounded one ``(type,
    PARTIAL_MATCH_CONFIDENCE)``, and ``(SectionType.UNKNOWN, 0.0)`` means no
    pattern claimed it.
    """
    return _CLASSIFIER._match_section_type(text)
