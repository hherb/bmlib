# bmlib::transparency — Transparency Analysis

Scores a paper's reporting transparency (0–100) from CrossRef, Europe PMC
(search and full text), PubMed, OpenAlex and ClinicalTrials.gov — funding and
industry involvement, conflict-of-interest disclosure, data availability,
trial registration and posted results, open access — and turns the score into
a risk level. The Python page is
[transparency.md](../../../../docs/manual/transparency.md); this one describes
the Rust API and where it differs.

| Need | Feature |
|---|---|
| `TransparencyAnalyzer` over your own `HttpClient`, the models, `calculate_risk_level`, the funder matcher | none |
| A real transport (`bmlib::http::UreqClient`) | `http-ureq` (default) |

The analyzer and the result type live in `transparency::analyzer`, the enums,
settings and risk rule in `transparency::models`; the main names are
re-exported at `bmlib::transparency`.

## Running an analysis

`TransparencyAnalyzer::new(email, pubmed_api_key, settings)` holds no client.
`analyze(client, document_id, pmid, doi)` takes any
`&dyn HttpClient` (`bmlib::publications::fetchers::HttpClient`, whose one
required method is `get`), so it runs against a stub as readily as against
the network. Here a stub answers CrossRef with an industry funder and Europe
PMC with no record; everything else is a 404:

```rust
use std::time::Duration;
use bmlib::publications::fetchers::{FetchError, HttpClient, HttpResponse};
use bmlib::transparency::{
    FullTextStatus, TransparencyAnalyzer, TransparencyRisk, TransparencySettings,
    TrialResultsStatus,
};

struct Stub;

impl HttpClient for Stub {
    fn get(&self, url: &str) -> Result<HttpResponse, FetchError> {
        if url.starts_with("https://api.crossref.org/works/") {
            return Ok(HttpResponse::ok(r#"{"message": {"funder": [{"name": "Pfizer Inc"}]}}"#));
        }
        if url.contains("/europepmc/webservices/rest/search?") {
            return Ok(HttpResponse::ok(r#"{"resultList": {"result": []}}"#));
        }
        Ok(HttpResponse { status: 404, body: Vec::new() })
    }
}

let mut analyzer = TransparencyAnalyzer::new("me@example.org", None, TransparencySettings::default());
analyzer.min_request_interval = Duration::ZERO; // the default paces requests 0.35 s apart

let result = analyzer.analyze(&Stub, "doc-1", None, Some("10.1000/example"));

assert!(result.industry_funding_detected);
assert!(result.risk_indicators.iter().any(|line| line == "Industry funder: Pfizer Inc"));
assert_eq!(result.transparency_score, 15);            // funder information only
assert_eq!(result.risk_level, TransparencyRisk::High); // below score_threshold (40)
assert_eq!(result.tier_downgrade_applied, 1);
assert_eq!(result.coi_disclosed, None);                // no full text, so undetermined
assert_eq!(result.full_text_status, Some(FullTextStatus::NotAttempted));
assert_eq!(result.trial_results_status, Some(TrialResultsStatus::NotRegistered));
assert_eq!(result.validate(), Ok(()));
```

The order is Python's: CrossRef (DOI only), the Europe PMC search, the full
text, PubMed `efetch` (with the PMID given or the one on the Europe PMC
record), OpenAlex (DOI only), then the posted-results check at
ClinicalTrials.gov. An empty `pmid` or `doi` counts as absent. Every step
swallows its own failure, so `analyze` returns a `TransparencyResult` and
not a `Result`: one dead API costs a component, never the analysis.
`analyze` takes `&self`; the rate limiter is a `Mutex` shared by every
thread using one analyzer.

For the network, use `bmlib::http::UreqClient` and **give it the analyzer's
user agent**. The analyzer requests through `HttpClient::get`, which sends
no per-request header, so the client must carry it, and ClinicalTrials.gov answers a request without the
`python-httpx` token with a 403:

```rust,ignore
use bmlib::http::UreqClient;
use bmlib::transparency::analyzer::user_agent;

let client = UreqClient {
    user_agent: user_agent("me@example.org", "0.28.1"), // bmlib/… (mailto:…) python-httpx/0.28.1
    ..UreqClient::default()
};
let result = analyzer.analyze(&client, "doc-1", Some("33301246"), Some("10.1056/NEJMoa2034577"));
```

The `pubmed_api_key` rides on the `efetch` request. `JSON_ACCEPT_HEADERS` is
exposed for a transport that wants to send it.

## Unknown is not high risk

Three results carry `TransparencyRisk::Unknown` and score 0, and
`unknown_reason` says which: `Disabled` (`settings.enabled` is false),
`NoIdentifier` (neither PMID nor DOI) — both make no request — and
`Unreachable` (not one API answered). `unknown_reason` is set if and only if
the level is `Unknown`, and an `Unknown` result applies no tier downgrade.

