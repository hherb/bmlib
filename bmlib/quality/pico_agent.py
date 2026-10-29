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


"""PICO extraction with quotes verified against the paper.

A port of bmlibrarian's ``agents/pico_agent.py`` (StudyAnalyzer item 7; the
design is ``docs/superpowers/specs/2026-10-30-pico-agent-design.md``).  It
answers two questions, as two independent calls:

* :meth:`PicoAgent.check_suitability` — is this an intervention study with a
  PICO at all?  Reviews, case reports and editorials are not.
* :meth:`PicoAgent.extract` — the Population, Intervention, Comparison and
  Outcome, plus study type and sample size.

Unlike upstream, every part carries the passages that back it.  The model
copies quotes from the paper; each is located by
:func:`bmlib.citations.find_passage` in the text the caller passed, and the
**paper's** span is kept.  A part none of whose quotes is found is
:attr:`~PicoPartState.UNVERIFIED`, its description kept for a reader to judge;
a part the paper does not state is :attr:`~PicoPartState.NOT_REPORTED`, which
needs no quote.

Nothing is truncated: text too long for one context is condensed first by the
Cochrane assessor's map-reduce, and the quotes are still verified against the
original text, never the digest.  Nothing is discarded for low confidence.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from typing import Any, TypeVar

from bmlib.agents.base import BaseAgent, ChatJsonError, ChatJsonTruncatedError
from bmlib.citations import find_passage
from bmlib.context_processor import ProcessingConfig
from bmlib.llm import LLMClient
from bmlib.quality._condense import CondensationRefused, condense_text
from bmlib.quality._json_fields import as_bool, as_text
from bmlib.quality.cochrane_assessor import DEFAULT_CONDENSE_THRESHOLD_CHARS, clamped_confidence
from bmlib.quality.pico_models import (
    PicoCitation,
    PicoError,
    PicoErrorKind,
    PicoExtraction,
    PicoPart,
    PicoPartState,
    PicoSuitability,
    mean_confidence,
)
from bmlib.templates import TemplateEngine

logger = logging.getLogger(__name__)

#: The four parts, in the order they are read, reported and averaged.
PICO_PARTS: tuple[str, ...] = ("population", "intervention", "comparison", "outcome")

#: How many times a question is asked when its reply parses but cannot be read
#: (so it is asked once more) — the Cochrane assessor's ``_ASSESSMENT_ATTEMPTS``.
_READ_ATTEMPTS = 2

_T = TypeVar("_T")


# --- Prompts (byte-identical in the Rust port) ---

PICO_SYSTEM_PROMPT = """\
You are an expert in clinical research methodology and evidence-based \
medicine.

CRITICAL RULES:
1. Base everything ONLY on what is ACTUALLY PRESENT in the text
2. DO NOT invent, assume, or fabricate any information
3. Describe THIS study, not studies it references
4. Return ONLY valid JSON, no explanation"""


SUITABILITY_TASK_PROMPT = """\
Determine whether the document below is an INTERVENTION STUDY suitable for \
PICO (Population, Intervention, Comparison, Outcome) extraction.

A document is SUITABLE if it is:
- A randomized controlled trial (RCT)
- A clinical trial with an intervention
- A cohort study with an intervention or exposure
- A case-control study
- A comparative effectiveness study
- Any primary research testing an intervention, treatment, or exposure

A document is NOT SUITABLE if it is:
- A systematic review or meta-analysis (it reviews other studies and does not \
test an intervention itself)
- A narrative review or literature review
- An editorial, commentary, or opinion piece
- A case report or case series (no comparison group)
- A cross-sectional survey (no intervention)
- A basic science or laboratory study (not clinical)
- A qualitative study without quantifiable outcomes

KEY INDICATORS of an intervention study:
- It tests a specific treatment, drug, procedure, or intervention
- It has a defined patient population or study participants
- It includes a control or comparison group (or a baseline comparison)
- It measures specific outcomes or endpoints
- It reports results from actual participants, not a review of literature

