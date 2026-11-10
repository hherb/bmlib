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

"""Tests for the RoB 2 assessor: the questions, reading a domain reply, and the agent."""

from __future__ import annotations

import json
import threading
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from bmlib.citations import PassageContradiction
from bmlib.context_processor import ProcessingConfig, ProcessingStatus
from bmlib.llm.data_types import LLMResponse
from bmlib.quality._condense import CondensationRefusedError
from bmlib.quality.evidence_ladder import EffectScale, ReportedResult
from bmlib.quality.outcome_agent import ProposedOutcome
from bmlib.quality.pico_models import PicoContradictedQuote
from bmlib.quality.rob2_agent import (
    ROB2_SYSTEM_PROMPT,
    DomainAssessment,
    DomainFailed,
    Escalation,
    OutcomeRob2,
    Rob2Agent,
    Rob2Run,
    Rob2RunError,
    Rob2RunErrorKind,
    domain_prompt,
    read_domain_reply,
    read_escalation_reply,
)
from bmlib.quality.rob2_algorithm import (
    ROB2_ALGORITHM_VERSION,
    Answer,
    Domain,
    Judgement,
    questions,
)
from bmlib.quality.rob2_questions import (
    D5_OUTCOME_SWITCHING,
    ESCALATION_PROMPT,
    ROB2_CONDENSE_QUERIES,
    ROB2_PROMPT_VERSION,
    domain_title,
    question_text,
)

RANDOM = "Participants were randomly assigned using a computer-generated list."
CENTRAL = "Allocation was concealed by a central web-based service."
TEXT = (
    f"{RANDOM} {CENTRAL} Baseline characteristics were similar between groups. "
    "Participants and clinicians were blinded with an identical placebo. "
    "Outcome data were available for 98% of participants. "
    "The analysis followed the registered protocol, NCT00000000, at 12 weeks."
)
SIM = 0.9


def entry(answer: str, *quotes: Any, reasoning: Any = "Because.") -> dict[str, Any]:
    return {"answer": answer, "quotes": list(quotes), "reasoning": reasoning}


#: Answers that make each domain low.
LOW: dict[Domain, dict[str, dict[str, Any]]] = {
    Domain.D1: {"1.1": entry("Y", RANDOM), "1.2": entry("Y", CENTRAL), "1.3": entry("N")},
    Domain.D2: {
        "2.1": entry("N"),
        "2.2": entry("N"),
        "2.3": entry("NA"),
        "2.4": entry("NA"),
        "2.5": entry("NA"),
        "2.6": entry("Y"),
        "2.7": entry("NA"),
    },
    Domain.D3: {"3.1": entry("Y"), "3.2": entry("NA"), "3.3": entry("NA"), "3.4": entry("NA")},
    Domain.D4: {"4.1": entry("N"), "4.2": entry("N"), "4.3": entry("N"), "4.4": entry("NA")},
    Domain.D5: {"5.1": entry("Y"), "5.2": entry("N"), "5.3": entry("N")},
}

#: Answers that give some concerns in D1 and in D5.
CONCERNS: dict[Domain, dict[str, dict[str, Any]]] = {
    Domain.D1: {"1.1": entry("Y"), "1.2": entry("NI"), "1.3": entry("N")},
    Domain.D5: {"5.1": entry("NI"), "5.2": entry("N"), "5.3": entry("N")},
}


def reply(domain: Domain, **overrides: Any) -> dict[str, Any]:
    """The low reply for *domain*, with ids replaced (``q2_3=...`` for ``"2.3"``)."""
    answers = dict(LOW[domain])
    for key, value in overrides.items():
        question = key[1:].replace("_", ".")
        if value is None:
            answers.pop(question, None)
        else:
            answers[question] = value
    return {"answers": answers}


def read(domain: Domain, data: Any) -> DomainAssessment:
    return read_domain_reply(domain, data, TEXT, SIM)


def refused(domain: Domain, data: Any) -> str:
    with pytest.raises(ValueError) as raised:
        read(domain, data)
    return str(raised.value)


