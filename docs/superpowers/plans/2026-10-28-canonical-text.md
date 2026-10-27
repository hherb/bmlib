# Canonical text, part 1: model, JATS, Markdown, plain text (StudyAnalyzer item 4a)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** one canonical text per paper (a title plus ordered sections), with
every span in code points, built from a `JATSArticle`, from Markdown or from
plain text, identically in Python and Rust.

**Architecture:** each converter produces *drafts* (heading, level, block
texts); one pure function, `assemble`, flattens them into one string and
records every title, heading and block as a span into it. The JSON shape of
`CanonicalText` is the shared contract: `tests/fixtures/canonical_text.json`
holds inputs and expected outputs, and both languages run it.

**Tech stack:** Python stdlib (`re`, `dataclasses`, `enum`); Rust `regex`,
`serde` (both already dependencies). No new dependency.

**Spec:** `~/src/studyanalyzer/docs/superpowers/specs/2026-10-21-studyanalyzer-design.md`
§5 (canonical text, tables), §7 row 4. ROADMAP/HANDOVER of StudyAnalyzer for
the item order.

**Maintainer's calls (2026-10-28).** Item 4 is three PRs: **4a** (this:
model, JATS, Markdown, plain text), 4b (PDF), 4c (HTML extraction). The JATS
canonical text holds **everything** `to_html()` shows apart from the
author/journal/identifier lines: abstract, body (with back matter), funding,
figure captions, tables as plain rows, and references.

## Global constraints

- Offsets are **Unicode code points**, half-open `[start, end)`. Python `str`
  indices; Rust counts `char`s (never byte offsets in the public types).
- Rust and Python on par in the same PR; the fixture is the arbiter.
- Never truncate (house rule 6): whitespace normalisation is allowed, dropping
  text is not. An empty block is dropped only because it is empty.
- No magic numbers or strings: separators and versions are named constants.
- Every public function, type and method has a doc comment and a unit test.
- Worktree `~/src/bmlib-canonical`, branch `feat/canonical-text`; run Python as
  `env -u VIRTUAL_ENV uv run …`. Gates: `CLAUDE.md` § Gates (StudyAnalyzer's),
  plus bmlib's `uvx ruff@0.15.20`, `uv run mypy`.

## Decisions this plan makes (builder's calls, §11)

- **Layout.** `text` = title, then each section's heading (if any) and blocks,
  every piece separated by `BLOCK_SEPARATOR = "\n\n"`; no leading or trailing
  separator. Separators belong to no span.
- **Whitespace.** A paragraph's runs of whitespace become one space, trimmed.
  "Whitespace" is the Unicode `White_Space` property in both languages: Rust's
  `char::is_whitespace`, Python's `str.isspace()` minus U+001C–U+001F (which
  Python counts and Rust does not). A table row is its cells, each normalised
  the same way, joined by `CELL_SEPARATOR = "\t"`; a tab is whitespace, so
  `find_passage` matches a quote that spaces the cells.
- **Structure.** A flat list of sections, each with a `level` (1 = top) and an
  optional heading. A section with no heading and no blocks is dropped; a
  heading with no blocks is kept.
- **Version.** `converter_version` is `"<format>-<n>"`: `PLAIN_TEXT_VERSION`,
  `MARKDOWN_VERSION`, `JATS_CANONICAL_VERSION`, each `1`. A change to
  `assemble` bumps all three. `JATS_CANONICAL_VERSION` is pinned beside
  `RENDERER_VERSION` in `tests/test_renderer_version.py`, since a parser
  change moves both. Rust has no pin; it must match the fixture's versions.

## Review focus

1. **Characters outside the BMP and combining marks** (`𝛽`, `é` as e + U+0301):
   spans agree in both languages (a UTF-16 or byte offset would not).
   Fixture case `plain/astral-and-combining`.
2. **A quote from a table row or a link-stripped paragraph is found** by
   `find_passage` at the block's span. Unit test in both languages (Task 3).
3. **Empty input** (`""`, whitespace only, a `JATSArticle` with no abstract or
   body): a valid `CanonicalText` with `text == ""`, no title span, no
   sections. Fixture cases `plain/empty`, `markdown/blank`, `jats/front-only`.
4. **`\r\n` and lone `\r` line endings** give the same result as `\n`
   (`plain/crlf`, `markdown/crlf`).
