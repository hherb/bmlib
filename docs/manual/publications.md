# bmlib.publications — Publication Ingestion and Sync

Publication ingestion, deduplication, storage, and multi-source sync for biomedical literature. Fetches records from PubMed, bioRxiv, medRxiv, and OpenAlex, stores them in a unified schema with automatic deduplication by normalised DOI and PMID, and tracks which source/date combinations have already been downloaded.

## Installation

```bash
pip install bmlib[publications]
```

Requires `httpx` for HTTP requests to external APIs.

## Module layout

| Submodule | Contents | Role |
|-----------|----------|------|
| `models` | `Publication`, `FullTextSource`, `DownloadDay`, `FetchResult`, `SyncProgress`, `SyncReport`, `FetchedRecord`, `SourceDescriptor`, `SourceParam` | Data models |
| `schema` | `SCHEMA_SQL`, `ensure_schema()` | Table definitions |
| `storage` | `store_publication()`, `get_publication_by_doi()`, `get_publication_by_pmid()`, `add_fulltext_source()` | De-duplicating writes and lookups |
| `sync` | `sync()` | The sole orchestration entry point |
| `fetchers.registry` | `register_source()`, `list_sources()`, `get_source()`, `get_fetcher()`, `source_names()` | Pluggable source registry |
| `fetchers.pubmed` / `.biorxiv` / `.openalex` | `fetch_pubmed()`, `fetch_biorxiv()`, `fetch_openalex()` | Built-in fetchers |

