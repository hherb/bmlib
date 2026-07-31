# Data deposition from PubMed's `<DataBankList>`

_Design, 2026-08-01. Implements the follow-up left open by the transparency
PubMed step (`2026-07-29-transparency-pubmed-and-unknown-reason-design.md`) and
made safe to add by the `_Analysis` carrier
(`2026-07-31-analysis-accumulator-dataclass-design.md`)._

## The problem

`bmlib.transparency` decides data availability by scanning the full text for
seven substrings — `"zenodo"`, `"figshare"`, `"dryad"`, `"github"`,
`"available upon request"`, `"upon reasonable request"`, `"not available"`.
That is the whole of it. A paper that deposited its sequences in GenBank and
said so in a structured field earns nothing unless one of those seven words
happens to appear in its prose, and a closed-access paper has no full text to
scan at all.

PubMed already carries the answer. `<DataBankList>` is publisher-supplied
structured metadata naming the repositories a paper's data went into, with
accession numbers. The transparency analyzer already fetches and walks it —
`_parse_pubmed_signals()` reads it for clinical-trial registrations and steps
over everything else:

```python
name = (databank.findtext("DataBankName") or "").strip().lower()
if name not in _TRIAL_REGISTRY_NAMES:
    continue
```

Everything that `continue` discards is a data-deposition signal, strictly
stronger than the substring scan it would replace. Reading it costs no
additional HTTP request.

This was deliberately deferred when the PubMed step landed, because it touches
the data-availability *scoring* path and the accumulator tuples of the day
made that awkward. Both obstacles are gone.

## Decisions

### A deposition accession means `full_open`, and the best level wins

`data_level` has had exactly one producer — `_check_europepmc` — which is why
that step could get away with assigning the field directly. A second producer
needs a merge rule, and the rule is **the strongest evidence of sharing wins,
regardless of which source found it or in what order**:

```python
_DATA_LEVEL_RANK = {"unknown": 0, "not_available": 1, "on_request": 2, "full_open": 3}
```

Ranked by how much sharing is *established*, not by how good the news is: an
explicit denial (`not_available`) outranks silence (`unknown`) because it is a
finding rather than the absence of one, and any positive level outranks the
denial.

The consequential case is a GenBank accession alongside a full text saying
*"data are not available"*. The accession wins. Those two statements are
usually not even about the same data — the denial in a clinical paper is
routinely about individual patient records, while the accession is a sequence
that is on a public server right now, fetchable by anyone reading this
sentence. Hard evidence of an actual public deposit beats a substring match in
prose whose subject the analyzer cannot determine.

This mirrors `industry_confidence`, which already takes a `max()` over
whatever the sources reported and is documented as "the strongest evidence
seen wins, regardless of arrival order".

Rejected: **letting a prose `not_available` win**, on the view that the
authors' own sentence is the more deliberate assertion. It is more deliberate,
but it is also less specific — the substring `"not available"` matches any
sentence in the document containing those words — and it would mean the
analyzer reads a live public accession and reports the data as unavailable.

Rejected: **a fifth `"deposited"` level** distinct from `full_open`. It would
let callers tell an accession from a substring match, but it needs its own
score, `calculate_risk_level()` has to learn it, and every consumer switching
on the string sees a value it has never seen. Provenance is delivered by an
indicator line instead (below), which costs nothing in the schema.

### Only repositories authors deposit *into*, from a curated allow-list

NLM publishes the `DataBankName` vocabulary in two tables. The second one
mixes two different kinds of thing:

| | |
|---|---|
| **Deposited into** | BioProject, dbGaP, dbVar, Dryad, figshare, GENBANK, GEO, PDB, SRA |
| **Cited a record in** | GDB, OMIM, PIR, PubChem-BioAssay, PubChem-Compound, PubChem-Substance, RefSeq, SWISSPROT, UniMES, UniParc, UniProtKB, UniRef |

An OMIM number says the paper is about a known condition. A RefSeq accession
names a reference sequence NCBI curated, not one these authors produced. Only
the first column is evidence that *these* authors shared *their* data, so only
the first column scores. The second column is excluded by a comment naming
each member and the reason, following the `_INDUSTRY_WORDS` precedent, so a
future contributor reads the omission as a decision rather than an oversight.

dbGaP is genuine deposition but **controlled access** — a reader needs Data
Access Committee approval — so it maps to `on_request` (10) rather than
`full_open` (20). That is what `on_request` already means, and it keeps the
level honest about what a reader can actually obtain.

