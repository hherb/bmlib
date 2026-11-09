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

"""Tests for the design proposal: reading a reply, and the agent that asks."""

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
from bmlib.quality.data_models import RctLayout, StudyDesign
from bmlib.quality.design_agent import (
    DESIGN_CONDENSE_QUERY,
    DESIGN_PROMPT_VERSION,
    DESIGN_SYSTEM_PROMPT,
    DESIGN_TASK_PROMPT,
    DesignAgent,
    DesignProposal,
    ProposalError,
    ProposalErrorKind,
    read_design_reply,
)
from bmlib.quality.pico_models import PicoContradictedQuote

TEXT = "Participants were randomly assigned 1:1 to metformin or placebo. Follow-up was 12 weeks."
QUOTE = "Participants were randomly assigned 1:1 to metformin or placebo."
SIM = 0.9


def reply(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "study_design": "rct",
        "rct_layout": "parallel",
        "quotes": [QUOTE],
        "reasoning": "Individual randomisation to two arms.",
    }
    value.update(overrides)
    return value


class TestRctLayout:
    def test_wire_values_and_coverage(self) -> None:
        assert [layout.value for layout in RctLayout] == [
            "parallel",
            "cluster",
            "crossover",
            "other",
        ]
        assert [layout.is_covered() for layout in RctLayout] == [True, False, False, False]

    def test_study_design_is_unchanged(self) -> None:
        assert StudyDesign.RCT.value == "rct"
        assert len(StudyDesign) == 15


class TestReadingAReply:
    def test_a_parallel_rct_with_a_found_quote(self) -> None:
        proposal = read_design_reply(reply(), TEXT, SIM)

        assert proposal.design is StudyDesign.RCT
        assert proposal.rct_layout is RctLayout.PARALLEL
        assert [c.text for c in proposal.citations] == [QUOTE]
        assert (proposal.citations[0].start, proposal.citations[0].end) == (0, len(QUOTE))
        assert proposal.quotes_not_found == []
        assert proposal.condensed is False

    def test_an_rct_without_a_layout_is_refused(self) -> None:
        with pytest.raises(ValueError, match="rct_layout missing"):
            read_design_reply(reply(rct_layout=None), TEXT, SIM)
        data = reply()
        del data["rct_layout"]
        with pytest.raises(ValueError, match="rct_layout missing"):
            read_design_reply(data, TEXT, SIM)

    def test_a_layout_on_a_non_rct_is_refused(self) -> None:
        with pytest.raises(ValueError, match="rct_layout given for a non-RCT"):
            read_design_reply(reply(study_design="cohort_prospective"), TEXT, SIM)

    def test_a_non_rct_without_a_layout(self) -> None:
        proposal = read_design_reply(
            reply(study_design="cohort_prospective", rct_layout=None), TEXT, SIM
        )
        assert proposal.design is StudyDesign.COHORT_PROSPECTIVE
        assert proposal.rct_layout is None

    def test_a_mapped_alias_reads_as_rct(self) -> None:
        proposal = read_design_reply(
            reply(study_design="  Randomised Controlled Trial "), TEXT, SIM
        )
        assert proposal.design is StudyDesign.RCT

    @pytest.mark.parametrize("label", ["banana", "unknown", "", 3, None, ["rct"]])
    def test_an_unknown_or_unreadable_design_is_refused(self, label: object) -> None:
        with pytest.raises(ValueError, match="study_design"):
            read_design_reply(reply(study_design=label), TEXT, SIM)

    @pytest.mark.parametrize("layout", ["banana", 1, True, ["parallel"]])
    def test_an_unreadable_layout_is_refused(self, layout: object) -> None:
        with pytest.raises(ValueError, match="rct_layout"):
            read_design_reply(reply(rct_layout=layout), TEXT, SIM)

    def test_a_layout_is_folded_for_case_and_space(self) -> None:
        proposal = read_design_reply(reply(rct_layout=" Cluster "), TEXT, SIM)
        assert proposal.rct_layout is RctLayout.CLUSTER

    def test_a_quote_absent_from_the_text_is_listed(self) -> None:
        lost = "Patients were allocated by minimisation."
        proposal = read_design_reply(reply(quotes=[lost]), TEXT, SIM)

        assert proposal.citations == []
        assert proposal.quotes_not_found == [lost]

    def test_a_contradicted_quote_keeps_its_reason_and_is_not_not_found(self) -> None:
        changed = "Participants were randomly assigned 1:2 to metformin or placebo."
        proposal = read_design_reply(reply(quotes=[changed]), TEXT, SIM)

        assert proposal.citations == []
        assert proposal.quotes_not_found == []
        assert proposal.contradicted == [
            PicoContradictedQuote(changed, PassageContradiction.NUMBER)
        ]

    def test_unusable_quote_members_are_counted(self) -> None:
        proposal = read_design_reply(reply(quotes=[QUOTE, "  ", 7, None]), TEXT, SIM)

        assert len(proposal.citations) == 1
        assert proposal.quotes_dropped == 3

    def test_no_quote_at_all_is_kept(self) -> None:
        assert read_design_reply(reply(quotes=[]), TEXT, SIM).citations == []

    def test_reasoning_absent_is_empty_and_a_non_string_is_refused(self) -> None:
        data = reply()
        del data["reasoning"]
        assert read_design_reply(data, TEXT, SIM).reasoning == ""
        with pytest.raises(ValueError, match="reasoning"):
            read_design_reply(reply(reasoning=5), TEXT, SIM)

    def test_a_reply_that_is_not_an_object_is_refused(self) -> None:
        with pytest.raises(ValueError, match="object"):
            read_design_reply(["rct"], TEXT, SIM)  # type: ignore[arg-type]

    @pytest.mark.parametrize("bad", [0.0, 1.5, -1.0, math.nan, True, "0.9"])
    def test_a_bad_similarity_is_refused(self, bad: Any) -> None:
        with pytest.raises(ValueError, match="min_similarity"):
            read_design_reply(reply(), TEXT, bad)

    def test_the_proposal_round_trips(self) -> None:
        proposal = read_design_reply(
            reply(
                quotes=[
                    QUOTE,
                    "lost words here",
                    "Participants were randomly assigned 1:2 to metformin or placebo.",
                    " ",
                ]
            ),
            TEXT,
            SIM,
        )
        assert proposal.contradicted and proposal.quotes_dropped == 1
        assert DesignProposal.from_dict(proposal.to_dict()) == proposal

    def test_from_dict_refuses_the_wrong_types(self) -> None:
        good = read_design_reply(reply(), TEXT, SIM).to_dict()
        for key, bad in [
            ("design", "banana"),
            ("rct_layout", "banana"),
            ("condensed", "yes"),
            ("reasoning", 3),
            ("quotes_not_found", "x"),
            ("citations", {}),
            ("contradicted", "x"),
            ("quotes_dropped", -1),
            ("quotes_dropped", True),
        ]:
            with pytest.raises(ValueError, match=key):
                DesignProposal.from_dict({**good, key: bad})


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


