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
"""Tests for ``scripts/convert_embedding_model.py`` (pure parts; no llama.cpp)."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "convert_embedding_model.py"
_spec = importlib.util.spec_from_file_location("convert_embedding_model", _PATH)
assert _spec is not None and _spec.loader is not None
conv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(conv)


def test_sha256_file_streams(tmp_path):
    data = b"abc" * (1024 * 1024)  # 3 MiB
    f = tmp_path / "x.bin"
    f.write_bytes(data)
    assert conv.sha256_file(f) == hashlib.sha256(data).hexdigest()


def test_manifest_lists_every_file(tmp_path):
    a, b = tmp_path / "a.gguf", tmp_path / "b.gguf"
    a.write_bytes(b"aa")
    b.write_bytes(b"bbbb")
    m = conv.manifest([a, b], "c" * 40, "r" * 40)
    assert m["llama_cpp_commit"] == "c" * 40
    assert m["base_revision"] == "r" * 40
    assert m["base_model"] == "Snowflake/snowflake-arctic-embed-l-v2.0"
    assert [(e["file"], e["size"]) for e in m["files"]] == [("a.gguf", 2), ("b.gguf", 4)]
    assert m["files"][1]["sha256"] == hashlib.sha256(b"bbbb").hexdigest()


def test_model_card_front_matter():
    m = {
        "base_model": "Snowflake/snowflake-arctic-embed-l-v2.0",
        "base_revision": "r" * 40,
        "llama_cpp_commit": "c" * 40,
        "files": [
            {"file": "m-F16.gguf", "size": 10, "sha256": "1" * 64, "min_cosine": 0.9995},
            {"file": "m-Q8_0.gguf", "size": 5, "sha256": "2" * 64, "min_cosine": 0.9991},
        ],
    }
    card = conv.model_card(m)
    assert card.startswith("---\nlicense: apache-2.0\n")
    assert "base_model: Snowflake/snowflake-arctic-embed-l-v2.0" in card
    assert "base_model_relation: quantized" in card
    assert "1" * 64 in card and "2" * 64 in card
    assert "0.9995" in card and "0.9991" in card


def test_cosine_and_embedding_parse():
    assert conv.cosine([1.0, 0.0], [2.0, 0.0]) == pytest.approx(1.0)
    out = json.dumps({"data": [{"embedding": [3.0, 4.0, 9.0]}]})
    assert conv.parse_embedding(out, 2) == pytest.approx([0.6, 0.8])
    for bad in (
        "not json",
        "{}",
        json.dumps({"data": []}),
        json.dumps({"data": [{"embedding": [1.0]}]}),
    ):
        with pytest.raises(ValueError):
            conv.parse_embedding(bad, 2)


def test_refuses_wrong_llama_cpp_commit(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="f" * 40 + "\n")

    monkeypatch.setattr(conv.subprocess, "run", fake_run)
    argv = ["--llama-cpp", str(tmp_path), "--base-dir", str(tmp_path), "--out", str(tmp_path / "o")]
    with pytest.raises(SystemExit) as e:
        conv.main(argv)
    assert e.value.code != 0
    assert len(calls) == 1 and "rev-parse" in calls[0]
