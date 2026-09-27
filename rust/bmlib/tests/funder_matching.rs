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

//! The stated counts in `analyzer.rs` against the hand-labelled funder corpus.
//!
//! `funder_matcher.rs` answers *"does the port agree with Python on each of the
//! 417 labelled names?"*. This file answers the other question, and it is the
//! one the Python's `TestTheStatedCountsAreWhatTheCorpusHolds` answers: *"is the
//! evidence each membership decision cites actually in the corpus?"*
//!
//! Issue #112 is why both exist. The matcher's comments state a measurement as
//! the reason for each token's inclusion or exclusion, and those measurements —
//! not the token list — are what the next edit is checked against. Nothing
//! checked them, and **eight claims were wrong**, not by drift: the corpus has
//! one commit and the matcher was byte-identical, so the figures had been taken
//! against a revision that was never committed. They were internally coherent
//! (`0.917 = 11/12` and `0.324 = 11/34` describe one corpus holding 34 industry
//! names where the committed one held 30).
//!
//! **This is the half the name-agreement oracle cannot see.** Eight of the rows
//! below are tokens that were considered and *refused*; they are in no tuple, so
//! adding one back changes no measured count and — for the four two-character
//! forms the corpus holds no trace of — no name-agreement case either. The row
//! is the only record that the decision was made, and it is the only thing that
//! can catch the decision being unmade.
//!
//! The source is read at test time rather than restated here, and it is read
//! **fail-closed**: a moved block delimiter, a reformatted table or a doubled
//! row raises rather than returning a smaller map, because "I found nothing"
//! must never be an answer this can give — one reformat would otherwise turn
//! the whole file green at once.
//!
//! Three properties a first cut would miss, each mirrored from the Python:
//!
//! * **A row states its own membership (`in`/`out`) and the rule that decided
//!   it**, and both are checked against the tuples. Arithmetic alone was never
//!   the defect; #112 is a rule stated and not applied, and a cut that only
//!   re-derived counts stayed green while a row was moved into the refused
//!   block with its token still in `INDUSTRY_WORDS`.
//! * **The corpus's own size is asserted.** Every count here is a numerator;
//!   cutting the corpus to the names some documented token reaches would leave
//!   every row reproducing.
//! * **Per-token scoring goes through the matcher's own constructor**
//!   ([`compile_word_re`]), never a hand-written second copy of `\b…\b`:
//!   dropping the leading `\b` from a private copy moved four of the stated
//!   counts while the whole-name agreement control stayed green, because no
//!   corpus name disagreed.

use bmlib::transparency::analyzer::{
    compile_word_re, is_industry_funder, INDUSTRY_STEMS, INDUSTRY_WORDS,
};
use regex::Regex;
use serde_json::Value;
use std::collections::BTreeMap;
use std::path::PathBuf;

/// The vendored funder corpus, in the oracle's `expected` shape — every entry
/// carries the `name` and the hand-assigned `label`, which is all this file
/// reads. The `cases` half is a name list only, and `funder_matcher.rs` already
/// zips the two to diff Rust against Python.
const EXPECTED: &str = include_str!("data/funder_matcher_expected.json");

/// The module whose comments are the input under test.
const SOURCE: &str = "src/transparency/analyzer.rs";

/// The rows are scanned only between these markers. Unscoped, deleting the
/// whole table and leaving one stray matching line anywhere in the file would
/// read as a healthy parse of one row.
const BLOCK_START: &str = "// Substring stems.";
const BLOCK_END: &str = "pub fn compile_word_re(";

/// `//   "<token>"  <stem|word>  <in|out>  <N> TP / <M> FP  rule <R>`, with any
/// reason on indented continuation lines that deliberately match nothing.
const CLAIM_RE: &str = r#"^//\s+"(?P<token>[a-z ]+)"\s+(?P<kind>stem|word)\s+(?P<status>in|out)\s+(?P<tp>\d+) TP / (?P<fp>\d+) FP\s+rule (?P<rule>\d)\b"#;

