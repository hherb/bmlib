// bmlib — shared library for biomedical literature tools
// Copyright (C) 2024-2026 Dr Horst Herb
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with this program.  If not, see <https://www.gnu.org/licenses/>.

//! The three LLM quality tiers, driven by a scripted source.
//!
//! Tiers 2, 3 and 4 never hold a client: each takes a
//! [`JsonChat`] whose default production implementation is
//! [`LlmChat`]. The scripted implementation below is a real
//! [`ChatSource`] underneath, so the retry, truncation and `require_dict` rules
//! the tiers inherit are [`bmlib::agents::chat_json`]'s and not a test double's
//! — a script that answered `Ok`/`Err` directly would test nothing about them.

use std::collections::BTreeMap;

use bmlib::agents::{chat_json, ChatJsonError, ChatJsonOutcome, ChatSource};
use bmlib::context_processor::{
    ExtractionResult, ProcessingConfig, ProcessingResult, ProcessingStatus,
    DEFAULT_MAX_CONTEXT_CHARS,
};
use bmlib::llm::{LLMMessage, LLMResponse, LlmClient};
use bmlib::publications::fetchers::registry::{FetchError, HttpClient, HttpResponse};
use bmlib::quality::agent_chat::{format_template, JsonChat};
use bmlib::quality::cochrane_assessor::{
    parse_cochrane_assessment, render_condense_consolidation, render_condense_extraction,
    AssessOptions, CochraneAssessor, Condenser, StudyInput,
};
use bmlib::quality::data_models::BiasRisk;
use bmlib::quality::quality_agent::QualityAgent;
use bmlib::quality::study_classifier::StudyClassifier;
use serde_json::{json, Value};

// ---------------------------------------------------------------------------
// The scripted source
// ---------------------------------------------------------------------------

/// One response per call, repeating the last, and a record of the backoffs.
struct Script {
    responses: Vec<Result<LLMResponse, String>>,
    calls: usize,
    backoffs: Vec<usize>,
}

impl Script {
    fn new(responses: Vec<Result<LLMResponse, String>>) -> Self {
        Script {
            responses,
            calls: 0,
            backoffs: Vec::new(),
        }
    }

    /// A source answering `content` with `stop_reason`, forever.
    fn answering(content: &str, stop_reason: Option<&str>) -> Self {
        Script::new(vec![Ok(response(content, stop_reason))])
    }
}

fn response(content: &str, stop_reason: Option<&str>) -> LLMResponse {
    LLMResponse {
        content: content.to_string(),
        stop_reason: stop_reason.map(str::to_string),
        ..Default::default()
    }
}

impl ChatSource for Script {
    fn chat(&mut self, _attempt: usize) -> Result<LLMResponse, String> {
        let index = self.calls.min(self.responses.len().saturating_sub(1));
        self.calls += 1;
        self.responses[index].clone()
    }

    fn backoff(&mut self, attempt: usize) {
        self.backoffs.push(attempt);
    }
}

/// What one tier asked for.
#[derive(Debug, Clone, PartialEq)]
struct Call {
    temperature: f64,
    max_tokens: i64,
    max_retries: usize,
    require_dict: bool,
}

/// A [`JsonChat`] that is really [`bmlib::agents::chat_json`] over a [`Script`].
struct ScriptedChat {
    source: Script,
    seen: Vec<Vec<LLMMessage>>,
    calls: Vec<Call>,
}

impl ScriptedChat {
    fn new(source: Script) -> Self {
        ScriptedChat {
            source,
            seen: Vec::new(),
            calls: Vec::new(),
        }
    }

    fn answering(content: &str, stop_reason: Option<&str>) -> Self {
        ScriptedChat::new(Script::answering(content, stop_reason))
    }

    /// The user turn of the last call, or `""` when nothing was sent.
    fn last_user_message(&self) -> &str {
        self.seen
            .last()
            .and_then(|messages| messages.iter().find(|m| m.role == bmlib::llm::Role::User))
            .map(|m| m.content.as_str())
            .unwrap_or("")
    }
}

impl JsonChat for ScriptedChat {
    fn chat_json(
        &mut self,
        messages: &[LLMMessage],
        temperature: f64,
        max_tokens: i64,
        max_retries: usize,
        require_dict: bool,
    ) -> Result<ChatJsonOutcome, ChatJsonError> {
        self.seen.push(messages.to_vec());
        self.calls.push(Call {
            temperature,
            max_tokens,
            max_retries,
            require_dict,
        });
        chat_json(
            &mut self.source,
            max_retries,
            temperature,
            Some(max_tokens),
            max_tokens,
            require_dict,
        )
    }
}

// ---------------------------------------------------------------------------
// Tier 2 — the classifier
// ---------------------------------------------------------------------------

/// The happy path: the model's design, confidence, sample size and blinding are
/// recorded, and the call carries the Tier 2 sampling defaults the Python's
/// `StudyClassifier.__init__` sets — a low temperature because the shape is
/// fixed, and a budget well above the ~50 tokens it needs.
#[test]
fn tier2_classifies_a_model_answer() {
    let json = json!({
        "study_design": "rct",
        "confidence": 0.9,
        "sample_size": 120,
        "blinding": "double"
    })
    .to_string();
    let mut chat = ScriptedChat::answering(&json, Some("stop"));

    let assessment = {
        let mut classifier = StudyClassifier::new(&mut chat);
        classifier.classify(Some("A trial"), Some("We randomised 120 adults."))
    };

    assert_eq!(assessment.assessment_tier, 2);
    assert_eq!(assessment.extraction_method, "llm_classifier");
    assert_eq!(assessment.study_design.member_name(), "RCT");
    assert_eq!(assessment.confidence, 0.9);
    assert_eq!(assessment.sample_size, Some(120));
    assert_eq!(assessment.is_blinded.as_deref(), Some("double"));
    assert_eq!(
        chat.calls,
        vec![Call {
            temperature: 0.1,
            max_tokens: 1024,
            max_retries: 3,
            require_dict: true,
        }],
        "the Python's defaults, and the shape demanded"
    );
}

/// **With both title and abstract missing there is no call at all.** An empty
/// prompt does not yield an empty answer — the model invents a plausible design,
/// and nothing downstream can tell that from a real classification.
#[test]
fn tier2_makes_no_call_for_an_empty_paper() {
    let mut chat = ScriptedChat::answering("{}", Some("stop"));
    let assessment = {
        let mut classifier = StudyClassifier::new(&mut chat);
        classifier.classify(None, None)
    };

    assert_eq!(chat.source.calls, 0, "no model call");
    assert!(chat.seen.is_empty(), "and nothing was sent");
    assert_eq!(
        assessment,
        bmlib::quality::data_models::QualityAssessment::unclassified()
    );
}

