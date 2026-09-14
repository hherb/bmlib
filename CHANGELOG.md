# Changelog

All notable changes to bmlib are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); bmlib follows
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **Cell text that reaches no table leaves a line** (issue #245, found while
  measuring issue #243). `<array>` is JATS's *non-floating* tabular structure —
  tabular markup with no `<table-wrap>` wrapping it — and bmlib models none of
  it, so no `_TableBuilder` opens and no `JATSTableInfo` is ever built. Defined
  by the wrapper's absence and not by the absence of a `<table>`, which is what
  the arm tests: JATS admits a `<table>` inside an `<array>`, and such a cell
  reaches the counter too. Until #243 that text still reached the buffer
  above the cell: the enclosing `<sec>`'s, where it was discarded, or the
  enclosing `<p>`'s, where it was spliced into the sentence. Isolating the
  cell's own buffer makes the loss total in the second shape too, which is the
  right direction by this module's standing preference — a blank beats a wrong
  value (#116, #162) — and is what earns the counter rather than an excuse,
  `refused_apparatus_prose`'s rule.

  `cell_text_dropped` counts the *cell*, never the character, and a cell that
  carried nothing costs nothing; `_audit_parse` reports it once per article at
  WARNING. **Measured by the counter itself**: 355 cells in 8 of the 8,118
  served articles of `PMC10030002_PMC10040000.xml.gz` — 173 of them in 3
  articles inside a `<p>`, the only shape where the loss was ever visible, and
  the other 182 in 5 articles inside a `<glossary>`, where they were already
  being discarded, so there the counter reports a loss that is pre-existing and
  was silent — and **248,720 cells in 6,726** of the 97,909 archive
  articles of `oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26.tar.gz`. Every one
  is an `<array>`'s on both artifacts, so the log line names that as the
  measured cause and not as a claim about the document in hand. Nothing stored
  moves.

  **The counter is keyed on no builder being open, which is narrower than "no
  table received this cell".** An `<array>` deposited *inside* an open
  `<table-wrap>` routes into that builder instead, splicing a phantom row into
  a table the publisher never wrote that way and taking the silent branch. That
  is pre-existing and filed as issue #247 rather than fixed here; it measures 0
  of 8,118 served and 0 of 97,909 archive articles, so "every one is an
  `<array>`'s" describes what this arm has seen and not where an `<array>` may
  sit.

  **The two renditions disagree by roughly seventy-fold on this population** —
  0.1% of served articles against 6.9% of archive ones — and the two draws are
  different accession ranges, so rendition and corpus cannot be separated here.
  The served figure is the one that sizes the priority, being the bytes
  `FullTextService` is fed.

  **The counter and the deposit survey close on both artifacts.** Served, they
  agree outright at 355 in 8. On the archive the markup walk finds 251,362
  unwrapped cells, of which 2,141 are blank and 501 more, in 115 articles,
  carry their text only inside a child that takes a buffer and does not merge
  it back — so that text never reaches the cell's buffer. **Nor is it lost on
  either artifact**: every one of those children resolves to a `<p>`, whose own
  arm files the text (ahead of the enclosing sentence, which is #147's routing
  question and not a loss). That is a direction rather than a guarantee — a
  child with no arm at all, a `<list-item>` say, would be dropped here with
  nothing counted, and measures 0 on both. The remaining
  **248,720 in 6,726** is the counter, to the unit and to the article. A gap
  between two of bmlib's own counts is a defect in one of them until it is
  explained; this one was 2,642 wide before it was measured.

- **A footnote block's own heading and image are counted when they are
  dropped** (issue #238, filed by PR #237's review). #124 made an exhibit's
  footnote a destination, and two things deposited in the same block still
  reached nothing with no counter and no line: a `<table-wrap-foot>`'s or
  exhibit `<fn-group>`'s own `<title>` — *"Note:"*, *"Fontes:"* —
  refused by the `<title>` owner rule (#125, #130), and a `<graphic>` the
  footnote matter owns, refused by `_graphic_owner`'s opacity (#127). Both
  refusals are rules this module argued for, so **both drops stay** and each
  is counted and reported once per article at WARNING — `refused_apparatus_prose`'s
  rule, that a loss the module chose earns a line rather than excusing one.
  Folding the heading in as the block's lead, the shape #124 and #228 took for
  a marker and a term, is refused: a heading belongs to the *block* and the
  block is a list of notes, so the fold would either put a heading in the list
  as if it were a note or weld it onto the first note's marker and break the
  one `split` the #124 decision promises — a *wrong* value where the
  alternative is a blank, this module's standing preference. `docs/DECISIONS.md`
  records it.

  **Scoped by a deposit survey, and every exclusion has a home elsewhere.**
  The heading counter is keyed on the block's own `<title>` — parent
  `<table-wrap-foot>` or `<fn-group>` — *and* on the owner walk finding an
  exhibit, and the two guards keep different populations out: the parent a
  `<list><title>` inside a note (dropped by the same rule wherever the list
  sits), the walk every `<fn-group>` heading belonging to no exhibit — an
  unsectioned `<back>`'s, issue #231's population, and a sectioned one, which
  is #125's own residual and was dropped with no counter and no line until
  PR #239's review filed it as #240. The image counter is keyed on an owner
  that *is* the footnote matter, because of the 329 footnote-matter
  `<graphic>` in the archive artifact 319 (in 70 articles) are an
  `<inline-formula>`'s — issue #175's population, a formula deposited as an
  image — and 3 a `<boxed-text>`'s, against **7 in 4 articles owned by the
  `<fn>`**; an ancestor test would have pooled all three under this issue's
  name, and every owner outside the three sets is #244's residual. **It
  counts deposits**: an `<alternatives>` pair is one image in two encodings
  and reads 2, the unit the survey counts, and its line says *graphic
  deposit(s)* rather than claiming two images. **An empty deposit costs
  nothing on either counter** — `<title/>`, a `<graphic/>` with no href —
  the rule every sibling makes, since nothing was read and the line would
  state a loss that did not happen; the first cut counted both. The block's
  own `<label>` is still dropped uncounted and is #235's.

  **Measured by the counters themselves**: **0 and 0 over the 8,118 served
  articles** of `PMC10030002_PMC10040000.xml.gz`, the rendition bmlib is
  fed, and over the 97,909 archive articles of
  `oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26` 7 headings in 4 articles
  and 7 graphic deposits in 4 — every archive heading a
  `<table-wrap-foot>`'s reading *"Note"*, *"Note:"* or *"Fontes:"*, none
  empty. The *scoped* deposit survey and the routing tally agree to the unit
  on both counters; the issue's unscoped whole-document walk agrees for the
  image (7) and not for the heading (8). The same run re-reads
  `footnote_markers_dropped` at its recorded 0 on both artifacts. **Nothing
  stored moves**: the counters add two log lines and no value.
  Eighteen mutants, seventeen killed, each by the fixture written for it —
  the three guards on each arm (empty deposit, parent or owner test, exhibit
  walk), the abstract-exhibit route, `fn-group` refused as an owner, both
  block-set members, a double increment on each counter, the audit lines
  chained, cross-gated and removed, and the image line's unit reverted. The
  survivor widens the block set to the container set, which only an
  `<fn><title>` JATS does not admit could tell apart, and is documented as
  such rather than pinned.

  **PR #239's review found four surviving mutants and two false claims**,
  all fixed above — the audit lines were independent `if`s no fixture held
  both of, the abstract-exhibit route was exercised and unpinned, `fn-group`
  as an image's direct owner was unpinned, and four documents attributed the
  `<back><fn-group>` exclusion to the parent test, which cannot make it. It
  also found five defects older than this change, each verified by parse and
  filed rather than fixed here: a sectioned `<fn-group>`'s heading dropped
  uncounted (#240), a `<graphic>`'s `<alt-text>` welded into the surrounding
  prose (#241), `<inline-graphic>` having no handler at all (#242), prose
  around an inline `<table-wrap>` absorbing the cell text (#243), and a
  `<graphic>` owned by anything but an exhibit or its footnote matter
  dropped uncounted (#244).

- **An exhibit's footnotes reach the exhibit, and the marker with them** (issue
  #124). `JATSFigureInfo` and `JATSTableInfo` gain a `footnotes: list[str]`,
  filled from a `<table-wrap-foot>`'s `<fn>` prose, from an `<fn-group>` — which
  JATS admits in both exhibits and **neither artifact deposits inside one**, 0 of
  8,118 served and 0 of 97,909 archive, so that member is spec-driven and
  unexercised rather than observed — and from the loose `<p>` deposited after the
  last marked note. `to_html()` prints them as a `<div class="fn-group">`
  after the exhibit. Nothing collected them before: the `<p>` handler drops
  exhibit internals so a cell is not printed twice, which is right for a cell
  and wrong for a note, so a table's abbreviation expansions and its per-table
  funding and disclosure notes reached nothing at all.

  **The marker is folded into the note rather than modelled**, `"a — Adjusted
  for age."`. `<sup>` is an inline element flattened into the surrounding cell,
  so the rendered body still reads `12.3a` and with two footnotes the mapping
  back is otherwise unrecoverable — a reference to nothing, which is issue
  #116's *"a swallowed marker is not a blank"* one element down, and the shape
  issue #228 settled one container over for a definition's `<term>`. #116 is
  what discarded the marker, correctly, because there was nowhere to put it;
  there is now. The separator is measured on **this** population rather than
  borrowed: of the 16,947 footnote paragraphs the markup survey reads in the
  served artifact, **2**
  contain `" — "`, against 47 containing a spaced hyphen and 4,133 a colon —
  so the em dash collides an order of magnitude less often than either
  alternative, and a consumer wanting the marker separately can split on it.
  The issue priced this as *"a `footnotes: list[str]` on both, plus
  `to_dict()`/`from_dict()` and the HTML renderer"*; neither exhibit model has
  ever had those methods, so a third of the predicted work is not there.

  **Which exhibit a note belongs to is an ancestor question, and both halves of
  the walk are load-bearing.** `_owning_exhibit_footnote` walks outward from
  the closing element and answers with whichever it meets first. Stopping at
  the exhibit keeps a `<back><fn-group><fn>` — the article's own
  competing-interest statement, which issue #224 routes to the article — out of
  whatever figure happens to be open. Requiring the footnote container *before*
  the exhibit keeps an exhibit nested inside another's footnote from inheriting
  it, and that is the half the sibling Swift port got wrong: routed on a
  parser-wide footnote **depth**, the counter still stands at the outer table's
  depth while an inner `<table-wrap>` is parsed, so the inner table's own cell
  `<p>` takes the footnote branch and is rendered twice, once in the cell and
  once below it (bmlibrarian_lite#173). A depth cannot answer a question about
  the *innermost* exhibit. Nesting measures **0 in both artifacts**, so that
  half pins a direction rather than a population — the standing this module
  gives its other structural nesting rules.

  **The caption is asked first, and that ordering is a rule.** A `<fig>` or
  `<table-wrap>` opened inside a footnote ends the owner walk on its own, so
  the two destinations can only overlap under a caption-carrying element bmlib
  does not model — a `<supplementary-material>` or `<media>` inside an `<fn>`.
  Asking the footnote first would file that element's legend as the enclosing
  table's note, a *wrong* value where the alternative is a blank; asking the
  caption first keeps `_append_caption_text`'s standing rule unconditional,
  that text inside a caption belongs to that caption's owner and to nobody
  where the owner is unmodelled. Measured **0 of 8,118 served and 0 of 97,909
  archive**, so nothing stored moves and the rule is kept for what it prevents.
  Both orderings passed the whole suite until a fixture was written for the
  overlap — it was a surviving mutant, not a reasoned choice, and it is
  recorded as one.

  **A marker read for a note that deposits no prose is given back and
  counted.** Left pending it would fold into whatever footnote prose arrived
  next — one note's marker printed on another, silently — which is #228's own
  hazard one container over and takes the same remedy `_DefinitionFrame.term`
  takes at `</def-item>`. `footnote_markers_dropped` reports once per article
  at WARNING, the `rejected_spans` granularity. **The counter is wholly
  prospective and says so**: 1 of the 10,763 `<fn>` inside an exhibit in the
  served artifact carries no prose, and 11 of 137,735 in the archive, and every
  one of those twelve carries no marker either — so it reads **0** over both
  artifacts. A direction, not a rate, which is the standing the audit's own
  predicates are given.

  **Two counts of an `<fn>`'s `<label>` differ by scope, not by disagreement.**
  3,102 of the served artifact's exhibit footnotes carry a marker (39,349
  archive); issue #235's table records 5,891 served labels owned by an `<fn>`,
  which counts **every** `<fn>` including the back-matter and author-notes ones
  that belong to no exhibit. Both are right and each now names its element set
  — the `<aff>`-against-`<aff>`+`<corresp>` correction PR #236's review had to
  make, avoided in advance this time.

  **Blast radius, diffed against `main` over all 8,118 served articles of Europe
  PMC's `PMC10030002_PMC10040000.xml.gz`.** Notes appear in **3,707 articles
  (45.7%)** — 16,935 of them, 2.37 MB, of which **2** are on a figure rather
  than a table — and `html_content` moves in exactly those 3,707, so **a
  downstream holding cached full text must re-fetch**. Everything else is
  unmoved by construction and measured to be: prose, section titles,
  `abstract_sections`, figure and table captions, `references` and `has_body`
  move in **0**, and 0 paragraphs are gained or lost. The archive artifact
  agrees on shape and scale — **190,198 notes in 45,099 of the 97,909 articles
  of `oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26` (46.1%), 277 of them a
  figure's** — measured as a routing tally rather than diffed. The one counter
  that moves is `definition_terms_dropped`, **1,510 → 1,444 in 8 served
  articles and 10,394 → 9,468 in the archive**: a
  `<def-list>` deposited in a `<table-wrap-foot>` used to route nowhere and be
  counted as a loss, and it is now folded and filed like any other definition.
  That is the 66 the #228 entry below records as *"in a `<body>` float"*, and
  it is this change's population rather than that counter's now.

  **Two instruments were wrong, and each was caught by a count that would not
  close.** The blast-radius harness read `f.footnotes` unguarded on both sides,
  so **6,961 of 8,118 `main` rows errored** and the join reported them as
  errors rather than as a diff — visible only because the error column was
  non-zero, which is the harness-is-an-instrument lesson the #224 and #228
  entries below both record. And the markup survey that sized the population
  **over-counts by 12**: a footnote may deposit a `<def-list>` inside a `<p>`,
  and a subtree walk counts that wrapper once as itself and again as its
  definitions, whose text is all it has. The parser's own tally — 16,935 — is
  the honest one, which is why it is quoted above and the survey's 16,947 is
  quoted only for the separator, a question about the deposit rather than about
  the routing. The 12 are where #228's fold is visible inside a note:
  `"AICc — Akaike information criterion adjusted for small sample size"` where
  the subtree walk reads `"AICcAkaike information criterion…"`.

  **Mutation: 15 mutants over the walk, the fold, the two arms, the container
  set and the renderer.** The first sweep left six survivors and every one was
  a fixture gap rather than dead code. Three are worth carrying. A
  `<fn-group>`'s own heading — *"Notes"*, *"Abbreviations"* — cannot be
  separated from a note's marker by any fixture where the note carries a marker
  of its own, because the note's `</label>` overwrites the leaked heading before
  prose arrives; only an unmarked note inside a headed group separates them.
  The unspent-marker guard has the same shape one arm over: a second note
  carrying its own marker masks the leak. And `<fn-group>`'s membership in the
  container set is invisible wherever a `<table-wrap-foot>` or an `<fn>` is also
  in the walk's path, so it takes a loose `<p>` in a *figure's* group to pin it.
  Two more: `</fn>` asks the walk with the closing element included, which only
  a figure's `<fn>` can show, since a table's has `<table-wrap-foot>` above it
  either way; and the caption-first ordering above. All 15 die now.

  **Review of PR #237 found two live defects the sweep could not reach, and
  both were in what the walk did *not* ask.** A cell did not end the owner
  walk, so an `<fn>` deposited inside a `<td>` set the container flag and the
  walk carried on outward to the `<table-wrap>` — while `characters()` had
  already written that text into the open cell, `append_cell_text` being gated
  on `in_cell` alone. The note was then rendered **twice**, in the cell and
  again in the footnote block, in the string `FullTextService` caches. That is
  bmlibrarian_lite#173's own symptom reached by a different route, and the
  exact invariant the `<p>` branch's own comment claimed unconditionally. And
  the `</label>` arm assigned `pending_footnote_label` directly, so a second
  `<label>` in one `<fn>` displaced the first marker with nothing counted, and
  an **empty** one erased a good marker outright — `""` is the slot's absent
  spelling, so `</fn>` found nothing to give back either and the note rendered
  unmarked against a body still reading `12.3a`, silently. That is the `<term>`
  arm's defect forty lines down in the same method, whose comment already names
  the rule: *"a rule resting on a remembered content model is the rule this
  module keeps being caught by"*. Both populations measure **0 of 8,118 served
  and 0 of 97,909 archive**, so each pins a direction, which is the standing
  this entry already gives its nesting and caption-order rules.

  **And the documented way to recover a marker did not work.** `" — "` is also
  `_DEFINITION_SEPARATOR`; issue #228's fold runs first, so a `<def-list>` in a
  `<table-wrap-foot>` emits `"BMI — body mass index"` with no marker at all.
  Measured on what the parser **emits**, which is the population the advice is
  about: **68 of the 16,935 notes, in 10 of the 8,118 served articles**. The
  separator choice stands — it was measured on the deposit and is right there —
  but `models.py` and the manual told a consumer to split unguarded and now
  say not to. The marked-and-folded case, ambiguous either way, measures 0.

  **Four further mutants survived the whole suite**, each moving a stored value
  or a counter: taking the *outermost* exhibit rather than the innermost, which
  is the walk docstring's own headline claim; answering with the wrong exhibit
  *kind* where a figure and a table are both open; counting a note towards
  `body_paragraph_count`, which flips `has_body` on an exhibit-only body and
  would end the retrieval chain on an article with no prose; and reversing the
  mirror's branch order in `_prose_reaches_output`, which under-reports a real
  formula loss. Nine tests close them and the two defects above; all twelve
  mutants die. Six documentation figures were corrected at the same time,
  including four sites quoting the superseded survey count as the parser's own
  tally, and issue #238 records what an exhibit footnote still loses in
  silence.

- **A partly-answered posted-results check is no longer stored as a finding**
  (issue #206). `TrialResultsStatus.PARTLY_ANSWERED` is the sixth member, and
  the schema addition is free rather than cheap: `trial_results_status` is
  itself unreleased (issue #198), so this rides the recompute issues #184 and
  #194 already force. After the release it would cost a second one.

  `_check_trial_registration` walked the paper's ClinicalTrials.gov accessions
  and set `answered` on the **first** one that replied, so a single reachable
  *"no results"* outvoted any number of unreachable ones. And
  `MAX_TRIAL_IDS_TO_CHECK` sliced an unbounded list — `_parse_pubmed_signals`
  collects every ClinicalTrials.gov accession that is a well-formed NCT id —
  with no log line, no indicator, and no test of the analyzer's truncation
  (`tests/test_api_failure_sampler.py` did reference the constant; the wider
  claim first written here was false, PR #225's review). Either
  way `"Registered trial without posted results"` was stored about a paper
  whose remaining accessions bmlib had never reached, and one of those may be
  the trial with results. That is issue #194's class of false claim, narrowed
  by PR #195's tri-state rather than closed by it — the same shape as
  correcting the `User-Agent` narrowing #194 without making the `bool` honest.

  **The cap and the unanswered accession are one question**, so they are one
  member: both mean *"bmlib did not ask about every accession"*, and while
  either is true the finding has not been earned. They share
  `_INDICATOR_RESULTS_NOT_CHECKABLE` for the reason `REQUEST_FAILED` and
  `NOT_CHECKABLE` already share it — the claim a human can act on is
  identical and it puts nothing in ClinicalTrials.gov's mouth. Which cause it
  was reaches an **operator**, not a stored field: raising the cap is an
  action. *"Would re-running change this?"* — the question that does earn a
  member elsewhere — **does** discriminate here, `no` for the cap and `yes`
  for an accession that did not answer, and the first statement of this
  reasoned about the cap alone and generalised to the member, which is issue
  #191's own defect (PR #225's review). What rules a split out is that the
  walk computes `unestablished = dropped + asked - answered`, a **sum**: one
  paper can have both causes at once, so splitting would need three members or
  a second field. The residual — a downstream cannot ask *"retry, or change
  the config?"* of the stored value — is filed rather than argued away.

  **It sits on the unanswered side of the partition, and that is the
  load-bearing half.** `trial_results_compliant` is what both known
  downstreams render; `False` under `is_answered` `True` reads as *"the trial
  fell short"*, which is the unearned sentence issue #198 exists to stop
  being published.

  **The measurement decided which half mattered, and reversed the issue's own
  emphasis.** Over the 30 papers naming an accession in the 2026-09-08
  trial-enriched sampler draw, a partly-answered check is **1 of 30** and the
  cap truncates **8 of 30** — so the half that was entirely silent is the
  larger one. Read the 1 as a **floor**: that draw predates PR #213's
  correction of `TrialCheck.answered`, which counted HTTP 200 where bmlib
  counts a non-`None` return and so deflated exactly that row, while the
  truncation count derives from `found > probed` and is unaffected — which is
  why the comparison rests on the 8 (PR #225's review). The cap still bounds
  the requests; a truncated walk that did not find posted results now WARNs,
  naming how many accessions were skipped, what the cap is, and **which
  accessions they were** — the last being what raising the cap recovers, and
  what gives the line a subject. That line is gated on the walk not having
  concluded `POSTED`, because a posted result settles the paper and the
  accessions behind it cost nothing — and only the **cap** gets a line, four
  of the five ways an asked accession fails to answer already having one from
  `_request`. The fifth does not: `_json_bool` refuses a wrong-typed value in
  silence, which is issue #226 (issue #209's residual at the one site where it
  decides a stored status), and the comment claiming universal
  coverage is narrowed rather than the absence licensed by a false premise.
  How far the accession-count distribution runs past three is still
  unmeasured; `scripts/sample_api_failures.py` records each paper's count
  before the cap, so a run would answer it with one more report line.

  **PR #225's review found six further defects in this change and in the one
  below, all fixed here.** A **repeated accession** made the cap report a
  truncation that lost nothing and retract a finding ClinicalTrials.gov had
  made: `<DataBankList>` is `(DataBank+)`, so one paper naming one trial twice
  is well-formed input, and `_parse_pubmed_signals` collected the entries
  verbatim where the funders tuple twelve lines below already deduplicated and
  `_find_trial_ids` has since issue #202. Deduplicated at the parser, so the
  sampler's own truncation count is not inflated by repeats either. The
  **book branch** matched a `<PubmedBookArticle>` anywhere and was tried
  first, so a legal mixed set reported an article record at DEBUG on the
  strength of its neighbour — children now, and every child. Four mutants
  survived the whole suite and now die: deleting `root.tag ==
  _PUBMED_RECORD_SET_ROOT` (which let an empty `<eFetchResult/>` be reported
  as an empty `PubmedArticleSet` NCBI never sent), emptying
  `_PUBMED_SIGNALS_LOST`, passing `""` for the `pmid` at the one call site
  (so the whole point of the signature change was unpinned end to end), and
  substituting `len(ct_ids)` for `dropped` in the new WARNING. The first cut
  of the constant's own test asserted `_PUBMED_SIGNALS_LOST in message`, which
  the mutant satisfies vacuously — this repository's *"a log assertion must be
  unique to the line"*, turned on the assertion written to enforce it.
  `PARTLY_ANSWERED` gained the `analyze()`-level test every other member has,
  and `is_answered` gained the generic partition guard its `FullTextStatus`
  twin has carried since issue #161. Three stale counts and the misdated
  figure above were corrected in the same pass.

  **What moves:** for a paper whose results check was partial,
  `trial_results_status` moves `NOT_POSTED` → `PARTLY_ANSWERED` and
  `risk_indicators` swaps *"Registered trial without posted results"* for
  *"Trial registration found; posted-results status could not be checked"*.
  **No score moves** — neither indicator feeds the score, which a test
  asserts rather than reasons — so a reader diffing stored results should see
  no number change. One existing test asserted the reverse and is reversed
  with a comment saying so: it pinned the reading `answered = True` on the
  first reply encodes, which is what the issue is about.

- **The sampler probes the address it categorises** (issue #216, from PR
  #213's review). `scripts/sample_api_failures.py` only — no library code and
  nothing stored moves; the deliverable is the measurement, and it is what
  licenses the issue #188 fix below.

  That script categorised how bmlib *would* address each record's full text
  and never built `{EUROPEPMC_REST_BASE}/{accession}/fullTextXML`. So the
  finding issue #188's remedy rests on — *"the bare `id` 404s, three of
  three, against a `pmcid` address serving 53 kB"* — was a spot check quoted
  in four files beside a committed table that could not produce it, and the
  404's own DEBUG level rested on a hand-taken 200-probe draw in the same
  position. `europepmc_fulltext` is now the sixth endpoint: one request per
  record that offers an address, ~50 on the default draw.

  **What the script probes and what bmlib asks are two names.** They
  coincided until issue #188; a table keyed on what bmlib *asks* would then
  stop measuring the very thing that licensed the refusal, so
  `PROBED_CATEGORIES` follows the record's own offer and `id-not-an-address`
  stays a probed row.

  **Two cross-tabulations over one set of probes**: by address category, with
  both `id`-fallback categories split by `source` (issue #188's own split);
  and by `isOpenAccess`, which bmlib does not read but issue #188's second,
  larger population turns on. Recorded, not acted on — a gate narrowed on a
  floor silently loses an article that would have been served.

  **`id-only` is gone and did not become either new name.** It counted every
  record addressed by its bare `id`, which issue #188 splits into the
  accession that serves and the PMID that cannot; keeping the name for either
  half would have made a published figure mean something else without
  changing, which is this repository's own scar. Any figure quoted against
  `id-only` predates the split.

  The full-text body's shape is deliberately two values, `served` and
  `empty` — everything past that in `_fetch_europepmc_fulltext` is a
  *judgement* about the document, and an instrument does not import the
  predicate under test. And that endpoint keeps its shape table under a rule
  of its own: `shapes_reportable`'s *"a probe that reached no body is as
  uninformative as a throttled one"* was written for five endpoints at which
  a non-200 is close to unheard of, and here the 404 is the finding. That was
  the instrument's own first run reporting ERROR and flipping the exit code
  on a clean draw; the rule is withdrawn for that endpoint by a named set and
  for nothing else.

  **First run of the committed code** (2026-09-09, 123 + 60 records at the
  documented defaults; the run before it, on the pre-fix instrument, is what
  exposed the shape-rule defect above): `europepmc_fulltext` 52 probed,
  **46 not served (88.5%, all 404)**; 6 bodies served, **0 empty**, which is
  issue #190's population measured for the first time at 0 of 6.

  **That 88.5% is over every address *offered*, and is not the 404 branch's
  own denominator** — 43 of those 46 are addresses the issue #188 entry below
  stops bmlib sending. Over the addresses it still sends the same draw
  measures **3 of 9** not served, `[12.1%, 64.6%]`, and the two intervals do
  not overlap. The report prints that population as its own row since PR
  #219's review; before it the pooled share was the only served share on the
  page and four documents quoted it as the level's evidence.

  **PR #219's own review found six more.** The pooled share above is the
  largest and is corrected in place. Beside it: `_EUROPEPMC_ACCESSION_RE`
  folded no case, and the endpoint is case-insensitive — `pmc4154587` and
  `ppr1301373` each serve 200 with bytes identical to the uppercase form
  (2026-09-09), so a case-sensitive test refused an address that *serves*,
  which is the failure that guard's own comment calls worse than the request
  it saves; `FullTextStatus.NOT_ATTEMPTED`'s docstring still enumerated three
  causes and named the WARNING of the one nearest the new one, in a file the
  diff did not touch; `AddressProbe.served` meant HTTP 200 where
  `_fulltext_kind` splits a 200 into served and empty, so the address table
  and the shape table reported one probe two ways (0 empty of 6, so nothing
  published moved); a probed category no record offered now prints
  `NO POPULATION HERE` rather than vanishing, `id-accession` having drawn
  nothing; three live guards survived mutation and now die; and the test
  helper built an `id-accession` carrying `"ID-1"`, a state
  `_addressability` cannot produce, which `RecordAddressing` now refuses.
  Filed rather than fixed: issues #220, #221, #222 and #223. The address rows
  are quoted under the issue #188 entry below. Two further readings ride
  along: `pubmed_efetch` served `no-citation` for **50 of 60** bodies — the
  one silent branch of that step, now issue #218 — and `crossref`'s `funder`
  key was absent in 71 of 73 records, so the funder coercers issue #199 added
  are reached by a small minority of bodies.

- **A stored result now says what happened, once per claim** (issues #198,
  #202 and #203, all three from PR #195's review).
  `TransparencyResult.trial_results_status` is a new field carrying a new
  public enum, `TrialResultsStatus`; `risk_indicators` gains a provenance line
  and loses two indicator strings.

  **The posted-results flag was a bare `bool` for four claims** (issue #198).
  `trial_results_compliant` is `False` when ClinicalTrials.gov said no results
  are posted, when every request for the paper's accessions was refused, when
  the registration is in a registry ClinicalTrials.gov has no answer for, and
  when there is no registered trial at all. `risk_indicators` distinguishes
  three of those in prose — but prose is a list a downstream string-matches,
  and both known downstreams render the flag instead, which is how issue #194
  published *"Registered trial without posted results"* about every registered
  trial for a release.

  `TrialResultsStatus` is `FullTextStatus`'s argument (issue #161) one endpoint
  over: `POSTED` / `NOT_POSTED` are ClinicalTrials.gov's own answers,
  `REQUEST_FAILED` is nobody answering, `NOT_CHECKABLE` is a registration it
  has no answer to give for, and `NOT_REGISTERED` is nothing to ask. The
  grouping to branch on is `is_answered`, mechanised as a partition over two
  named sets so a member added later must choose a side. The last two share an
  indicator string and stay separate members because *"would re-running change
  this?"* differs, which is what results being cacheable makes worth storing.
  The flag stays as the compatibility field, `__post_init__` holds the pair to
  agreeing, and `None` means *not recorded* — no path this version writes
  leaves it unset, so `None` keeps meaning *legacy row*.

  **One indicator string carried two claims, and the retraction took both**
  (issue #203). Three branches wrote *"COI disclosure status unknown (…)"*
  whose parenthetical said what became of the full text — *"(full text
  unavailable)"*, *"(full text served but not usable)"*, *"(EuropePMC lookup
  failed)"* — and all three were in `_INDICATORS_RETRACTED_BY_PUBMED_COI`. A
  PubMed `<CoiStatement>` refutes the COI half and says nothing about the
  other, so a result could reach HIGH with a tier downgrade whose only
  human-readable line was a COI **success**, up to 30 points missing and
  nothing saying why. That is issue #193's own complaint, reintroduced through
  the retraction set.

  The issue names the outage branch; the other two have the same shape and the
  same consequence, so all three are split. The COI claim is now one line,
  *"COI disclosure status unknown"*, and what became of the full text is a
  **provenance line keyed on `FullTextStatus`** — one per member, appended
  once after every step has run, which puts it structurally beyond the
  retraction rather than merely absent from its set. Keying it on the enum
  also makes the prose as precise as the machine-readable half: *"(full text
  unavailable)"* was a claim about Europe PMC and false for `REQUEST_FAILED`,
  issue #191's defect surviving in the prose. Every member must have a line or
  be a named exclusion (`ANALYZED` is the exclusion), which
  `test_every_status_says_what_happened` pins — and the lookup is
  **subscripted**, so a member listed in neither collection raises rather than
  costing the result one line in silence.

  `NOT_ATTEMPTED`'s line says only that **no full-text request was made**. It
  read *"EuropePMC holds no open-access full text for this article"*, which
  that member's own third cause — a record carrying `inEPMC == "Y"` and no
  address for the text — contradicts outright, so the fix had reintroduced
  issues #187/#190/#191's defect in its own prose half. Keyed on the enum the
  line can only be as precise as the member, so a member covering three causes
  gets the claim true of all three; splitting the third out is issue #207.

  **This moves stored `risk_indicators` for every analysis that did not scan
  full text** — a much larger population than the outage case, since it
  includes every closed-access paper: the COI line's text changes and a
  provenance line is added. A downstream matching either string has to be
  updated, and should read `full_text_status` instead.

  **A failed Europe PMC search was re-issued** (issue #202). `_find_trial_ids`
  documented that it reuses the record `analyze()` already fetched *"so the
  same search is not issued twice per document"*, and decided that with
  `if data is None` — which is exactly what a **failed** search returns. So
  during an outage the identical failing search went out twice, and since the
  failure gained a log line in PR #195, an operator counting Europe PMC
  failures double-counted every document. Rather than thread a sentinel
  distinguishing the two `None`s, the fallback is deleted: `_find_trial_ids`
  is a module-level function over the record, with no client, so the promise
  is structural — a trial id scraped out of an abstract bmlib never received
  is not a thing that can happen. PubMed's `<DataBankList>` accessions are
  untouched, so a record with a structured accession still gets its results
  check during an outage. `scripts/sample_api_failures.py` follows it to
  module level and loses the analyzer instance it held only for this.

- **Every dropped API response now leaves a line, and an outage no longer
  looks like an answer** (issue #193, from PR #192's review, plus issue #194,
  found while measuring for it). `FullTextStatus.SEARCH_FAILED` is a new
  member; `TransparencyAnalyzer` gains a `_request` helper the five
  request-making helpers share.

  Five methods — `_query_crossref`, `_query_europepmc`, `_query_pubmed`,
  `_query_openalex` and `_check_trial_results` — each wrapped their request in
  `except Exception` → `logger.debug` → `return None`. Two silences of
  different kinds:

  - a **`_BUG_TYPES` member held at DEBUG**, which is issue #187 unfixed in
    five more places: a `TypeError` from a client that is not what the code
    assumes is bmlib being wrong, kept at a level nobody enables;
  - **a non-200 falling off the end with no line at any level.** Not a level
    problem but an absence — the `except` catches only raises, so a 429, a
    503 or a 403 simply reached `return None` and there was no DEBUG line for
    an operator to turn on.

  `_query_europepmc`'s copy is the one that gated PR #192's whole fix.
  `analyze()` calls `_check_europepmc` only when the search returned a record,
  so in a Europe PMC outage the search 503s, the full-text step is never
  reached, and the result stores `NOT_ATTEMPTED` — documented *"No request was
  made"* — at HIGH with `tier_downgrade_applied`, out of zero log lines.

  **`SEARCH_FAILED` splits that case out.** `NOT_ATTEMPTED` says no request was
  made *and Europe PMC's own answer is why*: it never claimed to hold full
  text, or it answered with no record. During an outage it answered nothing,
  so the old value put a claim in Europe PMC's mouth — issues #187/#190/#191
  exactly, one step up the call chain. The new member is not a refusal
  (nothing was served) and `test_every_status_chooses_a_side` is what made it
  pick a side. `is None` rather than falsiness decides it: a 200 carrying an
  empty object *is* Europe PMC answering, and that stays `NOT_ATTEMPTED`.
  Beside it, a fourth COI indicator — *"COI disclosure status unknown
  (EuropePMC lookup failed)"* — goes into `_INDICATORS_RETRACTED_BY_PUBMED_COI`,
  the set written for exactly the hazard of a fourth line being added to the
  appending site and not the retracting one. (Issue #203, above, then split
  that line in two: the parenthetical is a claim a `<CoiStatement>` does not
  refute, so retracting the whole of it took the outage off the record.) `risk_indicators` carried **no
  COI line at all** on this path, so a HIGH verdict with a downgrade carried no
  stated reason for the finding that drove it. (Not "was empty": CrossRef and
  PubMed can both append on the same path, and the reproduction recorded above
  is one in which CrossRef answered — corrected in PR #195's review.)

  **ClinicalTrials.gov has been refusing bmlib since it first asked** (issue
  #194). `analyze()` sets a `User-Agent` on its client, overriding httpx's,
  and that header is refused at ClinicalTrials.gov's edge with a bare 134-byte
  `403 Forbidden` page. `_check_trial_results` returned `False`, which in a
  `bool` is indistinguishable from *"this trial posted no results"* — so
  `SCORE_RESULTS_POSTED` (15) had never been awarded to any paper,
  `trial_results_compliant` was `False` on every result, and *"Registered
  trial without posted results"* was stored as a false claim about every
  registered trial analysed. Measured 2026-09-06 over thirteen header shapes:
  only the five carrying the token `python-httpx` serve 200 — `curl/8.7.1`,
  `python-requests/2.31.0`, `Python-urllib/3.11`, `Go-http-client/2.0`,
  `PostmanRuntime/7.37.0` and a browser string are all refused, and six
  alternating rounds of bmlib's header against httpx's default gave 403/200
  six times of six. `_user_agent` now **appends** httpx's own token to bmlib's
  identification rather than replacing it: CrossRef and NCBI both ask a caller
  to say who it is, and it is not a fiction — bmlib *is* httpx here. That is a
  live-only property **no test can hold**, which is why the whole suite missed
  it; `scripts/sample_api_failures.py` is the guard.

  **The levels are measured, and the measurement is what earns them.** The new
  sampler draws 180 records stratified over source × year (MED/PMC/PPR ×
  2024/2014/2004, preprints 2024/2019/2014) and 60 more for the
  separately-drawn trial population, addressing and heading every request
  exactly as this module does. It read **0 non-200s at all five endpoints** —
  CrossRef 0/73, Europe PMC search 0/180, PubMed efetch 0/60, OpenAlex 0/73,
  ClinicalTrials.gov 0/53, upper bounds 2.1%–6.8%. So no status is the
  ordinary outcome anywhere here, every non-200 warns, and the five
  `_ORDINARY_STATUSES` sets are empty **as a measurement** rather than as a
  default — pinned by `test_no_endpoint_claims_an_ordinary_status_today`, with
  the mechanism itself exercised separately so five empty sets are not untested
  wiring. The contrast is `_fetch_europepmc_fulltext`'s 404, where 81 of 81
  non-200s were 404, and the 404 is separately the majority outcome of that
  module's own gate (88 of 150 in a stratified draw) — two denominators, both
  needed, neither implying the other; over the 200-probe draw the 81 are
  40.5%. That is what earns DEBUG. Read the zeroes as upper bounds, not
  as proof: the population is *identifiers bmlib is handed*, which come from
  indexed records, and a caller passing an invented DOI is outside the draw.
  The ClinicalTrials.gov row is also the live confirmation of #194: 53 of 53
  serve under the corrected header, against **ten probes** on the old one — six
  alternating rounds and four accessions, all 403. Those ten are the whole of
  what was measured on the old header; an earlier draft said "the same draw
  against the old header is 403 for every probe", which claims 53 probes
  nobody took and which the sampler has no switch to take (PR #195's
  review).

  Levels: **ERROR** for a `_BUG_TYPES` member, with `exc_info`, since that can
  only mean bmlib is wrong (`jats_parser`'s level for the identical claim);
  **WARNING** for a raised request, for a non-200, and for a 200 whose body
  will not decode — the last being the remote's failure and not ours, which is
  the side `_BUG_TYPES` deliberately puts `ValueError` on, `json.JSONDecodeError`
  being one. Nothing re-raises: `analyze()` wraps none of these steps, so each
  must swallow its own request or one dead API costs the analysis.

  Two smaller corrections ride along. `_check_trial_results` used to reach
  `.get()` on a JSON body that might not be an object, so a list came back as
  `False` — a *finding* — through an `AttributeError` swallowed at DEBUG; it
  now tests the type. And the CrossRef, OpenAlex and ClinicalTrials.gov URLs
  are module constants (`CROSSREF_WORKS_URL`, `OPENALEX_WORKS_URL`,
  `CLINICALTRIALS_STUDY_URL`) beside `EUROPEPMC_REST_BASE`, together with the
  `Accept` header (`JSON_ACCEPT_HEADERS`), so the sampler provably probes what
  the analyzer requests rather than a restated literal — which is how issue
  #184 lived a whole release.

  **PR #195's review found four things the above did not reach, and they are
  part of this entry rather than a follow-up.**

  *Correcting the header did not make issue #194 honest.* It narrowed the
  false claim from *always* to *whenever ClinicalTrials.gov does not answer*,
  because `_check_trial_results` still returned a `bool` and its caller turned
  `False` into `"Registered trial without posted results"` — a persisted claim
  about the trial — for a 404, a 403, or a body that would not decode.
  Reproduced three ways: one accession 404ing, and a two-accession mix in
  which the refused one is the one *with* results. It is now a tri-state
  (`True` / `False` / `None`), and the caller distinguishes three outcomes:
  results posted, asked-and-answered-no, and **not one accession answered**,
  which gets `"Trial registration found; posted-results status could not be
  checked"` — the line a registration in another registry already gets,
  because the claim is identical and it puts nothing in ClinicalTrials.gov's
  mouth. **This moves stored values again**: a paper whose ClinicalTrials.gov
  requests all fail now carries the second indicator instead of the first.
  `TransparencyResult.trial_results_compliant` is still a bare `bool` at this
  point, so it is `False` for both — recorded in `docs/DECISIONS.md` as a
  deliberate residual, and taken as issue #198 above, in the entry at the top
  of this section.

  *The fix for issue #187 was reproduced, one layer above itself.*
  `_request_json` and `_request_text` each kept a bare `except Exception`
  around the decode and WARNed unconditionally, so a response object bmlib was
  wrong about — no `.json` at all — printed as *"CrossRef answered 200 with a
  body that is not JSON"*: a `_BUG_TYPES` member dressed as a claim about the
  remote, which is exactly the defect `_request` was given the two-level split
  to prevent. All three sites now share one `_report_swallowed_exception`,
  because the second copy of a rule is the second place to get it wrong.

  *PubMed was the one endpoint of five whose unusable 200 stayed invisible.*
  `_request_text` returns `""` for a 200 carrying nothing and `_check_pubmed`'s
  falsy test could not tell that from a request already reported, so an empty
  body was dropped in silence; a body that was not parsable XML logged at
  DEBUG, the level `_request_json`'s own docstring argues "names the wrong
  stage". Both WARN now. Not cosmetic: empty signals mean no `<CoiStatement>`,
  so nothing in `_INDICATORS_RETRACTED_BY_PUBMED_COI` is retracted and the
  missing-COI downgrade can fire — issue #193's own justification, applied to
  the path it did not take.

  *The non-200 line claimed a consequence it could not know.* It ended "that
  component is not scored", which was true for CrossRef and OpenAlex and wrong
  for the rest — a refused ClinicalTrials.gov request *manufactured* a scored
  finding, and a failed Europe PMC search gates the whole full-text step. The
  shared helper now reports the request only, and `analyze()` states what the
  outage cost, naming `document_id` and the points.

  And several comments were corrected against the code they describe. The
  unreachable-API early return still said `NOT_ATTEMPTED` was the only status
  reachable there, which this very change falsified and its own test refutes —
  dangerous less as a wrong claim than as a licence to substitute the literal
  and reinstate #193 on the one path the issue opens with. `_request`'s
  reachability comment said the flag used to be set *after* the body was read,
  which `git show main` refutes: all four helpers already set it before.
  `is_refusal`'s docstring enumerates the non-refusal side by hand and was not
  updated when `SEARCH_FAILED` joined the frozenset, while the manual was —
  now pinned by `test_the_docstring_names_every_non_refusal`.

- **`scripts/sample_api_failures.py` measured a request bmlib does not make,
  and its draw was not the one its numbers describe** (PR #195's review). Four
  corrections, all of which weaken or move the evidence the log levels rest
  on:

  - **Two of five endpoints were probed bare.** `probe()` had no `headers`
    parameter, so CrossRef and OpenAlex never received the
    `Accept: application/json` the analyzer sends — the same class of error as
    issue #194, one header over. And `_search_params` added a `pageSize` the
    analyzer never sends, on the endpoint whose failure gates issue #193's
    whole fix, while its docstring claimed to be "in the shape
    `_query_europepmc` sends". The draw's page size is now `_draw_params`,
    where it belongs to the draw.
  - **The client was not the analyzer's.** `timeout=45.0` and
    `follow_redirects=True` against the analyzer's 15.0 and httpx's default
    `False`, so a 3xx — which `FullTextStatus.REQUEST_FAILED` names explicitly
    as an outcome — was a 200 to the sampler and a dropped response to bmlib,
    and a 20-second response was a success here and a `ReadTimeout` there. The
    script could not observe the one status class the module documents.
  - **The default draw was 144 records and every table says 180.** `150 // 9`
    is 16, the invocation that produced 180 was recorded nowhere, and a reader
    re-running the documented command got a third number. `DEFAULT_TARGET` is
    180, the per-stratum count rounds up as `sample_jats_exhibits.py`'s does,
    and the default is now the committed draw.
  - **The trial population could be reshaped in silence.** `probe_trials`'
    efetch *builds* the ClinicalTrials.gov population rather than measuring
    it, and a non-200 there printed nothing, counted nothing and could not
    reach the exit code — so the headline share could not be told from a
    population thinned by NCBI throttling, which is the bias `trial_ids_for`
    exists to balance. It is reported and counted now, and `main` exits
    non-zero on it.

  Two type invariants close the rest. `ProbeOutcome` refuses an outcome
  `probe()` cannot produce — the test file was building two of them, including
  a success carrying no status — and `DrawnRecord` refuses a record with
  neither identifier, which `probe_record` used to turn into the literal
  `EXT_ID:None` and probe into the Europe PMC denominator, a request bmlib
  would never make entering the population that sets that endpoint's level.
  The headline label reads "not served" rather than "non-200", since the count
  includes exception outcomes that carry no status.

  **What it costs a downstream.** A stored result computed during a Europe PMC
  outage read `not_attempted` and now reads `search_failed`, and carries a COI
  indicator it did not carry before; `risk_indicators` is persisted, so that is
  a visible change. A downstream branching on the member rather than on
  `is_refusal` has to widen. **The #194 half moves scores**: any paper with a
  registered trial that has posted results now gains `SCORE_RESULTS_POSTED`
  (15) and loses the *"Registered trial without posted results"* indicator, so
  `transparency_score` and `risk_level` can both move, in the favourable
  direction. **Any downstream holding stored transparency results for papers
  with registered trials should recompute them.** How many papers that is has
  not been measured.

- **An attempt that got no answer now says so, instead of blaming Europe PMC**
  (issues #187, #190 and #191, all three from PR #185's and PR #189's reviews).
  `FullTextStatus.REQUEST_FAILED` is a new member, and `NOT_SERVED` narrows to
  exactly the HTTP 404.

  `NOT_SERVED` is documented as *"Requested and not served"*, and since #161 it
  is determinate, machine-readable and persisted — so each of three outcomes
  reaching it stored a claim about Europe PMC that Europe PMC never made:

  - a **bmlib defect** on the request line — a `TypeError` from a client that
    is not what the code assumes, an `httpx.InvalidURL` from an `ext_id` that
    arrived as a non-string — swallowed at DEBUG and filed as a Europe PMC
    absence (#187);
  - a **429, 503 or 403**, stored identically to the 404 whose ordinariness is
    the entire measured basis for the DEBUG level. `cache_results` defaults
    `True` and there is no retry or `Retry-After` handling anywhere in the
    package, so an outage window cached a corpus of absences indistinguishable
    from legitimately closed-access papers, each having silently lost up to 30
    points (#191);
  - an **empty HTTP 200 body**, which reached the *entirely nested* branch —
    `_strip_nested_articles("")` returns `""`, falsy but not `None` — and
    stored `ENTIRELY_NESTED`, an `is_refusal` outcome, logging the
    self-contradictory *"is entirely nested articles (0 bytes served)"*. The
    caller then persisted *"COI disclosure status unknown (full text served
    but not usable)"* for a response that carried no document: #161's own
    class of stored dishonesty, one branch it did not reach (#190).

  `REQUEST_FAILED` is the honest answer to all three — the attempt produced no
  document **and Europe PMC did not say it holds none**, which is what a 404
  says and nothing else here does. Both are `is_refusal` `False`, nothing
  having been served, so the two paths that used to read `NOT_SERVED` keep
  the *"full text unavailable"* indicator unchanged. **The empty body does
  not**, and that is the point of #190 rather than a side effect: it used to
  read `ENTIRELY_NESTED`, whose `is_refusal` is `True`, so it carried *"full
  text served but not usable"* — a claim about a response that carried no
  document. `risk_indicators` is persisted, so this is a stored-value change
  a downstream can see. ("The indicator is unchanged for every one of them"
  stood here until PR #192's review; it was true of two paths of three.)
  `test_every_status_chooses_a_side` is what forced the new member to pick a
  side rather than defaulting into the wrong one.

  **Measured, and each number names its draw.** 200 live probes of
  `fullTextXML` on 2026-09-05, stratified by source (MED/PMC/PPR) and
  publication year and addressed exactly as `_check_europepmc` addresses them
  — a cursor page being a contiguous block, an unstratified draw of
  `IN_EPMC:Y` read 48% 404 where a stratified one read 3% (PR #189). 119
  served, 81 non-200, and **81 of the 81 were 404**: so the DEBUG level is now
  measured over the whole of what its branch takes, where before it
  generalised past its own draw, and a non-404 is the ordinary outcome of
  nothing — 0 of the 81 non-200s, which is the eligible denominator (the
  other 119 served, so they could not reach that branch); a floor rather than
  a proof that Europe PMC never emits
  one. Of the 119 served, **0 carried an empty body**, the smallest being
  2,622 bytes and the median 85,925, so #190's fix is carried by the branch it
  was landing in being wrong for it rather than by a rate. Whether Europe PMC
  ever serves an empty 200 is **not measured**; the local corpora cannot
  answer it, the importer that built them having possibly discarded failures
  (#183's caveat).

  Levels, each argued at its branch: DEBUG for the 404 alone; WARNING for a
  non-404, a raised request and an empty body; **ERROR** for a request raising
  a `_BUG_TYPES` member, which can only mean bmlib is wrong — the level
  `fulltext/_parse_audit.py` fixes for the identical claim. It **does not
  re-raise**, which was #187's other option: every network step in the module
  swallows its own request so one dead API cannot cost an analysis (`analyze()`
  itself wraps nothing, which is why each step must), `fulltext/service.py` —
  the precedent #187 itself cites — reports a `_BUG_TYPES` member and
  *continues* rather than raising, and making this step alone fatal would
  change what a public `analyze()` may raise while `_check_crossref`'s
  identical defect stayed swallowed. The ERROR level is `jats_parser`'s
  precedent and **not** `fulltext/service.py`'s: four documents said that
  module reports a `_BUG_TYPES` member *at ERROR*, and it does not —
  `_warn_swallowed_bug` routes through `_warn_once`, which is
  `logger.warning`, and the module contains no `logger.error` at all. It is
  the precedent for reporting-and-continuing, which is what the argument
  needs. Corrected in the review of PR #192.
  `_BUG_TYPES` is restated rather than imported, as the
  nested-article element set is, and the two copies are pinned as agreeing by
  `TestTheRestatedBugTypesMatchTheOtherModules` — a rule enforced by prose is
  not enforced.

  **What it costs a downstream:** a stored result that used to read
  `not_served` for a 503, a timeout or an empty body now reads
  `request_failed`; one that used to read `entirely_nested` for an empty body
  now reads `request_failed` and no longer carries the *"served but not
  usable"* indicator. Nothing moves for a 404, which is 81 of 81 non-200s in
  the draw above. No score, risk level or COI value moves on any path.

  **No released corpus is affected.** `FullTextStatus` itself is unreleased —
  #161 added it in this same `[Unreleased]` block — so no published version
  of bmlib has ever written `full_text_status` at all, and the narrowing of
  `not_served` to the 404 cannot be read back into anyone's stored data. The
  two meanings never ship apart, which is why `analyzer_version` is not
  bumped: only a corpus written from unreleased `main` between #161 and this
  change can hold a `not_served` that meant a 503, and it is not
  distinguishable from a 404 there. Raised in PR #192's review as a
  migration question; this is the answer.

- **A refused full text now leaves a trace in the stored result** (issue #161,
  from PR #159's own review). `FullTextStatus` — exported from
  `bmlib.transparency` and carried on `TransparencyResult.full_text_status` —
  records what became of the full-text attempt: `NOT_ATTEMPTED`, `NOT_SERVED`,
  `ANALYZED`, or one of the four refusals (`TRUNCATED`,
  `UNTERMINATED_MARKUP`, `UNCLOSED_REGION`, `ENTIRELY_NESTED`), with
  `is_refusal` for the grouping rather than an enumeration at each call site.

  Before it, several outcomes were indistinguishable in anything a caller
  stored: a non-200, a document bmlib could not segment, and one that was
  entirely nested articles all reached storage as `full_text_analyzed=False`
  and the indicator `"COI disclosure status unknown (full text unavailable)"`
  — **false** for the refusals, where Europe PMC answered HTTP 200 with a
  document. Results are cacheable (`TransparencySettings.cache_results`) and
  driven concurrently, so a refusal was stored, permanent and unmarked, while
  the score lost up to `SCORE_COI_DISCLOSED + SCORE_DATA_FULL_OPEN` = 30
  points — enough on its own to reach HIGH against the default
  `score_threshold=40` and set `tier_downgrade_applied`. *"Which of my stored
  results were computed without the full text I was served?"* was answerable
  only by grepping logs and re-joining identifiers to a corpus. The same
  argument `publications/` made for `FetchResult.note` -> `SyncReport.notes`.

  The field mirrors `unknown_reason` throughout: **declared last** for
  positional stability, serialised by value, and read defensively so results
  persisted before it existed still load — while a present-but-unrecognised
  value raises, since a member this version does not know about is a result it
  cannot interpret. `None` means *not recorded*, never `NOT_ATTEMPTED`: a
  legacy result may perfectly well carry `full_text_analyzed=True`, and
  reading that back as a determinate "nothing was attempted" would be the
  worse answer. `__post_init__` enforces the one direction it can — when the
  status is set, `ANALYZED` if and only if `full_text_analyzed`.

  The prose half moves with it: a served-and-refused document now reports
  `"COI disclosure status unknown (full text served but not usable)"`. Plus
  the two smaller things the issue names — `document_id` threaded into every
  refusal WARNING, the only field joining a log line to a stored result, and
  each message quantified with the bytes served (`len(resp.content)`, not
  `len(resp.text)`: the latter is characters, and the number exists to be
  compared against a `Content-Length`).

  All three of the COI lines written while the status is undeterminable are
  retracted together when PubMed supplies a `<CoiStatement>`, from one named
  set (`_INDICATORS_RETRACTED_BY_PUBMED_COI`). The refused line was added to
  the site that appends it and not to the site that retracts, so a
  served-and-refused full text with a PubMed statement stored *"status
  unknown"* beside *"disclosure found"* against `coi_disclosed=True` —
  permanently, in a persisted field, which is this issue's own failure mode
  inside the fix for it. `is_refusal`'s two sides are named sets and the
  partition is asserted, so a member added later cannot default to *not a
  refusal*; and no path this version writes leaves `full_text_status` at
  `None`, which is what keeps that value meaning *legacy row* alone.

  A record claiming `inEPMC="Y"` and carrying no `source`/`pmcid` to address
  it by now WARNs instead of passing silently as an ordinary closed-access
  paper.

### Changed

- **A PubMed body that parses and carries no `PubmedArticle` says which kind
  it is** (issue #218, filed from PR #219's own live run). Diagnostics only —
  no stored value moves, and no request is added or removed.

  `_parse_pubmed_signals` returned empty signals in **silence** for such a
  document, while both of its neighbours reported: a body that will not parse
  WARNs, and an empty 200 body WARNs one level up. It is not a quiet outcome
  — empty signals mean no `<CoiStatement>`, so nothing in
  `_INDICATORS_RETRACTED_BY_PUBMED_COI` is retracted, *"COI disclosure status
  unknown"* stands, and the missing-COI downgrade is free to fire — and it
  was the *majority* outcome of the draw that finally sized it: `no-citation`
  for 50 of 60 served bodies on **2026-09-09** — the run that first carried
  that counter, the 09-08 one having reported the same bodies as plain `xml`,
  which is the defect that created it (PR #225's review). This is issue #193's
  *"check the
  diagnostic exists before arguing about its level"*, applied to the branch
  that fix did not reach.

  **One branch was three populations, and they do not share a level.** A
  single line would have repeated issue #191, whose whole finding is that a
  level measured on one population must not be applied to a wider branch —
  that draw is heavily NCBI Bookshelf, so it licenses a quiet level for book
  records and says nothing about the rest. Probed live on 2026-09-10 against
  three identifiers NCBI will not serve: a `<PubmedBookArticle>` set is
  **DEBUG** (declined by name, and nothing was lost that could have been
  had); an **empty `<PubmedArticleSet>`** at HTTP 200 — 205 bytes, what PMID
  999999999 returns — is **WARNING**, NCBI holding no record for an
  identifier that came from the caller or from `_pmid_from_epmc`; anything
  else that parses is **WARNING**, naming the root element.

  **Reading a book record would recover nothing, and that is measured rather
  than read off the DTD** — which is the issue's second question. Across 60
  `statpearls[book]` records and 100 drawn from `pubmed books[filter]`, **not
  one** carries a `<GrantList>`, a `<CoiStatement>` or a `<DataBankList>`;
  the existing comment asserted the first two from the DTD and conceded the
  third unmeasured. Read the zeroes as upper bounds over two draws NCBI's own
  search ordered.

  **The issue's own third population belongs to a request this module does not
  make.** It named `<eFetchResult><ERROR>…` at HTTP 200, which is real: an
  evicted **history session** efetch serves exactly that, probed the same day,
  and it is why `publications/fetchers/pubmed.py` refuses a root that is not a
  record set. `transparency` fetches **by id**, where the same probe read 400
  for a malformed id list and an empty record set for an id NCBI does not
  hold. Two error classes on one request shape is not every error class, so
  the unrecognised-document branch is what takes it if one arrives at 200 —
  and the two modules' comments describe different requests and must not be
  "reconciled".

  `_parse_pubmed_signals` takes the PMID now, so all four of its outcomes name
  the record. The neighbouring parse-failure WARNING named no subject at all,
  so an operator running two analyses could not tell which record produced it
  — the `subject` every request in this module already carries.

### Fixed

- **The article's own metadata is read only where the article deposits it**
  (issues #254 and #259, filed reviewing #230, and #152).

  `JATSArticle.title`, `volume`, `issue`, `pages` and `year` were written by
  any element of that name *somewhere inside* `<article-meta>` that was not in
  a `<ref>`. Among what JATS nests there, three things carry the same child
  names — a `<related-article>` after `<title-group>`, a book review's
  `<product>`, and a `<mixed-citation>` in abstract prose — so an editorial, a
  correction or a commentary took **the related paper's title**, a back-file
  article took **its companion's**, and a Wiley
  retraction notice, which cites the retracted paper in its `<abstract><p>`,
  took **the retracted paper's title, volume and issue** and a page range no
  document carries (`2010-2019-587`, the `<lpage>` arm appending). A **wrong
  value** each time, in the fields a downstream keys, matches and formats
  citations from, and on exactly the notices a literature tool must not
  confuse with their subject.

  **Each field is now read only at its owner path**, the wrappers taken
  element by element from the JATS 1.3 Tag Library: `front > article-meta >
  title-group` for the title, `> pub-date` (or its `<string-date>`) for the
  year, `front > article-meta` for the `<fpage>` and `<lpage>`, and the same or
  a `<volume-issue-group>` for the volume and issue. `<journal-title>` reads
  `front > journal-meta > journal-title-group`, and `<article-id>` `front >
  article-meta` — issue #152, whose guard was `parent == "article-meta" or
  self.in_front`, neither half pinned and the two not equivalent. Both
  measure **0** non-owner firings outside a nested article on every artifact
  below, so they join the rule for consistency and not for a population;
  but the `in_front` half *was* reachable by valid markup, since JATS 1.3
  admits an `<article-id>` in a `<pub-history><event>` — another version's
  identifier, a preprint's DOI, which replaced the article's typed DOI. Each
  wrapper is optional where the article's own container holds the value bare:
  the NLM 2.x `<journal-meta><journal-title>` is the **majority** spelling in
  the back-files (2,309 of 3,028 articles in `PMC000xxxxxx`, 16,771 of 27,515
  in `PMC001xxxxxx`), and a bare `<article-title>` or `<year>` is invalid,
  measured nowhere, and admitted because nothing else there could own it. No
  other dated element — a `<history>` date, a `<pub-history>` event's, another
  work's — stands in for a missing `<pub-date>` year (every measured article
  carries one, so no value moves), and where the article carries no `<fpage>`
  of its own, `pages` stays blank rather than taking a citation's or a related
  article's. The handler's `in_article_meta` flag had no reader left and is
  gone, with its audit entry.

  **Measured at the arms before the fix** (the parser's own `endElement`,
  instrumented, recording each firing's ancestor path and whether it changed a
  value, nested articles and references aside), then **diffed against `main`
  with both checkouts in one process**, and re-diffed after the review fixes
  with identical results:

  | moves | served: 8,118 of `PMC10030002_PMC10040000.xml.gz` | archive: 97,909 of `oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26.tar.gz` | `PMC000xxxxxx` (3,028) | `PMC001xxxxxx` (27,515) |
  |---|---|---|---|---|
  | `title` | 95 | 1,115 | 529 (17.5%) | 1,667 |
  | `volume` | 2 | 171 | 0 | 4 |
  | `issue` | 0 | 145 | 0 | 10 |
  | `pages` | 2 | 84 (58 set from nothing) | 0 | 17 (15 set from nothing) |
  | `year`, `journal`, `doi`, `pmc_id`, `pmid` | 0 | 0 | 0 | 0 |
  | HTML | 96 | 1,138 | 529 | 1,667 |

  HTML moves in exactly the union of the metadata movers on all four, and
  authors, both section lists, figures, tables, references and `has_body` move
  in **0** — the `<h1>` and the journal line are the rendered half, so **a
  downstream holding cached full text should re-fetch**. By the moving
  article's own type, the archive's 1,115 titles are 402 editorials, 394
  corrections, 256 retractions, 38 commentaries, 13 expressions of concern
  and 2 book reviews among others; of the archive's 1,126 title-overwriting
  firings, 952 were a `<related-article>`, 172 an abstract citation and 2 a
  `<product>`. `PMC000xxxxxx`'s 529 are 520 `companion` related articles (418
  research articles and 101 `other`) and 9 corrections. Two gaps between the
  survey's last-writer tally and the diff were each traced to one shape, a
  non-owner writing back the article's own value: `PMC12008015`'s own volume
  22 is overwritten by the first cited work's 20 and written back by the
  second's 22 (171 against 172), and three `PMC001` articles carry a companion
  `<related-article>`, another DOI, whose title equals the article's own
  (1,667 against 1,670). The back-files are the archive rendition; in two
  early served bundles (`PMC100320_PMC107849` and `PMC110703_PMC119861`, 429
  articles) no non-owner firing changed a value — surveyed, not diffed.

  **Mutation**: 33 mutants on the guards, the helpers, the path constants and
  the wrapper lists — each owner test swapped for the old gate or for a
  one-shape exclusion, a root-anchored `_owned_by`, each wrapper list emptied —
  all killed, plus two re-measurements of the `element_stack.pop()` placement:
  224 and 236 tests now redden, against 179 and 191 on `main`, where its
  comment still quoted the 58 and 65 of an earlier revision. Every mutant the
  test-coverage review found surviving now has a test — a wrapped root such as
  NCBI efetch's `<pmc-articleset>`, a related article's own volume and pages,
  the `<lpage>` guards, a stray `<article-meta>` with nothing set first, a
  review round's full `<front>` — and the claims review corrected sixteen
  statements, five of them false, before the PR. Of the first cut's two
  survivors, one was a fixture gap and one an unmade decision: the year arm's
  pre-existing `and not self.year`, pinned by nothing. First writer among the
  `<pub-date>`s stores a manuscript submission
  (`nihms-submitted`) year that differs from the epub-else-ppub year in 35
  served and 249 archive articles. Filed as **#261** and pinned by a test to
  reverse when it is decided. The Swift and Kotlin ports carry the same
  ambient gates.

- **Front-matter prose reaches the article, and a front-matter section carries
  its prose** (issues #230 and #234, both filed while reviewing #224's
  routing).

  `_append_prose` admitted `<body>` and, since #224, `<back>`; prose in
  `<front>` fell past every branch with **no counter and no line**. That is
  where JAMA deposits *"Funding/Support"* and *"Role of the Funder/Sponsor"*
  as bare `<author-notes><p>`, where `<fn fn-type="COI-statement">` sits, and
  where PLOS puts its data-availability `<notes>` — the material #224 routes
  when a publisher puts it in `<back>`. Beside it (#234), a `<sec>` in front
  matter was already appended to `body_sections`, ahead of the body, **titled
  and empty**: a *"Data availability"* or *"Competing interests"* heading with
  its statement dropped, which reads as the article declaring nothing there.

  **Routed in document order, with no special case** — the maintainer's choice
  once the numbers were in, over a separate `front_matter` field and over a
  counter with routing deferred. A third implicit-section slot flushes at
  `</front>` and at each front `<sec>`, so loose front-matter prose forms
  untitled sections in document order — **ahead of the body** in
  `body_sections`, rendered just after the abstract — and a front `<sec>`
  keeps its paragraphs. A `<trans-abstract>` routes the same way — it is
  sometimes the only English abstract an article carries — and stays out of
  `abstract_sections`. `fn-type="edited-by"` boilerplate (*"Edited by: …"*;
  2,443 of the 6,280 served `<author-notes>` runs, 41,431 of the 81,810
  archive ones) is not filtered: that would decide by an attribute vocabulary.
  `has_body` still counts `<body>` alone, and a `<permissions><license><p>` is
  still declined as metadata — a refusal that is now load-bearing for the 19
  archive licences, each the article's own in `<article-meta>`. **The `<ref-list>` refusal
  applies in `<front>` as in `<back>`**, since `<front>` admits `<notes>` and
  `<notes>` admits a `<ref-list>`: the first cut routed that apparatus as
  prose on a comment's claim that JATS admits none there, which the claims
  review refuted. No artifact deposits one, and diffed against the commit
  before, the refusal moves 0 of the 8,118 served and 0 of the 97,909 archive
  articles.

  **A paragraph the publisher deposits twice is rendered twice.** Springer
  puts *"Open Access funding enabled and organized by …"* in both
  `<funding-group><open-access>` and a `<back><notes>`, so 108 served and
  2,984 archive articles now carry some paragraph twice in `body_sections` —
  2,844 of the archive ones that line, the rest a sentence such as *"These
  authors contributed equally"* deposited in two places. Nothing is
  deduplicated: dropping a repeat by its text would decide which of two
  deposits is the article's.

  **Measured at the drop**, with the parser's own predicates and every run
  checked against a before/after fingerprint of every destination (0
  mismatches), a `<p>` in a table cell excluded since `characters()` files the
  cell: **9,328 runs in 3,350 of the 8,118 served articles** of
  `PMC10030002_PMC10040000.xml.gz` (41.3%, 1.08 million characters) and
  **114,519 in 46,737 of the 97,909** of
  `oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26.tar.gz` (47.7%, 12.1 million).
  By owner, served / archive: `<author-notes>` 6,280 /
  81,810 (9,865 archive `COI-statement` runs in 9,645 articles), `<notes>` 833
  / 13,988, `<def-list>` 1,441 / 9,280, `<funding-group>` 304 / 5,328,
  `<trans-abstract>` 318 / 3,059, `<title-group>` 73 / 538, `<contrib-group>`
  (`<bio>`) 73 / 516, `<fn-group>` 6 / 0. Every owner is an input under test. The empty front
  sections were 263 served and 3,099 archive; **0 remain empty** on either.

  **Blast radius, diffed against `main` with both checkouts in one process**:
  prose moves in **3,350 served** and **46,737 archive** articles, **every
  move an insertion** — 9,332 and 114,549 paragraphs, 1.09 and 12.1 million
  characters (1.12 and 12.4 MB as UTF-8) — and `html_content` moves in exactly
  those, so **a downstream holding cached full text should re-fetch**.
  `abstract_sections`, `figures`, `tables`, `references`, authors, metadata,
  `has_body` and every non-empty section title move in **0**, with 0 audit
  ERRORs on either side. Reconciled per article against the tally, not in
  total: the diff exceeds it by 4 served and 30 archive paragraphs, in 1 and 7
  articles, and every one is an empty string — a front `<sec>`'s `<p>` holding
  only an author photo, kept by the sectioned branch's `keep_empty` and skipped
  by the renderer. Its characters exceed the tally's because the tally counts a
  run before #228 folds a definition term into it: everything up to a ` — ` in
  an inserted paragraph is at most 11,367 and 66,749 characters, and the diff
  less that is 1,078,900 and 12,073,209 — the tally's 1.08 and 12.1 million.
  Both figures were quoted as MB until PR #256's review; they count
  characters.

  **`definition_terms_dropped` loses its main population**: 1,444 → 3 served
  and 9,468 → 23 archive, the 1,441 and 9,445 front-matter terms now folded
  (14,174 and 153,226 folds, re-measured, closing on the same totals). What is
  left is exactly the `<def-item>` depositing no `<def>`, **per article** on
  both artifacts — an identity the counter's comment could previously only
  call a coincidence of totals. Its routing test moved to a `<floats-group>`
  shape so it cannot go vacuous.

  **Mutation**: 14 mutants on the change, 12 dying first time. One survivor
  was a fixture gap — the routing predicate's back-before-front order, pinned
  by a `<back>` nested in a `<front>` keeping its `<ref-list>` refusal, and
  then made moot when the review's `<front><notes><ref-list>` finding put back
  and front under one rule. A control mutant dropping `in_back` from
  `_prose_reaches_output`'s section conjunction **survived too**, a
  pre-existing unpinned guard, and a formula under a sectioned back
  `<ref-list>` now pins it. **`in_front` in the same conjunction was recorded
  as equivalent by construction, and it is not** (PR #256's review, four
  reviewers independently): that held until the `<front><notes><ref-list>`
  fix gave front matter the `<ref-list>` test, after which dropping it lost an
  `<attrib>` and a definition term under a sectioned front `<ref-list>` and
  reported a filed formula as dropped — with every test in the module passing.
  The formula test now runs per container, and
  `test_prose_under_a_sectioned_reference_list_is_filed_whole` asserts the
  lost content for both; only `in_body` is equivalent. A well-formed fixture
  interleaving loose front prose with two front `<sec>`s pins document order,
  which only the DTD-invalid nesting tests reached before. A sectioned `has_body`
  fixture was added ahead of the sweep for the mutant that planning showed
  would survive.

  **Not taken, and filed**: a `<floats-group>` sits in none of the three
  containers, so non-float content in it still falls past every branch — 30
  runs in 9 served articles (28 in 8 a `<boxed-text>`'s, 2 in 1 a
  `<table-wrap-group>` caption's) and 925 in 192 archive (894 in 184 and a
  `<fig-group>` caption's 31 in 8) — with the same empty-heading shape after
  the body (3 and 116 `<sec>`): #253, a position decision of its own. The tests that used `<front>` as the example of prose reaching nothing
  now use that shape. Issue #233 (a formula merged into a dropped `<p>`) loses
  its front-matter population and keeps the float and `<floats-group>` ones.

  **Pinned rather than changed: front matter renders under the Abstract
  heading.** An untitled section gets no heading (#30), so in the cached HTML
  the front section's paragraphs follow the abstract's under
  `<h2>Abstract</h2>` — `abstract_sections` itself stays clean. An unsectioned
  `<body>` already did this on `main`; the maintainer chose to settle untitled
  sections for body, back and front together under #231, and a test now pins
  the exact markup so that fix changes it on purpose.

  **Found by the reviews and filed**: #254, a `<related-article>`'s
  `<article-title>` overwriting `JATSArticle.title` — pre-existing and
  independent of this change, but a wrong value in 94 of the 8,118 served
  articles, a retraction notice parsed with the retracted paper's title among
  them; and #255, a Wiley self-citation `<p><mixed-citation>` in front matter
  that arrives empty and is still dropped with no line (231 served articles).

- **An object's metadata is not prose, and an attribution is filed where it is
  printed** (issues #241 and #248, filed by the reviews of PRs #239 and #246).

  `<alt-text>`, `<long-desc>`, `<object-id>`, `<permissions>` and `<attrib>`
  accumulated nowhere and had no arm, so `characters()` appended their text to
  whatever buffer was open above the object — for an exhibit deposited inside
  a `<p>`, the sentence: `'BeforeTable 2after.'`, and PMC10030262 read `'…in
  Tables 2.Table 2Table 3'`. And because `characters()` also writes a table
  cell directly, an image in a cell put its description in the rendered table,
  `'12.3Image 1'`. A **wrong value** where a blank was the alternative.
  Counted over the 8,118 served articles of `PMC10030002_PMC10040000.xml.gz`,
  by each text-bearing element's nearest accumulating ancestor with suppressed
  regions skipped: 4,018 `<alt-text>` in 522 articles reached a `<p>`'s buffer
  and 67 (in 9) a cell; over the 97,909 archive articles of
  `oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26`, 15,792 (in 2,160) and 462
  (in 49). #248's own 3,877 in 537 counted six element types inside an exhibit
  inside a `<p>`, a different predicate. The `<alt-text>` values are
  placeholders almost throughout — `"Fig. 1"`, `"Image 1"`, `"Multimedia
  component 1"`, a figure's DOI — and an archive `<permissions>` typically reads
  `"© 2024 WILEY-VCH GmbH"`. **That is not true of every member**: 5 of the 7
  served `<long-desc>` are genuine descriptions, and 5 served `<permissions>`
  on a figure inside a `<p>` are a stock-photo credit. They are declined all
  the same, having welded into the sentence on `main`; whether a figure's
  credit or description should be routed as `<attrib>` is, is issue #251.

  **The four metadata elements are declined: three routes, three guards.**
  `_NON_PROSE_METADATA` joins `_TEXT_ACCUMULATING`, which isolates every child
  that *merges* — #243's argument for a cell. It cannot stop a child that
  *routes*: a `<p>` inside `<license>` (modelled `(p)+` before `<license-p>`)
  goes through its own arm whatever buffer surrounds it, so `_append_prose`
  refuses prose under this metadata and `_prose_reaches_output` mirrors the
  refusal. And a cell is written by `characters()` and by the formula arm
  directly, bypassing every buffer, so both now go through one
  `_offer_cell_text`, which holds the text back. The cells are a population
  (the 67 and 462 above, which buffer membership does not reach); the `<p>`
  route and the formula half of the cell route measure 0 outside
  `<article-meta>` and pin directions. Declined metadata adds to no counter.
  **Two ancestors keep it, on every route** (`_TEXT_CLAIMING_ELEMENTS`): a
  `<mixed-citation>`, under #146's rule that every descendant is the citation's
  text, and an `<xref>`, where an image that *is* the reference would otherwise
  leave the link label empty and have `"Figure"` invented for it. One predicate,
  `_inside_declined_metadata`, walks from the root and serves the cell, the
  prose routes and the formula counter, so under either claimer the parse is
  `main`'s. No member lands in either in the two artifacts. And a formula's
  image `<alt-text>` is kept a third way, as that formula's rendition of last
  resort (`_FormulaFrame.alt_text`), read only where no LaTeX renders and the
  buffer is empty — so an image-only formula whose deposit spells it out keeps
  the text `main` printed, while a MathML formula no longer welds the image's
  alt-text beside its own encoding (0 in both artifacts).

  **`<attrib>` is routed, not discarded, which is where this departs from both
  issues' suggested resolution.** An attribution is typeset — an interview
  quote's `"(P2, CP)"`, a figure's `"Source: Authors' elaboration."`, a table's
  abbreviation list — and `main` was already losing most of it in silence: a
  quote or exhibit standing in a `<sec>` put its attribution in that section's
  unread buffer. Of the archive's 5,266 quote attributions 3,844 (217 articles)
  went that way and 2 more with no buffer open, against 1,331 (94) welded into
  a sentence and 89 in cells, so discarding would have made the majority's
  loss total. It now routes as a `<p>` does — a quote's attribution becomes the
  paragraph after the quote, one in a `<table-wrap-foot>` a table note — and an
  exhibit's attribution, or its image's, is appended to that exhibit's
  `footnotes`: `_append_prose` could not file it inside a float. Four positions
  take it first: under a `<mixed-citation>` or an `<xref>` it is that element's
  text, under declined metadata it is declined, and in a table cell — an
  `<array>`'s included, so #245's counter sees it — it is the cell's. Three
  further differences from a `<p>`: an empty one adds nothing; it never spends
  a pending footnote marker or definition term (it closes before the `<p>`
  around it, and would take the word meant for that paragraph) unless it is
  the whole of its note, when `</fn>` folds the marker into it; and one that
  reaches nothing is **counted** (`attributions_dropped`, one WARNING per
  article) — an attribution owned by an element bmlib does not model inside a
  float welded into the sentence on `main` and is a blank here. The counter
  reads 0 over both artifacts. **Every `<attrib>` is accounted for**: of the archive's 6,343, 5,378
  become paragraphs, 677 figure notes, 192 table notes, 89 stay in the cell
  holding their quote and 7 are empty; the served artifact's 385 split 239 /
  125 / 21.

  **Blast radius, diffed against `main` with both checkouts loaded in one
  process.** Served: rendered HTML moves in **584 of 8,118 (7.2%)**, against 585
  whose structured fields move. Paragraphs move in 577 — 3,200 stripped in
  place, 239 attribution paragraphs inserted (in 15 articles, equal to the
  routing tally), and 11 dropped, each an unsectioned paragraph whose whole
  content was metadata. 121 abstract sections move, one per article, each a
  graphical abstract losing its image placeholder: 84 now read empty where they
  read `"Image 1"` or `"ga1"`, 37 keep a summary the placeholder had welded
  onto. 125 figure notes and 21 table notes are added, and 12 tables' cells are
  stripped. Section titles, figure and table labels, captions and graphics,
  `references`, `has_body` and every drop counter move in **0**. Archive: HTML
  moves in **3,098 of 97,909 (3.2%)**, against 3,101; 13,414 paragraphs are
  stripped, 5,358 inserted and 16 dropped (7 whose whole text was an
  attribution now in a footnote, 9 wholly metadata); 563 abstract sections are
  stripped, and 18 graphical abstracts whose only text was their figure's
  attribution lose that section, the text moving to the figure's `footnotes`;
  6 figure captions in 4 articles lose an `<inline-graphic>`'s placeholder
  (`"colostrum (Image 1)"` becomes `"colostrum ()"`, part of #242's population); 5
  articles lose an empty implicit section title and none a non-empty one.
  **Every count was reconciled, not just reported.** The archive prose tally
  exceeds the inserted paragraphs by 20, all traced per article: an enclosing
  unsectioned `<p>` that held only the object already carried the attribution
  as a whole paragraph (PMC12175162's twelve author-photo credits), or the
  alignment paired two resulting empty paragraphs arbitrarily. The HTML
  shortfalls (1 served, 3 archive) are each an enclosing paragraph become `""`,
  which the renderer skips. A second comparator run after the review fixes
  below reproduced every figure. **A downstream holding cached full text should
  re-fetch.**

  **Mutation: 26 mutants and 2 pairs in the first sweep, 10 more after review.**
  Four first-sweep survivors were fixture gaps: dropping `long-desc` or
  `object-id` from the named set alone (the cell test is now parametrised over
  all four), an ambient `current_figure` test in place of the parent test
  (killed by a `<table-wrap>` placed directly in a `<fig>`, a shape deposited
  once in the served artifact), and an un-normalised quote attribution. **PR
  review then found five shapes the sweep could not see**, each legal JATS at 0
  measured population: an image credit inside a note's or definition's `<p>`
  took the marker or term (`'BMI — Credit: X.'`); an image-only `<xref>` lost
  its label to the invented `"Figure"`; a figure's image credit reached nothing
  inside the float; an attribution in a `<mixed-citation>` was filed twice; and
  the mirror *paired with* the formula counter's subtraction survived, which
  had been written down as equivalent — true in a section, false in a float or
  in `<front>`, where only the subtraction stands between a declined formula and
  a WARNING claiming a loss. All five are fixed and pinned, the refusal is now
  exercised for every member (narrowing it to `<permissions>` had survived),
  and the ten mutants aimed at the new guards all die but one: `fold_marker=False`
  on the exhibit branches, where no marker can be pending. The mirror alone
  remains an equivalent mutant, each of its consumers carrying a protection of
  its own. The buffer-reading inventory was re-measured at twenty-six elements —
  `disp-formula`, `inline-formula`, `tex-math` and `term` had been read by arms
  added since it was taken and were never listed — and at twenty-seven once
  `<alt-text>` gained its arm.

  **A second review found the exception reaching one route of three**, and
  five edge shapes beside it, all at 0 measured population. The `<xref>` and
  `<mixed-citation>` exception lived at the buffer pop alone, so a cell lost
  `See Figure 1` to `See` while four documents said the parse under an
  `<xref>` was exactly `main`'s; declining every `<alt-text>` emptied an
  image-only formula (`'where is the rate.'`, and a labelled display formula
  rendered as nothing with its `(1)`); an image credit that was the whole of a
  note lost the marker behind a WARNING saying no prose was filed; an
  `<attrib>` under an `<xref>` brought back the invented label; one in an
  `<array>`'s cell became a paragraph while `cell_text_dropped` fell to zero;
  and one owned by an unmodelled element inside a float was dropped with no
  line. All are fixed as above. **Diffed against the commit before, over all
  8,118 served and 97,909 archive articles, nothing moves** — every field and
  the rendered HTML identical, and `attributions_dropped` 0 throughout — so the
  blast radius above stands. Mutation over the new guards: 20 mutants, five
  surviving the first sweep. Three were unpinned rules (first alt-text wins,
  its normalisation, which of several credits takes the marker); one was a
  defect — the cell walk ended at a `<fig>` as well as a `<table-wrap>`, but
  `characters()` offers text to the innermost open *table*, so it would have
  counted as lost an attribution sitting in the cell; and `</alt-text>`'s
  declined-metadata condition could not be separated from its absence, the
  fallback being read only for an empty buffer, so it is gone. The resweep's
  one survivor, the walk's stop at an inner `<table-wrap>`, took a fixture of
  its own; all 21 mutants of the final sweep die.

  **Filed and recorded rather than fixed.** Issue #251: some declined metadata
  is printed content (the `<long-desc>` and figure-credit measurements above).
  Issue #252: an image credit inside a caption's or an abstract's `<p>` is
  routed ahead of the paragraph containing it, so the one string reads `'G src
  Cap end.'` where `main` read `'Cap G src end.'`; a nested `<p>` has always
  done the same, no ordering inside one string is clean, and the choice should
  serve both (0 in both artifacts for `<attrib>`). Issue #249: an `<abstract>` inside
  a `<fig>` or `<table-wrap>` — a second-language caption, 247 deposits in the
  archive and 40 served — reaches nothing, and its open resets the article's
  abstract state, so one inside the article's own `<abstract>` would erase it
  (0 in both artifacts, so a direction). #241's `<caption>` half is #137's rule
  with an empty population for a `<graphic>` owner, and the measurement posted
  to #137 sizes that issue for the first time: 3,155 served and 58,263 archive
  `<supplementary-material>` legend paragraphs, and 6,443 and 27,705 `<media>`
  ones, are filed as article prose without their titles. #241's `<label>` half
  is #235's. Whether the now-isolated `<alt-text>` should feed `<img alt>` is
  #173's question, and the measurement posted there cuts against it: 2,390 of
  2,491 served figure-level ones are placeholders.

- **A cell's text is the cell's own** (issue #243, filed by PR #239's review).

  `characters()` delivered every cell's text to the open buffer *as well as* to
  the cell, so a `<table-wrap>` deposited inside a `<p>` — legal JATS, and
  7,248 such deposits sit in 2,237 of the 8,118 served articles of
  `PMC10030002_PMC10040000.xml.gz` — spliced the table's numbers into the
  sentence around it:
  `<p>Before<table-wrap>…12.3…</table-wrap>after.</p>` stored
  `'Before12.3after.'` in `body_sections` and in the HTML `FullTextService`
  caches, and an exhibit opened inside a footnote's `<p>` gave the outer note
  `'a — See12.3'`. A **wrong value** where a blank was the alternative, which
  is what puts it ahead of the drops filed beside it.

  **The hold is the cell's own text buffer, not a test in `characters()`.** The
  issue proposed the latter, mirroring the formula hold one line up, and it
  reaches two of the four routes *found* — raw character data, and an inline
  run merging back. It leaves the other two and makes one of them *worse*: an
  `<xref>` builds its link from the buffer the hold would have emptied, and the
  arm's own `text or "Figure"` fallback then fires, so the paragraph gains
  `'[Figure](#f1)'` in place of `'[Fig 1](#f1)'` — an **invented** label, which
  is #162's own symptom and worse than the blank it replaces. And the formula
  arm appends its chosen rendition through an `_append_text` that
  `characters()` never sees. Enumerating the arms that merge is the list #116
  established cannot be completed by inspection, so the argument is about the
  fifth route nobody has found rather than about these four.

  `td`/`th` join `_TEXT_ACCUMULATING` instead: the cell takes a buffer at its
  open, every child that merges back merges into *that*, and `</td>` pops it
  and drops it, the cell itself filling `_TableBuilder.current_cell_text` from
  `characters()` directly. Accumulating in order to discard is not by itself
  unusual here — `<sec>`, `<abstract>`, `<caption>`, `<def>`, `<list-item>`,
  `<person-group>`, `<element-citation>`, `<alt-title>` and `<kwd>` all take a
  buffer no arm consumes — and one arm does consult a cell's, for emptiness
  alone, to decide whether an unmodelled cell lost anything (#245). Its
  *content* is read nowhere. Membership needs one exclusion of its own:
  `_inside_mixed_citation()` was the single path by which a cell's buffer could
  still merge, and left to it the drop would rest on the absence of an
  `<array>` under a `<mixed-citation>` rather than on the code — so the pop
  carries `not is_cell` beside the terms `_FORMULA_PARTS` and
  `_UNDIVIDED_NAME_ELEMENTS` already earn. Measured 0 such cells over both
  artifacts, so that pins a direction.

  The paragraph then reads `'Beforeafter.'` — **of the cells**, which is the
  `<fig>` shape's own long-standing answer. It is not clean of everything an
  inline exhibit holds: an `<alt-text>`, `<attrib>`, `<long-desc>`,
  `<object-id>`, `<copyright-statement>` or `<copyright-year>` accumulates
  nowhere and still welds into the sentence, in 537 of the 8,118 served
  articles (3,877 runs, `<alt-text>` 3,765 of them). That is pre-existing, is
  untouched here, and is issue #248, neighbouring #241 — answered by the entry
  above. Spacing round a merged
  block is #147's open question and is not touched here either.

  **The population is far larger than the issue supposed, and it is a routing
  diff rather than a markup walk.** Diffed against `main` over all 8,118 served
  articles of `PMC10030002_PMC10040000.xml.gz`: a paragraph moves in **2,222
  (27.4%)** — 6,356 stripped in place, 10 dropped outright, **0 unexplained** —
  and `html_content` moves in exactly those 2,222. `abstract_sections`, figure
  and table captions, exhibit footnotes, `references`, every table's own
  `html_content` and `has_body` move in **0**. Over the 97,909 archive articles
  of `oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26.tar.gz` a paragraph moves in
  **21,377 (21.8%)** — 50,042 stripped, 286
  dropped, 0 unexplained — and that rendition also moves 11 abstracts and
  1 figure caption, the two destinations the served bundle happens
  not to exercise. **A downstream holding cached full text should re-fetch.**

  **The diff's own predicate is a claim, and the first one was wrong.** A
  `difflib` opcode walk over a list whose every member changed aligns
  arbitrarily: it reported 15 paragraphs lost, one of which is present and
  merely stripped. This change can only *remove* characters from a paragraph,
  and remove the paragraph outright when the cell text was all it held, so the
  honest predicate is a character-level subsequence test walked with two
  pointers — which reads 10 dropped and 0 unexplained.

  The 10 are each a `<p>` whose only content was the table. Three are Springer
  and Adis *"Key Points"* panels deposited as a one-column table, and every one
  of them is present in `JATSArticle.tables` with proper rows — so on `main`
  that content was stored **twice**, once as the table and once run together
  into prose. Six articles also lose a section title, and all six lose an
  *empty* one: the untitled implicit section whose only paragraph was the cell
  text. No named heading moves — checked at archive scale too, where **68 titles in
  68 articles** are lost and **0** of them is non-empty. Each unit is stated
  because the two coincide on both artifacts (6 in 6 served) and a bare "68"
  read either way.

  **The deposit survey says where the shape lives, and one gap needed
  explaining.** 7,248 `<table-wrap>` sit inside a `<p>` in 2,237 of the 8,118
  served articles, and a `<p>` is the only text-reading buffer that carries one
  — 0 in a `<caption>`, 0 in an `<fn>`, 0 nested inside another exhibit. The
  survey counts 2,223 articles with a cell under a reading buffer against the
  diff's 2,222: the one article over is a paper whose single inline table holds
  one *empty* cell, so there was nothing to strip. A gap between two of bmlib's
  own counts is a defect in one of them until it is explained.

  Beside it, issue #245 is what the fix newly makes total rather than partial —
  see the entry above.

- **A definition carries the word it defines** (issue #228, this repo's first
  issue filed from a measurement rather than from a review — the survey issue
  #224 needed turned it up.)

  `<def-item>` pairs a `<term>` with a `<def>`, and the `<def>`'s `<p>` routes
  as ordinary prose while the `<term>`'s buffer was popped and discarded — so
  an abbreviations list arrived as *"messenger RNA / odds ratio /
  reverse-transcriptase polymerase chain reaction"*, definitions with no words
  defined. Pre-existing in `<body>`, and multiplied in `<back>` by the routing
  issue #224 added, `<glossary>` being one of the larger containers it
  reaches.

  **The term is folded into the definition's own paragraph** —
  `"mRNA — messenger RNA"` — rather than modelled. That is what this module
  already does with a `<list>`, whose `<list-item>` contributes no text of its
  own while its `<p>` becomes a paragraph, and it is the shape issue #124
  proposes for a footnote marker: one answer for three containers instead of
  three public fields. A `definitions` field on `JATSBodySection` would be a
  new public shape every downstream must learn *and* would move the
  definitions out of `paragraphs`, so stored values would move twice for one
  recovery.

  **The population, over two named public artifacts.** 14,186 `<def-item>` in
  965 of the 8,118 served articles of Europe PMC's
  `PMC10030002_PMC10040000.xml.gz`, and 153,256 in 9,813 of the 97,909 archive
  articles of PMC's `oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26.tar.gz`.
  Every one of them carries exactly one `<term>`, and every `<term>` in both
  is a direct child of its `<def-item>`, so the parent test this arm uses pins
  a direction rather than a population.

  **What the routing does with them.** 12,667 terms folded in 840 of the 8,118
  served articles and 142,855 in 8,978 of the 97,909 archive ones; 1,510 and
  10,394 dropped, in 128 and 844 articles. **Three counts close on both
  artifacts**: fold plus drop equals the terms carrying a word — 12,667 +
  1,510 = 14,177 = 14,186 − 9 empty, and 142,855 + 10,394 = 153,249 =
  153,256 − 7 — so the markup walk, the fold counter and the drop counter
  agree to the unit and the partition sums to its own denominator.

  **Blast radius, from a diff over all 8,118 served articles.** A paragraph
  moves in **840 articles (10.3%)** and **12,667 paragraphs change in place**
  — every move is a change in place, with **0 paragraphs gained and 0 lost**,
  the totals identical at 329,733 either side. `html_content` moves in the
  same 840 (+86,253 bytes), so **a downstream holding cached full text should
  re-fetch**. `has_body`, section titles, `abstract_sections`, figure and
  table captions and `references` move in **0**.

  The routing tally and the diff agree exactly on both numbers, and their
  agreeing is what caught a defect in the diff harness: it flattened
  `body_sections` without recursing into subsections, which hid 48% of the
  corpus's paragraphs and read 839 moved articles where 840 had moved. Two of
  bmlib's own counts never settle in favour of the weaker one.

  **A term whose definition routes nowhere is lost with it, and now leaves a
  line.** The fold is spent only on a paragraph that is *accounted for*:
  `_append_prose` has three outcomes, not two — filed, refused as bibliography
  apparatus and counted, or fallen past every branch with no counter at all,
  which was `<front>` until issue #230 routed it (the entry for that change
  records what the counter was left with). Consuming the term in the third case would
  hand it to a paragraph nobody sees and leave the new counter reading zero
  over the population it exists to size; consuming it on the refusal keeps one
  loss to one count, the rule PR #232's review had to correct for a
  `<disp-formula>`. `definition_terms_dropped` counts what is left — 1,510
  terms in 128 of the 8,118 served articles — and `_audit_parse` reports it
  once per article at WARNING, the granularity and the level `rejected_spans`
  (#129), `formulas_dropped` (#177) and `refused_apparatus_prose` (#224) all
  settled.

  Where those 1,510 sit is measured **at the drop** and not inferred from the
  markup, a `<front><abstract>`'s definition list being *folded* into the
  abstract rather than lost: 1,441 in `<front>` (issue #230), 66 in a `<body>`
  float with no `<caption>` open (the definition dropped as exhibit furniture,
  which is issue #124's container), and 3 in `<back>` outside a float, where
  prose does route — so the only way there is to deposit no routable prose at
  all. The served bundle also holds exactly 3 items carrying no `<def>` — a
  coincidence of counts and not a checked identity at the time, written as
  *"the same 3"* until PR #236's review; nothing then verified the two sets are
  one, and the archive offered no cross-check, its 10,394 drops never having
  been decomposed this way against 23 items with no `<def>`. (Issue #230's
  entry checks that identity per article on both artifacts, once front matter
  routes.) None was reached by a second `<term>` displacing the first.

  **The shared label-or-term counter issue #228's own comment proposed is
  refused on measurement**, and filed with its table as issue #235. An
  unfiled `<label>` — one whose owner is not a formula, a `<fig>`, a
  `<table-wrap>` or a `<ref>` — reaches 6,225 of the 8,118 served articles
  (76.7%) and 86,516 of the 97,909 archive ones (88.4%), where each of the
  four counters it would sit beside fires on a small minority; and its owners
  are **at least four** separate questions with four answers. Those four leave
  ~4,190 of the 62,226 unaccounted, the largest remainder a
  `<supplementary-material>`'s own label (2,998 served, 42,901 archive —
  comparable to `<corresp>`), so the split is a floor on the number of
  questions rather than a partition — the exhaustive
  phrasing was PR #236's review. The `<aff>`/`<corresp>` row is 25,332, of
  which 23,077 is the `<aff>` alone; the narrower figure is the one
  `jats_parser.py` states, and the two differ by `<corresp>`'s 2,255 rather
  than by any disagreement.

  A stack of pending terms rather than a slot, because a `<def>` admits a
  `<def-list>`; `ParseUnwindState.open_definition_items` audits it. A stranded
  frame costs one of two opposite things — carrying a word it welds that onto
  the next paragraph of any kind to arrive, carrying none it masks the
  enclosing item's term and suppresses its fold — and the diagnostic names
  both, as `open_contribs` names its own.

  **Three corrections from PR #236's review, one of them behavioural.**

  *The fold is scoped to the definition's own prose.* JATS admits a `<fig>` or
  `<table-wrap>` inside a `<def>`, whose `<caption>` is the first prose to
  reach output while the item is open — so the term was folded onto
  `JATSFigureInfo.caption` / `JATSTableInfo.caption`, a public field `to_html`
  renders and `FullTextService` caches, while the definition went without it:
  a *wrong* value where the alternative is a blank, silent and uncounted, and
  a third way out of the partition above. `_DefinitionFrame` now captures
  `len(figure_stack) + len(table_stack)` at the open and the fold is refused
  where that has grown, the term staying pending to be counted at
  `</def-item>`. A depth rather than a flag, because a `<def-list>` *inside* a
  caption is legitimate and common and must still fold. **0 of 14,186 served
  `<def-item>` and 0 of 153,395 archive ones hold a float inside their
  `<def>`** (whole-document walks, so the wider denominator), so nothing
  stored moves and the rule pins a direction.

  *A `<term>` that reaches no frame is counted.* One whose parent is not a
  `<def-item>` was read and discarded in silence, so the partition closed only
  because neither corpus deposits one — a property of the draw, not of the
  code. It is counted now, on the rule that counting is not parsing.

  *The separator's evidence is the terms themselves.* A first cut argued the
  em dash from `tests/data/funder_names.json`, a `transparency` corpus of
  funder organisation names — the wrong population in the position this module
  reserves for a rule's evidence. Measured on the `<term>` corpora instead:
  174 of 14,177 served and 1,597 of 153,388 archive terms contain a colon (164
  and 1,401 end in one), against **0 and 0** containing `" — "`.

- **Unsectioned back-matter prose reaches the article** (issue #224, filed by
  the maintainer from a JATS parity check against the Swift port in BioMedLit
  — the first open issue here not filed by a PR reviewing an earlier fix. It
  is *not* the first that loses content the document carries: issue #124 is
  open, predates it by eighteen days, and drops table and figure footnote prose
  entirely.)

  `_append_prose`'s unsectioned branch was gated on `in_body` alone. `<sec>`
  is optional in `<back>` as well, and `<ack>`, `<notes>`, `<fn-group>`,
  `<app>`, `<glossary>` and `<bio>` routinely hold a `<p>` directly — which is
  where funding acknowledgements and competing-interest statements live, so
  every one of them was dropped. The Swift port routes both and its own
  comment names the same consequence.

  **The population is the largest this module has measured, and it is a tally
  of what the routing does rather than of what the markup holds.** Counted by
  instrumenting `_append_prose` itself, over the 8,118 served articles of
  Europe PMC's named OA package `PMC10030002_PMC10040000.xml.gz`, **5,990
  (73.8%) gain at least one such paragraph** — 40,342 paragraphs, 5.91 MB of
  prose. Over the 97,909 articles of PMC's
  `oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26.tar.gz` (the archive
  rendition), **82,058 (83.8%)** and 541,481 paragraphs. By the `<back>` child
  that owns them, served / archive: `<fn-group>` 13,650 / 147,635, `<notes>`
  10,286 / 192,002, `<glossary>` 10,693 / 113,468, `<ack>` 4,892 / 61,319,
  `<app-group>` 618 / 24,741, `<bio>` 203 / 2,316. Neither corpus is committed
  here, so both are quoted from their named public artifacts rather than
  re-derived by a test — but every row is an input under test, one case per
  container, because a table quoted in six files and driven by four cases is
  the shape `TestTheStatedCountsAreWhatTheCorpusHolds` exists to break.

  **The first cut of that table was a raw-XML walk and did not survive its own
  arithmetic.** It counted paragraphs this branch never reaches — whitespace-
  only ones, and `<p>` inside a back-matter float — so every row was
  overstated, its archive rows summed to 136 fewer than the total printed
  beside them, and the two figures it gave for the same population, 40,645
  encountered against 40,341 inserted, were never reconciled. Both columns now
  sum to their own totals exactly. A count is of what you looked for.

  **And the two instruments now agree, which is the check the first cut
  skipped.** Re-derived over all 8,118 served articles by parsing each one
  twice, once on `main` and once here: the routing tally and the corpus diff
  both read **5,990 articles and 40,342 paragraphs**, on the same article set,
  with `main`'s paragraph list a subsequence of this branch's in all 5,990. So
  the earlier blast-radius row — 5,989 / 40,341 / 6,977 `body_sections`, and
  the 2,324 the prefix test reported — was one article short throughout, its
  harness having skipped one, and the gap that left between the tally and the
  diff was the harness rather than the two measuring different events. Two of
  bmlib's own counts never settle in favour of the weaker one, and here they
  do not have to: they are the same number.

  **`<ref-list>` is the one refusal, and it is a misfiling rule rather than a
  taste.** A `<ref>`'s `<note>` and a `<ref-list>`'s own `<p>` are bibliography
  apparatus: sampled from the served package they read *"Faculty Opinions
  Recommendation"* ten times in one article, *"Papers of special note have
  been highlighted as: ..."*, and bare DOI fragments. Appended to
  `body_sections` they become paragraphs of an article that never carried
  them — a corruption where the alternative is a blank, which is this module's
  own preference (issues #116, #162) — and issue #150, which puts a note-only
  `<ref>` where it belongs, would then be left with its content misfiled
  rather than missing and its symptom invisible. 163 paragraphs in 39 of the
  8,118 served articles (0.40% of the 40,505 offered to this branch), 1,311 in
  293 of the 97,909 archive ones (0.24%). It is also the one place this module
  and the Swift port deliberately differ, filed there so a later parity check
  does not "reconcile" it — though the note is asymmetric: this side carries it
  in code and in `docs/DECISIONS.md`, that side only as its own open issue.

  **And it is reported.** `refused_apparatus_prose` counts it and `_audit_parse`
  emits one WARNING per article, the granularity `rejected_spans` (#129) and
  `formulas_dropped` (#177) both settled. Leaving it silent would have been the
  wrong answer twice over: on `main` this prose was incidental collateral of a
  branch gated on `in_body`, while here the refusal is named and argued, which
  earns a line rather than excusing one — and #150 is a downstream that cannot
  learn the content existed without it. A `<disp-formula>` refused by the same
  rule goes to the same counter and **not** to `formulas_dropped`, or a policy
  this module chose would print as a gap in it.

  **One counting site, and a first cut had two.** The `<disp-formula>` arm
  incremented `refused_apparatus_prose` and then called `_append_prose`, whose
  own refusal arm re-evaluated the same predicate on the same state and
  incremented it again — so one refused formula reported as two, in the
  counter this change added so a downstream could size the loss. The arm now
  *subtracts* the refusal from `formulas_dropped` and counts nothing itself.
  What let it ship is that both WARNING tests asserted the substring and never
  the number; they assert the count now, and the mutant that restores the
  second increment reddens one of them. Measured live at **0 occurrences** in
  both corpora, so no published figure moves — but the shape is reachable from
  valid JATS and it is the PR's own fixture that exercises it. The line also
  reads *"item(s)"* rather than *"paragraph(s)"* now, a rendition not being a
  paragraph.

  **Review-round corrections to the measurements, re-derived rather than
  argued.** Parsing all 8,118 served articles twice, once on `main` and once
  here, the routing tally and the corpus diff **agree exactly** — 5,990
  articles and 40,342 paragraphs, on the same article set, with `main`'s
  paragraph list a subsequence of this branch's in all 5,990. The blast-radius
  row first published here was one article short throughout (5,989 / 40,341 /
  6,977 sections, and the 2,324 the prefix test reported), its harness having
  skipped one; corrected above. The `<glossary>` archive count is **113,468**,
  not the 113,444 that reached `ROADMAP.md` and `HANDOVER.md` from issue
  #228's body — 113,468 is the value the archive column sums with.

  **The rule is scoped to the unsectioned branch**, which "the one refusal"
  reads wider than. Prose under an open `<sec>` never reaches the predicate, so
  a `<ref-list>` inside a `<back>` `<sec>` keeps its apparatus, and so does one
  in `<body>`. Both are pre-existing and both measure near-empty — 0 apparatus
  paragraphs in 0 of the 8,118 served articles, 1 in 1 of the 97,909 archive
  ones — so it is a scope to state, not a hole to close.

  Nothing else is refused, because every other container here already routes
  this way *inside* `<body>` — a `<def-list>`'s `<def><p>` in a body `<sec>`
  reaches that section today — so refusing one in `<back>` would make the same
  markup mean two different things depending on where the publisher put it.
  `<glossary>` is routed on that argument even though it arrived without its
  `<term>` (#228, since answered) and #231 is what the resulting untitled
  section costs a reader.

  An **ancestor** test on `element_stack` rather than the `in_ref_list` flag:
  JATS permits a `<ref-list>` inside a `<ref-list>`, and the flag is a bare
  boolean the inner close clears, which would re-admit the outer list's
  remaining apparatus — issue #115 one element family over.

  **`has_body` is untouched, and that is the load-bearing half.**
  `body_paragraph_count` still counts `<body>` prose alone, so an article that
  is front matter plus back matter is still body-less and `FullTextService`
  still holds it back rather than caching it and going no further. Diffed
  against `main` over all 8,118 articles of the served package: `has_body`,
  `figures`, `tables`, `references` and `abstract_sections` move in **0**
  articles.

  **A pending section per container, because one would have hidden a defect in
  the other.** `</body>` and `</back>` each flush unsectioned prose. Held in
  one slot, a `</body>` flush that failed would leave its prose pending, the
  `<back>` that follows would append to the same builder, and `</back>` would
  emit the pair as one section — the article silently losing the boundary
  between its body and its acknowledgements, with nothing stranded for
  `_audit_parse` to report. At least 73.8% of the served corpus carries a
  `<back>` — that is the share gaining prose, so a `<back>` holding only a
  `<ref-list>` is not in it and the true share is higher — so
  almost every document would mask it. `_flush_implicit_section` picks its slot
  from `in_body` / `in_back`, which is what makes each arm's flush-before-clear
  ordering load-bearing rather than decorative — it was neither while the
  helper emptied whatever was pending, and a comment asserted otherwise for two
  revisions.

  **Blast radius, measured by diffing a corpus rather than argued from the
  call graph.** Prose moves in **5,990 of 8,118 articles (73.8%)**, and in
  every one of them `main`'s paragraph list is a **subsequence** of this
  branch's — 40,342 paragraphs and 5.91 MB inserted, **0 lost, 0 altered**.
  `body_sections` gains 6,978 entries and `html_content`, which is what
  `FullTextService` caches, moves in the same 5,990. A downstream holding
  cached full text should re-fetch.

  **The larger half of issue #177 is answered by this.** That issue contained
  a rendered `<disp-formula>` in an unsectioned `<back>` — 192 in 23 of the
  97,909 archive articles — as `formulas_dropped`, and named the remedy it
  deliberately did not take: *"giving `<back>` prose an implicit section the
  way `<body>` has one"*. Those formulas now reach the article, and the test
  that pinned the containment is reversed with a comment saying which issue
  overturned it. What is left of #177 is its second, latent shape — a formula
  inside a `<fig>`/`<table-wrap>` with no `<caption>` open, measured 0 in both
  corpora — which now has a test of its own so the counter cannot go quietly
  vacuous.

- **A request whose answer is known before it leaves is not made** (issue
  #188, filed from PR #189's review and blocked until PR #213's instrument
  sized it).

  `_check_europepmc` addresses the full text with `record["pmcid"] or
  record["id"]`. For a `PMC` record and for a `PPR` preprint that fallback is
  the accession; for a `MED` record carrying no `pmcid` it is the **PMID**,
  and a PMID addresses nothing at `fullTextXML`. Two costs, and the second
  persists: a rate-limited request per such analysis, whose 404 was
  determined before it left, and a stored `FullTextStatus.NOT_SERVED` —
  documented *"requested and not served"* — for an address **bmlib chose**
  rather than one Europe PMC declined. That is the issue #187/#190/#191
  defect once more: a claim in Europe PMC's mouth that only their 404 to a
  real address makes.

  A request is now made only for an identifier matching `(?:PMC|PPR)\d+`.
  Anything else stores `NOT_ATTEMPTED` and logs at DEBUG.

  **`NOT_ATTEMPTED` rather than a member of its own**, and the reading is
  exact: the member says *"no request was made, and Europe PMC's own answer
  is why"*, and the record **is** their answer — it names no accession for
  this article. Issue #207 is the observation that the same member's sibling
  cause (a record claiming `inEPMC: Y` and carrying nothing at all) makes
  that sentence false; this cause makes it true. Two guards, two levels, one
  status: that one is malformed and WARNs, this one is ordinary and does not.

  **A shape test, not a `source` allow-list.** The measurement reads *"the
  record's own `source` is what separates them"*, which is true and is not
  the rule that got written: the two agree on every population drawn, and
  they differ where an accession-shaped identifier arrives under a source
  nobody has enumerated — where the allow-list refuses a fetch that would
  have worked. It is **not** a deletion of the `or id` fallback either:
  `SRC:PPR AND IN_EPMC:Y` is 75,841 records whose only address is that `id`.

  **Measured, with a denominator, by the sampler change above** (2026-09-09,
  123 records at the documented defaults): **0 of 43** bare-`id` addresses
  served, 95% CI `[0.0%, 8.2%]`, against **6 of 9** accession addresses. The
  fix drops 43 of the 52 full-text requests that draw would have made — one
  per 35.0% of records analysed. Read the 0 as an upper bound rather than a
  proof: those 43 are a contiguous cursor page's worth of `SRC:MED` records,
  and the issue's own evidence is that such records are NCBI Bookshelf
  chapters whose `bookid` is not addressable here either.

  **The larger population beside it is recorded and not acted on.** The same
  probes cross-tabulate by `isOpenAccess` — the flag bmlib does not read, and
  the one that would actually predict whether `fullTextXML` serves — at `N`
  0 of 3 and `Y` 6 of 49. Three probes settle nothing, and a gate narrowed on
  a floor silently loses an article that would have been served.

  **Stored values move**, which is why this was filed rather than folded into
  issue #184. Every such record's `full_text_status` moves `NOT_SERVED` →
  `NOT_ATTEMPTED`, and its provenance line moves from *"EuropePMC served none
  for this article"* to *"no EuropePMC full-text request was made"*. Where the
  request was refused or dropped rather than answered it moves
  `REQUEST_FAILED` → `NOT_ATTEMPTED` instead, and a WARNING stops being
  emitted for it. Nothing else moves: no full text was scanned on either
  path, so the score, `coi_disclosed` and `data_availability_level` are what
  they were. (This named only the 404 transition, which is the modal case and
  not the whole of it — PR #219's review.)

- **No JSON shape a remote can send escapes the public `analyze()`** (issue
  #199, from PR #195's review; corrected and completed by PR #208's review).
  `analyze()` documents that a dead or misbehaving API costs a *component*
  and not the analysis, and it wraps none of its steps — so anything a reader
  raises leaves a public method.

  **Measured against `main` with the corpus this branch commits**
  (`_HOSTILE_BODIES`, 43 bodies × 2 identifier columns = 86 rows):
  **48 rows escape, 24 in each column** — 40 `AttributeError`, 6 `TypeError`,
  2 `KeyError`, every one a `_BUG_TYPES` member, so had they been caught one
  layer down they would have been reported as a bmlib defect they are not.
  Per column that is 20 / 3 / 1. *A first cut of this entry said 23 = 18 + 4 +
  1; no committed instrument re-derived it, the `18` was the count of `.get()`
  escapes carried into the exception tally, and the corpus had grown since.
  Quote the figures above, which the suite reproduces.*

  **The escapes divide by where a guard can go, not by the expression.** The
  issue names four consumers calling `.get()` on an undecoded body — 18 of
  the 24 per column. **Six** are not a `.get()` at all: two `.lower()` calls,
  two `>` comparisons, and `result[0]` raising `KeyError: 0` for an object
  and `TypeError` for a scalar. *A first cut said five, counting the two
  `result[0]` shapes as one.*

  **Twelve: the body is not a JSON object.** JSON's top level may be an
  array, a string, a number, `true` or `null`; the four *truthy* shapes at
  three endpoints are the twelve, `null`/`[]`/`false`/`0` being refused a step
  earlier by the callers' own `if cr:` / `elif epmc:` / `if oa:` and
  ClinicalTrials.gov having been guarded already. `_request_json` promises an
  object now — `dict[str, Any] | None` — and reports a non-object 200 at
  WARNING, naming the type **and the URL**, in the voice it already uses for a
  body that will not parse. The refusal belongs at that layer by `_request`'s
  own rule read the other way round: that helper pushes the *consequence* out
  to its callers because each loses something different, while the *body* is
  this layer's subject already. It also makes `_query_crossref`,
  `_query_europepmc` and `_query_openalex`'s existing `dict | None`
  annotations **true** — they were false, and invisible to mypy only because
  `_request_json` returned `Any`. An endpoint whose 200 legitimately carries
  an array wants its own helper rather than a flag on this one, which is
  `_request_text`'s rule about `headers` one method down.

  **Twelve: a value inside the object has the wrong type.** No boundary guard
  reaches these — the object arrived and a value is wrong — so they are read
  through coercers at the point of use. `_json_object` replaces
  `x.get("k", {})`, which returns its default only for an **absent** key: a
  key present with `null`, or with an array, hands the reader the wrong type
  and the next `.get()` raises, which is the same shape as the defect
  recorded against `fulltext`'s `_extract_free_pdf_url` one package over
  (there the `None` is *iterated* and raises `TypeError`; same cause,
  different exception). `_json_text` replaces `(x.get("k") or "")`, which
  rescues `null` and passes an object straight through to the `.lower()`
  after it. `_json_count` excludes `bool` although it is an `int` in Python,
  or `"cited_by_count": true` awards `SCORE_CITED` for a body that stated no
  count at all.

  **`_json_bool` is the fourth coercer and was measured differently** (PR
  #208's review): a wrong-typed boolean **raises nothing**, so no contract net
  can see it. `bool("no")` is `True`, so ClinicalTrials.gov stating *no
  results* was stored as `TrialResultsStatus.POSTED` with
  `trial_results_compliant=True` and `SCORE_RESULTS_POSTED` awarded — a false
  claim in the affirmative about a trial, at the exact site issue #194 made
  one for a whole release — and `{"is_oa": "false"}` awarded
  `SCORE_OPEN_ACCESS`. Both are truthy strings, so `bool()` **inverts** the
  remote's answer rather than merely losing it. `None` is its absent value
  rather than `False`, which the existing tri-state absorbs with no new
  vocabulary: `_check_trial_registration` already routes it to
  `REQUEST_FAILED` + `_INDICATOR_RESULTS_NOT_CHECKABLE`. An **absent**
  `hasResults` deliberately keeps its old `False`, that being a well-formed
  body whose stored value this change does not move — filed as issue #210.

  **`_epmc_records` truncates the record list; it does not filter it** (PR
  #208's review). Europe PMC returns best-match-first and every reader takes
  `records[0]` as *this paper*, so an index is a rank: a filter whose head
  record was malformed silently promoted the one behind it and made a
  **different article** the subject — its trial accession, its PMID sent on to
  efetch, its abstract scanned for COI. That is worse than the `KeyError` it
  replaced, which at least said so.

  **The helper serves three readers, and the third was missed.** Its own
  docstring said *two* — `_check_europepmc` and `_find_trial_ids` — while
  `_pmid_from_epmc` kept its hand-rolled copy, so four `_BUG_TYPES` members
  still escaped a public `analyze()` on **any DOI-only analysis**: 2
  `AttributeError`, 1 `KeyError`, 1 `TypeError`. `analyze()` reads `pmid or
  _pmid_from_epmc(epmc)`, so supplying a PMID short-circuits the reader
  entirely, and every row of the first net supplied one — the escapes redden 4
  rows in the DOI-only column against this branch's own first commit, and 0 in
  the other. **The identifier is now an axis of the net**, with its own
  anti-vacuity assertion that the record-derived PMID actually reached
  `_check_pubmed`; the endpoint-level assertion could not see this, EuropePMC
  having been requested either way.

  That rule is now **mechanised rather than stated**:
  `TestOnlyTheHelperWalksTheEuropePMCResultList` walks `analyzer.py` with
  `ast` and fails on any function outside `_epmc_records` naming
  `resultList`, docstrings excluded. *A rule enforced by prose is not
  enforced* — `TestTheAuditNetIsComplete`'s lesson one package over, and this
  rule had already slipped in the commit that wrote it down.

  **A body that was served is not reported as one that carried nothing.**
  CrossRef's funder branch was two-way, so a `funder` arriving as
  `{"name": "Acme Pharmaceuticals Inc"}` stored *"No funder information in
  CrossRef"* — a false claim about a record that named an industry funder. It
  splits three ways now: absent or empty gets `_INDICATOR_NO_FUNDER_INFO`,
  and an unreadable shape gets `_INDICATOR_FUNDERS_NOT_READABLE`, which is
  issue #191's rule one endpoint over and the distinction
  `_INDICATOR_RESULTS_NOT_CHECKABLE` already draws. For the same reason a
  record's `source` and `pmcid`/`id` are coerced before they are interpolated
  into a URL: a mistyped accession is truthy, so it used to spend a
  rate-limited request on `.../{'a': 1}/fullTextXML` and store the 404 as
  `FullTextStatus.NOT_SERVED` — a claim in Europe PMC's mouth for a URL bmlib
  mangled.

  `_check_trial_results`' own `isinstance(data, dict)` is reached only for
  `None` now, and is **kept rather than narrowed**: the second of two
  independent protections at the one site where an unusable body did not
  merely raise but published a false finding about a trial for a whole
  release (issue #194) — the redundancy issue #203 argues for, at the place
  with the worst measured cost. That defence was itself unpinned until PR
  #208's review — narrowing it to `data is None` passed the entire suite,
  `_request_json` having closed the only path that reached it — so
  `test_an_unusable_body_is_refused_at_this_site_too` stubs the boundary,
  which is the only way left to exercise what it defends.

  **The net is the deliverable, not the guards.** It drives `analyze()` with
  every one of these bodies at every endpoint, on both identifier shapes, and
  asserts the contract — keyed on what must hold rather than on the
  expression that happened to break.

  **A contract net is blind to a value read *wrongly* without raising.**
  Mutation testing found the first instance and PR #208's review found three
  more. Dropping the `isinstance` on CrossRef's `funder` credits CrossRef
  with funder information it did not send — 15 points, silently — because an
  object is truthy and iterates into its keys; `hasResults` and `is_oa` are
  the two above. Every one is now pinned by an assertion on what the analysis
  *concluded*, and the `hasResults` row uses `"no"` rather than the `"yes"`
  it first used — the one string whose truthiness coincides with the correct
  answer, so it could never have failed.

  **What moves.** Nothing moves for a well-formed body except through
  `_json_count`: `"cited_by_count": true` and `3.5` are well-formed JSON
  objects that did not raise, and both now score 5 points lower. *A first cut
  of this entry said "nothing stored moves" and offered "no draw has seen
  these endpoints answer 200 with a non-object" as the reason — an argument
  that reaches only the twelve top-level escapes, the value-level ones being
  objects by construction.* For malformed bodies, stored values move by
  design at every site above. And **no draw has looked**: nothing in this
  repo measures the shape of a 200 body, `ProbeOutcome` carrying HTTP
  statuses only — filed as issue #211.

- **Every Europe PMC full-text fetch 404'd, so the module scored every
  open-access paper on its abstract** (issue #184, found while measuring for
  issue #183 rather than by review). `_fetch_europepmc_fulltext` built
  `.../rest/{source}/{ext_id}/fullTextXML` — a two-segment path Europe PMC
  answers with its own HTTP 404 (their CORS headers, `content-length: 0`, so
  theirs and not a proxy's). The form that serves is the single-segment one
  `bmlib.fulltext.service` has always used, so the two modules disagreed and
  `transparency` was the broken one.

  Measured against the live API rather than read off the documentation: the
  single-segment form serves HTTP 200 for `PMC12900525`, `PMC3258128`,
  `PMC10030002`, `PMC13426601` and six `PPR` accessions, while
  `{source}/{ext_id}`, the bare numeric id and the PMID all 404 — as do the
  sibling two-segment endpoints `textMinedTerms` and `supplementaryFiles`, so
  it is the path *shape* and not one endpoint or one article. **An article is
  addressed by its Europe PMC accession alone**, and that accession is **not a
  PMCID**: 75,760 of Europe PMC's 12,220,678 `IN_EPMC:Y` records — 0.62%, from
  their own hit counts rather than a draw — are preprints carrying no `pmcid`
  at all, addressed by a `PPR…` accession that `fulltext`'s
  `_normalise_pmc_id` would reject. So the two modules now **agree on the
  base** and deliberately not on the identifier; a test pins the first and
  would fail on the second. They agree by assertion, not by sharing: each
  still holds its own literal (`transparency`'s `EUROPEPMC_REST_BASE`,
  `fulltext`'s `EUROPE_PMC_BASE`), because importing across would be the
  runtime dependency `transparency` deliberately does not have. What the one
  new constant unified is this module's **own** two literals — the search call
  and the full-text call, which is where the drift happened.

  The guard beside it lost its `source` half at the same time. It refused to
  fetch unless *both* `source` and `ext_id` were present, which was right
  while the source was a path segment and is over-strict now that it addresses
  nothing — a guard kept past its reason refuses a fetch that would work.
  `source` still names the subject of every log line.

  **Stored values are not comparable across this fix**, and the radius is
  measured by diffing real analyses rather than argued from the call graph.
  48 articles drawn `IN_EPMC:Y AND OPEN_ACCESS:Y AND HAS_DOI:Y`, stratified
  across two sources and four publication years, each analysed twice in one
  process — once as it is, once with the full-text step forced to the
  `NOT_SERVED` that `main` produces once every request 404s, with every other
  API hit for real both times. All 48 reached full text, and:

  | field | moves | note |
  |---|---|---|
  | `full_text_analyzed`, `full_text_status`, `risk_indicators` | 48 (100%) | |
  | `coi_disclosed` | 32 (67%) | `None` → `True` in 19, `None` → `False` in 13 |
  | `transparency_score` | 20 (42%) | `+10` in 19, `+20` in 1; never negative |
  | `risk_level` | 5 (10%) | HIGH → MEDIUM in all five |
  | `tier_downgrade_applied` | 5 (10%) | `1` → `0` in all five |
  | `data_availability_level` | 4 (8%) | `unknown` → `not_available` 3, → `full_open` 1 |
  | `industry_funding_detected` / `_confidence` | 1 (2%) | |

  No article in the draw was scored worse. **That is a property of the draw
  and not a guarantee**: the fix makes the missing-COI downgrade *reachable*,
  since `coi_disclosed` could never previously be set `False`, and a paper
  scoring above `score_threshold` whose full text carries no COI statement can
  now be downgraded where before it could not. Every one of the 13 that became
  a determinate `False` here was already HIGH on score alone (15-20 against a
  threshold of 40), so none of them moved. The five indicator lines that
  appear or vanish are `"COI disclosure status unknown (full text
  unavailable)"` (32), `"COI disclosure found in PubMed record"` (16), `"No
  COI disclosure found in full text"` (13), `"Data explicitly not available"`
  (3) and `"Industry ties disclosed in COI statement"` (1).

  Because the draw requires an open-access record with a DOI, those are
  per-article effects **over articles whose full text the fix restores** and
  not rates over an arbitrary corpus. How large that population is depends on
  the caller's own mix; probed separately over 150 `IN_EPMC:Y` records, no
  `isOpenAccess: N` record served (0 of 53) and `isOpenAccess: Y` still 404'd
  in 35 of 97.

  **Traffic and per-analysis latency move too**, which is a consequence of
  restoring the step rather than of any choice in it, and is not visible in
  the table above. Every one of these requests used to be answered with a
  0-byte 404; each now downloads a full JATS body and lexes it —
  `PMC3258128`, `PMC10030002` and `PMC12900525` measure 94 kB, 99 kB and
  164 kB. A caller analysing a large open-access corpus should expect roughly
  100 kB per article where it previously paid nothing, plus one
  `_strip_nested_articles` pass over it; the 0.35 s inter-request floor is
  unchanged, so throughput is not.

  **The test is the remedy for the silence, not the log line.** A non-200 was
  then the one outcome that deliberately did not warn — narrowed to the 404
  alone by #191, below — so the defect was invisible
  for its whole life; issue #184 proposed raising that level, arguing a 404
  under `inEPMC: Y` is Europe PMC contradicting itself. **That is refuted by
  the measurement above**: `inEPMC` says Europe PMC *holds* the text where
  `fullTextXML` serves the open-access subset of it, so a non-200 is the
  ordinary majority outcome for the gate this module uses and a WARNING would
  fire on every closed-access paper analysed. The URL is logged at DEBUG
  instead — it is what named the defect — and the level is pinned in both
  directions. (That argument is of **404s**, and #191 below narrowed the
  branch to match it: a 429, a 503 and a 403 now WARN.)

  What could have caught it is a test, and there was none: `_FakeFullTextClient`
  matched `url.endswith("/fullTextXML")` and `_RecordingClient` — the fake
  reached through `analyze()`, so the end-to-end path — matched the looser
  `"fullTextXML" in url`. **Both** URL forms satisfy either, so tests that each
  looked like a full-text test asserted nothing about the path. Measured, and
  each number says which tree it is of: with the suffix match, reintroducing
  the defect passes **236 of `main`'s 236**; with both fakes matching the whole
  URL it reddens **52 of this branch's 249**, of which **43 predate the
  branch**. That is the `parser_log` fixture's trick one module over — a net
  that costs nothing because existing tests become URL checks without being
  rewritten — plus **thirteen** new tests: the URL literal, the source's
  absence from it, a `PPR` accession passing through unnormalised, the
  `record["pmcid"] or record["id"]` fallback in both directions, a record
  naming no source and one naming no accession, the subject rendering in both
  directions, the two modules' bases, the search endpoint, and the DEBUG line's
  URL, level and absence of a warning.

  Filed rather than folded in: **issue #188**, a record carrying no PMC
  accession is still fetched by PMID — which never serves — and stores
  `NOT_SERVED` for the guaranteed 404, where `NOT_ATTEMPTED` is the honest
  value. The closed-access population above is recorded there too, both being
  requests whose outcome is known before they are made.

- **A body truncated between tags was scanned as a complete article** (issue
  #183, from PR #182's own review, and the half of issue #160 that fix does
  not reach). Such a body opens no unterminated construct, leaves no region
  open and empties nothing, so all three existing checks passed it. Scanned as
  a whole article it yielded `coi_disclosed=False` with the indicator `"No COI
  disclosure found in full text"` — for a disclosure that was in the lost tail
  — which is the missing-COI HIGH downgrade fired on evidence that does not
  exist, **with nothing logged at any level**. Where issue #160's half is loud
  and merely costs the article its full text, this one was silent and scored
  it wrong. It is now refused, WARNed, and fallen back from like every other
  refusal, which leaves COI *unknown* rather than absent.

  **The issue's own remedy is refuted by measurement.** It proposed
  `xml.rstrip().endswith("</article>")`; trailing comments, PIs and whitespace
  after the root are legal XML, and **1,727 of the 97,909 archive articles
  (1.76%) and 23 of the 8,118 served ones (0.28%)** end
  `</article><!--requester-ID …-->`, so that check refuses complete articles
  at a real rate. Testing for the **presence** of the end tag instead is
  cheaper and exact — a truncation removes the tail and the root's end tag is
  in the tail — and refuses **0 of the 97,909 archive articles**. That zero is
  of the archive half alone: the served draw is one concatenation split into
  articles on `</article>`, so containment there is true by construction and
  pooling the two into a single 106,027 would report a tautology as evidence.
  The 1,727/23 counts above are unaffected, measuring the gap *between* one
  article's end tag and the next article's opener. `</sub-article>` does not
  contain the substring, so what the strip removes cannot affect it. It is still not a well-formedness check, which would be
  the second parse PR #159 and PR #182 both declined.

  **The four refusals are ordered most-specific-first, and the order is
  load-bearing.** A truncated body can satisfy several at once — truncation is
  the cause and the rest are symptoms — and each of the other three knows
  something this one does not. Ahead of the lex it would make issue #160's
  construct-and-offset message unreachable for the input that most often
  produces it — a *corrupted* body still carries `</article>` and reaches the
  lex; ahead of the entirely-nested report it would make that unreachable too,
  a body of nothing but `<sub-article>` carrying no `</article>` either. Three
  ordering tests pin it.

  One residual is stated rather than implied: httpx raises on a Content-Length
  or chunked-framing mismatch, so reaching this needs a proxy that truncates
  *and* re-frames. Whether Europe PMC ever serves a legitimately partial body
  at HTTP 200 is **not measured**, and the local served draw is not evidence
  either way: its articles were split on `</article>`, and it was written by an
  importer that may have discarded failures. Narrow, not zero.

- **The nested-article lexer assumed well-formed input in two ways that cost
  the article, and enforced neither** (issue #160, from PR #159's own review).
  `_strip_nested_articles` in `bmlib/transparency/analyzer.py` documented its
  input as *"a `fullTextXML` body as Europe PMC served it"* and left both
  consequences of that assumption unhandled. Neither is reachable from a
  publisher's deposit — **0 of 98,789 articles across both corpora** carries
  either shape — but both are reachable from a truncated HTTP-200 body, which
  is a network product rather than a deposit.

  **An end tag now closes only the element that opened the region.** As a bare
  depth, `</response>` closed a region a `<sub-article>` had opened, and the
  rest of the outer round came back as this article's prose — issue #119's own
  defect, reached through the fix for it. The regions are a stack of names; a
  mismatch closes nothing, so a region left open by one is refused like any
  other.

  **A construct that never terminates refuses the document.** Each of the four
  skip branches scanned to end-of-string when its terminator was absent while
  the scan retried at every later opener, so the lex was quadratic: measured
  here at 33.6s for 256 kB of a repeated `<!DOCTYPE a[` and 33.3s for 224 kB
  of an unterminated tag, every doubling costing about four times the last
  (the issue's own 22.9s is the first shape on another machine), against
  4-9 ms for the corpus's three largest well-formed articles (2.9-3.4 MB). `_HTTP_TIMEOUT_SECONDS` bounds the request and nothing bounded
  this, so such a body did not fail — it stalled, reaching neither the refusal
  nor the warning. Being slow was not the whole of it: with no branch
  matching, the unterminated construct's own content was then read as this
  article's markup, which is what those four branches exist to prevent.
  Refusing at the first opener costs one failed scan and stops; the same
  shapes now take 0.001-0.004s. It is **not** a well-formedness check —
  nothing here would notice a mismatched `<p>` — and neither fix needs a
  constant drawn from a corpus, which the issue's other two remedies (a size
  cap, a multiple of the input length) both did.

  The refusal is a private exception rather than the `None` the function
  already returns, because the two are different claims: an unclosed region is
  a document bmlib will not segment, and this is a document that did not
  arrive. Each WARNs in its own words, naming the construct and the offset;
  collapsing them onto one return value would put issue #161's shape one level
  down. `TransparencyAnalyzer` behaviour is otherwise unchanged — the analysis
  falls back to the abstract exactly as it does for an unclosed region.

  **The literal `<` sits outside the new branch's capturing group, and that is
  worth 7.2x.** `sre` derives a prefix for the whole pattern only when every
  top-level branch opens with the same literal, and then skips from `<` to `<`
  rather than trying the pattern at every position; a branch opening with a
  group defeats that analysis silently. Three configurations over 7.8 MB of
  real articles, and they must be kept apart: **13.4 ms** with no refusal
  branch, **26.6 ms** with it and the literal outside the group, **191 ms**
  with it inside. So the *placement* penalty is 191/26.6 = **7.2x**, and the
  guard's own cost against having none is **1.9x** (0.17 to 0.31 ms on a
  median article) — for a function called once per analysis behind four to six
  HTTP round trips. Multiplying the two together gives the 14x an earlier
  draft of this entry claimed, which compared the misplaced guard against no
  guard at all and so counted the 1.9x twice. Factoring the alternatives
  inside the group recovers ~8% of the penalty and not the penalty. The two
  forms differ by two characters and both pass every behavioural test, so
  `test_every_branch_of_the_lexer_opens_with_the_literal` is what stands
  between them. Re-measured on a re-derivable draw — the first 880 articles of
  `PMC10030002_PMC10040000.xml.gz`, 90.8 MB, nine interleaved runs — 165 to
  297 to 1,959 ms, so 1.80x and 6.6x; the ratios move with the sample and with
  the run (drift reached 27% on one machine), the ordering does not.

  **Review of this change corrected its own account of itself, and added two
  guards.** Three configurations were being reported as two: "13.5 ms with the
  literal outside" is the *no-guard* baseline, so the headline "14x" compared
  the misplaced guard against having none and counted the guard's own 1.9x a
  second time. Both figures were right and both labels were wrong in seven
  files, which no arithmetic check catches — only naming the configurations
  does. Two mutants also survived the suite: one letting a `<response>`-opened
  region close on any end tag (the fixture exercised `<sub-article>` only, and
  the mutant re-admitted reviewer prose verbatim), and one widening the call
  site's `except` to `except Exception`, which is the swallow PR #159 moved
  that call out of the request handler to avoid. Both are pinned now, and the
  refusal branch's non-tag half is **derived** from `_UNTERMINATED_OPENER_NAMES`
  so its `'tag'` fallback is provable rather than hand-enumerated — an
  alternative added to one and not the other used to come out labelled `'tag'`
  with the branch count still 6. Issue **#183** was filed for the half of the
  truncation hazard this change does not reach: a body cut *between* tags opens
  no unterminated construct, so it is scanned as complete and manufactures a
  missing-COI finding, silently.

  **Blast radius is a diff, not an argument**: lexing every article of both
  corpora with and without the change — all 97,909 of PMC's `oa_comm`
  `PMC012xxxxxx` baseline package (archive rendition) and all 880 of a Europe
  PMC draw (served rendition) — **0 of 98,789 outputs differ and not one
  article is refused**. That also measures the contract itself, over 25x the
  3,880 articles the issue sampled.

- **A counter redefined in place is invisible to every sentinel, and a stale
  journal pooled with it** (found reviewing PR #180). #164 changed four
  *first-generation* counters — `figures_with_graphic`,
  `figures_multi_graphic`, `last_is_thumb`, `first_is_thumb` — from a
  whole-subtree walk to the owner-scoped one **under their own names**. Every
  mechanism in `scripts/sample_jats_exhibits.py` detects an *absent* field: a
  generation is a set of names a stale row does not carry, and `NOT_MEASURED`
  is what that absence loads as. `_journal_disagreement` compared
  `(source, rendition, draw)` — everything about *which identifiers* a run
  asked for and nothing about what the sampler did with them — so a journal
  written by the previous commit for the same package, window and seed
  resumed **cleanly** and its rows pooled with fresh ones. Reproduced
  half-stale over the real recent corpus, section 4 printed `2,664  57.9%`:
  neither the owner-scoped 2,658 nor the subtree 2,676, above a heading
  asserting `owner-scoped`, with the only marker a `NOT MEASURED` line naming
  the companion generation. The corpus filename stayed protected — the sixth
  generation's sentinel forces exit 1 — so the artifact was safe and the
  number a maintainer *reads* was not, which is the half the instrument
  exists for. `_COUNTER_DEFINITIONS_VERSION` is a fourth journal-identity
  axis, stamped in the header and compared like the other three; **bump it
  whenever an existing counter starts counting something else**, since no test
  can detect a change of meaning. Renaming the four was refused: the names are
  the corpus's own keys, so a rename regenerates both corpora and moves every
  figure again, destroying exactly the "only the walk moved" attribution #164
  was able to make.

  Four smaller instrument defects came out of the same review.
  **A report section is gated on every counter it *reads*** — the rule #164
  established for 14b — now enforced by an `ast` walk over `print_report`
  (`TestEverySectionIsGatedOnEveryCounterItReads`, with a negative control
  that reverts 14b's gate and requires the walk to name `tex_math`) rather
  than by one hand-written scenario, and `_pct` returns `NOT MEASURED` on a
  sentinel at either end instead of raising a bare `math domain error` out of
  the report. The converse held too: section 4's gate named #162's counter,
  which it does not read. **`Totals.articles_where` raises on a type it does
  not know**, the silent zero it was rewritten to remove having simply moved
  to the next type — `unscoped` is a plain `dict` and read as carried by **0**
  articles over a corpus holding 29 non-empty ones. **`ArticleMeasurement.
  from_dict` is as strict as the write**: a key naming no field used to become
  a phantom attribute that `to_dict` round-tripped into the committed corpus
  where `TestEveryCounterIsInAGeneration` could not see it, and a count
  carrying a string was summed by `sum_of` and skipped by `articles_where`.
  And **`_TRANSPARENT_WRAPPERS` is a scope, not a judgement** — grouped with
  `_ARCHIVAL_HINTS`/`_THUMB_PATTERN`, which must *differ* from the parser's
  sets so a corpus cannot merely confirm the rule under test, while since #164
  it decides every #117 share; it is pinned as identical to the parser's now.
  Beside them, a run that can measure nothing — every drawn identifier already
  journalled at an older generation — says to delete the journal rather than
  repeating "re-run to fill it" at exit 1 for ever.

- **Stale cross-corpus sums** (found reviewing PR #180). The back-filled
  window going 997 → 1,000 reconciled every per-window figure and neither
  figure cited as a **sum over both**: "1,994 articles" (997 + 1,000 = 1,997)
  in `bmlib/fulltext/jats_parser.py` ×5, `bmlib/fulltext/models.py`,
  `CLAUDE.md`, `ROADMAP.md`, `CHANGELOG.md` and a test docstring, and
  "12,650 `<contrib>`" (7,798 + 4,861 = 12,659) in four files — one of them
  eleven lines above an assertion of `(7798, 4861)`, a file contradicting
  itself. `models.py` is the sharpest instance: the paragraph below it exists
  to record that the *previous* redraw's reconciliation missed that docstring.
  Both sums are now asserted against the corpora, which is the guard that was
  missing — a cross-window sum is exactly the figure a per-window assertion
  cannot see.

- **The #162 label owners were measured, committed, and printed nowhere**
  (found reviewing PR #180). `unlabelled_exhibit_label_owners` exists so the
  seven exhibits' labels need not be read out of `label_parents`, which pools
  every exhibit in the draw — and `print_report`'s section 1 still told the
  reader *"Section 2 says what those labels belong to"*, which is that pooling.
  Section 1 prints the owners now (`{fn: 9, list-item: 67}`, in 7 articles)
  and points at them. `docs/DECISIONS.md` records that the refutation is
  re-derivable from the corpus rather than resting on the 2026-09-02 live
  fetch of seven articles. Section 4's companion block prints the subtree
  *count* (2,676) and not only its share, labels the difference as what the
  subtree **overcounted** — `subtree - scoped` is non-negative by
  construction, so the old `{:+d}` could only ever print `+18` for a
  reduction — and says how many articles the scoping changes (**4 of 997**,
  each losing every multi-graphic figure it had), since a bare total reads as
  diffuse where this is a per-publisher property.

- **The figure-side graphic walk counted what the parser does not route**
  (#164). `scripts/sample_jats_exhibits.py` scoped its **table** counters to
  the owner test the parser uses when #135 was answered, and left the
  **figure** ones on a whole-subtree `el.iter()` walk, because every share
  #117 cites was of that walk and re-scoping them silently would have
  invalidated each one. The argument that the asymmetry cost nothing was
  refuted by the corpora committed in PR #163 and is not restated: it read
  *"both committed draws record zero nested exhibits and every foreign owner
  is a `<td>`, which can only sit under a `<table-wrap>`"*, and the recent
  window holds **7 nested `<fig>`** — all in `PMC12143881`, eLife's figure
  supplements — and **three** foreign owners, `<td>` 82, `<inline-formula>`
  69 and `<disp-formula>` 2. An `<inline-formula>` sits wherever prose does,
  including in a figure's own caption, so it is confined to no exhibit at all.

  **What the scoping costs, on the rendition the shares are of: 18 figures.**
  `figures_multi_graphic` goes 2,676 → 2,658 and 58.1% → **57.8%**
  [56.3-59.2], each inside the other's interval. The other three counters do
  not move at all — `figures_with_graphic` stays 4,602, `last_is_thumb` 2,639
  and `first_is_thumb` 0 — and the back-filled window is untouched by the
  scoping on every count (its own totals do move with the redraw, which served
  three more articles: see below). So #117's ranking rule keeps its evidence: around half of all figures
  carry several deposits and end on a thumbnail, and none deposits one first.

  **That is not the size #164 expected, and the reason is a denominator.** The
  issue's spot check over the same articles' *archive* bytes moved the
  multi-graphic count 77 → 58 and read as "large enough to matter". The
  absolute correction is almost identical on the two renditions — 19 figures
  there, 18 here — but the archive holds 77 multi-graphic figures against the
  served rendition's 2,676, so the same handful of figures is a quarter of one
  population and two thirds of one percent of the other. *A share is of a
  denominator, and the rendition chooses the denominator.*

  **Both readings are kept per row**, which is what makes this a measurement
  rather than a silent application. A redraw ordinarily moves the sample, the
  served bytes and the walk at once, and PR #163's own rule is that three
  simultaneous causes make a movement unattributable — so
  `_FIGURE_SCOPE_COUNTERS` records what the subtree walk said beside what the
  parser routes. Two of the three did not move here: the recent window's 997
  identifiers are the previous draw's, and every counter the scoping does not
  touch came back **identical**. That is a property of one redraw, not a
  promise, which is exactly why the companions are per-row.

  Beside it, **what owns a `<label>` inside an exhibit carrying none of its
  own** — the set a descendant-search fallback would fire on, and the whole of
  what refuted #162. Settling that cost a live fetch of the seven articles,
  because the corpus held the counts and not the owners. It holds them now:
  **7 exhibits in 7 articles, whose labels are 9 `<fn>` markers and 67
  `<list-item>` bullets** — the two containers #116 was about, not one of them
  an exhibit's own label, so the fallback would corrupt 7 of 7.

  The back-filled window is redrawn too and Europe PMC served all of it, so
  that corpus is now **1,000 articles** where it was 997; its 627 figures and
  0 `<table-wrap>` are unchanged, the three added articles carrying neither.

- **A counter generation reached the registry and not the sentinel** — found
  while testing the above, and live on both committed corpora.
  `ArticleMeasurement.from_dict` splatted a hand-written list of generation
  tuples *beside* `_COUNTER_GENERATIONS` rather than reading it, and
  `_FORMULA_ROUTING_COUNTERS` — registered by the commit that added it — never
  reached that list. Every row of both corpora, written before that generation
  existed, therefore loaded its three integer counters at `0` instead of at
  `NOT_MEASURED`; `print_report` read them as measured, and section 14b printed
  `<tex-math> inside a <td>/<th>: 0  0.0%` over a population nothing had
  counted — on the very population #147's live-corruption fix rests on
  (24,476 deposits in 856 of 97,909 articles). That is exactly the collapse
  the sentinel exists to prevent, and `TestEveryCounterIsInAGeneration` could
  not catch it: it checks the registry, and the registry was right.

  The registry is the single source now, and
  `TestASentinelReachesEveryGenerationsCounters` checks the other end. A
  `Counter` field takes no sentinel — there is no negative dict, and
  `counter_of` would raise updating from an `int` — so a generation is
  detectable as absent through its *integer* counters, and none consists of
  anything else. And **a section is gated on every counter it reads**, not on
  the generation it is named after: 14b divides by `tex_math`, which belongs
  to the generation before it, so a row fresh for the routing counters and
  stale for the waiting ones reached `wilson()` with `n = -1` and raised out
  of the report, losing the other fourteen sections with it. The mutation
  sweep found that one — six of seven mutants died to the tests as written,
  and the survivor was the gate.

  **The counters this unblocked corrected a figure in `jats_parser.py`.**
  `_DISPLAY_FORMULA_MERGE_PARENTS` cited 116,623 of 150,598 (77.4%) display
  formulas sitting inside a `<p>`, measured over the *archive* bytes of the
  whole `PMC012xxxxxx` package and written down as though it described what
  the parser is fed. On Europe PMC's `fullTextXML` the recent corpus measures
  **714 of 1,915 (37.3%)**, with `<sec>` the commoner parent at 1,199 — and
  the 880-article served draw the same fix quotes measured 201 of 654 (30.7%).
  The two served measurements agree and the archive is the outlier, so a `<p>`
  is the **minority** parent on the bytes that reach this code. The routing
  rule is unaffected, both parents being handled; only the share and the
  "commonest shape" wording were wrong for this rendition.

- **An article carrying a `Counter` population counted as none of them.**
  `Totals.articles_where` borrowed `_as_count`, which flattens a `Counter` to
  `0` so that `Totals.measured` stops raising on one — a right answer to a
  different question. Every `Counter`-backed population therefore read as
  carried by no article, and the report printed
  `<title> inside a <sec>, owned elsewhere: 411   in 0 articles   0.0%` over a
  population the corpus holds in more than a hundred. Only the report was
  wrong, the prose figures being derived from the corpus rows directly — but a
  maintainer running the sampler is the whole audience the instrument has. The
  test beside it did not catch it because its fixture populates no `Counter`
  at all, so the assertion held for the wrong reason.

- **A formula reaches the prose that contains it** (#147). Two constructs lost
  their content, in the two ways a text-accumulating element can. `<tex-math>`
  accumulates a buffer and is not inline, so an `<inline-formula>` merged an
  empty one and the sentence rendered with a hole in it. `<disp-formula>`
  accumulates with no handler at all, so a display equation was popped and
  discarded whole — its LaTeX, its MathML **and** the `(1)` that body prose
  goes on to cross-reference.

  The rule is **choose one rendition, at the formula element**, held until the
  formula closes because either encoding may be deposited first — 4,377 of the
  package's 188,473 both-encoding formulas are MathML-first, though **in 37 of
  its 97,909 articles at ~118 apiece**, so that is one publisher's house style
  rather than a rate and a 997-article draw expects none of it. The
  measurement is what rules out the obvious alternative: 1,087 formulas in the
  committed recent corpus, and 188,473 across PMC's `oa_comm_xml.PMC012xxxxxx`
  baseline package, carry a LaTeX *and* a MathML encoding of one expression,
  so adding `<tex-math>` to `_INLINE_ELEMENTS` would print each of them twice.
  LaTeX wins where a `<tex-math>` arrived; the formula's own buffer serves
  otherwise, and *otherwise* is the common case rather than a fallback — it
  carries the MathML flattening (10,202 against 1,398 `<tex-math>` in that
  corpus), a formula deposited as ordinary `<italic>`/`<sub>`/`<sup>` markup
  (71 of the 141 encoding-less display formulas in an 880-article local
  draw), and a MathML deposit bound to some prefix other than `mml`, which
  therefore keeps exactly its old behaviour instead of depending on a literal
  prefix match the way #128 does.

  **The LaTeX is a whole document, not an expression**, which is why the fix
  is not a merge. 99.9% of 4,422 sampled deposits in that package are
  `\documentclass[12pt]{minimal}` … `\begin{document}` …, so merging the
  element's text raw would inject some 300 characters of `\usepackage` lines
  per formula — worse than the drop it replaces. Every one of the 7,769 sampled
  *document-wrapped* deposits carries exactly one
  `\begin{document}`/`\end{document}` pair, and
  so do 147 of 147 in two articles fetched live from Europe PMC, the rendition
  bmlib is actually fed. 96.0% of the bodies already carry `$$…$$` and 3.7%
  `$…$`, so the depositor's own delimiters are kept and a pair is added only
  where there is none.

  **A live corruption went with it**, and no buffer rule reaches it: a table
  cell collects its text in `characters()`, not from a buffer, so the LaTeX
  document was pasted into the rendered table verbatim — 24,476 `<tex-math>`
  in 856 of that package's 97,909 articles sit inside a `<td>` or a `<th>`.
  The cell now takes the same one rendition the prose does.

  Two routing rules, both measured. A `<disp-formula>` inside a `<p>` is
  **merged into that paragraph** — 116,623 of the package's 150,598 (77.4%)
  and 201 of the local draw's 654 sit there, and emitted as a paragraph of its
  own each would land *ahead* of the paragraph it interrupts, the enclosing
  `<p>` not having closed yet. One deposited as a block child stands as its
  own paragraph, routed exactly as a `<p>` is, prefixed by its `<label>`
  (1,459 of the corpus's 1,915 display formulas carry one). And the number is
  printed **only** where the equation stands apart: merged into a sentence a
  bare number is not read as a number, which over the local corpus gave
  `'as shown in eqn (2):2 τ = kn'` — a coefficient the deposit does not
  contain — and, for consecutive equations,
  `'NH3 + H2O → NH4+ + OH−2 Al3+ + 3OH− → Al(OH)33 Al(OH)3'`. A corruption is
  worse than a blank (#116, #162).

  A formula holding nothing renders as nothing: 140 of the corpus's display
  formulas hold nothing but a `<graphic>` once a `<label>` is set aside — the
  population the counter actually measures, which does not require a label and
  which at least 12 of the 140 do not carry — and no text-taking rule recovers
  those. Emitting the label alone would be #162's defect, a number standing
  for content that is not there. An inline formula keeps the spacing the
  deposit gave it, the module's own `_text_with_formatting` rule (a run's edge
  whitespace is re-emitted outside its markers); a merged display formula gets
  one space either side, because a block deposit has no spacing of its own to
  keep and welds without it.

  **Moves stored values**, measured by diffing parsed output against `main`
  rather than reasoned from the call graph — and the first statement of it
  counted only what its metric could see. "433 paragraphs gained, none lost,
  abstracts/tables/citations zero" was of an 880-article draw nobody can
  re-take, and *gained/lost* is structurally blind to a paragraph that changed
  in place, which is the commoner effect. Re-measured over the 880 articles of
  Europe PMC's named OA package `PMC10030002_PMC10040000.xml.gz`: prose moves
  in **34** articles, abstracts in **3**, the `html_content` that
  `FullTextService` caches in **12**, and figure and table captions in **17**
  and **5** — captions being a public field the earlier statement named
  nowhere. Paragraphs: 1 gained, **0 lost**, **159 changed in place**. Tables
  moving is the point rather than a surprise, each of those 12 being the LaTeX
  preamble leaving a rendered cell; zero there was never consistent with the
  24,476 `<tex-math>` in a `<td>`/`<th>` cited two paragraphs above.

  `formula_stack` joins `_parse_audit`'s net, which demanded it:
  `TestTheAuditNetIsComplete` failed on the new stack before a line of the
  audit was written. `scripts/sample_jats_exhibits.py` gains a counter
  generation for the three populations the *fix* rests on — where a
  `<disp-formula>` is deposited, which encoding a both-encoding formula
  deposits first, and what a `<tex-math>` actually holds — so the next redraw
  re-derives them rather than leaving them in a throwaway script. Those five
  counters are **not** in either committed corpus yet, so the package figures
  above stay non-re-derivable until the next redraw, and `print_report` prints
  `NOT MEASURED` for that section against the corpora as they stand.

  **Six defects found reviewing the above, three of which moved stored
  values.** Each is a rule the docstrings stated and no fixture exercised.

  - *An equation number in a table cell was discarded.* A formula in a cell is
    never "standalone", so the label was read and never printed, while
    `characters()` had stopped delivering it — a regression against `main`. A
    cell is a slot, not a sentence: all 40 labelled display formulas measured
    in a cell (8 of 97,909 articles) sit in a cell whose whole content is the
    number and the equation, PMC12164272's Table 2 being a reaction-number
    column the prose cross-references.
  - *An inline formula emitted display delimiters.* 98.6% of 20,251 inline
    `<tex-math>` bodies carry `$$…$$`, which is the `minimal`-documentclass
    converter's artifact and not a claim about context — so `'×'` rendered as
    `'$$\times$$'` in a figure caption. A display pair on an inline formula is
    now re-spelled `$…$`; the rule is one-directional, and a body of several
    delimited runs (`$a$ + $b$`) is left alone.
  - *An empty `<tex-math>` suppressed the encoding beside it.* The list was
    tested for presence rather than for a rendition, so `'Before Vmax after.'`
    became `'Before after.'`.
  - *Several `<tex-math>` printed the expression twice*, the outcome the design
    exists to prevent. The first that renders now wins.
  - *A deposit carrying `\begin{document}` and no `\end{document}`* fell
    through to the bare-expression path and delimited its own preamble — both
    failures this rule prevents, in one string.
  - *A rendered formula could reach no section, caption or cell and be dropped
    in silence.* Now counted and reported once per article at WARNING
    (`formulas_dropped`); routing it is #177. 192 in 23 of 97,909 articles.

  Filed rather than fixed: **#178**, whether LaTeX should win for a
  *both-encoding inline* formula at all, where it replaces prose that was
  already correct in 20,046 formulas against the 205 it recovers.

- **`JATSTableInfo.graphic_url`'s cited populations are the committed ones**
  (found while fixing #147). The docstring still quoted the pre-#138 draw —
  600 articles, 755 tables, 11 image-only "all in the back-filled window" and
  5 carrying both — where the redrawn corpora hold 1,997 articles and 2,448
  `<table-wrap>`, of which **8** are image-only and **84** carry both, every
  one in the recent window. It had come to say the opposite of the evidence in
  both directions: the back-filled window holds no `<table-wrap>` at all, and
  "both" is the commoner rendition. PR #163 reconciled `jats_parser.py`,
  `CLAUDE.md`, `ROADMAP.md`, `CHANGELOG.md` and `docs/manual/` and did not
  walk this file; the mechanised check asserts the corpus against literals in
  the test, so it cannot catch prose drifting away from it.

- **No number is invented for an exhibit the publisher did not number** (#162,
  and the reading of #138's corpus that filed it). `to_html()` rendered
  `fig.label or f"Figure {i + 1}"` and `tbl.label or f"Table {i + 1}"`, so an
  exhibit deposited without a `<label>` was given one. That is #116's own
  symptom — a swallowed label is not a blank, it is an invented value —
  reached from the other side, and worse than a blank for #116's own reason:
  the invented number is the *index*, so it does not merely add a number but
  **collides with a real one**. A paper whose first figure is an unnumbered
  schematic rendered two exhibits as `Figure 1`.

  **Measured, and derivable from two first-generation counters of the
  committed recent corpus**: 7,058 exhibits carry 6,937 direct-child `<label>`
  elements, so **121 exhibits in 83 of 997 articles (1.7% and 8.3%)** were
  given a number the deposit does not carry. The redrawn back-filled window
  measures 0 of 627. Both kinds are reached — at least 7 of the 121 are a
  `<fig>` and at least 11 a `<table-wrap>`, from the articles whose rows hold
  none of the other kind. **Moves stored values**: the heading, the `<img>`
  `alt` and, where the deposit carries neither label nor caption, the
  `<figcaption>` element itself all change in the HTML `FullTextService`
  caches, for 83 of every 997 recent articles. The anchor id keeps its
  `fig{i + 1}` fallback — that is a link target this renderer owns, never a
  claim about the document — and `alt` falls back to the caption, which is
  text the deposit does carry, and then to the empty string.

  **The issue as filed said something else, and the corpus refutes it.** It
  reported the `<label>` direct-child premise **violated** — 6,937 direct
  against 6,944 "carrying one anywhere" — and leaned toward a bounded
  descendant search as the remedy. `exhibits_with_descendant_label` counts an
  exhibit holding *any* `<label>` anywhere in its subtree, so that difference
  is the set such a fallback would **fire** on, not the set carrying its own
  label indirectly; reading it as the premise is *a count is of what you
  looked for* one more time, inside the instrument built to check it. Fetched
  from Europe PMC (2026-09-02), all seven of the named exhibits are a
  `<table-wrap>` carrying no `<label>` **and no `<caption>`**, and every label
  below them is a `<table-wrap-foot><fn>` marker (`*`, `**`, the empty string)
  or a `<list-item>` bullet inside a cell (`1.`, `-`, `•`) — the two
  containers #116 was about. A descendant search would have corrupted **7 of
  7**. Four are deposited under ids their publisher reserves for an unnumbered
  table (`array1`, `array2`, `utbl0001`), so the absent label is the deposit's
  intent rather than an omission. The premise is therefore neither refuted nor
  confirmed by the corpus: deciding it needs a rule for which descendant label
  would have been the exhibit's own, and that is the rule under test.

  The instrument says so now rather than printing a verdict it cannot
  support — and only the half it cannot. `print_report` prints the exhibits
  with no label of their own and, separately, how many of those hold a label
  below, in place of `PREMISE VIOLATED`. But `direct` is a subset of
  `descendant` by construction, so a zero difference is a sound one-directional
  all-clear, and removing it along with the over-claim left the report with no
  content-level line that changes between draws. It is kept, phrased as what
  was measured rather than as a verdict, in both the `<label>` and `<caption>`
  sections — the latter's equality (6,938 / 6,938 recent) being the measured
  result that certifies #123's premise, not the coincidence an earlier draft
  called it. The claim is corrected in `jats_parser.py`, `CLAUDE.md`,
  `ROADMAP.md`, the sampler's module docstring and here.

  The all-clear needed a second guard of its own, which is the same error one
  step further out: a draw carrying no `<label>` — or no `<caption>` — anywhere
  satisfies the zero test vacuously, and the back-filled window does exactly
  that on `<caption>` (0 of 627). Both sections now distinguish "no exhibit
  holds one below" from "this draw carries none at all", the latter worded
  `NO POPULATION HERE` rather than borrowing `NOT MEASURED`, which is reserved
  for a row generation predating a counter.

  Two consequences are filed rather than fixed: the cached HTML this moves has
  no version stamp to invalidate it (#172), and a figure's `alt` now duplicates
  its own `<figcaption>` verbatim (#173).

- **Six ways the exhibit sampler reported more than it measured** (#165-#170,
  from the review of PR #163). Each is a case of the instrument being trusted
  past what it had established, which is the class this whole branch exists to
  remove; each is pinned by a test that was mutation-verified to fail without
  the fix. No committed figure moves — every one of these was reachable rather
  than triggered, and the two corpora carry **0** sentinels between them.

  *Three path guards failed open* (#165). `_is_package_path` tested the gzip
  magic bytes alone, so a `.tar.gz` that is a gzipped non-tar passed
  `_validate_args`, reached `package_candidates`, and raised `PackageError`
  there — uncaught anywhere in the module, and after the journal header had
  been written. `iter_package_articles` globbed `*.xml` one level deep for a
  directory while walking tar members at any depth, so one artifact unpacked
  and packed yields different candidate sets and so a different `draw()` under
  the same `(packages, window, target, seed)` — the reproducibility claim
  itself, resting on an unstated premise that the real packages are flat. And
  both `DEFAULT_OUTPUT` guards used raw `PurePath` equality, so
  `-o "$PWD/tests/data/jats_exhibits.json"` with the back-filled package
  **overwrote the committed recent corpus at exit 0**; no journal is committed,
  so on a fresh clone `_journal_disagreement` cannot catch that either.
  `_names_default_corpus` resolves both sides, the rule `_package_location`
  already applies for exactly this reason.

  *A well-formed non-JATS body was measured as an article* (#166).
  `measure_article` read `ET.fromstring` succeeding as "this is the article",
  so an HTML error page served at HTTP 200 by a proxy or CDN produced a valid
  row with every counter at zero — added, journalled, and entering every
  denominator. On `--compare-europepmc` an outage was counted as a *rendition
  disagreement*, which is the population `jats_exhibits.rendition.json` is
  committed as evidence for. The corpora legitimately hold all-zero rows, so
  nothing downstream can tell the two apart afterwards. A root-element test on
  the local name, so a namespaced deposit is still accepted.

  *A permanent failure was filed as a transient one* (#167).
  `compare_renditions` used falsiness where `_measure_and_journal` uses
  `is None` — with a nine-line comment arguing why — so a served body that
  arrived whole and would not parse was recorded as `europepmc_unavailable`,
  telling a reader to re-run for an article no re-run recovers. A third cause,
  `served_unparseable`, and the same falsiness corrected in `main`'s live
  branch.

  *The `NOT_MEASURED` sentinel escaped into the canonical corpus* (#168).
  `print_report` printed `NOT MEASURED` for a counter generation the rows
  predate and returned `True` regardless, so that corpus reached the canonical
  `-o` path — not `*.unreportable.json` — at exit 0, with `-1` inline and no
  header marker. Beside it, `articles_where` used truthiness, and `-1` is
  truthy, so a row that measured *nothing* counted as an article that carries
  the thing, while `sum_of` subtracted it: two cited numbers moving in
  opposite directions, neither self-cancelling and neither looking like a
  sentinel. `measured` also raised `TypeError` on all eleven `Counter` fields.
  Now `_unmeasured_generations` makes the return value mean what the docstring
  says and names the gap in the corpus header, `_as_count` makes both
  accessors total, and `TestEveryCounterIsInAGeneration` walks the dataclass in
  both directions — the `TestTheAuditNetIsComplete` precedent, applied where
  the rule had lived in prose.

  *The corpus held rows its own header did not explain* (#169). Every
  journalled row was written under this run's `window`, and `target` is
  deliberately outside the draw identity so a top-up resumes — so `--target
  300` over a journal of 1,000 wrote `"target": 300` above 1,000 rows, and a
  reader following this module's own recipe got 300 identifiers against a file
  holding 1,000. Growing was safe only by accident: `random.sample` is
  prefix-nested at the committed pool size and not at 2,000. Rows outside the
  draw now leave the corpus and stay in the journal, so nothing measured is
  lost and the top-up workflow is undisturbed. `Totals.articles` is derived
  from `len(rows)`, the reconcile being exactly the operation that moved one
  and not the other.

  *A short hold overwrote the rendition artifact* (#170).
  `_comparison_reportable` guards the served side against `held` and nothing
  guarded `held` against `requested`, so a run holding 12 of 300 wrote
  `compared: 12` to the canonical name at exit 0 — the headline this repo
  quotes off that file silently becoming a 12-article claim. The comparison is
  still computed and kept; only the name is refused.

  Two more, neither a defect today. An **undated article** is now named on
  stderr rather than dropped from the candidate pool in silence — the shape the
  `_YEAR_RE` fix took, whose population measures **0 of 220,485** across both
  packages, so this is a net for the next cause rather than a live one. And
  `article_year`'s **whole-document scope** — the one thing in this branch not
  scoped to what the parser routes, so a `<sub-article>`'s own `<pub-date>`
  decides the parent's window under `min` — is now stated rather than
  accidental, and measured at 0 of 3,385 region-carrying articles.

  Test gaps closed alongside them, each mutation-verified: the lazy
  `<pub-date>` regex that produced the first draw's articles "published in
  1861" had a regression test whose fixture could not distinguish the defect;
  `_fetch` was 100% unexercised, including its non-200 guard; `fullTextXML`
  was unpinned at all three call sites, so the wrong Europe PMC resource would
  have produced a full corpus of all-zero rows; no test drove a refusal through
  `main`, so `if refusal is not None:` → `if False:` survived; `nested_tables`
  had never been shown able to fire, making the cited "0 nested `<table-wrap>`"
  tautological; and `_looks_archival`'s `mime-subtype` branch never returned
  `True` in any test, so half of "archival by either test" was unexercised.

### Added

- **Both JATS corpora are redrawn from a named public artifact, and the walk
  is scoped the way the parser is** (#138, closing over #132 and #158). Every
  exhibit figure in this file, in `CLAUDE.md`, in `docs/manual/fulltext.md`
  and in `jats_parser.py`'s comments has moved, and each moved for **three**
  reasons at once — a different sample, a different rendition, and a scoped
  walk — so no movement below may be attributed to any one of them, the
  scoping least of all, since it is the only one whose effect the corpus
  records (`unscoped`).

  *The sample is now re-derivable.* `scripts/sample_jats_exhibits.py --package`
  draws deterministically from a PMC OA baseline package, so a reader
  reconstructs the identifier list from `(packages, window, target, seed)`,
  all four of which the corpus records. Both committed corpora are 1,000-article
  draws at `seed 0` — 997 of the recent window served and all 1,000 of the
  back-filled one:
  `oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26.tar.gz` for 2023-2025 and
  `oa_comm_xml.PMC002xxxxxx.baseline.2025-06-26.tar.gz` for 1996-1998. The
  windows they replace were live stratified draws counted back from *today*,
  which named a sample nobody else could take — #132.

  *A candidate is dated by a regex, and the first one silently excluded an
  attributed `<year>`.* `<year>` legally carries `@iso-8601-date`,
  `@calendar` and `@content-type`, and the pattern required a bare open tag —
  so an attributed one made the article undated, and an undated article is
  **undrawable**: absent from the candidate pool, never counted as
  unmeasured, exit 0. Measured over `PMC012xxxxxx`: 17 of 97,909, every one
  `<year iso-8601-date="2025">`, every one inside the recent window, and 14
  of the 17 one contiguous journal block (PMC12085917-PMC12085930), so
  publisher-clustered rather than random. That is the same silent,
  publisher-correlated loss the whole-member read is required for, reached by
  another route, so the recent window was **redrawn** rather than documented
  as a limitation: 97,668 candidates, and the undated population now measures
  0 of 97,909 and 0 of 122,576. The back-filled package carried none and its
  corpus is untouched. Each corpus also records `unmeasured_causes` beside
  its `unmeasured` count, in the vocabulary the rendition artifact already
  used — the recent one's three are all `europepmc_unavailable`; the
  back-filled one predates the field, filling it meaning a redraw of a window
  the year fix does not touch.

  *The bytes are not the package's, and that was the plan's own premise
  disproved midway.* A baseline package holds an **archive** rendition;
  `FullTextService` feeds the parser Europe PMC's `fullTextXML`; and the two
  differ on exactly the cited populations: `last_is_thumb` **differs in 156
  of 300 compared articles, and where it differs the archive measures 0
  against 781 served** — so a corpus drawn *and* measured from a package would
  have read #117's whole ranking rule as dead code.

  **That number is scoped, and the first draft of this entry was not.**
  `rendition_delta` records a field only where the two renditions disagree, so
  an agreeing article appears nowhere in the file and summing the deltas gives
  a sum over disagreements, never a corpus total; the archive's `last_is_thumb`
  over all 300 is simply not derivable from the artifact. Nor is there a single
  mechanism to name. An earlier draft said the archive deposits one bare
  `<graphic xlink:href="…-g001">` per figure where Europe PMC synthesises an
  `.jpg`/`.gif` image-and-thumb pair; that holds for a spot-checked article and
  fails in general, `PMC12169732` depositing its own four thumbnails as
  `specific-use="thumbnail"` where Europe PMC re-labels them
  `content-type="thumb"`, both renditions measuring four. The finding is
  decisive either way — it was the statement that overreached, in the same
  release whose whole point is that a count is of what you looked for.

  `--measure-europepmc` therefore measures the
  package-drawn identifiers from the served rendition, the corpus records
  which under `window["rendition"]`, and `--compare-europepmc N` writes
  `tests/data/jats_exhibits.rendition.json`, which is the committed evidence
  for all of this.

  *The walk no longer descends into a nested article.* `<sub-article>` and
  `<response>` are skipped exactly as the parser suppresses them, so the
  counters are commensurable with what the parser sees; contributor counters
  were the worst affected, a peer-review round being the densest `<contrib>`
  construct JATS has. What the scoping removed is kept per row in `unscoped`
  rather than discarded, so the correction is measurable from the corpus
  instead of asserted: it is non-empty for exactly the 29 of 997 recent
  articles that carry a region (145 regions), and empty throughout the
  back-filled window.

  *Four rules waiting on a population now have one*, and they stay open
  because a measurement makes a rule decidable without deciding it: #142
  (`<collab>` element children) and #143 (contributor multiplicity) measure
  **empty** on both windows; #150 finds **0** `<ref>` carrying only a
  `<note>` in 52,969, one carrying a `<note>` beside other children — a
  population this draw does not reach, where the draw before it found 2;
  #147 is the one live population, 1,915 `<disp-formula>`, 1,398
  `<tex-math>` and 1,087 `<alternatives>` holding both a MathML and a TeX
  encoding.

  *One claim was overturned and two are withdrawn as unre-derivable.* The
  `<label>` direct-child premise was recorded here as **violated** on the
  served rendition; that reading is itself corrected above under #162 — the
  counter behind it never measured the premise. #127's image-only-table population cannot be
  re-measured from either corpus, the redrawn back-filled window holding no
  `<table-wrap>` at all. And the empty population behind the abstract-branch
  exhibit guard — 44 exhibits inside an `<abstract>`, none titled — was an
  ad-hoc walk over the replaced draws, which the sampler has no counter for.
  `TestTheCitedPopulationsAreWhatTheCorporaHold` pins every surviving figure,
  including the corpus headers themselves, so the next redraw fails the suite
  rather than leaving a stale number behind.

- **`JATSTableInfo.graphic_url`** (#127) — a `<table-wrap>` whose content is a
  `<graphic>` keeps its image. A scanned or typographically complex table used
  to lose its only content: the parser returned an id, a label and a caption
  over nothing, which is indistinguishable from an empty `<table-wrap>`. The
  deposit is chosen among several by the same ranking a figure's is, moved
  into a shared `_GraphicHolder` rather than written twice, because two copies
  of a rule that heavily argued are two things to keep in step. **Whether a
  table is ever deposited with several `<graphic>` has now been measured, and
  the population is empty** (#135): across the two committed draws — 2,448
  `<table-wrap>`, every one in the recent window — 92 carry a `<graphic>` of
  their own and not one carries two. So ranking and plain first-wins agree
  there and the rule is *unexercised* on tables rather than confirmed, which
  is what the comments say. Sharing it is still right; stating publisher
  behaviour as observed was not. A deposited href is stripped first: XML
  normalises a pretty-printed attribute to spaces rather than collapsing it,
  and a padded href is truthy, so it would take the ranking slot, block the
  real deposit behind it and render as a broken `src`. No instance in either
  corpus (13,624 deposits) — the guard is for a population measured empty.
  `to_html()` renders the image as an `<img>`, but **only where there is no
  `<table>` markup**: a `<table-wrap>` may carry both, and where it does the
  markup is the better rendition. The model holds the href either way.

  **The evidence for the image-only shape is historical and no longer
  re-derivable, which is worth stating rather than smoothing over.** It was
  measured on two 300-article windows: **0 of 662 tables** in the recent one
  and **11 of 93 — 11.8% [6.7-20.0]** in a draw from 1996-1998, those 11
  sitting in 2 articles from one journal where they were *every* table the
  article had (6 of 6, 5 of 5) — PMC3437083 and PMC3437093, both clinical
  papers whose data is entirely in those tables. The #138 redraw replaced both
  windows, and the new back-filled one contributes **0 `<table-wrap>` in 1,000
  articles**. (*That `oa_comm`'s 1996-1998 material is scanned page images
  with no tabular markup is an inference* from 0 tables beside 627 figures and
  3,880 `.png` deposits — no counter measures it.) That 0 is an absent
  denominator, not a measurement of
  the population, and must not be quoted as one. The recent window measures
  8 of 2,448 (0.3%), so the shape is present but rare there. The rule stands
  on the older evidence; re-taking it needs a window that actually holds
  back-filled tabular deposits.

- **`--months-ago` on `scripts/sample_jats_exhibits.py`** — the stratified
  draw can be displaced backwards by whole months. A stratified sample of
  *recent* deposits is still one window, and #127's population lives in
  back-filled material the default draw cannot see at all.

  Three rules keep a displaced draw from quietly becoming the evidence.
  **A negative offset is refused**, at the entry: `skip` is both a loop bound
  and a slice index, so `--months-ago -1` returned a single window from two
  years ago and `-24` returned none at all, each printed as a rate with a
  Wilson interval — the same shape as `sync()`'s negative `recheck_days`.
  **A displaced draw must name its own `-o`**, since the default path is the
  recent corpus and the journal follows it, so writing there would replace
  that corpus under its name or pool two windows into a number describing
  neither. And **the written corpus records its `window`**, because the strata
  are counted back from *today* and the same command run later draws a
  different sample — without it "1996-1998" lives only in prose.

- **The table side of #117's ranking is counted, and answered** (#135) —
  `tables_with_graphic`, `tables_multi_graphic`, `tables_first_is_thumb`,
  `tables_last_is_thumb` and `tables_with_both`, in their own report section
  and deliberately **not** folded into the figure counters, whose percentages
  are cited in `jats_parser` and CLAUDE.md and would be invalidated by a wider
  denominator. A row written before these carries none of them, and each would
  then sum to zero — indistinguishable from a draw in which no table deposits
  an image, which is the exact misreading #127 needed two windows to correct.
  So absence is loaded as a sentinel and reported as **NOT MEASURED**, never
  as 0%.

  The live run those counters were added for has now been made, on both
  windows, and it required **fixing the instrument first**. The counters
  walked `el.iter()`, a whole subtree, while the parser routes a `<graphic>`
  by its **owner** — the residual the issue itself named. Unscoped, four of
  ten recent-window tables "carried several deposits"; every one was the
  `<td>` cell images of two articles. Scoped to what the parser would route
  (`_owned`), no table in either draw carries a second deposit at all. The
  figure counters kept the subtree walk at this revision, their percentages
  being cited. **The argument that justified that is refuted by this branch's
  own corpora, and the scoping was left open rather than restated** (#164): it
  read "both draws record zero nested exhibits and every foreign owner is a
  `<td>`, which can only sit under a `<table-wrap>`", and the redrawn recent
  corpus holds **7 nested `<fig>`** (all `PMC12143881`) and **three** foreign
  owners — `<td>` 82, `<inline-formula>` 69, `<disp-formula>` 2, 153 graphics
  in 12 of 997 articles. An `<inline-formula>` is not confined to a
  `<table-wrap>`, so the premise that the two walks agree on the figure side
  did not hold. What that cost was **not** measured on the rendition the
  percentages are of: a spot measurement over the same drawn articles'
  *archive* bytes moved the multi-graphic figure count 77 → 58, which is the
  right order of magnitude to matter and the wrong rendition to cite. **#164
  scoped the figure side and answered it on the right rendition — 18 figures
  — in the entry above.**

- **`scripts/sample_jats_exhibits.py`** (#131), the live runner behind the
  JATS exhibit rules below — the fifth in `scripts/`, and the one to re-run
  before changing `_ARCHIVAL_MIME_SUBTYPES`, `_ARCHIVAL_EXTENSIONS`,
  `_GRAPHIC_TRANSPARENT_WRAPPERS` or the `<label>` parent test. The rules had
  shipped with their populations measured in a sibling repository and nothing
  in-tree to re-earn a list member from, which is the one thing `CLAUDE.md`
  requires of every other curated list here.

  It does **not** import the parser's predicates — a corpus labelled by the
  rule under test can only confirm that rule — and it draws a sample
  **stratified by publication month**, because a single cursor walk returns a
  contiguous block of accessions: its own first run drew 120 articles of which
  106 carried no exhibit at all.

  What it measured, over 276 open-access Europe PMC articles carrying 2,067
  exhibits, is folded into the comments at each site — and **both committed
  corpora have since been redrawn** (#138) with every counter present, so a
  reader can re-derive from the repo what that vanished draw only asserted.
  One rule keeps an **empty** population on the new evidence too: across
  **7,055** `<alternatives>` members, none declares a `mime-subtype` and none
  is archival by either test. **One moved, and matters more than it did**: the
  276-article draw found exactly one `<graphic>` owned by a non-exhibit inside
  an exhibit, which read as a population of one; the redrawn recent corpus
  finds **153, in 12 of 997 articles, over three owners** — `<td>` 82 (in 8
  articles), `<inline-formula>` 69 (3), `<disp-formula>` 2 (1).
  Since #127 gave `JATSTableInfo` a `graphic_url`, relaxing ownership would
  land a cell decoration in it as though it were the table's own rendition, so
  the rule is measured as load-bearing rather than carried against a
  hypothetical — and the spread of owners is itself the argument for keeping
  the *listed* side short and everything else opaque. The draw before this one
  over the same window also found `<chem-struct>` and `<th>`, which this one
  does not, so the owners are a set drawn from rather than a fixed list.

  **And one was recorded as overturned, wrongly** — see the #162 entry above,
  which corrects it. The claim was that the `<label>` parent rule's premise,
  **full** in all three earlier draws (2,033 / 2,033, 1,446 / 1,446, 365 /
  365), is VIOLATED on the redrawn recent corpus at 6,937 direct against 6,944
  "carrying one anywhere". `exhibits_with_descendant_label` counts an exhibit
  holding *any* `<label>` in its subtree, so that difference is the set a
  descendant-search fallback would fire on and not the premise. What the pair
  does support — 121 exhibits carrying no `<label>` of their own, in 83 of 997
  articles — is the population #162 acts on. The rule remains much the better
  of the two on the comparison the corpus does support, a depth counter
  *mis-assigning* 561 labels in 95 of those 997 articles.

- **An end-of-parse audit for the JATS handler** (#134) — new private
  `bmlib/fulltext/_parse_audit.py`. `_JATSHandler` carries two dozen stacks,
  depths and flags, and every one of them decides where content is *routed*;
  `_run_parser()` returned the handler without looking at any of them. A parse
  ending with one unbalanced produced a thin article, an article missing its
  last sections, or an article whose remaining prose was filed as caption
  text, and said nothing at all. A frozen `ParseUnwindState` — one field per
  stack or counter, every field defaulting to its clean value so a test names
  only the imbalance it is about — is read by a pure `unwind_diagnostics()`
  returning one message per imbalance, each naming what the imbalance *cost*
  rather than merely what was left open. `_run_parser()` is the one place
  `parse`, `to_html` and `parse_with_html` all funnel through, so every entry
  point is covered without any of them having to remember.

  **A net, not an input check.** `expat` rejects an unbalanced *document*, so
  nothing a publisher deposits can reach these predicates — they fire only
  when the parser is wrong. So the level is ERROR: every line is a claim that
  bmlib itself is wrong. Nothing raises — a partial article reported loudly
  beats no article, which is #129 below in the other direction.

  **And it is prospective, which the first draft of this entry got wrong.**
  #115, #123 and #130 are stack-handling defects but would each have unwound
  *clean* — #115 cleared `in_figure`/`current_figure` unconditionally at
  `</fig>`, #123's `in_caption` was a bare boolean set and cleared in matching
  pairs (and its nesting population measures empty), #130 routed a `<title>`
  by an ambient test leaving no state at all. None left residue, which is why
  all three went undetected until they were found from outside bmlib. The one
  genuine precedent is the sibling Swift port, where the same shape stranded a
  footnote counter above zero and drained every remaining paragraph in the
  document into it, one at a time, unremarked, surviving to code review. The
  module is kept for what it prevents, not for a draw that caught it.

  Two fields the issue did not name. **Unfilled exhibit slots**, because
  `build_figures()` filters the holes out and its docstring calls that filter
  unreachable — if it ever is reached, an article silently loses a figure. And
  **the routing flags, grouped into one field** rather than given one each: a
  flag is set on a start tag and cleared on the matching end tag, so if the
  end tag *arrived* and the handler failed to clear it, `element_stack` is
  empty and only the flag shows it. `current_abstract_text` is deliberately
  excluded — `</abstract>` flushes without clearing, so it is non-empty at the
  end of every article carrying an abstract, and including it would have fired
  the audit on nearly every real document.

  What caught that is the new autouse `parser_log` fixture, which **fails any
  test in `test_jats_parser.py` whose parse emits an ERROR**, making all 186
  pre-existing fixtures a false-positive check without being written as one.
  Nothing else in the module looks at logs, so a predicate firing on
  well-formed input would otherwise have shipped green and turned the ERROR
  channel into noise from its first day — the same failure the audit exists to
  end, one level up. Two named mutants confirm it has teeth: putting
  `current_abstract_text` back into `_ROUTING_FLAGS` costs 36 tests, and
  reading `excess_text_buffers` as the raw `text_stack` length costs 221.

  Membership of `_ROUTING_FLAGS` is no longer enforced by prose alone.
  `TestTheAuditNetIsComplete` walks the handler's own attributes and fails on
  any that reaches neither the audit nor a *named* exclusion — the rule the
  module states, mechanised, after review found the net already missing
  `implicit_body_section` (see Fixed below).

- **A JATS parse that yields no authors now says so** (#121) — and says which
  kind it is. An article parsing to zero authors renders HTML byte-identical
  to one that genuinely lists none, and `FullTextService` caches that HTML, so
  the correct answer and the catastrophic one persisted to disk the same way.
  #111 dropped every author from 57% of open-access articles and survived
  undetected until it was found from outside bmlib, while porting the parser
  to Swift.

  A new `front_contributor_name_count` separates the two, **gated on
  `in_front` and not on `in_contrib`**: the latter is set only once
  `_is_author_contrib` has said yes, which is precisely the routing decision
  #111 got wrong, so a counter keyed on it would go to zero in exactly the
  situation it exists to detect. `<back>` is excluded because a bibliography
  is full of surnames and none is a contributor, and a suppressed
  `<sub-article>`'s `<front>` never sets the flag, so nested contributors are
  excluded for free. It counts **every JATS spelling of a contributor's
  name** — see Fixed below for why counting only `<surname>` was itself a
  silent failure.

  **WARNING, not ERROR.** Unlike the audit beside it, this branch can fire on
  a well-formed document bmlib parsed correctly — #121's measurement (1,025
  articles, drawn during the Swift port; not reproducible from a corpus
  committed here) names `PMC12803704`, an `article-type="correction"` that is
  genuinely author-less and still carries `<front>` surnames. ERROR is
  reserved for "bmlib is wrong", and keeping that meaning exact is what the
  audit's net above depends on. A `<front>` naming no contributor at all logs
  at DEBUG.

- **The prospective half of `_inside_mixed_citation` is mechanised** (#151).
  That helper keeps its strict-ancestor slice (`element_stack[:-1]`) as
  prospective and argues it is currently harmless with a whole-method claim:
  no arm of `endElement` reads `text` for an element outside
  `_TEXT_ACCUMULATING`, so the base buffer is written and never consulted.
  The claim was true and nothing tied the two together — it is a property of
  a 500-line method asserted in one helper's docstring 300 lines away, and
  the next queued issue is exactly the shape that breaks it, since #142 wants
  a `<collab>`'s `<institution>`/`<addr-line>` children read and neither
  accumulates. Adding such an arm would fail no test; it would quietly make a
  paragraph false while the code around it still relied on the reasoning.
  `TestTheAuditNetIsComplete` is the precedent — *a rule enforced by prose is
  not enforced* — and it caught a routing flag shipping missing from the net
  it belonged in.

  `TestOnlyAnAccumulatingElementReadsTheBuffer` walks `endElement`'s arms with
  `ast` and reports every read of the buffer together with the element names
  that can reach it. **The net is keyed on the buffer, not on identifiers.**
  Five spellings reach it: `text`, `normalized_text` and `element_text` — the
  latter two being the same buffer a line either side — plus
  `self.current_text` and `self._pop_text_buffer()`. The first cut watched the
  three locals alone, and review found that `elif name in ("institution",
  "addr-line"): self.collab_address = self.current_text.strip()` — #142's own
  arm, in the spelling an implementer reasoning *"the `<collab>` buffer is
  already open"* would reach for — passed the whole suite green while making
  the slice load-bearing: a `<back>` whose `<ref-list>` is followed by an
  `<institution>` put the citation into the institution's text. Whether the
  guard fired turned on which of two synonymous forms the author typed.

  **Every outcome fails closed, which is the whole design.** A guard the
  walker cannot read is a finding in its own right rather than a read passed
  over; a read no `name` test constrains is reachable for every element and
  reported unless it is the method's *plumbing*; and the verdict is
  containment in `_TEXT_ACCUMULATING`, never overlap with it, since the
  likeliest breakage is an existing arm gaining an element rather than a new
  arm appearing. The walker raises when it cannot find the class or the
  method: *no reads* must never be an answer it can give — measured, that
  answer leaves four of the twenty tests green, one of them the invariant
  itself, which asserts an empty finding list.

  **Plumbing is recognised by what a statement does, not by what it binds.**
  A statement is exempt only when it stands under no guard, binds a buffer
  name, *and* hands the buffer to no method on the handler. Exempting on the
  binding alone — the first cut — silently allowed `text =
  self._collab_child(name, text)` wedged beside the preamble: a per-element
  hook is the natural way to add handling without disturbing a forty-branch
  `elif` chain, and it reads the buffer for every element there is. It was the
  one shape inside the method that produced no finding at all.

  Three smaller repairs from the same review. A read in an `and` guard's own
  test now inherits the operands to its left, which short-circuiting
  guarantees — crediting only the outer guards reported `elif name ==
  "journal-title" and text:` as reachable for every element, a false
  accusation whose message announces a broken invariant and whose only remedy
  would be to un-refactor correct code. `or` finishes its loop instead of
  returning at the first unconstraining operand, which used to drop an
  unreadable guard sitting to its right and, combined with the exemption
  above, turned a fail-closed reading into a green one. And tuple targets
  count as binds, so writing the preamble's two statements as one is not
  reported as two violations.

  Most of the twenty tests exist so that a green means something, a walk that
  finds nothing being a walk that passes: teeth controls in #142's own shape
  for each spelling of the buffer, an unreadable-guard control, controls for a
  read no guard constrains and for one guarded by something other than
  `name`, the guard-algebra controls, and the two raises. The positive control
  carries the **whole** inventory of arms that consume the buffer — nineteen
  elements — read as a floor: it was six while the walk saw nineteen, so
  thirty-eight of the fifty-three reads it is built from could have left
  without a word, and a *partial* extraction of one arm into a helper is a likelier
  refactor than the wholesale kind. It is a floor and not an equality so that
  #142 adding a legitimate arm stays green without anyone editing an inventory
  to let it through.

  Verified end to end in both directions against the real parser. Nine
  mutations of `endElement` are each reported by line and element — #142's arm
  in all four spellings, an existing arm widened to admit `<institution>`, an
  arm extracted into a helper, a preamble hook, a guarded rebinding, and an
  unreadable guard in an `or`'s right-hand branch — while the *permitted*
  change, that arm plus the matching `_TEXT_ACCUMULATING` membership, leaves
  all twenty green. Twelve mutants of the walker itself, twelve caught; the
  last two controls added exist because two of those twelve survived the first
  time, and the inventory's exclusion of preamble reads is pinned by a
  witness (`<abstract>` accumulates and no arm consumes it), that exclusion
  being what stops containment from being satisfied by the preamble alone. The
  synthetic controls are judged against an accumulating set of their own
  rather than the parser's: asserting `<institution>` does not accumulate is
  asserting something #142 is entitled to change, and seven controls would
  otherwise fail for the opposite of the reason they were written.

  Its sibling is a comment gap at three sites, and measuring it corrected the
  issue's own account. `element_stack[:-1]` is a *strict*-ancestor slice only
  because `element_stack.pop()` sits at the end of `endElement`, and nothing
  said so at either end. Moving the pop reddens 58 tests when placed before
  the handler arms and 65 when placed above the buffer pop at the top — and
  **only the second reaches the citation slice**, because
  `_inside_mixed_citation` is evaluated inside `_pop_text_buffer`'s own
  argument, so "moving the pop up fails it" is true only of a placement the
  issue did not name. The slice is pinned by the seven-test difference between
  those two placements: three of `TestAMixedCitationKeepsTheTextItPrints`'
  six, and **four in `TestARefCarryingSeveralCitationsKeepsThemAll`** — the
  majority of the guard, and a class an earlier draft of the comment omitted,
  which would have told a maintainer rewriting #149's tests that nothing was
  at stake. Two neighbours turn out not to ride on it at all: the `<caption>`
  parent test is made in `startElement`, where the push is what places it, and
  `<article-id>`'s is pinned by nothing, being disjoined as `parent ==
  "article-meta" or self.in_front`.

  No behaviour change: one test class and three comments.

### Fixed

- **A reviewer's disclosure answered for the article** (#119).
  `TransparencyAnalyzer` never consumes `JATSParser` output: it fetches
  `fullTextXML` itself and scans the raw string, so every `<sub-article>` and
  `<response>` region — a peer-review round, an author response, a translated
  full text, a meeting abstract, Europe PMC's injected `associated-data` block
  — was read as the article's own text. Reviewers write in exactly the
  vocabulary these scans hunt for: a round's "the reviewers declare no
  competing interests" became *this paper's* COI disclosure, its data statement
  *this paper's* data-availability level, and an "employee of" line an industry
  tie the paper never disclosed. `_fetch_europepmc_fulltext()` now removes
  those regions before returning, which is the one door the full text enters
  through, so the tagged-COI match, the cue-phrase scan, the data-availability
  patterns and the industry-COI extraction all read a string that has to be the
  article's.

  The two-element set and its completeness argument are `jats_parser`'s — of
  JATS's ~295 elements exactly three admit `<front>`/`<front-stub>` and
  `<body>`, the third being `<article>`, and the disjunction is what makes the
  count three, `<response>` admitting `<front-stub>` only — **restated rather
  than imported**, so `bmlib.transparency`
  depends on nothing in `bmlib.fulltext` — a tuple rather than the parser's
  frozenset, because it is joined into a regex alternation and needs a
  deterministic order, and `TestTheRestatedSetMatchesTheParsers` is what keeps
  the two in step, a rule enforced by prose not being enforced. It is a
  **depth**, not a flag,
  because JATS nests these and an inner end tag would otherwise re-admit the
  rest of the outer round as article prose. And because a literal `<` can only
  open markup in well-formed XML, a comment, a CDATA section, a processing
  instruction and the DOCTYPE internal subset are the *complete* set of places
  the characters `<sub-article` can appear without being a start tag; all four
  are lexed as tokens, which is what makes the scan exact rather than a list of
  hazards someone thought of. (The converse does not hold for `>`, legal
  unescaped in an attribute value and a system literal, nor for `]` in an
  entity's replacement text, so the tag and doctype branches step over quoted
  literals rather than scanning to the first `>`.) An **unclosed region returns
  nothing at all** and the analysis falls back to the abstract, with one
  `WARNING`: scanning the tail is the defect itself, and dropping it silently
  manufactures "No COI disclosure found in full text", which — absent a PubMed
  `<CoiStatement>` — is the finding that triggers the missing-COI HIGH-risk
  rule. An unmatched *end* tag **at depth 0** is not an imbalance in that sense
  — no nested prose reaches the scans through one — so it costs the article
  nothing; one *inside* a region names an element that did not open it, so
  since issue #160 it closes nothing and the region is refused like any other.
  Only a document expat would reject can carry either.
  A document that is **entirely** nested articles WARNs too, rather than
  returning the empty string the caller would read as "nothing was served".
  The element names are matched with a negative lookahead rather than `\b` —
  `-`, `.` and `:` are all legal in an XML name and all word boundaries, so
  `<response-note>` and `<sub-article-x>` matched and stripped prose no JATS
  element owns — and interpolated through `re.escape`. The two groups the loop
  reads are **named**: positional ones made the "is this a tag?" test a
  property of the pattern's shape, so a group added to any earlier branch would
  have made a comment look like a start tag with nothing failing. And the strip
  runs *outside* the `try` wrapping the HTTP call: it is bmlib's own
  computation, so anything it raises is a bmlib defect, and inside that handler
  it would have been logged at DEBUG as "fetch failed" and reported to the
  caller as "EuropePMC served nothing".

  **Stored transparency values are not comparable across this change** for a
  paper whose Europe PMC full text carries a nested article. Measured over
  PMC's `oa_comm` baseline package `PMC012xxxxxx` (2025-06-26, 97,909
  open-access articles): 3,382 (3.45%) carry a region this removes — 3,377 a
  `<sub-article>`, and 5 more a top-level `<response response-type="reply">`
  with no `<sub-article>` at all — and 602 of those, 0.61% of the corpus, have
  at least one scan output move once the regions go: 499 the data-availability
  level, 125 the COI cue phrase (4 of them flipping the stored
  `coi_disclosed`, the tagged section usually still firing), 6 the industry-COI
  signal — `industry_funding` and `industry_confidence`, not only the indicator
  string — and 1 the tagged section itself. None of the five `<response>`
  articles is among the 602, so that element is **rare rather than absent**.
  Nesting is exercised rather than defensive: 98 of the 3,382 carriers nest.
  Two paths measure empty — none of the 97,909 leaves a region open, and none
  is emptied by the removal (all 3,389 carriers keep their `<body>`, the least
  retaining 32.2% of its bytes). The lexer's four skip tokens have no measured
  population on the module's own input: the comment token fires on 3 *archive*
  deposits, Springer commenting out an `<authorqueries>` block whose `<aq>`
  children carry `<response>` elements, but Europe PMC's `fullTextXML` serves
  those same three with no comments at all.

- **A `<ref>` carrying several citation elements lost all but the last, and
  welded their authors into one byline** (#149). JATS admits several citation
  elements in one `<ref>`, and both close arms assigned
  `JATSReferenceInfo.citation` unconditionally, so every part but the last was
  discarded. The structured fields did the opposite — scalars were last-wins
  while `authors` *accumulated* — so one reference reported 40 authors and
  rendered `"A. Ricci, J. S. K. Clark, et al."`, two people from two different
  papers presented as one paper's byline.

  **Measured before the rule was picked**: 216 such references in 21 of 880
  local PMC articles, and **not one uses `<citation-alternatives>`** — every
  case is bare siblings, so this is never "the same reference deposited
  twice". Two shapes, both a single bibliography entry as printed:

  - **149 with each part labelled** — RSC's `(a)`/`(b)`/`(c)`: several distinct
    works under one bibliography number;
  - **61 unlabelled** — one reference *split*, its tail (a URL, an
    `[Online]. Available:` note) deposited as a second element.

  The second shape is what rules out emitting one `JATSReferenceInfo` per
  part: it would split a single work into a work plus a bare URL. So a `<ref>`
  remains one reference, `references` keeps its length, and:

  **The parts are joined with nothing between them**, because that is what the
  deposit holds — the character data between consecutive citation elements is
  empty in **586 of 586** occurrences. Each part's *raw* text is kept and the
  whole normalised once at `</ref>`, the module's "strip once, at the outermost
  call" rule, which preserves the space in front of `(b)` while not inventing
  one in front of `, [Online]`.

  **The structured fields come from the first part.** Every field arm is gated
  on `in_ref_citation`, so leaving it unset for the later parts is the whole of
  first-wins. For a split reference the first part *is* the work; for a
  multi-part one it is work `(a)`, and `citation` still carries all of them.
  Nothing is discarded — bmlib simply stops assembling one reference out of
  several different works. 86 references drew fields from more than one part.

  **And a part's marker is no longer the reference's number.** The `<label>`
  arm's reference branch was gated on the ambient `in_ref` flag — the very
  routing #116 established is wrong, missing on this one branch — so RSC's
  `(a)`/`(b)` overwrote the reference's own label, last one winning. It is a
  parent test now, like the `<fig>` and `<table-wrap>` branches beside it.
  Measured: 158 references in 14 articles, and **nought** where a real
  reference label was overwritten, so the entire population was a number the
  publisher never wrote on a reference that has none — #116's own symptom,
  an invented value rather than a blank. The markers are not lost; they sit in
  `citation`, where the deposit puts them.

  Found in the review of PR #148, and settled with a measurement rather than a
  preference: `<citation-alternatives>` at 0 of 216 is what killed the
  one-reference-per-part option, and the empty separator at 586 of 586 is what
  chose the join.

- **A `<mixed-citation>`'s rendered string lost every non-inline child**
  (#146). `JATSReferenceInfo.citation` is built from the `<mixed-citation>`
  text buffer, and a child that accumulates a buffer of its own without
  merging it back has its text *taken and not returned*. `<person-group>`,
  `<article-title>`, `<source>`, `<year>`, `<volume>`, `<issue>`, `<fpage>`,
  `<lpage>` and `<pub-id>` are all in that state — which is the whole of a
  standard NLM deposit — so the string bmlib rendered was whatever direct
  character data was left over: the punctuation between the children.

  ```
  deposit  : Smith, J, Doe, A. An observed effect. J Med. 2020;10(2):100-109. doi: 10.1/xyz.
  citation : '. . . ;():-. doi: .'      # before
  citation : 'Smith, J, Doe, A. An observed effect. J Med. 2020;10(2):100-109. doi: 10.1/xyz.'
  ```

  A `<mixed-citation>` is JATS's *mixed content* citation — the marked-up
  parts with the depositor's own punctuation between them, deposited as they
  typeset it — so every descendant's text is the citation's too, and `citation`
  now holds that whole string in document order.

  **The rule is a property of the context, not of the element.** PR #141 fixed
  this same shape for `<collab>` and `<string-name>` by adding them to
  `_INLINE_ELEMENTS`, which was right there because those two carry a name
  wherever they appear. It cannot serve here: an `<article-title>` in
  `<article-meta>` is the *article's* own title, and merging it unconditionally
  would append it to whatever buffer happened to be open. So the merge is
  conditioned on a `<mixed-citation>` being open above the element — an
  *ancestor* test rather than the parent test the module usually makes
  (`<label>`, `<caption>`, `<article-id>`), because mixed content is inherited
  down the whole subtree: a `<surname>` sits inside `<name>` inside
  `<person-group>`, and each merge composes into the one above.

  **`<element-citation>` is deliberately excluded, and leaves `citation`
  empty.** That content model is element-only, so the depositor authored no
  string and the whitespace between children is insignificant; concatenating
  them yields a run-together word or the depositor's indentation as a
  separator. Assembling a reference for display is a citation-style decision,
  and `formatted_citation` is where this library makes it.

  Excluding it from the *merge* turned out to be necessary and not sufficient,
  which the review of this PR established. A child bmlib does not accumulate
  never withheld a buffer to begin with — its characters go straight to
  whatever is open — so a routine book deposit carrying `<edition>`,
  `<publisher-loc>` and `<publisher-name>` produced `'3rd edAmsterdamElsevier'`:
  precisely the run-together word the exclusion exists to avoid, and the
  opposite of the empty string it was documented to leave. So the close arm
  writes `citation` for `<mixed-citation>` **only**. That also settles a `<ref>`
  carrying both spellings — legal as bare siblings and inside
  `<citation-alternatives>` — where the unconditional write was
  last-writer-wins and an `<element-citation>` deposited second wiped the
  string the publisher did typeset. Several `<mixed-citation>` in one `<ref>`
  was left as a modelling decision rather than a defect fix at this point in
  the work, and is the entry above — settled in the same PR by measuring the
  population.

  **What moves for a caller, measured rather than reasoned.** The first
  account of this was wrong in the direction that matters — it said every
  structured field was already correct and only the fallback rendering could
  move. Diffing this branch against `main` over 880 local PMC articles /
  20,770 references gives:

  | | references | articles |
  |---|---|---|
  | `citation` rebuilt from the merge | 3,541 | — |
  | `citation` emptied (element-only leak removed) | 958 | 84 |
  | `citation` changed, total | 4,499 (21.7%) | 191 |
  | `authors` changed | 502 (2.4%) | 14 |
  | rendered HTML changed | 576 (2.77%) | 23 |

  `authors` is a structured field and it moves, because the `<surname>` and
  `<given-names>` arms are gated on `in_ref_person_group`: a cited
  `<string-name>` deposited outside a `<person-group>` — Wiley's house style —
  had no arm fire at all, so the merge is the only route by which the name is
  collected. On `main` those references held `[]`, or entries like
  `[',', ',', ',']` and `[', Jr.']`; they now hold names. So a downstream
  holding cached full text should re-fetch for `authors` as well as for
  `citation`.

  That path needed one repair of its own before it was safe: the `<collab>`
  and `<string-name>` reference arms append the element's buffer directly,
  end-stripped only, so a name the publisher wrapped across lines arrived as
  the literal `'J.\nTan'` — a line break mid-name, in a public list and in the
  cached HTML. Both arms normalise now, as every other author on that list
  already did via `finish_current_author()`.

  Found in the review of PR #141. Walking the other paths the merge rule
  reaches turned up two more things. A cross-reference to a figure or table is
  *replaced* by a `[text](#rid)` link rather than merged, and nothing pinned
  that: dropping the suppression passed the whole suite while emitting
  `Figure 1[Figure 1](#f1)` into body prose. It has tests now. And the same
  taken-and-not-returned shape lost a `<tex-math>` formula from the prose
  around it and a `<disp-formula>` from the article outright — filed as #147,
  since delimiting LaTeX in prose is a decision rather than one more member of
  a set. **#147 is fixed in this same unreleased window, and the citation path
  is not the exception this entry used to name**: `_FORMULA_PARTS` is
  subtracted from the merge after the ancestor test, so a `<tex-math>` in a
  `<mixed-citation>` is rendered by the formula arm like any other — one
  rendition, delimited, no preamble. The 0 of 10,671 `<mixed-citation>` across
  227 articles and 0 in the local corpus sizes an unexercised path, which is
  all it ever sized.

- **A contributor whose name arrived undivided was dropped** (#120, #140).
  JATS names a contributor with `(name | string-name | collab | …)` and bmlib
  read only the first: `_AuthorBuilder.build()` refused anything without a
  `<surname>` and the call site dropped it without a word. A `<collab>`
  consortium author — *"the INHERIT Trial Group"*, *"NIH-ManNAc Study Team"* —
  therefore vanished from **34 of the 1,025 open-access articles drawn in the
  PR #118 review (3.3%)** — a count of `<contrib>` elements carrying no
  `<surname>`, which is a set both undivided spellings share, so it is a rate
  for neither of them alone — and an
  article naming every contributor with `<string-name>` parsed to **no authors
  at all**. Each came back as a well-formed shorter list, which reads as "this
  article credits nobody" rather than as a parser that looked in the wrong
  place.

  Both are now collected, verbatim, each in a field of its own —
  `JATSAuthorInfo.collab` and `JATSAuthorInfo.string_name`. Verbatim because
  splitting *"Ahmed Al-Rashid"* into a surname and given names is a decision
  about particles, multi-word surnames and name order, assumed rather than
  measured and undetectable by the caller once stored; and out of `surname`
  because that field is sorted and de-duplicated on, where an organisation is
  indistinguishable from a person. `full_name` prefers a structured name over
  both, since a `<contrib>` carrying a `<name>` *and* a `<collab>` is *"Smith,
  on behalf of the Y Group"*.

  **This moves what a corpus holds.** Those authors now appear in
  `JATSArticle.authors` and in the `<p class="authors">` line of the HTML
  `FullTextService` caches, so stored full text is not comparable across the
  upgrade. Both spellings now also reach `JATSReferenceInfo.authors` when
  cited, gated on the whole citation rather than on `<person-group>` — JATS
  admits either as a direct child of `<mixed-citation>`. **A contributor may
  now carry an empty `surname`**, where before a collaboration produced no
  entry at all, so code reading it unconditionally should read `full_name` or
  branch on `collab` / `string_name`.

  Four things the extraction needed beyond the two fields, three of them
  shapes this module has been caught by before. **`<contrib>` is a stack**,
  with `in_contrib` and `current_author` derived from it: a `<collab>` may
  carry a `<contrib-group>` of the collaboration's own members, so a
  `<contrib>` opens inside another, and held as one slot each member overwrote
  the consortium's builder while its close cleared the flag — #115 one element
  family over. A *non-author* `<contrib>` pushes a `None` frame, because
  skipping the push lets an editor's end tag pop the author's own, and reading
  the nearest builder instead of the top of the stack writes that editor's
  surname into the consortium; one fixture kills both, and both were live
  mutants. **A contributor is listed where its `<contrib>` opened**, the
  exhibits' reserve-and-fill, or a consortium is listed behind the members it
  encloses — and the reservation is *given back* where the `<contrib>` names
  nobody, so an unfilled slot keeps meaning "never closed" and cannot make the
  audit ERROR on the well-formed `<anonymous/>`. **`<string-name>` accumulates
  a text buffer and merges it back**: accumulating so its close reads its own
  text rather than the ancestor's, inline so a `<mixed-citation>` printing a
  bare one keeps that author in the citation string it renders. Its own text
  fills the field only when no structured name arrived, since JATS lets
  `<string-name>` carry `<surname>` and `<given-names>` children and the
  buffer then holds only the punctuation between them — and testing `surname`
  alone short-circuits, so the guard reads `given_names` too. The merge is
  **refused while any `<contrib>` is open**: the nearest accumulating ancestor
  of a roster member is the enclosing `<collab>`, so an unconditional merge
  appended every member to the consortium's own name. `<collab>` joined
  `<string-name>` in `_INLINE_ELEMENTS` at the same time, having had the same
  defect all along — it was accumulating and not inline, so a consortium-
  authored reference lost its author from the rendered citation string.

  On the reference side the divided shape needs a **flush** rather than a
  refusal. Appending the element's own buffer put a bare `","` in
  `references[].authors` *ahead* of the name it belongs to, and rendered it
  into the reference list; and since only `</name>` and `</person-group>`
  finish a pending cited author — neither of which closes between two adjacent
  `<string-name>` — the first of two divided siblings collapsed onto the
  second, which was a silent loss of a cited author predating this work.

  The end-of-parse audit gains `open_contribs` and `unfilled_author_slots`,
  each naming what its imbalance costs — `TestTheAuditNetIsComplete` forced the
  accounting *decision* the moment the stack existed (its exclusion sets are
  name lists, so it can demand that someone choose and not that a field
  appear), which is #134's mechanism working as
  intended. A `<contrib>` from which no name could be read is counted and
  reported **once per article at WARNING** from `_audit_parse` rather than
  dropped in silence, which is what kept both spellings invisible for as long
  as they were — the level and granularity `rejected_spans` settled for the
  same reasons (#129), and emitted at end of parse so it can name the article.
  It reports that *bmlib read no name*, never that the document carried none:
  `<on-behalf-of>` is a fourth spelling, JATS-legal and still unextracted
  (#144), and an article naming its only contributor that way reached the
  **quiet** branch of the zero-author detector until that spelling was added to
  `front_contributor_name_count`. `JATSAuthorInfo.is_named` now defines "did
  any spelling arrive?" on the public type — deliberately not a raising
  `__post_init__`, which would be #129 exactly.

  `JATSAuthorInfo.affiliations` is marked **reserved**: it is public,
  documented, and has never been populated, since the parser has no `<aff>`
  handler (#145).

  **The rule is spec-driven, and the population is not measured here.** JATS
  says the name is undivided; refusing to split it is the same "measured, not
  assumed" rule the rest of this module runs on, and no rate changes it.
  `scripts/sample_jats_exhibits.py` gained the counters that answer how much
  of a corpus each spelling reaches (section 11: the spelling vocabulary,
  nested `<contrib>`, `<collab>` rosters, and articles naming every
  contributor undivided), and **the #138 redraw has now run them**, scoped so
  a peer-review `<sub-article>`'s reviewers no longer inflate a count the
  parser never sees. Across 12,659 `<contrib>` in the two committed corpora:
  `<collab>` names 14 contributors (all in the recent window:
  0.18% [0.11-0.30] of *that* window's 7,798)
  and **`<string-name>` none at all** — 0 of 12,659, upper
  bound 0.03%, which is a measured absence rather than an omission, the
  vocabulary being open. **No** `<contrib>` nests inside another, **no**
  `<collab>` carries a roster, and 2 of 997 recent articles name every
  contributor undivided. The first two were 20 and 1 in the draw this one
  replaced, and `<on-behalf-of>` 1 against 0 here — populations this window
  does not reach rather than ones it refutes. So the rules stand on the spec, as they always did, and the two
  spellings now have populations of very different sizes rather than none.
  The 3.3% above is still #120's own figure, from the PR #118 review rather
  than from a committed corpus, and it counted `<contrib>` elements carrying
  no `<surname>` — a set both spellings share — so it remains a rate for
  neither of them alone and should not be quoted for either. Section 11's
  spelling vocabulary is deliberately **open**: every non-excluded child of a
  `<contrib>` is counted under its own name, because against a closed list an
  unforeseen spelling falls into `(none)` and is reported as a contributor
  naming nobody — #121's mis-certification inside the instrument built to
  detect the next #120. It is that openness which makes the `<string-name>`
  zero readable as a zero.

- **A malformed `colspan` cost the whole article** (#129). `colspan` is CDATA
  in JATS, so `colspan="two"` — or `"1.5"`, or a whitespace-only value — is
  well-formed markup, and `startElement` read it with a bare `int()`. The
  `ValueError` propagated out of the SAX callback and out of
  `JATSParser.parse()`, and every call site in `fulltext/service.py` sits
  under a tier-level `except Exception` logging at DEBUG — so one malformed
  attribute on one cell lost the entire article, and the tier chain then
  reported it as *unavailable from that source*, which is a far larger claim
  than "this table has a bad span". `_read_span()` falls back to 1 and names
  the value at DEBUG. `rowspan` needs no companion — this module never reads
  it — and a negative or zero value needs none either, since `start_cell`
  already clamps with `max(1, …)`. An empty `colspan=""` is still normalised
  ahead of the parse rather than reported: it is an absent value, not a
  malformed one, and reporting it would stop DEBUG distinguishing the values
  worth looking at.

  **Both halves of that were wrong, and review caught them before release.**

  *A refused span is not cosmetic.* The first draft justified DEBUG on the
  grounds that "a cell spanning one column instead of two is a cosmetic defect
  in one table". It is not: `_build_html_table` fixes the column count from
  the **first** row and `_pad_row` pads short rows at the **end**, so a span
  rendered as 1 instead of 2 does not blank a cell — it slides every later
  cell in that row one column left. Under headings `Group | n | Mean | SD`, a
  row the document wrote as `Mean=42, SD=7.1` renders as `n=42, Mean=7.1,
  SD=''`: wrong numbers under the right headings, with no visual tell, cached
  to disk by `FullTextService` and read downstream by an LLM as fact. Refused
  spans are now counted on the handler and reported **once per article at
  WARNING** from `_audit_parse` — once per article because a 40-cell table
  emitted 40 identical lines, and WARNING rather than ERROR because a
  publisher's deposit *can* reach this one, unlike the audit beside it, so
  raising it to ERROR would spend the "an ERROR here means bmlib is wrong"
  contract the audit depends on.

  *And the bound was on the wrong end.* `_read_span` guarded the value `int()`
  **refuses** and left the value it **accepts** unbounded, while
  `_TableBuilder.end_cell` materialises `colspan - 1` empty strings per cell.
  A 305-byte document declaring `colspan="20000000"` rendered a 320 MB
  `html_content` at ~2.1 GB peak RSS in 2.4 s — which `FullTextService` then
  wrote to its disk cache — and a larger value raises `MemoryError` out of the
  SAX callback, which is #129 verbatim: `MemoryError` is not a `_BUG_TYPES`
  member, so `_warn_swallowed_bug` never fires and the chain reports the
  article as unavailable in silence. `_MAX_COLSPAN = 1000` bounds it, matching
  the `MAX_HEADING_LEVEL` idiom already in the file; no real table is a
  thousand columns wide, so the bound costs nothing a document plausibly
  meant.

- **The audit's own net had a hole, and its ERROR channel a false positive**
  (#134, found in review of this PR).

  `implicit_body_section` — the single-slot builder holding unsectioned
  `<body>` prose, structurally identical to `current_author` and
  `current_reference`, which were both listed — was **missing from
  `_ROUTING_FLAGS`**. Stranded, the article loses that prose outright while
  `has_body` stays `True` (`body_paragraph_count` already counted it), so
  neither the model nor the audit said anything: exactly the silent loss the
  module was written to catch. It was covered only *transitively*, by
  `in_body` being cleared on the adjacent line — an accident of layout, not a
  property anything asserted. Added, and the rule that governs the list is now
  mechanised rather than stated.

  `current_article_id_type` was **set unconditionally and cleared
  conditionally**: the open sets it for every `<article-id>`, while the clear
  sat two levels inside `if parent == "article-meta" or self.in_front:`. An
  `<article-id>` outside `<article-meta>`/`<front>` — JATS-invalid, but this
  parser is deliberately lenient about invalid markup — stranded it, and the
  audit then reported a **correctly parsed** article as a bmlib defect. A
  false accusation twice over, since a stale value mis-routes nothing (the
  next open overwrites it). The clear is dedented to the branch, which is a
  parse no-op: the value is read only above that line.

- **A zero-author parse no longer certifies what it did not check** (#121,
  found in review of this PR). The detector separated "mis-routed" from
  "genuinely author-less" by counting `<surname>` in `<front>` — but JATS
  names a contributor with `(name | string-name | collab | anonymous | …)`,
  and bmlib extracts only `<name>`. So the two spellings it does not extract
  both landed in the quiet DEBUG branch and were reported as *genuinely*
  author-less: `<collab>` (#120), which loses some authors, and
  **`<string-name>` (#140, filed), which loses every one of them**. Counting
  surnames alone, an article whose entire author list was dropped read as an
  article that had none — the precise failure #121 exists to end.

  `front_surname_count` becomes `front_contributor_name_count` and counts all
  three spellings, so both shapes now take the WARNING branch. Counting is not
  parsing: extracting either remains open. And the quiet branch now reports
  its **evidence** rather than a conclusion — "its `<front>` named no
  contributor via `<surname>`, `<string-name>` or `<collab>`" instead of "the
  article appears genuinely author-less".

- **A `<title>` renamed the section it sat in, and a `<caption>` was routed by
  the wrong exhibit** (#125, #130, #123) — one defect wearing three hats.
  A `<title>` was routed by "is a section open?" and a `<caption>`'s prose by
  "is an exhibit open?", when both belong to the element that encloses them.
  The `<label>` parent test settled the same question in #116, and the
  argument carries: it needs no enumeration of the elements involved, which is
  what made this uncloseable by inspection.

  **`<sec>` is far from the only JATS element carrying a `<title>`.**
  `<fn-group>` is modelled `(label?, title?, (fn|p)+)`, and `<ref-list>`,
  `<glossary>`, `<app>`, `<boxed-text>` and every `<caption>` carry one too.
  Any of them renamed the enclosing section — leaving not a blank but a
  heading the publisher never wrote, which is why it survived so long.
  eLife's *Additional information* section holds an `<fn-group>` per
  contribution type, so PMC8754430's heading was overwritten twice and the
  last one won (#125); a `<boxed-text><caption><title>` at section level did
  the same (#130), and there the caption's `<p>` children still reach the
  section, so that half corrupts without losing anything.

  **Measured, and this half is not a small population.** Counting only a
  `<title>` that a `<sec>` was open for and that no exhibit already excluded:
  **411 titles in 104 of 997 recent articles — 10.4% [8.7-12.5]** — owned by
  a `<caption>` (387, in 94 articles), a **`<def-list>`** (12, in 12) and an
  `<fn-group>` (12, in 3). The `<def-list>` is the parent test's argument
  restated: every draw taken has turned up an owner neither issue mentions,
  the window this one replaced offering a **`<list>`**, and no enumeration
  written from #125 and #130 would have held either. #125's own `<fn-group>`
  shape is in *this* draw, where it was in neither of the two before it, and
  also reproduces on PMC8754430 — 12 titles in 3 articles is a floor for that
  shape rather than a rate. The redrawn back-filled window carries none,
  holding no `<caption>` at all.

  Both shapes were checked end to end against the real deposits, old parser
  against new. PMC8754430's back matter section reads *Author contributions*
  before and *Additional information* after; PMC12755737's reads *Analysis of
  10 Candidate Orphan Proteins Per AlphaFold Confidence Category.* before —
  a `<supplementary-material>` caption's lead — and *Supporting information*
  after.

  **`in_caption` was a stored boolean, so #123's two halves failed together.**
  A `<caption>` nested inside a figure's own was appended to the figure *and*
  its close cleared the flag, dropping the figure's caption tail after it. A
  depth counter fixes only the second: the inner legend's owner is not an
  exhibit bmlib models, so counted rather than named it still lands on the
  figure. The state is therefore a stack **of owners**. Both of that half's
  populations were **empty in every draw before the final redraw and are not
  now**: 6 `<caption>` of 8,111 recent nest inside another, and 6 inside an
  exhibit are owned by a `<supplementary-material>` rather than by the exhibit
  enclosing them. All twelve are one article — eLife's PMC12143881, which also
  carries every nested `<fig>` in the window — depositing its figure
  supplements as captioned `<supplementary-material>` inside the `<fig>` they
  belong to. That is the shape earlier comments asserted and no draw could
  find, so both halves are exercised by a committed corpus rather than only
  argued for, and as one publisher's house style rather than as a rate. (The
  seven-article corpus in the sibling Swift repository deposits its supplements
  as nested `<fig>` instead, so eLife uses both shapes.) The premise
  the rule rests on measures **full** — 6,938 of 6,938 exhibits carry a
  direct-child `<caption>` — so the parent can never come up empty where the
  old rule found something, and that is a measured result rather than a
  symmetry with the `<label>` premise, which the same redraw broke. The
  back-filled window contributes to neither count, holding **0 `<caption>`**:
  its zeroes are an absent denominator.

  `_innermost_exhibit()` and `_ExhibitFrame.open_seq` go with it: their only
  caller was caption routing, and naming the owner is exact where "the
  innermost exhibit open anywhere above" was merely usually right.

  **The exhibit test the parent rule replaced on the section branch stays on
  the abstract branch.** JATS admits a `<fig>` and a `<table-wrap>` inside an
  `<abstract>` — a graphical abstract — and the `if in_figure or
  in_table_wrap:` that used to open the whole `<title>` arm swallowed every
  title inside one. Routing by parent replaced that arm, so without an
  explicit guard a `<table-wrap-foot><fn-group><title>` in an abstract flushes
  the pending abstract section and installs itself as the next heading,
  splitting the abstract and re-attributing the prose after it: #125 one
  branch over, and the worse half of it, since `abstract_sections` is rendered
  into the HTML `FullTextService` caches while `body_sections` reaches no
  bmlib path at all. Caught in review of this change, before release. The
  population **measured empty** — 44 exhibits inside an `<abstract>`, none
  carrying a `<title>` — but that was an ad-hoc walk over the two 300-article
  draws #138 has since replaced, and `scripts/sample_jats_exhibits.py` carries
  no counter for it, so the 44 is **not re-derivable from the repo** and the
  next reader must re-measure rather than trust it. The guard is kept for the
  reason the `<alternatives>` archival tiers are: an empty population is not
  an impossible one, and what it prevents is silent and, through the cache,
  permanent.

  **Behaviour change for a caller of `JATSParser`.** A section renamed by a
  footnote group, a boxed text or a list keeps its own heading, and the
  usurping title is dropped rather than relocated — it was never a heading and
  bmlib models none of those containers. Only the `<title>`: a section-level
  `<caption>`'s `<p>` children never enter the exhibit branch and still reach
  the section's prose, which is issue #137. A figure caption truncated by a
  nested one comes back whole. `JATSArticle.body_sections` and
  `.figures`/`.tables` therefore move for roughly one article in ten; nothing
  a bmlib *sync* stores is affected, since no bmlib path carries them.

- **A nested `<fig>` dropped its parent figure** (#115). eLife wraps every
  figure supplement inside the figure it belongs to — the convention that
  motivated the issue, though the measurement below counts every publisher's
  nesting, not eLife's share. `current_figure` was
  a single slot: the inner `<fig>` overwrote the parent's builder, the inner
  `</fig>` emitted the child and cleared the slot, and the parent's own
  `</fig>` then found nothing to build. The parent figure — label, caption and
  graphic — was lost outright. **Measured, and none of the rates survives the
  #138 redraw:** the original survey put nesting at 19.6% of 225 open-access
  Europe PMC articles and a later 276-article draw at **0.7%** (2 articles,
  both eLife, losing 6 of 12 and 5 of 11 figures); neither draw is in the
  repo. The two committed corpora reproduce the **shape** for the first time
  and refute both rates: **7 nested `<fig>` and 0 nested `<table-wrap>` across
  1,997 articles**, every one of the seven in a single eLife article
  (`PMC12143881`, 7 of its 19 figures). One article in 1,997 is a fact about
  which publishers a draw catches rather than a rate, so neither 19.6% nor
  0.7% is re-derivable here — but the house style they describe is, and this
  is the first committed evidence that exercises the stack at all.
  Separately, PMC8754430 carries 12 and
  the parser returned 9, the three missing ones being exactly those with
  supplements. `in_figure` was cleared by the inner close too, so whatever the
  parent had left was read under the enclosing `<sec>`'s rules and reprinted
  as article prose, reaching `body_sections`, `has_body` and the rendered
  HTML — and so any downstream scan over parser output. Not
  `bmlib.transparency`, which regexes the raw XML itself and never sees
  `JATSParser`; that exposure is **#119**.

  The open figures are now a **stack**, with `in_figure` and `current_figure`
  derived from it rather than stored, so a stored flag cannot be reintroduced
  by a later early return. The other half is **slot reservation**: the entry
  in the figure list is reserved when `<fig>` opens and filled when it closes,
  because an exhibit is *built* at its end tag but has to be *listed* at its
  start. Plain pop-and-append restores the parent and still fails, listing
  every supplement ahead of the figure that contains it, so a test that only
  counts figures does not tell the two apart.

  `current_table` was the same single slot and is fixed the same way: a
  `<table-wrap>` opened inside another's `<table-wrap-foot>` lost the outer
  table entirely. Unmeasured, unlike the figure case, but structural — and
  found only because the figure fix was being pinned.

- **A `<table-wrap-foot><fn><label>` overwrote the table's own number**
  (#116). A footnote carries its marker — `a`, `b`, `*` — as its own
  `<label>`, and every `<label>` was routed on the ambient "am I in a
  figure/table?" flags, so the last footnote marker won. PMC12661592's single
  table reported its label as `"a"`. **Measured:** 27 of 225 surveyed articles
  (12.0%) carry a labelled `<table-wrap-foot><fn>`; `<fig>` has the identical
  hole, JATS admitting `<fn>` there too. An overwritten label is not inert
  either — the marker is rendered as the exhibit's own number, so the symptom
  is a *wrong* number rather than a blank. (The renderer used to substitute
  `Table {i + 1}` / `Figure {i + 1}` for an exhibit carrying no label of its own;
  #162 removed that, so mis-routing is now the only route to an invented
  number.)

  **The label is now routed by its parent element**, `<label>` being a direct
  child of the exhibit it numbers. That replaced a first cut which counted
  footnote depth: the depth needed an enumeration of every container whose
  `<label>` is not the exhibit's, and review showed the enumeration could not
  be completed by inspection — an `<fn-group>` directly inside a `<fig>` (no
  `<table-wrap-foot>` to wrap it), a `<disp-formula>`'s `(1)`, a `<media>`'s
  `Video 1` and eLife's `<supplementary-material>` `Figure 1-source data 1`
  each still overwrote the number, with a different plausible-looking wrong
  answer. Asking the parent needs no enumeration, and is exact where the depth
  was merely close: an exhibit opened *inside* a footnote keeps its own label,
  because its `<label>`'s parent is the exhibit either way. The marker itself
  is discarded rather than held, which is correct only because bmlib captures
  no footnote prose for it to belong to; that gap is filed as **#124**.

- **A figure with several `<graphic>` resolved to the thumbnail** (#117). The
  last deposit won, and publishers commonly emit the full image first and a
  thumbnail second. **Measured:** 58.0% of the 959 figures in the same
  225-article survey *that carry a `<graphic>` at all* carry more than one,
  and 52.9% end on a thumbnail, so the majority of figures resolved to a
  preview.

  Both figures are **superseded and neither is re-derivable** — the
  225-article survey is in no commit. `jats_parser.py`'s `_GraphicHolder`
  says so at the site and carries the redrawn measurement in its place:
  57.8% / 57.3% on the recent committed corpus and 44.0% on both counts on
  the back-filled one, with 0% depositing a thumbnail first in either. The
  shape of the finding — around half of all figures, never a thumbnail first
  — is what reproduces across every draw taken; the share is not.

  Position cannot decide it, because the two multi-graphic conventions
  disagree about order: a thumbnail is deposited *last* (PLOS, Springer) while
  an `<alternatives>` archival master is deposited *first*. First-wins was
  correct for every article measured, but it inverts wherever a master is
  deposited first, trading the thumbnail for a TIFF no renderer displays —
  unmeasured, and no corpus instance exists. The deposits
  are **ranked** instead — `ARCHIVAL < THUMBNAIL < FULL` — and one is accepted
  only when it is *strictly* better, which is what makes the first win among
  equals. "Thumbnail" is read from `content-type` **or** `specific-use` as a
  lowercased substring, neither attribute being case-controlled; **a thumbnail
  is never inferred from the file extension**, since every thumbnail in the
  corpus is a `.gif` only because PLOS and Springer both deposit that way, and
  elsewhere a `.gif` is the one image a figure has.

- **An undeclared archival master beat the web image beside it.** Found
  reviewing the #117 fix above, and a regression that fix introduced:
  `mime-subtype` is optional, so an `<alternatives>` TIFF that declares none
  ranked `FULL`, and — deposited first, under the strictly-better rule that
  makes the first win among equals — permanently beat the JPEG that followed.
  The pre-#117 "keep the last" resolved that case correctly.

  **An archival master *is* now inferred from the extension** (`.tif`,
  `.tiff`, `.eps`, `.ps`), and the asymmetry with the thumbnail rule above is
  deliberate rather than an exception to it: a first deposit is accepted
  whatever its rank, so demoting a master can only ever break a tie against a
  real web image, while a `.gif` rule would discard the only image a figure
  has. A lone master is still the figure's image, which is the test that pins
  the difference.

- **A nested exhibit's `<graphic>` was donated to the figure enclosing it.**
  The sibling of the entry below, and the one it missed: `<label>` and caption
  text were moved onto the exhibit stacks, but `<graphic>` kept asking
  `current_figure` — "the innermost figure open anywhere above". A `<graphic>`
  held by a nested `<table-wrap>`, `<fn>` or `<supplementary-material>` was
  offered to the enclosing figure, and #117's strictly-better rule is what
  made it stick: both deposits rank `FULL`, so the foreign href arriving first
  beat the figure's own for good, where "keep the last" had overwritten it.

  A `<graphic>` is now routed by its owning element, with `<alternatives>` —
  a wrapper around several encodings of one image — and `<p>` — prose flow,
  which holds an image without owning it — transparent. The `<p>` member is
  load-bearing rather than defensive: JATS admits `<p>` inside `<fig>`, and
  without it a figure whose graphic is wrapped in prose loses it outright.
  Same
  principle as the `<label>` parent test, and for the same reason: no
  enumeration of the containers that may hold a `<graphic>`. A table's own
  `<graphic>` was left with nowhere to go, `JATSTableInfo` having no graphic
  field, and was dropped with a DEBUG line naming the href and the table. That
  model gap was filed as **#127** and is fixed above, in this same release.

- **An inner exhibit's label and caption went to the exhibit enclosing it.**
  Found while pinning #116's exhibit-scoped label rule, and the same defect
  one level up: `<label>` and caption text asked whether a
  figure was open *anywhere above* before considering the table, so a
  `<table-wrap>` inside a figure's footnote lost its own number and legend to
  the figure. Both now route to the innermost open exhibit, which among
  properly nested elements is simply the one that opened last.

- **Every author dropped when the contributor role is declared on the group**
  (#111). `JATSParser` collected a `<contrib>` only where it carried
  `contrib-type="author"`. JATS lets the role be declared once on the
  enclosing `<contrib-group>` instead, leaving the children bare — and that
  is the dominant form in PMC: measured over 79 random open-access articles,
  45 (57.0%) parsed with **zero** authors while their XML carried surnames in
  `<front>`, a separate 249-article sample putting it at 60.6%. It failed as
  a well-formed empty list, so it read as "this article lists no authors"
  rather than as a parser looking in the wrong place. The group's
  `content-type` is now read and a bare `<contrib>` inherits it. Five rules,
  each pinned by a named test, of which the sample earns two. **Measured:** a
  contributor's own `contrib-type` decides on its own, so an `editor` inside
  an author group stays an editor and an `author` inside an editor group is
  still collected (33 of the 79 rely on it); and a group naming any other
  role is taken at its word, since the `content-type="editor"` group beside
  the author group appears in 23 of them and collecting it would be a new
  defect rather than a wider fix. **Not measured** — #111's sample contains
  no instance of any of these, so each rests on convention: a group declaring
  nothing is authors; an empty attribute declares nothing rather than
  declaring "not an author", which read as a declaration is the same silent
  loss for a document whose only fault is a stray empty attribute; and the
  comparison folds case, which the JATS Tag Library itself asks for on its
  `@article-type` page (*"JATS recommends a case-insensitive search for such
  values"* — written of a different attribute, so precedent rather than
  citation), which is the module's own habit (`pub-id-type` is folded a few
  handlers below), and which cannot cost anything, a role that is not
  `author` in any casing being excluded either way while an unfolded `Author`
  drops the group.

  The role is held as a **stack** of the open groups, innermost declared
  winning. A single value was wrong twice over, because `<collab>` legally
  contains a `<contrib-group>` — that is how a collaboration's member roster
  is tagged: the inner group's close cleared the enclosing group's
  declaration, so an `editor` group's own remaining members were collected as
  this article's authors, and the roster itself fell back to the
  authors-by-default rule. Popping restores the enclosing role and empties
  the stack at the outermost close, which is what a `<contrib>` with no
  enclosing group at all needs — out of place for JATS, and so exactly what a
  lenient parse must still answer for. Nothing here validates JATS, so that
  input has to be answered for.

- **A `<sub-article>`'s metadata and prose taken as the article's own**
  (#110). JATS lets a `<sub-article>` carry a complete `<front>` and `<body>`,
  and PLOS was observed depositing each peer-review round that way — PLOS,
  eLife, BMJ Open and F1000 all publish review histories as a matter of
  policy; every handler fired again inside one, into the same accumulators.
  PMC12774363 parsed as title "Associated Data", DOI
  `10.1371/journal.pgen.1012008.r006` — the sixth review round's, real and
  resolvable, so it does not 404 — and 230 body paragraphs of which about 180
  are reviewer correspondence, in exactly the funding, conflict and
  data-availability vocabulary a transparency scan hunts for. (That prose
  does *not* reach `bmlib.transparency`, which fetches and regexes the raw
  XML itself and never sees `JATSParser` output — the exposure there was
  real but separate, and is removed on the raw string, above.) Uncommon and
  severe rather than widespread — and **which population a rate is of decides
  what it means** (#158). Peer-review deposits specifically measured 4 of 249
  random open-access articles (1.6%), on a draw that is in no commit. The
  figure this repo can re-derive is a different one — how often an article
  *carries* a nested-article region at all, of any kind: **29 of 997 (2.9%
  [2.0-4.1]) in `tests/data/jats_exhibits.json`** and 0 of 1,000 in the
  back-filled corpus. That is the quantity bounding "loses body text", since
  an article can only lose content to a region it carries; it bounds nothing
  about peer review, a translation `<sub-article>` costing an article its
  prose while depositing no review round.

  Its interval overlaps the 3,382 of 97,909 (3.45%) `bmlib.transparency`
  counts over the same PMC `oa_comm` baseline package — **but the two read
  different renditions**, transparency the archive bytes and the sampler the
  `fullTextXML` the parser is fed, and `jats_exhibits.rendition.json` records
  Europe PMC *adding* regions in 5 of 300 articles (27 archive against 32
  served). The added element is the injected `associated-data` block named
  above — spot-checked live in three of those five rather than read off the
  artifact, which records counts and no `article-type`; each of the three
  gains exactly one `<sub-article article-type="associated-data">`. So
  they corroborate each other across a known difference rather than being one
  source. None of these is a rate *inside* the publishers that deposit review
  histories as policy, where it is far higher — the population is not random,
  which is why one number cannot serve for all four questions.

  `<sub-article>` and `<response>` now open a suppressed region in which no
  handler fires. The set of two is complete, and structurally so: of JATS's
  ~295 elements exactly three admit `<front>`/`<front-stub>` and `<body>`,
  and the third is `<article>` itself. The suppression is structural rather
  than driven by `@article-type`, which is `CDATA #IMPLIED` and whose four
  published vocabularies disagree — publishers deposit values in none of them
  (eLife's `decision-letter`, the F1000 platform's `response`), so no
  allow-list of types could have decided it. Peer review is not the only
  thing suppressed: `<sub-article>` also carries the alternative-language
  full text (SciELO's `article-type="translation"`), meeting abstracts, and
  Europe PMC's own injected `associated-data` block, which is absent from
  PMC's copy of the same record.

  A **depth** rather than a flag, since JATS permits a nested article inside
  one and a flag cleared by the inner close re-admits the rest of the outer;
  measured, 16 of 16 nested occurrences in one sample are a `reviewer-report`
  containing a `response`. Suppressed on the **opening** tags as well as the
  closes that write the outputs: an open leaves state behind, and for a
  nested article placed before the article's own `<body>` a nested `<sec>`
  whose close never comes pops nothing, so the article's section was filed as
  a subsection of a review round's and never flushed — losing the entire body
  to a document that is merely out of order rather than malformed. A float is
  worse: `<fig>`/`<table-wrap>` set flags the suppressed close never clears,
  and the leftover flag swallows the rest of the parse.

  The closing half is load-bearing on an **ordinarily ordered** document too,
  which is why it has its own tests rather than riding on the opening half's.
  Most handlers are already inert inside a suppressed region — they need
  `in_front`, `in_article_meta`, `in_body` or a non-empty section stack, none
  of which the suppressed open set. Two are not: `</abstract>` flushes its
  buffer without clearing it and only the opening tag clears, so a nested one
  re-emits the article's own abstract a second time; and `<article-id>` falls
  through to the shape-matching fallback when its type is absent or
  unrecognised, which would let a review round's identifier answer for the
  article's.

  The element and text stacks keep running, so the two stay balanced across
  the skipped region. `characters()` is the third thing that keeps running,
  and it is now guarded in its own right: text delivered by neither
  `startElement` nor `endElement` — character data sitting *directly* inside
  a nested article rather than in a child that pushes a buffer of its own —
  otherwise landed in whichever buffer was open above, which is the article's
  own paragraph. Unreachable in valid JATS, where a nested article's only
  parents are `<article>` and `<sub-article>`; reachable on input that is
  merely well-formed, which this module answers for rather than rejects.

- **Four ways a partitioned PubMed day could still report success it did not
  have** (#105, all found by the review of PR #114, each reproduced end to end
  before being fixed). A day recorded `completed` is never re-offered, so each
  of these lost records permanently and silently.

  A **derived count of zero dropped a range nobody had counted.** The ladder
  derives every right-hand child by subtraction, which holds only while both
  counts describe one instant, and planning spends one ESearch per split — so
  a range whose count grew between its parent's probe and its own left
  `n - left` at or below zero, and the arm that cheaply prunes a range
  *measured* empty discarded it. It is the one wrong derivation nothing
  downstream repairs: any other error still yields a part, and a part
  re-counts itself when its session opens, but a zero yields no part at all,
  so the range is never visited and every part planned around it reconciles
  perfectly. A six-record day fetched five and returned `completed` with
  neither note nor error. Non-positive derived counts are now measured; a
  strictly negative one, which cannot be stale but only impossible, also logs
  a warning.

  A **part whose count collapsed to a small non-zero number was checkpointed
  as clean.** The guard was written as exactly `== 0`, and its own argument
  never depended on that: a part reporting 1 where planning measured 5,000 was
  walked, delivered its 1, and reconciled that 1 against itself. Eight of
  twenty parts collapsing 10 → 1 completed a day holding 128 of 200 records.
  The fetch-time count is now reconciled against the planned one with the
  existing floor.

  A **day-level count of zero sealed a partially-fetched day and deleted the
  evidence.** Partitioning made that day reachable, and `sync()` drops a day's
  part rows the moment it completes — so a soft zero turned 20 checkpointed
  parts and 130,000 records into `completed` at `record_count=0` with no part
  rows, empty `errors` and empty `notes`. A zero contradicted by this day's own
  checkpoints now fails, naming what contradicts it.

  A **part's session ESearch failing had no test**, and mutating its
  `return failed(...)` to `continue` passed the whole suite — the same silent
  shape, in the most frequent request class on a partitioned day. It also
  reported no cause: a bare `ConnectionError` stringifies to nothing, so the
  day failed forever saying `part edat:a:b: `. Both handlers here now report
  the exception type.

- **A stored part checkpoint is read as strictly as a stored day** (#105).
  `PartCheckpoint.from_dict` used `str()` and `int()`, so a missing column
  raised `KeyError` and a null raised `TypeError` — neither caught by the
  documented `except ValueError` — and `str(None)` became the literal
  `"None"`, deserialising a null `part_key` into a key that matches no plan,
  which degrades resume to re-fetching every unfinished day with nothing
  raised. `PartCheckpoint` now validates on construction, and `_load_day_parts`
  — which runs *before* the per-day handler, inside a loop carrying only a
  `finally` — no longer lets one malformed row escape `sync()` and leave the
  whole multi-source run with no `SyncReport` at all.

- **A day is no longer refused on a number no ESearch returned** (#105). A
  parent counted higher than its children really hold parks the surplus on the
  right, and the root reaches 2100, so it walked down a structurally empty tail
  to a single future date claiming tens of thousands of records —
  `_UnsplittableDayError` named that derived figure and the day was re-fetched
  on every later run over a range PubMed has never indexed anything into. A
  single date is now measured before the day is refused on it: a phantom
  measures 0 and disappears, a date merely overstated becomes an ordinary
  part, and what remains is genuinely unsplittable with a measured count. A
  planning range that *is* one date is exempt, which is the re-partition path
  — measuring there is what `known_count` exists to prevent.

- **An over-cap day is no longer refused for want of a session it does not
  use** (#105). The history-session guard ran ahead of the branch that
  partitions the day, and `_fetch_partitioned` opens a session per part.


- **A PubMed day larger than 9,999 records is now fetched in parts, not
  walked into a wall** (#105, found measuring #96). NCBI's search backend
  serves only the first 9,999 records of a history session — `retstart=9999`
  is HTTP 400, and a page whose window crosses the boundary is *silently*
  clamped to it (`retstart=9500&retmax=500` → 499 records at HTTP 200).
  0.10.0's `fetch_pubmed` paged on regardless, asked for record 10,000, and
  failed the day with `Client error '400 Bad Request'` after twenty pointless
  requests, naming neither the cause nor the remedy. One day-size did **not**
  fail: a day of *exactly* 10,000 records never asks for a `retstart` above
  9,998, so it walked to its natural end, was silently clamped to 9,999
  delivered, cleared the shortfall floor, and was recorded `completed` —
  durable, never re-offered, one record lost with only a note.

  Both cases are now closed the same way. A day whose `[Date - Publication]`
  count exceeds what one session serves is partitioned into Entrez-date
  (`[EDAT]`) ranges — the fixed root `1900/01/01–2100/12/31`, recursively
  halved until every part is under the cap — and each part is walked as an
  ordinary day-walk with its own session, its own count and the existing
  stall and shortfall rules, after which the day's whole delivery is
  reconciled against the day's own count as well. A range rather than a facet
  because disjointness and coverage have to hold structurally: a record
  carries several publication types, so `AND pt1` / `AND pt2` fetches it
  twice and inflates delivery past the day's own count, which is what would
  hide a real shortfall. Before any record is fetched the root is verified to
  cover the day (`count(day AND root) >= count(day)`); short fails the day,
  because records outside the ladder are in no part's promise and every part
  would otherwise reconcile perfectly while the day is silently incomplete. A
  part whose own count comes back below half what planning measured fails the
  day too, rather than being walked at the lower number: two of bmlib's own
  measurements disagree, the weaker one does not decide, and letting such
  parts through was silent at a scale the day-level reconcile does not catch.
  The 10,000-record day is routed down that same path — its ten-thousandth
  record is requested with the rest — so the cap no longer refuses a day, and
  no longer silently truncates one. That is a statement about the cap and not
  a promise that a day arrives whole: a walk that comes up short but still
  clears the `SHORTFALL_FAILURE_RATIO` floor completes the day on a note here
  exactly as it does for every other source, and the cap has simply stopped
  being one of the things that can cause it. That floor is the one threshold
  in bmlib fixed before measurement, and partitioning raises what it exposes
  by roughly 24×: a `completed` PubMed day used to be able to lack at most
  4,999 records, where a 242,216-record day can now be `completed` missing
  some 121,000. The rule did not change, the population it applies to did —
  which is what makes issue #92 more urgent than when it was filed.

  **This is not an edge case in the field bmlib queries.** Measured
  2026-08-20: every first-of-month `[Date - Publication]` day holds
  49,543–90,571 records (a record carrying only a year and month is indexed
  at day 1) and every 1 January holds 212,439–315,282, against a median
  ordinary day of 4,890.

  **What it costs, which is the question a version number does not answer.**
  For a 242,216-record day (2024/01/01, its count and 37-part ladder measured
  live 2026-08-21): 40 planning ESearches, measured; one session ESearch per
  part, so 37, arithmetic over the measured part count, since **no session
  ESearch was ever issued**; and ~503 EFetch pages derived from the record
  count and the 500-record page size, **rounded up per part** rather than over
  the day, since `_walk_session` pages one part at a time — bounded 485–521,
  where 485 is what a single session would have cost — ≈**580 requests**, and
  at roughly 4 KB a record about **1 GB**. Everything after the planning ESearches is
  arithmetic and has not been confirmed by a full fetch. A six-year
  backfill window holds some 72 such days (66 month firsts at 49,543–90,571
  and 6 January firsts at 212,439–315,282), so roughly **6.2M records and
  ~25 GB — once**. Once, because the day is then `completed` and never
  offered again, where refusing it — the interim containment this supersedes,
  never released — re-offered the day on every run for the life of the
  installation and stored nothing at all. If that is not the trade you want
  for a given window, narrow the window: there is no flag, because an opt-in
  leaves "no publication is missed" false by default for exactly the
  operators least likely to find the flag.

  **A partitioned day resumes.** Each part's records are stored at its own
  boundary — which is also what keeps such a day out of memory — in one
  transaction with its checkpoint where it earned one, so a checkpoint can
  never attest to records a rollback discarded, and an interrupted run does
  not repeat the parts that finished. Flushing and checkpointing are separate
  questions: a part that came up short of its own promise without failing is
  still flushed, and is deliberately not checkpointed.
  A part is skipped on a later run only if its key is checkpointed
  **and** its count still matches what this run's plan reports — a part that
  has gained records since is re-walked, since skipping it would lose them
  permanently and silently. Skipped parts are credited to the day's
  reconciliation and to `download_days.record_count`, so a day fetched across
  three runs is not recorded as holding only the last run's share.

  **New storage:** a `download_day_parts` table, created by `ensure_schema()`
  through `CREATE TABLE IF NOT EXISTS` on both backends — an existing
  database gains it on the next call, with no migration script. Its rows
  describe an *unfinished* day: they are deleted in the same transaction that
  records the day `completed`, so a day that finished leaves nothing behind
  and what is in the table names the days a re-run will resume.

  **New public API:** `SourceDescriptor.resumable: bool = False`, and a
  `PartCheckpoint` dataclass (exported from `bmlib.publications`) describing
  one finished part — `part_scheme`, `part_key`, `promised`, `record_count`.
  `sync()` passes the per-part resume keywords only to a fetcher whose
  descriptor declares `resumable`. The default is `False` because
  `register_source()` is public: a third-party fetcher written against an
  earlier bmlib does not accept those keywords, and passing one would raise
  inside the per-day handler and record a working source's day as failed.

  **The cap is still a hard-coded 9,999.** One NCBI *raises* now costs
  unnecessary partitioning rather than a refusal — more requests, no records
  lost, and nothing logged above INFO; one it *lowers* is still not reliably
  caught, since for a band up to `EFETCH_PAGE_SIZE` wide no page is ever
  requested past the new limit and the part completes on a shortfall note.
  `scripts/sample_efetch_paging.py` is what detects either, and its
  `--partition` mode is the standing evidence for the ladder; see
  `docs/DECISIONS.md` for the measured band.

  **This dissolves #107** rather than answering it. That issue asked whether
  a known-permanent refusal should have a `SyncReport` field of its own,
  because a six-year backfill's ~72 structural days meant `errors` never
  returned to empty and an operator alerting on non-emptiness was paged from
  day one. That population no longer exists. What is left is one case the
  ladder cannot reach — a **single Entrez date** holding more than the cap,
  which cannot be split further — and that day is still `failed`, still
  re-offered, and still an `errors` line on every run. It is not a structural
  population, though: no such date occurred in six ladder walks over five
  real over-cap days. So `errors` returns to empty in the ordinary case, and
  if a later measurement finds stuck days are common, #107's `blocked` field
  is the right answer and the issue should be reopened.
- **An identifier is read from the type the document declares, not from its
  shape.** `JATSParser` took *any* `<article-id>` beginning `10.` as the DOI
  when its `pub-id-type` was absent or unrecognised, overwriting a DOI already
  read from `pub-id-type="doi"`. SAGE stamps every article it publishes with a
  filename-form copy of the DOI — the slash replaced by an underscore — under
  `pub-id-type="publisher-id"`, and puts it *after* the real one, so the wrong
  value always won: PMC12759138 parsed as `10.1177_20552076251406653` where
  its DOI is `10.1177/20552076251406653`. Two guards, either of which alone
  would fix that document, because neither is sufficient in general. A value
  that arrived under `pub-id-type="doi"` is **authoritative** and the shape
  fallback may no longer replace it — so document order cannot decide, which
  matters for a companion or collection DOI that is perfectly well-formed and
  would still pass any shape test. And the fallback now requires DOI *shape*,
  a `10.` prefix **and** a slash — the prefix and suffix of a DOI are joined
  by one and it is not optional — so the underscore form fails on its own
  merits even in a document carrying no typed DOI at all. `pmcid-ver`,
  `pmcaid` and `pmcaiid` were already recognised-and-ignored and needed no
  change; there is now a test that pins it, against a document carrying no
  plain `pmc`, since in one that does the fallback would decline the versioned
  id anyway and the test could not tell recognition from arriving second.
  Found porting the fix to BioMedLit (bmlibrarian_lite #142).

- **An untyped `PMC…` article-id no longer overwrites the PMC id** — the same
  defect one branch down, found while fixing the DOI. The typed branch stores
  `pmc_id` only `if not self.pmc_id`, and `JATSParser(known_pmc_id=…)` seeds
  it — which is how `FullTextService` passes the id it fetched by — but the
  untyped fallback assigned unconditionally, so an `<article-id>` under any
  unrecognised type could replace both.


- **A default template is installed atomically, or not at all** (#73).
  `TemplateEngine.install_defaults()` copied each default template with a
  bare `write_text` guarded by `if not dest.exists()`. A copy interrupted
  partway — a full disk, a killed process — left a truncated template that
  the guard then reported as installed, so it was never repaired. Jinja2
  renders whatever survived: a prompt missing its second half is not a
  `TemplateNotFound`, it is a prompt that renders and is sent to a model,
  with nothing logged and `install_defaults()` reporting success. The write
  now goes through the temp-file + `os.replace` publish that #70 gave the
  full-text cache, so a faulted copy publishes nothing and the next call
  installs it — the guard itself needed no change, the write being atomic is
  what makes it correct. Found while fixing #70 and deliberately kept
  separate, because the fix wanted a decision rather than two lines.

- **A user's symlinked template is no longer replaced by the default**
  (#73, found in review of this change). `dest.exists()` follows symlinks,
  so a symlink whose target is missing — a prompt kept on a volume that is
  unmounted at startup, or in a dotfiles repo not yet cloned — reads as
  absent, and the atomic publish replaces *the link* rather than writing
  through it as the old `write_text` did. The user's prompt was gone, the
  default was in its place, and the only trace was an `INFO` line
  indistinguishable from an ordinary first install. Such a destination is
  now skipped and reported at `WARNING`, since rendering falls back to the
  default with the user's own version unreachable.

- **A failed write names the file you asked for** (#73, found in review).
  The failing syscall operates on the temporary file `atomic_write` stages
  through, so `OSError.filename` named a path the caller never chose and
  that the cleanup had already removed — and at `fsync`, the failure the
  helper is built around, it named nothing at all. `FullTextService`
  interpolates that exception into the one warning it emits for a failed
  cache write, so an operator was handed a filename that was not on disk.
  Both cases now name the target. Behaviour visible to `except OSError:` is
  otherwise unchanged: same type, same `errno`.

### Added

- **`JATSArticle.suppressed_nested_articles`** — how many
  `<sub-article>`/`<response>` elements the parse skipped, a nested one
  counted separately, with a `logger.debug` naming each one's `article-type`
  as it opens. The `<sub-article>` suppression that closed #110 is otherwise
  entirely invisible: across 1,022 open-access articles parsed before and
  after, 288 lose body text and 5,520,938 characters are removed, and
  `has_body` flips on **none** of them, because it and
  `FullTextResult.content_kind` report only *total* loss. A translation
  sub-article alone can be ~90% as much text as the article itself. That 28.2%
  is a rate of articles *losing body text* on a draw held on one disk and in
  no commit; the population bounding it — how often an article carries a
  region at all — measures 29 of 997 (2.9%) in the committed recent corpus
  (#158). This is the one field that says a nested article was there at all.

- **`scripts/sample_efetch_paging.py`** — the instrument behind
  `EFETCH_MAX_RETRIEVABLE` and the fixed stride. Binary-searches the live
  backend for the largest `retstart` it serves, checks whether the straddling
  page is still clamped silently, compares a page's record elements against
  the session's own UID list, and sizes `[Date - Publication]` days against
  the cap. Run it before changing the constant or the page walk. `--partition`
  adds a second mode: it walks a real day's Entrez-date ladder and reports its
  shape — parts, depth, ESearch calls, whether the parts tiled the root
  exactly, and any Entrez date still over the cap. That is the standing
  evidence for #105's ladder, and specifically for the "no stuck Entrez date"
  claim, which is the one claim there that a future PubMed could falsify; it
  is a second, independent descent, deliberately not importing the planner it
  measures. Offline coverage in `tests/test_efetch_paging_sampler.py`, in the
  convention the other samplers follow: a probe that could not be made never
  prints as a finding — sharper here, since the measurement itself arrives as
  an HTTP 400.

### Changed

- **`"plc"` and `"pty"` join `_INDUSTRY_WORDS`, and the funder-matching
  comments now state the rule they are actually applying** (#112). The
  comments in `bmlib/transparency/analyzer.py` gave a measurement as the
  reason for each token's inclusion and exclusion — the measurements a future
  edit gets checked against — and nothing checked *them*. Eight claims were
  wrong, and not by drift: `tests/data/funder_names.json` has one commit and
  `_is_industry_funder` was byte-identical between that commit and now, so
  they were taken against a corpus revision that was never committed. They
  were internally coherent, which is why they survived: `0.917 = 11/12` and
  `0.324 = 11/34` describe one corpus holding 34 industry names, where the
  committed one holds 30, and the same revision explains the two constants
  recording what the pre-#36 matcher scored. The committed corpus reads
  **precision 0.909, recall 0.333** for this matcher and **0.357 / 0.167**
  for the one it replaced.

  Four further figures and one named example were wrong beyond those four
  headline readings. `"pharmaceutic"` is **3 TP / 1 FP**, not 3 TP / 0 FP —
  it holds the whole matcher's only false positive, which is what caps
  precision below 1.000, so the blanket claim that no stem has one was wrong
  at the one place it mattered. `"co"` is **4 TP / 0 FP**, not 4 TP / 1 FP,
  and the collision recorded against it (`"project co-sponsored by
  province…"`) is in no corpus entry; it stays excluded, but on a **stated**
  risk rather than a measured one, and the comment now records the true
  positive that costs — `"Merck & Co.; Merck Sharp & Dohme"`, which no other
  token reaches. The singular `"Key Laboratory"` appears **twice**, not the
  eight times recorded. And the `"pharma"` stem's five false positives were
  enumerated as "Pharmacy, Pharmacology and Pharmacogenetics, all academic":
  nothing in the corpus contains *Pharmacolog-* at all, and one of the five
  is the non-academic name `"pharmaceutic"` inherits.

  **Membership now follows four rules, and rule 4 vetoes the other three.**
  `"plc"` and `"pty"` were excluded for scoring no true positives while
  `"pharma"`, `"biotech"`, `"corp"` and `"gmbh"` were kept on exactly that
  score — so the stated rule was not the rule applied, and the next person to
  add a token could not tell which governed. The rules: corpus evidence earns
  a token (and refuses `"corporation"` at 1 TP / 1 FP); a reserved
  incorporation suffix is a **prior, not proof**, so it is kept where the
  corpus is silent; the residue of a disqualified stem is kept as a bare word
  where it cannot match more than the stem it replaced, which is what admits
  `"pharma"` and `"biotech"` — a category that had gone unnamed while the
  block claimed to cover every token; and a token colliding with a form the
  corpus cannot see is refused even where it passes the count, overriding the
  other three, which is what refuses `"ab"`, `"labs"` and the two-character
  candidates. The veto had to be written as a veto: `"ab"`, `"ag"`, `"bv"`,
  `"nv"` and `"sa"` are every bit as reserved as rule 2's members, so without
  a precedence the rules contradict each other on five tokens.

  **Rule 2 is a prior because the premise it was first written with is
  false.** "A public body cannot use the form" is not true of these suffixes:
  German and Austrian public research institutes routinely incorporate as
  GmbH (`"Forschungszentrum Jülich GmbH"`, `"Helmholtz Zentrum München
  GmbH"`) and UK charities and public bodies as companies limited by
  guarantee (`"Genome Research Limited"`), and all of them are flagged. The
  corpus holds such a name itself — `"Goethe Business School GmbH"`, labelled
  *ambiguous* as "an academic business school rather than a commercial
  research sponsor" — and because ambiguous entries are excluded from
  scoring, the `"gmbh"` row's 0 TP / 0 FP means *not scored*, never *not
  present*. Those costs are now pinned by tests rather than described, and
  #156 is the redraw that would measure them.

  Rule 2 admits `"plc"` and `"pty"`, **the one behaviour change**. Neither
  appears in the corpus at all, so no measured figure moves; what moves is
  that a funder named `"GSK plc"` is now flagged where it was not.
  `industry_funding_detected` feeds a HIGH-risk rule and HIGH downgrades a
  paper's quality tier, so stored transparency values are not comparable
  across this change for any paper with such a funder. `"plc"` is also the
  one member rule 4 reaches and does not refuse — PLC is the usual
  abbreviation of *phospholipase C*, so `"Role of PLC-gamma signalling in
  tumour invasion"` is flagged — kept because rule 4's other members collide
  with forms appearing in organisation names while this one collides with a
  research topic, though 41 of the corpus's 417 names run to ten words or
  more. Unmeasured, said so at the row, and #157 is what would settle it.

  **The correction is mechanised, because a comment cannot compute.**
  `tests/test_funder_matching.py::TestTheStatedCountsAreWhatTheCorpusHolds`
  parses the rows out of `analyzer.py` itself, and the headline table out of
  `docs/manual/transparency.md`, and re-derives all of them against the
  corpus — so a redraw fails the suite instead of leaving a stale number
  behind, and a token cannot enter either tuple without bringing its counts.
  A row now carries its own `in`/`out` and the rule that decided it, both
  checked against the tuples, because **arithmetic was never the defect**:
  counts alone stayed green while a row was moved into the refused block with
  its token still in `_INDUSTRY_WORDS`, which is #112's own shape. The
  corpus's size is asserted too — 833 drawn, 816 unique, 417 labelled, 412
  scoring, 30 industry — since every count is a numerator, and cutting the
  corpus to the names some token reaches reproduced all of them unchanged.
  Per-token scoring borrows the matcher's own `_compile_word_re` rather than
  hand-writing `\b…\b` a second time, a copy in which a dropped boundary
  moved four counts undetected. It fails closed: an unreadable source, a
  block whose delimiters have moved, a table reformatted out of recognition,
  or a token claimed twice all raise, because "I found nothing" must not be
  an answer it can return. The two float constants recording the pre-#36
  matcher's score are gone; that list is kept instead and scored live, so
  both sides of "it must beat what it replaced" move together. The
  `## [0.6.0]` entry below keeps the old figures as the record of what was
  believed then.

- **`register_source()` refuses `resumable=True` over a fetcher that cannot
  accept the resume keywords** (#105, review of PR #114). `sync()` reads the
  descriptor, so the mismatch used to raise `TypeError` inside the per-day
  handler — failing every day of that source, on every run, forever, at a
  place naming the day rather than the registration. A `**kwargs` parameter
  satisfies the check, since that is how the built-in fetchers absorb
  per-source configuration.

- **The PubMed page walk's fixed stride is now documented and pinned** (#96,
  closed as correct). `retstart` indexes the *session's UID list*, not the
  records delivered so far: measured against esearch's own `IdList`, a page's
  record elements are exactly the slice it named, in order. So a record
  missing from a page was requested and not returned, not postponed — and
  advancing by what arrived, as #96 proposed, would re-request the tail of
  every short page, deliver those records twice and count the duplicates as
  delivery, hiding a real shortfall from `reconcile_delivery`. No behaviour
  change; two tests now fail against that "fix".

- **`install_defaults()` says when it installs nothing** (#73, found in
  review). A `default_dir` that is not a directory — a typo, or a path that
  does not exist yet — made the method a silent no-op that reported success,
  which is the shape of the bug it exists to have fixed; it now logs a
  `WARNING`. Having configured neither directory stays at `DEBUG`, since
  that is a legitimate way to use the engine. Templates are also scanned in
  sorted order now, so which of them are installed before a failure is
  reproducible.

- **A default template is now copied byte for byte** (#73). `read_text`
  applies universal newlines and `write_text` translates back through
  `os.linesep`, so the installed file's line endings need not have been the
  source's — on *either* platform, wherever the default file's endings
  differ from the platform's convention, and not on Windows alone. Since
  bmlib ships no templates, `default_dir` is the caller's own directory and
  may hold CRLF on a POSIX host just as easily. What this buys is fidelity
  of the installed artefact for whatever tool opens it next; it is not a
  claim about what reaches a model, since the loader reads every template
  with `read_text` in any case. No stored value moves and no signature
  changes; anyone who has already installed the defaults keeps the copies
  they have, since an existing file is still skipped.

### Internal

- **The sampler now reads the *shape* of a 200 body, not only its status**
  (issue #211, from PR #208's review, with rider counters for issues #204,
  #206, #207, #210 and #188). `scripts/sample_api_failures.py` only: no
  library code changes and **nothing stored moves**. What it produced is a
  measurement, and it moved four open decisions and found a fifth defect.

  **The claim had no instrument.** PR #208 added four coercers to
  `transparency/analyzer.py` and priced them with *"no draw has seen these
  endpoints answer 200 with a non-object"*. Nothing here could support that:
  `ProbeOutcome` carried `endpoint`, `status`, `cause` and `measured` — HTTP
  statuses alone — and had never decoded a body, so the sentence was a count
  of what nobody looked for.

  `BodyShape` records each served body's top-level JSON type and, for every
  field the analyzer reads, that field's type over the bodies where its parent
  made it reachable. **The field list is derived, not restated**:
  `TestTheFieldListIsEveryFieldTheAnalyzerReads` walks `analyzer.py` with
  `ast` and holds the two sets *equal*, because the list written into issue
  #211's own text was already missing `source`, read by `_check_europepmc`
  since PR #208 coerced it into the full-text URL. The walk fails closed on a
  literal `.get()` in a function no endpoint claims, and carries an
  anti-vacuity floor. **Two element steps and not one**, because the module
  does two different things with a list: `_check_crossref` iterates every
  funder where all three `_epmc_records` callers take `records[0]`, so one
  sentinel would be wider than the code on one endpoint and narrower on the
  other. A field reachable in no served body says so rather than vanishing, or
  *"never wrong-typed"* and *"never asked"* print alike. `pubmed_efetch` is
  shaped too — `xml` / `empty` / `not-xml` — those being the two branches
  whose WARNING levels issue #193's status draw could not speak to.

  Four decisions blocked on a count ride on the same bodies at no extra
  request, and `trial_ids_for` now returns every accession with
  `probe_trials` applying `MAX_TRIAL_IDS_TO_CHECK` where bmlib applies it —
  capped at both ends, issue #206's count could never be taken. The address
  category restates the one literal it cannot import, the `inEPMC == "Y"` gate
  inline in `_check_europepmc`, so
  `TestTheAddressCategoryAgreesWithWhatTheAnalyzerDoes` drives both over the
  same bodies and compares which accession each would ask with; it cannot
  separate `not-claimed` from `unaddressable`, both making no request and both
  storing `NOT_ATTEMPTED`, and that indistinguishability **is** issue #207.

  **The first run (2026-09-08, 124 + 60 records) found every served body
  well-formed** — 0 non-object and 0 undecodable across the four JSON
  endpoints, 0 `not-xml` at `efetch`, and 0 empty at all five (CrossRef 74,
  EuropePMC 124, PubMed efetch 60, OpenAlex 74, ClinicalTrials.gov 55 bodies
  served; `pubmed_efetch` is XML, so "non-object" is not a category it has) — so no coercer added by PR #208 was observed to fire, and its
  *"nothing stored moves for a well-formed body"* is now measured rather than
  assumed. Read every share below with issue #212: two `SRC:PMC` strata
  contributed no record and a third kept 4 of 20, so the draw is MED+PPR plus
  four `PMC/2014` records — 60 + 60 + 4.

  Also measured: `hasResults` absent in **0 of 55** CT.gov bodies, which
  settles issue #210 in favour of the existing `False`; `unaddressable` **0 of
  124** and `neither` source answering **0 of 124**, which are issues #207's
  and #204's own populations and argue against both schema changes; the
  accession cap truncating **8 of the 30** papers in the 60-record trial draw
  that named an accession, against a partly-answered check of **1 of 30**, which are issue #206's two halves and reverse its emphasis; and
  the first non-200 this sampler has ever recorded, a single CT.gov 404 of 56
  probes, leaving every `_ORDINARY_STATUSES` set empty as before.

  **The largest finding is issue #188's, which was blocked on exactly this
  draw**: 43 of 124 records (34.7%), and 43 of the 53 claiming `inEPMC: Y`
  (81%), carry no `pmcid` and are addressed by the record's bare `id` — a URL
  that 404s, three of three probed, against a `pmcid` address serving 53 kB.
  All 84 such records in a 150-record `SRC:MED` draw are NCBI Bookshelf
  chapters, and a `bookid` is not addressable through `fullTextXML` either
  (three of three), so there is no full text to recover and the remedy is to
  stop asking. The `PPR` half of the same expression is confirmed
  load-bearing: 75,841 `SRC:PPR AND IN_EPMC:Y` records (2026-09-08; the 75,760
  quoted for #184 is the earlier draw of the same population), 50 of 50 sampled
  carrying no `pmcid`, and their `id` serves.

  Every new population reports ERROR rather than a share when it has none and
  can **flip the exit code on its own**, which is what
  `test_each_rider_populations_verdict_reaches_the_exit_code` pins — they are
  ANDed into two names rather than six. The shape term's first test
  passed for the wrong reason — with every probe 404ing the rider terms fire
  together — so its fixture now fails one endpoint only; the three
  record-level terms no fixture separates from the probe-level `is_reportable`
  are pinned on the wire instead.

  **PR #213's own review found the instrument reproducing three defects it
  exists to measure, and four guards pinned by nothing.** Every one is now
  mutation-verified — 24 edits, 24 killed.

  *Reported as the remote's fault when it was the script's.* A non-object
  EuropePMC 200 came back `no-record`, a category documented as EuropePMC
  answering and holding nothing, where bmlib refuses such a body at
  `_request_json` and stores `FullTextStatus.SEARCH_FAILED` — a loud failure
  read as a quiet absence, inside the denominator #207 and #188 are decided
  on. `observe_body` wrapped `resp.json()` in a bare `except Exception`, so a
  response object the script was wrong about printed a **valid** JSON body as
  `not-json`: `_report_swallowed_exception`'s defect one layer up, now split
  the same way, with the instrument's own kind, an ERROR line and an
  exit-code term. And `draw_records` read the envelope through
  `.get("resultList", {}).get("result", [])` — the idiom `_json_object`
  replaces, which printed *"unreadable body"* for a `resultList: null` that
  decoded perfectly — with the record loop outside the `try`, so a
  wrong-typed element raised out of `main` and discarded every paced request
  the run had already spent. It reads through `_epmc_records` now, and
  coerces each identifier, a mistyped one having been truthy enough to probe.

  *Measured on a population that was not there.* `shapes_reportable`'s whole
  floor was `bool(shapes)`, so one served body out of 180 printed `100.0%`
  with no interval while the status table above honestly reported the
  endpoint failing 99.4% of its probes — a non-200 is the measurement for
  that table and a hole for this one, so the module's own threshold now
  applies to it, and each top-level row carries its Wilson interval.
  `addressing_reportable` accepted a wholly `no-record` population, printing
  *"a full-text request would be made for 0 of 180"* at exit 0 — every drawn
  record came from that same API, so that is the lookup failing, not a
  finding. `_xml_kind` folded into `xml` the one efetch branch that is
  **silent** — a body that parses and carries no `PubmedArticle`, which is
  NCBI's error envelope and every Bookshelf PMID, so #188's own population —
  and it is now `no-citation`. `TrialCheck.answered` counted HTTP 200 where
  bmlib counts *"did `_check_trial_results` return non-`None`"*, inflating
  `complete` and deflating the two rows #206 turns on, and it is the
  wrong-typed-boolean shape `_json_bool` exists for. A draw thinned short of
  emptying a stratum reached no exit-code term at all.

  *Types.* `UNADDRESSED_CATEGORIES` names the complement so a sixth category
  must choose a side — `FullTextStatus.is_refusal`'s rule, which
  `ADDRESSED_CATEGORIES` was not following; `UNMEASURED` replaces a literal
  decided at seven sites across two vocabularies; `_THROTTLE_STATUSES` is
  shared with `probe`, an `http-429` having satisfied every clause and put a
  throttled probe into the failure share as a *failure*; `TrialCheck` gained
  the counter ordering that was its whole meaning in prose; `BodyShape`
  builds `fields` through a dict, and refuses a decoded object body carrying
  no category.

  *Prose.* Four claims were factually wrong and are corrected here and in
  `CLAUDE.md`, `HANDOVER.md`, `ROADMAP.md` and the manual: the draw is 60 MED
  + 60 PPR + **4 `PMC/2014`**, not "MED+PPR", which the report's own "7
  strata" says; `DEFAULT_TARGET`'s comment claimed the default *is* the
  measured draw when the run kept 124 of 180 — the invocation is
  re-derivable and the count is not, and EuropePMC's live search makes the
  draw itself unrepeatable either way; `draw_records`' contract promised at
  least `target` records "when every stratum answers", which that run had;
  and the module docstring's exit contract did not say a clean run now exits
  1. *"Every count is 0 today"* beside the not-served label was made false by
  this run's single CT.gov 404.

- **A stratum that answers and contributes no record is a hole too** (issue
  #212, found by the run above). `DrawnRecord` refuses a record carrying
  neither a DOI nor a PMID, correctly — `analyze()` accepts nothing else — and
  a `SRC:PMC` record carries neither, its only identifier being the PMCID. So
  `PMC/2024` and `PMC/2004` drew twenty records apiece and kept none, while
  the report read *"124 records over 7 strata"* with `failed_strata` empty and
  the run exited `0`: a source-and-year spread the sample does not have, which
  is the thing `summarise_draw` exists to prevent. The existing guard could
  not see it because it tests the *page*, and the loss happens one level down.
  `Draw.unusable_strata` names such a stratum, `summarise_draw` reports it as
  an ERROR, and the exit code carries it — so **the script now exits non-zero
  on a clean run** until issue #212's choice of population is made, which is
  honest and is the reason it is worth making.

- `_atomic_write` is promoted out of `fulltext/cache.py` into a new
  top-level private module, `bmlib/_atomic.py`, and is now
  `atomic_write` — the leading underscore moves to the module, matching
  `scripts/_sampling.py`. It was promoted rather than copied because the
  four load-bearing details in its docstring (the `fsync`, the UUID in the
  temporary name, the 0666 mode, the guarded cleanup) were each earned by
  #70's review, and that is exactly the knowledge that must not exist in two
  copies free to drift. Nothing public moves and the module depends on the
  standard library alone. Two things change for anyone reading logs: the
  cleanup's DEBUG line now logs under `bmlib._atomic` rather than
  `bmlib.fulltext.cache`, and its message drops the word "cache" now that
  the helper serves two packages — so a filter keyed on either the logger
  name or the old text needs updating.

- `tests/test_atomic.py` is new. The four load-bearing details stay pinned
  at the two call sites, where the behaviour is delivered; what the helper
  gained a test file for is the handful of guarantees no call site can see —
  the 38-character temporary-name overhead that `fulltext.cache`'s filename
  cap is arithmetic over (its own test has 41 characters of slack, so it
  cannot catch the two drifting apart), and the exception the caller gets
  back.

## [0.10.0] — 2026-08-15

**One family, seven issues: a sync day that reported success it did not
have.** `sync()` writes `status='completed'` to `download_days`, and
`_days_needing_fetch()` does not offer that day again once it is past — so
every one of #88, #89, #90, #91, #95, #98 and #99 lost the day's records
permanently rather than losing a request, with nothing logged above INFO.
Three kinds of cause, closed in three rounds, each round raised by the review
of the one before it. **The walk was never reconciled against the count the
source itself gave** (#88, reproduced on PubMed at `Count=5000` serving an
error document → `completed`, 0 records; and on OpenAlex at `meta.count`
5,000 delivering one work), and neither was the envelope it arrived in.
**A status the table did not recognise was read as success** (#89), a record
that failed to store did not fail its day (#90), and OpenAlex reported a
decode error as somebody else's problem (#91). And **a day was certified
before it had ended** (#95) — the instance needing no API malfunction at all,
firing on every ordinary run: a 09:00 cron durably lost the following 15
hours of indexing, and no reconciliation rule can see it, because the
source's own count agreed at 09:00. One rule replaces the old *today* special
case: a completed day is durable only once it was fetched at or after **12:00
UTC on the following day**, the instant day *D* has ended in every timezone.

The last round (#98, #99) is the rule **refusing to guess its own inputs** —
`DownloadDay.from_dict()` no longer substitutes *now* for an absent
`downloaded_at`, which is the most durable-looking value the rule can be
handed, and `sync()` validates its window at the entry rather than raising
`OverflowError` from inside day selection and losing the whole multi-source
run's `SyncReport`. Its own review round then found that guard checked
*values* where the dangerous inputs were *types*: `datetime` subclasses
`date`, so `date_to=datetime.now()` satisfied mypy, defeated every value
check, and on **both** ends raised nothing at all — writing
`download_days.date` values with a time component that no date-keyed lookup
can ever match.

Alongside the family, **CI now checks types** (#81). bmlib ships `py.typed`,
telling every downstream its annotations may be relied on, while CI ran ruff
alone — a downstream's own mypy run was the first thing in the world to check
them. The gate found one real defect (an efetch history-session hole
indistinguishable from a quiet day) and eighteen annotation errors.

**This is a minor bump because three changes reach a public API**, not
because anything stored moved: `DownloadDay.from_dict()` raises where it
defaulted (#98); a third-party fetcher's unrecognised status is recorded
`failed` rather than coerced to `completed` (#89), a behaviour change at the
`register_source()` extension point; and `bmlib[pdf]` floors
`pymupdf>=1.28.2`.

**The number cannot answer the data question, and here it is a real cost.**
Nothing stored moves — but **on the first run after upgrading, expect the
whole window to be re-fetched once**, because every row a previous release
wrote was written while its own day was current and none of them is durable
under the new rule (measured at 29 of 29 days for a 30-day window, per
source). It is one-off, self-correcting and idempotent (`store_publication()`
merges), but a wide window across several sources will make that run much
longer and may meet a source's rate limiter. Steady-state, the default window
now costs two day-fetches per run rather than one, which is the fix working.

### Added

- **CI checks types (#81).** bmlib ships `py.typed`, which tells every
  downstream that its annotations are meant to be relied on — a guarantee
  CI never verified, since it ran ruff only and none of `E, F, I, N, W, UP`
  catches a type error. A downstream's own mypy run was the first thing in
  the world to check them. A `types` job now runs mypy over the same
  3.11/3.12/3.13 range `requires-python` advertises (no `python_version` is
  set, so each entry checks against its own interpreter), with mypy pinned
  in the `dev` extra the way ruff is pinned in the workflow and the
  settings in `pyproject.toml`, so CI and a developer run the identical
  command. Beyond mypy's defaults: `disallow_untyped_defs` — without it an
  unannotated function is skipped in silence, so the gate would pass a file
  carrying no annotations at all, which is the exact hole `py.typed` denies
  — plus `warn_unused_ignores`, `warn_redundant_casts` and
  `warn_unreachable`. Anything deliberately unchecked carries an inline
  `# type: ignore[code]` with its reason at the site rather than a
  per-module override, because `warn_unused_ignores` reports the inline
  form the day it goes stale and would never report the override. bmlib
  has no untyped imports left — see the `pymupdf` entry under *Changed*.

### Fixed

- **`sync()` lost the whole run's report on an out-of-range input (#99).**
  `date_to=date.max` and an extreme `recheck_days` each raised
  `OverflowError` from inside day selection — the first from the loop's own
  `current += timedelta(days=1)`, the second from
  `today - timedelta(days=recheck_days)`. `sync()`'s `try` carries only a
  `finally`, so it escaped the whole multi-source run and took the
  `SyncReport` with it, before a single record was fetched and for every
  source rather than for one day. A new `_validate_window()` rejects both at
  `sync()`'s entry — before any source is touched and before the HTTP client
  is built — raising `ValueError` naming the offending parameter.
  Deliberately **not** an `except OverflowError` at the helpers: that would
  convert a caller bug into a day that quietly looks like it needs no fetch,
  which is the failure mode the rest of this family exists to remove. A
  negative `recheck_days`, until now swallowed in silence by
  `recheck_days > 0` and so delivering the opposite of what was asked, is
  rejected too. An **empty** window (`date_from` after `date_to`) is
  deliberately still accepted and now pinned by its own test: it is what the
  ordinary incremental-sync idiom produces once it has caught up
  (`date_from = last_synced + 1 day`, `date_to = today`), and it writes no
  row and claims no day. Pre-existing — the `date.max` case predates every
  rule in this family.

  Review of this fix found the guard answered a narrower question than it
  claimed, and it now **validates types as well as ranges**, on `date_from`
  as well as `date_to`. `datetime` subclasses `date`, so
  `sync(date_to=datetime.now())` satisfied every type checker and defeated
  every value check (`datetime.max == date.max` is `False`); mixed with a
  `date` it raised `TypeError` and lost the run's report, and on *both* ends
  it raised nothing at all — writing `download_days.date` values carrying a
  time component that no date-keyed lookup can ever match, so the day was
  re-fetched forever and the table filled with rows nothing reads. A `str`
  date, the type `DownloadDay.date` and `FetchResult.date` both use, escaped
  as `AttributeError`. And `recheck_days=float('nan')` slipped both range
  checks — every comparison against it is false — then disabled rechecking in
  silence, the same failure the negative case had just closed.

- **A fetcher that returned a non-`FetchResult` killed the whole run.**
  The `except Exception` around the fetcher call absorbed one
  that *raises*; one that *returns* — successfully — something without a
  `.status`, the shape a forgotten `return` produces, reached
  `_resolve_day_status` outside that handler. The `AttributeError` propagated
  through the `finally` and out of `sync()`, losing every source's
  `SyncReport` while leaving earlier days committed. `register_source()` is
  public, so the caller getting this wrong is a third party. The return value
  is now type-checked inside the existing handler's reach and recorded as a
  failed day, naming the offending type.

- **A day fetched *as* today synced as a complete day (#95).**
  `_days_needing_fetch()` re-offered `today` unconditionally and checked
  nothing about *when* a completed day had been fetched, so a day captured
  as today was stored `completed` and — being neither `today` nor `failed`
  tomorrow — was never offered again at the documented default
  `recheck_days=0`. With `sync()`'s default window of `[yesterday, today]`,
  a 09:00 cron durably lost whatever was indexed over the following 15
  hours. Nothing in the #88 family can catch it: the source's own count
  agreed at 09:00, because the walk really did deliver everything that
  existed then. One rule now replaces the special case — a completed day is
  durable only once it was fetched at or after **12:00 UTC on the following
  day**, the instant day *D* has ended in every timezone (UTC−12 is the last
  to finish it). The hour is not a safety margin: it is equally the point
  beyond which "now" can no longer fall inside day *D* anywhere on earth,
  which is why the rule *subsumes* the `today` branch rather than
  approximating it, and why the wall clock no longer *decides* whether a
  completed day is done — it is read only as an upper bound, which can move
  the answer towards a re-fetch and never away from one. Both cheaper rules
  are unsafe and not hypothetically — all three built-in sources are US-based
  (UTC−5 to UTC−8), so comparing UTC *dates* would call a fetch at 00:30 UTC
  on *D+1* durable while PubMed's own day *D* still had four and a half hours
  to run, and comparing *local* dates is up to 16 hours out for a machine in
  Sydney. Every day in a window is judged against its own boundary, not the
  window's first. A `downloaded_at` that cannot be *read* fails closed with a
  WARNING naming the row — the naive case in particular must not reach the
  comparison, since `aware >= naive` raises `TypeError` from inside day
  selection and would abort a whole sync rather than cost one merged
  re-fetch — and one that reads cleanly but sits in the future cannot be
  *true* and fails closed as well, since a restored backup or a bad RTC would
  otherwise make every affected day durable forever, which is this issue over
  again. `last_verified_at` is now read through the same kind of guard, laxer
  because only its calendar date is used: read raw, a corrupt value raised
  `ValueError` from inside day selection and killed the whole multi-source run
  before a single record was fetched, `SyncReport` and all. **Behaviour change
  to expect:** under the default window each day is now fetched once more, on
  *D+1* — two day-fetches per run rather than one, which is the fix — and a
  caller passing a window of three days or more whose run happens before 12:00
  UTC pays one more again; a run at or after 12:00 UTC pays nothing.
  **On the first run after upgrading, expect the whole window to be
  re-fetched once**: every row the previous release stored was written while
  its own day was current, so none of them is durable under the new rule
  (measured at 29 of 29 days for a 30-day window, per source). It is one-off
  and self-correcting, but a wide window across several sources will make that
  run much longer and may meet a source's rate limiter.
  `store_publication()` merges, so all of it is idempotent. This does **not**
  address late *indexing*, which is what `recheck_days` is for. 19 tests; 10 mutations, 10 caught.

- **A fetch that stopped short synced as a quiet day (#88).** Every built-in
  fetcher learns a record count before walking pages — PubMed's `<Count>`,
  OpenAlex's `meta.count`, bioRxiv's `messages[0].total` — and none of them
  compared it against what arrived. A walk that stopped early therefore
  returned `status="completed"`, `sync()` wrote the day to `download_days` as
  done, and `_days_needing_fetch()` did not offer it again once it was in the
  past (with `recheck_days` at its default): the records are permanently
  absent, with nothing logged above INFO. Reproduced on PubMed
  (`Count=5000`, efetch serving an error document: `completed`, 0 records,
  `error=None`, 11 HTTP calls) and on OpenAlex (`meta.count` 5,000, one work,
  no `next_cursor`). The comparison now lives once in
  `publications/fetchers/_reconcile.py` and applies **three rules that differ
  in kind**. A *stalled* walk — a page delivering nothing while the source's
  own count says records remain — is broken outright and carries no
  threshold; it is also the only rule that catches a history session expiring
  on the last page of a long walk, so every fetcher computes and passes it.
  *Unreconcilable* delivery — records arrived against no count at all —
  cannot be shown to have finished and so cannot complete, while nothing
  delivered against no count is the ordinary quiet day. And a walk that ended
  naturally but came up short is judged against a floor,
  `SHORTFALL_FAILURE_RATIO = 0.5`, with a smaller gap logged at WARNING,
  completed, and returned as `FetchResult.note` — which `sync()` collects
  into the new `SyncReport.notes`, apart from `errors`, since that day will
  not be retried and is otherwise invisible after the run.

  The floor rather than strict inequality is the load-bearing choice: a day
  recorded `failed` is re-offered on **every** later run, so failing on a gap
  that is benign *and permanent* re-fetches and re-merges that whole day for
  the rest of an installation's life, growing with the date range. Benign
  causes are real — a record withdrawn between search and fetch, an index
  moving under a long walk. Unlike bmlib's other thresholds, this one is
  **fixed before measurement** and says only what can be argued without data;
  #92 is the follow-up that measures the per-source distribution and tightens
  it, and until it runs `0.5` must not be read as a measured value.

  Two supporting changes. Each fetcher now **checks its envelope** instead of
  reading it through `.get()` defaults, since an HTTP-200 error body is
  otherwise indistinguishable from a day with no publications: PubMed refuses
  an efetch response that is not a `PubmedArticleSet`, carrying NCBI's own
  error text into the message and stopping the walk instead of paging on
  (10 useless requests on the measured day, of which up to 9 follow the
  stall); OpenAlex requires `results` to be a list and `meta` an object with
  a numeric `count`; and bioRxiv refuses a body carrying **neither** a
  `collection` key **nor** messages — one making no claim about the day at
  all. bioRxiv's guard is deliberately not `isinstance(data.get("collection"),
  list)`: it reports a quiet day by omitting `total`, and whether it also
  omits `collection` is unmeasured, so requiring that key risks failing every
  quiet day on every run for ever. One case stays indistinguishable from a
  quiet day — an error body carrying messages and no collection — and #94 is
  the sampler that would measure bioRxiv's real shapes and close it. And
  PubMed reconciles **delivered elements** rather than parsed records:
  efetch delivers `<PubmedBookArticle>` elements the fetcher deliberately
  skips, so counting parsed records would report a phantom shortfall on every
  day carrying a book chapter — and then re-fetch that day forever. Delivery
  counts those two element names rather than every child of the set, since
  `<DeleteCitation>` is also legal and counting it both masks a shortfall and
  stops an otherwise-empty page from registering as a stall.

  One previously-accepted OpenAlex response changes verdict: a first page
  with `"meta": null` used to complete (a guard added so it could not raise
  `AttributeError` mid-walk). It now fails. The no-crash invariant is
  unchanged and still pinned by its own test.
- **`sync()` converted a fetcher's failure into a durable success (#89).**
  The status was read through a denylist — anything not exactly `"failed"`
  became `"completed"` — so a fetcher reporting failure in any other spelling
  had that failure written to `download_days` as success. The error still
  reached the transient `SyncReport`, which is the worst combination: the run
  looks noisy while the database looks clean, and the database is what the
  next run consults. It is now an allowlist: a status that is neither
  `"completed"` nor `"failed"` is logged and recorded as failed, and named in
  `SyncReport.errors`. Failing closed is right because `register_source()` is
  a documented extension point, and a third-party fetcher is exactly the
  caller who will not know the convention. `_days_needing_fetch()` now reads
  the same way — anything that is not `"completed"` is re-offered — so a
  status the table does not recognise costs a re-fetch instead of silently
  counting as done. The validated status is typed
  `DayStatus = Literal["completed", "failed"]` from `_resolve_day_status`
  through `_upsert_download_day`, which makes writing a third value into
  `download_days` a type error; `FetchResult.status` stays a bare `str`,
  since it is a boundary value from a public extension point and narrowing it
  would break third-party fetchers under their own type checker.
- **A day whose records failed to store was recorded `completed` with an
  empty error list (#90).** `day_failed` was counted and logged per record but
  never influenced the day's stored status, and `errors` was appended to only
  when the *fetch* reported an error — so a day where every record raised was
  stored as done with `record_count=0`, never retried, and `SyncReport.errors`
  was empty. The only trace was per-record log lines plus an aggregate counter
  naming neither the source nor the day. Any store failure now records the day
  `failed` and appends `"{source}/{date}: N record(s) failed to store"`;
  retrying is safe because `store_publication()` merges. The per-record
  handler stays broad — one bad record must not lose the batch — but now logs
  the exception **type** and the day, so a `TypeError` affecting every record
  no longer reads as bad data from the source. Worth knowing: a record the
  storage layer will never accept pins its day into a retry on every run,
  loudly rather than silently.
- **An OpenAlex decode error was attributed to the wrong layer (#91).**
  `response.json()` sat outside the `try` guarding the HTTP call, so a
  malformed body escaped `fetch_openalex()` entirely and was caught by
  `sync()`'s generic handler, which logs it as "Fetcher raised". The day was
  still retried, so this cost diagnosis rather than data. Moved inside the
  guard; the other two fetchers were checked and already decode inside theirs.
  The record loop is now guarded the same way, since `isinstance(results,
  list)` passes for a list of non-objects and `_normalize` then raised out of
  the fetcher for the identical wrong-layer report.
- **Diagnostics that vanished when the message was empty.** `SyncReport`
  collected a day's error through a truthiness test, so `str(OSError())` —
  the empty string — was dropped entirely: a deterministic failure retried
  the day on every run while the report showed no errors at all. It is now an
  `is not None` test. Alongside it, the three remaining handlers that
  reported a bare `str(exc)` now name the exception type, and the bioRxiv and
  OpenAlex fetchers log their failures instead of only returning them.
- **A PubMed day with no history session synced as an empty day.**
  `_esearch()` returns `(count, web_env, query_key)` with both session
  values `str | None`, and `fetch_pubmed()` guarded only `count == 0`. A
  response carrying a count but no `WebEnv`/`QueryKey` left both `None`,
  which httpx encodes as an empty parameter — so every efetch page asked
  NCBI for `WebEnv=` and got back a document holding no `PubmedArticle`.
  Measured on a 5,000-record day: 11 requests, 10 of them useless, and a
  result of `status="completed"` with 0 records and `error=None` — a broken
  fetch wearing the shape of a quiet day, which a caller cannot tell from
  one. It now returns `failed`, naming what was missing, before any page is
  requested. Found by the type gate above, which is what the gate is for.
- **A search NCBI rejected synced as a day with no publications.** The same
  failure as above, one step earlier and past that guard. `_esearch()` read
  the count as `int(_text(root.find("Count")) or "0")`, and `_text()`
  returns `None` for an absent element as well as an empty one — so an
  NCBI `<ERROR>` document (unknown db, invalid term, throttled key: all
  answered with HTTP 200 and no `<Count>`) became a count of 0 and returned
  `completed` at the `count == 0` branch, which sits *before* the
  history-session guard. `sync` then wrote the day to `download_days` as
  done and never retried it, so the records were permanently absent with
  nothing logged above INFO. An absent or non-numeric `<Count>` now raises,
  which the existing handler turns into `failed`, and NCBI's own error text
  is carried into the message. A genuine `<Count>0</Count>` still completes.

### Changed

- **`sync()` reports a window reaching into the future.** A day
  that has not ended cannot satisfy the durability rule — which needs a fetch
  at or after 12:00 UTC on the following day — so every future day was stored
  `completed` and re-offered on every subsequent run, for the life of the
  installation, at no log level and in no field of the `SyncReport`.
  Permanent *and* invisible is the pair the shortfall rule and
  `FetchResult.note` exist to break up, so this takes the same answer: a
  `SyncReport.notes` line and a WARNING. Rejecting the window was weighed and
  refused — the past half of a window ending tomorrow is perfectly fetchable,
  and raising would discard it too.

- **`DownloadDay.from_dict()` raises on an absent `downloaded_at` instead of
  substituting now (#98).** `_parse_datetime(None)` returns *now*, which is
  the single most durable-looking value the day-durability rule above can be
  handed: a row deserialised that way reads as fetched at the latest possible
  instant, so the day is never offered again. That is #95's own failure mode
  reached from the model side, and it fails **open** while the SQL path now
  fails closed on the same column — the model must not disagree with the rule
  about what an absent value means. The column is `NOT NULL` in both DDLs, so
  a dict lacking it did not come from the database. `from_dict()` now raises
  `ValueError` for an absent *or* null value, via a new strict
  `_require_datetime()` beside `_parse_datetime()`.

  **No behaviour changes today**: `sync()` reads `download_days` with raw SQL
  and never goes through the model, which is why this was filed separately
  from #97 rather than folded into it. The guard exists so that wiring the
  model onto the selection path later cannot inherit a fail-open default with
  nothing to catch it.

  Two things are deliberately *not* changed, both pinned by tests so they are
  not later "tidied" into consistency: the dataclass **default** still stamps
  now, because a freshly constructed `DownloadDay` describes a fetch that has
  just happened; and `from_dict()` does not re-judge a timestamp it can read
  — a naive or future value deserialises fine, since faithful deserialisation
  is the model's contract and usability is the rule's. `Publication`'s
  `created_at` / `updated_at` keep the old defaulting for the same reason:
  nothing decides whether work may be skipped from them.

  Review of this fix found the advertised contract was not the delivered one.
  `_require_datetime()` delegated to `_parse_datetime()`, so a non-`str`
  escaped as **`TypeError`** out of `fromisoformat` — a caller writing the
  documented `except ValueError` got an uncaught crash — and an unreadable
  string reported `Invalid isoformat string: ''`, naming neither the column
  nor the row. A plain `date` was the live trap: `isinstance(datetime_value,
  date)` is true but the converse is not, so it looked accepted and was not.
  Every rejection is now a `ValueError` naming the field. Nothing here could
  fail open — the durability rule refuses all of these values — so this is a
  contract fix rather than a safety one.

- **Eighteen annotation errors fixed** alongside the gate, none of which
  changes behaviour. The gate reports 20 errors in 10 files against the
  previous release; two are the PubMed defect above, and these are the rest.
  Nine were one decision: a `**kwargs: object` bag splatted into a callee
  that still has a typed named parameter *the call does not itself fill*
  cannot be checked — `object` is the stricter annotation and that is
  precisely why it fails, since a parameter declared `str | None` will not
  accept an `object`. Four such bags become `Any`; the eighteen that fill
  every named parameter, or are only inspected or forwarded untyped, keep
  `object`. (Being splatted is not on its own the trigger: `LLMClient.chat`
  and `LLMClient.embed` are splatted and correctly stay `object`.)
  `_FallbackLoader.get_source()` declared `tuple[str, str, callable]`,
  naming the builtin *function*, so that element asserted nothing; it is
  narrower than jinja2's `Optional[Callable[[], bool]]`, since this loader
  always supplies both, and is now spelled `Callable[[], bool]`.
  `QualityTier.__lt__` and a helper in `BiasRisk.from_dict` carried no
  annotations at all, and `__lt__` now narrows with `isinstance` rather
  than `self.__class__ is other.__class__` — identical on every possible
  argument, an `Enum` with members being unsubclassable. The OpenAlex
  fetcher's `cursor` is declared `str | None` (inferred `str`, it made
  `while cursor is not None` read as always-true and the return below it
  dead), a redeclared `result` in `text_utils.py` is annotated once, and a
  stale `# type: ignore[arg-type]` in `retractions.py` that `hasattr`
  narrowing had made unnecessary is gone. The remaining two are the untyped
  `fitz` import, fixed by the `pymupdf` change below rather than suppressed,
  and `_reject_unusable_stream()`'s `TextIOBase` guard, which is
  `# type: ignore[unreachable]` with its reason at the site — the annotation
  is a request and the guard exists for the caller who ignores it.
- **`bmlib[pdf]` now requires `pymupdf>=1.28.2`, and the converter imports
  `pymupdf` rather than the legacy `fitz` alias.** PyMuPDF added `py.typed`
  in 1.27.1, but writes it only into the `pymupdf` package — the modules
  copied into `fitz/` are never covered — so importing the alias costs a
  `# type: ignore[import-untyped]` that no future release can retire, and
  that ignore switches off type checking for all of `pdf_converter.py`.
  Measured: under the alias mypy does not report a call to a non-existent
  PyMuPDF attribute; under `import pymupdf` it is an `attr-defined` error.
  `>=1.27.1` is the minimum the type reason justifies (the module name
  arrived in 1.24.3); the floor is set at the then-current release instead.

## [0.9.1] — 2026-08-13

Four issues in the full-text retrieval path — #79, #68, #72 and #56 — all of
the same family as 0.9.0's: a failure that reported as a success, or a
success that reported nothing at all. Five smaller fixes found while
reviewing them are listed below with the four. Tier 1d was discarding about 95% of the free PDFs it exists to find,
because it recognised only the rarer of Europe PMC's two labels for "free";
a PDF that then failed to download was swallowed at `DEBUG`, so a caller who
asked for text and got a bare link could not tell a full disk from a
publisher 404; a bmlib defect raised by every PMC tier hid behind an
unrelated tier that still worked, degrading a whole corpus while reporting
success; and a PDF's metadata title beat the title printed on page 1, so a
typesetter's job number was stored as an article's title.

Two of these were closed by **measuring** rather than by reasoning, and both
instruments ship with the release. `scripts/sample_free_pdf_urls.py` sets
#68's log levels from observed failure rates and is the evidence behind #79's
allow-list; `scripts/sample_pdf_metadata_titles.py` built a 235-PDF corpus
(`tests/data/pdf_metadata_titles.json`) against which #56's acceptance rule
was scored — under a rule fixed before the corpus was collected. Not one of
the junk shapes issue #56 proposed appears in that corpus.

**One change moves what downstream stores**, and only one: #79 makes many
more articles come back with `pdf_url` / `file_path` / extracted text instead
of a bare link, so a corpus's stored full text is not comparable across the
upgrade and outbound traffic to Europe PMC rises. Nothing else here changes a
stored value. The single API addition, `ConversionResult.title`, is purely
additive and declared last.

### Added

- **`ConversionResult.title`** — the document's title where page 1
  corroborates the metadata's claim to it, and `None` otherwise (#56).
  Purely additive, declared last so positional construction stays stable.
  **`metadata["title"]` is unchanged** and stays a verbatim record of what the
  PDF says, junk and all: `creator` and `producer` sit beside it unmodified,
  so sanitising one key would make the dict lie about its neighbours, and a
  caller debugging provenance needs the original. Read `result.title` for the
  judged answer.

- **`scripts/sample_pdf_metadata_titles.py`** — the instrument behind the rule
  above, and `scripts/_sampling.py`, which now shares the per-host pacer, the
  clamped `Retry-After` and `wilson()` between both live samplers rather than
  letting a rule learned from a bad run exist in two copies.

  A bioRxiv attempt records the **posting day** it came from, and an unmeasured
  one also records a `cause` and an `attempts` count. Without the day, a
  resumed run could not retry what it had lost: that walk covers
  `[today-30, today-49]` recomputed from `date.today()`, so it slides a day per
  calendar day and after 20 shares nothing with the window that produced the
  journal — an unmeasured attempt stayed open by design but became
  unreachable, permanently inflating the population's unmeasured share with no
  escape but deleting the journal and losing every good row. Days owed a retry
  are now walked before the fresh window and in addition to it, so retrying old
  work never costs the run its budget for new work. `MAX_UNMEASURED_ATTEMPTS`
  bounds the tail: a retired attempt stops being offered but keeps being
  counted, and `summarise()` names how many were retried out.

- **`scripts/sample_free_pdf_urls.py` now measures the access-label
  distribution** it was already cited as the evidence for. It read neither
  `availability` nor `availabilityCode`, so a maintainer following the
  instruction to run it before changing `_FREE_PDF_AVAILABILITY_CODES` got a
  failure-rate table and no evidence either way. It counts every
  `documentStyle=pdf` entry by `(availability, availabilityCode)` and marks
  each row taken/SKIPPED — counted **before** the allow-list filters, since a
  distribution counted after it could only ever confirm it, and #79 was
  precisely a value that never appeared in what bmlib accepted.

  Three further corrections to the instrument: a 429/503 in the Unpaywall
  *resolution* phase is now unmeasured rather than invisible (that is where
  that API's limiter bites, and a throttled resolution phase printed as a
  confident rate over whatever got through first); `Retry-After` is clamped
  at a maximum as well as at zero, since an honoured `86400` is a run that
  prints nothing, gets killed, and loses every population — the same loss the
  zero clamp was reasoned about preventing; and `ProbeOutcome.ok` becomes a
  property of `cause`, because `ok=True` beside `cause="http-403"`
  constructed happily and would silently lower the rate that sets a
  production log level. bioRxiv now honours `--target`, and `main()` exits
  non-zero when any population printed `ERROR`.

### Changed

- **Europe PMC's free PDFs are now taken under their common label, not just
  their rare one** (#79). `_extract_free_pdf_url` accepted
  `availability == "Free"` only. Measured over 600 recent MEDLINE records,
  that is the rare label: of 326 `documentStyle=pdf` entries, 312 (95.7%) read
  `"Open access"` and 14 (4.3%) read `"Free"` — both the identical
  `?pdf=render` URL on the identical host. Tier 1d was silently discarding
  about 95% of the PDFs it exists to find; there is no log line for "a PDF
  entry was seen and not taken." It now allow-lists on `availabilityCode`
  (`OA`, `F`), falls back to the display string only for an entry carrying no
  code, and rejects a present-but-unknown code rather than trusting the label
  — an unknown value must under-credit, not risk a paywalled download. **This
  moves what downstream stores**: many more articles now come back with
  `pdf_url` / `file_path` / extracted text instead of a bare link, so a
  corpus's stored full text is not comparable across the change, and outbound
  traffic to Europe PMC rises, since PDFs the old code skipped are now
  downloaded.

### Fixed

- **A junk PDF metadata title no longer beats the title on the page** (#56).
  `SectionSegmenter._extract_title()` returned any truthy `metadata["title"]`
  verbatim, so a typesetter's job number won over a perfectly good large-font
  first-page line. The issue proposed a reject-list of junk shapes; ground
  truth turned out to be free — every free PDF comes from a record that
  already states the article's title — so the rule was **measured** instead:
  a metadata title is believed only where page 1 prints it. Both sides
  normalise to lowercase alphanumeric tokens (line-break hyphenation closed
  up, line numbers dropped, NFKD, combining marks removed), which absorbs
  case, the terminal period, en-dash versus hyphen, ligatures, diacritics and
  a wrapped title's line break, while rejecting a string the paper never
  states. Containment is **anchored to whole tokens**: an unanchored substring
  test matches inside a word, in the accepting direction, so a `/Title`
  truncated mid-word — which producers emit routinely — was returned verbatim
  *and* beat the font-size fallback that would have recovered the whole line.
  Anchoring changes no verdict on any of the 235 measured rows.

  Measured over **235 real PDFs** (`tests/data/pdf_metadata_titles.json`;
  Europe PMC 175, bioRxiv 60), against a rule fixed before the corpus was
  collected: **0 of 126 conclusive good titles wrongly rejected** (ceiling
  1%; 95% CI [0%, 3.0%]) and **34 of 35 junk titles rejected** (floor 80%;
  95% CI [85.5%, 99.5%]). Both rules are thresholds, so both need the
  interval and not just the point estimate — and the two answer differently.
  The junk floor holds at confidence: its lower bound clears 80%. The
  wrong-rejection ceiling does **not** — 126 rows bound that rate at about
  3%, roughly triple the 1% named, so the corpus establishes ≤3% and a reader
  should not take ≤1% as measured. The one junk title
  accepted is not junk — the PDF's title reads "Drive" where the record reads
  "Drives", so the rule sided with the document in front of it. Where a junk
  title is rejected, the font-size fallback returns *some* title in 44% of
  cases (15 of 34) and nothing in the rest — but it returns one line, so what
  it recovers is the title's **first line** (38%, 13 of 34) and **never the
  complete record title** (0 of 34). A missing title is the intended trade,
  since a junk title is asserted as fact by a document the caller trusts.

  Two findings the issue could not have guessed. **Nearly 40% of Europe PMC's
  publisher-typeset PDFs carry no metadata title at all**, so the affected
  population is smaller than it looks. And **not one of the shapes the issue
  proposed** — `.docx`, `"untitled"`, the file stem — appears anywhere in the
  235; what appears is typesetter output: bare Appligent AppendPDF job
  numbers (14 of bioRxiv's 16 junk titles), Arbortext job numbers with page
  ranges (`"ma5c03166 1..10"`), QuarkXPress's `"Layout 1"`, InDesign template
  codes, an InDesign source filename, and a journal name truncated mid-word.
  A reject-list written from the issue's examples would have caught none of
  them.

  The reject-list survives only as a **backstop** for junk the document does
  print, and exactly one member earned its place under the same rule: a title
  of fewer than three words. It rejects `"Nepal Journ"` — a journal name in a
  running header, which page 1 genuinely prints, so corroboration has nothing
  to object to — and rejects no row whose metadata title matched its record;
  the shortest genuine title measured is five words. Short article titles do
  exist in the wild, so that member's false-positive risk is bounded by the
  corpus rather than disproven, and a title it rejects still falls through to
  the font heuristic.

  **Every rejection is logged at `DEBUG` with its reason and the offending
  title.** The four reasons collapse into one `None` at the API — every caller
  asks a binary question and would discard a richer answer — so the log is
  where the operator asking "why did bmlib drop the good title on this paper"
  gets an answer. Without it, a code path whose whole job is rejecting things
  said nothing at any level.

  **Not a behaviour change for stored data**: `SectionSegmenter` has no
  consumer inside bmlib yet, and the converter's change is a new field.

- **A failed PDF download is no longer invisible** (#68).
  `_download_and_cache_pdf` swallowed a non-200 response, a failed
  magic-byte validation, and any exception, all at `DEBUG` — so with
  `convert_pdfs=True` the caller asked for text, got a bare `pdf_url`, and
  could not tell a full disk from a publisher 404. The two server-side
  causes are now reported per `(tier, cause)`, at a level chosen from a
  measured rate against a rule fixed beforehand: under 5% of attempts, a
  per-article `WARNING`; at or above it, one line per `(tier, cause)` plus
  per-article `DEBUG`. Measured with `scripts/sample_free_pdf_urls.py
  --target 150 --per-host-interval 4.0`: `europepmc` 0.7% failed (n=150, 95%
  CI [0.1%, 3.7%], 1 transport exception), `unpaywall` 64.3% failed (n=28,
  95% CI [45.8%, 79.3%], 4 HTTP 403 + 14 not-a-pdf), `biorxiv` 0.7% failed
  (n=150, 95% CI [0.1%, 3.7%], 1 transport exception). Europe PMC and
  bioRxiv had **zero** server-side failures — every one of the 18 counted
  above is Unpaywall's, and 14 of those are landing pages rather than PDFs —
  so Unpaywall's rate, whose CI lower bound is roughly 9x the threshold,
  selected the one-shot variant. The exception path (a lost network, a full
  disk) is separate and needed no measurement: it fails every article once
  it starts failing, so it is one-shot per `(tier, exception type)`
  regardless of the rate rule. `_save_pdf_to_cache` now returns
  `tuple[str | None, Literal["saved", "write-failed", "not-a-pdf"]]` so a
  failed cache *write* is reported as a write failure rather than blamed on
  the publisher's bytes. `FullTextCache.save_pdf`'s own magic-byte rejection
  drops to `DEBUG` with it: at `WARNING` it emitted a line per article for
  the dominant measured failure — Unpaywall landing pages, 14 of 28 probes —
  behind a message promising the report was one-shot, defeating the one-shot
  for the very cause the 5% rule selected it for.

  Both keys are built from a bounded `origin` — `"europepmc_pdf"`,
  `"unpaywall"` or `"known_source"`, written out at each of the three call
  sites — rather than from `result.source`. For Tiers 1d and 2 those
  coincide, but a Tier 0 `source` comes from the fetcher's
  `FullTextSourceEntry`, and OpenAlex derives it from the location's venue
  display name: one distinct, remote-data-derived string per journal or
  repository, which would turn "reported once" into one warning per article
  over a bulk sync. The source still appears in the message, so the first
  report names the specific venue. The message says the report is one-shot
  without asserting the failure is common: #79 makes `europepmc_pdf` the
  dominant emitter, and Europe PMC measured zero server-side failures.

- **A bmlib bug no longer hides behind a tier that still works** (#72).
  `_TierFailures.describe()` is consulted only on total exhaustion, so an
  `AttributeError` raised by every PMC tier — the shape a `JATSArticle` API
  change takes — with Unpaywall still healthy silently degraded a whole
  corpus from structured JATS to bare links, reporting success throughout.
  `_TierFailures` gains an `on_bug` callback fired at the moment a
  defect-shaped exception is swallowed, not at an exit: every exit-based
  alternative is the defect itself, since the next early return would
  silently re-break it. `_BUG_TYPES` deny-lists `TypeError`,
  `AttributeError`, `NameError`, `KeyError`, `IndexError` — a deny-list
  because the legitimate failures are varied (`FullTextError`,
  `httpx.HTTPError`, `OSError`, ...) while the always-a-defect set is small;
  `ValueError` and `SyntaxError` are deliberately excluded, since
  `json.JSONDecodeError` *is* a `ValueError` and
  `xml.etree.ElementTree.ParseError` *is* a `SyntaxError`, so either would
  misreport an ordinary malformed remote response as a bmlib defect.
  `WARNING`, once per `(service, exception type)` — a defect that hits one
  tier hits it for every article, so per-article would be unreadable exactly
  when it mattered, but a second, different defect still gets its own line.
  `on_bug` is a mandatory field, not an optional one: an unwired callback is
  not a quieter channel but total silence, since `describe()` is read only at
  the exit this case never reaches. `_TierFailures.unreported()` is the
  deliberate opt-out for direct helper calls and tests.

- **A malformed `fullTextUrlList` is skipped, not reported as a bmlib defect.**
  `_extract_free_pdf_url` iterated `.get("fullTextUrl", [])`, which is `None`
  rather than `[]` for a key present with a JSON null, and the resulting
  `TypeError` is a `_BUG_TYPES` member — so Europe PMC's malformed bytes were
  reported as a defect in bmlib *and* spent the one-shot `bug:TypeError` slot
  a later genuine defect needs. `_entry_is_free` guards its own two reads the
  same way; this is the container one level up.

- **A cache-write failure is reported per cause, not per site.** The key was
  the bare literal `"cache-write"` while `_warn_once`'s own documented rule is
  to name the cause. Both writers catch bare `Exception` and funnel here, so a
  transient `OSError` early in a run permanently silenced a genuine bmlib
  `TypeError` inside `save_pdf` — held at `DEBUG`, which is the failure mode
  #72 exists to fix — and, in the other order, presented a type error to the
  operator as a full disk.

- **An unquarantinable cache entry is reported** — the last swallow-to-`DEBUG`
  of a bmlib defect in `fulltext/service.py`. The consequence is permanent:
  the corrupt entry stays in the lookup path, so the per-article "could not
  read the cached full text" warning repeats every run for that article for
  ever, and an undecodable HTML entry keeps hiding a good PDF behind it. The
  operator saw the symptom on every run and never the cause.

- **A failing text extraction is no longer reported as a failed download.**
  `_attach_pdf_text` ran under `_download_and_cache_pdf`'s handler, after
  `result.file_path` was set, so an exception escaping it produced "there is
  no file and no extracted text" about an article whose file was cached and on
  the result — and a defect-shaped exception was filed as a transport fault.
  It now reports as the defect it is and keeps the cached PDF, since the
  download did succeed.

## [0.9.0] — 2026-08-10

Five fixes, every one of them in the full-text retrieval path and every one of
them the kind a bugfix release exists for: a failure that looked like a
success. A headline 0.8.0 addition — the stdlib-only `SectionSegmenter` —
turned out to be unreachable for anyone who installed core bmlib; an exhausted
retrieval chain returned a result byte-identical to a paywalled paper's; a
cache file truncated by a full disk was served as a complete article forever
after; one corrupt entry aborted a whole bulk sync; and a cache directory that
could not be created killed `FullTextService` construction outright, on a run
that would have succeeded without a cache at all.

None was found by a failing test. Three came out of reviewing the previous fix
in the chain — #70 and #71 from #67's, #75 from #74's — and #64 from
smoke-testing the published 0.8.0 wheel in a venv holding nothing else.

**Nothing stored moves.** No score, no parsed value and no cached content
changes shape, so unlike 0.6.0 through 0.8.0 — which each moved stored values,
compounding — this release needs no re-sync. The only new output is log lines,
and a retrieval that succeeds without a cache fault emits none of them. A
cache that cannot be written to or read back now warns on a run that otherwise
succeeds, which is the point of those two fixes.

**A minor bump, for a release that is only bugfixes.** Three of the fixes
change a public API — `save_html`/`save_pdf` raise where they used to write a
partial file, `sanitize_identifier()`'s output moves for a long identifier,
and `FullTextService.cache` is now nullable. "Nothing stored moves" is a
statement about *data*, not about the API, and bmlib's downstream pins are
written on the convention that a minor bump is the one that may change the
API. A patch number would have delivered all three to anyone on a `<0.9.0`
range with no decision on their part.

**Four API notes.** `save_html`/`save_pdf` now raise `OSError` where they
previously wrote a partial file — a break for a *direct* `FullTextCache`
caller only, both `FullTextService` call sites having already reported a
failed cache write. `quarantine()` is new. `sanitize_identifier()` caps its
readable prefix at 160 characters, and this one reaches every caller rather
than only a direct one, since `fetch_fulltext()` builds its cache key through
it: an entry cached under a longer identifier is orphaned and re-fetched once.
The fourth reaches anyone who dereferences the attribute:
**`FullTextService.cache` is now `FullTextCache | None`**, so
`service.cache.clear()` wants a `None` check.
Because bmlib ships `py.typed`, a downstream running mypy or pyright sees a
new error on that line even though bmlib's own ruff-only CI does not.

**One note for operators.** #70 closes the window in which a truncated cache
entry is *written*; it does not detect one already on disk, and a truncation
of English-language prose usually lands on an ASCII boundary and decodes
perfectly. A cache written by an older version is best cleared once.

### Added

- **`fulltext` extra** (`pip install bmlib[fulltext]`, httpx), included in
  `all`. The manual previously sent readers to `bmlib[publications]` — a
  publication-ingestion extra — for a PDF segmenter. `pdf` stays separate:
  bundling pymupdf would duplicate an existing extra and drag a ~20 MB binary
  wheel onto anyone who only wants JATS retrieval. `publications` and
  `transparency` keep their own httpx, so no existing install changes.

### Changed

- **`FullTextService.cache` is now `FullTextCache | None`** (#75). It is `None`
  only when the `cache` argument was omitted *and* the default could not be
  built — a caller who passes a cache always gets it back. Code that calls a
  method on the attribute (`service.cache.clear()`) needs a `None` check, and
  because bmlib ships `py.typed`, a downstream running mypy or pyright will
  see a new error on that line even though bmlib's own CI, which runs ruff
  only, does not. Flagged separately from the fix below because the break is
  latent: it never fires on a developer machine or in CI, only in the broken
  environment where the operator already has a problem, and there it turns a
  `FileExistsError` naming the cache directory into an `AttributeError` far
  from its cause.

### Fixed

- **A total full-text retrieval failure no longer reads as "no free full
  text"** (#67). `fetch_fulltext()` wraps each of the swallowers on its path
  in `except Exception` that logs at `DEBUG` and moves on — right in itself,
  since an unreachable Unpaywall must not cost the DOI fallback — but the only
  `WARNING` on the path sat inside the `if abstract_only is not None:` branch,
  so the *more* complete the failure, the quieter it got. A caller who had
  lost the network, hit a bmlib bug or misconfigured the service received
  `source="doi"`, `html=None`, `content_kind="none"` for every paper in a
  corpus — byte-identical to the legitimate outcome for a paywalled paper —
  with nothing above `DEBUG` to say so. Attempts on the tier chain are now
  accounted for — the download half of the PDF tier is deliberately not, since
  every one of its call sites returns immediately after it and it could never
  feed the report (#68) — and the warning moved out to cover every
  empty-handed exit, sorting what happened into the two buckets that read
  differently: `3 attempts failed (ConnectError)` is a broken network,
  `3 sources had nothing` is an ordinary paywalled paper, and
  `TypeError`/`AttributeError` among the failures is a bug. `FullTextResult`
  is unchanged, and a successful retrieval emits no exhaustion warning.

- **An unreachable source no longer counts as an absence** (#67). Two things
  made a broken chain look like a paywalled one even with the report above.
  Both resolvers signalled an HTTP failure by *returning* what an empty result
  set returns, so a Europe PMC or NCBI outage was counted as nothing having
  happened. Both now raise, but only one raises to its caller: the search
  resolver's `FullTextError` reaches the callers that already caught it, and
  they record the fault, while the ID converter's is caught by its own handler,
  which records the fault and still returns `None` — a caller that already
  holds a free-PDF URL by that point must not be made to pay for the converter
  being down. And `FullTextError` was raised alike for `Unpaywall HTTP 503`
  and `DOI not found in Unpaywall`, so an outage and a paper nobody serves for
  free produced byte-identical summaries. The absences now raise
  `FullTextUnavailableError`, a subclass, so nothing that catches
  `FullTextError` is affected; it is an internal signal and never escapes
  `fetch_fulltext()`. An article 404 is an absence from
  *every* source — Europe PMC, NCBI, Unpaywall and a fetcher-supplied URL
  alike, where all four used to raise the same `FullTextError` a 5xx did —
  since a stored source URL going stale is ordinary, and counting it as broken
  inflates the one bucket the summary asks the operator to act on. A 404 from
  a *search* endpoint stays a fault: Europe PMC answers "no such paper" with
  HTTP 200 and an empty list, so a 404 there means the API path is wrong.

- **A call whose identifiers all failed is no longer told it gave none**
  (#67). With a `pmc_id` or `fulltext_sources` but no `doi`/`pmid`, an
  exhausted chain raised `FullTextError("No identifiers provided")` and
  skipped the summary entirely — the same misdirection as the bug above, on
  the one path with no result to return. It now reports the failures and says
  what was actually missing.

- **A cache that cannot be written to says so, once** (#67, same file).
  `_cache_html` swallowed every exception at `DEBUG`, so a read-only cache
  directory or a full disk meant the whole corpus was silently re-fetched over
  the network on every run, permanently. The first failed write now emits a
  `WARNING` naming what was raised; later ones stay at `DEBUG`, since the
  cause is a property of the directory rather than of the article — the
  one-shot pattern the missing-`bmlib[pdf]` warning already used. HTML and PDF
  writes share that one warning: the PDF write was folded into the download's
  own handler, so it was reported as "PDF download failed" and never reached
  the warning at all — leaving a corpus served mostly by Unpaywall, which
  never writes HTML, completely silent.

- **A truncated cache file is no longer written** (#70, found reviewing #67's
  fix). `save_html` and `save_pdf` wrote with a bare
  `write_text`/`write_bytes` and `get_html` read back with no validation, so a
  disk that filled mid-write — one of the two causes the warning above names —
  left a truncated file that decodes perfectly and was then returned as
  `content_kind="fulltext"` from `source="cached"` on every later run, with no
  log at any level: `quality/` would score a paper whose Methods and Results
  do not exist. Strictly worse than #67, which lost data in a shape resembling
  absence. Both writes now go to a uniquely-named temporary file beside the
  target and are published with `os.replace`, so a failed write leaves the
  previous entry or nothing. The headline says *written* deliberately: this
  closes the window rather than detecting an entry already truncated on disk,
  and a real truncation of English-language prose usually lands on an ASCII
  boundary and decodes fine, so `clear()` is the remedy for a cache an older
  version wrote. Several details are load-bearing, and each has a named test
  except `O_BINARY`, which no run off Windows can observe:
  the `fsync` before the replace is not durability theatre — under delayed
  allocation the `write(2)` that `flush()` issues returns success and ENOSPC
  reaches userspace only at `fsync`, so without it `os.replace` would publish
  a file whose blocks were never written; the temporary name carries a UUID,
  because the loser of a race between two processes would otherwise unlink the
  winner's in-flight temp file and leave neither having cached anything;
  `O_BINARY` is added where the platform has it, since a descriptor `os.open`
  leaves in the CRT's default text mode would rewrite every LF in a cached PDF
  as CRLF on Windows; the mode is 0666 filtered by the umask — exactly what
  `write_bytes` requests, and neither `tempfile.mkstemp`'s 0600 nor 0644,
  both of which silently break a cache directory shared between users; and the
  cleanup's own `unlink` is guarded so it cannot replace the exception it is
  tidying up after. `sanitize_identifier` now truncates its readable prefix,
  since the temporary name is 38 characters longer than the entry's and a long
  identifier would otherwise fail a write that used to succeed. `save_html`
  and `save_pdf` now raise `OSError` where they previously wrote a partial
  file — both call sites in `FullTextService` already report a failed cache
  write, so a retrieval is unaffected, and both docstrings carry a `Raises:`
  section for direct callers.

- **A corrupt cache entry no longer aborts the run** (#71, same review).
  `_check_cache` was called unguarded and `get_html` does a bare `read_text`,
  so an entry truncated mid-multibyte-sequence raised `UnicodeDecodeError`
  straight out of `fetch_fulltext()`: it broke the documented
  `FullTextError`-only contract, contradicted #67's own new bullet that a bad
  cache does not fail a retrieval, and was a hard stop where re-fetching over
  the network was available — one bad file made a paper permanently
  unfetchable and took a bulk sync down mid-corpus. A cache *read* is now
  best-effort exactly as a cache write is. The guard is deliberately broad: a
  decode failure is only the shape it was reported in, and a file the process
  cannot read raises `OSError` instead, so narrowing it to `UnicodeDecodeError`
  restores the bug — pinned by its own test after mutation testing found the
  first cut survived that narrowing. It reports the exception type as well as
  its message, so a bmlib bug does not read as an ordinary bad file. Warned
  per article rather than once per service, unlike the write warning above: an
  unwritable directory is a property of the directory, an unreadable file is a
  property of that file. The unreadable entry is not deleted but **moved aside**
  to a `.corrupt` name by the new `FullTextCache.quarantine()`: leaving it in
  place healed only when the re-fetch happened to return JATS full text, since
  an article served as a PDF never rewrites the HTML entry and the undecodable
  entry is read first — so it hid the freshly cached PDF behind it and the
  article warned and re-downloaded on every run, forever. `delete()` and
  `clear()` now also remove an entry that is not a regular file, which is the
  corrupt shape an operator is most likely to meet and the one both of them
  previously failed on.

- **A cache directory that cannot be created no longer aborts construction**
  (#75, found reviewing PR #74). `FullTextCache.__init__`'s three `mkdir`
  calls were unguarded and ran inside `FullTextService.__init__` whenever no
  cache was passed, so a file standing where the cache directory should be —
  or a read-only parent, or a full disk — took down a run that had every
  chance of succeeding without a cache. It was the last place *`FullTextService`
  touches the cache* that was not best-effort: a failed write already warned
  once (#67) and a failed read already fell through to the network (#71). The
  default construction now warns once, naming what was raised, and leaves
  `service.cache` as `None`; retrieval proceeds and caches nothing. A
  `FullTextCache` constructed *directly* still raises — that caller asked for
  a cache specifically, and degrading would return an object whose every
  method then failed one at a time instead of failing once at construction.
  The scoping in that first sentence is meant literally: `FullTextCache`'s own
  methods are unchanged and still raise to a direct caller, so "the cache is
  best-effort" is true of the service, not of the class.
  The warning says what the degraded run costs rather than only that it is
  degraded — a PDF is fetched *into* the cache, so with no cache there is no
  download at all and a PDF-only article comes back as a bare URL. That is
  lost content, not merely repeated traffic, and an operator told only that
  "nothing will be cached" would go looking for a network fault. It names
  `cache=FullTextCache(cache_dir=...)` as the remedy, as the missing-`bmlib[pdf]`
  warning already names its extra.
  The guard catches `RuntimeError` as well as `OSError`, because
  `_default_cache_dir()` runs before any `mkdir` and calls `Path.home()`,
  which raises the former where there is no `HOME` and no passwd entry — an
  ordinary distroless container — so `except OSError` would have fixed the
  reported shape and left the same defect one layer up. It is not
  `except Exception`: inside that one constructor `RuntimeError` has exactly
  one *source*, so the guard stays narrow enough that a bmlib bug still
  surfaces as one — pinned by a test that raises a `ValueError` from the
  constructor and demands it escape, since widening a guard catches strictly
  more and no test that merely uses the cache could fail on it.
  `FullTextService.__init__`'s `Raises:` section, which documented only
  `ImportError`, becomes accurate rather than needing a new entry. One log
  line changes: `_download_and_cache_pdf`'s `self.cache` check was dead code —
  `FullTextCache` is always truthy and `self.cache` could not be `None` — and
  reaching it now would have printed "no identifier was given" when an
  identifier had been given, so the two conditions are split. The no-cache one
  logs at `DEBUG`, the construction warning having already named that exact
  consequence, and unlike its sibling it is *not* gated on `convert_pdfs`: the
  download is skipped either way, so `file_path` is lost even for the caller
  who turned extraction off precisely because they wanted the file.

- **`bmlib.fulltext` imports on a core install** (#64). `fulltext/__init__.py`
  eagerly re-exported `service`, whose top-level `import httpx` was the last
  unguarded optional import in bmlib — and since importing a submodule imports
  its parent package first, it gated everything beside it. Measured against the
  published 0.8.0 wheel in a venv holding only `bmlib`, `jinja2` and
  `markupsafe`, **one fresh interpreter per module**: **ten** modules across two
  packages raised a bare `ModuleNotFoundError`. All seven of `bmlib.fulltext.*`,
  including the pure-dataclass `models` and the stdlib-only `SectionSegmenter`
  — one of 0.8.0's headline additions, documented as standalone and making no
  HTTP request — plus `publications.fetchers.{pubmed,biorxiv,openalex}`, which
  borrow one dataclass from `models` and take an injected HTTP client rather
  than importing httpx themselves. `FullTextService` and `FullTextError` now
  resolve through a PEP 562 `__getattr__`, as `bmlib.context_processor` already
  does for its LLM-backed half; the public API and `__all__` are unchanged, and
  the same probe now reports **69 importable, 0 not**. What deferring adds over
  the guarded import below — which restores importability by itself, as
  mutation testing showed — is that `import bmlib.fulltext` does not load
  `service` at all, so no future top-level import in that module can gate the
  parser, the models or the segmenter again.

- **Constructing `FullTextService` without httpx names the extra.** The import
  moved out of the module top level into `_require_httpx()`, called first thing
  in `__init__` so the failure lands at construction rather than on the first
  request, and again in `_http_get` where the client is actually built. The
  module is deliberately **not** stored on the instance: a module object cannot
  be pickled, so holding one would have broken handing a configured service to
  a process pool, and reading it back as instance state would let anything that
  reached `_http_get` without running `__init__` fail with an `AttributeError`
  that the tier chain swallows at DEBUG. The check is the first statement, so a
  failed construction leaves no cache directory behind.

- **A broken httpx is no longer reported as an absent one.** `except
  ImportError` also catches the `ModuleNotFoundError` a *present* httpx raises
  for its own missing dependency, so the message now reports what was actually
  raised — `httpx is required for full-text retrieval, but importing it failed
  (…). Install with: pip install bmlib[fulltext]`. Asserting the cause instead
  prescribed a `pip install` that answers "Requirement already satisfied" and
  changes nothing, leaving the reader to run it, see success, retry and hit the
  identical error. This is the reasoning `_attach_pdf_text` already spells out
  for the analogous PyMuPDF case.

- **`dir()` on `bmlib.fulltext` and `bmlib.context_processor` no longer hides
  the submodules.** Both `__dir__` implementations returned `__all__` alone,
  which added the two deferred names while dropping `cache`, `models`,
  `segmenter` and every dunder — breaking REPL completion for
  `bmlib.fulltext.models` and shrinking `inspect.getmembers()`. They now return
  the union. Resolved lazy names are also bound into `globals()`, as PEP 562
  recommends, so repeat access skips `__getattr__` entirely.

## [0.8.0] — 2026-08-08

Phase 2 of the bmlibrarian port, complete — four ports in one release. A new
pure-stdlib `bmlib.citations` numbers and formats reference lists in four
styles; `SectionSegmenter` turns a PDF's text lines into typed sections;
`CochraneAssessor` becomes the quality pipeline's Tier 4, condensing an
oversized paper to an evidence digest rather than truncating it; and the
PubMed fetcher grafts on `<GrantList>` and `<AffiliationInfo>` as child rows
while ending the silent truncation of every title that carried markup.

Everything is additive, so a minor bump. But **three of the four move stored
values**, and they compound: the PubMed change alters every synced title and
abstract, a Cochrane-enriched assessment reports different bias domains, and
a PDF that previously "converted" to empty text is now a failure. Anything
persisting these should re-sync or accept a mix; each entry below says what
moved.

### Added

- **`bmlib.citations`** — citation-marker parsing, four citation styles, and
  reference-list building, ported from bmlibrarian's `writing` package
  (Phase 2 row 4 of the porting analysis). `parse_citations()` and friends
  read the `[@id:12345:Smith2023]` marker format as pure functions;
  `CitationFormatter` renders references and inline citations in Vancouver,
  APA, Harvard, or Chicago style; `build_references()` /
  `format_document()` number citations by order of first appearance,
  combine adjacent markers (`[1-3]`), and append a markdown reference list,
  with document metadata injected by the caller as
  `Mapping[int, DocumentMetadata]` instead of fetched from a database. Five
  upstream defects fixed, each with a named regression test: a
  semicolon-separated author string of inverted names was shattered into
  fragments (`"Smith, John; Doe, Jane"` became four authors); marker
  validation anchored only the start, so trailing junk validated;
  author–date styles (APA/Harvard/Chicago) received numeric `[N]` inline
  citations against an unnumbered reference list; APA/Chicago author
  blocks doubled the terminal period (`"Williams, B.."`); and a
  whitespace-only author entry crashed every style's reference formatting
  with an `IndexError` (blank entries are now dropped). The app-editor
  pieces (`document_store`, `WritingDocument`, autosave/editor constants)
  were deliberately not ported.
- **PDF section segmenter** (`bmlib.fulltext.SectionSegmenter`) — Phase 2
  row 8 of the bmlibrarian port. `segment_document()` turns a PDF's text
  lines into a `SegmentedDocument` of typed, titled `Section`s, located by
  heading detection (font size against the document's median, bold as the
  rescue for body-sized headings) and an anchored pattern table covering
  every producible `SectionType`. Three content-losing upstream defects are
  fixed, each with a named regression test: everything before the first
  detected heading was silently dropped (now a `FRONT_MATTER` section at
  0.5 confidence); a heading with no body vanished along with its heading
  text (now reported with empty content); and the partial-match fallback
  compared regex *source* against the heading as literal text, which killed
  every multi-word pattern and classified a heading "A" as ABSTRACT (now an
  unanchored, word-bounded search of the same compiled pattern, at 0.7).
  Enum members no pattern could produce were not ported from upstream
  (`MATERIALS_AND_METHODS`, `CONCLUSIONS` — duplicates of the members that
  own their patterns), or were given patterns instead (`APPENDIX`); `TITLE`
  stays, reserved for callers. "Financial disclosure(s)" classifies as
  `CONFLICTS` in both numbers — the singular once sat in `FUNDING`'s list
  too, so the two numbers landed in different sections, decided by dict
  order. `TextBlock`, `Section` and `SegmentedDocument` carry
  `to_dict()`/`from_dict()` for JSON-safe persistence of a segmentation.
- **`PyMuPDFConverter.extract_blocks()`** and the `LayoutExtractor`
  protocol (`bmlib.fulltext`) — one `TextBlock` per text *line*, not per
  span. PyMuPDF starts a new span at every font change, so upstream's
  span-level extraction shattered a mixed-font heading ("2." + "Materials
  and Methods") into fragments no anchored pattern could match, and split
  sentences at every italic word. Font attributes come from the line's
  dominant span, so a superscript marker cannot restyle a line. Declared as
  a protocol rather than on the `PDFConverter` ABC so a backend that cannot
  report line geometry is not forced to fake it. Raises on a corrupt file
  rather than returning a partial list — unlike `convert()`, whose partial
  text is useful, a partial block list is indistinguishable from a sparse
  PDF.
- **Cochrane assessment agent** (`bmlib.quality.CochraneAssessor`) — Phase 2
  row 9 of the bmlibrarian port, and the producer `cochrane_models.py` has
  been waiting for since 0.4.0. `assess()` turns a title and text into a
  `CochraneStudyAssessment`: the Cochrane Handbook's five-section
  study-characteristics table plus a judgement and supporting text for each of
  the nine Risk-of-Bias domains. Text larger than the configured context is
  first reduced to an evidence digest by `bmlib.context_processor`, so the
  nine-domain judgement is always made once, over content that fits —
  enforced by measuring the digest itself rather than trusting
  `ProcessingStatus` to imply it, since a `TRUNCATED` run names the harness's
  recursion ceiling, not the size of what it produced. `condensed_from_chars`
  says when condensation happened and `condensation_status` says how it
  finished (`"completed"`, `"partial"`, `"truncated"`), because a judgement
  made over a digest — especially an incomplete one — is weaker evidence than
  one made over the paper. Truncating instead was rejected: allocation
  concealment and blinding live in Methods and attrition in Results, so a
  head-of-string cut drops exactly the evidence the domains rest on. Failure
  returns `None`, not an all-"Unclear risk" stand-in that would be
  indistinguishable from a real assessment.
- **`collapse_risk_of_bias()`** — the nine Cochrane domains reduced to the
  five `BiasRisk` domains, closing the `BiasRisk` ↔ `CochraneRiskOfBias` gap.
  The grouping is derived from each item's own `bias_type` rather than written
  out per domain; where several collapse onto one field the worst wins, with
  `unclear` outranking `low` because an unreported domain is not a clean bill
  of health. An unrecognised `bias_type` raises rather than returning a
  `BiasRisk` that looks complete.
- **`QualityFilter(use_cochrane_assessment=True)`** and a `full_text=` keyword
  on `QualityManager.assess()`. The Cochrane pass *enriches* a classification
  rather than replacing it — the classification supplies the study design,
  quality tier/score and confidence a Cochrane assessment does not produce,
  the Cochrane pass supplies the bias detail no classification tier can see —
  and attaches the full assessment to the new
  `QualityAssessment.cochrane_assessment`. Which classification depends on
  Tier 1: a confident metadata result is the base and Tier 2 is skipped, but
  an inconclusive one is not, because enriching it would return
  `study_design=UNKNOWN` at score 0.0 and confidence 0.0 with a full
  nine-domain bias table attached — worse than the Tier 2 answer the caller
  had enabled. So when Tier 1 is inconclusive and `use_llm_classification` is
  set (the default), the cheap classifier runs first and its result is the
  base. That is the common path for preprints, which carry no PubMed
  publication types at all. Neither `evidence_level` nor `confidence` is
  copied across: both are foreign vocabularies (Cochrane's `evidence_level`
  is free-form model text against the classification's Oxford CEBM, and
  `overall_confidence` describes the model's certainty about blinding and
  allocation concealment, not about the `study_design` the classification
  already supplied); both stay reachable on the attached object. A successful
  pass supersedes Tier 3 when both are requested; a *failed* pass falls
  through to Tier 3 and then Tier 2 exactly as if the flag had not been set,
  rather than returning the Tier 1 result outright — "supersedes" means "runs
  instead of, when it works", not "suppresses even on failure". With neither
  Tier 3 nor Tier 2 requested a failed pass still ends at the Tier 1 result,
  unchanged. Additive: `assessment_tier=4` is new, the flag is off by
  default, and no stored value moves.

  Six upstream defects were fixed in the port, each with a named regression
  test: `min_confidence` was accepted and never read; `success_rate` could
  only ever report 1.0, because the attempt total was incremented on the
  success path alone; judgement strings bypassed
  `RiskOfBiasJudgement.from_string()`, so a model answering `"low"` rather
  than `"Low risk"` stored an invalid value that `get_summary_counts()` then
  skipped, silently reporting eight domains of nine; `overall_confidence` was
  unclamped, so a model reporting 1.4 outranked every honest result; a reply
  carrying no `risk_of_bias` section at all was accepted and turned into nine
  fabricated defaults; and the study label was derived by
  `first_author.split()[-1]`, which reads "van der Berg" as "Berg".
- **PubMed grants and author affiliations** (`bmlib.publications.Grant`,
  `AuthorAffiliation`) — Phase 2 row 11 of the bmlibrarian port, and the last
  Phase 2 row. The PubMed fetcher now reads `<GrantList>` awards and
  `<AffiliationInfo>` affiliations, and both are persisted: two new tables,
  `publication_grants` and `publication_affiliations`, created by
  `ensure_schema()` on both backends, read back by the new `get_grants()` and
  `get_author_affiliations()`. They are child rows of a publication, following
  the `FullTextSource` precedent, so `Publication` and its `to_dict()`
  contract are unchanged; the new `FetchedRecord.grants` and
  `FetchedRecord.author_affiliations` are declared last, for positional
  stability. Affiliations are stored one row per *(author, affiliation)* pair
  rather than upstream's nested grouping — the relational shape, which makes
  "which papers have an author at this institution?" a join rather than a scan
  through nested JSON (only `publication_id` is indexed; an index suiting a
  search *by* institution is the consumer's to add) — and
  carry the author's `position` in the `<AuthorList>`, because first-author
  and senior-author affiliation are the conflict-of-interest signals and the
  name alone cannot recover the ordering.

  Both tables carry a `source` column, and storage is **replace-per-source**:
  a record's rows replace the stored rows for the source that asserted them
  and leave every other source's alone, so re-syncing PubMed cannot disturb
  what OpenAlex found. That gives idempotent re-syncs and self-correcting
  updates while letting two sources coexist — scoping by publication alone
  made the stored set depend on whichever source synced last, flip-flopping on
  every sync with no error and no warning, which matters because OpenAlex's
  API does carry funder data. `sync()` stamps the column from the record's own
  source rather than each fetcher setting it, so a new fetcher cannot forget.
  A record carrying no rows at all still leaves everything alone: an absent
  `<GrantList>` means the record did not carry the data, not that the funding
  was withdrawn. A row naming *no* source raises `ValueError` rather than
  being stored, because scoping is the whole mechanism: an unnamed row is
  unreachable, so no later sync can replace it and each one stacks a
  correctly-labelled duplicate beside it. The check is in the storage layer
  rather than left to the `NOT NULL` column because the column rejects `None`
  while `""` — the dataclass default, and so the value a forgetful caller
  actually produces — was stored happily.

  There is no UNIQUE constraint on the natural key, deliberately — every
  column of a grant proper is nullable and both backends treat NULL as
  *distinct* in a unique index, so it would protect nothing while appearing
  to. Nothing is left for one to catch: exact repeats are collapsed at parse
  time, since PubMed emits a `<Grant>` block verbatim twice often enough to
  matter (31 of 575 entries across 200 NIH-funded records, affecting 14 of
  them), and stored separately they inflate every count of a paper's funders.

  Two upstream defects fixed, each with a named regression test: a grant
  naming neither an agency nor an award id was stored as a row identifying no
  award, and affiliations named their author `"Smith John"` while the author
  list said `"Smith, John A"`, so joining the two was guesswork — one pass
  over `<AuthorList>` now formats both. Which elements are read with the
  formatting walker below is decided by NLM's DTD rather than by eye:
  `<Affiliation>` is declared with the same `(%text;)*` content model as
  `<ArticleTitle>`, so it gets the walker too — a trailing superscript
  footnote marker would otherwise truncate the institution, and a *leading*
  one would drop the affiliation row entirely. Upstream's `is_retracted` was
  deliberately not ported: `publication_types` already carries "Retracted
  Publication" verbatim, `bmlib.publications.retractions` answers the question
  authoritatively, and upstream treats RefType `RetractionOf` as retracted
  when it marks an article as *being* the retraction notice.

### Changed

- **PubMed titles and abstracts preserve inline markup, and abstracts are
  Markdown.** `_text()` read `el.text`, which is the text *before the first
  child element*, so any PubMed title carrying markup was truncated there and
  the loss was silent: `"Effects of H<sub>2</sub>O and <i>E. coli</i> on
  outcomes"` parsed as `"Effects of H"`. Titles drive dedup display, quality
  assessment and citation building, and chemical formulas and italicised
  species names are ordinary in PubMed titles. Titles and abstracts are now
  read with a mixed-content walker that maps `<b>`/`<i>`/`<sup>`/`<sub>`
  to Markdown, and each `AbstractText` becomes a `**LABEL:** text` section
  separated by a blank line, with the label taken from `Label` *or*
  `NlmCategory` — reading only `Label` dropped the heading from every section
  labelled the other way, running it into its neighbour.

  Prose taken from the document is escaped (`` \ ` * ~ ^ ``), so a field
  *declared* Markdown cannot be re-read as markup it never carried. Without
  this the change would corrupt values that were fine before: `CYP2C19 (*1,
  *2, *3, *17 alleles)`, the standard star-allele notation, renders as
  `(<em>1, </em>2, …)`, and the `~` of "AUC ~ 0.80" pairs with the next one to
  subscript half a sentence — a hazard the `~x~` mapping itself created. The
  escape set is measured against 3,403 real titles and abstract sections: it
  alters 0.35% of them and removes every construct a CommonMark parser found,
  while also escaping `_` and `[`/`]` churned 4.3% and fixed nothing further
  (intraword `_` is inert in CommonMark, and a bare `[…]` is not a link).

  `<u>`/`<underline>` is **not** mapped, and passes through undecorated.
  Markdown has no underline — `__x__` is *strong* emphasis, so mapping `<u>`
  to it renders underlined text identically to `<b>` while asserting the
  source said "bold", which is exactly the ambiguity `<sub>`/`<sup>` earned
  their Pandoc markers to avoid. Underline is presentational, unlike a
  subscript, so dropping it loses nothing a reader needs.

  A second upstream defect fixed on the way: upstream stripped whitespace at
  every recursion level, so the space inside `<b>Randomised </b><b>trial</b>`
  vanished and the runs welded into `**Randomised****trial**`, which is broken
  Markdown rather than merely ugly text. Leaving the space where it sits is no
  better — CommonMark requires an emphasis delimiter to be adjacent to
  non-whitespace — so a run's edge whitespace is re-emitted *outside* its
  markers, giving `**Randomised** **trial**`.

  **Not comparable with previously stored values.** Every synced PubMed title
  and abstract changes shape: titles because they were being truncated,
  abstracts because they gain recovered `NlmCategory` labels, blank-line
  section breaks, and `CO~2~` / `m^2^` where the old flattening produced an
  ambiguous `CO2` / `m2`. Anything persisting abstracts should re-sync or
  accept the mix.

### Fixed

- **A password-protected PDF is a failed conversion, not an empty successful
  one** (#57). `PyMuPDFConverter.convert()` returned `success=True` with
  `text=""`, `converted_pages=0` and only warnings to show for it: PyMuPDF
  opens an encrypted document without its password and fails only on *use*,
  so metadata extraction and every page's `get_text()` failed inside the
  handlers that exist to stop one bad page aborting the rest. A caller
  testing `success` alone therefore read an unreadable file as a paper that
  happens to contain no text — and the two need different responses, since
  one is worth retrying from another source and the other is not.
  `convert()` now checks `doc.needs_pass` immediately after opening and
  returns `success=False` with `error_message="PDF is password-protected"`.
  `extract_blocks()` gets the same explicit check: it already raised on such
  a file, but only because `get_text()` failed of its own accord, under
  PyMuPDF's message naming two causes at once ("document closed or
  encrypted") — and had that call ever stopped raising it would have
  returned `[]`, which is precisely what a legitimate image-only scan
  returns. The test is `needs_pass`, not `is_encrypted`: an *owner* password
  restricts permissions without blocking reads, so such a file is encrypted
  and converts perfectly. Four regression tests, each guard paired with that
  owner-password negative control.

## [0.7.0] — 2026-08-04

Two new capabilities and two widened ones. `bmlib.publications` can answer
"is this paper retracted?"; the new `bmlib.context_processor` works through
more content than one context window holds; `bmlib.fulltext` reaches PMC
through a second resolver and reads NCBI's own copy; and the transparency
analyzer credits data deposition that PubMed reports in a structured field
rather than only what a paper's prose happens to say.

No public signature changed incompatibly and nothing was removed, so the bump
is minor. **Four changes move stored values**, none of them behind a flag:

- `transparency_score` rises and `data_availability_level` strengthens — the
  two sources are merged by rank, so it can only move up — for papers whose
  PubMed record names a deposition repository.
- `trial_registered` becomes `True` and `transparency_score` rises by 20 for
  papers registered in `JMACCT`, `REPEC` or `UMIN CTR`, which
  `_TRIAL_REGISTRY_NAMES` did not recognise.
- `risk_indicators` collapses a funder CrossRef names repeatedly to a single
  line; no score and no risk level moves with it.
- `FullTextResult.source` gains `"ncbi_pmc"`, and papers that previously fell
  through to a bare DOI link can now return real full text.

Each entry below says exactly who is affected. Retraction Watch and
`context_processor` are purely additive — a new module each, nothing existing
changed.

### Added

- **Retraction Watch notices: answer "is this paper retracted?"** Ported from
  bmlibrarian (Phase 2 row 10 of the porting analysis). A biomedical
  literature tool must not present a retracted paper as evidence, and bmlib
  had no way to tell. `parse_retraction_watch_csv()` streams the
  Crossref-distributed export (65 MB, 71,306 rows) into `RetractionNotice`
  records; `store_retraction_notices()` upserts them on Retraction Watch's own
  `record_id`, so re-importing the monthly file updates rather than
  duplicates; `lookup_retractions()` returns every notice about one paper,
  newest first, and the pure `is_retracted()` reduces them to a boolean.

  Purely additive — a new table and a new module, nothing existing changed, so
  no stored value moves.

  This is deliberately **not** a registered source fetcher. Fetchers are a
  date-keyed feed protocol producing publications; a retraction notice
  annotates a paper that is usually not in the caller's `publications` table
  at all.

  A row describes **two** papers, so both identifier pairs are kept under
  names that say which is which: `doi`/`pmid` are always the retracted paper,
  `notice_doi`/`notice_pmid` the notice.

  Five defects in the upstream implementation are fixed, each pinned by a
  regression test named for it:

  1. **The PMID match path was dead.** Its candidate column tuple contained
     none of the export's real names (`OriginalPaperPubMedID`,
     `RetractionPubMedID`), so every row matched `None`.
  2. **A failed encoding attempt duplicated every row already read.** The row
     accumulator was created outside the encoding retry loop and never
     cleared, so `utf-8` failing part-way through left those rows in place and
     the next encoding appended the whole file again. The port scans the
     whole file through an incremental decoder before committing to an
     encoding, then streams it once with that choice — so a decode failure
     is caught before the first row is ever yielded, and a partially-read
     accumulator can no longer exist to be duplicated.
  3. **A byte-order mark hid the first column.** `utf-8` was tried before
     `utf-8-sig`; on a BOM'd file it succeeds and glues the BOM to the first
     field name, so `Record ID` became unfindable.
  4. **Every row was stored as retracted** — including Corrections,
     Expressions of Concern, and Reinstatements, which are the opposite.
  5. **Missing identifiers are truthy sentinels.** The export writes `0` for
     an absent PubMed ID (46.04% of rows) and `Unavailable`/`unavailable` for
     an absent DOI, none of them falsy, so a truthiness test accepts them and
     collapses tens of thousands of unrelated notices onto a single fake key.

  The retraction rule is deliberately not "latest notice wins": scanning
  newest-first, only a Retraction or a Reinstatement decides, because a
  correction does not undo a retraction. 52 papers in the live export are
  retracted while carrying a later Correction or Expression of Concern.

  Every way this feature can degrade rather than fail is reported, because
  each one degrades into an import that looks successful:

  - `lookup_retractions()` rejects the same sentinels the parser does, so
    `pmid="0"` or `doi="Unavailable"` raises rather than returning `[]` — a
    caller whose own PMID column stores `"0"` for "absent" would otherwise
    read a paper it knows nothing about as not retracted.
  - Falling back off `utf-8-sig` to `cp1252` or `latin-1` logs at `WARNING`.
    Neither fallback can fail, so one corrupt byte would otherwise re-read
    the whole export under an encoding that mis-renders every non-ASCII
    character in 66,000 rows, in silence.
  - A `RetractionNature` value this version cannot map logs at `WARNING`,
    once per distinct value. `is_retracted()` reads `OTHER` as evidence of
    nothing, so a reworded `"Retraction"` upstream would answer "not
    retracted" for every paper in the file.
  - A malformed CSV raises `ValueError` naming the last line read whole,
    rather than a bare `csv.Error` reading as a bmlib bug.
  - A stream that is text rather than binary, or not seekable, raises at the
    call rather than at the first iteration — which for the documented usage
    means at the caller's mistake rather than from inside
    `store_retraction_notices()`'s open transaction.

- **`context_processor`: process more content than one context window holds.**
  Ported from bmlibrarian (Phase 1 item 2, issue #49). Hierarchical
  map-reduce: batch the items to fit, extract from each batch, then feed the
  extractions back in as items and repeat until what remains fits in a single
  context. The alternative — truncating — loses information silently and
  leaves no way to tell an answer drawn from everything apart from one drawn
  from the first 4,000 characters.

  `IterativeContextProcessor` is the harness and has **no LLM dependency**: it
  is batching, recursion, consolidation, progress and failure accounting over
  caller-supplied items. Subclasses supply `format_item()` and
  `extract_from_batch()`. `LLMChunkProcessor` is a ready-made subclass that
  runs every model call through a `BaseAgent`, so token accounting, retries
  and JSON repair are the ones the rest of bmlib uses; it accepts plain
  strings or `(text, score)` tuples, the shape a semantic search returns.
  Upstream's equivalent called the raw Ollama client directly and was
  rewritten rather than copied. `create_prisma_chunk_processor` was
  deliberately not ported: PRISMA 2020 is an application concept.

  `bmlib.llm.text_utils.process_with_map_reduce()` is the shallow case of this
  — one map, one reduce, over one string — and stays. The processor uses
  `TextChunker` from that module when it has to split an oversized item, so
  pieces break on paragraph and sentence boundaries instead of mid-word.

  Four defects in the upstream implementation are fixed in the port, each
  pinned by a regression test named for it:

  1. **The bin-packing ran twice per level.** `process()` re-ran the whole
     packing purely to record the batch count in its statistics —
     re-formatting every item, re-splitting every oversized one, and
     re-emitting every skip and split log line, so the logs claimed twice the
     skips that happened. `_process_level()` now returns the count it has.
  2. **Split pieces were measured before formatting.** Pieces were cut to
     `max_chars` of *raw* content but measured after `format_item()` added its
     decoration, so a piece cut to exactly the limit exceeded it — breaking
     the one guarantee `max_context_chars` makes. The overflow is now measured
     and the budget reduced by exactly that much, then verified.
  3. **`OversizedItemStrategy.TRUNCATE` double-decorated.** It truncated the
     *formatted* item and returned it as an ordinary item, which the batcher
     then decorated again — over the limit once more, by the width of the
     second decoration.
  4. **Boundary items were measured at the wrong index.** The item that
     *starts* a new batch was measured with the outgoing batch's index, so
     `total_chars` under-counted wherever `format_item()` renders the index.
     Items are now measured at the position they land in, and `total_chars`
     equals the length of the content the extractor receives.

  Two upstream shapes were changed rather than carried over.
  `estimate_item_size()` is not ported — the batcher must call `format_item()`
  on every item anyway, so the estimate saved nothing while letting the
  oversized decision and the packing measurement disagree, which is how an
  underestimated item was never split and silently overflowed its batch. And
  the recursion now wraps results in a `ConsolidatedItem` instead of an
  anonymous `(content, metadata)` tuple, which makes upstream's
  `format_consolidated_item()` live code: it was defined and never called, so
  every subclass had to sniff tuple shapes inside `format_item()` to tell a
  consolidated result from one of its own items.

  `ProcessingConfig` is frozen, and rejects an `overlap_chars` above half of
  `max_context_chars`. The stride of a split is the difference between them,
  and the piece count grows without bound as it shrinks: one below the
  window, a split advances a character at a time, so a megabyte-long item
  becomes a million batches and a million model calls with nothing to warn
  the caller. Half is the largest overlap keeping the piece count within
  twice its minimum.

  Review of the port closed a further set of defects, each with a regression
  test verified by reverting the fix:

  - **`ProgressInfo.progress_percent` could never be anything but 0.0.**
    Nothing ever set `current_item`. `_process_level()` now counts items off
    as their batch completes — and counts an item dropped by the oversized
    strategy the moment the batcher drops it, since no extraction will reach
    it and a bar waiting for one would never fill.
  - **A query containing the literal `{content}` had the batch spliced into
    it.** Prompt rendering chained two `str.replace` calls, so the second ran
    over what the first substituted — doubling a prompt sized to fit exactly,
    which is the overflow the module exists to prevent. Substitution is now
    a single pass.
  - **A run that lost every item reported "All batches failed" and a
    `success_rate` of 1.0.** With every item dropped as oversized, no batch
    was ever built: the message named a failure that had not happened, and
    the ratio read as a clean run. The message now names both counts, and
    `success_rate` answers 0.0 when a batch-less run lost something and 1.0
    only when there was nothing to lose.
  - **The strict `FAIL` strategy was reported as an unexpected error**, with
    a full traceback, though it is the configuration doing exactly what it
    was asked. It raises `OversizedItemError` — still a `ValueError`, as
    documented — which `process()` reports plainly.
  - **`CONCATENATE` and `WEIGHTED` disagreed about the same results.** The
    former averaged only confidences above zero, so a batch the extractor had
    no confidence in *raised* the merged confidence. Every valid result now
    counts under both.
  - **`process()` kept its statistics on the instance**, so two concurrent
    runs on one processor interleaved and each could return the other's
    counts. They are a local.
  - `batch_metadata["item_indices"]` and the result's `source_indices` were
    both the `Batch`'s own list, and merging a lone result copied it
    shallowly. Nothing handed to a caller now shares a list with anything
    else.
  - Importing the package eagerly re-exported `LLMChunkProcessor`, and with
    it `BaseAgent`, `bmlib.templates` and jinja2 — over half the import cost
    for callers wanting only the LLM-free harness. A :pep:`562` `__getattr__`
    defers it, making the "no LLM dependency" claim true of the package and
    not merely of `base.py`.
  - `("text", True)` rendered as `score 1.00`, `bool` being an `int`. A
    boolean is no longer taken for a relevance score.

- **`fulltext`: a second source for PMC ID resolution, and NCBI as a full-text
  tier.** `FullTextService` could reach a PMC ID exactly one way — Europe PMC's
  search, gated on `inEPMC == "Y"`, which requires Europe PMC *both* to have
  indexed the paper and to hold its full text. A paper in PMC failing either
  condition skipped Tiers 1a/1b and fell through to Unpaywall or a bare DOI
  link. Two changes close that:

  `_resolve_pmc_id_via_idconv()` asks NCBI's ID Converter — the authoritative
  DOI/PMID→PMCID mapping, which depends on neither condition — but only when
  the Europe PMC search reported no PMC ID or could not be reached at all.
  Second, never first: that one search also returns the free-PDF URL the
  render tier needs, so asking the converter first would cost a request on
  every lookup or forfeit that URL. But it is consulted even when that search
  raised — a search that failed is when a second, independent resolver is
  worth most. It is asked by PMID when there is one, DOI otherwise, and never
  raises.

  `_fetch_ncbi_pmc()` becomes a new **Tier 1c**, reading NCBI's own copy via
  E-utilities `efetch` for whichever PMC ID is in hand — the caller's or a
  discovered one. Europe PMC serves the corpus its `inEPMC` flag describes;
  NCBI serves PMC itself, so this answers where Europe PMC cannot. It sits
  ahead of the free-PDF tier (renumbered to **1d**) because structured JATS
  beats a PDF that needs the optional `bmlib[pdf]` extra to read at all. An
  efetch reply carrying neither body nor abstract — what a publisher who does
  not release XML produces — raises rather than becoming a near-empty
  last-resort abstract.

  A PMC ID is now validated as `PMC\d+` in both PMC fetch helpers, at the point
  where it becomes a URL path rather than at each of the three places it
  arrives from.

  New constructor parameter `ncbi_api_key`, **declared last** for positional
  stability, sent with both NCBI requests. As with
  `TransparencyAnalyzer.pubmed_api_key` it changes which NCBI allowance the
  requests draw on, not bmlib's own pacing — the package still throttles
  nothing.

  **Moves stored values, not behind a flag:** `FullTextResult.source` gains
  `"ncbi_pmc"`, and results that were `content_kind="abstract"` or a bare
  `web_url` can now be `"fulltext"`. A caller who supplies `pmc_id` whose
  Europe PMC XML fails, or looks up an identifier Europe PMC cannot resolve,
  pays one or two extra requests in exactly the cases that previously ended at
  Unpaywall or Tier 3. Closes #47. Design:
  `docs/superpowers/specs/2026-08-02-pmc-id-resolution-fallback-design.md`.

- **`transparency`: `<DataBankList>` deposition accessions now score as
  data-availability evidence.** `bmlib.transparency` decided data availability
  by scanning full text for seven substrings (`"zenodo"`, `"figshare"`,
  `"dryad"`, `"github"`, `"available upon request"`, `"upon reasonable
  request"`, `"not available"`) — a paper that deposited its sequences in
  GenBank and said so in a structured field earned nothing unless one of
  those words happened to appear in its prose, and a closed-access paper has
  no full text to scan at all. `_parse_pubmed_signals()` now also collects
  `DataBankName` values against a curated allow-list drawn from
  [NLM's published vocabulary](https://www.nlm.nih.gov/bsd/medline_databank_source.html):
  `_DEPOSITION_DATABANK_LEVELS` maps each repository to the level a deposit
  into it establishes — BioProject, dbVar, Dryad, figshare, GenBank, GEO, PDB
  and SRA nominate `full_open`; dbGaP nominates only `on_request`, since its
  data needs Data Access Committee approval. A mapping rather than a
  set-per-level so that adding a repository has to state what a deposit into
  it is worth instead of inheriting the generous default. NLM's remaining
  names — dbSNP, GDB, OMIM, PIR,
  the three PubChem tables, RefSeq, SWISSPROT, UniMES, UniParc, UniProtKB,
  UniRef — are curated *reference* databases and score nothing (an OMIM
  number says the paper is about a known condition, not that these authors
  shared data of their own); a `<DataBank>` entry needs at least one
  non-blank accession to count.

  `data_level` now has two producers — this one and Europe PMC's existing
  prose scan — so `_Analysis` gained `note_data_level()`: each sub-step
  nominates a level and the strongest wins by rank (`_DATA_LEVEL_RANK`:
  `unknown` < `not_available` < `on_request` < `full_open`), mirroring the
  rule `industry_confidence` already follows. The winning level's points are
  now awarded once, by a new `_score_data_availability()` called from
  `analyze()` after every sub-step has run, rather than by the step that
  finds the level — with two producers, scoring at the point of discovery
  would double-count or spend points on a level a later nomination beats.
  PubMed's deposits are also reported verbatim as a new `risk_indicators`
  line, `Data deposited: GENBANK, PDB`, written whenever PubMed reported a
  deposit — even when the level it nominated lost the merge.

  **Moves stored values, not behind a flag:** `transparency_score` rises by
  10 or 20 for papers whose PubMed record names a deposition repository the
  prose scan missed. `data_availability_level` can move off
  `"not_available"`, which can in turn lift a `HIGH` result the
  industry-funding + restricted-data rule produced — `calculate_risk_level()`
  treats `"not_available"` as restricted. It can also move off `"unknown"`,
  but `"unknown"` was never restricted, so that move cannot affect the
  industry-funding rule; it can still turn a score-threshold `HIGH` into
  something else, since the added points can carry the score past
  `score_threshold`. And `"Data explicitly not available"` moves to the end
  of `risk_indicators` (it is now appended by `_score_data_availability()`
  after every step has run, rather than by the step that found the level),
  with `"Data deposited: …"` a new line alongside it. See
  `docs/superpowers/specs/2026-08-01-databank-data-deposition-design.md` for
  the rejected alternatives — a fifth `"deposited"` level distinct from
  `full_open`, scoring inside `note_data_level()` with a refund pass, and
  PubMed awarding only the diff against Europe PMC.

- **`scripts/sample_databank_names.py` — measures the two `DataBankName`
  allow-lists against real PubMed records.** `_TRIAL_REGISTRY_NAMES` and
  `_DEPOSITION_DATABANK_LEVELS` are curated from NLM's published vocabulary,
  and curation is the part that goes stale: the script counts records per
  candidate name, reads the literal spelling off the XML, and reports how
  bmlib classifies each — so a repository NLM adds shows up as `unclassified`
  with a non-zero count, and a member earning nothing shows up as dead weight.
  It also reports the *level* a deposit establishes, since that is a mapping
  rather than a membership test. Candidates include the deliberate exclusions
  (OMIM, RefSeq, dbSNP, PubChem-\*, the UniProt family): their counts are the
  evidence for leaving them out. A live runner like
  `scripts/sample_funder_names.py`, but covered offline by
  `tests/test_databank_sampler.py`, which pins the one property that makes its
  table trustworthy — a failed request never prints as a finding.

### Changed

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

### Fixed

- **`transparency`: `_TRIAL_REGISTRY_NAMES` was missing three registry names
  PubMed actually emits** — `JMACCT`, `REPEC`, and NLM's own spelling of
  UMIN's registry, `"UMIN CTR"` (bmlib had only the hyphenated
  `"umin-ctr"`, so the exact-match test failed on the string PubMed sends;
  both spellings are now kept, since the hyphenated form appears in older
  records). A paper registered in any of the three silently lost
  `SCORE_TRIAL_REGISTERED`. **Moves stored values:** `trial_registered`
  becomes `True` and `transparency_score` rises by 20 for affected papers;
  a paper registered in one of the three with no NCT id credited in its
  abstract also gains a new `risk_indicators` line, `"Trial registration
  found; posted-results status could not be checked"`, from the
  `_check_trial_registration` branch that `registration_not_checkable` now
  reaches for these names. Found while curating the deposition allow-lists
  above against NLM's vocabulary table; it is a pre-existing bug rather than
  part of that feature, so it gets its own entry.

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
