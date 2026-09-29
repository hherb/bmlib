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

//! The cache's naming rules and the atomic publish — the oracle and named tests.

use bmlib::atomic::{atomic_write, temp_path_beside, TEMP_SUFFIX_LEN};
use bmlib::fulltext::cache::{
    is_readable, safe_filename, sanitize_identifier, FullTextCache, CORRUPT_SUFFIX, MAX_KEY_CHARS,
    MAX_PREFIX_CHARS, PDF_MAGIC_BYTES, TEMP_ROOM,
};
use serde_json::Value;

const CASES: &str = include_str!("data/cache_cases.json");
const EXPECTED: &str = include_str!("data/cache_expected.json");

fn run(case: &Value) -> Value {
    let a = &case["args"];
    match case["fn"].as_str().unwrap_or_default() {
        "sanitize_identifier" => {
            Value::String(sanitize_identifier(a["raw"].as_str().unwrap_or_default()))
        }
        "safe_filename" => {
            Value::String(safe_filename(a["identifier"].as_str().unwrap_or_default()))
        }
        other => panic!("unknown fn {other:?}"),
    }
}

/// **The corpus carries no correction, and the mechanism stays.**
///
/// `safe_filename/161` was the register's one entry: `_safe_filename` passed an
/// over-long *safe* identifier through where its own docstring said it sanitised
/// it, and the port implemented the docstring. Python's PR #355 moved the
/// pass-through bound from `_MAX_PREFIX_CHARS` to `_MAX_KEY_CHARS`, which is the
/// length of the longest key the sanitiser returns, so the docstring and the code
/// agree and the block is retired rather than kept as a stale note. The list is
/// asserted empty so re-adding one is a deliberate act, and the reason check below
/// still applies to whatever the next divergence is.
#[test]
fn no_case_carries_a_correction_today() {
    let cases: Value = serde_json::from_str(CASES).expect("cases parse");
    let named: Vec<&str> = cases
        .as_array()
        .expect("cases is a list")
        .iter()
        .filter(|case| case.get("corrected").is_some())
        .filter_map(|case| case["name"].as_str())
        .collect();
    assert!(
        named.is_empty(),
        "the corpus is expected to agree with Python everywhere; a correction is \
         a deliberate divergence and must be recorded here and in the plan's §9: {named:?}"
    );
    for case in cases.as_array().expect("cases is a list") {
        if let Some(corrected) = case.get("corrected") {
            let why = corrected["why"].as_str().unwrap_or_default();
            assert!(
                !why.is_empty(),
                "{}: a correction with no reason is a tolerance",
                case["name"]
            );
        }
    }
}

#[test]
fn the_port_agrees_with_python_on_every_case() {
    let cases: Value = serde_json::from_str(CASES).expect("cases parse");
    let expected: Value = serde_json::from_str(EXPECTED).expect("expected parse");
    let cases = cases.as_array().expect("cases is a list");
    let wants = expected["cases"].as_array().expect("cases");

    let mut failures: Vec<String> = Vec::new();
    for (case, want) in cases.iter().zip(wants.iter()) {
        let name = case["name"].as_str().unwrap_or_default();
        assert_eq!(name, want["name"].as_str().unwrap_or_default());
        assert!(
            want["ok"].as_bool().unwrap_or(false),
            "{name}: {}",
            want["error"]
        );
        let got = run(case);
        // A case Python and the port **disagree** on carries what the port should
        // answer instead, with Python's own committed answer asserted to differ —
        // so a correction cannot quietly become a tautology.
        let target = match case.get("corrected") {
            Some(corrected) => {
                assert!(
                    want["value"] != corrected["value"],
                    "{name}: the correction is Python's own answer, so it pins nothing"
                );
                &corrected["value"]
            }
            None => &want["value"],
        };
        if got != *target {
            failures.push(format!(
                "  {name}\n    python: {}\n    port:   {}",
                serde_json::to_string(&want["value"]).unwrap_or_default(),
                serde_json::to_string(&got).unwrap_or_default()
            ));
        }
    }
    assert!(
        failures.is_empty(),
        "{} of {} diverge:\n{}",
        failures.len(),
        cases.len(),
        failures.join("\n")
    );

    assert_eq!(
        MAX_PREFIX_CHARS,
        expected["tables"]["MAX_PREFIX_CHARS"]
            .as_u64()
            .expect("prefix cap") as usize
    );
    assert_eq!(
        MAX_KEY_CHARS,
        expected["tables"]["MAX_KEY_CHARS"]
            .as_u64()
            .expect("key cap") as usize
    );
    // The cap is *derived* from the prefix cap, not an independent literal: 160
    // characters, `_`, and a ten-character digest is the longest key
    // `sanitize_identifier` can return, and this is the only place the two
    // figures are tied together.
    assert_eq!(MAX_KEY_CHARS, MAX_PREFIX_CHARS + 1 + 10);
}