class TestTheQuestions:
    def test_every_question_has_its_own_self_contained_text(self) -> None:
        texts = [question_text(q) for d in Domain for q in questions(d)]
        assert len(texts) == 22 and len(set(texts)) == 22
        for text in texts:
            assert "NI" in text and "previous question" not in text

    def test_every_question_says_what_each_answer_means_and_when_it_applies(self) -> None:
        # 3.3 is asked on a branch too, but always answered (see the next test).
        conditional = {"2.3", "2.4", "2.5", "2.7", "3.2", "3.4", "4.3", "4.4", "4.5"}
        for question in (q for d in Domain for q in questions(d)):
            lines = question_text(question).split("\n")
            for label in ("Y:", "PY:", "PN:", "N:", "NI:"):
                assert sum(line.startswith(label) for line in lines) == 1, (question, label)
            applies = [line for line in lines if line.startswith("Applies only when:")]
            assert len(applies) == (question in conditional), question
            assert all("answer NA" in line for line in applies), question

    def test_question_3_3_is_answered_even_when_the_amount_missing_is_unknown(self) -> None:
        # Its branch can open on 3.1 NI, so the model must not steer itself to NA.
        text = question_text("3.3")
        assert "do not answer NA" in text and "does not say how many" in text

    def test_an_unknown_id_is_refused(self) -> None:
        with pytest.raises(KeyError, match="2.9"):
            question_text("2.9")

    def test_each_domain_has_a_condense_query_and_a_title(self) -> None:
        assert [d for d, _ in ROB2_CONDENSE_QUERIES] == list(Domain)
        assert all(query for _, query in ROB2_CONDENSE_QUERIES)
        assert len({domain_title(d) for d in Domain}) == 5

    def test_the_constants(self) -> None:
        assert ROB2_PROMPT_VERSION == 3
        assert '"escalate"' in ESCALATION_PROMPT and "registered" in D5_OUTCOME_SWITCHING