/// An empty string is a missing field, and whitespace is not a title: the guard
/// is on `.strip()`, so `"   "` counts as nothing.
#[test]
fn tier2_treats_whitespace_as_empty() {
    for (title, abstract_text) in [(Some("   "), Some("\n\t")), (Some(""), Some(""))] {
        let mut chat = ScriptedChat::answering("{}", Some("stop"));
        let assessment = {
            let mut classifier = StudyClassifier::new(&mut chat);
            classifier.classify(title, abstract_text)
        };
        assert_eq!(chat.source.calls, 0, "{title:?}/{abstract_text:?}");
        assert_eq!(assessment.assessment_tier, 0);
    }
}

/// **A title alone is classified.** Sources omit abstracts often enough, and a
/// nullable database column delivers the gap that way; raising would abort the
/// caller's whole batch.
#[test]
fn tier2_classifies_from_the_title_alone() {
    let json = json!({"study_design": "case_report"}).to_string();
    let mut chat = ScriptedChat::answering(&json, Some("stop"));
    let assessment = {
        let mut classifier = StudyClassifier::new(&mut chat);
        classifier.classify(Some("A single case"), None)
    };
    assert_eq!(chat.source.calls, 1);
    assert_eq!(assessment.study_design.member_name(), "CASE_REPORT");
}

/// The rendered prompt is the Python's `.format()` output, which **collapses the
/// doubled braces**: the model is sent `{` where the constant carries `{{`. A
/// naive `replace("{title}", …)` would send it the doubled form instead.
#[test]
fn tier2_sends_the_formatted_prompt() {
    let json = json!({"study_design": "rct"}).to_string();
    let mut chat = ScriptedChat::answering(&json, Some("stop"));
    {
        let mut classifier = StudyClassifier::new(&mut chat);
        classifier.classify(Some("A title"), Some("An abstract"));
    }

    let sent = chat.last_user_message();
    assert!(
        sent.starts_with("Classify this paper's study design:"),
        "{sent}"
    );
    assert!(sent.contains("Title: A title"), "{sent}");
    assert!(sent.contains("Abstract: An abstract"), "{sent}");
    assert!(
        sent.contains("Return JSON:\n{\n    \"study_design\""),
        "the braces are collapsed, as str.format collapses them: {sent}"
    );
    assert!(!sent.contains("{{"), "no doubled brace survives: {sent}");
}

/// **The guard strips and the prompt does not.** A padded title is sent to the
/// model padded, exactly as the Python sends it — the prompt is part of the
/// behaviour, so tidying the whitespace here would put different bytes in front
/// of the model than the Python put there.
#[test]
fn a_padded_title_reaches_the_model_padded() {
    let mut chat = ScriptedChat::answering("{}", Some("stop"));
    {
        let mut classifier = StudyClassifier::new(&mut chat);
        classifier.classify(Some("  A trial  "), Some("  An abstract  "));
    }
    let sent = chat.last_user_message();
    assert!(sent.contains("Title:   A trial  \n"), "{sent:?}");
    assert!(sent.contains("Abstract:   An abstract  \n"), "{sent:?}");
}

/// The abstract is cut to [`MAX_ABSTRACT_CHARS`] **characters** — not bytes, or
/// a cut would split a multi-byte character.
#[test]
fn tier2_truncates_the_abstract_to_the_character_budget() {
    let abstract_text = "ü".repeat(5000);
    let mut chat = ScriptedChat::answering("{}", Some("stop"));
    {
        let mut classifier = StudyClassifier::new(&mut chat);
        classifier.classify(Some("T"), Some(&abstract_text));
    }

    let sent = chat.last_user_message().to_string();
    let sent_abstract = sent
        .split_once("Abstract: ")
        .expect("the template names the abstract")
        .1;
    assert_eq!(
        sent_abstract.chars().filter(|c| *c == 'ü').count(),
        3000,
        "MAX_ABSTRACT_CHARS, in characters"
    );
}

/// **A transport failure degrades the paper rather than raising.** It is the
/// `except Exception` the Python wraps the call in, and it is load-bearing: a
/// parse or transport failure must not cost the caller's batch.
#[test]
fn tier2_degrades_on_a_transport_failure() {
    let mut chat = ScriptedChat::new(Script::new(vec![Err("connection refused".to_string())]));
    let assessment = {
        let mut classifier = StudyClassifier::new(&mut chat);
        classifier.classify(Some("A trial"), Some("An abstract"))
    };

    assert_eq!(
        assessment,
        bmlib::quality::data_models::QualityAssessment::unclassified()
    );
    assert_eq!(
        chat.source.calls, 1,
        "a request that never arrived is not retried"
    );
}

/// **A truncated response degrades too, and it is the retry loop that decides
/// that** — at a non-zero temperature the truncation is retried, and the tier
/// sees only the final error.
#[test]
fn tier2_degrades_on_a_truncated_response() {
    let mut chat = ScriptedChat::answering(
        r#"{"study_design": "rct", "confidence": 0."#,
        Some("max_tokens"),
    );
    let assessment = {
        let mut classifier = StudyClassifier::new(&mut chat);
        classifier.classify(Some("A trial"), Some("An abstract"))
    };

    assert_eq!(
        assessment,
        bmlib::quality::data_models::QualityAssessment::unclassified()
    );
    assert_eq!(
        chat.source.calls, 3,
        "three attempts at temperature 0.1, then the truncation is reported"
    );
    assert_eq!(chat.source.backoffs, vec![1, 2], "1s then 2s");
}

/// `require_dict` is passed, and the shape it rejects is a **retry**, not an
/// immediate unclassified: at temperature 0 the same messages return the same
/// array, so the failure is immediate and the tier degrades.
#[test]
fn tier2_degrades_when_the_model_answers_an_array() {
    let mut chat = ScriptedChat::answering("[1, 2]", Some("stop"));
    let assessment = {
        let mut classifier = StudyClassifier::new(&mut chat).with_temperature(0.0);
        classifier.classify(Some("A trial"), Some("An abstract"))
    };

    assert_eq!(
        assessment,
        bmlib::quality::data_models::QualityAssessment::unclassified()
    );
    assert!(chat.calls[0].require_dict, "the shape was demanded");
    assert_eq!(chat.source.calls, 1, "greedy sampling repeats itself");
}

/// A model that answers `null` for a field — which the prompt permits — is read
/// as "not answered" rather than taking the paper down. That repair lives in
/// `llm_parsers` (defect #295) and the tier inherits it.
#[test]
fn tier2_keeps_a_conclusive_design_when_a_field_is_null() {
    let json = json!({"study_design": "rct", "confidence": null, "blinding": null}).to_string();
    let mut chat = ScriptedChat::answering(&json, Some("stop"));
    let assessment = {
        let mut classifier = StudyClassifier::new(&mut chat);
        classifier.classify(Some("A trial"), Some("An abstract"))
    };

    assert_eq!(assessment.study_design.member_name(), "RCT");
    assert_eq!(assessment.confidence, 0.5, "the default, not a crash");
    assert_eq!(assessment.is_blinded, None);
}

// ---------------------------------------------------------------------------
// Tier 3 — the deep assessment
// ---------------------------------------------------------------------------

