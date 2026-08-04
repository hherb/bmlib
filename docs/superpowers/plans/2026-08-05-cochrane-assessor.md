# Cochrane Assessor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `bmlib.quality` a producer of `CochraneStudyAssessment` objects, collapse its nine risk-of-bias domains onto the five the rest of the package speaks, and route both through `QualityManager`.

**Architecture:** `CochraneAssessor(BaseAgent)` sends one JSON-mode prompt and parses the reply into the existing Cochrane models. Text larger than the configured context is first reduced to an evidence digest by `LLMChunkProcessor` (the assessor passes itself as the agent), so the nine-domain judgement is always made once, over content that fits. `collapse_risk_of_bias()` is a pure function deriving the 9→5 reduction from each item's own `bias_type`. `QualityManager` gains a flag that runs the assessor and *enriches* the free Tier 1 metadata result rather than replacing it.

**Tech Stack:** Python ≥3.11, `bmlib.agents.BaseAgent`, `bmlib.context_processor.LLMChunkProcessor`, pytest with `unittest.mock`. No new dependencies — everything used is already in bmlib's core or existing extras.

**Design spec:** [`docs/superpowers/specs/2026-08-05-cochrane-assessor-design.md`](../specs/2026-08-05-cochrane-assessor-design.md). Read it before Task 1; it carries the reasoning this plan only executes.

## Global Constraints

- **Branch:** `feature/cochrane-assessor` (already created, spec already committed). Do not work on `main`.
- **AGPL-3 licence header** at the top of every new source file. Copy it verbatim from `bmlib/quality/quality_agent.py` lines 1–15.
- **`from __future__ import annotations`** as the first statement after the module docstring in every new file.
- **Type hints on every signature**, parameters and return. Lowercase builtin generics (`list[str]`, `dict[str, Any]`, `X | None`).
- **Google-style docstrings** on every public module, class and function, consistent within a module — match `bmlib/quality/quality_agent.py`.
- **New dataclass fields are declared LAST.** `Publication.pmcid`, `BaseAgent.embedding_model` and `TransparencyResult.unknown_reason` all are, because downstream projects construct these positionally and any other placement silently shifts every following argument. This applies to `CochraneStudyAssessment.condensed_from_chars`, `QualityAssessment.cochrane_assessment` and `QualityFilter.use_cochrane_assessment`.
- **Tests:** `uv run pytest tests/ -v`. Never bare `pip`; `uv` only.
- **Lint with the CI-pinned ruff, not `.venv`'s:** `uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .`. The `.venv` copy is 0.6.5 and false-flags `UP038` on `ollama.py`.
- **Line length 100.**
- **Every task ends green:** full suite passing and both ruff commands clean before the commit.
- **Test naming:** these files use long behavioural names (`test_a_malformed_accession_never_reaches_a_url`), not `test_1`. Each defect fix below names its own test; keep those names — they are referenced from HANDOVER.md.

---

### Task 1: The 9→5 collapse and the condensation provenance field

**Files:**
- Modify: `bmlib/quality/cochrane_models.py` (add an import of `BiasRisk`, three module constants, `collapse_risk_of_bias()`, and one field on `CochraneStudyAssessment`)
- Test: `tests/test_cochrane.py` (append two test classes)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `bmlib.quality.cochrane_models.collapse_risk_of_bias(rob: CochraneRiskOfBias) -> BiasRisk` — raises `ValueError` on an unrecognised `bias_type`.
  - `CochraneStudyAssessment.condensed_from_chars: int | None` — declared last, round-trips through `to_dict()`/`from_dict()`.

`data_models.py` imports nothing from `quality`, so importing `BiasRisk` into `cochrane_models.py` creates no cycle. The dependency points this way — from the richer model to the simpler one — so `data_models.py` stays free of Cochrane knowledge.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cochrane.py`. Add `BiasRisk` to the existing `from bmlib.quality.data_models import ...` block if present, otherwise add the import; add `collapse_risk_of_bias` to the existing `from bmlib.quality.cochrane_models import (...)` block.

```python
class TestCollapsingTheNineDomainsOntoTheFive:
    """``collapse_risk_of_bias()`` reduces Cochrane's nine domains to the
    five ``BiasRisk`` speaks, deriving the grouping from each item's own
    ``bias_type`` rather than a hard-coded per-domain table."""

    def test_all_low_collapses_to_all_low(self) -> None:
        rob = create_default_cochrane_risk_of_bias()
        for item in rob.to_list():
            item.judgement = ROB_JUDGEMENT_LOW

        assert collapse_risk_of_bias(rob) == BiasRisk(
            selection="low",
            performance="low",
            detection="low",
            attrition="low",
            reporting="low",
        )

    def test_the_worst_judgement_in_a_group_wins(self) -> None:
        """Four domains feed ``selection``; one high makes the group high."""
        rob = create_default_cochrane_risk_of_bias()
        for item in rob.to_list():
            item.judgement = ROB_JUDGEMENT_LOW
        rob.allocation_concealment.judgement = ROB_JUDGEMENT_HIGH

        assert collapse_risk_of_bias(rob).selection == "high"

    def test_unclear_outranks_low(self) -> None:
        """An unreported domain is not a clean bill of health: you cannot
        claim low selection-bias risk when allocation concealment was never
        described."""
        rob = create_default_cochrane_risk_of_bias()
        for item in rob.to_list():
            item.judgement = ROB_JUDGEMENT_LOW
        rob.allocation_concealment.judgement = ROB_JUDGEMENT_UNCLEAR

        assert collapse_risk_of_bias(rob).selection == "unclear"

    def test_high_outranks_unclear(self) -> None:
        rob = create_default_cochrane_risk_of_bias()
        rob.random_sequence_generation.judgement = ROB_JUDGEMENT_HIGH
        # The other three selection domains are Unclear by default.

        assert collapse_risk_of_bias(rob).selection == "high"

    def test_the_two_detection_domains_collapse_together(self) -> None:
        rob = create_default_cochrane_risk_of_bias()
        for item in rob.to_list():
            item.judgement = ROB_JUDGEMENT_LOW
        rob.blinding_outcome_assessment_subjective.judgement = ROB_JUDGEMENT_HIGH

        collapsed = collapse_risk_of_bias(rob)
        assert collapsed.detection == "high"
        # ...and nothing else moved with it.
        assert collapsed.performance == "low"
        assert collapsed.attrition == "low"

    def test_a_judgement_in_another_casing_still_collapses(self) -> None:
        """A hand-built item carrying "low" rather than "Low risk" is
        normalised through ``RiskOfBiasJudgement.from_string()`` instead of
        being counted as unclear."""
        rob = create_default_cochrane_risk_of_bias()
        for item in rob.to_list():
            item.judgement = "low"

        assert collapse_risk_of_bias(rob).selection == "low"

    def test_an_unrecognised_bias_type_raises(self) -> None:
        """``RiskOfBiasItem`` is public and a caller may build one with any
        ``bias_type``.  Dropping it silently would emit a ``BiasRisk`` that
        looks complete and is not — the same reason
        ``_Analysis.note_data_level()`` raises on an unknown level."""
        rob = create_default_cochrane_risk_of_bias()
        rob.selective_reporting.bias_type = "funding bias"

        with pytest.raises(ValueError, match="funding bias"):
            collapse_risk_of_bias(rob)

    def test_every_default_domain_maps_to_a_domain_the_collapse_knows(self) -> None:
        """The guard above must never fire for bmlib's own nine domains."""
        collapse_risk_of_bias(create_default_cochrane_risk_of_bias())


class TestTheCondensationProvenanceField:
    def test_it_defaults_to_none(self) -> None:
        """``None`` means the paper went to the model whole."""
        assert _sample_assessment().condensed_from_chars is None

    def test_it_round_trips(self) -> None:
        assessment = _sample_assessment()
        assessment.condensed_from_chars = 91_234

        restored = CochraneStudyAssessment.from_dict(assessment.to_dict())
        assert restored.condensed_from_chars == 91_234

    def test_a_dict_without_the_key_loads_as_none(self) -> None:
        data = _sample_assessment().to_dict()
        del data["condensed_from_chars"]

        assert CochraneStudyAssessment.from_dict(data).condensed_from_chars is None
```

`tests/test_cochrane.py` already defines `_sample_assessment()` and already imports `ROB_JUDGEMENT_LOW`, `ROB_JUDGEMENT_HIGH`, `ROB_JUDGEMENT_UNCLEAR`, `CochraneStudyAssessment` and `create_default_cochrane_risk_of_bias`. It does **not** yet import `pytest` or `BiasRisk` — add both.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cochrane.py -v -k "Collapsing or Provenance"`
Expected: FAIL — `ImportError: cannot import name 'collapse_risk_of_bias'`.

- [ ] **Step 3: Add the collapse to `bmlib/quality/cochrane_models.py`**

Add to the imports at the top of the file, below `from typing import Any`:

```python
from bmlib.quality.data_models import BiasRisk
```

Add these constants in the "Constants" section, after `VALID_ROB_JUDGEMENTS`:

```python
# ``BiasRisk``'s vocabulary, weakest first.  The order *is* the severity
# ranking used when several Cochrane domains collapse onto one ``BiasRisk``
# field: "unclear" outranks "low" because an unreported domain is not a clean
# bill of health — you cannot claim low selection-bias risk when allocation
# concealment was never described.
_SEVERITY_ORDER = ("low", "unclear", "high")

# Cochrane judgement string → the word ``BiasRisk`` uses for it.
_JUDGEMENT_TO_BIAS_RISK = {
    ROB_JUDGEMENT_LOW: "low",
    ROB_JUDGEMENT_UNCLEAR: "unclear",
    ROB_JUDGEMENT_HIGH: "high",
}

# ``RiskOfBiasItem.bias_type`` → the ``BiasRisk`` field it feeds.  The 9→5
# grouping is read off the items themselves rather than written out per
# domain, so a tenth domain of an existing type collapses correctly without
# this function being touched.
_BIAS_TYPE_TO_FIELD = {
    "selection bias": "selection",
    "performance bias": "performance",
    "detection bias": "detection",
    "attrition bias": "attrition",
    "reporting bias": "reporting",
}
```

Add the function at the end of the file, after `create_default_cochrane_risk_of_bias()`:

