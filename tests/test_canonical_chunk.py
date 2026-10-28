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

"""Unit tests for :func:`chunk_canonical` and its sentence rule."""

from __future__ import annotations

import random

import pytest

from bmlib.fulltext.canonical import (
    ABBREVIATIONS,
    DEFAULT_MAX_CHARS,
    DEFAULT_OVERLAP_CHARS,
    BlockDraft,
    CanonicalText,
    Chunk,
    SectionDraft,
    SourceFormat,
    TextSpan,
    assemble,
    chunk_canonical,
    is_abbreviation,
    is_white_space,
    sentence_spans,
)
from bmlib.fulltext.canonical import chunk as chunk_module


def build(title: str, *sections: SectionDraft) -> CanonicalText:
    """Assemble *sections* as plain text, version ``test-1``."""
    return assemble(title, list(sections), SourceFormat.PLAIN_TEXT, "test-1")


def paragraphs(*texts: str, heading: str = "") -> SectionDraft:
    """A top-level section of paragraphs."""
    return SectionDraft(heading, 1, [BlockDraft.paragraph(t) for t in texts])


def texts(doc: CanonicalText, chunks: list[Chunk]) -> list[str]:
    """The text each chunk covers."""
    return [doc.span_text(chunk.span) for chunk in chunks]


def sentences(text: str) -> list[str]:
    """The sentences of all of *text*."""
    return [text[s.start : s.end] for s in sentence_spans(text, TextSpan(0, len(text)))]


class TestSentenceSpans:
    def test_a_mark_followed_by_whitespace_ends_a_sentence(self) -> None:
        assert sentences("One. Two! Three? Four") == ["One.", "Two!", "Three?", "Four"]

    def test_a_mark_not_followed_by_whitespace_does_not(self) -> None:
        assert sentences("p = 0.05 and 1.5-fold.") == ["p = 0.05 and 1.5-fold."]

    @pytest.mark.parametrize(
        "text",
        [
            "As in Fig. 2 we saw it.",
            "As (Fig. 2) shows.",
            "Smith et al. found it.",
            "Drugs, e.g. aspirin, help.",
            "Seen by Dr. Who today.",
            "A vs. B was tested.",
            "See FIG. 3 here.",
        ],
    )
    def test_an_abbreviation_does_not_end_a_sentence(self, text: str) -> None:
        assert sentences(text) == [text]

    def test_whitespace_around_the_span_is_left_out(self) -> None:
        text = "\tA. B.\t"
        assert sentences(text) == ["A.", "B."]

    def test_whitespace_alone_has_no_sentence(self) -> None:
        assert sentences(" \t ") == []

    def test_spans_point_into_the_whole_text(self) -> None:
        text = "xx One. Two. yy"
        spans = sentence_spans(text, TextSpan(3, 12))
        assert [text[s.start : s.end] for s in spans] == ["One.", "Two."]