fn tier3_json() -> String {
    json!({
        "study_design": "rct",
        "quality_score": 8,
        "evidence_level": "1b",
        "design_characteristics": {
            "randomized": true,
            "controlled": true,
            "blinded": "double",
            "prospective": true,
            "multicenter": false
        },
        "sample_size": 450,
        "bias_risk": {
            "selection": "low",
            "performance": "low",
            "detection": "unclear",
            "attrition": "low",
            "reporting": "high"
        },
        "strengths": ["randomised", "large sample"],
        "limitations": ["open-label follow-up"],
        "confidence": 0.85
    })
    .to_string()
}

/// The happy path: every field of the deep assessment is read, and the call
/// carries Tier 3's own defaults — temperature 0.2, which is the Python's.
#[test]
fn tier3_assesses_a_model_answer() {
    let mut chat = ScriptedChat::answering(&tier3_json(), Some("stop"));
    let assessment = {
        let mut agent = QualityAgent::new(&mut chat);
        agent.assess(Some("A trial"), Some("We randomised 450 adults."))
    };

    assert_eq!(assessment.assessment_tier, 3);
    assert_eq!(assessment.extraction_method, "llm_deep_assessment");
    assert_eq!(assessment.study_design.member_name(), "RCT");
    assert_eq!(assessment.quality_score, 8.0);
    assert_eq!(assessment.evidence_level.as_deref(), Some("1b"));
    assert_eq!(assessment.is_randomized, Some(true));
    assert_eq!(assessment.is_controlled, Some(true));
    assert_eq!(assessment.is_blinded.as_deref(), Some("double"));
    assert_eq!(assessment.is_prospective, Some(true));
    assert_eq!(assessment.is_multicenter, Some(false));
    assert_eq!(assessment.sample_size, Some(450));
    assert_eq!(assessment.confidence, 0.85);
    assert_eq!(assessment.strengths, vec!["randomised", "large sample"]);
    assert_eq!(assessment.limitations, vec!["open-label follow-up"]);
    assert_eq!(
        assessment.bias_risk,
        Some(BiasRisk {
            selection: "low".to_string(),
            performance: "low".to_string(),
            detection: "unclear".to_string(),
            attrition: "low".to_string(),
            reporting: "high".to_string(),
        })
    );
    assert_eq!(
        chat.calls,
        vec![Call {
            temperature: 0.2,
            max_tokens: 1024,
            max_retries: 3,
            require_dict: true,
        }]
    );
}

/// With both fields missing, Tier 3 makes no call either — left to itself the
/// model would return fully-formed strengths and limitations for a paper it was
/// told nothing about.
#[test]
fn tier3_makes_no_call_for_an_empty_paper() {
    let mut chat = ScriptedChat::answering(tier3_json().as_str(), Some("stop"));
    let assessment = {
        let mut agent = QualityAgent::new(&mut chat);
        agent.assess(Some("  "), None)
    };
    assert_eq!(chat.source.calls, 0);
    assert_eq!(assessment.assessment_tier, 0);
}

/// **A null section is "not answered", not a crash** — the #295 sites, reaching
/// the tier through `llm_parsers`. `is_randomized` stays `None` rather than
/// becoming a denial, which is what the quality filter reads.
#[test]
fn tier3_reads_null_sections_as_unanswered() {
    let json = json!({
        "study_design": "rct",
        "quality_score": 8,
        "design_characteristics": null,
        "bias_risk": null
    })
    .to_string();
    let mut chat = ScriptedChat::answering(&json, Some("stop"));
    let assessment = {
        let mut agent = QualityAgent::new(&mut chat);
        agent.assess(Some("A trial"), Some("An abstract"))
    };

    assert_eq!(assessment.study_design.member_name(), "RCT");
    assert_eq!(assessment.quality_score, 8.0);
    assert_eq!(assessment.is_randomized, None, "absent is not false");
    assert_eq!(assessment.bias_risk.expect("a record").selection, "unclear");
}

/// **A transport failure degrades here too, and it costs more than in Tier 2**:
/// the manager lets Tier 3 replace Tier 1, so a paper the metadata classified
/// conclusively comes back unclassified.
#[test]
fn tier3_degrades_on_a_transport_failure() {
    let mut chat = ScriptedChat::new(Script::new(vec![Err("timed out".to_string())]));
    let assessment = {
        let mut agent = QualityAgent::new(&mut chat);
        agent.assess(Some("A trial"), Some("An abstract"))
    };
    assert_eq!(
        assessment,
        bmlib::quality::data_models::QualityAssessment::unclassified()
    );
    assert_eq!(chat.source.calls, 1);
}

/// The Tier 3 prompt is formatted the same way, and the title and abstract go
/// into the same two placeholders.
#[test]
fn tier3_sends_the_formatted_prompt() {
    let mut chat = ScriptedChat::answering(&tier3_json(), Some("stop"));
    {
        let mut agent = QualityAgent::new(&mut chat);
        agent.assess(Some("A title"), Some("An abstract"));
    }
    let sent = chat.last_user_message();
    assert!(
        sent.starts_with("Assess this research paper's methodological quality:"),
        "{sent}"
    );
    assert!(sent.contains("Title: A title"), "{sent}");
    assert!(sent.contains("Abstract: An abstract"), "{sent}");
    assert!(
        sent.contains("Return JSON:\n{\n"),
        "braces collapsed: {sent}"
    );
}

// ---------------------------------------------------------------------------
// Tier 4 — the Cochrane assessor
// ---------------------------------------------------------------------------

fn cochrane_json() -> String {
    json!({
        "study_characteristics": {
            "methods": "Parallel randomised trial",
            "participants": {
                "setting": "Germany",
                "population": "adults with hypertension",
                "inclusion_criteria": ["adults"],
                "exclusion_criteria": ["pregnancy"],
                "total_participants": 45,
                "group_sizes": {"intervention": 25, "control": 20},
                "baseline_characteristics_reported": true
            },
            "interventions": {
                "description": "drug X",
                "intervention_groups": ["drug X"],
                "control_description": "placebo",
                "duration": "12 weeks"
            },
            "outcomes": {
                "description": "mortality",
                "primary_outcomes": ["all-cause mortality"],
                "secondary_outcomes": ["blood pressure"],
                "outcome_timepoints": ["6 months", "12 months"]
            },
            "notes": {
                "follow_up_periods": ["12 months"],
                "funding_source": "public",
                "conflicts_of_interest": "none declared",
                "ethical_approval": "approved",
                "trial_registration": "NCT00000001",
                "publication_status": "full publication",
                "additional_notes": ["protocol published"]
            }
        },
        "risk_of_bias": {
            "random_sequence_generation": {"judgement": "Low risk", "support_for_judgement": "computer-generated"},
            "allocation_concealment": {"judgement": "Unclear risk", "support_for_judgement": "not reported"},
            "baseline_outcome_measurements": {"judgement": "Low risk", "support_for_judgement": "similar at baseline"},
            "baseline_characteristics": {"judgement": "Low risk", "support_for_judgement": "balanced"},
            "blinding_participants_personnel": {"judgement": "High risk", "support_for_judgement": "open label"},
            "blinding_outcome_assessment_subjective": {"judgement": "High risk", "support_for_judgement": "open label"},
            "blinding_outcome_assessment_objective": {"judgement": "Low risk", "support_for_judgement": "mortality"},
            "incomplete_outcome_data": {"judgement": "Low risk", "support_for_judgement": "complete follow-up"},
            "selective_reporting": {"judgement": "Unclear risk", "support_for_judgement": "protocol missing"}
        },
        "overall_confidence": 0.7,
        "evidence_level": "Level 2 (moderate-high)",
        "assessment_notes": ["note 1"]
    })
    .to_string()
}

