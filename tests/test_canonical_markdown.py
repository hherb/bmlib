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

import time

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


def test_a_quoted_fence_keeps_its_lines_without_the_markers() -> None:
    # #548: the markers were kept inside the fence, so it never closed and
    # swallowed the heading after it.
    source = "> ```\n> code *x*\n> ```\n\n# Heading\n\nText"
    doc = canonical_from_markdown(source)
    assert [doc.span_text(s.heading_span) for s in doc.sections if s.heading_span] == ["Heading"]
    assert texts(source) == ["code *x*", "Text"]


def test_a_quoted_fence_ends_with_its_quote() -> None:
    assert texts("> ```\n> code\nafter *y*") == ["code", "after y"]
    assert texts("> > ```\n> > deep\n> shallow *y*") == ["deep", "shallow y"]


def test_a_fence_outside_a_quote_keeps_a_quote_marker() -> None:
    assert texts("```\n> kept\n```") == ["> kept"]


def test_a_backtick_in_a_backtick_info_string_is_no_fence() -> None:
    # #548: it opened a fence that swallowed the rest of the document.
    assert texts("```code``` here\n\n*Text*") == ["code here", "Text"]
    assert texts("~~~ `tilde` info\nkept *z*\n~~~") == ["kept *z*"]


@pytest.mark.parametrize(
    "text",
    [
        "(_a " * 20_000,
        "[a " * 20_000,
        "[" * 60_000 + "]",
        "[a](" * 20_000,
        "[a](" * 10_000 + "x" + " " * 10_000 + "y" + ")" * 10_000,
        "<" * 60_000 + ">",
        "<http://a" * 9_000 + " >",
        " ".join("`" * k for k in range(1, 400)),
    ],
    ids=[
        "emphasis",
        "bracket",
        "brackets-one-close",
        "link-targets",
        "nested-targets",
        "angles",
        "autolinks",
        "code-span-runs",
    ],
)
def test_unclosed_openers_cost_linear_time(text: str) -> None:
    # #549: each opener rescanned the paragraph; the emphasis shape took ~61 s
    # at this size and takes ~0.03 s, so the ceiling is a wide margin both ways.
    # The autolink shape took ~15 s, and each backtick run of a new length
    # rescanned to the end.
    started = time.perf_counter()
    strip_inline(text)
    assert time.perf_counter() - started < 2.0


def test_the_converter_version_is_markdown_2() -> None:
    assert canonical_from_markdown("x").converter_version == "markdown-2"
