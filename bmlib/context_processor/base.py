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

"""Hierarchical map-reduce processing of more content than fits one context.

Subclasses say how to render an item and how to extract from a batch of
them; this class handles batching, recursion, consolidation, progress and
failure accounting.

The algorithm:

1. Pack items into batches whose formatted content fits
   ``max_context_chars``.
2. Extract from each batch.
3. If the extractions together still exceed one context, feed them back in
   as items and repeat, until they fit or the recursion ceiling is reached.

Example::

    class ChunkProcessor(IterativeContextProcessor):
        def format_item(self, item, index):
            text, score = item
            return f"[Chunk {index + 1}, score {score:.2f}]\\n{text}"

        def extract_from_batch(self, batch_content, query, batch_metadata):
            answer = my_llm(f"{query}\\n\\n{batch_content}")
            return ExtractionResult(content=answer)

    result = ChunkProcessor().process(chunks, query="What was measured?")
    print(result.content)
"""

from __future__ import annotations

import dataclasses
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from bmlib.context_processor.data_types import (
    Batch,
    ConsolidatedItem,
    ConsolidationStrategy,
    ExtractionResult,
    OversizedItemError,
    OversizedItemStrategy,
    ProcessingConfig,
    ProcessingResult,
    ProcessingStatus,
    ProgressInfo,
)
from bmlib.llm.text_utils import TextChunker

logger = logging.getLogger(__name__)

#: Callback invoked with each :class:`ProgressInfo` update.
ProgressCallback = Callable[[ProgressInfo], None]

# How many times a split may be retried with a smaller budget before giving
# up.  Each attempt measures the decoration ``format_item`` adds and shrinks
# the budget by exactly that much, so one retry is normally enough; the bound
# guards a ``format_item`` whose decoration grows as its content shrinks.
_MAX_SPLIT_ATTEMPTS = 4


def _error_result(message: str) -> ExtractionResult:
    """Build the stand-in result a failed run reports instead of raising."""
    return ExtractionResult(
        content="",
        metadata={"error": message},
        confidence=0.0,
        is_error=True,
        error_message=message,
    )


@dataclass
class _Preformatted:
    """An item that is already rendered and must not be decorated again.

    Produced by :attr:`OversizedItemStrategy.TRUNCATE`, which cuts the
    *formatted* item to the limit. Passing that back through
    ``format_item()`` — as upstream did — applies the decoration twice and
    pushes the result over the limit again.
    """

    text: str


