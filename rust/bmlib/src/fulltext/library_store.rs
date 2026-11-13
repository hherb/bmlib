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

//! The shared library as the full-text service's second store: the request's
//! identifiers as library identifiers, and the store itself.
//!
//! The library is keyed by the request's `pmid`, `doi` and `pmc_id`. Each is
//! made canonical on its own, so one bad value costs only that identifier, not
//! the whole key.

use std::path::PathBuf;

use crate::fulltext::JATSArticle;
use crate::library::{
    component, find, FileRef, Installation, Library, PaperIds, PaperInfo, Scan, SourceKind,
    PAPERS_DIR,
};

/// The request's identifiers as the library's canonical ones, and the names of
/// the fields that were refused.
///
/// Each of `pmid`, `doi` and `pmc_id` goes alone through
/// [`PaperIds::canonical`] (a blank one is simply absent). A field it refuses
/// is left out of the result and its name (`"pmid"`, `"doi"` or `"pmc_id"`,
/// the request's parameter names) is returned in the second part, in that
/// order. This function only reports; the caller logs the warning.
#[must_use]
pub fn library_ids(
    pmid: &str,
    doi: Option<&str>,
    pmc_id: Option<&str>,
) -> (PaperIds, Vec<&'static str>) {
    let mut ids = PaperIds::default();
    let mut dropped = Vec::new();
    match PaperIds::canonical(Some(pmid), None, None) {
        Ok(one) => ids.pmid = one.pmid,
        Err(_) => dropped.push("pmid"),
    }
    match PaperIds::canonical(None, doi, None) {
        Ok(one) => ids.doi = one.doi,
        Err(_) => dropped.push("doi"),
    }
    match PaperIds::canonical(None, None, pmc_id) {
        Ok(one) => ids.pmcid = one.pmcid,
        Err(_) => dropped.push("pmc_id"),
    }
    (ids, dropped)
}

/// The shared library and the installation that writes to it: what the
/// full-text service needs to read and save papers there.
pub struct LibraryStore {
    /// The library folder.
    pub library: Library,
    /// The installation whose record the service's saves go in.
    pub who: Installation,
}

/// A source file read from the library, its bytes already checked against its digest.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Held {
    /// Where the file is in the library.
    pub file: FileRef,
    /// Its bytes.
    pub bytes: Vec<u8>,
}

/// The paper's identifiers as `pmid=1 doi=x pmcid=PMC1` (only those present), or
/// `no identifiers`.
fn describe_ids(ids: &PaperIds) -> String {
    let parts: Vec<String> = [
        ("pmid", &ids.pmid),
        ("doi", &ids.doi),
        ("pmcid", &ids.pmcid),
    ]
    .iter()
    .filter_map(|(name, v)| v.as_ref().map(|v| format!("{name}={v}")))
    .collect();
    if parts.is_empty() {
        "no identifiers".to_owned()
    } else {
        parts.join(" ")
    }
}

/// The shape of a well-formed `saved_at`: `D` is an ASCII digit, anything else
/// must match exactly (`2026-11-14T09:30:00Z`).
const SAVED_AT_SHAPE: &str = "DDDD-DD-DDTDD:DD:DDZ";

/// `saved_at` as a sort key: itself when it has exactly the shape
/// [`SAVED_AT_SHAPE`] (fixed-width UTC, so string order is time order), else `""`,
/// which sorts below every well-formed one. A record is free text, so a foreign
/// or tampered `saved_at` must not shadow the real newest file.
fn saved_at_key(saved_at: &str) -> &str {
    let ok = saved_at.len() == SAVED_AT_SHAPE.len()
        && saved_at
            .bytes()
            .zip(SAVED_AT_SHAPE.bytes())
            .all(|(c, want)| {
                if want == b'D' {
                    c.is_ascii_digit()
                } else {
                    c == want
                }
            });
    if ok {
        saved_at
    } else {
        ""
    }
}

/// The earlier of two [`saved_at_key`] values; `""` (malformed) only when both are.
fn earliest<'a>(a: &'a str, b: &'a str) -> &'a str {
    match (a.is_empty(), b.is_empty()) {
        (true, _) => b,
        (_, true) => a,
        _ => a.min(b),
    }
}

