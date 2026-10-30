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

"""The vector store for a paper's chunks (StudyAnalyzer item 8).

The pure half — codec, label, scorer — is also held to the shared
``tests/fixtures/vector_store.json`` (``test_vector_store_contract.py``); the
database half runs on SQLite and, when configured, PostgreSQL.
"""

from __future__ import annotations

import dataclasses
import hashlib
import math
import pickle
import random
import struct
from collections.abc import Sequence
from typing import Any

import pytest

import bmlib.embed.store
from bmlib.db import connect_sqlite, execute, fetch_all, fetch_scalar, placeholder
from bmlib.embed import (
    ARCTIC_EMBED_L_V2_F16,
    ARCTIC_EMBED_L_V2_Q8_0,
    EMBEDDING_DIM,
    ModelFile,
    TextTooLongError,
    VectorError,
    query_text,
    truncate_and_normalize,
)
from bmlib.embed.store import (
    INDEX_BATCH_SIZE,
    LABEL_FIELDS,
    ChunkHit,
    ChunkSetError,
    ChunkSetLabel,
    CorruptChunkSetError,
    IndexingCancelled,
    NotIndexedError,
    StaleChunkSetError,
    UnidentifiedEmbedderError,
    decode_vector,
    delete_paper,
    encode_vector,
    ensure_schema,
    index_paper,
    rank,
    round_to_binary32,
    score,
    search,
)
from bmlib.fulltext.canonical import CanonicalText, TextSpan, canonical_from_plain_text


def _unit(seed: int) -> list[float]:
    rng = random.Random(seed)
    return truncate_and_normalize([rng.gauss(0.0, 1.0) for _ in range(EMBEDDING_DIM)])


def test_a_vector_round_trips() -> None:
    vector = _unit(0)
    blob = encode_vector(vector)
    assert len(blob) == 4 * EMBEDDING_DIM
    assert decode_vector(blob) == round_to_binary32(vector)


@pytest.mark.parametrize(
    "values",
    [
        _unit(1)[:-1],
        _unit(1) + [0.0],
        [math.nan] + _unit(1)[1:],
        [math.inf] + _unit(1)[1:],
        [x * 0.5 for x in _unit(1)],
        [x * 1.001 for x in _unit(1)],
    ],
    ids=["255 values", "257 values", "nan", "inf", "norm 0.5", "norm 1.001"],
)
def test_encode_refuses(values: list[float]) -> None:
    with pytest.raises(VectorError):
        encode_vector(values)


def test_decode_refuses_a_short_blob() -> None:
    with pytest.raises(CorruptChunkSetError, match="1023 bytes"):
        decode_vector(encode_vector(_unit(2))[:-1])


def test_decode_refuses_a_nan() -> None:
    blob = struct.pack("<f", math.nan) + encode_vector(_unit(2))[4:]
    with pytest.raises(CorruptChunkSetError):
        decode_vector(blob)


def _left_to_right(a: list[float], b: list[float]) -> float:
    total = 0.0
    for x, y in zip(round_to_binary32(a), round_to_binary32(b), strict=True):
        total += x * y
    return total


def test_score_sums_left_to_right() -> None:
    fsum_differs = False
    for seed in range(50):
        a, b = _unit(seed), _unit(seed + 1000)
        assert score(round_to_binary32(a), round_to_binary32(b)) == _left_to_right(a, b)
        products = [x * y for x, y in zip(round_to_binary32(a), round_to_binary32(b), strict=True)]
        fsum_differs |= math.fsum(products) != _left_to_right(a, b)
    assert fsum_differs  # so a compensated sum would be caught


def test_rank_breaks_ties_by_chunk_index() -> None:
    same = round_to_binary32(_unit(3))
    other = round_to_binary32(_unit(4))
    hits = rank(same, [other, same, same, other], 3)
    assert [index for index, _ in hits] == [1, 2, 0]  # 0 and 3 tie too
    assert hits[0][1] == hits[1][1]


