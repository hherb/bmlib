# Design — Retraction Watch support in `bmlib.publications`

_2026-08-02. Phase 2 row 10 of the bmlibrarian → bmlib porting effort
(`docs/plans/2026-07-17-bmlibrarian-porting-analysis.md`)._

## The problem it solves

A biomedical literature tool must not present a retracted paper as evidence.
bmlib today has no way to answer "is this DOI retracted?" — nothing in the
library carries retraction status, and every consumer that wants it builds
its own table over the same public dataset.

The Retraction Watch database is that dataset. Crossref distributes it as a
CSV under CC0 at `https://api.labs.crossref.org/data/retractionwatch?<mailto>`
— 65 MB and **71,306 rows** in the 2026-08-03 export, covering retractions,
corrections, expressions of concern and reinstatements.

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
and can grow; one new notice type must not abort a 71,000-row import, and
nothing is lost.

To be precise about the evidence: **every one of the 71,306 real rows in the
2026-08-03 export maps to one of the four known values** (Retraction 92.65%,
Expression of concern 5.03%, Correction 2.10%, Reinstatement 0.22%). `OTHER`
is forward-compatibility, not a case the current file exercises — it exists
so that the *next* notice type Retraction Watch introduces costs a row of
reduced fidelity instead of a failed import.

Matching is case-insensitive on a stripped value, because the file writes
`Expression of concern` (lower-case `c`) against the enum's
`EXPRESSION_OF_CONCERN`.

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
re-importing 71,306 rows if it ever changes.

> Scan notices newest first. The first `RETRACTION` or `REINSTATEMENT`
> decides: `RETRACTION` → `True`, `REINSTATEMENT` → `False`. Any other
> nature is not evidence either way. No such notice → `False`.

The subtlety is that a flat "latest notice wins" is wrong. A paper retracted
in 2020 and *corrected* in 2021 would read as not-retracted, because the
correction is the latest notice. A correction does not undo a retraction;
only a reinstatement does.

**This is not hypothetical.** In the 2026-08-03 export, 2,354 papers carry
more than one notice and 2,297 of those have notices of differing natures —
so multi-notice papers are ordinary, not an edge case. Of them, **52 papers
have a Correction or Expression of Concern as their newest notice while
having been retracted earlier**; flat latest-wins reports every one of those
retracted papers as clean. `10.1016/j.anbehav.2009.11.027` is the starkest —
retracted 2011-09-08, corrected 2017-12-14, six years apart — and is used as
a test fixture.

The rule also handles the converse correctly, which the same data exercises:
`10.1161/CIRCRESAHA.116.308301` was reinstated 2022-10-28 and corrected
2023-03-23, and reads as not retracted.

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
   `utf-8-sig` then appends all 71,306 again — 40,000 of them twice, with no
   error anywhere. The port **probes** a leading chunk to choose the
   encoding, then **streams** with it. Streaming also stops the 65 MB file
   being held in memory as 71,306 dicts, which is the other reason to
   restructure.

   > **Superseded:** the leading-chunk probe described above did not survive
   > review — see defect 3's note below for why. The shipped code scans the
   > *whole* file (`_decodes_whole_file()` / `_detect_encoding()` in
   > `retractions.py`) before choosing an encoding, then rewinds and streams
   > it. This paragraph's "probes a leading chunk" is history, not current
   > behaviour.

3. **A byte-order mark hides the first column.** Upstream tries `utf-8`
   *before* `utf-8-sig`. On a BOM'd file `utf-8` does not fail — it succeeds
   and glues `﻿` to the first field name, so `DictReader` yields
   `"﻿Record ID"` and `_find_column()` can never match `Record ID`. The
   port probes `utf-8-sig` first. Ordering `latin-1` **last** additionally
   makes the chain total: every byte is valid Latin-1, so a file that
   defeats the earlier candidates still streams rather than raising
   mid-parse, which a streaming reader cannot retry.

   > **Superseded:** a 64 KiB leading-chunk probe can decode cleanly and
   > still leave a single bad byte tens of megabytes later — a real failure
   > mode a streaming `TextIOWrapper`/`DictReader` cannot recover from once
   > rows have already reached the caller. The shipped code scans the whole
   > file up front instead of probing a leading chunk (see
   > `_ENCODING_SCAN_CHUNK_BYTES` and `_decodes_whole_file()` in
   > `retractions.py`), which is what "still streams rather than raising
   > mid-parse" now actually rests on. `test_an_invalid_byte_past_the_old_probe_window_does_not_crash_mid_stream`
   > in `tests/test_retractions.py` is the regression test for this.

