# bmlib::quality — Quality Assessment

Tiered study-quality assessment: free classification from PubMed publication
types (Tier 1), a cheap LLM classifier (Tier 2), a deep LLM assessment
(Tier 3) and a Cochrane-aligned risk-of-bias assessment (Tier 4), plus the
rule-based extractors, the PICO and PRISMA 2020 agents and the unattended
evidence-weight pipeline (design, primary outcomes, RoB 2, evidence ladder),
which stand outside the tiers. The Python page is
[quality.md](../../../../docs/manual/quality.md); this one describes the Rust
API and where it differs.

| Need | Feature |
|---|---|
| Tier 1, the models, the filter, the tier rules, the extractors, the Cochrane formatters, the RoB 2 rules, the evidence ladder | none |
| The LLM tiers, `PicoAgent`, `Prisma2020Agent` and the evidence-weight agents against your own `JsonChat` | none |
| `LlmChat` over a real `LlmClient` | none, plus a transport (`http-ureq`, default, for `bmlib::http::UreqClient`) |

Most names are re-exported at `bmlib::quality`. The pipeline functions
(`manager::assess`, `manager::next_tier`, `TierRunner`) and Tier 1
(`metadata_filter::classify_from_metadata`) are reached through their modules.

## Tier 1: publication types

```rust
use bmlib::quality::metadata_filter::classify_from_metadata;
use bmlib::quality::{QualityTier, StudyDesign};

let a = classify_from_metadata(&["Randomized Controlled Trial".into(), "Multicenter Study".into()]);
assert_eq!(a.study_design, StudyDesign::Rct);
assert_eq!(a.quality_tier, QualityTier::Tier4Experimental);
assert_eq!(a.assessment_tier, 1);
assert_eq!(a.confidence, 0.9);
assert_eq!(a.is_randomized, Some(true));

// Case, hyphens and underscores are folded.
let r = classify_from_metadata(&["systematic-review".into()]);
assert_eq!(r.study_design, StudyDesign::SystematicReview);

// Nothing recognised: the unclassified result, not a weak guess.
let none = classify_from_metadata(&["Journal Article".into()]);
assert_eq!(none.quality_tier, QualityTier::Unclassified);
```

A type in the priority list (`TYPE_PRIORITY`) scores
`METADATA_HIGH_CONFIDENCE` (0.9); any other known type scores
`METADATA_LOW_CONFIDENCE` (0.72), below `METADATA_ACCEPTANCE_THRESHOLD`, so
the pipeline consults a deeper tier.

`StudyDesign` has two spellings: `as_str()` is the JSON name (`"rct"`) and
`member_name()` the Python enum member (`"RCT"`); `study_design_from_str`
reads the first. `QualityTier` orders weakest first and `value()` is the
integer `to_json` writes. `design_to_tier`, `design_to_score` and
`design_to_randomized` are functions where Python has dicts;
`design_to_randomized` returns `None` for a design that does not settle it
(a systematic review, a guideline).

## Filtering

`QualityFilter::default()` is **not** all-`false`: like Python's constructor
it turns `use_llm_classification` on, so a default filter reaches Tier 2.

```rust
use bmlib::quality::metadata_filter::classify_from_metadata;
use bmlib::quality::{QualityFilter, QualityTier};

let filter = QualityFilter {
    min_tier: Some(QualityTier::Tier4Experimental),
    require_randomization: true,
    ..QualityFilter::default()
};
assert!(filter.use_llm_classification);

let rct = classify_from_metadata(&["Randomized Controlled Trial".into()]);
let review = classify_from_metadata(&["Systematic Review".into()]);
assert!(rct.passes_filter(&filter));
assert!(!review.passes_filter(&filter)); // randomisation not established
```

`min_sample_size` is skipped for an assessment that records no sample size,
and `require_randomization` refuses `None` as well as `Some(false)`.
`QualityAssessment::to_json` / `from_json` serialise as Python's `to_dict` /
`from_dict` do.

## The LLM tiers

The tiers do not hold a client. Each takes a `&mut dyn JsonChat`, which sends
messages and reads the answer as JSON; `LlmChat` is the implementation over an
`LlmClient`, and runs the retry, truncation and shape rules of
`bmlib::agents::chat_json`.

```rust,ignore
use std::sync::Arc;
use bmlib::http::UreqClient;
use bmlib::llm::LlmClient;
use bmlib::quality::{LlmChat, StudyClassifier};

let client = LlmClient::new(Arc::new(UreqClient::new()));
let mut chat = LlmChat::new(&client, "anthropic:claude-3-haiku-20240307");
let tier2 = StudyClassifier::new(&mut chat).classify(Some(title), Some(abstract_text));
```

Any type implementing `JsonChat` will do, which is how the examples below run
offline: `Canned` answers every request with one JSON value.