def test_rank_orders_by_score() -> None:
    query = _unit(5)
    vectors = [round_to_binary32(_unit(s)) for s in range(6, 16)]
    hits = rank(query, vectors, 10)
    scores = [s for _, s in hits]
    assert scores == sorted(scores, reverse=True)
    assert hits[0][1] == max(score(round_to_binary32(query), v) for v in vectors)


def test_rank_returns_the_set_when_k_exceeds_it() -> None:
    vectors = [round_to_binary32(_unit(s)) for s in range(3)]
    assert len(rank(_unit(9), vectors, 10)) == 3


@pytest.mark.parametrize("k", [0, -1, True, 1.0])
def test_rank_refuses_k(k: object) -> None:
    with pytest.raises(ValueError, match="k must be"):
        rank(_unit(9), [round_to_binary32(_unit(1))], k)  # type: ignore[arg-type]


_LABEL = ChunkSetLabel("a" * 64, 1, 1, "jats-1", "b" * 64)


def test_label_fields_are_in_order() -> None:
    assert LABEL_FIELDS == (
        "model_sha256",
        "recipe_version",
        "chunker_version",
        "converter_version",
        "text_sha256",
    )


def test_differences_names_every_field_in_order() -> None:
    assert _LABEL.differences(_LABEL) == ()
    other = ChunkSetLabel("c" * 64, 2, 2, "jats-2", "d" * 64)
    assert _LABEL.differences(other) == LABEL_FIELDS
    assert ChunkSetLabel.from_dict(_LABEL.to_dict()) == _LABEL


@pytest.mark.parametrize(
    "error",
    [
        NotIndexedError("never indexed"),
        StaleChunkSetError(("model_sha256", "text_sha256")),
        CorruptChunkSetError("bad row"),
        UnidentifiedEmbedderError("no model"),
        IndexingCancelled("cancelled"),
    ],
    ids=lambda e: type(e).__name__,
)
def test_errors_pickle(error: ChunkSetError) -> None:
    copy = pickle.loads(pickle.dumps(error))
    assert type(copy) is type(error) and str(copy) == str(error)
    assert isinstance(copy, ValueError)
    if isinstance(error, StaleChunkSetError):
        assert copy.fields == error.fields
        assert "model_sha256" in str(error) and "text_sha256" in str(error)


# --- the database half -------------------------------------------------------


class FakeEmbedder:
    """An embedder that needs no model: a text's vector comes from its sha256."""

    def __init__(self, model: ModelFile | None = ARCTIC_EMBED_L_V2_F16) -> None:
        self.model = model
        self.documents: list[str] = []
        self.queries: list[str] = []
        self.batches = 0
        self.fail_on_batch: int | None = None

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.batches += 1
        if self.batches == self.fail_on_batch:
            raise TextTooLongError(0, 9999)
        self.documents.extend(texts)
        return [_vector_of(t) for t in texts]

    def embed_query(self, query: str) -> list[float]:
        self.queries.append(query)
        return _vector_of(query_text(query))


def _vector_of(text: str) -> list[float]:
    digest = hashlib.sha256(text.encode()).digest() * 8
    return truncate_and_normalize([b - 127.5 for b in digest[:EMBEDDING_DIM]])


def _paper(paragraphs: int = 40, word: str = "aspirin") -> CanonicalText:
    sentence = f"Patients given {word} in trial {{}} had fewer events than controls. "
    body = "\n\n".join(sentence.format(i) * 8 for i in range(paragraphs))
    return canonical_from_plain_text(body, title="A trial")


@pytest.fixture
def conn(backend_conn: Any) -> Any:
    ensure_schema(backend_conn)
    return backend_conn


def _count(conn: Any, key: str) -> int:
    p = placeholder(conn)
    return fetch_scalar(conn, f"SELECT COUNT(*) FROM embedded_chunks WHERE paper_key = {p}", [key])


def test_ensure_schema_is_idempotent(conn: Any) -> None:
    ensure_schema(conn)
    assert fetch_scalar(conn, "SELECT COUNT(*) FROM embedded_chunk_sets") == 0


