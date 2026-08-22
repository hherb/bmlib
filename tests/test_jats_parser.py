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

"""Tests for bmlib.fulltext.jats_parser."""

import xml.sax
from pathlib import Path

import pytest

from bmlib.fulltext.jats_parser import JATSParser

FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class TestJATSParserMetadata:
    def test_parse_title(self):
        data = _load_fixture("sample_article.xml")
        parser = JATSParser(data)
        article = parser.parse()
        assert article.title != ""

    def test_parse_authors(self):
        data = _load_fixture("sample_article.xml")
        article = JATSParser(data).parse()
        assert len(article.authors) > 0
        assert article.authors[0].surname != ""

    def test_parse_journal(self):
        data = _load_fixture("sample_article.xml")
        article = JATSParser(data).parse()
        assert article.journal != ""

    def test_parse_identifiers(self):
        # Asserted by value, not merely non-empty: the fixture carries a
        # publisher-id holding a filename-form copy of the DOI, and a
        # non-empty check passes just as happily on the wrong one.
        data = _load_fixture("sample_article.xml")
        article = JATSParser(data).parse()
        assert (article.doi, article.pmc_id, article.pmid) == (
            "10.1234/jbr.2024.001",
            "PMC7614751",
            "34567890",
        )


def _article_with_ids(ids: str) -> bytes:
    """Build a minimal article whose <article-meta> carries `ids` verbatim."""
    return f"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    {ids}
    <title-group><article-title>An article</article-title></title-group>
  </article-meta></front>
  <body><sec><title>Intro</title><p>Text.</p></sec></body>
</article>""".encode()


class TestArticleIdentifiers:
    """An identifier is read from its declared type, not from its shape.

    The untyped path is a fallback for documents that omit `pub-id-type`.
    It used to be able to *overwrite* a typed value, so document order
    decided the answer — see the SAGE case in
    `test_a_publisher_id_does_not_overwrite_the_typed_doi`.
    """

    # PMC12759138, as Europe PMC serves it: the publisher-id is the DOI with
    # the slash replaced by an underscore, and it follows the real DOI.
    SAGE_IDS = """<article-id pub-id-type="pmid">41488273</article-id>
    <article-id pub-id-type="pmc">PMC12759138</article-id>
    <article-id pub-id-type="doi">10.1177/20552076251406653</article-id>
    <article-id pub-id-type="publisher-id">10.1177_20552076251406653</article-id>"""

    def test_a_publisher_id_does_not_overwrite_the_typed_doi(self):
        """SAGE stamps every article with a filename-form copy of its DOI."""
        article = JATSParser(_article_with_ids(self.SAGE_IDS)).parse()

        assert article.doi == "10.1177/20552076251406653"

    def test_the_other_identifiers_in_that_document_survive_too(self):
        article = JATSParser(_article_with_ids(self.SAGE_IDS)).parse()

        assert (article.pmid, article.pmc_id) == ("41488273", "PMC12759138")

    def test_a_doi_shaped_publisher_id_is_rejected_on_its_own_merits(self):
        """With no typed DOI to defend it, order cannot be what saves us: a
        DOI always carries a slash, so the underscore form is not one."""
        ids = '<article-id pub-id-type="publisher-id">10.1177_20552076251406653</article-id>'
        article = JATSParser(_article_with_ids(ids)).parse()

        assert article.doi == ""

    def test_a_well_formed_untyped_doi_after_the_typed_one_is_ignored(self):
        """What pins the authority guard on its own.

        On the SAGE document the two guards overlap — the underscore form
        fails the shape test too — so neither is pinned there. A companion
        or collection DOI carried under a type bmlib does not know is a
        perfectly well-formed DOI, and only authority can settle it.
        """
        ids = """<article-id pub-id-type="doi">10.1177/real</article-id>
        <article-id pub-id-type="publisher-id">10.9999/companion</article-id>"""
        article = JATSParser(_article_with_ids(ids)).parse()

        assert article.doi == "10.1177/real"

    def test_a_typed_doi_still_wins_when_the_untyped_id_comes_first(self):
        """The guard is about authority, not about which element came last."""
        ids = """<article-id pub-id-type="publisher-id">10.9999/decoy</article-id>
        <article-id pub-id-type="doi">10.1177/real</article-id>"""
        article = JATSParser(_article_with_ids(ids)).parse()

        assert article.doi == "10.1177/real"

    def test_an_untyped_doi_is_still_read(self):
        """Negative control: the fallback must still do its job, or the two
        guards above would pass against a branch that never fires."""
        ids = "<article-id>10.1234/jbr.2024.001</article-id>"
        article = JATSParser(_article_with_ids(ids)).parse()

        assert article.doi == "10.1234/jbr.2024.001"

    def test_an_unrecognised_id_type_holding_a_real_doi_is_still_read(self):
        """A type bmlib does not know falls through to the same fallback."""
        ids = '<article-id pub-id-type="art-access-id">10.1234/jbr.2024.001</article-id>'
        article = JATSParser(_article_with_ids(ids)).parse()

        assert article.doi == "10.1234/jbr.2024.001"

    def test_versioned_and_internal_pmc_ids_do_not_become_the_pmc_id(self):
        """The shape PMC actually serves: the canonical id, then the rest."""
        ids = """<article-id pub-id-type="pmc">PMC12759138</article-id>
        <article-id pub-id-type="pmcid-ver">PMC12759138.1</article-id>
        <article-id pub-id-type="pmcaid">12759138</article-id>
        <article-id pub-id-type="pmcaiid">12759138</article-id>"""
        article = JATSParser(_article_with_ids(ids)).parse()

        assert article.pmc_id == "PMC12759138"

    def test_a_versioned_pmc_id_alone_is_recognised_and_ignored(self):
        """What pins `pmcid-ver`'s place in the recognised list.

        In the document above the fallback would refuse the versioned id
        anyway, having already got a PMC id — so that test cannot tell
        recognition from arriving second. Only a document carrying no plain
        `pmc` can. (`pmcaid` / `pmcaiid` need no such test: their values are
        bare numerals, which the fallback already declines to guess at, so
        listing them is documentation rather than behaviour.)
        """
        ids = '<article-id pub-id-type="pmcid-ver">PMC12759138.1</article-id>'
        article = JATSParser(_article_with_ids(ids)).parse()

        assert article.pmc_id == ""

    def test_an_untyped_pmc_id_does_not_overwrite_the_typed_one(self):
        ids = """<article-id pub-id-type="pmc">PMC12759138</article-id>
        <article-id pub-id-type="archive-id">PMC0000000</article-id>"""
        article = JATSParser(_article_with_ids(ids)).parse()

        assert article.pmc_id == "PMC12759138"

    def test_a_known_pmc_id_survives_an_untyped_pmc_article_id(self):
        """`FullTextService` passes the PMC id it fetched by. The typed branch
        already refused to overwrite it; the fallback did not."""
        ids = '<article-id pub-id-type="archive-id">PMC0000000</article-id>'
        article = JATSParser(_article_with_ids(ids), known_pmc_id="PMC12759138").parse()

        assert article.pmc_id == "PMC12759138"

    def test_an_untyped_pmc_id_is_still_read_when_nothing_claimed_it(self):
        """Negative control for the two guards above."""
        ids = "<article-id>PMC12759138</article-id>"
        article = JATSParser(_article_with_ids(ids)).parse()

        assert article.pmc_id == "PMC12759138"


class TestAnExhibitBuildersFirstArgumentIsItsId:
    """``_GraphicHolder`` is a base class, so its fields lead by default.

    Both builders inherit ``graphic_href``/``graphic_rank``, and a dataclass
    puts a base's fields *first* in the generated ``__init__`` — so without
    ``kw_only`` on the base, ``_TableBuilder("t1")`` sets the href and leaves
    ``graphic_rank`` ``None`` beside it, which is the one pairing
    ``offer_graphic`` exists to maintain. The next deposit of any rank then
    wins outright. Both parameters are ``str``-compatible at position 0, so
    mypy cannot see it; only this can.
    """

    def test_a_positional_argument_is_the_id_for_both_builders(self):
        from bmlib.fulltext.jats_parser import _FigureBuilder, _TableBuilder

        assert _FigureBuilder("f1").id == "f1"
        assert _TableBuilder("t1").id == "t1"
        assert _TableBuilder("t1").graphic_href == ""

    def test_the_deposit_fields_cannot_be_passed_positionally(self):
        """``offer_graphic`` is their only legitimate writer.

        ``_FigureBuilder`` declares exactly three fields of its own, so a
        fourth positional argument can only be reaching an inherited one — it
        raises here and would silently populate the deposit fields if the base
        were an ordinary dataclass.
        """
        from bmlib.fulltext.jats_parser import _FigureBuilder

        with pytest.raises(TypeError):
            _FigureBuilder("f1", "Figure 1.", "A caption.", "sneaked-in.png")


class TestTableBuilderHeaderClassification:
    def test_row_label_th_not_treated_as_header(self):
        # A table without <thead>/<tbody> whose first row is a data row with a
        # leading <th> row-label must NOT be misclassified as a header row.
        from bmlib.fulltext.jats_parser import _TableBuilder

        b = _TableBuilder()
        b.start_row()
        b.start_cell(is_header=True)
        b.append_cell_text("Gene")
        b.end_cell()
        b.start_cell(is_header=False)
        b.append_cell_text("1.2")
        b.end_cell()
        b.end_row()

        assert b.header_rows == []
        assert len(b.body_rows) == 1

    def test_all_th_row_is_header(self):
        from bmlib.fulltext.jats_parser import _TableBuilder

        b = _TableBuilder()
        b.start_row()
        for text in ("Gene", "Value"):
            b.start_cell(is_header=True)
            b.append_cell_text(text)
            b.end_cell()
        b.end_row()

        assert len(b.header_rows) == 1
        assert b.body_rows == []


class TestJATSParserAbstract:
    def test_titled_section_without_body_preserved(self):
        # A structured-abstract subsection that has a title but no <p> body
        # must still be emitted, not silently dropped.
        xml = (
            b"<article><front><article-meta><abstract>"
            b"<sec><title>Background</title><p>Some text.</p></sec>"
            b"<sec><title>Conclusions</title></sec>"
            b"</abstract></article-meta></front></article>"
        )
        article = JATSParser(xml).parse()
        titles = [s.title for s in article.abstract_sections]
        assert "Background" in titles
        assert "Conclusions" in titles

    def test_structured_abstract(self):
        data = _load_fixture("sample_article.xml")
        article = JATSParser(data).parse()
        assert len(article.abstract_sections) > 0
        # Should have titled sections
        titles = [s.title for s in article.abstract_sections]
        assert any(t != "" for t in titles)

    def test_abstract_content(self):
        data = _load_fixture("sample_article.xml")
        article = JATSParser(data).parse()
        for section in article.abstract_sections:
            assert section.content != ""


class TestJATSParserBody:
    def test_body_sections(self):
        data = _load_fixture("sample_article.xml")
        article = JATSParser(data).parse()
        assert len(article.body_sections) > 0
        assert article.body_sections[0].title != ""

    def test_section_paragraphs(self):
        data = _load_fixture("sample_article.xml")
        article = JATSParser(data).parse()
        # At least one section should have paragraphs
        has_paragraphs = any(len(s.paragraphs) > 0 for s in article.body_sections)
        assert has_paragraphs


class TestJATSParserReferences:
    def test_references(self):
        data = _load_fixture("sample_article.xml")
        article = JATSParser(data).parse()
        assert len(article.references) > 0


class TestJATSParserHTML:
    def test_to_html(self):
        data = _load_fixture("sample_article.xml")
        html = JATSParser(data).to_html()
        assert "<h1>" in html
        assert "<h2>" in html
        assert "Abstract" in html

    def test_html_escaping(self):
        data = _load_fixture("sample_article.xml")
        html = JATSParser(data).to_html()
        # Should not contain unescaped XML artifacts
        assert "<!DOCTYPE" not in html

    def test_to_html_with_known_pmc_id(self):
        data = _load_fixture("sample_article.xml")
        html = JATSParser(data, known_pmc_id="PMC7614751").to_html()
        assert "<h1>" in html


class TestJATSParserHasBody:
    """Telling a real article apart from a metadata-only JATS record.

    Some publishers — medRxiv among them — serve a JATS document made of
    ``<front>`` and ``<back>`` alone for certain preprints. It parses without
    error but carries nothing past the abstract, so consumers need a way to
    tell the two apart rather than treating any parse as full text.
    """

    def test_true_for_article_with_body(self):
        article = JATSParser(_load_fixture("sample_article.xml")).parse()
        assert article.has_body is True

    def test_false_without_body_element(self):
        article = JATSParser(_load_fixture("abstract_only_article.xml")).parse()
        assert article.has_body is False

    def test_back_matter_alone_does_not_count_as_body(self):
        """A <back> section lands in body_sections, so it must not fool has_body."""
        article = JATSParser(_load_fixture("abstract_only_article.xml")).parse()

        # The "Data Availability" section is present and rendered...
        assert any("Data Availability" in s.title for s in article.body_sections)
        # ...but the article still has no body.
        assert article.has_body is False

    def test_parse_with_html_agrees_with_parse(self):
        data = _load_fixture("abstract_only_article.xml")
        article, html = JATSParser(data).parse_with_html()

        assert article.has_body is False
        assert html == JATSParser(data).to_html()
        assert "Why More Doctors" in html


class TestJATSParserUnsectionedBody:
    """``<sec>`` is optional inside ``<body>``.

    A valid article may put its prose in bare ``<p>`` children of ``<body>``.
    Such paragraphs must still reach ``body_sections`` and the rendered HTML —
    and must count towards ``has_body``, or ``FullTextService`` reads the
    article as abstract-only, declines to cache it, and re-fetches it forever.
    """

    UNSECTIONED = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Unsectioned</article-title>
  </title-group></article-meta></front>
  <body>
    <p>Introduction paragraph with real article prose.</p>
    <p>A second substantial paragraph, also unsectioned.</p>
  </body>
</article>"""

    MIXED = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Mixed</article-title>
  </title-group></article-meta></front>
  <body>
    <p>Opening prose before any section.</p>
    <sec><title>Methods</title><p>We did the thing.</p></sec>
    <p>Trailing prose after the section.</p>
  </body>
