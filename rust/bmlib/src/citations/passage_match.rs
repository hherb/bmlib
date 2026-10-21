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

//! Locate an (LLM-copied) passage inside a source text and return the exact
//! source span.
//!
//! A port of `bmlib/citations/passage_match.py`, operation for operation, so
//! both return the same span and the same `f64` similarity. Text is folded
//! character by character (typographic quotes and dashes to ASCII, NFKD,
//! accents dropped, lowercase) and whitespace runs collapse to one space.
//! [`normalize_with_offsets`] keeps a map from every normalised `char` back to
//! the `char` of the original that produced it. **Offsets and spans are
//! Unicode code-point (`char`) indices, never bytes**, matching Python's
//! string indices so the two implementations agree exactly.
//!
//! [`find_passage`] tries an exact match of the normalised passage first,
//! rejects a passage too long to reach the threshold ([`fits_text`]), then
//! lets short seeds of the passage vote for where it starts and refines a
//! window around the best-supported locations on word boundaries. The
//! similarity is `difflib.SequenceMatcher.ratio()`; the Python module's
//! docstring has the full search strategy and cost.

use std::cmp::Reverse;
use std::collections::{BTreeMap, BTreeSet};
use std::fmt;

use unicode_normalization::char::canonical_combining_class;
use unicode_normalization::UnicodeNormalization;

use super::sequence_matcher::SequenceMatcher;

/// Shortest seed used for voting; shorter seeds match almost anywhere.
const SEED_MIN_LENGTH: usize = 4;
/// Seeds occurring more often than this carry no location signal and are skipped.
const SEED_MAX_OCCURRENCES: usize = 64;
/// Number of distinct candidate locations refined per search.
const MAX_CANDIDATE_REGIONS: usize = 5;
/// Alternating start/end refinement passes per candidate location.
const REFINEMENT_PASSES: usize = 2;
/// Matching blocks shorter than this are ignored when estimating where the
/// passage starts and ends; short blocks also match stray text nearby.
const EDGE_BLOCK_MIN_LENGTH: usize = 2 * SEED_MIN_LENGTH;
/// Characters searched for word boundaries on either side of an estimated
/// window edge (a few words).
const EDGE_SEARCH_MARGIN: isize = 16;

const SPACE: char = ' ';

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

/// An accepted span of the original text, in `char` indices.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PassageSpan {
    /// Start index in the original text.
    pub start: usize,
    /// End index (exclusive).
    pub end: usize,
    /// The original text's own wording for `start..end`.
    pub text: String,
}

/// Result of [`find_passage`].
#[derive(Debug, Clone, PartialEq)]
pub struct PassageMatch {
    /// Best similarity found, in `[0, 1]`: 1.0 for a passage found verbatim
    /// after normalisation; 0.0 when no candidate location was found at all
    /// or the passage is too long to reach the threshold. Reported even when
    /// the match is rejected.
    pub similarity: f64,
    /// The matched span, or `None` when the match was rejected.
    pub span: Option<PassageSpan>,
    /// The normalised passage occurs verbatim in the normalised text (only
    /// set with a span). An exact span may start or end inside a character
    /// that folds to several ("ﬁ"), so its text can hold one more letter.
    pub exact: bool,
}

impl PassageMatch {
    /// Whether a span at or above the threshold was found.
    pub fn matched(&self) -> bool {
        self.span.is_some()
    }

    fn rejected(similarity: f64) -> Self {
        Self {
            similarity,
            span: None,
            exact: false,
        }
    }
}

/// A `min_similarity` outside `(0, 1]`, or NaN.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct InvalidSimilarity(pub f64);

impl fmt::Display for InvalidSimilarity {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "min_similarity must be in (0, 1], got {}", self.0)
    }
}

impl std::error::Error for InvalidSimilarity {}

