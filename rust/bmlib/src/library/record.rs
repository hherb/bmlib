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

//! An installation's JSON account of one paper: who wrote it, what the paper
//! is, and which source files it saved.

use serde::Serialize;
use serde_json::Value;

use super::ids::{Installation, PaperIds, PaperInfo, SourceKind};
use super::{DIGEST_PREFIX_HEX, RECORD_FORMAT, RECORD_VERSION};

/// One source file an installation saved in the paper's folder.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FileEntry {
    /// The SHA-256 of the bytes, 64 lowercase hex digits.
    pub sha256: String,
    /// What the file is.
    pub kind: SourceKind,
    /// Its name in the folder: `{kind}-{first 12 hex of sha256}.{extension}`.
    pub name: String,
    /// Where the bytes came from, such as a repository's name.
    pub origin: String,
    /// Its size in bytes.
    pub size: u64,
    /// When it was saved, as the writer formatted it.
    pub saved_at: String,
}

/// An installation's account of one paper.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Record {
    /// Who wrote the record.
    pub installation: Installation,
    /// What the installation knows of the paper.
    pub paper: PaperInfo,
    /// The files it saved.
    pub files: Vec<FileEntry>,
}

/// Why a record was not accepted.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RecordProblem {
    /// The text is not a valid record; the reason says why.
    Unreadable(String),
    /// A newer version of bmlib wrote it; this is its version.
    Newer(u32),
}

/// An integer as an `i64`. serde_json reads `-0` as the float `-0.0`; Python reads it as the
/// integer 0, so negative zero counts as 0 here and in `records.py`.
fn whole(v: &Value) -> Option<i64> {
    v.as_i64().or_else(|| {
        v.as_f64()
            .filter(|f| *f == 0.0 && f.is_sign_negative())
            .map(|_| 0)
    })
}

fn bad<T>(reason: impl Into<String>) -> Result<T, RecordProblem> {
    Err(RecordProblem::Unreadable(reason.into()))
}

fn string<'a>(obj: &'a Value, key: &str, what: &str) -> Result<&'a str, RecordProblem> {
    match obj.get(key) {
        Some(Value::String(s)) => Ok(s),
        _ => bad(format!("{what}.{key} must be a string")),
    }
}

/// A string, or null/absent as `None`; anything else is refused.
fn opt_string(obj: &Value, key: &str) -> Result<Option<String>, RecordProblem> {
    match obj.get(key) {
        None | Some(Value::Null) => Ok(None),
        Some(Value::String(s)) => Ok(Some(s.clone())),
        _ => bad(format!("paper.{key} must be a string or null")),
    }
}

fn parse_paper(paper: &Value) -> Result<PaperInfo, RecordProblem> {
    if !paper.is_object() {
        return bad("paper must be an object");
    }
    let (pmid, doi, pmcid) = (
        opt_string(paper, "pmid")?,
        opt_string(paper, "doi")?,
        opt_string(paper, "pmcid")?,
    );
    let ids = PaperIds::canonical(pmid.as_deref(), doi.as_deref(), pmcid.as_deref())
        .map_err(|e| RecordProblem::Unreadable(e.to_string()))?;
    if (&ids.pmid, &ids.doi, &ids.pmcid) != (&pmid, &doi, &pmcid) {
        return bad("paper identifiers are not canonical");
    }
    let year = match paper.get("year") {
        None | Some(Value::Null) => None,
        Some(v) => match whole(v).map(i32::try_from) {
            Some(Ok(y)) => Some(y),
            _ => return bad("paper.year must be an integer or null"),
        },
    };
    Ok(PaperInfo {
        ids,
        title: opt_string(paper, "title")?,
        year,
        first_author: opt_string(paper, "first_author")?,
    })
}

fn parse_file(file: &Value) -> Result<FileEntry, RecordProblem> {
    if !file.is_object() {
        return bad("a file entry must be an object");
    }
    let sha256 = string(file, "sha256", "file")?;
    if sha256.len() != 64
        || !sha256
            .bytes()
            .all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
    {
        return bad("file.sha256 must be 64 lowercase hex digits");
    }
    let kind = string(file, "kind", "file")?;
    let Some(kind) = SourceKind::parse(kind) else {
        return bad(format!("unknown file kind {kind:?}"));
    };
    let name = string(file, "name", "file")?;
    if name
        != format!(
            "{}-{}.{}",
            kind.as_str(),
            &sha256[..DIGEST_PREFIX_HEX],
            kind.extension()
        )
    {
        return bad(format!(
            "file name {name:?} does not match its kind and digest"
        ));
    }
    let Some(size) = file.get("size").and_then(|v| {
        v.as_u64()
            .or_else(|| whole(v).and_then(|n| u64::try_from(n).ok()))
    }) else {
        return bad("file.size must be a non-negative integer");
    };
    Ok(FileEntry {
        sha256: sha256.to_owned(),
        kind,
        name: name.to_owned(),
        origin: string(file, "origin", "file")?.to_owned(),
        size,
        saved_at: string(file, "saved_at", "file")?.to_owned(),
    })
}

