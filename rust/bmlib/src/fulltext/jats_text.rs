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

//! JATS text primitives: whitespace, locators, and LaTeX deposits.
//!
//! Extracted from `jats_parser.py` as **functions of their arguments**, which is
//! what makes them testable without an 800-line SAX handler around them. They are
//! also where the parser's subtlest rules live, and each rule below is one that a
//! plausible implementation gets wrong.

/// Every whitespace character removed.
#[must_use]
pub fn without_whitespace(text: &str) -> String {
    text.chars().filter(|c| !c.is_whitespace()).collect()
}

/// Runs of whitespace collapsed to one space, then trimmed.
#[must_use]
pub fn normalize_whitespace(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut in_run = false;
    for ch in text.chars() {
        if ch.is_whitespace() {
            in_whitespace(&mut out, &mut in_run);
        } else {
            in_run = false;
            out.push(ch);
        }
    }
    out.trim().to_string()
}

fn in_whitespace(out: &mut String, in_run: &mut bool) {
    if !*in_run {
        out.push(' ');
        *in_run = true;
    }
}

/// Does a citation print its `<elocation-id>` parts **joined** as one run?
///
/// Whitespace is judged by the **spelling**, because the two spellings mean
/// different things by it:
///
/// * in a `<mixed-citation>` it is typeset text, so `e1` and `e2` printed
///   `e1 e2` are two locators and not `e1e2` — the buffer, less the closing
///   part's own trailing whitespace, must end with `joined` exactly;
/// * an `<element-citation>` is element-only, so the whitespace between its
///   children is insignificant indentation and cannot part them, and it is
///   ignored on both sides.
///
/// An unrecognised `citation_element` takes the element-only reading, which is
/// the one that **joins**: failing to join splits one locator into two, while
/// joining wrongly merges two — and the source's own five measured cases are all
/// mixed-citations with nothing between the parts, where both readings agree.
#[must_use]
pub fn elocation_part_continues(buffer: &str, joined: &str, citation_element: &str) -> bool {
    if citation_element == "mixed-citation" {
        return buffer.trim_end().ends_with(joined);
    }
    without_whitespace(buffer).ends_with(&without_whitespace(joined))
}

/// The delimiter pairs a depositor may already have written, **tested in this
/// order**.
///
/// `$$` precedes `$` because the shorter is a prefix of the longer: testing `$`
/// first would read `$$x$$` as the pair `$…$` with the body `` $x$ ``, which is
/// not one expression.
pub const LATEX_DELIMITERS: &[(&str, &str)] =
    &[("$$", "$$"), ("\\[", "\\]"), ("\\(", "\\)"), ("$", "$")];

/// The delimiter pairs that put a renderer into **display** mode, which breaks
/// the line.
///
/// A display delimiter inside a sentence is wrong markup, which is what makes the
/// re-delimiting rule one-directional.
#[must_use]
pub fn is_display_delimiter(opening: &str, closing: &str) -> bool {
    (opening == "$$" && closing == "$$") || (opening == "\\[" && closing == "\\]")
}

/// The delimiter pair the depositor wrote around `body`, if any.
///
/// A pair counts only when the body has room for **both** halves: `"$"` opens and
/// closes with the same character, so `"$"` alone is not a delimited body.
#[must_use]
pub fn delimiter_pair(body: &str) -> Option<(&'static str, &'static str)> {
    for (opening, closing) in LATEX_DELIMITERS {
        if body.starts_with(opening)
            && body.ends_with(closing)
            && body.len() >= opening.len() + closing.len()
        {
            return Some((opening, closing));
        }
    }
    None
}

/// Render one `<tex-math>` deposit as an expression fit for prose.
///
/// A `<tex-math>` does not hold an expression. 99.9% of 4,422 sampled deposits
/// are a whole LaTeX **document** — `\documentclass[12pt]{minimal}`, a run of
/// `\usepackage` lines, then `\begin{document}` — so merging the element's text
/// as it stands injects some 300 characters of preamble per formula, which is
/// worse than the drop it replaces.
///
/// Three rules, each measured:
///
/// * **the two markers are read independently**, because a deposit carrying one
///   of them fails closed. Requiring both let a truncated deposit fall through to
///   the bare-expression path, which then delimited the preamble and merged it
///   into the prose — the exact outcome this function exists to prevent, *plus*
///   the doubled pair the delimiter rule exists to prevent. Splitting on
///   whichever marker is present recovers the expression instead. Both corpora
///   measure 0 unpaired deposits, so this is **severity and not frequency**: it is
///   silent, and it lands in the HTML the service caches;
/// * **the depositor's own delimiters are kept**, except where they would put a
///   sentence into display mode — 96.0% of bodies are already wrapped in `$$…$$`
///   so adding a pair unconditionally gives `$$$$…$$$$`. But that `$$` is not a
///   claim about the deposit's context: **98.6% of 20,251 inline** bodies carry
///   it, and inline formulas cannot genuinely be 98.6% display math, so a
///   *display* pair on an inline formula is re-spelled `$…$`;
/// * the rule is **one-directional** — an inline pair on a display formula is
///   left alone, because the two errors do not cost the same. A display delimiter
///   inside a sentence breaks the line; an inline delimiter on a formula that
///   stands alone merely under-styles it, and re-spelling that way would be
///   inventing a claim rather than reading one.
///
/// A body carrying several delimited runs (`$a$ + $b$`) is left alone: its outer
/// characters are not one pair around one expression, and stripping them would
/// corrupt it. A body opening an environment is left alone for the same reason —
/// the environment establishes its own math mode, and `$$\begin{equation}…` is
/// not valid LaTeX.
#[must_use]
pub fn latex_expression(deposit: &str, display: bool) -> String {
    let mut body = deposit;
    if let Some(index) = body.find("\\begin{document}") {
        body = &body[index + "\\begin{document}".len()..];
    }
    if let Some(index) = body.rfind("\\end{document}") {
        body = &body[..index];
    }
    let body = normalize_whitespace(body);
    if body.is_empty() {
        return String::new();
    }
    if body.starts_with("\\begin{") {
        return body;
    }
    let Some((opening, closing)) = delimiter_pair(&body) else {
        return if display {
            format!("$${body}$$")
        } else {
            format!("${body}$")
        };
    };
    if display || !is_display_delimiter(opening, closing) {
        return body;
    }
    let inner = body[opening.len()..body.len() - closing.len()].trim();
    if inner.is_empty() || inner.contains(opening) || inner.contains(closing) {
        // Not one delimited expression but several runs, or an empty pair.
        return body;
    }
    format!("${inner}$")
}

