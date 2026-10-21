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

//! Passage-matching tests, translated from `tests/test_passage_match.py`.
//!
//! The variant, negation and superscript folding tests are stated on
//! `normalize_with_offsets`, which is what decides "exact"; the span-based
//! folding tests and everything after them drive `find_passage`. Python's
//! `TestPassageMatchInvariants` has no counterpart: `PassageMatch` and
//! `PassageSpan` are `#[non_exhaustive]`, so only `find_passage` builds them
//! (and the property test checks every span it returns). Python's non-string
//! inputs cannot be expressed, and `TestFitsText`/`TestMaxEditPoints` are unit
//! tests in the crate, the two bounds being crate-private.

use std::time::Instant;

use bmlib::citations::passage_match::{
    find_passage, normalize_with_offsets, occurs_once, InvalidSimilarity, PassageMatch, PassageSpan,
};

// The crate's difflib port is crate-private; the exhaustive search compiles
// the same source so it scores windows exactly as `find_passage` does.
#[allow(dead_code)]
#[path = "../src/citations/sequence_matcher.rs"]
mod sequence_matcher;
use sequence_matcher::SequenceMatcher;

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

const SENTENCES: [&str; 7] = [
    "Background: Atrial fibrillation is the most common sustained arrhythmia and is \
     associated with a fivefold increase in the risk of stroke.",
    "Methods: We conducted a randomised controlled trial in 1,204 patients aged 65 \
     years or older across 14 centres in Europe between 2015 and 2019.",
    "Patients were assigned to apixaban 5 mg twice daily or to warfarin with a target \
     international normalised ratio of 2.0 to 3.0.",
    "The primary outcome was stroke or systemic embolism; the primary safety outcome \
     was major bleeding according to ISTH criteria.",
    "Results: Over a median follow-up of 2.1 years, stroke or systemic embolism \
     occurred in 1.27% per year in the apixaban group and 1.60% per year in the \
     warfarin group (hazard ratio 0.79; 95% CI 0.66 to 0.95).",
    "Major bleeding occurred in 2.13% per year with apixaban versus 3.09% per year \
     with warfarin.",
    "Conclusions: In elderly patients with atrial fibrillation, apixaban was superior \
     to warfarin in preventing stroke or systemic embolism, caused less bleeding and \
     resulted in lower mortality.",
];

/// Generous wall-clock bound for a fuzzy match on a ~10 kB text.
const FUZZY_TIME_BUDGET_SECONDS: f64 = 2.0;
const LONG_TEXT_REPEATS: usize = 10;

/// Seeded comparison against exhaustive search over word-boundary windows.
/// Python's `random.Random` is not reproduced; an inline LCG takes its seed.
const PROPERTY_SEED: u64 = 20261017;
const PROPERTY_TRIALS: usize = 60;
const PROPERTY_THRESHOLD: f64 = 0.95;
/// Allowed shortfall of find_passage's score against the exhaustive best.
const PROPERTY_SCORE_TOLERANCE: f64 = 0.01;

fn abstract_text() -> String {
    SENTENCES.join(" ")
}

fn long_text() -> String {
    (0..LONG_TEXT_REPEATS)
        .map(|i| format!("Section {i}. {}", SENTENCES.join(" ")))
        .collect::<Vec<_>>()
        .join(" ")
}

fn find(passage: &str, text: &str, min_similarity: f64) -> PassageMatch {
    find_passage(passage, text, min_similarity).expect("a valid threshold")
}

fn span(m: &PassageMatch) -> &PassageSpan {
    m.span.as_ref().expect("an accepted match")
}

fn matched_text(m: &PassageMatch) -> Option<&str> {
    m.span.as_ref().map(|s| s.text.as_str())
}

/// Python's `text[start:end]` over chars.
fn char_slice(text: &str, start: usize, end: usize) -> String {
    text.chars().skip(start).take(end - start).collect()
}

/// Python's `text.index(needle)`, in chars.
fn char_index(text: &str, needle: &str) -> usize {
    text[..text.find(needle).expect("needle present")]
        .chars()
        .count()
}

// TestUnicodeFolding: the span-based tests.

#[test]
fn span_keeps_trailing_nfd_accent() {
    // The passage ends on an accented letter that is decomposed in the source.
    let m = find(
        "served with caf\u{e9}",
        "Served with cafe\u{301}. Then tea.",
        0.95,
    );
    assert_eq!(matched_text(&m), Some("Served with cafe\u{301}"));
}

