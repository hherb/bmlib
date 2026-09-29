# Changelog

All notable changes to the `bmlib` **Rust crate** are documented here. The format
is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
crate follows [Semantic Versioning](https://semver.org/).

The Python library is documented separately, in the repository's
[`CHANGELOG.md`](../../CHANGELOG.md).

## [Unreleased]

### Tests — four open JATS defects are pinned as reproductions

The corpus now pins Python's current behaviour for four filed defects, so the
port follows Python the moment it fixes them rather than silently disagreeing
with a stale fixture. Each has a `QUIRK:` comment at the site that causes it and
a named test stating the rule:

- **#385** — a reference with a fourth author renders `et al..`, the period
  doubled, because `et al.` already ends in one and the parts are joined with
  `". "`. `cited/385-a-fourth-author-doubles-the-period` and
  `build_html/ref_four_authors_double_the_period` pin both renderers, and
  `cited/385-exactly-three-authors-do-not` pins the arm boundary.
- **#397** — a cited `pub-id` is classified by its shape, not the declared
  `pub-id-type`, so a declared six-digit PMID is refused.
  `cited/397-a-declared-six-digit-pmid-is-refused` carries a refused one beside a
  seven-digit one that is read.
- **#393** — an element-only citation whose every child is one no field reads
  renders nothing, and the HTML reference list gets an empty `<li>`.
  `cited/393-a-citation-no-field-reads-renders-nothing` and
  `build_html/ref_no_field_reads_renders_an_empty_li` pin it.
- **#396** — an element-only citation's edition, publisher and comment reach no
  field and no counter. `cited/396-a-books-edition-publisher-and-comment-reach-no-field`
  pins the rendered loss.

The JATS corpus grows 60 → 65 documents and the service corpus 69 → 71 cases.

### Changed — the `fulltext` module follows Python's PR #355 decisions

**A caller's PMC ID is validated before it is used, and superseded when it
fails** (Python #304). A malformed id is recorded once as a fault and treated
as absent, so discovery runs in full — the ID Converter included — exactly as
with no id at all, where supplying one used to return strictly less than
omitting it. A well-formed id that neither Europe PMC nor NCBI serves is
**superseded by the Europe PMC search hit's id**, the hit whose free-PDF URL the
tier already trusted as the article; a usable caller id keeps the converter out,
because an ID Converter answer would make an identity claim that recovery never
made. A `pmcid` in the search response is validated where it is read — a bare
numeric one is normalised so it compares equal to the caller's prefixed
spelling, and a malformed or non-string one is a WARNING, a recorded fault, and
`None` so the converter is asked instead.

**A cached PDF keeps the abstract its retrieval returned** (Python #305), in an
`abstracts/` sidecar. It is written whenever a PDF is cached with a body-less
JATS rendering held back, whether or not that PDF yielded text, and read only on
a PDF hit that yields no text — alone, without a PDF, it is never a hit. The
directory is created by the first save, so a cache an earlier version built —
possibly read-only — still constructs. **This also reverts the previous
behaviour**, which treated a text-less PDF hit as a miss and re-ran the chain:
Python's decision was the sidecar alone, because for a `convert_pdfs = false`
caller every PDF hit became a network re-fetch. `FullTextCache` gains
`abstract_dir`, `save_abstract` and `get_abstract`; `delete`, `quarantine`
(HTML, then PDF, then abstract) and `clear` cover the new directory, and `clear`
skips one that is absent.

**The cache key is never hashed twice** (Python #309 part 1). `safe_filename`'s
pass-through bound is the new `MAX_KEY_CHARS` (171 — a 160-character prefix, `_`
and a ten-character digest, the longest key `sanitize_identifier` returns),
where it was `MAX_PREFIX_CHARS` (160). Below the bound the identifier passes
through unchanged; above it, it is sanitised. The cache corpus's last
`corrected` block is retired: `safe_filename/160`, `/161`, `/171` and `/172`
now agree with Python.

### Changed — the JATS reader follows Python's cited-name and NLM-citation decisions

**A cited `<name>` outside a `<person-group>` is an author** (Python PR #387,
Rust #388). JATS 1.3 admits `<name>` directly in both citation elements, and
the `<surname>`/`<given-names>` arms were gated on `in_ref_person_group` alone,
so such a reference stored no authors and the rendered bibliography printed
none. The widened gate is a **parent** test — inside the reference's
`person-group`, or a `<name>` directly in the citation — so a bare
`<string-name>` keeps the verbatim reading its own arm gives it.

**A `<name>` carrying `<given-names>` alone is a mononym** and its own author.
It used to be dropped, its given names left pending for the next cited surname
(`Madonna Smith` for two cited people). The flush happens at `</name>` only:
Wiley deposits some editors split across two `<person-group>`, given names in
the first and surname in the second, and the pending given names reassemble
them.

**A reference naming no work prints its deposit, however many components**
(Python #276). `R Core Team. (2019)` for a whole software citation is a pair the
component count let through; where there is a deposit and none of
`article_title`, `source` or `doi` is populated, the deposit is printed. This
reaches the reader's own `defers_to_the_deposit` in `fulltext::service` as well
as the structured model.

**The zero-author detector counts only the article's own contributor list**
(Python #264). It counted every name spelling anywhere in `<front>`, so a
journal's editors or a retraction notice's byline made an author-less notice
read as a routing failure (168 of 169 WARNINGs over the 97,909-article archive
artifact). The scope is structural, never the role.

**An NLM 2.x `<citation>` is a reference** (Python PR #394, Rust #395).
`<citation>` joins the mixed-content spellings and `<nlm-citation>` the
element-only ones. The DTD makes `<citation>` mixed content, but PMC deposits
it element-only (1,124,468 of 1,155,505 served carry no character data of their
own), so it writes the citation string only where it carries typeset text of
its own — directly or in an `<x>` — and reads as an `<element-citation>`
otherwise. A `<citation>` printed outside a `<ref>` merges back into its
sentence; a later `citation-type="display-unstructured"` part fills an
identifier the first part left empty; and a locator join made across whitespace
while the deposit looked element-only is undone if typeset text arrives before
the close.

The JATS oracle corpus grows from 43 to 60 documents — 9 that move on the
change, 6 pinning the direction it must not, and two reproducing the open
Python defect #391 (a `<mixed-citation>` or `<element-citation>` printed in
prose is cut out of the sentence, which the port mirrors) — the service corpus
by two, and five named tests state the rules.

### Changed — breaking
**The `Fetcher` trait, and where a walk's records go.** `fetch` takes one sink rather
than an `on_progress` closure, and the records reach the caller through it as they are
read instead of coming back in `FetchOutcome`:

- **`Fetcher::fetch(&self, request, sink: &mut dyn FetchSink)`**, where it took
  `on_progress: &mut dyn FnMut(Progress)` and returned the records in `FetchOutcome`.
- **`FetchOutcome::records: Vec<FetchedRecord>` is now `record_count: i64`**, and
  `FetchOutcome::completed` takes that count. A record belongs to the caller as soon as
  it is read; carrying it here as well would be a second copy, and the second copy is
  the peak the sink exists to remove.
- **`FetchSink`** (new) — `record` and `progress`: Python's `on_record` and
  `on_progress` as one object, because `sync`'s per-part flush needs the day's buffer
  *and* its connection at the same moment.
- **`CountingSink`** (new) — how a walk keeps the count it reports equal to what its
  caller received, which `FetchOutcome::records.len()` used to guarantee by
  construction.
- **`PartDisposition::Completed { checkpoint: Option<PartCheckpoint> }`**, where the
  checkpoint was bare: a part that came up short can now say *finished, no checkpoint*
  rather than claim one it did not earn or report no boundary at all.

**The quality extractors' public surface.** The module is now a transcription of
Python's own rule tables rather than a hand-rolled matcher, so the names follow
Python's:

- **Removed: `is_negated`, `NEGATION_WORDS`, `NEGATION_CONTEXT_WINDOW`.** Python
  replaced its negation-window model with `is_denied(text, start, end)`, which
  searches Python's `_DENIED_BEFORE`/`_DENIED_AFTER` — a negation at most three
  words before a mention with no preposition in between, or a negated verb of
  reporting straight after it. The window the port had refused **16 genuine
  confidence-interval reports** over a 5,976-abstract draw and found no real
  denial, because a CI is reported next to exactly that vocabulary.
- **Removed: `NUMBER`**, whose value is now Python's `_COUNT` under the name
  `COUNT` — the same pattern, with the lookarounds that refuse a fragment.
- **Added: `is_denied`, `COUNT`, `CI_PATTERNS`, `POWER_CALCULATION_KEYWORDS`,
  `POWER_CALCULATION_PATTERNS`, `DENIAL_LOOKAROUND`, `find_ci_context`**.
- **`parse_number` now strips every non-digit**, as Python's `int(re.sub(r"\D",
  "", raw))` does, rather than only commas; a count deposited in a non-ASCII
  decimal script reads as itself.
- **`find_sample_size`, `has_power_calculation`, `has_ci_reporting` and
  `extract_text_context` keep their signatures**; `has_exclusion_pattern`'s
  `exclusion_patterns` is now the only length it takes, unchanged.

**`strip_nested_articles` names the region it refuses** (following Python's
issue #186). It returned `Result<Option<String>, UnterminatedMarkupError>`, with
`Ok(None)` for a region left open. It now returns `Result<String,
StripNestedArticlesError>`, with two variants:

- `StripNestedArticlesError::Unterminated(UnterminatedMarkupError)`: the refusal
  that was already there, unchanged.
- `StripNestedArticlesError::UnclosedRegion(UnclosedRegionError)` (new): the
  regions still open, outermost first. Its `Display` is Python's `str(exc)`, for
  example `<response> inside <sub-article> left open`.

The analyzer's WARNING now includes that text, as Python's does. The stored
`FullTextStatus::UnclosedRegion` does not change. The oracle corpus gains
`strip_nested_articles/unclosed-nested-regions`, so the order of the names is
checked against Python.

### Changed

- **The study-type exclusions are Python's again, and the contrastive veto is
  gone (#298).** The port vetoed a higher-priority study type whose mention sat
  in a contrastive clause, so an RCT comparing itself with quasi-experimental
  work classified as `rct`. Python measured the veto over the draw and refused
  it: it moved **55 study-type answers and none for the better**, and the shape
  it was written for occurs in **0 of 914** RCT abstracts. `rct`'s exclusion list
  holds `quasi-experimental` and `quasi experimental` again, as Python's does.
- **The sample-size dimension's evidence keeps the paper's capitalisation.**
  `extract_sample_size_dimension` no longer lower-cases its search text, because
  Python does not; `extract_study_type` still does. A power-calculation or CI
  excerpt in the audit trail moves for every abstract that reports one.
- **The CI bonus records its mention's excerpt**, where it carried none.

### Added

- **`fancy-regex`, for the extractor rule tables only.** They use lookbehind,
  lookahead, scoped case folding and possessive quantifiers, and Python runs the
  same patterns on the same bytes through `re` — also a backtracking engine. The
  port plan's §2 allows it for exactly these sites and refuses it for the ones a
  network reaches.
- **`PubMedFetcher`, and `builtin_registry(client)`.** `fetch_pubmed` was reachable
  only by calling it directly: nothing implemented `Fetcher` over it, and nothing wired
  the built-in sources into a registry, so `sync()` over `"pubmed"` recorded
  `No fetcher found for source: pubmed` and a caller had to register the two fetchers
  by hand. `PubMedFetcher::http(client)` / `PubMedFetcher::new(transport)` puts the
  day's records and part boundaries through a [`FetchSink`], and
  `builtin_registry(client)` registers all four built-in sources — `pubmed`,
  `biorxiv`, `medrxiv` and `openalex` — from [`builtin_descriptors`], so the
  described set and the fetchable set cannot drift apart without a test failing.
  `descriptors_only()` keeps its use: metadata without a network client.

### Fixed

- **The JATS reader follows Python's owner-test fixes, so a related work's
  parts are never read as this work's** (Python PR #381, issues #270, #267,
  #271, #258, #266 and the latent half of #249). Four predicates answer *who
  owns a value*, and each arm that used an ambient flag now asks one of them:
  - **`cited_reference`** replaces the `in_ref_citation` gate on every
    reference structured-field arm — `article-title`, `source`, `year`,
    `volume`, `issue`, `fpage`, `lpage`, `pub-id` and the byline arms. The old
    gate was true anywhere under the citation, so a `<related-object>`,
    `<related-article>` or `<product>` nested there wrote *its* title, journal,
    year, volume, pages and DOI onto the reference — an erratum's `99:7` in
    place of the cited work's `1:2`, last writer winning. The walk refuses a
    related work between the element and the nearest citation element.
  - **`inside_related_work`** joins the text-merge rule, so a related work's
    accumulating children merge back into the buffer it sits in. Unmerged,
    `<article-title>` was cut out of the sentence printing it: a retraction
    notice read `titled “,”`, and a reply typing the work it answers inside its
    own title stored `Reply to , a comment`.
  - **`contrib_owns_name`** gates all four contributor-name arms and the
    undivided-name merge refusal, so a `<name>`, `<string-name>` or `<collab>`
    in a contributor's `<bio>` or `<author-comment>` is prose about them, not
    their name (which the old code let replace it), and merges into the
    paragraph rather than being cut out.
  - **`is_articles_abstract`** and **`in_articles_contributor_list`** restrict
    the abstract and author arms to a direct child of `front > article-meta`.
    An object's `<abstract>` (a `<supplementary-material>`'s, a `<fig>`'s) no
    longer joins `abstract_sections` — which also ends a figure's abstract
    erasing the article's own — and a `<contrib>` with no declared role in
    `<journal-meta>`, a `<supplement>` or a `<sec-meta>` is no longer an author.
  **The oracle corpus gains 24 documents** (18 → 42) that reach each path — the
  22 shapes Python's own tests use, plus the archive's two real retraction and
  correction notices (`PMC12105076`, `PMC12180358`); without the port **18 of
  the 42 diverged**, so the corpus that already existed had been green and
  hollow for this change. The `jats_reader` suite gains four named tests pinning
  the same conclusions. **The blast radius is now measured**, which Python's PR
  could not do: over 8,118 served articles and the 3,028 / 27,515 / 97,909-article
  `PMC000` / `PMC001` / `PMC012` archives, **0 / 0 / 0 / 2** articles change,
  the two being #271's own retraction and correction notices — the population
  the issue had measured and the PR shipped without diffing.
- **The exclusion window ends *after* the keyword, which is Python's shape
  (#366).** `has_exclusion_pattern` scanned `text[start..keyword_pos]`, so it
  ended **before** the keyword, where Python scans `text[start_pos :
  keyword_pos + len(keyword)]` and includes it. That is the whole of the rule
  for `"non-randomised controlled trial"`: `randomized controlled trial` is
  found *inside* the negation (the hyphen is a word boundary), so the exclusion
  that has to fire is the one containing the keyword itself. Measured over a
  5,976-abstract draw, **27 `Controlled Clinical Trial` abstracts moved
  `unknown` → `rct`** — the design the paper explicitly says it is not.
- **The port's #294/#297/#298 corrections are retired, because Python decided
  all three.** #294 (a digit-grouped sample size) was adopted outright; #297
  (negation-blind power/CI bonuses) was replaced by the narrower `is_denied`,
  which the port now implements; #298 (priority over evidence) was **refused**
  on the same measurement that retired the veto. The quality corpus's thirteen
  `corrected` blocks are gone, three measured character-class divergences take
  their place, and the corpus's 575 cases diff against Python.
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
- **One home for Python's `repr()` and `type(value).__name__`.** `pyvalue` now holds
  `python_repr`, `repr_str` and `json_type_name`; the crate's three other `repr()` copies
  (`publications::models`, `publications::fetchers::biorxiv`, `publications::sync`) and its
  **five** `json_type_name`s are replaced by them, and the three public names
  (`publications::models::{python_repr, json_type_name}`, `agents::base::json_type_name`)
  keep their signatures and delegate (#365). **A container now renders as Python's repr** —
  `[1, 2]`, `{'a': 1}` — where `publications::models::python_repr` wrote JSON text
  (`[1,2]`, `{"a":1}`), which is the spelling Python's `%r`/`{value!r}` messages carry.
  No message the oracle compares moved: three of that function's call sites narrow to a
  string first, and the one site a container *can* reach — `biorxiv`'s non-numeric-`total`
  refusal — had no case. **Two were added with the change**
  (`fetch/non-numeric-total-object`, `fetch/non-numeric-total-list`), so the spelling is
  pinned by the oracle and not by a comment. An integer outside `i64`/`u64` is the one type
  name that still differs from Python; it is a §9 row, since `serde_json` cannot hold the
  literal without `arbitrary_precision`.
- **`execute` no longer reports the previous statement's row count.** It returned
  `sqlite3_changes()`, which is the most recent INSERT/UPDATE/DELETE's count and is
  **not reset** by a statement that changes nothing — so `CREATE TABLE b` after an
  `UPDATE` of two rows reported **two**. It now asks `total_changes()` whether the
  statement changed anything at all and reads `changes()` for the count, which keeps
  a trigger's rows out of it as Python's `cursor.rowcount` does. Found by the new
  `db/` corpus, the first place this was diffed against Python. One divergence
  remains and is §9's: Python's cursor answers `-1` where the port answers `0`.
- **`sync` stores a day one part at a time, and a finished part is checkpointed.** Python
  drains its buffer at every part boundary (`flush_part`); the port returned the day's
  records from `Fetcher::fetch` and stored them once at the end — and, worse, it dropped
  the checkpoint each finished part carried, so **no sync ever wrote a
  `download_day_parts` row** and an interrupted partitioned day could not resume at all.
  Both are one object now: the day's buffer drains *and* checkpoints a part in a single
  transaction, which is what makes a checkpoint unable to attest to records a rollback
  discarded. The buffer also stops being fed by a `Vec` of every `Progress` event the walk
  emitted, which was a second unbounded step in the same place. The day's own store and
  its status row are one transaction as well — which the storage helpers already
  documented ("the caller's per-day transaction") and nothing opened.
- **A failed day keeps the records it delivered, and counts them once.** A hard `Err` from
  a fetcher threw away everything delivered before it, where Python's closing store keeps
  it; and the day's row was written from the *failure's* count — the parts already flushed
  plus the records still buffered — which double-counts once the closing store has folded
  the buffer in. `resolve_day_status` now runs on every path, as Python's does, so a day
  whose records failed to store also carries its `record(s) failed to store` line.

## [0.2.0] - 2026-09-28

The first release after 0.1.0, published from `0efd488`. **0.1.1 was prepared and never
published** — it was to hold the quality-reader fixes on their own, and they are folded in
here rather than left under a version nobody could install.

**What this version carries is what `main` held when it was published**, which is the
round-43 quality-reader defects that are why 0.2.0 exists at all, the three changed
`fulltext::cache` signatures, the round-46 rendering hooks, and rounds 47-49's #349 (a
non-2xx is a status error) and #350 (`pyvalue`). **Everything after that is under
[Unreleased]**, below: the stack that builds the rest of rounds 49-55 merged into its own
base branches rather than into `main`, so the crate published here does not carry it. The
[crate's own copy of this file](https://crates.io/crates/bmlib/0.2.0) says `2026-09-27`
because the release was prepared that day and published the next morning.

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
- **One home for Python's `repr()` and `type(value).__name__`.** `pyvalue` now holds
  `python_repr`, `repr_str` and `json_type_name`; the crate's three other `repr()` copies
  (`publications::models`, `publications::fetchers::biorxiv`, `publications::sync`) and its
  **five** `json_type_name`s are replaced by them, and the three public names
  (`publications::models::{python_repr, json_type_name}`, `agents::base::json_type_name`)
  keep their signatures and delegate (#365). **A container now renders as Python's repr** —
  `[1, 2]`, `{'a': 1}` — where `publications::models::python_repr` wrote JSON text
  (`[1,2]`, `{"a":1}`), which is the spelling Python's `%r`/`{value!r}` messages carry.
  No message the oracle compares moved: three of that function's call sites narrow to a
  string first, and the one site a container *can* reach — `biorxiv`'s non-numeric-`total`
  refusal — had no case. **Two were added with the change**
  (`fetch/non-numeric-total-object`, `fetch/non-numeric-total-list`), so the spelling is
  pinned by the oracle and not by a comment. An integer outside `i64`/`u64` is the one type
  name that still differs from Python; it is a §9 row, since `serde_json` cannot hold the
  literal without `arbitrary_precision`.

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
- **The ID Converter's log lines use Python's wording** (#350), reaching a caller
  through `FullTextService::log_lines()` and `warnings()`. An unusable `pmcid` is
  printed as Python's `%r` prints it — `'garbage'` quoted, where the port wrote
  `garbage` — in the WARNING; an error record carrying no `errmsg` logs `None`
  rather than an empty string; and a list or object in either renders as its
  Python `repr` (`['x']`) rather than JSON text (`["x"]`). Wording only: which
  records are absences and which are faults is unchanged, and is now pinned for
  each typing of `live` and `pmcid`: dropping the flag's lowercasing, or reading a
  `pmcid` by presence rather than by Python's truth, each survived the whole suite
  before.

## [0.1.0] - 2026-09-27

The first release — the whole port, as [`rust/README.md`](../README.md) lays it
out: `db/`, `citations/`, `context_processor/`, `fulltext/`, `transparency/`,
`publications/`, `quality/`, `llm/`, `agents/` and `templates/`.