/// Reads a record from its JSON text.
///
/// Unknown fields are ignored. The format is checked first, then the version
/// (a newer one is [`RecordProblem::Newer`] whatever else it holds), then the
/// installation, identifiers and files.
///
/// # Errors
/// [`RecordProblem::Unreadable`] with the reason, or [`RecordProblem::Newer`].
pub fn parse_record(text: &str) -> Result<Record, RecordProblem> {
    let root: Value = serde_json::from_str(text)
        .map_err(|e| RecordProblem::Unreadable(format!("not JSON: {e}")))?;
    if root.get("format").and_then(Value::as_str) != Some(RECORD_FORMAT) {
        return bad(format!("format is not {RECORD_FORMAT:?}"));
    }
    let Some(version) = root
        .get("version")
        .and_then(Value::as_u64)
        .and_then(|v| u32::try_from(v).ok())
        .filter(|v| *v >= 1)
    else {
        return bad("version must be a positive integer");
    };
    if version > RECORD_VERSION {
        return Err(RecordProblem::Newer(version));
    }
    let Some(inst) = root.get("installation").filter(|v| v.is_object()) else {
        return bad("installation must be an object");
    };
    let installation = Installation::new(
        string(inst, "app", "installation")?,
        string(inst, "id", "installation")?,
        string(inst, "label", "installation")?,
    )
    .map_err(|e| RecordProblem::Unreadable(e.to_string()))?;
    let paper = parse_paper(root.get("paper").unwrap_or(&Value::Null))?;
    let Some(Value::Array(raw)) = root.get("files") else {
        return bad("files must be a list");
    };
    let mut files: Vec<FileEntry> = Vec::with_capacity(raw.len());
    for f in raw {
        let entry = parse_file(f)?;
        if files.iter().any(|o| o.sha256 == entry.sha256) {
            return bad(format!("file {} is listed twice", entry.sha256));
        }
        files.push(entry);
    }
    Ok(Record {
        installation,
        paper,
        files,
    })
}

// Field order here is the order in the written JSON.
#[derive(Serialize)]
struct OutInstallation<'a> {
    app: &'a str,
    id: &'a str,
    label: &'a str,
}

#[derive(Serialize)]
struct OutPaper<'a> {
    pmid: Option<&'a str>,
    doi: Option<&'a str>,
    pmcid: Option<&'a str>,
    title: Option<&'a str>,
    year: Option<i32>,
    first_author: Option<&'a str>,
}

#[derive(Serialize)]
struct OutFile<'a> {
    sha256: &'a str,
    kind: &'static str,
    name: &'a str,
    origin: &'a str,
    size: u64,
    saved_at: &'a str,
}

#[derive(Serialize)]
struct OutRecord<'a> {
    format: &'static str,
    version: u32,
    installation: OutInstallation<'a>,
    paper: OutPaper<'a>,
    files: Vec<OutFile<'a>>,
}

/// The record as JSON text: two-space indent, fixed key order, absent
/// optionals as `null`, a trailing newline. Python's `render_record` writes
/// the same bytes.
#[must_use]
pub fn render_record(record: &Record) -> String {
    let p = &record.paper;
    let out = OutRecord {
        format: RECORD_FORMAT,
        version: RECORD_VERSION,
        installation: OutInstallation {
            app: &record.installation.app,
            id: &record.installation.id,
            label: &record.installation.label,
        },
        paper: OutPaper {
            pmid: p.ids.pmid.as_deref(),
            doi: p.ids.doi.as_deref(),
            pmcid: p.ids.pmcid.as_deref(),
            title: p.title.as_deref(),
            year: p.year,
            first_author: p.first_author.as_deref(),
        },
        files: record
            .files
            .iter()
            .map(|f| OutFile {
                sha256: &f.sha256,
                kind: f.kind.as_str(),
                name: &f.name,
                origin: &f.origin,
                size: f.size,
                saved_at: &f.saved_at,
            })
            .collect(),
    };
    // Plain structs of strings and numbers cannot fail to serialise.
    let mut text = serde_json::to_string_pretty(&out).expect("record serialises");
    text.push('\n');
    text
}
