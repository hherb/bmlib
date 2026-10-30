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

import hashlib
import math
import struct
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime
from typing import Any, Protocol

from bmlib.db import (
    create_tables,
    execute,
    executemany,
    fetch_all,
    is_sqlite,
    placeholder,
    placeholders,
    transaction,
)
from bmlib.embed.model import ModelFile
from bmlib.embed.vectors import EMBEDDING_DIM, EMBEDDING_RECIPE_VERSION, VectorError
from bmlib.fulltext.canonical import CHUNKER_VERSION, CanonicalText, TextSpan, chunk_canonical

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

# Chunks embedded per call, and per progress report.
INDEX_BATCH_SIZE = 16

_CODEC = struct.Struct(f"<{EMBEDDING_DIM}f")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS embedded_chunk_sets (
    paper_key TEXT PRIMARY KEY,
    model_sha256 TEXT NOT NULL,
    recipe_version INTEGER NOT NULL,
    chunker_version INTEGER NOT NULL,
    converter_version TEXT NOT NULL,
    text_sha256 TEXT NOT NULL,
    chunk_count INTEGER NOT NULL,
    indexed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS embedded_chunks (
    paper_key TEXT NOT NULL REFERENCES embedded_chunk_sets(paper_key) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    section_index INTEGER NOT NULL,
    span_start INTEGER NOT NULL,
    span_end INTEGER NOT NULL,
    embedding {blob} NOT NULL,
    PRIMARY KEY (paper_key, chunk_index)
);
"""


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


class ChunkEmbedder(Protocol):
    """What the store needs of an embedder; :class:`~bmlib.embed.Embedder` is one.

    ``model`` is the file the vectors come from; ``None`` is refused.
    """

    model: ModelFile | None

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed texts as documents, in order."""
        ...

    def embed_query(self, query: str) -> list[float]:
        """Embed a search query, with the model's query prompt."""
        ...


@dataclass(frozen=True)
class ChunkHit:
    """One search result: which chunk, its section, where it lies, its score."""

    chunk_index: int
    section_index: int
    span: TextSpan
    score: float

    def to_dict(self) -> dict[str, Any]:
        """Return ``{"chunk_index", "section_index", "span", "score"}``."""
        return {
            "chunk_index": self.chunk_index,
            "section_index": self.section_index,
            "span": self.span.to_dict(),
            "score": self.score,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChunkHit:
        """Rebuild a hit from :meth:`to_dict` output."""
        return cls(
            data["chunk_index"],
            data["section_index"],
            TextSpan.from_dict(data["span"]),
            data["score"],
        )


def ensure_schema(conn: Any) -> None:
    """Create the store's two tables if they do not exist (SQLite or PostgreSQL)."""
    create_tables(conn, _SCHEMA.format(blob="BLOB" if is_sqlite(conn) else "BYTEA"))


def current_label(canonical: CanonicalText, embedder: ChunkEmbedder) -> ChunkSetLabel:
    """Return the label a set indexed now from ``canonical`` would carry.

    Raises:
        UnidentifiedEmbedderError: If the embedder carries no model.
        ValueError: If the text holds a lone surrogate, which cannot be hashed.
    """
    model = getattr(embedder, "model", None)
    if not isinstance(model, ModelFile):
        raise UnidentifiedEmbedderError(
            "the embedder carries no model identity; build it with Embedder.from_model"
        )
    try:
        encoded = canonical.text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"the canonical text holds a lone surrogate ({exc})") from exc
    return ChunkSetLabel(
        model.sha256,
        EMBEDDING_RECIPE_VERSION,
        CHUNKER_VERSION,
        canonical.converter_version,
        hashlib.sha256(encoded).hexdigest(),
    )


def _check_key(key: object) -> None:
    if type(key) is not str or not key:
        raise ValueError(f"a paper's key must be a non-empty str, got {key!r}")


def index_paper(
    conn: Any,
    key: str,
    canonical: CanonicalText,
    embedder: ChunkEmbedder,
    on_progress: Callable[[int, int], bool | None] | None = None,
) -> int:
    """Chunk, embed and store ``canonical`` under ``key``, replacing any set there.

    Every chunk is embedded before the write transaction opens, so a failure
    or a cancel leaves the stored set as it was. ``on_progress(done, total)``
    is called after each batch of :data:`INDEX_BATCH_SIZE` chunks; returning
    ``False`` cancels. A text with no chunks is stored as a set of none.

    Returns:
        The number of chunks stored.

    Raises:
        ValueError: For an empty or non-``str`` key, or a lone surrogate.
        UnidentifiedEmbedderError: If the embedder carries no model.
        IndexingCancelled: If ``on_progress`` returned ``False``.
        EmbedError: As the embedder raises it.
    """
    _check_key(key)
    label = current_label(canonical, embedder)
    chunks = chunk_canonical(canonical)
    blobs: list[bytes] = []
    for start in range(0, len(chunks), INDEX_BATCH_SIZE):
        batch = chunks[start : start + INDEX_BATCH_SIZE]
        texts = [canonical.text[c.span.start : c.span.end] for c in batch]
        vectors = embedder.embed_documents(texts)
        if len(vectors) != len(texts):
            raise VectorError(f"the embedder returned {len(vectors)} vectors for {len(texts)}")
        blobs.extend(encode_vector(v) for v in vectors)
        if on_progress is not None and on_progress(len(blobs), len(chunks)) is False:
            raise IndexingCancelled(f"indexing {key!r} was cancelled; nothing was stored")
    p = placeholder(conn)
    with transaction(conn):
        _delete(conn, key)
        execute(
            conn,
            f"INSERT INTO embedded_chunk_sets (paper_key, {', '.join(LABEL_FIELDS)}, "
            f"chunk_count, indexed_at) VALUES ({placeholders(conn, 8)})",
            [key, *(getattr(label, f) for f in LABEL_FIELDS), len(chunks), _now()],
        )
        executemany(
            conn,
            "INSERT INTO embedded_chunks (paper_key, chunk_index, section_index, span_start, "
            f"span_end, embedding) VALUES ({p}, {p}, {p}, {p}, {p}, {p})",
            [
                (key, i, c.section_index, c.span.start, c.span.end, blob)
                for i, (c, blob) in enumerate(zip(chunks, blobs, strict=True))
            ],
        )
    return len(chunks)


def search(
    conn: Any,
    key: str,
    canonical: CanonicalText,
    question: str,
    embedder: ChunkEmbedder,
    k: int,
) -> list[ChunkHit]:
    """Return the ``k`` chunks of ``key``'s set that best match ``question``, best first.

    ``canonical`` is the text the app holds for the paper; the set must have
    been indexed from exactly it, by the embedder's model, under the current
    recipe and chunker. An exact tie goes to the lower chunk index.

    Raises:
        ValueError: For an empty or non-``str`` key, a bad ``k``, or a lone surrogate.
        UnidentifiedEmbedderError: If the embedder carries no model.
        NotIndexedError: If no set is stored under ``key``.
        StaleChunkSetError: If the stored label differs from the current one.
        CorruptChunkSetError: If a stored row cannot be read as part of the set.
        VectorError: If the question's vector would not be stored.
        EmbedError: As the embedder raises it.
    """
    _check_key(key)
    if type(k) is not int or k < 1:
        raise ValueError(f"k must be a positive integer, got {k!r}")
    label = current_label(canonical, embedder)
    p = placeholder(conn)
    # One statement, so the label and the rows come from one snapshot: read
    # apart, a re-index between the two had another model's vectors scored.
    rows = fetch_all(
        conn,
        f"SELECT {', '.join('s.' + f for f in LABEL_FIELDS)}, s.chunk_count, c.chunk_index, "
        "c.section_index, c.span_start, c.span_end, c.embedding "
        "FROM embedded_chunk_sets s LEFT JOIN embedded_chunks c ON c.paper_key = s.paper_key "
        f"WHERE s.paper_key = {p} ORDER BY c.chunk_index",
        [key],
    )
    if not rows:
        raise NotIndexedError(f"no chunk set is stored for {key!r}")
    try:
        stored = ChunkSetLabel.from_dict({f: rows[0][f] for f in LABEL_FIELDS})
    except ValueError as exc:
        raise CorruptChunkSetError(f"the stored label for {key!r} is unreadable: {exc}") from exc
    differing = stored.differences(label)
    if differing:
        raise StaleChunkSetError(differing)
    count = rows[0]["chunk_count"]
    chunks = [r for r in rows if r["chunk_index"] is not None]
    if (
        type(count) is not int
        or count < 0
        or [r["chunk_index"] for r in chunks] != list(range(count))
    ):
        raise CorruptChunkSetError(f"the set for {key!r} records {count!r} chunks, not its rows")
    if not chunks:
        return []
    vectors = [decode_vector(bytes(r["embedding"])) for r in chunks]
    spans = [_stored_span(canonical, r) for r in chunks]
    query = embedder.embed_query(question)
    encode_vector(query)  # held to the stored vectors' rules: a VectorError otherwise
    return [
        ChunkHit(index, chunks[index]["section_index"], spans[index], value)
        for index, value in rank(query, vectors, k)
    ]


def delete_paper(conn: Any, key: str) -> None:
    """Remove ``key``'s set; an absent key is not an error."""
    _check_key(key)
    with transaction(conn):
        _delete(conn, key)


def _delete(conn: Any, key: str) -> None:
    # Explicit, not by cascade: a connection may have foreign keys off.
    p = placeholder(conn)
    execute(conn, f"DELETE FROM embedded_chunks WHERE paper_key = {p}", [key])
    execute(conn, f"DELETE FROM embedded_chunk_sets WHERE paper_key = {p}", [key])


def _stored_span(canonical: CanonicalText, row: Any) -> TextSpan:
    section, start, end = row["section_index"], row["span_start"], row["span_end"]
    if not all(type(v) is int for v in (section, start, end)) or not (
        0 <= section < len(canonical.sections) and 0 <= start < end <= len(canonical.text)
    ):
        raise CorruptChunkSetError(
            f"stored chunk {row['chunk_index']!r} lies outside the text: {(section, start, end)!r}"
        )
    return TextSpan(start, end)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
