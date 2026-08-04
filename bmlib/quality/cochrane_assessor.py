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

"""Cochrane-aligned study assessment.

Produces a :class:`~bmlib.quality.cochrane_models.CochraneStudyAssessment`
— the Cochrane Handbook's study-characteristics table plus the nine-domain
Risk of Bias assessment — from a paper's title and text.

Text larger than one context is reduced to an evidence digest by
:mod:`bmlib.context_processor` first, so the nine-domain judgement is always
made once, over content that fits.  Truncation is not an option here:
allocation concealment and blinding live in Methods and attrition in Results,
so a head-of-string cut drops exactly the evidence the domains are about.

Reference: Cochrane Handbook for Systematic Reviews of Interventions
(https://training.cochrane.org/handbook).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from bmlib.agents.base import BaseAgent
from bmlib.context_processor import LLMChunkProcessor, ProcessingConfig, ProcessingStatus
from bmlib.llm import LLMClient
from bmlib.quality.cochrane_models import (
    ROB_JUDGEMENT_UNCLEAR,
    CochraneInterventions,
    CochraneNotes,
    CochraneOutcomes,
    CochraneParticipants,
    CochraneRiskOfBias,
    CochraneStudyAssessment,
    CochraneStudyCharacteristics,
    RiskOfBiasItem,
    RiskOfBiasJudgement,
)
from bmlib.templates import TemplateEngine

logger = logging.getLogger(__name__)

#: Text longer than this is condensed before assessment.  Roughly 12k tokens,
#: so a whole research paper usually passes through uncondensed while still
#: leaving room in a 32k-token window for the ~4k-character prompt and a
#: 4096-token answer.  The context processor's own 4000-character default
#: would condense almost every full text and most long abstracts.
DEFAULT_CONDENSE_THRESHOLD_CHARS = 48_000

#: Whole assessment attempts before giving up.  ``chat_json()`` retries
#: transport and JSON-shape failures inside each one; this outer bound covers
#: a reply that parses but carries no risk-of-bias section.  Two, not three:
#: a model that omits it twice has misread the prompt, and the bound keeps the
#: worst case at six model calls rather than nine.
_ASSESSMENT_ATTEMPTS = 2

#: Stand-in support text for a domain the model did not report.
_NO_INFORMATION = "Not reported or insufficient information to assess"

#: The nine Cochrane domains: response key, domain name, bias type, outcome
#: type.  One table rather than nine hand-written constructor calls, and the
#: source of the ``bias_type`` values ``collapse_risk_of_bias()`` groups by.
_ROB_DOMAINS: tuple[tuple[str, str, str, str | None], ...] = (
    ("random_sequence_generation", "Random sequence generation", "selection bias", None),
    ("allocation_concealment", "Allocation concealment", "selection bias", None),
    ("baseline_outcome_measurements", "Baseline outcome measurements", "selection bias", None),
    ("baseline_characteristics", "Baseline characteristics", "selection bias", None),
    (
        "blinding_participants_personnel",
        "Blinding of participants and personnel",
        "performance bias",
        None,
    ),
    (
        "blinding_outcome_assessment_subjective",
        "Blinding of outcome assessment (subjective outcomes)",
        "detection bias",
        "subjective",
    ),
    (
        "blinding_outcome_assessment_objective",
        "Blinding of outcome assessment (objective outcomes)",
        "detection bias",
        "objective",
    ),
    ("incomplete_outcome_data", "Incomplete outcome data", "attrition bias", None),
    ("selective_reporting", "Selective reporting", "reporting bias", None),
)


COCHRANE_SYSTEM_PROMPT = """\
You are a medical research methodologist specialising in systematic reviews \
and Cochrane methodology.

CRITICAL RULES:
1. Extract ONLY information that is ACTUALLY PRESENT in the text
2. DO NOT invent, assume, or fabricate any information
3. For anything not reported, use "Not reported" or "Details not reported"
4. Assess THIS study's methodology, not studies it references
5. Return ONLY valid JSON, no explanation"""


COCHRANE_TASK_PROMPT = """\
Conduct a complete Cochrane-style assessment of the study below.