5. **Link markup**: `[2](#bib2)` in a JATS paragraph or title reads `2`;
   brackets not followed by `(…)` are kept; Markdown `snake_case_name` and
   `IL_6` keep their underscores (`jats/inline-links`, `markdown/intraword`).

---

### Task 1: the model, `assemble`, plain text

**Files:**
- Create: `bmlib/fulltext/canonical/__init__.py`, `bmlib/fulltext/canonical/model.py`, `bmlib/fulltext/canonical/plain.py`
- Create: `rust/bmlib/src/fulltext/canonical/mod.rs`, `model.rs`, `plain.rs`
- Modify: `bmlib/fulltext/__init__.py`, `rust/bmlib/src/fulltext/mod.rs` (exports)
- Create: `tests/fixtures/canonical_text.json`, `tests/test_canonical_text_contract.py`, `rust/bmlib/tests/canonical_text_contract.rs`
- Test: `tests/test_canonical_model.py`; Rust unit tests in `model.rs`, `plain.rs`

**Interfaces (Produces):**

```python
class BlockKind(Enum): PARAGRAPH = "paragraph"; TABLE_ROW = "table_row"
class SourceFormat(Enum): JATS = "jats"; MARKDOWN = "markdown"; PLAIN_TEXT = "plain_text"
@dataclass(frozen=True) class TextSpan: start: int; end: int
@dataclass(frozen=True) class CanonicalBlock: kind: BlockKind; span: TextSpan
@dataclass(frozen=True) class CanonicalSection:
    level: int; heading_span: TextSpan | None; blocks: tuple[CanonicalBlock, ...]
@dataclass(frozen=True) class CanonicalText:
    text: str; title_span: TextSpan | None; sections: tuple[CanonicalSection, ...]
    source_format: SourceFormat; converter_version: str
    def span_text(self, span: TextSpan) -> str        # ValueError if outside text or start > end
    def validate(self) -> None                        # ValueError naming the first bad span
    def to_dict(self) -> dict[str, Any]; @classmethod from_dict(cls, d) -> CanonicalText  # validates
@dataclass(frozen=True) class BlockDraft: kind: BlockKind; text: str
    @classmethod paragraph(cls, text: str) -> BlockDraft
    @classmethod row(cls, cells: Sequence[str]) -> BlockDraft
@dataclass class SectionDraft: heading: str; level: int; blocks: list[BlockDraft]
def assemble(title: str, sections: Sequence[SectionDraft], source_format: SourceFormat,
             converter_version: str) -> CanonicalText
def canonical_from_plain_text(text: str, title: str = "") -> CanonicalText
BLOCK_SEPARATOR, CELL_SEPARATOR, PLAIN_TEXT_VERSION
```

Rust: the same names in `bmlib::fulltext::canonical`, `TextSpan { start: usize, end: usize }`,
`level: usize`, `Option<TextSpan>`, `Vec<…>`, serde `snake_case` enums so the
JSON is the Python `to_dict` shape. `span_text(&self, TextSpan) -> Option<&str>`,
`validate(&self) -> Result<(), CanonicalError>`, `CanonicalError` an enum with
`Display` + `std::error::Error`. `assemble(title: &str, sections: &[SectionDraft],
source_format: SourceFormat, converter_version: &str) -> CanonicalText`,
`canonical_from_plain_text(text: &str, title: &str) -> CanonicalText`.

`validate` checks: every span within `text` in code points, `start <= end`,
spans in increasing document order without overlap, `level >= 1`.

**Plain text:** normalise line endings (`\r\n`, then `\r`, to `\n`); blocks are
separated by lines that are whitespace only; one section, level 1, no heading,
holding the paragraphs. `title` becomes the title span when non-blank.

- [ ] **Step 1: Write the fixture** with cases `plain/one-paragraph`,
  `plain/blank-line-split` (`"a\nb\n\n  \nc"` → blocks `"a b"`, `"c"`),
  `plain/titled`, `plain/empty`, `plain/whitespace-only`, `plain/crlf`,
  `plain/astral-and-combining` (`"𝛽 = 0.5\n\ncafé"`: block 2 starts at
  code point 9), `plain/odd-whitespace` (U+00A0 collapses; U+001F is kept as a
  character). Each case: `{"name", "format", "input", "title"?, "expected": <to_dict>}`.
