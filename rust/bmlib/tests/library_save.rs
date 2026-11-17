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

//! Saving, releasing and removing: the guarantees that need more than a
//! fixture tree (Python's `tests/test_library_save.py` is the twin).

mod common;

use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use bmlib::library::{
    find, folder_name, parse_record, sha256_hex, FileRef, Installation, Library, LibraryError,
    PaperIds, PaperInfo, SourceKind, CORRUPT_SUFFIX,
};

const BYTES: &[u8] = b"%PDF a paper";

fn paper() -> PaperInfo {
    PaperInfo {
        ids: PaperIds::canonical(Some("111"), None, None).unwrap(),
        title: Some("Metformin".into()),
        year: Some(2024),
        first_author: Some("Smith".into()),
    }
}

fn titled(title: &str) -> PaperInfo {
    PaperInfo {
        title: Some(title.into()),
        ..PaperInfo::default()
    }
}

fn me() -> Installation {
    Installation::new("studyanalyzer", "aaaa", "Mac").unwrap()
}

fn you() -> Installation {
    Installation::new("medicalfactchecker", "bbbb", "Mac").unwrap()
}

fn scratch(tag: &str) -> (PathBuf, Library) {
    let dir = std::env::temp_dir().join(format!("bmlib-save-{tag}-{}", std::process::id()));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();
    let lib = Library::open(&dir).unwrap();
    (dir, lib)
}

/// Every path under `dir` with its bytes (None for a folder), sorted.
fn tree(dir: &Path) -> Vec<(PathBuf, Option<Vec<u8>>)> {
    fn walk(dir: &Path, out: &mut Vec<(PathBuf, Option<Vec<u8>>)>) {
        for entry in fs::read_dir(dir).unwrap() {
            let path = entry.unwrap().path();
            if path.is_dir() {
                out.push((path.clone(), None));
                walk(&path, out);
            } else {
                let bytes = fs::read(&path).unwrap();
                out.push((path, Some(bytes)));
            }
        }
    }
    let mut out = Vec::new();
    walk(dir, &mut out);
    out.sort();
    out
}

fn record_path(dir: &Path, who: &Installation, folder: &str) -> PathBuf {
    dir.join("papers")
        .join(folder)
        .join("records")
        .join(who.record_file_name())
}

fn save(lib: &Library, who: &Installation, info: &PaperInfo, bytes: &[u8]) -> FileRef {
    lib.save(who, info, bytes, SourceKind::Pdf, "t").unwrap()
}

#[test]
fn save_same_bytes_twice_is_one_file() {
    let (dir, lib) = scratch("twice");
    let lib = Arc::new(lib);
    let handles: Vec<_> = [me(), you()]
        .into_iter()
        .map(|who| {
            let lib = Arc::clone(&lib);
            std::thread::spawn(move || {
                for _ in 0..20 {
                    lib.save(&who, &paper(), BYTES, SourceKind::Pdf, "thread")
                        .expect("a concurrent save never errors");
                }
            })
        })
        .collect();
    for h in handles {
        h.join().unwrap();
    }
    let scan = lib.scan().unwrap();
    assert!(scan.problems.is_empty(), "{:?}", scan.problems);
    assert_eq!(scan.folders.len(), 1);
    let folder = &scan.folders[0];
    assert_eq!(folder.files_on_disk.len(), 1);
    let mut apps: Vec<&str> = folder
        .records
        .iter()
        .map(|r| r.installation.app.as_str())
        .collect();
    apps.sort_unstable();
    assert_eq!(apps, ["medicalfactchecker", "studyanalyzer"]);
    assert!(folder.records.iter().all(|r| r.files.len() == 1));
    fs::remove_dir_all(dir).unwrap();
}

#[test]
fn save_never_overwrites_other_bytes() {
    let (dir, lib) = scratch("clash");
    let first = save(&lib, &me(), &paper(), BYTES);
    let target = dir.join("papers").join(&first.folder).join(&first.name);
    fs::write(&target, b"planted").unwrap();
    let err = lib
        .save(&you(), &paper(), BYTES, SourceKind::Pdf, "t")
        .unwrap_err();
    // #755: the clash names the folder as `FileRef` does.
    assert!(
        matches!(&err, LibraryError::FileClash { folder, name }
            if *folder == first.folder && *name == first.name),
        "{err}"
    );
    assert_eq!(fs::read(&target).unwrap(), b"planted");
    assert!(!record_path(&dir, &you(), &first.folder).exists());
    fs::remove_dir_all(dir).unwrap();
}

#[test]
fn newer_own_record_is_not_downgraded() {
    let (dir, lib) = scratch("newer");
    let first = save(&lib, &me(), &paper(), BYTES);
    let path = record_path(&dir, &me(), &first.folder);
    let newer = fs::read_to_string(&path)
        .unwrap()
        .replace("\"version\": 1", "\"version\": 2");
    fs::write(&path, &newer).unwrap();
    let before = tree(&dir);
    let err = lib
        .save(&me(), &paper(), b"%PDF other", SourceKind::Pdf, "t")
        .unwrap_err();
    assert!(
        matches!(err, LibraryError::NewerRecord { version: 2, .. }),
        "{err}"
    );
    assert_eq!(fs::read_to_string(&path).unwrap(), newer);
    assert_eq!(tree(&dir), before); // no new folder, nothing else written
    fs::remove_dir_all(dir).unwrap();
}