```python
def collapse_risk_of_bias(rob: CochraneRiskOfBias) -> BiasRisk:
    """Reduce the nine Cochrane domains to the five :class:`BiasRisk` domains.

    Each :class:`RiskOfBiasItem` already names its target through
    ``bias_type``, so the grouping is derived rather than hard-coded: four
    domains feed ``selection``, two feed ``detection``, and one each feeds
    ``performance``, ``attrition`` and ``reporting``.

    Where several domains collapse onto one field the **worst wins**, ranked
    ``high`` > ``unclear`` > ``low``.  Judgements are normalised through
    :meth:`RiskOfBiasJudgement.from_string` first, so an item carrying
    ``"low"`` rather than ``"Low risk"`` is not miscounted as unclear.

    Args:
        rob: The nine-domain assessment to reduce.

    Returns:
        The five-domain equivalent.

    Raises:
        ValueError: If any item's ``bias_type`` is not one of the five
            Cochrane categories.  Silently dropping it would return a
            ``BiasRisk`` that looks complete and is not.
    """
    worst: dict[str, int] = {}

    for item in rob.to_list():
        field = _BIAS_TYPE_TO_FIELD.get(item.bias_type.strip().lower())
        if field is None:
            raise ValueError(
                f"Cannot collapse domain {item.domain!r}: unknown bias_type "
                f"{item.bias_type!r}. Expected one of {sorted(_BIAS_TYPE_TO_FIELD)}."
            )
        judgement = RiskOfBiasJudgement.from_string(item.judgement).value
        rank = _SEVERITY_ORDER.index(_JUDGEMENT_TO_BIAS_RISK[judgement])
        if rank > worst.get(field, -1):
            worst[field] = rank

    return BiasRisk(**{field: _SEVERITY_ORDER[rank] for field, rank in worst.items()})
```

- [ ] **Step 4: Add the provenance field**

In `CochraneStudyAssessment`, add the field **after** `assessment_version` — last on the dataclass:

```python
    assessment_version: str = "2.0.0"

    # Set to the original character count when the text was condensed by
    # ``bmlib.context_processor`` before assessment; ``None`` when the paper
    # went to the model whole.  A judgement made over an LLM-condensed digest
    # is weaker evidence than one made over the paper, so it says so rather
    # than leaving the caller to infer it.  Declared last: downstream projects
    # construct this positionally.
    condensed_from_chars: int | None = None
```

Add it to `to_dict()`, after `"assessment_version"`:

```python
            "condensed_from_chars": self.condensed_from_chars,
```

And to `from_dict()`, after the `assessment_version` line:

```python
            condensed_from_chars=data.get("condensed_from_chars"),
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cochrane.py -v`
Expected: PASS, including the pre-existing tests in that file.

- [ ] **Step 6: Run the full suite and lint**

```bash
uv run pytest tests/ -q
uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .
```
Expected: all pass, ruff clean.

- [ ] **Step 7: Commit**

```bash
git add bmlib/quality/cochrane_models.py tests/test_cochrane.py
git commit -m "feat(quality): collapse the nine Cochrane RoB domains onto BiasRisk

The 9-to-5 grouping is derived from each item's own bias_type rather
than written out per domain, and reduces worst-wins with unclear
outranking low: an unreported domain is not a clean bill of health.
An unrecognised bias_type raises rather than silently returning a
BiasRisk that looks complete.

Adds CochraneStudyAssessment.condensed_from_chars, declared last."
```

---

### Task 2: `CochraneAssessor` — the single-shot assessment

**Files:**
- Create: `bmlib/quality/cochrane_assessor.py`
- Test: `tests/test_cochrane_assessor.py` (new)

**Interfaces:**
- Consumes: `collapse_risk_of_bias` is *not* used here (Task 5 uses it). This task uses only the pre-existing Cochrane models plus `RiskOfBiasJudgement`.
- Produces:
  - `CochraneAssessor(llm: LLMClient, model: str, template_engine: TemplateEngine | None = None, temperature: float = 0.1, max_tokens: int = 4096, condense_config: ProcessingConfig | None = None)`
  - `CochraneAssessor.assess(title: str | None, text: str | None, *, study_id: str | None = None, pmid: str | None = None, doi: str | None = None, document_id: int | None = None, min_confidence: float = 0.0) -> CochraneStudyAssessment | None`
  - Module constants `DEFAULT_CONDENSE_THRESHOLD_CHARS: int` and `_ASSESSMENT_ATTEMPTS: int`.

Condensation is stubbed out in this task — `assess()` uses the text as given — and Task 3 fills it in. Everything else about `assess()` is final here.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cochrane_assessor.py` with the AGPL header (copy lines 1–15 from `tests/test_cochrane.py`), then:

```python
"""Tests for the Cochrane assessment agent."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

from bmlib.llm.data_types import LLMResponse
from bmlib.quality.cochrane_assessor import CochraneAssessor
from bmlib.quality.cochrane_models import (
    ROB_JUDGEMENT_HIGH,
    ROB_JUDGEMENT_LOW,
    ROB_JUDGEMENT_UNCLEAR,
)


def _full_response(**overrides: Any) -> dict[str, Any]:
    """A well-formed model reply, before any per-test overrides."""
    domains = [
        "random_sequence_generation",
        "allocation_concealment",
        "baseline_outcome_measurements",
        "baseline_characteristics",
        "blinding_participants_personnel",
        "blinding_outcome_assessment_subjective",
        "blinding_outcome_assessment_objective",
        "incomplete_outcome_data",
        "selective_reporting",
    ]
    data: dict[str, Any] = {
        "study_characteristics": {
            "methods": "Parallel randomised trial",
            "participants": {"setting": "Romania", "population": "Chronic heart failure"},
            "interventions": {"description": "Hospital at home"},
            "outcomes": {"description": "Mortality, cost"},
            "notes": {"funding_source": "None declared"},
        },
        "risk_of_bias": {
            d: {"judgement": ROB_JUDGEMENT_LOW, "support_for_judgement": "Computer-generated"}
            for d in domains
        },
        "overall_confidence": 0.8,
        "evidence_level": "Level 2 (moderate-high)",
        "assessment_notes": ["A note"],
    }
    data.update(overrides)
    return data


def make_assessor(*replies: str, **kwargs: Any) -> CochraneAssessor:
    """An assessor whose client returns *replies* in order, repeating the last."""
    llm = MagicMock()
    responses = [
        LLMResponse(content=reply, model="test:model", input_tokens=1, output_tokens=1)
        for reply in (replies or (json.dumps(_full_response()),))
    ]
    llm.chat = MagicMock(side_effect=responses + [responses[-1]] * 20)
    return CochraneAssessor(llm=llm, model="test:model", **kwargs)


class TestAWellFormedAssessment:
    def test_it_returns_a_complete_assessment(self) -> None:
        assessment = make_assessor().assess("A trial", "Full text", study_id="Andrei 2011")

        assert assessment is not None
        assert assessment.study_id == "Andrei 2011"
        assert assessment.study_characteristics.methods == "Parallel randomised trial"
        assert assessment.risk_of_bias.allocation_concealment.judgement == ROB_JUDGEMENT_LOW
        assert assessment.overall_confidence == 0.8
        assert assessment.evidence_level == "Level 2 (moderate-high)"

    def test_the_nine_domains_carry_their_cochrane_metadata(self) -> None:
        """The model supplies judgement and support; the domain name, bias
        type and outcome type are bmlib's, not the model's."""
        rob = make_assessor().assess("A trial", "Full text").risk_of_bias

        assert rob.random_sequence_generation.domain == "Random sequence generation"
        assert rob.random_sequence_generation.bias_type == "selection bias"
        assert rob.blinding_outcome_assessment_subjective.outcome_type == "subjective"
        assert rob.blinding_outcome_assessment_objective.outcome_type == "objective"
        assert rob.selective_reporting.bias_type == "reporting bias"

    def test_uncondensed_text_reports_no_condensation(self) -> None:
        assessment = make_assessor().assess("A trial", "Short text")

        assert assessment.condensed_from_chars is None


class TestIdentity:
    def test_the_caller_supplied_study_id_wins(self) -> None:
        assessment = make_assessor().assess("T", "text", study_id="Smith 2020", document_id=7)
        assert assessment.study_id == "Smith 2020"

    def test_a_document_id_is_the_first_fallback(self) -> None:
        """No surname is guessed from an author list.  Upstream derived one
        with ``first_author.split()[-1]``, which reads "van der Berg" as
        "Berg" and any "Surname, Given" string backwards."""
        assessment = make_assessor().assess("T", "text", document_id=7)
        assert assessment.study_id == "Study 7"

    def test_the_title_is_the_last_fallback(self) -> None:
        assessment = make_assessor().assess("A trial of things", "text")
        assert assessment.study_id == "A trial of things"

    def test_identifiers_reach_the_characteristics_table(self) -> None:
        assessment = make_assessor().assess(
            "T", "text", pmid="12345678", doi="10.1/x", document_id=7
        )
        chars = assessment.study_characteristics
        assert (chars.pmid, chars.doi, chars.document_id) == ("12345678", "10.1/x", 7)


class TestNothingToAssess:
    def test_a_blank_title_and_text_returns_none_without_calling_the_model(self) -> None:
        """Left to itself the model returns a fully-formed nine-domain
        judgement for a paper it was told nothing about."""
        assessor = make_assessor()
        assert assessor.assess(None, None) is None
        assessor.llm.chat.assert_not_called()

    def test_a_title_alone_is_still_assessed(self) -> None:
        assert make_assessor().assess("A trial", None) is not None


class TestJudgementsAreNormalised:
    """Upstream wrote the model's raw string into ``RiskOfBiasItem.judgement``.
    A model answering "low" stores an invalid value: ``__post_init__`` warns,
    ``get_summary_counts()`` then skips that domain entirely, and the summary
    silently reports eight domains instead of nine."""

    def test_a_lowercase_judgement_is_normalised(self) -> None:
        reply = _full_response()
        reply["risk_of_bias"]["allocation_concealment"]["judgement"] = "low"

        rob = make_assessor(json.dumps(reply)).assess("T", "text").risk_of_bias
        assert rob.allocation_concealment.judgement == ROB_JUDGEMENT_LOW

    def test_an_underscored_judgement_is_normalised(self) -> None:
        reply = _full_response()
        reply["risk_of_bias"]["selective_reporting"]["judgement"] = "high_risk"

        rob = make_assessor(json.dumps(reply)).assess("T", "text").risk_of_bias
        assert rob.selective_reporting.judgement == ROB_JUDGEMENT_HIGH

    def test_every_domain_is_counted_in_the_summary(self) -> None:
        """The consequence the normalisation exists to prevent."""
        reply = _full_response()
        reply["risk_of_bias"]["allocation_concealment"]["judgement"] = "low"

        rob = make_assessor(json.dumps(reply)).assess("T", "text").risk_of_bias
        assert sum(rob.get_summary_counts().values()) == 9

    def test_an_unintelligible_judgement_becomes_unclear(self) -> None:
        reply = _full_response()
        reply["risk_of_bias"]["incomplete_outcome_data"]["judgement"] = "probably fine"

        rob = make_assessor(json.dumps(reply)).assess("T", "text").risk_of_bias
        assert rob.incomplete_outcome_data.judgement == ROB_JUDGEMENT_UNCLEAR


class TestConfidence:
    def test_a_confidence_outside_the_range_is_clamped(self) -> None:
        """A model reporting 1.4 would outrank every honest result and defeat
        ``min_confidence``."""
        assessment = make_assessor(json.dumps(_full_response(overall_confidence=1.4))).assess(
            "T", "text"
        )
        assert assessment.overall_confidence == 1.0

    def test_a_negative_confidence_is_clamped(self) -> None:
        assessment = make_assessor(json.dumps(_full_response(overall_confidence=-0.2))).assess(
            "T", "text"
        )
        assert assessment.overall_confidence == 0.0

    def test_an_unusable_confidence_becomes_none(self) -> None:
        assessment = make_assessor(json.dumps(_full_response(overall_confidence="high"))).assess(
            "T", "text"
        )
        assert assessment.overall_confidence is None

    def test_min_confidence_is_honoured(self) -> None:
        """Upstream declared DEFAULT_MIN_CONFIDENCE = 0.4, threaded it through
        the signature, and never read it."""
        assessor = make_assessor(json.dumps(_full_response(overall_confidence=0.3)))
        assert assessor.assess("T", "text", min_confidence=0.5) is None

    def test_min_confidence_defaults_to_dropping_nothing(self) -> None:
        assessor = make_assessor(json.dumps(_full_response(overall_confidence=0.0)))
        assert assessor.assess("T", "text") is not None


class TestAMissingRiskOfBiasSection:
    """A Cochrane assessment without any risk-of-bias section is not a
    Cochrane assessment.  Upstream accepted it and fabricated nine "Unclear
    risk" defaults out of nothing."""

    def test_a_response_with_no_risk_of_bias_block_is_rejected(self) -> None:
        reply = _full_response()
        del reply["risk_of_bias"]

        assert make_assessor(json.dumps(reply)).assess("T", "text") is None

    def test_a_response_with_an_empty_risk_of_bias_block_is_rejected(self) -> None:
        assert make_assessor(json.dumps(_full_response(risk_of_bias={}))).assess("T", "text") is None

    def test_it_is_retried_once_before_giving_up(self) -> None:
        """Two whole attempts, not three: a model that omits the section twice
        has misread the prompt, and the bound keeps the worst case at six
        model calls rather than nine."""
        bad = _full_response()
        del bad["risk_of_bias"]
        assessor = make_assessor(json.dumps(bad), json.dumps(_full_response()))

        assert assessor.assess("T", "text") is not None
        assert assessor.llm.chat.call_count == 2

    def test_a_single_missing_domain_defaults_to_unclear(self) -> None:
        """Honest per-domain degradation of an otherwise good answer, which is
        a different thing from fabricating the whole section."""
        reply = _full_response()
        del reply["risk_of_bias"]["selective_reporting"]

        rob = make_assessor(json.dumps(reply)).assess("T", "text").risk_of_bias
        assert rob.selective_reporting.judgement == ROB_JUDGEMENT_UNCLEAR
        assert "insufficient information" in rob.selective_reporting.support_for_judgement


