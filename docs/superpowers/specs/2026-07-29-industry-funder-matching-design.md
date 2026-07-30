# Industry-Funder Matching: Word Boundaries, Measured — Design

**Date:** 2026-07-29
**Status:** Approved (interactive session; scope decisions recorded below)
**Closes:** #36
**Ships as:** its own PR, separate from #33 — unrelated defect, unrelated module

## Problem

`_INDUSTRY_KEYWORDS` in `bmlib/transparency/analyzer.py:55` is a substring test:

```python
["pharma", "biotech", "therapeutics", "inc.", "corp.", "ltd.", "gmbh", "laboratories"]
```

The three org-suffix forms carry a trailing dot because a bare `"inc"` matches
`"Lincoln"`, `"Vincent"` and `"province"` as a substring. The cost is a false
negative whenever the name omits the dot:

```
"Genentech Inc."  -> industry_funding_detected True
"Pfizer Inc"      -> industry_funding_detected False
```

This has always applied to CrossRef `funder[].name`. PR #35 widened the same
list to PubMed `<Grant><Agency>`, where NLM-normalised strings drop the
punctuation more often, so the false-negative rate is now higher than it was.

`industry_funding_detected` feeds a HIGH-risk rule
(`industry_funding and restricted data` in `models.py:225`) and a MEDIUM one
(`models.py:234`), and HIGH applies `tier_downgrade_amount` to a paper's
quality tier. A false positive is therefore more costly than a false negative,
which is why the issue insists the change be measured rather than assumed.

## Scope decisions (recorded)

| Decision | Chosen | Rejected alternatives |
|---|---|---|
| Matching rule | **Split the list: stems stay substrings, org suffixes get word boundaries** | Word-boundary everything (breaks `pharma` → `pharmaceutical`); leave as-is |
| Corpus | **Live-sample CrossRef + PubMed once, hand-label, commit the fixture** | Hand-written fixture only (no genuine false-positive rate); sample without committing (not re-runnable, numbers unauditable) |
| Optional suffixes (`llc`, `plc`, `ag`, …) | **Each earns inclusion from the corpus** | Ship the full list on intuition |
| Pre-existing false positives the corpus reveals | **Fix in this PR** | File a separate issue; fix only when recall-neutral |
| PR split | **Separate PR from #33** | One combined PR |

## Design

### 1. The list is two kinds of thing

`pharma`, `biotech`, `therapeutics` are **stems**: they exist to match *inside*
longer words — `pharmaceuticals`, `biotechnology`. Applying `\b…\b` uniformly,
as the issue's one-line framing suggests, would trade a punctuation false
negative for a much larger stem false negative. The two classes separate:

- `_INDUSTRY_STEMS` — substring test, semantics unchanged.
- `_INDUSTRY_ORG_TOKENS` — one precompiled word-boundary alternation, trailing
  dot optional, so `Inc`, `Inc.` and `INC` all match while `Lincoln`,
  `Vincent` and `province` do not.

A comment records why the split exists, because collapsing it back into one
list is exactly the "simplification" a later session would reach for.

### 2. One predicate

Both call sites (`analyzer.py:388` for PubMed agencies, `analyzer.py:779` for
CrossRef funders) currently inline
`any(kw in name.lower() for kw in _INDUSTRY_KEYWORDS)`. They collapse into a
module-level

```python
def _is_industry_funder(name: str) -> bool:
```

— one definition to test, one to measure, and the only thing the corpus test
needs to call.

`_INDUSTRY_COI_KEYWORDS` is untouched. It matches COI *prose*, a different
corpus with different failure modes, and the existing comment already explains
why the org suffixes must not be applied there.

### 3. The corpus

`scripts/sample_funder_names.py` — a live runner outside the pytest suite, in
the established pattern of `scripts/smoke_test_tool_calling.py`:

- CrossRef `/works?filter=has-funder:true&select=funder`, with a polite
  `mailto`, paged until ~400 unique `funder[].name` values;
- PubMed `esearch` over a broad recent-literature query, then `efetch` in
  batches, harvesting `<Grant><Agency>` from whichever records carry a
  `<GrantList>`, until ~400 unique values.

Both are low-volume — a handful of requests each. The script paces itself
(there is no grant-support filter to narrow the PubMed search with, so it
takes what the batches yield) and stays under NCBI's keyless 3/s limit; it
does not share the analyzer's rate limiter, which is per-analyzer state the
script has no instance of.

Labelling covers every string either matcher flags, every string the two
matchers disagree on, and a random sample of shared negatives — that is what
bounds the false-positive measurement, since unflagged names cannot contribute
false positives. Each entry carries `name`, `source` (`crossref` | `pubmed`)
and `label` (`industry` | `not_industry` | `ambiguous`); `ambiguous` entries
are kept with their reason and excluded from the metrics rather than being
silently dropped. The labelled corpus is committed to
`tests/data/funder_names.json`, so a reviewer can re-run the numbers and a
later edit cannot quietly regress them.

### 4. Ship rule, fixed before the data is seen

- The new matcher must not lose precision against the current one, and must
  gain recall.
- Each optional suffix (`llc`, `plc`, `ag`, `bv`, `nv`, `pty`, `sa`) is
  measured independently and ships only on ≥1 true positive and 0 false
  positives in the corpus. Short ambiguous tokens are expected to fail this —
  `ag` and `sa` are the ones to watch.
- Ties go to precision, because a false positive here can downgrade a paper's
  quality tier.

### 5. Pre-existing false positives

`laboratories` is expected to flag `Sandia National Laboratories` and
`Los Alamos National Laboratories` — US national laboratories, not industry.
This is outside #36's stated punctuation scope but inside the scope of "measure
precision", and having measured it, leaving it would mean knowingly shipping a
defect. Any keyword the labelled corpus proves to be a net false-positive
source is corrected in this PR, and the CHANGELOG says plainly that scores can
move in **both** directions across this change, so stored scores are not
comparable across it.

If the corpus shows a correction that trades away substantial recall, the
finding and the numbers go in the PR description so the trade is visible rather
than buried in a diff.

## Testing

`tests/test_funder_matching.py`:

- the punctuation cases both ways: `Pfizer Inc`, `Genentech Inc.`,
  `Novartis Pharma AG`, `Boehringer Ingelheim GmbH` match; `Lincoln`,
  `Vincent`, `province`, `Provincial Health Authority` do not;
- stem preservation: `pharmaceutical`, `biotechnology` still match, pinning the
  reason the split exists;
- whatever the corpus decides about `laboratories` and the optional suffixes,
  pinned as explicit cases with the finding named in the test;
- the corpus metric test: load `tests/data/funder_names.json`, run
  `_is_industry_funder` over it, assert the measured precision and recall meet
  the floors this PR establishes.

The metric test runs offline against the committed fixture — no network in the
suite, per the repo's standing rule.

## Documentation

- `CHANGELOG.md` under `[Unreleased]`: the matcher change, the measured
  numbers, and the explicit warning that detection moves in both directions.
- `docs/manual/transparency.md`: what counts as an industry funder and why the
  two keyword classes differ.
- `HANDOVER.md` deliberate-non-fix list: why `_INDUSTRY_STEMS` and
  `_INDUSTRY_ORG_TOKENS` must not be merged back into one list.

## Verification

`uv run pytest tests/ -v`, then `uvx ruff@0.15.20 check . && uvx ruff@0.15.20
format --check .` — the CI-pinned ruff, not the older one in `.venv`.
