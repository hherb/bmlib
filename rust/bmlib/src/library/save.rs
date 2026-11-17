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

//! Writing a library: save a source file, note one already there, drop it from
//! one installation's record, and the manual clear.
//!
//! Nothing is overwritten or deleted unless the caller asks. A source file is
//! written once under a name made from its digest; each installation writes
//! only its own record. Two writers saving the same paper at once therefore
//! end with one file and both records.

use std::path::{Path, PathBuf};
use std::sync::{Mutex, MutexGuard, PoisonError};

use unicode_normalization::UnicodeNormalization;

use super::files::{
    check_folder, component, create_dir_new, exists, file_name, has_placeholder, io_error, is_dir,
    is_not_downloaded, list_names, paper_dir, read_bytes, remove_dir_all, remove_file, rename,
    sha256_hex, sync_name, write_once, write_replace,
};
use super::names::may_name;
use super::scan::{scan_folder, PaperFolder, Problem, Scan};
use super::{
    find, find_digest, folder_name, folder_name_numbered, parse_record, render_record, FileEntry,
    FileRef, Installation, Library, LibraryError, PaperIds, PaperInfo, Record, RecordProblem,
    SourceKind, CORRUPT_SUFFIX, DIGEST_PREFIX_HEX, MAX_NAME_SUFFIX, PAPERS_DIR, RECORDS_DIR,
};
use crate::fulltext::jats_reader;
use crate::fulltext::library_store::library_ids;

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

/// A folder name as the code compares it: NFC, then lowercase, so the
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
/// claimed the folder, or a crash left it empty). A folder whose records are
/// all newer than this code is neither: its identifiers cannot be read, and a
/// second folder beside a newer app's paper would be a silent second store, so
/// that is [`LibraryError::NewerRecord`].
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
                version,
            }),
            None => Ok(true),
        };
    }
    Ok(found.shares_any(&paper.ids))
}

/// Refuse to make a new folder while one with a record the scan could not use
/// may hold the paper.
///
/// A record that cannot be listed, read or downloaded, is newer than this
/// code, or is misnamed is invisible to `find`. A save under a revised title or
/// another identifier would then fork the paper into a second folder. The
/// folder's name is the only evidence left, so a name ending in one of the
/// paper's identifiers refuses ([`may_name`]; #778, #755):
/// [`LibraryError::NewerRecord`] for a newer record, [`LibraryError::Io`]
/// naming the folder otherwise. Only `claim_folder`'s create step asks, so a
/// folder it joins (the paper's own name, numbered or not) is never refused.
/// Nothing is written. Python's `save._refuse_hidden_folder` is the twin.
fn refuse_hidden_folder(scan: &Scan, papers: &Path, paper: &PaperInfo) -> Result<(), LibraryError> {
    for p in &scan.problems {
        let (folder, why) = match p {
            Problem::UnreadableFolder { folder, reason } => {
                (folder, format!("its records cannot be read ({reason})"))
            }
            Problem::UnreadableRecord {
                folder,
                file,
                reason,
            } => (
                folder,
                format!("its record {file} cannot be read ({reason})"),
            ),
            Problem::RecordNotDownloaded { folder, file } => {
                (folder, format!("its record {file} is not downloaded"))
            }
            Problem::MisnamedRecord { folder, file } => (
                folder,
                format!("its record {file} is not named for its installation"),
            ),
            Problem::NewerRecord {
                folder,
                file,
                version,
            } => {
                if may_name(folder, &paper.ids) {
                    return Err(LibraryError::NewerRecord {
                        folder: folder.clone(),
                        file: file.clone(),
                        version: *version,
                    });
                }
                continue;
            }
            _ => continue,
        };
        if may_name(folder, &paper.ids) {
            return Err(LibraryError::Io {
                path: papers.join(folder),
                reason: format!(
                    "{why}, and its name says it may hold this paper; nothing was written"
                ),
            });
        }
    }
    Ok(())
}

