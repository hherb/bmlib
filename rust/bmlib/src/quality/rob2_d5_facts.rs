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

//! RoB 2's domain 5 from facts: the reply, its reading and the rules.
//!
//! D5 asks no signalling question. The model reports facts about the plan
//! behind the outcome assessed, the other measurements of that outcome the
//! paper names, and the main analysis set against the one reported, each with
//! quotes copied from the paper ([`facts_prompt`]). Code then decides:
//!
//! - a fact counts only when the model claims it, one of its quotes is found
//!   in the paper, and the quote can show it
//!   ([`rob2_d5_checks`](crate::quality::rob2_d5_checks), the planning check
//!   included);
//! - an other measurement is missing only when its naming passage is found
//!   and neither the reply nor a second, narrow look ([`second_look_prompt`])
//!   gives a result passage with a number or a pointer to a figure, table or
//!   supplement; a second look that fails leaves it undecided;
//! - 5.1, 5.2 and 5.3 follow from these by the evidence ladder
//!   ([`d5_answers`]), and RoB 2's algorithm gives the judgement
//!   ([`d5_assessment`](crate::quality::rob2_agent::d5_assessment)).
//!
//! The design is the RoB 2 wording smoke test's (StudyAnalyzer
//! `docs/smokescreens/2026-11-10-rob2-wording.md`, arm F version 3 with its
//! checks) and the evidence-weight design's §4.6, ported from Python's
//! `bmlib.quality.rob2_d5_facts`; `tests/fixtures/rob2_d5_facts.json` holds
//! the two together. The method is RoB 2 (Sterne JAC et al., BMJ
//! 2019;366:l4898; guidance by Higgins JPT et al., 2019); the wording here is
//! bmlib's own.

use std::collections::BTreeMap;

use serde_json::{json, Map, Value};

use crate::quality::full_text_agent::check_similarity;
use crate::quality::outcome_agent::ProposedOutcome;
use crate::quality::pico_agent::verify_quotes;
use crate::quality::pico_models::{PicoCitation, PicoContradictedQuote};
use crate::quality::rob2_agent::outcome_block;
use crate::quality::rob2_algorithm::Answer;
use crate::quality::rob2_d5_checks::{fact_verdict, shows_result, D5Fact, FactVerdict, D5_FACTS};

/// The facts call's system turn.
pub const D5_FACTS_SYSTEM_PROMPT: &str = "\
You read reports of randomised trials and report what they say about how the trial was planned.

CRITICAL RULES:
1. Report ONLY what is ACTUALLY PRESENT in the text
2. DO NOT infer, assume or judge: a fact the paper does not state is absent
3. Every fact you mark present needs a quote copied VERBATIM from the paper
4. Return ONLY valid JSON, no explanation";

/// The second look's system turn.
pub const D5_SECOND_LOOK_SYSTEM_PROMPT: &str = "\
You find passages in reports of randomised trials.

CRITICAL RULES:
1. Quote ONLY passages that are ACTUALLY PRESENT in the text, VERBATIM
2. A passage counts only if it gives the result itself, with its numbers, or points to the \
figure, table or supplement that shows it
3. Return ONLY valid JSON, no explanation";