```rust
use bmlib::agents::{ChatJsonError, ChatJsonOutcome};
use bmlib::llm::LLMMessage;
use bmlib::quality::{JsonChat, QualityAgent, StudyClassifier, StudyDesign};
use serde_json::{json, Value};

struct Canned(Value);

impl JsonChat for Canned {
    fn chat_json(
        &mut self, _messages: &[LLMMessage], _temperature: f64, _max_tokens: i64,
        _max_retries: usize, _require_dict: bool,
    ) -> Result<ChatJsonOutcome, ChatJsonError> {
        Ok(ChatJsonOutcome { value: self.0.clone(), attempts: 1, repaired: false })
    }
}

// Tier 2: design, confidence, sample size, blinding.
let mut chat = Canned(json!({
    "study_design": "rct", "confidence": 0.85, "sample_size": 120, "blinding": "double"
}));
let t2 = StudyClassifier::new(&mut chat).classify(Some("A trial"), Some("We randomised 120 adults."));
assert_eq!((t2.assessment_tier, t2.study_design), (2, StudyDesign::Rct));
assert_eq!(t2.sample_size, Some(120));

// Tier 3: the full assessment, with bias risk, strengths and limitations.
let mut chat = Canned(json!({
    "study_design": "cohort_prospective", "quality_score": 6, "confidence": 0.7,
    "bias_risk": {"selection": "low", "performance": "unclear", "detection": "low",
                  "attrition": "high", "reporting": "low"},
    "strengths": ["Large sample"], "limitations": ["No blinding"]
}));
let t3 = QualityAgent::new(&mut chat).with_temperature(0.0).assess(Some("A cohort"), Some("..."));
assert_eq!(t3.assessment_tier, 3);
assert_eq!(t3.bias_risk.unwrap().attrition, "high");

// Both missing: no model call, the unclassified result.
let empty = StudyClassifier::new(&mut chat).classify(None, None);
assert_eq!(empty.assessment_tier, 0);
```