</article>"""

    def test_unsectioned_paragraphs_are_kept(self):
        article = JATSParser(self.UNSECTIONED).parse()
        paragraphs = [p for s in article.body_sections for p in s.paragraphs]

        assert "Introduction paragraph with real article prose." in paragraphs
        assert "A second substantial paragraph, also unsectioned." in paragraphs

    def test_unsectioned_body_counts_as_a_body(self):
        article = JATSParser(self.UNSECTIONED).parse()
        assert article.has_body is True

    def test_unsectioned_prose_renders(self):
        html = JATSParser(self.UNSECTIONED).to_html()
        assert "Introduction paragraph with real article prose." in html
        assert "A second substantial paragraph, also unsectioned." in html

    def test_implicit_section_has_no_invented_title(self):
        article = JATSParser(self.UNSECTIONED).parse()
        assert len(article.body_sections) == 1
        assert article.body_sections[0].title == ""

    def test_sections_stay_top_level_alongside_loose_prose(self):
        """The implicit section must not swallow a real <sec> as a subsection."""
        article = JATSParser(self.MIXED).parse()
        titles = [s.title for s in article.body_sections]

        assert "Methods" in titles
        methods = next(s for s in article.body_sections if s.title == "Methods")
        assert methods.paragraphs == ["We did the thing."]
        assert methods.subsections == []

    def test_document_order_is_preserved(self):
        article = JATSParser(self.MIXED).parse()
        flattened = [(s.title, tuple(s.paragraphs)) for s in article.body_sections]

        assert flattened == [
            ("", ("Opening prose before any section.",)),
            ("Methods", ("We did the thing.",)),
            ("", ("Trailing prose after the section.",)),
        ]

    def test_back_matter_prose_still_does_not_count_as_body(self):
        """Only <body> gets an implicit section; loose <back> prose must not
        start counting as an article body."""
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Back only</article-title>
  </title-group></article-meta></front>
  <back><p>Loose acknowledgement text.</p></back>
</article>"""
        article = JATSParser(data).parse()
        assert article.has_body is False

    def test_whitespace_only_body_reports_no_body(self):
        """An empty <p> must not open an implicit section — that would make a
        <body> carrying no prose at all look like full text and get cached."""
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Blank</article-title>
  </title-group></article-meta></front>
  <body><p>   </p></body>
</article>"""
        article = JATSParser(data).parse()

        assert article.has_body is False
        assert article.body_sections == []


class TestJATSParserUnsectionedBodyFurniture:
    """Figures and tables are legal direct children of ``<body>``.

    Their captions are ``<p>`` elements, and outside a ``<sec>`` they reach the
    same handler branch as unsectioned prose. The caption must stay on the
    figure or table: routing it to the implicit body section would both blank
    the caption and render it as article prose — and, for a ``<body>`` holding
    nothing but a captioned figure, make ``has_body`` true on furniture alone.
    """

    FIGURE_IN_UNSECTIONED_BODY = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Loose figure</article-title>
  </title-group></article-meta></front>
  <body>
    <p>Loose opening prose.</p>
    <fig id="f1"><label>Figure 1</label>
      <caption><p>A caption for the figure.</p></caption>
      <graphic xlink:href="f1.jpg"/></fig>
    <table-wrap id="t1"><label>Table 1</label>
      <caption><p>A caption for the table.</p></caption></table-wrap>
  </body>
</article>"""

    FIGURE_AFTER_LAST_SECTION = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Floated figure</article-title>
  </title-group></article-meta></front>
  <body>
    <sec><title>Methods</title><p>We did the thing.</p></sec>
    <fig id="f1"><label>Figure 1</label>
      <caption><p>A caption for the figure.</p></caption></fig>
  </body>
</article>"""

    def test_caption_stays_on_the_figure(self):
        article = JATSParser(self.FIGURE_IN_UNSECTIONED_BODY).parse()

        assert [(f.label, f.caption) for f in article.figures] == [
            ("Figure 1", "A caption for the figure.")
        ]

    def test_caption_stays_on_the_table(self):
        article = JATSParser(self.FIGURE_IN_UNSECTIONED_BODY).parse()

        assert [(t.label, t.caption) for t in article.tables] == [
            ("Table 1", "A caption for the table.")
        ]

    def test_captions_do_not_become_body_prose(self):
        article = JATSParser(self.FIGURE_IN_UNSECTIONED_BODY).parse()
        paragraphs = [p for s in article.body_sections for p in s.paragraphs]

        assert paragraphs == ["Loose opening prose."]

    def test_figure_floated_after_the_last_section_keeps_its_caption(self):
        """A <fig> can sit directly under <body> after the final <sec> — a
        normal JATS layout, and one where section_stack is empty again."""
        article = JATSParser(self.FIGURE_AFTER_LAST_SECTION).parse()

        assert [f.caption for f in article.figures] == ["A caption for the figure."]
        assert [(s.title, tuple(s.paragraphs)) for s in article.body_sections] == [
            ("Methods", ("We did the thing.",))
        ]

    def test_a_captioned_figure_alone_is_not_a_body(self):
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Figure only</article-title>
  </title-group></article-meta></front>
  <body>
    <fig id="f1"><label>Figure 1</label>
      <caption><p>A caption for the figure.</p></caption></fig>
  </body>
</article>"""
        article = JATSParser(data).parse()

        assert article.has_body is False


class TestJATSParserCaptionScoping:
    """A caption belongs to its figure or table in *every* document shape.

    ``<fig>`` and ``<table-wrap>`` are usually nested inside a ``<sec>`` — the
    ordinary PMC layout. Caption text is carried in ``<p>`` and ``<title>``
    elements, the same ones that carry section prose and section headings, so
    the handler has to route them by their enclosing ``<caption>`` rather than
    by which of the ``in_*`` flags happens to be set. Getting this wrong blanks
    the caption and reprints it as article prose, and — for ``<title>`` —
    renames the enclosing section after the figure.

    The same scoping keeps table internals out of the prose: cell and footnote
    text reaches the table's own rendering and must not be duplicated into
    ``body_sections`` or appended to the caption.
    """

    FIGURE_IN_SECTION = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Figured</article-title>
  </title-group></article-meta></front>
  <body>
    <sec><title>Methods</title><p>We did the thing.</p>
      <fig id="f1"><label>Figure 1</label>
        <caption><title>Caption heading</title><p>A caption for the figure.</p></caption>
        <graphic xlink:href="f1.jpg"/></fig>
    </sec>
  </body>
</article>"""

    TABLE_IN_SECTION = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Tabled</article-title>
  </title-group></article-meta></front>
  <body>
    <sec><title>Results</title><p>We measured the thing.</p>
      <table-wrap id="t1"><label>Table 1</label>
        <caption><p>A caption for the table.</p></caption>
        <table>
          <thead><tr><th><p>Group</p></th></tr></thead>
          <tbody><tr><td><p>Treated</p></td></tr></tbody>
        </table>
        <table-wrap-foot><fn><p>A footnote under the table.</p></fn></table-wrap-foot>
      </table-wrap>
    </sec>
  </body>
</article>"""

    def test_figure_caption_survives_inside_a_section(self):
        article = JATSParser(self.FIGURE_IN_SECTION).parse()

        assert [f.label for f in article.figures] == ["Figure 1"]
        assert article.figures[0].caption == "Caption heading A caption for the figure."

    def test_figure_caption_does_not_leak_into_section_prose(self):
        article = JATSParser(self.FIGURE_IN_SECTION).parse()

        assert [(s.title, tuple(s.paragraphs)) for s in article.body_sections] == [
            ("Methods", ("We did the thing.",))
        ]

    def test_caption_title_does_not_rename_the_section(self):
        """<caption><title> and <sec><title> are the same element name."""
        article = JATSParser(self.FIGURE_IN_SECTION).parse()

        assert [s.title for s in article.body_sections] == ["Methods"]

    def test_table_caption_survives_inside_a_section(self):
        article = JATSParser(self.TABLE_IN_SECTION).parse()

        assert [(t.label, t.caption) for t in article.tables] == [
            ("Table 1", "A caption for the table.")
        ]

    def test_table_internals_do_not_leak_into_section_prose(self):
        article = JATSParser(self.TABLE_IN_SECTION).parse()

        assert [(s.title, tuple(s.paragraphs)) for s in article.body_sections] == [
            ("Results", ("We measured the thing.",))
        ]

    def test_table_cells_still_render(self):
        """Dropping cell <p> from the prose must not empty the table itself —
        cell text is collected by characters(), not by the <p> handler."""
        article = JATSParser(self.TABLE_IN_SECTION).parse()

        assert "Group" in article.tables[0].html_content
        assert "Treated" in article.tables[0].html_content

    def test_table_cells_do_not_append_to_the_caption(self):
        """Outside a <sec> the cell <p> used to fall through to the caption."""
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Loose table</article-title>
  </title-group></article-meta></front>
  <body>
    <table-wrap id="t1"><label>Table 1</label>
      <caption><p>A caption for the table.</p></caption>
      <table><tbody><tr><td><p>Treated</p></td></tr></tbody></table>
    </table-wrap>
  </body>
</article>"""
        article = JATSParser(data).parse()

        assert article.tables[0].caption == "A caption for the table."

    def test_captions_do_not_count_towards_has_body(self):
        """A section carrying only a captioned figure is not article prose."""
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Caption only</article-title>
  </title-group></article-meta></front>
  <body>
    <sec><title>Figures</title>
      <fig id="f1"><label>Figure 1</label>
        <caption><p>A caption for the figure.</p></caption></fig>
    </sec>
  </body>
</article>"""
        article = JATSParser(data).parse()

        assert article.has_body is False


class TestContributorRoleDeclaredOnTheGroup:
    """JATS lets the contributor role be declared on ``<contrib-group>``.

    ``<contrib contrib-type="author">`` is only one of the two spellings, and
    it is the *minority* one in PMC: the dominant form declares
    ``content-type="author"`` once on the enclosing group and leaves the
    children bare. Reading only the per-contrib attribute drops every author
    from roughly three open-access articles in five (issue #111) — and does
    it as a well-formed empty list, so it reads as "this article lists no
    authors" rather than as a parser that looked in the wrong place.
    """

    GROUP_DECLARED = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <title-group><article-title>Group-declared authors</article-title></title-group>
    <contrib-group content-type="author">
      <contrib><name><surname>Hwang</surname><given-names>Sun-Hee</given-names></name></contrib>
      <contrib><name><surname>Choi</surname><given-names>Kyungsuk</given-names></name></contrib>
    </contrib-group>
  </article-meta></front>
</article>"""

    AUTHOR_AND_EDITOR_GROUPS = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <title-group><article-title>Two groups</article-title></title-group>
    <contrib-group content-type="author">
      <contrib><name><surname>Hwang</surname><given-names>Sun-Hee</given-names></name></contrib>
    </contrib-group>
    <contrib-group content-type="editor">
      <contrib><name><surname>Bloggs</surname><given-names>Joe</given-names></name></contrib>
    </contrib-group>
  </article-meta></front>
</article>"""

    UNTYPED_GROUP = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <title-group><article-title>Untyped group</article-title></title-group>
    <contrib-group>
      <contrib><name><surname>Rivera</surname><given-names>Ana</given-names></name></contrib>
    </contrib-group>
  </article-meta></front>
</article>"""

    EDITOR_INSIDE_AN_AUTHOR_GROUP = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <title-group><article-title>Mixed group</article-title></title-group>
    <contrib-group content-type="author">
      <contrib><name><surname>Hwang</surname><given-names>Sun-Hee</given-names></name></contrib>
      <contrib contrib-type="editor">
        <name><surname>Bloggs</surname><given-names>Joe</given-names></name>
      </contrib>
    </contrib-group>
  </article-meta></front>
</article>"""

    AUTHOR_INSIDE_AN_EDITOR_GROUP = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <title-group><article-title>Mixed group</article-title></title-group>
    <contrib-group content-type="editor">
      <contrib><name><surname>Bloggs</surname><given-names>Joe</given-names></name></contrib>
      <contrib contrib-type="author">
        <name><surname>Hwang</surname><given-names>Sun-Hee</given-names></name>
      </contrib>
    </contrib-group>
  </article-meta></front>
</article>"""

    CONTRIB_OUTSIDE_ANY_GROUP = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <title-group><article-title>A stray contrib</article-title></title-group>
    <contrib-group content-type="editor">
      <contrib><name><surname>Bloggs</surname><given-names>Joe</given-names></name></contrib>
    </contrib-group>
    <contrib><name><surname>Rivera</surname><given-names>Ana</given-names></name></contrib>
  </article-meta></front>
</article>"""

    EDITOR_GROUP_THEN_UNTYPED_GROUP = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <title-group><article-title>Editors first</article-title></title-group>
    <contrib-group content-type="editor">
      <contrib><name><surname>Bloggs</surname><given-names>Joe</given-names></name></contrib>
    </contrib-group>
    <contrib-group>
      <contrib><name><surname>Rivera</surname><given-names>Ana</given-names></name></contrib>
    </contrib-group>
  </article-meta></front>
