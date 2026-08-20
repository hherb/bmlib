# Partitioning an Over-Cap PubMed Day — Design

**Date:** 2026-08-21
**Status:** Approved (interactive session; scope decisions recorded below)
**Closes:** #105. Dissolves #107.
**Ships as:** its own PR

## Problem

`fetch_pubmed` refuses any day whose ESearch count exceeds
`EFETCH_MAX_RETRIEVABLE` (9,999):

```python
if count > EFETCH_MAX_RETRIEVABLE:
    ...
    return FetchResult(..., status="failed", error=message)
```

That refusal is correct containment and was the right call for #96's PR — a
history session serves only its first 9,999 records, so the day cannot be
`completed` without durably losing the remainder, and fetching the reachable
part would re-fetch it on every later run forever. But it is containment, not a
fix: **those days have no records at all**, and while any is in the sync window
bmlib's "no publication is missed" property is false.

The condition is not an edge case in the field bmlib queries. A record carrying
only a year and a month is indexed at day 1 of that month under
`[Date - Publication]`, and one carrying only a year at 1 January, so those days
are structurally enormous. Measured 2026-08-20: 0 of 58 ordinary days over the
cap (median 4,890), **16 of 16 month firsts and 1 Januarys over it** (month
firsts 49,543–90,571, 1 January 212,439–315,282). A six-year backfill window
holds some 72 such days.

Two consequences follow, and the second is why #107 exists. A refused day is
recorded `failed`, and `_days_needing_fetch()` re-offers a failed day on
**every** later run — correct, since it is what makes this fix pick those days
up automatically, but it also means `SyncReport.errors` never returns to empty
and an operator alerting on non-emptiness is paged from day one forever.

## Scope decisions (recorded)

