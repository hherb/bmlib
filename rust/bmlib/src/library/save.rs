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

//! Writing a library: save a source file, drop it from one installation's
//! record, and the manual clear.
//!
//! Nothing is overwritten or deleted unless the caller asks. A source file is
//! written once under a name made from its digest; each installation writes
//! only its own record. Two writers saving the same paper at once therefore
//! end with one file and both records.

use std::path::{Path, PathBuf};
use std::sync::{Mutex, MutexGuard, PoisonError};

use unicode_normalization::UnicodeNormalization;

use super::files::{
    component, create_dir_all, create_dir_new, exists, io_error, is_dir, list_names, read_bytes,
    remove_dir_all, remove_file, rename, sha256_hex, write_once, write_replace,
};
use super::scan::{scan_folder, Problem};
use super::{
    find, find_digest, folder_name, folder_name_numbered, parse_record, render_record, FileEntry,
    FileRef, Installation, Library, LibraryError, PaperInfo, Record, RecordProblem, SourceKind,
    CORRUPT_SUFFIX, DIGEST_PREFIX_HEX, MAX_NAME_SUFFIX, PAPERS_DIR, RECORDS_DIR,
};

/// Serialises the read-merge-write of records (and keeping one aside) within
/// this process, so two threads of one installation never lose each other's
/// update. One lock for all records: a record write is short, and separate
/// processes of one installation are out of scope.
static RECORD_LOCK: Mutex<()> = Mutex::new(());

/// Take [`RECORD_LOCK`]; a panic in another holder does not poison it for good.
fn record_guard() -> MutexGuard<'static, ()> {
    RECORD_LOCK.lock().unwrap_or_else(PoisonError::into_inner)
}

/// The log target (Python's logger name) for this module's warnings.
const LOG_TARGET: &str = "bmlib.library.save";

/// A folder name as the code compares it (R4): NFC, then lowercase, so the
/// outcome is the same on a case-sensitive and a case-insensitive disk.
fn folded(name: &str) -> String {
    name.nfc().collect::<String>().to_lowercase()
}

/// The names in `papers` that equal `candidate` once folded, in order.
fn same_name_as(papers: &Path, candidate: &str) -> Result<Vec<String>, LibraryError> {
    let want = folded(candidate);
    Ok(list_names(papers)
        .map_err(|e| io_error(papers, &e, false))?
        .into_iter()
        .filter(|n| folded(n) == want)
        .collect())
}

/// Whether the folder `name` may take `paper`: some record there shares an
/// identifier with it, or no record there is readable yet (another writer has
/// claimed the folder, or a crash left it empty). A folder whose records are all
/// newer than this code is neither: its identifiers cannot be read, and a second
/// folder beside a newer app's paper would be a silent second store, so that is
/// [`LibraryError::NewerRecord`] (R16).
fn belongs_to(papers: &Path, name: &str, paper: &PaperInfo) -> Result<bool, LibraryError> {
    let mut problems = Vec::new();
    let found = scan_folder(&papers.join(name), name, &mut problems);
    if found.records.is_empty() {
        return match problems.into_iter().find_map(|p| match p {
            Problem::NewerRecord {
                folder,
                file,
                version,
            } => Some((folder, file, version)),
            _ => None,
        }) {
            Some((folder, file, version)) => Err(LibraryError::NewerRecord {
                folder,
                file,
                version: u64::from(version),
            }),
            None => Ok(true),
        };
    }
    Ok(found.ids().shares_any(&paper.ids))
}

/// The folder `paper` goes in when no folder holds it yet: a new one, or one a
/// concurrent writer of the same paper just made.
fn claim_folder(papers: &Path, paper: &PaperInfo) -> Result<String, LibraryError> {
    let base = folder_name(paper);
    for n in 1..=MAX_NAME_SUFFIX {
        let candidate = folder_name_numbered(paper, n);
        // The second round re-reads the names after losing a create race.
        for _ in 0..2 {
            let same = same_name_as(papers, &candidate)?;
            if !same.is_empty() {
                let mut newer = None;
                for s in &same {
                    match belongs_to(papers, s, paper) {
                        Ok(true) => return Ok(s.clone()),
                        Ok(false) => {}
                        Err(e) => newer = newer.or(Some(e)),
                    }
                }
                if let Some(e) = newer {
                    return Err(e);
                }
                break;
            }
            let dir = papers.join(&candidate);
            if create_dir_new(&dir).map_err(|e| io_error(&dir, &e, true))? {
                return Ok(candidate);
            }
        }
    }
    Err(LibraryError::Io {
        path: papers.join(&base),
        reason: format!("no free folder name up to suffix {MAX_NAME_SUFFIX}"),
    })
}

