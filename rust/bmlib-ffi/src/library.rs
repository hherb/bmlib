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

//! The shared paper library (`bmlib::library`) for the apps: open a folder the
//! user chose, scan it, find a paper or a file, read a file with its digest
//! checked, save, record a use, release, and remove.
//!
//! **What comes in is validated** by bmlib's own constructors: identifiers by
//! `PaperIds::canonical` (raw values are accepted and come back canonical), an
//! installation by `Installation::new`. A refusal is the same [`LibraryError`]
//! bmlib returns.
//!
//! **Every [`Library`] call does disk I/O on the calling thread**; call them off
//! the main thread. A [`LibraryScan`] is a snapshot: a later save does not
//! change it.
//!
//! Values going out are copied with exhaustive destructuring, so a field bmlib
//! adds fails to compile here until it is carried across. Fields are only ever
//! appended (Kotlin destructures by position).

use std::path::Path;
use std::sync::Arc;

use bmlib::library as lib;

// ---------------------------------------------------------------------------
// Records
// ---------------------------------------------------------------------------

/// What a stored source file is.
#[derive(Debug, Clone, Copy, PartialEq, Eq, uniffi::Enum)]
pub enum SourceKind {
    /// A PDF.
    Pdf,
    /// JATS XML.
    Jats,
    /// An HTML page.
    Html,
    /// Markdown.
    Markdown,
    /// Plain text.
    Text,
}

impl From<lib::SourceKind> for SourceKind {
    fn from(k: lib::SourceKind) -> Self {
        match k {
            lib::SourceKind::Pdf => Self::Pdf,
            lib::SourceKind::Jats => Self::Jats,
            lib::SourceKind::Html => Self::Html,
            lib::SourceKind::Markdown => Self::Markdown,
            lib::SourceKind::Text => Self::Text,
        }
    }
}

impl From<SourceKind> for lib::SourceKind {
    fn from(k: SourceKind) -> Self {
        match k {
            SourceKind::Pdf => Self::Pdf,
            SourceKind::Jats => Self::Jats,
            SourceKind::Html => Self::Html,
            SourceKind::Markdown => Self::Markdown,
            SourceKind::Text => Self::Text,
        }
    }
}

/// A paper's identifiers. Coming out of the library each is canonical or
/// `None`; going in, raw values are canonicalised and blank ones dropped.
#[derive(Debug, Clone, PartialEq, Eq, uniffi::Record)]
pub struct PaperIds {
    /// PubMed ID: digits without leading zeros, or `PPR` and digits.
    pub pmid: Option<String>,
    /// DOI: lower-case, starting `10.` and holding a `/`.
    pub doi: Option<String>,
    /// PubMed Central ID: `PMC` and digits without leading zeros.
    pub pmcid: Option<String>,
}

impl From<lib::PaperIds> for PaperIds {
    fn from(ids: lib::PaperIds) -> Self {
        let lib::PaperIds { pmid, doi, pmcid } = ids;
        Self { pmid, doi, pmcid }
    }
}

impl TryFrom<PaperIds> for lib::PaperIds {
    type Error = LibraryError;
    fn try_from(ids: PaperIds) -> Result<Self, LibraryError> {
        let PaperIds { pmid, doi, pmcid } = ids;
        Ok(lib::PaperIds::canonical(
            pmid.as_deref(),
            doi.as_deref(),
            pmcid.as_deref(),
        )?)
    }
}

/// What an app knows of a paper: its identifiers and what names its folder.
#[derive(Debug, Clone, PartialEq, Eq, uniffi::Record)]
pub struct PaperInfo {
    /// The paper's identifiers.
    pub ids: PaperIds,
    /// Its title.
    pub title: Option<String>,
    /// Its publication year.
    pub year: Option<i32>,
    /// Its first author's name.
    pub first_author: Option<String>,
}

impl From<lib::PaperInfo> for PaperInfo {
    fn from(p: lib::PaperInfo) -> Self {
        let lib::PaperInfo {
            ids,
            title,
            year,
            first_author,
        } = p;
        Self {
            ids: ids.into(),
            title,
            year,
            first_author,
        }
    }
}

