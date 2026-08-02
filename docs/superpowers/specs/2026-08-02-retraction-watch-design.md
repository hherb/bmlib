# Design — Retraction Watch support in `bmlib.publications`

_2026-08-02. Phase 2 row 10 of the bmlibrarian → bmlib porting effort
(`docs/plans/2026-07-17-bmlibrarian-porting-analysis.md`)._

## The problem it solves

A biomedical literature tool must not present a retracted paper as evidence.
bmlib today has no way to answer "is this DOI retracted?" — nothing in the
library carries retraction status, and every consumer that wants it builds
its own table over the same public dataset.

The Retraction Watch database is that dataset. Crossref distributes it as a
CSV under CC0 at `https://api.labs.crossref.org/data/retractionwatch?<mailto>`,
roughly 60,000 rows covering retractions, corrections, expressions of concern
and reinstatements.

## Source and scope

Upstream: `~/src/bmlibrarian/src/bmlibrarian/importers/retraction_watch_importer.py`
(357 lines).

| Upstream | Disposition |
|---|---|
| Column-variation constants + `_find_column()` | Ported, **corrected** (defect 1 below). |
| Multi-encoding read | Ported, **restructured** (defects 2 and 3). |
| Multi-format date parsing | Ported, extended for a trailing time component. |
| `RetractionWatchImporter.import_csv()` DB tail | **Dropped.** It writes `transparency.document_metadata` and `public.doi_metadata` in a fixed PostgreSQL schema bmlib does not have, through a `get_db_manager()` singleton — the exact anti-pattern a port exists to sever. |
| `.lookup()` / `.get_status()` | **Dropped**, same reason. Replaced by pure functions over an injected connection. |

Target: **`bmlib/publications/retractions.py`**, with the dataclass and enum
in `publications/models.py` and the DDL in `publications/schema.py`.

### Why this is not a fetcher

The analysis doc files this row under `publications/fetchers/`. That is the
wrong home, and the reason is worth recording so the next contributor does
not "complete the set".

Every registered fetcher shares one calling convention —
`fetcher(client, target_date, *, on_record, on_progress=None, **config)` —
and yields `FetchedRecord`s that `storage.store_publication()` upserts into
`publications`. That protocol assumes a **date-keyed feed of publications**.
Retraction Watch is neither: it is one bulk file with no date to iterate, and
its rows are *annotations about papers that are usually not in your
`publications` table at all*. The dataset covers all of scholarly publishing;
a given consumer holds a small slice of it.

Registering it as a source would therefore require either a fake
`target_date` loop or a second, divergent protocol behind the same registry —
and would tell every reader that retraction notices are publications. They
are not.

## Public surface

```python
# bmlib.publications.models
class RetractionNature(str, Enum):
    RETRACTION            = "retraction"
    CORRECTION            = "correction"
    EXPRESSION_OF_CONCERN = "expression_of_concern"
    REINSTATEMENT         = "reinstatement"
    OTHER                 = "other"

@dataclass
class RetractionNotice:
    record_id: str                      # Retraction Watch's own key — required
    nature: RetractionNature
    doi: str | None = None              # the RETRACTED paper
    pmid: str | None = None
    notice_doi: str | None = None       # the notice itself
    notice_pmid: str | None = None
    title: str | None = None
    journal: str | None = None
    retraction_date: str | None = None       # ISO yyyy-mm-dd
    original_paper_date: str | None = None   # ISO yyyy-mm-dd
    reasons: list[str] = field(default_factory=list)
    raw_nature: str | None = None
    def to_dict(self) -> dict[str, Any]: ...
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RetractionNotice: ...

# bmlib.publications.retractions
def parse_retraction_watch_csv(
    source: str | Path | IO[bytes],
    *,
    on_skip: Callable[[int, str], None] | None = None,
) -> Iterator[RetractionNotice]: ...

def store_retraction_notices(conn: Any, notices: Iterable[RetractionNotice]) -> int: ...

def lookup_retractions(
    conn: Any, *, doi: str | None = None, pmid: str | None = None
) -> list[RetractionNotice]: ...

def is_retracted(notices: Sequence[RetractionNotice]) -> bool: ...
```