#[test]
fn corrupt_own_record_is_kept_aside() {
    let (dir, lib) = scratch("corrupt");
    let first = save(&lib, &me(), &paper(), BYTES);
    let path = record_path(&dir, &me(), &first.folder);
    fs::write(&path, "{ not json").unwrap();
    common::log_capture::start();
    save(&lib, &me(), &paper(), BYTES);
    let aside = PathBuf::from(format!("{}{CORRUPT_SUFFIX}", path.display()));
    assert_eq!(fs::read_to_string(&aside).unwrap(), "{ not json");
    let rec = parse_record(&fs::read_to_string(&path).unwrap()).unwrap();
    assert_eq!(rec.files.len(), 1);
    let logged = common::log_capture::take("bmlib.library.save");
    assert_eq!(logged.len(), 1, "{logged:?}");
    assert_eq!(logged[0].level, log::Level::Warn);
    fs::remove_dir_all(dir).unwrap();
}

#[test]
fn a_second_corrupt_record_does_not_replace_the_first_kept_aside() {
    let (dir, lib) = scratch("corrupt2");
    let first = save(&lib, &me(), &paper(), BYTES);
    let path = record_path(&dir, &me(), &first.folder);
    for text in ["bad one", "bad two"] {
        fs::write(&path, text).unwrap();
        save(&lib, &me(), &paper(), BYTES);
    }
    let mut kept: Vec<String> = fs::read_dir(path.parent().unwrap())
        .unwrap()
        .map(|e| e.unwrap().path())
        .filter(|p| p.to_string_lossy().contains(CORRUPT_SUFFIX))
        .map(|p| fs::read_to_string(p).unwrap())
        .collect();
    kept.sort();
    assert_eq!(kept, ["bad one", "bad two"]);
    fs::remove_dir_all(dir).unwrap();
}

#[test]
fn an_identifier_conflict_is_logged_and_the_record_keeps_its_own() {
    let (dir, lib) = scratch("conflict");
    let ids = |doi| PaperInfo {
        ids: PaperIds::canonical(Some("111"), Some(doi), None).unwrap(),
        ..PaperInfo::default()
    };
    let first = save(&lib, &me(), &paper(), BYTES);
    lib.save(&me(), &ids("10.1/a"), b"two", SourceKind::Text, "t")
        .unwrap();
    common::log_capture::start();
    lib.save(&me(), &ids("10.1/b"), b"three", SourceKind::Text, "t")
        .unwrap();
    let rec = parse_record(&fs::read_to_string(record_path(&dir, &me(), &first.folder)).unwrap())
        .unwrap();
    assert_eq!(rec.paper.ids.doi.as_deref(), Some("10.1/a"));
    let logged = common::log_capture::take("bmlib.library.save");
    assert!(
        logged.iter().any(|r| r.message.contains("doi")),
        "{logged:?}"
    );
    fs::remove_dir_all(dir).unwrap();
}

#[test]
fn a_folder_without_a_readable_record_is_used_not_duplicated() {
    // Another writer has claimed the folder but not yet written its record.
    let (dir, lib) = scratch("claimed");
    let claimed = folder_name(&paper()).to_uppercase();
    fs::create_dir(dir.join("papers").join(&claimed)).unwrap();
    let got = save(&lib, &me(), &paper(), BYTES);
    assert_eq!(got.folder, claimed);
    assert_eq!(fs::read_dir(dir.join("papers")).unwrap().count(), 1);
    fs::remove_dir_all(dir).unwrap();
}

#[test]
fn a_taken_name_with_other_identifiers_takes_the_next_suffix() {
    let (dir, lib) = scratch("suffix");
    let put = |n: &str| {
        lib.save(&me(), &titled("T"), n.as_bytes(), SourceKind::Text, "t")
            .unwrap()
    };
    let (a, b, c) = (put("1"), put("2"), put("3"));
    assert!(!a.folder.ends_with(')') || a.folder.ends_with("(no identifier)"));
    assert!(b.folder.ends_with("(no identifier) (2)"), "{}", b.folder);
    assert!(c.folder.ends_with("(no identifier) (3)"), "{}", c.folder);
    fs::remove_dir_all(dir).unwrap();
}

#[test]
fn release_keeps_the_file_and_the_other_records() {
    let (dir, lib) = scratch("release");
    let first = save(&lib, &me(), &paper(), BYTES);
    save(&lib, &you(), &paper(), BYTES);
    lib.release(&me(), &first).unwrap();
    assert_eq!(lib.read(&first).unwrap(), BYTES);
    let read = |who: &Installation| {
        parse_record(&fs::read_to_string(record_path(&dir, who, &first.folder)).unwrap()).unwrap()
    };
    assert_eq!((read(&me()).files.len(), read(&you()).files.len()), (0, 1));
    fs::remove_dir_all(dir).unwrap();
}

