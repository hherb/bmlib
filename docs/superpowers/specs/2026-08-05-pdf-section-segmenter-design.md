# PDF section segmenter — design

_Date: 2026-08-05. Phase 2 row 8 of the bmlibrarian port._

Ports `bmlibrarian/pdf_processor/`'s `segmenter.py` and `models.py` into
`bmlib/fulltext/`, together with the span-level extraction the segmenter needs.

## The problem

`bmlib.fulltext` can turn a PDF into text — `PyMuPDFConverter.convert()` gives
`ConversionResult.text`, and `render_html()` reflows it into paragraphs — but
it cannot say which part of that text is the Methods and which is the
references. A JATS article has `body_sections`; a PDF-only article is an
undifferentiated blob.

That gap matters for everything downstream that reasons about where evidence
lives: risk-of-bias domains sit in Methods and Results, funding and
conflict-of-interest statements sit in their own back-matter sections, and a
reference list is pure noise to a model asked to judge a study.

Upstream has a segmenter for this. It has no unit tests —
`tests/test_pdf_processor.py` is a command-line demo script — so every
behaviour in it is currently unguarded, and seven defects are described below.

## Scope

**Standalone port.** The segmenter is exported and tested; wiring it into
`FullTextService`, `CochraneAssessor`, the rule-based extractors or
`TransparencyAnalyzer` is a separate decision, deliberately not taken here.

This repeats the shape `quality/cochrane_models.py` and `quality/extractors.py`
landed in, and carries the same risk — a vocabulary with no speaker. It is
accepted knowingly: the consumers each need their own design conversation, and
none of them is blocked by this landing first.

## What this delivers

- `SectionType` — the section vocabulary of a biomedical paper.
- `TextBlock` — one line of a PDF with its layout and font attributes.
- `Section` — a typed, titled span of the document with a confidence.
- `SegmentedDocument` — title, sections and metadata, with `get_section()` and
  `to_markdown()`.
- `SectionSegmenter` — `list[TextBlock]` + metadata → `SegmentedDocument`.
- `LayoutExtractor` — the protocol for a converter that can report lines.
- `PyMuPDFConverter.extract_blocks()` — the only implementation.

Data flow:

```
PDF path
  → PyMuPDFConverter.extract_blocks()        [needs bmlib[pdf]]
  → list[TextBlock]                          (one per PDF line)
  → SectionSegmenter.segment_document(blocks, metadata)
  → SegmentedDocument
```

## Decisions

### The models live in `fulltext/models.py`, the segmenter in its own file

`fulltext/models.py` is already where every dataclass in the package lives —
`FullTextResult`, the seven `JATS*` models, `FullTextSourceEntry` — and it
imports nothing from the package, so adding four more creates no cycle.
`SectionSegmenter` and its pattern table go in a new `fulltext/segmenter.py`,
which is where the behaviour and the ~100 lines of regex belong.

Upstream's `Document` is renamed `SegmentedDocument`. bmlib already has
`JATSArticle` and `FullTextResult`; a third type called `Document` invites the
wrong import at the wrong moment.

### Block extraction is an optional capability of the converter

bmlib opens PDFs in exactly one place. Rather than a second module that opens
them its own way, `PyMuPDFConverter` gains `extract_blocks()`, declared in a
`LayoutExtractor` protocol:

```python
@runtime_checkable
class LayoutExtractor(Protocol):
    def extract_blocks(self, pdf_path: Path) -> list[TextBlock]: ...
```

The protocol, not the `PDFConverter` ABC, is what declares it. An abstract
method on the ABC would break every third-party converter registered through
`_CONVERTER_REGISTRY`, and a backend that cannot report line geometry — a
future OCR or LLM-based converter — has no honest way to implement it. A caller
asks `isinstance(converter, LayoutExtractor)` and gets a clear answer.

One place opens PDFs, one place handles `fitz` errors, one place carries the
`pip install bmlib[pdf]` `ImportError`.

### `TextBlock` is a line, not a span

This is the one real departure from upstream, and it fixes the worst of the
seven defects.