def test_index_then_search(conn: Any) -> None:
    paper = _paper()
    count = index_paper(conn, "pmid:1", paper, FakeEmbedder())
    assert count > INDEX_BATCH_SIZE and _count(conn, "pmid:1") == count
    hits = search(conn, "pmid:1", paper, "fewer events", FakeEmbedder(), 5)
    assert len(hits) == 5 and all(isinstance(h, ChunkHit) for h in hits)
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)
    assert all(0 <= h.span.start < h.span.end <= len(paper.text) for h in hits)
    assert ChunkHit.from_dict(hits[0].to_dict()) == hits[0]


def test_the_best_chunk_is_the_one_the_question_matches(conn: Any) -> None:
    paper = _paper()
    embedder = FakeEmbedder()
    index_paper(conn, "k", paper, embedder)
    # The fake embeds the question to exactly one chunk's vector.
    target = embedder.documents[7]
    embedder.embed_query = lambda q: _vector_of(target)  # type: ignore[method-assign]
    assert search(conn, "k", paper, "anything", embedder, 1)[0].chunk_index == 7


def test_the_question_uses_the_query_path(conn: Any) -> None:
    paper = _paper(2)
    embedder = FakeEmbedder()
    index_paper(conn, "k", paper, embedder)
    search(conn, "k", paper, "fewer events", embedder, 3)
    assert embedder.queries == ["fewer events"]
    assert not any(t.startswith("query: ") for t in embedder.documents)


def test_reindexing_replaces_the_set(conn: Any) -> None:
    index_paper(conn, "k", _paper(40), FakeEmbedder())
    small = _paper(1)
    count = index_paper(conn, "k", small, FakeEmbedder())
    assert _count(conn, "k") == count
    assert len(search(conn, "k", small, "q", FakeEmbedder(), 100)) == count


def test_other_papers_are_untouched(conn: Any) -> None:
    index_paper(conn, "a", _paper(3), FakeEmbedder())
    count = index_paper(conn, "b", _paper(2), FakeEmbedder())
    index_paper(conn, "a", _paper(1), FakeEmbedder())
    delete_paper(conn, "a")
    assert _count(conn, "b") == count


def test_an_empty_text_is_an_empty_set(conn: Any) -> None:
    empty = canonical_from_plain_text("")
    assert index_paper(conn, "k", empty, FakeEmbedder()) == 0
    embedder = FakeEmbedder()
    assert search(conn, "k", empty, "q", embedder, 3) == []
    assert embedder.queries == []


def test_a_never_indexed_key_raises(conn: Any) -> None:
    with pytest.raises(NotIndexedError):
        search(conn, "nobody", _paper(1), "q", FakeEmbedder(), 3)


def _stale_fields(conn: Any, paper: CanonicalText, embedder: FakeEmbedder) -> tuple[str, ...]:
    with pytest.raises(StaleChunkSetError) as caught:
        search(conn, "k", paper, "q", embedder, 3)
    return caught.value.fields


def test_a_different_model_file_is_stale(conn: Any) -> None:
    paper = _paper(2)
    index_paper(conn, "k", paper, FakeEmbedder(ARCTIC_EMBED_L_V2_Q8_0))
    assert _stale_fields(conn, paper, FakeEmbedder(ARCTIC_EMBED_L_V2_F16)) == ("model_sha256",)


