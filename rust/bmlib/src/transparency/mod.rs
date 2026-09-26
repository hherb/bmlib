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

//! Multi-API transparency analysis.
//!
//! | Python | Rust | Status |
//! |---|---|---|
//! | `transparency/models.py` | [`models`] | ported |
//! | `transparency/analyzer.py` | [`analyzer`] | ported |
//!
//! [`TransparencyResult`] is the one item of `models.py` that lives in
//! [`analyzer`] rather than [`models`]: it is what
//! [`analyzer::TransparencyAnalyzer::analyze`] returns, and `models.rs` was
//! ported without it. It could move.

pub mod analyzer;
pub mod models;

pub use analyzer::{
    strip_nested_articles, Analysis, FullTextFetch, PubMedSignals, TransparencyAnalyzer,
    TransparencyResult, UnterminatedMarkupError,
};
pub use models::{
    calculate_risk_level, FullTextStatus, TransparencyRisk, TransparencySettings,
    TransparencyUnknownReason, TrialResultsStatus, ANSWERED_TRIAL_RESULTS_STATUSES,
    MEDIUM_RISK_SCORE_THRESHOLD, NOT_ANSWERED_TRIAL_RESULTS_STATUSES,
    NOT_REFUSED_FULL_TEXT_STATUSES, REFUSED_FULL_TEXT_STATUSES,
};
