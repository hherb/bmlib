# bmlib::publications — Publication Ingestion and Sync

Publication records from PubMed, bioRxiv/medRxiv and OpenAlex, stored once per
paper (deduplicated by DOI and PMID, merged on upsert), a date-keyed sync that
records which days are done, a one-paper lookup, and the Retraction Watch
export with a retraction-status verdict. The Python page is
[publications.md](../../../../docs/manual/publications.md); this one describes
the Rust API and where it differs.

| Need | Feature |
|---|---|
| Models, the rules, the parsers, the Crossref request builder, `RetractionIndex` | none |
| Storage, `sync`, Retraction Watch storage — on SQLite (`db::open_memory`, `db::open_path`) | `sqlite` (default) |
| The same on PostgreSQL | `postgres` |
| A real `HttpClient` for the fetchers and `lookup_record` (`bmlib::http::UreqClient`) | `http-ureq` (default) |

Every storage function takes a `&mut dyn Db` (see [db.md](db.md)) and works on
both backends; `ensure_schema` picks the matching DDL. The crate root of the
module re-exports the models, `ensure_schema`, the storage functions, the
Retraction Watch functions and the day-selection rules; the fetchers,
`sync::sync`, `lookup` and `retraction_status` are reached by their module path.

## The models

| Type | What it is |
|---|---|
| `Publication` | One stored paper: `title`, `doi`, `pmid`, `pmcid`, `abstract_text`, `authors`, `journal`, `publication_date`, `publication_types`, `keywords`, `is_open_access`, `license`, `sources`, `first_seen_source`, timestamps. `Publication::new(title, source)` |
| `FetchedRecord` | What a fetcher delivers: the same fields (`pmc_id` rather than `pmcid`) plus `fulltext_sources` (JSON values), `extras`, `grants`, `author_affiliations`. `FetchedRecord::new(title, source)` |
| `Grant` | `agency`, `grant_id`, `country`, and the `source` that asserted it |
| `AuthorAffiliation` | `author` (`"Last, Fore"`), `affiliation` (Markdown), `position` (0-based in the author list), `source` |
| `FullTextSource` | A stored full-text location (`source`, `url`, `format`, `version`) |
| `SourceDescriptor`, `SourceParam` | A registered source's metadata: `name`, `params`, `resumable`, `settle_days` |
| `DownloadDay`, `PartCheckpoint` | A `download_days` row and a finished part of a partitioned day |
| `FetchResult`, `SyncReport`, `SyncProgress` | One day's result, and a run's totals (`days_processed`, `records_added`, `records_merged`, `records_failed`, `errors`, `notes`, `sources_synced`) |
| `RetractionNotice`, `RetractionNature` | One Retraction Watch row and its kind |

Python's `to_dict()` / `from_dict()` are `to_json()` / `from_json()` here,
over `serde_json::Value`. `DownloadDay::from_json` refuses a row with no
`downloaded_at` rather than inventing one, and `DayStatus` (`Completed`,
`Failed`) is the only status the day table holds. `FetchResult::status` stays a
`String`, because a third-party fetcher returns it.

## Storing a paper

`ensure_schema` creates `publications`, `fulltext_sources`, `download_days`,
`download_day_parts`, `retraction_notices`, `publication_grants` and
`publication_affiliations`, and adds columns a database written by an older
bmlib lacks. Call it once after upgrading: `store_publication` names every
column in its `INSERT`.

`store_publication(db, &mut pub_, fulltext_sources, grants, affiliations)`
normalises the DOI (prefix stripped, lower-cased) and PMID in place, looks the
paper up by each, and either inserts (`StoreOutcome::Added`) or merges
(`StoreOutcome::Merged`): source lists are unioned, an empty field is filled,
**an existing value is never overwritten**, and `is_open_access` latches on.
Everything — merge, full-text sources, grants, affiliations — is one
transaction.

