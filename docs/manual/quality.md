# bmlib.quality — Quality Assessment Pipeline

Three-tier quality assessment pipeline for biomedical publications, inspired by the Oxford Centre for Evidence-Based Medicine (CEBM) evidence hierarchy. The pipeline escalates from free metadata checks to increasingly capable LLM assessments:

- **Tier 1:** PubMed metadata classification (free, instant)
- **Tier 2:** LLM study-design classification (cheap model, ~$0.001/doc)
- **Tier 3:** Deep methodological assessment (capable model, ~$0.003/doc)

Beyond the tiered pipeline, the module also provides Cochrane-aligned nine-domain Risk-of-Bias models with Markdown/HTML formatters, rule-based (LLM-free) extractors, and audit-trail scoring models.

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
    # Cochrane-aligned models
    CochraneInterventions,
    CochraneNotes,
    CochraneOutcomes,
    CochraneParticipants,
    CochraneRiskOfBias,
    CochraneStudyAssessment,
    CochraneStudyCharacteristics,
    RiskOfBiasItem,
    RiskOfBiasJudgement,
    create_default_cochrane_risk_of_bias,
    create_default_risk_of_bias_item,
    # Rule-based extractors + audit-trail scoring models
    AssessmentDetail,
    DimensionScore,
    extract_sample_size_dimension,
    extract_study_type,
)
```

The Cochrane Markdown/HTML formatters are not re-exported at package level; import them from `bmlib.quality.cochrane_formatter` (see [Cochrane Formatters](#cochrane-formatters)).

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

Cochrane Risk-of-Bias assessment across five domains. For the nine-domain variant with per-domain judgement and rationale, see [`CochraneRiskOfBias`](#cochraneriskofbias) below — a strict superset of this model.

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

1. **Tier 1 (metadata):** Always runs first. If `filter_settings.use_metadata_only` is `True`, its result is returned unconditionally. If the metadata result is confident (confidence ≥ 0.9 and classified) and no detailed assessment was requested, it is returned without any LLM call.
2. **Tier 3 (deep assessment):** If `filter_settings.use_detailed_assessment` is `True`, perform comprehensive methodological assessment including bias risk, strengths, and limitations. This *supersedes* Tier 2 — the classifier is skipped entirely.
3. **Tier 2 (classifier):** Otherwise, if `filter_settings.use_llm_classification` is `True`, use a cheap LLM to classify the study design from title + abstract. If Tier 2 is also disabled, the Tier 1 result is returned as fallback.

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

# Tier 3 deep assessment (supersedes Tier 2 — the classifier is skipped)
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

Matching is case-insensitive and normalizes hyphens/underscores (`"systematic review"`, `"Systematic Review"`, and `"systematic-review"` all match). Types are resolved in priority order (most specific first); known types outside the priority list match at reduced confidence (0.72 instead of 0.9). If nothing matches, `QualityAssessment.unclassified()` is returned.

### Supported PubMed Publication Types

The following PubMed publication types are mapped to study designs (resolved in priority order):

| PubMed Publication Type | Study Design |
|------------------------|--------------|
| Systematic Review | `SYSTEMATIC_REVIEW` |
| Meta-Analysis | `META_ANALYSIS` |
| Randomized Controlled Trial, Clinical Trial (Phase I–IV), Controlled Clinical Trial, Pragmatic Clinical Trial, Equivalence Trial | `RCT` |
| Cohort Study, Longitudinal Study, Prospective Study | `COHORT_PROSPECTIVE` |
| Retrospective Study | `COHORT_RETROSPECTIVE` |
| Case-Control Study | `CASE_CONTROL` |
| Cross-Sectional Study, Twin Study, Validation Study | `CROSS_SECTIONAL` |
| Case Reports | `CASE_REPORT` |
| Practice Guideline, Guideline, Consensus Development Conference | `GUIDELINE` |
| Editorial | `EDITORIAL` |
| Letter | `LETTER` |
| Comment | `COMMENT` |
| Review, Published Erratum, Retracted Publication | `OTHER` |

**Deliberately unmapped:** `"Multicenter Study"`, `"Comparative Study"`, and `"Observational Study"` are *not* mapped. The first two are organisational/generic attributes, not designs; `"Observational Study"` is PubMed's catch-all for non-experimental studies whose specific subtype was not indexed. Records carrying only such tags fall through to LLM classification instead.

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

---

## Cochrane Risk-of-Bias Models

Cochrane-aligned models (`bmlib.quality.cochrane_models`) matching the Cochrane Handbook template for systematic reviews: a study-characteristics table (Methods, Participants, Interventions, Outcomes, Notes) plus a nine-domain Risk-of-Bias assessment with a judgement and rationale per domain. A strict superset of `BiasRisk`: where `BiasRisk` records five domains as bare strings, these capture nine domains with supporting text. All dataclasses provide `to_dict()` / `from_dict()`.

### `RiskOfBiasJudgement`

Cochrane Risk of Bias judgement categories.

```python
class RiskOfBiasJudgement(Enum):
    LOW = "Low risk"
    HIGH = "High risk"
    UNCLEAR = "Unclear risk"
