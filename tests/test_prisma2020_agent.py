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

"""The PRISMA 2020 agent: suitability, a part prepared and assessed.

``rust/oracle/dump_prisma2020_agent.py`` holds the Rust agent to this one; these
tests pin the rules a whole run must keep: a part condenses for its own items,
quotes index the paper and never the digest, a partial digest is refused, and
nothing is ever cut.
"""

from __future__ import annotations

import json
import threading
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from bmlib.context_processor import (
    ExtractionResult,
    LLMChunkProcessor,
    ProcessingConfig,
    ProcessingResult,
    ProcessingStatus,
)
from bmlib.llm.data_types import LLMResponse
from bmlib.quality.prisma2020_agent import (
    PRISMA_SYSTEM_PROMPT,
    SUITABILITY_CONDENSE_QUERY,
    Prisma2020Agent,
    PrismaError,
    PrismaErrorKind,
    PrismaPrepared,
    part_prompt_chars,
    suitability_prompt_chars,
)
from bmlib.quality.prisma2020_checklist import item
from bmlib.quality.prisma2020_models import Judgement
from bmlib.quality.prisma2020_parts import part_query

TITLE = "Inhaled steroids in asthma: a systematic review"
SEARCH = "We searched MEDLINE and Embase from inception to March 2020"
SCREEN = "Two reviewers independently screened titles and abstracts"
TEXT = (
    "Methods. Studies were eligible if they were randomised trials of adults with asthma. "
    f"{SEARCH}. {SCREEN}, resolving disagreements by discussion."
)
DIGEST = f"{SCREEN}. {SEARCH}."
IDS = ["6", "7", "8"]
SIM = 0.9


def _part_reply() -> dict[str, Any]:
    return {
        "items": [
            {"id": "6", "judgement": "partly_reported", "explanation": "e", "quotes": [SEARCH]},
            {"id": "7", "judgement": "not_reported", "explanation": "e", "quotes": []},
            {"id": "8", "judgement": "reported", "explanation": "e", "quotes": [SCREEN]},
        ]
    }


_SUITABLE = {
    "is_systematic_review": True,
    "is_meta_analysis": False,
    "document_type": "systematic review",
    "variant": None,
    "rationale": "r",
    "confidence": 0.9,
}


def make_agent(*replies: dict[str, Any], **kwargs: Any) -> Prisma2020Agent:
    """An agent whose client answers *replies* in order, repeating the last."""
    responses = [
        LLMResponse(
            content=json.dumps(reply),
            model="test:model",
            input_tokens=1,
            output_tokens=1,
            stop_reason="stop",
        )
        for reply in replies
    ]
    llm = MagicMock()
    llm.chat = MagicMock(side_effect=responses + [responses[-1]] * 20)
    return Prisma2020Agent(llm=llm, model="test:model", **kwargs)


def sent_contents(agent: Prisma2020Agent) -> list[str]:
    """Every message every model call was sent."""
    return [m.content for call in agent.llm.chat.call_args_list for m in call.kwargs["messages"]]


def digest_result(content: str, status: ProcessingStatus) -> ProcessingResult:
    return ProcessingResult(
        final_result=ExtractionResult(content=content, confidence=1.0),
        status=status,
        total_items_processed=1,
        batches_created=1,
        recursion_levels_used=0,
        successful_batches=1,
    )


def condensing(content: str = DIGEST, status: ProcessingStatus = ProcessingStatus.COMPLETED) -> Any:
    """Patch the map-reduce to return *content*, recording what it was given."""
    return patch.object(
        LLMChunkProcessor, "process", autospec=True, return_value=digest_result(content, status)
    )


SMALL = ProcessingConfig(max_context_chars=150)


@pytest.fixture(autouse=True)
def _no_backoff() -> Any:
    with patch("bmlib.agents.base.time.sleep"):
        yield


