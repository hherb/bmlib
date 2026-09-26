// bmlib — shared library for biomedical literature tools
// Copyright (C) 2024-2026 Dr Horst Herb
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with this program.  If not, see <https://www.gnu.org/licenses/>.

//! Publication ingestion, deduplication and sync.
//!
//! A port of `bmlib/publications/`. Ported so far:
//!
//! | Python | Rust | Status |
//! |---|---|---|
//! | `models.py` | [`models`] | ported |
//! | `schema.py` | [`schema`] | ported |
//! | `storage.py` | [`storage`] | ported |
//! | `retractions.py` | [`retractions`] | ported |
//! | `sync.py` | [`sync`] | ported (rules + storage helpers) |
//! | `fetchers/` | [`fetchers`] | in progress (`_reconcile`, `registry`) |

pub mod csv;
pub mod fetchers;
pub mod models;
pub mod retractions;
pub mod schema;
pub mod storage;
pub mod sync;

pub use models::{
    now_utc, parse_iso8601, parse_optional_datetime, python_repr, require_count, require_datetime,
    require_text, AuthorAffiliation, DayStatus, DownloadDay, FetchResult, FetchedRecord,
    FullTextSource, FullTextSourceEntry, Grant, ModelError, PartCheckpoint, Publication,
    RetractionNature, RetractionNotice, SourceDescriptor, SourceParam, SyncProgress, SyncReport,
};
pub use retractions::{
    is_retracted, lookup_retractions, parse_retraction_watch_csv, row_to_stored_notice,
    store_retraction_notices, ParseOutcome, SkipReason, Skipped, NOTICE_COLUMNS, UPSERT_CHUNK_ROWS,
};
pub use schema::{ensure_schema, SCHEMA_SQL, SCHEMA_SQL_POSTGRESQL};
pub use storage::{
    get_author_affiliations, get_grants, get_publication_by_doi, get_publication_by_pmid,
    normalize_doi, normalize_pmid, store_publication, StoreOutcome,
};
pub use sync::{
    day_was_over_when_fetched, days_needing_fetch, note_unreachable_days, read_aware_timestamp,
    read_verification_date, resolve_day_status, stamp_affiliation_source, stamp_grant_source,
    store_records, validate_window, DayOutcome, DayRow, DayToFetch, FetchReason, WindowError,
    CLOCK_SKEW_TOLERANCE_MINUTES, DAY_ENDS_EVERYWHERE_AT_UTC_HOUR,
};