/// Move an unreadable record aside, to a name nothing is at yet.
fn keep_aside(path: &Path) -> Result<PathBuf, LibraryError> {
    let mut n = 1_u32;
    loop {
        let suffix = if n == 1 {
            CORRUPT_SUFFIX.to_owned()
        } else {
            format!("{CORRUPT_SUFFIX}-{n}")
        };
        let aside = PathBuf::from(format!("{}{suffix}", path.display()));
        if !exists(&aside) {
            rename(path, &aside).map_err(|e| io_error(path, &e, true))?;
            return Ok(aside);
        }
        n += 1;
    }
}

/// This installation's record at `path`: `None` when there is none, or when it
/// was unreadable and has been kept aside. A newer record is an error.
fn load_own_record(path: &Path, folder: &str) -> Result<Option<Record>, LibraryError> {
    let bytes = match read_bytes(path) {
        Ok(b) => b,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(io_error(path, &e, false)),
    };
    let parsed = String::from_utf8(bytes)
        .map_err(|e| RecordProblem::Unreadable(e.to_string()))
        .and_then(|text| parse_record(&text));
    match parsed {
        Ok(record) => Ok(Some(record)),
        Err(RecordProblem::Newer(version)) => Err(LibraryError::NewerRecord {
            folder: folder.to_owned(),
            file: file_name(path),
            version: u64::from(version),
        }),
        Err(RecordProblem::Unreadable(reason)) => {
            let aside = keep_aside(path)?;
            log::warn!(
                target: LOG_TARGET,
                "the record {} was unreadable ({reason}); kept as {} and replaced",
                path.display(),
                aside.display()
            );
            Ok(None)
        }
    }
}

fn file_name(path: &Path) -> String {
    path.file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_default()
}

/// Write `record` over `path` unless that is what is there already.
fn store_record(path: &Path, record: &Record) -> Result<(), LibraryError> {
    let text = render_record(record);
    if read_bytes(path).is_ok_and(|old| old == text.as_bytes()) {
        return Ok(());
    }
    write_replace(path, text.as_bytes())
}

/// `paper` folded into the record's own account of it: identifiers merged
/// (a conflict is logged, the record's own value kept), gaps in the rest filled.
fn merge_paper(mut record: Record, paper: &PaperInfo, path: &Path) -> Record {
    let (ids, conflicts) = record.paper.ids.merged(&paper.ids);
    for field in conflicts {
        log::warn!(
            target: LOG_TARGET,
            "{}: the {field} given differs from the one recorded; the recorded one is kept",
            path.display()
        );
    }
    record.paper.ids = ids;
    record.paper.title = record.paper.title.or_else(|| paper.title.clone());
    record.paper.year = record.paper.year.or(paper.year);
    record.paper.first_author = record
        .paper
        .first_author
        .or_else(|| paper.first_author.clone());
    record
}

/// A [`FileRef`]'s folder and name as safe path components, and the paths.
fn locate(root: &Path, file: &FileRef) -> Result<(PathBuf, PathBuf), LibraryError> {
    let dir = root.join(PAPERS_DIR).join(component(&file.folder)?);
    let path = dir.join(component(&file.name)?);
    Ok((dir, path))
}

