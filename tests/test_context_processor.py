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

"""Tests for the hierarchical map-reduce context processor."""

from __future__ import annotations

import dataclasses
import subprocess
import sys
import threading
from typing import Any

import pytest

from bmlib.context_processor import (
    Batch,
    ConsolidatedItem,
    ConsolidationStrategy,
    ExtractionResult,
    IterativeContextProcessor,
    OversizedItemError,
    OversizedItemStrategy,
    ProcessingConfig,
    ProcessingResult,
    ProcessingStatus,
    ProgressInfo,
)


class CountingProcessor(IterativeContextProcessor):
    """Test processor that records every call the base class makes.

    Items are plain strings.  ``format_item`` decorates them with a
    fixed-width marker so the difference between raw and formatted size is
    both non-zero and predictable.
    """

    #: Width of the decoration ``format_item`` adds, excluding the index.
    DECORATION = len("[0] ")

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.format_calls: list[tuple[Any, int]] = []
        self.split_calls: list[tuple[Any, int, int]] = []
        self.extract_calls: list[tuple[str, str, dict[str, Any]]] = []

    def format_item(self, item: Any, index: int) -> str:
        self.format_calls.append((item, index))
        return f"[{index}] {item}"

    def split_oversized_item(self, item: Any, max_chars: int, overlap: int = 0) -> list[Any]:
        self.split_calls.append((item, max_chars, overlap))
        return super().split_oversized_item(item, max_chars, overlap)

    def extract_from_batch(
        self, batch_content: str, query: str, batch_metadata: dict[str, Any]
    ) -> ExtractionResult:
        self.extract_calls.append((batch_content, query, batch_metadata))
        # Halve the content, so recursion converges.
        return ExtractionResult(content=batch_content[: len(batch_content) // 2])


class EchoProcessor(IterativeContextProcessor):
    """Minimal processor whose extraction returns a fixed string."""

    def __init__(self, reply: str = "ok", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.reply = reply

    def format_item(self, item: Any, index: int) -> str:
        return str(item)

    def extract_from_batch(
        self, batch_content: str, query: str, batch_metadata: dict[str, Any]
    ) -> ExtractionResult:
        return ExtractionResult(content=self.reply)


class FailingProcessor(EchoProcessor):
    """Processor whose extraction raises for selected batch indices."""

    def __init__(self, fail_on: set[int] | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.fail_on = fail_on if fail_on is not None else set()

    def extract_from_batch(
        self, batch_content: str, query: str, batch_metadata: dict[str, Any]
    ) -> ExtractionResult:
        if batch_metadata["batch_index"] in self.fail_on:
            raise RuntimeError(f"batch {batch_metadata['batch_index']} exploded")
        return ExtractionResult(content=self.reply)


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


class TestProcessingConfig:
    """Validation and immutability of the configuration object."""

    def test_defaults_are_usable(self) -> None:
        config = ProcessingConfig()
        assert config.max_context_chars > 0
        assert config.oversized_item_strategy is OversizedItemStrategy.SPLIT
        assert config.consolidation_strategy is ConsolidationStrategy.CONCATENATE

    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"max_context_chars": 0}, "max_context_chars"),
            ({"max_context_chars": -1}, "max_context_chars"),
            ({"overlap_chars": -1}, "overlap_chars"),
            ({"max_recursion_depth": -1}, "max_recursion_depth"),
            ({"min_items_for_recursion": 0}, "min_items_for_recursion"),
            ({"min_confidence_threshold": 1.5}, "min_confidence_threshold"),
            ({"min_confidence_threshold": -0.1}, "min_confidence_threshold"),
        ],
    )
    def test_invalid_values_are_rejected(self, kwargs: dict[str, Any], message: str) -> None:
        with pytest.raises(ValueError, match=message):
            ProcessingConfig(**kwargs)

    def test_overlap_must_leave_room_to_advance(self) -> None:
        """An overlap at or above the window makes the split stride zero."""
        with pytest.raises(ValueError, match="overlap_chars"):
            ProcessingConfig(max_context_chars=100, overlap_chars=100)

    def test_an_overlap_above_half_the_window_is_rejected(self) -> None:
        """The stride is ``max_context_chars - overlap_chars``, and the piece
        count grows without bound as it shrinks: one below the window, a split
        advances a character at a time, so a megabyte item becomes a million
        batches — a million model calls — with nothing to warn the caller."""
        with pytest.raises(ValueError, match="at most half"):
            ProcessingConfig(max_context_chars=100, overlap_chars=51)

    def test_exactly_half_the_window_is_allowed(self) -> None:
        assert ProcessingConfig(max_context_chars=100, overlap_chars=50).overlap_chars == 50

    def test_the_config_is_frozen(self) -> None:
        """Batching decisions read the config; mutating it mid-run would make
        the recorded statistics describe a configuration that never ran."""
        config = ProcessingConfig()
        with pytest.raises(dataclasses.FrozenInstanceError):
            config.max_context_chars = 10  # type: ignore[misc]


# --------------------------------------------------------------------------
# Data types
# --------------------------------------------------------------------------


