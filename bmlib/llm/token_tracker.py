"""Thread-safe token usage and cost tracking.

Maintains a running total of token usage and estimated costs across all
LLM calls during a session.

Usage::

    from bmlib.llm import get_token_tracker

    tracker = get_token_tracker()
    tracker.record_usage(
        model="anthropic:claude-3-haiku",
        input_tokens=100,
        output_tokens=50,
        cost=0.00045,
    )
    summary = tracker.get_summary()
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class TokenUsageRecord:
    """Record of a single LLM call's token usage."""

    model: str
    input_tokens: int
    output_tokens: int
    timestamp: datetime = field(default_factory=datetime.now)
    cost_usd: float = 0.0


@dataclass
class TokenUsageSummary:
    """Aggregate summary of token usage."""

    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    call_count: int = 0
    by_model: dict[str, dict] = field(default_factory=dict)


class TokenTracker:
    """Thread-safe token usage tracker."""

    def __init__(self) -> None:
        self._records: list[TokenUsageRecord] = []
        self._lock = threading.Lock()
        self._total_input = 0
        self._total_output = 0
        self._total_cost = 0.0

    def record_usage(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost: float = 0.0,
    ) -> None:
        record = TokenUsageRecord(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
        )
        with self._lock:
            self._records.append(record)
            self._total_input += input_tokens
            self._total_output += output_tokens
            self._total_cost += cost

        logger.debug(
            "Recorded usage: %s, in=%d, out=%d, cost=$%.6f",
            model, input_tokens, output_tokens, cost,
        )

    def get_summary(self) -> TokenUsageSummary:
        with self._lock:
            by_model: dict[str, dict] = {}
            for record in self._records:
                if record.model not in by_model:
                    by_model[record.model] = {
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cost_usd": 0.0,
                        "calls": 0,
                    }
                by_model[record.model]["input_tokens"] += record.input_tokens
                by_model[record.model]["output_tokens"] += record.output_tokens
                by_model[record.model]["cost_usd"] += record.cost_usd
                by_model[record.model]["calls"] += 1

            return TokenUsageSummary(
                total_input_tokens=self._total_input,
                total_output_tokens=self._total_output,
                total_tokens=self._total_input + self._total_output,
                total_cost_usd=self._total_cost,
                call_count=len(self._records),
                by_model=by_model,
            )

    def reset(self) -> None:
        with self._lock:
            self._records.clear()
            self._total_input = 0
            self._total_output = 0
            self._total_cost = 0.0

    def get_recent_records(self, count: int = 10) -> list[TokenUsageRecord]:
        with self._lock:
            return list(self._records[-count:])


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------
_global_tracker: Optional[TokenTracker] = None
_tracker_lock = threading.Lock()


def get_token_tracker() -> TokenTracker:
    global _global_tracker
    with _tracker_lock:
        if _global_tracker is None:
            _global_tracker = TokenTracker()
        return _global_tracker


def reset_token_tracker() -> None:
    global _global_tracker
    with _tracker_lock:
        _global_tracker = TokenTracker()
