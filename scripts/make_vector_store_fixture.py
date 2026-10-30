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

"""Write ``tests/fixtures/vector_store.json`` from the Python vector store.

The shared contract for the store's pure half — the stored byte layout, the
vector checks, the scorer and its order, the label comparison — which Python
and Rust both assert against. Every float is written as the 16 hex digits of
its binary64 bits (big-endian) and every stored vector as its blob's hex, so
neither language parses a decimal.

    python scripts/make_vector_store_fixture.py [tests/fixtures/vector_store.json]

``tests/test_vector_store_contract.py`` checks that this script still builds
the committed fixture, so add a case here and re-run it rather than editing
the JSON by hand.
"""

from __future__ import annotations

import json
import math
import random
import struct
import sys
from pathlib import Path
from typing import Any

from bmlib.embed import EMBEDDING_DIM, EMBEDDING_RECIPE_VERSION, VectorError, truncate_and_normalize
from bmlib.embed.store import (
    INDEX_BATCH_SIZE,
    LABEL_FIELDS,
    NORM_TOLERANCE,
    ChunkSetLabel,
    CorruptChunkSetError,
    decode_vector,
    encode_vector,
    rank,
    round_to_binary32,
)

DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "tests/fixtures/vector_store.json"


def f64(value: float) -> str:
    """The 16 hex digits of a binary64's bits, big-endian."""
    return struct.pack(">d", value).hex()


def unit(seed: int) -> list[float]:
    """A seeded random unit vector, as the embedder would hand it over."""
    rng = random.Random(seed)
    return truncate_and_normalize([rng.gauss(0.0, 1.0) for _ in range(EMBEDDING_DIM)])


def encode_cases() -> list[dict[str, Any]]:
    """Vectors offered for storage: their blob, or refused."""
    base = unit(1)
    offered = [
        ("unit", base),
        ("unit-other", unit(2)),
        ("norm-just-inside-tolerance", [x * (1 + 0.9 * NORM_TOLERANCE) for x in base]),
        ("norm-just-outside-tolerance", [x * (1 + 1.1 * NORM_TOLERANCE) for x in base]),
        ("norm-just-inside-below", [x * (1 - 0.9 * NORM_TOLERANCE) for x in base]),
        ("norm-half", [x * 0.5 for x in base]),
        ("too-few-values", base[:-1]),
        ("too-many-values", base + [0.0]),
        ("nan", [math.nan] + base[1:]),
        ("infinity", [math.inf] + base[1:]),
        ("past-binary32-range", [1e39] + base[1:]),
        ("zero", [0.0] * EMBEDDING_DIM),
    ]
    cases = []
    for name, values in offered:
        case: dict[str, Any] = {"name": name, "values": [f64(v) for v in values]}
        try:
            case["blob"] = encode_vector(values).hex()
        except VectorError:
            case["refused"] = True
        cases.append(case)
    return cases


def decode_cases() -> list[dict[str, Any]]:
    """Stored blobs read back: their values, or refused as corrupt."""
    good = encode_vector(unit(3))
    blobs = [
        ("round-trip", good),
        ("one-byte-short", good[:-1]),
        ("one-byte-long", good + b"\0"),
        ("empty", b""),
        ("nan", struct.pack("<f", math.nan) + good[4:]),
        ("infinity", struct.pack("<f", math.inf) + good[4:]),
    ]
    cases = []
    for name, blob in blobs:
        case: dict[str, Any] = {"name": name, "blob": blob.hex()}
        try:
            case["values"] = [f64(v) for v in decode_vector(blob)]
        except CorruptChunkSetError:
            case["refused"] = True
        cases.append(case)
    return cases


def rank_cases() -> list[dict[str, Any]]:
    """A query against stored vectors: the best ``k`` and their exact scores."""
    random_set = [round_to_binary32(unit(s)) for s in range(100, 112)]
    same, other = round_to_binary32(unit(7)), round_to_binary32(unit(8))
    offered = [
        ("random-k5", unit(9), random_set, 5),
        ("k-above-the-set", unit(10), random_set[:3], 10),
        ("k-one", unit(11), random_set, 1),
        ("ties-go-to-the-lower-index", same, [other, same, same, other, same], 4),
        ("query-not-yet-binary32", unit(12), random_set, 12),
    ]
    cases = []
    for name, query, vectors, k in offered:
        cases.append(
            {
                "name": name,
                "query": [f64(v) for v in query],
                "vectors": [encode_vector(v).hex() for v in vectors],
                "k": k,
                "expected": [
                    {"chunk_index": index, "score": f64(score)}
                    for index, score in rank(query, vectors, k)
                ],
            }
        )
    return cases


def label_cases() -> list[dict[str, Any]]:
    """Two labels and the fields a stale error names, in label order."""
    stored = ChunkSetLabel("a" * 64, 1, 1, "jats-1", "b" * 64)
    changed = ChunkSetLabel("c" * 64, 2, 2, "jats-2", "d" * 64)
    pairs = [("identical", stored)]
    for field in LABEL_FIELDS:
        pairs.append(
            (f"only-{field}", ChunkSetLabel(**{**stored.to_dict(), field: getattr(changed, field)}))
        )
    pairs.append(
        (
            "model-and-text",
            ChunkSetLabel(
                **{
                    **stored.to_dict(),
                    "model_sha256": changed.model_sha256,
                    "text_sha256": changed.text_sha256,
                }
            ),
        )
    )
    pairs.append(("every-field", changed))
    return [
        {
            "name": name,
            "stored": stored.to_dict(),
            "current": current.to_dict(),
            "differs": list(stored.differences(current)),
        }
        for name, current in pairs
    ]


def build_contract() -> dict[str, Any]:
    """The whole fixture."""
    return {
        "_readme": (
            "The vector store's shared contract (scripts/make_vector_store_fixture.py). "
            "Floats are binary64 bits as 16 big-endian hex digits; blobs are hex. "
            "A query is rounded to binary32 before scoring."
        ),
        "embedding_dim": EMBEDDING_DIM,
        "recipe_version": EMBEDDING_RECIPE_VERSION,
        "norm_tolerance": f64(NORM_TOLERANCE),
        "index_batch_size": INDEX_BATCH_SIZE,
        "label_fields": list(LABEL_FIELDS),
        "encode": encode_cases(),
        "decode": decode_cases(),
        "rank": rank_cases(),
        "labels": label_cases(),
    }


def main() -> None:
    """Write the contract to the path given, or to the committed fixture."""
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT
    output.write_text(json.dumps(build_contract(), indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
