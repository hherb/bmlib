# bmlib.transparency — Transparency Analysis

Multi-API transparency analyzer for biomedical publications. Queries external APIs to assess funding sources, data availability, conflict-of-interest disclosure, and clinical trial registration compliance.

## Installation

```bash
pip install bmlib[transparency]
```

Requires `httpx` for HTTP requests to external APIs.

## Imports

```python
from bmlib.transparency import (
    TransparencyAnalyzer,
    TransparencyResult,
    TransparencyRisk,
    TransparencySettings,
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
| `missing_coi_triggers_downgrade` | `bool` | `True` | Whether missing COI disclosure triggers HIGH risk. |
| `tier_downgrade_amount` | `int` | `1` | Number of quality tiers to downgrade for HIGH-risk papers. |
| `filtering_enabled` | `bool` | `False` | Whether to exclude HIGH-risk papers from results. |
| `max_concurrent_analyses` | `int` | `3` | Maximum concurrent analyses. |
| `cache_results` | `bool` | `True` | Whether to cache analysis results. |

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
    coi_disclosed: bool = True
    trial_registered: bool = False
    trial_results_compliant: bool = False
    outcome_switching_detected: bool = False

    risk_indicators: list[str] = field(default_factory=list)
    tier_downgrade_applied: int = 0

    analyzed_at: datetime = field(default_factory=...)
    analyzer_version: str = "1.0"
    full_text_analyzed: bool = False
```

| Field | Type | Description |
|-------|------|-------------|
| `document_id` | `str` | Identifier for the document. |
| `transparency_score` | `int` | Overall transparency score (0–100). |
| `risk_level` | `TransparencyRisk` | Computed risk level. |
| `industry_funding_detected` | `bool` | Whether industry/pharma funding was found. |
| `industry_funding_confidence` | `float` | Confidence in the industry funding detection (0–1). |
| `data_availability_level` | `str` | One of: `"full_open"`, `"on_request"`, `"not_available"`, `"not_stated"`, `"unknown"`. |
| `coi_disclosed` | `bool` | Whether a conflict-of-interest statement was found. |
| `trial_registered` | `bool` | Whether a linked clinical trial registration was found. |
| `trial_results_compliant` | `bool` | Whether the registered trial has posted results. |
| `outcome_switching_detected` | `bool` | Whether outcome switching was detected. |
| `risk_indicators` | `list[str]` | Human-readable list of risk factors found. |
| `tier_downgrade_applied` | `int` | Number of quality tiers downgraded (0 if no downgrade). |
| `analyzed_at` | `datetime` | Timestamp of the analysis (UTC). |
| `analyzer_version` | `str` | Version of the analyzer. |

#### Serialisation

| Method | Description |
|--------|-------------|
| `to_dict() -> dict[str, Any]` | Serialise to a JSON-safe dictionary. |
| `from_dict(data: dict) -> TransparencyResult` | Deserialise from a dictionary. |

---

## TransparencyAnalyzer

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
| `email` | `str` | `"user@example.com"` | Contact email for API politeness headers (required by CrossRef, OpenAlex). |
| `pubmed_api_key` | `str \| None` | `None` | Optional NCBI API key for higher PubMed rate limits. |
| `settings` | `TransparencySettings \| None` | `None` | Transparency settings. Defaults to `TransparencySettings()`. |

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
| `document_id` | `str` | *(required)* | An identifier for the document (used in the result). |
| `pmid` | `str \| None` | `None` | PubMed ID. |
| `doi` | `str \| None` | `None` | Digital Object Identifier. |

**Returns:** `TransparencyResult`

**Example:**

```python
analyzer = TransparencyAnalyzer(email="researcher@example.com")

# Analyze by DOI
result = analyzer.analyze("doc-001", doi="10.1038/s41586-024-00001-0")
print(f"Score: {result.transparency_score}/100")
print(f"Risk: {result.risk_level.value}")
print(f"Industry funding: {result.industry_funding_detected}")
print(f"Data availability: {result.data_availability_level}")
print(f"Risk indicators: {result.risk_indicators}")

# Analyze by PMID
result = analyzer.analyze("doc-002", pmid="39142365")

# Analyze by both (more complete analysis)
result = analyzer.analyze("doc-003", pmid="39142365", doi="10.1038/s41586-024-00001-0")
```

---

## Analysis Pipeline

The analyzer queries four external APIs in sequence:

### 1. CrossRef (Funder Information)

Queries `api.crossref.org/works/{doi}` to extract funder information. Industry funders are detected via keyword matching against known patterns (pharma, biotech, therapeutics, inc., corp., etc.).

**Scoring:** +15 points if funder information is present.

### 2. EuropePMC (Abstract, COI, Data Availability)

Queries the EuropePMC search API for abstract text, then scans for:

- **COI disclosure:** Looks for patterns like "conflict of interest", "competing interest", "no conflict", "nothing to disclose", "financial disclosure".
- **Data availability:** Detects repository mentions (Zenodo, Figshare, Dryad, GitHub → `full_open`), "upon request" language → `on_request`, or "not available" → `not_available`.

**Scoring:** +10 for COI disclosure, +20 for full open data, +10 for data on request.

### 3. OpenAlex (Open Access, Citations)

Queries `api.openalex.org/works/doi:{doi}` for open access status and citation count.

**Scoring:** +15 for open access, +5 if cited.

### 4. ClinicalTrials.gov (Trial Registration)

Searches for NCT IDs in the abstract, then queries `clinicaltrials.gov/api/v2/studies/{nct_id}` to check if results have been posted.

**Scoring:** +20 for trial registration, +15 for results posted.

### Maximum Score: 100

---

## Risk Level Calculation

### `calculate_risk_level`

```python
def calculate_risk_level(
    score: int,
    industry_funding: bool,
    data_availability: str,
    coi_disclosed: bool,
    settings: TransparencySettings,
) -> TransparencyRisk
```

Determine risk level from transparency metrics.

**Risk classification rules:**

| Condition | Risk Level |
|-----------|-----------|
| `score < settings.score_threshold` (default 40) | **HIGH** |
| Industry funding + restricted/unavailable data | **HIGH** |
| Missing COI disclosure | **HIGH** |
| `score <= 70` | **MEDIUM** |
| Industry funding present (but data available) | **MEDIUM** |
| `score > 70` and transparent | **LOW** |

---

## Rate Limiting

The analyzer enforces a minimum interval of 350ms between HTTP requests to external APIs, ensuring polite access to public services.

## Integration with Quality Assessment

`TransparencyResult` can be attached to a `QualityAssessment` via its `transparency_result` field. When `risk_level` is HIGH, the quality tier can be downgraded by `settings.tier_downgrade_amount` (default: 1 tier).

```python
from bmlib.quality import QualityAssessment
from bmlib.transparency import TransparencyAnalyzer, TransparencyRisk

# Perform quality assessment
assessment = manager.assess(title="...", abstract="...")

# Perform transparency analysis
analyzer = TransparencyAnalyzer(email="researcher@example.com")
transparency = analyzer.analyze("doc-001", doi="10.1038/...")

# Integrate results
assessment.transparency_result = transparency
if transparency.risk_level == TransparencyRisk.HIGH:
    assessment.original_quality_tier = assessment.quality_tier
    assessment.transparency_adjusted = True
    # Downgrade tier...
```
