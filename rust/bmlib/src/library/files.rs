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
//! [`crate::atomic::atomic_write`] and refuses a different file of the same
//! name.

use std::fs;
use std::io::{self, Read};
use std::path::{Path, PathBuf};

use sha2::{Digest, Sha256};

use super::names::is_reserved;
use super::{LibraryError, ICLOUD_PLACEHOLDER, IGNORED_NAMES, PAPERS_DIR};
use crate::atomic::{TEMP_SUFFIX, TEMP_SUFFIX_LEN};

/// The reason [`read_bytes`] gives for a symbolic link, FIFO, device or socket
/// (#761).
pub(crate) const NOT_A_REGULAR_FILE: &str = "not a regular file";

/// The reason [`check_folder`] and [`create_dir_new`] give for a link or a file
/// where the library needs a folder (#761).
pub(crate) const NOT_A_FOLDER: &str = "not a folder";

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
/// read-only write is [`LibraryError::ReadOnly`], anything else
/// [`LibraryError::Io`].
pub(crate) fn io_error(path: &Path, err: &io::Error, write: bool) -> LibraryError {
    if is_not_downloaded(err) {
        return LibraryError::NotDownloaded {
            folder: parent(path).display().to_string(),
            name: file_name(path),
        };
    }
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
/// U+FFFD); it is only ever shown or compared for prefixes, never used as a
/// path.
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
    /// Temporaries of `atomic_write` ([`is_temp_name`]), in flight or left by a
    /// crash.
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

/// The file an iCloud placeholder name (`.<name>.icloud`) stands for, if it is
/// one.
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

/// `lstat(path)`, or `None` when nothing is there. Any other failure (a folder
/// that cannot be searched, an I/O error) is an error, never "absent": a scan
/// would miss the paper and a save make it a second folder.
fn lstat(path: &Path) -> io::Result<Option<fs::Metadata>> {
    match fs::symlink_metadata(path) {
        Ok(m) => Ok(Some(m)),
        Err(e)
            if matches!(
                e.kind(),
                io::ErrorKind::NotFound | io::ErrorKind::NotADirectory
            ) =>
        {
            Ok(None)
        }
        Err(e) => Err(e),
    }
}

/// Whether `path` is a directory itself, not a symbolic link (or, on Windows,
/// a junction) to one. A linked folder inside the library would let a save
/// write outside it (#761). An error when `path` cannot be examined ([`lstat`]).
pub(crate) fn is_dir(path: &Path) -> io::Result<bool> {
    Ok(lstat(path)?.is_some_and(|m| m.is_dir()))
}

/// Whether `path` is a directory or a link to one: only for the library's
/// root, which a user may well reach through a link.
pub(crate) fn is_dir_following_links(path: &Path) -> bool {
    path.is_dir()
}

/// Whether anything exists at `path`, a dangling symbolic link included. An
/// error when `path` cannot be examined ([`lstat`]).
pub(crate) fn exists(path: &Path) -> io::Result<bool> {
    Ok(lstat(path)?.is_some())
}

/// Refuse `path` when something that is not a directory itself is there
/// (#761); nothing at `path` passes, for the caller to find missing.
///
/// [`LibraryError::Io`] for a symbolic link or a file ([`NOT_A_FOLDER`]), or
/// when `path` cannot be examined.
pub(crate) fn check_folder(path: &Path) -> Result<(), LibraryError> {
    match lstat(path) {
        Ok(Some(m)) if !m.is_dir() => Err(LibraryError::Io {
            path: path.to_owned(),
            reason: NOT_A_FOLDER.to_owned(),
        }),
        Ok(_) => Ok(()),
        Err(e) => Err(io_error(path, &e, false)),
    }
}

/// The paper folder `folder` under `root`, its name checked by [`component`].
/// `papers/` is checked as well as the folder: it may have been replaced by a
/// link since the library was opened.
///
/// [`LibraryError::BadName`] when `folder` is not one path component;
/// [`LibraryError::Io`] when `papers/` or the folder is not a directory itself
/// ([`check_folder`]), so nothing is read, written or deleted through it
/// (#761). A name with nothing at it is returned, for the caller to find
/// missing.
pub(crate) fn paper_dir(root: &Path, folder: &str) -> Result<PathBuf, LibraryError> {
    let papers = root.join(PAPERS_DIR);
    let dir = papers.join(component(folder)?);
    check_folder(&papers)?;
    check_folder(&dir)?;
    Ok(dir)
}

/// The last component of `path`, shown lossily if it is not UTF-8 (empty for
/// none).
pub(crate) fn file_name(path: &Path) -> String {
    path.file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_default()
}

/// Whether `dir` holds iCloud's online-only placeholder for `name`.
pub(crate) fn has_placeholder(dir: &Path, name: &str) -> bool {
    dir.join(ICLOUD_PLACEHOLDER.replace("{name}", name))
        .exists()
}

/// Read a whole file, never downloading it.
///
/// On macOS 14+ and iOS a sync service (iCloud Drive, Dropbox, OneDrive) can
/// evict a file to a *dataless* one under its real name, and a plain read then
/// blocks until it is downloaded, perhaps forever offline. Here the read fails
/// at once instead, with an error [`is_not_downloaded`] recognises (#769).
///
/// Only a regular file is read: a symbolic link (to a file outside the
/// library, or to `/dev/zero`), a FIFO or a device is an error whose message
/// is [`NOT_A_REGULAR_FILE`], so a scan or read can neither hang nor leave the
/// library (#761), short of the race in the `shortcut:` below.
pub(crate) fn read_bytes(path: &Path) -> io::Result<Vec<u8>> {
    #[cfg(any(target_os = "macos", target_os = "ios"))]
    let _off = dataless::MaterialisationOff::begin(path);
    let not_regular = || io::Error::other(NOT_A_REGULAR_FILE);
    let seen = fs::symlink_metadata(path)?;
    if !seen.is_file() {
        return Err(not_regular());
    }
    // shortcut: a FIFO (or a link to one) swapped in between the lstat and the
    // open still blocks the open, and on Windows a link swapped in is followed;
    // elsewhere the open file must be the one lstat saw. Open with
    // O_NONBLOCK | O_NOFOLLOW if a writer ever races this.
    let mut file = fs::File::open(path)?;
    let opened = file.metadata()?;
    if !opened.is_file() || !same_file(&seen, &opened) {
        return Err(not_regular());
    }
    let mut bytes = Vec::new();
    file.read_to_end(&mut bytes)?;
    Ok(bytes)
}

/// Whether `err`, from [`read_bytes`], means the file is dataless: present but
/// not downloaded to this device. Always `false` off macOS and iOS.
pub(crate) fn is_not_downloaded(err: &io::Error) -> bool {
    #[cfg(any(target_os = "macos", target_os = "ios"))]
    return err.raw_os_error() == Some(dataless::EDEADLK);
    #[cfg(not(any(target_os = "macos", target_os = "ios")))]
    {
        let _ = err;
        false
    }
}

/// The thread's I/O policy for dataless files (`setiopolicy_np(3)`).
#[cfg(any(target_os = "macos", target_os = "ios"))]
mod dataless {
    use std::ffi::c_int;
    use std::io;
    use std::path::Path;

    /// `IOPOL_TYPE_VFS_MATERIALIZE_DATALESS_FILES` in `<sys/resource.h>`.
    const POLICY_TYPE: c_int = 3;
    /// `IOPOL_SCOPE_THREAD`: the calling thread only, not the host's others.
    const SCOPE_THREAD: c_int = 1;
    /// `IOPOL_MATERIALIZE_DATALESS_FILES_OFF`: an I/O on a dataless file fails.
    pub(super) const OFF: c_int = 1;
    /// The error such an I/O fails with (`<sys/errno.h>`).
    pub(super) const EDEADLK: i32 = 11;

    extern "C" {
        fn getiopolicy_np(iotype: c_int, scope: c_int) -> c_int;
        fn setiopolicy_np(iotype: c_int, scope: c_int, policy: c_int) -> c_int;
    }

    /// Materialisation turned off on this thread until dropped, when the
    /// thread's previous policy comes back.
    pub(super) struct MaterialisationOff {
        /// The policy to restore; `None` when it could not be read or set.
        previous: Option<c_int>,
    }

    impl MaterialisationOff {
        /// Turn materialisation off for a read of `path`. A failure is logged
        /// and the read goes ahead with the default policy, which downloads an
        /// evicted file (the behaviour before #769) rather than refusing every
        /// read.
        pub(super) fn begin(path: &Path) -> Self {
            // SAFETY: both calls take plain integers and touch only this
            // thread's policy; -1 is their error return.
            let previous = unsafe { getiopolicy_np(POLICY_TYPE, SCOPE_THREAD) };
            if previous == -1 || unsafe { setiopolicy_np(POLICY_TYPE, SCOPE_THREAD, OFF) } == -1 {
                log::warn!(
                    target: "bmlib.library.files",
                    "could not stop {} downloading if it is evicted: {}",
                    path.display(),
                    io::Error::last_os_error()
                );
                return Self { previous: None };
            }
            Self {
                previous: Some(previous),
            }
        }
    }

    impl Drop for MaterialisationOff {
        fn drop(&mut self) {
            if let Some(previous) = self.previous {
                // SAFETY: as in `begin`.
                if unsafe { setiopolicy_np(POLICY_TYPE, SCOPE_THREAD, previous) } == -1 {
                    log::warn!(
                        target: "bmlib.library.files",
                        "could not restore this thread's policy for evicted files: {}",
                        io::Error::last_os_error()
                    );
                }
            }
        }
    }

    /// This thread's current policy, for the tests.
    #[cfg(test)]
    pub(super) fn current() -> c_int {
        // SAFETY: as in `begin`.
        unsafe { getiopolicy_np(POLICY_TYPE, SCOPE_THREAD) }
    }
}

/// Publish `bytes` at `path` unless a file is there. `sha256` is the digest of
/// `bytes`; an existing file with that digest is [`Written::AlreadyThere`], one
/// with another is [`LibraryError::FileClash`] and is left untouched.
pub(crate) fn write_once(path: &Path, bytes: &[u8], sha256: &str) -> Result<Written, LibraryError> {
    match read_bytes(path) {
        Ok(existing) => {
            return if sha256_hex(&existing) == sha256 {
                // Another writer's, perhaps not yet synced.
                sync_name(path)?;
                Ok(Written::AlreadyThere)
            } else {
                Err(LibraryError::FileClash {
                    folder: path
                        .parent()
                        .map(|p| p.display().to_string())
                        .unwrap_or_default(),
                    name: file_name(path),
                })
            };
        }
        Err(e) if e.kind() == io::ErrorKind::NotFound => {}
        Err(e) => return Err(io_error(path, &e, false)),
    }
    publish(path, bytes)?;
    Ok(Written::New)
}

/// The directory holding `path` (`.` for a bare name).
fn parent(path: &Path) -> &Path {
    match path.parent() {
        Some(p) if !p.as_os_str().is_empty() => p,
        _ => Path::new("."),
    }
}

/// Flush `dir`'s entries to disk, so that a name published or a directory made
/// in it survives a crash. `atomic_write` syncs a file's data, not its name,
/// and a caller may drop its own copy once a save returns (#768).
///
/// `sync_all` is `F_FULLFSYNC` on macOS, which also flushes the drive's cache;
/// Python's twin uses the same call there.
pub(crate) fn sync_dir(dir: &Path) -> io::Result<()> {
    #[cfg(test)]
    tests::note_sync(dir)?;
    // shortcut: a no-op on Windows, where a directory cannot be opened to sync
    // it; the rename is as durable as NTFS makes it. Use MoveFileEx's
    // write-through when the Windows build gets the Library setting.
    #[cfg(unix)]
    fs::File::open(dir)?.sync_all()?;
    #[cfg(not(unix))]
    let _ = dir;
    Ok(())
}

/// Sync `path`'s directory ([`sync_dir`]), a failure reported against `path`.
/// A file system that refuses the sync (`ENOTSUP`, `EINVAL`) fails the call:
/// the save is not durable there, and saying so beats a save that may vanish
/// (DECISIONS, #768).
pub(crate) fn sync_name(path: &Path) -> Result<(), LibraryError> {
    sync_dir(parent(path)).map_err(|e| LibraryError::Io {
        path: path.to_owned(),
        reason: format!("syncing its folder: {e}"),
    })
}

/// `atomic_write` `bytes` at `path`, then sync its directory so the name is
/// durable.
/// Whether two metadata describe one file (device and inode); always `true` off
/// Unix, where std gives no stable file identity.
fn same_file(a: &fs::Metadata, b: &fs::Metadata) -> bool {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        (a.dev(), a.ino()) == (b.dev(), b.ino())
    }
    #[cfg(not(unix))]
    {
        let _ = (a, b);
        true
    }
}

