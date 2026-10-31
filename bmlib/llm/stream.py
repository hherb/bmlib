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

"""Reading a streamed chat answer, one event at a time.

A streamed answer arrives as a sequence of events, each a JSON object whose
shape depends on the protocol. :class:`StreamState` reads them in order,
hands back the pieces of text and reasoning each one carries
(:class:`StreamDelta`), and builds the final :class:`LLMResponse` once the
stream ends. It is pure: the providers feed it the events their SDKs yield,
and the Rust port (``bmlib::llm::stream``) feeds it the events it parses off
the wire. Both are held to ``tests/fixtures/chat_stream.json``.

Three protocols:

* ``"anthropic"``: Anthropic's Messages stream (``message_start``,
  ``content_block_*``, ``message_delta``, ``message_stop``).
* ``"openai"``: OpenAI-compatible ``chat.completion.chunk`` objects.
* ``"ollama"``: Ollama's native ``/api/chat`` chunks, one JSON object per
  line, which both clients read.

**A stream that ends before its end marker is an error**, never a short
answer: a dropped connection would otherwise read as a model that stopped
early.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypeVar, get_args

from bmlib.llm.data_types import LLMResponse

StreamProtocol = Literal["anthropic", "openai", "ollama"]
"""The stream shapes :class:`StreamState` reads."""

DeltaKind = Literal["text", "thinking"]
"""What a delta adds to: the answer, or the model's separated reasoning."""

STREAM_PROTOCOLS: tuple[str, ...] = get_args(StreamProtocol)
"""Every protocol :class:`StreamState` accepts."""

CANCELLED_STOP_REASON = "cancelled"
"""The ``stop_reason`` of a stream the caller stopped from ``on_delta``."""

STREAM_USAGE_PROVIDERS: tuple[str, ...] = ("openai", "deepseek", "ollama")
"""OpenAI-compatible providers asked to report usage on a stream.

An OpenAI-compatible server reports token usage on a stream only when the
request carries ``stream_options: {"include_usage": true}``, and some servers
refuse that field as unknown. So it is sent only to the providers listed
here, whose OpenAI-compatible APIs document it. Python's Ollama provider
speaks the native API, which reports usage unasked, so the ``"ollama"`` entry
matters only to the Rust port, which keeps the same list.
"""

THINKING_BLOCK_SEPARATOR = "\n\n"
"""What joins two of Anthropic's reasoning blocks, as ``chat()`` joins them."""


class StreamError(RuntimeError):
    """A stream that failed or ended before the answer was complete.

    Raised for an ``error`` event or object in the stream (the server's own
    message is quoted), for a stream that broke off after it opened (the
    providers wrap their SDK's exception, kept as ``__cause__``), and for a
    stream that ended before its end marker. The deltas already handed to
    ``on_delta`` stay valid as what arrived, but the answer is not complete.
    A request refused before the stream opened raises what :meth:`chat`
    would.
    """


class _Closable(Protocol):
    def close(self) -> None: ...


_StreamT = TypeVar("_StreamT", bound=_Closable)


@contextlib.contextmanager
def closing_stream(stream: _StreamT) -> Iterator[_StreamT]:
    """Close *stream* on the way out, without hiding why the way out was taken.

    A plain ``finally: stream.close()`` lets a ``close()`` that raises replace
    the :class:`StreamError` or ``on_delta`` exception already propagating.
    So on that path a failing ``close()`` is suppressed; after a complete
    answer it raises, as nothing else would report it.
    """
    try:
        yield stream
    except BaseException:
        with contextlib.suppress(Exception):
            stream.close()
        raise
    stream.close()


def refuse_tools(kwargs: Mapping[str, object]) -> None:
    """Refuse a streamed request that carries tools.

    A stream does not carry tool calls back, so tools sent with it would be
    dropped silently and the caller would read prose as the model choosing
    not to call them. Every built-in provider's ``chat_stream`` calls this
    before sending. Tools that reach the server some other way (``extra_body``)
    are caught when a tool call arrives, as a :class:`StreamError`.

    Raises:
        NotImplementedError: If ``kwargs["tools"]`` is set.
    """
    if kwargs.get("tools") is not None:
        raise NotImplementedError(
            "chat_stream does not carry tool calls; use chat() for a request with tools"
        )