/// Find the best-scoring span of `text` it can locate for `passage`.
///
/// An exact match (after normalisation) is returned as the raw source span
/// and may start or end mid-word; a fuzzy match starts and ends on word
/// boundaries (unless no boundary lies near an estimated edge). The match is
/// accepted when the best similarity reaches `min_similarity`; its span then
/// holds the original source wording.
///
/// # Errors
///
/// [`InvalidSimilarity`] if `min_similarity` is outside `(0, 1]` or NaN.
pub fn find_passage(
    passage: &str,
    text: &str,
    min_similarity: f64,
) -> Result<PassageMatch, InvalidSimilarity> {
    // Negated so NaN is refused too.
    if !(min_similarity > 0.0 && min_similarity <= 1.0) {
        return Err(InvalidSimilarity(min_similarity));
    }
    let (norm_passage, _) = normalize_chars(passage);
    let (norm_text, offsets) = normalize_chars(text);
    if norm_passage.is_empty() || norm_text.is_empty() {
        return Ok(PassageMatch::rejected(0.0));
    }

    if let Some(at) = find_from(&norm_text, &norm_passage, 0) {
        return Ok(accepted(
            text,
            &offsets,
            at,
            at + norm_passage.len(),
            1.0,
            true,
        ));
    }
    if !fits_text(norm_passage.len(), norm_text.len(), min_similarity) {
        return Ok(PassageMatch::rejected(0.0));
    }

    let max_edits = max_edit_points(norm_passage.len(), min_similarity);
    let mut matcher = SequenceMatcher::new(&norm_passage);

    let (mut best_similarity, mut best_start, mut best_end) = (0.0, 0, 0);
    for anchor in candidate_anchors(&norm_passage, &norm_text, max_edits) {
        let (similarity, start, end) = refine_window(
            &mut matcher,
            &norm_text,
            anchor,
            norm_passage.len(),
            max_edits + 1,
            min_similarity,
        );
        if similarity > best_similarity {
            (best_similarity, best_start, best_end) = (similarity, start, end);
        }
    }

    if best_similarity >= min_similarity {
        return Ok(accepted(
            text,
            &offsets,
            best_start,
            best_end,
            best_similarity,
            false,
        ));
    }
    Ok(PassageMatch::rejected(best_similarity))
}

/// Whether a passage this long could reach the threshold against the text.
///
/// A window `W` of the text matches at most `W` characters, so
/// `ratio <= 2W / (P + W) <= 2T / (P + T)`; that bound reaches `s` only when
/// `P <= T(2-s)/s`. Lengths are of the normalised strings.
pub fn fits_text(passage_length: usize, text_length: usize, min_similarity: f64) -> bool {
    passage_length as f64 * min_similarity <= text_length as f64 * (2.0 - min_similarity)
}

/// Upper bound on edit points in a window scoring `>= min_similarity`.
///
/// With `ratio = 2M / (P + W)` and the longest admissible window
/// `W = P(2-s)/s`, unmatched characters on both sides total at most
/// `(1-s)(P+W) = 2P(1-s)/s`. Each is at most one edit point, and the
/// alignment offset can drift by at most that much along the window.
pub fn max_edit_points(passage_length: usize, min_similarity: f64) -> usize {
    // Python's order of operations, so the floor is bit-identical.
    ((2 * passage_length) as f64 * (1.0 - min_similarity) / min_similarity).floor() as usize
}

/// Python's `text.find(needle, from)` over chars (`needle` non-empty).
fn find_from(text: &[char], needle: &[char], from: usize) -> Option<usize> {
    text.get(from..)?
        .windows(needle.len())
        .position(|window| window == needle)
        .map(|at| at + from)
}

/// Rank text offsets where the passage plausibly starts, by seed votes.
///
/// Returns up to `MAX_CANDIDATE_REGIONS` offsets (possibly negative),
/// strongest first, more than `max_edits` apart.
fn candidate_anchors(passage: &[char], text: &[char], max_edits: usize) -> Vec<isize> {
    let seed_length = SEED_MIN_LENGTH.max(passage.len() / (max_edits + 1));
    // Sorted by offset, as Python's `sorted(votes)`.
    let mut votes: BTreeMap<isize, usize> = BTreeMap::new();
    for seed_start in (0..(passage.len() + 1).saturating_sub(seed_length)).step_by(seed_length) {
        let seed = &passage[seed_start..seed_start + seed_length];
        for position in occurrences(text, seed) {
            *votes
                .entry(position as isize - seed_start as isize)
                .or_insert(0) += 1;
        }
    }
    if votes.is_empty() {
        return Vec::new();
    }

    // Indels shift the offset by up to max_edits, splitting one location's
    // votes over neighbouring offsets: score each offset by its neighbourhood.
    let radius = max_edits as isize;
    let offsets: Vec<isize> = votes.keys().copied().collect();
    let mut cumulative = vec![0];
    for count in votes.values() {
        cumulative.push(cumulative[cumulative.len() - 1] + count);
    }
    let neighbourhood = |offset: isize| {
        let low = offsets.partition_point(|&o| o < offset - radius);
        let high = offsets.partition_point(|&o| o <= offset + radius);
        cumulative[high] - cumulative[low]
    };

    // Python's stable sort by (-neighbourhood, -votes) over ascending offsets.
    let mut ranked: Vec<(Reverse<usize>, Reverse<usize>, isize)> = offsets
        .iter()
        .map(|&offset| {
            (
                Reverse(neighbourhood(offset)),
                Reverse(votes[&offset]),
                offset,
            )
        })
        .collect();
    ranked.sort_unstable();
    let mut chosen: Vec<isize> = Vec::new();
    for (_, _, offset) in ranked {
        if chosen.iter().all(|&kept| (offset - kept).abs() > radius) {
            chosen.push(offset);
            if chosen.len() == MAX_CANDIDATE_REGIONS {
                break;
            }
        }
    }
    chosen
}

