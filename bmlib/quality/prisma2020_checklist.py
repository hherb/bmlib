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

"""The PRISMA 2020 reporting checklist, as data for a language model.

The 42 items and sub-items of the PRISMA 2020 checklist, in checklist order,
each with the rubric a model judges a systematic review's reporting by.  The
judgement vocabulary the guidance uses is ``reported``, ``partly_reported``,
``not_reported``, ``reported_elsewhere`` and ``not_applicable``.

**Source and licence.**  Each item's :attr:`ChecklistItem.wording` is copied
verbatim from the PRISMA 2020 checklist: Page MJ, McKenzie JE, Bossuyt PM, et
al. The PRISMA 2020 statement: an updated guideline for reporting systematic
reviews. *BMJ* 2021;372:n71 (doi:10.1136/bmj.n71), in the form the PRISMA
Executive distributes at https://www.prisma-statement.org/prisma-2020-checklist,
under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/).  The names,
guidance, condensation hints and reply estimates are bmlib's own; the guidance
is written from the PRISMA 2020 explanation and elaboration (Page MJ et al.,
*BMJ* 2021;372:n160).

Rust's ``rust/bmlib/src/quality/prisma2020_checklist.rs`` holds the same table;
``tests/fixtures/prisma2020_checklist.json`` pins both.  Nothing here reads the
fixture: it is the test, not the data.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "CHECKLIST",
    "KEY_ITEMS",
    "ChecklistItem",
    "ChecklistSection",
    "item",
]


class ChecklistSection(StrEnum):
    """A checklist section, in the checklist's order; the value is its wire string."""

    TITLE = "title"
    ABSTRACT = "abstract"
    INTRODUCTION = "introduction"
    METHODS = "methods"
    RESULTS = "results"
    DISCUSSION = "discussion"
    OTHER_INFORMATION = "other_information"


@dataclass(frozen=True)
class ChecklistItem:
    """One checklist item or sub-item.

    Attributes:
        id: The checklist's item number, for example ``"7"`` or ``"13d"``.
        section: The section the item belongs to.
        name: A short name for display, for example ``"Search strategy"``.
        wording: The checklist's own wording, verbatim.
        guidance: The model's rubric: a line per judgement, opening with its
            name, saying what counts as it, and any note the item needs.
        condense_hint: One sentence naming what a condensation of the paper
            must keep for this item.
        key: Whether the item is a key gap, shown first; see :data:`KEY_ITEMS`.
        may_be_not_applicable: Whether the item may be judged
            ``not_applicable`` (with a located quote that shows why).  False
            items never are.
        reply_chars: An estimate, in characters, of one item's reply
            (explanation plus up to three quotes), used to split the checklist
            into parts.
    """

    id: str
    section: ChecklistSection
    name: str
    wording: str
    guidance: str
    condense_hint: str
    key: bool
    may_be_not_applicable: bool
    reply_chars: int


#: The key items, in display order: search, risk of bias, synthesis, reporting
#: bias, certainty, registration.  Exactly the rows whose ``key`` is true.
KEY_ITEMS: tuple[str, ...] = ("7", "11", "18", "13d", "20b", "14", "21", "15", "22", "24a")

