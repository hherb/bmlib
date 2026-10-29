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

"""Tests for the PICO agent: reading a reply, verifying its quotes, and the agent."""

from __future__ import annotations

import json
import pickle
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from bmlib.agents import ChatJsonUnparseableError
from bmlib.context_processor import ProcessingConfig, ProcessingStatus
from bmlib.llm.data_types import LLMResponse
from bmlib.quality.pico_agent import (
    PICO_PARTS,
    PicoAgent,
    read_extraction,
    read_part,
    read_suitability,
    verify_quotes,
)
from bmlib.quality.pico_models import (
    PicoError,
    PicoErrorKind,
    PicoExtraction,
    PicoPartState,
    PicoSuitability,
)

TEXT = "Adults aged 40-65 were randomised to metformin or placebo."
SIM = 0.9


def part(**overrides: Any) -> dict[str, Any]:
    """A reported part whose one quote is in ``TEXT``."""
    value: dict[str, Any] = {
        "description": "Adults aged 40 to 65",
        "reported": True,
        "confidence": 0.9,
        "quotes": ["Adults aged 40-65"],
    }
    value.update(overrides)
    return value


def reply(**parts: Any) -> dict[str, Any]:
    data: dict[str, Any] = {name: part() for name in PICO_PARTS}
    data.update(parts)
    data.setdefault("study_type", "randomised controlled trial")
    data.setdefault("sample_size", "N=120")
    return data


class TestReadingAReply:
    def test_a_found_quote_is_the_papers_span(self) -> None:
        result = read_part(part(quotes=["adults aged 40–65"]), TEXT, SIM)

        assert result.state is PicoPartState.VERIFIED
        assert result.citations[0].text == "Adults aged 40-65"
        assert result.citations[0].start == 0
        assert result.citations[0].end == len("Adults aged 40-65")

    def test_some_found_some_lost(self) -> None:
        lost = "middle-aged people took a diabetes drug"
        result = read_part(part(quotes=["Adults aged 40-65", lost]), TEXT, SIM)

        assert result.state is PicoPartState.VERIFIED
        assert len(result.citations) == 1
        assert result.quotes_not_found == [lost]

    def test_none_found_is_unverified_and_keeps_the_description(self) -> None:
        result = read_part(part(quotes=["middle-aged people took a drug"]), TEXT, SIM)

        assert result.state is PicoPartState.UNVERIFIED
        assert result.description == "Adults aged 40 to 65"
        assert result.citations == []

    def test_no_quotes_given_is_unverified(self) -> None:
        assert read_part(part(quotes=[]), TEXT, SIM).state is PicoPartState.UNVERIFIED

    def test_not_reported_ignores_its_quotes(self) -> None:
        result = read_part(part(reported=False), TEXT, SIM)

        assert result.state is PicoPartState.NOT_REPORTED
        assert result.citations == []
        assert result.quotes_not_found == []

    def test_quotes_as_a_bare_string(self) -> None:
        result = read_part(part(quotes="Adults aged 40-65"), TEXT, SIM)

        assert result.state is PicoPartState.UNVERIFIED
        assert result.quotes_dropped == 1

    def test_a_non_string_quote_is_dropped_and_counted(self) -> None:
        result = read_part(part(quotes=[3, "  ", "metformin"]), TEXT, SIM)

        assert result.quotes_dropped == 2
        assert [c.text for c in result.citations] == ["metformin"]

    def test_a_repeated_span_is_cited_once(self) -> None:
        result = read_part(part(quotes=["metformin", "Metformin"]), TEXT, SIM)

        assert len(result.citations) == 1

    def test_a_non_bool_reported_counts_as_reported(self) -> None:
        result = read_part(part(reported="no"), TEXT, SIM)

        assert result.state is PicoPartState.VERIFIED

    def test_a_part_that_is_not_an_object_raises(self) -> None:
        with pytest.raises(ValueError):
            read_part("Adults", TEXT, SIM)

    def test_a_part_without_a_text_description_raises(self) -> None:
        with pytest.raises(ValueError):
            read_part(part(description=None), TEXT, SIM)

    def test_a_missing_part_raises(self) -> None:
        data = reply()
        del data["comparison"]
        with pytest.raises(ValueError, match="comparison"):
            read_extraction(data, TEXT, SIM)

    def test_overall_confidence_skips_unreported_parts(self) -> None:
        data = reply(
            population=part(confidence=0.9),
            intervention=part(confidence=0.7),
            comparison=part(reported=False, confidence=0.1),
            outcome=part(confidence=None),
        )

        assert read_extraction(data, TEXT, SIM).overall_confidence == (0.9 + 0.7) / 2

    def test_overall_confidence_is_unknown_with_no_number(self) -> None:
        data = reply(**{name: part(confidence=None) for name in PICO_PARTS})

        assert read_extraction(data, TEXT, SIM).overall_confidence is None

    def test_confidence_is_clamped(self) -> None:
        assert read_part(part(confidence=1.4), TEXT, SIM).confidence == 1.0
        assert read_part(part(confidence=True), TEXT, SIM).confidence is None

    def test_study_type_and_sample_size_are_read_as_text(self) -> None:
        extraction = read_extraction(reply(study_type=3, sample_size="N=120"), TEXT, SIM)

        assert extraction.study_type is None
        assert extraction.sample_size == "N=120"

    def test_suitability_requires_a_bool_is_suitable(self) -> None:
        with pytest.raises(ValueError, match="is_suitable"):
            read_suitability({"is_suitable": "yes"})

    def test_suitability_reads_wrong_types_as_unstated(self) -> None:
        suitability = read_suitability(
            {
                "is_suitable": False,
                "is_intervention_study": "no",
                "has_comparison": None,
                "confidence": "high",
                "rationale": 7,
                "study_type": "systematic review",
            }
        )

        assert suitability == PicoSuitability(
            is_suitable=False,
            is_intervention_study=None,
            has_comparison=None,
            study_type="systematic review",
            confidence=None,
            rationale=None,
        )

    def test_models_round_trip(self) -> None:
        extraction = read_extraction(
            reply(comparison=part(reported=False), outcome=part(quotes=["lost words"])),
            TEXT,
            SIM,
        )
        extraction.condensed_from_chars = 90_000
        extraction.condensation_status = "partial"
        extraction.notes = ["a note"]
        suitability = read_suitability({"is_suitable": True, "confidence": 0.8})

        assert PicoExtraction.from_dict(extraction.to_dict()) == extraction
        assert PicoSuitability.from_dict(suitability.to_dict()) == suitability