/// A temporary directory that cleans up after itself.
struct TempDir(std::path::PathBuf);

impl TempDir {
    fn new(label: &str) -> Self {
        let unique = format!(
            "bmlib-test-{label}-{}-{:x}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_nanos())
                .unwrap_or_default()
        );
        let path = std::env::temp_dir().join(unique);
        std::fs::create_dir_all(&path).expect("temp dir");
        TempDir(path)
    }

    fn path(&self) -> &std::path::Path {
        &self.0
    }
}

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

// ---------------------------------------------------------------------------
// Naming
// ---------------------------------------------------------------------------

/// **The hash is over the *whole* raw identifier**, and that is what carries the
/// collision guarantee — the readable prefix is only there to be read, so two
/// identifiers that sanitise alike still get different files.
#[test]
fn two_identifiers_that_sanitise_alike_get_different_files() {
    // These differ only in a character that maps to `_`.
    let a = sanitize_identifier("10.1234/abc");
    let b = sanitize_identifier("10.1234:abc");
    assert_ne!(a, b, "distinct identifiers must not share a file");
    // And their readable prefixes are identical, which is the point: the hash is
    // what distinguishes them.
    assert_eq!(
        a.rsplit_once('_').expect("a hash").0,
        b.rsplit_once('_').expect("a hash").0
    );
    // The same identifier is stable.
    assert_eq!(a, sanitize_identifier("10.1234/abc"));
}

/// The prefix is truncated to [`MAX_PREFIX_CHARS`] **by characters**, and an
/// already-safe identifier up to [`MAX_KEY_CHARS`] — the longest key the
/// sanitiser returns — passes through so existing cache files stay addressable.
#[test]
fn the_prefix_cap_is_characters_and_safe_names_pass_through() {
    assert_eq!(safe_filename("abc_123.def-ghi"), "abc_123.def-ghi");
    let at_prefix = "y".repeat(MAX_PREFIX_CHARS);
    assert_eq!(
        safe_filename(&at_prefix),
        at_prefix,
        "exactly at the prefix cap passes through"
    );
    // The pass-through bound is the **key** cap (171), not the prefix cap (160):
    // a 161-character identifier is below every key the sanitiser can return, so
    // hashing it again would address a file no lookup by that key finds (#309).
    let at_key = "z".repeat(MAX_KEY_CHARS);
    assert_eq!(
        safe_filename(&at_key),
        at_key,
        "a whole key passes through unchanged"
    );
    let over = "x".repeat(MAX_KEY_CHARS + 1);
    let result = safe_filename(&over);
    assert_ne!(
        result, over,
        "over the key cap is sanitised even though it is safe"
    );
    // The result is **not** shorter: the hash suffix is appended, so the name is
    // the truncated prefix plus `_` plus ten hex characters. What the cap buys is
    // that the *prefix* stops at 160, leaving room for the atomic write's
    // temporary affix inside the filesystem's name limit.
    assert_eq!(
        result.chars().count(),
        MAX_PREFIX_CHARS + 1 + 10,
        "the prefix is capped and the hash is appended: {result}"
    );
    // A multi-byte identifier of 160 *characters* passes through.
    let unicode = "\u{e9}".repeat(MAX_PREFIX_CHARS);
    assert_eq!(safe_filename(&unicode), unicode);
}

