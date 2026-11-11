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

"""The RoB 2 assessor: one call per domain, answers backed by verified quotes.

Piece 4 of the evidence-weight assessment.  The model answers RoB 2's
signalling questions (wording in :mod:`bmlib.quality.rob2_questions`), one
domain per call: domain 1 (randomisation) once for the trial, domains 2-5 once
for each primary outcome (:class:`~bmlib.quality.outcome_agent.ProposedOutcome`,
whose name, timepoint and result go into the prompt).  Code does every rule:
:func:`read_domain_reply` parses the answers (``NA`` means "not answered"),
checks applicability and derives the judgement with
:mod:`bmlib.quality.rob2_algorithm`, and locates every quote in the
**original** text.  When the guidance leaves the overall judgement to the
assessor (no domain high, two or more with some concerns), one more call asks
whether to escalate (:data:`~bmlib.quality.rob2_questions.ESCALATION_PROMPT`).

A failure is never a score.  A domain whose reply cannot be read (after the
shared re-ask) or whose request fails is recorded as :class:`DomainFailed`
naming why, the other domains go on, and every outcome it touches has
``overall`` ``None`` ("not assessed").  An unreadable or failed escalation
leaves ``escalation`` and ``overall`` ``None`` with the reason in
``escalation_error``.  Only a stop or a refused input ends the run
(:class:`Rob2RunError`).

Nothing is truncated: a paper over the window is condensed **per domain**,
for that domain's own query, once per run, and each domain it served is
flagged ``condensed``.

The replies are shared with Rust's ``quality::rob2_agent`` through
``tests/fixtures/rob2_replies.json`` and a whole run through
``tests/fixtures/rob2_run.json`` (both written by ``scripts/make_rob2_fixtures.py``).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from bmlib.quality._full_text_agent import (
    LABEL_CHARS,
    AgentFailure,
    UnreadableReplyError,
    _FullTextAgent,
    check_min_similarity,
)
from bmlib.quality.design_agent import _wire
from bmlib.quality.evidence_ladder import U32_MAX
from bmlib.quality.outcome_agent import ProposedOutcome
from bmlib.quality.pico_agent import verify_quotes
from bmlib.quality.pico_models import PicoCitation, PicoContradictedQuote
from bmlib.quality.rob2_algorithm import (
    DOMAINS_ALL,
    ROB2_ALGORITHM_VERSION,
    Answer,
    Answers,
    Domain,
    Judgement,
    Rob2Error,
    applicable_questions,
    domain_judgement,
    needs_escalation_decision,
    overall_judgement,
    questions,
)
from bmlib.quality.rob2_d5_checks import D5_FACTS, D5Fact
from bmlib.quality.rob2_d5_facts import (
    DIFFERING_ANALYSIS,
    MISSING_MEASUREMENT,
    D5Facts,
    d5_answers,
)
from bmlib.quality.rob2_questions import (
    D5_OUTCOME_SWITCHING,
    ESCALATION_PROMPT,
    ROB2_PROMPT_VERSION,
    condense_query,
    domain_title,
    outcome_block,
    question_text,
)

__all__ = [
    "NOT_APPLICABLE",
    "ROB2_DOMAIN_PROMPT",
    "ROB2_SYSTEM_PROMPT",
    "DomainAssessment",
    "DomainFailed",
    "DomainResult",
    "Escalation",
    "MachineAnswer",
    "OutcomeRob2",
    "Rob2Agent",
    "Rob2Run",
    "Rob2RunError",
    "Rob2RunErrorKind",
    "d5_assessment",
    "domain_prompt",
    "domain_result_from_dict",
    "escalation_message",
    "read_domain_reply",
    "read_escalation_reply",
    "rob2_overall",
]

logger = logging.getLogger(__name__)

#: The answer that means "this question does not apply"; read as unanswered.
NOT_APPLICABLE = "NA"

#: Every spelling read as :data:`NOT_APPLICABLE`, once whitespace is removed
#: and the label upper-cased ("n/a", "Not applicable").
_NOT_APPLICABLE_FORMS = frozenset({NOT_APPLICABLE, "N/A", "NOTAPPLICABLE"})

#: The answers a reply may give, as the refusal names them.
_ALLOWED = "Y, PY, PN, N, NI, NA"

#: The domains judged once per outcome, in order.
_OUTCOME_DOMAINS: tuple[Domain, ...] = DOMAINS_ALL[1:]

#: A ``DomainResult``'s ``status`` values.
_ASSESSED = "assessed"
_FAILED = "failed"

#: The system turn of every RoB 2 call.
ROB2_SYSTEM_PROMPT = """\
You are an expert in clinical research methodology and in assessing the risk \
of bias of randomised trials.