```rust
use bmlib::db::open_memory;
use bmlib::publications::{
    ensure_schema, get_author_affiliations, get_grants, get_publication_by_doi,
    store_publication, AuthorAffiliation, Grant, Publication, StoreOutcome,
};

let mut db = open_memory()?;
ensure_schema(&mut db)?;

let mut from_pubmed = Publication::new("A trial of X", "pubmed");
from_pubmed.doi = Some("10.1000/ABC".into());
from_pubmed.pmid = Some("12345".into());
let grants = [Grant {
    source: "pubmed".into(),
    agency: Some("NIH".into()),
    grant_id: Some("R01 AB000001".into()),
    ..Grant::default()
}];
let affiliations = [AuthorAffiliation {
    source: "pubmed".into(),
    author: "Smith, Jane".into(),
    affiliation: "University of Somewhere".into(),
    position: 0,
    ..AuthorAffiliation::default()
}];
let added = store_publication(&mut db, &mut from_pubmed, &[], &grants, &affiliations)?;
assert_eq!(added, StoreOutcome::Added);
assert_eq!(from_pubmed.doi.as_deref(), Some("10.1000/abc")); // normalised in place

// The same paper from another source, its DOI spelled differently.
let mut from_openalex = Publication::new("A trial of X", "openalex");
from_openalex.doi = Some("https://doi.org/10.1000/abc".into());
from_openalex.abstract_text = Some("We tested X.".into());
from_openalex.is_open_access = true;
let merged = store_publication(&mut db, &mut from_openalex, &[], &[], &[])?;
assert_eq!(merged, StoreOutcome::Merged);

let stored = get_publication_by_doi(&mut db, "DOI:10.1000/ABC")?.expect("one row");
assert_eq!(stored.sources, ["pubmed", "openalex"]);
assert_eq!(stored.pmid.as_deref(), Some("12345"));      // kept from PubMed
assert_eq!(stored.abstract_text.as_deref(), Some("We tested X.")); // filled in
assert!(stored.is_open_access);

let id = stored.id.expect("a stored row has an id");
assert_eq!(get_grants(&mut db, id)?[0].agency.as_deref(), Some("NIH"));
assert_eq!(get_author_affiliations(&mut db, id)?[0].author, "Smith, Jane");
# Ok::<(), Box<dyn std::error::Error>>(())
```

**Child rows are replaced per source.** Storing a source's grants (or
affiliations) deletes that source's stored rows for the paper and inserts the
new ones; other sources' rows are untouched, and an empty slice changes
nothing. A row with an empty `source` is refused with
`ReplaceError::MissingSource`, since an unnamed row could never be replaced.
`sync` stamps the source for you; a direct caller must set it.

```rust
use bmlib::db::open_memory;
use bmlib::publications::storage::ReplaceError;
use bmlib::publications::{ensure_schema, get_grants, store_publication, Grant, Publication};

let mut db = open_memory()?;
ensure_schema(&mut db)?;
let grant = |source: &str, agency: &str| Grant {
    source: source.into(),
    agency: Some(agency.into()),
    ..Grant::default()
};
let mut paper = Publication::new("A trial of X", "pubmed");
paper.pmid = Some("12345".into());

store_publication(&mut db, &mut paper, &[], &[grant("pubmed", "NIH")], &[])?;
store_publication(&mut db, &mut paper, &[], &[grant("openalex", "Wellcome")], &[])?;
store_publication(&mut db, &mut paper, &[], &[grant("pubmed", "NIHR")], &[])?;

let stored = bmlib::publications::get_publication_by_pmid(&mut db, "12345")?.unwrap();
let mut agencies: Vec<_> = get_grants(&mut db, stored.id.unwrap())?
    .into_iter()
    .map(|g| (g.source, g.agency.unwrap()))
    .collect();
agencies.sort();
assert_eq!(agencies, [("openalex".into(), "Wellcome".into()), ("pubmed".into(), "NIHR".into())]);

let unnamed = store_publication(&mut db, &mut paper, &[], &[grant("", "NIH")], &[]);
assert!(matches!(unnamed, Err(ReplaceError::MissingSource(_))));
# Ok::<(), Box<dyn std::error::Error>>(())
```

When a record's DOI and PMID point at two different stored rows, the rows are
consolidated into the DOI's row before the merge, moving each child-row source
the kept row lacks. `get_publication_by_pmid`, `get_grants` (insertion order)
and `get_author_affiliations` (by `position`) read back; `add_fulltext_source`
adds one location and returns `false` for a `(publication, url)` pair already
held.

## Fetchers and the registry

A fetcher implements one method:

```rust,ignore
pub trait Fetcher: Send + Sync {
    fn fetch(&self, request: &FetchRequest, sink: &mut dyn FetchSink)
        -> Result<FetchOutcome, FetchError>;
}
```

It hands every record to `sink.record(..)` as it reads it, may report
`Progress::Page` and `Progress::PartFinished` through `sink.progress(..)`, and
returns a `FetchOutcome`: the `record_count` the sink received, `status`
(`"completed"` or `"failed"`), the count the source `promised`, whether the walk
`stalled`, an `error`, a `note` for a day that completed short, and its `parts`.
`FetchRequest` carries the `date`, the source's `config` (`config_value(key)`)
and, for a source whose descriptor is `resumable`, the `resume` state.
`CountingSink` keeps the count and the delivery from disagreeing, and
`reconcile_delivery` with `SHORTFALL_FAILURE_RATIO` (0.5) judges a walk against
its promise the way the built-ins do. Records delivered against **no** promise
cannot be shown complete, so the day fails: report `promised`.

Python's module-level registry is a `Registry` value the caller owns:
`register(descriptor, Box<dyn Fetcher>)` (replacing an entry of the same name,
refusing an unusable `settle_days`), `fetcher(name)`, `descriptor(name)`
(`UnknownSource` names what is registered), `list_sources`, `source_names`,
`contains`, `is_resumable`. `builtin_registry(client)` wires the four built-ins
to one shared `Arc<dyn HttpClient + Send + Sync>`; `builtin_descriptors()`
lists them without a client.

| Source | Fetcher | Notes |
|---|---|---|
| `pubmed` | `pubmed::PubMedFetcher` (`::http(client)`, or `::new` over an `Eutils` transport) | `api_key` config; `resumable`; a day over `EFETCH_MAX_RETRIEVABLE` (9,999) records is planned into Entrez-date parts (`plan_partitions`) and walked part by part, each finished part checkpointed |
| `biorxiv`, `medrxiv` | `biorxiv::BiorxivFetcher::new(client, server)` | reads `/pubs`; `settle_days` = `BIORXIV_SETTLE_DAYS` (90) |
| `openalex` | `openalex::OpenAlexFetcher { client }` | `email` config (required by the descriptor), optional `api_key` |

`fetchers::europepmc` is not a sync source: its `normalize` turns one Europe
PMC search hit into a `FetchedRecord` for `lookup_record`. PubMed titles and
abstracts are read as Markdown (`text_with_formatting`, `escape_markdown`), as
in Python.

`HttpClient` is the transport seam: `get(url)` returns an `HttpResponse`
(`status`, raw `body` bytes) for any status the server answered with, and
`Err(FetchError::Transport)` only when the request never completed;
`get_with_headers`, `post_json` and `post_json_stream` default to refusing.
`HttpResponse::text` is strict UTF-8 and `json(url)` names the URL on a body
that will not parse. A test supplies its own client (below); production uses
`bmlib::http::UreqClient` ([http.md](http.md)).

## Syncing days

`sync::sync(db, &registry, &SyncRequest, now)` walks each source's days in
`[date_from, date_to]`, stores each day's records through `store_publication`,
and writes the day's row to `download_days`. It returns the `SyncReport`; a
day's failure is a line in `errors` and the run continues, and only a database
failure outside a day is an `Err`. The clock is the `now` argument, never read
inside.