class TestReadingADomainReply:
    def test_a_complete_d1_reply_is_low_with_its_quotes_located(self) -> None:
        found = read(Domain.D1, reply(Domain.D1))

        assert found.domain is Domain.D1 and found.judgement is Judgement.LOW
        assert list(found.answers) == ["1.1", "1.2", "1.3"]
        assert found.answers["1.1"].answer is Answer.Y
        assert [c.text for c in found.answers["1.1"].citations] == [RANDOM]
        assert found.answers["1.1"].citations[0].start == 0
        assert found.answers["1.1"].reasoning == "Because."
        assert found.condensed is False and found.ignored == []

    def test_na_for_an_applicable_question_fails_naming_it(self) -> None:
        bad = reply(Domain.D2, q2_1=entry("Y"), q2_3=entry("NA"))
        assert refused(Domain.D2, bad) == "answers.2.3 is applicable but not answered"

    def test_an_omitted_applicable_question_fails_naming_it(self) -> None:
        assert refused(Domain.D2, reply(Domain.D2, q2_6=None)) == (
            "answers.2.6 is applicable but not answered"
        )

    def test_ni_where_the_question_allows_none_fails_naming_it(self) -> None:
        bad = reply(Domain.D3, q3_1=entry("N"), q3_2=entry("NI"))
        assert refused(Domain.D3, bad) == "answers.3.2.answer NI is not allowed for this question"

    def test_case_and_surrounding_space_are_tolerated(self) -> None:
        found = read(Domain.D1, reply(Domain.D1, q1_1=entry(" py ")))
        assert found.answers["1.1"].answer is Answer.PY

    @pytest.mark.parametrize("label", ["Unclear", "yes", "", "N.A.", "not app"])
    def test_an_answer_outside_the_set_fails(self, label: str) -> None:
        assert refused(Domain.D1, reply(Domain.D1, q1_1=entry(label))) == (
            f'answers.1.1.answer "{label}" is not one of Y, PY, PN, N, NI, NA'
        )

    @pytest.mark.parametrize("label", ["NA", " na ", "N/A", "n/a", "n / a", "Not applicable"])
    def test_every_spelling_of_not_applicable_reads_as_na(self, label: str) -> None:
        found = read(Domain.D2, reply(Domain.D2, q2_7=entry(label)))
        assert found.ignored == [] and "2.7" not in found.answers
        assert refused(Domain.D2, reply(Domain.D2, q2_6=entry(label))) == (
            "answers.2.6 is applicable but not answered"
        )

    @pytest.mark.parametrize(
        "item", [entry("Unclear"), entry(3), "Y", entry("Y", reasoning=3)], ids=str
    )
    def test_an_unreadable_entry_on_a_question_that_does_not_apply_is_ignored(
        self, item: Any
    ) -> None:
        # 2.6 is Y in the low reply, so 2.7 is not asked.
        found = read(Domain.D2, reply(Domain.D2, q2_7=item))
        assert found.judgement is Judgement.LOW
        assert found.ignored == ["2.7"] and "2.7" not in found.answers

    def test_an_unreadable_entry_on_a_question_that_applies_still_fails(self) -> None:
        bad = reply(Domain.D2, q2_6=entry("N"), q2_7=entry("Unclear"))
        assert refused(Domain.D2, bad) == (
            'answers.2.7.answer "Unclear" is not one of Y, PY, PN, N, NI, NA'
        )

    def test_an_answer_to_a_question_that_does_not_apply_is_listed_as_ignored(self) -> None:
        found = read(Domain.D2, reply(Domain.D2, q2_7=entry("Y")))
        assert found.judgement is Judgement.LOW
        assert found.ignored == ["2.7"]
        assert "2.7" not in found.answers

    def test_an_ni_answer_without_quotes_is_kept(self) -> None:
        found = read(Domain.D1, reply(Domain.D1, q1_2=entry("NI")))
        assert found.answers["1.2"].answer is Answer.NI
        assert found.answers["1.2"].citations == []
        assert found.judgement is Judgement.SOME_CONCERNS

    def test_lost_contradicted_and_dropped_quotes_each_have_their_place(self) -> None:
        lost = "Participants were followed for a year."
        changed = "Outcome data were available for 90% of participants."
        found = read(Domain.D3, reply(Domain.D3, q3_1=entry("Y", lost, changed, "  ", 7)))
        answer = found.answers["3.1"]
        assert answer.answer is Answer.Y  # the answer stands
        assert answer.quotes_not_found == [lost]
        assert answer.contradicted == [PicoContradictedQuote(changed, PassageContradiction.NUMBER)]
        assert answer.quotes_dropped == 2

    def test_an_unknown_id_fails(self) -> None:
        assert refused(Domain.D2, reply(Domain.D2, q2_9=entry("Y"))) == (
            "answers.2.9 is not a question of D2"
        )

    @pytest.mark.parametrize(
        ("bad", "message"),
        [
            ([], "expected an object"),
            ({}, "answers is not an object"),
            ({"answers": []}, "answers is not an object"),
            ({"answers": {**LOW[Domain.D1], "1.1": "Y"}}, "answers.1.1 is not an object"),
            (
                {"answers": {**LOW[Domain.D1], "1.1": {"answer": 1}}},
                "answers.1.1.answer is not text",
            ),
            (reply(Domain.D1, q1_1=entry("Y", reasoning=3)), "answers.1.1.reasoning is not text"),
        ],
    )
    def test_a_malformed_reply_names_the_field(self, bad: Any, message: str) -> None:
        assert refused(Domain.D1, bad) == message

    def test_absent_reasoning_is_empty(self) -> None:
        found = read(Domain.D1, reply(Domain.D1, q1_3={"answer": "N"}))
        assert found.answers["1.3"].reasoning == ""

    @pytest.mark.parametrize("bad", [0.0, 1.5, True, "0.9"])
    def test_a_bad_similarity_is_refused(self, bad: Any) -> None:
        with pytest.raises(ValueError, match="min_similarity"):
            read_domain_reply(Domain.D1, reply(Domain.D1), TEXT, bad)


class TestReadingAnEscalationReply:
    def test_a_decision_with_its_reasoning(self) -> None:
        assert read_escalation_reply({"escalate": True, "reasoning": "Two."}) == Escalation(
            True, "Two."
        )
        assert read_escalation_reply({"escalate": False}) == Escalation(False, "")

    @pytest.mark.parametrize(
        ("bad", "message"),
        [
            ([], "expected an object"),
            ({}, "escalate is not true or false"),
            ({"escalate": "true"}, "escalate is not true or false"),
            ({"escalate": 1}, "escalate is not true or false"),
            ({"escalate": True, "reasoning": 2}, "reasoning is not text"),
        ],
    )
    def test_anything_but_a_boolean_is_refused(self, bad: Any, message: str) -> None:
        with pytest.raises(ValueError) as raised:
            read_escalation_reply(bad)
        assert str(raised.value) == message