class TestAnUnusableResponse:
    def test_an_unparseable_response_returns_none(self) -> None:
        assert make_assessor("not json at all").assess("T", "text") is None

    def test_a_top_level_array_returns_none(self) -> None:
        assert make_assessor("[1, 2, 3]").assess("T", "text") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cochrane_assessor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bmlib.quality.cochrane_assessor'`. That is the correct red for a new module.

- [ ] **Step 3: Create `bmlib/quality/cochrane_assessor.py`**

Licence header (copy lines 1–15 from `bmlib/quality/quality_agent.py`), then:

```python
"""Cochrane-aligned study assessment.

Produces a :class:`~bmlib.quality.cochrane_models.CochraneStudyAssessment`
— the Cochrane Handbook's study-characteristics table plus the nine-domain
Risk of Bias assessment — from a paper's title and text.

Text larger than one context is reduced to an evidence digest by
:mod:`bmlib.context_processor` first, so the nine-domain judgement is always
made once, over content that fits.  Truncation is not an option here:
allocation concealment and blinding live in Methods and attrition in Results,
so a head-of-string cut drops exactly the evidence the domains are about.

Reference: Cochrane Handbook for Systematic Reviews of Interventions
(https://training.cochrane.org/handbook).
"""

from __future__ import annotations

import logging

from bmlib.agents.base import BaseAgent
from bmlib.context_processor import ProcessingConfig
from bmlib.llm import LLMClient
from bmlib.quality.cochrane_models import (
    ROB_JUDGEMENT_UNCLEAR,
    CochraneInterventions,
    CochraneNotes,
    CochraneOutcomes,
    CochraneParticipants,
    CochraneRiskOfBias,
    CochraneStudyAssessment,
    CochraneStudyCharacteristics,
    RiskOfBiasItem,
    RiskOfBiasJudgement,
)
from bmlib.templates import TemplateEngine

logger = logging.getLogger(__name__)

#: Text longer than this is condensed before assessment.  Roughly 12k tokens,
#: so a whole research paper usually passes through uncondensed while still
#: leaving room in a 32k-token window for the ~4k-character prompt and a
#: 4096-token answer.  The context processor's own 4000-character default
#: would condense almost every full text and most long abstracts.
DEFAULT_CONDENSE_THRESHOLD_CHARS = 48_000

#: Whole assessment attempts before giving up.  ``chat_json()`` retries
#: transport and JSON-shape failures inside each one; this outer bound covers
#: a reply that parses but carries no risk-of-bias section.  Two, not three:
#: a model that omits it twice has misread the prompt, and the bound keeps the
#: worst case at six model calls rather than nine.
_ASSESSMENT_ATTEMPTS = 2

#: Stand-in support text for a domain the model did not report.
_NO_INFORMATION = "Not reported or insufficient information to assess"

#: The nine Cochrane domains: response key, domain name, bias type, outcome
#: type.  One table rather than nine hand-written constructor calls, and the
#: source of the ``bias_type`` values ``collapse_risk_of_bias()`` groups by.
_ROB_DOMAINS: tuple[tuple[str, str, str, str | None], ...] = (
    ("random_sequence_generation", "Random sequence generation", "selection bias", None),
    ("allocation_concealment", "Allocation concealment", "selection bias", None),
    ("baseline_outcome_measurements", "Baseline outcome measurements", "selection bias", None),
    ("baseline_characteristics", "Baseline characteristics", "selection bias", None),
    (
        "blinding_participants_personnel",
        "Blinding of participants and personnel",
        "performance bias",
        None,
    ),
    (
        "blinding_outcome_assessment_subjective",
        "Blinding of outcome assessment (subjective outcomes)",
        "detection bias",
        "subjective",
    ),
    (
        "blinding_outcome_assessment_objective",
        "Blinding of outcome assessment (objective outcomes)",
        "detection bias",
        "objective",
    ),
    ("incomplete_outcome_data", "Incomplete outcome data", "attrition bias", None),
    ("selective_reporting", "Selective reporting", "reporting bias", None),
)


COCHRANE_SYSTEM_PROMPT = """\
You are a medical research methodologist specialising in systematic reviews \
and Cochrane methodology.

CRITICAL RULES:
1. Extract ONLY information that is ACTUALLY PRESENT in the text
2. DO NOT invent, assume, or fabricate any information
3. For anything not reported, use "Not reported" or "Details not reported"
4. Assess THIS study's methodology, not studies it references
5. Return ONLY valid JSON, no explanation"""


COCHRANE_TASK_PROMPT = """\
Conduct a complete Cochrane-style assessment of the study below.

Extract the STUDY CHARACTERISTICS table: methods (the study design, e.g.
"Parallel randomised trial"); participants (setting, population, inclusion and
exclusion criteria, total participants, group sizes); interventions
(description, control, duration); outcomes (description, primary, secondary,
timepoints); and notes (follow-up periods, funding, conflicts of interest,
ethical approval, trial registration, publication status).

Then judge the NINE Cochrane RISK OF BIAS domains. For each, give a judgement
of exactly "Low risk", "High risk" or "Unclear risk", plus the text supporting
it:

a) Random sequence generation (selection bias) — low if adequate
   (computer-generated, random number table), high if inadequate (alternation,
   birth date), unclear if not reported.
b) Allocation concealment (selection bias) — low if adequate (central
   allocation, sealed opaque envelopes), high if open lists, unclear if not
   reported.
c) Baseline outcome measurements (selection bias) — low if similar at
   baseline, high if they differed materially, unclear if not reported.
d) Baseline characteristics (selection bias) — low if balanced, high if
   important imbalances, unclear if not reported.
e) Blinding of participants and personnel (performance bias) — low if blinded
   or the outcome is unlikely to be affected by its absence.
f) Blinding of outcome assessment, SUBJECTIVE outcomes (detection bias) —
   patient-reported measures, quality of life.
g) Blinding of outcome assessment, OBJECTIVE outcomes (detection bias) —
   mortality, laboratory values.
h) Incomplete outcome data (attrition bias) — low if dropout is low, balanced
   across groups and handled appropriately.
i) Selective reporting (reporting bias) — low if every pre-specified outcome
   is reported."""


COCHRANE_RESPONSE_FORMAT = """\
Respond with JSON in exactly this shape:

{
    "study_characteristics": {
        "methods": "study design description",
        "participants": {
            "setting": "location/country",
            "population": "description of participants",
            "inclusion_criteria": ["criterion 1"],
            "exclusion_criteria": ["criterion 1"],
            "total_participants": 45,
            "group_sizes": {"intervention": 25, "control": 20},
            "baseline_characteristics_reported": true
        },
        "interventions": {
            "description": "intervention description",
            "intervention_groups": ["group 1"],
            "control_description": "control description",
            "duration": "duration"
        },
        "outcomes": {
            "description": "outcomes measured",
            "primary_outcomes": ["outcome 1"],
            "secondary_outcomes": ["outcome 1"],
            "outcome_timepoints": ["1 month", "3 months"]
        },
        "notes": {
            "follow_up_periods": ["6 months", "12 months"],
            "funding_source": "funding info",
            "conflicts_of_interest": "conflicts",
            "ethical_approval": "approval status",
            "trial_registration": "registration id",
            "publication_status": "full publication",
            "additional_notes": ["note 1"]
        }
    },
    "risk_of_bias": {
        "random_sequence_generation": {"judgement": "Low risk", "support_for_judgement": "..."},
        "allocation_concealment": {"judgement": "Unclear risk", "support_for_judgement": "..."},
        "baseline_outcome_measurements": {"judgement": "Low risk", "support_for_judgement": "..."},
        "baseline_characteristics": {"judgement": "Low risk", "support_for_judgement": "..."},
        "blinding_participants_personnel": {"judgement": "High risk", "support_for_judgement": "..."},
        "blinding_outcome_assessment_subjective": {"judgement": "High risk", "support_for_judgement": "..."},
        "blinding_outcome_assessment_objective": {"judgement": "Low risk", "support_for_judgement": "..."},
        "incomplete_outcome_data": {"judgement": "Low risk", "support_for_judgement": "..."},
        "selective_reporting": {"judgement": "Unclear risk", "support_for_judgement": "..."}
    },
    "overall_confidence": 0.7,
    "evidence_level": "Level 2 (moderate-high)",
    "assessment_notes": ["note 1"]
}

Use null for any field the text does not report. Every one of the nine
risk_of_bias domains must be present. Respond ONLY with valid JSON."""
```

