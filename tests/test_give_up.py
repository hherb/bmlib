"""Unit tests for :mod:`bmlib.llm.give_up` (#649, #652).

The real stack is driven by ``tests/test_stream_give_up_contract.py``; these
pin the wrapper's rules on stand-in streams and clients.
"""

from __future__ import annotations

import logging
import threading
import time
from types import SimpleNamespace
from typing import Any

import httpcore
import httpx
import pytest

from bmlib.llm.give_up import (
    CANCEL_POLL_SECONDS,
    GIVEN_UP_MESSAGE,
    _GivingUpBackend,
    _GivingUpStream,
    install_give_up,
    watch_this_thread,
)


class SlowStream:
    """A stream answering ``b"data"`` once *ready_after* seconds have passed,
    and timing out each read before then as a socket would."""

    def __init__(self, ready_after: float) -> None:
        self.ready_at = time.monotonic() + ready_after
        self.timeouts: list[float | None] = []
        self.written: list[bytes] = []

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        self.timeouts.append(timeout)
        left = self.ready_at - time.monotonic()
        if timeout is not None and left > timeout:
            time.sleep(timeout)
            raise httpcore.ReadTimeout("timed out")
        time.sleep(max(left, 0))
        return b"data"

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self.written.append(buffer)


def on_watched_thread(abandoned: threading.Event, call: Any) -> Any:
    """Run *call* on a fresh thread watching *abandoned*; its result or error."""
    outcome: dict[str, Any] = {}

    def run() -> None:
        watch_this_thread(abandoned)
        try:
            outcome["value"] = call()
        except Exception as exc:
            outcome["error"] = exc

    thread = threading.Thread(target=run)
    thread.start()
    thread.join(10)
    return outcome


def test_an_unwatched_thread_reads_straight_through() -> None:
    inner = SlowStream(0.0)
    assert _GivingUpStream(inner, httpcore).read(10, 5.0) == b"data"
    assert inner.timeouts == [5.0]


def test_a_watched_read_reads_on_through_its_slices() -> None:
    inner = SlowStream(3 * CANCEL_POLL_SECONDS)
    outcome = on_watched_thread(
        threading.Event(), lambda: _GivingUpStream(inner, httpcore).read(10, None)
    )
    assert outcome == {"value": b"data"}
    assert len(inner.timeouts) > 1
    assert all(t == CANCEL_POLL_SECONDS for t in inner.timeouts)


def test_a_watched_read_keeps_its_own_timeout() -> None:
    inner = SlowStream(10.0)
    started = time.monotonic()
    outcome = on_watched_thread(
        threading.Event(), lambda: _GivingUpStream(inner, httpcore).read(10, 0.2)
    )
    assert isinstance(outcome["error"], httpcore.ReadTimeout)
    assert 0.2 <= time.monotonic() - started < 2


def test_a_read_given_up_fails_within_a_slice() -> None:
    abandoned = threading.Event()
    threading.Timer(0.1, abandoned.set).start()
    started = time.monotonic()
    outcome = on_watched_thread(
        abandoned, lambda: _GivingUpStream(SlowStream(10.0), httpcore).read(10, None)
    )
    assert isinstance(outcome["error"], httpcore.ReadError)
    assert str(outcome["error"]) == GIVEN_UP_MESSAGE
    assert time.monotonic() - started < 0.1 + 5 * CANCEL_POLL_SECONDS


def test_a_given_up_thread_writes_nothing_and_opens_nothing() -> None:
    abandoned = threading.Event()
    abandoned.set()
    inner = SlowStream(0.0)
    wrote = on_watched_thread(abandoned, lambda: _GivingUpStream(inner, httpcore).write(b"x"))
    assert isinstance(wrote["error"], httpcore.WriteError)
    assert inner.written == []
    opened: list[object] = []
    backend = _GivingUpBackend(SimpleNamespace(connect_tcp=opened.append), httpcore)
    connected = on_watched_thread(abandoned, lambda: backend.connect_tcp("h"))
    assert isinstance(connected["error"], httpcore.ConnectError)
    assert opened == []


def test_tls_keeps_the_wrapper() -> None:
    tls = SlowStream(0.0)
    inner = SimpleNamespace(start_tls=lambda *a, **k: tls)
    upgraded = _GivingUpStream(inner, httpcore).start_tls(None)
    assert isinstance(upgraded, _GivingUpStream)
    assert upgraded._inner is tls


def test_install_wraps_every_pool_once() -> None:
    http = httpx.Client(mounts={"http://proxy.example": httpx.HTTPTransport()})
    sdk = SimpleNamespace(_client=http)
    assert install_give_up(sdk)
    assert install_give_up(sdk)
    pools = [http._transport._pool, *(t._pool for t in http._mounts.values() if t)]
    assert len(pools) == 2
    for pool in pools:
        backend = pool._network_backend
        assert isinstance(backend, _GivingUpBackend)
        assert not isinstance(backend._inner, _GivingUpBackend)


def test_a_client_without_a_pool_is_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="bmlib.llm.give_up"):
        assert not install_give_up(SimpleNamespace())
    assert "#649" in caplog.text