```rust
use std::time::Duration;
use bmlib::publications::fetchers::{FetchError, HttpClient, HttpResponse};
use bmlib::transparency::{
    FullTextStatus, TransparencyAnalyzer, TransparencyRisk, TransparencySettings,
    TransparencyUnknownReason,
};

struct Offline;
impl HttpClient for Offline {
    fn get(&self, _url: &str) -> Result<HttpResponse, FetchError> {
        Err(FetchError::Transport("connection refused".into()))
    }
}

let mut analyzer = TransparencyAnalyzer::new("me@example.org", None, TransparencySettings::default());
analyzer.min_request_interval = Duration::ZERO;

let r = analyzer.analyze(&Offline, "doc-1", Some("12345"), None);
assert_eq!(r.risk_level, TransparencyRisk::Unknown);
assert_eq!(r.unknown_reason, Some(TransparencyUnknownReason::Unreachable));
assert_eq!(r.full_text_status, Some(FullTextStatus::SearchFailed));
assert_eq!(r.tier_downgrade_applied, 0);

let none = analyzer.analyze(&Offline, "doc-2", None, Some(""));
assert_eq!(none.unknown_reason, Some(TransparencyUnknownReason::NoIdentifier));
```

## Reading a result

`TransparencyResult` carries the score, the level, `risk_indicators` (prose
findings, in the order made) and the flags beside them. Branch on the enums,
not the prose:

| Field | Type | What it says |
|---|---|---|
| `coi_disclosed` | `Option<bool>` | `Some(true)` found; `Some(false)` full text scanned and none exists; `None` undetermined. Only `Some(false)` downgrades |
| `full_text_status` | `Option<FullTextStatus>` | What became of the full text; `Analyzed` exactly when `full_text_analyzed` is true |
| `trial_results_status` | `Option<TrialResultsStatus>` | What became of the posted-results check; `Posted` exactly when `trial_results_compliant` is true |
| `pubmed_status` | `Option<PubMedStatus>` | What became of the PubMed step (see below) |
| `unknown_reason` | `Option<TransparencyUnknownReason>` | Why the level is `Unknown` |
| `data_availability_level` | `String` | `"full_open"`, `"on_request"`, `"not_available"` or `"unknown"` |