</article>"""

    EMPTY_ROLE_ATTRIBUTES = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <title-group><article-title>Empty attributes</article-title></title-group>
    <contrib-group content-type="">
      <contrib contrib-type="">
        <name><surname>Rivera</surname><given-names>Ana</given-names></name>
      </contrib>
    </contrib-group>
  </article-meta></front>
</article>"""

    UPPERCASE_ROLES = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <title-group><article-title>Shouted roles</article-title></title-group>
    <contrib-group content-type="Author">
      <contrib><name><surname>Rivera</surname><given-names>Ana</given-names></name></contrib>
      <contrib contrib-type="AUTHOR">
        <name><surname>Hwang</surname><given-names>Sun-Hee</given-names></name>
      </contrib>
    </contrib-group>
  </article-meta></front>
</article>"""

    NESTED_GROUP_INSIDE_A_COLLAB = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <title-group><article-title>A collaboration roster</article-title></title-group>
    <contrib-group content-type="author">
      <contrib><name><surname>Hwang</surname><given-names>Sun-Hee</given-names></name></contrib>
    </contrib-group>
    <contrib-group content-type="editor">
      <contrib><collab>Editorial Board
        <contrib-group>
          <contrib><name><surname>Member</surname><given-names>Bo</given-names></name></contrib>
        </contrib-group>
      </collab></contrib>
      <contrib><name><surname>Bloggs</surname><given-names>Joe</given-names></name></contrib>
    </contrib-group>
  </article-meta></front>
</article>"""

    @staticmethod
    def _surnames(data: bytes) -> list[str]:
        return [a.surname for a in JATSParser(data).parse().authors]

    def test_a_group_declared_author_is_collected(self):
        assert self._surnames(self.GROUP_DECLARED) == ["Hwang", "Choi"]

    def test_a_group_with_no_content_type_is_authors_by_convention(self):
        assert self._surnames(self.UNTYPED_GROUP) == ["Rivera"]

    def test_an_editor_group_is_not_collected_as_authors(self):
        assert self._surnames(self.AUTHOR_AND_EDITOR_GROUPS) == ["Hwang"]

    def test_a_contribs_own_type_overrides_an_author_group(self):
        assert self._surnames(self.EDITOR_INSIDE_AN_AUTHOR_GROUP) == ["Hwang"]

    def test_a_contribs_own_type_overrides_an_editor_group(self):
        assert self._surnames(self.AUTHOR_INSIDE_AN_EDITOR_GROUP) == ["Hwang"]

    def test_a_group_is_read_on_its_own_declaration_not_the_previous_ones(self):
        """A group declaring nothing is authors even after one that did.

        The convention for a bare ``<contrib-group>`` has to hold wherever
        the group sits, not only in a document whose first group is the
        author group.
        """
        assert self._surnames(self.EDITOR_GROUP_THEN_UNTYPED_GROUP) == ["Rivera"]

    def test_a_nested_group_does_not_clear_the_enclosing_groups_role(self):
        """The role is a stack, because ``<contrib-group>`` nests.

        ``<collab>`` legally contains a ``<contrib-group>`` — that is how a
        collaboration's member roster is tagged. Held as a single value, the
        inner group's close cleared the *enclosing* group's declaration, and
        every remaining bare ``<contrib>`` in it was then read as an author
        of this article: the ``editor`` group's own members, collected
        because a sibling's roster happened to close first. Both the
        collaboration's members and the editor after them must stay out.
        """
        assert self._surnames(self.NESTED_GROUP_INSIDE_A_COLLAB) == ["Hwang"]

    def test_a_closed_groups_role_is_not_inherited_outside_it(self):
        """``</contrib-group>`` clears the role, and this is what needs it.

        Not the shape the issue named: a *following* group cannot inherit,
        because opening one assigns the role unconditionally, absent
        attribute included. What the clearing protects is a ``<contrib>``
        with no enclosing group at all — out of place for JATS, and so
        exactly the input a lenient SAX parse still has to answer for. Left
        uncleared it inherits ``editor`` from a group that has closed and is
        dropped, which is issue #111 again in the one shape no well-formed
        document can show.
        """
        assert self._surnames(self.CONTRIB_OUTSIDE_ANY_GROUP) == ["Rivera"]

    def test_an_empty_role_attribute_declares_nothing(self):
        """``contrib-type=""`` is not a claim that this is not an author.

        Read as a declaration it drops the contributor — the same silent
        loss as #111 itself, for a document whose only fault is a stray
        empty attribute. Absent and empty are treated alike on both the
        ``<contrib>`` and the group.
        """
        assert self._surnames(self.EMPTY_ROLE_ATTRIBUTES) == ["Rivera"]

    def test_the_role_is_matched_without_regard_to_case(self):
        """Defensive, and by the module's own precedent rather than by count.

        Every one of the 45 articles measured for #111 spells it lowercase,
        so this is not earned from a population the way bmlib's thresholds
        are. It is here because `pub-id-type` is already folded a few
        handlers below, and because folding cannot cost anything: a role
        that is not "author" in any casing is excluded either way, while an
        unfolded "Author" drops every author in the group — issue #111
        again, in a document nothing would flag.
        """
        assert self._surnames(self.UPPERCASE_ROLES) == ["Rivera", "Hwang"]

    def test_a_per_contrib_type_still_works(self):
        """The spelling that already worked must keep working."""
        data = _load_fixture("sample_article.xml")
        assert [a.surname for a in JATSParser(data).parse().authors] == [
            "Smith",
            "Doe",
            "Chen",
        ]


class TestSubArticlesAreNotTheArticle:
    """A ``<sub-article>`` is a whole article of its own, and not this one.

    PLOS, eLife, BMJ Open and F1000 deposit their peer-review history as one
    ``<sub-article>`` per round, each carrying its own ``<front>`` — DOI,
    title, authors — and its own ``<body>``. Handlers that fire again inside
    one simply overwrite the article's own metadata with the *last* round's
    and append reviewer correspondence to its prose (issue #110). Every one
    of those failures looks like success: a review round's DOI is real and
    resolvable, so it does not 404, and reviewers write about funding,
    conflicts and data availability — the exact vocabulary
    ``TransparencyAnalyzer`` scans for.
    """

    WITH_REVIEW_ROUNDS = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <article-id pub-id-type="doi">10.1371/journal.pgen.1012008</article-id>
    <title-group>
      <article-title>Lack of ANKMY2 suppresses kidney cystogenesis</article-title>
    </title-group>
    <contrib-group content-type="author">
      <contrib contrib-type="author">
        <name><surname>Tanaka</surname><given-names>Yuki</given-names></name>
      </contrib>
    </contrib-group>
    <abstract><p>The article's own abstract.</p></abstract>
  </article-meta></front>
  <body>
    <sec><title>Introduction</title><p>Prose belonging to the article itself.</p></sec>
  </body>
  <back>
    <ref-list>
      <ref id="r1"><mixed-citation>A work the article cites.</mixed-citation></ref>
    </ref-list>
  </back>
  <sub-article article-type="reviewer-report">
    <front-stub>
      <article-id pub-id-type="doi">10.1371/journal.pgen.1012008.r001</article-id>
      <title-group><article-title>Decision Letter 0</article-title></title-group>
      <contrib-group content-type="author">
        <contrib contrib-type="author">
          <name><surname>Reviewer</surname><given-names>One</given-names></name>
        </contrib>
      </contrib-group>
    </front-stub>
    <body><p>Reviewer prose about funding and data availability.</p></body>
  </sub-article>
  <sub-article article-type="reviewer-report">
    <front>
      <article-meta>
        <article-id pub-id-type="doi">10.1371/journal.pgen.1012008.r006</article-id>
        <title-group><article-title>Associated Data</article-title></title-group>
        <contrib-group content-type="author">
          <contrib contrib-type="author">
            <name><surname>Reviewer</surname><given-names>Two</given-names></name>
          </contrib>
        </contrib-group>
        <abstract><p>A review round's abstract.</p></abstract>
      </article-meta>
    </front>
    <body>
      <sec><title>Data Availability Statement</title>
        <p>Correspondence from the sixth round.</p></sec>
    </body>
    <back>
      <ref-list>
        <ref id="rr1"><mixed-citation>A work the reviewer cites.</mixed-citation></ref>
      </ref-list>
    </back>
  </sub-article>
</article>"""

    NESTED = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <article-id pub-id-type="doi">10.1000/outer</article-id>
    <title-group><article-title>The article</article-title></title-group>
  </article-meta></front>
  <body><sec><title>Introduction</title><p>The article's own prose.</p></sec></body>
  <sub-article>
    <front-stub>
      <article-id pub-id-type="doi">10.1000/outer.r001</article-id>
    </front-stub>
    <sub-article>
      <front-stub>
        <article-id pub-id-type="doi">10.1000/outer.r001.inner</article-id>
      </front-stub>
      <body><p>Prose of the innermost nested article.</p></body>
    </sub-article>
    <body>
      <sec><title>After the inner one closed</title><p>Prose of the outer nested article.</p></sec>
    </body>
  </sub-article>
</article>"""

    RESPONSE = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <article-id pub-id-type="doi">10.1000/article</article-id>
    <title-group><article-title>The article</article-title></title-group>
  </article-meta></front>
  <body><sec><title>Introduction</title><p>The article's own prose.</p></sec></body>
  <response>
    <front-stub>
      <article-id pub-id-type="doi">10.1000/article.response</article-id>
      <title-group><article-title>Author response</article-title></title-group>
    </front-stub>
    <body><p>Prose of the response article.</p></body>
  </response>
</article>"""

    BODY_ONLY_IN_THE_SUB_ARTICLE = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <title-group><article-title>Abstract only</article-title></title-group>
    <abstract><p>The article's abstract, and no body of its own.</p></abstract>
  </article-meta></front>
  <sub-article>
    <front-stub><article-id pub-id-type="doi">10.1000/x.r001</article-id></front-stub>
    <body><p>Reviewer prose, which is not this article's body.</p></body>
  </sub-article>
</article>"""

    BEFORE_THE_BODY = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <article-id pub-id-type="doi">10.1000/article</article-id>
    <title-group><article-title>The article</article-title></title-group>
  </article-meta></front>
  <sub-article>
    <front-stub><article-id pub-id-type="doi">10.1000/article.r001</article-id></front-stub>
    <body><sec><title>Reviewer section</title><p>Reviewer prose.</p></sec></body>
  </sub-article>
  <body>
    <sec><title>Introduction</title>
      <p>Prose belonging to the article itself.</p></sec>
  </body>
</article>"""

    RAW_TEXT_IN_A_NESTED_ARTICLE = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <title-group><article-title>The article</article-title></title-group>
  </article-meta></front>
  <body>
    <sec><title>Introduction</title>
      <p>Prose belonging to the article itself.<response>Reviewer text with no
      element of its own.</response> More of the article's own prose.</p></sec>
  </body>
</article>"""

    FIGURES_AND_TABLES_BEFORE_THE_BODY = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <article-id pub-id-type="doi">10.1000/article</article-id>
    <title-group><article-title>The article</article-title></title-group>
  </article-meta></front>
  <sub-article>
    <front-stub><article-id pub-id-type="doi">10.1000/article.r001</article-id></front-stub>
    <body>
      <fig id="rf1">
        <caption><title>Reviewer figure</title><p>A figure the reviewer drew.</p></caption>
      </fig>
      <table-wrap id="rt1">
        <caption><p>A table the reviewer drew.</p></caption>
        <table><tbody><tr><td>Reviewer cell</td></tr></tbody></table>
      </table-wrap>
    </body>
  </sub-article>
  <body>
    <sec><title>Introduction</title>
      <p>Prose belonging to the article itself.</p></sec>
  </body>
