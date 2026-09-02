# Changelog

All notable changes to bmlib are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); bmlib follows
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed

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

  The instrument says so now rather than printing a verdict it cannot support.
  `print_report` prints the exhibits with no label of their own and, separately,
  how many of those hold a label below, in place of `PREMISE HOLDS` /
  `PREMISE VIOLATED`; the identical line over `<caption>`, which had the same
  defect and never fired only because those two counts have been equal in
  every draw, goes the same way. The claim is corrected in `jats_parser.py`,
  `CLAUDE.md`, `ROADMAP.md`, the sampler's module docstring and here.

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
  all four of which the corpus records. Both committed corpora are 997
  measured articles of 1,000 drawn at `seed 0`:
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
  corpus (13,617 deposits) — the guard is for a population measured empty.
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
  windows, and the new back-filled one contributes **0 `<table-wrap>` in 997
  articles**. (*That `oa_comm`'s 1996-1998 material is scanned page images
  with no tabular markup is an inference* from 0 tables beside 627 figures and
  3,873 `.png` deposits — no counter measures it.) That 0 is an absent
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
  figure counters keep the subtree walk on purpose, their percentages being
  cited. **The argument that used to justify that is refuted by this branch's
  own corpora, and the scoping is left open rather than restated** (#164): it
  read "both draws record zero nested exhibits and every foreign owner is a
  `<td>`, which can only sit under a `<table-wrap>`", and the redrawn recent
  corpus holds **7 nested `<fig>`** (all `PMC12143881`) and **three** foreign
  owners — `<td>` 82, `<inline-formula>` 69, `<disp-formula>` 2, 153 graphics
  in 12 of 997 articles. An `<inline-formula>` is not confined to a
  `<table-wrap>`, so the premise that the two walks agree on the figure side
  no longer holds. What that costs is **not** measured on the rendition the
  percentages are of: a spot measurement over the same drawn articles'
  *archive* bytes moves the multi-graphic figure count 77 → 58, which is the
  right order of magnitude to matter and the wrong rendition to cite. Do not
  read the cited 58.1% / 57.3% as confirmed against a scoped walk — they are
  what the subtree walk measured.

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
  does support — 121 exhibits carrying no `<label>` at all, in 83 of 997
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
  nothing; the depth is not matched against the element name, so one *inside* a
  region does close it, and only a document expat would reject can carry one.
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
  taken-and-not-returned shape loses a `<tex-math>` formula from the prose
  around it and a `<disp-formula>` from the article outright — filed as #147,
  since delimiting LaTeX in prose is a decision rather than one more member of
  a set. Note that #147 is now scoped to prose *outside* a citation: inside
  one the ancestor test merges `<tex-math>` like anything else, so a formula
  cited in a reference emits raw undelimited LaTeX and an `<alternatives>`
  pair emits both encodings. That path measures 0 of 10,671 `<mixed-citation>`
  across 227 articles and 0 in the local corpus, so it is unexercised rather
  than live.

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
  parser never sees. Across 12,650 `<contrib>` in the two committed corpora:
  `<collab>` names 14 contributors (all in the recent window:
  0.18% [0.11-0.30] of *that* window's 7,798)
  and **`<string-name>` none at all** — 0 of 12,650, upper
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
  1,994 articles**, every one of the seven in a single eLife article
  (`PMC12143881`, 7 of its 19 figures). One article in 1,994 is a fact about
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
  hole, JATS admitting `<fn>` there too. An empty label is not inert either —
  the renderer substitutes `Table {i + 1}` for a table and `Figure {i + 1}`
  for a figure, so the symptom is an invented number rather than a blank.

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
  58.1% / 57.3% on the recent committed corpus and 44.0% on both counts on
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
  [2.0-4.1]) in `tests/data/jats_exhibits.json`** and 0 of 997 in the
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
