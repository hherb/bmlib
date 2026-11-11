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

"""The RoB 2 decision rules (Tables 1, 3-4, 6, 10, 12, 14 of the 2019 guidance)."""

from __future__ import annotations

import pytest

from bmlib.quality.rob2_algorithm import (
    Answer,
    Domain,
    Judgement,
    Rob2Error,
    Scope,
    applicable_questions,
    domain_judgement,
    needs_escalation_decision,
    overall_judgement,
    questions,
)


def judge(domain: str, answers: dict[str, str]) -> str:
    """Parse the answer strings, judge the domain, return the wire value."""
    parsed = {q: Answer.parse(a) for q, a in answers.items()}
    return domain_judgement(Domain(domain), parsed).value


def _ids(cases: list[tuple[dict[str, str], str]]) -> list[str]:
    return [" ".join(f"{q}={a}" for q, a in c[0].items()) for c in cases]


D1 = [
    ({"1.1": "Y", "1.2": "Y", "1.3": "N"}, "low"),
    ({"1.1": "NI", "1.2": "PY", "1.3": "NI"}, "low"),
    ({"1.1": "Y", "1.2": "Y", "1.3": "Y"}, "some_concerns"),
    ({"1.1": "N", "1.2": "Y", "1.3": "N"}, "some_concerns"),
    ({"1.1": "Y", "1.2": "NI", "1.3": "N"}, "some_concerns"),
    ({"1.1": "Y", "1.2": "NI", "1.3": "PY"}, "high"),
    ({"1.1": "Y", "1.2": "N", "1.3": "N"}, "high"),
]
D2 = [
    ({"2.1": "N", "2.2": "PN", "2.6": "Y"}, "low"),
    ({"2.1": "Y", "2.2": "N", "2.3": "N", "2.6": "Y"}, "low"),
    ({"2.1": "Y", "2.2": "N", "2.3": "NI", "2.6": "Y"}, "some_concerns"),
    ({"2.1": "Y", "2.2": "N", "2.3": "Y", "2.4": "N", "2.6": "Y"}, "some_concerns"),
    ({"2.1": "Y", "2.2": "N", "2.3": "Y", "2.4": "Y", "2.5": "Y", "2.6": "Y"}, "some_concerns"),
    ({"2.1": "Y", "2.2": "N", "2.3": "Y", "2.4": "NI", "2.5": "N", "2.6": "Y"}, "high"),
    ({"2.1": "N", "2.2": "N", "2.6": "N", "2.7": "N"}, "some_concerns"),
    ({"2.1": "N", "2.2": "N", "2.6": "NI", "2.7": "NI"}, "high"),
]
D3 = [
    ({"3.1": "Y"}, "low"),
    ({"3.1": "N", "3.2": "Y"}, "low"),
    ({"3.1": "N", "3.2": "N", "3.3": "N"}, "low"),
    ({"3.1": "NI", "3.2": "PN", "3.3": "NI", "3.4": "N"}, "some_concerns"),
    ({"3.1": "NI", "3.2": "PN", "3.3": "NI", "3.4": "NI"}, "high"),
]
D4 = [
    ({"4.1": "N", "4.2": "N", "4.3": "N"}, "low"),
    ({"4.1": "NI", "4.2": "N", "4.3": "Y", "4.4": "N"}, "low"),
    ({"4.1": "N", "4.2": "N", "4.3": "Y", "4.4": "Y", "4.5": "N"}, "some_concerns"),
    ({"4.1": "N", "4.2": "N", "4.3": "Y", "4.4": "Y", "4.5": "PY"}, "high"),
    ({"4.1": "N", "4.2": "NI", "4.3": "N"}, "some_concerns"),
    ({"4.1": "N", "4.2": "NI", "4.3": "Y", "4.4": "Y", "4.5": "Y"}, "high"),
    ({"4.1": "Y", "4.2": "N"}, "high"),
    ({"4.1": "N", "4.2": "PY"}, "high"),
]
D5 = [
    ({"5.1": "Y", "5.2": "N", "5.3": "N"}, "low"),
    ({"5.1": "NI", "5.2": "N", "5.3": "N"}, "some_concerns"),
    ({"5.1": "Y", "5.2": "N", "5.3": "NI"}, "some_concerns"),
    ({"5.1": "Y", "5.2": "NI", "5.3": "NI"}, "some_concerns"),
    ({"5.1": "Y", "5.2": "PY", "5.3": "N"}, "high"),
    ({"5.1": "N", "5.2": "N", "5.3": "Y"}, "high"),
]


@pytest.mark.parametrize("answers, expected", D1, ids=_ids(D1))
def test_d1(answers: dict[str, str], expected: str) -> None:
    assert judge("D1", answers) == expected


@pytest.mark.parametrize("answers, expected", D2, ids=_ids(D2))
def test_d2(answers: dict[str, str], expected: str) -> None:
    assert judge("D2", answers) == expected


@pytest.mark.parametrize("answers, expected", D3, ids=_ids(D3))
def test_d3(answers: dict[str, str], expected: str) -> None:
    assert judge("D3", answers) == expected


@pytest.mark.parametrize("answers, expected", D4, ids=_ids(D4))
def test_d4(answers: dict[str, str], expected: str) -> None:
    assert judge("D4", answers) == expected


@pytest.mark.parametrize("answers, expected", D5, ids=_ids(D5))
def test_d5(answers: dict[str, str], expected: str) -> None:
    assert judge("D5", answers) == expected


