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

//! The wording of the RoB 2 questions, as bmlib puts them to a language model.
//!
//! The method is RoB 2, the Cochrane risk-of-bias tool for randomised trials
//! (Sterne JAC, Savović J, Page MJ, et al. RoB 2: a revised tool for
//! assessing risk of bias in randomised trials. BMJ 2019;366:l4898; guidance
//! by Higgins JPT et al., 22 August 2019), for an individually randomised
//! parallel-group trial and the effect of *assignment* to the interventions.
//! The question numbers are RoB 2's and [`crate::quality::rob2_algorithm`]
//! holds its decision rules.
//!
//! The guidance is licensed CC BY-NC-ND 4.0, so none of its text is copied,
//! edited or paraphrased here. Each question is written afresh in bmlib's own
//! words from the decision it feeds in [`crate::quality::rob2_algorithm`]
//! (what makes the answer Y rather than N on its branch); the guidance's
//! text, examples and list order are not reused. Each is laid out for a
//! language model reading only the paper: the question itself; for a
//! question asked only on some branches, an "Applies only when:" line (when
//! the condition clearly fails, the model answers `NA`); where in the paper
//! to look; then one line each saying what counts as Y, PY, PN, N and NI in
//! this trial. Question 3.3 is the exception to the "Applies only when:"
//! line: its branch can open when the paper does not say how much is
//! missing, which the model cannot rule on, so it is always answered and the
//! algorithm decides whether it counts. Every question stands alone: none
//! refers to another by number.
//!
//! A port of `bmlib/quality/rob2_questions.py`, byte for byte;
//! `tests/fixtures/rob2_replies.json` (its `question_texts` section) pins both.

use crate::quality::rob2_algorithm::Domain;

/// Bumped whenever any wording in this module or the agent's prompts changes,
/// so a stored run says which wording produced it. Python's
/// `ROB2_PROMPT_VERSION`.
pub const ROB2_PROMPT_VERSION: u32 = 2;

/// Question 1.1's wording.
pub const Q_1_1: &str = r##"Did chance alone decide which group each participant joined?
Look in: the methods paragraph on randomisation, any supplement, and a protocol the paper cites.
Y: the paper names a chance mechanism, for example permuted blocks from statistical software or a randomisation website. Minimisation also counts as chance here.
PY: the paper names no mechanism, but the set-up makes a proper one the likely choice, for example a trials unit or an independent statistician ran the allocation.
PN: details in the paper hint that the order could be foreseen, without saying so.
N: something other than chance set the group: a fixed pattern (alternating patients, odd and even numbers, the day of the week) or a person's decision.
NI: the paper says the trial was randomised and nothing about how."##;

/// Question 1.2's wording.
pub const Q_1_2: &str = r##"Was the next assignment out of reach of the people enrolling participants until each participant's enrolment was final?
Look in: the methods, for who kept the allocation list, how the enrolling team learned each group, and whether enrolment came first.
Y: the group was released by a system or person outside the enrolling team, only once the participant was registered, so the team could neither foresee nor swap it.
PY: the paper names a safeguard of this kind but leaves gaps, for example it does not say who prepared the materials or when the group became known.
PN: the method the paper describes left the next assignment within the enrolling team's reach, for example materials kept by that team with no safeguard stated, though nothing shows anyone looked.
N: the enrolling team, or the participants, could see, predict or alter what came next, for example because the list was open to them or the order followed a pattern.
NI: the paper says nothing about how the assignment was kept from the enrolling team."##;

/// Question 1.3's wording.
pub const Q_1_3: &str = r##"Do the groups, as they stood at the start, look different in a way that hints the randomisation went wrong?
Look in: the baseline table (characteristics by group) and the flow diagram's count randomised to each group. Judge from these numbers, not from the paper's account of its method.
Y: something chance can hardly explain. In the baseline table: a strong predictor of this kind of outcome, or the outcome's own starting value, is unequal enough between groups to sway the result; many more rows differ than chance would give; or nearly every row matches too closely. In the flow diagram: group sizes far from the planned split.
PY: there are signs of this kind, but they are not clear-cut.
PN: nothing stands out, but the baseline table is short or leaves out characteristics that matter for this outcome.
N: the groups look alike at the start, or differ only as much as random allocation would be expected to make them. One or two rows with a p-value under 0.05 in a long table are what chance alone produces.
NI: the paper gives no baseline characteristics for each randomised group."##;