</article>"""

    INSIDE_A_SECTION = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <title-group><article-title>The article</article-title></title-group>
    <abstract><p>The article's own abstract.</p></abstract>
  </article-meta></front>
  <body>
    <sec><title>Introduction</title>
      <p>Prose belonging to the article itself.</p>
      <sub-article>
        <front-stub><article-id pub-id-type="doi">10.1000/article.r001</article-id></front-stub>
        <body><sec><title>Reviewer heading</title>
          <p>Reviewer prose about funding.</p></sec></body>
      </sub-article>
    </sec>
  </body>
</article>"""

    @staticmethod
    def _paragraphs(data: bytes) -> list[str]:
        article = JATSParser(data).parse()
        return [p for s in article.body_sections for p in s.paragraphs]

    def test_the_articles_own_doi_survives(self):
        assert JATSParser(self.WITH_REVIEW_ROUNDS).parse().doi == "10.1371/journal.pgen.1012008"

    def test_the_articles_own_title_survives(self):
        article = JATSParser(self.WITH_REVIEW_ROUNDS).parse()
        assert article.title == "Lack of ANKMY2 suppresses kidney cystogenesis"

    def test_a_review_rounds_authors_are_not_the_articles(self):
        article = JATSParser(self.WITH_REVIEW_ROUNDS).parse()
        assert [a.surname for a in article.authors] == ["Tanaka"]

    def test_reviewer_prose_is_not_article_prose(self):
        assert self._paragraphs(self.WITH_REVIEW_ROUNDS) == [
            "Prose belonging to the article itself."
        ]

    def test_a_review_rounds_abstract_is_not_the_articles(self):
        """Two failures at once, which is why the article has one of its own.

        The review round's abstract must not be collected — and the article's
        must not be emitted twice. ``</abstract>`` flushes the buffer without
        clearing it; only the *opening* tag clears, and that is suppressed, so
        a nested ``</abstract>`` reaching the handler re-emits whatever the
        article left there. Asserted against an article that has an abstract,
        because against one that does not the buffer is empty and the
        duplicate flush is a no-op — which made the obvious ``== []``
        assertion vacuous.
        """
        article = JATSParser(self.WITH_REVIEW_ROUNDS).parse()
        assert [s.content for s in article.abstract_sections] == ["The article's own abstract."]

    def test_a_review_rounds_references_are_not_the_articles(self):
        article = JATSParser(self.WITH_REVIEW_ROUNDS).parse()
        assert [r.citation for r in article.references] == ["A work the article cites."]

    def test_reviewer_prose_does_not_reach_the_rendered_html(self):
        html = JATSParser(self.WITH_REVIEW_ROUNDS).to_html()
        assert "Correspondence from the sixth round." not in html
        assert "Reviewer prose about funding and data availability." not in html
        assert "Prose belonging to the article itself." in html

    def test_a_sub_articles_body_is_not_this_articles_body(self):
        """``has_body`` decides whether ``FullTextService`` caches the result.

        Counting a review round as the article's body makes an abstract-only
        document look like a full text, which is cached and never looked for
        again.
        """
        assert JATSParser(self.BODY_ONLY_IN_THE_SUB_ARTICLE).parse().has_body is False

    def test_an_inner_sub_article_closing_does_not_re_admit_the_outer_one(self):
        """A depth, not a flag: JATS permits a sub-article inside one.

        A boolean cleared by the inner ``</sub-article>`` lets the remainder
        of the outer one back in, which is the whole defect again for every
        element after that point.
        """
        article = JATSParser(self.NESTED).parse()

        assert article.doi == "10.1000/outer"
        assert self._paragraphs(self.NESTED) == ["The article's own prose."]

    def test_the_article_survives_a_nested_article_that_precedes_it(self):
        """The suppression has to hold on the *opening* tag as well.

        JATS puts ``<sub-article>`` last, so suppressing only the closing
        tags looks sufficient — every output is written on a close. It is
        not, because the opens leave state behind: a nested ``<sec>`` pushes
        a section builder that no close pops, and the article's own section
        is then filed as a subsection of a review round's and never flushed
        to ``body_sections``. The article loses its entire body, silently, to
        a document that is merely out of order rather than malformed — and
        nothing here validates JATS.
        """
        article = JATSParser(self.BEFORE_THE_BODY).parse()

        assert self._paragraphs(self.BEFORE_THE_BODY) == ["Prose belonging to the article itself."]
        assert article.has_body is True

    def test_raw_text_inside_a_nested_article_is_not_article_prose(self):
        """``characters()`` runs through the suppressed region, and it writes.

        ``startElement`` returns early and ``endElement`` skips its handlers,
        but character data is delivered by neither. Text sitting *directly*
        inside a nested article — not wrapped in a child that pushes its own
        buffer — lands in whichever buffer is open, which is the article's
        own paragraph. Nothing here validates JATS, so that shape has to be
        answered for rather than assumed away.
        """
        assert self._paragraphs(self.RAW_TEXT_IN_A_NESTED_ARTICLE) == [
            "Prose belonging to the article itself. More of the article's own prose."
        ]

    def test_a_nested_articles_figures_and_tables_are_not_the_articles(self):
        """Neither the float nor the state its open tag would leave behind.

        The suppression is tested on the *opening* tag here as well as the
        close: were ``<fig>``/``<table-wrap>`` handled above the guard, the
        matching closes would still be suppressed, so ``in_figure`` and
        ``in_table_wrap`` would stay set and swallow everything after them —
        the article's own body included, which is what decides whether the
        result is worth caching.
        """
        article = JATSParser(self.FIGURES_AND_TABLES_BEFORE_THE_BODY).parse()
        assert article.figures == []
        assert article.tables == []
        assert self._paragraphs(self.FIGURES_AND_TABLES_BEFORE_THE_BODY) == [
            "Prose belonging to the article itself."
        ]
        assert article.has_body is True

    def test_a_nested_article_inside_a_section_does_not_extend_it(self):
        """The closing half of the suppression, on its own.

        With a nested article opened inside the article's own ``<sec>``, the
        outer section is still on the stack, so the ``<p>`` and ``<title>``
        handlers are live: unguarded, the review round's prose is appended to
        the article's section and its heading replaces the article's own.
        """
        article = JATSParser(self.INSIDE_A_SECTION).parse()
        assert [(s.title, s.paragraphs) for s in article.body_sections] == [
            ("Introduction", ["Prose belonging to the article itself."])
        ]

    def test_a_suppressed_nested_article_is_counted(self):
        """Silent removal is the failure mode this whole fix is about.

        A nested article can carry most of a document's prose — a peer-review
        history, or the alternative-language full text SciELO deposits as
        ``article-type="translation"`` — and discarding it changes neither
        ``has_body`` nor ``content_kind``, which between them only report
        *total* loss. Without a count, nothing anywhere in the system records
        that the parser saw a nested article at all.
        """
        assert JATSParser(self.WITH_REVIEW_ROUNDS).parse().suppressed_nested_articles == 2

    def test_a_nested_article_inside_another_is_counted_too(self):
        """The count is of articles suppressed, not of regions entered."""
        assert JATSParser(self.NESTED).parse().suppressed_nested_articles == 2

    def test_an_article_with_no_nested_article_counts_none(self):
        data = _load_fixture("sample_article.xml")
        assert JATSParser(data).parse().suppressed_nested_articles == 0

    def test_a_response_is_treated_like_a_sub_article(self):
        article = JATSParser(self.RESPONSE).parse()

        assert article.doi == "10.1000/article"
        assert article.title == "The article"
        assert self._paragraphs(self.RESPONSE) == ["The article's own prose."]


def _article_with_body(body: str) -> bytes:
    """Wrap ``body`` markup in a minimal well-formed JATS article."""
    return f"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <article-id pub-id-type="pmc">PMC1234567</article-id>
    <title-group><article-title>Real article</article-title></title-group>
  </article-meta></front>
  <body>
{body}
  </body>
