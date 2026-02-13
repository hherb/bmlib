# Publications Module Design

## Overview

New `bmlib/publications/` module for publication ingestion, storage, deduplication, and daily sync tracking across four sources: PubMed, bioRxiv, medRxiv, and OpenAlex.

## Data Models

### Publication

Canonical record per paper. Discovery metadata only — full text tracking is separate.

Fields: `doi`, `pmid`, `title`, `abstract`, `authors` (JSON list), `journal`, `publication_date`, `publication_types` (JSON list), `keywords` (JSON list), `is_open_access`, `license`, `sources` (JSON list of discovery sources), `first_seen_source`, `created_at`, `updated_at`.

### FullTextSource

Multiple full text URLs per paper. A single paper may have preprint PDF from bioRxiv, final PDF from publisher, XML from PMC, and multiple OpenAlex locations.

Fields: `publication_id` (FK), `source`, `url`, `format` (pdf/html/xml), `version` (preprint/accepted/published), `retrieved_at` (NULL if not yet downloaded), `created_at`.

Unique constraint: `(publication_id, url)`.

### DownloadDay

Per-source per-day sync tracker with retention policy.

Fields: `source`, `date`, `status` (complete/partial/failed), `record_count`, `downloaded_at`, `last_verified_at`.

Unique constraint: `(source, date)`.

### SyncProgress

Callback payload for UI progress reporting.

Fields: `source`, `date`, `records_processed`, `records_total` (None if unknown), `status` (fetching/storing/complete/failed), `message`.

## Database Schema

- `publications` table with unique partial index on `doi` (WHERE doi IS NOT NULL), index on `pmid`, index on `publication_date`
- `fulltext_sources` table with unique constraint on `(publication_id, url)`, FK to publications
- `download_days` table with unique constraint on `(source, date)`
- All dates/times stored as ISO text for SQLite/PostgreSQL compatibility
- List fields stored as JSON text

## Deduplication Logic

Incoming record flow:
1. Has DOI? Look up by DOI. Found? Merge. Not found? Continue.
2. Has PMID? Look up by PMID. Found? Merge. Not found? Insert.
3. Neither? Insert without dedup.

Merge rules:
- Append new source to `sources` array (if not already present)
- Fill NULL fields from new record (never overwrite non-NULL)
- Update `updated_at`
- Add any new full text URLs to `fulltext_sources`

## Source Fetchers

Shared signature:
```python
def fetch_source(client, date, *, on_record, on_progress=None, api_key=None) -> FetchResult
```

Streaming `on_record` callback keeps memory flat. Optional `on_progress` callback for UI.

### PubMed
- NCBI E-utilities (esearch + efetch), XML response
- Paginate with retstart/retmax
- Rate limit: 3/sec (10/sec with API key)

### bioRxiv / medRxiv
- bioRxiv API `/details/{server}/{date}/{date}/{cursor}`
- JSON response, cursor pagination (100/page)
- Single implementation, server parameter selects biorxiv vs medrxiv

### OpenAlex
- Filter by publication_date, cursor pagination
- JSON response with inverted abstract index
- Multiple full text locations per record

## Sync Orchestrator

```python
def sync(conn, *, sources=None, date_from=None, date_to=None,
         email, api_keys=None, on_record=None, on_progress=None,
         recheck_days=0) -> SyncReport
```

Flow:
1. Ensure schema exists
2. Per source, find days needing fetch (gaps, failures, today always, retention re-checks)
3. Per (source, day): fetch, normalize, dedup/store in a transaction
4. Return SyncReport (sources_synced, days_processed, records_added, records_merged, records_failed, errors)

Today is always re-fetched. Failed days are retried. Each day is atomic (transaction rollback on failure).

## Module Structure

```
bmlib/publications/
    __init__.py          # Public API
    models.py            # Dataclasses
    schema.py            # DDL + ensure_schema()
    storage.py           # store_publication(), dedup, queries
    fetchers/
        __init__.py      # FetchResult, registry
        pubmed.py
        biorxiv.py       # Handles both bioRxiv and medRxiv
        openalex.py
    sync.py              # sync() + SyncReport
```

New optional dependency group: `publications = ["httpx>=0.25"]`

## Public API

```python
from bmlib.publications import (
    sync, SyncReport,
    Publication, FullTextSource, DownloadDay, SyncProgress,
    store_publication, get_publication_by_doi, get_publication_by_pmid,
    ensure_schema,
)
```