Note the prompt constants carry **raw JSON braces and are never passed through
`str.format()`** — the prompt is assembled by concatenation, so no brace
doubling is needed and none must be added. (`quality_agent.py`'s much smaller
template does use `.format()`; hand-doubling sixty braces here would be a
defect waiting to happen.)

- [ ] **Step 4: Add the class**

Append to the same file:

```python
class CochraneAssessor(BaseAgent):
    """Produces Cochrane-aligned assessments of individual studies.

    Args:
        llm: The LLM client to use.
        model: Full model string (``"provider:model_name"``).  A capable
            model — the assessment is a nine-domain judgement plus a
            five-section extraction in one reply.
        template_engine: Optional template engine, for parity with the other
            quality agents.  This agent's prompts are module constants.
        temperature: Low by default, for consistency between runs.
        max_tokens: Output ceiling.  The reply carries nine judgements with
            their supporting text plus the characteristics table, so it is
            substantially larger than Tier 3's.
        condense_config: Governs the map-reduce pass that runs when the text
            is larger than ``max_context_chars``.  Defaults to
            :data:`DEFAULT_CONDENSE_THRESHOLD_CHARS`.
    """

    def __init__(
        self,
        llm: LLMClient,
        model: str,
        template_engine: TemplateEngine | None = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        condense_config: ProcessingConfig | None = None,
    ) -> None:
        super().__init__(
            llm=llm,
            model=model,
            template_engine=template_engine,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        self.condense_config = condense_config or ProcessingConfig(
            max_context_chars=DEFAULT_CONDENSE_THRESHOLD_CHARS
        )
        # ``total`` counts every ``assess()`` call, so
        # ``successful + failed == total`` holds.  Upstream incremented the
        # total only on the success path, after every failure had already
        # returned, so its ``success_rate`` could only ever be 1.0.
        self._stats: dict[str, int] = {
            "total_assessments": 0,
            "successful_assessments": 0,
            "failed_assessments": 0,
            "parse_failures": 0,
        }

    # --- Assessment ---

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
    ) -> CochraneStudyAssessment | None:
        """Assess one study against the Cochrane template.

        Either field may be ``None``.  With both missing there is nothing to
        assess and no model call is made — left to itself the model returns a
        fully-formed nine-domain judgement for a paper it was told nothing
        about.

        The caller chooses what *text* is: full text gives a real risk-of-bias
        assessment, an abstract gives a weak one.  Text longer than
        ``condense_config.max_context_chars`` is condensed first, and the
        result says so through
        :attr:`CochraneStudyAssessment.condensed_from_chars`.

        Args:
            title: The paper's title.
            text: The text to assess — full text or abstract.
            study_id: Cochrane's "Author Year" study label.  Unset, it falls
                back to ``"Study {document_id}"`` and then to the title; no
                surname is guessed from an author list.
            pmid: PubMed id, recorded on the characteristics table.
            doi: DOI, recorded on the characteristics table.
            document_id: The caller's own row id.
            min_confidence: Reject an assessment whose ``overall_confidence``
                falls below this.  Zero, the default, rejects nothing.

        Returns:
            The assessment, or ``None`` if it could not be made.  ``None``
            rather than an all-"Unclear risk" stand-in: that would be
            indistinguishable from a real assessment in which the model
            genuinely judged every domain unclear, and anything persisting
            results would store the fabrication permanently.
        """
        title = (title or "").strip()
        text = (text or "").strip()
        label = study_id or (f"document {document_id}" if document_id is not None else title[:60])

        self._stats["total_assessments"] += 1

        if not title and not text:
            logger.warning("Cannot assess: both title and text are empty")
            self._stats["failed_assessments"] += 1
            return None

        notes: list[str] = []
        condensed_from: int | None = None

        assessment = self._attempt_assessment(title, text, label, notes, condensed_from)
        if assessment is None:
            return None

        assessment.study_characteristics.study_id = self._resolve_study_id(
            study_id, document_id, title
        )
        assessment.study_characteristics.document_id = document_id
        assessment.study_characteristics.document_title = title or None
        assessment.study_characteristics.pmid = pmid
        assessment.study_characteristics.doi = doi

        confidence = assessment.overall_confidence
        if confidence is not None and confidence < min_confidence:
            logger.info(
                "Discarding the assessment of %s: confidence %.2f is below the %.2f minimum",
                label,
                confidence,
                min_confidence,
            )
            self._stats["failed_assessments"] += 1
            return None

        self._stats["successful_assessments"] += 1
        return assessment

    def _attempt_assessment(
        self,
        title: str,
        text: str,
        label: str,
        notes: list[str],
        condensed_from: int | None,
    ) -> CochraneStudyAssessment | None:
        """Run the model and parse its reply, retrying a structural failure.

        Args:
            title: The paper's title.
            text: The text to send (already condensed, if it was going to be).
            label: Names the study in log lines.
            notes: Extra notes to fold into the assessment.
            condensed_from: Original length when *text* is a digest.

        Returns:
            The parsed assessment, or ``None``.
        """
        prompt = (
            f"{COCHRANE_TASK_PROMPT}\n\n"
            f"Paper Title: {title}\n\n"
            f"Paper Text:\n{text}\n\n"
            f"{COCHRANE_RESPONSE_FORMAT}"
        )
        messages = [self.system_msg(COCHRANE_SYSTEM_PROMPT), self.user_msg(prompt)]

        for attempt in range(_ASSESSMENT_ATTEMPTS):
            try:
                data = self.chat_json(
                    messages=messages,
                    retry_context=f"Cochrane assessment of {label}",
                    require_dict=True,
                )
            except Exception as exc:
                logger.warning("Cochrane assessment of %s failed: %s", label, exc)
                self._stats["failed_assessments"] += 1
                return None

            try:
                return self._parse_assessment(data, notes, condensed_from)
            except ValueError as exc:
                logger.warning(
                    "Unusable Cochrane response for %s (attempt %d/%d): %s",
                    label,
                    attempt + 1,
                    _ASSESSMENT_ATTEMPTS,
                    exc,
                )

        self._stats["parse_failures"] += 1
        self._stats["failed_assessments"] += 1
        return None

    @staticmethod
    def _resolve_study_id(study_id: str | None, document_id: int | None, title: str) -> str:
        """Pick the Cochrane study label, without guessing a surname."""
        if study_id:
            return study_id
        if document_id is not None:
            return f"Study {document_id}"
        return title or "Unknown study"

    # --- Parsing ---

    def _parse_assessment(
        self,
        data: dict,
        notes: list[str],
        condensed_from: int | None,
    ) -> CochraneStudyAssessment:
        """Build a :class:`CochraneStudyAssessment` from the model's reply.

        Args:
            data: The parsed JSON object.
            notes: Notes to prepend to the model's own.
            condensed_from: Original length when the text was condensed.

        Returns:
            The assessment.  Identity fields are filled in by the caller.

        Raises:
            ValueError: If the reply carries no risk-of-bias section.  Nine
                fabricated "Unclear risk" defaults would be indistinguishable
                from a real assessment.
        """
        rob_data = data.get("risk_of_bias")
        if not isinstance(rob_data, dict) or not rob_data:
            raise ValueError("the response carries no risk_of_bias section")

        sc_data = data.get("study_characteristics")
        if not isinstance(sc_data, dict):
            sc_data = {}

        characteristics = CochraneStudyCharacteristics(
            study_id="",  # replaced by the caller
            methods=str(sc_data.get("methods") or "Not reported"),
            participants=CochraneParticipants.from_dict(_as_dict(sc_data.get("participants"))),
            interventions=CochraneInterventions.from_dict(_as_dict(sc_data.get("interventions"))),
            outcomes=CochraneOutcomes.from_dict(_as_dict(sc_data.get("outcomes"))),
            notes=CochraneNotes.from_dict(_as_dict(sc_data.get("notes"))),
        )

        model_notes = data.get("assessment_notes")
        all_notes = [*notes, *(model_notes if isinstance(model_notes, list) else [])]

        return CochraneStudyAssessment(
            study_characteristics=characteristics,
            risk_of_bias=_parse_risk_of_bias(rob_data),
            overall_confidence=_clamped_confidence(data.get("overall_confidence")),
            evidence_level=data.get("evidence_level"),
            assessment_notes=all_notes or None,
            condensed_from_chars=condensed_from,
        )
```

- [ ] **Step 5: Add the module-level helpers**

Append these three helpers to the same file, after the class:

```python
def _as_dict(value: object) -> dict:
    """Return *value* when it is a dict, an empty dict otherwise.

    A model that answers ``null`` or a bare string for a whole section must
    not take the assessment down with it; the section's ``from_dict`` then
    supplies its own "Not reported" defaults.
    """
    return value if isinstance(value, dict) else {}


def _clamped_confidence(value: object) -> float | None:
    """Read the model's confidence, clamped to 0.0–1.0.

    A model reporting 1.4 would outrank every honest result and defeat
    ``min_confidence``.  An unusable value becomes ``None`` rather than a
    fabricated number.
    """
    if value is None:
        return None
    try:
        return min(1.0, max(0.0, float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        logger.warning("Model reported an unusable confidence %r; recording none", value)
        return None


def _parse_risk_of_bias(rob_data: dict) -> CochraneRiskOfBias:
    """Build the nine-domain assessment from the model's ``risk_of_bias``.

    Every judgement is normalised through
    :meth:`RiskOfBiasJudgement.from_string`.  Writing the model's raw string
    through — as upstream did — stores an invalid value for a model that
    answers ``"low"`` rather than ``"Low risk"``, and
    :meth:`CochraneRiskOfBias.get_summary_counts` then skips that domain
    entirely, silently reporting eight of nine.

    A domain the model omitted defaults to "Unclear risk".  That is honest
    per-domain degradation of an otherwise good answer; a missing *section*
    is rejected by the caller instead.

    Args:
        rob_data: The reply's ``risk_of_bias`` object.

    Returns:
        The nine-domain assessment.
    """
    items = {}
    for key, domain, bias_type, outcome_type in _ROB_DOMAINS:
        raw = _as_dict(rob_data.get(key))
        judgement = RiskOfBiasJudgement.from_string(
            str(raw.get("judgement") or ROB_JUDGEMENT_UNCLEAR)
        ).value
        items[key] = RiskOfBiasItem(
            domain=domain,
            bias_type=bias_type,
            judgement=judgement,
            support_for_judgement=str(raw.get("support_for_judgement") or _NO_INFORMATION),
            outcome_type=outcome_type,
        )
    return CochraneRiskOfBias(**items)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cochrane_assessor.py -v`
Expected: PASS, all classes.

- [ ] **Step 7: Run the full suite and lint**

```bash
uv run pytest tests/ -q
uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .
```
Expected: all pass, ruff clean.

- [ ] **Step 8: Commit**

```bash
git add bmlib/quality/cochrane_assessor.py tests/test_cochrane_assessor.py
git commit -m "feat(quality): port the Cochrane assessment agent from bmlibrarian

Explicit keyword parameters rather than a document dict; None on
failure rather than a fabricated all-unclear assessment.

Fixes four upstream defects: min_confidence was accepted and never
read; judgement strings bypassed RiskOfBiasJudgement.from_string(),
so a model answering \"low\" dropped that domain out of
get_summary_counts() silently; overall_confidence was unclamped; and
a reply carrying no risk_of_bias section was accepted and turned into
nine fabricated defaults."
```

---

### Task 3: Condensing oversized text

**Files:**
- Modify: `bmlib/quality/cochrane_assessor.py` (two prompt constants, `_condense()`, and the branch in `assess()`)
- Test: `tests/test_cochrane_assessor.py` (append one test class)

**Interfaces:**
- Consumes: `CochraneAssessor.assess()` from Task 2, and `CochraneStudyAssessment.condensed_from_chars` from Task 1.
- Produces: `CochraneAssessor._condense(text: str, label: str) -> tuple[str, list[str]] | None` — the digest and any notes, or `None` when there is nothing usable to judge.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cochrane_assessor.py`. Add to the imports at the top:

```python
from bmlib.context_processor import ProcessingConfig
```

```python
class TestCondensingOversizedText:
    """Text larger than one context is reduced to an evidence digest first, so
    the nine-domain judgement is made once, over content that fits."""

    @staticmethod
    def _tiny_config() -> ProcessingConfig:
        return ProcessingConfig(max_context_chars=200)

    def test_text_below_the_threshold_is_not_condensed(self) -> None:
        assessor = make_assessor(condense_config=self._tiny_config())
        assessment = assessor.assess("T", "x" * 100)

        assert assessment.condensed_from_chars is None
        assert assessor.llm.chat.call_count == 1

    def test_oversized_text_is_condensed_and_says_so(self) -> None:
        # ``_condense`` is stubbed for the same reason as the test below: a
        # real run's call count depends on the chunking, and every reply it
        # consumes off the queue advances what the *assessment* call receives.
        # Worse, the stub queue repeats its last reply — the assessment JSON —
        # so a real run feeds ~1.5k of JSON back in as digest content, which
        # balloons into recursion levels and exhausts the queue. The real
        # condensation is exercised by the two tests below, against replies
        # short enough to converge.
        assessor = make_assessor(
            json.dumps(_full_response()), condense_config=self._tiny_config()
        )
        assessor._condense = lambda text, label: ("a digest", [])  # type: ignore[method-assign]
        assessment = assessor.assess("T", "x " * 400)

        assert assessment is not None
        assert assessment.condensed_from_chars == len("x " * 400)

    def test_condensation_reduces_every_chunk_of_the_paper(self) -> None:
        """The real harness run, against a reply short enough that the
        extractions fit one context and no recursion is needed."""
        assessor = make_assessor("short evidence", condense_config=self._tiny_config())

        condensed = assessor._condense("x " * 400, "a study")

        assert condensed is not None
        digest, notes = condensed
        assert "short evidence" in digest
        assert assessor.llm.chat.call_count > 1  # the paper really was chunked
        assert notes == []  # a clean run records nothing

    def test_the_digest_reaches_the_model_instead_of_the_paper(self) -> None:
        # ``_condense`` is stubbed rather than run: how many model calls a
        # real condensation consumes depends on the chunking, so asserting on
        # the *last* call's content while the stub queue advances underneath
        # makes the assertion depend on that count.  What this test is for is
        # the wiring — that the digest replaces the paper in the prompt.
        assessor = make_assessor(
            json.dumps(_full_response()), condense_config=self._tiny_config()
        )
        assessor._condense = lambda text, label: ("DIGEST-MARKER", [])  # type: ignore[method-assign]
        assessor.assess("T", "ORIGINAL-MARKER " * 40)

        final_prompt = assessor.llm.chat.call_args.kwargs["messages"][-1].content
        assert "DIGEST-MARKER" in final_prompt
        assert "ORIGINAL-MARKER" not in final_prompt

    def test_an_empty_digest_returns_none_rather_than_judging_nothing(self) -> None:
        """Running the Cochrane prompt over an empty string returns a
        confident nine-domain assessment of no paper at all."""
        assessor = make_assessor("", "", condense_config=self._tiny_config())

        assert assessor.assess("T", "x " * 400) is None

    def test_the_assessment_is_never_made_from_a_failed_condensation(self) -> None:
        assessor = make_assessor(json.dumps(_full_response()), condense_config=self._tiny_config())
        assessor._condense = lambda text, label: None  # type: ignore[method-assign]

        assert assessor.assess("T", "x " * 400) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cochrane_assessor.py -v -k Condensing`
Expected: FAIL — `test_oversized_text_is_condensed_and_says_so` asserts `condensed_from_chars == 800` and gets `None`, because Task 2 never condenses.

- [ ] **Step 3: Add the condensation prompts**

Add to `bmlib/quality/cochrane_assessor.py`, after `COCHRANE_RESPONSE_FORMAT`:

```python
#: What the digest must preserve.  A digest that drops these is a digest the
#: assessment pass cannot judge from.
CONDENSE_QUERY = (
    "Everything needed for a Cochrane assessment: the study design; the "
    "setting, population and group sizes; the interventions and controls; the "
    "outcomes measured and when; funding, conflicts of interest, ethical "
    "approval and trial registration; and the reported detail behind each risk "
    "of bias domain — how the randomisation sequence was generated, how "
    "allocation was concealed, whether groups were comparable at baseline, who "
    "was blinded to what, how much outcome data was missing and how it was "
    "handled, and whether every pre-specified outcome was reported."
)

CONDENSE_EXTRACTION_PROMPT = """\
Extract, verbatim where possible, every passage of this paper that bears on \
the following.

Needed: {query}

Paper section:
{content}

INSTRUCTIONS:
- Quote or closely paraphrase what the text actually says
- Keep numbers, group sizes, timepoints and named funders exactly
- Say nothing about what the text does not report — omissions are recorded by
  the assessment step, not invented here
- Return plain text

Extracted evidence:"""

CONDENSE_CONSOLIDATION_PROMPT = """\
Merge these extracted passages into one evidence summary.

Needed: {query}

Extracted evidence:
{content}

INSTRUCTIONS:
- Merge overlapping passages, keeping every distinct detail
- Preserve numbers, group sizes, timepoints and named funders exactly
- Keep the methodological detail even where it seems minor: it is what the
  risk of bias judgements rest on
- Return plain text

Consolidated evidence:"""
```

- [ ] **Step 4: Add `_condense()`**

Add to `CochraneAssessor`, after `_resolve_study_id`:

```python
    def _condense(self, text: str, label: str) -> tuple[str, list[str]] | None:
        """Reduce oversized text to an evidence digest that fits one context.

        Runs :class:`~bmlib.context_processor.LLMChunkProcessor` with this
        agent, so token accounting, retries and JSON repair are the ones the
        rest of bmlib uses.  The judgement is made once, afterwards, over the
        digest — no per-chunk judgements are made and none are merged.

        Args:
            text: The oversized text.
            label: Names the study in log lines.

        Returns:
            ``(digest, notes)``, or ``None`` when the run failed or produced
            nothing to judge.
        """
        processor = LLMChunkProcessor(
            agent=self,
            extraction_prompt=CONDENSE_EXTRACTION_PROMPT,
            consolidation_prompt=CONDENSE_CONSOLIDATION_PROMPT,
            config=self.condense_config,
        )
        result = processor.process([text], query=CONDENSE_QUERY)

        if result.status is ProcessingStatus.FAILED:
            logger.error("Could not condense the text of %s: %s", label, result.error_message)
            return None

        digest = result.final_result.content.strip()
        if not digest:
            # The Cochrane prompt over an empty string returns a confident
            # nine-domain assessment of no paper at all.
            logger.error("Condensing the text of %s produced an empty digest", label)
            return None

        notes: list[str] = []
        if result.status is not ProcessingStatus.COMPLETED:
            notes.append(
                f"Source text was condensed before assessment; the condensation "
                f"finished with status {result.status.value}, so the digest may be "
                f"incomplete."
            )
        return digest, notes
```

Extend the imports at the top of the file:

```python
from bmlib.context_processor import LLMChunkProcessor, ProcessingConfig, ProcessingStatus
```

- [ ] **Step 5: Branch in `assess()`**

Replace these three lines in `assess()`:

```python
        notes: list[str] = []
        condensed_from: int | None = None
```

with:

```python
        notes: list[str] = []
        condensed_from: int | None = None

        if len(text) > self.condense_config.max_context_chars:
            condensed = self._condense(text, label)
            if condensed is None:
                self._stats["failed_assessments"] += 1
                return None
            condensed_from = len(text)
            text, notes = condensed
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cochrane_assessor.py -v`
Expected: PASS, including the Task 2 classes.

- [ ] **Step 7: Run the full suite and lint**

```bash
uv run pytest tests/ -q
uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .
```

- [ ] **Step 8: Commit**

```bash
git add bmlib/quality/cochrane_assessor.py tests/test_cochrane_assessor.py
git commit -m "feat(quality): condense oversized papers before assessing them