```

| Method | Description |
|--------|-------------|
| `RiskOfBiasJudgement.from_string(value: str) -> RiskOfBiasJudgement` | Tolerant conversion (`"low"`, `"High risk"`, `"unclear_risk"`, ...). Unknown values fall back to `UNCLEAR` with a warning. |

---

### `RiskOfBiasItem`

A single risk-of-bias domain assessment.

```python
@dataclass
class RiskOfBiasItem:
    domain: str                      # e.g. "Random sequence generation"
    bias_type: str                   # e.g. "selection bias"
    judgement: str                   # "Low risk", "High risk", or "Unclear risk"
    support_for_judgement: str       # text explaining the basis for the judgement
    outcome_type: str | None = None  # detection bias only: "subjective" / "objective"
```

An invalid `judgement` logs a warning (it does not raise). `to_dict()` omits `outcome_type` when unset.

---

### `CochraneRiskOfBias`

Complete Risk-of-Bias assessment across the nine standard Cochrane domains, each a `RiskOfBiasItem`.

```python
@dataclass
class CochraneRiskOfBias:
    random_sequence_generation: RiskOfBiasItem              # selection bias
    allocation_concealment: RiskOfBiasItem                  # selection bias
    baseline_outcome_measurements: RiskOfBiasItem           # selection bias
    baseline_characteristics: RiskOfBiasItem                # selection bias
    blinding_participants_personnel: RiskOfBiasItem         # performance bias
    blinding_outcome_assessment_subjective: RiskOfBiasItem  # detection bias
    blinding_outcome_assessment_objective: RiskOfBiasItem   # detection bias
    incomplete_outcome_data: RiskOfBiasItem                 # attrition bias
    selective_reporting: RiskOfBiasItem                     # reporting bias
```

| Method | Description |
|--------|-------------|
| `to_list() -> list[RiskOfBiasItem]` | The nine domains in canonical Cochrane table order. |
| `get_summary_counts() -> dict[str, int]` | Count of domains per judgement (`"Low risk"` / `"High risk"` / `"Unclear risk"`). |
| `to_dict() -> dict[str, Any]` | Serialise all nine domains. |
| `from_dict(data: dict) -> CochraneRiskOfBias` | Deserialise from a dictionary. |

---

### Study Characteristics Sections

Sections of the Cochrane study-characteristics table. All provide `to_dict()` / `from_dict()`; `from_dict()` defaults missing required text fields to `"Not reported"`.

```python
@dataclass
class CochraneParticipants:
    setting: str
    population: str
    inclusion_criteria: list[str] | None = None
    exclusion_criteria: list[str] | None = None
    total_participants: int | None = None
    group_sizes: dict[str, int] | None = None
    baseline_characteristics_reported: bool = False

@dataclass
class CochraneInterventions:
    description: str
    intervention_groups: list[str] | None = None
    control_description: str | None = None
    duration: str | None = None
    setting: str | None = None

@dataclass
class CochraneOutcomes:
    description: str
    primary_outcomes: list[str] | None = None
    secondary_outcomes: list[str] | None = None
    outcome_timepoints: list[str] | None = None
    outcome_assessment_methods: list[str] | None = None