/// Every token the block is expected to account for, as a floor rather than an
/// equality: a later row may be added without editing this list, but a row
/// silently vanishing — taking its claim out of the check with it — is what
/// this catches. Sized to the whole inventory, not to a canary sample.
const EXPECTED_CLAIMS: &[(&str, &str)] = &[
    ("pharmaceutic", "stem"),
    ("therapeutics", "stem"),
    ("laboratories", "stem"),
    ("pharma", "stem"),
    ("biotech", "stem"),
    ("key laboratory", "stem"),
    ("pharma", "word"),
    ("biotech", "word"),
    ("incorporated", "word"),
    ("inc", "word"),
    ("corp", "word"),
    ("limited", "word"),
    ("ltd", "word"),
    ("gmbh", "word"),
    ("llc", "word"),
    ("plc", "word"),
    ("pty", "word"),
    ("co", "word"),
    ("corporation", "word"),
    ("ag", "word"),
    ("bv", "word"),
    ("nv", "word"),
    ("sa", "word"),
    ("ab", "word"),
    ("labs", "word"),
];

/// One row of the table, as the source states it.
#[derive(Debug, Clone, PartialEq, Eq)]
struct Claim {
    tp: u32,
    fp: u32,
    status: String,
    rule: u32,
}

/// Read the module's own source.
fn source_text() -> String {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(SOURCE);
    std::fs::read_to_string(&path)
        .unwrap_or_else(|error| panic!("cannot read {}: {error}", path.display()))
}

/// Return `{(token, kind): Claim}` as the module's comments state it.
///
/// Fails closed in every direction: an unreadable source, a block whose
/// delimiters have moved, a table reformatted out of recognition, and a token
/// claimed twice for one kind all raise.
fn claims() -> BTreeMap<(String, String), Claim> {
    let text = source_text();
    let lines: Vec<&str> = text.lines().collect();
    let start = lines.iter().position(|line| *line == BLOCK_START);
    let end = lines.iter().position(|line| line.starts_with(BLOCK_END));
    let (Some(start), Some(end)) = (start, end) else {
        panic!(
            "the membership block in {SOURCE} is not delimited by {BLOCK_START:?} … \
             {BLOCK_END:?} any more, so nothing below is checking anything"
        );
    };
    assert!(
        start < end,
        "the membership block in {SOURCE} ends before it starts"
    );

    let re = Regex::new(CLAIM_RE).expect("the row pattern compiles");
    let mut claims: BTreeMap<(String, String), Claim> = BTreeMap::new();
    for line in &lines[start..end] {
        let Some(caps) = re.captures(line) else {
            continue;
        };
        let key = (caps["token"].to_string(), caps["kind"].to_string());
        let claim = Claim {
            tp: caps["tp"].parse().expect("digits"),
            fp: caps["fp"].parse().expect("digits"),
            status: caps["status"].to_string(),
            rule: caps["rule"].parse().expect("one digit"),
        };
        assert!(
            claims.insert(key.clone(), claim).is_none(),
            "{key:?} is claimed twice; one of the two is unchecked"
        );
    }
    assert!(
        !claims.is_empty(),
        "no count rows found in {SOURCE} — the table has moved or been reformatted, \
         so nothing below is checking anything"
    );
    claims
}

/// The non-ambiguous corpus entries, the population every count is over.
///
/// Ambiguous names are excluded from scoring, which is why a `0 TP / 0 FP` row
/// under rule 2 means "not scored" rather than "not present".
fn entries() -> Vec<(String, String)> {
    let parsed: Value = serde_json::from_str(EXPECTED).expect("the corpus parses");
    let entries = parsed.as_array().expect("the corpus is a list");
    entries
        .iter()
        .map(|entry| {
            (
                entry["name"]
                    .as_str()
                    .expect("every entry names a funder")
                    .to_string(),
                entry["label"]
                    .as_str()
                    .expect("every entry carries a label")
                    .to_string(),
            )
        })
        .filter(|(_, label)| label != "ambiguous")
        .collect()
}

/// Every entry, ambiguous included — the denominator the label counts are of.
fn all_labels() -> Vec<String> {
    let parsed: Value = serde_json::from_str(EXPECTED).expect("the corpus parses");
    parsed
        .as_array()
        .expect("the corpus is a list")
        .iter()
        .map(|entry| entry["label"].as_str().expect("labelled").to_string())
        .collect()
}

/// Report whether one token hits one name, the way its kind is matched.
///
/// The word branch borrows [`compile_word_re`] rather than rebuilding `\b…\b`,
/// because `INDUSTRY_WORD_RE` is one union over the whole tuple and so cannot
/// answer for a single token.
fn token_matches(token: &str, kind: &str, name: &str) -> bool {
    if kind == "stem" {
        return name.to_lowercase().contains(token);
    }
    compile_word_re(&[token]).is_match(name)
}

