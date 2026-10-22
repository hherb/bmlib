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
    with pytest.raises(SystemExit) as e:
        conv.main(_argv(tmp_path, tmp_path))
    assert e.value.code != 0
    assert len(calls) == 1 and "rev-parse" in calls[0]


def _argv(tmp_path, base):
    ref = tmp_path / "ref.json"
    ref.write_text(json.dumps({"items": [{"name": "d", "kind": "document", "text": "a",
                                          "vector": [1.0] + [0.0] * 255}]}))  # fmt: skip
    return ["--llama-cpp", str(tmp_path), "--base-dir", str(base),
            "--out", str(tmp_path / "o"), "--verify-reference", str(ref)]  # fmt: skip


def _fake_tools(vector):
    """git, the converter, llama-quantize and llama-embedding, without llama.cpp."""
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if "rev-parse" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout=conv.LLAMA_CPP_COMMIT + "\n")
        if "--outfile" in cmd:
            Path(cmd[cmd.index("--outfile") + 1]).write_bytes(b"f16")
        elif cmd[-1] == "Q8_0":
            Path(cmd[2]).write_bytes(b"q8")
        out = json.dumps({"data": [{"embedding": vector}]})
        return subprocess.CompletedProcess(cmd, 0, stdout=out)

    return run, calls


def test_refuses_a_base_dir_of_another_revision(tmp_path, monkeypatch):
    run, calls = _fake_tools([1.0] + [0.0] * 255)
    monkeypatch.setattr(conv.subprocess, "run", run)
    base = tmp_path / ("0" * 40)
    base.mkdir()
    with pytest.raises(SystemExit, match="snapshot of"):
        conv.main(_argv(tmp_path, base))
    assert len(calls) == 1  # nothing converted


def test_a_failed_verification_leaves_no_card(tmp_path, monkeypatch):
    run, _ = _fake_tools([0.0, 1.0] + [0.0] * 254)  # orthogonal to the reference
    monkeypatch.setattr(conv.subprocess, "run", run)
    base = tmp_path / conv.BASE_REVISION
    base.mkdir()
    out = tmp_path / "o"
    out.mkdir()
    for stale in ("manifest.json", "README.md"):
        (out / stale).write_text("from an earlier run")
    with pytest.raises(SystemExit, match="min cosine"):
        conv.main(_argv(tmp_path, base))
    assert not (out / "manifest.json").exists() and not (out / "README.md").exists()


def test_a_verified_conversion_writes_card_and_manifest(tmp_path, monkeypatch):
    run, _ = _fake_tools([1.0] + [0.0] * 255)
    monkeypatch.setattr(conv.subprocess, "run", run)
    base = tmp_path / conv.BASE_REVISION
    base.mkdir()
    conv.main(_argv(tmp_path, base))
    m = json.loads((tmp_path / "o" / "manifest.json").read_text())
    assert [f["min_cosine"] for f in m["files"]] == [pytest.approx(1.0)] * 2
    assert "1.00000" in (tmp_path / "o" / "README.md").read_text()


def test_parse_embedding_rejects_multiple_entries():
    out = json.dumps({"data": [{"embedding": [1.0, 0.0]}, {"embedding": [0.0, 1.0]}]})
    with pytest.raises(ValueError, match="expected 1"):
        conv.parse_embedding(out, 2)


def test_verify_passes_separator_and_prefixes_queries_only(tmp_path, monkeypatch):
    seen = []

    def fake_run(cmd, **kw):
        seen.append(cmd)
        out = json.dumps({"data": [{"embedding": [1.0] + [0.0] * 255}]})
        return subprocess.CompletedProcess(cmd, 0, stdout=out)

    monkeypatch.setattr(conv.subprocess, "run", fake_run)
    ref = {
        "items": [
            {"name": "d", "kind": "document", "text": "a\nb", "vector": [1.0] + [0.0] * 255},
            {"name": "q", "kind": "query", "text": "hi", "vector": [1.0] + [0.0] * 255},
        ]
    }
    gguf = tmp_path / "m-F16.gguf"
    assert conv.verify(tmp_path, gguf, ref) == pytest.approx(1.0)
    prompts = [c[c.index("-p") + 1] for c in seen]
    assert prompts == ["a\nb", "query: hi"]
    assert all(c[c.index("--embd-separator") + 1] == conv.SEPARATOR for c in seen)