</article>""".encode()


class TestNestedFiguresKeepTheirParent:
    """A ``<fig>`` may contain another ``<fig>``, and the parent must survive it.

    eLife wraps every figure supplement inside the figure it belongs to, in
    19.6% of 225 surveyed open-access articles — re-measured by
    ``scripts/sample_jats_exhibits.py`` at 0.7% of a general draw, both of
    them eLife, so it is that publisher's house style costing about half of
    *its* figures rather than a general convention. A single ``current_figure``
    slot is overwritten by the inner open, appended and cleared by the inner
    close, and the parent's own ``</fig>`` then finds nothing to build — so the
    parent figure, its label, caption and graphic, is lost outright (issue
    #115). Measured on PMC8754430: 9 of 12 figures, the three missing ones
    being exactly those carrying supplements.
    """

    PARENT_AND_SUPPLEMENT = _article_with_body("""
    <sec>
      <title>Results</title>
      <p>Section prose.</p>
      <fig id="fig2">
        <label>Figure 2.</label>
        <caption><title>Parent figure caption.</title></caption>
        <graphic xlink:href="parent.jpg"/>
        <p>
          <fig id="fig2s1">
            <label>Figure 2-figure supplement 1.</label>
            <caption><title>Supplement caption.</title></caption>
            <graphic xlink:href="supplement.jpg"/>
          </fig>
        </p>
      </fig>
    </sec>""")

    def test_the_parent_figure_is_not_dropped(self):
        article = JATSParser(self.PARENT_AND_SUPPLEMENT).parse()

        assert [f.label for f in article.figures] == [
            "Figure 2.",
            "Figure 2-figure supplement 1.",
        ]

    def test_the_parent_is_listed_where_it_opened_not_where_it_closed(self):
        """Document order: the parent opens first, so it is listed first.

        Pop-and-append restores the parent but emits it *after* its own
        supplement, because a figure is built at its end tag and the child's
        comes first. The slot is what makes this test distinguish the two.
        """
        article = JATSParser(self.PARENT_AND_SUPPLEMENT).parse()

        assert [f.id for f in article.figures] == ["fig2", "fig2s1"]

    def test_each_graphic_belongs_to_the_innermost_open_figure(self):
        article = JATSParser(self.PARENT_AND_SUPPLEMENT).parse()

        assert [f.graphic_url for f in article.figures] == ["parent.jpg", "supplement.jpg"]

    def test_each_caption_belongs_to_the_innermost_open_figure(self):
        article = JATSParser(self.PARENT_AND_SUPPLEMENT).parse()

        assert [f.caption for f in article.figures] == [
            "Parent figure caption.",
            "Supplement caption.",
        ]

    def test_the_parents_remaining_internals_do_not_leak_into_the_section(self):
        """The other half of #115: the inner close cleared ``in_figure``.

        A ``<fig>`` almost always sits inside a ``<sec>``, so what the parent
        had left was read under the section's rules and reprinted as article
        prose — reaching ``body_sections``, ``has_body`` and the rendered
        HTML, and so any downstream scan over parser output. (Not
        ``bmlib.transparency``, which regexes the raw XML itself and never
        sees ``JATSParser`` — that exposure is issue #119.)

        The prose *after* the parent closes is the other end of the same
        flag, and pins it going **off**. Deriving ``in_figure`` from the slot
        list rather than the stack — a five-character edit — leaves it true
        for the rest of the document, swallowing every later paragraph and
        every later section title, and no fixture that stops at the ``</fig>``
        can tell.
        """
        article = JATSParser(
            _article_with_body("""
    <sec>
      <title>Results</title>
      <p>Section prose.</p>
      <fig id="fig2">
        <label>Figure 2.</label>
        <p><fig id="fig2s1"><label>Figure 2-figure supplement 1.</label></fig></p>
        <p>Parent figure internals after the supplement.</p>
      </fig>
      <p>Section prose after the figure.</p>
    </sec>
    <sec><title>Discussion</title><p>Prose in the next section.</p></sec>""")
        ).parse()

        assert [(s.title, tuple(s.paragraphs)) for s in article.body_sections] == [
            ("Results", ("Section prose.", "Section prose after the figure.")),
            ("Discussion", ("Prose in the next section.",)),
        ]

    def test_the_parent_is_current_again_once_its_supplement_closes(self):
        """The positive counterpart: *current*, not merely open.

        Every other case here loads the parent before the child opens, so it
        passes whether the pop restores the parent or leaves the child current.
        Depositing the parent's own label, caption and graphic *after* the
        child closes is the order that tells those apart.
        """
        article = JATSParser(
            _article_with_body("""
    <sec>
      <title>Results</title>
      <fig id="fig2">
        <p>
          <fig id="fig2s1">
            <label>Figure 2-figure supplement 1.</label>
            <caption><title>Supplement caption.</title></caption>
            <graphic xlink:href="supplement.jpg"/>
          </fig>
        </p>
        <label>Figure 2.</label>
        <caption><title>Parent figure caption.</title></caption>
        <graphic xlink:href="parent.jpg"/>
      </fig>
    </sec>""")
        ).parse()

        assert [f.label for f in article.figures] == [
            "Figure 2.",
            "Figure 2-figure supplement 1.",
        ]
        assert [f.caption for f in article.figures] == [
            "Parent figure caption.",
            "Supplement caption.",
        ]
        assert [f.graphic_url for f in article.figures] == ["parent.jpg", "supplement.jpg"]

    def test_figures_nested_three_deep_stay_in_document_order(self):
        """The corpus tops out at two; nothing should start caring at three."""
        article = JATSParser(
            _article_with_body("""
    <sec>
      <title>Results</title>
      <fig id="a">
        <label>A.</label>
        <p><fig id="b">
          <label>B.</label>
          <p><fig id="c"><label>C.</label></fig></p>
        </fig></p>
      </fig>
    </sec>""")
        ).parse()

        assert [f.label for f in article.figures] == ["A.", "B.", "C."]

    def test_an_unnested_figure_still_works(self):
        """The ordinary shape, which is 80.4% of articles."""
        article = JATSParser(
            _article_with_body("""
    <sec>
      <title>Results</title>
      <fig id="f1">
        <label>Figure 1.</label>
        <caption><p>Only figure.</p></caption>
        <graphic xlink:href="f1.jpg"/>
      </fig>
    </sec>""")
        ).parse()

        assert [(f.id, f.label, f.caption, f.graphic_url) for f in article.figures] == [
            ("f1", "Figure 1.", "Only figure.", "f1.jpg")
        ]


class TestAnExhibitLabelIsNotAFootnoteMarker:
    """A ``<fn>`` carries its own marker as a ``<label>``, and it is not the
    exhibit's number.

    ``<label>`` was routed on the ambient "am I in a figure/table?" flags
    alone, so a footnote marker — ``a``, ``b``, ``*`` — overwrote the exhibit's
    own number, last one winning (issue #116). Measured: 27 of 225 surveyed
    articles (12.0%) carry a labelled ``<table-wrap-foot><fn>``. The table
    loses its number wherever it is rendered or cross-referenced, and an empty
    label is not inert either — the renderer substitutes ``Table {i + 1}``, so
    the symptom is an invented number rather than a blank.
    """

    def test_a_table_footnote_marker_does_not_overwrite_the_tables_number(self):
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="T1">
        <label>Table 1.</label>
        <caption><title>Commonly asked questions.</title></caption>
        <table><tbody><tr><td>12.3</td></tr></tbody></table>
        <table-wrap-foot><fn id="T1_FN1"><label>a</label>
          <p>AI: artificial intelligence.</p></fn></table-wrap-foot>
      </table-wrap>
    </sec>""")
        ).parse()

        assert [t.label for t in article.tables] == ["Table 1."]

    def test_the_last_of_several_footnote_markers_does_not_win_either(self):
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="T1">
        <label>Table 1.</label>
        <table><tbody><tr><td>12.3</td></tr></tbody></table>
        <table-wrap-foot>
          <fn><label>a</label><p>Adjusted for age.</p></fn>
          <fn><label>b</label><p>Adjusted for sex.</p></fn>
        </table-wrap-foot>
      </table-wrap>
    </sec>""")
        ).parse()

        assert [t.label for t in article.tables] == ["Table 1."]

    def test_a_figure_footnote_marker_does_not_overwrite_the_figures_number(self):
        """``in_figure`` has the identical hole — JATS allows ``<fn>`` in ``<fig>``."""
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <fig id="f1">
        <label>Figure 1.</label>
        <caption><p>A figure.</p></caption>
        <fn><label>*</label><p>Scale bar 10um.</p></fn>
      </fig>
    </sec>""")
        ).parse()

        assert [f.label for f in article.figures] == ["Figure 1."]

    def test_a_figure_opened_inside_a_footnote_keeps_its_own_label(self):
        """Why the depth is compared against the exhibit's, never against zero.

        JATS lets a ``<fig>`` open *inside* a footnote. "Am I inside a
        footnote?" is therefore the wrong question — it eats the nested
        exhibit's own label, which is #116 again one level down.
        """
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="T1">
        <label>Table 1.</label>
        <table><tbody><tr><td>12.3</td></tr></tbody></table>
        <table-wrap-foot><fn><label>a</label>
          <p><fig id="ffn"><label>Figure S1.</label>
            <caption><p>A figure inside a footnote.</p></caption></fig></p>
        </fn></table-wrap-foot>
      </table-wrap>
    </sec>""")
        ).parse()

        assert [t.label for t in article.tables] == ["Table 1."]
        assert [f.label for f in article.figures] == ["Figure S1."]

    def test_a_table_opened_inside_a_figures_footnote_keeps_its_own_label(self):
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <fig id="f1">
        <label>Figure 1.</label>
        <fn><label>*</label>
          <p><table-wrap id="Tfn"><label>Table S1.</label>
            <table><tbody><tr><td>1</td></tr></tbody></table></table-wrap></p>
        </fn>
      </fig>
    </sec>""")
        ).parse()

        assert [f.label for f in article.figures] == ["Figure 1."]
        assert [t.label for t in article.tables] == ["Table S1."]

    def test_the_exhibits_own_label_still_arrives_after_its_footnote_closes(self):
        """A label deposited after the footnote is the exhibit's again."""
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="T1">
        <table><tbody><tr><td>12.3</td></tr></tbody></table>
        <table-wrap-foot><fn><label>a</label><p>Adjusted.</p></fn></table-wrap-foot>
        <label>Table 1.</label>
      </table-wrap>
    </sec>""")
        ).parse()

        assert [t.label for t in article.tables] == ["Table 1."]

    def test_a_footnote_groups_own_label_is_not_the_tables_number_either(self):
        """Why the container is counted and not only ``<fn>``.

        A ``<fn-group>`` carries a heading of its own — "Notes",
        "Abbreviations" — as a ``<label>``, and that is no more the table's
        number than a marker is. JATS admits ``<fn-group>`` here only inside
        ``<table-wrap-foot>``, so counting the container covers it without a
        member of its own.
        """
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="T1">
        <label>Table 1.</label>
        <table><tbody><tr><td>12.3</td></tr></tbody></table>
        <table-wrap-foot><fn-group><label>Notes</label>
          <fn><p>Adjusted for age.</p></fn></fn-group></table-wrap-foot>
      </table-wrap>
    </sec>""")
        ).parse()

        assert [t.label for t in article.tables] == ["Table 1."]

    def test_a_reference_label_is_still_read(self):
        """The third branch of the same routing must keep working."""
        article = JATSParser(
            b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>T</article-title>
  </title-group></article-meta></front>
  <body><sec><title>Methods</title><p>Prose.</p></sec></body>
  <back><ref-list><ref id="CR1"><label>1</label>
    <element-citation><source>J</source><year>2020</year></element-citation>
  </ref></ref-list></back>
</article>"""
        ).parse()

        assert [r.label for r in article.references] == ["1"]


def _figure_with_graphics(graphics: str) -> bytes:
    return _article_with_body(f"""
    <sec><title>Results</title>
      <fig id="f1"><label>Figure 1.</label>
{graphics}
      </fig>
    </sec>""")


class TestChoosingAmongSeveralGraphics:
    """A figure commonly deposits the same image more than once.

    Only one href fits the model, and the parser kept the last, so a figure
    resolved to the thumbnail publishers deposit second (issue #117). Measured
    across 225 open-access articles: 58.0% of 959 figures carry more than one
    ``<graphic>``, and 52.9% end on a thumbnail.

    Position cannot decide it, because the two multi-graphic conventions
    disagree about order: a thumbnail is deposited *last* (PLOS, Springer)
    while an ``<alternatives>`` archival master is deposited *first*, so
    first-wins trades the thumbnail for a TIFF no renderer displays. The
    deposits are ranked instead, and a new one is accepted only when it is
    *strictly* better — which is what makes the first win among equals.
    """

    def test_a_thumbnail_deposited_last_does_not_beat_the_image(self):
        article = JATSParser(
            _figure_with_graphics("""
        <graphic content-type="image" xlink:href="pone.0338891.g001.jpg"/>
        <graphic content-type="thumb" xlink:href="pone.0338891.g001.gif"/>""")
        ).parse()

        assert article.figures[0].graphic_url == "pone.0338891.g001.jpg"

    def test_a_thumbnail_deposited_first_does_not_win_either(self):
        """The order that catches an attribute dropped from the predicate.

        With the thumbnail last, plain first-wins already resolves the image,
        so a test in that order passes even with ``content-type`` never
        consulted. Only this order can fail.
        """
        article = JATSParser(
            _figure_with_graphics("""
        <graphic content-type="thumb" xlink:href="g001.gif"/>
        <graphic content-type="image" xlink:href="g001.jpg"/>""")
        ).parse()

        assert article.figures[0].graphic_url == "g001.jpg"

    def test_specific_use_marks_a_thumbnail_deposited_last(self):
        article = JATSParser(
            _figure_with_graphics("""
        <graphic xlink:href="fig1.jpg"/>
        <graphic specific-use="thumbnail" xlink:href="fig1-thumb.gif"/>""")
        ).parse()

        assert article.figures[0].graphic_url == "fig1.jpg"

    def test_specific_use_marks_a_thumbnail_deposited_first(self):
        article = JATSParser(
            _figure_with_graphics("""
        <graphic specific-use="thumbnail" xlink:href="fig1-thumb.gif"/>
        <graphic xlink:href="fig1.jpg"/>""")
        ).parse()

        assert article.figures[0].graphic_url == "fig1.jpg"

    def test_the_content_type_comparison_folds_case(self):
        """Neither attribute is case-controlled; both are open-valued."""
        article = JATSParser(
            _figure_with_graphics("""
        <graphic content-type="Thumb" xlink:href="fig1-thumb.gif"/>
        <graphic xlink:href="fig1.jpg"/>""")
        ).parse()

        assert article.figures[0].graphic_url == "fig1.jpg"

    def test_the_specific_use_comparison_folds_case(self):
        article = JATSParser(
            _figure_with_graphics("""
        <graphic specific-use="THUMBNAIL" xlink:href="fig1-thumb.gif"/>
        <graphic xlink:href="fig1.jpg"/>""")
        ).parse()

        assert article.figures[0].graphic_url == "fig1.jpg"

    def test_a_figure_carrying_only_thumbnails_keeps_one(self):
        """A thumbnail is held provisionally, not refused."""
        article = JATSParser(
            _figure_with_graphics("""
        <graphic content-type="thumb" xlink:href="only-thumb.gif"/>""")
        ).parse()

        assert article.figures[0].graphic_url == "only-thumb.gif"

    def test_an_archival_master_deposited_first_does_not_win(self):
        """``<alternatives>`` deposits the TIFF first; no renderer displays it."""
        article = JATSParser(
            _figure_with_graphics("""
        <alternatives>
          <graphic mime-subtype="tiff" xlink:href="fig1.tif"/>
          <graphic mime-subtype="jpeg" xlink:href="fig1.jpg"/>
        </alternatives>""")
        ).parse()

        assert article.figures[0].graphic_url == "fig1.jpg"

    def test_an_archival_master_deposited_last_does_not_win_either(self):
        article = JATSParser(
            _figure_with_graphics("""
        <alternatives>
          <graphic mime-subtype="jpeg" xlink:href="fig1.jpg"/>
          <graphic mime-subtype="eps" xlink:href="fig1.eps"/>
        </alternatives>""")
        ).parse()

        assert article.figures[0].graphic_url == "fig1.jpg"

    def test_a_thumbnail_beats_an_archival_master(self):
        """Three tiers, not two: a thumbnail at least renders."""
        article = JATSParser(
            _figure_with_graphics("""
        <graphic mime-subtype="tiff" xlink:href="fig1.tif"/>
        <graphic content-type="thumb" xlink:href="fig1-thumb.gif"/>""")
        ).parse()

        assert article.figures[0].graphic_url == "fig1-thumb.gif"

    def test_every_archival_mime_subtype_loses_to_a_web_image(self):
        """All four members of the reject-list, each in the losing position."""
        for subtype in ("tiff", "tif", "eps", "postscript"):
            article = JATSParser(
                _figure_with_graphics(f"""
        <alternatives>
          <graphic mime-subtype="{subtype}" xlink:href="master.bin"/>
          <graphic mime-subtype="jpeg" xlink:href="fig1.jpg"/>
        </alternatives>""")
            ).parse()

            assert article.figures[0].graphic_url == "fig1.jpg", subtype

    def test_the_archival_comparison_folds_case(self):
        article = JATSParser(
            _figure_with_graphics("""
        <alternatives>
          <graphic mime-subtype="TIFF" xlink:href="master.tif"/>
          <graphic mime-subtype="jpeg" xlink:href="fig1.jpg"/>
        </alternatives>""")
        ).parse()

        assert article.figures[0].graphic_url == "fig1.jpg"

    def test_the_first_wins_among_equals(self):
        """Accept a deposit only when it is *strictly* better."""
        article = JATSParser(
            _figure_with_graphics("""
        <graphic xlink:href="first.jpg"/>
        <graphic xlink:href="second.jpg"/>""")
        ).parse()

        assert article.figures[0].graphic_url == "first.jpg"

    def test_nothing_is_inferred_from_the_file_extension(self):
        """Every corpus thumbnail is a ``.gif``, and that proves nothing.

        A ``.gif`` is the thumbnail at PLOS and the only image a figure has
        elsewhere, so an extension rule passes the corpus and then discards
        that figure's only image.
        """
        article = JATSParser(
            _figure_with_graphics("""
        <graphic xlink:href="fig1.gif"/>
        <graphic xlink:href="fig1.jpg"/>""")
        ).parse()

        assert article.figures[0].graphic_url == "fig1.gif"

    def test_an_empty_href_never_displaces_a_real_one(self):
        article = JATSParser(
            _figure_with_graphics("""
        <graphic content-type="thumb" xlink:href="fig1-thumb.gif"/>
        <graphic xlink:href=""/>""")
        ).parse()

        assert article.figures[0].graphic_url == "fig1-thumb.gif"

    def test_a_single_graphic_still_works(self):
        article = JATSParser(
            _figure_with_graphics('        <graphic xlink:href="f1.jpg"/>')
        ).parse()

        assert article.figures[0].graphic_url == "f1.jpg"


class TestNestedTablesKeepTheirParent:
    """``current_table`` is the same single slot ``current_figure`` was.

    JATS lets a ``<table-wrap>`` open inside another's ``<table-wrap-foot>``,
    and the outer table was then lost outright — label, caption, rendered rows
    and all — exactly as the outer figure was in issue #115. Unmeasured, unlike
    the figure nesting, re-measured at 0.7% of a general draw and concentrated
    in eLife, but structural:
    every flag cleared on an end tag is a latent defect where the element can
    contain another of its own kind.
    """

    NESTED_TABLES = _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="T1">
        <label>Table 1.</label>
        <caption><p>The outer table.</p></caption>
        <table><tbody><tr><td>outer cell</td></tr></tbody></table>
        <table-wrap-foot><fn><p>
          <table-wrap id="T2">
            <label>Table S1.</label>
            <caption><p>The inner table.</p></caption>
            <table><tbody><tr><td>inner cell</td></tr></tbody></table>
          </table-wrap>
        </p></fn></table-wrap-foot>
      </table-wrap>
    </sec>""")

    def test_the_outer_table_is_not_dropped(self):
        article = JATSParser(self.NESTED_TABLES).parse()

        assert [t.id for t in article.tables] == ["T1", "T2"]

    def test_each_table_keeps_its_own_label(self):
        article = JATSParser(self.NESTED_TABLES).parse()

        assert [t.label for t in article.tables] == ["Table 1.", "Table S1."]

    def test_each_table_keeps_its_own_caption(self):
        article = JATSParser(self.NESTED_TABLES).parse()

        assert [t.caption for t in article.tables] == ["The outer table.", "The inner table."]

    def test_each_tables_rows_reach_its_own_rendering(self):
        article = JATSParser(self.NESTED_TABLES).parse()

        assert "outer cell" in article.tables[0].html_content
        assert "outer cell" not in article.tables[1].html_content
        assert "inner cell" in article.tables[1].html_content
        assert "inner cell" not in article.tables[0].html_content

    def test_the_outer_tables_internals_do_not_leak_into_the_section(self):
        """The inner close cleared ``in_table_wrap`` while the outer was open.

        Carries prose after the outer ``</table-wrap>`` for the reason the
        figure counterpart does: it is what pins ``in_table_wrap`` going off,
        and without it deriving the flag from ``table_slots`` survives.
        """
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <p>Section prose.</p>
      <table-wrap id="T1">
        <label>Table 1.</label>
        <table-wrap-foot><fn><p>
          <table-wrap id="T2"><label>Table S1.</label></table-wrap>
        </p></fn></table-wrap-foot>
        <p>Outer table internals after the nested one.</p>
      </table-wrap>
      <p>Section prose after the table.</p>
    </sec>
    <sec><title>Discussion</title><p>Prose in the next section.</p></sec>""")
        ).parse()

        assert [(s.title, tuple(s.paragraphs)) for s in article.body_sections] == [
            ("Results", ("Section prose.", "Section prose after the table.")),
            ("Discussion", ("Prose in the next section.",)),
        ]

    def test_an_unnested_table_still_works(self):
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="t1">
        <label>Table 1.</label>
        <caption><p>Only table.</p></caption>
        <table><tbody><tr><td>a cell</td></tr></tbody></table>
      </table-wrap>
    </sec>""")
        ).parse()

        assert [(t.id, t.label, t.caption) for t in article.tables] == [
            ("t1", "Table 1.", "Only table.")
        ]
        assert "a cell" in article.tables[0].html_content


