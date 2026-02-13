# bmlib.quality — Quality Assessment Pipeline

Three-tier quality assessment pipeline for biomedical publications, inspired by the Oxford Centre for Evidence-Based Medicine (CEBM) evidence hierarchy. The pipeline escalates from free metadata checks to increasingly capable LLM assessments:

- **Tier 1:** PubMed metadata classification (free, instant)
- **Tier 2:** LLM study-design classification (cheap model, ~$0.001/doc)
- **Tier 3:** Deep methodological assessment (capable model, ~$0.003/doc)

## Imports

```python
from bmlib.quality import (
    QualityManager,
    QualityAssessment,
    QualityFilter,
    QualityTier,
    StudyDesign,
    BiasRisk,
    DESIGN_TO_TIER,
    DESIGN_TO_SCORE,
)
```

---

## Enums

### `StudyDesign`

Enumeration of biomedical study design types.

```python
class StudyDesign(Enum):
    SYSTEMATIC_REVIEW = "systematic_review"
    META_ANALYSIS = "meta_analysis"
    RCT = "rct"
    COHORT_PROSPECTIVE = "cohort_prospective"
    COHORT_RETROSPECTIVE = "cohort_retrospective"
    CASE_CONTROL = "case_control"
    CROSS_SECTIONAL = "cross_sectional"
    CASE_SERIES = "case_series"
    CASE_REPORT = "case_report"
    GUIDELINE = "guideline"
    EDITORIAL = "editorial"
    LETTER = "letter"
    COMMENT = "comment"
    OTHER = "other"
    UNKNOWN = "unknown"
```

---

### `QualityTier`

Evidence quality tier. Higher value = stronger evidence. Supports comparison operators (`<`, `>`, `<=`, `>=`).

```python
class QualityTier(Enum):
    UNCLASSIFIED = 0
    TIER_1_ANECDOTAL = 1       # case reports, editorials, letters
    TIER_2_OBSERVATIONAL = 2   # cross-sectional, case-control
    TIER_3_CONTROLLED = 3      # cohort studies
    TIER_4_EXPERIMENTAL = 4    # RCTs
    TIER_5_SYNTHESIS = 5       # systematic reviews, meta-analyses
```

**Example:**

```python
assert QualityTier.TIER_4_EXPERIMENTAL > QualityTier.TIER_2_OBSERVATIONAL
assert QualityTier.TIER_5_SYNTHESIS >= QualityTier.TIER_4_EXPERIMENTAL
```

---

## Mappings

### `DESIGN_TO_TIER`

Maps `StudyDesign` to `QualityTier`:

| Study Design | Quality Tier |
|-------------|-------------|
| `SYSTEMATIC_REVIEW`, `META_ANALYSIS`, `GUIDELINE` | `TIER_5_SYNTHESIS` |
| `RCT` | `TIER_4_EXPERIMENTAL` |
| `COHORT_PROSPECTIVE`, `COHORT_RETROSPECTIVE` | `TIER_3_CONTROLLED` |
| `CASE_CONTROL`, `CROSS_SECTIONAL` | `TIER_2_OBSERVATIONAL` |
| `CASE_SERIES`, `CASE_REPORT`, `EDITORIAL`, `LETTER`, `COMMENT` | `TIER_1_ANECDOTAL` |
| `OTHER`, `UNKNOWN` | `UNCLASSIFIED` |

### `DESIGN_TO_SCORE`

Maps `StudyDesign` to default numeric scores (0–10):

| Study Design | Default Score |
|-------------|--------------|
| Systematic review / Meta-analysis | 9.0 |
| Guideline | 8.5 |
| RCT | 8.0 |
| Prospective cohort | 6.0 |
| Retrospective cohort | 5.0 |
| Case-control | 4.5 |
| Cross-sectional | 4.0 |
| Case series | 3.0 |
| Case report | 2.0 |
| Editorial / Letter | 1.5 |
| Comment | 1.0 |
| Other / Unknown | 0.0 |

---

## Data Models

### `BiasRisk`

Cochrane Risk-of-Bias assessment across five domains.

```python
@dataclass
class BiasRisk:
    selection: str = "unclear"     # "low", "unclear", or "high"
    performance: str = "unclear"
    detection: str = "unclear"
    attrition: str = "unclear"
    reporting: str = "unclear"
```

| Method | Description |
|--------|-------------|
| `to_dict() -> dict[str, str]` | Serialise to dictionary. |
| `from_dict(data: dict) -> BiasRisk` | Deserialise from dictionary. Invalid values default to `"unclear"`. |

---

### `QualityAssessment`

Result from any tier of the quality pipeline.