Text past the configured context is reduced to an evidence digest by
LLMChunkProcessor, then judged once over that digest — so no per-chunk
judgements are made and none have to be merged. Truncating instead
would drop exactly the evidence the domains rest on: allocation
concealment and blinding live in Methods, attrition in Results.

A failed or empty condensation returns None rather than judging an
empty string, and a partial one records its status in the notes."
```

---

### Task 4: Batch assessment and honest statistics

**Files:**
- Modify: `bmlib/quality/cochrane_assessor.py` (two methods)
- Test: `tests/test_cochrane_assessor.py` (append one test class)

**Interfaces:**
- Consumes: `CochraneAssessor.assess()` and `self._stats` from Task 2.
- Produces:
  - `CochraneAssessor.assess_batch(studies: list[dict[str, Any]], *, min_confidence: float = 0.0, progress_callback: Callable[[int, int, str], None] | None = None) -> list[CochraneStudyAssessment]`
  - `CochraneAssessor.get_stats() -> dict[str, Any]`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cochrane_assessor.py`:

```python
class TestBatchAssessment:
    def test_it_returns_one_assessment_per_successful_study(self) -> None:
        assessor = make_assessor()
        results = assessor.assess_batch(
            [
                {"title": "First", "text": "text one", "study_id": "A 2020"},
                {"title": "Second", "text": "text two", "study_id": "B 2021"},
            ]
        )

        assert [a.study_id for a in results] == ["A 2020", "B 2021"]

    def test_a_failed_study_is_skipped_and_the_rest_are_kept(self) -> None:
        assessor = make_assessor("not json", json.dumps(_full_response()))
        results = assessor.assess_batch(
            [
                {"title": "First", "text": "text one", "study_id": "A 2020"},
                {"title": "Second", "text": "text two", "study_id": "B 2021"},
            ]
        )

        assert [a.study_id for a in results] == ["B 2021"]

    def test_progress_is_reported_for_every_study(self) -> None:
        seen: list[tuple[int, int, str]] = []
        make_assessor().assess_batch(
            [{"title": "First", "text": "t"}, {"title": "Second", "text": "t"}],
            progress_callback=lambda current, total, title: seen.append((current, total, title)),
        )

        assert seen == [(1, 2, "First"), (2, 2, "Second")]


class TestStatistics:
    """Upstream incremented ``total_assessments`` only on the success path,
    after every failure had already returned — so ``success_rate`` was
    ``successful / successful`` and could only ever be 1.0."""

    def test_the_success_rate_reports_failures(self) -> None:
        assessor = make_assessor("not json", json.dumps(_full_response()))
        assessor.assess_batch(
            [{"title": "First", "text": "t"}, {"title": "Second", "text": "t"}]
        )

        stats = assessor.get_stats()
        assert stats["total_assessments"] == 2
        assert stats["successful_assessments"] == 1
        assert stats["failed_assessments"] == 1
        assert stats["success_rate"] == 0.5

    def test_successes_and_failures_account_for_every_attempt(self) -> None:
        assessor = make_assessor("not json")
        assessor.assess("T", "text")
        assessor.assess(None, None)

        stats = assessor.get_stats()
        assert (
            stats["successful_assessments"] + stats["failed_assessments"]
            == stats["total_assessments"]
        )

    def test_an_empty_run_reports_a_zero_rate(self) -> None:
        assert make_assessor().get_stats()["success_rate"] == 0.0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cochrane_assessor.py -v -k "Batch or Statistics"`
Expected: FAIL — `AttributeError: 'CochraneAssessor' object has no attribute 'assess_batch'`.

- [ ] **Step 3: Add the two methods**

Add to `CochraneAssessor`, after `assess()`. Extend the file's imports with `from collections.abc import Callable` and `from typing import Any`:

```python
    def assess_batch(
        self,
        studies: list[dict[str, Any]],
        *,
        min_confidence: float = 0.0,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> list[CochraneStudyAssessment]:
        """Assess several studies, keeping the ones that succeeded.

        A convenience loop over :meth:`assess`, so it does take dicts — each
        keyed by that method's own parameter names (``title``, ``text``,
        ``study_id``, ``pmid``, ``doi``, ``document_id``).  That is a batch
        helper mapping a caller's records onto a typed call, not the typed
        call itself.

        Args:
            studies: One dict per study.
            min_confidence: Passed to each :meth:`assess` call.
            progress_callback: Called ``(current, total, title)`` before each
                study.

        Returns:
            The assessments that succeeded, in input order.  A study that
            could not be assessed is absent; :meth:`get_stats` counts it.
        """
        assessments: list[CochraneStudyAssessment] = []
        total = len(studies)

        for index, study in enumerate(studies):
            title = study.get("title") or ""
            if progress_callback:
                progress_callback(index + 1, total, title)

            assessment = self.assess(
                title,
                study.get("text"),
                study_id=study.get("study_id"),
                pmid=study.get("pmid"),
                doi=study.get("doi"),
                document_id=study.get("document_id"),
                min_confidence=min_confidence,
            )
            if assessment is not None:
                assessments.append(assessment)

        logger.info("Assessed %d of %d studies", len(assessments), total)
        return assessments

    def get_stats(self) -> dict[str, Any]:
        """Report what this assessor has done.

        ``total_assessments`` counts every :meth:`assess` call, so
        ``successful_assessments + failed_assessments == total_assessments``
        and ``success_rate`` can report a failure.  ``parse_failures`` is a
        *subset* of the failures, naming the ones that were an unusable reply
        rather than a transport error or a rejected confidence.

        Returns:
            The counters plus the derived ``success_rate``.
        """
        total = self._stats["total_assessments"]
        return {
            **self._stats,
            "success_rate": self._stats["successful_assessments"] / total if total else 0.0,
        }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cochrane_assessor.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite and lint**

```bash
uv run pytest tests/ -q
uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .
```

- [ ] **Step 6: Commit**

```bash
git add bmlib/quality/cochrane_assessor.py tests/test_cochrane_assessor.py
git commit -m "feat(quality): batch Cochrane assessment with statistics that can fail

total_assessments now counts every attempt, so successful + failed ==
total and success_rate can report something other than 1.0. Upstream
incremented the total only on the success path, after every failure
had already returned."
```

---

### Task 5: The `QualityManager` route

**Files:**
- Modify: `bmlib/quality/data_models.py` (one field on `QualityAssessment`, one on `QualityFilter`, plus serialisation)
- Modify: `bmlib/quality/manager.py` (construct the assessor, a `full_text` keyword, the Tier 4 branch, the early-return guard)
- Test: `tests/test_quality.py` (append one test class)

**Interfaces:**
- Consumes: `CochraneAssessor` (Task 2) and `collapse_risk_of_bias` (Task 1).
- Produces:
  - `QualityFilter.use_cochrane_assessment: bool = False` (declared last)
  - `QualityAssessment.cochrane_assessment: Any = None` (declared last)
  - `QualityManager.assess(..., full_text: str | None = None)`
  - `QualityManager.cochrane: CochraneAssessor`

`cochrane_assessment` is typed `Any`, exactly as the neighbouring
`transparency_result` is, so `data_models.py` keeps importing nothing from
`cochrane_models.py` — which imports `data_models` for `BiasRisk` in Task 1, so
the reverse import would be a cycle.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_quality.py`:

```python
class TestTheCochraneRoute:
    """The tiered pipeline can produce Cochrane data, not merely represent it."""

    @staticmethod
    def _manager_with_cochrane(assessment=None):
        from unittest.mock import MagicMock

        from bmlib.quality.manager import QualityManager

        mgr = QualityManager(llm=MagicMock(), classifier_model="o:x", assessor_model="o:y")
        mgr.classifier = MagicMock()
        mgr.assessor = MagicMock()
        mgr.cochrane = MagicMock()
        mgr.cochrane.assess.return_value = assessment
        return mgr

    @staticmethod
    def _cochrane_assessment():
        from unittest.mock import MagicMock

        from bmlib.quality.cochrane_models import (
            ROB_JUDGEMENT_HIGH,
            CochraneStudyAssessment,
            create_default_cochrane_risk_of_bias,
        )

        rob = create_default_cochrane_risk_of_bias()
        rob.selective_reporting.judgement = ROB_JUDGEMENT_HIGH
        chars = MagicMock()
        return CochraneStudyAssessment(
            study_characteristics=chars,
            risk_of_bias=rob,
            overall_confidence=0.85,
            evidence_level="Level 2 (moderate-high)",
        )

    def test_the_flag_routes_to_the_cochrane_assessor(self):
        mgr = self._manager_with_cochrane(self._cochrane_assessment())
        f = QualityFilter(use_cochrane_assessment=True)

        result = mgr.assess("Title", "Abstract", filter_settings=f)

        assert result.assessment_tier == 4
        assert result.extraction_method == "llm_cochrane_assessment"
        assert result.cochrane_assessment is not None
        mgr.cochrane.assess.assert_called_once()

    def test_the_five_domain_bias_risk_is_populated_from_the_nine(self):
        mgr = self._manager_with_cochrane(self._cochrane_assessment())
        f = QualityFilter(use_cochrane_assessment=True)

        result = mgr.assess("Title", "Abstract", filter_settings=f)

        assert result.bias_risk is not None
        assert result.bias_risk.reporting == "high"
        assert result.bias_risk.selection == "unclear"

    def test_the_tier_1_classification_survives(self):
        """Cochrane produces no study design, so the free metadata tier still
        supplies it rather than being thrown away."""
        mgr = self._manager_with_cochrane(self._cochrane_assessment())
        f = QualityFilter(use_cochrane_assessment=True)

        result = mgr.assess(
            "Title",
            "Abstract",
            publication_types=["Randomized Controlled Trial"],
            filter_settings=f,
        )

        assert result.study_design == StudyDesign.RCT
        assert result.quality_tier == QualityTier.TIER_4_EXPERIMENTAL

    def test_the_full_text_is_preferred_over_the_abstract(self):
        mgr = self._manager_with_cochrane(self._cochrane_assessment())
        f = QualityFilter(use_cochrane_assessment=True)

        mgr.assess("Title", "Abstract", full_text="The whole paper", filter_settings=f)

        assert mgr.cochrane.assess.call_args.args[1] == "The whole paper"

    def test_the_abstract_is_used_when_there_is_no_full_text(self):
        mgr = self._manager_with_cochrane(self._cochrane_assessment())
        f = QualityFilter(use_cochrane_assessment=True)

        mgr.assess("Title", "Abstract", filter_settings=f)

        assert mgr.cochrane.assess.call_args.args[1] == "Abstract"

    def test_cochrane_supersedes_tier_3(self):
        """Deeper than Tier 3, so the shallower tier is skipped rather than
        run and discarded — as Tier 3 already supersedes Tier 2."""
        mgr = self._manager_with_cochrane(self._cochrane_assessment())
        f = QualityFilter(use_cochrane_assessment=True, use_detailed_assessment=True)

        result = mgr.assess("Title", "Abstract", filter_settings=f)

        assert result.assessment_tier == 4
        mgr.assessor.assess.assert_not_called()
        mgr.classifier.classify.assert_not_called()

    def test_confident_metadata_does_not_short_circuit_the_cochrane_pass(self):
        mgr = self._manager_with_cochrane(self._cochrane_assessment())
        f = QualityFilter(use_cochrane_assessment=True)

        mgr.assess(
            "Title",
            "Abstract",
            publication_types=["Randomized Controlled Trial"],
            filter_settings=f,
        )

        mgr.cochrane.assess.assert_called_once()

    def test_a_failed_pass_degrades_to_the_tier_1_result(self):
        """Not to nothing — and the two are distinguishable."""
        mgr = self._manager_with_cochrane(None)
        f = QualityFilter(use_cochrane_assessment=True)

        result = mgr.assess(
            "Title",
            "Abstract",
            publication_types=["Randomized Controlled Trial"],
            filter_settings=f,
        )

        assert result.assessment_tier == 1
        assert result.cochrane_assessment is None
        assert result.study_design == StudyDesign.RCT

    def test_the_evidence_level_vocabularies_are_not_mixed(self):
        """CochraneStudyAssessment.evidence_level is free-form model text
        ("Level 2 (moderate-high)"); QualityAssessment.evidence_level is
        Oxford CEBM ("1a"…"5").  Copying one into the other puts a foreign
        vocabulary in a field callers parse."""
        mgr = self._manager_with_cochrane(self._cochrane_assessment())
        f = QualityFilter(use_cochrane_assessment=True)

        result = mgr.assess("Title", "Abstract", filter_settings=f)

        assert result.evidence_level != "Level 2 (moderate-high)"
        assert result.cochrane_assessment.evidence_level == "Level 2 (moderate-high)"

    def test_the_model_confidence_is_carried_across(self):
        mgr = self._manager_with_cochrane(self._cochrane_assessment())
        f = QualityFilter(use_cochrane_assessment=True)

        result = mgr.assess("Title", "Abstract", filter_settings=f)

        assert result.confidence == 0.85

    def test_the_flag_is_off_by_default(self):
        mgr = self._manager_with_cochrane(self._cochrane_assessment())

        mgr.assess("Title", "Abstract", filter_settings=QualityFilter())

        mgr.cochrane.assess.assert_not_called()
```

