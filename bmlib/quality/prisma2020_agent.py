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

"""The PRISMA 2020 agent: is the paper a systematic review, and how fully does
it report each checklist item of a part?

Three calls, which the caller drives so it can store each part as it finishes
(StudyAnalyzer's PRISMA tab, design ``2026-11-18-prisma2020-tab-design.md``):

* :meth:`Prisma2020Agent.check_suitability` — a systematic review or
  meta-analysis (a variant included) is suitable.  The caller passes the
  abstract or the whole paper; nothing is cut (bmlibrarian's
  ``full_text[:2000]`` is not ported).
* :meth:`Prisma2020Agent.prepare_part` — the paper for one part, whole when it
  fits, else condensed with :func:`~bmlib.quality.prisma2020_parts.part_query`
  of the part's items.
* :meth:`Prisma2020Agent.assess_part` — the model judges each item and quotes
  the paper; :func:`~bmlib.quality.prisma2020_models.read_part_reply` locates
  every quote in the **paper**, never the digest, and shows only what a quote
  backs.

A condensation runs with ``continue_on_error=False``, so a failed batch fails
it, and a digest whose run did not complete is refused: no call is ever judged
from a partial digest.  ``num_ctx`` reaches every request, the condenser's
included, and a ``stop`` ends a run before its next request, as in
:class:`~bmlib.quality.pico_agent.PicoAgent`.

The checklist wording the prompts carry is the PRISMA 2020 checklist's (Page MJ
et al., *BMJ* 2021;372:n71, CC BY 4.0).

Rust's ``rust/bmlib/src/quality/prisma2020_agent.rs`` is the same, its prompts
byte-identical; ``rust/oracle/dump_prisma2020_agent.py`` holds the two together.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, TypeVar

from bmlib.agents.base import ChatJsonError, ChatJsonTruncatedError, ChatStoppedError
from bmlib.context_processor import ProcessingConfig, ProcessingStatus, ProcessingStoppedError
from bmlib.llm import LLMClient
from bmlib.llm.stream import Stop
from bmlib.llm.utils import http_status
from bmlib.quality._condense import (
    CondensationRefusedError,
    condense_text,
    is_retryable_request,
)
from bmlib.quality._full_text_agent import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_MAX_TOKENS,
    DEFAULT_TEMPERATURE,
    LABEL_CHARS,
    AgentFailure,
    QueryCondenser,
    UnreadableReplyError,
    _FullTextAgent,
    check_min_similarity,
)
from bmlib.quality.pico_agent import _NOT_A_REQUEST_FAILURE, _READ_ATTEMPTS, STOPPED_MESSAGE
from bmlib.quality.pico_models import PicoPrepared
from bmlib.quality.prisma2020_checklist import ChecklistItem, item
from bmlib.quality.prisma2020_models import (
    PartAssessment,
    Suitability,
    read_part_reply,
    read_suitability,
)
from bmlib.quality.prisma2020_parts import PartPlan, part_query
from bmlib.templates import TemplateEngine

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


@dataclass(frozen=True)
class PrismaPrepared:
    """A paper made ready for one part: the text whole, or its digest.

    Built by :meth:`Prisma2020Agent.prepare_part`.  It keeps the paper as
    passed, since quotes are located in the paper and never the digest.  A
    digest records the items it was condensed for, and
    :meth:`Prisma2020Agent.assess_part` refuses it for any others (design 6.5:
    a key item re-checked alone needs its own digest); the whole paper serves
    any items.  Rust's ``PrismaPrepared`` is the same.

    Attributes:
        paper: The prepared paper, checked as
            :class:`~bmlib.quality.pico_models.PicoPrepared` checks it.
        condensed_for: The item ids the digest was condensed for, in the order
            given; empty when the text is sent whole.
    """

    paper: PicoPrepared
    condensed_for: tuple[str, ...] = ()

    @property
    def title(self) -> str:
        """The title, stripped."""
        return self.paper.title

    @property
    def text(self) -> str:
        """The paper as passed, unstripped: citation offsets index it."""
        return self.paper.text

    @property
    def sent(self) -> str:
        """What the model is sent: the digest, or the stripped text."""
        return self.paper.sent

    @property
    def condensed_from_chars(self) -> int | None:
        """The stripped text's length when it was condensed; ``None`` when not."""
        return self.paper.condensed_from_chars

    @property
    def condensation_status(self) -> str | None:
        """The condensation's status (always ``"completed"``), when condensed."""
        return self.paper.condensation_status

    @property
    def notes(self) -> tuple[str, ...]:
        """Notes on the condensation, carried into the result."""
        return self.paper.notes