def test_whole_paper_is_assessed_against_its_items() -> None:
    agent = make_agent(_part_reply())
    part = agent.assess_part(agent.prepare_part(TITLE, TEXT, IDS), IDS, min_similarity=SIM)
    assert [row.id for row in part.items] == IDS
    assert [row.judgement for row in part.items] == [
        Judgement.PARTLY_REPORTED,
        Judgement.NOT_REPORTED,
        Judgement.REPORTED,
    ]
    assert part.condensed_from_chars is None
    messages = agent.llm.chat.call_args.kwargs["messages"]
    assert messages[0].content == PRISMA_SYSTEM_PROMPT
    user = messages[1].content
    assert TEXT in user
    for item_id in IDS:
        row = item(item_id)
        assert row is not None
        assert row.wording in user and row.guidance in user


def test_part_condenses_with_its_query() -> None:
    agent = make_agent(_part_reply(), condense_config=SMALL)
    with condensing() as process:
        prepared = agent.prepare_part(TITLE, TEXT, IDS)
    rows = [item(i) for i in IDS]
    assert process.call_count == 1
    assert process.call_args.kwargs["query"] == part_query([r for r in rows if r is not None])
    # A failed batch fails the part (spec 6.3): the map-reduce may not carry on.
    assert process.call_args.args[0].config.continue_on_error is False
    assert prepared.sent == DIGEST


def test_suitability_condenses_with_its_own_query() -> None:
    agent = make_agent(_SUITABLE, condense_config=SMALL)
    with condensing() as process:
        result = agent.check_suitability(TITLE, TEXT)
    assert process.call_args.kwargs["query"] == SUITABILITY_CONDENSE_QUERY
    assert result.is_suitable
    assert result.condensed_from_chars == len(TEXT)
    assert result.condensation_status == "completed"


def test_quotes_located_in_whole_text() -> None:
    agent = make_agent(_part_reply(), condense_config=SMALL)
    with condensing():
        prepared = agent.prepare_part(TITLE, TEXT, IDS)
    part = agent.assess_part(prepared, IDS, min_similarity=SIM)
    citation = part.items[0].citations[0]
    # The digest puts SCREEN first; the offsets are the paper's.
    assert TEXT[citation.start : citation.end] == SEARCH
    assert DIGEST[citation.start : citation.end] != SEARCH
    assert part.condensed_from_chars == len(TEXT)
    assert part.condensation_status == "completed"


@pytest.mark.parametrize("status", [ProcessingStatus.PARTIAL, ProcessingStatus.TRUNCATED])
def test_partial_digest_is_refused(status: ProcessingStatus) -> None:
    agent = make_agent(_part_reply(), condense_config=SMALL)
    with condensing(status=status), pytest.raises(PrismaError) as raised:
        agent.prepare_part(TITLE, TEXT, IDS)
    assert raised.value.kind is PrismaErrorKind.CONDENSATION_FAILED
    assert status.value in str(raised.value)
    assert agent.llm.chat.call_count == 0


@pytest.mark.parametrize("budget", [250_000, 150_000])
def test_suitability_never_cut(budget: int) -> None:
    # No abstract: the whole paper is checked, sent whole or condensed, never sliced.
    text = " ".join(f"Sentence {i} of a long review." for i in range(8000))[:200_000]
    assert len(text) == 200_000
    agent = make_agent(_SUITABLE, condense_config=ProcessingConfig(max_context_chars=budget))
    with condensing() as process:
        agent.check_suitability(TITLE, text)
    stripped = text.strip()
    for content in sent_contents(agent):
        for end in (1_000, 100_000, len(stripped) - 1):
            if stripped[:end] in content:
                assert stripped in content, "a slice of the paper was sent"
    if budget >= len(stripped):
        assert process.call_count == 0
        assert any(stripped in content for content in sent_contents(agent))
    else:
        assert process.call_count == 1
        assert any(DIGEST in content for content in sent_contents(agent))


def test_a_stop_set_first_sends_nothing() -> None:
    event = threading.Event()
    event.set()
    agent = make_agent(_part_reply(), stop=event, condense_config=SMALL)
    with condensing() as process, pytest.raises(PrismaError) as raised:
        agent.prepare_part(TITLE, TEXT, IDS)
    assert raised.value.kind is PrismaErrorKind.STOPPED
    assert process.call_count == 0
    agent = make_agent(_part_reply(), stop=event)
    prepared = agent.prepare_part(TITLE, TEXT, IDS)  # a short paper sends nothing
    with pytest.raises(PrismaError) as raised:
        agent.assess_part(prepared, IDS, min_similarity=SIM)
    assert raised.value.kind is PrismaErrorKind.STOPPED
    assert agent.llm.chat.call_count == 0