```rust
use std::collections::BTreeMap;

use bmlib::db::{fetch_scalar, open_memory, Value};
use bmlib::publications::fetchers::{
    FetchError, FetchOutcome, FetchRequest, FetchSink, Fetcher, Registry,
};
use bmlib::publications::sync::{sync, SyncRequest};
use bmlib::publications::{ensure_schema, FetchedRecord, SourceDescriptor};
use chrono::{NaiveDate, TimeZone, Utc};

/// Delivers one paper twice, under two spellings of its DOI.
struct TwoCopies;

impl Fetcher for TwoCopies {
    fn fetch(&self, _: &FetchRequest, sink: &mut dyn FetchSink) -> Result<FetchOutcome, FetchError> {
        for doi in ["10.1000/ABC", "doi:10.1000/abc"] {
            let mut record = FetchedRecord::new("A trial of X", "stub");
            record.doi = Some(doi.into());
            sink.record(record);
        }
        // The promise is what lets the walk reconcile; without it the day fails.
        Ok(FetchOutcome { promised: Some(2), ..FetchOutcome::completed(2) })
    }
}

let mut db = open_memory()?;
ensure_schema(&mut db)?;
let mut registry = Registry::new();
registry.register(SourceDescriptor::new("stub", "Stub", "a stand-in"), Box::new(TwoCopies))?;

let day = NaiveDate::from_ymd_opt(2024, 6, 10).unwrap();
let configs = BTreeMap::new();
let request = SyncRequest {
    sources: vec!["stub".into()],
    date_from: day,
    date_to: day,
    recheck_days: 0,
    configs: &configs,
};
let now = Utc.with_ymd_and_hms(2024, 6, 11, 12, 0, 0).unwrap(); // the day is over everywhere

let report = sync(&mut db, &registry, &request, now)?;
assert_eq!((report.days_processed, report.records_added, report.records_merged), (1, 1, 1));
assert!(report.errors.is_empty());
assert_eq!(report.sources_synced, ["stub"]);
let status = fetch_scalar(&mut db, "SELECT status FROM download_days WHERE source = 'stub'", &[])?;
assert_eq!(status, Some(Value::Text("completed".into())));

// Fetched after the day was over, so the next run leaves it alone.
assert_eq!(sync(&mut db, &registry, &request, now)?.days_processed, 0);

# Ok::<(), Box<dyn std::error::Error>>(())
```

A source with no fetcher in the registry contributes `"No fetcher found for
source: <name>"` and is absent from `sources_synced`. A per-day error line
reads `"<source>/<yyyy-mm-dd>: <Python exception name>: <message>"`
(`HTTPStatusError`, `TransportError`, `ValueError`), as in Python.

**Which days are fetched** is `days_needing_fetch(rows, date_from, date_to,
recheck_days, now, settle_days)`, a pure function: a day with no row, a row not
`completed`, a completed day that was not over everywhere when fetched
(`day_was_over_when_fetched`: day *D* ends at 12:00 UTC on *D+1*), and, with
`recheck_days > 0`, a day not verified that recently. For a source declaring
`settle_days`, a completed day counts as durable only once fetched that many
days after it ended, and every row of that source that is not yet durable is
re-offered whatever the window (`FetchReason::Unsettled`). Each day carries its
`FetchReason`.

A day's status is decided by `resolve_day_status`: only `"completed"` and
`"failed"` are recognised, anything else is recorded `failed`, and a record that
failed to store fails its day. A window reaching into the future gets a `notes`
line (`note_unreachable_days`). For a partitioned PubMed day `sync` stores each
part's records when the part finishes, so memory holds one part rather than
the day, and writes a `download_day_parts` checkpoint for a part that
reconciled clean; a later run skips a part whose checkpoint still matches.

**Differences from Python.**

- `sync` does **not** validate its window. Call
  `sync::validate_window(date_to, recheck_days, today)` first: it refuses a
  `date_to` at or past `9999-12-31` and a negative or impossibly large
  `recheck_days` (`WindowError`). The `datetime`-is-not-a-`date` check Python
  needs is the type system's here.
- There is no `on_record` / `on_progress` callback and no `source_configs` /
  `email` / `api_keys` arguments. Configuration is `SyncRequest::configs`,
  keyed by source name; `sync::build_source_configs(source_configs, email,
  api_keys)` assembles it from Python's legacy parameters.
