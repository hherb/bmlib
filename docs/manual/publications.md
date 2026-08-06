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
| `models` | `Publication`, `FullTextSource`, `DownloadDay`, `FetchResult`, `SyncProgress`, `SyncReport`, `FetchedRecord`, `SourceDescriptor`, `SourceParam`, `RetractionNature`, `RetractionNotice` | Data models |
| `schema` | `SCHEMA_SQL`, `ensure_schema()` | Table definitions |
| `storage` | `store_publication()`, `get_publication_by_doi()`, `get_publication_by_pmid()`, `add_fulltext_source()` | De-duplicating writes and lookups |
| `sync` | `sync()` | The sole orchestration entry point |
| `retractions` | `parse_retraction_watch_csv()`, `store_retraction_notices()`, `lookup_retractions()`, `is_retracted()` | Retraction Watch notices — see [Retractions](#retractions) |
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
    Grant,
    AuthorAffiliation,
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
    get_grants,
    get_author_affiliations,

    # Schema
    ensure_schema,

    # Retraction Watch notices
    RetractionNature,
    RetractionNotice,
    parse_retraction_watch_csv,
    store_retraction_notices,
    lookup_retractions,
    is_retracted,
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

    pmcid: str | None = None      # declared last on purpose — see below
```

> **`pmcid` is declared last, not next to `pmid` (0.6.0).** New fields are appended so that positional construction keeps working: inserting one in the middle silently shifts every later argument, and a caller's `abstract` would land in `pmcid` with nothing raised at any layer. If you add a field to this dataclass, append it.

| Field | Type | Description |
|-------|------|-------------|
| `title` | `str` | Paper title. *(required)* |
| `sources` | `list[str]` | List of source names that provided this record (e.g. `["pubmed", "openalex"]`). *(required)* |
| `first_seen_source` | `str` | The first source to provide this record. *(required)* |
| `doi` | `str \| None` | Digital Object Identifier. Normalised on store — see [Identifier normalisation](#identifier-normalisation). |
| `pmid` | `str \| None` | PubMed ID. Whitespace-stripped on store. |
| `pmcid` | `str \| None` | PubMed Central ID, e.g. `"PMC1234567"`. Populated from `FetchedRecord.pmc_id` during sync; useful for full-text retrieval. Not used for deduplication. *(new in 0.6.0)* |
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

    # -- Declared last, for positional stability --
    grants: list[Grant] = field(default_factory=list)
    author_affiliations: list[AuthorAffiliation] = field(default_factory=list)
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
| `grants` | `list[Grant]` | Funding awards. Only `fetch_pubmed` populates these. |
| `author_affiliations` | `list[AuthorAffiliation]` | Author affiliations. Only `fetch_pubmed` populates these. |

`sync()` maps `FetchedRecord` → `Publication` by copying the core fields and setting both `sources=[record.source]` and `first_seen_source=record.source`. `grants` and `author_affiliations` are persisted separately, into their own tables (see [`Grant` and `AuthorAffiliation`](#grant-and-authoraffiliation)). Note that `pmc_id` and `extras` are **not** persisted — if you need them, capture them in an `on_record` callback.

The last two fields are declared after `extras` rather than beside the content fields they read best beside, because downstream projects construct `FetchedRecord` positionally: inserting a field anywhere but the end shifts every later argument silently.

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

### `Grant` and `AuthorAffiliation`

Funding awards and author affiliations, parsed from PubMed's `<GrantList>` and `<AffiliationInfo>`. Like `FullTextSource`, these are **child rows** of a publication rather than columns on it, stored in `publication_grants` and `publication_affiliations`.

```python
@dataclass
class Grant:
    agency: str | None = None
    grant_id: str | None = None
    country: str | None = None
    source: str = ""            # which source asserted this; scopes storage
    publication_id: int = 0     # ignored on the way in; see below
    id: int | None = None

@dataclass
class AuthorAffiliation:
    author: str
    affiliation: str
    position: int = 0           # 0-based index in <AuthorList>
    source: str = ""
    publication_id: int = 0
    id: int | None = None
```

Every field of a `Grant` is optional because PubMed's own records are: an award may name an agency with no id, or an id with no country. A grant naming **neither** an agency nor an id is dropped at parse time — it identifies no award. Exact repeats are collapsed there too: PubMed really does emit a `<Grant>` block verbatim twice (31 of 575 entries measured across 200 NIH-funded records), and stored separately they inflate any count of a paper's funders.

`source` names the publication source that asserted the row, and it is what **scopes storage** — see [`store_publication`](#store_publication). `sync()` fills it in from the record's own source, so a fetcher cannot forget it; a caller reaching `store_publication()` directly must set it.

`AuthorAffiliation` is one row per *(author, affiliation)* pair, so an author listing three institutions produces three rows. `author` is formatted exactly as `Publication.authors` formats it (`"Last, Fore"`), so the two can be matched **by name**. `position` is the author's index in the `<AuthorList>`, carried because first-author and senior-author affiliations are the ones a conflict-of-interest check cares about and the name alone cannot recover the ordering.

`affiliation` is Markdown, read by the same walker as titles and abstracts (an `<Affiliation>` shares `<ArticleTitle>`'s content model and can carry a superscript footnote marker), so it is escaped like any other prose — see [Titles and abstracts are Markdown](#titles-and-abstracts-are-markdown). That matters more here than for a title, because this column is a join key: matching it against an institution name obtained elsewhere must compare against the escaped form. Only `publication_id` is indexed, so a search *by* institution is a table scan until you add an index suiting your backend.

> **`authors[a.position]` is the wrong way to resolve an affiliation's author.** `position` counts every `<Author>` element, while `authors` omits `<CollectiveName>` consortia, which have no personal name — so the two lists differ in length whenever a consortium is present. Match on `author` instead. (A consortium's own affiliation, if it states one, is not recorded.)

Neither model's `publication_id` is read on the way in: `store_publication()` takes the id from the publication being stored and does **not** write it back onto the object you passed, unlike its `pub` argument. Read the persisted form back with `get_grants()` / `get_author_affiliations()`, whose results carry `publication_id` and `id`.

Both are serialisable via `to_dict()` / `from_dict()`, and both are read back with [`get_grants()` / `get_author_affiliations()`](#get_grants-and-get_author_affiliations).

```python
from bmlib.publications import get_author_affiliations, get_grants

for grant in get_grants(conn, pub.id):
    print(grant.agency, grant.grant_id)

industry = [a for a in get_author_affiliations(conn, pub.id) if "Pfizer" in a.affiliation]
```

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

Create all publications tables if they do not exist, delegating to `bmlib.db.create_tables`. Creates:

- **`publications`** — Core publication records, with unique **partial** indexes on `doi` and `pmid` (each `WHERE ... IS NOT NULL`, so any number of rows may have a `NULL` identifier) and a plain index on `publication_date`.
- **`fulltext_sources`** — Full-text source URLs linked to publications. Unique on `(publication_id, url)`.
- **`download_days`** — Tracks which source/date combinations have been fetched. Unique on `(source, date)`.
- **`retraction_notices`** — Retraction Watch notices, unique on `record_id`. See [Retractions](#retractions).
- **`publication_grants`** and **`publication_affiliations`** — funding awards and author affiliations, each carrying a `source` column and indexed on `publication_id`.

Neither of the last two carries a UNIQUE constraint on its natural key, and that is deliberate: every column of a grant proper is nullable, and both backends treat `NULL` as *distinct* in a unique index, so `UNIQUE(publication_id, source, agency, grant_id)` would let `(1, 'pubmed', NULL, 'R01')` insert twice — protecting nothing while appearing to. An expression index over `COALESCE`d columns would work on both backends, but there is nothing left for it to catch: repeats are collapsed at parse time and [`store_publication`](#store_publication) is idempotent per source, and both of those are reachable from a test in a way an index written around three nullable columns is not.

Called automatically by `sync()`. Call manually if you need the schema before syncing. The raw DDL is available as `bmlib.publications.schema.SCHEMA_SQL` (SQLite) and `SCHEMA_SQL_POSTGRESQL`; `ensure_schema()` picks the matching one. The two differ only where the dialects do — surrogate keys and booleans. Everything the storage layer leans on exists in both.

> **Call this once after upgrading bmlib to 0.6.0.** `CREATE TABLE IF NOT EXISTS` is a no-op against a database an earlier bmlib created, so a column added since then has to be applied explicitly. `ensure_schema()` reconciles against the live column list and adds what is missing — currently just `pmcid`.
>
> Reads tolerate a database that has not been through it: storage treats a post-release column as absent rather than raising. **Writes do not** — `store_publication()` names every column in its INSERT and will fail on one the database lacks. `sync()` calls `ensure_schema()` for you; code that goes straight to `store_publication()` must call it itself.

**Example:**

```python
from bmlib.db import connect_postgresql, connect_sqlite
from bmlib.publications import ensure_schema

conn = connect_sqlite("publications.db")
ensure_schema(conn)

# Or against PostgreSQL — same call, matching DDL chosen for you.
conn = connect_postgresql(dsn="host=localhost dbname=papers user=app")
ensure_schema(conn)
```

---

## Storage Operations

All storage functions take a DB-API connection as the first argument and operate on the publications schema.

> **Both backends are supported (0.6.0).**
> `schema.py`, `storage.py` and `sync.py` were SQLite-only until recently — `?` placeholders, `UPDATE OR IGNORE`, `cur.lastrowid`, `AUTOINCREMENT` — even though `bmlib.db` supported PostgreSQL all along. Every statement is now written for both, `ensure_schema()` picks the matching DDL, and `tests/test_backends.py` runs each of its cases against both. Pass either connection type.
>
> The one irreducibly dialect-specific need is reading back an inserted row's id: `cur.lastrowid` on SQLite, `RETURNING id` on PostgreSQL. Everything else is written in the intersection of the two dialects.

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
3. The drop row's grants and affiliations move to the keep row **per source**: a source the keep row already has wins and the drop row's rows for it are discarded, while a source only the drop row saw moves across. That is the same "fill, never overwrite" rule the field merge follows, at source granularity — merging two rows' accounts of what PubMed said would produce a set PubMed never asserted, while a source the keep row has never seen is real information it should gain.
4. The drop row's data is snapshotted, the drop row is deleted — freeing its unique identifier — and the snapshot is merged into the keep row.
5. The keep row is re-read, and the incoming record is merged into it as usual. The call returns `"merged"`.

Steps 2 and 3 must both complete before step 4: both backends enforce foreign keys, so any child row still pointing at the drop row makes its `DELETE` raise and aborts the entire store.

The whole consolidation runs inside `store_publication()`'s transaction, so a failure part-way through rolls back completely and cannot lose the drop row's data.

---

### `store_publication`

```python
def store_publication(
    conn: Any,
    pub: Publication,
    fulltext_sources: Sequence[FullTextSource] | None = None,
    *,
    grants: Sequence[Grant] | None = None,
    affiliations: Sequence[AuthorAffiliation] | None = None,
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
7. Apply `grants` and `affiliations` against the resulting row id.

**Merge behaviour:**
- Appends new source names to the existing `sources` list (no duplicates).
- Fills `NULL` fields from the incoming record via `COALESCE`; never overwrites an existing non-`NULL` value.
- `authors`, `publication_types`, `keywords`: keeps the existing list if non-empty, otherwise takes the incoming one.
- `is_open_access`: can only be upgraded from `0` to the incoming value, never downgraded.
- `updated_at` is set to now.

**Grants and affiliations are replace-per-source.** Supplying rows replaces the stored rows **for each source those rows name**, and leaves every other source's alone; supplying none leaves everything untouched. So re-syncing PubMed replaces PubMed's grants and does not disturb OpenAlex's.

This is the one rule to understand about these two tables, and it differs from `fulltext_sources`, which simply accumulates — a paper genuinely has several full-text URLs, whereas each source states that paper's funding completely and its statement should supersede its own previous one rather than pile up.

The design makes three things true at once:

| | |
|---|---|
| Re-syncing a day is **idempotent** | delete-then-insert within the source, so no duplicates accumulate |
| A corrected record is **self-correcting** | PubMed's new set supersedes PubMed's stale set |
| Sources **coexist** | scoping by source means the last sync no longer wins outright |

That last one was a real defect before the `source` column existed: with replacement scoped by publication alone, PubMed's grants replaced OpenAlex's and then OpenAlex's replaced PubMed's, flip-flopping on every sync with no error and no warning. OpenAlex's API does carry funder data, so this was not hypothetical.

The "supplying none leaves it alone" half is separate and still needed: an absent `<GrantList>` means the record did not carry the data, not that the funding was withdrawn — and with no rows there is no source to scope a delete to anyway.

**Transactions:** the whole store (row consolidation, insert/merge, full-text
sources, grants and affiliations) is one atomic transaction. Standalone calls
commit on return; calls made inside a caller's `transaction(conn)` block join
it via a savepoint, deferring the commit to the caller (this is how `sync()`
batches a whole day into one commit — see `bmlib.db.transaction`).

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `conn` | `Any` | *(required)* | A DB-API connection with the publications schema. |
| `pub` | `Publication` | *(required)* | The publication to store. **Mutated in place** to hold normalised identifiers. |
| `fulltext_sources` | `Sequence[FullTextSource] \| None` | `None` | Optional full-text sources to associate with the publication. Their `publication_id` is ignored; the stored row's id is used. They accumulate across calls. |
| `grants` | `Sequence[Grant] \| None` | `None` | Keyword-only. Funding awards. Replaces the stored rows for each `source` they name; leaves other sources', and everything, alone if empty. Every row **must** name a source — see below. |
| `affiliations` | `Sequence[AuthorAffiliation] \| None` | `None` | Keyword-only. Author affiliations, same replace-per-source rule. |

**A row naming no source raises `ValueError`.** Scoping is the whole mechanism, so an unnamed row is not merely unlabelled — it is unreachable: no later sync can name it, so it can never be replaced, and each subsequent sync stacks a correctly-labelled duplicate beside it. `sync()` stamps the source for you from the record's own (`_stamp_source`), so this only concerns callers reaching `store_publication` directly. Note that `Grant.source` and `AuthorAffiliation.source` **default to `""`**, which is why the check lives in the storage layer rather than being left to the `NOT NULL` column — the column rejects `None`, but the value a forgetful caller actually produces is the empty string.

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

### `get_grants` and `get_author_affiliations`

```python
def get_grants(conn: Any, publication_id: int) -> list[Grant]
def get_author_affiliations(conn: Any, publication_id: int) -> list[AuthorAffiliation]
```

Read back what [`store_publication`](#store_publication) persisted for one publication. Both return an empty list when nothing was stored. Affiliations come back ordered by `position`, so the first and senior authors are at the ends.

Rows from every source come back together — filter on `source` when you want one source's account of the paper.

```python
pub = get_publication_by_pmid(conn, "12345678")
funders = {g.agency for g in get_grants(conn, pub.id) if g.agency}
per_source = {g.source for g in get_grants(conn, pub.id)}   # e.g. {"pubmed"}

first_author_affiliations = [
    a.affiliation for a in get_author_affiliations(conn, pub.id) if a.position == 0
]
```

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
- Fields dropped during storage — `pmc_id`, `extras` — are still present here. This is the only place to capture them. (`grants` and `author_affiliations` *are* persisted, into their own tables.)

What a callback **must not** assume:

- That the record exists in the database. It does not yet; it is only buffered.
- That `Publication.id` is available, or that `get_publication_by_doi()` will find the record.
- That being called means the record will be stored — a later per-record failure, or a failure writing the day-status row, can roll it back.
- That raising is safe. An exception from `on_record` escapes into the fetcher's record loop and aborts the rest of that day's fetch — `fetch_biorxiv` converts it into a failed `FetchResult`, while `fetch_pubmed` and `fetch_openalex` let it propagate to `sync()`'s per-day handler. Either way the day is marked `"failed"` (and retried next run), though whatever was buffered before the throw is still stored. Catch your own exceptions.

To act on records **after** they are durably stored, do the work between `sync()` calls (for example one call per day, then query the database), rather than in `on_record`.

**Write batching — changed in 0.3.0:**

Each day's records are buffered in memory during the fetch and stored in one
batch afterwards, inside a single per-day transaction. Consequences:

- Writes cost one commit per synced day instead of one per statement, and
  SQLite's write lock is never held across network I/O.
- `on_record` fires while the fetcher streams, **before** the record is
  stored — the callback must not expect to read the record back from the
  database. Use it for progress display, filtering statistics, or side
  channels, not for read-after-write.
- A day's records are held in memory for the duration of that day's fetch.
- The day's `download_days` status row commits atomically with the day's
  records: a crash mid-day leaves the day marked incomplete and it is
  re-fetched next run.

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
- Extracts: PMID, title, abstract, authors, journal, DOI, PMC ID, MeSH keywords, and full-text source URLs (PMC article page, DOI resolver). It also populates `publication_types` from `PublicationTypeList`, which is what the free Tier 1 quality filter classifies study design from (see [quality.md](quality.md)). Before 0.4.0 this field was left empty, so synced PubMed records skipped the free tier entirely.
- Populates `grants` from `<GrantList>` and `author_affiliations` from `<AffiliationInfo>`; `sync()` persists both (see [`Grant` and `AuthorAffiliation`](#grant-and-authoraffiliation)). It is the only built-in fetcher that produces either.
- Takes **no** `email` parameter.

#### Titles and abstracts are Markdown

Titles and abstracts preserve PubMed's inline markup, mapped to Markdown: `<b>`/`<bold>` → `**x**`, `<i>`/`<italic>` → `*x*`, `<sup>` → `^x^`, `<sub>` → `~x~`. An unrecognised tag contributes its text undecorated, and so does `<u>`/`<underline>` — see below.

Each `AbstractText` becomes one section, separated from the next by a blank line. Its label comes from the `Label` attribute, falling back to `NlmCategory` (except the placeholders `UNASSIGNED` and `UNLABELLED`), and renders as a bold upper-case heading:

```markdown
**BACKGROUND:** Levels of CO~2~ rose over 10 m^2^ plots.

**METHODS:** We conducted a randomised trial.
```

Four things to know:

- **`~x~` and `^x^` are Pandoc extensions, not CommonMark.** A renderer without them shows the tildes and carets literally. The alternative was worse: flattening the markup away renders both `CO<sub>2</sub>` and `CO<sup>2</sup>` as an ambiguous `CO2`.
- **The source text is escaped, so these are real Markdown, not Markdown-ish text.** `\`, `` ` ``, `*`, `~` and `^` taken from the document are backslash-escaped; the markers the fetcher inserts are not. Without this, declaring the field Markdown would corrupt values that were fine before — `CYP2C19 (*1, *2, *3, *17 alleles)`, the standard star-allele notation, renders as `(<em>1, </em>2, …)`, and the `~` of "AUC ~ 0.80" pairs with the next one to subscript half a sentence. The set is measured rather than assumed: across 3,403 real titles and abstract sections it alters 0.35% of them and removes every construct a CommonMark parser found in the unescaped text, whereas also escaping `_` and `[`/`]` churned 4.3% and fixed nothing further. Intraword `_` is inert in CommonMark, so gene names like `TP53_R175H` are safe unescaped, and a bare `[...]` — common in PubMed's `[This corrects the article …]` — is not a link.
- **`<u>` is dropped rather than mapped.** Markdown has no underline: `__x__` is *strong* emphasis, so mapping `<u>` to it would render underlined text identically to `<b>` while asserting the source said "bold". That is the ambiguity `~`/`^` exist to avoid, and underline — unlike a subscript — is presentational, so losing it costs nothing a reader needs.
- **Values are not comparable with those stored before this release.** Titles changed because they were previously truncated at their first markup tag — `"Effects of H<sub>2</sub>O and <i>E. coli</i> on outcomes"` was stored as `"Effects of H"`. Abstracts changed because they gain the recovered `NlmCategory` labels, the blank-line section breaks, and the sub/superscript notation. Re-sync, or accept a mix.

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

Core fields are guaranteed present (possibly `None`/empty); source-specific
data goes in `extras`.

---

## Retractions

A biomedical literature tool must not present a retracted paper as evidence.
`bmlib.publications.retractions` answers "is this paper retracted?" against
the Retraction Watch database, which Crossref distributes as a CC0 CSV at
`https://api.labs.crossref.org/data/retractionwatch?<mailto>`.

> **Not a registered source fetcher.** Every fetcher in this module produces
> `FetchedRecord`s for a single date, feeding `sync()`'s date-keyed loop. A
> Retraction Watch export is one bulk file with no date to iterate, and its
> rows are annotations about papers that are usually **not** in your
> `publications` table at all. `retractions.py` is deliberately a separate,
> standalone module — call its functions directly rather than through `sync()`.

> **Get the file yourself; give it minutes.** `parse_retraction_watch_csv()`
> takes a path or an already-open file — it does not download anything.
> Crossref's endpoint is CC0 but slow: the file is 65 MB (71,306 real rows,
> followed by 190 entirely empty ones), and it has been observed to take
> several minutes and to occasionally return a `504 Gateway Time-out`. Fetch
> it with a client that has a generous timeout, save it to disk, then hand
> the path to `parse_retraction_watch_csv()`.

### Two identifier pairs, named as such

A Retraction Watch row describes **two** papers — the paper that was
retracted, and the notice announcing it — and the export carries a column
pair for each. `RetractionNotice` keeps them apart by name:

| Attribute | Means |
|---|---|
| `doi`, `pmid` | The **retracted paper** (the export's `OriginalPaperDOI` / `OriginalPaperPubMedID`). This is what you look a paper up by. |
| `notice_doi`, `notice_pmid` | The **retraction notice itself** (`RetractionDOI` / `RetractionPubMedID`). Sometimes equal to the paper's own identifiers, sometimes not. |

### `RetractionNature`

```python
class RetractionNature(StrEnum):
    RETRACTION = "retraction"
    CORRECTION = "correction"
    EXPRESSION_OF_CONCERN = "expression_of_concern"
    REINSTATEMENT = "reinstatement"
    OTHER = "other"

    @classmethod
    def from_raw(cls, value: str | None) -> RetractionNature: ...
```

`from_raw()` maps the export's `RetractionNature` cell (e.g. `"Expression of
concern"`, lower-case `c` in the live file) case-insensitively onto the enum.
An unrecognised or empty value maps to `OTHER` rather than raising —
Retraction Watch's vocabulary can grow, and the current export exercises only
the first four values (Retraction 66,062 / Expression of concern 3,585 /
Correction 1,499 / Reinstatement 160, measured against the 2026-08-03 export;
no row falls outside them). The original string survives regardless, in
`RetractionNotice.raw_nature`.

**An unrecognised value is logged at `WARNING`, once per distinct value.**
`is_retracted()` treats `OTHER` as evidence of neither retraction nor
reinstatement, so if Retraction Watch ever reworded `"Retraction"`, an import
would succeed, store all 66,062 of them as `OTHER`, and answer "not
retracted" for every paper in the file. That is the worst failure this
feature can have, and the warning is what makes it visible. It is emitted per
distinct value rather than per row, so vocabulary drift costs a handful of
log lines rather than 66,000.

Note the asymmetry with the *read* path: `RetractionNotice.from_dict()` and
`lookup_retractions()` use the strict `RetractionNature(...)` constructor and
**raise** on a value they do not know. That is deliberate. A value in the CSV
comes from a vocabulary bmlib does not own, so an unknown one must cost a row
rather than the import; a value in the `nature` column was written by bmlib
itself, so an unknown one means the database was written by a newer version —
and mapping it to `OTHER` there would turn a loud, accurate error into a
silent "not retracted".

### `RetractionNotice`

```python
@dataclass
class RetractionNotice:
    record_id: str
    nature: RetractionNature

    doi: str | None = None
    pmid: str | None = None
    notice_doi: str | None = None
    notice_pmid: str | None = None
    title: str | None = None
    journal: str | None = None
    retraction_date: str | None = None
    original_paper_date: str | None = None
    reasons: list[str] = field(default_factory=list)
    raw_nature: str | None = None
```

| Field | Type | Description |
|-------|------|-------------|
| `record_id` | `str` | Retraction Watch's own primary key (the export's `Record ID`). *(required)* |
| `nature` | `RetractionNature` | The typed kind of notice. *(required)* |
| `doi` / `pmid` | `str \| None` | The retracted paper's identifiers. |
| `notice_doi` / `notice_pmid` | `str \| None` | The notice's own identifiers. |
| `title` / `journal` | `str \| None` | The retracted paper's title and journal. |
| `retraction_date` / `original_paper_date` | `str \| None` | ISO `yyyy-mm-dd` strings, matching `Publication.publication_date`. |
| `reasons` | `list[str]` | Individual reasons from the `Reason` cell. |
| `raw_nature` | `str \| None` | The export's own wording for `nature`, unmodified. |

Serialisable via `to_dict()` / `from_dict()`, same convention as every other
model in this module.

### `parse_retraction_watch_csv`

```python
def parse_retraction_watch_csv(
    source: str | Path | IO[bytes],
    *,
    on_skip: Callable[[int, str], None] | None = None,
) -> Iterator[RetractionNotice]
```

Stream `RetractionNotice` records from a Retraction Watch CSV. The file is
read lazily — 71,306 rows is not worth materialising as dicts — and `source`
may be a path or an already-open **seekable binary** stream.

A text stream (`open(path)` without the `"b"`) or a non-seekable one raises
`ValueError` **at the call**, not at the first iteration:
`parse_retraction_watch_csv()` is deliberately not itself a generator, since
the documented usage feeds its iterator straight to
`store_retraction_notices()` and a deferred raise would surface from inside
that function's open transaction rather than at the call that got the
argument wrong. Opening a *path* stays lazy, so that a caller who never
iterates does not leak a file handle.

**Encoding.** The encoding is not guessed from a leading sample. Choosing one
that way can decode a leading chunk cleanly and still hit a single bad byte
tens of megabytes later, which a streaming `TextIOWrapper` cannot retry once
rows have already been handed to the caller. Instead, `parse_retraction_watch_csv()`
scans the **whole file** once through an incremental decoder — in fixed-size
chunks, so memory use does not scale with file size — trying `utf-8-sig`,
then `cp1252`, and falling back to `latin-1` if neither decodes it
end-to-end. `latin-1` accepts every byte, so this fallback is guaranteed to
succeed: the whole-file scan means an encoding is committed to only once it
is known to decode every row, so the mid-stream `UnicodeDecodeError` failure
mode cannot occur. The scan costs one extra read pass (well under a second
for a 65 MB file).

Plain `utf-8` is deliberately **not** in that chain. `utf-8-sig` decodes a
BOM'd file by stripping the BOM and a non-BOM'd file identically to plain
`utf-8`, so as a yes/no decodability test the two are equivalent and trying
`utf-8` after it could only ever lose. The ordering matters because on a
BOM'd file plain `utf-8` does not fail — it *succeeds* and glues the BOM onto
the first field name, silently hiding `Record ID`.

**Any fallback off `utf-8-sig` is logged at `WARNING`.** Because `cp1252` and
`latin-1` decode bytes that are not valid UTF-8 rather than rejecting them, a
single corrupt byte in an otherwise-UTF-8 export causes the whole file to be
re-read under an encoding that mis-renders every non-ASCII character in
66,000 titles, journals and reasons — an import that looks completely
successful. The warning is the only thing that distinguishes it from one.

**Malformed CSV.** An unclosed quote makes the `csv` module read to EOF
hunting for its close and trip the field-size limit, tens of thousands of
rows in. This raises `ValueError` naming the last line read whole, rather
than a bare `csv.Error` whose "field larger than field limit" reads as a
bmlib bug rather than a corrupt download.

**Skipped rows.** `on_skip`, if given, is called as `on_skip(line_number, reason)`
for every row that cannot be used: no `Record ID` (the export's 190 trailing
empty rows), or no usable identifier for the retracted paper. Skips are also
logged at `DEBUG`. Measured against the live 2026-08-03 export, 5,189 of
71,306 real rows (7.28%) carry no usable identifier and are skipped; 66,117
are usable. Two sentinel values are recognised and treated as absent rather
than as real identifiers, because neither is falsy and a plain truthiness
check would collapse tens of thousands of unrelated rows onto one key:
`OriginalPaperPubMedID` of `"0"` (46.04% of rows) and `OriginalPaperDOI` of
`Unavailable` / `unavailable` in either casing (4.80% of rows).

**Example:**

```python
skipped = []
notices = list(
    parse_retraction_watch_csv(
        "retraction_watch.csv",
        on_skip=lambda line, reason: skipped.append((line, reason)),
    )
)
print(f"{len(notices)} usable notices, {len(skipped)} rows skipped")
```

### `store_retraction_notices`

```python
def store_retraction_notices(conn: Any, notices: Iterable[RetractionNotice]) -> int
```

Insert or refresh retraction notices, keyed by `record_id`. **Re-importing
the monthly export is idempotent**: `record_id` is Retraction Watch's own
primary key and carries a `UNIQUE` constraint, so writing the same file a
second time updates existing rows (`ON CONFLICT (record_id) DO UPDATE`)
rather than duplicating them — every column except `record_id` and
`created_at` is overwritten from the new file, since the CSV is the source of
truth. Identifiers are normalised with the same functions
`store_publication()` uses, so a DOI stored here matches any case or prefix
variant looked up later.

Runs inside one `transaction(conn)`: called standalone it commits once;
called inside a caller's open `transaction(conn)` block it joins that block
via a savepoint, as every other write in this module does.

**Returns:** the number of notices written.

### `lookup_retractions`

```python
def lookup_retractions(
    conn: Any,
    *,
    doi: str | None = None,
    pmid: str | None = None,
) -> list[RetractionNotice]
```

Return every stored notice about one paper, newest first (undated notices
last). A paper can have more than one notice — 2,354 papers do, in the live
export — so this returns a list; pass it to `is_retracted()` for the
boolean. Both `doi` and `pmid` are normalised before lookup, matching any
case or prefix variant, and supplying both matches a notice on **either**.

Raises `ValueError` if neither is given, **or if neither reduces to anything
usable**: blank, whitespace, a bare `https://doi.org/` prefix, or one of the
export's own "no identifier here" sentinels (`pmid="0"`, `doi="Unavailable"`
— the values 46.04% and 4.80% of the file's own cells carry). A lookup with
no usable identifier is a programming error, not an empty result: returning
`[]` would let a caller whose PMID column stores `"0"` for "absent" read a
paper it knows nothing about as not retracted, which is the worst failure
this feature can have.

### `is_retracted`

```python
def is_retracted(notices: Sequence[RetractionNotice]) -> bool
```

Decide whether a paper is currently retracted, from all its notices. Pure —
it takes notices, not a connection — so the rule is testable without a
database and re-derivable without re-importing 71,306 rows if it ever
changes.

**The rule:** scan the notices newest first; **only a Retraction or a
Reinstatement decides** — a Retraction makes the answer `True`, a
Reinstatement makes it `False`, and anything else (a Correction, an
Expression of Concern) is not evidence either way and is skipped over. No
decisive notice at all means not retracted.

This is deliberately *not* "the latest notice wins": a Correction issued
years after a Retraction does not undo it. In the live export, 52 papers are
retracted while carrying a later Correction or Expression of Concern as their
newest notice — a flat latest-wins reading would call every one of them
clean.

### Complete example

```python
from bmlib.db import connect_sqlite
from bmlib.publications import (
    ensure_schema,
    is_retracted,
    lookup_retractions,
    parse_retraction_watch_csv,
    store_retraction_notices,
)

conn = connect_sqlite("literature.db")
ensure_schema(conn)

skipped = []
notices = parse_retraction_watch_csv(
    "retraction_watch.csv", on_skip=lambda n, why: skipped.append((n, why))
)
print(f"stored {store_retraction_notices(conn, notices)} notices, skipped {len(skipped)} rows")

if is_retracted(lookup_retractions(conn, doi="10.1016/j.anbehav.2009.11.027")):
    print("retracted — do not cite as evidence")
```

---

## Database Schema

The publications module creates four tables. Types are given for both backends where they differ; the constraints are identical.

### `publications`

| Column | Type (SQLite) | Type (PostgreSQL) | Constraints |
|--------|---------------|-------------------|-------------|
| `id` | `INTEGER` | `SERIAL` | `PRIMARY KEY` (`AUTOINCREMENT` on SQLite) |
| `doi` | `TEXT` | `TEXT` | `UNIQUE` (partial index, `WHERE doi IS NOT NULL`) |
| `pmid` | `TEXT` | `TEXT` | `UNIQUE` (partial index, `WHERE pmid IS NOT NULL`) |
| `pmcid` | `TEXT` | `TEXT` | *(new in 0.6.0 — added to existing databases by `ensure_schema()`)* |
| `title` | `TEXT` | `TEXT` | `NOT NULL` |
| `abstract` | `TEXT` | `TEXT` | |
| `authors` | `TEXT` | `TEXT` | JSON array, default `'[]'` |
| `journal` | `TEXT` | `TEXT` | |
| `publication_date` | `TEXT` | `TEXT` | Indexed |
| `publication_types` | `TEXT` | `TEXT` | JSON array, default `'[]'` |
| `keywords` | `TEXT` | `TEXT` | JSON array, default `'[]'` |
| `is_open_access` | `INTEGER` | `BOOLEAN` | Default `0` / `FALSE`. A one-way latch: once any source reports open access, a later record cannot unset it. |
| `license` | `TEXT` | `TEXT` | |
| `sources` | `TEXT` | `TEXT` | JSON array, `NOT NULL`, default `'[]'` |
| `first_seen_source` | `TEXT` | `TEXT` | `NOT NULL` |
| `created_at` | `TEXT` | `TEXT` | `NOT NULL` |
| `updated_at` | `TEXT` | `TEXT` | `NOT NULL` |

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

### `retraction_notices`

| Column | Type (SQLite) | Type (PostgreSQL) | Constraints |
|--------|---------------|-------------------|-------------|
| `id` | `INTEGER` | `SERIAL` | `PRIMARY KEY` (`AUTOINCREMENT` on SQLite) |
| `record_id` | `TEXT` | `TEXT` | `NOT NULL UNIQUE` — Retraction Watch's own key, not partial like `publications.doi`/`.pmid` |
| `doi` | `TEXT` | `TEXT` | The retracted paper's DOI. Indexed, not unique — a paper can carry more than one notice. |
| `pmid` | `TEXT` | `TEXT` | The retracted paper's PMID. Indexed, not unique. |
| `notice_doi` | `TEXT` | `TEXT` | The notice's own DOI. |
| `notice_pmid` | `TEXT` | `TEXT` | The notice's own PMID. |
| `nature` | `TEXT` | `TEXT` | `NOT NULL` — the `RetractionNature` value. |
| `raw_nature` | `TEXT` | `TEXT` | The export's original wording. |
| `title` | `TEXT` | `TEXT` | |
| `journal` | `TEXT` | `TEXT` | |
| `retraction_date` | `TEXT` | `TEXT` | |
| `original_paper_date` | `TEXT` | `TEXT` | |
| `reasons` | `TEXT` | `TEXT` | JSON array, `NOT NULL`, default `'[]'` |
| `created_at` | `TEXT` | `TEXT` | `NOT NULL` |
| `updated_at` | `TEXT` | `TEXT` | `NOT NULL` |

Indexes: `idx_retraction_notices_doi`, `idx_retraction_notices_pmid` (both plain,
non-unique). Rows are upserted by `store_retraction_notices()` with
`ON CONFLICT (record_id) DO UPDATE`, the same full-`UNIQUE`-constraint
mechanism `download_days` uses — unlike `publications.doi`/`.pmid`, which are
nullable partial indexes and therefore looked up and merged by hand instead.
