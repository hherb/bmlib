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
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from bmlib.agents import ChatJsonUnparseableError, ChatJsonWrongShapeError
from bmlib.context_processor import ProcessingConfig, ProcessingStatus
from bmlib.llm import LLMClient
from bmlib.llm.data_types import LLMResponse
from bmlib.llm.providers.ollama import MAX_NUM_CTX
from bmlib.quality._condense import CondensationRefusedError
from bmlib.quality.pico_agent import (
    PICO_CONDENSE_CONSOLIDATION_PROMPT,
    PICO_CONDENSE_EXTRACTION_PROMPT,
    PICO_CONDENSE_QUERY,
    PICO_PARTS,
    PicoAgent,
    _UnreadableReplyError,
    read_extraction,
    read_part,
    read_suitability,
    verify_quotes,
)
from bmlib.quality.pico_models import (
    PicoError,
    PicoErrorKind,
    PicoExtraction,
    PicoPart,
    PicoPartState,
    PicoPrepared,
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

    @pytest.mark.parametrize("blank", ["", "   ", "\n\t", "\x1c"])
    def test_a_blank_description_raises_as_null_does(self, blank: str) -> None:
        """#578: a blank description shows nothing, so it is asked again.

        ``"\\x1c"`` is blank to ``str.strip`` and not to Rust's ``trim``, which
        the port's ``python_strip`` exists to bridge.
        """
        with pytest.raises(_UnreadableReplyError, match="blank"):
            read_part(part(description=blank), TEXT, SIM)
        with pytest.raises(_UnreadableReplyError, match="blank"):
            read_part(part(description=blank, reported=False), TEXT, SIM)

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

    def test_a_confidence_past_the_float_range_is_unstated(self) -> None:
        """``json`` reads ``1e400`` written as an integer as an ``int``; it must
        not escape as ``OverflowError`` past the "result or PicoError" contract."""
        assert read_part(part(confidence=10**400), TEXT, SIM).confidence is None
        assert read_suitability({"is_suitable": True, "confidence": 10**400}).confidence is None

    def test_confidence_is_clamped(self) -> None:
        assert read_part(part(confidence=1.4), TEXT, SIM).confidence == 1.0
        assert read_part(part(confidence=True), TEXT, SIM).confidence is None

    def test_study_type_and_sample_size_are_read_as_text(self) -> None:
        extraction = read_extraction(reply(study_type=3, sample_size="N=120"), TEXT, SIM)

        assert extraction.study_type is None
        assert extraction.sample_size == "N=120"

    @pytest.mark.parametrize(
        ("value", "expected"),
        [(150, "150"), (0, "0"), (150.0, None), (True, None), ("N=150", "N=150"), (None, None)],
        ids=["int", "zero", "float", "bool", "text", "null"],
    )
    def test_an_integer_sample_size_is_kept_as_its_digits(
        self, value: object, expected: str | None
    ) -> None:
        """#578: ``"sample_size": 150`` is the natural answer; a float is not a count."""
        assert read_extraction(reply(sample_size=value), TEXT, SIM).sample_size == expected

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

    @pytest.mark.parametrize(
        "raised",
        [
            TypeError("bmlib defect"),
            AttributeError("bmlib defect"),
            KeyError("bmlib defect"),
            NameError("bmlib defect"),
            IndexError("bmlib defect"),
            ImportError("the provider's SDK will not import"),
        ],
        ids=lambda e: type(e).__name__,
    )
    def test_what_no_retry_cures_propagates(self, raised: Exception) -> None:
        """#573: only a provider failure is ``REQUEST_FAILED``, which an app retries."""
        with pytest.raises(type(raised)) as caught:
            make_agent(raised).extract("A trial", TEXT, min_similarity=SIM)

        assert caught.value is raised

    def test_a_value_error_from_the_request_is_request_failed(self) -> None:
        """An SDK reading a 200 body that is not JSON raises one: the remote's."""
        raised = json.JSONDecodeError("Expecting value", "<html>", 0)

        with pytest.raises(PicoError) as caught:
            make_agent(raised).extract("A trial", TEXT, min_similarity=SIM)

        assert caught.value.kind is PicoErrorKind.REQUEST_FAILED
        assert caught.value.__cause__ is raised

    @pytest.mark.parametrize(
        "raised",
        [ValueError("Unknown provider 'nosuch'"), ImportError("needs the 'ollama' package")],
        ids=lambda e: type(e).__name__,
    )
    @pytest.mark.parametrize("length", ["short", "condensed"])
    def test_an_unknown_provider_is_refused_before_any_request(
        self, raised: Exception, length: str
    ) -> None:
        """Whatever the text's length: a long one would fail its condensation."""
        agent = make_agent(condense_config=ProcessingConfig(max_context_chars=100))
        agent.llm._get_provider = MagicMock(side_effect=raised)
        text = TEXT if length == "short" else "Filler sentence. " * 20 + TEXT

        for call in (
            lambda: agent.check_suitability("A trial", text),
            lambda: agent.extract("A trial", text, min_similarity=SIM),
        ):
            with pytest.raises(type(raised)) as caught:
                call()
            assert caught.value is raised
        assert agent.llm.chat.call_count == 0
        agent.llm._get_provider.assert_called_with("test")

    def test_a_real_client_refuses_an_unknown_provider(self) -> None:
        agent = PicoAgent(llm=LLMClient(), model="nosuch:model")

        with pytest.raises(ValueError, match="Unknown provider 'nosuch'"):
            agent.extract("A trial", "Filler sentence. " * 5000, min_similarity=SIM)

    def test_a_blank_description_is_asked_again(self) -> None:
        agent = make_agent(reply(comparison=part(description="  ")), reply())

        extraction = agent.extract("A trial", TEXT, min_similarity=SIM)

        assert extraction.comparison.state is PicoPartState.VERIFIED
        assert agent.llm.chat.call_count == 2

    def test_a_defect_while_reading_propagates_and_is_not_re_asked(self) -> None:
        """#573: a ``ValueError`` from bmlib's own reading is not an unusable reply."""
        agent = make_agent(reply(), reply())
        broken = ValueError("a broken invariant")
        with (
            patch("bmlib.quality.pico_agent.find_passage", side_effect=broken),
            pytest.raises(ValueError) as caught,
        ):
            agent.extract("A trial", TEXT, min_similarity=SIM)

        assert caught.value is broken
        assert agent.llm.chat.call_count == 1

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
        assert condense.call_args.kwargs["extraction_prompt"] == PICO_CONDENSE_EXTRACTION_PROMPT
        assert (
            condense.call_args.kwargs["consolidation_prompt"] == PICO_CONDENSE_CONSOLIDATION_PROMPT
        )
        assert condense.call_args.kwargs["query"] == PICO_CONDENSE_QUERY
        assert digest in sent_user_message(agent)
        assert TEXT not in sent_user_message(agent)
        assert extraction.population.state is PicoPartState.VERIFIED
        assert extraction.population.citations[0].start == text.index("Adults")
        assert extraction.condensed_from_chars == len(text)
        assert extraction.condensation_status == "completed"

    def test_condensation_failure_is_reported(self) -> None:
        agent = make_agent(condense_config=ProcessingConfig(max_context_chars=100))

        with (
            patch(
                "bmlib.quality.pico_agent.condense_text",
                side_effect=CondensationRefusedError("the map-reduce produced an empty digest"),
            ),
            pytest.raises(PicoError) as caught,
        ):
            agent.extract("A trial", "x" * 500, min_similarity=SIM)

        assert caught.value.kind is PicoErrorKind.CONDENSATION_FAILED
        assert str(caught.value) == (
            "a 500-character text could not be condensed to fit 100 characters: "
            "the map-reduce produced an empty digest"
        )
        assert isinstance(caught.value.__cause__, CondensationRefusedError)
        assert agent.llm.chat.call_count == 0

    def test_an_outage_during_condensation_reads_as_a_failed_run(self) -> None:
        """An outage on a long paper says the map-reduce failed, not that the
        text would not fit the budget."""
        agent = make_agent(condense_config=ProcessingConfig(max_context_chars=100))
        agent.llm.chat = MagicMock(side_effect=ConnectionError("the server is down"))

        with pytest.raises(PicoError) as caught:
            agent.extract("A trial", "Filler sentence. " * 40, min_similarity=SIM)

        assert caught.value.kind is PicoErrorKind.CONDENSATION_FAILED
        assert ": the map-reduce failed: No batch produced a result: " in str(caught.value)

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


def test_offsets_index_the_text_as_passed() -> None:
    """A caller highlights ``text[start:end]`` in the text it passed; a leading
    newline must not shift every highlight by one."""
    text = "\n  " + TEXT

    extraction = make_agent(reply()).extract("A trial", text, min_similarity=SIM)

    citation = extraction.population.citations[0]
    assert text[citation.start : citation.end] == "Adults aged 40-65"


#: Similarity 0.848 against ``TEXT`` (measured): found at 0.8, not at 0.9.
FUZZY_QUOTE = "adults between 40 and 65 were randomised to metformin"


class TestTheThresholdIsTheCallers:
    """The fuzzy match is what the verification exists for, so the caller's
    threshold is shown to decide it, and the citation says it was fuzzy."""

    def test_a_paraphrase_is_found_below_the_callers_threshold(self) -> None:
        result = read_part(part(quotes=[FUZZY_QUOTE]), TEXT, 0.8)

        assert result.state is PicoPartState.VERIFIED
        (citation,) = result.citations
        assert (citation.start, citation.end) == (0, 46)
        assert citation.text == "Adults aged 40-65 were randomised to metformin"
        assert citation.exact is False
        assert citation.similarity == pytest.approx(0.8484848484848485)

    def test_the_same_paraphrase_is_lost_above_it(self) -> None:
        result = read_part(part(quotes=[FUZZY_QUOTE]), TEXT, 0.9)

        assert result.state is PicoPartState.UNVERIFIED
        assert result.citations == []
        assert result.quotes_not_found == [FUZZY_QUOTE]

    def test_the_agent_passes_the_callers_threshold_on(self) -> None:
        fuzzy = reply(population=part(quotes=[FUZZY_QUOTE]))

        low = make_agent(fuzzy).extract("A trial", TEXT, min_similarity=0.8)
        high = make_agent(fuzzy).extract("A trial", TEXT, min_similarity=0.9)

        assert low.population.state is PicoPartState.VERIFIED
        assert high.population.state is PicoPartState.UNVERIFIED

    def test_a_threshold_of_one_is_accepted(self) -> None:
        extraction = make_agent(reply()).extract("A trial", TEXT, min_similarity=1.0)
        assert extraction.population.state is PicoPartState.VERIFIED

    @pytest.mark.parametrize("bad", ["0.9", None, True, 0.0, 1.5, float("nan")])
    def test_a_threshold_that_is_not_a_number_in_range_is_refused(self, bad: Any) -> None:
        agent = make_agent(reply())
        with pytest.raises(ValueError, match="min_similarity"):
            agent.extract("A trial", TEXT, min_similarity=bad)
        assert agent.llm.chat.call_count == 0


class TestWhatAReplyGives:
    def test_citations_keep_the_quotes_order(self) -> None:
        result = read_part(part(quotes=["placebo", "metformin"]), TEXT, SIM)
        assert [c.text for c in result.citations] == ["placebo", "metformin"]

    def test_an_unverified_part_counts_in_the_mean(self) -> None:
        extraction = read_extraction(
            reply(outcome=part(confidence=0.5, quotes=["lost words"])), TEXT, SIM
        )
        assert extraction.outcome.state is PicoPartState.UNVERIFIED
        assert extraction.overall_confidence == pytest.approx((0.9 * 3 + 0.5) / 4)

    def test_a_not_reported_part_keeps_its_confidence_out_of_the_mean(self) -> None:
        extraction = read_extraction(
            reply(comparison=part(reported=False, confidence=0.2)), TEXT, SIM
        )
        assert extraction.comparison.confidence == 0.2
        assert extraction.overall_confidence == pytest.approx(0.9)

    def test_an_array_reply_is_unusable(self) -> None:
        agent = make_agent("[1, 2]")
        with pytest.raises(PicoError) as caught:
            agent.extract("A trial", TEXT, min_similarity=SIM)
        assert caught.value.kind is PicoErrorKind.UNUSABLE_REPLY
        assert isinstance(caught.value.__cause__, ChatJsonWrongShapeError)
        assert agent.llm.chat.call_count == 3

    def test_two_unreadable_replies_chain_the_last_problem(self) -> None:
        agent = make_agent(reply(outcome=None))
        with pytest.raises(PicoError) as caught:
            agent.extract("A trial", TEXT, min_similarity=SIM)
        assert caught.value.kind is PicoErrorKind.UNUSABLE_REPLY
        assert isinstance(caught.value.__cause__, ValueError)
        assert str(caught.value.__cause__) == "outcome: expected an object, got NoneType"
        assert agent.llm.chat.call_count == 2

    def test_the_input_is_stripped_before_it_is_sent(self) -> None:
        agent = make_agent(reply())
        agent.extract("  A trial \n", "\n  " + TEXT + "  \n", min_similarity=SIM)
        assert "Paper Title: A trial\n\nPaper Text:\n" + TEXT + "\n\n" in sent_user_message(agent)

    def test_text_exactly_at_the_budget_is_sent_whole(self) -> None:
        agent = make_agent(reply(), condense_config=ProcessingConfig(max_context_chars=len(TEXT)))
        with patch("bmlib.quality.pico_agent.condense_text") as condense:
            extraction = agent.extract("A trial", TEXT, min_similarity=SIM)
        condense.assert_not_called()
        assert extraction.condensed_from_chars is None

    def test_a_condensed_suitability_check_carries_the_note(self) -> None:
        note = "Source text was condensed before the suitability check; ..."
        agent = make_agent(
            {"is_suitable": True}, condense_config=ProcessingConfig(max_context_chars=10)
        )
        with patch(
            "bmlib.quality.pico_agent.condense_text",
            return_value=("a digest", [note], ProcessingStatus.PARTIAL),
        ) as condense:
            suitability = agent.check_suitability("A trial", TEXT)
        assert condense.call_args.kwargs["stage"] == "the suitability check"
        assert suitability.notes == [note]
        assert suitability.condensation_status == "partial"
        assert suitability.condensed_from_chars == len(TEXT)

    def test_the_error_kinds_have_their_stored_values(self) -> None:
        assert {kind.name: kind.value for kind in PicoErrorKind} == {
            "EMPTY_INPUT": "empty_input",
            "REQUEST_FAILED": "request_failed",
            "TRUNCATED": "truncated",
            "UNUSABLE_REPLY": "unusable_reply",
            "CONDENSATION_FAILED": "condensation_failed",
            "STOPPED": "stopped",
        }


class TestStoredResults:
    """``from_dict`` reads what ``to_dict`` wrote and refuses anything else."""

    @staticmethod
    def _extraction() -> PicoExtraction:
        extraction = read_extraction(
            reply(
                population=part(quotes=[FUZZY_QUOTE, 7, "  "]),
                comparison=part(reported=False),
                outcome=part(quotes=["lost words"]),
            ),
            TEXT,
            0.8,
        )
        extraction.condensed_from_chars = 90_000
        extraction.condensation_status = "partial"
        extraction.notes = ["a note"]
        return extraction

    def test_every_field_survives_json(self) -> None:
        extraction = self._extraction()
        assert extraction.population.quotes_dropped == 2
        assert extraction.population.citations[0].exact is False
        suitability = read_suitability(
            {
                "is_suitable": False,
                "is_intervention_study": True,
                "has_comparison": False,
                "study_type": "case series",
                "confidence": 0.7,
                "rationale": "No comparison group.",
            }
        )
        suitability.condensed_from_chars = 70_000
        suitability.condensation_status = "completed"
        suitability.notes = ["n"]

        assert PicoExtraction.from_dict(json.loads(json.dumps(extraction.to_dict()))) == extraction
        assert (
            PicoSuitability.from_dict(json.loads(json.dumps(suitability.to_dict()))) == suitability
        )

    @pytest.mark.parametrize(
        ("path", "value", "match"),
        [
            (("population", "state"), "verified-ish", "PicoPartState"),
            (("outcome", "state"), "verified", "carries 0 citation"),
            (("comparison", "quotes_not_found"), ["q"], "not-reported part carries no quotes"),
            (("population", "confidence"), "0.9", "'confidence' is a str"),
            (("population", "confidence"), True, "'confidence' is a bool"),
            (("population", "confidence"), 3, "number in \\[0, 1\\]"),
            (("population", "quotes_not_found"), "abc", "'quotes_not_found' is a str"),
            (("population", "quotes_dropped"), -1, "must not be negative"),
            (("population", "citations"), None, "'citations' is a NoneType"),
            (("population", "description"), None, "'description' is a NoneType"),
            (("notes",), "hi", "'notes' is a str"),
            (("overall_confidence",), 0.1, "not the mean of its parts"),
        ],
    )
    def test_a_corrupt_extraction_is_refused(
        self, path: tuple[str, ...], value: Any, match: str
    ) -> None:
        data = self._extraction().to_dict()
        target = data
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        with pytest.raises(ValueError, match=match):
            PicoExtraction.from_dict(data)

    @pytest.mark.parametrize(
        ("key", "value", "match"),
        [
            ("start", 5, "invalid span"),
            ("text", "too short", "invalid span"),
            ("similarity", 1.5, "similarity must be in"),
            ("exact", True, "an exact match has similarity 1.0"),
            ("start", "0", "'start' is a str"),
        ],
    )
    def test_a_corrupt_citation_is_refused(self, key: str, value: Any, match: str) -> None:
        data = self._extraction().to_dict()
        data["population"]["citations"][0][key] = value
        with pytest.raises(ValueError, match=match):
            PicoExtraction.from_dict(data)

    @pytest.mark.parametrize(
        ("key", "value", "match"),
        [
            ("is_suitable", "no", "'is_suitable' is a str"),
            ("is_suitable", None, "'is_suitable' is a NoneType"),
            ("has_comparison", 1, "'has_comparison' is a int"),
            ("confidence", 1.2, "number in \\[0, 1\\]"),
        ],
    )
    def test_a_corrupt_suitability_is_refused(self, key: str, value: Any, match: str) -> None:
        data = read_suitability({"is_suitable": True}).to_dict()
        data[key] = value
        with pytest.raises(ValueError, match=match):
            PicoSuitability.from_dict(data)

    def test_a_part_cannot_be_built_verified_without_a_citation(self) -> None:
        with pytest.raises(ValueError, match="carries 0 citation"):
            PicoPart("d", PicoPartState.VERIFIED, 0.5)


class TestAPreparedPaper:
    """#638: a long paper is condensed once for both calls and every retry."""

    LONG = "Filler sentence. " * 20 + TEXT
    DIGEST = "Middle-aged adults were given a drug."

    def _agent(self, *replies: Any) -> PicoAgent:
        return make_agent(*replies, condense_config=ProcessingConfig(max_context_chars=100))

    def _condense(self, notes: list[str] | None = None) -> Any:
        status = ProcessingStatus.PARTIAL if notes else ProcessingStatus.COMPLETED
        return patch(
            "bmlib.quality.pico_agent.condense_text",
            return_value=(self.DIGEST, notes or [], status),
        )

    def test_both_calls_and_a_retry_condense_once(self) -> None:
        note = "Source text was condensed before the suitability check and extraction; ..."
        # Padded, so an offset into the stripped text would be caught.
        padded = "\n  " + self.LONG + " "
        agent = self._agent({"is_suitable": True}, reply(), reply())
        with self._condense([note]) as condense:
            prepared = agent.prepare("  A trial ", padded)
            suitability = agent.check_suitability_prepared(prepared)
            first = agent.extract_prepared(prepared, min_similarity=SIM)
            again = agent.extract_prepared(prepared, min_similarity=SIM)

        assert condense.call_count == 1
        assert condense.call_args.kwargs["stage"] == "the suitability check and extraction"
        assert condense.call_args.args[1] == self.LONG
        assert prepared.title == "A trial"
        assert prepared.text == padded
        assert prepared.digest == self.DIGEST
        for call in range(3):
            assert self.DIGEST in sent_user_message(agent, call)
            assert TEXT not in sent_user_message(agent, call)
        for result in (suitability, first, again):
            assert result.notes == [note]
            assert result.condensation_status == "partial"
            assert result.condensed_from_chars == len(self.LONG)
        # Verified against the paper as passed, never the digest.
        assert first.population.citations[0].start == padded.index("Adults")
        assert again == first

    def test_a_short_paper_is_sent_whole_and_stripped(self) -> None:
        agent = make_agent(reply())
        with patch("bmlib.quality.pico_agent.condense_text") as condense:
            prepared = agent.prepare("A trial", "  " + TEXT + "\n")
            extraction = agent.extract_prepared(prepared, min_similarity=SIM)
        condense.assert_not_called()
        assert prepared.digest is None
        assert prepared.sent == TEXT
        assert prepared.condensed_from_chars is None
        assert extraction.condensed_from_chars is None
        assert extraction.population.citations[0].start == 2

    def test_the_direct_calls_are_unchanged(self) -> None:
        """Through the prepared path, the direct calls keep their own note wording."""
        agent = self._agent(reply())
        with self._condense() as condense:
            agent.extract("A trial", self.LONG, min_similarity=SIM)
        assert condense.call_args.kwargs["stage"] == "extraction"

    def test_a_stored_paper_serves_both_calls(self) -> None:
        agent = self._agent({"is_suitable": True}, reply())
        with self._condense():
            stored = json.loads(json.dumps(agent.prepare("A trial", self.LONG).to_dict()))
        with patch("bmlib.quality.pico_agent.condense_text") as condense:
            prepared = PicoPrepared.from_dict(stored)
            agent.check_suitability_prepared(prepared)
            extraction = agent.extract_prepared(prepared, min_similarity=SIM)
        condense.assert_not_called()
        assert extraction.population.state is PicoPartState.VERIFIED
        assert prepared.to_dict() == stored

    def test_empty_input_is_refused_before_any_request(self) -> None:
        agent = make_agent()
        with pytest.raises(PicoError) as caught:
            agent.prepare(" ", None)
        assert caught.value.kind is PicoErrorKind.EMPTY_INPUT
        assert agent.llm.chat.call_count == 0

    def test_a_paper_prepared_for_a_larger_budget_is_refused(self) -> None:
        prepared = make_agent().prepare("A trial", self.LONG)
        agent = self._agent()
        for call in (
            lambda: agent.check_suitability_prepared(prepared),
            lambda: agent.extract_prepared(prepared, min_similarity=SIM),
        ):
            with pytest.raises(ValueError, match="over this agent's 100-character budget"):
                call()
        assert agent.llm.chat.call_count == 0

    def test_a_paper_condensed_for_a_smaller_budget_is_refused(self) -> None:
        """A wider agent would send the text whole, so the digest is not sent in its place."""
        with self._condense():
            prepared = self._agent().prepare("A trial", self.LONG)
        agent = make_agent()
        for call in (
            lambda: agent.check_suitability_prepared(prepared),
            lambda: agent.extract_prepared(prepared, min_similarity=SIM),
        ):
            with pytest.raises(
                ValueError, match=f"condensed from {len(self.LONG)} characters, which fit"
            ):
                call()
        assert agent.llm.chat.call_count == 0

    def test_a_digest_of_a_text_exactly_at_the_budget_is_refused(self) -> None:
        """``prepare`` condenses only a text over the budget: one at it is sent whole."""
        agent = self._agent()
        prepared = PicoPrepared("A trial", " " + "x" * 100, "a digest", "completed")
        with pytest.raises(ValueError, match="condensed from 100 characters, which fit"):
            agent.check_suitability_prepared(prepared)
        assert agent.llm.chat.call_count == 0

    @pytest.mark.parametrize("bad", [0.0, 1.5, float("nan"), True])
    def test_an_invalid_min_similarity_makes_no_call(self, bad: Any) -> None:
        agent = make_agent()
        prepared = agent.prepare("A trial", TEXT)
        with pytest.raises(ValueError, match="min_similarity"):
            agent.extract_prepared(prepared, min_similarity=bad)
        assert agent.llm.chat.call_count == 0

    @pytest.mark.parametrize("extract", [False, True])
    def test_an_unknown_provider_is_refused_before_any_request(self, extract: bool) -> None:
        prepared = make_agent().prepare("A trial", TEXT)
        agent = PicoAgent(llm=LLMClient(), model="nosuchprovider:model")
        with pytest.raises(ValueError, match="nosuchprovider"):
            if extract:
                agent.extract_prepared(prepared, min_similarity=SIM)
            else:
                agent.check_suitability_prepared(prepared)

    @pytest.mark.parametrize(
        "status", sorted({s.value for s in ProcessingStatus} - {ProcessingStatus.FAILED.value})
    )
    def test_every_status_a_digest_finishes_with_is_stored(self, status: str) -> None:
        prepared = PicoPrepared("A trial", TEXT, "a digest", status, ("n",))
        assert PicoPrepared.from_dict(json.loads(json.dumps(prepared.to_dict()))) == prepared

    def test_the_notes_are_the_values_checked(self) -> None:
        notes = ["n"]
        prepared = PicoPrepared("A trial", TEXT, "a digest", "partial", notes)  # type: ignore[arg-type]
        notes.append("m")
        assert prepared.notes == ("n",)

    @pytest.mark.parametrize(
        ("fields", "match"),
        [
            ({"title": "", "text": " \n"}, "both the title and the text are empty"),
            ({"title": "  ", "text": ""}, "the title '  ' is not stripped"),
            ({"title": " A trial"}, "the title ' A trial' is not stripped"),
            (
                {"text": " ", "digest": "d", "condensation_status": "completed"},
                "a digest stands for an empty text",
            ),
            ({"condensation_status": "completed"}, "carries no condensation status"),
            ({"notes": ["n"]}, "carries no condensation status"),
            ({"digest": " ", "condensation_status": "completed"}, "the digest is blank"),
            ({"digest": "d"}, "condensation_status None is not one"),
            ({"digest": "d", "condensation_status": "failed"}, "'failed' is not one"),
            ({"text": None}, "'text' is a NoneType"),
            ({"notes": [1]}, "holds a member that is not text"),
        ],
    )
    def test_a_corrupt_stored_paper_is_refused(self, fields: dict[str, Any], match: str) -> None:
        data = {**PicoPrepared("A trial", TEXT).to_dict(), **fields}
        with pytest.raises(ValueError, match=match):
            PicoPrepared.from_dict(data)


CONTRACT = json.loads(
    (Path(__file__).parent / "fixtures" / "pico_stop_num_ctx.json").read_text(encoding="utf-8")
)


def _contract_run(case: dict[str, Any]) -> tuple[list[Any], str | None]:
    """Run one contract case: the real agent and map-reduce over a fake
    Ollama client; returns each request's ``num_ctx`` and the error kind."""
    stop = threading.Event()
    sent: list[Any] = []

    def chat(**kwargs: Any) -> LLMResponse:
        index = len(sent)
        sent.append(kwargs.get("num_ctx"))
        if case.get("fail_request") == index:
            raise ConnectionError("the connection was reset")
        if case.get("stop_at_request") == index:
            stop.set()
            if case["request_at_stop"] == "fails":
                raise ConnectionError("the server went away")
        content = (
            json.dumps(CONTRACT["json_reply"]) if kwargs.get("json_mode") else CONTRACT["digest"]
        )
        return _response(content)

    llm = MagicMock()
    llm.chat = MagicMock(side_effect=chat)
    agent = PicoAgent(
        llm=llm,
        model=CONTRACT["model"],
        condense_config=ProcessingConfig(max_context_chars=CONTRACT["max_context_chars"]),
        num_ctx=case["num_ctx"],
        stop=stop,
    )
    if case.get("stop_before"):
        stop.set()
    title = CONTRACT["title"]
    text = CONTRACT["text_unit"] * case["text_repeat"]
    similarity = CONTRACT["min_similarity"]
    with patch("bmlib.context_processor.llm_processor.time.sleep"):
        try:
            if case["fn"] == "extract":
                agent.extract(title, text, min_similarity=similarity)
            elif case["fn"] == "suitability":
                agent.check_suitability(title, text)
            elif case["fn"] == "prepare":
                agent.prepare(title, text)
            else:
                prepared = agent.prepare(title, text)
                assert prepared.digest is not None
                agent.check_suitability_prepared(prepared)
                agent.extract_prepared(prepared, min_similarity=similarity)
        except PicoError as exc:
            return sent, exc.kind.value
    return sent, None


class TestTheStopAndNumCtxContract:
    """The shared fixture (#640, #642): Rust's ``tests/pico_stop_contract.rs``
    runs the same cases and must agree."""

    def test_the_stopped_kind_is_the_fixtures(self) -> None:
        assert PicoErrorKind.STOPPED.value == CONTRACT["stopped_kind"]

    @pytest.mark.parametrize("case", CONTRACT["cases"], ids=lambda case: case["name"])
    def test_a_case(self, case: dict[str, Any]) -> None:
        sent, error = _contract_run(case)

        assert error == case["expected"]["error"]
        assert len(sent) == case["expected"]["requests"]
        assert all(num_ctx == case["num_ctx"] for num_ctx in sent)


class TestTheStop:
    """#642: the stop as an ``Event`` or a callable, read before each request."""

    @pytest.mark.parametrize("as_callable", [False, True])
    def test_a_set_stop_sends_nothing(self, as_callable: bool) -> None:
        event = threading.Event()
        event.set()
        agent = make_agent(reply(), stop=event.is_set if as_callable else event)
        with pytest.raises(PicoError) as caught:
            agent.extract("A trial", TEXT, min_similarity=SIM)

        assert caught.value.kind is PicoErrorKind.STOPPED
        assert agent.llm.chat.call_count == 0

    def test_an_unset_stop_changes_nothing(self) -> None:
        agent = make_agent(reply(), stop=threading.Event())

        assert agent.extract("A trial", TEXT, min_similarity=SIM).population.citations

    def test_a_stop_during_an_unusable_reply_is_stopped(self) -> None:
        event = threading.Event()
        agent = make_agent("not json", stop=event)
        agent.llm.chat.side_effect = lambda **_: (event.set(), _response("not json"))[1]
        with pytest.raises(PicoError) as caught:
            agent.extract("A trial", TEXT, min_similarity=SIM)

        assert caught.value.kind is PicoErrorKind.STOPPED
        # chat_json's own re-ask is refused before it is sent.
        assert agent.llm.chat.call_count == 1

    def test_a_stop_that_is_neither_is_refused(self) -> None:
        with pytest.raises(TypeError, match="stop"):
            make_agent(reply(), stop=True)


class TestNumCtx:
    """#640: the window reaches every request, and a bad one is refused up front."""

    def test_it_reaches_the_request(self) -> None:
        agent = make_agent(reply(), num_ctx=16384)
        agent.extract("A trial", TEXT, min_similarity=SIM)

        assert agent.llm.chat.call_args.kwargs["num_ctx"] == 16384

    def test_none_sends_no_key(self) -> None:
        agent = make_agent(reply())
        agent.extract("A trial", TEXT, min_similarity=SIM)

        assert "num_ctx" not in agent.llm.chat.call_args.kwargs

    @pytest.mark.parametrize("bad", [0, -1, True, 1.5, "8192", MAX_NUM_CTX + 1])
    def test_a_bad_value_is_refused(self, bad: Any) -> None:
        with pytest.raises(ValueError, match="num_ctx"):
            make_agent(reply(), num_ctx=bad)
