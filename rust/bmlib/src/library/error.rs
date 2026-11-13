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

//! The library's errors. Every variant names what the user must be told.

use std::fmt;
use std::path::PathBuf;

/// Why a library operation failed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LibraryError {
    /// The library folder cannot be reached (an unmounted drive, a revoked grant).
    Unreachable {
        /// The folder.
        path: PathBuf,
        /// What the system said.
        reason: String,
    },
    /// The folder exists but is not a library.
    NotALibrary {
        /// The folder.
        path: PathBuf,
        /// Why it is not one.
        reason: String,
    },
    /// The library was written by a newer version of the layout.
    NewerLibrary {
        /// The layout version found.
        version: u64,
    },
    /// The library is read-only, so nothing can be saved to it.
    ReadOnly {
        /// The folder.
        path: PathBuf,
    },
    /// An identifier is not in its canonical shape.
    BadIdentifier {
        /// `"pmid"`, `"doi"` or `"pmcid"`.
        field: &'static str,
        /// The value refused.
        value: String,
    },
    /// An installation's app, id or label is not acceptable.
    BadInstallation {
        /// `"app"`, `"id"` or `"label"`.
        field: &'static str,
        /// Why.
        reason: String,
    },
    /// A name from a caller or a record cannot be used as a path component.
    BadName {
        /// The name refused.
        name: String,
    },
    /// A file the record names is not in the paper's folder.
    Missing {
        /// The paper's folder.
        folder: String,
        /// The file.
        name: String,
    },
    /// A file exists only as an online placeholder, not yet downloaded.
    NotDownloaded {
        /// The paper's folder.
        folder: String,
        /// The file.
        name: String,
    },
    /// A file's bytes do not match the digest the record holds.
    DigestMismatch {
        /// The paper's folder.
        folder: String,
        /// The file.
        name: String,
        /// The digest recorded.
        expected: String,
        /// The digest found.
        found: String,
    },
    /// A file of that name already exists with other bytes.
    FileClash {
        /// The paper's folder.
        folder: String,
        /// The file.
        name: String,
    },
    /// A record was written by a newer version of the layout.
    NewerRecord {
        /// The paper's folder.
        folder: String,
        /// The record file.
        file: String,
        /// The record version found.
        version: u64,
    },
    /// A file-system operation failed.
    Io {
        /// What was being touched.
        path: PathBuf,
        /// What the system said.
        reason: String,
    },
}

impl fmt::Display for LibraryError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Unreachable { path, reason } => {
                write!(
                    f,
                    "the library at {} cannot be reached: {reason}",
                    path.display()
                )
            }
            Self::NotALibrary { path, reason } => {
                write!(f, "{} is not a library: {reason}", path.display())
            }
            Self::NewerLibrary { version } => write!(
                f,
                "the library uses layout version {version}, newer than this app understands; \
                 update the app"
            ),
            Self::ReadOnly { path } => {
                write!(f, "the library at {} is read-only", path.display())
            }
            Self::BadIdentifier { field, value } => {
                write!(f, "{value:?} is not a valid {field}")
            }
            Self::BadInstallation { field, reason } => {
                write!(f, "the installation's {field} is not acceptable: {reason}")
            }
            Self::BadName { name } => write!(f, "{name:?} cannot be used as a file or folder name"),
            Self::Missing { folder, name } => write!(f, "{name} is missing from {folder}"),
            Self::NotDownloaded { folder, name } => write!(
                f,
                "{name} in {folder} is not downloaded to this device yet; open it in the file \
                 manager to download it"
            ),
            Self::DigestMismatch {
                folder,
                name,
                expected,
                found,
            } => write!(
                f,
                "{name} in {folder} has changed: its digest is {found}, the record says {expected}"
            ),
            Self::FileClash { folder, name } => write!(
                f,
                "{folder} already holds a different file named {name}; nothing was overwritten"
            ),
            Self::NewerRecord {
                folder,
                file,
                version,
            } => write!(
                f,
                "the record {file} in {folder} uses version {version}, newer than this app \
                 understands; update the app"
            ),
            Self::Io { path, reason } => write!(f, "{}: {reason}", path.display()),
        }
    }
}

impl std::error::Error for LibraryError {}