def test_a_stop_set_during_a_failing_call_is_stopped() -> None:
    event = threading.Event()
    llm = MagicMock()

    def fail(**_kwargs: Any) -> LLMResponse:
        event.set()
        raise ConnectionError("network down")

    llm.chat = MagicMock(side_effect=fail)
    agent = Prisma2020Agent(llm=llm, model="test:model", stop=event)
    with pytest.raises(PrismaError) as raised:
        agent.check_suitability(TITLE, TEXT)
    assert raised.value.kind is PrismaErrorKind.STOPPED


@pytest.mark.parametrize("bad", [[], ["99"], ["7", "7"]])
def test_bad_item_ids_are_refused_before_any_request(bad: list[str]) -> None:
    agent = make_agent(_part_reply())
    with pytest.raises(ValueError, match="item"):
        agent.prepare_part(TITLE, TEXT, bad)
    prepared = agent.prepare_part(TITLE, TEXT, IDS)
    with pytest.raises(ValueError, match="item"):
        agent.assess_part(prepared, bad, min_similarity=SIM)
    assert agent.llm.chat.call_count == 0


def test_a_bad_similarity_is_refused_before_any_request() -> None:
    agent = make_agent(_part_reply())
    prepared = agent.prepare_part(TITLE, TEXT, IDS)
    with pytest.raises(ValueError, match="min_similarity"):
        agent.assess_part(prepared, IDS, min_similarity=0.0)
    assert agent.llm.chat.call_count == 0


def test_a_part_prepared_for_another_budget_is_refused() -> None:
    small = make_agent(_part_reply(), condense_config=SMALL)
    with condensing():
        prepared = small.prepare_part(TITLE, TEXT, IDS)
    with pytest.raises(ValueError, match="budget"):
        make_agent(_part_reply()).assess_part(prepared, IDS, min_similarity=SIM)


def test_part_prompt_chars_holds_the_longest_prompt() -> None:
    agent = make_agent(_part_reply())
    agent.assess_part(agent.prepare_part("", "x", IDS), IDS, min_similarity=SIM)
    sent = sum(len(m.content) for m in agent.llm.chat.call_args.kwargs["messages"])
    assert sent - len("x") <= part_prompt_chars(IDS)
    assert part_prompt_chars(IDS) < part_prompt_chars([*IDS, "13d"])


def test_suitability_prompt_chars_holds_the_check_prompt() -> None:
    agent = make_agent(_SUITABLE)
    agent.check_suitability("", "x")
    sent = sum(len(m.content) for m in agent.llm.chat.call_args.kwargs["messages"])
    assert sent - len("x") <= suitability_prompt_chars()
    assert suitability_prompt_chars() > len(PRISMA_SYSTEM_PROMPT)


@pytest.mark.parametrize("other", [["7"], ["8", "7"], ["7", "8", "9"]])
def test_a_digest_for_other_items_is_refused_before_any_request(other: list[str]) -> None:
    # Design 6.5: a key item re-checked alone needs a digest of its own.
    agent = make_agent(_part_reply(), condense_config=SMALL)
    with condensing():
        prepared = agent.prepare_part(TITLE, TEXT, ["7", "8"])
        suitability = agent._prepare(TITLE, TEXT, SUITABILITY_CONDENSE_QUERY, "s")
    assert prepared.condensed_for == ("7", "8")
    with pytest.raises(ValueError, match="condensed for items"):
        agent.assess_part(prepared, other, min_similarity=SIM)
    with pytest.raises(ValueError, match="condensed for items"):
        agent.assess_part(PrismaPrepared(suitability), ["7"], min_similarity=SIM)
    assert agent.llm.chat.call_count == 0


def test_a_whole_paper_serves_any_items() -> None:
    agent = make_agent({"items": []})
    prepared = agent.prepare_part(TITLE, TEXT, ["7", "8"])
    assert prepared.condensed_for == ()
    for subset in (["7"], ["8"], ["13d", "7"]):
        assert agent.assess_part(prepared, subset, min_similarity=SIM).missing == subset
    assert agent.llm.chat.call_count == 3
