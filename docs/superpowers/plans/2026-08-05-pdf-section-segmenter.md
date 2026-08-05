# PDF Section Segmenter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port bmlibrarian's PDF section segmenter into `bmlib/fulltext/` — heading-driven segmentation of a PDF's text lines into the standard sections of a biomedical paper, plus the line-level block extraction it consumes.

**Architecture:** Four dataclasses/enums join the existing `fulltext/models.py`; a new `fulltext/segmenter.py` holds `SectionSegmenter`; `PyMuPDFConverter` gains `extract_blocks()` declared by a `LayoutExtractor` protocol in `pdf_converter.py`. The segmenter is pure (no PyMuPDF) and is tested by building `TextBlock`s directly; only block extraction needs `fitz`.

**Tech Stack:** Python 3.11+, stdlib (`re`, `statistics`, `enum`, `dataclasses`), PyMuPDF behind the existing `bmlib[pdf]` extra, pytest.

**Spec:** `docs/superpowers/specs/2026-08-05-pdf-section-segmenter-design.md` — read it first; every design decision below is argued there.

## Global Constraints

- AGPL-3 header verbatim at the top of every new source file (copy from `bmlib/fulltext/segmenter.py`'s neighbours, e.g. `bmlib/fulltext/pdf_converter.py:1-15`).
- `from __future__ import annotations` in every new module; lowercase builtin generics (`list`, `dict`, `X | None`).
- Type hints and docstrings on every public function, class, and module.
- `uv` only, never pip. Tests: `uv run pytest tests/ -q`.
- Lint with the CI-pinned ruff, not the stale one in `.venv`: `uvx ruff@0.15.20 check .` and `uvx ruff@0.15.20 format --check .`. Run `uvx ruff@0.15.20 format .` to fix formatting.
- Line length 100.
- No network in tests. Tests needing PyMuPDF go under `@pytest.mark.skipif(not _HAS_FITZ, ...)` in `tests/test_pdf_converter.py`; `tests/test_segmenter.py` must import cleanly and pass without PyMuPDF installed.
- Commit messages are conventional (`feat(fulltext): ...`), body ending with:
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`

---

### Task 1: Section models in `fulltext/models.py`

**Files:**
- Modify: `bmlib/fulltext/models.py` (append after `FullTextSourceEntry`; add `from enum import Enum` to the imports)
- Modify: `bmlib/fulltext/__init__.py` (export the four new names)
- Test: `tests/test_segmenter.py` (new file — model tests go here, not in `test_fulltext_models.py`, so the port's tests stay together)

**Interfaces:**
- Consumes: nothing new.
- Produces (later tasks rely on these exact names):
  - `SectionType(Enum)` — members `TITLE, ABSTRACT, INTRODUCTION, BACKGROUND, METHODS, RESULTS, DISCUSSION, CONCLUSION, ACKNOWLEDGMENTS, REFERENCES, SUPPLEMENTARY, APPENDIX, FUNDING, CONFLICTS, DATA_AVAILABILITY, AUTHOR_CONTRIBUTIONS, FRONT_MATTER, UNKNOWN` (values are the lowercase member names).
  - `TextBlock(text: str, page_num: int, font_size: float, font_name: str, is_bold: bool, is_italic: bool, x: float, y: float, width: float, height: float)` — all positional, in this order.
  - `Section(section_type: SectionType, title: str, content: str, page_start: int, page_end: int, confidence: float = 1.0, subsections: list[Section] = [])` with `to_markdown() -> str`.
  - `SegmentedDocument(file_path: str = "", title: str | None = None, authors: list[str] = [], sections: list[Section] = [], metadata: dict[str, Any] = {})` with `get_section(section_type) -> Section | None` and `to_markdown() -> str`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_segmenter.py` with the AGPL header, then:

```python
"""Behaviour tests for PDF section segmentation.

The segmenter consumes ``TextBlock``s, so everything here runs without
PyMuPDF — blocks are built directly. The extraction half (PDF → blocks)
is tested in ``tests/test_pdf_converter.py``.
"""

from __future__ import annotations

from bmlib.fulltext.models import Section, SectionType, SegmentedDocument, TextBlock

# Font sizes used throughout: body text, a section heading, the paper title.
BODY_SIZE = 10.0
HEADING_SIZE = 13.0
TITLE_SIZE = 20.0


def block(
    text: str,
    *,
    page: int = 0,
    size: float = BODY_SIZE,
    bold: bool = False,
    y: float = 0.0,
    height: float = 12.0,
) -> TextBlock:
    """A TextBlock with the segmentation-relevant fields settable, rest defaulted."""
    return TextBlock(
        text=text,
        page_num=page,
        font_size=size,
        font_name="Helvetica",
        is_bold=bold,
        is_italic=False,
        x=72.0,
        y=y,
        width=400.0,
        height=height,
    )


class TestSectionModels:
    def test_get_section_returns_the_first_match_or_none(self):
        methods = Section(SectionType.METHODS, "Methods", "how", 0, 1)
        doc = SegmentedDocument(sections=[methods])
        assert doc.get_section(SectionType.METHODS) is methods
        assert doc.get_section(SectionType.RESULTS) is None

    def test_section_renders_at_heading_level_two(self):
        md = Section(SectionType.METHODS, "Methods", "We measured.", 0, 0).to_markdown()
        assert md.startswith("## Methods")
        assert "We measured." in md

    def test_a_title_section_renders_at_heading_level_one(self):
        md = Section(SectionType.TITLE, "A Trial", "", 0, 0).to_markdown()
        assert md.startswith("# A Trial")

    def test_subsections_render_at_heading_level_three(self):
        sub = Section(SectionType.UNKNOWN, "Participants", "Adults.", 0, 0)
        md = Section(
            SectionType.METHODS, "Methods", "Overview.", 0, 0, subsections=[sub]
        ).to_markdown()
        assert "### Participants" in md
        assert "Adults." in md

    def test_document_markdown_carries_title_authors_and_sections(self):
        doc = SegmentedDocument(
            title="A Trial",
            authors=["J Smith", "R Jones"],
            sections=[Section(SectionType.METHODS, "Methods", "We measured.", 0, 0)],
        )
        md = doc.to_markdown()
        assert "# A Trial" in md
        assert "**Authors:** J Smith, R Jones" in md
        assert "## Methods" in md

    def test_document_markdown_without_title_or_authors_has_neither_line(self):
        md = SegmentedDocument(
            sections=[Section(SectionType.METHODS, "Methods", "x", 0, 0)]
        ).to_markdown()
        assert not md.startswith("# ")
        assert "**Authors:**" not in md

    def test_str_summarises_rather_than_dumping_content(self):
        section = Section(SectionType.METHODS, "Methods", "x" * 5000, 0, 3)
        assert len(str(section)) < 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_segmenter.py -v`
Expected: FAIL at import — `ImportError: cannot import name 'Section' from 'bmlib.fulltext.models'`.

- [ ] **Step 3: Implement the models**

In `bmlib/fulltext/models.py`: add `from enum import Enum` to the imports (after `from dataclasses import ...`, keeping ruff's import order: `dataclasses`, `enum`, `typing`). Append at the end of the file:

```python
class SectionType(Enum):
    """Standard sections of a biomedical publication.

    ``TITLE`` is reserved: the segmenter carries the document title on
    :attr:`SegmentedDocument.title` and never emits a ``TITLE`` section, but
    the member stays as the name a caller building one by hand would reach
    for, and :meth:`Section.to_markdown` renders it at heading level one.
    ``FRONT_MATTER`` and ``UNKNOWN`` are containers, not classifications —
    what precedes the first detected heading, and text no heading claimed.
    Every other member has at least one heading pattern in
    :data:`bmlib.fulltext.segmenter.SectionSegmenter.SECTION_PATTERNS`.
    """

    TITLE = "title"
    ABSTRACT = "abstract"
    INTRODUCTION = "introduction"
    BACKGROUND = "background"
    METHODS = "methods"
    RESULTS = "results"
    DISCUSSION = "discussion"
    CONCLUSION = "conclusion"
    ACKNOWLEDGMENTS = "acknowledgments"
    REFERENCES = "references"
    SUPPLEMENTARY = "supplementary"
    APPENDIX = "appendix"
    FUNDING = "funding"
    CONFLICTS = "conflicts"
    DATA_AVAILABILITY = "data_availability"
    AUTHOR_CONTRIBUTIONS = "author_contributions"
    FRONT_MATTER = "front_matter"
    UNKNOWN = "unknown"


@dataclass
class TextBlock:
    """One text line of a PDF with its layout and font attributes.

    A line, not a span: PyMuPDF starts a new span at every font change, so a
    heading numbered in a different weight or a sentence holding an italic
    gene name would shatter into fragments no anchored heading pattern can
    match. Font attributes are those of the line's dominant span — see
    ``_line_to_block()`` in :mod:`bmlib.fulltext.pdf_converter`.
    """

    text: str
    page_num: int  # 0-indexed
    font_size: float
    font_name: str
    is_bold: bool
    is_italic: bool
    x: float
    y: float
    width: float
    height: float

    def __str__(self) -> str:
        """Return a short summary that does not dump the text."""
        return f"TextBlock(page={self.page_num}, font={self.font_size:.1f}, text={self.text[:50]!r})"


@dataclass
class Section:
    """A typed, titled span of a segmented document.

    ``page_start`` / ``page_end`` are 0-indexed and cover the section's
    content blocks; for a heading with no body they are the heading's page.
    ``confidence`` is 1.0 for an exact heading match, 0.7 for a partial one,
    and 0.5 for the two container sections (front matter, the no-headings
    fallback). ``subsections`` is carried for callers but never populated by
    the segmenter, which emits a flat list.
    """

    section_type: SectionType
    title: str
    content: str
    page_start: int
    page_end: int
    confidence: float = 1.0
    subsections: list[Section] = field(default_factory=list)

    def to_markdown(self) -> str:
        """Render as markdown — ``#`` for a TITLE section, ``##`` otherwise."""
        level = "#" if self.section_type is SectionType.TITLE else "##"
        md = f"{level} {self.title}\n\n{self.content}\n"
        for subsection in self.subsections:
            md += f"\n### {subsection.title}\n\n{subsection.content}\n"
        return md

    def __str__(self) -> str:
        """Return a short summary that does not dump the content."""
        return (
            f"Section({self.section_type.value}, pages={self.page_start}-{self.page_end}, "
            f"{len(self.content)} chars)"
        )


@dataclass
class SegmentedDocument:
    """A publication segmented into typed sections.

    ``authors`` is reserved: nothing populates it today — author extraction
    from PDF front matter is its own heuristic problem — but a parser that
    can fill it should not need a schema change. ``metadata`` is whatever
    the caller passed to ``segment_document()``, stored as-is.
    """

    file_path: str = ""
    title: str | None = None
    authors: list[str] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def get_section(self, section_type: SectionType) -> Section | None:
        """Return the first section of *section_type*, or None."""
        for section in self.sections:
            if section.section_type is section_type:
                return section
        return None

    def to_markdown(self) -> str:
        """Render the whole document as markdown."""
        md_parts: list[str] = []
        if self.title:
            md_parts.append(f"# {self.title}\n")
        if self.authors:
            md_parts.append(f"**Authors:** {', '.join(self.authors)}\n")
        for section in self.sections:
            md_parts.append("\n---\n")
            md_parts.append(f"**{section.title.upper()}**")
            md_parts.append("\n---\n\n")
            md_parts.append(section.to_markdown())
        return "\n".join(md_parts)

    def __str__(self) -> str:
        """Return a short summary that does not dump the sections."""
        return f"SegmentedDocument({self.file_path or '<no path>'}, {len(self.sections)} sections)"
```

In `bmlib/fulltext/__init__.py`: add `Section`, `SectionType`, `SegmentedDocument`, `TextBlock` to the `from bmlib.fulltext.models import (...)` block and to `__all__`, both alphabetically. (`ContentKind, FullTextResult, FullTextSourceEntry, JATS..., Section, SectionType, SegmentedDocument, TextBlock` — note `SegmentedDocument` sorts after `SectionType`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_segmenter.py -v`
Expected: all PASS.

Run: `uv run pytest tests/ -q` — no regressions.

- [ ] **Step 5: Lint and commit**

Run: `uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .` — fix anything it reports.

```bash
git add bmlib/fulltext/models.py bmlib/fulltext/__init__.py tests/test_segmenter.py
git commit -m "feat(fulltext): add the section models for the PDF segmenter

SectionType, TextBlock, Section and SegmentedDocument, ported from
bmlibrarian's pdf_processor.models. TextBlock documents line granularity;
TITLE and authors are reserved rather than dead; MATERIALS_AND_METHODS and
CONCLUSIONS are dropped as exact duplicates of members that own their
patterns.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Segmenter core — pattern table, matching, heading detection

**Files:**
- Create: `bmlib/fulltext/segmenter.py`
- Test: `tests/test_segmenter.py` (append)

**Interfaces:**
- Consumes: `SectionType`, `TextBlock` from Task 1.
- Produces (Task 3 relies on these exact names):
  - `SectionSegmenter.__init__(font_size_threshold: float = 1.2, min_heading_size: float = 10.0)`
  - `SectionSegmenter.SECTION_PATTERNS: dict[SectionType, list[str]]` (class attribute)
  - `SectionSegmenter._match_section_type(text: str) -> tuple[SectionType, float]`
  - `SectionSegmenter._is_potential_header(block: TextBlock, median_font_size: float) -> bool`
  - Module-level `_median_font_size(blocks: list[TextBlock]) -> float`
  - Module constants `FALLBACK_CONFIDENCE = 0.5`, `PARTIAL_MATCH_CONFIDENCE = 0.7`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_segmenter.py`. Add `_median_font_size` and `SectionSegmenter` to a new import line (keep the models import as-is):

```python
from bmlib.fulltext.segmenter import SectionSegmenter, _median_font_size
```

```python
class TestPatternMatching:
    def setup_method(self):
        self.segmenter = SectionSegmenter()

    def test_an_exact_heading_matches_at_full_confidence(self):
        assert self.segmenter._match_section_type("Methods") == (SectionType.METHODS, 1.0)

    def test_matching_ignores_case(self):
        assert self.segmenter._match_section_type("MATERIALS AND METHODS") == (
            SectionType.METHODS,
            1.0,
        )

    def test_leading_numbering_is_stripped(self):
        assert self.segmenter._match_section_type("3.  Results") == (SectionType.RESULTS, 1.0)

    def test_trailing_punctuation_is_stripped(self):
        assert self.segmenter._match_section_type("Discussion:") == (SectionType.DISCUSSION, 1.0)

    def test_a_multi_word_pattern_matches_partially(self):
        # Upstream compared the regex source against the heading as literal
        # text, so r"supplementary\s+materials?" could never occur in prose
        # and every multi-word pattern was dead in the fallback.
        assert self.segmenter._match_section_type("Supplementary materials online") == (
            SectionType.SUPPLEMENTARY,
            0.7,
        )

    def test_a_heading_containing_a_known_name_matches_partially(self):
        assert self.segmenter._match_section_type("Study methods overview") == (
            SectionType.METHODS,
            0.7,
        )

    def test_a_short_heading_is_not_a_partial_match(self):
        # Upstream's reverse containment ("a" in "abstract") classified a
        # heading "A" as ABSTRACT at 0.7. The reverse direction is deleted.
        assert self.segmenter._match_section_type("A") == (SectionType.UNKNOWN, 0.0)

    def test_a_partial_match_respects_word_boundaries(self):
        assert self.segmenter._match_section_type("methodsxyz") == (SectionType.UNKNOWN, 0.0)

    def test_appendices_is_an_appendix_not_supplementary(self):
        assert self.segmenter._match_section_type("Appendices") == (SectionType.APPENDIX, 1.0)


class TestSectionTypeCoverage:
    def test_every_section_type_is_produced_or_declared_reserved(self):
        # TITLE is reserved for callers, FRONT_MATTER and UNKNOWN are the
        # containers the extraction itself produces; every other member must
        # have a heading pattern, or get_section() lies for it forever.
        reserved = {SectionType.TITLE, SectionType.FRONT_MATTER, SectionType.UNKNOWN}
        assert set(SectionSegmenter.SECTION_PATTERNS) == set(SectionType) - reserved


class TestHeadingDetection:
    def setup_method(self):
        self.segmenter = SectionSegmenter()

    def test_a_font_below_the_minimum_is_not_a_heading(self):
        assert not self.segmenter._is_potential_header(block("Methods", size=8.0), 10.0)

    def test_body_sized_text_must_be_bold(self):
        assert not self.segmenter._is_potential_header(block("Methods", size=10.0), 10.0)
        assert self.segmenter._is_potential_header(block("Methods", size=10.0, bold=True), 10.0)

    def test_a_clearly_larger_font_needs_no_bold(self):
        assert self.segmenter._is_potential_header(block("Methods", size=13.0), 10.0)

    def test_a_long_line_is_not_a_heading(self):
        assert not self.segmenter._is_potential_header(block("m" * 101, size=14.0, bold=True), 10.0)

    def test_digits_alone_are_not_a_heading(self):
        assert not self.segmenter._is_potential_header(block("123 456", size=14.0, bold=True), 10.0)


class TestMedianFontSize:
    def test_the_median_not_the_mean(self):
        blocks = [block("a", size=10.0), block("b", size=10.0), block("c", size=40.0)]
        assert _median_font_size(blocks) == 10.0

    def test_non_positive_sizes_are_ignored(self):
        blocks = [block("a", size=0.0), block("b", size=-1.0), block("c", size=10.0)]
        assert _median_font_size(blocks) == 10.0

    def test_no_usable_sizes_returns_the_default(self):
        assert _median_font_size([]) == 12.0
        assert _median_font_size([block("a", size=0.0)]) == 12.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_segmenter.py -v`
Expected: FAIL at import — `ModuleNotFoundError: No module named 'bmlib.fulltext.segmenter'`. (The Task 1 tests still pass once the import line is the only failure; the collection error is the correct red.)

- [ ] **Step 3: Implement the segmenter core**

Create `bmlib/fulltext/segmenter.py` with the AGPL header, then:

```python
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
from typing import Any

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_segmenter.py -v`
Expected: all PASS.

- [ ] **Step 5: Lint and commit**

Run: `uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .`

```bash
git add bmlib/fulltext/segmenter.py tests/test_segmenter.py
git commit -m "feat(fulltext): port the segmenter's heading detection and pattern table

The partial matcher searches the compiled pattern, unanchored and
word-bounded, instead of comparing regex source text against the heading —
upstream's comparison made every multi-word pattern unmatchable and let the
reverse containment classify a heading 'A' as ABSTRACT. APPENDIX gains its
patterns; every non-reserved SectionType member must have a producer or the
coverage test fails.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `segment_document()` — markers, extraction, front matter, title

**Files:**
- Modify: `bmlib/fulltext/segmenter.py` (add methods to `SectionSegmenter` and one module function)
- Modify: `bmlib/fulltext/__init__.py` (export `SectionSegmenter` and the two confidence constants' owner module is not exported — just the class)
- Test: `tests/test_segmenter.py` (append)

**Interfaces:**
- Consumes: everything Task 2 produced.
- Produces:
  - `SectionSegmenter.segment_document(blocks: list[TextBlock], metadata: dict[str, Any] | None = None) -> SegmentedDocument`
  - Module-level `_join_blocks(blocks: list[TextBlock]) -> str` (private, used by extraction)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_segmenter.py`:

```python
def paper_blocks() -> list[TextBlock]:
    """A miniature paper: front matter, four sections, two pages.

    Sizes matter: the median is 11.5 (five body lines at 10, four headings
    at 13, one title at 20), so 13-point headings are below 1.2x the median
    and need their bold; the 20-point title clears every threshold but
    matches no pattern.
    """
    return [
        block("Aspirin and Mortality: A Trial", size=TITLE_SIZE),
        block("J Smith, R Jones"),
        block("Abstract", size=HEADING_SIZE, bold=True),
        block("We tested aspirin against placebo."),
        block("Methods", size=HEADING_SIZE, bold=True),
        block("Participants were randomised by coin toss."),
        block("Results", size=HEADING_SIZE, bold=True),
        block("Mortality fell.", page=1),
        block("References", size=HEADING_SIZE, bold=True, page=1),
        block("1. Prior trial.", page=1),
    ]


class TestSectionExtraction:
    def setup_method(self):
        self.segmenter = SectionSegmenter()

    def test_a_paper_segments_into_its_sections(self):
        doc = self.segmenter.segment_document(paper_blocks())
        assert [s.section_type for s in doc.sections] == [
            SectionType.FRONT_MATTER,
            SectionType.ABSTRACT,
            SectionType.METHODS,
            SectionType.RESULTS,
            SectionType.REFERENCES,
        ]
        methods = doc.get_section(SectionType.METHODS)
        assert methods is not None
        assert methods.content == "Participants were randomised by coin toss."
        assert methods.confidence == 1.0

    def test_front_matter_is_kept(self):
        # Upstream dropped everything before the first detected heading —
        # title, authors, an abstract whose heading was missed — silently.
        doc = self.segmenter.segment_document(paper_blocks())
        front = doc.sections[0]
        assert front.section_type is SectionType.FRONT_MATTER
        assert front.content == "Aspirin and Mortality: A Trial\nJ Smith, R Jones"
        assert front.confidence == 0.5

    def test_a_heading_with_no_body_is_still_reported(self):
        # Upstream skipped a marker whose slice was empty, discarding the
        # heading with it — two adjacent headings lost the first entirely.
        blocks = [
            block("Methods", size=HEADING_SIZE, bold=True),
            block("Results", size=HEADING_SIZE, bold=True),
            block("Mortality fell."),
        ]
        doc = self.segmenter.segment_document(blocks)
        methods = doc.get_section(SectionType.METHODS)
        assert methods is not None
        assert methods.content == ""
        assert methods.page_start == 0 and methods.page_end == 0

    def test_no_headings_returns_one_unknown_section(self):
        doc = self.segmenter.segment_document([block("Just prose."), block("More prose.")])
        assert [s.section_type for s in doc.sections] == [SectionType.UNKNOWN]
        fallback = doc.sections[0]
        assert fallback.title == "Full Text"
        assert fallback.confidence == 0.5
        assert "Just prose." in fallback.content
        assert "More prose." in fallback.content

    def test_no_blocks_returns_no_sections(self):
        doc = self.segmenter.segment_document([])
        assert doc.sections == []
        assert doc.title is None

    def test_a_vertical_gap_becomes_a_paragraph_break(self):
        blocks = [
            block("Methods", size=HEADING_SIZE, bold=True, y=100.0),
            block("First paragraph.", y=120.0, height=12.0),
            # Gap: 160 - (120 + 12) = 28 > 12 * 1.5 — a paragraph boundary.
            block("Second paragraph.", y=160.0, height=12.0),
        ]
        doc = self.segmenter.segment_document(blocks)
        assert doc.sections[-1].content == "First paragraph.\n\nSecond paragraph."

    def test_a_page_boundary_is_not_a_paragraph_break(self):
        # The next page starts higher on the canvas, so the gap is negative;
        # a paragraph continuing across the page break stays one paragraph.
        blocks = [
            block("Methods", size=HEADING_SIZE, bold=True, y=100.0),
            block("wrapped line one", y=700.0, height=12.0),
            block("continues at the top of the next page", page=1, y=72.0, height=12.0),
        ]
        doc = self.segmenter.segment_document(blocks)
        assert doc.sections[-1].content == (
            "wrapped line one\ncontinues at the top of the next page"
        )

    def test_metadata_is_optional(self):
        doc = self.segmenter.segment_document([block("Just prose.")])
        assert doc.file_path == ""
        assert doc.metadata == {}


class TestTitleExtraction:
    def setup_method(self):
        self.segmenter = SectionSegmenter()

    def test_the_metadata_title_wins(self):
        doc = self.segmenter.segment_document(paper_blocks(), {"title": "From Metadata"})
        assert doc.title == "From Metadata"

    def test_the_largest_first_page_line_is_the_fallback_title(self):
        doc = self.segmenter.segment_document(paper_blocks())
        assert doc.title == "Aspirin and Mortality: A Trial"

    def test_a_title_must_clear_the_median_by_half_again(self):
        doc = self.segmenter.segment_document([block("Modest line"), block("Body text.")])
        assert doc.title is None

    def test_no_first_page_blocks_means_no_title(self):
        doc = self.segmenter.segment_document([block("Late text", page=2)])
        assert doc.title is None

    def test_file_path_comes_from_metadata(self):
        doc = self.segmenter.segment_document(paper_blocks(), {"file_path": "paper.pdf"})
        assert doc.file_path == "paper.pdf"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_segmenter.py -v`
Expected: the new tests FAIL with `AttributeError: 'SectionSegmenter' object has no attribute 'segment_document'`; Task 1–2 tests still pass.

- [ ] **Step 3: Implement extraction**

In `bmlib/fulltext/segmenter.py`, add after `_median_font_size()`:

```python
def _join_blocks(blocks: list[TextBlock]) -> str:
    """Join lines, inserting a blank line at each paragraph-sized gap.

    A vertical gap larger than 1.5x the line's height is a paragraph
    boundary — the leading within a paragraph is smaller. A column or page
    boundary sends the gap negative, so no break is inserted there: a
    paragraph continuing across the boundary stays one paragraph, and a PDF
    gives no signal that would distinguish it from one that ends at it.
    """
    lines: list[str] = []
    previous_bottom: float | None = None
    for block in blocks:
        gap_threshold = block.height * _PARAGRAPH_GAP_RATIO
        if previous_bottom is not None and block.y - previous_bottom > gap_threshold:
            lines.append("")
        lines.append(block.text)
        previous_bottom = block.y + block.height
    return "\n".join(lines)
```

Add to `SectionSegmenter` (after `_is_potential_header`):

```python
    def segment_document(
        self, blocks: list[TextBlock], metadata: dict[str, Any] | None = None
    ) -> SegmentedDocument:
        """Segment *blocks* into a :class:`SegmentedDocument`.

        Args:
            blocks: Text lines in reading order, as produced by
                :meth:`PyMuPDFConverter.extract_blocks
                <bmlib.fulltext.pdf_converter.PyMuPDFConverter.extract_blocks>`.
            metadata: Optional document metadata; only ``title`` and
                ``file_path`` are read, so a caller who has not run
                ``convert()`` can pass nothing and loses only the metadata
                title. Stored on the result as-is.

        Returns:
            The segmented document. With no blocks, a document with no
            sections; with blocks but no detected headings, one ``UNKNOWN``
            section titled "Full Text" at 0.5 confidence.
        """
        metadata = metadata or {}
        median_size = _median_font_size(blocks)
        markers = self._identify_section_markers(blocks, median_size)
        return SegmentedDocument(
            file_path=str(metadata.get("file_path", "")),
            title=self._extract_title(blocks, metadata, median_size),
            sections=self._extract_sections(blocks, markers),
            metadata=metadata,
        )

    def _identify_section_markers(
        self, blocks: list[TextBlock], median_font_size: float
    ) -> list[tuple[int, SectionType, str, float]]:
        """Find heading blocks, as ``(index, type, title, confidence)`` tuples."""
        markers: list[tuple[int, SectionType, str, float]] = []
        for i, block in enumerate(blocks):
            if not self._is_potential_header(block, median_font_size):
                continue
            section_type, confidence = self._match_section_type(block.text)
            if section_type is not SectionType.UNKNOWN:
                markers.append((i, section_type, block.text, confidence))
        return markers

    def _extract_sections(
        self,
        blocks: list[TextBlock],
        markers: list[tuple[int, SectionType, str, float]],
    ) -> list[Section]:
        """Slice *blocks* into sections at the marker boundaries."""
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
        front_blocks = blocks[: markers[0][0]]
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

        for i, (start_idx, section_type, title, confidence) in enumerate(markers):
            end_idx = markers[i + 1][0] if i + 1 < len(markers) else len(blocks)
            section_blocks = blocks[start_idx + 1 : end_idx]
            # A heading with no body is still a heading. Dropping it — as
            # upstream did — says the paper has no such section when it has
            # an (empty) one, and loses the heading text with it.
            heading = blocks[start_idx]
            sections.append(
                Section(
                    section_type=section_type,
                    title=title,
                    content=_join_blocks(section_blocks),
                    page_start=section_blocks[0].page_num if section_blocks else heading.page_num,
                    page_end=section_blocks[-1].page_num if section_blocks else heading.page_num,
                    confidence=confidence,
                )
            )
        return sections

    def _extract_title(
        self, blocks: list[TextBlock], metadata: dict[str, Any], median_font_size: float
    ) -> str | None:
        """Document title from metadata, else the largest first-page line.

        The fallback is believed only when it exceeds the body median by
        half again — otherwise an ordinary line would become the title of
        every PDF whose metadata is blank.
        """
        title = metadata.get("title")
        if title:
            return str(title)
        first_page = [b for b in blocks if b.page_num == 0]
        if not first_page:
            return None
        candidate = max(first_page, key=lambda b: b.font_size)
        if candidate.font_size > median_font_size * _TITLE_SIZE_RATIO:
            return candidate.text
        return None
```

In `bmlib/fulltext/__init__.py`: add `from bmlib.fulltext.segmenter import SectionSegmenter` (a new import line, alphabetically after the `pdf_converter` import block) and `"SectionSegmenter"` to `__all__`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_segmenter.py -v`
Expected: all PASS.

Run: `uv run pytest tests/ -q` — no regressions.

- [ ] **Step 5: Lint and commit**

Run: `uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .`

```bash
git add bmlib/fulltext/segmenter.py bmlib/fulltext/__init__.py tests/test_segmenter.py
git commit -m "feat(fulltext): segment text blocks into a SegmentedDocument

Front matter is kept as a 0.5-confidence section instead of silently
dropped; a heading with no body is reported with empty content instead of
vanishing; the paragraph-gap rule stays as upstream wrote it, now
meaningful at line granularity, with the negative-gap page boundary
documented rather than 'fixed'.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `LayoutExtractor` protocol and `PyMuPDFConverter.extract_blocks()`

**Files:**
- Modify: `bmlib/fulltext/pdf_converter.py`
- Modify: `bmlib/fulltext/__init__.py` (export `LayoutExtractor`)
- Test: `tests/test_pdf_converter.py` (append)

**Interfaces:**
- Consumes: `TextBlock` (Task 1), `SectionSegmenter` / `SectionType` (Tasks 1–3, for the end-to-end test).
- Produces:
  - `LayoutExtractor` — `@runtime_checkable` `Protocol` with `extract_blocks(pdf_path: Path) -> list[TextBlock]`
  - `PyMuPDFConverter.extract_blocks(pdf_path: Path) -> list[TextBlock]`
  - Module-level `_line_to_block(raw_line: dict[str, Any], page_num: int) -> TextBlock | None`

- [ ] **Step 1: Install PyMuPDF so the guarded tests run here, not just in CI**

```bash
uv pip install -e ".[all,dev]"
uv run python -c "import fitz; print(fitz.version)"
```

Expected: a version tuple prints. Note: `tests/test_pdf_converter.py`'s two previously-skipped PyMuPDF tests now run, and `TestRegistry.test_pymupdf_requires_dependency` (guarded `skipif(_HAS_FITZ, ...)`) now skips instead — the suite's skip count changes by −1 net.

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_pdf_converter.py`. Extend the existing `from bmlib.fulltext.pdf_converter import (...)` to also import `LayoutExtractor` and `_line_to_block`, and add:

```python
from bmlib.fulltext.models import SectionType
from bmlib.fulltext.segmenter import SectionSegmenter
```

Then the unguarded tests (no PyMuPDF needed — `_line_to_block` is pure over the dict shape PyMuPDF emits):

```python
class TestLayoutExtractorProtocol:
    def test_pymupdf_converter_implements_the_protocol(self):
        assert issubclass(PyMuPDFConverter, LayoutExtractor)


class TestLineToBlock:
    """_line_to_block over the dict shape PyMuPDF's get_text("dict") emits."""

    @staticmethod
    def _line(spans, bbox=(72.0, 100.0, 300.0, 112.0)):
        return {"spans": spans, "bbox": bbox}

    @staticmethod
    def _span(text, size=10.0, font="Helvetica", flags=0):
        return {"text": text, "size": size, "font": font, "flags": flags}

    def test_a_heading_split_across_spans_is_one_block(self):
        # PyMuPDF starts a new span at every font change; upstream flattened
        # to spans, so "2." and "Materials and Methods" were separate blocks
        # and the anchored heading pattern could never match.
        line = self._line(
            [
                self._span("2. ", size=12.0),
                self._span("Materials and Methods", size=12.0, font="Helvetica-Bold", flags=16),
            ]
        )
        result = _line_to_block(line, page_num=0)
        assert result is not None
        assert result.text == "2. Materials and Methods"

    def test_font_attributes_come_from_the_dominant_span(self):
        line = self._line(
            [
                self._span("2. ", size=12.0),
                self._span("Materials and Methods", size=14.0, font="Helvetica-Bold", flags=16),
            ]
        )
        result = _line_to_block(line, page_num=0)
        assert result.is_bold is True
        assert result.font_name == "Helvetica-Bold"
        assert result.font_size == 14.0

    def test_a_superscript_marker_does_not_restyle_the_line(self):
        line = self._line(
            [
                self._span("Aspirin reduced mortality", size=10.0),
                self._span("1", size=6.0, flags=1),  # superscript reference marker
            ]
        )
        result = _line_to_block(line, page_num=0)
        assert result.font_size == 10.0
        assert result.is_bold is False

    def test_span_text_is_concatenated_not_double_spaced(self):
        # Span text carries its own trailing spaces; joining with " " would
        # double them, and the whitespace collapse must repair any that
        # PyMuPDF already carries.
        line = self._line([self._span("word "), self._span("next")])
        assert _line_to_block(line, page_num=0).text == "word next"

    def test_an_empty_line_is_none(self):
        assert _line_to_block(self._line([]), page_num=0) is None
        assert _line_to_block(self._line([self._span("   ")]), page_num=0) is None

    def test_geometry_comes_from_the_line_bbox(self):
        result = _line_to_block(
            self._line([self._span("text")], bbox=(72.0, 100.0, 300.0, 112.0)), page_num=3
        )
        assert (result.x, result.y) == (72.0, 100.0)
        assert (result.width, result.height) == (228.0, 12.0)
        assert result.page_num == 3
```

And the guarded tests:

```python
@pytest.mark.skipif(not _HAS_FITZ, reason="PyMuPDF not installed")
class TestExtractBlocks:
    @staticmethod
    def _write_pdf(path, pages):
        """Write a PDF; *pages* is a list of (x, y, text, fontname, fontsize) lists."""
        import fitz

        doc = fitz.open()
        for page_items in pages:
            page = doc.new_page()
            for x, y, text, fontname, fontsize in page_items:
                page.insert_text((x, y), text, fontname=fontname, fontsize=fontsize)
        doc.save(str(path))
        doc.close()

    def test_each_line_is_one_block_on_its_page(self, tmp_path):
        pdf = tmp_path / "two_pages.pdf"
        self._write_pdf(
            pdf,
            [
                [(72, 100, "Methods", "hebo", 14), (72, 130, "We randomised patients.", "helv", 10)],
                [(72, 100, "Results", "hebo", 14)],
            ],
        )
        blocks = get_converter("pymupdf").extract_blocks(pdf)
        texts = [(b.text, b.page_num) for b in blocks]
        assert ("Methods", 0) in texts
        assert ("We randomised patients.", 0) in texts
        assert ("Results", 1) in texts

    def test_a_real_pdf_heading_in_mixed_fonts_is_one_line(self, tmp_path):
        import fitz

        pdf = tmp_path / "mixed.pdf"
        doc = fitz.open()
        page = doc.new_page()
        # Same baseline, two fonts — PyMuPDF reports two spans in one line.
        page.insert_text((72, 100), "2. ", fontname="helv", fontsize=14)
        page.insert_text((92, 100), "Materials and Methods", fontname="hebo", fontsize=14)
        doc.save(str(pdf))
        doc.close()

        blocks = get_converter("pymupdf").extract_blocks(pdf)
        assert any(b.text == "2. Materials and Methods" for b in blocks)

    def test_bold_is_read_from_the_font_flags(self, tmp_path):
        pdf = tmp_path / "bold.pdf"
        self._write_pdf(pdf, [[(72, 100, "Methods", "hebo", 14), (72, 130, "Body.", "helv", 10)]])
        blocks = get_converter("pymupdf").extract_blocks(pdf)
        by_text = {b.text: b for b in blocks}
        assert by_text["Methods"].is_bold is True
        assert by_text["Body."].is_bold is False

    def test_an_empty_page_contributes_no_blocks(self, tmp_path):
        pdf = tmp_path / "empty.pdf"
        self._write_pdf(pdf, [[]])
        assert get_converter("pymupdf").extract_blocks(pdf) == []

    def test_a_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            get_converter("pymupdf").extract_blocks(tmp_path / "nope.pdf")

    def test_a_non_pdf_suffix_raises(self, tmp_path):
        f = tmp_path / "notes.txt"
        f.write_text("hello")
        with pytest.raises(ValueError, match="not a PDF"):
            get_converter("pymupdf").extract_blocks(f)

    def test_a_corrupt_pdf_raises_rather_than_degrading(self, tmp_path):
        # Unlike convert(), which returns a failed result because partial
        # text is useful, a partial block list is indistinguishable from a
        # sparse PDF — so this path raises.
        pdf = tmp_path / "corrupt.pdf"
        pdf.write_bytes(b"%PDF-1.4 this is not a valid pdf body")
        with pytest.raises(ValueError, match="Failed to extract text blocks"):
            get_converter("pymupdf").extract_blocks(pdf)

    def test_a_pdf_paper_segments_end_to_end(self, tmp_path):
        pdf = tmp_path / "paper.pdf"
        self._write_pdf(
            pdf,
            [
                [
                    (72, 90, "Aspirin and Mortality: A Trial", "helv", 20),
                    (72, 120, "J Smith, R Jones", "helv", 10),
                    (72, 160, "Abstract", "hebo", 14),
                    (72, 180, "We tested aspirin against placebo.", "helv", 10),
                    (72, 220, "Methods", "hebo", 14),
                    (72, 240, "Participants were randomised by coin toss.", "helv", 10),
                ],
                [
                    (72, 90, "Results", "hebo", 14),
                    (72, 110, "Mortality fell.", "helv", 10),
                ],
            ],
        )
        blocks = get_converter("pymupdf").extract_blocks(pdf)
        doc = SectionSegmenter().segment_document(blocks, {"file_path": str(pdf)})

        assert doc.sections[0].section_type is SectionType.FRONT_MATTER
        assert doc.title == "Aspirin and Mortality: A Trial"
        methods = doc.get_section(SectionType.METHODS)
        assert methods is not None
        assert "randomised" in methods.content
        assert doc.get_section(SectionType.RESULTS) is not None
        assert doc.file_path == str(pdf)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_pdf_converter.py -v`
Expected: FAIL at import — `ImportError: cannot import name 'LayoutExtractor'`.

- [ ] **Step 4: Implement**

In `bmlib/fulltext/pdf_converter.py`:

1. Extend the typing import: `from typing import Any, Protocol, runtime_checkable`.
2. Add below the existing imports: `from bmlib.fulltext.models import TextBlock` (models imports nothing from the package, so no cycle).
3. Add near the other module constants:

```python
# PyMuPDF span-flag bits (get_text("dict") -> span["flags"]).
_SPAN_BOLD_FLAG = 1 << 4
_SPAN_ITALIC_FLAG = 1 << 1
```

4. Add after the `PDFConverter` ABC:

```python
@runtime_checkable
class LayoutExtractor(Protocol):
    """A converter that can report a PDF's text lines with their layout.

    A protocol rather than an abstract method on :class:`PDFConverter`: a
    backend that cannot report line geometry — an OCR or LLM-based
    converter — is not forced to fake it, and third-party converters
    registered before this protocol existed keep working. Ask
    ``isinstance(converter, LayoutExtractor)``.
    """

    def extract_blocks(self, pdf_path: Path) -> list[TextBlock]:
        """Extract one :class:`TextBlock` per text line, in reading order."""
        ...
```

5. Add the module-level helpers (before `_WS_RE` is fine — after works too; keep them adjacent to `LayoutExtractor`):

```python
def _span_text_weight(span: dict[str, Any]) -> int:
    """Non-whitespace characters a span contributes to its line."""
    return sum(not c.isspace() for c in span.get("text", ""))


def _line_to_block(raw_line: dict[str, Any], page_num: int) -> TextBlock | None:
    """Collapse one PyMuPDF line dict into a :class:`TextBlock`.

    One block per *line*, not per span: PyMuPDF starts a new span at every
    font change, so a heading numbered in a different weight ("2." +
    "Materials and Methods") or a sentence holding an italic gene name
    would otherwise shatter into fragments no anchored heading pattern can
    match. Span text is concatenated, not joined with spaces — spans carry
    their own trailing spaces, and joining would double them. Font
    attributes come from the dominant span (most non-whitespace characters,
    ties to the first), so a superscript reference marker or an inline
    formula cannot restyle the line.

    Returns None for a line with no non-whitespace text.
    """
    spans = raw_line.get("spans", [])
    text = _normalize("".join(span.get("text", "") for span in spans))
    if not text:
        return None
    dominant = max(spans, key=_span_text_weight)
    flags = int(dominant.get("flags", 0))
    x0, y0, x1, y1 = raw_line.get("bbox", (0.0, 0.0, 0.0, 0.0))
    return TextBlock(
        text=text,
        page_num=page_num,
        font_size=float(dominant.get("size", 12.0)),
        font_name=str(dominant.get("font", "")),
        is_bold=bool(flags & _SPAN_BOLD_FLAG),
        is_italic=bool(flags & _SPAN_ITALIC_FLAG),
        x=float(x0),
        y=float(y0),
        width=float(x1 - x0),
        height=float(y1 - y0),
    )
```

Note: `_normalize` already exists in this module (whitespace collapse + strip) — reuse it, do not add another.

6. Add to `PyMuPDFConverter`, after `convert()`:

```python
    def extract_blocks(self, pdf_path: Path) -> list[TextBlock]:
        """Extract one :class:`TextBlock` per text line, in reading order.

        Unlike :meth:`convert`, which returns a failed result because
        partial text is still useful, this raises: a partial block list is
        indistinguishable from a sparse PDF, so degradation would be
        silent. A page with no extractable text (an image-only scan)
        contributes no blocks — that is not an error.

        Args:
            pdf_path: Path to the PDF file.

        Returns:
            One block per text line, pages in order, lines in reading order.

        Raises:
            FileNotFoundError: If the file does not exist.
            ValueError: If the path is not a PDF file, or the file cannot
                be parsed (corrupt or encrypted).
        """
        self.validate_pdf_path(pdf_path)
        blocks: list[TextBlock] = []
        try:
            with self._fitz.open(str(pdf_path)) as doc:
                for page_num in range(len(doc)):
                    page_dict = doc[page_num].get_text("dict")
                    for raw_block in page_dict.get("blocks", []):
                        if raw_block.get("type") != 0:  # not a text block (image)
                            continue
                        for raw_line in raw_block.get("lines", []):
                            text_block = _line_to_block(raw_line, page_num)
                            if text_block is not None:
                                blocks.append(text_block)
        except Exception as e:  # noqa: BLE001 — any parse failure becomes ValueError
            raise ValueError(f"Failed to extract text blocks from {pdf_path}: {e}") from e
        return blocks
```

7. In `bmlib/fulltext/__init__.py`: add `LayoutExtractor` to the `pdf_converter` import block and to `__all__` (alphabetical: after `JATSTableInfo`, before `PDFConverter`).

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_pdf_converter.py -v`
Expected: all PASS, none skipped except `test_pymupdf_requires_dependency`.

If `test_a_real_pdf_heading_in_mixed_fonts_is_one_line` fails because PyMuPDF returned two *lines* (geometry, not logic): nudge the second `insert_text` x-coordinate closer to where the first string ends (e.g. 90–95) so the spans share a line, and re-run. The synthetic `TestLineToBlock` version is the logic's regression test; this one exists to prove the shape against a real PDF.

Run: `uv run pytest tests/ -q` — no regressions (expect the skip-count change from Step 1).

- [ ] **Step 6: Lint and commit**

Run: `uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .`

```bash
git add bmlib/fulltext/pdf_converter.py bmlib/fulltext/__init__.py tests/test_pdf_converter.py
git commit -m "feat(fulltext): line-level block extraction behind a LayoutExtractor protocol

PyMuPDFConverter.extract_blocks() emits one TextBlock per text line — not
per span, which is the upstream defect that made a heading in mixed fonts
unmatchable — with font attributes from the line's dominant span. A
protocol rather than an abstract method, so a backend that cannot report
line geometry is not forced to fake it. Raises on a corrupt file: a
partial block list is indistinguishable from a sparse PDF.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Documentation

**Files:**
- Modify: `CHANGELOG.md` (`[Unreleased]` → `### Added`)
- Modify: `docs/manual/fulltext.md` (new `## Section Segmentation` before `## FullTextError`)
- Modify: `CLAUDE.md` (directory tree, `fulltext/` module description, test-file mapping)
- Modify: `ROADMAP.md` (new row under **Full text**)

**Interfaces:** none — prose only. Verify every code snippet you write into the manual actually runs.

- [ ] **Step 1: CHANGELOG entries**

Add to the top of the `### Added` list under `## [Unreleased]`:

```markdown
- **PDF section segmenter** (`bmlib.fulltext.SectionSegmenter`) — Phase 2
  row 8 of the bmlibrarian port. `segment_document()` turns a PDF's text
  lines into a `SegmentedDocument` of typed, titled `Section`s, located by
  heading detection (font size against the document's median, bold as the
  rescue for body-sized headings) and an anchored pattern table covering
  every producible `SectionType`. Three content-losing upstream defects are
  fixed, each with a named regression test: everything before the first
  detected heading was silently dropped (now a `FRONT_MATTER` section at
  0.5 confidence); a heading with no body vanished along with its heading
  text (now reported with empty content); and the partial-match fallback
  compared regex *source* against the heading as literal text, which killed
  every multi-word pattern and classified a heading "A" as ABSTRACT (now an
  unanchored, word-bounded search of the same compiled pattern, at 0.7).
  Enum members no pattern could produce are gone (`MATERIALS_AND_METHODS`,
  `CONCLUSIONS` — duplicates of the members that own their patterns) or
  given patterns (`APPENDIX`); `TITLE` stays, reserved for callers.
- **`PyMuPDFConverter.extract_blocks()`** and the `LayoutExtractor`
  protocol (`bmlib.fulltext`) — one `TextBlock` per text *line*, not per
  span. PyMuPDF starts a new span at every font change, so upstream's
  span-level extraction shattered a mixed-font heading ("2." + "Materials
  and Methods") into fragments no anchored pattern could match, and split
  sentences at every italic word. Font attributes come from the line's
  dominant span, so a superscript marker cannot restyle a line. Declared as
  a protocol rather than on the `PDFConverter` ABC so a backend that cannot
  report line geometry is not forced to fake it. Raises on a corrupt file
  rather than returning a partial list — unlike `convert()`, whose partial
  text is useful, a partial block list is indistinguishable from a sparse
  PDF.
```

- [ ] **Step 2: Manual section**

In `docs/manual/fulltext.md`, insert before `## FullTextError`:

```markdown
## Section Segmentation

Split a PDF's text into the standard sections of a biomedical paper —
abstract, introduction, methods, results, discussion, funding, conflicts,
data availability, and the rest of `SectionType`. Extraction needs
`bmlib[pdf]`; the segmenter itself is pure and works on any
`list[TextBlock]`.

```python
from pathlib import Path
from bmlib.fulltext import SectionSegmenter, SectionType, get_converter

converter = get_converter("pymupdf")
blocks = converter.extract_blocks(Path("paper.pdf"))       # list[TextBlock]
document = SectionSegmenter().segment_document(blocks)     # SegmentedDocument

methods = document.get_section(SectionType.METHODS)
if methods is not None:
    print(methods.title, methods.confidence)
    print(methods.content[:200])

print(document.to_markdown())
```

### How sections are found

A line is a candidate heading when its font size clears the document's
median by the configured factor (`font_size_threshold`, default 1.2) — or
fails that but is bold — and it is short (≤100 characters) and contains at
least one letter. Candidate headings are classified against an anchored,
case-insensitive pattern table (`"3.  Results"` matches: leading numbering
and trailing punctuation are stripped first). A heading no anchored pattern
claims gets a second, word-bounded partial pass at 0.7 confidence
(`"Supplementary materials online"` → `SUPPLEMENTARY`).

Sections are the text between consecutive headings. Three container rules:

| Situation | Result |
|---|---|
| Text before the first heading | A `FRONT_MATTER` section, confidence 0.5 |
| No headings detected at all | One `UNKNOWN` section titled "Full Text", confidence 0.5 |
| A heading directly followed by another | Reported with `content == ""`, not dropped |

### `TextBlock` granularity

`extract_blocks()` emits one `TextBlock` per text **line**. PyMuPDF starts
a new span at every font change, so span-level blocks would shatter a
mixed-font heading into fragments no anchored pattern can match. Font
attributes (`font_size`, `font_name`, `is_bold`, `is_italic`) are those of
the line's *dominant* span — the one contributing the most non-whitespace
characters — so a superscript reference marker cannot restyle its line.

`extract_blocks()` **raises** (`FileNotFoundError`, `ValueError`) rather
than returning a partial result: unlike `convert()`, whose partial text is
useful, a partial block list is indistinguishable from a sparse PDF. A
page with no extractable text simply contributes no blocks.

Only `PyMuPDFConverter` implements extraction; test for the capability
with `isinstance(converter, LayoutExtractor)`.

### `SegmentedDocument`

| Field / method | Notes |
|---|---|
| `title` | Metadata title if present, else the largest first-page line when it clears the median font size by 1.5× |
| `authors` | **Reserved** — never populated today |
| `sections` | Flat list, document order; `Section.subsections` is likewise reserved |
| `metadata` | Whatever was passed to `segment_document()`, stored as-is |
| `get_section(t)` | First section of that type, or `None` — an empty-content section means the heading exists with no body |
| `to_markdown()` | Title, authors, then each section under `##` headings |

`segment_document()`'s `metadata` argument is optional; only `title` and
`file_path` are read from it.
```

Note: the outer fence around this markdown is for the plan; in the manual the Python block keeps its own fences. Also update the manual's `## Module layout` list (line ~14) to mention `segmenter.py`.

- [ ] **Step 3: CLAUDE.md**

In the directory tree, between `pdf_converter.py` and `service.py`:

```
│   ├── segmenter.py         # Heading-driven section segmentation of PDF text lines
```

In the `fulltext/` module description bullet, append:

> `SectionSegmenter` (in `segmenter.py`) segments the `TextBlock` lines from `PyMuPDFConverter.extract_blocks()` — an optional capability declared by the `LayoutExtractor` protocol, not by the `PDFConverter` ABC — into a `SegmentedDocument` of typed sections. One block per PDF *line* with dominant-span font attributes, because span-level extraction shattered mixed-font headings; front matter is kept as a section rather than dropped; standalone for now — nothing in `fulltext` or `quality` calls it yet.

In the test-file mapping table, `fulltext/` row: add `test_segmenter.py`.

- [ ] **Step 4: ROADMAP.md**

Under **Full text (`bmlib.fulltext`)**, after the NCBI tier row:

```markdown
| ✅ Done | PDF section segmenter | Phase 2 row 8 of the bmlibrarian port. `SectionSegmenter` turns the `TextBlock` lines from `PyMuPDFConverter.extract_blocks()` (new, behind the `LayoutExtractor` protocol) into a `SegmentedDocument` of typed sections. Blocks are PDF *lines* with dominant-span font attributes — upstream's span-level blocks shattered any mixed-font heading. Three content-losing upstream defects fixed with named regression tests: front matter silently dropped, an empty-bodied heading discarded with its heading text, and a partial matcher that compared regex source against the heading as literal text. Standalone: nothing wires it into `FullTextService` or `quality/` yet (unreleased) |
```

- [ ] **Step 5: Verify and commit**

```bash
uv run pytest tests/ -q
uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .
```

Run the manual's example against a real PDF if one is handy; at minimum, run the example's imports:
`uv run python -c "from bmlib.fulltext import SectionSegmenter, SectionType, LayoutExtractor, get_converter"`

```bash
git add CHANGELOG.md docs/manual/fulltext.md CLAUDE.md ROADMAP.md
git commit -m "docs(fulltext): document the PDF section segmenter

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### After the plan: session close-out (not a plan task)

Per the nextsession workflow, once all five tasks are done: update `HANDOVER.md` (the `[Unreleased]` summary, the test counts — the skip count changed in Task 4 — and new "deliberate non-fixes": line granularity with dominant-span attribution; front matter at 0.5 confidence; `TITLE` and `authors`/`subsections` reserved; `extract_blocks()` raising where `convert()` returns a failed result; the negative-gap page boundary documented rather than fixed), push the branch, and open the PR to `main`.
