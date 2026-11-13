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

//! Reading a library: scan it, find papers by identifier or file by digest,
//! group folders that are one paper split in two, and read a file with its
//! digest checked.
//!
//! A scan never fails on one bad folder: every oddity is a [`Problem`], so the
//! caller can show it (a record is never silently dropped).

use std::path::Path;

use super::files::{
    component, has_placeholder, io_error, is_dir, list_names, read_bytes, sha256_hex,
};
use super::{
    parse_record, Installation, Library, LibraryError, PaperIds, Record, RecordProblem, SourceKind,
    DIGEST_PREFIX_HEX, PAPERS_DIR, RECORDS_DIR,
};

/// Every source kind, in the order [`find_digest`] tries them.
const KINDS: [SourceKind; 5] = [
    SourceKind::Pdf,
    SourceKind::Jats,
    SourceKind::Html,
    SourceKind::Markdown,
    SourceKind::Text,
];

/// One paper's folder as found on disk.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PaperFolder {
    /// The folder's name under `papers/`.
    pub folder: String,
    /// The valid, correctly named records, by file name (bytes).
    pub records: Vec<Record>,
    /// The source files present, by name (bytes); placeholders and `records/` are left out.
    pub files_on_disk: Vec<String>,
}

impl PaperFolder {
    /// The union of the records' identifiers; the first record wins a conflict.
    #[must_use]
    pub fn ids(&self) -> PaperIds {
        self.records
            .iter()
            .fold(PaperIds::default(), |acc, r| acc.merged(&r.paper.ids).0)
    }

    /// The installations whose record lists a file with this digest.
    #[must_use]
    pub fn used_by(&self, sha256: &str) -> Vec<&Installation> {
        self.records
            .iter()
            .filter(|r| r.files.iter().any(|f| f.sha256 == sha256))
            .map(|r| &r.installation)
            .collect()
    }
}

/// Something odd a scan found and did not use.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Problem {
    /// A record (or, with `file` `"records"`/`"."`, a listing) could not be read.
    UnreadableRecord {
        /// The paper's folder.
        folder: String,
        /// The record file.
        file: String,
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
    /// A valid record whose file name is not its installation's.
    MisnamedRecord {
        /// The paper's folder.
        folder: String,
        /// The record file.
        file: String,
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

/// The result of scanning a library.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Scan {
    /// Paper folders, ordered by name (bytes).
    pub folders: Vec<PaperFolder>,
    /// Everything odd, in the order found.
    pub problems: Vec<Problem>,
}

/// A source file in a paper's folder, with the digest it should have.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FileRef {
    /// The paper's folder.
    pub folder: String,
    /// The file's name.
    pub name: String,
    /// The full SHA-256 the bytes must have.
    pub sha256: String,
    /// What the file is.
    pub kind: SourceKind,
}

/// Scan one paper folder, adding what is odd to `problems`.
fn scan_folder(dir: &Path, folder: &str, problems: &mut Vec<Problem>) -> PaperFolder {
    let unreadable = |file: &str, reason: String| Problem::UnreadableRecord {
        folder: folder.to_owned(),
        file: file.to_owned(),
        reason,
    };
    let mut records = Vec::new();
    let records_dir = dir.join(RECORDS_DIR);
    if is_dir(&records_dir) {
        match list_names(&records_dir) {
            Err(e) => problems.push(unreadable(RECORDS_DIR, e.to_string())),
            Ok(names) => {
                for file in names {
                    let path = records_dir.join(&file);
                    if is_dir(&path) {
                        continue;
                    }
                    let parsed = read_bytes(&path)
                        .map_err(|e| e.to_string())
                        .and_then(|b| String::from_utf8(b).map_err(|e| e.to_string()))
                        .map_err(|reason| unreadable(&file, reason))
                        .and_then(|text| {
                            parse_record(&text).map_err(|p| match p {
                                RecordProblem::Unreadable(reason) => unreadable(&file, reason),
                                RecordProblem::Newer(version) => Problem::NewerRecord {
                                    folder: folder.to_owned(),
                                    file: file.clone(),
                                    version,
                                },
                            })
                        });
                    match parsed {
                        Err(problem) => problems.push(problem),
                        Ok(r) if r.installation.record_file_name() != file => {
                            problems.push(Problem::MisnamedRecord {
                                folder: folder.to_owned(),
                                file,
                            });
                        }
                        Ok(r) => records.push(r),
                    }
                }
            }
        }
    }
    let files_on_disk = match list_names(dir) {
        Ok(names) => names
            .into_iter()
            .filter(|n| !is_dir(&dir.join(n)))
            .collect(),
        Err(e) => {
            problems.push(unreadable(".", e.to_string()));
            Vec::new()
        }
    };
    for name in &files_on_disk {
        if !records
            .iter()
            .any(|r| r.files.iter().any(|f| &f.name == name))
        {
            problems.push(Problem::UnrecordedFile {
                folder: folder.to_owned(),
                name: name.clone(),
            });
        }
    }
    PaperFolder {
        folder: folder.to_owned(),
        records,
        files_on_disk,
    }
}

