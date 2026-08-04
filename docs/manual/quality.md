# bmlib.quality — Quality Assessment Pipeline

Tiered quality assessment pipeline for biomedical publications, inspired by the Oxford Centre for Evidence-Based Medicine (CEBM) evidence hierarchy. The pipeline escalates from free metadata checks to increasingly capable LLM assessments:

- **Tier 1:** PubMed metadata classification (free, instant)
- **Tier 2:** LLM study-design classification (cheap model, ~$0.001/doc)
- **Tier 3:** Deep methodological assessment (capable model, ~$0.003/doc)
- **Tier 4:** Cochrane-aligned assessment — nine-domain risk of bias plus the study-characteristics table (capable model; opt in via `QualityFilter(use_cochrane_assessment=True)`, off by default)

Tier 2 runs by default when Tier 1's metadata result is not confident enough (`use_llm_classification=True`); Tiers 3 and 4 are opt-in via [`QualityFilter`](#qualityfilter) and each supersedes every shallower tier when requested, rather than running alongside it. Alongside the pipeline, the module ships two toolkits that remain **standalone**: Cochrane table formatters, and rule-based (LLM-free) extractors with an audit-trail scoring model.

> **The Cochrane half is wired in; the formatters and extractors are not.**
> `QualityManager` now imports `cochrane_assessor` and `cochrane_models` alongside `data_models`, `metadata_filter`, `study_classifier`, and `quality_agent`. Tier 3 (`QualityAgent`) still produces only the five-domain [`BiasRisk`](#biasrisk) — never a [`CochraneRiskOfBias`](#cochraneriskofbias) — but Tier 4 (`CochraneAssessor`) produces the nine-domain table directly, and [`collapse_risk_of_bias()`](#collapse_risk_of_bias) is the conversion function from `CochraneRiskOfBias` to `BiasRisk` that used not to exist. See [Cochrane assessment](#cochrane-assessment) below. `cochrane_formatter` and `extractors` remain untouched: no tier calls them, and there is still no conversion between [`DimensionScore`](#dimensionscore) and [`QualityAssessment`](#qualityassessment). Render Cochrane tables and call the extractors yourself.

## Module layout

| Submodule | Contents | Wired into the pipeline? |
|-----------|----------|--------------------------|
| `data_models` | `StudyDesign`, `QualityTier`, `BiasRisk`, `QualityAssessment`, `QualityFilter`, design mappings | Yes |
| `metadata_filter` | Tier 1 — `classify_from_metadata()` | Yes |
| `study_classifier` | Tier 2 — `StudyClassifier` | Yes |
| `quality_agent` | Tier 3 — `QualityAgent` | Yes |
| `cochrane_assessor` | Tier 4 — `CochraneAssessor` | Yes |
| `manager` | `QualityManager` orchestrator | Yes |
| `cochrane_models` | Nine-domain RoB + study-characteristics dataclasses, plus `collapse_risk_of_bias()` | **Yes — produced by `cochrane_assessor`, bridged onto `BiasRisk`** |
| `cochrane_formatter` | Markdown / HTML Cochrane table renderers | **No — standalone** |
| `extractors` | Rule-based, LLM-free extraction functions | **No — standalone** |
| `scoring_models` | `AssessmentDetail`, `DimensionScore` audit trail | **No — standalone** |

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
    CochraneAssessor,
    CochraneStudyAssessment,
    CochraneStudyCharacteristics,
    CochraneRiskOfBias,
    CochraneParticipants,
    CochraneInterventions,
    CochraneOutcomes,
    CochraneNotes,
    RiskOfBiasItem,
    RiskOfBiasJudgement,
    collapse_risk_of_bias,
    create_default_cochrane_risk_of_bias,
    create_default_risk_of_bias_item,
    # Rule-based extractors + audit-trail scoring models
    AssessmentDetail,
    DimensionScore,
    extract_study_type,
    extract_sample_size_dimension,
)
```

The list above is the complete `bmlib.quality.__all__`. Everything else must be imported from its submodule:

```python
from bmlib.quality.cochrane_formatter import format_complete_assessment_markdown
from bmlib.quality.cochrane_models import ROB_JUDGEMENT_LOW, VALID_ROB_JUDGEMENTS
from bmlib.quality.data_models import DESIGN_TO_RANDOMIZED, STUDY_DESIGN_MAPPING
from bmlib.quality.extractors import find_sample_size, get_extracted_study_type
from bmlib.quality.metadata_filter import classify_from_metadata
from bmlib.quality.quality_agent import QualityAgent
from bmlib.quality.scoring_models import ALL_DIMENSIONS, DIMENSION_SAMPLE_SIZE
from bmlib.quality.study_classifier import StudyClassifier
```

In particular, **none** of the `cochrane_formatter` functions and **none** of the `DIMENSION_*` constants are re-exported at package level.

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

### `DESIGN_TO_RANDOMIZED`

Maps `StudyDesign` to randomization status (`dict[StudyDesign, bool | None]`). Not exported at package level — import from `bmlib.quality.data_models`.

| Study Design | `is_randomized` |
|-------------|-----------------|
| `RCT` | `True` |
| `COHORT_PROSPECTIVE`, `COHORT_RETROSPECTIVE`, `CASE_CONTROL`, `CROSS_SECTIONAL`, `CASE_SERIES`, `CASE_REPORT`, `EDITORIAL`, `LETTER`, `COMMENT` | `False` |
| Any other design (absent key → `.get()` returns `None`) | `None` |

`SYSTEMATIC_REVIEW`, `META_ANALYSIS`, `GUIDELINE`, `OTHER`, and `UNKNOWN` are deliberately absent: the design alone does not determine randomization (a systematic review may synthesise RCTs or observational studies).

Both `QualityAssessment.from_metadata()` and `QualityAssessment.from_classification()` populate `is_randomized` from this mapping, so `QualityFilter(require_randomization=True)` now recognises an RCT identified at Tier 1 or Tier 2 instead of rejecting it for a missing flag.

```python
from bmlib.quality import QualityAssessment, QualityFilter, StudyDesign

rct = QualityAssessment.from_metadata(StudyDesign.RCT)
assert rct.is_randomized is True
assert rct.passes_filter(QualityFilter(require_randomization=True))

