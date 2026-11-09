# bmlib — shared library for biomedical literature tools
# Copyright (C) 2024-2026 Dr Horst Herb
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""The wording of the RoB 2 questions, as bmlib puts them to a language model.

The method is RoB 2, the Cochrane risk-of-bias tool for randomised trials
(Sterne JAC, Savović J, Page MJ, et al. RoB 2: a revised tool for assessing
risk of bias in randomised trials. BMJ 2019;366:l4898; guidance by Higgins JPT
et al., 22 August 2019), for an individually randomised parallel-group trial
and the effect of *assignment* to the interventions.  The question numbers are
RoB 2's and :mod:`bmlib.quality.rob2_algorithm` holds its decision rules.

The guidance is licensed CC BY-NC-ND 4.0, so none of its sentences are copied
or edited here.  Each question is bmlib's own plain-language wording, written
for a language model: one self-contained question per id that says what counts
as Y, PY, PN, N and NI in this trial, and, for a question asked only on some
branches, when it applies (otherwise the model answers ``NA``).  No question
refers to another by number.

Rust's ``rust/bmlib/src/quality/rob2_questions.rs`` holds the same strings;
``tests/fixtures/rob2_replies.json`` (its ``question_texts`` section) pins both.
"""

from __future__ import annotations

from bmlib.quality.rob2_algorithm import Domain

__all__ = [
    "D5_OUTCOME_SWITCHING",
    "ESCALATION_PROMPT",
    "QUESTION_TEXTS",
    "ROB2_CONDENSE_QUERIES",
    "ROB2_PROMPT_VERSION",
    "condense_query",
    "domain_title",
    "question_text",
]

#: Bumped whenever any wording in this module or the agent's prompts changes,
#: so a stored run says which wording produced it.
ROB2_PROMPT_VERSION = 1

# --- Domain 1: the randomisation process ---

Q_1_1 = """\
Was the order in which participants were allocated to the groups produced by \
a genuinely random method? Answer Y if the paper describes a random method: \
computer-generated random numbers, a table of random numbers, minimisation, \
drawing lots, tossing a coin, or shuffling cards or envelopes. Answer N if \
allocation followed a predictable or non-random rule, such as alternation, \
date of birth or admission, record numbers, the choice of clinicians or \
participants, or whichever intervention happened to be available. Answer PY \
or PN when the paper does not say outright but its details clearly point one \
way. Answer NI if the paper only says the trial was randomised without saying \
how."""

Q_1_2 = """\
Were the people enrolling participants unable to know or foresee which group \
the next participant would join until that participant had been enrolled and \
assigned? Answer Y if allocation was handled by someone independent of \
enrolment (for example a central telephone or web-based randomisation \
service, or an independent pharmacy), or by envelopes that were opaque, \
sealed and numbered in sequence, or identical numbered drug containers, \
opened only once the participant was irreversibly assigned. Answer N if the \
people enrolling, or the participants themselves, could know or predict the \
next assignment (for example from an open list, or envelopes that were not \
sealed and opaque). Answer PY or PN when the paper gives only part of this \
detail. Answer NI if the paper says nothing about how the allocation was kept \
hidden."""

Q_1_3 = """\
Do differences between the groups at the start of the trial (baseline) \
suggest that the randomisation went wrong? Judge this from the baseline data \
alone, whatever the paper says about its randomisation methods. Answer N if \
the groups look alike at baseline or differ only as much as chance would \
produce; a few differences called statistically significant are expected by \
chance. Answer Y if the group sizes differ far more than the planned \
allocation ratio allows, if there are many more baseline differences than \
chance explains, if an important prognostic factor or the baseline value of \
the outcome differs between the groups by an amount very unlikely to be \
chance and large enough to bias the result, or if the groups are more alike \
than chance would allow. Answer PY or PN when the pattern leans one way \
without being clear. Answer NI if the paper gives no usable baseline data for \
the groups as randomised."""

# --- Domain 2: deviations from the intended interventions (effect of assignment) ---

Q_2_1 = """\
During the trial, did the participants know which intervention they had been \
assigned to? Answer N if participants were kept unaware, for example by a \
placebo or sham that looked the same, and nothing gave the assignment away. \
Answer Y if the trial was open-label for participants or the interventions \
were obviously different to receive. Answer PY if side effects that \
participants would recognise as typical of one intervention were common, or \
if blinding is claimed but unlikely to have held. Answer PN when blinding of \
participants is described and probably held, without full detail. Answer NI \
if the paper does not say whether participants knew."""

Q_2_2 = """\
During the trial, did the people caring for the participants or delivering \
the interventions (clinicians, nurses, therapists, carers) know which \
intervention each participant had been assigned to? Answer N if they were \
kept unaware, for example because the interventions looked identical and \
nothing gave the assignment away. Answer Y if they knew, as in an open-label \
trial or when they delivered visibly different interventions. Answer PY if \
side effects that they would recognise as typical of one intervention were \
common, or if the allocation was not kept hidden from them at enrolment. \
Answer PN when their blinding is described and probably held, without full \
detail. Answer NI if the paper does not say whether they knew."""

Q_2_3 = """\
This question applies only if the participants or the people delivering the \
interventions knew or probably knew the assignment, or the paper does not say \
whether they knew; if both were clearly kept unaware, answer NA. Because they \
were in a trial, did participants receive something other than what the \
protocol set for their group? Examples: participants disappointed with their \
assignment seeking out the experimental treatment or another remedy, or staff \
departing from the protocol in ways that would not happen in normal care. \
Answer Y or PY only if the paper shows, or gives strong reason to believe, \
that the trial setting caused such departures from the protocol. Answer N or \
PN if there were no such departures, if the departures are of the kind that \
also happen in normal care (such as participants giving up a treatment), or \
if the protocol allowed them (such as stopping a drug for toxicity or \
treating its side effects). Answer NI if the paper does not report whether \
such departures happened."""

Q_2_4 = """\
This question applies only if participants received something other than \
the protocol's intervention for their group because of the trial setting \
(beyond what happens in normal care or what the protocol allows); if there \
were no such departures, answer NA. Were these trial-induced departures \
likely to have changed this outcome? Answer Y if they were frequent or large \
enough, or of a kind, to affect this outcome. Answer N if they could not \
plausibly have affected this outcome. Answer PY or PN when an effect is \
likely or unlikely without being certain. Answer NI if the paper does not \
give enough to judge."""