class TestAnInnerExhibitOwnsItsOwnContent:
    """Exhibits nest both ways round, so "figure first" is not "innermost".

    ``<label>`` and caption text were routed by asking whether a figure was
    open *anywhere above* before considering the table, which hands an inner
    table's own content to the figure enclosing it. Found while pinning #116's
    "compare against the exhibit's depth, not against zero" rule, and the same
    defect one level up: routing on an ambient flag rather than on the
    enclosing element.
    """

    TABLE_INSIDE_A_FIGURES_FOOTNOTE = _article_with_body("""
    <sec><title>Results</title>
      <fig id="f1">
        <label>Figure 1.</label>
        <caption><p>The figure's caption.</p></caption>
        <fn><label>*</label>
          <p><table-wrap id="Tfn">
            <label>Table S1.</label>
            <caption><p>The table's caption.</p></caption>
            <table><tbody><tr><td>1</td></tr></tbody></table>
          </table-wrap></p>
        </fn>
      </fig>
    </sec>""")

    def test_the_inner_tables_caption_does_not_go_to_the_enclosing_figure(self):
        article = JATSParser(self.TABLE_INSIDE_A_FIGURES_FOOTNOTE).parse()

        assert [f.caption for f in article.figures] == ["The figure's caption."]
        assert [t.caption for t in article.tables] == ["The table's caption."]

    def test_the_inner_tables_label_does_not_go_to_the_enclosing_figure(self):
        article = JATSParser(self.TABLE_INSIDE_A_FIGURES_FOOTNOTE).parse()

        assert [f.label for f in article.figures] == ["Figure 1."]
        assert [t.label for t in article.tables] == ["Table S1."]

    def test_a_figure_inside_a_tables_footnote_owns_its_caption_too(self):
        """The mirror image, so neither kind is merely winning by test order."""
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="T1">
        <label>Table 1.</label>
        <caption><p>The table's caption.</p></caption>
        <table><tbody><tr><td>1</td></tr></tbody></table>
        <table-wrap-foot><fn><p>
          <fig id="ffn">
            <label>Figure S1.</label>
            <caption><p>The figure's caption.</p></caption>
          </fig>
        </p></fn></table-wrap-foot>
      </table-wrap>
    </sec>""")
        ).parse()

        assert [(t.label, t.caption) for t in article.tables] == [
            ("Table 1.", "The table's caption.")
        ]
        assert [(f.label, f.caption) for f in article.figures] == [
            ("Figure S1.", "The figure's caption.")
        ]


class TestAnUndeclaredArchivalMasterDoesNotWin:
    """``mime-subtype`` is optional, and an undeclared TIFF used to rank FULL.

    #117 demotes an archival master so it cannot beat the web image beside it,
    but it read only the declared ``mime-subtype``. An ``<alternatives>`` block
    need not declare one, and an undeclared TIFF deposited *first* then ranked
    ``FULL`` and — under the strictly-better rule that makes the first deposit
    win among equals — beat the JPEG that followed it, permanently. The
    pre-#117 "keep the last" resolved that case correctly, so it was a
    regression rather than a residual.

    An extension is read *here* and not for thumbnails because the costs are
    not symmetric: a first deposit is accepted whatever its rank, so demoting
    an archival master can only break a tie against a real web image, while a
    ``.gif`` rule would discard the only image a figure has.
    """

    def test_an_undeclared_archival_master_deposited_first_does_not_win(self):
        article = JATSParser(
            _figure_with_graphics("""
        <alternatives>
          <graphic xlink:href="f9.tif"/>
          <graphic mimetype="image" mime-subtype="jpeg" xlink:href="f9.jpg"/>
        </alternatives>""")
        ).parse()

        assert article.figures[0].graphic_url == "f9.jpg"

    def test_an_undeclared_archival_master_deposited_last_does_not_win_either(self):
        """The other deposit order, for the reason the thumbnail pair gives.

        With the master first, "keep the last" would already resolve it, so
        that order alone cannot fail if the extension is never consulted.
        """
        article = JATSParser(
            _figure_with_graphics("""
        <alternatives>
          <graphic mimetype="image" mime-subtype="jpeg" xlink:href="f9.jpg"/>
          <graphic xlink:href="f9.tif"/>
        </alternatives>""")
        ).parse()

        assert article.figures[0].graphic_url == "f9.jpg"

    def test_a_lone_archival_master_is_still_the_figures_image(self):
        """Demoting must never cost a figure the only image it has.

        This is what makes reading the extension safe here and not for
        thumbnails: ``offer_graphic`` accepts a first deposit whatever its
        rank, so the demotion only ever breaks a tie.
        """
        article = JATSParser(
            _figure_with_graphics('        <graphic xlink:href="f9.tif"/>')
        ).parse()

        assert article.figures[0].graphic_url == "f9.tif"

    def test_every_archival_extension_loses_to_a_web_image(self):
        for extension in (".tif", ".tiff", ".eps", ".ps"):
            article = JATSParser(
                _figure_with_graphics(f"""
        <graphic xlink:href="master{extension}"/>
        <graphic xlink:href="web.jpg"/>""")
            ).parse()

            assert article.figures[0].graphic_url == "web.jpg", extension

    def test_an_extensionless_href_is_not_read_as_archival(self):
        """PMC deposits extensionless hrefs; none of them is a print master."""
        article = JATSParser(
            _figure_with_graphics("""
        <graphic xlink:href="pone.0012345.g001"/>
        <graphic content-type="thumb" xlink:href="pone.0012345.g001.gif"/>""")
        ).parse()

        assert article.figures[0].graphic_url == "pone.0012345.g001"

    def test_a_query_string_does_not_hide_the_extension(self):
        article = JATSParser(
            _figure_with_graphics("""
        <graphic xlink:href="f9.tif?download=1"/>
        <graphic xlink:href="f9.jpg"/>""")
        ).parse()

        assert article.figures[0].graphic_url == "f9.jpg"

    def test_an_archival_master_still_beats_a_thumbnail_deposited_first(self):
        """The rank order's remaining deposit order (``ARCHIVAL < THUMBNAIL``).

        The declared-mime-subtype pair covers thumbnail-then-master; this is
        master-then-thumbnail, where "keep the last" would answer differently.
        """
        article = JATSParser(
            _figure_with_graphics("""
        <graphic mimetype="image" mime-subtype="tiff" xlink:href="f1.tif"/>
        <graphic content-type="thumb" xlink:href="f1-thumb.gif"/>""")
        ).parse()

        assert article.figures[0].graphic_url == "f1-thumb.gif"


class TestAGraphicBelongsToItsOwnExhibit:
    """A ``<graphic>`` was routed to the innermost open *figure*, not its owner.

    #115's sibling: ``<label>`` and caption text were moved onto the exhibit
    stacks, but ``<graphic>`` kept asking ``current_figure``, which answers
    "the innermost figure open anywhere above". A ``<graphic>`` held by a
    nested ``<table-wrap>``, ``<fn>`` or ``<supplementary-material>`` was
    therefore offered to the figure enclosing it.

    #117 is what makes that permanent rather than transient: both deposits
    rank ``FULL``, and ``offer_graphic`` accepts only a strictly better one, so
    the foreign href arriving first now beats the figure's own for good.
    Pre-#117 "keep the last" overwrote it, so each of these is a regression the
    ranking introduced, not a pre-existing residual.

    Ownership is decided by the enclosing element, with ``<alternatives>``
    transparent — the same principle as ``<label>``'s parent test, and for the
    same reason: it needs no enumeration of the containers that may hold a
    ``<graphic>``.
    """

    def test_a_nested_tables_graphic_is_not_the_figures_image(self):
        """Both halves are asserted, and the table half only became meaningful
        with #127: before it there was no field to receive the href, so the
        deposit's arriving nowhere was indistinguishable from its arriving in
        the right place. Asserting the figure alone leaves `and not
        self.figure_stack` on the table branch alive — a mutant that silently
        drops the whole content of every table nested inside a figure.
        """
        article = JATSParser(
            _figure_with_graphics("""
        <table-wrap id="t1"><graphic xlink:href="tbl.jpg"/></table-wrap>
        <graphic xlink:href="real.jpg"/>""")
        ).parse()

        assert article.figures[0].graphic_url == "real.jpg"
        assert article.tables[0].graphic_url == "tbl.jpg"

    def test_a_footnotes_graphic_is_not_the_figures_image(self):
        article = JATSParser(
            _figure_with_graphics("""
        <fn><p><graphic xlink:href="icon.gif"/></p></fn>
        <graphic xlink:href="real.jpg"/>""")
        ).parse()

        assert article.figures[0].graphic_url == "real.jpg"

    def test_supplementary_materials_graphic_is_not_the_figures_image(self):
        """eLife deposits source data inside the figure it belongs to."""
        article = JATSParser(
            _figure_with_graphics("""
        <supplementary-material><graphic xlink:href="supp.jpg"/></supplementary-material>
        <graphic xlink:href="real.jpg"/>""")
        ).parse()

        assert article.figures[0].graphic_url == "real.jpg"

    def test_the_figures_own_graphic_wins_from_either_position(self):
        """With the foreign graphic last, plain first-wins already answers it.

        So the three tests above — which all deposit the foreign graphic first
        — are the ones that can fail. This is the mirror order, which must keep
        working and would pass even with ownership never consulted.
        """
        article = JATSParser(
            _figure_with_graphics("""
        <graphic xlink:href="real.jpg"/>
        <table-wrap id="t1"><graphic xlink:href="tbl.jpg"/></table-wrap>""")
        ).parse()

        assert article.figures[0].graphic_url == "real.jpg"

    def test_alternatives_is_transparent_for_ownership(self):
        """The one wrapper that does *not* take ownership.

        ``<alternatives>`` offers several encodings of a single image, so a
        ``<graphic>`` inside it is still the exhibit's own.
        """
        article = JATSParser(
            _figure_with_graphics("""
        <alternatives><graphic xlink:href="f1.jpg"/></alternatives>""")
        ).parse()

        assert article.figures[0].graphic_url == "f1.jpg"

    def test_a_graphic_outside_any_figure_is_ignored_without_raising(self):
        """Pins the guard, not just the routing.

        Dropping the ``is not None`` test raises ``AttributeError`` out of
        ``parse()`` for ordinary markup — a section-level
        ``<supplementary-material>`` carries a ``<graphic>`` and no figure is
        open — which the tier chain then swallows at DEBUG.
        """
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <supplementary-material><graphic xlink:href="supp.jpg"/></supplementary-material>
      <p>Section prose.</p>
    </sec>""")
        ).parse()

        assert article.figures == []
        assert [(s.title, tuple(s.paragraphs)) for s in article.body_sections] == [
            ("Results", ("Section prose.",))
        ]


def _table_containing(markup: str) -> bytes:
    return _article_with_body(f"""
    <sec><title>Results</title>
      <table-wrap id="t1"><label>Table 1.</label>
        <caption><p>Baseline characteristics.</p></caption>
{markup}
      </table-wrap>
    </sec>""")


