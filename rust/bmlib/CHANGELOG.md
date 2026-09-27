# Changelog

All notable changes to the `bmlib` **Rust crate** are documented here. The format
is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
crate follows [Semantic Versioning](https://semver.org/).

The Python library is documented separately, in the repository's
[`CHANGELOG.md`](../../CHANGELOG.md).

## [0.2.0] - 2026-09-27

The first release after 0.1.0, and the one carrying everything fixed since. **0.1.1
was prepared and never published** — it was to hold the quality-reader fixes on their
own, and they are folded in here rather than left under a version nobody could install.
0.1.0 is the only version that has shipped.

### Changed — breaking

**The full-text cache.** Three public signatures, for one defect: `default_cache_dir`
read `HOME` on every platform and fell back to `PathBuf::from(".")`, so a process
with no home directory wrote its cache into whatever directory it happened to be
started in. Python reads `HOME` only on POSIX — on Windows `Path.home()` consults
`USERPROFILE`, then `HOMEDRIVE` + `HOMEPATH`, and **never `HOME`** — and where no
home can be determined it raises, which `FullTextService` catches and degrades to
no caching. `None` now travels the whole chain instead of a fabricated directory.

- **`default_cache_dir() -> Option<PathBuf>`,** where it returned `PathBuf`. The
  `Option` is what makes the relocation unrepresentable rather than merely fixed:
  there is no value left for the no-home case to take.
- **`FullTextCache::new(Option<PathBuf>) -> Option<Self>`,** where it returned
  `Self`. `None` returned is Python's `RuntimeError` from `FullTextCache()`; a
  `Some` argument still cannot fail, because this cache creates no directory on
  construction.
- **`sync_source` takes the source's `settle_days`,** where nothing carried it.
  Breaking for a caller who calls it directly; `sync` resolves the period from the
  registry for them.
- **`Registry::register` returns `Result<(), SettleDaysError>`,** where it returned
  `()`. It re-checks the descriptor's `settle_days` although the value was checked
  where it was set, because the field is public and mutable — day selection does
  date arithmetic with it, outside every per-day handler, so an unusable value
  costs the whole run its report rather than one day's. Python re-checks at the
  same point for the same reason.
- **`impl Default for FullTextCache` is removed.** Python's `FullTextCache()`
  raises where there is no home, and `Default` has no way to report that — an
  infallible default could only panic on such a machine or invent a directory,
  and inventing one is the defect. `FullTextCache::new(None)` is the
  replacement, and the `cache_dir` field is public, so a caller with a directory
  already had one.
- **`FetchError` gained `HttpStatus { url, status }`,** so a non-success status is
  no longer an `FetchError::Transport`. Python's `raise_for_status()` raises
  `httpx.HTTPStatusError` for a 4xx/5xx and a `httpx.TransportError` subclass when
  no request arrived, and the walkers store `f"{type(exc).__name__}: {exc}"`, so
  the two reach a caller under different names. Exhaustive matches on `FetchError`
  need the new arm.

### Added

- **`SourceDescriptor::settle_days`** — how many days after a day has ended a source
  may still add to it, with `MAX_SETTLE_DAYS`, `check_settle_days` and a validating
  `with_settle_days`. `BIORXIV_SETTLE_DAYS = 90` is declared on the bioRxiv and
  medRxiv descriptors: `/pubs` files a record under its *publication's* date and
  learns of the publication weeks later, so a day fetched as soon as it ends is
  nearly empty. Python refuses a boolean, a non-integer and a negative as well; a
  `u32` cannot hold them, and the port plan's §9 records that.
- **`extras["published_journal"]` and `["published_date"]`** on a bioRxiv record.

### Fixed

- **A bioRxiv record with no DOI fails the day**, naming the day, the source and
  both spellings. `/pubs` renamed `doi` to `preprint_doi`, so a reader that was only
  re-pointed finds every DOI absent — and a stored record then has no identity to
  deduplicate on.
- **A bioRxiv record's extras are Python's expressions, not readings of them.** Two
  divergences this exposed were pre-existing: a *present* `null` in `server` was
  replaced by the server name, which is a claim the source never made, and a truthy
  non-string in `category`/`published` was coerced to `""` where Python's `_field`
  passes it through.
- **A non-success status now reports itself as `HTTPStatusError`,** which is the name
  Python's handler writes. It was rendered `RemoteProtocolError` — a protocol
  violation, which is not what a 500 is — and **no test could see it**: both corpora's
  `fetch/http-error` case encoded its page as `[body, 500]`, and the Python dumper
  recognised that pair only as a *tuple*, which JSON cannot express, so on both sides
  the case was served a list body and duplicated `fetch/non-object-payload` (#349).
  The corpus now marks a response as `{"http_status": N, "body": B}`, two cases per
  source reach the status path (one with a page of records already delivered), and the
  message wording — the port's own, where Python's is httpx's — is a `corrected` block
  recorded in the port plan's §9.
- **The PubMed transport names its failures, which is what Python stores.** Every
  PubMed handler writes `f"{type(exc).__name__}: {exc}"`, and the part-level one is
  explicit about why: without the type a day fails reporting `part edat:a:b: ` and no
  cause at all. `Eutils` returns a `String` where Python raises, so `HttpEutils` now
  puts the name back through the same table its three sibling modules keep: a 4xx/5xx
  as `HTTPStatusError: {url} returned HTTP {status}`, a request that never arrived as
  `RemoteProtocolError: …`, and an unreadable `<Count>` or EFetch document as
  `ValueError: …` (#354). This **moves the stored error string** for every failed
  PubMed day; `read_esearch` and `count_delivered` keep their bare messages, which the
  oracle compares directly.
- **A planning probe that fails is no longer reported as a refusal.** `plan_partitions`
  could not carry a `count_fn` error, so all **four** probe sites fabricated a
  structural refusal: a 500 or a dropped connection was stored as *"the Entrez-date
  range … holds 0 of this day's N records, so N of them lie outside the ladder and would
  be silently absent; refusing the day"* — a claim about PubMed's index that nothing
  measured, and one that sends the reader to look at Entrez dates rather than at NCBI
  (#359). `PlanError::CountFailed` carries the failure, and the two call sites report it
  under Python's two arms: the structural refusals verbatim, everything else as
  `planning the Entrez-date parts failed: {type}: {exc}` and `re-partitioning part {key}
  failed: {type}: {exc}`. As part of it, the corpus's `plan/unsplittable-measured` case
  — which keyed the wide range while asking for a narrow one, so it reached
  `RootNotCovering` ("holds 0") instead of the measured descent it is named for — has a
  fixture that matches, and four `probe-fails-*` cases cover the sites, one per probe.
- **A transport failure is named `TransportError`, which is true whatever happened.**
  Python's `httpx` raises `ConnectError` for a refused connection and for a DNS failure,
  `ReadTimeout` for a server that accepts and never answers, and `ReadError` for a
  connection reset — all subclasses of `httpx.TransportError` (measured 2026-09-27).
  `FetchError::Transport` is one variant for all of them, so the base name is the only
  one that is true whichever it was; `biorxiv.rs`, `openalex.rs`, `pubmed.rs` and
  `sync.rs` said `RemoteProtocolError` — the *narrowest* of the four, and a false claim
  about the peer for three of them — until #361, and `fulltext/service.rs` already said
  `TransportError`. **This moves the stored error string** for every failed day whose
  request never arrived; the residual divergence (Python names the subclass) is in the
  port plan's §9, and the bioRxiv and OpenAlex corpora now carry a `fetch/transport-error`
  case with a `corrected` block recording it — the channel those tables had no coverage
  for at all.

**Day durability for a source that settles late.** A completed day is durable only
once it was fetched at least `settle_days` after the day ended, and every day of such
a source that is not yet final is re-offered on every run **whatever the caller's
window** — a *failed* row included, since a revisit that fails turns a completed row
`failed` and a rule offering only completed rows would drop the day after its first
transient error. Without this the port recorded nearly-empty bioRxiv days as complete
as soon as they ended (#325). The boundary comparison is a **difference**
(`fetched_at - day_over_everywhere >= settle_days`), never `boundary + settle_days`:
rule 5 reads rows of any date, and adding the period to a day near the end of the
calendar overflows outside every per-day handler, where it costs the whole run its
report.

**The full-text cache**, which is what the breaking change above is for:

- **A machine with no home directory caches nothing instead of writing into the
  current working directory.** `FullTextService::with_default_cache` degrades
  with a warning that names the cause — a caller who cannot determine a home is
  not helped by being told to choose a writable location — where the port used to
  report nothing and silently relocate.
- **A Windows home directory is found the way Windows defines one.**
  `USERPROFILE`, then `HOMEDRIVE` + `HOMEPATH` concatenated (so a rooted
  `HOMEPATH` keeps the drive), and `HOME` is never consulted. Reading `HOME`
  there found a different directory whenever a POSIX-flavoured shell had set it.
- The platform table — macOS `~/Library/Caches`, Windows `~/AppData/Local` with a
  `~/.cache` fallback when that directory does not exist, and `~/.cache`
  elsewhere — is now exercised for all three platforms from one machine, by
  taking the environment lookup and the home directory as arguments rather than
  reading process-global state.

**The quality readers**, and everything from here down: the rules that read a model's
JSON reply back into a Cochrane assessment or a Tier 2/3 answer. All of them move toward
Python's behaviour, and `src/quality/json_fields.rs` now states the numeric half of those
rules once rather than at each site.

- **An absent `risk_of_bias` no longer fabricates a risk-of-bias table.**
  `parse_cochrane_assessment` filled a missing section with nine `"Unclear risk"`
  domains — a judgement the model never made — and `COCHRANE_RESPONSE_FORMAT`
  explicitly sanctions `null` for a field the text does not report, so a
  *compliant* model reached it. The section is now refused, in Python's wording:
  `the response carries no risk_of_bias section`.
- **A boolean is not a number.** `bool` is an `int` in Python, and a `true` read
  as `1` / `1.0` is the most confident answer there is. Every numeric reader
  refuses it now.
- **A non-finite float is not a measurement.** `"nan"`, `"inf"`, `"-inf"`,
  `"Infinity"` and `"1e400"` were accepted by the Tier 2/3 float reader and by
  `clamped_confidence`; a confidence of `"nan"` was stored as `0.0`, which is a
  claim about the paper rather than a missing value. All are refused, and a
  refused `overall_confidence` is logged at WARNING as Python logs it — that line
  is the only trace the model answered one, an unstated confidence being kept
  under any `min_confidence` bar.
- **A count that is not an integer is no longer truncated into one.** `"45.5"`
  read as `45` and `"nan"` as `0`. `as_int` now follows `int()`: a numeric string
  is parsed (`"120"`, `"  +45  "`, `"1_000"`), a finite float truncates toward
  zero (`100.5` → `100`), and a malformed or non-finite one is unstated. A count
  beyond `i64` is refused rather than saturated at `i64::MAX`, which would record
  a number nobody reported.
- **`clamped_confidence` keeps Python's signed zero.** `(-0.0).clamp(0.0, 1.0)`
  is `-0.0` and a formatter renders it as `-0%`; Python's two-argument `max`
  returns `0.0`.
- **The risk-of-bias refusals use Python's wording**, so a caller matching on the
  message sees the same sentence: `the risk of bias has no '<domain>' domain` and
  `the risk of bias item has no '<key>'`. A domain that is not an object is
  refused rather than read.
- **A section is read leniently, a field is not.** `methods` and
  `support_for_judgement` are annotated as text, so a number or an object for one
  reads as unstated rather than being stringified; `group_sizes` goes through the
  same integer-map rule as its siblings.

## [0.1.0] - 2026-09-27

The first release — the whole port, as [`rust/README.md`](../README.md) lays it
out: `db/`, `citations/`, `context_processor/`, `fulltext/`, `transparency/`,
`publications/`, `quality/`, `llm/`, `agents/` and `templates/`.
