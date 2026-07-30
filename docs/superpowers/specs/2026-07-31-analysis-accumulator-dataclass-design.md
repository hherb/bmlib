# `analyze()`'s Accumulators: A Carrier Object, Not Tuple Arity — Design

**Date:** 2026-07-31
**Status:** Approved (interactive session; scope decisions recorded below)
**Closes:** #37
**Ships as:** its own PR

## Problem

`TransparencyAnalyzer.analyze()` accumulates ten values across five sub-steps,
and passes each one in and out by position:

| Sub-step | Signature today |
|---|---|
| `_check_crossref` | `(client, doi, score, industry_funding, industry_confidence, indicators, funder_info_scored)` → 5-tuple |
| `_check_europepmc` | `(client, epmc, score, indicators)` → 6-tuple |
| `_merge_pubmed_signals` | `(pubmed, coi_disclosed, score, indicators, industry_funding, industry_confidence, funder_info_scored)` → 6-tuple |
| `_check_trial_registration` | `(client, pmid, doi, score, indicators, *, epmc, pubmed)` → 4-tuple |
| `_check_openalex` | `(client, doi, score)` → `int` |

Each sub-step reads well on its own. The cost is at the call sites, which are
now multi-line destructuring assignments where element *order* is the only
thing binding a value to its name. Two hazards, both latent today:

- **A mis-ordered unpacking is a silent, type-compatible swap.**
  `industry_funding: bool` and `funder_info_scored: bool` are
  interchangeable to the interpreter, as are `score: int` and any other int.
  Nothing raises; the score is simply wrong.
- **`funder_info_scored` invites the bug it was added to fix.** It exists so
  `SCORE_FUNDER_INFO` cannot be awarded twice, once by CrossRef's funder
  records and once by PubMed's `<GrantList>`. It was originally computed fresh
  inside `_check_crossref`, which is correct only while CrossRef is the first
  funder source consulted; PR #35 threaded it in as a parameter to fix that.
  The same shape invites the same bug for the next shared component — the fix
  is a convention two call sites must both remember, not a mechanism.

PR #35 also had to widen two of these signatures to add a single boolean.
Nothing is broken today. This is worth doing before the next signal source
lands in `analyze()` — the `<DataBankList>` data-deposition work already on the
roadmap is exactly that.

## Scope decisions (recorded)

| Decision | Chosen | Rejected alternatives |
|---|---|---|
| Carrier | **Mutable module-private `_Analysis` dataclass, mutated in place** | Threading a `dict` (no field names checked, no type hints); `NamedTuple` (immutable — every step still rebuilds and returns it, keeping the arity) |
| Sub-step returns | **`None`; every result lands on `_Analysis`** | Returning a small per-step result object (a second type per step, and `analyze()` still folds it by hand) |
| `_check_openalex` | **In scope** | Leave it — a 1-tuple is no hazard. Rejected: one step threading a value while four mutate is the inconsistency that makes the next contributor guess |
| `industry_coi` | **Folded inside `_check_europepmc`** | Keep returning it as a local finding for `analyze()` to fold |
| CrossRef duplicate funders | **Deduped, like PubMed's** (behaviour change) | Preserve exactly — rejected by the user in favour of one rule for both sources |
| `_PubMedSignals` | **Stays frozen** | Merging it into `_Analysis`. It is a message from one source, not shared state |

## Design

### The carrier

A module-private mutable dataclass in `analyzer.py`, beside `_PubMedSignals`.
No `to_dict()`/`from_dict()`: it never leaves the module, so the project's
serialisation convention for dataclass *models* does not apply — the frozen
`_PubMedSignals` is the local precedent.

```python
@dataclass
class _Analysis:
    score: int = 0
    indicators: list[str] = field(default_factory=list)
    industry_funding: bool = False
    industry_confidence: float = 0.0
    data_level: str = "unknown"
    coi_disclosed: bool | None = None
    trial_registered: bool = False
    results_compliant: bool = False
    full_text_analyzed: bool = False
    funder_info_scored: bool = False
```

Field defaults reproduce `analyze()`'s current initialisation exactly, so
constructing `_Analysis()` is the whole of the setup block it replaces.

### Three named operations

The double-scoring hazard is closed by making "award this component once" a
method rather than a convention:

- **`award_funder_info()`** — adds `SCORE_FUNDER_INFO` the first time *any*
  source reports funders, and records that it is spent. Called by
  `_check_crossref` and by `_merge_pubmed_signals`. Neither call site has to
  know whether the other ran first, which is what makes a third source safe to
  add.