impl TryFrom<PaperInfo> for lib::PaperInfo {
    type Error = LibraryError;
    fn try_from(p: PaperInfo) -> Result<Self, LibraryError> {
        let PaperInfo {
            ids,
            title,
            year,
            first_author,
        } = p;
        Ok(Self {
            ids: ids.try_into()?,
            title,
            year,
            first_author,
        })
    }
}

/// One app on one machine, which writes its own record per paper.
#[derive(Debug, Clone, PartialEq, Eq, uniffi::Record)]
pub struct Installation {
    /// The app, `[a-z0-9]{1,40}`.
    pub app: String,
    /// This installation, `[a-z0-9-]{1,64}`. Generate it once and keep it.
    pub id: String,
    /// A name a person recognises, such as the machine's: 1 to 200
    /// characters, no control or bidirectional control characters.
    pub label: String,
}

impl From<lib::Installation> for Installation {
    fn from(i: lib::Installation) -> Self {
        let lib::Installation { app, id, label } = i;
        Self { app, id, label }
    }
}

impl TryFrom<Installation> for lib::Installation {
    type Error = LibraryError;
    fn try_from(i: Installation) -> Result<Self, LibraryError> {
        Ok(lib::Installation::new(&i.app, &i.id, &i.label)?)
    }
}

/// A source file in a paper's folder, with the digest it should have. An app
/// stores only `sha256` and `kind`, and finds the file again with
/// [`LibraryScan::find_digest`].
#[derive(Debug, Clone, PartialEq, Eq, uniffi::Record)]
pub struct FileRef {
    /// The paper's folder, under `papers/`.
    pub folder: String,
    /// The file's name in the folder.
    pub name: String,
    /// The full SHA-256 the bytes must have, 64 lowercase hex digits.
    pub sha256: String,
    /// What the file is.
    pub kind: SourceKind,
}

impl From<lib::FileRef> for FileRef {
    fn from(f: lib::FileRef) -> Self {
        let lib::FileRef {
            folder,
            name,
            sha256,
            kind,
        } = f;
        Self {
            folder,
            name,
            sha256,
            kind: kind.into(),
        }
    }
}

impl From<FileRef> for lib::FileRef {
    fn from(f: FileRef) -> Self {
        let FileRef {
            folder,
            name,
            sha256,
            kind,
        } = f;
        Self {
            folder,
            name,
            sha256,
            kind: kind.into(),
        }
    }
}

/// One source file an installation's record lists.
#[derive(Debug, Clone, PartialEq, Eq, uniffi::Record)]
pub struct FileEntry {
    /// The SHA-256 of the bytes, 64 lowercase hex digits.
    pub sha256: String,
    /// What the file is.
    pub kind: SourceKind,
    /// Its name in the folder.
    pub name: String,
    /// Where the bytes came from, such as a repository's name; may be empty.
    pub origin: String,
    /// Its size in bytes.
    pub size: u64,
    /// When it was saved, as the writer formatted it.
    pub saved_at: String,
}

impl From<lib::FileEntry> for FileEntry {
    fn from(e: lib::FileEntry) -> Self {
        let lib::FileEntry {
            sha256,
            kind,
            name,
            origin,
            size,
            saved_at,
        } = e;
        Self {
            sha256,
            kind: kind.into(),
            name,
            origin,
            size,
            saved_at,
        }
    }
}

/// One installation's account of a paper (bmlib's `Record`).
#[derive(Debug, Clone, PartialEq, Eq, uniffi::Record)]
pub struct LibraryRecord {
    /// Who wrote the record.
    pub installation: Installation,
    /// What it knows of the paper.
    pub paper: PaperInfo,
    /// The files it uses.
    pub files: Vec<FileEntry>,
}

impl From<lib::Record> for LibraryRecord {
    fn from(r: lib::Record) -> Self {
        let lib::Record {
            installation,
            paper,
            files,
        } = r;
        Self {
            installation: installation.into(),
            paper: paper.into(),
            files: files.into_iter().map(Into::into).collect(),
        }
    }
}

/// One paper's folder as found on disk.
#[derive(Debug, Clone, PartialEq, Eq, uniffi::Record)]
pub struct PaperFolder {
    /// The folder's name under `papers/`.
    pub folder: String,
    /// The union of the records' identifiers; the first record wins a
    /// conflict, which the scan reports as [`LibraryProblem::ConflictingIds`].
    pub ids: PaperIds,
    /// The valid, correctly named records, by file name.
    pub records: Vec<LibraryRecord>,
    /// The source files present, by name.
    pub files_on_disk: Vec<String>,
}