class TestVerifyQuotes:
    def test_it_returns_citations_lost_quotes_and_the_dropped_count(self) -> None:
        citations, lost, dropped = verify_quotes(
            ["placebo", "unrelated words here", None], TEXT, SIM
        )

        assert [c.text for c in citations] == ["placebo"]
        assert lost == ["unrelated words here"]
        assert dropped == 1

    def test_absent_quotes_are_none_and_drop_nothing(self) -> None:
        assert verify_quotes(None, TEXT, SIM) == ([], [], 0)


# --- The agent ---


def _response(content: str, stop_reason: str | None = "stop") -> LLMResponse:
    return LLMResponse(
        content=content,
        model="test:model",
        input_tokens=1,
        output_tokens=1,
        stop_reason=stop_reason,
    )


def make_agent(*replies: Any, **kwargs: Any) -> PicoAgent:
    """An agent whose client answers *replies* in order, repeating the last.

    A reply is a dict (sent as JSON), a string (sent verbatim), or an
    exception (raised by the client).
    """
    llm = MagicMock()
    responses: list[Any] = []
    for item in replies or (reply(),):
        if isinstance(item, BaseException):
            responses.append(item)
        elif isinstance(item, str):
            responses.append(_response(item))
        else:
            responses.append(_response(json.dumps(item)))
    llm.chat = MagicMock(side_effect=responses + [responses[-1]] * 20)
    return PicoAgent(llm=llm, model="test:model", **kwargs)


def sent_user_message(agent: PicoAgent, call: int = 0) -> str:
    messages = agent.llm.chat.call_args_list[call].kwargs["messages"]
    return messages[-1].content


@pytest.fixture(autouse=True)
def _no_backoff() -> Any:
    with patch("bmlib.agents.base.time.sleep"):
        yield


