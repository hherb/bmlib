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

"""Tests for the primary-outcome proposal: reading a reply, and the agent that asks."""

from __future__ import annotations

import json
import math
import threading
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from bmlib.citations import PassageContradiction
from bmlib.context_processor import ProcessingConfig, ProcessingStatus
from bmlib.llm.data_types import LLMResponse
from bmlib.quality.design_agent import ProposalError, ProposalErrorKind
from bmlib.quality.evidence_ladder import EffectScale, ReportedResult
from bmlib.quality.outcome_agent import (
    OUTCOME_CONDENSE_QUERY,
    OUTCOME_PROMPT_VERSION,
    OUTCOME_SYSTEM_PROMPT,
    OUTCOME_TASK_PROMPT,
    OutcomeAgent,
    OutcomeProposal,
    ProposedOutcome,
    read_outcome_reply,
)
from bmlib.quality.pico_models import PicoContradictedQuote

TEXT = (
    "The primary outcome was all-cause mortality at 28 days. "
    "The risk ratio was 0.70 (95% CI 0.55 to 0.88), n=1200."
)
QUOTE = "The primary outcome was all-cause mortality at 28 days."
SIM = 0.9


def result(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "measure": "risk ratio",
        "scale": "ratio",
        "estimate": 0.7,
        "ci_low": 0.55,
        "ci_high": 0.88,
        "ci_level": 0.95,
        "n_analysed": 1200,
    }
    value.update(overrides)
    return value


def outcome(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "name": "All-cause mortality",
        "timepoint": "28 days",
        "quotes": [QUOTE],
        "result": result(),
    }
    value.update(overrides)
    return value


def reply(*outcomes: dict[str, Any]) -> dict[str, Any]:
    return {"primary_outcomes": list(outcomes) if outcomes else [outcome()]}


