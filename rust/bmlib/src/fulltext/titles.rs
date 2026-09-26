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

//! Is the PDF's metadata title the article's title?
//!
//! A PDF's `/Title` is often the *file's* name, a running header, or the
//! software that produced it — so it is believed only where page 1 corroborates
//! it, and only after a backstop has rejected shapes known to be junk.
//!
//! # Two rejections, and their order is load-bearing
//!
//! The junk backstop is consulted **before** page 1, so a document that cannot be
//! corroborated at all does not thereby get a free pass for a shape already known
//! to be junk.
//!
//! # Two uncheckable cases, deliberately opposite
//!
//! The distinction is the one a sampler draws between an unmeasured probe and a
//! failed one:
//!
//! * a page **read as empty** accepts the title — corroboration is then a test
//!   that could not be run, and rejecting would blank the title of every
//!   image-only scan, where the metadata is the only title signal there is;
//! * a page that **could not be read** rejects it — that is a test that
//!   *failed*, not one that was inapplicable, and a fault is not evidence. It is
//!   precisely where there is least reason to trust anything the file claims
//!   about itself.
//!
//! The backstop applies in both, so an unrunnable check is never a free pass.

/// A title of fewer than this many words is not an article title.
///
/// **Retained as defence-in-depth, not as a member the corpus currently earns.**
/// It was earned by exactly one row of 235 — `"Nepal Journ"`, a journal name
/// truncated mid-word in a running header, which page 1 really does print —
/// under the membership rule in [`looks_like_junk`]. Anchored containment now
/// rejects that row on its own, so re-measured over the corpus this threshold
/// rescues **nothing** corroboration does not already reject.
///
/// What it still covers, and anchoring does not: a short but *complete* junk
/// string that page 1 genuinely prints — a footer reading `"Layout 1"`.
/// Anchoring only catches junk cut mid-token. The corpus shows no such row, so
/// that cover is **argued rather than measured**.
///
/// A title rejected here is not lost: it falls through to the font-size
/// heuristic, which for a genuinely short title printed large returns it anyway.
pub const MIN_TITLE_WORDS: usize = 3;

/// The widest run of digits that may be a line or page number.
///
/// Four digits covers continuous journal pagination, while a longer run alone on
/// a line is an accession or job number whose removal would let a title match a
/// page with its distinguishing token gone.
pub const MAX_LINE_NUMBER_DIGITS: usize = 4;

/// Reduce `text` to the form both sides of the corroboration test compare in.
///
/// Line-break hyphenation is closed up first; the text is then decomposed,
/// its combining marks dropped, lowercased, and reduced to its alphanumeric runs
/// joined by single spaces. That absorbs the differences which separate a correct
/// metadata title from its printed form — case, the terminal period metadata
/// usually drops, en-dash versus hyphen, ligatures, diacritics, and the line
/// break a wrapped title carries — while keeping every difference that changes
/// what the string says.
#[must_use]
pub fn normalise(text: &str) -> String {
    let closed = close_line_break_hyphens(text);
    let stripped = strip_combining_marks(&closed);
    // **ASCII-only tokens**, which is what the Python's `[a-z0-9]+` matches.
    // A non-ASCII letter is therefore a *separator* and not a word character: a
    // Greek or Cyrillic title normalises to nothing and is refused as wordless,
    // and a ligature NFKD does not decompose (`æ`) splits the token it sits in.
    // Reproduced rather than widened — a widened class would make two spellings
    // compare equal that the source tells apart, which is a new claim about a
    // document rather than a tidier reading of one.
    let mut out = String::with_capacity(stripped.len());
    let mut in_run = false;
    for ch in stripped.chars().flat_map(char::to_lowercase) {
        if ch.is_ascii_alphanumeric() {
            out.push(ch);
            in_run = true;
        } else if in_run {
            out.push(' ');
            in_run = false;
        }
    }
    out.trim_end().to_string()
}