# --- The agent ---

OUTCOME = ProposedOutcome(
    "Systolic blood pressure",
    "12 weeks",
    ReportedResult("mean difference", EffectScale.DIFFERENCE, -4.2, -6.1, -2.3, 0.95, 412),
)
SECOND = ProposedOutcome("Diastolic blood pressure", None, None)


def _response(content: str) -> LLMResponse:
    return LLMResponse(
        content=content, model="test:model", input_tokens=1, output_tokens=1, stop_reason="stop"
    )


def make_agent(*replies: Any, **kwargs: Any) -> Rob2Agent:
    """An agent whose model answers *replies* in order (a string is sent as is)."""
    llm = MagicMock()
    responses = [_response(r if isinstance(r, str) else json.dumps(r)) for r in replies]
    llm.chat = MagicMock(side_effect=responses)
    return Rob2Agent(llm=llm, model="test:model", **kwargs)


def low_run(outcomes: int = 1) -> list[dict[str, Any]]:
    """The replies of a run in which every domain is low."""
    return [reply(Domain.D1)] + [reply(d) for d in list(Domain)[1:]] * outcomes


def sent(agent: Rob2Agent) -> list[str]:
    return [c.kwargs["messages"][-1].content for c in agent.llm.chat.call_args_list]


class ScriptedCondenser:
    """A condenser that records its queries and returns a fixed digest."""

    def __init__(self, digest: str) -> None:
        self.digest = digest
        self.queries: list[str] = []

    def condense(
        self, query: str, text: str, label: str, config: ProcessingConfig, *, stage: str
    ) -> tuple[str, list[str], ProcessingStatus]:
        self.queries.append(query)
        return self.digest, [], ProcessingStatus.COMPLETED


@pytest.fixture(autouse=True)
def _no_backoff() -> Any:
    with patch("bmlib.agents.base.time.sleep"):
        yield


def assessed(result: DomainAssessment | DomainFailed) -> DomainAssessment:
    assert isinstance(result, DomainAssessment), result
    return result