impl From<&lib::PaperFolder> for PaperFolder {
    fn from(f: &lib::PaperFolder) -> Self {
        let lib::PaperFolder {
            folder,
            records,
            files_on_disk,
        } = f;
        Self {
            folder: folder.clone(),
            ids: f.ids().into(),
            records: records.iter().cloned().map(Into::into).collect(),
            files_on_disk: files_on_disk.clone(),
        }
    }
}

/// Something odd a scan found and did not use.
#[derive(Debug, Clone, PartialEq, Eq, uniffi::Enum)]
pub enum LibraryProblem {
    /// A record file could not be read or is not a valid record.
    UnreadableRecord {
        /// The paper's folder.
        folder: String,
        /// The record file.
        file: String,
        /// Why.
        reason: String,
    },
    /// A paper folder or its `records/` directory could not be listed.
    UnreadableFolder {
        /// The paper's folder.
        folder: String,
        /// Why.
        reason: String,
    },
    /// A record written by a newer layout.
    NewerRecord {
        /// The paper's folder.
        folder: String,
        /// The record file.
        file: String,
        /// Its version.
        version: u32,
    },
    /// A record present only as an iCloud placeholder: its installation's
    /// uses are unknown until it is downloaded.
    RecordNotDownloaded {
        /// The paper's folder.
        folder: String,
        /// The record file it stands for.
        file: String,
    },
    /// A temporary file of an atomic write, in flight or left by a crash.
    /// Never deleted.
    LeftoverTemp {
        /// The paper's folder.
        folder: String,
        /// The file, as `records/<name>` when it is in `records/`.
        name: String,
    },
    /// A valid record whose file name is not its installation's.
    MisnamedRecord {
        /// The paper's folder.
        folder: String,
        /// The record file.
        file: String,
    },
    /// Two records of one folder hold different values for one identifier.
    ConflictingIds {
        /// The paper's folder.
        folder: String,
        /// `"pmid"`, `"doi"` or `"pmcid"`.
        field: String,
    },
    /// A source file no valid record lists.
    UnrecordedFile {
        /// The paper's folder.
        folder: String,
        /// The file.
        name: String,
    },
    /// An entry of `papers/` that is not a folder.
    StrayEntry {
        /// The entry.
        name: String,
    },
}

impl From<&lib::Problem> for LibraryProblem {
    fn from(p: &lib::Problem) -> Self {
        use lib::Problem as P;
        match p.clone() {
            P::UnreadableRecord {
                folder,
                file,
                reason,
            } => Self::UnreadableRecord {
                folder,
                file,
                reason,
            },
            P::UnreadableFolder { folder, reason } => Self::UnreadableFolder { folder, reason },
            P::NewerRecord {
                folder,
                file,
                version,
            } => Self::NewerRecord {
                folder,
                file,
                version,
            },
            P::RecordNotDownloaded { folder, file } => Self::RecordNotDownloaded { folder, file },
            P::LeftoverTemp { folder, name } => Self::LeftoverTemp { folder, name },
            P::MisnamedRecord { folder, file } => Self::MisnamedRecord { folder, file },
            P::ConflictingIds { folder, field } => Self::ConflictingIds {
                folder,
                field: field.to_owned(),
            },
            P::UnrecordedFile { folder, name } => Self::UnrecordedFile { folder, name },
            P::StrayEntry { name } => Self::StrayEntry { name },
        }
    }
}

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

