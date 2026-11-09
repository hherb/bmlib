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

//! The unattended evidence-weight pipeline: paper in, number out, nobody confirming.
//!
//! A port of `bmlib/quality/evidence_weight.py`. [`assess_unattended`] chains
//! the earlier pieces: design proposal, then (not an RCT, or not a
//! parallel-group one: stop) primary outcomes, then (none: stop) RoB 2 over
//! **all** of them, then the evidence ladder per outcome, then the paper's
//! weight. Machine proposals are accepted as they come; that is what
//! "unattended" means. A reader who confirms each step uses the pieces directly.
//!
//! A failure is never a score. An outcome whose risk of bias was not assessed,
//! whose result was refused, or whose result cannot be laddered has `ladder`
//! `None` and the reason in `ladder_error`; the paper's weight is the largest
//! of the ladders that were built (`None` when there are none).

use std::fmt;
use std::sync::atomic::AtomicBool;

use serde_json::{json, Value};

use crate::quality::agent_chat::JsonChat;
use crate::quality::design_agent::{DesignAgent, DesignProposal, ProposalError, QueryCondenser};
use crate::quality::evidence_ladder::{
    build_ladder, paper_weight, start_level, EvidenceLadder, LadderSettings,
};
use crate::quality::outcome_agent::{OutcomeAgent, OutcomeProposal, ProposedOutcome};
use crate::quality::rob2_agent::{
    list, object, text, DomainResult, OutcomeRob2, Rob2Agent, Rob2Run, Rob2RunError,
};

/// Wire `status` of a paper whose design the RoB 2 core questions do not cover.
const STATUS_NOT_COVERED: &str = "not_covered";
/// Wire `status` of a covered trial in which the model found no primary outcome.
const STATUS_NO_PRIMARY_OUTCOME: &str = "no_primary_outcome";
/// Wire `status` of a trial taken all the way through.
const STATUS_ASSESSED: &str = "assessed";

/// Why the pipeline ended without a result: the step's own error.
#[derive(Debug, Clone, PartialEq)]
pub enum EvidenceWeightError {
    /// The design or outcome proposal could not be made.
    Proposal(ProposalError),
    /// The RoB 2 run was refused or stopped.
    Rob2(Rob2RunError),
}

impl fmt::Display for EvidenceWeightError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            EvidenceWeightError::Proposal(e) => e.fmt(f),
            EvidenceWeightError::Rob2(e) => e.fmt(f),
        }
    }
}

impl std::error::Error for EvidenceWeightError {}

impl From<ProposalError> for EvidenceWeightError {
    fn from(error: ProposalError) -> Self {
        EvidenceWeightError::Proposal(error)
    }
}

impl From<Rob2RunError> for EvidenceWeightError {
    fn from(error: Rob2RunError) -> Self {
        EvidenceWeightError::Rob2(error)
    }
}

/// One primary outcome taken through RoB 2 and the ladder.
#[derive(Debug, Clone, PartialEq)]
pub struct AssessedOutcome {
    /// The proposed outcome.
    pub outcome: ProposedOutcome,
    /// Its RoB 2 result.
    pub rob2: OutcomeRob2,
    /// Its ladder; `None` when `rob2.overall` is `None` or the result was
    /// refused (then see `ladder_error`).
    pub ladder: Option<EvidenceLadder>,
    /// Why there is no ladder; `None` when there is one.
    pub ladder_error: Option<String>,
}

impl AssessedOutcome {
    /// Python's `to_dict()`.
    #[must_use]
    pub fn to_json(&self) -> Value {
        json!({
            "outcome": self.outcome.to_json(),
            "rob2": self.rob2.to_json(),
            "ladder": self.ladder,
            "ladder_error": self.ladder_error,
        })
    }