- **The peak-count hold is not applied** (tracked as #444). The
  `download_days.peak_count` column exists and `DownloadDay::peak_count` reads
  it, but the Rust sync neither writes it nor refuses a re-fetch that shrinks
  below half the peak, so a settling preprint day can be stored lower than it
  once held. Rust also takes no per-day lock before reading a day's row, so two
  concurrent syncs of one database are not serialised as Python's are.

## Looking up one paper

`lookup::lookup_record(&dyn HttpClient, &LookupRequest)` takes any of a `pmid`,
`doi`, `pmc_id` and `ppr_id` and returns a `RecordLookup`: the `record` (or
`None`), a `published_version` for a preprint OpenAlex merged with its journal
version, and one `SourceReport { outcome, message }` each for `pubmed`,
`openalex` and `europepmc`. The sources, the order of precedence and the
outcomes are Python's, held to it by `tests/fixtures/record_lookup.json`. A
malformed or missing identifier is a `LookupError` before any request; an
outage is `SourceOutcome::RequestFailed`, never "not found".

```rust
use bmlib::publications::fetchers::{FetchError, HttpClient, HttpResponse};
use bmlib::publications::lookup::{lookup_record, LookupError, LookupRequest, SourceOutcome};

/// A network that is down.
struct Offline;

impl HttpClient for Offline {
    fn get(&self, url: &str) -> Result<HttpResponse, FetchError> {
        Err(FetchError::Transport(format!("no route to {url}")))
    }
}

let request = LookupRequest { pmid: Some("12345"), email: "you@example.org", ..Default::default() };
let found = lookup_record(&Offline, &request)?;
assert!(found.record.is_none());
assert_eq!(found.pubmed.outcome, SourceOutcome::RequestFailed);   // worth a retry
assert_eq!(found.europepmc.outcome, SourceOutcome::NotAsked);     // no PPR accession

let bad = LookupRequest { pmid: Some("PMC123"), email: "you@example.org", ..Default::default() };
assert!(matches!(lookup_record(&Offline, &bad), Err(LookupError::InvalidIdentifier { .. })));
# Ok::<(), Box<dyn std::error::Error>>(())
```

With a real client:

```rust,ignore
use bmlib::http::UreqClient;

let found = lookup_record(&UreqClient::new(), &LookupRequest {
    doi: Some("10.1056/NEJMoa2034577"),
    email: "you@example.org",
    ..Default::default()
})?;
```

## Retraction Watch

`parse_retraction_watch_csv(bytes)` reads the export (Crossref's CC0 CSV; you
download it) into a `ParseOutcome`: the usable `notices`, the `skipped` rows
with their line and `SkipReason`, and `unknown_natures` — non-empty means
Retraction Watch's vocabulary moved, which would otherwise read every notice
as evidence of nothing. The export's "no identifier" sentinels (`0`,
`unavailable`) are absent values, and dates are read to ISO.
`store_retraction_notices` upserts on Retraction Watch's `record_id` in one
transaction; `lookup_retractions(db, doi, pmid)` returns one paper's notices
newest first, matching either identifier; `is_retracted(&notices)` is the pure
rule — the newest Retraction or Reinstatement decides, and a later Correction
does not undo a Retraction.

```rust
use bmlib::db::open_memory;
use bmlib::publications::{
    ensure_schema, is_retracted, lookup_retractions, parse_retraction_watch_csv,
    store_retraction_notices,
};

const EXPORT: &[u8] = b"\
Record ID,Title,Journal,RetractionDate,OriginalPaperDate,RetractionNature,Reason,OriginalPaperDOI,OriginalPaperPubMedID,RetractionDOI,RetractionPubMedID
1,Paper A,J Things,05/15/2020,01/01/2019,Retraction,+Fabrication;,10.1000/abc,0,10.1000/notice-1,0
2,Paper B,J Things,06/01/2021,,Correction,,unavailable,7654321,,0
3,Paper C,J Things,,,Retraction,,unavailable,0,,0
";

let parsed = parse_retraction_watch_csv(EXPORT)?;
assert_eq!(parsed.notices.len(), 2);
assert_eq!(parsed.skipped.len(), 1);               // row 3 names no paper
assert!(parsed.unknown_natures.is_empty());
assert_eq!(parsed.notices[0].retraction_date.as_deref(), Some("2020-05-15"));

let mut db = open_memory()?;
ensure_schema(&mut db)?;
assert_eq!(store_retraction_notices(&mut db, &parsed.notices)?, 2);

let a = lookup_retractions(&mut db, Some("https://doi.org/10.1000/ABC"), None)?;
assert!(is_retracted(&a));
let b = lookup_retractions(&mut db, None, Some("7654321"))?;
assert!(!is_retracted(&b));                        // a correction decides nothing
assert!(lookup_retractions(&mut db, None, Some("PMC1")).is_err()); // not a PMID
# Ok::<(), Box<dyn std::error::Error>>(())
```

