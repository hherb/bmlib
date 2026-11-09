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

"""Tests for the unattended pipeline: design, outcomes, RoB 2 and the ladder in one call."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from bmlib.agents.base import BaseAgent
from bmlib.context_processor import ProcessingConfig
from bmlib.llm.data_types import LLMResponse
from bmlib.quality.design_agent import DESIGN_PROMPT_VERSION, ProposalError, ProposalErrorKind
from bmlib.quality.evidence_ladder import LADDER_VERSION, EvidenceLadder, LadderSettings, Level
from bmlib.quality.evidence_weight import (
    Assessed,
    AssessedOutcome,
    EvidenceWeightError,
    NoPrimaryOutcome,
    NotCovered,
    _any_condensed,
    assess_unattended,
    evidence_weight_from_dict,
)
from bmlib.quality.outcome_agent import OUTCOME_PROMPT_VERSION, OutcomeProposal
from bmlib.quality.rob2_agent import DomainAssessment, Rob2RunError
from bmlib.quality.rob2_algorithm import Domain, Judgement
from tests.test_rob2_agent import SIM, ScriptedCondenser, reply
from tests.test_rob2_agent import TEXT as RANDOMISED_TEXT

DESIGN_QUOTE = "Participants were randomly assigned 1:1 to metformin or placebo."
OUTCOME_QUOTE = "The primary outcome was all-cause mortality at 28 days."
TEXT = f"{DESIGN_QUOTE} {OUTCOME_QUOTE} {RANDOMISED_TEXT}"
SETTINGS = LadderSettings()


@pytest.fixture(autouse=True)
def _no_backoff() -> Any:
    with patch("bmlib.agents.base.time.sleep"):
        yield


def design(study_design: str = "rct", layout: str | None = "parallel") -> dict[str, Any]:
    return {
        "study_design": study_design,
        "rct_layout": layout,
        "quotes": [DESIGN_QUOTE],
        "reasoning": "Why.",
    }


def outcome(name: str, **result: Any) -> dict[str, Any]:
    body = {
        "measure": "risk ratio",
        "scale": "ratio",
        "estimate": 0.7,
        "ci_low": 0.55,
        "ci_high": 0.88,
        "ci_level": 0.95,
        "n_analysed": 1200,
    }
    body.update(result)
    return {"name": name, "timepoint": "28 days", "quotes": [OUTCOME_QUOTE], "result": body}


def rob2_replies(outcomes: int = 1) -> list[dict[str, Any]]:
    return [reply(Domain.D1)] + [reply(d) for d in list(Domain)[1:]] * outcomes


def run(*replies: Any, text: str = TEXT, **kwargs: Any) -> tuple[Any, MagicMock]:
    """Run the pipeline against a model that answers *replies* in order."""
    llm = MagicMock()
    llm.chat = MagicMock(
        side_effect=[
            LLMResponse(
                content=r if isinstance(r, str) else json.dumps(r),
                model="test:model",
                input_tokens=1,
                output_tokens=1,
                stop_reason="stop",
            )
            for r in replies
        ]
    )
    result = assess_unattended(
        llm,
        "test:model",
        settings=SETTINGS,
        title="A trial",
        text=text,
        min_similarity=SIM,
        **kwargs,
    )
    return result, llm.chat


class TestWhereItStops:
    @pytest.mark.parametrize(
        "proposal",
        [design("cohort_prospective", None), design("rct", "crossover"), design("rct", "cluster")],
    )
    def test_a_design_not_covered_stops_after_one_request(self, proposal: dict[str, Any]) -> None:
        result, chat = run(proposal)
        assert isinstance(result, NotCovered)
        assert result.design.design.value == proposal["study_design"]
        assert chat.call_count == 1

    def test_no_primary_outcome_stops_before_rob2(self) -> None:
        result, chat = run(design(), {"primary_outcomes": []})
        assert isinstance(result, NoPrimaryOutcome)
        assert result.outcomes.outcomes == []
        assert chat.call_count == 2

    def test_a_step_that_cannot_be_made_names_its_own_error(self) -> None:
        with pytest.raises(EvidenceWeightError) as raised:
            run(text="   ")
        assert isinstance(raised.value.cause, ProposalError)
        assert raised.value.cause.kind is ProposalErrorKind.EMPTY_INPUT
        assert str(raised.value) == "the paper's text is empty"

    def test_a_stop_in_rob2_is_the_rob2_error(self) -> None:
        llm = MagicMock()
        llm.chat = MagicMock(
            side_effect=[
                LLMResponse(json.dumps(r), "test:model", 1, 1, "stop")
                for r in (design(), {"primary_outcomes": [outcome("Mortality")]})
            ]
        )
        with pytest.raises(EvidenceWeightError) as raised:
            assess_unattended(
                llm,
                "test:model",
                settings=SETTINGS,
                title="A trial",
                text=TEXT,
                min_similarity=SIM,
                stop=lambda: llm.chat.call_count >= 2,
            )
        assert isinstance(raised.value.cause, Rob2RunError)


class TestAssessed:
    def test_one_outcome_runs_through_to_a_weight(self) -> None:
        result, chat = run(design(), {"primary_outcomes": [outcome("Mortality")]}, *rob2_replies())
        assert isinstance(result, Assessed)
        assert chat.call_count == 2 + 5
        [only] = result.assessed
        assert only.outcome.name == "Mortality" and only.ladder_error is None
        assert only.ladder is not None and only.ladder.start is Level.HIGH
        assert only.ladder.level is Level.HIGH
        assert result.paper_weight == only.ladder.weight == SETTINGS.weight_high
        assert result.condensed is False
        assert result.ladder_version == LADDER_VERSION and result.settings == SETTINGS
        assert result.design.prompt_version == DESIGN_PROMPT_VERSION
        assert result.outcomes.prompt_version == OUTCOME_PROMPT_VERSION

    def test_a_refused_result_has_no_ladder_and_the_other_outcome_sets_the_weight(self) -> None:
        refused = outcome("Hospitalisation", ci_low=0.9, ci_high=0.5)
        result, chat = run(
            design(),
            {"primary_outcomes": [outcome("Mortality"), refused]},
            *rob2_replies(2),
        )
        assert isinstance(result, Assessed)
        assert chat.call_count == 2 + 9
        good, bad = result.assessed
        assert bad.ladder is None and bad.rob2.overall is Judgement.LOW
        assert bad.outcome.result_error is not None
        assert bad.ladder_error == f"the reported result was refused: {bad.outcome.result_error}"
        assert good.ladder is not None
        assert result.paper_weight == good.ladder.weight

    def test_a_failed_domain_leaves_that_outcome_not_assessed(self) -> None:
        bad = {"answers": {"3.1": {"answer": "Unclear", "quotes": [], "reasoning": "x"}}}
        d1, d2, d3, d4, d5 = rob2_replies()
        replies = [d1, d2, bad, bad, d4, d5, *rob2_replies()[1:]]
        result, _ = run(
            design(),
            {"primary_outcomes": [outcome("Mortality"), outcome("Hospitalisation")]},
            *replies,
        )
        assert isinstance(result, Assessed)
        failed, fine = result.assessed
        assert failed.rob2.overall is None and failed.ladder is None
        assert failed.ladder_error is not None
        assert failed.ladder_error.startswith(
            "the outcome's risk of bias was not assessed: D3 failed ("
        )
        assert "answers.3.1.answer" in failed.ladder_error
        assert fine.ladder is not None
        assert result.paper_weight == fine.ladder.weight

    def test_no_ladder_at_all_is_no_weight(self) -> None:
        bad = {"answers": {}}
        result, _ = run(
            design(), {"primary_outcomes": [outcome("Mortality")]}, bad, bad, *rob2_replies()[1:]
        )
        assert isinstance(result, Assessed)
        [only] = result.assessed
        assert only.ladder is None
        assert only.ladder_error is not None and "D1 failed (" in only.ladder_error
        assert result.paper_weight is None

    def test_a_paper_over_the_window_is_flagged_condensed(self) -> None:
        digest = ScriptedCondenser(TEXT)
        result, _ = run(
            design(),
            {"primary_outcomes": [outcome("Mortality")]},
            *rob2_replies(),
            text=TEXT + " filler" * 8000,
            condenser=digest,
        )
        assert isinstance(result, Assessed)
        assert result.condensed is True
        assert len(digest.queries) == 2 + 5

    def test_every_option_reaches_all_three_steps(self) -> None:
        budgets: list[int] = []

        class Recording(ScriptedCondenser):
            def condense(self, query: str, text: str, label: str, config: Any, **kw: Any) -> Any:
                budgets.append(config.max_context_chars)
                return super().condense(query, text, label, config, **kw)

        original = BaseAgent.chat_json
        with patch.object(BaseAgent, "chat_json", autospec=True, side_effect=original) as chat_json:
            result, chat = run(
                design(),
                {"primary_outcomes": [outcome("Mortality")]},
                *rob2_replies(),
                condenser=Recording(TEXT),
                temperature=0.4,
                max_tokens=777,
                max_retries=2,
                condense_config=ProcessingConfig(max_context_chars=100),
                num_ctx=8192,
            )
        assert isinstance(result, Assessed) and result.condensed is True
        # Design, outcomes, then D1-D5: every step condensed at the small budget.
        assert budgets == [100] * (2 + 5)
        assert chat.call_count == 2 + 5
        for call in chat.call_args_list:
            assert call.kwargs["temperature"] == 0.4
            assert call.kwargs["max_tokens"] == 777
            assert call.kwargs["num_ctx"] == 8192
        assert [c.kwargs["max_retries"] for c in chat_json.call_args_list] == [2] * (2 + 5)


class TestCondensedIfAnyStepWas:
    def parts(self) -> tuple[Any, OutcomeProposal, Any]:
        result, _ = run(design(), {"primary_outcomes": [outcome("M")]}, *rob2_replies())
        assert isinstance(result, Assessed)
        return result.design, result.outcomes, result.run

    def test_none(self) -> None:
        assert _any_condensed(*self.parts()) is False

    def test_the_design_alone(self) -> None:
        d, o, r = self.parts()
        d.condensed = True
        assert _any_condensed(d, o, r) is True

    def test_the_outcomes_alone(self) -> None:
        d, o, r = self.parts()
        o.condensed = True
        assert _any_condensed(d, o, r) is True

    def test_one_rob2_domain_alone(self) -> None:
        d, o, r = self.parts()
        assert isinstance(r.outcomes[0].domains[2], DomainAssessment)
        r.outcomes[0].domains[2].condensed = True
        assert _any_condensed(d, o, r) is True


class TestTheWireForm:
    def assessed(self) -> Assessed:
        refused = outcome("Hospitalisation", ci_low=0.9, ci_high=0.5)
        result, _ = run(
            design(), {"primary_outcomes": [outcome("Mortality"), refused]}, *rob2_replies(2)
        )
        assert isinstance(result, Assessed)
        return result

    def test_every_status_round_trips_through_json(self) -> None:
        results = [
            run(design("cohort_prospective", None))[0],
            run(design(), {"primary_outcomes": []})[0],
            self.assessed(),
        ]
        for original in results:
            wire = json.loads(json.dumps(original.to_dict()))
            assert evidence_weight_from_dict(wire) == original

    def test_a_ladder_round_trips(self) -> None:
        ladder = self.assessed().assessed[0].ladder
        assert ladder is not None
        assert EvidenceLadder.from_wire(json.loads(json.dumps(ladder.to_wire()))) == ladder

    @pytest.mark.parametrize(
        ("change", "message"),
        [
            (lambda w: w.update(status="maybe"), 'status "maybe"'),
            (lambda w: w.update(ladder_version="ladder/0"), "ladder_version ladder/0 is not"),
            (lambda w: w["settings"].update(high_steps=-1), "high_steps must be a whole"),
            (lambda w: w.pop("settings"), "the ladder settings are not an object"),
            (lambda w: w["design"].update(prompt_version=-1), "fits 32 bits"),
            (lambda w: w["outcomes"].pop("prompt_version"), "prompt_version is not a whole"),
            (lambda w: w.update(paper_weight=0.1), "paper_weight"),
            (lambda w: w["assessed"].pop(), "same length"),
            (lambda w: w["assessed"].reverse(), r"assessed\[0\] is not outcome 0"),
            (lambda w: w["assessed"][0].update(ladder_error="x"), "exactly one"),
            (lambda w: w["assessed"][0]["ladder"].update(level="best"), "not a level"),
            (
                lambda w: w["assessed"][0]["ladder"]["steps"].append(
                    {"reason": "huh", "levels": 0}
                ),
                "ladder reason",
            ),
        ],
    )
    def test_what_does_not_add_up_is_refused(self, change: Any, message: str) -> None:
        wire = json.loads(json.dumps(self.assessed().to_dict()))
        change(wire)
        with pytest.raises(ValueError, match=message):
            evidence_weight_from_dict(wire)

    def test_an_assessed_outcome_needs_a_ladder_or_a_reason(self) -> None:
        data = self.assessed().assessed[0].to_dict()
        data["ladder"] = None
        with pytest.raises(ValueError, match="exactly one"):
            AssessedOutcome.from_dict(data)