def sdk_events(
    stream: Iterable[Any], dump: Callable[[Any], Mapping[str, Any]]
) -> Iterator[Mapping[str, Any]]:
    """Yield an SDK stream's events as dicts, its failures as :class:`StreamError`.

    The SDKs raise their own exceptions for an error event or a broken
    connection mid-stream; wrapping them here gives a caller one exception
    for "the stream failed after it opened", as the Rust port's
    ``ChatError::Stream`` is. Only the SDK's own iteration is wrapped: an
    exception *dump* raises is bmlib's and propagates unchanged, as does one
    ``on_delta`` raises, which is outside this generator.

    Args:
        stream: The SDK's stream object.
        dump: Turns one SDK event into its JSON-shaped dict.

    Raises:
        StreamError: Whatever the SDK raised while streaming, as its cause.
    """
    iterator = iter(stream)
    while True:
        try:
            event = next(iterator)
        except StopIteration:
            return
        except Exception as exc:
            raise StreamError(f"the stream failed: {exc}") from exc
        yield dump(event)


@dataclass(frozen=True)
class StreamDelta:
    """One piece of a streamed answer.

    Attributes:
        kind: ``"text"`` for the answer itself, ``"thinking"`` for the
            model's separated reasoning trace.
        text: The piece, never empty.
    """

    kind: DeltaKind
    text: str

    def __post_init__(self) -> None:
        if self.kind not in get_args(DeltaKind):
            raise ValueError(f"unknown delta kind {self.kind!r}")
        if not self.text:
            raise ValueError("a delta's text is never empty")


def _field(obj: object, key: str) -> Any:
    """Return ``obj[key]`` when *obj* is a mapping holding it, else ``None``."""
    if isinstance(obj, Mapping):
        return obj.get(key)
    return None


def _text(obj: object, key: str) -> str:
    """Return ``obj[key]`` when it is a string, else ``""``.

    ``None`` and absent fields read alike: SDK dumps carry ``None`` for a
    field the server left out.
    """
    value = _field(obj, key)
    return value if isinstance(value, str) else ""


def _count(obj: object, key: str) -> int | None:
    """Return ``obj[key]`` when it is a token count, else ``None``.

    A ``bool`` is not a count, though Python reads it as an ``int``.
    """
    value = _field(obj, key)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _server_error(error: object) -> StreamError:
    """Build the error for an ``error`` event or object, quoting the server.

    Anthropic and OpenAI send ``{"message": ...}`` inside ``error``; Ollama
    sends the message as the ``error`` string itself.
    """
    message = error if isinstance(error, str) else _text(error, "message")
    detail = f" (server said: {message})" if message else ""
    return StreamError(f"the stream failed{detail}")


_TOOL_CALL_ERROR = (
    "the stream carried a tool call, which chat_stream does not read; "
    "use chat() for a request with tools"
)
"""Why a stream carrying a tool call fails rather than dropping it."""


