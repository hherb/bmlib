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

//! The context windows bmlib knows by heart.
//!
//! [`KNOWN_CONTEXT_WINDOWS`] holds `(provider, model, window in tokens)`. Every
//! row is a figure read off the provider's own model page;
//! `tests/fixtures/context_window.json` is the record of each row's source and
//! read date, and this table and Python's `bmlib.llm.context_window` are both
//! tested equal to it. Keys match exactly: a dated snapshot id or a different
//! case is *unknown*, not guessed, because a wrong window silently truncates
//! or overflows a prompt.

/// `(provider, model, window in tokens)`, one row per known model.
pub const KNOWN_CONTEXT_WINDOWS: &[(&str, &str, u64)] = &[
    ("openai", "gpt-6-astra", 1_050_000),
    ("openai", "gpt-6.1-sol", 1_050_000),
    ("openai", "gpt-6.1-terra", 1_050_000),
    ("openai", "gpt-6-luna", 1_050_000),
    ("openai", "gpt-4o-mini", 128_000),
];

/// The context window (tokens) for an exact `provider` and `model`, or `None`
/// when the pair is not in [`KNOWN_CONTEXT_WINDOWS`]. There is no prefix,
/// case-folding or suffix matching.
pub fn known_context_window(provider: &str, model: &str) -> Option<u64> {
    KNOWN_CONTEXT_WINDOWS
        .iter()
        .find(|(p, m, _)| *p == provider && *m == model)
        .map(|(_, _, w)| *w)
}