class TestIsAbbreviation:
    def test_the_token_is_compared_case_blind(self) -> None:
        text = "see FIG."
        assert is_abbreviation(text, 0, text.index("."))

    def test_opening_marks_are_stripped(self) -> None:
        text = "“(e.g."
        assert is_abbreviation(text, 0, len(text) - 1)

    def test_the_token_does_not_reach_before_start(self) -> None:
        # "fig" only if the token may start at 0; from 1 it is "ig".
        assert is_abbreviation("fig.", 0, 3)
        assert not is_abbreviation("fig.", 1, 3)

    def test_a_non_ascii_token_never_matches(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # U+212A KELVIN SIGN lowercases to ASCII "k" in Python, never in Rust's
        # to_ascii_lowercase; no listed token has a "k" today, so add one.
        monkeypatch.setattr(chunk_module, "ABBREVIATIONS", ABBREVIATIONS | {"kg"})
        assert is_abbreviation("kg.", 0, 2)
        assert not is_abbreviation("\u212ag.", 0, 2)

    def test_an_ordinary_word_is_not_one(self) -> None:
        assert not is_abbreviation("total.", 0, 5)


class TestChunkCanonical:
    def test_a_short_section_is_one_chunk_without_heading_or_title(self) -> None:
        doc = build("Title", paragraphs("A short paragraph.", heading="Intro"))
        chunks = chunk_canonical(doc)
        assert chunks == [Chunk(0, doc.sections[0].blocks[0].span)]

    def test_a_chunk_crosses_blocks_but_not_sections(self) -> None:
        doc = build("", paragraphs("A.", "B."), paragraphs("C."))
        chunks = chunk_canonical(doc)
        assert texts(doc, chunks) == ["A.\n\nB.", "C."]
        assert [c.section_index for c in chunks] == [0, 1]

    def test_a_heading_only_section_has_no_chunk(self) -> None:
        doc = build("", SectionDraft("Empty", 1, []), paragraphs("Body."))
        assert [c.section_index for c in chunk_canonical(doc)] == [1]

    def test_sentences_are_packed_up_to_the_cap(self) -> None:
        doc = build("", paragraphs("Aaaa. Bbbb. Cccc. Dddd."))
        assert texts(doc, chunk_canonical(doc, 11, 0)) == ["Aaaa. Bbbb.", "Cccc. Dddd."]

    def test_the_next_chunk_carries_the_last_whole_sentences_within_the_overlap(self) -> None:
        doc = build("", paragraphs("Aaaa. Bbbb. Cccc. Dddd."))
        assert texts(doc, chunk_canonical(doc, 11, 5)) == [
            "Aaaa. Bbbb.",
            "Bbbb. Cccc.",
            "Cccc. Dddd.",
        ]

    def test_a_last_sentence_longer_than_the_overlap_is_not_carried(self) -> None:
        doc = build("", paragraphs("Aa. Bbbbbbbb. Cc."))
        assert texts(doc, chunk_canonical(doc, 13, 5)) == ["Aa. Bbbbbbbb.", "Cc."]

    def test_no_overlap_when_it_would_leave_no_room_for_new_text(self) -> None:
        doc = build("", paragraphs("Aaaa. Bbbb. Ccccccccc."))
        assert texts(doc, chunk_canonical(doc, 11, 5)) == ["Aaaa. Bbbb.", "Ccccccccc."]

    def test_an_over_long_sentence_is_cut_at_whitespace(self) -> None:
        doc = build("", paragraphs("one two three four"))
        assert texts(doc, chunk_canonical(doc, 9, 0)) == ["one two", "three", "four"]

    def test_a_word_longer_than_the_cap_is_cut_at_the_cap(self) -> None:
        doc = build("", paragraphs("abcdefgh ij"))
        assert texts(doc, chunk_canonical(doc, 3, 0)) == ["abc", "def", "gh", "ij"]

    def test_spans_count_code_points(self) -> None:
        doc = build("", paragraphs("𝛽 é. Next."))
        chunks = chunk_canonical(doc, 5, 0)
        assert texts(doc, chunks) == ["𝛽 é.", "Next."]
        assert chunks[0].span == TextSpan(0, 4)

    def test_table_rows_are_units(self) -> None:
        doc = build(
            "", SectionDraft("", 1, [BlockDraft.row(["", "a"]), BlockDraft.row(["b", "c"])])
        )
        assert texts(doc, chunk_canonical(doc, 3, 0)) == ["a", "b\tc"]

    def test_a_cut_piece_does_not_end_on_whitespace(self) -> None:
        doc = build("", SectionDraft("", 1, [BlockDraft.row(["abc", "", "de"])]))
        assert texts(doc, chunk_canonical(doc, 5, 0)) == ["abc", "de"]

    def test_an_empty_text_has_no_chunks(self) -> None:
        assert chunk_canonical(build("")) == []

    def test_defaults_are_bmlibrarians(self) -> None:
        assert (DEFAULT_MAX_CHARS, DEFAULT_OVERLAP_CHARS) == (1000, 100)

    @pytest.mark.parametrize(
        ("max_chars", "overlap_chars"),
        [(0, 0), (5, 5), (5, 6), (5, -1), (True, 0), (5, 1.0)],
    )
    def test_bad_sizes_are_refused(self, max_chars: object, overlap_chars: object) -> None:
        with pytest.raises(ValueError, match="max_chars"):
            chunk_canonical(build("", paragraphs("x")), max_chars, overlap_chars)  # type: ignore[arg-type]

    def test_an_invalid_canonical_text_is_refused(self) -> None:
        doc = build("", paragraphs("x"))
        broken = CanonicalText("xy", None, doc.sections, doc.source_format, "test-1")
        with pytest.raises(ValueError, match="runs on"):
            chunk_canonical(broken)

    def test_chunk_to_dict(self) -> None:
        assert Chunk(2, TextSpan(3, 5)).to_dict() == {
            "section_index": 2,
            "span": {"start": 3, "end": 5},
        }


class TestInvariants:
    """Over random texts: the cap, the trim, the order, and nothing dropped."""

    ALPHABET = "ab .!?\tFig(é𝛽"

    def random_doc(self, rng: random.Random) -> CanonicalText:
        def para() -> str:
            return "".join(rng.choice(self.ALPHABET) for _ in range(rng.randrange(0, 60)))

        sections = [
            paragraphs(*(para() for _ in range(rng.randrange(0, 4))), heading=rng.choice(["", "H"]))
            for _ in range(rng.randrange(0, 4))
        ]
        return build(rng.choice(["", "T"]), *sections)

    @pytest.mark.parametrize("seed", range(300))
    def test_every_chunking_keeps_the_invariants(self, seed: int) -> None:
        rng = random.Random(seed)
        doc = self.random_doc(rng)
        max_chars = rng.randrange(1, 30)
        overlap = rng.randrange(0, max_chars)
        chunks = chunk_canonical(doc, max_chars, overlap)
        covered: set[int] = set()
        for previous, chunk in zip([None, *chunks], chunks, strict=False):
            span = chunk.span
            assert 0 < span.end - span.start <= max_chars
            assert not is_white_space(doc.text[span.start])
            assert not is_white_space(doc.text[span.end - 1])
            blocks = doc.sections[chunk.section_index].blocks
            assert blocks[0].span.start <= span.start and span.end <= blocks[-1].span.end
            if previous is not None:
                assert (previous.section_index, previous.span.start) < (
                    chunk.section_index,
                    span.start,
                )
                if previous.section_index == chunk.section_index:
                    assert span.end > previous.span.end
                    assert max(0, previous.span.end - span.start) <= overlap
            covered.update(range(span.start, span.end))
        for section in doc.sections:
            for block in section.blocks:
                for i in range(block.span.start, block.span.end):
                    assert i in covered or is_white_space(doc.text[i])