/// The first identifier on which `theirs` contradicts `ours`, as the request's
/// field name (`"pmid"`, `"doi"` or `"pmc_id"`), their value and ours; `None`
/// when they agree. A field absent on either side agrees. The one rule for a
/// library JATS's own identifiers and for a record's.
pub(crate) fn contradiction<'a>(
    theirs: &'a PaperIds,
    ours: &'a PaperIds,
) -> Option<(&'static str, &'a str, &'a str)> {
    [
        ("pmid", &theirs.pmid, &ours.pmid),
        ("doi", &theirs.doi, &ours.doi),
        ("pmc_id", &theirs.pmcid, &ours.pmcid),
    ]
    .into_iter()
    .find_map(|(field, a, b)| match (a, b) {
        (Some(a), Some(b)) if a != b => Some((field, a.as_str(), b.as_str())),
        _ => None,
    })
}

/// The one wording of every library failure: the paper's identifiers, the file
/// (`folder/name`) when there is one, and what the library said. Python's
/// `_failure` words it the same.
fn failure(ids: &PaperIds, file: Option<(&str, &str)>, err: &dyn std::fmt::Display) -> String {
    let at = file.map_or_else(String::new, |(folder, name)| {
        format!(" file {folder}/{name}")
    });
    format!("library, paper {}{at}: {err}", describe_ids(ids))
}

impl LibraryStore {
    /// A store over `library`, saving as `who`.
    #[must_use]
    pub fn new(library: Library, who: Installation) -> Self {
        Self { library, who }
    }

    /// The paper's newest source file of `kind`, read and digest-checked: one
    /// scan of the library, then [`LibraryStore::find_source_in`].
    ///
    /// # Errors
    ///
    /// A message naming the paper's identifiers, the file and the library's error
    /// when the scan or the read fails.
    pub fn find_source(&self, ids: &PaperIds, kind: SourceKind) -> Result<Option<Held>, String> {
        self.find_source_in(&self.scan(ids)?, ids, kind)
    }

    /// The library scanned once, for [`LibraryStore::find_source_in`] and
    /// [`LibraryStore::note_use_in`].
    ///
    /// # Errors
    ///
    /// A message naming `ids` and the library's error when the scan fails.
    pub fn scan(&self, ids: &PaperIds) -> Result<Scan, String> {
        self.library.scan().map_err(|e| failure(ids, None, &e))
    }

    /// The paper's newest source file of `kind` in `scan`, read and
    /// digest-checked.
    ///
    /// Looks in the paper's whole linked group of folders (`find`, not only an
    /// exact-identifier folder) for files of `kind` that are on disk and that
    /// at least one record lists under identifiers not contradicting `ids`
    /// (field by field; a field absent on either side agrees), so a file only
    /// another paper's records list is never served. Each such file ranks by
    /// the earliest `saved_at` among the records listing it, since noting a use
    /// stamps the time of the note; the latest wins, and ties go to the lower
    /// digest. A `saved_at` not of the exact form `YYYY-MM-DDTHH:MM:SSZ` counts
    /// only when no record has a well-formed one, and then sorts below every
    /// well-formed one; fixed-width UTC compares in time order as a string. If
    /// the newest file cannot be read, that is an error: a tampered file is
    /// reported, not skipped for an older one.
    ///
    /// # Errors
    ///
    /// A message naming the paper's identifiers, the file and the library's error
    /// when the read fails.
    pub fn find_source_in(
        &self,
        scan: &Scan,
        ids: &PaperIds,
        kind: SourceKind,
    ) -> Result<Option<Held>, String> {
        // Per digest: its earliest well-formed `saved_at` (`""` when none), its
        // file, and whether a record not contradicting `ids` lists it.
        let mut seen: Vec<(&str, FileRef, bool)> = Vec::new();
        for folder in find(scan, ids) {
            for record in &folder.records {
                let agrees = contradiction(&record.paper.ids, ids).is_none();
                for entry in &record.files {
                    if entry.kind != kind || !folder.files_on_disk.contains(&entry.name) {
                        continue;
                    }
                    let at = saved_at_key(&entry.saved_at);
                    match seen.iter_mut().find(|(_, f, _)| f.sha256 == entry.sha256) {
                        Some(known) => {
                            known.0 = earliest(known.0, at);
                            known.2 |= agrees;
                        }
                        None => seen.push((
                            at,
                            FileRef {
                                folder: folder.folder.clone(),
                                name: entry.name.clone(),
                                sha256: entry.sha256.clone(),
                                kind,
                            },
                            agrees,
                        )),
                    }
                }
            }
        }
        let mut best: Option<(&str, FileRef)> = None;
        for (at, file, agrees) in seen {
            // Later `saved_at` wins; on a tie the lower digest does.
            let better = best
                .as_ref()
                .is_none_or(|(was, f)| (at, f.sha256.as_str()) > (*was, file.sha256.as_str()));
            if agrees && better {
                best = Some((at, file));
            }
        }
        let Some((_, file)) = best else {
            return Ok(None);
        };
        let bytes = self
            .library
            .read(&file)
            .map_err(|e| failure(ids, Some((&file.folder, &file.name)), &e))?;
        Ok(Some(Held { file, bytes }))
    }

