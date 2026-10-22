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
import logging
import os
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import bmlib.embed.model as model_module
from bmlib.embed import (
    ARCTIC_EMBED_L_V2_F16,
    ChecksumMismatchError,
    DownloadCancelled,
    ModelFile,
    ModelVerificationError,
    SizeMismatchError,
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
        revision="0" * 40,
        file="m.gguf",
        size=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
    )


def test_download_fetches_and_verifies(server: _Server, tmp_path: Path) -> None:
    path = download_model(_model(server), tmp_path)
    assert path == tmp_path / "m.gguf" and path.read_bytes() == BODY
    assert [p.name for p in tmp_path.iterdir()] == ["m.gguf"]


def test_final_path_is_absent_until_the_rename(
    server: _Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[str, bool]] = []
    real_replace = os.replace

    def replace(src: Path, dst: Path) -> None:
        seen.append((Path(src).name, Path(dst).exists()))
        real_replace(src, dst)

    monkeypatch.setattr(model_module.os, "replace", replace)
    download_model(_model(server), tmp_path)
    [(temp, final_existed)] = seen
    assert temp.startswith(".m.gguf.") and temp.endswith(".part")
    assert not final_existed


def test_a_concurrent_download_cannot_corrupt_the_file(server: _Server, tmp_path: Path) -> None:
    model = _model(server)
    nested: list[Path] = []

    def start_another(done: int, total: int) -> None:
        if not nested:  # a second call runs to completion mid-way through the first
            nested.append(download_model(model, tmp_path))

    download_model(model, tmp_path, start_another)
    assert server.requests == 2
    assert (tmp_path / "m.gguf").read_bytes() == BODY
    assert [p.name for p in tmp_path.iterdir()] == ["m.gguf"]


def test_download_skips_verified_file(server: _Server, tmp_path: Path) -> None:
    (tmp_path / "m.gguf").write_bytes(BODY)
    download_model(_model(server), tmp_path)
    assert server.requests == 0


def test_download_truncated_body_leaves_no_model(server: _Server, tmp_path: Path) -> None:
    server.truncate = True
    with pytest.raises(SizeMismatchError) as info:
        download_model(_model(server), tmp_path)
    assert info.value.what == "size"
    assert list(tmp_path.iterdir()) == []


def test_download_oversized_body_fails_early(server: _Server, tmp_path: Path) -> None:
    model = _model(server, BODY[: 1024 * 1024])  # server sends 3 MiB, 1 MiB expected
    with pytest.raises(SizeMismatchError) as info:
        download_model(model, tmp_path)
    assert info.value.what == "size"
    assert info.value.actual == f"more than {model.size}"  # the in-stream check, not the final one
    assert list(tmp_path.iterdir()) == []


def test_download_wrong_bytes_is_checksum_error(server: _Server, tmp_path: Path) -> None:
    model = dataclasses.replace(_model(server), sha256="0" * 64)
    with pytest.raises(ChecksumMismatchError) as info:
        download_model(model, tmp_path)
    assert info.value.what == "sha256"
    assert info.value.expected == "0" * 64
    assert info.value.actual == hashlib.sha256(BODY).hexdigest()
    assert list(tmp_path.iterdir()) == []


def test_existing_mismatched_file_is_refetched(
    server: _Server, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "m.gguf").write_bytes(b"stale")
    with caplog.at_level(logging.WARNING, logger="bmlib.embed.model"):
        download_model(_model(server), tmp_path)
    assert server.requests == 1 and (tmp_path / "m.gguf").read_bytes() == BODY
    assert "fetching it again" in caplog.text


def test_existing_same_size_corrupt_file_is_refetched(server: _Server, tmp_path: Path) -> None:
    (tmp_path / "m.gguf").write_bytes(bytes(len(BODY)))
    download_model(_model(server), tmp_path)
    assert server.requests == 1 and (tmp_path / "m.gguf").read_bytes() == BODY


def test_mismatched_file_is_gone_even_when_the_refetch_fails(
    server: _Server, tmp_path: Path
) -> None:
    (tmp_path / "m.gguf").write_bytes(bytes(len(BODY)))
    server.truncate = True
    with pytest.raises(ModelVerificationError):
        download_model(_model(server), tmp_path)
    assert list(tmp_path.iterdir()) == []


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


def test_an_interrupt_leaves_no_part_file(server: _Server, tmp_path: Path) -> None:
    def interrupt(done: int, total: int) -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        download_model(_model(server), tmp_path, interrupt)
    assert list(tmp_path.iterdir()) == []


def test_verify_model_checksum_mismatch(tmp_path: Path, server: _Server) -> None:
    (tmp_path / "m.gguf").write_bytes(bytes(len(BODY)))
    with pytest.raises(ChecksumMismatchError) as info:
        verify_model(tmp_path / "m.gguf", _model(server))
    assert info.value.what == "sha256"
    assert info.value.actual == hashlib.sha256(bytes(len(BODY))).hexdigest()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("revision", "main"),
        ("file", "../m.gguf"),
        ("file", "/abs/m.gguf"),
        ("file", "sub\\m.gguf"),
        ("file", ".."),
        ("file", ""),
        ("size", 0),
        ("sha256", "A" * 64),
        ("sha256", "0" * 63),
    ],
)
def test_model_file_refuses_what_cannot_pin(field: str, value: object) -> None:
    with pytest.raises(ValueError, match=field):
        dataclasses.replace(ARCTIC_EMBED_L_V2_F16, **{field: value})


def test_verify_model_size_mismatch(tmp_path: Path, server: _Server) -> None:
    (tmp_path / "m.gguf").write_bytes(b"x")
    with pytest.raises(SizeMismatchError) as info:
        verify_model(tmp_path / "m.gguf", _model(server))
    assert (info.value.what, info.value.expected, info.value.actual) == ("size", len(BODY), 1)


def test_both_mismatches_are_verification_errors() -> None:
    assert issubclass(SizeMismatchError, ModelVerificationError)
    assert issubclass(ChecksumMismatchError, ModelVerificationError)