/// Why a library call failed: bmlib's `LibraryError`, case for case. Paths are
/// strings. Its text (Swift's `errorDescription`, Kotlin's `toString`) is
/// bmlib's own wording, fit to show the user. No field is named `message`,
/// which collides with Kotlin's `Throwable.message`.
#[derive(Debug, Clone, PartialEq, Eq, uniffi::Error)]
#[uniffi::export(Display)]
pub enum LibraryError {
    /// The library's folder is missing or not a folder (an unmounted drive,
    /// a revoked bookmark): keep sources waiting for the library.
    Unreachable {
        /// The folder.
        path: String,
        /// Why.
        reason: String,
    },
    /// The folder holds something other than a library.
    NotALibrary {
        /// The folder.
        path: String,
        /// Why.
        reason: String,
    },
    /// The library's layout is newer than this bmlib.
    NewerLibrary {
        /// Its layout version.
        version: u64,
    },
    /// The library cannot be written.
    ReadOnly {
        /// What could not be written.
        path: String,
    },
    /// An identifier is not valid.
    BadIdentifier {
        /// `"pmid"`, `"doi"` or `"pmcid"`.
        field: String,
        /// The value refused.
        value: String,
    },
    /// An installation is not acceptable.
    BadInstallation {
        /// `"app"`, `"id"` or `"label"`.
        field: String,
        /// Why.
        reason: String,
    },
    /// A folder or file name that is not one plain path component.
    BadName {
        /// The name.
        name: String,
    },
    /// The file is not there.
    Missing {
        /// The paper's folder.
        folder: String,
        /// The file.
        name: String,
    },
    /// Only an iCloud placeholder is here: the file is not downloaded yet.
    NotDownloaded {
        /// The paper's folder.
        folder: String,
        /// The file.
        name: String,
    },
    /// The file's bytes are not the ones its digest names.
    DigestMismatch {
        /// The paper's folder.
        folder: String,
        /// The file.
        name: String,
        /// The digest asked for.
        expected: String,
        /// The digest of the bytes found.
        found: String,
    },
    /// A different file of that name is already there; nothing was overwritten.
    FileClash {
        /// The paper's folder.
        folder: String,
        /// The file.
        name: String,
    },
    /// A record from a newer layout is in the way.
    NewerRecord {
        /// The paper's folder.
        folder: String,
        /// The record file.
        file: String,
        /// Its version.
        version: u32,
    },
    /// Any other file-system failure.
    Io {
        /// The path concerned.
        path: String,
        /// Why.
        reason: String,
    },
}

impl From<lib::LibraryError> for LibraryError {
    fn from(e: lib::LibraryError) -> Self {
        use lib::LibraryError as E;
        let s = |p: &Path| p.display().to_string();
        match e {
            E::Unreachable { path, reason } => Self::Unreachable {
                path: s(&path),
                reason,
            },
            E::NotALibrary { path, reason } => Self::NotALibrary {
                path: s(&path),
                reason,
            },
            E::NewerLibrary { version } => Self::NewerLibrary { version },
            E::ReadOnly { path } => Self::ReadOnly { path: s(&path) },
            E::BadIdentifier { field, value } => Self::BadIdentifier {
                field: field.to_owned(),
                value,
            },
            E::BadInstallation { field, reason } => Self::BadInstallation {
                field: field.to_owned(),
                reason,
            },
            E::BadName { name } => Self::BadName { name },
            E::Missing { folder, name } => Self::Missing { folder, name },
            E::NotDownloaded { folder, name } => Self::NotDownloaded { folder, name },
            E::DigestMismatch {
                folder,
                name,
                expected,
                found,
            } => Self::DigestMismatch {
                folder,
                name,
                expected,
                found,
            },
            E::FileClash { folder, name } => Self::FileClash { folder, name },
            E::NewerRecord {
                folder,
                file,
                version,
            } => Self::NewerRecord {
                folder,
                file,
                version,
            },
            E::Io { path, reason } => Self::Io {
                path: s(&path),
                reason,
            },
        }
    }
}

/// The `&'static str` bmlib names a field with. bmlib only ever names these
/// six; anything else (an error the app built itself) reads as `"value"`.
fn field_name(field: &str) -> &'static str {
    ["pmid", "doi", "pmcid", "app", "id", "label"]
        .into_iter()
        .find(|f| *f == field)
        .unwrap_or("value")
}