/// What a fact means, as the model is told.
#[must_use]
pub fn fact_meaning(fact: D5Fact) -> &'static str {
    match fact {
        D5Fact::PlanCited => {
            "The paper cites a trial registration (registry and number), a published protocol, \
             or a statistical analysis plan (by reference or as a supplement)."
        }
        D5Fact::PlanDatedEarly => {
            "The paper places that registration, protocol or plan BEFORE the outcome data could \
             be seen by group: a registration date before recruitment began or before the trial \
             ended, \"prospectively registered\", a protocol published, or an analysis plan \
             finalised, before unblinding or before the data were analysed."
        }
        D5Fact::PlanLate => {
            "The paper says the registration or plan was made AFTER recruitment began or after \
             the data were collected (\"retrospectively registered\", a registration date after \
             the first participant was enrolled)."
        }
        D5Fact::MeasureFixed => {
            "For the outcome assessed, the paper states which measurement was planned in \
             advance: the instrument or scale and how the outcome is defined, as set in the \
             registration, protocol or plan, or described as prespecified. A bare \"prespecified \
             primary outcome\" that does not name the measurement does not count."
        }
        D5Fact::TimepointFixed => {
            "For the outcome assessed, the paper states the time point (or points) planned in \
             advance, as set in the registration, protocol or plan, or described as prespecified."
        }
        D5Fact::AnalysisFixed => {
            "For the outcome assessed, the paper states the analysis planned in advance: the \
             statistical model or test, and the covariates or the handling of missing data, as \
             set in the registration, protocol or plan, or described as prespecified."
        }
        D5Fact::Switched => {
            "The paper says the outcome assessed ITSELF differs from the registration or \
             protocol: added, dropped, moved to or from primary, or defined or timed \
             differently. A change to another outcome does not count."
        }
        D5Fact::ChangeExplained => {
            "The paper gives a reason for that change to the outcome assessed that does not \
             depend on its results (for example a ruling of an ethics committee, or a change \
             made and stated before unblinding)."
        }
    }
}

/// The reply's key for the list of other measurements.
pub const OTHER_MEASUREMENTS_KEY: &str = "other_measurements";

/// What that list holds, as the model is told.
pub const OTHER_MEASUREMENTS_MEANING: &str = "Every OTHER measurement of the SAME outcome as the \
one assessed (the same thing measured) that the methods, protocol or registration name: the same \
thing on another instrument or scale, at another time point, or defined another way. Not the \
measurement assessed itself, not other outcomes, not subgroups, not within-group comparisons.";

/// The reply's key for the analysis check.
pub const MAIN_ANALYSIS_KEY: &str = "main_analysis";

/// What the analysis check asks, as the model is told.
pub const MAIN_ANALYSIS_MEANING: &str = "main_analysis: the analysis whose result the paper gives \
for the outcome assessed, set against the main analysis its methods, protocol or analysis plan \
name for it. planned: 1-2 passages naming the main analysis (the model or test, its covariates, \
the analysis population, the handling of missing data). reported: 1-2 passages giving the result \
with the analysis it came from. differs: true only when the analysis reported is NOT the one \
named as main (another model, other covariates, another population or handling of missing data) \
and the paper gives no reason for the change; false when they match, or when either is not \
stated.";

/// An example item of the measurement list in the reply's shape.
const ITEM_SHAPE: &str = "{\"label\": \"short name, e.g. pain VAS at 6 months\", \"named\": \
[\"passage naming it\"], \"result\": [\"passage giving its result, with numbers, or pointing to \
its figure or table\"]}";

/// The reason a 5.2 PY names: an other measurement missing at both looks.
pub const MISSING_MEASUREMENT: &str = OTHER_MEASUREMENTS_KEY;
/// The reason a 5.3 PY names: the analysis reported differs from the main one.
pub const DIFFERING_ANALYSIS: &str = MAIN_ANALYSIS_KEY;

/// The user turn asking for the facts, the other measurements and the
/// analysis check about `outcome` in `paper` (the whole paper, or D5's
/// digest), titled `title`.
#[must_use]
pub fn facts_prompt(title: &str, paper: &str, outcome: &ProposedOutcome) -> String {
    let listed = D5_FACTS
        .iter()
        .map(|f| format!("- {}: {}", f.as_str(), fact_meaning(*f)))
        .collect::<Vec<_>>()
        .join("\n");
    let shape = D5_FACTS
        .iter()
        .map(|f| {
            format!(
                "    \"{}\": {{\"present\": false, \"quotes\": []}}",
                f.as_str()
            )
        })
        .collect::<Vec<_>>()
        .join(",\n");
    [
        "Report the following about the randomised trial in the paper below, for the outcome \
         named. Copy every quote VERBATIM from the paper, character for character, without \
         ellipses or joined sentences."
            .to_string(),
        outcome_block(outcome),
        format!(
            "Facts: for each give present (true or false) and quotes, 1-3 passages that show it, \
             an empty list when it is absent. Mark a fact present only when the paper itself \
             states it.\n{listed}"
        ),
        format!(
            "List: for each item give a short label, named (1-2 passages from the methods, \
             protocol or registration that name it) and result (1-2 passages from the results, a \
             table or the abstract that give its result with its numbers, or that point to the \
             figure, table or supplement showing it; an empty list when the paper gives no \
             result for it, or only a summary without numbers). An empty list when there is no \
             such item.\n- {OTHER_MEASUREMENTS_KEY}: {OTHER_MEASUREMENTS_MEANING}"
        ),
        format!("Analysis check:\n- {MAIN_ANALYSIS_MEANING}"),
        format!(
            "Respond with JSON in exactly this shape, every key present:\n\n{{\n  \"facts\": \
             {{\n{shape}\n  }},\n  \"{OTHER_MEASUREMENTS_KEY}\": [{ITEM_SHAPE}],\n  \
             \"{MAIN_ANALYSIS_KEY}\": {{\"differs\": false, \"planned\": [\"...\"], \"reported\": \
             [\"...\"]}}\n}}"
        ),
        format!("Paper Title: {title}"),
        format!("Paper Text:\n{paper}"),
    ]
    .join("\n\n")
}