Two frozensets, lowercased, mirroring `_TRIAL_REGISTRY_NAMES`:

```python
_DEPOSITION_DATABANK_NAMES = frozenset({
    "bioproject", "dbvar", "dryad", "figshare", "genbank", "geo", "pdb", "sra",
})
_CONTROLLED_DEPOSITION_DATABANK_NAMES = frozenset({"dbgap"})
```

Rejected: **treating anything that is not a trial registry as a data
deposit.** It needs no maintenance and survives NLM adding a repository, but
it fails in the wrong direction — a trial registry *missing* from our curated
set would be read as a data repository and awarded 20 points for sharing it
never demonstrated. That is not hypothetical: this design found three such
gaps (see below). An allow-list fails conservatively, and the prose scan is
still there to catch what it misses.

Zenodo is deliberately **not** added. It is not in NLM's vocabulary, so PubMed
does not emit it; the prose scan already matches `"zenodo"`.

### At least one non-blank accession is required

A `<DataBank>` naming a repository with an empty or absent
`<AccessionNumberList>` scores nothing. The claim being made is *structured
proof of a deposit*, and a repository name with no accession is an assertion
with no referent — nothing a reader could go and fetch. The trial-registry
branch next door already discards accessions that fail validation for a
related reason.

No format validation beyond non-blankness. The trial branch validates
`NCT\d{8}` because that accession is interpolated into a ClinicalTrials.gov
URL path; a deposition accession is never used to build a URL, so there is no
injection surface to defend and no cross-repository accession grammar worth
inventing.

### The data component is scored once, at the end

`_check_europepmc` currently nominates the level and spends the points in one
breath. With two producers that would double-count, or spend points on a level
later beaten. Sub-steps therefore only *nominate*:

```python
analysis.note_data_level(level)   # keeps the higher rank, raises on an unknown level
```

`_check_europepmc` nominates unconditionally, including the `"unknown"` it
falls through to when no pattern matched — that is a no-op at rank 0, and
calling it always keeps the step free of a "is this worth reporting?"
judgement that only the carrier can make.

and `analyze()` scores the winner once, after every step has run, through a
module-level `_score_data_availability(analysis)` beside `_merge_pubmed_signals`
(neither needs an HTTP client). It runs after the reachability early-return —
which discards the analysis entirely — and before the `MAX_TRANSPARENCY_SCORE`
cap.

This makes double-counting unrepresentable rather than guarded against, and it
means the `"Data explicitly not available"` indicator is written only if that
level actually won, so it never has to be retracted the way the PubMed COI
lines are.

Rejected: **scoring inside `note_data_level()` and refunding the beaten
level.** It preserves today's indicator ordering exactly, but the running
score would briefly hold a value that is later revoked, and the
`not_available` indicator would need a retraction pass.

Rejected: **letting PubMed award only the difference** between `full_open` and
whatever Europe PMC already gave. Smallest diff, and it makes the two steps
know about each other's arithmetic — the coupling `award_funder_info()` was
introduced to remove.

`note_data_level()` raises `KeyError` on a level outside `_DATA_LEVEL_RANK`
rather than defaulting it to zero: a typo must fail loudly, not silently rank
below everything. `calculate_risk_level()` recognises two further levels,
`"restricted"` and `"not_stated"`, which the analyzer has never produced and
still does not; they exist for callers computing the level themselves.

### Provenance goes in an indicator line

`data_availability_level` alone cannot distinguish a hard accession from the
word `"github"` appearing somewhere in the full text. `_merge_pubmed_signals`
appends one line naming the repositories:

```
Data deposited: GENBANK, PDB
```

`risk_indicators` is the only free-text channel the result has, it already
carries the structurally analogous `Industry funder: X`, and it round-trips
through `to_dict()`. A new dataclass field was rejected as widening the public
schema and every persisted result for a signal no consumer has asked for.

