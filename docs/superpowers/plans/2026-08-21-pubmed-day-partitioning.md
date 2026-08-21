# Partitioning an Over-Cap PubMed Day Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a PubMed day larger than 9,999 records fetchable, by splitting it into Entrez-date sub-queries that each fit, and resumable, by checkpointing each part as it completes.

**Architecture:** `fetch_pubmed` plans a ladder of `[EDAT]` date ranges — halved recursively until every part is under the cap — then walks each part as an ordinary history session. A new `download_day_parts` table records each completed part, and `sync()` flushes that part's buffered records and its checkpoint row in one transaction, so a checkpoint can never attest to records a rollback discarded.

**Tech Stack:** Python 3.11+, `xml.etree.ElementTree`, httpx-compatible injected client, SQLite + PostgreSQL via `bmlib.db`, pytest with `MagicMock` clients and in-memory SQLite.

**Spec:** [`docs/superpowers/specs/2026-08-21-pubmed-day-partitioning-design.md`](../specs/2026-08-21-pubmed-day-partitioning-design.md) — read it before Task 1; every task argues from it.

## Global Constraints

- **AGPL-3 header** at the top of every new source file. Copy verbatim from any existing file.
- **`from __future__ import annotations`** at the top of every module; lowercase builtin generics (`list[str]`, not `List[str]`); `datetime.UTC`, not `timezone.utc`.
- **Type hints and docstrings** on every public function, class and module. Google-style, consistent within a module.
- **No ORM.** Explicit SQL through `bmlib.db` helpers (`execute`, `fetch_one`, `fetch_all`, `fetch_scalar`). Placeholders come from `placeholder(conn)` / `placeholders(conn, n)` — never a hard-coded `?`.
- **Both backends.** Any SQL added must run on SQLite and PostgreSQL. `tests/test_backends.py` is where dual-backend coverage lives.
- **Tests before implementation**, always. A `ModuleNotFoundError`/`AttributeError` is the correct red for a new symbol.
- **Verification before any commit:** `uv run pytest tests/ -v`, `uvx ruff@0.15.20 check .`, `uvx ruff@0.15.20 format --check .`, `uv run mypy` (bare — takes no arguments).
- **`uv` only, never bare pip.**
- **Constants fixed by the spec:** `EFETCH_MAX_RETRIEVABLE = 9999` (unchanged), `EFETCH_PAGE_SIZE = 500` (unchanged), ladder root `1900/01/01`–`2100/12/31`, `PART_SCHEME = "edat-range"`.
- **Rate limiting applies to ladder ESearches too** — `RATE_LIMIT_WITH_KEY` / `RATE_LIMIT_WITHOUT_KEY` between every request, not only between EFetch pages. A 40-request ladder issued back to back would exceed NCBI's un-keyed 3/sec limit.
- **Log assertions must be unique to the line under test.** A bare `"9999"` or `"400"` already appears in neighbouring DEBUG lines; assert on a distinctive phrase.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `bmlib/publications/fetchers/pubmed.py` | Term building, the EDAT ladder, session walking, `fetch_pubmed` | Modify — grows the ladder, loses its refusal branch |
| `bmlib/publications/models.py` | `PartCheckpoint` dataclass; `SourceDescriptor.resumable` | Modify |
| `bmlib/publications/schema.py` | `download_day_parts` DDL, both backends | Modify |
| `bmlib/publications/sync.py` | Checkpoint load/write/clear; per-part flush; resumable dispatch | Modify |
| `bmlib/publications/fetchers/registry.py` | Declare PubMed resumable | Modify |
| `scripts/sample_efetch_paging.py` | `--partition` live measurement mode | Modify |
| `tests/test_pubmed_fetcher.py` | Ladder, walk, skip rule, crediting | Modify |
| `tests/test_sync.py` | Checkpoint lifecycle, per-part flush, dispatch | Modify |
| `tests/test_backends.py` | `download_day_parts` on both backends | Modify |
| `tests/test_efetch_paging_sampler.py` | Offline cover for `--partition` | Modify |

---

## Task 1: `_esearch` takes a term, not a date

