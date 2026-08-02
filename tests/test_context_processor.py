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
from typing import Any

import pytest

from bmlib.context_processor import (
    Batch,
    ConsolidatedItem,
    ConsolidationStrategy,
    ExtractionResult,
    IterativeContextProcessor,
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
