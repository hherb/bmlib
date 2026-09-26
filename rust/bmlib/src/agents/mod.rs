// bmlib — shared library for biomedical literature tools
// Copyright (C) 2024-2026 Dr Horst Herb
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with this program.  If not, see <https://www.gnu.org/licenses/>.

//! LLM-driven task support.
//!
//! | Python | Rust | Status |
//! |---|---|---|
//! | `agents/metrics.py` | [`metrics`] | ported |
//! | `agents/base.py` | [`base`] | ported (fixes #300) |

pub mod base;
pub mod metrics;

pub use base::{
    chat_json, classify_stop_reason, json_type_name, ChatJsonError, ChatJsonOutcome, ChatSource,
    JsonAttempt, StopReasonClass, TRUNCATION_STOP_REASONS,
};
pub use metrics::{group, round_to, MetricsSnapshot, Monotonic, PerformanceMetrics};