/// `(tp, fp)` for one token over the non-ambiguous corpus.
fn score_token(token: &str, kind: &str) -> (u32, u32) {
    let (mut tp, mut fp) = (0, 0);
    for (name, label) in entries() {
        if token_matches(token, kind, &name) {
            if label == "industry" {
                tp += 1;
            } else {
                fp += 1;
            }
        }
    }
    (tp, fp)
}

/// The tokens the matcher is actually using, as `(token, kind)`.
fn in_use() -> Vec<(String, String)> {
    let mut used: Vec<(String, String)> = INDUSTRY_STEMS
        .iter()
        .map(|stem| ((*stem).to_string(), "stem".to_string()))
        .collect();
    used.extend(
        INDUSTRY_WORDS
            .iter()
            .map(|word| ((*word).to_string(), "word".to_string())),
    );
    used
}

/// The denominator, which every row above is silently a numerator of.
///
/// Without this the corpus can be cut to the names some documented token
/// reaches — 417 entries to 45, the 372 negatives to 10 — and every count in
/// the table still reproduces. That is the #112 defect itself: figures
/// self-consistent with a corpus that is not the committed one.
///
/// The Python also asserts the corpus's `sampled` provenance block
/// (431 CrossRef + 402 PubMed drawn, 816 unique). That block is not in the
/// vendored copy — `dump_funder_matcher.py` writes the per-name results and not
/// the file's own metadata — so what is asserted here is the population the
/// counts are actually over. Copying the metadata across would add a fifth
/// place for it to drift from the corpus it describes.
#[test]
fn the_corpus_is_the_one_the_comments_describe() {
    let labels = all_labels();
    let count = |what: &str| labels.iter().filter(|label| *label == what).count();
    assert_eq!(
        labels.len(),
        417,
        "the corpus holds a different number of names"
    );
    assert_eq!(count("industry"), 35);
    assert_eq!(count("not_industry"), 372);
    assert_eq!(count("ambiguous"), 10);
    assert_eq!(
        labels.len() - count("ambiguous"),
        407,
        "the scoring population"
    );
    assert_eq!(entries().len(), 407);
}

/// A token cannot enter either tuple without bringing its counts.
///
/// The direction that matters: an undocumented token is one whose justification
/// was never measured, which is how `plc`/`pty` came to be excluded on a rule
/// four kept tokens do not satisfy either.
#[test]
fn the_table_accounts_for_every_token_in_use() {
    let claims = claims();
    let missing: Vec<(String, String)> = in_use()
        .into_iter()
        .filter(|key| !claims.contains_key(key))
        .collect();
    assert!(
        missing.is_empty(),
        "tokens in use with no stated counts: {missing:?}"
    );
}

/// The positive control: a row may be added, but none may quietly go.
#[test]
fn the_table_still_holds_every_claim_it_was_built_from() {
    let claims = claims();
    let missing: Vec<(String, String)> = EXPECTED_CLAIMS
        .iter()
        .map(|(token, kind)| ((*token).to_string(), (*kind).to_string()))
        .filter(|key| !claims.contains_key(key))
        .collect();
    assert!(
        missing.is_empty(),
        "rows the table was built from have gone: {missing:?}"
    );
}

/// The half arithmetic cannot check, and the half #112 was actually about.
///
/// A row saying `out` for a token the matcher uses, or `in` for one it does
/// not, is a comment asserting the opposite of the code. Counts alone never see
/// it: the tokens involved score 0 TP / 0 FP either way.
#[test]
fn every_row_agrees_with_the_tuples_about_membership() {
    let used = in_use();
    let wrong: Vec<(String, String)> = claims()
        .into_iter()
        .filter(|(key, claim)| (claim.status == "in") != used.contains(key))
        .map(|(key, claim)| (format!("{key:?}"), claim.status))
        .collect();
    assert!(
        wrong.is_empty(),
        "rows whose in/out disagrees with the tuples: {wrong:?}"
    );
}

/// Rule 4 only ever refuses; rules 2 and 3 only ever admit.
///
/// Rule 1 does both — it earns `pharmaceutic` and refuses `corporation` — so it
/// constrains nothing here and is deliberately not checked. The counts are what
/// hold rule 1 honest.
#[test]
fn the_rule_a_row_cites_could_have_decided_it() {
    let wrong: Vec<(String, u32, String)> = claims()
        .into_iter()
        .filter(|(_, claim)| {
            !(1..=4).contains(&claim.rule)
                || (claim.rule == 4 && claim.status != "out")
                || ((claim.rule == 2 || claim.rule == 3) && claim.status != "in")
        })
        .map(|(key, claim)| (format!("{key:?}"), claim.rule, claim.status))
        .collect();
    assert!(
        wrong.is_empty(),
        "rows citing a rule that cannot have decided them: {wrong:?}"
    );
}