/// One fact as the model gave it and as the paper and the checks bear it out.
#[derive(Debug, Clone, PartialEq)]
pub struct FactReport {
    /// Whether the model said it is present.
    pub claimed: bool,
    /// The paper's own spans for its quotes that were found.
    pub citations: Vec<PicoCitation>,
    /// Its quotes not located in the paper, verbatim.
    pub quotes_not_found: Vec<String>,
    /// Its quotes the paper contradicts, each with the reason.
    pub contradicted: Vec<PicoContradictedQuote>,
    /// How many quote members were not usable text.
    pub quotes_dropped: usize,
    /// Whether it counts, and if not, why ([`fact_verdict`]).
    pub verdict: FactVerdict,
}

impl FactReport {
    /// Whether the fact counts.
    #[must_use]
    pub fn present(&self) -> bool {
        self.verdict == FactVerdict::Present
    }

    /// Python's `to_dict()`.
    #[must_use]
    pub fn to_json(&self) -> Value {
        json!({
            "claimed": self.claimed,
            "citations": citations_json(&self.citations),
            "quotes_not_found": self.quotes_not_found,
            "contradicted": self.contradicted.iter().map(PicoContradictedQuote::to_json).collect::<Vec<_>>(),
            "quotes_dropped": self.quotes_dropped,
            "verdict": self.verdict.as_str(),
        })
    }
}

/// An other measurement of the outcome the model listed, as the paper bears
/// it out.
#[derive(Debug, Clone, PartialEq)]
pub struct OtherMeasurement {
    /// The model's short name for it.
    pub label: String,
    /// The paper's spans naming it that were found.
    pub named: Vec<PicoCitation>,
    /// The paper's spans giving its result that were found.
    pub result: Vec<PicoCitation>,
    /// What a second look found; `None` when none was made.
    pub second_look: Option<Vec<PicoCitation>>,
    /// Why the second look failed, when it did.
    pub second_look_error: Option<String>,
}

impl OtherMeasurement {
    /// Whether a passage naming it was found in the paper.
    #[must_use]
    pub fn counts(&self) -> bool {
        !self.named.is_empty()
    }

    /// Whether a found result passage, at either look, shows a result.
    #[must_use]
    pub fn reported(&self) -> bool {
        let passages: Vec<&str> = self
            .result
            .iter()
            .chain(self.second_look.iter().flatten())
            .map(|c| c.text.as_str())
            .collect();
        shows_result(&passages)
    }

    /// Whether it counts, shows no result yet, and has had no second look.
    #[must_use]
    pub fn needs_second_look(&self) -> bool {
        self.counts() && !self.reported() && self.second_look.is_none()
    }

    /// Whether it counts and neither look found its result (a failed look
    /// does not count).
    #[must_use]
    pub fn missing(&self) -> bool {
        self.counts() && !self.reported() && self.second_look.is_some()
    }

