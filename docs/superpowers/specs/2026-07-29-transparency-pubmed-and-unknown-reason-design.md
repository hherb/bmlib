# Transparency: a real PubMed step, and a structured UNKNOWN reason

_Design, 2026-07-29. Closes issues #18 and #21._

Two changes to `bmlib/transparency/`, independent of each other but shipped
together because they touch the same two files and interact in one place (the
new PubMed call can flip an `UNREACHABLE` result into a measured one).

## Issue #18 — `pubmed_api_key` becomes load-bearing

`TransparencyAnalyzer.__init__` has always accepted `pubmed_api_key` and never
read it: the port from bmlibrarian's `StudyTransparencyAnalyzer` dropped the
`PubMedClient` that used it, leaving the parameter orphaned. The alternative
resolutions — delete it, or deprecate then delete — were rejected: Europe PMC
and PubMed overlap but are not identical, and NCBI's rate limit is a real
constraint on the applications that consume this library.

### The step

One `efetch` request per analysis, at most.

* **Placement:** after the Europe PMC step, before ClinicalTrials.gov. After
  Europe PMC so that a DOI-only analysis can recover a PMID from the EPMC
  record; before ClinicalTrials.gov so structured accessions can feed the
  results-posted check.
* **PMID source:** the caller's `pmid`, else `record["pmid"]` from the Europe
  PMC result. Neither → the step is skipped entirely and costs nothing.
* **Request:** `efetch.fcgi?db=pubmed&id=<pmid>&retmode=xml`, plus `api_key`
  when set, plus `tool=bmlib` and `email=<self.email>` (NCBI etiquette; the
  analyzer already holds an email for its User-Agent).
* **Plumbing:** goes through the existing `_rate_limit()`; a 200 sets
  `_api_reachable`; every failure is swallowed and logged at DEBUG, like every
  other query helper in the module.

### What the API key actually buys

Stated precisely, because overstating it is how the parameter became a lie in
the first place. NCBI meters unkeyed E-utilities traffic at 3 requests/second
**per IP** and keyed traffic at 10 requests/second **per key**. Passing the key
therefore moves bmlib's transparency requests out of the shared per-IP bucket
that the consuming application's own E-utilities traffic
(`bmlib.publications.fetchers.pubmed`, among others) is already competing for.

It does **not** speed bmlib up on its own: the analyzer's client-side pacing
stays at the shared `_MIN_REQUEST_INTERVAL_SECONDS` (0.35 s), which already
satisfies the unkeyed limit, and that one clock is shared across CrossRef,
Europe PMC, OpenAlex and ClinicalTrials.gov, each with its own etiquette.

### Signals parsed

A private frozen dataclass `_PubMedSignals` carries the parse result, so the
XML parsing is testable without HTTP and the merge logic is testable without
XML:

```python
@dataclass(frozen=True)
class _PubMedSignals:
    coi_statement: bool = False           # non-blank <CoiStatement>
    trial_accessions: tuple[str, ...] = ()  # ClinicalTrials.gov NCT ids, upper-cased
    other_registry: bool = False          # registered somewhere not followable
    funders: tuple[str, ...] = ()         # <Grant><Agency> names
```

Tuples rather than lists, so the frozen dataclass is genuinely immutable, and
every field defaults, so `_PubMedSignals()` is the single "nothing found"
value every failure path returns.

| Element | Rule |
|---------|------|
| `MedlineCitation/CoiStatement` | Non-blank ⇒ `coi_disclosed=True` and `SCORE_COI_DISCLOSED`, **only if** Europe PMC did not already establish it. A *missing* statement never demotes `None` → `False`: absence means the publisher supplied no statement to PubMed, not that the paper carries none. |
| `Article/DataBankList` | Accessions under a `DataBankName` in a curated trial-registry set count as registration. ClinicalTrials.gov accessions additionally feed `_check_trial_results`. |
| `Article/GrantList` | Non-empty ⇒ `SCORE_FUNDER_INFO`, **only if** CrossRef did not already award it. `Grant/Agency` matched against `_INDUSTRY_KEYWORDS` ⇒ `industry_funding=True` at `DEFAULT_INDUSTRY_CONFIDENCE` — a grant agency is structured metadata, the same evidence class as a CrossRef funder record, so it earns the same confidence as one rather than the weaker text-derived `TEXT_INDUSTRY_CONFIDENCE`. |

Data-deposition accessions (GENBANK, PDB, SRA, Dryad, …) also appear in
`DataBankList` and would be structured proof of data sharing, strictly stronger
than the current substring scan. Deliberately **out of scope** — it touches the
data-availability scoring path as well, and the parser is written so that adding
it later is a small follow-up rather than a rewrite.

### Not double-counting

Each score component is awarded at most once per analysis, tracked explicitly
rather than by assuming the paths are disjoint:

* **COI** is guarded by `coi_disclosed is not True`. That is reliable, not
  incidental: the only branch that sets `True` is the same one that adds
  `SCORE_COI_DISCLOSED`.
