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

//! A [`FetchSink`] that keeps what a test wants to look at afterwards.
//!
//! A production sink must **not** hold the batch — that is the whole point of
//! the interface, and `sync`'s `DayBuffer` drains its buffer at every part
//! boundary. A test wants the opposite: the records and the progress events, in
//! order, to compare against Python. One implementation rather than one per test
//! file, because the two harnesses that need it would otherwise drift on what
//! "the records the walk delivered" means.
//!
//! The corpus's `record_count` is
//! [`records`](RecordingSink::records)`.len()` — the same thing
//! `FetchOutcome::record_count` reports, which is what makes a test able to check
//! the two agree.

use bmlib::publications::fetchers::{FetchSink, Progress};
use bmlib::publications::models::FetchedRecord;

/// Everything a walk handed over, kept in order.
#[derive(Default)]
pub struct RecordingSink {
    /// Every record delivered, in reading order.
    pub records: Vec<FetchedRecord>,
    /// Every progress event, in order.
    pub progress: Vec<Progress>,
}

impl RecordingSink {
    /// A sink that has recorded nothing.
    #[must_use]
    pub fn new() -> Self {
        RecordingSink::default()
    }

    /// The progress event's `(delivered, promised)` when it is a page boundary.
    ///
    /// The corpus's `progress` list is built from these; a part boundary is not
    /// one, which is why the filter is here rather than at each call site.
    #[must_use]
    pub fn pages(&self) -> Vec<(i64, Option<i64>)> {
        self.progress
            .iter()
            .filter_map(|event| match event {
                Progress::Page {
                    delivered,
                    promised,
                } => Some((*delivered, *promised)),
                Progress::PartFinished(_) => None,
            })
            .collect()
    }
}

impl FetchSink for RecordingSink {
    fn record(&mut self, record: FetchedRecord) {
        self.records.push(record);
    }

    fn progress(&mut self, progress: Progress) {
        self.progress.push(progress);
    }
}
