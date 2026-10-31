# bmlib::quality — Quality Assessment

Tiered study-quality assessment: free classification from PubMed publication
types (Tier 1), a cheap LLM classifier (Tier 2), a deep LLM assessment
(Tier 3) and a Cochrane-aligned risk-of-bias assessment (Tier 4), plus the
rule-based extractors and the PICO agent, which stand outside the tiers. The
Python page is [quality.md](../../../../docs/manual/quality.md); this one
describes the Rust API and where it differs.

| Need | Feature |
|---|---|
| Tier 1, the models, the filter, the tier rules, the extractors, the Cochrane formatters | none |
| The LLM tiers and `PicoAgent` against your own `JsonChat` | none |
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
map-reduce over a `context_processor::llm_processor::ContextModel`, which you
implement over your client — the crate ships no `ContextModel`, and an
implementation must return `Err` for a completion cut off at the output
ceiling. The result records `condensed_from_chars` and `condensation_status`.

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

Citation offsets are `char` indices into `text` exactly as passed. Quotes are
always verified against the original text, never a digest. `min_similarity`
outside (0, 1] is `PicoError::InvalidSimilarity` before any request (Python
raises `ValueError`); `PicoError::kind()` gives Python's `PicoErrorKind`
value, and the enum is `#[non_exhaustive]`. A part whose `description` is
blank is refused and re-asked as a non-text one is, and an integer
`sample_size` is kept as its digits (`150` → `"150"`, a float refused)
*(unreleased, #578)*. Every `ChatError` the client returns, an unknown provider
or a missing API key included, is `RequestFailed`, where Python lets those
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