class TestATableDepositedAsAnImageKeepsIt:
    """A ``<table-wrap>`` whose content is a ``<graphic>`` — issue #127.

    ``JATSTableInfo`` carried ``html_content`` and no graphic field, so a
    scanned or typographically complex table lost its only content: the parser
    returned the id, the label and the caption over nothing, which is
    indistinguishable from an empty ``<table-wrap>``. PR #126 fixed the half
    that was *loud* in the wrong place — the image was being donated to an
    enclosing ``<fig>`` — and left the drop, naming it at DEBUG.

    The deposit is ranked exactly as a figure's is (#117): a scanned table may
    be deposited beside a thumbnail too, and the rule lives in one place rather
    than being re-derived here.
    """

    def test_a_table_deposited_as_an_image_keeps_its_href(self):
        article = JATSParser(
            _table_containing('        <graphic xlink:href="scanned-table.png"/>')
        ).parse()

        assert [(t.id, t.label, t.graphic_url) for t in article.tables] == [
            ("t1", "Table 1.", "scanned-table.png")
        ]

    def test_a_thumbnail_deposited_beside_it_does_not_win(self):
        article = JATSParser(
            _table_containing("""
        <graphic xlink:href="scan.jpg"/>
        <graphic content-type="thumbnail" xlink:href="scan-thumb.gif"/>""")
        ).parse()

        assert article.tables[0].graphic_url == "scan.jpg"

    def test_a_thumbnail_deposited_first_does_not_win_either(self):
        """The half plain first-wins cannot answer, and the ranking can."""
        article = JATSParser(
            _table_containing("""
        <graphic content-type="thumbnail" xlink:href="scan-thumb.gif"/>
        <graphic xlink:href="scan.jpg"/>""")
        ).parse()

        assert article.tables[0].graphic_url == "scan.jpg"

    def test_supplementary_materials_graphic_is_not_the_tables_image(self):
        """The case ownership actually decides, and the one that can fail.

        ``<supplementary-material>`` sits in ``<table-wrap>``'s content flow, so
        it may be deposited *before* the table's own image — and then plain
        first-wins keeps the wrong href, since both rank ``FULL`` and
        ``offer_graphic`` accepts only a strictly better deposit. Mutation
        testing found this: with the owner test dropped, the whole class passed
        because every foreign deposit here was written second.
        """
        article = JATSParser(
            _table_containing("""
        <supplementary-material><graphic xlink:href="supp.jpg"/></supplementary-material>
        <graphic xlink:href="scan.jpg"/>""")
        ).parse()

        assert article.tables[0].graphic_url == "scan.jpg"

    def test_a_footnotes_graphic_is_not_the_tables_image(self):
        """The mirror order, which must keep working.

        JATS puts ``<table-wrap-foot>`` after the table's content, so a
        footnote's image can only ever be deposited second and first-wins
        already answers it. Kept as the control: it would pass with ownership
        never consulted.
        """
        article = JATSParser(
            _table_containing("""
        <graphic xlink:href="scan.jpg"/>
        <table-wrap-foot><fn><p><graphic xlink:href="icon.gif"/></p></fn></table-wrap-foot>""")
        ).parse()

        assert article.tables[0].graphic_url == "scan.jpg"

    def test_a_nested_figures_graphic_is_not_the_tables_image(self):
        """The mirror of ``TestAGraphicBelongsToItsOwnExhibit``'s first case.

        Both exhibits now hold a graphic, so donating one to the other is a
        live hazard in *both* directions rather than one.
        """
        article = JATSParser(
            _table_containing("""
        <fig id="f1"><graphic xlink:href="inset.jpg"/></fig>
        <graphic xlink:href="scan.jpg"/>""")
        ).parse()

        assert article.tables[0].graphic_url == "scan.jpg"
        assert article.figures[0].graphic_url == "inset.jpg"

    def test_a_nested_tables_graphic_is_not_the_outer_tables_image(self):
        """#115's defect shape, in the direction #127 opened.

        JATS lets a ``<table-wrap>`` open inside another's
        ``<table-wrap-foot>``, and until #127 the inner one had no graphic
        field, so this direction could not be got wrong. It can now: routing
        the deposit to ``table_stack[0]`` rather than the innermost open table
        donates the supplement's image to the table enclosing it, and #117's
        ranking then makes that permanent — both rank ``FULL``, so whichever
        arrives first wins for good.
        """
        article = JATSParser(
            _table_containing("""
        <graphic xlink:href="outer.png"/>
        <table-wrap-foot><table-wrap id="inner">
          <graphic xlink:href="inner.png"/>
        </table-wrap></table-wrap-foot>""")
        ).parse()

        assert [(t.id, t.graphic_url) for t in article.tables] == [
            ("t1", "outer.png"),
            ("inner", "inner.png"),
        ]

    def test_the_outer_tables_graphic_wins_from_either_position(self):
        """The mirror order, and the one that can actually fail.

        With the outer table's own deposit first, plain "whoever arrives
        first" already answers it. Deposited *after* the nested table's, only
        routing by the innermost open exhibit keeps them apart.
        """
        article = JATSParser(
            _table_containing("""
        <table-wrap-foot><table-wrap id="inner">
          <graphic xlink:href="inner.png"/>
        </table-wrap></table-wrap-foot>
        <graphic xlink:href="outer.png"/>""")
        ).parse()

        assert [(t.id, t.graphic_url) for t in article.tables] == [
            ("t1", "outer.png"),
            ("inner", "inner.png"),
        ]

    def test_an_inline_image_in_a_cell_is_not_the_tables_deposit(self):
        """The one instance the 276-article draw found of a non-exhibit owner.

        It was recorded as resolving the same either way, which was true only
        while ``JATSTableInfo`` had nowhere to put an href. Now it would land
        in ``graphic_url`` as a cell decoration masquerading as the table's
        own rendition, so the case has to be pinned rather than noted.
        """
        article = JATSParser(
            _table_containing("""
        <table><tbody><tr><td><graphic xlink:href="tick.gif"/></td></tr></tbody></table>""")
        ).parse()

        assert article.tables[0].graphic_url is None

    def test_a_wrapped_href_does_not_displace_the_real_deposit(self):
        """XML normalises a pretty-printed attribute; it does not collapse it.

        So a href wrapped across lines arrives padded with spaces, which is
        truthy — it would pass the emptiness guard, take the ranking slot, and
        block the real deposit behind it. Neither committed corpus carries an
        instance; this pins a guard whose population measures empty.
        """
        article, html = JATSParser(
            _table_containing("""
        <graphic xlink:href="
            "/>
        <graphic xlink:href="scan.png"/>""")
        ).parse_with_html()

        assert article.tables[0].graphic_url == "scan.png"
        assert "bin/scan.png" in html

    def test_a_table_carrying_no_graphic_reports_none(self):
        """The negative control: the field is not filled from somewhere else."""
        article = JATSParser(
            _table_containing("""
        <table><tbody><tr><td>1</td></tr></tbody></table>""")
        ).parse()

        assert article.tables[0].graphic_url is None


class TestRenderingATableDepositedAsAnImage:
    """``_build_html``'s table branch, which emitted nothing for a graphic.

    The image is rendered **only** where there is no ``<table>`` markup. A
    ``<table-wrap>`` may carry both, and where it does the markup is the better
    rendition — emitting both shows the same table twice. The model carries the
    href either way, because that is data and the choice of rendition is the
    renderer's.
    """

    def test_the_image_is_rendered_where_there_is_no_markup(self):
        html = JATSParser(
            _table_containing('        <graphic xlink:href="scanned-table.png"/>')
        ).to_html()

        assert "scanned-table.png" in html
        assert "<img" in html

    def test_the_href_is_resolved_against_the_articles_pmc_id(self):
        """A table's deposit is resolved exactly as a figure's is."""
        html = JATSParser(
            _table_containing('        <graphic xlink:href="scanned-table"/>')
        ).to_html()

        assert "https://europepmc.org/articles/PMC1234567/bin/scanned-table.jpg" in html

    def test_the_label_is_the_images_alt_text(self):
        html = JATSParser(
            _table_containing('        <graphic xlink:href="scanned-table.png"/>')
        ).to_html()

        assert 'alt="Table 1."' in html

    def test_markup_and_image_together_render_the_markup_alone(self):
        article, html = JATSParser(
            _table_containing("""
        <graphic xlink:href="scan.png"/>
        <table><tbody><tr><td>1</td></tr></tbody></table>""")
        ).parse_with_html()

        assert article.tables[0].graphic_url == "scan.png"
        assert "<table>" in html
        assert "scan.png" not in html


def _figure_containing(markup: str) -> bytes:
    return _article_with_body(f"""
    <sec><title>Results</title>
      <fig id="f1"><label>Figure 1.</label>
        <caption><p>Figure caption.</p></caption>
{markup}
      </fig>
    </sec>""")


class TestAnExhibitsLabelComesFromItsOwnElement:
    """A ``<label>`` belongs to the element enclosing it, and JATS spells it
    as a direct child — so the parent decides outright.

    #116 fixed one member of this family by counting footnote depth: a
    ``<table-wrap-foot><fn>``'s "a"/"b"/"*" marker was overwriting the table's
    number for 12.0% of 225 surveyed articles. But the depth needed an
    enumeration of every container whose ``<label>`` is not the exhibit's, and
    that enumeration cannot be completed by inspection — ``<fn-group>``
    directly inside a ``<fig>``, ``<disp-formula>``, ``<media>`` and eLife's
    ``<supplementary-material>`` were all still overwriting it, each with a
    different plausible-looking wrong answer.

    Asking the parent needs no enumeration at all. It is also exact where a
    depth was merely close: an exhibit opened *inside* a footnote keeps its own
    label, because its ``<label>``'s parent is the exhibit either way.
    """

    def test_a_footnote_groups_label_inside_a_figure_is_not_the_figures_number(self):
        """No ``<table-wrap-foot>`` to wrap it, so the depth rule never fired."""
        article = JATSParser(
            _figure_containing("""
        <fn-group><label>Notes</label><fn><label>a</label><p>A note.</p></fn></fn-group>""")
        ).parse()

        assert article.figures[0].label == "Figure 1."

    def test_a_display_formulas_label_is_not_the_figures_number(self):
        article = JATSParser(
            _figure_containing("""
        <disp-formula><label>(1)</label></disp-formula>""")
        ).parse()

        assert article.figures[0].label == "Figure 1."

    def test_supplementary_materials_label_is_not_the_figures_number(self):
        """eLife's source-data convention, in the corpus that motivated #115."""
        article = JATSParser(
            _figure_containing("""
        <supplementary-material>
          <label>Figure 1-source data 1</label>
        </supplementary-material>""")
        ).parse()

        assert article.figures[0].label == "Figure 1."

    def test_a_medias_label_is_not_the_figures_number(self):
        article = JATSParser(
            _figure_containing("""
        <media><label>Video 1</label></media>""")
        ).parse()

        assert article.figures[0].label == "Figure 1."

    def test_a_labels_own_exhibit_still_wins_when_it_opens_inside_a_footnote(self):
        """The case a "am I in a footnote?" test eats — #116 one level down.

        Pinned here as well as under the depth rule it replaced, because the
        parent test is what now delivers it.
        """
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="t1"><label>Table 1.</label>
        <table-wrap-foot><fn><label>a</label>
          <p><fig id="f1"><label>Figure 1.</label></fig></p>
        </fn></table-wrap-foot>
      </table-wrap>
    </sec>""")
        ).parse()

        assert [(t.id, t.label) for t in article.tables] == [("t1", "Table 1.")]
        assert [(f.id, f.label) for f in article.figures] == [("f1", "Figure 1.")]


class TestFurtherExhibitNestingShapes:
    """Combinations the stacks must already handle, pinned so they stay so."""

    def test_an_unbalanced_document_is_refused_outright(self):
        """The premise the two ``is not None`` slot filters rest on.

        Both are documented as unreachable because expat rejects an unbalanced
        document before ``parse()`` returns. Nothing asserted that, so a future
        lenient feed would turn two documented-unreachable filters into live
        hole-hiders in silence.
        """
        with pytest.raises(xml.sax.SAXParseException):
            JATSParser(
                _article_with_body('<sec><title>Results</title><fig id="f1">').rsplit(
                    b"</body>", 1
                )[0]
            ).parse()

    def test_three_deep_tables_each_keep_their_own_label(self):
        """The figures have this; the tables did not."""
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="A"><label>Table A.</label>
        <table-wrap-foot><fn><p>
          <table-wrap id="B"><label>Table B.</label>
            <table-wrap-foot><fn><p>
              <table-wrap id="C"><label>Table C.</label></table-wrap>
            </p></fn></table-wrap-foot>
          </table-wrap>
        </p></fn></table-wrap-foot>
      </table-wrap>
    </sec>""")
        ).parse()

        assert [(t.id, t.label) for t in article.tables] == [
            ("A", "Table A."),
            ("B", "Table B."),
            ("C", "Table C."),
        ]

    def test_a_figure_inside_a_table_inside_a_figure_keeps_all_three_apart(self):
        """Where ``open_seq``'s tie-breaking works hardest.

        Both stacks are non-empty at the innermost level, and each exhibit's
        own label, caption and graphic must reach it rather than the exhibit
        enclosing it.
        """
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <fig id="F1"><label>Figure 1.</label>
        <caption><p>Outer figure caption.</p></caption>
        <table-wrap id="T1"><label>Table 1.</label>
          <caption><p>Table caption.</p></caption>
          <table-wrap-foot><fn><p>
            <fig id="F2"><label>Figure 2.</label>
              <caption><p>Inner figure caption.</p></caption>
              <graphic xlink:href="f2.jpg"/>
            </fig>
          </p></fn></table-wrap-foot>
        </table-wrap>
        <graphic xlink:href="f1.jpg"/>
      </fig>
    </sec>""")
        ).parse()

        assert [(f.id, f.label, f.caption, f.graphic_url) for f in article.figures] == [
            ("F1", "Figure 1.", "Outer figure caption.", "f1.jpg"),
            ("F2", "Figure 2.", "Inner figure caption.", "f2.jpg"),
        ]
        assert [(t.id, t.label, t.caption) for t in article.tables] == [
            ("T1", "Table 1.", "Table caption.")
        ]

    def test_a_figure_directly_inside_a_table_wrap_foot_keeps_its_label(self):
        """Every other footnote fixture wraps in ``<fn>``."""
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="t1"><label>Table 1.</label>
        <table-wrap-foot>
          <fig id="f1"><label>Figure 1.</label></fig>
        </table-wrap-foot>
      </table-wrap>
    </sec>""")
        ).parse()

        assert [(t.id, t.label) for t in article.tables] == [("t1", "Table 1.")]
        assert [(f.id, f.label) for f in article.figures] == [("f1", "Figure 1.")]

    def test_both_nested_tables_are_present_regardless_of_order(self):
        """The presence claim, split from the ordering one.

        ``test_the_outer_table_is_not_dropped`` asserts an ordered list, so it
        dies to the ordering mutant too and cannot show which of the two
        claims failed.
        """
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="T1"><label>Table 1.</label>
        <table-wrap-foot><fn><p>
          <table-wrap id="T2"><label>Table S1.</label></table-wrap>
        </p></fn></table-wrap-foot>
      </table-wrap>
    </sec>""")
        ).parse()

        assert {t.id for t in article.tables} == {"T1", "T2"}

    def test_a_sibling_exhibit_after_a_nested_pair_is_listed_last(self):
        """A mis-indexed slot reservation the three-deep test cannot catch."""
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <fig id="f1"><label>Figure 1.</label>
        <fig id="f1s1"><label>Figure 1-supplement 1.</label></fig>
      </fig>
      <fig id="f2"><label>Figure 2.</label></fig>
    </sec>""")
        ).parse()

        assert [f.id for f in article.figures] == ["f1", "f1s1", "f2"]


