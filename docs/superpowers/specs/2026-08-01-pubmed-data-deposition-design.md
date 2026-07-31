# Data Deposition from PubMed's `<DataBankList>` — Design

**Date:** 2026-08-01
**Status:** Approved (non-interactive session; the scope was set by the
handover note that queued this work, and every decision taken here is recorded
below with the alternative it beat)
**Closes:** #44
**Ships as:** its own PR

## Problem

PubMed's `<DataBankList>` carries two kinds of entry. The trial-registry names
— ClinicalTrials.gov, ISRCTN, EudraCT, … — are read by the PubMed step added in
#35. Everything else (GENBANK, PDB, SRA, GEO, Dryad, figshare) is a
**data-deposition accession**, and `_parse_pubmed_signals()` walks straight past
it:

```python
name = (databank.findtext("DataBankName") or "").strip().lower()
if name not in _TRIAL_REGISTRY_NAMES:
    continue
```

That discarded entry is publisher-supplied structured metadata asserting that
this article's data went into a public archive. Today the only thing feeding
`data_availability_level` is a substring scan of the retrieved text for four
repository names (`zenodo`, `figshare`, `dryad`, `github`) and two
availability-on-request phrasings. A deposition record is strictly stronger
evidence than that scan, and it reaches papers the scan cannot: the scan needs
Europe PMC full text, so a **closed-access** paper has no data-availability
signal at all today. This is the first one it can earn.

The work was held back from #35 deliberately, to keep the data-availability
scoring path out of that change, and #37 (the `_Analysis` carrier) was done to
clear its way.

## What makes it more than a parse

`data_availability_level` has exactly one producer today. `_check_europepmc`
publishes what it found and never reads the field back — a rule pinned by
`test_a_level_this_step_did_not_find_is_not_scored`, and sound only while
there is one producer. This change adds the second, so it has to bring:

1. **A merge rule.** Two steps can now both have an opinion about the same
   field. Strongest evidence wins, the way `industry_confidence` already
   resolves its two sources with `max()`.
2. **Score bookkeeping.** `SCORE_DATA_FULL_OPEN` (20) and
   `SCORE_DATA_ON_REQUEST` (10) are documented as mutually exclusive — that is
   what makes the best attainable total exactly 100. A second producer that
   simply added its own credit would break that, and a paper whose full text
   says "upon reasonable request" and whose PubMed record lists a GenBank
   accession would score 30 for one component.
3. **A retractable indicator.** A full text reading "individual patient data
   are not available" resolves to `not_available` and appends
   `Data explicitly not available`. If a deposition record then establishes
   `full_open`, that line contradicts the result field. This is exactly the
   situation `_INDICATOR_NO_COI_IN_FULLTEXT` is a named constant for, and the
   same answer applies: retract it.

## Scope decisions (recorded)

| Decision | Chosen | Rejected |
|---|---|---|
| Which databanks count | **Every non-trial-registry entry** | An allowlist of known repositories (GENBANK, PDB, SRA, …). `DataBankName` is an NLM controlled vocabulary of registries *and* archives; once the registries are named, the complement is the archives. An allowlist would go stale as NLM adds repositories, and would silently discard the very signal being added |
| Controlled-access archives (dbGaP, EGA) | **`full_open`, like the rest** | A carve-out mapping them to `on_request`. `on_request` in this module means "email the authors" — a documented, enforceable access process is not weaker than that, so the carve-out would *understate* them. It would also be an unmeasured keyword list, which this module has one standing rule against |
| Evidence required | **A non-blank `DataBankName`** | Requiring at least one `<AccessionNumber>`. `AccessionNumberList` is optional in the MEDLINE DTD, and the trial branch beside it already treats a registration as established when the accession is missing or unusable. Same rule, one line of code, no new concept |
| Accession numbers | **Not carried** | Carrying and validating them, as the NCT ids are. Validation there exists because the accession is interpolated into a ClinicalTrials.gov URL; nothing fetches a deposition accession, so validating it would be ceremony with no consumer |
| Merge ordering | **Openness rank; ties keep the incumbent** | "Last writer wins" (a later `unknown` would erase a real finding); "most reliable source wins" (needs a source ranking no field carries) |
| Where the rule lives | **One `note_data_availability()` method on `_Analysis`**, called by both producers | Leaving `_check_europepmc`'s inline block alone and adding a second one. Two copies of a merge rule is the shape `award_funder_info()` exists to prevent |
| Deposition indicator | **One `Data deposited in <name>` line per distinct databank** | No indicator (the caller could not tell why `full_open` contradicted the full text); one line listing all names (the funder precedent is one line per finding) |

## Design

### Parsing — `_PubMedSignals.data_banks`