impl LibraryError {
    /// The bmlib error this one was made from, for its wording.
    fn to_core(&self) -> lib::LibraryError {
        use lib::LibraryError as E;
        let p = |s: &str| std::path::PathBuf::from(s);
        match self.clone() {
            Self::Unreachable { path, reason } => E::Unreachable {
                path: p(&path),
                reason,
            },
            Self::NotALibrary { path, reason } => E::NotALibrary {
                path: p(&path),
                reason,
            },
            Self::NewerLibrary { version } => E::NewerLibrary { version },
            Self::ReadOnly { path } => E::ReadOnly { path: p(&path) },
            Self::BadIdentifier { field, value } => E::BadIdentifier {
                field: field_name(&field),
                value,
            },
            Self::BadInstallation { field, reason } => E::BadInstallation {
                field: field_name(&field),
                reason,
            },
            Self::BadName { name } => E::BadName { name },
            Self::Missing { folder, name } => E::Missing { folder, name },
            Self::NotDownloaded { folder, name } => E::NotDownloaded { folder, name },
            Self::DigestMismatch {
                folder,
                name,
                expected,
                found,
            } => E::DigestMismatch {
                folder,
                name,
                expected,
                found,
            },
            Self::FileClash { folder, name } => E::FileClash { folder, name },
            Self::NewerRecord {
                folder,
                file,
                version,
            } => E::NewerRecord {
                folder,
                file,
                version,
            },
            Self::Io { path, reason } => E::Io {
                path: p(&path),
                reason,
            },
        }
    }
}

impl std::fmt::Display for LibraryError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        self.to_core().fmt(f)
    }
}
impl std::error::Error for LibraryError {}

// ---------------------------------------------------------------------------
// Objects
// ---------------------------------------------------------------------------

/// A library folder, opened and checked.
#[derive(Debug, uniffi::Object)]
pub struct Library(lib::Library);

#[uniffi::export]
impl Library {
    /// Open the library at `root`, creating it in an empty folder. On macOS and
    /// iOS, `root` is the path of the resolved security-scoped bookmark, whose
    /// access the app holds for as long as it uses the library.
    ///
    /// # Errors
    ///
    /// `Unreachable` (missing or not a folder), `NotALibrary` (holds other
    /// things), `NewerLibrary`, `ReadOnly` or `Io`.
    #[uniffi::constructor]
    pub fn open(root: String) -> Result<Arc<Self>, LibraryError> {
        Ok(Arc::new(Self(lib::Library::open(Path::new(&root))?)))
    }

    /// The library's folder, as given to [`Library::open`].
    pub fn root(&self) -> String {
        self.0.root().display().to_string()
    }

    /// Read every paper folder. Never writes.
    ///
    /// # Errors
    ///
    /// `Io` when `papers/` cannot be listed; trouble inside a folder is a
    /// [`LibraryScan::problems`] entry instead.
    pub fn scan(&self) -> Result<Arc<LibraryScan>, LibraryError> {
        Ok(Arc::new(LibraryScan(self.0.scan()?)))
    }

    /// A file's bytes, checked against `file.sha256`.
    ///
    /// # Errors
    ///
    /// `BadName`, `NotDownloaded`, `Missing`, `DigestMismatch` or `Io`.
    pub fn read(&self, file: FileRef) -> Result<Vec<u8>, LibraryError> {
        Ok(self.0.read(&file.into())?)
    }

    /// Save `bytes` for `paper` and note them in `who`'s record. Bytes already
    /// in the library are not written again: the use goes to the folder that
    /// holds them. Returns where the file is.
    ///
    /// # Errors
    ///
    /// `BadInstallation` or `BadIdentifier` (nothing written), `FileClash`
    /// (nothing overwritten), `NewerRecord`, `NotDownloaded`, `ReadOnly` or
    /// `Io`.
    pub fn save(
        &self,
        who: Installation,
        paper: PaperInfo,
        bytes: Vec<u8>,
        kind: SourceKind,
        origin: String,
    ) -> Result<FileRef, LibraryError> {
        let (who, paper) = (who.try_into()?, paper.try_into()?);
        Ok(self
            .0
            .save(&who, &paper, &bytes, kind.into(), &origin)?
            .into())
    }

    /// Note in `who`'s record that it uses `file`, a file already in the
    /// library (one another app saved, say). The bytes are read and checked
    /// first.
    ///
    /// # Errors
    ///
    /// As for [`Library::save`], plus `BadName`, `Missing`, `NotDownloaded` or
    /// `DigestMismatch` from reading the file (nothing written).
    pub fn record_use(
        &self,
        who: Installation,
        paper: PaperInfo,
        file: FileRef,
    ) -> Result<(), LibraryError> {
        let (who, paper) = (who.try_into()?, paper.try_into()?);
        Ok(self.0.record_use(&who, &paper, &file.into())?)
    }

