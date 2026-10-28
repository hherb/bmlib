# Canonical text from a segmented PDF (StudyAnalyzer item 4b)

StudyAnalyzer's design §5 and §5.2: a PDF becomes the canonical text through
bmlib's own extraction and section segmenter, its text taken as the extraction
gives it. Item 4a (PR #547) built the layout (`assemble`) and three converters;
this adds the fourth.

## Shape

- `canonical_from_segmented(document: SegmentedDocument) -> CanonicalText`, in
  `bmlib.fulltext.canonical.pdf` / `bmlib::fulltext::canonical::pdf`. Pure: it
  reads the segmenter's output, so it needs neither PyMuPDF nor PDFium, and is
  compiled without the `pdf` feature.
- **Title**: `document.title`. A caller who knows the paper's title passes it to
  `segment_document()` as `metadata["title"]`, where the segmenter already
  checks it against page 1.
- **Sections**: one per segmenter section, in order, at `TOP_LEVEL`; a
  `subsections` entry (the segmenter never fills them, a caller building a
  document by hand may) follows its parent one level deeper, so nothing is
  dropped.
- **Headings**: the section's `title`, except for the two containers
  (`FRONT_MATTER`, `UNKNOWN`), whose "Front Matter" and "Full Text" bmlib makes
  up rather than reads from the page. They become untitled sections: a quote
  can only be located in the paper's own text.
- **Paragraphs**: `content` split at blank lines, as `_join_blocks` marks a
  paragraph gap, lines joined by a space. This is the plain-text converter's
  rule, factored out as `split_paragraphs(text)` (no behaviour change).
- **Version**: `SourceFormat.PDF` (`"pdf"`), `PDF_CANONICAL_VERSION = 1`, so
  `"pdf-1"`. Python pins it against a digest of the converter, the layout, the
  segmenter and the PyMuPDF block extraction feeding it, since a segmenter
  change moves the text too; in Rust it is held by the shared contract, as the
  JATS converter is.

## Parity

Python extracts lines with PyMuPDF, Rust with PDFium, so the same PDF can give
the two languages different `TextBlock`s; the segmenter is already held to
Python by `rust/bmlib/tests/data/segmenter_cases.json`. The shared contract
starts at the `SegmentedDocument`: `pdf` cases in
`tests/fixtures/canonical_text.json` take its `to_dict()` form as `input`.

## Out of scope

- Closing up a word hyphenated across a line break: `dose-` / `response` must
  keep its hyphen, and nothing in a PDF tells the two apart, so the lines are
  joined by a space after the hyphen (`dose- response`). `render_html` does
  not close them up either.
- PDF tables (§5.2: later, measured work). HTML (4c).

## Gates

Python: `uv run pytest tests/ -v`, ruff 0.15.20 check and format, mypy. Rust:
`cargo test`, clippy `-D warnings`, `cargo fmt --check`, `cargo doc` with
`-D warnings`, and `--no-default-features`.