All six names above are re-exported from `bmlib.publications.__init__` and
listed in its `__all__`.

### Two identifier pairs, named as such

A Retraction Watch row describes **two** papers, and the Crossref export
carries a column pair for each:

| The retracted paper | The retraction notice |
|---|---|
| `OriginalPaperDOI` | `RetractionDOI` |
| `OriginalPaperPubMedID` | `RetractionPubMedID` |

`RetractionNotice.doi`/`.pmid` always mean **the retracted paper** — that is
what a caller looks a paper up by. `.notice_doi`/`.notice_pmid` carry the
notice, so a caller can cite *why*. Upstream collapsed both pairs into one
`doi`/`pmid` resolved by scanning a candidate tuple, a shape that structurally
cannot say which paper an identifier refers to.

### `OTHER` rather than a raise

`RetractionNature` maps an unrecognised value to `OTHER` and preserves the
file's own string in `raw_nature`. The vocabulary belongs to Retraction Watch
and can grow; one new notice type must not abort a 60,000-row import, and
nothing is lost.

This deliberately differs from `_Analysis.note_data_level()`, which raises
`KeyError` on an unknown level (see HANDOVER.md). That guards an **internal**
curated map with two in-tree producers, where an unrecognised value means a
bmlib bug. This reads an **external** file whose vocabulary bmlib does not
control.

## Storage

`retraction_notices` is added to `SCHEMA_SQL` and `SCHEMA_SQL_POSTGRESQL`, so
existing `ensure_schema()` callers get it with no new call. `CREATE TABLE IF
NOT EXISTS` makes that idempotent; the table is new, so `_ADDED_COLUMNS`
needs no entry.

```sql
CREATE TABLE IF NOT EXISTS retraction_notices (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,  -- SERIAL on PostgreSQL
    record_id           TEXT NOT NULL UNIQUE,
    doi                 TEXT,
    pmid                TEXT,
    notice_doi          TEXT,
    notice_pmid         TEXT,
    nature              TEXT NOT NULL,
    raw_nature          TEXT,
    title               TEXT,
    journal             TEXT,
    retraction_date     TEXT,
    original_paper_date TEXT,
    reasons             TEXT NOT NULL DEFAULT '[]',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_retraction_notices_doi  ON retraction_notices (doi);
CREATE INDEX IF NOT EXISTS idx_retraction_notices_pmid ON retraction_notices (pmid);
```

`reasons` is a JSON array in a `TEXT` column, matching how `publications`
stores `authors`, `keywords` and `publication_types`.

The `UNIQUE` constraint on `record_id` is what makes re-importing the monthly
CSV idempotent rather than doubling the table. `Record ID` is Retraction
Watch's own primary key and the first column of the export.

It is a **full table constraint, and `record_id` is `NOT NULL`** — not the
partial unique index `publications.doi`/`.pmid` use. That difference is
deliberate and decides the upsert mechanism:

- `store_publication()` cannot use `ON CONFLICT` precisely *because* its
  indexes are partial. PostgreSQL will not infer a partial index unless the
  statement repeats its predicate, so `store_publication()` does
  lookup-then-insert-or-update by hand. The only two `ON CONFLICT` sites in
  the package — `download_days (source, date)` and
  `fulltext_sources (publication_id, url)` — both target full `UNIQUE(...)`
  table constraints.
- `publications.doi` *must* be nullable: a publication legitimately has no
  DOI. A retraction notice always has a Record ID, so there is no reason to
  accept a null and every reason not to — a nullable key means duplicate
  unconstrained rows on every re-import, since both backends treat `NULL`s as
  distinct in a `UNIQUE` index.

So `store_retraction_notices()` upserts with `ON CONFLICT (record_id) DO
UPDATE`, the same mechanism and the same shape of constraint as
`download_days`. It runs inside one `transaction(conn)`, so a standalone call
commits once and a call inside a caller's block joins it — the
composable-transaction convention the rest of `publications/` follows.