class IterativeContextProcessor(ABC):
    """Batch, extract, and recursively consolidate until the result fits.

    Args:
        config: Processing configuration; defaults are used when omitted.
        progress_callback: Called with each :class:`ProgressInfo` update. An
            exception raised by the callback is logged and swallowed — a
            broken progress bar must not lose the work.
    """

    def __init__(
        self,
        config: ProcessingConfig | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> None:
        self.config = config or ProcessingConfig()
        self.progress_callback = progress_callback

    # --- Subclass extension points ---

    @abstractmethod
    def format_item(self, item: Any, index: int) -> str:
        """Render one item for inclusion in a batch.

        Args:
            item: The item, of whatever type the caller supplies.
            index: Its position within the batch being built. Rendering it
                is fine — the batcher measures the item at the position it
                actually lands in.

        Returns:
            The item's text, including any decoration.
        """

    @abstractmethod
    def extract_from_batch(
        self,
        batch_content: str,
        query: str,
        batch_metadata: dict[str, Any],
    ) -> ExtractionResult:
        """Extract what answers *query* from one batch.

        Args:
            batch_content: The batch's formatted, joined content. Never
                longer than ``config.max_context_chars``.
            query: The question guiding extraction.
            batch_metadata: ``batch_index``, ``item_count``, ``total_chars``,
                ``item_indices`` and ``recursion_level``.

        Returns:
            The extraction. ``batch_index``, ``recursion_level`` and
            ``source_indices`` are filled in by the caller.

        Raises:
            Exception: Any failure is recorded against the batch; whether it
                ends the run depends on ``config.continue_on_error``.
        """

    def format_consolidated_item(self, item: ConsolidatedItem, index: int) -> str:
        """Render a result from the level below as an item for this level.

        Defaults to the content alone. Override to label the level or fold
        metadata back in.

        Args:
            item: The consolidated item.
            index: Its position within the batch being built.

        Returns:
            The item's text.
        """
        return item.content

    def split_oversized_item(self, item: Any, max_chars: int, overlap: int = 0) -> list[Any]:
        """Cut an item too large to fit into pieces of at most *max_chars*.

        Handles :class:`str` and :class:`ConsolidatedItem`; override for
        other item types. The budget passed in already allows for the
        decoration ``format_item`` will add.

        Args:
            item: The item to cut.
            max_chars: Maximum characters of content per piece.
            overlap: Characters of overlap between consecutive pieces.

        Returns:
            Pieces, each of which fits.

        Raises:
            NotImplementedError: If the item type is not supported. The
                caller records the item as skipped rather than failing the
                run.
        """
        if isinstance(item, str):
            return list(self._split_string(item, max_chars, overlap))

        if isinstance(item, ConsolidatedItem):
            return [
                ConsolidatedItem(content=piece, metadata=dict(item.metadata))
                for piece in self._split_string(item.content, max_chars, overlap)
            ]

        raise NotImplementedError(
            f"Cannot split an item of type {type(item).__name__}. "
            f"Override split_oversized_item() for custom item types."
        )

    # --- Splitting ---

    def _split_string(self, text: str, max_chars: int, overlap: int = 0) -> list[str]:
        """Split *text* into pieces of at most *max_chars*, on text boundaries.

        Uses bmlib's boundary-aware :class:`~bmlib.llm.text_utils.TextChunker`,
        which prefers to end a piece at a paragraph or sentence break and
        never discards text. The minimum boundary offset scales with the
        budget, so a small window still gets boundary treatment.
        """
        if len(text) <= max_chars:
            return [text]

        chunker = TextChunker(
            chunk_size=max_chars,
            overlap=min(overlap, max(0, max_chars - 1)),
            boundary_aware=True,
            min_chunk_size=max(1, max_chars // 2),
        )
        return [chunk.content for chunk in chunker.chunk_text(text)]

    # --- Internal formatting ---

    def _format_one(self, item: Any, index: int) -> str:
        """Render any item the batcher may hold, routing by its type."""
        if isinstance(item, _Preformatted):
            return item.text
        if isinstance(item, ConsolidatedItem):
            return self.format_consolidated_item(item, index)
        return self.format_item(item, index)

    def _format_batch_content(self, batch: Batch, config: ProcessingConfig) -> str:
        """Join a batch's items into the content handed to the extractor."""
        return config.separator.join(
            self._format_one(item, index) for index, item in enumerate(batch.items)
        )

    # --- Batching ---

    def _create_batches(
        self,
        items: list[Any],
        config: ProcessingConfig,
        skipped_items: list[int] | None = None,
    ) -> list[Batch]:
        """Pack *items* into batches that fit ``config.max_context_chars``.

        Greedy bin-packing. Each item is measured at the position it would
        actually occupy, because a ``format_item()`` that renders the index
        changes width with it; an item that no longer fits is re-measured at
        the head of a fresh batch. An item that does not fit even alone is
        handled by ``config.oversized_item_strategy``.

        Args:
            items: Items to pack.
            config: The configuration to pack against.
            skipped_items: Appended to with the index of any item dropped.

        Returns:
            The batches, in input order.

        Raises:
            OversizedItemError: If an item is oversized and the strategy is
                FAIL. A :class:`ValueError`, as that strategy has always
                documented.
        """
        batches: list[Batch] = []
        current_items: list[Any] = []
        current_indices: list[int] = []
        current_chars = 0
        separator_len = len(config.separator)

        def flush() -> None:
            nonlocal current_items, current_indices, current_chars
            if current_items:
                batches.append(
                    Batch(
                        items=current_items,
                        item_indices=current_indices,
                        total_chars=current_chars,
                        batch_index=len(batches),
                    )
                )
                current_items = []
                current_indices = []
                current_chars = 0

        def try_place(item: Any, original_idx: int) -> bool:
            """Place *item* at the end of the current batch if it fits."""
            nonlocal current_chars
            cost = len(self._format_one(item, len(current_items)))
            if current_items:
                cost += separator_len
            if current_chars + cost > config.max_context_chars:
                return False
            current_items.append(item)
            current_indices.append(original_idx)
            current_chars += cost
            return True

        def place(item: Any, original_idx: int, *, may_split: bool = True) -> None:
            """Place *item*, starting a new batch or splitting it as needed."""
            if try_place(item, original_idx):
                return
            if current_items:
                # It did not fit alongside what is already batched.  Close
                # that batch and re-measure at the head of a fresh one, where
                # the index — and so the decoration — is different.
                flush()
                if try_place(item, original_idx):
                    return
            if not may_split:
                # A piece that still does not fit alone would silently
                # overflow the context the whole module exists to respect.
                logger.error(
                    "Dropping item %d: it does not fit in %d chars even after "
                    "splitting. Check that format_item() does not grow as its "
                    "content shrinks.",
                    original_idx,
                    config.max_context_chars,
                )
                if skipped_items is not None:
                    skipped_items.append(original_idx)
                return
            for piece in self._handle_oversized_item(item, original_idx, config, skipped_items):
                place(piece, original_idx, may_split=False)

        for original_idx, item in enumerate(items):
            place(item, original_idx)
        flush()

        logger.debug(
            "Created %d batches from %d items (max_chars=%d)",
            len(batches),
            len(items),
            config.max_context_chars,
        )
        return batches

    def _handle_oversized_item(
        self,
        item: Any,
        original_idx: int,
        config: ProcessingConfig,
        skipped_items: list[int] | None,
    ) -> list[Any]:
        """Apply ``oversized_item_strategy`` to an item that does not fit.

        Args:
            item: The oversized item.
            original_idx: Its index in the caller's list.
            config: The configuration in force.
            skipped_items: Appended to when the item is dropped.

        Returns:
            Replacement pieces, each of which fits alone. Empty when the
            item was skipped.

        Raises:
            OversizedItemError: If the strategy is FAIL.
        """
        strategy = config.oversized_item_strategy
        limit = config.max_context_chars

        def skip(reason: str) -> list[Any]:
            logger.warning("Skipping oversized item %d: %s", original_idx, reason)
            if skipped_items is not None:
                skipped_items.append(original_idx)
            return []

        if strategy is OversizedItemStrategy.FAIL:
            raise OversizedItemError(
                f"Item {original_idx} is oversized (needs more than {limit} chars). "
                f"Use a different oversized_item_strategy to handle this."
            )

        if strategy is OversizedItemStrategy.SKIP:
            return skip(f"larger than the {limit}-char limit")

        if strategy is OversizedItemStrategy.TRUNCATE:
            logger.warning(
                "Truncating oversized item %d to %d chars; the remainder is lost",
                original_idx,
                limit,
            )
            # Truncate what the item *renders to*, and mark it so the batcher
            # does not decorate it a second time.
            return [_Preformatted(self._format_one(item, 0)[:limit])]

        try:
            pieces = self._split_to_fit(item, config)
        except NotImplementedError as exc:
            return skip(str(exc))
        if not pieces:
            return skip("no split budget small enough to fit the decoration")
        logger.info("Split oversized item %d into %d pieces", original_idx, len(pieces))
        return pieces

    def _split_to_fit(self, item: Any, config: ProcessingConfig) -> list[Any]:
        """Split *item* into pieces that fit **once formatted**.

        ``split_oversized_item()`` cuts raw content, but the batcher measures
        the item after ``format_item()`` has decorated it — so a piece cut to
        exactly the limit exceeds it. The overflow is measured and the budget
        reduced by it, rather than guessed at.

        Args:
            item: The item to split.
            config: The configuration in force.

        Returns:
            Pieces that each fit when formatted, or an empty list if no
            budget was small enough.

        Raises:
            NotImplementedError: From ``split_oversized_item()``, if the item
                type is not supported.
        """
        limit = config.max_context_chars
        budget = limit

        for _ in range(_MAX_SPLIT_ATTEMPTS):
            if budget <= 0 or budget <= config.overlap_chars:
                break
            pieces = self.split_oversized_item(item, budget, config.overlap_chars)
            if not pieces:
                break
            overflow = max(len(self._format_one(piece, 0)) for piece in pieces) - limit
            if overflow <= 0:
                return pieces
            budget -= overflow

        return []

    # --- Consolidation ---

    def _merge_results(
        self,
        results: list[ExtractionResult],
        config: ProcessingConfig,
        recursion_level: int = 0,
    ) -> ExtractionResult:
        """Merge one level's results into a single result.

        Error results and results below ``min_confidence_threshold`` are
        dropped first; the rest are joined by ``consolidation_strategy``.

        Args:
            results: The level's results.
            config: The configuration in force.
            recursion_level: The level being merged.

        Returns:
            The merged result, reporting *recursion_level* as its level.
        """
        if not results:
            return ExtractionResult(content="", confidence=0.0, recursion_level=recursion_level)

        valid = [
            r for r in results if r.is_valid and r.confidence >= config.min_confidence_threshold
        ]
        if not valid:
            return ExtractionResult(
                content="",
                metadata={"all_filtered": True, "original_count": len(results)},
                confidence=0.0,
                recursion_level=recursion_level,
            )
        if len(valid) == 1:
            # Report the level it was merged at, without disturbing the
            # caller's result — ``intermediate_results`` may still hold it.
            # ``replace()`` copies shallowly, so the mutable fields are
            # copied too; otherwise the "copy" shares them with the original
            # and mutating either rewrites both.
            return dataclasses.replace(
                valid[0],
                metadata=dict(valid[0].metadata),
                source_indices=list(valid[0].source_indices),
                recursion_level=recursion_level,
            )

        strategy = config.consolidation_strategy
        if strategy is ConsolidationStrategy.WEIGHTED:
            ordered = sorted(valid, key=lambda r: r.confidence, reverse=True)
            content = config.separator.join(r.content for r in ordered)
            total_weight = sum(len(r.content) for r in valid)
            confidence = (
                sum(r.confidence * len(r.content) for r in valid) / total_weight
                if total_weight
                else 0.0
            )
        else:
            if strategy is ConsolidationStrategy.DEDUPLICATE:
                seen: set[str] = set()
                contents: list[str] = []
                for r in valid:
                    key = r.content.lower().strip()
                    if key not in seen:
                        seen.add(key)
                        contents.append(r.content)
            else:
                contents = [r.content for r in valid]
            content = config.separator.join(contents)
            # Every valid result counts, including one that reported 0.0.
            # Excluding those would make a batch the model had no confidence
            # in *raise* the merged confidence, and would disagree with the
            # weighted branch above about what the same inputs are worth.
            confidence = sum(r.confidence for r in valid) / len(valid)

        metadata: dict[str, Any] = {}
        if config.preserve_metadata:
            metadata = {
                "merged_from": len(valid),
                "filtered_count": len(results) - len(valid),
                "consolidation_strategy": strategy.value,
                "source_metadata": [r.metadata for r in valid],
            }

        sources: list[int] = []
        for r in valid:
            sources.extend(r.source_indices)

        return ExtractionResult(
            content=content,
            metadata=metadata,
            source_indices=sources,
            confidence=confidence,
            recursion_level=recursion_level,
        )

    # --- Progress ---

    def _report_progress(self, stage: str, **fields: Any) -> None:
        """Hand a :class:`ProgressInfo` to the callback, if there is one."""
        if self.progress_callback is None:
            return
        try:
            self.progress_callback(ProgressInfo(stage=stage, **fields))
        except Exception as exc:  # a broken UI must not lose the work
            logger.warning("Progress callback failed: %s", exc)

    # --- Processing ---

    def _process_level(
        self,
        items: list[Any],
        query: str,
        config: ProcessingConfig,
        recursion_level: int,
        intermediate_results: list[list[ExtractionResult]] | None,
        failed_batches: list[int],
        skipped_items: list[int],
    ) -> tuple[list[ExtractionResult], bool, int, int]:
        """Batch and extract one level.

        Returns:
            ``(results, needs_recursion, successful_count, batch_count)``.
            The batch count is returned rather than recomputed: upstream
            re-ran the whole bin-packing to obtain it, re-formatting every
            item and re-splitting every oversized one.

        Raises:
            RuntimeError: On the first failed batch when
                ``continue_on_error`` is false.
            OversizedItemError: From the batcher, when an item is oversized
                and the strategy is FAIL.
        """
        skipped_before = len(skipped_items)
        batches = self._create_batches(items, config, skipped_items)
        # An item dropped during packing will never reach an extraction, so a
        # progress count that waited for it would never reach the end.  It is
        # accounted for the moment the batcher drops it.
        items_done = len(skipped_items) - skipped_before

        self._report_progress(
            "batching",
            current_item=items_done,
            total_items=len(items),
            total_batches=len(batches),
            recursion_level=recursion_level,
            message=f"Created {len(batches)} batches from {len(items)} items",
        )

        results: list[ExtractionResult] = []
        successful = 0

        for batch in batches:
            self._report_progress(
                "extracting",
                current_item=items_done,
                total_items=len(items),
                current_batch=batch.batch_index + 1,
                total_batches=len(batches),
                recursion_level=recursion_level,
                message=f"Processing batch {batch.batch_index + 1}/{len(batches)}",
            )
            metadata = {
                "batch_index": batch.batch_index,
                "item_count": batch.size,
                "total_chars": batch.total_chars,
                # Copied: the subclass is free to keep or mutate what it is
                # handed, and this list is also the batch's own.
                "item_indices": list(batch.item_indices),
                "recursion_level": recursion_level,
            }
            try:
                result = self.extract_from_batch(
                    batch_content=self._format_batch_content(batch, config),
                    query=query,
                    batch_metadata=metadata,
                )
            except Exception as exc:
                message = str(exc)
                logger.error(
                    "Extraction failed for batch %d at level %d: %s",
                    batch.batch_index,
                    recursion_level,
                    message,
                )
                failed_batches.append(batch.batch_index)
                if not config.continue_on_error:
                    raise RuntimeError(
                        f"Batch {batch.batch_index} extraction failed: {message}"
                    ) from exc
                results.append(
                    ExtractionResult(
                        content="",
                        metadata={"error": message},
                        source_indices=list(batch.item_indices),
                        confidence=0.0,
                        batch_index=batch.batch_index,
                        recursion_level=recursion_level,
                        is_error=True,
                        error_message=message,
                    )
                )
            else:
                result.batch_index = batch.batch_index
                result.recursion_level = recursion_level
                # Copied, not aliased: the batch's list outlives this call
                # inside ``Batch``, and a caller sorting or clearing one
                # would otherwise silently rewrite the other.
                result.source_indices = list(batch.item_indices)
                results.append(result)
                successful += 1

            items_done += batch.size

        if intermediate_results is not None:
            intermediate_results.append(results)

        valid = [r for r in results if r.is_valid]
        joined_length = sum(len(r.content) for r in valid) + len(config.separator) * max(
            0, len(valid) - 1
        )
        return results, joined_length > config.max_context_chars, successful, len(batches)

    def process(
        self,
        items: list[Any],
        query: str,
        config: ProcessingConfig | None = None,
        store_intermediate: bool = False,
    ) -> ProcessingResult:
        """Extract an answer to *query* from *items*, however many there are.

        Args:
            items: The items to process, of whatever type ``format_item()``
                understands.
            query: The question guiding extraction.
            config: Overrides the instance configuration for this call.
            store_intermediate: Keep each level's results on the returned
                :attr:`ProcessingResult.intermediate_results`.

        Returns:
            The run's report. Failures are recorded on it rather than
            raised — inspect :attr:`ProcessingResult.status`.
        """
        config = config or self.config
        intermediate: list[list[ExtractionResult]] | None = [] if store_intermediate else None
        failed_batches: list[int] = []
        skipped_items: list[int] = []
        successful = 0

        # Local, not instance state: two concurrent ``process()`` calls on one
        # processor would otherwise append their per-level counts into
        # whichever dict the later call installed.
        stats: dict[str, Any] = {
            "total_items": len(items),
            "batches_per_level": [],
            "items_per_level": [len(items)],
        }

        if not items:
            return ProcessingResult(
                final_result=ExtractionResult(content="", confidence=0.0),
                status=ProcessingStatus.COMPLETED,
                total_items_processed=0,
                batches_created=0,
                recursion_levels_used=0,
                intermediate_results=intermediate,
                processing_stats=stats,
            )

        self._report_progress(
            "starting",
            total_items=len(items),
            message=f"Starting processing of {len(items)} items",
        )

        current_items = items
        recursion_level = 0
        total_batches = 0
        status = ProcessingStatus.COMPLETED
        error_message: str | None = None

        try:
            while True:
                results, needs_recursion, level_successful, batch_count = self._process_level(
                    items=current_items,
                    query=query,
                    config=config,
                    recursion_level=recursion_level,
                    intermediate_results=intermediate,
                    failed_batches=failed_batches,
                    skipped_items=skipped_items,
                )
                successful += level_successful
                stats["batches_per_level"].append(batch_count)
                total_batches += batch_count

                if not needs_recursion:
                    final_result = self._merge_results(results, config, recursion_level)
                    break

                if recursion_level >= config.max_recursion_depth:
                    valid = [r for r in results if r.is_valid]
                    remaining = sum(len(r.content) for r in valid) + len(config.separator) * max(
                        0, len(valid) - 1
                    )
                    logger.warning(
                        "Max recursion depth (%d) reached; returning a truncated result. "
                        "Remaining: %d results, %d chars (%+d over the %d-char limit)",
                        config.max_recursion_depth,
                        len(valid),
                        remaining,
                        remaining - config.max_context_chars,
                        config.max_context_chars,
                    )
                    status = ProcessingStatus.TRUNCATED
                    final_result = self._merge_results(results, config, recursion_level)
                    break

                valid = [r for r in results if r.is_valid]
                if len(valid) < config.min_items_for_recursion:
                    # One result has nothing to be consolidated *with*, so
                    # recursing would re-summarise it until the ceiling.
                    logger.info(
                        "Below the minimum for consolidation (%d < %d); returning as is",
                        len(valid),
                        config.min_items_for_recursion,
                    )
                    final_result = self._merge_results(results, config, recursion_level)
                    break

                self._report_progress(
                    "recursing",
                    recursion_level=recursion_level + 1,
                    message=f"Recursing to level {recursion_level + 1} with {len(valid)} results",
                )
                # The level is the base class's own knowledge, so it is put
                # on the item rather than left to whether the extractor
                # happened to copy its batch metadata forward.
                current_items = [
                    ConsolidatedItem(
                        content=r.content,
                        metadata={**r.metadata, "recursion_level": r.recursion_level},
                    )
                    for r in valid
                ]
                stats["items_per_level"].append(len(current_items))
                recursion_level += 1

        except OversizedItemError as exc:
            # The strict oversized strategy raising is the configuration
            # doing what it was asked, not a defect: no traceback.
            logger.error("Processing stopped: %s", exc)
            status = ProcessingStatus.FAILED
            error_message = str(exc)
            final_result = _error_result(error_message)
        except RuntimeError as exc:
            logger.error("Processing failed: %s", exc)
            status = ProcessingStatus.FAILED
            error_message = str(exc)
            final_result = _error_result(error_message)
        except Exception as exc:  # reported on the result rather than raised
            logger.exception("Unexpected error during processing")
            status = ProcessingStatus.FAILED
            error_message = f"Unexpected error: {exc}"
            final_result = _error_result(error_message)

        if status is ProcessingStatus.COMPLETED and (failed_batches or skipped_items):
            if successful > 0:
                status = ProcessingStatus.PARTIAL
                logger.info(
                    "Completed with partial results: %d failed batches, %d skipped items",
                    len(failed_batches),
                    len(skipped_items),
                )
            else:
                # Naming both counts matters: a run where every item was
                # dropped as oversized created no batch at all, and
                # reporting that as "all batches failed" sends the reader
                # looking for an extraction error that never happened.
                status = ProcessingStatus.FAILED
                error_message = (
                    f"No batch produced a result: {len(failed_batches)} failed, "
                    f"{len(skipped_items)} items skipped"
                )

        self._report_progress(
            "complete",
            current_item=len(items),
            total_items=len(items),
            recursion_level=recursion_level,
            message=(
                f"Processing complete after {recursion_level} recursion levels "
                f"(status: {status.value})"
            ),
        )

        return ProcessingResult(
            final_result=final_result,
            status=status,
            total_items_processed=len(items),
            batches_created=total_batches,
            recursion_levels_used=recursion_level,
            intermediate_results=intermediate,
            error_message=error_message,
            processing_stats=stats,
            failed_batches=failed_batches,
            skipped_items=skipped_items,
            successful_batches=successful,
        )