Extract the STUDY CHARACTERISTICS table: methods (the study design, e.g.
"Parallel randomised trial"); participants (setting, population, inclusion and
exclusion criteria, total participants, group sizes); interventions
(description, control, duration); outcomes (description, primary, secondary,
timepoints); and notes (follow-up periods, funding, conflicts of interest,
ethical approval, trial registration, publication status).

Then judge the NINE Cochrane RISK OF BIAS domains. For each, give a judgement
of exactly "Low risk", "High risk" or "Unclear risk", plus the text supporting
it:

a) Random sequence generation (selection bias) — low if adequate
   (computer-generated, random number table), high if inadequate (alternation,
   birth date), unclear if not reported.
b) Allocation concealment (selection bias) — low if adequate (central
   allocation, sealed opaque envelopes), high if open lists, unclear if not
   reported.
c) Baseline outcome measurements (selection bias) — low if similar at
   baseline, high if they differed materially, unclear if not reported.
d) Baseline characteristics (selection bias) — low if balanced, high if
   important imbalances, unclear if not reported.
e) Blinding of participants and personnel (performance bias) — low if blinded
   or the outcome is unlikely to be affected by its absence.
f) Blinding of outcome assessment, SUBJECTIVE outcomes (detection bias) —
   patient-reported measures, quality of life.
g) Blinding of outcome assessment, OBJECTIVE outcomes (detection bias) —
   mortality, laboratory values.
h) Incomplete outcome data (attrition bias) — low if dropout is low, balanced
   across groups and handled appropriately.
i) Selective reporting (reporting bias) — low if every pre-specified outcome
   is reported."""


COCHRANE_RESPONSE_FORMAT = """\
Respond with JSON in exactly this shape:

{
    "study_characteristics": {
        "methods": "study design description",
        "participants": {
            "setting": "location/country",
            "population": "description of participants",
            "inclusion_criteria": ["criterion 1"],
            "exclusion_criteria": ["criterion 1"],
            "total_participants": 45,
            "group_sizes": {"intervention": 25, "control": 20},
            "baseline_characteristics_reported": true
        },
        "interventions": {
            "description": "intervention description",
            "intervention_groups": ["group 1"],
            "control_description": "control description",
            "duration": "duration"
        },
        "outcomes": {
            "description": "outcomes measured",
            "primary_outcomes": ["outcome 1"],
            "secondary_outcomes": ["outcome 1"],
            "outcome_timepoints": ["1 month", "3 months"]
        },
        "notes": {
            "follow_up_periods": ["6 months", "12 months"],
            "funding_source": "funding info",
            "conflicts_of_interest": "conflicts",
            "ethical_approval": "approval status",
            "trial_registration": "registration id",
            "publication_status": "full publication",
            "additional_notes": ["note 1"]
        }
    },
    "risk_of_bias": {
        "random_sequence_generation": {"judgement": "Low risk", "support_for_judgement": "..."},
        "allocation_concealment": {"judgement": "Unclear risk", "support_for_judgement": "..."},
        "baseline_outcome_measurements": {"judgement": "Low risk", "support_for_judgement": "..."},
        "baseline_characteristics": {"judgement": "Low risk", "support_for_judgement": "..."},
        "blinding_participants_personnel": {
            "judgement": "High risk", "support_for_judgement": "..."
        },
        "blinding_outcome_assessment_subjective": {
            "judgement": "High risk", "support_for_judgement": "..."
        },
        "blinding_outcome_assessment_objective": {
            "judgement": "Low risk", "support_for_judgement": "..."
        },
        "incomplete_outcome_data": {"judgement": "Low risk", "support_for_judgement": "..."},
        "selective_reporting": {"judgement": "Unclear risk", "support_for_judgement": "..."}
    },
    "overall_confidence": 0.7,
    "evidence_level": "Level 2 (moderate-high)",
    "assessment_notes": ["note 1"]
}

