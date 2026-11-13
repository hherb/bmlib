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

//! The only module of the library that touches the file system, so a platform
//! without ordinary paths (phase 4's Android) swaps this one file.
//!
//! Nothing here overwrites: [`write_once`] publishes with
//! [`crate::atomic::atomic_write`] and refuses a different file of the same name.

use std::fs;
use std::io;
use std::path::Path;

use sha2::{Digest, Sha256};

use super::names::is_reserved;
use super::{LibraryError, ICLOUD_PLACEHOLDER, IGNORED_NAMES};
use crate::atomic::{TEMP_SUFFIX, TEMP_SUFFIX_LEN};

/// Whether `write_once` made the file or found the same bytes already there.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum Written {
    /// The file did not exist and now does.
    New,
    /// A file of that name with the same bytes was already there.
    AlreadyThere,
}

/// The lowercase hex SHA-256 of `bytes`.
pub fn sha256_hex(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

/// `name` if it is one safe path component, else [`LibraryError::BadName`].
///
/// Refuses, on every platform, the empty name, any name holding `/`, `\\`, NUL
/// or `:`, a name ending in `.` or a space or starting with a space, and a
/// Windows device name (`con`, `NUL.txt`). Windows drops trailing dots and
/// spaces (`...` and `x.` would name `papers` itself or `x`), `C:` replaces
/// a joined path and `a:b` names a stream, so a name from a record or a caller
/// can never reach anything but its own entry. The library's own names never
/// take these shapes ([`super::folder_name`] sanitises).
pub(crate) fn component(name: &str) -> Result<&str, LibraryError> {
    let bad = name.is_empty()
        || name.contains(['/', '\\', '\0', ':'])
        || name.ends_with(['.', ' '])
        || name.starts_with(' ')
        || is_reserved(name);
    if bad {
        Err(LibraryError::BadName {
            name: name.to_owned(),
        })
    } else {
        Ok(name)
    }
}

/// Turn an I/O failure at `path` into the library's error: a refused or
/// read-only write is [`LibraryError::ReadOnly`], anything else [`LibraryError::Io`].
pub(crate) fn io_error(path: &Path, err: &io::Error, write: bool) -> LibraryError {
    let read_only = write
        && matches!(
            err.kind(),
            io::ErrorKind::PermissionDenied | io::ErrorKind::ReadOnlyFilesystem
        );
    if read_only {
        LibraryError::ReadOnly {
            path: path.to_owned(),
        }
    } else {
        LibraryError::Io {
            path: path.to_owned(),
            reason: err.to_string(),
        }
    }
}

/// Every entry of `dir` as (raw bytes, display name), sorted by the raw bytes.
///
/// The display name is a lossy UTF-8 conversion (a name that is not UTF-8 shows
/// U+FFFD); it is only ever shown or compared for prefixes, never used as a path.
fn entries(dir: &Path) -> io::Result<Vec<(Vec<u8>, String)>> {
    let mut all = Vec::new();
    for entry in fs::read_dir(dir)? {
        let os = entry?.file_name();
        all.push((
            os.as_encoded_bytes().to_vec(),
            os.to_string_lossy().into_owned(),
        ));
    }
    all.sort();
    Ok(all)
}

/// The hex digits in the temporary name Python's `atomic_write` uses,
/// `.{name}.{uuid4 hex}.tmp`.
const PY_TEMP_HEX: usize = 32;
/// The extension of Python's `atomic_write` temporaries.
const PY_TEMP_EXT: &str = ".tmp";

/// Whether `name` is a temporary of either language's `atomic_write`: Rust's
/// `{name}{TEMP_SUFFIX}{42-character token}` or Python's
/// `.{name}.{32 hex}.tmp`. One is there while a write is in flight, and stays
/// after a crash; it is never a user's file and never a paper's.
pub(crate) fn is_temp_name(name: &str) -> bool {
    let token = TEMP_SUFFIX_LEN - TEMP_SUFFIX.len();
    let rust = name.len() > TEMP_SUFFIX_LEN
        && name
            .get(..name.len() - token)
            .is_some_and(|head| head.ends_with(TEMP_SUFFIX));
    let python = name.starts_with('.')
        && name.strip_suffix(PY_TEMP_EXT).is_some_and(|stem| {
            stem.len() > PY_TEMP_HEX + 2
                && stem
                    .get(stem.len() - PY_TEMP_HEX - 1..)
                    .is_some_and(|tail| {
                        tail.starts_with('.')
                            && tail[1..]
                                .bytes()
                                .all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
                    })
        });
    rust || python
}

/// What [`listing`] found in a folder, each list sorted by bytes.
#[derive(Debug, Default)]
pub(crate) struct Listing {
    /// The names a scan looks at: not [`IGNORED_NAMES`], not starting with `.`.
    pub(crate) names: Vec<String>,
    /// Files present only as an iCloud placeholder, by their own name
    /// (`.x.pdf.icloud` gives `x.pdf`).
    pub(crate) placeholders: Vec<String>,
    /// Temporaries of `atomic_write` ([`is_temp_name`]), in flight or left by a crash.
    pub(crate) temps: Vec<String>,
}

/// The entries of `dir`, sorted into a [`Listing`]. A name that is not UTF-8
/// is kept, shown lossily.
pub(crate) fn listing(dir: &Path) -> io::Result<Listing> {
    let mut out = Listing::default();
    for (_, name) in entries(dir)? {
        if is_temp_name(&name) {
            out.temps.push(name);
        } else if let Some(own) = placeholder_for(&name) {
            out.placeholders.push(own.to_owned());
        } else if !name.starts_with('.') && !IGNORED_NAMES.contains(&name.as_str()) {
            out.names.push(name);
        }
    }
    Ok(out)
}

/// The names in `dir`, sorted by bytes, leaving out [`IGNORED_NAMES`], any
/// name starting with `.` and `atomic_write` temporaries. A name that is not
/// UTF-8 is kept, shown lossily.
pub(crate) fn list_names(dir: &Path) -> io::Result<Vec<String>> {
    Ok(listing(dir)?.names)
}

/// The file an iCloud placeholder name (`.<name>.icloud`) stands for, if it is one.
fn placeholder_for(name: &str) -> Option<&str> {
    let (pre, post) = ICLOUD_PLACEHOLDER
        .split_once("{name}")
        .expect("the pattern holds {name}");
    name.strip_prefix(pre)
        .and_then(|n| n.strip_suffix(post))
        .filter(|own| !own.is_empty())
}

/// Whether `name` is an iCloud placeholder, `.<something>.icloud`.
fn is_placeholder_name(name: &str) -> bool {
    placeholder_for(name).is_some()
}

/// The first entry of `dir` that belongs to the user: not an ignored name,
/// dotfile or `atomic_write` temporary, but an iCloud placeholder does count
/// (it stands for a real file).
pub(crate) fn first_user_entry(dir: &Path) -> io::Result<Option<String>> {
    Ok(entries(dir)?.into_iter().map(|(_, name)| name).find(|n| {
        !is_temp_name(n)
            && (is_placeholder_name(n)
                || (!n.starts_with('.') && !IGNORED_NAMES.contains(&n.as_str())))
    }))
}

/// Whether `path` is an existing directory.
pub(crate) fn is_dir(path: &Path) -> bool {
    path.is_dir()
}

/// Whether anything exists at `path`.
pub(crate) fn exists(path: &Path) -> bool {
    path.exists()
}

/// Whether `dir` holds iCloud's online-only placeholder for `name`.
pub(crate) fn has_placeholder(dir: &Path, name: &str) -> bool {
    dir.join(ICLOUD_PLACEHOLDER.replace("{name}", name))
        .exists()
}

/// Read a whole file.
pub(crate) fn read_bytes(path: &Path) -> io::Result<Vec<u8>> {
    fs::read(path)
}

/// Publish `bytes` at `path` unless a file is there. `sha256` is the digest of
/// `bytes`; an existing file with that digest is [`Written::AlreadyThere`], one
/// with another is [`LibraryError::FileClash`] and is left untouched.
pub(crate) fn write_once(path: &Path, bytes: &[u8], sha256: &str) -> Result<Written, LibraryError> {
    match fs::read(path) {
        Ok(existing) => {
            return if sha256_hex(&existing) == sha256 {
                Ok(Written::AlreadyThere)
            } else {
                Err(LibraryError::FileClash {
                    folder: path
                        .parent()
                        .map(|p| p.display().to_string())
                        .unwrap_or_default(),
                    name: path
                        .file_name()
                        .map(|n| n.to_string_lossy().into_owned())
                        .unwrap_or_default(),
                })
            };
        }
        Err(e) if e.kind() == io::ErrorKind::NotFound => {}
        Err(e) => return Err(io_error(path, &e, false)),
    }
    crate::atomic::atomic_write(path, bytes).map_err(|e| io_error(path, &e, true))?;
    Ok(Written::New)
}

/// Make the directory `path` (its parent must exist); `false` when it already exists.
pub(crate) fn create_dir_new(path: &Path) -> io::Result<bool> {
    match fs::create_dir(path) {
        Ok(()) => Ok(true),
        Err(e) if e.kind() == io::ErrorKind::AlreadyExists => Ok(false),
        Err(e) => Err(e),
    }
}

/// Make the directory `path` and any missing parents; fine when it exists.
pub(crate) fn create_dir_all(path: &Path) -> io::Result<()> {
    fs::create_dir_all(path)
}

/// Rename `from` to `to`.
pub(crate) fn rename(from: &Path, to: &Path) -> io::Result<()> {
    fs::rename(from, to)
}

/// Publish `bytes` at `path` atomically, replacing what is there. Only for a
/// file its writer alone owns (an installation's own record).
pub(crate) fn write_replace(path: &Path, bytes: &[u8]) -> Result<(), LibraryError> {
    crate::atomic::atomic_write(path, bytes).map_err(|e| io_error(path, &e, true))
}

/// Delete one file.
pub(crate) fn remove_file(path: &Path) -> io::Result<()> {
    fs::remove_file(path)
}

/// Delete a directory and everything in it.
pub(crate) fn remove_dir_all(path: &Path) -> io::Result<()> {
    fs::remove_dir_all(path)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A fresh empty directory under the system temp dir, removed by the caller.
    fn scratch(tag: &str) -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!("bmlib-files-{tag}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn component_refuses_paths() {
        for bad in [
            "", ".", "..", "a/b", "a\\b", "../x", "x\0", "...", " ", ". ", "x.", "C:", "a:b",
            "con", "NUL.txt",
        ] {
            assert!(
                matches!(component(bad), Err(LibraryError::BadName { .. })),
                "{bad:?}"
            );
        }
        assert_eq!(component("2024 Smith").unwrap(), "2024 Smith");
    }

    #[test]
    fn write_once_never_overwrites() {
        let dir = scratch("once");
        let file = dir.join("a.pdf");
        assert_eq!(
            write_once(&file, b"one", &sha256_hex(b"one")).unwrap(),
            Written::New
        );
        assert_eq!(
            write_once(&file, b"one", &sha256_hex(b"one")).unwrap(),
            Written::AlreadyThere
        );
        let err = write_once(&file, b"two", &sha256_hex(b"two")).unwrap_err();
        assert!(matches!(err, LibraryError::FileClash { .. }));
        assert_eq!(fs::read(&file).unwrap(), b"one");
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn list_names_sorts_and_skips_ignored() {
        let dir = scratch("list");
        for n in ["b", "a", ".DS_Store", ".hidden", "Thumbs.db", "C"] {
            fs::write(dir.join(n), b"").unwrap();
        }
        assert_eq!(list_names(&dir).unwrap(), ["C", "a", "b"]);
        fs::write(dir.join(".x.pdf.icloud"), b"").unwrap();
        assert!(has_placeholder(&dir, "x.pdf"));
        assert!(!has_placeholder(&dir, "y.pdf"));
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn first_user_entry_counts_placeholders_not_dotfiles() {
        let dir = scratch("user");
        for n in [".DS_Store", ".hidden", "Thumbs.db"] {
            fs::write(dir.join(n), b"").unwrap();
        }
        assert_eq!(first_user_entry(&dir).unwrap(), None);
        fs::write(dir.join(".x.pdf.icloud"), b"").unwrap();
        assert_eq!(
            first_user_entry(&dir).unwrap().as_deref(),
            Some(".x.pdf.icloud")
        );
        fs::remove_dir_all(&dir).unwrap();
    }

    /// A non-UTF-8 name counts as a user entry. Skipped where the file system
    /// refuses such names (macOS/APFS, Windows).
    #[cfg(target_os = "linux")]
    #[test]
    fn non_utf8_name_is_a_user_entry() {
        use std::os::unix::ffi::OsStrExt;
        let dir = scratch("nonutf8");
        let name = std::ffi::OsStr::from_bytes(b"caf\xe9.pdf");
        if fs::write(dir.join(name), b"").is_ok() {
            assert!(first_user_entry(&dir).unwrap().is_some());
            assert_eq!(list_names(&dir).unwrap().len(), 1);
        }
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn create_dir_new_reports_existing() {
        let dir = scratch("mk");
        let sub = dir.join("p");
        assert!(create_dir_new(&sub).unwrap());
        assert!(!create_dir_new(&sub).unwrap());
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn temp_names_of_both_languages_are_recognised() {
        let rust = format!(
            "a.pdf{TEMP_SUFFIX}{}",
            "0".repeat(TEMP_SUFFIX_LEN - TEMP_SUFFIX.len())
        );
        assert!(is_temp_name(&rust));
        assert!(is_temp_name(".a.pdf.0123456789abcdef0123456789abcdef.tmp"));
        for not in [
            "a.pdf",
            &rust[..rust.len() - 1],
            ".a.pdf.0123456789ABCDEF0123456789abcdef.tmp",
            "a.pdf.0123456789abcdef0123456789abcdef.tmp",
            ".0123456789abcdef0123456789abcdef.tmp",
        ] {
            assert!(!is_temp_name(not), "{not}");
        }
    }

    #[test]
    fn sha256_hex_is_lowercase_hex() {
        assert_eq!(
            sha256_hex(b"abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
    }
}