#[test]
fn no_exact_match_ends_inside_a_ligature() {
    // "The f" occurs in the folded "the final", but only by cutting "ﬁ".
    let m = find("The f", "The \u{fb01}nal result.", 0.95);
    assert!(!m.exact);
    assert!(!m.matched());
}

#[test]
fn returned_text_keeps_source_typography() {
    let sentence = "Mortality fell from 12% to 8% \u{2014} a \u{201c}marked\u{201d} drop.";
    let text = format!("Background text here. {sentence} More text follows.");
    let m = find(
        "Mortality fell from 12% to 8% - a \"marked\" drop.",
        &text,
        0.95,
    );
    assert_eq!(matched_text(&m), Some(sentence));
}

#[test]
fn fuzzy_match_over_folded_text_spans_whole_characters() {
    let text = "Intro. The \u{fb01}nal e\u{fb03}cacy was shown here today. End.";
    let m = find(
        "The final efficacy was clearly shown here today.",
        text,
        0.85,
    );
    assert!(m.matched());
    assert!(!m.exact);
    assert_eq!(
        matched_text(&m),
        Some("The \u{fb01}nal e\u{fb03}cacy was shown here today.")
    );
}

#[test]
fn span_keeps_nfd_accent_at_end_of_text() {
    let text = "Served with cafe\u{301}";
    let m = find("served with caf\u{e9}", text, 0.95);
    assert_eq!(matched_text(&m), Some(text));
}

// TestExactMatch

#[test]
fn verbatim() {
    let abstract_ = abstract_text();
    let m = find(SENTENCES[4], &abstract_, 0.95);
    assert!(m.matched());
    assert_eq!(m.similarity, 1.0);
    assert_eq!(matched_text(&m), Some(SENTENCES[4]));
    let s = span(&m);
    assert_eq!(char_slice(&abstract_, s.start, s.end), SENTENCES[4]);
}

#[test]
fn case_differences_return_original_case() {
    let m = find(&SENTENCES[0].to_lowercase(), &abstract_text(), 0.95);
    assert_eq!(m.similarity, 1.0);
    assert_eq!(matched_text(&m), Some(SENTENCES[0]));
}

#[test]
fn whitespace_differences_return_original_whitespace() {
    let text = "Type 2 diabetes   can often be\n managed through lifestyle changes.";
    let passage = "Type 2 diabetes can often be managed through lifestyle changes.";
    let m = find(passage, text, 0.95);
    assert_eq!(m.similarity, 1.0);
    assert_eq!(matched_text(&m), Some(text));
}

// TestFuzzyMatch

#[test]
fn single_edit_returns_source_sentence() {
    let passage = SENTENCES[4].replace("hazard ratio", "hazard-ratio");
    let m = find(&passage, &abstract_text(), 0.95);
    assert!(m.matched());
    assert!(0.95 <= m.similarity && m.similarity < 1.0);
    assert_eq!(matched_text(&m), Some(SENTENCES[4]));
}

#[test]
fn span_does_not_drift_late_in_text() {
    let passage = SENTENCES[6].replace("lower mortality", "lower  mortality;");
    let m = find(&passage, &abstract_text(), 0.95);
    assert!(m.matched());
    let text = &span(&m).text;
    assert!(text.starts_with("Conclusions:"));
    assert!(text.ends_with("mortality."));
}

#[test]
fn punctuation_change() {
    let text = "Aspirin reduces the risk of heart attack in patients with cardiovascular \
                disease, however it may increase bleeding risk.";
    let passage = text.replace("disease, however", "disease; however");
    let m = find(&passage, text, 0.95);
    assert!(m.matched());
    assert_eq!(matched_text(&m), Some(text));
}

#[test]
fn returned_text_is_from_source_not_passage() {
    let abstract_ = abstract_text();
    let passage = SENTENCES[2].replace("twice daily", "twice-daily");
    let m = find(&passage, &abstract_, 0.95);
    assert!(m.matched());
    let text = &span(&m).text;
    assert!(text.contains("twice daily"));
    assert!(abstract_.contains(text.as_str()));
}

#[test]
fn dropped_word_maps_to_full_source_sentence() {
    let passage = SENTENCES[4].replace("a median follow-up", "a follow-up");
    let m = find(&passage, &abstract_text(), 0.95);
    assert!(m.matched());
    assert_eq!(matched_text(&m), Some(SENTENCES[4]));
}

