# Design: `bmlib/citations/` — citation/reference stack port

**Date:** 2026-08-06
**Status:** Proposed
**Source:** Phase 2 row 4 of
[`docs/plans/2026-07-17-bmlibrarian-porting-analysis.md`](../../plans/2026-07-17-bmlibrarian-porting-analysis.md)
— port `bmlibrarian/writing/citation_parser.py`, `citation_formatter.py`, and
the models + constants subset they need, into a new `bmlib/citations/`
package.

## Purpose

Give bmlib a citation-aware document toolchain: parse `[@id:12345:Smith2023]`
citation markers out of manuscript text, number the cited documents in order
of first appearance, format bibliographic references in Vancouver / APA /
Harvard / Chicago style, replace the markers with inline citations (numbered
or author–date, per style), and append a formatted reference list.

Upstream, this powers bmlibrarian's citation-aware markdown editor. In bmlib
it is a standalone, pure-stdlib package — no database, no LLM, no optional
dependency group.

## Scope

**Ported** (upstream file → bmlib module):

| Upstream | bmlib | Notes |
|---|---|---|
| `writing/models.py` (subset) | `citations/models.py` | `Citation`, `DocumentMetadata`, `FormattedReference`, `CitationStyle` |
| `writing/constants.py` (subset) | folded into the module that owns each | `CITATION_PATTERN` → `parser.py`; `MAX_AUTHORS_BEFORE_ET_AL` → `formatter.py`; `DEFAULT_CITATION_STYLE` → `models.py` |
| `writing/citation_parser.py` | `citations/parser.py` | Class dissolved into pure module functions (stateless upstream) |
| `writing/citation_formatter.py` | `citations/formatter.py` | Class hierarchy kept — style polymorphism is a genuine use for classes |
| `writing/reference_builder.py` | `citations/builder.py` | DB coupling severed: metadata is injected as `Mapping[int, DocumentMetadata]`, never fetched |

**Not ported**, with reasons:

