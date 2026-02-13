# bmlib.publications — Publication Ingestion and Sync

Publication ingestion, deduplication, storage, and multi-source sync for biomedical literature. Fetches records from PubMed, bioRxiv, medRxiv, and OpenAlex, stores them in a unified schema with automatic deduplication by DOI and PMID.

## Installation

```bash
pip install bmlib[publications]
```

Requires `httpx` for HTTP requests to external APIs.

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
    FullTextSource,
    DownloadDay,

    # Storage operations
    store_publication,
    get_publication_by_doi,
    get_publication_by_pmid,
    add_fulltext_source,

    # Schema
    ensure_schema,
)
```

---

## Data Models

### `Publication`

A biomedical publication record.

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
| `doi` | `str \| None` | Digital Object Identifier. |
| `pmid` | `str \| None` | PubMed ID. |
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

### `FullTextSource`

A full-text source for a publication (e.g. PMC XML, publisher PDF).

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
| `record_count` | `int` | Number of records fetched. |
| `downloaded_at` | `datetime` | When the fetch was performed. |
| `last_verified_at` | `datetime \| None` | When the data was last verified/re-fetched. |

---

### `FetchResult`

Result of fetching records from a source for a given date.

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

Progress report during a sync operation.

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

---

## Schema

### `ensure_schema`

```python
def ensure_schema(conn: Any) -> None
```

Create all publications tables if they do not exist. Creates three tables:

- **`publications`** — Core publication records with unique indexes on `doi` and `pmid`, and an index on `publication_date`.
- **`fulltext_sources`** — Full-text source URLs linked to publications. Unique on `(publication_id, url)`.
- **`download_days`** — Tracks which source/date combinations have been fetched. Unique on `(source, date)`.

Called automatically by `sync()`. Call manually if you need the schema before syncing.

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
1. Look up existing record by DOI (if present).
2. If not found, look up by PMID (if present).
3. If found, **merge** the incoming record into the existing one.
4. If not found, **insert** as a new record.

**Merge behaviour:**
- Appends new sources to the existing sources list.
- Fills `NULL` fields from the incoming record (COALESCE logic).
- Never overwrites existing non-NULL fields.
- Authors, publication_types, and keywords: keeps existing if non-empty, otherwise takes incoming.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `conn` | `Any` | *(required)* | A DB-API connection with the publications schema. |
| `pub` | `Publication` | *(required)* | The publication to store. |
| `fulltext_sources` | `Sequence[FullTextSource] \| None` | `None` | Optional full-text sources to associate with the publication. |

**Returns:** `"added"` for a new record, `"merged"` for an updated existing record.

**Example:**

```python
pub = Publication(
    title="A Novel Study",
    doi="10.1234/example",
    pmid="12345678",
    abstract="We investigated...",
    authors=["Smith, John", "Doe, Jane"],
    journal="Nature",
    publication_date="2025-01-15",
    sources=["pubmed"],
    first_seen_source="pubmed",
)
result = store_publication(conn, pub)
print(result)  # "added" or "merged"
```

---

### `get_publication_by_doi`

```python
def get_publication_by_doi(conn: Any, doi: str) -> Publication | None
```

Look up a publication by DOI. Returns `None` if not found.

---

### `get_publication_by_pmid`

```python
def get_publication_by_pmid(conn: Any, pmid: str) -> Publication | None
```

Look up a publication by PMID. Returns `None` if not found.

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

Add a full-text source for a publication.

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
    email: str,
    api_keys: dict[str, str] | None = None,
    on_record: Callable[[dict], None] | None = None,
    on_progress: Callable[[SyncProgress], None] | None = None,
    recheck_days: int = 0,
) -> SyncReport
```

Orchestrate syncing publications from multiple sources. Automatically creates the schema, determines which days need fetching, fetches records, deduplicates, and stores results.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `conn` | `Any` | *(required)* | A DB-API connection. |
| `sources` | `list[str] \| None` | `None` | Source names to sync. Defaults to all: `["pubmed", "biorxiv", "medrxiv", "openalex"]`. |
| `date_from` | `date \| None` | `None` | Start date (inclusive). Defaults to yesterday. |
| `date_to` | `date \| None` | `None` | End date (inclusive). Defaults to today. |
| `email` | `str` | *(required)* | Contact email for polite API access (required by OpenAlex and CrossRef). |
| `api_keys` | `dict[str, str] \| None` | `None` | Optional dict mapping source names to API keys (e.g. `{"pubmed": "your_ncbi_key"}`). |
| `on_record` | `Callable \| None` | `None` | Optional callback invoked with each raw record dict. |
| `on_progress` | `Callable \| None` | `None` | Optional callback invoked with `SyncProgress` updates. |
| `recheck_days` | `int` | `0` | If > 0, re-fetch completed days older than this many days. |

**Returns:** `SyncReport`

**Smart fetching logic:**
- Today's date is always re-fetched (to catch late additions).
- Days with `status="completed"` are skipped unless `recheck_days` triggers a re-check.
- Days with `status="failed"` are always retried.
- Days with no download record are fetched.