# Items 11, 14, 15, 18, 21 and 22 judge a stated "not done" as not_reported, unlike the "any
# methods" items, where "none" is a full report: they are decision-critical assessments (spec §4.1).
#: Every item and sub-item of the PRISMA 2020 checklist, in checklist order.
CHECKLIST: tuple[ChecklistItem, ...] = (
    ChecklistItem(
        id="1",
        section=ChecklistSection.TITLE,
        name="Title",
        wording="Identify the report as a systematic review.",
        guidance=(
            "reported: the title identifies the report as a systematic review, for "
            'example "a systematic review" or "systematic review and meta-analysis".\n'
            "partly_reported: the title names only a meta-analysis, or uses a looser term"
            ' such as "review" or "overview", while the paper is a systematic review.\n'
            "not_reported: the title does not identify the report as a systematic review."
            " A statement elsewhere in the paper does not count for this item."
        ),
        condense_hint="The paper's title, word for word.",
        key=False,
        may_be_not_applicable=False,
        reply_chars=900,
    ),
    ChecklistItem(
        id="2",
        section=ChecklistSection.ABSTRACT,
        name="Abstract",
        wording="See the PRISMA 2020 for Abstracts checklist.",
        guidance=(
            "Judge the abstract against the twelve elements of PRISMA 2020 for Abstracts:"
            " (1) identifies the report as a systematic review; (2) the main objective(s)"
            " or question(s); (3) inclusion and exclusion criteria; (4) information "
            "sources and the date each was last searched; (5) methods to assess risk of "
            "bias; (6) methods to present and synthesise results; (7) number of included "
            "studies and participants, with their relevant characteristics; (8) results "
            "for the main outcomes, with the number of studies and participants for each,"
            " the summary estimate and its confidence or credible interval if "
            "meta-analysis was done, and the direction of effect if groups are compared; "
            "(9) limitations of the evidence; (10) a general interpretation and important"
            " implications; (11) the primary source of funding; (12) the register name "
            "and registration number.\n"
            "reported: the abstract covers all twelve elements.\n"
            "partly_reported: it covers some of them; name each element that is missing.\n"
            "not_reported: the paper has no abstract."
        ),
        condense_hint="The abstract in full, every heading and sentence.",
        key=False,
        may_be_not_applicable=False,
        reply_chars=1200,
    ),
    ChecklistItem(
        id="3",
        section=ChecklistSection.INTRODUCTION,
        name="Rationale",
        wording="Describe the rationale for the review in the context of existing knowledge.",
        guidance=(
            "reported: the paper says what is already known on the question, including "
            "any earlier reviews of it, and why this review is needed now, for example "
            "uncertainty, new studies, or gaps in earlier reviews.\n"
            "partly_reported: background on the condition or intervention without saying "
            "why a review is needed, or a stated need with no account of existing "
            "knowledge.\n"
            "not_reported: no rationale for the review."
        ),
        condense_hint=(
            "What the paper says is already known, earlier reviews, and why this review is needed."
        ),
        key=False,
        may_be_not_applicable=False,
        reply_chars=900,
    ),
    ChecklistItem(
        id="4",
        section=ChecklistSection.INTRODUCTION,
        name="Objectives",
        wording=(
            "Provide an explicit statement of the objective(s) or question(s) the review addresses."
        ),
        guidance=(
            "reported: an explicit statement of each objective or question, with its "
            "elements named, for example the population, intervention, comparator and "
            "outcomes of an intervention review.\n"
            "partly_reported: an aim that names the topic but not what the review "
            'compares or measures, for example "to review the literature on X".\n'
            "not_reported: no stated objective or question."
        ),
        condense_hint=(
            "Every stated aim, objective or review question, with its population, "
            "intervention, comparator and outcomes."
        ),
        key=False,
        may_be_not_applicable=False,
        reply_chars=900,
    ),
    ChecklistItem(
        id="5",
        section=ChecklistSection.METHODS,
        name="Eligibility criteria",
        wording=(
            "Specify the inclusion and exclusion criteria for the review and how studies "
            "were grouped for the syntheses."
        ),
        guidance=(
            "reported: inclusion and exclusion criteria for the studies (for example "
            "design, population, interventions, comparators, outcomes, setting) and for "
            "the reports (for example years, language, publication status), and, where "
            "the review synthesises results, how studies were grouped for each synthesis.\n"
            "partly_reported: criteria for only some of these, or criteria without how "
            "studies were grouped for the syntheses.\n"
            "not_reported: no eligibility criteria."
        ),
        condense_hint=(
            "Every inclusion and exclusion criterion, and how studies were grouped for "
            "the syntheses."
        ),
        key=False,
        may_be_not_applicable=False,
        reply_chars=900,
    ),
    ChecklistItem(
        id="6",
        section=ChecklistSection.METHODS,
        name="Information sources",
        wording=(
            "Specify all databases, registers, websites, organisations, reference lists "
            "and other sources searched or consulted to identify studies. Specify the "
            "date when each source was last searched or consulted."
        ),
        guidance=(
            "reported: every database (with its platform or interface, for example Ovid),"
            " register, website, organisation, reference list and other source searched "
            "or consulted, each with the date it was last searched or consulted. One date"
            " counts for all sources only if the paper plainly applies it to each.\n"
            "partly_reported: sources named but dates missing for some or all, or other "
            'sources mentioned without naming them, for example "grey literature was '
            'searched".\n'
            "not_reported: no sources named."
        ),
        condense_hint=(
            "Every database, register, website, organisation, reference list and other "
            "source searched or consulted, with dates."
        ),
        key=False,
        may_be_not_applicable=False,
        reply_chars=1200,
    ),
    ChecklistItem(
        id="7",
        section=ChecklistSection.METHODS,
        name="Search strategy",
        wording=(
            "Present the full search strategies for all databases, registers and "
            "websites, including any filters and limits used."
        ),
        guidance=(
            "reported: the full search strategy, exactly as run (every term, Boolean "
            "operator, field tag, filter and limit), for every database, register and "
            "website searched, in the text.\n"
            "partly_reported: the full strategy for some sources but not all, or only the"
            " terms or concepts searched, or a strategy without its filters and limits.\n"
            "not_reported: only a statement that a search was done, with no terms.\n"
            "reported_elsewhere: the paper says where the full strategies are, outside "
            'its text, for example "full search strategies in Supplementary Appendix 1"; '
            "quote the pointer. If some strategies are in the text and the rest are "
            "pointed to, judge reported_elsewhere and say which are in the text."
        ),
        condense_hint=(
            "Every search string as printed, with its database, filters, limits and "
            "dates, and any pointer to where the full strategies are."
        ),
        key=True,
        may_be_not_applicable=False,
        reply_chars=1400,
    ),
    ChecklistItem(
        id="8",
        section=ChecklistSection.METHODS,
        name="Selection process",
        wording=(
            "Specify the methods used to decide whether a study met the inclusion "
            "criteria of the review, including how many reviewers screened each record "
            "and each report retrieved, whether they worked independently, and if "
            "applicable, details of automation tools used in the process."
        ),
        guidance=(
            "reported: how many reviewers screened each record (titles and abstracts) and"
            " each report retrieved (full texts), whether they worked independently, how "
            "disagreements were resolved, and the automation tools used, if any. Silence "
            "about automation tools is not a gap.\n"
            "partly_reported: some of this is missing, for example two reviewers named "
            "without saying whether they worked independently, or only the title and "
            "abstract stage described.\n"
            "not_reported: no account of how studies were selected."
        ),
        condense_hint=(
            "How records and full texts were screened: number of reviewers, independence,"
            " disagreements and automation tools."
        ),
        key=False,
        may_be_not_applicable=False,
        reply_chars=900,
    ),
    ChecklistItem(
        id="9",
        section=ChecklistSection.METHODS,
        name="Data collection process",
        wording=(
            "Specify the methods used to collect data from reports, including how many "
            "reviewers collected data from each report, whether they worked "
            "independently, any processes for obtaining or confirming data from study "
            "investigators, and if applicable, details of automation tools used in the "
            "process."
        ),
        guidance=(
            "reported: how many reviewers collected data from each report, whether they "
            "worked independently, whether and how data were sought or confirmed from "
            "study investigators, and the automation tools used, if any. Silence about "
            "automation tools is not a gap.\n"
            "partly_reported: some of this is missing, for example no statement on "
            "whether data were sought from investigators, or no number of reviewers.\n"
            "not_reported: no account of how data were collected."
        ),
        condense_hint=(
            "How data were extracted: number of reviewers, independence, contact with "
            "investigators and automation tools."
        ),
        key=False,
        may_be_not_applicable=False,
        reply_chars=900,
    ),
    ChecklistItem(
        id="10a",
        section=ChecklistSection.METHODS,
        name="Data items: outcomes",
        wording=(
            "List and define all outcomes for which data were sought. Specify whether all"
            " results that were compatible with each outcome domain in each study were "
            "sought (e.g. for all measures, time points, analyses), and if not, the "
            "methods used to decide which results to collect."
        ),
        guidance=(
            "reported: every outcome for which data were sought, listed and defined "
            "(measures and time points), and whether all results compatible with each "
            "outcome domain were sought; if not, how the results to collect were chosen, "
            "for example a preferred measure or time point.\n"
            "partly_reported: outcomes listed but not defined, or no statement of which "
            "results were collected when a study reported several.\n"
            "not_reported: the outcomes for which data were sought are not listed."
        ),
        condense_hint=(
            "Every outcome sought, its definition, measures and time points, and how "
            "results were chosen when a study gave several."
        ),
        key=False,
        may_be_not_applicable=False,
        reply_chars=900,
    ),
    ChecklistItem(
        id="10b",
        section=ChecklistSection.METHODS,
        name="Data items: other variables",
        wording=(
            "List and define all other variables for which data were sought (e.g. "
            "participant and intervention characteristics, funding sources). Describe any"
            " assumptions made about any missing or unclear information."
        ),
        guidance=(
            "reported: every other variable for which data were sought, listed and "
            "defined, for example participant, intervention and setting characteristics "
            "and funding sources, and the assumptions made about missing or unclear "
            "information.\n"
            'partly_reported: variables named only vaguely, for example "study '
            'characteristics were extracted", or no word on assumptions about missing or '
            "unclear information.\n"
            "not_reported: no other variables listed."
        ),
        condense_hint=(
            "Every other variable extracted, and any assumptions made about missing or "
            "unclear information."
        ),
        key=False,
        may_be_not_applicable=False,
        reply_chars=900,
    ),
    ChecklistItem(
        id="11",
        section=ChecklistSection.METHODS,
        name="Study risk of bias assessment",
        wording=(
            "Specify the methods used to assess risk of bias in the included studies, "
            "including details of the tool(s) used, how many reviewers assessed each "
            "study and whether they worked independently, and if applicable, details of "
            "automation tools used in the process."
        ),
        guidance=(
            "reported: the tool or tools used, named (for example RoB 2, ROBINS-I, "
            "Newcastle-Ottawa Scale), how many reviewers assessed each study, whether "
            "they worked independently, and the automation tools used, if any. Silence "
            "about automation tools is not a gap.\n"
            "partly_reported: the tool named but the number of reviewers or their "
            "independence missing, or a method described without naming a tool or its "
            "domains.\n"
            "not_reported: no method of assessing risk of bias, or only a statement that "
            "study quality was considered. A statement that this was not done is "
            'not_reported: open the explanation with "The paper states this was not done"'
            " and quote the statement."
        ),
        condense_hint=(
            "The risk of bias tool and its version, the number of reviewers, their "
            "independence and how disagreements were resolved."
        ),
        key=True,
        may_be_not_applicable=False,
        reply_chars=900,
    ),
    ChecklistItem(
        id="12",
        section=ChecklistSection.METHODS,
        name="Effect measures",
        wording=(
            "Specify for each outcome the effect measure(s) (e.g. risk ratio, mean "
            "difference) used in the synthesis or presentation of results."
        ),
        guidance=(
            "reported: the effect measure for each outcome, for example risk ratio, odds "
            "ratio, mean difference, standardised mean difference or hazard ratio, "
            "wherever in the paper it is stated.\n"
            "partly_reported: effect measures for some outcomes only.\n"
            "not_reported: no effect measure stated.\n"
            "not_applicable: only when the review neither synthesises nor presents any "
            "effect estimate, for example a review of descriptive or qualitative data "
            "only; quote the passage that shows it."
        ),
        condense_hint="The effect measure named for each outcome.",
        key=False,
        may_be_not_applicable=True,
        reply_chars=900,
    ),
    ChecklistItem(
        id="13a",
        section=ChecklistSection.METHODS,
        name="Synthesis methods: eligibility for each synthesis",
        wording=(
            "Describe the processes used to decide which studies were eligible for each "
            "synthesis (e.g. tabulating the study intervention characteristics and "
            "comparing against the planned groups for each synthesis (item #5))."
        ),
        guidance=(
            "reported: how the review decided which studies went into each synthesis, for"
            " example by tabulating each study's intervention characteristics and "
            "comparing them with the groups planned for each synthesis.\n"
            "partly_reported: the syntheses or groups named without how studies were "
            "assigned to them.\n"
            "not_reported: nothing on which studies were eligible for each synthesis.\n"
            "not_applicable: only when the review has no synthesis at all, statistical or"
            " not (vote counting, combining P values and a structured narrative synthesis"
            " are all syntheses); quote the passage that shows it, for example a "
            "statement that results are presented study by study only."
        ),
        condense_hint="How studies were assigned to each synthesis or comparison group.",
        key=False,
        may_be_not_applicable=True,
        reply_chars=900,
    ),
    ChecklistItem(
        id="13b",
        section=ChecklistSection.METHODS,
        name="Synthesis methods: data preparation",
        wording=(
            "Describe any methods required to prepare the data for presentation or "
            "synthesis, such as handling of missing summary statistics, or data "
            "conversions."
        ),
        guidance=(
            "reported: the methods used to prepare data for presentation or synthesis, "
            "for example imputing missing standard deviations, converting medians to "
            "means, combining groups or converting units. A plain statement that none was"
            " done or needed counts as reported.\n"
            "partly_reported: some preparation mentioned without saying how it was done.\n"
            "not_reported: nothing on data preparation.\n"
            "not_applicable: only when the review has no synthesis at all, statistical or"
            " not (vote counting, combining P values and a structured narrative synthesis"
            " are all syntheses); quote the passage that shows it, for example a "
            "statement that results are presented study by study only."
        ),
        condense_hint=(
            "How missing summary statistics were handled and how data were converted or combined."
        ),
        key=False,
        may_be_not_applicable=True,
        reply_chars=900,
    ),
    ChecklistItem(
        id="13c",
        section=ChecklistSection.METHODS,
        name="Synthesis methods: tabulation and display",
        wording=(
            "Describe any methods used to tabulate or visually display results of "
            "individual studies and syntheses."
        ),
        guidance=(
            "reported: how the results of individual studies and of the syntheses were "
            "tabulated or displayed, for example summary tables and how they were "
            "ordered, forest plots or harvest plots.\n"
            "partly_reported: displays described for individual studies only or for "
            "syntheses only.\n"
            "not_reported: nothing on how results were tabulated or displayed.\n"
            "not_applicable: only when the review has no synthesis at all, statistical or"
            " not (vote counting, combining P values and a structured narrative synthesis"
            " are all syntheses); quote the passage that shows it, for example a "
            "statement that results are presented study by study only."
        ),
        condense_hint="How results of studies and syntheses were tabulated or plotted.",
        key=False,
        may_be_not_applicable=True,
        reply_chars=900,
    ),
    ChecklistItem(
        id="13d",
        section=ChecklistSection.METHODS,
        name="Synthesis methods: synthesis method",
        wording=(
            "Describe any methods used to synthesize results and provide a rationale for "
            "the choice(s). If meta-analysis was performed, describe the model(s), "
            "method(s) to identify the presence and extent of statistical heterogeneity, "
            "and software package(s) used."
        ),
        guidance=(
            "reported: each synthesis method and the reason it was chosen. For a "
            "meta-analysis: the model (fixed or random effects, and the estimator, for "
            "example REML or DerSimonian-Laird), the methods to identify the presence and"
            " extent of statistical heterogeneity (for example I², tau², Cochran's Q) and"
            " the software. Without a meta-analysis: the method used instead, for example"
            " vote counting on the direction of effect or a structured synthesis "
            "following SWiM, and why.\n"
            "partly_reported: a method named without its rationale; a meta-analysis "
            'missing its model, its heterogeneity method or its software; or "a narrative'
            ' synthesis" with no account of how it was done.\n'
            "not_reported: no synthesis method described.\n"
            "not_applicable: only when the review has no synthesis at all, statistical or"
            " not (vote counting, combining P values and a structured narrative synthesis"
            " are all syntheses); quote the passage that shows it, for example a "
            "statement that results are presented study by study only. A review without a"
            " meta-analysis still owes this item: only its statistical parts (model, "
            "heterogeneity method, software) then do not apply."
        ),
        condense_hint=(
            "Every synthesis method with its rationale, the meta-analysis model and "
            "estimator, heterogeneity statistics and software."
        ),
        key=True,
        may_be_not_applicable=True,
        reply_chars=1400,
    ),
    ChecklistItem(
        id="13e",
        section=ChecklistSection.METHODS,
        name="Synthesis methods: heterogeneity",
        wording=(
            "Describe any methods used to explore possible causes of heterogeneity among "
            "study results (e.g. subgroup analysis, meta-regression)."
        ),
        guidance=(
            "reported: the methods used to explore causes of heterogeneity, for example "
            "subgroup analysis or meta-regression, with the variables examined and "
            "whether they were planned in advance. A plain statement that none was done "
            "or needed counts as reported.\n"
            "partly_reported: subgroup analyses or meta-regression mentioned without the "
            "variables or the method.\n"
            "not_reported: nothing on exploring heterogeneity.\n"
            "not_applicable: only when the review has no synthesis at all, statistical or"
            " not (vote counting, combining P values and a structured narrative synthesis"
            " are all syntheses); quote the passage that shows it, for example a "
            "statement that results are presented study by study only."
        ),
        condense_hint=(
            "Every planned or performed subgroup analysis or meta-regression, with its variables."
        ),
        key=False,
        may_be_not_applicable=True,
        reply_chars=900,
    ),
    ChecklistItem(
        id="13f",
        section=ChecklistSection.METHODS,
        name="Synthesis methods: sensitivity analyses",
        wording=(
            "Describe any sensitivity analyses conducted to assess robustness of the "
            "synthesized results."
        ),
        guidance=(
            "reported: each sensitivity analysis and the decision it tests, for example "
            "excluding studies at high risk of bias or using another model. A plain "
            "statement that none was done or needed counts as reported.\n"
            "partly_reported: sensitivity analyses mentioned without saying what they "
            "varied.\n"
            "not_reported: nothing on sensitivity analyses.\n"
            "not_applicable: only when the review has no synthesis at all, statistical or"
            " not (vote counting, combining P values and a structured narrative synthesis"
            " are all syntheses); quote the passage that shows it, for example a "
            "statement that results are presented study by study only."
        ),
        condense_hint="Every sensitivity analysis and what it varied.",
        key=False,
        may_be_not_applicable=True,
        reply_chars=900,
    ),
    ChecklistItem(
        id="14",
        section=ChecklistSection.METHODS,
        name="Reporting bias assessment",
        wording=(
            "Describe any methods used to assess risk of bias due to missing results in a"
            " synthesis (arising from reporting biases)."
        ),
        guidance=(
            "reported: the methods used to assess risk of bias due to missing results, "
            "for example funnel plots, Egger's test, comparing the outcomes in protocols "
            "or registrations with those published, or ROB-ME. A method must be named and"
            " applied; a reason alone is not a method.\n"
            'partly_reported: a method named only vaguely, for example "publication bias '
            'was assessed", without saying how.\n'
            'not_reported: nothing on reporting bias. A bare "not done" or "not '
            'assessed", with no reason given, is not_reported: open the explanation with '
            '"The paper states this was not done" and quote the statement.\n'
            "not_applicable: no method was applied, for a reason the paper states, for "
            "example fewer than 10 studies for a funnel plot, or a review with no "
            "synthesis at all (statistical or not); quote the reason."
        ),
        condense_hint=(
            "How publication bias and other missing results were assessed, or why they "
            "could not be."
        ),
        key=True,
        may_be_not_applicable=True,
        reply_chars=900,
    ),
    ChecklistItem(
        id="15",
        section=ChecklistSection.METHODS,
        name="Certainty assessment",
        wording=(
            "Describe any methods used to assess certainty (or confidence) in the body of"
            " evidence for an outcome."
        ),
        guidance=(
            "reported: the method used to assess certainty in the body of evidence, for "
            "example GRADE, with the domains considered and how judgements were made, and"
            " for which outcomes.\n"
            'partly_reported: a method named, for example "GRADE was used", without the '
            "domains, the outcomes or how judgements were made.\n"
            "not_reported: nothing on certainty of the evidence. A statement that this "
            'was not done is not_reported: open the explanation with "The paper states '
            'this was not done" and quote the statement.'
        ),
        condense_hint=(
            "The certainty method (for example GRADE), its domains, the outcomes it was "
            "applied to and who judged."
        ),
        key=True,
        may_be_not_applicable=False,
        reply_chars=900,
    ),
    ChecklistItem(
        id="16a",
        section=ChecklistSection.RESULTS,
        name="Study selection",
        wording=(
            "Describe the results of the search and selection process, from the number of"
            " records identified in the search to the number of studies included in the "
            "review, ideally using a flow diagram."
        ),
        guidance=(
            "reported: the numbers from the records identified, through duplicates "
            "removed, records screened, reports sought and assessed, to the studies "
            "included, with the reasons reports were excluded at full text.\n"
            "partly_reported: only some of these numbers, for example records identified "
            "and studies included with nothing between them.\n"
            "not_reported: no numbers from the search and selection.\n"
            "reported_elsewhere: the counts are given only in a flow diagram whose "
            "content is not in the text (its caption or a reference to it is); quote the "
            "caption or the reference."
        ),
        condense_hint=(
            "Every count from records identified to studies included, the exclusion "
            "reasons, and the flow diagram's caption."
        ),
        key=False,
        may_be_not_applicable=False,
        reply_chars=900,
    ),
    ChecklistItem(
        id="16b",
        section=ChecklistSection.RESULTS,
        name="Study selection: excluded studies",
        wording=(
            "Cite studies that might appear to meet the inclusion criteria, but which "
            "were excluded, and explain why they were excluded."
        ),
        guidance=(
            "reported: the studies that might appear to meet the inclusion criteria but "
            "were excluded are cited, each with the reason it was excluded.\n"
            "partly_reported: such studies counted with reasons but not cited, or cited "
            "without reasons.\n"
            "not_reported: nothing on studies that were nearly included.\n"
            "reported_elsewhere: the paper says the excluded studies are listed outside "
            'its text, for example "excluded studies with reasons in Appendix 3"; quote '
            "the pointer."
        ),
        condense_hint=(
            "Studies excluded at full text that might look eligible, with the reason for "
            "each, and any pointer to a list of them."
        ),
        key=False,
        may_be_not_applicable=False,
        reply_chars=900,
    ),
    ChecklistItem(
        id="17",
        section=ChecklistSection.RESULTS,
        name="Study characteristics",
        wording="Cite each included study and present its characteristics.",
        guidance=(
            "reported: each included study is cited and its characteristics presented, "
            "for example design, population, interventions, comparators, outcomes, "
            "setting and follow-up, typically in a table.\n"
            "partly_reported: characteristics for only some studies, or summarised across"
            " studies only, or studies not cited.\n"
            "not_reported: the included studies are neither cited nor described.\n"
            "reported_elsewhere: these results are given only in a figure, whose content "
            "is not in the text (its caption or a reference to it is); quote the caption "
            "or the reference."
        ),
        condense_hint=(
            "Each included study's citation and characteristics: design, population, "
            "interventions, comparators, outcomes, setting."
        ),
        key=False,
        may_be_not_applicable=False,
        reply_chars=900,
    ),
    ChecklistItem(
        id="18",
        section=ChecklistSection.RESULTS,
        name="Risk of bias in studies",
        wording="Present assessments of risk of bias for each included study.",
        guidance=(
            "reported: the risk of bias judgement for each included study, ideally per "
            "domain as well as overall.\n"
            "partly_reported: judgements for only some studies, or only a summary across "
            'studies, for example "most studies were at low risk of bias".\n'
            "not_reported: no risk of bias results. A statement that this was not done is"
            ' not_reported: open the explanation with "The paper states this was not '
            'done" and quote the statement.\n'
            "reported_elsewhere: these results are given only in a figure, whose content "
            "is not in the text (its caption or a reference to it is); quote the caption "
            "or the reference."
        ),
        condense_hint=(
            "The risk of bias judgement for each study and domain, and any figure caption"
            " showing them."
        ),
        key=True,
        may_be_not_applicable=False,
        reply_chars=900,
    ),
    ChecklistItem(
        id="19",
        section=ChecklistSection.RESULTS,
        name="Results of individual studies",
        wording=(
            "For all outcomes, present, for each study: (a) summary statistics for each "
            "group (where appropriate) and (b) an effect estimate and its precision (e.g."
            " confidence/credible interval), ideally using structured tables or plots."
        ),
        guidance=(
            "reported: for every outcome and every study, summary statistics for each "
            "group where appropriate, and an effect estimate with its precision, for "
            "example a confidence interval.\n"
            "partly_reported: results for some outcomes or studies only, or effect "
            "estimates without their precision, or only pooled results.\n"
            "not_reported: no results of individual studies.\n"
            "reported_elsewhere: the paper says per-study results are outside its text, "
            "for example in supplementary tables, or they are given only in a figure, "
            "whose content is not in the text (its caption or a reference to it is); "
            "quote the pointer or the caption."
        ),
        condense_hint=(
            "Each study's group summary statistics and effect estimate with its interval,"
            " per outcome, and any pointer to them."
        ),
        key=False,
        may_be_not_applicable=False,
        reply_chars=1100,
    ),
    ChecklistItem(
        id="20a",
        section=ChecklistSection.RESULTS,
        name="Results of syntheses: contributing studies",
        wording=(
            "For each synthesis, briefly summarise the characteristics and risk of bias "
            "among contributing studies."
        ),
        guidance=(
            "reported: for each synthesis, a brief summary of the characteristics and the"
            " risk of bias of the studies contributing to it.\n"
            "partly_reported: for some syntheses only, or characteristics without risk of"
            " bias (or the reverse).\n"
            "not_reported: no such summary for any synthesis.\n"
            "not_applicable: only when the review has no synthesis at all, statistical or"
            " not (vote counting, combining P values and a structured narrative synthesis"
            " are all syntheses); quote the passage that shows it, for example a "
            "statement that results are presented study by study only."
        ),
        condense_hint=(
            "For each synthesis, the characteristics and risk of bias of the studies that"
            " contribute to it."
        ),
        key=False,
        may_be_not_applicable=True,
        reply_chars=900,
    ),
    ChecklistItem(
        id="20b",
        section=ChecklistSection.RESULTS,
        name="Results of syntheses: results of each synthesis",
        wording=(
            "Present results of all statistical syntheses conducted. If meta-analysis was"
            " done, present for each the summary estimate and its precision (e.g. "
            "confidence/credible interval) and measures of statistical heterogeneity. If "
            "comparing groups, describe the direction of the effect."
        ),
        guidance=(
            "reported: the result of every synthesis conducted. For each meta-analysis: "
            "the summary estimate with its confidence or credible interval and measures "
            "of statistical heterogeneity (for example I², tau², Cochran's Q). When "
            "groups are compared, the direction of the effect. Without a meta-analysis: "
            "the result of the synthesis used instead, for example the number of studies "
            "favouring each group.\n"
            "partly_reported: results for some syntheses only, or estimates without "
            "intervals, or no heterogeneity measures, or no direction of effect.\n"
            "not_reported: no results of any synthesis.\n"
            "reported_elsewhere: these results are given only in a figure, whose content "
            "is not in the text (its caption or a reference to it is); quote the caption "
            "or the reference.\n"
            "not_applicable: only when the review has no synthesis at all, statistical or"
            " not (vote counting, combining P values and a structured narrative synthesis"
            " are all syntheses); quote the passage that shows it, for example a "
            "statement that results are presented study by study only. A review without a"
            " meta-analysis still owes this item: the results of the synthesis it did."
        ),
        condense_hint=(
            "Every pooled estimate with its interval, heterogeneity statistics, number of"
            " studies and participants, and direction of effect."
        ),
        key=True,
        may_be_not_applicable=True,
        reply_chars=1400,
    ),
    ChecklistItem(
        id="20c",
        section=ChecklistSection.RESULTS,
        name="Results of syntheses: heterogeneity",
        wording=(
            "Present results of all investigations of possible causes of heterogeneity "
            "among study results."
        ),
        guidance=(
            "reported: the results of every investigation of causes of heterogeneity, for"
            " example each subgroup's estimate and the test for subgroup differences, or "
            "meta-regression coefficients. A plain statement that none was done or needed"
            " counts as reported.\n"
            "partly_reported: results for some investigations only, or a conclusion "
            "without the results behind it.\n"
            "not_reported: nothing on investigations of heterogeneity.\n"
            "not_applicable: only when the review has no synthesis at all, statistical or"
            " not (vote counting, combining P values and a structured narrative synthesis"
            " are all syntheses); quote the passage that shows it, for example a "
            "statement that results are presented study by study only."
        ),
        condense_hint=(
            "Every subgroup and meta-regression result, with estimates and tests for differences."
        ),
        key=False,
        may_be_not_applicable=True,
        reply_chars=900,
    ),
    ChecklistItem(
        id="20d",
        section=ChecklistSection.RESULTS,
        name="Results of syntheses: sensitivity analyses",
        wording=(
            "Present results of all sensitivity analyses conducted to assess the "
            "robustness of the synthesized results."
        ),
        guidance=(
            "reported: the results of every sensitivity analysis, for example the "
            "re-estimated effect with its interval. A plain statement that none was done "
            "or needed counts as reported.\n"
            "partly_reported: results for some sensitivity analyses only, or a conclusion"
            ' such as "results were robust" without the results behind it.\n'
            "not_reported: nothing on sensitivity analysis results.\n"
            "not_applicable: only when the review has no synthesis at all, statistical or"
            " not (vote counting, combining P values and a structured narrative synthesis"
            " are all syntheses); quote the passage that shows it, for example a "
            "statement that results are presented study by study only."
        ),
        condense_hint="Every sensitivity analysis result, with its estimate and interval.",
        key=False,
        may_be_not_applicable=True,
        reply_chars=900,
    ),
    ChecklistItem(
        id="21",
        section=ChecklistSection.RESULTS,
        name="Reporting biases",
        wording=(
            "Present assessments of risk of bias due to missing results (arising from "
            "reporting biases) for each synthesis assessed."
        ),
        guidance=(
            "reported: for each synthesis assessed, the result of the assessment of risk "
            "of bias due to missing results, for example funnel plot asymmetry, an "
            "Egger's test P value, or a judgement with its reasons. A synthesis the paper"
            " says could not be assessed, for a reason it states, does not count against "
            "the item when others were assessed.\n"
            'partly_reported: for some syntheses only, or a bare conclusion such as "no '
            'evidence of publication bias" without the assessment behind it.\n'
            'not_reported: no results of a reporting bias assessment. A bare "not done" '
            'or "not assessed", with no reason given, is not_reported: open the '
            'explanation with "The paper states this was not done" and quote the '
            "statement.\n"
            "not_applicable: no synthesis was assessed, for a reason the paper states, "
            "for example fewer than 10 studies, or the review has no synthesis at all "
            "(statistical or not); quote the reason."
        ),
        condense_hint=(
            "Every funnel plot, test or judgement of missing results, per synthesis, or "
            "why one could not be made."
        ),
        key=True,
        may_be_not_applicable=True,
        reply_chars=900,
    ),
    ChecklistItem(
        id="22",
        section=ChecklistSection.RESULTS,
        name="Certainty of evidence",
        wording=(
            "Present assessments of certainty (or confidence) in the body of evidence for"
            " each outcome assessed."
        ),
        guidance=(
            "reported: the certainty rating for each outcome assessed, for example GRADE "
            "high, moderate, low or very low, with the reasons for each rating, ideally "
            "in a summary of findings table.\n"
            "partly_reported: ratings for some outcomes only, or ratings without reasons.\n"
            "not_reported: no certainty ratings. A statement that this was not done is "
            'not_reported: open the explanation with "The paper states this was not done"'
            " and quote the statement."
        ),
        condense_hint=(
            "The certainty rating for each outcome and the reasons for downgrading or upgrading it."
        ),
        key=True,
        may_be_not_applicable=False,
        reply_chars=900,
    ),
    ChecklistItem(
        id="23a",
        section=ChecklistSection.DISCUSSION,
        name="Discussion: interpretation",
        wording=(
            "Provide a general interpretation of the results in the context of other evidence."
        ),
        guidance=(
            "reported: a general interpretation of the results set against other "
            "evidence, for example earlier reviews or major studies.\n"
            "partly_reported: an interpretation with no reference to other evidence.\n"
            "not_reported: no interpretation of the results."
        ),
        condense_hint=(
            "How the paper interprets its results and how it compares them with other evidence."
        ),
        key=False,
        may_be_not_applicable=False,
        reply_chars=900,
    ),
    ChecklistItem(
        id="23b",
        section=ChecklistSection.DISCUSSION,
        name="Discussion: limitations of the evidence",
        wording="Discuss any limitations of the evidence included in the review.",
        guidance=(
            "reported: the limitations of the included evidence are discussed, for "
            "example risk of bias, inconsistency, imprecision, indirectness or few "
            "studies. Limitations of the review's own methods belong to item 23c, not "
            "here.\n"
            "partly_reported: limitations of the evidence mentioned in passing without "
            "saying what they are.\n"
            "not_reported: no limitations of the included evidence."
        ),
        condense_hint="Every limitation the paper names in the included studies' evidence.",
        key=False,
        may_be_not_applicable=False,
        reply_chars=900,
    ),
    ChecklistItem(
        id="23c",
        section=ChecklistSection.DISCUSSION,
        name="Discussion: limitations of the review process",
        wording="Discuss any limitations of the review processes used.",
        guidance=(
            "reported: the limitations of the review's own methods are discussed, for "
            "example a search limited by language or date, a single screener, or "
            "unpublished data not sought. Limitations of the included studies belong to "
            "item 23b, not here.\n"
            "partly_reported: limitations of the review mentioned in passing without "
            "saying what they are.\n"
            "not_reported: no limitations of the review process."
        ),
        condense_hint="Every limitation the paper names in its own review methods.",
        key=False,
        may_be_not_applicable=False,
        reply_chars=900,
    ),
    ChecklistItem(
        id="23d",
        section=ChecklistSection.DISCUSSION,
        name="Discussion: implications",
        wording=("Discuss implications of the results for practice, policy, and future research."),
        guidance=(
            "reported: implications for practice, for policy and for future research are "
            "all discussed.\n"
            "partly_reported: implications for only some of these; name the ones missing.\n"
            "not_reported: no implications discussed."
        ),
        condense_hint="The implications the paper draws for practice, policy and future research.",
        key=False,
        may_be_not_applicable=False,
        reply_chars=900,
    ),
    ChecklistItem(
        id="24a",
        section=ChecklistSection.OTHER_INFORMATION,
        name="Registration",
        wording=(
            "Provide registration information for the review, including register name and"
            " registration number, or state that the review was not registered."
        ),
        guidance=(
            "reported: the register name and the registration number (for example "
            "PROSPERO CRD42020123456), or a statement that the review was not registered.\n"
            "partly_reported: the review said to be registered without the number, or a "
            "number without the register.\n"
            "not_reported: nothing on registration.\n"
            "This item asks where the review is registered, so a stated register and "
            "number is the report itself: judge it reported, never reported_elsewhere, "
            "even though the registry record is not in the text."
        ),
        condense_hint=(
            "The register name and registration number, or the statement that the review "
            "was not registered."
        ),
        key=True,
        may_be_not_applicable=False,
        reply_chars=900,
    ),
    ChecklistItem(
        id="24b",
        section=ChecklistSection.OTHER_INFORMATION,
        name="Protocol",
        wording=(
            "Indicate where the review protocol can be accessed, or state that a protocol"
            " was not prepared."
        ),
        guidance=(
            "reported: where the protocol can be accessed, for example a citation of a "
            'published protocol, a link, or "protocol available at PROSPERO '
            'CRD42020123456", or a statement that no protocol was prepared.\n'
            "partly_reported: a protocol said to exist without saying where it can be "
            "accessed.\n"
            'not_reported: nothing on a protocol. "Available on request" is not a place '
            "the protocol can be accessed: judge it not_reported.\n"
            "This item asks where the protocol is, so a stated location is the report "
            "itself: judge it reported, never reported_elsewhere, even though the "
            "protocol is not in the text."
        ),
        condense_hint="Where the protocol can be found, or the statement that there was none.",
        key=False,
        may_be_not_applicable=False,
        reply_chars=900,
    ),
    ChecklistItem(
        id="24c",
        section=ChecklistSection.OTHER_INFORMATION,
        name="Amendments",
        wording=(
            "Describe and explain any amendments to information provided at registration "
            "or in the protocol."
        ),
        guidance=(
            "reported: each amendment to the registration or protocol is described and "
            "explained. A plain statement that there were no amendments counts as "
            "reported.\n"
            "partly_reported: deviations said to have occurred without describing or "
            "explaining them.\n"
            "not_reported: nothing on amendments.\n"
            "reported_elsewhere: the paper says the amendments are described outside its "
            "text, for example in the registry record or a supplement; quote the pointer.\n"
            "not_applicable: only when the paper states the review was neither registered"
            " nor had a protocol; quote that statement."
        ),
        condense_hint=(
            "Every change from the registration or protocol and its reason, or the "
            "statement that there was none."
        ),
        key=False,
        may_be_not_applicable=True,
        reply_chars=900,
    ),
    ChecklistItem(
        id="25",
        section=ChecklistSection.OTHER_INFORMATION,
        name="Support",
        wording=(
            "Describe sources of financial or non-financial support for the review, and "
            "the role of the funders or sponsors in the review."
        ),
        guidance=(
            "reported: the sources of financial and non-financial support and the role of"
            " the funders or sponsors in the review. A statement that the review had no "
            "funding counts as reported.\n"
            "partly_reported: funding named without the funders' role.\n"
            "not_reported: nothing on support."
        ),
        condense_hint="The funding statement in full: sources of support and the funders' role.",
        key=False,
        may_be_not_applicable=False,
        reply_chars=900,
    ),
    ChecklistItem(
        id="26",
        section=ChecklistSection.OTHER_INFORMATION,
        name="Competing interests",
        wording="Declare any competing interests of review authors.",
        guidance=(
            "reported: a declaration of the review authors' competing interests. A "
            "statement that there are none counts as reported.\n"
            "partly_reported: a declaration for only some authors.\n"
            "not_reported: no declaration of competing interests."
        ),
        condense_hint="The competing interests declaration in full.",
        key=False,
        may_be_not_applicable=False,
        reply_chars=900,
    ),
    ChecklistItem(
        id="27",
        section=ChecklistSection.OTHER_INFORMATION,
        name="Availability of data, code and other materials",
        wording=(
            "Report which of the following are publicly available and where they can be "
            "found: template data collection forms; data extracted from included studies;"
            " data used for all analyses; analytic code; any other materials used in the "
            "review."
        ),
        guidance=(
            "reported: for each of template data collection forms, data extracted from "
            "the included studies, data used for all analyses, analytic code and other "
            "materials, the paper says whether it is publicly available and, if so, "
            "where.\n"
            "partly_reported: only some of these covered, or materials said to be public "
            "without saying where.\n"
            "not_reported: no statement on the availability of data, code or other "
            "materials.\n"
            '"Available on request" is a statement of availability (the material is not '
            "public), which is this item's own subject: count it for the materials it "
            "names, and never as reported_elsewhere.\n"
            "This item asks where the materials are, so a stated location (a repository, "
            "a link or a supplement) is the report itself: judge it reported, never "
            "reported_elsewhere, even though the materials are not in the text."
        ),
        condense_hint=(
            "The data, code and materials availability statement in full, with every "
            "repository or supplement it names."
        ),
        key=False,
        may_be_not_applicable=False,
        reply_chars=900,
    ),
)

_BY_ID = {entry.id: entry for entry in CHECKLIST}


def item(item_id: str) -> ChecklistItem | None:
    """Return the checklist item with this id (for example ``"13d"``), or ``None``."""
    return _BY_ID.get(item_id)