/// Close a hyphen that sits immediately before a line break.
///
/// A hyphen before a break is **typesetting, not spelling**: the word continues
/// on the next line. Anchored on the break, so an ordinary mid-line hyphen is
/// left alone and `dose-response` stays two tokens.
///
/// Four hyphen spellings are recognised, and the soft hyphen especially is
/// invisible in a source file — a class whose members cannot be seen is one a
/// later reader deletes as a duplicate of the plain `-`.
#[must_use]
pub fn close_line_break_hyphens(text: &str) -> String {
    let chars: Vec<char> = text.chars().collect();
    let mut out = String::with_capacity(text.len());
    let mut index = 0;
    while index < chars.len() {
        let ch = chars[index];
        if is_hyphen(ch) {
            // Look past horizontal whitespace and one line break.
            let mut probe = index + 1;
            while probe < chars.len()
                && (chars[probe] == ' ' || chars[probe] == '\t' || chars[probe] == '\r')
            {
                probe += 1;
            }
            if probe < chars.len() && chars[probe] == '\n' {
                probe += 1;
                while probe < chars.len()
                    && (chars[probe] == ' ' || chars[probe] == '\t' || chars[probe] == '\r')
                {
                    probe += 1;
                }
                // A break follows the hyphen, so the hyphen and the break go and
                // the word joins up.
                index = probe;
                continue;
            }
        }
        out.push(ch);
        index += 1;
    }
    out
}

/// Whether `ch` is one of the four hyphen spellings.
#[must_use]
pub fn is_hyphen(ch: char) -> bool {
    matches!(ch, '\u{002d}' | '\u{2010}' | '\u{2011}' | '\u{00ad}')
}

/// Remove every line that holds nothing but a number.
///
/// Preprint servers number the lines of a submitted manuscript, and a PDF reader
/// reports each number as its own line — *between* the lines of the title it sits
/// beside. Joining them into the page text splices digits into the middle of the
/// title, so a metadata title reading *"Coordinated leaf hydraulic thresholds
/// maintain virtually null stomatal safety margins…"* is not contained in a page
/// reading *"…virtually null 1 stomatal safety margins… 2 and nutrient
/// induced…"*, and a perfectly good title is rejected.
///
/// Measured: this was the **only** wrongly rejected title in 130 matched rows,
/// and it is a whole class of document rather than one file.
///
/// Note it does not only remove: excising a line also **joins** what sat either
/// side of it, which is how the wrapped title matches — so it can create a
/// containment the page does not literally print. That is the intended effect,
/// not a side-effect.
///
/// **The corpus constrains none of this.** Four independent mutations of the
/// digit bound change the answer on zero of 235 rows, so do not read a green
/// corpus run as having checked it.
#[must_use]
pub fn page_text_for_matching(page_one_text: &str) -> String {
    let mut out = String::with_capacity(page_one_text.len());
    for (index, line) in page_one_text.split('\n').enumerate() {
        if index > 0 {
            out.push('\n');
        }
        let trimmed = line.trim_matches(|c| c == ' ' || c == '\t');
        let is_number = !trimmed.is_empty()
            && trimmed.len() <= MAX_LINE_NUMBER_DIGITS
            && trimmed.chars().all(|c| c.is_ascii_digit());
        if !is_number {
            out.push_str(line);
        }
    }
    out
}

/// Whether `title` is a known junk shape, regardless of what page 1 says.
///
/// The backstop to corroboration, for junk the document *does* print — a running
/// header, a job number repeated in the footer. Every member is earned from a
/// measured corpus under the rule that it must reject at least one measured junk
/// title corroboration accepted, and no measured good one; **a shape the corpus
/// never showed does not become a member however obvious it looks**, which is the
/// reject-list this design exists to avoid.
#[must_use]
pub fn looks_like_junk(title: &str) -> bool {
    normalise(title).split_whitespace().count() < MIN_TITLE_WORDS
}

/// Why a metadata title was refused.
///
/// The public function returns `Option<String>` — every caller asks one binary
/// question and would discard a richer answer — but that collapses four unrelated
/// reasons, and the one party who wanted them is the human debugging why a title
/// vanished from one PDF. This enum is for a caller that wants to log them.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TitleRefusal {
    /// The metadata carried no title, or only whitespace.
    Absent,
    /// It matched a shape known to be junk.
    JunkShape,
    /// It holds no word characters at all.
    Wordless,
    /// Page 1 could not be read, so the title could not be corroborated.
    PageUnreadable,
    /// Page 1 carried text, and the title is not printed on it.
    NotPrinted,
}