class StreamState:
    """The answer so far, built from a stream's events in order.

    Feed every event to :meth:`apply`, which returns the deltas it carries,
    then call :meth:`finish` once for the response. The state is private, so
    nothing outside can mark an incomplete stream complete, and it is spent by
    :meth:`finish`: a later :meth:`apply` or :meth:`finish` raises.

    Args:
        protocol: One of :data:`STREAM_PROTOCOLS`.

    Raises:
        ValueError: If *protocol* is not one this reader knows.
    """

    def __init__(self, protocol: StreamProtocol) -> None:
        if protocol not in STREAM_PROTOCOLS:
            raise ValueError(
                f"unknown stream protocol {protocol!r}; expected one of {STREAM_PROTOCOLS}"
            )
        self._protocol = protocol
        self._content = ""
        # One entry per reasoning block (Anthropic) or a single running one.
        self._thinking_parts: list[str] = []
        self._input_tokens = 0
        self._output_tokens = 0
        self._stop_reason: str | None = None
        # Whether the protocol's end marker has arrived.
        self._complete = False
        self._finished = False

    @property
    def complete(self) -> bool:
        """Whether the protocol's end marker has arrived."""
        return self._complete

    def apply(self, event: Mapping[str, Any]) -> list[StreamDelta]:
        """Read one event and return the deltas it carries, in order.

        Raises:
            StreamError: If the event is the server reporting a failure, is
                not a JSON object, or carries a tool call.
            RuntimeError: If :meth:`finish` was already called.
        """
        if self._finished:
            raise RuntimeError("this stream's response was already built")
        if not isinstance(event, Mapping):
            raise StreamError(
                f"the stream sent an event that is not a JSON object: {type(event).__name__}"
            )
        if self._protocol == "anthropic":
            return self._apply_anthropic(event)
        if self._protocol == "openai":
            return self._apply_openai(event)
        return self._apply_ollama(event)

    def _add(self, kind: DeltaKind, text: str, deltas: list[StreamDelta]) -> None:
        """Append *text* to the answer or the reasoning, and record the delta.

        An empty piece adds nothing and is not sent.
        """
        if not text:
            return
        if kind == "text":
            self._content += text
        elif self._thinking_parts:
            self._thinking_parts[-1] += text
        else:
            self._thinking_parts.append(text)
        deltas.append(StreamDelta(kind, text))

    def _apply_anthropic(self, event: Mapping[str, Any]) -> list[StreamDelta]:
        """Read one Anthropic Messages stream event."""
        deltas: list[StreamDelta] = []
        kind = _field(event, "type")
        if kind == "error":
            raise _server_error(_field(event, "error"))
        if kind == "message_start":
            usage = _field(_field(event, "message"), "usage")
            self._read_anthropic_usage(usage)
        elif kind == "content_block_start":
            block = _field(event, "content_block")
            block_type = _field(block, "type")
            if block_type == "thinking":
                # A new block starts a new part, so two blocks stay separate.
                self._thinking_parts.append("")
                self._add("thinking", _text(block, "thinking"), deltas)
            elif block_type == "text":
                self._add("text", _text(block, "text"), deltas)
            elif isinstance(block_type, str) and block_type.endswith("tool_use"):
                raise StreamError(_TOOL_CALL_ERROR)
            # `redacted_thinking` is encrypted: nothing readable to pass on.
        elif kind == "content_block_delta":
            delta = _field(event, "delta")
            delta_type = _field(delta, "type")
            if delta_type == "text_delta":
                self._add("text", _text(delta, "text"), deltas)
            elif delta_type == "thinking_delta":
                self._add("thinking", _text(delta, "thinking"), deltas)
            # `signature_delta` verifies a reasoning block; it is not text.
        elif kind == "message_delta":
            stop = _field(_field(event, "delta"), "stop_reason")
            if isinstance(stop, str):
                self._stop_reason = stop
            self._read_anthropic_usage(_field(event, "usage"))
        elif kind == "message_stop":
            self._complete = True
        # `ping` and `content_block_stop` carry nothing to keep.
        return deltas

    def _read_anthropic_usage(self, usage: object) -> None:
        """Take the token counts an Anthropic usage object carries.

        The counts are running totals, so a later one replaces an earlier one.
        """
        input_tokens = _count(usage, "input_tokens")
        if input_tokens is not None:
            self._input_tokens = input_tokens
        output_tokens = _count(usage, "output_tokens")
        if output_tokens is not None:
            self._output_tokens = output_tokens

    def _apply_openai(self, event: Mapping[str, Any]) -> list[StreamDelta]:
        """Read one OpenAI-compatible ``chat.completion.chunk``."""
        deltas: list[StreamDelta] = []
        error = _field(event, "error")
        if error is not None:
            raise _server_error(error)
        choices = _field(event, "choices")
        # One answer is asked for, so only the first choice is read.
        if isinstance(choices, list) and choices:
            choice = choices[0]
            delta = _field(choice, "delta")
            if _field(delta, "tool_calls"):
                raise StreamError(_TOOL_CALL_ERROR)
            # DeepSeek and vLLM name the reasoning `reasoning_content`,
            # OpenRouter-style servers and Ollama `reasoning`. The first that
            # is set is read, as `chat()` reads it: a server may send both.
            reasoning = _text(delta, "reasoning_content") or _text(delta, "reasoning")
            self._add("thinking", reasoning, deltas)
            self._add("text", _text(delta, "content"), deltas)
            finish = _field(choice, "finish_reason")
            if isinstance(finish, str):
                self._stop_reason = finish
                self._complete = True
        usage = _field(event, "usage")
        prompt_tokens = _count(usage, "prompt_tokens")
        if prompt_tokens is not None:
            self._input_tokens = prompt_tokens
        completion_tokens = _count(usage, "completion_tokens")
        if completion_tokens is not None:
            self._output_tokens = completion_tokens
        return deltas

    def _apply_ollama(self, event: Mapping[str, Any]) -> list[StreamDelta]:
        """Read one chunk of Ollama's native ``/api/chat`` stream."""
        deltas: list[StreamDelta] = []
        error = _field(event, "error")
        if error is not None:
            raise _server_error(error)
        message = _field(event, "message")
        if _field(message, "tool_calls"):
            raise StreamError(_TOOL_CALL_ERROR)
        self._add("thinking", _text(message, "thinking"), deltas)
        self._add("text", _text(message, "content"), deltas)
        if _field(event, "done") is True:
            self._complete = True
            # `chat()`'s mapping: only a truncation is told apart from a stop.
            self._stop_reason = "length" if _field(event, "done_reason") == "length" else "stop"
            prompt_tokens = _count(event, "prompt_eval_count")
            if prompt_tokens is not None:
                self._input_tokens = prompt_tokens
            eval_tokens = _count(event, "eval_count")
            if eval_tokens is not None:
                self._output_tokens = eval_tokens
        return deltas

    def finish(self, model: str, *, cancelled: bool = False) -> LLMResponse:
        """Build the response from everything read.

        Args:
            model: The model name the response reports.
            cancelled: Whether the caller stopped the stream. A stopped stream
                is not checked for its end marker, and its ``stop_reason`` is
                :data:`CANCELLED_STOP_REASON`, unless the end marker had
                already arrived, when the server's own reason is kept.

        Raises:
            StreamError: If the stream ended before its end marker.
            RuntimeError: If the response was already built.
        """
        if self._finished:
            raise RuntimeError("this stream's response was already built")
        self._finished = True
        if not cancelled and not self._complete:
            marker = {
                "anthropic": "message_stop",
                "openai": "a finish_reason",
                "ollama": "done",
            }[self._protocol]
            raise StreamError(
                f"the {self._protocol} stream ended before {marker}; the answer is incomplete"
            )
        thinking = THINKING_BLOCK_SEPARATOR.join(part for part in self._thinking_parts if part)
        stopped_early = cancelled and not self._complete
        return LLMResponse(
            content=self._content,
            model=model,
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
            stop_reason=CANCELLED_STOP_REASON if stopped_early else self._stop_reason,
            thinking=thinking or None,
        )


def run_stream(
    protocol: StreamProtocol,
    events: Iterable[Mapping[str, Any]],
    model: str,
    on_delta: Callable[[StreamDelta], object],
) -> LLMResponse:
    """Read *events* to the end, calling *on_delta* for every delta.

    *on_delta* returning ``False`` (exactly ``False``, not merely falsy) stops
    the stream: no further event is read and the response holds what arrived,
    with ``stop_reason`` :data:`CANCELLED_STOP_REASON` (or the server's own,
    when the end marker came in the event that carried the last delta). Any
    other return value carries on. Closing the underlying connection is the
    caller's job.

    Args:
        protocol: One of :data:`STREAM_PROTOCOLS`.
        events: The stream's events as JSON objects, in order.
        model: The model name the response reports.
        on_delta: Called once per delta, in order.

    Raises:
        StreamError: If the server reports a failure mid-stream, an event is
            not a JSON object or carries a tool call, or the stream ends
            before its end marker.
        ValueError: If *protocol* is unknown.
    """
    state = StreamState(protocol)
    for event in events:
        for delta in state.apply(event):
            if on_delta(delta) is False:
                return state.finish(model, cancelled=True)
    return state.finish(model)