```python
@dataclass
class QualityAssessment:
    assessment_tier: int = 0             # 0=unclassified, 1=metadata, 2=classifier, 3=deep
    extraction_method: str = "none"
    study_design: StudyDesign = StudyDesign.UNKNOWN
    quality_tier: QualityTier = QualityTier.UNCLASSIFIED
    quality_score: float = 0.0           # 0–10
    evidence_level: str | None = None    # Oxford CEBM level (1a, 1b, 2a, ..., 5)
    is_randomized: bool | None = None
    is_controlled: bool | None = None
    is_blinded: str | None = None        # none / single / double / triple
    is_prospective: bool | None = None
    is_multicenter: bool | None = None
    sample_size: int | None = None
    confidence: float = 0.0              # 0–1
    bias_risk: BiasRisk | None = None
    strengths: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    extraction_details: list[str] = field(default_factory=list)
    transparency_result: Any = None
    original_quality_tier: QualityTier | None = None
    transparency_adjusted: bool = False
```

#### Factory Methods

| Method | Description |
|--------|-------------|
| `QualityAssessment.unclassified()` | Create an empty/unclassified assessment. |
| `QualityAssessment.from_metadata(design, confidence=0.9)` | Create a Tier 1 assessment from PubMed metadata. |
| `QualityAssessment.from_classification(study_design, confidence=0.7, sample_size=None, is_blinded=None)` | Create a Tier 2 assessment from LLM classification. |

#### Instance Methods

| Method | Description |
|--------|-------------|
| `passes_filter(qfilter: QualityFilter) -> bool` | Check if this assessment passes the given filter criteria. |
| `to_dict() -> dict[str, Any]` | Serialise to a JSON-safe dictionary. |
| `from_dict(data: dict) -> QualityAssessment` | Deserialise from a dictionary. |

---

### `QualityFilter`

User-configurable filter thresholds for controlling which tiers are enabled and what passes.

```python
@dataclass
class QualityFilter:
    min_tier: QualityTier | None = None
    require_randomization: bool = False
    require_blinding: bool = False
    min_sample_size: int | None = None
    use_metadata_only: bool = False
    use_llm_classification: bool = True
    use_detailed_assessment: bool = False
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `min_tier` | `QualityTier \| None` | `None` | Minimum quality tier to pass. |
| `require_randomization` | `bool` | `False` | Require `is_randomized=True` to pass. |
| `require_blinding` | `bool` | `False` | Require blinding (not `None` or `"none"`) to pass. |
| `min_sample_size` | `int \| None` | `None` | Minimum sample size to pass. |
| `use_metadata_only` | `bool` | `False` | Stop at Tier 1 (metadata only). |
| `use_llm_classification` | `bool` | `True` | Enable Tier 2 (LLM classifier). |
| `use_detailed_assessment` | `bool` | `False` | Enable Tier 3 (deep assessment). |

**Example:**

```python
# Only show RCTs and higher
strict_filter = QualityFilter(
    min_tier=QualityTier.TIER_4_EXPERIMENTAL,
    require_randomization=True,
    require_blinding=True,
    min_sample_size=50,
    use_llm_classification=True,
    use_detailed_assessment=True,
)

if assessment.passes_filter(strict_filter):
    print("High-quality evidence")
```

---

## QualityManager

Orchestrates the tiered assessment pipeline.

### Constructor

```python
class QualityManager:
    def __init__(
        self,
        llm: LLMClient,
        classifier_model: str,
        assessor_model: str,
        template_engine: TemplateEngine | None = None,
    ) -> None
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `llm` | `LLMClient` | *(required)* | LLM client for Tier 2 and Tier 3 assessments. |
| `classifier_model` | `str` | *(required)* | Model string for Tier 2 classification (cheap/fast, e.g. `"anthropic:claude-3-haiku-20240307"`). |
| `assessor_model` | `str` | *(required)* | Model string for Tier 3 deep assessment (capable, e.g. `"anthropic:claude-sonnet-4-20250514"`). |
| `template_engine` | `TemplateEngine \| None` | `None` | Optional template engine for custom prompts. |

---

### `QualityManager.assess`

```python
def assess(
    self,
    title: str,
    abstract: str,
    *,
    publication_types: Sequence[str] = (),
    filter_settings: QualityFilter | None = None,
) -> QualityAssessment
```

Run the tiered assessment pipeline for a single paper.

**Assessment flow:**

1. **Tier 1 (metadata):** If `publication_types` are provided and match a known PubMed type, classify immediately. Free and instant.
2. **Tier 2 (classifier):** If metadata is inconclusive or low-confidence, use a cheap LLM to classify the study design from title + abstract.
3. **Tier 3 (deep assessment):** If `filter_settings.use_detailed_assessment` is `True`, perform comprehensive methodological assessment including bias risk, strengths, and limitations.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `title` | `str` | *(required)* | Paper title. |
| `abstract` | `str` | *(required)* | Paper abstract. |
| `publication_types` | `Sequence[str]` | `()` | PubMed publication type strings (e.g. `["Randomized Controlled Trial"]`). |
| `filter_settings` | `QualityFilter \| None` | `None` | Controls which tiers are enabled. Defaults to `QualityFilter()`. |

