# Changelog

All notable changes to bmlib are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); bmlib follows
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **`transparency`: data deposition from PubMed's `<DataBankList>` now sets
  `data_availability_level`.** A `<DataBank>` naming a public archive —
  GENBANK, PDB, figshare, Dryad, GEO, BioProject, SRA, dbGaP, dbSNP, dbVar,
  PubChem-Substance, PubChem-BioAssay — is the publisher asserting that this
  article's data went into one. It was parsed past and discarded; it now
  yields `full_open` and a `Data deposited in {name}` indicator. Structured
  metadata, so it outranks the substring scan of the retrieved text that was
  the level's only source, and it reaches papers that scan cannot: a
  closed-access paper has no full text to scan, so this is the first
  data-availability evidence it can carry
  ([#44](https://github.com/hherb/bmlib/issues/44)).

  The archive names are an **allowlist**, measured with the new
  `scripts/sample_databank_names.py`, not the complement of the trial-registry
  set. The complement would score a registry NLM adds later as open data, and
  would credit the ~9,000 PubMed records naming a database an author cannot
  deposit into (RefSeq, OMIM, SWISSPROT, PIR, GDB, the UniProt family,
  PubChem-Compound), where an accession cites a curated third-party record. An
  allowlist goes stale by under-crediting instead, which is the direction a
  transparency score should fail in.

- **`transparency`: `JMACCT` and `REPEC` recognised as trial registries.** Both
  are on NLM's databank-source list and neither was in
  `_TRIAL_REGISTRY_NAMES`, so a paper registered with the Japan Medical
  Association's centre missed its 20 registration points.

  **Stored results are not comparable across this change.** For a paper with a
  deposition accession `transparency_score` rises by up to 20 and
  `data_availability_level` moves to `full_open`, which can also lift an
  industry-funded paper out of HIGH risk — rule 2 of `calculate_risk_level()`
  fires on withheld data. That is the intended effect: an archive accession is
  hard evidence and should outrank a phrase matched in running text.

### Changed

- **`transparency`: `data_availability_level` is merged, not assigned.** It has
  two producers as of the change above, so both report through
  `_Analysis.note_data_availability()`, which keeps the level carrying the
  strongest evidence of openness whichever arrives first. The credit is
  *swapped* rather than added to — a paper whose full text says "upon
  reasonable request" and whose PubMed record lists a GenBank accession scores
  20 for data availability, not 30 — which is what keeps the two awards
  mutually exclusive and the documented maximum at exactly 100. `unknown`
  ranks below the withheld levels because it is the absence of a finding, so a
  step that found nothing no longer erases another step's finding.
  `Data explicitly not available` became the module constant
  `_INDICATOR_DATA_NOT_AVAILABLE` so the method that appends it can retract it:
  a paper can withhold individual patient data and still have deposited its
  sequences, and the line would otherwise contradict the field.

- **`transparency`: `analyze()`'s accumulators moved onto one `_Analysis`
  carrier.** Ten values were passed into each sub-step and unpacked back out of
  a 4-to-6-element tuple, where element order was the only thing binding a
  value to its name — so a mis-ordered unpacking was a silent, type-compatible
  swap, and adding one signal meant widening several signatures. All five
  sub-steps now mutate the carrier and return `None`. `SCORE_FUNDER_INFO` is
  spent through a named `award_funder_info()` method, which makes "award this
  component at most once" a mechanism rather than a convention two call sites
  had to remember. Internal only: no public signature changed
  ([#37](https://github.com/hherb/bmlib/issues/37)).
- **`transparency`: a funder named repeatedly by CrossRef now yields one
  `Industry funder: X` indicator, not one per award record.** CrossRef emits
  one record per award, so an organisation funding several awards on a paper
  repeated in `risk_indicators`. PubMed's grant list already deduplicated;
  both sources now go through the same `note_industry_funder()` and follow the
  same rule. No score, no `industry_funding_detected` and no risk level moves
  — only the length of `risk_indicators` for affected papers.

## [0.6.0] — 2026-07-30

The largest release since 0.4.0. `bmlib.publications` runs on PostgreSQL,
`FullTextService` reads PDFs, `BaseAgent` gained per-agent metrics and
embeddings, the transparency analyzer queries PubMed, and the JSON extraction
path was consolidated and two silent-truncation defects fixed.

No public signature changed incompatibly and nothing was removed, so the bump
is minor. **Three behaviour changes make stored results non-comparable**, none
of them behind an opt-in flag: transparency scores can rise (the PubMed step),
`industry_funding_detected` moves in *both* directions (the measured funder
matcher), and an unfenced or truncated array of objects now extracts whole
where it used to arrive as its first element. See **Compatibility** at the end
of this section for exactly who is affected.

### Added

- **`bmlib.publications` works on PostgreSQL.** `schema.py`, `storage.py` and
  `sync.py` were SQLite-only (`?` placeholders, `cur.lastrowid`,
  `UPDATE OR IGNORE`, `AUTOINCREMENT`) even though `bmlib.db` has supported
  both backends all along. Every statement is now written for both, and
  `ensure_schema()` picks the matching DDL. The behaviour is pinned by
  `tests/test_backends.py`, which runs each test against both backends.
- `bmlib.db.is_sqlite()`, `placeholder()` and `placeholders()` — the backend
  detection every dual-dialect module needs, promoted out of the private
  helpers in `db/migrations.py`.
- `publications.pmcid` — a column, a `Publication` field, and the conversion
  in `sync._record_to_publication()`. `FetchedRecord.pmc_id` was being dropped
  on store, so full-text retrieval could not use the PMC id a fetcher had
  already found. `ensure_schema()` adds the column to databases created by an
  earlier bmlib. The field is declared **last** on the dataclass, not beside
  `pmid` where it reads best: `Publication` is constructed positionally by
  downstream projects, so any other placement would shift every following
  argument and land a caller's `abstract` in `pmcid` with no error anywhere.
  Pinned by `test_positional_construction_is_stable_across_versions`.
- `bmlib.db.transaction_depth()` / `owns_commit()` — how many `transaction()`
  blocks the calling thread has open on a connection.
- Opt-in PostgreSQL test coverage: set `BMLIB_TEST_POSTGRESQL_DSN` to run the
  two-backend suite against a live server. Unset, those parameterisations skip
  and the suite is unchanged. CI runs it against a `postgres:16` service on
  every matrix entry, with `BMLIB_REQUIRE_POSTGRESQL=1` so a missing or broken
  DSN fails the build instead of skipping behind a green check.
- **`FullTextService` extracts a retrieved PDF's text into
  `FullTextResult.html`**, so a PDF-only article can be read inline. Needs the
  `bmlib[pdf]` extra and a cached PDF (that is, an `identifier`); opt out with
  `FullTextService(convert_pdfs=False)`. `pdf_url` and `file_path` stay
  populated, since extraction recovers prose but not figures, tables or
  layout. This closes the ROADMAP item that had the converter standalone.
- `fulltext.render_html()` — renders extracted PDF text as HTML, stripping
  repeated page furniture (running heads, footers, publisher watermarks) by a
  frequency rule that needs no per-publisher knowledge, and reflowing
  hard-wrapped lines back into paragraphs.
- `FullTextResult.content_kind` — says whether `html` holds a real article
  (`"fulltext"`), only an abstract (`"abstract"`), or prose extracted from a
  PDF (`"extracted"`). Code that scores or summarises an article should branch
  on this rather than on `html` being set.
- `JATSArticle.has_body` — whether `<body>` carried actual prose. It counts
  body paragraphs rather than `body_sections`, because back-matter sections
  land in the latter and a "Data Availability" section was otherwise passing
  for an article body.
- `JATSParser.parse_with_html()` — parses once and returns both the article
  and its HTML, instead of the two SAX passes `parse()` + `to_html()` cost.
- `ConversionResult.page_texts` — the text of each page that yielded any.
  Page boundaries are what let `render_html()` spot repeated furniture.
- `bmlib.agents.PerformanceMetrics` — thread-safe per-agent call accounting
  (prompt/completion/total tokens, request and retry counts, wall time),
  independent of the process-wide `TokenTracker`: `PerformanceMetrics` answers
  "what did this agent do", `TokenTracker` answers "what has this process
  spent". `BaseAgent` gained the matching accessors — `metrics` (an
  independent snapshot), `reset_metrics()`, `start_metrics()`,
  `stop_metrics()`, and `format_metrics_report()`. `chat()` times every call
  and records it into the metrics only on success; a call that raises records
  nothing, so a burst of failures cannot deflate `tokens_per_second`.
- `BaseAgent.embed()` / `embed_batch()` / `test_connection()`, and the
  `embedding_model` constructor parameter. `embedding_model` is declared
  **last**, after `max_tokens`, so existing positional construction is
  unaffected. Embedding calls are deliberately excluded from
  `PerformanceMetrics` — mixing them into `tokens_per_second`, a figure about
  generation throughput, would distort it.
- `BaseAgent.chat_json(..., retry_context: str = "")` — a label naming the
  task being attempted, folded into every retry, error, and failure message,
  including the temperature-0 truncation raise. Empty by default, so existing
  log lines are unchanged for callers that do not pass it.
- `bmlib.llm.utils.iter_json_spans()` — the locator now shared by
  `extract_json()` and `extract_and_repair_json()` (see Changed, below).
  Yields JSON candidate spans best-first without validating them: fenced
  ` ```json ` blocks, other JSON-shaped fences, remaining fences, balanced
  `{...}`/`[...]` spans, brace-only spans nested inside an already-yielded
  span, and — only when nothing balanced, i.e. truncated output — the text
  from the first opener to the end.
- `bmlib.llm.json_repair.salvage_json_fields()` — recovers individually named
  fields from a response `extract_and_repair_json()` gives up on entirely.
  Two-phase per key, both phases bounded: a fast `raw_decode` pass over the
  first `MAX_SALVAGE_MATCHES` (200) matches, then at most one `repair_json`
  attempt, at the last match, if no fast attempt succeeded. Both bounds
  matter, because every failed decode scans forward to the end of the
  document and a repetition-looping model — the failure mode salvage exists
  for — is what produces thousands of matches: unbounded repair made 3,000
  matches take 135s, and an unbounded fast pass left the whole function
  quadratic at ~1.0s for 50,000 matches. Bounded, that case is ~0.08s. Never
  raises on malformed text — including `RecursionError`, which `raw_decode()`
  throws rather than `ValueError` on input nested past the interpreter's stack
  limit; returns `{}` when nothing is found. Not wired into `parse_json()` —
  silently returning partial data would turn a loud failure into a quiet wrong
  answer, so callers opt in after catching the `ValueError`.
- **`TransparencyAnalyzer` queries PubMed, and `pubmed_api_key` finally does
  something** (closes #18). The parameter has always been accepted and never
  read — the port from bmlibrarian dropped the client that used it. There is
  now one E-utilities `efetch` request per analysis at most, placed after
  Europe PMC (so a DOI-only analysis can reuse the PMID from the record
  already fetched) and before ClinicalTrials.gov (so a structured accession
  can feed the posted-results check). No PMID from either source and the step
  is skipped entirely. It contributes three signals, all publisher-supplied
  structured metadata, each closing a gap Europe PMC leaves on closed-access
  papers:
  - `<CoiStatement>` establishes a COI disclosure with no full text to scan.
    A *missing* statement never demotes `coi_disclosed` from `None` to
    `False`: it means the publisher supplied none, not that the paper carries
    none, and `False` would trigger the missing-COI downgrade on no evidence.
  - `<DataBankList>` trial-registry accessions are trusted directly, skipping
    the abstract heuristic's registration-cue window and two-id cap — those
    exist only because scraping NCT ids out of prose cannot tell a paper's own
    registration from a review's citation list, which a databank entry
    already distinguishes. Registration in a registry other than
    ClinicalTrials.gov now counts too, with a distinct indicator, since its
    posted-results status cannot be looked up there.
  - `<GrantList>` gives a PMID-only analysis its first funder signal; an
    industry agency carries `DEFAULT_INDUSTRY_CONFIDENCE`, the same as a
    CrossRef funder record, both being structured metadata.

  What the key buys, stated precisely: NCBI meters unkeyed E-utilities traffic
  at 3 requests/second per IP and keyed traffic at 10 requests/second per key,
  so passing it moves bmlib's request out of the bucket the calling
  application's own E-utilities traffic already competes for. It does not
  change bmlib's own pacing, which stays on the 350 ms interval shared with
  the other APIs.
- `TransparencyUnknownReason` (`DISABLED` / `NO_IDENTIFIER` / `UNREACHABLE`)
  and `TransparencyResult.unknown_reason` (closes #21). `analyze()` returns
  `UNKNOWN` at score 0 for three unrelated reasons, and telling them apart
  meant matching `risk_indicators` prose — documentation, not API. The
  strings stay for humans. Set if and only if `risk_level` is `UNKNOWN`:
  `calculate_risk_level()` never returns `UNKNOWN`, so every one comes from a
  known early return. Serialised by value like `risk_level`, and the *key* is
  read defensively on the way back in, so results persisted before the field
  existed still load — a present-but-unrecognised value still raises, exactly
  as `risk_level` does. `__post_init__` enforces the invariant in the one
  direction that cannot collide with those legacy results: a reason on a
  non-`UNKNOWN` result raises `ValueError`, while an `UNKNOWN` without a
  reason is accepted. Declared **last** on the dataclass, for the same reason
  as `Publication.pmcid`.
- **`require_dict` on `BaseAgent.parse_json()` and `chat_json()`** (part of
  #33) — opt-in strictness for callers that need a JSON object rather than
  whatever the model happened to emit. `parse_json(require_dict=True)` raises
  `ValueError` naming the shape it got. `chat_json(require_dict=True)` treats a
  wrong shape as a retryable failure inside its existing backoff loop, so a
  model that answered with an array gets up to `max_retries` attempts at a
  usable answer — **except at temperature 0**, where it raises on the first
  one, mirroring the truncation path: greedy sampling returns the same array
  from the same messages, so the retry is provably futile. The shape failure is
  reported separately from `"unparseable response"`: the response *was* valid
  JSON, just the wrong shape, and `chat_json()` runs its own `isinstance` check
  rather than message-sniffing a `ValueError` to tell the two apart. Both
  return paths are covered, including the truncation path's `_try_parse()`
  shortcut. `@overload` on `Literal[True]` narrows the return to `dict` for
  strict callers, so the widened annotation costs them no `isinstance`
  friction; CI runs ruff only, so the `@overload`/`@staticmethod` stacking
  order was verified once against mypy outside the build. A **third overload
  taking a plain `bool`** keeps `require_dict=self.strict` type-checkable —
  mypy does not expand `bool` into `Literal[True] | Literal[False]` to match
  one of the other two, so without it a caller holding a runtime flag gets
  "no overload variant matches" and no way to satisfy it.
- **`allow_fragments` on `bmlib.llm.utils.extract_json()`** — when False the
  last-resort second walk is skipped, so *text* comes back unchanged rather
  than an object dug out of the inside of a span. A caller that can *repair*
  has something better to try than a fragment; `BaseAgent.parse_json()` is the
  one caller that does. The providers' `json_mode` path takes the default,
  since it has no repair stage.

### Changed

- **`extract_json()` prefers a whole span over a nested fragment** (part of
  #33). The acceptance policy is split out as a private `_first_acceptable()`
  and run twice: once over whole spans only, and — only when nothing there
  parsed — once more with the nested-object stage enabled. An object dug out
  of the inside of another span is now a last resort rather than a preference,
  so a response whose JSON is an **array of objects** is returned whole where
  it was previously reduced to its first element.

  The non-dict fallback within each walk is *ranked* rather than
  first-parseable: a span that is a list holding at least one object beats any
  other non-dict span. Without that, an incidental parseable span earlier in
  the response (`'[] and [{"a": 1}]'`) would be accepted by the first walk, the
  second walk would never run, and the caller would receive unrelated data
  that parses cleanly and survives every downstream shape check — a worse
  failure than the truncation being fixed.

  `extract_and_repair_json()` deliberately has **no** equivalent second walk.
  Validating a nested fragment reports what is there; repairing one closes
  brackets around it and fabricates a structure the model never emitted.
- **`BaseAgent.parse_json()` and `chat_json()` are annotated `dict | list`**
  (closes #33). They always returned whatever the response parsed to, so a
  model answering with a top-level array handed back a list; the annotation
  now says so. Raising on a non-dict was considered and rejected: it would
  have hidden the fragment loss above rather than repairing it — and
  inconsistently, since `parse_json()` tries `json.loads(text)` first, so a
  bare array would have raised while the same array in prose came back as its
  first element and passed as a dict. It would also lock out the array-shaped
  agents queued for the bmlibrarian port. Callers needing an object say so
  with `require_dict` instead; `_try_parse()` widens to `dict | list | None`
  to match.

  `dict | list` is now the whole contract and is **enforced**, not merely
  annotated: a response that parses to a bare scalar — `42`, `"done"`,
  `true`, `null` — raises `ValueError` naming the type it got, where it used
  to be handed back past an annotation that excluded it. A scalar is not a
  structured answer to a `json_mode` request, and returning one only defers
  the failure to the caller's first subscript. Inside `chat_json()` it
  surfaces as an ordinary unparseable response and is retried.
- **`BaseAgent.parse_json()` defers the nested fragment past its repair
  stage.** It now asks `extract_json()` for whole spans only, tries repair,
  and re-asks with fragments allowed only if repair also failed. A *truncated*
  array of objects — `'[{"a": 1}, {"b": 2}'` — never balances, so extraction
  could only ever offer the first object: taking it dropped the sibling and
  skipped repair's truncation WARNING, while repair closes the bracket and
  recovers the whole array. This is the same silent loss the whole-span
  preference fixes one level up, on the shape most likely to arrive that way.
  `'[{"a": 1}, invalid junk]'` — nothing whole to recover — still returns the
  fragment.
- **`extract_json()` and `extract_and_repair_json()` are rebuilt on the
  shared locator `iter_json_spans()`** (closes #17). Behaviour deltas fall
  out of the consolidation:
  - Bare top-level arrays are now visible to `extract_json()` — previously an
    unfenced `[...]` response with no object anywhere fell through to the
    raw, unparsed input.
  - **Dict preference (`extract_json()` only):** when a response contains a
    **top-level** object alongside an incidental array, `extract_json()`
    returns the object, because the object is what a `json_mode` caller
    actually asked for. This is not new: the pre-consolidation brace-only scan
    was object-only, so it already returned `{"a": 1}` for
    `extract_json('[1, 2] then {"a": 1}')`. What *is* new is that a **fenced**
    candidate now outranks dict preference — a fence is the model's own
    delimitation of its answer, so a fenced JSON array must not be reduced to
    an object plucked from inside it by a later, unfenced stage — and that the
    preference no longer extends to an object reachable only from *inside*
    another span; see "prefers a whole span over a nested fragment" below.
  - **Fence priority (`extract_json()` only):** a ` ```json `-tagged fence now
    wins over an earlier untagged fence, instead of whichever fence comes
    first in document order winning regardless of its language tag.
    `extract_and_repair_json()` already prioritised ` ```json ` fences before
    this branch, so this delta is new only for `extract_json()`.
  - `extract_and_repair_json()` now walks candidates instead of staking
    everything on a single span: a candidate that fails to parse or repair no
    longer ends the search, so the next one gets a chance. With
    `repair=False`, this raises a plain `ValueError` on the final exhausted
    candidate where the pre-consolidation code re-raised the original
    `json.JSONDecodeError`. `JSONDecodeError` subclasses `ValueError`, so
    `except ValueError` callers are unaffected; `except json.JSONDecodeError`
    specifically no longer catches it.
  - `iter_json_spans()` yields no span twice, compared by text rather than
    position. The stages overlap — stages 4 and 5 rescan fence interiors as
    plain text, so every fenced body reached the balanced scan a second time
    — and a repeated candidate only buys a second run of `repair_json()`'s
    attempt loop on a span that has already failed.
  - `RecursionError` is caught alongside `JSONDecodeError` wherever a
    candidate is decoded — in `extract_json()`, `extract_and_repair_json()`
    and `BaseAgent.parse_json()`. `json.loads()` descends recursively, so
    text nested past the interpreter's stack limit (`'{"j": ' * 20000`, the
    shape a repetition-looping model emits) blows the stack rather than
    failing to decode, and each of those functions documents a
    never-raise-or-`ValueError` contract that the escape broke.
    `extract_json()` is the one that matters: it runs unconditionally on
    every `json_mode` response in both the Anthropic and OpenAI-compatible
    providers, and the stage-6 tail candidate hands it the whole nested run
    where the pre-consolidation brace scan found nothing balanced and
    returned the input untouched.
- `BaseAgent.parse_json()` now logs a WARNING when its repair stage is what
  rescued the response — repair closes brackets, so a truncated response can
  parse into a valid but incomplete object, and the log line says so.
- `PerformanceMetrics.elapsed_time_seconds` is measured on `time.monotonic()`
  rather than as a difference of the `time.time()` timestamps in `start_time`
  / `end_time`, which remain absolute so a caller can still render them as
  dates. A wall-clock difference can be distorted — or made negative — by an
  NTP step or a DST change mid-run, and `format_report()` prints this figure
  directly against `total_wall_time_seconds`, which `BaseAgent` accumulates
  from `time.monotonic()`; two clocks either side of that comparison is how
  "12.3s elapsed (14.1s in requests)" gets printed. `snapshot()` carries the
  monotonic marks across; an instance rebuilt by `from_dict()` has none —
  they are not meaningful between processes, so they are not serialised —
  and falls back to the timestamp difference.
- `TransparencyResult.trial_registered` can now be `True` for a registration
  in a registry other than ClinicalTrials.gov, which PubMed's `<DataBankList>`
  makes visible for the first time. `trial_results_compliant` stays `False`
  there — ClinicalTrials.gov has no answer for an ISRCTN number — so the
  indicator says `"Trial registration found; posted-results status could not
  be checked"` rather than the misleading `"Registered trial without posted
  results"`. Read the indicator, not the flag, to tell "checked and absent"
  from "not checkable". The line names the consequence rather than the cause
  because it also covers a *ClinicalTrials.gov* registration whose accession
  was unusable, for which "registered outside ClinicalTrials.gov" would be
  false.
- A COI disclosure found in PubMed retracts the two full-text COI indicators
  (`"No COI disclosure found in full text"`, `"COI disclosure status unknown
  (full text unavailable)"`) rather than leaving them to contradict
  `coi_disclosed=True`, and appends `"COI disclosure found in PubMed record"`
  in their place.

### Fixed

- **Industry-funder matching was punctuation-dependent, and measurably
  imprecise** (closes #36). `_INDUSTRY_KEYWORDS` tested substrings, so `"inc."`
  had to carry its trailing dot as a crude word-boundary substitute — and
  therefore missed an NLM-normalised `"Pfizer Inc"`. The list is now split into
  substring stems and whole-word terms behind one `_is_industry_funder()`
  predicate, used by both structured funder sources.

  The recalibration was **measured** rather than assumed, because
  `industry_funding_detected` feeds a HIGH-risk rule and HIGH applies
  `tier_downgrade_amount`. Against 833 real names sampled from CrossRef
  `funder[].name` and PubMed `<Grant><Agency>` (`scripts/sample_funder_names.py`,
  a live runner outside the pytest suite), 417 of them hand-labelled and
  committed as `tests/data/funder_names.json`:

  | Matcher | Precision | Recall |
  |---|---|---|
  | Substring (before) | 0.400 | 0.176 |
  | Split (now) | 0.917 | 0.324 |

  The corpus overturned two members that looked obviously right:
  - `"pharma"` scored 3 true positives against 5 false ones, reaching
    `"Faculty of Pharmacy"`, `"Pharmacogenetics …"` and `"Clinical Pharmacy"`.
    Narrowed to `"pharmaceutic"`, which keeps every true positive; the bare
    word is retained separately for `"Novartis Pharma AG"`.
  - `"biotech"` scored 0 true positives against 4 false ones — an Indian
    ministry department and a UK research council. *Biotechnology* names a
    field, not a company type. Only the bare word survives.

  Added on measured evidence: `"llc"`, `"incorporated"`, `"limited"` (2/1/1 true
  positives, no false ones; `\binc\b` cannot reach `"Incorporated"`). Rejected
  on it: `"co"` (collides with the English prefix) and `"corporation"` (US
  non-profits use it). `"ab"` and `"labs"` passed the count but were excluded
  because they collide with province codes and national laboratories, which the
  corpus happens not to contain — costing two true positives, named in the
  source comment.

  **Detection moves in both directions**, so stored `industry_funding_detected`
  values and the scores derived from them are not comparable across this change.
  Papers funded by `"… Inc"`, `"… LLC"`, `"… Limited"` or `"… Incorporated"`
  start being flagged; papers whose only match was a pharmacy department, a
  biotechnology ministry or a research council stop being flagged. The second
  group is the larger one, and every one of them was a false positive.
- **`extract_json()` silently dropped every sibling of an unfenced array of
  objects** (part of #33). `iter_json_spans()` offers the array at stage 4 and
  the object nested inside it at stage 5, and the dict preference accepted the
  fragment — so `'[{"a": 1}, {"b": 2}]'` in prose returned `{"a": 1}` with no
  error anywhere. See the two-walk policy under **Changed** for the fix, and
  **Compatibility** for who is affected.
- **A wrong-shaped response cost the two quality tiers a whole assessment.**
  `StudyClassifier.classify()` and `QualityAgent.assess()` hand `chat_json()`'s
  result to a `_parse_data()` that calls `.get()`, so a list raised
  `AttributeError` into a broad `except Exception` and degraded the paper to
  `UNCLASSIFIED` — no retry, and nothing in the log naming the shape. Both now
  pass `require_dict=True`, and both run at temperature > 0, so the wrong shape
  buys up to three attempts at a usable answer instead of one silent failure.
  Note the cost on the other side: `assess_batch()` is a serial loop and the
  backoff is a blocking `sleep`, so a model that answers *every* request with
  the wrong shape now spends 3 calls plus ~3s per paper where it spent 1 call
  and no sleep.
- **A truncated array of objects reached the caller as its first element.**
  `require_dict=True` was no defence: `chat_json()`'s truncation branch asked
  `_try_parse()` first, `parse_json()`'s extraction stage returned the object
  dug out of the unbalanced array, and the result passed the `isinstance`
  check as a dict — so the response came back as `{"a": 1}` on the first
  attempt with no truncation error and no repair WARNING, under a comment
  claiming the JSON "happens to be complete". `parse_json()` now holds the
  fragment back until repair has had its turn; repair closes the bracket and
  recovers the whole array.
- **An unsectioned JATS `<body>` lost all its prose.** `<sec>` is optional
  inside `<body>`, but the handler recorded a `<p>` only when a section was
  open, so an article whose body is bare `<p>` children was parsed as having
  no body at all — the paragraphs reached neither `body_sections` nor the
  rendered HTML. Since `has_body` landed, that also cost a permanent cache
  miss: `FullTextService` read such an article as abstract-only, declined to
  cache it, and re-fetched it on every request. Loose prose now becomes a
  `JATSBodySection` with an empty `title` — no heading is invented — flushed
  at each `<sec>` boundary so document order survives and real sections stay
  top-level instead of nesting inside it. Empty paragraphs are dropped, so a
  whitespace-only `<body>` still reports no body.
- **Figure and table captions were lost whenever the figure sat inside a
  `<sec>`** — the ordinary PMC layout. JATS carries caption body in `<p>` and
  the caption lead in `<title>`, the same elements that carry section prose
  and section headings, and the handler routed them by whichever `in_*` flag
  was set rather than by the enclosing `<caption>`. Inside a section the
  section branch won, so `JATSFigureInfo.caption` and `JATSTableInfo.caption`
  came back empty, the caption text was reprinted as article prose, and a
  `<caption><title>` **renamed the enclosing section** after the figure.
  Captions are now routed on `<caption>` itself and survive in every document
  shape. Non-caption `<p>` inside a figure or table — cell text, table
  footnotes — no longer leaks either: cells reach the rendered table through
  `characters()`, so passing them on had been duplicating them into
  `body_sections` and counting them towards `has_body`, and outside a `<sec>`
  appending them to the caption.
- **A body-less JATS document was mistaken for full text.** medRxiv's
  `jatsxml` URL serves, for some preprints, a document made of `<front>` and
  `<back>` alone. It returns HTTP 200 and parses cleanly, so the retrieval
  chain — which sorts `xml` ahead of `pdf` — treated it as a successful
  retrieval, never tried the PDF holding the actual article, and cached the
  abstract-only rendering permanently. Body presence varies per paper rather
  than per publisher, so this is fixed generically: such a document is now
  detected, never cached, and held back as a last resort while the chain keeps
  looking. If nothing better turns up it is returned with any resolved link
  attached, so the reader gets the abstract *and* somewhere to go.
- **Text extracted from a PDF was produced once and then lost.** Only the PDF
  bytes were cached, so a second `fetch_fulltext()` for the same identifier
  returned a bare `file_path` and the inline article text silently
  disappeared. A cached PDF hit now re-derives it.
- **A missing abstract killed the whole scoring batch.** A record with no
  abstract arrives as `None` from a nullable column, and both LLM tiers sliced
  it unguarded, so a `TypeError` escaped the assessment and took every later
  paper down with it. Both tiers now tolerate a `None` title or abstract. With
  *both* missing they return `unclassified()` without calling the model, since
  an empty prompt yields not an empty answer but an invented one that nothing
  downstream can tell from a real assessment.
- **The Tier 2 classifier's token budget could not be raised.** `classify()`
  repeated `temperature` and `max_tokens` at the call site, silently
  overriding the constructor. The classification JSON is ~50 tokens, but small
  local models preface it with commentary despite being asked for JSON alone,
  and the 256-token ceiling truncated the preamble and lost the JSON with it —
  affected papers fell back to `UNCLASSIFIED` with only a warning. The
  overrides are gone and the budget is now 1024, matching the assessor. Both
  agents carry their tuned sampling as constructor defaults, so it holds
  however they are built rather than only via `QualityManager`.
- **A PDF that yielded no text failed silently.** `PyMuPDFConverter.convert()`
  reports failure in its result rather than raising, so a corrupt PDF, an
  image-only scan, or a partial extraction all passed unlogged. Each is now
  reported at WARNING, and a partial extraction is flagged rather than
  attached as if it were the whole article.
- **`render_html()` collapsed a document into a single paragraph** when fewer
  than a tenth of its lines ran full width — a reference list, a table, a
  two-column extraction. The wrap-width estimate landed on a stub line, so no
  line ever counted as short enough to end a paragraph.
- **`fetch_scalar()` always returned `None` on PostgreSQL.** psycopg2's
  `RealDictRow` is keyed by column name, so `row[0]` raised `KeyError` and was
  swallowed by the fallback. It now reads the first value on dict-like rows.
- **`transaction()` now nests on PostgreSQL**, via savepoints, as it already
  did on SQLite. Previously an inner block committed connection-wide, so a
  batch's partial writes could not be rolled back — `publications.sync()`'s
  one-commit-per-day batching silently degraded to one commit per record.
  Nesting is detected from bmlib's own open-block count, *not* psycopg2's
  transaction status: psycopg2 opens a transaction on the first statement of
  any kind, so a bare `SELECT` would have made every following block look
  nested and stop committing. Un-nested blocks commit exactly as before.
  The count is kept per *(thread, connection)*: nesting describes one call
  stack, and counting by connection alone let a block open on one thread make
  an unrelated outermost block on another thread look nested — that block
  opened a savepoint, never committed, and its write was lost silently.
- `create_tables()` no longer commits mid-migration on PostgreSQL, so a
  migration that fails part-way rolls back whole. It already behaved this way
  on SQLite.
- `ensure_schema()` looks for existing columns in `current_schema()` only.
  `information_schema.columns` spans every schema the connected user can see,
  so on a database shared with another consumer the check could answer about
  *their* `publications` table — reporting `pmcid` present, skipping the
  `ALTER`, and failing the next write on the missing column.

### Documentation

- `docs/manual/fulltext.md` carried the `## PDF Conversion` section **twice**,
  with overlapping but non-identical content, so every converter API change
  had to be made in two places or the page contradicted itself — which it
  did: one copy called the converter a standalone module "nothing in the
  retrieval chain calls", while the Module layout table above it correctly
  said the service extracts a retrieved PDF's text. The two copies are merged
  into one, keeping the fuller reference and folding in the `page_texts` and
  `render_html()` material the other copy held alone. A stray cache-key
  paragraph that had been duplicated into the same region, restating what
  "Cache keys" already covers, is gone too.
- `docs/manual/transparency.md` contradicted itself on thread safety: the
  constructor section said "do not share one analyzer across threads" —
  guidance from before 0.4.0 made it thread-safe — while the concurrency
  section 300 lines below correctly recommended sharing one instance. The
  stale sentence is gone. A paragraph about the COI fallback window's known
  limitation also appeared twice, in slightly different words; the two are
  merged.

### Added — development tooling

- `scripts/sample_funder_names.py` — samples funder names live from CrossRef and
  PubMed to build the labelled corpus behind `_is_industry_funder()`. A live
  runner outside the pytest suite, like `scripts/smoke_test_tool_calling.py`;
  the suite consumes only its committed, hand-labelled output, so tests stay
  offline.

### Compatibility

No public signature changed and nothing was removed. SQLite behaviour is
byte-for-byte unchanged — the full pre-existing suite passes untouched. On
PostgreSQL the changes above are strictly fixes to paths that were broken or
absent. Databases created by an earlier bmlib pick up the new `pmcid` column
on the next `ensure_schema()` call, which `sync()` makes for you.

This section is otherwise about additive and fix-only changes, but the JSON
extraction deltas above (dict/fence-priority ordering and the whole-span
preference in `extract_json()`; walk-past-a-bad-candidate policy in
`extract_and_repair_json()`) are the first **behaviour** change here on a
genuinely hot path: both `bmlib/llm/providers/anthropic.py` and
`openai_compat.py` call `extract_json()` on every `json_mode` response,
unconditionally, not from an opt-in code path.

**Who is affected by the whole-span preference.** In `extract_json()` — the
function the two providers call — exactly one response shape: an **array of
objects sitting unfenced in prose**. Such a response now arrives whole where it
previously arrived as its first element. Two neighbouring shapes are unchanged
there — a fenced array already came back whole, and a bare array parses at the
provider's own `json.loads()` guard and never reaches `extract_json()` at all —
and an array of scalars had no nested candidate to lose to. Code that relied on
receiving the first element will now receive a list; `BaseAgent` callers wanting
the old dict-or-nothing guarantee should pass `require_dict=True`, which turns
the wrong shape into a diagnosed retry rather than a silent truncation.

**A second shape changes through `BaseAgent.parse_json()` only:** a **truncated**
array of objects, `'[{"a": 1}, {"b": 2}'`. It never balances, so `extract_json()`
still has only the first object to offer and is unchanged for the providers —
but `parse_json()` now lets its repair stage go first, so the response arrives
as the whole array with the usual possibly-truncated WARNING instead of as
`{"a": 1}` in silence. Under `require_dict=True` the recovered list is the wrong
shape, so it becomes a diagnosed retry — this is the one case where adding
`require_dict=True` can turn a previously "successful" call into a raise. That
call was returning a single record out of two.

`parse_json()` and `chat_json()` return `dict | list` where they were annotated
`-> dict`. No runtime behaviour changed for a response that parses to an
object, and nothing was removed — the annotation was always wrong for an array
response, which is what #33 reported. The one narrowing is that `dict | list`
is now enforced: a bare scalar response raises where it used to be returned.

Two details worth knowing when upgrading:

- **`ensure_schema()` is required after upgrading, not optional.** Reads
  tolerate a database that has not been through it — `storage` treats a
  post-release column as absent rather than raising — but writes name every
  column and will fail on one the database lacks. `sync()` calls it for you;
  code that goes straight to `store_publication()` must call it itself.
- `Publication` gained a field. Positional construction and `from_dict()` on a
  dict serialised by an older bmlib both behave exactly as before.
- `TransparencyResult` likewise gained `unknown_reason`, declared last, so
  positional construction is unaffected and a dict without the key loads with
  it set to `None`.

The transparency analyzer's behaviour does change, in ways worth planning for
even though no signature did:

- **One more outgoing request per analysis** (~0.35 s of enforced interval)
  whenever a PMID is available, which is most of the time. An analysis with
  neither a supplied PMID nor one in the Europe PMC record costs exactly what
  it did before.
- **Scores can go up.** A closed-access paper that previously scored 0 for COI
  and funding can now earn both from PubMed metadata, which may move a paper
  across `score_threshold` and out of HIGH. Stored scores from an earlier
  bmlib are not comparable with new ones for the same paper.
- `coi_disclosed` can now be `True` where it was `None`, and `False` is
  correspondingly rarer: it now means neither the full text nor PubMed had a
  statement.
- **`industry_funding_detected` moves in both directions** with the #36 matcher
  recalibration — see **Fixed** for the measured numbers. Papers matched only by
  a pharmacy department, a biotechnology ministry or a research council stop
  being flagged (all false positives); papers funded by `"… Inc"` without a dot,
  or by an LLC, stop being missed. Precision rose from 0.400 to 0.917 on the
  labelled corpus, so the net effect is fewer spurious tier downgrades.

## [0.5.1] — 2026-07-21

All changes are confined to `bmlib/llm/providers/ollama.py`. No public
signature changed incompatibly; `list_models()` gained an optional keyword.

### Changed

- **`OllamaProvider.list_models()` now costs one HTTP request** regardless of
  how many models are installed, instead of one `/api/show` per model. On a
  server with 139 models the call went from minutes to 64 ms. It reads
  `/api/tags` as raw JSON rather than through the `ollama` SDK, whose Pydantic
  model silently drops the per-model `capabilities` array and
  `details.context_length`.
- `list_models()` results are cached for `CACHE_TTL_SECONDS` (60); pass the new
  `force_refresh=True` to bypass the cache. The cache is cleared only on a
  successful fetch, so a refused connection no longer discards accumulated
  results.
- Models whose `/api/tags` entry omits `context_length` return metadata whose
  `context_window` — and `capabilities.max_context_window` — resolves via a
  memoised `show()` call on first read, not at list time. `__repr__` renders
  `<unresolved>` rather than fetching, so logging a model list stays free.
  These objects degrade to plain `ModelMetadata` / `ProviderCapabilities` when
  copied, pickled, or passed through `dataclasses.replace()`. This is the only
  place in bmlib where attribute access performs I/O.

### Fixed

- **Capability flags from `list_models()` were always `False`.**
  `supports_function_calling` and `supports_vision` are now derived from the
  `/api/tags` capabilities array. They are a **lower bound**: `/api/show`,
  reached via `get_model_metadata()`, reports a superset for these two flags
  (across 139 local models, tags reported 77 tool-capable against show's 102,
  and 32 vision-capable against 44). Filter by capability with
  `get_model_metadata()` when completeness matters — but note it is
  authoritative only when its `show()` call succeeds; for a cloud model on a
  server with cloud disabled, `show()` returns 403 and the fallback is
  *weaker* than the listing.
- **Context windows resolved to the 8192 fallback for every model.**
  `_extract_context_window` looked up `model_info`, which `ShowResponse`
  declares as `modelinfo` with `model_info` only as an alias, so on a real SDK
  response the lookup returned `None`. Real windows (131072, 128000, …) now
  resolve. The string-valued `parameters` fallback was dead for the same
  reason and now works.
- `get_model_metadata()` hardcoded its capability flags to `False`, so it
  contradicted `list_models()` for the same model. It now derives them from
  `ShowResponse.capabilities`.
- GGUF emits both `<arch>.context_length` and
  `<arch>.rope.scaling.original_context_length` — 9 of 139 models carry both,
  differing by up to two orders of magnitude. The exact key now wins outright
  instead of the first loose "context" match, removing a dependence on key
  emission order.

### Security

- `OLLAMA_API_KEY` is no longer leaked across a redirect. `urllib` re-sends
  every header to any host on redirect, so a gateway answering `/api/tags`
  with a 302 elsewhere received the bearer token in full. The raw fetch now
  builds an opener that strips `Authorization` when the target origin differs,
  matching the SDK path; same-origin redirects keep it.
- `OLLAMA_HOST` is restricted to HTTP(S). `urlopen` honours whatever scheme it
  is given, so `OLLAMA_HOST=file://…` read a local path straight into
  `json.loads`.
- Scheme-less `OLLAMA_HOST` values work again. `urlsplit` reads the
  conventional `localhost:11434` as scheme `localhost`; a `<word>:<digits>`
  form is now treated as host:port.

## [0.5.0] — 2026-07-20

### Added

- **Batch embedding.** `LLMClient.embed_batch(texts, model=..., max_batch_size=None)`
  embeds many texts per provider round-trip instead of one request per text,
  returning a new `BatchEmbeddingResponse` (`embeddings` — one vector per input
  in input order, `model`, `dimensions`, `input_tokens` summed across requests).
  Measured on 32 chunks against a local Ollama server: 0.59 s batched vs 4.48 s
  looped (7.6×). `BaseProvider.embed_batch()` is a concrete default raising
  `NotImplementedError`, mirroring `embed()`, so third-party providers are
  unaffected; only Ollama overrides it. Batching is bounded — texts are sent in
  groups of at most `max_batch_size` (Ollama default:
  `DEFAULT_EMBED_BATCH_SIZE = 256`) so a large corpus does not become one
  enormous request; pass `max_batch_size=len(texts)` to force a single
  round-trip. Not atomic: if a later group fails, vectors already computed for
  earlier groups are discarded with the exception. A vector-count mismatch
  raises `ValueError`; request failure raises `ConnectionError` as before.
- Ollama `embed()` / `embed_batch()` now forward `**kwargs` verbatim to the
  ollama SDK (`truncate`, `options`, `keep_alive`); previously they were
  accepted and silently discarded, so `truncate=False` could not be set.
- **Thinking/reasoning support across providers.** `LLMResponse` gained an
  optional `thinking` field (appended after `tool_calls`, so positional
  construction is unaffected) carrying the model's reasoning trace separated
  from `content`. The `think` kwarg on `LLMClient.chat()` is now interpreted
  by every built-in provider, not just Ollama: `bool` toggles thinking, a
  `"low"`/`"medium"`/`"high"` string sets effort, an `int` sets a token
  budget. Ollama forwards `think` natively and extracts `message.thinking`;
  Anthropic enables extended thinking (`budget_tokens` clamped to
  `[1024, max_tokens - 1]`, sampling params omitted as the API requires) and
  extracts `thinking` content blocks; OpenAI-compatible providers send
  `reasoning_effort` for effort strings on reasoning models and extract
  `reasoning_content` / `reasoning` response fields, with an opt-in
  `<think>…</think>` content split for local servers that emit reasoning
  inline. Callers that never pass `think` see identical requests and
  untouched `content`. Known limitation: Anthropic thinking does not compose
  with multi-turn tool loops (thinking blocks are not round-tripped into
  follow-up requests) — see `docs/manual/llm.md` and ROADMAP.md.
- OpenAI-compatible providers accept an `extra_body` kwarg forwarded verbatim
  to the SDK, as the escape hatch for server-specific parameters (e.g. vLLM's
  `chat_template_kwargs`).

### Changed — breaking

- **Ollama embeddings moved to the `/api/embed` endpoint, changing vector
  scale.** `OllamaProvider.embed()` previously called the deprecated
  `/api/embeddings` endpoint, which returned **raw** vectors; it now delegates
  to `embed_batch()` and so uses `/api/embed`, which returns **L2-normalised**
  vectors. This keeps `embed(t)` and `embed_batch([t]).embeddings[0]` in
  permanent agreement — keeping the old endpoint for single embeds would have
  made them disagree in scale forever.

  Cosine similarity is scale-invariant and is unaffected. **Raw dot-product or
  Euclidean (L2) comparisons are affected**, and the failure is silent: mixing
  vectors stored before this change with vectors produced after it degrades
  retrieval quality with no exception and no warning. If your store uses a
  non-cosine distance metric, **re-embed the corpus**. Callers on cosine
  similarity need do nothing.

## [0.4.0] — 2026-07-19

### Changed — breaking

- **`bmlib.db.transaction()` no longer commits when joining an open
  transaction** (SQLite savepoint path). Previously, a `transaction(conn)`
  block entered while the connection already held uncommitted writes would
  call `conn.commit()` on success, committing the caller's pending writes
  along with its own. Now the block joins via a savepoint and the owner of
  the enclosing transaction commits. Code that relied on `transaction()` as
  a durability checkpoint after bare `execute()` writes must commit
  explicitly (or wrap the whole batch in an outer `transaction()`). The same
  applies to `run_migrations()` when called with a transaction already open.
  On PostgreSQL the old connection-wide commit behaviour is unchanged (no
  savepoint nesting is implemented there).
- **`bmlib.publications.sync()` buffers each day's records and stores them
  after the fetch.** The `on_record` callback now fires while the fetcher
  streams, *before* the record is stored — callbacks must not expect to read
  the record back from the database. Writes cost one commit per day instead
  of one per statement, and SQLite's write lock is no longer held across
  network I/O; in exchange, a day's records are held in memory during the
  fetch.
- `TransparencyAnalyzer._check_europepmc()` now returns a 6-tuple (adds
  `industry_coi`).

### Added

- transparency: industry conflict-of-interest detection in full-text
  COI/disclosure statements — negation-aware, scoped to the COI region, with
  a guard for non-industry contexts (university/government employment,
  editorial boards). ORs into `industry_funding_detected` at moderate
  confidence (#7).
- llm: embedding support in the LLM abstraction layer (`LLMClient.embed()`,
  `EmbeddingResponse`). Implemented by the Ollama provider; other providers
  inherit `BaseProvider.embed()`, which raises `NotImplementedError`.
- llm: tool calling — `LLMClient.chat()` accepts `tools` and `tool_choice`,
  with the new `LLMToolDefinition` and `LLMToolCall` data types,
  `LLMResponse.tool_calls`, and `LLMMessage.tool_calls` / `tool_call_id` for
  multi-turn tool conversations. Implemented for Anthropic, Ollama, and the
  OpenAI-compatible providers (OpenAI, DeepSeek, Mistral, Gemini). Passing
  `tools` to a provider that does not support them raises
  `NotImplementedError` before any network call. Ollama accepts but ignores
  `tool_choice` — its native API has no equivalent.
- llm: `supports_tools()` — public probe for the tool-calling allowlist, so
  callers can test support for a provider name or `"provider:model"` string
  without catching `NotImplementedError`.
- db: nested `transaction()` blocks on SQLite are now composable (savepoint
  join; the outer block owns the commit).
- llm: `bmlib.llm.json_repair` — repairs malformed LLM JSON (single quotes,
  trailing/missing commas, unescaped control chars, truncation, unquoted
  keys) via `repair_json()`, `safe_json_loads()`, `extract_and_repair_json()`.
  `BaseAgent.parse_json()` now uses it as a last-resort fallback. Ported from
  bmlibrarian.
- llm: `bmlib.llm.text_utils` — boundary-aware text chunking (`TextChunk`,
  `TextChunker`, `chunk_text`) that never drops text, plus map-reduce /
  rolling-summary long-document processing and document-text helpers. Ported
  and consolidated from bmlibrarian's two chunkers.
- quality: `bmlib.quality.cochrane_models` and `cochrane_formatter` —
  Cochrane-aligned nine-domain Risk-of-Bias models with judgement + rationale,
  the full study-characteristics table, and Markdown/HTML renderers. A strict
  superset of `BiasRisk`. Ported from bmlibrarian.
- quality: `bmlib.quality.extractors` and `scoring_models` — rule-based
  (LLM-free) study-type detection with exclusion-context guarding and
  sample-size scoring, producing `DimensionScore` audit trails. Ported from
  bmlibrarian's paper_weight.
- fulltext: `bmlib.fulltext.pdf_converter` — pluggable PDF→text conversion
  (`ConversionResult`, `PDFConverter`, `get_converter`, `list_converters`)
  with a PyMuPDF backend behind the new optional `bmlib[pdf]` extra. Ported
  from bmlibrarian.

### Fixed

- transparency: a JATS-tagged COI section now counts as `coi_disclosed=True`
  even when its wording contains no cue phrase — the tag is structural proof
  of a disclosure; the cue-phrase scan remains the fallback for untagged text
  (#13).
- llm: `list_models()` on the Anthropic and OpenAI-compatible providers now
  returns a copy of the cached model list; mutating a returned list no longer
  corrupts the cache for subsequent callers (#12).
- publications: batched database commits — one commit per stored publication
  and one per synced day instead of one per statement (#8).
- llm: `get_llm_client()` singleton creation is now thread-safe; the
  openai-compat `list_models()` caches a successful-but-empty response for
  the TTL instead of re-hitting the API every call; the Anthropic provider
  warns (once per model per instance) when an unknown model id falls back to
  estimated pricing (#9).
- fulltext: `FullTextCache` sanitizes identifiers internally, so a raw DOI or
  path-traversal string cannot write outside the cache directory;
  already-safe identifiers keep their exact filenames (#9).
- publications: the OpenAlex fetcher tolerates a `"meta": null` page instead
  of raising `AttributeError` (#9).
- agents: `chat_json()` now fails fast with the real cause when a response is
  truncated at the `max_tokens` ceiling, instead of reporting a generic
  "unparseable response". At `temperature == 0.0` it raises immediately —
  greedy sampling reproduces the identical truncation, so retrying only pays
  for it again; above 0.0 it retries, since a different sample may fit. A
  response that is complete JSON despite hitting the ceiling is returned
  rather than rejected. Truncation detection covers Anthropic's
  `stop_reason="max_tokens"` and the OpenAI-compatible `"length"`, and empty
  responses are now treated as retryable transport errors.
- fulltext: cache keys are now `{sanitized}_{sha1[:10]}`, so DOIs that
  differed only in characters the sanitizer collapsed (for example
  `10.1/a:b` and `10.1/a/b`) no longer share a cache file and serve each
  other's full text.
- fulltext: JATS parsing no longer drops abstract sections, mislabels table
  headers, or loses figure and table captions.
- fulltext: the final fallback result is labelled `source="pubmed"` rather
  than `"doi"` when it resolves to a PubMed URL.
- db: `create_tables()` no longer uses SQLite's `executescript()`, whose
  implicit `COMMIT` broke a surrounding `transaction()` block and left
  migrations non-atomic. Statements are split and executed individually.
- llm: provider names are normalised to lowercase in client routing, so
  `"Anthropic:claude-..."` resolves like `"anthropic:claude-..."`.
- llm: JSON extraction handles responses containing multiple objects and
  braces inside strings.
- llm: OpenAI reasoning models receive `max_completion_tokens` instead of the
  rejected `max_tokens`.
- llm: the Ollama provider no longer clobbers a legitimate zero token count
  when recording usage.
- quality: the Tier 1 metadata filter no longer misclassifies study designs
  from ambiguous PubMed publication types, and `QualityAssessment` records
  `is_randomized` from the new `DESIGN_TO_RANDOMIZED` mapping, so
  `QualityFilter.require_randomization` recognises a Tier 1/2 RCT instead of
  rejecting it.
- transparency: conflict-of-interest detection and the ClinicalTrials.gov
  posted-results check were both under-detecting — the latter requested
  `ResultsSection` but read `resultsSection`. The analyzer now returns an
  `UNKNOWN` risk level with score 0 when no external API was reachable,
  rather than letting an all-zero score read as HIGH risk.
- publications: full-text sources are no longer silently dropped during sync.
- publications: the bioRxiv fetcher records the correct PDF version, and the
  PubMed fetcher handles non-numeric month names in publication dates.
- publications: `fetch_pubmed()` now populates `publication_types` from
  `PublicationTypeList`. It never did, yet the free Tier 1 quality filter
  classifies study design from exactly that field — so every synced PubMed
  record skipped the free tier and fell through to the paid LLM classifier.
- publications: `register_source()` now registers the built-ins before
  writing its entry, so registering under a built-in name actually overrides
  it. Previously an override installed before the first lookup was silently
  reverted the moment lazy registration ran.
- publications: the three built-in fetchers annotated `on_record` as
  `Callable[[dict], None]` while passing a `FetchedRecord`; the annotations
  now match the behaviour, which is unchanged.
- transparency: `TransparencyAnalyzer` is now safe to share across threads,
  which is what makes `settings.max_concurrent_analyses` usable. Rate-limit
  state is mutex-guarded (the interval throttles a shared remote API, so it
  must apply across threads); reachability is held per-thread, since it
  describes a single analysis. Previously two concurrent `analyze()` calls
  contaminated each other: a thread whose APIs were all down inherited a
  concurrent thread's success and was scored 0 / HIGH instead of UNKNOWN,
  wrongly triggering a tier downgrade.
- transparency: `settings.enabled` is now honoured. `enabled=False`
  short-circuits `analyze()` before any HTTP — and before the `httpx` import,
  so a disabled analyzer does not require the optional extra. It was
  previously ignored and analysis ran regardless.
- transparency: `TransparencyResult.to_dict()` now round-trips
  `full_text_analyzed`. Dropping it made a persisted `coi_disclosed=False`
  uninterpretable, since that value only means "scanned and absent" when the
  full text really was read.
- transparency: removed the unreachable `resultsSection` fallback in
  `_check_trial_results()`. The request is narrowed to `fields=hasResults`,
  so no other key can come back; the fallback implied a robustness it could
  not provide.
- db: `create_tables()` now parses `CREATE TRIGGER ... BEGIN ... END;`.
  Splitting on the semicolons inside a trigger body handed SQLite a fragment
  and raised `OperationalError: incomplete input`. Nesting counts
  `BEGIN`/`CASE` against `END`, so a `CASE ... END` inside a body does not
  close it early and a bare `BEGIN;` is not mistaken for one.

### Documented

- transparency: `TransparencySettings` now states which fields the analyzer
  honours and which are orchestration hints for the calling application
  (`filtering_enabled`, `max_concurrent_analyses`, `cache_results` — the
  library analyses one document per call and does no filtering, threading,
  or caching of its own).
- transparency: `outcome_switching_detected` is documented as reserved and
  always `False`. Deciding it means comparing a trial's pre-registered
  primary outcomes against those reported; it is kept in the schema so
  persisted results need no migration when detection lands.

## [0.3.0]

Never released. The version string was bumped in-tree when embedding support
landed, but no release was cut; those changes ship as part of 0.4.0 above.

## [0.2.1] and earlier

No changelog was kept; see the git history.
