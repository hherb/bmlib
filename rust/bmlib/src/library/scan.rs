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

use std::collections::hash_map::Entry;
use std::collections::HashMap;
use std::path::Path;

use serde::{Deserialize, Serialize};

use super::files::{
    component, exists, has_placeholder, io_error, is_dir, listing, read_bytes, sha256_hex,
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
    /// The source files present, by name (bytes); placeholders and `records/`
    /// are left out.
    pub files_on_disk: Vec<String>,
}

impl PaperFolder {
    /// The union of the records' identifiers; the first record wins a conflict
    /// (a scan reports it as [`Problem::ConflictingIds`]). To match a query,
    /// use [`PaperFolder::shares_any`], which sees every record's value.
    #[must_use]
    pub fn ids(&self) -> PaperIds {
        self.records
            .iter()
            .fold(PaperIds::default(), |acc, r| acc.merged(&r.paper.ids).0)
    }

    /// True when any record here holds the same value as `ids` for an
    /// identifier, so a value that lost a conflict inside the folder still
    /// finds it.
    #[must_use]
    pub fn shares_any(&self, ids: &PaperIds) -> bool {
        self.records.iter().any(|r| r.paper.ids.shares_any(ids))
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
    /// A record present only as an iCloud placeholder (`.<file>.icloud`): its
    /// installation's uses are unknown until it is downloaded.
    RecordNotDownloaded {
        /// The paper's folder.
        folder: String,
        /// The record file it stands for.
        file: String,
    },
    /// A temporary file of `atomic_write`, from a write in flight or one a
    /// crash interrupted. It is left alone, never deleted.
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
    /// Both values count for [`find`] and [`group`]; [`PaperFolder::ids`] keeps
    /// the first record's.
    ConflictingIds {
        /// The paper's folder.
        folder: String,
        /// `"pmid"`, `"doi"` or `"pmcid"`.
        field: &'static str,
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
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
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
pub(super) fn scan_folder(dir: &Path, folder: &str, problems: &mut Vec<Problem>) -> PaperFolder {
    let unreadable_folder = |reason: String| Problem::UnreadableFolder {
        folder: folder.to_owned(),
        reason,
    };
    let unreadable = |file: &str, reason: String| Problem::UnreadableRecord {
        folder: folder.to_owned(),
        file: file.to_owned(),
        reason,
    };
    let mut records = Vec::new();
    let records_dir = dir.join(RECORDS_DIR);
    if exists(&records_dir) && !is_dir(&records_dir) {
        problems.push(unreadable_folder(format!("{RECORDS_DIR} is not a folder")));
    } else if is_dir(&records_dir) {
        match listing(&records_dir) {
            Err(e) => problems.push(unreadable_folder(format!("{RECORDS_DIR}: {e}"))),
            Ok(found) => {
                for file in found.names.iter().cloned() {
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
                for file in found.placeholders {
                    if !found.names.contains(&file) {
                        problems.push(Problem::RecordNotDownloaded {
                            folder: folder.to_owned(),
                            file,
                        });
                    }
                }
                for name in found.temps {
                    problems.push(Problem::LeftoverTemp {
                        folder: folder.to_owned(),
                        name: format!("{RECORDS_DIR}/{name}"),
                    });
                }
            }
        }
    }
    let mut acc = PaperIds::default();
    let mut conflicts: Vec<&'static str> = Vec::new();
    for r in &records {
        let (merged, fields) = acc.merged(&r.paper.ids);
        acc = merged;
        for field in fields {
            if !conflicts.contains(&field) {
                conflicts.push(field);
                problems.push(Problem::ConflictingIds {
                    folder: folder.to_owned(),
                    field,
                });
            }
        }
    }
    let files_on_disk = match listing(dir) {
        Ok(found) => {
            for name in found.temps {
                problems.push(Problem::LeftoverTemp {
                    folder: folder.to_owned(),
                    name,
                });
            }
            found
                .names
                .into_iter()
                .filter(|n| n != RECORDS_DIR && !is_dir(&dir.join(n)))
                .collect()
        }
        Err(e) => {
            problems.push(unreadable_folder(e.to_string()));
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
    /// [`LibraryError::Io`] when `papers/` itself cannot be listed; trouble
    /// inside a folder is a [`Problem`] in the result instead.
    pub fn scan(&self) -> Result<Scan, LibraryError> {
        let papers = self.root().join(PAPERS_DIR);
        let found = listing(&papers).map_err(|e| io_error(&papers, &e, false))?;
        let (mut folders, mut problems) = (Vec::new(), Vec::new());
        for name in found.names {
            let dir = papers.join(&name);
            if is_dir(&dir) {
                folders.push(scan_folder(&dir, &name, &mut problems));
            } else {
                problems.push(Problem::StrayEntry { name });
            }
        }
        // Nothing writes a file into `papers/` itself; a temporary there is stray.
        problems.extend(
            found
                .temps
                .into_iter()
                .map(|name| Problem::StrayEntry { name }),
        );
        Ok(Scan { folders, problems })
    }

    /// Read a file's bytes, checking them against `file.sha256`.
    ///
    /// # Errors
    ///
    /// [`LibraryError::BadName`] for a folder or file name that is not one path
    /// component; [`LibraryError::NotDownloaded`] when only an online
    /// placeholder is here; [`LibraryError::Missing`];
    /// [`LibraryError::DigestMismatch`]; [`LibraryError::Io`].
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

/// Every folder of the paper(s) matching `ids`, in scan order.
///
/// A paper is the union of folders whose records share an identifier, so this
/// is each folder in the [`group`] of any folder with a record sharing one with
/// `ids`.
#[must_use]
pub fn find<'a>(scan: &'a Scan, ids: &PaperIds) -> Vec<&'a PaperFolder> {
    let mut hit = vec![false; scan.folders.len()];
    for g in group(scan) {
        if g.iter().any(|&i| scan.folders[i].shares_any(ids)) {
            for i in g {
                hit[i] = true;
            }
        }
    }
    scan.folders
        .iter()
        .zip(hit)
        .filter_map(|(f, h)| h.then_some(f))
        .collect()
}

/// A file on disk named for `sha256` (`{kind}-{first 12 hex}.{ext}`), if any.
///
/// The same bytes saved as two kinds are two files, so pass `kind` to find the
/// one stored as that kind; with `None`, the first folder holding any kind
/// wins, and in it the first kind in [`SourceKind`]'s declaration order. Only
/// the name is looked at: [`Library::read`] checks the full digest. A digest
/// that is not 64 lowercase hex digits finds nothing.
#[must_use]
pub fn find_digest(scan: &Scan, sha256: &str, kind: Option<SourceKind>) -> Option<FileRef> {
    let hex = |b: u8| matches!(b, b'0'..=b'9' | b'a'..=b'f');
    if sha256.len() != 64 || !sha256.bytes().all(hex) {
        return None;
    }
    let prefix = &sha256[..DIGEST_PREFIX_HEX];
    let kinds = kind.as_ref().map_or(&KINDS[..], std::slice::from_ref);
    scan.folders.iter().find_map(|f| {
        kinds.iter().find_map(|&kind| {
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
/// sorted. Every record's identifiers count, not only [`PaperFolder::ids`]. A
/// folder with no identifiers stands alone.
///
/// Linear in the number of records: each identifier value is mapped to the
/// first folder holding it, and later holders are joined to that one.
#[must_use]
pub fn group(scan: &Scan) -> Vec<Vec<usize>> {
    let n = scan.folders.len();
    let mut parent: Vec<usize> = (0..n).collect();
    /// The representative of `i`'s set, halving the path on the way.
    fn root(parent: &mut [usize], mut i: usize) -> usize {
        while parent[i] != i {
            parent[i] = parent[parent[i]];
            i = parent[i];
        }
        i
    }
    let mut first: HashMap<(&'static str, &str), usize> = HashMap::new();
    for (i, f) in scan.folders.iter().enumerate() {
        for r in &f.records {
            let ids = &r.paper.ids;
            for (field, value) in [
                ("pmid", &ids.pmid),
                ("doi", &ids.doi),
                ("pmcid", &ids.pmcid),
            ] {
                let Some(value) = value else { continue };
                match first.entry((field, value.as_str())) {
                    Entry::Vacant(e) => {
                        e.insert(i);
                    }
                    Entry::Occupied(e) => {
                        let (a, b) = (root(&mut parent, i), root(&mut parent, *e.get()));
                        parent[a.max(b)] = a.min(b);
                    }
                }
            }
        }
    }
    let mut groups: Vec<Vec<usize>> = Vec::new();
    let mut slot = vec![usize::MAX; n];
    for i in 0..n {
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
    fn every_records_ids_count_for_find_and_group() {
        // a's second record lost the PMID conflict to its first; b shares the loser.
        let mut a = folder("a", Some("1"), None);
        a.records
            .push(folder("a", Some("2"), None).records.remove(0));
        let scan = Scan {
            folders: vec![a, folder("b", Some("2"), None)],
            problems: Vec::new(),
        };
        assert_eq!(group(&scan), vec![vec![0, 1]]);
        let q = PaperIds::canonical(Some("2"), None, None).unwrap();
        let got: Vec<&str> = find(&scan, &q).iter().map(|f| f.folder.as_str()).collect();
        assert_eq!(got, ["a", "b"]);
    }

    #[test]
    fn find_follows_a_chain_of_three_folders() {
        // a-b share a DOI, b-c share a PMCID: a query for c's PMCID finds all three.
        let mut a = folder("a", None, Some("10.1/x"));
        let mut b = folder("b", None, Some("10.1/x"));
        let mut c = folder("c", None, None);
        b.records[0].paper.ids.pmcid = Some("PMC5".into());
        c.records[0].paper.ids.pmcid = Some("PMC5".into());
        a.records[0].paper.ids.pmid = None;
        let other = folder("d", Some("9"), None);
        let scan = Scan {
            folders: vec![a, other, b, c],
            problems: Vec::new(),
        };
        let q = PaperIds::canonical(None, None, Some("PMC5")).unwrap();
        let got: Vec<&str> = find(&scan, &q).iter().map(|f| f.folder.as_str()).collect();
        assert_eq!(got, ["a", "b", "c"]);
    }

    #[test]
    fn a_records_that_is_a_file_is_an_unreadable_folder() {
        let base = std::env::temp_dir().join(format!("bmlib-scan-rf-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        std::fs::create_dir_all(base.join("papers/p")).unwrap();
        std::fs::write(base.join("papers/p/records"), "x").unwrap();
        std::fs::write(
            base.join("bmlib-library.json"),
            "{\"format\":\"bmlib-library\",\"version\":1}",
        )
        .unwrap();
        let scan = Library::open(&base).unwrap().scan().unwrap();
        assert!(matches!(
            scan.problems.as_slice(),
            [Problem::UnreadableFolder { folder, .. }] if folder == "p"
        ));
        let _ = std::fs::remove_dir_all(&base);
    }

    #[test]
    fn find_digest_refuses_a_malformed_digest() {
        let scan = Scan {
            folders: Vec::new(),
            problems: Vec::new(),
        };
        assert_eq!(find_digest(&scan, "ABC", None), None);
        assert_eq!(find_digest(&scan, &"A".repeat(64), None), None);
    }
}
