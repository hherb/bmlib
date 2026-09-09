# HANDOVER — bmlib development

_Last updated: 2026-09-10. **0.10.0 is released and on PyPI**; thirty-one
changes sit unreleased, three of them instrument-only. All five version places
agree at 0.10.0. Every unreleased ROADMAP row carries an `*(unreleased)*`
marker._

## What is unreleased, and what it costs a downstream

Thirty-one changes, fourteen of them `fulltext` JATS fixes filed within days
of each other — whoever cuts the next release should describe those together.
**Per-PR argument is in `CHANGELOG.md`; only the *data* answer is kept here**,
because the version number answers the API question and never that one. Three
(#211, #212, #216) touch `scripts/` alone and cost a downstream nothing.

**The JATS fixes move what a caller of `JATSParser` gets, and each of those
moves what a bmlib *sync* stores** — reaching a bmlib path through the cached
HTML, since `_build_html` renders authors, figures, tables and both section
lists into the string `FullTextService` caches. Nothing *structured* is
stored, so a downstream holding cached full text should re-fetch, not only one
calling `JATSParser` itself. The largest is **#146/#149**, the only one
measured by diffing a corpus rather than reasoned: over 880 local PMC articles
/ 20,770 references, `citation` moves for 4,499 (21.7%) in 191 articles —
3,541 rebuilt, 958 emptied of an `<element-citation>` leak — `authors` for 502
in 14, rendered HTML for 576 in 23. Then **#111** (an author list that was
empty for the majority of open-access articles), **#115/#117** (`figures` and
`.tables`; roughly half of `graphic_url` moves from a thumbnail to the full
image), **#147** (prose and cached HTML for 68 of 880 articles, and a LaTeX
preamble out of every table cell), **#162** (cached HTML for 83 of every 997
recent articles), **#123/#125/#130** (`body_sections`, about one recent
article in ten), **#127**, **#120/#140**, **#129**.

**Eleven move stored *transparency* values.** Two are large enough that **any
downstream holding stored transparency results should recompute them**:

- **#184** — every Europe PMC full-text fetch was 404ing, so every analysis
  ran on the abstract. Over 48 real open-access analyses diffed against
  `main`: `coi_disclosed` moved in 32, `transparency_score` in 20 (+10 or +20,
  never negative), `risk_level` HIGH→MEDIUM in 5. Nothing scored worse *in
  that draw*, but the missing-COI downgrade is now reachable at all.
- **#194** — ClinicalTrials.gov had been refusing bmlib's own `User-Agent`
  with a 403 since the endpoint was first called, so `SCORE_RESULTS_POSTED`
  (15) had never been awarded to any paper and *"Registered trial without
  posted results"* was stored about every registered trial. **How many papers
  gain those 15 points is unmeasured** — a class of paper, not a rate.

The rest, briefly. **#203 has the widest population**: `risk_indicators` moves
for every analysis that did not scan full text, i.e. every closed-access
paper, and **a downstream string-matching *"COI disclosure status unknown
(full text unavailable)"* or its two siblings breaks** — read `full_text_status`
instead. **#161/#198** add fields (`full_text_status`, `trial_results_status`)
rather than moving values, and a downstream pinned to an older bmlib raises
out of `from_dict` on a row this version writes. **#193** moves
`full_text_status` and adds a COI indicator; **#187/#190/#191** move that
field alone; **#195**'s tri-state swaps one CT.gov indicator for another.
**#112** flips `industry_funding_detected` for a `"… plc"` funder. **#119**
moves a scan output for 0.61% of 97,909 articles. **#160** and **#183** move
nothing measurable. **#202** moves nothing at all.