review = QualityAssessment.from_metadata(StudyDesign.SYSTEMATIC_REVIEW)
assert review.is_randomized is None
```

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
    cochrane_assessment: Any = None      # CochraneStudyAssessment, set by Tier 4
```

`cochrane_assessment` is typed `Any` rather than `CochraneStudyAssessment | None` for the same reason `transparency_result` is: naming the type here would make `data_models` import `cochrane_models`, which imports `data_models` back for `BiasRisk`. Declared last, so positional construction stays stable.

#### Factory Methods

| Method | Description |
|--------|-------------|
| `QualityAssessment.unclassified()` | Create an empty/unclassified assessment (all defaults). |
| `QualityAssessment.from_metadata(design, confidence=0.9)` | Create a Tier 1 assessment from PubMed metadata. Sets `extraction_method="pubmed_metadata"` and derives `quality_tier`, `quality_score`, and `is_randomized` from the design mappings. |
| `QualityAssessment.from_classification(study_design, confidence=0.7, sample_size=None, is_blinded=None)` | Create a Tier 2 assessment from LLM classification. Sets `extraction_method="llm_classifier"` and derives the same three fields. |

#### Instance Methods

| Method | Description |
|--------|-------------|
| `passes_filter(qfilter: QualityFilter) -> bool` | Check if this assessment passes the given filter criteria. |
| `to_dict() -> dict[str, Any]` | Serialise to a JSON-safe dictionary. |
| `from_dict(data: dict) -> QualityAssessment` | Deserialise from a dictionary. |

`to_dict()` is **lossy**: it omits `extraction_details`, `transparency_result`, and `original_quality_tier`, and includes `"bias_risk"` and `"cochrane_assessment"` only when each is set (via their own `to_dict()`). A `from_dict(to_dict(x))` round-trip therefore drops those three always-omitted fields, but does carry a set `cochrane_assessment` through — `from_dict()` rebuilds it with `CochraneStudyAssessment.from_dict()`, and a dict without the key loads it back as `None`.