Q_2_5 = """\
This question applies only if participants received something other than \
the protocol's intervention for their group because of the trial setting, \
and these departures did or may have affected this outcome; otherwise answer \
NA. Were these departures similar in kind and frequency in the intervention \
groups? Answer Y if they were balanced between the groups. Answer N if they \
were more common in one group, or of a different kind. Answer PY or PN when \
the paper's figures lean one way. Answer NI if the paper does not report them \
by group."""

Q_2_6 = """\
Was this outcome analysed in a way that estimates the effect of being \
assigned to the interventions? Answer Y for an intention-to-treat analysis \
(every randomised participant analysed in the group they were assigned to), \
or a modified intention-to-treat analysis that leaves out only participants \
with missing outcome data, or participants found after randomisation to be \
ineligible when that finding could not have depended on their group. Answer \
N for a per-protocol analysis that leaves out participants who did not \
receive their assigned intervention, an as-treated analysis that groups \
participants by what they actually received, or any analysis that leaves out \
eligible randomised participants. Answer PY or PN when the description of the \
analysis points one way without being explicit. Answer NI if the paper does \
not say how participants were grouped and counted in the analysis."""

Q_2_7 = """\
This question applies only if this outcome was not, or may not have been, \
analysed with all eligible randomised participants in the groups they were \
assigned to (for example a per-protocol or as-treated analysis, or an \
analysis the paper does not describe); if it clearly was, answer NA. Could \
analysing participants outside the group they were randomised to, or leaving \
randomised participants out, have changed the result substantially? Answer Y \
if the number of such participants is large enough for this outcome to \
matter; even under 5% of participants can matter when the outcome is rare or \
those participants differ in prognosis. Answer N if the numbers are too small \
to change the result. Answer PY or PN when the numbers lean one way. Answer \
NI if the paper does not report how many participants were affected."""

# --- Domain 3: missing outcome data ---

