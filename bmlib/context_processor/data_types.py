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

"""Data types for hierarchical map-reduce context processing.

The processor batches items to fit an LLM context window, extracts from each
batch, and recursively consolidates the extractions until what remains fits
in one context. These are the carriers for its configuration, its per-batch
results, and its final report.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# Default configuration constants (characters).
DEFAULT_MAX_CONTEXT_CHARS = 4000
DEFAULT_OVERLAP_CHARS = 0
DEFAULT_MAX_RECURSION_DEPTH = 5
DEFAULT_MIN_ITEMS_FOR_RECURSION = 2
DEFAULT_SEPARATOR = "\n\n---\n\n"


class ProcessingStatus(Enum):
    """Outcome of a processing run."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    TRUNCATED = "truncated"  # Recursion ceiling reached; partial results.
    PARTIAL = "partial"  # Some batches failed or items were skipped.


class OversizedItemStrategy(Enum):
    """What to do with an item larger than ``max_context_chars`` on its own."""

    SPLIT = "split"  # Cut it into pieces that fit (see split_oversized_item).
    TRUNCATE = "truncate"  # Keep the leading part; the rest is lost.
    SKIP = "skip"  # Drop it entirely, recording its index.
    FAIL = "fail"  # Raise ValueError (strict mode).


class ConsolidationStrategy(Enum):
    """How to merge the extraction results of one level into one result."""

    CONCATENATE = "concatenate"  # Join in order, with the separator.
    WEIGHTED = "weighted"  # Join most-confident first.
    DEDUPLICATE = "deduplicate"  # Drop repeated content before joining.


@dataclass(frozen=True)
class ProcessingConfig:
    """Configuration for one processing run.

    Frozen: every batching decision reads it, and a caller mutating it
    mid-run would leave the recorded statistics describing a configuration
    that never ran. Pass ``config=`` to :meth:`~bmlib.context_processor.
    IterativeContextProcessor.process` to vary it per call.

    Attributes:
        max_context_chars: Maximum characters in one batch's formatted
            content. This is the promise the whole module makes: no batch
            handed to ``extract_from_batch`` exceeds it.
        overlap_chars: Characters of overlap between pieces when an
            oversized item is split. Zero for discrete items.
        max_recursion_depth: Levels of recursive consolidation allowed
            before the run gives up and returns ``TRUNCATED``.
        min_items_for_recursion: Below this many results, consolidation is
            not attempted — one result has nothing to be merged with, so
            recursing would just re-summarise it until the ceiling.
        separator: String joining items within a batch, and results within
            a consolidation.
        preserve_metadata: Carry each result's metadata into the merged
            result's ``source_metadata``.
        oversized_item_strategy: Answer to an item that does not fit alone.
        consolidation_strategy: How results are merged.
        continue_on_error: Record a failed batch and carry on. When false,
            the first failure ends the run.
        min_confidence_threshold: Results below this confidence are dropped
            before merging.

    Raises:
        ValueError: If any bound is violated.
    """

    max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS
    overlap_chars: int = DEFAULT_OVERLAP_CHARS
    max_recursion_depth: int = DEFAULT_MAX_RECURSION_DEPTH
    min_items_for_recursion: int = DEFAULT_MIN_ITEMS_FOR_RECURSION
    separator: str = DEFAULT_SEPARATOR
    preserve_metadata: bool = True
    oversized_item_strategy: OversizedItemStrategy = OversizedItemStrategy.SPLIT
    consolidation_strategy: ConsolidationStrategy = ConsolidationStrategy.CONCATENATE
    continue_on_error: bool = True
    min_confidence_threshold: float = 0.0

    def __post_init__(self) -> None:
        """Validate the configuration."""
        if self.max_context_chars <= 0:
            raise ValueError(f"max_context_chars must be positive, got {self.max_context_chars}")
        if self.overlap_chars < 0:
            raise ValueError(f"overlap_chars must be non-negative, got {self.overlap_chars}")
        # An overlap at or above the window leaves no room to advance, so a
        # split would emit the same leading piece forever.
        if self.overlap_chars >= self.max_context_chars:
            raise ValueError(
                f"overlap_chars ({self.overlap_chars}) must be less than "
                f"max_context_chars ({self.max_context_chars})"
            )
        if self.max_recursion_depth < 0:
            raise ValueError(
                f"max_recursion_depth must be non-negative, got {self.max_recursion_depth}"
            )
        if self.min_items_for_recursion < 1:
            raise ValueError(
                f"min_items_for_recursion must be at least 1, got {self.min_items_for_recursion}"
            )
        if not 0.0 <= self.min_confidence_threshold <= 1.0:
            raise ValueError(
                f"min_confidence_threshold must be between 0.0 and 1.0, "
                f"got {self.min_confidence_threshold}"
            )