Assess:
1. is_intervention_study: does this study test an intervention, treatment or \
exposure on participants?
2. has_comparison: does it have a control or comparison group?
3. is_suitable: should PICO components be extracted? (true for an \
intervention study)
4. confidence: how confident you are in this assessment (0.0-1.0)
5. rationale: a brief explanation (2-3 sentences)
6. study_type: the type of study (e.g. "RCT", "cohort study", "systematic \
review", "case-control study")

Do not confuse a systematic review (which reviews other studies) with primary \
research. Look for evidence of actual participant enrollment and intervention. \
If uncertain, lean towards NOT SUITABLE and say why in the rationale."""


SUITABILITY_RESPONSE_FORMAT = """\
Respond with JSON in exactly this shape:

{
    "is_intervention_study": true,
    "has_comparison": true,
    "is_suitable": true,
    "confidence": 0.9,
    "rationale": "This is an RCT testing intervention X in population Y with control group Z.",
    "study_type": "randomized controlled trial"
}"""


EXTRACTION_TASK_PROMPT = """\
Extract the PICO components (Population, Intervention, Comparison, Outcome) \
from the research paper below.

For EACH of the four components give:
- description: what the paper says, specific and close to its wording
- reported: false ONLY when the paper says nothing about this component; \
otherwise true
- confidence: 0.0-1.0, using the scale below
- quotes: 1-3 passages copied VERBATIM from the paper that support the \
description. Copy them character for character; do not paraphrase, shorten \
with ellipses, or join sentences. Give no quotes when reported is false.

1. Population (P): who was studied — demographics (age, sex, condition), \
inclusion and exclusion criteria, setting.
   Example: "Adults aged 40-65 with type 2 diabetes and HbA1c > 7%, recruited \
from primary care clinics"

2. Intervention (I): what was done to the study population — the treatment, \
procedure or exposure, with dose, frequency and duration where stated.
   Example: "Metformin 1000mg twice daily for 12 weeks"

3. Comparison (C): what the intervention was compared against — placebo, \
standard care, no treatment, or an alternative intervention.
   Example: "Placebo tablets twice daily for 12 weeks"
   A study the paper describes as single-arm or uncontrolled HAS a reported \
comparison: say there was no comparison group, set reported to true, and \
quote the passage that says so.

4. Outcome (O): what was measured — primary and secondary outcomes, how they \
were measured, and when.
   Example: "Change in HbA1c from baseline to 12 weeks (primary); fasting \
glucose and body weight (secondary)"

Also give:
- study_type: the study design if clear (RCT, cohort study, case-control, \
cross-sectional, ...), or null
- sample_size: the number of participants if stated (e.g. "N=150"), or null

Confidence scale:
- 1.0 = explicitly stated, no ambiguity
- 0.8 = clearly stated but some details missing
- 0.6 = can be inferred but not explicitly stated
- 0.4 = partially mentioned, significant uncertainty
- 0.2 = barely mentioned, high uncertainty"""


EXTRACTION_RESPONSE_FORMAT = """\
Respond with JSON in exactly this shape:

{
    "population": {
        "description": "who was studied",
        "reported": true,
        "confidence": 0.9,
        "quotes": ["a passage copied verbatim from the paper"]
    },
    "intervention": {
        "description": "what was done",
        "reported": true,
        "confidence": 0.9,
        "quotes": ["a passage copied verbatim from the paper"]
    },
    "comparison": {
        "description": "what it was compared against",
        "reported": true,
        "confidence": 0.9,
        "quotes": ["a passage copied verbatim from the paper"]
    },
    "outcome": {
        "description": "what was measured",
        "reported": true,
        "confidence": 0.9,
        "quotes": ["a passage copied verbatim from the paper"]
    },
    "study_type": "randomized controlled trial",
    "sample_size": "N=150"
}"""


PICO_CONDENSE_QUERY = (
    "Everything needed to identify the study's PICO: the study design; who was "
    "studied (age, sex, condition, inclusion and exclusion criteria, setting) "
    "and how many; the intervention (treatment, dose, frequency, duration); "
    "what it was compared against, or that there was no comparison group; and "
    "the outcomes measured, how, and when."
)

PICO_CONDENSE_EXTRACTION_PROMPT = """\
Extract VERBATIM every passage of this paper that bears on the following.