    /// Python's `to_dict()`.
    #[must_use]
    pub fn to_json(&self) -> Value {
        json!({
            "label": self.label,
            "named": citations_json(&self.named),
            "result": citations_json(&self.result),
            "second_look": self.second_look.as_deref().map(citations_json),
            "second_look_error": self.second_look_error,
        })
    }
}

/// The main analysis the methods name, set against the one reported.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct AnalysisCheck {
    /// Whether the model says they differ with no reason given.
    pub differs: bool,
    /// The paper's spans naming the main analysis that were found.
    pub planned: Vec<PicoCitation>,
    /// The paper's spans giving the result with its analysis that were found.
    pub reported: Vec<PicoCitation>,
}

impl AnalysisCheck {
    /// Whether they differ, with a found passage for each side.
    #[must_use]
    pub fn counts(&self) -> bool {
        self.differs && !self.planned.is_empty() && !self.reported.is_empty()
    }

    /// Python's `to_dict()`.
    #[must_use]
    pub fn to_json(&self) -> Value {
        json!({
            "differs": self.differs,
            "planned": citations_json(&self.planned),
            "reported": citations_json(&self.reported),
        })
    }
}

/// Everything D5 is answered from, for one outcome.
#[derive(Debug, Clone, PartialEq)]
pub struct D5Facts {
    /// The name of the outcome assessed, which the checks read.
    pub outcome: String,
    /// Every fact of [`D5_FACTS`].
    pub facts: BTreeMap<D5Fact, FactReport>,
    /// The other measurements listed, in the reply's order.
    pub measurements: Vec<OtherMeasurement>,
    /// The analysis check.
    pub analysis: AnalysisCheck,
}

impl D5Facts {
    /// Whether `fact` counts.
    #[must_use]
    pub fn present(&self, fact: D5Fact) -> bool {
        self.facts.get(&fact).is_some_and(FactReport::present)
    }

    /// Python's `to_dict()`.
    #[must_use]
    pub fn to_json(&self) -> Value {
        let facts: Map<String, Value> = self
            .facts
            .iter()
            .map(|(f, r)| (f.as_str().to_string(), r.to_json()))
            .collect();
        json!({
            "outcome": self.outcome,
            "facts": facts,
            OTHER_MEASUREMENTS_KEY: self.measurements.iter().map(OtherMeasurement::to_json).collect::<Vec<_>>(),
            MAIN_ANALYSIS_KEY: self.analysis.to_json(),
        })
    }