/// The happy path: nine domains, the characteristics table, and the identity
/// fields the caller supplies. The study label falls back through
/// `document {id}` because no `study_id` was given.
#[test]
fn tier4_assesses_a_model_answer() {
    let mut chat = ScriptedChat::answering(&cochrane_json(), Some("stop"));
    let assessment = {
        let mut assessor = CochraneAssessor::new(&mut chat);
        assessor.assess(
            Some("Drug X versus placebo"),
            Some("Full text."),
            AssessOptions {
                document_id: Some(7),
                pmid: Some("12345678"),
                doi: Some("10.1000/x"),
                ..AssessOptions::default()
            },
        )
    }
    .expect("an assessment");

    assert_eq!(assessment.study_id(), "Study 7");
    assert_eq!(assessment.document_id(), Some(7));
    assert_eq!(
        assessment.study_characteristics.document_title.as_deref(),
        Some("Drug X versus placebo")
    );
    assert_eq!(
        assessment.study_characteristics.pmid.as_deref(),
        Some("12345678")
    );
    assert_eq!(
        assessment.study_characteristics.doi.as_deref(),
        Some("10.1000/x")
    );
    assert_eq!(
        assessment.study_characteristics.methods,
        "Parallel randomised trial"
    );
    assert_eq!(
        assessment.study_characteristics.participants.setting,
        "Germany"
    );
    assert_eq!(
        assessment
            .study_characteristics
            .participants
            .total_participants,
        Some(45)
    );
    assert_eq!(assessment.overall_confidence, Some(0.7));
    assert_eq!(
        assessment.evidence_level.as_deref(),
        Some("Level 2 (moderate-high)")
    );
    assert_eq!(
        assessment.assessment_notes,
        Some(vec!["note 1".to_string()])
    );
    assert_eq!(assessment.condensed_from_chars, None);
    assert_eq!(assessment.condensation_status, None);

    let counts = assessment.risk_of_bias.summary_counts();
    assert_eq!(counts["Low risk"], 5);
    assert_eq!(counts["High risk"], 2);
    assert_eq!(counts["Unclear risk"], 2);
    assert_eq!(
        assessment
            .risk_of_bias
            .blinding_outcome_assessment_subjective
            .outcome_type
            .as_deref(),
        Some("subjective")
    );
    assert_eq!(
        assessment
            .risk_of_bias
            .random_sequence_generation
            .support_for_judgement,
        "computer-generated"
    );
}

/// `_as_dict`'s rule, which the `llm_parsers` module states as the general one
/// #295 fixes: a model that answers `null` or a bare string for a whole
/// **section** must not take the assessment down with it. The section's own
/// reader then supplies its "Not reported" defaults.
#[test]
fn tier4_reads_a_null_section_as_not_reported() {
    let mut value: Value = serde_json::from_str(&cochrane_json()).expect("valid JSON");
    value["study_characteristics"]["participants"] = Value::Null;
    value["study_characteristics"]["notes"] = json!("not a section");
    let mut chat = ScriptedChat::answering(&value.to_string(), Some("stop"));

    let assessment = {
        let mut assessor = CochraneAssessor::new(&mut chat);
        assessor.assess(Some("A trial"), Some("Text."), AssessOptions::default())
    }
    .expect("a null section is not a reason to give up");

    assert_eq!(
        assessment.study_characteristics.participants.setting,
        "Not reported"
    );
    assert_eq!(
        assessment.study_characteristics.participants.population,
        "Not reported"
    );
    assert_eq!(assessment.study_characteristics.notes.funding_source, None);
    // The rest of the reply still reads.
    assert_eq!(
        assessment.risk_of_bias.selective_reporting.judgement,
        "Unclear risk"
    );
}

/// **With both title and text missing there is no call** — left to itself the
/// model returns a fully-formed nine-domain judgement for a paper it was told
/// nothing about.
#[test]
fn tier4_makes_no_call_for_an_empty_study() {
    let mut chat = ScriptedChat::answering(&cochrane_json(), Some("stop"));
    let (assessment, stats) = {
        let mut assessor = CochraneAssessor::new(&mut chat);
        let assessment = assessor.assess(None, Some("   "), AssessOptions::default());
        (assessment, assessor.get_stats())
    };

    assert_eq!(assessment, None);
    assert_eq!(chat.source.calls, 0);
    assert_eq!(stats.total_assessments, 1);
    assert_eq!(stats.failed_assessments, 1);
    assert_eq!(stats.successful_assessments, 0);
}

/// **The assessor returns `None` when the model answers nothing usable**, and
/// the counters keep `successful + failed == total`. A reply that parses but
/// carries no `risk_of_bias` is retried as a whole — twice, not once — and then
/// reported as a parse failure.
#[test]
fn tier4_returns_none_when_the_model_omits_the_risk_of_bias() {
    let json = json!({"study_characteristics": {"methods": "RCT"}}).to_string();
    let mut chat = ScriptedChat::answering(&json, Some("stop"));

    let assessment = {
        let mut assessor = CochraneAssessor::new(&mut chat);
        let assessment = assessor.assess(Some("A trial"), Some("Text."), AssessOptions::default());
        let stats = assessor.get_stats();
        assert_eq!(stats.total_assessments, 1);
        assert_eq!(stats.failed_assessments, 1);
        assert_eq!(stats.successful_assessments, 0);
        assert_eq!(stats.parse_failures, 1);
        assert_eq!(stats.success_rate, 0.0);
        assessment
    };

    assert_eq!(assessment, None);
    assert_eq!(
        chat.source.calls, 2,
        "the outer bound is two whole-assessment attempts"
    );
}

/// A **transport failure** is one failed assessment, and not a parse failure:
/// the two diagnoses point at different things.
#[test]
fn tier4_degrades_on_a_transport_failure() {
    let mut chat = ScriptedChat::new(Script::new(vec![Err("connection refused".to_string())]));
    let mut assessor = CochraneAssessor::new(&mut chat);
    let assessment = assessor.assess(Some("A trial"), Some("Text."), AssessOptions::default());

    assert_eq!(assessment, None);
    let stats = assessor.get_stats();
    assert_eq!(stats.failed_assessments, 1);
    assert_eq!(stats.parse_failures, 0);
    assert_eq!(chat.source.calls, 1);
}

