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

from pathlib import Path

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
    19.6% of 225 surveyed open-access articles. A single ``current_figure``
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
        prose — which is what a transparency scan reads.
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
    </sec>""")
        ).parse()

        assert [(s.title, tuple(s.paragraphs)) for s in article.body_sections] == [
            ("Results", ("Section prose.",))
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
    the figure nesting that issue measured at 19.6% of articles, but structural:
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
        """The inner close cleared ``in_table_wrap`` while the outer was open."""
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
    </sec>""")
        ).parse()

        assert [(s.title, tuple(s.paragraphs)) for s in article.body_sections] == [
            ("Results", ("Section prose.",))
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