# --- Prompts (byte-identical in the Rust port) ---

PRISMA_SYSTEM_PROMPT = """\
You are an expert in systematic review methodology and the PRISMA 2020 reporting guideline.

CRITICAL RULES:
1. Judge ONLY what is ACTUALLY PRESENT in the text
2. DO NOT assume, infer, or fabricate anything the text does not state
3. Judge THIS review's own reporting, not the studies it includes
4. Return ONLY valid JSON, no explanation; use null, never NaN or Infinity, for a number you \
cannot give"""


SUITABILITY_TASK_PROMPT = """\
Determine whether the document below is a SYSTEMATIC REVIEW or META-ANALYSIS, which makes it \
suitable for assessment against the PRISMA 2020 reporting checklist.

A document is SUITABLE if it is:
- A systematic review (a systematic search, with stated methods to identify, select and appraise \
studies)
- A meta-analysis (a statistical synthesis of the results of several studies)
- A systematic review with a meta-analysis
- One of these kinds of systematic review: a scoping review, a network meta-analysis, an umbrella \
review (a review of reviews), a rapid review or a living review

A document is NOT SUITABLE if it is:
- Primary research (an RCT, a cohort study, a case-control study, ...)
- A narrative review or literature review without systematic methods
- A protocol for a review, with no results yet
- A clinical guideline
- An editorial, commentary, or opinion piece
- A case report or case series

KEY INDICATORS of a systematic review:
- It describes a systematic search (databases, search terms, dates)
- It states criteria for selecting studies
- It assesses the risk of bias or quality of the included studies
- It may include a PRISMA flow diagram or cite PRISMA
- Its title often says "systematic review" or "meta-analysis"

Assess:
1. is_systematic_review: is this a systematic review (any of the kinds above)?
2. is_meta_analysis: does it include a statistical meta-analysis?
3. document_type: the type of document (e.g. "systematic review with meta-analysis", "scoping \
review", "narrative review", "RCT")
4. variant: "scoping_review", "network_meta_analysis", "umbrella_review", "rapid_review" or \
"living_review" when it is one of these kinds, otherwise null
5. rationale: a brief explanation (2-3 sentences)
6. confidence: how confident you are in this assessment (0.0-1.0)

Do not take a document for a systematic review just because it reviews literature: look for the \
methods above. If uncertain, lean towards NOT SUITABLE and say why in the rationale."""


SUITABILITY_RESPONSE_FORMAT = """\
Respond with JSON in exactly this shape:

{
    "is_systematic_review": true,
    "is_meta_analysis": true,
    "document_type": "systematic review with meta-analysis",
    "variant": null,
    "rationale": "The review searched MEDLINE and Embase, stated its inclusion criteria and \
pooled 12 trials in a random-effects meta-analysis.",
    "confidence": 0.9
}"""


PART_TASK_PROMPT = """\
Judge how fully the systematic review below reports each of these PRISMA 2020 checklist items.

{items}

For EACH item give:
- id: the item's id exactly as listed (for example "13d")
- judgement: one of "reported", "partly_reported", "not_reported", "reported_elsewhere" or \
"not_applicable", as the item's guidance defines them
- explanation: 1-3 sentences saying what the paper reports for the item, or what is missing
- quotes: 1-3 passages copied VERBATIM from the paper that show what you judged. Copy them \
character for character; do not paraphrase, shorten with ellipses, or join sentences.

STRICT EVIDENCE RULES:
- Score only what is actually present in the paper; do not infer what it does not say
- Every judgement but "not_reported" needs a quote; one with no quote found in the paper is shown \
as "not_reported"
- "reported_elsewhere": quote the paper's pointer to where the item is reported (a supplement, a \
protocol, a registry record)
- "not_applicable": only where the item's guidance allows it, quoting the passage that shows why
- Be strict: an item reported only in part is "partly_reported", not "reported\""""