class TestReadingAReply:
    def test_one_outcome_with_a_ratio_result_and_a_found_quote(self) -> None:
        proposal = read_outcome_reply(reply(), TEXT, SIM)

        assert proposal.condensed is False
        [found] = proposal.outcomes
        assert (found.name, found.timepoint) == ("All-cause mortality", "28 days")
        assert found.result == ReportedResult(
            "risk ratio", EffectScale.RATIO, 0.7, 0.55, 0.88, 0.95, 1200
        )
        assert found.result_error is None
        assert [c.text for c in found.citations] == [QUOTE]
        assert (found.citations[0].start, found.citations[0].end) == (0, len(QUOTE))

    def test_co_primary_outcomes_keep_their_order(self) -> None:
        second = outcome(name="Hospitalisation", timepoint=None, quotes=[], result=None)
        proposal = read_outcome_reply(reply(outcome(), second), TEXT, SIM)

        assert [o.name for o in proposal.outcomes] == ["All-cause mortality", "Hospitalisation"]
        assert proposal.outcomes[1].timepoint is None
        assert proposal.outcomes[1].result is None and proposal.outcomes[1].result_error is None

    def test_an_empty_list_is_a_valid_answer(self) -> None:
        assert read_outcome_reply({"primary_outcomes": []}, TEXT, SIM) == OutcomeProposal([], False)

    @pytest.mark.parametrize(
        ("overrides", "reason"),
        [
            ({"ci_low": 0.9, "ci_high": 0.5}, "ci_low <= estimate <= ci_high does not hold"),
            ({"ci_low": 0.0, "estimate": 0.4}, "ci_low must be above 0 on the ratio scale"),
            ({"ci_high": None}, "ci_low and ci_high must be given together"),
            ({"estimate": 0.2}, "ci_low <= estimate <= ci_high does not hold"),
            ({"ci_level": 95.0}, "ci_level must be above 0 and below 1"),
            ({"n_analysed": -3}, "n_analysed must be a whole number that fits 32 bits"),
            ({"n_analysed": 2**32}, "n_analysed must be a whole number that fits 32 bits"),
        ],
    )
    def test_an_incoherent_result_is_kept_out_with_its_reason(
        self, overrides: dict[str, Any], reason: str
    ) -> None:
        [found] = read_outcome_reply(reply(outcome(result=result(**overrides))), TEXT, SIM).outcomes

        assert found.result is None
        assert found.result_error == reason
        assert [c.text for c in found.citations] == [QUOTE]  # the outcome itself stands

    def test_optional_numbers_may_be_null_or_absent(self) -> None:
        bare = {"measure": "risk ratio", "scale": "ratio", "estimate": 0.7}
        [found] = read_outcome_reply(reply(outcome(result=bare)), TEXT, SIM).outcomes
        assert found.result == ReportedResult("risk ratio", EffectScale.RATIO, 0.7)

    def test_a_scale_is_folded_for_case_and_space_and_an_int_estimate_is_a_float(self) -> None:
        raw = result(scale=" Difference ", estimate=-2, ci_low=-3, ci_high=-1)
        [found] = read_outcome_reply(reply(outcome(result=raw)), TEXT, SIM).outcomes
        assert found.result is not None
        assert found.result.scale is EffectScale.DIFFERENCE
        assert isinstance(found.result.estimate, float)

    @pytest.mark.parametrize(
        ("raw", "message"),
        [
            (result(scale="percent"), 'primary_outcomes[0].result.scale "percent" is not a scale'),
            (result(scale=1), "primary_outcomes[0].result.scale is not text"),
            (result(measure=None), "primary_outcomes[0].result.measure is not text"),
            (result(estimate=math.inf), "result.estimate is not a finite number"),
            (result(estimate=math.nan), "result.estimate is not a finite number"),
            (result(estimate=None), "result.estimate is not a finite number"),
            (result(estimate=True), "result.estimate is not a finite number"),
            (result(estimate="0.7"), "result.estimate is not a finite number"),
            (result(estimate=10**400), "result.estimate is not a finite number"),
            (result(ci_low=math.inf), "result.ci_low is not a finite number"),
            (result(ci_level="high"), "result.ci_level is not a finite number"),
            (result(n_analysed=1200.0), "result.n_analysed is not a whole number"),
            (result(n_analysed=True), "result.n_analysed is not a whole number"),
            ("risk ratio 0.7", "primary_outcomes[0].result is not an object"),
        ],
    )
    def test_an_unreadable_result_refuses_the_reply(self, raw: Any, message: str) -> None:
        with pytest.raises(ValueError) as raised:
            read_outcome_reply(reply(outcome(result=raw)), TEXT, SIM)
        assert message in str(raised.value)

    @pytest.mark.parametrize(
        ("bad", "message"),
        [
            ({"primary_outcomes": "none"}, "primary_outcomes is not a list"),
            ({}, "primary_outcomes is not a list"),
            (reply("x"), "primary_outcomes[0] is not an object"),  # type: ignore[arg-type]
            (reply(outcome(name=None)), "primary_outcomes[0].name is not text"),
            (reply(outcome(name="  ")), "primary_outcomes[0].name is empty"),
            (reply(outcome(timepoint=3)), "primary_outcomes[0].timepoint is not text"),
            (reply({"name": "x", "quotes": []}), "primary_outcomes[0].result is missing"),
        ],
    )
    def test_a_malformed_reply_names_the_field(self, bad: Any, message: str) -> None:
        with pytest.raises(ValueError) as raised:
            read_outcome_reply(bad, TEXT, SIM)
        assert str(raised.value) == message

    def test_a_reply_that_is_not_an_object_is_refused(self) -> None:
        with pytest.raises(ValueError, match="object"):
            read_outcome_reply([], TEXT, SIM)  # type: ignore[arg-type]

    def test_lost_contradicted_and_dropped_quotes_each_have_their_place(self) -> None:
        lost = "Patients were followed for a year."
        changed = "The primary outcome was all-cause mortality at 90 days."
        quotes = [lost, changed, QUOTE, "  ", 7, None]
        [found] = read_outcome_reply(reply(outcome(quotes=quotes)), TEXT, SIM).outcomes

        assert len(found.citations) == 1
        assert found.quotes_not_found == [lost]
        assert found.contradicted == [PicoContradictedQuote(changed, PassageContradiction.NUMBER)]
        assert found.quotes_dropped == 3

    def test_a_blank_timepoint_reads_as_none(self) -> None:
        [found] = read_outcome_reply(reply(outcome(timepoint="  ")), TEXT, SIM).outcomes
        assert found.timepoint is None

    @pytest.mark.parametrize("bad", [0.0, 1.5, -1.0, math.nan, True, "0.9"])
    def test_a_bad_similarity_is_refused(self, bad: Any) -> None:
        with pytest.raises(ValueError, match="min_similarity"):
            read_outcome_reply(reply(), TEXT, bad)


