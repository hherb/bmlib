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

"""The primary-outcome proposal: which outcomes the paper calls primary, and their results.

Piece 3 of the evidence-weight assessment.  One question put to the model over
the paper's full text: the outcomes the paper itself calls primary (co-primary
allowed), when each was measured, and the effect reported for the primary
analysis (:class:`~bmlib.quality.evidence_ladder.ReportedResult`).  The model
copies the passages that show them; each is located by
:func:`bmlib.citations.find_passage` in the paper as passed, and the paper's
own span is kept.

Nothing is truncated: a paper over the window is condensed once, gathering the
primary outcomes and their results (:data:`OUTCOME_CONDENSE_QUERY`), and the
proposal says so (``condensed``).  Quotes are verified against the original
text, never the digest.  An empty list is a valid answer (the paper names no
primary outcome).  A reply that cannot be read, or whose result names a scale
outside ``ratio``/``difference`` or holds something that is not a number, fails
with :class:`~bmlib.quality.design_agent.ProposalError`.  A result that reads
but is incoherent (a bound the wrong way round, a ratio at or below 0) is kept
out of ``result`` with its reason in ``result_error``; it is never repaired and
no ladder is built from it.

The replies are shared with Rust's ``quality::outcome_agent`` through
``tests/fixtures/outcome_replies.json`` (written by
``scripts/make_outcome_replies.py``).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

from bmlib.quality._full_text_agent import (
    LABEL_CHARS,
    AgentFailure,
    UnreadableReplyError,
    _FullTextAgent,
    check_min_similarity,
)
from bmlib.quality.design_agent import ProposalError, ProposalErrorKind, _prompt_version
from bmlib.quality.evidence_ladder import U32_MAX, EffectScale, ReportedResult
from bmlib.quality.pico_agent import verify_quotes
from bmlib.quality.pico_models import PicoCitation, PicoContradictedQuote

__all__ = [
    "OUTCOME_CONDENSE_QUERY",
    "OUTCOME_PROMPT_VERSION",
    "OUTCOME_SYSTEM_PROMPT",
    "OUTCOME_TASK_PROMPT",
    "OutcomeAgent",
    "OutcomeProposal",
    "ProposedOutcome",
    "read_outcome_reply",
]

logger = logging.getLogger(__name__)

#: Bumped whenever the prompts below change, so a stored proposal says which
#: wording produced it.
OUTCOME_PROMPT_VERSION = 1

#: Message for an ``n_analysed`` that is not a count that fits 32 bits (the
#: same words as :meth:`ReportedResult.validate`).
_N_ANALYSED_RANGE = "n_analysed must be a whole number that fits 32 bits"

#: A level printed as a percentage (``95`` for "95% CI") is divided by this.
PERCENT = 100

#: Message for a ``ci_level`` that is neither a percentage nor a fraction.
_CI_LEVEL_RANGE = "ci_level is neither a percentage in (1, 100) nor a fraction in (0, 1)"

#: The result's number fields that may be null, in reading order.
_OPTIONAL_NUMBERS = ("ci_low", "ci_high", "ci_level")

#: The system turn.
OUTCOME_SYSTEM_PROMPT = """\
You are an expert in clinical research methodology and evidence-based medicine.

CRITICAL RULES:
1. Base everything ONLY on what is ACTUALLY PRESENT in the text
2. DO NOT invent, assume, calculate or fabricate any information or number
3. List only outcomes the paper itself calls primary; never choose one yourself
4. Return ONLY valid JSON, no explanation"""

#: The question, with the reply shape; the paper follows it.
OUTCOME_TASK_PROMPT = """\
Find the PRIMARY outcomes of the research paper below, and the result the \
paper reports for each.

primary_outcomes: only the outcomes the paper itself calls primary (or \
co-primary). Do not list secondary, exploratory or safety outcomes, and do \
not pick an outcome because it seems the most important. If the paper names \
no primary outcome, return an empty list.

For each primary outcome give:
- name: the outcome as the paper names it
- timepoint: when it was measured (for example "12 weeks"), or null if the \
paper does not say
- quotes: 1-3 passages copied VERBATIM from the paper that show the outcome \
is primary or give its result. Copy them character for character; do not \
paraphrase, shorten with ellipses, or join sentences.
- result: the effect the paper reports for the primary analysis of this \
outcome, or null if the paper reports no effect estimate for it:
  - measure: the name of the measure as the paper gives it (for example \
"risk ratio", "mean difference")
  - scale: "ratio" for a ratio (risk ratio, odds ratio, hazard ratio), \