#[test]
fn the_manual_clear_refuses_paths_in_names() {
    let (dir, lib) = scratch("badname");
    let first = save(&lib, &me(), &paper(), BYTES);
    let bad_name = |r: Result<(), LibraryError>| {
        assert!(matches!(r, Err(LibraryError::BadName { .. })), "{r:?}");
    };
    for bad in ["../x", "a/b", ""] {
        bad_name(lib.remove_folder(bad));
        for file in [
            FileRef {
                folder: bad.into(),
                ..first.clone()
            },
            FileRef {
                name: bad.into(),
                ..first.clone()
            },
        ] {
            bad_name(lib.remove_file(&file));
            bad_name(lib.release(&me(), &file));
        }
    }
    assert_eq!(lib.read(&first).unwrap(), BYTES);
    fs::remove_dir_all(dir).unwrap();
}

#[test]
fn removing_what_is_not_there_is_missing() {
    let (dir, lib) = scratch("absent");
    let first = save(&lib, &me(), &paper(), BYTES);
    lib.remove_file(&first).unwrap();
    assert!(matches!(
        lib.remove_file(&first),
        Err(LibraryError::Missing { .. })
    ));
    lib.remove_folder(&first.folder).unwrap();
    assert!(matches!(
        lib.remove_folder(&first.folder),
        Err(LibraryError::Missing { .. })
    ));
    assert!(lib.scan().unwrap().folders.is_empty());
    fs::remove_dir_all(dir).unwrap();
}

#[test]
fn remove_folder_of_a_regular_file_is_missing() {
    let (dir, lib) = scratch("stray");
    fs::write(dir.join("papers").join("stray"), "x").unwrap();
    assert!(matches!(
        lib.remove_folder("stray"),
        Err(LibraryError::Missing { .. })
    ));
    assert!(dir.join("papers").join("stray").exists());
    fs::remove_dir_all(dir).unwrap();
}

#[test]
fn release_of_an_unlisted_file_or_without_a_record_is_fine() {
    let (dir, lib) = scratch("unlisted");
    let first = save(&lib, &me(), &paper(), BYTES);
    lib.release(&you(), &first).unwrap();
    lib.release(&me(), &first).unwrap();
    lib.release(&me(), &first).unwrap();
    assert_eq!(lib.read(&first).unwrap(), BYTES);
    fs::remove_dir_all(dir).unwrap();
}

#[test]
fn a_folder_with_only_a_newer_record_fails_the_claim_and_writes_nothing() {
    let (dir, lib) = scratch("newer-only");
    let first = save(&lib, &you(), &paper(), BYTES);
    let path = record_path(&dir, &you(), &first.folder);
    let text = fs::read_to_string(&path).unwrap();
    fs::write(&path, text.replace("\"version\": 1", "\"version\": 2")).unwrap();
    let before = tree(&dir);
    let err = lib
        .save(&me(), &paper(), b"other", SourceKind::Pdf, "t")
        .unwrap_err();
    assert!(
        matches!(&err, LibraryError::NewerRecord { folder, .. } if *folder == first.folder),
        "{err}"
    );
    assert_eq!(tree(&dir), before);
    fs::remove_dir_all(dir).unwrap();
}

#[test]
fn two_threads_of_one_installation_both_land_in_the_record() {
    let (dir, lib) = scratch("one-installation");
    let lib = Arc::new(lib);
    for i in 0..25_usize {
        let barrier = Arc::new(std::sync::Barrier::new(2));
        let handles: Vec<_> = [(SourceKind::Pdf, "pdf"), (SourceKind::Text, "text")]
            .into_iter()
            .map(|(kind, label)| {
                let (lib, barrier) = (Arc::clone(&lib), Arc::clone(&barrier));
                std::thread::spawn(move || {
                    barrier.wait();
                    lib.save(
                        &me(),
                        &paper(),
                        format!("{label} {i}").as_bytes(),
                        kind,
                        "t",
                    )
                    .unwrap();
                })
            })
            .collect();
        for h in handles {
            h.join().unwrap();
        }
        let scan = lib.scan().unwrap();
        assert_eq!(scan.folders.len(), 1);
        assert_eq!(scan.folders[0].records.len(), 1);
        assert_eq!(
            scan.folders[0].records[0].files.len(),
            2 * (i + 1),
            "round {i}"
        );
    }
    fs::remove_dir_all(dir).unwrap();
}

#[test]
fn the_saved_ref_carries_the_full_digest() {
    let (dir, lib) = scratch("ref");
    let got = save(&lib, &me(), &paper(), BYTES);
    assert_eq!(got.sha256, sha256_hex(BYTES));
    assert_eq!(got.name, format!("pdf-{}.pdf", &got.sha256[..12]));
    fs::remove_dir_all(dir).unwrap();
}

/// Skipped when the process ignores directory permissions (root): there is no
/// uid without a new dependency, so this probes by writing into the folder.
#[cfg(unix)]
#[test]
fn read_only_library_says_so() {
    use std::os::unix::fs::PermissionsExt;
    let (dir, lib) = scratch("readonly");
    let papers = dir.join("papers");
    fs::set_permissions(&papers, fs::Permissions::from_mode(0o555)).unwrap();
    let root_like = fs::write(papers.join("probe"), b"").is_ok();
    let got = lib.save(&me(), &paper(), BYTES, SourceKind::Pdf, "t");
    fs::set_permissions(&papers, fs::Permissions::from_mode(0o755)).unwrap();
    if !root_like {
        assert!(matches!(got, Err(LibraryError::ReadOnly { .. })), "{got:?}");
        assert_eq!(fs::read_dir(&papers).unwrap().count(), 0);
    }
    fs::remove_dir_all(dir).unwrap();
}

