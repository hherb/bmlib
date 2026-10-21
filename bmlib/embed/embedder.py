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
    """Embeds documents and queries to 256-dimension unit vectors."""

    def __init__(self, model_path: Path, *, gpu_layers: int = -1, batch_tokens: int = 8192) -> None:
        """Load the GGUF model.

        Args:
            model_path: Path to the arctic-embed-l-v2.0 GGUF file.
            gpu_layers: Layers offloaded to the GPU (-1 for all).
            batch_tokens: Tokens per llama.cpp batch.

        Raises:
            EmbedError: If llama-cpp-python is not installed.
        """
        try:
            import llama_cpp
        except ImportError as exc:
            raise EmbedError(
                "llama-cpp-python is not installed; install it with: pip install 'bmlib[embed]'"
            ) from exc
        self._llm = llama_cpp.Llama(
            model_path=str(model_path),
            embedding=True,
            pooling_type=llama_cpp.LLAMA_POOLING_TYPE_CLS,
            n_ctx=MAX_TOKENS,
            n_batch=batch_tokens,
            n_ubatch=batch_tokens,
            n_gpu_layers=gpu_layers,
            verbose=False,
        )

    @staticmethod
    def _check(texts: Sequence[str]) -> None:
        try:
            check_texts(texts)
        except EmptyTextError as exc:
            raise EmbedError(str(exc), exc.index) from exc

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed texts as documents (no prefix).

        Raises:
            EmbedError: With ``index`` set, for an empty text or one longer
                than ``MAX_TOKENS`` tokens.
        """
        self._check(texts)
        if not texts:
            return []
        for i, text in enumerate(texts):
            tokens = len(self._llm.tokenize(text.encode(), add_bos=True, special=False))
            if tokens > MAX_TOKENS:
                raise EmbedError(
                    f"text at index {i} has {tokens} tokens, over the {MAX_TOKENS} limit", i
                )
        raw = self._llm.embed(list(texts), normalize=False, truncate=False)
        return [truncate_and_normalize(vec) for vec in raw]

    def embed_query(self, query: str) -> list[float]:
        """Embed a search query, with the model's query prefix."""
        self._check([query])  # the prefix would otherwise make an empty query look valid
        return self.embed_documents([query_text(query)])[0]