class TestDataTypes:
    """Behaviour of the result and progress carriers."""

    def test_extraction_result_validity(self) -> None:
        assert ExtractionResult(content="text").is_valid
        assert not ExtractionResult(content="").is_valid
        assert not ExtractionResult(content="text", is_error=True).is_valid

    def test_extraction_result_reports_its_length(self) -> None:
        assert ExtractionResult(content="12345").content_length == 5

    def test_an_error_result_reprs_as_its_message(self) -> None:
        result = ExtractionResult(content="", is_error=True, error_message="boom")
        assert "boom" in repr(result)

    def test_batch_size_is_its_item_count(self) -> None:
        assert Batch(items=["a", "b"]).size == 2

    def test_processing_result_success_rate(self) -> None:
        result = ProcessingResult(
            final_result=ExtractionResult(content="x"),
            status=ProcessingStatus.PARTIAL,
            total_items_processed=4,
            batches_created=4,
            recursion_levels_used=0,
            successful_batches=3,
        )
        assert result.success_rate == 0.75
        assert result.is_partial
        assert not result.is_complete

    def test_success_rate_of_an_empty_run_is_one(self) -> None:
        """No batch failed, because none was created."""
        result = ProcessingResult(
            final_result=ExtractionResult(content=""),
            status=ProcessingStatus.COMPLETED,
            total_items_processed=0,
            batches_created=0,
            recursion_levels_used=0,
        )
        assert result.success_rate == 1.0

    def test_success_rate_of_a_run_that_lost_everything_is_zero(self) -> None:
        """The other way to create no batches: every item dropped as
        oversized. Reporting 1.0 there would have a total loss read as a
        clean run, since the ratio cannot tell the two apart on its own."""
        result = ProcessingResult(
            final_result=ExtractionResult(content=""),
            status=ProcessingStatus.FAILED,
            total_items_processed=3,
            batches_created=0,
            recursion_levels_used=0,
            skipped_items=[0, 1, 2],
        )
        assert result.success_rate == 0.0

    def test_content_reaches_through_to_the_final_result(self) -> None:
        result = ProcessingResult(
            final_result=ExtractionResult(content="answer"),
            status=ProcessingStatus.COMPLETED,
            total_items_processed=1,
            batches_created=1,
            recursion_levels_used=0,
        )
        assert result.content == "answer"

    def test_progress_percent(self) -> None:
        assert ProgressInfo(stage="x", current_item=5, total_items=10).progress_percent == 50.0
        assert ProgressInfo(stage="x").progress_percent == 0.0

    def test_a_consolidated_item_carries_content_and_metadata(self) -> None:
        item = ConsolidatedItem(content="text", metadata={"recursion_level": 1})
        assert item.content == "text"
        assert item.metadata["recursion_level"] == 1


# --------------------------------------------------------------------------
# Batching
# --------------------------------------------------------------------------


class TestBatching:
    """Greedy bin-packing of items into context-sized batches."""

    def test_no_items_makes_no_batches(self) -> None:
        processor = CountingProcessor()
        assert processor._create_batches([], ProcessingConfig()) == []

    def test_items_that_fit_share_one_batch(self) -> None:
        processor = CountingProcessor(config=ProcessingConfig(max_context_chars=1000))
        batches = processor._create_batches(["a", "b", "c"], processor.config)
        assert len(batches) == 1
        assert batches[0].size == 3
        assert batches[0].item_indices == [0, 1, 2]

    def test_items_are_split_across_batches_at_the_limit(self) -> None:
        # Each formatted item is "[i] " + 10 chars = 14 chars; separator is
        # long, so two items per batch is the most that fits in 40.
        config = ProcessingConfig(max_context_chars=40, separator="\n\n")
        processor = CountingProcessor(config=config)
        batches = processor._create_batches(["x" * 10] * 5, config)
        assert len(batches) > 1
        assert sum(b.size for b in batches) == 5

    def test_every_batch_reports_the_size_it_actually_formats_to(self) -> None:
        """``total_chars`` is the promise the recursion decision rests on."""
        config = ProcessingConfig(max_context_chars=60, separator="\n--\n")
        processor = CountingProcessor(config=config)
        batches = processor._create_batches([f"item-{i}" * 3 for i in range(12)], config)
        assert batches
        for batch in batches:
            rendered = processor._format_batch_content(batch, config)
            assert batch.total_chars == len(rendered)
            assert batch.total_chars <= config.max_context_chars

    def test_a_boundary_item_is_measured_where_it_lands(self) -> None:
        """Defect 4: upstream measured the item that *starts* a new batch with
        the outgoing batch's index, so ``total_chars`` under-counted whenever
        ``format_item`` renders the index."""

        class WideIndexProcessor(CountingProcessor):
            def format_item(self, item: Any, index: int) -> str:
                # Decoration width grows sharply with the index, so measuring
                # at the wrong index is visible rather than a rounding error.
                return f"[{'#' * index}] {item}"

        config = ProcessingConfig(max_context_chars=50, separator="|")
        processor = WideIndexProcessor(config=config)
        batches = processor._create_batches(["content"] * 12, config)
        for batch in batches:
            assert batch.total_chars == len(processor._format_batch_content(batch, config))
            assert batch.total_chars <= config.max_context_chars

    def test_indices_track_the_original_positions(self) -> None:
        config = ProcessingConfig(max_context_chars=30, separator="")
        processor = CountingProcessor(config=config)
        batches = processor._create_batches([f"item{i}" for i in range(6)], config)
        seen = [idx for batch in batches for idx in batch.item_indices]
        assert seen == [0, 1, 2, 3, 4, 5]