/// Question 2.1's wording.
pub const Q_2_1: &str = r##"Did the participants know which intervention they were getting while the trial ran?
Look in: the methods on blinding and on what each group received (its look, taste, route and schedule), and the results on adverse effects.
Y: the trial was open-label, or the interventions could not be disguised, for example surgery against a drug, or a talking therapy against a waiting list.
PY: the paper says participants were blinded, but something would likely have given the group away to many of them, such as an effect felt only on the active treatment or a dosing schedule that differed between groups.
PN: the paper says participants were blinded and reports nothing that would have broken it, but gives little detail.
N: a matching dummy made the groups impossible to tell apart, and nothing reported would have given the group away.
NI: the paper does not say whether participants knew."##;

/// Question 2.2's wording.
pub const Q_2_2: &str = r##"Did the staff treating the participants or delivering the interventions know each participant's group while the trial ran?
Look in: the methods on blinding of clinicians, nurses, therapists or anyone else giving care, and on whether the interventions or their effects looked different.
Y: the trial was open-label for staff, or the staff had to give interventions that differ visibly.
PY: staff blinding is claimed, but something likely showed them the group, for example a treatment that shifts routine test results in a way staff would notice, or staff who also enrolled participants and could see the allocation.
PN: staff are said to be blinded and nothing reported would have broken it, but the paper gives little detail.
N: staff could not tell the interventions apart and nothing reported would have shown them a participant's group.
NI: the paper does not say whether staff knew."##;

/// Question 2.3's wording.
pub const Q_2_3: &str = r##"Did anyone depart from a group's planned intervention because of being in the trial, in a way routine care would not see?
Applies only when: the participants or the staff knew the group, probably knew it, or the paper does not say whether they did. If both participants and staff were, or probably were, unaware, answer NA.
Look in: the results and discussion for crossover, added treatments or extra attention, and for why they happened.
Y: the paper reports departures driven by knowing the group, for example staff giving one group extra care or tests, or participants adding treatments on their own because they knew their group.
PY: the paper does not say so outright, but what it reports makes such departures likely.
PN: the paper's account of the care given leaves little room for such departures, without ruling them out in so many words.
N: no departures happened; or the only ones are the sort routine care also sees, such as people stopping a treatment or skipping doses; or the protocol allowed them.
NI: the paper does not report whether anyone received something other than their group's intervention."##;

/// Question 2.4's wording.
pub const Q_2_4: &str = r##"Could those trial-driven departures have changed the result for this outcome?
Applies only when: departures from a group's planned intervention happened, or probably happened, because of being in the trial and not as routine care would have it. If there were none, probably none, or the paper does not say, answer NA.
Look in: how many participants were affected in each group, and whether the extra or different care acts on this outcome.
Y: the departures were frequent or large, and act on this outcome, so the result would plausibly differ without them.
PY: an effect on this outcome is likely, though not certain.
PN: an effect on this outcome is unlikely, though it cannot be excluded.
N: the departures could not plausibly reach this outcome, for example because the extra care does nothing for it.
NI: the paper does not describe the departures well enough to judge."##;

/// Question 2.5's wording.
pub const Q_2_5: &str = r##"Were those trial-driven departures spread evenly across the groups?
Applies only when: such departures happened, or probably happened, and they changed, probably changed, or may have changed the result for this outcome (the paper leaving that open counts). Otherwise answer NA.
Look in: the counts of each kind of departure, group by group.
Y: each kind of departure happened about as often in every group.
PY: the counts reported point to an even spread without showing it fully.
PN: the counts reported point to more, or different, departures in one group.
N: one group clearly had more of them, or a different kind.
NI: the paper does not give the departures group by group."##;