`passes_filter()` skips the `min_sample_size` check when `sample_size` is `None` — an assessment with an unknown sample size passes a size filter rather than failing it.

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
    use_cochrane_assessment: bool = False
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
| `use_cochrane_assessment` | `bool` | `False` | Enable Tier 4 (Cochrane assessment). Supersedes Tier 3 when both are set — see [Cochrane assessment](#cochrane-assessment). |

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
| `llm` | `LLMClient` | *(required)* | LLM client for Tier 2, Tier 3 and Tier 4 assessments. |
| `classifier_model` | `str` | *(required)* | Model string for Tier 2 classification (cheap/fast, e.g. `"anthropic:claude-3-haiku-20240307"`). |
| `assessor_model` | `str` | *(required)* | Model string for Tier 3 deep assessment *and* Tier 4 Cochrane assessment (capable, e.g. `"anthropic:claude-sonnet-4-20250514"`). One model parameter serves both — a second buys nothing until someone needs the two to differ. |
| `template_engine` | `TemplateEngine \| None` | `None` | Optional template engine for custom prompts. |

---

### `QualityManager.assess`

```python
def assess(
    self,
    title: str | None,
    abstract: str | None,
    *,
    publication_types: Sequence[str] = (),
    filter_settings: QualityFilter | None = None,
    full_text: str | None = None,
) -> QualityAssessment
```

Run the tiered assessment pipeline for a single paper.

**Assessment flow:**

1. **Tier 1 (metadata)** always runs first, via `classify_from_metadata(publication_types)`. Free and instant.
2. If `use_metadata_only` is `True`, the Tier 1 result is returned immediately — no LLM call.
3. The Tier 1 result is *confident* when its `confidence >= 0.9` (`METADATA_ACCEPTANCE_THRESHOLD`) **and** its `quality_tier` is not `UNCLASSIFIED`. A confident result is returned as-is unless `use_detailed_assessment` or `use_cochrane_assessment` is `True`.
4. **Tier 4 (Cochrane assessment):** if `use_cochrane_assessment` is `True`, `CochraneAssessor.assess()` runs against `full_text` (falling back to `abstract` when `full_text` is absent) and *enriches* the Tier 1 result rather than replacing it — see [Cochrane assessment](#cochrane-assessment) for what that means. **Both Tier 2 and Tier 3 are skipped entirely** — Tier 4 is deeper than Tier 3 exactly as Tier 3 is deeper than Tier 2, so the shallower tiers are never run and discarded.
5. **Tier 3 (deep assessment):** otherwise, if `use_detailed_assessment` is `True`, `QualityAgent.assess()` runs and its result is returned. **Tier 2 is skipped entirely** — the detailed assessment supersedes the classifier, so the cheap-but-not-free call is never made.
6. **Tier 2 (classifier):** otherwise, if `use_llm_classification` is `True` (the default), `StudyClassifier.classify()` runs and its result is returned.
7. Otherwise the Tier 1 result is returned as a fallback.

Tiers 2, 3 and 4 never run together. Tiers 2 and 3 *replace* the Tier 1 result outright; Tier 4 *merges* into it (see below) — the one tier that does not discard the metadata pass's `study_design`.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `title` | `str \| None` | *(required)* | Paper title. May be `None`. |
| `abstract` | `str \| None` | *(required)* | Paper abstract. May be `None` — sources omit it often enough that the LLM tiers work around the gap rather than raising. |
| `publication_types` | `Sequence[str]` | `()` | PubMed publication type strings (e.g. `["Randomized Controlled Trial"]`). |
| `filter_settings` | `QualityFilter \| None` | `None` | Controls which tiers are enabled. Defaults to `QualityFilter()`. |
| `full_text` | `str \| None` | `None` | The paper's full text, for the Cochrane pass (Tier 4 only). Falls back to `abstract` when absent — a weak risk-of-bias assessment beats none. |

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

# Tier 4 Cochrane assessment (supersedes Tier 3 — see "Cochrane assessment" below)
result = manager.assess(
    title="...",
    abstract="...",
    full_text="...",
    filter_settings=QualityFilter(use_cochrane_assessment=True),
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

Assess a batch of papers. Each dict in `papers` should have `"title"` and `"abstract"` keys, and optionally `"publication_types"` and `"full_text"` (read for the Tier 4 Cochrane pass).

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

Matching is case-insensitive and normalises hyphens and underscores, so `"systematic review"`, `"Systematic Review"`, and `"systematic-review"` all match.

**Confidence:** a type found in the priority list scores `METADATA_HIGH_CONFIDENCE` (0.9). A known type *not* in the priority list scores `0.9 * 0.8 = 0.72` — below the manager's acceptance threshold, so such a record falls through to LLM classification. No match at all returns `QualityAssessment.unclassified()`.

### Supported PubMed Publication Types

The following PubMed publication types are mapped to study designs (resolved in priority order):

| PubMed Publication Type | Study Design |
|------------------------|--------------|
| Systematic Review | `SYSTEMATIC_REVIEW` |
| Meta-Analysis | `META_ANALYSIS` |
| Randomized Controlled Trial, Controlled Clinical Trial, Clinical Trial, Clinical Trial (Phase I–IV), Pragmatic Clinical Trial, Equivalence Trial | `RCT` |
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

Cohort designs precede case-control in the priority list, so a paper tagged with both resolves to the stronger (cohort) design.

**Deliberately unmapped:** `"Multicenter Study"`, `"Comparative Study"`, and `"Observational Study"`. The first two are organisational or generic attributes rather than designs. `"Observational Study"` is PubMed's catch-all for non-experimental studies whose specific subtype was not indexed, so mapping it to prospective cohort asserted a tier and a prospectivity the evidence did not support, at high confidence. Records carrying only such tags fall through to LLM classification.

---

## Tier 2: Study Classifier

```python
class StudyClassifier(BaseAgent):
    def __init__(self, *args, temperature: float = 0.1, max_tokens: int = 1024, **kwargs)
    def classify(self, title: str | None, abstract: str | None) -> QualityAssessment
```

The `StudyClassifier` (subclass of `BaseAgent`) uses a cheap/fast LLM to classify study design from title + abstract. The abstract is truncated to `MAX_ABSTRACT_CHARS` (3000). Any exception — including JSON-repair failure after retries — is logged and yields `QualityAssessment.unclassified()` rather than propagating.

Either argument may be `None` — sources omit abstracts often enough, and a nullable database column delivers the gap that way. A missing abstract is worked around rather than raised, so one gappy record cannot abort a caller's batch. When *both* are missing there is nothing to classify, so no LLM call is made and `QualityAssessment.unclassified()` is returned: an empty prompt does not produce an empty answer, and a fabricated classification is indistinguishable downstream from a real one.

The classifier's budget is far above the ~50 tokens its JSON needs because small local models preface it with commentary despite being asked for JSON alone; a tight ceiling truncates the preamble and loses the JSON with it, leaving every paper `UNCLASSIFIED`.

It returns structured JSON with:

- `study_design`: One of the `StudyDesign` enum values
- `confidence`: 0.0–1.0
- `sample_size`: Integer or null
- `blinding`: `"none"`, `"single"`, `"double"`, `"triple"`, or null

The classifier focuses on the paper's own methodology (e.g. "we conducted", "this study") and ignores referenced studies (e.g. "a previous meta-analysis found").

---

## Tier 3: Deep Assessment

```python
class QualityAgent(BaseAgent):
    def assess(self, title: str, abstract: str) -> QualityAssessment
```

The `QualityAgent` (subclass of `BaseAgent`) uses a capable LLM for comprehensive assessment including:

- Study design classification
- Quality score (1–10)
- Oxford CEBM evidence level
- Design characteristics (randomized, controlled, blinded, prospective, multicenter)
- Sample size
- Cochrane Risk-of-Bias across 5 domains — a populated [`BiasRisk`](#biasrisk), **not** a [`CochraneRiskOfBias`](#cochraneriskofbias)
- Methodological strengths and limitations
- Confidence score

The abstract is truncated to `MAX_ABSTRACT_CHARS` (4000). The result carries `assessment_tier=3` and `extraction_method="llm_deep_assessment"`. As with Tier 2, any exception yields `QualityAssessment.unclassified()`, either argument may be `None`, and both missing short-circuits to `unclassified()` without an LLM call.

This tier is the most expensive and should be used selectively.

Both agents override `BaseAgent`'s generic `temperature=0.3, max_tokens=4096` with their own defaults — `0.1 / 1024` for the classifier, `0.2 / 1024` for the assessor. The defaults live on the agents rather than at the call site or in `QualityManager`, so they hold however an agent is constructed; a caller can still override either through the constructor.

---

## Cochrane-Aligned Models

`bmlib.quality.cochrane_models` provides dataclasses matching the Cochrane Handbook's *Characteristics of included studies* and *Risk of bias* tables. These are the **classic RoB 1 / Cochrane Handbook domains** — selection, performance, detection, attrition, and reporting bias. They are **not** RoB 2 signalling questions, **not** ROBINS-I, and **not** GRADE.

> **No longer standalone.** [`CochraneAssessor`](#cochrane-assessment) (Tier 4) produces these types from a title and text, and [`collapse_risk_of_bias()`](#collapse_risk_of_bias) converts a `CochraneRiskOfBias` to a [`BiasRisk`](#biasrisk) — the conversion this note used to say did not exist. You can still build the models yourself from your own extraction step (nothing requires going through the assessor) and render them with the [formatters](#cochrane-formatters), which remain a separate, standalone toolkit.

### Judgement Constants

```python
ROB_JUDGEMENT_LOW = "Low risk"
ROB_JUDGEMENT_HIGH = "High risk"
ROB_JUDGEMENT_UNCLEAR = "Unclear risk"

VALID_ROB_JUDGEMENTS = {ROB_JUDGEMENT_LOW, ROB_JUDGEMENT_HIGH, ROB_JUDGEMENT_UNCLEAR}
```

Note the capitalisation and the `" risk"` suffix: these are **not** the same vocabulary as `BiasRisk`, which uses bare lowercase `"low"` / `"unclear"` / `"high"`.

### `RiskOfBiasJudgement`

```python
class RiskOfBiasJudgement(Enum):
    LOW = "Low risk"
    HIGH = "High risk"
    UNCLEAR = "Unclear risk"

    @classmethod
    def from_string(cls, value: str) -> RiskOfBiasJudgement
```

`from_string()` is tolerant of case and common variants (`"low"`, `"low risk"`, `"low_risk"`; `"unclear"`, `"unknown"`, …). An unrecognised value logs a warning and falls back to `UNCLEAR`.

**This enum is not used by any other model in the module** — `RiskOfBiasItem.judgement` is a plain `str`. Treat `RiskOfBiasJudgement` as a normalisation helper for untrusted input, then store `.value`:

```python
from bmlib.quality import RiskOfBiasJudgement

judgement = RiskOfBiasJudgement.from_string("LOW RISK").value  # "Low risk"
```

### `RiskOfBiasItem`

A single risk-of-bias domain assessment.

```python
@dataclass
class RiskOfBiasItem:
    domain: str                     # e.g. "Random sequence generation"
    bias_type: str                  # e.g. "selection bias"
    judgement: str                  # one of VALID_ROB_JUDGEMENTS
    support_for_judgement: str      # text explaining the basis
    outcome_type: str | None = None # for detection bias: "subjective" | "objective"
```

`__post_init__` **warns** on a judgement outside `VALID_ROB_JUDGEMENTS` — it does not raise, and the invalid value is kept.

| Method | Description |
|--------|-------------|
| `to_dict() -> dict[str, Any]` | Serialise. `outcome_type` is omitted when falsy. |
| `from_dict(data: dict) -> RiskOfBiasItem` | Deserialise. `domain`, `bias_type`, `judgement`, and `support_for_judgement` are required keys. |

### `CochraneRiskOfBias`

Nine required `RiskOfBiasItem` fields, in declaration order:

| Field | Bias type | Notes |
|-------|-----------|-------|
| `random_sequence_generation` | selection bias | |
| `allocation_concealment` | selection bias | |
| `baseline_outcome_measurements` | selection bias | |
| `baseline_characteristics` | selection bias | |
| `blinding_participants_personnel` | performance bias | |
| `blinding_outcome_assessment_subjective` | detection bias | `outcome_type="subjective"` |
| `blinding_outcome_assessment_objective` | detection bias | `outcome_type="objective"` |
| `incomplete_outcome_data` | attrition bias | |
| `selective_reporting` | reporting bias | |

Four selection-bias domains, one performance, two detection (split by outcome type), one attrition, one reporting.

| Method | Description |
|--------|-------------|
| `to_dict() -> dict[str, Any]` | Serialise all nine domains. |
| `to_list() -> list[RiskOfBiasItem]` | The nine domains in canonical Cochrane table order. |
| `from_dict(data: dict) -> CochraneRiskOfBias` | Deserialise. All nine keys are required. |
| `get_summary_counts() -> dict[str, int]` | Domain counts keyed by the three judgement constants. |

### Study Characteristics Sections

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

All four have `to_dict()` and `from_dict()`. `from_dict()` defaults missing required text to `"Not reported"` (`setting` and `population` on `CochraneParticipants`; `description` on `CochraneInterventions` and `CochraneOutcomes`).

| Method | Description |
|--------|-------------|
| `CochraneParticipants.format_for_table() -> str` | Renders `Setting: …`, a blank line, the population, and an `N=…` line (with per-group breakdown when `group_sizes` is set). |
| `CochraneNotes.format_for_table() -> str` | Renders the populated fields as blank-line-separated blocks, or `"No additional notes"` when all are empty. |

### `CochraneStudyCharacteristics`

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
    created_at: datetime | None = None
```

`__post_init__` stamps `datetime.now(UTC)` when `created_at` is `None`. `to_dict()` writes `created_at` as an ISO 8601 string; `from_dict()` parses it back with `datetime.fromisoformat()`.

### `CochraneStudyAssessment`

```python
@dataclass
class CochraneStudyAssessment:
    study_characteristics: CochraneStudyCharacteristics
    risk_of_bias: CochraneRiskOfBias
    overall_quality_score: float | None = None   # 0–10
    overall_confidence: float | None = None      # 0–1
    evidence_level: str | None = None            # e.g. "Level 2 (moderate-high)"
    assessment_notes: list[str] | None = None
    assessment_version: str = "2.0.0"
    condensed_from_chars: int | None = None      # original length, if condensed before assessment
```

| Member | Description |
|--------|-------------|
| `to_dict() -> dict[str, Any]` | Serialise the whole assessment. |
| `from_dict(data: dict) -> CochraneStudyAssessment` | Deserialise; `assessment_version` defaults to `"2.0.0"`. |
| `study_id` *(property)* | Delegates to `study_characteristics.study_id`. |
| `document_id` *(property)* | Delegates to `study_characteristics.document_id`. |

`condensed_from_chars` is set by [`CochraneAssessor`](#cochrane-assessment) to the original character count when the text was reduced to an evidence digest before assessment, and left `None` when the paper went to the model whole — see [Cochrane assessment](#cochrane-assessment). Declared last, so positional construction stays stable across versions.

### Factory Functions

| Function | Description |
|----------|-------------|
| `create_default_risk_of_bias_item(domain, bias_type, outcome_type=None) -> RiskOfBiasItem` | An `"Unclear risk"` item with `support_for_judgement="Not reported or insufficient information to assess"`. |
| `create_default_cochrane_risk_of_bias() -> CochraneRiskOfBias` | All nine domains `"Unclear risk"`, with the canonical domain names and bias types. |

Start from `create_default_cochrane_risk_of_bias()` and overwrite the domains you can actually judge — this guarantees the nine required fields are present and correctly labelled.

---

## Cochrane assessment

`bmlib.quality.cochrane_assessor` is the producer `cochrane_models.py` was missing: `CochraneAssessor`, a `BaseAgent` subclass that turns a title and text into a `CochraneStudyAssessment` — the Handbook's five-section study-characteristics table plus a judgement and supporting text for each of the nine Risk of Bias domains. It is Tier 4 of the pipeline, reachable through `QualityManager` behind `QualityFilter(use_cochrane_assessment=True)`.

### `CochraneAssessor`

```python
class CochraneAssessor(BaseAgent):
    def __init__(
        self,
        llm: LLMClient,
        model: str,
        template_engine: TemplateEngine | None = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        condense_config: ProcessingConfig | None = None,
    ) -> None
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `llm` | `LLMClient` | *(required)* | The LLM client to use. |
| `model` | `str` | *(required)* | Full model string (`"provider:model_name"`). A capable model — the reply carries a nine-domain judgement plus a five-section extraction in one pass. |
| `template_engine` | `TemplateEngine \| None` | `None` | For parity with the other quality agents. This agent's prompts are module constants, not templates. |
| `temperature` | `float` | `0.1` | Low by default, for consistency between runs. |
| `max_tokens` | `int` | `4096` | Output ceiling. Larger than Tier 3's, since the reply carries nine judgements with their supporting text plus the whole characteristics table. |
| `condense_config` | `ProcessingConfig \| None` | `None` | Governs the map-reduce pass that runs when *text* is larger than one context. See "Condensing oversized text" below. |

### `CochraneAssessor.assess`

```python
def assess(
    self,
    title: str | None,
    text: str | None,
    *,
    study_id: str | None = None,
    pmid: str | None = None,
    doi: str | None = None,
    document_id: int | None = None,
    min_confidence: float = 0.0,
) -> CochraneStudyAssessment | None
```

Assess one study against the Cochrane template. Either `title` or `text` may be `None`; with **both** missing there is nothing to assess and no model call is made — left to itself, the model returns a fully-formed nine-domain judgement for a paper it was told nothing about.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `title` | `str \| None` | *(required)* | The paper's title. |
| `text` | `str \| None` | *(required)* | The text to assess — full text or abstract. Full text gives a real risk-of-bias assessment; an abstract gives a weak one. |
| `study_id` | `str \| None` | `None` | Cochrane's "Author Year" study label. Unset, it falls back to `"Study {document_id}"` and then to the title — no surname is guessed from an author list. |
| `pmid` | `str \| None` | `None` | PubMed id, recorded on the characteristics table. |
| `doi` | `str \| None` | `None` | DOI, recorded on the characteristics table. |
| `document_id` | `int \| None` | `None` | The caller's own row id. |
| `min_confidence` | `float` | `0.0` | Reject an assessment whose `overall_confidence` falls below this. Zero rejects nothing. Only a *reported* confidence below the bar is rejected — an assessment whose confidence could not be parsed (`overall_confidence is None`) is kept regardless of `min_confidence`; an unknown confidence is not treated as a low one. |

**Returns:** the assessment, or `None` if it could not be made. `None` rather than an all-"Unclear risk" stand-in: that would be indistinguishable from a real assessment in which the model genuinely judged every domain unclear, and anything persisting results would store the fabrication permanently. `assess()` returns `None` when: both `title` and `text` are empty; condensing oversized text fails or produces an empty digest; the model call itself fails (a transport error, or a reply that still will not parse as JSON after `chat_json()`'s own retries); the model's reply parses but carries no `risk_of_bias` section, after both of `assess()`'s own attempts; or a reported `overall_confidence` falls below `min_confidence`.

**Example:**

```python
from bmlib.llm import get_llm_client
from bmlib.quality import CochraneAssessor, collapse_risk_of_bias

assessor = CochraneAssessor(
    llm=get_llm_client(),
    model="anthropic:claude-sonnet-4-20250514",
)

assessment = assessor.assess(
    title="Hospital at home for chronic heart failure",
    text=full_text,                 # or the abstract, if that is all there is
    study_id="Andrei 2011",
    pmid="21234567",
    doi="10.1000/example",
)

if assessment is None:
    ...                             # nothing was assessed; see the log
else:
    print(assessment.risk_of_bias.get_summary_counts())
    print(collapse_risk_of_bias(assessment.risk_of_bias))
    if assessment.condensed_from_chars:
        print(f"judged from a digest of {assessment.condensed_from_chars} chars")
```

`CochraneAssessor` also has `assess_batch(studies, *, min_confidence=0.0, progress_callback=None) -> list[CochraneStudyAssessment]` — a convenience loop over `assess()` that takes a dict per study (keyed by `assess()`'s own parameter names) and returns only the studies that succeeded — and `get_stats() -> dict[str, Any]`, which reports `total_assessments`, `successful_assessments`, `failed_assessments`, `parse_failures` and a derived `success_rate`; `successful_assessments + failed_assessments == total_assessments` always holds, so a batch that failed outright reports `success_rate=0.0` rather than the `1.0` an increment-on-success-only counter would give.

### Condensing oversized text

Text longer than `condense_config.max_context_chars` is reduced to an evidence digest by `bmlib.context_processor.LLMChunkProcessor` before assessment, so the nine-domain judgement is always made **once**, over content that fits — no per-chunk judgements are made and none have to be merged. Truncating instead was rejected: allocation concealment and blinding live in Methods and attrition in Results, so a head-of-string cut drops exactly the evidence the domains rest on.

Left unset, `condense_config` defaults to `ProcessingConfig(max_context_chars=DEFAULT_CONDENSE_THRESHOLD_CHARS)`, where `DEFAULT_CONDENSE_THRESHOLD_CHARS` is **48,000 characters** — roughly 12k tokens, chosen so a whole research paper usually passes through uncondensed while still leaving room in a 32k-token window for the ~4k-character prompt and a 4096-token answer. `ProcessingConfig`'s own default (4,000 characters — see `bmlib.context_processor`) would condense almost every full text and most long abstracts, which is why the assessor overrides it rather than taking the harness default.

When condensation ran, `CochraneStudyAssessment.condensed_from_chars` is set to the original character count; it is `None` when the paper went to the model whole. A judgement made over an LLM-condensed digest is weaker evidence than one made over the paper, so the result says so rather than leaving the caller to infer it.

### `collapse_risk_of_bias()`

```python
def collapse_risk_of_bias(rob: CochraneRiskOfBias) -> BiasRisk
```

Reduces the nine Cochrane domains to the five [`BiasRisk`](#biasrisk) domains — the conversion the "Cochrane-Aligned Models" section above used to say did not exist. The grouping is read off each [`RiskOfBiasItem`](#riskofbiasitem)'s own `bias_type` rather than written out per domain: four domains feed `selection`, two feed `detection`, and one each feeds `performance`, `attrition` and `reporting`.

Where several domains collapse onto one field, **the worst wins**, ranked `high` > `unclear` > `low` — `unclear` outranks `low` because an unreported domain is not a clean bill of health; you cannot claim low selection-bias risk when allocation concealment was never described. Judgements are normalised through `RiskOfBiasJudgement.from_string()` first, so an item carrying `"low"` rather than `"Low risk"` is not miscounted as unclear.

Raises `ValueError` if any item's `bias_type` is not one of the five Cochrane categories — silently dropping it would return a `BiasRisk` that looks complete and is not.

### Wired into `QualityManager` — Tier 4

`QualityFilter(use_cochrane_assessment=True)` routes `QualityManager.assess()` through `CochraneAssessor` instead of Tier 2 or Tier 3 — both are skipped entirely, exactly as Tier 3 already skips Tier 2 when requested. `QualityManager.assess()` gained a `full_text: str | None = None` keyword for this: the Cochrane pass reads `full_text`, falling back to `abstract` when it is absent (a weak risk-of-bias assessment beats none).

The Cochrane pass **enriches** the free Tier 1 metadata result rather than replacing it: the metadata tier supplies `study_design`, `quality_tier` and `quality_score`, which a Cochrane assessment does not produce; the Cochrane pass supplies `bias_risk` (via `collapse_risk_of_bias()`) and the full `cochrane_assessment` object, which the metadata tier cannot see. On success the returned `QualityAssessment` carries `assessment_tier=4`, `extraction_method="llm_cochrane_assessment"`, and `cochrane_assessment` set to the `CochraneStudyAssessment`. `evidence_level` is deliberately **not** copied across — Cochrane's is free-form model text, the metadata tier's is an Oxford CEBM level, and the Cochrane value stays reachable at `result.cochrane_assessment.evidence_level`. If `CochraneAssessor.assess()` returns `None`, `QualityManager.assess()` degrades to the Tier 1 result rather than to nothing — `assessment_tier` staying at whatever Tier 1 produced (`0` unclassified, `1` classified) rather than becoming `4` is what tells the two outcomes apart.

**Example:**

```python
from bmlib.quality import QualityFilter, QualityManager

manager = QualityManager(
    llm=get_llm_client(),
    classifier_model="anthropic:claude-haiku-4-5-20251001",
    assessor_model="anthropic:claude-sonnet-4-20250514",
)

result = manager.assess(
    title=paper["title"],
    abstract=paper["abstract"],
    full_text=paper["full_text"],
    publication_types=paper["publication_types"],
    filter_settings=QualityFilter(use_cochrane_assessment=True),
)

result.assessment_tier        # 4 (on success)
result.study_design           # from the free Tier 1 metadata pass
result.bias_risk              # five domains, collapsed from the nine
result.cochrane_assessment    # the full table + RoB, or None if Tier 4 failed
```

---

## Cochrane Formatters

`bmlib.quality.cochrane_formatter` renders `cochrane_models` objects as Cochrane Handbook tables. Module-level functions only, no classes. **None are re-exported from `bmlib.quality`** — import from the submodule.

| Function | Returns |
|----------|---------|
| `format_study_characteristics_markdown(study_chars: CochraneStudyCharacteristics) -> str` | The two-column *Study characteristics* table, headed by `### {study_id}`. |
| `format_risk_of_bias_markdown(rob: CochraneRiskOfBias) -> str` | The three-column *Risk of bias* table (Bias / Authors' judgement / Support for judgement). |
| `format_complete_assessment_markdown(assessment: CochraneStudyAssessment) -> str` | Characteristics + risk of bias, plus an *Assessment Summary* block when a score or evidence level is set, and a *Notes* block when `assessment_notes` is non-empty. |
| `format_multiple_assessments_markdown(assessments: list[CochraneStudyAssessment], title: str = "Characteristics of included studies") -> str` | One `## {title}` document, each assessment separated by a `---` rule. |
| `format_risk_of_bias_summary_markdown(assessments: list[CochraneStudyAssessment]) -> str` | A cross-study matrix (domains × studies) using `+` (low), `-` (high), `?` (unclear), with a legend. Returns `"No assessments to summarize."` for an empty list. |
| `format_study_characteristics_html(study_chars) -> str` | The same table as HTML. Values are HTML-escaped and newlines become `<br>`. |
| `format_risk_of_bias_html(rob) -> str` | The RoB table as HTML, with per-cell classes `judgement-low` / `judgement-high` / `judgement-unclear`. |
| `get_cochrane_css() -> str` | The `COCHRANE_CSS` stylesheet (a complete `<style>` element) for the HTML output. |

Markdown formatting tokens are exposed as `MD_BOLD_START`, `MD_BOLD_END`, `MD_ITALIC_START`, `MD_ITALIC_END`.

`format_risk_of_bias_summary_markdown()` transposes `to_list()` across assessments with `zip(..., strict=True)`, so every assessment must carry all nine domains — which `CochraneRiskOfBias` guarantees by construction.

**Example — build an assessment and render it:**

```python
from bmlib.quality import (
    CochraneInterventions,
    CochraneNotes,
    CochraneOutcomes,
    CochraneParticipants,
    CochraneStudyAssessment,
    CochraneStudyCharacteristics,
    RiskOfBiasItem,
    create_default_cochrane_risk_of_bias,
)
from bmlib.quality.cochrane_formatter import (
    format_complete_assessment_markdown,
    format_risk_of_bias_summary_markdown,
)
from bmlib.quality.cochrane_models import ROB_JUDGEMENT_LOW

characteristics = CochraneStudyCharacteristics(
    study_id="Andrei 2011",
    methods="Randomised controlled trial, parallel groups, 12-month follow-up",
    participants=CochraneParticipants(
        setting="Three university hospitals, Romania",
        population="Adults aged 65+ admitted with acute heart failure",
        total_participants=240,
        group_sizes={"intervention": 120, "control": 120},
        baseline_characteristics_reported=True,
    ),
    interventions=CochraneInterventions(
        description="Nurse-led hospital-at-home programme versus usual inpatient care",
        duration="30 days",
    ),
    outcomes=CochraneOutcomes(
        description="All-cause mortality, readmission, cost per patient",
        primary_outcomes=["All-cause mortality at 12 months"],
    ),
    notes=CochraneNotes(
        follow_up_periods=["6 months", "12 months"],
        funding_source="University research grant",
        trial_registration="NCT01234567",
    ),
    pmid="21234567",
)

# Start from all-unclear, then overwrite the domains you can judge.
rob = create_default_cochrane_risk_of_bias()
rob.random_sequence_generation = RiskOfBiasItem(
    domain="Random sequence generation",
    bias_type="selection bias",
    judgement=ROB_JUDGEMENT_LOW,
    support_for_judgement="Computer-generated randomisation sequence.",
)

assessment = CochraneStudyAssessment(
    study_characteristics=characteristics,
    risk_of_bias=rob,
    overall_quality_score=7.5,
    overall_confidence=0.8,
    evidence_level="Level 2 (moderate-high)",
    assessment_notes=["Allocation concealment not described."],
)

print(format_complete_assessment_markdown(assessment))
print(rob.get_summary_counts())          # {'Low risk': 1, 'High risk': 0, 'Unclear risk': 8}
print(format_risk_of_bias_summary_markdown([assessment]))
```

The `format_complete_assessment_markdown()` output begins:

```markdown
### Andrei 2011

*Study characteristics*

| **Characteristic** | **Description** |
|---|---|
| Methods | Randomised controlled trial, parallel groups, 12-month follow-up |
| Participants | Setting: Three university hospitals, Romania |
| | Adults aged 65+ admitted with acute heart failure |
| | N=240 (intervention: 120, control: 120) |
| Interventions | Nurse-led hospital-at-home programme versus usual inpatient care |
| Outcomes | All-cause mortality, readmission, cost per patient |
| Notes | Follow-up at 6 months, 12 months |
| | Funding: University research grant |
| | Trial registration: NCT01234567 |
```

For HTML output, emit `get_cochrane_css()` once per page and then the table fragments:

```python
from bmlib.quality.cochrane_formatter import (
    format_risk_of_bias_html,
    format_study_characteristics_html,
    get_cochrane_css,
)

page = "\n".join([
    get_cochrane_css(),
    format_study_characteristics_html(assessment.study_characteristics),
    format_risk_of_bias_html(assessment.risk_of_bias),
])
```

---

## Rule-Based Extractors

`bmlib.quality.extractors` provides stateless, **LLM-free** pure functions that estimate study characteristics with keyword and regex heuristics. They cost nothing and need no network access.

> **Standalone.** No tier calls these, and their output is a [`DimensionScore`](#dimensionscore), not a `QualityAssessment` — there is no conversion between the two. Use them as your own pre-filter or offline fallback.

### Input Shape

Every extractor takes a `document` dict. Three keys are recognised — `full_text`, `abstract`, `methods_text` — and all are optional.

```python
def prepare_extractor_search_text(document: dict[str, Any]) -> str
```

Returns `full_text` when it is present and longer than the abstract; otherwise `f"{abstract} {methods_text}"`.

### Study Type Detection

```python
def extract_study_type(
    document: dict[str, Any],
    keywords_config: dict[str, list[str]] | None = None,
    hierarchy_config: dict[str, float] | None = None,
    priority_order: list[str] | None = None,
    exclusions_config: dict[str, list[str]] | None = None,
) -> DimensionScore
```

Tries each study type in `STUDY_TYPE_PRIORITY` order and returns on the first keyword match that survives exclusion checking. Keywords match whole words with an optional trailing plural `"s"`, so `"RCT"` matches `"RCTs"` but not `"infarct"`. Every occurrence of a keyword is tried, so one excluded mention does not suppress a later clean one.

`quasi_experimental` is checked **before** `rct` so that "non-randomized trial" is not captured by the RCT keyword "randomized trial". `STUDY_TYPE_EXCLUSIONS` provides a second guard: for `rct`, patterns such as `"non-randomized"`, `"not randomised"`, and `"quasi-experimental"` appearing within `EXCLUSION_CONTEXT_WINDOW` (50) characters before the keyword reject the match.

When nothing matches, the result is a `DimensionScore` of `5.0` with a detail whose `extracted_value` is `"unknown"`.

> **The extracted study type is not a `StudyDesign`.** `extract_study_type()` returns raw strings from its own vocabulary, three of which — `"quasi_experimental"`, `"pilot_feasibility"`, `"interventional_single_arm"` — have **no** corresponding `StudyDesign` member. There is no 1:1 mapping, and `STUDY_DESIGN_MAPPING` will resolve those three to `StudyDesign.UNKNOWN`. Handle them explicitly if you need to bridge to the tiered pipeline's vocabulary.

**Configuration constants** (all overridable via the keyword arguments):

| Constant | Description |
|----------|-------------|
| `STUDY_TYPE_PRIORITY` | 12 study types in resolution order, highest evidence first. |
| `DEFAULT_STUDY_TYPE_KEYWORDS` | `dict[str, list[str]]` of trigger phrases per study type. |
| `STUDY_TYPE_EXCLUSIONS` | Patterns that invalidate a match (currently only for `rct`). |
| `EXCLUSION_CONTEXT_WINDOW` | `50` — characters before a keyword searched for exclusions. |
| `DEFAULT_STUDY_TYPE_HIERARCHY` | 15 study types → scores 0–10. Includes `scoping_review`, `narrative_review`, and `expert_opinion`, which have no keywords and so are only reachable via a custom `keywords_config`. |

### Sample Size Extraction

```python
def extract_sample_size_dimension(
    document: dict[str, Any],
    scoring_config: dict[str, float] | None = None,
) -> DimensionScore
```

Default `scoring_config`:

```python
{"log_multiplier": 2.0, "power_calculation_bonus": 2.0, "ci_reported_bonus": 0.5}
```

Scores `log10(n) * log_multiplier`, then adds the power-calculation and confidence-interval bonuses where those signals are present. The running score is capped at `10.0` after each bonus. When no sample size is found, the score is `0.0` and the single detail has `extracted_value="not_found"`.

### Helper Functions

| Function | Description |
|----------|-------------|
| `find_sample_size(text, min_n=5, max_n=1_000_000) -> int \| None` | Runs the eight `SAMPLE_SIZE_PATTERNS` case-insensitively and returns the **largest** match within bounds, or `None`. |
| `calculate_sample_size_score(n, log_multiplier=2.0) -> float` | `log10(n) * log_multiplier`, clamped to 0–10. Returns `0.0` for `n <= 0`. |
| `has_power_calculation(text) -> bool` | Case-insensitive substring test against `POWER_CALCULATION_KEYWORDS`. |
| `find_power_calc_context(text) -> str` | Snippet around the first of the three leading power-calculation keywords, or `""`. |
| `has_ci_reporting(text) -> bool` | Tests `CI_PATTERNS`. The bare bracket/range forms require decimal points, so citation markers like `[12, 15]` and year ranges like `(2010-2015)` do not count. |
| `has_exclusion_pattern(text, keyword, exclusion_patterns, context_window=EXCLUSION_CONTEXT_WINDOW, keyword_pos=None) -> bool` | Whether an exclusion pattern appears just before `keyword`. Checks the first occurrence unless `keyword_pos` is given. |
| `extract_text_context(text, keyword, context_chars=50, keyword_pos=None) -> str` | Snippet around an occurrence, with ellipses where truncated. Returns `""` if absent. |
| `get_extracted_sample_size(dimension_score) -> int \| None` | Reads the numeric `n` back out of a sample-size `DimensionScore` (first detail only). |
| `get_extracted_study_type(dimension_score) -> str \| None` | Reads the study-type string back out of a study-design `DimensionScore` (first detail only). |

**Example — extract both dimensions with the audit trail:**

```python
from bmlib.quality import extract_sample_size_dimension, extract_study_type
from bmlib.quality.extractors import get_extracted_sample_size, get_extracted_study_type

document = {
    "abstract": (
        "In this randomized controlled trial we enrolled 450 patients with "
        "type 2 diabetes. A power calculation indicated 400 participants were "
        "required. The hazard ratio was 0.72 (95% CI 0.55-0.94)."
    ),
}

design = extract_study_type(document)
print(design.dimension_name, design.score)      # study_design 8.0
print(get_extracted_study_type(design))         # rct

size = extract_sample_size_dimension(document)
print(size.dimension_name, round(size.score, 2))  # sample_size 7.81
print(get_extracted_sample_size(size))            # 450

for detail in size.details:
    print(f"{detail.component}: {detail.extracted_value} "
          f"(+{detail.score_contribution}) — {detail.reasoning}")
# extracted_n: 450 (+5.306425027550687) — Log10(450) * 2.0 = 5.31
# power_calculation: yes (+2.0) — Power calculation mentioned, bonus +2.0
# ci_reporting: yes (+0.5) — Confidence intervals reported, bonus +0.5
```

Exclusion guarding in action:

```python
>>> get_extracted_study_type(extract_study_type({"abstract": "A non-randomized trial of X"}))
'quasi_experimental'
```

---

## Scoring Models

`bmlib.quality.scoring_models` holds the audit-trail dataclasses that the extractors populate. There are no enums — the dimension vocabulary is a set of string constants.

> **Standalone.** `QualityAssessment` does not carry `DimensionScore` objects, and nothing converts between them.

### Dimension Constants

```python
DIMENSION_STUDY_DESIGN = "study_design"
DIMENSION_SAMPLE_SIZE = "sample_size"
DIMENSION_METHODOLOGICAL_QUALITY = "methodological_quality"
DIMENSION_RISK_OF_BIAS = "risk_of_bias"
DIMENSION_REPLICATION_STATUS = "replication_status"

ALL_DIMENSIONS = [...]  # the five above, in that order
```

Only the first two are produced by the shipped extractors; the other three are reserved for callers implementing their own dimensions. None of these constants are re-exported from `bmlib.quality`.

### `AssessmentDetail`

One audit-trail entry for a scored component.

```python
@dataclass
class AssessmentDetail:
    dimension: str                    # e.g. "sample_size"
    component: str                    # e.g. "power_calculation"
    extracted_value: str | None       # e.g. "450"
    score_contribution: float         # points contributed
    evidence_text: str | None = None  # excerpt from the paper
    reasoning: str | None = None      # explanation for the score
```

| Method | Description |
|--------|-------------|
| `to_dict() -> dict[str, Any]` | Serialise all six fields. |
| `from_dict(data: dict) -> AssessmentDetail` | Deserialise. `dimension`, `component`, and `score_contribution` are required keys. |

Note that `extracted_value` is a `str | None`: numeric values are stored as strings (hence `get_extracted_sample_size()` converting back with `.isdigit()`).

### `DimensionScore`

One dimension's score plus its contributing entries.

```python
@dataclass
class DimensionScore:
    dimension_name: str
    score: float                                    # typically 0–10
    details: list[AssessmentDetail] = field(default_factory=list)
```

| Method | Description |
|--------|-------------|
| `add_detail(component, value, contribution, evidence=None, reasoning=None) -> None` | Append an `AssessmentDetail`, stamping `dimension` from `dimension_name`. Does **not** modify `score` — adjust it yourself. |
| `to_dict() -> dict[str, Any]` | Serialise, including all details. |
| `from_dict(data: dict) -> DimensionScore` | Deserialise; `details` defaults to `[]` when absent. |

**Example — a custom dimension with a round-trip:**

```python
from bmlib.quality import DimensionScore
from bmlib.quality.scoring_models import DIMENSION_RISK_OF_BIAS

dimension = DimensionScore(dimension_name=DIMENSION_RISK_OF_BIAS, score=0.0)
dimension.add_detail(
    component="allocation_concealment",
    value="described",
    contribution=3.0,
    evidence="sealed opaque envelopes were used",
    reasoning="Adequate concealment described",
)
dimension.score += 3.0

restored = DimensionScore.from_dict(dimension.to_dict())
assert restored == dimension
```