#[test]
fn save_and_release_refuse_unchecked_fields_and_write_nothing() {
    let (dir, lib) = scratch("unchecked");
    let before = tree(&dir);
    let not_canonical = PaperInfo {
        ids: PaperIds {
            doi: Some("10.1/AbC".into()),
            ..PaperIds::default()
        },
        ..PaperInfo::default()
    };
    let err = lib
        .save(&me(), &not_canonical, BYTES, SourceKind::Pdf, "t")
        .unwrap_err();
    assert!(
        matches!(&err, LibraryError::BadIdentifier { field: "doi", value } if value == "10.1/AbC"),
        "{err}"
    );
    // Fields are public, so an installation can be built without `new`; its
    // record name would climb out of `papers/`.
    let escaped = Installation {
        app: "../../../escaped".into(),
        id: "x".into(),
        label: "x".into(),
    };
    let err = lib
        .save(&escaped, &paper(), BYTES, SourceKind::Pdf, "t")
        .unwrap_err();
    assert!(
        matches!(err, LibraryError::BadInstallation { field: "app", .. }),
        "{err}"
    );
    assert_eq!(tree(&dir), before);
    let first = save(&lib, &me(), &paper(), BYTES);
    let saved = tree(&dir);
    let err = lib.release(&escaped, &first).unwrap_err();
    assert!(matches!(err, LibraryError::BadInstallation { .. }), "{err}");
    assert_eq!(tree(&dir), saved);
    fs::remove_dir_all(dir).unwrap();
}

#[test]
fn an_evicted_own_record_is_not_downloaded_and_nothing_is_written() {
    // iCloud's Optimize Storage left only `.<record>.icloud`: a fresh record
    // would replace the real one when it comes back, losing its entries.
    let (dir, lib) = scratch("evicted");
    let first = save(&lib, &me(), &paper(), BYTES);
    let path = record_path(&dir, &me(), &first.folder);
    let placeholder = path.with_file_name(format!(".{}.icloud", me().record_file_name()));
    fs::rename(&path, &placeholder).unwrap();
    let before = tree(&dir);
    let err = lib
        .save(&me(), &paper(), b"%PDF other", SourceKind::Pdf, "t")
        .unwrap_err();
    assert!(
        matches!(&err, LibraryError::NotDownloaded { name, .. } if *name == me().record_file_name()),
        "{err}"
    );
    let err = lib.release(&me(), &first).unwrap_err();
    assert!(matches!(err, LibraryError::NotDownloaded { .. }), "{err}");
    assert_eq!(tree(&dir), before);
    fs::remove_dir_all(dir).unwrap();
}

#[test]
fn record_use_checks_its_inputs_and_the_file() {
    let (dir, lib) = scratch("record-use");
    let theirs = save(&lib, &you(), &paper(), BYTES);
    let before = tree(&dir);
    let unchecked = Installation {
        app: "../x".into(),
        ..me()
    };
    let err = lib.record_use(&unchecked, &paper(), &theirs).unwrap_err();
    assert!(matches!(err, LibraryError::BadInstallation { .. }), "{err}");
    let mut upper = paper();
    upper.ids.doi = Some("10.1/X".into());
    let err = lib.record_use(&me(), &upper, &theirs).unwrap_err();
    assert!(
        matches!(err, LibraryError::BadIdentifier { field: "doi", .. }),
        "{err}"
    );
    // A name that is not the library's name for those bytes is refused.
    let renamed = FileRef {
        name: "paper.pdf".into(),
        ..theirs.clone()
    };
    fs::copy(
        dir.join("papers").join(&theirs.folder).join(&theirs.name),
        dir.join("papers").join(&theirs.folder).join("paper.pdf"),
    )
    .unwrap();
    let err = lib.record_use(&me(), &paper(), &renamed).unwrap_err();
    assert!(matches!(err, LibraryError::BadName { .. }), "{err}");
    fs::remove_file(dir.join("papers").join(&theirs.folder).join("paper.pdf")).unwrap();
    assert_eq!(tree(&dir), before);
    lib.remove_file(&theirs).unwrap();
    let err = lib.record_use(&me(), &paper(), &theirs).unwrap_err();
    assert!(matches!(err, LibraryError::Missing { .. }), "{err}");
    assert!(!record_path(&dir, &me(), &theirs.folder).exists());
    fs::remove_dir_all(dir).unwrap();
}

/// Run `body` with `dir` listable but not searchable, so every lstat of a name
/// in it fails; `None` (skip) when the process ignores permissions (root).
#[cfg(unix)]
fn unsearchable<T>(dir: &Path, body: impl FnOnce() -> T) -> Option<T> {
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(dir, fs::Permissions::from_mode(0o600)).unwrap();
    let first = fs::read_dir(dir).unwrap().next().unwrap().unwrap().path();
    let root_like = fs::symlink_metadata(first).is_ok();
    let got = (!root_like).then(body);
    fs::set_permissions(dir, fs::Permissions::from_mode(0o755)).unwrap();
    got
}