4. **Every row is stored as `is_retracted = TRUE`.** Upstream hardcodes it in
   the INSERT, so a Correction, an Expression of Concern, and a Reinstatement
   — which is the *opposite* of a retraction — all mark the paper retracted.
   The port types the nature and applies the rule above.

5. **Missing identifiers are written as truthy sentinels, not empty cells.**
   This is the sharpest defect, and it is invisible without looking at the
   data. `_find_column()` returns the first candidate whose value is truthy —
   and neither sentinel is falsy:

   | Column | Sentinel for "absent" | Rows | Share |
   |---|---|---|---|
   | `OriginalPaperPubMedID`, `RetractionPubMedID` | `0` | 32,831 | **46.04%** |
   | `OriginalPaperDOI` | `Unavailable` / `unavailable` | 3,419 | **4.80%** |

   Upstream would therefore store a PMID of `"0"` for nearly half the
   database and a DOI of `"Unavailable"` for 3,419 rows. Each sentinel
   collapses those rows onto a single fake key: `lookup_retractions(pmid="0")`
   would return thirty-two thousand unrelated notices. Both are stripped to
   `None` before `storage._normalize_doi` / `_normalize_pmid` ever see them.

   The DOI sentinel appears in **two casings** in the same file
   (`Unavailable` 2,235, `unavailable` 1,184), so the check is
   case-insensitive. A case-sensitive check would leak 1,184 rows — the kind
   of partial fix that looks like it works.

   Once both sentinels and genuinely empty cells are accounted for,
   **5,189 rows (7.28%) carry no usable identifier for the retracted paper at
   all** and are skipped, since nothing could ever look them up.

## Parsing details

All of the following are measured against the live Crossref export
downloaded 2026-08-03 (see "Evidence" below), not inferred from
documentation.

- **Reasons.** Semicolon-separated within the cell, with a **trailing
  semicolon on every one of the 71,306 real rows** — so a naive `split(";")`
  always yields an empty final item. Split on `;`, strip whitespace, drop
  empties. There is **no `+` prefix** in the Crossref export (0 rows of
  71,306); that convention belongs to Retraction Watch's own export. One
  leading `+` is stripped anyway, as a cheap accommodation for the other
  variant, but it is not what this file looks like.
- **Dates.** `M/D/YYYY H:MM` — US month-first, unpadded, with a time
  component present on **all 71,306** dated rows (`3/9/2026 0:00`). Parsed
  by trying ISO first, then `%m/%d/%Y`, `%d/%m/%Y`, `%Y/%m/%d`, each with and
  without a trailing time. Output is an ISO `yyyy-mm-dd` string, matching
  `Publication.publication_date`. An unparseable date becomes `None` rather
  than failing the row.
- **The `%m/%d/%Y` / `%d/%m/%Y` ambiguity is real and is not resolved.** For
  any day ≤ 12 the two formats both parse and disagree, and nothing in the
  row says which was meant. US-first is kept — confirmed correct for this
  export by `3/9/2026`-style values whose day exceeds 12 elsewhere in the
  file — and **documented in the function's docstring** rather than presented
  as settled, since the format is Retraction Watch's to change.
- **The export has an unnamed 21st column.** The header row ends in a comma,
  so `csv.DictReader` yields a `""` key holding an empty string on every row.
  Harmless — `_find_column()` only ever asks for named columns — but it is
  why the fieldname list has 21 entries for 20 documented fields, and worth
  knowing before someone "fixes" a header-count assertion.