/// Question 2.6's wording.
pub const Q_2_6: &str = r##"Does this outcome's analysis compare the groups as randomised, so that it estimates the effect of being assigned to an intervention?
Look in: the statistical methods for the analysis population (intention-to-treat, full analysis set, per-protocol and so on), and the flow diagram for who was analysed in each group.
Check two things. First: is everyone counted in the group they were randomised to, whatever they actually received? Second: was anyone left out for a reason other than having no result for this outcome? Leaving out people found after randomisation never to have been eligible is acceptable when that finding could not depend on their group.
Y: both checks pass, as in an intention-to-treat analysis.
PY: the paper's description points to such an analysis but does not state it fully.
PN: the paper's description points to an analysis by treatment received, or one limited to participants who followed the protocol, without stating it fully.
N: either check fails, for example participants were counted under the treatment they actually got, or only those who completed their assigned intervention were analysed.
NI: the paper does not say who was analysed for this outcome, or under which group."##;

/// Question 2.7's wording.
pub const Q_2_7: &str = r##"Could the way participants were left out of, or moved between groups in, this outcome's analysis have changed the result materially?
Applies only when: this outcome was not, or probably was not, analysed with everyone in their randomised group, or the paper does not say how it was analysed. If it was, or probably was, answer NA.
Look in: the flow diagram and the results tables, for how many were left out or moved in each group. Set that beside the number of events, or beside the size of the difference between the groups.
Ask: if those participants were put back in their randomised groups, could the difference between the groups change materially? Even a few can matter when there are few events, or when those participants were in better or worse health than the rest.
Y: yes, the change could be material.
PY: the numbers suggest a material change is likely.
PN: the numbers suggest any change would be small.
N: so few were affected that the result could not change materially.
NI: the paper does not report how many were left out or moved."##;

/// Question 3.1's wording.
pub const Q_3_1: &str = r##"For this outcome, is a result missing for none, or only a handful, of the randomised participants?
Look in: the flow diagram and the results tables, for the number randomised in each group and the number whose result for this outcome is in the analysis. A value the authors filled in themselves (imputed) counts as missing; so does a participant who withdrew before the end of follow-up in a time-to-event analysis.
To decide whether it is only a handful, picture the missing participants doing as badly as is believable in one group and as well as is believable in the other. If even that could not change what the result means, it is a handful. For a count of events, set the number missing beside the number of events: when the two are of similar size, it is not a handful.
Y: no result is missing, or only a handful by this test.
PY: the numbers given suggest only a handful are missing, without showing it exactly.
PN: the numbers given suggest more than a handful are missing.
N: more than a handful are missing.
NI: the paper does not say how many participants have a result for this outcome."##;

/// Question 3.2's wording.
pub const Q_3_2: &str = r##"Does the paper show that the missing results for this outcome did not distort it?
Applies only when: more than a handful of randomised participants have, or probably have, no result for this outcome, or the paper does not say how many. If results are, or probably are, complete or missing for only a handful, answer NA.
Look in: the statistical methods and any supplement, for how missing results were handled and for sensitivity or tipping-point analyses.
Y: the authors re-ran the analysis under several believable guesses about how the missing participants fared, including guesses where those who left did worse or better than those who stayed, and the result held; or they used an analysis built to adjust for the missing results, not merely to fill the gaps.
PY: such checks are reported, but only in part, for example the paper says the result held without showing the numbers.
PN: the checks give little comfort, for example one alternative analysis resting on a single guess.
N: the paper reports no such checks. Filling the gaps (with the last value seen, or a group mean) and analysing as if complete is not a check.
NI: not allowed for this question. If the paper is silent, answer N."##;

