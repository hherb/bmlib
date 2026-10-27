# bmlib — shared library for biomedical literature tools
# Copyright (C) 2024-2026 Dr Horst Herb
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""
Tests for bmlib.citations.passage_match.

Covers whitespace/case/Unicode normalisation with offset mapping, exact and fuzzy
passage location (including inserted and dropped words, text edges and
repeated phrasing), rejection of paraphrases and fabrications, the result
invariants, input validation, a seeded comparison against exhaustive search,
and the performance bound that motivated the module (the old sliding-window
matcher in CitationFinderAgent took ~30 s per citation on a 1 kB abstract).
"""

import math
import random
from difflib import SequenceMatcher

import pytest

from bmlib.citations import passage_match
from bmlib.citations.passage_match import (
    EDGE_SEARCH_MARGIN,
    MAX_CANDIDATE_REGIONS,
    REFINEMENT_PASSES,
    PassageMatch,
    _fits_text,
    _max_edit_points,
    find_passage,
    normalize_with_offsets,
    occurs_once,
)

SENTENCES = [
    "Background: Atrial fibrillation is the most common sustained arrhythmia and is "
    "associated with a fivefold increase in the risk of stroke.",
    "Methods: We conducted a randomised controlled trial in 1,204 patients aged 65 "
    "years or older across 14 centres in Europe between 2015 and 2019.",
    "Patients were assigned to apixaban 5 mg twice daily or to warfarin with a target "
    "international normalised ratio of 2.0 to 3.0.",
    "The primary outcome was stroke or systemic embolism; the primary safety outcome "
    "was major bleeding according to ISTH criteria.",
    "Results: Over a median follow-up of 2.1 years, stroke or systemic embolism "
    "occurred in 1.27% per year in the apixaban group and 1.60% per year in the "
    "warfarin group (hazard ratio 0.79; 95% CI 0.66 to 0.95).",
    "Major bleeding occurred in 2.13% per year with apixaban versus 3.09% per year with warfarin.",
    "Conclusions: In elderly patients with atrial fibrillation, apixaban was superior "
    "to warfarin in preventing stroke or systemic embolism, caused less bleeding and "
    "resulted in lower mortality.",
]
ABSTRACT = " ".join(SENTENCES)

# Most windows a fuzzy search may score, whatever the text and passage length:
# per candidate region, the edge alignment, the estimated window, and per
# refinement pass every word boundary within EDGE_SEARCH_MARGIN of the two
# estimates of each edge. A bound on work rather than wall time (#533), since
# a clock fails on a loaded machine; the old implementation scored windows in
# proportion to the text and took hours on these inputs.
MAX_WINDOWS_SCORED = MAX_CANDIDATE_REGIONS * (
    2 + REFINEMENT_PASSES * 2 * 2 * (2 * EDGE_SEARCH_MARGIN + 1)
)
LONG_TEXT_REPEATS = 10

# Seeded comparison against exhaustive search over word-boundary windows.
PROPERTY_SEED = 20261017
PROPERTY_TRIALS = 60
PROPERTY_THRESHOLD = 0.95
# Allowed shortfall of find_passage's score against the exhaustive best.
PROPERTY_SCORE_TOLERANCE = 0.01


class TestNormalizeWithOffsets:
    """normalize_with_offsets() lowercases, collapses whitespace, maps back."""

    def test_collapses_and_strips_whitespace(self) -> None:
        norm, offsets = normalize_with_offsets("  Foo \n\t Bar  ")
        assert norm == "foo bar"
        assert len(offsets) == len(norm)

    def test_offsets_point_into_original(self) -> None:
        original = "A  B\nC"
        norm, offsets = normalize_with_offsets(original)
        assert norm == "a b c"
        assert [original[i] for i in offsets] == ["A", " ", "B", "\n", "C"]

    def test_length_changing_fold_keeps_offsets_aligned(self) -> None:
        # "ﬁ" folds to two code points; both must map to the same source index.
        original = "ﬁrst x"
        norm, offsets = normalize_with_offsets(original)
        assert norm == "first x"
        assert offsets[0] == offsets[1] == 0
        assert offsets[2] == 1

    def test_dotted_capital_i_folds_to_plain_i(self) -> None:
        assert normalize_with_offsets("İstanbul")[0] == "istanbul"

    def test_empty(self) -> None:
        assert normalize_with_offsets("   ") == ("", [])


class TestUnicodeFolding:
    """Typographic and Unicode variants of the same text compare equal (#291)."""

    @pytest.mark.parametrize(
        ("passage", "text"),
        [
            ("Caf\u00e9 au lait was served.", "Cafe\u0301 au lait was served."),  # NFC vs NFD
            ("Cafe\u0301 au lait was served.", "Caf\u00e9 au lait was served."),  # NFD vs NFC
            ('The patient\'s "dose" was low.', "The patient\u2019s \u201cdose\u201d was low."),
            ("A dose-response effect was seen.", "A dose\u2013response effect was seen."),
            ("Effects were seen - not large.", "Effects were seen \u2014 not large."),
            ("The final efficacy was shown.", "The \ufb01nal e\ufb03cacy was shown."),
            ("Areas of 5 m2 were treated.", "Areas of 5 m\u00b2 were treated."),
            ("The treatment was effective.", "The treat\u00adment was effec\u200btive."),
        ],
    )
    def test_variants_match_exactly(self, passage: str, text: str) -> None:
        match = find_passage(passage, text, min_similarity=0.95)
        assert match.matched
        assert match.exact
        assert match.similarity == 1.0
        assert match.text == text

    def test_span_keeps_trailing_nfd_accent(self) -> None:
        # The passage ends on an accented letter that is decomposed in the source.
        text = "Served with cafe\u0301. Then tea."
        match = find_passage("served with caf\u00e9", text, min_similarity=0.95)
        assert match.text == "Served with cafe\u0301"

    def test_no_exact_match_ends_inside_a_ligature(self) -> None:
        # "The f" occurs in the folded "the final", but only by cutting "ﬁ".
        match = find_passage("The f", "The \ufb01nal result.", min_similarity=0.95)
        assert not match.exact
        assert not match.matched

    def test_returned_text_keeps_source_typography(self) -> None:
        sentence = "Mortality fell from 12% to 8% \u2014 a \u201cmarked\u201d drop."
        text = f"Background text here. {sentence} More text follows."
        match = find_passage(
            'Mortality fell from 12% to 8% - a "marked" drop.', text, min_similarity=0.95
        )
        assert match.text == sentence

    @pytest.mark.parametrize(
        ("passage", "text"),
        [
            ("The groups differed: A = B.", "The groups differed: A \u2260 B."),
            ("The value x was in S.", "The value x was \u2209 S."),
        ],
    )
    def test_negated_relation_is_not_an_exact_match(self, passage: str, text: str) -> None:
        # "≠" is "=" + U+0338; dropping the overlay would flip the meaning.
        assert not find_passage(passage, text, min_similarity=0.95).exact

    def test_superscript_minus_folds_to_hyphen(self) -> None:
        # NFKD turns "⁻" into U+2212, which must fold on to "-".
        match = find_passage(
            "Doses of 10-3 M were used.", "Doses of 10\u207b\u00b3 M were used.", 0.95
        )
        assert match.exact

    def test_fuzzy_match_over_folded_text_spans_whole_characters(self) -> None:
        text = "Intro. The \ufb01nal e\ufb03cacy was shown here today. End."
        match = find_passage("The final efficacy was clearly shown here today.", text, 0.85)
        assert match.matched
        assert not match.exact
        assert match.text == "The \ufb01nal e\ufb03cacy was shown here today."

    def test_span_keeps_nfd_accent_at_end_of_text(self) -> None:
        text = "Served with cafe\u0301"
        match = find_passage("served with caf\u00e9", text, min_similarity=0.95)
        assert match.text == text


class TestExactMatch:
    """Passages present verbatim (modulo case/whitespace) score 1.0."""

    def test_verbatim(self) -> None:
        match = find_passage(SENTENCES[4], ABSTRACT, min_similarity=0.95)
        assert match.matched
        assert match.similarity == 1.0
        assert match.text == SENTENCES[4]
        assert ABSTRACT[match.start : match.end] == SENTENCES[4]

    def test_case_differences_return_original_case(self) -> None:
        match = find_passage(SENTENCES[0].lower(), ABSTRACT, min_similarity=0.95)
        assert match.similarity == 1.0
        assert match.text == SENTENCES[0]

    def test_whitespace_differences_return_original_whitespace(self) -> None:
        text = "Type 2 diabetes   can often be\n managed through lifestyle changes."
        passage = "Type 2 diabetes can often be managed through lifestyle changes."
        match = find_passage(passage, text, min_similarity=0.95)
        assert match.similarity == 1.0
        assert match.text == text


class TestFuzzyMatch:
    """Near-verbatim passages map onto the exact source span."""

    def test_single_edit_returns_source_sentence(self) -> None:
        passage = SENTENCES[4].replace("hazard ratio", "hazard-ratio")
        match = find_passage(passage, ABSTRACT, min_similarity=0.95)
        assert match.matched
        assert 0.95 <= match.similarity < 1.0
        assert match.text == SENTENCES[4]

    def test_span_does_not_drift_late_in_text(self) -> None:
        # Regression: the old matcher counted only non-whitespace characters
        # when mapping back, so the start drifted one char per preceding word.
        passage = SENTENCES[6].replace("lower mortality", "lower  mortality;")
        match = find_passage(passage, ABSTRACT, min_similarity=0.95)
        assert match.matched
        assert match.text.startswith("Conclusions:")
        assert match.text.endswith("mortality.")

    def test_punctuation_change(self) -> None:
        text = (
            "Aspirin reduces the risk of heart attack in patients with cardiovascular "
            "disease, however it may increase bleeding risk."
        )
        passage = text.replace("disease, however", "disease; however")
        match = find_passage(passage, text, min_similarity=0.95)
        assert match.matched
        assert match.text == text

    def test_returned_text_is_from_source_not_passage(self) -> None:
        passage = SENTENCES[2].replace("twice daily", "twice-daily")
        match = find_passage(passage, ABSTRACT, min_similarity=0.95)
        assert match.matched
        assert "twice daily" in match.text
        assert match.text in ABSTRACT

    def test_dropped_word_maps_to_full_source_sentence(self) -> None:
        passage = SENTENCES[4].replace("a median follow-up", "a follow-up")
        match = find_passage(passage, ABSTRACT, min_similarity=0.95)
        assert match.matched
        assert match.text == SENTENCES[4]

    def test_inserted_word_maps_to_source_sentence(self) -> None:
        passage = SENTENCES[3].replace("major bleeding", "major clinical bleeding")
        match = find_passage(passage, ABSTRACT, min_similarity=0.95)
        assert match.matched
        assert match.text == SENTENCES[3]

    def test_edit_in_first_word_of_text(self) -> None:
        passage = SENTENCES[0].replace("Background:", "Backgruond:")
        match = find_passage(passage, ABSTRACT, min_similarity=0.95)
        assert match.matched
        assert match.start == 0
        assert match.text == SENTENCES[0]

    def test_edit_in_last_word_of_text(self) -> None:
        passage = SENTENCES[6].replace("lower mortality.", "lower mortalty.")
        match = find_passage(passage, ABSTRACT, min_similarity=0.95)
        assert match.matched
        assert match.end == len(ABSTRACT)
        assert match.text == SENTENCES[6]

    def test_changed_final_punctuation_keeps_source_punctuation(self) -> None:
        # Dropping the mismatched "." would score higher; spans end on word
        # boundaries so the citation keeps the source's full last word.
        passage = SENTENCES[5].replace("warfarin.", "warfarin;")
        match = find_passage(passage, ABSTRACT, min_similarity=0.95)
        assert match.matched
        assert match.text == SENTENCES[5]

    def test_fuzzy_match_does_not_start_mid_number(self) -> None:
        # "3% per year with apixaban" only occurs inside "2.13% per year ...";
        # returning a span starting at "3%" would misquote the number.
        match = find_passage("3% per ear; with apixaban", ABSTRACT, min_similarity=0.95)
        assert not match.matched

    def test_better_match_beats_location_with_more_seed_votes(self) -> None:
        # The decoy repeats the passage's first 70% verbatim (more seed
        # votes) but ends differently; the source has scattered typos
        # (fewer votes) yet is the only span above the threshold.
        passage = SENTENCES[4]
        cut = int(len(passage) * 0.7)
        decoy = passage[:cut] + " an unrelated ending about placebo recipients."
        source = "".join(
            "Q" if index % 22 == 6 and index < cut and char != " " else char
            for index, char in enumerate(passage)
        )
        text = decoy + " Filler sentence about something else entirely. " + source
        match = find_passage(passage, text, min_similarity=0.95)
        assert match.matched
        assert match.start == text.index(source)
        assert match.text == source

    def test_unicode_whitespace_in_source(self) -> None:
        text = "Mortality was lower (p\u2009<\u20090.05) with\u00a0apixaban."
        match = find_passage("(p < 0.05) with apixaban.", text, min_similarity=0.95)
        assert match.exact
        assert match.text == "(p\u2009<\u20090.05) with\u00a0apixaban."

    def test_no_edge_whitespace_around_fuzzy_span(self) -> None:
        text = "Intro.\n\nAspirin reduces the risk of heart attack in patients.\n\nOther."
        match = find_passage(
            "Aspirin reduces the risk of heart attack in patient.", text, min_similarity=0.95
        )
        assert match.matched
        assert match.text == "Aspirin reduces the risk of heart attack in patients."

    def test_threshold_is_inclusive(self) -> None:
        # One substitution in 20 characters: ratio is exactly 38/40 = 0.95.
        text = "xxxxx abcdefghij klmnopqrs yyyyy"
        match = find_passage("abcdefghij klmnopqrZ", text, min_similarity=0.95)
        assert match.similarity == pytest.approx(0.95)
        assert match.matched
        assert not find_passage("abcdefghij klmnopqrZ", text, min_similarity=0.951).matched

    def test_fuzzy_match_is_not_exact(self) -> None:
        passage = SENTENCES[4].replace("hazard ratio", "hazard-ratio")
        assert not find_passage(passage, ABSTRACT, min_similarity=0.95).exact
        assert find_passage(SENTENCES[4], ABSTRACT, min_similarity=0.95).exact

    def test_long_passage_scores_a_bounded_number_of_windows(
        self, windows_scored: list[int]
    ) -> None:
        # The whole ~1 kB abstract, with two edits, against a ~10 kB text.
        text = " ".join(f"Section {i}. " + " ".join(SENTENCES) for i in range(LONG_TEXT_REPEATS))
        passage = ABSTRACT.replace("twice daily", "twice-daily").replace("Major", "Majr")
        match = find_passage(passage, text, min_similarity=0.95)
        assert match.matched
        # A search scoring one window per text character would exceed the bound.
        assert len(text) > MAX_WINDOWS_SCORED
        assert 0 < windows_scored[0] <= MAX_WINDOWS_SCORED

    def test_long_non_matching_passage_scores_a_bounded_number_of_windows(
        self, windows_scored: list[int]
    ) -> None:
        passage = " ".join(reversed(SENTENCES))
        match = find_passage(passage, ABSTRACT, min_similarity=0.95)
        assert not match.matched
        assert 0 < windows_scored[0] <= MAX_WINDOWS_SCORED

    def test_long_text_scores_a_bounded_number_of_windows(self, windows_scored: list[int]) -> None:
        text = " ".join(f"Section {i}. " + " ".join(SENTENCES) for i in range(LONG_TEXT_REPEATS))
        passage = SENTENCES[4].replace("hazard ratio", "hazard-ratio")
        match = find_passage(passage, text, min_similarity=0.95)
        assert match.matched
        assert match.text == SENTENCES[4]
        assert len(text) > MAX_WINDOWS_SCORED
        assert 0 < windows_scored[0] <= MAX_WINDOWS_SCORED


@pytest.fixture
def windows_scored(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Count the windows ``find_passage`` scores: one ``set_seq1`` each.

    ``SequenceMatcher``'s constructor calls ``set_seq1`` too, so the count is one high.
    """
    count = [0]

    class CountingMatcher(SequenceMatcher):
        def set_seq1(self, a: str) -> None:
            count[0] += 1
            super().set_seq1(a)

    monkeypatch.setattr(passage_match, "SequenceMatcher", CountingMatcher)
    return count


class TestRejection:
    """Paraphrases and fabrications fall below the threshold."""

    def test_fabricated(self) -> None:
        match = find_passage(
            "Statins are the most effective treatment for lowering cholesterol levels.",
            ABSTRACT,
            min_similarity=0.95,
        )
        assert not match.matched
        assert match.text is None
        assert match.start is None and match.end is None
        assert match.similarity < 0.95

    def test_paraphrase(self) -> None:
        text = (
            "Liver transplantation with living related donor has been recently "
            "developed to compensate for the insufficient number of liver grafts "
            "for children."
        )
        passage = (
            "Liver transplantation with living related donors has recently been "
            "developed to address the insufficient number of liver grafts for children"
        )
        match = find_passage(passage, text, min_similarity=0.95)
        assert not match.matched
        assert match.similarity < 0.95

    def test_best_similarity_reported_on_rejection(self) -> None:
        passage = SENTENCES[4].replace("stroke or systemic embolism", "any thromboembolic event")
        match = find_passage(passage, ABSTRACT, min_similarity=0.95)
        assert not match.matched
        assert 0.5 < match.similarity < 0.95

    @pytest.mark.parametrize(
        ("passage", "text"),
        [("", ABSTRACT), ("   ", ABSTRACT), ("some passage", ""), ("ab", "xyz")],
    )
    def test_degenerate_inputs(self, passage: str, text: str) -> None:
        match = find_passage(passage, text, min_similarity=0.95)
        assert match == PassageMatch(similarity=match.similarity)
        assert not match.matched

    def test_passage_too_long_for_text_is_rejected_without_search(self) -> None:
        match = find_passage(ABSTRACT, SENTENCES[5], min_similarity=0.95)
        assert match == PassageMatch(similarity=0.0)


class TestFitsText:
    """_fits_text() bounds the passage length that can reach the threshold."""

    def test_bound(self) -> None:
        # P <= T(2-s)/s: at s=0.5, a 30-char passage fits a 10-char text.
        assert _fits_text(30, 10, 0.5)
        assert not _fits_text(31, 10, 0.5)
        assert _fits_text(10, 10, 1.0)
        assert not _fits_text(11, 10, 1.0)


class TestMaxEditPoints:
    """_max_edit_points() is 2P(1-s)/s, capped at P + T."""

    def test_bound(self) -> None:
        # 2 * 20 * 0.05 / 0.95 = 2.1..., floored.
        assert _max_edit_points(20, 1000, 0.95) == 2
        assert _max_edit_points(20, 1000, 1.0) == 0

    def test_capped_at_passage_plus_text(self) -> None:
        # 2 * 20 * 0.9 / 0.1 = 360, above P + T = 52.
        assert _max_edit_points(20, 32, 0.1) == 52

    def test_vanishing_threshold_does_not_overflow(self) -> None:
        # The quotient is infinite here; uncapped, math.floor raised OverflowError.
        assert _max_edit_points(20, 32, 1e-310) == 52
        assert find_passage("abcdefgh", "xxxx abcdefgX yyyy", 1e-310).matched


class TestPassageMatchInvariants:
    """PassageMatch rejects internally inconsistent values."""

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"similarity": 1.5},
            {"similarity": -0.1},
            {"similarity": 0.9, "start": 1, "end": 4},
            {"similarity": 0.9, "text": "abc"},
            {"similarity": 0.9, "start": 4, "end": 4, "text": ""},
            {"similarity": 0.9, "start": 0, "end": 4, "text": "abc"},
            {"similarity": 0.9, "exact": True},
            {"similarity": 0.9, "start": 0, "end": 3, "text": "abc", "exact": True},
        ],
    )
    def test_inconsistent_values_raise(self, kwargs: dict) -> None:
        with pytest.raises(ValueError):
            PassageMatch(**kwargs)

    def test_consistent_values(self) -> None:
        assert PassageMatch(similarity=0.97, start=2, end=5, text="abc").matched
        assert not PassageMatch(similarity=0.3).matched


def _exhaustive_best(passage: str, text: str, min_similarity: float) -> float:
    """Best similarity over every word-boundary window of admissible length."""
    norm_passage, _ = normalize_with_offsets(passage)
    norm_text, _ = normalize_with_offsets(text)
    length = len(norm_passage)
    starts = [
        i
        for i in range(len(norm_text))
        if norm_text[i] != " " and (i == 0 or norm_text[i - 1] == " ")
    ]
    ends = [
        i
        for i in range(1, len(norm_text) + 1)
        if norm_text[i - 1] != " " and (i == len(norm_text) or norm_text[i] == " ")
    ]
    shortest = length * min_similarity / (2 - min_similarity)
    longest = length * (2 - min_similarity) / min_similarity
    matcher = SequenceMatcher(None, autojunk=False)
    matcher.set_seq2(norm_passage)
    best = 0.0
    for start in starts:
        for end in ends:
            if end - start < shortest:
                continue
            if end - start > longest:
                break
            matcher.set_seq1(norm_text[start:end])
            if matcher.quick_ratio() > best:
                best = max(best, matcher.ratio())
    return best


def _random_edits(passage: str, rng: random.Random) -> str:
    """Apply 1-3 character or word insertions/deletions/substitutions."""
    chars = list(passage)
    for _ in range(rng.randint(1, 3)):
        index = rng.randrange(len(chars))
        operation = rng.choice(["substitute", "insert", "delete", "insert_word"])
        if operation == "substitute":
            chars[index] = rng.choice("abcdefghijklmnopqrstuvwxyz,;")
        elif operation == "insert":
            chars.insert(index, rng.choice("abcdefghijklmnopqrstuvwxyz"))
        elif operation == "delete" and len(chars) > 2:
            del chars[index]
        else:
            chars[index:index] = list(" clinical")
    return "".join(chars)


def test_matches_exhaustive_search_on_random_edits() -> None:
    """Seeded property test: no missed matches, no wrong spans, near-best scores."""
    rng = random.Random(PROPERTY_SEED)
    for _ in range(PROPERTY_TRIALS):
        sentences = SENTENCES[:]
        rng.shuffle(sentences)
        text = " ".join(sentences[:4])
        word_starts = [0] + [i + 1 for i, char in enumerate(text) if char == " "]
        start = rng.choice(word_starts[:-10])
        source = text[start : start + rng.randint(40, 200)]
        passage = _random_edits(source, rng)

        best = _exhaustive_best(passage, text, PROPERTY_THRESHOLD)
        match = find_passage(passage, text, PROPERTY_THRESHOLD)

        assert match.matched == (best >= PROPERTY_THRESHOLD), passage
        if match.matched:
            assert text[match.start : match.end] == match.text
            assert match.text == match.text.strip()
            assert match.similarity >= best - PROPERTY_SCORE_TOLERANCE, passage


class TestExactMatchesCutNothing:
    """An exact match never starts or ends inside a word or a source character."""

    @pytest.mark.parametrize(
        ("passage", "text"),
        [
            ("HR 0.9", "The HR 0.95 (95% CI 0.8-1.1) held."),
            ("5 mg daily", "Patients received 25 mg daily."),
            ("effective in reducing mortality", "The drug was ineffective in reducing mortality."),
            ("typical presentation", "It was an atypical presentation."),
            # Ends inside one source character: "≠" precomposed and decomposed.
            ("the difference was =", "So the difference was \u2260 0 here."),
            ("the difference was =", "So the difference was =\u0338 0 here."),
            # Starts inside one: "½" folds to "1⁄2".
            ("2 dose was given", "A \u00bd dose was given."),
        ],
    )
    def test_a_cut_occurrence_is_not_exact(self, passage: str, text: str) -> None:
        match = find_passage(passage, text, min_similarity=0.95)
        assert not match.exact
        assert match.similarity < 1.0

    def test_a_cut_occurrence_still_matches_fuzzily_where_close(self) -> None:
        # Not exact, but one character from the source's own wording.
        match = find_passage("the difference was =", "So the difference was \u2260 0.", 0.9)
        assert match.matched and not match.exact
        assert match.text == "the difference was \u2260"

    def test_a_later_whole_word_occurrence_is_exact(self) -> None:
        text = "It was ineffective at first, then effective at last."
        match = find_passage("effective at", text, min_similarity=0.95)
        assert match.exact
        assert (match.start, match.text) == (34, "effective at")

    def test_punctuation_next_to_the_edge_cuts_nothing(self) -> None:
        match = find_passage("reduced mortality", "It (reduced mortality).", 0.95)
        assert match.exact and match.text == "reduced mortality"


class TestValidation:
    """Invalid arguments raise instead of silently matching."""

    @pytest.mark.parametrize(
        "threshold", [0.0, -0.1, 1.01, math.nan, math.inf, -math.inf, True, False]
    )
    def test_threshold_out_of_range(self, threshold: float) -> None:
        with pytest.raises(ValueError):
            find_passage("abc", "abc", min_similarity=threshold)

    def test_non_string_inputs(self) -> None:
        with pytest.raises(TypeError):
            find_passage(None, "abc", min_similarity=0.95)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            find_passage("abc", None, min_similarity=0.95)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("fragment", "text", "expected"),
    [
        ("stroke", "Aspirin reduced stroke.", True),
        ("stroke", "Stroke fell. Aspirin reduced stroke.", False),  # case
        ("risk was higher", "Risk was\nhigher. The risk  was higher.", False),  # whitespace
        ("risk was higher", "Risk was hi\u00adgher. Risk was higher.", False),  # soft hyphen
        ("aa", "aaa", False),  # overlapping copies
        ("heart", "Aspirin reduced stroke.", False),  # absent
        ("\u200b ", "Aspirin reduced stroke.", False),  # nothing to look for
    ],
)
def test_occurs_once(fragment: str, text: str, expected: bool) -> None:
    """Copies are counted the way find_passage compares text."""
    assert occurs_once(fragment, text) is expected


def test_occurs_once_rejects_non_strings() -> None:
    """A non-string argument raises instead of counting as absent."""
    with pytest.raises(TypeError):
        occurs_once(None, "abc")  # type: ignore[arg-type]


def test_quote_across_paragraph_break_matches_exactly() -> None:
    text = "Results\n\nAmong patients aged 75 years or older,\n\nthe hazard ratio was 0.91."
    match = find_passage(
        "Among patients aged 75 years or older, the hazard ratio was 0.91.", text, 0.95
    )
    assert match.exact
    assert match.text == "Among patients aged 75 years or older,\n\nthe hazard ratio was 0.91."


def test_repeated_quote_returns_first_occurrence() -> None:
    text = "Abstract. The effect was null. Discussion. The effect was null."
    match = find_passage("The effect was null.", text, 0.95)
    assert match.start == text.index("The effect was null.")


def test_elided_quote_is_rejected() -> None:
    first = "Patients were randomly assigned to drug X or warfarin."
    last = "Major bleeding occurred in 3.1% versus 2.2% of patients."
    filler = " ".join(SENTENCES) * 3  # about 3 000 characters between the two
    text = f"{first} {filler} {last}"
    assert not find_passage(f"{first} … {last}", text, 0.95).matched


def test_information_separators_are_whitespace() -> None:
    match = find_passage("hazard ratio", "the hazard\x1eratio was low", 0.95)
    assert match.exact and match.text == "hazard\x1eratio"