    /// Python's `from_dict()`: rebuilds from [`to_json`](Self::to_json)
    /// output, recomputing every verdict.
    ///
    /// # Errors
    ///
    /// A message naming the field that is absent or of the wrong type, or the
    /// fact whose stored verdict is not the one the checks give.
    pub fn from_json(value: &Value) -> Result<Self, String> {
        let data = object(value, "the facts")?;
        let outcome = field_text(data, "outcome", "")?.to_string();
        let raw = data
            .get("facts")
            .and_then(Value::as_object)
            .ok_or("facts is not an object")?;
        let mut facts = BTreeMap::new();
        for fact in D5_FACTS {
            let path = format!("facts.{}", fact.as_str());
            let item = object(raw.get(fact.as_str()).unwrap_or(&Value::Null), &path)?;
            let claimed = item
                .get("claimed")
                .and_then(Value::as_bool)
                .ok_or_else(|| format!("{path}.claimed is not a boolean"))?;
            let citations = citations_field(item, "citations", &path)?;
            let stored = field_text(item, "verdict", &path)?;
            let found: Vec<&str> = citations.iter().map(|c| c.text.as_str()).collect();
            let verdict = fact_verdict(fact, claimed, &found, &outcome);
            if stored != verdict.as_str() {
                return Err(format!(
                    "{path}.verdict {stored} is not {}, which the checks give",
                    verdict.as_str()
                ));
            }
            let quotes_not_found = list_field(item, "quotes_not_found", &path)?
                .iter()
                .map(|q| q.as_str().map(str::to_string))
                .collect::<Option<Vec<_>>>()
                .ok_or_else(|| format!("{path}.quotes_not_found is not a list"))?;
            let contradicted = list_field(item, "contradicted", &path)?
                .iter()
                .map(PicoContradictedQuote::from_json)
                .collect::<Result<_, _>>()?;
            let quotes_dropped = item
                .get("quotes_dropped")
                .and_then(Value::as_u64)
                .and_then(|n| usize::try_from(n).ok())
                .ok_or_else(|| format!("{path}.quotes_dropped is not a count"))?;
            facts.insert(
                fact,
                FactReport {
                    claimed,
                    citations,
                    quotes_not_found,
                    contradicted,
                    quotes_dropped,
                    verdict,
                },
            );
        }
        let mut measurements = Vec::new();
        for (n, item) in list_field(data, OTHER_MEASUREMENTS_KEY, "")?
            .iter()
            .enumerate()
        {
            let path = format!("{OTHER_MEASUREMENTS_KEY}[{n}]");
            let item = object(item, &path)?;
            let second_look = match item.get("second_look") {
                None | Some(Value::Null) => None,
                Some(_) => Some(citations_field(item, "second_look", &path)?),
            };
            let second_look_error = match item.get("second_look_error") {
                None | Some(Value::Null) => None,
                Some(Value::String(e)) => Some(e.clone()),
                Some(_) => return Err(format!("{path}.second_look_error is not text")),
            };
            measurements.push(OtherMeasurement {
                label: field_text(item, "label", &path)?.to_string(),
                named: citations_field(item, "named", &path)?,
                result: citations_field(item, "result", &path)?,
                second_look,
                second_look_error,
            });
        }
        let check = object(
            data.get(MAIN_ANALYSIS_KEY).unwrap_or(&Value::Null),
            MAIN_ANALYSIS_KEY,
        )?;
        let analysis = AnalysisCheck {
            differs: check
                .get("differs")
                .and_then(Value::as_bool)
                .ok_or_else(|| format!("{MAIN_ANALYSIS_KEY}.differs is not a boolean"))?,
            planned: citations_field(check, "planned", MAIN_ANALYSIS_KEY)?,
            reported: citations_field(check, "reported", MAIN_ANALYSIS_KEY)?,
        };
        Ok(D5Facts {
            outcome,
            facts,
            measurements,
            analysis,
        })
    }
}

/// Citations as JSON.
fn citations_json(citations: &[PicoCitation]) -> Vec<Value> {
    citations.iter().map(PicoCitation::to_json).collect()
}

/// `value` as an object, or a message naming `what`.
fn object<'v>(value: &'v Value, what: &str) -> Result<&'v Map<String, Value>, String> {
    value
        .as_object()
        .ok_or_else(|| format!("{what} is not an object"))
}

/// `path.key` for a message; `key` alone when `path` is empty.
fn at(path: &str, key: &str) -> String {
    if path.is_empty() {
        key.to_string()
    } else {
        format!("{path}.{key}")
    }
}

/// `data[key]` as text.
fn field_text<'v>(data: &'v Map<String, Value>, key: &str, path: &str) -> Result<&'v str, String> {
    data.get(key)
        .and_then(Value::as_str)
        .ok_or_else(|| format!("{} is not text", at(path, key)))
}

/// `data[key]` as a list.
fn list_field<'v>(
    data: &'v Map<String, Value>,
    key: &str,
    path: &str,
) -> Result<&'v Vec<Value>, String> {
    data.get(key)
        .and_then(Value::as_array)
        .ok_or_else(|| format!("{} is not a list", at(path, key)))
}

/// `data[key]` as a list of citations.
fn citations_field(
    data: &Map<String, Value>,
    key: &str,
    path: &str,
) -> Result<Vec<PicoCitation>, String> {
    list_field(data, key, path)?
        .iter()
        .map(PicoCitation::from_json)
        .collect()
}

/// The quotes of `value` found in `text`, `min_similarity` already checked.
fn found(
    value: Option<&Value>,
    text: &str,
    min_similarity: f64,
) -> Result<Vec<PicoCitation>, String> {
    verify_quotes(value, text, min_similarity)
        .map(|(citations, ..)| citations)
        .map_err(|e| e.to_string())
}