/// Positions of `seed` in `text`; empty if it occurs more than
/// `SEED_MAX_OCCURRENCES` times (too common to locate).
fn occurrences(text: &[char], seed: &[char]) -> Vec<usize> {
    let mut positions = Vec::new();
    let mut found = find_from(text, seed, 0);
    while let Some(position) = found {
        if positions.len() == SEED_MAX_OCCURRENCES {
            return Vec::new();
        }
        positions.push(position);
        found = find_from(text, seed, position + 1);
    }
    positions
}

/// Choose the best-scoring window near an anchor offset.
///
/// Aligns the passage against `text` around the anchor (`slack` on either
/// side) to estimate where it starts and ends, then, starting from the word
/// boundaries nearest those estimates, alternately moves the start, then the
/// end, to the best nearby word boundary. Refinement is skipped when the
/// alignment matches too few characters for any window to reach
/// `min_similarity`. Returns `(similarity, start, end)` in normalised indices.
fn refine_window(
    matcher: &mut SequenceMatcher,
    text: &[char],
    anchor: isize,
    passage_length: usize,
    slack: usize,
    min_similarity: f64,
) -> (f64, usize, usize) {
    let length = text.len();
    let (starts, ends, aligned) = edge_estimates(matcher, text, anchor, passage_length, slack);
    let start_candidates = near_boundaries(word_starts, text, starts, 0, length - 1);
    let end_candidates = near_boundaries(word_ends, text, ends, 1, length);
    let mut best_start = closest(&start_candidates, starts.0);
    let viable_ends: Vec<usize> = end_candidates
        .iter()
        .copied()
        .filter(|&end| end > best_start)
        .collect();
    let mut best_end = if viable_ends.is_empty() {
        closest(&[(best_start + passage_length).min(length)], ends.1)
    } else {
        closest(&viable_ends, ends.1)
    };
    let mut best = similarity(matcher, text, best_start, best_end, -1.0);
    // A window matching M characters scores at most 2M / (P + M); `aligned` is
    // difflib's count for the wider region, so this skips regions that are
    // almost surely too dissimilar.
    if ((2 * aligned) as f64) < min_similarity * (passage_length + aligned) as f64 {
        return (best, best_start, best_end);
    }
    for _ in 0..REFINEMENT_PASSES {
        for &start in &start_candidates {
            if start < best_end {
                let score = similarity(matcher, text, start, best_end, best);
                if score > best {
                    (best, best_start) = (score, start);
                }
            }
        }
        for &end in &end_candidates {
            if end > best_start {
                let score = similarity(matcher, text, best_start, end, best);
                if score > best {
                    (best, best_end) = (score, end);
                }
            }
        }
    }
    (best, best_start, best_end)
}

/// Word boundaries within `EDGE_SEARCH_MARGIN` of either estimate, sorted;
/// the estimates clamped to `lowest..=highest` when no boundary lies near
/// them (e.g. inside one very long token).
fn near_boundaries(
    boundaries: fn(&[char], isize, isize) -> BTreeSet<usize>,
    text: &[char],
    estimates: (isize, isize),
    lowest: usize,
    highest: usize,
) -> Vec<usize> {
    let mut found = BTreeSet::new();
    for estimate in [estimates.0, estimates.1] {
        found.extend(boundaries(
            text,
            estimate - EDGE_SEARCH_MARGIN,
            estimate + EDGE_SEARCH_MARGIN,
        ));
    }
    if found.is_empty() {
        found = [estimates.0, estimates.1]
            .into_iter()
            .map(|estimate| estimate.max(lowest as isize).min(highest as isize) as usize)
            .collect();
    }
    found.into_iter().collect()
}

/// The candidate nearest `target` (the lower one on ties).
fn closest(candidates: &[usize], target: isize) -> usize {
    *candidates
        .iter()
        .min_by_key(|&&candidate| ((candidate as isize - target).abs(), candidate))
        .expect("at least one candidate")
}

