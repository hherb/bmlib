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

//! Paper-folder names: readable, safe on every file system, bounded.

use unicode_normalization::UnicodeNormalization;

use super::ids::{is_bidi_control, PaperIds, PaperInfo};
use super::{MAX_AUTHOR_CHARS_IN_NAME, MAX_FOLDER_NAME_BYTES, MAX_TITLE_CHARS_IN_NAME};
use crate::pyvalue::{is_python_space, python_strip};

/// Device names Windows refuses as a file's base name: CPython 3.13's
/// `ntpath.isreserved` list, the superscript digits and console names included.
const RESERVED: [&str; 30] = [
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "CONIN$",
    "CONOUT$",
    "COM1",
    "COM2",
    "COM3",
    "COM4",
    "COM5",
    "COM6",
    "COM7",
    "COM8",
    "COM9",
    "COM\u{b9}",
    "COM\u{b2}",
    "COM\u{b3}",
    "LPT1",
    "LPT2",
    "LPT3",
    "LPT4",
    "LPT5",
    "LPT6",
    "LPT7",
    "LPT8",
    "LPT9",
    "LPT\u{b9}",
    "LPT\u{b2}",
    "LPT\u{b3}",
];

/// The longest prefix of `s` that is at most `max` bytes, on a character
/// boundary.
fn cut_bytes(s: &str, max: usize) -> &str {
    let mut end = max.min(s.len());
    while !s.is_char_boundary(end) {
        end -= 1;
    }
    &s[..end]
}

/// `s` cut to `max` characters at the last space at or before the limit.
fn cut_title(s: &str, max: usize) -> String {
    let chars: Vec<char> = s.chars().collect();
    if chars.len() <= max {
        return s.to_owned();
    }
    let end = chars[..=max].iter().rposition(|&c| c == ' ').unwrap_or(max);
    chars[..end].iter().collect()
}

/// NFC; forbidden, control and bidi characters to spaces; whitespace runs to
/// one space; no leading dots or spaces, no trailing spaces.
fn sanitise(s: &str) -> String {
    let spaced: String = s
        .nfc()
        .map(|c| {
            if "<>:\"/\\|?*".contains(c) || c.is_control() || is_bidi_control(c) {
                ' '
            } else {
                c
            }
        })
        .collect();
    let joined = spaced.split_whitespace().collect::<Vec<_>>().join(" ");
    joined.trim_start_matches(['.', ' ']).to_owned()
}

/// Whether `folder` could be [`folder_name_numbered`]'s name for a paper
/// holding one of `ids`: it ends `(PMID …)`, `(DOI …)` or `(PMCID …)` naming
/// one of them, before any ` (n)`, compared after NFC and lowercasing.
///
/// Nothing else reads an identifier from a name. A save asks this only of a
/// folder whose records it cannot read (#778, #755). A name cut short or
/// renamed by a user is a miss, so this can only ever refuse a save, never
/// choose a folder. Python's `names.may_name` is the twin.
pub(crate) fn may_name(folder: &str, ids: &PaperIds) -> bool {
    let name = folder.nfc().collect::<String>().to_lowercase();
    let mut bodies = vec![name.as_str()];
    if let Some(body) = numbered_body(&name) {
        bodies.push(body);
    }
    [
        ("PMID", &ids.pmid),
        ("DOI", &ids.doi),
        ("PMCID", &ids.pmcid),
    ]
    .into_iter()
    .filter_map(|(label, value)| value.as_ref().map(|v| (label, v)))
    .any(|(label, value)| {
        let tag = format!("({})", sanitise(&format!("{label} {value}"))).to_lowercase();
        bodies.iter().any(|b| b.ends_with(&tag))
    })
}

/// `name` without a trailing ` (n)` (one or more ASCII digits), if it has one:
/// Python's `re.fullmatch(r"(.*) \((\d+)\)", name)`.
fn numbered_body(name: &str) -> Option<&str> {
    let open = name.strip_suffix(')')?.rfind(" (")?;
    let digits = &name[open + 2..name.len() - 1];
    (!digits.is_empty() && digits.bytes().all(|b| b.is_ascii_digit())).then(|| &name[..open])
}

/// The folder name for a paper: `year author – title (ID)`, at most
/// [`MAX_FOLDER_NAME_BYTES`] bytes, safe on macOS, Windows and Linux. The
/// length is fitted first; a Windows device name (`con.x`) in the fitted name
/// then gets a `_` prefix, for which one byte of the budget is kept.
#[must_use]
pub fn folder_name(info: &PaperInfo) -> String {
    named(info, 1)
}

/// The folder name for the `n`th paper to want [`folder_name`]'s name: the same
/// name ending ` (n)`, still at most [`MAX_FOLDER_NAME_BYTES`] bytes (the body
/// is cut to make room). `n` below 2 gives [`folder_name`] itself.
#[must_use]
pub fn folder_name_numbered(info: &PaperInfo, n: u32) -> String {
    named(info, n)
}