    /// Drop `file` from `who`'s record. The file and other records stay. A
    /// file the record does not list is already released, so that is `Ok`.
    ///
    /// # Errors
    ///
    /// `BadInstallation`, `BadName`, `NewerRecord`, `NotDownloaded` or `Io`.
    pub fn release(&self, who: Installation, file: FileRef) -> Result<(), LibraryError> {
        Ok(self.0.release(&who.try_into()?, &file.into())?)
    }

    /// Delete one source file: the user's manual clear, never automatic.
    ///
    /// # Errors
    ///
    /// `BadName`, `Missing`, `ReadOnly` or `Io`.
    pub fn remove_file(&self, file: FileRef) -> Result<(), LibraryError> {
        Ok(self.0.remove_file(&file.into())?)
    }

    /// Delete a paper's whole folder, records included: the user's manual
    /// clear, never automatic.
    ///
    /// # Errors
    ///
    /// `BadName`, `Missing`, `ReadOnly` or `Io`.
    pub fn remove_folder(&self, folder: String) -> Result<(), LibraryError> {
        Ok(self.0.remove_folder(&folder)?)
    }
}

/// What [`Library::scan`] found: a snapshot, unchanged by later saves.
#[derive(Debug, uniffi::Object)]
pub struct LibraryScan(lib::Scan);

#[uniffi::export]
impl LibraryScan {
    /// Every paper folder, ordered by name.
    pub fn folders(&self) -> Vec<PaperFolder> {
        self.0.folders.iter().map(Into::into).collect()
    }

    /// Everything odd, in the order found.
    pub fn problems(&self) -> Vec<LibraryProblem> {
        self.0.problems.iter().map(Into::into).collect()
    }

    /// Every folder of the paper(s) matching `ids`: a paper split across
    /// folders by its identifiers comes back whole.
    ///
    /// # Errors
    ///
    /// `BadIdentifier` for an identifier that is not valid.
    pub fn find(&self, ids: PaperIds) -> Result<Vec<PaperFolder>, LibraryError> {
        let ids = ids.try_into()?;
        Ok(lib::find(&self.0, &ids)
            .into_iter()
            .map(Into::into)
            .collect())
    }

    /// The file named for `sha256`, if any. Only the name is looked at;
    /// [`Library::read`] checks the bytes.
    pub fn find_digest(&self, sha256: String) -> Option<FileRef> {
        lib::find_digest(&self.0, &sha256).map(Into::into)
    }

    /// Indices into [`LibraryScan::folders`] of folders that are one paper,
    /// each group and the list sorted.
    pub fn group(&self) -> Vec<Vec<u64>> {
        lib::group(&self.0)
            .into_iter()
            .map(|g| g.into_iter().map(|i| i as u64).collect())
            .collect()
    }

    /// The installations whose record in `folder` lists a file with this
    /// digest; empty for a folder the scan did not find.
    pub fn used_by(&self, folder: String, sha256: String) -> Vec<Installation> {
        self.0
            .folders
            .iter()
            .find(|f| f.folder == folder)
            .map(|f| {
                f.used_by(&sha256)
                    .into_iter()
                    .cloned()
                    .map(Into::into)
                    .collect()
            })
            .unwrap_or_default()
    }
}

// ---------------------------------------------------------------------------
// Functions
// ---------------------------------------------------------------------------

/// Canonical identifiers from raw ones; blank values become `None`. Use it to
/// check identifiers before storing them.
///
/// # Errors
///
/// `BadIdentifier` naming the first field that is not valid.
#[uniffi::export]
pub fn paper_ids_canonical(
    pmid: Option<String>,
    doi: Option<String>,
    pmcid: Option<String>,
) -> Result<PaperIds, LibraryError> {
    Ok(lib::PaperIds::try_from(PaperIds { pmid, doi, pmcid })?.into())
}

/// A validated installation.
///
/// # Errors
///
/// `BadInstallation` naming the field that is not acceptable.
#[uniffi::export]
pub fn installation_new(
    app: String,
    id: String,
    label: String,
) -> Result<Installation, LibraryError> {
    Ok(lib::Installation::new(&app, &id, &label)?.into())
}

#[cfg(test)]
#[path = "library_tests.rs"]
mod tests;