/// Estimate where the passage starts and ends near an anchor.
///
/// The passage is aligned against `text` from `slack` before the anchor to
/// `slack` past the anchored end. Returns `((start_low, start_high),
/// (end_low, end_high), aligned)`: edge estimates in normalised indices (not
/// clamped to the text; both pairs are the anchored window edges when the
/// alignment has no substantial block), and the number of passage characters
/// aligned.
fn edge_estimates(
    matcher: &mut SequenceMatcher,
    text: &[char],
    anchor: isize,
    passage_length: usize,
    slack: usize,
) -> ((isize, isize), (isize, isize), usize) {
    let (p, slack) = (passage_length as isize, slack as isize);
    let low = (anchor - slack).max(0);
    let high = (anchor + p + slack).min(text.len() as isize);
    matcher.set_a(window(text, low, high));
    let all_blocks = matcher.matching_blocks();
    let aligned = all_blocks.iter().map(|block| block.size).sum();
    let blocks: Vec<_> = all_blocks
        .iter()
        .filter(|block| block.size >= EDGE_BLOCK_MIN_LENGTH)
        .collect();
    let (Some(first), Some(last)) = (blocks.first(), blocks.last()) else {
        let anchored_end = anchor + p;
        return ((anchor, anchor), (anchored_end, anchored_end), aligned);
    };
    let first_at = low + first.a as isize;
    let last_end = low + (last.a + last.size) as isize;
    let unmatched_tail = p - (last.b + last.size) as isize;
    (
        (first_at - first.b as isize, first_at),
        (last_end, last_end + unmatched_tail),
        aligned,
    )
}

/// Python's `text[start:end]` for non-negative bounds.
fn window(text: &[char], start: isize, end: isize) -> &[char] {
    let start = (start.max(0) as usize).min(text.len());
    let end = (end.max(0) as usize).clamp(start, text.len());
    &text[start..end]
}

/// Ratio of the passage against `text[start..end]`, or `floor` when the cheap
/// upper bounds show the window cannot beat it.
fn similarity(
    matcher: &mut SequenceMatcher,
    text: &[char],
    start: usize,
    end: usize,
    floor: f64,
) -> f64 {
    matcher.set_a(window(text, start as isize, end as isize));
    if matcher.real_quick_ratio() <= floor || matcher.quick_ratio() <= floor {
        return floor;
    }
    matcher.ratio()
}

/// Indices in `low..=high` where a word starts in normalised `text`.
fn word_starts(text: &[char], low: isize, high: isize) -> BTreeSet<usize> {
    let (low, high) = (low.max(0), high.min(text.len() as isize - 1));
    (low..=high)
        .map(|index| index as usize)
        .filter(|&index| text[index] != SPACE && (index == 0 || text[index - 1] == SPACE))
        .collect()
}

/// Exclusive indices in `low..=high` where a word ends in normalised `text`.
fn word_ends(text: &[char], low: isize, high: isize) -> BTreeSet<usize> {
    let (low, high) = (low.max(1), high.min(text.len() as isize));
    (low..=high)
        .map(|index| index as usize)
        .filter(|&index| text[index - 1] != SPACE && (index == text.len() || text[index] == SPACE))
        .collect()
}

/// Map a normalised window (`start < end`) back onto whole characters of the
/// original text, trimming whitespace at its edges.
fn accepted(
    original: &str,
    offsets: &[usize],
    start: usize,
    end: usize,
    similarity: f64,
    exact: bool,
) -> PassageMatch {
    let chars: Vec<char> = original.chars().collect();
    let mut original_start = offsets[start];
    // Extend up to the next kept character so marks folded to nothing (an
    // NFD accent after the last letter) stay in the span; a window ending
    // inside a multi-code-point fold ("ﬁ") keeps the whole character.
    let next_kept = offsets.get(end).copied().unwrap_or(chars.len());
    let mut original_end = (offsets[end - 1] + 1).max(next_kept);
    // A window edge on a collapsed space maps to raw whitespace: trim it.
    let span = &chars[original_start..original_end];
    original_start += span.iter().take_while(|&&c| is_py_space(c)).count();
    original_end -= span.iter().rev().take_while(|&&c| is_py_space(c)).count();
    PassageMatch {
        similarity,
        span: Some(PassageSpan {
            start: original_start,
            end: original_end,
            text: chars[original_start..original_end].iter().collect(),
        }),
        exact,
    }
}
