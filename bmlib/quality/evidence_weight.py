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

"""The unattended evidence-weight pipeline: paper in, number out, nobody confirming.

Piece 6 of the evidence-weight assessment.  :func:`assess_unattended` chains
the earlier pieces for a literature pipeline: design proposal, then (not an
RCT, or not a parallel-group one: stop) primary outcomes, then (none: stop)
RoB 2 over **all** of them, then the evidence ladder per outcome, then the
paper's weight.  Machine proposals are accepted as they come; that is what
"unattended" means.  A reader who confirms each step uses the pieces directly.

A failure is never a score.  An outcome whose risk of bias was not assessed,
whose result was refused, or whose result cannot be laddered has ``ladder``
``None`` and the reason in ``ladder_error``; the paper's weight is the largest
of the ladders that were built (``None`` when there are none).

Rust's ``quality::evidence_weight`` is the same flow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bmlib.context_processor import ProcessingConfig
from bmlib.llm.client import LLMClient
from bmlib.llm.stream import Stop
from bmlib.quality._full_text_agent import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_MAX_TOKENS,
    DEFAULT_TEMPERATURE,
    QueryCondenser,
)
from bmlib.quality.design_agent import DesignAgent, DesignProposal, ProposalError, _wire
from bmlib.quality.evidence_ladder import (
    LADDER_VERSION,
    EvidenceLadder,
    LadderSettings,
    build_ladder,
    paper_weight,
    settings_from_wire,
    settings_to_wire,
    start_level,
)
from bmlib.quality.outcome_agent import OutcomeAgent, OutcomeProposal, ProposedOutcome
from bmlib.quality.rob2_agent import (
    DomainAssessment,
    DomainFailed,
    OutcomeRob2,
    Rob2Agent,
    Rob2Run,
    Rob2RunError,
    _object,
)

STATUS_NOT_COVERED = "not_covered"
"""Wire ``status`` of a paper whose design the RoB 2 core questions do not cover."""

STATUS_NO_PRIMARY_OUTCOME = "no_primary_outcome"
"""Wire ``status`` of a covered trial in which the model found no primary outcome."""

STATUS_ASSESSED = "assessed"
"""Wire ``status`` of a trial taken all the way through."""


class EvidenceWeightError(Exception):
    """Why the pipeline ended without a result: the proposal or RoB 2 step's own error.

    Attributes:
        cause: The :class:`~bmlib.quality.design_agent.ProposalError` or
            :class:`~bmlib.quality.rob2_agent.Rob2RunError` itself; a
            ``ProposalError`` carries ``retryable`` and ``status`` for a failed
            request, so ``error.cause.retryable`` says whether to try again.
    """

    def __init__(self, cause: ProposalError | Rob2RunError) -> None:
        super().__init__(str(cause))
        self.cause = cause


@dataclass
class AssessedOutcome:
    """One primary outcome taken through RoB 2 and the ladder.

    Attributes:
        outcome: The proposed outcome.
        rob2: Its RoB 2 result.
        ladder: Its ladder; ``None`` when ``rob2.overall`` is ``None`` or the
            result was refused (then see *ladder_error*).
        ladder_error: Why there is no ladder; ``None`` when there is one.
    """

    outcome: ProposedOutcome
    rob2: OutcomeRob2
    ladder: EvidenceLadder | None = None
    ladder_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-ready dict (Rust's wire form)."""
        return {
            "outcome": self.outcome.to_dict(),
            "rob2": self.rob2.to_dict(),
            "ladder": None if self.ladder is None else self.ladder.to_wire(),
            "ladder_error": self.ladder_error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AssessedOutcome:
        """Rebuild from :meth:`to_dict` output.

        Raises:
            ValueError: A field is absent or of the wrong type or value, or a
                ladder and a ladder error are both given or both absent.
        """
        data = _object(data, "the assessed outcome")
        raw, error = data.get("ladder"), data.get("ladder_error")
        if error is not None and not isinstance(error, str):
            raise ValueError("ladder_error is not text")
        if (raw is None) == (error is None):
            raise ValueError("exactly one of ladder and ladder_error is set")
        return cls(
            outcome=ProposedOutcome.from_dict(_object(data.get("outcome"), "outcome")),
            rob2=OutcomeRob2.from_dict(_object(data.get("rob2"), "rob2")),
            ladder=None if raw is None else EvidenceLadder.from_wire(raw),
            ladder_error=error,
        )


@dataclass
class NotCovered:
    """The paper is not a parallel-group randomised trial; nothing more was asked.

    Attributes:
        design: The design proposal that said so.
    """

    design: DesignProposal

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-ready dict, tagged ``"status": "not_covered"``."""
        return {"status": STATUS_NOT_COVERED, "design": self.design.to_dict()}


@dataclass
class NoPrimaryOutcome:
    """A covered trial in which no primary outcome was found; RoB 2 was not asked.

    Attributes:
        design: The design proposal.
        outcomes: The (empty) outcome proposal, which may still say it was condensed.
    """

    design: DesignProposal
    outcomes: OutcomeProposal

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-ready dict, tagged ``"status": "no_primary_outcome"``."""
        return {
            "status": STATUS_NO_PRIMARY_OUTCOME,
            "design": self.design.to_dict(),
            "outcomes": self.outcomes.to_dict(),
        }


@dataclass
class Assessed:
    """A covered trial taken through every step.

    Attributes:
        design: The design proposal.
        outcomes: The outcome proposal (never empty).
        run: The RoB 2 run over all its outcomes.
        assessed: One entry per outcome, in order.
        paper_weight: The largest ladder weight, ``None`` when no ladder was built.
        condensed: Whether the model saw a digest in any step.
        ladder_version: The ladder rules that built the ladders (:data:`LADDER_VERSION`).
        settings: The ladder's numbers they were built with.
    """

    design: DesignProposal
    outcomes: OutcomeProposal
    run: Rob2Run
    assessed: list[AssessedOutcome] = field(default_factory=list)
    paper_weight: float | None = None
    condensed: bool = False
    ladder_version: str = LADDER_VERSION
    settings: LadderSettings = field(default_factory=LadderSettings)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-ready dict, tagged ``"status": "assessed"``."""
        return {
            "status": STATUS_ASSESSED,
            "design": self.design.to_dict(),
            "outcomes": self.outcomes.to_dict(),
            "run": self.run.to_dict(),
            "assessed": [a.to_dict() for a in self.assessed],
            "paper_weight": self.paper_weight,
            "condensed": self.condensed,
            "ladder_version": self.ladder_version,
            "settings": settings_to_wire(self.settings),
        }


EvidenceWeight = NotCovered | NoPrimaryOutcome | Assessed
"""What :func:`assess_unattended` returns."""


def evidence_weight_from_dict(data: dict[str, Any]) -> EvidenceWeight:
    """Rebuild any :data:`EvidenceWeight` from its ``to_dict`` output, by ``status``.

    Raises:
        ValueError: ``status`` is unknown, ``ladder_version`` is not
            :data:`LADDER_VERSION`, the settings are refused, a field is absent
            or of the wrong type or value, or the parts disagree (a different number of
            outcomes, RoB 2 results or assessed outcomes; a ``paper_weight``
            that is not the largest ladder weight).
    """
    data = _object(data, "the evidence weight")
    status = _wire(data, "status", str, "text")
    design = DesignProposal.from_dict(_object(data.get("design"), "design"))
    if status == STATUS_NOT_COVERED:
        return NotCovered(design)
    outcomes = OutcomeProposal.from_dict(_object(data.get("outcomes"), "outcomes"))
    if status == STATUS_NO_PRIMARY_OUTCOME:
        return NoPrimaryOutcome(design, outcomes)
    if status != STATUS_ASSESSED:
        raise ValueError(
            f'status "{status}" is not {STATUS_NOT_COVERED}, '
            f"{STATUS_NO_PRIMARY_OUTCOME} or {STATUS_ASSESSED}"
        )
    version = _wire(data, "ladder_version", str, "text")
    if version != LADDER_VERSION:
        raise ValueError(f"ladder_version {version} is not {LADDER_VERSION}")
    settings = settings_from_wire(data.get("settings"))
    run = Rob2Run.from_dict(_object(data.get("run"), "run"))
    assessed = [AssessedOutcome.from_dict(a) for a in _wire(data, "assessed", list, "a list")]
    weight = data.get("paper_weight")
    if len(assessed) != len(run.outcomes) or len(assessed) != len(outcomes.outcomes):
        raise ValueError("outcomes, run and assessed do not have the same length")
    for index, (one, proposed, rob2) in enumerate(
        zip(assessed, outcomes.outcomes, run.outcomes, strict=True)
    ):
        if one.outcome != proposed or one.rob2 != rob2:
            raise ValueError(f"assessed[{index}] is not outcome {index} of outcomes and run")
    expected = paper_weight([a.ladder for a in assessed if a.ladder is not None])
    if weight != expected:
        raise ValueError(f"paper_weight {weight!r} is not the largest ladder weight {expected!r}")
    return Assessed(
        design,
        outcomes,
        run,
        assessed,
        expected,
        _wire(data, "condensed", bool, "a boolean"),
        version,
        settings,
    )


def _not_assessed(study: Any, rob2: OutcomeRob2) -> str:
    """The ladder error of an outcome whose overall judgement is ``None``, naming why if known."""
    failed = [
        f"{d.domain.value} failed ({d.reason})"
        for d in [study, *rob2.domains]
        if isinstance(d, DomainFailed)
    ]
    if rob2.escalation_error is not None:
        failed.append(f"the escalation question failed ({rob2.escalation_error})")
    base = "the outcome's risk of bias was not assessed"
    return f"{base}: {'; '.join(failed)}" if failed else base


def _ladder_error(study: Any, outcome: ProposedOutcome, rob2: OutcomeRob2) -> str | None:
    """Why no ladder can be built for this outcome, or ``None`` when one can."""
    if rob2.overall is None:
        return _not_assessed(study, rob2)
    if outcome.result_error is not None:
        return f"the reported result was refused: {outcome.result_error}"
    return None


def _any_condensed(design: DesignProposal, outcomes: OutcomeProposal, run: Rob2Run) -> bool:
    """Whether the model saw a digest in the design, outcome or any RoB 2 step."""
    domains = [run.study] + [d for o in run.outcomes for d in o.domains]
    return (
        design.condensed
        or outcomes.condensed
        or any(isinstance(d, DomainAssessment) and d.condensed for d in domains)
    )


def assess_unattended(
    llm_client: LLMClient,
    model: str,
    *,
    settings: LadderSettings,
    title: str | None,
    text: str | None,
    min_similarity: float,
    stop: Stop | None = None,
    condenser: QueryCondenser | None = None,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    max_retries: int = DEFAULT_MAX_RETRIES,
    condense_config: ProcessingConfig | None = None,
    num_ctx: int | None = None,
) -> EvidenceWeight:
    """Take one paper from full text to its evidence weight, with nobody confirming.

    Args:
        llm_client: The client requests go through.
        model: The ``"provider:model"`` string, used for every step.
        settings: The ladder's numbers.
        title: The paper's title.
        text: The paper's full text.  Citation offsets are code points into it.
        min_similarity: How close a quote must be to a span of *text*.
        stop: Read before every request; once true the run ends.
        condenser: Condenses an oversized paper; defaults to the model itself.
        temperature: Sampling temperature of every step.
        max_tokens: The reply's output ceiling in every step.
        max_retries: ``chat_json``'s attempts per question in every step.
        condense_config: The budget beyond which a step condenses the paper;
            ``None`` is the agents' default.
        num_ctx: The window sent with every request (Ollama); ``None`` sends none.

    Every option reaches all three agents (design, outcomes, RoB 2).

    Returns:
        :class:`NotCovered` (not a parallel-group RCT: one request made),
        :class:`NoPrimaryOutcome`, or :class:`Assessed`.

    Raises:
        EvidenceWeightError: A step could not be made at all (empty text, bad
            ``min_similarity``, stop, an unreadable proposal), wrapping its own error.
        ValueError: The model's provider is unknown, or *num_ctx* is out of range.
        ImportError: The provider's SDK is not installed.
    """
    kwargs: dict[str, Any] = {
        "stop": stop,
        "condenser": condenser,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "max_retries": max_retries,
        "condense_config": condense_config,
        "num_ctx": num_ctx,
    }
    try:
        design = DesignAgent(llm_client, model, **kwargs).propose(
            title, text, min_similarity=min_similarity
        )
        start = start_level(design.design)
        if start is None or design.rct_layout is None or not design.rct_layout.is_covered():
            return NotCovered(design)
        outcomes = OutcomeAgent(llm_client, model, **kwargs).propose(
            title, text, min_similarity=min_similarity
        )
        if not outcomes.outcomes:
            return NoPrimaryOutcome(design, outcomes)
        run = Rob2Agent(llm_client, model, **kwargs).assess(
            title, text, outcomes.outcomes, min_similarity=min_similarity
        )
    except (ProposalError, Rob2RunError) as exc:
        raise EvidenceWeightError(exc) from exc

    assessed = []
    for proposed, rob2 in zip(outcomes.outcomes, run.outcomes, strict=True):
        error = _ladder_error(run.study, proposed, rob2)
        ladder = None
        if error is None:
            assert rob2.overall is not None
            try:
                ladder = build_ladder(start, rob2.overall, proposed.result, settings)
            except ValueError as exc:
                error = str(exc)
        assessed.append(AssessedOutcome(proposed, rob2, ladder, error))
    weight = paper_weight([a.ladder for a in assessed if a.ladder is not None])
    condensed = _any_condensed(design, outcomes, run)
    return Assessed(design, outcomes, run, assessed, weight, condensed, LADDER_VERSION, settings)
