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

"""In-process embedder over llama-cpp-python.

Needs the ``bmlib[embed]`` extra. Nothing is truncated silently: a text longer
than :data:`MAX_TOKENS` or an empty one raises an :class:`EmbedError` naming it.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from types import TracebackType
from typing import Any

from bmlib.embed.model import ModelFile
from bmlib.embed.vectors import (
    EMBEDDING_DIM,
    MAX_TOKENS,
    EmbedError,
    VectorError,
    check_texts,
    query_text,
    truncate_and_normalize,
)


class TextTooLongError(EmbedError):
    """A text has more than :data:`MAX_TOKENS` tokens (Rust: ``EmbedError::TooLong``).

    Attributes:
        index: Position of the first such text.
        tokens: How many tokens it has.
    """

    index: int

    def __init__(self, index: int, tokens: int) -> None:
        super().__init__(
            f"text at index {index} has {tokens} tokens, over the {MAX_TOKENS} limit", index
        )
        self.tokens = tokens

    def __reduce__(self) -> tuple[Any, ...]:  # ``args`` holds the message, not the fields
        return type(self), (self.index, self.tokens), self.__dict__


class UnusableOutputError(EmbedError):
    """A text's model output is not an embedding (Rust: ``EmbedError::Vector``).

    Attributes:
        index: Position of the text.
        error: The :class:`VectorError` saying why; the embedder raises this
            error ``from`` it, so it is the ``__cause__`` too.
    """

    index: int

    def __init__(self, index: int, error: VectorError) -> None:
        super().__init__(f"text at index {index}: {error}", index)
        self.error = error

    def __reduce__(self) -> tuple[Any, ...]:  # ``args`` holds the message, not the fields
        return type(self), (self.index, self.error), self.__dict__


class ModelLoadError(EmbedError):
    """The embedder could not be made (Rust: ``EmbedError::Load``).

    llama-cpp-python will not import, llama.cpp will not load the file or
    create its context, or the model embeds to fewer than ``EMBEDDING_DIM``
    values. ``index`` is None. llama-cpp-python creates the context with the
    model, so a context failure is this error here, where Rust, which creates
    one per call, reports it as ``EmbedError::Decode`` when embedding.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)