/// Question 3.3's wording.
pub const Q_3_3: &str = r##"Could a participant's chance of having no result for this outcome be tied to what that result would have been?
Answer this question in every case; do not answer NA. When the paper does not say how many results are missing, judge from whatever it says about why participants dropped out or were not measured, and answer NI if it says nothing about that either.
Look in: the flow diagram and the text on withdrawals, losses to follow-up and missed visits, for the reasons given. In a time-to-event analysis, a participant who left follow-up early counts as missing.
Y: the reasons are, or could well be, tied to how participants were doing, for example they stopped coming because they got worse, got better, or had a side effect.
PY: reasons are given for some participants, and some of those could be tied to how they were doing.
PN: reasons are given for some participants, and none of those is tied to how they were doing.
N: every missing result has a stated reason that cannot be tied to how the participant was doing, for example a blood sample spoiled in the laboratory or a data file lost.
NI: the paper gives no reasons for the missing results."##;

/// Question 3.4's wording.
pub const Q_3_4: &str = r##"Is there a concrete sign, beyond mere possibility, that whether results went missing depended on what they would have been?
Applies only when: the missing results could, probably could, or might be tied to what they would have been, including when the paper gives too little to tell. If the reasons for the missing results have, or probably have, nothing to do with the outcome, answer NA.
Look for signs in three places:
- the flow diagram: do the groups lose different shares of participants, or lose them for different reasons?
- the stated reasons: do they point to the outcome itself, for example "withdrew because symptoms worsened"?
- the condition and the follow-up: do people with this condition who are doing badly tend to stop attending, or, in a time-to-event analysis, did follow-up end when participants came off or changed their assigned treatment?
Y: at least one such sign is present.
PY: the signs lean that way, without being clear.
PN: the signs lean the other way.
N: none of these signs is present, or the analysis already adjusted for what links dropping out to the outcome (for example severity at the start).
NI: the paper gives too little to look for these signs."##;

/// Question 4.1's wording.
pub const Q_4_1: &str = r##"Was this outcome measured with a method that is unfit for it?
Judge the measuring only, not whether the outcome itself was a wise choice.
Look in: the methods for the instrument, test, scale or definition and any reference to its validation, and the results for scores bunched at the top or bottom of the scale.
Y: the method could not register a change of the size the intervention might plausibly cause (for example most participants already at the top of the scale at the start), or it is known not to measure what it claims to.
PY: the description suggests a poor fit, without being clear.
PN: the method looks reasonable, though the paper says little about it.
N: the method is an accepted or validated way to measure this outcome.
NI: the paper does not say how this outcome was measured."##;

/// Question 4.2's wording.
pub const Q_4_2: &str = r##"Could this outcome have been measured, or its events found, differently in one group than in another?
Look in: the methods and the schedule of visits. Was each group assessed with the same instrument, the same cut-off and at the same times? Did one group see staff or get tested more often, so that more of its events would come to light?
Y: the groups were assessed differently in any of these ways, for example one group had monthly scans and the other only a final one.
PY: the description suggests such a difference without stating it.
PN: the description suggests the same approach in every group without stating it.
N: every group was assessed the same way, on the same schedule, with similar contact.
NI: the paper does not say how or when this outcome was assessed in each group."##;

/// Question 4.3's wording.
pub const Q_4_3: &str = r##"Did whoever produced this outcome's values know which intervention each participant had received?
Applies only when: the method of measuring this outcome was fit for it and the same in every group, or the paper does not say. If the method was, or probably was, unfit, or the outcome was, or probably was, measured differently between the groups, answer NA.
Who counts: the person whose report or judgement creates the value. For a questionnaire or diary that participants fill in, that is the participant; for a scan, a slide or an examination, it is the person reading or examining.
Y: they knew, for example participants in an open-label trial filling in their own questionnaire, or an unblinded investigator doing the examination.
PY: the description suggests they knew, without stating it.
PN: the description suggests they were unaware, without full detail.
N: they were kept unaware of each participant's intervention.
NI: the paper does not say whether they knew."##;

/// Question 4.4's wording.
pub const Q_4_4: &str = r##"Could knowing the intervention have swayed the value recorded for this outcome?
Applies only when: whoever produced the values knew the intervention, probably knew it, or the paper does not say. If they were, or probably were, unaware, answer NA.
Look at: how the value comes about. Ask whether a person has to decide or interpret something for the value to exist.
Y: someone has to decide or interpret: the care team choosing to act (for example to operate, or to start a rescue treatment), a clinician grading severity or reading an image, or a participant rating how they feel.
PY: there is some room for interpretation.
PN: there is very little room for interpretation.
N: no one has to interpret anything, for example a laboratory value produced by an automated analyser.
NI: the paper does not describe the outcome well enough to judge."##;