@dataclass
class ExtractionResult:
    """What one extraction pass produced.

    Attributes:
        content: The extracted or summarised text.
        metadata: Anything the extractor wants carried forward.
        source_indices: Indices of the original items behind this result,
            for traceability back to the caller's list.
        confidence: 0.0–1.0 confidence in the extraction. Used by the
            ``WEIGHTED`` consolidation strategy and by
            ``min_confidence_threshold``.
        batch_index: Which batch produced it.
        recursion_level: Depth it was produced at; 0 is the first pass.
        is_error: True when extraction failed and this stands in for it.
        error_message: Why, when ``is_error``.
    """

    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    source_indices: list[int] = field(default_factory=list)
    confidence: float = 1.0
    batch_index: int | None = None
    recursion_level: int = 0
    is_error: bool = False
    error_message: str | None = None

    @property
    def content_length(self) -> int:
        """Length of :attr:`content` in characters."""
        return len(self.content)

    @property
    def is_valid(self) -> bool:
        """True when this is a non-error result carrying content."""
        return not self.is_error and bool(self.content)

    def __repr__(self) -> str:
        """Concise representation, truncating the content."""
        if self.is_error:
            return f"ExtractionResult(ERROR: {self.error_message})"
        preview = self.content[:50] + "..." if len(self.content) > 50 else self.content
        return (
            f"ExtractionResult(content={preview!r}, "
            f"confidence={self.confidence:.2f}, "
            f"sources={len(self.source_indices)})"
        )


@dataclass
class ConsolidatedItem:
    """An extraction result fed back in as an item for the next level.

    The recursion changes what an "item" is: level 0 processes the caller's
    items, every level above processes the results of the level below. Giving
    those a type of their own is what lets
    :meth:`~bmlib.context_processor.IterativeContextProcessor.format_consolidated_item`
    exist — upstream used an anonymous ``(content, metadata)`` tuple, so every
    subclass had to sniff tuple shapes inside ``format_item`` to tell a
    consolidated item from one of its own.

    Attributes:
        content: The consolidated text.
        metadata: Metadata from the result it was made from.
    """

    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Batch:
    """Items grouped to fit one context window.

    Attributes:
        items: The items in this batch.
        item_indices: Their indices in the source list.
        total_chars: Length of the batch's formatted content. Equal to
            ``len(processor._format_batch_content(batch, config))`` and never
            greater than ``config.max_context_chars``.
        batch_index: Sequential index within the level.
    """

    items: list[Any]
    item_indices: list[int] = field(default_factory=list)
    total_chars: int = 0
    batch_index: int = 0

    @property
    def size(self) -> int:
        """Number of items in this batch."""
        return len(self.items)

    def __repr__(self) -> str:
        """Concise representation."""
        return f"Batch(items={self.size}, chars={self.total_chars}, index={self.batch_index})"


@dataclass
class ProcessingResult:
    """The complete report from a processing run.

    Attributes:
        final_result: The consolidated result.
        status: How the run ended.
        total_items_processed: Items the caller supplied.
        batches_created: Batches across every level.
        recursion_levels_used: Consolidation passes beyond the first.
        intermediate_results: Per-level results, when ``store_intermediate``.
        error_message: Why, when the run failed.
        processing_stats: Per-level counts.
        failed_batches: Indices of batches whose extraction raised. Batch
            indices restart at each level, so a value can repeat across a
            run that recursed.
        skipped_items: Indices of items dropped as oversized. At levels above
            0 these index that level's consolidated items rather than the
            caller's list.
        successful_batches: Batches that produced a result.
    """

    final_result: ExtractionResult
    status: ProcessingStatus
    total_items_processed: int
    batches_created: int
    recursion_levels_used: int
    intermediate_results: list[list[ExtractionResult]] | None = None
    error_message: str | None = None
    processing_stats: dict[str, Any] = field(default_factory=dict)
    failed_batches: list[int] = field(default_factory=list)
    skipped_items: list[int] = field(default_factory=list)
    successful_batches: int = 0

    @property
    def is_complete(self) -> bool:
        """True when the run finished with nothing failed or truncated."""
        return self.status == ProcessingStatus.COMPLETED

    @property
    def is_partial(self) -> bool:
        """True when the run finished but something was lost."""
        return self.status == ProcessingStatus.PARTIAL

    @property
    def has_failures(self) -> bool:
        """True when any batch failed or any item was skipped."""
        return bool(self.failed_batches) or bool(self.skipped_items)

    @property
    def content(self) -> str:
        """The final result's content."""
        return self.final_result.content

    @property
    def success_rate(self) -> float:
        """Fraction of batches that produced a result; 1.0 when none ran."""
        if self.batches_created == 0:
            return 1.0
        return self.successful_batches / self.batches_created

    def __repr__(self) -> str:
        """Concise representation naming any losses."""
        base = (
            f"ProcessingResult(status={self.status.value}, "
            f"items={self.total_items_processed}, "
            f"batches={self.batches_created}, "
            f"recursion={self.recursion_levels_used}"
        )
        if self.failed_batches:
            base += f", failed={len(self.failed_batches)}"
        if self.skipped_items:
            base += f", skipped={len(self.skipped_items)}"
        return base + ")"


@dataclass
class ProgressInfo:
    """A progress update handed to the caller's callback.

    Attributes:
        stage: One of ``starting``, ``batching``, ``extracting``,
            ``recursing``, ``complete``.
        current_item: Item index reached.
        total_items: Items at this level.
        current_batch: Batch index reached (1-based, for display).
        total_batches: Batches at this level.
        recursion_level: Depth.
        message: Human-readable summary.
    """

    stage: str
    current_item: int = 0
    total_items: int = 0
    current_batch: int = 0
    total_batches: int = 0
    recursion_level: int = 0
    message: str = ""

    @property
    def progress_percent(self) -> float:
        """Progress through this level's items, 0.0–100.0."""
        if self.total_items == 0:
            return 0.0
        return (self.current_item / self.total_items) * 100.0

    def __repr__(self) -> str:
        """Concise representation."""
        return (
            f"ProgressInfo(stage={self.stage!r}, "
            f"progress={self.progress_percent:.1f}%, "
            f"level={self.recursion_level})"
        )