def test_a_new_recipe_is_stale(conn: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    paper = _paper(2)
    index_paper(conn, "k", paper, FakeEmbedder())
    monkeypatch.setattr(bmlib.embed.store, "EMBEDDING_RECIPE_VERSION", 2)
    assert _stale_fields(conn, paper, FakeEmbedder()) == ("recipe_version",)


def test_a_new_chunker_is_stale(conn: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    paper = _paper(2)
    index_paper(conn, "k", paper, FakeEmbedder())
    monkeypatch.setattr(bmlib.embed.store, "CHUNKER_VERSION", 2)
    assert _stale_fields(conn, paper, FakeEmbedder()) == ("chunker_version",)


def test_another_converter_is_stale(conn: Any) -> None:
    paper = _paper(2)
    index_paper(conn, "k", paper, FakeEmbedder())
    other = dataclasses.replace(paper, converter_version="plain-99")
    assert _stale_fields(conn, other, FakeEmbedder()) == ("converter_version",)


def test_different_text_is_stale(conn: Any) -> None:
    index_paper(conn, "k", _paper(2), FakeEmbedder())
    assert _stale_fields(conn, _paper(2, "heparin"), FakeEmbedder()) == ("text_sha256",)


def test_an_embedder_without_identity_is_refused(conn: Any) -> None:
    paper = _paper(2)
    anonymous = FakeEmbedder(model=None)
    with pytest.raises(UnidentifiedEmbedderError):
        index_paper(conn, "k", paper, anonymous)
    assert anonymous.documents == [] and _count(conn, "k") == 0
    index_paper(conn, "k", paper, FakeEmbedder())
    with pytest.raises(UnidentifiedEmbedderError):
        search(conn, "k", paper, "q", anonymous, 3)
    assert anonymous.queries == []


def test_an_embedder_failure_keeps_the_old_set(conn: Any) -> None:
    old = _paper(3)
    count = index_paper(conn, "k", old, FakeEmbedder())
    failing = FakeEmbedder()
    failing.fail_on_batch = 2
    with pytest.raises(TextTooLongError):
        index_paper(conn, "k", _paper(40, "heparin"), failing)
    assert _count(conn, "k") == count
    assert search(conn, "k", old, "q", FakeEmbedder(), 1)


def test_a_cancel_keeps_the_old_set(conn: Any) -> None:
    old = _paper(3)
    count = index_paper(conn, "k", old, FakeEmbedder())
    with pytest.raises(IndexingCancelled):
        index_paper(conn, "k", _paper(40, "heparin"), FakeEmbedder(), lambda done, total: False)
    assert _count(conn, "k") == count


def test_progress_reaches_the_total(conn: Any) -> None:
    seen: list[tuple[int, int]] = []
    count = index_paper(conn, "k", _paper(), FakeEmbedder(), lambda d, t: seen.append((d, t)))
    assert seen[-1] == (count, count)
    assert [d for d, _ in seen] == sorted({d for d, _ in seen})
    assert seen[0] == (INDEX_BATCH_SIZE, count)


def test_a_set_missing_rows_is_corrupt(conn: Any) -> None:
    paper = _paper(3)
    index_paper(conn, "k", paper, FakeEmbedder())
    p = placeholder(conn)
    execute(conn, f"DELETE FROM embedded_chunks WHERE chunk_index = {p}", [1])
    conn.commit()
    with pytest.raises(CorruptChunkSetError):
        search(conn, "k", paper, "q", FakeEmbedder(), 3)


def test_a_bad_blob_is_corrupt(conn: Any) -> None:
    paper = _paper(3)
    index_paper(conn, "k", paper, FakeEmbedder())
    p = placeholder(conn)
    execute(conn, f"UPDATE embedded_chunks SET embedding = {p}", [b"\0" * 1023])
    conn.commit()
    with pytest.raises(CorruptChunkSetError, match="1023 bytes"):
        search(conn, "k", paper, "q", FakeEmbedder(), 3)


def test_a_span_outside_the_text_is_corrupt(conn: Any) -> None:
    paper = _paper(3)
    index_paper(conn, "k", paper, FakeEmbedder())
    p = placeholder(conn)
    execute(conn, f"UPDATE embedded_chunks SET span_end = {p}", [len(paper.text) + 1])
    conn.commit()
    with pytest.raises(CorruptChunkSetError):
        search(conn, "k", paper, "q", FakeEmbedder(), 3)


def test_delete_paper(conn: Any) -> None:
    paper = _paper(2)
    index_paper(conn, "k", paper, FakeEmbedder())
    delete_paper(conn, "k")
    delete_paper(conn, "k")  # an absent key is not an error
    assert _count(conn, "k") == 0
    with pytest.raises(NotIndexedError):
        search(conn, "k", paper, "q", FakeEmbedder(), 3)


def test_delete_needs_no_cascade() -> None:
    conn = connect_sqlite(":memory:", foreign_keys=False)
    ensure_schema(conn)
    index_paper(conn, "k", _paper(3), FakeEmbedder())
    count = index_paper(conn, "k", _paper(1), FakeEmbedder())
    assert _count(conn, "k") == count
    delete_paper(conn, "k")
    assert fetch_all(conn, "SELECT * FROM embedded_chunks") == []


def test_a_lone_surrogate_is_refused(conn: Any) -> None:
    paper = canonical_from_plain_text("Text with a lone \ud800 surrogate.")
    embedder = FakeEmbedder()
    with pytest.raises(ValueError, match="surrogate"):
        index_paper(conn, "k", paper, embedder)
    assert embedder.documents == []


@pytest.mark.parametrize("key", ["", None, 1])
def test_bad_keys_are_refused(conn: Any, key: object) -> None:
    with pytest.raises(ValueError, match="key"):
        index_paper(conn, key, _paper(1), FakeEmbedder())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="key"):
        search(conn, key, _paper(1), "q", FakeEmbedder(), 3)  # type: ignore[arg-type]


def test_a_hit_carries_its_section(conn: Any) -> None:
    paper = _paper(2)
    index_paper(conn, "k", paper, FakeEmbedder())
    for hit in search(conn, "k", paper, "q", FakeEmbedder(), 10):
        blocks = paper.sections[hit.section_index].blocks
        assert blocks[0].span.start <= hit.span.start and hit.span.end <= blocks[-1].span.end
        assert isinstance(hit.span, TextSpan)


class _SaltedEmbedder(FakeEmbedder):
    """Another model: different vectors for the same texts."""

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        super().embed_documents(texts)  # the bookkeeping only
        return [_vector_of("salt" + t) for t in texts]


def test_a_search_racing_a_reindex_scores_one_snapshot(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Review finding: the label and the rows were two statements, so a
    # re-index between them had another model's vectors scored as this one's.
    path = tmp_path / "store.db"
    searching, racing = connect_sqlite(path), connect_sqlite(path)
    ensure_schema(searching)
    paper = _paper(3)
    index_paper(searching, "k", paper, FakeEmbedder(ARCTIC_EMBED_L_V2_Q8_0))
    embedder = FakeEmbedder(ARCTIC_EMBED_L_V2_Q8_0)
    expected = search(searching, "k", paper, "q", embedder, 5)

    raced = False

    def reindex_once() -> None:
        nonlocal raced
        if not raced:
            raced = True
            index_paper(racing, "k", paper, _SaltedEmbedder(ARCTIC_EMBED_L_V2_F16))

    for name in ("fetch_one", "fetch_all", "fetch_scalar"):  # whichever the store reads with
        original = getattr(bmlib.embed.store, name, None)
        if original is None:
            continue

        def hooked(*args: Any, _original: Any = original) -> Any:
            result = _original(*args)
            reindex_once()
            return result

        monkeypatch.setattr(bmlib.embed.store, name, hooked)
    assert search(searching, "k", paper, "q", embedder, 5) == expected
    assert raced
    with pytest.raises(StaleChunkSetError):  # the next search sees the new set
        search(searching, "k", paper, "q", embedder, 5)


class _QueryEmbedder(FakeEmbedder):
    def __init__(self, query: list[float]) -> None:
        super().__init__()
        self.query = query

    def embed_query(self, query: str) -> list[float]:
        return self.query


@pytest.mark.parametrize(
    "query",
    [
        _unit(1)[:128],
        _unit(1) * 4,
        [math.nan] + _unit(1)[1:],
        [x * 0.5 for x in _unit(1)],
    ],
    ids=["128 values", "1024 values", "nan", "norm 0.5"],
)
def test_a_bad_query_vector_is_refused(conn: Any, query: list[float]) -> None:
    paper = _paper(2)
    index_paper(conn, "k", paper, FakeEmbedder())
    with pytest.raises(VectorError):
        search(conn, "k", paper, "q", _QueryEmbedder(query), 3)


def test_a_set_recording_no_chunks_beside_rows_is_corrupt(conn: Any) -> None:
    paper = _paper(3)
    index_paper(conn, "k", paper, FakeEmbedder())
    execute(conn, "UPDATE embedded_chunk_sets SET chunk_count = 0")
    conn.commit()
    with pytest.raises(CorruptChunkSetError):
        search(conn, "k", paper, "q", FakeEmbedder(), 3)