/// Question 4.5's wording.
pub const Q_4_5: &str = r##"Is there a concrete reason, beyond mere possibility, to think that knowing the intervention did sway this outcome's values?
Applies only when: whoever produced the values knew, probably knew, or may have known the intervention (the paper not saying counts), and the values rest on judgement that this knowledge could, probably could, or might sway. Otherwise answer NA.
Look for: anything giving those people a stake in, or firm expectations about, the result, for example authors who devised the treatment doing the assessing, or participants recruited with the message that the new treatment works.
Y: such a stake or expectation is clear.
PY: it is likely but not documented.
PN: it is possible but nothing points to it.
N: there is no particular reason to expect the assessment was swayed.
NI: the paper gives too little to judge."##;

/// Question 5.1's wording.
pub const Q_5_1: &str = r##"Did this result come from an analysis that was planned, and written down, before anyone could see the outcome data split by group?
Look in: the trial registration, the protocol or statistical analysis plan the paper cites, and the dates it gives for them. Compare their outcome measure and analysis with the ones reported.
Y: such a plan exists, the reported measure and analysis follow it, and any change was made before the data were seen or for a stated reason that has nothing to do with the results.
PY: such a plan exists and the report seems to follow it, but the paper does not allow a full comparison.
PN: the report seems to depart from the plan, or the plan seems to have been settled late.
N: the report departs from the plan without a reason unrelated to the results, or the plan was written or changed after the outcome data were seen.
NI: the paper names no plan settled in advance, or gives too little of it to compare."##;

/// Question 5.2's wording.
pub const Q_5_2: &str = r##"Was the result reported for this outcome chosen, because of what it showed, from among several measurements of it?
What counts as several measurements: the paper, or the registration or protocol it cites, shows this outcome measured in more than one way: on more than one scale or questionnaire, under more than one definition, or at more than one time point. A measurement of a different outcome does not count.
Look in: the outcome list in the registration or protocol and in the methods, then the results. Which of those measurements have their results given in full?
Y: you can point to a measurement that the plan or the methods name whose result is missing or only summarised, while the one given in full is the more favourable, and the paper does not explain the choice. A primary outcome swapped for the registered one counts here.
PY: some measurements named in the methods have no results, but it is not clear the results guided which were shown.
PN: a plan exists but says little about how this outcome would be measured, and what is reported fits it.
N: every measurement the plan or the paper shows has its result given; or there was no choice to make, because the outcome has a single measure and time point; or the authors explain any gap with a reason unrelated to the results.
NI: the paper does not say what was planned (no registration, protocol or analysis plan is cited or described), and this outcome is of a kind commonly measured in several ways."##;

/// Question 5.3's wording.
pub const Q_5_3: &str = r##"Was the result reported for this outcome chosen, because of what it showed, from among several analyses of the same measurement?
What counts as several analyses: the paper, or the plan it cites, shows the same measurement analysed in more than one way, for example with or without outliers, with a different threshold for calling a participant a responder, or with a different set of adjustment variables.
Look in: the analysis the plan describes, the analyses the methods mention, and the ones the results show. Note any analysis mentioned but not shown, and any result labelled post hoc.
Y: you can point to an analysis that was planned or mentioned whose result is missing or played down, while the one given prominence is the more favourable, and the paper does not explain the choice.
PY: the reported analysis differs from the planned one with no reason given, but it is not clear the results guided the choice.
PN: a plan exists but says little about how this outcome would be analysed, and what is reported fits it.
N: the reported analysis is the planned one; or the measurement left no real choice of analysis; or the authors explain any difference with a reason unrelated to the results.
NI: the paper does not say what analysis was planned, and this measurement is of a kind that can reasonably be analysed in several ways."##;

