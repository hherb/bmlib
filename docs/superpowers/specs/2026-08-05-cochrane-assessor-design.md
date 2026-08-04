# Cochrane assessor — design

_Date: 2026-08-05. Phase 2 row 9 of the bmlibrarian port
([`docs/plans/2026-07-17-bmlibrarian-porting-analysis.md`](../../plans/2026-07-17-bmlibrarian-porting-analysis.md)),
plus the standing ROADMAP item "wire the new quality tools into the pipeline"._

## The problem

`bmlib.quality.cochrane_models` has been in the tree since 0.4.0 and nothing
imports it. It defines the nine-domain Cochrane Risk-of-Bias assessment and the
study-characteristics table; `cochrane_formatter` renders them. But no code
*produces* a `CochraneStudyAssessment`, so the models are a vocabulary with no
speaker, and the tiered quality pipeline (`QualityManager`) does not know they
exist.

Upstream `bmlibrarian` has the missing producer:
`agents/systematic_review/cochrane_assessor.py`, 685 lines, the least-coupled
agent in that repo — it needs only `BaseAgent` and the Cochrane models, with no
database, config, or orchestrator reads.

## What this delivers

1. `bmlib/quality/cochrane_assessor.py` — a `CochraneAssessor(BaseAgent)` that
   turns a title plus text into a `CochraneStudyAssessment`.
2. `collapse_risk_of_bias()` — the nine Cochrane domains reduced to the five
   `BiasRisk` domains the rest of `quality/` speaks.
3. A route through `QualityManager`, so the tiered pipeline can produce
   Cochrane data instead of merely being able to represent it.

Out of scope, and left as a separate decision: making the rule-based
`quality/extractors.py` a free pre-filter ahead of Tier 1. That changes Tier 1
behaviour for existing callers and moves stored values; this change does not.

## Decisions