/// The folder `paper` goes in when no folder holds it yet: a new one, or one a
/// concurrent writer of the same paper just made. Before making one,
/// `refuse_hidden_folder` asks `scan` whether a folder it could not read may
/// hold the paper.
fn claim_folder(papers: &Path, paper: &PaperInfo, scan: &Scan) -> Result<String, LibraryError> {
    let base = folder_name(paper);
    for n in 1..=MAX_NAME_SUFFIX {
        let candidate = folder_name_numbered(paper, n);
        // The second round re-reads the names after losing a create race.
        for _ in 0..2 {
            let same = same_name_as(papers, &candidate)?;
            if !same.is_empty() {
                let mut newer = None;
                for s in &same {
                    let path = papers.join(s);
                    if !is_dir(&path).map_err(|e| io_error(&path, &e, false))? {
                        continue; // a file or link holds the name (#761): it is taken
                    }
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
            refuse_hidden_folder(scan, papers, paper)?;
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
        let mut aside = path.as_os_str().to_owned();
        aside.push(&suffix);
        let aside = PathBuf::from(aside);
        if !exists(&aside).map_err(|e| io_error(&aside, &e, false))? {
            rename(path, &aside).map_err(|e| io_error(path, &e, true))?;
            return Ok(aside);
        }
        n += 1;
    }
}

/// Refuse to go on when this installation's record at `path` is absent but
/// iCloud holds a placeholder for it: a fresh record written now would replace
/// the real one when it is downloaded, and its entries would be lost.
fn check_not_evicted(path: &Path, folder: &str) -> Result<(), LibraryError> {
    let absent = !exists(path).map_err(|e| io_error(path, &e, false))?;
    match path.parent() {
        Some(dir) if absent && has_placeholder(dir, &file_name(path)) => {
            Err(not_downloaded(path, folder))
        }
        _ => Ok(()),
    }
}

/// [`LibraryError::NotDownloaded`] for the record at `path` in the paper
/// folder `folder`.
fn not_downloaded(path: &Path, folder: &str) -> LibraryError {
    LibraryError::NotDownloaded {
        folder: folder.to_owned(),
        name: file_name(path),
    }
}

/// This installation's record at `path`: `None` when there is none, or when it
/// was unreadable and has been kept aside. A newer record is an error, and so
/// is one present only as an iCloud placeholder
/// ([`LibraryError::NotDownloaded`]).
fn load_own_record(path: &Path, folder: &str) -> Result<Option<Record>, LibraryError> {
    let bytes = match read_bytes(path) {
        Ok(b) => b,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            check_not_evicted(path, folder)?;
            return Ok(None);
        }
        Err(e) if is_not_downloaded(&e) => return Err(not_downloaded(path, folder)),
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
            version,
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

/// Refuse an installation [`Installation::new`] would refuse. Its fields are
/// public, so a caller can build one unchecked, and its record name becomes a
/// path.
fn check_who(who: &Installation) -> Result<(), LibraryError> {
    Installation::new(&who.app, &who.id, &who.label)?;
    component(&who.record_file_name())?;
    Ok(())
}

/// Refuse identifiers that are not canonical: [`parse_record`] refuses them, so
/// a record holding them would not read back and the next save would put it
/// aside.
fn check_ids(ids: &PaperIds) -> Result<(), LibraryError> {
    let canon = PaperIds::canonical(
        ids.pmid.as_deref(),
        ids.doi.as_deref(),
        ids.pmcid.as_deref(),
    )?;
    let fields = [
        ("pmid", &ids.pmid, &canon.pmid),
        ("doi", &ids.doi, &canon.doi),
        ("pmcid", &ids.pmcid, &canon.pmcid),
    ];
    for (field, given, want) in fields {
        if given != want {
            return Err(LibraryError::BadIdentifier {
                field,
                value: given.clone().unwrap_or_default(),
            });
        }
    }
    Ok(())
}

/// `record` as the text to write, after the last check that it reads back:
/// a record [`parse_record`] refuses would be put aside by the next save, and
/// the files it lists would then look unused.
fn record_text(record: &Record, path: &Path) -> Result<String, LibraryError> {
    let text = render_record(record);
    match parse_record(&text) {
        Ok(_) => Ok(text),
        Err(problem) => Err(LibraryError::Io {
            path: path.to_owned(),
            reason: format!("the record would not read back ({problem:?}); nothing was written"),
        }),
    }
}

/// Write `record` over `path` unless that is what is there already, which is
/// synced instead.
fn store_record(path: &Path, record: &Record) -> Result<(), LibraryError> {
    let text = record_text(record, path)?;
    if read_bytes(path).is_ok_and(|old| old == text.as_bytes()) {
        // A failed sync may have left it so; a retry must not return without one.
        return sync_name(path);
    }
    write_replace(path, text.as_bytes())
}

/// `paper` folded into the record's own account of it: identifiers merged (a
/// conflict is logged, the record's own value kept), gaps in the rest filled.
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

/// The name a file of `kind` with this digest has in a paper's folder:
/// `{kind}-{first 12 hex}.{extension}`. `digest` is 64 hex digits.
fn stored_name(kind: SourceKind, digest: &str) -> String {
    format!(
        "{}-{}.{}",
        kind.as_str(),
        &digest[..DIGEST_PREFIX_HEX],
        kind.extension()
    )
}

/// The time a file is noted, as records write it: `YYYY-MM-DDTHH:MM:SSZ` in
/// UTC.
fn now() -> String {
    chrono::Utc::now().format("%Y-%m-%dT%H:%M:%SZ").to_string()
}

/// Note `entry` in `who`'s record in the paper folder `dir` (named `folder`),
/// under the record lock: the entry added unless the record lists that file
/// (its digest and kind) already, and `paper` merged into the record's
/// account. The one path by which both [`Library::save`] and
/// [`Library::record_use`] change a record.
fn note_use(
    dir: &Path,
    folder: &str,
    who: &Installation,
    paper: &PaperInfo,
    entry: FileEntry,
) -> Result<(), LibraryError> {
    // The folder may be another writer's, not yet synced into `papers/`.
    sync_name(dir)?;
    let records_dir = dir.join(RECORDS_DIR);
    create_dir_new(&records_dir).map_err(|e| io_error(&records_dir, &e, true))?;
    let path = records_dir.join(component(&who.record_file_name())?);
    let _guard = record_guard();
    let record = match load_own_record(&path, folder)? {
        Some(mut record) => {
            if !record
                .files
                .iter()
                .any(|f| f.sha256 == entry.sha256 && f.kind == entry.kind)
            {
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
    store_record(&path, &record)
}

/// A [`FileRef`]'s folder and name as safe path components, and the paths;
/// [`LibraryError::Io`] when the folder is a symbolic link or a file (#761).
fn locate(root: &Path, file: &FileRef) -> Result<(PathBuf, PathBuf), LibraryError> {
    let dir = paper_dir(root, &file.folder)?;
    let path = dir.join(component(&file.name)?);
    Ok((dir, path))
}

/// A JATS's own identifiers as the full-text service reads them (its reader,
/// without logging the parse's warnings, then `library_ids`), or `None` when it
/// is not UTF-8 or does not parse: such a file names no paper, and is warned of
/// as `at`.
fn jats_ids(at: &str, bytes: &[u8]) -> Option<PaperIds> {
    let parsed = std::str::from_utf8(bytes)
        .map_err(|error| error.to_string())
        .and_then(|xml| jats_reader::parse_unwarned(xml, "").map_err(|error| error.to_string()));
    match parsed {
        Ok(jats_reader::JatsReport { article, .. }) => {
            Some(library_ids(&article.pmid, Some(&article.doi), Some(&article.pmc_id)).0)
        }
        Err(error) => {
            log::warn!(
                "Not checking the JATS {at} for another paper's identifiers: it does not parse ({error})."
            );
            None
        }
    }
}

/// [`LibraryError::ForeignJats`] when the JATS `bytes`' own identifiers
/// contradict `ids` (#766), naming the first contradicting field.
fn refuse_foreign(
    folder: Option<&str>,
    name: &str,
    bytes: &[u8],
    ids: &PaperIds,
) -> Result<(), LibraryError> {
    let at = folder.map_or_else(|| name.to_owned(), |folder| format!("{folder}/{name}"));
    let Some(own) = jats_ids(&at, bytes) else {
        return Ok(());
    };
    let Some(&field) = own.merged(ids).1.first() else {
        return Ok(());
    };
    let pick = |ids: &PaperIds| match field {
        "pmid" => ids.pmid.clone(),
        "doi" => ids.doi.clone(),
        _ => ids.pmcid.clone(),
    };
    Err(LibraryError::ForeignJats {
        folder: folder.map(str::to_owned),
        name: name.to_owned(),
        field,
        theirs: pick(&own).unwrap_or_default(),
        ours: pick(ids).unwrap_or_default(),
    })
}

/// Refuse a folder holding a JATS whose own identifiers contradict `ids`
/// (#766). Every JATS a record lists is read (digest-checked) once, except the
/// digest `skip`, already checked by the caller. One that is missing (as after
/// the manual clear) is passed over; one that cannot be read now is an error,
/// since it cannot be checked ([`LibraryError::NotDownloaded`],
/// [`LibraryError::DigestMismatch`]).
fn refuse_foreign_folder(
    library: &Library,
    found: &PaperFolder,
    ids: &PaperIds,
    skip: &str,
) -> Result<(), LibraryError> {
    let mut seen = vec![skip];
    for entry in found.records.iter().flat_map(|r| &r.files) {
        if entry.kind != SourceKind::Jats || seen.contains(&entry.sha256.as_str()) {
            continue;
        }
        seen.push(&entry.sha256);
        let file = FileRef {
            folder: found.folder.clone(),
            name: entry.name.clone(),
            sha256: entry.sha256.clone(),
            kind: entry.kind,
        };
        match library.read(&file) {
            Ok(bytes) => refuse_foreign(Some(&found.folder), &entry.name, &bytes, ids)?,
            Err(LibraryError::Missing { .. }) => {}
            Err(e) => return Err(e),
        }
    }
    Ok(())
}

impl Library {
    /// Save `bytes` of kind `kind` for `paper` and note it in `who`'s record.
    ///
    /// The paper's folder is the folder already holding these bytes, even when
    /// the identifiers find another (the bytes are kept once); otherwise the
    /// first of the paper's folders [`find`] returns; otherwise a new folder,
    /// named by [`folder_name`] (with ` (2)`, ` (3)` … when another paper has
    /// the name, compared without regard to case). The file is written first,
    /// then the record (by the same merge as [`Library::record_use`]), so a
    /// crash between leaves only an unrecorded file, which saving the same
    /// bytes again records.
    ///
    /// A JATS whose own identifiers contradict `paper.ids` is refused, whether
    /// it is `bytes` or one a record in the chosen folder lists (#766): a folder
    /// holding another paper's JATS fails every later lookup of the paper, so
    /// nothing more is put in it until the manual clear.
    ///
    /// Within one process the update of a record is serialised, so threads of
    /// one installation never lose each other's entries; separate processes of
    /// one installation are out of scope.
    ///
    /// # Errors
    ///
    /// [`LibraryError::BadInstallation`] or [`LibraryError::BadIdentifier`] for
    /// a `who` or `paper.ids` the validating constructors would refuse (nothing
    /// is written); [`LibraryError::FileClash`] when a file of that name holds
    /// other bytes (nothing is overwritten); [`LibraryError::NewerRecord`] when
    /// `who`'s record, or every record of the folder the paper would go in, or a
    /// record of another folder named for it (#778), is from a newer layout (no
    /// second folder is made);
    /// [`LibraryError::NotDownloaded`] when `who`'s record is only an iCloud
    /// placeholder (nothing is written); [`LibraryError::ForeignJats`] when
    /// `bytes` are a JATS naming another paper or the folder holds one, and
    /// [`LibraryError::NotDownloaded`] or [`LibraryError::DigestMismatch`] when
    /// a JATS there cannot be checked (nothing is written);
    /// [`LibraryError::Io`] when no folder holds the paper, and a new one would
    /// be made while one named for one of its identifiers has a record that
    /// cannot be listed, read or downloaded, or is misnamed (#778, #755; nothing
    /// is written);
    /// [`LibraryError::ReadOnly`]; [`LibraryError::Io`].
    pub fn save(
        &self,
        who: &Installation,
        paper: &PaperInfo,
        bytes: &[u8],
        kind: SourceKind,
        origin: &str,
    ) -> Result<FileRef, LibraryError> {
        check_who(who)?;
        check_ids(&paper.ids)?;
        let digest = sha256_hex(bytes);
        let name = stored_name(kind, &digest);
        let entry = FileEntry {
            sha256: digest.clone(),
            kind,
            name: name.clone(),
            origin: origin.to_owned(),
            size: bytes.len() as u64,
            saved_at: now(),
        };
        // Before anything is written: the record this save would start must read back.
        let fresh = Record {
            installation: who.clone(),
            paper: paper.clone(),
            files: vec![entry.clone()],
        };
        record_text(&fresh, self.root())?;
        if kind == SourceKind::Jats {
            refuse_foreign(None, &name, bytes, &paper.ids)?;
        }
        // shortcut: save rescans the whole library per call, so a full-text
        // lookup costs one scan plus one per file saved (and one more when it
        // uses a held PDF); when that shows in a lookup's time, let the caller
        // pass its Scan.
        let scan = self.scan()?;
        let papers = self.root().join(PAPERS_DIR);
        // Bytes are kept once: the folder already holding them wins over the
        // identifiers, which may name another folder (a split paper).
        let existing = find_digest(&scan, &digest, None)
            .map(|r| r.folder)
            .or_else(|| find(&scan, &paper.ids).first().map(|f| f.folder.clone()));
        let folder = match existing {
            Some(f) => f,
            None => claim_folder(&papers, paper, &scan)?,
        };
        let dir = papers.join(component(&folder)?);
        let found = scan_folder(&dir, &folder, &mut Vec::new());
        // Only a JATS was checked above: the same bytes saved as another kind
        // skip nothing.
        let skip = if kind == SourceKind::Jats {
            digest.as_str()
        } else {
            ""
        };
        refuse_foreign_folder(self, &found, &paper.ids, skip)?;
        let path = dir
            .join(RECORDS_DIR)
            .join(component(&who.record_file_name())?);
        check_not_evicted(&path, &folder)?;
        write_once(&dir.join(&name), bytes, &digest).map_err(|e| match e {
            // `write_once` knows only the path; the error names the folder as
            // `FileRef` does (#755).
            LibraryError::NotDownloaded { name, .. } => LibraryError::NotDownloaded {
                folder: folder.clone(),
                name,
            },
            e => e,
        })?;
        note_use(&dir, &folder, who, paper, entry)?;
        Ok(FileRef {
            folder,
            name,
            sha256: digest,
            kind,
        })
    }

    /// Note in `who`'s record that it uses `file`, a file already in the
    /// library (such as one another installation saved), with what it knows of
    /// `paper`.
    ///
    /// The file is read through [`Library::read`], so it must be present with
    /// the bytes `file.sha256` names; then it is merged into the record exactly
    /// as [`Library::save`] does, under the same lock. The entry's origin is
    /// copied from a record in the folder that lists the file, or left empty
    /// when none does.
    ///
    /// # Errors
    ///
    /// [`LibraryError::BadInstallation`] or [`LibraryError::BadIdentifier`] as
    /// for `save`; [`LibraryError::BadName`] for a name that is not one path
    /// component or not the library's name for those bytes;
    /// [`LibraryError::Missing`], [`LibraryError::NotDownloaded`] or
    /// [`LibraryError::DigestMismatch`] from reading the file (nothing is
    /// written); [`LibraryError::ForeignJats`] when the file is a JATS naming
    /// another paper or its folder holds one, another JATS there that cannot be
    /// read failing as reading it does (nothing is written);
    /// [`LibraryError::NewerRecord`]; [`LibraryError::ReadOnly`];
    /// [`LibraryError::Io`].
    pub fn record_use(
        &self,
        who: &Installation,
        paper: &PaperInfo,
        file: &FileRef,
    ) -> Result<(), LibraryError> {
        check_who(who)?;
        check_ids(&paper.ids)?;
        let bytes = self.read(file)?;
        // `read` proved `file.sha256` is the bytes' digest, so it can be sliced.
        if file.name != stored_name(file.kind, &file.sha256) {
            return Err(LibraryError::BadName {
                name: file.name.clone(),
            });
        }
        let (dir, _) = locate(self.root(), file)?;
        let found = scan_folder(&dir, &file.folder, &mut Vec::new());
        let mut skip = "";
        if file.kind == SourceKind::Jats {
            refuse_foreign(Some(&file.folder), &file.name, &bytes, &paper.ids)?;
            skip = &file.sha256;
        }
        refuse_foreign_folder(self, &found, &paper.ids, skip)?;
        let origin = found
            .records
            .iter()
            .flat_map(|r| &r.files)
            .find(|f| f.sha256 == file.sha256 && f.kind == file.kind)
            .map_or_else(String::new, |f| f.origin.clone());
        let entry = FileEntry {
            sha256: file.sha256.clone(),
            kind: file.kind,
            name: file.name.clone(),
            origin,
            size: bytes.len() as u64,
            saved_at: now(),
        };
        let fresh = Record {
            installation: who.clone(),
            paper: paper.clone(),
            files: vec![entry.clone()],
        };
        record_text(&fresh, &dir)?;
        note_use(&dir, &file.folder, who, paper, entry)
    }

    /// Drop `file` from `who`'s record. The file stays, and so do the other
    /// installations' records. A file the record does not list (or no record)
    /// is already not used, so that is `Ok`. Serialised with [`Library::save`]
    /// in this process.
    ///
    /// # Errors
    ///
    /// [`LibraryError::BadInstallation`] for a `who` [`Installation::new`]
    /// would refuse; [`LibraryError::BadName`]; [`LibraryError::NewerRecord`];
    /// [`LibraryError::NotDownloaded`] when the record is only an iCloud
    /// placeholder; [`LibraryError::Io`] for a record that cannot be read (it
    /// is not touched), or when `papers/` or the folder is not a folder itself
    /// (#761).
    pub fn release(&self, who: &Installation, file: &FileRef) -> Result<(), LibraryError> {
        check_who(who)?;
        let (dir, _) = locate(self.root(), file)?;
        let path = dir
            .join(RECORDS_DIR)
            .join(component(&who.record_file_name())?);
        let _guard = record_guard();
        let bytes = match read_bytes(&path) {
            Ok(b) => b,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                return check_not_evicted(&path, &file.folder);
            }
            Err(e) if is_not_downloaded(&e) => return Err(not_downloaded(&path, &file.folder)),
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
                    version,
                })
            }
        };
        record
            .files
            .retain(|f| f.sha256 != file.sha256 || f.kind != file.kind);
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
    /// [`LibraryError::Missing`] when there is no such folder (or it is a
    /// file or a symbolic link); [`LibraryError::ReadOnly`];
    /// [`LibraryError::Io`], `papers/` not a folder itself (#761) included.
    pub fn remove_folder(&self, folder: &str) -> Result<(), LibraryError> {
        let papers = self.root().join(PAPERS_DIR);
        let dir = papers.join(component(folder)?);
        check_folder(&papers)?;
        if !is_dir(&dir).map_err(|e| io_error(&dir, &e, true))? {
            return Err(LibraryError::Missing {
                folder: PAPERS_DIR.to_owned(),
                name: folder.to_owned(),
            });
        }
        remove_dir_all(&dir).map_err(|e| io_error(&dir, &e, true))
    }
}