Use null for any field the text does not report. Every one of the nine
risk_of_bias domains must be present. Respond ONLY with valid JSON."""


#: What the digest must preserve.  A digest that drops these is a digest the
#: assessment pass cannot judge from.
CONDENSE_QUERY = (
    "Everything needed for a Cochrane assessment: the study design; the "
    "setting, population and group sizes; the interventions and controls; the "
    "outcomes measured and when; funding, conflicts of interest, ethical "
    "approval and trial registration; and the reported detail behind each risk "
    "of bias domain — how the randomisation sequence was generated, how "
    "allocation was concealed, whether groups were comparable at baseline, who "
    "was blinded to what, how much outcome data was missing and how it was "
    "handled, and whether every pre-specified outcome was reported."
)

CONDENSE_EXTRACTION_PROMPT = """\
Extract, verbatim where possible, every passage of this paper that bears on \
the following.

Needed: {query}

Paper section:
{content}

INSTRUCTIONS:
- Quote or closely paraphrase what the text actually says
- Keep numbers, group sizes, timepoints and named funders exactly
- Say nothing about what the text does not report — omissions are recorded by
  the assessment step, not invented here
- Return plain text

Extracted evidence:"""

CONDENSE_CONSOLIDATION_PROMPT = """\
Merge these extracted passages into one evidence summary.

Needed: {query}

Extracted evidence:
{content}

INSTRUCTIONS:
- Merge overlapping passages, keeping every distinct detail
- Preserve numbers, group sizes, timepoints and named funders exactly
- Keep the methodological detail even where it seems minor: it is what the
  risk of bias judgements rest on
- Return plain text

