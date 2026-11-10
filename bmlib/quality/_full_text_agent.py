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

"""The base of the agents that put one question to the model over a paper's full text.

The design proposal, the primary-outcome extraction and the RoB 2 agent all
do the same plumbing: refuse a bad stop or window up front, condense a paper
that does not fit once (for a query of their own), ask until the reply reads,
and never send a request once the caller's stop is set.  That lives here once;
each agent keeps its own prompts, its own reader and its own error kinds, by
saying how a failure becomes its exception (:meth:`_FullTextAgent._error`).

Private: the agents are the API.  :class:`PicoAgent` predates this and keeps
its own copy of the same rules.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from enum import Enum
from typing import Any, Protocol, TypeVar

from bmlib.agents.base import BaseAgent, ChatJsonError, ChatJsonTruncatedError, ChatStoppedError
from bmlib.context_processor import ProcessingConfig, ProcessingStatus, ProcessingStoppedError
from bmlib.llm import LLMClient, LLMMessage, LLMResponse
from bmlib.llm.providers.ollama import MAX_NUM_CTX
from bmlib.llm.stream import Stop, stop_reader
from bmlib.llm.utils import http_status
from bmlib.quality._condense import CondensationRefusedError, condense_text, is_retryable_request
from bmlib.quality.cochrane_assessor import DEFAULT_CONDENSE_THRESHOLD_CHARS
from bmlib.quality.pico_agent import (
    _NOT_A_REQUEST_FAILURE,
    _READ_ATTEMPTS,
    PICO_CONDENSE_CONSOLIDATION_PROMPT,
    PICO_CONDENSE_EXTRACTION_PROMPT,
    STOPPED_MESSAGE,
    _check_similarity,
)
from bmlib.templates import TemplateEngine

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

#: How much of the title names the paper in a log line or a condensation label.
LABEL_CHARS = 60

#: Sampling temperature of every full-text agent (Rust's ``DEFAULT_TEMPERATURE``).
DEFAULT_TEMPERATURE = 0.1

#: The reply's output ceiling (Rust's ``DEFAULT_MAX_TOKENS``).
DEFAULT_MAX_TOKENS = 4096

#: ``chat_json``'s attempts per question (Rust's ``DEFAULT_MAX_RETRIES``).
DEFAULT_MAX_RETRIES = 3


class AgentFailure(Enum):
    """The ways a full-text agent's call fails, before an agent names them its own."""

    #: The caller's stop was set.
    STOPPED = "stopped"
    #: The paper did not fit and could not be condensed to fit.
    CONDENSATION_FAILED = "condensation_failed"
    #: The provider failed or refused a request.
    REQUEST_FAILED = "request_failed"
    #: Every reply was unusable or cut off, or named something outside the allowed set.
    UNREADABLE = "unreadable"


class UnreadableReplyError(ValueError):
    """A reply that parsed but cannot be read; asking again may help.

    Its own class so that only this is re-asked: a ``ValueError`` from bmlib's
    own code while reading is a defect and propagates.
    """


def check_min_similarity(min_similarity: object) -> float:
    """Refuse a threshold :func:`~bmlib.citations.find_passage` would refuse.

    Raises:
        ValueError: *min_similarity* is not a number in (0, 1].
    """
    return _check_similarity(min_similarity)


class QueryCondenser(Protocol):
    """Condenses an oversized text for one stated need; the seam the agents share."""

    def condense(
        self, query: str, text: str, label: str, config: ProcessingConfig, *, stage: str
    ) -> tuple[str, list[str], ProcessingStatus]:
        """Return ``(digest, notes, status)`` or raise ``CondensationRefusedError``."""
        ...


class LlmQueryCondenser:
    """The production :class:`QueryCondenser`: the PICO verbatim-passage map-reduce.

    Args:
        agent: The agent whose model, stop and ``num_ctx`` the requests use.
        progress: Handed every progress report of the map-reduce.
    """

    def __init__(self, agent: BaseAgent, progress: Any = None) -> None:
        self._agent = agent
        self._progress = progress

    def condense(
        self, query: str, text: str, label: str, config: ProcessingConfig, *, stage: str
    ) -> tuple[str, list[str], ProcessingStatus]:
        """:func:`~bmlib.quality._condense.condense_text` over *query*."""
        return condense_text(
            self._agent,
            text,
            label,
            config,
            extraction_prompt=PICO_CONDENSE_EXTRACTION_PROMPT,
            consolidation_prompt=PICO_CONDENSE_CONSOLIDATION_PROMPT,
            query=query,
            stage=stage,
            progress_callback=self._progress,
        )