The line is appended when the finding is made, by the step that made it —
even when the level it nominates loses to a stronger one from Europe PMC. It
reports what PubMed said, which stays true regardless of which level won;
that separation is the rule the carrier design established ("a sub-step
publishes its own finding; it never reads a field back to decide what it
found").

## Components

`_PubMedSignals` gains one field:

```python
deposition_databanks: tuple[str, ...] = ()
```

PubMed's own spelling (stripped, not lowercased — it is rendered to humans),
document order, holding names that matched **either** deposition set and
carried at least one non-blank accession. Deduplicated on the lowercased name,
keeping the first spelling seen, so a record naming `GENBANK` twice — or once
as `GenBank` — yields one entry. Storing names rather than a pre-computed
level matches how `funders` already works: `_PubMedSignals` reports what the
record said, and the merge step decides what it is worth.

`_merge_pubmed_signals` nominates a level for each collected name rather than
pre-reducing them; `note_data_level` keeps the best, so `dbGaP` alongside
`GENBANK` lands on `full_open`.

`_Analysis` gains `note_data_level()`, joining `award_funder_info()`,
`note_industry_funder()` and `note_industry_coi()` as a named mechanism rather
than a convention every call site has to remember.

The `"Data explicitly not available"` literal becomes
`_INDICATOR_DATA_NOT_AVAILABLE`, since it is now written by a different
function from the one that finds the level, and `Data deposited: ` becomes
`_INDICATOR_DATA_DEPOSITED_PREFIX`.

`_parse_pubmed_signals`'s loop stops using `continue` as its only branch:
a name is a trial registry, or a deposition repository, or ignored.

## Two registry gaps, fixed in passing

Checking the deposition names against NLM's published vocabulary showed
`_TRIAL_REGISTRY_NAMES` is wrong in three places:

- **JMACCT** is missing.
- **REPEC** is missing.
- UMIN's registry is spelled `"umin-ctr"`; NLM's table says **`UMIN CTR`**,
  so the exact-match test fails on the string PubMed actually emits.

A paper registered in any of the three silently loses `SCORE_TRIAL_REGISTERED`
today. Both spellings of UMIN are kept, since the hyphenated form may appear
in older records. `jrct` and `iran registry of clinical trials`, already in
the set and absent from NLM's table, are left alone — they cost nothing and
jRCT is the live successor to Japan's earlier registries.

This is a one-line fix to a set the change is editing anyway, but it moves
stored scores, so it gets its own CHANGELOG entry rather than riding along
inside the feature's.

## Behaviour changes

All four move values a caller may have persisted. None is behind a flag.

1. `transparency_score` rises by 10 or 20 for papers whose PubMed record names
   a deposition repository the prose scan missed.
2. `data_availability_level` can move off `"not_available"` and `"unknown"`,
   which can in turn lift a `HIGH` result produced by the industry-funding +
   restricted-data rule.
3. Papers registered in JMACCT, REPEC or UMIN CTR gain
   `trial_registered=True` and 20 points.
4. `"Data explicitly not available"` moves to the end of `risk_indicators`,
   and `"Data deposited: …"` is a new line.

## Testing

New tests in `tests/test_transparency.py`.

**Parser** — a GENBANK entry with an accession is collected; an entry with an
empty `<AccessionNumberList>`, an absent one, or a whitespace-only accession
is not; OMIM and RefSeq are recognised and score nothing; dbGaP is collected;
matching is case-insensitive (`GenBank`); a `<DataBankList>` holding both a
registry and a repository feeds both branches; PubMed's spelling survives into
the field; repeats of one repository collapse to one entry.

**Merge rule** — every ordered pair in `_DATA_LEVEL_RANK` is checked in both
arrival orders; a lower level never demotes a higher one; an unknown level
raises.

**Scoring once** — Europe PMC `full_open` plus a GENBANK accession scores 20,
not 40; dbGaP alone scores 10; dbGaP plus a prose `full_open` scores 20, not
30; a prose `"not available"` alongside a GENBANK accession yields `full_open`
with **no** `"Data explicitly not available"` line.

**Indicator** — `Data deposited: GENBANK, PDB` names both, in document order;
it is still written when the level it nominated lost.

**Registries** — `JMACCT`, `REPEC` and `UMIN CTR` each establish
`trial_registered`.

**End to end** — `analyze()` with mocked HTTP, asserting score,
`data_availability_level` and indicators together.

## Documentation

`docs/manual/transparency.md`: the `Article/DataBankList` row, the
data-availability pattern table and the scoring table all describe only the
prose scan today, and the page carries an explicit "deliberately out of scope"
paragraph that this change closes. `CHANGELOG.md` `[Unreleased]`, the ROADMAP
row (⬜ Planned → ✅ Done), and HANDOVER's "worth doing" entry follow.