**`None` in the four enum fields means *not recorded*** — a row stored before
the field existed — never the enum's "nothing happened" member.
`FullTextStatus::is_refusal()` separates a document that was served and
refused (`Truncated`, `UnterminatedMarkup`, `UnclosedRegion`,
`EntirelyNested`, `Undecodable`) from one that never arrived (`NotAttempted`,
`SearchFailed`, `NotServed` — Europe PMC's own 404 — and `RequestFailed`);
`REFUSED_FULL_TEXT_STATUSES` and `NOT_REFUSED_FULL_TEXT_STATUSES` name the two
sides. `TrialResultsStatus::is_answered()` is true for `Posted` and
`NotPosted` only: for `PartlyAnswered`, `RequestFailed`, `NotCheckable` and
`NotRegistered`, a false `trial_results_compliant` means *not established*,
not *the trial fell short*. `outcome_switching_detected` is reserved and
always `false`.

Every enum has `as_str()` (the wire name), `parse()` and `ALL`.
`to_dict()` / `from_dict()` serialise in Python's field order and spellings;
`validate()` is Python's `__post_init__`, returning the contradiction rather
than raising:

```rust
use bmlib::transparency::{FullTextStatus, TransparencyResult, TransparencyRisk};

let mut r = TransparencyResult::new("doc-1", 80, TransparencyRisk::Low);
r.full_text_status = Some(FullTextStatus::Truncated);
assert!(FullTextStatus::Truncated.is_refusal());

let stored = r.to_dict();
assert_eq!(stored["full_text_status"], "truncated");
assert_eq!(stored["pubmed_status"], serde_json::Value::Null);
assert_eq!(TransparencyResult::from_dict(&stored)?, r);

r.full_text_analyzed = true; // contradicts Truncated
assert!(r.validate().is_err());
# Ok::<(), String>(())
```

Two corrections against Python's model: `coi_disclosed` defaults to `None`
(Python's dataclass default is `True`), so `TransparencyResult::new` and
`from_dict` on a row without it claim nothing; and `analyzed_at`
(`chrono::DateTime<Utc>`) is written as RFC 3339, ending `+00:00` as Python's
`isoformat()` does, and read by both.

## The risk level

`calculate_risk_level(score, industry_funding, data_availability, coi_disclosed, &settings)`
is the pure rule `analyze` applies: below `score_threshold` is `High`;
industry funding with `"restricted"`, `"not_available"` or `"not_stated"` data
is `High` (when `industry_funding_triggers_downgrade`); an explicit
`Some(false)` COI is `High` (when `missing_coi_triggers_downgrade`); up to
`MEDIUM_RISK_SCORE_THRESHOLD` (70) is `Medium`; industry funding caps the rest
at `Medium`.

```rust
use bmlib::transparency::{calculate_risk_level, TransparencyRisk, TransparencySettings};

let s = TransparencySettings::default();
assert_eq!(calculate_risk_level(85, false, "full_open", Some(true), &s), TransparencyRisk::Low);
assert_eq!(calculate_risk_level(85, true, "full_open", Some(true), &s), TransparencyRisk::Medium);
assert_eq!(calculate_risk_level(85, true, "not_available", Some(true), &s), TransparencyRisk::High);

// An undetermined COI does not downgrade; an absent one does.
assert_eq!(calculate_risk_level(85, false, "full_open", None, &s), TransparencyRisk::Low);
assert_eq!(calculate_risk_level(85, false, "full_open", Some(false), &s), TransparencyRisk::High);

let lenient = TransparencySettings { missing_coi_triggers_downgrade: false, ..s };
assert_eq!(calculate_risk_level(85, false, "full_open", Some(false), &lenient), TransparencyRisk::Low);
assert_eq!(calculate_risk_level(30, false, "full_open", Some(true), &lenient), TransparencyRisk::High);
```

`TransparencySettings::default()` is Python's: enabled, threshold 40, both
downgrade rules on, a one-tier downgrade. `filtering_enabled`,
`max_concurrent_analyses` and `cache_results` are for the calling application;
the library analyses one document per call and neither filters, threads nor
caches.

The score components are constants in `transparency::analyzer`:
`SCORE_FUNDER_INFO` (15), `SCORE_COI_DISCLOSED` (10), `SCORE_DATA_FULL_OPEN`
(20) or `SCORE_DATA_ON_REQUEST` (10), `SCORE_OPEN_ACCESS` (15), `SCORE_CITED`
(5), `SCORE_TRIAL_REGISTERED` (20) and `SCORE_RESULTS_POSTED` (15), capped at
`MAX_TRANSPARENCY_SCORE`.

## Industry funders

`is_industry_funder(name)` is the one predicate applied to CrossRef's
`funder[].name` and PubMed's `<Grant><Agency>`: a substring stem
(`INDUSTRY_STEMS`) anywhere, or a whole word (`INDUSTRY_WORDS`), case-folded.
A structured match records confidence `DEFAULT_INDUSTRY_CONFIDENCE` (0.8).

```rust
use bmlib::transparency::analyzer::{discloses_industry_ties, is_industry_funder};

assert!(is_industry_funder("Pfizer Inc."));
assert!(is_industry_funder("Novartis Pharmaceuticals"));       // a stem inside a word
assert!(!is_industry_funder("Abraham Lincoln Foundation"));    // "inc" needs a word boundary
assert!(!is_industry_funder("National Institutes of Health"));
assert!(!is_industry_funder("Merck & Co."));                   // "co" is refused

// COI prose is read by a separate rule, at TEXT_INDUSTRY_CONFIDENCE (0.5).
assert!(discloses_industry_ties("J.S. is a consultant for Acme."));
assert!(!discloses_industry_ties("The authors declare no competing interests."));
```

The token lists, and why each member is in or out, are documented at their
definitions and in the Python page; the Rust matcher is held to Python's
answers over the labelled `tests/data/funder_names.json` corpus.

## Full text

Only an accession matching `EUROPEPMC_ACCESSION_RE` (`PMC…` or `PPR…`) is
fetched, at `{EUROPEPMC_REST_BASE}/{accession}/fullTextXML`. Before it is
scanned, `strip_nested_articles` removes every `<sub-article>` and
`<response>` region, so a peer-review round's COI line is not read as the
paper's; it is public and returns `StripNestedArticlesError`
(`UnclosedRegion`, `Unterminated`) for a body that cannot be segmented. A body
without `</article>` is `Truncated`. The module imports nothing from
`bmlib::fulltext`.

## Differences from Python

- **No exception hierarchy.** Python's ERROR for an exception that can only
  mean a bmlib defect has no counterpart: `FetchError` is a closed enum, so a
  failed request is a WARNING. Log lines go to `stderr` with a
  `bmlib.transparency.analyzer DEBUG:`/`WARNING:` prefix; the crate has no
  logging facade.
- **`pubmed_status`** is recorded only by the `Disabled` and `NoIdentifier`
  returns; a result built from an analysis leaves it `None`. The reader takes
  the first `<PubmedArticle>` without checking that it names the PMID
  requested, so the member `OtherRecord` is never stored.
- **`Undecodable`** is never produced: a full-text body that is not UTF-8 is
  `RequestFailed`. An HTTP 500 from `fullTextXML` is `RequestFailed` whatever
  the record's `isOpenAccess` says.
- **The identifiers are not checked.** A PMID is sent as given (Python
  refuses one that is not ASCII digits and sends it canonical), and a PMID-only
  search is `EXT_ID:{pmid}` without Python's `AND SRC:MED`.
- **`min_request_interval`** is a public field (0.35 s by default), so a test
  can set it to zero; in Python the interval is a module constant.