class TestAGraphicReachesItsFigureThroughProseFlow:
    """``<p>`` contains an image without owning it.

    JATS admits ``<p>`` inside ``<fig>``, so a ``<graphic>`` wrapped in one is
    still the figure's. Reading the ``<p>`` as the owner costs the figure its
    image — which is what routing by owner does unless prose flow is
    transparent, and the ``current_figure`` routing it replaced got this case
    right.
    """

    def test_a_graphic_wrapped_in_a_paragraph_is_still_the_figures(self):
        article = JATSParser(
            _figure_with_graphics('        <p><graphic xlink:href="real.jpg"/></p>')
        ).parse()

        assert article.figures[0].graphic_url == "real.jpg"

    def test_a_paragraph_does_not_carry_a_graphic_out_of_a_footnote(self):
        """Transparency must not reach *through* an owner.

        ``<fn><p><graphic/></p></fn>`` still stops at the ``<fn>``: the walk
        skips the ``<p>`` and finds the footnote, not the figure above it.
        """
        article = JATSParser(
            _figure_with_graphics("""
        <fn><p><graphic xlink:href="icon.gif"/></p></fn>
        <graphic xlink:href="real.jpg"/>""")
        ).parse()

        assert article.figures[0].graphic_url == "real.jpg"

    def test_a_section_level_graphic_in_a_paragraph_belongs_to_no_figure(self):
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <p><graphic xlink:href="loose.jpg"/></p>
    </sec>""")
        ).parse()

        assert article.figures == []


class TestASectionTitleComesFromItsOwnElement:
    """A ``<title>`` names the element that *owns* it, not whichever ``<sec>``
    happens to be open above it.

    ``<sec>`` is far from the only JATS element carrying a ``<title>``:
    ``<fn-group>`` is modelled ``(label?, title?, (fn|p)+)``, and
    ``<ref-list>``, ``<glossary>``, ``<app>``, ``<boxed-text>`` and every
    ``<caption>`` carry one too. Routing on "is a section open?" alone let any
    of them rename the enclosing section — issues #125 and #130, the same
    defect the ``<label>`` parent test settled for exhibit numbers in #116.

    The usual position for a ``<ref-list>`` or an ``<app>`` is loose in
    ``<back>`` with no section open, which is why this stayed hidden until a
    publisher nested one. eLife nests two: PMC8754430's *Additional
    information* section holds a ``<fn-group>`` per contribution type, and the
    last one won.

    A swallowed title is not a blank, which is what makes it worth a test
    rather than a note — the section keeps a heading, and the heading is text
    that was never one.
    """

    ELIFE_BACK_MATTER = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Additional</article-title>
  </title-group></article-meta></front>
  <body><sec><title>Results</title><p>We measured the thing.</p></sec></body>
  <back>
    <sec sec-type="additional-information" id="s5"><title>Additional information</title>
      <fn-group content-type="competing-interest"><title>Competing interests</title>
        <fn fn-type="COI-statement"><p>The authors declare none.</p></fn></fn-group>
      <fn-group content-type="author-contribution"><title>Author contributions</title>
        <fn><p>AB, conceptualisation.</p></fn></fn-group>
    </sec>
  </back>
</article>"""

    BOXED_TEXT_IN_SECTION = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Boxed</article-title>
  </title-group></article-meta></front>
  <body>
    <sec><title>Results</title><p>Section prose.</p>
      <boxed-text id="b1"><caption><title>Box 1. Key points</title>
          <p>Box caption prose.</p></caption>
        <p>Box prose.</p></boxed-text>
    </sec>
  </body>
</article>"""

    REF_LIST_IN_SECTION = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Reffed</article-title>
  </title-group></article-meta></front>
  <body><sec><title>Results</title><p>We measured the thing.</p></sec></body>
  <back>
    <sec id="s6"><title>Additional information</title>
      <ref-list><title>References</title>
        <ref id="r1"><label>1</label><element-citation>
          <article-title>A cited paper</article-title></element-citation></ref>
      </ref-list>
    </sec>
  </back>
</article>"""

    def test_a_footnote_groups_title_does_not_rename_the_section(self):
        """PMC8754430's shape: the last <fn-group> won twice over."""
        article = JATSParser(self.ELIFE_BACK_MATTER).parse()

        assert [s.title for s in article.body_sections] == [
            "Results",
            "Additional information",
        ]

    def test_a_footnote_groups_prose_still_reaches_the_section(self):
        """Dropping the title must not drop the statement under it."""
        article = JATSParser(self.ELIFE_BACK_MATTER).parse()

        back = article.body_sections[-1]
        assert tuple(back.paragraphs) == (
            "The authors declare none.",
            "AB, conceptualisation.",
        )

    def test_a_boxed_texts_caption_title_does_not_rename_the_section(self):
        """<boxed-text> admits a <caption> at section level — issue #130."""
        article = JATSParser(self.BOXED_TEXT_IN_SECTION).parse()

        assert [s.title for s in article.body_sections] == ["Results"]

    def test_a_boxed_texts_prose_still_reaches_the_section(self):
        """Including its caption's own <p>, which has nowhere better to go."""
        article = JATSParser(self.BOXED_TEXT_IN_SECTION).parse()

        assert tuple(article.body_sections[0].paragraphs) == (
            "Section prose.",
            "Box caption prose.",
            "Box prose.",
        )

    def test_a_reference_lists_title_does_not_rename_the_section(self):
        article = JATSParser(self.REF_LIST_IN_SECTION).parse()

        assert [s.title for s in article.body_sections] == [
            "Results",
            "Additional information",
        ]

    def test_a_reference_list_nested_in_a_section_still_parses(self):
        article = JATSParser(self.REF_LIST_IN_SECTION).parse()

        assert [r.article_title for r in article.references] == ["A cited paper"]

    def test_a_sections_own_title_is_still_read(self):
        """The negative control: the rule must not cost a real section title."""
        article = JATSParser(self.ELIFE_BACK_MATTER).parse()

        assert article.body_sections[0].title == "Results"

    def test_a_nested_sections_title_is_still_read(self):
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Nested</article-title>
  </title-group></article-meta></front>
  <body>
    <sec><title>Methods</title><p>Outer prose.</p>
      <sec><title>Participants</title><p>Inner prose.</p></sec>
    </sec>
  </body>
</article>"""
        article = JATSParser(data).parse()

        assert article.body_sections[0].title == "Methods"
        assert [s.title for s in article.body_sections[0].subsections] == ["Participants"]

    def test_an_abstract_section_title_is_still_read(self):
        """<abstract> keeps its own accumulator, and <sec> inside it pushes no
        builder — so the abstract branch must stay ahead of the parent test."""
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <title-group><article-title>Structured</article-title></title-group>
    <abstract><sec><title>Background</title><p>Why.</p></sec>
      <sec><title>Methods</title><p>How.</p></sec></abstract>
  </article-meta></front>
</article>"""
        article = JATSParser(data).parse()

        assert [(s.title, s.content) for s in article.abstract_sections] == [
            ("Background", "Why."),
            ("Methods", "How."),
        ]

    def test_a_footnote_group_inside_a_table_was_already_covered(self):
        """The variant issue #125 predicted was separately reachable here.

        It was not. A ``<table-wrap-foot><fn-group><title>`` sits inside an
        open exhibit, and until this fix the ``<title>`` branch tested that
        ahead of the section branch, so it was dropped rather than promoted.
        Measured rather than assumed: this test passed before the fix, which
        is what makes it a control on it. The prediction came from the sibling
        Swift parser, whose guard was a footnote *depth* that back matter
        leaves at zero.

        It is no longer only a control — the ambient exhibit test is gone and
        the parent test now carries it — so it is one of the tests that dies
        if the routing is reverted.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Footed</article-title>
  </title-group></article-meta></front>
  <body>
    <sec><title>Results</title><p>Section prose.</p>
      <table-wrap id="t1"><label>Table 1</label>
        <caption><p>A caption for the table.</p></caption>
        <table><tbody><tr><td><p>Treated</p></td></tr></tbody></table>
        <table-wrap-foot><fn-group><title>Abbreviations</title>
          <fn><p>CI, confidence interval.</p></fn></fn-group></table-wrap-foot>
      </table-wrap>
    </sec>
  </body>
</article>"""
        article = JATSParser(data).parse()

        assert [s.title for s in article.body_sections] == ["Results"]
        assert article.tables[0].caption == "A caption for the table."


class TestACaptionBelongsToTheElementThatOpenedIt:
    """Caption prose goes to the ``<caption>``'s *owner*, not to the innermost
    open exhibit — issue #123.

    ``in_caption`` was a stored boolean, so the two halves failed together. A
    ``<caption>`` nested inside a figure's own — a ``<media>`` legend, say —
    was appended to the figure, *and* its close cleared the flag, so the
    figure's own caption tail after it was dropped. A depth counter fixes only
    the second half: the inner legend's owner is not an exhibit bmlib models,
    so with a depth it still lands on the enclosing figure.

    The half a depth cannot reach at all is the *sibling* case, which needs no
    nesting: JATS admits a ``<caption>`` on ``<boxed-text>``, ``<media>`` and
    ``<supplementary-material>``, any of which may sit inside a ``<fig>``
    beside the figure's own, and every word of it was being appended to the
    figure's legend. That is the same shape #116 settled for ``<label>``, and
    it is the case a stack alone gets wrong.

    **Both of #123's populations measure empty**, so these two fixtures are
    hand-built rather than drawn from a corpus: no ``<caption>`` nests inside
    another across the two committed draws (0 of 1,550 and 0 of 288), and none
    inside an exhibit is owned by anything but that exhibit. The
    ``<supplementary-material>`` fixture below is a shape JATS permits, not one
    a publisher was observed depositing — an earlier draft of this docstring
    attributed it to eLife, which deposits its figure supplements as nested
    ``<fig>``. What is measured is the premise the rule rests on, and it is
    full: every exhibit that carries a caption carries one directly.
    """

    NESTED_CAPTION = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Nested caption</article-title>
  </title-group></article-meta></front>
  <body>
    <sec><title>Results</title><p>Section prose.</p>
      <fig id="f1"><label>Figure 1</label>
        <caption><title>Study flow.</title>
          <p>Caption lead.</p>
          <p><media mimetype="video" xlink:href="v1.mp4">
            <caption><p>Video legend.</p></caption></media></p>
          <p>Caption tail.</p></caption>
        <graphic xlink:href="f1.jpg"/></fig>
      <p>Prose after the figure.</p>
    </sec>
  </body>
</article>"""

    SIBLING_SUPPLEMENT_CAPTION = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Supplemented</article-title>
  </title-group></article-meta></front>
  <body>
    <sec><title>Results</title><p>Section prose.</p>
      <fig id="f1"><label>Figure 1</label>
        <caption><title>Study flow.</title><p>Caption lead.</p></caption>
        <graphic xlink:href="f1.jpg"/>
        <supplementary-material id="sd1"><label>Figure 1\xe2\x80\x94source data 1.</label>
          <caption><title>Raw counts.</title>
            <p>Numbers behind panel A.</p></caption></supplementary-material>
      </fig>
      <p>Prose after the figure.</p>
    </sec>
  </body>
</article>"""

    def test_the_enclosing_caption_keeps_its_tail(self):
        """The inner </caption> used to clear the flag and drop everything
        after it — the truncation half of #123."""
        article = JATSParser(self.NESTED_CAPTION).parse()

        assert article.figures[0].caption == "Study flow. Caption lead. Caption tail."

    def test_a_nested_captions_legend_does_not_join_the_figure(self):
        """The absorption half. A depth counter keeps the tail and still files
        the <media> legend on the figure, so this is what needs the owner."""
        article = JATSParser(self.NESTED_CAPTION).parse()

        assert "Video legend" not in article.figures[0].caption

    def test_a_nested_captions_legend_does_not_become_section_prose_either(self):
        """It is furniture of an element bmlib does not model, so it is
        dropped — not promoted into the article's body."""
        article = JATSParser(self.NESTED_CAPTION).parse()

        assert tuple(article.body_sections[0].paragraphs) == (
            "Section prose.",
            "Prose after the figure.",
        )

    def test_a_sibling_supplements_caption_does_not_join_the_figure(self):
        """eLife's shape. No nesting at all, so a depth counter never fires."""
        article = JATSParser(self.SIBLING_SUPPLEMENT_CAPTION).parse()

        assert article.figures[0].caption == "Study flow. Caption lead."

    def test_prose_after_the_figure_is_still_section_prose(self):
        """Both fixtures carry prose past the </fig>, so the caption state is
        pinned going *off* as well as on — a stack that never popped would
        swallow it."""
        article = JATSParser(self.SIBLING_SUPPLEMENT_CAPTION).parse()

        assert tuple(article.body_sections[0].paragraphs) == (
            "Section prose.",
            "Prose after the figure.",
        )

    def test_two_figures_do_not_share_a_caption(self):
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Two</article-title>
  </title-group></article-meta></front>
  <body>
    <sec><title>Results</title>
      <fig id="f1"><label>Figure 1</label>
        <caption><p>The first caption.</p></caption></fig>
      <fig id="f2"><label>Figure 2</label>
        <caption><p>The second caption.</p></caption></fig>
    </sec>
  </body>
</article>"""
        article = JATSParser(data).parse()

        assert [f.caption for f in article.figures] == [
            "The first caption.",
            "The second caption.",
        ]

    def test_an_inner_table_caption_stays_on_the_inner_table(self):
        """The rule must keep what `_innermost_exhibit` already delivered:
        exhibits nest both ways round, and a <table-wrap> inside a figure's
        footnote owns its own legend."""
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Both ways</article-title>
  </title-group></article-meta></front>
  <body>
    <sec><title>Results</title>
      <fig id="f1"><label>Figure 1</label>
        <caption><p>The figure caption.</p></caption>
        <table-wrap id="t1"><label>Table 1</label>
          <caption><p>The table caption.</p></caption>
          <table><tbody><tr><td><p>x</p></td></tr></tbody></table></table-wrap>
      </fig>
    </sec>
  </body>
</article>"""
        article = JATSParser(data).parse()

        assert article.figures[0].caption == "The figure caption."
        assert article.tables[0].caption == "The table caption."