/// Read the facts reply, verifying every quote against `text` (the paper as
/// passed, never a digest), for the outcome named `outcome`.
///
/// # Errors
///
/// `min_similarity` is not a number in (0, 1]; or the reply or a part of it
/// is not the shape asked for, the message naming the field
/// (`facts.plan_cited.present`).
pub fn read_facts_reply(
    data: &Value,
    text: &str,
    outcome: &str,
    min_similarity: f64,
) -> Result<D5Facts, String> {
    check_similarity(min_similarity)?;
    let data = data.as_object().ok_or("expected an object")?;
    let raw = data
        .get("facts")
        .and_then(Value::as_object)
        .ok_or("facts is not an object")?;
    let mut facts = BTreeMap::new();
    for fact in D5_FACTS {
        let path = format!("facts.{}", fact.as_str());
        let item = raw
            .get(fact.as_str())
            .and_then(Value::as_object)
            .ok_or_else(|| format!("{path} is missing or not an object"))?;
        let claimed = item
            .get("present")
            .and_then(Value::as_bool)
            .ok_or_else(|| format!("{path}.present is not true or false"))?;
        let (citations, quotes_not_found, contradicted, quotes_dropped) =
            verify_quotes(item.get("quotes"), text, min_similarity).map_err(|e| e.to_string())?;
        let texts: Vec<&str> = citations.iter().map(|c| c.text.as_str()).collect();
        let verdict = fact_verdict(fact, claimed, &texts, outcome);
        facts.insert(
            fact,
            FactReport {
                claimed,
                citations,
                quotes_not_found,
                contradicted,
                quotes_dropped,
                verdict,
            },
        );
    }
    let items = data
        .get(OTHER_MEASUREMENTS_KEY)
        .and_then(Value::as_array)
        .ok_or_else(|| format!("{OTHER_MEASUREMENTS_KEY} is missing or not a list"))?;
    let mut measurements = Vec::new();
    for (n, entry) in items.iter().enumerate() {
        let path = format!("{OTHER_MEASUREMENTS_KEY}[{n}]");
        let entry = entry
            .as_object()
            .ok_or_else(|| format!("{path} is not an object"))?;
        let label = entry
            .get("label")
            .and_then(Value::as_str)
            .filter(|l| !l.trim().is_empty())
            .ok_or_else(|| format!("{path}.label is not text"))?;
        measurements.push(OtherMeasurement {
            label: label.to_string(),
            named: found(entry.get("named"), text, min_similarity)?,
            result: found(entry.get("result"), text, min_similarity)?,
            second_look: None,
            second_look_error: None,
        });
    }
    let check = data
        .get(MAIN_ANALYSIS_KEY)
        .and_then(Value::as_object)
        .ok_or_else(|| format!("{MAIN_ANALYSIS_KEY} is missing or not an object"))?;
    let differs = check
        .get("differs")
        .and_then(Value::as_bool)
        .ok_or_else(|| format!("{MAIN_ANALYSIS_KEY}.differs is not true or false"))?;
    Ok(D5Facts {
        outcome: outcome.to_string(),
        facts,
        measurements,
        analysis: AnalysisCheck {
            differs,
            planned: found(check.get("planned"), text, min_similarity)?,
            reported: found(check.get("reported"), text, min_similarity)?,
        },
    })
}

/// The user turn asking where `paper` (the text sent with the facts call)
/// gives `item`'s result, for `outcome`.
#[must_use]
pub fn second_look_prompt(
    title: &str,
    paper: &str,
    outcome: &ProposedOutcome,
    item: &OtherMeasurement,
) -> String {
    let named = item
        .named
        .iter()
        .map(|c| c.text.as_str())
        .collect::<Vec<_>>()
        .join(" / ");
    [
        format!(
            "Find where the paper below gives the result of this measurement or analysis of the \
             outcome, which its methods name: \"{}\", named as: \"{named}\".",
            item.label
        ),
        outcome_block(outcome),
        "Quote 1-3 passages, VERBATIM, from the results, a table or the abstract that give its \
         result with its numbers, or that point to the figure, table or supplement showing it. If \
         the paper gives no result for it, or only a summary without numbers and without such a \
         pointer, answer found: false with no quotes."
            .to_string(),
        "Respond with JSON in exactly this shape:\n\n{\"found\": true, \"quotes\": [\"...\"]}"
            .to_string(),
        format!("Paper Title: {title}"),
        format!("Paper Text:\n{paper}"),
    ]
    .join("\n\n")
}

