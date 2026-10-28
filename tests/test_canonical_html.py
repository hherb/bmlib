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

"""``canonical_from_html``: one test per rule of its module docstring.

The byte-exact layouts are in the shared contract
(``tests/test_canonical_text_contract.py``); these tests name the rules.
"""

from __future__ import annotations

import builtins
import sys
from typing import Any

import pytest

from bmlib.fulltext.canonical import (
    BlockKind,
    CanonicalText,
    SourceFormat,
    canonical_from_html,
)
from bmlib.fulltext.canonical.html import PRUNED


def pieces(doc: CanonicalText) -> list[str]:
    """The text of every span, title first."""
    return [doc.span_text(span) for span in doc.spans()]


def blocks(doc: CanonicalText) -> list[str]:
    """The text of every block, in order."""
    return [doc.span_text(b.span) for s in doc.sections for b in s.blocks]


class TestPruning:
    # `<embed>` is void, so "junk" would be its sibling.
    @pytest.mark.parametrize("element", sorted(PRUNED - {"embed"}))
    def test_a_pruned_element_is_dropped_and_the_text_after_it_kept(self, element: str) -> None:
        # A <div>, since <nav>, <aside> and <form> close a <p> as they open.
        doc = canonical_from_html(f"<div>before <{element}>junk</{element}> after</div>")
        assert blocks(doc) == ["before after"]

    def test_noscript_is_dropped_even_in_the_head(self) -> None:
        # Scripting off would move the <img> and "junk" out of the head into
        # the body; html5ever parses with scripting on, in both languages.
        doc = canonical_from_html("<head><noscript><img src=x>junk</noscript></head><p>body</p>")
        assert blocks(doc) == ["body"]

    def test_a_hidden_element_is_dropped(self) -> None:
        doc = canonical_from_html("<p>shown</p><div hidden><p>not shown</p></div>")
        assert blocks(doc) == ["shown"]

    def test_a_comment_is_dropped_and_its_tail_kept(self) -> None:
        assert blocks(canonical_from_html("<p>a<!-- note -->b</p>")) == ["ab"]


class TestRoot:
    def test_the_fullest_article_is_read_and_the_page_around_it_is_not(self) -> None:
        doc = canonical_from_html(
            "<p>site blurb</p><article><p>teaser</p></article>"
            "<article><p>the paper, which is longer</p></article>"
        )
        assert blocks(doc) == ["the paper, which is longer"]

    def test_the_first_of_two_equal_articles_wins(self) -> None:
        doc = canonical_from_html("<article><p>one</p></article><article><p>two</p></article>")
        assert blocks(doc) == ["one"]

    def test_without_an_article_main_is_read(self) -> None:
        doc = canonical_from_html("<div>banner</div><main><p>content</p></main>")
        assert blocks(doc) == ["content"]

    def test_a_role_main_element_counts_as_main(self) -> None:
        doc = canonical_from_html('<div>banner</div><div role="main"><p>content</p></div>')
        assert blocks(doc) == ["content"]

    def test_without_either_the_body_is_read(self) -> None:
        assert blocks(canonical_from_html("<div>a</div><p>b</p>")) == ["a", "b"]

    def test_an_article_inside_a_pruned_element_is_not_a_root(self) -> None:
        doc = canonical_from_html(
            "<aside><article><p>a long sidebar story</p></article></aside><p>x</p>"
        )
        assert blocks(doc) == ["x"]


class TestTitle:
    def test_the_roots_first_h1_is_the_title_and_not_a_section(self) -> None:
        doc = canonical_from_html("<article><h1>The Trial</h1><p>Body.</p><h1>Later</h1></article>")
        assert pieces(doc) == ["The Trial", "Body.", "Later"]
        assert doc.title_span is not None and doc.span_text(doc.title_span) == "The Trial"

    def test_an_empty_h1_is_passed_over(self) -> None:
        doc = canonical_from_html('<h1><img src="logo.png"></h1><h1>Real</h1><p>x</p>')
        assert pieces(doc) == ["Real", "x"]
        assert doc.title_span is not None and doc.span_text(doc.title_span) == "Real"

    def test_without_an_h1_the_citation_title_meta_tag(self) -> None:
        doc = canonical_from_html(
            '<head><title>Junk | Site</title><meta name="Citation_Title" content="Meta  title">'
            "</head><p>x</p>"
        )
        assert pieces(doc) == ["Meta title", "x"]

    def test_an_empty_citation_title_falls_through_to_the_title_element(self) -> None:
        doc = canonical_from_html('<title>Page</title><meta name="citation_title" content=" ">')
        assert pieces(doc) == ["Page"]

    def test_without_either_the_title_element(self) -> None:
        assert pieces(canonical_from_html("<title>Page</title><p>x</p>")) == ["Page", "x"]

    def test_with_none_there_is_no_title(self) -> None:
        assert canonical_from_html("<p>x</p>").title_span is None


