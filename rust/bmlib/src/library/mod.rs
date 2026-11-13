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

//! A shared folder of research papers that several apps read and write.
//!
//! This is the pure, file-system-free foundation: identifiers, source kinds,
//! installations and folder names. The module needs no feature gate, so it
//! builds with `default-features = false` (bmlib-ffi's configuration). Python's
//! `bmlib.library` is its twin, held to it by
//! `tests/fixtures/library_cases.json`.

mod error;
mod files;
mod ids;
mod names;
mod open;
mod record;

pub use crate::fulltext::CORRUPT_SUFFIX;
pub use error::LibraryError;
pub use ids::{Installation, PaperIds, PaperInfo, SourceKind};
pub use names::folder_name;
pub use open::Library;
pub use record::{parse_record, render_record, FileEntry, Record, RecordProblem};

/// The `format` value of a library's marker file.
pub const LIBRARY_FORMAT: &str = "bmlib-library";
/// The library layout version this code reads and writes.
pub const LIBRARY_VERSION: u32 = 1;
/// The `format` value of a paper's per-installation record.
pub const RECORD_FORMAT: &str = "bmlib-library-record";
/// The record layout version this code reads and writes.
pub const RECORD_VERSION: u32 = 1;
/// The marker file that makes a folder a library.
pub const MARKER_FILE: &str = "bmlib-library.json";
/// The folder holding one sub-folder per paper.
pub const PAPERS_DIR: &str = "papers";
/// The folder inside a paper's folder holding one record per installation.
pub const RECORDS_DIR: &str = "records";
/// How many hex digits of a file's digest go into a name that must be unique.
pub const DIGEST_PREFIX_HEX: usize = 12;
/// The longest paper-folder name, in UTF-8 bytes.
pub const MAX_FOLDER_NAME_BYTES: usize = 150;
/// The most characters of a title that go into a folder name.
pub const MAX_TITLE_CHARS_IN_NAME: usize = 60;
/// The most characters of the first author that go into a folder name.
pub const MAX_AUTHOR_CHARS_IN_NAME: usize = 40;
/// The highest ` (n)` suffix tried when a folder name is taken.
pub const MAX_NAME_SUFFIX: u32 = 1000;
/// The most characters in an installation's label.
pub const MAX_LABEL_CHARS: usize = 200;
/// File names a scan ignores (as does any name starting with `.`).
pub const IGNORED_NAMES: [&str; 4] = [".DS_Store", ".localized", "desktop.ini", "Thumbs.db"];
/// The pattern of iCloud's placeholder for an online-only file; `{name}` is the file's name.
pub const ICLOUD_PLACEHOLDER: &str = ".{name}.icloud";