class TestOversizedItems:
    """The four strategies for an item that does not fit on its own."""

    def test_split_produces_pieces_that_each_fit(self) -> None:
        config = ProcessingConfig(
            max_context_chars=100,
            oversized_item_strategy=OversizedItemStrategy.SPLIT,
        )
        processor = CountingProcessor(config=config)
        batches = processor._create_batches(["x" * 500], config)
        assert processor.split_calls
        for batch in batches:
            assert batch.total_chars <= config.max_context_chars

    def test_a_split_piece_still_fits_once_it_is_decorated(self) -> None:
        """Defect 2: upstream cut pieces to ``max_chars`` of *raw* text, then
        measured them after ``format_item`` added its decoration, so a piece
        cut to exactly the limit exceeded it."""

        class FatDecorationProcessor(CountingProcessor):
            def format_item(self, item: Any, index: int) -> str:
                return f"<<<<<<<<<< chunk {index} >>>>>>>>>>\n{item}"

        config = ProcessingConfig(max_context_chars=200, overlap_chars=0)
        processor = FatDecorationProcessor(config=config)
        batches = processor._create_batches(["y" * 1000], config)
        assert batches
        for batch in batches:
            rendered = processor._format_batch_content(batch, config)
            assert len(rendered) <= config.max_context_chars

    def test_truncate_keeps_the_item_within_the_limit(self) -> None:
        """Defect 3: upstream truncated the *formatted* string and returned it
        as an ordinary item, which the batcher then decorated a second time —
        so the decoration appeared twice and the result exceeded the limit."""
        config = ProcessingConfig(
            max_context_chars=100,
            oversized_item_strategy=OversizedItemStrategy.TRUNCATE,
        )
        processor = CountingProcessor(config=config)
        batches = processor._create_batches(["z" * 400], config)
        assert len(batches) == 1
        rendered = processor._format_batch_content(batches[0], config)
        assert len(rendered) <= config.max_context_chars
        assert rendered.count("[0] ") == 1

    def test_skip_drops_the_item_and_records_it(self) -> None:
        config = ProcessingConfig(
            max_context_chars=50,
            oversized_item_strategy=OversizedItemStrategy.SKIP,
        )
        processor = CountingProcessor(config=config)
        skipped: list[int] = []
        batches = processor._create_batches(["ok", "x" * 400], config, skipped)
        assert skipped == [1]
        assert sum(b.size for b in batches) == 1

    def test_fail_raises(self) -> None:
        config = ProcessingConfig(
            max_context_chars=50,
            oversized_item_strategy=OversizedItemStrategy.FAIL,
        )
        processor = CountingProcessor(config=config)
        with pytest.raises(ValueError, match="oversized"):
            processor._create_batches(["x" * 400], config)

    def test_an_unsplittable_item_is_skipped_not_crashed(self) -> None:
        class UnsplittableProcessor(EchoProcessor):
            def format_item(self, item: Any, index: int) -> str:
                return "x" * 500

        config = ProcessingConfig(max_context_chars=50)
        processor = UnsplittableProcessor(config=config)
        skipped: list[int] = []
        batches = processor._create_batches([object()], config, skipped)
        assert skipped == [0]
        assert batches == []

    def test_splitting_prefers_a_text_boundary(self) -> None:
        """bmlib already ships a boundary-aware chunker; the naive character
        cut upstream used splits words in half."""
        config = ProcessingConfig(max_context_chars=400)
        processor = EchoProcessor(config=config)
        sentences = "".join(f"Sentence number {i} sits here. " for i in range(60))
        pieces = processor.split_oversized_item(sentences, 300, 0)
        assert len(pieces) > 1
        # Every piece but the last ends at a sentence break.
        assert all(piece.strip().endswith(".") for piece in pieces[:-1])
        assert "".join(pieces) == sentences