class TestTheWireForm:
    def _proposal(self) -> OutcomeProposal:
        quotes = [
            QUOTE,
            "lost words here",
            "The primary outcome was all-cause mortality at 90 days.",
        ]
        return read_outcome_reply(
            reply(
                outcome(quotes=quotes),
                outcome(name="Second", result=result(ci_low=0.9, ci_high=0.5)),
                outcome(name="Third", result=None, quotes=[" "]),
            ),
            TEXT,
            SIM,
        )

    def test_the_proposal_round_trips(self) -> None:
        proposal = self._proposal()
        assert proposal.outcomes[0].contradicted and proposal.outcomes[2].quotes_dropped == 1
        assert proposal.outcomes[1].result_error
        assert OutcomeProposal.from_dict(proposal.to_dict()) == proposal

    def test_from_dict_refuses_what_the_reader_refuses(self) -> None:
        good = self._proposal().outcomes[0].to_dict()
        for key, bad in [
            ("name", 3),
            ("timepoint", 3),
            ("result", "x"),
            ("result", {**good["result"], "scale": "percent"}),
            ("result", {**good["result"], "estimate": True}),
            ("result", {**good["result"], "ci_low": 0.9, "ci_high": 0.5}),
            ("result_error", 3),
            ("result_error", "also set"),
            ("quotes_not_found", "x"),
            ("citations", {}),
            ("contradicted", "x"),
            ("quotes_dropped", -1),
            ("quotes_dropped", True),
        ]:
            with pytest.raises(ValueError, match=key):
                ProposedOutcome.from_dict({**good, key: bad})
        absent = {k: v for k, v in good.items() if k != "result"}
        with pytest.raises(ValueError, match="result"):
            ProposedOutcome.from_dict(absent)
        with pytest.raises(ValueError, match="condensed"):
            OutcomeProposal.from_dict({"outcomes": [], "condensed": "yes"})
        with pytest.raises(ValueError, match="outcomes"):
            OutcomeProposal.from_dict({"outcomes": {}, "condensed": False})


# --- The agent ---


def _response(content: str) -> LLMResponse:
    return LLMResponse(
        content=content, model="test:model", input_tokens=1, output_tokens=1, stop_reason="stop"
    )


class ScriptedCondenser:
    """A condenser that records its query and returns a fixed digest."""

    def __init__(self, digest: str) -> None:
        self.digest = digest
        self.calls: list[tuple[str, str]] = []

    def condense(
        self, query: str, text: str, label: str, config: ProcessingConfig, *, stage: str
    ) -> tuple[str, list[str], ProcessingStatus]:
        self.calls.append((query, text))
        return self.digest, [], ProcessingStatus.COMPLETED


def make_agent(*replies: Any, **kwargs: Any) -> OutcomeAgent:
    llm = MagicMock()
    responses = [_response(r if isinstance(r, str) else json.dumps(r)) for r in replies]
    llm.chat = MagicMock(side_effect=responses + [responses[-1]] * 20)
    return OutcomeAgent(llm=llm, model="test:model", **kwargs)


@pytest.fixture(autouse=True)
def _no_backoff() -> Any:
    with patch("bmlib.agents.base.time.sleep"):
        yield