/// The shared fitting code behind both; `n` of 1 or less adds no number.
fn named(info: &PaperInfo, n: u32) -> String {
    let ids = &info.ids;
    let id = if let Some(p) = &ids.pmid {
        format!("PMID {p}")
    } else if let Some(d) = &ids.doi {
        format!("DOI {d}")
    } else if let Some(p) = &ids.pmcid {
        format!("PMCID {p}")
    } else {
        "no identifier".to_owned()
    };
    // NFC before any cut by characters, so NFD input (Swift, PDFKit) fits the same.
    let nfc = |s: &str| s.nfc().collect::<String>();
    let author = info.first_author.as_deref().map(nfc).map(|a| {
        python_strip(&a)
            .chars()
            .take(MAX_AUTHOR_CHARS_IN_NAME)
            .collect::<String>()
    });
    let year = info
        .year
        .filter(|y| (1000..=9999).contains(y))
        .map(|y| y.to_string());
    let head = [year, author]
        .into_iter()
        .flatten()
        .filter(|s| !s.is_empty());
    let head = head.collect::<Vec<_>>().join(" ");
    let title = info.title.as_deref().map(nfc).map_or(String::new(), |t| {
        cut_title(python_strip(&t), MAX_TITLE_CHARS_IN_NAME)
            .trim_end_matches(is_python_space)
            .to_owned()
    });
    let body = match (head.is_empty(), title.is_empty()) {
        (false, false) => format!("{head} – {title}"),
        (false, true) => head,
        (true, _) => title,
    };
    let body = sanitise(&body);
    let id = sanitise(&id);
    let mut suffix = if body.is_empty() {
        format!("({id})")
    } else {
        format!(" ({id})")
    };
    if n > 1 {
        suffix.push_str(&format!(" ({n})"));
    }
    // Length first, then the reserved-name check on the final name: a fit that
    // is reserved is redone one byte shorter so the `_` still keeps it in budget.
    let name = fit(&body, &suffix, MAX_FOLDER_NAME_BYTES);
    if is_reserved(&name) {
        return format!("_{}", fit(&body, &suffix, MAX_FOLDER_NAME_BYTES - 1));
    }
    name
}

/// True when the part before the first `.`, trailing spaces dropped, is a
/// Windows device name (`nul .txt` opens the device too).
pub(super) fn is_reserved(name: &str) -> bool {
    let base = name.split('.').next().unwrap_or("").trim_end_matches(' ');
    RESERVED.contains(&base.to_uppercase().as_str())
}

/// `body suffix` within `limit` bytes: the body is cut at a character
/// boundary; when the suffix alone does not fit, the whole name is.
fn fit(body: &str, suffix: &str, limit: usize) -> String {
    let name = format!("{body}{suffix}");
    if name.len() <= limit {
        return name;
    }
    if suffix.len() <= limit {
        let kept = cut_bytes(body, limit - suffix.len()).trim_end();
        let suffix = if kept.is_empty() {
            suffix.trim_start()
        } else {
            suffix
        };
        return format!("{kept}{suffix}");
    }
    cut_bytes(&name, limit)
        .trim_end_matches(['.', ' '])
        .to_owned()
}

#[cfg(test)]
mod tests {
    use super::may_name;
    use crate::library::PaperIds;

    /// Python's `test_may_name_reads_the_identifier_a_folder_name_ends_in`, case for case.
    #[test]
    fn may_name_reads_the_identifier_a_folder_name_ends_in() {
        let cases: [(&str, [Option<&str>; 3], bool); 12] = [
            ("2024 Smith – T (PMID 123)", [Some("123"), None, None], true),
            (
                "2024 Smith – T (PMID 123) (2)",
                [Some("123"), None, None],
                true,
            ),
            ("2024 Smith – T (pmid 123)", [Some("123"), None, None], true),
            ("(PMID 123)", [Some("123"), None, None], true),
            (
                "2024 Smith – T (PMID 1234)",
                [Some("123"), None, None],
                false,
            ),
            (
                "2024 Smith – T (PMID 123) x",
                [Some("123"), None, None],
                false,
            ),
            (
                "2024 Smith – T (PMID 123) ()",
                [Some("123"), None, None],
                false,
            ),
            (
                "2024 Smith – T (DOI 10.1 x)",
                [None, Some("10.1/x"), None],
                true,
            ),
            (
                "2024 Smith – T (DOI 10.1 x)",
                [Some("123"), Some("10.1/x"), None],
                true,
            ),
            (
                "2024 Smith – T (PMCID PMC9)",
                [None, None, Some("PMC9")],
                true,
            ),
            (
                "2024 Smith – T (PMID 123)",
                [None, Some("10.1/x"), None],
                false,
            ),
            ("2024 Smith – T (no identifier)", [None, None, None], false),
        ];
        for (folder, [pmid, doi, pmcid], expect) in cases {
            let ids = PaperIds::canonical(pmid, doi, pmcid).unwrap();
            assert_eq!(may_name(folder, &ids), expect, "{folder} {ids:?}");
        }
    }
}
