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

//! Opening a library folder.

use std::path::{Path, PathBuf};

use serde::Serialize;

use super::files::{create_dir_new, io_error, list_names, read_bytes};
use super::{LibraryError, LIBRARY_FORMAT, LIBRARY_VERSION, MARKER_FILE, PAPERS_DIR};

/// A user-chosen folder of papers, opened and checked.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Library {
    root: PathBuf,
}

/// The marker file's content, field order fixed as written.
#[derive(Serialize)]
struct Marker {
    format: &'static str,
    version: u32,
}

impl Library {
    /// Open the library at `root`, creating the marker and `papers/` in an empty folder.
    ///
    /// # Errors
    ///
    /// [`LibraryError::Unreachable`] when `root` is missing or not a folder;
    /// [`LibraryError::NotALibrary`] when it holds anything but a valid marker
    /// (or only ignored names); [`LibraryError::NewerLibrary`] when the marker's
    /// version is newer than [`LIBRARY_VERSION`]; [`LibraryError::ReadOnly`] or
    /// [`LibraryError::Io`] when the marker or `papers/` cannot be written.
    pub fn open(root: &Path) -> Result<Library, LibraryError> {
        let unreachable = |reason: String| LibraryError::Unreachable {
            path: root.to_owned(),
            reason,
        };
        let meta = std::fs::metadata(root).map_err(|e| unreachable(e.to_string()))?;
        if !meta.is_dir() {
            return Err(unreachable("not a folder".to_owned()));
        }
        let not_a_library = |reason: String| LibraryError::NotALibrary {
            path: root.to_owned(),
            reason,
        };
        let marker = root.join(MARKER_FILE);
        if marker.exists() {
            let bytes = read_bytes(&marker).map_err(|e| io_error(&marker, &e, false))?;
            let value: serde_json::Value = serde_json::from_slice(&bytes)
                .map_err(|e| not_a_library(format!("{MARKER_FILE} is not JSON: {e}")))?;
            if value["format"].as_str() != Some(LIBRARY_FORMAT) {
                return Err(not_a_library(format!(
                    "{MARKER_FILE} is not a {LIBRARY_FORMAT} marker"
                )));
            }
            match value["version"].as_u64() {
                Some(v) if v > u64::from(LIBRARY_VERSION) => {
                    return Err(LibraryError::NewerLibrary { version: v })
                }
                Some(_) => {}
                None => {
                    return Err(not_a_library(format!("{MARKER_FILE} has no version")));
                }
            }
        } else {
            let names = list_names(root).map_err(|e| io_error(root, &e, false))?;
            if let Some(first) = names.first() {
                return Err(not_a_library(format!(
                    "the folder is not empty (found {first}) and has no {MARKER_FILE}"
                )));
            }
            let mut text = serde_json::to_string_pretty(&Marker {
                format: LIBRARY_FORMAT,
                version: LIBRARY_VERSION,
            })
            .expect("marker serialises");
            text.push('\n');
            crate::atomic::atomic_write(&marker, text.as_bytes())
                .map_err(|e| io_error(&marker, &e, true))?;
        }
        let papers = root.join(PAPERS_DIR);
        create_dir_new(&papers).map_err(|e| io_error(&papers, &e, true))?;
        Ok(Library {
            root: root.to_owned(),
        })
    }

    /// The library's folder.
    #[must_use]
    pub fn root(&self) -> &Path {
        &self.root
    }
}