CRITICAL RULES:
1. Base every answer ONLY on what is ACTUALLY PRESENT in the text
2. DO NOT invent, assume or fabricate any information
3. When the paper gives nothing to go on, answer NI (no information) rather than guess
4. Return ONLY valid JSON, no explanation"""

#: The start of each domain's prompt, with the reply shape; the domain, the
#: outcome, the questions and the paper follow it.
ROB2_DOMAIN_PROMPT = """\
Assess one domain of the risk of bias of the randomised trial in the research \
paper below, for the effect of assignment to the interventions (as an \
intention-to-treat analysis estimates it).

Answer every question listed below. For each give:
- answer: "Y" (yes), "PY" (probably yes), "PN" (probably no), "N" (no) or \
"NI" (no information), as the question describes; "NA" (not applicable) only \
for a question that says it applies only under a condition, when that \
condition clearly does not hold
- quotes: 1-3 passages copied VERBATIM from the paper that support the \
answer, or an empty list when the paper says nothing on it. Copy them \
character for character; do not paraphrase, shorten with ellipses, or join \
sentences.
- reasoning: one or two sentences saying why.

Respond with JSON in exactly this shape, one entry per question id:

{
    "answers": {
        "1.1": {
            "answer": "PY",
            "quotes": ["Participants were randomly assigned using a computer-generated list."],
            "reasoning": "A computer-generated list is a random sequence."
        }
    }
}"""


def _object(value: object, what: str) -> dict[str, Any]:
    """*value* if it is a JSON object, else ``ValueError`` naming *what*."""
    if not isinstance(value, dict):
        raise ValueError(f"{what} is not an object")
    return value


#: The lowest and highest HTTP status a stored ``status_code`` may hold.
HTTP_STATUS_MIN = 100
HTTP_STATUS_MAX = 599


def _status_code(data: dict[str, Any], key: str) -> int | None:
    """``data[key]``: required, an HTTP status or null, else ``ValueError``."""
    if key not in data:
        raise ValueError(f"{key} is absent")
    status = data[key]
    if status is not None and (
        isinstance(status, bool)
        or not isinstance(status, int)
        or not HTTP_STATUS_MIN <= status <= HTTP_STATUS_MAX
    ):
        raise ValueError(f"{key} is not null or an HTTP status")
    return status


def _optional_text(data: dict[str, Any], key: str) -> str | None:
    """``data[key]`` if it is text or null/absent, else ``ValueError``."""
    value = data.get(key)
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{key} is not text")
    return value


def _enum(enum: Any, value: object, key: str) -> Any:
    """``enum(value)`` for an exact wire value, else ``ValueError`` naming *key*."""
    if not isinstance(value, str):
        raise ValueError(f"{key} is not text")
    try:
        return enum(value)
    except ValueError:
        raise ValueError(
            f'{key} "{value}" is not one of {", ".join(e.value for e in enum)}'
        ) from None


@dataclass
class MachineAnswer:
    """The model's answer to one signalling question, with its passages.

    Attributes:
        answer: The answer.
        citations: The paper's own spans for the quotes found, offsets in code
            points into the text passed.
        quotes_not_found: The quotes not located in the paper, verbatim; the
            answer stands without them.
        contradicted: The quotes the paper contradicts (a changed number,
            negation or direction), each with the reason; never also in
            ``quotes_not_found``.
        quotes_dropped: How many quote members were not usable text (blank, or
            not a string) and so were never searched for.
        reasoning: The model's explanation, ``""`` when absent.
    """

    answer: Answer
    citations: list[PicoCitation] = field(default_factory=list)
    quotes_not_found: list[str] = field(default_factory=list)
    contradicted: list[PicoContradictedQuote] = field(default_factory=list)
    quotes_dropped: int = 0
    reasoning: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-ready dict (Rust's wire form)."""
        return {
            "answer": self.answer.value,
            "citations": [c.to_dict() for c in self.citations],
            "quotes_not_found": list(self.quotes_not_found),
            "contradicted": [c.to_dict() for c in self.contradicted],
            "quotes_dropped": self.quotes_dropped,
            "reasoning": self.reasoning,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MachineAnswer:
        """Rebuild from :meth:`to_dict` output; the answer must be an exact wire value.

        Raises:
            ValueError: A field is absent or of the wrong type or value, naming it.
        """
        data = _object(data, "the answer")
        not_found = _wire(data, "quotes_not_found", list, "a list")
        if not all(isinstance(q, str) for q in not_found):
            raise ValueError("quotes_not_found is not a list of text")
        dropped = _wire(data, "quotes_dropped", int, "a count")
        if dropped < 0:
            raise ValueError("quotes_dropped is not a count")
        return cls(
            answer=_enum(Answer, data.get("answer"), "answer"),
            citations=[PicoCitation.from_dict(c) for c in _wire(data, "citations", list, "a list")],
            quotes_not_found=list(not_found),
            contradicted=[
                PicoContradictedQuote.from_dict(c)
                for c in _wire(data, "contradicted", list, "a list")
            ],
            quotes_dropped=dropped,
            reasoning=_wire(data, "reasoning", str, "text"),
        )


@dataclass
class DomainAssessment:
    """One domain, assessed: the applicable answers and the judgement they give.

    Attributes:
        domain: The domain.
        answers: The applicable questions' answers by id, in question order.
        judgement: The judgement :func:`~bmlib.quality.rob2_algorithm.domain_judgement`
            derives from *answers*.
        condensed: Whether the model saw a digest instead of the whole paper.
        ignored: Ids the model answered although they do not apply on the
            branch its answers took; listed rather than dropped silently.
    """

    domain: Domain
    answers: dict[str, MachineAnswer]
    judgement: Judgement
    condensed: bool = False
    ignored: list[str] = field(default_factory=list)
    facts: D5Facts | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-ready dict, tagged ``"status": "assessed"``; D5's
        facts, when it has them, under ``"facts"``."""
        data = {
            "status": _ASSESSED,
            "domain": self.domain.value,
            "answers": {q: a.to_dict() for q, a in self.answers.items()},
            "judgement": self.judgement.value,
            "condensed": self.condensed,
            "ignored": list(self.ignored),
        }
        if self.facts is not None:
            data["facts"] = self.facts.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DomainAssessment:
        """Rebuild from :meth:`to_dict` output, refusing what the reader refuses.

        The answers must give the stored judgement, every answer must apply,
        and ``ignored`` may list only questions that do not apply.

        Raises:
            ValueError: A field is absent, of the wrong type or value, or the
                answers do not give the judgement, naming it.
        """
        data = _object(data, "the domain")
        domain = _enum(Domain, data.get("domain"), "domain")
        raw = _wire(data, "answers", dict, "an object")
        _refuse_unknown(domain, raw)
        answers: dict[str, MachineAnswer] = {}
        for question in questions(domain):
            if question in raw:
                try:
                    answers[question] = MachineAnswer.from_dict(raw[question])
                except ValueError as exc:
                    raise ValueError(f"answers.{question}.{exc}") from None
        judgement = _enum(Judgement, data.get("judgement"), "judgement")
        given = {q: a.answer for q, a in answers.items()}
        derived = _judge(domain, given)
        applicable = applicable_questions(domain, given)
        for question in answers:
            if question not in applicable:
                raise ValueError(f"answers.{question} is not applicable")
        if derived is not judgement:
            raise ValueError(f"judgement {judgement.value} does not follow from the answers")
        ignored = _wire(data, "ignored", list, "a list")
        for question in ignored:
            if question not in questions(domain) or question in applicable:
                raise ValueError(
                    f"ignored lists {question!s}, which is not an unasked question of "
                    f"{domain.value}"
                )
        facts = None
        if data.get("facts") is not None:
            if domain is not Domain.D5:
                raise ValueError(f"facts are D5's, not {domain.value}'s")
            facts = D5Facts.from_dict(data["facts"])
        return cls(
            domain,
            answers,
            judgement,
            _wire(data, "condensed", bool, "a boolean"),
            list(ignored),
            facts,
        )


@dataclass
class DomainFailed:
    """One domain that could not be assessed, and why.

    Attributes:
        domain: The domain.
        reason: Why: the unreadable field, the failed request or condensation.
        retryable: Whether sending the failed request again may succeed (as
            :attr:`~bmlib.quality.pico_models.PicoError.retryable`); ``False``
            where the failure was not a request.
        status: The HTTP status the failed request was answered with (401 or
            403 is a refused key); ``None`` where no HTTP answer came or the
            failure was not a request.
    """

    domain: Domain
    reason: str
    retryable: bool = False
    status: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-ready dict, tagged ``"status": "failed"``.

        ``status`` is written as ``status_code``, since ``status`` is the tag.
        """
        return {
            "status": _FAILED,
            "domain": self.domain.value,
            "reason": self.reason,
            "retryable": self.retryable,
            "status_code": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DomainFailed:
        """Rebuild from :meth:`to_dict` output.

        ``retryable`` and ``status_code`` (the ``status`` attribute) are both required.

        Raises:
            ValueError: A field is absent or of the wrong type or value, naming it.
        """
        data = _object(data, "the domain")
        return cls(
            _enum(Domain, data.get("domain"), "domain"),
            _wire(data, "reason", str, "text"),
            _wire(data, "retryable", bool, "a boolean"),
            _status_code(data, "status_code"),
        )


#: A domain's result: assessed, or failed with its reason (Rust's ``DomainResult``).
DomainResult = DomainAssessment | DomainFailed


def domain_result_from_dict(data: dict[str, Any]) -> DomainResult:
    """Rebuild a :data:`DomainResult` by its ``status``.

    Raises:
        ValueError: ``status`` is neither ``assessed`` nor ``failed``, or the
            rest does not read (see the two ``from_dict``).
    """
    status = _object(data, "the domain").get("status")
    if not isinstance(status, str):
        raise ValueError("status is not text")
    if status == _ASSESSED:
        return DomainAssessment.from_dict(data)
    if status == _FAILED:
        return DomainFailed.from_dict(data)
    raise ValueError(f'status "{status}" is not {_ASSESSED} or {_FAILED}')


@dataclass
class Escalation:
    """The model's answer to the escalation question.

    Attributes:
        escalate: Whether the combined concerns warrant high risk of bias.
        reasoning: The model's explanation, ``""`` when absent.
    """

    escalate: bool
    reasoning: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-ready dict (Rust's wire form)."""
        return {"escalate": self.escalate, "reasoning": self.reasoning}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Escalation:
        """Rebuild from :meth:`to_dict` output.

        Raises:
            ValueError: A field is absent or of the wrong type, naming it.
        """
        data = _object(data, "escalation")
        return cls(
            _wire(data, "escalate", bool, "true or false"), _wire(data, "reasoning", str, "text")
        )


@dataclass
class OutcomeRob2:
    """RoB 2 for one primary outcome: domains 2-5, and the overall judgement.

    Attributes:
        outcome: The outcome's name, as the outcome proposal gave it.
        domains: Domains D2, D3, D4 and D5, in that order.
        escalation: The model's escalation answer; ``None`` when none was
            needed or it could not be had (then see *escalation_error*).
        overall: The overall judgement; ``None`` ("not assessed") when any of
            D1-D5 failed or a needed escalation could not be had.  Never a
            guessed judgement.
        escalation_error: Why a needed escalation could not be had; ``None``
            otherwise.
        escalation_retryable: Whether sending the failed escalation request
            again may succeed; ``False`` unless ``escalation_error`` came from
            a request.
        escalation_status: The HTTP status that request was answered with;
            ``None`` where no HTTP answer came or ``escalation_error`` is not
            from a request.  Written as ``escalation_status_code``.
    """

    outcome: str
    domains: list[DomainResult]
    escalation: Escalation | None = None
    overall: Judgement | None = None
    escalation_error: str | None = None
    escalation_retryable: bool = False
    escalation_status: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-ready dict (Rust's wire form)."""
        return {
            "outcome": self.outcome,
            "domains": [d.to_dict() for d in self.domains],
            "escalation": None if self.escalation is None else self.escalation.to_dict(),
            "escalation_error": self.escalation_error,
            "escalation_retryable": self.escalation_retryable,
            "escalation_status_code": self.escalation_status,
            "overall": None if self.overall is None else self.overall.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OutcomeRob2:
        """Rebuild from :meth:`to_dict` output.

        Whether ``overall`` follows from the domains needs the study's D1, so
        :meth:`Rob2Run.from_dict` checks it.

        Raises:
            ValueError: A field is absent or of the wrong type or value, the
                domains are not D2-D5 in order, or both ``escalation`` and
                ``escalation_error`` are set, naming it.
        """
        data = _object(data, "the outcome")
        domains = [domain_result_from_dict(d) for d in _wire(data, "domains", list, "a list")]
        if [d.domain for d in domains] != list(_OUTCOME_DOMAINS):
            raise ValueError("domains are not D2, D3, D4, D5 in order")
        raw = data.get("escalation")
        escalation = None if raw is None else Escalation.from_dict(raw)
        error = _optional_text(data, "escalation_error")
        if escalation is not None and error is not None:
            raise ValueError("escalation and escalation_error are both set")
        retryable = _wire(data, "escalation_retryable", bool, "a boolean")
        status = _status_code(data, "escalation_status_code")
        if error is None and (retryable or status is not None):
            raise ValueError(
                "escalation_retryable or escalation_status_code given without an error"
            )
        overall = data.get("overall")
        return cls(
            outcome=_wire(data, "outcome", str, "text"),
            domains=domains,
            escalation=escalation,
            overall=None if overall is None else _enum(Judgement, overall, "overall"),
            escalation_error=error,
            escalation_retryable=retryable,
            escalation_status=status,
        )


@dataclass
class Rob2Run:
    """One RoB 2 run over a paper: D1 for the trial, D2-D5 for each primary outcome.

    Attributes:
        algorithm_version: The decision rules that judged it
            (:data:`~bmlib.quality.rob2_algorithm.ROB2_ALGORITHM_VERSION`).
        prompt_version: The wording that asked it
            (:data:`~bmlib.quality.rob2_questions.ROB2_PROMPT_VERSION`).
        study: Domain D1, judged once for the trial.
        outcomes: One entry per primary outcome, in the order given.
    """

    algorithm_version: str
    prompt_version: int
    study: DomainResult
    outcomes: list[OutcomeRob2]

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-ready dict (Rust's wire form)."""
        return {
            "algorithm_version": self.algorithm_version,
            "prompt_version": self.prompt_version,
            "study": self.study.to_dict(),
            "outcomes": [o.to_dict() for o in self.outcomes],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Rob2Run:
        """Rebuild from :meth:`to_dict` output, refusing what the agent would not make.

        A run judged by other decision rules is refused, since its judgements
        cannot be checked against these.

        Raises:
            ValueError: A field is absent or of the wrong type or value, the
                study is not D1, an escalation is given where none is asked, or
                an outcome's ``overall`` does not follow from its domains,
                naming it (``outcomes[1].``).
        """
        data = _object(data, "the run")
        version = _wire(data, "algorithm_version", str, "text")
        if version != ROB2_ALGORITHM_VERSION:
            raise ValueError(f"algorithm_version {version} is not {ROB2_ALGORITHM_VERSION}")
        prompt_version = _wire(data, "prompt_version", int, "a whole number")
        if not 0 <= prompt_version <= U32_MAX:
            raise ValueError("prompt_version is not a whole number that fits 32 bits")
        try:
            study = domain_result_from_dict(data.get("study"))  # type: ignore[arg-type]
        except ValueError as exc:
            raise ValueError(f"study.{exc}") from None
        if study.domain is not Domain.D1:
            raise ValueError("study is not domain D1")
        outcomes = []
        for index, raw in enumerate(_wire(data, "outcomes", list, "a list")):
            try:
                outcome = OutcomeRob2.from_dict(raw)
                _check_overall(study, outcome)
            except ValueError as exc:
                raise ValueError(f"outcomes[{index}].{exc}") from None
            outcomes.append(outcome)
        return cls(version, prompt_version, study, outcomes)


def _check_overall(study: DomainResult, outcome: OutcomeRob2) -> None:
    """Refuse an escalation nobody asked for and an ``overall`` the domains do not give."""
    judgements = _judgements(study, outcome.domains)
    asked = judgements is not None and needs_escalation_decision(judgements)
    if outcome.escalation is not None and not asked:
        raise ValueError("escalation given where none is asked")
    if outcome.escalation_error is not None and not asked:
        raise ValueError("escalation_error given where no escalation is asked")
    if outcome.overall != rob2_overall(study, outcome.domains, outcome.escalation):
        raise ValueError("overall does not follow from the domains")


def _judgements(study: DomainResult, domains: Sequence[DomainResult]) -> list[Judgement] | None:
    """The five judgements, D1 first; ``None`` when any domain failed."""
    results = [study, *domains]
    if not all(isinstance(r, DomainAssessment) for r in results):
        return None
    return [r.judgement for r in results if isinstance(r, DomainAssessment)]


def rob2_overall(
    study: DomainResult, domains: Sequence[DomainResult], escalation: Escalation | None
) -> Judgement | None:
    """The overall judgement of one outcome, or ``None`` when it is not assessed.

    Args:
        study: Domain D1.
        domains: Domains D2-D5 of the outcome, in order.
        escalation: The escalation answer, if one was had.

    Returns:
        ``None`` when any domain failed, or an escalation is needed and
        *escalation* is ``None``; otherwise
        :func:`~bmlib.quality.rob2_algorithm.overall_judgement`.
    """
    judgements = _judgements(study, domains)
    if judgements is None:
        return None
    if needs_escalation_decision(judgements):
        return None if escalation is None else overall_judgement(judgements, escalation.escalate)
    return overall_judgement(judgements)


# --- Reading a reply ---


def _refuse_unknown(domain: Domain, answers: dict[str, Any]) -> None:
    """Refuse the first id (in sorted order) that is not one of *domain*'s questions."""
    unknown = sorted(q for q in answers if q not in questions(domain))
    if unknown:
        raise UnreadableReplyError(f"answers.{unknown[0]} is not a question of {domain.value}")


def _judge(domain: Domain, answers: Answers) -> Judgement:
    """:func:`domain_judgement`, its refusal named in wire terms (``answers.2.3``)."""
    try:
        return domain_judgement(domain, answers)
    except Rob2Error as exc:
        if exc.kind == "MissingAnswer":
            message = f"answers.{exc.question} is applicable but not answered"
        elif exc.kind == "UnknownQuestion":
            message = f"answers.{exc.question} is not a question of {domain.value}"
        elif exc.kind == "NotAllowed" and exc.answer is not None:
            message = (
                f"answers.{exc.question}.answer {exc.answer.value} is not allowed for this question"
            )
        else:
            message = str(exc)
        raise UnreadableReplyError(message) from None


def read_domain_reply(
    domain: Domain, data: dict[str, Any], text: str, min_similarity: float
) -> DomainAssessment:
    """Read the model's answers for one domain and verify their quotes against *text*.

    Args:
        domain: The domain asked.
        data: The parsed reply, ``{"answers": {"<id>": {"answer", "quotes",
            "reasoning"}}}``.
        text: The paper as passed (never a digest); citation offsets index it.
        min_similarity: :func:`~bmlib.citations.find_passage`'s threshold.

    Returns:
        The assessment, ``condensed`` false.  An answer of ``NA`` (also
        ``N/A`` or ``Not applicable``; any case, whitespace ignored) counts as
        unanswered; an answer to a question that does not apply, even one that
        cannot be read, is left out and its id listed in ``ignored``.

    Raises:
        ValueError: *min_similarity* is not a number in (0, 1].
        UnreadableReplyError: (a ``ValueError``) The reply is not an object;
            ``answers`` is not an object; an id is not one of the domain's
            questions; an entry is not an object; an answer is not text or not
            one of Y, PY, PN, N, NI, NA; reasoning is not text (each of these
            only for a question that applies, decided from the answers that
            can be read); an applicable question is unanswered (or ``NA``); or
            an answer is not allowed for its question (3.2 has no NI).  The
            message names the field (``answers.2.3.answer``).
    """
    similarity = check_min_similarity(min_similarity)
    if not isinstance(data, dict):
        raise UnreadableReplyError("expected an object")
    raw = data.get("answers")
    if not isinstance(raw, dict):
        raise UnreadableReplyError("answers is not an object")
    _refuse_unknown(domain, raw)

    parsed: dict[str, MachineAnswer] = {}
    unreadable: dict[str, str] = {}
    for question in questions(domain):
        if question not in raw:
            continue
        try:
            entry = _read_entry(raw[question], f"answers.{question}", text, similarity)
        except UnreadableReplyError as exc:
            unreadable[question] = str(exc)
            continue
        if entry is not None:
            parsed[question] = entry

    given = {q: a.answer for q, a in parsed.items()}
    applicable = applicable_questions(domain, given)
    for question in applicable:
        if question in unreadable:
            raise UnreadableReplyError(unreadable[question])
    judgement = _judge(domain, given)
    return DomainAssessment(
        domain,
        {q: parsed[q] for q in applicable if q in parsed},
        judgement,
        False,
        [q for q in questions(domain) if (q in parsed or q in unreadable) and q not in applicable],
    )


def _read_entry(item: object, path: str, text: str, similarity: float) -> MachineAnswer | None:
    """One question's entry as a :class:`MachineAnswer`, or ``None`` for ``NA``.

    ``NA``, ``N/A`` and ``Not applicable`` (any case, whitespace ignored) read
    as ``NA``.

    Raises:
        UnreadableReplyError: The entry is not an object, its answer is not
            text or not one of Y, PY, PN, N, NI, NA, or its reasoning is not
            text; the message names the field under *path*.
    """
    if not isinstance(item, dict):
        raise UnreadableReplyError(f"{path} is not an object")
    label = item.get("answer")
    if not isinstance(label, str):
        raise UnreadableReplyError(f"{path}.answer is not text")
    if "".join(label.split()).upper() in _NOT_APPLICABLE_FORMS:
        return None
    try:
        answer = Answer.parse(label)
    except Rob2Error:
        raise UnreadableReplyError(f'{path}.answer "{label}" is not one of {_ALLOWED}') from None
    reasoning = item.get("reasoning")
    if reasoning is None:
        reasoning = ""
    elif not isinstance(reasoning, str):
        raise UnreadableReplyError(f"{path}.reasoning is not text")
    citations, not_found, contradicted, dropped = verify_quotes(
        item.get("quotes"), text, similarity
    )
    return MachineAnswer(answer, citations, not_found, contradicted, dropped, reasoning)


def read_escalation_reply(data: dict[str, Any]) -> Escalation:
    """Read the model's escalation answer.

    Raises:
        UnreadableReplyError: (a ``ValueError``) The reply is not an object,
            ``escalate`` is not ``true`` or ``false``, or ``reasoning`` is not
            text.
    """
    if not isinstance(data, dict):
        raise UnreadableReplyError("expected an object")
    escalate = data.get("escalate")
    if not isinstance(escalate, bool):
        raise UnreadableReplyError("escalate is not true or false")
    reasoning = data.get("reasoning")
    if reasoning is None:
        reasoning = ""
    elif not isinstance(reasoning, str):
        raise UnreadableReplyError("reasoning is not text")
    return Escalation(escalate, reasoning)


def d5_assessment(facts: D5Facts) -> DomainAssessment:
    """D5 assessed from *facts*: the answers by
    :func:`~bmlib.quality.rob2_d5_facts.d5_answers`, the judgement by RoB 2's
    algorithm, and the facts kept with it.

    Each answer's citations are the passages that decided it: the deciding
    facts' quotes, the naming passages of the measurements missing at both
    looks, or both sides of a differing analysis.  Its reasoning lists every
    fact's verdict, so a reader sees why it came out.
    """
    verdicts = "; ".join(f"{f.value}: {facts.facts[f].verdict.value}" for f in D5_FACTS)
    missing = [m.label for m in facts.measurements if m.missing()]
    reasoning = (
        f"From the facts ({verdicts}); other measurements missing at both looks: "
        f"{', '.join(missing) if missing else 'none'}; the analysis reported differs from the "
        f"main one: {'yes' if facts.analysis.counts() else 'no'}."
    )
    answers: dict[str, MachineAnswer] = {}
    for question, (answer, deciding) in d5_answers(facts).items():
        citations: list[PicoCitation] = []
        for reason in deciding:
            if reason == MISSING_MEASUREMENT:
                citations += [c for m in facts.measurements if m.missing() for c in m.named]
            elif reason == DIFFERING_ANALYSIS:
                citations += facts.analysis.planned + facts.analysis.reported
            else:
                citations += facts.facts[D5Fact(reason)].citations
        answers[question] = MachineAnswer(answer, citations, reasoning=reasoning)
    judgement = _judge(Domain.D5, {q: a.answer for q, a in answers.items()})
    return DomainAssessment(Domain.D5, answers, judgement, facts=facts)


# --- The prompts ---


def domain_prompt(domain: Domain, title: str, paper: str, outcome: ProposedOutcome | None) -> str:
    """The user turn asking *domain*'s questions over *paper*.

    Args:
        domain: The domain asked.
        title: The paper's title.
        paper: The text the model reads: the whole paper, or its digest.
        outcome: The outcome assessed; ``None`` for D1, required for D2-D5.

    Raises:
        ValueError: An outcome given for D1, or none for D2-D5.
    """
    if domain is Domain.D1 and outcome is not None:
        raise ValueError("D1 is judged once for the trial, without an outcome")
    if domain is not Domain.D1 and outcome is None:
        raise ValueError(f"{domain.value} is judged for an outcome, and none was given")
    parts = [ROB2_DOMAIN_PROMPT, domain_title(domain)]
    if outcome is not None:
        parts.append(outcome_block(outcome))
    if domain is Domain.D5:
        parts.append(D5_OUTCOME_SWITCHING)
    listed = "\n\n".join(f"{q}: {question_text(q)}" for q in questions(domain))
    parts += [f"Questions:\n\n{listed}", f"Paper Title: {title}", f"Paper Text:\n{paper}"]
    return "\n\n".join(parts)


def escalation_message(
    outcome: ProposedOutcome, study: DomainAssessment, domains: Sequence[DomainAssessment]
) -> str:
    """The user turn of the escalation question: the outcome and all five domains.

    Each domain is its title and judgement, then one line per answer with its
    reasoning.
    """
    blocks = [ESCALATION_PROMPT, outcome_block(outcome)]
    for assessment in [study, *domains]:
        lines = [f"{domain_title(assessment.domain)}: {assessment.judgement.value}"]
        for question, answer in assessment.answers.items():
            line = f"{question}: {answer.answer.value}"
            lines.append(f"{line} - {answer.reasoning}" if answer.reasoning else line)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


# --- The agent ---


class Rob2RunErrorKind(Enum):
    """Why a RoB 2 run ended without a result (a domain's own failure is not one)."""

    #: The paper's text was empty; no request was made.
    EMPTY_INPUT = "empty_input"
    #: No primary outcome was given; no request was made.
    NO_OUTCOMES = "no_outcomes"
    #: The caller's stop was set; no partial run is returned.
    STOPPED = "stopped"
    #: ``min_similarity`` was not a number in (0, 1]; no request was made.
    INVALID_SIMILARITY = "invalid_similarity"


class Rob2RunError(Exception):
    """A run that ended without a result; ``kind`` says why, the message says more."""

    def __init__(self, kind: Rob2RunErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind


class _CallFailedError(Exception):
    """One call failed (request, condensation or unreadable reply); its domain is failed.

    ``retryable`` and ``status`` are those of a failed request, else ``False``
    and ``None``.
    """

    def __init__(self, message: str, retryable: bool = False, status: int | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status = status


class Rob2Agent(_FullTextAgent):
    """Assesses a paper with RoB 2, one call per domain.

    Takes the arguments of :class:`~bmlib.quality._full_text_agent._FullTextAgent`:
    the client, the model, sampling, the condensation budget, ``num_ctx``, a
    ``stop`` and an optional :class:`~bmlib.quality._full_text_agent.QueryCondenser`.
    """

    def _error(
        self,
        failure: AgentFailure,
        message: str,
        retryable: bool = False,
        status: int | None = None,
    ) -> Exception:
        """A stop ends the run; every other failure fails the call's domain only."""
        if failure is AgentFailure.STOPPED:
            return Rob2RunError(Rob2RunErrorKind.STOPPED, message)
        return _CallFailedError(message, retryable, status)

    def assess(
        self,
        title: str | None,
        text: str | None,
        outcomes: Sequence[ProposedOutcome],
        *,
        min_similarity: float,
    ) -> Rob2Run:
        """Assess the paper: D1 once, D2-D5 for each outcome, then any escalation.

        Args:
            title: The paper's title.
            text: The paper's full text.  Citation offsets are code points into
                it, exactly as passed.
            outcomes: The primary outcomes to assess, in order.
            min_similarity: How close a quote must be to a span of *text*.

        Returns:
            The run; a domain that could not be assessed is a
            :class:`DomainFailed` in it.

        Raises:
            Rob2RunError: The input was refused before any request, or the
                stop was set.
            ValueError: The model's provider is unknown.
            ImportError: The provider's SDK is not installed.
        """
        try:
            similarity = check_min_similarity(min_similarity)
        except ValueError as exc:
            raise Rob2RunError(Rob2RunErrorKind.INVALID_SIMILARITY, str(exc)) from exc
        original = text or ""
        stripped = original.strip()
        title = (title or "").strip()
        if not stripped:
            raise Rob2RunError(Rob2RunErrorKind.EMPTY_INPUT, "the paper's text is empty")
        if not outcomes:
            raise Rob2RunError(Rob2RunErrorKind.NO_OUTCOMES, "no primary outcome to assess")
        self._check_provider()
        self._raise_if_stopped()

        papers: dict[Domain, str | _CallFailedError] = {}
        study = self._domain(Domain.D1, title, stripped, original, None, similarity, papers)
        results = []
        for outcome in outcomes:
            domains = [
                self._domain(d, title, stripped, original, outcome, similarity, papers)
                for d in _OUTCOME_DOMAINS
            ]
            results.append(self._outcome(title, outcome, study, domains))
        return Rob2Run(ROB2_ALGORITHM_VERSION, ROB2_PROMPT_VERSION, study, results)

    def _paper(
        self,
        domain: Domain,
        title: str,
        stripped: str,
        papers: dict[Domain, str | _CallFailedError],
    ) -> tuple[str, bool]:
        """The text *domain*'s call sends and whether it is a digest; condensed once per run."""
        if len(stripped) <= self.condense_config.max_context_chars:
            return stripped, False
        if domain not in papers:
            try:
                papers[domain] = self._condense_once(
                    title, stripped, condense_query(domain), f"the RoB 2 {domain.value} assessment"
                )
            except _CallFailedError as exc:
                papers[domain] = exc
        paper = papers[domain]
        if isinstance(paper, _CallFailedError):
            raise _CallFailedError(str(paper), paper.retryable, paper.status)
        return paper, True

    def _domain(
        self,
        domain: Domain,
        title: str,
        stripped: str,
        original: str,
        outcome: ProposedOutcome | None,
        similarity: float,
        papers: dict[Domain, str | _CallFailedError],
    ) -> DomainResult:
        """Ask one domain's questions; a failed call comes back as :class:`DomainFailed`."""
        label = f"RoB 2 {domain.value} of {title[:LABEL_CHARS]!r}"
        try:
            paper, condensed = self._paper(domain, title, stripped, papers)
            assessment = self._ask_until_readable(
                ROB2_SYSTEM_PROMPT,
                domain_prompt(domain, title, paper, outcome),
                lambda data: read_domain_reply(domain, data, original, similarity),
                label,
            )
        except _CallFailedError as exc:
            logger.warning("%s not assessed: %s", label, exc)
            return DomainFailed(domain, str(exc), exc.retryable, exc.status)
        assessment.condensed = condensed
        return assessment

    def _outcome(
        self,
        title: str,
        outcome: ProposedOutcome,
        study: DomainResult,
        domains: list[DomainResult],
    ) -> OutcomeRob2:
        """Combine one outcome's domains, asking the escalation question when it is needed."""
        escalation: Escalation | None = None
        error: str | None = None
        retryable, status = False, None
        judgements = _judgements(study, domains)
        if judgements is not None and needs_escalation_decision(judgements):
            assessed = [d for d in domains if isinstance(d, DomainAssessment)]
            assert isinstance(study, DomainAssessment)  # _judgements returned five
            label = f"RoB 2 escalation of {title[:LABEL_CHARS]!r}"
            try:
                escalation = self._ask_until_readable(
                    ROB2_SYSTEM_PROMPT,
                    escalation_message(outcome, study, assessed),
                    read_escalation_reply,
                    label,
                )
            except _CallFailedError as exc:
                logger.warning("%s not had: %s", label, exc)
                error, retryable, status = str(exc), exc.retryable, exc.status
        return OutcomeRob2(
            outcome.name,
            domains,
            escalation,
            rob2_overall(study, domains, escalation),
            error,
            retryable,
            status,
        )