* **Funder info** needs a real flag, because "CrossRef found funders" is not
  recoverable from any value `_check_crossref` currently returns — only from
  its `"No funder information in CrossRef"` indicator string, and keying
  behaviour off an indicator string is precisely what issue #21 is about.
  `_check_crossref` therefore gains a fifth return element,
  `funder_info_scored: bool`.

### Contradictory indicators

`_check_europepmc` appends `"No COI disclosure found in full text"` or
`"COI disclosure status unknown (full text unavailable)"`. If PubMed then
produces a statement, those lines contradict the result and must go. Both
strings become module-level constants, appended and filtered through the same
name, and the PubMed merge appends
`"COI disclosure found in PubMed record"` when PubMed is what established it.

### Trial registries

`DataBankName` matching is case-insensitive against a curated set of registry
names PubMed actually emits: ClinicalTrials.gov, ISRCTN, EudraCT, ANZCTR,
ChiCTR, CRiS, CTRI, DRKS, IRCT, JPRN, jRCT, JapicCTI, NTR, PACTR, ReBec, RPCEC,
SLCTR, TCTR, UMIN-CTR.

A registry accession is structured metadata, so it is trusted directly: none of
the abstract heuristics apply to it — no `_MAX_OWN_TRIAL_IDS` cap, no
registration-cue window. Those exist because scraping NCT ids out of abstract
prose cannot distinguish a paper's own registration from a review's citation
list; a `DataBankList` entry is the publisher asserting *this* paper's
registration. The abstract heuristic remains the fallback for records with no
`DataBankList` (and for DOI-only analyses that never reached PubMed).

An accession is nonetheless validated as a well-formed `NCT\d{8}` id before it
is carried forward, because it is publisher-supplied text that would otherwise
be interpolated into a ClinicalTrials.gov URL path unchecked. A
ClinicalTrials.gov entry whose accession is missing or malformed still
establishes registration — it just cannot be followed up, which is what
`other_registry` records.

### Signature

Unchanged. `pubmed_api_key` keeps its name and position; nothing downstream
breaks, and the parameter simply starts doing what its docstring always claimed.

## Issue #21 — `TransparencyResult.unknown_reason`

`analyze()` returns `UNKNOWN` at score 0 in three distinct situations, today
distinguishable only by matching `risk_indicators` strings — documentation, not
API.

* New enum in `models.py`, serialised by value like `TransparencyRisk`:

  ```python
  class TransparencyUnknownReason(Enum):
      DISABLED = "disabled"
      NO_IDENTIFIER = "no_identifier"
      UNREACHABLE = "unreachable"
  ```

* New field `unknown_reason: TransparencyUnknownReason | None = None`,
  **declared last** on `TransparencyResult`, after `full_text_analyzed` — the
  same rule as `Publication.pmcid` and `BaseAgent.embedding_model`: downstream
  projects construct these positionally, so any other placement shifts every
  following argument silently.
* `to_dict()` emits `self.unknown_reason.value` or `None`; `from_dict()` maps a
  present value back through the enum and tolerates a missing key, so results
  persisted before this change still load.
* Populated at exactly the three `UNKNOWN` return sites. The existing indicator
  strings stay, for humans.
* Exported from `bmlib.transparency.__all__`.

**Invariant:** a result carries a reason if and only if its `risk_level` is
`UNKNOWN`. `calculate_risk_level()` returns only HIGH/MEDIUM/LOW, so every
`UNKNOWN` the analyzer produces comes from one of the three early returns.
Pinned by a test.

**Interaction with #18:** the PubMed call sets `_api_reachable` on a 200, so a
PMID-only analysis in which only NCBI answers is now measured rather than
reported `UNREACHABLE`.

## Also in this change

* `docs/manual/transparency.md` line 206 says "do not share one analyzer across
  threads"; line 492 says it is "safe to share across threads (since 0.4.0)".
  The code agrees with line 492. Line 206 is wrong and is corrected.
* `analyzer.py`'s module docstring says "No PubMed endpoint is currently
  called", which this change falsifies.

## Testing

Mocked HTTP throughout, as everywhere else in `tests/test_transparency.py`; no
network, no new dependency.

**PubMed step:** `CoiStatement` present / absent / whitespace-only; COI not
double-scored when Europe PMC already found one; a PubMed statement suppressing
the contradictory full-text indicator; missing statement leaving `None` as
`None`; NCT accessions feeding the results check and bypassing the abstract
heuristic's id cap; a non-ClinicalTrials.gov registry counting as registered;
`GrantList` awarding funder info once and not twice after CrossRef; an industry
agency detected at `DEFAULT_INDUSTRY_CONFIDENCE`; PMID recovered from the Europe
PMC record on a DOI-only analysis; the step skipped when no PMID is available;
`api_key` present in the request params when set and absent when not; a 200
setting reachability.

**`unknown_reason`:** each of the three causes; a determinate result carrying
`None`; `to_dict()` / `from_dict()` round trip; a legacy dict without the key
loading as `None`.