def make_agent(*replies: Any, **kwargs: Any) -> DesignAgent:
    llm = MagicMock()
    responses = [_response(r if isinstance(r, str) else json.dumps(r)) for r in replies]
    llm.chat = MagicMock(side_effect=responses + [responses[-1]] * 20)
    return DesignAgent(llm=llm, model="test:model", **kwargs)


@pytest.fixture(autouse=True)
def _no_backoff() -> Any:
    with patch("bmlib.agents.base.time.sleep"):
        yield


class TestTheAgent:
    def test_a_reply_is_read(self) -> None:
        agent = make_agent(reply())

        proposal = agent.propose("A trial", TEXT, min_similarity=SIM)

        assert proposal.design is StudyDesign.RCT
        assert [c.text for c in proposal.citations] == [QUOTE]
        assert proposal.condensed is False
        sent = agent.llm.chat.call_args.kwargs["messages"]
        assert sent[0].content == DESIGN_SYSTEM_PROMPT
        assert sent[-1].content.startswith(DESIGN_TASK_PROMPT)
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
        agent = make_agent(reply(study_design="banana"))

        with pytest.raises(ProposalError) as raised:
            agent.propose("A trial", TEXT, min_similarity=SIM)

        assert raised.value.kind is ProposalErrorKind.UNREADABLE
        assert "study_design" in str(raised.value)
        assert agent.llm.chat.call_count == 2

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
        assert condenser.calls == [(DESIGN_CONDENSE_QUERY, text.strip())]
        sent = agent.llm.chat.call_args.kwargs["messages"][-1].content
        assert digest in sent
        assert "Filler sentence" not in sent
        # The model saw the digest, yet the quote is located in the paper.
        assert [c.text for c in proposal.citations] == [QUOTE]
        assert proposal.citations[0].start == 0

    def test_an_oversized_paper_with_a_refusing_condenser_fails_visibly(self) -> None:
        from bmlib.quality._condense import CondensationRefusedError

        class Refusing:
            def condense(self, *args: Any, **kwargs: Any) -> Any:
                raise CondensationRefusedError("the map-reduce failed: boom")

        agent = make_agent(
            reply(),
            condenser=Refusing(),
            condense_config=ProcessingConfig(max_context_chars=50),
        )

        with pytest.raises(ProposalError) as raised:
            agent.propose("A trial", TEXT, min_similarity=SIM)

        assert raised.value.kind is ProposalErrorKind.CONDENSATION_FAILED
        agent.llm.chat.assert_not_called()

    def test_the_constants(self) -> None:
        assert DESIGN_PROMPT_VERSION == 1
        assert "not" in DESIGN_SYSTEM_PROMPT and DESIGN_CONDENSE_QUERY