@dataclass
class CochraneNotes:
    follow_up_periods: list[str] | None = None
    funding_source: str | None = None
    conflicts_of_interest: str | None = None
    ethical_approval: str | None = None
    trial_registration: str | None = None
    publication_status: str | None = None
    additional_notes: list[str] | None = None
```

`CochraneParticipants.format_for_table() -> str` and `CochraneNotes.format_for_table() -> str` produce the multi-line cell text used by the formatters.

---

### `CochraneStudyCharacteristics`

The complete study-characteristics table for one study, plus optional identifying metadata.

```python
@dataclass
class CochraneStudyCharacteristics:
    study_id: str
    methods: str
    participants: CochraneParticipants
    interventions: CochraneInterventions
    outcomes: CochraneOutcomes
    notes: CochraneNotes
    document_id: int | None = None
    document_title: str | None = None
    pmid: str | None = None
    doi: str | None = None
    created_at: datetime | None = None   # auto-stamped with UTC now when omitted
```

---

### `CochraneStudyAssessment`

A complete Cochrane-aligned study assessment: characteristics table + nine-domain RoB, plus optional overall scoring metadata (a superset of the Cochrane template).

```python
@dataclass
class CochraneStudyAssessment:
    study_characteristics: CochraneStudyCharacteristics
    risk_of_bias: CochraneRiskOfBias
    overall_quality_score: float | None = None  # 0–10 scale
    overall_confidence: float | None = None     # 0–1 scale
    evidence_level: str | None = None           # e.g. "Level 2 (moderate-high)"
    assessment_notes: list[str] | None = None
    assessment_version: str = "2.0.0"
```

| Property | Description |
|----------|-------------|
| `study_id -> str` | The study identifier (from the characteristics table). |
| `document_id -> int \| None` | The document id (from the characteristics table), if any. |

---

### Factory Functions

```python
def create_default_risk_of_bias_item(
    domain: str,
    bias_type: str,
    outcome_type: str | None = None,
) -> RiskOfBiasItem

def create_default_cochrane_risk_of_bias() -> CochraneRiskOfBias
```

Create `"Unclear risk"` placeholders for when information is unavailable; the second returns all nine domains preset to `"Unclear risk"` with a standard support text.

---

## Cochrane Formatters

Renderers in `bmlib.quality.cochrane_formatter` (not re-exported from `bmlib.quality`) produce the Cochrane Handbook study-characteristics and risk-of-bias tables in Markdown or HTML.

```python
from bmlib.quality.cochrane_formatter import (
    format_study_characteristics_markdown,
    format_risk_of_bias_markdown,
    format_complete_assessment_markdown,
    format_multiple_assessments_markdown,
    format_risk_of_bias_summary_markdown,
    format_study_characteristics_html,
    format_risk_of_bias_html,
    get_cochrane_css,
)
```

| Function | Description |
|----------|-------------|
| `format_study_characteristics_markdown(study_chars: CochraneStudyCharacteristics) -> str` | Study-characteristics table as Markdown. |
| `format_risk_of_bias_markdown(rob: CochraneRiskOfBias) -> str` | Nine-domain RoB table as Markdown. |
| `format_complete_assessment_markdown(assessment: CochraneStudyAssessment) -> str` | Characteristics + RoB + summary (quality score, confidence, evidence level, notes) as Markdown. |
| `format_multiple_assessments_markdown(assessments: list[CochraneStudyAssessment], title: str = "Characteristics of included studies") -> str` | Several assessments as one document, separated by horizontal rules. |
| `format_risk_of_bias_summary_markdown(assessments: list[CochraneStudyAssessment]) -> str` | Cross-study RoB summary matrix (one row per domain, one column per study; `+` low, `-` high, `?` unclear). |
| `format_study_characteristics_html(study_chars: CochraneStudyCharacteristics) -> str` | Study-characteristics table as HTML (style with `get_cochrane_css()`). |
| `format_risk_of_bias_html(rob: CochraneRiskOfBias) -> str` | RoB table as HTML with colour-coded judgement cells. |
| `get_cochrane_css() -> str` | `<style>` block (also available as the `COCHRANE_CSS` constant) for the HTML tables. |

**Example:**

```python
from bmlib.quality import (
    CochraneInterventions,
    CochraneNotes,
    CochraneOutcomes,
    CochraneParticipants,
    CochraneStudyAssessment,
    CochraneStudyCharacteristics,
    create_default_cochrane_risk_of_bias,
)
from bmlib.quality.cochrane_formatter import format_complete_assessment_markdown

