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

//! Locate an (LLM-copied) passage inside a source text — normalisation half.
//!
//! A port of the normalisation in `bmlib/citations/passage_match.py`. Text is
//! folded character by character (typographic quotes and dashes to ASCII,
//! NFKD, accents dropped, lowercase) and whitespace runs collapse to one
//! space. [`normalize_with_offsets`] keeps a map from every normalised `char`
//! back to the `char` of the original that produced it. **Offsets are
//! Unicode code-point (`char`) indices, never bytes**, matching Python's
//! string indices so the two implementations agree exactly.

use unicode_normalization::char::canonical_combining_class;
use unicode_normalization::UnicodeNormalization;

/// Typographic punctuation folded to a single ASCII character.
const TYPOGRAPHIC_FOLDS: &[(&str, char)] = &[
    ("\u{2018}\u{2019}\u{201a}\u{201b}\u{2032}\u{b4}`", '\''),
    ("\u{201c}\u{201d}\u{201e}\u{201f}\u{2033}\u{ab}\u{bb}", '"'),
    (
        "\u{2010}\u{2011}\u{2012}\u{2013}\u{2014}\u{2015}\u{2212}\u{fe58}\u{fe63}\u{ff0d}",
        '-',
    ),
];

/// Invisible characters dropped outright (soft hyphen, zero-width, BOM).
const INVISIBLE: &str = "\u{ad}\u{200b}\u{200c}\u{200d}\u{2060}\u{feff}";

/// Combining overlays that negate their base character ("≠" is "=" + U+0338);
/// kept so a negated relation never folds to its positive form.
const NEGATION_MARKS: [char; 3] = ['\u{338}', '\u{20d2}', '\u{20e5}'];

/// `str.isspace` as Python defines it: Unicode whitespace plus U+001C..U+001F.
fn is_py_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// The typographic fold for one character; `None` means "drop it".
fn typographic(c: char) -> Option<char> {
    if INVISIBLE.contains(c) {
        return None;
    }
    Some(
        TYPOGRAPHIC_FOLDS
            .iter()
            .find(|(from, _)| from.contains(c))
            .map_or(c, |&(_, to)| to),
    )
}

/// Fold one character to the form passages are compared in.
///
/// Typographic quotes and dashes become ASCII, compatibility characters are
/// decomposed (NFKD: "ﬁ" -> "fi", "²" -> "2"), accents and other combining
/// marks are dropped except negation overlays, whitespace is dropped, and the
/// result is lowercased. Returns an empty string for a character dropped
/// entirely.
pub fn fold_char(c: char) -> String {
    // Fold before NFKD ("´" would decompose to a bare accent) and after it
    // (NFKD turns "⁻" into U+2212 and "‴" into primes).
    let Some(first) = typographic(c) else {
        return String::new();
    };
    first
        .nfkd()
        .filter_map(typographic)
        .filter(|&cp| {
            (NEGATION_MARKS.contains(&cp) || canonical_combining_class(cp) == 0) && !is_py_space(cp)
        })
        .flat_map(char::to_lowercase)
        .collect()
}

/// `normalize_with_offsets` over chars: `(chars, offsets)` of equal length.
pub(crate) fn normalize_chars(text: &str) -> (Vec<char>, Vec<usize>) {
    let mut chars: Vec<char> = Vec::new();
    let mut offsets: Vec<usize> = Vec::new();
    let mut pending_space: Option<usize> = None;
    for (index, c) in text.chars().enumerate() {
        if is_py_space(c) {
            if pending_space.is_none() && !chars.is_empty() {
                pending_space = Some(index);
            }
            continue;
        }
        let folded = fold_char(c);
        if folded.is_empty() {
            continue;
        }
        if let Some(at) = pending_space.take() {
            chars.push(' ');
            offsets.push(at);
        }
        // Folding can yield several code points ("ﬁ" -> "fi"); map each back.
        for cp in folded.chars() {
            chars.push(cp);
            offsets.push(index);
        }
    }
    (chars, offsets)
}

/// Fold `text` and collapse whitespace, keeping a map back to it.
///
/// Leading and trailing whitespace is dropped and every internal whitespace
/// run becomes one space. `offsets[i]` is the `char` index in `text` of the
/// character that produced the `i`-th `char` of the normalised string (for a
/// collapsed run, its first character).
pub fn normalize_with_offsets(text: &str) -> (String, Vec<usize>) {
    let (chars, offsets) = normalize_chars(text);
    (chars.into_iter().collect(), offsets)
}

/// Whether `fragment` occurs exactly once in `text`, compared after
/// normalisation on both sides. Overlapping occurrences count; an empty
/// normalised fragment is never found.
pub fn occurs_once(fragment: &str, text: &str) -> bool {
    let (needle, _) = normalize_chars(fragment);
    let (haystack, _) = normalize_chars(text);
    if needle.is_empty() {
        return false;
    }
    let mut starts = haystack.windows(needle.len()).filter(|w| *w == needle);
    starts.next().is_some() && starts.next().is_none()
}
