# PubMed metadata graft — design

_2026-08-06. Row 11 of
[`docs/plans/2026-07-17-bmlibrarian-porting-analysis.md`](../../plans/2026-07-17-bmlibrarian-porting-analysis.md)
— the last Phase 2 port._

Grafts four things from bmlibrarian's `importers/pubmed_bulk_importer.py` onto
`bmlib/publications/fetchers/pubmed.py`: inline-formatting-preserving text
extraction, Markdown abstract formatting, `<GrantList>` grants, and
`<AffiliationInfo>` author affiliations. Grants and affiliations are persisted
in two new child tables.

`is_retracted` is deliberately **not** ported — see "Rejected" below.

## The defect this fixes first

bmlib's `_text()` returns `el.text`, which is the text *before the first child
element*. Any PubMed title containing inline markup is therefore truncated at
the first tag, silently:

```python
"Effects of H<sub>2</sub>O and <i>E. coli</i> on outcomes"  # → "Effects of H"
```

The title is the primary field: it drives dedup display, quality assessment and
citation building. Chemical formulas, species names and superscripts are common
in PubMed titles, so this is not a corner case. The abstract escaped truncation
(it uses `itertext()`) but flattened `CO<sub>2</sub>` to the ambiguous `CO2` and
read only the `Label` attribute, dropping every `NlmCategory` section label.

## Architecture

Six files change. Each piece is separable and independently testable.

### 1. `fetchers/pubmed.py` — two new pure functions

**`_text_with_formatting(el) -> str`** walks mixed content and maps inline tags
to Markdown: `b`/`bold` → `**x**`, `i`/`italic` → `*x*`, `sup` → `^x^`,
`sub` → `~x~`, `u`/`underline` → `__x__`. An unrecognised tag contributes its
text without decoration. Tail text after each child is preserved.

**Upstream defect fixed:** upstream calls `.strip()` at *every* recursion level,
so a space living inside a formatted run is eaten and words weld together:

| input | upstream | this port |
|---|---|---|
| `<b>Randomised </b><b>trial</b>` | `**Randomised****trial**` | `**Randomised** **trial**` |
| `A <b>bold </b><i>italic</i> tail` | `A **bold***italic* tail` | `A **bold** *italic* tail` |

The upstream output is not merely cosmetically wrong —
`**Randomised****trial**` is broken Markdown.

Simply *not* stripping during the recursion is not enough, which the
implementation found: it yields `**Randomised **`, and CommonMark requires an
emphasis delimiter to be adjacent to non-whitespace, so that does not emphasise
either. The rule is therefore two-part — **a run's edge whitespace is
re-emitted outside its markers, and the result is stripped once, by the
outermost call.** Both halves have a test.

**`_format_abstract_markdown(abstract_el) -> str | None`** renders each
`AbstractText`: the label is `Label`, falling back to `NlmCategory` unless it is
`UNASSIGNED` or `UNLABELLED`; it is upper-cased and rendered as
`**LABEL:** text`; sections join with a blank line. An unlabelled abstract is
just its text. Returns `None` when nothing survives, matching the current
contract (`abstract` is `str | None`).

`ArticleTitle` switches to `_text_with_formatting`. `Journal/Title` and the MeSH
and publication-type lists keep plain `.text` — they are leaf elements in
practice, and widening the change is scope the port does not need.

**Behaviour change, unflagged.** Every synced PubMed title and abstract changes
shape. Titles change because they were being truncated; abstracts because they
gain the recovered `NlmCategory` labels, `\n\n` section breaks, and Pandoc-style
`CO~2~` / `m^2^` in place of the ambiguous flattening. One code path, as with
0.7.0's four unflagged behaviour changes. `CHANGELOG.md` records it as
non-comparable: anything storing abstracts should re-sync or accept the mix.

### 2. `models.py` — two child-table dataclasses

Follows the `FullTextSource` precedent: a publication's grants and affiliations
are child rows, not columns, so `Publication` and its `to_dict()` contract are
untouched.

```python
@dataclass
class Grant:
    agency: str | None = None
    grant_id: str | None = None
    country: str | None = None
    publication_id: int = 0     # set by store_publication
    id: int | None = None

@dataclass
class AuthorAffiliation:
    author: str
    affiliation: str
    position: int = 0           # 0-based index in AuthorList
    publication_id: int = 0
    id: int | None = None
```

Both get `to_dict()`/`from_dict()` per convention.

One row per *(author, affiliation)* pair rather than upstream's
`{author, affiliations: [...]}` grouping — that is the relational shape, and it
makes the transparency question ("which papers have an author at Pfizer?") a
single indexed query instead of a JSON scan.

`position` is carried because first-author and senior-author affiliation are
*the* COI signals, and the author name alone cannot recover the ordering:
upstream formats it `"Smith John"` while bmlib's `Publication.authors` uses
`"Smith, John"`, so joining the two on name is fragile. **Upstream defect
fixed:** this port formats the affiliation's author name the same way
`_parse_article_xml` formats the author list, so the two agree.

`FetchedRecord` gains `grants: list[Grant]` and
`author_affiliations: list[AuthorAffiliation]`, **declared last** — the
positional-stability rule that `Publication.pmcid` and
`TransparencyResult.unknown_reason` already follow, pinned by
`test_positional_construction_is_stable_across_versions`.