/// A **truncated** reply is a transport-shaped failure too, not a parse failure,
/// and it is the `chat_json` loop that retries it inside the one whole-assessment
/// attempt.
#[test]
fn tier4_degrades_on_a_truncated_response() {
    let mut chat = ScriptedChat::answering(
        r#"{"risk_of_bias": {"random_sequence_generation": {"judgement": "Low risk""#,
        Some("max_tokens"),
    );
    let (assessment, stats) = {
        let mut assessor = CochraneAssessor::new(&mut chat);
        let assessment = assessor.assess(Some("A trial"), Some("Text."), AssessOptions::default());
        (assessment, assessor.get_stats())
    };

    assert_eq!(assessment, None);
    assert_eq!(
        chat.source.calls, 3,
        "three attempts inside one whole assessment"
    );
    assert_eq!(stats.total_assessments, 1);
    assert_eq!(stats.failed_assessments, 1);
    assert_eq!(stats.parse_failures, 0, "truncation is not a parse failure");
}

/// `require_dict` is demanded here too, and a top-level array degrades.
#[test]
fn tier4_rejects_a_top_level_array() {
    let mut chat = ScriptedChat::answering("[1, 2]", Some("stop"));
    let mut assessor = CochraneAssessor::new(&mut chat).with_temperature(0.0);
    let assessment = assessor.assess(Some("A trial"), Some("Text."), AssessOptions::default());

    assert_eq!(assessment, None);
    assert!(chat.calls[0].require_dict);
    assert_eq!(chat.source.calls, 1);
}

/// **A confidence below the bar is rejected, and an unknown one is not.** The
/// same rule keeps the transparency module from reading an undetermined COI
/// disclosure as a missing one, and the distinction is the whole point: an
/// unknown confidence is not a low one.
#[test]
fn tier4_rejects_only_a_reported_confidence_below_the_bar() {
    // Reported and below the bar.
    let mut value: Value = serde_json::from_str(&cochrane_json()).expect("valid JSON");
    value["overall_confidence"] = json!(0.2);
    let mut chat = ScriptedChat::answering(&value.to_string(), Some("stop"));
    let assessment = {
        let mut assessor = CochraneAssessor::new(&mut chat);
        assessor.assess(
            Some("A trial"),
            Some("Text."),
            AssessOptions {
                min_confidence: 0.5,
                ..AssessOptions::default()
            },
        )
    };
    assert_eq!(assessment, None);

    // Unreported and below any bar at all: kept.
    let mut value: Value = serde_json::from_str(&cochrane_json()).expect("valid JSON");
    value["overall_confidence"] = Value::Null;
    let mut chat = ScriptedChat::answering(&value.to_string(), Some("stop"));
    let assessment = {
        let mut assessor = CochraneAssessor::new(&mut chat);
        assessor.assess(
            Some("A trial"),
            Some("Text."),
            AssessOptions {
                min_confidence: 0.99,
                ..AssessOptions::default()
            },
        )
    };
    assert!(
        assessment.is_some(),
        "an unknown confidence is not a low one"
    );
    assert_eq!(assessment.expect("kept").overall_confidence, None);
}

/// The confidence read is Python's `float()`, **booleans included**:
/// `float(True)` is `1.0`. Reproduced rather than tidied, because reading a
/// boolean as "not answered" would silently keep an assessment `min_confidence`
/// was set to reject.
#[test]
fn tier4_reads_a_boolean_confidence_as_python_does() {
    let mut value: Value = serde_json::from_str(&cochrane_json()).expect("valid JSON");
    value["overall_confidence"] = json!(true);
    let mut chat = ScriptedChat::answering(&value.to_string(), Some("stop"));
    let assessment = {
        let mut assessor = CochraneAssessor::new(&mut chat);
        assessor.assess(
            Some("A trial"),
            Some("Text."),
            AssessOptions {
                min_confidence: 0.5,
                ..AssessOptions::default()
            },
        )
    };
    assert_eq!(assessment.expect("kept").overall_confidence, Some(1.0));

    // And `false` is 0.0, which a 0.5 bar rejects.
    let mut value: Value = serde_json::from_str(&cochrane_json()).expect("valid JSON");
    value["overall_confidence"] = json!(false);
    let mut chat = ScriptedChat::answering(&value.to_string(), Some("stop"));
    let assessment = {
        let mut assessor = CochraneAssessor::new(&mut chat);
        assessor.assess(
            Some("A trial"),
            Some("Text."),
            AssessOptions {
                min_confidence: 0.5,
                ..AssessOptions::default()
            },
        )
    };
    assert_eq!(assessment, None);
}

/// A judge is normalised through `from_string`, so a model answering `"low"`
/// rather than `"Low risk"` is still counted — the defect upstream had, where
/// `get_summary_counts` skipped the domain and silently reported eight of nine.
#[test]
fn tier4_normalises_a_judgement_spelling() {
    let mut value: Value = serde_json::from_str(&cochrane_json()).expect("valid JSON");
    value["risk_of_bias"]["random_sequence_generation"]["judgement"] = json!("low");
    value["risk_of_bias"]["allocation_concealment"]["judgement"] = json!("HIGH");
    let mut chat = ScriptedChat::answering(&value.to_string(), Some("stop"));
    let assessment = {
        let mut assessor = CochraneAssessor::new(&mut chat);
        assessor
            .assess(Some("A trial"), Some("Text."), AssessOptions::default())
            .expect("an assessment")
    };

    assert_eq!(
        assessment.risk_of_bias.random_sequence_generation.judgement,
        "Low risk"
    );
    assert_eq!(
        assessment.risk_of_bias.allocation_concealment.judgement,
        "High risk"
    );
    let counts = assessment.risk_of_bias.summary_counts();
    assert_eq!(
        counts.values().sum::<usize>(),
        9,
        "every domain is counted, none skipped"
    );
}

/// The batch helper is a loop over `assess`: a study that could not be assessed
/// is absent from the result and counted as a failure.
#[test]
fn tier4_batch_keeps_the_studies_that_succeeded() {
    let good = cochrane_json();
    let mut chat = ScriptedChat::new(Script::new(vec![
        Ok(response(&good, Some("stop"))),
        Ok(response("{}", Some("stop"))),
    ]));
    let mut seen_progress: Vec<(usize, usize, String)> = Vec::new();
    let mut callback = |current: usize, total: usize, title: &str| {
        seen_progress.push((current, total, title.to_string()));
    };

    let studies = vec![
        StudyInput {
            title: "Good study".to_string(),
            text: Some("Text.".to_string()),
            ..StudyInput::default()
        },
        StudyInput {
            title: "Hopeless study".to_string(),
            text: Some("Text.".to_string()),
            ..StudyInput::default()
        },
    ];
    let assessments = {
        let mut assessor = CochraneAssessor::new(&mut chat);
        let assessments = assessor.assess_batch(&studies, 0.0, Some(&mut callback));
        let stats = assessor.get_stats();
        assert_eq!(stats.total_assessments, 2);
        assert_eq!(stats.successful_assessments, 1);
        assert_eq!(stats.failed_assessments, 1);
        assessments
    };

    assert_eq!(assessments.len(), 1);
    assert_eq!(assessments[0].study_id(), "Good study");
    assert_eq!(
        seen_progress,
        vec![
            (1, 2, "Good study".to_string()),
            (2, 2, "Hopeless study".to_string()),
        ],
        "the callback names each study before it is attempted"
    );
}

