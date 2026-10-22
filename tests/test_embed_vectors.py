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

"""Tests for the embedder's pure rules, driven by the shared contract fixture."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from bmlib.embed import (
    ARCTIC_EMBED_L_V2_F16,
    ARCTIC_EMBED_L_V2_Q8_0,
    EMBEDDING_DIM,
    MAX_TOKENS,
    QUERY_PREFIX,
    EmptyTextError,
    VectorError,
    check_texts,
    query_text,
    truncate_and_normalize,
)

FIXTURES = Path(__file__).parent / "fixtures"
CONTRACT = json.loads((FIXTURES / "embedding_contract.json").read_text())
MANIFEST = json.loads((FIXTURES / "embedding_model_manifest.json").read_text())
TOLERANCE = 1e-6


def test_every_contract_section_is_read() -> None:
    # A section added for one language must be read by both suites.
    assert set(CONTRACT) == {
        "_readme",
        "truncate_and_normalize",
        "query_text",
        "check_texts",
        "model_files",
    }


def test_constants() -> None:
    assert (EMBEDDING_DIM, QUERY_PREFIX, MAX_TOKENS) == (256, "query: ", 8192)


@pytest.mark.parametrize("case", CONTRACT["truncate_and_normalize"], ids=lambda c: c["name"])
def test_truncate_and_normalize(case: dict) -> None:
    vector = [float(x) for x in case["input"]]  # a non-finite value is written as a string
    if case["expected"] == "error":
        with pytest.raises(VectorError):
            truncate_and_normalize(vector)
        return
    got = truncate_and_normalize(vector)
    assert len(got) == len(case["expected"]) == EMBEDDING_DIM
    assert all(math.isclose(g, e, abs_tol=TOLERANCE) for g, e in zip(got, case["expected"]))


def test_a_norm_past_a_floats_range_is_refused() -> None:
    # Only a double can overflow the norm; the contract's f32 values cannot.
    with pytest.raises(VectorError, match="norm is inf"):
        truncate_and_normalize([1e200] * EMBEDDING_DIM)


@pytest.mark.parametrize("case", CONTRACT["query_text"], ids=lambda c: c["name"])
def test_query_text(case: dict) -> None:
    assert query_text(case["input"]) == case["expected"]


@pytest.mark.parametrize("case", CONTRACT["check_texts"], ids=lambda c: c["name"])
def test_check_texts(case: dict) -> None:
    if case["error_index"] is None:
        check_texts(case["texts"])
        return
    with pytest.raises(EmptyTextError) as caught:
        check_texts(case["texts"])
    assert caught.value.index == case["error_index"]
    assert isinstance(caught.value, ValueError)


@pytest.mark.parametrize("case", CONTRACT["model_files"], ids=lambda c: c["name"])
def test_model_urls_match_contract(case: dict) -> None:
    model = {"F16": ARCTIC_EMBED_L_V2_F16, "Q8_0": ARCTIC_EMBED_L_V2_Q8_0}[case["name"]]
    assert model.url() == case["url"]


@pytest.mark.parametrize(
    "model", [ARCTIC_EMBED_L_V2_F16, ARCTIC_EMBED_L_V2_Q8_0], ids=["F16", "Q8_0"]
)
def test_constants_equal_manifest(model) -> None:
    entry = next(f for f in MANIFEST["files"] if f["file"] == model.file)
    assert (model.repo, model.revision) == (MANIFEST["repo"], MANIFEST["revision"])
    assert (model.size, model.sha256) == (entry["size"], entry["sha256"])