class TestSections:
    def test_a_heading_starts_a_section_at_its_level(self) -> None:
        doc = canonical_from_html("<h1>T</h1><h2>Methods</h2><p>a</p><h4>Detail</h4><p>b</p>")
        assert [s.level for s in doc.sections] == [2, 4]

    def test_content_before_the_first_heading_is_an_untitled_top_level_section(self) -> None:
        doc = canonical_from_html("<p>lead</p><h2>Methods</h2><p>a</p>")
        assert doc.sections[0].heading_span is None and doc.sections[0].level == 1

    def test_br_in_a_heading_or_a_cell_is_a_space(self) -> None:
        doc = canonical_from_html("<h2>Results<br>and discussion</h2><table><tr><td>a<br>b</table>")
        assert pieces(doc) == ["Results and discussion", "a b"]

    def test_a_headings_inner_blocks_are_spaced(self) -> None:
        doc = canonical_from_html("<h2><span>1.</span><div>Methods</div></h2><p>a</p>")
        assert pieces(doc)[0] == "1. Methods"


class TestBlocks:
    def test_a_block_element_ends_the_paragraph_and_inline_runs_on(self) -> None:
        doc = canonical_from_html("<div>one <b>bold</b><i>italic</i><p>two</p>three</div>")
        assert blocks(doc) == ["one bolditalic", "two", "three"]

    def test_br_is_a_space(self) -> None:
        assert blocks(canonical_from_html("<p>a<br>b</p>")) == ["a b"]

    def test_an_unclosed_paragraph_is_closed_by_the_next(self) -> None:
        assert blocks(canonical_from_html("<p>a<p>b")) == ["a", "b"]

    def test_entities_are_decoded(self) -> None:
        assert blocks(canonical_from_html("<p>a &amp; b &lt; 5&nbsp;mg</p>")) == ["a & b < 5 mg"]

    def test_pre_is_collapsed_like_any_paragraph(self) -> None:
        assert blocks(canonical_from_html("<pre>a\n   b</pre>")) == ["a b"]


class TestTables:
    def test_a_row_is_its_cells_separated_by_a_tab(self) -> None:
        doc = canonical_from_html(
            "<table><caption>Table 1</caption><tr><th>Arm</th><th>n</th></tr>"
            "<tr><td>Drug</td><td>60</td></tr></table>"
        )
        assert blocks(doc) == ["Table 1", "Arm\tn", "Drug\t60"]
        assert [b.kind for b in doc.sections[0].blocks][1:] == [BlockKind.TABLE_ROW] * 2

    def test_a_cells_inner_blocks_are_spaced(self) -> None:
        doc = canonical_from_html("<table><tr><td><p>a</p><p>b</p></td><td>c</td></tr></table>")
        assert blocks(doc) == ["a b\tc"]

    def test_a_hidden_cell_is_dropped(self) -> None:
        doc = canonical_from_html("<table><tr><td>a</td><td hidden>b</td><td>c</td></tr></table>")
        assert blocks(doc) == ["a\tc"]

    def test_an_empty_cell_keeps_its_column(self) -> None:
        doc = canonical_from_html("<table><tr><td></td><td>c</td></tr></table>")
        assert blocks(doc) == ["\tc"]


def test_deep_nesting_is_walked_without_recursion() -> None:
    # Five times the default recursion limit: every walk is iterative.
    depth = 5 * sys.getrecursionlimit()
    doc = canonical_from_html("<div>" * depth + "<h2>Deep</h2>text" + "</div>" * depth)
    assert pieces(doc) == ["Deep", "text"]


def test_the_version_and_format_are_stamped() -> None:
    doc = canonical_from_html("<p>x</p>")
    assert (doc.source_format, doc.converter_version) == (SourceFormat.HTML, "html-1")


def test_an_empty_page_is_an_empty_text() -> None:
    doc = canonical_from_html("")
    assert (doc.text, doc.sections) == ("", ())


def test_a_missing_markupever_is_an_import_error_naming_the_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_import = builtins.__import__

    def refuse(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "markupever":
            raise ImportError("no markupever")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    with pytest.raises(ImportError, match=r"bmlib\[html\]"):
        canonical_from_html("<p>x</p>")