impl Library {
    /// Read every paper folder. Never writes.
    ///
    /// # Errors
    ///
    /// [`LibraryError::Io`] when `papers/` itself cannot be listed; trouble inside
    /// a folder is a [`Problem`] in the result instead.
    pub fn scan(&self) -> Result<Scan, LibraryError> {
        let papers = self.root().join(PAPERS_DIR);
        let names = list_names(&papers).map_err(|e| io_error(&papers, &e, false))?;
        let (mut folders, mut problems) = (Vec::new(), Vec::new());
        for name in names {
            let dir = papers.join(&name);
            if is_dir(&dir) {
                folders.push(scan_folder(&dir, &name, &mut problems));
            } else {
                problems.push(Problem::StrayEntry { name });
            }
        }
        Ok(Scan { folders, problems })
    }

    /// Read a file's bytes, checking them against `file.sha256`.
    ///
    /// # Errors
    ///
    /// [`LibraryError::BadName`] for a folder or file name that is not one path
    /// component; [`LibraryError::NotDownloaded`] when only an online placeholder is
    /// here; [`LibraryError::Missing`]; [`LibraryError::DigestMismatch`]; [`LibraryError::Io`].
    pub fn read(&self, file: &FileRef) -> Result<Vec<u8>, LibraryError> {
        let dir = self.root().join(PAPERS_DIR).join(component(&file.folder)?);
        let path = dir.join(component(&file.name)?);
        let bytes = match read_bytes(&path) {
            Ok(b) => b,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                let (folder, name) = (file.folder.clone(), file.name.clone());
                return Err(if has_placeholder(&dir, &file.name) {
                    LibraryError::NotDownloaded { folder, name }
                } else {
                    LibraryError::Missing { folder, name }
                });
            }
            Err(e) => return Err(io_error(&path, &e, false)),
        };
        let found = sha256_hex(&bytes);
        if found != file.sha256 {
            return Err(LibraryError::DigestMismatch {
                folder: file.folder.clone(),
                name: file.name.clone(),
                expected: file.sha256.clone(),
                found,
            });
        }
        Ok(bytes)
    }
}

/// The folders whose identifiers share any with `ids`, in scan order.
#[must_use]
pub fn find<'a>(scan: &'a Scan, ids: &PaperIds) -> Vec<&'a PaperFolder> {
    scan.folders
        .iter()
        .filter(|f| f.ids().shares_any(ids))
        .collect()
}

