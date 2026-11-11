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

"""RoB 2's domain 5 from facts: the reply, its reading and the rules.

D5 asks no signalling question.  The model reports facts about the plan
behind the outcome assessed, the other measurements of that outcome the paper
names, and the main analysis set against the one reported, each with quotes
copied from the paper (:func:`facts_prompt`).  Code then decides:

- a fact counts only when the model claims it, one of its quotes is found in
  the paper, and the quote can show it (:mod:`bmlib.quality.rob2_d5_checks`,
  the planning check included);
- an other measurement is missing only when its naming passage is found and
  neither the reply nor a second, narrow look (:func:`second_look_prompt`)
  gives a result passage with a number or a pointer to a figure, table or
  supplement; a second look that fails leaves it undecided;
- 5.1, 5.2 and 5.3 follow from these by the evidence ladder
  (:func:`d5_answers`), and RoB 2's algorithm gives the judgement
  (``rob2_agent.d5_assessment``).

The design is the RoB 2 wording smoke test's (StudyAnalyzer
``docs/smokescreens/2026-11-10-rob2-wording.md``, arm F version 3 with its
checks) and the evidence-weight design's §4.6.  The method is RoB 2 (Sterne
JAC et al., BMJ 2019;366:l4898; guidance by Higgins JPT et al., 2019); the
wording here is bmlib's own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bmlib.quality._full_text_agent import UnreadableReplyError, check_min_similarity
from bmlib.quality.outcome_agent import ProposedOutcome
from bmlib.quality.pico_agent import verify_quotes
from bmlib.quality.pico_models import PicoCitation, PicoContradictedQuote
from bmlib.quality.rob2_algorithm import Answer
from bmlib.quality.rob2_d5_checks import (
    D5_FACTS,
    D5Fact,
    FactVerdict,
    fact_verdict,
    shows_result,
)
from bmlib.quality.rob2_questions import outcome_block

__all__ = [
    "D5_FACTS_SYSTEM_PROMPT",
    "D5_FACT_MEANINGS",
    "D5_SECOND_LOOK_SYSTEM_PROMPT",
    "MAIN_ANALYSIS_MEANING",
    "MAIN_ANALYSIS_KEY",
    "OTHER_MEASUREMENTS_KEY",
    "OTHER_MEASUREMENTS_MEANING",
    "AnalysisCheck",
    "D5Facts",
    "FactReport",
    "OtherMeasurement",
    "d5_answers",
    "facts_prompt",
    "read_facts_reply",
    "read_second_look",
    "second_look_prompt",
]

#: The facts call's system turn.
D5_FACTS_SYSTEM_PROMPT = """\
You read reports of randomised trials and report what they say about how the trial was planned.

CRITICAL RULES:
1. Report ONLY what is ACTUALLY PRESENT in the text
2. DO NOT infer, assume or judge: a fact the paper does not state is absent
3. Every fact you mark present needs a quote copied VERBATIM from the paper
4. Return ONLY valid JSON, no explanation"""

#: The second look's system turn.
D5_SECOND_LOOK_SYSTEM_PROMPT = """\
You find passages in reports of randomised trials.

