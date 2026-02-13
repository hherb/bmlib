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
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


@dataclass
class TokenUsageRecord:
    """Record of a single LLM call's token usage."""

    model: str
    input_tokens: int
    output_tokens: int
    timestamp: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
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
        """Record token usage for a single LLM call."""
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
        """Return an aggregate summary of all recorded usage."""
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
        """Clear all recorded usage."""
        with self._lock:
            self._records.clear()
            self._total_input = 0
            self._total_output = 0
            self._total_cost = 0.0

    def get_recent_records(self, count: int = 10) -> list[TokenUsageRecord]:
        """Return the *count* most recent usage records."""
        with self._lock:
            return list(self._records[-count:])


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------
_global_tracker: TokenTracker | None = None
_tracker_lock = threading.Lock()


def get_token_tracker() -> TokenTracker:
    """Return the global :class:`TokenTracker` singleton (created on first call)."""
    global _global_tracker
    with _tracker_lock:
        if _global_tracker is None:
            _global_tracker = TokenTracker()
        return _global_tracker


def reset_token_tracker() -> None:
    """Replace the global :class:`TokenTracker` with a fresh instance."""
    global _global_tracker
    with _tracker_lock:
        _global_tracker = TokenTracker()
