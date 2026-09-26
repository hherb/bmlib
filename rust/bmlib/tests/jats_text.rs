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

//! The JATS text primitives — the oracle and the named tests.
//!
//! The corpus (74 cases) diffs each primitive against Python's. The named tests
//! state the measured rules the oracle cannot: why the two document markers are
//! read independently, and why the re-delimiting rule runs one way only.

use bmlib::fulltext::jats_text::{
    delimiter_pair, elocation_part_continues, latex_expression, normalize_whitespace,
    pad_as_deposited, pad_row, render_formula, without_whitespace,
};
use serde_json::{json, Value};

const CASES: &str = include_str!("data/jats_text_cases.json");
const EXPECTED: &str = include_str!("data/jats_text_expected.json");

fn strings(value: &Value) -> Vec<String> {
    value
        .as_array()
        .map(|a| {
            a.iter()
                .map(|v| v.as_str().unwrap_or_default().to_string())
                .collect()
        })
        .unwrap_or_default()
}

fn run(case: &Value) -> Value {
    let fn_name = case["fn"].as_str().unwrap_or_default();
    let a = &case["args"];
    match fn_name {
        "without_whitespace" => json!(without_whitespace(a["text"].as_str().unwrap_or_default())),
        "normalize_whitespace" => {
            json!(normalize_whitespace(a["text"].as_str().unwrap_or_default()))
        }
        "elocation_part_continues" => json!(elocation_part_continues(
            a["buffer"].as_str().unwrap_or_default(),
            a["joined"].as_str().unwrap_or_default(),
            a["citation_element"].as_str().unwrap_or_default(),
        )),
        "delimiter_pair" => match delimiter_pair(a["body"].as_str().unwrap_or_default()) {
            Some((opening, closing)) => json!([opening, closing]),
            None => Value::Null,
        },
        "latex_expression" => json!(latex_expression(
            a["deposit"].as_str().unwrap_or_default(),
            a["display"].as_bool().unwrap_or(false),
        )),
        "pad_as_deposited" => json!(pad_as_deposited(
            a["rendered"].as_str().unwrap_or_default(),
            a["buffered"].as_str().unwrap_or_default(),
            a["display"].as_bool().unwrap_or(false),
        )),
        "render_formula" => json!(render_formula(
            &strings(&a["latex"]),
            a["buffered"].as_str().unwrap_or_default(),
            a.get("alt_text")
                .and_then(Value::as_str)
                .unwrap_or_default(),
            a.get("label").and_then(Value::as_str).unwrap_or_default(),
            a["display"].as_bool().unwrap_or(false),
            a.get("numbered").and_then(Value::as_bool).unwrap_or(false),
        )),
        "pad_row" => json!(pad_row(
            strings(&a["row"]),
            a["count"].as_u64().unwrap_or(0) as usize
        )),
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
// The document wrapper
// ---------------------------------------------------------------------------

/// **The two markers are read independently**, because a deposit carrying one of
/// them fails closed.
///
/// Requiring both let a truncated deposit fall through to the bare-expression
/// path, which then delimited the *preamble* and merged it into the prose —
/// `$$\documentclass…\begin{document}$$E=mc^2$$`, the exact outcome this function
/// exists to prevent, **plus** the doubled pair the delimiter rule exists to
/// prevent.
#[test]
fn either_document_marker_alone_recovers_the_expression() {
    let wide = "\\documentclass[12pt]{minimal}\\usepackage{amsmath}\
                \\begin{document}E=mc^2\\end{document}";
    assert_eq!(latex_expression(wide, false), "$E=mc^2$");

    // Only the opener: everything after it is the body.
    assert_eq!(
        latex_expression("\\documentclass{x}\\begin{document}E=mc^2", false),
        "$E=mc^2$"
    );
    // Only the closer: everything before it is the body.
    assert_eq!(latex_expression("E=mc^2\\end{document}", false), "$E=mc^2$");

    // **A preamble with no marker at all is not preamble to this function.**
    // It drops what lies *outside* the markers, and a deposit carrying neither
    // is read as a bare expression and delimited — the 300 characters included.
    // That is the source's behaviour, and the reason the two markers are tested
    // independently rather than the preamble being recognised by its opening.
    assert_eq!(
        latex_expression("\\documentclass[12pt]{minimal}", false),
        "$\\documentclass[12pt]{minimal}$"
    );
}

/// **The depositor's own delimiters are kept, except where they would put a
/// sentence into display mode.** 96% of bodies already carry `$$…$$`, so adding a
/// pair unconditionally gives `$$$$…$$$$`.
#[test]
fn a_display_pair_on_an_inline_formula_is_respelled() {
    // The measured case: 98.6% of *inline* bodies carry `$$…$$`, and inline
    // formulas cannot genuinely be 98.6% display math. Left verbatim it rendered
    // `'×'` as `'$$\times$$'` inside a figure caption.
    assert_eq!(latex_expression("$$\\times$$", false), "$\\times$");
    // On a display formula the same pair is correct and stays.
    assert_eq!(latex_expression("$$\\times$$", true), "$$\\times$$");
    // A single-dollar pair is already inline, so it stays either way.
    assert_eq!(latex_expression("$x$", false), "$x$");
    assert_eq!(latex_expression("$x$", true), "$x$");
}

/// **The rule is one-directional**: an inline pair on a *display* formula is left
/// alone. A display delimiter inside a sentence breaks the line — wrong markup —
/// while an inline delimiter on a formula that stands alone merely under-styles
/// it, and re-spelling that way would be **inventing a claim** rather than reading
/// one.
#[test]
fn the_redelimiting_rule_runs_one_way_only() {
    // Display formula, inline pair: left alone.
    assert_eq!(latex_expression("$x$", true), "$x$");
    // Inline formula, display pair: respelled.
    assert_eq!(latex_expression("$$x$$", false), "$x$");
    // Inline formula, bracket display pair: respelled too.
    assert_eq!(latex_expression("\\[x\\]", false), "$x$");
}

/// A body carrying **several** delimited runs is left alone: its outer characters
/// are not one pair around one expression, and stripping them would corrupt it.
/// An **environment** is left alone for the same reason — it establishes its own
/// math mode, and `$$\begin{equation}…` is not valid LaTeX.
#[test]
fn several_runs_and_environments_are_left_alone() {
    assert_eq!(latex_expression("$$a$$ + $$b$$", false), "$$a$$ + $$b$$");
    assert_eq!(
        latex_expression("\\begin{aligned}x\\end{aligned}", false),
        "\\begin{aligned}x\\end{aligned}"
    );
    assert_eq!(
        latex_expression("\\begin{equation}x\\end{equation}", true),
        "\\begin{equation}x\\end{equation}"
    );
    // An empty pair is not one expression either.
    assert_eq!(latex_expression("$$$$", false), "$$$$");
}

/// `$$` is tested **before** `$`, because the shorter is a prefix of the longer:
/// testing `$` first would read `$$x$$` as the pair `$…$` with the body `$x$`.
#[test]
fn the_longer_delimiter_is_tested_first() {
    assert_eq!(delimiter_pair("$$x$$"), Some(("$$", "$$")));
    assert_eq!(delimiter_pair("$x$"), Some(("$", "$")));
    assert_eq!(delimiter_pair("\\[x\\]"), Some(("\\[", "\\]")));
    assert_eq!(delimiter_pair("\\(x\\)"), Some(("\\(", "\\)")));
    // A pair needs room for both halves.
    assert_eq!(delimiter_pair("$"), None);
    assert_eq!(delimiter_pair(""), None);
    assert_eq!(delimiter_pair("$$x"), None);
}

// ---------------------------------------------------------------------------
// Locators
// ---------------------------------------------------------------------------

/// **Whitespace is judged by the spelling**, because the two spellings mean
/// different things by it: in a `<mixed-citation>` it is typeset text, so `e1`
/// and `e2` printed `e1 e2` are two locators and not `e1e2`; an
/// `<element-citation>` is element-only, so the whitespace between its children is
/// insignificant indentation and cannot part them.
#[test]
fn whitespace_parts_a_locator_in_one_spelling_only() {
    // Mixed: whitespace breaks the join.
    assert!(elocation_part_continues("e1e2", "e1e2", "mixed-citation"));
    assert!(!elocation_part_continues("e1 e2", "e1e2", "mixed-citation"));
    // A part's own trailing whitespace does not, because the buffer is trimmed.
    assert!(elocation_part_continues("e1e2 ", "e1e2", "mixed-citation"));
    // A part's own *inner* whitespace is on both sides and so survives.
    assert!(elocation_part_continues(
        "quiz 380",
        "quiz 380",
        "mixed-citation"
    ));
    // Element: whitespace is indentation and cannot part them.
    assert!(elocation_part_continues(
        "e1 e2",
        "e1e2",
        "element-citation"
    ));
    assert!(elocation_part_continues(
        "e1\n  e2",
        "e1e2",
        "element-citation"
    ));
    // But a real character between them still does.
    assert!(!elocation_part_continues(
        "e1X e2",
        "e1e2",
        "element-citation"
    ));
}

// ---------------------------------------------------------------------------
// Spacing and the formula
// ---------------------------------------------------------------------------

/// **A run's edge whitespace is re-emitted outside its markers**: normalisation
/// would lose the separation the publisher put *inside* the element, which
/// measured over the 880-article corpus welded `'EndMatrix represents'` into one
/// word. A **display** formula has no spacing to keep — it is a block — so it gets
/// one space either side, the least that can be invented and still not join two
/// expressions into one.
#[test]
fn formula_spacing_follows_the_deposit_then_the_block_rule() {
    assert_eq!(pad_as_deposited("k", " k", false), " k");
    assert_eq!(pad_as_deposited("k", "k ", false), "k ");
    assert_eq!(pad_as_deposited("k", " k ", false), " k ");
    assert_eq!(pad_as_deposited("k", "k", false), "k");
    assert_eq!(pad_as_deposited("k", "\nk", false), " k");
    // A display formula is padded even when the deposit had nothing.
    assert_eq!(pad_as_deposited("k", "k", true), " k ");
}

/// **The first deposit that renders to anything wins, and the buffer is reached
/// whenever none does.** Testing the LaTeX list for *presence* rather than for a
/// rendition let an empty or preamble-only `<tex-math>` suppress a perfectly good
/// MathML flattening, so `'Before Vmax after.'` became `'Before after.'`.
#[test]
fn the_first_rendering_deposit_wins_and_the_buffer_is_the_fallback() {
    // The first renders, the second would too: the first wins rather than both
    // being joined, which is the defect the whole design prevents. Note the
    // **single** dollars — each deposit goes through `latex_expression`, so a
    // display pair on an inline formula is respelled.
    assert_eq!(
        render_formula(
            &["$$a$$".to_string(), "$$b$$".to_string()],
            "flat",
            "",
            "",
            false,
            false
        ),
        "$a$"
    );
    // The first is empty: the second renders, rather than the empty one
    // suppressing it.
    assert_eq!(
        render_formula(
            &["".to_string(), "$$b$$".to_string()],
            "flat",
            "",
            "",
            false,
            false
        ),
        "$b$"
    );
    // And a bare deposit on a display formula keeps the display delimiters.
    assert_eq!(
        render_formula(&["E=mc^2".to_string()], "flat", "", "", true, false),
        "$$E=mc^2$$"
    );
    // None renders: the buffer.
    assert_eq!(
        render_formula(
            &["".to_string(), "  ".to_string()],
            " flat ",
            "",
            "",
            false,
            false
        ),
        "flat"
    );
    // Neither renders: the alt text, which is not nothing.
    assert_eq!(
        render_formula(&[], "   ", "an image", "", false, false),
        "an image"
    );
    // Nothing at all renders as nothing — not as a bare label.
    assert_eq!(render_formula(&[], "   ", "", "", false, false), "");
    assert_eq!(render_formula(&[], "   ", "", "(1)", false, true), "");
}

/// **The equation number is printed only where a number is what the reader would
/// read.** Merged into a sentence it is not: the 880-article corpus produced
/// `'as shown in eqn (2):2 τ = kn'`, where the label reads as a coefficient, and
/// `'NH3 + H2O → NH4+ + OH−2 Al3+ + 3OH− → Al(OH)33'`, where each number welds
/// onto the previous formula's tail and **changes the chemistry**.
#[test]
fn the_equation_number_is_the_callers_decision() {
    // Note the **single** dollars: the deposit's `$$…$$` is a display pair on an
    // inline formula, so it is respelled — `_render_formula` runs the deposit
    // through `_latex_expression` rather than using it verbatim.
    let latex = ["$$x$$".to_string()];
    assert_eq!(
        render_formula(&latex, "", "", "(1)", false, true),
        "(1) $x$"
    );
    assert_eq!(
        render_formula(&latex, "", "", "(1)", false, false),
        "$x$",
        "merged into a sentence, the number reads as a coefficient"
    );
    // A numbered formula with no label prints no number.
    assert_eq!(render_formula(&latex, "", "", "", false, true), "$x$");
    // On a display formula the pair is kept.
    assert_eq!(
        render_formula(&latex, "", "", "(1)", true, true),
        "(1) $$x$$"
    );
}

/// Normalisation collapses **runs** and trims, and a non-breaking space is
/// whitespace to Rust's `char::is_whitespace` — which is the behaviour the oracle
/// pins rather than an assumption.
#[test]
fn whitespace_normalisation_collapses_and_trims() {
    assert_eq!(normalize_whitespace("a   b\t\nc"), "a b c");
    assert_eq!(normalize_whitespace("  x  "), "x");
    assert_eq!(normalize_whitespace(" \t\n "), "");
    assert_eq!(normalize_whitespace("abc"), "abc");
    assert_eq!(without_whitespace(" a\tb\nc "), "abc");
    assert_eq!(without_whitespace("   "), "");
}

/// A row is padded with **empty** cells, and one already wider is
/// **truncated** — the caller's column count is a target, not a minimum.
#[test]
fn a_row_pads_to_a_minimum_width() {
    assert_eq!(pad_row(vec!["a".to_string()], 3), vec!["a", "", ""]);
    assert_eq!(
        pad_row(vec!["a".to_string(), "b".to_string()], 2),
        vec!["a", "b"]
    );
    assert_eq!(
        pad_row(vec!["a".to_string(), "b".to_string(), "c".to_string()], 2),
        vec!["a", "b"],
        "a malformed colspan can widen a row past its frame"
    );
    assert_eq!(pad_row(Vec::new(), 2), vec!["", ""]);
}