    /// Save `bytes` for `paper` in the library, as this installation.
    ///
    /// # Errors
    ///
    /// A message naming the paper and the library's error.
    pub fn keep(
        &self,
        paper: &PaperInfo,
        bytes: &[u8],
        kind: SourceKind,
        origin: &str,
    ) -> Result<FileRef, String> {
        self.library
            .save(&self.who, paper, bytes, kind, origin)
            .map_err(|e| failure(&paper.ids, None, &e))
    }

    /// Note in this installation's record that it uses `file`: one scan of the
    /// library, then [`LibraryStore::note_use_in`].
    ///
    /// # Errors
    ///
    /// A message naming the paper, the file and the library's error.
    pub fn note_use(&self, paper: &PaperInfo, file: &FileRef) -> Result<bool, String> {
        self.note_use_in(&self.scan(&paper.ids)?, paper, file)
    }

    /// Note in this installation's record that it uses `file`, unless that record
    /// (in the file's folder) already lists the digest in `scan`, in which case
    /// nothing is written. Returns whether a use was written. A `scan` older
    /// than the record only costs a write that changes nothing.
    ///
    /// # Errors
    ///
    /// A message naming the paper, the file and the library's error.
    pub fn note_use_in(
        &self,
        scan: &Scan,
        paper: &PaperInfo,
        file: &FileRef,
    ) -> Result<bool, String> {
        let already = scan
            .folders
            .iter()
            .filter(|f| f.folder == file.folder)
            .any(|f| {
                f.records.iter().any(|r| {
                    r.installation.app == self.who.app
                        && r.installation.id == self.who.id
                        && r.files.iter().any(|e| e.sha256 == file.sha256)
                })
            });
        if already {
            return Ok(false);
        }
        self.library
            .record_use(&self.who, paper, file)
            .map_err(|e| failure(&paper.ids, Some((&file.folder, &file.name)), &e))?;
        Ok(true)
    }

    /// The path of `file` inside the library: `root/papers/<folder>/<name>`.
    ///
    /// # Errors
    ///
    /// A message when the folder or the name is not one safe path component.
    pub fn path_of(&self, file: &FileRef) -> Result<PathBuf, String> {
        let at = Some((file.folder.as_str(), file.name.as_str()));
        let folder = component(&file.folder).map_err(|e| failure(&PaperIds::default(), at, &e))?;
        let name = component(&file.name).map_err(|e| failure(&PaperIds::default(), at, &e))?;
        Ok(self.library.root().join(PAPERS_DIR).join(folder).join(name))
    }
}

/// What the library needs to know of a paper: `ids` plus, from the JATS when there
/// is one, the title, the year and the first author.
///
/// The year is the JATS `year` when it is all digits, else `None`. The first
/// author is the first contributor's surname, or their collaboration name when
/// there is no surname. The JATS's own identifiers fill the ones `ids` lacks; each
/// goes through [`library_ids`], so a value the library refuses is left out.
#[must_use]
pub fn paper_info(ids: PaperIds, article: Option<&JATSArticle>) -> PaperInfo {
    let Some(a) = article else {
        return PaperInfo {
            ids,
            ..PaperInfo::default()
        };
    };
    let (own, _) = library_ids(&a.pmid, Some(&a.doi), Some(&a.pmc_id));
    let year = a.year.trim();
    let non_empty = |s: &str| (!s.trim().is_empty()).then(|| s.to_owned());
    PaperInfo {
        ids: ids.merged(&own).0,
        title: non_empty(&a.title),
        year: if !year.is_empty() && year.bytes().all(|b| b.is_ascii_digit()) {
            year.parse().ok()
        } else {
            None
        },
        first_author: a
            .authors
            .first()
            .and_then(|au| non_empty(&au.surname).or_else(|| non_empty(&au.collab))),
    }
}