PART_RESPONSE_FORMAT = """\
Respond with JSON in exactly this shape, one entry for each item listed:

{
    "items": [
        {
            "id": "7",
            "judgement": "partly_reported",
            "explanation": "The MEDLINE strategy is given in full; the strategies for the other \
databases are not.",
            "quotes": ["a passage copied verbatim from the paper"]
        }
    ]
}"""


SUITABILITY_CONDENSE_QUERY = """\
Everything needed to tell whether the document is a systematic review or meta-analysis: its title \
and stated design; how studies were searched for (databases, search terms, dates); the criteria \
for selecting studies; any assessment of the included studies' risk of bias or quality; any \
statistical pooling of results; and any mention of PRISMA, a protocol or a registration."""


PRISMA_CONDENSE_EXTRACTION_PROMPT = """\
Extract VERBATIM every passage of this paper that bears on the following.

Needed: {query}

Paper section:
{content}

INSTRUCTIONS:
- Copy the passages character for character; do not paraphrase or shorten them, since each will \
be checked against the paper word for word
- Keep numbers, dates, database names, search strings, registration numbers and pointers to \
supplements exactly
- Keep a passage that says something was not done, or is reported elsewhere
- Say nothing about what the text does not report
- Return plain text

Extracted passages:"""


PRISMA_CONDENSE_CONSOLIDATION_PROMPT = """\
Merge these extracted passages into one collection of passages.

Needed: {query}

Extracted passages:
{content}

INSTRUCTIONS:
- Keep every passage VERBATIM, character for character; drop only exact duplicates
- Preserve numbers, dates, database names, search strings, registration numbers and pointers to \
supplements exactly
- Return plain text

Consolidated passages:"""


#: Names the suitability check in a degraded condensation's note.
_SUITABILITY_STAGE = "the suitability check"

#: Names a part's assessment in a degraded condensation's note.
_PART_STAGE = "the assessment of PRISMA 2020 items"


def _part_task(rows: Sequence[ChecklistItem]) -> str:
    """:data:`PART_TASK_PROMPT` with its ``{items}`` filled in."""
    items = "\n\n".join(
        f"Item {row.id}: {row.name}\nChecklist wording: {row.wording}\nGuidance:\n{row.guidance}"
        for row in rows
    )
    # ``replace``, not ``format``: guidance text is data.
    return PART_TASK_PROMPT.replace("{items}", items)


def _wrapped(task: str, response_format: str, title: str, text: str) -> str:
    """The user turn: the task, the paper and the reply's format, as PICO's.

    Concatenated, never ``str.format``ted: braces in a paper are text.
    """
    return (
        task + "\n\nPaper Title: " + title + "\n\nPaper Text:\n" + text + "\n\n" + response_format
    )


def part_prompt_chars(item_ids: Sequence[str]) -> int:
    """The characters of the longest prompt a part of *item_ids* sends.

    Without the paper and its title: the system prompt and the longest of the
    part's question (as it is wrapped) and its two condensation prompts.  A
    caller subtracts it from the window when sizing the paper's budget.  Ids
    not on the checklist add nothing.
    """
    rows = [row for row in (item(item_id) for item_id in item_ids) if row is not None]
    query = part_query(rows)

    def condense(template: str) -> str:
        return template.replace("{query}", query).replace("{content}", "")

    prompts = (
        _wrapped(_part_task(rows), PART_RESPONSE_FORMAT, "", ""),
        condense(PRISMA_CONDENSE_EXTRACTION_PROMPT),
        condense(PRISMA_CONDENSE_CONSOLIDATION_PROMPT),
    )
    return len(PRISMA_SYSTEM_PROMPT) + max(len(prompt) for prompt in prompts)