Consolidated evidence:"""


class CochraneAssessor(BaseAgent):
    """Produces Cochrane-aligned assessments of individual studies.

    Args:
        llm: The LLM client to use.
        model: Full model string (``"provider:model_name"``).  A capable
            model — the assessment is a nine-domain judgement plus a
            five-section extraction in one reply.
        template_engine: Optional template engine, for parity with the other
            quality agents.  This agent's prompts are module constants.
        temperature: Low by default, for consistency between runs.
        max_tokens: Output ceiling.  The reply carries nine judgements with
            their supporting text plus the characteristics table, so it is
            substantially larger than Tier 3's.
        condense_config: Governs the map-reduce pass that runs when the text
            is larger than ``max_context_chars``.  Defaults to
            :data:`DEFAULT_CONDENSE_THRESHOLD_CHARS`.
    """

    def __init__(
        self,
        llm: LLMClient,
        model: str,
        template_engine: TemplateEngine | None = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        condense_config: ProcessingConfig | None = None,
    ) -> None:
        super().__init__(
            llm=llm,
            model=model,
            template_engine=template_engine,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        self.condense_config = condense_config or ProcessingConfig(
            max_context_chars=DEFAULT_CONDENSE_THRESHOLD_CHARS
        )
        # ``total`` counts every ``assess()`` call, so
        # ``successful + failed == total`` holds.  Upstream incremented the
        # total only on the success path, after every failure had already
        # returned, so its ``success_rate`` could only ever be 1.0.
        self._stats: dict[str, int] = {
            "total_assessments": 0,
            "successful_assessments": 0,
            "failed_assessments": 0,
            "parse_failures": 0,
        }

    # --- Assessment ---

    def assess(
        self,
        title: str | None,
        text: str | None,
        *,
        study_id: str | None = None,
        pmid: str | None = None,
        doi: str | None = None,
        document_id: int | None = None,
        min_confidence: float = 0.0,
    ) -> CochraneStudyAssessment | None:
        """Assess one study against the Cochrane template.

        Either field may be ``None``.  With both missing there is nothing to
        assess and no model call is made — left to itself the model returns a
        fully-formed nine-domain judgement for a paper it was told nothing
        about.

        The caller chooses what *text* is: full text gives a real risk-of-bias
        assessment, an abstract gives a weak one.  Text longer than
        ``condense_config.max_context_chars`` is condensed first, and the
        result says so through
        :attr:`CochraneStudyAssessment.condensed_from_chars`.

        Args:
            title: The paper's title.
            text: The text to assess — full text or abstract.
            study_id: Cochrane's "Author Year" study label.  Unset, it falls
                back to ``"Study {document_id}"`` and then to the title; no
                surname is guessed from an author list.
            pmid: PubMed id, recorded on the characteristics table.
            doi: DOI, recorded on the characteristics table.
            document_id: The caller's own row id.
            min_confidence: Reject an assessment whose ``overall_confidence``
                falls below this.  Zero, the default, rejects nothing.  Only
                a *reported* confidence below the bar is rejected — an
                assessment whose confidence could not be parsed
                (``overall_confidence`` is ``None``) is kept regardless of
                how high *min_confidence* is set.  An unknown confidence is
                not a low one: the same rule keeps
                :func:`bmlib.transparency.models.calculate_risk_level` from
                treating an undetermined COI disclosure as a missing one.

        Returns:
            The assessment, or ``None`` if it could not be made.  ``None``
            rather than an all-"Unclear risk" stand-in: that would be
            indistinguishable from a real assessment in which the model
            genuinely judged every domain unclear, and anything persisting
            results would store the fabrication permanently.
        """
        title = (title or "").strip()
        text = (text or "").strip()
        label = study_id or (f"document {document_id}" if document_id is not None else title[:60])

        self._stats["total_assessments"] += 1

        if not title and not text:
            logger.warning("Cannot assess: both title and text are empty")
            self._stats["failed_assessments"] += 1
            return None

        notes: list[str] = []
        condensed_from: int | None = None

        if len(text) > self.condense_config.max_context_chars:
            condensed = self._condense(text, label)
            if condensed is None:
                self._stats["failed_assessments"] += 1
                return None
            condensed_from = len(text)
            text, notes = condensed

        assessment = self._attempt_assessment(title, text, label, notes, condensed_from)
        if assessment is None:
            return None

        assessment.study_characteristics.study_id = self._resolve_study_id(
            study_id, document_id, title
        )
        assessment.study_characteristics.document_id = document_id
        assessment.study_characteristics.document_title = title or None
        assessment.study_characteristics.pmid = pmid
        assessment.study_characteristics.doi = doi

        confidence = assessment.overall_confidence
        if confidence is not None and confidence < min_confidence:
            logger.info(
                "Discarding the assessment of %s: confidence %.2f is below the %.2f minimum",
                label,
                confidence,
                min_confidence,
            )
            self._stats["failed_assessments"] += 1
            return None

        self._stats["successful_assessments"] += 1
        return assessment

    def assess_batch(
        self,
        studies: list[dict[str, Any]],
        *,
        min_confidence: float = 0.0,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> list[CochraneStudyAssessment]:
        """Assess several studies, keeping the ones that succeeded.

        A convenience loop over :meth:`assess`, so it does take dicts — each
        keyed by that method's own parameter names (``title``, ``text``,
        ``study_id``, ``pmid``, ``doi``, ``document_id``).  That is a batch
        helper mapping a caller's records onto a typed call, not the typed
        call itself.

        Args:
            studies: One dict per study.
            min_confidence: Passed to each :meth:`assess` call.
            progress_callback: Called ``(current, total, title)`` before each
                study.

        Returns:
            The assessments that succeeded, in input order.  A study that
            could not be assessed is absent; :meth:`get_stats` counts it.
        """
        assessments: list[CochraneStudyAssessment] = []
        total = len(studies)

        for index, study in enumerate(studies):
            title = study.get("title") or ""
            if progress_callback:
                progress_callback(index + 1, total, title)

            assessment = self.assess(
                title,
                study.get("text"),
                study_id=study.get("study_id"),
                pmid=study.get("pmid"),
                doi=study.get("doi"),
                document_id=study.get("document_id"),
                min_confidence=min_confidence,
            )
            if assessment is not None:
                assessments.append(assessment)

        logger.info("Assessed %d of %d studies", len(assessments), total)
        return assessments

    def get_stats(self) -> dict[str, Any]:
        """Report what this assessor has done.

        ``total_assessments`` counts every :meth:`assess` call, so
        ``successful_assessments + failed_assessments == total_assessments``
        and ``success_rate`` can report a failure.  ``parse_failures`` is a
        *subset* of the failures, naming the ones that were an unusable reply
        rather than a transport error or a rejected confidence.

        Returns:
            The counters plus the derived ``success_rate``.
        """
        total = self._stats["total_assessments"]
        return {
            **self._stats,
            "success_rate": self._stats["successful_assessments"] / total if total else 0.0,
        }

    def _attempt_assessment(
        self,
        title: str,
        text: str,
        label: str,
        notes: list[str],
        condensed_from: int | None,
    ) -> CochraneStudyAssessment | None:
        """Run the model and parse its reply, retrying a structural failure.

        Args:
            title: The paper's title.
            text: The text to send (already condensed, if it was going to be).
            label: Names the study in log lines.
            notes: Extra notes to fold into the assessment.
            condensed_from: Original length when *text* is a digest.

        Returns:
            The parsed assessment, or ``None``.
        """
        prompt = (
            f"{COCHRANE_TASK_PROMPT}\n\n"
            f"Paper Title: {title}\n\n"
            f"Paper Text:\n{text}\n\n"
            f"{COCHRANE_RESPONSE_FORMAT}"
        )
        messages = [self.system_msg(COCHRANE_SYSTEM_PROMPT), self.user_msg(prompt)]

        for attempt in range(_ASSESSMENT_ATTEMPTS):
            try:
                data = self.chat_json(
                    messages=messages,
                    retry_context=f"Cochrane assessment of {label}",
                    require_dict=True,
                )
            except Exception as exc:
                logger.warning("Cochrane assessment of %s failed: %s", label, exc)
                self._stats["failed_assessments"] += 1
                return None

            try:
                return self._parse_assessment(data, notes, condensed_from)
            except ValueError as exc:
                logger.warning(
                    "Unusable Cochrane response for %s (attempt %d/%d): %s",
                    label,
                    attempt + 1,
                    _ASSESSMENT_ATTEMPTS,
                    exc,
                )

        self._stats["parse_failures"] += 1
        self._stats["failed_assessments"] += 1
        return None

    @staticmethod
    def _resolve_study_id(study_id: str | None, document_id: int | None, title: str) -> str:
        """Pick the Cochrane study label, without guessing a surname."""
        if study_id:
            return study_id
        if document_id is not None:
            return f"Study {document_id}"
        return title or "Unknown study"

    def _condense(self, text: str, label: str) -> tuple[str, list[str]] | None:
        """Reduce oversized text to an evidence digest that fits one context.

        Runs :class:`~bmlib.context_processor.LLMChunkProcessor` with this
        agent, so token accounting, retries and JSON repair are the ones the
        rest of bmlib uses.  The judgement is made once, afterwards, over the
        digest — no per-chunk judgements are made and none are merged.

        Args:
            text: The oversized text.
            label: Names the study in log lines.

        Returns:
            ``(digest, notes)``, or ``None`` when the run failed or produced
            nothing to judge.
        """
        processor = LLMChunkProcessor(
            agent=self,
            extraction_prompt=CONDENSE_EXTRACTION_PROMPT,
            consolidation_prompt=CONDENSE_CONSOLIDATION_PROMPT,
            config=self.condense_config,
        )
        result = processor.process([text], query=CONDENSE_QUERY)

        if result.status is ProcessingStatus.FAILED:
            logger.error("Could not condense the text of %s: %s", label, result.error_message)
            return None

        digest = result.final_result.content.strip()
        if not digest:
            # The Cochrane prompt over an empty string returns a confident
            # nine-domain assessment of no paper at all.
            logger.error("Condensing the text of %s produced an empty digest", label)
            return None

        notes: list[str] = []
        if result.status is not ProcessingStatus.COMPLETED:
            notes.append(
                f"Source text was condensed before assessment; the condensation "
                f"finished with status {result.status.value}, so the digest may be "
                f"incomplete."
            )
        return digest, notes

    # --- Parsing ---

    def _parse_assessment(
        self,
        data: dict[str, Any],
        notes: list[str],
        condensed_from: int | None,
    ) -> CochraneStudyAssessment:
        """Build a :class:`CochraneStudyAssessment` from the model's reply.

        Args:
            data: The parsed JSON object.
            notes: Notes to prepend to the model's own.
            condensed_from: Original length when the text was condensed.

        Returns:
            The assessment.  Identity fields are filled in by the caller.

        Raises:
            ValueError: If the reply carries no risk-of-bias section.  Nine
                fabricated "Unclear risk" defaults would be indistinguishable
                from a real assessment.
        """
        rob_data = data.get("risk_of_bias")
        if not isinstance(rob_data, dict) or not rob_data:
            raise ValueError("the response carries no risk_of_bias section")

        sc_data = data.get("study_characteristics")
        if not isinstance(sc_data, dict):
            sc_data = {}

        characteristics = CochraneStudyCharacteristics(
            study_id="",  # replaced by the caller
            methods=str(sc_data.get("methods") or "Not reported"),
            participants=CochraneParticipants.from_dict(_as_dict(sc_data.get("participants"))),
            interventions=CochraneInterventions.from_dict(_as_dict(sc_data.get("interventions"))),
            outcomes=CochraneOutcomes.from_dict(_as_dict(sc_data.get("outcomes"))),
            notes=CochraneNotes.from_dict(_as_dict(sc_data.get("notes"))),
        )

        model_notes = data.get("assessment_notes")
        all_notes = [*notes, *(model_notes if isinstance(model_notes, list) else [])]

        return CochraneStudyAssessment(
            study_characteristics=characteristics,
            risk_of_bias=_parse_risk_of_bias(rob_data),
            overall_confidence=_clamped_confidence(data.get("overall_confidence")),
            evidence_level=data.get("evidence_level"),
            assessment_notes=all_notes or None,
            condensed_from_chars=condensed_from,
        )


def _as_dict(value: object) -> dict:
    """Return *value* when it is a dict, an empty dict otherwise.

    A model that answers ``null`` or a bare string for a whole section must
    not take the assessment down with it; the section's ``from_dict`` then
    supplies its own "Not reported" defaults.
    """
    return value if isinstance(value, dict) else {}


def _clamped_confidence(value: object) -> float | None:
    """Read the model's confidence, clamped to 0.0–1.0.

    A model reporting 1.4 would outrank every honest result and defeat
    ``min_confidence``.  An unusable value becomes ``None`` rather than a
    fabricated number.
    """
    if value is None:
        return None
    try:
        return min(1.0, max(0.0, float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        logger.warning("Model reported an unusable confidence %r; recording none", value)
        return None


def _parse_risk_of_bias(rob_data: dict[str, Any]) -> CochraneRiskOfBias:
    """Build the nine-domain assessment from the model's ``risk_of_bias``.

    Every judgement is normalised through
    :meth:`RiskOfBiasJudgement.from_string`.  Writing the model's raw string
    through — as upstream did — stores an invalid value for a model that
    answers ``"low"`` rather than ``"Low risk"``, and
    :meth:`CochraneRiskOfBias.get_summary_counts` then skips that domain
    entirely, silently reporting eight of nine.

    A domain the model omitted defaults to "Unclear risk".  That is honest
    per-domain degradation of an otherwise good answer; a missing *section*
    is rejected by the caller instead.

    Args:
        rob_data: The reply's ``risk_of_bias`` object.

    Returns:
        The nine-domain assessment.
    """
    items = {}
    for key, domain, bias_type, outcome_type in _ROB_DOMAINS:
        raw = _as_dict(rob_data.get(key))
        judgement = RiskOfBiasJudgement.from_string(
            str(raw.get("judgement") or ROB_JUDGEMENT_UNCLEAR)
        ).value
        items[key] = RiskOfBiasItem(
            domain=domain,
            bias_type=bias_type,
            judgement=judgement,
            support_for_judgement=str(raw.get("support_for_judgement") or _NO_INFORMATION),
            outcome_type=outcome_type,
        )
    return CochraneRiskOfBias(**items)