/// **Every key the sanitiser returns passes back through it unchanged** — the
/// property #309's correction exists for. Bounded at the prefix alone, a raw
/// identifier of 150 characters or more was hashed a *second* time by the
/// service's `sanitize_identifier` → `safe_filename` pair, so the file written
/// was not the documented `sanitize_identifier(identifier)` and a lookup by that
/// key missed it.
#[test]
fn a_key_the_sanitiser_returned_is_never_hashed_twice() {
    for raw in [
        "10.1234/jbr.2024.001".to_string(),
        "a".repeat(150),
        "b".repeat(160),
        "c".repeat(171),
        "d".repeat(400),
        "e/".repeat(100),
        "\u{e9}".repeat(200),
    ] {
        let key = sanitize_identifier(&raw);
        assert!(
            key.chars().count() <= MAX_KEY_CHARS,
            "{} characters from {} raw: {key}",
            key.chars().count(),
            raw.chars().count()
        );
        assert_eq!(
            safe_filename(&key),
            key,
            "the service's own key must survive its second pass"
        );
    }
}

/// **No identifier can produce a name outside the cache directory.** This is the
/// property the sanitiser exists for, and it holds even for a name made only of
/// dots: the cache appends an extension, so `".."` becomes `"...pdf"` — a plain
/// file, not a parent reference.
///
/// I checked this adversarially after first suspecting a traversal, and the
/// suspicion was **wrong**: no result of the sanitiser contains a path separator,
/// and every resolved target stays inside the cache.
#[test]
fn no_identifier_escapes_the_cache_directory() {
    let dir = TempDir::new("escape");
    let cache = FullTextCache::new(Some(dir.path().to_path_buf()))
        .expect("an explicit directory needs no home");
    let adversarial = [
        "..",
        ".",
        "...",
        "....",
        ". .",
        "../",
        "a/../../b",
        "%2e%2e",
        "",
        "\u{2024}\u{2024}",
        &"a".repeat(300),
    ];
    for identifier in adversarial {
        let name = safe_filename(identifier);
        assert!(
            !name.contains('/') && !name.contains('\\'),
            "{identifier:?} produced a separator: {name:?}"
        );
        for (directory, extension) in [(cache.html_dir(), "html"), (cache.pdf_dir(), "pdf")] {
            let target = directory.join(format!("{name}.{extension}"));
            // The parent of the target must be the sub-cache itself, always.
            assert_eq!(
                target.parent(),
                Some(directory.as_path()),
                "{identifier:?} escaped via {target:?}"
            );
        }
    }
}

// ---------------------------------------------------------------------------
// Reading, quarantine and removal
// ---------------------------------------------------------------------------

/// An HTML entry truncated mid-multibyte-sequence **opens perfectly** and fails
/// on the decode, so readability is judged the way the getter reads it.
#[test]
fn an_unreadable_entry_is_not_readable() {
    let dir = TempDir::new("readable");
    let good = dir.path().join("good.html");
    std::fs::write(&good, b"<html>ok</html>").expect("write");
    assert!(is_readable(&good));

    let bad = dir.path().join("bad.html");
    // Invalid UTF-8: the file opens and the decode fails.
    std::fs::write(&bad, [0x3c, 0xff, 0xfe, 0x3e]).expect("write");
    assert!(!is_readable(&bad), "a bad decode is not readable");

    // A directory where a file should be fails on the open.
    let subdir = dir.path().join("adir");
    std::fs::create_dir(&subdir).expect("mkdir");
    assert!(!is_readable(&subdir));
}

/// **An unreadable entry is quarantined, not deleted**, so a failed re-fetch
/// leaves the evidence — and it must leave the lookup path, or it hides a good
/// PDF behind it for ever.
#[test]
fn a_corrupt_entry_is_moved_aside_and_a_good_one_is_left_alone() {
    let dir = TempDir::new("quarantine");
    let cache = FullTextCache::new(Some(dir.path().to_path_buf()))
        .expect("an explicit directory needs no home");
    std::fs::create_dir_all(cache.html_dir()).expect("html dir");
    std::fs::create_dir_all(cache.pdf_dir()).expect("pdf dir");

    let name = safe_filename("10.1234/x");
    let html = cache.html_dir().join(format!("{name}.html"));
    let pdf = cache.pdf_dir().join(format!("{name}.pdf"));
    std::fs::write(&html, [0xff, 0xfe]).expect("write bad html");
    std::fs::write(&pdf, b"%PDF-1.4 good").expect("write good pdf");

    let moved = cache.quarantine("10.1234/x");
    assert_eq!(moved.len(), 1, "only the unreadable one moves: {moved:?}");
    assert!(moved[0].to_string_lossy().ends_with(CORRUPT_SUFFIX));
    assert!(!html.exists(), "the bad entry left the lookup path");
    assert!(pdf.exists(), "a readable entry is left alone");
    // The bytes are still there for an operator to inspect.
    assert!(moved[0].exists());
}