| Decision | Chosen | Rejected alternatives |
|---|---|---|
| Route | **Partition the day into sub-queries via the E-utilities API** | The PubMed FTP baseline / daily-update files (see "The FTP route", below) |
| Splitting predicate | **Entrez-date (`[EDAT]`) ranges, recursively halved** | Publication type, MeSH, language, journal — none is both disjoint and covering (see "Why a numeric range") |
| Ladder root | **Fixed `1900/01/01 – 2100/12/31`** | Derived from the target date (would have to assume how far before or after publication a record may be indexed — the thing the root probe exists to *verify*) |
| Sibling counts | **Subtraction: `right = parent − left`** | A second ESearch per split (doubles the planning cost to re-measure a number arithmetic already gives) |
| A single Entrez day still over cap | **Fail the day, naming that Entrez date and its count** | Fetch its reachable 9,999 and complete (a durable, invisible loss — precisely what #88–#95 exist to prevent); add a PMID-range second rung now (designs against a case not yet shown to exist, on unmeasured syntax) |
| Coverage | **A root probe: `count(day AND root range)` must not come up short of `count(day)`** | Assume the root covers (a record outside it is absent from every part's promise, so every part reconciles perfectly while the day is short) |
| Reconciliation | **Root probe + per part + day total** | Per part only (blind to a bad root); day total only (loses per-part stall detection, and a 50% floor over 242k records tolerates a 121,000-record gap) |
| Default | **On, with the cost stated in prose** | Opt-in flag (leaves the property false by default for the operators least likely to find the flag); a caller-set ceiling (a knob whose default value is itself this same decision) |
| Resume within a day | **Checkpoint each completed part; a re-run skips it** | None (a part failing at 90% of a 242k-record day costs the whole day again — and the buffer problem below forces a per-part flush regardless, so the boundary already exists) |
| Checkpoint identity | **An opaque `part_key` plus an explicit `part_scheme`** | Typed `edat_lo`/`edat_hi` columns (bakes one fetcher's partitioning scheme into the shared schema) |
| How a fetcher opts in | **`SourceDescriptor.resumable`, default `False`** | Pass the new kwargs to every fetcher (`register_source()` is public — an existing third-party fetcher would raise on an unexpected keyword) |
| #107 | **Close it, dissolved** | Build its `blocked` field (the permanent refusal it describes no longer occurs) |

## Measured evidence

Probed live 2026-08-21, three real over-cap days, ladder as designed below:

| Day | Count | Root range | Parts | Stuck | Depth | ESearch calls | Sum of parts |
|---|---|---|---|---|---|---|---|
| 2024/01/01 | 242,216 | 242,216 ✔ | 37 | 0 | 13 | 40 | 242,216 — exact |
| 2020/01/01 | 234,972 | 234,972 ✔ | 37 | 0 | 13 | 40 | 234,972 — exact |
| 2015/01/01 | 227,173 | 227,173 ✔ | 36 | 0 | 13 | 40 | 227,173 — exact |

Four things that establishes, each of which the design would otherwise be
assuming:

1. **An `[EDAT]` range term composes with a `[Date - Publication]` term.** The
   syntax is legal and the root range returns the day's whole count.
2. **The root covers.** `count(day AND 1900–2100[EDAT]) == count(day)` on every
   day probed — no record of these days is indexed outside the root.
3. **The halves tile exactly.** Summed leaves equal the root count with no
   residue, so subtraction is sound and double-counting does not occur.
4. **The ladder terminates well above its floor.** Depth 13 of the ~17 the root span allows,
   largest leaf 9,931, and **no single Entrez day exceeded the cap** — the
   "stuck" branch was not reached on any probed day. A single Entrez day of
   2024/01/01 held 2,026 records.

The largest leaf at 9,931 is worth noting: halving stops as soon as a part
fits, so leaves sit *just* under the cap by construction. That is what makes
the re-check in §3 load-bearing rather than defensive.

## Design

### 1. Why a numeric range, and not a facet

Any predicate `P` splits a day into `AND P` and `NOT P`, which is disjoint and
covering by construction — but only if `P` is a predicate each record either
satisfies or does not, *and* the fetcher can then subdivide both halves. The
facets a reader reaches for first fail one of those:

- **Publication type, MeSH term** — a record carries several. `AND pt1` and
  `AND pt2` overlap, so records are fetched twice and delivery is inflated past
  the day's own count, which is exactly what would hide a real shortfall from
  `reconcile_delivery`.
- **Journal, language** — can be absent, and the vocabulary is unbounded and
  heavily skewed, so a ladder over it neither terminates predictably nor covers.
- **`NOT P` chains generally** — the complement of a facet value is not itself
  subdividable by the same mechanism, so the recursion has no uniform step.

A numeric range has both properties structurally: `[lo, mid]` and `[mid+1, hi]`
tile `[lo, hi]` as arithmetic, and each half is the same kind of thing as its
parent, so one step recurses to any depth. Entrez date is the range every record
has exactly one of, and — because a structural day's records were loaded across
decades — it shards a day well rather than piling it into one bucket.

### 2. The ladder

```
plan(day):
    day_count = esearch_count(day_term)
    root_count = esearch_count(day_term AND edat[1900/01/01 : 2100/12/31])
    if root_count < day_count:   fail the day        # coverage probe
    return descend(1900/01/01, 2100/12/31, root_count)

descend(lo, hi, n):
    if n == 0:                   return []           # skipped, not recursed
    if n <= EFETCH_MAX_RETRIEVABLE: return [Partition(lo, hi, n)]
    if lo == hi:                 raise Unsplittable(lo, n)
    mid  = lo + (hi - lo) // 2
    left = esearch_count(day_term AND edat[lo : mid])
    return descend(lo, mid, left) + descend(mid + 1 day, hi, n - left)
```

where `edat[lo : hi]` is literally
`("<lo>"[EDAT] : "<hi>"[EDAT])` in `YYYY/MM/DD`, ANDed to the day's own
`("<date>"[Date - Publication])` term.

One ESearch per internal node — the right child's count is the parent's minus
the left's, since the halves tile. A zero-count part is skipped rather than
recursed, which is what keeps the empty 1900–2012 region free. Measured cost:
40 calls to plan 37 parts.

**Plan first, then fetch.** The descent is cheap and the fetch is not, so the
whole ladder is built before any record is retrieved, and the plan is logged —
part count and record total — at INFO before the walk begins. An operator
watching a run sees what it has committed to before a gigabyte arrives.

### 3. Fetching a part

Each part is an ordinary day-walk: its own `usehistory=y` ESearch, the existing
`range(0, count, EFETCH_PAGE_SIZE)` loop, the existing stall rule, the existing
`reconcile_delivery` against **its own** count.

The page walk is extracted out of `fetch_pubmed` into `_walk_session()` so the
single-part and partitioned paths run identical code. Two copies of that loop
is how the stall rule and the fixed stride — both of which cost a measurement
round to establish (#88, #96) — would come to differ.

**A part re-checks its own count.** The part's ESearch returns a fresh count;
if it now exceeds the cap, the part is re-partitioned rather than refused. A
leaf measured at 9,931 is one busy indexing day from 9,999, and the check is
free — that ESearch happens anyway, to open the session.

### 4. What fails, and how

Every rule fails closed, and a failed day is still re-offered, so a transient
stays recoverable.

| Condition | Verdict | Why not something softer |
|---|---|---|
| Root probe comes up **short** (`root < day`) | Fail the day | Records of this day exist outside the ladder. They are in no part's promise, so every part would reconcile perfectly while the day is silently short. |
| Root probe comes up **long** (`root > day`) | Proceed | The two counts are two ESearches at two instants, and a record indexed between them is added at EDAT=today — inside the range. Requiring equality would fail a correct ladder on ordinary drift; the day-total rule below still judges what actually arrived. |
| A single Entrez day over cap | Fail the day, naming that Entrez date and its count | Keeps today's behaviour as the floor for the one case the ladder cannot reach. The message must be distinguishable from the refusal this change removes — an operator reading a log has to be able to tell "still broken" from "broken differently". |
| Any part's reconciliation fails | Fail the day | Preserves the stall rule, the only rule that catches a session expiring on a last page. |
| Day total short vs the day's own count | The existing `reconcile_delivery` | Catches a whole part going missing even when every part passed. |

The asymmetry in the two root rows is the same one that governs the day total,
and it is why that check uses the existing floor rather than equality: a record
indexed mid-run lands at EDAT=today, *inside* the root range, so it inflates
rather than hides. Short is the dangerous direction, and short is what both
rules judge.

A removal between the two root ESearches also reads as short and so fails the
day. That is accepted: removals are rare, the verdict is recoverable rather
than durable — a failed day is re-offered on the next run, which re-probes —
and the alternative is a tolerance band, which is a threshold nothing has
measured.

### 5. Progress and reporting

`SyncProgress.records_total` stays the **day's** count, not the part's, and
`records_processed` accumulates across parts — so a caller's progress bar
measures the day it asked for. The part being walked goes in `message`.

### 6. Resuming an interrupted day

**The buffer forces this boundary anyway.** `sync()` collects a whole day into
`day_records` and stores it in one transaction after the fetch returns, and the
comment at that buffer already names the limit it is about to meet:

> The whole day is held in memory (typically a few thousand records, tens of MB
> with abstracts); if a source ever delivers far larger days, flush in chunks
> here.

A 242,216-record day is not tens of MB. Partitioning needs a chunked flush
whether or not it resumes — and the part is the natural chunk, so the flush
boundary and the checkpoint boundary are the same boundary. That is what makes
the checkpoint trustworthy: **a part's rows and its checkpoint commit in one
transaction**, so a checkpoint can never attest to records a rollback discarded.

**Storage.** A new table, on the existing parallel-DDL pattern in
`publications/schema.py` (`TEXT` dates, `AUTOINCREMENT` against `SERIAL`):

```sql
CREATE TABLE IF NOT EXISTS download_day_parts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source        TEXT NOT NULL,
    date          TEXT NOT NULL,
    part_scheme   TEXT NOT NULL,
    part_key      TEXT NOT NULL,
    promised      INTEGER NOT NULL,
    record_count  INTEGER NOT NULL,
    completed_at  TEXT NOT NULL,
    UNIQUE(source, date, part_key)
);
```

`IF NOT EXISTS` means an existing database gains it on the next
`ensure_schema()`, with no migration script.

Rows are **deleted when the day completes**. They describe an unfinished day, so
keeping them past completion would both grow without bound and make a
`recheck_days` re-fetch skip parts it was explicitly asked to redo.

**Why `part_key` is opaque, and what that costs.** The storage layer does not
know how a fetcher partitions, so a second rung — or another fetcher splitting
some other way — needs no schema change. The cost is real and worth naming: the
skip rule is a **string comparison**, so a key format that drifts between
releases matches nothing, resume silently degrades to re-fetching the whole day,
and *nothing is raised* — cost with no error. Two things answer that: one
canonical constructor for the key with a test pinning its exact output, and the
`part_scheme` column, which makes a scheme change visible in the data rather
than silently mismatching, so stale rows can be recognised and dropped
deliberately.

**Protocol, opt-in.** `SourceDescriptor` gains `resumable: bool = False`, and
`sync()` passes the two new keywords *only* to a fetcher whose descriptor
declares it. `register_source()` is public, so an existing third-party fetcher
must keep being called exactly as it is today — passing an unexpected keyword to
it would raise, and the handler around the call would turn a working source into
a failed day.

`fetch_pubmed` gains, mirroring `on_record` / `on_progress`:

- `completed_parts: Mapping[str, PartCheckpoint]` — what a previous run finished.
- `on_part_complete: Callable[[PartCheckpoint], None] | None` — called after a
  part's walk has reconciled, never before.

**The skip rule.** A planned part is skipped if and only if its key is
checkpointed **and its current count equals the stored `promised`**. Key alone
is not enough: a part that has gained records since it was checkpointed would be
skipped forever, and those records would be permanently absent — the exact
durable, silent loss the surrounding family exists to prevent. A count mismatch
re-fetches the part, which is idempotent.

**Skipped parts must be credited.** This is the easiest thing here to get wrong,
and it breaks a guard rather than a feature. §4 reconciles the day's total
delivery against the day's own count; a resumed run never *delivers* the skipped
parts, so without crediting them at their stored `promised` **every resumed day
fails its own day-total check**. The same applies to
`download_days.record_count`, which would otherwise report only the last run's
share of a day fetched across three. Both are credited from the checkpoint rows.

## Cost

Per structural day of ~242,000 records: 40 planning ESearches + 37 session
ESearches + 485 EFetch pages ≈ **562 requests**, and at roughly 4 KB a record
about **1 GB**. With an API key the rate limiting alone is about a minute.

A six-year backfill window holds ~72 such days — 66 month firsts at 49,543–90,571
and 6 January firsts at 212,439–315,282 — so roughly **6.2M records and ~25 GB,
once**. It is once because the day is then `completed` and never re-offered,
which is the opposite of the current state, where the day is `failed` and
retried forever while storing nothing.

This is the data the operator is missing rather than waste, but it arrives
without being asked for, so it goes in the CHANGELOG's data-answer prose beside
0.10.0's re-fetch note, and in `docs/manual/publications.md`.

That is the cost of a *first* run. A run interrupted partway does not repeat
it: parts are checkpointed as they complete, so a re-run resumes where it
stopped. See §6.

## The FTP route, and why not

NCBI's own 400 suggests EDirect, and the annual baseline plus daily update files
at `ftp.ncbi.nlm.nih.gov/pubmed/` are the documented route for bulk retrieval.
They are not the route for this:

- The baseline is the **whole corpus** (~37M records, tens of GB) with no
  publication-date selectivity. Reaching one day's 242,216 records means reading
  all 37M and discarding 99.3% of them. Per day it loses by two orders of
  magnitude against 562 requests.
- It only wins for a *full-corpus* load — at which point `download_days`' entire
  per-day model is beside the point, and the question is no longer "how does
  this fetcher walk a day" but "does bmlib have a second ingestion mode". That
  is a product decision, not this fix.
- It has no path for the ordinary incremental case, which is what `sync()` is.

Recorded in `docs/DECISIONS.md` so it is not re-derived. A whole-corpus
ingestion mode remains open as its own question; nothing here forecloses it.

## Testing

Mocked HTTP throughout, following `test_pubmed_fetcher.py`'s existing patterns.
Each rule above gets a named test:

- **The ladder tiles** — a stubbed count function over a synthetic distribution;
  assert every record of the day lands in exactly one part.
- **Subtraction is not double-counting** — a distribution where the right child
  is non-empty and its count is never separately requested.
- **A zero part is skipped, not recursed** — assert no ESearch is issued below it.
- **The root probe fails the day** when `count(day AND root) < count(day)`, with
  every part otherwise reconciling — the case the probe exists for, so the test
  must show the other checks passing.
- **An unsplittable Entrez day fails the day**, and the message names that date
  and its count. Assert on something unique to that line, not on a bare number
  that a neighbouring DEBUG line also emits.
- **A part re-partitions when its own count has crossed the cap** since planning.
- **A failing part fails the day**, including the stall case.
- **The day total is reconciled** — a part silently delivering nothing while
  each other part passes.
- **The under-cap path is unchanged** — a negative control: a day of 4,890
  records issues no partitioning ESearch at all.
- **Progress reports the day's total**, not a part's.

Resume (`test_sync.py`, plus the dual-backend file since it is a schema change):

- **A checkpoint and its records commit together** — a store failure inside a
  part leaves no checkpoint row for it.
- **A re-run skips a checkpointed part** whose count is unchanged, and issues no
  session ESearch for it.
- **A re-run re-fetches a checkpointed part whose count moved** — the rule's
  whole point, so the test asserts the records arrive rather than only that a
  request was made.
- **A skipped part is credited to the day total** — a negative control: without
  crediting, this day fails; the test pins that it completes.
- **`download_days.record_count` covers the whole day**, not the last run's share.
- **Part rows are deleted when the day completes**, and a `recheck_days` re-fetch
  therefore redoes every part.
- **A non-resumable fetcher is called with exactly today's keywords** — pinned
  by a fetcher whose signature rejects extras, so the guard cannot rot into a
  claim about a fetcher that happens to accept `**kwargs`.
- **The key constructor's output is pinned literally**, since the skip rule is a
  string comparison and a silent format drift costs a full re-fetch with nothing
  raised.

Plus a `--partition` mode in `scripts/sample_efetch_paging.py`, with its own
offline test file on the samplers' shared conventions: a probe that could not be
made never prints as a finding, a population past
`UNMEASURED_SHARE_ERROR_THRESHOLD` reports ERROR rather than a share, and the
run exits non-zero when anything came back unreportable. It reports ladder
shape, exactness, and any stuck part on real days — the standing evidence for
the "0 stuck" claim, which is the one claim here that a future PubMed could
falsify.

## Follow-ups to file, not build

- **A whole-corpus ingestion mode** from the FTP baseline, if bulk loading ever
  becomes a requirement in its own right.