#[test]
fn inserted_word_maps_to_source_sentence() {
    let passage = SENTENCES[3].replace("major bleeding", "major clinical bleeding");
    let m = find(&passage, &abstract_text(), 0.95);
    assert!(m.matched());
    assert_eq!(matched_text(&m), Some(SENTENCES[3]));
}

#[test]
fn edit_in_first_word_of_text() {
    let passage = SENTENCES[0].replace("Background:", "Backgruond:");
    let m = find(&passage, &abstract_text(), 0.95);
    assert!(m.matched());
    assert_eq!(span(&m).start, 0);
    assert_eq!(matched_text(&m), Some(SENTENCES[0]));
}

#[test]
fn edit_in_last_word_of_text() {
    let abstract_ = abstract_text();
    let passage = SENTENCES[6].replace("lower mortality.", "lower mortalty.");
    let m = find(&passage, &abstract_, 0.95);
    assert!(m.matched());
    assert_eq!(span(&m).end, abstract_.chars().count());
    assert_eq!(matched_text(&m), Some(SENTENCES[6]));
}

#[test]
fn changed_final_punctuation_keeps_source_punctuation() {
    // Dropping the mismatched "." would score higher; spans end on word
    // boundaries so the citation keeps the source's full last word.
    let passage = SENTENCES[5].replace("warfarin.", "warfarin;");
    let m = find(&passage, &abstract_text(), 0.95);
    assert!(m.matched());
    assert_eq!(matched_text(&m), Some(SENTENCES[5]));
}

#[test]
fn fuzzy_match_does_not_start_mid_number() {
    // "3% per year with apixaban" only occurs inside "2.13% per year ...";
    // returning a span starting at "3%" would misquote the number.
    let m = find("3% per ear; with apixaban", &abstract_text(), 0.95);
    assert!(!m.matched());
}

#[test]
fn better_match_beats_location_with_more_seed_votes() {
    // The decoy repeats the passage's first 70% verbatim (more seed votes)
    // but ends differently; the source has scattered typos (fewer votes) yet
    // is the only span above the threshold.
    let passage = SENTENCES[4];
    let cut = (passage.chars().count() as f64 * 0.7) as usize;
    let decoy = format!(
        "{} an unrelated ending about placebo recipients.",
        char_slice(passage, 0, cut)
    );
    let source: String = passage
        .chars()
        .enumerate()
        .map(|(index, c)| {
            if index % 22 == 6 && index < cut && c != ' ' {
                'Q'
            } else {
                c
            }
        })
        .collect();
    let text = format!("{decoy} Filler sentence about something else entirely. {source}");
    let m = find(passage, &text, 0.95);
    assert!(m.matched());
    assert_eq!(span(&m).start, char_index(&text, &source));
    assert_eq!(matched_text(&m), Some(source.as_str()));
}

#[test]
fn unicode_whitespace_in_source() {
    let text = "Mortality was lower (p\u{2009}<\u{2009}0.05) with\u{a0}apixaban.";
    let m = find("(p < 0.05) with apixaban.", text, 0.95);
    assert!(m.exact);
    assert_eq!(
        matched_text(&m),
        Some("(p\u{2009}<\u{2009}0.05) with\u{a0}apixaban.")
    );
}

#[test]
fn no_edge_whitespace_around_fuzzy_span() {
    let text = "Intro.\n\nAspirin reduces the risk of heart attack in patients.\n\nOther.";
    let m = find(
        "Aspirin reduces the risk of heart attack in patient.",
        text,
        0.95,
    );
    assert!(m.matched());
    assert_eq!(
        matched_text(&m),
        Some("Aspirin reduces the risk of heart attack in patients.")
    );
}

#[test]
fn threshold_is_inclusive() {
    // One substitution in 20 characters: ratio is exactly 38/40 = 0.95.
    let text = "xxxxx abcdefghij klmnopqrs yyyyy";
    let m = find("abcdefghij klmnopqrZ", text, 0.95);
    assert!((m.similarity - 0.95).abs() < 1e-9, "{}", m.similarity);
    assert!(m.matched());
    assert!(!find("abcdefghij klmnopqrZ", text, 0.951).matched());
}