class TestTheAgent:
    def test_one_outcome_is_five_calls_in_domain_order(self) -> None:
        agent = make_agent(*low_run())

        run = agent.assess("A trial", TEXT, [OUTCOME], min_similarity=SIM)

        assert agent.llm.chat.call_count == 5
        prompts = sent(agent)
        for prompt, domain in zip(prompts, Domain, strict=True):
            assert domain_title(domain) in prompt and TEXT in prompt
        assert "Systolic blood pressure" not in prompts[0]
        assert all("Systolic blood pressure" in p and '"estimate":-4.2' in p for p in prompts[1:])
        assert D5_OUTCOME_SWITCHING in prompts[4] and D5_OUTCOME_SWITCHING not in prompts[3]
        system = agent.llm.chat.call_args.kwargs["messages"][0].content
        assert system == ROB2_SYSTEM_PROMPT
        assert run.algorithm_version == ROB2_ALGORITHM_VERSION
        assert run.prompt_version == ROB2_PROMPT_VERSION
        assert assessed(run.study).judgement is Judgement.LOW
        [only] = run.outcomes
        assert only.outcome == "Systolic blood pressure"
        assert [assessed(d).domain for d in only.domains] == list(Domain)[1:]
        assert only.escalation is None and only.overall is Judgement.LOW

    def test_two_outcomes_are_nine_calls(self) -> None:
        agent = make_agent(*low_run(2))
        run = agent.assess("A trial", TEXT, [OUTCOME, SECOND], min_similarity=SIM)
        assert agent.llm.chat.call_count == 9
        assert [o.outcome for o in run.outcomes] == [OUTCOME.name, SECOND.name]
        assert "Diastolic blood pressure" in sent(agent)[5]
        assert all(o.overall is Judgement.LOW for o in run.outcomes)

    @pytest.mark.parametrize(
        ("escalate", "overall"), [(True, Judgement.HIGH), (False, Judgement.SOME_CONCERNS)]
    )
    def test_two_some_concerns_ask_a_sixth_question(
        self, escalate: bool, overall: Judgement
    ) -> None:
        replies = [{"answers": CONCERNS[Domain.D1]}] + [reply(d) for d in list(Domain)[1:4]]
        replies += [{"answers": CONCERNS[Domain.D5]}, {"escalate": escalate, "reasoning": "R."}]
        agent = make_agent(*replies)

        run = agent.assess("A trial", TEXT, [OUTCOME], min_similarity=SIM)

        assert agent.llm.chat.call_count == 6
        assert sent(agent)[5].startswith(ESCALATION_PROMPT)
        [only] = run.outcomes
        assert only.escalation == Escalation(escalate, "R.")
        assert only.overall is overall

    def test_an_unreadable_escalation_reply_leaves_the_outcome_not_assessed(self) -> None:
        replies = [{"answers": CONCERNS[Domain.D1]}] + [reply(d) for d in list(Domain)[1:4]]
        replies += [{"answers": CONCERNS[Domain.D5]}, {"escalate": "yes"}, {"escalate": "yes"}]
        agent = make_agent(*replies)

        [only] = agent.assess("A trial", TEXT, [OUTCOME], min_similarity=SIM).outcomes

        assert agent.llm.chat.call_count == 7
        assert only.escalation is None and only.overall is None
        assert only.escalation_error is not None and "escalate" in only.escalation_error

    @pytest.mark.parametrize(
        ("code", "retryable", "status"), [(401, False, 401), (503, True, 503), (None, True, None)]
    )
    def test_a_failed_escalation_request_says_whether_to_retry_and_the_status(
        self, code: int | None, retryable: bool, status: int | None
    ) -> None:
        failure = ConnectionError("down")
        if code is not None:
            failure.status_code = code  # type: ignore[attr-defined]
        replies = [{"answers": CONCERNS[Domain.D1]}] + [reply(d) for d in list(Domain)[1:4]]
        replies += [{"answers": CONCERNS[Domain.D5]}]
        agent = make_agent(*replies)
        responses = list(agent.llm.chat.side_effect)
        agent.llm.chat.side_effect = [*responses, failure]

        with patch("bmlib.agents.base.time.sleep"):
            [only] = agent.assess("A trial", TEXT, [OUTCOME], min_similarity=SIM).outcomes

        assert only.escalation_error is not None
        assert (only.escalation_retryable, only.escalation_status) == (retryable, status)

    def test_an_unreadable_escalation_is_not_retryable(self) -> None:
        replies = [{"answers": CONCERNS[Domain.D1]}] + [reply(d) for d in list(Domain)[1:4]]
        replies += [{"answers": CONCERNS[Domain.D5]}, {"escalate": "yes"}, {"escalate": "yes"}]
        [only] = (
            make_agent(*replies).assess("A trial", TEXT, [OUTCOME], min_similarity=SIM).outcomes
        )
        assert (only.escalation_retryable, only.escalation_status) == (False, None)

    def test_an_unreadable_domain_fails_alone(self) -> None:
        bad = {"answers": {"3.1": entry("Unclear")}}
        replies = [reply(Domain.D1), reply(Domain.D2), bad, bad, reply(Domain.D4)]
        agent = make_agent(*replies, reply(Domain.D5))

        run = agent.assess("A trial", TEXT, [OUTCOME], min_similarity=SIM)

        assert agent.llm.chat.call_count == 6
        [only] = run.outcomes
        failed = only.domains[1]
        assert isinstance(failed, DomainFailed) and failed.domain is Domain.D3
        assert "answers.3.1.answer" in failed.reason
        assert [assessed(only.domains[i]).domain for i in (0, 2, 3)] == [
            Domain.D2,
            Domain.D4,
            Domain.D5,
        ]
        assert only.overall is None and only.escalation is None

    def test_a_failed_request_fails_that_domain_alone(self) -> None:
        agent = make_agent(*low_run())
        responses = list(agent.llm.chat.side_effect)
        agent.llm.chat.side_effect = [responses[0], ConnectionError("down"), *responses[2:]]

        run = agent.assess("A trial", TEXT, [OUTCOME], min_similarity=SIM)

        [only] = run.outcomes
        assert isinstance(only.domains[0], DomainFailed) and only.domains[0].domain is Domain.D2
        assert only.overall is None
        assert assessed(only.domains[3]).judgement is Judgement.LOW

    @pytest.mark.parametrize(
        ("code", "retryable", "status"), [(401, False, 401), (503, True, 503), (None, True, None)]
    )
    def test_a_failed_domain_says_whether_to_retry_and_the_status(
        self, code: int | None, retryable: bool, status: int | None
    ) -> None:
        failure = ConnectionError("down")
        if code is not None:
            failure.status_code = code  # type: ignore[attr-defined]
        agent = make_agent(*low_run())
        responses = list(agent.llm.chat.side_effect)
        agent.llm.chat.side_effect = [responses[0], failure, *responses[2:]]

        with patch("bmlib.agents.base.time.sleep"):
            run = agent.assess("A trial", TEXT, [OUTCOME], min_similarity=SIM)

        failed = run.outcomes[0].domains[0]
        assert isinstance(failed, DomainFailed)
        assert (failed.retryable, failed.status) == (retryable, status)

    def test_an_unreadable_domain_is_not_retryable(self) -> None:
        bad = {"answers": {}}
        agent = make_agent(bad, bad, *[reply(d) for d in list(Domain)[1:]])
        run = agent.assess("A trial", TEXT, [OUTCOME], min_similarity=SIM)
        assert isinstance(run.study, DomainFailed)
        assert (run.study.retryable, run.study.status) == (False, None)

    def test_a_request_that_failed_the_condensation_carries_the_refusals_status(self) -> None:
        refused = ConnectionError("HTTP 401")
        refused.status_code = 401  # type: ignore[attr-defined]

        class Refusing(ScriptedCondenser):
            def condense(self, query: str, *args: Any, **kwargs: Any) -> Any:
                raise CondensationRefusedError("the map-reduce failed") from refused

        text = TEXT + " " + "Filler sentence about nothing. " * 20
        agent = make_agent(
            condenser=Refusing("unused"), condense_config=ProcessingConfig(max_context_chars=200)
        )

        run = agent.assess("A trial", text, [OUTCOME], min_similarity=SIM)

        domains = [run.study] + [d for o in run.outcomes for d in o.domains]
        assert all(isinstance(d, DomainFailed) for d in domains)
        assert {(d.retryable, d.status) for d in domains if isinstance(d, DomainFailed)} == {
            (False, 401)
        }

    @pytest.mark.parametrize("bad", ["401", 99, 600, True, 4.5])
    def test_a_stored_failure_with_a_bad_status_is_refused(self, bad: Any) -> None:
        stored = DomainFailed(Domain.D2, "x", False, 401).to_dict()
        stored["status_code"] = bad
        with pytest.raises(ValueError, match="status_code"):
            DomainFailed.from_dict(stored)

    def test_a_stored_failure_without_retryable_is_refused(self) -> None:
        stored = DomainFailed(Domain.D2, "x").to_dict()
        del stored["retryable"]
        with pytest.raises(ValueError, match="retryable"):
            DomainFailed.from_dict(stored)

    def test_a_failed_study_domain_leaves_every_outcome_not_assessed(self) -> None:
        bad = {"answers": {}}
        agent = make_agent(bad, bad, *[reply(d) for d in list(Domain)[1:]])
        run = agent.assess("A trial", TEXT, [OUTCOME], min_similarity=SIM)
        assert isinstance(run.study, DomainFailed) and run.study.domain is Domain.D1
        assert run.outcomes[0].overall is None

    def test_a_stop_between_domain_calls_ends_the_run(self) -> None:
        stop = threading.Event()
        replies = iter(_response(json.dumps(r)) for r in low_run())

        def answer(**kwargs: Any) -> LLMResponse:
            if agent.llm.chat.call_count == 2:
                stop.set()
            return next(replies)

        agent = make_agent(*low_run(), stop=stop)
        agent.llm.chat.side_effect = answer
        with pytest.raises(Rob2RunError) as raised:
            agent.assess("A trial", TEXT, [OUTCOME], min_similarity=SIM)
        assert raised.value.kind is Rob2RunErrorKind.STOPPED
        assert agent.llm.chat.call_count == 2

    @pytest.mark.parametrize(
        ("text", "outcomes", "similarity", "kind"),
        [
            (TEXT, [], SIM, Rob2RunErrorKind.NO_OUTCOMES),
            ("  ", [OUTCOME], SIM, Rob2RunErrorKind.EMPTY_INPUT),
            (TEXT, [OUTCOME], 0.0, Rob2RunErrorKind.INVALID_SIMILARITY),
        ],
    )
    def test_a_refused_call_sends_nothing(
        self, text: str, outcomes: list[ProposedOutcome], similarity: float, kind: Any
    ) -> None:
        agent = make_agent(*low_run())
        with pytest.raises(Rob2RunError) as raised:
            agent.assess("A trial", text, outcomes, min_similarity=similarity)
        assert raised.value.kind is kind
        agent.llm.chat.assert_not_called()

    def test_a_paper_over_the_window_is_condensed_per_domain(self) -> None:
        text = TEXT + " " + "Filler sentence about nothing. " * 20
        condenser = ScriptedCondenser("A digest.")
        agent = make_agent(
            *low_run(2),
            condenser=condenser,
            condense_config=ProcessingConfig(max_context_chars=200),
        )

        run = agent.assess("A trial", text, [OUTCOME, SECOND], min_similarity=SIM)

        assert condenser.queries == [query for _, query in ROB2_CONDENSE_QUERIES]
        assert all("A digest." in p and "Filler sentence" not in p for p in sent(agent))
        domains = [run.study] + [d for o in run.outcomes for d in o.domains]
        assert all(assessed(d).condensed for d in domains)
        # Quotes are verified against the original text, not the digest.
        assert [c.text for c in assessed(run.study).answers["1.1"].citations] == [RANDOM]

    def test_a_failed_condensation_fails_its_domain_once_and_is_not_retried(self) -> None:
        class Refusing(ScriptedCondenser):
            def condense(self, query: str, *args: Any, **kwargs: Any) -> Any:
                self.queries.append(query)
                raise CondensationRefusedError("the digest does not fit")

        text = TEXT + " " + "Filler sentence about nothing. " * 20
        condenser = Refusing("unused")
        agent = make_agent(
            condenser=condenser, condense_config=ProcessingConfig(max_context_chars=200)
        )

        run = agent.assess("A trial", text, [OUTCOME, SECOND], min_similarity=SIM)

        # One attempt per domain, cached for the second outcome; no question is sent.
        assert condenser.queries == [query for _, query in ROB2_CONDENSE_QUERIES]
        agent.llm.chat.assert_not_called()
        domains = [run.study] + [d for o in run.outcomes for d in o.domains]
        assert all(
            isinstance(d, DomainFailed) and "could not be condensed" in d.reason for d in domains
        )
        assert [o.overall for o in run.outcomes] == [None, None]

    def test_a_paper_that_fits_is_sent_whole(self) -> None:
        agent = make_agent(*low_run())
        run = agent.assess("A trial", TEXT, [OUTCOME], min_similarity=SIM)
        assert not assessed(run.study).condensed