**Files:**
- Modify: `bmlib/publications/fetchers/pubmed.py:511-558` (`_esearch`), `:640-829` (`fetch_pubmed`'s call site)
- Test: `tests/test_pubmed_fetcher.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `_day_term(target_date: date) -> str`; `_esearch(client: Any, term: str, api_key: str | None, *, usehistory: bool = True) -> tuple[int, str | None, str | None]`.

A pure refactor. The ladder needs to count arbitrary terms, and `_esearch` currently builds its own term from a `date`. Existing tests are the guard that behaviour did not move.

- [ ] **Step 1: Write the failing test**

In `tests/test_pubmed_fetcher.py`, add:

```python
class TestTheSearchTermIsBuiltSeparately:
    """The ladder counts arbitrary terms, so term-building is not _esearch's job."""

    def test_day_term_is_the_publication_date_field(self):
        from bmlib.publications.fetchers.pubmed import _day_term

        assert _day_term(date(2024, 1, 1)) == '("2024/01/01"[Date - Publication])'

    def test_esearch_sends_the_term_it_was_given(self):
        from bmlib.publications.fetchers.pubmed import _esearch

        client = MagicMock()
        response = MagicMock()
        response.text = _make_esearch_xml(7)
        client.get.return_value = response

        count, web_env, query_key = _esearch(client, "SOME TERM", None)

        assert count == 7
        assert client.get.call_args.kwargs["params"]["term"] == "SOME TERM"

    def test_counting_does_not_open_a_history_session(self):
        from bmlib.publications.fetchers.pubmed import _esearch

        client = MagicMock()
        response = MagicMock()
        response.text = _make_esearch_xml(3)
        client.get.return_value = response

        _esearch(client, "SOME TERM", None, usehistory=False)

        assert "usehistory" not in client.get.call_args.kwargs["params"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_pubmed_fetcher.py::TestTheSearchTermIsBuiltSeparately -v`
Expected: FAIL — `ImportError: cannot import name '_day_term'`

- [ ] **Step 3: Write minimal implementation**

In `pubmed.py`, above `_esearch`:

```python
def _day_term(target_date: date) -> str:
    """Return the ESearch term for one publication day.

    ``[Date - Publication]`` rather than ``[EDAT]`` is deliberate and load-bearing:
    it is the field bmlib syncs by, and the two disagree by orders of magnitude on
    exactly the days this module has to handle (see ``docs/DECISIONS.md``).
    """
    return f'("{target_date:%Y/%m/%d}"[Date - Publication])'
```

Change `_esearch`'s signature and body:

```python
def _esearch(
    client: Any,
    term: str,
    api_key: str | None,
    *,
    usehistory: bool = True,
) -> tuple[int, str | None, str | None]:
    """Run an ESearch query for *term* and return (count, web_env, query_key).

    *usehistory* is ``False`` for the ladder's counting probes, which need a
    number and not a session; opening one per probe would leave dozens of
    unused sessions on NCBI's server for every over-cap day.
    """
    params: dict[str, str | int] = {"db": "pubmed", "term": term, "retmax": 0}
    if usehistory:
        params["usehistory"] = "y"
    if api_key:
        params["api_key"] = api_key
    ...  # body below `response = client.get(...)` is unchanged
```

Update the one call site in `fetch_pubmed`:

```python
    day_term = _day_term(target_date)
    try:
        count, web_env, query_key = _esearch(client, day_term, api_key)
```

- [ ] **Step 4: Run the whole fetcher suite**

Run: `uv run pytest tests/test_pubmed_fetcher.py -v`
Expected: PASS — the pre-existing tests are the guard that nothing moved.

- [ ] **Step 5: Commit**

```bash
git add bmlib/publications/fetchers/pubmed.py tests/test_pubmed_fetcher.py
git commit -m "refactor(publications): give _esearch a term instead of a date"
```

---

## Task 2: Extract the page walk into `_walk_session`

**Files:**
- Modify: `bmlib/publications/fetchers/pubmed.py` (`fetch_pubmed`'s page loop)
- Test: `tests/test_pubmed_fetcher.py`

**Interfaces:**
- Consumes: Task 1's `_esearch`.
- Produces: `_WalkOutcome(NamedTuple)` with fields `processed: int`, `delivered: int`, `stalled: bool`, `error: str | None`; and `_walk_session(client, web_env: str, query_key: str, promised: int, *, on_record, api_key, rate_limit: float, on_page: Callable[[int], None] | None = None) -> _WalkOutcome`.

Another pure refactor. Every part walks exactly the way a whole day does today, and two copies of this loop is how the stall rule and the fixed stride — each of which cost a measurement round (#88, #96) — would come to differ.

- [ ] **Step 1: Write the failing test**

```python
class TestTheWalkIsSharedBetweenWholeDaysAndParts:
    """One loop, so the stride and the stall rule cannot drift apart."""

    def test_walk_reports_processed_delivered_and_no_stall(self):
        from bmlib.publications.fetchers.pubmed import _walk_session

        client = MagicMock()
        response = MagicMock()
        response.text = _make_efetch_xml(FULL_ARTICLE_XML, MINIMAL_ARTICLE_XML)
        client.get.return_value = response
        records = []

        outcome = _walk_session(
            client, "WE", "1", 2, on_record=records.append, api_key=None, rate_limit=0.0
        )

        assert (outcome.processed, outcome.delivered, outcome.stalled) == (2, 2, False)
        assert outcome.error is None
        assert len(records) == 2

    def test_an_empty_page_before_the_promise_is_met_is_a_stall(self):
        from bmlib.publications.fetchers.pubmed import _walk_session

        client = MagicMock()
        response = MagicMock()
        response.text = _make_efetch_xml()
        client.get.return_value = response

        outcome = _walk_session(
            client, "WE", "1", 5000, on_record=lambda r: None, api_key=None, rate_limit=0.0
        )

        assert outcome.stalled is True
        assert outcome.delivered == 0

    def test_a_failing_page_returns_an_error_rather_than_raising(self):
        from bmlib.publications.fetchers.pubmed import _walk_session

        client = MagicMock()
        client.get.side_effect = RuntimeError("connection reset")

        outcome = _walk_session(
            client, "WE", "1", 10, on_record=lambda r: None, api_key=None, rate_limit=0.0
        )

        assert outcome.error is not None
        assert "connection reset" in outcome.error
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_pubmed_fetcher.py::TestTheWalkIsSharedBetweenWholeDaysAndParts -v`
Expected: FAIL — `cannot import name '_walk_session'`

- [ ] **Step 3: Write minimal implementation**

Add to `pubmed.py`:

```python
class _WalkOutcome(NamedTuple):
    """What one history session's page walk produced."""

    processed: int
    """Records parsed and handed to ``on_record``."""

    delivered: int
    """Record elements the server handed over — never ``processed``."""

    stalled: bool
    """A page delivered nothing while *promised* was still unmet."""

    error: str | None
    """Set when a page raised; the walk stops and the caller fails the day."""


def _walk_session(
    client: Any,
    web_env: str,
    query_key: str,
    promised: int,
    *,
    on_record: Callable[[FetchedRecord], None],
    api_key: str | None,
    rate_limit: float,
    on_page: Callable[[int], None] | None = None,
) -> _WalkOutcome:
    """Walk one history session's pages, parsing every article into *on_record*.

    ``retstart`` indexes the *session's UID list*, not the records delivered so
    far, so the stride stays ``EFETCH_PAGE_SIZE`` whatever a page returns.
    Measured 2026-08-20 (issue #96): a page's record elements are exactly that
    slice of esearch's own ``IdList``. Advancing by what arrived would
    re-request the tail of every short page, deliver those records twice, and
    count the duplicates as delivery — which is precisely what would hide a
    real shortfall from ``reconcile_delivery``.

    *on_page* is called with the running processed count after each page, for
    progress reporting; it is not called after a stalled or failed page.
    """
    processed = 0
    delivered = 0
    for retstart in range(0, promised, EFETCH_PAGE_SIZE):
        try:
            page = _efetch_page(client, web_env, query_key, retstart, api_key)
        except Exception as exc:
            logger.error(
                "efetch failed at retstart=%d: %s: %s", retstart, type(exc).__name__, exc
            )
            return _WalkOutcome(processed, delivered, False, f"{type(exc).__name__}: {exc}")

        delivered += page.delivered
        for article_el in page.articles:
            on_record(_parse_article_xml(article_el))
            processed += 1

        if page.delivered == 0:
            # The session holds `promised` UIDs, so an empty page before the
            # walk is done means it stopped serving them. Paging on costs a
            # request per remaining page and returns nothing.
            return _WalkOutcome(processed, delivered, True, None)

        if on_page is not None:
            on_page(processed)

        if retstart + EFETCH_PAGE_SIZE < promised:
            time.sleep(rate_limit)

    return _WalkOutcome(processed, delivered, False, None)
```

Then replace `fetch_pubmed`'s page loop with a call to it, keeping the surrounding `reconcile_delivery` and `FetchResult` construction exactly as they are:

```python
    records_processed = 0

    def _report(processed: int) -> None:
        if on_progress is not None:
            on_progress(
                SyncProgress(
                    source="pubmed",
                    date=date_str,
                    records_processed=processed,
                    records_total=count,
                    status="in_progress",
                    message=f"Fetched {processed}/{count} records",
                )
            )

    outcome = _walk_session(
        client,
        web_env,
        query_key,
        count,
        on_record=on_record,
        api_key=api_key,
        rate_limit=rate_limit,
        on_page=_report,
    )
    records_processed = outcome.processed
    if outcome.error is not None:
        return FetchResult(
            source="pubmed",
            date=date_str,
            record_count=records_processed,
            status="failed",
            error=outcome.error,
        )

    verdict = reconcile_delivery(
        "pubmed",
        date_str,
        delivered=outcome.delivered,
        promised=count,
        stalled=outcome.stalled,
    )
```

- [ ] **Step 4: Run the whole fetcher suite**

Run: `uv run pytest tests/test_pubmed_fetcher.py -v`
Expected: PASS, including every pre-existing walk, stall and progress test.

- [ ] **Step 5: Commit**

```bash
git add bmlib/publications/fetchers/pubmed.py tests/test_pubmed_fetcher.py
git commit -m "refactor(publications): extract the efetch page walk into _walk_session"
```

---

## Task 3: The EDAT ladder

**Files:**
- Modify: `bmlib/publications/fetchers/pubmed.py`
- Test: `tests/test_pubmed_fetcher.py`

**Interfaces:**
- Consumes: Task 1's `_day_term`.
- Produces: `PART_SCHEME: str`; `_part_key(lo: date, hi: date) -> str`; `_edat_range_term(day_term: str, lo: date, hi: date) -> str`; `_Partition(NamedTuple)` with `lo: date`, `hi: date`, `promised: int`, `key: str`; `_UnsplittableDay(Exception)` with `.edat_day: date` and `.count: int`; `_RootNotCovering(Exception)`; `_plan_partitions(count_fn: Callable[[str], int], day_term: str, day_count: int, *, lo: date = EDAT_ROOT_LO, hi: date = EDAT_ROOT_HI, probe_root: bool = True) -> list[_Partition]`.

The recursion takes an injected `count_fn`, so it is unit-testable without HTTP and the tree's shape can be pinned against a synthetic distribution.

- [ ] **Step 1: Write the failing test**

```python
class TestTheEdatLadder:
    """Parts must tile the day exactly — coverage is the whole guarantee."""

    @staticmethod
    def _counter(distribution: dict[date, int]):
        """Return a count_fn over a synthetic EDAT -> record-count distribution."""

        def count_fn(term: str) -> int:
            m = re.search(r'"([\d/]+)"\[EDAT\] : "([\d/]+)"\[EDAT\]', term)
            if m is None:  # the bare day term
                return sum(distribution.values())
            lo = datetime.strptime(m.group(1), "%Y/%m/%d").date()
            hi = datetime.strptime(m.group(2), "%Y/%m/%d").date()
            return sum(n for d, n in distribution.items() if lo <= d <= hi)

        return count_fn

    def test_parts_tile_the_day_exactly(self):
        from bmlib.publications.fetchers.pubmed import _plan_partitions

        distribution = {date(2023, 1, 1) + timedelta(days=i): 400 for i in range(100)}
        total = sum(distribution.values())  # 40,000 — four times the cap

        parts = _plan_partitions(self._counter(distribution), "DAY", total)

        assert sum(p.promised for p in parts) == total
        assert all(p.promised <= 9999 for p in parts)
        # Disjoint: no two parts share a date.
        spans = sorted((p.lo, p.hi) for p in parts)
        for (_, prev_hi), (next_lo, _) in zip(spans, spans[1:]):
            assert prev_hi < next_lo

    def test_an_empty_range_is_skipped_not_recursed(self):
        from bmlib.publications.fetchers.pubmed import _plan_partitions

        distribution = {date(2023, 6, 1): 20000}
        calls: list[str] = []
        inner = self._counter(distribution)

        def counting(term: str) -> int:
            calls.append(term)
            return inner(term)

        parts = _plan_partitions(counting, "DAY", 20000)

        # Every part returned holds records; the empty centuries cost nothing
        # below themselves.
        assert all(p.promised > 0 for p in parts)
        assert len(calls) < 40

    def test_a_single_entrez_day_over_the_cap_raises(self):
        from bmlib.publications.fetchers.pubmed import _plan_partitions, _UnsplittableDay

        distribution = {date(2023, 6, 1): 25000}

        with pytest.raises(_UnsplittableDay) as exc_info:
            _plan_partitions(self._counter(distribution), "DAY", 25000)

        assert exc_info.value.edat_day == date(2023, 6, 1)
        assert exc_info.value.count == 25000

    def test_a_root_that_does_not_cover_the_day_raises(self):
        from bmlib.publications.fetchers.pubmed import _plan_partitions, _RootNotCovering

        # The day claims 30,000 but only 20,000 are inside the root range.
        distribution = {date(2023, 6, 1) + timedelta(days=i): 10000 for i in range(2)}

        with pytest.raises(_RootNotCovering):
            _plan_partitions(self._counter(distribution), "DAY", 30000)

    def test_a_root_that_is_long_proceeds(self):
        from bmlib.publications.fetchers.pubmed import _plan_partitions

        # A record indexed between the two counts lands inside the range.
        distribution = {date(2023, 6, 1) + timedelta(days=i): 10000 for i in range(2)}

        parts = _plan_partitions(self._counter(distribution), "DAY", 19999)

        assert sum(p.promised for p in parts) == 20000

    def test_the_part_key_format_is_pinned(self):
        from bmlib.publications.fetchers.pubmed import _part_key

        # The skip rule is a string comparison: a silent format change costs a
        # full re-fetch of every unfinished day, with nothing raised.
        assert _part_key(date(2023, 4, 10), date(2023, 8, 31)) == "edat:2023-04-10:2023-08-31"
```

Add `import re` and `from datetime import datetime, timedelta` to the test file's imports if absent.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_pubmed_fetcher.py::TestTheEdatLadder -v`
Expected: FAIL — `cannot import name '_plan_partitions'`

- [ ] **Step 3: Write minimal implementation**

```python
# The ladder's root. Wide enough that no record of any publication day falls
# outside it — verified per day by the root probe rather than assumed, since a
# record indexed outside it would be in no part's promise and every part would
# then reconcile perfectly while the day was silently short.
EDAT_ROOT_LO = date(1900, 1, 1)
EDAT_ROOT_HI = date(2100, 12, 31)

# Names the partitioning scheme in `download_day_parts.part_scheme`. A stored
# key is compared as a string, so a scheme that changes without this changing
# would match nothing and silently re-fetch every unfinished day.
PART_SCHEME = "edat-range"


class _Partition(NamedTuple):
    """One Entrez-date range of a day, small enough to fetch in one session."""

    lo: date
    hi: date
    promised: int

    @property
    def key(self) -> str:
        """This part's identity in ``download_day_parts``."""
        return _part_key(self.lo, self.hi)


class _UnsplittableDay(Exception):
    """A single Entrez day holds more records than one session can serve."""

    def __init__(self, edat_day: date, count: int) -> None:
        self.edat_day = edat_day
        self.count = count
        super().__init__(
            f"{count} records share the Entrez date {edat_day.isoformat()}, above the"
            f" {EFETCH_MAX_RETRIEVABLE} a history session serves, and an Entrez date"
            " cannot be split further; refusing the day"
        )


class _RootNotCovering(Exception):
    """Records of this day lie outside the ladder's root range."""


def _part_key(lo: date, hi: date) -> str:
    """Return the stored identity of the partition spanning *lo* to *hi*.

    The one constructor, because the resume skip rule compares this string:
    a second spelling of the same range matches no checkpoint, so resume
    degrades to a full re-fetch with nothing raised. Pinned by a test.
    """
    return f"edat:{lo.isoformat()}:{hi.isoformat()}"


def _edat_range_term(day_term: str, lo: date, hi: date) -> str:
    """Restrict *day_term* to records indexed between *lo* and *hi* inclusive."""
    return f'{day_term} AND ("{lo:%Y/%m/%d}"[EDAT] : "{hi:%Y/%m/%d}"[EDAT])'


def _plan_partitions(
    count_fn: Callable[[str], int],
    day_term: str,
    day_count: int,
    *,
    lo: date = EDAT_ROOT_LO,
    hi: date = EDAT_ROOT_HI,
    probe_root: bool = True,
) -> list[_Partition]:
    """Split a day into Entrez-date ranges that each fit in one session.

    ``[lo, mid]`` and ``[mid+1, hi]`` tile ``[lo, hi]`` as arithmetic and every
    record carries exactly one Entrez date, so the parts are disjoint and
    covering by construction — below the root. At the root that is an empirical
    claim, so *probe_root* verifies it.

    Only the left child is counted; the right is the parent's count minus it,
    which the tiling makes exact and which halves the ladder's cost.

    Raises:
        _RootNotCovering: The root range holds fewer records than the day does,
            so some record of the day is indexed outside it. Coming up *long*
            is benign — the two counts are two requests at two instants, and a
            record indexed between them lands at EDAT=today, inside the range.
        _UnsplittableDay: A single Entrez date exceeds the cap.
    """
    root_count = count_fn(_edat_range_term(day_term, lo, hi))
    if probe_root and root_count < day_count:
        raise _RootNotCovering(
            f"the Entrez-date range {lo.isoformat()}..{hi.isoformat()} holds {root_count}"
            f" of this day's {day_count} records, so {day_count - root_count} of them lie"
            " outside the ladder and would be silently absent; refusing the day"
        )

    parts: list[_Partition] = []

    def descend(lo_: date, hi_: date, n: int) -> None:
        if n <= 0:
            return
        if n <= EFETCH_MAX_RETRIEVABLE:
            parts.append(_Partition(lo_, hi_, n))
            return
        if lo_ == hi_:
            raise _UnsplittableDay(lo_, n)
        mid = lo_ + (hi_ - lo_) // 2
        left = count_fn(_edat_range_term(day_term, lo_, mid))
        descend(lo_, mid, left)
        descend(mid + timedelta(days=1), hi_, n - left)

    descend(lo, hi, root_count)
    return parts
```

Add `timedelta` to the `datetime` import at the top of `pubmed.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_pubmed_fetcher.py::TestTheEdatLadder -v`
Expected: PASS — 6 tests.

- [ ] **Step 5: Commit**

```bash
git add bmlib/publications/fetchers/pubmed.py tests/test_pubmed_fetcher.py
git commit -m "feat(publications): plan an over-cap day as a ladder of EDAT ranges (#105)"
```

---

## Task 4: Fetch an over-cap day as parts

**Files:**
- Modify: `bmlib/publications/fetchers/pubmed.py` (`fetch_pubmed`'s refusal branch)
- Test: `tests/test_pubmed_fetcher.py`

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces: `_fetch_partitioned(client, target_date: date, day_term: str, day_count: int, *, on_record, on_progress, api_key: str | None, rate_limit: float, completed_parts: Mapping[str, PartCheckpoint] | None = None, on_part_complete: Callable[[PartCheckpoint], None] | None = None) -> FetchResult`. Resume arguments are accepted here but ignored until Task 7, so `fetch_pubmed`'s signature does not churn twice.

This is where #105 actually closes. The refusal branch becomes the recursion.

- [ ] **Step 1: Write the failing test**

Add the shared fake first — it models the real API, so tests do not hard-code term strings:

```python
def _eutils_client(count_fn, *, article_xml=MINIMAL_ARTICLE_XML):
    """A fake E-utilities client: ESearch answers from *count_fn*, EFetch slices.

    EFetch synthesises the page its parameters name, exactly as the live
    backend does — the slice of the session's UID list at ``retstart``, capped
    at ``retmax``. Modelling that rather than a fixed list of bodies is what
    lets a test exercise a ladder whose terms it never has to spell out.
    """
    import itertools

    sessions: dict[str, int] = {}
    counter = itertools.count(1)

    def get(url, params=None):
        params = params or {}
        response = MagicMock()
        if url == ESEARCH_URL:
            key = str(next(counter))
            sessions[key] = count_fn(params["term"])
            response.text = _make_esearch_xml(sessions[key], query_key=key)
            return response
        if url == EFETCH_URL:
            total = sessions[params["query_key"]]
            n = max(0, min(int(params["retmax"]), total - int(params["retstart"])))
            response.text = _make_efetch_xml(*([article_xml] * n))
            return response
        raise AssertionError(f"unexpected URL {url}")

    client = MagicMock()
    client.get.side_effect = get
    return client


def _distribution_counter(distribution: dict[date, int]):
    """Return a count_fn over a synthetic EDAT -> record-count distribution."""

    def count_fn(term: str) -> int:
        m = re.search(r'"([\d/]+)"\[EDAT\] : "([\d/]+)"\[EDAT\]', term)
        if m is None:
            return sum(distribution.values())
        lo = datetime.strptime(m.group(1), "%Y/%m/%d").date()
        hi = datetime.strptime(m.group(2), "%Y/%m/%d").date()
        return sum(n for d, n in distribution.items() if lo <= d <= hi)

    return count_fn
```

Then the tests. Both constants are patched small so a ladder is exercised in a handful of requests rather than thousands:

```python
@patch("bmlib.publications.fetchers.pubmed.time.sleep", lambda *_: None)
@patch("bmlib.publications.fetchers.pubmed.EFETCH_PAGE_SIZE", 2)
@patch("bmlib.publications.fetchers.pubmed.EFETCH_MAX_RETRIEVABLE", 2)
class TestAnOverCapDayIsFetchedRatherThanRefused:
    """#105: the day that used to be refused outright is now fetched in parts."""

    def test_every_record_of_an_over_cap_day_arrives(self):
        distribution = {date(2023, 6, 1): 2, date(2023, 6, 2): 2, date(2023, 6, 3): 2}
        client = _eutils_client(_distribution_counter(distribution))
        records = []

        result = fetch_pubmed(
            client, date(2024, 1, 1), on_record=records.append, api_key=None
        )

        assert result.status == "completed"
        assert result.record_count == 6
        assert len(records) == 6

    def test_an_unsplittable_entrez_day_fails_the_day_and_names_it(self):
        distribution = {date(2023, 6, 1): 5}
        client = _eutils_client(_distribution_counter(distribution))

        result = fetch_pubmed(client, date(2024, 1, 1), on_record=lambda r: None)

        assert result.status == "failed"
        assert "2023-06-01" in result.error
        assert "cannot be split further" in result.error

    def test_a_root_that_does_not_cover_fails_the_day(self):
        # The bare day term reports 8; the root EDAT range holds only 4.
        def count_fn(term: str) -> int:
            if "[EDAT]" not in term:
                return 8
            return _distribution_counter({date(2023, 6, 1): 2, date(2023, 6, 2): 2})(term)

        client = _eutils_client(count_fn)

        result = fetch_pubmed(client, date(2024, 1, 1), on_record=lambda r: None)

        assert result.status == "failed"
        assert "lie outside the ladder" in result.error

    def test_a_part_that_grew_past_the_cap_is_split_again(self):
        # Planning sees 2 for 2023-06-02; its own ESearch then reports 4.
        seen: dict[str, int] = {}

        def count_fn(term: str) -> int:
            base = _distribution_counter({date(2023, 6, 1): 2, date(2023, 6, 2): 2})(term)
            if '"2023/06/02"[EDAT] : "2023/06/02"[EDAT]' in term:
                seen[term] = seen.get(term, 0) + 1
                if seen[term] > 1:  # the fetch-time re-check
                    return 4
            return base

        client = _eutils_client(count_fn)

        result = fetch_pubmed(client, date(2024, 1, 1), on_record=lambda r: None)

        # It cannot split a single Entrez day, so it fails loudly rather than
        # walking into the silent clamp — which is the point of the re-check.
        assert result.status == "failed"
        assert "2023-06-02" in result.error

    def test_progress_reports_the_days_total_not_a_parts(self):
        distribution = {date(2023, 6, 1): 2, date(2023, 6, 2): 2}
        client = _eutils_client(_distribution_counter(distribution))
        seen: list[SyncProgress] = []

        fetch_pubmed(
            client,
            date(2024, 1, 1),
            on_record=lambda r: None,
            on_progress=seen.append,
            api_key=None,
        )

        assert seen, "expected at least one progress report"
        assert {p.records_total for p in seen} == {4}
        assert [p.records_processed for p in seen] == sorted(p.records_processed for p in seen)


class TestTheUnderCapPathIsUnchanged:
    """A negative control: an ordinary day must not pay for the ladder."""

    def test_an_ordinary_day_issues_no_partitioning_search(self):
        client = _eutils_client(lambda term: 2)

        fetch_pubmed(client, date(2024, 3, 15), on_record=lambda r: None)

        terms = [
            c.kwargs["params"]["term"]
            for c in client.get.call_args_list
            if "term" in c.kwargs.get("params", {})
        ]
        assert terms == ['("2024/03/15"[Date - Publication])']
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_pubmed_fetcher.py::TestAnOverCapDayIsFetchedRatherThanRefused -v`
Expected: FAIL — the over-cap day is still refused, so `status == "failed"` with the old message.

- [ ] **Step 3: Write minimal implementation**

Replace `fetch_pubmed`'s `if count > EFETCH_MAX_RETRIEVABLE:` block (the whole refusal, comment included) with:

```python
    if count > EFETCH_MAX_RETRIEVABLE:
        # The session opened above is unused on this path — one wasted session
        # per over-cap day, against a partitioned fetch of tens of thousands of
        # records. Not worth a second code path to avoid.
        return _fetch_partitioned(
            client,
            target_date,
            day_term,
            count,
            on_record=on_record,
            on_progress=on_progress,
            api_key=api_key,
            rate_limit=rate_limit,
        )
```

And add, above `fetch_pubmed`:

```python
def _fetch_partitioned(
    client: Any,
    target_date: date,
    day_term: str,
    day_count: int,
    *,
    on_record: Callable[[FetchedRecord], None],
    on_progress: Callable[[SyncProgress], None] | None,
    api_key: str | None,
    rate_limit: float,
    completed_parts: Mapping[str, PartCheckpoint] | None = None,
    on_part_complete: Callable[[PartCheckpoint], None] | None = None,
) -> FetchResult:
    """Fetch a day too large for one history session, as Entrez-date parts.

    A history session serves only its first ``EFETCH_MAX_RETRIEVABLE`` records,
    so a day above that cannot be completed through one. It is split into
    Entrez-date ranges that each fit — disjoint and covering, so every record is
    fetched exactly once — and each part is walked as an ordinary session.

    Every failure path fails the whole day. A day recorded ``completed`` is
    never re-offered, so a part that could not be verified must not be allowed
    to leave the day looking whole.
    """
    date_str = target_date.isoformat()
    checkpoints = dict(completed_parts or {})

    def count_fn(term: str) -> int:
        n, _, _ = _esearch(client, term, api_key, usehistory=False)
        time.sleep(rate_limit)
        return n

    def failed(error: str) -> FetchResult:
        return FetchResult(
            source="pubmed",
            date=date_str,
            record_count=processed,
            status="failed",
            error=error,
        )

    processed = 0
    try:
        parts = _plan_partitions(count_fn, day_term, day_count)
    except (_RootNotCovering, _UnsplittableDay) as exc:
        logger.error("%s (%s)", exc, date_str)
        return failed(str(exc))

    logger.info(
        "PubMed %s holds %d records, above the %d one history session serves:"
        " fetching it as %d Entrez-date parts",
        date_str,
        day_count,
        EFETCH_MAX_RETRIEVABLE,
        len(parts),
    )

    pending = deque(parts)
    delivered = 0
    notes: list[str] = []

    while pending:
        part = pending.popleft()
        term = _edat_range_term(day_term, part.lo, part.hi)

        try:
            part_count, web_env, query_key = _esearch(client, term, api_key)
        except Exception as exc:
            logger.error("esearch failed for %s part %s: %s", date_str, part.key, exc)
            return failed(f"part {part.key}: {exc}")

        if part_count > EFETCH_MAX_RETRIEVABLE:
            # It grew between planning and fetching. Split it again rather than
            # walk it: the last page of an over-cap session is silently clamped,
            # so walking would look like an ordinary short day.
            try:
                pending.extendleft(
                    reversed(
                        _plan_partitions(
                            count_fn,
                            day_term,
                            part_count,
                            lo=part.lo,
                            hi=part.hi,
                            probe_root=False,
                        )
                    )
                )
            except _UnsplittableDay as exc:
                logger.error("%s (%s)", exc, date_str)
                return failed(str(exc))
            continue

        if part_count == 0:
            continue

        if web_env is None or query_key is None:
            message = f"part {part.key} returned count={part_count} without a history session"
            logger.error("%s for %s", message, date_str)
            return failed(message)

        before = processed

        def _report(part_processed: int, _before: int = before) -> None:
            if on_progress is not None:
                total = _before + part_processed
                on_progress(
                    SyncProgress(
                        source="pubmed",
                        date=date_str,
                        records_processed=total,
                        records_total=day_count,
                        status="in_progress",
                        message=f"Fetched {total}/{day_count} records (part {part.key})",
                    )
                )

        outcome = _walk_session(
            client,
            web_env,
            query_key,
            part_count,
            on_record=on_record,
            api_key=api_key,
            rate_limit=rate_limit,
            on_page=_report,
        )
        processed += outcome.processed
        delivered += outcome.delivered

        if outcome.error is not None:
            return failed(f"part {part.key}: {outcome.error}")

        verdict = reconcile_delivery(
            "pubmed",
            f"{date_str} part {part.key}",
            delivered=outcome.delivered,
            promised=part_count,
            stalled=outcome.stalled,
        )
        if verdict.failure is not None:
            return failed(verdict.failure)
        if verdict.note is not None:
            notes.append(verdict.note)

        time.sleep(rate_limit)

    day_verdict = reconcile_delivery(
        "pubmed",
        date_str,
        delivered=delivered,
        promised=day_count,
        stalled=False,
    )
    if day_verdict.failure is not None:
        return failed(day_verdict.failure)
    if day_verdict.note is not None:
        notes.append(day_verdict.note)

    return FetchResult(
        source="pubmed",
        date=date_str,
        record_count=processed,
        status="completed",
        note="; ".join(notes) or None,
    )
```

Add to `pubmed.py`'s imports: `from collections import deque` and `from collections.abc import Callable, Mapping`. `PartCheckpoint` arrives in Task 5 — until then, annotate the two resume parameters as `Mapping[str, Any] | None` and `Callable[[Any], None] | None`, and tighten them in Task 7.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_pubmed_fetcher.py -v`
Expected: PASS. Two pre-existing tests assert the old refusal message and must be **rewritten, not deleted** — they become the over-cap-is-fetched tests above. Search for `refusing the day` and `cannot be reached`.

- [ ] **Step 5: Commit**

```bash
git add bmlib/publications/fetchers/pubmed.py tests/test_pubmed_fetcher.py
git commit -m "feat(publications): fetch an over-cap PubMed day in parts (#105)"
```

---

## Task 5: `download_day_parts` and its checkpoint model

**Files:**
- Modify: `bmlib/publications/schema.py` (both DDL strings), `bmlib/publications/models.py`, `bmlib/publications/sync.py`
- Test: `tests/test_sync.py`, `tests/test_backends.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `PartCheckpoint` dataclass in `models.py` with fields `part_scheme: str`, `part_key: str`, `promised: int`, `record_count: int` plus `to_dict()` / `from_dict()`; and in `sync.py`: `_load_day_parts(conn, source: str, day: date) -> dict[str, PartCheckpoint]`, `_record_day_part(conn, source: str, day: date, checkpoint: PartCheckpoint) -> None`, `_clear_day_parts(conn, source: str, day: date) -> None`.

- [ ] **Step 1: Write the failing test**

In `tests/test_sync.py`:

```python
class TestDayPartCheckpoints:
    """Checkpoints are what makes a very large day resumable."""

    def test_a_checkpoint_round_trips(self):
        conn = connect_sqlite(":memory:")
        ensure_schema(conn)
        cp = PartCheckpoint(
            part_scheme="edat-range",
            part_key="edat:2023-04-10:2023-08-31",
            promised=9375,
            record_count=9375,
        )

        _record_day_part(conn, "pubmed", date(2024, 1, 1), cp)

        assert _load_day_parts(conn, "pubmed", date(2024, 1, 1)) == {cp.part_key: cp}

    def test_recording_the_same_part_twice_updates_rather_than_duplicates(self):
        conn = connect_sqlite(":memory:")
        ensure_schema(conn)
        key = "edat:2023-04-10:2023-08-31"
        _record_day_part(conn, "pubmed", date(2024, 1, 1), PartCheckpoint("edat-range", key, 10, 10))
        _record_day_part(conn, "pubmed", date(2024, 1, 1), PartCheckpoint("edat-range", key, 12, 12))

        stored = _load_day_parts(conn, "pubmed", date(2024, 1, 1))

        assert len(stored) == 1
        assert stored[key].promised == 12

    def test_parts_are_scoped_to_their_source_and_day(self):
        conn = connect_sqlite(":memory:")
        ensure_schema(conn)
        cp = PartCheckpoint("edat-range", "edat:2023-04-10:2023-08-31", 1, 1)
        _record_day_part(conn, "pubmed", date(2024, 1, 1), cp)

        assert _load_day_parts(conn, "pubmed", date(2024, 1, 2)) == {}
        assert _load_day_parts(conn, "biorxiv", date(2024, 1, 1)) == {}

    def test_clearing_removes_only_that_day(self):
        conn = connect_sqlite(":memory:")
        ensure_schema(conn)
        cp = PartCheckpoint("edat-range", "edat:2023-04-10:2023-08-31", 1, 1)
        _record_day_part(conn, "pubmed", date(2024, 1, 1), cp)
        _record_day_part(conn, "pubmed", date(2024, 1, 2), cp)

        _clear_day_parts(conn, "pubmed", date(2024, 1, 1))

        assert _load_day_parts(conn, "pubmed", date(2024, 1, 1)) == {}
        assert _load_day_parts(conn, "pubmed", date(2024, 1, 2)) == {cp.part_key: cp}
```

And in `tests/test_backends.py`, following that file's `backend_conn` fixture pattern so it runs on SQLite **and** PostgreSQL:

```python
    def test_day_part_checkpoints_round_trip(self, backend_conn):
        ensure_schema(backend_conn)
        cp = PartCheckpoint("edat-range", "edat:2023-04-10:2023-08-31", 9375, 9375)

        _record_day_part(backend_conn, "pubmed", date(2024, 1, 1), cp)
        _record_day_part(backend_conn, "pubmed", date(2024, 1, 1), cp)  # idempotent

        assert _load_day_parts(backend_conn, "pubmed", date(2024, 1, 1)) == {cp.part_key: cp}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_sync.py::TestDayPartCheckpoints -v`
Expected: FAIL — `cannot import name 'PartCheckpoint'`

- [ ] **Step 3: Write minimal implementation**

In `models.py`, beside `SourceDescriptor`:

```python
@dataclass
class PartCheckpoint:
    """One completed partition of a day, so a re-run can skip it.

    A day too large for one history session is fetched as parts (see
    ``fetchers/pubmed.py``). Each part's records and its checkpoint are written
    in one transaction, so a checkpoint never attests to records a rollback
    discarded.

    ``part_key`` is opaque to storage: the partitioning scheme belongs to the
    fetcher, so a second scheme needs no schema change. ``part_scheme`` names
    which scheme wrote the key, so a scheme that changes is visible in the data
    rather than silently matching nothing.
    """

    part_scheme: str
    part_key: str
    promised: int
    record_count: int

    def to_dict(self) -> dict[str, Any]:
        """Return a plain-dict form of this checkpoint."""
        return {
            "part_scheme": self.part_scheme,
            "part_key": self.part_key,
            "promised": self.promised,
            "record_count": self.record_count,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PartCheckpoint:
        """Build a checkpoint from its plain-dict form."""
        return cls(
            part_scheme=str(data["part_scheme"]),
            part_key=str(data["part_key"]),
            promised=int(data["promised"]),
            record_count=int(data["record_count"]),
        )
```

In `schema.py`, add to **both** `SQLITE_SCHEMA` and `POSTGRESQL_SCHEMA`, immediately after `download_days` (`AUTOINCREMENT` in the SQLite copy, `SERIAL` in the PostgreSQL one):

```sql
CREATE TABLE IF NOT EXISTS download_day_parts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL,
    date            TEXT NOT NULL,
    part_scheme     TEXT NOT NULL,
    part_key        TEXT NOT NULL,
    promised        INTEGER NOT NULL,
    record_count    INTEGER NOT NULL,
    completed_at    TEXT NOT NULL,
    UNIQUE(source, date, part_key)
);
```

In `sync.py`, beside `_upsert_download_day`:

```python
def _load_day_parts(conn: Any, source: str, day: date) -> dict[str, PartCheckpoint]:
    """Return the parts of *day* a previous run finished, keyed by part key."""
    ph = placeholder(conn)
    rows = fetch_all(
        conn,
        "SELECT part_scheme, part_key, promised, record_count FROM download_day_parts"
        f" WHERE source = {ph} AND date = {ph}",
        (source, day.isoformat()),
    )
    parts = {}
    for row in rows:
        cp = PartCheckpoint.from_dict(row)
        parts[cp.part_key] = cp
    return parts


def _record_day_part(conn: Any, source: str, day: date, checkpoint: PartCheckpoint) -> None:
    """Record one completed part.

    Runs inside the caller's transaction (see :func:`sync`), so the checkpoint
    commits atomically with the records it attests to — a checkpoint that
    outlived a rolled-back batch would make a re-run skip records that were
    never stored.
    """
    ph = placeholder(conn)
    with transaction(conn):
        execute(
            conn,
            "INSERT INTO download_day_parts (source, date, part_scheme, part_key,"
            f" promised, record_count, completed_at) VALUES ({', '.join([ph] * 7)})"
            " ON CONFLICT (source, date, part_key) DO UPDATE SET"
            "   part_scheme = excluded.part_scheme,"
            "   promised = excluded.promised,"
            "   record_count = excluded.record_count,"
            "   completed_at = excluded.completed_at",
            (
                source,
                day.isoformat(),
                checkpoint.part_scheme,
                checkpoint.part_key,
                checkpoint.promised,
                checkpoint.record_count,
                datetime.now(tz=UTC).isoformat(),
            ),
        )


def _clear_day_parts(conn: Any, source: str, day: date) -> None:
    """Drop *day*'s part rows.

    Called when the day completes: the rows describe an unfinished day, so
    keeping them would grow the table without bound and would make a
    ``recheck_days`` re-fetch skip parts it was explicitly asked to redo.
    """
    ph = placeholder(conn)
    with transaction(conn):
        execute(
            conn,
            f"DELETE FROM download_day_parts WHERE source = {ph} AND date = {ph}",
            (source, day.isoformat()),
        )
```

Export `PartCheckpoint` from `bmlib/publications/__init__.py`'s `__all__`.

Note: `fetch_all` returns rows that behave as mappings on both backends (see `db/operations.py`); `PartCheckpoint.from_dict` takes a `Mapping`, so no per-backend branch is needed.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_sync.py::TestDayPartCheckpoints tests/test_backends.py -v`
Expected: PASS on SQLite; PASS on PostgreSQL too when `BMLIB_TEST_POSTGRESQL_DSN` is set (see HANDOVER for the two-minute local recipe — run it, this task adds SQL).

- [ ] **Step 5: Commit**

```bash
git add bmlib/publications/schema.py bmlib/publications/models.py bmlib/publications/sync.py bmlib/publications/__init__.py tests/test_sync.py tests/test_backends.py
git commit -m "feat(publications): store per-part checkpoints for a partitioned day (#105)"
```

---

## Task 6: A fetcher declares itself resumable

**Files:**
- Modify: `bmlib/publications/models.py` (`SourceDescriptor`), `bmlib/publications/fetchers/registry.py`
- Test: `tests/test_registry.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `SourceDescriptor.resumable: bool = False`.

`register_source()` is public, so `sync()` must keep calling an existing third-party fetcher with exactly today's keywords. A new keyword passed to a fetcher that does not accept it raises `TypeError`, and the handler around the call would turn a working source into a failed day — silently, once per day, forever.

- [ ] **Step 1: Write the failing test**

```python
class TestAFetcherDeclaresResumability:
    """New keywords reach only fetchers that asked for them."""

    def test_a_descriptor_is_not_resumable_by_default(self):
        descriptor = SourceDescriptor(
            name="custom", display_name="Custom", description="A third-party source"
        )

        assert descriptor.resumable is False

    def test_pubmed_declares_itself_resumable(self):
        descriptor, _ = get_source("pubmed")

        assert descriptor.resumable is True

    def test_the_other_builtins_do_not(self):
        for name in ("biorxiv", "medrxiv", "openalex"):
            descriptor, _ = get_source(name)
            assert descriptor.resumable is False, name
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_registry.py::TestAFetcherDeclaresResumability -v`
Expected: FAIL — `AttributeError: 'SourceDescriptor' object has no attribute 'resumable'`

- [ ] **Step 3: Write minimal implementation**

In `models.py`, append the field **last**, after `params`, so existing positional construction is unaffected:

```python
@dataclass
class SourceDescriptor:
    """Metadata describing a registered publication source."""

    name: str
    display_name: str
    description: str
    params: list[SourceParam] = field(default_factory=list)
    resumable: bool = False
    """Whether ``sync()`` may pass this fetcher the per-part resume keywords.

    Defaults to ``False`` because :func:`register_source` is public: a fetcher
    written against an earlier bmlib does not accept them, and passing an
    unexpected keyword would raise inside the per-day handler and record a
    working source's day as failed.
    """
```

In `registry.py`, add `resumable=True` to the PubMed `SourceDescriptor` only.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_registry.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bmlib/publications/models.py bmlib/publications/fetchers/registry.py tests/test_registry.py
git commit -m "feat(publications): let a source declare that it can resume mid-day"
```

---

## Task 7: The skip rule, and crediting what it skips

**Files:**
- Modify: `bmlib/publications/fetchers/pubmed.py` (`fetch_pubmed`, `_fetch_partitioned`)
- Test: `tests/test_pubmed_fetcher.py`

**Interfaces:**
- Consumes: Tasks 4, 5.
- Produces: `fetch_pubmed(..., completed_parts: Mapping[str, PartCheckpoint] | None = None, on_part_complete: Callable[[PartCheckpoint], None] | None = None)`; the two are threaded straight through to `_fetch_partitioned`, whose annotations tighten from `Any`.

Two rules here, and each breaks something real if it is skipped. Skipping on key alone loses every record a part gained since it was checkpointed. Failing to credit a skipped part makes the day-total reconciliation — shipped in Task 4 — fail every resumed day.

- [ ] **Step 1: Write the failing test**

```python
@patch("bmlib.publications.fetchers.pubmed.time.sleep", lambda *_: None)
@patch("bmlib.publications.fetchers.pubmed.EFETCH_PAGE_SIZE", 2)
@patch("bmlib.publications.fetchers.pubmed.EFETCH_MAX_RETRIEVABLE", 2)
class TestResumingAnOverCapDay:
    """A part finished by an earlier run is not fetched twice."""

    DISTRIBUTION = {date(2023, 6, 1): 2, date(2023, 6, 2): 2}

    def test_a_completed_part_is_reported_and_can_be_skipped(self):
        client = _eutils_client(_distribution_counter(self.DISTRIBUTION))
        done: list[PartCheckpoint] = []

        result = fetch_pubmed(
            client,
            date(2024, 1, 1),
            on_record=lambda r: None,
            on_part_complete=done.append,
        )

        assert result.status == "completed"
        assert {c.part_key for c in done} == {
            "edat:2023-06-01:2023-06-01",
            "edat:2023-06-02:2023-06-02",
        }
        assert all(c.part_scheme == "edat-range" for c in done)
        assert all(c.promised == 2 and c.record_count == 2 for c in done)

    def test_a_checkpointed_part_is_not_fetched_again(self):
        client = _eutils_client(_distribution_counter(self.DISTRIBUTION))
        prior = {
            "edat:2023-06-01:2023-06-01": PartCheckpoint(
                "edat-range", "edat:2023-06-01:2023-06-01", 2, 2
            )
        }
        records = []

        result = fetch_pubmed(
            client,
            date(2024, 1, 1),
            on_record=records.append,
            completed_parts=prior,
        )

        assert result.status == "completed"
        assert len(records) == 2, "only the outstanding part should be fetched"
        efetch_calls = [c for c in client.get.call_args_list if c.args[0] == EFETCH_URL]
        assert len(efetch_calls) == 1

    def test_a_skipped_part_is_credited_to_the_day_total(self):
        # Without crediting, the day delivers 2 of 4 and fails the floor. This
        # test is the negative control for that: it must complete.
        client = _eutils_client(_distribution_counter(self.DISTRIBUTION))
        prior = {
            "edat:2023-06-01:2023-06-01": PartCheckpoint(
                "edat-range", "edat:2023-06-01:2023-06-01", 2, 2
            )
        }

        result = fetch_pubmed(
            client, date(2024, 1, 1), on_record=lambda r: None, completed_parts=prior
        )

        assert result.status == "completed"
        assert result.error is None

    def test_a_part_whose_count_moved_is_fetched_again(self):
        # The stored promise is 1; the part now holds 2. Skipping on key alone
        # would lose that record permanently, which is the whole reason the
        # rule compares counts.
        client = _eutils_client(_distribution_counter(self.DISTRIBUTION))
        prior = {
            "edat:2023-06-01:2023-06-01": PartCheckpoint(
                "edat-range", "edat:2023-06-01:2023-06-01", 1, 1
            )
        }
        records = []

        result = fetch_pubmed(
            client,
            date(2024, 1, 1),
            on_record=records.append,
            completed_parts=prior,
        )

        assert result.status == "completed"
        assert len(records) == 4, "the moved part must be re-fetched, not skipped"

    def test_an_under_cap_day_ignores_the_resume_arguments(self):
        client = _eutils_client(lambda term: 2)
        done: list[PartCheckpoint] = []

        result = fetch_pubmed(
            client, date(2024, 3, 15), on_record=lambda r: None, on_part_complete=done.append
        )

        assert result.status == "completed"
        assert done == [], "a day that needs no partitioning has no parts to checkpoint"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_pubmed_fetcher.py::TestResumingAnOverCapDay -v`
Expected: FAIL — `fetch_pubmed() got an unexpected keyword argument 'on_part_complete'`

- [ ] **Step 3: Write minimal implementation**

Add the two keyword-only parameters to `fetch_pubmed`, documented, and pass them through to `_fetch_partitioned`:

```python
    completed_parts: Mapping[str, PartCheckpoint] | None = None,
    on_part_complete: Callable[[PartCheckpoint], None] | None = None,
```

```
    completed_parts:
        Parts of this day a previous run finished, keyed by part key. Only
        consulted for a day large enough to be partitioned. A part is skipped
        only if its stored ``promised`` still matches what the source reports
        now.
    on_part_complete:
        Called with a :class:`PartCheckpoint` after each part's walk has
        reconciled. The caller is expected to store the part's records and this
        checkpoint in one transaction.
```

In `_fetch_partitioned`, tighten the two annotations to `Mapping[str, PartCheckpoint] | None` and `Callable[[PartCheckpoint], None] | None`, then insert the skip immediately after `part = pending.popleft()`:

```python
        prior = checkpoints.get(part.key)
        if prior is not None and prior.promised == part.promised:
            # Counted as delivered because a previous run delivered it: the
            # checkpoint is written only after that part reconciled. Without
            # this credit the day-total reconciliation below would fail every
            # resumed day.
            delivered += prior.promised
            logger.debug(
                "PubMed %s part %s already complete (%d records); skipping",
                date_str,
                part.key,
                prior.record_count,
            )
            continue
```

and, immediately after the part's `verdict` is found clean, before the `time.sleep`:

```python
        if on_part_complete is not None:
            on_part_complete(
                PartCheckpoint(
                    part_scheme=PART_SCHEME,
                    part_key=part.key,
                    promised=part_count,
                    record_count=outcome.processed,
                )
            )
```

Import `PartCheckpoint` from `bmlib.publications.models` at the top of `pubmed.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_pubmed_fetcher.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bmlib/publications/fetchers/pubmed.py tests/test_pubmed_fetcher.py
git commit -m "feat(publications): skip a part an earlier run finished, and credit it (#105)"
```

---

## Task 8: `sync()` flushes and checkpoints per part

**Files:**
- Modify: `bmlib/publications/sync.py:845-955` (the per-day loop)
- Test: `tests/test_sync.py`

**Interfaces:**
- Consumes: Tasks 5, 6, 7.
- Produces: `_source_is_resumable(source: str) -> bool`.

This is where the memory problem the existing buffer comment predicted actually gets fixed: `day_records` is flushed per part instead of per day, so peak memory is one part rather than 242,216 records.

- [ ] **Step 1: Write the failing test**

```python
class TestSyncResumesAPartitionedDay:
    """The flush boundary and the checkpoint boundary are the same boundary."""

    @staticmethod
    def _fetcher(parts, *, fail_after=None):
        """A fake resumable fetcher emitting *parts* as (key, [record, ...])."""

        def fetch(client, day, *, on_record, on_progress=None, completed_parts=None,
                  on_part_complete=None, **config):
            completed_parts = completed_parts or {}
            emitted = 0
            for index, (key, records) in enumerate(parts):
                if key in completed_parts:
                    continue
                for record in records:
                    on_record(record)
                    emitted += 1
                if fail_after is not None and index == fail_after:
                    return FetchResult(
                        source="pubmed", date=day.isoformat(), record_count=emitted,
                        status="failed", error="part exploded",
                    )
                if on_part_complete is not None:
                    on_part_complete(
                        PartCheckpoint("edat-range", key, len(records), len(records))
                    )
            return FetchResult(
                source="pubmed", date=day.isoformat(), record_count=emitted, status="completed"
            )

        return fetch

    def test_a_failed_day_keeps_the_parts_that_finished(self):
        conn = connect_sqlite(":memory:")
        ensure_schema(conn)
        parts = [("edat:a:a", [_record("1")]), ("edat:b:b", [_record("2")])]

        sync(
            conn,
            date_from=date(2024, 1, 1),
            date_to=date(2024, 1, 1),
            sources=["pubmed"],
            email="t@example.com",
            _fetcher_override={"pubmed": self._fetcher(parts, fail_after=1)},
        )

        stored = _load_day_parts(conn, "pubmed", date(2024, 1, 1))
        assert set(stored) == {"edat:a:a"}, "the finished part survives the failed day"

    def test_a_records_and_its_checkpoint_commit_together(self):
        conn = connect_sqlite(":memory:")
        ensure_schema(conn)
        parts = [("edat:a:a", [_record("1")])]

        sync(
            conn,
            date_from=date(2024, 1, 1),
            date_to=date(2024, 1, 1),
            sources=["pubmed"],
            email="t@example.com",
            _fetcher_override={"pubmed": self._fetcher(parts)},
        )

        assert fetch_scalar(conn, "SELECT COUNT(*) FROM publications") == 1

    def test_a_completed_day_drops_its_part_rows(self):
        conn = connect_sqlite(":memory:")
        ensure_schema(conn)
        parts = [("edat:a:a", [_record("1")]), ("edat:b:b", [_record("2")])]

        sync(
            conn,
            date_from=date(2024, 1, 1),
            date_to=date(2024, 1, 1),
            sources=["pubmed"],
            email="t@example.com",
            _fetcher_override={"pubmed": self._fetcher(parts)},
        )

        assert _load_day_parts(conn, "pubmed", date(2024, 1, 1)) == {}

    def test_the_day_record_count_covers_parts_an_earlier_run_stored(self):
        conn = connect_sqlite(":memory:")
        ensure_schema(conn)
        _record_day_part(
            conn, "pubmed", date(2024, 1, 1), PartCheckpoint("edat-range", "edat:a:a", 1, 1)
        )
        parts = [("edat:a:a", [_record("1")]), ("edat:b:b", [_record("2")])]

        sync(
            conn,
            date_from=date(2024, 1, 1),
            date_to=date(2024, 1, 1),
            sources=["pubmed"],
            email="t@example.com",
            _fetcher_override={"pubmed": self._fetcher(parts)},
        )

        row = fetch_one(
            conn, "SELECT record_count FROM download_days WHERE source = ? AND date = ?",
            ("pubmed", "2024-01-01"),
        )
        assert row["record_count"] == 2, "the whole day, not this run's share"

    def test_a_non_resumable_fetcher_is_called_with_todays_keywords_only(self):
        conn = connect_sqlite(":memory:")
        ensure_schema(conn)
        seen: dict[str, object] = {}

        # No **kwargs: an extra keyword raises, which is exactly the third-party
        # fetcher this guard protects.
        def strict(client, day, *, on_record, on_progress=None, **config):
            seen["config"] = config
            return FetchResult(
                source="biorxiv", date=day.isoformat(), record_count=0, status="completed"
            )

        sync(
            conn,
            date_from=date(2024, 1, 1),
            date_to=date(2024, 1, 1),
            sources=["biorxiv"],
            email="t@example.com",
            _fetcher_override={"biorxiv": strict},
        )

        assert "completed_parts" not in seen["config"]
        assert "on_part_complete" not in seen["config"]
```

Add a `_record(pmid)` helper to the test module if one does not already exist, returning a minimal `FetchedRecord` with that PMID and `source="pubmed"`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_sync.py::TestSyncResumesAPartitionedDay -v`
Expected: FAIL — `fetch() got an unexpected keyword argument` is not raised, but no part rows are stored, so the first test fails on an empty `stored`.

- [ ] **Step 3: Write minimal implementation**

Add beside `_get_fetcher_for_source`:

```python
def _source_is_resumable(source: str) -> bool:
    """Whether *source*'s descriptor declares it accepts the resume keywords."""
    try:
        descriptor, _ = get_source(source)
    except ValueError:
        return False
    return bool(descriptor.resumable)
```

Import `get_source` alongside `get_fetcher`.

In the per-day loop, before the `try:` around the fetcher call:

```python
                resumable = _source_is_resumable(source)
                prior_parts = (
                    _load_day_parts(conn, source, day) if resumable else {}
                )
                finished_keys: set[str] = set()

                def flush_part(checkpoint: PartCheckpoint) -> None:
                    """Store this part's records and its checkpoint atomically.

                    One transaction, so a checkpoint can never attest to records
                    a rollback discarded — and the buffer is emptied per part
                    rather than per day, which is what keeps a 242,000-record
                    day out of memory.
                    """
                    nonlocal day_added, day_merged, day_failed
                    with transaction(conn):
                        added, merged, failed_ = _store_records(conn, source, day, day_records)
                        _record_day_part(conn, source, day, checkpoint)
                    day_added += added
                    day_merged += merged
                    day_failed += failed_
                    finished_keys.add(checkpoint.part_key)
                    day_records.clear()

                if resumable:
                    src_config = {
                        **src_config,
                        "completed_parts": prior_parts,
                        "on_part_complete": flush_part,
                    }
```

`day_added`, `day_merged` and `day_failed` must be declared before `flush_part`; they already are, at the top of the day loop.

Extract the existing record-storing loop out of the day transaction into a helper so both call sites share it — the body is unchanged, only its home moves:

```python
def _store_records(
    conn: Any, source: str, day: date, records: Sequence[FetchedRecord]
) -> tuple[int, int, int]:
    """Store *records*, returning (added, merged, failed).

    Runs inside the caller's transaction, so a record that fails rolls back to
    its own savepoint without losing the batch.
    """
    added = merged = failed = 0
    for record in records:
        try:
            pub = _record_to_publication(record)
            fts = _record_to_fulltext_sources(record)
            result = store_publication(
                conn,
                pub,
                fulltext_sources=fts,
                grants=_stamp_source(record.grants, record.source),
                affiliations=_stamp_source(record.author_affiliations, record.source),
            )
            if result == "added":
                added += 1
            elif result == "merged":
                merged += 1
        except Exception as exc:
            # Broad on purpose — one bad record must not lose the batch — so
            # the exception *type* is logged too: a TypeError here is a bmlib
            # defect affecting every record, not bad data from the source, and
            # the two read identically without it.
            failed += 1
            logger.error(
                "Failed to store record from %s/%s: %s: %s",
                source,
                day.isoformat(),
                type(exc).__name__,
                exc,
            )
    return added, merged, failed
```

Then the final per-day transaction becomes:

```python
                with transaction(conn):
                    added, merged, failed_ = _store_records(conn, source, day, day_records)
                    day_added += added
                    day_merged += merged
                    day_failed += failed_

                    outcome = _resolve_day_status(source, day, fetch_result, day_failed)
                    # Credit parts an earlier run stored, so a day fetched
                    # across three runs is not recorded as holding only the
                    # last run's share.
                    carried = sum(
                        cp.record_count
                        for key, cp in prior_parts.items()
                        if key not in finished_keys
                    )
                    record_count = day_added + day_merged + carried

                    _upsert_download_day(conn, source, day, outcome.status, record_count)
                    if outcome.status == "completed":
                        _clear_day_parts(conn, source, day)
```

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest tests/test_sync.py tests/test_backends.py -v`
Expected: PASS. Watch for pre-existing tests that assert `store_publication` is called exactly once per day — the flush is now per part.

- [ ] **Step 5: Commit**

```bash
git add bmlib/publications/sync.py tests/test_sync.py
git commit -m "feat(publications): flush and checkpoint a partitioned day per part (#105)"
```

---

## Task 9: `--partition` mode in the live sampler

**Files:**
- Modify: `scripts/sample_efetch_paging.py`
- Test: `tests/test_efetch_paging_sampler.py`

**Interfaces:**
- Consumes: nothing from earlier tasks — the sampler deliberately does **not** import the ladder it measures.
- Produces: `measure_partition(day: date, base: dict[str, str]) -> LadderReport`, a `LadderReport` dataclass with `day: date`, `day_count: int | None`, `root_count: int | None`, `parts: int`, `stuck: list[tuple[date, int]]`, `depth: int`, `calls: int`, `exact: bool | None`; and `report_partitions(days, base) -> bool` returning whether every population was reportable.

The `0 stuck` claim in the spec is the one thing here a future PubMed could falsify, and this is what would notice. Follow the sibling samplers' conventions exactly: a probe that could not be made never prints as a finding, a population past `UNMEASURED_SHARE_ERROR_THRESHOLD` reports ERROR rather than a share, throttled requests retry through `_sampling`'s two-ended `Retry-After` clamp, and the process exits non-zero when anything came back unreportable.

**It must not import `_plan_partitions`.** A corpus labelled by the rule under test can only confirm that rule — the same reason `sample_pdf_metadata_titles.py` carries its own comparison rather than importing `_titles.normalise`. Carry an independent descent here, and do not let a later refactor "deduplicate" the two.

- [ ] **Step 1: Write the failing test**

```python
class TestThePartitionMode:
    """A probe that could not be made must never print as a finding."""

    def test_a_failed_count_is_unmeasured_not_a_finding(self, monkeypatch):
        monkeypatch.setattr(sampler, "_count", lambda term, base: None)

        report = sampler.measure_partition(date(2024, 1, 1), {})

        assert report.day_count is None
        assert report.parts == 0
        assert report.exact is None

    def test_the_ladder_tiles_and_reports_exact(self, monkeypatch):
        distribution = {date(2023, 6, 1) + timedelta(days=i): 4000 for i in range(10)}
        monkeypatch.setattr(sampler, "_count", _stub_count(distribution))

        report = sampler.measure_partition(date(2024, 1, 1), {})

        assert report.exact is True
        assert report.stuck == []
        assert sum(1 for _ in range(report.parts)) == report.parts
        assert report.parts > 1

    def test_a_single_entrez_day_over_the_cap_is_reported_stuck(self, monkeypatch):
        monkeypatch.setattr(sampler, "_count", _stub_count({date(2023, 6, 1): 25000}))

        report = sampler.measure_partition(date(2024, 1, 1), {})

        assert report.stuck == [(date(2023, 6, 1), 25000)]

    def test_a_run_with_an_unreportable_population_exits_non_zero(self, monkeypatch):
        monkeypatch.setattr(sampler, "_count", lambda term, base: None)

        assert sampler.report_partitions([date(2024, 1, 1)], {}) is False

    def test_the_sampler_does_not_import_the_rule_it_measures(self):
        # A corpus labelled by the rule under test can only confirm that rule.
        source = pathlib.Path(sampler.__file__).read_text()
        assert "_plan_partitions" not in source
        assert "_edat_range_term" not in source
```

`_stub_count(distribution)` is the same synthetic counter used in `tests/test_pubmed_fetcher.py`, written out again here rather than imported — the two test modules must be able to disagree.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_efetch_paging_sampler.py::TestThePartitionMode -v`
Expected: FAIL — `module 'sample_efetch_paging' has no attribute 'measure_partition'`

- [ ] **Step 3: Write minimal implementation**

Add to `scripts/sample_efetch_paging.py`, following the file's existing `Probe`/`report_*` shape:

```python
# The ladder's root, restated rather than imported: this script is the evidence
# for the rule in `fetchers/pubmed.py`, and evidence gathered with the rule
# under test can only ever agree with it.
LADDER_ROOT_LO = date(1900, 1, 1)
LADDER_ROOT_HI = date(2100, 12, 31)


@dataclass
class LadderReport:
    """What one day's Entrez-date ladder looked like."""

    day: date
    day_count: int | None = None
    root_count: int | None = None
    parts: int = 0
    stuck: list[tuple[date, int]] = field(default_factory=list)
    depth: int = 0
    calls: int = 0
    exact: bool | None = None

    @property
    def measured(self) -> bool:
        """Whether every probe this report needed actually came back."""
        return self.day_count is not None and self.root_count is not None


def _range_term(day: date, lo: date, hi: date) -> str:
    """Build the term for one rung, independently of the library's builder."""
    return (
        f'("{day:%Y/%m/%d}"[Date - Publication])'
        f' AND ("{lo:%Y/%m/%d}"[EDAT] : "{hi:%Y/%m/%d}"[EDAT])'
    )


def measure_partition(day: date, base: dict[str, str]) -> LadderReport:
    """Walk *day*'s Entrez-date ladder and report its shape.

    Every count that could not be made leaves the report unmeasured rather than
    contributing a zero: a zero is what an empty range looks like, and a failed
    request read as one would print a ladder that tiles when it does not.
    """
    report = LadderReport(day=day)
    day_count = _count(f'("{day:%Y/%m/%d}"[Date - Publication])', base)
    if day_count is None:
        return report
    report.day_count = day_count
    report.calls += 1

    root_count = _count(_range_term(day, LADDER_ROOT_LO, LADDER_ROOT_HI), base)
    if root_count is None:
        return report
    report.root_count = root_count
    report.calls += 1

    total = 0
    unmeasured = False

    def descend(lo: date, hi: date, n: int, depth: int) -> None:
        nonlocal total, unmeasured
        report.depth = max(report.depth, depth)
        if n <= 0:
            return
        if n <= EFETCH_MAX_RETRIEVABLE:
            report.parts += 1
            total += n
            return
        if lo == hi:
            report.stuck.append((lo, n))
            total += n
            return
        mid = lo + (hi - lo) // 2
        left = _count(_range_term(day, lo, mid), base)
        if left is None:
            unmeasured = True
            return
        report.calls += 1
        descend(lo, mid, left, depth + 1)
        descend(mid + timedelta(days=1), hi, n - left, depth + 1)

    descend(LADDER_ROOT_LO, LADDER_ROOT_HI, root_count, 0)
    if unmeasured:
        report.root_count = None  # the ladder is incomplete; do not report it
        return report
    report.exact = total == root_count
    return report
```

`report_partitions(days, base)` prints one row per day — count, root agreement, parts, depth, calls, `EXACT`/`MISMATCH`, and any stuck Entrez date — computes the unmeasured share across the population, prints `ERROR` instead of a share when it exceeds `UNMEASURED_SHARE_ERROR_THRESHOLD`, and returns `False` if any population was unreportable or any day was inexact or stuck. Wire `--partition` and `--partition-days N` into `main()` alongside `--skip-day-sizes`, and fold its return into `main()`'s exit status the way the existing probes are.

Note the request cost in `--help`: a full ladder is 40-51 ESearches per day, so `--partition --partition-days 3` is ~135 requests (measured: 51 + 40 + 44 over its three default targets).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_efetch_paging_sampler.py -v`
Expected: PASS

- [ ] **Step 5: Run it live, once, and record what it says**

Run: `uv run python scripts/sample_efetch_paging.py --partition --partition-days 3`
Expected: exit 0, every day `EXACT`, no stuck rows. Paste the table into the DECISIONS entry in Task 10. If it disagrees with the spec's measured table, **stop and report** — that is the sampler doing its job, and the design's "0 stuck" claim would need revisiting.

- [ ] **Step 6: Commit**

```bash
git add scripts/sample_efetch_paging.py tests/test_efetch_paging_sampler.py
git commit -m "feat(scripts): measure a day's Entrez-date ladder live (#105)"
```

---

## Task 10: Documentation, and closing the issues

**Files:**
- Modify: `CHANGELOG.md`, `docs/DECISIONS.md`, `docs/manual/publications.md`, `CLAUDE.md`, `HANDOVER.md`, `ROADMAP.md`
- Test: none (documentation), but the full gate must pass.

**Interfaces:**
- Consumes: everything above, plus Task 9's live run.

- [ ] **Step 1: CHANGELOG**

Under `## [Unreleased]` → `### Fixed`, above the #105 containment entry that is already there. The containment entry stays — it describes a release that shipped — but gains a closing sentence pointing here. The new entry must carry **the data answer, not just the API one**:

- what changes (an over-cap day is fetched, not refused);
- the mechanism in one sentence (Entrez-date ranges, halved, disjoint and covering);
- **the cost**: ~562 requests and ~1 GB for a 242,000-record day; a six-year backfill window holds ~72 such days, ~6.2M records, ~25 GB, **once** — where the previous behaviour re-offered them forever and stored nothing;
- that a partitioned day is resumable, so an interrupted run does not repeat it;
- the new `download_day_parts` table, created by `ensure_schema()` with no migration;
- `SourceDescriptor.resumable` as a new public field, default `False`.

- [ ] **Step 2: `docs/DECISIONS.md`**

Extend the existing `## publications — how far a PubMed history session can be walked (#96, #105)` section. Add:

- **Why Entrez-date ranges and not a facet** — disjointness and coverage have to be structural; a co-occurring facet double-fetches and inflates delivery past the day's own count, which is what would hide a shortfall.
- **The measured ladder** — the spec's table, plus Task 9's live re-run.
- **Why the FTP baseline is not the route** — reaching one day's 242,216 records means reading ~37M and discarding 99.3%; it only wins for a whole-corpus load, which is a different question.
- **Why the root probe tolerates long but not short**, and why a removal between the two counts is accepted as a (recoverable) failure rather than given a tolerance band.
- **Why `part_key` is opaque**, what that costs (a silent format drift re-fetches every unfinished day with nothing raised), and the two things that answer it.

Update the containment bullets: the "until #105 lands, those days have no records at all" sentence is now historical and must say so.

- [ ] **Step 3: `docs/manual/publications.md`**

Document, each marked `unreleased` (bare — never with a guessed version number):

- that a day over the cap is now fetched in parts, with the cost stated;
- `download_day_parts` and what an operator sees in it (rows only while a day is unfinished);
- `SourceDescriptor.resumable` for anyone writing a fetcher via `register_source()`;
- that an unsplittable Entrez day still fails, and what its error looks like.

- [ ] **Step 4: `CLAUDE.md`**

The `publications/` module description and the "A completed day is a durable claim" section both state that an over-cap day is refused. Update both, and add `download_day_parts` to the `publications/schema.py` line in the directory tree.

- [ ] **Step 5: Close #107**

`SyncReport.errors` saturated because the refusal was permanent and re-offered forever. It no longer is.

```bash
gh issue close 107 --comment "Closed by #105's fix rather than on its own terms: ..."
```

State in the comment what remains — an unsplittable Entrez day is still a permanent, re-offered failure, but it is not a *structural* population the way month firsts were (0 of 3 measured days), so `errors` returns to empty in the ordinary case. If a later measurement finds stuck days are common, #107's `blocked` field is the right answer and the issue should be reopened.

- [ ] **Step 6: HANDOVER.md and ROADMAP.md**

Per the session workflow: prune both, keep under 500 lines, focus on what still needs doing. Move #105 from "Next up" to done; update the open-issue count; update the test counts; add the ROADMAP row.

- [ ] **Step 7: Run the full gate**

```bash
uv run pytest tests/ -v
uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .
uv run mypy
```

Then with PostgreSQL, since this PR adds SQL:

```bash
BMLIB_TEST_POSTGRESQL_DSN="host=/tmp/bmlpg/run port=55432 dbname=bmlib_test user=postgres" \
    uv run pytest tests/test_backends.py -v
```

Expected: all green, both backends.

- [ ] **Step 8: Commit and open the PR**

```bash
git add -A
git commit -m "docs: record the partition design, its cost, and what it closes (#105)"
git push -u origin fix/105-partition-over-cap-pubmed-days
gh pr create --base main --title "Partition an over-cap PubMed day, and resume one (#105)" --body "..."
```

The PR body must link #105 and #107, state the one-off backfill cost in prose, and note that `main`'s ruleset requires CodeQL — which comes from GitHub's default setup, so a green pytest run is not the whole gate.

---

## Self-Review

**Spec coverage.** Every section of the spec maps to a task: §1 (why a range) → Task 3's docstrings and Task 10's DECISIONS entry; §2 (the ladder) → Task 3; §3 (fetching a part, incl. the re-check) → Tasks 2 and 4; §4 (what fails) → Tasks 3 and 4; §5 (progress) → Task 4; §6 (resume) → Tasks 5–8; Cost → Task 10; The FTP route → Task 10; Testing → the test steps of Tasks 3–9.

**Two spec claims that needed a task and now have one:** the `part_scheme` column is written by Task 5 and read by nothing — deliberately, since it exists so a future scheme change is *visible*; and the "non-resumable fetcher is called with exactly today's keywords" test is in Task 8, not Task 6, because it needs `sync()`.

**Type consistency.** `PartCheckpoint` field order is `(part_scheme, part_key, promised, record_count)` in Task 5 and in every positional construction in Tasks 7–9. `_walk_session` returns `_WalkOutcome(processed, delivered, stalled, error)` in Task 2 and is destructured by attribute everywhere. `_plan_partitions`' keyword-only `lo`/`hi`/`probe_root` are used in Task 4's re-partition call exactly as Task 3 defines them.

**Ordering constraint.** Task 4 accepts the resume parameters as `Any` and Task 7 tightens them, so `fetch_pubmed`'s signature is touched once. Tasks 5 and 6 are independent of 1–4 and may be done in parallel; 7 needs 5, and 8 needs 5, 6 and 7.