class TestTheAgent:
    def test_a_reply_is_read(self) -> None:
        agent = make_agent(reply())

        proposal = agent.propose("A trial", TEXT, min_similarity=SIM)

        assert [o.name for o in proposal.outcomes] == ["All-cause mortality"]
        assert proposal.condensed is False
        sent = agent.llm.chat.call_args.kwargs["messages"]
        assert sent[0].content == OUTCOME_SYSTEM_PROMPT
        assert sent[-1].content.startswith(OUTCOME_TASK_PROMPT)
        assert TEXT in sent[-1].content

    def test_an_empty_text_is_refused_without_a_request(self) -> None:
        agent = make_agent(reply())
        with pytest.raises(ProposalError) as raised:
            agent.propose("A trial", "   ", min_similarity=SIM)
        assert raised.value.kind is ProposalErrorKind.EMPTY_INPUT
        agent.llm.chat.assert_not_called()

    def test_a_bad_similarity_is_refused_without_a_request(self) -> None:
        agent = make_agent(reply())
        with pytest.raises(ProposalError) as raised:
            agent.propose("A trial", TEXT, min_similarity=0.0)
        assert raised.value.kind is ProposalErrorKind.INVALID_SIMILARITY
        agent.llm.chat.assert_not_called()

    def test_a_stop_set_before_the_call_sends_nothing(self) -> None:
        stop = threading.Event()
        stop.set()
        agent = make_agent(reply(), stop=stop)
        with pytest.raises(ProposalError) as raised:
            agent.propose("A trial", TEXT, min_similarity=SIM)
        assert raised.value.kind is ProposalErrorKind.STOPPED
        agent.llm.chat.assert_not_called()

    def test_an_unreadable_reply_is_asked_twice_then_refused(self) -> None:
        agent = make_agent(reply(outcome(result=result(scale="percent"))))
        with pytest.raises(ProposalError) as raised:
            agent.propose("A trial", TEXT, min_similarity=SIM)
        assert raised.value.kind is ProposalErrorKind.UNREADABLE
        assert "scale" in str(raised.value)
        assert agent.llm.chat.call_count == 2

    def test_a_reply_holding_a_non_finite_number_is_unreadable(self) -> None:
        measure = '{"measure": "m", "scale": "ratio", "estimate": 1e999}'
        text = '{"primary_outcomes": [{"name": "x", "result": ' + measure + "}]}"
        with pytest.raises(ProposalError) as raised:
            make_agent(text).propose("A trial", TEXT, min_similarity=SIM)
        assert raised.value.kind is ProposalErrorKind.UNREADABLE

    def test_a_non_json_reply_is_unreadable(self) -> None:
        with pytest.raises(ProposalError) as raised:
            make_agent("not json at all").propose("A trial", TEXT, min_similarity=SIM)
        assert raised.value.kind is ProposalErrorKind.UNREADABLE

    def test_a_failed_request_is_reported(self) -> None:
        agent = make_agent(reply())
        agent.llm.chat = MagicMock(side_effect=ConnectionError("down"))
        with pytest.raises(ProposalError) as raised:
            agent.propose("A trial", TEXT, min_similarity=SIM)
        assert raised.value.kind is ProposalErrorKind.REQUEST_FAILED

    def test_a_paper_over_the_window_is_condensed_and_verified_against_the_original(self) -> None:
        text = TEXT + " " + "Filler sentence about nothing. " * 20
        digest = "A digest that does not contain the quote."
        condenser = ScriptedCondenser(digest)
        agent = make_agent(
            reply(),
            condenser=condenser,
            condense_config=ProcessingConfig(max_context_chars=200),
        )

        proposal = agent.propose("A trial", text, min_similarity=SIM)

        assert proposal.condensed is True
        assert condenser.calls == [(OUTCOME_CONDENSE_QUERY, text.strip())]
        sent = agent.llm.chat.call_args.kwargs["messages"][-1].content
        assert digest in sent and "Filler sentence" not in sent
        assert [c.text for c in proposal.outcomes[0].citations] == [QUOTE]
        assert proposal.outcomes[0].citations[0].start == 0

    def test_an_oversized_paper_with_a_refusing_condenser_fails_visibly(self) -> None:
        from bmlib.quality._condense import CondensationRefusedError

        class Refusing:
            def condense(self, *args: Any, **kwargs: Any) -> Any:
                raise CondensationRefusedError("the map-reduce failed: boom")

        agent = make_agent(
            reply(), condenser=Refusing(), condense_config=ProcessingConfig(max_context_chars=50)
        )
        with pytest.raises(ProposalError) as raised:
            agent.propose("A trial", TEXT, min_similarity=SIM)
        assert raised.value.kind is ProposalErrorKind.CONDENSATION_FAILED
        agent.llm.chat.assert_not_called()

    def test_the_constants(self) -> None:
        assert OUTCOME_PROMPT_VERSION == 1
        assert "primary" in OUTCOME_SYSTEM_PROMPT and OUTCOME_CONDENSE_QUERY