/// **A PDF entry that cannot be read is not a hit** (defect #309). Python tested
/// only `path.exists()`, so a directory standing where the entry should be — or
/// any file the process cannot open — was returned as a cached PDF. The
/// conversion that followed failed, `_attach_pdf_text` swallowed it, and the same
/// bogus hit was served on every later run. A readable entry is still a hit.
#[test]
fn an_unreadable_pdf_entry_is_not_a_hit() {
    let dir = TempDir::new("pdf-hit");
    let cache = FullTextCache::new(Some(dir.path().to_path_buf()))
        .expect("an explicit directory needs no home");
    std::fs::create_dir_all(cache.pdf_dir()).expect("pdf dir");

    let name = safe_filename("10.1234/x");
    let path = cache.pdf_dir().join(format!("{name}.pdf"));
    assert!(cache.get_pdf("10.1234/x").is_none(), "nothing written yet");

    // A directory where the PDF should be: present, and not a file to read.
    std::fs::create_dir(&path).expect("mkdir");
    assert!(
        cache.get_pdf("10.1234/x").is_none(),
        "a directory is not a cached PDF"
    );

    std::fs::remove_dir(&path).expect("rmdir");
    std::fs::write(&path, b"%PDF-1.4 x").expect("write");
    assert_eq!(cache.get_pdf("10.1234/x"), Some(path));
}

/// `clear` removes **every** entry, including one that is a directory and one
/// that is quarantined — an entry that is not a regular file is exactly the
/// corrupt case this exists to clear.
#[test]
fn clear_removes_odd_shaped_entries_too() {
    let dir = TempDir::new("clear");
    let cache = FullTextCache::new(Some(dir.path().to_path_buf()))
        .expect("an explicit directory needs no home");
    std::fs::create_dir_all(cache.pdf_dir()).expect("pdf dir");
    std::fs::create_dir_all(cache.html_dir()).expect("html dir");
    std::fs::write(cache.pdf_dir().join("a.pdf"), b"%PDF").expect("write");
    std::fs::write(
        cache.html_dir().join(format!("b.html{CORRUPT_SUFFIX}")),
        b"junk",
    )
    .expect("write");
    // An entry that is a directory rather than a file.
    std::fs::create_dir(cache.html_dir().join("odd.html")).expect("mkdir");

    cache.clear().expect("clears");
    let left: Vec<_> = std::fs::read_dir(cache.html_dir())
        .expect("read")
        .flatten()
        .collect();
    assert!(left.is_empty(), "clear left {left:?}");
}

// ---------------------------------------------------------------------------
// Saving
// ---------------------------------------------------------------------------

/// **Non-PDF data is rejected, not written** — and rejection is a different
/// outcome from a write that failed, so a caller can report the first and must
/// not swallow the second.
#[test]
fn non_pdf_data_is_rejected_rather_than_written() {
    let dir = TempDir::new("pdf");
    let cache = FullTextCache::new(Some(dir.path().to_path_buf()))
        .expect("an explicit directory needs no home");
    // The cache does **not** create its sub-directories: the source's
    // `atomic_write` does not either, and a missing directory is a caller's
    // setup mistake rather than something to paper over.
    std::fs::create_dir_all(cache.pdf_dir()).expect("pdf dir");
    assert_eq!(
        cache
            .save_pdf(b"<html>not a pdf</html>", "x")
            .expect("no io error"),
        None
    );
    assert!(cache.get_pdf("x").is_none());

    // A real PDF is written and found.
    let saved = cache
        .save_pdf(b"%PDF-1.4 content", "x")
        .expect("writes")
        .expect("accepted");
    assert_eq!(cache.get_pdf("x"), Some(saved));
    // The magic check is a prefix, and a short body is refused.
    assert_eq!(cache.save_pdf(b"%PD", "y").expect("no io error"), None);
    assert_eq!(PDF_MAGIC_BYTES, b"%PDF");
}