/// The wording of RoB 2 question `id` (such as `"2.3"`).
///
/// # Panics
///
/// When `id` is not one of the 22 ids of
/// [`questions`](crate::quality::rob2_algorithm::questions): a defect of the
/// caller, since the ids are bmlib's own (Python raises `KeyError`).
#[must_use]
pub fn question_text(id: &str) -> &'static str {
    match id {
        "1.1" => Q_1_1,
        "1.2" => Q_1_2,
        "1.3" => Q_1_3,
        "2.1" => Q_2_1,
        "2.2" => Q_2_2,
        "2.3" => Q_2_3,
        "2.4" => Q_2_4,
        "2.5" => Q_2_5,
        "2.6" => Q_2_6,
        "2.7" => Q_2_7,
        "3.1" => Q_3_1,
        "3.2" => Q_3_2,
        "3.3" => Q_3_3,
        "3.4" => Q_3_4,
        "4.1" => Q_4_1,
        "4.2" => Q_4_2,
        "4.3" => Q_4_3,
        "4.4" => Q_4_4,
        "4.5" => Q_4_5,
        "5.1" => Q_5_1,
        "5.2" => Q_5_2,
        "5.3" => Q_5_3,
        _ => panic!("{id} is not a RoB 2 question"),
    }
}

/// The heading that names `domain` in a prompt.
#[must_use]
pub fn domain_title(domain: Domain) -> &'static str {
    match domain {
        Domain::D1 => {
            r##"Domain D1: bias arising from the randomisation process (judged once for the trial)"##
        }
        Domain::D2 => {
            r##"Domain D2: bias due to deviations from the intended interventions (the effect of assignment to the interventions)"##
        }
        Domain::D3 => r##"Domain D3: bias due to missing outcome data"##,
        Domain::D4 => r##"Domain D4: bias in measurement of the outcome"##,
        Domain::D5 => r##"Domain D5: bias in selection of the reported result"##,
    }
}

/// What a condensation gathers for each domain when the paper does not fit the
/// window, in domain order.
pub const ROB2_CONDENSE_QUERIES: [(Domain, &str); 5] = [
    (
        Domain::D1,
        r##"Everything about how participants were allocated to the groups: how the random sequence was generated, how the allocation was kept hidden from those enrolling participants, the planned and actual group sizes, and the participants' baseline characteristics by group."##,
    ),
    (
        Domain::D2,
        r##"Everything about whether participants and the people delivering the interventions knew the assigned group (blinding, placebo or sham, open-label design, side effects that reveal the group), departures from the assigned interventions and their reasons, adherence, crossover or contamination between groups, and how participants were grouped and counted in the analysis (intention-to-treat, modified intention-to-treat, per-protocol or as-treated, exclusions after randomisation)."##,
    ),
    (
        Domain::D3,
        r##"Everything about missing outcome data: how many participants were randomised and how many were analysed for each outcome, by group; losses to follow-up, withdrawals and censoring with their reasons; and how missing data were handled (imputation, sensitivity analyses)."##,
    ),
    (
        Domain::D4,
        r##"Everything about how the outcomes were measured: the methods, instruments, definitions, thresholds and timing in each group, who assessed the outcomes, and whether the assessors or the participants knew the assigned intervention."##,
    ),
    (
        Domain::D5,
        r##"Everything about the plan fixed in advance and the reported results: the trial registration, protocol or statistical analysis plan and when it was finalised, the registered or pre-specified primary and secondary outcomes, changes to outcomes or analyses, and every way each outcome was measured, analysed and reported."##,
    ),
];

/// The condensation query of `domain` (its entry in [`ROB2_CONDENSE_QUERIES`]).
#[must_use]
pub fn condense_query(domain: Domain) -> &'static str {
    let index = match domain {
        Domain::D1 => 0,
        Domain::D2 => 1,
        Domain::D3 => 2,
        Domain::D4 => 3,
        Domain::D5 => 4,
    };
    ROB2_CONDENSE_QUERIES[index].1
}

