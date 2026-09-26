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

//! The PDF-title corroboration — the oracle and the named tests.
//!
//! The corpus (74 cases) diffs each function against Python's, including 40
//! normalisation cases chosen to exercise every transformation the rule claims to
//! absorb. The named tests state the two arguments the oracle cannot: why the
//! uncheckable cases are opposite, and why the junk backstop runs first.

use bmlib::fulltext::titles::{
    accepted_metadata_title, accepted_metadata_title_why, looks_like_junk, normalise,
    page_text_for_matching, TitleRefusal,
};
use serde_json::{json, Value};

const CASES: &str = include_str!("data/titles_cases.json");
const EXPECTED: &str = include_str!("data/titles_expected.json");

fn run(case: &Value) -> Value {
    let a = &case["args"];
    let text = || a["text"].as_str().unwrap_or_default();
    match case["fn"].as_str().unwrap_or_default() {
        "normalise" => json!(normalise(text())),
        "page_text_for_matching" => json!(page_text_for_matching(text())),
        "looks_like_junk" => json!(looks_like_junk(a["title"].as_str().unwrap_or_default())),
        "accepted_metadata_title" => {
            let title = a
                .get("metadata")
                .and_then(|m| m.get("title"))
                .and_then(Value::as_str);
            let page = a.get("page_one_text").and_then(Value::as_str);
            json!(accepted_metadata_title(title, page))
        }
        other => panic!("unknown fn {other:?}"),
    }
}

