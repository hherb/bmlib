# Redrawing the JATS Corpora From a Named Public Artifact — Design

**Date:** 2026-09-01
**Status:** Approved (interactive session; scope decisions recorded below)
**Closes:** #138, #132, #158
**Sizes, without deciding:** #142, #143, #147, #150
**Ships as:** its own PR

## Problem

Three defects in the evidence behind `fulltext/jats_parser.py`'s routing rules,
and they are the same defect at three sizes.

**The instrument does not measure what the parser routes (#138).**
`scripts/sample_jats_exhibits.py` walks into `<sub-article>` and `<response>`,
which the parser suppresses entirely (#110). Every counter it writes is
therefore a whole-document count where the parser's is a
suppressed-region-excluding one. This was checked for exactly one population —
of the 69 `section_renaming_titles` in the recent draw, 69 sit outside a nested
article and 0 inside — and left unchecked for the rest. PR #141's contributor
counters are the ones it bites hardest: a peer-review `<sub-article>` names its
reviewers with `<contrib><name>`, the densest such construct JATS has, so
`contribs`, `contrib_name_spellings` and `nested_contribs` are all inflated,
and `articles_losing_every_author` is *suppressed outright* by a single
reviewer's `<name>` — the counter failing in the direction it exists to detect.

**The cited figures come from a corpus nobody else can draw (#132).** #115's
"0.7% of a general open-access draw, both eLife" and #117's 49.9% / 49.5% cite
a 276-article draw that is in no commit and no longer exists. Exhibit nesting
measures 0 in *both* committed 300-article draws, so that figure has no
in-repo evidence at all.

**The nested-article rate is cited four times and the figures disagree by 8×
(#158).** `jats_parser` says 4 in 249 (1.6%) for peer-review deposits; the
manual and the CHANGELOG say 288 of 1,022 articles lose body text (28.2%);
#119 measures 3,382 of 97,909 (3.45%) carrying a region at all; and a fourth
citation reads 6 of 876. The last bounds the second — an article can only lose
body text to a region it carries — and the spread is genuinely a per-publisher
property, which is why 28.2% can be honest for a draw weighted to
PLOS/eLife/BMJ/F1000 and why the draw not being in the repo is the problem.

**Why one job.** Scoping the walk without redrawing leaves the committed
corpora unre-derivable by the fixed instrument, which is the one property they
exist for. Redrawing without scoping re-commits inflated counts. And the
corpora have to be redrawn anyway for #132, so the four issues that are waiting
on a population from this same walk — #142, #143, #147, #150 — are answered by
the same pass or by a second expensive one later.

**Why now.** These rules ship in the next release. The CHANGELOG entry is still
editable; after the release it is a correction rather than a draft.

## Scope decisions (recorded)

| Decision | Chosen | Rejected alternatives |
|---|---|---|
| Corpus behind the cited figures | **PMC OA baseline packages (`oa_comm_xml.*.baseline.2025-06-26`)** | A fresh live Europe PMC draw (re-commits #132's own defect one year later); the 880-article local draw (machine-local, unreproducible) |
| The live Europe PMC source | **Kept, unchanged** | Deleted (it measures the rendition bmlib actually parses, and the rendition-gap mode reuses its fetch plumbing) |
| Window definition | **Absolute ISO date ranges** | `--months-ago` counted from `date.today()` (the sampler's own docstring confesses this makes the same command draw a different sample — it is *why* #132 exists) |
| Draw size | **1,000 per window** | 300 (spends the redraw without buying precision); 3,000 (8 MB of committed JSON); whole-package aggregates (breaks the row format the cited-populations test sums over) |
| Nested-article scoping | **Stop descending; record the scoped *and* unscoped count** | Scope silently (throws away the measurement #158 wants); leave unscoped and caveat (#138 as filed) |
| Rendition risk | **Measured: a paired live diff over 300 articles** | Caveated in prose (the caveat is exactly what #119 found to be load-bearing for comments) |
| The four waiting issues | **Sized in the same pass, left open for their rule decision** | Sized later (a second full redraw); decided here (each is a modelling question this PR has no mandate for) |
| New counters' vocabulary | **Open — count what is there, under its own name** | A closed list (#121: counted against one, an unforeseen spelling falls into `(none)` and is *reported* as absent) |
| Determinism | **`(package names, window, target, seed)` recorded in the corpus header** | Rely on file order (an unpacked directory's `iglob` order is not stable across machines) |
| Reading a candidate's date | **Read the whole member** | A prefix read — 49% faster, and it silently drops 19% of recent articles at 8 KB along a publisher-correlated axis |

## Measured evidence for the design itself

Probed 2026-08-31/09-01 against `/Users/hherb/pmc_archive/packages/`:

| Fact | Measurement |
|---|---|
| Scan throughput, tarball, whole members | 122,576 articles in 16.5 s (**7,447/s**), `PMC002xxxxxx` |
| Scan throughput, tarball, first 8 KB only | 11,086/s — 49% faster and **wrong**, see below |
| Recent-window candidates (2023 – 2025) | **97,668** of 97,909 in `PMC012xxxxxx` (93,014 published 2025, 4,013 in 2024, 641 in 2023; **241 older, 0 undated**) |
| Back-filled candidates (1996 – 1998) | **3,141**, all in `PMC002xxxxxx` — `PMC000xxxxxx` and `PMC001xxxxxx` measure **0** |
| Overlap, 880-article Europe PMC draw × unpacked package | **4 articles** — which is why the rendition diff cannot be a corpus-to-corpus comparison |
| Taking the earliest `<pub-date>` year vs excluding deposit/submission dates | **0 of 3,000 differ**, in each window separately |
| Articles carrying no `<pub-date>` at all | **0 of 2,000**, in each window separately |
| Articles whose `<pub-date>` is missed by a prefix read | 4 KB: **1,385** of 2,000; 8 KB: **379**; 16 KB: 49; 32 KB: 8; 64 KB: **1** (recent window) |

The recent-candidates row was wrong twice over as first written — "only 17
undated and 26 older", which contradicted the "0 of 2,000 carrying no
`<pub-date>`" row three lines below it and summed to 97,694 against the
package's 97,909. Both halves came from the same defect: `_YEAR_RE` required a
*bare* `<year>` open tag, so an attributed one made the article undated and
undrawable — 17 of 97,909, every one `<year iso-8601-date="2025">`, every one
inside the window, and 14 of them one contiguous journal block
(PMC12085917–PMC12085930), so publisher-clustered. The pattern is anchored on
the element name now, the undated population measures **0**, and the older
count is the 241 it always was. It is the same silent, publisher-correlated
loss the prefix read is rejected for below, arriving by a different route —
which is why it was fixed and the recent window redrawn rather than
documented as a limitation.

The prefix-read row is the one that shaped section 5. The Europe PMC draw spans
PMC6102553–PMC13327518, almost all of it in packages held as tarballs, so
pairing those two corpora as they sit would compare two different *samples*
and report the sample difference as a rendition difference.

The `oa_comm` subset is thin at the back-filled vintage, and **not where the
accession ranges suggest**: `PMC000xxxxxx` (3,028 articles) and `PMC001xxxxxx`
(27,515) hold *no* 1996–1998 publications at all, while `PMC002xxxxxx` holds
3,141. Accession order is deposit order, not publication order. The
back-filled window therefore draws from `PMC002xxxxxx` alone; the two older
packages are recorded here as measured-at-zero so a later reader knows they
were checked rather than overlooked.

**The last row is why the scan reads whole members.** A prefix read is the
obvious optimisation and it fails silently in the direction that matters: at
8 KB it finds no date for 19% of recent articles, which the date filter would
drop from the draw with nothing raised. The miss is not random — it is
front-matter size, so it tracks author-list length and abstract length, which
are *publisher* properties, and those are the axis every population here varies
along. **The evidence is the recent window specifically**: on the back-filled
window a prefix read is harmless — over the whole of `PMC002xxxxxx` an 8 KB
read and a whole-member read both yield 3,141 candidates and the prefix misses
none of them — because 1996–1998 front matter is short. A rule drawn from that
window alone would license exactly the optimisation that costs a fifth of the
recent one. Reading whole members costs 33% more wall time and nothing else —
for a tarball the bytes are decompressed either way.

## Design

### 1. A second source, not a changed one

`--package <dir-or-tarball>` (repeatable) selects articles from a PMC OA
baseline package. A tarball is streamed with `tarfile` — members are read
sequentially and never unpacked — and a directory is walked with `iglob`. Each
candidate is read **whole** to extract `<pub-date>`; the prefix read that would
obviously do instead is measured above and is both lossy and wrong.

An article's date is **the earliest `<year>` in any `<pub-date>`**, and the
rule deliberately does not key on the date's declared kind. The attribute
vocabulary is not one vocabulary: the recent window is dominated by
`pub-type="epub"` (2,704 of 3,000 articles) and the back-filled range by
`pub-type="ppub"` (2,868), JATS 1.x spells the same thing
`date-type="pub" publication-format="electronic"`, and `pmc-release`,
`nihms-submitted` and `epreprint` all appear. The obvious refinement — exclude
the deposit and submission kinds, which are the two that could pull a date away
from publication — was measured against the simple rule and **changes the
earliest year in 0 of 3,000 articles in each window**, so the simple rule is
kept on evidence rather than on the enumeration being complete.

The live Europe PMC walk (`open_access_pmcids`, `_fetch`, the pacer, the
journal, the unmeasured accounting) is untouched. The two sources meet at
`measure_article(pmcid, xml)`, which already takes bytes.

### 2. Absolute windows

`--from-year YYYY --to-year YYYY` replaces `--months-ago` for the package
source. Year precision, not day: `article_year` reads a `<year>`, and a
`<pub-date>` need carry no month — so a day-precision window would silently
drop every article dated to the year alone. The two committed draws become:

| corpus | window | packages |
|---|---|---|
| `tests/data/jats_exhibits.json` | 2023 – 2025 (calendar years) | `PMC012xxxxxx` |
| `tests/data/jats_exhibits.backfill.json` | 1996-01-01 – 1998-12-31 | `PMC002xxxxxx` |

The recent window is bounded by the baseline snapshot itself (dated 2025-06-26
in the package name) rather than by today: the package cannot contain anything
published after it, so `2023 – 2025` is in practice 2023-01-01 to 2025-06-26.
Pinning the window to the artifact is what makes the draw reproducible.

Selection is `sorted(candidates)` then a seeded `random.Random(seed).sample`,
so the same four inputs yield the same 1,000 PMCIDs on any machine. `--seed`
defaults to 0 and is recorded whether or not it was passed, because a default
that is not written down is not a reproduction instruction. The header
records `source`, `packages`, `window`, `target` and `seed` beside the existing
`articles` / `unmeasured` / `rows`.

### 3. Scoped, and the correction measured

`walk()` stops descending at `<sub-article>` and `<response>`, matching the
parser's suppressed region (#110). Two counters record what that changed:

- `nested_article_regions` — how many such regions the article carries, at
  top level and nested, which is #158's number on a named corpus;
- `unscoped` — a nested mapping holding, for **every** counter whose scoped
  and unscoped values differ, what the old whole-document walk would have
  said. Only the differing fields are stored, so an article carrying no
  nested region contributes an empty mapping and the corpus does not double
  in size. The corpus therefore answers "how much was the old count inflated"
  for every population rather than for the one that was spot-checked.

Recording both is what turns #138 from a correction into a measurement. The
report prints the pair wherever they differ.

### 4. Four new counter families

One walk, four waiting issues. Each counter is open-vocabulary: a name is
counted under itself, never against a list this script wrote, because counted
against a closed list an unforeseen spelling is reported as absent — #121's
mis-certification inside the instrument built to detect the next one.

| Issue | Counters |
|---|---|
| #142 | `collab_children` (Counter, by element name), `collabs_with_element_children` |
| #143 | `contribs_multi_collab`, `contribs_multi_string_name`, `name_alternatives` |
| #147 | `disp_formulas`, `inline_formulas`, `tex_math`, `mml_math`, `formula_alternatives_both`, `disp_formulas_with_label` |
| #150 | `refs`, `refs_note_only`, `ref_child_kinds` (Counter) |

`_record_contrib` already descends with the parser's own stop rule (it halts at
a nested `<contrib>`, where the parser's frame stack hands routing to the inner
contributor), so #142's and #143's counters go there and inherit that scoping.

### 5. The rendition gap, measured

The citable corpus is the *archive* rendition; `FullTextService` feeds the
parser Europe PMC's `fullTextXML`. #119 established that these differ in a way
that matters — Springer's commented-out `<authorqueries>` block is in the
archive copy of three articles and absent from Europe PMC's copy of the same
three — so the difference is measured rather than caveated.

`--compare-europepmc N` takes N PMCIDs from the drawn sample, fetches each
one's `fullTextXML` live through the existing paced client, runs
`measure_article` over **both** byte strings, and writes per-article per-field
deltas to `tests/data/jats_exhibits.rendition.json`. N = 300. An article
Europe PMC will not serve is unmeasured and enters no denominator, accounted
the way every other population here is.

This is the only live-network step, and it is what licenses citing
archive-drawn figures for a parser fed by Europe PMC.

### 6. Reconciliation

`TestTheCitedPopulationsAreWhatTheCorporaHold` asserts every cited figure
against the committed corpora and its docstring says a redraw is *meant* to
break it. Its failures are the checklist: each names the file to reconcile.
Files that carry cited figures are `bmlib/fulltext/jats_parser.py`,
`CLAUDE.md`, `docs/manual/fulltext.md`, `CHANGELOG.md`, `ROADMAP.md`, and the
sampler's own module docstring.

The framing in every one of them is **"redrawn from a named public artifact"**,
not "corrected": every figure moves both because the walk is scoped and because
the sample is different, and attributing the movement to the scoping alone
would be a claim the draw cannot support.

## Testing

Offline, in `tests/test_jats_exhibit_sampler.py`, per the standing rule that a
live runner's *reading* is a maintainer's evidence and must be covered without
the network:

- the package source: a synthetic tarball and a synthetic directory yield the
  same rows; a member that will not parse is unmeasured, not empty;
- the date filter: an article outside the window is not drawn, one with no
  `<pub-date>` is not drawn, and the earliest date wins where several are
  declared;
- **the whole-member read**, with a fixture whose `<pub-date>` sits beyond
  8 KB of front matter. This is the guard against the optimisation someone
  will reach for later: it is 49% faster, it raises nothing, and it drops a
  fifth of the recent window along a publisher-correlated axis;
- determinism: the same `(packages, window, target, seed)` draws the same
  identifiers, and a different seed draws a different set;
- the scoping: a fixture carrying a `<sub-article>` full of exhibits,
  contributors and captions contributes **nothing** from inside it, and its
  `unscoped_*` counters record what the old walk would have said;
- each new counter family, including its empty case;
- the rendition diff: two byte strings differing in one construct produce a
  delta naming that field, and identical inputs produce none;
- the standing negative control that the sampler's predicates still differ
  from the parser's — a corpus labelled by the rule under test can only
  confirm that rule.

## What this PR does not do

It does not pick a rule for #142, #143, #147 or #150. Each is a modelling
decision — what a `<collab>`'s parts join with, which of several names wins,
how LaTeX is delimited, whether a note-only `<ref>` is a reference — and this
PR's mandate is to make those decidable, not to decide them. Each issue gets
its measured population posted and stays open.

It does not touch `bmlib/fulltext/jats_parser.py` except to reconcile the
figures in its comments. No parser behaviour changes, so no stored value moves.

It does not delete the live Europe PMC draw path.