#[cfg(unix)]
#[test]
fn a_folder_that_cannot_be_examined_is_reported_not_read_as_empty() {
    let (dir, lib) = scratch("unexamined-records");
    let first = save(&lib, &me(), &paper(), BYTES);
    if let Some(scan) = unsearchable(&dir.join("papers").join(&first.folder), || lib.scan()) {
        let scan = scan.unwrap();
        assert_eq!(scan.folders.len(), 1);
        let reasons: Vec<&String> = scan
            .problems
            .iter()
            .filter_map(|p| match p {
                bmlib::library::Problem::UnreadableFolder { reason, .. } => Some(reason),
                _ => None,
            })
            .collect();
        assert_eq!(reasons.len(), 1, "{:?}", scan.problems);
        assert!(reasons[0].starts_with("records: "), "{}", reasons[0]);
        assert!(reasons[0].contains("Permission denied"), "{}", reasons[0]);
    }
    fs::remove_dir_all(dir).unwrap();
}

#[cfg(unix)]
#[test]
fn a_paper_folder_that_cannot_be_examined_is_unreadable_not_stray() {
    let (dir, lib) = scratch("unexamined-folder");
    let first = save(&lib, &me(), &paper(), BYTES);
    if let Some(scan) = unsearchable(&dir.join("papers"), || lib.scan()) {
        let problems = scan.unwrap().problems;
        assert!(
            matches!(&problems[..], [bmlib::library::Problem::UnreadableFolder { folder, .. }] if *folder == first.folder),
            "{problems:?}"
        );
    }
    fs::remove_dir_all(dir).unwrap();
}

#[cfg(unix)]
#[test]
fn a_taken_name_that_cannot_be_examined_fails_the_save() {
    let (dir, lib) = scratch("unexamined-claim");
    let first = save(&lib, &me(), &paper(), BYTES);
    let papers = dir.join("papers");
    let got = unsearchable(&papers, || {
        lib.save(&me(), &paper(), b"%PDF other bytes", SourceKind::Pdf, "t")
    });
    if let Some(got) = got {
        assert!(
            matches!(&got, Err(LibraryError::Io { path, .. }) if *path == papers.join(&first.folder)),
            "{got:?}"
        );
        assert_eq!(fs::read_dir(&papers).unwrap().count(), 1);
    }
    fs::remove_dir_all(dir).unwrap();
}

#[cfg(unix)]
#[test]
fn removing_a_folder_that_cannot_be_examined_is_not_missing() {
    let (dir, lib) = scratch("unexamined-remove");
    let first = save(&lib, &me(), &paper(), BYTES);
    if let Some(got) = unsearchable(&dir.join("papers"), || lib.remove_folder(&first.folder)) {
        assert!(matches!(got, Err(LibraryError::ReadOnly { .. })), "{got:?}");
    }
    assert_eq!(lib.read(&first).unwrap(), BYTES);
    fs::remove_dir_all(dir).unwrap();
}

#[cfg(unix)]
#[test]
fn a_library_reached_through_a_link_is_used() {
    let base = std::env::temp_dir().join(format!("bmlib-save-linked-root-{}", std::process::id()));
    let _ = fs::remove_dir_all(&base);
    fs::create_dir_all(base.join("real")).unwrap();
    std::os::unix::fs::symlink(base.join("real"), base.join("lib")).unwrap();
    let lib = Library::open(&base.join("lib")).unwrap();
    let first = save(&lib, &me(), &paper(), BYTES);
    assert_eq!(lib.read(&first).unwrap(), BYTES);
    assert_eq!(lib.scan().unwrap().folders.len(), 1);
    let again = Library::open(&base.join("lib")).unwrap();
    assert!(again.scan().unwrap().problems.is_empty());
    assert!(base
        .join("real/papers")
        .join(&first.folder)
        .join(&first.name)
        .is_file());
    fs::remove_dir_all(base).unwrap();
}

#[cfg(unix)]
#[test]
fn an_unplugged_drive_behind_the_root_link_is_no_such_folder() {
    let base = std::env::temp_dir().join(format!("bmlib-save-gone-root-{}", std::process::id()));
    let _ = fs::remove_dir_all(&base);
    fs::create_dir_all(&base).unwrap();
    std::os::unix::fs::symlink(base.join("gone"), base.join("lib")).unwrap();
    let got = Library::open(&base.join("lib"));
    assert!(
        matches!(&got, Err(LibraryError::Unreachable { reason, .. }) if reason == "no such folder"),
        "{:?}",
        got.err()
    );
    fs::remove_dir_all(base).unwrap();
}

// A JATS naming another paper (#766): what the shared fixture cannot set up.
const JATS_A: &[u8] = b"<article><front><article-meta>\
<article-id pub-id-type=\"doi\">10.1/a</article-id></article-meta></front></article>";

fn other_doi() -> PaperInfo {
    PaperInfo {
        ids: PaperIds::canonical(Some("111"), Some("10.1/b"), None).unwrap(),
        ..PaperInfo::default()
    }
}

