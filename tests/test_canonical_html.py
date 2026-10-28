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

"""``canonical_from_html``: a test per rule of its module docstring.

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
from bmlib.fulltext.canonical.html import BLOCKS, PRUNED


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

    def test_hidden_until_found_is_kept_in_any_case(self) -> None:
        doc = canonical_from_html(
            '<div hidden="until-found"><p>a</p></div><div hidden="UNTIL-FOUND">b</div>'
            '<div hidden="until">c</div>'
        )
        assert blocks(doc) == ["a", "b"]

    def test_noembed_markup_is_not_text(self) -> None:
        # Raw text to html5ever: its content would be the literal "<b>x</b>".
        assert blocks(canonical_from_html("<p>a<noembed><b>x</b></noembed>b</p>")) == ["ab"]

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

    def test_an_article_holding_under_half_the_text_is_not_the_root(self) -> None:
        doc = canonical_from_html(
            "<main><p>the paper, a long text</p></main><article><p>a teaser</p></article>"
        )
        assert blocks(doc) == ["the paper, a long text"]

    def test_a_nested_articles_text_counts_towards_the_one_around_it(self) -> None:
        doc = canonical_from_html("<p>x</p><article>a<article>bbbb</article></article>")
        assert blocks(doc) == ["a", "bbbb"]

    def test_nested_articles_are_measured_in_one_walk(self) -> None:
        # Each article measured over its own subtree took 19 s at this depth.
        doc = canonical_from_html("<article>x" * 4000)
        assert len(blocks(doc)) == 4000

    def test_a_hidden_body_leaves_the_head_unread(self) -> None:
        doc = canonical_from_html("<title>T</title><body hidden><p>the paper</p></body>")
        assert (doc.text, doc.sections) == ("T", ())

    def test_a_frameset_page_has_no_text_but_its_title(self) -> None:
        doc = canonical_from_html(
            "<title>T</title><frameset><frame src=a><noframes><p>Read it</p></noframes></frameset>"
        )
        assert (doc.text, doc.sections) == ("T", ())

    def test_a_namespaced_role_is_not_role(self) -> None:
        doc = canonical_from_html("<p>a</p><math xlink:role=main><mi>x</mi></math><p>b</p>")
        assert blocks(doc) == ["a", "x", "b"]

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

    def test_a_space_closes_an_inner_block_too(self) -> None:
        doc = canonical_from_html(
            "<h2><div>Results</div>and more</h2><table><tr><td><p>a</p>b</table>"
        )
        assert pieces(doc) == ["Results and more", "a b"]

    def test_br_in_a_heading_or_a_cell_is_a_space(self) -> None:
        doc = canonical_from_html("<h2>Results<br>and discussion</h2><table><tr><td>a<br>b</table>")
        assert pieces(doc) == ["Results and discussion", "a b"]

    def test_a_headings_inner_blocks_are_spaced(self) -> None:
        doc = canonical_from_html("<h2><span>1.</span><div>Methods</div></h2><p>a</p>")
        assert pieces(doc)[0] == "1. Methods"


#: Elements html5ever drops outside a table; the table tests read them.
_TABLE_PARTS = frozenset({"caption", "table", "tbody", "td", "tfoot", "th", "thead", "tr"})


class TestBlocks:
    # `<body>` merges into the page's own; `<hr>` is void. The <main> keeps an
    # <article> or a <main> from becoming the root.
    @pytest.mark.parametrize("element", sorted(BLOCKS - _TABLE_PARTS - {"body", "hr"}))
    def test_every_block_element_ends_the_paragraph(self, element: str) -> None:
        doc = canonical_from_html(f"<main><div>a<{element}>b</{element}>c</div></main>")
        assert blocks(doc) == ["a", "b", "c"]

    def test_hr_ends_the_paragraph(self) -> None:
        assert blocks(canonical_from_html("<div>a<hr>b</div>")) == ["a", "b"]

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


def test_bytes_are_refused_not_decoded() -> None:
    # markupever would decode a Latin-1 page's é to U+FFFD, in silence.
    with pytest.raises(TypeError, match="not bytes"):
        canonical_from_html(b"<p>caf\xe9</p>")  # type: ignore[arg-type]


def test_a_lone_surrogate_is_a_value_error_naming_it() -> None:
    with pytest.raises(ValueError, match="lone surrogate at index 4"):
        canonical_from_html("<p>a\ud800</p>")


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
