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
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from bmlib.quality._full_text_agent import (
    LABEL_CHARS,
    AgentFailure,
    LlmQueryCondenser,
    QueryCondenser,
    UnreadableReplyError,
    _FullTextAgent,
    check_min_similarity,
)
from bmlib.quality.data_models import STUDY_DESIGN_MAPPING, RctLayout, StudyDesign
from bmlib.quality.evidence_ladder import U32_MAX
from bmlib.quality.pico_agent import verify_quotes
from bmlib.quality.pico_models import PicoCitation, PicoContradictedQuote

__all__ = [
    "DESIGN_CONDENSE_QUERY",
    "DESIGN_PROMPT_VERSION",
    "DESIGN_SYSTEM_PROMPT",
    "DESIGN_TASK_PROMPT",
    "DesignAgent",
    "DesignProposal",
    "LlmQueryCondenser",
    "ProposalError",
    "ProposalErrorKind",
    "QueryCondenser",
    "read_design_reply",
]

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


def _wire(data: dict[str, Any], key: str, kind: type, what: str) -> Any:
    """``data[key]`` if it is a *kind* (a ``bool`` is only a bool), else ``ValueError``."""
    value = data.get(key)
    if not isinstance(value, kind) or (isinstance(value, bool) and kind is not bool):
        raise ValueError(f"{key} is not {what}")
    return value


def _prompt_version(data: dict[str, Any]) -> int:
    """``data["prompt_version"]`` if it is a whole number that fits 32 bits, else ``ValueError``."""
    version = _wire(data, "prompt_version", int, "a whole number")
    if not 0 <= version <= U32_MAX:
        raise ValueError("prompt_version is not a whole number that fits 32 bits")
    return int(version)


@dataclass
class DesignProposal:
    """The model's reading of the paper's own design, backed by its passages.

    Attributes:
        design: The study design; never ``UNKNOWN``.
        rct_layout: Set exactly when *design* is ``RCT``.
        citations: The paper's own spans for the quotes found, offsets in code
            points into the text passed.
        quotes_not_found: The quotes not located in the paper, verbatim.
        contradicted: The quotes the paper contradicts (a changed number,
            negation or direction), each with the reason; never also in
            ``quotes_not_found``.
        quotes_dropped: How many quote members were not usable text (blank, or
            not a string) and so were never searched for.
        reasoning: The model's explanation, ``""`` when absent.
        condensed: Whether the model saw a digest instead of the whole paper.
        prompt_version: The wording that asked for it (:data:`DESIGN_PROMPT_VERSION`).
    """

    design: StudyDesign
    rct_layout: RctLayout | None
    citations: list[PicoCitation] = field(default_factory=list)
    quotes_not_found: list[str] = field(default_factory=list)
    contradicted: list[PicoContradictedQuote] = field(default_factory=list)
    quotes_dropped: int = 0
    reasoning: str = ""
    condensed: bool = False
    prompt_version: int = DESIGN_PROMPT_VERSION

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-ready dict (Rust's wire form)."""
        return {
            "design": self.design.value,
            "rct_layout": None if self.rct_layout is None else self.rct_layout.value,
            "citations": [c.to_dict() for c in self.citations],
            "quotes_not_found": list(self.quotes_not_found),
            "contradicted": [c.to_dict() for c in self.contradicted],
            "quotes_dropped": self.quotes_dropped,
            "reasoning": self.reasoning,
            "condensed": self.condensed,
            "prompt_version": self.prompt_version,
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
        contradicted = _wire(data, "contradicted", list, "a list")
        dropped = _wire(data, "quotes_dropped", int, "a count")
        if dropped < 0:
            raise ValueError("quotes_dropped is not a count")
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
            contradicted=[PicoContradictedQuote.from_dict(c) for c in contradicted],
            quotes_dropped=dropped,
            reasoning=_wire(data, "reasoning", str, "text"),
            condensed=_wire(data, "condensed", bool, "a boolean"),
            prompt_version=_prompt_version(data),
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
        UnreadableReplyError: (a ``ValueError``) The reply is not an object,
            the design is not a label the study-design lookup knows (or is
            ``unknown``), the layout is missing for an RCT, given for another
            design or not a layout, or ``reasoning`` is not text.
    """
    similarity = check_min_similarity(min_similarity)
    if not isinstance(data, dict):
        raise UnreadableReplyError("expected an object")

    label = data.get("study_design")
    if not isinstance(label, str):
        raise UnreadableReplyError("study_design is not text")
    design = STUDY_DESIGN_MAPPING.get(label.lower().strip(), StudyDesign.UNKNOWN)
    if design is StudyDesign.UNKNOWN:
        raise UnreadableReplyError(f'study_design "{label}" is not a design label')

    raw_layout = data.get("rct_layout")
    layout: RctLayout | None = None
    if design is not StudyDesign.RCT:
        if raw_layout is not None:
            raise UnreadableReplyError("rct_layout given for a non-RCT")
    elif raw_layout is None:
        raise UnreadableReplyError("rct_layout missing")
    else:
        if not isinstance(raw_layout, str):
            raise UnreadableReplyError("rct_layout is not text")
        try:
            layout = RctLayout(raw_layout.lower().strip())
        except ValueError:
            raise UnreadableReplyError(f'rct_layout "{raw_layout}" is not a layout') from None

    reasoning = data.get("reasoning")
    if reasoning is None:
        reasoning = ""
    elif not isinstance(reasoning, str):
        raise UnreadableReplyError("reasoning is not text")

    citations, not_found, contradicted, dropped = verify_quotes(
        data.get("quotes"), text, similarity
    )
    return DesignProposal(
        design, layout, citations, not_found, contradicted, dropped, reasoning, False
    )


class DesignAgent(_FullTextAgent):
    """Proposes the paper's own study design, backed by passages of the paper.

    Takes the arguments of :class:`~bmlib.quality._full_text_agent._FullTextAgent`:
    the client, the model, sampling, the condensation budget, ``num_ctx``, a
    ``stop`` and an optional :class:`QueryCondenser`.
    """

    def _error(self, failure: AgentFailure, message: str) -> Exception:
        """Name *failure* as this agent's :class:`ProposalError`."""
        return ProposalError(ProposalErrorKind(failure.value), message)

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
            similarity = check_min_similarity(min_similarity)
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
            sent = self._condense_once(
                title, stripped, DESIGN_CONDENSE_QUERY, "the design proposal"
            )
            condensed = True

        prompt = f"{DESIGN_TASK_PROMPT}\n\nPaper Title: {title}\n\nPaper Text:\n{sent}"
        label = f"design proposal of {title[:LABEL_CHARS]!r}"
        proposal = self._ask_until_readable(
            DESIGN_SYSTEM_PROMPT,
            prompt,
            lambda data: read_design_reply(data, original, similarity),
            label,
        )
        proposal.condensed = condensed
        return proposal
