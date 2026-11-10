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
import io
import json
import pickle
import sys
import time
from typing import Any

import markupever
import psutil
import pytest

from bmlib.fulltext.canonical import (
    BlockKind,
    CanonicalText,
    HtmlPageTooLargeError,
    HtmlParseMemoryError,
    HtmlParseTimeoutError,
    SourceFormat,
    canonical_from_html,
)
from bmlib.fulltext.canonical import html as html_module
from bmlib.fulltext.canonical.html import BLOCKS, HTML_PARSE_SECONDS, PRUNED


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


class TestParseDeadline:
    """The page is parsed in chunks against a deadline (#563).

    In this process (``_convert``), the child's own work: the monkeypatches
    do not reach a child process.
    """

    # A leading byte-order mark, which is dropped, and one inside a paragraph,
    # which is text: html5ever discards one at the start of every chunk it is fed.
    PAGE = (
        "\ufeff<html><head><title>T&amp;t</title></head><body>\r\n<h1>Caf\u00e9 \U0001f600</h1>"
        "<p>One\ufeff\r\ntwo &lt;three&gt;</p><!-- a comment --><table><tr><td>a</td>"
        "<td>b</td></tr></table><script>if (a < b) {}</script><p>end</p></body></html>"
    )

    def test_a_page_split_into_tiny_chunks_parses_as_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        whole = html_module._convert(self.PAGE)
        assert whole.text.count("\ufeff") == 1
        for chunk in (1, 2, 3, 7):
            monkeypatch.setattr(html_module, "HTML_PARSE_CHUNK", chunk)
            assert html_module._convert(self.PAGE).to_dict() == whole.to_dict(), chunk

    def test_a_parse_past_the_deadline_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The clock reads 0 when the parse starts, then past the deadline.
        readings = iter([0.0, HTML_PARSE_SECONDS + 1])
        monkeypatch.setattr(html_module, "monotonic", lambda: next(readings))
        monkeypatch.setattr(html_module, "HTML_PARSE_CHUNK", 10)
        with pytest.raises(HtmlParseTimeoutError, match="longer than 30 s") as raised:
            html_module._convert(self.PAGE)
        assert (raised.value.parsed, raised.value.length) == (10, len(self.PAGE) - 1)
        assert isinstance(raised.value, ValueError)

    def test_the_clock_starts_before_the_first_chunk_and_is_read_before_each_next(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Each chunk the parser is fed takes one second on a fake clock.
        clock = [0.0]
        real = markupever.Parser

        class SlowParser:
            def __init__(self, options: Any) -> None:
                self._parser = real(options)

            def process(self, content: str) -> None:
                clock[0] += 1
                self._parser.process(content)

            def finish(self) -> Any:
                return self._parser.finish()

        monkeypatch.setattr(markupever, "Parser", SlowParser)
        monkeypatch.setattr(html_module, "monotonic", lambda: clock[0])
        monkeypatch.setattr(html_module, "HTML_PARSE_CHUNK", 10)
        monkeypatch.setattr(html_module, "HTML_PARSE_SECONDS", 1.5)
        with pytest.raises(HtmlParseTimeoutError) as raised:
            html_module._convert(self.PAGE)
        assert raised.value.parsed == 20

    def test_a_page_fed_whole_within_the_budget_is_not_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Every reading the parse makes is inside the budget; any later one is past it.
        page = self.PAGE.removeprefix("\ufeff")
        checks = -(-len(page) // 10) - 1
        readings = iter([0.0] * (1 + checks) + [HTML_PARSE_SECONDS + 1] * 10)
        monkeypatch.setattr(html_module, "monotonic", lambda: next(readings))
        monkeypatch.setattr(html_module, "HTML_PARSE_CHUNK", 10)
        assert pieces(html_module._convert(self.PAGE))[0] == "Caf\u00e9 \U0001f600"

    def test_the_refusal_pickles(self) -> None:
        error = pickle.loads(pickle.dumps(HtmlParseTimeoutError(10, 99)))
        assert (type(error), error.parsed, error.length, str(error)) == (
            HtmlParseTimeoutError,
            10,
            99,
            str(HtmlParseTimeoutError(10, 99)),
        )

    def test_the_deadline_itself_is_not_past_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        readings = iter([0.0] + [HTML_PARSE_SECONDS] * len(self.PAGE))
        monkeypatch.setattr(html_module, "monotonic", lambda: next(readings))
        monkeypatch.setattr(html_module, "HTML_PARSE_CHUNK", 10)
        assert pieces(html_module._convert(self.PAGE))[0] == "Caf\u00e9 \U0001f600"

    def test_a_deeply_nested_page_is_stopped_by_the_real_clock(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 40,000 nested <div> take about 2.5 s to parse whole; at 50 ms the
        # parse is stopped part way, so the work happens chunk by chunk.
        monkeypatch.setattr(html_module, "HTML_PARSE_SECONDS", 0.05)
        depth = 40_000
        page = "<div>" * depth + "x" + "</div>" * depth
        with pytest.raises(HtmlParseTimeoutError, match="longer than 0.05 s") as raised:
            html_module._convert(page)
        assert 0 < raised.value.parsed < len(page)

    def test_a_page_at_the_tag_cap_is_parsed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(html_module, "HTML_MAX_TAGS", self.PAGE.count("<"))
        assert pieces(canonical_from_html(self.PAGE))[0] == "Caf\u00e9 \U0001f600"

    def test_a_page_past_the_tag_cap_is_refused_unparsed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def no_parser(*args: Any) -> None:
            raise AssertionError("parsed")

        monkeypatch.setattr(html_module, "_convert_in_child", no_parser)
        tags = self.PAGE.count("<")
        monkeypatch.setattr(html_module, "HTML_MAX_TAGS", tags - 1)
        with pytest.raises(HtmlPageTooLargeError, match=f"holds {tags} '<'") as raised:
            canonical_from_html(self.PAGE)
        assert raised.value.tags == tags
        assert isinstance(raised.value, ValueError)

    def test_a_page_long_with_data_uris_is_converted(self) -> None:
        # #656: a saved page carries its images inline, and they hold no `<`.
        image = "data:image/png;base64," + "A" * 3_000_000
        page = f'<h1>T</h1><p>One<img src="{image}"> two</p>'
        assert pieces(canonical_from_html(page)) == ["T", "One two"]

    def test_the_tag_refusal_pickles(self) -> None:
        error = pickle.loads(pickle.dumps(HtmlPageTooLargeError(99)))
        assert (type(error), error.tags, str(error)) == (
            HtmlPageTooLargeError,
            99,
            str(HtmlPageTooLargeError(99)),
        )

    def test_a_page_of_one_chunk_never_reads_the_clock_again(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        readings = iter([0.0])
        monkeypatch.setattr(html_module, "monotonic", lambda: next(readings))
        assert pieces(html_module._convert(self.PAGE))[0] == "Caf\u00e9 \U0001f600"


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
    with pytest.raises(ImportError, match=r"bmlib\[html\]"):
        html_module._convert("<p>x</p>")


def test_a_missing_psutil_is_an_import_error_naming_the_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_import = builtins.__import__

    def refuse(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "psutil":
            raise ImportError("no psutil")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    with pytest.raises(ImportError, match=r"no psutil.*bmlib\[html\]"):
        canonical_from_html("<p>x</p>")


def rebuilding(n: int) -> str:
    """#723's page: ``n`` formatting elements, each with its own attribute so
    html5ever's Noah's Ark clause matches none, left open in a block that
    closes; then ``n`` blocks, each of whose text has all ``n`` rebuilt."""
    return "<div>" + "".join(f"<b id={k}>" for k in range(n)) + "</div>" + "<div>x</div>" * n


class TestTheParseRunsInAChildHeldToItsMemory:
    """The parse runs in a child process, killed past its memory limit (#723)."""

    def test_a_page_rebuilding_its_formatting_elements_is_killed_at_the_limit(self) -> None:
        # Parsed whole, this page holds about 1.2 GB.
        page = rebuilding(2_000)
        limit = 150_000_000
        with pytest.raises(HtmlParseMemoryError, match="misnested formatting") as raised:
            html_module._convert_in_child(page, limit, 60)
        assert raised.value.held > limit
        assert (raised.value.length, raised.value.limit) == (len(page), limit)
        assert f"more than the {limit} allowed" in str(raised.value)
        assert isinstance(raised.value, ValueError)
        # A reading comes about one poll late, so not far past the limit.
        assert raised.value.held < 3 * limit

    def test_the_child_is_killed_and_gone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A child that holds 300 MB and would then sleep a minute: only a kill
        # ends it sooner.
        before = {c.pid for c in psutil.Process().children()}
        monkeypatch.setattr(
            html_module, "_CHILD_CODE", "b = b'x' * 300_000_000; import time; time.sleep(60)"
        )
        started = time.monotonic()
        with pytest.raises(HtmlParseMemoryError):
            html_module._convert_in_child("<p>x</p>", 100_000_000, 60)
        assert time.monotonic() - started < 10
        assert {c.pid for c in psutil.Process().children()} <= before

    def test_a_child_that_does_not_answer_is_killed_at_the_deadline(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(html_module, "_CHILD_CODE", "import time; time.sleep(60)")
        started = time.monotonic()
        with pytest.raises(RuntimeError, match=r"did not answer within 0\.5 s"):
            html_module._convert_in_child("<p>x</p>", 1 << 30, 0.5)
        assert time.monotonic() - started < 10

    def test_the_public_function_holds_the_child_to_the_limit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(html_module, "HTML_PARSE_MAX_BYTES", 150_000_000)
        with pytest.raises(HtmlParseMemoryError) as raised:
            canonical_from_html(rebuilding(2_000))
        assert raised.value.limit == 150_000_000
        assert raised.value.held < 3 * 150_000_000

    @pytest.mark.parametrize(
        "page",
        ["", "<p>x</p>", TestParseDeadline.PAGE, "<h1>T</h1><table><tr><td>a<td>b</table>"],
    )
    def test_the_child_converts_as_this_process_does(self, page: str) -> None:
        assert canonical_from_html(page) == html_module._convert(page)

    def test_the_current_directory_is_not_on_the_childs_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        # The child imports json before it sets its path: without -P, the
        # current directory's json.py would be the one it ran.
        (tmp_path / "json.py").write_text("raise SystemExit('planted json')\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "path", [p for p in sys.path if p not in ("", str(tmp_path))])
        assert pieces(canonical_from_html("<p>x</p>")) == ["x"]

    def test_a_child_importing_another_file_refuses(self, tmp_path: Any) -> None:
        with pytest.raises(SystemExit, match="the HTML parse process imported"):
            html_module._child_main(str(tmp_path / "html.py"))

    def test_the_child_reports_its_deadline_passing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def past(html: str) -> CanonicalText:
            raise HtmlParseTimeoutError(10, 99)

        out = io.BytesIO()
        monkeypatch.setattr(html_module, "_convert", past)
        monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"<p>x</p>")))
        monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(out))
        html_module._child_main(html_module.__file__)
        assert json.loads(out.getvalue()) == {"timed_out": [10, 99]}
        with pytest.raises(HtmlParseTimeoutError) as raised:
            html_module._read_child_answer(0, out.getvalue(), b"")
        assert (raised.value.parsed, raised.value.length) == (10, 99)

    def test_a_failed_child_is_a_runtime_error_naming_its_last_line(self) -> None:
        err = b"Traceback (most recent call last):\n  ...\nKeyError: 'x'\n"
        with pytest.raises(RuntimeError, match=r"exit status 1\): KeyError: 'x'") as raised:
            html_module._read_child_answer(1, b"", err)
        assert "standard error ended:\nTraceback" in str(raised.value)
        with pytest.raises(RuntimeError, match="it wrote nothing"):
            html_module._read_child_answer(3, b"", b"")

    def test_a_real_failing_child_carries_its_traceback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(html_module, "_CHILD_CODE", "raise KeyError('x')")
        with pytest.raises(RuntimeError, match=r"exit status 1\): KeyError: 'x'") as raised:
            canonical_from_html("<p>x</p>")
        assert "Traceback" in str(raised.value)

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
    def test_a_child_killed_by_a_signal_names_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            html_module, "_CHILD_CODE", "import os, signal; os.kill(os.getpid(), signal.SIGTERM)"
        )
        with pytest.raises(RuntimeError, match="killed by SIGTERM, which this process did not"):
            canonical_from_html("<p>x</p>")
        with pytest.raises(RuntimeError, match="SIGKILL.*out-of-memory killer"):
            html_module._read_child_answer(-9, b"", b"")

    @pytest.mark.parametrize(
        "out", [b"", b"x", b"{}", b"3", b"[]", b'{"text": 1}', b'{"timed_out": 5}', b"{} {}"]
    )
    def test_an_unreadable_answer_is_a_runtime_error_not_a_refusal(self, out: bytes) -> None:
        with pytest.raises(RuntimeError, match="wrote no answer this process can read") as raised:
            html_module._read_child_answer(0, out, b"a warning")
        assert not isinstance(raised.value, ValueError)
        assert "a warning" in str(raised.value)

    def test_a_child_printing_past_its_answer_is_a_runtime_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(html_module, "_CHILD_CODE", "print('hello')")
        with pytest.raises(RuntimeError, match="wrote no answer"):
            canonical_from_html("<p>x</p>")

    def test_a_frozen_application_is_told_it_cannot_start_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        with pytest.raises(RuntimeError, match="child Python process"):
            canonical_from_html("<p>x</p>")

    def test_an_interpreter_that_cannot_name_itself_is_told_so(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "executable", "")
        with pytest.raises(RuntimeError, match="child Python process"):
            canonical_from_html("<p>x</p>")

    def test_a_child_that_cannot_be_started_is_an_os_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        monkeypatch.setattr(sys, "executable", str(tmp_path / "no-python"))
        with pytest.raises(OSError):
            canonical_from_html("<p>x</p>")

    def test_the_memory_refusal_pickles(self) -> None:
        error = pickle.loads(pickle.dumps(HtmlParseMemoryError(10, 99, 7)))
        assert (type(error), error.held, error.length, error.limit, str(error)) == (
            HtmlParseMemoryError,
            10,
            99,
            7,
            str(HtmlParseMemoryError(10, 99, 7)),
        )
