"""Give up an abandoned stream's request at the socket (#649, #652).

A cancellable stream (:func:`bmlib.llm.stream.read_stream` with *cancel*)
sends its request and reads its events on a worker thread, which the caller
abandons on a cancel. That worker is blocked in a socket read, which no
other thread can wake, and a local model server sends nothing until it has
read the whole prompt: minutes, for a long paper on a large model, and it
stops only when the connection closes. The SDKs also retry opening a stream
on their own, which would send the prompt again after the cancel.

So every connection behind a provider's SDK client goes through
:class:`_GivingUpBackend` (installed by :func:`install_give_up`, once per
client). On a thread that :func:`watch_this_thread` was given an *abandoned*
event, a read waits in slices of :data:`CANCEL_POLL_SECONDS`, looking at the
event between them, and once it is set the read, any later write, and any new
connection fail with the HTTP library's own network error: the connection
closes, and a retry sends nothing. A slice that times out has read nothing, so
the next one reads on, and the read's own timeout is kept. On any other thread
(every non-streamed call) the wrapper passes straight through.

The backend is set through the HTTP library's connection pool
(``pool._network_backend``, a private attribute of httpcore and httpcore2),
since neither httpx nor httpx2 takes a backend. Where a client has no such
pool, nothing is installed, a WARNING says so, and a cancelled stream's
connection then closes only when the server answers, as before #649.
"""

from __future__ import annotations

import importlib
import logging
import threading
import time
from types import ModuleType
from typing import Any

logger = logging.getLogger(__name__)

CANCEL_POLL_SECONDS = 0.05
"""How often a cancellable stream looks at its cancel while it waits, and how
long each slice of an abandonable read waits for the server."""

GIVEN_UP_MESSAGE = "the request was given up by its caller"
"""What the network error raised for an abandoned request says."""

_watched = threading.local()
"""The *abandoned* event of the stream worker running on this thread, if any."""


def watch_this_thread(abandoned: threading.Event) -> None:
    """Give up this thread's requests once *abandoned* is set.

    Called by a cancellable stream's worker thread before it opens the
    stream; the thread is the worker's own, so the watch is never removed.

    Args:
        abandoned: Set by the caller when it stops waiting for the stream.
    """
    _watched.abandoned = abandoned


def _abandoned_event() -> threading.Event | None:
    """The event this thread was told to watch, or ``None``."""
    return getattr(_watched, "abandoned", None)


def install_give_up(sdk_client: Any) -> bool:
    """Route every connection *sdk_client* opens through the give-up wrapper.

    *sdk_client* is an SDK client holding its httpx (or httpx2) client as
    ``_client``, as ``openai.OpenAI``, ``anthropic.Anthropic`` and
    ``ollama.Client`` do. Each connection pool behind it (its transport and
    any proxy mounts) gets the wrapper; installing twice changes nothing.
    Call it when the SDK client is made, before any request: a connection
    already pooled keeps the backend it was made with.

    Args:
        sdk_client: The provider's SDK client.

    Returns:
        Whether at least one pool now has the wrapper. ``False`` is logged at
        WARNING, since a cancel then leaves the connection open until the
        server answers.
    """
    http = getattr(sdk_client, "_client", None)
    transports = [getattr(http, "_transport", None), *getattr(http, "_mounts", {}).values()]
    installed = False
    for transport in transports:
        pool = getattr(transport, "_pool", None)
        backend = getattr(pool, "_network_backend", None)
        if pool is None or backend is None:
            continue
        if not isinstance(backend, _GivingUpBackend):
            pool._network_backend = _GivingUpBackend(backend, _core_module(pool))
        installed = True
    if not installed:
        logger.warning(
            "%s has no connection pool bmlib can reach; a cancelled stream's "
            "connection stays open until the server answers (#649)",
            type(sdk_client).__name__,
        )
    return installed


def _core_module(pool: object) -> ModuleType:
    """The httpcore package *pool* comes from (``httpcore`` or ``httpcore2``),
    whose network errors the wrapper raises so the HTTP client maps them."""
    return importlib.import_module(type(pool).__module__.split(".")[0])


class _GivingUpBackend:
    """A network backend whose connections give up on a watched thread.

    Wraps the pool's own backend; every connection it opens is a
    :class:`_GivingUpStream`, and on a thread whose watched event is set it
    opens none.
    """

    def __init__(self, inner: Any, core: ModuleType) -> None:
        """Wrap *inner*, raising *core*'s network errors."""
        self._inner = inner
        self._core = core

    def connect_tcp(self, *args: Any, **kwargs: Any) -> _GivingUpStream:
        """Open a TCP connection as *inner* does, unless this thread gave up."""
        _refuse_if_abandoned(self._core.ConnectError)
        return _GivingUpStream(self._inner.connect_tcp(*args, **kwargs), self._core)

    def connect_unix_socket(self, *args: Any, **kwargs: Any) -> _GivingUpStream:
        """Open a Unix-socket connection as *inner* does, unless this thread gave up."""
        _refuse_if_abandoned(self._core.ConnectError)
        return _GivingUpStream(self._inner.connect_unix_socket(*args, **kwargs), self._core)

    def sleep(self, seconds: float) -> None:
        """Sleep as *inner* does."""
        self._inner.sleep(seconds)


class _GivingUpStream:
    """A connection whose reads and writes give up on a watched thread."""

    def __init__(self, inner: Any, core: ModuleType) -> None:
        """Wrap *inner*, raising *core*'s network errors."""
        self._inner = inner
        self._core = core

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        """Read as *inner* does; on a watched thread, in slices, until given up.

        Raises:
            core.ReadError: The thread's watched event was set.
            core.ReadTimeout: *timeout* ran out, as *inner* raises it.
        """
        abandoned = _abandoned_event()
        if abandoned is None:
            return self._inner.read(max_bytes, timeout)
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            if abandoned.is_set():
                raise self._core.ReadError(GIVEN_UP_MESSAGE)
            left = None if deadline is None else deadline - time.monotonic()
            if left is not None and left <= 0:
                raise self._core.ReadTimeout("timed out")
            wait = CANCEL_POLL_SECONDS if left is None else min(CANCEL_POLL_SECONDS, left)
            try:
                return self._inner.read(max_bytes, wait)
            except self._core.ReadTimeout:
                # The slice ran out, not the read: read on.
                if left is not None and left <= wait:
                    raise

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        """Write as *inner* does, unless this thread gave up."""
        _refuse_if_abandoned(self._core.WriteError)
        self._inner.write(buffer, timeout)

    def close(self) -> None:
        """Close *inner*."""
        self._inner.close()

    def start_tls(self, *args: Any, **kwargs: Any) -> _GivingUpStream:
        """Upgrade *inner* to TLS; the TLS stream gives up as this one does."""
        return _GivingUpStream(self._inner.start_tls(*args, **kwargs), self._core)

    def get_extra_info(self, info: str) -> Any:
        """What *inner* says about itself (its socket, TLS object, peer)."""
        return self._inner.get_extra_info(info)


def _refuse_if_abandoned(error: type[Exception]) -> None:
    """Raise *error* if this thread's watched event is set."""
    abandoned = _abandoned_event()
    if abandoned is not None and abandoned.is_set():
        raise error(GIVEN_UP_MESSAGE)