"difference" for a difference (mean difference, risk difference)
  - estimate: the point estimate, as a number
  - ci_low, ci_high: the confidence interval bounds, or null if not reported
  - ci_level: the interval's level as printed (95 for a 95% interval), or \
null if not stated
  - n_analysed: the number of participants analysed for this result, or null \
if not reported
Copy numbers exactly as printed; do not calculate, convert or round.

Respond with JSON in exactly this shape:

{
    "primary_outcomes": [
        {
            "name": "Systolic blood pressure",
            "timepoint": "12 weeks",
            "quotes": ["The primary outcome was change in systolic blood pressure at 12 weeks."],
            "result": {
                "measure": "mean difference",
                "scale": "difference",
                "estimate": -4.2,
                "ci_low": -6.1,
                "ci_high": -2.3,
                "ci_level": 95,
                "n_analysed": 412
            }
        }
    ]
}"""

#: What the condensation gathers when the paper does not fit the window.
OUTCOME_CONDENSE_QUERY = (
    "Everything that names the study's primary outcome or outcomes (including "
    "co-primary outcomes) and when each was measured, and the results of the "
    "primary analysis: effect estimates with their confidence intervals and "
    "the number of participants analysed."
)


def _text(data: dict[str, Any], key: str, path: str) -> str:
    """``data[key]`` if it is text, else :class:`UnreadableReplyError` naming it."""
    value = data.get(key)
    if not isinstance(value, str):
        raise UnreadableReplyError(f"{path}{key} is not text")
    return value


def _number(value: object, name: str, path: str) -> float:
    """*value* as a finite float; a bool, text or ``inf``/``nan`` is refused."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise UnreadableReplyError(f"{path}{name} is not a finite number")
    try:
        number = float(value)
    except OverflowError:
        raise UnreadableReplyError(f"{path}{name} is not a finite number") from None
    if not math.isfinite(number):
        raise UnreadableReplyError(f"{path}{name} is not a finite number")
    return number


def _read_result(raw: object, path: str) -> tuple[ReportedResult | None, str | None]:
    """Read a ``result`` in its wire form (``ci_level`` a fraction) into ``(result, result_error)``.

    The strict reader behind :meth:`ProposedOutcome.from_dict`, and behind
    :func:`_read_reply_result` once the level is a fraction.

    Raises:
        UnreadableReplyError: *raw* is neither null nor an object, the measure
            is not text, the scale is not ``ratio``/``difference``, or a number
            field is not a finite number.  A result that reads but is
            incoherent is not an error here: it comes back as
            ``(None, reason)``.
    """
    if raw is None:
        return None, None
    path = f"{path}result."
    if not isinstance(raw, dict):
        raise UnreadableReplyError(f"{path[:-1]} is not an object")
    measure = _text(raw, "measure", path)
    scale_label = _text(raw, "scale", path)
    try:
        scale = EffectScale(scale_label.lower().strip())
    except ValueError:
        raise UnreadableReplyError(f'{path}scale "{scale_label}" is not a scale') from None
    estimate = _number(raw.get("estimate"), "estimate", path)
    optional = {
        name: None if raw.get(name) is None else _number(raw[name], name, path)
        for name in _OPTIONAL_NUMBERS
    }
    n = raw.get("n_analysed")
    if n is not None and (isinstance(n, bool) or not isinstance(n, int)):
        raise UnreadableReplyError(f"{path}n_analysed is not a whole number")
    if n is not None and not 0 <= n <= U32_MAX:
        return None, _N_ANALYSED_RANGE
    result = ReportedResult(measure, scale, estimate, n_analysed=n, **optional)
    try:
        result.validate()
    except ValueError as exc:
        return None, str(exc)
    return result, None


def _read_reply_result(raw: object, path: str) -> tuple[ReportedResult | None, str | None]:
    """Read the ``result`` of one outcome in a model's reply into ``(result, result_error)``.

    ``ci_level`` is read as printed: a value in (1, 100) is a percentage and is
    divided by :data:`PERCENT`; a value in (0, 1) is a fraction.  Any field
    that cannot be read (a number that is text, null or not finite, a scale
    other than ``ratio``/``difference``, a level that is neither) refuses this
    outcome's result only, as ``(None, reason)`` naming the field; the outcome
    itself stays.

    Raises:
        UnreadableReplyError: *raw* is neither null nor an object.
    """
    if raw is not None and not isinstance(raw, dict):
        raise UnreadableReplyError(f"{path}result is not an object")
    if raw is not None:
        level = raw.get("ci_level")
        if isinstance(level, (int, float)) and not isinstance(level, bool):
            if 1 < level < PERCENT:
                raw = {**raw, "ci_level": level / PERCENT}
            elif not 0 < level < 1:
                return None, f"result.{_CI_LEVEL_RANGE}"
    try:
        return _read_result(raw, "")
    except UnreadableReplyError as exc:
        return None, str(exc)