def _checklist_rows(item_ids: Sequence[str]) -> list[ChecklistItem]:
    """The checklist rows of *item_ids*.

    Raises:
        ValueError: *item_ids* is a string, empty, repeats an id, or names an
            id not on the checklist: a caller's mistake, refused before any
            request (Rust's ``PrismaError::InvalidItems``).
    """
    if isinstance(item_ids, str):
        raise ValueError(f"item ids are a list of ids, not the string {item_ids!r}")
    if not item_ids:
        raise ValueError("a part needs at least one PRISMA 2020 item")
    rows: list[ChecklistItem] = []
    for item_id in item_ids:
        row = item(item_id)
        if row is None:
            raise ValueError(f"item {item_id!r} is not on the PRISMA 2020 checklist")
        if row in rows:
            raise ValueError(f"item {item_id!r} is asked twice")
        rows.append(row)
    return rows


class PrismaErrorKind(Enum):
    """Why a PRISMA 2020 call failed: :class:`~bmlib.quality.pico_models.PicoErrorKind`'s kinds.

    Rust's ``PrismaError`` adds ``invalid_items`` (here a ``ValueError``) and
    ``parts`` (here :class:`~bmlib.quality.prisma2020_parts.PartsError` itself).
    """

    #: The title and text were both empty, so no model call was made.
    EMPTY_INPUT = "empty_input"
    #: The provider was unreachable or refused the request, a condensation's
    #: included (#616); :attr:`PrismaError.retryable` says whether a retry may help.
    REQUEST_FAILED = "request_failed"
    #: The reply hit the output ceiling: split the part, or raise ``max_tokens``.
    TRUNCATED = "truncated"
    #: Every reply was empty, did not parse, or could not be read.
    UNUSABLE_REPLY = "unusable_reply"
    #: The paper did not fit and no digest that fits came of condensing it (a
    #: failed run, an empty or oversized digest, or a run that did not
    #: complete), though no request failed it.
    CONDENSATION_FAILED = "condensation_failed"
    #: The caller's stop was set: the call ended before its next request.
    STOPPED = "stopped"


class PrismaError(Exception):
    """A PRISMA 2020 call that could not produce a result.

    Attributes:
        kind: Why, as a :class:`PrismaErrorKind`.
        retryable: Whether sending the request again may succeed; set for
            ``REQUEST_FAILED`` only, as :class:`~bmlib.quality.pico_models.PicoError`'s.
        status: The HTTP status a ``REQUEST_FAILED`` request was answered
            with, ``None`` where no HTTP answer came, and for every other kind.

    Raises:
        ValueError: ``retryable`` or ``status`` given for a kind other than
            ``REQUEST_FAILED``, which Rust's ``PrismaError`` cannot express.
    """

    def __init__(
        self,
        kind: PrismaErrorKind,
        message: str,
        retryable: bool = False,
        status: int | None = None,
    ) -> None:
        if kind is not PrismaErrorKind.REQUEST_FAILED and (retryable or status is not None):
            raise ValueError(
                f"only a request_failed PrismaError carries retryable or status, not {kind.value}"
            )
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable
        self.status = status

    def __reduce__(self) -> tuple[Any, ...]:
        # Exception's default rebuilds with ``cls(*self.args)``, which lacks ``kind``.
        return (type(self), (self.kind, str(self), self.retryable, self.status))


def _request_error(exc: Exception) -> PrismaError:
    """The ``PrismaError`` a failed ``chat_json`` call is reported as."""
    if isinstance(exc, ChatJsonTruncatedError):
        return PrismaError(PrismaErrorKind.TRUNCATED, str(exc))
    if isinstance(exc, ChatJsonError):
        return PrismaError(PrismaErrorKind.UNUSABLE_REPLY, str(exc))
    return PrismaError(
        PrismaErrorKind.REQUEST_FAILED, str(exc), is_retryable_request(exc), http_status(exc)
    )