Q_3_1 = """\
Were data for this outcome available for all, or nearly all, randomised \
participants? Nearly all means so few are missing that their results, \
whatever they were, could not change the estimated effect in any important \
way: for a continuous outcome, data from about 95% of participants is often \
enough; for an event outcome, compare the number missing with the number of \
events. Count imputed values as missing. Answer Y if data were available for \
all or nearly all randomised participants. Answer N if more were missing than \
that. Answer PY or PN when the figures lean one way. Answer NI only if the \
paper does not report how many participants had data for this outcome."""

Q_3_2 = """\
This question applies only if data for this outcome were missing for more \
than a negligible number of randomised participants, or the paper does not \
report how many; if data were complete or nearly complete, answer NA. Does \
the paper show that the missing data did not bias the result? Answer Y if it \
used an analysis that corrects for the bias missing data can cause, or \
sensitivity analyses showing that the result hardly changes under a range of \
plausible assumptions about the missing participants' outcomes. Answer N if \
it did neither. Filling in the missing values (for example carrying the last \
observation forward, or imputing them from the assigned group alone) does not \
show this. Answer PY or PN when such evidence is only partly reported. NI is \
not allowed for this question: when the paper offers no such evidence, answer \
N."""

Q_3_3 = """\
This question applies only if data for this outcome were missing for more \
than a negligible number of participants and the paper does not show that \
the result was unbiased by it; otherwise answer NA. Could whether a \
participant's outcome went missing have depended on what that outcome \
actually was? Answer Y if dropping out, withdrawing or being lost to \
follow-up could plausibly be linked to the participant's health or to the \
outcome itself. Answer N if every missing value has a documented reason \
unrelated to the outcome (for example a broken measuring device or a gap in \
routine data collection). Answer PY or PN when the reasons lean one way. \
Answer NI if the paper gives no reasons for the missing data. In a \
time-to-event analysis, participants censored before the end of follow-up \
count as having missing outcome data."""

Q_3_4 = """\
This question applies only if whether this outcome went missing could have \
depended on the participants' true outcome, or the paper gives too little to \
rule that out; otherwise answer NA. Is it likely, not merely possible, that \
the missingness depended on the true outcome? Answer Y if, for example, the \
proportion of missing data differs between the groups, the reported reasons \
for missing data point to the outcome itself, the reasons differ between the \
groups, the clinical setting makes such a link likely (as when people whose \
symptoms persist are the ones who leave the trial), or, in a time-to-event \
analysis, follow-up \
was censored when participants stopped or changed their assigned treatment. \
Answer N if the analysis took account of participant characteristics that \
explain the link between missingness and outcome. Answer PY or PN when the \
evidence leans one way. Answer NI if the paper does not give enough to \
judge."""

# --- Domain 4: measurement of the outcome ---

Q_4_1 = """\
Was the way this outcome was measured unsuitable for it? Do not judge \
whether the outcome itself was a sensible choice, only how it was measured. \
Answer Y if the method or instrument could not detect a plausible effect of \
the intervention (for example, important values fall outside what it can \
measure) or is known to measure this outcome poorly. Answer N if the method \
is a standard or validated way of measuring this outcome, as it usually is \
for an outcome specified in advance. Answer PY or PN when the description \
leans one way. Answer NI if the paper does not describe how the outcome was \
measured."""

Q_4_2 = """\
Could this outcome have been measured or detected differently in the \
intervention groups? Answer Y if the groups were measured with different \
methods, thresholds or timing, or if one group had more contact with health \
care (for example extra visits) that gave more chances for outcome events to \
be found. Answer N if the same method, thresholds and timing were used in all \
groups with similar contact. Answer PY or PN when the description leans one \
way. Answer NI if the paper does not say."""

Q_4_3 = """\
This question applies only if the way this outcome was measured was not \
clearly unsuitable and the outcome was not clearly measured differently \
between the groups; otherwise answer NA. Did the people assessing this \
outcome know which intervention each participant received? For an outcome \
the participants report themselves (such as pain or quality of life), the \
participants are the assessors. Answer N if the assessors were kept unaware \
of the assignment. Answer Y if they knew, as when participants in an \
open-label trial report their own outcome. Answer PY or PN when the paper's \
description leans one way. Answer NI if the paper does not say whether the \
assessors knew."""

