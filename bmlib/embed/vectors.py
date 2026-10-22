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

"""Embedding constants and the pure vector rules shared with the Rust crate.

The behaviour here is pinned by ``tests/fixtures/embedding_contract.json``,
which the Rust tests read too.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

EMBEDDING_DIM = 256
QUERY_PREFIX = "query: "
MAX_TOKENS = 8192
NORM_FLOOR = 1e-12


def truncate_and_normalize(vector: Sequence[float]) -> list[float]:
    """Keep the first :data:`EMBEDDING_DIM` values and scale them to unit length.

    Args:
        vector: A model output of at least ``EMBEDDING_DIM`` values.

    Returns:
        The truncated, L2-normalised vector. A norm below :data:`NORM_FLOOR`
        is replaced by it, so an all-zero head gives zeros.

    Raises:
        ValueError: If the vector is shorter than ``EMBEDDING_DIM``.
    """
    if len(vector) < EMBEDDING_DIM:
        raise ValueError(f"vector has {len(vector)} values, need at least {EMBEDDING_DIM}")
    head = [float(x) for x in vector[:EMBEDDING_DIM]]
    norm = max(math.sqrt(sum(x * x for x in head)), NORM_FLOOR)
    return [x / norm for x in head]


def query_text(query: str) -> str:
    """Return the text the model embeds for a search query."""
    return QUERY_PREFIX + query


class EmptyTextError(ValueError):
    """A text was empty or whitespace-only.

    Attributes:
        index: Position of the first such text.
    """

    def __init__(self, index: int) -> None:
        super().__init__(f"text at index {index} is empty or whitespace-only")
        self.index = index


def check_texts(texts: Sequence[str]) -> None:
    """Refuse empty or whitespace-only texts.

    Raises:
        EmptyTextError: A ValueError carrying the index of the first such text.
    """
    for index, text in enumerate(texts):
        if not text.strip():
            raise EmptyTextError(index)