PyMuPDF's `get_text("dict")` returns `blocks → lines → spans`, and it starts a
new span at every font change. Upstream flattens that to spans and discards the
line grouping it was handed. Two consequences, both bad:

- **Headings stop matching.** `"2. Materials and Methods"` typeset with the
  numeral in a different weight arrives as two blocks, `"2."` and
  `"Materials and Methods"` — and the pattern table is anchored, so a heading
  split anywhere in its middle matches nothing at all.
- **Prose is shattered.** An italic gene name mid-sentence splits one line into
  three blocks, which `'\n'.join` then writes as three lines.

So one `TextBlock` per **line**. Its `text` is the spans concatenated and then
whitespace-collapsed — concatenated, not joined with `" "`, because PyMuPDF
span text carries its own trailing spaces and joining doubles them. Its bbox is
the line's.

Font attributes come from the **dominant span**: the one contributing the most
non-whitespace characters, ties resolved to the first. One rule, and it lands
correctly in each case that matters — `"1. "` + `"Methods"` attributes to the
bold heading span; a body line carrying a superscript reference marker or an
inline 14pt formula attributes to the body span rather than to the outlier.
Taking the maximum font size instead would let any inline oddity inflate a body
line to heading size.

### Front matter is kept

Upstream's `_extract_sections()` iterates markers and slices
`blocks[start_idx + 1 : end_idx]`, so **every block before the first marker is
dropped** — the title, the authors, and an abstract whose heading was not
detected. Silently, with no warning and no count.

Those blocks become a leading `Section` of a new `SectionType.FRONT_MATTER`,
emitted only when non-empty, at confidence 0.5.

0.5, not 1.0: what precedes the first heading is a container, not a
classification. If the real first heading was missed, this section has
swallowed the introduction. That is the same confidence, for the same reason,
as the existing no-markers-at-all fallback.

### The partial matcher searches the pattern it already compiled

Upstream's fallback pass does this:

```python
pattern_text = pattern.strip('^$')
if pattern_text in normalized or normalized in pattern_text:
```

`pattern` there is the **regex source**, treated as literal text. So
`background\s+and\s+introduction` is looked for verbatim in the heading and can
never be found — every multi-word pattern is dead in the fallback. Meanwhile
the reverse test, `normalized in pattern_text`, matches a heading against a
*substring of the regex source*: a heading `"A"` is inside `"abstract"`, so it
is classified ABSTRACT at 0.7 confidence.

The fix reuses the single source of truth. Each pattern is compiled a second
time with its anchors removed and `\b…\b` around it, and the fallback runs
`.search()` on the normalised heading at 0.7 confidence. `\s+` now works
because it is still a regex; `"methods"` no longer matches `"methodsxyz"`
because of the boundaries — the same word-boundary lesson
`_INDUSTRY_STEMS`/`_INDUSTRY_WORDS` records in `transparency/analyzer.py`. The
reverse direction is deleted.

### Every `SectionType` member has a producer, except the two that are declared reserved

Upstream declares nineteen members and can produce fifteen — fourteen from the
pattern table plus `UNKNOWN` from the fallback. The other four have no producer
at all: `TITLE`, `MATERIALS_AND_METHODS`, `CONCLUSIONS`, `APPENDIX`. A caller asking
`get_section(SectionType.MATERIALS_AND_METHODS)` gets `None` for every paper
ever written, including one with a "Materials and Methods" heading — because
`^materials\s+and\s+methods$` maps to `METHODS`.

- **Removed:** `MATERIALS_AND_METHODS` and `CONCLUSIONS`. Both are exact
  duplicates of a member that already owns their patterns.
- **Added:** `APPENDIX` gets `^appendix$` and `^appendices$`, the latter moved
  off `SUPPLEMENTARY` where it did not belong. `FRONT_MATTER` is new, per
  above.
- **Reserved, deliberately:** `TITLE` and `UNKNOWN`. `UNKNOWN` is produced by
  the no-markers fallback and by a failed match. `TITLE` has no producer —
  `SegmentedDocument.title` carries the title, and `to_markdown()` renders it —
  but it stays as the name a caller building a title `Section` by hand would
  reach for, and `Section.to_markdown()` keeps rendering it at `#`.