class PrismaCondenser:
    """The production :class:`~bmlib.quality._full_text_agent.QueryCondenser` here.

    :func:`~bmlib.quality._condense.condense_text` over the PRISMA condensation
    prompts, for each call's query.

    Args:
        agent: The agent whose model, stop and ``num_ctx`` the requests use.
        progress: Handed every progress report of the map-reduce (#609).
    """

    def __init__(self, agent: Prisma2020Agent, progress: Any = None) -> None:
        self._agent = agent
        self._progress = progress

    def condense(
        self, query: str, text: str, label: str, config: ProcessingConfig, *, stage: str
    ) -> tuple[str, list[str], ProcessingStatus]:
        """Condense *text* for *query*; ``condense_text``'s result and errors."""
        return condense_text(
            self._agent,
            text,
            label,
            config,
            extraction_prompt=PRISMA_CONDENSE_EXTRACTION_PROMPT,
            consolidation_prompt=PRISMA_CONDENSE_CONSOLIDATION_PROMPT,
            query=query,
            stage=stage,
            progress_callback=self._progress,
        )


class Prisma2020Agent(_FullTextAgent):
    """Judges a systematic review against the PRISMA 2020 checklist, a part at a time.

    Every judgement is backed by passages of the paper.  Each call returns its
    result or raises :class:`PrismaError`, whose ``kind`` says why; a
    caller's mistake (bad item ids, a ``min_similarity`` outside (0, 1], a
    paper prepared for another budget) is a ``ValueError`` before any request,
    and what no retry can cure (a bmlib defect, a missing SDK) propagates
    unwrapped, as in :class:`~bmlib.quality.pico_agent.PicoAgent`.

    Args:
        llm: The client requests go through.
        model: The ``"provider:model"`` string.
        template_engine: Unused; accepted as every agent does.
        temperature: Sampling temperature.
        max_tokens: The reply's output ceiling; the full-text agents' 16384,
            since a part's reply is long.
        condense_config: The budget beyond which text is condensed first.  Its
            ``continue_on_error`` is set to false for every condensation.
        num_ctx: The window sent with every request (Ollama), the condenser's
            included; ``None`` sends none.
        stop: Read before every request, the condenser's included; once true
            the call under way raises ``STOPPED``, also when it was set during
            a request that failed.
        condense_progress: Handed every progress report of a condensation.
        condenser: Condenses an oversized paper for a query; defaults to
            :class:`PrismaCondenser` over this agent.
        max_retries: ``chat_json``'s attempts per question.
    """

    def __init__(
        self,
        llm: LLMClient,
        model: str,
        template_engine: TemplateEngine | None = None,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        condense_config: ProcessingConfig | None = None,
        num_ctx: int | None = None,
        stop: Stop | None = None,
        condense_progress: Any = None,
        condenser: QueryCondenser | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        super().__init__(
            llm=llm,
            model=model,
            template_engine=template_engine,
            temperature=temperature,
            max_tokens=max_tokens,
            condense_config=condense_config,
            num_ctx=num_ctx,
            stop=stop,
            condense_progress=condense_progress,
            condenser=condenser,
            max_retries=max_retries,
        )
        if condenser is None:
            self.condenser = PrismaCondenser(self, condense_progress)

    def _error(
        self,
        failure: AgentFailure,
        message: str,
        retryable: bool = False,
        status: int | None = None,
    ) -> Exception:
        """The :class:`PrismaError` for a shared step's *failure*."""
        kind = {
            AgentFailure.STOPPED: PrismaErrorKind.STOPPED,
            AgentFailure.CONDENSATION_FAILED: PrismaErrorKind.CONDENSATION_FAILED,
            AgentFailure.REQUEST_FAILED: PrismaErrorKind.REQUEST_FAILED,
            AgentFailure.UNREADABLE: PrismaErrorKind.UNUSABLE_REPLY,
        }[failure]
        return PrismaError(kind, message, retryable, status)

    def check_suitability(self, title: str | None, text: str | None) -> Suitability:
        """Say whether the paper is a systematic review PRISMA 2020 applies to.

        Args:
            title: The paper's title.
            text: The abstract or the whole paper.  Over the budget it is
                condensed with :data:`SUITABILITY_CONDENSE_QUERY`, never cut.

        Raises:
            PrismaError: Naming why no answer could be made.
        """
        prepared = self._prepare(title, text, SUITABILITY_CONDENSE_QUERY, _SUITABILITY_STAGE)
        suitability = self._ask(
            SUITABILITY_TASK_PROMPT,
            SUITABILITY_RESPONSE_FORMAT,
            prepared,
            read_suitability,
            f"PRISMA 2020 suitability check of {prepared.title[:LABEL_CHARS]!r}",
        )
        suitability.condensed_from_chars = prepared.condensed_from_chars
        suitability.condensation_status = prepared.condensation_status
        suitability.notes = list(prepared.notes)
        return suitability

    def prepare_part(
        self, title: str | None, text: str | None, item_ids: Sequence[str]
    ) -> PrismaPrepared:
        """Make the paper ready for the part of *item_ids*.

        The whole text when it fits ``max_context_chars``, else a digest made
        with :func:`~bmlib.quality.prisma2020_parts.part_query` of those items.
        Never cut.

        Raises:
            ValueError: *item_ids* are none, repeated or not on the checklist,
                before anything else.
            PrismaError: ``EMPTY_INPUT``, ``CONDENSATION_FAILED`` (a digest
                whose run did not complete included), ``REQUEST_FAILED`` (a
                condensation request, #616) or ``STOPPED``.
        """
        rows = _checklist_rows(item_ids)
        paper = self._prepare(title, text, part_query(rows), _PART_STAGE)
        condensed_for = tuple(row.id for row in rows) if paper.digest is not None else ()
        return PrismaPrepared(paper, condensed_for)

    def assess_part(
        self, prepared: PrismaPrepared, item_ids: Sequence[str], *, min_similarity: float
    ) -> PartAssessment:
        """Judge the part of *item_ids* over *prepared*.

        Every quote is located in ``prepared.text``, the paper as passed, never
        the digest; citation offsets are code points into it.  An item the
        reply leaves out is listed in ``missing``, for the caller to ask again.

        Raises:
            ValueError: *min_similarity* is not a number in (0, 1], *item_ids*
                are none, repeated or not on the checklist, *prepared* is a
                digest condensed for other items (Rust's ``InvalidItems``), or
                it was made for another budget, before any request.
            PrismaError: Naming why no assessment could be made.
        """
        similarity = check_min_similarity(min_similarity)
        rows = _checklist_rows(item_ids)
        ids = [row.id for row in rows]
        # A digest gathered for other items may lack what these need, so a
        # re-check of one item must not reuse its part's (design 6.5).
        if prepared.paper.digest is not None and prepared.condensed_for != tuple(ids):
            raise ValueError(
                f"the prepared paper was condensed for items {list(prepared.condensed_for)}, "
                f"not {ids}: prepare it for these items"
            )
        part = self._ask(
            _part_task(rows),
            PART_RESPONSE_FORMAT,
            prepared.paper,
            lambda data: read_part_reply(data, ids, prepared.text, similarity),
            f"PRISMA 2020 part {PartPlan(tuple(ids)).label()} of {prepared.title[:LABEL_CHARS]!r}",
        )
        part.condensed_from_chars = prepared.condensed_from_chars
        part.condensation_status = prepared.condensation_status
        part.notes = list(prepared.notes)
        return part

    def _prepare(self, title: str | None, text: str | None, query: str, stage: str) -> PicoPrepared:
        """Strip, refuse an empty input, and condense an oversized one for *query*.

        A digest whose run did not complete is refused.  A bmlib defect or a
        missing SDK behind a failed condensation propagates unwrapped; a stop
        wins over whatever else went wrong.
        """
        # Quotes are located in the text as passed, unstripped, so an offset
        # indexes the string the caller will highlight.
        original = text or ""
        title = (title or "").strip()
        text = original.strip()
        if not title and not text:
            raise PrismaError(PrismaErrorKind.EMPTY_INPUT, "both the title and the text are empty")
        self._check_provider()
        if len(text) <= self.condense_config.max_context_chars:
            return PicoPrepared(title, original)

        self._raise_if_stopped()
        # A failed batch fails the condensation (design 6.3).
        config = dataclasses.replace(self.condense_config, continue_on_error=False)
        defect: BaseException | None = None
        try:
            digest, notes, status = self.condenser.condense(
                query, text, title[:LABEL_CHARS], config, stage=stage
            )
        except ProcessingStoppedError as exc:
            raise PrismaError(PrismaErrorKind.STOPPED, STOPPED_MESSAGE) from exc
        except CondensationRefusedError as exc:
            self._raise_if_stopped(exc)
            cause = exc.__cause__
            if isinstance(cause, _NOT_A_REQUEST_FAILURE):
                defect = cause
            else:
                message = (
                    f"a {len(text)}-character text could not be condensed to fit "
                    f"{config.max_context_chars} characters: {exc}"
                )
                if cause is None:
                    raise PrismaError(PrismaErrorKind.CONDENSATION_FAILED, message) from exc
                raise PrismaError(
                    PrismaErrorKind.REQUEST_FAILED,
                    message,
                    is_retryable_request(cause),
                    http_status(cause),
                ) from exc
        if defect is not None:
            # Raised outside the handler, as PicoAgent's, so it is not chained
            # to its own effect.
            raise defect
        # A run stopped part-way can finish with a partial digest, which is no digest.
        self._raise_if_stopped()
        if status is not ProcessingStatus.COMPLETED:
            raise PrismaError(
                PrismaErrorKind.CONDENSATION_FAILED,
                f"a {len(text)}-character text was condensed, but the condensation finished "
                f"with status {status.value}, so the digest may be incomplete; nothing is "
                "judged from it",
            )
        return PicoPrepared(title, original, digest, status.value, tuple(notes))

    def _sent(self, prepared: PicoPrepared) -> str:
        """What *prepared* sends, refused unless this agent would have made it.

        Raises:
            ValueError: *prepared* sends more than this agent's budget, or was
                condensed though this agent would send its text whole.
        """
        budget = self.condense_config.max_context_chars
        sent = prepared.sent
        if len(sent) > budget:
            raise ValueError(
                f"the prepared text is {len(sent)} characters, over this agent's "
                f"{budget}-character budget: prepare it with this agent"
            )
        whole = prepared.condensed_from_chars
        if whole is not None and whole <= budget:
            raise ValueError(
                f"the prepared text was condensed from {whole} characters, which fit "
                f"this agent's {budget}-character budget whole: prepare it with this agent"
            )
        return sent

    def _ask(
        self,
        task: str,
        response_format: str,
        prepared: PicoPrepared,
        read: Callable[[dict[str, Any]], _T],
        label: str,
    ) -> _T:
        """Send one question over what *prepared* sends, re-asking an unreadable reply once."""
        self._check_provider()
        prompt = _wrapped(task, response_format, prepared.title, self._sent(prepared))
        messages = [self.system_msg(PRISMA_SYSTEM_PROMPT), self.user_msg(prompt)]
        problem: UnreadableReplyError | None = None
        for attempt in range(_READ_ATTEMPTS):
            self._raise_if_stopped()
            try:
                data = self.chat_json(
                    messages,
                    max_retries=self.max_retries,
                    retry_context=label,
                    require_dict=True,
                )
            except ChatStoppedError as exc:
                raise PrismaError(PrismaErrorKind.STOPPED, STOPPED_MESSAGE) from exc
            except _NOT_A_REQUEST_FAILURE:
                raise
            except Exception as exc:
                # A stop set during the call wins over whatever it raised.
                self._raise_if_stopped(exc)
                raise _request_error(exc) from exc
            try:
                return read(data)
            except UnreadableReplyError as exc:
                problem = exc
                logger.warning(
                    "Unusable reply for %s (attempt %d/%d): %s",
                    label,
                    attempt + 1,
                    _READ_ATTEMPTS,
                    exc,
                )
        raise PrismaError(
            PrismaErrorKind.UNUSABLE_REPLY,
            f"{label}: {_READ_ATTEMPTS} replies could not be read: {problem}",
        ) from problem
