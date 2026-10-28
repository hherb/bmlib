# Canonical text from HTML, and a full-text check (StudyAnalyzer item 4c)

StudyAnalyzer's design §5: an HTML page becomes the canonical text by
main-content extraction, the approach left to the builder (§11). Item 4a
(PR #547) built the layout and three converters, 4b (PR #556) the PDF one; this
adds the fifth, and a check every format can use.

## Who sends HTML

bmlib's retrieval never fetches a publisher page (an `html` source comes back
as `web_url` only), and MedicalFactChecker stores Markdown beside every HTML it
keeps, so the HTML that reaches this converter is a page a reader saved or
pasted: some publishers serve the full text as HTML and charge for the PDF.
Such a page may equally be an abstract-only landing page, which converts just
as cleanly. The maintainer (2026-10-28): convert faithfully, and judge
separately whether the result is a whole paper; try it and report back.

## Shape

- `canonical_from_html(html: str) -> CanonicalText`, in
  `bmlib.fulltext.canonical.html` / `bmlib::fulltext::canonical::html`,
  `SourceFormat.HTML`, `HTML_CANONICAL_VERSION = 1` (`"html-1"`). Input is
  text; decoding bytes (a `<meta charset>`) is the caller's.
- **Parser**: html5ever in both languages, so a malformed page builds the
  same tree in each: markupever (Python, MIT, extra `html`) and scraper 0.27
  (Rust, ISC, feature `html`). Both optional, as PDF is. html5lib was the
  first choice and was refused by the pre-PR review: it predates
  `<template>`, `<search>` and the BOM rule. Every walk is iterative.
- **Pruned** (subtree dropped, text after it kept): `script`, `style`,
  `noscript`, `template`, `nav`, `aside`, `form`, `button`, `select`,
  `textarea`, `svg`, `iframe`, `object`, `embed`, `canvas`, `audio`,
  `video`, and any element with a `hidden` attribute.
- **Root**: the `<article>` holding the most text (the first on a tie), else
  the first `<main>` or `role="main"`, else `<body>`.
- **Title**: the first `<h1>` with text the walk reaches (not one in a table
  row), which is then not a section;
  else `<meta name="citation_title">`; else `<title>`.
- **Sections**: `<h1>`–`<h6>` start a section at their level; content before
  the first is an untitled top-level section (as Markdown).
- **Blocks**: a block element (`p`, `div`, `li`, `blockquote`, `pre`,
  `figcaption`, `caption`, `section`, …) ends the running paragraph; inline
  elements and text run on; `<br>` is a space. A `<tr>` is a table row of its
  `td`/`th` cells. A heading's or cell's own text puts a space between the
  blocks inside it.

## The full-text check

`assess_full_text(text: CanonicalText) -> FullTextAssessment`, in
`bmlib.fulltext.canonical.assess` / `…::canonical::assess`, any format:

- Each heading is classified by the segmenter's pattern table
  (`match_section_type`, now public in Python as it is in Rust).
- A section under an `Abstract` heading (deeper level, until the next at its
  level or above) is the abstract's, so a structured abstract's `Methods` /
  `Results` do not count; nor do they under `References`.
- `is_full_text`: a `methods` heading and a `results` or `discussion` one
  outside the abstract, and at least `MIN_BODY_WORDS = 1000` words in the
  blocks outside the abstract and references.
- The result carries the section types found and the word count, so the app
  can say why. What the app does with `False` is phase 2's call.
- **Unmeasured**: the threshold and the heading rule are a starting point
  without a corpus; a case report or letter without IMRaD headings fails it.

## Parity

`html` cases in `tests/fixtures/canonical_text.json` start at the HTML source;
a new `tests/fixtures/full_text_assessment.json` starts at Markdown (its
converter already on contract), so the check is tested without the `html`
feature. Rust CI tests the `html` feature in the `pdf` matrix row.

## Out of scope

Readability-style scoring; charset sniffing; images and `alt` text; telling a
site's own `<h1>` from the paper's when a page has neither `<article>` nor
`<main>`.

## Gates

Python: `uv run pytest tests/ -v`, ruff 0.15.20 check and format, mypy. Rust:
`cargo test` (and `--features html`), clippy `-D warnings` with all features,
`cargo fmt --check`, `cargo doc` with `-D warnings`, `--no-default-features`.