- **Rows are skipped**, not yielded, when they carry no `Record ID` (nothing
  to make re-import idempotent against — and `RetractionNotice` requires one,
  so the check necessarily precedes construction) or no identifier at all for
  the retracted paper (nothing to ever look it up by). Each skip is reported
  through the optional `on_skip(row_number, reason)` callback and logged at
  DEBUG, so a malformed export is diagnosable rather than silently short.

  Both branches fire on the real file, which is why neither is theoretical.
  **The export ends with 190 entirely empty rows** (lines 71,308–71,497 —
  every field blank, including `Record ID`), and 5,189 further rows carry
  only sentinel identifiers. A parser that trusted the row count would report
  71,496; the honest figure is 71,306 real rows, of which 66,117 are usable.
  This is also why `Record ID` can be required without qualification: it is
  present on every real row, and absent only on padding that must be dropped
  regardless.

## What this does *not* do

Recorded so a later reader does not mistake an absence for an oversight.

- **No downloader.** `parse_retraction_watch_csv()` takes a path or an open
  binary stream; acquiring the CSV is the caller's. Crossref's endpoint
  returned `504 Gateway Time-out` on three attempts on 2026-08-02 and served
  the full file on 2026-08-03 — it works, but it is slow enough to time out
  a default client, which is a fair warning about owning that dependency
  inside a library.
- **No fetcher-registry entry**, for the protocol reasons above.
- **No wiring into `transparency/` or `quality/`.** A retracted paper
  arguably belongs in a transparency score or as a quality veto, but both are
  scoring changes that move stored values for existing users, and neither is
  needed to deliver the lookup. Separate work, separate decision.
- **No `is_paper_retracted(conn, doi=...)` convenience.** It would be
  `is_retracted(lookup_retractions(conn, doi=...))` — two named steps that
  keep the pure rule separable from the I/O.

## Evidence

Every quantitative claim above is measured against the live Crossref export,
not taken from documentation. Provenance, so a later reader can re-derive or
challenge them:

- **File:** `https://api.labs.crossref.org/data/retractionwatch?<mailto>`,
  downloaded **2026-08-03**, HTTP 200, 65,634,375 bytes, UTF-8, no BOM.
  The same URL returned `504 Gateway Time-out` three times on 2026-08-02; it
  takes several minutes to serve, so a short client timeout reads as an
  outage.
- **Shape:** 21 header fields for 20 documented columns (the header line ends
  in a comma, so `csv.DictReader` carries a `""` key). 71,496 parsed rows,
  of which the last **190 are entirely empty**; 71,306 real rows.

| Measurement | Value |
|---|---|
| Nature: Retraction / EoC / Correction / Reinstatement | 66,062 / 3,585 / 1,499 / 160 |
| Nature values outside those four | 0 |
| `OriginalPaperPubMedID == "0"` | 32,831 (46.04%) |
| `OriginalPaperDOI` ∈ {`Unavailable`, `unavailable`} | 3,419 (2,235 / 1,184) |
| Rows with no usable DOI **and** no usable PMID | 5,189 (7.28%) |
| `Reason` containing `+` | 0 |
| `Reason` with a trailing `;` | 71,306 (100%) |
| `RetractionDate` carrying a time component | 71,306 (100%) |
| Papers (real DOI) with >1 notice | 2,354 |
| …whose notices differ in nature | 2,297 |
| …newest is Correction/EoC yet retracted earlier | 52 |

The download is **not** committed — it is 65 MB and belongs to Retraction
Watch. Test fixtures are small hand-built CSVs reproducing these shapes,
including the `0` and `Unavailable` sentinels, the trailing `;`, the trailing
empty rows, and the `10.1016/j.anbehav.2009.11.027` retraction-then-correction
sequence.

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
| `test_a_zero_pubmed_id_is_not_stored_as_a_pmid` | Defect 5 — the 46% sentinel |
| `test_an_unavailable_doi_is_not_stored_as_a_doi` | Defect 5 — both casings |
| `test_the_trailing_empty_rows_are_skipped_not_stored` | The export's 190 rows of padding |
| `test_the_trailing_semicolon_does_not_become_an_empty_reason` | Reason splitting |
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