**Returns:** `QualityAssessment`

**Example:**

```python
from bmlib.llm import LLMClient
from bmlib.quality import QualityManager, QualityFilter

llm = LLMClient()
manager = QualityManager(
    llm=llm,
    classifier_model="anthropic:claude-3-haiku-20240307",
    assessor_model="anthropic:claude-sonnet-4-20250514",
)

# Tier 1 only (instant, free)
result = manager.assess(
    title="...",
    abstract="...",
    publication_types=["Randomized Controlled Trial"],
    filter_settings=QualityFilter(use_metadata_only=True),
)

# Tier 1 + Tier 2 (default)
result = manager.assess(
    title="...",
    abstract="...",
)

# Full pipeline (Tier 1 + 2 + 3)
result = manager.assess(
    title="...",
    abstract="...",
    filter_settings=QualityFilter(use_detailed_assessment=True),
)
```

---

### `QualityManager.assess_batch`

```python
def assess_batch(
    self,
    papers: list[dict],
    *,
    filter_settings: QualityFilter | None = None,
    progress_callback: Callable[[int, int, QualityAssessment], None] | None = None,
) -> list[QualityAssessment]
```

Assess a batch of papers. Each dict in `papers` should have `"title"` and `"abstract"` keys, and optionally `"publication_types"`.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `papers` | `list[dict]` | *(required)* | List of paper dicts. |
| `filter_settings` | `QualityFilter \| None` | `None` | Controls which tiers are enabled. |
| `progress_callback` | `Callable \| None` | `None` | Called with `(current_index, total, assessment)` after each paper. |

**Returns:** `list[QualityAssessment]` in the same order as input.

**Example:**

```python
papers = [
    {"title": "Paper A", "abstract": "...", "publication_types": ["Meta-Analysis"]},
    {"title": "Paper B", "abstract": "..."},
    {"title": "Paper C", "abstract": "...", "publication_types": ["Case Reports"]},
]

results = manager.assess_batch(
    papers,
    filter_settings=QualityFilter(use_llm_classification=True),
    progress_callback=lambda i, total, a: print(f"  [{i}/{total}] {a.study_design.value}"),
)
```

---

## Tier 1: Metadata Classification

The function `classify_from_metadata` is used internally by `QualityManager` but is also available for direct use:

```python
from bmlib.quality.metadata_filter import classify_from_metadata

result = classify_from_metadata(["Randomized Controlled Trial", "Multicenter Study"])
print(result.study_design)  # StudyDesign.RCT
print(result.quality_tier)  # QualityTier.TIER_4_EXPERIMENTAL
print(result.confidence)    # 0.9
```

### Supported PubMed Publication Types

The following PubMed publication types are mapped to study designs (resolved in priority order):

| PubMed Publication Type | Study Design |
|------------------------|--------------|
| Systematic Review | `SYSTEMATIC_REVIEW` |
| Meta-Analysis | `META_ANALYSIS` |
| Randomized Controlled Trial, Clinical Trial (Phase I–IV), Controlled Clinical Trial, Pragmatic Clinical Trial | `RCT` |
| Observational Study, Cohort Study, Longitudinal Study, Prospective Study | `COHORT_PROSPECTIVE` |
| Retrospective Study | `COHORT_RETROSPECTIVE` |
| Case-Control Study | `CASE_CONTROL` |
| Cross-Sectional Study, Twin Study, Validation Study, Comparative Study | `CROSS_SECTIONAL` |
| Case Reports | `CASE_REPORT` |
| Practice Guideline, Guideline, Consensus Development Conference | `GUIDELINE` |
| Editorial | `EDITORIAL` |
| Letter | `LETTER` |
| Comment | `COMMENT` |

---

## Tier 2: Study Classifier

The `StudyClassifier` (subclass of `BaseAgent`) uses a cheap/fast LLM to classify study design from title + abstract. It returns structured JSON with:

- `study_design`: One of the `StudyDesign` enum values
- `confidence`: 0.0–1.0
- `sample_size`: Integer or null
- `blinding`: `"none"`, `"single"`, `"double"`, `"triple"`, or null

The classifier focuses on the paper's own methodology (e.g. "we conducted", "this study") and ignores referenced studies (e.g. "a previous meta-analysis found").

---

## Tier 3: Deep Assessment

The `QualityAgent` (subclass of `BaseAgent`) uses a capable LLM for comprehensive assessment including:

- Study design classification
- Quality score (1–10)
- Oxford CEBM evidence level
- Design characteristics (randomized, controlled, blinded, prospective, multicenter)
- Sample size
- Cochrane Risk-of-Bias across 5 domains
- Methodological strengths and limitations
- Confidence score

This tier is the most expensive and should be used selectively.