CRITICAL RULES:
1. Quote ONLY passages that are ACTUALLY PRESENT in the text, VERBATIM
2. A passage counts only if it gives the result itself, with its numbers, or points to the \
figure, table or supplement that shows it
3. Return ONLY valid JSON, no explanation"""

#: What each fact means, as the model is told, in :data:`D5_FACTS` order.
D5_FACT_MEANINGS: dict[D5Fact, str] = {
    D5Fact.PLAN_CITED: (
        "The paper cites a trial registration (registry and number), a published protocol, or a "
        "statistical analysis plan (by reference or as a supplement)."
    ),
    D5Fact.PLAN_DATED_EARLY: (
        "The paper places that registration, protocol or plan BEFORE the outcome data could be "
        "seen by group: a registration date before recruitment began or before the trial ended, "
        '"prospectively registered", a protocol published, or an analysis plan finalised, before '
        "unblinding or before the data were analysed."
    ),
    D5Fact.PLAN_LATE: (
        "The paper says the registration or plan was made AFTER recruitment began or after the "
        'data were collected ("retrospectively registered", a registration date after the first '
        "participant was enrolled)."
    ),
    D5Fact.MEASURE_FIXED: (
        "For the outcome assessed, the paper states which measurement was planned in advance: "
        "the instrument or scale and how the outcome is defined, as set in the registration, "
        'protocol or plan, or described as prespecified. A bare "prespecified primary outcome" '
        "that does not name the measurement does not count."
    ),
    D5Fact.TIMEPOINT_FIXED: (
        "For the outcome assessed, the paper states the time point (or points) planned in "
        "advance, as set in the registration, protocol or plan, or described as prespecified."
    ),
    D5Fact.ANALYSIS_FIXED: (
        "For the outcome assessed, the paper states the analysis planned in advance: the "
        "statistical model or test, and the covariates or the handling of missing data, as set "
        "in the registration, protocol or plan, or described as prespecified."
    ),
    D5Fact.SWITCHED: (
        "The paper says the outcome assessed ITSELF differs from the registration or protocol: "
        "added, dropped, moved to or from primary, or defined or timed differently. A change to "
        "another outcome does not count."
    ),
    D5Fact.CHANGE_EXPLAINED: (
        "The paper gives a reason for that change to the outcome assessed that does not depend "
        "on its results (for example a ruling of an ethics committee, or a change made and "
        "stated before unblinding)."
    ),
}

#: The reply's key for the list of other measurements.
OTHER_MEASUREMENTS_KEY = "other_measurements"

#: What that list holds, as the model is told.
OTHER_MEASUREMENTS_MEANING = (
    "Every OTHER measurement of the SAME outcome as the one assessed (the same thing "
    "measured) that the methods, protocol or registration name: the same thing on another "
    "instrument or scale, at another time point, or defined another way. Not the "
    "measurement assessed itself, not other outcomes, not subgroups, not within-group "
    "comparisons."
)

#: The reply's key for the analysis check.
MAIN_ANALYSIS_KEY = "main_analysis"

#: What the analysis check asks, as the model is told.
MAIN_ANALYSIS_MEANING = (
    "main_analysis: the analysis whose result the paper gives for the outcome assessed, set "
    "against the main analysis its methods, protocol or analysis plan name for it. planned: "
    "1-2 passages naming the main analysis (the model or test, its covariates, the analysis "
    "population, the handling of missing data). reported: 1-2 passages giving the result with "
    "the analysis it came from. differs: true only when the analysis reported is NOT the one "
    "named as main (another model, other covariates, another population or handling of missing "
    "data) and the paper gives no reason for the change; false when they match, or when either "
    "is not stated."
)

#: An example item of the measurement list in the reply's shape.
_ITEM_SHAPE = (
    '{"label": "short name, e.g. pain VAS at 6 months", "named": ["passage naming it"], '
    '"result": ["passage giving its result, with numbers, or pointing to its figure or table"]}'
)


def facts_prompt(title: str, paper: str, outcome: ProposedOutcome) -> str:
    """The user turn asking for the facts, the other measurements and the analysis check.

    Args:
        title: The paper's title.
        paper: The text the model reads: the whole paper, or D5's digest.
        outcome: The outcome assessed.
    """
    listed = "\n".join(f"- {f.value}: {D5_FACT_MEANINGS[f]}" for f in D5_FACTS)
    shape = ",\n".join(f'    "{f.value}": {{"present": false, "quotes": []}}' for f in D5_FACTS)
    return "\n\n".join(
        [
            "Report the following about the randomised trial in the paper below, for the outcome "
            "named. Copy every quote VERBATIM from the paper, character for character, without "
            "ellipses or joined sentences.",
            outcome_block(outcome),
            "Facts: for each give present (true or false) and quotes, 1-3 passages that show it, "
            "an empty list when it is absent. Mark a fact present only when the paper itself "
            f"states it.\n{listed}",
            "List: for each item give a short label, named (1-2 passages from the methods, "
            "protocol or registration that name it) and result (1-2 passages from the results, a "
            "table or the abstract that give its result with its numbers, or that point to the "
            "figure, table or supplement showing it; an empty list when the paper gives no result "
            "for it, or only a summary without numbers). An empty list when there is no such "
            f"item.\n- {OTHER_MEASUREMENTS_KEY}: {OTHER_MEASUREMENTS_MEANING}",
            f"Analysis check:\n- {MAIN_ANALYSIS_MEANING}",
            'Respond with JSON in exactly this shape, every key present:\n\n{\n  "facts": {\n'
            + shape
            + "\n  },\n"
            + f'  "{OTHER_MEASUREMENTS_KEY}": [{_ITEM_SHAPE}],\n'
            + f'  "{MAIN_ANALYSIS_KEY}": '
            + '{"differs": false, "planned": ["..."], "reported": ["..."]}\n}',
            f"Paper Title: {title}",
            f"Paper Text:\n{paper}",
        ]
    )


@dataclass
class FactReport:
    """One fact as the model gave it and as the paper and the checks bear it out.

    Attributes:
        claimed: Whether the model said it is present.
        citations: The paper's own spans for its quotes that were found.
        quotes_not_found: Its quotes not located in the paper, verbatim.
        contradicted: Its quotes the paper contradicts, each with the reason.
        quotes_dropped: How many quote members were not usable text.
        verdict: Whether it counts, and if not, why
            (:func:`~bmlib.quality.rob2_d5_checks.fact_verdict`).
    """

    claimed: bool
    citations: list[PicoCitation]
    quotes_not_found: list[str]
    contradicted: list[PicoContradictedQuote]
    quotes_dropped: int
    verdict: FactVerdict

    @property
    def present(self) -> bool:
        """Whether the fact counts."""
        return self.verdict is FactVerdict.PRESENT

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-ready dict."""
        return {
            "claimed": self.claimed,
            "citations": [c.to_dict() for c in self.citations],
            "quotes_not_found": list(self.quotes_not_found),
            "contradicted": [q.to_dict() for q in self.contradicted],
            "quotes_dropped": self.quotes_dropped,
            "verdict": self.verdict.value,
        }