impl Library {
    /// Save `bytes` of kind `kind` for `paper` and note it in `who`'s record.
    ///
    /// The paper's folder is the first folder holding a record that shares an
    /// identifier with it; with no identifiers, the folder already holding these
    /// bytes; otherwise a new folder, named by [`folder_name`] (with ` (2)`,
    /// ` (3)` … when another paper has the name, compared without regard to case).
    /// The file is written first, then the record, so a crash between leaves
    /// only an unrecorded file, which saving the same bytes again records.
    ///
    /// Within one process the update of a record is serialised, so threads of
    /// one installation never lose each other's entries; separate processes of
    /// one installation are out of scope.
    ///
    /// # Errors
    ///
    /// [`LibraryError::FileClash`] when a file of that name holds other bytes
    /// (nothing is overwritten); [`LibraryError::NewerRecord`] when `who`'s record, or every record of the
    /// folder the paper would go in, is from a newer layout (no second folder is made); [`LibraryError::ReadOnly`]; [`LibraryError::Io`].
    pub fn save(
        &self,
        who: &Installation,
        paper: &PaperInfo,
        bytes: &[u8],
        kind: SourceKind,
        origin: &str,
    ) -> Result<FileRef, LibraryError> {
        let digest = sha256_hex(bytes);
        let name = format!(
            "{}-{}.{}",
            kind.as_str(),
            &digest[..DIGEST_PREFIX_HEX],
            kind.extension()
        );
        let scan = self.scan()?;
        let papers = self.root().join(PAPERS_DIR);
        let existing = match find(&scan, &paper.ids).first() {
            Some(f) => Some(f.folder.clone()),
            None if paper.ids.is_empty() => find_digest(&scan, &digest).map(|r| r.folder),
            None => None,
        };
        let folder = match existing {
            Some(f) => f,
            None => claim_folder(&papers, paper)?,
        };
        let dir = papers.join(component(&folder)?);
        write_once(&dir.join(&name), bytes, &digest)?;

        let records_dir = dir.join(RECORDS_DIR);
        create_dir_all(&records_dir).map_err(|e| io_error(&records_dir, &e, true))?;
        let path = records_dir.join(who.record_file_name());
        let entry = FileEntry {
            sha256: digest.clone(),
            kind,
            name: name.clone(),
            origin: origin.to_owned(),
            size: bytes.len() as u64,
            saved_at: chrono::Utc::now().format("%Y-%m-%dT%H:%M:%SZ").to_string(),
        };
        let _guard = record_guard();
        let record = match load_own_record(&path, &folder)? {
            Some(mut record) => {
                if !record.files.iter().any(|f| f.sha256 == digest) {
                    record.files.push(entry);
                }
                merge_paper(record, paper, &path)
            }
            None => Record {
                installation: who.clone(),
                paper: paper.clone(),
                files: vec![entry],
            },
        };
        store_record(&path, &record)?;
        Ok(FileRef {
            folder,
            name,
            sha256: digest,
            kind,
        })
    }

    /// Drop `file` from `who`'s record. The file stays, and so do the other
    /// installations' records. A file the record does not list (or no record) is
    /// already not used, so that is `Ok`. Serialised with [`Library::save`] in
    /// this process.
    ///
    /// # Errors
    ///
    /// [`LibraryError::BadName`]; [`LibraryError::NewerRecord`]; [`LibraryError::Io`]
    /// for a record that cannot be read (it is not touched).
    pub fn release(&self, who: &Installation, file: &FileRef) -> Result<(), LibraryError> {
        let (dir, _) = locate(self.root(), file)?;
        let path = dir.join(RECORDS_DIR).join(who.record_file_name());
        let _guard = record_guard();
        let bytes = match read_bytes(&path) {
            Ok(b) => b,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(()),
            Err(e) => return Err(io_error(&path, &e, false)),
        };
        let unreadable = |reason: String| LibraryError::Io {
            path: path.clone(),
            reason: format!("the record is not readable: {reason}"),
        };
        let text = String::from_utf8(bytes).map_err(|e| unreadable(e.to_string()))?;
        let mut record = match parse_record(&text) {
            Ok(r) => r,
            Err(RecordProblem::Unreadable(reason)) => return Err(unreadable(reason)),
            Err(RecordProblem::Newer(version)) => {
                return Err(LibraryError::NewerRecord {
                    folder: file.folder.clone(),
                    file: file_name(&path),
                    version: u64::from(version),
                })
            }
        };
        record.files.retain(|f| f.sha256 != file.sha256);
        store_record(&path, &record)
    }

    /// Delete one source file (the manual clear). Other installations' records
    /// are left as they are and will read [`LibraryError::Missing`].
    ///
    /// # Errors
    ///
    /// [`LibraryError::BadName`] for a folder or file name that is not one path
    /// component; [`LibraryError::Missing`] when the file is not there;
    /// [`LibraryError::ReadOnly`]; [`LibraryError::Io`].
    pub fn remove_file(&self, file: &FileRef) -> Result<(), LibraryError> {
        let (_, path) = locate(self.root(), file)?;
        match remove_file(&path) {
            Ok(()) => Ok(()),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Err(LibraryError::Missing {
                folder: file.folder.clone(),
                name: file.name.clone(),
            }),
            Err(e) => Err(io_error(&path, &e, true)),
        }
    }

    /// Delete a paper's whole folder, records included (the manual clear).
    ///
    /// # Errors
    ///
    /// [`LibraryError::BadName`] when `folder` is not one path component;
    /// [`LibraryError::Missing`] when there is no such folder (or it is a file);
    /// [`LibraryError::ReadOnly`]; [`LibraryError::Io`].
    pub fn remove_folder(&self, folder: &str) -> Result<(), LibraryError> {
        let dir = self.root().join(PAPERS_DIR).join(component(folder)?);
        if !is_dir(&dir) {
            return Err(LibraryError::Missing {
                folder: PAPERS_DIR.to_owned(),
                name: folder.to_owned(),
            });
        }
        remove_dir_all(&dir).map_err(|e| io_error(&dir, &e, true))
    }
}
