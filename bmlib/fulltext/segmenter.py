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

import re
import statistics

from bmlib.fulltext.models import SectionType, TextBlock

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

# "1.2  Introduction" -> "introduction"; "Discussion:" -> "discussion".
_LEADING_NUMBERING_RE = re.compile(r"^[\d.\s)\]]+")
_TRAILING_PUNCTUATION_RE = re.compile(r"[:.?!]+$")


def _median_font_size(blocks: list[TextBlock]) -> float:
    """Median font size of *blocks*, ignoring non-positive sizes.

    The median, not the mean, so headings and footnotes cannot drag the
    body-text estimate. Returns 12.0 when no block carries a usable size.
    """
    sizes = [b.font_size for b in blocks if b.font_size > 0]
    if not sizes:
        return _DEFAULT_FONT_SIZE
    return float(statistics.median(sizes))


class SectionSegmenter:
    """Segment a biomedical publication's text lines into standard sections.

    Headings are detected by font size against the document's median — with
    bold as the rescue for body-sized headings — and classified against an
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
            r"^funding$",
            r"^funding\s+sources?$",
            r"^financial\s+support$",
            r"^financial\s+disclosure$",
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
            r"^author\s+contributions?$",
            r"^contributors?$",
            r"^credit\s+authorship$",
            r"^authorship\s+contributions?$",
            r"^authors?\s*\'\s*contributions?$",
        ],
    }

    def __init__(self, font_size_threshold: float = 1.2, min_heading_size: float = 10.0) -> None:
        """Initialise the segmenter.

        Args:
            font_size_threshold: Multiplier over the document's median font
                size above which a line is heading-sized without being bold.
            min_heading_size: Absolute font-size floor for headings.
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
        # containment classify a heading "A" as ABSTRACT.
        self._partial_patterns = {
            section_type: [re.compile(rf"\b{p.strip('^$')}\b", re.IGNORECASE) for p in patterns]
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

        Heading-sized (or body-sized but bold), short, and carrying at least
        one alphabetic character — a bare "3." is numbering, not a heading.
        """
        if block.font_size < self.min_heading_size:
            return False
        if block.font_size < median_font_size * self.font_size_threshold and not block.is_bold:
            return False
        if len(block.text) > _MAX_HEADING_CHARS:
            return False
        if not any(c.isalpha() for c in block.text):
            return False
        return True