**#188 moves one field for about a third of the records in one draw**: a
record claiming `inEPMC: Y` whose only identifier is neither a `PMC…` nor a
`PPR…` accession — 43 of 123 on 2026-09-09, all `source: MED` — stores
`full_text_status` `NOT_ATTEMPTED` where it stored `NOT_SERVED`, with its
provenance line. A `PPR` preprint also carries no `pmcid`, is still fetched by
its `id`, and moves nothing; saying *"carries no `pmcid`"* here named the
75,841-record population this fix deliberately keeps (PR #219's review), and
the draw is source-stratified, so the share over a caller's own corpus follows
its source mix and is not measured. Nothing else moves: the request it
replaces was measured at 0 of 43 served, and where it failed rather than 404'd
the move is from `REQUEST_FAILED` and costs a WARNING. A downstream counting
`NOT_SERVED` as *"EuropePMC has no full text for this"* was counting these
wrongly.

**#206 moves one field and one string for a paper whose results check was
partial**: `trial_results_status` goes `NOT_POSTED` → `PARTLY_ANSWERED`, and
`risk_indicators` swaps *"Registered trial without posted results"* for
*"Trial registration found; posted-results status could not be checked"*. **No
score moves** — neither indicator feeds it. A downstream string-matching the
first line, or reading `trial_results_compliant` without `is_answered`, was
counting these papers as trials that fell short. How many is not measured as a
rate: the cap truncates 8 of 30 papers naming an accession in a
trial-enriched draw, which is a share of an enriched population and not of a
corpus. **#218 moves nothing stored** — three log lines where there was
silence.

**#199 moves one value for a well-formed body**: `_json_count` refuses a
`true` or a fractional `cited_by_count`, so such a paper scores 5 points
lower. For a malformed body it moves values at every site it touches, which is
the point — 48 of 86 committed rows escaped the public `analyze()` on `main`.
**Two draws have now looked at how often that fires** (2026-09-08 and
-09-09): every served body was well-formed at all five endpoints, so no
coercer was *observed* to fire and "unmeasured" is an upper bound of a few
percent. Read both with #212 — the `SRC:PMC` strata contribute almost
nothing, so each draw is MED + PPR in all but name.

## Rules carried forward

Each is argued in full in `CLAUDE.md`, `docs/DECISIONS.md` and at its call site;
only the shortest form is kept here, because these are what a session gets wrong
again rather than what it can look up.

*Evidence.* A rule's population can be large, empty, or both, and only a draw
says which; one window is not the rate (#127 read 0 of 662 recent tables and 11
of 93 in a 1996-1998 draw; #119 reads 0.7% of one corpus and 3.45% of another).
**An issue's own remedy is a hypothesis too** — #162's cost ten minutes to
refute, #183's was refuted by 1,750 articles that end in a legal trailing
comment.
**Measure the population the code actually reads**; prefer a corpus with a public
name over one on your disk — and check that its *rendition* is the one the code
is fed, which is the half #138 learned the hard way. **A live Europe PMC draw
must be stratified by source and publication year**, a cursor page being a
contiguous block of accessions: unstratified, `IN_EPMC:Y` read 48% 404 where a
stratified draw read 3%, and no preprints where a source-stratified one read a
third. Needed twice now (#184, #191). And **run one live probe at a time** —
the per-host pacer is per-process (#179), so two concurrent runs poisoned
#184's first blast-radius measurement into reading "nothing moved". **A share is of a
denominator, and the rendition chooses the denominator** (#164's correction is 18
figures or a quarter of the population, depending only on which bytes you count).
A number in a comment goes stale silently and coherently —
`TestTheCitedPopulationsAreWhatTheCorporaHold` and
`TestTheStatedCountsAreWhatTheCorpusHolds` are the answer. A rule can be
spec-driven and still owe an instrument, and an instrument's vocabulary has to be
open or it certifies (#121). **Probe the contract, not the expression the reporter noticed** — #199 was
filed as four `.get()` calls and measured as 23 escapes, five of which are not
a `.get()` at all, so the fix as described leaves five. **State a blast radius
from a diff, not from the
call graph**: PR #148 reasoned soundly from a false premise, and four review
agents missed what two parses over 880 articles showed in minutes; `gained/lost`
is blind to the commoner case, a value that changed in place.

*Rules and their neighbours.* When a rule replaces a guard, ask what else that
guard was holding. **A guard whose reason moves needs its comment moved with
it** — #199 left `_check_trial_results`' `isinstance` reachable only for
`None`, and its comment still claimed the branch was "only the
200-carrying-a-list case". **And an `Any`-returning helper launders every
annotation above it**: three `_query_*` helpers said `dict | None` and could
all return a list, invisibly to mypy, because `_request_json` returned `Any`. When a fix extends a routing rule, walk every other path it
reaches — the guard written on one branch is the guard the others need, and the
same rule stated in prose on one branch is not applied on the next. Read the
rules *next to* the one you are adding before calling a fix one line. A stack of
frames needs the entries it will not use. **A set keyed on the element cannot
express a rule about the context** (`_INLINE_ELEMENTS` was right for #120 and
wrong for #146). **Suppressing a merge does not empty a buffer** — only an
accumulating child ever withheld anything.

*Diagnostics and tests.* A diagnostic's *level* is a claim that has to be
measured — **and the branch it sits on must be no wider than the draw**, which
is #191: DEBUG was measured on 404s and applied to every status code. **So a
branch that is several populations has to be split before it can be levelled**
(#218): one line over three would have set the level from a Bookshelf-heavy
draw and applied it to a PMID that does not resolve. **And a survivor can be a
gap in the *fixtures* rather than in the code** — the one equivalent mutant
here exposed that `len(root) == 0` had nothing separating it from a record set
carrying a non-article child. A
detector must report what it *checked*, not what it concluded. **A status enum
member is a stored claim**, so one covering several causes puts words in a
third party's mouth (#187/#190/#191, `NOT_SERVED` for a bmlib bug, a 503 and
an empty 200; #193, `NOT_ATTEMPTED` for an outage). **And before arguing about
a level, check the diagnostic exists** — #193's five branches emitted nothing
at any level, so there was none to raise. A
net needs its own false-positive net, and it must be free — the autouse
`parser_log` fixture makes all 186 pre-existing fixtures one. Key a counter on
*structure*, never on the routing it is checking, and **read the increment site,
not the name or the report** (a verdict line invented #162 outright).
**Asserting that a constant was imported is not asserting that it is used** —
#216's accession test was pinned by an identity check, and a mutant that kept
the import and restated the rule as `startswith` passed both affected test files (775 at the time) and the whole suite; pick a
fixture where the restatement and the real rule disagree. **A rule
enforced by prose is not enforced** (`TestTheAuditNetIsComplete`,
`TestOnlyAnAccumulatingElementReadsTheBuffer`,
`TestEverySectionIsGatedOnEveryCounterItReads`), and it demands a *choice* rather
than a field. **Checking the arithmetic is not checking the rule**, and check the
denominator too. **A zero over an absent population is not a clean result.** Tell
a vacuous green from one asserting silence: **ask which line of the fixture the
assertion depends on**. **Mutate the *old* half of a condition you extend**, and
give a fixture prose *after* the close as well as before it — two survivors hid
that way in PR #126. **An equivalent mutant is not an untested guard**: where
two independent protections cover one defect (#203's — the line is out of the
retraction's set *and* appended after it), no single edit changes behaviour.
Read that as the redundancy working, and say which pair you broke — **and
which edit you actually made**, because "behaviourally equivalent" and
"survives the suite" are different claims and the first does not imply the
second. Measured (PR #205's review): moving the append inside the retraction
window survives all 411 of `test_transparency.py`, while putting a provenance
line *into* the retraction set reddens 2 — both are equivalent, and one is
caught by a structural test. **When you re-scope a counter, keep both readings
per row**, because a redraw moves the sample, the bytes and the walk at once.
**A contract net is blind to a value read *wrongly* without raising** (#199): a
test asking *"did the analysis survive?"* passes over a body that silently
credits CrossRef with 15 points of funder information it never sent, and that
mutant survived every test in the file. Assert what the run **concluded**,
not that it finished — and expect the net to need a second kind of assertion
beside it, not a wider net. **Pick the fixture that separates the guard from its
own mutant**: only a *scalar* tells `isinstance(x, list)` from `x is None`,
since an object, a string and an absent key all reduce to the same empty
answer. That is the empty-list lesson of PR #195 in a second shape. **Where no
fixture separates two guards, pin the wire and say why** — with every probe
404ing, four exit-code terms fire together and deleting one leaves the suite
green; three of #211's are record-level where `is_reportable` is probe-level,
so nothing natural separates them. **And a malformed fixture reads as a
measurement**: `NCT01` is refused by `_NCT_ID_RE.fullmatch`, so the
accession-cap tests were measuring "no accession found".

*Live behaviour.* **A remote's error shape is a property of the *request*, not
of the endpoint.** #218's own text, the sampler's docstring and
`publications/fetchers/pubmed.py` all said NCBI serves `<eFetchResult><ERROR>`
at HTTP 200; probed, an evicted **history session** does and an **id-based**
efetch answers 400. Both comments were right about their own request and would
have been "reconciled" into one wrong claim by anyone tidying them. **A
property only a real remote can refute needs a real probe.** #194 — ClinicalTrials.gov 403ing bmlib's `User-Agent`, so no paper had
ever been credited with posted results — was invisible to the whole suite because
**no test in it makes a live request**, and it surfaced the first time an
instrument presented bmlib's own identity to the live endpoint. **A sampler
must address *and head* requests exactly as the code does**: #184's lesson is
usually told about URLs, and the header is the same lesson.

*Instruments.* **A list an instrument declares must be derived from the code it
measures, not restated** — #211's own field list was stale on the day it was
written, and an `ast` walk holding the two *equal* is what catches that; the
same rule that gave `TestTheAuditNetIsComplete` and
`TestOnlyTheHelperWalksTheEuropePMCResultList` their reason. **One declared
list can hide two rules**: `_check_crossref` iterates every funder where every
`_epmc_records` caller takes `records[0]`, so a single element sentinel would
be wider than the code on one endpoint and narrower on the other. **Where an
instrument is wider than the code, say so at the site and bound the cost** —
`if cr:` means an empty object body reaches no field read, so a first-level
`absent` count is an upper bound. **A guard on the page cannot see a loss one
level down**: a stratum that answered and kept none of its records read as a
stratum that was there (#212). And **a measured-empty population is an argument
for closing an issue, not for building it** — #204, #207 and #210 each measure
0; #204 and #207 had a schema change waiting on them, and #210 a semantics
question (its remedy is the comment, not the stored value).

*Cost.* **A test that pins a decision is reversed, not deleted, when the
decision is** — #206 flips
`test_one_refusal_does_not_hide_another_trials_posted_results`, whose comment
argued the old reading in full, and the new comment says which issue overturned
it. A session finding it should read the comment rather than restore the
assertion. **The cost of a schema addition is not a constant — it depends on
what else is unreleased beside it.** #198 was deferred in `docs/DECISIONS.md` as
wanting "its own release note and a downstream recompute", which was true and
already paid for: #184 and #194 force that recompute anyway, so the field was
free before the release and a second recompute after it. Ask what the batch
already costs before pricing a change against zero.

*Process.* An issue can be closed as COMPLETED without being fixed, and **a
closing keyword in prose has closed one nobody decided — four times**, the fourth
being the commit that warned about the other three. GitHub reads *"filed rather
than ‹keyword›: ‹number›"* literally and does not care that the sentence says the
opposite, nor that the substring sits in a quotation or a code span. So the rule
is **never reproduce the substring at all** — describe it, or drop the `#` — and
because the rule was written, read and then broken by one session, the real check
is after the fact: **after every merge that mentions an issue in prose, diff `gh
issue list` against what the commit says it filed and fixed.** That has now caught
a keyword that fired (#137, #142, #160) *and* six that did not (#147, #164,
#184, #187/#190/#191, #211, and #188/#216 — each closed by hand a session
late). **The same diff catches the other direction**: this session's found
four issues filed after the previous census was written and one filed from
outside the repo's own PR chain, so the census read four short. Also, a mutation
harness restoring with `git checkout -- <file>` deletes whatever is uncommitted
in it.

## This session: #218 and #206, the two silent steps

Both are library-level, both had a measured population waiting from a sampler
run — #218's from **2026-09-09**, #206's from 2026-09-08 — and both had the
same shape: a step that costs the analysis something and says nothing. **The full argument is in `CHANGELOG.md`
and `docs/DECISIONS.md`**; what follows is only what a next session needs.

**#218 — one branch was three populations, and a live probe redrew all three.**
`_parse_pubmed_signals` returned empty signals in silence for a body that
parses and carries no `PubmedArticle`, and that was the *majority* outcome of
the draw that sized it, 50 of 60 served bodies on **2026-09-09**. That date is
the 09-09 run and not the 09-08 one: the `no-citation` counter did not exist
until then, and the earlier run reported these same bodies as plain `xml`,
which is the defect that created it — quoting the figure against the earlier
draw is `_COUNTER_DEFINITIONS_VERSION`'s own scar, and PR #225's review caught
it in six places. The cheap reading of the issue
is one DEBUG line, and it would have been #191 exactly — the draw is
Bookshelf-heavy, so it licenses a quiet level for **book records** and says
nothing about the rest. Probed live on 2026-09-10:

- A set whose records are **all** `<PubmedBookArticle>` is **DEBUG**. Declined
  by name, and **0 of 60**
  `statpearls[book]` records and **0 of 100** drawn from `pubmed books[filter]`
  carry a `<GrantList>`, `<CoiStatement>` or `<DataBankList>` — which settles
  the issue's second question in favour of the line and nothing else. The old
  comment asserted the first two from the DTD and conceded the third
  unmeasured. **Children, and every child** — a descendant search matched a
  book anywhere and was tried first, so a legal mixed set (`PubmedArticleSet`
  is `(PubmedArticle | PubmedBookArticle)*`) reported an article record at the
  quiet level on its neighbour's strength, which is #191 inside #218's own fix
  (PR #225's review).
- An **empty `<PubmedArticleSet>`** at HTTP 200 — 205 bytes, what PMID
  999999999 returns — is **WARNING**. NCBI holds no record for an identifier
  bmlib was handed or derived, which is a fact about the identifier.
- Anything else that parses is **WARNING**, naming its root. A
  `<DeleteCitation>`-only set lands here, and it is the fixture separating
  `len(root) == 0` from its own widening.

**The issue's third population belongs to a different request, which is
sharper than the "refuted" a first cut wrote.** `<eFetchResult><ERROR>` at HTTP
200 is real — an evicted **history session** efetch serves it, reproduced live,
and it is why `publications/fetchers/pubmed.py` refuses a non-record-set root.
`transparency` fetches **by id**, where the same probe read 400 for a malformed
id list and an empty record set for an unknown id. Two comments, two requests:
do not reconcile them. Two error classes on one shape is not every class, so
the unrecognised-document branch is what takes one if it arrives at 200.

**#206 — the cap and the unanswered accession are one question.** `answered`
was a `bool` set on the first accession that replied, so one reachable *"no
results"* outvoted any number of unreachable ones; and `MAX_TRIAL_IDS_TO_CHECK`
sliced an unbounded list with no line, no indicator and no test of the
analyzer's truncation (`tests/test_api_failure_sampler.py` did reference the
constant; the wider *"nothing in the suite"* first written was false). Either
way
*"Registered trial without posted results"* was stored about a paper whose
remaining accessions bmlib never reached. `TrialResultsStatus.PARTLY_ANSWERED`
is both causes, sharing `_INDICATOR_RESULTS_NOT_CHECKABLE` on the argument that
already made `REQUEST_FAILED` and `NOT_CHECKABLE` share it, and on the
**unanswered** side of the partition — which is the load-bearing half, since
`trial_results_compliant` is what both downstreams render. Four things to carry
forward:

- **The measurement reversed the issue's own emphasis.** A partly-answered
  check is 1 of 30 and the cap truncates **8 of 30**, so the half that was
  entirely silent is the larger one. **Read the 1 as a floor**: that draw
  predates PR #213's correction of `TrialCheck.answered`, which counted HTTP
  200 where bmlib counts a non-`None` return and so deflated exactly that row,
  while the truncation count derives from `found > probed` and is unaffected —
  which is why the comparison rests on the 8. That sizes how *often* the cap
  bites and **not by how much**, so it is no argument for raising the
  constant; the sampler records each paper's count before the cap, and a run
  would answer it with one more report line.
- **The schema addition was free, not cheap.** `trial_results_status` is
  itself unreleased (#198), so this rides the recompute #184 and #194 already
  force. The same rule that overturned #198's own deferral.
- **The log splits the two causes; the stored field does not.** Raising the
  cap is an action, so the cap gets a WARNING naming the counts **and the
  dropped accessions** — the last being what raising the cap recovers, and
  what gave the line a subject it had lacked. Four of the five ways an asked
  accession fails to answer already have a line from `_request`; the fifth,
  `_json_bool` refusing a wrong-typed value, does not, and the comment
  claiming otherwise licensed that absence until PR #225's review narrowed it.
  Filed as **#226**. The line is gated on the walk
  not concluding `POSTED` — a posted result settles the paper — and
  deliberately **not** on the resulting status, or a truncation hides behind
  an outage.
- **One existing test asserted the reverse and is reversed with a comment
  saying so.** `test_one_refusal_does_not_hide_another_trials_posted_results`
  pinned *"one accession answered, and its answer was 'no' — so the finding
  stands"*, which is the reading the issue is about. A session finding it
  should read the comment, not restore it.

**Mutation: 22 mutants, 21 die.** The one survivor prints `root.tag` where the
code prints `_PUBMED_RECORD_SET_ROOT`, and on that branch the two are the same
string — provably equivalent, and it says so at the site; its premise is now
*pinned* by a fixture rather than only argued, which is PR #225's review.

**PR #225's review found six further defects, and four mutants that survived
the whole suite.** Carry the shape forward rather than the list: every one was
a claim the change made about itself that nothing checked.

- **A repeated accession manufactured #206's own false claim in the mirror.**
  `<DataBankList>` is `(DataBank+)`, so one paper naming one trial twice is
  well-formed input, and until PR #225 four such entries reported a truncation
  that lost nothing and retracted a `NOT_POSTED` ClinicalTrials.gov had
  answered for every distinct trial. Deduplicated at the parser, not the walk,
  because the field is what the sampler's own truncation count reads;
  `_find_trial_ids` has deduplicated since #202, so the two producers had
  disagreed and only the un-deduplicated one could reach the new branch.
- **Four surviving mutants**: deleting the `root.tag ==
  _PUBMED_RECORD_SET_ROOT` half of the empty-set guard (which let an empty
  `<eFetchResult/>` be reported as an empty `PubmedArticleSet` NCBI never
  sent), emptying `_PUBMED_SIGNALS_LOST`, passing `""` for the `pmid` at the
  one call site — so the *entire point* of the signature change was unpinned
  end to end — and substituting `len(ct_ids)` for `dropped` in the new
  WARNING. All die now.
- **The first cut of the constant's test asserted `_PUBMED_SIGNALS_LOST in
  message`**, which the mutant satisfies vacuously. That is this repository's
  own *"a log assertion must be unique to the line"*, turned on the assertion
  written to enforce it — assert the literal, and pin the constant separately.
- **The merge argument for `PARTLY_ANSWERED` was corrected.** *"Would
  re-running change this?"* **does** discriminate the two causes (`no` for the
  cap, `yes` for an unanswered accession), so the merge rests on their
  **co-occurring** — `unestablished` is a sum — and not on the criterion.
- `PARTLY_ANSWERED` gained the `analyze()`-level test every other member had,
  and `is_answered` the generic partition guard `FullTextStatus` has carried
  since #161. Without it, moving the member to the wrong side reddened one
  test in another class; it now reddens three.

**Not measured, and worth saying**: how far the trial-accession distribution
runs past three; whether any efetch error class reaches HTTP 200 for an
id-based request; how often a paper repeats an accession, the shape now
deduplicated on the DTD's own reading rather than on a count; and whether an
empty `<PubmedArticleSet>` is common enough to justify its WARNING, the
2026-09-09 draw not splitting the three populations apart (its `no-citation`
counter is deliberately one value — widening it would restate the analyzer's
predicates inside the instrument).

**Two findings were filed rather than fixed**, both out of these issues' scope
and both the same rule one step over: **#226**, a wrong-typed `hasResults`
refusing a results check with no line at any level while `REQUEST_FAILED`
absorbs it — #209's residual at the one site where it decides a stored status;
and **#227**, the PubMed step that was never asked (no PMID) leaving no line
and, unlike the full-text step's `NOT_ATTEMPTED`, no stored trace either.

## Current state

- **Version 0.10.0, released 2026-08-15 and live on PyPI** (0.4.0 → … → 0.10.0;
  `CHANGELOG.md` has the dates). The version lives in **five** places —
  `pyproject.toml`, `bmlib/__init__.py`, the README version line, `CLAUDE.md`'s
  header, and `docs/manual/index.md`'s header line — and all five agree. The
  fifth was missing from this list until 0.10.0 and had gone stale at 0.4.0 for
  five releases; only `bmlib/__init__.py` is guarded by anything but this list.
- **What each release shipped is in `CHANGELOG.md`** — do not re-narrate it here.
  0.6.0, 0.7.0 and 0.8.0 each moved stored values, none behind a flag, and they
  compound for anyone upgrading across them; 0.8.0's largest changes the shape of
  every synced PubMed title and abstract. **0.9.0 moves nothing stored.**
  **0.9.1 moves one thing**: #79 makes Tier 1d take the free PDFs it had been
  discarding, so stored full text is not comparable across the upgrade.
  **0.10.0 moves nothing stored but is not free**: no `download_days` row a
  previous release wrote is durable under #95's rule, so the whole window is
  re-fetched once. The two questions are independent, and a downstream reading
  only the number must still read this list.
- **Tests: 3769 passing + 63 skipped** (`uv run pytest tests/ -q`, measured
  2026-09-10 on this branch; `main` at 4e7ef00 measures **3747 + 63**, so this
  branch adds **22**, all in `tests/test_transparency.py`. Measure `main`
  rather than subtracting from a previous handover's figure — the 3614
  recorded two sessions ago was never what `main` held, which is how that
  number survived.)
  **The PostgreSQL half was not re-run for this branch and did not need to be**
  — it touches `transparency/`, `scripts/` and one `publications/` docstring,
  none of which carries SQL. The PostgreSQL half has not been re-run since the
  SQL last moved; the last measured figure with `BMLIB_TEST_POSTGRESQL_DSN` set
  is 2435 + 2 on the #105 branch. Of the 63 default skips, 61 are the PostgreSQL
  parameterisations, 1 is a PostgreSQL-only schema test, and 1 is
  `test_pymupdf_requires_dependency`, which runs only when PyMuPDF is *absent*.
  **PyMuPDF is installed in the dev venv.**
- **Run the PostgreSQL half locally — it is two minutes and it finds real bugs.**
  Postgres.app ships the binaries. The socket directory must be a *short* path
  (the 103-byte limit bites, and a scratchpad path exceeds it; `createdb` then
  fails while `pg_ctl` reports success):
  ```bash
  PGBIN=/Applications/Postgres.app/Contents/Versions/16/bin
  mkdir -p /tmp/bmlpg/run
  $PGBIN/initdb -D /tmp/bmlpg/data -U postgres --auth=trust
  $PGBIN/pg_ctl -D /tmp/bmlpg/data \
      -o "-k /tmp/bmlpg/run -p 55432 -c listen_addresses=''" -l /tmp/bmlpg/pg.log start
  $PGBIN/createdb -h /tmp/bmlpg/run -p 55432 -U postgres bmlib_test
  export BMLIB_TEST_POSTGRESQL_DSN="host=/tmp/bmlpg/run port=55432 dbname=bmlib_test user=postgres"
  ```
- **Documentation was rewritten for 0.4.0 and has been kept current since.**
  Treat drift as a regression. The `unreleased` markers in `docs/manual/` and
  `ROADMAP.md` are promoted at release time; **127 lines carry one** — 46
  `ROADMAP.md` rows and 81 spots across `docs/manual/transparency.md` (47),
  `fulltext.md` (18), `publications.md` (13) and `templates.md` (3).
  Recounted 2026-09-10 on this branch as
  `grep -ric unreleased ROADMAP.md docs/manual/*.md`, so it counts *lines* and
  not markers; the figure is measured, not maintained, so recount rather
  than adjust it — the previous handover's 109 was itself four short of what
  `main` held (42 + 37 + 18 + 13 + 3 = 113), which is what "recount" means. Grep case-insensitively for `unreleased`, not for
  `(unreleased)`: three of 0.10.0's were spelled `*(unreleased, #99)*` and
  `(changed, unreleased — …)`. Write the marker bare, never with a guessed
  version number. Markers inside `docs/superpowers/plans/` are historical records
  — leave them alone.
- **`main` is protected by the `protect_main` ruleset**: no deletion, no
  non-fast-forward push, and CodeQL code scanning plus code quality required to
  merge. CodeQL comes from GitHub's *default setup*, so there is no workflow file
  in the repo — and its generated workflow ignores a PR's `reopened` action, so a
  PR predating the setup needs a fresh commit rather than a close/reopen. It does
  **not** constrain the merge strategy; squash away (#78).

## Next up

### Open GitHub issues

**Forty-three open** as this file is written, **forty-one once this branch
merges and its two are closed by hand** (`gh issue list --state open --limit
200`, 2026-09-10 — the limit matters, `gh` pages at 30 and the bare command
reports a page size as a total): #86, #92, #94, #103, #124, #128, #137, #142,
#143, #144, #145, #150, #152, #154, #156, #157, #172, #173, #174, #175, #177,
#178, #179, #181, #186, #196, #197, #200, #201, #204, #206, #207, #209, #210,
#212, #214, #215, #217, #218, #221, #222, #223, #224. This branch answers
**#206** and **#218**. **#188 and #216 were closed by hand at the start of
this session** — PR #219 answered both, said in its own body that it carried no
closing keyword deliberately, and merged without anyone doing so; the process
rule below caught its **fourteenth** instance. #211, #199, #198/#202/#203,
#193/#194, #187/#190/#191, #184, #183 and #161 went the same way before it.
**The previous census predicted thirty-nine and the repo holds forty-three**,
because PR #219's own review filed #220-#223 after that line was written and
#224 arrived from outside the chain entirely. Count, do not project.

**#224 is the freshest and the only one filed from outside a PR review** — by
the maintainer, from a JATS parity check against the Swift port in BioMedLit,
and it is the one open issue that **loses content the document carries**:
`_append_paragraph` routes unsectioned back-matter prose only when
`self.in_body`, so `<ack>`, `<notes>` and `<fn-group>` children reaching
`<back>` directly are dropped — which is where funding acknowledgements and
competing-interest statements live, so `transparency` is blind to declarations
the article did make. The Swift side routes both and its comment names that
consequence. The suggested fix is three lines; the issue asks for a live survey
before sizing it, per this repo's own convention.

**#220-#223 are PR #219's leavings**, all instrument-side: #220 is already
answered; **#221** is an `id-not-an-address` that serves, which would refute
#188's guard and reaches no exit-code term; **#222** is the sixth endpoint
having no `_ORDINARY_STATUSES` set, so its level reaches the wiring net by
prose alone; **#223** is four string vocabularies where the repo's precedent is
a named enum — one sweep with **#217**.

**#214, #215 and #217 are the rest of PR #213's review**, also instrument-side:
**#214** is the sampler's PubMed population omitting the efetch `analyze()`
makes for a DOI-only record; **#215** buckets a sampler-side exception into the
rate a log level is set from; **#217** is `ProbeOutcome.cause` being stored and
then re-parsed by its own invariant.

Every open issue was found by review or measurement rather than by a failing
test, and **#224 is the first that loses records outright**. Beside it, **#124**
loses an exhibit's footnotes, **#150** renders a note-only reference as an empty
bullet, and **#128** would lose every figure image in a document binding XLink
to another prefix.

**Three still have a measured-empty population and want closing rather than
building**: #204 and #207 measure 0 of 124 each, #210 measures 0 of 55, and a
zero is an argument for a recorded residual — #210's remedy is its comment,
which states the conflation and then performs it. **That family is otherwise
done**: #206 was the one with work left and this branch takes it. **#212 blocks
nothing but qualifies every share here** — it is why the sampler exits 1 on a
clean run. Three options, three different populations: drop the PMC strata,
condition each query on the record being analysable, or page each stratum until
it fills.

**The obvious next work is #224**, on three counts: it is the only open issue
that loses content, its remedy is named and small, and it has a cross-project
witness in the Swift port rather than resting on one reading of the spec. What
it wants first is the survey the issue asks for — `<ack>`/`<notes>`/`<fn-group>`
carrying a direct `<p>` in `<back>`, over the two committed corpora, which
`scripts/sample_jats_exhibits.py` does not currently count. Of the rest:
**#186** is the last of the full-text-refusal family and is a decision rather
than a fix (below); **#178** is the one open *question*; **#196** is a latent
second site for the #194 class; **#197** mechanises a grouping that exists in
prose; **#200**, **#201** and #214/#215/#217/#221-#223 are shape. **The lesson
of #194 is worth acting on rather than only recording**: it was a live-only
defect the whole suite missed — no test in it makes a live request — found the
first time an instrument presented bmlib's real identity to a real remote. It
has now paid three times: #216's own first run found a rule wrong in the change
that added it, and this session's probe of NCBI redrew all three of #218's
populations and found the fourth claim about them belonged to a different
request. Nothing in `fulltext/` has ever been probed that way, so the *class*
is still open.

**Count them against the repo before trusting that number.** The line has been
wrong in several sessions, and an issue closed as COMPLETED without being fixed
is invisible to any such count. Almost every open issue was filed *by* a PR
reviewing an earlier fix, so the provenance is a chain: **#119** → PR #159,
filing #158/#160/#161; **#160** → PR #182, filing #183; **#183**/**#161** → PR
#185, filing #184/#186/#187; **#184** → PR #189, filing #188/#190/#191;
**#193/#194** → PR #195, filing #196-#203; **#199** → PR #208, filing
#209/#210/#211; **#211** → PR #213, filing #212 (from the instrument's own
first live run rather than from review, which was a new link in the chain) and
#214-#217; **#216/#188** → PR #219, filing #218 from its own run and
#220-#223 from review — of which this branch answers **#218** and **#206**.
**#224 is the first link from outside the chain**: filed by the maintainer from
a parity check against the Swift port, not by any PR here. Older provenance is in `gh issue view <n>` and
`CHANGELOG.md`. (#149 and #152 were filed and fixed inside one
PR, so neither ever appeared as open work.)

Each issue carries its own argument on GitHub; what follows is only what a
session needs to *choose* between them. Almost none is a drive-by.

**The JATS corpus redraw is done** (#132, #138, #158 answered), so #142, #143
and #150 have a population — and **all three measure empty**, which blocks them
on a stratified draw rather than on effort. Both corpora are 1,000-article draws
at `seed 0`: recent from `oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26.tar.gz`
(2023-2025, 997 served), back-filled from `…PMC002xxxxxx…` (1996-1998, 1,000).
The sample is drawn from the package and the bytes measured from Europe PMC's
`fullTextXML`, the two renditions disagreeing on exactly the cited populations
(`last_is_thumb` differs in 156 of 300 compared articles, the archive measuring
0 against 781 served where it does).

**Three issues lose content the document carries, each blocked on a modelling
decision, not on effort.** **#124** — no exhibit model has a `footnotes` field,
so a `<table-wrap-foot><fn>`'s abbreviations and funding notes reach nothing
while `<sup>` is flattened into the cell (`12.3a` pointing at a note that
exists nowhere). **#150** — a `<ref>` whose only content is a `<note>` renders
as an empty `<li>`, 4 instances in one publisher. **#144** — whether
`<on-behalf-of>` is a name or an attribution.

**#142, #143 and #145 are the rest of PR #141's review**, in `jats_parser`'s
contributor and reference half: #143 is bare last-wins with no parent test and
no log (the #116/#127 class), #142 is a `<collab>`'s children running together
with no separator, #145 is `<aff>` resolution through `@id`.

**#137 has been auto-closed twice without being decided** — a section-level
`<caption>`'s `<p>` children reach `body_sections` while its `<title>` is
dropped, and the sampler records the `<title>`'s parent but not the
`<caption>`'s owner, so the population is not derivable yet. **#152** —
`<article-id>`'s reachability guard is `parent == "article-meta" or
self.in_front` and each half deletes on its own with the suite green; nothing
is known to be wrong today, but this is where #109 was. **#128 is weaker than
filed** — all 13,624 `<graphic>` hrefs in the two corpora use the `xlink`
prefix bound to the XLink namespace, so the literal match is safe on measured
evidence; downgrade rather than close.

**#172–#181 are PR #171's, #176's and #180's leavings.** #178 is the group's
open *question*: whether LaTeX should win for a both-encoding inline formula at
all, replacing prose already correct in 20,046 formulas to recover 205. #177
routes a display formula reaching no section, caption or cell (192 in 23 of
97,909); #174 is MathML flattening losing spacing and brackets; #175 is a
formula deposited as an image, which no field carries; #172 is the cache
having no version stamp; #173 is a figure's `alt` duplicating its own
`figcaption`; #179 is the per-host pacer being per-process, so two concurrent
sampler runs double the rate against one host. **#181 is the sharpest of
them**: `last_is_thumb` increments only inside `len(graphics) > 1`, so its
population is `figures_multi_graphic` while the report and five files divide
it by `figures_with_graphic` — over its own population the recent window reads
**99.3%**, not 57.3%, which makes #117's rule far more load-bearing than the
published figure says. One remedy restates a share cited in five files and the
other needs both corpora redrawn, destroying #164's attribution.

**#186 is the last of the full-text-refusal family**, in
`_fetch_europepmc_fulltext`, and it is a decision rather than a fix — which is
why #187/#190/#191, then #193, and now #188 were taken first. The
unclosed-region refusal holds the element names in a stack and discards them at
the return, so neither the WARNING nor the stored `UNCLOSED_REGION` says
whether a `<sub-article>` or a `<response>` was left open. Naming one moves a
heavily-argued return contract — `_strip_nested_articles` returns `str | None`
and the `None` *is* the signal — so it wants a small result type or a dedicated
exception, the shape #160 chose one case over.

**#154, #156 and #157 are one job, and it is the funder corpus.** #154:
`scripts/sample_funder_names.py` writes `tests/data/funder_names.raw.json`,
which is in no commit, so the repo holds the 417 labelled names and not the 816
they were drawn from — a count over the unlabelled remainder is unanswerable
and a redraw has nothing to diff against. The old draw is unrecoverable, so the
fix carries a decision: re-label the intersection, or commit a fresh draw as
the baseline for the *next* comparison and say so. #156 needs a draw stratified
for European funders; #157 a targeted `\bplc\b` draw, the general one having
found none in 816. Both risks are pinned by
`TestTheKnownFalsePositivesAreKnown` — keep the choice, file the measurement,
do not quote the reasoning as measured. **Any session extending a funder list
owes #154 first**, `docs/DECISIONS.md` requiring the sampler be run before
either list is touched.

**#103 — `install_defaults()` reserves no `NAME_MAX` headroom for the temp
name**, so a template named beyond ~217 characters fails with `ENAMETOOLONG`.
Left alone deliberately: the names come from the caller's own source tree and
the failure is loud. The fix is a docstring line, not a cap — capping renames a
caller's template and `render("<name>")` then does not find it.

**#94, #92 and #86 are the older non-JATS three.** #94 and #92 are the same
shape — a guard resting on an unmeasured quantity, argued in full in
`CLAUDE.md` under *A completed day is a durable claim* — and **neither may be
tightened without running the sampler it asks for**: #94 because one bioRxiv
error body is indistinguishable from a quiet day (the tests pin *both* possible
quiet-day shapes so the guard cannot come to depend on the unmeasured answer),
#92 because `SHORTFALL_FAILURE_RATIO = 0.5` is bmlib's only threshold not set
from a sampled population and a floor tightened past the real benign gap
re-fetches that day for ever. #86: `docs/manual/llm.md` documents
`LLMClient.generate` and `LLMClient.embed` twice each — not a delete, the
copies differing (the two `embed` sections disagree on the default model,
`embed_batch`'s being right).

### Worth doing, not yet an issue

- **Widen bmlibrarian's `<0.6.0` pin** — `~/src/bmlibrarian` still pins
  `bmlib[ollama]>=0.5.1,<0.6.0` and has missed six releases; a downstream change,
  not a bmlib one. Read the intervening non-comparable behaviour changes first
  (see the release list above). The widened pin should clear
  `FullTextService.cache` being nullable, one of 0.9.0's three API changes.
- **Wire the segmenter and the rule-based extractors in.** Two halves of one
  roadmap item: the segmenter could give `CochraneAssessor` Methods/Results
  boundaries and `TransparencyAnalyzer` the paper's own Funding/COI/Data
  sections; `quality/extractors.py` is called by no tier. Each needs a design
  conversation.
- **Feed the stored grants to `transparency/`.** `TransparencyAnalyzer` runs its
  own `efetch` per paper to read `<GrantList>`, which `fetch_pubmed` already
  stores at sync time. Reading the table saves that request, but it is a scoring
  change that moves stored values — its own decision, not a quiet optimisation.

### bmlibrarian → bmlib porting (Phase 3 is next)

The "mother project" `~/src/bmlibrarian` holds functionality that belongs in
bmlib. The assessment and phased backlog live in
[`docs/plans/2026-07-17-bmlibrarian-porting-analysis.md`](docs/plans/2026-07-17-bmlibrarian-porting-analysis.md)
— **read that first.** It has a master priority table, a "do not port" list with
reasons, and open caveats (ClinicalTrials.gov legacy XML deprecation,
transparency/quality reconciliation, no GRADE engine exists, SSRF guard).

- **Phases 0, 1 and 2 are done and shipped** — Phase 0 in 0.4.0, Phase 1 in
  0.7.0, Phase 2 across 0.7.0 and 0.8.0 (rows 10, 9, 8, 4 and 11 of that doc's
  master table — rows, not GitHub issues; PRs #51, #54, #55, #58, #59).
- **Phase 3 is next**: discovery (#12), `pubmed_search` (#13), MeSH (#21),
  ClinicalTrials.gov (#14 — **check the caveat first**, the legacy bulk XML the
  parser targets was deprecated in the 2024 API v2 migration). Each is a larger
  subsystem than anything in Phase 2 and needs its own design conversation rather
  than a straight port. Phase 4 (the prompt-driven agent family, paper_weight,
  review building-blocks) follows, reconciled against the existing `quality/` and
  `transparency/` rather than forked.

### The port recipe (repeat it)

1. **TDD, always.** Behaviour tests first (upstream is the spec), watch them fail
   (`ModuleNotFoundError` is the correct red for a new module), then port. Bug in
   a test you wrote? Fix the test, not correct code.
2. **Modernise to bmlib style:** AGPL header, `from __future__ import
   annotations`, lowercase builtin generics, `datetime.UTC`.
3. **Sever app coupling:** injected connections/params instead of
   `get_db_manager()`/`bmlibrarian.config`; optional deps behind
   `try/except ImportError` raising `pip install bmlib[extra]`; LLM calls through
   `bmlib.llm` / `bmlib.agents.BaseAgent`, never raw `ollama`.
4. **Export** from the package `__init__.py` `__all__` — and if the module needs
   an extra, through a PEP 562 `__getattr__` rather than eagerly (#64: one eager
   re-export made ten modules unimportable on a core install).
5. **Verify** (tests + both ruff commands + mypy), **record** in `CHANGELOG.md`
   under `[Unreleased]`, and **reconcile rather than fork**.
6. **Read the spec on both sides; do not decide by eye.** Row 11's reviews found
   this three times. Reading someone's XML, check their DTD: `<Affiliation>` looks
   like a leaf, is declared `(%text;)*`, and a bare `.text` silently dropped rows.
   Declaring an output format, you owe that format's rules for *every* value, not
   only the ones carrying markup.

## Deliberate non-fixes — do not "fix" these

**Moved to [`docs/DECISIONS.md`](docs/DECISIONS.md). Read it before "correcting"
anything that looks wrong in `db/`, `llm/`, `agents/`, `context_processor/`,
`citations/`, `quality/`, `transparency/`, `publications/` or `fulltext/`.** Each
entry there was investigated and closed as correct, so reopening one wastes a
session; the file also records where each argument lives in full. Add new entries
there, not here — this file is for what still needs doing.

## Conventions and gotchas for the next session

- Coding rules live in `CLAUDE.md` under *Coding Conventions* — read them there
  rather than from a copy that can drift.
- `uv` only (never pip). Tests: `uv run pytest tests/ -v`.
- **Lint with the CI-pinned ruff, not the one in `.venv`** — CI pins **0.15.20**
  (`.github/workflows/ci.yml`), while `.venv` holds an older one that false-flags
  rules newer ruff removed:
  `uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .`
- **`uv run mypy` is a gate too** (#81), pinned to **2.3.0** in the `dev` extra
  with its settings in `pyproject.toml`. Give it no arguments — the bare command
  is what the `types` CI job runs — and run it in the dev venv: every extra but
  psycopg2 ships its own `py.typed` (that one via `types-psycopg2`), so against a
  bare interpreter mypy reports the optional imports *and `jinja2`, a core
  dependency*, as missing stubs. Anything deliberately unchecked is an inline
  `# type: ignore[code]` with its reason at the site, never a per-module
  `ignore_missing_imports`: `warn_unused_ignores` reports the first when it goes
  stale and can never report the second.
- Tests use in-memory SQLite (`connect_sqlite(":memory:")`) and mocked HTTP; no
  external services. `BMLIB_TEST_POSTGRESQL_DSN` must point at a database the
  tests may drop every table in (recipe under "Current state").
- New functionality needs unit tests; see CLAUDE.md's test-file mapping table.
- Session workflow lives in the `nextsession` skill (`.claude/skills/nextsession/`);
  the post-review fix-up workflow lives in the `fixall` skill.
- **Cutting a release** (0.4.0 through 0.10.0 were all cut this way): bump the
  version in the **five** places that carry it — `pyproject.toml`,
  `bmlib/__init__.py`, the README version line, `CLAUDE.md`'s header,
  `docs/manual/index.md`'s header line — promote the CHANGELOG's `[Unreleased]`
  body under a dated `## [X.Y.Z]` heading (leaving `## [Unreleased]` above it)
  with a short prose summary, promote any `unreleased` markers in `docs/manual/`
  and `ROADMAP.md`, add the release's own `ROADMAP.md` row, then commit on a
  `release/X.Y.Z` branch and open a PR. **The number is a claim about the API, not
  about the data**, so state the data answer in prose every time. Three shapes,
  all real: 0.9.0 was renumbered from 0.8.1 in review (API moved, nothing stored
  did); 0.9.1 is a patch that moves stored full text (#79); 0.10.0 is a minor bump
  moving nothing stored whose real cost is a one-off re-fetch of the whole sync
  window. After CI **and CodeQL** are green, merge it with any button, then **tag
  `main`'s tip rather than a particular commit** (#78):

  ```bash
  git checkout main && git pull --ff-only
  test "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" || exit 1
  grep -q '__version__ = "X.Y.Z"' bmlib/__init__.py || exit 1
  git tag -a vX.Y.Z -m "bmlib X.Y.Z" && git push origin vX.Y.Z
  ```

  The two checks are the whole point: the first catches a stale local `main`, the
  second catches tagging a commit that does not carry the version — the failure
  `release.yml` would otherwise find *after* the release is public. These are
  **annotated** tags, so verifying one needs `git rev-parse 'vX.Y.Z^{commit}'`.
  Then create the GitHub release, which is **what publishes** —
  `.github/workflows/release.yml` rebuilds, refuses to go on unless the tag
  matches `bmlib.__version__`, runs `twine check --strict`, asserts `py.typed`
  survived packaging, and uploads via Trusted Publishing. **Hand the `pypi`
  environment gate over rather than approving it**, even when `gh api
  .../pending_deployments` says `current_user_can_approve: true`: a PyPI upload is
  irreversible and the version can never be reused. Nothing is lost by waiting.
  Afterwards verify against `https://pypi.org/simple/bmlib/`, not the JSON API,
  which serves a stale CDN cache. Rehearse the whole path any time with a
  `workflow_dispatch` run, which targets TestPyPI only.
- **Rehearse the release gates locally before opening the PR** — `uv build`,
  `twine check --strict` on both artifacts, and a clean-venv install asserting
  `py.typed` survived packaging. `release.yml` runs them only *after* the version
  is burned and the release is public, so a failure there is expensive and a
  failure locally is free. On any release touching an `__init__.py`, probe the
  built wheel **one fresh interpreter per module** as well: a single process
  leaves the half-initialised parent in `sys.modules` and its siblings then
  falsely read as importable, which is how #64 was first mis-scoped.
- **Do not upload by hand.** The publish job has no `skip-existing`, so a manual
  upload makes it fail on a duplicate — which is why v0.5.0's and v0.6.0's runs
  still sit unapproved. v0.7.0, v0.8.0 and v0.9.0 all went the whole way through
  the workflow.
