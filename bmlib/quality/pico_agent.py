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

:meth:`PicoAgent.prepare` condenses a long paper once for both, and for every
retry, through :meth:`~PicoAgent.check_suitability_prepared` and
:meth:`~PicoAgent.extract_prepared` (#638).

Unlike upstream, every part carries the passages that back it.  The model
copies quotes from the paper; each is located by
:func:`bmlib.citations.find_passage` in the text the caller passed, and the
**paper's** span is kept.  A part none of whose quotes is found is
:attr:`~PicoPartState.UNVERIFIED`, its description kept for a reader to judge,
or :attr:`~PicoPartState.CONTRADICTED` when a quote is the paper's passage with
a number, a negation or a direction changed (#647); a part the paper does not
state is :attr:`~PicoPartState.NOT_REPORTED`, which needs no quote.

Nothing is truncated: text too long for one context is condensed first by the
Cochrane assessor's map-reduce, and the quotes are still verified against the
original text, never the digest.  Nothing is discarded for low confidence.

``num_ctx`` reaches every request, the condenser's included (#640), and a
``stop`` ends a run before its next request (#642).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from typing import Any, TypeVar

from bmlib.agents.base import (
    BaseAgent,
    ChatJsonError,
    ChatJsonTruncatedError,
    ChatStoppedError,
)
from bmlib.citations import PassageMatch, find_passage, split_at_ellipses
from bmlib.citations.passage_match import ELLIPSIS_PIECE_MIN_WORDS
from bmlib.context_processor import ProcessingConfig, ProcessingStoppedError, ProgressCallback
from bmlib.llm import LLMClient, LLMMessage, LLMResponse
from bmlib.llm.providers.ollama import MAX_NUM_CTX
from bmlib.llm.stream import Stop, stop_reader
from bmlib.llm.utils import http_status
from bmlib.quality._condense import (
    CondensationRefusedError,
    condense_text,
    is_retryable_request,
)
from bmlib.quality._json_fields import as_bool, as_text
from bmlib.quality.cochrane_assessor import DEFAULT_CONDENSE_THRESHOLD_CHARS, clamped_confidence
from bmlib.quality.pico_models import (
    PicoCitation,
    PicoContradictedQuote,
    PicoError,
    PicoErrorKind,
    PicoExtraction,
    PicoPart,
    PicoPartState,
    PicoPrepared,
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

#: Exceptions that can only mean bmlib is wrong — ``transparency``'s
#: ``_BUG_TYPES``, restated rather than imported, and held to agreeing by
#: ``tests/test_transparency.py``.
_BUG_TYPES: tuple[type[BaseException], ...] = (
    TypeError,
    AttributeError,
    NameError,
    KeyError,
    IndexError,
)

#: What a request may raise that is not the provider failing, so is not
#: ``REQUEST_FAILED`` (#573): a bmlib defect and a provider SDK that will not
#: import.  They propagate, since retrying cannot cure them.  A missing
#: Anthropic key raises ``TypeError`` from the SDK and propagates too, which is
#: right for a configuration error.  ``ValueError`` is not a member: an SDK
#: reading a 200 body that is not JSON raises one, which is the remote's
#: (PR #615's review).  An unknown provider is refused before any request
#: instead, by :meth:`PicoAgent._check_provider`.
_NOT_A_REQUEST_FAILURE: tuple[type[BaseException], ...] = (*_BUG_TYPES, ImportError)


#: What the stop's refusal and :attr:`PicoErrorKind.STOPPED` say.
STOPPED_MESSAGE = "stopped before the next request was sent"


class _UnreadableReplyError(ValueError):
    """A reply that parsed but cannot be read; asking again may help.

    Its own class so that :meth:`PicoAgent._ask` re-asks for this alone: a
    ``ValueError`` from bmlib's own code while reading is a defect and
    propagates, rather than being re-requested as the model's fault (#573).
    """


# --- Prompts (byte-identical in the Rust port) ---

PICO_SYSTEM_PROMPT = """\
You are an expert in clinical research methodology and evidence-based \
medicine.

CRITICAL RULES:
1. Base everything ONLY on what is ACTUALLY PRESENT in the text
2. DO NOT invent, assume, or fabricate any information
3. Describe THIS study, not studies it references
4. Return ONLY valid JSON, no explanation; use null, never NaN or \
Infinity, for a number you cannot give"""


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
) -> tuple[list[PicoCitation], list[str], list[PicoContradictedQuote], int]:
    """Locate the model's quotes in *text*.

    Args:
        quotes: The reply's ``quotes`` value, as it arrived.
        text: The text the quotes were copied from; offsets index it.
        min_similarity: :func:`~bmlib.citations.find_passage`'s threshold.

    Returns:
        ``(citations, not_found, contradicted, dropped)``: the paper's spans
        for the quotes found, in the order the quotes came and each span once;
        the quotes not found, verbatim; the quotes the paper contradicts, each
        with :func:`~bmlib.citations.find_passage`'s reason (#647); and how
        many were not usable text at all.  An absent
        ``quotes`` (``None``) drops nothing, and a bare string reads as a list
        of one (#577); any other value that is not a list counts as one
        dropped, as does a member that is not a string or is blank.

        A quote not found whole that holds an ellipsis is located piece by
        piece (:func:`_locate`): found when every piece is, each piece then a
        citation of its own; contradicted when any piece is.
    """
    if quotes is None:
        return [], [], [], 0
    if isinstance(quotes, str):
        quotes = [quotes]
    if not isinstance(quotes, list):
        logger.debug("Reading quotes as none: the answer was a %s", type(quotes).__name__)
        return [], [], [], 1

    citations: list[PicoCitation] = []
    seen: set[tuple[int, int]] = set()
    not_found: list[str] = []
    contradicted: list[PicoContradictedQuote] = []
    dropped = 0
    for quote in quotes:
        if not isinstance(quote, str) or not quote.strip():
            dropped += 1
            continue
        matches = _locate(quote, text, min_similarity)
        contradiction = next((m.contradiction for m in matches if m.contradiction), None)
        if contradiction is not None:
            contradicted.append(PicoContradictedQuote(quote, contradiction))
            continue
        if not all(m.matched for m in matches):
            not_found.append(quote)
            continue
        for match in matches:
            if match.start is None or match.end is None or match.text is None:
                continue  # unreachable: every match is accepted
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
    return citations, not_found, contradicted, dropped


def _locate(quote: str, text: str, min_similarity: float) -> list[PassageMatch]:
    """Where *quote* is in *text*: the whole quote, or else its pieces.

    The whole quote first, so a quote the paper holds as given (an ellipsis
    and all) is matched as before.  When it is neither found nor
    contradicted and it holds an ellipsis, the pieces
    :func:`~bmlib.citations.split_at_ellipses` cuts it into are located one by
    one — unless a piece is shorter than
    :data:`~bmlib.citations.passage_match.ELLIPSIS_PIECE_MIN_WORDS` words,
    which would match almost anywhere: then the whole quote's miss stands.

    Returns:
        One match per piece located, or the whole quote's match alone.
    """
    whole = find_passage(quote, text, min_similarity)
    if whole.matched or whole.contradiction is not None:
        return [whole]
    pieces = split_at_ellipses(quote)
    if not pieces or any(len(piece.split()) < ELLIPSIS_PIECE_MIN_WORDS for piece in pieces):
        return [whole]
    return [find_passage(piece, text, min_similarity) for piece in pieces]


def read_part(value: object, text: str, min_similarity: float) -> PicoPart:
    """Read one PICO part and verify its quotes.

    Quotes given beside ``reported: false`` are verified too (#577): one the
    paper holds makes the part reported, and the rest stay on the part as not
    found, contradicted or dropped.  A part that stays not reported may have a blank or
    absent description, read as ``""`` when absent (#618); a reported part
    may not.

    Raises:
        ValueError: When *value* is not an object, its ``description`` is
            neither a string nor absent, or a reported part's description is
            absent or blank — a part that cannot be shown at all.
    """
    if not isinstance(value, dict):
        raise _UnreadableReplyError(f"expected an object, got {type(value).__name__}")
    description = value.get("description")
    citations, not_found, contradicted, dropped = verify_quotes(
        value.get("quotes"), text, min_similarity
    )
    # Only an explicit ``false`` is an absence: ``bool("no")`` is ``True``, and
    # a flag the model garbled must not hide a part it described.
    reported = as_bool(value.get("reported"), "reported") is not False or bool(citations)
    if description is None and not reported:
        description = ""
    if not isinstance(description, str):
        raise _UnreadableReplyError(f"description is a {type(description).__name__}, not text")
    # A blank description shows nothing, as ``null`` does, so it is asked again (#578).
    if reported and not description.strip():
        raise _UnreadableReplyError("description is blank")

    confidence = clamped_confidence(value.get("confidence"))
    if not reported:
        state = PicoPartState.NOT_REPORTED
    elif citations:
        state = PicoPartState.VERIFIED
    elif contradicted:
        state = PicoPartState.CONTRADICTED
    else:
        state = PicoPartState.UNVERIFIED
    return PicoPart(description, state, confidence, citations, not_found, dropped, contradicted)


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
        except _UnreadableReplyError as exc:
            raise _UnreadableReplyError(f"{name}: {exc}") from None

    return PicoExtraction(
        population=parts["population"],
        intervention=parts["intervention"],
        comparison=parts["comparison"],
        outcome=parts["outcome"],
        study_type=as_text(data.get("study_type"), "study_type"),
        sample_size=_sample_size(data.get("sample_size")),
        overall_confidence=mean_confidence(list(parts.values())),
    )


def _sample_size(value: object) -> str | None:
    """The reply's ``sample_size`` as text: a string verbatim, an integer as its digits.

    ``"sample_size": 150`` is the natural answer to the prompt, and was lost
    to ``as_text`` (#578).  A ``bool`` and a float are not counts: Python's
    ``str(150.0)`` is ``"150.0"`` where Rust's is ``"150"``, so a float is
    refused in both rather than spelt two ways.
    """
    if isinstance(value, int) and not isinstance(value, bool):
        # shortcut: past 64 bits Rust's serde_json reads the integer as a float
        # and refuses it, where Python keeps it; no participant count is that big.
        return str(value)
    return as_text(value, "sample_size")


def read_suitability(data: dict[str, Any]) -> PicoSuitability:
    """Read a suitability reply.

    Raises:
        ValueError: When ``is_suitable`` is not a boolean — the one answer
            the check exists to give.
    """
    is_suitable = data.get("is_suitable")
    if not isinstance(is_suitable, bool):
        raise _UnreadableReplyError(f"is_suitable is a {type(is_suitable).__name__}, not a boolean")
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

    Each call returns its result or raises :class:`PicoError` (or, from
    :meth:`extract`, ``ValueError`` for a bad ``min_similarity``), whose
    ``kind`` says why, unless what failed is no outage (below) — so an app
    can retry an outage, ask for a larger ``max_tokens`` after a truncation,
    and suggest another model when the replies are unusable.  An outage
    that fails the condensation of a long text is ``REQUEST_FAILED`` too
    (#616), its message naming the failed map-reduce.
    What no retry or other model can cure propagates unwrapped instead (#573):
    a bmlib defect (``TypeError``, ``AttributeError``, ``NameError``,
    ``KeyError``, ``IndexError``, or a ``ValueError`` raised while reading a
    reply), an unknown provider (``ValueError``) and a provider SDK not
    installed (``ImportError``), both refused before any request, and a missing
    Anthropic key (the SDK's ``TypeError``), also when it fails the
    condensation of a long text (#616), whichever batch it hit; in a
    condensation that still produced a digest it is only noted (#665).

    Args:
        llm: The client requests go through.
        model: The ``"provider:model"`` string.
        template_engine: Unused by this agent; accepted as every agent does.
        temperature: Sampling temperature.
        max_tokens: The reply's output ceiling.
        condense_config: The budget beyond which text is condensed first;
            ``max_context_chars`` defaults to the Cochrane assessor's
            :data:`~bmlib.quality.cochrane_assessor.DEFAULT_CONDENSE_THRESHOLD_CHARS`.
        num_ctx: The window, in tokens, Ollama loads the model with, sent with
            every request (the condenser's included); ``None`` sends none,
            leaving Ollama's own, often much smaller, default.  Other
            providers ignore it (#640).
        stop: Read before every request, the condenser's included (#642).
            Once it answers true no request is sent, and the call under way
            raises ``PicoError`` ``STOPPED``, whatever else went wrong, except
            what propagates unwrapped (a bmlib defect, a missing SDK; #573).  A
            request already sent runs to its end; a call that then succeeds
            returns its result, so a caller that must not keep it reads the
            stop again.  A callable must not raise.
        condense_progress: Handed every
            :class:`~bmlib.context_processor.ProgressInfo` the condensation of a
            long text reports, so a caller can show how far it got (#609); one
            that raises is logged and the run goes on.
    """

    def __init__(
        self,
        llm: LLMClient,
        model: str,
        template_engine: TemplateEngine | None = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        condense_config: ProcessingConfig | None = None,
        num_ctx: int | None = None,
        stop: Stop | None = None,
        condense_progress: ProgressCallback | None = None,
    ) -> None:
        if num_ctx is not None and (
            isinstance(num_ctx, bool)
            or not isinstance(num_ctx, int)
            or not 0 < num_ctx <= MAX_NUM_CTX
        ):
            raise ValueError(f"num_ctx must be an int in 1..{MAX_NUM_CTX}, got {num_ctx!r}")
        self._num_ctx = num_ctx
        self._stop_requested = stop_reader(stop)
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
        self.condense_progress = condense_progress

    @property
    def num_ctx(self) -> int | None:
        """The window sent with every request; read-only, being checked once."""
        return self._num_ctx

    def stop_requested(self) -> bool:
        """Whether the agent's stop is set."""
        return self._stop_requested()

    def chat(self, messages: list[LLMMessage], **kwargs: Any) -> LLMResponse:
        """:meth:`BaseAgent.chat` with ``num_ctx``, refused once stopped.

        Every request this agent makes passes here: its own (through
        ``chat_json``, whose re-asks are refused too) and the condenser's.

        Raises:
            ChatStoppedError: The stop is set; nothing was sent.
        """
        if self.stop_requested():
            raise ChatStoppedError(STOPPED_MESSAGE)
        if self._num_ctx is not None:
            kwargs["num_ctx"] = self._num_ctx
        return super().chat(messages, **kwargs)

    def prepare(self, title: str | None, text: str | None) -> PicoPrepared:
        """Make a paper ready for both PICO calls, condensing it at most once (#638).

        Hand the result to :meth:`check_suitability_prepared` and
        :meth:`extract_prepared`, and to each retry of either, so a long
        paper's map-reduce is not run again before every call.  It can be
        stored with :meth:`~bmlib.quality.pico_models.PicoPrepared.to_dict`,
        and is for an agent with this one's ``max_context_chars``: another
        refuses it before any request.

        Args:
            title: The paper's title.
            text: The paper's text, which quotes are later verified against.

        Returns:
            The prepared paper.

        Raises:
            PicoError: ``EMPTY_INPUT``, ``CONDENSATION_FAILED``,
                ``REQUEST_FAILED`` (a condensation request, #616) or ``STOPPED``.
            ValueError: The model's provider is unknown.
            ImportError: The provider's SDK is not installed.
        """
        return self._prepare(title, text, "the suitability check and extraction")

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
            ValueError: The model's provider is unknown.
            ImportError: The provider's SDK is not installed.
        """
        return self.check_suitability_prepared(self._prepare(title, text, "the suitability check"))

    def check_suitability_prepared(self, prepared: PicoPrepared) -> PicoSuitability:
        """:meth:`check_suitability` over a paper :meth:`prepare` made ready.

        Raises:
            PicoError: Naming why no answer could be made.
            ValueError: The model's provider is unknown, or *prepared* was
                made for another budget (see :meth:`extract_prepared`).
            ImportError: The provider's SDK is not installed.
        """
        sent = self._sent(prepared)
        suitability = self._ask(
            SUITABILITY_TASK_PROMPT,
            SUITABILITY_RESPONSE_FORMAT,
            prepared.title,
            sent,
            read_suitability,
            f"PICO suitability check of {prepared.title[:60]!r}",
        )
        suitability.condensed_from_chars = prepared.condensed_from_chars
        suitability.condensation_status = prepared.condensation_status
        suitability.notes = list(prepared.notes)
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
            ValueError: *min_similarity* is not a number in (0, 1], or the
                model's provider is unknown.
            ImportError: The provider's SDK is not installed.
            PicoError: Naming why no breakdown could be made.
        """
        # Refused before the condensation, so a caller's mistake costs no request.
        similarity = _check_similarity(min_similarity)
        return self.extract_prepared(
            self._prepare(title, text, "extraction"), min_similarity=similarity
        )

    def extract_prepared(self, prepared: PicoPrepared, *, min_similarity: float) -> PicoExtraction:
        """:meth:`extract` over a paper :meth:`prepare` made ready.

        Quotes are verified against ``prepared.text``, the paper as passed to
        :meth:`prepare`, never the digest.

        Raises:
            ValueError: *min_similarity* is not a number in (0, 1], the
                model's provider is unknown, or *prepared* was made for
                another budget: it sends more than this agent's, or it was
                condensed though this agent would send its text whole.
            ImportError: The provider's SDK is not installed.
            PicoError: Naming why no breakdown could be made.
        """
        similarity = _check_similarity(min_similarity)
        sent = self._sent(prepared)
        extraction = self._ask(
            EXTRACTION_TASK_PROMPT,
            EXTRACTION_RESPONSE_FORMAT,
            prepared.title,
            sent,
            # Quotes are verified against the paper, never the digest: a quote
            # the condensation paraphrased is caught here like any other.
            lambda data: read_extraction(data, prepared.text, similarity),
            f"PICO extraction of {prepared.title[:60]!r}",
        )
        extraction.condensed_from_chars = prepared.condensed_from_chars
        extraction.condensation_status = prepared.condensation_status
        extraction.notes = list(prepared.notes)
        return extraction

    def _prepare(self, title: str | None, text: str | None, stage: str) -> PicoPrepared:
        """Strip the input, refuse an empty one, condense an oversized one.

        Args:
            stage: Names the calls the digest is for in a degraded run's note.
        """
        # Quotes are located in the text as passed, unstripped, so an offset
        # indexes the string the caller will highlight.
        original = text or ""
        title = (title or "").strip()
        text = original.strip()
        if not title and not text:
            raise PicoError(PicoErrorKind.EMPTY_INPUT, "both the title and the text are empty")
        self._check_provider()
        if len(text) <= self.condense_config.max_context_chars:
            return PicoPrepared(title, original)

        self._raise_if_stopped()
        defect: BaseException | None = None
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
                progress_callback=self.condense_progress,
            )
        except ProcessingStoppedError as exc:
            raise PicoError(PicoErrorKind.STOPPED, STOPPED_MESSAGE) from exc
        except CondensationRefusedError as exc:
            self._raise_if_stopped(exc)
            cause = exc.__cause__
            if isinstance(cause, _NOT_A_REQUEST_FAILURE):
                defect = cause
            else:
                message = (
                    f"a {len(text)}-character text could not be condensed to fit "
                    f"{self.condense_config.max_context_chars} characters: {exc}"
                )
                # No request failed it: a truncated pass, an item too large for
                # its strategy, or an empty or long digest.
                if cause is None:
                    raise PicoError(PicoErrorKind.CONDENSATION_FAILED, message) from exc
                raise _request_failed(cause, message) from exc
        if defect is not None:
            # What a short paper's request would have propagated (#616), raised
            # outside the handler so it is neither chained to its own effect
            # nor given that effect as its ``__context__``.
            raise defect
        # A run stopped part-way can finish with a partial digest, which is
        # no digest of the paper.
        self._raise_if_stopped()
        return PicoPrepared(title, original, digest, status.value, tuple(notes))

    def _sent(self, prepared: PicoPrepared) -> str:
        """What *prepared* sends, refused unless this agent would have made it.

        Both directions are refused: a paper prepared for a larger budget does
        not fit, and one condensed for a smaller budget would send a digest
        where this agent sends the whole text.

        Raises:
            ValueError: The provider is unknown, or *prepared* was made for
                another budget.
            ImportError: The provider's SDK is not installed.
        """
        self._check_provider()
        budget = self.condense_config.max_context_chars
        sent = prepared.sent
        if len(sent) > budget:
            raise ValueError(
                f"the prepared text is {len(sent)} characters, over this agent's "
                f"{budget}-character budget: prepare it with this agent"
            )
        whole = prepared.condensed_from_chars
        if whole is not None and whole <= budget:
            raise ValueError(
                f"the prepared text was condensed from {whole} characters, which fit "
                f"this agent's {budget}-character budget whole: prepare it with this agent"
            )
        return sent

    def _check_provider(self) -> None:
        """Resolve the model's provider before any request is made (#573).

        An unknown provider (``ValueError``) and an uninstalled SDK
        (``ImportError``) then propagate whatever the text's length, rather
        than failing the condensation of a long one as an outage would.

        Raises:
            ValueError: No provider is registered under the model's name.
            ImportError: The provider's SDK is not installed.
        """
        provider = self.model.split(":", 1)[0] if ":" in self.model else self.llm.default_provider
        self.llm._get_provider(provider or self.llm.default_provider)

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

        problem: _UnreadableReplyError | None = None
        for attempt in range(_READ_ATTEMPTS):
            self._raise_if_stopped()
            try:
                data = self.chat_json(messages, retry_context=label, require_dict=True)
            except ChatStoppedError as exc:
                raise PicoError(PicoErrorKind.STOPPED, STOPPED_MESSAGE) from exc
            except _NOT_A_REQUEST_FAILURE:
                raise
            except Exception as exc:
                # A stop set during the call wins over whatever it raised.
                self._raise_if_stopped(exc)
                raise _request_error(exc) from exc
            try:
                return read(data)
            except _UnreadableReplyError as exc:
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

    def _raise_if_stopped(self, cause: BaseException | None = None) -> None:
        """Raise ``STOPPED`` when the stop is set, chained to *cause*.

        Raises:
            PicoError: ``STOPPED``.
        """
        if self.stop_requested():
            raise PicoError(PicoErrorKind.STOPPED, STOPPED_MESSAGE) from cause


def _request_error(exc: Exception) -> PicoError:
    """The ``PicoError`` a failed ``chat_json`` call is reported as."""
    if isinstance(exc, ChatJsonTruncatedError):
        return PicoError(PicoErrorKind.TRUNCATED, str(exc))
    if isinstance(exc, ChatJsonError):
        return PicoError(PicoErrorKind.UNUSABLE_REPLY, str(exc))
    return _request_failed(exc, str(exc))


def _request_failed(exc: BaseException, message: str) -> PicoError:
    """``REQUEST_FAILED`` for a request that raised *exc*, saying *message*."""
    return PicoError(
        PicoErrorKind.REQUEST_FAILED, message, is_retryable_request(exc), http_status(exc)
    )