    /// Python's `from_dict()`.
    ///
    /// # Errors
    ///
    /// A message naming the field that is absent, of the wrong type or value,
    /// or saying a ladder and a ladder error are both given or both absent.
    pub fn from_json(value: &Value) -> Result<Self, String> {
        let data = object(value, "the assessed outcome")?;
        let ladder = match data.get("ladder") {
            None | Some(Value::Null) => None,
            Some(raw) => Some(
                serde_json::from_value::<EvidenceLadder>(raw.clone())
                    .map_err(|e| format!("ladder: {e}"))?,
            ),
        };
        let ladder_error = match data.get("ladder_error") {
            None | Some(Value::Null) => None,
            Some(Value::String(error)) => Some(error.clone()),
            Some(_) => return Err("ladder_error is not text".to_string()),
        };
        if ladder.is_some() == ladder_error.is_some() {
            return Err("exactly one of ladder and ladder_error is set".to_string());
        }
        Ok(AssessedOutcome {
            outcome: ProposedOutcome::from_json(data.get("outcome").unwrap_or(&Value::Null))?,
            rob2: OutcomeRob2::from_json(data.get("rob2").unwrap_or(&Value::Null))?,
            ladder,
            ladder_error,
        })
    }
}

/// What [`assess_unattended`] returns.
#[derive(Debug, Clone, PartialEq)]
pub enum EvidenceWeight {
    /// The paper is not a parallel-group randomised trial; nothing more was asked.
    NotCovered {
        /// The design proposal that said so.
        design: DesignProposal,
    },
    /// A covered trial in which no primary outcome was found; RoB 2 was not asked.
    NoPrimaryOutcome {
        /// The design proposal.
        design: DesignProposal,
        /// The (empty) outcome proposal, which may still say it was condensed.
        outcomes: OutcomeProposal,
    },
    /// A covered trial taken through every step.
    Assessed {
        /// The design proposal.
        design: DesignProposal,
        /// The outcome proposal (never empty).
        outcomes: OutcomeProposal,
        /// The RoB 2 run over all its outcomes.
        run: Rob2Run,
        /// One entry per outcome, in order.
        assessed: Vec<AssessedOutcome>,
        /// The largest ladder weight; `None` when no ladder was built.
        paper_weight: Option<f64>,
        /// Whether the model saw a digest in any step.
        condensed: bool,
    },
}

impl EvidenceWeight {
    /// Python's `to_dict()` of any variant, tagged by `status`.
    #[must_use]
    pub fn to_json(&self) -> Value {
        match self {
            EvidenceWeight::NotCovered { design } => {
                json!({"status": STATUS_NOT_COVERED, "design": design.to_json()})
            }
            EvidenceWeight::NoPrimaryOutcome { design, outcomes } => json!({
                "status": STATUS_NO_PRIMARY_OUTCOME,
                "design": design.to_json(),
                "outcomes": outcomes.to_json(),
            }),
            EvidenceWeight::Assessed {
                design,
                outcomes,
                run,
                assessed,
                paper_weight,
                condensed,
            } => json!({
                "status": STATUS_ASSESSED,
                "design": design.to_json(),
                "outcomes": outcomes.to_json(),
                "run": run.to_json(),
                "assessed": assessed.iter().map(AssessedOutcome::to_json).collect::<Vec<_>>(),
                "paper_weight": paper_weight,
                "condensed": condensed,
            }),
        }
    }

