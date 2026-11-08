"""The shared contract for a cancelled stream's connection (#649, #652).

Each case of ``tests/fixtures/stream_give_up.json`` runs a provider's
cancellable ``chat_stream`` over its real SDK client against a local server
that stalls or refuses, cancels it, and checks that the server sees the
connection close and no second request. Rust runs the same cases in
``rust/bmlib/tests/stream_give_up_contract.rs``.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from bmlib.llm import LLMMessage
from bmlib.llm.providers.anthropic import AnthropicProvider
from bmlib.llm.providers.ollama import OllamaProvider
from bmlib.llm.providers.openai_provider import OpenAIProvider
from bmlib.llm.stream import StreamDelta

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "stream_give_up.json").read_text(encoding="utf-8")
)

POLL_SECONDS = 0.01
"""How often a wait on the server's counters looks again."""

REFUSAL_BODY = b'{"error": {"message": "busy"}}'

PROVIDERS: dict[str, Callable[[str], Any]] = {
    "ollama": lambda url: OllamaProvider(base_url=url),
    "openai": lambda url: OpenAIProvider(api_key="k", base_url=url),
    "anthropic": lambda url: AnthropicProvider(api_key="k", base_url=url),
}


class Server:
    """A local server behaving as one case says; its threads are daemons."""

    def __init__(self, case: dict[str, Any]) -> None:
        """Listen on 127.0.0.1 and serve *case* until the process ends."""
        self.case = case
        self.requests = 0
        self.open = 0
        self._lock = threading.Lock()
        self._listener = socket.create_server(("127.0.0.1", 0))
        self.url = f"http://127.0.0.1:{self._listener.getsockname()[1]}"
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while True:
            try:
                conn, _ = self._listener.accept()
            except OSError:
                return
            with self._lock:
                self.open += 1
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        reader = conn.makefile("rb")
        try:
            while _read_request(reader):
                with self._lock:
                    first = self.requests == 0
                if first:
                    conn.sendall(self._first_answer())
                with self._lock:
                    self.requests += 1
        except OSError:
            pass
        finally:
            with self._lock:
                self.open -= 1
            conn.close()

    def _first_answer(self) -> bytes:
        server = self.case["server"]
        if server == "stalls_before_headers":
            return b""
        if server == "stalls_after_first_event":
            event = self.case["event"].encode()
            return (
                b"HTTP/1.1 200 OK\r\nContent-Type: %s\r\nTransfer-Encoding: chunked\r\n\r\n"
                b"%x\r\n%s\r\n" % (self.case["content_type"].encode(), len(event), event)
            )
        if server == "refuses_retryably":
            return (
                b"HTTP/1.1 503 Service Unavailable\r\nContent-Type: application/json\r\n"
                b"Retry-After: 1\r\nContent-Length: %d\r\n\r\n%s"
                % (len(REFUSAL_BODY), REFUSAL_BODY)
            )
        raise AssertionError(f"unknown server behaviour {server}")


def _read_request(reader: Any) -> bool:
    """Read one request, headers and body; False once the connection ends."""
    length = 0
    while True:
        line = reader.readline()
        if not line:
            return False
        if line.lower().startswith(b"content-length:"):
            length = int(line.split(b":", 1)[1])
        if line == b"\r\n":
            break
    return len(reader.read(length)) == length


def _within(limit: float, done: Callable[[], bool]) -> bool:
    """Whether *done* becomes true within *limit* seconds."""
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        if done():
            return True
        time.sleep(POLL_SECONDS)
    return done()


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=[c["name"] for c in FIXTURE["cases"]])
def test_a_cancel_closes_the_connection_and_sends_no_retry(case: dict[str, Any]) -> None:
    limit = FIXTURE["closed_within_ms"] / 1000
    server = Server(case)
    provider = PROVIDERS[case["provider"]](server.url)
    cancel = threading.Event()
    if case["cancel"] == "once_requested":

        def cancel_once_requested() -> None:
            _within(10, lambda: server.requests > 0)
            cancel.set()

        threading.Thread(target=cancel_once_requested, daemon=True).start()
    else:
        assert case["cancel"] == "on_first_delta"
    deltas: list[str] = []

    def on_delta(delta: StreamDelta) -> None:
        if delta.kind == "text":
            deltas.append(delta.text)
        cancel.set()

    refuses = case["server"] == "refuses_retryably"
    try:
        response = provider.chat_stream(
            [LLMMessage(role="user", content="Was it blinded?")],
            on_delta,
            model="m",
            cancel=cancel,
        )
    except Exception:
        if not refuses:
            raise
    else:
        assert response.stop_reason == "cancelled"
        assert deltas == case["deltas"]
    if refuses:
        # A retry would come after its backoff: wait the whole bound.
        time.sleep(limit)
    assert _within(limit, lambda: server.open == 0), f"{server.open} connection(s) still open"
    assert server.requests == case["requests"]