/// A cache hit returns the content, and a miss is a clean `None` — including for
/// an entry whose bytes will not decode.
#[test]
fn html_round_trips_and_a_miss_is_none() {
    let dir = TempDir::new("html");
    let cache = FullTextCache::new(Some(dir.path().to_path_buf()))
        .expect("an explicit directory needs no home");
    std::fs::create_dir_all(cache.html_dir()).expect("html dir");
    assert_eq!(cache.get_html("absent"), None);
    cache
        .save_html("<html>caf\u{e9}</html>", "doi:10.1/x")
        .expect("writes");
    assert_eq!(
        cache.get_html("doi:10.1/x").as_deref(),
        Some("<html>caf\u{e9}</html>")
    );
    // An undecodable entry reads as a miss, not as an error.
    let path = cache
        .html_dir()
        .join(format!("{}.html", safe_filename("bad")));
    std::fs::write(&path, [0xff, 0xfe]).expect("write");
    assert_eq!(cache.get_html("bad"), None);
}

/// **The abstract a cached PDF was returned with round-trips in its own
/// directory** (#305), and that directory is created by the first save rather
/// than by construction — a cache an earlier bmlib built, possibly read-only,
/// still constructs.
#[test]
fn an_abstract_round_trips_beside_a_cached_pdf() {
    let dir = TempDir::new("abstract");
    let cache = FullTextCache::new(Some(dir.path().to_path_buf()))
        .expect("an explicit directory needs no home");
    assert_eq!(cache.get_abstract("absent"), None);
    assert!(
        !cache.abstract_dir().exists(),
        "construction never creates abstracts/"
    );

    let saved = cache
        .save_abstract("<h1>Only the abstract</h1>", "doi:10.1/x")
        .expect("writes");
    assert_eq!(
        saved,
        cache
            .abstract_dir()
            .join(format!("{}.html", safe_filename("doi:10.1/x"))),
        "the entry is addressable by safe_filename"
    );
    assert_eq!(
        cache.get_abstract("doi:10.1/x").as_deref(),
        Some("<h1>Only the abstract</h1>")
    );
    // The directory is named `abstracts` and is not `html/`: an abstract must
    // never be served as full text by the HTML lookup.
    assert_eq!(cache.get_html("doi:10.1/x"), None);
    assert!(cache.abstract_dir().ends_with("abstracts"));

    // An undecodable entry reads as a miss, exactly as `get_html`'s does.
    std::fs::write(&saved, [0xff, 0xfe]).expect("write");
    assert_eq!(cache.get_abstract("doi:10.1/x"), None);
}

/// `delete` and `clear` cover the abstract entry, and `quarantine` moves an
/// unreadable one aside **third**, after HTML and PDF — Python's `_entries`
/// order.
#[test]
fn delete_quarantine_and_clear_cover_the_abstract_entry() {
    let dir = TempDir::new("abstract-ops");
    let cache = FullTextCache::new(Some(dir.path().to_path_buf()))
        .expect("an explicit directory needs no home");
    for directory in [cache.html_dir(), cache.pdf_dir(), cache.abstract_dir()] {
        std::fs::create_dir_all(&directory).expect("sub-cache");
    }
    let name = safe_filename("10.1/x");
    std::fs::write(
        cache.abstract_dir().join(format!("{name}.html")),
        "abstract",
    )
    .expect("write");
    assert!(cache.get_abstract("10.1/x").is_some());

    cache.delete("10.1/x");
    assert_eq!(
        cache.get_abstract("10.1/x"),
        None,
        "delete covers abstracts/"
    );
}