def _result_to_wire(result: ReportedResult | None) -> dict[str, Any] | None:
    """A result as the JSON object Rust's serde reads and writes."""
    if result is None:
        return None
    return {
        "measure": result.measure,
        "scale": result.scale.value,
        "estimate": result.estimate,
        "ci_low": result.ci_low,
        "ci_high": result.ci_high,
        "ci_level": result.ci_level,
        "n_analysed": result.n_analysed,
    }


def _wire(data: dict[str, Any], key: str, kind: type, what: str) -> Any:
    """``data[key]`` if it is a *kind* (a ``bool`` is only a bool), else ``ValueError``."""
    value = data.get(key)
    if not isinstance(value, kind) or (isinstance(value, bool) and kind is not bool):
        raise ValueError(f"{key} is not {what}")
    return value


@dataclass
class ProposedOutcome:
    """One primary outcome the paper names, with its result and passages.

    Attributes:
        name: The outcome as the paper names it.
        timepoint: When it was measured, or ``None`` if not said.
        result: The reported effect; ``None`` when the paper gives none, or
            when it was incoherent (then see *result_error*).
        result_error: Why the reported effect was refused, naming the field;
            ``None`` when *result* is set or the paper gave none.
        citations: The paper's own spans for the quotes found, offsets in code
            points into the text passed.
        quotes_not_found: The quotes not located in the paper, verbatim.
        contradicted: The quotes the paper contradicts (a changed number,
            negation or direction), each with the reason; never also in
            ``quotes_not_found``.
        quotes_dropped: How many quote members were not usable text (blank, or
            not a string) and so were never searched for.
    """

    name: str
    timepoint: str | None = None
    result: ReportedResult | None = None
    result_error: str | None = None
    citations: list[PicoCitation] = field(default_factory=list)
    quotes_not_found: list[str] = field(default_factory=list)
    contradicted: list[PicoContradictedQuote] = field(default_factory=list)
    quotes_dropped: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-ready dict (Rust's wire form)."""
        return {
            "name": self.name,
            "timepoint": self.timepoint,
            "result": _result_to_wire(self.result),
            "result_error": self.result_error,
            "citations": [c.to_dict() for c in self.citations],
            "quotes_not_found": list(self.quotes_not_found),
            "contradicted": [c.to_dict() for c in self.contradicted],
            "quotes_dropped": self.quotes_dropped,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProposedOutcome:
        """Rebuild from :meth:`to_dict` output, refusing what the reader refuses.

        Raises:
            ValueError: A field is absent or of the wrong type or value, naming it.
        """
        timepoint = data.get("timepoint")
        if timepoint is not None and not isinstance(timepoint, str):
            raise ValueError("timepoint is not text")
        error = data.get("result_error")
        if error is not None and not isinstance(error, str):
            raise ValueError("result_error is not text")
        if "result" not in data:
            raise ValueError("result is not an object")
        try:
            result, refusal = _read_result(data["result"], "")
        except UnreadableReplyError as exc:
            raise ValueError(str(exc)) from None
        if refusal is not None:
            raise ValueError(f"result is incoherent: {refusal}")
        if result is not None and error is not None:
            raise ValueError("result and result_error are both set")
        citations = _wire(data, "citations", list, "a list")
        not_found = _wire(data, "quotes_not_found", list, "a list")
        if not all(isinstance(q, str) for q in not_found):
            raise ValueError("quotes_not_found is not a list of text")
        contradicted = _wire(data, "contradicted", list, "a list")
        dropped = _wire(data, "quotes_dropped", int, "a count")
        if dropped < 0:
            raise ValueError("quotes_dropped is not a count")
        return cls(
            name=_wire(data, "name", str, "text"),
            timepoint=timepoint,
            result=result,
            result_error=error,
            citations=[PicoCitation.from_dict(c) for c in citations],
            quotes_not_found=list(not_found),
            contradicted=[PicoContradictedQuote.from_dict(c) for c in contradicted],
            quotes_dropped=dropped,
        )


@dataclass
class OutcomeProposal:
    """The primary outcomes the paper names; empty when it names none.

    Attributes:
        outcomes: The proposed outcomes, in the order the model gave them.
        condensed: Whether the model saw a digest instead of the whole paper.
        prompt_version: The wording that asked for them (:data:`OUTCOME_PROMPT_VERSION`).
    """

    outcomes: list[ProposedOutcome] = field(default_factory=list)
    condensed: bool = False
    prompt_version: int = OUTCOME_PROMPT_VERSION

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-ready dict (Rust's wire form)."""
        return {
            "outcomes": [o.to_dict() for o in self.outcomes],
            "condensed": self.condensed,
            "prompt_version": self.prompt_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OutcomeProposal:
        """Rebuild from :meth:`to_dict` output.

        Raises:
            ValueError: A field is absent or of the wrong type or value, naming it.
        """
        outcomes = _wire(data, "outcomes", list, "a list")
        return cls(
            outcomes=[ProposedOutcome.from_dict(o) for o in outcomes],
            condensed=_wire(data, "condensed", bool, "a boolean"),
            prompt_version=_prompt_version(data),
        )


def read_outcome_reply(data: dict[str, Any], text: str, min_similarity: float) -> OutcomeProposal:
    """Read the model's reply and verify its quotes against *text*.

    Args:
        data: The parsed reply.
        text: The paper as passed; citation offsets index it.
        min_similarity: :func:`~bmlib.citations.find_passage`'s threshold.

    Returns:
        The proposal, ``condensed`` false.  An empty list is a valid answer.

    Raises:
        ValueError: *min_similarity* is not a number in (0, 1].
        UnreadableReplyError: (a ``ValueError``) The reply is not an object,
            ``primary_outcomes`` is not a list, an outcome is not an object
            or has no name, a timepoint is not text, or a result is present
            but not an object.  The message names the outcome
            (``primary_outcomes[1].``) and the field.  A field inside a result
            that cannot be read refuses that outcome's result only (its
            ``result_error``).
    """
    similarity = check_min_similarity(min_similarity)
    if not isinstance(data, dict):
        raise UnreadableReplyError("expected an object")
    items = data.get("primary_outcomes")
    if not isinstance(items, list):
        raise UnreadableReplyError("primary_outcomes is not a list")

    outcomes: list[ProposedOutcome] = []
    for index, item in enumerate(items):
        path = f"primary_outcomes[{index}]."
        if not isinstance(item, dict):
            raise UnreadableReplyError(f"{path[:-1]} is not an object")
        name = _text(item, "name", path).strip()
        if not name:
            raise UnreadableReplyError(f"{path}name is empty")
        timepoint = item.get("timepoint")
        if timepoint is not None and not isinstance(timepoint, str):
            raise UnreadableReplyError(f"{path}timepoint is not text")
        if "result" not in item:
            raise UnreadableReplyError(f"{path}result is missing")
        result, result_error = _read_reply_result(item["result"], path)
        citations, not_found, contradicted, dropped = verify_quotes(
            item.get("quotes"), text, similarity
        )
        outcomes.append(
            ProposedOutcome(
                name,
                (timepoint.strip() or None) if timepoint is not None else None,
                result,
                result_error,
                citations,
                not_found,
                contradicted,
                dropped,
            )
        )
    return OutcomeProposal(outcomes, False)


class OutcomeAgent(_FullTextAgent):
    """Proposes the paper's primary outcomes, backed by passages of the paper.

    Takes the arguments of :class:`~bmlib.quality._full_text_agent._FullTextAgent`:
    the client, the model, sampling, the condensation budget, ``num_ctx``, a
    ``stop`` and an optional :class:`~bmlib.quality.design_agent.QueryCondenser`.
    """

    def _error(self, failure: AgentFailure, message: str) -> Exception:
        """Name *failure* as the shared :class:`ProposalError`."""
        return ProposalError(ProposalErrorKind(failure.value), message)

    def propose(
        self, title: str | None, text: str | None, *, min_similarity: float
    ) -> OutcomeProposal:
        """Propose the paper's primary outcomes.

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
                title, stripped, OUTCOME_CONDENSE_QUERY, "the outcome proposal"
            )
            condensed = True

        prompt = f"{OUTCOME_TASK_PROMPT}\n\nPaper Title: {title}\n\nPaper Text:\n{sent}"
        label = f"outcome proposal of {title[:LABEL_CHARS]!r}"
        proposal = self._ask_until_readable(
            OUTCOME_SYSTEM_PROMPT,
            prompt,
            lambda data: read_outcome_reply(data, original, similarity),
            label,
        )
        proposal.condensed = condensed
        return proposal
