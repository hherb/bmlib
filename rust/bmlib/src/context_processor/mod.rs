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

//! Hierarchical map-reduce over content that exceeds one context window.
//!
//! A port of `bmlib/context_processor/` (1,710 Python lines across four
//! files):
//!
//! | Python | Here | Status |
//! |---|---|---|
//! | `context_processor/data_types.py` | [`data_types`] | ported |
//! | `context_processor/base.py` | [`base`] | ported |
//! | `context_processor/llm_processor.py` | — | follows `llm` |
//! | `context_processor/__init__.py` | this file | ported |
//!
//! The harness carries **no LLM dependency** — the extractor is supplied by
//! the caller — which is why the package is top-level rather than under
//! `llm/`.
//!
//! # Why the API reads differently from Python
//!
//! Python typed items as `Any` and dispatched with `isinstance`. Rust needs a
//! closed set, so items are [`Item`] trait objects carrying two methods:
//! `render` and `split`. A caller with a new item type implements those
//! instead of finding every `isinstance` site, and "cannot split this type" is
//! a declared method rather than a `NotImplementedError` from a type check.

pub mod base;
pub mod data_types;
pub mod llm_processor;

pub use base::{
    text_items, ConsolidatedItemRef, Item, ItemRef, IterativeContextProcessor, Preformatted,
    ProcessingCore, ProgressCallback, SplitError, TextItem,
};
pub use data_types::{
    ConfigError, ConsolidatedItem, ConsolidationStrategy, ExtractionResult, OversizedItemError,
    OversizedItemStrategy, ProcessingConfig, ProcessingResult, ProcessingStatus, ProgressInfo,
    DEFAULT_MAX_CONTEXT_CHARS, DEFAULT_MAX_RECURSION_DEPTH, DEFAULT_MIN_ITEMS_FOR_RECURSION,
    DEFAULT_OVERLAP_CHARS, DEFAULT_SEPARATOR,
};