rob = create_default_cochrane_risk_of_bias()  # all nine domains "Unclear risk"
rob.random_sequence_generation.judgement = "Low risk"
rob.random_sequence_generation.support_for_judgement = "Computer-generated randomisation."

assessment = CochraneStudyAssessment(
    study_characteristics=CochraneStudyCharacteristics(
        study_id="Smith 2024",
        methods="Randomized, double-blind, placebo-controlled trial",
        participants=CochraneParticipants(
            setting="12 primary-care clinics",
            population="Adults with type 2 diabetes",
            total_participants=450,
        ),
        interventions=CochraneInterventions(description="Metformin 500 mg vs placebo"),
        outcomes=CochraneOutcomes(description="HbA1c at 12 months"),
        notes=CochraneNotes(trial_registration="NCT01234567"),
    ),
    risk_of_bias=rob,
    overall_quality_score=8.0,
    evidence_level="Level 2 (moderate-high)",
)

print(format_complete_assessment_markdown(assessment))
```

---

## Scoring Models (Audit Trail)

`bmlib.quality.scoring_models` defines audit-trail models for multi-dimensional quality scoring: a `DimensionScore` holds one dimension's score plus `AssessmentDetail` entries recording *what* was extracted, *how much* it contributed, and *why*. Populated by the rule-based extractors below (and usable by LLM assessors). `AssessmentDetail` and `DimensionScore` are re-exported from `bmlib.quality`; the dimension-name constants must be imported from `bmlib.quality.scoring_models`.

### Dimension Name Constants

```python
DIMENSION_STUDY_DESIGN = "study_design"
DIMENSION_SAMPLE_SIZE = "sample_size"
DIMENSION_METHODOLOGICAL_QUALITY = "methodological_quality"
DIMENSION_RISK_OF_BIAS = "risk_of_bias"
DIMENSION_REPLICATION_STATUS = "replication_status"

ALL_DIMENSIONS  # list of the five names above
```

---

### `AssessmentDetail`

One audit-trail entry for a scored component.

```python
@dataclass
class AssessmentDetail:
    dimension: str                    # e.g. "study_design", "sample_size"
    component: str                    # e.g. "randomization"
    extracted_value: str | None       # e.g. "double-blind", "450"
    score_contribution: float         # points contributed to the dimension score
    evidence_text: str | None = None  # relevant excerpt from the paper
    reasoning: str | None = None      # explanation for the score
```

| Method | Description |
|--------|-------------|
| `to_dict() -> dict[str, Any]` | Serialise to dictionary. |
| `from_dict(data: dict) -> AssessmentDetail` | Deserialise from dictionary. |

---

### `DimensionScore`

A single dimension's score with its contributing audit-trail entries.

```python
@dataclass
class DimensionScore:
    dimension_name: str
    score: float                                          # typically 0–10
    details: list[AssessmentDetail] = field(default_factory=list)