**Example:**

```python
from datetime import date
from bmlib.db import connect_sqlite
from bmlib.publications import sync

conn = connect_sqlite("publications.db")

# Sync last week from PubMed and bioRxiv
report = sync(
    conn,
    sources=["pubmed", "biorxiv"],
    date_from=date(2025, 6, 1),
    date_to=date(2025, 6, 7),
    email="researcher@example.com",
    api_keys={"pubmed": "your_ncbi_api_key"},
)

print(f"Sources: {report.sources_synced}")
print(f"Days processed: {report.days_processed}")
print(f"Records added: {report.records_added}")
print(f"Records merged: {report.records_merged}")
print(f"Errors: {report.errors}")

# Sync with progress reporting
def on_progress(progress):
    pct = (progress.records_processed / max(progress.records_total, 1)) * 100
    print(f"  {progress.source} {progress.date}: {pct:.0f}% ({progress.records_processed}/{progress.records_total})")

report = sync(
    conn,
    sources=["pubmed"],
    date_from=date(2025, 6, 1),
    date_to=date(2025, 6, 1),
    email="researcher@example.com",
    on_progress=on_progress,
)

# Re-check old data (re-fetch anything older than 30 days)
report = sync(
    conn,
    date_from=date(2025, 1, 1),
    date_to=date(2025, 6, 30),
    email="researcher@example.com",
    recheck_days=30,
)
```

---

## Source Fetchers

Each source has a dedicated fetcher module. Fetchers are used internally by `sync()` but can be called directly for advanced use cases.

### Available Sources

| Source | Module | API |
|--------|--------|-----|
| `pubmed` | `bmlib.publications.fetchers.pubmed` | NCBI E-utilities (esearch + efetch) |
| `biorxiv` | `bmlib.publications.fetchers.biorxiv` | bioRxiv API |
| `medrxiv` | `bmlib.publications.fetchers.biorxiv` | medRxiv API (same endpoint as bioRxiv) |
| `openalex` | `bmlib.publications.fetchers.openalex` | OpenAlex API |

### `fetch_pubmed`

```python
def fetch_pubmed(
    client: Any,
    target_date: date,
    *,
    on_record: Callable[[dict], None],
    on_progress: Callable[[SyncProgress], None] | None = None,
    api_key: str | None = None,
) -> FetchResult
```

Fetch all PubMed articles published on `target_date` using NCBI E-utilities.

- Uses ESearch to find PMIDs, then EFetch to retrieve full XML.
- Parses PubmedArticle XML into record dicts.
- Pages through results in batches of 500.
- Rate limits: 0.1s with API key, 0.34s without.
- Extracts: PMID, title, abstract (multi-part), authors, journal, DOI, PMC ID, MeSH keywords, full-text source URLs.

### `fetch_biorxiv`

```python
def fetch_biorxiv(
    client: Any,
    target_date: date,
    *,
    on_record: Callable[[dict], None],
    on_progress: Callable[[SyncProgress], None] | None = None,
    server: str = "biorxiv",
    api_key: str | None = None,
) -> FetchResult
```

Fetch preprint records from the bioRxiv/medRxiv API for a single date.

- Uses `api.biorxiv.org/details/{server}/{date}/{date}/{cursor}`.
- Pages through results in batches of 100.
- Rate limits: 0.5s between pages.
- Extracts: DOI, title, authors (semicolon-separated), abstract, date, category, PDF URL, JATS XML URL.

### `fetch_openalex`

```python
def fetch_openalex(
    client: Any,
    target_date: date,
    *,
    on_record: Callable[[dict], None],
    on_progress: Callable[[SyncProgress], None] | None = None,
    email: str,
    api_key: str | None = None,
) -> FetchResult
```

Fetch all OpenAlex works published on `target_date`.

- Uses cursor-based pagination (`per_page=200`).
- Reconstructs abstracts from OpenAlex's inverted-index format.
- Rate limits: 0.1s between pages.
- Extracts: DOI, PMID, title, authors, journal, abstract, publication date, keywords (primary topic), open access status, license, full-text source URLs with versions.

---

## Record Dict Format

All fetchers produce normalised record dicts with these common keys:

```python
{
    "title": str,
    "doi": str | None,
    "pmid": str | None,
    "abstract": str | None,
    "authors": list[str],
    "journal": str | None,
    "publication_date": str | None,
    "publication_types": list[str],
    "keywords": list[str],
    "is_open_access": bool,
    "license": str | None,
    "fulltext_sources": list[dict],  # [{"url": ..., "source": ..., "format": ...}]
    "source": str,                    # "pubmed", "biorxiv", "medrxiv", "openalex"
}
```

---

## Database Schema

The publications module creates three tables:

### `publications`

| Column | Type | Constraints |
|--------|------|-------------|
| `id` | `INTEGER` | `PRIMARY KEY AUTOINCREMENT` |
| `doi` | `TEXT` | `UNIQUE` (partial, where not null) |
| `pmid` | `TEXT` | `UNIQUE` (partial, where not null) |
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