#[test]
fn the_port_agrees_with_python_on_every_case() {
    let cases: Value = serde_json::from_str(CASES).expect("cases parse");
    let expected: Value = serde_json::from_str(EXPECTED).expect("expected parse");
    let cases = cases.as_array().expect("cases is a list");
    let expected = expected.as_array().expect("expected is a list");
    assert_eq!(cases.len(), expected.len(), "regenerate the expectations");

    let mut failures: Vec<String> = Vec::new();
    for (case, want) in cases.iter().zip(expected.iter()) {
        let name = case["name"].as_str().unwrap_or_default();
        assert_eq!(name, want["name"].as_str().unwrap_or_default());
        assert!(
            want["ok"].as_bool().unwrap_or(false),
            "{name}: {}",
            want["error"]
        );
        let got = run(case);
        if got != want["value"] {
            failures.push(format!(
                "  {name}\n    python: {}\n    rust:   {}",
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
}

// ---------------------------------------------------------------------------
// Normalisation
// ---------------------------------------------------------------------------

/// **The transformations the rule claims to absorb**, each named: case, the
/// terminal period metadata drops, en-dash versus hyphen, ligatures, diacritics,
/// and the line break a wrapped title carries.
#[test]
fn normalisation_absorbs_every_stated_difference() {
    let same = [
        ("A Study of Things", "a study of THINGS"),
        ("A Study of Things.", "A Study of Things"),
        ("Dose\u{2013}response", "Dose-response"),
        ("Dose\u{2014}response", "Dose-response"),
        // A diacritic, precomposed and combining.
        ("Caf\u{e9} Study", "Cafe\u{301} Study"),
        ("M\u{fc}ller", "Muller"),
        ("Se\u{f1}or", "Senor"),
        ("\u{c5}ngstr\u{f6}m", "Angstrom"),
        // Compatibility characters, which NFKD *does* fold.
        ("\u{ff21}\u{ff22}\u{ff23}", "abc"),
        ("x\u{b2} study", "x2 study"),
    ];
    for (left, right) in same {
        assert_eq!(normalise(left), normalise(right), "{left:?} vs {right:?}");
    }
}

/// **It adds no folds of its own.** NFKD does *not* expand the ligatures — `æ`
/// stays `æ`, and `ß` stays `ß` — so a "helpful" `æ` → `ae` would be a spelling
/// the source tells apart, and one the ASCII-only token rule then splits in two.
/// The first cut of this file had exactly that fold, and the oracle refuted it.
#[test]
fn nfkd_does_not_expand_the_ligatures() {
    // The ligature is not folded, so it is not an ASCII token character, so it
    // splits the word it sits in.
    assert_eq!(normalise("H\u{e6}modynamic"), "h modynamic");
    assert_eq!(normalise("\u{153}sophageal"), "sophageal");
    assert_eq!(normalise("Stra\u{df}e"), "stra e");
    assert_eq!(normalise("K\u{f8}benhavn"), "k benhavn");
}

/// **A hyphen before a line break is typesetting, not spelling** — anchored on the
/// break, so an ordinary mid-line hyphen is left alone and `dose-response` stays
/// two tokens.
#[test]
fn only_a_break_hyphen_is_closed_up() {
    let joined = ["hydrau-\nlic", "hydrau-\r\nlic", "hydrau- \n lic"];
    for wrapped in joined {
        assert_eq!(
            normalise(wrapped),
            normalise("hydraulic"),
            "{wrapped:?} must close up"
        );
    }
    // **A soft hyphen does _not_ close the break**, and that is measured rather
    // than reasoned: the Python's class holds it, but the pattern then needs the
    // break to follow immediately, and it does not here — so the soft hyphen is
    // left in place, is not a token character, and splits the word. Reproduced.
    assert_eq!(normalise("hydrau-\u{ad}\nlic"), "hydrau lic");
    assert_eq!(normalise("hy\u{ad}phen"), "hy phen");
    // A mid-line hyphen is spelling and survives as a token separator.
    assert_eq!(normalise("dose-response"), "dose response");
    // As does a hyphen with no break after it.
    assert_eq!(normalise("dose- response"), "dose response");
}

/// **The four hyphen spellings are all recognised**, and the soft hyphen
/// especially is invisible — a class whose members cannot be seen is one a later
/// reader deletes as a duplicate of the plain `-`.
#[test]
fn all_four_hyphen_spellings_close_a_break() {
    for hyphen in ['\u{2d}', '\u{2010}', '\u{2011}', '\u{ad}'] {
        let wrapped = format!("hydrau{hyphen}\nlic");
        assert_eq!(
            normalise(&wrapped),
            "hydraulic",
            "U+{:04X} must close the break",
            hyphen as u32
        );
    }
}

/// **NFKD is taken from a crate rather than hand-rolled**, and this test is what
/// says so: these are the characters a guessed table got wrong.
///
/// The guess folded `Đ` to `D`, `Ð` to `D` and `Þ` to `th` — each an ASCII
/// equivalence that reads as obvious and is **not** what NFKD does. NFKD leaves
/// all three alone, so each is a separator under the ASCII-only token rule and
/// the token drops. The fold is data, not reasoning.
#[test]
fn the_fold_is_nfkd_and_not_a_guess() {
    // NFKD leaves `ł` alone, so it is not a word character and the token drops.
    assert_eq!(normalise("\u{142}\u{f3}d\u{17a}"), "odz");
    // And it leaves `Đ` alone: the guess's `D` was wrong.
    assert_eq!(normalise("\u{110}uro"), "uro");
    assert_eq!(normalise("\u{d0}ing"), "ing");
    assert_eq!(normalise("\u{de}or"), "or");
    // A character NFKD *does* fold, for contrast — `é` is a base plus a mark.
    assert_eq!(normalise("Caf\u{e9}"), "cafe");
    // **A non-ASCII letter is a separator, not a word character**, because the
    // token rule is `[a-z0-9]+` — so a Greek title keeps only its Latin words and
    // a wholly Cyrillic one normalises to nothing.
    assert_eq!(normalise("\u{3b1}\u{3b2}\u{3b3} study"), "study");
    assert_eq!(normalise("\u{416}\u{443}\u{440}\u{43d}\u{430}\u{43b}"), "");
    // Which means such a title is refused as **junk**, not as wordless: the
    // junk rule counts words and runs first. See the refusal test.
    assert!(normalise("\u{416}\u{443}\u{440}\u{43d}\u{430}\u{43b}").is_empty());
}

// ---------------------------------------------------------------------------
// Page text
// ---------------------------------------------------------------------------

/// **A line holding nothing but a short number is a line or page number**, and
/// joining it into the page text splices digits into the middle of a wrapped
/// title. Note the rule does not only *remove*: excising a line also **joins**
/// what sat either side of it, which is how the wrapped title matches.
#[test]
fn line_numbers_are_excised_and_their_neighbours_join() {
    let page = "virtually null\n1\nstomatal safety";
    assert_eq!(
        page_text_for_matching(page),
        "virtually null\n\nstomatal safety"
    );
    // And the join is what makes a wrapped title match.
    let title = "virtually null stomatal safety";
    assert!(accepted_metadata_title(Some(title), Some(page)).is_some());

    // A number with text on the line is not a line number.
    assert_eq!(page_text_for_matching("page\n12a\nmore"), "page\n12a\nmore");
    // Four digits is a page; a longer run is an accession or job number, and
    // removing it would let a title match with its distinguishing token gone.
    assert_eq!(page_text_for_matching("a\n1234\nb"), "a\n\nb");
    assert_eq!(page_text_for_matching("a\n12345\nb"), "a\n12345\nb");
    // Padded with spaces or tabs, still a line number.
    assert_eq!(page_text_for_matching("a\n  12  \nb"), "a\n\nb");
    assert_eq!(page_text_for_matching("a\n\t7\t\nb"), "a\n\nb");
}

/// **The corpus constrains none of this**: four independent mutations of the
/// digit bound change the answer on zero of 235 rows. So the bound is pinned
/// here, where a mutation *is* visible — do not read a green corpus run as having
/// checked it.
#[test]
fn the_digit_bound_is_pinned_by_a_test_not_by_the_corpus() {
    use bmlib::fulltext::titles::MAX_LINE_NUMBER_DIGITS;
    assert_eq!(
        MAX_LINE_NUMBER_DIGITS, 4,
        "the bound is a decision, not a detail"
    );
    for digits in 1..=MAX_LINE_NUMBER_DIGITS {
        let page = format!("a\n{}\nb", "1".repeat(digits));
        assert_eq!(page_text_for_matching(&page), "a\n\nb", "{digits} digits");
    }
    let over = "1".repeat(MAX_LINE_NUMBER_DIGITS + 1);
    let page = format!("a\n{over}\nb");
    assert_eq!(
        page_text_for_matching(&page),
        page,
        "one digit too many is kept"
    );
}

// ---------------------------------------------------------------------------
// The decision
// ---------------------------------------------------------------------------

/// **The two uncheckable cases are deliberately opposite.** A page read as
/// **empty** accepts the title — corroboration is then a test that could not be
/// run, and rejecting would blank the title of every image-only scan, where the
/// metadata is the only title signal there is. A page that **could not be read**
/// rejects it — that is a test that *failed*, not one that was inapplicable, and a
/// fault is not evidence.
#[test]
fn an_empty_page_accepts_and_an_unreadable_page_rejects() {
    let title = "A Perfectly Good Title";
    assert_eq!(
        accepted_metadata_title(Some(title), Some("")),
        Some(title.to_string()),
        "an image-only scan must keep its title"
    );
    assert_eq!(
        accepted_metadata_title(Some(title), None),
        None,
        "a fault is not evidence"
    );
    assert_eq!(
        accepted_metadata_title_why(Some(title), None),
        Err(TitleRefusal::PageUnreadable)
    );
    assert_eq!(
        accepted_metadata_title_why(Some(title), Some("")),
        Ok(title.to_string())
    );
}

/// **The junk backstop runs before page 1**, so a document that cannot be
/// corroborated at all does not thereby get a free pass for a shape already known
/// to be junk. The empty page accepts; the junk title is still refused.
#[test]
fn the_junk_backstop_survives_an_empty_page() {
    assert!(looks_like_junk("Nepal Journ"));
    assert_eq!(
        accepted_metadata_title(Some("Nepal Journ"), Some("")),
        None,
        "an unrunnable check must not be a free pass"
    );
    assert_eq!(
        accepted_metadata_title_why(Some("Nepal Journ"), Some("")),
        Err(TitleRefusal::JunkShape)
    );
    // And the same title on a page that really does print it is still refused.
    assert_eq!(
        accepted_metadata_title(Some("Nepal Journ"), Some("Nepal Journ here")),
        None
    );

    use bmlib::fulltext::titles::MIN_TITLE_WORDS;
    assert_eq!(MIN_TITLE_WORDS, 3);
    assert!(looks_like_junk("Malaria"));
    assert!(!looks_like_junk("A Study Of Things"));
    // **A wordless title is refused as junk, not as wordless**, because the junk
    // rule counts words and runs first. `Wordless` is reachable only for a title
    // that cleared the word count and then normalised to nothing — see the test
    // below, which is the shape that does reach it.
    assert_eq!(
        accepted_metadata_title_why(Some("--- ... ---"), Some("--- ... ---")),
        Err(TitleRefusal::JunkShape)
    );
    assert!(normalise("--- ... ---").is_empty());
}

/// **Containment is anchored**, so a title is not "printed on page 1" merely
/// because it is a prefix of a longer word there. The corpus's own one earned
/// junk row — a truncated journal name page 1 really prints — is rejected on this
/// alone.
#[test]
fn containment_is_anchored() {
    let page = "Study Of Things";
    assert!(accepted_metadata_title(Some("Study Of Things"), Some(page)).is_some());
    assert_eq!(
        accepted_metadata_title(Some("Study Of Thing"), Some(page)),
        None,
        "a prefix of a word is not the word"
    );
    assert_eq!(
        accepted_metadata_title(Some("Tudy Of Things"), Some(page)),
        None
    );
    assert_eq!(
        accepted_metadata_title_why(Some("Tudy Of Things"), Some(page)),
        Err(TitleRefusal::NotPrinted)
    );
}

/// Every refusal has its own reason, so the human debugging why a title vanished
/// from one PDF can tell them apart — the return type deliberately collapses
/// them, and the enum is what a caller logs.
#[test]
fn every_refusal_names_its_reason() {
    let page = Some("A Perfectly Good Title");
    assert_eq!(
        accepted_metadata_title_why(None, page),
        Err(TitleRefusal::Absent)
    );
    assert_eq!(
        accepted_metadata_title_why(Some("   "), page),
        Err(TitleRefusal::Absent)
    );
    assert_eq!(
        accepted_metadata_title_why(Some("Nepal Journ"), page),
        Err(TitleRefusal::JunkShape)
    );
    // A title holding no word characters is refused as **junk** — three words is
    // what the junk rule counts, and punctuation counts as none. `Wordless` is
    // for a shape that clears the count and then normalises away, which needs a
    // token the count sees and the normaliser does not: a title of digits that
    // NFKD folds to nothing does not exist, so the reachable case is a title
    // whose words are all non-ASCII.
    assert_eq!(
        accepted_metadata_title_why(Some("--- --- ---"), page),
        Err(TitleRefusal::JunkShape)
    );
    assert_eq!(
        accepted_metadata_title_why(Some("\u{416}\u{443}\u{440}\u{43d}\u{430}\u{43b} \u{416}\u{443}\u{440}\u{43d}\u{430}\u{43b} \u{416}\u{443}\u{440}"), page),
        Err(TitleRefusal::JunkShape),
        "a wholly non-ASCII title is junk too, since it counts as no words"
    );
    assert_eq!(
        accepted_metadata_title_why(Some("A Perfectly Good Title"), page),
        Ok("A Perfectly Good Title".to_string())
    );
    // And the wrapper returns exactly what the explained form does.
    assert_eq!(
        accepted_metadata_title(Some("A Perfectly Good Title"), page),
        Some("A Perfectly Good Title".to_string())
    );
}

/// The returned title is the **document's own spelling**, stripped of surrounding
/// whitespace — not the normalised form. A caller displaying it must see what the
/// file said, diacritics and all.
#[test]
fn the_returned_title_is_the_documents_own_spelling() {
    let title = "  Caf\u{e9} Study Of Things  ";
    assert_eq!(
        accepted_metadata_title(Some(title), Some("Cafe\u{301} Study Of Things")),
        Some("Caf\u{e9} Study Of Things".to_string())
    );
}

// ---------------------------------------------------------------------------
// The enums, against Python's own values
// ---------------------------------------------------------------------------

/// **Every `SectionType` member and wire value is Python's**, compared as data —
/// a hand-transcribed enum drifts, and a drifted member classifies a section
/// differently while every other test still passes.
#[test]
fn the_section_type_members_are_pythons() {
    // `(Python member name, Python wire value)`, from
    // `[(m.name, m.value) for m in SectionType]`.
    let pairs: &[(&str, &str)] = &[
        ("TITLE", "title"),
        ("ABSTRACT", "abstract"),
        ("INTRODUCTION", "introduction"),
        ("BACKGROUND", "background"),
        ("METHODS", "methods"),
        ("RESULTS", "results"),
        ("DISCUSSION", "discussion"),
        ("CONCLUSION", "conclusion"),
        ("ACKNOWLEDGMENTS", "acknowledgments"),
        ("REFERENCES", "references"),
        ("SUPPLEMENTARY", "supplementary"),
        ("APPENDIX", "appendix"),
        ("FUNDING", "funding"),
        ("CONFLICTS", "conflicts"),
        ("DATA_AVAILABILITY", "data_availability"),
        ("AUTHOR_CONTRIBUTIONS", "author_contributions"),
        ("FRONT_MATTER", "front_matter"),
        ("UNKNOWN", "unknown"),
    ];
    for (member, wire) in pairs {
        let parsed = bmlib::fulltext::models::SectionType::parse(wire)
            .unwrap_or_else(|e| panic!("{wire} must parse: {e}"));
        assert_eq!(parsed.as_str(), *wire, "{member} ({wire}) must round-trip");
    }
    assert_eq!(
        pairs.len(),
        bmlib::fulltext::models::SectionType::ALL.len(),
        "the port must not carry an extra or missing member"
    );
    assert_eq!(pairs.len(), 18, "Python's SectionType has 18 members");
    // An unknown value is refused by name rather than defaulting.
    let err = bmlib::fulltext::models::SectionType::parse("not-a-section").expect_err("refused");
    assert!(err.contains("not-a-section"), "{err}");
}

/// **Every `ContentKind`** — a Python `Literal`, so its members are the strings
/// themselves — and the default is the `none` one.
#[test]
fn the_content_kind_members_are_pythons() {
    let members: &[&str] = &["none", "abstract", "extracted", "fulltext"];
    for wire in members {
        let parsed = bmlib::fulltext::models::ContentKind::parse(wire)
            .unwrap_or_else(|e| panic!("{wire} must parse: {e}"));
        assert_eq!(parsed.as_str(), *wire);
    }
    assert_eq!(members.len(), 4, "Python's ContentKind has four members");
    assert_eq!(
        bmlib::fulltext::models::ContentKind::default().as_str(),
        "none",
        "the default must be the `none` member, not `fulltext`"
    );
}
