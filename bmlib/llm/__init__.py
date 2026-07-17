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

"""LLM abstraction layer — unified interface across providers.

Usage::

    from bmlib.llm import LLMClient, LLMMessage

    client = LLMClient()
    response = client.chat(
        messages=[LLMMessage(role="user", content="Hello")],
        model="ollama:medgemma4B_it_q8",
    )
"""

from bmlib.llm.client import LLMClient, get_llm_client, reset_llm_client
from bmlib.llm.data_types import (
    EmbeddingResponse,
    LLMMessage,
    LLMResponse,
    LLMToolCall,
    LLMToolDefinition,
)
from bmlib.llm.json_repair import (
    JSONRepairError,
    extract_and_repair_json,
    repair_json,
    safe_json_loads,
)
from bmlib.llm.text_utils import (
    TextChunk,
    TextChunker,
    chunk_text,
    combine_title_and_text,
    get_text_with_priority,
    process_with_map_reduce,
    process_with_rolling_summary,
)
from bmlib.llm.token_tracker import TokenTracker, get_token_tracker, reset_token_tracker

__all__ = [
    "EmbeddingResponse",
    "JSONRepairError",
    "LLMClient",
    "LLMMessage",
    "LLMResponse",
    "LLMToolCall",
    "LLMToolDefinition",
    "TextChunk",
    "TextChunker",
    "TokenTracker",
    "chunk_text",
    "combine_title_and_text",
    "extract_and_repair_json",
    "get_llm_client",
    "get_text_with_priority",
    "get_token_tracker",
    "process_with_map_reduce",
    "process_with_rolling_summary",
    "repair_json",
    "reset_llm_client",
    "reset_token_tracker",
    "safe_json_loads",
]