@dataclass
class OtherMeasurement:
    """An other measurement of the outcome the model listed, as the paper bears it out.

    Attributes:
        label: The model's short name for it.
        named: The paper's spans naming it that were found.
        result: The paper's spans giving its result that were found.
        second_look: What a second look found; ``None`` when none was made.
        second_look_error: Why the second look failed, when it did.
    """

    label: str
    named: list[PicoCitation]
    result: list[PicoCitation]
    second_look: list[PicoCitation] | None = None
    second_look_error: str | None = None

    def counts(self) -> bool:
        """Whether a passage naming it was found in the paper."""
        return bool(self.named)

    def reported(self) -> bool:
        """Whether a found result passage, at either look, shows a result."""
        passages = [c.text for c in self.result] + [c.text for c in self.second_look or []]
        return shows_result(passages)

    def needs_second_look(self) -> bool:
        """Whether it counts, shows no result yet, and has had no second look."""
        return self.counts() and not self.reported() and self.second_look is None

    def missing(self) -> bool:
        """Whether it counts and neither look found its result (a failed look does not count)."""
        return self.counts() and not self.reported() and self.second_look is not None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-ready dict."""
        return {
            "label": self.label,
            "named": [c.to_dict() for c in self.named],
            "result": [c.to_dict() for c in self.result],
            "second_look": (
                None if self.second_look is None else [c.to_dict() for c in self.second_look]
            ),
            "second_look_error": self.second_look_error,
        }


@dataclass
class AnalysisCheck:
    """The main analysis the methods name, set against the one reported.

    Attributes:
        differs: Whether the model says they differ with no reason given.
        planned: The paper's spans naming the main analysis that were found.
        reported: The paper's spans giving the result with its analysis that were found.
    """

    differs: bool
    planned: list[PicoCitation]
    reported: list[PicoCitation]

    def counts(self) -> bool:
        """Whether they differ, with a found passage for each side."""
        return self.differs and bool(self.planned) and bool(self.reported)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-ready dict."""
        return {
            "differs": self.differs,
            "planned": [c.to_dict() for c in self.planned],
            "reported": [c.to_dict() for c in self.reported],
        }