class _FullTextAgent(BaseAgent):
    """An agent that asks one question over a paper, condensing it first if needed.

    Subclasses implement :meth:`_error` and call :meth:`_condense_once` and
    :meth:`_ask_until_readable`.

    Args:
        llm: The client requests go through.
        model: The ``"provider:model"`` string.
        template_engine: Unused; accepted as every agent does.
        temperature: Sampling temperature.
        max_tokens: The reply's output ceiling.
        max_retries: ``chat_json``'s attempts per question.
        condense_config: The budget beyond which text is condensed first.
        num_ctx: The window sent with every request (Ollama); ``None`` sends none.
        stop: Read before every request; once true no request is sent.
        condense_progress: Handed every progress report of a condensation.
        condenser: Condenses an oversized paper; defaults to
            :class:`LlmQueryCondenser` over this agent.
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
        self.max_retries = max_retries
        if num_ctx is not None and (
            isinstance(num_ctx, bool)
            or not isinstance(num_ctx, int)
            or not 0 < num_ctx <= MAX_NUM_CTX
        ):
            raise ValueError(f"num_ctx must be an int in 1..{MAX_NUM_CTX}, got {num_ctx!r}")
        self._num_ctx = num_ctx
        self._stop_requested = stop_reader(stop)
        super().__init__(
            llm=llm,
            model=model,
            template_engine=template_engine,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        self.condense_config = condense_config or ProcessingConfig(
            max_context_chars=DEFAULT_CONDENSE_THRESHOLD_CHARS
        )
        self.condenser: QueryCondenser = condenser or LlmQueryCondenser(self, condense_progress)

    def _error(
        self,
        failure: AgentFailure,
        message: str,
        retryable: bool = False,
        status: int | None = None,
    ) -> Exception:
        """The exception this agent raises for *failure*; each agent says which.

        *retryable* and *status* are set for ``REQUEST_FAILED`` only, derived as
        :class:`~bmlib.quality.pico_agent.PicoAgent` derives them.
        """
        raise NotImplementedError

    def chat(self, messages: list[LLMMessage], **kwargs: Any) -> LLMResponse:
        """:meth:`BaseAgent.chat` with ``num_ctx``, refused once stopped."""
        if self._stop_requested():
            raise ChatStoppedError(STOPPED_MESSAGE)
        if self._num_ctx is not None:
            kwargs["num_ctx"] = self._num_ctx
        return super().chat(messages, **kwargs)

    def _check_provider(self) -> None:
        """Resolve the model's provider before any request (unknown provider, missing SDK)."""
        provider = self.model.split(":", 1)[0] if ":" in self.model else self.llm.default_provider
        self.llm._get_provider(provider or self.llm.default_provider)

    def _raise_if_stopped(self, cause: BaseException | None = None) -> None:
        """Raise the agent's ``STOPPED`` error when the stop is set, chained to *cause*."""
        if self._stop_requested():
            raise self._error(AgentFailure.STOPPED, STOPPED_MESSAGE) from cause

    def _condense_once(self, title: str, text: str, query: str, stage: str) -> str:
        """Condense *text* for *query*, returning the digest.

        A bmlib defect or missing SDK behind a failed condensation propagates
        unwrapped; a stop wins over whatever else went wrong.
        """
        self._raise_if_stopped()
        defect: BaseException | None = None
        try:
            digest, _notes, _status = self.condenser.condense(
                query,
                text,
                title[:LABEL_CHARS],
                self.condense_config,
                stage=stage,
            )
        except ProcessingStoppedError as exc:
            raise self._error(AgentFailure.STOPPED, STOPPED_MESSAGE) from exc
        except CondensationRefusedError as exc:
            self._raise_if_stopped(exc)
            cause = exc.__cause__
            if isinstance(cause, _NOT_A_REQUEST_FAILURE):
                defect = cause
            else:
                message = (
                    f"a {len(text)}-character text could not be condensed to fit "
                    f"{self.condense_config.max_context_chars} characters: {exc}"
                )
                if cause is None:
                    raise self._error(AgentFailure.CONDENSATION_FAILED, message) from exc
                raise self._error(
                    AgentFailure.REQUEST_FAILED,
                    message,
                    is_retryable_request(cause),
                    http_status(cause),
                ) from exc
        if defect is not None:
            raise defect
        # A run stopped part-way can finish with a partial digest, which is no digest.
        self._raise_if_stopped()
        return digest

    def _ask_until_readable(
        self,
        system_prompt: str,
        prompt: str,
        read: Callable[[dict[str, Any]], _T],
        label: str,
    ) -> _T:
        """Send the question and read the reply, asking again once if it is unreadable.

        *read* raises :class:`UnreadableReplyError` for a reply it cannot use.
        """
        messages = [self.system_msg(system_prompt), self.user_msg(prompt)]
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
                raise self._error(AgentFailure.STOPPED, STOPPED_MESSAGE) from exc
            except _NOT_A_REQUEST_FAILURE:
                raise
            except Exception as exc:
                self._raise_if_stopped(exc)
                if isinstance(exc, ChatJsonTruncatedError | ChatJsonError):
                    raise self._error(AgentFailure.UNREADABLE, str(exc)) from exc
                raise self._error(
                    AgentFailure.REQUEST_FAILED,
                    str(exc),
                    is_retryable_request(exc),
                    http_status(exc),
                ) from exc
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
        raise self._error(
            AgentFailure.UNREADABLE,
            f"{label}: {_READ_ATTEMPTS} replies could not be read: {problem}",
        ) from problem
