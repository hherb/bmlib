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

//! RoB 2 decision rules: signalling answers to judgements, no LLM, no wording.
//!
//! The official mapping tables of the Cochrane RoB 2 guidance (22 August 2019,
//! the randomised-parallel-group tool, effect of *assignment*) as code: Table 1
//! (overall), Tables 3-4 (domain 1), 6 (domain 2), 10 (domain 3), 12 (domain 4)
//! and 14 (domain 5). `bmlib/quality/rob2_algorithm.py` is the same rules;
//! `tests/fixtures/rob2_algorithm.json` holds both to them.
//!
//! In the comments "Y" means Y or PY and "N" means N or PN, as the guidance says.

use std::collections::BTreeMap;
use std::fmt;

use serde::{Deserialize, Serialize};

/// Names the rule set; stored beside a result so it can be re-judged when the rules change.
pub const ROB2_ALGORITHM_VERSION: &str = "rob2-2019-08-22/assignment/parallel/1";

/// One signalling-question answer.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Answer {
    /// Yes.
    #[serde(rename = "Y")]
    Y,
    /// Probably yes.
    #[serde(rename = "PY")]
    Py,
    /// Probably no.
    #[serde(rename = "PN")]
    Pn,
    /// No.
    #[serde(rename = "N")]
    N,
    /// No information.
    #[serde(rename = "NI")]
    Ni,
}

impl Answer {
    /// Read an answer, ignoring case and surrounding space.
    ///
    /// `"NA"` is not an answer (a question that is not applicable is simply not
    /// answered), so it is [`Rob2Error::UnreadableAnswer`].
    pub fn parse(text: &str) -> Result<Answer, Rob2Error> {
        match text.trim().to_uppercase().as_str() {
            "Y" => Ok(Answer::Y),
            "PY" => Ok(Answer::Py),
            "PN" => Ok(Answer::Pn),
            "N" => Ok(Answer::N),
            "NI" => Ok(Answer::Ni),
            _ => Err(Rob2Error::UnreadableAnswer {
                value: text.to_string(),
            }),
        }
    }

    /// The answer with Y/PY and N/PN merged, which is all the rules look at.
    fn collapsed(self) -> Code {
        match self {
            Answer::Y | Answer::Py => Code::Yes,
            Answer::Pn | Answer::N => Code::No,
            Answer::Ni => Code::Ni,
        }
    }
}

/// An answer with the "probably" forms merged into the certain ones.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Code {
    Yes,
    No,
    Ni,
}

/// A risk-of-bias judgement.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Judgement {
    /// Low risk of bias.
    #[serde(rename = "low")]
    Low,
    /// Some concerns.
    #[serde(rename = "some_concerns")]
    SomeConcerns,
    /// High risk of bias.
    #[serde(rename = "high")]
    High,
}

/// Whether a domain is judged once per study or once per outcome.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Scope {
    /// Judged once for the whole study.
    #[serde(rename = "study")]
    Study,
    /// Judged separately for each outcome.
    #[serde(rename = "outcome")]
    Outcome,
}

/// The five RoB 2 domains.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
pub enum Domain {
    /// Randomisation process.
    D1,
    /// Deviations from the intended interventions.
    D2,
    /// Missing outcome data.
    D3,
    /// Measurement of the outcome.
    D4,
    /// Selection of the reported result.
    D5,
}

impl Domain {
    /// All five domains in order.
    pub const ALL: [Domain; 5] = [Domain::D1, Domain::D2, Domain::D3, Domain::D4, Domain::D5];

    /// Domain 1 (randomisation) is judged per study, the rest per outcome.
    pub fn scope(self) -> Scope {
        match self {
            Domain::D1 => Scope::Study,
            _ => Scope::Outcome,
        }
    }
}

/// Question id such as `"2.3"` to its answer; a question not asked is absent.
pub type Answers = BTreeMap<String, Answer>;

/// A refusal.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Rob2Error {
    /// An applicable question was left unanswered.
    MissingAnswer {
        /// The question id.
        question: String,
    },
    /// The answer is not allowed for the question (3.2 has no NI).
    NotAllowed {
        /// The question id.
        question: String,
        /// The refused answer.
        answer: Answer,
    },
    /// The question id is not one of the domain's.
    UnknownQuestion {
        /// The question id.
        question: String,
    },
    /// The domains need the assessor's escalation decision and none was given.
    EscalationUndecided,
    /// The text is not one of Y, PY, PN, N, NI.
    UnreadableAnswer {
        /// The text as given.
        value: String,
    },
}