    /// Python's `evidence_weight_from_dict()`, by `status`.
    ///
    /// # Errors
    ///
    /// A message when `status` is unknown, a field is absent or of the wrong
    /// type or value, or the parts disagree (a different number of outcomes,
    /// RoB 2 results or assessed outcomes; a `paper_weight` that is not the
    /// largest ladder weight).
    pub fn from_json(value: &Value) -> Result<Self, String> {
        let data = object(value, "the evidence weight")?;
        let status = text(data, "status")?;
        let part = |key: &str| data.get(key).unwrap_or(&Value::Null);
        let design = DesignProposal::from_json(part("design"))?;
        if status == STATUS_NOT_COVERED {
            return Ok(EvidenceWeight::NotCovered { design });
        }
        let outcomes = OutcomeProposal::from_json(part("outcomes"))?;
        if status == STATUS_NO_PRIMARY_OUTCOME {
            return Ok(EvidenceWeight::NoPrimaryOutcome { design, outcomes });
        }
        if status != STATUS_ASSESSED {
            return Err(format!(
                "status \"{status}\" is not {STATUS_NOT_COVERED}, {STATUS_NO_PRIMARY_OUTCOME} or {STATUS_ASSESSED}"
            ));
        }
        let run = Rob2Run::from_json(part("run"))?;
        let assessed = list(data, "assessed")?
            .iter()
            .map(AssessedOutcome::from_json)
            .collect::<Result<Vec<_>, _>>()?;
        if assessed.len() != run.outcomes.len() || assessed.len() != outcomes.outcomes.len() {
            return Err("outcomes, run and assessed do not have the same length".to_string());
        }
        for (index, ((one, proposed), rob2)) in assessed
            .iter()
            .zip(&outcomes.outcomes)
            .zip(&run.outcomes)
            .enumerate()
        {
            if one.outcome != *proposed || one.rob2 != *rob2 {
                return Err(format!(
                    "assessed[{index}] is not outcome {index} of outcomes and run"
                ));
            }
        }
        let expected = weight_of(&assessed);
        let given = match data.get("paper_weight") {
            None | Some(Value::Null) => None,
            Some(number) => Some(number.as_f64().ok_or("paper_weight is not a number")?),
        };
        if given != expected {
            return Err(format!(
                "paper_weight {given:?} is not the largest ladder weight {expected:?}"
            ));
        }
        Ok(EvidenceWeight::Assessed {
            design,
            outcomes,
            run,
            assessed,
            paper_weight: expected,
            condensed: data
                .get("condensed")
                .and_then(Value::as_bool)
                .ok_or("condensed is not a boolean")?,
        })
    }
}

/// The largest weight of the ladders built.
fn weight_of(assessed: &[AssessedOutcome]) -> Option<f64> {
    let ladders: Vec<EvidenceLadder> = assessed.iter().filter_map(|a| a.ladder.clone()).collect();
    paper_weight(&ladders)
}

/// The ladder error of an outcome whose overall judgement is `None`, naming why if known.
fn not_assessed(study: &DomainResult, rob2: &OutcomeRob2) -> String {
    let mut failed: Vec<String> = std::iter::once(study)
        .chain(&rob2.domains)
        .filter_map(|d| match d {
            DomainResult::Failed { domain, reason } => {
                Some(format!("{domain:?} failed ({reason})"))
            }
            DomainResult::Assessed(_) => None,
        })
        .collect();
    if let Some(error) = &rob2.escalation_error {
        failed.push(format!("the escalation question failed ({error})"));
    }
    let base = "the outcome's risk of bias was not assessed";
    if failed.is_empty() {
        base.to_string()
    } else {
        format!("{base}: {}", failed.join("; "))
    }
}

/// Why no ladder can be built for this outcome, or `None` when one can.
fn ladder_error(
    study: &DomainResult,
    outcome: &ProposedOutcome,
    rob2: &OutcomeRob2,
) -> Option<String> {
    if rob2.overall.is_none() {
        return Some(not_assessed(study, rob2));
    }
    outcome
        .result_error
        .as_ref()
        .map(|error| format!("the reported result was refused: {error}"))
}

/// Whether the model saw a digest in the design, outcome or any RoB 2 step.
fn any_condensed(design: &DesignProposal, outcomes: &OutcomeProposal, run: &Rob2Run) -> bool {
    design.condensed
        || outcomes.condensed
        || std::iter::once(&run.study)
            .chain(run.outcomes.iter().flat_map(|o| &o.domains))
            .any(|d| matches!(d, DomainResult::Assessed(a) if a.condensed))
}

