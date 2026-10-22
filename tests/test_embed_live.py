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

"""Live tests of the Embedder against the reference vectors.

Skipped unless ``BMLIB_EMBED_MODEL`` names a GGUF file;
``rust/bmlib/tests/embed_live.rs`` mirrors them, with the same names and
tolerances. The tests after them need no model: they run the Embedder's own
rules against a fake llama-cpp-python object.
"""

from __future__ import annotations

import builtins
import json
import math
import os
import sys
import types
from pathlib import Path

import pytest

from bmlib.embed import (
    EMBEDDING_DIM,
    MAX_TOKENS,
    Embedder,
    EmbedError,
    query_text,
    truncate_and_normalize,
)

MODEL = os.environ.get("BMLIB_EMBED_MODEL")
REFERENCE = Path(__file__).parent / "fixtures" / "embedding_reference.json"

live = pytest.mark.skipif(not MODEL, reason="BMLIB_EMBED_MODEL is not set")


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.hypot(*a) * math.hypot(*b))


@pytest.fixture(scope="module")
def embedder() -> Embedder:
    assert MODEL
    return Embedder(Path(MODEL).expanduser())


@live
def test_reference_vectors_within_tolerance(embedder: Embedder) -> None:
    assert MODEL
    floor = 0.998 if "Q8_0" in MODEL else 0.999
    items = json.loads(REFERENCE.read_text())["items"]
    for item in items:
        if item["kind"] == "query":
            got = embedder.embed_query(item["text"])
        else:
            got = embedder.embed_documents([item["text"]])[0]
        cos = _cosine(got, item["vector"])
        print(f"cosine {item['name']}: {cos:.6f}")
        assert cos >= floor, f"{item['name']}: cosine {cos:.6f} < {floor}"


@live
def test_batch_keeps_input_order(embedder: Embedder) -> None:
    """All 20 items, twice, in one call: each is its own text's vector."""
    assert MODEL
    floor = 0.998 if "Q8_0" in MODEL else 0.999
    items = json.loads(REFERENCE.read_text())["items"]
    texts = [query_text(i["text"]) if i["kind"] == "query" else i["text"] for i in items]
    got = embedder.embed_documents(texts + texts)
    assert len(got) == 2 * len(items)
    for k, vec in enumerate(got):
        item = items[k % len(items)]
        single = embedder.embed_documents([texts[k % len(items)]])[0]
        # Batching moves the numerics a little on the CPU (min 0.99968 seen);
        # another reference text is at most 0.795 away, so a swap is caught.
        same = _cosine(vec, single)
        assert same >= 0.999, f"{item['name']}: batch vs single cosine {same:.6f}"
        cos = _cosine(vec, item["vector"])
        assert cos >= floor, f"{item['name']}: cosine {cos:.6f} < {floor}"


@live
def test_query_is_prefixed_document(embedder: Embedder) -> None:
    query = embedder.embed_query("what treats malaria")
    prefixed = embedder.embed_documents(["query: what treats malaria"])[0]
    assert _cosine(query, prefixed) > 0.9999, "query is not the prefixed document"


@live
def test_overlong_text_is_an_error(embedder: Embedder) -> None:
    long_text = "word " * 40_000
    with pytest.raises(EmbedError) as caught:
        embedder.embed_documents(["short", long_text])
    assert caught.value.index == 1


def _text_of_tokens(embedder: Embedder, count: int) -> str:
    """A text the model's tokenizer counts as exactly ``count`` tokens."""

    def tokens(text: str) -> int:
        return len(embedder._llm.tokenize(text.encode(), add_bos=True, special=False))

    per_word = tokens("word word") - tokens("word")
    words = (count - tokens("word")) // per_word + 1
    text = " ".join(["word"] * words)
    assert tokens(text) == count, f"{tokens(text)} tokens, wanted {count}"
    return text


@live
def test_text_at_max_tokens_is_accepted(embedder: Embedder) -> None:
    got = embedder.embed_documents(["short", _text_of_tokens(embedder, MAX_TOKENS)])
    assert len(got) == 2


@live
def test_text_one_over_max_tokens_is_an_error(embedder: Embedder) -> None:
    with pytest.raises(EmbedError, match=f"has {MAX_TOKENS + 1} tokens") as caught:
        embedder.embed_documents(["short", _text_of_tokens(embedder, MAX_TOKENS + 1)])
    assert caught.value.index == 1


@live
def test_empty_text_is_an_error(embedder: Embedder) -> None:
    with pytest.raises(EmbedError) as caught:
        embedder.embed_documents(["fine", "  "])
    assert caught.value.index == 1


@live
def test_empty_query_is_an_error(embedder: Embedder) -> None:
    with pytest.raises(EmbedError) as caught:
        embedder.embed_query("   ")
    assert caught.value.index == 0