impl fmt::Display for Rob2Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Rob2Error::MissingAnswer { question } => {
                write!(f, "question {question} is applicable but unanswered")
            }
            Rob2Error::NotAllowed { question, answer } => {
                write!(f, "{answer:?} is not allowed for question {question}")
            }
            Rob2Error::UnknownQuestion { question } => {
                write!(f, "question {question} is not in this domain")
            }
            Rob2Error::EscalationUndecided => {
                write!(f, "the overall judgement needs an escalation decision")
            }
            Rob2Error::UnreadableAnswer { value } => {
                write!(f, "{value:?} is not one of Y, PY, PN, N, NI")
            }
        }
    }
}

impl std::error::Error for Rob2Error {}

/// The domain's signalling-question ids, in order.
pub fn questions(domain: Domain) -> &'static [&'static str] {
    match domain {
        Domain::D1 => &["1.1", "1.2", "1.3"],
        Domain::D2 => &["2.1", "2.2", "2.3", "2.4", "2.5", "2.6", "2.7"],
        Domain::D3 => &["3.1", "3.2", "3.3", "3.4"],
        Domain::D4 => &["4.1", "4.2", "4.3", "4.4", "4.5"],
        Domain::D5 => &["5.1", "5.2", "5.3"],
    }
}

/// The answer to `question` collapsed to Y / N / NI, if there is one.
fn code(answers: &Answers, question: &str) -> Option<Code> {
    answers.get(question).map(|a| a.collapsed())
}

/// True when `question`'s answer is one of `allowed`; false if unanswered.
fn is_one_of(answers: &Answers, question: &str, allowed: &[Code]) -> bool {
    code(answers, question).is_some_and(|c| allowed.contains(&c))
}

/// The conditions for asking a question, in one place; unlisted ones are always asked.
/// A prerequisite that is itself unanswered counts as "not met".
fn is_asked(question: &str, answers: &Answers) -> bool {
    use Code::{Ni, No, Yes};
    match question {
        "2.3" => is_one_of(answers, "2.1", &[Yes, Ni]) || is_one_of(answers, "2.2", &[Yes, Ni]),
        "2.4" => is_one_of(answers, "2.3", &[Yes]),
        "2.5" => is_one_of(answers, "2.4", &[Yes, Ni]),
        "2.7" => is_one_of(answers, "2.6", &[No, Ni]),
        "3.2" => is_one_of(answers, "3.1", &[No, Ni]),
        "3.3" => is_one_of(answers, "3.2", &[No]),
        "3.4" => is_one_of(answers, "3.3", &[Yes, Ni]),
        "4.3" => is_one_of(answers, "4.1", &[No, Ni]) && is_one_of(answers, "4.2", &[No, Ni]),
        "4.4" => is_one_of(answers, "4.3", &[Yes, Ni]),
        "4.5" => is_one_of(answers, "4.4", &[Yes, Ni]),
        _ => true,
    }
}

/// The questions to ask given the answers so far, in order.
///
/// A question whose prerequisite is not yet answered is not listed.
pub fn applicable_questions(domain: Domain, answers: &Answers) -> Vec<&'static str> {
    questions(domain)
        .iter()
        .copied()
        .filter(|q| is_asked(q, answers))
        .collect()
}

/// The applicable answers of a domain, validated and collapsed.
type Collapsed = BTreeMap<&'static str, Code>;

/// Validate `answers` and return the applicable ones collapsed to Y / N / NI.
fn checked(domain: Domain, answers: &Answers) -> Result<Collapsed, Rob2Error> {
    if let Some(question) = answers
        .keys()
        .find(|q| !questions(domain).contains(&q.as_str()))
    {
        return Err(Rob2Error::UnknownQuestion {
            question: question.clone(),
        });
    }
    let mut collapsed = Collapsed::new();
    for question in applicable_questions(domain, answers) {
        let answer = *answers
            .get(question)
            .ok_or_else(|| Rob2Error::MissingAnswer {
                question: question.to_string(),
            })?;
        if answer == Answer::Ni && question == "3.2" {
            return Err(Rob2Error::NotAllowed {
                question: question.to_string(),
                answer,
            });
        }
        collapsed.insert(question, answer.collapsed());
    }
    Ok(collapsed)
}

fn d1(c: &Collapsed) -> Judgement {
    use Code::{Ni, No, Yes};
    if c["1.2"] == Yes && matches!(c["1.1"], Yes | Ni) && matches!(c["1.3"], No | Ni) {
        Judgement::Low
    } else if c["1.2"] == No || (c["1.2"] == Ni && c["1.3"] == Yes) {
        Judgement::High
    } else {
        Judgement::SomeConcerns
    }
}

