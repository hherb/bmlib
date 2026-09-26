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

//! Reconcile what a fetcher's page walk delivered against what the source
//! promised.
//!
//! A port of `bmlib/publications/fetchers/_reconcile.py`.
//!
//! Every built-in fetcher learns a record count from its source before walking
//! pages — PubMed's `<Count>`, OpenAlex's `meta.count`, bioRxiv's
//! `messages[0].total` — and none of them used to compare that promise against
//! what arrived (#88). A walk that stopped early therefore returned
//! `status="completed"`, `sync` wrote the day to `download_days` as done, and
//! day selection never offered that day again once it was in the past:
//! **the records are permanently absent, with nothing logged above INFO.**
//!
//! # Three rules, deliberately different in kind
//!
//! **Stalled** — a page delivering nothing while the source's own count says
//! records remain. That is broken outright whatever the magnitude, so it
//! carries **no threshold**. It is also the only rule that catches a history
//! session expiring on the *last* page of a long walk.
//!
//! **Unreconcilable** — records arrived but the source named no count to judge
//! them against. There is no threshold to apply and no way to tell a complete
//! walk from a truncated one, so the day cannot be claimed as complete: an
//! unverifiable success is the failure this module exists to prevent. A day
//! that delivered *nothing* against no count is the ordinary quiet day and
//! passes silently, which is what keeps bioRxiv's total-omitting quiet
//! response working.
//!
//! **Shortfall** — a walk that ran to its natural end and still came up short.
//! Here the sources are not exact, so a threshold is unavoidable, and it is a
//! **floor rather than strict inequality** for a reason that is easy to miss: a
//! day marked `failed` is re-offered on *every* later sync run, so failing on a
//! gap with a benign and permanent cause re-fetches that day for ever, silently
//! growing with the date range. Known benign causes include a record withdrawn
//! between search and fetch and an index updated mid-walk.

/// Fraction of the promised count below which a naturally-ended walk is treated
/// as broken rather than merely short.
///
/// Fixed **before measurement**, unlike the allow-lists and log levels
/// elsewhere in bmlib that were set from sampled populations. It says only what
/// can be argued without data: no benign cause plausibly removes half a day's
/// records. #92 is the follow-up that measures the real
/// delivered-versus-promised distribution per source and tightens it; until
/// that runs, a reader must not take 0.5 as a measured value.
///
/// **Exclusive**: delivering exactly this fraction passes.
pub const SHORTFALL_FAILURE_RATIO: f64 = 0.5;

/// What the comparison concluded.
///
/// At most one field is ever set; both are `None` for a walk that delivered
/// what was promised.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct Reconciliation {
    /// Set when the day must be recorded `failed`; use as `FetchResult.error`.
    pub failure: Option<String>,
    /// Set when the day completes but came up short; use as `FetchResult.note`.
    ///
    /// Returning this rather than only logging it is what lets a caller find a
    /// short day afterwards. A day may be missing nearly half its records on
    /// this path, and a log line is not a surface anything can query.
    pub note: Option<String>,
}

impl Reconciliation {
    /// A walk that delivered what was promised.
    #[must_use]
    pub fn clean() -> Self {
        Reconciliation::default()
    }

    /// Whether the day must be recorded as failed.
    #[must_use]
    pub fn is_failure(&self) -> bool {
        self.failure.is_some()
    }
}

/// Judge a finished page walk against the count its source promised.
///
/// # Why `promised` is an `Option` and not a number
///
/// `None` is **not** the same as zero and must not be flattened into it: zero
/// is a source saying "this day is empty", which a delivery of zero satisfies,
/// while `None` is a source saying nothing at all, against which no delivery
/// can be verified.
///
/// # `delivered` is what the *server* handed over
///
/// Not what the fetcher chose to parse. PubMed's efetch returns
/// `<PubmedBookArticle>` elements that the fetcher skips, so counting parsed
/// records here would report a phantom shortfall on every day carrying a book
/// chapter.
#[must_use]
pub fn reconcile_delivery(
    source: &str,
    date_str: &str,
    delivered: i64,
    promised: Option<i64>,
    stalled: bool,
) -> Reconciliation {
    let Some(promised) = promised else {
        if delivered <= 0 {
            return Reconciliation::clean();
        }
        return Reconciliation {
            failure: Some(format!(
                "{source} delivered {delivered} records for {date_str} but reported no \
                 count to reconcile them against, so the walk cannot be shown to have \
                 finished"
            )),
            note: None,
        };
    };

    // `delivered >= promised` is the whole test for every input this can
    // receive. The `promised <= 0` half is **subsumed**, not load-bearing:
    // `delivered` is a count of records the server handed over and so is never
    // negative, and every `promised <= 0` is therefore `<= delivered`. Checked
    // exhaustively over `delivered` in 0..6 and `promised` in -3..6, where the
    // two spellings agree on every input.
    //
    // It is kept because it is *why* the line is right — a source promising
    // zero is saying the day is empty, and no delivery can miss that target —
    // and because `delivered` is the parameter whose contract would have to
    // change for it to matter. Mutation found no test that distinguishes them,
    // which is the honest reporting of a guard that cannot be exercised.
    if promised <= 0 || delivered >= promised {
        return Reconciliation::clean();
    }

    let counted = format!("{source} delivered {delivered} of {promised} records for {date_str}");

    if stalled {
        return Reconciliation {
            failure: Some(format!(
                "{counted} and then returned an empty page, so the walk stopped short \
                 (an expired history session, or an index that moved under the walk)"
            )),
            note: None,
        };
    }

    // The floor is exclusive, so exactly half passes.
    if (delivered as f64) < (promised as f64) * SHORTFALL_FAILURE_RATIO {
        return Reconciliation {
            failure: Some(format!(
                "{counted} — below the {:.0}% floor, so the walk is treated as truncated \
                 rather than short",
                SHORTFALL_FAILURE_RATIO * 100.0
            )),
            note: None,
        };
    }

    Reconciliation {
        failure: None,
        note: Some(format!(
            "{counted}; recording the day as completed, since a shortfall this small has \
             benign causes (a record withdrawn between search and fetch, an index \
             updated mid-walk)"
        )),
    }
}