A test asserts that every member except `TITLE`, `FRONT_MATTER` and `UNKNOWN`
has at least one pattern, so a member added without a producer fails the build.
It is the guard `test_every_pattern_maps_to_a_level_the_ranking_knows` gives
the transparency allow-lists.

### A heading with no body is reported, not dropped

Upstream skips a marker whose slice is empty (`if not section_blocks:
continue`), which discards the heading text along with it. Two adjacent
headings — a real layout, and also what a mis-detection produces — lose the
first one's existence entirely.

The section is emitted with `content=""`. `get_section(METHODS)` then returns a
`Section` a caller can see is empty, rather than `None`, which would say the
paper has no Methods heading when it has one.

### `extract_blocks()` raises rather than degrading

`convert()` returns a failed `ConversionResult` instead of raising, and that is
right for text: partial text is still useful, and `converted_pages` says how
partial. A partial *block list* carries no such signal — it is
indistinguishable from a sparse PDF — so `extract_blocks()` raises:
`FileNotFoundError` and `ValueError` from the shared `validate_pdf_path()`, and
`ValueError` wrapping a `fitz` failure on a corrupt or encrypted file.

A page that yields no spans contributes no blocks and is not an error. That is
what an image-only page is.

### Paragraph breaks stay as upstream wrote them, now over lines

A section's content is its blocks joined with newlines, with a blank line
inserted where the vertical gap to the previous block exceeds 1.5× the current
block's height. Kept as-is: at span granularity the rule fired on noise, and at
line granularity it is what it always meant to be — the gap between paragraphs
is larger than the leading within one.

A column or page boundary sends the gap negative, so no break is inserted
there. That is the right default for reflowed prose — a paragraph continuing
across a page break stays one paragraph — and it is documented rather than
"fixed", because a PDF gives no signal that distinguishes the two cases.

### Two smaller corrections

- `_calculate_avg_font_size` returns the **median** — it says so in its own
  docstring. Renamed `_median_font_size`, and computed once in
  `segment_document()` rather than recomputed inside `_extract_title()`.
- The `isinstance` sweep over every block in `segment_document()` is removed.
  Runtime type policing is not bmlib's idiom anywhere else, and it is O(n) over
  what can be tens of thousands of lines.

## Models

```python
class SectionType(Enum):
    TITLE, ABSTRACT, INTRODUCTION, BACKGROUND, METHODS, RESULTS,
    DISCUSSION, CONCLUSION, ACKNOWLEDGMENTS, REFERENCES, SUPPLEMENTARY,
    APPENDIX, FUNDING, CONFLICTS, DATA_AVAILABILITY, AUTHOR_CONTRIBUTIONS,
    FRONT_MATTER, UNKNOWN

@dataclass
class TextBlock:
    text: str
    page_num: int          # 0-indexed
    font_size: float
    font_name: str
    is_bold: bool
    is_italic: bool
    x: float
    y: float
    width: float
    height: float

@dataclass
class Section:
    section_type: SectionType
    title: str
    content: str
    page_start: int
    page_end: int
    confidence: float = 1.0
    subsections: list[Section] = field(default_factory=list)

    def to_markdown(self) -> str: ...

@dataclass
class SegmentedDocument:
    file_path: str = ""
    title: str | None = None
    authors: list[str] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def get_section(self, section_type: SectionType) -> Section | None: ...
    def to_markdown(self) -> str: ...
```

`authors` is never populated by the segmenter and stays anyway, documented as
reserved for a parser that can fill it — the `outcome_switching_detected`
precedent, where a field kept in the schema saves a migration later.
`subsections` is likewise carried and never populated: the segmenter emits a
flat list, and nesting is a heading-level question it does not answer.

No `to_dict()` / `from_dict()`. The `JATS*` models beside them have none
either; nothing persists a segmentation, and `to_markdown()` is the export
path. `FullTextSourceEntry` has them because it is stored.