> **Breaking change in 0.4.0 — `on_record` now fires *before* storage.**
> `sync()` no longer stores each record as it arrives. Records are buffered for the duration of a source/day fetch and written in a **single transaction per day** once the fetch completes. Your `on_record` callback is invoked while the record is still only in memory, so it **must not** expect to read the record back from the database. See [Buffering and commit batching](#buffering-and-commit-batching).

> **`source_configs` supersedes `email` and `api_keys`.**
> Per-source configuration is now passed as `source_configs={"openalex": {"email": ...}}` and unpacked as `**kwargs` into the fetcher. The `email` and `api_keys` parameters still work and are still supported, but they are legacy and are ignored entirely when `source_configs` is supplied.

## Imports

```python
from bmlib.publications import (
    # Sync orchestration
    sync,
    SyncReport,
    SyncProgress,
    FetchResult,

    # Data models
    Publication,
    FetchedRecord,
    FullTextSource,
    DownloadDay,

    # Source registry
    SourceDescriptor,
    SourceParam,
    register_source,
    list_sources,
    get_source,
    get_fetcher,
    source_names,

    # Storage operations
    store_publication,
    get_publication_by_doi,
    get_publication_by_pmid,
    add_fulltext_source,

    # Schema
    ensure_schema,
)
```

The list above is the complete `bmlib.publications.__all__`. The fetcher functions themselves are not re-exported at package level and must be imported from their submodules:

```python
from bmlib.publications.fetchers.biorxiv import fetch_biorxiv
from bmlib.publications.fetchers.openalex import fetch_openalex
from bmlib.publications.fetchers.pubmed import fetch_pubmed
from bmlib.publications.fetchers import ALL_SOURCES  # backward-compat constant
```

---

## Data Models

### `Publication`

A biomedical publication record — the stored form.

```python
@dataclass
class Publication:
    title: str
    sources: list[str]
    first_seen_source: str

    doi: str | None = None
    pmid: str | None = None
    abstract: str | None = None
    authors: list[str] = field(default_factory=list)
    journal: str | None = None
    publication_date: str | None = None
    publication_types: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    is_open_access: bool = False
    license: str | None = None
    created_at: datetime = field(default_factory=_now_utc)
    updated_at: datetime = field(default_factory=_now_utc)
    id: int | None = None
```

| Field | Type | Description |
|-------|------|-------------|
| `title` | `str` | Paper title. *(required)* |
| `sources` | `list[str]` | List of source names that provided this record (e.g. `["pubmed", "openalex"]`). *(required)* |
| `first_seen_source` | `str` | The first source to provide this record. *(required)* |
| `doi` | `str \| None` | Digital Object Identifier. Normalised on store — see [Identifier normalisation](#identifier-normalisation). |
| `pmid` | `str \| None` | PubMed ID. Whitespace-stripped on store. |
| `abstract` | `str \| None` | Paper abstract. |
| `authors` | `list[str]` | List of author names. |
| `journal` | `str \| None` | Journal name. |
| `publication_date` | `str \| None` | Publication date string (YYYY, YYYY-MM, or YYYY-MM-DD). |
| `publication_types` | `list[str]` | PubMed publication type strings or OpenAlex work types. |
| `keywords` | `list[str]` | Keywords or MeSH headings. |
| `is_open_access` | `bool` | Whether the paper is open access. |
| `license` | `str \| None` | License identifier. |
| `created_at` | `datetime` | When the record was first stored (UTC). |
| `updated_at` | `datetime` | When the record was last updated (UTC). |
| `id` | `int \| None` | Database row ID (set after storage). |

#### Serialisation

| Method | Description |
|--------|-------------|
| `to_dict() -> dict[str, Any]` | Serialise to a JSON-safe dictionary. |
| `from_dict(data: dict) -> Publication` | Deserialise from a dictionary. |

---

### `FetchedRecord`

The canonical record format returned by **all** source fetchers — the wire format between a fetcher and `sync()`. Core fields are guaranteed present (though they may be `None` or empty); anything source-specific goes in `extras`.

```python
@dataclass
class FetchedRecord:
    # -- Identifiers --
    title: str
    source: str
    doi: str | None = None
    pmid: str | None = None
    pmc_id: str | None = None

    # -- Content --
    abstract: str | None = None
    authors: list[str] = field(default_factory=list)
    journal: str | None = None
    publication_date: str | None = None
    keywords: list[str] = field(default_factory=list)
    publication_types: list[str] = field(default_factory=list)

    # -- Access --
    is_open_access: bool = False
    license: str | None = None
    fulltext_sources: list[FullTextSourceEntry] = field(default_factory=list)

    # -- Source-specific extras --
    extras: dict[str, Any] = field(default_factory=dict)
```

| Field | Type | Description |
|-------|------|-------------|
| `title` | `str` | Paper title. *(required)* |
| `source` | `str` | Name of the source that produced the record. *(required)* |
| `doi` | `str \| None` | DOI as reported by the source (may carry a prefix or mixed case). |
| `pmid` | `str \| None` | PubMed ID. |
| `pmc_id` | `str \| None` | PubMed Central ID. Not carried over into `Publication`. |
| `abstract` | `str \| None` | Abstract text. |
| `authors` | `list[str]` | Author names. |
| `journal` | `str \| None` | Journal or venue name. |
| `publication_date` | `str \| None` | Publication date string. |
| `keywords` | `list[str]` | Keywords or MeSH headings. |
| `publication_types` | `list[str]` | Publication / work types. |
| `is_open_access` | `bool` | Open-access flag. |
| `license` | `str \| None` | License identifier. |
| `fulltext_sources` | `list[FullTextSourceEntry]` | Known full-text URLs (see [`bmlib.fulltext`](fulltext.md)). Plain dicts with `url`/`source`/`format`/`version` keys are also accepted. |
| `extras` | `dict[str, Any]` | Source-specific data not covered by the core fields. |

`sync()` maps `FetchedRecord` → `Publication` by copying the core fields and setting both `sources=[record.source]` and `first_seen_source=record.source`. Note that `pmc_id` and `extras` are **not** persisted — if you need them, capture them in an `on_record` callback.

---

### `FullTextSource`

A full-text source for a publication (e.g. PMC XML, publisher PDF), as stored in the database.

```python
@dataclass
class FullTextSource:
    publication_id: int
    source: str
    url: str
    format: str

    version: str | None = None
    retrieved_at: datetime | None = None
    created_at: datetime = field(default_factory=_now_utc)
    id: int | None = None
```

| Field | Type | Description |
|-------|------|-------------|
| `publication_id` | `int` | Foreign key to the publications table. |
| `source` | `str` | Source name (e.g. `"pmc"`, `"publisher"`, `"biorxiv"`). |
| `url` | `str` | URL to the full text. |
| `format` | `str` | Format of the full text (e.g. `"html"`, `"pdf"`, `"xml"`). |
| `version` | `str \| None` | Version (e.g. `"published"`, `"accepted"`, `"preprint"`). |
| `retrieved_at` | `datetime \| None` | When the full text was last retrieved. |

Serialisable via `to_dict()` / `from_dict()`.

---

### `DownloadDay`

Tracks download status for a single source on a single date.

```python
@dataclass
class DownloadDay:
    source: str
    date: str
    status: str
    record_count: int

    downloaded_at: datetime = field(default_factory=_now_utc)
    last_verified_at: datetime | None = None
    id: int | None = None
```

| Field | Type | Description |
|-------|------|-------------|
| `source` | `str` | Source name (e.g. `"pubmed"`). |
| `date` | `str` | Date string (YYYY-MM-DD). |
| `status` | `str` | `"completed"` or `"failed"`. |
| `record_count` | `int` | Number of records stored (added + merged). |
| `downloaded_at` | `datetime` | When the fetch was performed. |
| `last_verified_at` | `datetime \| None` | When the data was last verified/re-fetched. |

Serialisable via `to_dict()` / `from_dict()`.

---

### `FetchResult`

Result of fetching records from a source for a given date. Returned by every fetcher.

```python
@dataclass
class FetchResult:
    source: str
    date: str
    record_count: int
    status: str          # "completed" or "failed"
    error: str | None = None
```

---

### `SyncProgress`

Progress report during a sync operation. Passed straight through from `sync()` to each fetcher, which invokes it after each page.

```python
@dataclass
class SyncProgress:
    source: str
    date: str
    records_processed: int
    records_total: int
    status: str
    message: str | None = None
```

---

### `SyncReport`

Summary report after completing a sync operation.

```python
@dataclass
class SyncReport:
    sources_synced: list[str]
    days_processed: int
    records_added: int
    records_merged: int
    records_failed: int
    errors: list[str] = field(default_factory=list)
```

| Field | Type | Description |
|-------|------|-------------|
| `sources_synced` | `list[str]` | Every source whose sync loop ran to completion. |
| `days_processed` | `int` | Number of source/day combinations processed. |
| `records_added` | `int` | Records inserted as new rows. |
| `records_merged` | `int` | Records merged into existing rows. |
| `records_failed` | `int` | Records that raised while being stored. |
| `errors` | `list[str]` | Per-day error strings, formatted `"{source}/{date}: {error}"`, plus one `"No fetcher found for source: {name}"` entry per unresolvable source. |

> **`sources_synced` is not a success list.**
> It contains every source whose loop ran to completion — **including** sources where individual days failed, because a fetcher error records a failed day and moves on rather than aborting. A source is absent only when no fetcher could be found for it. To detect failures, check `errors` and `records_failed`, not membership in `sources_synced`.

---

### `SourceDescriptor` and `SourceParam`

Metadata describing a registered publication source and its configurable parameters. Used by the registry and by UIs that need to prompt for credentials.

```python
@dataclass
class SourceParam:
    name: str
    description: str
    required: bool = False
    default: str | None = None
    secret: bool = False


@dataclass
class SourceDescriptor:
    name: str
    display_name: str
    description: str
    params: list[SourceParam] = field(default_factory=list)
```

| Field | Type | Description |
|-------|------|-------------|
| `SourceParam.name` | `str` | Keyword-argument name passed to the fetcher. |
| `SourceParam.description` | `str` | Human-readable explanation. |
| `SourceParam.required` | `bool` | Whether the fetcher will fail without it. |
| `SourceParam.default` | `str \| None` | Suggested default value. |
| `SourceParam.secret` | `bool` | Whether the value is a credential (mask it in UIs and logs). |
| `SourceDescriptor.name` | `str` | Registry key, e.g. `"pubmed"`. |
| `SourceDescriptor.display_name` | `str` | Presentation name, e.g. `"PubMed"`. |
| `SourceDescriptor.description` | `str` | What the source covers. |
| `SourceDescriptor.params` | `list[SourceParam]` | Configurable parameters for this source. |

---

## Schema

### `ensure_schema`

```python
def ensure_schema(conn: Any) -> None
```

Create all publications tables if they do not exist, delegating to `bmlib.db.create_tables`. Creates three tables:

- **`publications`** — Core publication records, with unique **partial** indexes on `doi` and `pmid` (each `WHERE ... IS NOT NULL`, so any number of rows may have a `NULL` identifier) and a plain index on `publication_date`.
- **`fulltext_sources`** — Full-text source URLs linked to publications. Unique on `(publication_id, url)`.
- **`download_days`** — Tracks which source/date combinations have been fetched. Unique on `(source, date)`.

Called automatically by `sync()`. Call manually if you need the schema before syncing. The raw DDL is available as `bmlib.publications.schema.SCHEMA_SQL`.

**Example:**

```python
from bmlib.db import connect_sqlite
from bmlib.publications import ensure_schema

conn = connect_sqlite("publications.db")
ensure_schema(conn)
```

---

## Storage Operations

All storage functions take a DB-API connection as the first argument and operate on the publications schema.

> **The storage layer is SQLite-specific.**
> It uses `?` placeholders, `ON CONFLICT`, `UPDATE OR IGNORE`, and `cur.lastrowid`, even though `bmlib.db` itself also supports PostgreSQL. Pass an SQLite connection.

### Commit semantics

`store_publication()` and `add_fulltext_source()` each run their work inside a `bmlib.db.transaction` block:

- Called with **no transaction open**, they commit exactly once and return.
- Called when the connection is **already inside a transaction** — a caller-managed `transaction(conn)` block, or pending auto-begun writes from an earlier bare `execute()` — they join it via a `SAVEPOINT`, leaving the commit (and therefore the batch boundary) to the caller. A failure rolls back only that call's own writes.

This is what makes bulk ingestion cheap: `sync()` wraps a whole day of records in one outer `transaction(conn)` and pays a single commit. You can do the same in your own batch loops:

```python
from bmlib.db import transaction
from bmlib.publications import store_publication

with transaction(conn):              # one commit for the whole batch
    for pub in publications:
        store_publication(conn, pub)  # joins the open transaction
```

### Identifier normalisation

`store_publication()` normalises both identifiers before lookup and before storage, so the same work fetched from different sources resolves to a single row:

| Identifier | Normalisation |
|------------|---------------|
| `doi` | Strip whitespace, strip a leading `https://doi.org/`, `http://doi.org/`, `https://dx.doi.org/`, `http://dx.doi.org/`, or `doi:` prefix, then lower-case. Empty results become `None`. |
| `pmid` | Strip whitespace. Empty results become `None`. |

DOIs are case-insensitive per the DOI handbook, but sources disagree in practice — PubMed preserves the registered (often mixed-case) form while OpenAlex lower-cases everything and prefixes with `https://doi.org/`. Storing one canonical form is what makes cross-source deduplication work.

> **`store_publication()` mutates its `pub` argument in place.**
> `pub.doi` and `pub.pmid` are overwritten with their canonical forms before the write. If you need the source's original strings, keep a copy before calling.

`get_publication_by_doi()` and `get_publication_by_pmid()` apply the same normalisation to their arguments, so a lookup using any case or prefix variant matches the stored form.

### Split-identity consolidation

An incoming record's DOI and PMID can point at **two different existing rows** — a "split identity" that arises when a work is indexed under one identifier before its cross-reference to the other exists (typically a preprint DOI ingested from bioRxiv, later cross-referenced by PubMed). Without handling, the subsequent merge would try to write the second row's identifier onto the first and hit the `UNIQUE` index, aborting the write and stranding the duplicates permanently.

When `store_publication()` detects this, it consolidates before merging:

1. The **DOI row is kept**; the PMID row is dropped.
2. The drop row's full-text sources are re-pointed at the keep row with `UPDATE OR IGNORE` (silently skipping any `(publication_id, url)` pair the keep row already has); leftover duplicates on the drop row are then deleted.
3. The drop row's data is snapshotted, the drop row is deleted — freeing its unique identifier — and the snapshot is merged into the keep row.
4. The keep row is re-read, and the incoming record is merged into it as usual. The call returns `"merged"`.

The whole consolidation runs inside `store_publication()`'s transaction, so a failure part-way through rolls back completely and cannot lose the drop row's data.

---

### `store_publication`

```python
def store_publication(
    conn: Any,
    pub: Publication,
    fulltext_sources: Sequence[FullTextSource] | None = None,
) -> str
```

Store a publication, de-duplicating by DOI then PMID.

**Deduplication logic:**
1. Normalise `pub.doi` and `pub.pmid` in place.
2. Look up the existing row by DOI and by PMID independently.
3. If both match but are **different rows**, [consolidate them](#split-identity-consolidation) first.
4. If a row was found, **merge** the incoming record into it.
5. Otherwise, **insert** as a new record.
6. Insert any `fulltext_sources` against the resulting row id.

**Merge behaviour:**
- Appends new source names to the existing `sources` list (no duplicates).
- Fills `NULL` fields from the incoming record via `COALESCE`; never overwrites an existing non-`NULL` value.
- `authors`, `publication_types`, `keywords`: keeps the existing list if non-empty, otherwise takes the incoming one.
- `is_open_access`: can only be upgraded from `0` to the incoming value, never downgraded.
- `updated_at` is set to now.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `conn` | `Any` | *(required)* | A DB-API connection with the publications schema. |
| `pub` | `Publication` | *(required)* | The publication to store. **Mutated in place** to hold normalised identifiers. |
| `fulltext_sources` | `Sequence[FullTextSource] \| None` | `None` | Optional full-text sources to associate with the publication. Their `publication_id` is ignored; the stored row's id is used. |

**Returns:** `"added"` for a new record, `"merged"` for an updated existing record.

**Example:**

```python
pub = Publication(
    title="A Novel Study",
    doi="https://doi.org/10.1234/EXAMPLE",   # prefix + case are normalised away
    pmid="12345678",
    abstract="We investigated...",
    authors=["Smith, John", "Doe, Jane"],
    journal="Nature",
    publication_date="2025-01-15",
    sources=["pubmed"],
    first_seen_source="pubmed",
)
result = store_publication(conn, pub)
print(result)    # "added" or "merged"
print(pub.doi)   # "10.1234/example" — mutated in place
```

---

### `get_publication_by_doi`

```python
def get_publication_by_doi(conn: Any, doi: str) -> Publication | None
```

Look up a publication by DOI. The argument is normalised before lookup, so any case or prefix variant matches. Returns `None` if not found.

---

### `get_publication_by_pmid`

```python
def get_publication_by_pmid(conn: Any, pmid: str) -> Publication | None
```

Look up a publication by PMID. The argument is whitespace-stripped before lookup. Returns `None` if not found.

---

### `add_fulltext_source`

```python
def add_fulltext_source(
    conn: Any,
    publication_id: int,
    source: str,
    url: str,
    fmt: str,
    version: str | None = None,
) -> bool
```

Add a full-text source for a publication. Commits when called standalone; joins the caller's open transaction otherwise.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `conn` | `Any` | *(required)* | A DB-API connection. |
| `publication_id` | `int` | *(required)* | The publication's database ID. |
| `source` | `str` | *(required)* | Source name (e.g. `"pmc"`, `"publisher"`). |
| `url` | `str` | *(required)* | URL to the full text. |
| `fmt` | `str` | *(required)* | Format (e.g. `"html"`, `"pdf"`, `"xml"`). |
| `version` | `str \| None` | `None` | Version string. |

**Returns:** `True` if inserted, `False` if the `(publication_id, url)` pair already exists.

---

## Sync Orchestrator

### `sync`

```python
def sync(
    conn: Any,
    *,
    sources: list[str] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    email: str = "",
    api_keys: dict[str, str] | None = None,
    source_configs: dict[str, dict[str, Any]] | None = None,
    on_record: Callable[[FetchedRecord], None] | None = None,
    on_progress: Callable[[SyncProgress], None] | None = None,
    recheck_days: int = 0,
    _fetcher_override: dict[str, Callable] | None = None,
) -> SyncReport
```

The sole public entry point for ingestion. Ensures the schema exists, determines which days need fetching per source, fetches records, deduplicates, and stores them one day per transaction.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `conn` | `Any` | *(required)* | A DB-API connection. |
| `sources` | `list[str] \| None` | `None` | Source names to sync. Defaults to **all registered sources** (`source_names()`), which includes any you registered yourself. |
| `date_from` | `date \| None` | `None` | Start date (inclusive). Defaults to yesterday (`today - 1 day`). |
| `date_to` | `date \| None` | `None` | End date (inclusive). Defaults to today. |
| `source_configs` | `dict[str, dict[str, Any]] \| None` | `None` | Per-source config. Each inner dict is unpacked as `**kwargs` into that source's fetcher. **Supersedes `email` and `api_keys` entirely when provided.** |
| `email` | `str` | `""` | *(legacy)* Contact email. Folded into `source_configs["openalex"]["email"]`. |
| `api_keys` | `dict[str, str] \| None` | `None` | *(legacy)* Source name → API key. Folded into `source_configs[source]["api_key"]`. |
| `on_record` | `Callable[[FetchedRecord], None] \| None` | `None` | Callback invoked with each `FetchedRecord` **as it is fetched, before it is stored**. See [the contract](#the-on_record-contract). |
| `on_progress` | `Callable[[SyncProgress], None] \| None` | `None` | Callback invoked with `SyncProgress` updates. Passed directly to each fetcher. |
| `recheck_days` | `int` | `0` | If > 0, re-fetch completed days last verified more than this many days ago. |
| `_fetcher_override` | `dict[str, Callable] \| None` | `None` | Private. Source name → callable, for testing. When set, no HTTP client is created and `client` is passed as `None`. |

**Returns:** `SyncReport`

**HTTP client:** when `_fetcher_override` is `None`, `sync()` creates one `httpx.Client` for the whole run, with a 30 s timeout and a `User-Agent` of `bmlib/{version} (mailto:{email})`, where `{version}` is `bmlib.__version__` — where the email is taken from `source_configs["openalex"]["email"]`, falling back to the `email` parameter, and finally to the literal `unknown`. The client is closed in a `finally` block.

### Which days get fetched

For each source, `sync()` walks `date_from`..`date_to` and selects a day when:

- The day **is today** — today is *always* re-fetched, to catch late additions.
- There is **no `download_days` row** for that source/day.
- The row's `status` is `"failed"`.
- `recheck_days > 0` **and** the row's `last_verified_at` is older than `today - recheck_days` — **or** is `NULL`.

Days with a `"completed"` row inside the recheck window are skipped.

### Buffering and commit batching

This is the core of the 0.4.0 rewrite and the reason for the `on_record` contract change.

For each source/day, `sync()` runs two distinct phases:

1. **Fetch phase (no database writes).** The fetcher streams records into an internal `handle_record` callback, which appends each one to an in-memory list for the day and forwards it to your `on_record`. Nothing is written to the database.
2. **Store phase (one transaction).** After the fetch returns, a single `with transaction(conn):` block wraps the entire store loop *plus* the `download_days` status upsert.

Why it is built this way:

- **One commit per day, not one per record.** Each `store_publication()` call joins the open transaction via a savepoint rather than committing, so a day of several thousand records costs a single commit/fsync.
- **The write lock is never held across network I/O.** Because records are buffered during the fetch, SQLite's write lock is taken only for the store loop, not for the minutes-long, network-bound fetch. Concurrent readers and writers are not blocked while the fetcher is waiting on an API.
- **A failed record does not lose the batch.** Per-record exceptions roll back to that record's own savepoint, increment `records_failed`, log, and continue.
- **The day status commits atomically with its records.** The `download_days` row lands in the same transaction, so the database can never claim a day is `"completed"` while its records are missing. If writing that status row itself fails, the whole day rolls back and the error propagates — the day is simply left unrecorded and retried on the next run.

The trade-off is memory: the whole day is held at once. In practice this is a few thousand records and tens of megabytes with abstracts. A source delivering far larger days would need chunked flushing.

**Failure isolation.** A fetcher exception — a misconfigured source (e.g. OpenAlex's required `email` missing), a network failure, or a bug inside the fetcher — is caught **per day**. It is logged, synthesised into a `FetchResult(status="failed", ...)`, appended to `SyncReport.errors`, and the run continues with the next day and the next source. One broken source never aborts a multi-source run or discards the report.

### The `on_record` contract

> **Changed in 0.4.0.** `on_record` is now invoked *before* the record is stored.

What a callback **may** assume:

- It receives a fully normalised `FetchedRecord` (not a raw dict, and not a `Publication`).
- It is called once per record, in fetch order, on the calling thread.
- Fields dropped during storage — `pmc_id`, `extras` — are still present here. This is the only place to capture them.

What a callback **must not** assume:

- That the record exists in the database. It does not yet; it is only buffered.
- That `Publication.id` is available, or that `get_publication_by_doi()` will find the record.
- That being called means the record will be stored — a later per-record failure, or a failure writing the day-status row, can roll it back.
- That raising is safe. An exception from `on_record` escapes into the fetcher's record loop and aborts the rest of that day's fetch — `fetch_biorxiv` converts it into a failed `FetchResult`, while `fetch_pubmed` and `fetch_openalex` let it propagate to `sync()`'s per-day handler. Either way the day is marked `"failed"` (and retried next run), though whatever was buffered before the throw is still stored. Catch your own exceptions.

To act on records **after** they are durably stored, do the work between `sync()` calls (for example one call per day, then query the database), rather than in `on_record`.

**Example:**

```python
from datetime import date
from bmlib.db import connect_sqlite
from bmlib.publications import sync

conn = connect_sqlite("publications.db")

# Modern configuration: one config dict per source, unpacked into the fetcher
report = sync(
    conn,
    sources=["pubmed", "biorxiv", "openalex"],
    date_from=date(2026, 6, 1),
    date_to=date(2026, 6, 7),
    source_configs={
        "pubmed": {"api_key": "your_ncbi_api_key"},
        "openalex": {"email": "researcher@example.com"},
    },
)

print(f"Sources: {report.sources_synced}")
print(f"Days processed: {report.days_processed}")
print(f"Added: {report.records_added}, merged: {report.records_merged}")
print(f"Failed: {report.records_failed}")
for err in report.errors:
    print(f"  ! {err}")
```

```python
# Progress reporting
def on_progress(progress):
    pct = (progress.records_processed / max(progress.records_total, 1)) * 100
    print(f"  {progress.source} {progress.date}: {pct:.0f}%"
          f" ({progress.records_processed}/{progress.records_total})")

report = sync(
    conn,
    sources=["pubmed"],
    date_from=date(2026, 6, 1),
    date_to=date(2026, 6, 1),
    on_progress=on_progress,
)
```

```python
# Capturing fields that storage drops, safely
pmc_ids: dict[str, str] = {}

def on_record(record):
    try:
        if record.pmc_id and record.doi:
            pmc_ids[record.doi] = record.pmc_id
    except Exception as exc:          # never let the callback fail the day
        print(f"callback error: {exc}")

report = sync(
    conn,
    sources=["pubmed"],
    date_from=date(2026, 6, 1),
    date_to=date(2026, 6, 1),
    on_record=on_record,
)
# Records are queryable only now that sync() has returned and committed.
```

```python
# Legacy configuration — still supported, ignored if source_configs is given
report = sync(
    conn,
    date_from=date(2026, 1, 1),
    date_to=date(2026, 6, 30),
    email="researcher@example.com",
    api_keys={"pubmed": "your_ncbi_api_key"},
    recheck_days=30,
)
```

---

## Source Registry

Sources are looked up in a module-level registry. Built-ins are registered lazily on first access, guarded by a dedicated flag rather than registry truthiness — so a custom source registered *before* any lookup does not suppress the built-ins.

### Registry functions

```python
def register_source(descriptor: SourceDescriptor, fetcher: Callable[..., Any]) -> None
def list_sources() -> list[SourceDescriptor]
def get_source(name: str) -> tuple[SourceDescriptor, Callable[..., Any]]
def get_fetcher(name: str) -> Callable[..., Any]
def source_names() -> list[str]
```

| Function | Description |
|----------|-------------|
| `register_source(descriptor, fetcher)` | Register (or replace) a fetcher under `descriptor.name`. |
| `list_sources()` | Descriptors for all registered sources. |
| `get_source(name)` | The `(descriptor, fetcher)` tuple. Raises `ValueError(f"Unknown source {name!r}. Available: [...]")` if unregistered. |
| `get_fetcher(name)` | Just the fetcher callable. Same `ValueError` on an unknown name. |
| `source_names()` | Names of all registered sources — this is what `sync()` defaults `sources` to. |

`bmlib.publications.fetchers` additionally exports `ALL_SOURCES = ["pubmed", "biorxiv", "medrxiv", "openalex"]`, a backward-compatibility constant for code that reads the built-in list at module level. It does not reflect runtime registrations; use `source_names()` instead.

### Built-in sources

| Source | Display name | Fetcher | Parameters |
|--------|--------------|---------|------------|
| `pubmed` | PubMed | `fetch_pubmed` | `api_key` — NCBI API key for higher rate limits *(secret)* |
| `biorxiv` | bioRxiv | `fetch_biorxiv` (`server="biorxiv"`) | `api_key` — reserved, unused *(secret)* |
| `medrxiv` | medRxiv | `fetch_biorxiv` (`server="medrxiv"`) | `api_key` — reserved, unused *(secret)* |
| `openalex` | OpenAlex | `fetch_openalex` | `email` — contact email for polite API access *(**required**)*; `api_key` — OpenAlex API key for premium access *(secret)* |

`biorxiv` and `medrxiv` are registered as thin lambdas that call `fetch_biorxiv` with the appropriate `server`. Only `openalex` has a genuinely required parameter — omitting `email` makes its fetcher raise, which `sync()` records as a failed day.

**Example:**

```python
from bmlib.publications import list_sources

for desc in list_sources():
    required = [p.name for p in desc.params if p.required]
    print(f"{desc.name:10s} {desc.display_name:10s} required={required or '-'}")
```

---

## Writing a Custom Fetcher

Every registered fetcher shares one calling convention:

```python
fetcher(client, target_date, *, on_record, on_progress=None, **config)
```

| Argument | Type | Description |
|----------|------|-------------|
| `client` | `Any` | The shared HTTP client (an `httpx.Client`), positional. `None` when `sync()` is driven with `_fetcher_override`. |
| `target_date` | `date` | The single day to fetch, positional. |
| `on_record` | `Callable[[FetchedRecord], None]` | Keyword-only. Call once per record. |
| `on_progress` | `Callable[[SyncProgress], None] \| None` | Keyword-only, may be `None`. Call after each page. |
| `**config` | `Any` | Whatever `source_configs[name]` contained, unpacked. Declare each key you accept as an explicit keyword parameter. |

Contract:

- Return a `FetchResult`, with `status` of `"completed"` or `"failed"` (`sync()` treats any status other than `"failed"` as completed).
- Emit `FetchedRecord` instances — always set `title` and `source`.
- Prefer catching your own HTTP errors and returning `FetchResult(status="failed", error=...)`; a raised exception is caught by `sync()` per day, but returning lets you report a partial `record_count`.
- Rate-limit yourself. `sync()` does not throttle on your behalf.

**Example — registering a source backed by a local JSON dump:**

```python
import json
from datetime import date
from typing import Any

from bmlib.publications import (
    FetchedRecord,
    FetchResult,
    SourceDescriptor,
    SourceParam,
    register_source,
    sync,
)


def fetch_local_dump(
    client: Any,
    target_date: date,
    *,
    on_record,
    on_progress=None,
    dump_dir: str,
) -> FetchResult:
    """Emit records from {dump_dir}/{YYYY-MM-DD}.json."""
    path = f"{dump_dir}/{target_date.isoformat()}.json"
    try:
        with open(path) as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        return FetchResult(
            source="local", date=target_date.isoformat(), record_count=0, status="completed"
        )
    except Exception as exc:
        return FetchResult(
            source="local",
            date=target_date.isoformat(),
            record_count=0,
            status="failed",
            error=str(exc),
        )

    for item in raw:
        on_record(
            FetchedRecord(
                title=item["title"],
                source="local",
                doi=item.get("doi"),
                abstract=item.get("abstract"),
                authors=item.get("authors", []),
                publication_date=target_date.isoformat(),
            )
        )

    return FetchResult(
        source="local", date=target_date.isoformat(), record_count=len(raw), status="completed"
    )


register_source(
    SourceDescriptor(
        name="local",
        display_name="Local dump",
        description="Reads pre-downloaded JSON files from disk",
        params=[SourceParam("dump_dir", "Directory holding YYYY-MM-DD.json files", required=True)],
    ),
    fetch_local_dump,
)

report = sync(
    conn,
    sources=["local"],
    date_from=date(2026, 6, 1),
    date_to=date(2026, 6, 3),
    source_configs={"local": {"dump_dir": "/data/dumps"}},
)
```

Registering under an existing name replaces that source's fetcher — useful for swapping in an instrumented or cached variant. Overriding a **built-in** name works directly, in any order:

```python
from bmlib.publications import register_source, get_fetcher

register_source(pubmed_descriptor, my_fetcher)
get_fetcher("pubmed") is my_fetcher   # True
```

`register_source()` registers the built-ins first, then writes your entry, so your override always wins. Before 0.4.0 it did not, and an override installed before the first lookup was silently reverted the moment lazy registration ran.

---

## Built-in Fetchers

Fetchers are used internally by `sync()` but can be called directly for advanced use cases. Each takes an HTTP client and one date.

All fetchers pass a `FetchedRecord` to `on_record`, matching `sync()`. (Before 0.4.0 the three built-ins annotated the parameter as `Callable[[dict], None]` while passing a `FetchedRecord`; the annotations were corrected, the behaviour never changed.)

### `fetch_pubmed`

```python
def fetch_pubmed(
    client: Any,
    target_date: date,
    *,
    on_record: Callable[[FetchedRecord], None],
    on_progress: Callable[[SyncProgress], None] | None = None,
    api_key: str | None = None,
) -> FetchResult
```

Fetch all PubMed articles published on `target_date` using NCBI E-utilities.

- ESearch (with `usehistory=y`) to count and stage PMIDs, then paged EFetch to retrieve XML.
- Pages through results in batches of 500 (`EFETCH_PAGE_SIZE`).
- Rate limits: `0.1 s` with an API key, `0.34 s` without — selected purely by whether `api_key` is truthy.
- Extracts: PMID, title, abstract (multi-part), authors, journal, DOI, PMC ID, MeSH keywords, and full-text source URLs (PMC article page, DOI resolver). It also populates `publication_types` from `PublicationTypeList`, which is what the free Tier 1 quality filter classifies study design from (see [quality.md](quality.md)). Before 0.4.0 this field was left empty, so synced PubMed records skipped the free tier entirely.
- Takes **no** `email` parameter.

### `fetch_biorxiv`

```python
def fetch_biorxiv(
    client: Any,
    target_date: date,
    *,
    on_record: Callable[[FetchedRecord], None],
    on_progress: Callable[[SyncProgress], None] | None = None,
    server: str = "biorxiv",
    api_key: str | None = None,
) -> FetchResult
```

Fetch preprint records from the bioRxiv/medRxiv API for a single date.

- Uses `https://api.biorxiv.org/details/{server}/{date}/{date}/{cursor}`.
- `server` is `"biorxiv"` (default) or `"medrxiv"`; the registry supplies it via the two registered lambdas.
- Pages through results in batches of 100 (`PAGE_SIZE`), 0.5 s between pages.
- Extracts: DOI, title, authors (semicolon-separated), abstract, date, category, PDF URL, JATS XML URL.
- Requires **no** credentials. `api_key` is accepted but unused — reserved for future API authentication.

### `fetch_openalex`

```python
def fetch_openalex(
    client: Any,
    target_date: date,
    *,
    on_record: Callable[[FetchedRecord], None],
    on_progress: Callable[[SyncProgress], None] | None = None,
    email: str,
    api_key: str | None = None,
) -> FetchResult
```

Fetch all OpenAlex works published on `target_date`.

- `email` is a **required keyword-only argument with no default** — sent as the `mailto` query parameter for the polite pool. Omitting it raises `TypeError`, which `sync()` records as a failed day.
- Cursor-based pagination at `per_page=200`, 0.1 s between pages.
- Reconstructs abstracts from OpenAlex's inverted-index format.
- Strips the `https://doi.org/` and `https://pubmed.ncbi.nlm.nih.gov/` prefixes from identifiers.
- Extracts: DOI, PMID, title, authors, journal, abstract, publication date, keywords (primary topic), open-access status, license, and full-text source URLs with versions.

---

## Database Schema

The publications module creates three tables.

### `publications`

| Column | Type | Constraints |
|--------|------|-------------|
| `id` | `INTEGER` | `PRIMARY KEY AUTOINCREMENT` |
| `doi` | `TEXT` | `UNIQUE` (partial index, `WHERE doi IS NOT NULL`) |
| `pmid` | `TEXT` | `UNIQUE` (partial index, `WHERE pmid IS NOT NULL`) |
| `title` | `TEXT` | `NOT NULL` |
| `abstract` | `TEXT` | |
| `authors` | `TEXT` | JSON array, default `'[]'` |
| `journal` | `TEXT` | |
| `publication_date` | `TEXT` | Indexed |
| `publication_types` | `TEXT` | JSON array, default `'[]'` |
| `keywords` | `TEXT` | JSON array, default `'[]'` |
| `is_open_access` | `INTEGER` | Default `0` |
| `license` | `TEXT` | |
| `sources` | `TEXT` | JSON array, `NOT NULL`, default `'[]'` |
| `first_seen_source` | `TEXT` | `NOT NULL` |
| `created_at` | `TEXT` | `NOT NULL` |
| `updated_at` | `TEXT` | `NOT NULL` |

Indexes: `idx_publications_doi` (unique, partial), `idx_publications_pmid` (unique, partial), `idx_publications_publication_date`.

### `fulltext_sources`

| Column | Type | Constraints |
|--------|------|-------------|
| `id` | `INTEGER` | `PRIMARY KEY AUTOINCREMENT` |
| `publication_id` | `INTEGER` | `NOT NULL REFERENCES publications(id)` |
| `source` | `TEXT` | `NOT NULL` |
| `url` | `TEXT` | `NOT NULL` |
| `format` | `TEXT` | `NOT NULL` |
| `version` | `TEXT` | |
| `retrieved_at` | `TEXT` | |
| `created_at` | `TEXT` | `NOT NULL` |
| | | `UNIQUE(publication_id, url)` |

### `download_days`

| Column | Type | Constraints |
|--------|------|-------------|
| `id` | `INTEGER` | `PRIMARY KEY AUTOINCREMENT` |
| `source` | `TEXT` | `NOT NULL` |
| `date` | `TEXT` | `NOT NULL` |
| `status` | `TEXT` | `NOT NULL` |
| `record_count` | `INTEGER` | Default `0` |
| `downloaded_at` | `TEXT` | `NOT NULL` |
| `last_verified_at` | `TEXT` | |
| | | `UNIQUE(source, date)` |

Rows are upserted by `sync()` inside the day's transaction, with `ON CONFLICT (source, date) DO UPDATE`. `record_count` records how many records were **stored** (added + merged), which can be lower than the number fetched if individual records failed.
