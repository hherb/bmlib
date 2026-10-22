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
than :data:`MAX_TOKENS` or an empty one raises :class:`EmbedError` naming it.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from types import TracebackType

from bmlib.embed.vectors import (
    MAX_TOKENS,
    EmptyTextError,
    check_texts,
    query_text,
    truncate_and_normalize,
)


class EmbedError(Exception):
    """An embedding request that cannot be honoured.

    Attributes:
        index: Position of the offending text, or None when not text-specific.
    """

    def __init__(self, message: str, index: int | None = None) -> None:
        super().__init__(message)
        self.index = index


class Embedder:
    """Embeds documents and queries to 256-dimension unit vectors.

    Not thread-safe (llama.cpp's context is not): use one per thread, or hold
    a lock round each call. :meth:`close` frees the model; so does leaving a
    ``with`` block.
    """

    def __init__(self, model_path: Path, *, gpu_layers: int = -1) -> None:
        """Load the GGUF model.

        Args:
            model_path: Path to the arctic-embed-l-v2.0 GGUF file.
            gpu_layers: Layers offloaded to the GPU (-1 for all).

        Raises:
            EmbedError: If llama-cpp-python cannot be imported (it comes with
                ``bmlib[embed]``), or llama.cpp cannot load the file.
        """
        try:
            import llama_cpp  # type: ignore[import-not-found, unused-ignore]  # optional extra
        # An installed llama_cpp can still fail: a missing dependency of its own,
        # or a shared library that will not load (RuntimeError, OSError).
        except (ImportError, OSError, RuntimeError) as exc:
            raise EmbedError(
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
            raise EmbedError(f"cannot load {model_path}: {exc}") from exc

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

    @staticmethod
    def _check(texts: Sequence[str]) -> None:
        try:
            check_texts(texts)
        except EmptyTextError as exc:
            raise EmbedError(str(exc), exc.index) from exc

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed texts as documents (no prefix).

        Raises:
            TypeError: If ``texts`` is a single ``str`` (it would embed each
                character).
            EmbedError: With ``index`` set, for an empty text or one longer
                than ``MAX_TOKENS`` tokens, before anything is embedded;
                without, if llama.cpp fails to decode.
        """
        if isinstance(texts, str):
            raise TypeError("embed_documents takes a sequence of texts, not a str")
        self._check(texts)
        if not texts:
            return []
        for i, text in enumerate(texts):
            tokens = len(self._llm.tokenize(text.encode(), add_bos=True, special=False))
            if tokens > MAX_TOKENS:
                raise EmbedError(
                    f"text at index {i} has {tokens} tokens, over the {MAX_TOKENS} limit", i
                )
        try:
            raw = self._llm.embed(list(texts), normalize=False, truncate=False)
        except RuntimeError as exc:  # llama_decode failed
            raise EmbedError(f"llama.cpp: {exc}") from exc
        return [truncate_and_normalize(vec) for vec in raw]

    def embed_query(self, query: str) -> list[float]:
        """Embed a search query, with the model's query prefix.

        Raises:
            EmbedError: As :meth:`embed_documents`; an empty query has
                ``index`` 0.
        """
        self._check([query])  # the prefix would otherwise make an empty query look valid
        return self.embed_documents([query_text(query)])[0]