One new field on the frozen signals dataclass, declared last:

```python
data_banks: tuple[str, ...] = ()
```

Distinct non-blank `DataBankName` values that are **not** trial registries, in
document order, spelled as PubMed spelled them (the indicator shows the name to
a human). Deduplication is case-insensitive, keeping the first spelling: the
name is a controlled vocabulary the loop beside it already matches
case-insensitively, so `GENBANK` and `GenBank` are one finding, not two.

The existing loop gains one branch. Its `continue` becomes the collection
point, so nothing about trial-registry handling moves.

### Merging — `_Analysis.note_data_availability()`

Two module-level tables replace the inline `if/elif/elif`:

```python
_DATA_LEVEL_RANK = {"unknown": 0, "not_stated": 1, "restricted": 1,
                    "not_available": 1, "on_request": 2, "full_open": 3}
_DATA_LEVEL_SCORES = {"on_request": SCORE_DATA_ON_REQUEST,
                      "full_open": SCORE_DATA_FULL_OPEN}
```

`_DATA_LEVEL_RANK` lists the whole vocabulary `calculate_risk_level()` accepts,
including the two levels the analyzer never produces (`restricted`,
`not_stated`) — so the table is the definition of the vocabulary and an
unlisted level raises `KeyError` rather than being silently ranked weakest.
Rank orders *evidence of openness*, which is why `unknown` sits below
`not_available`: `unknown` is the absence of a finding, and must never
overwrite one.

```python
def note_data_availability(self, level: str) -> None:
    if _DATA_LEVEL_RANK[level] <= _DATA_LEVEL_RANK[self.data_level]:
        return
    self.score += _DATA_LEVEL_SCORES.get(level, 0) - _DATA_LEVEL_SCORES.get(self.data_level, 0)
    self.data_level = level
    ...indicator bookkeeping...
```

Subtracting the credit already held is what keeps the component spent at most
once, so the documented maximum of exactly 100 survives a second producer. The
`<=` keeps the incumbent on a tie, so a step re-reporting a level it agrees
with changes nothing.

Indicator bookkeeping, in the same method because that is the only way the two
cannot drift apart: append `_INDICATOR_DATA_NOT_AVAILABLE` (a new module
constant, for the same reason the COI lines are constants) when the winning
level is `not_available`, and retract it when a stronger level supersedes it.

`_check_europepmc` loses its inline scoring block and calls this method
instead — including with `"unknown"` when it found nothing, which is now a
no-op rather than an erasure.

### `_Analysis.note_data_deposition(name)`

Appends `f"Data deposited in {name}"` if not already present and calls
`note_data_availability("full_open")`. The level is fixed inside the method
rather than passed in, for the reason `note_industry_funder()` fixes its
confidence: "an archive accession is `full_open`" is the rule, and a caller
free to choose could blur it.

### Folding it in

`_merge_pubmed_signals()` gains three lines beside its COI and funder branches:

```python
for name in pubmed.data_banks:
    analysis.note_data_deposition(name)
```

No new request, no new failure path: the record is already fetched, and every
existing failure path already yields empty signals.

## Consequences

**Stored results are not comparable across this change.** For a paper with a
deposition accession, `transparency_score` rises by up to 20,
`data_availability_level` moves to `full_open`, and — because rule 2 of
`calculate_risk_level()` fires on `industry_funding and data in (restricted,
not_available, not_stated)` — an industry-funded paper can move out of HIGH.
That last one is the intended effect: a public archive accession is hard
evidence, and it should outrank a prose scan that matched "not available"
somewhere in a full text.

## Testing

Behaviour tests in `tests/test_transparency.py`, TDD, against the three layers
already separated there — parsing without HTTP, the merge without HTTP, and one
end-to-end `analyze()` through the recording client.

| Test | Pins |
|---|---|
| A deposition databank is collected | the parse |
| …and is still not a trial registration | the existing boundary (extends `test_data_deposition_databank_is_not_a_registration`) |
| A blank name is not a deposition | no `Data deposited in ` line |
| `GENBANK` + `GenBank` is one finding | case-insensitive dedup |
| A trial registry never appears in `data_banks` | the two branches stay disjoint |
| Deposition sets `full_open` and scores 20 | the merge |
| `not_available` from full text is superseded, and its indicator retracted | the retraction |
| `on_request` upgraded to `full_open` scores 20 total, not 30 | the credit swap — the invariant that makes 100 attainable |
| A second `full_open` re-report scores once | the tie case |
| `unknown` does not erase an established finding | the rank floor (replaces the assertion in `test_a_level_this_step_did_not_find_is_not_scored`, whose premise — one producer — this change ends) |
| A closed-access paper with a deposition accession earns `full_open` end to end | the reason for the change |