`lookup_retractions` returns `Err` for an unusable request — no identifier,
identifiers that reduce to nothing, or a PMID that is not ASCII digits — so a
caller cannot mistake it for "no notices". The errors of
`store_retraction_notices` and `lookup_retractions` are `String`.

**Python's `import_retraction_watch_export` has no Rust counterpart.** There is
no one-call import that replaces the table, deletes notices the new export no
longer carries, or refuses a truncated download; a Rust caller parses, checks
`skipped` and `unknown_natures` itself, and calls `store_retraction_notices`,
which only upserts. The apps use `RetractionIndex` instead.

## Retraction status

`retraction_status::retraction_status(doi, export, crossref,
publication_types)` turns the export's notices, Crossref's `updated-by`
relations and PubMed's `Retracted Publication` label into one
`RetractionVerdict { status, deciding, unanswered }`. `RetractionStatus` is
`Retracted`, `Concern`, `Clear` or `Unchecked`, and **clear needs the export
consulted and Crossref to have served this DOI**: `export: None` is "not
consulted", `Some(&[])` is "consulted, nothing". The rules are Python's,
pinned by the shared `tests/fixtures/retraction_status.json`.

`RetractionIndex` exists only in Rust: the export parsed into one versioned
file an app keeps (`build(csv, data_as_of)`, `to_bytes`, `from_bytes`), whose
`lookup(doi, pmid)` returns exactly what `retraction_status` takes as
`export`. `build` refuses an export that is not UTF-8, holds no usable notice,
or in which no notice carries a paper DOI, a PMID, or a recognised nature (a
renamed column); `from_bytes` refuses another `RETRACTION_INDEX_VERSION`. Read
any refusal as "not consulted".

```rust
use bmlib::publications::retraction_status::{
    crossref_retraction_requests, parse_crossref_retraction_response, retraction_status,
    RetractionIndex, RetractionLookup, RetractionStatus,
};
use bmlib::publications::storage::normalize_doi;

const EXPORT: &[u8] = b"\
Record ID,RetractionDate,RetractionNature,OriginalPaperDOI,OriginalPaperPubMedID
1,05/15/2020,Retraction,10.1000/abc,0
2,06/01/2021,Correction,unavailable,7654321
";

let built = RetractionIndex::build(EXPORT, "2026-10-01")?;
let index = RetractionIndex::from_bytes(&built.index.to_bytes())?;
assert_eq!(index.len(), 2);

// Retracted on the export alone.
let export = index.lookup(Some("10.1000/ABC"), None);
let verdict = retraction_status(Some("10.1000/abc"), export.as_deref(), None, &[]);
assert_eq!(verdict.status, RetractionStatus::Retracted);

// Nothing in the export, Crossref not asked: unchecked, never clear.
let export = index.lookup(Some("10.1000/xyz"), None);
assert_eq!(export.as_deref(), Some(&[][..]));
let verdict = retraction_status(Some("10.1000/xyz"), export.as_deref(), None, &[]);
assert_eq!(verdict.status, RetractionStatus::Unchecked);
assert_eq!(verdict.unanswered, [RetractionLookup::Crossref]);

// Crossref served the DOI with no relations: clear.
let requests = crossref_retraction_requests(&["10.1000/XYZ"], Some("you@example.org"));
let batch = &requests.batches[0];                 // GET batch.url with your own client
let body = br#"{"message": {"items": [{"DOI": "10.1000/xyz"}]}}"#;
let answers = parse_crossref_retraction_response(200, body, &batch.dois);
let crossref = answers.get(&normalize_doi(Some("10.1000/XYZ")).unwrap());
let verdict = retraction_status(Some("10.1000/xyz"), export.as_deref(), crossref, &[]);
assert_eq!(verdict.status, RetractionStatus::Clear);

// PubMed's label settles retraction on its own.
let label = ["Retracted Publication".to_string()];
assert_eq!(retraction_status(None, None, None, &label).status, RetractionStatus::Retracted);
# Ok::<(), Box<dyn std::error::Error>>(())
```

Python's `crossref_key(doi)` is not ported: the response map is keyed by
`storage::normalize_doi`, which is what it computes. On the desktop, the
export's notices from `lookup_retractions` become evidence through
`evidence_from_notices`.
