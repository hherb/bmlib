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

//! Splitting the PRISMA 2020 checklist into parts a model's reply can hold.
//!
//! A run asks the model to judge a part of the checklist at a time, so that
//! one reply (an explanation and up to three quotes per item) fits the
//! model's output budget. [`plan_parts`] packs items greedily, in the order
//! given. Nothing is cut: an item that cannot fit a part even alone is
//! refused with [`PartsError::ItemTooLarge`].
//!
//! A port of `bmlib/quality/prisma2020_parts.py`;
//! `tests/fixtures/prisma2020_parts.json` pins both.

use std::fmt;

use super::prisma2020_checklist::{item, ChecklistItem, ChecklistSection};

/// The characters of a reply's JSON frame, counted once per part.
pub const PART_OVERHEAD_CHARS: usize = 400;

/// The words that open [`part_query`].
const QUERY_PREFIX: &str = "Everything needed to judge these PRISMA 2020 items: ";

/// One part of a split: the ids of its items, in the order given.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PartPlan {
    /// The checklist ids of the part's items, for example `["5", "6", "13c"]`.
    pub item_ids: Vec<&'static str>,
}

impl PartPlan {
    /// A label for the part: `"Methods 5–13c"` within a section, or
    /// `"Methods 13d – Results 17"` across sections (an en dash in both). A
    /// one-item part is `"Methods 13d"`.
    pub fn label(&self) -> String {
        let end = |id: &'static str| (id, item(id).map(|row| section_label(row.section)));
        let (Some(first), Some(last)) = (self.item_ids.first(), self.item_ids.last()) else {
            return String::new();
        };
        let ((first, first_section), (last, last_section)) = (end(first), end(last));
        let first_section = first_section.unwrap_or("");
        if first == last {
            format!("{first_section} {first}")
        } else if first_section == last_section.unwrap_or("") {
            format!("{first_section} {first}–{last}")
        } else {
            format!(
                "{first_section} {first} – {} {last}",
                last_section.unwrap_or("")
            )
        }
    }
}

/// A section's name as a label.
fn section_label(section: ChecklistSection) -> &'static str {
    match section {
        ChecklistSection::Title => "Title",
        ChecklistSection::Abstract => "Abstract",
        ChecklistSection::Introduction => "Introduction",
        ChecklistSection::Methods => "Methods",
        ChecklistSection::Results => "Results",
        ChecklistSection::Discussion => "Discussion",
        ChecklistSection::OtherInformation => "Other information",
    }
}

/// Why a split failed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PartsError {
    /// An item's reply, with the frame, is larger than the budget.
    ItemTooLarge {
        /// The item's id.
        id: &'static str,
        /// The characters the item needs: its `reply_chars` plus
        /// [`PART_OVERHEAD_CHARS`].
        needed: usize,
        /// The reply budget given.
        budget: usize,
    },
}

impl PartsError {
    /// The error's kind string: `"item_too_large"`.
    pub fn kind(&self) -> &'static str {
        match self {
            PartsError::ItemTooLarge { .. } => "item_too_large",
        }
    }
}

impl fmt::Display for PartsError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            PartsError::ItemTooLarge { id, needed, budget } => write!(
                f,
                "PRISMA item {id} needs {needed} reply characters, over the budget of {budget}"
            ),
        }
    }
}

impl std::error::Error for PartsError {}

/// Split `items` into parts whose replies fit `reply_budget_chars`.
///
/// Greedy, in the order given: a part takes the next item while
/// [`PART_OVERHEAD_CHARS`] plus the sum of the items' `reply_chars` is at most
/// the budget. Callers pass the whole checklist for a run, or a subset for a
/// re-check or a split part.
///
/// # Errors
///
/// [`PartsError::ItemTooLarge`] for the first item that does not fit a part
/// alone, before any part is returned.
pub fn plan_parts(
    items: &[&'static ChecklistItem],
    reply_budget_chars: usize,
) -> Result<Vec<PartPlan>, PartsError> {
    let mut parts: Vec<PartPlan> = Vec::new();
    let mut used = 0usize;
    for row in items {
        let alone = PART_OVERHEAD_CHARS.saturating_add(row.reply_chars);
        if alone > reply_budget_chars {
            return Err(PartsError::ItemTooLarge {
                id: row.id,
                needed: alone,
                budget: reply_budget_chars,
            });
        }
        match parts.last_mut() {
            Some(part) if used.saturating_add(row.reply_chars) <= reply_budget_chars => {
                part.item_ids.push(row.id);
                used = used.saturating_add(row.reply_chars);
            }
            _ => {
                parts.push(PartPlan {
                    item_ids: vec![row.id],
                });
                used = alone;
            }
        }
    }
    Ok(parts)
}

/// The condensation query for a part: the items' `condense_hint`s joined with
/// `"; "`, after "Everything needed to judge these PRISMA 2020 items: ".
pub fn part_query(items: &[&'static ChecklistItem]) -> String {
    let hints: Vec<&str> = items.iter().map(|row| row.condense_hint).collect();
    format!("{QUERY_PREFIX}{}", hints.join("; "))
}
