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

"""The design proposal: what kind of study this paper reports.

Piece 2 of the evidence-weight assessment.  One question put to the model over
the paper's full text: its own study design, and for a randomised trial how it
allocated participants (:class:`~bmlib.quality.data_models.RctLayout`).  The
model copies the passages that show it; each is located by
:func:`bmlib.citations.find_passage` in the paper as passed, and the paper's
own span is kept.

Nothing is truncated: a paper over the window is condensed once, by the same
verbatim-passage map-reduce the PICO agent uses but gathering what shows the
design (:data:`DESIGN_CONDENSE_QUERY`), and the result says so
(``condensed``).  Quotes are verified against the original text, never the
digest.  An answer that cannot be read, or names a design or layout outside
the allowed set, fails with :class:`ProposalError`; it is never read as
``unknown`` or guessed.

The rule set and the replies are shared with Rust's
``quality::design_agent`` through ``tests/fixtures/design_replies.json``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from bmlib.agents.base import (
    BaseAgent,
    ChatJsonError,
    ChatJsonTruncatedError,
    ChatStoppedError,
)
from bmlib.context_processor import ProcessingConfig, ProcessingStatus, ProcessingStoppedError
from bmlib.llm import LLMClient, LLMMessage, LLMResponse
from bmlib.llm.providers.ollama import MAX_NUM_CTX
from bmlib.llm.stream import Stop, stop_reader
from bmlib.quality._condense import CondensationRefusedError, condense_text
from bmlib.quality.cochrane_assessor import DEFAULT_CONDENSE_THRESHOLD_CHARS
from bmlib.quality.data_models import STUDY_DESIGN_MAPPING, RctLayout, StudyDesign
from bmlib.quality.pico_agent import (
    _NOT_A_REQUEST_FAILURE,
    _READ_ATTEMPTS,
    PICO_CONDENSE_CONSOLIDATION_PROMPT,
    PICO_CONDENSE_EXTRACTION_PROMPT,
    STOPPED_MESSAGE,
    _check_similarity,
    verify_quotes,
)
from bmlib.quality.pico_models import PicoCitation
from bmlib.templates import TemplateEngine

logger = logging.getLogger(__name__)

#: Bumped whenever the prompts below change, so a stored proposal says which
#: wording produced it.
DESIGN_PROMPT_VERSION = 1

#: The system turn.
DESIGN_SYSTEM_PROMPT = """\
You are an expert in clinical research methodology and evidence-based medicine.

CRITICAL RULES:
1. Base everything ONLY on what is ACTUALLY PRESENT in the text
2. DO NOT invent, assume, or fabricate any information
3. Classify THIS study's own design, not the designs of studies it cites or reviews
4. Return ONLY valid JSON, no explanation"""

#: The question, with the reply shape; the paper follows it.
DESIGN_TASK_PROMPT = """\
Classify the study design of the research paper below.

Give the design of the study THIS paper reports. A paper that cites randomised \
trials, cohort studies or other designs is not itself one of them, unless it \
is a systematic review or meta-analysis of them.

study_design must be exactly one of:
- "systematic_review", "meta_analysis"
- "rct" (a randomised controlled trial)
- "cohort_prospective", "cohort_retrospective", "case_control", "cross_sectional"
- "case_series", "case_report"
- "guideline", "editorial", "letter", "comment"
- "other" (a design not listed)

rct_layout: required when study_design is "rct", and null for every other design. \
Exactly one of:
- "parallel": participants are randomised individually to parallel groups
- "cluster": groups of participants (clinics, schools, villages) are randomised
- "crossover": each participant receives the arms in turn
- "other": any other randomised layout

quotes: 1-3 passages copied VERBATIM from the paper that show the design (for \
example the sentence describing how participants were allocated). Copy them \
character for character; do not paraphrase, shorten with ellipses, or join \
sentences.

reasoning: one or two sentences saying why.

Respond with JSON in exactly this shape:

