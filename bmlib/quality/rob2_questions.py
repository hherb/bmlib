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

The guidance is licensed CC BY-NC-ND 4.0, so none of its text is copied,
edited or paraphrased here.  Each question was written afresh from what it
asks, in bmlib's own structure and with bmlib's own examples, for a language
model reading only the paper: the question itself; when it applies (for a
question asked only on some branches; otherwise the model answers ``NA``);
what to look for in the paper; then one line each saying what counts as Y,
PY, PN, N and NI in this trial.  Every question stands alone: none refers to
another by number.

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
Is the sequence that decided each participant's group random?
What to look for: the paper's account of how the order of group assignment \
was produced, in the methods, a supplement or a cited protocol.
Y: the paper names a method with a genuine element of chance, such as a \
sequence produced by statistical software (simple, blocked or stratified), \
or minimisation, which counts as random here.
PY: the paper does not name the method, but what it does say (for example \
that a statistician independent of recruitment prepared the sequence in \
advance) makes a chance-based method very likely.
PN: the paper's account hints that assignment followed a rule someone could \
predict, without saying so.
N: assignment followed a rule other than chance: taking turns, the weekday \
or week someone attended, the ward or clinic they came through, an \
identification number, or what a clinician or the participant preferred.
NI: the paper only calls the trial randomised and says nothing about how the \
sequence was produced."""

Q_1_2 = """\
Was each participant's group kept hidden from the people recruiting \
participants until that participant had been enrolled and the assignment \
could no longer be changed?
What to look for: who held the allocation sequence, how a recruiter learned \
a new participant's group, and whether that happened only after enrolment \
was recorded.
Y: the group was released only after enrolment, by a party or system the \
recruiters could not influence, for example an automated remote service that \
registered the participant first, or allocation materials prepared outside \
the recruiting team that could not be read or reordered beforehand.
PY: the paper describes such a safeguard but leaves out a detail, for \
example it names a remote service without saying when the group was \
released.
PN: the description suggests recruiters could have seen or worked out the \
next assignment, without saying so.
N: recruiters or participants could see or predict upcoming assignments, for \
example because the sequence was kept where recruiters could read it, \
followed a visible pattern, or could be inspected before enrolment.
NI: the paper says nothing about how the assignment was kept hidden."""

Q_1_3 = """\
Judged only from the groups' characteristics at enrolment, does the \
comparison between the groups suggest that the randomisation did not work as \
it should?
What to look for: the table of baseline characteristics and the numbers \
randomised to each group. Here, set aside what the paper says about its \
randomisation method and judge from the data.
Y: the groups differ in a way chance is very unlikely to explain: group \
sizes far from the planned ratio, many more baseline differences than chance \
would give, an imbalance in a strong predictor of the outcome (or in the \
outcome's own starting value) big enough to distort the result, or groups \
that are more alike than chance would make them.
PY: the baseline data lean towards such a problem without being conclusive.
PN: the differences look like chance, but the baseline data reported are \
incomplete.
N: the groups are similar at baseline, or differ only as chance would make \
them; a few statistically significant differences in a long table are \
expected and are not a sign of a problem.
NI: the paper reports no baseline characteristics by randomised group."""

# --- Domain 2: deviations from the intended interventions (effect of assignment) ---

Q_2_1 = """\
While the trial was running, did the participants know which intervention \
they had been assigned?
What to look for: whether the participants were blinded, and whether \
anything about the interventions, how they were given or what they did could \
have told participants their group.
Y: participants knew, for example because the trial was open-label or the \
interventions were plainly different to receive (an operation compared with \
tablets, a supervised exercise class compared with usual care).
PY: blinding is claimed, but something probably revealed the group to many \
participants, such as a distinctive taste, a frequent effect that \
participants would recognise, or a different schedule of treatment.
PN: participants are described as blinded and nothing suggests the blinding \
failed, but the paper gives few details of how it was achieved.
N: participants were kept unaware by a placebo or sham they could not tell \
apart from the active intervention, and nothing reported would have revealed \
their group.
NI: the paper does not say whether participants knew their assignment."""

Q_2_2 = """\
While the trial was running, did the people looking after the participants \
or giving the interventions (doctors, nurses, therapists, family carers) \
know which intervention each participant had been assigned?
What to look for: whether these people were blinded, and whether anything \
about the interventions or their effects would have shown them a \
participant's group.
Y: they knew, for example because the trial was open-label or they had to \
give interventions that look different.
PY: their blinding is claimed but probably broke down (for example because \
one intervention changes routine measurements in a recognisable way), or the \
group was not kept hidden from them when participants were enrolled.
PN: they are described as blinded and nothing suggests the blinding failed, \
but details are thin.
N: they were kept unaware, the interventions looked the same, and nothing \
reported would have revealed a participant's group.
NI: the paper does not say whether these people knew the assignment."""

Q_2_3 = """\
Did participants receive care that departed from the trial protocol for \
their group because they were taking part in a trial, in a way that would \
not happen in ordinary practice?
When this applies: only when the participants, or the people looking after \
them or giving the interventions, knew the assignment, probably knew it, or \
may have known it because the paper does not say. If both were, or probably \
were, kept unaware, answer NA.
What to look for: reports of participants in one group getting the other \
group's treatment or extra treatments on their own initiative because of \
their assignment, or of staff giving one group extra attention, tests or \
treatments because they knew the group.
Y: the paper reports such trial-driven departures.
PY: the paper does not state it, but what it reports gives strong grounds to \
believe such departures happened.
PN: the paper describes the care given in enough detail to make such \
departures unlikely, without saying outright that there were none.
N: there were no departures, or the only ones were of a kind that also \
happens in everyday care (such as participants not taking a treatment, or \
stopping it because of an adverse effect), or ones the protocol allowed for.
NI: the paper does not report whether participants received anything other \
than their assigned intervention."""

Q_2_4 = """\
Could the trial-driven departures from the intended interventions have \
changed the result for this outcome?
When this applies: only when participants received, or probably received, \
care that departed from the protocol for their group because they were in a \
trial, beyond ordinary practice and beyond what the protocol allowed. If \
there were none, probably none, or the paper does not report any, answer NA.
What to look for: how many participants were affected, in which groups, and \
whether the different or extra care acts on this outcome.
Y: the departures were common enough, large enough, or of a kind that acts \
on this outcome, so that the result would plausibly be different without \
them.
PY: an effect on this outcome is likely but not certain.
PN: an effect on this outcome is unlikely, though it cannot be ruled out.
N: the departures could not plausibly have affected this outcome.
NI: the paper does not describe the departures well enough to judge."""

Q_2_5 = """\
Were the trial-driven departures from the intended interventions balanced \
between the groups?
When this applies: only when participants received, or probably received, \
care that departed from the protocol because they were in a trial, and those \
departures did, probably did, or may have affected this outcome. Otherwise \
answer NA.
What to look for: how often each kind of departure happened in each group.
Y: the departures were of a similar kind and happened at a similar rate in \
every group.
PY: the figures reported suggest balance without showing it fully.
PN: the figures reported suggest that one group had more of them, or \
different ones.
N: they were clearly more frequent in one group, or of a different kind in \
different groups.
NI: the paper does not report the departures separately for each group."""

Q_2_6 = """\
Was this outcome analysed in a way that estimates the effect of being \
assigned to the interventions, whatever the participants went on to receive?
What to look for: which randomised participants were counted in this \
outcome's analysis, and under which group.
Y: an intention-to-treat analysis, in which participants are counted in the \
group they were randomised to whether or not they received that \
intervention; or a modified version of it that leaves out only participants \
whose result for this outcome is missing, or participants found after \
randomisation not to meet the entry criteria when that finding could not \
have depended on their group.
PY: the description of the analysis points to such an analysis without \
stating it fully.
PN: the description points to an analysis limited to, or grouped by, the \
treatment actually received, without stating it fully.
N: a per-protocol analysis (only participants who received their assigned \
intervention as planned), an as-treated analysis (participants counted under \
the treatment they actually received), or any other analysis that drops \
eligible randomised participants for a reason other than a missing result.
NI: the paper does not say which participants were included in this \
outcome's analysis or how they were grouped."""

Q_2_7 = """\
Could leaving randomised participants out of this outcome's analysis, or \
counting them under a group other than their randomised one, have changed \
the result materially?
When this applies: only when this outcome was not, probably was not, or may \
not have been analysed with every eligible randomised participant counted in \
their randomised group, for example in a per-protocol or as-treated \
analysis, or when the paper does not say how it was analysed. If it was, or \
probably was, analysed that way, answer NA.
What to look for: how many participants were left out or moved between \
groups for this outcome, in each group, set against the number of events or \
the size of the difference between the groups.
Y: enough participants were left out or regrouped that the result could \
change materially; when the outcome is rare, or those participants' \
prospects differ from the others', even a small share can be enough.
PY: the numbers suggest a material change is likely.
PN: the numbers suggest any change would be small.
N: so few participants were affected that the result could not change \
materially.
NI: the paper does not report how many participants were left out or \
regrouped."""

# --- Domain 3: missing outcome data ---

Q_3_1 = """\
For this outcome, were results available for every randomised participant, \
or for so nearly every one that the missing results could not matter?
What to look for: the number randomised and the number with a result for \
this outcome in each group, often in a flow diagram or a results table. A \
value filled in by imputation counts as missing.
"Could not matter" means that whatever the missing participants' results \
were, they could not shift the estimated effect meaningfully. A useful \
check: if every missing participant had an extreme but plausible result, \
would the conclusion change? For an outcome counted as events, compare the \
number missing with the number of events: a number missing close to the \
number of events is not negligible.
Y: results were available for all participants, or the number missing is \
negligible in this sense.
PY: the figures suggest that a negligible amount is missing, without showing \
it exactly.
PN: the figures suggest that more than a negligible amount is missing.
N: more than a negligible number of participants have no result for this \
outcome.
NI: the paper does not report how many participants had a result for this \
outcome."""

Q_3_2 = """\
Does the paper give evidence that the missing results for this outcome did \
not bias it?
When this applies: only when more than a negligible number of randomised \
participants have, or probably have, no result for this outcome, or the \
paper does not report how many. If results are, or probably are, complete or \
nearly complete, answer NA.
What to look for: an analysis method built to correct for the bias that \
missing results can cause, or sensitivity analyses that try a range of \
plausible values for the missing participants and show the result holding.
Y: the paper reports such an analysis, or such sensitivity analyses, and the \
result barely changes.
PY: the paper reports evidence of this kind, but incompletely.
PN: the paper's handling of the missing results gives little reassurance, \
for example a single sensitivity analysis under one assumption.
N: the paper gives no such evidence. Filling in the missing values, for \
example by carrying a participant's last value forward or giving them their \
group's average, is not such evidence on its own.
NI: not allowed for this question. When the paper offers no evidence either \
way, answer N."""

Q_3_3 = """\
Could whether a participant's result for this outcome is missing depend on \
what that result would have been?
When this applies: only when more than a negligible number of participants \
have no result for this outcome and the paper gives no convincing evidence \
(none, or only weak evidence) that this did not bias the result. Otherwise \
answer NA.
What to look for: why participants dropped out, withdrew, were lost to \
follow-up or were not measured. In an analysis of the time until an event, \
participants whose follow-up was cut short (censored) count as having a \
missing result.
Y: the reasons are, or plausibly could be, connected to the participants' \
health or to this outcome, for example people leaving because they felt \
worse, felt better, or had an adverse effect.
PY: the reasons are partly reported and some of them may be connected to the \
outcome.
PN: the reasons are partly reported and those given are unrelated to the \
outcome.
N: every missing result has a documented reason that has nothing to do with \
the outcome, for example a sample lost in transit or an appointment the \
study site cancelled.
NI: the paper gives no reasons for the missing results."""

Q_3_4 = """\
Is it likely, and not just conceivable, that whether a result for this \
outcome is missing depended on what that result would have been?
When this applies: only when the missing results could, probably could, or \
may depend on their true values because the paper gives too little to rule \
that out. If the missing results have, or probably have, reasons unrelated \
to the outcome, answer NA.
What to look for: whether the share of missing results differs between the \
groups, whether the stated reasons point to the outcome, whether the reasons \
differ between the groups, and whether the clinical situation makes such a \
link expected.
Y: there is a concrete sign of the link, for example more missing results in \
one group, reasons that point to the outcome or differ between the groups, a \
condition in which getting worse itself stops people attending follow-up \
(such as a relapse during treatment for addiction), or, in an analysis of \
the time until an event, follow-up that was cut short when participants \
stopped or switched their assigned treatment.
PY: the signs lean towards such a link.
PN: the signs lean against such a link.
N: such a link is unlikely, for example because the missing results are \
similar in number and reasons across the groups and nothing about the \
condition suggests a link, or because the analysis adjusted for participant \
characteristics that explain how missingness relates to the outcome.
NI: the paper does not give enough information to judge."""

# --- Domain 4: measurement of the outcome ---

Q_4_1 = """\
Was the method used to measure this outcome unsuitable for it?
This is about the measurement only, not about whether the outcome was a good \
choice.
What to look for: the instrument, test, scale or definition used, how well \
it is known to perform, and whether it can register a change of the size the \
intervention could cause.
Y: the method could not pick up a plausible effect of the intervention (for \
example a scale on which most participants already score the maximum at the \
start), or it is known to perform poorly for this outcome.
PY: the description suggests the method is a poor fit, without being clear.
PN: the method seems reasonable, though the paper gives little detail.
N: the method is an established or validated way of measuring this outcome.
NI: the paper does not say how this outcome was measured."""

Q_4_2 = """\
Could this outcome have been measured or picked up differently in one group \
than in another?
What to look for: whether every group was assessed with the same method, \
cut-offs and schedule, and whether one group had more contact with staff, \
tests or monitoring that would make outcome events more likely to be \
noticed.
Y: the groups were assessed with different methods, cut-offs or timing, or \
one group was seen, tested or monitored more often in a way that would \
reveal more events (for example weekly visits in one group and a single \
final visit in the other).
PY: the description suggests such a difference, without stating it.
PN: the description suggests the same approach in every group, without \
stating it.
N: the same method, cut-offs and schedule were used in every group, with \
similar contact.
NI: the paper does not say how or when this outcome was assessed in each \
group."""

Q_4_3 = """\
Did the people who assessed this outcome know which intervention each \
participant had received?
When this applies: only when the method of measuring this outcome was not \
unsuitable and the outcome was not measured differently between the groups, \
or the paper does not say. If the method was, or probably was, unsuitable, \
or the outcome was, or probably was, measured differently between the \
groups, answer NA.
Who the assessors are: whoever produced the outcome value. For an outcome \
participants report themselves (a symptom score, a questionnaire on daily \
activities), the participants are the assessors; for the reading of a scan \
or a clinical examination, the reader or the examiner is.
Y: the assessors knew, for example participants in an open-label trial \
rating their own symptoms, or investigators who knew the groups examining \
the participants.
PY: the description suggests they knew, without stating it.
PN: the description suggests they were kept unaware, without full detail.
N: the assessors were kept unaware of each participant's intervention.
NI: the paper does not say whether the assessors knew."""

Q_4_4 = """\
Could knowing which intervention a participant received have affected how \
this outcome was assessed?
When this applies: only when the people who assessed this outcome knew the \
intervention, probably knew it, or may have known it because the paper does \
not say. If they were, or probably were, kept unaware, answer NA.
What to look for: how much human judgement goes into producing the outcome \
value.
Y: the outcome rests on judgement: a participant's own rating of how they \
feel, an observer's grading or interpretation, or a decision made by the \
treating team (for example whether to discharge a patient early or to start \
a rescue treatment).
PY: the outcome involves some judgement.
PN: the outcome involves very little judgement.
N: the outcome involves no judgement, for example whether a participant is \
alive according to a national death register.
NI: the paper does not describe the outcome well enough to judge."""

Q_4_5 = """\
Is it likely, and not just conceivable, that knowing the intervention did \
affect how this outcome was assessed?
When this applies: only when the assessors knew, probably knew, or may have \
known the intervention, and the outcome involves judgement that this \
knowledge could, probably could, or may sway. Otherwise answer NA.
What to look for: reasons the assessors would expect, want or believe in a \
particular result.
Y: the assessors had a clear stake in, or a strong expectation about, the \
result, for example investigators who developed the new intervention grading \
the outcome themselves, or participants who were told when they joined that \
the new intervention was expected to work better rating their own \
improvement.
PY: such expectations are likely but not documented.
PN: such expectations are possible but nothing points to them.
N: there is no particular reason to think the assessment was swayed.
NI: the paper does not give enough information to judge."""

# --- Domain 5: selection of the reported result ---

Q_5_1 = """\
Was this result analysed according to a plan that was fixed before the \
investigators could see the outcome data by group?
What to look for: a trial registration, published protocol or statistical \
analysis plan; when it was fixed compared with when the outcome data by \
group became available; and whether the outcome measure and analysis \
reported match it.
Y: the paper points to such a plan, and the reported outcome measure and \
analysis follow it, or any changes were made before the data were seen or \
for reasons the paper gives that have nothing to do with the results.
PY: there is such a plan and the report appears to follow it, but the paper \
does not allow a full comparison.
PN: the report appears to depart from the plan, or it is doubtful that the \
plan was fixed before the data were seen.
N: the reported analysis departs from the plan without a reason unrelated to \
the results, or the plan was written or changed after the outcome data were \
seen.
NI: the paper mentions no plan fixed in advance, or gives too little detail \
to compare this result with one."""

Q_5_2 = """\
Was this result likely picked, because of what it showed, from several ways \
of measuring this outcome?
"Ways of measuring this outcome" means alternatives that would each count as \
a fair measurement of the same outcome as the paper defines it: another \
scale or questionnaire for the same thing, another definition of the event, \
or another time point. A measurement of a different outcome does not count.
What to look for: the measures and time points that the registration, \
protocol or methods name for this outcome, compared with the ones whose \
results the paper reports in full.
Y: the plan or the paper shows several such measurements, but only one or \
some are reported in full, without explanation and in a way that looks \
driven by the results. A reported primary outcome that differs from the one \
the paper says was registered or pre-specified counts here.
PY: there are signs of such selection, for example measurements named in the \
methods but missing from the results, without clear evidence that the \
results drove it.
PN: a plan exists but is vague about how this outcome would be measured, and \
what is reported is consistent with it.
N: every planned measurement is reported, or there is only one way to \
measure this outcome, or any differences from the plan have reasons \
unrelated to the results.
NI: there is no plan to compare with, and this outcome could have been \
measured in more than one way."""

Q_5_3 = """\
Was this result likely picked, because of what it showed, from several ways \
of analysing the same measurement?
"Ways of analysing" means alternatives that would each count as a fair \
analysis of this measurement, for example a different statistical model or \
set of adjustment variables, analysing the change rather than the end value, \
a different cut-off for counting a participant as a responder, or setting \
aside extreme values.
What to look for: the analysis that the plan describes for this outcome, \
compared with the analyses the paper reports, and any analyses mentioned but \
not shown.
Y: the plan or the paper shows several such analyses, but only one or some \
are reported in full, without explanation and in a way that looks driven by \
the results.
PY: there are signs of such selection, for example an analysis different \
from the planned one with no reason given, without clear evidence that the \
results drove it.
PN: a plan exists but is vague about how this outcome would be analysed, and \
what is reported is consistent with it.
N: the reported analysis matches the planned one, or there was only one \
sensible way to analyse this measurement.
NI: there is no plan to compare with, and this measurement could have been \
analysed in more than one way."""

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
Every domain of this result's risk-of-bias assessment has now been judged. \
None was judged at high risk of bias, but two or more raised some concerns. \
In that situation the assessor decides whether the concerns, taken together, \
are serious enough to rate the result at high risk of bias overall, or \
whether "some concerns" remains the right overall rating.

Weigh the domain judgements and answers below: how serious each concern is, \
whether several of them could push the result in the same direction, and \
whether together they leave real doubt that the result reflects the effect \
of being assigned to the interventions. Minor concerns, or concerns that \
would push the result in opposite directions, weigh less.

escalate: true to rate this result at high risk of bias overall, false to \
keep it at some concerns.
reasoning: one or two sentences saying why.

Respond with JSON in exactly this shape:

{
    "escalate": false,
    "reasoning": "Each concern is small and they would bias the result in \
different directions."
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