- `writing/document_store.py` — on the analysis doc's do-not-port list
  (bmlibrarian's PG document persistence).
- `WritingDocument`, `DocumentVersion` — editor persistence models; app
  concern.
- Autosave, editor-font, UI-colour, and semantic-search constants — app
  editor configuration.
- `CITATION_ID_PATTERN` — assigned to `self._id_pattern` upstream and never
  read; dead code.
- `CITATION_INCOMPLETE_PATTERN` — an as-you-type editor aid; app concern.
- `CITATION_SEPARATOR` — declared upstream, never used (the group formatter
  hard-codes `","`); dead code.
- `BaseFormatter._format_authors()` — dead upstream; every concrete
  formatter overrides with its own style-specific author logic.
- `ReferenceBuilder.get_citation_preview()` — returns a UI-tooltip-shaped
  dict; app concern.
- `ReferenceBuilder.generate_label(document_id)` /
  `create_citation_marker(document_id)` — DB-backed conveniences; callers
  holding a `DocumentMetadata` use `metadata.generate_label()` and
  `parser.create_citation_marker()` instead.

## Package layout

```
bmlib/citations/
├── __init__.py      # public exports (__all__)
├── models.py        # CitationStyle, Citation, DocumentMetadata, FormattedReference
├── parser.py        # pure functions over the [@id:N:Label] marker format
├── formatter.py     # BaseFormatter ABC + 4 style formatters + CitationFormatter facade
└── builder.py       # pure orchestration: text + metadata → formatted doc + references
```

Pure stdlib (`re`, `dataclasses`, `enum`, `abc`, `typing`). No new optional
dependency group; nothing in `pyproject.toml` changes.

## Module designs

### `models.py`

- `CitationStyle(str, Enum)`: `VANCOUVER`, `APA`, `HARVARD`, `CHICAGO`.
  `DEFAULT_CITATION_STYLE: Final = CitationStyle.VANCOUVER` module constant
  (upstream's redundant `get_default()` classmethod is dropped — one way to
  state the default).
- `Citation`: `document_id: int`, `label: str`, `position: int`,
  `text: str`. **Deviation:** ordinary dataclass field equality, not
  upstream's equality/hash by `document_id` alone. Upstream's identity trick
  served `set[Citation]` operations that nothing in the ported scope
  performs, and it makes two citations of the same document at different
  positions compare equal — surprising for a value object. Nothing ported
  relies on the old semantics.
- `DocumentMetadata`: `document_id`, `title`, `authors: list[str]`,
  `journal`, `year`, `pmid`, `doi`, `volume`, `issue`, `pages`,
  `publication_date` — plus `get_first_author_surname()`,
  `generate_label()` (e.g. `"Smith2023"`; year `None` → `"Smithn.d."`,
  upstream-faithful), `from_dict()` / `to_dict()`.
- `FormattedReference`: `number`, `document_id`, `formatted_text`,
  `metadata: DocumentMetadata | None` — plus `from_dict()` / `to_dict()`.

### `parser.py` — pure functions

`CITATION_PATTERN = re.compile(r"\[@id:(\d+):([^\]]+)\]")` module constant.

- `parse_citations(text) -> list[Citation]` — in order of appearance. The
  upstream per-match `try/except (ValueError, IndexError)` is dropped: with
  this pattern, `int()` on a `\d+` group cannot raise in Python and both
  groups always exist, so the handler was unreachable.
- `unique_document_ids(text) -> list[int]` — order of first appearance.
- `count_citations(text) -> int`, `count_unique_citations(text) -> int`,
  `citation_positions(text) -> dict[int, list[int]]`,
  `citations_in_range(text, start, end) -> list[Citation]`.
- `create_citation_marker(document_id, label) -> str`.
- `replace_citation_with_number(text, document_id, number) -> str`,
  `replace_all_citations_with_numbers(text, id_to_number) -> str` —
  markers whose id is not in the mapping are preserved verbatim.
- `find_adjacent_citations(text) -> list[list[Citation]]` — markers
  separated only by whitespace/commas group together.
- `format_citation_group(citations, id_to_number, combine_sequential=True)
  -> str` — `[1,2]`, `[1-3]`, `[1,2,4]` combining, upstream-faithful.
- `validate_citation_marker(text) -> tuple[bool, str | None]`,
  `extract_label_from_citation(text) -> str | None`,
  `extract_document_id_from_citation(text) -> int | None`.

### `formatter.py`

- `MAX_AUTHORS_BEFORE_ET_AL: Final = 6` module constant.
- `BaseFormatter` ABC: abstract `format_reference(metadata, number=None)`
  and `format_inline_citation(metadata, number=None)`; shared `_format_title`,
  `_format_journal`, and one shared `_surname(author)` helper (upstream
  duplicated `_get_surname` in three subclasses).
- `VancouverFormatter`, `APAFormatter`, `HarvardFormatter`,
  `ChicagoFormatter` — output preserved **exactly** as upstream produces it
  (upstream is the spec), including each style's own author-truncation
  shape at 6 authors and its year/volume/pages punctuation.
- `CitationFormatter` facade: constructor takes a `CitationStyle`, `style`
  property with setter, `format_reference()`, `format_inline_citation()`,
  `format_reference_list(references) -> str` (markdown `## References`
  section), `get_available_styles()`, `get_style_description()`.

### `builder.py` — pure functions

The upstream `ReferenceBuilder` class dissolves into functions; its DB
fetching (`get_db_manager()`, a PG query against bmlibrarian's `document`
table) is severed. The caller supplies `metadata: Mapping[int,
DocumentMetadata]`.

- `build_references(text, metadata, style=DEFAULT_CITATION_STYLE,
  combine_sequential=True) -> tuple[str, list[FormattedReference]]` —
  numbers cited documents by order of first appearance, formats one
  reference per unique document, replaces the markers in the text, and
  returns both. A cited id missing from `metadata` keeps upstream's visible
  `"N. [Document N not found]"` placeholder reference — visible in the
  output, not a silent drop — and its inline marker is replaced by `[N]`
  (Vancouver) or left verbatim (author–date styles, which need a surname
  the metadata would have supplied).
- `format_document(text, metadata, style=..., include_reference_list=True,
  combine_sequential=True) -> str` — `build_references()` plus the appended
  reference list.
- `find_missing_documents(text, metadata) -> list[Citation]` — citations
  whose `document_id` has no metadata entry. **Deviation:** replaces
  upstream's `validate_citations()`, which returned ad-hoc issue dicts with
  exactly one issue type (`missing_document`); a typed list of the affected
  citations says the same thing without the stringly-typed wrapper.

## Upstream defects fixed (each with a named regression test)

1. **`DocumentMetadata.from_dict()` shatters inverted author names.**
   Upstream normalises an authors *string* by replacing `;` with `,` and
   splitting on `,`, so `"Smith, John; Doe, Jane"` becomes four authors
   (`Smith` / `John` / `Doe` / `Jane`). Fix: when the string contains `;`,
   split on `;` only (each item may be `"Surname, Firstname"`); otherwise
   split on `,`.
   Test: `test_semicolon_separated_inverted_names_survive_from_dict`.
2. **`validate_citation()` anchors only at the start.** Upstream uses
   `.match()`, so `"[@id:5:Smith] trailing junk"` validates as a well-formed
   marker. Fix: `fullmatch` in `validate_citation_marker()` (and the two
   `extract_*_from_citation` helpers, which take "a citation marker string").
   Test: `test_a_marker_with_trailing_text_is_not_valid`.
3. **Author–date styles get numeric inline citations.** Upstream
   `_replace_citations()`'s docstring promises the inline citation format for
   non-Vancouver styles, but the code path calls
   `replace_all_citations_with_numbers()`, so an APA document reads `[3]`
   against an unnumbered author–date reference list. Fix: for APA / Harvard /
   Chicago, each marker is replaced with the style's
   `format_inline_citation(metadata)` — e.g. `(Smith et al., 2023)`; a
   marker with no metadata stays verbatim.
   Test: `test_author_date_styles_get_author_date_inline_citations`.

## Error handling

All functions are pure and total over `str` input: no I/O, no logging, no
exceptions raised in normal operation. Malformed marker-like text simply
does not match the pattern. `validate_citation_marker()` reports problems as
a `(False, reason)` value rather than raising, upstream-faithful.

## Testing

TDD throughout — behaviour tests first against the upstream spec, watch the
`ModuleNotFoundError` red, then port. Test files follow the per-module
convention (`tests/test_citations_parser.py`, `tests/test_citations_formatter.py`,
`tests/test_citations_builder.py`; the models are exercised where used and
their serialisation round-trips tested alongside the parser file).

- **Parser:** marker extraction and positions; order of first appearance;
  adjacent grouping across whitespace/comma gaps but not across prose;
  sequential combining `[1,2]` / `[1-3]` / `[1,2,4]`; unmapped markers
  preserved; range and counting helpers; marker validation including the
  trailing-junk regression and label-length bound.
- **Formatter:** golden reference + inline output per style, with 1, 2, 3,
  and 7+ authors; both `"Surname, First"` and `"First M. Surname"` inputs;
  missing journal / year / doi / pages / volume combinations; `n.d.`
  handling in author–date styles.
- **Builder:** end-to-end Vancouver document (numbering by first
  appearance, adjacent-group combining, reference list); end-to-end APA
  document (author–date inline regression); missing-metadata placeholder;
  text with no citations returns unchanged; `include_reference_list=False`.
- **Models:** `from_dict` author handling (list, comma string,
  semicolon-inverted regression), `to_dict`/`from_dict` round-trips,
  `generate_label()`.

## Documentation and bookkeeping

- `CHANGELOG.md` `[Unreleased]` entry (Phase 2 row 4, defects fixed).
- New manual page `docs/manual/citations.md`.
- `CLAUDE.md`: directory-structure block, module description, test-file
  mapping row.
- `ROADMAP.md`: new row under a Citations heading, `(unreleased)` marker.
- `HANDOVER.md`: refreshed at session end.
- Public names exported from `bmlib/citations/__init__.py` with `__all__`.

## Out of scope / future

- Wiring citations into `publications/` (e.g. building `DocumentMetadata`
  from a `Publication`) — an obvious follow-on, but a separate design.
- String document ids in the marker format — upstream and bmlibrarian use
  integer ids; changing the marker grammar is a spec change, not a port.
- CSL/BibTeX export, further citation styles.