### 3. `schema.py` — two new tables, both dialects

```sql
CREATE TABLE IF NOT EXISTS publication_grants (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,   -- SERIAL on PostgreSQL
    publication_id  INTEGER NOT NULL REFERENCES publications(id),
    agency          TEXT,
    grant_id        TEXT,
    country         TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX … ON publication_grants (publication_id);

CREATE TABLE IF NOT EXISTS publication_affiliations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    publication_id  INTEGER NOT NULL REFERENCES publications(id),
    author          TEXT NOT NULL,
    affiliation     TEXT NOT NULL,
    position        INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL
);
CREATE INDEX … ON publication_affiliations (publication_id);
```

New *tables* need no `_ADDED_COLUMNS` entry: `CREATE TABLE IF NOT EXISTS` in
`ensure_schema()` creates them on an existing database, which is exactly what
that list exists to work around for columns.

**No UNIQUE constraint on the natural key.** `UNIQUE(publication_id, agency,
grant_id)` looks right and is a trap: both backends treat `NULL` as distinct in
a unique index, and `agency`/`grant_id`/`country` are all nullable, so
`(1, NULL, 'R01')` would insert twice and the constraint would silently protect
nothing. Idempotency is handled in the storage layer instead (next section),
where it can be tested directly.

### 4. `storage.py` — replace-if-nonempty

`store_publication()` gains keyword-only `grants` and `affiliations`. Both
follow one rule:

> If the incoming record carries any, delete this publication's existing rows
> and insert the incoming set. If it carries none, leave what is there alone.

This makes re-syncing a day idempotent (no accumulating duplicates) and
self-correcting (a corrected grant replaces the stale one) without a unique
index that cannot be written correctly over nullable columns. The
`if incoming:` guard is the same "fill, never clobber" rule
`_merge_publication` already applies per field — a bioRxiv record merging into a
PubMed row carries no grants and so erases nothing.

The limit is honest and documented: if a second source ever supplies grants,
successive syncs would alternate between the two sources' sets. Only PubMed
produces them today.

Two readers, pure functions with the connection first:
`get_grants(conn, publication_id)` and
`get_author_affiliations(conn, publication_id)`, the latter ordered by
`position`.

**`_consolidate_rows()` must relocate the new child rows.** It deletes the drop
publication row after moving its `fulltext_sources`; both backends enforce
foreign keys (`connect_sqlite(foreign_keys=True)` by default), so leaving grant
or affiliation rows pointing at the doomed id makes the `DELETE` raise and
aborts the whole store. The drop row's children move to the keep row **only if
the keep row has none**, and are deleted otherwise — table-granularity "fill,
never overwrite", mirroring `_merge_publication`. This is the subtlest part of
the change and gets its own test on both backends.

### 5. `sync.py` — pass-through

`_record_to_publication()` is unchanged. A new `_record_to_grants()` /
`_record_to_affiliations()` pair mirrors `_record_to_fulltext_sources()`, and
the `store_publication()` call site forwards both. Everything stays inside the
existing one-commit-per-day transaction.

### 6. Docs

`docs/manual/publications.md` gains the two models, the two tables, the two
readers, and the changed abstract/title shape. The line stating that `extras` is
not persisted stays true and is not touched. `CHANGELOG.md`, `ROADMAP.md` and
`HANDOVER.md` are updated.

## Testing

TDD throughout — behaviour tests first, upstream's code as the spec.

| Area | File | What is pinned |
|---|---|---|
| Formatting | `test_pubmed_fetcher.py` | A title with `<sub>`/`<i>` survives whole (the truncation regression); each inline tag maps to its Markdown; an unknown tag keeps its text; a space inside a formatted run is not eaten (the upstream defect, both cases in the table above); `NlmCategory` supplies a label when `Label` is absent; `UNASSIGNED`/`UNLABELLED` do not become labels; sections join with a blank line; an unlabelled abstract is bare text; an empty abstract stays `None` |
| Extraction | `test_pubmed_fetcher.py` | Grants parse; a grant with neither agency nor id is skipped; affiliations carry author, position and one row per affiliation; the affiliation author name matches the `authors` list format; a record with none yields empty lists |
| Storage | `test_publications.py` | Round-trip through both readers; re-storing the same record does not duplicate; a record with no grants does not erase stored ones; a record with grants replaces them; split-identity consolidation relocates children rather than raising; consolidation keeps the keep row's children when it has some |
| Both dialects | `test_backends.py` | The same storage behaviours against PostgreSQL |
| Positional stability | existing test | `FetchedRecord`'s new fields are last |

## Rejected

- **`is_retracted`.** `publication_types` already carries `"Retracted
  Publication"` verbatim, and `publications/retractions.py` answers the question
  authoritatively from Retraction Watch. Upstream's version also treats RefType
  `RetractionOf` as retracted, but that marks an article as *being* a retraction
  notice, not as retracted — porting it would import a false positive.
- **Upstream's `_extract_date`.** bmlib's `_parse_pubdate` is strictly better:
  upstream defaults a missing month and day to `01`, inventing a precision the
  record does not have, and swallows every failure in a bare `except:`.
- **A unique index on the grant natural key.** Cannot be written correctly over
  nullable columns; see section 3.
- **Persisting `extras`.** Unrelated to this row and a separate decision.