/// Every row, named, re-derived against the corpus.
///
/// This includes rows [`EXPECTED_CLAIMS`] has never heard of: a row added with
/// an invented count would otherwise sit in the table unread, which is the very
/// shape #112 is about. A token cannot enter the table on a number nobody
/// measured any more than it can enter the tuple without one.
#[test]
fn no_row_at_all_disagrees_with_the_corpus() {
    let mut wrong: Vec<String> = Vec::new();
    for (key, claim) in claims() {
        let measured = score_token(&key.0, &key.1);
        if (claim.tp, claim.fp) != measured {
            wrong.push(format!(
                "  {} as a {}: stated {:?}, measured {measured:?}",
                key.0,
                key.1,
                (claim.tp, claim.fp)
            ));
        }
    }
    assert!(
        wrong.is_empty(),
        "{} rows disagree with the corpus:\n{}",
        wrong.len(),
        wrong.join("\n")
    );
}

/// The instrument's own control: per-token scoring must be the matcher.
///
/// Whole-name agreement over the corpus. Necessary but not sufficient on its
/// own — it can only speak for tokens some corpus name reaches, which is 14 of
/// the 25 rows — so `every_row_is_exercised_one_token_at_a_time` covers the
/// rest.
#[test]
fn the_scorer_agrees_with_the_matcher_it_mirrors() {
    let used = in_use();
    let (mut hit, mut missed) = (0, 0);
    for (name, _) in entries() {
        let by_parts = used
            .iter()
            .any(|(token, kind)| token_matches(token, kind, &name));
        assert_eq!(
            by_parts,
            is_industry_funder(&name),
            "{name:?} is scored differently by the parts than by the matcher"
        );
        if by_parts {
            hit += 1;
        } else {
            missed += 1;
        }
    }
    // The control is only an instrument if it can see both answers.
    assert!(
        hit > 0 && missed > 0,
        "the control saw {hit} hits and {missed} misses, so it discriminates nothing"
    );
}

/// Per-token control, including the ten rows no corpus name reaches.
///
/// `score_token` returns `(0, 0)` both for a token the corpus does not contain
/// and for a scorer that has stopped working, and ten rows claim `0 TP / 0 FP`
/// — so 40% of the table would otherwise be self-confirming. Synthetic probes
/// exercise every row one token at a time instead.
///
/// **`x{token}x` is the one that matters**: it separates a stem from a word,
/// which is what a dropped `\b` silently erased. A row's declared kind is
/// checked against the semantics the matcher actually gives it, so a token
/// labelled `word` that behaves as a stem — or a `compile_word_re` that lost its
/// boundary — fails here even though every count in the table still reproduces.
///
/// This is the Python's `test_the_mirror_scores_one_token_as_the_matcher_would`
/// reached from the other side. That version narrows the *module globals* and
/// asks the real matcher; this port has no process-global matcher to narrow and
/// will not widen its public surface to make one, so it asks the same question
/// of the token's own semantics. The defect the Python version was written for
/// is the one above, and it is caught here.
#[test]
fn every_row_is_exercised_one_token_at_a_time() {
    for (key, _) in claims() {
        let (token, kind) = (&key.0, &key.1);
        let probes = [
            token.clone(),
            token.to_uppercase(),
            title_case(token),
            format!("Acme {token} Group"),
        ];
        for probe in &probes {
            assert!(
                token_matches(token, kind, probe),
                "\"{token}\" as a {kind} does not match its own probe {probe:?}"
            );
        }
        // A word must not match inside a longer word; a stem must. This is the
        // assertion that fails for every word row if `\b` goes missing.
        assert_eq!(
            token_matches(token, kind, &format!("x{token}x")),
            kind == "stem",
            "\"{token}\" as a {kind} matches inside a longer word, so its declared \
             kind is not the semantics it gets"
        );
    }
}

/// `"key laboratory"` → `"Key Laboratory"`, the Python's `str.title()`.
fn title_case(text: &str) -> String {
    text.split(' ')
        .map(|word| {
            let mut chars = word.chars();
            match chars.next() {
                None => String::new(),
                Some(first) => first.to_uppercase().collect::<String>() + chars.as_str(),
            }
        })
        .collect::<Vec<String>>()
        .join(" ")
}