- [ ] **Step 2: Write both contract runners** (each case: convert, compare
  `to_dict()` / `serde_json::to_value` with `expected`; also `from_dict` of
  `expected` round-trips) and the unit tests: `assemble` drops an empty
  untitled section and keeps a heading-only one; `BlockDraft.row(["a", " b  c"])`
  is `"a\tb c"`; `span_text` and `validate` refuse a span past the end, a
  reversed span and overlapping blocks; `from_dict` refuses an unknown kind.
- [ ] **Step 3: Run them and see them fail** (`env -u VIRTUAL_ENV uv run pytest
  tests/test_canonical_text_contract.py tests/test_canonical_model.py -v`;
  `cargo test --test canonical_text_contract canonical`).
- [ ] **Step 4: Implement** the model, `assemble` and the plain-text converter
  in both languages. Rust `assemble` counts code points while pushing
  (`chars().count()` per piece), never byte offsets.
- [ ] **Step 5: Run both suites and see them pass**, then the Rust and Python gates.
- [ ] **Step 6: Commit** `feat(canonical): the canonical text model and the plain-text converter`.

### Task 2: what the JATS converter needs from the reader

**Files:**
- Modify: `bmlib/fulltext/models.py` (`JATSTableInfo`), `bmlib/fulltext/jats_parser.py` (`_TableBuilder.build`)
- Modify: `rust/bmlib/src/fulltext/models.rs`, `jats_reader.rs` (table build), `service.rs` (move reference helpers)
- Modify: `tests/test_renderer_version.py` (re-pin, **no bump**: `to_html()` does not move)
- Test: `tests/test_jats_parser.py` (or the module already testing tables), Rust unit tests in `models.rs`

**Interfaces (Produces):**
- `JATSTableInfo.header_rows: list[list[str]]` and `body_rows: list[list[str]]`
  (Rust `Vec<Vec<String>>`, `#[serde(default)]`), declared last, default
  empty, filled from the builder's rows as deposited (unpadded).
- Rust `JATSReferenceInfo::formatted_citation(&self) -> String`, the port of
  Python's property, in `models.rs`. Move `reference_volume_info`,
  `defers_to_the_deposit` and the author-text helper out of `service.rs` into
  `models.rs` so `format_ref_html` and `formatted_citation` share them.

- [ ] **Step 1: Failing tests**: a two-row table (one `<thead>` row, one body
  row) parses to `header_rows == [["Group", "n"]]`, `body_rows == [["A", "12"]]`
  in both languages; a graphic-only table has both empty. Rust
  `formatted_citation` equals Python's for three references: a full
  `<element-citation>`, a `<mixed-citation>` with a deposited `citation` and
  one printable part (defers), and a DOI-only one.
- [ ] **Step 2: Run, see them fail. Step 3: Implement. Step 4: Run, see them pass.**
- [ ] **Step 5:** `tests/test_renderer_version.py` fails; re-pin `PINNED`
  without a bump. Run `scripts/rerun_rust_oracle.py`; where the only
  difference is the two new table fields, rerun with `--write`, and add the
  fields to any Rust oracle case copy (SESSION-RULES: the oracle).
- [ ] **Step 6: Commit** `feat(jats): a table's rows as data, and a plain citation in Rust`.

### Task 3: the JATS converter

**Files:**
- Create: `bmlib/fulltext/canonical/jats.py`, `rust/bmlib/src/fulltext/canonical/jats.rs`
- Modify: `tests/fixtures/canonical_text.json`, `tests/test_renderer_version.py` (second pin)
- Test: `tests/test_canonical_jats.py`, Rust unit tests in `jats.rs`

**Interfaces:** Consumes Task 1's `assemble`/drafts and Task 2's fields.
Produces `canonical_from_jats(article: JATSArticle) -> CanonicalText` (Rust
`&JATSArticle`), `JATS_CANONICAL_VERSION = 1`.

**Order and shape** (headings `Abstract`, `Funding`, `Figures`, `Tables`,
`References` are the labels `to_html()` already prints for modelled fields):

1. Title: `article.title`, links stripped.
2. `Abstract` (level 1, no blocks) when there are abstract sections; each
   abstract section is a level-2 section headed by its title (possibly
   untitled) holding its `content`.
3. Body sections in order, recursively: level = depth from 1; heading = the
   title; blocks = paragraphs. Links stripped in both.
