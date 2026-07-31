# bmlib.transparency — Transparency Analysis

Multi-API transparency analyzer for biomedical publications. Queries external APIs to assess funding sources, data availability, conflict-of-interest disclosure, and clinical trial registration compliance, returning a 0–100 score and a [`TransparencyRisk`](#transparencyrisk) level.

> **A score of `0` no longer means "opaque".**
> Since 0.4.0, [`analyze()`](#transparencyanalyzeranalyze) checks whether *any* external API answered. If none did, it returns `transparency_score=0` with `risk_level=TransparencyRisk.UNKNOWN`, the indicator `"Transparency APIs unreachable — score not determinable"`, and `unknown_reason=TransparencyUnknownReason.UNREACHABLE`. Previously a network outage produced an all-zero score, which fell below `score_threshold` and was reported as **HIGH** risk — indistinguishable from a genuinely opaque paper, and enough to trigger a quality-tier downgrade. **Callers must branch on `risk_level == TransparencyRisk.UNKNOWN` before acting on a score**, and can branch on [`unknown_reason`](#transparencyunknownreason) to tell an outage from a disabled analyzer. See [Unreachable-API guard](#unreachable-api-guard).

## Module layout

| Submodule | Contents | Public? |
|-----------|----------|---------|
| `analyzer` | `TransparencyAnalyzer`, scoring-weight constants | `TransparencyAnalyzer` only |
| `models` | `TransparencyRisk`, `TransparencyUnknownReason`, `TransparencySettings`, `TransparencyResult`, `calculate_risk_level()` | Yes — all five |

The list of six names below is the complete `bmlib.transparency.__all__`. Everything else in `analyzer` — the scoring weights, the detection patterns, and all analysis sub-steps — is either a module-level constant or underscore-private; import constants from the submodule if you need them:

```python
from bmlib.transparency.analyzer import MAX_TRANSPARENCY_SCORE, SCORE_TRIAL_REGISTERED
from bmlib.transparency.models import MEDIUM_RISK_SCORE_THRESHOLD
```

## Installation

```bash
pip install bmlib[transparency]
```

Requires `httpx` for HTTP requests to external APIs. The import is **lazy**: `httpx` is imported inside `analyze()`, so constructing a `TransparencyAnalyzer` never fails. A missing `httpx` raises `ImportError("httpx is required for transparency analysis. Install with: pip install bmlib[transparency]")` on the first `analyze()` call — unless `settings.enabled` is `False`, which short-circuits before the import, so a disabled analyzer runs without the extra installed.

## Imports

```python
from bmlib.transparency import (
    TransparencyAnalyzer,
    TransparencyResult,
    TransparencyRisk,
    TransparencySettings,
    TransparencyUnknownReason,
    calculate_risk_level,
)
```

---

## Enums

### `TransparencyRisk`

```python
class TransparencyRisk(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"
```

`UNKNOWN` is returned in exactly three cases: `settings.enabled` is `False`, neither `pmid` nor `doi` was supplied, and no external API was reachable. Each names itself in `risk_indicators` for humans and in [`unknown_reason`](#transparencyunknownreason) for code. `UNKNOWN` is never produced by [`calculate_risk_level()`](#calculate_risk_level), and it never triggers a quality-tier downgrade — a paper nothing was learned about is not penalised.

### `TransparencyUnknownReason`

```python
class TransparencyUnknownReason(Enum):
    DISABLED = "disabled"            # settings.enabled is False
    NO_IDENTIFIER = "no_identifier"  # neither PMID nor DOI was supplied
    UNREACHABLE = "unreachable"      # no external API answered
```

The machine-readable form of *why* a result is `UNKNOWN`, carried on [`TransparencyResult.unknown_reason`](#transparencyresult). The three causes want different handling — retry an outage, skip a disabled analyzer, fix a missing identifier — and before this enum existed the only way to tell them apart was matching the indicator prose, which is documentation rather than API.

```python
if result.unknown_reason is TransparencyUnknownReason.UNREACHABLE:
    retry_later(result.document_id)
elif result.unknown_reason is TransparencyUnknownReason.NO_IDENTIFIER:
    logger.warning("no identifier for %s", result.document_id)
# DISABLED: nothing to do.
```

**Invariant:** `unknown_reason` is set if and only if `risk_level is TransparencyRisk.UNKNOWN`. Every determinate result carries `None`.

`__post_init__` enforces one half of that: constructing a non-`UNKNOWN` result with a reason raises `ValueError`. The other half is deliberately not enforced — an `UNKNOWN` *without* a reason constructs fine, because that is what results persisted before this field existed load as, and rejecting them would turn an additive field into a breaking change.

---

## Data Models

### `TransparencySettings`

User-configurable thresholds and options for transparency analysis.

```python
@dataclass
class TransparencySettings:
    enabled: bool = True
    score_threshold: int = 40
    industry_funding_triggers_downgrade: bool = True
    missing_coi_triggers_downgrade: bool = True
    tier_downgrade_amount: int = 1
    filtering_enabled: bool = False
    max_concurrent_analyses: int = 3
    cache_results: bool = True
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | `bool` | `True` | Whether transparency analysis is enabled. |
| `score_threshold` | `int` | `40` | Scores below this are classified as HIGH risk. |
| `industry_funding_triggers_downgrade` | `bool` | `True` | Whether industry funding combined with restricted data triggers HIGH risk. |
| `missing_coi_triggers_downgrade` | `bool` | `True` | Whether an *explicitly absent* COI disclosure triggers HIGH risk. |
| `tier_downgrade_amount` | `int` | `1` | Number of quality tiers to downgrade for HIGH-risk papers. Written to `TransparencyResult.tier_downgrade_applied`. |
| `filtering_enabled` | `bool` | `False` | Whether to exclude HIGH-risk papers from results. |
| `max_concurrent_analyses` | `int` | `3` | Maximum concurrent analyses. |
| `cache_results` | `bool` | `True` | Whether to cache analysis results. |

> **Two groups, with different owners.**
> `TransparencyAnalyzer` honours `enabled`, `score_threshold`, `industry_funding_triggers_downgrade`, `missing_coi_triggers_downgrade`, and `tier_downgrade_amount`. `enabled=False` short-circuits `analyze()` before any HTTP — it returns `UNKNOWN` at score 0 with the indicator `"Transparency analysis disabled in settings"`, and does not even require `httpx` to be installed. (Before 0.4.0 `enabled` was ignored and analysis ran regardless.)
>
> `filtering_enabled`, `max_concurrent_analyses`, and `cache_results` are orchestration hints for the **calling application**: the library analyses one document per call and does no filtering, threading, or caching of its own. They live here so an application has one place to configure transparency behaviour.

---

### `TransparencyResult`

Result of a transparency analysis for a single document.

```python
@dataclass
class TransparencyResult:
    document_id: str
    transparency_score: int                    # 0–100
    risk_level: TransparencyRisk

    industry_funding_detected: bool = False
    industry_funding_confidence: float = 0.0
    data_availability_level: str = "unknown"
    coi_disclosed: bool | None = True
    trial_registered: bool = False
    trial_results_compliant: bool = False
    outcome_switching_detected: bool = False

    risk_indicators: list[str] = field(default_factory=list)
    tier_downgrade_applied: int = 0

    analyzed_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    analyzer_version: str = "1.0"
    full_text_analyzed: bool = False
    unknown_reason: TransparencyUnknownReason | None = None
```

> **`unknown_reason` is declared last on purpose.** Downstream projects construct this dataclass positionally, so placing it beside its logical neighbours (`risk_level`, `risk_indicators`) would shift every following argument by one with nothing raised anywhere. The same rule governs `Publication.pmcid` and `BaseAgent`'s `embedding_model`.

| Field | Type | Description |
|-------|------|-------------|
| `document_id` | `str` | Identifier for the document (echoed from the `analyze()` argument). |
| `transparency_score` | `int` | Overall transparency score, capped at `MAX_TRANSPARENCY_SCORE` (100). |
| `risk_level` | `TransparencyRisk` | Computed risk level. |
| `industry_funding_detected` | `bool` | Industry involvement found — via a CrossRef funder name, a PubMed grant agency, **or** the full-text COI statement. The name matcher behind the first two is measured against a labelled corpus; see [Industry Involvement Detection](#industry-involvement-detection). |
| `industry_funding_confidence` | `float` | `0.8` for a structured funder match (CrossRef funder or PubMed grant agency), `0.5` when inferred from COI text, `0.0` when nothing was found. The largest of those that fire. |
| `data_availability_level` | `str` | One of `"full_open"`, `"on_request"`, `"not_available"`, `"unknown"`. |
| `coi_disclosed` | `bool \| None` | **Tri-state** — see below. |
| `trial_registered` | `bool` | Whether *this paper's own* trial registration was found — in any registry PubMed records, not only ClinicalTrials.gov. |
| `trial_results_compliant` | `bool` | Whether the registered trial has posted results. `False` also covers "could not be checked" — see [Trial Registration Detection](#trial-registration-detection). |
| `outcome_switching_detected` | `bool` | Always `False` — no detection is implemented; the field is reserved. |
| `risk_indicators` | `list[str]` | Human-readable list of risk factors found. |
| `tier_downgrade_applied` | `int` | `settings.tier_downgrade_amount` when `risk_level` is HIGH, otherwise `0`. |
| `analyzed_at` | `datetime` | Timestamp of the analysis (UTC). |
| `analyzer_version` | `str` | Version of the analyzer heuristics (`"1.0"`), **not** the bmlib version. |
| `full_text_analyzed` | `bool` | Whether Europe PMC full text was retrieved and scanned, rather than only the abstract. |
| `unknown_reason` | `TransparencyUnknownReason \| None` | Why the result is `UNKNOWN`; `None` on every determinate result. |

#### Tri-state `coi_disclosed`

`coi_disclosed` distinguishes *absent* from *unknown*, because only the first is evidence of anything:

| Value | Meaning | Accompanying indicator |
|-------|---------|------------------------|
| `True` | A COI/disclosure statement was found. A statement that there is nothing to declare counts as disclosed. | *(none, or `"COI disclosure found in PubMed record"`)* |
| `False` | Full text **was** retrieved and scanned, it contains no COI statement, and PubMed carries none either. | `"No COI disclosure found in full text"` |
| `None` | Undeterminable — full text was unavailable, the abstract carried no COI signal, and PubMed carried no statement. | `"COI disclosure status unknown (full text unavailable)"` |

A statement is found by any of three routes:

1. **Structural** — a non-blank JATS-tagged COI container (`<fn fn-type="COI-statement">`, `<sec sec-type="conflict">`, a `<sec>` whose `<title>` names conflicts or competing interests) counts as a disclosure *regardless of its wording*. The tag is proof a statement exists, so a disclosure phrased in a way no cue phrase matches is still credited.
2. **Cue phrase** — the fallback for untagged text, scanning for `"conflict of interest"`, `"competing interest"`, `"no conflict"`, `"nothing to disclose"`, `"declare no"`, `"financial disclosure"`.
3. **PubMed `<CoiStatement>`** — publisher-supplied structured metadata, consulted whether or not full text was available. This is the route that reaches closed-access papers, where routes 1 and 2 have nothing to scan. The element's full text content is read, not just its leading text node: the MEDLINE DTD declares `CoiStatement` as `(%text;)*`, so a statement may open with inline markup (`<b>Conflict of interest:</b> …`) and would otherwise be read as blank.

A whitespace-only tagged section proves nothing and does not suppress the fallback, so an untagged disclosure elsewhere in the document is still found. `SCORE_COI_DISCLOSED` is credited exactly once no matter how many routes fire.

A PubMed statement can arrive *after* the full-text scan has already appended `"No COI disclosure found in full text"` or `"COI disclosure status unknown (full text unavailable)"`. Both lines would then contradict `coi_disclosed=True`, so they are removed and `"COI disclosure found in PubMed record"` is appended in their place — the indicator list never has to be reconciled against the field.

The converse does **not** hold: a PubMed record *without* `<CoiStatement>` leaves `coi_disclosed` alone. Absence there means the publisher supplied no statement to PubMed, not that the paper carries none, and demoting `None` to `False` would trigger the missing-COI downgrade on no evidence.

Only an explicit `False` triggers the missing-COI HIGH-risk rule. `None` does not, so a paper is never penalised merely because Europe PMC had no open-access full text for it. `coi_disclosed is False` therefore always implies `full_text_analyzed is True`.

Note that the dataclass **default** is `True`, as is the `from_dict()` fallback for a missing key — a hand-constructed `TransparencyResult` is optimistic unless you say otherwise. `analyze()` always sets the field explicitly.

#### Serialisation

| Method | Description |
|--------|-------------|
| `to_dict() -> dict[str, Any]` | Serialise to a JSON-safe dictionary. `risk_level` and `unknown_reason` become their `.value` strings (`unknown_reason` is `None` when unset); `analyzed_at` becomes an ISO 8601 string. |
| `from_dict(data: dict) -> TransparencyResult` | Deserialise. `document_id`, `transparency_score`, and `risk_level` are required keys; a missing or empty `analyzed_at` defaults to now; a missing or null `unknown_reason` defaults to `None`, so results persisted before the field existed still load. |

The round trip is lossless — every field, including `full_text_analyzed`, survives:

```python
restored = TransparencyResult.from_dict(result.to_dict())
assert restored == result
```

This matters because `full_text_analyzed` qualifies `coi_disclosed`: only when the full text was read does `coi_disclosed is False` mean "scanned and absent" rather than "undeterminable". Before 0.4.0 `to_dict()` dropped the flag, so a persisted `coi_disclosed=False` could not be interpreted.

---

## TransparencyAnalyzer

`TransparencyAnalyzer` has exactly one public method, [`analyze()`](#transparencyanalyzeranalyze). Every other member is underscore-private.

### Constructor

```python
class TransparencyAnalyzer:
    def __init__(
        self,
        email: str = "user@example.com",
        pubmed_api_key: str | None = None,
        settings: TransparencySettings | None = None,
    ) -> None
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `email` | `str` | `"user@example.com"` | Contact email for the `User-Agent` politeness header sent to every API, and for the `email` parameter NCBI asks E-utilities callers to send. |
| `pubmed_api_key` | `str \| None` | `None` | NCBI API key, sent with the PubMed request. See below. |
| `settings` | `TransparencySettings \| None` | `None` | Transparency settings. Defaults to `TransparencySettings()`. |

> **What the API key actually buys.**
> NCBI meters unkeyed E-utilities traffic at **3 requests/second per IP** and keyed traffic at **10 requests/second per key**. Passing the key moves the analyzer's PubMed request out of the per-IP bucket that the rest of your application's E-utilities traffic — `bmlib.publications.fetchers.pubmed` among it — is already competing for.
>
> It does **not** change bmlib's own pacing: the analyzer stays on the 350 ms interval it shares with the other APIs, which already satisfies the unkeyed limit. The benefit is entirely about which bucket your requests are counted against.
>
> The key is optional. Without it the PubMed step still runs.

> **Before this release `pubmed_api_key` was accepted and never read**, and no PubMed endpoint was called at all — the port from bmlibrarian dropped the client that used it. Code that has been passing a key all along starts benefiting from it with no change.

`TransparencyAnalyzer` is safe to share across threads (see [Rate Limiting and Concurrency](#rate-limiting-and-concurrency)), and sharing one instance is the recommended pattern: it gives you a single global rate limit rather than one per worker.

---

### `TransparencyAnalyzer.analyze`

```python
def analyze(
    self,
    document_id: str,
    *,
    pmid: str | None = None,
    doi: str | None = None,
) -> TransparencyResult
```

Run transparency analysis for a single document. At least one of `pmid` or `doi` must be provided.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `document_id` | `str` | *(required)* | An identifier for the document (echoed into the result). |
| `pmid` | `str \| None` | `None` | PubMed ID. |
| `doi` | `str \| None` | `None` | Digital Object Identifier. |

**Returns:** `TransparencyResult`

**Raises:** `ImportError` if `httpx` is not installed.

A DOI still unlocks more than a PMID — CrossRef and OpenAlex are queried **only** when `doi` is set, so a PMID-only analysis forgoes the 20 open-access and citation points — but the gap narrowed with the PubMed step: a PMID now reaches funder information (via `<GrantList>`), a COI statement, and trial registration without a DOI. Supply both where you have both.

The PubMed step does not need `pmid` to be supplied: when only a `doi` is given, the PMID is taken from the Europe PMC record already fetched, so it costs no extra request. Only when neither source yields a PMID is the step skipped.

Every network failure is swallowed and logged at `DEBUG` — a non-200 response, a timeout, or a connection error degrades the score rather than raising. Enable `logging.getLogger("bmlib.transparency.analyzer").setLevel(logging.DEBUG)` to see which calls failed.

**Example:**

```python
from bmlib.transparency import (
    TransparencyAnalyzer,
    TransparencyRisk,
    TransparencyUnknownReason,
)

analyzer = TransparencyAnalyzer(
    email="researcher@example.com",
    pubmed_api_key="...",  # optional; see the constructor notes
)

result = analyzer.analyze("doc-001", pmid="39142365", doi="10.1038/s41586-024-00001-0")

if result.risk_level is TransparencyRisk.UNKNOWN:
    print("Not determinable:", result.unknown_reason, result.risk_indicators)
else:
    print(f"Score: {result.transparency_score}/100  Risk: {result.risk_level.value}")
    print(f"Industry: {result.industry_funding_detected} "
          f"(confidence {result.industry_funding_confidence})")
    print(f"Data: {result.data_availability_level}  COI: {result.coi_disclosed}")
    print(f"Full text scanned: {result.full_text_analyzed}")
    for indicator in result.risk_indicators:
        print(" -", indicator)

# DOI-only and PMID-only both work; PMID-only skips CrossRef and OpenAlex.
result = analyzer.analyze("doc-002", doi="10.1038/s41586-024-00001-0")
result = analyzer.analyze("doc-003", pmid="39142365")

# Neither identifier: immediate UNKNOWN, no HTTP traffic.
empty = analyzer.analyze("doc-004")
assert empty.risk_level is TransparencyRisk.UNKNOWN
assert empty.unknown_reason is TransparencyUnknownReason.NO_IDENTIFIER
assert empty.risk_indicators == ["No PMID or DOI provided"]
```

---

## Analysis Pipeline

All requests go through one `httpx.Client` with a 15-second timeout and the header `User-Agent: bmlib/{version} (mailto:{email})`, where `{version}` is `bmlib.__version__`. Five endpoint families are queried, in this order:

| Step | API | Endpoint | Requires | Used for |
|------|-----|----------|----------|----------|
| 1 | CrossRef | `https://api.crossref.org/works/{doi}` | `doi` | Funder records; structured industry-funder detection. |
| 2 | Europe PMC search | `https://www.ebi.ac.uk/europepmc/webservices/rest/search` | `doi` or `pmid` | Record lookup by `DOI:"{doi}"` (preferred) or `EXT_ID:{pmid}`; abstract text; the PMID for step 4. |
| 3 | Europe PMC full text | `https://www.ebi.ac.uk/europepmc/webservices/rest/{source}/{ext_id}/fullTextXML` | step 2 record with `inEPMC == "Y"` | COI statement, industry-COI detection, data-availability statement. |
| 4 | PubMed E-utilities | `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi` (`db=pubmed&retmode=xml`) | `pmid`, or a PMID in the step-2 record | `<CoiStatement>`, `<DataBankList>` trial registrations *and* data depositions, `<GrantList>` funders. |
| 5 | OpenAlex | `https://api.openalex.org/works/doi:{doi}` | `doi` | Open-access status, citation count. |
| 6 | ClinicalTrials.gov v2 | `https://clinicaltrials.gov/api/v2/studies/{nct_id}` (`fields=hasResults`) | an NCT id from step 4, else one credited in step 2's abstract | Posted-results check. |

The step-2 search is issued **once** per document: the record is threaded into the PubMed and trial-registration steps rather than re-queried, halving Europe PMC traffic compared to earlier releases.

Step 3 is the difference between a real data-availability reading and a guess. COI and data-availability statements live in a paper's full text, never its abstract, so when `inEPMC != "Y"` (no open-access full text at Europe PMC) the analyzer falls back to scanning the abstract, `full_text_analyzed` stays `False`, and industry-COI detection does not run at all. Two signals are the exceptions, and both come from step 4's structured metadata whether or not full text was reachable: COI *disclosure*, and a data *deposition*.

Step 4's ordering is deliberate. It sits after Europe PMC so a DOI-only analysis can reuse the PMID from the record already fetched, and before ClinicalTrials.gov so a structured registry accession can feed the posted-results check. It costs one request at most, and none at all when no PMID is available. All four of its signals are publisher-supplied structured metadata, which is why they outrank the text heuristics elsewhere in the module. A PubMed record that is missing, unreachable, or unparsable yields no signals and changes nothing.

### Scoring components

Weights are module-level constants in `bmlib.transparency.analyzer`:

| Constant | Points | Awarded when |
|----------|--------|--------------|
| `SCORE_FUNDER_INFO` | 15 | CrossRef returned at least one funder record, **or** PubMed returned a non-empty `<GrantList>`. Awarded once, never twice. |
| `SCORE_COI_DISCLOSED` | 10 | A COI/disclosure statement was found, by any of the three routes. Awarded once. |
| `SCORE_DATA_FULL_OPEN` | 20 | `data_availability_level == "full_open"` — a PubMed deposition accession, or a repository named in the text. |
| `SCORE_DATA_ON_REQUEST` | 10 | `data_availability_level == "on_request"`. |
| `SCORE_OPEN_ACCESS` | 15 | OpenAlex reports `open_access.is_oa`. |
| `SCORE_CITED` | 5 | OpenAlex reports `cited_by_count > 0`. |
| `SCORE_TRIAL_REGISTERED` | 20 | This paper's own trial registration was credited — from PubMed's `<DataBankList>` or from the abstract heuristic. |
| `SCORE_RESULTS_POSTED` | 15 | One of the registered trials has posted results. Only reachable for a ClinicalTrials.gov registration. |
| **Maximum** | **100** | `MAX_TRANSPARENCY_SCORE`, applied as `min(score, 100)`. |

The two data-availability awards are mutually exclusive — enforced by the credit swap described under [How the two are merged](#how-the-two-are-merged), not merely by the levels being distinct — so the best attainable total is exactly 100: `15 + 10 + 20 + 15 + 5 + 20 + 15`. The `min()` cap is therefore defensive rather than load-bearing — but it is applied, so a future weight change cannot overflow the documented range.

Note that the score is a *transparency* measure, not a quality measure, and it is heavily influenced by article type: a non-trial paper can never earn the 35 trial-related points, and a paywalled paper forfeits the 15 open-access points and (via step 3) the data-availability points. Compare scores within an article class, not across.

**Other constants:**

| Constant | Value | Purpose |
|----------|-------|---------|
| `MAX_TRIAL_IDS_TO_CHECK` | `3` | Registered trials queried for posted results before giving up. |
| `EFETCH_URL` | E-utilities `efetch.fcgi` | PubMed record endpoint. |
| `EUTILS_TOOL_NAME` | `"bmlib"` | Sent as `tool`, with `email`, to identify the caller to NCBI. |
| `DEFAULT_INDUSTRY_CONFIDENCE` | `0.8` | Confidence for a structured funder match (CrossRef funder or PubMed grant agency). |
| `TEXT_INDUSTRY_CONFIDENCE` | `0.5` | Confidence for industry ties inferred from COI text. |
| `_MIN_REQUEST_INTERVAL_SECONDS` | `0.35` | Minimum gap between outgoing requests. |
| `_HTTP_TIMEOUT_SECONDS` | `15.0` | Per-request timeout. |
| `_REGISTRATION_CUE_WINDOW` | `60` | Characters either side of an NCT id scanned for registration language. |
| `_MAX_OWN_TRIAL_IDS` | `2` | More distinct NCT ids than this means a citation list, not a registration. |
| `_COI_FALLBACK_WINDOW` | `1000` | Characters after a COI cue scanned when no tagged COI section exists. |

---

## Industry Involvement Detection

`industry_funding_detected` is the OR of three independent signals, deliberately kept on separate vocabularies.

### Signal 1 — structured CrossRef funders (confidence 0.8)

Each CrossRef funder `name` is passed to `_is_industry_funder()`, the single predicate behind both structured funder sources. A hit is recorded through `_Analysis.note_industry_funder()`, which appends the indicator `f"Industry funder: {name}"` and sets `industry_funding_confidence` to `DEFAULT_INDUSTRY_CONFIDENCE` (0.8). It is applied **only** to funder names — short org strings, never running prose.

**The vocabulary is two kinds of thing, and merging them back into one is a bug.**

```python
# Substrings — these must reach inside longer words.
_INDUSTRY_STEMS = ("pharmaceutic", "therapeutics", "laboratories")

# Whole words — `\b(?:…)\b`, so "Inc" and "Inc." both match and "Lincoln" does not.
_INDUSTRY_WORDS = ("pharma", "biotech", "incorporated", "inc", "corp",
                   "limited", "ltd", "gmbh", "llc")
```

A stem has to match inside a longer word (`"pharmaceutic"` reaching `"Pharmaceuticals"`); a whole word must not, because a bare `"inc"` as a substring matches `"Lincoln"`, `"Vincent"` and `"province"`. Applying word boundaries uniformly — the obvious one-line reading of [issue #36](https://github.com/hherb/bmlib/issues/36) — would lose the stems. Applying substrings uniformly is what made `"Pfizer Inc"` a false negative in the first place, since `"inc."` needed its dot as a crude word-boundary substitute.

**Membership was measured, not chosen.** `industry_funding_detected` feeds a HIGH-risk rule and HIGH applies `tier_downgrade_amount`, so a false positive costs more than a false negative — which is why the change was calibrated against 833 real names sampled from both corpora by `scripts/sample_funder_names.py`, of which 417 are hand-labelled and committed as `tests/data/funder_names.json`:

| Matcher | Precision | Recall |
|---|---|---|
| Substring (before) | 0.400 | 0.176 |
| Split (now) | **0.917** | **0.324** |

The corpus overturned two intuitive members:

- **`"pharma"` scored 3 true positives against 5 false ones.** It reached `"Faculty of Pharmacy"`, `"Pharmacogenetics and Medicines Optimisation Network"` and `"Clinical Pharmacy"` — all academic. Narrowing it to `"pharmaceutic"` keeps every true positive and drops four of the five false ones; the bare word is retained separately for `"Novartis Pharma AG"`.
- **`"biotech"` scored 0 true positives against 4 false ones.** Its only hits were `"Department of Biotechnology, Ministry of Science and Technology"` and `"Biotechnology and Biological Sciences Research Council"`. *Biotechnology* names a **field**, not a company type, so public bodies use it freely. Only the bare word survives, which is the company form (`"Acme Biotech"`).

`"laboratories"` survived at 1 true positive (`"Dr. Reddy's Laboratories"`) and no false ones. The plural is load-bearing: the singular `"Key Laboratory"` is a Chinese state-lab form that appeared 8 times in the corpus. A residual risk the corpus happened not to contain is `"Sandia National Laboratories"`.

`"llc"`, `"incorporated"` and `"limited"` earned inclusion on 2, 1 and 1 true positives with no false ones. The spelled-out forms need their own entries — `\binc\b` demands a boundary that `"Incorporated"` denies.

Rejected, each for a recorded reason: `"co"` (4 TP / 1 FP — it collides with the English prefix, as in `"project co-sponsored by province and ministry"`); `"corporation"` (1 TP / 1 FP — US non-profits use it, e.g. `"Research Corporation for Science Advancement"`); `"plc"`, `"pty"`, `"ag"`, `"bv"`, `"nv"`, `"sa"` (no true positives in the corpus, so nothing earned). `"ab"` and `"labs"` passed the count but were excluded anyway, because both collide with tokens the corpus happens not to contain — province and country codes in strings that demonstrably carry locations, and national laboratories. That call costs two true positives, `"Roche Sweden AB"` and `"Tempus Labs"`.

> **The recall ceiling is bare brand names.** Most industry funders this misses are names with no legal suffix and no field word — `"Pfizer"`, `"Roche"`, `"AbbVie"`, `"Teva"`, `"Bristol Myers Squibb"`. No keyword list can reach them; that needs a company-name gazetteer, which is a different feature with its own false-positive profile. `tests/test_funder_matching.py` pins those as known misses so the 0.32 figure reads as a ceiling rather than an unnoticed defect.

### Signal 2 — PubMed grant agencies (confidence 0.8)

`<Grant><Agency>` names from the PubMed record go through the same `_is_industry_funder()` predicate, append the same `f"Industry funder: {name}"` indicator, and carry the same 0.8 confidence: a grant agency is structured publisher-supplied metadata, the same class of evidence as a CrossRef funder record.

In practice PubMed's `GrantList` is dominated by public funders, so this signal fires rarely. Its real value is the funder *information* it supplies for papers CrossRef cannot be asked about — a PMID-only analysis had no funder signal at all before it.

Agencies are deduplicated twice over, because PubMed emits one `<Grant>` element per *grant number*: an agency funding four grants on one paper appears four times in the XML. `_parse_pubmed_signals()` collapses those to one entry, and `_Analysis.note_industry_funder()` skips a line already present — so a funder CrossRef has already named is not repeated. **That dedup is now symmetric (unreleased):** CrossRef also lists one record per award, and its own repeats used to produce one indicator line each. One funder is one indicator line, however many awards and sources report it.

### Signal 3 — industry ties in the COI statement (confidence 0.5) — *new in 0.4.0*

A paper can be industry-entangled without an industry funder record: the relationships are disclosed in the COI statement instead. When (and only when) full text was retrieved, the analyzer extracts the COI region and looks for disclosed relationships, using a separate keyword list:

```python
_INDUSTRY_COI_KEYWORDS = ["employee of", "speaker fee", "consultant for", "advisory board"]
```

A hit sets `industry_funding_detected = True`, raises `industry_funding_confidence` to at least `TEXT_INDUSTRY_CONFIDENCE` (0.5), and appends the indicator `"Industry ties disclosed in COI statement"`. The two vocabularies are **not** interchangeable, and `_is_industry_funder()` is deliberately not applied here: the org suffixes of signals 1 and 2 match far too freely in running prose, while these phrases never occur in a funder or agency name. COI prose is a different corpus with different failure modes, and it was left untouched by the #36 recalibration for that reason.

The lower confidence is the honest label on a text heuristic. Where several signals fire, the highest (0.8) is kept.

Three guards keep the false-positive rate down.

**1. Scope — only the COI region is read.** `_extract_coi_text()` prefers JATS containers that hold the disclosure: `<fn fn-type="COI-statement">`, `<sec sec-type="conflict">`, `<notes notes-type="COI-statement">` (case- and quote-style-insensitive), plus untyped `<sec>` elements whose `<title>` names conflicts, competing interests, or disclosure. Only when that tagged text is blank does it fall back to a 1000-character window after each cue phrase in `_COI_PATTERNS` — a whitespace-only tagged section proves nothing, so an untagged disclosure elsewhere must still be findable. Either way, an author affiliation or a reference list mentioning a company is never read as a disclosure.

A known limitation of the fallback: a window is a fixed span, so on a short disclosure it can bleed past the end into whatever follows (acknowledgements, references). That is the accepted trade-off behind the moderate `TEXT_INDUSTRY_CONFIDENCE` given to text-derived signals.

**2. Negation — per sentence.** `_discloses_industry_ties()` splits the COI text on `.` and `;` and scores each sentence independently. A sentence counts only if it contains an industry phrase **and** no negation cue (`no`, `none`, `not`, `neither`, `nor`, `never`, `without`, `deny/denies/denied`). ICMJE-style disclosures routinely enumerate the relationship types they deny — "none of the authors served as a consultant for or received speaker fees from any company" — which whole-text substring matching read as four disclosures. Per-sentence scoring also means a genuine disclosure sitting next to a denial still counts.

**3. Non-industry context — blanked before matching.** Being an employee of a university, hospital, college, school, government, ministry, the NIH, or a public health body is a genuine disclosure but not an industry tie; likewise an *editorial*, *community*, *data safety*, or *safety* advisory board, or the advisory board of the journal. `_NON_INDUSTRY_CONTEXT_RE` replaces those spans with whitespace **before** keyword matching, so they neither trigger a sentence nor mask a real industry relationship disclosed in the same sentence.

The employer nouns are curated rather than generic on purpose: a catch-all like "institute" would excuse industry bodies such as the Novartis Institutes for BioMedical Research.

> **This is keyword matching, not entity recognition.**
> An unlisted non-industry employer — "employee of the World Bank" — still flags. Treat `industry_funding_detected` at confidence 0.5 as a prompt to look, not as a finding.

---

## Data Availability Detection

Two sources produce `data_availability_level`, and the structured one wins.

### PubMed `<DataBankList>` — a deposition accession

A `<DataBank>` whose name is in `_DATA_ARCHIVE_NAMES` — GENBANK, PDB, figshare, Dryad, GEO, BioProject, SRA, dbGaP, dbSNP, dbVar, PubChem-Substance, PubChem-BioAssay — sets the level to `full_open` and appends `"Data deposited in {name}"`, one line per distinct archive (matched case-insensitively, shown in the publisher's spelling). Three things worth knowing:

- **A closed-access paper can earn it.** The text scan below needs Europe PMC full text; a paywalled paper has none, so before this signal existed such a paper could only ever read `unknown`.
- **The accession numbers are not read.** The databank *name* is the publisher's assertion of deposition, and `<AccessionNumberList>` is optional in the MEDLINE DTD. Nothing fetches a deposition accession, so — unlike an NCT id, which is interpolated into a ClinicalTrials.gov URL — there is nothing for validation to protect.
- **It is an allowlist, not "everything that is not a registry".** That distinction is load-bearing, and `scripts/sample_databank_names.py` is what maintains it: it counts PubMed records per `DataBankName`, reads the literal spelling off the XML, and flags any candidate bmlib classifies as neither. Its candidates are NLM's published list, hand-copied into `NLM_DATABANK_NAMES`, plus whatever the two sets already hold — so it reports on names it has been told about, and a repository NLM adds after this release has to be added there before the script can see it.

  Reading the registry set's complement as archives would couple the two branches — a registry NLM adds later, or one misspelled in the set, would score 20 points of *open data*. It would also credit the ~9,000 records naming a database an author cannot deposit into (RefSeq, OMIM, SWISSPROT, PIR, GDB, the UniProt family, PubChem-Compound); an accession there cites a curated third-party record. A sequence the authors did submit reaches GENBANK, which is in the set, so excluding the derived databases costs no genuine deposition.

  An allowlist does go stale as NLM adds repositories — but it goes stale by *under*-crediting, which is the direction a transparency score should fail in. Same trade-off, same resolution as the [industry funder keywords](#industry-involvement-detection).

### Text scan — the fallback

`_DATA_PATTERNS` is an **ordered** dict scanned against the search text (full text when step 3 retrieved it, the abstract otherwise); the first hit wins and scanning stops.

| Pattern | Level | Points |
|---------|-------|--------|
| `"not available"` | `not_available` | 0 (adds the indicator `"Data explicitly not available"`) |
| `"zenodo"`, `"figshare"`, `"dryad"`, `"github"` | `full_open` | 20 |
| `"available upon request"`, `"upon reasonable request"` | `on_request` | 10 |
| *(no match)* | `unknown` | 0 |

**The order is load-bearing.** The negated form is tested first so that a statement like *"data are not available upon reasonable request"* resolves to `not_available` rather than matching `"upon reasonable request"` and being scored as if data sharing were offered. Preserve this ordering if you fork the dict — Python dicts iterate in insertion order, which is what the first-hit-wins loop relies on.

### How the two are merged

Both sources report through `_Analysis.note_data_availability()`, which keeps the level carrying the **strongest evidence of openness** — `full_open` > `on_request` > `not_available` / `restricted` / `not_stated` > `unknown` — whichever arrives first. Three properties follow, and each is a deliberate choice:

- **The credit is swapped, not added.** Superseding a level takes back the points already awarded for it, so a paper whose full text says "upon reasonable request" and whose PubMed record lists a GenBank accession scores 20 for data availability, not 30. This is what keeps the two awards mutually exclusive, and with them the maximum of exactly 100.
- **`unknown` never displaces a finding.** It is the absence of a finding, not a weaker one, so a step that read a text with no data-availability statement leaves the other step's finding standing.
- **`"Data explicitly not available"` is retracted when superseded.** A paper can withhold individual patient data and still have deposited its sequences; leaving the line in place would contradict `data_availability_level`. It is appended and removed through the same constant, `_INDICATOR_DATA_NOT_AVAILABLE`, for exactly that reason.

Two further levels, `"restricted"` and `"not_stated"`, are recognised by [`calculate_risk_level()`](#calculate_risk_level) and ranked alongside `not_available`, but are **never produced** by the analyzer. They exist for callers who compute `data_availability` themselves from a richer source and then call the risk function directly.

---

## Trial Registration Detection

Registration is established from two sources, and the structured one wins.

### PubMed `<DataBankList>` — preferred

When the PubMed record lists a trial-registry databank, that is the publisher asserting *this* paper's registration, so it is trusted directly and the abstract heuristic below is not consulted at all. `DataBankName` is matched case-insensitively against the registry names PubMed emits, taken from [NLM's databank-source list](https://www.nlm.nih.gov/bsd/medline_databank_source.html) — ClinicalTrials.gov, ISRCTN, EudraCT, ANZCTR, ChiCTR, CRiS, CTRI, DRKS, IRCT, JapicCTI, JMACCT, JPRN, jRCT, NTR, PACTR, ReBec, REPEC, RPCEC, SLCTR, TCTR, UMIN-CTR. A databank naming a public data archive instead is read by [Data Availability Detection](#data-availability-detection); the two sets are disjoint, and a name in neither is credited as neither.

Two consequences worth knowing:

- **Registration outside ClinicalTrials.gov now counts.** `trial_registered` is `True` and 20 points are awarded, but posted results cannot be looked up — ClinicalTrials.gov has no answer for an ISRCTN number. The result says so with `"Trial registration found; posted-results status could not be checked"` rather than the misleading `"Registered trial without posted results"`. `trial_results_compliant` is `False` in both cases, so read the indicator, not the flag, to tell "checked and absent" from "not checkable".
- **Accessions are validated before use.** Only a well-formed `NCT\d{8}` id is carried forward, because it is publisher-supplied text that would otherwise be interpolated into a ClinicalTrials.gov URL path unchecked. A ClinicalTrials.gov entry whose accession is missing or malformed still counts as a registration — it just falls into the not-checkable case above.

  This is why that indicator names the *consequence* and not the registry: it covers both a genuine non-ClinicalTrials.gov registration and a ClinicalTrials.gov one whose accession was unusable, and "registered outside ClinicalTrials.gov" would be simply false in the second case. The distinction is logged at `DEBUG`, not carried on the result — nothing scores differently on it.

### Abstract heuristic — the fallback

Used when PubMed has no `DataBankList` for the paper, or when no PMID was available at all. An NCT accession number in an abstract does not mean the paper *is* that trial — a systematic review enumerates the trials it pooled. `_find_trial_ids()` therefore credits a registration only under two conditions:

1. **Registration language nearby.** At least one NCT id must have a registration cue within ±60 characters (`_REGISTRATION_CUE_WINDOW`) on either side: `clinicaltrials.gov` (tolerating a missing dot), any `regist*` stem (register / registered / registration / registry), or a bare `NCT` used as a label rather than as part of an id. The cue may precede the id ("registered under NCT…") or follow it ("NCT…; registered at ClinicalTrials.gov").
2. **At most two distinct ids.** A paper's own registration cites one, occasionally two linked, trial numbers. Three or more distinct ids (`_MAX_OWN_TRIAL_IDS = 2`) is a citation list, and the function returns nothing.

Markup is stripped before scanning so tags cannot break the ±60-character window, and ids are deduplicated and upper-cased to ClinicalTrials.gov's canonical form. The patterns were calibrated against real Europe PMC abstracts: they credit ~97% of genuinely registered single-trial abstracts while rejecting citation lists of three or more distinct trials.

Neither guard applies to a `<DataBankList>` accession. They exist only because scraping ids out of prose cannot distinguish a paper's own registration from a citation list; a databank entry carries that distinction already.

### Posted results

When ClinicalTrials.gov ids are credited, `trial_registered` is `True` and 20 points are awarded. Up to `MAX_TRIAL_IDS_TO_CHECK` (3) of them are then queried for posted results; the first success awards 15 more and stops the loop. If none has results, the indicator `"Registered trial without posted results"` is appended.

`_check_trial_results()` requests `fields=hasResults` and reads the v2 API's top-level `hasResults` boolean. **This was a bug fix in 0.4.0:** the previous implementation requested a `ResultsSection` field but read a `resultsSection` key, so it systematically under-detected posted results and under-scored compliant trials by 15 points.

Because the request is narrowed to that one field, `hasResults` is the only key the response can carry. A missing key means the API did not answer the question and is reported as "no posted results". (A `resultsSection` fallback existed until 0.4.0 but was unreachable for exactly this reason, and was removed rather than left implying a robustness it did not provide.)

---

## Unreachable-API Guard

`analyze()` tracks whether any external API returned HTTP 200 during the run. The flag is set by the four record helpers — CrossRef, the Europe PMC search, PubMed, and OpenAlex — and is reset at the start of every `analyze()` call.

If nothing answered, the analyzer returns early, **before** scoring and before `calculate_risk_level()` is consulted:

```python
TransparencyResult(
    document_id=document_id,
    transparency_score=0,
    risk_level=TransparencyRisk.UNKNOWN,
    risk_indicators=["Transparency APIs unreachable — score not determinable"],
    unknown_reason=TransparencyUnknownReason.UNREACHABLE,
)
```

Every other field keeps its dataclass default, so `coi_disclosed` reads `True` and `tier_downgrade_applied` reads `0`. Branch on `risk_level` — do not read the other fields of an `UNKNOWN` result, apart from `unknown_reason`.

```python
result = analyzer.analyze("doc-001", doi="10.1038/...")

if result.risk_level is TransparencyRisk.UNKNOWN:
    retry_later(result.document_id)      # measured nothing; do not downgrade
elif result.risk_level is TransparencyRisk.HIGH:
    downgrade(result.document_id, result.tier_downgrade_applied)
```

**The guard is all-or-nothing, not per-API.** *Partial* reachability still scores: if CrossRef answers but Europe PMC is down, the run proceeds with whatever it measured, and the missing signals simply score zero. A DOI-only paper whose Europe PMC lookup fails can still land below `score_threshold` and be reported HIGH. The guard rules out the total-outage case — it does not certify that the score is complete. Use `full_text_analyzed` and the `risk_indicators` list to judge how much evidence a given score actually rests on.

The full-text fetch and the ClinicalTrials.gov query do not set the flag, which is harmless: neither is reached without a prior Europe PMC 200.

---

## Risk Level Calculation

### `calculate_risk_level`

```python
def calculate_risk_level(
    score: int,
    industry_funding: bool,
    data_availability: str,
    coi_disclosed: bool | None,
    settings: TransparencySettings,
) -> TransparencyRisk
```

Determine risk level from transparency metrics. Rules are evaluated in order; the first match wins.

| # | Condition | Gated by | Risk Level |
|---|-----------|----------|-----------|
| 1 | `score < settings.score_threshold` (default 40) | — | **HIGH** |
| 2 | Industry funding **and** `data_availability in ("restricted", "not_available", "not_stated")` | `industry_funding_triggers_downgrade` | **HIGH** |
| 3 | `coi_disclosed is False` | `missing_coi_triggers_downgrade` | **HIGH** |
| 4 | `score <= 70` (`MEDIUM_RISK_SCORE_THRESHOLD`) | — | **MEDIUM** |
| 5 | Industry funding present | — | **MEDIUM** |
| 6 | *(otherwise)* | — | **LOW** |

Two subtleties:

- **Rule 3 requires an explicit `False`.** `coi_disclosed is None` — full text unavailable, status undeterminable — does **not** downgrade. `is False` is an identity test, so `None` cannot slip through a truthiness check. This is the whole point of the tri-state: a paywalled paper is not punished for a COI statement nobody could read.
- **Rule 2's restricted set excludes `"unknown"`.** An industry-funded paper whose data-availability statement was never found is not HIGH on that rule; it will usually land MEDIUM via rule 5.

`calculate_risk_level()` never returns `UNKNOWN` — that value comes only from `analyze()`'s three early returns, which is why every `UNKNOWN` result carries an [`unknown_reason`](#transparencyunknownreason).

```python
from bmlib.transparency import TransparencySettings, TransparencyRisk, calculate_risk_level

settings = TransparencySettings()

# Undeterminable COI does not downgrade; absent COI does.
assert calculate_risk_level(85, False, "full_open", None, settings) is TransparencyRisk.LOW
assert calculate_risk_level(85, False, "full_open", False, settings) is TransparencyRisk.HIGH

# Industry funding caps an otherwise-transparent paper at MEDIUM.
assert calculate_risk_level(85, True, "full_open", True, settings) is TransparencyRisk.MEDIUM

# ...and makes it HIGH when the data are withheld.
assert calculate_risk_level(85, True, "not_available", True, settings) is TransparencyRisk.HIGH

# Opt out of the COI rule.
lenient = TransparencySettings(missing_coi_triggers_downgrade=False)
assert calculate_risk_level(85, False, "full_open", False, lenient) is TransparencyRisk.LOW
```

---

## Rate Limiting and Concurrency

The analyzer enforces a minimum interval of 350 ms (`_MIN_REQUEST_INTERVAL_SECONDS`) between outgoing HTTP requests by sleeping on the calling thread, ensuring polite access to public services. The clock is shared across all six endpoints, so a full DOI + PMID analysis costs roughly 2–2.5 s of enforced delay on top of network time.

That one shared clock is also why `pubmed_api_key` does not make bmlib faster: 350 ms already satisfies NCBI's unkeyed 3 requests/second limit, and the other four services have their own etiquette to respect. The key changes which NCBI bucket your requests are metered against, not how quickly this library issues them.

**`TransparencyAnalyzer` is safe to share across threads** (since 0.4.0), which is what makes `settings.max_concurrent_analyses` usable. Its two pieces of mutable state are handled differently, by design:

| State | Scope | Why |
|-------|-------|-----|
| `_last_request` (rate limiter) | Shared, mutex-guarded | The interval throttles a *shared remote API*, so it must apply across all threads. The lock is held across the sleep, so concurrent callers queue rather than all reading the same stale timestamp and firing at once. |
| reachability | Per-thread | It describes a *single analysis*. Held in `threading.local()` so one thread's success cannot make another thread's total outage look measured. |

Sharing one analyzer is therefore the recommended pattern, and it gives you a single global rate limit rather than N independent ones:

```python
from concurrent.futures import ThreadPoolExecutor

from bmlib.transparency import TransparencyAnalyzer, TransparencySettings

settings = TransparencySettings()
analyzer = TransparencyAnalyzer(email="researcher@example.com", settings=settings)

with ThreadPoolExecutor(max_workers=settings.max_concurrent_analyses) as pool:
    results = list(pool.map(lambda j: analyzer.analyze(j[0], doi=j[1]), jobs))
```

Because the rate limiter is shared, adding workers does **not** multiply your request rate — it overlaps network waits within one 350 ms budget. Set a real `email` so the services can contact you rather than block you.

> Before 0.4.0 both pieces of state were unsynchronised and the guidance here was to build one analyzer per worker. If you followed that, sharing a single instance is now both correct and better-behaved; per-worker instances still work but give each worker its own rate limit.

---

## Integration with Quality Assessment

`TransparencyResult` can be attached to a `QualityAssessment` via its `transparency_result` field (typed `Any`, so no import cycle). When `risk_level` is HIGH, the quality tier can be downgraded by `tier_downgrade_applied`, which the analyzer has already populated from `settings.tier_downgrade_amount`.

```python
from bmlib.quality import QualityAssessment, QualityTier
from bmlib.transparency import TransparencyAnalyzer, TransparencyRisk

assessment = manager.assess(title="...", abstract="...")

analyzer = TransparencyAnalyzer(email="researcher@example.com")
transparency = analyzer.analyze("doc-001", doi="10.1038/...")

assessment.transparency_result = transparency

# UNKNOWN means "not measured" — never downgrade on it.
if transparency.risk_level is TransparencyRisk.HIGH:
    assessment.original_quality_tier = assessment.quality_tier
    assessment.transparency_adjusted = True
    assessment.quality_tier = QualityTier(
        max(0, assessment.quality_tier.value - transparency.tier_downgrade_applied)
    )
```

Applying the downgrade is the caller's job — nothing in `bmlib.quality` reads `transparency_result`, and `QualityAssessment.to_dict()` omits both `transparency_result` and `original_quality_tier`. Persist the transparency result separately via `TransparencyResult.to_dict()` if you need it after a round-trip.

> **Guard the downgrade on `risk_level`, not on `transparency_score`.**
> Before 0.4.0, an unreachable API produced score 0 → HIGH → an automatic tier downgrade on evidence that was never gathered. The [unreachable-API guard](#unreachable-api-guard) now returns `UNKNOWN` in that case; code that tests `transparency_score < 40` instead of the risk level still has the old bug.