/// Added to the D5 prompt: outcome switching is evidence for D5 (spec §4.3).
pub const D5_OUTCOME_SWITCHING: &str = r##"Compare the primary outcome this paper reports with the registered or pre-specified primary outcome the paper states (in its protocol, trial registration or statistical analysis plan). If they differ, say so in your reasoning and quote both statements."##;

/// The escalation question: whether several domains with some concerns
/// together warrant an overall judgement of high risk of bias. The outcome and
/// the domain answers follow it.
pub const ESCALATION_PROMPT: &str = r##"Every domain of this result's risk-of-bias assessment has now been judged. None was judged at high risk of bias, but two or more raised some concerns. In that situation the assessor decides whether the concerns, taken together, are serious enough to rate the result at high risk of bias overall, or whether "some concerns" remains the right overall rating.

Weigh the domain judgements and answers below: how serious each concern is, whether several of them could push the result in the same direction, and whether together they leave real doubt that the result reflects the effect of being assigned to the interventions. Minor concerns, or concerns that would push the result in opposite directions, weigh less.

escalate: true to rate this result at high risk of bias overall, false to keep it at some concerns.
reasoning: one or two sentences saying why.

Respond with JSON in exactly this shape:

{
    "escalate": false,
    "reasoning": "Each concern is small and they would bias the result in different directions."
}"##;

#[cfg(test)]
mod tests {
    use super::*;
    use crate::quality::rob2_algorithm::questions;

    #[test]
    fn every_question_has_its_own_self_contained_text() {
        let texts: Vec<&str> = Domain::ALL
            .iter()
            .flat_map(|d| questions(*d).iter().map(|q| question_text(q)))
            .collect();
        assert_eq!(texts.len(), 22);
        let unique: std::collections::BTreeSet<_> = texts.iter().collect();
        assert_eq!(unique.len(), 22);
        assert!(texts
            .iter()
            .all(|t| t.contains("NI") && !t.contains("previous question")));
    }

    #[test]
    fn every_question_says_what_each_answer_means_and_when_it_applies() {
        // 3.3 is asked on a branch too, but always answered (see the next test).
        let conditional = [
            "2.3", "2.4", "2.5", "2.7", "3.2", "3.4", "4.3", "4.4", "4.5",
        ];
        for question in Domain::ALL.iter().flat_map(|d| questions(*d).iter()) {
            let lines: Vec<&str> = question_text(question).split('\n').collect();
            for label in ["Y:", "PY:", "PN:", "N:", "NI:"] {
                let count = lines.iter().filter(|l| l.starts_with(label)).count();
                assert_eq!(count, 1, "{question} {label}");
            }
            let applies: Vec<&&str> = lines
                .iter()
                .filter(|l| l.starts_with("Applies only when:"))
                .collect();
            assert_eq!(
                applies.len(),
                usize::from(conditional.contains(question)),
                "{question}"
            );
            assert!(
                applies.iter().all(|l| l.contains("answer NA")),
                "{question}"
            );
        }
    }

    #[test]
    fn question_3_3_is_answered_even_when_the_amount_missing_is_unknown() {
        // Its branch can open on 3.1 NI, so the model must not steer itself to NA.
        assert!(Q_3_3.contains("do not answer NA") && Q_3_3.contains("does not say how many"));
    }

    #[test]
    #[should_panic(expected = "2.9 is not a RoB 2 question")]
    fn an_unknown_id_is_a_defect() {
        let _ = question_text("2.9");
    }

    #[test]
    fn each_domain_has_a_query_and_a_title() {
        for (index, domain) in Domain::ALL.into_iter().enumerate() {
            assert_eq!(ROB2_CONDENSE_QUERIES[index].0, domain);
            assert!(!condense_query(domain).is_empty());
        }
        let titles: std::collections::BTreeSet<_> =
            Domain::ALL.iter().map(|d| domain_title(*d)).collect();
        assert_eq!(titles.len(), 5);
        assert!(
            ESCALATION_PROMPT.contains("\"escalate\"")
                && D5_OUTCOME_SWITCHING.contains("registered")
        );
    }
}