`tests/test_quality.py` already imports `QualityFilter`, `QualityTier`,
`StudyDesign` and `QualityAssessment`. Add `from unittest.mock import MagicMock`
at module level if it is not already there (the file currently imports it
inside methods; either is fine — match whichever the surrounding code does).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_quality.py -v -k CochraneRoute`
Expected: FAIL — `TypeError: QualityFilter.__init__() got an unexpected keyword argument 'use_cochrane_assessment'`.

- [ ] **Step 3: Add the two dataclass fields**

In `bmlib/quality/data_models.py`, `QualityAssessment` — after `transparency_adjusted`, i.e. last on the dataclass:

```python
    # Cochrane integration.  Typed ``Any`` for the same reason
    # ``transparency_result`` is: naming the type here would make
    # ``data_models`` import ``cochrane_models``, which imports this module
    # for ``BiasRisk``.  Declared last — downstream projects construct this
    # positionally.
    cochrane_assessment: Any = None
```

In `to_dict()`, after the `if self.bias_risk:` block:

```python
        if self.cochrane_assessment is not None:
            d["cochrane_assessment"] = self.cochrane_assessment.to_dict()
```

In `from_dict()`, add before the `return cls(`:

```python
        cochrane = None
        if "cochrane_assessment" in data:
            from bmlib.quality.cochrane_models import CochraneStudyAssessment

            cochrane = CochraneStudyAssessment.from_dict(data["cochrane_assessment"])
```

and pass `cochrane_assessment=cochrane,` as the last argument to `cls(...)`.
The import is inside the function on purpose: at module scope it would be the
cycle the `Any` annotation exists to avoid.

In `QualityFilter` — last on the dataclass:

```python
    use_cochrane_assessment: bool = False
```

- [ ] **Step 4: Wire the manager**

In `bmlib/quality/manager.py`, add to the imports:

```python
import dataclasses

from bmlib.quality.cochrane_assessor import CochraneAssessor
from bmlib.quality.cochrane_models import collapse_risk_of_bias
```

In `__init__`, after `self.assessor = QualityAgent(...)`:

```python
        # The Cochrane pass wants a capable model for the same reason Tier 3
        # does, so it shares ``assessor_model``.  A second model parameter
        # buys nothing until someone needs the two to differ.
        self.cochrane = CochraneAssessor(
            llm=llm,
            model=assessor_model,
            template_engine=template_engine,
        )
```

Change the `assess()` signature — `full_text` goes after `filter_settings`:

```python
    def assess(
        self,
        title: str | None,
        abstract: str | None,
        *,
        publication_types: Sequence[str] = (),
        filter_settings: QualityFilter | None = None,
        full_text: str | None = None,
    ) -> QualityAssessment:
```

Add to its docstring, after the `filter_settings` line:

```
            full_text: The paper's full text, for the Cochrane pass. Falls
                back to *abstract* when absent — an abstract yields a weak
                risk-of-bias assessment, but a weak one beats none.
```

Extend the early-return guard so a confident metadata result does not skip the
Cochrane pass:

```python
        if (
            metadata_is_confident
            and not filt.use_detailed_assessment
            and not filt.use_cochrane_assessment
        ):
            return metadata_result
```

Insert the Tier 4 branch immediately **before** the `# --- Tier 3 ---` block:

```python
        # --- Tier 4: Cochrane assessment ---
        # Deeper than Tier 3, so it supersedes it exactly as Tier 3 supersedes
        # Tier 2: the shallower tier is skipped, not run and discarded.
        if filt.use_cochrane_assessment:
            cochrane = self.cochrane.assess(title, full_text or abstract)
            if cochrane is None:
                # Degrade to the free classification rather than to nothing;
                # ``assessment_tier`` staying 1 is what tells the two apart.
                logger.debug("Tier 4: no Cochrane assessment; keeping the Tier 1 result")
                return metadata_result
            logger.debug(
                "Tier 4: Cochrane assessment of %s", cochrane.study_characteristics.study_id
            )
            return self._enrich_with_cochrane(metadata_result, cochrane)
```

Add the helper at the end of the class:

```python
    @staticmethod
    def _enrich_with_cochrane(
        base: QualityAssessment,
        cochrane: CochraneStudyAssessment,
    ) -> QualityAssessment:
        """Fold a Cochrane assessment into the Tier 1 result.

        The metadata tier supplies ``study_design``, ``quality_tier`` and
        ``quality_score``, which a Cochrane assessment does not produce; the
        Cochrane pass supplies the bias detail, which the metadata tier cannot
        see.  ``evidence_level`` is deliberately **not** copied: Cochrane's is
        free-form model text, this one is Oxford CEBM, and the Cochrane value
        stays reachable on the attached object.

        Args:
            base: The Tier 1 result to enrich.
            cochrane: The assessment to fold in.

        Returns:
            A new assessment; *base* is not modified.
        """
        # ``replace()`` copies shallowly, so the mutable fields are re-listed:
        # otherwise the "copy" shares them with the original and mutating
        # either rewrites both.
        enriched = dataclasses.replace(
            base,
            assessment_tier=4,
            extraction_method="llm_cochrane_assessment",
            bias_risk=collapse_risk_of_bias(cochrane.risk_of_bias),
            cochrane_assessment=cochrane,
            strengths=list(base.strengths),
            limitations=list(base.limitations),
            extraction_details=[*base.extraction_details, "Cochrane assessment via LLM"],
        )
        if cochrane.overall_confidence is not None:
            enriched.confidence = cochrane.overall_confidence
        return enriched
```

Add `CochraneStudyAssessment` to the `cochrane_models` import line in
`manager.py` so the annotation resolves:

```python
from bmlib.quality.cochrane_models import CochraneStudyAssessment, collapse_risk_of_bias
```

Finally, pass the full text through `assess_batch()` — in its `self.assess(...)`
call, add:

```python
                full_text=paper.get("full_text"),
```

and note it in that method's docstring alongside `"title"` and `"abstract"`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_quality.py -v`
Expected: PASS, including the pre-existing `TestQualityManager` tests.

- [ ] **Step 6: Run the full suite and lint**

```bash
uv run pytest tests/ -q
uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .
```

- [ ] **Step 7: Commit**

```bash
git add bmlib/quality/data_models.py bmlib/quality/manager.py tests/test_quality.py
git commit -m "feat(quality): route Cochrane assessment through QualityManager

use_cochrane_assessment runs the assessor and enriches the free Tier 1
metadata result rather than replacing it: the metadata tier supplies
the study design a Cochrane assessment does not produce, the Cochrane
pass supplies the bias detail the metadata tier cannot see. It
supersedes Tier 3 when both are asked for, and a failed pass degrades
to the Tier 1 result rather than to nothing.

evidence_level is not copied across: Cochrane's is free-form model
text, QualityAssessment's is Oxford CEBM."
```

---

### Task 6: Exports and documentation

**Files:**
- Modify: `bmlib/quality/__init__.py`
- Modify: `docs/manual/quality.md`
- Modify: `CHANGELOG.md`
- Modify: `CLAUDE.md`
- Modify: `ROADMAP.md`
- Modify: `HANDOVER.md`

**Interfaces:**
- Consumes: everything from Tasks 1–5.
- Produces: `bmlib.quality.CochraneAssessor` and `bmlib.quality.collapse_risk_of_bias` as public names.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cochrane_assessor.py`:

```python
class TestPublicExports:
    def test_the_assessor_and_the_collapse_are_public(self) -> None:
        import bmlib.quality as quality

        assert "CochraneAssessor" in quality.__all__
        assert "collapse_risk_of_bias" in quality.__all__
        assert quality.CochraneAssessor is CochraneAssessor
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_cochrane_assessor.py -v -k PublicExports`
Expected: FAIL — `AssertionError` on the first assertion.

- [ ] **Step 3: Export the new names**

In `bmlib/quality/__init__.py`, add to the `cochrane_models` import block (keeping it alphabetical):

```python
    collapse_risk_of_bias,
```

Add a new import after it:

```python
from bmlib.quality.cochrane_assessor import CochraneAssessor
```

Add both to `__all__`, under the `# Cochrane-aligned models` comment:

```python
    "CochraneAssessor",
    ...
    "collapse_risk_of_bias",
```

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/test_cochrane_assessor.py -v -k PublicExports`
Expected: PASS.

- [ ] **Step 5: Update `docs/manual/quality.md`**

Add a `## Cochrane assessment` section after the existing Cochrane models
section, built around these two examples:

````markdown
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

result.assessment_tier        # 4
result.study_design           # from the free Tier 1 metadata pass
result.bias_risk              # five domains, collapsed from the nine
result.cochrane_assessment    # the full table + RoB, or None if it failed
```
````

Around those, cover:

- Constructing `CochraneAssessor(llm=..., model=...)` and calling `assess()`.
- That failure is `None`, and why it is not an all-unclear stand-in.
- `condense_config`, the 48000-character default and what
  `condensed_from_chars` means when set.
- `collapse_risk_of_bias()` — the derived 9→5 grouping, worst-wins with
  unclear above low, and that an unrecognised `bias_type` raises.
- The `QualityFilter(use_cochrane_assessment=True)` route, `full_text=`, that
  the result is `assessment_tier=4` with the Cochrane object attached, and
  that `evidence_level` is not copied across.

Verify every example against the real signatures before writing it — the 0.4.0
documentation refresh set the standard that examples are executed, not
imagined.

- [ ] **Step 6: Update `CHANGELOG.md`**

Under `## [Unreleased]`, add:

```markdown
### Added

- **Cochrane assessment agent** (`bmlib.quality.CochraneAssessor`) — Phase 2
  row 9 of the bmlibrarian port, and the producer `cochrane_models.py` has
  been waiting for since 0.4.0. `assess()` turns a title and text into a
  `CochraneStudyAssessment`: the Cochrane Handbook's five-section
  study-characteristics table plus a judgement and supporting text for each of
  the nine Risk-of-Bias domains. Text larger than the configured context is
  first reduced to an evidence digest by `bmlib.context_processor`, so the
  nine-domain judgement is always made once, over content that fits — and
  `condensed_from_chars` says when that happened, because a judgement made
  over a digest is weaker evidence than one made over the paper. Truncating
  instead was rejected: allocation concealment and blinding live in Methods
  and attrition in Results, so a head-of-string cut drops exactly the evidence
  the domains rest on. Failure returns `None`, not an all-"Unclear risk"
  stand-in that would be indistinguishable from a real assessment.
- **`collapse_risk_of_bias()`** — the nine Cochrane domains reduced to the
  five `BiasRisk` domains, closing the `BiasRisk` ↔ `CochraneRiskOfBias` gap.
  The grouping is derived from each item's own `bias_type` rather than written
  out per domain; where several collapse onto one field the worst wins, with
  `unclear` outranking `low` because an unreported domain is not a clean bill
  of health. An unrecognised `bias_type` raises rather than returning a
  `BiasRisk` that looks complete.
- **`QualityFilter(use_cochrane_assessment=True)`** and a `full_text=` keyword
  on `QualityManager.assess()`. The Cochrane pass *enriches* the free Tier 1
  metadata result rather than replacing it — the metadata tier supplies the
  study design a Cochrane assessment does not produce, the Cochrane pass
  supplies the bias detail the metadata tier cannot see — and attaches the
  full assessment to the new `QualityAssessment.cochrane_assessment`. It
  supersedes Tier 3 when both are requested, and a failed pass degrades to the
  Tier 1 result rather than to nothing. Additive: `assessment_tier=4` is new,
  the flag is off by default, and no stored value moves.

  Six upstream defects were fixed in the port, each with a named regression
  test: `min_confidence` was accepted and never read; `success_rate` could
  only ever report 1.0, because the attempt total was incremented on the
  success path alone; judgement strings bypassed
  `RiskOfBiasJudgement.from_string()`, so a model answering `"low"` rather
  than `"Low risk"` stored an invalid value that `get_summary_counts()` then
  skipped, silently reporting eight domains of nine; `overall_confidence` was
  unclamped, so a model reporting 1.4 outranked every honest result; a reply
  carrying no `risk_of_bias` section at all was accepted and turned into nine
  fabricated defaults; and the study label was derived by
  `first_author.split()[-1]`, which reads "van der Berg" as "Berg".
```

- [ ] **Step 7: Update `CLAUDE.md`**

Two edits:

1. Add `cochrane_assessor.py` to the `quality/` block in the directory tree,
   after `cochrane_formatter.py`:
   `│   ├── cochrane_assessor.py   # Cochrane-aligned assessment agent (Tier 4)`
2. Rewrite the `**quality/**` module description. It currently says the
   Cochrane models and extractors are **standalone**, that "nothing in the
   tiered pipeline imports them", and that "there is no conversion between
   `BiasRisk` and `CochraneRiskOfBias`". The first two are no longer true of
   the Cochrane half and the third is now false outright. Say instead that
   `CochraneAssessor` produces the models, `collapse_risk_of_bias()` bridges
   them onto `BiasRisk`, and `QualityManager` reaches both behind
   `use_cochrane_assessment`. **The rule-based extractors are still
   standalone** — keep that claim, narrowed to them.

- [ ] **Step 8: Update `ROADMAP.md`**

Two edits:

1. The `⬜ Planned | Wire the new quality tools into the pipeline` row becomes
   `✅ Done` for the Cochrane half, restated as what remains: the rule-based
   extractors as a free pre-filter ahead of Tier 1, with `DimensionScore` →
   `QualityAssessment` conversion. Leave it `⬜ Planned` if that reads more
   honestly, but say plainly that the Cochrane half is closed.
2. Add a `✅ Done | Cochrane assessment agent` row to the **Quality
   (`bmlib.quality`)** section describing the port, marked `(unreleased)` —
   the next release promotes it.

- [ ] **Step 9: Update `HANDOVER.md`**

- Move Phase 2 row 9 from "the next port" to done, and note that row 9 was the
  one that also answered the Cochrane half of the wiring item.
- Add to **Deliberate non-fixes** the decisions a future session would
  otherwise re-open: `evidence_level` not being copied between the two
  vocabularies; `None` rather than an all-unclear stand-in on failure;
  `collapse_risk_of_bias()` raising on an unknown `bias_type`; `unclear`
  outranking `low` in the collapse; `_ASSESSMENT_ATTEMPTS = 2`; the caller
  owning `study_id`; and the two-pass condensation shape (why no per-chunk
  judgement merge exists).
- Update the test count from the new suite total.

- [ ] **Step 10: Run the full suite and lint**

```bash
uv run pytest tests/ -q
uvx ruff@0.15.20 check . && uvx ruff@0.15.20 format --check .
```
Expected: all pass, ruff clean. Record the new passed/skipped counts — HANDOVER.md quotes them.

- [ ] **Step 11: Commit**

```bash
git add bmlib/quality/__init__.py docs/manual/quality.md CHANGELOG.md CLAUDE.md ROADMAP.md HANDOVER.md tests/test_cochrane_assessor.py
git commit -m "docs(quality): document the Cochrane assessor and export it

CLAUDE.md's claim that the Cochrane models are standalone and that no
BiasRisk conversion exists is no longer true; the claim is narrowed to
the rule-based extractors, which still are."
```

- [ ] **Step 12: Push and open the PR**

```bash
git push -u origin feature/cochrane-assessor
gh pr create --base main \
  --title "feat(quality): Cochrane assessment agent and pipeline wiring" \
  --body "$(cat <<'BODY'
Phase 2 row 9 of the bmlibrarian port, and the Cochrane half of the standing
"wire the new quality tools into the pipeline" roadmap item.

`bmlib.quality.cochrane_models` has been in the tree since 0.4.0 with nothing
importing it — a vocabulary with no speaker. This adds the producer.

## What it adds

- `CochraneAssessor(BaseAgent)` — title + text to a `CochraneStudyAssessment`.
  Oversized text is reduced to an evidence digest by `bmlib.context_processor`
  first, so the nine-domain judgement is made once over content that fits; no
  per-chunk judgements are made and none have to be merged.
  `condensed_from_chars` records when that happened.
- `collapse_risk_of_bias()` — the 9-to-5 bridge onto `BiasRisk`, grouped by
  each item's own `bias_type` and reduced worst-wins with `unclear` above
  `low`.
- `QualityFilter(use_cochrane_assessment=True)` plus a `full_text=` keyword on
  `QualityManager.assess()`. The pass enriches the free Tier 1 result rather
  than replacing it, and attaches the full object to
  `QualityAssessment.cochrane_assessment`.

## Upstream defects fixed

Each has a named regression test:

| Defect | Test |
|---|---|
| `min_confidence` accepted, never read | `test_min_confidence_is_honoured` |
| `success_rate` could only ever be 1.0 | `test_the_success_rate_reports_failures` |
| Judgement strings bypassed `from_string()`, dropping a domain from the summary | `test_every_domain_is_counted_in_the_summary` |
| `overall_confidence` unclamped | `test_a_confidence_outside_the_range_is_clamped` |
| A reply with no `risk_of_bias` section became nine fabricated defaults | `test_a_response_with_no_risk_of_bias_block_is_rejected` |
| Study label guessed by `first_author.split()[-1]` | `test_a_document_id_is_the_first_fallback` |

## Compatibility

Additive. `assessment_tier=4` is new, the flag is off by default, and no
stored value moves. Two dataclasses gain a field, both declared last so
positional construction stays stable.

Design: `docs/superpowers/specs/2026-08-05-cochrane-assessor-design.md`.
Still open: the rule-based extractors as a free pre-filter ahead of Tier 1.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
BODY
)"
```

---

## Notes for the implementer

**The upstream source is the spec for behaviour, not for style.** It is at
`~/src/bmlibrarian/src/bmlibrarian/agents/systematic_review/cochrane_assessor.py`.
Read it for the prompt's domain guidance, which is good and worth keeping.
Ignore its structure: `Dict`/`Optional` typing, the `orchestrator` and
`callback` parameters, the per-document `test_connection()` call, and the
formatter passthroughs are all deliberately not ported (see the spec's
"Deliberately not ported").

**Do not `grep` `~/src/bmlibrarian` without excluding `.claude/worktrees/`** —
it holds three stale copies of the whole tree, so every match appears four
times.

**If a test you wrote is wrong, fix the test.** If the *implementation* looks
wrong, check the spec before changing either — most of what looks like an
oddity here is argued there.