class TestTheExtraction:
    def test_a_whole_extraction(self) -> None:
        extraction = make_agent(reply()).extract("A trial", TEXT, min_similarity=SIM)

        assert extraction.population.state is PicoPartState.VERIFIED
        assert extraction.population.citations[0].text == "Adults aged 40-65"
        assert extraction.study_type == "randomised controlled trial"
        assert extraction.sample_size == "N=120"
        assert extraction.overall_confidence == pytest.approx(0.9)
        assert extraction.condensed_from_chars is None

    def test_low_confidence_is_kept(self) -> None:
        low = reply(**{name: part(confidence=0.1) for name in PICO_PARTS})

        extraction = make_agent(low).extract("A trial", TEXT, min_similarity=SIM)

        assert extraction.overall_confidence == pytest.approx(0.1)

    def test_empty_input_makes_no_call(self) -> None:
        agent = make_agent()
        with pytest.raises(PicoError) as caught:
            agent.extract("  ", None, min_similarity=SIM)

        assert caught.value.kind is PicoErrorKind.EMPTY_INPUT
        assert agent.llm.chat.call_count == 0

    def test_a_missing_part_is_retried_once(self) -> None:
        broken = reply()
        del broken["outcome"]
        agent = make_agent(broken, reply())

        extraction = agent.extract("A trial", TEXT, min_similarity=SIM)

        assert agent.llm.chat.call_count == 2
        assert extraction.outcome.state is PicoPartState.VERIFIED

    def test_a_part_missing_twice_is_unusable(self) -> None:
        broken = reply()
        del broken["outcome"]
        agent = make_agent(broken, broken)

        with pytest.raises(PicoError, match="outcome") as caught:
            agent.extract("A trial", TEXT, min_similarity=SIM)

        assert caught.value.kind is PicoErrorKind.UNUSABLE_REPLY
        assert agent.llm.chat.call_count == 2

    def test_unparseable_is_unusable(self) -> None:
        with pytest.raises(PicoError) as caught:
            make_agent("this is not JSON at all").extract("A trial", TEXT, min_similarity=SIM)

        assert caught.value.kind is PicoErrorKind.UNUSABLE_REPLY
        assert isinstance(caught.value.__cause__, ChatJsonUnparseableError)

    def test_a_provider_exception_is_request_failed(self) -> None:
        refused = ConnectionError("connection refused")

        with pytest.raises(PicoError) as caught:
            make_agent(refused).extract("A trial", TEXT, min_similarity=SIM)

        assert caught.value.kind is PicoErrorKind.REQUEST_FAILED
        assert caught.value.__cause__ is refused

    def test_truncation_is_truncated(self) -> None:
        agent = make_agent(temperature=0.0)
        agent.llm.chat = MagicMock(return_value=_response('{"population": ', "max_tokens"))

        with pytest.raises(PicoError) as caught:
            agent.extract("A trial", TEXT, min_similarity=SIM)

        assert caught.value.kind is PicoErrorKind.TRUNCATED

    def test_long_text_is_condensed_and_verified_against_the_original(self) -> None:
        text = "Filler sentence. " * 20 + TEXT
        agent = make_agent(reply(), condense_config=ProcessingConfig(max_context_chars=100))
        digest = "Middle-aged adults were given a drug."

        with patch(
            "bmlib.quality.pico_agent.condense_text",
            return_value=(digest, [], ProcessingStatus.COMPLETED),
        ) as condense:
            extraction = agent.extract("A trial", text, min_similarity=SIM)

        assert condense.call_args.kwargs["stage"] == "extraction"
        assert digest in sent_user_message(agent)
        assert TEXT not in sent_user_message(agent)
        assert extraction.population.state is PicoPartState.VERIFIED
        assert extraction.population.citations[0].start == text.index("Adults")
        assert extraction.condensed_from_chars == len(text)
        assert extraction.condensation_status == "completed"

    def test_condensation_failure_is_reported(self) -> None:
        agent = make_agent(condense_config=ProcessingConfig(max_context_chars=100))

        with (
            patch("bmlib.quality.pico_agent.condense_text", return_value=None),
            pytest.raises(PicoError) as caught,
        ):
            agent.extract("A trial", "x" * 500, min_similarity=SIM)

        assert caught.value.kind is PicoErrorKind.CONDENSATION_FAILED
        assert agent.llm.chat.call_count == 0

    def test_the_text_is_sent_whole(self) -> None:
        text = "word " * 8_000
        agent = make_agent()

        agent.extract("A trial", text, min_similarity=SIM)

        assert text.strip() in sent_user_message(agent)

    def test_blank_text_with_a_title_still_calls_the_model(self) -> None:
        agent = make_agent()

        extraction = agent.extract("A trial of metformin", "   ", min_similarity=SIM)

        assert agent.llm.chat.call_count == 1
        assert extraction.population.state is PicoPartState.UNVERIFIED

    @pytest.mark.parametrize("bad", [0, 1.5, -0.1, True, float("nan")], ids=str)
    def test_an_invalid_min_similarity_makes_no_call(self, bad: Any) -> None:
        agent = make_agent()

        with pytest.raises(ValueError, match="min_similarity"):
            agent.extract("A trial", TEXT, min_similarity=bad)

        assert agent.llm.chat.call_count == 0

    def test_braces_in_the_paper_reach_the_model_unchanged(self) -> None:
        text = "Group {A} and {content} were compared."
        agent = make_agent()

        agent.extract("A {title}", text, min_similarity=SIM)

        assert text in sent_user_message(agent)
        assert "A {title}" in sent_user_message(agent)