class TestTheWireForm:
    def _run(self) -> Rob2Run:
        replies = [{"answers": CONCERNS[Domain.D1]}] + [reply(d) for d in list(Domain)[1:4]]
        replies += [{"answers": CONCERNS[Domain.D5]}, {"escalate": True, "reasoning": "R."}]
        bad = {"answers": {}}
        replies += [reply(Domain.D2), bad, bad, reply(Domain.D4), reply(Domain.D5)]
        return make_agent(*replies).assess("A trial", TEXT, [OUTCOME, SECOND], min_similarity=SIM)

    def test_a_run_round_trips(self) -> None:
        run = self._run()
        data = json.loads(json.dumps(run.to_dict()))
        assert Rob2Run.from_dict(data) == run
        assert data["study"]["status"] == "assessed"
        assert data["outcomes"][1]["domains"][1] == {
            "status": "failed",
            "domain": "D3",
            "reason": data["outcomes"][1]["domains"][1]["reason"],
            "retryable": False,
            "status_code": None,
        }

    @pytest.mark.parametrize(
        ("change", "word"),
        [
            (lambda d: d.update(algorithm_version="other"), "algorithm_version"),
            (lambda d: d.update(prompt_version=True), "prompt_version"),
            (lambda d: d.update(prompt_version=-1), "prompt_version"),
            (lambda d: d["study"].update(domain="D2"), "study"),
            (lambda d: d["study"].update(status="maybe"), "status"),
            (lambda d: d["study"].update(judgement="high"), "judgement"),
            (lambda d: d["study"].update(condensed="no"), "condensed"),
            (lambda d: d["study"]["answers"].pop("1.2"), "1.2"),
            (lambda d: d["study"]["answers"]["1.1"].update(answer="py"), "answer"),
            (lambda d: d["study"]["answers"]["1.1"].update(quotes_dropped=True), "quotes_dropped"),
            (lambda d: d["study"]["answers"]["1.1"].update(reasoning=None), "reasoning"),
            (lambda d: d["study"]["answers"]["1.1"].update(citations={}), "citations"),
            (lambda d: d["study"]["answers"].update({"1.9": {}}), "1.9"),
            (lambda d: d["study"].update(ignored=["1.1"]), "ignored"),
            (lambda d: d["outcomes"][0].update(outcome=3), "outcome"),
            (lambda d: d["outcomes"][0]["domains"].pop(), "domains"),
            (lambda d: d["outcomes"][0].update(overall="low"), "overall"),
            (lambda d: d["outcomes"][1].update(overall="low"), "overall"),
            (lambda d: d["outcomes"][0].update(escalation=None), "overall"),
            (lambda d: d["outcomes"][0]["escalation"].update(escalate="yes"), "escalate"),
            (
                lambda d: d["outcomes"][1].update(escalation={"escalate": True, "reasoning": ""}),
                "escalation",
            ),
            (lambda d: d["outcomes"][0].update(escalation_error="x"), "escalation_error"),
            (lambda d: d["outcomes"][1]["domains"][1].update(reason=None), "reason"),
            (lambda d: d["outcomes"][1]["domains"][1].pop("status_code"), "status_code"),
            (lambda d: d["outcomes"][0].pop("escalation_status_code"), "escalation_status_code"),
            (lambda d: d["outcomes"][0].pop("escalation_retryable"), "escalation_retryable"),
            (lambda d: d["outcomes"][0].update(escalation_retryable=True), "escalation_retryable"),
            (
                lambda d: d["outcomes"][0].update(escalation_status_code=99),
                "escalation_status_code",
            ),
        ],
    )
    def test_from_dict_refuses_what_the_reader_refuses(self, change: Any, word: str) -> None:
        data = json.loads(json.dumps(self._run().to_dict()))
        change(data)
        with pytest.raises(ValueError) as raised:
            Rob2Run.from_dict(data)
        assert word in str(raised.value)

    def test_an_outcome_alone_refuses_a_bad_shape(self) -> None:
        with pytest.raises(ValueError, match="domains"):
            OutcomeRob2.from_dict(
                {"outcome": "x", "domains": {}, "escalation": None, "overall": None}
            )


def test_the_d5_prompt_names_the_outcome_and_the_switching_check() -> None:
    prompt = domain_prompt(Domain.D5, "A trial", TEXT, OUTCOME)
    assert D5_OUTCOME_SWITCHING in prompt
    assert all(f"{q}: {question_text(q)}" in prompt for q in questions(Domain.D5))
    assert "Timepoint: 12 weeks" in prompt
    assert "Timepoint: not stated" in domain_prompt(Domain.D2, "A trial", TEXT, SECOND)
    assert "Reported result: none extracted" in domain_prompt(Domain.D2, "A trial", TEXT, SECOND)
    with pytest.raises(ValueError, match="D1"):
        domain_prompt(Domain.D1, "A trial", TEXT, OUTCOME)
    with pytest.raises(ValueError, match="D2"):
        domain_prompt(Domain.D2, "A trial", TEXT, None)