Needed: {query}

Paper section:
{content}

INSTRUCTIONS:
- Copy the passages character for character; do not paraphrase or shorten \
them, since each will be checked against the paper word for word
- Keep numbers, doses, group sizes and timepoints exactly
- Say nothing about what the text does not report
- Return plain text

Extracted passages:"""

PICO_CONDENSE_CONSOLIDATION_PROMPT = """\
Merge these extracted passages into one collection of passages.

Needed: {query}

Extracted passages:
{content}

INSTRUCTIONS:
- Keep every passage VERBATIM, character for character; drop only exact \
duplicates
- Preserve numbers, doses, group sizes and timepoints exactly
- Return plain text

Consolidated passages:"""


# --- Reading a reply ---


def verify_quotes(
    quotes: object, text: str, min_similarity: float
) -> tuple[list[PicoCitation], list[str], int]:
    """Locate the model's quotes in *text*.

    Args:
        quotes: The reply's ``quotes`` value, as it arrived.
        text: The text the quotes were copied from; offsets index it.
        min_similarity: :func:`~bmlib.citations.find_passage`'s threshold.

    Returns:
        ``(citations, not_found, dropped)``: the paper's spans for the quotes
        found, in the order the quotes came and each span once; the quotes not
        found, verbatim; and how many were not usable text at all.  An absent
        ``quotes`` (``None``) drops nothing; any other value that is not a list
        counts as one dropped, as does a member that is not a string or is
        blank.
    """
    if quotes is None:
        return [], [], 0
    if not isinstance(quotes, list):
        logger.debug("Reading quotes as none: the answer was a %s", type(quotes).__name__)
        return [], [], 1

    citations: list[PicoCitation] = []
    seen: set[tuple[int, int]] = set()
    not_found: list[str] = []
    dropped = 0
    for quote in quotes:
        if not isinstance(quote, str) or not quote.strip():
            dropped += 1
            continue
        match = find_passage(quote, text, min_similarity)
        if match.start is None or match.end is None or match.text is None:
            not_found.append(quote)
            continue
        if (match.start, match.end) in seen:
            continue
        seen.add((match.start, match.end))
        citations.append(
            PicoCitation(
                start=match.start,
                end=match.end,
                text=match.text,
                similarity=match.similarity,
                exact=match.exact,
            )
        )
    return citations, not_found, dropped


def read_part(value: object, text: str, min_similarity: float) -> PicoPart:
    """Read one PICO part and verify its quotes.

    Raises:
        ValueError: When *value* is not an object carrying a string
            ``description`` — a part that cannot be shown at all.
    """
    if not isinstance(value, dict):
        raise ValueError(f"expected an object, got {type(value).__name__}")
    description = value.get("description")
    if not isinstance(description, str):
        raise ValueError(f"description is a {type(description).__name__}, not text")

    confidence = clamped_confidence(value.get("confidence"))
    # Only an explicit ``false`` is an absence: ``bool("no")`` is ``True``, and
    # a flag the model garbled must not hide a part it described.
    if as_bool(value.get("reported"), "reported") is False:
        return PicoPart(description, PicoPartState.NOT_REPORTED, confidence)

    citations, not_found, dropped = verify_quotes(value.get("quotes"), text, min_similarity)
    state = PicoPartState.VERIFIED if citations else PicoPartState.UNVERIFIED
    return PicoPart(description, state, confidence, citations, not_found, dropped)


def read_extraction(data: dict[str, Any], text: str, min_similarity: float) -> PicoExtraction:
    """Read an extraction reply, verifying every part's quotes against *text*.

    Raises:
        ValueError: When any of the four parts is missing or unusable; the
            message names it.
    """
    parts: dict[str, PicoPart] = {}
    for name in PICO_PARTS:
        try:
            parts[name] = read_part(data.get(name), text, min_similarity)
        except ValueError as exc:
            raise ValueError(f"{name}: {exc}") from None

    return PicoExtraction(
        population=parts["population"],
        intervention=parts["intervention"],
        comparison=parts["comparison"],
        outcome=parts["outcome"],
        study_type=as_text(data.get("study_type"), "study_type"),
        sample_size=as_text(data.get("sample_size"), "sample_size"),
        overall_confidence=mean_confidence(list(parts.values())),
    )


def read_suitability(data: dict[str, Any]) -> PicoSuitability:
    """Read a suitability reply.

    Raises:
        ValueError: When ``is_suitable`` is not a boolean — the one answer
            the check exists to give.
    """
    is_suitable = data.get("is_suitable")
    if not isinstance(is_suitable, bool):
        raise ValueError(f"is_suitable is a {type(is_suitable).__name__}, not a boolean")
    return PicoSuitability(
        is_suitable=is_suitable,
        is_intervention_study=as_bool(data.get("is_intervention_study"), "is_intervention_study"),
        has_comparison=as_bool(data.get("has_comparison"), "has_comparison"),
        study_type=as_text(data.get("study_type"), "study_type"),
        confidence=clamped_confidence(data.get("confidence")),
        rationale=as_text(data.get("rationale"), "rationale"),
    )


# --- The agent ---


def _check_similarity(min_similarity: object) -> float:
    """Refuse a threshold :func:`~bmlib.citations.find_passage` would refuse.

    Checked before any model call, so a caller's mistake costs no request.
    """
    if (
        isinstance(min_similarity, bool)
        or not isinstance(min_similarity, int | float)
        or math.isnan(min_similarity)
        or not 0.0 < min_similarity <= 1.0
    ):
        raise ValueError(f"min_similarity must be a number in (0, 1], got {min_similarity!r}")
    return float(min_similarity)


class PicoAgent(BaseAgent):
    """Extracts a study's PICO, every part backed by passages of the paper.

    Two independent calls: :meth:`check_suitability` says whether the paper
    has a PICO at all, and :meth:`extract` extracts it.  A caller may run the
    extraction whatever the check answered.

    Each call either returns its result or raises :class:`PicoError` (or, from
    :meth:`extract`, ``ValueError`` for a bad ``min_similarity``), whose
    ``kind`` says why — so an app can retry an outage, ask for a larger
    ``max_tokens`` after a truncation, and suggest another model when the
    replies are unusable.  An outage during the condensation of a long text
    is ``CONDENSATION_FAILED``, its message naming the failed map-reduce.

    Args:
        llm: The client requests go through.
        model: The ``"provider:model"`` string.
        template_engine: Unused by this agent; accepted as every agent does.
        temperature: Sampling temperature.
        max_tokens: The reply's output ceiling.
        condense_config: The budget beyond which text is condensed first;
            ``max_context_chars`` defaults to the Cochrane assessor's
            :data:`~bmlib.quality.cochrane_assessor.DEFAULT_CONDENSE_THRESHOLD_CHARS`.
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

    def check_suitability(self, title: str | None, text: str | None) -> PicoSuitability:
        """Say whether the paper is an intervention study with a PICO.

        The caller chooses the text: an abstract keeps the check cheap.  Text
        too long for one context is condensed, never cut.

        Args:
            title: The paper's title.
            text: The text to judge.

        Returns:
            The answer.  Only ``is_suitable`` is required of the model.

        Raises:
            PicoError: Naming why no answer could be made.
        """
        title, text, notes, condensed_from, status = self._prepare(
            title, text, "the suitability check"
        )
        suitability = self._ask(
            SUITABILITY_TASK_PROMPT,
            SUITABILITY_RESPONSE_FORMAT,
            title,
            text,
            read_suitability,
            f"PICO suitability check of {title[:60]!r}",
        )
        suitability.condensed_from_chars = condensed_from
        suitability.condensation_status = status
        suitability.notes = notes
        return suitability

    def extract(
        self, title: str | None, text: str | None, *, min_similarity: float
    ) -> PicoExtraction:
        """Extract the paper's PICO and verify each part's quotes.

        Args:
            title: The paper's title.
            text: The paper's text.  Citation offsets are code points into it,
                exactly as passed; a caller who wants a quote from the title
                found includes the title here.
            min_similarity: How close a quote must be to a span of *text* to
                count as found — :func:`~bmlib.citations.find_passage`'s
                threshold.  Required: it is set by measurement, by the caller.

        Returns:
            The breakdown.  Nothing is discarded for low confidence.

        Raises:
            ValueError: *min_similarity* is not a number in (0, 1].
            PicoError: Naming why no breakdown could be made.
        """
        similarity = _check_similarity(min_similarity)
        # Quotes are located in the text as passed, unstripped, so an offset
        # indexes the string the caller will highlight.
        original = text or ""
        title, sent, notes, condensed_from, status = self._prepare(title, text, "extraction")
        extraction = self._ask(
            EXTRACTION_TASK_PROMPT,
            EXTRACTION_RESPONSE_FORMAT,
            title,
            sent,
            # Quotes are verified against the paper, never the digest: a quote
            # the condensation paraphrased is caught here like any other.
            lambda data: read_extraction(data, original, similarity),
            f"PICO extraction of {title[:60]!r}",
        )
        extraction.condensed_from_chars = condensed_from
        extraction.condensation_status = status
        extraction.notes = notes
        return extraction

    def _prepare(
        self, title: str | None, text: str | None, stage: str
    ) -> tuple[str, str, list[str], int | None, str | None]:
        """Strip the input, refuse an empty one, condense an oversized one.

        Returns:
            ``(title, text_to_send, notes, condensed_from_chars,
            condensation_status)``.
        """
        title = (title or "").strip()
        text = (text or "").strip()
        if not title and not text:
            raise PicoError(PicoErrorKind.EMPTY_INPUT, "both the title and the text are empty")
        if len(text) <= self.condense_config.max_context_chars:
            return title, text, [], None, None

        try:
            digest, notes, status = condense_text(
                self,
                text,
                title[:60],
                self.condense_config,
                extraction_prompt=PICO_CONDENSE_EXTRACTION_PROMPT,
                consolidation_prompt=PICO_CONDENSE_CONSOLIDATION_PROMPT,
                query=PICO_CONDENSE_QUERY,
                stage=stage,
            )
        except CondensationRefused as exc:
            raise PicoError(
                PicoErrorKind.CONDENSATION_FAILED,
                f"a {len(text)}-character text could not be condensed to fit "
                f"{self.condense_config.max_context_chars} characters: {exc}",
            ) from exc
        return title, digest, notes, len(text), status.value

    def _ask(
        self,
        task: str,
        response_format: str,
        title: str,
        text: str,
        read: Callable[[dict[str, Any]], _T],
        label: str,
    ) -> _T:
        """Send one question and read the reply, retrying an unreadable one."""
        # Concatenated, never ``str.format``ted: braces in a paper are text.
        prompt = (
            task
            + "\n\nPaper Title: "
            + title
            + "\n\nPaper Text:\n"
            + text
            + "\n\n"
            + response_format
        )
        messages = [self.system_msg(PICO_SYSTEM_PROMPT), self.user_msg(prompt)]

        problem: ValueError | None = None
        for attempt in range(_READ_ATTEMPTS):
            try:
                data = self.chat_json(messages, retry_context=label, require_dict=True)
            except ChatJsonTruncatedError as exc:
                raise PicoError(PicoErrorKind.TRUNCATED, str(exc)) from exc
            except ChatJsonError as exc:
                raise PicoError(PicoErrorKind.UNUSABLE_REPLY, str(exc)) from exc
            except Exception as exc:
                raise PicoError(PicoErrorKind.REQUEST_FAILED, str(exc)) from exc
            try:
                return read(data)
            except ValueError as exc:
                problem = exc
                logger.warning(
                    "Unusable reply for %s (attempt %d/%d): %s",
                    label,
                    attempt + 1,
                    _READ_ATTEMPTS,
                    exc,
                )
        raise PicoError(
            PicoErrorKind.UNUSABLE_REPLY,
            f"{label}: {_READ_ATTEMPTS} replies could not be read: {problem}",
        ) from problem