/// An unreadable abstract is moved aside like the other two entries, and it is
/// checked last.
#[test]
fn an_unreadable_abstract_is_quarantined_last() {
    let dir = TempDir::new("abstract-quarantine");
    let cache = FullTextCache::new(Some(dir.path().to_path_buf()))
        .expect("an explicit directory needs no home");
    for directory in [cache.html_dir(), cache.pdf_dir(), cache.abstract_dir()] {
        std::fs::create_dir_all(&directory).expect("sub-cache");
    }
    let name = safe_filename("10.1/x");
    std::fs::write(cache.pdf_dir().join(format!("{name}.pdf")), b"%PDF-1.4 x").expect("good pdf");
    std::fs::write(
        cache.abstract_dir().join(format!("{name}.html")),
        [0xff, 0xfe],
    )
    .expect("bad abstract");

    let moved = cache.quarantine("10.1/x");
    assert_eq!(moved.len(), 1, "only the unreadable one moves: {moved:?}");
    assert_eq!(
        moved[0],
        cache
            .abstract_dir()
            .join(format!("{name}.html{CORRUPT_SUFFIX}"))
    );
    assert!(cache.pdf_dir().join(format!("{name}.pdf")).exists());
}

/// `clear` sweeps `abstracts/` too, and **skips it when it is absent** — an
/// older cache lacks the directory, where a missing `pdfs/` or `html/` used to
/// raise before the loop skipped every subdirectory alike.
#[test]
fn clear_covers_the_abstract_directory_and_skips_an_absent_one() {
    let dir = TempDir::new("abstract-clear");
    let cache = FullTextCache::new(Some(dir.path().to_path_buf()))
        .expect("an explicit directory needs no home");
    cache.clear().expect("an absent abstracts/ is not an error");

    cache
        .save_abstract("<p>held back</p>", "10.1/x")
        .expect("writes");
    let leftover = cache.abstract_dir().join("stray.html");
    std::fs::write(&leftover, "stray").expect("write");
    cache.clear().expect("clears");
    let left: Vec<_> = std::fs::read_dir(cache.abstract_dir())
        .expect("read")
        .flatten()
        .collect();
    assert!(left.is_empty(), "clear left {left:?}");
}

// ---------------------------------------------------------------------------
// The atomic publish
// ---------------------------------------------------------------------------

/// **A failed write leaves the target untouched** — either the previous version
/// or nothing — never a truncated file that reads back perfectly and is trusted
/// for ever after.
#[test]
fn a_failed_write_leaves_the_previous_version_intact() {
    let dir = TempDir::new("atomic");
    let target = dir.path().join("entry.bin");
    atomic_write(&target, b"first").expect("writes");
    assert_eq!(std::fs::read(&target).expect("read"), b"first");

    // A write into a path whose parent is not a directory fails, and the target
    // is unchanged.
    let blocked = dir.path().join("entry.bin").join("nested");
    assert!(atomic_write(&blocked, b"second").is_err());
    assert_eq!(std::fs::read(&target).expect("read"), b"first");

    // Overwriting works and is complete.
    atomic_write(&target, b"third").expect("writes");
    assert_eq!(std::fs::read(&target).expect("read"), b"third");
}

/// **No temporary file survives a write**, and the temporary name is
/// [`TEMP_SUFFIX_LEN`] characters longer than the target — the figure the cache's
/// prefix cap leaves room for.
#[test]
fn a_write_leaves_no_temporary_behind() {
    let dir = TempDir::new("temp");
    let target = dir.path().join("entry.bin");
    atomic_write(&target, b"data").expect("writes");
    let left: Vec<_> = std::fs::read_dir(dir.path())
        .expect("read")
        .flatten()
        .map(|e| e.file_name().to_string_lossy().to_string())
        .collect();
    assert_eq!(left, vec!["entry.bin".to_string()], "left {left:?}");

    let temporary = temp_path_beside(&target);
    let extra = temporary
        .file_name()
        .expect("name")
        .to_string_lossy()
        .chars()
        .count()
        - target
            .file_name()
            .expect("name")
            .to_string_lossy()
            .chars()
            .count();
    // The token is variable-length, so the declared constant is a lower bound on
    // the affix — and the cache's cap is what leaves room for it.
    assert!(
        extra >= TEMP_SUFFIX_LEN,
        "the temporary name must be at least {TEMP_SUFFIX_LEN} longer, was {extra}"
    );
    assert_eq!(TEMP_ROOM, TEMP_SUFFIX_LEN);
    // Every temporary carries a unique component, so two writers cannot collide —
    // and the loser's cleanup cannot remove the winner's file.
    assert_ne!(temp_path_beside(&target), temp_path_beside(&target));
}