class BatchDecodeError(EmbedError):
    """llama.cpp failed to embed a batch (Rust: ``EmbedError::Decode``). ``index`` is None."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class Embedder:
    """Embeds documents and queries to 256-dimension unit vectors.

    Not thread-safe (llama.cpp's context is not): use one per thread, or hold
    a lock round each call. :meth:`close` frees the model; so does leaving a
    ``with`` block.

    ``model`` is the :class:`~bmlib.embed.ModelFile` the file was loaded as,
    set by :meth:`from_model`; ``None`` for one built from a path alone, which
    :mod:`bmlib.embed.store` refuses, since it cannot label what it stores.
    """

    _model: ModelFile | None = None

    def __init__(self, model_path: Path, *, gpu_layers: int = -1) -> None:
        """Load the GGUF model.

        Args:
            model_path: Path to the arctic-embed-l-v2.0 GGUF file.
            gpu_layers: Layers offloaded to the GPU (-1 for all).

        Raises:
            ModelLoadError: If llama-cpp-python cannot be imported (it comes with
                ``bmlib[embed]``), llama.cpp cannot load the file or create its
                context, or the model embeds to fewer than ``EMBEDDING_DIM`` values.
        """
        try:
            import llama_cpp  # type: ignore[import-not-found, unused-ignore]  # optional extra
        # An installed llama_cpp can still fail: a missing dependency of its own,
        # or a shared library that will not load (RuntimeError, OSError).
        except (ImportError, OSError, RuntimeError) as exc:
            raise ModelLoadError(
                f"cannot import llama-cpp-python ({exc!r}); "
                "it comes with: pip install 'bmlib[embed]'"
            ) from exc
        # One batch holds a whole MAX_TOKENS text, so none escapes the
        # pre-check below as llama-cpp-python's bare ValueError.
        try:
            self._llm = llama_cpp.Llama(
                model_path=str(model_path),
                embedding=True,
                pooling_type=llama_cpp.LLAMA_POOLING_TYPE_CLS,
                n_ctx=MAX_TOKENS,
                n_batch=MAX_TOKENS,
                n_ubatch=MAX_TOKENS,
                n_gpu_layers=gpu_layers,
                # Off (llama-cpp-python's default), the non-causal attention matrix
                # for an 8192-token batch is reserved up front: ~4.5 GiB, not ~350 MiB.
                flash_attn=True,
                verbose=False,
            )
        except (ValueError, RuntimeError) as exc:  # missing file, or llama.cpp refused it
            raise ModelLoadError(f"cannot load {model_path}: {exc}") from exc
        # The output's width is the model's, so a narrow one would fail every text alike.
        width = self._llm.n_embd()
        if width < EMBEDDING_DIM:
            self._llm.close()
            raise ModelLoadError(
                f"{model_path} embeds to {width} values, need at least {EMBEDDING_DIM}"
            )

    @classmethod
    def from_model(cls, model: ModelFile, model_path: Path, *, gpu_layers: int = -1) -> Embedder:
        """Load ``model_path`` as ``model``, and carry its identity.

        The file is not hashed: this trusts the check
        :func:`~bmlib.embed.download_model` made when it wrote or verified the
        file, so a launch hashes the file once. Its size is checked, which
        tells the pinned files apart: loaded as the other one, every set it
        stored would carry the wrong model's sha256 and pass the store's label.

        Raises:
            ModelLoadError: If the file is not ``model.size`` bytes, or as the
                constructor.
        """
        try:
            size: int | None = Path(model_path).stat().st_size
        except OSError:
            size = None  # absent or unreadable: the constructor reports it
        if size is not None and size != model.size:
            raise ModelLoadError(
                f"{model_path} is {size} bytes, not the {model.size} of {model.file}"
            )
        embedder = cls(model_path, gpu_layers=gpu_layers)
        embedder._model = model
        return embedder

    @property
    def model(self) -> ModelFile | None:
        """The model file this was loaded as, or ``None``."""
        return self._model

    def close(self) -> None:
        """Free the model. The embedder cannot be used afterwards."""
        self._llm.close()

    def __enter__(self) -> Embedder:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed texts as documents (no prefix).

        Raises:
            TypeError: If ``texts`` is a single ``str`` (it would embed each
                character).
            EmptyTextError: For the first empty or whitespace-only text,
                before anything is embedded.
            TextTooLongError: For the first text over ``MAX_TOKENS`` tokens,
                before anything is embedded.
            EmbedError: Itself, with ``index``, for the first text that cannot
                be tokenized (one carrying a lone surrogate), before anything is
                embedded.
            UnusableOutputError: For the first text whose model output is
                unusable (NaN, infinite or zero; see :class:`VectorError`).
            BatchDecodeError: If llama.cpp fails to decode (no ``index``).

        Each is an :class:`EmbedError`.
        """
        if isinstance(texts, str):
            raise TypeError("embed_documents takes a sequence of texts, not a str")
        check_texts(texts)
        if not texts:
            return []
        for i, text in enumerate(texts):
            try:
                tokens = len(self._llm.tokenize(text.encode(), add_bos=True, special=False))
            # A lone surrogate will not encode, and llama-cpp-python's tokenize can
            # raise; Rust meets neither (a str is valid UTF-8, its tokenize cannot fail).
            except (UnicodeEncodeError, RuntimeError) as exc:
                raise EmbedError(f"text at index {i} cannot be tokenized: {exc}", i) from exc
            if tokens > MAX_TOKENS:
                raise TextTooLongError(i, tokens)
        try:
            raw = self._llm.embed(list(texts), normalize=False, truncate=False)
        # RuntimeError: llama_decode failed; ValueError: a NULL embedding pointer.
        except (RuntimeError, ValueError) as exc:
            raise BatchDecodeError(f"llama.cpp: {exc}") from exc
        vectors = []
        for i, vec in enumerate(raw):
            try:
                vectors.append(truncate_and_normalize(vec))
            except VectorError as exc:
                raise UnusableOutputError(i, exc) from exc
        return vectors

    def embed_query(self, query: str) -> list[float]:
        """Embed a search query, with the model's query prefix.

        Raises:
            EmbedError: As :meth:`embed_documents`; an empty query is an
                :class:`EmptyTextError` at index 0.
        """
        check_texts([query])  # the prefix would otherwise make an empty query look valid
        return self.embed_documents([query_text(query)])[0]