### The call interface is explicit keyword parameters

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
    ) -> None: ...

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
    ) -> CochraneStudyAssessment | None: ...

    def assess_batch(
        self,
        studies: list[dict[str, Any]],
        *,
        min_confidence: float = 0.0,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> list[CochraneStudyAssessment]: ...
```

`assess_batch()` is a convenience loop over `assess()`, so it does take dicts —
each one keyed by `assess()`'s own parameter names. That is a batch helper
mapping a caller's records onto a typed call, not the typed call itself, and it
returns only the assessments that succeeded. A `None` from `assess()` is
skipped, as upstream does.

Upstream takes a `document: dict` and digs `id` / `title` / `abstract` /
`full_text` / `authors` / `year` / `pmid` / `doi` out of it. That couples the
library to one application's key names, cannot be type-checked, and silently
assesses an empty string when a key is misspelled. Explicit parameters match
`QualityAgent.assess(title, abstract)` and bmlib's "state lives in the caller"
convention.

One consequence is deliberate: **the caller chooses full text or abstract.**
Upstream's `full_text if full_text else abstract` means the agent cannot report
which it assessed; here the caller passes `text` and knows.

A second consequence: **`study_id` is the caller's.** Upstream derives an
"Author Year" label with `first_author.split()[-1]`, which reads "van der Berg"
as "Berg" and any `"Surname, Given"` string backwards. With no `authors`
parameter there is nothing to guess from, and guessing was wrong anyway.
Unset, `study_id` falls back to `f"Study {document_id}"`, or to the title when
there is no `document_id`.

### Failure returns `None`

Upstream returns `None`; bmlib's Tier 3 `QualityAgent.assess()` instead
degrades to `QualityAssessment.unclassified()` and never fails. The Cochrane
assessor keeps `None`.

An all-"Unclear risk" fallback would be indistinguishable from a real
assessment in which the model genuinely judged every domain unclear, and
anything persisting results would store that fabrication permanently. This is
the same reasoning that makes `_fetch_ncbi_pmc()` raise on a body-less stub
rather than degrade it to an abstract.

`create_default_cochrane_risk_of_bias()` therefore stays uncalled by the
assessor. It remains exported for callers assembling an assessment by hand.

### Oversized text is condensed first, then judged once

A Cochrane assessment wants full text — often 50k+ characters. Truncating is
not an option here: allocation concealment and blinding live in Methods,
attrition in Results, and a head-of-string cut drops exactly the evidence the
nine domains are about.

`bmlib.context_processor` exists for content larger than one context, and the
**two-pass** shape fits it exactly:

1. The harness reduces the paper to an **evidence digest** that fits.
2. The nine-domain judgement happens **once**, over that digest.

No merge rule for contradictory judgements is needed anywhere, because the
harness never makes a judgement. The rejected alternative — a full nine-domain
assessment per chunk, merged by severity — needs that rule and cannot defend
it: most chunks hold no evidence for most domains, so it merges mostly
"Unclear", and a caveat in the Discussion would override documented allocation
concealment in the Methods.

`LLMChunkProcessor` covers pass 1 with no new subclass. It takes any
`BaseAgent` — the assessor passes `self`, so token accounting, retries and JSON
repair are the ones the rest of bmlib uses — plus two prompt templates
carrying `{query}` and `{content}`. Condensation runs only when
`len(text) > condense_config.max_context_chars`; below that the text goes to
the model whole.

The `query` handed to the harness names what the digest must preserve: the
methods, participants, interventions, outcomes and notes the characteristics
table needs, and the reported detail behind each of the nine bias domains —
randomisation, allocation concealment, baseline comparability, blinding,
attrition and outcome reporting. A digest that drops those is a digest the
second pass cannot judge from.

**A failed condensation returns `None`.** `process()` reports failure on the
result rather than raising, so the assessor checks
`ProcessingStatus`: a `FAILED` run, or one whose digest is empty, means there
is nothing to judge, and running the Cochrane prompt over an empty string would
produce a confident nine-domain assessment of no paper at all. A `PARTIAL` or
`TRUNCATED` run does yield a digest and is used, with the status recorded in
`assessment_notes` so a caller can see the digest was incomplete.

### A condensed judgement says so

`CochraneStudyAssessment` gains:

```python
condensed_from_chars: int | None = None   # declared last
```

Set to the original character count when the text was condensed; `None` when
the paper went to the model whole. A judgement made over an LLM-condensed
digest is weaker evidence than one made over the paper, and the project's rule
is that every path which degrades reports itself rather than leaving the caller
to infer it.

Declared last for the same reason as `Publication.pmcid`,
`BaseAgent.embedding_model` and `TransparencyResult.unknown_reason`: downstream
projects construct these positionally, so any other placement shifts every
following argument. It round-trips through `to_dict()` / `from_dict()`; a dict
without the key loads as `None`.

### The 9→5 collapse is derived from the data

```python
def collapse_risk_of_bias(rob: CochraneRiskOfBias) -> BiasRisk
```

Lives in `cochrane_models.py`, not `data_models.py`: the richer model knows how
to reduce itself to the simpler one, and `data_models` stays free of Cochrane
knowledge. Neither imports the other today, so either direction would work;
this one keeps the dependency pointing the way the knowledge does.

The mapping is **not hard-coded**. Every `RiskOfBiasItem` already carries a
`bias_type` naming its target domain — "selection bias" (×4), "performance
bias", "detection bias" (×2), "attrition bias", "reporting bias" — so the
function groups by `bias_type` and reduces each group.

**The reduction is worst-wins, ordered `high > unclear > low`.** Where four
selection domains collapse to one, the result is "high" if any is high;
otherwise "unclear" if any is unclear; otherwise "low". "Unclear" outranks
"low" because you cannot claim low selection-bias risk when allocation
concealment was never reported — an unknown is not a clean bill of health.

**An unrecognised `bias_type` raises.** `RiskOfBiasItem` is public and a caller
may build one with `bias_type="funding bias"`; silently dropping it would emit
a `BiasRisk` that looks complete and is not. This follows
`_Analysis.note_data_level()`, which raises rather than scoring an unknown
level at zero, and gets the same style of pinning test: every domain
`create_default_cochrane_risk_of_bias()` produces maps to a domain the collapse
knows.

### The manager enriches, it does not replace

Three additions, each declared last on its dataclass:

- `QualityFilter.use_cochrane_assessment: bool = False`
- `QualityAssessment.cochrane_assessment: CochraneStudyAssessment | None = None`
- a `full_text: str | None = None` keyword on `QualityManager.assess()`

`QualityAssessment.cochrane_assessment` follows the shape
`transparency_result` already established: a foreign result attached to the
common carrier, so `assess()` keeps returning `QualityAssessment` and no
existing caller breaks.

The route, when the flag is set:

1. **Tier 1 metadata runs first** — it is free and supplies `study_design`,
   `quality_tier` and `quality_score`, which a Cochrane assessment does not
   produce. (Its `methods` field is free text like "Parallel randomised trial";
   mapping that onto `StudyDesign` would need fuzzy matching this change does
   not attempt.)
2. The Cochrane assessor runs over `full_text or abstract`.
3. Its result **enriches** the Tier 1 assessment: `assessment_tier = 4`,
   `extraction_method = "llm_cochrane_assessment"`, `bias_risk` from the
   collapse, `cochrane_assessment` attached, and `confidence` taken from
   `overall_confidence` when the model reported one.

Nothing emits `assessment_tier = 4` today, so no stored value moves.

**Cochrane supersedes `use_detailed_assessment`** when both flags are set: it
is strictly the deeper assessment, and Tier 3 is skipped rather than run and
discarded — mirroring how Tier 3 already supersedes Tier 2.

**A failed Cochrane pass degrades to the Tier 1 result**, not to nothing, and
the two are distinguishable: `assessment_tier` stays `1` and
`cochrane_assessment` is `None`.

**`evidence_level` is deliberately not copied.** `CochraneStudyAssessment`'s is
free-form model text ("Level 2 (moderate-high)"); `QualityAssessment`'s is
Oxford CEBM (`"1a"`…`"5"`, as the Tier 3 prompt specifies). Copying one into
the other puts a foreign vocabulary in a field callers parse. It stays
reachable on the attached object.

The manager builds the assessor with the existing `assessor_model` — the
Cochrane pass wants a capable model for the same reason Tier 3 does, and a
second model parameter buys nothing until someone needs them to differ.

## Upstream defects fixed

Each gets a named regression test that fails if the fix is reverted.

1. **`min_confidence` is accepted and never read.** Upstream defines
   `DEFAULT_MIN_CONFIDENCE = 0.4`, threads it through
   `assess_document(document, min_confidence=…)`, and never touches it in the
   body. Ported as a working filter: an assessment whose `overall_confidence`
   falls below it returns `None`. The default is `0.0`, not upstream's `0.4`,
   so nothing is dropped unasked — a caller that wants the threshold asks for
   it.

2. **`get_stats()["success_rate"]` can only ever be 1.0.**
   `_stats["total_assessments"]` is incremented on the success path only,
   *after* every failure has already returned; `parse_failures` and
   `failed_assessments` increment on their own paths but never the total. So
   the ratio is `successful/total` where the two are the same number. Every
   attempt counts here. (Same defect class as the context_processor port's
   `progress_percent` that could never leave 0.0.)

3. **LLM judgement strings bypass `RiskOfBiasJudgement.from_string()`.**
   Upstream writes `data.get("judgement", ROB_JUDGEMENT_UNCLEAR)` straight into
   `RiskOfBiasItem.judgement`. A model answering `"low"` or `"Low"` or
   `"low_risk"` — all of which `from_string()` handles — stores an invalid
   string. `__post_init__` warns, and then `get_summary_counts()` **skips that
   domain entirely** (`if item.judgement in counts`), so the summary silently
   reports eight domains and the formatter renders the ninth wrong. Every
   judgement is normalised through
   `RiskOfBiasJudgement.from_string(...).value`, which also makes
   `RiskOfBiasJudgement` — exported since 0.4.0 and called nowhere in bmlib —
   live code.

4. **`overall_confidence` is unclamped.** A model reporting `1.4` outranks
   every honest result and defeats `min_confidence`. Clamped to 0.0–1.0, as
   `LLMChunkProcessor._extract_structured()` already does for the same reason.

5. **A response missing the entire `risk_of_bias` block is accepted**, and
   `_parse_risk_of_bias({})` fabricates nine "Unclear risk" defaults from
   nothing. A Cochrane assessment without any risk-of-bias section is not a
   Cochrane assessment; it becomes a bad response, which `chat_json()` retries,
   and `None` if it never arrives. A *single missing domain* still defaults to
   Unclear with "Not reported or insufficient information" — that is honest
   per-domain degradation of an otherwise good answer, not fabrication of the
   whole.

6. **Dead imports.** `create_default_cochrane_risk_of_bias`,
   `ROB_JUDGEMENT_LOW` and `ROB_JUDGEMENT_HIGH` are imported and never used.

## Deliberately not ported

- **The per-document `test_connection()` call.** Upstream runs it at the top of
  every `assess_document()`, costing an extra round trip per paper in a batch.
  In bmlib it reports provider reachability only — not whether the model is
  installed — so it cannot tell the caller what the check implies.
- **The `orchestrator` and `callback` constructor parameters**, and the
  `_call_callback()` progress events. Queue integration is the application's,
  as with every other agent ported so far. `assess_batch()` keeps a
  `progress_callback`, which is the part that does not need a queue.
- **`format_assessment_markdown()` and its two siblings.** They are one-line
  passthroughs to `cochrane_formatter`, which callers already import directly.
  Re-exporting them from the agent implies the agent owns the rendering.

## Testing

New `tests/test_cochrane_assessor.py`, plus additions to `tests/test_cochrane.py`
(the collapse) and `tests/test_quality.py` (the manager route). Every test runs
against a stub LLM client — no network, matching the rest of the suite.

Coverage to hit:

- The six defects above, each by name.
- Text below the threshold is **not** condensed, and `condensed_from_chars` is
  `None`; text above it is, and the field carries the original length.
- A condensation that fails or yields an empty digest returns `None` rather
  than judging an empty string; a `PARTIAL`/`TRUNCATED` one still returns an
  assessment, with the status in `assessment_notes`.
- `assess_batch()` skips the papers that returned `None` and keeps the rest,
  and its statistics count every attempt (defect 2).
- The collapse: worst-wins in each direction, "unclear" beating "low", an
  unrecognised `bias_type` raising, and every default domain mapping to a known
  target.
- The manager: the flag routes to Cochrane; Cochrane supersedes Tier 3 when
  both are set; a failed pass leaves a Tier 1 result with
  `cochrane_assessment=None`; `evidence_level` is not copied across.
- `assess()` returns `None` for blank title *and* text without calling the
  model.

## Documentation

`docs/manual/quality.md` gains a Cochrane assessor section. `CHANGELOG.md`
records it under `[Unreleased]`. `CLAUDE.md`'s `quality/` description currently
says the Cochrane models are **standalone** and that "nothing in the tiered
pipeline imports them" — no longer true, and the `BiasRisk` ↔
`CochraneRiskOfBias` gap it names is what `collapse_risk_of_bias()` closes.
`ROADMAP.md`'s "wire the new quality tools into the pipeline" row becomes
partially done, with the extractors pre-filter named as what is left.