`metadata` is optional on `segment_document()` — the segmenter reads only
`title` and `file_path` from it, so a caller who has not run `convert()` passes
nothing and loses only the metadata title.

## Upstream defects fixed

Each gets a named regression test.

| Defect | Consequence | Test |
|---|---|---|
| Span-level blocks | A heading split at a font change matches nothing; an italic word splits a sentence across lines | `test_a_heading_split_across_spans_is_one_block` |
| Everything before the first marker is dropped | Title, authors and an unlabelled abstract vanish silently | `test_front_matter_is_kept` |
| Partial matcher compares regex source as literal text | Multi-word patterns are dead; a heading `"A"` classifies as ABSTRACT | `test_a_multi_word_pattern_matches_partially`, `test_a_short_heading_is_not_a_partial_match` |
| Partial matcher has no word boundaries | `"methodsxyz"` classifies as METHODS | `test_a_partial_match_respects_word_boundaries` |
| Four enum members have no pattern | `get_section(MATERIALS_AND_METHODS)` is always `None` | `test_every_section_type_is_produced_or_declared_reserved` |
| An empty section is dropped with its heading | Two adjacent headings lose the first | `test_a_heading_with_no_body_is_still_reported` |
| `_calculate_avg_font_size` returns a median | Naming lie, and two sorts per document | covered by rename; no behaviour change |

## Deliberately not ported

- **`PDFExtractor`'s `extract_metadata()` and `extract_raw_text()`.**
  `PyMuPDFConverter.convert()` already returns both, on the same
  `ConversionResult`. Porting them gives bmlib two ways to read a PDF's text
  and metadata, which is the fork the port recipe exists to prevent.
- **`PDFExtractor` as a context-manager class.** `extract_blocks()` opens and
  closes the document inside one call, as `convert()` does. The class exists
  upstream so several extraction calls can share one open document; with
  `convert()` and `extract_blocks()` as the only two operations, and both
  taking a path, it buys an object lifetime nobody needs.
- **`Document.authors` population.** Author extraction from a PDF's front
  matter is its own heuristic problem — a real feature, not a port.

## Testing

`tests/test_segmenter.py` — new, and pure. Every behaviour above lives in the
segmenter, which takes `TextBlock`s, so the whole file runs without PyMuPDF by
building blocks directly:

- `TestHeadingDetection` — font-size threshold, the bold rescue, the length
  cap, the alphabetic-character requirement.
- `TestSectionExtraction` — boundaries, front matter, the empty-body heading,
  the no-markers fallback, paragraph breaks from vertical gaps.
- `TestPatternMatching` — exact matches at 1.0, partial at 0.7, word
  boundaries, leading numbering stripped, the deleted reverse direction.
- `TestSectionTypeCoverage` — every member has a producer or is declared
  reserved.
- `TestTitleExtraction` — metadata title preferred; largest first-page font as
  the fallback, and only when it clears the median by half again.
- `TestMarkdown` — `Section.to_markdown()` and `SegmentedDocument.to_markdown()`.

`tests/test_pdf_converter.py` — `TestExtractBlocks` under the existing
`skipif(not _HAS_FITZ)` guard, building real PDFs with `fitz` as
`TestPyMuPDFConversion` already does: line granularity, dominant-span
attribution, page numbering, and both error paths.
`issubclass(PyMuPDFConverter, LayoutExtractor)` needs no PDF and runs
unguarded.

PyMuPDF is not currently installed in the dev venv, so those tests would skip
here. It gets installed for this work rather than left to CI.

## Documentation

- `CHANGELOG.md` under `[Unreleased]`.
- `docs/manual/fulltext.md` — a segmentation section.
- `CLAUDE.md` — the directory tree, the module description, and
  `tests/test_segmenter.py` in the test-file mapping table.
- `ROADMAP.md` — a row under **Full text**.
- `HANDOVER.md` — the deliberate non-fixes worth defending in review: line
  granularity, front matter at 0.5, `TITLE` reserved, and `extract_blocks()`
  raising where `convert()` does not.
