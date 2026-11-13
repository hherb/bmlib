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

use super::files::{
    create_dir_new, exists, first_user_entry, io_error, is_dir, read_bytes, sha256_hex, write_once,
};
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

fn not_a_library(root: &Path, reason: String) -> LibraryError {
    LibraryError::NotALibrary {
        path: root.to_owned(),
        reason,
    }
}

/// Validate a marker's bytes: JSON, the right format, and a version that is a
/// positive integer within `u32` (the rule records use) no newer than ours.
fn check_marker(root: &Path, bytes: &[u8]) -> Result<(), LibraryError> {
    let value: serde_json::Value = serde_json::from_slice(bytes)
        .map_err(|e| not_a_library(root, format!("{MARKER_FILE} is not JSON: {e}")))?;
    if value["format"].as_str() != Some(LIBRARY_FORMAT) {
        return Err(not_a_library(
            root,
            format!("{MARKER_FILE} is not a {LIBRARY_FORMAT} marker"),
        ));
    }
    let version = value["version"]
        .as_u64()
        .and_then(|v| u32::try_from(v).ok())
        .filter(|v| *v >= 1)
        .ok_or_else(|| {
            not_a_library(
                root,
                format!("{MARKER_FILE}: version must be a positive integer"),
            )
        })?;
    if version > LIBRARY_VERSION {
        return Err(LibraryError::NewerLibrary {
            version: u64::from(version),
        });
    }
    Ok(())
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
        if !is_dir(root) {
            let reason = if exists(root) {
                "not a folder"
            } else {
                "no such folder"
            };
            return Err(LibraryError::Unreachable {
                path: root.to_owned(),
                reason: reason.to_owned(),
            });
        }
        let marker = root.join(MARKER_FILE);
        if exists(&marker) {
            check_marker(
                root,
                &read_bytes(&marker).map_err(|e| io_error(&marker, &e, false))?,
            )?;
        } else {
            if let Some(first) = first_user_entry(root).map_err(|e| io_error(root, &e, false))? {
                return Err(not_a_library(
                    root,
                    format!("the folder is not empty (found {first}) and has no {MARKER_FILE}"),
                ));
            }
            let mut text = serde_json::to_string_pretty(&Marker {
                format: LIBRARY_FORMAT,
                version: LIBRARY_VERSION,
            })
            .expect("marker serialises");
            text.push('\n');
            match write_once(&marker, text.as_bytes(), &sha256_hex(text.as_bytes())) {
                Ok(_) => {}
                // Another opener published a marker first: it decides, not ours.
                Err(LibraryError::FileClash { .. }) => {
                    check_marker(
                        root,
                        &read_bytes(&marker).map_err(|e| io_error(&marker, &e, false))?,
                    )?;
                }
                Err(e) => return Err(e),
            }
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