#[test]
fn fuzzy_match_is_not_exact() {
    let passage = SENTENCES[4].replace("hazard ratio", "hazard-ratio");
    assert!(!find(&passage, &abstract_text(), 0.95).exact);
    assert!(find(SENTENCES[4], &abstract_text(), 0.95).exact);
}

#[test]
fn long_passage_is_fast() {
    // The whole ~1 kB abstract, with two edits, against a ~10 kB text.
    let text = long_text();
    let passage = abstract_text()
        .replace("twice daily", "twice-daily")
        .replace("Major", "Majr");
    let started = Instant::now();
    let m = find(&passage, &text, 0.95);
    let elapsed = started.elapsed().as_secs_f64();
    eprintln!("long_passage_is_fast: {elapsed:.3} s");
    assert!(m.matched());
    assert!(elapsed < FUZZY_TIME_BUDGET_SECONDS, "{elapsed} s");
}

#[test]
fn long_non_matching_passage_is_fast() {
    let mut reversed = SENTENCES;
    reversed.reverse();
    let passage = reversed.join(" ");
    let started = Instant::now();
    let m = find(&passage, &abstract_text(), 0.95);
    let elapsed = started.elapsed().as_secs_f64();
    eprintln!("long_non_matching_passage_is_fast: {elapsed:.3} s");
    assert!(!m.matched());
    assert!(elapsed < FUZZY_TIME_BUDGET_SECONDS, "{elapsed} s");
}

#[test]
fn long_text_is_fast() {
    let text = long_text();
    let passage = SENTENCES[4].replace("hazard ratio", "hazard-ratio");
    let started = Instant::now();
    let m = find(&passage, &text, 0.95);
    let elapsed = started.elapsed().as_secs_f64();
    eprintln!("long_text_is_fast: {elapsed:.3} s");
    assert!(m.matched());
    assert_eq!(matched_text(&m), Some(SENTENCES[4]));
    assert!(elapsed < FUZZY_TIME_BUDGET_SECONDS, "{elapsed} s");
}

// TestRejection

#[test]
fn fabricated() {
    let m = find(
        "Statins are the most effective treatment for lowering cholesterol levels.",
        &abstract_text(),
        0.95,
    );
    assert!(!m.matched());
    assert!(m.span.is_none());
    assert!(m.similarity < 0.95);
}

#[test]
fn paraphrase() {
    let text = "Liver transplantation with living related donor has been recently \
                developed to compensate for the insufficient number of liver grafts \
                for children.";
    let passage = "Liver transplantation with living related donors has recently been \
                   developed to address the insufficient number of liver grafts for children";
    let m = find(passage, text, 0.95);
    assert!(!m.matched());
    assert!(m.similarity < 0.95);
}

#[test]
fn best_similarity_reported_on_rejection() {
    let passage = SENTENCES[4].replace("stroke or systemic embolism", "any thromboembolic event");
    let m = find(&passage, &abstract_text(), 0.95);
    assert!(!m.matched());
    assert!(
        0.5 < m.similarity && m.similarity < 0.95,
        "{}",
        m.similarity
    );
}

#[test]
fn degenerate_inputs() {
    let abstract_ = abstract_text();
    let cases = [
        ("", abstract_.as_str()),
        ("   ", abstract_.as_str()),
        ("some passage", ""),
        ("ab", "xyz"),
    ];
    for (passage, text) in cases {
        let m = find(passage, text, 0.95);
        assert!(m.span.is_none() && !m.exact, "{passage:?} in {text:?}");
        assert!(!m.matched());
    }
}

#[test]
fn passage_too_long_for_text_is_rejected_without_search() {
    let m = find(&abstract_text(), SENTENCES[5], 0.95);
    assert_eq!(
        (m.similarity, m.span.is_none(), m.exact),
        (0.0, true, false)
    );
}

// TestExactMatchesCutNothing

#[test]
fn a_cut_occurrence_is_not_exact() {
    let cases = [
        ("HR 0.9", "The HR 0.95 (95% CI 0.8-1.1) held."),
        ("5 mg daily", "Patients received 25 mg daily."),
        (
            "effective in reducing mortality",
            "The drug was ineffective in reducing mortality.",
        ),
        ("typical presentation", "It was an atypical presentation."),
        // Ends inside one source character: "≠" precomposed and decomposed.
        (
            "the difference was =",
            "So the difference was \u{2260} 0 here.",
        ),
        (
            "the difference was =",
            "So the difference was =\u{338} 0 here.",
        ),
        // Starts inside one: "½" folds to "1⁄2".
        ("2 dose was given", "A \u{bd} dose was given."),
    ];
    for (passage, text) in cases {
        let m = find(passage, text, 0.95);
        assert!(!m.exact, "{passage:?} in {text:?}");
        assert!(m.similarity < 1.0, "{passage:?} in {text:?}");
    }
}

