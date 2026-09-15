# Deliberate non-fixes — do not "fix" these

Each entry below was investigated and closed as correct. Reopening one wastes
a session. Entries marked "argued inline" carry their full reasoning as
comments in the named source file; the entry here is the pointer, not the
argument — write new entries in that shape, and put the argument where the
code is.

Split out of `HANDOVER.md` on 2026-08-09, which the register had grown to
dominate. `HANDOVER.md` is for what still needs doing; this file is for what
must not be re-done.

## Transparency

- **`_NESTED_ARTICLE_ELEMENTS` is restated here, not imported from
  `bmlib.fulltext.jats_parser`** (#119). Both modules make the same rule —
  nothing inside a `<sub-article>` or `<response>` is this article's — from the
  same structural argument, and the parser states it in full. It is duplicated
  on purpose: `bmlib.transparency` depends on nothing in `bmlib.fulltext`
  today, and reaching across packages for a module-private name to save two
  strings would trade that for nothing. Do not "deduplicate" them; if the rule
  itself changes, change both, and read the parser's comment first — it carries
  the argument.
- **The full text is stripped, not parsed** (#119). The obvious tidier fix is
  to feed `JATSParser` output to the scans, and it is wrong here: the COI scan
  matches on JATS containers (`<fn fn-type="COI-statement">` is structural
  proof of a disclosure regardless of wording, issue #13), which a parse throws
  away. Stripping keeps every calibration and changes only what is in scope.
  Metric test:
  `tests/test_transparency.py::TestANestedArticleIsNotThisArticles`.
- **An unclosed nested article costs the whole full text, on purpose** (#119).
  `_strip_nested_articles` returns `None` and the analysis falls back to the
  abstract, so `coi_disclosed` can never be set `False` — it may still be set
  `True` from the abstract, and it is only the `False` that triggers the
  downgrade. Do not "recover" the tail: keeping it is the defect the fix exists
  to remove, and dropping it silently manufactures "No COI disclosure found in
  full text" — the finding that, absent a PubMed `<CoiStatement>`, triggers the
  missing-COI HIGH-risk rule. An unmatched *end* tag **at depth 0** is
  deliberately **not** treated the same way, since no nested prose reaches the
  scans through one; one *inside* a region names an element that did not open
  it, so since #160 it closes nothing and the region is refused like any
  other. Only a document expat would reject can carry either — 0 of 98,789
  articles across both corpora does. Two paths measure empty over all 97,909
  articles in the `oa_comm` `PMC012xxxxxx` baseline — none leaves a region
  open, and none is emptied by the removal — so both guard a truncated body
  rather than a shape anyone has
  seen. **The lexer's four skip tokens have no measured population on this
  module's input at all**: the comment token fires on 3 *archive* deposits,
  where Springer comments out an `<authorqueries>` block whose `<aq>` children
  carry `<response>` elements, but Europe PMC's `fullTextXML` serves those same
  three with no comments and carries one in 0 of an 880-article draw against
  25.6% of the archive. Keep all four for the structural argument; do not cite
  a population for them.
- **The refusal branch's `<` must stay outside its group** (#160). The lexer's
  fifth branch — a bare opener, which refuses a construct that never
  terminates — is written `<(?P<unterminated>!--|…)` and not
  `(?P<unterminated><!--|…)`. `sre` derives a prefix for the whole pattern only
  when every top-level branch begins with the same literal, and then skips from
  `<` to `<` instead of trying the pattern at every position; a branch opening
  with a group defeats that analysis silently. Three configurations over 7.8 MB
  of real articles, and the labels are load-bearing: **13.4 ms** with no
  refusal branch, **26.6 ms** with it and the literal outside, **191 ms** with
  it inside. The *placement* penalty is therefore **7.2x** — not the 14x an
  earlier draft gave, which was 191 against the no-guard baseline and so
  counted the guard's own 1.9x a second time. Factoring the alternatives
  *inside* the group recovers ~8% of the penalty and not the penalty, so it is
  the group boundary and not the shape of what follows. The two forms differ
  by two characters and both pass every behavioural test, so the guard is
  `tests/test_transparency.py::TestMarkupTheContractDoesNotDescribe::test_every_branch_of_the_lexer_opens_with_the_literal`.
  Correctly placed the branch still costs 1.9x on well-formed input, which is
  the accepted price of bounding a 33.6s stall.
- **An unterminated construct raises rather than returning `None`** (#160).
  `_strip_nested_articles` has two refusals and they are different claims: an
  unclosed region is a document bmlib will not segment, and an unterminated
  comment, CDATA section, PI, doctype or tag is a document that did not
  arrive — an HTTP 200 is not a promise the whole body came with it. Both fall
  back to the abstract, and each WARNs in its own words, naming the construct
  and the offset. Do not collapse them onto one return value: that is #161's
  shape one level down, and the caller cannot re-derive which without lexing
  the body a second time. The raise is caught at the one call site, on its own
  line, deliberately outside the `except` that wraps the request — anything
  *else* this computation raises is still a bmlib defect and must not be
  swallowed.
- **`_INDUSTRY_STEMS` and `_INDUSTRY_WORDS` must not be merged into one
  list**, and neither may be extended without re-running
  `scripts/sample_funder_names.py` against `tests/data/funder_names.json` —
  the corpus *removed* intuitive members (`pharma`, `biotech`) on measured
  false positives. Metric test:
  `tests/test_funder_matching.py::TestAgainstTheLabelledCorpus`.
- **Membership follows four rules, rule 4 overrides the other three, and the
  rows beside them are under test** (#112). Corpus evidence earns a token; a
  reserved incorporation suffix is a **prior, not proof**, so it is kept where
  the corpus is silent; the residue of a disqualified stem is kept as a bare
  word where it cannot match more than the stem it replaced (`pharma`,
  `biotech`, which satisfy neither of the first two); and a token colliding
  with something the corpus cannot see is refused even when it passes the
  count, vetoing the other three (`ab`, `labs`, `co`). Do not "simplify" this
  back to *0 TP means excluded* — that reading is what left `plc`/`pty` out
  while `corp` and `gmbh` stayed in on the same score. Do not restate rule 2
  as "a public body cannot use the form" either: that premise is false, it was
  corrected in the review of PR #155, and the counterexamples are pinned by
  `tests/test_funder_matching.py::TestTheKnownFalsePositivesAreKnown`
  (`Forschungszentrum Jülich GmbH`, `Genome Research Limited`, and the
  corpus's own ambiguous-labelled `Goethe Business School GmbH`). #156 is the
  redraw that would measure it. Every row in those comments — its counts, its
  `in`/`out` and the rule it cites — and the headline table in
  `docs/manual/transparency.md` are re-derived by
  `tests/test_funder_matching.py::TestTheStatedCountsAreWhatTheCorpusHolds`,
  which parses them out of the source files themselves, so a row is an input
  under test and not a copy of one. **Arithmetic was never the defect**: the
  first cut of that class checked counts alone and stayed green while a row
  was moved into the refused block with its token still in `_INDUSTRY_WORDS`.
  It also asserts the corpus's own size, because every count is a numerator
  and a corpus cut to the names some token reaches reproduces all of them.
- **`plc` is kept although rule 4 reaches it** (#112, review of PR #155). PLC
  is the usual abbreviation of *phospholipase C*, so `"Role of PLC-gamma
  signalling in tumour invasion"` is flagged. It stays because rule 4's other
  members collide with forms appearing in *organisation* names while this one
  collides with a research topic — but 41 of the corpus's 417 names run to ten
  words or more, so topic strings do reach this field. Unmeasured, pinned by
  `TestTheKnownFalsePositivesAreKnown`, and #157 is what would settle it. Do
  not quote the distinction as measured, and do not silently drop the token:
  refusing it is a behaviour change of the same class as admitting it was.
- **`co` stays out on a stated risk, not a measured one** (#112). It scores
  4 TP / 0 FP against the committed corpus and the collision once recorded
  against it (`"project co-sponsored by province…"`) is not in that corpus at
  all. It is refused because `\bco\b` reaches *co-sponsored*, *co-funded*
  and *Co-operative* in the wild, at the price of one true positive no other
  token reaches, `"Merck & Co.; Merck Sharp & Dohme"`. Re-deciding it needs a
  corpus that contains the collision, not a re-reading of this one.
- **`_is_industry_funder()` is deliberately not applied to COI prose**;
  `_INDUSTRY_COI_KEYWORDS` stays separate — org suffixes match far too
  freely in running text.
- **Every `analyze()` sub-step takes `_Analysis`, mutates it, and returns
  `None`** — one step threading a value while four mutate is the
  inconsistency that makes the next contributor guess. Pinned by
  `test_the_merge_applies_both_of_its_branches_to_one_list`.
- **The data-deposition rank-merge machinery** (`_DATA_LEVEL_RANK`,
  `note_data_level()`, `_DEPOSITION_DATABANK_LEVELS`) is argued inline in
  `transparency/analyzer.py`. Two rules: every producible level must be a
  key of the ranking or `note_data_level()` raises by design; the deposition
  list deliberately excludes reference-only databases (dbSNP, OMIM, RefSeq…).
  Three tests in `test_transparency.py` pin it.
- **A bmlib defect in the full-text request logs ERROR and does not raise**
  (#187). `_fetch_europepmc_fulltext` classifies what `client.get` raised
  against a restated `_BUG_TYPES` and logs a `TypeError`/`AttributeError`/
  `NameError`/`KeyError`/`IndexError` at ERROR, since those can only mean
  bmlib is wrong — but it still returns rather than propagating, which was the
  issue's other option. Three reasons, and re-raising undoes all three: every
  network step in the module swallows its own request, so one dead API cannot
  cost an analysis (`analyze()` itself wraps nothing, which is why each step
  must); `fulltext/service.py` — the precedent #187 itself cites — reports a
  `_BUG_TYPES` member through `on_bug` and continues, rather than raising —
  **at WARNING, not ERROR**: `on_bug` is `_warn_swallowed_bug`, which routes
  through `_warn_once`, and that module has no `logger.error` at all, so it is
  the precedent for continuing and `jats_parser` is the precedent for the
  level (PR #192's review); and making *this* step alone fatal changes what
  a public `analyze()` may raise while `_check_crossref`'s identical defect stays
  swallowed one method away. The ERROR is what an operator acts on; the stored
  `REQUEST_FAILED` is what the result is audited by afterwards. Tests:
  `TestAnAttemptThatGotNoAnswerSaysSo::test_a_bmlib_defect_is_reported_as_one`
  and `::test_a_bmlib_defect_does_not_cost_the_analysis`.
- **`_BUG_TYPES` is restated here too, not imported from
  `bmlib.fulltext.service`** (#187). The `_NESTED_ARTICLE_ELEMENTS` decision
  above, one constant over, and for the same reason. Unlike the samplers'
  predicates — which must *differ* from the code under test — the two copies
  must **agree**: this is one claim about Python's exception hierarchy, not a
  judgement about anyone's data, and the drift that matters is one-sided (a
  type added to `fulltext`'s copy alone goes on being held at DEBUG here). So
  it is pinned rather than left to prose:
  `tests/test_transparency.py::TestTheRestatedBugTypesMatchTheOtherModules`.
  What is load-bearing is what the list *excludes* — `ValueError` carries
  `json.JSONDecodeError`, `SyntaxError` carries `ET.ParseError`,
  `RuntimeError` carries `RecursionError`, and `OSError` is the environment —
  so adding any of the four would report a remote-data failure as a bmlib
  defect and break the ERROR level's meaning from the other side. **The
  exclusions are pinned by `isinstance`, never by `not in _BUG_TYPES`**
  (PR #192's review): a membership test sees only the names it was given,
  so replacing `KeyError, IndexError` with their shared base `LookupError`
  in *both* copies passed the whole suite — the agreement test saw two
  edited copies agreeing and the exclusion test saw a name nobody had told
  it about, while the deny-list had silently widened to every `LookupError`
  subclass. Test the relation the code uses.
- **`analyzer_version` is deliberately not bumped for the `NOT_SERVED`
  narrowing** (#191, raised in PR #192's review). Narrowing a published
  enum member's meaning is normally a migration problem: a stored
  `not_served` written before the change may have been a 503, and after
  it the member is documented as *"Europe PMC answered HTTP 404"*, so a
  consumer joining an existing corpus would read legacy rows as 404s —
  the failure mode `full_text_status=None` exists to prevent one field
  up. It does not arise here, and the reason is release scope rather
  than design: `FullTextStatus` was added by #161 in the **same
  `[Unreleased]` block**, so no published version of bmlib has ever
  written the field at all and the two meanings never ship apart. Only a
  corpus written from unreleased `main` between #161 and #191 can hold
  the old meaning, and nothing distinguishes it there. Do not re-open
  this as a compatibility gap — but note that the argument expires the
  moment `FullTextStatus` ships: a *later* narrowing of any member would
  need the bump, and `analyzer_version` has never been moved off `1.0`.
- **The empty-body guard is `not served`, never `not served.strip()`** (#190).
  It sits at the status dispatch, above all four refusals, because an empty
  body is not a claim about a document's shape — nothing arrived to have one.
  Tightening it to `.strip()` looks like the same test and is not: a body of
  whitespace *did* arrive, and moving the boundary makes the entirely-nested
  branch unreachable for a document whose nested regions strip out leaving
  whitespace. Pinned both ways —
  `::test_an_empty_body_is_not_a_refusal_that_did_not_happen` and
  `::test_a_whitespace_only_body_is_still_entirely_nested_not_empty` — and
  the second is what a mutant flips.
- **`NOT_SERVED` is the 404 and only the 404** (#191). It used to take every
  non-200 and every raised request, which generalised the DEBUG level's own
  measurement past what that measurement looked for. Do not re-widen it to
  save a branch: a 429, a 503 or a 403 is not Europe PMC saying it holds no
  full text for this article, and with `cache_results` defaulting `True` and
  no retry anywhere in `transparency/`, an outage window that stores
  `NOT_SERVED` caches absences indistinguishable from legitimately
  closed-access papers. Measured 2026-09-05 over 200 stratified live probes:
  81 of 81 non-200s were 404, so every non-200 the quiet branch has been seen
  to take is one and the loud one fires on nothing in a healthy draw. That
  draw addressed records as `_check_europepmc` addressed them *before* #188,
  so it is over a superset of what this branch now takes — the committed
  instrument reports the narrowed population separately (3 of 9 on
  2026-09-09), and what the level actually rests on is that an
  `isOpenAccess: N` record with a good accession still 404s, 0 of 53.
- **An address is recognised by its shape, not by the record's `source`**
  (#188). The measurement that sized this reads *"the accession expression is
  right for `PPR` and never right for `MED`, and the record's own `source` is
  what separates them"*, which is true and is not the rule that got written.
  The two agree on every population drawn; they differ where an
  accession-shaped identifier arrives under a source nobody has enumerated,
  and there the allow-list refuses a fetch that would have worked. That is the
  loss this issue's own comment calls worse than the wasted request it saves,
  and it is `_check_europepmc`'s dropped `source` guard (#184) waiting to
  happen again. `fullmatch` on `(?:PMC|PPR)\d+`, so `"PMC123\n"` is not an
  address — `fulltext/service.py`'s `_PMC_ID_RE` for the same reason, and the
  two modules still deliberately disagree about the identifier. Do not
  "simplify" this into a `source` test, and do not delete the `or id`
  fallback: 75,841 `SRC:PPR AND IN_EPMC:Y` records have no other address.
- **It stores `NOT_ATTEMPTED` rather than a member of its own** (#188). The
  member reads *"no request was made, and Europe PMC's own answer is why"*,
  and the record **is** Europe PMC's answer: it names no accession for this
  article. That reading is exact here — which is precisely what #207 says it
  is not for the sibling cause one guard up (a record claiming `inEPMC: Y` and
  carrying nothing at all, which is malformed and WARNs). Two guards, two
  levels, one status. Do not fold the two guards together to save a branch:
  the levels are the difference, and this one fired for 43 of the 123 records
  of a source-stratified draw where that one measured 0 of 124. (A share of
  that draw, not a rate over a caller's corpus, which follows its source mix
  and is not measured.)
- **The full-text endpoint keeps its shape table on a rule the other five do
  not get** (#216, from that instrument's own first run).
  `shapes_reportable`'s second rule — *"a probe that reached no body is as
  uninformative as a throttled one"* — was written for five endpoints at which
  a non-200 is close to unheard of. `europepmc_fulltext` is the one whose gate
  (`inEPMC`) is deliberately wider than what it serves; applying the rule
  reported ERROR and flipped the exit code on a clean run. **Two mechanisms
  produce that 46 of 52 and only one is the gate**: 3 of the 9 accession
  addresses 404 because `inEPMC` is wider than the open-access subset, and
  the other 43 are `id-not-an-address` probes the script deliberately keeps
  making after #188 stopped bmlib making them. The exception is right either
  way, and the majority is the second mechanism — so *"a 404 is the ordinary
  majority outcome"* must not be carried out of this row. The exception is a
  named set of exactly one member (asserted), it drops only that rule
  (throttling and the empty-population floor still apply), and every row
  carries its Wilson interval, so a distribution over six bodies prints as
  one; it **is** the `bool(shapes)` floor PR #213 removed, restored here and
  nowhere else, and the interval is what makes it safe. Do not generalise the
  exception to another endpoint without a draw saying its non-200 is ordinary
  — that is #191's rule one instrument over.
- **The five `_ORDINARY_STATUSES` sets are empty as a measurement, not as a
  placeholder** (#193). A status is quiet only where a draw measured it to be
  that endpoint's ordinary outcome, which is #191's rule stated forward
  instead of backward. `scripts/sample_api_failures.py` read 0 non-200s at
  all five endpoints over 240 drawn records (2026-09-06; upper bounds
  2.1%–6.8%), so nothing has earned one and every non-200 warns. Do not
  populate a set to quieten a log — run the sampler.
  `::test_no_endpoint_claims_an_ordinary_status_today` is the gate, and the
  mechanism is exercised separately so five empty sets are not mistaken for
  wiring that does not work.
- **`TransparencyResult.trial_results_compliant` is a bare `bool` and `False`
  covers two claims; the *step* no longer does** (#193, #194, and PR #195's
  review, which reopened this entry). It first read that
  `_check_trial_results`' own `bool` was a deliberate residual because #194
  had been fixed by correcting the header. That was wrong on its own terms:
  correcting the header narrowed the false claim from *always* to *whenever
  ClinicalTrials.gov does not answer* and left the conflation intact, so a
  404, a 403 or an unusable body still made the caller store *"Registered
  trial without posted results"* — a persisted claim about the trial that
  bmlib has no evidence for. The remedy cost six lines and needed no new
  vocabulary, `_INDICATOR_RESULTS_NOT_CHECKABLE` having existed all along for
  the other-registry case, so the deferral was resting on a cost comparison
  ("the `FullTextStatus` argument one endpoint over") that overstated it.
  `_check_trial_results` is now `True` / `False` / `None`, and the caller
  reports three outcomes — **four since #206**, which added
  `PARTLY_ANSWERED`; this entry is reopened in place rather than left counting
  three, the convention this register keeps.

  **The residual was taken too, one session later** (#198). It was recorded
  here as *"not worth a schema change today"* — `trial_results_compliant` is
  `False` for *"asked and answered no"* and for *"could not be checked"*
  alike, `risk_indicators` distinguishes them, and no downstream was newly
  wrong. Two things overturned that. The read it asks of a downstream is one
  neither known downstream makes (both render the flag), which is precisely
  how #194 published a false claim for a release; and the schema was already
  moving in the same unreleased batch, so the recompute a downstream owes for
  #184 and #194 covers this field for free, where after a release it would
  cost a second one. **The cost of a schema addition is not a constant — it
  depends on what else is unreleased beside it**, and this entry priced it as
  though it were.
- **`_find_trial_ids` takes the record and cannot fetch, and the fallback is
  not coming back** (#202). It was a method taking a client, falling back to
  its own Europe PMC search when no record was passed — which reads as
  convenience and was in fact a defect, since `epmc is None` is what a
  *failed* search returns, so an outage issued the identical failing search
  twice per document and (after PR #195) reported it twice. Restoring the
  fallback restores that: the two `None`s are indistinguishable at the
  signature, and no sentinel is needed once the parameter is mandatory.
  `analyze()` is the only caller and has always had the record in hand. A
  trial id scraped out of an abstract bmlib never received is not a thing that
  can happen.
- **The provenance line is appended after every step, not where the status is
  decided** (#203). It reads like misplaced code — the status is set deep in
  the full-text path and the line is appended near the end of `analyze()` —
  and moving it is what reintroduces the defect. `_merge_pubmed_signals`
  retracts indicators, so a line appended before it is inside the retraction
  window and protected only by staying out of
  `_INDICATORS_RETRACTED_BY_PUBMED_COI` — set membership, which is exactly
  what went wrong in #161 and again in #193. Appending afterwards makes the
  protection structural. Measured, and stated per edit rather than in the
  aggregate (PR #205's review): both single-edit mutants are *behaviourally*
  equivalent, but only one is unobserved — moving the append above
  `_merge_pubmed_signals` survives all 411 of `test_transparency.py`, while
  putting a provenance line into the retraction set reddens 2. Never appending
  it at all reddens 5, and breaking both protections together reddens the 3
  written for it.
- **`REQUEST_FAILED` and `NOT_CHECKABLE` share one indicator string and are
  still two enum members** (#198). It looks like the vocabulary disagreeing
  with itself. The prose deliberately does not split them — *"posted-results
  status could not be checked"* is the identical claim and puts nothing in
  ClinicalTrials.gov's mouth, and nothing downstream can act on the difference
  in a sentence — while the enum must, because *"would re-running change
  this?"* is `yes` for one and `no` for the other, results are cacheable, and
  nothing in `transparency/` retries. Prose is for a human deciding what a
  score means; the enum is for a caller deciding what to re-run. The same
  division `FullTextStatus` makes between `NOT_SERVED` and `REQUEST_FAILED`.
- **The sampler uses the analyzer's client, not a sampler one** (PR #195's
  review). It opened with `timeout=45.0, follow_redirects=True` where
  `analyze()` uses `_HTTP_TIMEOUT_SECONDS` and httpx's default `False`, so a
  3xx — which `FullTextStatus.REQUEST_FAILED` names explicitly as an outcome —
  was a 200 to the sampler and a dropped response to bmlib, and a slow reply
  succeeded here and timed out there. A measurement taken under a laxer
  transport policy than the code uses is a measurement of a different client,
  which is the same failure as measuring a different URL or a different
  header. `follow_redirects=False` is written out rather than left to the
  default, because here it is a decision.
- **`_user_agent` appends `python-httpx`, and it is not decoration** (#194).
  ClinicalTrials.gov's edge refuses every other header shape measured —
  `curl`, `python-requests`, `Python-urllib`, `Go-http-client`,
  `PostmanRuntime` and a browser string included — with a bare 134-byte 403.
  Do not "tidy" the token out: it is the whole reason step 6 of the pipeline
  works at all. It is appended to bmlib's identification rather than
  replacing it, since CrossRef and NCBI both ask a caller to name itself, and
  it is true — bmlib *is* httpx here. **No test can hold this**, every test in
  the suite mocking its client, which is exactly why the 403 survived a whole
  release; the sampler is the guard, and the unit tests only stop the token
  being dropped by a tidy-up.
- **The sampler imports the analyzer's URLs and header, against the rule its
  siblings follow** (#193). Every other live runner in `scripts/` is forbidden
  from importing the predicate it measures, because a corpus labelled by the
  rule under test can only confirm that rule. Neither import here is such a
  predicate: this script's subject *is* the request, so a restated literal
  would measure somebody else's endpoint — which is precisely how #184 lived a
  release, and #194 the same thing in a header.
  `TestTheSamplerProbesWhatTheAnalyzerRequests` drives both and diffs, rather
  than asserting that a constant was imported, which a restated literal
  passes.
- **Four more, each argued where it lives and each with a test naming it:**
  `TransparencySettings.filtering_enabled` / `max_concurrent_analyses` /
  `cache_results` are caller-owned orchestration hints, not dead code;
  `outcome_switching_detected` stays reserved and always `False` (kept in the
  schema so persisted results need no migration when detection lands); a
  PubMed record with no `<CoiStatement>` leaves `coi_disclosed` alone (absence
  means the publisher supplied none); and `<DataBankList>` accessions are
  validated as `NCT\d{8}` before becoming a URL, though an entry failing
  validation still counts as registered — registration is separate from
  followability.

- **`_check_trial_results` keeps its `isinstance(data, dict)` although
  `_request_json` now guarantees one** (#199). Since `_request_json` returns
  `dict[str, Any] | None`, that branch is reachable only for `None`, so it
  reads as dead code a `data is None` would say more plainly. It is kept as
  the second of two independent protections at the one site in the module
  where an unusable body did not merely raise but **published a false finding
  about a trial for a whole release** (#194) — the redundancy #203 argues for,
  placed where the measured cost of getting it wrong is worst. The comment at
  the site was rewritten when the guard moved; do not narrow it back on the
  grounds that mypy proves it redundant, because what it defends against is a
  future change to `_request_json`'s promise, which mypy would happily accept.
  **PR #208's review found this entry unenforced** — narrowing it passed the
  entire suite, `_request_json` having closed the only path that reached it
  with a non-object — so `test_an_unusable_body_is_refused_at_this_site_too`
  stubs the boundary, which is the only way left to exercise what the guard
  defends. The prose is the argument; the test is the enforcement.

- **`_request_json` promises a JSON *object*, and an endpoint that legitimately
  serves an array must get its own helper rather than a flag on this one**
  (#199). All four endpoints this helper serves answer with objects — PubMed
  is the fifth endpoint the module calls and reads XML through
  `_request_text`, so it is not one of them (PR #208's review) — and every
  reader here uses `.get()`, so narrowing the return type made three existing
  `dict | None` annotations true rather than adding a claim. Widening it back
  to `Any` — or adding an `allow_array=` parameter — reopens the 12 escapes
  the narrowing closed, for every caller and not only the new one. This is
  `_request_text`'s rule about `headers` one method up: add the second helper
  when the second endpoint arrives, not before.

- **The JSON coercers are coercers and not reporting guards** (#199).
  `_json_object`, `_json_text`, `_json_count` and `_epmc_records` return an
  empty value rather than logging, which reads as swallowing. The request
  itself has already been reported at `_request_json`, and a value of the
  wrong type is the *absence* of the value that was asked for — exactly what
  every reader here already does with an absent key. Adding a line per
  coerced value would emit one per field of every malformed body, which is
  the 200-identical-lines shape `jats_parser` settled.
  **Two corrections from PR #208's review.** The original entry said the
  request *"has already been reported at `_request_json`"*, which is false for
  precisely the case the coercers exist to handle: a 200 carrying a
  well-formed object whose *value* is wrong is reported nowhere, at no level.
  And `jats_parser` settled the 200-lines problem by **counting and reporting
  once per article at WARNING** (`rejected_spans`, `formulas_dropped`), not by
  silence — so that precedent rules out a line *per field*, not reporting.
  The silence is therefore a choice and not a consequence, and the
  per-analysis tally the precedent actually suggests is filed as #209 rather
  than argued away here.
- **The three ways a PubMed body can carry no `PubmedArticle` do not share a
  level** (#218). One DEBUG line would have been the cheap reading of the
  issue and would have repeated #191 exactly: the draw that sized the branch
  — `no-citation` for 50 of 60 served bodies on **2026-09-09**, the run that
  first carried the counter; quoting it against the 09-08 draw attributes a
  reading to an instrument that could not produce it — is heavily NCBI
  Bookshelf, so
  it licenses a quiet level for **book records** and says nothing about the
  other two. A book record is declined by name and carries none of the three
  signals (0 of 60 `statpearls[book]` and 0 of 100 `pubmed books[filter]`,
  probed 2026-09-10), so nothing is lost and it is DEBUG. An empty
  `<PubmedArticleSet>` means NCBI holds no record for an identifier bmlib was
  given or derived, which is a fact about the identifier and WARNING. Anything
  else that parses is a document bmlib does not recognise, also WARNING, with
  the root element named. Do not fold them back together, and do not read the
  50 of 60 as a rate: a stratum is one contiguous cursor page.
- **`<PubmedBookArticle>` stays unread, and the reason is now a measurement
  rather than a DTD reading** (#218's second question). The old comment
  asserted from the DTD that a book carries no `<CoiStatement>` and no
  `<DataBankList>`, and conceded `<GrantList>` was being given up unmeasured.
  Across 160 live book records not one carries any of the three. Reversing
  this means changing
  `tests/test_transparency.py::TestABookRecordCarriesNoneOfTheSignals`, which
  exists so the decision cannot be re-opened by inspection.
- **`<eFetchResult><ERROR>` at HTTP 200 is a different request's shape**
  (#218). Both the issue and `scripts/sample_api_failures.py` named it as a
  population reaching the no-citation branch, and the claim is not wrong — it
  is about the **history-session** efetch, which is why
  `publications/fetchers/pubmed.py` refuses a root that is not a record set,
  and probing an evicted session on 2026-09-10 reproduced it at 200.
  `transparency` fetches **by id**, where the same probe read 400 for a
  malformed id list and an empty record set for an id NCBI does not hold. So
  nothing was built for it here; the unrecognised-document branch takes it if
  some other error class turns out to arrive at 200, two classes on one
  request shape not being every class. Do not "reconcile" the two modules'
  comments by changing either — they describe different requests.
- **The truncated accession list and the unanswered accession are one status
  member, and only the first gets a line** (#206). Both mean bmlib did not ask
  about every accession, so neither leaves `NOT_POSTED`'s claim about the
  paper earned; they share `TrialResultsStatus.PARTLY_ANSWERED` and
  `_INDICATOR_RESULTS_NOT_CHECKABLE` for the reason `REQUEST_FAILED` and
  `NOT_CHECKABLE` already share a string. The question that does earn a
  separate member elsewhere — *"would re-running change this?"* — separates
  nothing here: a re-run under the same cap truncates identically. The **log**
  splits them because raising the cap is an action an operator can take, and
  an unanswered accession already has a line from `_request`.
- **`PARTLY_ANSWERED` is on the unanswered side of the partition** (#206).
  ClinicalTrials.gov did answer for some accessions, so the answered side
  looks defensible; it is not. `is_answered` exists so a downstream knows
  whether `trial_results_compliant` means what it says, and both known
  downstreams render that flag — `False` under `is_answered` `True` is
  read as *"the trial fell short"*, the unearned sentence #198 exists to stop
  being published.
- **The cap is reported, not raised** (#206). `MAX_TRIAL_IDS_TO_CHECK = 3`
  bounds requests per paper, and how far the accession-count distribution runs
  past three is unmeasured — `scripts/sample_api_failures.py` records each
  paper's count before the cap, so a run would answer it with one more report
  line; `summarise_trial_checks` prints the verdict distribution and the
  truncated share, not the distribution of the count. What the 2026-09-08
  **trial-enriched** draw did establish is that the cap truncates 8 of 30
  papers naming an accession against a partly-answered check of 1 of 30, which
  reverses the issue's own emphasis. Read the 1 as a **floor**: that draw
  predates PR #213's correction of `TrialCheck.answered`, which counted HTTP
  200 where bmlib counts a non-`None` return and so deflated exactly that row,
  while the truncation count derives from `found > probed` and is unaffected —
  which is why the comparison rests on the 8. Do not raise the constant on
  that evidence: it sizes *how often* the cap bites and not *by how much*.
- **The cap's WARNING is gated on the walk not having concluded `POSTED`**
  (#206). A posted result settles the paper, so the accessions behind it cost
  nothing and a line there would be noise on the one outcome beyond doubt. It
  is deliberately **not** gated on the resulting status: a walk where nobody
  answered reaches `REQUEST_FAILED` and the truncation is still real, and
  hiding it behind an outage would conflate two causes calling for different
  actions. Metric test:
  `tests/test_transparency.py::TestAPartialResultsCheckIsNotAFinding`.
- **A repeated accession is deduplicated at the parser** (#206, PR #225's
  review). MEDLINE's `<DataBankList>` is `(DataBank+)` and each `<DataBank>`
  carries its own `<AccessionNumberList>`, so one paper naming one trial twice
  is well-formed input — the shape `publications/` already collapses for
  `<Grant>` at 31 of 575 entries across 200 records. While `answered` was a
  `bool` and the cap was silent it cost only a redundant request; #206 made
  `len(ct_ids)` a WARNING's denominator and `dropped` the thing that decides
  `PARTLY_ANSWERED`, so four entries naming one trial reported a truncation
  that lost nothing and retracted a `NOT_POSTED` ClinicalTrials.gov had
  answered for every distinct trial the paper named. That is #206's own false
  claim in the mirror, manufactured by its fix. It is fixed at
  `_parse_pubmed_signals` rather than at the walk because the field is what
  the sampler reads too, so the instrument's own truncation count would
  otherwise be inflated by repeats; `_find_trial_ids`, the other producer, has
  deduplicated since #202, and the walk therefore receives a clean list from
  both. Order-preserving: the cap slices by the paper's own order.
- **The cap's WARNING names the accessions it dropped** (#206, PR #225's
  review). They are the whole of what raising the cap would recover, and
  without them the line had no subject at all — in the same commit whose other
  half threads a PMID through `_parse_pubmed_signals` for exactly that reason,
  and in a module whose `TransparencySettings.max_concurrent_analyses` makes
  interleaved lines the expected case.
- **The book branch tests children, and every child** (#218, PR #225's
  review). `.//PubmedBookArticle` matched a book anywhere in the document and
  was tried first, so a legal mixed set — `PubmedArticleSet` is
  `(PubmedArticle | PubmedBookArticle)*` — reported an article record carrying
  no `<MedlineCitation>` at DEBUG on the strength of its book neighbour. The
  160-record draw licensing that level is of responses that **are** book
  records, which is narrower than *"carries one"*, so the descendant test was
  #191's defect inside #218's own fix. A document whose own root is the book
  element takes the unrecognised-document branch: NCBI wraps every record in a
  set, so bmlib has no measured reading of a bare one.
- **`TrialResultsStatus.PARTLY_ANSWERED` merges its two causes because they
  co-occur, not because the usual criterion merges them** (#206, PR #225's
  review). *"Would re-running change this?"* is `no` for the cap and `yes` for
  an accession that did not answer, so the criterion that earns
  `REQUEST_FAILED` and `NOT_CHECKABLE` separate members does discriminate here
  — the first draft of the argument reasoned about the cap alone and
  generalised to the member, which is #191 one more time. What rules a split
  out is that the walk computes `unestablished = dropped + asked - answered`,
  a **sum**: one paper can have both causes at once, so splitting needs three
  members or a second field. The residual is real and filed: a downstream
  doing selective backfill cannot ask *"retry, or change the config?"* of the
  stored value.

## Repository process

- **Squash, rebase and merge commits are all enabled on purpose — do not
  disable two of them to protect the release tag** (#78, closed 2026-08-13).
  The old release recipe required `--merge` so the tag landed on `main`'s
  first-parent line, and #78 proposed enforcing that with one
  `gh api -X PATCH`. Measured before acting: **8 of the last 40 merged PRs
  landed as single-parent commits** (#60, #62, #63, #65, #66, #69, #74, #76),
  each collapsing a 3–7 commit branch, so squash is a deliberate habit for
  ordinary feature PRs and not an accident waiting to bite. GitHub cannot
  condition the merge method on the branch — `allow_squash_merge` is
  repo-wide and no ruleset rule expresses "release PRs must be merge
  commits" — so enforcement would have cost the habit to protect one PR a
  month.

  **The requirement was removed rather than enforced.** `main`'s tip is on
  `main`'s first-parent line under every merge strategy, so the recipe tags
  `main`'s tip after `git pull --ff-only`. The two guards it carries replace
  a constraint that never checked what mattered: `HEAD == origin/main`
  catches a stale local `main`, and grepping `__version__` at the tag target
  catches tagging a commit that does not carry the version — which is the
  failure `release.yml` would otherwise find *after* the release is public
  and the version burned. Neither is something a merge method could have
  caught. The recipe is in `HANDOVER.md` under "Cutting a release".

  The `protect_main` ruleset is a separate thing and closes none of this: it
  covers deletion, non-fast-forward pushes and code scanning, and is silent
  on merge strategy.

## Type checking (#81)

- **`**kwargs: object` and `**kwargs: Any` coexist on purpose — do not make
  the 25 sites uniform.** Seven bags are `Any`, eighteen are `object`. The
  trigger is narrower than "is it splatted?", and getting it wrong in either
  direction is why this entry exists: a bag needs `Any` when it is splatted
  into a callee that still has **a typed named parameter the call does not
  itself fill**. `object` is the stricter annotation and cannot survive that
  case — a parameter declared `str | None` will not accept an `object`, so
  `**dict[str, object]` makes the forwarding call *unchecked* rather than
  checked. It produced nine of the errors #81 fixed.

  "Splatted" alone does not decide it. `LLMClient.chat` and `LLMClient.embed`
  are both splatted into typed signatures and both correctly keep `object`,
  because they pass every named parameter of the callee explicitly and the
  residual can only land on the callee's own `**kwargs: object`. A reader
  applying the looser rule would "fix" two sites that are already right.

  The seven: `agents/base.py`, `llm/client.py` (×2, `generate` and
  `embed_batch`), `llm/providers/get_provider()` — where the rule is written
  out and which the other three point back to — plus `providers/ollama.py`
  (×3, at the two `ProviderCapabilities`/`ModelMetadata` subclass
  constructors and `embed`), which predate #81 and obey the same rule.

  What the widening does **not** cost is the boundary: `object` already
  accepts every keyword argument a caller can pass, so the two annotations
  are indistinguishable from outside. The loss is confined to the body, and
  all seven bodies only forward. That is what makes keeping the other
  eighteen at `object` worth doing rather than merely tidy — and why
  widening them would weaken annotations that cost nothing today, while
  narrowing the seven re-breaks the calls.
- **`_reject_unusable_stream()`'s `isinstance(handle, io.TextIOBase)` is
  unreachable per the annotation, and stays.** Nothing can subclass both
  `IO[bytes]` and `TextIOBase`, so `warn_unreachable` calls the body dead. The
  annotation is a request, not an enforcement, and the guard exists for the
  caller who passes `open(path)` in text mode — which the signature's `str |
  Path` branch makes a plausible slip. Deleting it to satisfy the checker
  restores a failure inside `codecs` reading "can't concat str to bytes",
  which names nothing the caller did. Argued inline; carries
  `# type: ignore[unreachable]`.
- **Deliberately-unchecked code takes an inline `# type: ignore[code]`, never
  a per-module `ignore_missing_imports` override.** `warn_unused_ignores`
  reports an inline ignore the day it stops suppressing anything; it can never
  report a stale override. #81 removed a stale `# type: ignore[arg-type]` in
  `retractions.py` that this setting caught. bmlib now has **no untyped
  imports at all** — see the next bullet.
- **`pdf_converter.py` imports `pymupdf`, not the legacy `fitz` alias, and
  the `pdf` extra floors at a release that ships `py.typed`.** PyMuPDF added
  the marker in 1.27.1, but `setup.py` writes it only into the `pymupdf`
  package; the three modules it copies into `fitz/` are never covered. So
  `import fitz` costs a `# type: ignore[import-untyped]` that **no PyMuPDF
  release can ever retire**, and that ignore switches off type checking for
  the whole module — verified: under the alias, a call to a non-existent
  PyMuPDF attribute is not reported; under `import pymupdf` it is an
  `attr-defined` error. This is the case the previous bullet's convention
  cannot handle, because the ignore would never go stale and so would never
  be revisited. The floor is `>=1.28.2` (current when set); `>=1.27.1` is
  the minimum the type reason justifies, with the module name itself
  arriving in 1.24.3.
- **`fetch_pubmed()`'s `count == 0` return stays *ahead* of the
  history-session guard, and that is only safe because `_esearch()` refuses
  an absent `<Count>`.** With `usehistory=y` NCBI returns a session even for
  a zero-hit day, so an empty day legitimately needs no session and must
  report `completed` — moving the guard first turns every quiet day into a
  `failed` fetch that `sync` retries forever. But the ordering means
  anything that reaches `count == 0` bypasses the guard entirely, which is
  how a rejected search used to sync as a quiet day: `_text()` returns
  `None` for an absent element and `or "0"` made an `<ERROR>` document a
  count of zero. The two decisions hold each other up — keep the ordering,
  keep the refusal, and do not collapse the refusal back into `or "0"`.
  Pinned by `test_a_rejected_search_is_not_reported_as_a_quiet_day` and
  `test_a_genuinely_empty_day_still_completes`; both verified by mutation.
- **mypy must run in the dev venv, and `uv run mypy` takes no arguments.**
  Every extra but psycopg2 ships its own `py.typed` — that one is covered by
  `types-psycopg2` in the `dev` extra — so against a bare interpreter mypy
  reports the optional imports *and `jinja2`, a core dependency*, as missing
  stubs. #81 opened claiming 24 errors in 15 files because of exactly this.
  Installing the extras took it to 22 in 11; adding `types-psycopg2`, which
  #81 also did, retires the two `psycopg2` errors and leaves **20 in 10** —
  which is what re-running the gate against `main` in a `.[all,dev]` venv
  reproduces today. Scope and settings live in `pyproject.toml` so the bare
  command is what CI's `types` job runs.

## Positional stability

- **`Publication.pmcid`, `BaseAgent.__init__`'s `embedding_model`, and
  `TransparencyResult.unknown_reason` are each declared last** on their
  dataclass/signature — downstream projects construct positionally, and any
  other placement shifts every following argument silently. Pinned by
  `test_positional_construction_is_stable_across_versions`.

## db / llm / agents

- **PostgreSQL transaction nesting is detected from bmlib's own open-block
  count, not psycopg2's status**, keyed by *(thread, `id(conn)`)* — see
  CLAUDE.md for why both parts are load-bearing.
- **The Ollama raw `/api/tags` path re-implements httpx's safety defaults on
  purpose** (HTTP(S)-only scheme, bearer token stripped across cross-origin
  redirects, `"<word>:<digits>"` read as host:port). Each has a regression
  test naming it.
- **The JSON extractors prefer a whole span to a nested fragment in three
  places** (argued inline in `llm/utils.py`, `llm/json_repair.py`,
  `agents/base.py` — all guarding #33's silent truncation), **a fenced
  candidate wins on parse alone**, **`parse_json()` enforces `dict | list`**
  (a bare scalar raises → retry inside `chat_json()`), **`require_dict` has
  a third `bool` overload** (mypy does not expand `bool` into the two
  `Literal`s; CI runs ruff only, so nothing catches its removal), and
  **`salvage_json_fields()` bounds both passes with `RecursionError` caught
  wherever a candidate is decoded**. `iter_json_spans()` dedupes candidates by
  text, not position. Eleven tests pin these — seven in
  `test_json_extraction.py` (from `TestExtractJsonPrefersWholeSpans`) and four
  in `test_agents.py`.
- **`PerformanceMetrics.elapsed_time_seconds` reads `time.monotonic()`**,
  not the wall-clock timestamps it stores; `snapshot()` must copy the
  monotonic marks by hand (`init=False`). Model-inference and prompt-eval
  timers are deliberately omitted — no provider reports them through bmlib.
  Pinned by `test_elapsed_survives_a_wall_clock_step` and
  `test_snapshot_carries_the_monotonic_marks`.

## context_processor

- **The batcher measures the string it will actually send; it never assumes
  a size.** Three tempting arithmetic shortcuts each re-break
  `max_context_chars` the way upstream did; the invariant is
  `Batch.total_chars == len(_format_batch_content(batch, config))`.
  `estimate_item_size()` was deliberately not ported — it let the oversized
  decision disagree with the packing measurement.
- **Six more load-bearing "simplifications" refused**, each with a named test
  in `test_context_processor.py` / `test_llm_chunk_processor.py`: `_render()`
  substitutes in one regex pass (two-pass `.replace()` splices the batch into
  a query containing `{content}`); the package `__init__` reaches
  `llm_processor` through PEP 562 `__getattr__` (a plain re-export drags
  jinja2 into the LLM-free harness); `process()` keeps statistics in a local,
  not on `self`; `success_rate` cannot return 1.0 for a batch-less run that
  dropped everything; the recursion wraps results in `ConsolidatedItem`, not
  a tuple (what made upstream's `format_consolidated_item()` dead code), and
  `min_items_for_recursion` stopping at one result is correct; and
  `LLMChunkProcessor` renders with `str.replace`, not `str.format` (templates
  legitimately hold literal braces).

## fulltext — retrieval and JATS

- **`_JATSHandler.endElement` tests `in_figure or in_table_wrap` before any
  prose branch, and routes the caption's own content by the open
  `<caption>`'s owner (`caption_stack`)** — asking about the section first
  blanks the caption and renames the section; the same branch deliberately
  drops non-caption `<p>` inside figures/tables. The owner replaced a stored
  `in_caption` boolean in issue #123, which both mis-routed a nested
  caption and truncated the enclosing one at its close. Pinned by
  `TestJATSParserCaptionScoping` and
  `TestJATSParserUnsectionedBodyFurniture`.
- **NCBI's ID Converter is consulted *after* the Europe PMC search** (the
  search also carries the free-PDF URL) **but *outside* the search's
  `except`, in its own statement** — a search that raised is exactly when a
  second resolver is worth its request, and one enclosing handler would
  swallow the error before the converter was reached. A converter-discovered
  PMC ID is tried at Europe PMC even when the search said `inEPMC="N"`, since
  a stale flag is one reason the converter exists. Two tests in
  `test_fulltext_service.py` pin it, starting at
  `test_the_converter_is_consulted_when_the_search_itself_failed`.
- **`_fetch_ncbi_pmc()` raises on a reply with neither body nor abstract** —
  efetch answers a publisher-withheld article with a stub that is HTTP 200
  and parses cleanly; returned instead of raised, it becomes near-empty HTML
  labelled `content_kind="abstract"`. Pinned by
  `test_a_stub_with_no_article_raises` and
  `test_a_body_less_article_with_an_abstract_is_returned`.

## fulltext — a formula is not one more `_INLINE_ELEMENTS` member (#147)

**Do not "fix" a dropped formula by adding `<tex-math>` to
`_INLINE_ELEMENTS`,** and do not print an equation number on a formula that
was merged into a sentence. Both look like the one-line version of the fix and
both are refuted by the corpus.

*The merge prints the formula twice.* 1,087 formulas in the committed recent
corpus and 188,473 across PMC's `oa_comm_xml.PMC012xxxxxx` baseline package
carry a LaTeX **and** a MathML encoding of one expression. MathML accumulates
no buffer, so its leaf text is already in the formula's own; merging the LaTeX
back as well emits both. The encodings are therefore held and one is chosen at
the formula's end tag, which also makes the choice independent of the order
they were deposited in — and that order does vary, though **say which
population that is**: 4,377 of the package's 188,473 both-encoding formulas
are MathML-first, sitting in **37 of its 97,909 articles** at ~118 apiece. It
is a house style, not a rate; a 997-article draw expects none, a random
4,000-article one measured 2, and the rule stands on the content model
admitting either order rather than on the count.

*And it prints a LaTeX document, not an expression.* 99.9% of 4,422 sampled
deposits are `\documentclass[12pt]{minimal}`, a run of `\usepackage` lines,
then `\begin{document}` — some 300 characters of preamble per formula, which
is worse than the drop it replaces. Every one of the 7,769 sampled
*document-wrapped* deposits carries exactly one
`\begin{document}`/`\end{document}` pair, and so do 147
of 147 in two articles fetched live from Europe PMC, so the body is
extractable; 96.0% of those bodies already carry `$$…$$` and 3.7% `$…$`, so
the depositor's own delimiters are kept rather than a second pair added.

*MathML needs no membership either*, and deliberately has none: it is why the
change is small, and it is what makes a MathML deposit bound to a prefix other
than `mml` keep exactly its old behaviour instead of depending on a literal
prefix match the way #128 does.

**The equation number is printed only where the equation stands apart.** A
`<disp-formula>` inside a `<p>` is merged into that paragraph, and emitted
separately each would land *ahead* of the paragraph it interrupts, the
enclosing `<p>` not having closed. How large that population is **depends on
the rendition, and this entry cited the wrong one**: 116,623 of 150,598
(77.4%) is over the *archive* bytes of the whole `PMC012xxxxxx` package, where
the served rendition `FullTextService` actually hands the parser measures
714 of 1,915 (37.3%) in the committed recent corpus and 201 of 654 (30.7%) in
the 880-article served draw — the two served measurements agreeing, the
archive the outlier, and a `<p>` therefore the *minority* parent on the bytes
that reach this code. The rule turns on neither share: both parents are
routed, one by merging and one by emitting a paragraph.
Printing the `<label>` there produced, over 880 local articles,
`'as shown in eqn (2):2 τ = kn'`, where `2 τ` is a coefficient the deposit
does not contain, and — for consecutive equations —
`'NH3 + H2O → NH4+ + OH−2 Al3+ + 3OH− → Al(OH)33 Al(OH)3'`, where each number
welds onto the previous formula and changes the chemistry. A corruption is
worse than a blank (#116, #162), and the prose introducing a merged equation
names its number in nearly every case anyway.

**A merged display formula gets one space either side; an inline one gets
whatever the deposit gave it.** The asymmetry is the point: a block deposit
has no spacing of its own to keep (the renderer supplies the line break), so
merged verbatim it welds onto the prose; an inline formula's separation is
often written *inside* the element — `<inline-formula> k </inline-formula>mer`
— and normalising without re-emitting it welded `'EndMatrix represents'` and
`'−minus 0.505'` into single words. That second rule is the module's own,
already written down for `_text_with_formatting`.

**But a cell is a slot, not a sentence, and there the number stays.** The
paragraph above is about prose, and applying it to a `<td>` was a regression
found in review: `characters()` used to deliver the label to the cell, so
withholding the formula's text took the equation number with it. All 40
labelled display formulas measured in a cell — 8 of the package's 97,909
articles — sit in a cell whose entire content is the number and the equation.
PMC12164272's Table 2 is a reaction-number column whose rows the body prose
cross-references by number; PMC12120668's tables 4, 6, 7 and 8 carry 18
equation numbers the same way. There is no surrounding sentence for the number
to weld into, which is the whole of why the prose rule does not reach it.

**Do not "keep the depositor's delimiters" for an inline formula.** The first
cut did, on the argument that re-spelling would invent a claim about the
deposit. It does not: measured over one Europe PMC package, **98.6% of 20,251
inline `<tex-math>` bodies carry `$$…$$`** (86 carry `$…$`, 203 none), and an
inline formula cannot genuinely be 98.6% display math — the
`minimal`-documentclass converter emits that wrapper for both contexts, so the
pair carries no information about which one. Left verbatim it rendered `'×'`
as `'$$\times$$'` inside a figure caption. A display pair on an inline formula
is therefore re-spelled `$…$`. The rule is **one-directional** — an inline pair
on a display formula is left alone, because a display delimiter inside a
sentence breaks the line while an inline one on a formula standing alone merely
under-styles it — and a body carrying several delimited runs (`$a$ + $b$`) is
left alone too, its outer characters not being one pair around one expression.

**Read the two document markers independently.** Requiring both let a deposit
carrying only `\begin{document}` fall through to the bare-expression path,
which then delimited the preamble and merged it into the prose:
`'$$\documentclass…\begin{document}$$E=mc^2$$'` — the outcome this whole entry
exists to prevent, *plus* the doubled pair the delimiter rule prevents, in one
string. 0 unpaired deposits measured in both corpora, so this is severity and
not frequency.

**Among several `<tex-math>`, the first that renders wins.** `<alternatives>`
holds alternative encodings of one expression, so joining them printed it
twice — the outcome this entry's opening argument says the design exists to
prevent. And the list is tested for a *rendition*, not for presence: an empty
or preamble-only deposit used to short-circuit the buffer that held the MathML
flattening, so `'Before Vmax after.'` became `'Before after.'`. Both
populations measure **0** across both corpora and 0 of 501,132 formulas in the
package, so both rules are stated rather than confirmed.

**A rendition that reaches nowhere is counted, not dropped.** `_append_prose`
has five branches and no fallthrough, so a standalone `<disp-formula>` reaching
none of them is built and lost. Not a regression — `main` discarded the whole
element — which is exactly why it is counted: `formulas_dropped` reports once
per article at WARNING, the granularity and level `rejected_spans` settled for
#129.

**#224 took the larger half of #177, and this paragraph used to say the
opposite.** It named the unsectioned-`<back>` shape (192 formulas in 23 of
97,909 articles) as the live population and gave the reason for leaving it
open: *"giving `<back>` an implicit section would move every unsectioned
`<back>` `<p>` as well, which reaches `has_body`."* Both halves were overturned
by the branch for #224 — `<back>` now has an implicit section, and it
deliberately does **not** reach `has_body`, `body_paragraph_count` still
counting `<body>` alone. See that issue's own entry below before concluding
anything from this one. What is left of #177 is two latent shapes: a formula
inside a float with no `<caption>` open (0 measured in both committed corpora),
and one standing outside `<front>`, `<body>` and `<back>` altogether — a
`<floats-group>`'s `<boxed-text>` (#253) — which is unmeasured. Front matter
was on that list until #230 routed it.
A formula refused as bibliography apparatus goes to `refused_apparatus_prose`
instead, or a chosen policy prints as a gap in itself. And the counter does not
see a formula merged into a `<p>` that is itself dropped, which is **#233**.

**Still open: whether LaTeX should win for a both-encoding *inline* formula
at all** (#178). For a display formula the preference is unambiguous — `main`
dropped the element whole — but inline it *replaces* text that already reached
the prose correctly, in **20,046 formulas against the 205 it recovers**
(8,000-article package draw). #174 is the case for it (flattened MathML loses
spacing and brackets); the case against is that `body_sections` is read as
prose by consumers that do not render LaTeX.

Pinned by `test_jats_parser.py::TestAFormulaReachesTheProseThatContainsIt`
(30 tests) and `::TestTheFormulaRulesTheReviewCorrected` (15 more, every one
mutation-verified), and by the `formula routing (#147)` counter generation in
`scripts/sample_jats_exhibits.py`, which is what makes the three package
populations above re-derivable at the next redraw — they are **not**
re-derivable from the committed corpora today, which carry no row for those
five counters.

## fulltext — back-matter prose routes, and `<ref-list>` is the one refusal (#224)

**Do not "reconcile" this with the Swift port, and do not widen it to
`<ref-list>`.** `_append_prose`'s unsectioned branch reads
`self._unsectioned_prose_is_the_articles()`, which is `in_body or (in_back and
no <ref-list> ancestor)`. The Swift port in BioMedLit widens the same branch to
a bare `inBody || inBack`, so a parity check finds a real difference here and
it is deliberate on this side.

**Why the refusal.** A `<ref>`'s `<note>` and a `<ref-list>`'s own `<p>` are
bibliography apparatus, not article prose. Sampled from Europe PMC's OA
package `PMC10030002_PMC10040000.xml.gz` they read *"Faculty Opinions
Recommendation"* ten times over in one article, *"Papers of special note have
been highlighted as: • of interest"*, bare DOI fragments, and a chemistry
paper's supporting-information note attached to the reference it belongs to.
Appended to `body_sections` each becomes a paragraph of an article that never
carried one — a corruption where the alternative is a blank, which is the
preference #116 and #162 already settled here. And #150 is the issue that puts
a note-only `<ref>` where it belongs: routing it into the prose now would leave
its content misfiled rather than missing, and its symptom invisible.

**What it costs**: 163 paragraphs in 39 of 8,118 served articles, 0.40% of the
40,505 the unsectioned branch is offered; 1,311 in 293 of 97,909 archive ones,
0.24% of 542,792. The first statement of this said 191 / 0.47% and 1,354 /
0.25%; those came from a raw-XML walk, which counts paragraphs the branch never
reaches — whitespace-only ones and `<p>` inside a back-matter float — so the
figures above are instrumented at `_append_prose` instead, and the per-container
rows now sum to their own totals, which the archive column did not. Ask what the
code routes, not what the markup holds.

**And it is reported.** `refused_apparatus_prose` counts it and `_audit_parse`
emits one WARNING per article, the granularity `rejected_spans` (#129) and
`formulas_dropped` (#177) both settled. Silence was the wrong answer twice
over: on `main` this prose was incidental collateral of a branch gated on
`in_body`, while here the refusal is named and argued, which earns a line
rather than excusing one — and #150 is a downstream that cannot learn the
content existed without it. WARNING, not ERROR, because a publisher's deposit
reaches it. A `<disp-formula>` refused by the same rule goes to the same
counter and **not** to `formulas_dropped`, or a policy this module chose would
print as a gap in it.

**The rule is scoped to the unsectioned branch, and "the one refusal" reads
wider than that.** Prose under an open `<sec>` never reaches the predicate, so
a `<ref-list>` inside a `<back>` `<sec>` keeps its apparatus, and so does one
in `<body>`, where `in_body` answers first. Both are pre-existing and both
measure near-empty — 0 apparatus paragraphs in 0 of 8,118 served articles, 1
in 1 of 97,909 archive ones — so this is a scope to state, not a hole to
close. Widening the refusal to the sectioned branch would need a population
first.

**Nothing else is refused, and that is not an oversight.** Every other
container here already routes this way *inside* `<body>` — a `<def-list>`'s
`<def><p>` in a body `<sec>` reaches that section today — so refusing one in
`<back>` would make identical markup mean two different things depending on
where the publisher put it. `<glossary>` is a large such population (10,693
served, second of six on that rendition and third on the archive one) and is
routed for exactly that reason, even though it arrived without its `<term>`
(#228, since answered) and #231 is what the resulting untitled section costs a
reader. Those are defects of their own, not an argument for dropping the
definition too.

**It is an ancestor test on `element_stack`, not `in_ref_list`.** JATS permits
a `<ref-list>` inside a `<ref-list>`; the flag is a bare boolean the inner
close clears, and it would then re-admit the outer list's remaining apparatus
— #115 one element family over. Pinned by
`test_a_nested_reference_list_is_refused_to_its_end`.

**The strict-ancestor slice is prospective**, like `_inside_mixed_citation`'s.
`_append_prose` is reached from the `<p>` and `<disp-formula>` arms only, so
the excluded element is never the `<ref-list>` being tested for: dropping the
slice survives the whole suite, and it was the one survivor of this change's
eight-mutant sweep. Kept so a third caller does not inherit a rule nobody
restated.

**A slot per container, and one slot would have hidden a defect in the
other.** `</body>` and `</back>` each flush unsectioned prose. Held in one
slot, a `</body>` flush that failed would leave its prose pending, `<back>`
would append to the same builder, and `</back>` would emit the pair as one
section — the article silently losing the boundary between its body and its
acknowledgements, with nothing stranded for `_audit_parse` to report. 73.8% of
the served corpus carries a `<back>` — that is the share gaining prose, so a
`<back>` holding only a `<ref-list>` is not counted and the true share is
higher — so almost every document would mask it.
`_flush_implicit_section` therefore picks its slot from `in_body` / `in_back`,
which is what makes each arm's flush-before-clear ordering load-bearing rather
than decorative — it was neither when the helper emptied whatever was pending,
and a comment claimed otherwise for two revisions. Pinned by
`TestTheBodySlotCannotBeEmptiedByTheBackFlush`.

**`has_body` is untouched and must stay untouched.** `body_paragraph_count`
counts `<body>` prose alone, so a front-matter-plus-back-matter document is
still body-less and `FullTextService` still holds it back rather than caching
it. `test_back_matter_alone_is_still_not_a_body` is the guard; the mutant that
counts back paragraphs dies there.

## fulltext — the article's own metadata is read at its owner path (#254, #259, #152)

The metadata arms (`<article-title>`, `<year>`, `<volume>`, `<issue>`,
`<fpage>`, `<lpage>`, `<article-id>`, `<journal-title>`) test an exact path on
`element_stack` through `_owned_by` / `_in_own_metadata`, not
`in_front and in_article_meta`. Eight choices in that change look like things
to tidy, and are not.

**The wrapper lists come from the Tag Library, and two of them are not
obvious.** `_YEAR_WRAPPERS` holds `pub-date > string-date` beside `pub-date`,
because JATS 1.3's `<pub-date>` admits `<string-date>` and that admits
`<year>`; `_VOLUME_ISSUE_WRAPPERS` holds `<volume-issue-group>`, which JATS
1.1+ admits in `<article-meta>` for an article published across several
issues. The first cut read neither and passed every test: both are the
article's own values, the ambient gate had read them, and no artifact
measured deposits either. Dropping one looks like removing dead weight; it is
refusing a legal shape. Pinned by
`test_a_year_in_a_publication_dates_string_date_is_read` and
`test_a_volume_and_issue_in_a_volume_issue_group_are_read`. What is *not* a
wrapper is as deliberate: `<related-article>`, `<related-object>` and
`<product>` hold `<article-title>`, `<year>`, `<volume>`, `<issue>`, `<fpage>`
and `<lpage>` too (not `<article-id>` or `<journal-title>`), and they are the
other works — as is a citation in abstract or author-note prose, mixed or
element. `test_another_works_fields_do_not_overwrite_the_articles` and
`test_another_works_fields_do_not_fill_what_the_article_left_blank` deposit
all five containers; with only the three the issues reproduce, an exclusion list naming
them in place of the title's owner test passed the whole suite.

**A bare `<article-title>` or `<year>` directly in `<article-meta>` is read.**
Tightening to wrapper-only (`title-group`, `pub-date`) looks stricter and costs
nothing measurable — no artifact holds the bare shape — but the rule the fix
exists to enforce is *"not another work's value"*, and a bare child of the
article's own `<article-meta>` has no other owner; every element that belongs
to another work sits one level deeper, inside it. The shared
`tests/fixtures/sample_article.xml` deposits its title bare, so the whole
retrieval chain's tests lean on it (8 tests across the parser and service
files failed under a wrapper-only rule, before the explicit test existed). The
same helper is what `<journal-title>` needs for a real spelling: NLM 2.x
deposits it bare in `<journal-meta>` — 2,309 of 3,028 articles in
`oa_comm_xml.PMC000xxxxxx`, 16,771 of 27,515 in `PMC001xxxxxx`, and 107 of 112
in the early served bundle `PMC100320_PMC107849`. Pinned by
`test_a_title_and_year_deposited_without_their_wrapper_are_still_read` and
`test_the_journal_title_is_read_in_either_spelling`.

**No other dated element stands in for a missing `<pub-date>` year.** First
writer under the ambient gate let the first dated element anywhere in
`<article-meta>` become the year where no `<pub-date>` preceded it: a received
or accepted `<history>` date, a `<pub-history>` event's, or another work's. None
is the publication year, and a blank is this module's preference over a wrong
value. No value moves: every article in the four artifacts measured carries a
`<pub-date>` year. Pinned by
`test_no_other_date_stands_in_for_a_missing_publication_date`, one case per
shape.

**Which `<pub-date>` decides is deliberately unchanged, and open.** First
writer, whatever the `pub-type` — which stores a manuscript submission
(`nihms-submitted`) year differing from the epub-else-ppub year in 35 served
and 249 archive articles. That is where the two disagree: 58 served and 515
archive articles take their year from that date at all, and in the rest it
matches the epub-else-ppub year or there is none to compare. That is a
decision about what `year` means (publication or citation year), filed as
#261; `test_the_first_publication_date_deposited_decides_the_year` pins today's
rule and is to be **reversed**, not deleted, when it is decided. Deleting
`and not self.year` passed the whole suite until that test existed.

**The `<fpage>` arm is last writer, and the year arm's first-writer guard is
not its model.** `pages` carried `and not self.pages` from the ambient gate,
where it kept a later citation's or related article's page off the article's.
The owner path does that job now, which left the guard firing only on a second
`<fpage>` of the article's own — which the `<article-meta>` model does not
admit (`(((fpage, lpage?)?, page-range?) | elocation-id)?`); no article in the
four artifacts deposits two `<fpage>`s, or two `<lpage>`s. On that invalid
shape the guard was worse than nothing: `100-101` then `200-201` stored
`100-101-201`, and two `<fpage>`s then one `<lpage>` stored `100-201`, ranges
neither document states, where last writer stores `200-201` for both. Restoring it
looks like consistency with the year; it is a guard kept past its reason, and
the year's first writer is an open question (#261) rather than a precedent.
Pinned by `test_a_doubled_page_range_stores_a_range_the_document_states`.

**The path is a suffix, not anchored at the root.** A wrapper around `<article>`
changes nothing — NCBI's efetch, `FullTextService`'s tier 1c, serves
`<pmc-articleset><article>`, and a root-anchored rewrite blanked every metadata
field of such a document while passing every test that existed at the time
(`test_a_wrapper_around_the_article_changes_nothing` pins it now). The price is
that a nested `<sub-article>`'s `<front>` matches `front > article-meta` exactly
as the article does. The round's own text never reaches a buffer —
`characters()` is suppressed there — so what the nested-article suppression in
`endElement`, tested before any arm, alone now stops is the round's closes
**blanking** the article's last-writer fields (title, volume, issue, pages,
journal) with empty strings; under the ambient gate the unset flags were a
second, independent protection.
Accepted because that guard is tested before every arm and pinned (four tests
redden without it, `test_a_review_rounds_front_matter_leaves_the_articles_alone`
among them), and a root anchor would refuse a wrapped document to buy back a
protection the guard already gives.

**`<article-id>` has no disjunction left, chosen without a population.** Issue
#152 asked which half of `parent == "article-meta" or self.in_front` the module
meant and wanted a draw first. The draw is empty both ways — every
`<article-id>` outside a nested article, on every artifact, sits directly in
`<front><article-meta>` — so the rule is chosen for agreeing with its
neighbours: one owner path for the whole family. The `in_front` half was not
only reachable by invalid markup, as #152 supposed: JATS 1.3 admits
`<article-id>` in a `<pub-history><event>`, where it identifies another
version (a preprint's DOI), and a typed DOI there would have replaced the
article's — as would a PMID, while a PMC ID there would have filled one the
article left blank. Pinned by
`test_a_publication_history_events_identifier_is_not_this_articles` (valid,
one case per identifier type),
`test_an_identifier_elsewhere_in_front_is_not_this_articles` and
`test_metadata_outside_front_supplies_nothing` (both DTD-invalid).

**A related article's title is refused, not modelled.** A `<related-article>`
names the work an editorial, correction, retraction or commentary is about —
and, in the back-files, a research article's companion — and a
`related_articles` field would carry real information. Nothing asks for it,
and the defect was a wrong value in `title`, which refusal fixes completely.
Add the field when a consumer needs it; do not read the refusal as a loss
nobody noticed.

## fulltext — an `<elocation-id>` is a locator of its own, printed only where there is no page range (#265)

`JATSArticle.elocation_id` and `JATSReferenceInfo.elocation_id` hold the
electronic locator JATS deposits in place of a page range. Seven choices look
like things to tidy, and are not. The first cut shipped with three defects that
review found (the lone-locator fallback, the nested related work, the
non-adjacent join), which is why several of these are about what *not* to
store.

**It is not folded into `pages` or `first_page`.** Folding looks simpler, and
it would make every consumer's locator non-empty at once. But `pages` is what a
downstream reads and formats *as a page range* — splits on the hyphen, prints
`pp.` in front of — and `e0123456` is not one, so folding trades a blank for a
wrong value in the field a citation formatter keys on. A caller wanting one
locator reads `pages or elocation_id`, which is what the renderers do.

**A page range wins wherever both are present, and the `<elocation-id>` is
kept rather than dropped.** `<article-meta>` admits one or the other
(`(((fpage, lpage?)?, page-range?) | elocation-id)?`) and no article in the
four artifacts #265 measured deposits both, so for the article this is a
direction. A citation may deposit both — 92 references in the served artifact,
340 in the archive — and there **neither element is reliably the locator**. By a
shape test the `<elocation-id>` is the `<fpage>`'s own value (33 / 41) or a DOI
or PII (43 / 112, a floor: the regex misses `0.1016/…` and `https://doi.org/…`
spellings). The other 16 / 187 are a mix, read by hand, that includes OUP and
SAGE item ids, the issue number beside a range (`24` beside `1883-90`), a
supplement suffix (`e8` after `2188-2201`), a PLOS id beside a PDF page count
(`e0114219` beside `1-19`), a locator split between the two elements (`e00162` +
`20`), junk in either element (`In press`, `et al`), and the true article number
beside an issue deposited as `<fpage>` (`9:10` beside `1109`). Preferring the
`<elocation-id>` repairs that last shape and breaks the DOI and PII rows.
Printing the range is also what was printed before the field existed, so **no
reference depositing both changes its rendering** — in the diff, the references
moving are exactly those carrying an `<elocation-id>` and no `<fpage>`. Pinned
by `test_a_page_range_is_printed_ahead_of_an_elocation_id` (the model),
`test_a_references_page_range_is_rendered_ahead_of_its_elocation_id` (the
reference list) and `test_a_page_range_is_rendered_ahead_of_an_elocation_id`
(the journal line).

**A locator alone does not displace the deposited citation.** Both renderers
print `citation` when no structured component is populated, and the first cut
counted the new locator as one — so a `<mixed-citation>` whose one tagged child
is an `<elocation-id>` rendered that child alone. In `PMC12019704` (2 archive
references, 0 served) the depositor put a *title* there, and the access date
and URL left the cached HTML. Where the locator is the only structured
component and `citation` is not empty, both renderers print `citation`
(`JATSReferenceInfo._carries_only_an_elocation_id`, one rule for both); an
`<element-citation>` leaves `citation` empty, and there the locator is all
there is. The same displacement is **pre-existing** for every *other* lone
component — an author list or a bare `(2019)` printed instead of the whole
deposited string — and is filed as #268 rather than widened here, since widening it
moves stored HTML on `main` for a population this change does not otherwise
touch. Pinned by `test_a_lone_elocation_id_does_not_displace_the_deposited_citation`,
`test_a_lone_elocation_id_defers_to_the_deposited_citation` and, one case per
field of the rule, `test_any_other_component_keeps_the_structured_rendering`.

**A reference's own `<elocation-id>` is a direct child of its citation
element.** The reference arm was first gated on `in_ref_citation` alone, which
is ambient: JATS 1.3 admits `<related-object>` and `<related-article>` inside
both citation elements, and their locator became the reference's. Every
reference's own `<elocation-id>` in both artifacts (8,549 served, 406,553
archive) is a direct child, so the parent test is exact on the data; nested ones
measure 0, so it pins a direction. The `<fpage>` and `<volume>` arms share the
ambient gate and are not changed here. Pinned by
`test_a_related_works_locator_inside_a_citation_is_not_the_references`.

**Several `<elocation-id>`s in one citation are joined only when each continues
the last; a repeat of the whole is skipped; the article's own is last writer.**
6 of the 406,553 archive references carrying one deposit more than one: five
split one locator across adjacent elements with nothing between them (`e8` `1`
`72` `1` for `e81721`, `e2016276` `118` for `e2016276118`, in two articles),
which `citation` prints as one word, and one repeats it (`i5239` twice). Last
writer stored `1` and first writer `e8`. So a part is appended where the
citation's text, whitespace aside, ends with the locator so far and that part —
which needs `<elocation-id>` to merge its text into the citation's buffer (the
inline membership below) — and otherwise the first part is kept, as a `<ref>`'s
first citation part is (#149): a second locator printed apart, an erratum's, is
0 in the archive and would otherwise weld into `e1e2`. The repeat skip cannot
tell a duplicate from a locator split into equal halves (`1` `1` for `11`
stores `1`); none of the six is that shape. The rule is about *citations*
because that is where the shape is: the article's arm keeps the `<fpage>` arm's
last writer rather than concatenating two values no measured article shows
adjacent. Pinned by
`test_several_elocation_ids_in_one_citation_are_one_locator` (four shapes),
`test_a_second_locator_the_citation_prints_apart_is_not_joined` and
`test_the_articles_last_elocation_id_is_kept`.

**`<elocation-id>` is inline, not merely accumulating.** Its arm must read its
own text (`TestOnlyAnAccumulatingElementReadsTheBuffer`), so it joins
`_TEXT_ACCUMULATING`; a member that is not inline takes its text *out* of the
buffer it used to land in. The first cut argued that away by measurement — on
`main`, over both named artifacts and `PMC000xxxxxx`, every `<elocation-id>` sat
in `<article-meta>`, a citation, or a nested article, none in bare prose or in a
`<related-article>`, `<product>` or `<related-object>` — but the loss was real
for valid markup the draw did not contain: a `<related-article>` in a `<p>` or
in an `<article-title>` dropped its locator from the sentence. Inline makes it
structural: the text lands exactly where it did before the arm existed, and the
arm only reads it. Its `<fpage>` and `<volume>` siblings still drop in that
shape, pre-existing. Pinned by
`test_an_elocation_id_in_another_work_in_prose_stays_in_the_prose`.

**A locator with no volume or issue is printed bare.** The journal line
prefixed its locator with `: ` whatever preceded it, so a page-range article
with no `<volume>` or `<issue>` read `<em>J</em> : 100-101 (2024)` — 158 of the
8,118 served articles on `main`. Fixed with this change rather than filed, since
storing the `<elocation-id>` would have spread the shape to 10 served and 1,005
archive articles; bare is what `formatted_citation` already gives a reference.
It moves those 158 journal lines, which is the whole of this change's movement
outside the locator itself. Pinned by
`test_a_locator_with_no_volume_or_issue_is_printed_bare`, whose issue-only case
keeps the separator.

## fulltext — front-matter prose routes into `body_sections`, ahead of the body, with no special case (#230, #234)

**Do not move front matter after the body, into a field of its own, or back
out of the article.** `_unsectioned_prose_is_the_articles` admits `in_front`
under the same `<ref-list>` test as `in_back`, `_append_prose`'s section branch
admits `in_front`, and a third implicit slot flushes ahead of each front
`<sec>` and at `</front>` — so front-matter prose lands **ahead of the body**
in `body_sections`, in document order, and renders just after the abstract. That position was the
maintainer's choice once the numbers were in, over two alternatives priced
against the same measurement: a new `front_matter` field (a public shape every
downstream learns, and it would move the front `<sec>`s already in
`body_sections` out again, so stored values move twice), and a counter with
routing deferred (the content stays lost in 41.3% and 47.7% of articles, and a WARNING on
nearly half of all documents is noise). **#234 is the reason there was no
fourth option**: a `<sec>` in front matter was *already* filed into
`body_sections`, ahead of the body, titled and empty — 263 served and 3,099
archive, every one — so the position was decided before this change and only
its prose was missing. Admitting the wrapper and refusing the content is the
split #234 says must not stand.

**Why route at all.** JAMA deposits *"Funding/Support"* and *"Role of the
Funder/Sponsor"* as bare `<author-notes><p>`, `<fn fn-type="COI-statement">`
sits in `<author-notes>`, and PLOS puts data availability in
`<front><notes>` — the material #224 routes when it sits in `<back>`, so
identical markup meant two things by position. Measured at the drop with the
parser's own predicates, every run fingerprinted against every destination (0
mismatches) and a `<p>` in a table cell excluded: 9,328 runs in 3,350 of the
8,118 served articles of `PMC10030002_PMC10040000.xml.gz` (41.3%), and 114,519
in 46,737 of the 97,909 of `oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26`
(47.7%); `<author-notes>` 6,280 / 81,810, of which 9,865 archive runs are
`COI-statement` fns in 9,645 articles.

**Do not filter the editorial boilerplate.** `fn-type="edited-by"` (*"Edited
by: …"*, *"Reviewed by: …"*) alone is 2,443 of the 6,280 served
`<author-notes>` runs and 41,431 of the 81,810 archive ones, and *"This article
was submitted to …"* sits beside it under `fn-type="other"`, uncounted. It is
routed all the same: `fn-type` is an
attribute vocabulary, and this module has declined to decide by one everywhere
else (`article-type` for nested articles, #110). A filter would also need a
list someone maintains, and an unlisted value would decide silently.

**Do not special-case `<trans-abstract>`.** It routes like any other front
matter (its sections were among #234's empty headings). It is not a duplicate
to refuse — sometimes it is the English version of a non-English abstract and
the only abstract a reader can use — and it stays out of `abstract_sections`,
where nothing marks which language an entry is in. Pinned by
`test_a_translated_abstract_is_routed_like_other_front_matter`.

**`has_body` is untouched**, in both branches: `body_paragraph_count` counts
`<body>` alone, so a front-plus-back document is still held back by
`FullTextService`. The sectioned branch counts independently of the
unsectioned one, so each has its own guard
(`test_front_matter_alone_is_still_not_a_body`,
`test_a_front_matter_section_alone_is_still_not_a_body`); the second was
written ahead of the mutation sweep, because planning it showed a mutant
widening only the sectioned count would survive without it.

**A slot per container, and the orders agree.** `_append_prose` picks its
slot body, back, front, and `_flush_implicit_section` empties them in the same
order; each pairing is pinned by a nested fixture (`..._inside_a_front_...`),
since only a nesting sets two flags at once. `_unsectioned_prose_is_the_articles`
answers `in_body` first and then applies one rule to `<back>` and `<front>`
alike, so its order between those two is equivalent — it was not, briefly: a
first cut answered `in_front` with no `<ref-list>` test, a mutant asking front
first survived, and the fixture written for it
(`test_a_back_inside_a_front_keeps_its_reference_list_refusal`) pinned an order
the claims review then made moot by finding that `<front>` admits a
`<ref-list>` (below). `implicit_front_section` is in `_ROUTING_FLAGS`, and a missing
`</front>` flush is stranded and reported rather than laundered into `<body>`.

**`in_body` in `_prose_reaches_output`'s section conjunction is an equivalent
mutant by construction, and is kept; `in_back` and `in_front` decide there and
are pinned.** The predicate's final line, `_unsectioned_prose_is_the_articles`,
answers `in_body` whether or not a section is open, so dropping it from the
conjunction changes no answer. It refuses a `<ref-list>`'s prose in `<back>`
and in `<front>`, where a `<ref-list>` under a `<sec>` keeps its apparatus — so
without either flag a formula filed into that section is reported dropped, and
an `<attrib>` or a definition term there is lost outright. `in_back` was
pre-existing and unpinned until this change's control mutant survived.
**`in_front` was recorded here, and in six other places, as equivalent** — true
when it was measured, false from the commit that put `<front>` under the
`<ref-list>` rule, and caught by PR #256's review with the mutant passing every
test in the module. Both flags are now pinned, per container, by
`test_a_formula_under_a_sectioned_reference_list_is_not_reported_dropped` and
`test_prose_under_a_sectioned_reference_list_is_filed_whole`. `in_body` stays so
the predicate reads branch for branch against `_append_prose`. **An equivalence
claim is a claim about the code around the flag**, so a later commit to that
code re-opens it.

**The object-metadata refusal is now load-bearing.** All 19 archive `<p>`
inside a `<permissions>` sit in `<article-meta>`, where they fell past every
branch whatever the refusal said; routed front matter would file each
article's licence among its front-matter paragraphs without it
(`test_a_front_matter_licence_paragraph_is_still_declined`).

**The `<ref-list>` refusal applies in `<front>` too — do not narrow it back
to `<back>`.** `<front>` admits `<notes>` and `<notes>` admits a `<ref-list>`,
so the bibliography apparatus #224 refuses can arrive in front matter. The
first cut said JATS admits none there and filed *"Faculty Opinions
Recommendation"* as article prose; the claims review refuted the premise. No
artifact deposits one — diffed against the commit before, the refusal moves 0
of 8,118 served and 0 of 97,909 archive articles — so it pins a direction
(`test_a_front_matter_reference_list_keeps_its_apparatus_out`).

**A paragraph the publisher deposits twice is rendered twice — do not
deduplicate it.** Springer deposits *"Open Access funding enabled and
organized by …"* in `<funding-group><open-access><p>` and again in a back
`<notes>`, so routing front matter makes 108 served and 2,984 archive articles
carry some paragraph twice in `body_sections` (2,844 of the archive ones that
line; the rest a sentence like *"These authors contributed equally"* deposited
in two places). A text-keyed dedupe would decide which deposit is the
article's, and would also drop a sentence legitimately repeated; neither is
this module's call to make silently. Found by the correctness review; measured
by diffing against `main`, a paragraph counted where it occurs more often on the
branch than on `main`. That count is of `body_sections` alone, so it misses the
same shape against the abstract, which PR #256's review found: in **16 archive
articles (0 served)** a paragraph inserted into `body_sections` equals a
paragraph of the article's own `abstract_sections` — a short abstract's
*"Linked article: …"* or *"This article is a Commentary on …"* (Wiley), or a
bare trial registration number, repeated in a front `<notes>` or `<fn>` — and
renders twice under the Abstract heading. Same rule, same reason. (Exact
paragraph equality; two review instruments matching on substrings counted 21
and 22.)

**Front matter renders under the Abstract heading, and that is #231's to
change — do not add a front-only separator.** An untitled section gets no
heading (#30), so the front section's paragraphs follow the abstract's under
`<h2>Abstract</h2>` in the cached HTML. An unsectioned `<body>` already did the
same on `main`, so a front-only boundary would render identical untitled
sections two ways; the maintainer chose to settle body, back and front together
under #231. `test_front_matter_renders_under_the_abstract_heading_until_231_decides`
pins the exact markup, since the ordering test beside it passes with or without
a separator.

**`<floats-group>` is not routed here, deliberately.** It sits in none of the
three containers, so non-float content in it still falls past every branch —
30 runs in 9 served articles (28 in 8 a `<boxed-text>`'s, 2 in 1 a
`<table-wrap-group>` caption's) and 925 in 192 archive (894 in 184 and a
`<fig-group>` caption's 31 in 8) — with #234's empty-heading shape after the
body (3 and 116 `<sec>`). Routing it by document
order would put a *"Research in context"* panel after the back matter, which is
a presentation decision of its own: #253. The tests that used `<front>` as
*the* example of prose reaching nothing now use this shape.

**Blast radius, diffed against `main` in one process over both artifacts**:
prose moves in 3,350 served and 46,737 archive articles, **every move an
insertion** (subsequence in all), 9,332 and 114,549 paragraphs gained;
`html_content` moves in exactly those; `abstract_sections`, `figures`,
`tables`, `references`, authors, metadata, `has_body` and non-empty section
titles move in 0; 0 audit ERRORs either side. Reconciled **per article**
against the tally: the diff exceeds it by 4 served and 30 archive (in 1 and 7
articles), every one an empty paragraph — a front `<sec>`'s `<p>` holding only
an author photo, kept by the sectioned branch's `keep_empty` and skipped by the
renderer. `definition_terms_dropped` falls by 1,441 served and 9,445 archive.

## fulltext — a definition's term is folded in, and the counter is not widened to labels (#228)

**Do not model a definition list, and do not widen
`definition_terms_dropped` to a `<label>`.** Both look like the obvious next
step and both were decided against on measurement.

**Why the term joins the paragraph rather than a field.** A `<def-item>`
pairs a `<term>` with a `<def>` whose `<p>` routes as ordinary prose, and this
module models no definition list — exactly as it models no `<list>`, whose
`<list-item>` contributes no text of its own while its `<p>` becomes a
paragraph. Folding the term in as `"mRNA — messenger RNA"` costs no public
field, no `to_dict` change and no renderer branch, and it is the shape #124
proposes for a footnote marker, so one answer serves three containers. A
`definitions` field on `JATSBodySection` would be a new public shape every
downstream has to learn *and* would move the definitions out of `paragraphs`,
so stored values would move twice for one recovery.

**Why the fold is gated on two predicates rather than done unconditionally.**
`_append_prose` has three outcomes, not two: it files the prose, it refuses it
as bibliography apparatus and counts that, or it falls past every branch with
no counter and no line at all. That third case was `<front>`, which was #230 and
where the measured population of an unfilable term lived. Consuming the term
there would spend it on a paragraph nobody ever sees and leave the new counter
reading zero over the one population it exists to size. **Since #230 routed
front matter** the third case is a `<floats-group>`'s `<boxed-text>` (#253),
and the counter's measured population is exactly the `<def-item>` depositing
no `<def>`, per article: 3 served and 23 archive, with 14,174 and 153,226
folded — both halves instrumented again, and closing on the same totals.

**Three counts close on both artifacts.** Fold plus drop equals the terms that
carry a word: **12,733 folded and 1,444 dropped** against 14,186 − 9 empty
served, **143,781 and 9,468** against 153,256 − 7 archive — the post-#124
figures, both halves instrumented on that revision. (Pre-#124 they read 12,667
/ 1,510 and 142,855 / 10,394; post-#230 14,174 / 3 and 153,226 / 23, as the
paragraph above records.) A table of counters owes that,
and it is what caught #224's archive column summing 136 short of its own total.

**#124 moved the split and the partition was re-measured rather than
re-derived — on both artifacts.** An exhibit's footnote is a destination now,
so a `<def-list>` deposited in a `<table-wrap-foot>` is folded where it used to
be dropped: the served row reads **12,733 folded and 1,444 dropped in 120
articles**, summing to the same 14,177, and the archive row **143,781 and
9,468**, summing to the same 153,249. The archive half was left at its
pre-#124 values through PR #237's review, recoverable only by the arithmetic
this paragraph forbids — and that arithmetic would have been **wrong by 14**,
which only re-measuring showed. Both halves are instrumented counts on that revision — the
fold through `_prefix_pending_definition_term`, the drop through
`definition_terms_dropped` — because arithmetic over a known move of 66 is
exactly the derivation this file tells a reader not to trust.

**Where the drops are is measured at the drop, not inferred from the markup** —
a `<front><abstract>`'s definition list is *folded* into the abstract, and a
region walk over `<term>` elements cannot tell that from a loss. Of the 1,510
dropped in 128 of the 8,118 served articles: **1,441 in `<front>`** (#230 —
**those left this counter when #230 routed front matter**, 9,445 of the archive
drops with them),
**66 in a `<body>` float with no `<caption>` open** (the definition dropped as
exhibit furniture — which is #124's container, and **those 66 left this counter
when #124 landed**: an exhibit's footnote is a destination now, so the served
figure is 1,444 in 120 articles and this paragraph records the measurement as
it was taken), and **3 in `<back>` outside a float** — where back-matter prose does route, so the only way to reach the drop
is to deposit no routable prose at all, and 3 is also the number of served
items carrying no `<def>`. **0** were reached by a second `<term>` displacing
the first. Consuming it on the *refusal* is the opposite
choice for the opposite reason: that loss already has a line, and counting the
term as well would report one loss twice, which is precisely what PR #232's
review had to correct for a `<disp-formula>`.

**Why the counter is a `<term>` and not "a label or term".** #228's own
comment asked for the wider one, on the good argument that a `<fn>`'s `<label>`
is the same kind of drop. Measured by owner, an unfiled `<label>` — one whose
owner is not a formula, a `<fig>`, a `<table-wrap>` or a `<ref>` — reaches
**6,225 of 8,118 served articles (76.7%)** and **86,516 of 97,909 archive ones
(88.4%)**, where each of the four counters it would sit beside fires on a
small minority. A WARNING on three articles in four is noise, and noise is
how the ERROR channel was nearly spent one level up. The owners are also **at
least four** questions with four answers — a numbered `<sec>`'s number (19,462
served), a footnote marker (5,891, #124's), an `<aff>`/`<corresp>`
cross-reference marker (25,332, which #145 resolves rather than prints) and a
`<list-item>` bullet (7,351, presentational). Filed with the full table as
#235.

**"At least four" is meant literally, and the row has two scopes.** Those four
sum to 58,036 against the 62,226 measured, leaving ~4,190 — the largest single
remainder a `<supplementary-material>`'s own label — 2,998 served and 42,901
archive, comparable to `<corresp>` — which no issue names. And the `<aff>`/`<corresp>` row is 25,332 where `jats_parser.py`
states 23,077: the same row under a narrower scope, differing by `<corresp>`'s
2,255 exactly. Both figures were correct and neither named its element set,
which read as a contradiction (PR #236's review); both name it now.

**A stack of pending terms, not a slot.** A `<def>` admits a `<def-list>`, so
definition items nest; held as one value the inner term overwrote the outer
and the inner close cleared it, which is #115 one element family over. The
audit carries `open_definition_items` for the same reason it carries every
other stack: it has a **depth**, and `stuck_flags` is a tuple of names built
by truthiness, so seven stranded items would report as one name. It was
written here as *"its cost is a wrong value rather than a missing one"*, which
does not discriminate — `open_captions`, `open_contribs`,
`excess_text_buffers` and `stuck_flags` itself each document a misrouting too
— and a stranded frame in fact costs one of two **opposite** things: carrying
a word it welds that onto the next paragraph of any kind to arrive, carrying
none it masks the enclosing item's term and suppresses its fold for the rest
of the parse. The diagnostic names both, as `open_contribs` names its own.

## fulltext — the fold is scoped to the definition's own prose (#228, PR #236)

**A pending `<term>` may not be spent on an exhibit's caption.** The fold
hands the word to whatever prose `_append_prose` routes next while the
`<def-item>` is open, and that is ambient routing state rather than ownership.
JATS admits a `<fig>` or `<table-wrap>` inside a `<def>` — `%def-model;` reaches
`%block-display.class;` — and its `<caption>` is the first prose to reach
output, so the term landed on `JATSFigureInfo.caption` / `JATSTableInfo.caption`
and the definition went without it. A *wrong* value in a public field that
`to_html` renders and `FullTextService` caches, silent and uncounted, and a
third way out of the fold/drop partition.

`_DefinitionFrame` captures `len(figure_stack) + len(table_stack)` at the open
and the fold is refused where the depth has grown. Three things about that
shape are load-bearing:

- **A depth, not a flag.** A `<def-list>` sitting *inside* a caption is
  legitimate and common — a figure legend defining its own abbreviations — and
  there the exhibit opened *before* the item, so the depth is unchanged and
  the fold proceeds. A boolean "is a float open?" refuses both directions.
- **Derived from the two exhibit stacks, not from `element_stack` names.** It
  is the same pair `in_figure` and `in_table_wrap` derive from, so the guard
  cannot drift from the routing it guards. A name scan is a second spelling of
  the same question, and this module's own history is of two spellings
  disagreeing.
- **The refused term stays pending and is counted at `</def-item>`**, or the
  scope test trades a wrong value for a missing one nobody is told about.

**The population is empty on both artifacts** — 0 of 14,186 served
`<def-item>` and 0 of 153,395 archive ones hold a float inside their `<def>`
(whole-document walks, so the archive denominator is the unscoped 153,395 and
not the 153,256 the parser sees; a zero over the wider set is a zero over the
subset) — so this pins a direction and not a population, the standing the `<term>`
parent test one arm over is given. "No instance" is not "cannot happen", and
what it prevents is silent, permanent and a corruption rather than a blank.

**A `<term>` that reaches no frame is counted too.** One whose parent is not a
`<def-item>` was read and discarded in silence, so the partition closed only
because neither corpus deposits one — 0 of 14,186 served and 0 of 153,395
archive `<term>` have any other parent. That is a property of the draw and not
of the code, and the routing question (which frame owns this word?) is
separate from the accounting question (did bmlib lose a word?), which is
`_report_zero_authors`' own rule: counting is not parsing. `<index-term>` is
the other JATS parent a `<term>` may have, bmlib extracts none, so counting
one is honest rather than over-reporting.

## fulltext — an exhibit's footnotes, and four rules a later session may want to undo (#124)

**The marker is folded into the note's string, not modelled beside it.** A
`JATSFootnote(label, text)` would keep `"a"` machine-readable, and it is
refused: the sibling Swift port's normative spec
(`doc/cross_platform/jats_parsing.md`) specifies `footnotes: list[string]` with
`"a — text"`, this is the shape #228 settled one container over for a
definition's `<term>`, and neither exhibit model has ever carried
`to_dict()`/`from_dict()` — so a fourth public type would be a new serialisation
surface for a value a consumer can recover with one `split`. The separator is
measured on this population and not borrowed: **2 of 16,947** served footnote
paragraphs already contain `" — "`, against **47** with a spaced hyphen and
**4,133** with a colon. Do not "improve" the fold into a type without the
downstream that needs it and a redraw of that table.

**The caption is asked before the footnote, and the order is a rule rather than
a preference.** A `<fig>` or `<table-wrap>` opened inside a footnote ends the
owner walk on its own, so the two destinations overlap only under a
caption-carrying element bmlib does not model — a `<supplementary-material>` or
`<media>` inside an `<fn>`. Footnote-first files that element's legend as the
enclosing table's note: a *wrong* value where the alternative is a blank.
Caption-first keeps `_append_caption_text`'s standing rule unconditional, that
text inside a caption belongs to that caption's owner and to nobody where the
owner is unmodelled. Measured **0 of 8,118 served and 0 of 97,909 archive**, so
it pins a direction and moves nothing stored — and it was a *surviving mutant*
before it was a decision, both orderings passing the whole suite until
`test_an_unmodelled_captions_legend_is_not_the_tables_note` was written for the
overlap.

**`_owning_exhibit_footnote` takes an `including_self` switch because two
different questions ask it.** Prose asks *"are my ancestors a footnote of an
exhibit?"* and takes the strict-ancestor slice, `element_stack.pop()` sitting at
the end of `endElement`. `</fn>` asks *"which exhibit is this footnote's?"*,
where the closing element **is** the container — and a `<fig><fn>` has no other,
so the strict slice answers `None` for exactly the shape a figure deposits and
the unspent marker goes uncounted there. Collapsing the two passes every table
fixture, `<table-wrap-foot>` being an ancestor of the closing `<fn>` either way.

**`<fn-group>` is a member of `_EXHIBIT_FOOTNOTE_CONTAINERS` in its own right,
and it is spec-driven and unexercised — the comment says so rather than
implying a population.** **0 of 8,118 served and 0 of 97,909 archive articles
deposit an `<fn-group>` inside an exhibit at all**, which is the opposite of
what five files said before the draw was taken. It is kept because removing it
is not free: JATS models it `(label?, title?, (fn|p)+)`, so a loose `<p>` may
sit directly in the group, and in a `<fig>` — which has no foot element and may
carry no `<fn>` — nothing else in the walk's path answers. Removing it passes
every fixture that does not deposit that exact shape, which is why one does.
Its own `<label>` is still dropped by the rule #116 set — #235's, noted there
by PR #239's review — and its own `<title>` by #125/#130's, the heading now
counted (#238, the entry below), where this
paragraph first said the residual was *"not worth filing"* on a zero that was
measured for the `<fn-group>` alone: a `<table-wrap-foot>`'s heading is
deposited 7 times in 4 of 97,909 archive articles.

**The nesting rules stand on their argument, not on a draw.** No exhibit opens
inside another's footnote in either artifact, so requiring the container
*before* the exhibit pins a direction. It is kept because what it prevents is
silent and permanent, and because the sibling port shipped the opposite: routed
on a parser-wide footnote depth, the counter stands at the outer table's depth
while an inner `<table-wrap>` is parsed, and the inner table's cell `<p>` is
rendered twice, once in the cell and once below it (bmlibrarian_lite#173). The
sibling port **fixed** #173 before this was written — `inInnermostExhibitFootnote`
is its shipped routing and the depth survives only for the unwind audit — so
read the depth account as what that port shipped once, not as what it does
(PR #237's review).

**A cell ends the owner walk, and that arm is not optional.** JATS admits an
`<fn>` inside a `<td>`, and without it the walk sets `saw_container` on that
`<fn>` and keeps going outward to the `<table-wrap>` — while `characters()`
has already delivered the same text to `append_cell_text`, which is gated on
`in_cell` alone. The note is then rendered twice, in the cell and again in the
footnote block, which is bmlibrarian_lite#173's own symptom reached by a
different route and the exact invariant the `<p>` branch exists to hold. The
module solves the same collision for a formula in a cell by *withholding* the
cell text until one rendition is chosen; a footnote has no such hold, so the
walk refuses and the cell keeps what it always had. Measured **0 of 8,118
served and 0 of 97,909 archive**, so it pins a direction — and it shipped as a
live double-print through a first round of review, caught only by asking what
each child of an `<fn>` does rather than by any fixture.

**A displaced footnote marker is counted, never overwritten.** The `</label>`
arm assigned `pending_footnote_label` directly, so a second `<label>` in one
`<fn>` put the second marker on the first note's prose with nothing counted,
and an **empty** one erased a good marker outright — `""` being the slot's
absent spelling, `</fn>` then had nothing to give back either, so the note
rendered unmarked against a body still reading `12.3a` with no line at any
level. That is the `<term>` arm's own defect one container over, forty lines
down in the same method, whose comment already says why: *"a rule resting on a
remembered content model is the rule this module keeps being caught by"* —
expat validates no content model, and `(label?, …)` makes a second `<label>`
invalid rather than ill-formed. `hold_footnote_label` makes the class the sole
writer of its own slot and returns what it displaced. Measured **0 of 8,118
served and 0 of 97,909 archive** `<fn>` carrying two labels; an empty `<label>`
alone is deposited (3 served, 11 archive) and costs nothing, there being no
marker to displace.

**Do not tell a consumer to split a note on the separator.** `" — "` is also
`_DEFINITION_SEPARATOR`, and #228's fold runs first, so a `<def-list>` in a
`<table-wrap-foot>` emits `"BMI — body mass index"` with no marker at all:
**68 of the 16,935 notes this parser files, in 10 of the 8,118 served
articles**. The separator was chosen honestly — 2 of 16,947 *deposited*
paragraphs contain it, against 47 with a spaced hyphen and 4,133 with a colon
— but that is a measurement of the deposit and the advice is about the
*emitted* string, which is the population a first cut did not look at. The
em dash stays: changing it would break the one thing that is right about it,
and the fold's own collision is the same string by construction, so no third
separator removes the ambiguity without also removing #228's.

## fulltext — a footnote block's heading and image are counted, not folded (#238)

**The two drops stay.** A `<table-wrap-foot>`'s or exhibit `<fn-group>`'s own
`<title>` is refused by the `<title>` owner rule (#125, #130) — bmlib models no
container that carries a heading, and that rule is what stops an `<fn-group>`'s
title renaming a section — and a `<graphic>` owned by the footnote matter is
refused by `_graphic_owner`'s opacity (#127), which keeps a nested supplement's
image off the figure enclosing it. Each is counted and reported once per
article at WARNING (`footnote_headings_dropped`, `footnote_graphics_dropped`),
which is `refused_apparatus_prose`'s rule for a loss the module chose. The
issue's own suggested resolution.

**Folding the heading in is refused, on shape and not on population.** #124
folds a marker into its note and #228 a term into its definition, each into the
one string it belongs to. A heading belongs to the *block*, and
`footnotes: list[str]` is a list of notes: folded as its own entry, *"Note:"*
is indistinguishable from an unmarked note; welded onto the first note it
reads `"Note: a — A note."` and the `split` the #124 decision promises hands
back `"Note: a"` as the marker. Both are a *wrong* value where the alternative
is a blank, the preference #116 and #162 settled. A `footnotes_title` field
would be a new public shape for a value measured **0 of 8,118 served** and 7 in
4 of 97,909 archive articles; reopen it with the downstream that reads it.

**The image counter is scoped to the footnote matter's own `<graphic>`, not to
every one inside it.** The deposit survey read 329 footnote-matter images in
the archive: 319 in 70 articles owned by an `<inline-formula>`, which is #175's
population; 3 in 2 owned by a `<boxed-text>`, dropped wherever the box sits;
and 7 in 4 owned by the `<fn>`. An ancestor test pools all three under #238's
name and hands #175 a counter it never asked for; every owner outside the
three sets is #244's residual. The heading counter is keyed on the block's
own `<title>` *and* on the owner walk finding an exhibit, for the matching
reason, and the two guards keep different populations out: the parent a
`<list><title>` inside a note, the same drop as one in body prose; the walk
every `<fn-group>` heading belonging to no exhibit, of which an unsectioned
`<back>`'s is a container's (#231) and a sectioned one is #125's own residual
(#240). Four documents attributed the second exclusion to the parent test,
which cannot make it — `fn-group` *is* in the set — until PR #239's review.
Every exclusion is pinned; the mutants that widen any die to exactly one
fixture each.

**The image counter counts deposits, and an empty deposit costs nothing on
either.** An `<alternatives>` pair is one image in two encodings, transparent
to the owner walk, and reaches the arm twice, so the counter reads 2 — the
unit the deposit survey counts and the unit every `<graphic>` figure here is
in — and the line names it, *graphic deposit(s)*, rather than claiming two
images are missing. Counting per group would need handler state (which
`<alternatives>` has already counted), for a number that reaches a log line
and no stored value, and would put the counter and the survey in different
units. Re-tallied after the guards landed: none of the archive's 7 deposits
sits in an `<alternatives>` and every one carries an href, so the two
readings agree on this draw and the unit is chosen on shape. An empty `<title/>` or an href-less `<graphic/>` is not
counted: nothing was read, so the line would state a loss that did not
happen — `offer_graphic`'s rule for the same deposit one branch up and
`hold_footnote_label`'s for an empty marker. The first cut counted both (PR
#239's review); none of the archive's 7 headings is empty, so the tally did
not move.

**Two counters rather than one**, because the two losses call for different
actions, each may be answered separately later, and a shared counter left
reading only the other half would be `_COUNTER_DEFINITIONS_VERSION`'s scar —
a published figure that changes meaning without changing.

## fulltext — a cell's text is isolated at the buffer, not held in `characters()` (#243, #245)

**Do not replace the `td`/`th` membership of `_TEXT_ACCUMULATING` with a test
in `characters()`.** That is the issue's own suggested resolution, mirroring
the formula hold three lines up, and it is the narrower fix for a rule that has
four routes. Raw character data and an inline run merging back (`<italic>`,
`<sup>`) are reached by it. An `<xref>` is not: it *replaces* its text with a
link built from the popped buffer, and the arm's own `text or "Figure"`
fallback then fires, so emptying that buffer leaves `'[Figure](#f1)'` in the
paragraph — an **invented** label where a real one stood, which is #162's own
symptom and worse than the blank it would replace. Nor is a formula: its arm
appends the one rendition it chose through its own `_append_text`, which
`characters()` never sees. Enumerating the arms that merge is the list #116
established cannot be completed by inspection, so the argument is about the
fifth route nobody has found and not about these four; the membership answers
an arm added later as well.
`test_a_cross_reference_in_a_cell_does_not_reach_the_paragraph` is the fixture
that separates the two remedies — of the new fixtures it is the only one whose
assertions differ under the rejected hold.

**The two members accumulate so that their children have somewhere to merge,
and the buffer is then discarded.** A cell fills
`_TableBuilder.current_cell_text` from `characters()` directly, so its buffer
carries nothing any arm wants: it exists to be what every child that *does*
merge back merges into, and `</td>` pops it and drops it. One arm consults it,
and only for emptiness — `</td>` tests it to decide whether an unmodelled cell
lost anything (#245). Its *content* is read nowhere, which is the claim that
matters and the one an earlier draft of this entry overstated as "no arm reads
it".

Accumulating in order to discard is not by itself unusual here: `<sec>`,
`<abstract>`, `<caption>`, `<def>`, `<list-item>`, `<person-group>`,
`<element-citation>`, `<alt-title>` and `<kwd>` all take a buffer no arm
consumes, two of them documented as such in this very module. What is
particular to a cell is *why* — its children route to a builder rather than to
the article.

**The membership needs one exclusion of its own, and it is explicit rather
than inherited.** `td`/`th` are not in `_INLINE_ELEMENTS`, so
`_inside_mixed_citation()` was the single remaining path by which a cell's
buffer could merge, and that helper is a bare ancestor test by design (#146).
Left to it, the drop would have been guaranteed by the absence of an `<array>`
or `<table-wrap>` under a `<mixed-citation>` rather than by the code: where one
appeared, the cell's text would reach `JATSReferenceInfo.citation` **and** the
cell — #243's own splice in a public field, plus the doubled rendition
`_FORMULA_PARTS` exists to prevent — and for the unmodelled half
`cell_text_dropped` would report content missing from an article that was
sitting in a public list. So the pop carries `not is_cell` beside the terms
`_FORMULA_PARTS` and `_UNDIVIDED_NAME_ELEMENTS` already earn. Measured 0 cells
under a `<mixed-citation>` over both named artifacts, so it pins a direction.

**The paragraph reads `'Beforeafter.'` — of the cells — and neither the
missing space nor what else an exhibit holds is a defect here.** A block
merged into a sentence welds without a space either side; that is
`_pad_as_deposited`'s known gap and issue #147's open question, identical for a
`<disp-formula>` deposited the same way. Fixing it in this arm would answer it
for one of the two shapes and leave the module with two spacings for one rule.
And the sentence is clean of cells, not of exhibit internals: an `<alt-text>`,
`<attrib>`, `<long-desc>`, `<object-id>`, `<copyright-statement>` or
`<copyright-year>` accumulated nowhere and still welded in, measured at 537 of
8,118 served articles and filed as #248 beside #241 — answered since, see the
next entry. Do not read the `'Beforeafter.'` claim wider than cells.

**An `<array>`'s cell text is dropped, not routed back to the prose** (#245).
The tempting "fix" is to keep the old splice where no `_TableBuilder` is open,
since nothing else carries that content. It is wrong for this module's
standing reason: the splice is a run-together string the publisher never wrote
— `'…outcomes.CharacteristicValueAge*(year)73.3(7.05)…'` — and a blank beats a
wrong value (#116, #162, and #224's `<ref-list>` refusal). The drop is counted
instead (`cell_text_dropped`), and modelling `<array>` is filed as its own
question. Measured: 355 cells in 8 of 8,118 served articles, 173 of them in
3 articles where the splice was visible at all and the other 182 in 5 where
the text was already being discarded.

**The counter is keyed on no builder being open, which is narrower than "no
table received this cell", and that scope is deliberate here rather than
overlooked.** An `<array>` deposited *inside* an open `<table-wrap>` routes
into that builder, splicing a phantom row into a table the publisher never
wrote that way and taking the silent branch. It is pre-existing, measures 0 of
8,118 served and 0 of 97,909 archive articles, and is filed as #247 rather
than fixed alongside — so "every one is an `<array>`'s" describes what the arm
has seen and not where an `<array>` may sit.

## fulltext — an object's metadata is declined, an attribution is routed (#241, #248)

**Do not "complete" the fix by discarding `<attrib>` with the other four.**
That is both issues' own suggested resolution, and it is right for
`<alt-text>`, `<long-desc>`, `<object-id>` and `<permissions>`, whose text
nobody typesets as a sentence of the article. It is wrong for `<attrib>`,
which is printed: an interview quote's `"(P2, CP)"`, a figure's `"Source:
Authors' elaboration."`, a table's abbreviation list. And `main` was already
losing most of it in silence — a quote or exhibit standing in a `<sec>` put its
attribution in the section's unread buffer — so discarding the rest would have
made a partial silent loss total: of the archive artifact's 5,266 quote
attributions 3,844 (in 217 articles) were lost that way and 2 more with no
buffer open, against 1,331 (in 94) welded into a sentence and 89 in cells.
Routing was chosen over
discard-and-count once those numbers were in. **Count every text-bearing
element when sizing this**: a first cut counted only attributions with text of
their own, missed those whose text sits wholly in an `<italic>` or `<xref>`, and
put the population at 5,072.

**An `<attrib>` routes as a `<p>`, once what it credits and where it stands
have been asked.** Through `_append_prose` a quote's attribution is the
paragraph after the quote and one in a `<table-wrap-foot>` a table note via
#124's owner walk. Ahead of that, in order: under a `<mixed-citation>` or an
`<xref>` it is that element's text (merged at the pop, so routing it again
stored it twice or, under an `<xref>`, left the label empty for `"Figure"` to
be invented); an exhibit's own attribution is that exhibit's note (below);
under declined metadata it is declined; and in a table cell it is the cell's
text — `characters()` holds it already, and for an `<array>`'s cell it goes back
to the buffer so #245's `cell_text_dropped` counts that cell rather than a
paragraph being filed out of it. **The cell walk ends at a `<table-wrap>` and
not at a `<fig>`**, because `characters()` offers text to the innermost open
*table*: a figure in a cell leaves its contents in the cell, while a table in a
cell takes them. Ending at both counted an attribution sitting in the cell as
lost (found by mutation). An attribution that reaches nothing after all that —
one owned by an unmodelled element inside a float, say — is counted
(`attributions_dropped`, WARNING), where the `<p>` beside it is not:
`formulas_dropped`'s precedent for content routed for the first time. An
exhibit's attribution — or its image's, the parent being walked past a
`<graphic>` as `_graphic_owner` walks — would reach neither destination that
method offers inside a float, so the arm files it among that exhibit's
`footnotes`, which render below it. The parent and not the ambient
`current_figure`: inside a `<fig>` every descendant sees a figure open, and a
nested `<table-wrap>`'s attribution would become the figure's note
(`test_an_attribution_in_a_table_nested_in_a_figure_is_the_tables`; a
`<table-wrap>` directly in a `<fig>` is deposited once in the served artifact).
`footnotes`, not `caption`, because an attribution sits below the exhibit where
its notes are printed, and the caption is what a direct-child `<caption>`
deposits (#123). Beside `keep_empty=False`, **it spends no pending footnote
marker or definition term**, because an image credit inside a note's `<p>`
closes before that `<p>` and took the marker — `['a — Credit: X.', 'Adjusted
for age.']`, the body's `12.3a` pointing at the credit. **Except where the
credit is the whole of the note**: then `</fn>` folds the marker into the first
credit (`_FootnoteHolder.unmarked_credit_slot`), which is what `main` stored;
giving it back left an unmarked note and a WARNING saying no prose was filed.
Every shape in this paragraph and the one above was found by PR review or
mutation at 0 measured population. **Do not "tidy" an image credit inside a
caption's or an abstract's `<p>` into place**: routed, it lands ahead of the
paragraph and reorders the one string (`'G src Cap end.'`), merged in place it
welds mid-sentence, and a nested `<p>` has always done the first — issue #252
is the choice, and it should serve both. The accounting closes: of the archive's 6,343 `<attrib>`, 5,378
become paragraphs, 677 figure notes, 192 table notes, 89 stay in their cell and
7 are empty. Figure and table notes equal the diff's insertions to the unit on
both artifacts, and so do paragraphs on the served one; on the archive the
prose tally exceeds inserted paragraphs by 20, every one traced (an enclosing
paragraph that already held the attribution alone, or arbitrary pairing of two
empty paragraphs).

**Three routes carry metadata into the article, so there are three guards.**
Membership of `_TEXT_ACCUMULATING` isolates every child that *merges* — #243's
argument for a cell. It cannot stop a child that *routes*: a `<p>` inside
`<license>` (modelled `(p)+` before `<license-p>`) goes through its own arm to
`_append_prose` whatever buffer surrounds it, so that method refuses prose
under this metadata. And a cell is written by `characters()` and by the formula
arm directly, bypassing every buffer, so both go through `_offer_cell_text`.
**The two that looked redundant are the `_append_prose` refusal and the formula
half of `_offer_cell_text`**: both measured 0 outside `<article-meta>` (all 19
archive `<p>` in a `<permissions>` sit there, where the paragraph fell past
every branch anyway; no formula sits in any member). Do not remove them on
that strength — nothing else closes either route — and **the first is a
population since #230 routed front matter**: it is now what keeps those 19
licences out of their articles' prose. The `characters()` half of
the cell guard is not redundant at all: 67 served and 462 archive cells.

**`_prose_reaches_output` mirrors the refusal, and on its own that is an
equivalent mutant — but its protections differ by consumer.** For the
definition fold, the refusal running ahead of the fold is a second, independent
protection: removing the mirror and moving the refusal below the fold together
redden `test_a_definition_term_is_not_spent_on_a_refused_paragraph`. For the
`<disp-formula>` counter the explicit subtraction in the arm is **the**
protection: in a section the mirror is what makes the predicate answer `False`
at all, but in a float or in `<front>` it answers `False` whatever the mirror
says, so only the subtraction stands between a declined formula and a WARNING
claiming a loss. A first cut called the mirror and the subtraction "one
protection, so removing both is equivalent", which was true only of the
sectioned fixture; the in-float fixture now kills that pair. The mirror stays
because a predicate named "would this be filed?" that answers `True` where
nothing is filed is the lie the mirror exists to prevent.

**Declined metadata adds to no counter of its own**, unlike `cell_text_dropped`
or `refused_apparatus_prose`. Those count content the article carried; this is
a text alternative, an identifier and a licence. The `<alt-text>` is measured
almost entirely as placeholders (`"Fig. 1"`, `"Image 1"`, a DOI), and a line
reporting that bmlib declined `"Image 1"` would tell nobody about a loss. **That
is not true of every member, and the decision does not rest on it being so**:
5 of the 7 served `<long-desc>` are genuine descriptions, and 5 served figure
`<permissions>` inside a `<p>` are a stock-photo credit. On `main` those welded
into the sentence the figure interrupts, so declining them replaces a wrong
value with a blank; routing them as `<attrib>` is routed is #251, which needs a
rule telling a per-image credit from 53 served tables' publisher licence line.
(An invalid `<def-list>` inside a `<license>` still reaches #228's term counter,
which counts terms, not metadata.)

**Two ancestors keep the metadata's text, on every route, and under either the
parse is `main`'s.** Under a `<mixed-citation>` it merges, #146 having settled
that every descendant is the citation's text; whether an `<object-id>` there is
printed (arguably) or an `<alt-text>` is (no) is not something a zero
population decides. Under an `<xref>` it merges too: an `<xref>` replaces its
text with a link label and invents `"Figure"` for an empty one, so isolating
the `<alt-text>` of an image that *is* the reference turned `[Figure 1](#f1)`
into `[Figure](#f1)` — an invented label, #162's symptom, found by PR review.
The price is `main`'s weld where an `<xref>` holds text *and* an image
(`[Fig. 1icon](#f1)`). No member lands in either place in the two artifacts.
**"Every route" is the load-bearing half.** The first cut kept the exception at
the buffer pop alone, and a table cell — filled by `_offer_cell_text` with no
buffer between — lost `See Figure 1` to `See` while this entry said `main`'s
parse was unchanged (PR #250's second review). `_inside_declined_metadata`
answers for every route and **walks from the root**, the first claimer or
member met deciding: two independent `any` tests would keep the text of an
`<xref>` *inside* a declined `<alt-text>`.

**A formula's image `<alt-text>` is a third keeper, as a field and not a
merge.** Declining every `<alt-text>` took it out of the formula's buffer, so an
image-only formula whose deposit spells it out rendered `'where is the rate.'`,
and a labelled display one nothing at all, its `(1)` with it.
`_FormulaFrame.alt_text` is read only where no LaTeX renders and the buffer is
empty. Do not
simplify it to merging the `<alt-text>` back under a formula: a MathML formula
carrying an image as well would then print one expression twice
(`'where xalpha is.'`, `main`'s weld), the outcome `_FORMULA_ELEMENTS` exists to
prevent. 0 in both artifacts.

**Not used for `<img alt>` either.** That is #173's decision, and the
measurement posted there cuts against it on the bytes this parser is fed: 2,390
of 2,491 served figure-level `<alt-text>` are placeholders. A `<graphic>`-level
one is 99% descriptive, but only the archive rendition deposits it.

## fulltext — an exhibit with no `<label>` gets no fallback search (#162)

**Do not add a descendant search when an exhibit carries no direct-child
`<label>`.** It looks obviously right — the corpus appears to say 7 exhibits
"carry a label only indirectly" — and it is refuted by 100% of that
population.

The appearance comes from the instrument. `exhibits_with_descendant_label`
counts an exhibit holding **any** `<label>` in its subtree, so
`descendant - direct` is the set a descendant search would *fire* on, never
the set carrying its own label indirectly. Printed as `PREMISE VIOLATED`, that
difference read as seven exhibits losing a label they had.

All seven were fetched from Europe PMC (2026-09-02): `PMC12011025`,
`PMC12111618`, `PMC12115352`, `PMC12149983`, `PMC12154067`, `PMC12159547`,
`PMC12177175`. **That fetch is no longer what this rests on** (#164): the
sampler records `unlabelled_exhibit_label_owners` per row, so the corpus now
holds the owners as well as the counts — **9 `<fn>` and 67 `<list-item>` over
those seven exhibits, in those same seven articles** — and `print_report`
prints them in section 1 instead of pointing a reader at `label_parents`,
which pools every exhibit in the draw and buries them among its 330 `<fn>`
and 225 `<list-item>`. Re-derive the refutation from
`tests/data/jats_exhibits.json` rather than re-fetching for it. Every one is a
`<table-wrap>` carrying **no `<label>` and no `<caption>`**, and every label
below it is a `<table-wrap-foot><fn>` marker
(`*`, `**`, the empty string) or a `<list-item>` bullet inside a cell (`1.`,
`-`, `•`) — the two containers #116 was about, and the two a depth counter
mis-assigns 561 labels from across the same 997 articles. A descendant search
would have corrupted **7 of 7**. Four of the seven are deposited under ids
their publisher reserves for an unnumbered table (`array1`, `array2`,
`utbl0001`), so the missing label is the deposit's intent.

So the parent rule's premise is **neither refuted nor confirmed** by the
committed corpus: deciding it needs a rule for which of an exhibit's
descendant labels *would* have been its own, and that is the rule under test.
It stands on its argument, and the sampler now prints the two populations it
can support instead of the verdict it cannot — but only the half it cannot.
`direct` is a subset of `descendant` by construction, so a zero difference
*does* prove no exhibit carries its label indirectly; that direction is kept,
phrased as what was measured rather than as `PREMISE HOLDS`, because removing
it left the report with no content-level line that changes between draws. The
`<caption>` section has the identical asymmetry, and its equality on the
recent corpus (6,938 / 6,938) is the measured result certifying #123's
premise, not the coincidence an earlier draft called it.

What the counters *do* support is why `to_html()` changed: 121 exhibits of
7,058, in 83 of 997 recent articles, carry no `<label>` of their own, and each was
given an invented `Figure {i + 1}` / `Table {i + 1}` — worse than a blank for
#116's own reason, since the invented number is the *index* and so collides
with a real one. Pinned by
`test_jats_parser.py::TestAnUnlabelledExhibitIsNotGivenANumber` and
`test_jats_exhibit_sampler.py::test_an_exhibit_with_no_label_of_its_own_is_a_measured_population`.

## fulltext — importable on a core install (#64, PR #66)

**CLAUDE.md argues this one in full**, under "Optional dependencies guarded
at the call site". What it omits:

- **Both halves of the fix stay, and the reason is counter-intuitive — read
  the mutation table in
  `docs/superpowers/specs/2026-08-08-fulltext-import-without-httpx-design.md`
  before removing either.** They overlap: once httpx moved into
  `FullTextService.__init__`, restoring the eager re-export gated nothing and
  **no test failed**. The deferral's own contribution is that
  `import bmlib.fulltext` never loads `service`, so no future top-level import
  there can gate the parser, models or segmenter again;
  `test_importing_the_package_does_not_load_the_service` isolates it, written
  *because* mutation testing found nothing else did.
- **`_http_get` had no test at all** until that review — all ~45 tests in the
  file patch it, so replacing its body with `raise AssertionError` left the
  suite green. `TestHttpGet` covers it now.
- **`fulltext = ["httpx>=0.25"]` is httpx only**; `pdf` stays separate (a
  ~20 MB wheel for anyone who only wants JATS).
  `test_the_extra_the_error_message_names_is_a_real_one` reads
  `Provides-Extra` from the installed metadata, so the message and
  `pyproject.toml` cannot drift apart.

## fulltext — the exhausted-chain report (#67, PR #69)

- **The warning belongs outside the `if abstract_only is not None:` branch,
  and that placement is the whole fix.** Inside it, the *more* complete
  failure was the quieter one. Mutation testing confirms it: putting the
  warning back inside the branch — the original bug — fails two tests.
- **Faults and absences are counted apart, and that is the discrimination
  the report exists to make.** `N attempts failed (ConnectError)` is a lost
  network; `N sources had nothing` is an ordinary paywalled paper; a
  `TypeError` among the faults is a bug. A single count could not say this.
- **The first cut still printed the reassuring line during a total outage,
  for two reasons worth remembering.** (1) Both resolvers reported an HTTP
  failure by *returning* `(None, None)` — which is also what an empty result
  set returns — so a 503 incremented nothing. A swallowed exception is not
  the only way a tier goes wrong; anything reporting failure in the same
  shape as absence has this bug. (2) `FullTextError` was raised alike for
  `Unpaywall HTTP 503` and `DOI not found in Unpaywall`. Hence
  `FullTextUnavailableError`, and hence `note_absence()`.
- **The counter says *attempts*, never tiers.** `_try_known_sources` records
  once per fetcher-supplied source, so it is not bounded by the chain's eight
  tiers — "9 tiers raised" was emittable from a run that attempted four.
- **`_download_and_cache_pdf`'s *download* half is deliberately not wired to
  the counter.** All three call sites return immediately after it, so a
  recorded failure could never be reported; threading it would be dead
  plumbing that reads as coverage. A comment at the handler says so, since
  the other eight swallowers were wired. This is not the same as saying the
  failure goes unreported — #68 gave that half its own keyed one-shot
  warning, which is a separate channel from the exhaustion report and reaches
  the operator on a run that otherwise succeeds. See the #68 entries below. Its *cache-write* half was
  split out into `_save_pdf_to_cache`: an unwritable directory is not a
  download failure, and folding the two left a PDF-only corpus with no cache
  warning at all.
- **`_resolve_pmc_id_via_idconv` takes `failures` as *optional*** because of
  its fourteen direct callers in the tests, which have no report to feed it.
  Not "because it swallows its own exceptions": Tier 0 swallows too and takes
  the parameter as required.
- **A successful retrieval emits no *exhaustion* warning** — pinned by two
  controls, one where nothing fails and one where an attempt fails and a
  later tier recovers. The narrow wording is deliberate: a success may still
  warn about an unwritable cache or an unextractable PDF.
- **A 404 is an absence from an *article* endpoint and a fault from a
  *search* endpoint.** Europe PMC answers "no such paper" with HTTP 200 and
  an empty list, so a 404 on the search path means the API moved; on an
  article path it means the paper is not there, which for a stored fetcher
  URL is ordinary staleness. Three of the four article fetchers called it a
  fault until review caught it.
- **`describe()`'s wording is pinned at its source**, not only through a tier
  chain. It is a documented interface — the manual tells operators to grep
  for it — and asserting it through `fetch_fulltext` left the singular branch
  untested and the counts matched by substring, where `"13 attempts failed"`
  satisfies `"3 attempts failed"`.

## fulltext — the cache is written atomically (#70, #71, PR #74)

**The helper itself now lives in `bmlib/_atomic.py` as `atomic_write()`**,
promoted out of `fulltext/cache.py` by #73 when `templates/` turned out to
need the identical publish. Everything below still holds of it word for word
— that is the point of having promoted it rather than copied it — and every
test named below still lives where it did. What the promotion added is
`tests/test_atomic.py`, which pins the few guarantees that belong to the
helper rather than to either call site: the 38-character temporary-name
overhead the cap below is arithmetic over, and the exception the caller gets
back.

Each of these looks like a line worth simplifying, and each re-opens the bug.
All are pinned by a named test in `test_fulltext_cache.py` /
`test_fulltext_service.py`, and every one was verified by mutation — review of
the first cut found two bullets here whose named test did not exist, and one
whose stated reason was wrong, so the claim is meant literally.

- **The `os.fsync()` before `os.replace` is not durability theatre.** Under
  delayed allocation the `write(2)` that `flush()` issues *returns success* on
  a disk about to fill; the blocks are allocated at writeback and ENOSPC
  reaches userspace only at `fsync`. Without it `os.replace` publishes a file
  whose blocks were never written, which is #70 again one layer down. The
  `flush()` is needed for a separate reason — `os.fsync` acts on the
  descriptor, so anything still in Python's `BufferedWriter` is not covered.
  Removing the two lines fails four tests. Note what those four *can* pin:
  delayed allocation is not observable from userspace, so they assert that
  `os.fsync` is called, not the kernel behaviour making it necessary. A
  refactor reaching the same guarantee via `O_DSYNC` would break them while
  being correct.
- **The temp file's name carries a UUID — but not for the reason first
  given.** Two processes cannot interleave into one temp file: `O_EXCL`
  already stops that. The real hazard is that the loser of the race runs the
  cleanup handler and unlinks the *winner's* in-flight file, whose
  `os.replace` then fails with `FileNotFoundError`, leaving neither writer
  having cached anything. Mutating the UUID to `os.getpid()` survived the
  whole suite *and* ruff until
  `test_two_writers_racing_on_one_article_do_not_destroy_each_other` was
  added.
- **The mode is `0o666` filtered by the umask, not `tempfile.mkstemp`'s
  0600 — and not `0o644` either.** A cache directory shared between users
  otherwise breaks silently: the second user cannot read what the first
  cached, re-fetches everything, and replaces the file with one the first then
  cannot read. `0o666` is what `write_bytes` requests, so the umask does the
  narrowing; the first cut requested `0o644`, which is the same bug a step
  smaller — it drops the group-write bit a umask of 002 grants, which is
  precisely the shared-group case, and it made the pinning test fail under
  that umask while passing under 022.
- **`os.open` adds `O_BINARY` where the platform has it.** Windows only, and
  a no-op elsewhere via `getattr(os, "O_BINARY", 0)`. Without it the CRT opens
  the descriptor in text mode — `os.fdopen(fd, "wb")` cannot undo that, since
  only `io.FileIO`'s path-opening branch sets the flag — and every LF in a
  cached PDF is written as CRLF, which is #70's own failure mode restored on
  one platform. The `PDF_MAGIC` fixture carries LF and CRLF bytes so the
  round-trip assertions are able to see it.
- **The cleanup's `unlink` is itself guarded.** `missing_ok=True` covers only
  `ENOENT`; an unlink failing for any other reason replaces the original
  exception, and that exception is what `FullTextService` interpolates into
  the one warning an operator sees — reporting a full disk as a permissions
  problem.
- **`sanitize_identifier` truncates its readable prefix.** The temp name is 38
  characters longer than the entry's, which lowered the effective `NAME_MAX`
  ceiling to ~217 and made a long identifier fail a write a bare `write_text`
  had completed. That per-article fault then tripped the once-per-service
  "nothing is being cached" warning — untrue, and it silences the
  directory-wide fault that warning exists to report. The prefix is only there
  to be read; the hash over the whole raw identifier carries the collision
  guarantee.
- **`save_html`/`save_pdf` raise rather than swallowing.** Both
  `FullTextService` call sites already report a failed cache write (#67), and
  a caller told nothing would believe the article was cached. Both docstrings
  carry a `Raises:` section, because for a direct caller this is a real change
  and not a relocation: under delayed allocation `write_text` *returned a
  path* in exactly the case that now raises.
- **#71's guard is `except Exception`, and narrowing it restores the bug.** A
  decode failure is only the shape #71 was reported in; a cached file the
  process cannot read raises `OSError` instead. Mutation testing found the
  first cut *survived* narrowing to `UnicodeDecodeError` — the extra test
  (`test_an_entry_that_fails_for_any_other_reason_falls_through_too`, which
  puts a directory where the file should be, so it raises for root too) exists
  because of that.
- **The guard reports the exception *type*, not just its message.** The same
  reason `_TierFailures` does: a `TypeError` printed under a sentence about an
  unreadable file reads as a bad cache entry rather than the bmlib bug it is,
  and a bare `OSError()` renders as an empty pair of brackets.
- **The unreadable file is not deleted — it is moved aside.** Deleting a
  user's data on a read error is a larger action than the bug asks for, so a
  failed re-fetch must leave the evidence. But leaving it *in place* does not
  work: the first cut justified that on "a successful re-fetch overwrites it",
  which holds only when the chain returns JATS full text. An article served as
  a PDF writes `pdfs/` and never touches `html/`, and since the undecodable
  HTML entry is read *first*, it hides the freshly cached PDF behind it — the
  article then warns and re-downloads on every run, forever. `quarantine()`
  renames it to `.corrupt`: out of the lookup path, still on disk. Only
  entries that actually fail to read are moved, pinned by a negative control.
- **`_remove` handles an entry that is not a regular file.** The corrupt shape
  the #71 test itself constructs is a directory standing where the file should
  be, and both documented ways to clear it failed on it: `delete()` raised and
  `clear()` skipped it silently, while the warning told the operator to go and
  delete that file.
- **That warning is per article, where the *write* warning is once per
  service.** An unwritable directory is a property of the directory; an
  unreadable file is a property of that one file. Pinned with two *different*
  corrupt articles rather than two runs over one, since a run now heals the
  entry it could not read. It is also not counted on #67's exhaustion report:
  the cache is not a retrieval attempt, and the line already says more than
  that report's two buckets could.
- **`_attach_pdf_text` catches everything `get_converter()` can raise, not
  just `ImportError`.** `_check_cache` re-extracts a cached PDF, so it runs
  inside #71's guard: narrowed, a `ValueError` for an unknown backend name (or
  anything a third-party backend's `__init__` raises) escaped this method and
  surfaced two frames up as "could not read the cached full text" — blaming a
  cached PDF that read perfectly, and re-downloading it into the identical
  deterministic fault.
- **#70's fix is prospective, and that is accepted.** It stops a truncated
  entry being *written*; it does not detect one already on disk. A real
  truncation of English-language biomedical HTML almost always lands on an
  ASCII boundary and decodes perfectly, so such an entry is still served as
  `content_kind="fulltext"` with nothing logged. Detecting it needs a length
  or checksum sidecar beside every entry — a cache format change, for a
  window that closes as entries are rewritten. Not done; `clear()` is the
  remedy for a cache written by an older version.

## templates — install_defaults writes atomically (#73)

The same defect as #70, found in a second place, which is what made
`atomic_write` shared rather than copied. Four choices here read as
tidy-ups and are not; all four are pinned by
`test_templates.py::TestInstallingDefaultsIsAtomic`, and six mutations were
run against it, six caught.

Read this section knowing that **bmlib ships no templates of its own** —
there is no `bmlib/templates/defaults/`, and `package-data` is `py.typed`
alone. `default_dir` is always the caller's own prompt directory, which is
what makes the second and fourth bullets matter rather than being theoretical.

- **`if not dest.exists()` was left exactly as it was.** It looks like the
  bug — it is the line that turns one truncated file into a permanently
  truncated file — but it is not what needed fixing, and "repair a template
  that looks wrong" is not implementable: a user edit is the whole point of
  the user directory, so a file differing from the default is the *expected*
  state and cannot be told from a truncated one. Making the write atomic is
  what makes the guard true: a faulted copy publishes nothing, so `dest`
  does not exist and the next call installs it. Reverting the write to
  `write_text` fails both of the original tests.
- **The copy is bytes, not text.** `src.read_text()` applies universal
  newlines and `write_text` translates back through `os.linesep`, so the
  installed template's line endings need not be the source's. **This is not
  Windows-only, and the round trip is not lossless on POSIX** — an earlier
  draft of this entry said it was, and the CRLF fixture in
  `test_a_template_is_copied_byte_for_byte` fails *here*, on Linux CI,
  against a re-encoding implementation. Both platforms corrupt, in opposite
  directions; the round trip preserves bytes only where the source's endings
  already match the platform's, and `default_dir` belongs to the caller, so
  they need not. What the change buys is fidelity of the installed
  **artefact**, for whatever editor or tool the user opens it with. It is
  deliberately *not* a claim about what reaches a model — `_FallbackLoader`
  reads every template with `read_text` too, so Jinja2 sees `\n` either way,
  and "a prompt is sent verbatim" was the wrong reason for a right change.
  An implementation that is atomic but still re-encodes fails that one test
  and nothing else.
- **A dangling symlink at the destination is skipped, not published over.**
  This is the one line the atomic publish *made* necessary rather than
  fixed. `exists()` follows symlinks, so one pointing at a missing target
  reads as absent — and `os.replace` then replaces the link itself, where
  the `write_text` it replaced wrote *through* the link and raised
  `FileNotFoundError`. A user who symlinks a prompt at a volume that is
  unmounted at startup came back, before the guard, to find the link gone
  and the default in its place, announced by an `INFO` line indistinguishable
  from an ordinary first install. Skipping is right rather than "install
  anyway": the link is the user's stated intent about where that prompt
  lives, and this method's whole job is to not overwrite the user. It is
  `WARNING` rather than silent because rendering then falls back to the
  default with the user's own version unreachable, which is exactly the
  substitution the guard exists to make visible. Note that the cache side
  has the same exposure by construction and does *not* guard: `save_pdf` /
  `save_html` overwrite unconditionally, a cache entry is bmlib's own
  storage rather than a user's file, and `clear()` would unlink such a
  symlink regardless.
- **`OSError` propagates, aborting the templates after the one that
  failed.** Collecting the errors and continuing looks kinder and is worse:
  the next call installs whatever is still missing, so the loop is already
  self-repairing, and a caller who cannot write is better told once than
  handed a partial installation and a summary. This is the same call
  `save_html` / `save_pdf` make. Review of the first cut found this bullet
  *unpinned* — both original tests installed a single template, so an
  implementation that rolled every success back on failure, the exact
  opposite of the documented contract, passed them both.
  `test_a_faulted_copy_leaves_the_ones_already_installed_alone` is the
  three-template case that closes it, and it is also why the scan is now
  `sorted()`: which templates are left uninstalled has to be reproducible
  before it can be asserted.

One thing deliberately *not* asserted: the fault is injected at `os.fsync`,
so on the unfixed code the test fails with `DID NOT RAISE` rather than by
finding a truncated file. That is honest rather than convenient — the
unfixed code has no `fsync` to fault, and a test faulting `write` instead
would pass against an implementation that omits the `fsync` and so
publishes a file whose blocks were never written. The directory is asserted
*empty*, not merely free of `scoring.txt`, because a leftover temporary file
is the other way this can go wrong: dropping the cleanup `unlink` fails that
assertion and nothing else in the suite.

The other thing an error injection cannot reach is the **killed process**,
which is half of what the issue names. Injecting at `fsync` only proves the
tidy-up: with nothing to overwrite, a plain `open(dest, "wb")` that unlinks
on failure is indistinguishable from an atomic publish *after the fact*, and
review confirmed by mutation that such an implementation passed every test
in the class. They differ only while the bytes are in flight, which is
precisely what survives `SIGKILL`, so
`test_the_destination_never_exists_until_it_is_complete` asserts on that
instant instead — the target name is absent, and the bytes are staged under
another name, at the moment `os.replace` is called. Without it the house
rule in CLAUDE.md ("a new writer of user-visible files uses this helper") is
unenforced at the very call site it was written for.

## fulltext — the service degrades but the cache still raises (#75)

`FullTextService` survives a cache directory it cannot create;
`FullTextCache(cache_dir=...)` constructed directly still raises. **This
asymmetry is deliberate — do not "make it consistent".** A caller who
constructs a cache asked for one specifically, and returning an object whose
every method then fails one at a time is worse than failing once, clearly, at
construction. Pinned by
`test_fulltext_cache.py::TestADirectlyConstructedCacheStillRaises`, which is
the only thing standing between the decision and a silent tidy-up.

Three further choices, each with a named test and each verified by mutation:

- **The guard catches `RuntimeError` as well as `OSError`.** Not defensive
  padding: `_default_cache_dir()` runs before any `mkdir` and calls
  `Path.home()`, which raises `RuntimeError` where there is no `HOME` and no
  passwd entry. Narrowing to `OSError` fixes the shape #75 was reported in and
  leaves the identical defect one layer up —
  `test_a_home_directory_that_cannot_be_determined_is_survived` fails under
  exactly that mutation.
- **It does not catch `Exception`.** Inside that one constructor
  `RuntimeError` has exactly one *source*, so the pair stays narrow enough
  that a bmlib bug still surfaces as one. Widening a guard catches strictly
  more, so no test that merely uses the cache can fail on it — which is why
  `test_an_unexpected_error_from_the_cache_still_propagates` exists and does
  nothing else: it raises a `ValueError` from the constructor and demands it
  escape. Without it this bullet was prose with nothing behind it.
- **No fallback cache location, and no writability probe.** Relocating to a
  temp directory surprises a caller who set `cache_dir` deliberately, and a
  cache that vanishes on reboot looks like one that never hits; probing would
  be TOCTOU and would litter the operator's cache directory with a file that
  is not an article. Pinned by
  `test_a_file_in_the_way_leaves_a_service_with_no_cache` (a relocating guard
  leaves `service.cache` set) and
  `test_nothing_is_written_where_the_cache_would_have_gone`, which asserts the
  directory holds nothing but the blocking file.

**`test_retrieval_still_works_with_no_cache` asserts the retrieval logs
nothing at `WARNING` or above. That assertion is not log tidiness — it is the
only thing pinning the two `self.cache is not None` guards.** Delete either
one and the retrieval still succeeds: `_check_cache(None, ...)` raises
`AttributeError` into #71's best-effort read handler and `_cache_html` raises
into #67's write handler, so the whole suite stays green and the only symptom
is a pair of WARNINGs blaming the environment for a bmlib bug, per article,
per run — the exact failure those two issues exist to prevent. Measured: with
both guards removed and the assertion absent, 1774 tests pass. Do not relax it
to "no errors" or drop it as noise.

**A read-only cache directory splits between #75 and #67, and the boundary is
not where it looks.** `FullTextCache.__init__` makes *three* `mkdir` calls and
only the first is suppressed by `exist_ok=True`, so a read-only root whose
`pdfs/` and `html/` do not yet exist raises `PermissionError` from the second
— #75's degrade, not #67's warn-once. #67 is reached only when the
subdirectories already exist and the *write* fails: an unwritable subdirectory,
or a full disk. Measured, not reasoned: `mkdir(exist_ok=True)` on a `0o555`
root gives `PermissionError: [Errno 13] … /pdfs`. The earlier version of this
entry claimed the whole read-only case was #67's and used that to justify the
no-probe non-goal; the non-goal stands on the two reasons above without it.

One consequence worth not undoing: **the three *post-check* cache helpers take
the cache as a parameter** rather than reading `self.cache`. Once `self.cache`
became optional their precondition — "the caller checked" — was a comment a
caller could forget; as a parameter the narrowing and the use sit in one
function body, where a type checker can discharge it. Note what that does and
does not claim: nothing in this repo checks it, since CI runs ruff and not
mypy, so unlike `sync._stamp_source()` — which raises `ValueError` at runtime —
the guarantee here is one a downstream's checker gets and a reader can verify
locally. `_cache_html` and `_download_and_cache_pdf` are deliberately *not*
in the set: they are the sites that do the checking, and giving them the same
shape would push one branch out into their seven unconditional call sites.

## fulltext — the free-PDF tier and what it reports (#68, #72, #79, PR #80)

- **The free-PDF allow-list is answerable to the records, not to taste.**
  `_FREE_PDF_AVAILABILITY_CODES` allow-lists `availabilityCode` (`OA`, `F`)
  and consults the `availability` display string *only* for an entry carrying
  no code; a present-but-unknown code is rejected without reading the label,
  because an unknown value must under-credit rather than risk a paywalled
  download. **Run `scripts/sample_free_pdf_urls.py` before changing either
  list** — #79 was precisely a value (`"Open access"`, 95.7% of free-PDF
  entries) that never appeared in what bmlib accepted, and the sampler counts
  the distribution *before* the allow-list filters for that reason: counted
  after it, it could only ever confirm the list. Pinned by
  `test_fulltext_service.py::TestFreePDFAvailability`.
- **Both access fields are type-checked before the membership test, and that
  is not defensive padding.** `x in frozenset` *hashes* `x`, so a JSON object
  where a string was expected raises `TypeError` — a `_BUG_TYPES` member,
  which would report Europe PMC's malformed bytes as a bmlib defect *and*
  spend the one-shot `bug:TypeError` slot a later real defect needs.
  `_extract_free_pdf_url` guards the container one level up for the same
  reason: `.get("fullTextUrl", [])` returns `None`, not `[]`, for a key
  present with a JSON null.
- **`_BUG_TYPES` is a deny-list, and `ValueError`/`SyntaxError` are
  deliberately outside it.** `json.JSONDecodeError` *is* a `ValueError` and
  `xml.etree.ElementTree.ParseError` *is* a `SyntaxError`, so admitting
  either would file an ordinary malformed remote response as a bmlib defect.
  A deny-list because the legitimate failures are varied while the
  always-a-defect set is small. Argued inline at `_BUG_TYPES`.
- **`on_bug` fires at the moment the exception is swallowed, and is a
  mandatory field.** Every exit-based alternative is the defect itself:
  `describe()` is read only on total exhaustion, which is the exit this case
  never reaches, so the next early return would silently re-break it. An
  unwired callback is not a quieter channel but total silence — hence no
  default. `_TierFailures.unreported()` is the deliberate opt-out for direct
  helper calls and tests. Pinned by
  `TestASwallowedBugDoesNotStayAtDebug` and
  `test_a_record_cannot_be_built_without_deciding_about_on_bug`.
- **The one-shot warning keys are built from a bounded `origin` written out
  at each call site, never from `result.source`.** Tier 0's `source` comes
  from the fetcher's `FullTextSourceEntry`, and OpenAlex derives it from the
  location's venue display name: one distinct, remote-data-derived string per
  journal, which turns "reported once" into one warning per article over a
  bulk sync. The source still appears in the message.
- **A `_warn_once` key names the *cause*, not the site.** `"cache-write"` as
  a bare literal let a transient `OSError` early in a run permanently silence
  a genuine `TypeError` inside `save_pdf` — the failure #72 exists to fix —
  and, in the other order, presented a type error to the operator as a full
  disk. Pinned by `test_html_and_pdf_write_failures_share_one_warning` and
  its per-cause companions.
- **`FullTextCache.save_pdf`'s own magic-byte rejection sits at `DEBUG` on
  purpose.** At `WARNING` it emitted a line per article for the dominant
  measured failure — Unpaywall landing pages, 14 of 28 probes — underneath a
  message promising the report was one-shot, defeating the one-shot for the
  very cause the 5% rule selected it for. The article-level detail is still
  there at `DEBUG`; `TestThePerArticleDetailThatTheWarningPromises` pins it.
- **The `WARNING`-level split is a measured rate against a rule fixed
  beforehand**, not a preference: under 5% of attempts, per-article
  `WARNING`; at or above it, one line per `(tier, cause)` plus per-article
  `DEBUG`. Re-deciding it means re-running the sampler, not re-reading the
  code. The *exception* path is one-shot per `(tier, exception type)`
  regardless of the rate, because it fails every article once it starts
  failing.

## fulltext — a PDF's metadata title (#56, PRs #82, #83)

The argument lives in `bmlib/fulltext/_titles.py`, which is unusually heavily
commented for that reason. The entries here are the pointers.

- **`metadata["title"]` stays verbatim; the judged answer is
  `ConversionResult.title`.** Sanitising the one key would make the dict lie
  about `creator` and `producer` beside it, and a caller debugging provenance
  needs the original string, junk and all.
- **The reject-list has exactly one member, and a shape the corpus never
  showed does not become a member however obvious it looks.** That is the
  reject-list this design exists to avoid — not one of the shapes issue #56
  proposed (`.docx`, `"untitled"`, the file stem) appears anywhere in the 235
  measured PDFs. **Run `scripts/sample_pdf_metadata_titles.py` before
  changing it.** `_MIN_TITLE_WORDS` is now kept as defence-in-depth rather
  than as a member the corpus earns — anchored containment rejects the row
  that admitted it — and says so at its definition;
  `TestTheOneBackstopMember` pins both halves.
- **`looks_like_junk` takes the title alone.** It carried the whole metadata
  dict against the day a member wanted `creator` or `producer`, but an
  argument added for a member the corpus never earned is the same species of
  speculation as the reject-list entry. Re-adding it is one line, here and at
  the single call site; what the measurement actually said about `creator` is
  recorded above `_MIN_TITLE_WORDS`, so the next reader does not re-derive it
  and reach the opposite conclusion.
- **Containment is anchored to whole tokens — do not simplify it back to
  `wanted in page`.** `normalise` exists to produce tokens, and a bare
  substring test throws those boundaries away in the *accepting* direction:
  a `/Title` truncated mid-word, which producers emit routinely, matched the
  page it was cut from and then beat the fallback that would have recovered
  the whole line. Pinned by `TestCorroborationIsAnchoredToWholeTokens`.
- **An empty page accepts and an unreadable page rejects.** The asymmetry is
  the distinction the samplers draw between an unmeasured probe and a failed
  one: a page read as carrying no text makes corroboration a test that
  *cannot be run* — rejecting would blank the title of every image-only scan
  — while a page whose extraction *raised* is a test that failed, and a fault
  is where there is least reason to trust what a file claims about itself.
  The backstop applies in both, so an unrunnable check is never a free pass.
- **The empty-normalisation guard is masked twice and still load-bearing
  once.** The backstop rejects a zero-word title before it and anchoring
  would reject one after — but neither covers a title normalising to nothing
  against a page that is *also* empty, which would otherwise hand back
  `"###"` as an image-only scan's title. Argued inline;
  `test_the_empty_normalisation_guard_stands_on_its_own` pins it.
- **`_LINE_NUMBER_RE` rests on its unit tests, not on the corpus.** Four
  independent mutations of it change the answer on zero of the 235 rows, so a
  green corpus run has *not* checked it —
  `TestALineNumberedManuscriptStillCorroborates` has. Both digit bounds are
  deliberate and argued at the pattern.
- **`accepted_metadata_title` returns `str | None` and sends its four
  rejection reasons to the log.** Every caller asks one binary question and
  would discard a richer answer; the one party who wants the reasons is the
  human debugging why a title vanished from one PDF, and `DEBUG` is where
  they get them. `TestARejectionSaysWhy` pins each line, with a control that
  an accepted title logs no rejection.
- **The sampler deliberately does not import `_titles.normalise`, and a
  future refactor must not "deduplicate" the two.** A corpus labelled by the
  rule under test can only ever confirm that rule. For the same reason the
  sampler writes to `*.unreportable.json` when a population trips the
  unmeasured-share threshold: a throttled run must not replace evidence a
  later reader takes as measured.
- **A bioRxiv attempt records its posting *day* rather than the run pinning
  its window.** Pinning would make one date range serve both "what am I
  sampling" and "what do I owe", and those diverge by a day every day.
  `MAX_UNMEASURED_ATTEMPTS` retires an attempt from being *offered* while it
  keeps being *counted*, in `tally_previous` and in the ERROR rule — because
  forgetting it is the silent loss the accounting exists to prevent.

## fulltext — the PDF converter (PR #60)

- **A password-protected PDF is rejected on `doc.needs_pass`, never on
  `doc.is_encrypted`.** An *owner* password restricts permissions without
  blocking reads, so such a file is encrypted and converts perfectly;
  widening the check to `is_encrypted` would reject it. Both guards carry an
  owner-password negative control for exactly that
  (`test_an_owner_password_alone_does_not_block_conversion` /
  `..._extraction`), so neither is a check that cannot fail.
- **`extract_blocks()` keeps its explicit check even though it already
  raised** — it raised only because `get_text()` failed of its own accord,
  and had that stopped, it would have returned `[]`, exactly what an
  image-only scan returns. The general lesson, and the reason #57 existed:
  `except` blocks written to keep one bad page from aborting the rest will
  also absorb a whole-file failure, and the result reports as a success.

## fulltext — PDF section segmenter (PR #55)

- **`TextBlock` is one PDF *line*, not a span, with font attributes from the
  dominant span** (most non-whitespace characters, ties to the first).
  PyMuPDF starts a new span at every font change, so upstream's span-level
  blocks shattered a mixed-font heading into fragments no anchored pattern
  could match. Pinned by `test_a_heading_split_across_spans_is_one_block` and
  `test_a_superscript_marker_does_not_restyle_the_line`.
- **Nothing is dropped for being empty or unclassified**, each with a named
  test: front matter is a 0.5-confidence section (if the real first heading
  was missed, it has swallowed the introduction); a heading with no body is
  reported with `content == ""`; and `SectionType.TITLE`,
  `SegmentedDocument.authors` and `Section.subsections` are reserved, not
  dead (the `outcome_switching_detected` precedent).
- **`extract_blocks()` raises where `convert()` returns a failed result** —
  a partial block list is indistinguishable from a sparse PDF, so degrading
  would be silent, where `converted_pages` says how partial a conversion was.
  Pinned by `test_a_corrupt_pdf_raises_rather_than_degrading`.
- **A negative vertical gap (column/page boundary) inserts no paragraph
  break** — a PDF gives no signal distinguishing a paragraph continuing
  across a page from one ending at it. The `height == 0` degenerate-bbox case
  is acknowledged in `_join_blocks` and left.
- **CONFLICTS owns the disclosure family, in both numbers** — listing the
  singular under FUNDING put the two numbers of one heading in different
  sections, decided by dict iteration order. A comment wards off re-adding it.
- **Two spec-level limits are documented in `docs/manual/fulltext.md` rather
  than fixed:** the 0.7 partial-match pass can fire on a bold figure caption
  ("Fig. 3 Study results" → RESULTS), and `min_heading_size` is an absolute
  floor (10.0) in an otherwise median-relative design, so it can silence the
  segmenter on a 9pt two-column layout. Callers check `Section.confidence`.

## citations (merged, PR #58)

**Argued in full in `docs/manual/citations.md` and
`docs/superpowers/specs/2026-08-06-citations-port-design.md` — read them
before "correcting" anything here.** Upstream's *code* is the output spec,
not its docstrings, where the two disagree. Five upstream-faithful oddities
are kept rather than unified (per-style empty-title rendering, the ambiguous
bare inverted `authors` string, `"\n---"` with no leading blank line,
`"Smithn.d."`, `author_surname("Jan van der Berg") == "Berg"`), each pinned
by a test naming it. Two deliberate departures: `Citation` compares by all
fields, and marker ids stay `int` only. Five upstream defects were fixed,
the fifth from PR #58's review — a whitespace-only author entry crashed every
style with `IndexError`.

## publications — PubMed metadata graft (PR #59)

**CLAUDE.md argues most of this port in full — read it there, and do not
re-derive any of it.** "Replace-per-source child rows" settles the `source`
column and scoped delete, `_stamp_source()`, the `ValueError` on an unnamed
row, the absent UNIQUE constraint, the empty guard and `_consolidate_rows()`;
"Markdown, measured against the markup" settles the mixed-content walker,
strip-once, edge whitespace outside the markers, `Label` **or**
`NlmCategory`, the measured escape set, and `<u>`. Each is pinned by a named
test on both backends, several verified by mutation. Only what it omits:

- **PubMed repeats a `<Grant>` block verbatim** — 31 of 575 entries across 200
  NIH-funded records — so `_parse_grants()` collapses exact repeats, keeping
  first-occurrence order. Two grants differing in any field are two grants.
- **`position` indexes `<AuthorList>`, not `Publication.authors`** — it counts
  the `<CollectiveName>` consortia that `authors` skips, so the two differ in
  length whenever one is present and `authors[a.position]` is the wrong way to
  resolve an affiliation's author (match on `author`). What position is *for*
  — first or senior author — is right either way. Accepted knock-on: a
  consortium stating an affiliation loses it, since recording it would put an
  `author` in the table that is absent from `authors`, breaking the one join
  the column exists for. Pinned by
  `test_position_indexes_the_xml_author_list_not_the_authors_field`.
- **`store_publication()` does not write `publication_id` back onto the
  `Grant` / `AuthorAffiliation` objects it is given**, unlike `pub`, which it
  mutates in place and documents as such (the `FullTextSource` precedent). The
  failure would be silent — the field reads `0`, a plausible id rather than an
  obvious sentinel. Pinned by `test_the_caller_s_objects_are_not_mutated`.
- **`is_retracted` and upstream's `_extract_date` were not ported.**
  `publication_types` already carries "Retracted Publication",
  `retractions.py` answers authoritatively, and upstream reads RefType
  `RetractionOf` (this article *is* the notice) as retracted. `_parse_pubdate`
  is strictly better than `_extract_date`, which defaults a missing month and
  day to `01` — inventing precision — and swallows every failure bare.
- **`~x~` / `^x^` are Pandoc extensions, knowingly.** A renderer without them
  shows the tildes literally; the alternative flattened `CO<sub>2</sub>` and
  `CO<sup>2</sup>` to the same ambiguous `CO2`. Documented in the manual.
- **Which elements get the formatting walker is decided by NLM's DTD.**
  `ArticleTitle`, `AbstractText` and `Affiliation` are declared `(%text;)*` —
  `#PCDATA | b | i | sup | sub | u` — so all three use
  `_text_with_formatting`. `Journal/Title`, `DescriptorName` and
  `PublicationType` are `(#PCDATA)`, genuine leaves, and keep plain `.text`.
  Do not widen or narrow this list by eye; check the DTD.

## publications — a completed day is a durable claim (#88–#91, PR #93)

**CLAUDE.md's "A completed day is a durable claim" argues this in full — read
it there.** It settles the two-rules-of-different-kinds split, why the
shortfall rule is a floor rather than strict inequality, why PubMed counts
delivered elements, why each envelope is checked rather than defaulted, and
why `sync()`'s status handling is an allowlist. Every guard below is pinned by
a named test and was verified by mutation. Only what it omits:

- **A small shortfall completes, and that is not an oversight.** Making
  `reconcile_delivery` strict — any shortfall fails — is the obvious
  "simplification" and it is a real defect: a `failed` day is re-offered by
  `_days_needing_fetch()` on *every* later run, so a gap that is benign and
  permanent re-fetches and re-merges the whole day for the rest of an
  installation's life, growing with the date range. Pinned by
  `test_a_small_shortfall_completes` and its per-fetcher twins.
- **"A completed day is never offered again" is shorthand, not the rule.**
  `_days_needing_fetch()` also re-offers a completed day that was fetched
  before the day was over, and one whose `recheck_days` window has passed.
  Neither weakens the argument — the default is `recheck_days=0`, and a past
  day fetched after it ended is what the reconciliation rules protect — but
  the unqualified form is false and was written into six documents in #88's
  first round. The `today` half of it was its own defect, **#95**, now fixed;
  see the next entry.
- **`SHORTFALL_FAILURE_RATIO = 0.5` is fixed before measurement**, unlike
  every other threshold in bmlib. Do not cite it as measured and do not
  tighten it by taste — **#92** is the sampler that would earn a different
  number. The docstring says so; keep that paragraph honest if the number
  moves. **#105 raised what that unmeasured floor exposes by roughly 24×,
  and did not touch the constant.** Before it, no PubMed day above 9,999
  records was walked at all, so a `completed` PubMed day could lack at most
  4,999 records; now every part of a partitioned day may be up to half short
  without failing, and the day's total is judged against the same floor, so a
  242,216-record day can be `completed` missing some 121,000. The rule is
  unchanged and the population it applies to is not — which is the argument
  for #92 being more urgent than when it was filed, and equally the reason
  #105 did not quietly move the number instead.
- **The floor is exclusive.** Delivering exactly half passes. Pinned by
  `test_exactly_the_floor_completes`, which exists because `<` versus `<=`
  here is a one-character edit no other test notices.
- **`stalled` is not redundant with the floor**, though most reproductions
  trip both. It is the only rule that catches a session expiring on a *late*
  page — 500 of 1,000 clears the floor. Pinned by
  `test_a_session_dying_on_a_late_page_fails` and
  `test_an_empty_page_with_only_a_few_records_outstanding_fails`, written
  after mutation showed the earlier tests survived removing it.
- **bioRxiv's envelope check is deliberately the weakest of the three, and
  the obvious tightening is wrong.** It refuses a body carrying *neither* a
  `collection` key *nor* messages — one making no claim about the day — and
  does **not** require a list `collection`. The first round of #88 wrote
  `data.get("collection", [])` guarded by an `isinstance`, which only fires
  when the key is present and non-list, so `{"error": ...}` and `{}` still
  completed as quiet days: the very bug the guard was added for.
  `isinstance(data.get("collection"), list)` is the tempting fix and is
  **not** safe: bioRxiv is known to report a quiet day by omitting `total`,
  but whether it also omits `collection` is unmeasured, and a wrong
  tightening fails every quiet day on every run for ever. The residual —
  an error body carrying messages and no collection still reads as quiet —
  is irreducible without knowing the `messages[0].status` vocabulary.
  **#94** is the sampler that would measure both and let this be tightened.
  Pinned by `test_a_body_carrying_neither_a_collection_nor_messages_fails`
  and `test_a_quiet_day_completes_whether_or_not_it_sends_a_collection`,
  which asserts *both* possible quiet-day shapes so the guard cannot come to
  depend on the unmeasured answer.
- **An absent count is `None`, never `0`.** `promised=0` is a source saying
  the day is empty, which any delivery satisfies; `promised=None` is a source
  saying nothing. bioRxiv's `records_total or 0` collapsed the two, which
  silently disabled *both* the shortfall and the stalled rules — the stalled
  flag is conditioned on knowing the total — so a first page carrying records
  and no `total` followed by an empty page completed as a whole day. Records
  delivered against `None` now fail; nothing delivered against `None` is the
  quiet day and passes. Pinned by
  `test_records_delivered_without_a_total_cannot_complete` and its negative
  control `test_nothing_delivered_against_no_count_is_a_quiet_day`.
- **Every fetcher must compute `stalled` itself.** It defaults to `False`,
  which is the value that *disables* the strongest rule, and OpenAlex took
  that default through #88's first round — so the one source whose cursor can
  be invalidated mid-walk was judged by the floor alone, and 600 of 1,000
  completed. An empty page also ends the walk, which additionally bounds a
  loop that `while cursor is not None` does not. Pinned by
  `test_a_walk_that_stops_serving_mid_count_fails` and
  `test_an_empty_page_ends_the_walk_rather_than_repeating_it`.
- **PubMed counts delivered records by element name, not by child count.**
  `len(list(root))` counts every child of `<PubmedArticleSet>`, and
  `<DeleteCitation>` is a legal one. Counting it as delivery is wrong twice:
  it inflates the count so a real shortfall clears the floor, and it makes a
  page carrying nothing else fail the `delivered == 0` stall test. The
  book-chapter test cannot catch this — it separates *delivered* from
  *parsed*, which any child-counting expression also satisfies. Pinned by
  `test_a_page_of_delete_citations_is_not_delivery`.
- **A shortfall that completes is returned, not only logged.** Up to half a
  day's records can go missing on that path, and the day is never re-offered,
  so a log line is not a surface any caller can query afterwards.
  `FetchResult.note` carries it to `SyncReport.notes`, deliberately *not* to
  `errors`: an error names a day that will be retried, a note names one that
  will not. Pinned by `test_a_short_day_that_completes_is_reported`.
- **An OpenAlex page emits its valid records before the page is refused**, so
  a first page with `"meta": null` fails with `record_count=1`, not 0. Those
  records were already handed to `on_record` and are stored; the day is
  retried regardless. The ordering of the three checks in the loop is what
  makes that true — do not "tidy" them into one block.
- **A permanently-unstorable record pins its day into a retry on every run.**
  Accepted knowingly (#90): loud — an ERROR and a `SyncReport.errors` line
  each time — beats a day silently missing a record it holds by name. The
  alternative considered and rejected was failing only on a *total* store
  failure.
- **The per-record `except Exception` in `sync()` stays broad.** One bad
  record must not lose the batch. What changed is that it logs the exception
  *type*, which is what tells a bmlib defect from bad source data; narrowing
  the guard instead would abort the day. Pinned by
  `test_the_store_failure_log_names_the_exception_type`.

## publications — a day fetched before it ended is not durable (#95)

`_days_needing_fetch()` re-offers a completed day whose `downloaded_at`
precedes **12:00 UTC on the following day**, which replaced an unconditional
`if current == today` branch. The manual's *When a day is over* argues it in
full. What is easy to get wrong later:

- **The 12:00 is not a safety margin, and must not be "simplified" to a date
  comparison.** Day *D* ends last in UTC−12, whose midnight is noon UTC on
  *D+1*. All three built-in sources are US-based (UTC−5 to UTC−8), so
  comparing UTC *dates* calls a fetch at 00:30 UTC on *D+1* durable while
  PubMed's day *D* still has four and a half hours to run (US Eastern in
  winter; three and a half on daylight time, and longer still for a
  Pacific-time source); comparing *local* dates is up to 16 hours out for a
  machine in Sydney. Pinned by
  `test_a_second_before_noon_utc_the_next_day_is_not` and
  `test_an_offset_timestamp_is_compared_as_an_instant_not_as_a_wall_clock`.
  **Both figures were rounded wrong in the first round** — "five hours" and
  "15 hours" — across five documents; they are arithmetic, so check them
  rather than copying them.
- **The comparison is `>=`.** A fetch at exactly the boundary saw the whole
  day everywhere. `<` versus `<=` here is a one-character edit only
  `test_noon_utc_the_next_day_is_late_enough` notices, which is why it exists
  — the same reason `test_exactly_the_floor_completes` does. That claim was
  **false when first written**: the negative control beside it,
  `test_a_day_fetched_after_it_ended_everywhere_is_not_offered_again`, used
  the identical boundary timestamp, so the two tests were the same test twice
  and died to the same mutation. The control now sits days past the boundary.
  A test asserting it is the sole pin for something is worth checking against
  its neighbours before it is believed.
- **Every day in a window is judged against its own boundary.** Passing
  `date_from` where the loop passes `current` **survived the entire suite**
  when this landed, because all eleven of the rule's tests used a one-day
  window — and it silently reintroduces #95 for any cron after 12:00 UTC.
  Pinned by `test_each_day_in_a_window_is_judged_against_its_own_boundary`.
  The general lesson: a rule that selects over a range needs at least one test
  whose range has more than one answer in it.
- **Removing the `today` branch was not a simplification for its own sake.**
  That instant is also exactly the point beyond which "now" cannot fall inside
  day *D* anywhere, so the timestamp rule *subsumes* the special case rather
  than approximating it — and with it gone, the wall clock no longer *decides*
  whether a completed day is done, which is what makes the rule testable
  without faking the clock. It is still read, but only as the upper bound
  below, which can move the answer towards a re-fetch and never away from one.
  Pinned by `test_today_is_still_offered_although_the_special_case_is_gone`.
- **A `downloaded_at` that cannot be read fails closed, and "unusable" is
  three shapes, not one.** Naive, unparseable, and not-a-string all mean the
  same thing to the rule; the naive case is the one that would otherwise raise
  `TypeError` from inside day selection and abort a whole sync before a record
  was fetched. Not-a-string is the shape a change of the PostgreSQL DDL to a
  real timestamp type would produce, which
  `test_the_durability_rule_can_read_what_each_backend_stores` guards from the
  other side — a **CI-only** guard, since that failure is reachable only
  through psycopg2 and the SQLite half proves nothing `test_sync.py` does not.
- **A `downloaded_at` that reads cleanly but cannot be true fails closed too,
  and that gap was live for a round.** A fetch cannot have happened in the
  future, so a restored backup, a bad RTC or an external writer could put a
  past-the-boundary timestamp on a running day and every such day read durable
  forever. The guard was loud about a value it could not parse and silent
  about one asserting the day was fetched tomorrow — #95's own failure mode.
  `_CLOCK_SKEW_TOLERANCE` is five minutes and is **a fixed choice, not a
  measured one**; unlike `SHORTFALL_FAILURE_RATIO` it is bounded on both sides
  by an asymmetry rather than by taste — too tight costs one merged re-fetch,
  too loose loses a day permanently — which is the argument for keeping it
  small rather than generous. Pinned by
  `test_a_timestamp_from_the_future_is_not_read_as_durable` and its negative
  control `test_a_clock_a_few_minutes_fast_is_still_believed`.
- **`last_verified_at` gets its own reader, deliberately laxer.** Only the
  calendar date is used, so a naive value is perfectly usable here where it is
  not for the durability rule; routing it through `_read_aware_timestamp`
  would fail closed on every naive row and re-fetch the whole window on every
  run for a `recheck_days` caller. What the two share is why they exist: read
  raw — as this column was for a round after #95 landed — a corrupt value
  raises `ValueError` from inside day selection, which escapes `sync()` (whose
  `try` carries only a `finally`) and kills the whole multi-source run before
  a single record is fetched, `SyncReport` and all. That is worse than the
  per-day losses the rest of these rules guard against, because it is total.
  A stored `NULL` is **not** unusable — it is the documented "never verified"
  state — so it rechecks without a warning; warning on it would fire for every
  row of a fresh install and tune the real warning out. Pinned by
  `test_an_unusable_last_verified_at_rechecks_rather_than_raising`,
  `test_a_non_string_last_verified_at_rechecks_rather_than_raising` and
  `test_a_null_last_verified_at_rechecks_without_warning`. The non-string case
  needs its own test even though `downloaded_at` short-circuits before rule 4
  is reached: a DDL change moving *both* columns never gets here, only one
  moving this column alone does — and without the test, removing the
  `isinstance` guard survived the whole suite while turning a recheck into a
  `TypeError` that aborts the run.
- **It does not fix late indexing, and must not be stretched to.** A record
  that appears for day *D* three days later is not covered by any rule about
  when *D* ended. `recheck_days` is what exists for that.
- **The extra re-fetch is the fix working, not a cost to optimise away** — but
  state it correctly. Under the default window `[yesterday, today]` it is
  exactly one extra per run, two rather than one, since day *D* is offered
  again on *D+1*. A window of three days or more, run before 12:00 UTC, pays
  one more (three); it does not grow with the window beyond that, and vanishes
  for a run at or after 12:00 UTC. On the **first run after upgrading** it is
  larger and one-off: every row the old code stored was written while its own
  day was current, so none is durable and the whole window is re-fetched once
  — measured at 29 of 29 days for a 30-day window, per source. All merged by
  `store_publication()`. The first round of docs said this "costs nothing
  under the default window", which is the one claim here that was simply
  arithmetic and wrong.
- **The default two-day window never certifies a day for a run before 12:00
  UTC**, and that is a property of the window, not a defect in the rule. Day
  *D* is fetched on *D*, re-fetched on *D+1* at the same hour — still short of
  its own boundary — and then the window slides past it. No records are lost:
  the *D+1* fetch happens after day *D* ended for every US-based source. But
  the row is left permanently non-durable, so a caller who later widens the
  window re-fetches it. Running at or after 12:00 UTC, or with a window of
  three days or more, settles every day.

## publications — what the durability rule refuses to guess (#98, #99)

Both were raised by the review of PR #97, both pre-existing, and both are
about the same thing from opposite ends: a value the day-durability rule
cannot honestly read. Argued inline in `publications/models.py`
(`_require_datetime`) and `publications/sync.py` (`_validate_window`).

- **`DownloadDay.from_dict()` raises on an absent `downloaded_at` rather than
  defaulting it to now (#98)** — the column is `NOT NULL` in both DDLs, so a
  dict lacking it did not come from the database, and *now* is the single most
  durable-looking value `_day_was_over_when_fetched()` can be handed. The SQL
  path fails **closed** on that column (unreadable, or in the future); the
  model must not disagree with the rule about what an absent value means. The
  other two options were weighed and rejected: returning `None` makes the
  field `datetime | None`, a typing break on a `py.typed` package that pushes
  the decision onto every caller for a state none of them can do anything
  about; keeping the default and logging leaves the fail-open in place, and
  "logged but wrong" is exactly what the #88–#95 family is a register of.
  Pinned by `TestDownloadDayRequiresTheTimestampTheRuleReads`.
- **The dataclass default that stamps now is deliberately kept**, and the
  asymmetry with `from_dict()` is the point: a freshly constructed
  `DownloadDay` describes a fetch that has just happened, while `from_dict()`
  deserialises a row that was already stored. Pinned by
  `test_constructing_a_row_still_stamps_now`, or nothing would notice it being
  "tidied" into consistency.
- **`from_dict()` does not re-judge a timestamp it *can* read** — naive, or in
  the future, both deserialise fine. Faithful deserialisation is the model's
  contract and usability is the rule's; duplicating the rule here would reject
  rows the database legitimately holds and which the rule already answers by
  re-fetching. `test_a_stored_timestamp_is_read_verbatim` is the negative
  control for the ordinary value, and
  `test_a_naive_or_future_timestamp_still_deserialises` pins the two cases
  the claim actually rests on — the review that added it found the claim
  unpinned, since neither of the two named values was covered by anything.
- **Every rejection is a `ValueError` naming the field.** Delegating straight
  to `_parse_datetime` did not deliver the contract the docstring advertised:
  a non-`str` escaped as `TypeError` out of `fromisoformat`, so a caller
  writing the documented `except ValueError` got an uncaught crash, and an
  unreadable string raised `Invalid isoformat string: ''` — which names
  neither the column nor the row, leaving a bulk deserialiser nothing to
  report. A plain `date` is the trap worth naming: `isinstance(datetime_value,
  date)` is true but the converse is not, so it looked accepted and was not.
  Nothing here could ever fail *open* — the durability rule refuses every one
  of these values — so this is a contract fix, not a safety one.
- **`Publication.created_at` / `updated_at` keep the same defaulting
  `from_dict()` just lost**, on purpose. Nothing decides whether work may be
  skipped from them, so *now* is a harmless default there and a load-bearing
  one for `downloaded_at`. The fix is scoped to the column a rule reads.
- **`sync()` validates `date_from`, `date_to` and `recheck_days` at its
  entry, and the helpers do not catch `OverflowError` (#99)** — an `except
  OverflowError` around the arithmetic converts a caller bug into a day that
  quietly looks like it needs no fetch, which is the failure mode this whole
  family exists to remove. A negative `recheck_days`, until now silently
  swallowed by `recheck_days > 0`, is rejected for the same reason.
- **The guard is a *type* check as well as a range check, and the type half
  is the one that matters most.** `datetime` subclasses `date`, so
  `sync(date_to=datetime.now())` satisfies the annotation and every type
  checker, and no value check can see it — `datetime.max == date.max` is
  `False`. Mistaking `datetime.now()` for `date.today()` is a likelier slip
  than any input #99 originally named, and it fails in two shapes: mixed with
  a `date` it raises `TypeError` from the comparison and loses the run's
  report; on **both** ends it raises *nothing*, and writes
  `download_days.date` values carrying a time component that no date-keyed
  lookup can ever match. That row is re-fetched for the life of the
  installation and the table accumulates rows nothing reads. The silent shape
  is why this is a type check and not another value check, and why it is
  worth spending a branch on an input the annotation already claims to
  exclude. `float('nan')` is the same lesson on the other parameter: it slips
  both range checks, because every comparison against it is `False`, and then
  disables rechecking in silence. Pinned by
  `test_a_datetime_window_never_reaches_the_download_days_table` and
  `test_a_recheck_days_that_is_not_a_whole_number_is_refused`.
- **Not every rejection is guarding an exception, and the docs must not say
  it is.** Two of them — a negative `recheck_days`, and `nan` — walked fine
  and were swallowed silently. An earlier draft of `docs/manual/publications.md`
  summarised the table as "each previously raised `OverflowError`", two lines
  below a row saying one of them was silently ignored. The distinction is the
  entire point of the write-up: two were a total run loss, two are silent
  no-ops, one is silent corruption.
- **A window reaching into the *future* is NOT rejected; it returns a
  `SyncReport.notes` line and logs a WARNING.** `_day_was_over_when_fetched()`
  needs a fetch at or after 12:00 UTC on the following day, which a day that
  has not happened can never satisfy — so the row is stored `completed` and
  re-offered on every run forever, and until now at no log level and in no
  field of the report. Permanent *and* invisible is the pair the shortfall
  rule and `FetchResult.note` exist to break up, so this takes the same
  answer they do. Rejecting was weighed and refused: the past half of a
  window ending tomorrow is perfectly fetchable, and raising would discard it
  along with the unreachable half. Pinned by
  `TestAWindowReachingIntoTheFutureSaysSo`, whose negative control keeps the
  ordinary `date_to=today` window quiet.
- **A fetcher that returns a non-`FetchResult` fails its own day, not the
  run.** The `except Exception` around the call already absorbed a fetcher
  that *raises*; one that *returns* — successfully — something without a
  `.status` reached `_resolve_day_status` outside that handler, and the
  `AttributeError` propagated through the `finally` and out of `sync()`,
  losing every source's report while leaving earlier days committed.
  `register_source()` is public, so the caller getting this wrong is a third
  party; this is the same allowlist reasoning that already records an
  unrecognised `status` as failed. Pinned by
  `TestAFetcherThatBreaksItsContractFailsOnlyItsDay`.
- **An *empty* window (`date_from` after `date_to`) is deliberately NOT
  rejected**, and this is the entry most likely to be re-opened, since it sits
  one line from three validations that do raise. It is what the ordinary
  incremental-sync idiom produces the moment it has caught up —
  `date_from = last_synced + 1 day`, `date_to = today` — so raising would turn
  a caller that is simply up to date into a crashing one. Unlike every
  rejection above it writes no row and claims no day, so it loses nothing.
  `test_an_empty_window_is_still_the_ordinary_way_to_ask_for_nothing` is the
  sole pin, verified by mutation: adding the rejection fails that test and no
  other.
- **The boundaries are pinned from both sides, and the placement is pinned at
  all.** The first round's mutation set was chosen from the same mental model
  as the code, so it contained no boundary-shift and no call-relocation
  mutant: `recheck_days=10**9` against a bound of ~739,842 left every value
  in between indistinguishable — including one that accepts a `recheck_days`
  which really does overflow — and moving `_validate_window` below the
  `httpx.Client` build passed the entire suite, though the client is created
  *outside* the `try` whose `finally` closes it, so a raise there strands the
  pool. `test_the_deepest_recheck_the_calendar_allows_is_accepted`,
  `test_one_day_deeper_than_the_calendar_is_rejected`,
  `test_a_window_ending_one_day_earlier_still_runs` and
  `test_the_window_is_refused_before_an_http_client_is_built` close those.
  The lesson generalises: a mutation set written by the author of the guard
  tends to test that the guard *exists*, not that it is *correctly bounded*
  or *correctly placed*.

## publications — how far a PubMed history session can be walked (#96, #105)

Issue #96 asked whether `fetch_pubmed`'s `range(0, count, EFETCH_PAGE_SIZE)`
skips records: `retstart` advances by the page size *requested*, so a short
non-empty page would seem to leave the records between what arrived and the
next offset never asked for. It was found by reading, not reproduced, and it
is **closed as correct** — but measuring it to answer that turned up a
different defect at the same call site, which is #105. Both are argued inline
in `fetchers/pubmed.py`; `scripts/sample_efetch_paging.py` is the instrument.
`--skip-day-sizes` re-runs the three **session** probes in a fixed 23 requests
(1 esearch + 2 bounds + 17 binary-search steps + 1 straddle + 2 slice); the
day-size populations need a full run, about 150 requests at `--days 120`. Two
figures below the sampler does **not** reproduce, and both say so where they
appear: the 500-of-500 walk was a one-off probe, and the `[EDAT]` comparison
has no flag — `measure_day_sizes` always asks for `[Date - Publication]`, and
a test pins that it cannot ask for anything else.

- **`retstart` indexes the session's UID list, not the records delivered.**
  Measured 2026-08-20: a page's record elements are exactly that slice of
  esearch's own `IdList`, in document order, `<PubmedBookArticle>` entries
  included — 50 of 50, and again 500 of 500 across a full 13-page walk of a
  6,403-record day (6,403 delivered, 6,403 promised). The 50 is what the
  sampler re-runs; the 13-page walk was an ad-hoc probe on 2026-08-19 and is
  deliberately not in it, since it downloads ~25 MB for no extra evidence. So
  a record missing from
  a page is a UID the server had nothing to return for, not one postponed to
  the next page: it *was* requested. **Advancing by what arrived would be the
  bug**, not the fix — it would re-request the tail of every short page,
  deliver those records twice, and count the duplicates as delivery, which is
  exactly what would hide a real shortfall from `reconcile_delivery`. Pinned
  by `TestTheWalkIsIndexedByTheSetNotByWhatArrived`, whose two tests fail
  against #96's proposed fix.
- **The session serves only its first 9,999 records** (#105), and says so:
  `retstart=9999` is HTTP 400 — *"'retstart' cannot be larger than 9998. For
  PubMed, ESearch can only retrieve the first 9,999 records matching the
  query. To obtain more than 9,999 PubMed records, consider using EDirect…"*.
  The quiet half matters more: a page whose window crosses the boundary is
  **clamped without a word** — `retstart=9500&retmax=500` returned 499 records
  at HTTP 200. So "walk as far as the server will go" is not a safe fallback;
  the last page it yields is indistinguishable from a day missing a record.
- **Under `[Date - Publication]`, the field bmlib queries, that cap is not an
  edge case — it is a second population.** A record carrying only a year and a
  month is indexed at day 1 of that month and one carrying only a year at 1
  January, so those days are structurally enormous. Measured 2026-08-20:
  **0 of 58 ordinary days** were over the cap (median 4,890, max 8,150) and
  **16 of 16 month firsts and 1 Januarys** were (median 73,266, max 315,282 —
  month firsts 49,543–90,571, 1 January 212,439–315,282). A 60-day sync window
  meets 2. Measuring `[EDAT]` instead gives 4 of 120 days, none above 12,096 —
  so the largest day the right field finds is 26× the largest the wrong one
  does (315,282 against 12,096). The wrong field understates the magnitude by
  that much and attributes to load spikes what the indexing convention does.
  Worth stating because that is the reading a reader reproduces if they sample
  the field the *name* suggests; it was a one-off probe on 2026-08-20 and the
  sampler has no flag to repeat it.
- **An over-cap day was refused before a single record was fetched — and that
  is now history, kept because the reasoning still binds the one case left.**
  Such a day cannot be `completed` — that would durably lose the remainder,
  which is the whole point of the family above — so it was `failed`, and a
  failed day is re-offered on *every* later run, which also meant
  `SyncReport.errors` never returned to empty while such a day was in the
  window (issue #107, dissolved rather than answered — see the partitioning
  entries below). That made the only real question what the doomed run costs.
  Walking first would buy the first 9,999 records once and then
  re-fetch them forever: a six-year backfill carries some 72 structural days,
  ~3 GB per run, storing nothing new. Refusing costs one esearch. The
  trade-off was deliberate and was the maintainer's call: **while it stood,
  those days had no records at all rather than the reachable 9,999** — a
  containment, not a fix, and "no publication is missed" was false for as long
  as one was in the window. **#105 has since landed and partitions such a day
  into sub-queries that each fit**, so the refusal now applies only where the
  ladder cannot reach: a single Entrez date over the cap. The absolute rather
  than the fraction is still the right way to read the loss it was containing:
  9,999 is a fifth only of the *smallest* structural day, a seventh of the
  median month first and a thirty-second of 1 January.
- **Before the containment, an over-cap day *mostly* failed anyway —
  inscrutably.** The walk asked for record 10,000, `raise_for_status()` fired
  on the 400 before the body was read, and the day failed with `Client error
  '400 Bad Request'`: the right verdict, reached after twenty pointless
  requests, naming neither the cause nor the remedy. **With one exception, and
  it is the one that matters.** A day of *exactly* 10,000 records never issues
  a `retstart` above 9,998 — `range(0, 10000, 500)` stops at 9,500 — so it
  never met the 400 at all. It walked to its natural end, its last page was
  silently clamped to 499, and it delivered 9,999 against a promise of 10,000:
  a shortfall of one record in ten thousand, far above
  `SHORTFALL_FAILURE_RATIO`, so the day was recorded **`completed`** —
  durable, never re-offered, one record lost with only a note. Finding that is
  what made the containment worth having: it moved exactly one day-size from
  *silent* success to loud failure, closing a durable loss rather than merely
  improving an error message. **All of that is now history too.** #105 has
  landed, `count > EFETCH_MAX_RETRIEVABLE` routes to `_fetch_partitioned`, and
  a day of exactly 10,000 records is partitioned like any other over-cap day —
  its ten-thousandth record is requested with the rest, so no day-size is
  refused on the cap any more, and none is silently clamped into completing
  short of its promise. Both halves of that are claims about the cap, not a
  guarantee that a day arrives whole: a walk still completes on a note when it
  comes up short while clearing `SHORTFALL_FAILURE_RATIO`, as it does for
  every source. The bullet is kept because the silent-clamp mechanism it
  documents is still what the page walk is written around, and because a
  reader who finds this page while wondering why the walk looks the way it
  does should not have to rediscover the 10,000-record case to learn that it
  was real.
- **The cap is a hard-coded 9,999 and that is a cost, not an oversight.** If
  NCBI *raises* it, bmlib partitions days it could now have walked in one
  session — extra ESearches and one session per part, with no record lost and
  nothing logged above INFO, which is why that direction is acceptable. (Until
  #105 it was acceptable for the opposite reason: bmlib *refused* those days,
  loudly, in an ERROR naming the cap. The change made that direction cheaper
  and quieter, so it is now the sampler and not a log line that would tell
  you.) If NCBI
  *lowers* it, the guard does **not** reliably fail closed, and it is worth
  being exact rather than reassuring about why. The walk meets a 400 only when
  it *requests* a page starting past the live limit, so for any count between
  the lowered cap and the next page boundary — a band up to `EFETCH_PAGE_SIZE`
  wide — no page is ever requested past it: the straddling page is silently
  clamped, the walk ends naturally, and the shortfall is at most 499 records,
  which is far above the failure floor, so the day completes on a note.
  Simulated against a cap of 4,750, counts 4,751–5,000 all completed having
  lost up to 250 records apiece and only 5,200 upwards raised the 400. What
  makes the *current* pairing safe is that 9,999 sits exactly one below a
  500-record page boundary — a coincidence of the two constants, not a
  property of the design. **`scripts/sample_efetch_paging.py` is therefore the
  real guard against a moved cap**, in either direction: re-run it before
  touching either constant, and it reports agreement or `DISAGREES` against
  the live backend.

The rest of this section is #105's fix — the partitioning that replaced that
refusal — and the choices inside it that read as arbitrary and are not. The
full argument is in
`docs/superpowers/specs/2026-08-21-pubmed-day-partitioning-design.md`.

- **Why an Entrez-date range, and not a facet.** Any predicate `P` splits a
  day into `AND P` and `NOT P`, which is disjoint and covering by
  construction — but only if `P` is something every record either satisfies or
  does not, *and* both halves are subdividable by the same step. The facets a
  reader reaches for first fail one of those, and the first failure is the
  dangerous one. **Publication type, MeSH term**: a record carries several, so
  `AND pt1` and `AND pt2` overlap, the same record is fetched twice, and
  delivery is inflated *past the day's own count* — which is exactly what
  would hide a real shortfall from `reconcile_delivery`, the guard this whole
  family exists to keep working. **Journal, language**: can be absent, and the
  vocabulary is unbounded and heavily skewed, so a ladder over one neither
  terminates predictably nor covers. **`NOT P` chains generally**: the
  complement of a facet value is not itself subdividable by the same
  mechanism, so the recursion has no uniform step. A numeric range has both
  properties as arithmetic: `[lo, mid]` and `[mid+1, hi]` tile `[lo, hi]`, and
  each half is the same kind of thing as its parent, so one step recurses to
  any depth. Entrez date is the range every record has exactly one of — and,
  because a structural day's records were indexed across decades, it shards
  such a day well rather than piling it into one bucket.
- **The ladder root is a fixed `1900/01/01 – 2100/12/31`, not derived from the
  target day.** Deriving it would mean assuming how far before or after
  publication a record may be indexed, which is the thing the root probe
  exists to *verify*. Sibling counts come from subtraction (`right = parent −
  left`) rather than a second ESearch, which is sound only because the halves
  tile — measured below, not assumed. A zero-count range is skipped rather
  than recursed, so the decades at either end of the root that hold nothing
  cost no further requests.

The ladder was measured before it was implemented, and again afterwards — the
second time by a descent that deliberately does not import the one it measures
(`scripts/sample_efetch_paging.py --partition`), since a corpus labelled by
the rule under test can only confirm that rule. Every probe is against the
live backend on 2026-08-21:

| Day | Probed by | Count | Root probe | Parts | Depth | ESearch calls | Sum of parts | Stuck |
|---|---|---|---|---|---|---|---|---|
| 2025/01/01 | sampler | 266,421 | = day count | 44 | 13 | 51 | exact | 0 |
| 2024/01/01 | design | 242,216 | = day count | 37 | 13 | 40 | exact | 0 |
| 2024/01/01 | sampler | 242,216 | = day count | 37 | 13 | 40 | exact | 0 |
| 2023/01/01 | sampler | 257,836 | = day count | 41 | 13 | 44 | exact | 0 |
| 2020/01/01 | design | 234,972 | = day count | 37 | 13 | 40 | exact | 0 |
| 2015/01/01 | design | 227,173 | = day count | 36 | 13 | 40 | exact | 0 |

Four things that establishes, each of which the design would otherwise be
assuming: that an `[EDAT]` range term composes with a `[Date - Publication]`
term at all; that the root **covers**, since `count(day AND root)` equalled
`count(day)` on every day probed; that the halves **tile exactly**, since the
leaves summed to the root with no residue, which is what makes subtraction
sound and double-counting absent; and that the ladder **terminates well above
its floor** — depth 13 of the ~17 halvings the root span allows, and no single
Entrez date over the cap on any day probed. The design's own run measured the
largest leaf at 9,931 and a single Entrez day of 2024/01/01 at 2,026 records.
Both bounds matter: leaves sit *just* under the cap by construction, since
halving stops the moment a part fits, which is why a part re-checks its own
count when its session opens and re-partitions rather than walks if it has
crossed the cap since planning.

- **The root probe tolerates long and refuses short, and the asymmetry is the
  same one that governs the day total.** Short (`root < day`) fails the day:
  records of this day are indexed outside the ladder, they are in no part's
  promise, and so every part would reconcile perfectly while the day is
  silently incomplete — the exact durable, invisible loss #88–#95 exist to
  prevent. Long (`root > day`) proceeds: the two counts are two ESearches at
  two instants, and a record indexed between them is stamped EDAT=today, which
  is *inside* the range, so it inflates rather than hides. Requiring equality
  would fail a correct ladder on ordinary drift, and the day-total reconcile
  still judges what actually arrived.
- **A removal between the two root counts also reads as short, and fails the
  day. That is accepted, not overlooked.** The alternative is a tolerance
  band, which would be a second threshold nothing has measured — bmlib already
  carries one (`SHORTFALL_FAILURE_RATIO`'s 0.5, which issue #92 exists to
  measure), and that is one more than is comfortable. The verdict is
  recoverable rather than durable: a failed day is re-offered on the next run,
  which re-probes, so a genuine removal costs one re-planned day and not a
  record.
- **`part_key` is opaque to storage, and the cost of that is a silent
  re-fetch.** The storage layer does not know how a fetcher partitions, so a
  second rung — or another fetcher splitting some other way — needs no schema
  change; typed `edat_lo`/`edat_hi` columns would bake one fetcher's scheme
  into the shared schema. What it costs is worth naming, because it is the
  worst kind: the skip rule is a **string comparison**, so a key format that
  drifts between releases matches nothing, resume degrades to re-fetching
  every unfinished day in full, and *nothing is raised* — a cost with no
  error. Two things answer that, and both must be kept: `_part_key()` is the
  one constructor for the string, with a test pinning its exact output
  literally; and the `part_scheme` column records which scheme wrote the key,
  so a scheme change is visible in the data and stale rows can be recognised
  and dropped deliberately rather than silently mismatching. `part_scheme` is
  written on every row and read back into `PartCheckpoint`, but **nothing
  branches on it** — that is the point of it, not an oversight, and a future
  scheme change is what it is waiting for.
- **A part is not checkpointed unless it reconciled clean, and not unless
  every one of its records stored — but it is always *flushed*.** Three rules,
  all fail-closed, all learned in review of this change. Flushing is the one
  that is unconditional, and separating it from checkpointing was the third
  round's fix: the callback that hands a finished part to `sync()` is the only
  thing that empties the record buffer, so calling it only for a part that
  reconciled clean made the per-part memory bound conditional on the source
  behaving. A degraded NCBI returning 37 short-but-not-failing parts of a
  242,216-record day would then hold every one of those 242,216 records in
  memory at once — the exact peak the per-part flush exists to remove, reached
  precisely when the source is misbehaving. The callback therefore carries
  `PartCheckpoint | None`: the records are stored either way, and the
  checkpoint is what the part has to earn. The two remaining rules are what it
  earns them against. A part that reconciled with a *note* delivered
  short of its own promise without clearing the failure floor; checkpointing
  it would let a later run skip it and credit the full `promised` it never
  delivered, manufacturing the records the note was reporting missing — and
  that later run would carry no note at all, since this run's note dies with
  it if a subsequent part fails. A part holding a record that would not store
  is the same shape: the store failure records the day `failed`, so it is
  re-offered, and a checkpoint written beside the gap would make that retry
  skip the one part holding the missing record. `_store_records` swallows a
  record's own exception so one bad record cannot lose the batch, which is
  precisely why the failure count has to be read back and acted on at the
  checkpoint boundary.
- **A part that reports 0 records having been measured non-empty at planning
  fails the day; it is not dropped.** Two of bmlib's own measurements
  disagree — planning counted this Entrez-date range at *n* > 0, the part's
  own session ESearch now says 0 — and the weaker one does not get to decide.
  Dropping it at INFO was silent at a scale nothing downstream catches:
  fourteen such parts of a 37-part day still deliver 62% of the day, which
  clears the day-level floor, so the day would be `completed`, never
  re-offered, and ~92,000 records permanently absent behind one shortfall
  note. The asymmetry is what settles it — a part that *delivers* 1 of 5,000
  fails the day, so a part that *claims* 0 having been measured at 5,000
  thirty seconds earlier cannot pass. It is reconciled like any other part
  (`delivered=0` against the *planned* promise), which always fails. **The
  cost is a re-fetch of one day**, and it is bounded: the parts already walked
  are checkpointed, and a range that genuinely emptied returns no partition at
  all when the next run plans the day, so it self-heals in one extra day-fetch.
  One residual, named so it is not re-discovered as a bug — and not confined to
  a re-plan. A re-plan carries `known_count` forward from the part's own session
  ESearch while its child counts come from fresh planning probes, so a count
  that *shrank* between the two hands the surplus to a right-hand child by
  subtraction — but `_plan_partitions`'s `descend` derives every right-hand
  child's count the same way (`right = n - left`), so the **first** plan carries
  the identical exposure, not only a re-plan. A reviewer reproduced it on a
  first plan: one record withdrawn immediately after the root probe — a benign
  cause `fetchers/_reconcile.py` itself names — parks the surplus on a
  structurally empty tail leaf such as `edat:2050-10-02:2100-12-31 promised=1,
  actual=0`, because the ladder's root spans 1900–2100. That day previously
  completed on a one-record note; it now fails. The verdict stands — failing
  closed beats the silent loss — and the case is recoverable the same way the
  root probe's own removal is: the next run re-plans from fresh probes, the
  phantom does not recur, the parts already checkpointed are skipped, and the
  day completes. But "recoverable" is not "cheap": a day that partitions at all
  is already over 9,999 records, so the re-fetch this costs is up to ~580
  requests and ~1 GB, not the one day-fetch the phrase implies.

  **Narrowed by the review of PR #114, in the two directions where the
  derivation is unrecoverable rather than merely wrong.** A phantom that
  reaches a *single date* is now measured before the day is refused on it, so
  the `edat:2050-10-02:2100-12-31` shape above measures 0 and disappears
  instead of failing the day — the surplus walks down the empty tail to a leaf
  and dies there. And a derived count of **zero** is measured rather than
  trusted anywhere in the descent, because it is the one derivation nothing
  downstream can repair: any other error still yields a part, and a part
  re-counts itself when its session opens, but a zero yields no part at all, so
  the range is never visited, every part planned around it reconciles
  perfectly, and the shortfall reaches only the day total — where anything
  under the floor completes on a note, durably. Reproduced end to end before
  the fix: a six-record day fetched five, returned `completed`, and carried
  neither note nor error.

  What is *not* narrowed, and stays exactly as recorded above: a phantom whose
  derived count is positive and lands on a range wide enough to become a part
  without reaching a leaf. That part's own ESearch reports 0 when its session
  opens, and the day fails there. Measuring every derived count at plan time
  would close it, at one ESearch per part — some 37 more on a 37-part day —
  and the verdict above already prefers the loud failure to that standing cost.

  Measured cost of what *was* changed: +3 planning probes on a synthetic
  401,500-record, 64-part day (68 → 71), and 24 on a day whose records all
  share one Entrez date. Not re-measured against the live backend, so the
  "40 planning ESearches" in the table above is the ladder **as it was
  measured** and the current one spends a little more.
- **A planning ESearch failure is a returned `FetchResult`, not a raise.** The
  ladder's counting probes are ordinary ESearch requests and fail like any
  other — a 500, a dropped connection, an `<ERROR>` document `_esearch`
  reports as a `ValueError`. `sync()` absorbs a raise and fails the day either
  way, so this costs no records; what it costs is coherence. The under-cap
  path returns `failed` for exactly this, and one public function answering
  the same transient with a return value or an exception depending on how
  large the day happened to be is a contract with a hole in it — the manual's
  own table of what a partitioned day returns lists return values only. Both
  planning call sites (first plan, and the re-plan a part triggers) now return
  it.
- **The PubMed FTP baseline is not the route, and this is recorded so it is
  not re-derived.** NCBI's own 400 suggests EDirect, and the annual baseline
  plus daily update files at `ftp.ncbi.nlm.nih.gov/pubmed/` are the documented
  way to retrieve in bulk. They lose here on three counts. The baseline is the
  **whole corpus** (~37M records, tens of GB) with no publication-date
  selectivity, so reaching one day's 242,216 records means reading all 37M and
  discarding 99.3% of them — two orders of magnitude worse, per day, than the
  ~562 requests the ladder costs (its ESearches measured, its EFetch pages
  arithmetic over the record count; the comparison survives either way). It
  only wins for a *full-corpus* load, at which point `download_days`' entire
  per-day model is beside the point and the question is no longer "how does
  this fetcher walk a day" but "does bmlib have a second ingestion mode",
  which is a product decision and not this fix. And it has no path at all for
  the ordinary incremental case, which is what `sync()` is. A whole-corpus
  ingestion mode remains open as its own question; nothing in #105 forecloses
  it.
- **A part's own count is reconciled against planning's with the existing
  floor, not with `== 0`.** The rule shipped in #105 refused a part whose
  session ESearch reported zero where planning had measured records, on the
  ground that two of bmlib's own measurements disagree and the weaker one does
  not decide. The review of PR #114 found the argument never depended on the
  collapsed count being *zero*: a part reporting 1 where planning measured
  5,000 was walked, delivered its 1, reconciled that 1 against **itself** —
  which always passes — and was checkpointed as clean. Reproduced: 8 of 20
  parts collapsing 10 → 1 completed the day holding 128 of 200 records, with
  eight parts recorded as having reconciled cleanly. The asymmetry settles it
  unchanged: a part that *delivers* 1 of 5,000 fails the day, so a part that
  *claims* 1 cannot pass. It reuses `SHORTFALL_FAILURE_RATIO` rather than
  demanding equality, because two requests at two instants routinely differ by
  a record and a day recorded `failed` is re-fetched for the life of the
  installation; and a part collapsing to 0 still always fails, since no planned
  part promises fewer than one record. A checkpoint now requires **both**
  reconciles clean, for the reason the delivery one already did: a note dies
  with the run that produced it, so checkpointing a short part lets a later run
  skip it and report no note at all.
- **A day-level count of zero does not overrule that day's own checkpoints.**
  The same rule one level up, where partitioning had newly made it reachable: a
  run can leave a day `failed` with most of its parts stored and checkpointed,
  and if the day-level ESearch then answers a soft zero — the mechanism the
  part-level rule exists for — `fetch_pubmed` completed the day at zero records
  without consulting `completed_parts`, while `sync()` deleted the day's part
  rows in the same transaction. The write that lost the records destroyed the
  checkpoints that would have made re-fetching them cheap. Reproduced: 20
  checkpointed parts and 130,000 records became `completed` at `record_count=0`
  with no part rows, empty `errors`, empty `notes`, and the day was never
  offered again. It fails instead, and the message carries the part count and
  record total because the remedy — deleting the rows if the day really was
  withdrawn — is a judgement an operator makes from it. A quiet day with no
  stored parts is untouched, which is what keeps this from being a blanket
  refusal of every empty day. The accepted cost is that a day PubMed genuinely
  empties stays `failed` until an operator drops its rows; PubMed emptying a
  120,000-record day is not a thing it does, and the alternative is the silent
  permanent loss above.
- **An over-cap day is partitioned before the history session is checked.** The
  session opened by the day-level ESearch is unused on the partitioned path —
  `_fetch_partitioned` opens one per part — so a count without a `WebEnv` was
  costing a day that was perfectly fetchable, and a failed day is re-offered on
  every later run. The guard still stands for the under-cap path, and if the
  anomaly is not transient each part's own session guard fails the day anyway.
- **`resumable=True` is checked against the fetcher's signature at
  registration.** `sync()` reads the *descriptor* to decide whether to pass the
  three resume keywords, so a descriptor declaring more than its fetcher
  accepts raised `TypeError` inside the per-day handler — recording every day
  of the range `failed`, on every run, forever. Loud, but once per day rather
  than once per mistake, and at a place naming the day instead of the
  registration. A `**kwargs` parameter satisfies the check, since that is how
  the built-in fetchers absorb per-source configuration, and a callable with no
  introspectable signature falls through to the old behaviour rather than being
  refused on an absence of evidence.
- **`PartCheckpoint` is read as strictly as `DownloadDay`, and guarded at the
  same place.** It is the other model on the day-selection path, and #98/#99's
  rules had not been carried across. `from_dict` used `str()` and `int()`,
  which accept everything: a missing column raised `KeyError` and a null raised
  `TypeError` — neither caught by the documented `except ValueError` — a bad
  value reported `invalid literal for int()`, naming neither column nor row,
  and `str(None)` became the literal `"None"`, so a null `part_key`
  deserialised into a key matching no plan and resume degraded to re-fetching
  every unfinished day with nothing raised. `__post_init__` refuses what cannot
  describe a finished part, but imposes **no** `record_count <= promised` rule:
  `promised` counts record elements the server delivered and `record_count`
  those the fetcher parsed, so the two are not commensurable — the conflation
  `_EFetchPage` exists to prevent. And `_load_day_parts` ran *before* the
  per-day handler, inside a source loop carrying only a `finally`, so one
  malformed row escaped `sync()` and left the whole multi-source run with no
  `SyncReport` at all — reproduced with a text value in `promised`, which
  SQLite stores into `INTEGER NOT NULL` without complaint. It fails the day
  instead. Failing *safe* — proceeding with no checkpoints — was rejected:
  fetching a day from scratch would be correct, but recording it `completed` on
  a run that could not read what an earlier run stored is recording success
  over an unknown, and `completed` is never re-offered.

## publications — retractions

- **`bmlib.publications.retractions` has no downloader** (the Crossref
  endpoint 504s freely), **is not a fetcher and never will be without a
  protocol change** (a notice annotates a paper usually not in the caller's
  table — see the design doc's "Why this is not a fetcher"), **is not wired
  into `transparency/` or `quality/`** (both are scoring changes moving
  stored values), and **has no `is_paper_retracted()` wrapper** (keeping the
  pure rule separable from the I/O is what makes it testable).
- **Two values measured against the live export, not reasoned about**: the
  `%m/%d/%Y` / `%d/%m/%Y` ambiguity resolves US-first (confirmed by same-file
  dates whose day exceeds 12), and `_ABSENT_IDENTIFIER_VALUES` holds exactly
  `{"0", "unavailable"}` — a third sentinel needs its own measurement.

## quality — Cochrane assessor (merged, PR #54)

Full reasoning in `docs/superpowers/specs/2026-08-05-cochrane-assessor-design.md`
and `docs/manual/quality.md`; every claim below has a named test.

- **Nothing is fabricated to fill a gap:** `assess()` returns `None` on
  failure rather than nine defaulted "Unclear risk" domains;
  `collapse_risk_of_bias()` raises on an unrecognised `bias_type` rather than
  skipping it into a `BiasRisk` that looks complete; `unclear` outranks `low`
  in its worst-wins reduction; `_enrich_with_cochrane()` does not copy
  Cochrane's `evidence_level` onto the assessment's; `study_id` comes from the
  caller, never parsed from an author list.
- **`_ASSESSMENT_ATTEMPTS = 2`, not 1 or 3** — `chat_json()` already retries
  inside each attempt; two keeps the worst case at six model calls.
- **Oversized text is condensed in exactly two passes** — digest, then one
  nine-domain judgement, no per-chunk verdicts to merge (blinding needs the
  whole Methods in view) — and **`_condense()` checks `len(digest)` against
  the budget, not `ProcessingStatus`**: `TRUNCATED` names the recursion
  ceiling, not the size of what it produced (a 21,269-char digest was measured
  emerging from a 200-char budget). Carries a negative control,
  `test_the_guard_does_not_reject_a_digest_that_actually_fits`.
