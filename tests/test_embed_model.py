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

"""Tests for the verified model download, against a loopback server only."""

from __future__ import annotations

import dataclasses
import hashlib
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from bmlib.embed import (
    DownloadCancelled,
    ModelFile,
    ModelVerificationError,
    download_model,
    verify_model,
)

BODY = bytes(range(256)) * (3 * 1024 * 4)  # 3 MiB


class _Server(ThreadingHTTPServer):
    body = BODY
    truncate = False
    requests = 0


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        server = self.server
        assert isinstance(server, _Server)
        server.requests += 1
        self.send_response(200)
        self.send_header("Content-Length", str(len(server.body)))
        self.end_headers()
        self.wfile.write(server.body[: len(server.body) // 2] if server.truncate else server.body)
        self.close_connection = True

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def server() -> Iterator[_Server]:
    srv = _Server(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def _model(server: _Server, body: bytes = BODY) -> ModelFile:
    class _Local(ModelFile):
        def url(self) -> str:
            return f"http://127.0.0.1:{server.server_address[1]}/m.gguf"

    return _Local(
        repo="r",
        revision="v",
        file="m.gguf",
        size=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
    )


def test_download_fetches_and_verifies(server: _Server, tmp_path: Path) -> None:
    path = download_model(_model(server), tmp_path)
    assert path == tmp_path / "m.gguf" and path.read_bytes() == BODY
    assert not (tmp_path / "m.gguf.part").exists()


def test_download_skips_verified_file(server: _Server, tmp_path: Path) -> None:
    (tmp_path / "m.gguf").write_bytes(BODY)
    download_model(_model(server), tmp_path)
    assert server.requests == 0


def test_download_truncated_body_leaves_no_model(server: _Server, tmp_path: Path) -> None:
    server.truncate = True
    with pytest.raises(ModelVerificationError) as info:
        download_model(_model(server), tmp_path)
    assert info.value.what == "size"
    assert list(tmp_path.iterdir()) == []


def test_download_oversized_body_fails_early(server: _Server, tmp_path: Path) -> None:
    model = _model(server, BODY[: 1024 * 1024])  # server sends 3 MiB, 1 MiB expected
    with pytest.raises(ModelVerificationError) as info:
        download_model(model, tmp_path)
    assert info.value.what == "size"
    assert list(tmp_path.iterdir()) == []


def test_download_wrong_bytes_is_checksum_error(server: _Server, tmp_path: Path) -> None:
    model = dataclasses.replace(_model(server), sha256="0" * 64)
    with pytest.raises(ModelVerificationError) as info:
        download_model(model, tmp_path)
    assert info.value.expected == "0" * 64
    assert info.value.actual == hashlib.sha256(BODY).hexdigest()
    assert list(tmp_path.iterdir()) == []


def test_existing_mismatched_file_is_refetched(server: _Server, tmp_path: Path) -> None:
    (tmp_path / "m.gguf").write_bytes(b"stale")
    download_model(_model(server), tmp_path)
    assert server.requests == 1 and (tmp_path / "m.gguf").read_bytes() == BODY


def test_progress_reports_reach_size(server: _Server, tmp_path: Path) -> None:
    seen: list[tuple[int, int]] = []
    download_model(_model(server), tmp_path, lambda done, total: seen.append((done, total)))
    assert seen[-1] == (len(BODY), len(BODY))
    assert [d for d, _ in seen] == sorted(d for d, _ in seen)


def test_progress_false_cancels(server: _Server, tmp_path: Path) -> None:
    calls: list[int] = []

    def cancel(done: int, total: int) -> bool:
        calls.append(done)
        return False

    with pytest.raises(DownloadCancelled):
        download_model(_model(server), tmp_path, cancel)
    assert len(calls) == 1
    assert list(tmp_path.iterdir()) == []


def test_verify_model_size_mismatch(tmp_path: Path, server: _Server) -> None:
    (tmp_path / "m.gguf").write_bytes(b"x")
    with pytest.raises(ModelVerificationError) as info:
        verify_model(tmp_path / "m.gguf", _model(server))
    assert (info.value.expected, info.value.actual) == (len(BODY), 1)