def suitability_reply(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "is_intervention_study": True,
        "has_comparison": True,
        "is_suitable": True,
        "confidence": 0.9,
        "rationale": "An RCT of metformin against placebo.",
        "study_type": "randomized controlled trial",
    }
    value.update(overrides)
    return value


class TestTheSuitabilityCheck:
    def test_suitability_rct(self) -> None:
        suitability = make_agent(suitability_reply()).check_suitability("A trial", TEXT)

        assert suitability.is_suitable is True
        assert suitability.has_comparison is True
        assert suitability.study_type == "randomized controlled trial"

    def test_suitability_systematic_review(self) -> None:
        answer = suitability_reply(
            is_intervention_study=False,
            has_comparison=False,
            is_suitable=False,
            study_type="systematic review",
        )

        suitability = make_agent(answer).check_suitability("A review", TEXT)

        assert suitability.is_suitable is False
        assert suitability.study_type == "systematic review"

    def test_suitability_case_report(self) -> None:
        answer = suitability_reply(
            is_suitable=False, has_comparison=False, study_type="case report"
        )

        assert make_agent(answer).check_suitability("A case", TEXT).is_suitable is False

    def test_suitability_empty_input(self) -> None:
        agent = make_agent(suitability_reply())
        with pytest.raises(PicoError) as caught:
            agent.check_suitability("", "")

        assert caught.value.kind is PicoErrorKind.EMPTY_INPUT
        assert agent.llm.chat.call_count == 0

    def test_suitability_request_failed(self) -> None:
        with pytest.raises(PicoError) as caught:
            make_agent(ConnectionError("down")).check_suitability("A trial", TEXT)

        assert caught.value.kind is PicoErrorKind.REQUEST_FAILED

    def test_suitability_unparseable(self) -> None:
        with pytest.raises(PicoError) as caught:
            make_agent("nope").check_suitability("A trial", TEXT)

        assert caught.value.kind is PicoErrorKind.UNUSABLE_REPLY

    def test_suitability_without_a_bool_answer_is_retried_then_unusable(self) -> None:
        agent = make_agent(suitability_reply(is_suitable="yes"))
        with pytest.raises(PicoError, match="is_suitable") as caught:
            agent.check_suitability("A trial", TEXT)

        assert caught.value.kind is PicoErrorKind.UNUSABLE_REPLY
        assert agent.llm.chat.call_count == 2

    def test_suitability_low_confidence_kept(self) -> None:
        answer = suitability_reply(confidence=0.2)

        assert make_agent(answer).check_suitability("A trial", TEXT).confidence == 0.2

    def test_suitability_text_not_cut(self) -> None:
        abstract = "Background. " * 400 + "THE TAIL SENTENCE."
        agent = make_agent(suitability_reply())

        agent.check_suitability("A trial", abstract)

        assert "THE TAIL SENTENCE." in sent_user_message(agent)

    def test_a_pico_error_survives_pickling(self) -> None:
        error = PicoError(PicoErrorKind.TRUNCATED, "cut off")

        copy = pickle.loads(pickle.dumps(error))

        assert copy.kind is PicoErrorKind.TRUNCATED
        assert str(copy) == "cut off"


def test_the_mean_is_a_plain_left_to_right_sum() -> None:
    """``sum()`` compensates its rounding on 3.12+ and not on 3.11; the mean
    must not depend on the interpreter (nor differ from the Rust port)."""
    data = reply(
        population=part(confidence=0.9),
        intervention=part(confidence=0.9),
        comparison=part(confidence=0.9),
        outcome=part(confidence=0.7),
    )

    # 0.8500000000000001, where a compensated sum gives 0.85.
    assert read_extraction(data, TEXT, SIM).overall_confidence == (((0.9 + 0.9) + 0.9) + 0.7) / 4
