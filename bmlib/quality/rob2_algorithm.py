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

"""RoB 2 decision rules: signalling answers to judgements, no LLM, no wording.

The official mapping tables of the Cochrane RoB 2 guidance (22 August 2019,
the randomised-parallel-group tool, effect of *assignment*) as code: Table 1
(overall), Tables 3-4 (domain 1), 6 (domain 2), 10 (domain 3), 12 (domain 4)
and 14 (domain 5). Rust's ``rust/bmlib/src/quality/rob2_algorithm.rs`` is the
same rules; ``tests/fixtures/rob2_algorithm.json`` holds both to them.

In this module "Y" means Y or PY and "N" means N or PN, as the guidance says.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from enum import Enum

ROB2_ALGORITHM_VERSION = "rob2-2019-08-22/assignment/parallel/1"


class Answer(Enum):
    """One signalling-question answer; the value is the wire form."""

    Y = "Y"
    PY = "PY"
    PN = "PN"
    N = "N"
    NI = "NI"

    @staticmethod
    def parse(text: str) -> Answer:
        """Read an answer, ignoring case and surrounding space.

        ``"NA"`` is not an answer (a question that is not applicable is simply
        not answered), so it raises ``Rob2Error`` of kind ``UnreadableAnswer``.
        """
        try:
            return Answer(text.strip().upper())
        except ValueError:
            raise Rob2Error("UnreadableAnswer", value=text) from None


class Judgement(Enum):
    """A risk-of-bias judgement; the value is the wire form."""

    LOW = "low"
    SOME_CONCERNS = "some_concerns"
    HIGH = "high"


class Scope(Enum):
    """Whether a domain is judged once per study or once per outcome."""

    STUDY = "study"
    OUTCOME = "outcome"


class Domain(Enum):
    """The five RoB 2 domains; the value is the wire form."""

    D1 = "D1"
    D2 = "D2"
    D3 = "D3"
    D4 = "D4"
    D5 = "D5"

    def scope(self) -> Scope:
        """Domain 1 (randomisation) is judged per study, the rest per outcome."""
        return Scope.STUDY if self is Domain.D1 else Scope.OUTCOME


DOMAINS_ALL: tuple[Domain, ...] = tuple(Domain)
"""All five domains in order (Rust's ``Domain::ALL``)."""

Answers = Mapping[str, Answer]
"""Question id such as ``"2.3"`` to its answer; a question not asked is absent."""

_QUESTIONS: dict[Domain, tuple[str, ...]] = {
    Domain.D1: ("1.1", "1.2", "1.3"),
    Domain.D2: ("2.1", "2.2", "2.3", "2.4", "2.5", "2.6", "2.7"),
    Domain.D3: ("3.1", "3.2", "3.3", "3.4"),
    Domain.D4: ("4.1", "4.2", "4.3", "4.4", "4.5"),
    Domain.D5: ("5.1", "5.2", "5.3"),
}

_NO_NI_QUESTIONS = frozenset({"3.2"})
"""Questions whose allowed answers exclude NI."""

_YES = frozenset({Answer.Y, Answer.PY})
_NO = frozenset({Answer.N, Answer.PN})
_YES_NI = _YES | {Answer.NI}
_NO_NI = _NO | {Answer.NI}

_Cond = Callable[[Answers], bool]


def _in(question: str, allowed: frozenset[Answer]) -> _Cond:
    return lambda answers: answers.get(question) in allowed


# The conditions for asking a question, in one place; a question absent here
# is always asked. A prerequisite that is itself unanswered counts as "not met".
_CONDITIONS: dict[str, _Cond] = {
    "2.3": lambda a: _in("2.1", _YES_NI)(a) or _in("2.2", _YES_NI)(a),
    "2.4": _in("2.3", _YES),
    "2.5": _in("2.4", _YES_NI),
    "2.7": _in("2.6", _NO_NI),
    "3.2": _in("3.1", _NO_NI),
    "3.3": _in("3.2", _NO),
    "3.4": _in("3.3", _YES_NI),
    "4.3": lambda a: _in("4.1", _NO_NI)(a) and _in("4.2", _NO_NI)(a),
    "4.4": _in("4.3", _YES_NI),
    "4.5": _in("4.4", _YES_NI),
}


class Rob2Error(ValueError):
    """A refusal; ``kind`` names the variant (the same names as in Rust).

    Kinds: ``MissingAnswer`` and ``UnknownQuestion`` (``question``),
    ``NotAllowed`` (``question`` and ``answer``), ``EscalationUndecided``, and
    ``UnreadableAnswer`` (``value``).
    """

    def __init__(
        self,
        kind: str,
        *,
        question: str | None = None,
        answer: Answer | None = None,
        value: str | None = None,
    ) -> None:
        detail = {"question": question, "answer": answer and answer.value, "value": value}
        super().__init__(f"{kind} { ({k: v for k, v in detail.items() if v is not None}) }")
        self.kind = kind
        self.question = question
        self.answer = answer
        self.value = value


def questions(domain: Domain) -> tuple[str, ...]:
    """The domain's signalling-question ids, in order."""
    return _QUESTIONS[domain]


def applicable_questions(domain: Domain, answers: Answers) -> list[str]:
    """The questions to ask given the answers so far, in order.

    A question whose prerequisite is not yet answered is not listed.
    """
    return [q for q in _QUESTIONS[domain] if q not in _CONDITIONS or _CONDITIONS[q](answers)]


def _checked(domain: Domain, answers: Answers) -> dict[str, str]:
    """Validate ``answers`` and return the applicable ones collapsed to Y / N / NI."""
    for question in answers:
        if question not in _QUESTIONS[domain]:
            raise Rob2Error("UnknownQuestion", question=question)
    collapsed: dict[str, str] = {}
    for question in applicable_questions(domain, answers):
        answer = answers.get(question)
        if answer is None:
            raise Rob2Error("MissingAnswer", question=question)
        if answer is Answer.NI and question in _NO_NI_QUESTIONS:
            raise Rob2Error("NotAllowed", question=question, answer=answer)
        collapsed[question] = "Y" if answer in _YES else "N" if answer in _NO else "NI"
    return collapsed


def _d1(c: dict[str, str]) -> Judgement:
    if c["1.2"] == "Y" and c["1.1"] in ("Y", "NI") and c["1.3"] in ("N", "NI"):
        return Judgement.LOW
    if c["1.2"] == "N" or (c["1.2"] == "NI" and c["1.3"] == "Y"):
        return Judgement.HIGH
    return Judgement.SOME_CONCERNS


def _d2(c: dict[str, str]) -> Judgement:
    if "2.3" not in c or c["2.3"] == "N":
        part1 = Judgement.LOW
    elif c["2.3"] == "NI":
        part1 = Judgement.SOME_CONCERNS
    elif c["2.4"] == "N":
        part1 = Judgement.SOME_CONCERNS
    else:  # 2.4 Y or NI, so 2.5 was asked
        part1 = Judgement.SOME_CONCERNS if c["2.5"] == "Y" else Judgement.HIGH
    if c["2.6"] == "Y":
        part2 = Judgement.LOW
    else:
        part2 = Judgement.SOME_CONCERNS if c["2.7"] == "N" else Judgement.HIGH
    parts = (part1, part2)
    if Judgement.HIGH in parts:
        return Judgement.HIGH
    return Judgement.SOME_CONCERNS if Judgement.SOME_CONCERNS in parts else Judgement.LOW


def _d3(c: dict[str, str]) -> Judgement:
    if c["3.1"] == "Y" or c.get("3.2") == "Y" or c.get("3.3") == "N":
        return Judgement.LOW
    return Judgement.SOME_CONCERNS if c["3.4"] == "N" else Judgement.HIGH


def _d4(c: dict[str, str]) -> Judgement:
    if c["4.1"] == "Y" or c["4.2"] == "Y":
        return Judgement.HIGH
    five = c.get("4.5")
    if c["4.2"] == "N":
        if c["4.3"] == "N" or c.get("4.4") == "N":
            return Judgement.LOW
        return Judgement.SOME_CONCERNS if five == "N" else Judgement.HIGH
    return Judgement.HIGH if five in ("Y", "NI") else Judgement.SOME_CONCERNS


def _d5(c: dict[str, str]) -> Judgement:
    if c["5.2"] == "Y" or c["5.3"] == "Y":
        return Judgement.HIGH
    if c["5.1"] == "Y" and c["5.2"] == "N" and c["5.3"] == "N":
        return Judgement.LOW
    return Judgement.SOME_CONCERNS


_RULES: dict[Domain, Callable[[dict[str, str]], Judgement]] = {
    Domain.D1: _d1,
    Domain.D2: _d2,
    Domain.D3: _d3,
    Domain.D4: _d4,
    Domain.D5: _d5,
}


def domain_judgement(domain: Domain, answers: Answers) -> Judgement:
    """The domain's judgement from its signalling answers.

    Raises ``Rob2Error``: ``UnknownQuestion`` for an id outside the domain,
    ``MissingAnswer`` for an applicable question left unanswered,
    ``NotAllowed`` for an answer the question does not allow (3.2 has no NI).
    Answers to questions that are not applicable are ignored.
    """
    return _RULES[domain](_checked(domain, answers))


def needs_escalation_decision(domains: Sequence[Judgement]) -> bool:
    """True when the guidance leaves the overall judgement to the assessor.

    That is: no domain is High and at least two have some concerns.
    """
    return Judgement.HIGH not in domains and (
        sum(d is Judgement.SOME_CONCERNS for d in domains) >= 2
    )


def overall_judgement(domains: Sequence[Judgement], escalate: bool | None = None) -> Judgement:
    """The overall judgement (Table 1) from the five domain judgements.

    ``escalate`` matters only when ``needs_escalation_decision`` is true; then
    ``None`` raises ``Rob2Error`` of kind ``EscalationUndecided``.
    """
    if Judgement.HIGH in domains:
        return Judgement.HIGH
    if needs_escalation_decision(domains):
        if escalate is None:
            raise Rob2Error("EscalationUndecided")
        return Judgement.HIGH if escalate else Judgement.SOME_CONCERNS
    if all(d is Judgement.LOW for d in domains):
        return Judgement.LOW
    return Judgement.SOME_CONCERNS
