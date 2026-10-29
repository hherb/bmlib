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

from typing import Any

import pytest

from bmlib.quality.pico_agent import (
    PICO_PARTS,
    read_extraction,
    read_part,
    read_suitability,
    verify_quotes,
)
from bmlib.quality.pico_models import (
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