- **`note_industry_funder(name)`** — a named funder from structured metadata:
  sets `industry_funding`, raises `industry_confidence` to
  `DEFAULT_INDUSTRY_CONFIDENCE`, and appends a deduped `Industry funder:
  {name}`. The confidence is hardcoded rather than a parameter because
  "structured metadata" is precisely what distinguishes this path from the
  COI-prose one below; a caller free to pass any confidence could blur them.
- **`note_industry_coi()`** — the weaker full-text signal: `industry_funding`,
  confidence raised to `TEXT_INDUSTRY_CONFIDENCE`, and the industry-COI
  indicator appended. That string is currently a literal inside `analyze()`
  and becomes a module constant, matching the five indicator constants already
  there.

Both `note_*` methods raise confidence with `max()`, as the code does today, so
a paper with both signals keeps the stronger one regardless of arrival order.

### Sub-steps become mutators

| Sub-step | After |
|---|---|
| `_check_crossref` | `(client, doi, analysis) -> None` |
| `_check_europepmc` | `(client, epmc, analysis) -> None` |
| `_merge_pubmed_signals` | `(pubmed, analysis) -> None` |
| `_check_trial_registration` | `(client, pmid, doi, analysis, *, epmc, pubmed) -> None` |
| `_check_openalex` | `(client, doi, analysis) -> None` |

`analyze()` constructs one `_Analysis`, passes it to each step, and reads
fields off it when building the `TransparencyResult`. The three early
`UNKNOWN` returns are untouched — they precede any accumulation.

`industry_coi` stops being a returned finding: `_check_europepmc` calls
`analysis.note_industry_coi()` itself, at the end of its body. That is where
`analyze()` folds it today — after the COI and data-availability indicators —
so indicator order is unchanged.

### `_merge_pubmed_signals`' contract inverts

Today it copies `indicators` on the way in and returns the copy, documented as
"mutates nothing it is given". That guarantee exists because its COI branch
*rebinds* the list (filtering out two now-contradicted lines) while its funder
branch *appends*, so a caller that ignored the return value would be left with
a half-applied merge.

With no return value there is nothing to ignore, and the hazard is gone by
construction. The copy is therefore removed and the docstring rewritten. The
retraction rebinds the field — `analysis.indicators = [...]` — which is
consistent with how every other field is updated, and safe because every
reader goes through `analysis`.

`test_the_merge_does_not_mutate_the_caller_s_indicators` inverts rather than
dies: it is replaced by a test asserting that the retraction *and* the appends
both land on `analysis.indicators`.

### The one behaviour change

`note_industry_funder()` dedupes for both sources. Today `_merge_pubmed_signals`
guards its append with `if line not in indicators` — PubMed emits one `<Grant>`
per award, so an agency funding four grants would otherwise produce four
identical lines — while `_check_crossref` appends unconditionally. CrossRef can
list one organisation once per award in exactly the same way.

Consequence: `risk_indicators` can be shorter for a paper whose CrossRef record
names one funder repeatedly. No score moves (`SCORE_FUNDER_INFO` was already
once-only), `industry_funding_detected` does not move, and risk level does not
move. Needs a CHANGELOG entry and a test.

## Testing

The existing 1033 tests are the spec. 27 white-box call sites in
`tests/test_transparency.py` destructure the tuples; each migrates to:

```python
analysis = _Analysis()
analyzer._check_europepmc(client, _epmc_record(), analysis)
assert analysis.coi_disclosed is True
```

Assertions are carried over unchanged, so any drift in the refactor surfaces as
a failure rather than as a quietly-adjusted expectation. Three tests are added:

1. A CrossRef record naming one funder twice yields one indicator line — the
   behaviour change above.
2. The replacement merge-contract test: a retraction and an append in the same
   merge both land on `analysis.indicators`.
3. At `analyze()` level: CrossRef funders *and* PubMed grants together count
   `SCORE_FUNDER_INFO` once. `test_crossref_respects_an_already_spent_component`
   pins the sub-step in isolation; nothing currently pins the composition.

## Out of scope

- **Splitting `analyzer.py`.** It is 1225 lines and does warrant it eventually,
  but that is a different change with a different review.
- **`_Analysis.to_result()`.** `analyze()` keeps building the
  `TransparencyResult`: it owns `document_id`, the settings and the risk-level
  call, none of which are accumulator state.
- **The `<DataBankList>` data-deposition signal.** The roadmap item this
  refactor clears the way for, not part of it.
