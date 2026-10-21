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

"""In-process embedding: constants, vector rules and the verified model download."""

from bmlib.embed.embedder import Embedder, EmbedError
from bmlib.embed.model import (
    ARCTIC_EMBED_L_V2_F16,
    ARCTIC_EMBED_L_V2_Q8_0,
    ModelFile,
    ModelVerificationError,
    download_model,
    verify_model,
)
from bmlib.embed.vectors import (
    EMBEDDING_DIM,
    MAX_TOKENS,
    QUERY_PREFIX,
    check_texts,
    query_text,
    truncate_and_normalize,
)

__all__ = [
    "ARCTIC_EMBED_L_V2_F16",
    "ARCTIC_EMBED_L_V2_Q8_0",
    "EMBEDDING_DIM",
    "EmbedError",
    "Embedder",
    "MAX_TOKENS",
    "QUERY_PREFIX",
    "ModelFile",
    "ModelVerificationError",
    "check_texts",
    "download_model",
    "query_text",
    "truncate_and_normalize",
    "verify_model",
]