def _refused(domain: str, answers: dict[str, str]) -> Rob2Error:
    with pytest.raises(Rob2Error) as caught:
        judge(domain, answers)
    return caught.value


def test_a_disallowed_answer_is_refused() -> None:
    error = _refused("D3", {"3.1": "N", "3.2": "NI"})
    assert (error.kind, error.question, error.answer) == ("NotAllowed", "3.2", Answer.NI)


def test_an_unanswered_applicable_question_is_refused() -> None:
    error = _refused("D2", {"2.1": "Y", "2.2": "N", "2.6": "Y"})
    assert (error.kind, error.question) == ("MissingAnswer", "2.3")
    assert _refused("D4", {"4.1": "Y"}).question == "4.2"


def test_a_question_outside_the_domain_is_refused() -> None:
    error = _refused("D1", {"1.1": "Y", "1.2": "Y", "1.3": "N", "9.9": "Y"})
    assert (error.kind, error.question) == ("UnknownQuestion", "9.9")


def test_an_answer_to_a_question_not_asked_is_ignored() -> None:
    assert judge("D3", {"3.1": "Y", "3.2": "NI"}) == "low"


def test_parse_tolerates_case_and_space_and_refuses_the_rest() -> None:
    assert Answer.parse(" py ") is Answer.PY
    for text in ("NA", "Unclear", ""):
        with pytest.raises(Rob2Error) as caught:
            Answer.parse(text)
        assert (caught.value.kind, caught.value.value) == ("UnreadableAnswer", text)


def test_applicable_questions_follow_the_answers() -> None:
    n = {"2.1": Answer.N, "2.2": Answer.N}
    assert applicable_questions(Domain.D2, n) == ["2.1", "2.2", "2.6"]
    assert applicable_questions(Domain.D2, {**n, "2.6": Answer.NI}) == ["2.1", "2.2", "2.6", "2.7"]


def test_questions_scope_and_domains() -> None:
    assert [len(questions(d)) for d in Domain] == [3, 7, 4, 5, 3]
    assert [d.scope() for d in Domain] == [Scope.STUDY] + [Scope.OUTCOME] * 4


L, S, H = Judgement.LOW, Judgement.SOME_CONCERNS, Judgement.HIGH


def test_overall() -> None:
    assert overall_judgement([L] * 5) is L
    assert not needs_escalation_decision([L, L, S, L, L])
    assert overall_judgement([L, L, S, L, L]) is S
    assert needs_escalation_decision([L, S, S, L, L])
    with pytest.raises(Rob2Error) as caught:
        overall_judgement([L, S, S, L, L])
    assert caught.value.kind == "EscalationUndecided"
    assert overall_judgement([L, S, S, L, L], False) is S
    assert overall_judgement([L, S, S, L, L], True) is H
    assert overall_judgement([L, S, H, S, L]) is H
    assert not needs_escalation_decision([S, S, H, L, L])
    assert overall_judgement([S, S, H, L, L], False) is H


def test_probably_answers_judge_as_their_certain_forms() -> None:
    """Y/PY and N/PN are interchangeable, over every walk of every domain."""
    for domain in Domain:
        for answers in _walks(domain):
            twin = {
                q: {Answer.Y: Answer.PY, Answer.N: Answer.PN}.get(a, a) for q, a in answers.items()
            }
            assert domain_judgement(domain, answers) == domain_judgement(domain, twin)


def _walks(domain: Domain) -> list[dict[str, Answer]]:
    """Every complete answer set over Y / N / NI along the applicable branch."""
    done: list[dict[str, Answer]] = []
    stack: list[dict[str, Answer]] = [{}]
    while stack:
        answers = stack.pop()
        pending = [q for q in applicable_questions(domain, answers) if q not in answers]
        if not pending:
            done.append(answers)
            continue
        for answer in (Answer.Y, Answer.N, Answer.NI):
            if not (answer is Answer.NI and pending[0] == "3.2"):
                stack.append({**answers, pending[0]: answer})
    return done


def test_every_walk_gets_a_judgement() -> None:
    for domain in Domain:
        walks = _walks(domain)
        assert walks
        for answers in walks:
            domain_judgement(domain, answers)


def test_leftover_answers_do_not_open_later_questions() -> None:
    leftover = {
        k: Answer.parse(v) for k, v in {"4.1": "N", "4.2": "NI", "4.3": "N", "4.4": "Y"}.items()
    }
    assert applicable_questions(Domain.D4, leftover) == ["4.1", "4.2", "4.3"]
    assert (
        judge("D4", {"4.1": "N", "4.2": "NI", "4.3": "N", "4.4": "Y", "4.5": "Y"})
        == "some_concerns"
    )
    assert judge("D2", {"2.1": "Y", "2.2": "N", "2.3": "N", "2.4": "Y", "2.6": "Y"}) == "low"
    assert judge("D3", {"3.1": "Y", "3.2": "N", "3.3": "Y"}) == "low"


def test_raw_strings_are_not_answers() -> None:
    with pytest.raises(Rob2Error) as caught:
        domain_judgement(Domain.D1, {"1.1": "Y", "1.2": "Y", "1.3": "N"})  # type: ignore[dict-item]
    assert caught.value.kind == "UnreadableAnswer"


def test_domain_lists_must_be_five_judgements() -> None:
    for call in (overall_judgement, needs_escalation_decision):
        with pytest.raises(ValueError):
            call([])
        with pytest.raises(ValueError):
            call([L] * 6)
        with pytest.raises(TypeError):
            call(["low"] * 5)  # type: ignore[list-item]