/// The study label's fallback chain, in order: an explicit `study_id`, then
/// `Study {document_id}`, then the title, then `"Unknown study"` — no surname is
/// guessed from an author list.
#[test]
fn tier4_resolves_the_study_label_in_order() {
    let cases = [
        (Some("Andrei 2011"), Some(7), "Andrei 2011"),
        (None, Some(7), "Study 7"),
        (None, None, "A trial"),
    ];
    for (study_id, document_id, expected) in cases {
        let mut chat = ScriptedChat::answering(&cochrane_json(), Some("stop"));
        let assessment = {
            let mut assessor = CochraneAssessor::new(&mut chat);
            assessor
                .assess(
                    Some("A trial"),
                    Some("Text."),
                    AssessOptions {
                        study_id,
                        document_id,
                        ..AssessOptions::default()
                    },
                )
                .expect("an assessment")
        };
        assert_eq!(
            assessment.study_id(),
            expected,
            "{study_id:?}/{document_id:?}"
        );
    }
}

// ---------------------------------------------------------------------------
// Tier 4 — condensation
// ---------------------------------------------------------------------------

/// A scripted condenser: whatever the test wants the map-reduce to have done.
struct StubCondenser {
    result: ProcessingResult,
    seen: Option<(usize, usize)>,
}

impl StubCondenser {
    fn new(content: &str, status: ProcessingStatus) -> Self {
        StubCondenser {
            result: processing_result(content, status),
            seen: None,
        }
    }
}

impl Condenser for StubCondenser {
    fn condense(
        &mut self,
        text: &str,
        _label: &str,
        config: &ProcessingConfig,
    ) -> ProcessingResult {
        self.seen = Some((text.chars().count(), config.max_context_chars));
        self.result.clone()
    }
}

fn processing_result(content: &str, status: ProcessingStatus) -> ProcessingResult {
    ProcessingResult {
        final_result: ExtractionResult::new(content),
        status,
        total_items_processed: 1,
        batches_created: 1,
        recursion_levels_used: 0,
        intermediate_results: None,
        error_message: None,
        processing_stats: BTreeMap::new(),
        failed_batches: Vec::new(),
        skipped_items: Vec::new(),
        successful_batches: 1,
    }
}

/// Oversized text goes through the condenser, the digest is judged, and the
/// result says so through both `condensed_from_chars` and `condensation_status`.
#[test]
fn tier4_condenses_oversized_text_and_records_it() {
    let mut condenser = StubCondenser::new("A digest of the paper.", ProcessingStatus::Completed);
    let mut chat = ScriptedChat::answering(&cochrane_json(), Some("stop"));

    let text = "x".repeat(50_000);
    let assessment = {
        let mut assessor = CochraneAssessor::new(&mut chat).with_condenser(&mut condenser);
        assessor
            .assess(Some("A trial"), Some(&text), AssessOptions::default())
            .expect("an assessment")
    };

    assert_eq!(assessment.condensed_from_chars, Some(50_000));
    assert_eq!(assessment.condensation_status.as_deref(), Some("completed"));
    assert_eq!(
        assessment.assessment_notes,
        Some(vec!["note 1".to_string()])
    );
    assert_eq!(
        condenser.seen,
        Some((50_000, 48_000)),
        "the condenser sees the whole text and the assessor's own budget"
    );
    // The digest, not the 50k characters, is what reached the model.
    let sent = chat.last_user_message();
    assert!(
        sent.contains("A digest of the paper."),
        "the digest was sent"
    );
    assert!(!sent.contains(&text), "the raw text was not");
}

/// A run that did not finish cleanly attaches a note saying so — a "partial"
/// condensation means whole sections of the paper are absent from the digest the
/// nine-domain judgement ran over, which the model's own confidence cannot know.
#[test]
fn tier4_notes_a_condensation_that_did_not_complete() {
    let mut condenser = StubCondenser::new("A partial digest.", ProcessingStatus::Partial);
    let mut chat = ScriptedChat::answering(&cochrane_json(), Some("stop"));
    let text = "x".repeat(50_000);

    let assessment = {
        let mut assessor = CochraneAssessor::new(&mut chat).with_condenser(&mut condenser);
        assessor
            .assess(Some("A trial"), Some(&text), AssessOptions::default())
            .expect("an assessment")
    };

    assert_eq!(assessment.condensation_status.as_deref(), Some("partial"));
    let notes = assessment.assessment_notes.expect("notes");
    assert_eq!(notes.len(), 2);
    assert!(
        notes[0].contains("finished with status partial"),
        "{notes:?}"
    );
    assert_eq!(notes[1], "note 1", "the model's own note follows");
}

/// **The four refusals.** A failed run, an empty digest and a digest that still
/// does not fit the budget are all refused, and an oversized paper with no
/// condenser configured is refused rather than sent whole — truncation is not an
/// option here, because allocation concealment lives in Methods and attrition in
/// Results.
#[test]
fn tier4_refuses_a_condensation_it_cannot_judge() {
    let oversized = "x".repeat(50_000);
    let tight = ProcessingConfig::default().with_max_context_chars(48_000);

    // A failed run.
    let mut condenser = StubCondenser::new("anything", ProcessingStatus::Failed);
    let mut chat = ScriptedChat::answering(&cochrane_json(), Some("stop"));
    {
        let mut assessor = CochraneAssessor::new(&mut chat).with_condenser(&mut condenser);
        assert_eq!(
            assessor.assess(Some("A trial"), Some(&oversized), AssessOptions::default()),
            None
        );
    }
    assert_eq!(chat.source.calls, 0, "nothing was judged");

    // An empty digest.
    let mut condenser = StubCondenser::new("   ", ProcessingStatus::Completed);
    let mut chat = ScriptedChat::answering(&cochrane_json(), Some("stop"));
    {
        let mut assessor = CochraneAssessor::new(&mut chat).with_condenser(&mut condenser);
        assert_eq!(
            assessor.assess(Some("A trial"), Some(&oversized), AssessOptions::default()),
            None
        );
    }
    assert_eq!(chat.source.calls, 0);

    // A digest that still exceeds the budget: `truncated` names the recursion
    // ceiling, not the content size, so the status is not trusted.
    let mut condenser = StubCondenser::new(&"y".repeat(48_001), ProcessingStatus::Completed);
    let mut chat = ScriptedChat::answering(&cochrane_json(), Some("stop"));
    {
        let mut assessor = CochraneAssessor::new(&mut chat)
            .with_condenser(&mut condenser)
            .with_condense_config(tight);
        assert_eq!(
            assessor.assess(Some("A trial"), Some(&oversized), AssessOptions::default()),
            None
        );
    }
    assert_eq!(chat.source.calls, 0);

    // No condenser at all: refused rather than sent whole.
    let mut chat = ScriptedChat::answering(&cochrane_json(), Some("stop"));
    {
        let mut assessor = CochraneAssessor::new(&mut chat);
        assert_eq!(
            assessor.assess(Some("A trial"), Some(&oversized), AssessOptions::default()),
            None
        );
        assert_eq!(assessor.get_stats().failed_assessments, 1);
    }
    assert_eq!(chat.source.calls, 0);
}