Q_4_4 = """\
This question applies only if the people assessing this outcome knew or \
probably knew which intervention each participant received, or the paper \
does not say; if they were clearly kept unaware, answer NA. Could knowing the \
intervention have influenced how this outcome was assessed? Answer Y for an \
outcome that involves judgement: one reported by participants (such as pain), \
one recorded by an observer who has to judge, or one that is a decision by \
the treating clinicians (such as admission to hospital). Answer N for an \
outcome that involves no judgement, such as death from any cause or an \
automated laboratory value. Answer PY or PN when the outcome involves little \
judgement. Answer NI if the paper does not describe the outcome well enough \
to judge."""

Q_4_5 = """\
This question applies only if the people assessing this outcome knew, or may \
have known, the intervention and that knowledge could have influenced the \
assessment; otherwise answer NA. Is it likely, not merely possible, that \
knowing the intervention did influence the assessment? Answer Y if those \
assessing held strong beliefs about the intervention's benefit or harm (for \
example participants who believe in the treatment they chose reporting their \
own symptoms, or a therapist rating the recovery of the patients they \
treated). Answer N if there is no particular reason to think the assessment \
was swayed. Answer PY or PN when the circumstances lean one way. Answer NI if \
the paper does not give enough to judge."""

# --- Domain 5: selection of the reported result ---

Q_5_1 = """\
Was this result analysed according to an analysis plan that was fixed before \
the investigators saw the unblinded outcome data (for example a registered \
protocol or a statistical analysis plan finalised before unblinding)? Answer \
Y if the paper cites such a plan, the outcome and analysis it reports match \
it, and any changes were made before unblinding or for reasons unrelated to \
the results. Answer N if the reported analysis departs from the plan without \
a reason unrelated to the results, or the plan was written or changed after \
the outcome data were seen. Answer PY or PN when the paper's account leans \
one way. Answer NI if the paper refers to no plan fixed in advance, or gives \
too little detail to compare with one."""

Q_5_2 = """\
Is this result likely to have been chosen, because of what it showed, from \
several eligible ways of measuring this outcome (for example different \
scales, definitions or time points)? Answer Y if the paper or its \
registration shows the outcome was measured in several eligible ways but only \
one or some are fully reported, without explanation and apparently because of \
the results; this includes a reported primary outcome that differs from the \
one the paper says was registered or pre-specified. Answer N if the reported \
measurements match all those planned, if there is only one way to measure \
this outcome, or if differences between reports are explained by reasons \
unrelated to the results. Answer PY or PN when the evidence leans one way. \
Answer NI if there is no plan to compare with and the outcome could have been \
measured in more than one way."""

Q_5_3 = """\
Is this result likely to have been chosen, because of what it showed, from \
several eligible analyses of the same measurement (for example adjusted and \
unadjusted models, final values or change from baseline, different cut-points \
for turning a scale into categories, different covariates, or different ways \
of handling missing data)? Answer Y if the paper or its plan shows the \
measurement was analysed in several eligible ways but only one or some are \
fully reported, without explanation and apparently because of the results. \
Answer N if the reported analysis matches the planned one, or there was only \
one sensible way to analyse this measurement. Answer PY or PN when the \
evidence leans one way. Answer NI if there is no plan to compare with and the \
measurement could have been analysed in more than one way."""

#: Every question's wording by its RoB 2 id, in question order.
QUESTION_TEXTS: dict[str, str] = {
    "1.1": Q_1_1,
    "1.2": Q_1_2,
    "1.3": Q_1_3,
    "2.1": Q_2_1,
    "2.2": Q_2_2,
    "2.3": Q_2_3,
    "2.4": Q_2_4,
    "2.5": Q_2_5,
    "2.6": Q_2_6,
    "2.7": Q_2_7,
    "3.1": Q_3_1,
    "3.2": Q_3_2,
    "3.3": Q_3_3,
    "3.4": Q_3_4,
    "4.1": Q_4_1,
    "4.2": Q_4_2,
    "4.3": Q_4_3,
    "4.4": Q_4_4,
    "4.5": Q_4_5,
    "5.1": Q_5_1,
    "5.2": Q_5_2,
    "5.3": Q_5_3,
}

