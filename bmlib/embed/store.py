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

"""A paper's chunks and their embeddings, stored and searched (both backends).

StudyAnalyzer's item 8. :func:`index_paper` chunks a canonical text, embeds
every chunk and stores the set under the caller's key with a label of how it
was made; :func:`search` embeds a question and returns the best chunks by
brute-force cosine, refusing a set whose label is not the current one. The
pure half here — codec, label, scorer — is held to the Rust port by
``tests/fixtures/vector_store.json``.

A vector is stored as :data:`EMBEDDING_DIM` little-endian IEEE-754 binary32
values (the layout sqlite-vec reads). A score is the dot product of binary32
values, each widened to binary64 and summed left to right, so both languages
rank identically.
"""

from __future__ import annotations

import math
import struct
from collections.abc import Sequence
from dataclasses import asdict, dataclass, fields
from typing import Any

from bmlib.embed.vectors import EMBEDDING_DIM, VectorError

# The five label fields' wire names, in label order.
LABEL_FIELDS = (
    "model_sha256",
    "recipe_version",
    "chunker_version",
    "converter_version",
    "text_sha256",
)

# A stored vector's norm, after rounding to binary32, may miss 1 by this much.
NORM_TOLERANCE = 1e-4

_CODEC = struct.Struct(f"<{EMBEDDING_DIM}f")


class ChunkSetError(ValueError):
    """A stored chunk set cannot be searched as asked; each cause is a subclass."""


class NotIndexedError(ChunkSetError):
    """No set is stored under the key."""


class StaleChunkSetError(ChunkSetError):
    """The stored set's label differs from the current one: call ``index_paper`` again.

    ``fields`` names every differing label field, in :data:`LABEL_FIELDS` order.
    """

    def __init__(self, fields: Sequence[str]) -> None:
        self.fields = tuple(fields)
        super().__init__(
            f"the stored chunk set is stale ({', '.join(self.fields)} changed); index it again"
        )

    def __reduce__(self) -> tuple[Any, ...]:  # ``args`` holds the message, not the fields
        return (type(self), (self.fields,))


class CorruptChunkSetError(ChunkSetError):
    """A stored row cannot be read as part of the set; it is never scored."""


class UnidentifiedEmbedderError(ChunkSetError):
    """The embedder carries no model identity (build it with ``Embedder.from_model``)."""


class IndexingCancelled(ChunkSetError):  # noqa: N818 - a cancel, not an error
    """The progress callback cancelled indexing; nothing was stored."""


@dataclass(frozen=True)
class ChunkSetLabel:
    """How a stored set was made; a set is current only if every field matches."""

    model_sha256: str
    recipe_version: int
    chunker_version: int
    converter_version: str
    text_sha256: str

    def differences(self, other: ChunkSetLabel) -> tuple[str, ...]:
        """Return the wire names of the fields that differ, in label order."""
        return tuple(f for f in LABEL_FIELDS if getattr(self, f) != getattr(other, f))

    def to_dict(self) -> dict[str, Any]:
        """Return the label keyed by its wire names."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChunkSetLabel:
        """Rebuild a label from :meth:`to_dict` output; ``ValueError`` if malformed."""
        types = {"recipe_version": int, "chunker_version": int}
        values: dict[str, Any] = {}
        for f in fields(cls):
            value = data.get(f.name) if isinstance(data, dict) else None
            if type(value) is not types.get(f.name, str):
                raise ValueError(f"a chunk set label's {f.name} is {value!r}")
            values[f.name] = value
        return cls(**values)


def round_to_binary32(values: Sequence[float]) -> list[float]:
    """Return each value rounded to the nearest binary32, as a float."""
    return list(struct.unpack(f"<{len(values)}f", struct.pack(f"<{len(values)}f", *values)))


def score(query: Sequence[float], vector: Sequence[float]) -> float:
    """Return the dot product of two binary32-valued vectors, summed left to right.

    Hand-rolled: ``sum()`` compensates on Python 3.12+ and not on 3.11, and
    the Rust port sums plainly.
    """
    total = 0.0
    for x, y in zip(query, vector, strict=True):
        total += x * y
    return total


def encode_vector(values: Sequence[float]) -> bytes:
    """Return a vector's stored form.

    Raises:
        VectorError: Unless it has exactly :data:`EMBEDDING_DIM` finite values
            whose norm, rounded to binary32, is within :data:`NORM_TOLERANCE` of 1.
    """
    if len(values) != EMBEDDING_DIM:
        raise VectorError(f"a stored vector needs {EMBEDDING_DIM} values, got {len(values)}")
    try:
        blob = _CODEC.pack(*values)
    except (struct.error, OverflowError) as exc:  # a value past binary32's range
        raise VectorError(f"a vector value cannot be stored as binary32: {exc}") from exc
    rounded = _CODEC.unpack(blob)
    norm = math.sqrt(score(rounded, rounded))
    if not abs(norm - 1.0) <= NORM_TOLERANCE:  # a NaN or infinity carries into the norm
        raise VectorError(f"a stored vector must be unit length, its norm is {norm}")
    return blob


def decode_vector(blob: bytes) -> list[float]:
    """Return a stored vector's values.

    Raises:
        CorruptChunkSetError: Unless it is ``4 * EMBEDDING_DIM`` bytes of finite values.
    """
    if len(blob) != _CODEC.size:
        raise CorruptChunkSetError(f"a stored vector is {len(blob)} bytes, not {_CODEC.size}")
    values = list(_CODEC.unpack(blob))
    if not all(math.isfinite(x) for x in values):
        raise CorruptChunkSetError("a stored vector holds a value that is not finite")
    return values


def rank(
    query: Sequence[float], vectors: Sequence[Sequence[float]], k: int
) -> list[tuple[int, float]]:
    """Return the best ``k`` of ``vectors`` as ``(index, score)``, best first.

    ``query`` is rounded to binary32 first, as the stored vectors are; an
    exact tie goes to the lower index. A ``k`` above the count returns all.

    Raises:
        ValueError: If ``k`` is not a positive integer.
    """
    if type(k) is not int or k < 1:
        raise ValueError(f"k must be a positive integer, got {k!r}")
    rounded = round_to_binary32(query)
    scored = [(index, score(rounded, vector)) for index, vector in enumerate(vectors)]
    scored.sort(key=lambda hit: (-hit[1], hit[0]))
    return scored[:k]