fn d2(c: &Collapsed) -> Judgement {
    use Code::{Ni, No, Yes};
    let part1 = match c.get("2.3") {
        None | Some(No) => Judgement::Low,
        Some(Ni) => Judgement::SomeConcerns,
        Some(_) if c["2.4"] == No => Judgement::SomeConcerns,
        // 2.4 Y or NI, so 2.5 was asked.
        Some(_) if c["2.5"] == Yes => Judgement::SomeConcerns,
        Some(_) => Judgement::High,
    };
    let part2 = if c["2.6"] == Yes {
        Judgement::Low
    } else if c["2.7"] == No {
        Judgement::SomeConcerns
    } else {
        Judgement::High
    };
    let parts = [part1, part2];
    if parts.contains(&Judgement::High) {
        Judgement::High
    } else if parts.contains(&Judgement::SomeConcerns) {
        Judgement::SomeConcerns
    } else {
        Judgement::Low
    }
}

fn d3(c: &Collapsed) -> Judgement {
    use Code::{No, Yes};
    if c["3.1"] == Yes || c.get("3.2") == Some(&Yes) || c.get("3.3") == Some(&No) {
        Judgement::Low
    } else if c["3.4"] == No {
        Judgement::SomeConcerns
    } else {
        Judgement::High
    }
}

fn d4(c: &Collapsed) -> Judgement {
    use Code::{Ni, No, Yes};
    if c["4.1"] == Yes || c["4.2"] == Yes {
        return Judgement::High;
    }
    let five = c.get("4.5");
    if c["4.2"] == No {
        if c["4.3"] == No || c.get("4.4") == Some(&No) {
            Judgement::Low
        } else if five == Some(&No) {
            Judgement::SomeConcerns
        } else {
            Judgement::High
        }
    } else if matches!(five, Some(Yes | Ni)) {
        Judgement::High
    } else {
        Judgement::SomeConcerns
    }
}

fn d5(c: &Collapsed) -> Judgement {
    use Code::{No, Yes};
    if c["5.2"] == Yes || c["5.3"] == Yes {
        Judgement::High
    } else if c["5.1"] == Yes && c["5.2"] == No && c["5.3"] == No {
        Judgement::Low
    } else {
        Judgement::SomeConcerns
    }
}

/// The domain's judgement from its signalling answers.
///
/// Errors: [`Rob2Error::UnknownQuestion`] for an id outside the domain,
/// [`Rob2Error::MissingAnswer`] for an applicable question left unanswered,
/// [`Rob2Error::NotAllowed`] for an answer the question does not allow (3.2 has
/// no NI). Answers to questions that are not applicable are ignored.
pub fn domain_judgement(domain: Domain, answers: &Answers) -> Result<Judgement, Rob2Error> {
    let c = checked(domain, answers)?;
    Ok(match domain {
        Domain::D1 => d1(&c),
        Domain::D2 => d2(&c),
        Domain::D3 => d3(&c),
        Domain::D4 => d4(&c),
        Domain::D5 => d5(&c),
    })
}

/// True when the guidance leaves the overall judgement to the assessor: no
/// domain is High and at least two have some concerns.
pub fn needs_escalation_decision(domains: &[Judgement; 5]) -> bool {
    /// How many domains with some concerns, with none High, force the decision.
    const ESCALATION_CONCERNS: usize = 2;
    !domains.contains(&Judgement::High)
        && domains
            .iter()
            .filter(|d| **d == Judgement::SomeConcerns)
            .count()
            >= ESCALATION_CONCERNS
}

/// The overall judgement (Table 1) from the five domain judgements.
///
/// `escalate` matters only when [`needs_escalation_decision`] is true; then
/// `None` is [`Rob2Error::EscalationUndecided`].
pub fn overall_judgement(
    domains: &[Judgement; 5],
    escalate: Option<bool>,
) -> Result<Judgement, Rob2Error> {
    if domains.contains(&Judgement::High) {
        Ok(Judgement::High)
    } else if needs_escalation_decision(domains) {
        match escalate {
            None => Err(Rob2Error::EscalationUndecided),
            Some(true) => Ok(Judgement::High),
            Some(false) => Ok(Judgement::SomeConcerns),
        }
    } else if domains.iter().all(|d| *d == Judgement::Low) {
        Ok(Judgement::Low)
    } else {
        Ok(Judgement::SomeConcerns)
    }
}