```

| Method | Description |
|--------|-------------|
| `add_detail(component: str, value: str, contribution: float, evidence: str \| None = None, reasoning: str \| None = None) -> None` | Append an audit-trail entry for a component of this dimension. |
| `to_dict() -> dict[str, Any]` | Serialise to dictionary, including all detail entries. |
| `from_dict(data: dict) -> DimensionScore` | Deserialise from dictionary. |

---

## Rule-Based Extractors

LLM-free extractors in `bmlib.quality.extractors`: stateless keyword/regex heuristics that estimate study characteristics and return `DimensionScore` objects with a full audit trail. A cheap pre-filter or fallback for the LLM-based tiers. `extract_study_type` and `extract_sample_size_dimension` are re-exported from `bmlib.quality`; the helpers must be imported from `bmlib.quality.extractors`.

All extractor functions take a `document` dict; text is chosen by `prepare_extractor_search_text` (a substantial `"full_text"` if longer than the abstract, else `"abstract"` + `"methods_text"`).

### `extract_study_type`

```python
def extract_study_type(
    document: dict[str, Any],
    keywords_config: dict[str, list[str]] | None = None,
    hierarchy_config: dict[str, float] | None = None,
    priority_order: list[str] | None = None,
    exclusions_config: dict[str, list[str]] | None = None,
) -> DimensionScore
```

Detect study type by keyword matching with exclusion-context guarding. Tries each type in priority order (systematic review > meta-analysis > quasi-experimental > RCT > ... > case report) and rejects matches preceded within 50 characters by an exclusion pattern — so `"non-randomized trial"` does not match RCT. Keywords match whole words only, with an optional plural "s" (`"RCT"` matches `"RCTs"` but not `"infarct"`); every occurrence is tried, so one excluded mention does not suppress a later clean one. Returns a `DimensionScore` for the `"study_design"` dimension with the matched keyword and its surrounding text as evidence; defaults to `"unknown"` at a neutral score of 5.0 when nothing matches. Default hierarchy scores range from 10.0 (systematic review / meta-analysis) through 8.0 (RCT) down to 1.0 (case report).

---

### `extract_sample_size_dimension`

```python
def extract_sample_size_dimension(
    document: dict[str, Any],
    scoring_config: dict[str, float] | None = None,
) -> DimensionScore
```

Extract the sample size (largest plausible match of patterns such as `n = 450`, `"450 participants"`, `"enrolled 450 patients"`) and score it logarithmically (`log10(n) * log_multiplier`, clamped to 0–10), then add bonuses when a power calculation and/or confidence intervals are reported (total capped at 10). Default `scoring_config`: `{"log_multiplier": 2.0, "power_calculation_bonus": 2.0, "ci_reported_bonus": 0.5}`. Returns a score of 0 with a `"not_found"` audit entry when no sample size is found.

---

### Helper Functions

| Function | Description |
|----------|-------------|
| `prepare_extractor_search_text(document: dict[str, Any]) -> str` | Choose the best text for extraction (`full_text` if longer than the abstract, else abstract + methods). |
| `find_sample_size(text: str, min_n: int = 5, max_n: int = 1_000_000) -> int \| None` | Largest plausible sample-size match within `[min_n, max_n]`, or `None`. |
| `calculate_sample_size_score(n: int, log_multiplier: float = 2.0) -> float` | Score a sample size on a 0–10 scale as `log10(n) * log_multiplier`. |
| `has_power_calculation(text: str) -> bool` | Whether the text mentions a power/sample-size calculation. |
| `find_power_calc_context(text: str) -> str` | Snippet around the first power-calculation mention, or `""`. |
| `has_ci_reporting(text: str) -> bool` | Whether the text reports confidence intervals (integer citation markers and year ranges do not count). |
| `has_exclusion_pattern(text: str, keyword: str, exclusion_patterns: list[str], context_window: int = 50, keyword_pos: int \| None = None) -> bool` | Whether an exclusion pattern appears just before the keyword occurrence. |
| `extract_text_context(text: str, keyword: str, context_chars: int = 50, keyword_pos: int \| None = None) -> str` | Snippet of text around a keyword occurrence, with ellipses where truncated. |
| `get_extracted_sample_size(dimension_score: DimensionScore) -> int \| None` | The numeric sample size recorded in a sample-size dimension. |
| `get_extracted_study_type(dimension_score: DimensionScore) -> str \| None` | The study-type string recorded in a study-design dimension. |

**Example:**

```python
from bmlib.quality import extract_sample_size_dimension, extract_study_type
from bmlib.quality.extractors import get_extracted_sample_size, get_extracted_study_type

doc = {
    "abstract": (
        "We conducted a randomized controlled trial and enrolled 450 patients. "
        "A power calculation determined the sample size; results are reported "
        "with 95% CI."
    )
}

design = extract_study_type(doc)
print(get_extracted_study_type(design))   # "rct"
print(design.score)                       # 8.0
for detail in design.details:
    print(detail.component, detail.extracted_value, detail.reasoning)

size = extract_sample_size_dimension(doc)
print(get_extracted_sample_size(size))    # 450
print(round(size.score, 2))               # 7.81 = log10(450)*2 + 2.0 (power) + 0.5 (CI)
```
