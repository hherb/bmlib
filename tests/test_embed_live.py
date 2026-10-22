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

Skipped unless ``BMLIB_EMBED_MODEL`` names a GGUF file. Task 7 ports these,
with the same names and tolerances, to the Rust crate.
"""

from __future__ import annotations

import builtins
import json
import math
import os
from pathlib import Path

import pytest

from bmlib.embed import EMBEDDING_DIM, MAX_TOKENS, Embedder, EmbedError, query_text

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
    with pytest.raises(EmbedError, match=r"bmlib\[embed\]"):
        Embedder(Path("nonexistent.gguf"))


def test_a_str_is_not_a_list_of_texts() -> None:
    embedder = Embedder.__new__(Embedder)  # no model: the check comes first
    with pytest.raises(TypeError, match="not a str"):
        embedder.embed_documents("aspirin")