{
    "study_design": "rct",
    "rct_layout": "parallel",
    "quotes": ["Participants were randomly assigned 1:1 to metformin or placebo."],
    "reasoning": "The authors randomised individual participants to two parallel arms."
}"""

#: What the condensation gathers when the paper does not fit the window.
DESIGN_CONDENSE_QUERY = (
    "Everything that shows the study's own design: how participants were "
    "recruited and allocated (randomisation method, unit of randomisation, "
    "crossover or cluster allocation), whether the study followed people "
    "forward or looked back, whether it compared groups, and how the authors "
    "themselves describe the study type."
)

#: How much of the title names the paper in a log line or a condensation label.
_LABEL_CHARS = 60


class ProposalErrorKind(Enum):
    """Why a proposal (this one's, or a later piece's) could not be made."""

    #: The paper's text was empty; no request was made.
    EMPTY_INPUT = "empty_input"
    #: The paper did not fit and could not be condensed to fit.
    CONDENSATION_FAILED = "condensation_failed"
    #: The provider failed or refused a request.
    REQUEST_FAILED = "request_failed"
    #: Every reply was unusable or cut off, or named something outside the allowed set.
    UNREADABLE = "unreadable"
    #: The caller's stop was set.
    STOPPED = "stopped"
    #: ``min_similarity`` was not a number in (0, 1]; no request was made.
    INVALID_SIMILARITY = "invalid_similarity"


class ProposalError(Exception):
    """A proposal that could not be made; ``kind`` says why, the message says more."""

    def __init__(self, kind: ProposalErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind


class _UnreadableReplyError(ValueError):
    """A reply that parsed but cannot be read; asking again may help."""


class QueryCondenser(Protocol):
    """Condenses an oversized text for one stated need; the seam later pieces share."""

    def condense(
        self, query: str, text: str, label: str, config: ProcessingConfig, *, stage: str
    ) -> tuple[str, list[str], ProcessingStatus]:
        """Return ``(digest, notes, status)`` or raise ``CondensationRefusedError``."""
        ...


class LlmQueryCondenser:
    """The production :class:`QueryCondenser`: the PICO verbatim-passage map-reduce.

    Args:
        agent: The agent whose model, stop and ``num_ctx`` the requests use.
        progress: Handed every progress report of the map-reduce.
    """

    def __init__(self, agent: BaseAgent, progress: Any = None) -> None:
        self._agent = agent
        self._progress = progress

    def condense(
        self, query: str, text: str, label: str, config: ProcessingConfig, *, stage: str
    ) -> tuple[str, list[str], ProcessingStatus]:
        """:func:`~bmlib.quality._condense.condense_text` over *query*."""
        return condense_text(
            self._agent,
            text,
            label,
            config,
            extraction_prompt=PICO_CONDENSE_EXTRACTION_PROMPT,
            consolidation_prompt=PICO_CONDENSE_CONSOLIDATION_PROMPT,
            query=query,
            stage=stage,
            progress_callback=self._progress,
        )


def _wire(data: dict[str, Any], key: str, kind: type, what: str) -> Any:
    """``data[key]`` if it is a *kind* (a ``bool`` is only a bool), else ``ValueError``."""
    value = data.get(key)
    if not isinstance(value, kind) or (isinstance(value, bool) and kind is not bool):
        raise ValueError(f"{key} is not {what}")
    return value


@dataclass
class DesignProposal:
    """The model's reading of the paper's own design, backed by its passages.

    Attributes:
        design: The study design; never ``UNKNOWN``.
        rct_layout: Set exactly when *design* is ``RCT``.
        citations: The paper's own spans for the quotes found, offsets in code
            points into the text passed.
        quotes_not_found: The quotes not located, or contradicted by the paper,
            verbatim.
        reasoning: The model's explanation, ``""`` when absent.
        condensed: Whether the model saw a digest instead of the whole paper.
    """

    design: StudyDesign
    rct_layout: RctLayout | None
    citations: list[PicoCitation] = field(default_factory=list)
    quotes_not_found: list[str] = field(default_factory=list)
    reasoning: str = ""
    condensed: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-ready dict (Rust's wire form)."""
        return {
            "design": self.design.value,
            "rct_layout": None if self.rct_layout is None else self.rct_layout.value,
            "citations": [c.to_dict() for c in self.citations],
            "quotes_not_found": list(self.quotes_not_found),
            "reasoning": self.reasoning,
            "condensed": self.condensed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DesignProposal:
        """Rebuild from :meth:`to_dict` output.

        Raises:
            ValueError: A field is absent or of the wrong type or value, naming it.
        """
        design = _wire(data, "design", str, "text")
        layout = data.get("rct_layout")
        if layout is not None and not isinstance(layout, str):
            raise ValueError("rct_layout is not text")
        citations = _wire(data, "citations", list, "a list")
        not_found = _wire(data, "quotes_not_found", list, "a list")
        if not all(isinstance(q, str) for q in not_found):
            raise ValueError("quotes_not_found is not a list of text")
        try:
            parsed_design = StudyDesign(design)
        except ValueError:
            raise ValueError(f'design "{design}" is not a design') from None
        try:
            parsed_layout = None if layout is None else RctLayout(layout)
        except ValueError:
            raise ValueError(f'rct_layout "{layout}" is not a layout') from None
        return cls(
            design=parsed_design,
            rct_layout=parsed_layout,
            citations=[PicoCitation.from_dict(c) for c in citations],
            quotes_not_found=list(not_found),
            reasoning=_wire(data, "reasoning", str, "text"),
            condensed=_wire(data, "condensed", bool, "a boolean"),
        )


def read_design_reply(data: dict[str, Any], text: str, min_similarity: float) -> DesignProposal:
    """Read the model's reply and verify its quotes against *text*.

    Args:
        data: The parsed reply.
        text: The paper as passed; citation offsets index it.
        min_similarity: :func:`~bmlib.citations.find_passage`'s threshold.

    Returns:
        The proposal, ``condensed`` false.

    Raises:
        ValueError: *min_similarity* is not a number in (0, 1].
        _UnreadableReplyError: (a ``ValueError``) The reply is not an object,
            the design is not a label the study-design lookup knows (or is
            ``unknown``), the layout is missing for an RCT, given for another
            design or not a layout, or ``reasoning`` is not text.
    """
    similarity = _check_similarity(min_similarity)
    if not isinstance(data, dict):
        raise _UnreadableReplyError("expected an object")

    label = data.get("study_design")
    if not isinstance(label, str):
        raise _UnreadableReplyError("study_design is not text")
    design = STUDY_DESIGN_MAPPING.get(label.lower().strip(), StudyDesign.UNKNOWN)
    if design is StudyDesign.UNKNOWN:
        raise _UnreadableReplyError(f'study_design "{label}" is not a design label')

    raw_layout = data.get("rct_layout")
    layout: RctLayout | None = None
    if design is not StudyDesign.RCT:
        if raw_layout is not None:
            raise _UnreadableReplyError("rct_layout given for a non-RCT")
    elif raw_layout is None:
        raise _UnreadableReplyError("rct_layout missing")
    else:
        if not isinstance(raw_layout, str):
            raise _UnreadableReplyError("rct_layout is not text")
        try:
            layout = RctLayout(raw_layout.lower().strip())
        except ValueError:
            raise _UnreadableReplyError(f'rct_layout "{raw_layout}" is not a layout') from None

    reasoning = data.get("reasoning")
    if reasoning is None:
        reasoning = ""
    elif not isinstance(reasoning, str):
        raise _UnreadableReplyError("reasoning is not text")

    citations, not_found, contradicted, _dropped = verify_quotes(
        data.get("quotes"), text, similarity
    )
    # A quote the paper contradicts is not in the paper as stated: listed, not dropped.
    not_found = [*not_found, *(c.quote for c in contradicted)]
    return DesignProposal(design, layout, citations, not_found, reasoning, False)


class DesignAgent(BaseAgent):
    """Proposes the paper's own study design, backed by passages of the paper.

    Args:
        llm: The client requests go through.
        model: The ``"provider:model"`` string.
        template_engine: Unused; accepted as every agent does.
        temperature: Sampling temperature.
        max_tokens: The reply's output ceiling.
        condense_config: The budget beyond which text is condensed first.
        num_ctx: The window sent with every request (Ollama); ``None`` sends none.
        stop: Read before every request; once true no request is sent.
        condense_progress: Handed every progress report of a condensation.
        condenser: Condenses an oversized paper; defaults to
            :class:`LlmQueryCondenser` over this agent.
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
        condense_progress: Any = None,
        condenser: QueryCondenser | None = None,
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
        self.condenser: QueryCondenser = condenser or LlmQueryCondenser(self, condense_progress)

    def chat(self, messages: list[LLMMessage], **kwargs: Any) -> LLMResponse:
        """:meth:`BaseAgent.chat` with ``num_ctx``, refused once stopped."""
        if self._stop_requested():
            raise ChatStoppedError(STOPPED_MESSAGE)
        if self._num_ctx is not None:
            kwargs["num_ctx"] = self._num_ctx
        return super().chat(messages, **kwargs)

    def propose(
        self, title: str | None, text: str | None, *, min_similarity: float
    ) -> DesignProposal:
        """Propose the paper's own design.

        Args:
            title: The paper's title.
            text: The paper's full text.  Citation offsets are code points into
                it, exactly as passed.
            min_similarity: How close a quote must be to a span of *text*.

        Returns:
            The proposal; ``condensed`` is true when the model saw a digest.

        Raises:
            ProposalError: Naming why no proposal could be made.
            ValueError: The model's provider is unknown.
            ImportError: The provider's SDK is not installed.
        """
        try:
            similarity = _check_similarity(min_similarity)
        except ValueError as exc:
            raise ProposalError(ProposalErrorKind.INVALID_SIMILARITY, str(exc)) from exc
        original = text or ""
        stripped = original.strip()
        title = (title or "").strip()
        if not stripped:
            raise ProposalError(ProposalErrorKind.EMPTY_INPUT, "the paper's text is empty")
        self._check_provider()
        self._raise_if_stopped()

        sent, condensed = stripped, False
        if len(stripped) > self.condense_config.max_context_chars:
            sent, condensed = self._condense(title, stripped), True

        prompt = f"{DESIGN_TASK_PROMPT}\n\nPaper Title: {title}\n\nPaper Text:\n{sent}"
        label = f"design proposal of {title[:_LABEL_CHARS]!r}"
        proposal = self._ask(
            prompt, lambda data: read_design_reply(data, original, similarity), label
        )
        proposal.condensed = condensed
        return proposal

    def _condense(self, title: str, text: str) -> str:
        """Condense *text* once, as the PICO agent does, with the design query."""
        self._raise_if_stopped()
        defect: BaseException | None = None
        try:
            digest, _notes, _status = self.condenser.condense(
                DESIGN_CONDENSE_QUERY,
                text,
                title[:_LABEL_CHARS],
                self.condense_config,
                stage="the design proposal",
            )
        except ProcessingStoppedError as exc:
            raise ProposalError(ProposalErrorKind.STOPPED, STOPPED_MESSAGE) from exc
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
                kind = (
                    ProposalErrorKind.CONDENSATION_FAILED
                    if cause is None
                    else ProposalErrorKind.REQUEST_FAILED
                )
                raise ProposalError(kind, message) from exc
        if defect is not None:
            raise defect
        # A run stopped part-way can finish with a partial digest, which is no digest.
        self._raise_if_stopped()
        return digest

    def _check_provider(self) -> None:
        """Resolve the model's provider before any request (unknown provider, missing SDK)."""
        provider = self.model.split(":", 1)[0] if ":" in self.model else self.llm.default_provider
        self.llm._get_provider(provider or self.llm.default_provider)

    def _ask(
        self, prompt: str, read: Callable[[dict[str, Any]], DesignProposal], label: str
    ) -> DesignProposal:
        """Send the question and read the reply, asking again once if it is unreadable."""
        messages = [self.system_msg(DESIGN_SYSTEM_PROMPT), self.user_msg(prompt)]
        problem: _UnreadableReplyError | None = None
        for attempt in range(_READ_ATTEMPTS):
            self._raise_if_stopped()
            try:
                data = self.chat_json(messages, retry_context=label, require_dict=True)
            except ChatStoppedError as exc:
                raise ProposalError(ProposalErrorKind.STOPPED, STOPPED_MESSAGE) from exc
            except _NOT_A_REQUEST_FAILURE:
                raise
            except Exception as exc:
                self._raise_if_stopped(exc)
                if isinstance(exc, ChatJsonTruncatedError | ChatJsonError):
                    raise ProposalError(ProposalErrorKind.UNREADABLE, str(exc)) from exc
                raise ProposalError(ProposalErrorKind.REQUEST_FAILED, str(exc)) from exc
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
        raise ProposalError(
            ProposalErrorKind.UNREADABLE,
            f"{label}: {_READ_ATTEMPTS} replies could not be read: {problem}",
        ) from problem

    def _raise_if_stopped(self, cause: BaseException | None = None) -> None:
        """Raise ``STOPPED`` when the stop is set, chained to *cause*."""
        if self._stop_requested():
            raise ProposalError(ProposalErrorKind.STOPPED, STOPPED_MESSAGE) from cause
