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

import math
import pickle
import random
import struct

import pytest

from bmlib.embed import EMBEDDING_DIM, VectorError, truncate_and_normalize
from bmlib.embed.store import (
    LABEL_FIELDS,
    ChunkSetError,
    ChunkSetLabel,
    CorruptChunkSetError,
    IndexingCancelled,
    NotIndexedError,
    StaleChunkSetError,
    UnidentifiedEmbedderError,
    decode_vector,
    encode_vector,
    rank,
    round_to_binary32,
    score,
)


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
