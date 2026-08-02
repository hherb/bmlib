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

"""Process more content than fits in one LLM context window.

Hierarchical map-reduce: batch the items to fit, extract from each batch,
then feed the extractions back in as items and repeat until what remains
fits in a single context. The alternative — truncating — loses information
silently.

:class:`IterativeContextProcessor` is the harness and has no LLM dependency
of its own; :class:`LLMChunkProcessor` is a ready-made subclass that runs
extraction through a :class:`~bmlib.agents.BaseAgent`.

Example::

    from bmlib.agents import BaseAgent
    from bmlib.context_processor import LLMChunkProcessor, ProcessingConfig

    agent = BaseAgent(llm=get_llm_client(), model="ollama:gpt-oss:20b")
    processor = LLMChunkProcessor(
        agent, config=ProcessingConfig(max_context_chars=8000)
    )
    result = processor.process(chunks, query="What outcomes were reported?")
    print(result.content)
"""

from bmlib.context_processor.base import IterativeContextProcessor, ProgressCallback
from bmlib.context_processor.data_types import (
    DEFAULT_MAX_CONTEXT_CHARS,
    DEFAULT_MAX_RECURSION_DEPTH,
    DEFAULT_MIN_ITEMS_FOR_RECURSION,
    DEFAULT_OVERLAP_CHARS,
    DEFAULT_SEPARATOR,
    Batch,
    ConsolidatedItem,
    ConsolidationStrategy,
    ExtractionResult,
    OversizedItemStrategy,
    ProcessingConfig,
    ProcessingResult,
    ProcessingStatus,
    ProgressInfo,
)
from bmlib.context_processor.llm_processor import (
    DEFAULT_CONSOLIDATION_PROMPT,
    DEFAULT_EXTRACTION_PROMPT,
    STRUCTURED_EXTRACTION_PROMPT,
    LLMChunkProcessor,
    ScoredChunk,
)

__all__ = [
    "DEFAULT_CONSOLIDATION_PROMPT",
    "DEFAULT_EXTRACTION_PROMPT",
    "DEFAULT_MAX_CONTEXT_CHARS",
    "DEFAULT_MAX_RECURSION_DEPTH",
    "DEFAULT_MIN_ITEMS_FOR_RECURSION",
    "DEFAULT_OVERLAP_CHARS",
    "DEFAULT_SEPARATOR",
    "STRUCTURED_EXTRACTION_PROMPT",
    "Batch",
    "ConsolidatedItem",
    "ConsolidationStrategy",
    "ExtractionResult",
    "IterativeContextProcessor",
    "LLMChunkProcessor",
    "OversizedItemStrategy",
    "ProcessingConfig",
    "ProcessingResult",
    "ProcessingStatus",
    "ProgressCallback",
    "ProgressInfo",
    "ScoredChunk",
]