@dataclass
class D5Facts:
    """Everything D5 is answered from, for one outcome.

    Attributes:
        outcome: The name of the outcome assessed, which the checks read.
        facts: Every fact of :data:`~bmlib.quality.rob2_d5_checks.D5_FACTS`.
        measurements: The other measurements listed, in the reply's order.
        analysis: The analysis check.
    """

    outcome: str
    facts: dict[D5Fact, FactReport]
    measurements: list[OtherMeasurement] = field(default_factory=list)
    analysis: AnalysisCheck = field(default_factory=lambda: AnalysisCheck(False, [], []))

    def present(self, fact: D5Fact) -> bool:
        """Whether *fact* counts."""
        return self.facts[fact].present

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-ready dict; facts in :data:`D5_FACTS` order."""
        return {
            "outcome": self.outcome,
            "facts": {f.value: self.facts[f].to_dict() for f in D5_FACTS},
            OTHER_MEASUREMENTS_KEY: [m.to_dict() for m in self.measurements],
            MAIN_ANALYSIS_KEY: self.analysis.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> D5Facts:
        """Rebuild from :meth:`to_dict` output, recomputing every verdict.

        Raises:
            ValueError: A field is absent or of the wrong type, or a stored
                verdict is not the one the checks give, naming it.
        """
        data = _object(data, "the facts")
        outcome = _field(data, "outcome", str, "text")
        raw = _field(data, "facts", dict, "an object")
        facts: dict[D5Fact, FactReport] = {}
        for fact in D5_FACTS:
            item = _object(raw.get(fact.value), f"facts.{fact.value}")
            path = f"facts.{fact.value}"
            claimed = _field(item, "claimed", bool, "a boolean", path)
            citations = _citations(item, "citations", path)
            stored = _field(item, "verdict", str, "text", path)
            verdict = fact_verdict(fact, claimed, [c.text for c in citations], outcome)
            if stored != verdict.value:
                raise ValueError(
                    f"{path}.verdict {stored} is not {verdict.value}, which the checks give"
                )
            facts[fact] = FactReport(
                claimed,
                citations,
                list(_field(item, "quotes_not_found", list, "a list", path)),
                [
                    PicoContradictedQuote.from_dict(q)
                    for q in _field(item, "contradicted", list, "a list", path)
                ],
                _field(item, "quotes_dropped", int, "a count", path),
                verdict,
            )
        measurements = []
        for n, item in enumerate(_field(data, OTHER_MEASUREMENTS_KEY, list, "a list")):
            path = f"{OTHER_MEASUREMENTS_KEY}[{n}]"
            item = _object(item, path)
            look = item.get("second_look")
            error = item.get("second_look_error")
            if error is not None and not isinstance(error, str):
                raise ValueError(f"{path}.second_look_error is not text")
            measurements.append(
                OtherMeasurement(
                    _field(item, "label", str, "text", path),
                    _citations(item, "named", path),
                    _citations(item, "result", path),
                    None if look is None else _citations(item, "second_look", path),
                    error,
                )
            )
        check = _object(data.get(MAIN_ANALYSIS_KEY), MAIN_ANALYSIS_KEY)
        analysis = AnalysisCheck(
            _field(check, "differs", bool, "a boolean", MAIN_ANALYSIS_KEY),
            _citations(check, "planned", MAIN_ANALYSIS_KEY),
            _citations(check, "reported", MAIN_ANALYSIS_KEY),
        )
        return cls(outcome, facts, measurements, analysis)


def _object(value: object, what: str) -> dict[str, Any]:
    """*value* as an object, or a ``ValueError`` naming *what*."""
    if not isinstance(value, dict):
        raise ValueError(f"{what} is not an object")
    return value


def _field(data: dict[str, Any], key: str, kind: type, noun: str, path: str = "") -> Any:
    """``data[key]`` of type *kind* (a bool is not an int), or a ``ValueError`` naming it."""
    value = data.get(key)
    if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
        raise ValueError(f"{path + '.' if path else ''}{key} is not {noun}")
    return value


def _citations(data: dict[str, Any], key: str, path: str) -> list[PicoCitation]:
    """``data[key]`` as a list of citations."""
    return [PicoCitation.from_dict(c) for c in _field(data, key, list, "a list", path)]


def read_facts_reply(
    data: dict[str, Any], text: str, outcome: str, min_similarity: float
) -> D5Facts:
    """Read the facts reply, verifying every quote against *text*.

    Args:
        data: The parsed reply: ``facts`` (every fact, ``{"present", "quotes"}``),
            ``other_measurements`` (items ``{"label", "named", "result"}``) and
            ``main_analysis`` (``{"differs", "planned", "reported"}``).
        text: The paper as passed (never a digest); citation offsets index it.
        outcome: The name of the outcome assessed, which the checks read.
        min_similarity: :func:`~bmlib.citations.find_passage`'s threshold.

    Raises:
        ValueError: *min_similarity* is not a number in (0, 1].
        UnreadableReplyError: (a ``ValueError``) The reply or a part of it is
            not the shape asked for; the message names the field
            (``facts.plan_cited.present``).
    """
    similarity = check_min_similarity(min_similarity)
    if not isinstance(data, dict):
        raise UnreadableReplyError("expected an object")
    raw = data.get("facts")
    if not isinstance(raw, dict):
        raise UnreadableReplyError("facts is not an object")
    facts: dict[D5Fact, FactReport] = {}
    for fact in D5_FACTS:
        path = f"facts.{fact.value}"
        item = raw.get(fact.value)
        if not isinstance(item, dict):
            raise UnreadableReplyError(f"{path} is missing or not an object")
        claimed = item.get("present")
        if not isinstance(claimed, bool):
            raise UnreadableReplyError(f"{path}.present is not true or false")
        citations, not_found, contradicted, dropped = verify_quotes(
            item.get("quotes"), text, similarity
        )
        verdict = fact_verdict(fact, claimed, [c.text for c in citations], outcome)
        facts[fact] = FactReport(claimed, citations, not_found, contradicted, dropped, verdict)
    items = data.get(OTHER_MEASUREMENTS_KEY)
    if not isinstance(items, list):
        raise UnreadableReplyError(f"{OTHER_MEASUREMENTS_KEY} is missing or not a list")
    measurements = []
    for n, entry in enumerate(items):
        path = f"{OTHER_MEASUREMENTS_KEY}[{n}]"
        if not isinstance(entry, dict):
            raise UnreadableReplyError(f"{path} is not an object")
        label = entry.get("label")
        if not isinstance(label, str) or not label.strip():
            raise UnreadableReplyError(f"{path}.label is not text")
        named = verify_quotes(entry.get("named"), text, similarity)[0]
        result = verify_quotes(entry.get("result"), text, similarity)[0]
        measurements.append(OtherMeasurement(label, named, result))
    check = data.get(MAIN_ANALYSIS_KEY)
    if not isinstance(check, dict):
        raise UnreadableReplyError(f"{MAIN_ANALYSIS_KEY} is missing or not an object")
    differs = check.get("differs")
    if not isinstance(differs, bool):
        raise UnreadableReplyError(f"{MAIN_ANALYSIS_KEY}.differs is not true or false")
    analysis = AnalysisCheck(
        differs,
        verify_quotes(check.get("planned"), text, similarity)[0],
        verify_quotes(check.get("reported"), text, similarity)[0],
    )
    return D5Facts(outcome, facts, measurements, analysis)


def second_look_prompt(
    title: str, paper: str, outcome: ProposedOutcome, item: OtherMeasurement
) -> str:
    """The user turn asking where *paper* gives *item*'s result.

    Args:
        title: The paper's title.
        paper: The text sent with the facts call (the paper, or D5's digest).
        outcome: The outcome assessed.
        item: The measurement looked for.
    """
    named = " / ".join(c.text for c in item.named)
    return "\n\n".join(
        [
            "Find where the paper below gives the result of this measurement or analysis of the "
            f'outcome, which its methods name: "{item.label}", named as: "{named}".',
            outcome_block(outcome),
            "Quote 1-3 passages, VERBATIM, from the results, a table or the abstract that give its "
            "result with its numbers, or that point to the figure, table or supplement showing "
            "it. If the paper gives no result for it, or only a summary without numbers and "
            "without such a pointer, answer found: false with no quotes.",
            'Respond with JSON in exactly this shape:\n\n{"found": true, "quotes": ["..."]}',
            f"Paper Title: {title}",
            f"Paper Text:\n{paper}",
        ]
    )


def read_second_look(data: dict[str, Any], text: str, min_similarity: float) -> list[PicoCitation]:
    """The second look's passages found in *text*; ``found: false`` is none.

    Raises:
        ValueError: *min_similarity* is not a number in (0, 1].
        UnreadableReplyError: The reply is not an object with ``found`` true or false.
    """
    similarity = check_min_similarity(min_similarity)
    if not isinstance(data, dict) or not isinstance(data.get("found"), bool):
        raise UnreadableReplyError("found is not true or false")
    if not data["found"]:
        return []
    return verify_quotes(data.get("quotes"), text, similarity)[0]


#: The reason a 5.2 PY names: an other measurement missing at both looks.
MISSING_MEASUREMENT = OTHER_MEASUREMENTS_KEY
#: The reason a 5.3 PY names: the analysis reported differs from the main one.
DIFFERING_ANALYSIS = MAIN_ANALYSIS_KEY


def d5_answers(facts: D5Facts) -> dict[str, tuple[Answer, list[str]]]:
    """5.1, 5.2 and 5.3 from *facts*, each with what decided it.

    The evidence ladder of the design's §4.6: N (Y for 5.1) needs a cited plan
    dated before the data by group were seen that fixes every choice; the same
    content stated by the paper alone is PN (PY for 5.1); a late plan, or
    anything less, is NI.  A switch of the outcome assessed without a reason
    independent of its results makes 5.1 N and 5.2 Y; an other measurement
    missing at both looks makes 5.2 PY; the analysis reported differing from
    the main one makes 5.3 PY.  RoB 2's "measured in only one way" N is not
    used: such an outcome without a plan is NI.

    Returns:
        ``{"5.1": (answer, deciding), ...}``; *deciding* lists the wire names
        of the facts that decided it, or ``other_measurements`` /
        ``main_analysis``.
    """

    def present(fact: D5Fact) -> bool:
        return facts.present(fact)

    plan = (
        present(D5Fact.PLAN_CITED)
        and present(D5Fact.PLAN_DATED_EARLY)
        and not present(D5Fact.PLAN_LATE)
    )

    def ladder(fixed: list[D5Fact], low: Answer, on_word: Answer) -> tuple[Answer, list[str]]:
        if all(present(f) for f in fixed):
            if plan:
                return low, [D5Fact.PLAN_CITED.value, D5Fact.PLAN_DATED_EARLY.value] + [
                    f.value for f in fixed
                ]
            if not present(D5Fact.PLAN_LATE):
                return on_word, [f.value for f in fixed]
        return Answer.NI, [f.value for f in (D5Fact.PLAN_LATE, *fixed) if present(f)]

    switched = present(D5Fact.SWITCHED) and not present(D5Fact.CHANGE_EXPLAINED)
    if switched:
        a51 = (Answer.N, [D5Fact.SWITCHED.value])
        a52 = (Answer.Y, [D5Fact.SWITCHED.value])
    else:
        a51 = ladder([D5Fact.MEASURE_FIXED, D5Fact.ANALYSIS_FIXED], Answer.Y, Answer.PY)
        a52 = (
            (Answer.PY, [MISSING_MEASUREMENT])
            if any(m.missing() for m in facts.measurements)
            else ladder([D5Fact.MEASURE_FIXED, D5Fact.TIMEPOINT_FIXED], Answer.N, Answer.PN)
        )
    a53 = (
        (Answer.PY, [DIFFERING_ANALYSIS])
        if facts.analysis.counts()
        else ladder([D5Fact.ANALYSIS_FIXED], Answer.N, Answer.PN)
    )
    return {"5.1": a51, "5.2": a52, "5.3": a53}