/// The PDF's own metadata title, where the document corroborates it.
///
/// `page_one_text` is page 1's text, newline-separated:
///
/// * `Some("")` when page 1 was read and carried no text — an image-only scan;
/// * `None` when page 1 could not be read *at all*.
///
/// The two are **deliberately opposite** — see the module comment — and the
/// backstop applies in both, so an unrunnable check is never a free pass.
#[must_use]
pub fn accepted_metadata_title(
    metadata_title: Option<&str>,
    page_one_text: Option<&str>,
) -> Option<String> {
    accepted_metadata_title_why(metadata_title, page_one_text).ok()
}

/// As [`accepted_metadata_title`], saying **why** a title was refused.
///
/// # Errors
///
/// [`TitleRefusal`] naming which of the five reasons applied.
pub fn accepted_metadata_title_why(
    metadata_title: Option<&str>,
    page_one_text: Option<&str>,
) -> Result<String, TitleRefusal> {
    let title = metadata_title.unwrap_or_default().trim().to_string();
    if title.is_empty() {
        return Err(TitleRefusal::Absent);
    }
    // Asked **before** page 1, so a document that cannot be corroborated at all
    // does not thereby get a free pass for a shape already known to be junk.
    if looks_like_junk(&title) {
        return Err(TitleRefusal::JunkShape);
    }
    let wanted = normalise(&title);
    if wanted.is_empty() {
        return Err(TitleRefusal::Wordless);
    }
    let Some(page_one_text) = page_one_text else {
        // A test that **failed**, not one that was inapplicable: a fault is not
        // evidence, and this is where there is least reason to trust the file.
        return Err(TitleRefusal::PageUnreadable);
    };
    let page = normalise(&page_text_for_matching(page_one_text));
    if page.is_empty() {
        // A test that **could not be run**. Rejecting would blank the title of
        // every image-only scan, where the metadata is the only signal there is.
        return Ok(title);
    }
    // Anchored, so a title is not "contained" in a page that merely carries it
    // as a prefix of a longer word.
    if format!(" {page} ").contains(&format!(" {wanted} ")) {
        return Ok(title);
    }
    Err(TitleRefusal::NotPrinted)
}

/// Decompose compatibility characters and drop combining marks.
///
/// **NFKD plus a mark-strip, which is exactly what the Python's
/// `unicodedata.normalize("NFKD", …)` followed by a combining test does** — taken
/// from `unicode-normalization` rather than hand-rolled, and that is a deliberate
/// departure from the port's "hand-roll it if it is small" rule.
///
/// It is not small: the fold from a precomposed character to its base plus marks
/// covers about **1,900** characters, and a partial table fails **silently in the
/// dangerous direction** — two spellings of one title compare unequal, so a good
/// title is dropped with only a DEBUG line to say why, and a reviewer cannot see
/// the missing row because the symptom is an absence.
///
/// The first cut of this file *was* a hand-rolled table, guessed from the block
/// structure of Latin Extended-A. The oracle refuted it on both sides at once:
/// it folded `Ł` to `l` where NFKD leaves it alone, and it left `Đ` unfolded
/// where NFKD strips it to `D`. Both would have shipped as a title silently
/// accepted or refused.
///
/// **It adds no folds of its own.** NFKD does *not* expand the ligatures — `æ`
/// stays `æ`, and `ß` stays `ß` — so neither does this, and a "helpful" `æ` → `ae`
/// here would be a spelling the source tells apart.
#[must_use]
pub fn strip_combining_marks(text: &str) -> String {
    use unicode_normalization::UnicodeNormalization;
    text.nfkd().filter(|c| !is_combining_mark(*c)).collect()
}

/// Whether `ch` is a combining mark this module drops.
///
/// The combining diacritical marks block, plus the combining half marks. A mark
/// outside it is kept, because a mark this module cannot name is not one it
/// should silently delete from a title.
#[must_use]
pub fn is_combining_mark(ch: char) -> bool {
    matches!(ch as u32, 0x0300..=0x036f | 0x1ab0..=0x1aff | 0x20d0..=0x20ff | 0xfe20..=0xfe2f)
}