4. `Funding` (level 1): statements, then each award formatted as `_format_award`
   formats it (reuse it; Rust's `format_award` too, made `pub(crate)`).
5. `Figures` (level 1): each figure a level-2 section headed by its label,
   blocks: caption, then footnotes.
6. `Tables` (level 1): each table a level-2 section headed by its label,
   blocks: caption, header rows, body rows (`BlockDraft.row`), footnotes.
7. `References` (level 1): one paragraph per reference, `formatted_citation`.

Link stripping replaces `[text](target)` (the `_LINK_RE` pattern
`\[([^\]]+)\]\(([^)]+)\)`) by `text`; reuse the existing pattern, do not restate it.

- [ ] **Step 1: Fixture cases** (input is the XML, so each language runs its
  own reader): `jats/full` (title, structured abstract, nested body sections,
  funding statement and award, a figure with a footnote, a table with header
  and body rows, two references), `jats/inline-links`, `jats/front-only`
  (no abstract, no body: empty text apart from the title), `jats/untitled-body`
  (prose straight in `<body>`: one level-1 section without a heading).
- [ ] **Step 2: Unit tests**: (a) `find_passage("A 12", text)` on `jats/full`
  returns the span of the body row block (exact or above the default
  threshold); (b) a quote of a link-stripped sentence is found at its block's
  span; (c) a graphic-only table yields a heading-only section.
- [ ] **Step 3: Run, see them fail. Step 4: Implement both. Step 5: Run, see them pass.**
- [ ] **Step 6:** add `CANONICAL_PINNED = (JATS_CANONICAL_VERSION, <digest>)`
  in `tests/test_renderer_version.py`, using that file's digest over
  `jats_parser.py`, `models.py` and `canonical/model.py`, `canonical/jats.py`;
  its failure message says to decide on both versions.
- [ ] **Step 7: Gates, then commit** `feat(canonical): JATS to canonical text`.

### Task 4: the Markdown converter

**Files:**
- Create: `bmlib/fulltext/canonical/markdown.py`, `rust/bmlib/src/fulltext/canonical/markdown.rs`
- Modify: `tests/fixtures/canonical_text.json`
- Test: Rust unit tests in `markdown.rs`; `tests/test_canonical_markdown.py`

**Interfaces:** Produces `canonical_from_markdown(text: str) -> CanonicalText`,
`MARKDOWN_VERSION = 1`.

**The subset** (hand-written in both, line-based, no library, so the two
cannot drift through different parsers):
- ATX headings `#`–`######` (closing `#`s dropped) and setext (`===` → 1,
  `---` → 2 under a paragraph line) start a section at their level. A level-1
  heading before any other content is the **title**, not a section.
- Blank lines separate paragraphs; list items (`-`, `*`, `+`, `1.`, `1)`) are
  one block each, marker dropped; `>` is stripped; a fenced code block (```` ``` ````
  or `~~~`) is one paragraph, its fences dropped; a thematic break
  (`---`, `***`, `___` alone, not under a paragraph line) is dropped.
- A pipe table becomes `TABLE_ROW` blocks; the delimiter row (`|---|:--:|`) is dropped.
- Inline: `**x**`, `__x__`, `*x*`, `_x_` → `x` (`_` only at word boundaries);
  `` `x` `` → `x`; `[t](u)` → `t`; `![a](u)` → `a`; `<https://…>` → the URL;
  a backslash escape `\*` → `*`. Anything else, HTML tags included, is text.
- Content before the first heading is an untitled level-1 section.

- [ ] **Step 1: Fixture cases**: `markdown/title-and-sections`,
  `markdown/setext`, `markdown/lists-and-quote`, `markdown/fenced-code`,
  `markdown/pipe-table`, `markdown/inline`, `markdown/intraword`
  (`snake_case_name`, `IL_6`, `2*3*4` unchanged), `markdown/blank`, `markdown/crlf`.
- [ ] **Step 2: Run, see them fail. Step 3: Implement both. Step 4: Run, see them pass.**
- [ ] **Step 5: Gates, then commit** `feat(canonical): Markdown to canonical text`.

### Task 5: records

- [ ] `CHANGELOG.md` *Added* entry; `docs/manual/fulltext.md` gains a
  *Canonical text* section marked `*(unreleased)*`; bmlib `ROADMAP.md` row;
  bmlib `HANDOVER.md` "this session". Run every gate in both languages and
  `scripts/rerun_rust_oracle.py`; commit `docs: canonical text (item 4a)`.

**Out of scope:** PDF (4b), HTML (4c); persisting canonical text (StudyAnalyzer
phase 2, through bmlib's `db` layer); re-locating highlights after a version
change (phase 2's call).