/// A file on disk named for `sha256` (`{kind}-{first 12 hex}.{ext}`), if any.
///
/// Only the name is looked at: [`Library::read`] checks the full digest. A digest
/// that is not 64 lowercase hex digits finds nothing.
#[must_use]
pub fn find_digest(scan: &Scan, sha256: &str) -> Option<FileRef> {
    let hex = |b: u8| matches!(b, b'0'..=b'9' | b'a'..=b'f');
    if sha256.len() != 64 || !sha256.bytes().all(hex) {
        return None;
    }
    let prefix = &sha256[..DIGEST_PREFIX_HEX];
    scan.folders.iter().find_map(|f| {
        KINDS.iter().find_map(|&kind| {
            let name = format!("{}-{prefix}.{}", kind.as_str(), kind.extension());
            f.files_on_disk.contains(&name).then(|| FileRef {
                folder: f.folder.clone(),
                name,
                sha256: sha256.to_owned(),
                kind,
            })
        })
    })
}

/// Indices of folders connected by shared identifiers, each group and the list
/// sorted. A folder with no identifiers stands alone.
#[must_use]
pub fn group(scan: &Scan) -> Vec<Vec<usize>> {
    let ids: Vec<PaperIds> = scan.folders.iter().map(PaperFolder::ids).collect();
    let mut parent: Vec<usize> = (0..ids.len()).collect();
    fn root(parent: &mut [usize], mut i: usize) -> usize {
        while parent[i] != i {
            parent[i] = parent[parent[i]];
            i = parent[i];
        }
        i
    }
    for i in 0..ids.len() {
        for j in (i + 1)..ids.len() {
            if ids[i].shares_any(&ids[j]) {
                let (a, b) = (root(&mut parent, i), root(&mut parent, j));
                parent[a.max(b)] = a.min(b);
            }
        }
    }
    let mut groups: Vec<Vec<usize>> = Vec::new();
    let mut slot = vec![usize::MAX; ids.len()];
    for i in 0..ids.len() {
        let r = root(&mut parent, i);
        if slot[r] == usize::MAX {
            slot[r] = groups.len();
            groups.push(Vec::new());
        }
        groups[slot[r]].push(i);
    }
    groups
}

#[cfg(test)]
mod tests {
    use super::*;

    fn folder(name: &str, pmid: Option<&str>, doi: Option<&str>) -> PaperFolder {
        use crate::library::{PaperInfo, Record};
        let record = Record {
            installation: Installation::new("studyanalyzer", "a", "Mac").unwrap(),
            paper: PaperInfo {
                ids: PaperIds::canonical(pmid, doi, None).unwrap(),
                ..PaperInfo::default()
            },
            files: Vec::new(),
        };
        PaperFolder {
            folder: name.to_owned(),
            records: vec![record],
            files_on_disk: Vec::new(),
        }
    }

    #[test]
    fn group_joins_transitively_and_keeps_scan_order() {
        // 0 and 2 share the PMID, 2 and 3 share the DOI; 1 stands alone.
        let scan = Scan {
            folders: vec![
                folder("a", Some("1"), None),
                folder("b", Some("2"), None),
                folder("c", Some("1"), Some("10.1/x")),
                folder("d", None, Some("10.1/x")),
            ],
            problems: Vec::new(),
        };
        assert_eq!(group(&scan), vec![vec![0, 2, 3], vec![1]]);
    }

    #[test]
    fn group_keeps_folders_without_identifiers_apart() {
        let scan = Scan {
            folders: vec![folder("a", None, None), folder("b", None, None)],
            problems: Vec::new(),
        };
        assert_eq!(group(&scan), vec![vec![0], vec![1]]);
    }

    #[test]
    fn ids_first_record_wins_a_conflict() {
        let mut f = folder("a", Some("1"), None);
        f.records
            .push(folder("a", Some("2"), Some("10.1/x")).records.remove(0));
        let ids = f.ids();
        assert_eq!(ids.pmid.as_deref(), Some("1"));
        assert_eq!(ids.doi.as_deref(), Some("10.1/x"));
    }

    #[test]
    fn find_digest_refuses_a_malformed_digest() {
        let scan = Scan {
            folders: Vec::new(),
            problems: Vec::new(),
        };
        assert_eq!(find_digest(&scan, "ABC"), None);
        assert_eq!(find_digest(&scan, &"A".repeat(64)), None);
    }
}
