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

"""The vector store's pure half, held to the shared ``tests/fixtures/vector_store.json``.

The Rust port asserts the same file (``rust/bmlib/tests/vector_store_contract.rs``).
"""

from __future__ import annotations

import importlib.util
import json
import struct
import sys
from pathlib import Path
from typing import Any

import pytest

from bmlib.embed import EMBEDDING_DIM, EMBEDDING_RECIPE_VERSION, VectorError
from bmlib.embed.store import (
    INDEX_BATCH_SIZE,
    LABEL_FIELDS,
    NORM_TOLERANCE,
    ChunkSetLabel,
    CorruptChunkSetError,
    decode_vector,
    encode_vector,
    rank,
)

FIXTURE = Path(__file__).parent / "fixtures" / "vector_store.json"
CONTRACT: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "make_vector_store_fixture.py"


def _f64(hex_bits: str) -> float:
    value: float = struct.unpack(">d", bytes.fromhex(hex_bits))[0]
    return value


def _ids(section: str) -> list[str]:
    return [c["name"] for c in CONTRACT[section]]


def test_every_section_is_read() -> None:
    # A section added for one language must be read by both suites.
    assert set(CONTRACT) == {
        "_readme",
        "embedding_dim",
        "recipe_version",
        "norm_tolerance",
        "index_batch_size",
        "label_fields",
        "encode",
        "decode",
        "rank",
        "labels",
    }


def test_the_constants_are_the_contracts() -> None:
    assert CONTRACT["embedding_dim"] == EMBEDDING_DIM
    assert CONTRACT["recipe_version"] == EMBEDDING_RECIPE_VERSION
    assert _f64(CONTRACT["norm_tolerance"]) == NORM_TOLERANCE
    assert CONTRACT["index_batch_size"] == INDEX_BATCH_SIZE
    assert tuple(CONTRACT["label_fields"]) == LABEL_FIELDS


def test_both_outcomes_are_exercised() -> None:
    for section in ("encode", "decode"):
        outcomes = {"refused" in c for c in CONTRACT[section]}
        assert outcomes == {True, False}, section


@pytest.mark.parametrize("case", CONTRACT["encode"], ids=_ids("encode"))
def test_encode(case: dict[str, Any]) -> None:
    values = [_f64(v) for v in case["values"]]
    if case.get("refused"):
        with pytest.raises(VectorError):
            encode_vector(values)
    else:
        assert encode_vector(values).hex() == case["blob"]


@pytest.mark.parametrize("case", CONTRACT["decode"], ids=_ids("decode"))
def test_decode(case: dict[str, Any]) -> None:
    blob = bytes.fromhex(case["blob"])
    if case.get("refused"):
        with pytest.raises(CorruptChunkSetError):
            decode_vector(blob)
    else:
        assert decode_vector(blob) == [_f64(v) for v in case["values"]]


@pytest.mark.parametrize("case", CONTRACT["rank"], ids=_ids("rank"))
def test_rank(case: dict[str, Any]) -> None:
    vectors = [decode_vector(bytes.fromhex(b)) for b in case["vectors"]]
    hits = rank([_f64(v) for v in case["query"]], vectors, case["k"])
    assert hits == [(e["chunk_index"], _f64(e["score"])) for e in case["expected"]]


@pytest.mark.parametrize("case", CONTRACT["labels"], ids=_ids("labels"))
def test_labels(case: dict[str, Any]) -> None:
    stored = ChunkSetLabel.from_dict(case["stored"])
    current = ChunkSetLabel.from_dict(case["current"])
    assert list(stored.differences(current)) == case["differs"]


def test_the_generator_still_builds_the_fixture() -> None:
    """A case edited into the JSON by hand, not into the script, is lost on regeneration."""
    spec = importlib.util.spec_from_file_location("bmlib_make_vector_store", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    make = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = make
    spec.loader.exec_module(make)
    assert make.build_contract() == CONTRACT