Each tier takes `Option<&str>` for the title and abstract. With both empty no
request is made; any failure (transport, truncation, an unusable reply)
degrades to `QualityAssessment::unclassified()` rather than an error, as in
Python. The builders `with_temperature`, `with_max_tokens` and
`with_max_retries` override the Python defaults (`DEFAULT_TEMPERATURE`, …, in
each tier's module). The Python constructors' `TemplateEngine` is gone: the
prompts are module constants (`CLASSIFIER_USER_TEMPLATE`,
`ASSESSMENT_USER_TEMPLATE`, …) rendered by `format_template`, which follows
`str.format`'s `{{`/`}}` rule. The answer readers are pure functions in
`quality::llm_parsers` (`parse_classification`, …).

## The pipeline

Python's `QualityManager` holds a client and three agents. Here the pipeline
is a function, `manager::assess`, over a `TierRunner` of three callbacks —
Tier 2 and Tier 3 given `(title, abstract)`, Tier 4 given `(title, text)` and
returning `Option<CochraneStudyAssessment>` — so it owns no client and you
wire in whichever agents you like:

```rust
use bmlib::quality::manager::{assess, tier_label, TierRunner};
use bmlib::quality::{QualityAssessment, QualityFilter, StudyDesign};

let mut classify = |_t: &str, _a: &str| QualityAssessment::from_classification(
    StudyDesign::CaseControl, 0.8, None, None);
let mut deep = |_t: &str, _a: &str| QualityAssessment::unclassified();
let mut cochrane = |_t: &str, _x: &str| None;
let mut runner = TierRunner { classify: &mut classify, assess: &mut deep, cochrane: &mut cochrane };

// A conclusive Tier 1 stands under the default filter.
let rct = assess(&mut runner, "T", "A", &["Randomized Controlled Trial".into()],
                 &QualityFilter::default(), None);
assert_eq!(tier_label(&rct), "metadata");

// No publication types: Tier 2 decides.
let other = assess(&mut runner, "T", "A", &[], &QualityFilter::default(), None);
assert_eq!(other.study_design, StudyDesign::CaseControl);

// A Tier 3 that names no design is no answer, so the pipeline falls through.
let filter = QualityFilter { use_detailed_assessment: true, ..QualityFilter::default() };
let fallback = assess(&mut runner, "T", "A", &[], &filter, None);
assert_eq!(fallback.assessment_tier, 2);
```

The rules are Python's: `use_metadata_only` returns Tier 1; a confident
Tier 1 (`metadata_is_confident`) stands unless Tier 3 or 4 is enabled; a
successful Tier 4 supersedes Tier 3 and is folded into a classification by
`enrich_with_cochrane` (Tier 1's when it was conclusive, otherwise Tier 2's),
keeping that classification's `confidence` and `evidence_level`; a failed
Tier 4 or a Tier 3 naming no design falls through as if it had not been
asked. `full_text` falls back to the abstract for Tier 4. `next_tier` states
the same decision as a pure function returning a `TierStep`, for a caller
driving the tiers itself. There is no `assess_batch`; loop.

## Tier 4: the Cochrane assessor

`CochraneAssessor` produces a `CochraneStudyAssessment`: the
study-characteristics table and the nine-domain risk of bias.

```rust
use bmlib::agents::{ChatJsonError, ChatJsonOutcome};
use bmlib::llm::LLMMessage;
use bmlib::quality::{
    collapse_risk_of_bias, AssessOptions, CochraneAssessor, JsonChat, ROB_JUDGEMENT_LOW,
};
use serde_json::{json, Value};
# struct Canned(Value);
# impl JsonChat for Canned {
#     fn chat_json(&mut self, _: &[LLMMessage], _: f64, _: i64, _: usize, _: bool)
#         -> Result<ChatJsonOutcome, ChatJsonError> {
#         Ok(ChatJsonOutcome { value: self.0.clone(), attempts: 1, repaired: false })
#     }
# }

let mut chat = Canned(json!({
    "study_characteristics": {"methods": "Parallel-group RCT"},
    "risk_of_bias": {
        "random_sequence_generation": {"judgement": "Low risk",
                                       "support_for_judgement": "Computer-generated list"}
    },
    "overall_confidence": 0.8
}));
let mut assessor = CochraneAssessor::new(&mut chat);
let options = AssessOptions { study_id: Some("Smith 2024"), pmid: Some("12345"), ..Default::default() };
let a = assessor.assess(Some("A trial"), Some("full text ..."), options).expect("an assessment");

assert_eq!(a.study_id(), "Smith 2024");
assert_eq!(a.risk_of_bias.random_sequence_generation.judgement, ROB_JUDGEMENT_LOW);
assert_eq!(collapse_risk_of_bias(&a.risk_of_bias)?.selection, "unclear"); // worst of four
assert_eq!(assessor.get_stats().successful_assessments, 1);
# Ok::<(), bmlib::quality::UnknownBiasType>(())
```

`assess` returns `None` when no assessment could be made — both inputs empty,
a failed request, a reply with no `risk_of_bias`, an `overall_confidence`
below `AssessOptions::min_confidence` — never an all-`"Unclear risk"`
stand-in. `get_stats()` counts every call. `assess_batch(&[StudyInput], min_confidence, progress)`
loops over typed `StudyInput` records where Python takes dicts.
`collapse_risk_of_bias` reduces the nine domains to `BiasRisk`'s five, worst
rank winning, and is an `Err(UnknownBiasType)` for an item whose `bias_type`
is none of the five.

**Long papers.** Text over `DEFAULT_CONDENSE_THRESHOLD_CHARS` (48,000
characters; set another with `with_condense_config`) is condensed first by a
`Condenser`, and refused when none is configured (Python always has one).
`LlmCondenser::new(model, temperature, max_tokens)` is the production
map-reduce over a `context_processor::llm_processor::ContextModel`:
`quality::LlmContextModel` is the one over `LlmClient` (#641), and an
implementation of your own must return `Err` for a completion cut off at the
output ceiling. The result records `condensed_from_chars` and
`condensation_status`. *(0.4.0, #609)* `.with_progress(|info| …)` hands
the condensation's every `ProgressInfo` to a callback, Python's
`condense_progress`, for both agents.

```rust,ignore
let mut condenser = LlmCondenser::new(&my_context_model, 0.1, 4096)?;
let mut assessor = CochraneAssessor::new(&mut chat).with_condenser(&mut condenser);
```

## Cochrane tables

The formatters render any `CochraneStudyAssessment`, built by the assessor or
by hand. `create_default_cochrane_risk_of_bias()` starts from nine
`"Unclear risk"` domains:

```rust
use bmlib::quality::{
    create_default_cochrane_risk_of_bias, format_complete_assessment_markdown,
    format_risk_of_bias_html, format_risk_of_bias_markdown, CochraneInterventions,
    CochraneNotes, CochraneOutcomes, CochraneParticipants, CochraneStudyAssessment,
    CochraneStudyCharacteristics, ROB_JUDGEMENT_HIGH,
};

let mut rob = create_default_cochrane_risk_of_bias();
rob.blinding_participants_personnel.judgement = ROB_JUDGEMENT_HIGH.into();
rob.blinding_participants_personnel.support_for_judgement = "Open label".into();
assert_eq!(rob.summary_counts()["Unclear risk"], 8);

let markdown = format_risk_of_bias_markdown(&rob);
assert!(markdown.contains(
    "| Blinding of participants and personnel (performance bias) | High risk | Open label |"));
assert!(format_risk_of_bias_html(&rob).contains("judgement-high"));

let characteristics = CochraneStudyCharacteristics::new(
    "Andrei 2011", "Randomised controlled trial",
    CochraneParticipants::new("Romania", "Adults with asthma"),
    CochraneInterventions::new("Inhaled steroid versus placebo"),
    CochraneOutcomes::new("Exacerbations at 12 months"),
    CochraneNotes::default(),
);
let report = format_complete_assessment_markdown(&CochraneStudyAssessment::new(characteristics, rob));
assert!(report.starts_with("### Andrei 2011"));
```

The HTML renderers escape every value (`escape_html`) and pair with
`COCHRANE_CSS` / `get_cochrane_css()`; `format_multiple_assessments_markdown`
and `format_risk_of_bias_summary_markdown` take a slice. One difference:
`CochraneStudyCharacteristics::new` leaves `created_at` `None` where Python
stamps the current time; set it yourself if you store it.

## Rule-based extractors

Pure, LLM-free heuristics over a document given as a
`BTreeMap<String, String>` with any of the keys `"abstract"`,
`"methods_text"` and `"full_text"` (`prepare_extractor_search_text` prefers a
full text longer than the abstract). They return a `DimensionScore` with an
audit trail of `AssessmentDetail`s. Nothing in the tiered pipeline calls
them, in Python or here.

```rust
use std::collections::BTreeMap;
use bmlib::quality::{
    extract_sample_size_dimension, extract_study_type, find_sample_size,
    get_extracted_sample_size, get_extracted_study_type, has_ci_reporting,
    has_power_calculation,
};

let mut doc = BTreeMap::new();
doc.insert("abstract".to_string(), "In this randomized controlled trial, a total of 1,250 \
    patients were enrolled, giving 80% power to detect a difference. \
    The hazard ratio was 0.82 (95% CI 0.70-0.96).".to_string());

let design = extract_study_type(&doc);
assert_eq!(get_extracted_study_type(&design).as_deref(), Some("rct"));

let size = extract_sample_size_dimension(&doc);
assert_eq!(get_extracted_sample_size(&size), Some(1250)); // digit grouping read
assert_eq!(size.details.len(), 3);                         // n, power bonus, CI bonus

let text = &doc["abstract"];
assert_eq!(find_sample_size(text, 5, 1_000_000), Some(1250));
assert!(has_power_calculation(text) && has_ci_reporting(text));
assert!(!has_ci_reporting("No confidence intervals were reported."));
```

`extract_study_type` tries `STUDY_TYPE_PRIORITY` in order and the first type
with an unexcluded match wins (`"non-randomised controlled trial"` is not an
RCT); no match is `"unknown"` at a neutral 5.0. The sample-size score is
`log10(n) × 2`, plus 2.0 for a power calculation and 0.5 for confidence
intervals, capped at 10. The patterns are Python's, transcribed for
`fancy-regex`; Rust's `\w` and `\s` differ from Python's on combining marks
and U+001C–U+001F, which no abstract carries.

## PICO

`PicoAgent` reads a study's population, intervention, comparison and outcome,
and locates each part's quotes in the paper with `citations::find_passage`.
`check_suitability(title, text)` and `extract(title, text, min_similarity)`
are independent calls returning `Result<_, PicoError>`.

```rust
use bmlib::agents::{ChatJsonError, ChatJsonOutcome};
use bmlib::llm::LLMMessage;
use bmlib::quality::{JsonChat, PicoAgent, PicoPartState};
use serde_json::{json, Value};
# struct Canned(Value);
# impl JsonChat for Canned {
#     fn chat_json(&mut self, _: &[LLMMessage], _: f64, _: i64, _: usize, _: bool)
#         -> Result<ChatJsonOutcome, ChatJsonError> {
#         Ok(ChatJsonOutcome { value: self.0.clone(), attempts: 1, repaired: false })
#     }
# }

let text = "Adults aged 40-65 were randomised to metformin or placebo. HbA1c fell.";
let part = |d: &str, q: &str| json!({"description": d, "reported": true,
                                      "confidence": 0.9, "quotes": [q]});
let mut chat = Canned(json!({
    "population": part("Adults 40 to 65", "adults aged 40–65"),
    "intervention": part("Metformin", "randomised to metformin"),
    "comparison": part("Placebo", "the comparator was a sugar pill"),
    "outcome": {"description": "HbA1c", "reported": false, "quotes": []},
}));
let pico = PicoAgent::new(&mut chat).extract("A trial", text, 0.9)?;

let population = &pico.population;
assert_eq!(population.state, PicoPartState::Verified);
assert_eq!(population.citations[0].text, "Adults aged 40-65"); // the paper's own wording
assert_eq!(pico.comparison.state, PicoPartState::Unverified);  // quote not in the text
assert_eq!(pico.outcome.state, PicoPartState::NotReported);
# Ok::<(), bmlib::quality::PicoError>(())
```

A quote that `find_passage` rejected as a contradiction (a number, a negation
or a direction changed) is kept in `quotes_contradicted` with its reason, and a
reported part with no citation and at least one such quote is
`PicoPartState::Contradicted`: the
model misreported the paper *(0.4.0, #647)*.

Citation offsets are `char` indices into `text` exactly as passed. Quotes are
always verified against the original text, never a digest. `min_similarity`
outside (0, 1] is `PicoError::InvalidSimilarity` before any request (Python
raises `ValueError`); `PicoError::kind()` gives Python's `PicoErrorKind`
value, and the enum is `#[non_exhaustive]`. A part whose `description` is
blank is refused and re-asked as a non-text one is, and an integer
`sample_size` is kept as its digits (`150` → `"150"`, a float refused)
*(0.4.0, #578)*. Every `ChatError` the client returns to the request, an
unknown provider or a missing API key included, is `RequestFailed` (during
the condensation of a long text it is `CondensationFailed`), where Python lets those
propagate as exceptions (#573; see `docs/DECISIONS.md`).

**Long papers.** Text over the threshold (48,000 characters, like the
Cochrane assessor) needs a condenser over the PICO prompts, built by
`PicoAgent::pico_condenser(model, temperature, max_tokens)` from your
`ContextModel`; without one the call is `PicoError::CondensationFailed`, where
Python condenses through the agent's own client.

```rust,ignore
let mut condenser = PicoAgent::pico_condenser(&my_context_model, 0.1, 4096)?;
let mut agent = PicoAgent::new(&mut chat).with_condenser(&mut condenser);
let pico = agent.extract(title, &full_text, 0.9)?;
```

The models carry `to_json` (Python's `to_dict`, key for key) and no
`from_json`, so reading a stored result back is Python's job.

## PRISMA 2020

*(unreleased)* `Prisma2020Agent` judges how fully a systematic review
reports each item of the PRISMA 2020 checklist, a part of the checklist at a
time, with every judgement backed by a quote located in the paper. The rules
(the checklist's fields, the split, the strict-evidence rule, the failures)
are Python's, in [quality.md](../../../../docs/manual/quality.md#prisma-2020-agent-unreleased);
this section gives the Rust shapes.

```rust
use bmlib::agents::{ChatJsonError, ChatJsonOutcome};
use bmlib::llm::LLMMessage;
use bmlib::quality::prisma2020_checklist::item;
use bmlib::quality::prisma2020_models::Judgement;
use bmlib::quality::{plan_parts, ChecklistItem, JsonChat, Prisma2020Agent, CHECKLIST};
use serde_json::{json, Value};
# struct Canned(Vec<Value>);
# impl JsonChat for Canned {
#     fn chat_json(&mut self, _: &[LLMMessage], _: f64, _: i64, _: usize, _: bool)
#         -> Result<ChatJsonOutcome, ChatJsonError> {
#         Ok(ChatJsonOutcome { value: self.0.remove(0), attempts: 1, repaired: false })
#     }
# }

// The whole checklist, split to a 12,000-character reply budget.
let table: &'static [ChecklistItem; 42] = &CHECKLIST;
let all: Vec<&'static ChecklistItem> = table.iter().collect();
assert_eq!(plan_parts(&all, 12_000)?.len(), 4);

// One part of two items, judged over a paper short enough to send whole.
let rows: Vec<&'static ChecklistItem> = ["6", "7"].iter().filter_map(|id| item(id)).collect();
let plan = plan_parts(&rows, 12_000)?.remove(0);
assert_eq!(plan.label(), "Methods 6–7");

let search = "We searched MEDLINE and Embase from inception to March 2020";
let text = format!("Methods. {search}. Two reviewers screened every record.");
let mut chat = Canned(vec![json!({"items": [
    {"id": "6", "judgement": "reported", "explanation": "e", "quotes": [search]},
    {"id": "7", "judgement": "reported", "explanation": "e", "quotes": ["not in it"]},
    {"id": 99, "judgement": "reported", "explanation": "e", "quotes": []},
]})]);
let mut agent = Prisma2020Agent::new(&mut chat);
let prepared = agent.prepare_part("A review", &text, &plan.item_ids)?;
let part = agent.assess_part(&prepared, &plan.item_ids, 0.9)?;

assert_eq!(part.items[0].judgement, Judgement::Reported);
assert_eq!(part.items[1].model_judgement, Judgement::Reported);
assert_eq!(part.items[1].judgement, Judgement::NotReported); // no quote was located
assert!(part.items[1].unbacked);
assert_eq!(part.unasked, ["99"]); // an integer id reads as its digits
# Ok::<(), Box<dyn std::error::Error>>(())
```

`CHECKLIST` is a `const` array, so borrow it as `&'static` (as above) to hand
its rows to `plan_parts`, or take rows from `prisma2020_checklist::item(id)`.
`Judgement` and `read_suitability` are reached through `prisma2020_models`
only, since the RoB 2 and PICO modules export the same names.

The agent has `PicoAgent`'s builders: `with_stop`, `with_condenser`,
`with_temperature`, `with_max_tokens` (default 16,384), `with_max_retries`
and `with_condense_config` (its `continue_on_error` is always set to false).
`num_ctx` belongs to the chat (`LlmChat::with_num_ctx`), as for PICO. A call
returns `Result<_, PrismaError>`, whose `kind()` is Python's
`PrismaErrorKind` value, plus four kinds that Python raises as `ValueError`:
`InvalidItems` (ids empty, repeated or not on the checklist, or a digest
asked for items it was not condensed for), `InvalidSimilarity`,
`PreparedOverBudget` and `PreparedUnderBudget`. A `PartsError` from
`plan_parts` converts into `PrismaError::Parts`.

**Long papers.** Text over the threshold needs a condenser,
`PrismaCondenser::new(&context_model, temperature, max_tokens)` (with
`with_progress` for its reports); without one the call is
`PrismaError::CondensationFailed`, where Python condenses through the
agent's own client. A digest whose run did not complete is refused as
`CondensationFailed`, as in Python.

```rust,ignore
let mut condenser = PrismaCondenser::new(&my_context_model, 0.1, 4096);
let mut agent = Prisma2020Agent::new(&mut chat).with_condenser(&mut condenser);
let prepared = agent.prepare_part(title, &full_text, &ids)?;
```

`part_prompt_chars(&ids)` and `suitability_prompt_chars()` measure the prompt
around the paper, as Python's do. The results carry `to_json`, Python's
`to_dict`, key for key.

## Evidence weight

*(0.14.0)* `assess_unattended` takes one paper from full text to a number,
with nobody confirming a step: the design proposal, then the primary
outcomes, then RoB 2 over all of them, then an evidence ladder per outcome,
then the paper's weight. The rules (which designs are covered, the RoB 2
tables, the ladder's steps and weights, what counts as a failure) are
Python's, in
[quality.md](../../../../docs/manual/quality.md#unattended-evidence-weight-0140);
this section gives the Rust shapes. Every name below is re-exported at
`bmlib::quality`, and `Judgement` there is RoB 2's.

The decision rules and the ladder are pure functions, with no model:

```rust
use bmlib::quality::{
    build_ladder, domain_judgement, overall_judgement, paper_weight, start_level, Answer,
    Answers, Domain, EffectScale, Judgement, LadderReason, LadderSettings, Level,
    ReportedResult, Rob2Error, StudyDesign,
};

// Domain 1 from its signalling answers.
let mut answers = Answers::new();
answers.insert("1.1".into(), Answer::Y);
answers.insert("1.2".into(), Answer::Py);
answers.insert("1.3".into(), Answer::N);
assert_eq!(domain_judgement(Domain::D1, &answers)?, Judgement::Low);
answers.insert("1.2".into(), Answer::Ni);
assert_eq!(domain_judgement(Domain::D1, &answers)?, Judgement::SomeConcerns);
answers.remove("1.3"); // an applicable question unanswered is refused
assert_eq!(domain_judgement(Domain::D1, &answers),
           Err(Rob2Error::MissingAnswer { question: "1.3".into() }));

// Two domains with some concerns and none high: the assessor decides.
use Judgement::{Low, SomeConcerns};
let domains = [SomeConcerns, Low, Low, Low, SomeConcerns];
assert_eq!(overall_judgement(&domains, None), Err(Rob2Error::EscalationUndecided));
assert_eq!(overall_judgement(&domains, Some(false))?, SomeConcerns);

// The ladder: an RCT starts high; some concerns and a small trial cost a level each.
let start = start_level(StudyDesign::Rct).expect("an RCT has a ladder");
let result = ReportedResult {
    measure: "risk ratio".into(), scale: EffectScale::Ratio, estimate: 0.7,
    ci_low: Some(0.55), ci_high: Some(0.88), ci_level: Some(0.95), n_analysed: Some(240),
};
let ladder = build_ladder(start, SomeConcerns, Some(&result), &LadderSettings::default())?;
assert_eq!((ladder.level, ladder.weight), (Level::Low, 0.4));
assert_eq!(ladder.steps[1].reason, LadderReason::SmallTrial { n: 240, threshold: 400 });
assert_eq!(paper_weight(&[ladder]), Some(0.4));
assert_eq!(start_level(StudyDesign::CohortProspective), None); // not covered
# Ok::<(), Box<dyn std::error::Error>>(())
```

The pipeline asks through one `JsonChat`, in order: the design, the
outcomes, D1 once for the trial, then for each outcome D2, D3, D4 and D5
and, if it needs one, its escalation question. D5 asks no signalling
question: the model reports facts about the plan
(`rob2_d5_checks::D5_FACTS`), with a second look at each other measurement
whose result it did not find, and code answers 5.1-5.3 from them. A reply
that cannot be read is asked once more.

```rust
use bmlib::agents::{ChatJsonError, ChatJsonOutcome};
use bmlib::llm::LLMMessage;
use bmlib::quality::rob2_d5_checks::D5_FACTS;
use bmlib::quality::{
    assess_unattended, AgentOptions, EvidenceWeight, JsonChat, Judgement, LadderSettings,
    Level,
};
use serde_json::{json, Map, Value};
# struct Canned(Vec<Value>);
# impl JsonChat for Canned {
#     fn chat_json(&mut self, _: &[LLMMessage], _: f64, _: i64, _: usize, _: bool)
#         -> Result<ChatJsonOutcome, ChatJsonError> {
#         Ok(ChatJsonOutcome { value: self.0.remove(0), attempts: 1, repaired: false })
#     }
# }

let allocated = "Participants were randomly assigned 1:1 to metformin or placebo.";
let primary = "The primary outcome was all-cause mortality at 28 days.";
let text = format!("{allocated} Allocation was concealed centrally. {primary}");

let domain = |answers: &[(&str, &str)]| json!({"answers": answers.iter()
    .map(|(q, a)| (q.to_string(), json!({"answer": a, "quotes": [], "reasoning": ""})))
    .collect::<Map<_, _>>()});
let facts: Map<_, _> = D5_FACTS.iter()
    .map(|f| (f.as_str().to_string(), json!({"present": false, "quotes": []})))
    .collect();
let mut chat = Canned(vec![
    json!({"study_design": "rct", "rct_layout": "parallel", "quotes": [allocated]}),
    json!({"primary_outcomes": [{"name": "All-cause mortality", "timepoint": "28 days",
        "quotes": [primary], "result": {"measure": "risk ratio", "scale": "ratio",
        "estimate": 0.7, "ci_low": 0.55, "ci_high": 0.88, "ci_level": 95, "n_analysed": 1200}}]}),
    domain(&[("1.1", "Y"), ("1.2", "Y"), ("1.3", "N")]),
    domain(&[("2.1", "N"), ("2.2", "N"), ("2.6", "Y")]),
    domain(&[("3.1", "Y")]),
    domain(&[("4.1", "N"), ("4.2", "N"), ("4.3", "N")]),
    // D5: no fact about the plan stated, so 5.1-5.3 are NI: some concerns.
    json!({"facts": facts, "other_measurements": [],
           "main_analysis": {"differs": false, "planned": [], "reported": []}}),
]);
let weight = assess_unattended(&mut chat, None, None, &LadderSettings::default(),
                               &AgentOptions::default(), "A trial", &text, 0.9)?;
assert_eq!(EvidenceWeight::from_json(&weight.to_json())?, weight);

let EvidenceWeight::Assessed { assessed, paper_weight, condensed, .. } = weight else {
    panic!("a parallel-group RCT with a primary outcome is assessed");
};
assert_eq!(assessed[0].rob2.overall, Some(Judgement::SomeConcerns));
assert_eq!(assessed[0].ladder.as_ref().map(|l| l.level), Some(Level::Moderate));
assert_eq!((paper_weight, condensed), (Some(0.7), false));

// A design the RoB 2 questions do not cover ends after one request.
let mut chat = Canned(vec![json!({"study_design": "cohort_prospective", "quotes": []})]);
let weight = assess_unattended(&mut chat, None, None, &LadderSettings::default(),
                               &AgentOptions::default(), "A cohort", &text, 0.9)?;
assert!(matches!(weight, EvidenceWeight::NotCovered { .. }));
# Ok::<(), Box<dyn std::error::Error>>(())
```

**The result.** `EvidenceWeight` is `NotCovered { design }` (not a
parallel-group RCT: `start_level` is `None` or `RctLayout::is_covered` is
false), `NoPrimaryOutcome { design, outcomes }`, or `Assessed`, which holds
the proposals, the `Rob2Run`, one `AssessedOutcome` per outcome in order,
`paper_weight`, `condensed` (true if any step saw a digest), and the
`ladder_version` (`LADDER_VERSION`) and `LadderSettings` the ladders were
built with. An `AssessedOutcome` has exactly one of `ladder` and
`ladder_error`: the error says why there is none (a domain or the escalation
failed, so `rob2.overall` is `None`; the reported result was refused; the
result cannot be laddered). `paper_weight` is the largest ladder weight,
`None` when no ladder was built. A failure is never a score.

**Errors.** `assess_unattended` is `Err(EvidenceWeightError)` only when a
step could not be made at all, wrapping that step's own error:
`Proposal(ProposalError)` for the design or outcome proposal (`EmptyInput`,
`CondensationFailed`, `RequestFailed { message, retryable, status }`,
`Unreadable`, `Stopped` and `InvalidSimilarity`), or `Rob2(Rob2RunError)`
(`EmptyInput`, `NoOutcomes`, `Stopped`, `InvalidSimilarity`). A domain whose
request fails or whose reply stays unreadable is not an error: it is
`DomainResult::Failed { domain, reason, retryable, status }` in the run, and
the outcomes it touches are not assessed. A failed escalation is
`OutcomeRob2::escalation_error`, with `escalation_retryable` and
`escalation_status`. Retry only when `retryable`; a 401 or 403 means the
key. `ProposalError` is `#[non_exhaustive]`.

**Options.** `AgentOptions` (temperature, `max_tokens`, `max_retries`,
`condense_config`) reaches all three agents; its `Default` is theirs,
`max_tokens` 16,384 since a reasoning model's thinking counts against it.
`num_ctx` belongs to the chat (`LlmChat::with_num_ctx`), where Python takes
it as a keyword argument. `stop` is read before each request.
`LadderSettings::default()` is the shipped ladder; `validate` refuses a
weight outside 0 to 1 or weights that increase down the ladder, and
`build_ladder` and deserialisation call it. `build_ladder` is `Err(String)`
for such settings or a `ReportedResult` that fails `validate`; nothing is
repaired.

**The steps alone.** A reader who confirms each step uses the agents
directly: `DesignAgent::propose(title, text, min_similarity)` gives a
`DesignProposal` (`design`, `rct_layout` set exactly for an RCT, quotes
located as in PICO), `OutcomeAgent::propose` an `OutcomeProposal` of
`ProposedOutcome`s (a `ci_level` printed as `95` is read as 0.95; an
unreadable or incoherent `result` is that outcome's `result_error`, and the
outcome stays), and
`Rob2Agent::assess(title, text, &outcomes, min_similarity)` a `Rob2Run`.
Each has `PicoAgent`'s builders (`with_stop`, `with_condenser`,
`with_temperature`, `with_max_tokens`, `with_max_retries`,
`with_condense_config`). The replies are read by pure functions:
`read_design_reply`, `read_outcome_reply`, `read_domain_reply`
(`NOT_APPLICABLE`, also `N/A` or `Not applicable`, is unanswered; an answer
to a question that does not apply is listed in `ignored`) and
`read_escalation_reply`. `domain_prompt` (D1-D4),
`rob2_d5_facts::facts_prompt` and `second_look_prompt` (D5) and
`escalation_message` build the user turns, `rob2_overall` the overall
judgement of one outcome (`None` when it is not assessed). The prompts are
constants (`DESIGN_SYSTEM_PROMPT`, `DESIGN_TASK_PROMPT`,
`DESIGN_CONDENSE_QUERY`, the `OUTCOME_*` ones, `ROB2_SYSTEM_PROMPT`,
`ROB2_DOMAIN_PROMPT`), and the results record the wording that asked them
(`DESIGN_PROMPT_VERSION`, `OUTCOME_PROMPT_VERSION`,
`rob2_questions::ROB2_PROMPT_VERSION`) and the rules that judged them
(`ROB2_ALGORITHM_VERSION`). In `rob2_algorithm`, `questions(domain)` lists a
domain's ids, `applicable_questions` those the answers so far open,
`needs_escalation_decision` whether the assessor must decide, and
`Domain::ALL` is Python's `DOMAINS_ALL`; D1 is `Scope::Study`, the rest
`Scope::Outcome`.

**Long papers.** Text over the threshold (48,000 characters) needs a
`QueryCondenser`: each step condenses once for its own query, RoB 2 once
per domain. `LlmQueryCondenser::new(&context_model, temperature,
max_tokens)` is the production one; without one the step fails as
`CondensationFailed` (a RoB 2 domain as `DomainResult::Failed`), where
Python condenses through the agent's own client. Quotes are always located
in the text as passed, never in a digest.

```rust,ignore
let mut condenser = LlmQueryCondenser::new(&my_context_model, 0.1, 4096);
let weight = assess_unattended(&mut chat, Some(&mut condenser), None,
    &LadderSettings::default(), &AgentOptions::default(), title, &full_text, 0.9)?;
```

**RoB 2 a step at a time.** `assess` runs the whole plan at once. An app
that shows progress, lets the reader stop and resume, or keeps each domain
as it arrives drives the same steps itself. `plan_rob2(outcome_count)` lists
them in `assess`'s order: D1 once, then for each outcome D2 to D5 and its
escalation; it is empty for no outcomes. A `Rob2Step` is
`Domain { domain, outcome }` (`outcome` an index into the outcomes you pass,
`None` for D1) or `Escalation { outcome }`, and `to_json` gives the wire form
`tests/fixtures/rob2_steps.json` pins in both languages.

```rust
use bmlib::agents::{ChatJsonError, ChatJsonOutcome};
use bmlib::llm::LLMMessage;
use bmlib::quality::rob2_algorithm::Domain;
use bmlib::quality::{
    combine_outcome, plan_rob2, DomainResult, JsonChat, Judgement, ProposedOutcome, Rob2Agent,
    Rob2Digests, Rob2Step,
};
use serde_json::{json, Map, Value};
# struct Canned(Vec<Value>);
# impl JsonChat for Canned {
#     fn chat_json(&mut self, _: &[LLMMessage], _: f64, _: i64, _: usize, _: bool)
#         -> Result<ChatJsonOutcome, ChatJsonError> {
#         Ok(ChatJsonOutcome { value: self.0.remove(0), attempts: 1, repaired: false })
#     }
# }
use bmlib::quality::rob2_d5_checks::D5_FACTS;

let text = "Participants were randomly assigned 1:1. The primary outcome was mortality.";
let outcome = ProposedOutcome {
    name: "Mortality".into(), timepoint: None, result: None, result_error: None,
    citations: vec![], quotes_not_found: vec![], contradicted: vec![], quotes_dropped: 0,
};
let domain = |answers: &[(&str, &str)]| json!({"answers": answers.iter()
    .map(|(q, a)| (q.to_string(), json!({"answer": a, "quotes": [], "reasoning": ""})))
    .collect::<Map<_, _>>()});
let facts: Map<_, _> = D5_FACTS.iter()
    .map(|f| (f.as_str().to_string(), json!({"present": false, "quotes": []})))
    .collect();
let mut chat = Canned(vec![
    domain(&[("1.1", "Y"), ("1.2", "Y"), ("1.3", "N")]),
    domain(&[("2.1", "N"), ("2.2", "N"), ("2.6", "Y")]),
    domain(&[("3.1", "Y")]),
    domain(&[("4.1", "N"), ("4.2", "N"), ("4.3", "N")]),
    json!({"facts": facts, "other_measurements": [],
           "main_analysis": {"differs": false, "planned": [], "reported": []}}),
]);

let plan = plan_rob2(1); // D1, D2, D3, D4, D5, then the escalation
assert_eq!(plan.len(), 6);
let mut agent = Rob2Agent::new(&mut chat);
let mut digests = Rob2Digests::default(); // one paper's condensations
let (mut study, mut domains) = (None, Vec::new());
for step in &plan {
    match *step {
        Rob2Step::Domain { domain, outcome: index } => {
            let one = index.map(|_| &outcome);
            let result = agent.assess_domain(domain, "A trial", text, one, 0.9, &mut digests)?;
            if domain == Domain::D1 { study = Some(result) } else { domains.push(result) }
        }
        Rob2Step::Escalation { .. } => {
            let study = study.as_ref().expect("D1 comes first");
            // D5 had no stated plan: one domain has some concerns, so no question is asked.
            let escalation = agent.escalate_outcome("A trial", &outcome, study, &domains)?;
            let rob2 = combine_outcome(&outcome, study, domains.clone(), escalation);
            assert!(matches!(domains[3], DomainResult::Assessed(_)));
            assert_eq!(rob2.overall, Some(Judgement::SomeConcerns));
        }
    }
}
# Ok::<(), Box<dyn std::error::Error>>(())
```

`assess_domain(domain, title, text, outcome, min_similarity, &mut digests)`
makes one domain's request or requests (more than one when a reply is
re-asked or the paper condensed first) and returns a `DomainResult`, as
`assess` records it. It reads the stop first, so a stop set between two
calls ends the next as `Rob2RunError::Stopped` even where no request would
have been sent (a cached failed digest); `assess` does the same.
`Rob2RunError::WrongOutcome` (before any request) means D1 was given an
outcome, or D2-D5 none. `Rob2Digests` is the caller's to keep for one paper
within one process; it is not stored, so a Continue from storage condenses
again.
`escalate_outcome(title, outcome, study, domains)` is a `Rob2Escalation`:
`NotNeeded`, `Asked(Escalation)` or `Failed { reason, retryable, status }`.
It and `combine_outcome` panic, before any request, when `domains` is not
D2, D3, D4, D5 in order (Python raises `ValueError`). Run the escalation
step again whenever its outcome's D1 or any of its D2-D5 is run again: it
sends no request when the question is not needed, and an escalation kept from
the earlier domains no longer fits the new ones. `combine_outcome` panics on
such a stale one: `Asked` or `Failed` where the domains do not call for the
question, or `NotNeeded` where they do.

To size the paper to the window, `design_prompt_chars()`,
`outcome_prompt_chars()` and `domain_prompt_chars(domain, outcome)` (an
`Err(String)` for a wrong outcome) give the characters of the longest prompt
each step sends without the title and paper, counted as `part_prompt_chars`
counts. D5's measure leaves out a second look's measurement label, named
text and passages, which vary with the reply. `LlmQueryCondenser::new(..)`
also has `with_progress(callback)`, as Python's takes `progress`.

Every result type has `to_json`, Python's `to_dict`, and a checked
`from_json`. `EvidenceWeight::from_json` reads any status and refuses
another `ladder_version`, settings that do not validate, parts of different
lengths and a `paper_weight` that is not the largest ladder weight, but
does not rebuild a stored ladder from its inputs (#813);
`Rob2Run::from_json` refuses a run judged by other rules and an `overall`
that does not follow from its domains.
