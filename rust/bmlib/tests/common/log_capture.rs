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

//! A `log` logger that keeps each thread's records, for tests that check what
//! bmlib logged and at which level (#685, #688), as Python's `caplog` does.
//!
//! `log` allows one logger per process, and a test binary runs its tests on
//! several threads at once, so records are kept per thread: a test sees only
//! what its own thread logged. Code that logs from a thread it spawns is not
//! seen.

use std::cell::RefCell;
use std::sync::Once;

/// One record: its target (the Python logger name), level and message.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Record {
    /// The record's target, Python's logger name (`bmlib.secrets`).
    pub target: String,
    /// The record's level.
    pub level: log::Level,
    /// The formatted message.
    pub message: String,
}

thread_local! {
    static RECORDS: RefCell<Vec<Record>> = const { RefCell::new(Vec::new()) };
}

/// The logger: every record, every level, into the calling thread's list.
struct Capture;

impl log::Log for Capture {
    fn enabled(&self, _: &log::Metadata<'_>) -> bool {
        true
    }

    fn log(&self, record: &log::Record<'_>) {
        RECORDS.with(|records| {
            records.borrow_mut().push(Record {
                target: record.target().to_string(),
                level: record.level(),
                message: record.args().to_string(),
            })
        });
    }

    fn flush(&self) {}
}

static CAPTURE: Capture = Capture;
static INSTALL: Once = Once::new();

/// Install the logger (once per process) and forget this thread's records, so
/// [`take`] returns only what is logged from here on.
pub fn start() {
    INSTALL.call_once(|| {
        log::set_logger(&CAPTURE).expect("no other logger in a test binary");
        log::set_max_level(log::LevelFilter::Trace);
    });
    RECORDS.with(|records| records.borrow_mut().clear());
}

/// This thread's records since [`start`], under `target`, removed from the list.
pub fn take(target: &str) -> Vec<Record> {
    RECORDS.with(|records| {
        let (taken, kept) = records
            .borrow_mut()
            .drain(..)
            .partition(|record| record.target == target);
        *records.borrow_mut() = kept;
        taken
    })
}