#[test]
fn a_cut_occurrence_still_matches_fuzzily_where_close() {
    let m = find(
        "the difference was =",
        "So the difference was \u{2260} 0.",
        0.9,
    );
    assert!(m.matched() && !m.exact);
    assert_eq!(matched_text(&m), Some("the difference was \u{2260}"));
}

#[test]
fn a_later_whole_word_occurrence_is_exact() {
    let text = "It was ineffective at first, then effective at last.";
    let m = find("effective at", text, 0.95);
    assert!(m.exact);
    assert_eq!(
        (span(&m).start, span(&m).text.as_str()),
        (34, "effective at")
    );
}

#[test]
fn punctuation_next_to_the_edge_cuts_nothing() {
    let m = find("reduced mortality", "It (reduced mortality).", 0.95);
    assert!(m.exact);
    assert_eq!(matched_text(&m), Some("reduced mortality"));
}

// TestValidation

#[test]
fn threshold_out_of_range() {
    for threshold in [0.0, -0.1, 1.01, f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
        let err = find_passage("abc", "abc", threshold).unwrap_err();
        assert!(err.0.to_bits() == threshold.to_bits(), "{threshold}");
    }
    assert_eq!(
        InvalidSimilarity(1.01).to_string(),
        "min_similarity must be in (0, 1], got 1.01"
    );
}

// Module-level tests

#[test]
fn quote_across_paragraph_break_matches_exactly() {
    let text = "Results\n\nAmong patients aged 75 years or older,\n\nthe hazard ratio was 0.91.";
    let m = find(
        "Among patients aged 75 years or older, the hazard ratio was 0.91.",
        text,
        0.95,
    );
    assert!(m.exact);
    assert_eq!(
        matched_text(&m),
        Some("Among patients aged 75 years or older,\n\nthe hazard ratio was 0.91.")
    );
}

#[test]
fn repeated_quote_returns_first_occurrence() {
    let text = "Abstract. The effect was null. Discussion. The effect was null.";
    let m = find("The effect was null.", text, 0.95);
    assert_eq!(span(&m).start, char_index(text, "The effect was null."));
}

#[test]
fn elided_quote_is_rejected() {
    let first = "Patients were randomly assigned to drug X or warfarin.";
    let last = "Major bleeding occurred in 3.1% versus 2.2% of patients.";
    let filler = abstract_text().repeat(3); // about 3 000 characters between the two
    let text = format!("{first} {filler} {last}");
    assert!(!find(&format!("{first} \u{2026} {last}"), &text, 0.95).matched());
}

#[test]
fn information_separators_are_whitespace() {
    let m = find("hazard ratio", "the hazard\u{1e}ratio was low", 0.95);
    assert!(m.exact);
    assert_eq!(matched_text(&m), Some("hazard\u{1e}ratio"));
}

// Seeded comparison against exhaustive search.

/// Best similarity over every word-boundary window of admissible length.
fn exhaustive_best(passage: &str, text: &str, min_similarity: f64) -> f64 {
    let norm_passage: Vec<char> = normalize_with_offsets(passage).0.chars().collect();
    let norm_text: Vec<char> = normalize_with_offsets(text).0.chars().collect();
    let n = norm_text.len();
    let length = norm_passage.len() as f64;
    let starts: Vec<usize> = (0..n)
        .filter(|&i| norm_text[i] != ' ' && (i == 0 || norm_text[i - 1] == ' '))
        .collect();
    let ends: Vec<usize> = (1..=n)
        .filter(|&i| norm_text[i - 1] != ' ' && (i == n || norm_text[i] == ' '))
        .collect();
    let shortest = length * min_similarity / (2.0 - min_similarity);
    let longest = length * (2.0 - min_similarity) / min_similarity;
    let mut matcher = SequenceMatcher::new(&norm_passage);
    let mut best = 0.0_f64;
    for &start in &starts {
        for &end in &ends {
            let width = end as f64 - start as f64;
            if width < shortest {
                continue;
            }
            if width > longest {
                break;
            }
            matcher.set_a(&norm_text[start..end]);
            if matcher.quick_ratio() > best {
                best = best.max(matcher.ratio());
            }
        }
    }
    best
}