/// The second look's passages found in `text`; `found: false` is none.
///
/// # Errors
///
/// `min_similarity` is not a number in (0, 1]; or the reply is not an object
/// with `found` true or false.
pub fn read_second_look(
    data: &Value,
    text: &str,
    min_similarity: f64,
) -> Result<Vec<PicoCitation>, String> {
    check_similarity(min_similarity)?;
    let found_flag = data
        .as_object()
        .and_then(|d| d.get("found"))
        .and_then(Value::as_bool)
        .ok_or("found is not true or false")?;
    if !found_flag {
        return Ok(Vec::new());
    }
    found(data.get("quotes"), text, min_similarity)
}

/// 5.1, 5.2 and 5.3 from `facts`, each with what decided it: the wire names
/// of the deciding facts, or [`MISSING_MEASUREMENT`] / [`DIFFERING_ANALYSIS`].
///
/// The evidence ladder of the design's §4.6: N (Y for 5.1) needs a cited plan
/// dated before the data by group were seen that fixes every choice; the same
/// content stated by the paper alone is PN (PY for 5.1); a late plan, or
/// anything less, is NI. A switch of the outcome assessed without a reason
/// independent of its results makes 5.1 N and 5.2 Y; an other measurement
/// missing at both looks makes 5.2 PY; the analysis reported differing from
/// the main one makes 5.3 PY. RoB 2's "measured in only one way" N is not
/// used: such an outcome without a plan is NI.
#[must_use]
pub fn d5_answers(facts: &D5Facts) -> BTreeMap<String, (Answer, Vec<String>)> {
    let present = |f: D5Fact| facts.present(f);
    let plan =
        present(D5Fact::PlanCited) && present(D5Fact::PlanDatedEarly) && !present(D5Fact::PlanLate);
    let names = |fs: &[D5Fact]| {
        fs.iter()
            .map(|f| f.as_str().to_string())
            .collect::<Vec<_>>()
    };
    let ladder = |fixed: &[D5Fact], low: Answer, on_word: Answer| -> (Answer, Vec<String>) {
        if fixed.iter().all(|f| present(*f)) {
            if plan {
                let mut deciding = names(&[D5Fact::PlanCited, D5Fact::PlanDatedEarly]);
                deciding.extend(names(fixed));
                return (low, deciding);
            }
            if !present(D5Fact::PlanLate) {
                return (on_word, names(fixed));
            }
        }
        let considered: Vec<D5Fact> = std::iter::once(D5Fact::PlanLate)
            .chain(fixed.iter().copied())
            .filter(|f| present(*f))
            .collect();
        (Answer::Ni, names(&considered))
    };
    let switched = present(D5Fact::Switched) && !present(D5Fact::ChangeExplained);
    let (a51, a52) = if switched {
        (
            (Answer::N, names(&[D5Fact::Switched])),
            (Answer::Y, names(&[D5Fact::Switched])),
        )
    } else {
        let a52 = if facts.measurements.iter().any(OtherMeasurement::missing) {
            (Answer::Py, vec![MISSING_MEASUREMENT.to_string()])
        } else {
            ladder(
                &[D5Fact::MeasureFixed, D5Fact::TimepointFixed],
                Answer::N,
                Answer::Pn,
            )
        };
        (
            ladder(
                &[D5Fact::MeasureFixed, D5Fact::AnalysisFixed],
                Answer::Y,
                Answer::Py,
            ),
            a52,
        )
    };
    let a53 = if facts.analysis.counts() {
        (Answer::Py, vec![DIFFERING_ANALYSIS.to_string()])
    } else {
        ladder(&[D5Fact::AnalysisFixed], Answer::N, Answer::Pn)
    };
    BTreeMap::from([
        ("5.1".to_string(), a51),
        ("5.2".to_string(), a52),
        ("5.3".to_string(), a53),
    ])
}
