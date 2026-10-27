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

"""Unit tests for the canonical text model and :func:`assemble`."""

from __future__ import annotations

import pytest

from bmlib.fulltext.canonical import (
    BLOCK_SEPARATOR,
    CELL_SEPARATOR,
    BlockDraft,
    BlockKind,
    CanonicalBlock,
    CanonicalSection,
    CanonicalText,
    SectionDraft,
    SourceFormat,
    TextSpan,
    assemble,
)


def build(title: str, *sections: SectionDraft) -> CanonicalText:
    """Assemble *sections* as plain text, version ``test-1``."""
    return assemble(title, list(sections), SourceFormat.PLAIN_TEXT, "test-1")


class TestAssemble:
    def test_pieces_are_joined_by_the_block_separator(self) -> None:
        doc = build("T", SectionDraft("H", 1, [BlockDraft.paragraph("p")]))
        assert BLOCK_SEPARATOR == "\n\n"
        assert doc.text == "T\n\nH\n\np"

    def test_every_piece_is_a_span_into_the_text(self) -> None:
        doc = build("Title", SectionDraft("Head", 2, [BlockDraft.paragraph("Body")]))
        assert doc.title_span is not None
        assert doc.span_text(doc.title_span) == "Title"
        section = doc.sections[0]
        assert section.level == 2
        assert section.heading_span is not None
        assert doc.span_text(section.heading_span) == "Head"
        assert doc.span_text(section.blocks[0].span) == "Body"

    def test_an_empty_untitled_section_is_dropped(self) -> None:
        doc = build("", SectionDraft("", 1, [BlockDraft.paragraph("  ")]))
        assert doc.sections == ()
        assert doc.text == ""

    def test_a_heading_only_section_is_kept(self) -> None:
        doc = build("", SectionDraft("Figures", 1, []))
        assert len(doc.sections) == 1
        assert doc.sections[0].blocks == ()

    def test_a_paragraph_collapses_its_whitespace(self) -> None:
        doc = build("", SectionDraft("", 1, [BlockDraft.paragraph(" a \n\n b\t c ")]))
        assert doc.text == "a b c"

    def test_a_blank_title_has_no_span(self) -> None:
        assert build("  ").title_span is None

    def test_the_format_and_version_are_carried(self) -> None:
        doc = build("T")
        assert doc.source_format is SourceFormat.PLAIN_TEXT
        assert doc.converter_version == "test-1"


class TestBlockDraft:
    def test_a_row_joins_its_normalised_cells_with_a_tab(self) -> None:
        assert CELL_SEPARATOR == "\t"
        row = BlockDraft.row(["a", " b  c"])
        assert row.kind is BlockKind.TABLE_ROW
        assert row.text == "a\tb c"

    def test_a_row_keeps_an_empty_cell(self) -> None:
        assert BlockDraft.row(["a", "", "c"]).text == "a\t\tc"

    def test_a_row_of_empty_cells_is_an_empty_block(self) -> None:
        doc = build("", SectionDraft("", 1, [BlockDraft.row(["", " "])]))
        assert doc.sections == ()

    def test_a_tab_inside_a_cell_becomes_a_space(self) -> None:
        assert BlockDraft.row(["a\tb"]).text == "a b"


def doc_with(*blocks: CanonicalBlock, text: str = "abcdef") -> CanonicalText:
    """A hand-built text with one untitled section holding *blocks*."""
    return CanonicalText(
        text=text,
        title_span=None,
        sections=(CanonicalSection(level=1, heading_span=None, blocks=blocks),),
        source_format=SourceFormat.PLAIN_TEXT,
        converter_version="test-1",
    )


def para(start: int, end: int) -> CanonicalBlock:
    return CanonicalBlock(BlockKind.PARAGRAPH, TextSpan(start, end))


class TestValidation:
    def test_span_text_refuses_a_span_past_the_end(self) -> None:
        with pytest.raises(ValueError, match="outside"):
            doc_with().span_text(TextSpan(2, 7))

    def test_span_text_refuses_a_reversed_span(self) -> None:
        with pytest.raises(ValueError, match="reversed"):
            doc_with().span_text(TextSpan(3, 2))

    def test_span_text_counts_code_points(self) -> None:
        assert doc_with(text="\U0001d6fdx").span_text(TextSpan(1, 2)) == "x"

    def test_a_valid_text_validates(self) -> None:
        doc_with(para(0, 2), para(4, 6)).validate()

    def test_overlapping_blocks_are_refused(self) -> None:
        with pytest.raises(ValueError, match="order"):
            doc_with(para(0, 3), para(2, 5)).validate()

    def test_a_span_past_the_end_is_refused(self) -> None:
        with pytest.raises(ValueError, match="outside"):
            doc_with(para(0, 9)).validate()

    def test_a_level_below_one_is_refused(self) -> None:
        doc = CanonicalText(
            text="a",
            title_span=None,
            sections=(CanonicalSection(level=0, heading_span=None, blocks=(para(0, 1),)),),
            source_format=SourceFormat.PLAIN_TEXT,
            converter_version="test-1",
        )
        with pytest.raises(ValueError, match="level"):
            doc.validate()


class TestFromDict:
    def test_an_unknown_block_kind_is_refused(self) -> None:
        data = doc_with(para(0, 2)).to_dict()
        data["sections"][0]["blocks"][0]["kind"] = "figure"
        with pytest.raises(ValueError, match="figure"):
            CanonicalText.from_dict(data)

    def test_an_invalid_span_is_refused(self) -> None:
        data = doc_with(para(0, 2)).to_dict()
        data["sections"][0]["blocks"][0]["span"]["end"] = 99
        with pytest.raises(ValueError, match="outside"):
            CanonicalText.from_dict(data)

    def test_a_missing_field_is_refused(self) -> None:
        data = doc_with().to_dict()
        del data["converter_version"]
        with pytest.raises(ValueError, match="converter_version"):
            CanonicalText.from_dict(data)