/// The digest is measured **after** stripping, and a digest exactly at the
/// budget is judged: the rule is `> max_context_chars`, not `>=`.
#[test]
fn tier4_accepts_a_digest_exactly_at_the_budget() {
    let mut condenser = StubCondenser::new(&"y".repeat(48_000), ProcessingStatus::Completed);
    let mut chat = ScriptedChat::answering(&cochrane_json(), Some("stop"));
    let oversized = "x".repeat(50_000);
    let assessment = {
        let mut assessor = CochraneAssessor::new(&mut chat).with_condenser(&mut condenser);
        assessor.assess(Some("A trial"), Some(&oversized), AssessOptions::default())
    };
    assert!(assessment.is_some(), "48000 is not more than 48000");
}

/// Text **at** the threshold is not condensed; the rule is strictly greater
/// than, and a paper that fits must reach the model whole.
#[test]
fn tier4_does_not_condense_text_at_the_threshold() {
    let mut chat = ScriptedChat::answering(&cochrane_json(), Some("stop"));
    let text = "x".repeat(48_000);
    let assessment = {
        let mut assessor = CochraneAssessor::new(&mut chat);
        assessor.assess(Some("A trial"), Some(&text), AssessOptions::default())
    }
    .expect("an assessment");

    assert_eq!(chat.source.calls, 1);
    assert_eq!(assessment.condensed_from_chars, None);
    assert_eq!(assessment.condensation_status, None);
}

// ---------------------------------------------------------------------------
// The standalone readers
// ---------------------------------------------------------------------------

/// `parse_cochrane_assessment` is a pure function of the parsed JSON, so a reply
/// with a null or wrong-typed section is read without a model.
#[test]
fn the_cochrane_reader_reads_a_reply_on_its_own() {
    let value: Value = serde_json::from_str(&cochrane_json()).expect("valid JSON");
    let assessment = parse_cochrane_assessment(&value, &[], None, None).expect("an assessment");
    assert_eq!(
        assessment.study_id(),
        "",
        "identity is the caller's to fill"
    );
    assert_eq!(
        assessment.risk_of_bias.selective_reporting.judgement,
        "Unclear risk"
    );

    // A missing or empty section is the one refusal, because nine fabricated
    // "Unclear risk" defaults would be indistinguishable from a real assessment.
    for payload in [
        json!({}),
        json!({"risk_of_bias": null}),
        json!({"risk_of_bias": {}}),
    ] {
        assert!(
            parse_cochrane_assessment(&payload, &[], None, None).is_err(),
            "{payload}"
        );
    }
}

/// The condensation prompts keep their Python placeholder names, and rendering
/// them substitutes both.
#[test]
fn the_condensation_prompts_render_their_placeholders() {
    let extraction = render_condense_extraction("the query text", "the paper section");
    assert!(
        extraction.starts_with("Extract, verbatim where possible"),
        "{extraction}"
    );
    assert!(
        extraction.contains("Needed: the query text"),
        "{extraction}"
    );
    assert!(
        extraction.contains("Paper section:\nthe paper section"),
        "{extraction}"
    );
    assert!(!extraction.contains("{query}"), "{extraction}");

    let consolidation = render_condense_consolidation("the query text", "the evidence");
    assert!(
        consolidation.starts_with("Merge these extracted passages"),
        "{consolidation}"
    );
    assert!(
        consolidation.contains("Needed: the query text"),
        "{consolidation}"
    );
    assert!(
        consolidation.contains("Extracted evidence:\nthe evidence"),
        "{consolidation}"
    );
    assert!(!consolidation.contains("{content}"), "{consolidation}");
}

/// `DEFAULT_MAX_CONTEXT_CHARS` is the context processor's own default — 4,000 —
/// which is exactly why the assessor overrides it: it would condense almost
/// every full text and most long abstracts.
#[test]
fn the_condense_threshold_is_not_the_harness_default() {
    assert_eq!(DEFAULT_MAX_CONTEXT_CHARS, 4000);
    assert_eq!(bmlib::quality::DEFAULT_CONDENSE_THRESHOLD_CHARS, 48_000);
}

// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// The production adapter, over a real LlmClient
// ---------------------------------------------------------------------------

/// An HTTP transport that records what it was posted and answers with a canned
/// response. The only fake in this file that reaches `LlmClient`.
#[derive(Default)]
struct RecordingHttp {
    posted: std::sync::Mutex<Vec<Value>>,
    response: std::sync::Mutex<Option<(u16, String)>>,
}

impl RecordingHttp {
    fn answering(body: Value) -> std::sync::Arc<Self> {
        let http = RecordingHttp::default();
        *http.response.lock().expect("lock") = Some((200, body.to_string()));
        std::sync::Arc::new(http)
    }

    fn failing(status: u16) -> std::sync::Arc<Self> {
        let http = RecordingHttp::default();
        *http.response.lock().expect("lock") =
            Some((status, "{\"error\": {\"message\": \"boom\"}}".to_string()));
        std::sync::Arc::new(http)
    }

    fn last_body(&self) -> Value {
        self.posted
            .lock()
            .expect("lock")
            .last()
            .cloned()
            .expect("a posted request")
    }
}

impl HttpClient for RecordingHttp {
    fn get(&self, _url: &str) -> Result<HttpResponse, FetchError> {
        Err(FetchError::Transport(
            "this fake only serves POST".to_string(),
        ))
    }

    fn post_json(
        &self,
        _url: &str,
        body: &Value,
        _headers: &std::collections::BTreeMap<String, String>,
    ) -> Result<HttpResponse, FetchError> {
        self.posted.lock().expect("lock").push(body.clone());
        let (status, text) = self
            .response
            .lock()
            .expect("lock")
            .clone()
            .unwrap_or((500, String::new()));
        Ok(HttpResponse::from_bytes(status, text.into_bytes()))
    }
}

fn llm_client(transport: std::sync::Arc<RecordingHttp>) -> LlmClient {
    let mut client = LlmClient::new(transport);
    // Explicit rather than from the environment: a test that reads a developer's
    // real key is a test whose result depends on their shell.
    client.api_key = Some("k".to_string());
    client
}