/// Take one paper from full text to its evidence weight, with nobody confirming.
///
/// Every step uses `chat`, the optional `condenser` and `stop`; `text`'s
/// citation offsets are code points into it exactly as passed.
///
/// # Errors
///
/// [`EvidenceWeightError`] when a step could not be made at all (empty text,
/// bad `min_similarity`, stop, an unreadable proposal), wrapping its own error.
pub fn assess_unattended(
    chat: &mut dyn JsonChat,
    mut condenser: Option<&mut dyn QueryCondenser>,
    stop: Option<&AtomicBool>,
    settings: &LadderSettings,
    title: &str,
    text: &str,
    min_similarity: f64,
) -> Result<EvidenceWeight, EvidenceWeightError> {
    let design = {
        let mut agent = DesignAgent::new(&mut *chat);
        if let Some(stop) = stop {
            agent = agent.with_stop(stop);
        }
        if let Some(condenser) = condenser.as_mut() {
            agent = agent.with_condenser(&mut **condenser);
        }
        agent.propose(title, text, min_similarity)?
    };
    let covered = design.rct_layout.is_some_and(|layout| layout.is_covered());
    let Some(start) = start_level(design.design).filter(|_| covered) else {
        return Ok(EvidenceWeight::NotCovered { design });
    };

    let outcomes = {
        let mut agent = OutcomeAgent::new(&mut *chat);
        if let Some(stop) = stop {
            agent = agent.with_stop(stop);
        }
        if let Some(condenser) = condenser.as_mut() {
            agent = agent.with_condenser(&mut **condenser);
        }
        agent.propose(title, text, min_similarity)?
    };
    if outcomes.outcomes.is_empty() {
        return Ok(EvidenceWeight::NoPrimaryOutcome { design, outcomes });
    }

    let run = {
        let mut agent = Rob2Agent::new(&mut *chat);
        if let Some(stop) = stop {
            agent = agent.with_stop(stop);
        }
        if let Some(condenser) = condenser.as_mut() {
            agent = agent.with_condenser(&mut **condenser);
        }
        agent.assess(title, text, &outcomes.outcomes, min_similarity)?
    };

    let assessed: Vec<AssessedOutcome> = outcomes
        .outcomes
        .iter()
        .zip(&run.outcomes)
        .map(|(proposed, rob2)| {
            let mut error = ladder_error(&run.study, proposed, rob2);
            let mut ladder = None;
            if let (None, Some(overall)) = (&error, rob2.overall) {
                match build_ladder(start, overall, proposed.result.as_ref(), settings) {
                    Ok(built) => ladder = Some(built),
                    Err(message) => error = Some(message),
                }
            }
            AssessedOutcome {
                outcome: proposed.clone(),
                rob2: rob2.clone(),
                ladder,
                ladder_error: error,
            }
        })
        .collect();
    let paper_weight = weight_of(&assessed);
    let condensed = any_condensed(&design, &outcomes, &run);
    Ok(EvidenceWeight::Assessed {
        design,
        outcomes,
        run,
        assessed,
        paper_weight,
        condensed,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::agents::{ChatJsonError, ChatJsonOutcome};
    use crate::llm::LLMMessage;
    use crate::quality::evidence_ladder::Level;
    use crate::quality::rob2_agent::tests::{low_run, Condenser, SIM, TEXT as RANDOMISED};
    use crate::quality::rob2_algorithm::{Domain, Judgement};
    use std::sync::atomic::Ordering;

    const DESIGN_QUOTE: &str = "Participants were randomly assigned 1:1 to metformin or placebo.";
    const OUTCOME_QUOTE: &str = "The primary outcome was all-cause mortality at 28 days.";

    fn text() -> String {
        format!("{DESIGN_QUOTE} {OUTCOME_QUOTE} {RANDOMISED}")
    }

    fn design(study_design: &str, layout: Option<&str>) -> Value {
        json!({
            "study_design": study_design,
            "rct_layout": layout,
            "quotes": [DESIGN_QUOTE],
            "reasoning": "Why.",
        })
    }

    fn outcome(name: &str, ci_low: f64, ci_high: f64) -> Value {
        json!({
            "name": name,
            "timepoint": "28 days",
            "quotes": [OUTCOME_QUOTE],
            "result": {
                "measure": "risk ratio", "scale": "ratio", "estimate": 0.7,
                "ci_low": ci_low, "ci_high": ci_high, "ci_level": 0.95, "n_analysed": 1200,
            },
        })
    }

    fn outcomes(list: Vec<Value>) -> Value {
        json!({ "primary_outcomes": list })
    }

    /// Answers each request with the next reply; sets `stop` at request `stop_at`.
    struct Scripted<'s> {
        replies: Vec<Value>,
        sent: usize,
        stop: Option<(&'s AtomicBool, usize)>,
    }

    impl<'s> Scripted<'s> {
        fn new(replies: Vec<Value>) -> Self {
            Scripted {
                replies,
                sent: 0,
                stop: None,
            }
        }
    }

    impl JsonChat for Scripted<'_> {
        fn chat_json(
            &mut self,
            _messages: &[LLMMessage],
            _temperature: f64,
            _max_tokens: i64,
            _max_retries: usize,
            _require_dict: bool,
        ) -> Result<ChatJsonOutcome, ChatJsonError> {
            self.sent += 1;
            if let Some((stop, at)) = self.stop {
                if self.sent == at {
                    stop.store(true, Ordering::SeqCst);
                }
            }
            Ok(ChatJsonOutcome {
                value: self
                    .replies
                    .get(self.sent - 1)
                    .expect("no more replies")
                    .clone(),
                attempts: 1,
                repaired: false,
            })
        }
    }

    fn run(chat: &mut Scripted<'_>, text: &str) -> Result<EvidenceWeight, EvidenceWeightError> {
        assess_unattended(
            chat,
            None,
            None,
            &LadderSettings::default(),
            "A trial",
            text,
            SIM,
        )
    }

    fn assessed(result: EvidenceWeight) -> (Vec<AssessedOutcome>, Option<f64>, bool) {
        match result {
            EvidenceWeight::Assessed {
                assessed,
                paper_weight,
                condensed,
                ..
            } => (assessed, paper_weight, condensed),
            other => panic!("not assessed: {other:?}"),
        }
    }

    #[test]
    fn a_design_not_covered_stops_after_one_request() {
        for (study, layout) in [
            ("cohort_prospective", None),
            ("rct", Some("crossover")),
            ("rct", Some("cluster")),
        ] {
            let mut chat = Scripted::new(vec![design(study, layout)]);
            let result = run(&mut chat, &text()).expect("result");
            assert!(
                matches!(result, EvidenceWeight::NotCovered { .. }),
                "{study}"
            );
            assert_eq!(chat.sent, 1);
        }
    }

    #[test]
    fn no_primary_outcome_stops_before_rob2() {
        let mut chat = Scripted::new(vec![design("rct", Some("parallel")), outcomes(vec![])]);
        let result = run(&mut chat, &text()).expect("result");
        let EvidenceWeight::NoPrimaryOutcome { outcomes, .. } = result else {
            panic!("not NoPrimaryOutcome");
        };
        assert!(outcomes.outcomes.is_empty());
        assert_eq!(chat.sent, 2);
    }

    #[test]
    fn a_step_that_cannot_be_made_names_its_own_error() {
        let mut chat = Scripted::new(vec![]);
        let error = run(&mut chat, "   ").expect_err("empty");
        assert_eq!(
            error,
            EvidenceWeightError::Proposal(ProposalError::EmptyInput)
        );
        assert_eq!(error.to_string(), "the paper's text is empty");
    }

    #[test]
    fn a_stop_in_rob2_is_the_rob2_error() {
        let stop = AtomicBool::new(false);
        let mut chat = Scripted::new(vec![
            design("rct", Some("parallel")),
            outcomes(vec![outcome("Mortality", 0.55, 0.88)]),
        ]);
        chat.stop = Some((&stop, 2));
        let error = assess_unattended(
            &mut chat,
            None,
            Some(&stop),
            &LadderSettings::default(),
            "A trial",
            &text(),
            SIM,
        )
        .expect_err("stopped");
        assert_eq!(error, EvidenceWeightError::Rob2(Rob2RunError::Stopped));
    }

    fn one_outcome_replies() -> Vec<Value> {
        let mut replies = vec![
            design("rct", Some("parallel")),
            outcomes(vec![outcome("Mortality", 0.55, 0.88)]),
        ];
        replies.extend(low_run(1));
        replies
    }

    #[test]
    fn one_outcome_runs_through_to_a_weight() {
        let mut chat = Scripted::new(one_outcome_replies());
        let (assessed, weight, condensed) = assessed(run(&mut chat, &text()).expect("result"));
        assert_eq!(chat.sent, 2 + 5);
        let [only] = assessed.as_slice() else {
            panic!("one outcome");
        };
        assert_eq!(
            (only.outcome.name.as_str(), &only.ladder_error),
            ("Mortality", &None)
        );
        let ladder = only.ladder.as_ref().expect("ladder");
        assert_eq!((ladder.start, ladder.level), (Level::High, Level::High));
        assert_eq!(weight, Some(ladder.weight));
        assert_eq!(weight, Some(LadderSettings::default().weight_high));
        assert!(!condensed);
    }

    #[test]
    fn a_refused_result_has_no_ladder_and_the_other_outcome_sets_the_weight() {
        let mut replies = vec![
            design("rct", Some("parallel")),
            outcomes(vec![
                outcome("Mortality", 0.55, 0.88),
                outcome("Hospitalisation", 0.9, 0.5),
            ]),
        ];
        replies.extend(low_run(2));
        let mut chat = Scripted::new(replies);
        let (assessed, weight, _) = assessed(run(&mut chat, &text()).expect("result"));
        assert_eq!(chat.sent, 2 + 9);
        let [good, bad] = assessed.as_slice() else {
            panic!("two outcomes");
        };
        assert_eq!(bad.rob2.overall, Some(Judgement::Low));
        let refusal = bad.outcome.result_error.clone().expect("refused");
        assert!(bad.ladder.is_none());
        assert_eq!(
            bad.ladder_error,
            Some(format!("the reported result was refused: {refusal}"))
        );
        assert_eq!(weight, good.ladder.as_ref().map(|l| l.weight));
        assert!(weight.is_some());
    }

    fn bad_d3() -> Value {
        json!({"answers": {"3.1": {"answer": "Unclear", "quotes": [], "reasoning": "x"}}})
    }

    #[test]
    fn a_failed_domain_leaves_that_outcome_not_assessed() {
        let mut replies = vec![
            design("rct", Some("parallel")),
            outcomes(vec![
                outcome("Mortality", 0.55, 0.88),
                outcome("Hospitalisation", 0.55, 0.88),
            ]),
        ];
        let rob2 = low_run(2);
        // D1, D2, a D3 that stays unreadable after the re-ask, D4, D5; then the second outcome.
        replies.extend([rob2[0].clone(), rob2[1].clone(), bad_d3(), bad_d3()]);
        replies.extend(rob2[3..].iter().cloned());
        let mut chat = Scripted::new(replies);
        let (assessed, weight, _) = assessed(run(&mut chat, &text()).expect("result"));
        let [failed, fine] = assessed.as_slice() else {
            panic!("two outcomes");
        };
        assert!(failed.rob2.overall.is_none() && failed.ladder.is_none());
        let error = failed.ladder_error.as_deref().expect("reason");
        assert!(
            error.starts_with("the outcome's risk of bias was not assessed: D3 failed ("),
            "{error}"
        );
        assert!(error.contains("answers.3.1.answer"), "{error}");
        assert_eq!(weight, fine.ladder.as_ref().map(|l| l.weight));
        assert!(weight.is_some());
    }

    #[test]
    fn no_ladder_at_all_is_no_weight() {
        let mut replies = vec![
            design("rct", Some("parallel")),
            outcomes(vec![outcome("Mortality", 0.55, 0.88)]),
            json!({"answers": {}}),
            json!({"answers": {}}),
        ];
        replies.extend(low_run(1)[1..].iter().cloned());
        let mut chat = Scripted::new(replies);
        let (assessed, weight, _) = assessed(run(&mut chat, &text()).expect("result"));
        let error = assessed[0].ladder_error.as_deref().expect("reason");
        assert!(
            assessed[0].ladder.is_none() && error.contains("D1 failed ("),
            "{error}"
        );
        assert_eq!(weight, None);
    }

    #[test]
    fn a_paper_over_the_window_is_flagged_condensed() {
        let big = format!(
            "{} {}",
            text(),
            "Filler sentence about nothing. ".repeat(2000)
        );
        let mut condenser = Condenser(Vec::new());
        let mut chat = Scripted::new(one_outcome_replies());
        let result = assess_unattended(
            &mut chat,
            Some(&mut condenser),
            None,
            &LadderSettings::default(),
            "A trial",
            &big,
            SIM,
        )
        .expect("result");
        assert!(assessed(result).2);
        assert_eq!(condenser.0.len(), 2 + 5);
    }

    fn parts() -> (DesignProposal, OutcomeProposal, Rob2Run) {
        let mut chat = Scripted::new(one_outcome_replies());
        match run(&mut chat, &text()).expect("result") {
            EvidenceWeight::Assessed {
                design,
                outcomes,
                run,
                ..
            } => (design, outcomes, run),
            other => panic!("not assessed: {other:?}"),
        }
    }

    #[test]
    fn condensed_if_any_step_was() {
        let (d, o, r) = parts();
        assert!(!any_condensed(&d, &o, &r));
        let mut one = d.clone();
        one.condensed = true;
        assert!(any_condensed(&one, &o, &r));
        let mut one = o.clone();
        one.condensed = true;
        assert!(any_condensed(&d, &one, &r));
        let mut one = r.clone();
        let DomainResult::Assessed(domain) = &mut one.outcomes[0].domains[2] else {
            panic!("D4 assessed");
        };
        domain.condensed = true;
        assert_eq!(domain.domain, Domain::D4);
        assert!(any_condensed(&d, &o, &one));
    }

    fn assessed_two() -> EvidenceWeight {
        let mut replies = vec![
            design("rct", Some("parallel")),
            outcomes(vec![
                outcome("Mortality", 0.55, 0.88),
                outcome("Hospitalisation", 0.9, 0.5),
            ]),
        ];
        replies.extend(low_run(2));
        run(&mut Scripted::new(replies), &text()).expect("result")
    }

    #[test]
    fn every_status_round_trips_through_json() {
        let results = [
            run(
                &mut Scripted::new(vec![design("cohort_prospective", None)]),
                &text(),
            )
            .expect("not covered"),
            run(
                &mut Scripted::new(vec![design("rct", Some("parallel")), outcomes(vec![])]),
                &text(),
            )
            .expect("none"),
            assessed_two(),
        ];
        for original in results {
            let wire: Value = serde_json::from_str(&original.to_json().to_string()).expect("json");
            assert_eq!(EvidenceWeight::from_json(&wire), Ok(original));
        }
    }

    #[test]
    fn what_does_not_add_up_is_refused() {
        type Change = fn(&mut Value);
        let cases: [(Change, &str); 6] = [
            (|w| w["status"] = json!("maybe"), "status \"maybe\""),
            (|w| w["paper_weight"] = json!(0.1), "paper_weight"),
            (
                |w| drop(w["assessed"].as_array_mut().unwrap().pop()),
                "same length",
            ),
            (
                |w| w["assessed"].as_array_mut().unwrap().reverse(),
                "assessed[0] is not outcome 0",
            ),
            (
                |w| w["assessed"][0]["ladder_error"] = json!("x"),
                "exactly one",
            ),
            (
                |w| w["assessed"][0]["ladder"]["level"] = json!("best"),
                "ladder",
            ),
        ];
        for (change, message) in cases {
            let mut wire = assessed_two().to_json();
            change(&mut wire);
            let error = EvidenceWeight::from_json(&wire).expect_err(message);
            assert!(error.contains(message), "{error} lacks {message}");
        }
    }
}
