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

"""Unit tests for the Markdown converter beyond the shared contract."""

from __future__ import annotations

import pytest

from bmlib.fulltext.canonical import canonical_from_markdown, strip_inline


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("**unclosed bold", "**unclosed bold"),
        ("[no target]", "[no target]"),
        ("[label] (space)", "[label] (space)"),
        ("<not a link>", "<not a link>"),
        ("`unclosed code", "`unclosed code"),
        ("***three***", "***three***"),
        ("a \\_b\\_ c", "a _b_ c"),
        ("[**bold** label](u)", "bold label"),
        ("*a*b*", "a*b"),
        ("trailing \\", "trailing \\"),
        ("β *γ* δ", "β γ δ"),
    ],
)
def test_strip_inline(source: str, expected: str) -> None:
    assert strip_inline(source) == expected


def texts(markdown: str) -> list[str]:
    """Every block's text, in order, of *markdown*'s canonical text."""
    doc = canonical_from_markdown(markdown)
    return [doc.span_text(b.span) for s in doc.sections for b in s.blocks]


def test_an_unclosed_fence_keeps_its_lines() -> None:
    assert texts("~~~\nkept *as is*\n") == ["kept *as is*"]


def test_a_hash_without_a_space_is_text() -> None:
    assert texts("#5 is a tag") == ["#5 is a tag"]


def test_a_thematic_break_alone_is_dropped() -> None:
    assert texts("a\n\n---\n\nb") == ["a", "b"]


def test_an_empty_heading_starts_an_untitled_section() -> None:
    doc = canonical_from_markdown("Lead.\n\n##\n\nBody.")
    assert [s.heading_span for s in doc.sections] == [None, None]
    assert [s.level for s in doc.sections] == [1, 2]


def test_the_converter_version_is_markdown_1() -> None:
    assert canonical_from_markdown("x").converter_version == "markdown-1"