/// Space a merged formula the way the deposit spaced it.
///
/// Two rules, and the second is the module's own: **a run's edge whitespace is
/// re-emitted outside its markers**. Normalisation would otherwise lose the
/// separation the publisher put *inside* the element — measured over the
/// 880-article local corpus, that welded `'EndMatrix represents'` into one word
/// and `'−minus 0.505'` into another.
///
/// The first rule is the display one, and there the deposit has no spacing to
/// keep: a `<disp-formula>` is a block, so the markup puts nothing between it and
/// the text either side. Merged verbatim it welds — the same corpus ran
/// `'following reactions:'` straight into the first equation, and consecutive
/// equations into each other. One space either side is the least that can be
/// invented and still not join two expressions into one.
#[must_use]
pub fn pad_as_deposited(rendered: &str, buffered: &str, display: bool) -> String {
    if display {
        return format!(" {rendered} ");
    }
    let lead = if buffered.chars().next().is_some_and(char::is_whitespace) {
        " "
    } else {
        ""
    };
    let trail = if buffered
        .chars()
        .next_back()
        .is_some_and(char::is_whitespace)
    {
        " "
    } else {
        ""
    };
    format!("{lead}{rendered}{trail}")
}

/// What a formula contributes to the text around it.
///
/// **LaTeX wins wherever a `<tex-math>` arrived**, because it is the deposit's
/// exact expression where the alternative is a flattening. The buffer serves
/// otherwise, and *otherwise* is the common case rather than a fallback: it
/// carries the leaf text of a MathML encoding (which outnumbers LaTeX 10,202 to
/// 1,398 in the committed corpus), a formula deposited as ordinary
/// `<italic>`/`<sub>`/`<sup>` markup, and a MathML deposit whose namespace prefix
/// is not `mml`.
///
/// **The first deposit that renders to anything wins**, and the buffer is reached
/// whenever none does. Both halves were defects: joining every `<tex-math>` printed
/// one expression twice wherever an `<alternatives>` holds two LaTeX encodings of
/// it, and testing the LaTeX list for *presence* rather than for a rendition let an
/// empty or preamble-only `<tex-math>` suppress a perfectly good MathML
/// flattening, so `'Before Vmax after.'` became `'Before after.'`.
///
/// A formula holding nothing renders as nothing, and an `<alt-text>` is the last
/// rendition tried. Emitting a label alone would be a number standing for content
/// that is not there.
///
/// **The equation number is printed only where a number is what the reader would
/// read**, which is a measured rule rather than a taste. Merged into a sentence it
/// is not: over the 880-article corpus that produced
/// `'as shown in eqn (2):2 τ = kn'`, where the label reads as a coefficient, and —
/// two formulas running on — `'NH3 + H2O → NH4+ + OH−2 Al3+ + 3OH− → Al(OH)33'`,
/// where each number welds onto the previous formula's tail and changes the
/// chemistry. 21 insertions across that corpus opened with such a number. A
/// corruption is worse than a blank, and the prose introducing a merged equation
/// names its number in nearly every case anyway. The caller decides.
#[must_use]
pub fn render_formula(
    latex: &[String],
    buffered: &str,
    alt_text: &str,
    label: &str,
    display: bool,
    numbered: bool,
) -> String {
    let body = latex
        .iter()
        .map(|deposit| latex_expression(deposit, display))
        .find(|rendered| !rendered.is_empty())
        .unwrap_or_default();
    let body = if !body.is_empty() {
        body
    } else {
        normalize_whitespace(buffered)
    };
    let body = if !body.is_empty() {
        body
    } else {
        alt_text.to_string()
    };
    if body.is_empty() {
        return String::new();
    }
    if numbered && !label.is_empty() {
        return format!("{label} {body}");
    }
    body
}

/// Pad a row of cells to `count` columns, **truncating one that is wider**.
///
/// A truncation rather than a no-op, which `row[:count]` says and a pad-only
/// reading does not: a table with a malformed `colspan` can produce a row wider
/// than its column count, and every later row is laid out against that count — so
/// keeping the extra cells shifts nothing visible but makes the row's own width
/// disagree with the frame it is rendered into.
///
/// The caller's count is therefore a **target**, not a minimum.
#[must_use]
pub fn pad_row(mut row: Vec<String>, count: usize) -> Vec<String> {
    while row.len() < count {
        row.push(String::new());
    }
    row.truncate(count);
    row
}