/// **The production adapter builds the request the Python's `BaseAgent.chat`
/// builds**, and a real `LlmClient` round-trips a scripted HTTP answer into a
/// Tier 2 assessment. This is the one path in the file that is not a test
/// double: `LlmChat` over `LlmClient` over a fake transport.
#[test]
fn llm_chat_sends_the_tiers_request_through_a_real_client() {
    let transport = RecordingHttp::answering(json!({
        "choices": [{
            "message": {"content": "{\"study_design\": \"rct\", \"confidence\": 0.9}"},
            "finish_reason": "stop"
        }]
    }));
    let client = llm_client(transport.clone());
    let mut chat = bmlib::quality::agent_chat::LlmChat::new(&client, "openai:gpt-4o");

    let assessment = {
        let mut classifier = StudyClassifier::new(&mut chat);
        classifier.classify(Some("A trial"), Some("An abstract"))
    };
    assert_eq!(assessment.study_design.member_name(), "RCT");
    assert_eq!(assessment.confidence, 0.9);

    let body = transport.last_body();
    // The provider prefix routes the request; the body carries the bare model.
    assert_eq!(body["model"], json!("gpt-4o"));
    assert_eq!(body["temperature"], json!(0.1), "the tier's own default");
    assert_eq!(body["max_tokens"], json!(1024));
    assert_eq!(
        body["response_format"],
        json!({"type": "json_object"}),
        "chat_json always asks for a JSON object"
    );
    let system = body["messages"][0]["content"].as_str().unwrap_or_default();
    assert!(
        system.starts_with("You are a biomedical study design classifier"),
        "{system}"
    );
    let user = body["messages"][1]["content"].as_str().unwrap_or_default();
    assert!(user.contains("Title: A trial"), "{user}");
}

/// A failing request through the same adapter degrades the paper rather than
/// raising — the `except Exception` at the far end of the real transport.
#[test]
fn llm_chat_degrades_on_a_failed_request() {
    let transport = RecordingHttp::failing(500);
    let client = llm_client(transport);
    let mut chat = bmlib::quality::agent_chat::LlmChat::new(&client, "openai:gpt-4o");

    let assessment = {
        let mut classifier = StudyClassifier::new(&mut chat);
        classifier.classify(Some("A trial"), Some("An abstract"))
    };
    assert_eq!(
        assessment,
        bmlib::quality::data_models::QualityAssessment::unclassified()
    );
}

// ---------------------------------------------------------------------------
// The differential oracle
// ---------------------------------------------------------------------------

const ORACLE_CASES: &str = include_str!("data/cochrane_assessor_cases.json");
const ORACLE_EXPECTED: &str = include_str!("data/cochrane_assessor_expected.json");

/// Run one oracle case, as `dump_cochrane_assessor.py` runs its `fn`.
///
/// A refusal is an `Err`, so a case the Python recorded as `ok: false` can be
/// compared against the port's own refusal rather than against a value.
fn run_oracle_case(case: &Value) -> Result<Value, String> {
    let args = &case["args"];
    match case["fn"].as_str().unwrap_or_default() {
        "cochrane_parse" => {
            let notes: Vec<String> = args
                .get("notes")
                .and_then(Value::as_array)
                .map(|items| {
                    items
                        .iter()
                        .filter_map(Value::as_str)
                        .map(str::to_string)
                        .collect()
                })
                .unwrap_or_default();
            parse_cochrane_assessment(
                &args["data"],
                &notes,
                args.get("condensed_from").and_then(Value::as_i64),
                args.get("condensation_status").and_then(Value::as_str),
            )
            .map(|assessment| assessment.to_json())
        }
        "render_tier2" => Ok(json!(format_template(
            bmlib::quality::study_classifier::CLASSIFIER_USER_TEMPLATE,
            &[
                ("title", args["title"].as_str().unwrap_or_default()),
                ("abstract", args["abstract"].as_str().unwrap_or_default()),
            ],
        ))),
        "render_tier3" => Ok(json!(format_template(
            bmlib::quality::quality_agent::ASSESSMENT_USER_TEMPLATE,
            &[
                ("title", args["title"].as_str().unwrap_or_default()),
                ("abstract", args["abstract"].as_str().unwrap_or_default()),
            ],
        ))),
        "render_condense_extraction" => Ok(json!(render_condense_extraction(
            args["query"].as_str().unwrap_or_default(),
            args["content"].as_str().unwrap_or_default(),
        ))),
        "render_condense_consolidation" => Ok(json!(render_condense_consolidation(
            args["query"].as_str().unwrap_or_default(),
            args["content"].as_str().unwrap_or_default(),
        ))),
        other => Err(format!("unknown fn {other:?}")),
    }
}

/// **The port agrees with Python on every case**, over the Cochrane reply
/// reader and the three rendered prompts.
///
/// The corpus is generated by `rust/oracle/dump_cochrane_assessor.py`, which
/// calls `CochraneAssessor._parse_assessment` on a bare object — a pure function
/// of the parsed JSON — and renders the prompts with `str.format`. What is
/// diffed is the reading rule and the rendering, and nothing else.
///
/// A case may carry a `corrected` block: the places where this port narrows a
/// field to its annotated type (a findings list keeps only strings,
/// `evidence_level` is read only as a string, an object written through `str()`
/// is approximated by its JSON text) rather than stringifying whatever arrived.
/// The corpus holds the Python's value for those too, so the divergence is
/// recorded rather than hidden.
#[test]
fn the_port_agrees_with_python_on_every_oracle_case() {
    let cases: Value = serde_json::from_str(ORACLE_CASES).expect("cases parse");
    let expected: Value = serde_json::from_str(ORACLE_EXPECTED).expect("expected parse");
    let cases = cases.as_array().expect("cases is a list");
    let expected = expected.as_array().expect("expected is a list");
    assert_eq!(cases.len(), expected.len(), "regenerate the expectations");

    let mut failures: Vec<String> = Vec::new();
    let mut corrected_seen = 0usize;
    for (case, want) in cases.iter().zip(expected.iter()) {
        let name = case["name"].as_str().unwrap_or_default();
        assert_eq!(name, want["name"].as_str().unwrap_or_default());

        let got = run_oracle_case(case);
        let expected_value = match case.get("corrected") {
            Some(corrected) => {
                corrected_seen += 1;
                assert!(got.is_ok(), "{name}: the port must read this one: {got:?}");
                corrected
            }
            None if !want["ok"].as_bool().unwrap_or(false) => {
                if got.is_ok() {
                    failures.push(format!(
                        "  {name}: Python refused ({}), the port did not",
                        want["error"]
                    ));
                }
                continue;
            }
            None => &want["value"],
        };

        match got {
            Ok(value) => {
                if &value != expected_value {
                    failures.push(format!(
                        "  {name}\n    expected: {}\n    rust:     {}",
                        serde_json::to_string(expected_value).unwrap_or_default(),
                        serde_json::to_string(&value).unwrap_or_default()
                    ));
                }
            }
            Err(error) => failures.push(format!("  {name}: the port refused ({error})")),
        }
    }

    assert!(
        failures.is_empty(),
        "{} of {} diverge:\n{}",
        failures.len(),
        cases.len(),
        failures.join("\n")
    );
    assert!(
        corrected_seen > 0,
        "the corpus is meant to record at least one narrowing"
    );
}