Making `record_id` required on the dataclass as well as in the schema means a
hand-built notice missing one fails loudly at construction rather than
silently becoming a row that duplicates itself on every subsequent import.

### One normaliser, not two

`lookup_retractions()` and `store_retraction_notices()` both normalise
identifiers through `storage._normalize_doi` / `storage._normalize_pmid` —
the *same* functions `store_publication()` uses, imported within the package
rather than reimplemented. A DOI is case-insensitive and arrives with
assorted `https://doi.org/` prefixes; a second normaliser that drifts from
the first is a lookup that silently returns nothing for a paper that is in
fact retracted, which is the worst failure this feature can have.

## The retraction rule

`is_retracted()` is **pure** — it takes the notices, not a connection. The
rule is therefore testable without a database, and re-derivable without
re-importing 60,000 rows if it ever changes.

> Scan notices newest first. The first `RETRACTION` or `REINSTATEMENT`
> decides: `RETRACTION` → `True`, `REINSTATEMENT` → `False`. Any other
> nature is not evidence either way. No such notice → `False`.

The subtlety is that a flat "latest notice wins" is wrong. A paper retracted
in 2020 and *corrected* in 2021 would read as not-retracted, because the
correction is the latest notice. A correction does not undo a retraction;
only a reinstatement does.

`lookup_retractions()` orders by `retraction_date DESC, id DESC`. The
secondary key matters because `retraction_date` is nullable and ties are
common within a month, so date alone leaves the "newest" notice
non-deterministic — and this rule reads the first row.

## Defects fixed in the port

Each is a real behaviour bug upstream, and each gets a regression test named
after it.

1. **The PMID match path is dead against the real export.** Upstream's
   `PMID_COLUMNS = ("PubMedID", "PMID", "pmid", "PubMed ID", "OriginalPaperPMID")`
   contains **none** of the names the Crossref export actually uses —
   `OriginalPaperPubMedID` and `RetractionPubMedID`. `_find_column()`
   therefore returns `None` for every row, and the entire PMID branch of the
   importer never fires. `DOI_COLUMNS` works only by luck: it lists a bare
   `"DOI"` *ahead* of `OriginalPaperDOI`, and is saved solely by there being
   no `DOI` column in the export to grab. The port uses the real names,
   ordered **most-specific-first**, so the explicit column can never lose to
   an ambiguous one.

2. **A failed encoding attempt duplicates every row already read.** Upstream
   accumulates into a `rows` list that is created *outside* the encoding
   retry loop and never cleared between attempts:

   ```python
   rows = []
   for encoding in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
       try:
           ...
           for i, row in enumerate(reader):
               rows.append(row)
           break
       except (UnicodeDecodeError, UnicodeError):
           continue
   ```

   `utf-8` failing at row 40,000 leaves those 40,000 rows in the list, and
   `utf-8-sig` then appends all 60,000 again — 40,000 of them twice, with no
   error anywhere. The port **probes** a leading chunk to choose the
   encoding, then **streams** with it. Streaming also stops a 100 MB file
   being held in memory as ~60,000 dicts, which is the other reason to
   restructure.

3. **A byte-order mark hides the first column.** Upstream tries `utf-8`
   *before* `utf-8-sig`. On a BOM'd file `utf-8` does not fail — it succeeds
   and glues `﻿` to the first field name, so `DictReader` yields
   `"﻿Record ID"` and `_find_column()` can never match `Record ID`. The
   port probes `utf-8-sig` first. Ordering `latin-1` **last** additionally
   makes the chain total: every byte is valid Latin-1, so a file that
   defeats the earlier candidates still streams rather than raising
   mid-parse, which a streaming reader cannot retry.

4. **Every row is stored as `is_retracted = TRUE`.** Upstream hardcodes it in
   the INSERT, so a Correction, an Expression of Concern, and a Reinstatement
   — which is the *opposite* of a retraction — all mark the paper retracted.
   The port types the nature and applies the rule above.

## Parsing details