/// Minimal seeded generator (PCG multiplier/increment), standing in for
/// Python's `random.Random`.
struct Lcg(u64);

impl Lcg {
    fn next(&mut self) -> u64 {
        self.0 = self
            .0
            .wrapping_mul(6_364_136_223_846_793_005)
            .wrapping_add(1_442_695_040_888_963_407);
        self.0 >> 33
    }

    /// `randrange(n)`.
    fn below(&mut self, n: usize) -> usize {
        (self.next() % n as u64) as usize
    }

    /// `randint(low, high)`, inclusive.
    fn between(&mut self, low: usize, high: usize) -> usize {
        low + self.below(high - low + 1)
    }

    fn choice<T: Copy>(&mut self, items: &[T]) -> T {
        items[self.below(items.len())]
    }

    fn shuffle<T>(&mut self, items: &mut [T]) {
        for i in (1..items.len()).rev() {
            items.swap(i, self.below(i + 1));
        }
    }
}

/// Apply 1-3 character or word insertions/deletions/substitutions.
fn random_edits(passage: &str, rng: &mut Lcg) -> String {
    let substitutes: Vec<char> = "abcdefghijklmnopqrstuvwxyz,;".chars().collect();
    let letters = &substitutes[..26];
    let mut chars: Vec<char> = passage.chars().collect();
    for _ in 0..rng.between(1, 3) {
        let index = rng.below(chars.len());
        match rng.choice(&["substitute", "insert", "delete", "insert_word"]) {
            "substitute" => chars[index] = rng.choice(&substitutes),
            "insert" => chars.insert(index, rng.choice(letters)),
            "delete" if chars.len() > 2 => {
                chars.remove(index);
            }
            _ => {
                chars.splice(index..index, " clinical".chars());
            }
        }
    }
    chars.into_iter().collect()
}

#[test]
fn matches_exhaustive_search_on_random_edits() {
    // No missed matches, no wrong spans, near-best scores.
    let mut rng = Lcg(PROPERTY_SEED);
    let mut accepted = 0;
    for _ in 0..PROPERTY_TRIALS {
        let mut sentences = SENTENCES;
        rng.shuffle(&mut sentences);
        let text = sentences[..4].join(" ");
        let chars: Vec<char> = text.chars().collect();
        let mut word_starts = vec![0];
        word_starts.extend(
            chars
                .iter()
                .enumerate()
                .filter(|&(_, &c)| c == ' ')
                .map(|(i, _)| i + 1),
        );
        let start = rng.choice(&word_starts[..word_starts.len() - 10]);
        let stop = (start + rng.between(40, 200)).min(chars.len());
        let source: String = chars[start..stop].iter().collect();
        let passage = random_edits(&source, &mut rng);

        let best = exhaustive_best(&passage, &text, PROPERTY_THRESHOLD);
        let m = find(&passage, &text, PROPERTY_THRESHOLD);

        assert_eq!(m.matched(), best >= PROPERTY_THRESHOLD, "{passage:?}");
        if let Some(s) = &m.span {
            accepted += 1;
            assert_eq!(char_slice(&text, s.start, s.end), s.text);
            assert_eq!(s.text, s.text.trim());
            assert!(
                m.similarity >= best - PROPERTY_SCORE_TOLERANCE,
                "{passage:?}: {} vs {best}",
                m.similarity
            );
        }
    }
    eprintln!("property test: {accepted} of {PROPERTY_TRIALS} accepted");
}

#[test]
fn tiny_threshold_does_not_overflow() {
    // The edit bound is capped at P + T in both languages, so even a
    // subnormal threshold (an infinite quotient) is finite. Python: start 6,
    // end 26, similarity 0.95 for each.
    let text = "xxxxx abcdefghij klmnopqrs yyyyy";
    for threshold in [5e-324, 1e-310, 1e-300, 1e-17, 1e-5] {
        let m = find("abcdefghij klmnopqrZ", text, threshold);
        let s = span(&m);
        assert_eq!((s.start, s.end), (6, 26), "{threshold}");
        assert_eq!(m.similarity, 0.95, "{threshold}");
    }
}