@live
def test_vectors_are_unit_length(embedder: Embedder) -> None:
    for i, vec in enumerate(embedder.embed_documents(["aspirin", "insulin resistance"])):
        assert len(vec) == EMBEDDING_DIM, f"item {i}"
        assert math.isclose(math.hypot(*vec), 1.0, abs_tol=1e-6), f"item {i}"


def test_missing_extra_names_it(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def fail(name: str, *args: object, **kwargs: object) -> object:
        if name == "llama_cpp":
            raise ImportError("no llama_cpp")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", fail)
    with pytest.raises(EmbedError, match=r"bmlib\[embed\]") as caught:
        Embedder(Path("nonexistent.gguf"))
    assert "no llama_cpp" in str(caught.value)  # what was raised, not an assumed cause


def test_a_str_is_not_a_list_of_texts() -> None:
    embedder = Embedder.__new__(Embedder)  # no model: the check comes first
    with pytest.raises(TypeError, match="not a str"):
        embedder.embed_documents("aspirin")


class _FakeLlama:
    """llama-cpp-python's ``Llama``, one token per character."""

    def __init__(self) -> None:
        self.tokenized: list[str] = []
        self.embedded: list[tuple[list[str], bool, bool]] = []
        self.closed = False
        self.fail: Exception | None = None

    def tokenize(self, text: bytes, add_bos: bool, special: bool) -> list[int]:
        self.tokenized.append(text.decode())
        return [0] * len(text.decode())

    def embed(self, texts: list[str], normalize: bool, truncate: bool) -> list[list[float]]:
        self.embedded.append((texts, normalize, truncate))
        if self.fail is not None:
            raise self.fail
        return [_raw(t) for t in texts]

    def close(self) -> None:
        self.closed = True


def _raw(text: str) -> list[float]:
    return [float(len(text)), 1.0] + [0.0] * EMBEDDING_DIM


@pytest.fixture
def fake() -> tuple[Embedder, _FakeLlama]:
    embedder = Embedder.__new__(Embedder)
    llm = _FakeLlama()
    embedder._llm = llm
    return embedder, llm


def test_vectors_come_back_in_input_order(fake: tuple[Embedder, _FakeLlama]) -> None:
    embedder, llm = fake
    texts = ["ccc", "a", "bb"]
    assert embedder.embed_documents(texts) == [truncate_and_normalize(_raw(t)) for t in texts]
    assert llm.embedded == [(texts, False, False)]  # llama.cpp neither normalises nor truncates


def test_max_tokens_is_accepted_and_one_more_is_refused(
    fake: tuple[Embedder, _FakeLlama],
) -> None:
    embedder, llm = fake
    embedder.embed_documents(["a" * MAX_TOKENS])
    with pytest.raises(EmbedError, match=f"has {MAX_TOKENS + 1} tokens") as caught:
        embedder.embed_documents(["ok", "a" * (MAX_TOKENS + 1), "ok"])
    assert caught.value.index == 1
    assert len(llm.embedded) == 1  # the refused batch embedded nothing


def test_an_empty_text_is_refused_before_tokenizing(fake: tuple[Embedder, _FakeLlama]) -> None:
    embedder, llm = fake
    with pytest.raises(EmbedError) as caught:
        embedder.embed_documents(["ok", " \x1c "])
    assert caught.value.index == 1
    assert llm.tokenized == [] and llm.embedded == []


def test_no_texts_embed_nothing(fake: tuple[Embedder, _FakeLlama]) -> None:
    embedder, llm = fake
    assert embedder.embed_documents([]) == []
    assert llm.embedded == []


def test_a_query_is_prefixed_and_an_empty_one_refused(fake: tuple[Embedder, _FakeLlama]) -> None:
    embedder, llm = fake
    embedder.embed_query("aspirin")
    assert llm.embedded[0][0] == [query_text("aspirin")]
    with pytest.raises(EmbedError) as caught:
        embedder.embed_query("   ")
    assert caught.value.index == 0
    assert len(llm.embedded) == 1


def test_a_decode_failure_is_an_embed_error(fake: tuple[Embedder, _FakeLlama]) -> None:
    embedder, llm = fake
    llm.fail = RuntimeError("llama_decode returned -3")
    with pytest.raises(EmbedError, match="llama_decode returned -3") as caught:
        embedder.embed_documents(["a"])
    assert caught.value.index is None


def test_leaving_a_with_block_frees_the_model(fake: tuple[Embedder, _FakeLlama]) -> None:
    embedder, llm = fake
    with embedder:
        pass
    assert llm.closed


def test_a_model_that_will_not_load_is_an_embed_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def refuse(**kwargs: object) -> None:
        raise ValueError(f"Model path does not exist: {kwargs['model_path']}")

    module = types.SimpleNamespace(Llama=refuse, LLAMA_POOLING_TYPE_CLS=2)
    monkeypatch.setitem(sys.modules, "llama_cpp", module)
    with pytest.raises(EmbedError, match="does not exist") as caught:
        Embedder(tmp_path / "missing.gguf")
    assert caught.value.index is None