_DOMAIN_TITLES: dict[Domain, str] = {
    Domain.D1: "Domain D1: bias arising from the randomisation process (judged once for the trial)",
    Domain.D2: (
        "Domain D2: bias due to deviations from the intended interventions "
        "(the effect of assignment to the interventions)"
    ),
    Domain.D3: "Domain D3: bias due to missing outcome data",
    Domain.D4: "Domain D4: bias in measurement of the outcome",
    Domain.D5: "Domain D5: bias in selection of the reported result",
}

#: What a condensation gathers for each domain when the paper does not fit
#: the window, in domain order (Rust's ``[(Domain, &str); 5]``).
ROB2_CONDENSE_QUERIES: tuple[tuple[Domain, str], ...] = (
    (
        Domain.D1,
        "Everything about how participants were allocated to the groups: how the "
        "random sequence was generated, how the allocation was kept hidden from "
        "those enrolling participants, the planned and actual group sizes, and the "
        "participants' baseline characteristics by group.",
    ),
    (
        Domain.D2,
        "Everything about whether participants and the people delivering the "
        "interventions knew the assigned group (blinding, placebo or sham, "
        "open-label design, side effects that reveal the group), departures from "
        "the assigned interventions and their reasons, adherence, crossover or "
        "contamination between groups, and how participants were grouped and "
        "counted in the analysis (intention-to-treat, modified intention-to-treat, "
        "per-protocol or as-treated, exclusions after randomisation).",
    ),
    (
        Domain.D3,
        "Everything about missing outcome data: how many participants were "
        "randomised and how many were analysed for each outcome, by group; losses "
        "to follow-up, withdrawals and censoring with their reasons; and how "
        "missing data were handled (imputation, sensitivity analyses).",
    ),
    (
        Domain.D4,
        "Everything about how the outcomes were measured: the methods, "
        "instruments, definitions, thresholds and timing in each group, who "
        "assessed the outcomes, and whether the assessors or the participants "
        "knew the assigned intervention.",
    ),
    (
        Domain.D5,
        "Everything about the plan fixed in advance and the reported results: the "
        "trial registration, protocol or statistical analysis plan and when it was "
        "finalised, the registered or pre-specified primary and secondary "
        "outcomes, changes to outcomes or analyses, and every way each outcome was "
        "measured, analysed and reported.",
    ),
)

#: Added to the D5 prompt: outcome switching is evidence for D5 (spec §4.3).
D5_OUTCOME_SWITCHING = """\
Compare the primary outcome this paper reports with the registered or \
pre-specified primary outcome the paper states (in its protocol, trial \
registration or statistical analysis plan). If they differ, say so in your \
reasoning and quote both statements."""

#: The escalation question: whether several domains with some concerns
#: together warrant an overall judgement of high risk of bias.  The outcome
#: and the domain answers follow it.
ESCALATION_PROMPT = """\
All five domains of this risk-of-bias assessment have been judged: none is at \
high risk of bias and at least two have some concerns. RoB 2 lets the \
assessor raise the overall judgement to high risk of bias when concerns in \
several domains, taken together, substantially lower confidence in the \
result. Using the domain judgements and answers below, decide whether these \
combined concerns warrant an overall judgement of high risk of bias for this \
result.

escalate: true if the combined concerns substantially lower confidence in \
this result, false if the overall judgement should stay at some concerns.
reasoning: one or two sentences saying why.

Respond with JSON in exactly this shape:

{
    "escalate": false,
    "reasoning": "The concerns are minor and point in different directions."
}"""


def question_text(question: str) -> str:
    """The wording of RoB 2 question *question* (such as ``"2.3"``).

    Raises:
        KeyError: *question* is not one of the 22 ids.
    """
    try:
        return QUESTION_TEXTS[question]
    except KeyError:
        raise KeyError(f"{question} is not a RoB 2 question") from None


def domain_title(domain: Domain) -> str:
    """The heading that names *domain* in a prompt."""
    return _DOMAIN_TITLES[domain]


def condense_query(domain: Domain) -> str:
    """The condensation query of *domain* (its entry in :data:`ROB2_CONDENSE_QUERIES`)."""
    return dict(ROB2_CONDENSE_QUERIES)[domain]