- **Reasons.** Semicolon-separated within the cell, per Crossref's
  documentation, each item conventionally `+`-prefixed
  (`+Concerns/Issues About Data;+Investigation by Journal/Publisher;`). Split
  on `;`, strip whitespace, strip **one** leading `+`, drop empties.
- **Dates.** ISO first, then `%m/%d/%Y`, `%d/%m/%Y`, `%Y/%m/%d`, each also
  accepted with a trailing time component (Retraction Watch emits `0:00`).
  Output is an ISO `yyyy-mm-dd` string, matching
  `Publication.publication_date`. An unparseable date becomes `None` rather
  than failing the row.
- **The `%m/%d/%Y` / `%d/%m/%Y` ambiguity is real and is not resolved.** For
  any day ≤ 12 the two formats both parse and disagree, and nothing in the
  row says which was meant. US-first is kept (Retraction Watch is a US
  publication) and **documented in the function's docstring** rather than
  presented as settled. Callers needing certainty should read
  `original_paper_date`/`retraction_date` as approximate to the month.
- **Rows are skipped**, not yielded, when they carry no `Record ID` (nothing
  to make re-import idempotent against — and `RetractionNotice` requires one,
  so the check necessarily precedes construction) or no identifier at all for
  the retracted paper (nothing to ever look it up by). Each skip is reported
  through the optional `on_skip(row_number, reason)` callback and logged at
  DEBUG, so a malformed export is diagnosable rather than silently short.

## What this does *not* do

Recorded so a later reader does not mistake an absence for an oversight.

- **No downloader.** `parse_retraction_watch_csv()` takes a path or an open
  binary stream; acquiring the CSV is the caller's. Crossref's endpoint
  returned `504 Gateway Time-out` on both attempts while this was designed,
  which is a fair warning about owning that dependency.
- **No fetcher-registry entry**, for the protocol reasons above.
- **No wiring into `transparency/` or `quality/`.** A retracted paper
  arguably belongs in a transparency score or as a quality veto, but both are
  scoring changes that move stored values for existing users, and neither is
  needed to deliver the lookup. Separate work, separate decision.
- **No `is_paper_retracted(conn, doi=...)` convenience.** It would be
  `is_retracted(lookup_retractions(conn, doi=...))` — two named steps that
  keep the pure rule separable from the I/O.

## Testing

`tests/test_retractions.py` covers the parser, the model round-trip, and the
rule. Storage round-trips additionally go in `tests/test_backends.py`, which
CLAUDE.md names as the guard against `publications/` drifting back to
SQLite-only SQL — it runs each test against PostgreSQL too when
`BMLIB_TEST_POSTGRESQL_DSN` is set, and CI fails rather than skips.

Named regression tests, one per defect and per decision that could be
"simplified" back:

| Test | Pins |
|---|---|
| `test_the_pmid_column_of_the_real_export_is_found` | Defect 1 |
| `test_the_retracted_paper_is_preferred_to_the_notice` | Defect 1 — column ordering |
| `test_a_failed_encoding_attempt_does_not_duplicate_rows` | Defect 2 |
| `test_a_byte_order_mark_does_not_hide_the_first_column` | Defect 3 |
| `test_a_reinstatement_does_not_read_as_retracted` | Defect 4 |
| `test_a_later_correction_does_not_clear_an_earlier_retraction` | The rule's subtlety |
| `test_an_unknown_nature_is_preserved_rather_than_rejected` | `OTHER` over a raise |
| `test_reimporting_the_same_file_does_not_duplicate_notices` | `UNIQUE(record_id)` + the upsert |
| `test_a_prefixed_uppercase_doi_matches_a_stored_notice` | One normaliser, not two |
| `test_a_row_with_no_usable_identifier_is_reported_not_stored` | Skips are not silent |

Test data is a small committed CSV fixture built to the Crossref column list,
including a BOM'd and a mixed-encoding variant. No network, matching the rest
of the suite.

## Documentation

`docs/manual/publications.md` gains a Retractions section; `CHANGELOG.md`
gets an `[Unreleased]` entry; ROADMAP.md gains a row under **Publications**.
