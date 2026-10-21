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

//! Passage-matching tests: normalisation with an offset map and Unicode folding.
//!
//! Translated from `TestNormalizeWithOffsets` and `TestUnicodeFolding` in
//! `tests/test_passage_match.py`. Python's folding tests drive `find_passage`
//! (a later task); here each is stated on `normalize_with_offsets`, which is
//! what decides "exact".

use bmlib::citations::passage_match::{normalize_with_offsets, occurs_once};

fn norm(text: &str) -> String {
    normalize_with_offsets(text).0
}

fn chars_at(original: &str, offsets: &[usize]) -> Vec<char> {
    let chars: Vec<char> = original.chars().collect();
    offsets.iter().map(|&i| chars[i]).collect()
}

#[test]
fn collapses_and_strips_whitespace() {
    let (n, offsets) = normalize_with_offsets("  Foo \n\t Bar  ");
    assert_eq!(n, "foo bar");
    assert_eq!(offsets.len(), n.chars().count());
}

#[test]
fn offsets_point_into_original() {
    let original = "A  B\nC";
    let (n, offsets) = normalize_with_offsets(original);
    assert_eq!(n, "a b c");
    assert_eq!(chars_at(original, &offsets), vec!['A', ' ', 'B', '\n', 'C']);
}

#[test]
fn length_changing_fold_keeps_offsets_aligned() {
    let (n, offsets) = normalize_with_offsets("\u{fb01}rst x");
    assert_eq!(n, "first x");
    assert_eq!(offsets[0], 0);
    assert_eq!(offsets[1], 0);
    assert_eq!(offsets[2], 1);
}

#[test]
fn offsets_are_char_indices_not_bytes() {
    // "é" is two bytes; the "b" after it is char index 2.
    let (n, offsets) = normalize_with_offsets("\u{e9} b");
    assert_eq!(n, "e b");
    assert_eq!(offsets, vec![0, 1, 2]);
}

#[test]
fn dotted_capital_i_folds_to_plain_i() {
    assert_eq!(norm("\u{130}stanbul"), "istanbul");
}

#[test]
fn empty() {
    assert_eq!(normalize_with_offsets("   "), (String::new(), vec![]));
}

#[test]
fn variants_match_exactly() {
    let cases = [
        (
            "Caf\u{e9} au lait was served.",
            "Cafe\u{301} au lait was served.",
        ),
        (
            "Cafe\u{301} au lait was served.",
            "Caf\u{e9} au lait was served.",
        ),
        (
            "The patient's \"dose\" was low.",
            "The patient\u{2019}s \u{201c}dose\u{201d} was low.",
        ),
        (
            "A dose-response effect was seen.",
            "A dose\u{2013}response effect was seen.",
        ),
        (
            "Effects were seen - not large.",
            "Effects were seen \u{2014} not large.",
        ),
        (
            "The final efficacy was shown.",
            "The \u{fb01}nal e\u{fb03}cacy was shown.",
        ),
        (
            "Areas of 5 m2 were treated.",
            "Areas of 5 m\u{b2} were treated.",
        ),
        (
            "The treatment was effective.",
            "The treat\u{ad}ment was effec\u{200b}tive.",
        ),
    ];
    for (passage, text) in cases {
        assert_eq!(norm(passage), norm(text), "{passage:?} vs {text:?}");
    }
}

#[test]
fn negated_relation_is_not_an_exact_match() {
    // "≠" is "=" + U+0338; dropping the overlay would flip the meaning.
    assert_ne!(
        norm("The groups differed: A = B."),
        norm("The groups differed: A \u{2260} B.")
    );
    assert_ne!(
        norm("The value x was in S."),
        norm("The value x was \u{2209} S.")
    );
}

#[test]
fn superscript_minus_folds_to_hyphen() {
    // NFKD turns "⁻" into U+2212, which must fold on to "-".
    assert_eq!(
        norm("Doses of 10-3 M were used."),
        norm("Doses of 10\u{207b}\u{b3} M were used.")
    );
}

#[test]
fn occurs_once_counts_copies_the_way_find_passage_compares() {
    let cases = [
        ("stroke", "Aspirin reduced stroke.", true),
        ("stroke", "Stroke fell. Aspirin reduced stroke.", false),
        (
            "risk was higher",
            "Risk was\nhigher. The risk  was higher.",
            false,
        ),
        (
            "risk was higher",
            "Risk was hi\u{ad}gher. Risk was higher.",
            false,
        ),
        ("aa", "aaa", false),
        ("heart", "Aspirin reduced stroke.", false),
        ("\u{200b} ", "Aspirin reduced stroke.", false),
        ("\u{e9}", "caf\u{e9}", true),
    ];
    for (fragment, text, expected) in cases {
        assert_eq!(
            occurs_once(fragment, text),
            expected,
            "{fragment:?} in {text:?}"
        );
    }
}