class TestTheContextLimitIsNeverExceeded:
    """The promise the whole module makes, checked where it is delivered.

    Every other batching test calls ``_create_batches`` directly, at level 0,
    with one strategy. This one asserts from inside ``extract_from_batch`` —
    the only place that sees what a model would actually be sent — across
    every oversized strategy, several separators, and the recursion levels
    where ``format_consolidated_item``'s decoration applies instead of
    ``format_item``'s.
    """

    class StrictProcessor(IterativeContextProcessor):
        """Fails the run if it is ever handed more than it asked for."""

        def __init__(self, limit: int, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self.limit = limit
            self.batches_seen = 0

        def format_item(self, item: Any, index: int) -> str:
            return f"[item {index + 1}] {item}"

        def format_consolidated_item(self, item: ConsolidatedItem, index: int) -> str:
            level = item.metadata.get("recursion_level", 0)
            return f"[Consolidated level {level}, item {index + 1}]\n{item.content}"

        def extract_from_batch(
            self, batch_content: str, query: str, batch_metadata: dict[str, Any]
        ) -> ExtractionResult:
            self.batches_seen += 1
            assert len(batch_content) <= self.limit, (
                f"batch of {len(batch_content)} chars exceeds the {self.limit}-char "
                f"limit at level {batch_metadata['recursion_level']}"
            )
            assert batch_metadata["total_chars"] == len(batch_content), (
                f"total_chars says {batch_metadata['total_chars']}, "
                f"the content is {len(batch_content)}"
            )
            # Shrink, so the recursion converges rather than hitting the ceiling.
            return ExtractionResult(content=batch_content[: int(len(batch_content) * 0.7)])

    @pytest.mark.parametrize("limit", [60, 100, 250])
    @pytest.mark.parametrize(
        "strategy",
        [
            OversizedItemStrategy.SPLIT,
            OversizedItemStrategy.TRUNCATE,
            OversizedItemStrategy.SKIP,
        ],
    )
    @pytest.mark.parametrize("separator", ["", "\n", "\n\n---\n\n"])
    def test_no_batch_ever_exceeds_the_limit(
        self, limit: int, strategy: OversizedItemStrategy, separator: str
    ) -> None:
        config = ProcessingConfig(
            max_context_chars=limit,
            separator=separator,
            oversized_item_strategy=strategy,
            max_recursion_depth=4,
        )
        processor = self.StrictProcessor(limit, config=config)
        # A mix of items that fit, items needing a split, and one far over.
        items = ["w" * 5, "w" * (limit // 2), "w" * (limit * 3), "w" * 2, "w" * (limit * 8)]
        # Fail-fast, so a breached assertion surfaces as the run's error
        # rather than being recorded as one failed batch among many.
        result = processor.process(
            items, query="q", config=dataclasses.replace(config, continue_on_error=False)
        )
        assert processor.batches_seen > 0
        assert "exceeds" not in (result.error_message or "")
        assert "total_chars says" not in (result.error_message or "")

    def test_the_assertion_would_catch_a_breach(self) -> None:
        """A guard that cannot fail proves nothing: hand the processor one
        char less than the batcher packs to, and the run must report it."""
        config = ProcessingConfig(max_context_chars=100, separator="\n", continue_on_error=False)
        processor = self.StrictProcessor(99, config=config)
        result = processor.process(["w" * 40] * 8, query="q")
        assert result.status is ProcessingStatus.FAILED
        assert "exceeds the 99-char limit" in (result.error_message or "")


class TestBinPackingRunsOnce:
    """Defect 1: upstream re-ran the whole bin-packing purely to count it."""

    def test_the_bin_packing_runs_once_per_level(self) -> None:
        """``_process_level`` returns the batch count it already computed;
        upstream recomputed it by packing everything a second time."""

        class SpyProcessor(CountingProcessor):
            def __init__(self, **kwargs: Any) -> None:
                super().__init__(**kwargs)
                self.packing_passes = 0

            def _create_batches(
                self,
                items: list[Any],
                config: ProcessingConfig,
                skipped_items: list[int] | None = None,
            ) -> list[Batch]:
                self.packing_passes += 1
                return super()._create_batches(items, config, skipped_items)

        config = ProcessingConfig(max_context_chars=100, separator="\n")
        processor = SpyProcessor(config=config)
        result = processor.process([f"item {i} " * 5 for i in range(20)], query="q")
        assert result.recursion_levels_used >= 1
        assert processor.packing_passes == result.recursion_levels_used + 1

    def test_the_reported_batch_count_is_the_one_that_ran(self) -> None:
        config = ProcessingConfig(max_context_chars=100, separator="\n")
        processor = CountingProcessor(config=config)
        result = processor.process([f"item {i} " * 5 for i in range(20)], query="q")
        assert result.batches_created == len(processor.extract_calls)

    def test_items_are_formatted_once_per_placement(self) -> None:
        config = ProcessingConfig(max_context_chars=1000, max_recursion_depth=0)
        processor = CountingProcessor(config=config)
        processor.process(["a", "b", "c"], query="q")
        # Three items: measured once each while packing, rendered once each
        # for the batch content.  Upstream did this twice over.
        assert len(processor.format_calls) == 6


# --------------------------------------------------------------------------
# Processing and recursion
# --------------------------------------------------------------------------


class TestProcess:
    """The public entry point."""

    def test_empty_input_completes_with_no_work(self) -> None:
        processor = EchoProcessor()
        result = processor.process([], query="q")
        assert result.status is ProcessingStatus.COMPLETED
        assert result.total_items_processed == 0
        assert result.batches_created == 0
        assert result.content == ""

    def test_a_single_batch_returns_its_extraction(self) -> None:
        processor = EchoProcessor(reply="the answer")
        result = processor.process(["a", "b"], query="q")
        assert result.status is ProcessingStatus.COMPLETED
        assert result.content == "the answer"
        assert result.batches_created == 1
        assert result.recursion_levels_used == 0

    def test_the_query_reaches_the_extractor(self) -> None:
        processor = CountingProcessor()
        processor.process(["a"], query="what is it?")
        assert processor.extract_calls[0][1] == "what is it?"

    def test_batch_metadata_describes_the_batch(self) -> None:
        processor = CountingProcessor()
        processor.process(["a", "b"], query="q")
        metadata = processor.extract_calls[0][2]
        assert metadata["batch_index"] == 0
        assert metadata["item_count"] == 2
        assert metadata["item_indices"] == [0, 1]
        assert metadata["recursion_level"] == 0

    def test_source_indices_trace_back_to_the_input(self) -> None:
        config = ProcessingConfig(max_context_chars=1000)
        processor = EchoProcessor(config=config)
        result = processor.process(["a", "b", "c"], query="q")
        assert result.final_result.source_indices == [0, 1, 2]

    def test_results_that_do_not_fit_are_consolidated_recursively(self) -> None:
        # Each extraction returns half its input, so several levels are needed.
        config = ProcessingConfig(max_context_chars=100, separator="\n")
        processor = CountingProcessor(config=config)
        result = processor.process([f"item {i} " * 5 for i in range(20)], query="q")
        assert result.recursion_levels_used >= 1
        assert result.status is ProcessingStatus.COMPLETED
        assert len(result.content) <= config.max_context_chars

    def test_recursion_stops_at_the_configured_ceiling(self) -> None:
        class NeverShrinksProcessor(EchoProcessor):
            def extract_from_batch(
                self, batch_content: str, query: str, batch_metadata: dict[str, Any]
            ) -> ExtractionResult:
                return ExtractionResult(content="y" * 90)

        config = ProcessingConfig(
            max_context_chars=100,
            max_recursion_depth=2,
            min_items_for_recursion=1,
            separator="\n",
        )
        processor = NeverShrinksProcessor(config=config)
        result = processor.process(["a" * 60] * 10, query="q")
        assert result.status is ProcessingStatus.TRUNCATED
        assert result.recursion_levels_used == 2

    def test_too_few_results_to_consolidate_returns_what_there_is(self) -> None:
        class OversizedReplyProcessor(EchoProcessor):
            def extract_from_batch(
                self, batch_content: str, query: str, batch_metadata: dict[str, Any]
            ) -> ExtractionResult:
                return ExtractionResult(content="y" * 500)

        config = ProcessingConfig(max_context_chars=100, min_items_for_recursion=2)
        processor = OversizedReplyProcessor(config=config)
        result = processor.process(["a"], query="q")
        # One result cannot be consolidated with anything, so recursing would
        # loop until the depth ceiling for no gain.
        assert result.recursion_levels_used == 0
        assert result.status is ProcessingStatus.COMPLETED

    def test_intermediate_results_are_kept_on_request(self) -> None:
        config = ProcessingConfig(max_context_chars=100, separator="\n")
        processor = CountingProcessor(config=config)
        result = processor.process(
            [f"item {i} " * 5 for i in range(20)], query="q", store_intermediate=True
        )
        assert result.intermediate_results is not None
        assert len(result.intermediate_results) == result.recursion_levels_used + 1

    def test_intermediate_results_are_dropped_by_default(self) -> None:
        processor = EchoProcessor()
        assert processor.process(["a"], query="q").intermediate_results is None

    def test_a_per_call_config_overrides_the_instance_one(self) -> None:
        processor = CountingProcessor(config=ProcessingConfig(max_context_chars=10_000))
        processor.process(
            ["x" * 100] * 5, query="q", config=ProcessingConfig(max_context_chars=200)
        )
        assert len(processor.extract_calls) > 1

    def test_the_indices_handed_out_are_copies_not_the_batch_s_own_list(self) -> None:
        """``batch_metadata["item_indices"]`` and the result's
        ``source_indices`` were both the ``Batch``'s one list, so a subclass
        that kept or sorted what it was handed silently rewrote the result.

        Both copies are made, and either alone would break the chain — so
        this fails when both are reverted, which is the state it was written
        for, and not when only one is.
        """
        captured: list[list[int]] = []

        class CapturingProcessor(EchoProcessor):
            def extract_from_batch(
                self, batch_content: str, query: str, batch_metadata: dict[str, Any]
            ) -> ExtractionResult:
                captured.append(batch_metadata["item_indices"])
                return ExtractionResult(content=self.reply)

        result = CapturingProcessor().process(["a", "b", "c"], query="q", store_intermediate=True)
        assert result.intermediate_results is not None
        extracted = result.intermediate_results[0][0]

        assert captured[0] == [0, 1, 2]
        assert extracted.source_indices == [0, 1, 2]
        assert extracted.source_indices is not captured[0]
        # And mutating what the extractor was handed changes nothing else.
        captured[0].clear()
        assert extracted.source_indices == [0, 1, 2]

    def test_two_concurrent_runs_do_not_share_statistics(self) -> None:
        """``processing_stats`` was instance state: whichever run started
        second installed its own dict, and the first then appended into it
        and handed it back to its caller as its own report."""
        both_started = threading.Barrier(2)

        class BlockingProcessor(EchoProcessor):
            def extract_from_batch(
                self, batch_content: str, query: str, batch_metadata: dict[str, Any]
            ) -> ExtractionResult:
                # Hold at the first batch until the other run has started —
                # and so has already installed its own statistics.
                if batch_metadata["batch_index"] == 0:
                    both_started.wait(timeout=10)
                return ExtractionResult(content=self.reply)

        config = ProcessingConfig(max_context_chars=20, separator="")
        processor = BlockingProcessor(config=config)
        results: dict[int, ProcessingResult] = {}

        def run(count: int) -> None:
            results[count] = processor.process(["a" * 10] * count, query="q")

        threads = [threading.Thread(target=run, args=(count,)) for count in (4, 8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        assert set(results) == {4, 8}
        for count, result in results.items():
            assert result.processing_stats["total_items"] == count
            assert sum(result.processing_stats["batches_per_level"]) == result.batches_created

    def test_statistics_describe_the_run(self) -> None:
        config = ProcessingConfig(max_context_chars=100, separator="\n")
        processor = CountingProcessor(config=config)
        result = processor.process([f"item {i} " * 5 for i in range(20)], query="q")
        stats = result.processing_stats
        assert stats["total_items"] == 20
        assert len(stats["batches_per_level"]) == result.recursion_levels_used + 1
        assert sum(stats["batches_per_level"]) == result.batches_created


class TestConsolidatedItems:
    """What the recursion feeds back into the next level."""

    def test_a_recursion_level_receives_consolidated_items(self) -> None:
        """The level above never sees the caller's item type — it sees the
        results of the level below, wrapped so they are told apart."""
        seen: list[Any] = []

        class RecordingProcessor(CountingProcessor):
            def format_consolidated_item(self, item: ConsolidatedItem, index: int) -> str:
                seen.append(item)
                return super().format_consolidated_item(item, index)

            def format_item(self, item: Any, index: int) -> str:
                # A raw string is what level 0 gets; a consolidated result
                # must never arrive here undeclared, as it did upstream.
                assert isinstance(item, str)
                return super().format_item(item, index)

        config = ProcessingConfig(max_context_chars=100, separator="\n")
        processor = RecordingProcessor(config=config)
        processor.process([f"item {i} " * 5 for i in range(20)], query="q")
        assert seen
        assert all(isinstance(item, ConsolidatedItem) for item in seen)
        assert any(item.metadata for item in seen)

    def test_consolidated_items_route_to_their_own_formatter(self) -> None:
        """Upstream never called ``format_consolidated_item``; subclasses had
        to sniff tuple shapes inside ``format_item`` instead."""
        labelled: list[str] = []

        class LabellingProcessor(CountingProcessor):
            def format_consolidated_item(self, item: ConsolidatedItem, index: int) -> str:
                labelled.append(item.content)
                return f"<level {item.metadata.get('recursion_level', 0)}> {item.content}"

        config = ProcessingConfig(max_context_chars=100, separator="\n")
        processor = LabellingProcessor(config=config)
        result = processor.process([f"item {i} " * 5 for i in range(20)], query="q")
        assert result.recursion_levels_used >= 1
        assert labelled, "format_consolidated_item was never called"

    def test_an_oversized_consolidated_item_splits_and_keeps_its_metadata(self) -> None:
        processor = EchoProcessor()
        item = ConsolidatedItem(content="word " * 200, metadata={"recursion_level": 1})
        pieces = processor.split_oversized_item(item, 200, 0)
        assert len(pieces) > 1
        assert all(isinstance(piece, ConsolidatedItem) for piece in pieces)
        assert all(piece.metadata["recursion_level"] == 1 for piece in pieces)


class TestFailureHandling:
    """Partial progress, fail-fast, and the status that reports them."""

    def test_a_failed_batch_yields_partial_status(self) -> None:
        config = ProcessingConfig(max_context_chars=20, separator="", continue_on_error=True)
        processor = FailingProcessor(fail_on={0}, reply="ok", config=config)
        result = processor.process(["aaaaaaaaaa"] * 4, query="q")
        assert result.status is ProcessingStatus.PARTIAL
        assert result.failed_batches == [0]
        assert result.has_failures
        assert result.content == "ok"

    def test_every_batch_failing_is_a_failure(self) -> None:
        config = ProcessingConfig(max_context_chars=20, separator="", continue_on_error=True)
        processor = FailingProcessor(fail_on={0, 1, 2, 3}, config=config)
        result = processor.process(["aaaaaaaaaa"] * 4, query="q")
        assert result.status is ProcessingStatus.FAILED
        assert result.error_message

    def test_fail_fast_stops_at_the_first_error(self) -> None:
        config = ProcessingConfig(max_context_chars=20, separator="", continue_on_error=False)
        processor = FailingProcessor(fail_on={1}, config=config)
        result = processor.process(["aaaaaaaaaa"] * 6, query="q")
        assert result.status is ProcessingStatus.FAILED
        assert result.final_result.is_error
        assert "exploded" in (result.error_message or "")

    def test_a_skipped_item_makes_the_run_partial(self) -> None:
        config = ProcessingConfig(
            max_context_chars=60,
            oversized_item_strategy=OversizedItemStrategy.SKIP,
        )
        processor = EchoProcessor(config=config)
        result = processor.process(["fine", "x" * 500], query="q")
        assert result.skipped_items == [1]
        assert result.status is ProcessingStatus.PARTIAL

    def test_the_fail_strategy_reports_through_the_result(self) -> None:
        """``process()`` reports rather than raises, so the strict oversized
        strategy surfaces as a FAILED status carrying the reason — a caller
        never has to wrap the call to find out what happened."""
        config = ProcessingConfig(
            max_context_chars=50,
            oversized_item_strategy=OversizedItemStrategy.FAIL,
        )
        result = EchoProcessor(config=config).process(["x" * 400], query="q")
        assert result.status is ProcessingStatus.FAILED
        assert "oversized" in (result.error_message or "")

    def test_the_fail_strategy_is_not_reported_as_an_unexpected_error(self) -> None:
        """It is the configuration doing exactly what it was asked. Filing it
        under "unexpected", with a traceback, sends the reader hunting for a
        defect in the harness instead of reading their own config."""
        config = ProcessingConfig(
            max_context_chars=50,
            oversized_item_strategy=OversizedItemStrategy.FAIL,
        )
        result = EchoProcessor(config=config).process(["x" * 400], query="q")
        assert "Unexpected error" not in (result.error_message or "")

    def test_the_oversized_error_is_still_a_value_error(self) -> None:
        """``OversizedItemStrategy.FAIL`` has always documented itself as
        raising ``ValueError``; the dedicated type must not break that."""
        config = ProcessingConfig(
            max_context_chars=50,
            oversized_item_strategy=OversizedItemStrategy.FAIL,
        )
        processor = EchoProcessor(config=config)
        with pytest.raises(ValueError, match="oversized"):
            processor._create_batches(["x" * 400], config)
        with pytest.raises(OversizedItemError):
            processor._create_batches(["x" * 400], config)

    def test_losing_every_item_is_not_reported_as_failed_batches(self) -> None:
        """With every item dropped as oversized, no batch was ever built.
        Saying "all batches failed" sends the reader looking for an
        extraction error that never happened."""
        config = ProcessingConfig(
            max_context_chars=20,
            oversized_item_strategy=OversizedItemStrategy.SKIP,
        )
        result = EchoProcessor(config=config).process(["x" * 500, "y" * 500], query="q")
        assert result.status is ProcessingStatus.FAILED
        assert result.batches_created == 0
        assert result.skipped_items == [0, 1]
        assert result.failed_batches == []
        assert "2 items skipped" in (result.error_message or "")
        assert "0 failed" in (result.error_message or "")
        # And the ratio must not read as a clean run.
        assert result.success_rate == 0.0

    def test_an_unexpected_error_is_reported_not_raised(self) -> None:
        class BrokenProcessor(EchoProcessor):
            def format_item(self, item: Any, index: int) -> str:
                raise TypeError("cannot format")

        result = BrokenProcessor().process(["a"], query="q")
        assert result.status is ProcessingStatus.FAILED
        assert "cannot format" in (result.error_message or "")


class TestConsolidation:
    """Merging extraction results into one."""

    def _results(self) -> list[ExtractionResult]:
        return [
            ExtractionResult(content="low", confidence=0.2),
            ExtractionResult(content="high", confidence=0.9),
            ExtractionResult(content="mid", confidence=0.5),
        ]

    def test_concatenate_preserves_order(self) -> None:
        config = ProcessingConfig(separator="|")
        merged = EchoProcessor()._merge_results(self._results(), config)
        assert merged.content == "low|high|mid"

    def test_weighted_puts_the_confident_first(self) -> None:
        config = ProcessingConfig(
            separator="|", consolidation_strategy=ConsolidationStrategy.WEIGHTED
        )
        merged = EchoProcessor()._merge_results(self._results(), config)
        assert merged.content == "high|mid|low"

    def test_deduplicate_drops_repeats(self) -> None:
        config = ProcessingConfig(
            separator="|", consolidation_strategy=ConsolidationStrategy.DEDUPLICATE
        )
        results = [
            ExtractionResult(content="same"),
            ExtractionResult(content="  SAME  "),
            ExtractionResult(content="other"),
        ]
        merged = EchoProcessor()._merge_results(results, config)
        assert merged.content == "same|other"

    def test_low_confidence_results_are_filtered_out(self) -> None:
        config = ProcessingConfig(separator="|", min_confidence_threshold=0.5)
        merged = EchoProcessor()._merge_results(self._results(), config)
        assert merged.content == "high|mid"

    def test_error_results_never_reach_the_merge(self) -> None:
        config = ProcessingConfig(separator="|")
        results = [
            ExtractionResult(content="good"),
            ExtractionResult(content="", is_error=True, error_message="x"),
        ]
        merged = EchoProcessor()._merge_results(results, config)
        assert merged.content == "good"

    def test_merging_nothing_yields_an_empty_result(self) -> None:
        merged = EchoProcessor()._merge_results([], ProcessingConfig())
        assert merged.content == ""
        assert merged.confidence == 0.0

    def test_everything_filtered_says_so(self) -> None:
        config = ProcessingConfig(min_confidence_threshold=0.9)
        results = [ExtractionResult(content="a", confidence=0.1)]
        merged = EchoProcessor()._merge_results(results, config)
        assert merged.content == ""
        assert merged.metadata["all_filtered"] is True

    def test_a_lone_result_is_returned_at_the_merge_level(self) -> None:
        """The merged result must report the level it was merged at, not the
        level the single input happened to be produced at."""
        results = [ExtractionResult(content="only", recursion_level=0)]
        merged = EchoProcessor()._merge_results(results, ProcessingConfig(), recursion_level=3)
        assert merged.content == "only"
        assert merged.recursion_level == 3

    def test_merging_does_not_mutate_its_input(self) -> None:
        results = [ExtractionResult(content="only", recursion_level=0)]
        EchoProcessor()._merge_results(results, ProcessingConfig(), recursion_level=3)
        assert results[0].recursion_level == 0

    def test_a_lone_result_is_copied_not_aliased(self) -> None:
        """``dataclasses.replace`` copies shallowly, so the "copy" would share
        its mutable fields with the original — and ``intermediate_results``
        may still be holding that original."""
        original = ExtractionResult(content="only", metadata={"k": 1}, source_indices=[0, 1])
        merged = EchoProcessor()._merge_results([original], ProcessingConfig(), recursion_level=1)
        merged.metadata["k"] = 2
        merged.source_indices.append(2)
        assert original.metadata == {"k": 1}
        assert original.source_indices == [0, 1]

    def test_a_zero_confidence_result_counts_towards_the_average(self) -> None:
        """Skipping it would let a batch the extractor had no confidence in
        *raise* the merged confidence, and would make CONCATENATE and
        WEIGHTED disagree about what the same two results are worth."""
        results = [
            ExtractionResult(content="a", confidence=0.0),
            ExtractionResult(content="b", confidence=1.0),
        ]
        concatenated = EchoProcessor()._merge_results(results, ProcessingConfig(separator="|"))
        weighted = EchoProcessor()._merge_results(
            results,
            ProcessingConfig(separator="|", consolidation_strategy=ConsolidationStrategy.WEIGHTED),
        )
        assert concatenated.confidence == pytest.approx(0.5)
        assert weighted.confidence == pytest.approx(0.5)

    def test_source_metadata_is_preserved_when_asked(self) -> None:
        config = ProcessingConfig(preserve_metadata=True)
        results = [
            ExtractionResult(content="a", metadata={"k": 1}),
            ExtractionResult(content="b", metadata={"k": 2}),
        ]
        merged = EchoProcessor()._merge_results(results, config)
        assert merged.metadata["merged_from"] == 2
        assert merged.metadata["source_metadata"] == [{"k": 1}, {"k": 2}]

    def test_metadata_is_dropped_when_not_asked(self) -> None:
        config = ProcessingConfig(preserve_metadata=False)
        results = [ExtractionResult(content="a", metadata={"k": 1})]
        merged = EchoProcessor()._merge_results([*results, ExtractionResult(content="b")], config)
        assert merged.metadata == {}


class TestPackageImports:
    """What importing the package costs, and what it still offers."""

    def test_the_harness_imports_without_the_llm_stack(self) -> None:
        """The harness has no LLM dependency — that is the reason this is a
        top-level package rather than part of ``agents/``. Re-exporting
        ``LLMChunkProcessor`` eagerly pulled in ``BaseAgent``, and through it
        ``bmlib.templates`` and jinja2, making the claim true of the module
        but not of the package anyone actually imports.

        A subprocess, because ``sys.modules`` is already populated here.
        """
        code = (
            "import sys\n"
            "from bmlib.context_processor import IterativeContextProcessor\n"
            "print(sorted(m for m in ('bmlib.agents', 'jinja2') if m in sys.modules))\n"
        )
        completed = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=False
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip() == "[]"

    def test_the_llm_processor_is_still_reachable_from_the_package(self) -> None:
        """Deferred, not removed: the import path callers use must not change."""
        from bmlib.context_processor import LLMChunkProcessor

        assert LLMChunkProcessor.__module__ == "bmlib.context_processor.llm_processor"

    def test_a_name_the_package_does_not_have_still_raises(self) -> None:
        import bmlib.context_processor as package

        with pytest.raises(AttributeError, match="no attribute 'not_a_real_name'"):
            package.not_a_real_name

    def test_dir_lists_the_deferred_names_without_hiding_anything(self) -> None:
        """Adding the lazy names must not drop the submodules and dunders.

        Returning ``__all__`` alone does exactly that; the presence check
        alone passes under it, so the second half is what makes this a guard.
        """
        import bmlib.context_processor as package

        listed = dir(package)
        assert {"LLMChunkProcessor", "IterativeContextProcessor"} <= set(listed)
        assert {"base", "data_types"} <= set(listed)
        assert "__name__" in listed


class TestProgressReporting:
    """The optional progress callback."""

    def test_the_callback_sees_each_stage(self) -> None:
        seen: list[ProgressInfo] = []
        config = ProcessingConfig(max_context_chars=100, separator="\n")
        processor = CountingProcessor(config=config, progress_callback=seen.append)
        processor.process([f"item {i} " * 5 for i in range(20)], query="q")
        stages = {info.stage for info in seen}
        assert {"starting", "batching", "extracting", "complete"} <= stages

    def test_a_broken_callback_does_not_break_processing(self) -> None:
        def explode(info: ProgressInfo) -> None:
            raise RuntimeError("callback is broken")

        processor = EchoProcessor(reply="fine", progress_callback=explode)
        result = processor.process(["a"], query="q")
        assert result.status is ProcessingStatus.COMPLETED
        assert result.content == "fine"

    def test_the_reported_progress_actually_advances(self) -> None:
        """``current_item`` was never set by any caller, so every
        ``progress_percent`` a run reported was 0.0 — a progress bar that
        could not move, demonstrated as such in the manual."""
        seen: list[ProgressInfo] = []
        config = ProcessingConfig(max_context_chars=100, separator="\n")
        processor = CountingProcessor(config=config, progress_callback=seen.append)
        processor.process([f"item {i} " * 5 for i in range(20)], query="q")

        level_zero = [
            info.progress_percent
            for info in seen
            if info.stage == "extracting" and info.recursion_level == 0
        ]
        assert len(level_zero) > 1, "not enough batches at level 0 to show movement"
        assert level_zero == sorted(level_zero), "progress went backwards"
        assert max(level_zero) > 0.0, "progress never left zero"

    def test_progress_ends_at_a_hundred_percent(self) -> None:
        seen: list[ProgressInfo] = []
        processor = EchoProcessor(progress_callback=seen.append)
        processor.process(["a", "b", "c"], query="q")
        assert seen[-1].stage == "complete"
        assert seen[-1].progress_percent == 100.0

    def test_a_dropped_item_is_accounted_for_the_moment_it_is_dropped(self) -> None:
        """No extraction will ever reach it, so a count that waited for one
        would leave the bar short of the end for the rest of the run."""
        seen: list[ProgressInfo] = []
        config = ProcessingConfig(
            max_context_chars=60,
            oversized_item_strategy=OversizedItemStrategy.SKIP,
        )
        processor = EchoProcessor(config=config, progress_callback=seen.append)
        processor.process(["fine", "x" * 500], query="q")
        batching = next(info for info in seen if info.stage == "batching")
        assert batching.current_item == 1
        assert batching.total_items == 2