fn publish(path: &Path, bytes: &[u8]) -> Result<(), LibraryError> {
    crate::atomic::atomic_write(path, bytes).map_err(|e| io_error(path, &e, true))?;
    sync_name(path)
}

/// Make the directory `path` (its parent must exist); `false` when it already
/// exists. Either way the directory is synced into its parent ([`sync_dir`]):
/// one found there may be another writer's, not yet synced. Anything else at
/// `path`, a symbolic link to a directory included, is an error whose message
/// is [`NOT_A_FOLDER`]; a failure to examine `path` is that failure.
pub(crate) fn create_dir_new(path: &Path) -> io::Result<bool> {
    let made = match fs::create_dir(path) {
        Ok(()) => true,
        Err(e) if e.kind() == io::ErrorKind::AlreadyExists => {
            if !is_dir(path)? {
                return Err(io::Error::other(NOT_A_FOLDER));
            }
            false
        }
        Err(e) => return Err(e),
    };
    sync_dir(parent(path))?;
    Ok(made)
}

/// Rename `from` to `to`.
pub(crate) fn rename(from: &Path, to: &Path) -> io::Result<()> {
    fs::rename(from, to)
}

/// Publish `bytes` at `path` atomically, replacing what is there. Only for a
/// file its writer alone owns (an installation's own record). The directory is
/// synced, so the record survives a crash once this returns.
pub(crate) fn write_replace(path: &Path, bytes: &[u8]) -> Result<(), LibraryError> {
    publish(path, bytes)
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
    use std::cell::{Cell, RefCell};
    use std::collections::BTreeSet;
    use std::path::PathBuf;

    thread_local! {
        /// Each directory [`sync_dir`] was asked to sync on this thread, with
        /// the names it held at that moment.
        static SYNCED: RefCell<Vec<(PathBuf, BTreeSet<String>)>> = const { RefCell::new(Vec::new()) };
        /// The index in [`SYNCED`] of the sync to fail, if any.
        static FAIL_AT: Cell<Option<usize>> = const { Cell::new(None) };
    }

    pub(super) fn note_sync(dir: &Path) -> io::Result<()> {
        let names = fs::read_dir(dir)
            .map(|r| {
                r.filter_map(Result::ok)
                    .map(|e| e.file_name().to_string_lossy().into_owned())
                    .collect()
            })
            .unwrap_or_default();
        let at = SYNCED.with(|s| {
            let mut s = s.borrow_mut();
            s.push((dir.to_owned(), names));
            s.len() - 1
        });
        if FAIL_AT.with(Cell::get) == Some(at) {
            return Err(io::Error::other("injected"));
        }
        Ok(())
    }

    /// Run `f` with the sync numbered `at` in the log (counted from the last
    /// [`take_log`]) failing.
    fn failing_sync<T>(at: usize, f: impl FnOnce() -> T) -> T {
        FAIL_AT.with(|c| c.set(Some(at)));
        let out = f();
        FAIL_AT.with(|c| c.set(None));
        out
    }

    /// Whether `dir` was synced while it held `name`, since the log was taken.
    fn synced_holding(log: &[(PathBuf, BTreeSet<String>)], dir: &Path, name: &str) -> bool {
        log.iter()
            .any(|(d, names)| d == dir && names.contains(name))
    }

    fn take_log() -> Vec<(PathBuf, BTreeSet<String>)> {
        SYNCED.with(|s| std::mem::take(&mut *s.borrow_mut()))
    }

    /// #768: a caller drops its own copy once save returns, so each new entry
    /// (the folder in `papers/`, the file and `records/` in the folder, the
    /// record in `records/`) must have had its directory synced after it
    /// appeared. The second save finds the bytes there and must still sync
    /// them, since they may be another writer's not yet synced.
    #[test]
    fn every_name_a_save_makes_is_synced_into_its_directory() {
        use crate::library::{Installation, Library, PaperIds, PaperInfo, SourceKind};
        let root = scratch("sync");
        take_log();
        let lib = Library::open(&root).unwrap();
        let log = take_log();
        assert!(synced_holding(&log, &root, "bmlib-library.json"));
        assert!(synced_holding(&log, &root, "papers"));
        let paper = PaperInfo {
            ids: PaperIds::canonical(Some("111"), None, None).unwrap(),
            title: Some("Metformin".into()),
            year: Some(2024),
            first_author: Some("Smith".into()),
        };
        let me = Installation::new("studyanalyzer", "aaaa", "Mac").unwrap();
        let you = Installation::new("medicalfactchecker", "bbbb", "Mac").unwrap();
        let saved = lib
            .save(&me, &paper, b"%PDF a paper", SourceKind::Pdf, "test")
            .unwrap();
        let log = take_log();
        let folder = root.join("papers").join(&saved.folder);
        assert!(synced_holding(&log, &root.join("papers"), &saved.folder));
        assert!(synced_holding(&log, &folder, &saved.name));
        assert!(synced_holding(&log, &folder, "records"));
        assert!(synced_holding(
            &log,
            &folder.join("records"),
            &me.record_file_name()
        ));
        lib.save(&you, &paper, b"%PDF a paper", SourceKind::Pdf, "test")
            .unwrap();
        assert!(synced_holding(&take_log(), &folder, &saved.name));
        fs::remove_dir_all(&root).unwrap();
    }

    /// #768: the caller keeps its copy unless save returns, so every sync a
    /// save makes must fail it when it fails. A retry then finds the folder,
    /// `records/` and the record already there, and must still sync each into
    /// its directory before it returns.
    #[test]
    fn a_failed_sync_fails_the_save_and_a_retry_syncs_what_it_left() {
        use crate::library::{Installation, Library, PaperIds, PaperInfo, SourceKind};
        let base = scratch("retry");
        let paper = PaperInfo {
            ids: PaperIds::canonical(Some("111"), None, None).unwrap(),
            title: Some("Metformin".into()),
            year: Some(2024),
            first_author: Some("Smith".into()),
        };
        let me = Installation::new("studyanalyzer", "aaaa", "Mac").unwrap();
        let save = |lib: &Library| lib.save(&me, &paper, b"%PDF a paper", SourceKind::Pdf, "test");
        let open = |name: String| {
            let root = base.join(name);
            fs::create_dir(&root).unwrap();
            let lib = Library::open(&root).unwrap();
            take_log();
            (root, lib)
        };
        let (_, lib) = open("count".into());
        save(&lib).unwrap();
        let calls = take_log().len();
        assert!(calls >= 4);
        for at in 0..calls {
            let (root, lib) = open(at.to_string());
            let err = failing_sync(at, || save(&lib)).unwrap_err();
            assert!(matches!(err, LibraryError::Io { .. }), "{at}: {err:?}");
            take_log();
            let saved = save(&lib).unwrap();
            let log = take_log();
            let folder = root.join("papers").join(&saved.folder);
            for (dir, name) in [
                (root.join("papers"), saved.folder.clone()),
                (folder.clone(), saved.name.clone()),
                (folder.clone(), "records".to_owned()),
                (folder.join("records"), me.record_file_name()),
            ] {
                assert!(synced_holding(&log, &dir, &name), "{at}: {name}");
            }
        }
        fs::remove_dir_all(&base).unwrap();
    }

    /// The file is published before its directory is synced; the error must
    /// say which step failed, or a caller cannot tell a lost write from an
    /// unsynced one.
    #[test]
    fn a_failed_sync_names_the_file_and_the_sync() {
        let dir = scratch("syncname");
        let file = dir.join("a.pdf");
        take_log();
        match failing_sync(0, || sync_name(&file)) {
            Err(LibraryError::Io { path, reason }) => {
                assert_eq!(path, file);
                assert!(reason.starts_with("syncing its folder: "), "{reason}");
            }
            other => panic!("{other:?}"),
        }
        fs::remove_dir_all(&dir).unwrap();
    }

    /// A fresh empty directory under the system temp dir, removed by the
    /// caller.
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

    /// #768: one found there may be another writer's, not yet synced into its
    /// parent.
    #[test]
    fn create_dir_new_syncs_a_directory_found_there() {
        let dir = scratch("mkfound");
        fs::create_dir(dir.join("p")).unwrap();
        take_log();
        assert!(!create_dir_new(&dir.join("p")).unwrap());
        assert!(synced_holding(&take_log(), &dir, "p"));
        fs::remove_dir_all(&dir).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn a_path_that_cannot_be_examined_is_an_error_not_absent() {
        use std::os::unix::fs::PermissionsExt;
        let dir = scratch("unexamined");
        fs::create_dir_all(dir.join("d/sub")).unwrap();
        fs::set_permissions(dir.join("d"), fs::Permissions::from_mode(0o600)).unwrap();
        let sub = dir.join("d/sub");
        let root_like = fs::symlink_metadata(&sub).is_ok();
        let got = (is_dir(&sub), exists(&sub), create_dir_new(&sub));
        fs::set_permissions(dir.join("d"), fs::Permissions::from_mode(0o755)).unwrap();
        if !root_like {
            let denied = io::ErrorKind::PermissionDenied;
            assert_eq!(got.0.unwrap_err().kind(), denied);
            assert_eq!(got.1.unwrap_err().kind(), denied);
            assert_eq!(got.2.unwrap_err().kind(), denied);
        }
        assert!(!is_dir(&dir.join("nothing")).unwrap());
        assert!(!exists(&dir.join("nothing/below")).unwrap());
        fs::remove_dir_all(&dir).unwrap();
    }

    #[cfg(windows)]
    #[test]
    fn a_junction_is_not_a_folder() {
        let dir = scratch("junction");
        fs::create_dir_all(dir.join("target")).unwrap();
        let made = std::process::Command::new("cmd")
            .args(["/C", "mklink", "/J"])
            .arg(dir.join("junction"))
            .arg(dir.join("target"))
            .status()
            .unwrap();
        assert!(made.success(), "mklink /J");
        assert!(is_dir(&dir.join("target")).unwrap());
        assert!(!is_dir(&dir.join("junction")).unwrap());
        assert!(exists(&dir.join("junction")).unwrap());
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn create_dir_new_refuses_a_file_in_the_way() {
        let dir = scratch("mkfile");
        fs::write(dir.join("p"), b"").unwrap();
        let err = create_dir_new(&dir.join("p")).unwrap_err();
        assert_eq!(err.to_string(), NOT_A_FOLDER);
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

    /// #769: a read turns materialisation off on its own thread only while
    /// it runs, and gives the thread's policy back after.
    #[cfg(any(target_os = "macos", target_os = "ios"))]
    #[test]
    fn a_read_turns_materialisation_off_and_back() {
        let dir = std::env::temp_dir().join(format!("bmlib-files-iopol-{}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        let file = dir.join("x.pdf");
        fs::write(&file, b"one").unwrap();
        let before = dataless::current();
        {
            let _off = dataless::MaterialisationOff::begin(&file);
            assert_eq!(dataless::current(), dataless::OFF);
        }
        assert_eq!(dataless::current(), before);
        assert_eq!(read_bytes(&file).unwrap(), b"one");
        assert_eq!(dataless::current(), before);
        fs::remove_dir_all(&dir).unwrap();
    }

    /// #769: a dataless read's `EDEADLK` is "not downloaded", and `io_error`
    /// says so rather than "Resource deadlock avoided".
    #[test]
    fn edeadlk_reads_as_not_downloaded_on_apple_only() {
        let apple = cfg!(any(target_os = "macos", target_os = "ios"));
        let err = io::Error::from_raw_os_error(11);
        assert_eq!(is_not_downloaded(&err), apple);
        assert!(!is_not_downloaded(&io::Error::from(
            io::ErrorKind::NotFound
        )));
        let mapped = io_error(Path::new("lib/.bmlib-library.json"), &err, false);
        if apple {
            assert!(matches!(
                mapped,
                LibraryError::NotDownloaded { ref folder, ref name }
                    if folder == "lib" && name == ".bmlib-library.json"
            ));
        } else {
            assert!(matches!(mapped, LibraryError::Io { .. }));
        }
    }
}