#[test]
fn an_evicted_jats_in_the_folder_stops_the_save() {
    // A placeholder is not a file on disk, but it may be another paper's JATS:
    // it cannot be checked now, so the save waits rather than going in beside it.
    let (dir, lib) = scratch("foreign-evicted");
    let jats = lib
        .save(&me(), &paper(), JATS_A, SourceKind::Jats, "test")
        .unwrap();
    let path = dir.join("papers").join(&jats.folder).join(&jats.name);
    fs::rename(&path, path.with_file_name(format!(".{}.icloud", jats.name))).unwrap();
    let got = lib.save(&you(), &other_doi(), BYTES, SourceKind::Pdf, "test");
    assert!(
        matches!(&got, Err(LibraryError::NotDownloaded { folder, name })
            if *folder == jats.folder && *name == jats.name),
        "{got:?}"
    );
    let pdf = format!("pdf-{}.pdf", &sha256_hex(BYTES)[..12]);
    assert!(!path.with_file_name(pdf).exists());
    assert!(!record_path(&dir, &you(), &jats.folder).exists());
    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn a_changed_jats_in_the_folder_stops_the_save() {
    let (dir, lib) = scratch("foreign-changed");
    let jats = lib
        .save(&me(), &paper(), JATS_A, SourceKind::Jats, "test")
        .unwrap();
    let path = dir.join("papers").join(&jats.folder).join(&jats.name);
    fs::write(&path, b"<article/>").unwrap();
    let got = lib.save(&you(), &other_doi(), BYTES, SourceKind::Pdf, "test");
    assert!(
        matches!(&got, Err(LibraryError::DigestMismatch { folder, name, .. })
            if *folder == jats.folder && *name == jats.name),
        "{got:?}"
    );
    let pdf = format!("pdf-{}.pdf", &sha256_hex(BYTES)[..12]);
    assert!(!path.with_file_name(pdf).exists());
    assert!(!record_path(&dir, &you(), &jats.folder).exists());
    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn an_evicted_jats_beside_the_file_stops_record_use() {
    let (dir, lib) = scratch("foreign-evicted-use");
    let pdf = lib
        .save(&me(), &paper(), BYTES, SourceKind::Pdf, "test")
        .unwrap();
    let jats = lib
        .save(&me(), &paper(), JATS_A, SourceKind::Jats, "test")
        .unwrap();
    let path = dir.join("papers").join(&jats.folder).join(&jats.name);
    fs::rename(&path, path.with_file_name(format!(".{}.icloud", jats.name))).unwrap();
    let got = lib.record_use(&you(), &other_doi(), &pdf);
    assert!(
        matches!(&got, Err(LibraryError::NotDownloaded { folder, name })
            if *folder == jats.folder && *name == jats.name),
        "{got:?}"
    );
    assert!(!record_path(&dir, &you(), &jats.folder).exists());
    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn only_a_jats_in_the_folder_is_read() {
    // An evicted or changed PDF says nothing about the paper, so it must not
    // stop a save.
    let (dir, lib) = scratch("foreign-only-jats");
    let evicted = lib
        .save(&me(), &paper(), BYTES, SourceKind::Pdf, "test")
        .unwrap();
    let changed = lib
        .save(&me(), &paper(), b"%PDF another", SourceKind::Pdf, "test")
        .unwrap();
    let folder = dir.join("papers").join(&evicted.folder);
    fs::rename(
        folder.join(&evicted.name),
        folder.join(format!(".{}.icloud", evicted.name)),
    )
    .unwrap();
    fs::write(folder.join(&changed.name), b"%PDF changed").unwrap();
    let jats = lib
        .save(&you(), &paper(), JATS_A, SourceKind::Jats, "test")
        .unwrap();
    assert_eq!(fs::read(folder.join(&jats.name)).unwrap(), JATS_A);
    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn saving_an_evicted_jats_again_restores_it() {
    // The bytes being saved were checked already, so their evicted copy is not
    // read.
    let (dir, lib) = scratch("foreign-resave");
    let jats = lib
        .save(&me(), &paper(), JATS_A, SourceKind::Jats, "test")
        .unwrap();
    let path = dir.join("papers").join(&jats.folder).join(&jats.name);
    fs::rename(&path, path.with_file_name(format!(".{}.icloud", jats.name))).unwrap();
    let again = lib
        .save(&me(), &paper(), JATS_A, SourceKind::Jats, "test")
        .unwrap();
    assert_eq!(again, jats);
    assert_eq!(fs::read(&path).unwrap(), JATS_A);
    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn only_the_folder_written_to_is_checked() {
    // The paper's folders are a linked group; a foreign JATS in one the save
    // does not choose must not stop it, or one bad folder would block every
    // folder of the paper (#780). "Zebra" sorts after "2024 Smith", so
    // the save's first folder is the PDF's.
    let (dir, lib) = scratch("foreign-target-only");
    let pdf = lib
        .save(&me(), &paper(), BYTES, SourceKind::Pdf, "test")
        .unwrap();
    let zebra = PaperInfo {
        ids: PaperIds::canonical(None, None, Some("PMC9")).unwrap(),
        ..titled("Zebra")
    };
    let jats = lib
        .save(&me(), &zebra, JATS_A, SourceKind::Jats, "test")
        .unwrap();
    let link = PaperInfo {
        ids: PaperIds::canonical(Some("111"), None, Some("PMC9")).unwrap(),
        ..PaperInfo::default()
    };
    lib.record_use(&you(), &link, &pdf).unwrap();
    let scan = lib.scan().unwrap();
    let group: Vec<&str> = find(&scan, &other_doi().ids)
        .iter()
        .map(|f| f.folder.as_str())
        .collect();
    assert_eq!(group, [pdf.folder.as_str(), jats.folder.as_str()]);
    let saved = lib
        .save(
            &me(),
            &other_doi(),
            b"%PDF another",
            SourceKind::Pdf,
            "test",
        )
        .unwrap();
    assert_eq!(saved.folder, pdf.folder);
    // Nor does recording a use there.
    lib.record_use(&you(), &other_doi(), &pdf).unwrap();
    let _ = fs::remove_dir_all(&dir);
}

// A JATS whose parse warns (a colspan past the bound), so a repeated audit
// would show. It names an author, so the colspan is its one warning.
const JATS_WARNS: &[u8] = b"<article><front><article-meta>\
<article-id pub-id-type=\"doi\">10.1/a</article-id><contrib-group>\
<contrib contrib-type=\"author\"><name><surname>Smith</surname></name></contrib>\
</contrib-group></article-meta></front>\
<body><table-wrap><table><tr><td colspan=\"100000\">x</td></tr></table>\
</table-wrap></body></article>";

#[test]
fn reading_a_jats_for_its_identifiers_repeats_no_audit() {
    // Its audit is logged when it is parsed to be shown, not on every save
    // beside it (#780).
    const JATS_LOG: &str = "bmlib.fulltext.jats_parser";
    common::log_capture::start();
    bmlib::fulltext::parse(std::str::from_utf8(JATS_WARNS).unwrap()).unwrap();
    let control = common::log_capture::take(JATS_LOG);
    let levels: Vec<log::Level> = control.iter().map(|r| r.level).collect();
    assert_eq!(levels, [log::Level::Warn], "{control:?}");
    let (dir, lib) = scratch("foreign-quiet");
    let jats = lib
        .save(&me(), &paper(), JATS_WARNS, SourceKind::Jats, "test")
        .unwrap();
    lib.save(&me(), &paper(), BYTES, SourceKind::Pdf, "test")
        .unwrap();
    lib.record_use(&you(), &paper(), &jats).unwrap();
    let logged = common::log_capture::take(JATS_LOG);
    assert!(
        logged.iter().all(|r| r.level > log::Level::Info),
        "{logged:?}"
    );
    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn reading_a_jats_for_its_identifiers_logs_no_debug_line_either() {
    // Python's `warn=False` returns before `_report_zero_authors`, so its
    // DEBUG line for an author-less article is skipped as well (#782).
    const JATS_LOG: &str = "bmlib.fulltext.jats_parser";
    const AUTHOR_LESS: &[u8] = b"<article><front><article-meta>\
<article-id pub-id-type=\"doi\">10.1/a</article-id></article-meta></front>\
<body><p>t</p></body></article>";
    common::log_capture::start();
    bmlib::fulltext::parse(std::str::from_utf8(AUTHOR_LESS).unwrap()).unwrap();
    let control = common::log_capture::take(JATS_LOG);
    let levels: Vec<log::Level> = control.iter().map(|r| r.level).collect();
    assert_eq!(levels, [log::Level::Debug], "{control:?}");
    let (dir, lib) = scratch("foreign-quiet-debug");
    lib.save(&me(), &paper(), AUTHOR_LESS, SourceKind::Jats, "test")
        .unwrap();
    lib.save(&me(), &paper(), BYTES, SourceKind::Pdf, "test")
        .unwrap();
    let logged = common::log_capture::take(JATS_LOG);
    assert!(logged.is_empty(), "{logged:?}");
    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn foreign_jats_names_the_file() {
    let e = LibraryError::ForeignJats {
        folder: Some("F".into()),
        name: "jats-x.xml".into(),
        field: "doi",
        theirs: "10.1/a".into(),
        ours: "10.1/b".into(),
    };
    assert_eq!(
        e.to_string(),
        "F holds jats-x.xml, the JATS of another paper: its own doi 10.1/a is not this \
         paper's 10.1/b; nothing was written"
    );
}

/// #788 review: a folder whose name is not UTF-8 is a stray entry, even beside
/// a real folder holding its lossy spelling, which is scanned once. Linux only:
/// APFS and Windows refuse such names.
#[cfg(target_os = "linux")]
#[test]
fn a_non_utf8_folder_is_reported_beside_one_with_its_lossy_name() {
    use std::os::unix::ffi::OsStrExt;
    let (dir, lib) = scratch("lossy");
    let papers = dir.join("papers");
    for name in [&b"caf\xe9"[..], "caf\u{fffd}".as_bytes()] {
        let folder = papers.join(std::ffi::OsStr::from_bytes(name));
        fs::create_dir(&folder).unwrap();
        fs::write(folder.join("x.pdf"), name).unwrap();
    }
    let scan = lib.scan().unwrap();
    let folders: Vec<_> = scan
        .folders
        .iter()
        .map(|f| (f.folder.as_str(), f.files_on_disk.clone()))
        .collect();
    assert_eq!(folders, [("caf\u{fffd}", vec!["x.pdf".to_owned()])]);
    let stray: Vec<_> = scan
        .problems
        .iter()
        .filter(|p| matches!(p, bmlib::library::Problem::StrayEntry { .. }))
        .collect();
    assert_eq!(
        stray,
        [&bmlib::library::Problem::StrayEntry {
            name: "caf\u{fffd}".to_owned()
        }]
    );
    fs::remove_dir_all(dir).unwrap();
}

/// #796: a file whose name is not UTF-8 is an `UndecodableName` problem, not
/// one of the folder's files, so its lossy name, which a real sibling holds
/// here, is never handed back to be joined. Two such names that read alike
/// are each reported, and a record whose name is not UTF-8 is unreadable, as
/// in Python's faked-listing test. Linux only, as above.
#[cfg(target_os = "linux")]
#[test]
fn a_non_utf8_file_is_not_listed_beside_one_with_its_lossy_name() {
    use bmlib::library::Problem;
    use std::os::unix::ffi::OsStrExt;
    let (dir, lib) = scratch("lossy-file");
    let folder = dir.join("papers").join("p");
    fs::create_dir_all(folder.join("records")).unwrap();
    for name in [&b"x\xe9.pdf"[..], b"x\xea.pdf", "x\u{fffd}.pdf".as_bytes()] {
        fs::write(folder.join(std::ffi::OsStr::from_bytes(name)), name).unwrap();
    }
    let record = folder
        .join("records")
        .join(std::ffi::OsStr::from_bytes(b"r\xe9.json"));
    fs::write(record, b"{}").unwrap();
    let scan = lib.scan().unwrap();
    assert_eq!(scan.folders[0].files_on_disk, ["x\u{fffd}.pdf"]);
    let (folder, name) = ("p".to_owned(), "x\u{fffd}.pdf".to_owned());
    let lossy = || Problem::UndecodableName {
        folder: folder.clone(),
        name: name.clone(),
    };
    assert_eq!(
        scan.problems,
        [
            Problem::UnreadableRecord {
                folder: folder.clone(),
                file: "r\u{fffd}.json".to_owned(),
                reason: "the name is not UTF-8".to_owned(),
            },
            lossy(),
            lossy(),
            Problem::UnrecordedFile {
                folder: folder.clone(),
                name: name.clone(),
            },
        ]
    );
    fs::remove_dir_all(dir).unwrap();
}

/// A corrupt record is kept aside beside itself under a library root whose
/// path is not UTF-8: the path is built from the OS string, not a lossy
/// display. Linux only, as above.
#[cfg(target_os = "linux")]
#[test]
fn corrupt_record_is_kept_aside_under_a_non_utf8_root() {
    use std::os::unix::ffi::OsStrExt;
    let parent = std::env::temp_dir().join(format!("bmlib-save-root-{}", std::process::id()));
    let _ = fs::remove_dir_all(&parent);
    let dir = parent.join(std::ffi::OsStr::from_bytes(b"lib\xe9"));
    fs::create_dir_all(&dir).unwrap();
    let lib = Library::open(&dir).unwrap();
    let first = save(&lib, &me(), &paper(), BYTES);
    let path = record_path(&dir, &me(), &first.folder);
    fs::write(&path, "{ not json").unwrap();
    save(&lib, &me(), &paper(), BYTES);
    let mut aside = path.clone().into_os_string();
    aside.push(CORRUPT_SUFFIX);
    assert_eq!(fs::read_to_string(&aside).unwrap(), "{ not json");
    fs::remove_dir_all(parent).unwrap();
}

/// #778: the folder's records are hidden from `find`, and its name is the paper's.
#[cfg(unix)]
#[test]
fn a_revised_title_beside_a_folder_that_cannot_be_examined_is_refused_not_forked() {
    let (dir, lib) = scratch("unexamined-revised");
    let first = save(&lib, &me(), &paper(), BYTES);
    let papers = dir.join("papers");
    let revised = PaperInfo {
        title: Some("Metformin revised".into()),
        ..paper()
    };
    let got = unsearchable(&papers.join(&first.folder), || {
        lib.save(&me(), &revised, b"%PDF other bytes", SourceKind::Pdf, "t")
    });
    if let Some(got) = got {
        assert!(
            matches!(&got, Err(LibraryError::Io { path, reason })
                if *path == papers.join(&first.folder)
                    && reason.ends_with("may hold this paper; nothing was written")),
            "{got:?}"
        );
        assert_eq!(fs::read_dir(&papers).unwrap().count(), 1);
    }
    fs::remove_dir_all(dir).unwrap();
}

#[cfg(unix)]
#[test]
fn another_paper_beside_a_folder_that_cannot_be_examined_is_saved() {
    let (dir, lib) = scratch("unexamined-other");
    let first = save(&lib, &me(), &paper(), BYTES);
    let other = PaperInfo {
        ids: PaperIds::canonical(Some("222"), None, None).unwrap(),
        title: Some("Metformin revised".into()),
        ..paper()
    };
    let got = unsearchable(&dir.join("papers").join(&first.folder), || {
        lib.save(&me(), &other, b"%PDF other bytes", SourceKind::Pdf, "t")
    });
    if let Some(got) = got {
        assert_eq!(
            got.unwrap().folder,
            "2024 Smith – Metformin revised (PMID 222)"
        );
    }
    fs::remove_dir_all(dir).unwrap();
}
