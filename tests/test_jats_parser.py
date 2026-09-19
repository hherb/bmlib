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

import ast
import logging
import re
import xml.sax
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest

from bmlib.fulltext import jats_parser as jats_parser_module
from bmlib.fulltext._parse_audit import unwind_diagnostics
from bmlib.fulltext.jats_parser import _TEXT_ACCUMULATING, JATSParser, _JATSHandler

FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class _ParserLog:
    """The parser's log records for one test, and whether ERROR was expected."""

    def __init__(self) -> None:
        self.records: list[logging.LogRecord] = []
        self.errors_expected = False

    def expect_errors(self) -> None:
        """Opt this test out of the ERROR guard below.

        Called by the handful of tests that provoke the end-of-parse audit on
        purpose. Everything else stays under the guard.
        """
        self.errors_expected = True

    def messages(self, level: int = logging.DEBUG) -> list[str]:
        """The rendered messages at or above ``level``, in emission order."""
        return [r.getMessage() for r in self.records if r.levelno >= level]


class _ListHandler(logging.Handler):
    def __init__(self, sink: list[logging.LogRecord]) -> None:
        super().__init__(level=logging.DEBUG)
        self._sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        self._sink.append(record)


@pytest.fixture(autouse=True)
def parser_log():
    """Collect the parser's records, and fail any test that provokes an ERROR.

    Autouse, so **every fixture in this module is a false-positive check for
    the end-of-parse audit** (#134) without being written as one. That guard
    is the reason the fixture exists at all: no other test here looks at logs,
    so an audit predicate that fires on a well-formed document would ship
    green and turn the ERROR channel into noise from its first day — which is
    precisely the failure the audit is meant to end, one level up.

    The audit logs at ERROR because it fires only when *bmlib* is wrong; a
    well-formed document cannot reach it. So "this module emitted an ERROR"
    is a defect claim, and a test that means to make one says so with
    :meth:`_ParserLog.expect_errors`.
    """
    logger = logging.getLogger("bmlib.fulltext.jats_parser")
    collected = _ParserLog()
    handler = _ListHandler(collected.records)
    previous_level = logger.level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    try:
        yield collected
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)

    unexpected = [r.getMessage() for r in collected.records if r.levelno >= logging.ERROR]
    if unexpected and not collected.errors_expected:
        raise AssertionError(
            "the JATS parser logged an ERROR on a well-formed document, so the "
            "end-of-parse audit has a false positive:\n  " + "\n  ".join(unexpected)
        )


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


def _article_with_meta(meta: str, *, journal_meta: str = "", after_meta: str = "") -> bytes:
    """A minimal article whose ``<article-meta>`` carries ``meta`` verbatim.

    ``journal_meta`` is placed in a ``<journal-meta>`` ahead of it, and
    ``after_meta`` in ``<front>`` after it.
    """
    return f"""<?xml version="1.0"?>
<article>
  <front>
    <journal-meta>{journal_meta}</journal-meta>
    <article-meta>
{meta}
    </article-meta>
{after_meta}
  </front>
  <body><sec><title>Results</title><p>Body prose.</p></sec></body>
</article>""".encode()


_OWN_META = """
    <article-id pub-id-type="doi">10.1000/own</article-id>
    <title-group><article-title>Retraction: X</article-title></title-group>
    <pub-date pub-type="epub"><year>2024</year></pub-date>
    <volume>12</volume><issue>3</issue><fpage>100</fpage><lpage>101</lpage>"""

# Every field the article-metadata arms read, as another work would carry it.
# The Tag Library admits <elocation-id> in every container listed below, so
# each carries one (issue #265).
_ANOTHER_WORKS_FIELDS = (
    "<article-title>Other</article-title><year>1999</year>"
    "<volume>9</volume><issue>8</issue><fpage>7</fpage><lpage>77</lpage>"
    "<elocation-id>e777</elocation-id>"
)

# The containers JATS 1.3 admits in <article-meta> that hold those names and
# belong to another work, each as it is deposited there. A citation is not a
# child of <article-meta>, so it sits in abstract prose, which is where the
# archive's Wiley notices put theirs.
_OTHER_WORKS_IN_ARTICLE_META = [
    pytest.param(
        '<related-article related-article-type="corrected-article">',
        "</related-article>",
        id="related-article",
    ),
    pytest.param("<related-object>", "</related-object>", id="related-object"),
    pytest.param('<product product-type="book">', "</product>", id="product"),
    pytest.param(
        "<abstract><p>Cites <mixed-citation>",
        "</mixed-citation>.</p></abstract>",
        id="mixed-citation",
    ),
    pytest.param(
        "<abstract><p>Cites <element-citation>",
        "</element-citation>.</p></abstract>",
        id="element-citation",
    ),
]


class TestTheArticlesOwnMetadataIsReadWhereItIsDeposited:
    """A field is the article's own only at its own place in ``<front>``.

    The metadata arms were gated on *being somewhere inside* ``<article-meta>``
    (``in_front and in_article_meta``), so every element JATS nests there that
    carries the same child names wrote this article's fields: a
    ``<related-article>`` after ``<title-group>`` gave a correction or a
    commentary the corrected paper's title (issue #254), and a
    ``<mixed-citation>`` in a retraction notice's abstract gave it the
    retracted paper's title, volume, issue and a page range no document
    carries (issue #259). A **wrong value** each time, in the fields a
    downstream keys and matches on, and on exactly the notices a literature
    tool must not confuse with their subject.

    The rule is the owner test this module makes elsewhere (#116, #123, #125,
    #130), extended to the whole path: ``front > article-meta`` for the id,
    volume, issue and pages, ``> title-group`` for the title, ``> pub-date``
    for the year, and ``> journal-title-group`` under ``front > journal-meta``
    for the journal — each wrapper optional, since a bare child of the
    article's own container has no other owner. ``<article-id>`` and
    ``<journal-title>`` had no measured non-owner outside a nested article,
    and are held to the same rule so the family reads one rule (issue #152
    for the id).
    """

    def test_a_related_articles_title_is_not_this_articles(self):
        """Issue #254's own reproduction."""
        meta = (
            _OWN_META
            + """
    <related-article related-article-type="retracted-article">
      <article-title>Old paper</article-title>
    </related-article>"""
        )

        article, html = JATSParser(_article_with_meta(meta)).parse_with_html()

        assert article.title == "Retraction: X"
        assert "<h1>Retraction: X</h1>" in html

    def test_a_citation_in_abstract_prose_does_not_overwrite_the_articles_fields(self):
        """Issue #259's own reproduction, with a year the citation also carries.

        The rendered journal line is asserted too: it is the half of the fix
        that reaches the HTML ``FullTextService`` caches, beside the ``<h1>``.
        """
        meta = (
            _OWN_META
            + """
    <abstract><p>The article <mixed-citation><article-title>Old paper</article-title>
      <source>J</source> <year>2019</year> <volume>7</volume>(<issue>9</issue>):
      <fpage>5</fpage>-<lpage>8</lpage>
    </mixed-citation> has been retracted.</p></abstract>"""
        )

        article, html = JATSParser(
            _article_with_meta(meta, journal_meta="<journal-title>J</journal-title>")
        ).parse_with_html()

        assert (article.title, article.year, article.volume, article.issue, article.pages) == (
            "Retraction: X",
            "2024",
            "12",
            "3",
            "100-101",
        )
        assert '<p class="journal-info"><em>J</em> 12(3): 100-101 (2024)</p>' in html

    def test_a_citation_does_not_supply_pages_the_article_does_not_carry(self):
        """An article paginated by ``<elocation-id>`` has no ``pages`` to give.

        The ``<fpage>`` arm was first-writer under the ambient gate, so where
        the article carries none a citation's page range became the article's
        outright rather than being appended to one — the commoner of the two
        shapes in the archive artifact.
        """
        meta = """
    <title-group><article-title>Retraction: X</article-title></title-group>
    <volume>12</volume><elocation-id>e100</elocation-id>
    <abstract><p>Retracts <mixed-citation><volume>7</volume>:<fpage>5</fpage>-<lpage>8</lpage>
    </mixed-citation>.</p></abstract>"""

        article = JATSParser(_article_with_meta(meta)).parse()

        assert (article.volume, article.pages) == ("12", "")

    def test_a_related_articles_page_is_not_appended_to_this_articles(self):
        """The ``<lpage>`` arm appends, so a stray one welds a suffix on.

        An erratum's ``<related-article>`` carrying the corrected paper's
        range turned ``230-230`` into ``230-230-6`` (``PMC12005481``, whose
        related article runs 194-6). The related volume differs from the
        article's so a last-writer overwrite of it is visible too.
        """
        meta = """
    <title-group><article-title>Erratum: Vol. 74, No. 11</article-title></title-group>
    <volume>74</volume><issue>12</issue><fpage>230</fpage><lpage>230</lpage>
    <related-article related-article-type="corrected-article">
      <article-title>Notes from the field</article-title>
      <volume>73</volume><issue>11</issue><fpage>1</fpage><lpage>6</lpage>
    </related-article>"""

        article = JATSParser(_article_with_meta(meta)).parse()

        assert (article.title, article.volume, article.issue, article.pages) == (
            "Erratum: Vol. 74, No. 11",
            "74",
            "12",
            "230-230",
        )

    def test_a_related_articles_pages_do_not_stand_in_for_an_elocation_id(self):
        """The ``<fpage>`` arm, reached by a ``<related-article>``, with no page to overwrite.

        A correction paginated by ``<elocation-id>`` alone has no ``pages``;
        the related article's range must not become the correction's.
        """
        meta = """
    <title-group><article-title>Correction: X</article-title></title-group>
    <volume>9</volume><elocation-id>e1</elocation-id>
    <related-article related-article-type="corrected-article">
      <volume>8</volume><fpage>1</fpage><lpage>6</lpage>
    </related-article>"""

        article = JATSParser(_article_with_meta(meta)).parse()

        assert (article.volume, article.pages) == ("9", "")

    @pytest.mark.parametrize(
        ("pages_meta", "expected"),
        [
            # No <fpage> to append to: a bare "-101" is a corrupt value.
            ("<lpage>101</lpage>", ""),
            # An empty <lpage/> appends nothing: "100-" is one too.
            ("<fpage>100</fpage><lpage/>", "100"),
        ],
        ids=["lpage-without-fpage", "empty-lpage"],
    )
    def test_the_lpage_arm_appends_only_a_real_last_page_to_a_first(self, pages_meta, expected):
        meta = f"<title-group><article-title>An article</article-title></title-group>{pages_meta}"

        article = JATSParser(_article_with_meta(meta)).parse()

        assert article.pages == expected

    @pytest.mark.parametrize(
        "pages_meta",
        [
            "<fpage>100</fpage><lpage>101</lpage><fpage>200</fpage><lpage>201</lpage>",
            "<fpage>100</fpage><fpage>200</fpage><lpage>201</lpage>",
        ],
        ids=["two-ranges", "two-first-pages"],
    )
    def test_a_doubled_page_range_stores_a_range_the_document_states(self, pages_meta):
        """Invalid markup, measured nowhere, where the first-writer guard was worse.

        The ``<article-meta>`` model admits one ``<fpage>``, and no article in
        the four artifacts deposits two. The ``<fpage>`` arm kept an ``and not
        self.pages`` guard from the ambient gate, which stored ``100-101-201``
        and ``100-201`` here — ranges neither document states — once the
        owner path had taken over the only job it did. Last writer stores the
        last range deposited, as the volume and issue arms already do.
        """
        meta = f"<title-group><article-title>An article</article-title></title-group>{pages_meta}"

        article = JATSParser(_article_with_meta(meta)).parse()

        assert article.pages == "200-201"

    def test_a_reviewed_products_title_and_pages_are_not_this_articles(self):
        """A book review's ``<product>`` names the book, not the review."""
        meta = """
    <title-group><article-title>A review</article-title></title-group>
    <pub-date pub-type="ppub"><year>2024</year></pub-date>
    <product product-type="book">
      <source>The Book</source><article-title>A chapter</article-title>
      <year>2001</year><fpage>1</fpage><lpage>300</lpage>
    </product>"""

        article = JATSParser(_article_with_meta(meta)).parse()

        assert (article.title, article.year, article.pages) == ("A review", "2024", "")

    @pytest.mark.parametrize(("opening", "closing"), _OTHER_WORKS_IN_ARTICLE_META)
    def test_another_works_fields_do_not_overwrite_the_articles(self, opening, closing):
        """Every other work JATS 1.3 nests in ``<article-meta>``, after the article's own.

        The title, volume and issue arms are last writer, and the ``<lpage>``
        arm appends, so a value deposited *after* the article's own is what
        each of them would take. The shapes are every container the Tag
        Library admits there that holds these names — not only the three the
        issues reproduce: before ``<related-object>`` and ``<element-citation>``
        were here, the title's owner test replaced by an exclusion list of
        ``related-article``, ``product`` and ``mixed-citation`` passed the
        whole suite.

        The article is paginated by a page range, so for ``elocation_id`` this
        is the *fill-a-blank* direction; the test below is the overwrite one.
        """
        meta = f"{_OWN_META}\n    {opening}{_ANOTHER_WORKS_FIELDS}{closing}"

        article = JATSParser(_article_with_meta(meta)).parse()

        assert (
            article.title,
            article.year,
            article.volume,
            article.issue,
            article.pages,
            article.elocation_id,
        ) == ("Retraction: X", "2024", "12", "3", "100-101", "")

    @pytest.mark.parametrize(("opening", "closing"), _OTHER_WORKS_IN_ARTICLE_META)
    def test_another_works_fields_do_not_fill_what_the_article_left_blank(self, opening, closing):
        """The same containers, where the article leaves a field blank.

        The year arm is first writer, so it cannot be caught overwriting the
        article's own value; here the article carries no ``<pub-date>``, no
        issue and no ``<fpage>``, and each must stay blank. It *does* carry an
        ``<elocation-id>``, deposited ahead of the other work's, so for that
        field this is the overwrite direction (issue #265).
        """
        meta = f"""
    <title-group><article-title>An article</article-title></title-group>
    <volume>5</volume><elocation-id>e1</elocation-id>
    {opening}{_ANOTHER_WORKS_FIELDS}{closing}"""

        article = JATSParser(_article_with_meta(meta)).parse()

        assert (
            article.title,
            article.year,
            article.volume,
            article.issue,
            article.pages,
            article.elocation_id,
        ) == ("An article", "", "5", "", "", "e1")

    @pytest.mark.parametrize(
        ("opening", "closing"),
        [("<mixed-citation>", "</mixed-citation>"), ("<element-citation>", "</element-citation>")],
        ids=["mixed-citation", "element-citation"],
    )
    def test_a_citation_ahead_of_the_publication_date_does_not_decide_it(self, opening, closing):
        """A citation deposited *before* the article's own values, in valid order.

        ``<author-notes>`` precedes ``<pub-date>`` and ``<fpage>`` in the
        ``<article-meta>`` model, and its ``<fn><p>`` admits a citation, so
        this is the order in which the first-writer year arm meets another work
        first. Under the ambient gate this fixture parsed with the title
        ``Old paper``, the year ``2019`` and the pages ``5-8-101``.
        """
        meta = f"""
    <article-id pub-id-type="doi">10.1000/own</article-id>
    <title-group><article-title>Retraction: X</article-title></title-group>
    <author-notes><fn><p>Retracts {opening}<article-title>Old paper</article-title>
      <year>2019</year> <volume>7</volume>:<fpage>5</fpage>-<lpage>8</lpage>{closing}.</p></fn>
    </author-notes>
    <pub-date pub-type="epub"><year>2024</year></pub-date>
    <volume>12</volume><issue>3</issue><fpage>100</fpage><lpage>101</lpage>"""

        article = JATSParser(_article_with_meta(meta)).parse()

        assert (article.title, article.year, article.volume, article.pages) == (
            "Retraction: X",
            "2024",
            "12",
            "100-101",
        )

    def test_the_year_is_the_publication_dates_whatever_the_order(self):
        """The year arm is first-writer, so order used to choose the date.

        JATS places ``<pub-date>`` ahead of ``<history>``, and every article
        in both artifacts deposits them that way, so the year moves in none of
        them. This is one of the fixtures that separate the owner test from the
        ambient gate it replaced, the one where the article *has* a
        publication date: under the gate, a received date deposited first was
        the publication year.
        """
        meta = """
    <title-group><article-title>An article</article-title></title-group>
    <history><date date-type="received"><year>2019</year></date></history>
    <pub-date pub-type="epub"><year>2021</year></pub-date>"""

        article = JATSParser(_article_with_meta(meta)).parse()

        assert article.year == "2021"

    def test_the_first_publication_date_deposited_decides_the_year(self):
        """First writer among the dates that *are* publication dates.

        Issue #261 decided (option 3, 2026-09-15) to keep document order and
        refuse only the types that name no publication — so this pins the half
        that survives, with two types the rule accepts. The other half, that a
        ``nihms-submitted`` or ``pmc-release`` date is refused, is
        :class:`TestANonPublicationDateIsNotTheArticlesYear`; this test's own
        fixture used to be that shape. Deleting ``and not self.year`` passed
        the whole suite until a test existed.
        """
        meta = """
    <title-group><article-title>An article</article-title></title-group>
    <pub-date pub-type="collection"><year>2025</year></pub-date>
    <pub-date pub-type="epub"><year>2023</year></pub-date>"""

        article = JATSParser(_article_with_meta(meta)).parse()

        assert article.year == "2025"

    @pytest.mark.parametrize(
        "dated",
        [
            '<history><date date-type="accepted"><year>2019</year></date></history>',
            "<pub-history><event><pub-date><year>2019</year></pub-date></event></pub-history>",
            "<related-article><year>2019</year></related-article>",
            '<product product-type="book"><year>2019</year></product>',
            "<abstract><p>Cites <mixed-citation><year>2019</year></mixed-citation>.</p></abstract>",
        ],
        ids=["history", "pub-history-event", "related-article", "product", "abstract-citation"],
    )
    def test_no_other_date_stands_in_for_a_missing_publication_date(self, dated):
        """A received date, or another work's, is not this article's year.

        The year arm is first writer, so with no ``<pub-date>`` of the
        article's own the first dated element anywhere in ``<article-meta>``
        used to become the year. A direction and not a population: every
        article in the four artifacts measured carries a ``<pub-date>`` year.
        """
        meta = f"<title-group><article-title>An article</article-title></title-group>{dated}"

        article = JATSParser(_article_with_meta(meta)).parse()

        assert article.year == ""

    def test_a_year_in_a_publication_dates_string_date_is_read(self):
        """``<pub-date>`` admits ``<string-date>``, which admits ``<year>`` (JATS 1.3)."""
        meta = """
    <title-group><article-title>An article</article-title></title-group>
    <pub-date><string-date><season>Spring</season> <year>2006</year></string-date></pub-date>"""

        article = JATSParser(_article_with_meta(meta)).parse()

        assert article.year == "2006"

    def test_a_volume_and_issue_in_a_volume_issue_group_are_read(self):
        """The article's own numbering, grouped where it appears in several issues.

        JATS 1.1+ admits ``<volume-issue-group>`` in ``<article-meta>``, and the
        ambient gate read it. No artifact measured deposits one, so this pins a
        direction; among several groups the last writer wins, as among bare
        ``<volume>`` elements, which is why the fixture deposits two.
        """
        meta = """
    <title-group><article-title>An article</article-title></title-group>
    <volume-issue-group content-type="print">
      <volume>XLI</volume><issue>1072</issue>
    </volume-issue-group>
    <volume-issue-group content-type="publication">
      <volume>XLII</volume><issue>1073</issue>
    </volume-issue-group>"""

        article = JATSParser(_article_with_meta(meta)).parse()

        assert (article.volume, article.issue) == ("XLII", "1073")

    @pytest.mark.parametrize(
        "journal_meta",
        [
            "<journal-title-group><journal-title>The Journal</journal-title></journal-title-group>",
            # NLM 2.x: no group. The majority spelling in the oldest PMC
            # back-files, so it is not a legacy corner.
            "<journal-title>The Journal</journal-title>",
        ],
        ids=["jats-group", "nlm-bare"],
    )
    def test_the_journal_title_is_read_in_either_spelling(self, journal_meta):
        meta = "<title-group><article-title>An article</article-title></title-group>"

        article = JATSParser(_article_with_meta(meta, journal_meta=journal_meta)).parse()

        assert article.journal == "The Journal"

    def test_a_title_and_year_deposited_without_their_wrapper_are_still_read(self):
        """Leniency where it costs no wrong value.

        A bare ``<article-title>`` or ``<year>`` directly in the article's own
        ``<article-meta>`` is invalid markup no artifact measured holds, but
        nothing else nested there could own it — every element belonging to
        another work sits one level deeper, inside that work. The shared
        ``sample_article.xml`` fixture deposits its title this way, so the
        whole retrieval chain's tests lean on it too.
        """
        meta = """
    <article-title>A bare title</article-title>
    <year>2022</year>
    <related-article>
      <article-title>Another work</article-title><year>1999</year>
    </related-article>"""

        article = JATSParser(_article_with_meta(meta)).parse()

        assert (article.title, article.year) == ("A bare title", "2022")

    def test_a_journal_title_outside_journal_meta_is_not_this_articles(self):
        """DTD-invalid, so this pins a direction and not a population.

        ``<journal-title>`` may be contained only in ``<journal-title-group>``,
        and ``<related-article>`` admits neither: no valid document carries a
        second journal title in ``<front>`` outside a nested article. Held to
        the owner path anyway, so the metadata arms read one rule.
        """
        journal_meta = "<journal-title>The Journal</journal-title>"
        meta = """
    <title-group><article-title>An article</article-title></title-group>
    <related-article><journal-title>Another Journal</journal-title></related-article>"""

        article = JATSParser(_article_with_meta(meta, journal_meta=journal_meta)).parse()

        assert article.journal == "The Journal"

    @pytest.mark.parametrize(
        ("id_type", "value"),
        [("doi", "10.1101/2023.01.01.000001"), ("pmid", "999"), ("pmcid", "PMC999")],
        ids=["doi", "pmid", "pmcid"],
    )
    def test_a_publication_history_events_identifier_is_not_this_articles(self, id_type, value):
        """Issue #152's wider half, reached by valid markup.

        JATS 1.3 admits ``<article-id>`` in a ``<pub-history><event>``, where it
        identifies another version of the work — a preprint's DOI. The
        ``in_front`` half of the old guard would have read it as the
        article's. The article carries only its own DOI, since a typed DOI and
        a PMID are last writer but a PMC ID is first: an event's identifier
        deposited after the article's own PMC ID could not show the leak.
        """
        meta = (
            _OWN_META
            + f"""
    <pub-history><event event-type="preprint">
      <article-id pub-id-type="{id_type}">{value}</article-id>
    </event></pub-history>"""
        )

        article = JATSParser(_article_with_meta(meta)).parse()

        assert (article.doi, article.pmid, article.pmc_id) == ("10.1000/own", "", "")

    def test_an_identifier_elsewhere_in_front_is_not_this_articles(self):
        """Issue #152's wider half: ``in_front`` admitted any ``<article-id>``.

        DTD-invalid — ``<notes>`` admits no ``<article-id>`` — and measured at
        0 in all four artifacts, so this pins the direction the rule takes.
        """
        after_meta = (
            '<notes><p><article-id pub-id-type="doi">10.1000/other</article-id></p></notes>'
        )

        article = JATSParser(_article_with_meta(_OWN_META, after_meta=after_meta)).parse()

        assert article.doi == "10.1000/own"

    def test_metadata_outside_front_supplies_nothing(self):
        """Issue #152's other half: the parent test admitted a stray ``<article-meta>``.

        The ambient gate already refused the title, volume, issue, pages,
        year and journal there; only ``<article-id>`` was disjoined with a
        parent test that admitted it. Invalid markup, measured at 0, and one
        rule for all of them now — which is what `front` at the head of each
        owner path says, and a stray ``<journal-meta>`` is what pins it there
        for the journal.
        """
        data = f"""<?xml version="1.0"?>
<article>
  <front>
    <journal-meta><journal-title>The Journal</journal-title></journal-meta>
    <article-meta>{_OWN_META}</article-meta>
  </front>
  <body><sec><title>Results</title><p>Body prose.</p></sec></body>
  <journal-meta><journal-title>Stray Journal</journal-title></journal-meta>
  <article-meta>
    <article-id pub-id-type="doi">10.1000/stray</article-id>
    <title-group><article-title>Stray</article-title></title-group>
    <pub-date><year>1999</year></pub-date>
    <volume>9</volume><issue>9</issue><fpage>9</fpage><lpage>99</lpage>
    <elocation-id>e9</elocation-id>
  </article-meta>
</article>""".encode()

        article = JATSParser(data).parse()

        assert (
            article.doi,
            article.title,
            article.year,
            article.volume,
            article.issue,
            article.pages,
            article.journal,
            article.elocation_id,
        ) == ("10.1000/own", "Retraction: X", "2024", "12", "3", "100-101", "The Journal", "")

    def test_a_references_own_fields_still_reach_the_reference(self):
        """Negative control: the branch above each rewritten arm is untouched.

        Every metadata arm whose element a citation can carry tests
        ``in_ref_citation`` first, so a citation in the bibliography fills its
        ``JATSReferenceInfo`` and never the article.
        ``article_title`` and ``year`` were pinned elsewhere; the four below
        were not.
        """
        data = f"""<?xml version="1.0"?>
<article>
  <front><article-meta>{_OWN_META}</article-meta></front>
  <body><sec><title>Results</title><p>Body prose.</p></sec></body>
  <back><ref-list><ref id="r1"><mixed-citation><source>J</source>
    <volume>10</volume>(<issue>2</issue>):<fpage>5</fpage>-<lpage>8</lpage>
  </mixed-citation></ref></ref-list></back>
</article>""".encode()

        article = JATSParser(data).parse()

        reference = article.references[0]
        assert (reference.volume, reference.issue, reference.first_page, reference.last_page) == (
            "10",
            "2",
            "5",
            "8",
        )

    def test_a_citations_empty_repeat_still_blanks_the_references_field(self):
        """#272's guard is scoped to the article's arms, and that is deliberate.

        The article's ``<volume>``, ``<issue>`` and ``<fpage>`` refuse an empty
        repeat; a citation's do not, so a second empty element still blanks
        the reference's field. Narrowing the citation branch the same way
        survived the whole module, so the chosen scope was pinned by nothing
        (PR #274's review) — this pins it, whichever way a later decision goes.
        The shape is invalid markup measured nowhere, and a citation's fields
        are already first-wins **across** the parts of one ``<ref>`` (#149);
        what is unguarded is a repeat inside a single citation element.
        """
        data = f"""<?xml version="1.0"?>
<article>
  <front><article-meta>{_OWN_META}</article-meta></front>
  <body><sec><title>Results</title><p>Body prose.</p></sec></body>
  <back><ref-list><ref id="r1"><mixed-citation><source>J</source>
    <volume>10</volume><volume/>
  </mixed-citation></ref></ref-list></back>
</article>""".encode()

        article = JATSParser(data).parse()

        assert article.references[0].volume == ""
        assert (article.volume, article.issue, article.pages) == ("12", "3", "100-101")

    def test_a_stray_article_meta_does_not_fill_what_the_article_left_blank(self):
        """The first-writer year arm, which the fixture above cannot reach.

        That fixture deposits the article's own ``<pub-date>`` first, so a
        year leaking from a stray ``<article-meta>`` would find the field
        already set. Here the article carries no year, and no pages either.
        Of the fields asserted, its ``<elocation-id>`` is the one value it does
        carry, and the stray's must not overwrite it (issue #265).
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <title-group><article-title>An article</article-title></title-group>
    <elocation-id>e1</elocation-id>
  </article-meta></front>
  <body><sec><title>Results</title><p>Body prose.</p></sec></body>
  <article-meta><pub-date><year>1999</year></pub-date><fpage>9</fpage><lpage>99</lpage>
    <elocation-id>e9</elocation-id></article-meta>
</article>"""

        article = JATSParser(data).parse()

        assert (article.year, article.pages, article.elocation_id) == ("", "", "e1")

    def test_a_review_rounds_front_matter_leaves_the_articles_alone(self):
        """A ``<sub-article>`` with a full ``<front>`` matches the same owner path.

        The path is a suffix, so a review round's closes reach the metadata
        arms unless the nested-article suppression in ``endElement``, tested
        before any arm, stops them. The round's own text never arrives —
        ``characters()`` is suppressed too — so what leaks without the guard
        is an *empty* value, blanking the article's last-writer fields.
        ``TestSubArticlesAreNotTheArticle`` pins that for the title alone. The
        year arm is first writer and the ``<lpage>`` arm needs text, so
        neither can blank; the article carries no year of its own, and that
        stays blank either way.

        **Since #272 this document discriminates on ``journal`` alone**, and
        the fixture keeps the other fields for the sake of the assertion being
        whole rather than because they still have teeth: the ``<volume>``,
        ``<issue>`` and ``<fpage>`` arms now refuse an empty value in their own
        right, so removing the suppression leaves all three at the article's
        values. Measured both ways with the ``endElement`` guard disabled:
        ``main`` blanks volume, issue and pages, this branch does not
        (PR #274's review). A second protection arriving next door is not a
        reason to delete this one — the routing the guard stops is unchanged —
        but a docstring claiming teeth the document no longer has is how the
        next reader is misled about what a red line here would mean.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><journal-meta><journal-title>The Journal</journal-title></journal-meta><article-meta>
    <title-group><article-title>An article</article-title></title-group>
    <volume>5</volume><issue>2</issue><fpage>1</fpage><lpage>2</lpage>
  </article-meta></front>
  <body><sec><title>Results</title><p>Body prose.</p></sec></body>
  <sub-article article-type="reviewer-report">
    <front>
      <journal-meta><journal-title>Review Journal</journal-title></journal-meta>
      <article-meta>
        <pub-date><year>1999</year></pub-date>
        <volume>9</volume><issue>9</issue><fpage>9</fpage><lpage>99</lpage>
      </article-meta>
    </front>
    <body><p>Reviewer prose.</p></body>
  </sub-article>
</article>"""

        article = JATSParser(data).parse()

        assert (article.journal, article.year, article.volume, article.issue, article.pages) == (
            "The Journal",
            "",
            "5",
            "2",
            "1-2",
        )

    @pytest.mark.parametrize(
        ("opening", "closing"),
        [
            ("", ""),
            ("<pmc-articleset>", "</pmc-articleset>"),
            ("<articles>", "</articles>"),
        ],
        ids=["bare", "ncbi-efetch-wrapper", "europepmc-bundle-wrapper"],
    )
    def test_a_wrapper_around_the_article_changes_nothing(self, opening, closing):
        """The owner path is a suffix, never anchored at the root.

        NCBI's efetch — ``FullTextService``'s tier 1c — serves the article
        inside ``<pmc-articleset>``, and a Europe PMC bundle concatenates
        articles inside ``<articles>``. A root-anchored rewrite of
        ``_owned_by`` blanked every metadata field of a wrapped document and
        passed every other test, since none deposits a wrapper.
        """
        meta = _OWN_META + "<related-article><article-title>Old</article-title></related-article>"
        article_xml = _article_with_meta(
            meta, journal_meta="<journal-title>The Journal</journal-title>"
        ).decode()
        article_xml = article_xml.replace('<?xml version="1.0"?>', "")
        data = f'<?xml version="1.0"?>{opening}{article_xml}{closing}'.encode()

        article = JATSParser(data).parse()

        assert (
            article.doi,
            article.title,
            article.year,
            article.volume,
            article.issue,
            article.pages,
            article.journal,
        ) == ("10.1000/own", "Retraction: X", "2024", "12", "3", "100-101", "The Journal")


class TestANonPublicationDateIsNotTheArticlesYear:
    """``<pub-date>`` carries dates that are not publications, and one won.

    The year arm is first writer among the article's own ``<pub-date>``
    elements, so document order picked the year whatever the date's declared
    type — and PMC deposits two types that name no publication at all:
    ``nihms-submitted``, the day an author manuscript was submitted to NIH,
    and ``pmc-release``, the day PMC's embargo lifts. Both land in the field a
    downstream keys, sorts and formats citations from.

    **Issue #261, decided by the maintainer (option 3, 2026-09-15)**: keep
    first writer and refuse the types ending ``-submitted`` or ``-release``,
    which fixes the wrong value and leaves the epub-versus-issue ordering
    question open. Diffed against ``main`` over four named artifacts, the
    stored year moves in **183 of the 8,118** served articles of
    ``PMC10030002_PMC10040000.xml.gz``, **456 of the 97,909** archive articles
    of ``oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26.tar.gz`` and **0** of
    the 3,028 and 27,515 back-filled ones of ``…PMC000xxxxxx…`` and
    ``…PMC001xxxxxx…``; no other field of ``JATSArticle`` moves anywhere, the
    rendered HTML moves in exactly those articles, and the year moves to blank
    in **none** of them. A markup survey over the same four artifacts agrees
    with the routing diff to the article.

    The two refused values are the only ones matching the suffixes across all
    of that: the whole measured vocabulary is ``epub``, ``collection``,
    ``pmc-release``, ``ppub``, ``pub``, ``nihms-submitted``, ``epreprint``,
    ``ecorrected``, ``epub-ppub``, ``preprint`` and ``update``. So the rule is
    a *suffix* rather than those two names because a vocabulary this open will
    grow another (``@pub-type`` is CDATA, and the JATS 1.1+ ``@date-type``
    carries an overlapping set — it moves the electronic/print distinction
    into ``@publication-format``), and everything else is kept — an
    ``epreprint`` or ``update`` date is a publication of some kind, and which
    of several publication dates the year should be is the question this
    decision deliberately leaves open (issue #273).
    """

    @pytest.mark.parametrize(
        "refused",
        [
            '<pub-date pub-type="nihms-submitted"><year>2025</year></pub-date>',
            '<pub-date pub-type="pmc-release"><year>2025</year></pub-date>',
            # `@pub-type` is CDATA: the two measured values are instances of
            # the rule, not the whole of it.
            '<pub-date pub-type="author-submitted"><year>2025</year></pub-date>',
            '<pub-date pub-type="embargo-release"><year>2025</year></pub-date>',
            # Folded, as this module folds `pub-id-type` and `contrib-type`.
            # An unfolded comparison costs the article a correct year; no
            # casing of an accepted type is ever refused by folding.
            '<pub-date pub-type="NIHMS-Submitted"><year>2025</year></pub-date>',
            # The JATS 1.1+ spelling. 968 served and 22,021 archive
            # `<pub-date>` declare their type this way, and none of those
            # carries a refused value — so this pins a direction. Its values
            # overlap `@pub-type`'s without matching them (`pub` appears only
            # here, `epub` only there), which is why both are read.
            '<pub-date date-type="nihms-submitted" publication-format="electronic">'
            "<year>2025</year></pub-date>",
            # The precedence's other half: `@pub-type` is read first whichever
            # way the pair falls, so a refused value there is refused whatever
            # `@date-type` says. The accepting row one class down pins the
            # mirror, and without this one an edit reading whichever attribute
            # happens to be *accepted* survives (PR #274's review).
            '<pub-date pub-type="nihms-submitted" date-type="epub"><year>2025</year></pub-date>',
            # XML normalises a CDATA attribute's tabs and newlines to spaces
            # and trims nothing, so a padded value reaches the handler padded.
            # No artifact deposits one, so this pins a direction — but the
            # leniency cannot cost a correct year, no accepted type differing
            # from a refused one by whitespace alone.
            '<pub-date pub-type=" nihms-submitted "><year>2025</year></pub-date>',
            # The year may sit in a <string-date>, which the owner path admits.
            '<pub-date pub-type="pmc-release"><string-date><year>2025</year>'
            "</string-date></pub-date>",
        ],
        ids=[
            "nihms-submitted",
            "pmc-release",
            "another-submitted",
            "another-release",
            "case-folded",
            "date-type-spelling",
            "pub-type-beats-an-accepted-date-type",
            "whitespace-padded",
            "in-a-string-date",
        ],
    )
    def test_a_date_that_names_no_publication_does_not_decide_the_year(self, refused):
        """The reversal issue #261 asks for: the next date decides instead."""
        meta = f"""
    <title-group><article-title>An article</article-title></title-group>
    {refused}
    <pub-date pub-type="epub"><year>2023</year></pub-date>"""

        article = JATSParser(_article_with_meta(meta)).parse()

        assert article.year == "2023"

    @pytest.mark.parametrize(
        "declared",
        [
            'pub-type="epub"',
            'pub-type="ppub"',
            'pub-type="collection"',
            'pub-type="epreprint"',
            'pub-type="ecorrected"',
            'pub-type="epub-ppub"',
            'date-type="pub" publication-format="electronic"',
            'date-type="collection" publication-format="print"',
            'date-type="update" publication-format="electronic"',
            # The eleventh measured member, and the only one the class
            # docstring names that no other row here reaches: 13 archive
            # <pub-date> deposit it, all in this spelling (PR #274's review).
            'date-type="preprint" publication-format="electronic"',
            "",
            # `@pub-type` is read first. No <pub-date> in any artifact
            # declares both, so this pins a direction — and reversing the
            # precedence passed the whole module until this row existed.
            'pub-type="epub" date-type="nihms-submitted"',
            # The suffix is a *hyphenated* one, so a type merely containing
            # the word, or ending in it unqualified, is not refused. Neither
            # shape is in any artifact; they pin what the rule's own headline
            # claim ("a suffix, not those two names") rests on.
            'pub-type="pmc-release-date"',
            'pub-type="release"',
            'pub-type="resubmitted"',
        ],
        ids=[
            "epub",
            "ppub",
            "collection",
            "epreprint",
            "ecorrected",
            "epub-ppub",
            "date-type-pub",
            "date-type-collection",
            "date-type-update",
            "date-type-preprint",
            "untyped",
            "pub-type-beats-date-type",
            "suffix-not-contained",
            "unhyphenated-release",
            "unhyphenated-submitted",
        ],
    )
    def test_every_other_type_the_artifacts_deposit_still_decides_the_year(self, declared):
        """The whole measured vocabulary bar the two refused, plus an untyped date.

        A date declaring nothing is not refused: the article deposited it as
        its publication date and named no other kind. The refusal is narrow on
        purpose, so this is the half that says how narrow.
        """
        meta = f"""
    <title-group><article-title>An article</article-title></title-group>
    <pub-date {declared}><year>2025</year></pub-date>
    <pub-date pub-type="epub"><year>2023</year></pub-date>"""

        article = JATSParser(_article_with_meta(meta)).parse()

        assert article.year == "2025"

    def test_the_declared_type_does_not_outlive_its_own_publication_date(self):
        """The slot is cleared at ``</pub-date>``, so what follows is judged alone.

        ``<pub-date>`` admits only date parts (JATS 1.3), so it cannot nest
        and one slot serves — but a slot that is set and never cleared refuses
        every year after a refused date, which here would leave the article
        with no year at all.

        **The year that follows must not be in a ``<pub-date>`` of its own**,
        or the test is vacuous: ``startElement`` writes the slot
        unconditionally at every ``<pub-date>`` open, so an untyped second
        date resets it to ``None`` whether or not the close cleared it. Deleting
        the ``</pub-date>`` arm outright passed this test's earlier fixture
        (PR #274's review). A bare ``<year>`` in ``<article-meta>`` is the one
        shape the owner path admits with no ``<pub-date>`` open, so it is the
        only one that can see the clear.
        """
        meta = """
    <title-group><article-title>An article</article-title></title-group>
    <pub-date pub-type="pmc-release"><year>2025</year></pub-date>
    <year>2022</year>"""

        handler = JATSParser(_article_with_meta(meta))._run_parser()

        assert (handler.year, handler.non_publication_years_refused) == ("2022", 1)

    def test_a_second_publication_date_is_judged_on_its_own_declaration(self):
        """The companion shape: an untyped second ``<pub-date>`` is not refused.

        Vacuous for the clear at ``</pub-date>`` — the second date's *open*
        resets the slot — and kept because it is the ordinary document, where
        the test above is invalid markup admitted only by this module's
        leniency for a bare child of ``<article-meta>``.
        """
        meta = """
    <title-group><article-title>An article</article-title></title-group>
    <pub-date pub-type="pmc-release"><year>2025</year></pub-date>
    <pub-date><year>2023</year></pub-date>"""

        article = JATSParser(_article_with_meta(meta)).parse()

        assert article.year == "2023"

    def test_a_bare_year_in_article_meta_is_still_read(self):
        """No ``<pub-date>`` declared the type, so nothing refuses it.

        Invalid markup that no artifact deposits, but the shared
        ``sample_article.xml`` fixture leans on the same leniency for its
        title, so the refusal must not narrow it.
        """
        meta = """
    <title-group><article-title>An article</article-title></title-group>
    <year>2022</year>"""

        article = JATSParser(_article_with_meta(meta)).parse()

        assert article.year == "2022"

    def test_a_references_year_is_not_judged_by_the_articles_publication_date(self):
        """The reference arm runs ahead of the owner test and is untouched.

        The citation is deposited **inside** the refused ``<pub-date>``, which
        JATS does not admit and expat does not check — and that is the point:
        with the reference in its ordinary place the article's date has closed
        long before, so ``current_pub_date_type`` is ``None`` at the
        citation's ``</year>`` and gating the reference branch on the type
        survives the whole module. This is the only document in which the two
        can be told apart, so it pins a direction the valid shapes cannot.
        """
        meta = """
    <title-group><article-title>An article</article-title></title-group>
    <pub-date pub-type="pmc-release">
      <ref id="r1"><mixed-citation><source>J</source> <year>2019</year>.</mixed-citation></ref>
      <year>2025</year>
    </pub-date>"""

        article = JATSParser(_article_with_meta(meta)).parse()

        assert (article.references[0].year, article.year) == ("2019", "")

    @pytest.mark.parametrize(
        ("dated", "expected"),
        [
            ('<pub-date pub-type="nihms-submitted"><year>2025</year></pub-date>', 1),
            (
                '<pub-date pub-type="nihms-submitted"><year>2025</year></pub-date>'
                '<pub-date pub-type="pmc-release"><year>2024</year></pub-date>',
                2,
            ),
        ],
        ids=["one-refused-date", "two-refused-dates"],
    )
    def test_an_article_dated_only_by_a_refused_date_keeps_no_year(
        self, parser_log, dated, expected
    ):
        """The refusal's own cost, counted and reported once per article.

        Where every ``<pub-date>`` carrying a year names a non-publication
        date, the refusal leaves the article with no year where ``main``
        stored one — a loss this module chose, so it earns a line, the
        granularity and level ``rejected_spans`` settled for #129 and
        ``definition_terms_dropped`` for #228. Wholly prospective: **0 of the
        8,118 served, 0 of the 97,909 archive and 0 of the 3,028 and 27,515
        back-filled articles** lose their year, every one of them depositing
        another dated ``<pub-date>``.
        """
        meta = f"""
    <title-group><article-title>An article</article-title></title-group>
    {dated}"""

        handler = JATSParser(_article_with_meta(meta))._run_parser()

        assert handler.year == ""
        assert handler.non_publication_years_refused == expected
        warnings = parser_log.messages(logging.WARNING)
        assert any(
            f"{expected} <pub-date> year(s) name no publication date" in m for m in warnings
        ), warnings

    def test_a_refusal_that_costs_the_article_nothing_is_not_reported(self, parser_log):
        """The line is about the loss, not about the refusal.

        A refused date is the *first* dated one — which is what this counter
        counts, the arm being short-circuited once a year is stored — in
        **1,105 of the 8,118 served articles** (13.6%, about one in seven:
        1,047 from a ``pmc-release`` date and 58 from a ``nihms-submitted``
        one). A line there would be a line about no loss, which is why the
        shared counter #228's comment proposed was refused on measurement by
        #235, one counter over.
        """
        meta = """
    <title-group><article-title>An article</article-title></title-group>
    <pub-date pub-type="pmc-release"><year>2025</year></pub-date>
    <pub-date pub-type="epub"><year>2023</year></pub-date>"""

        handler = JATSParser(_article_with_meta(meta))._run_parser()

        assert (handler.year, handler.non_publication_years_refused) == ("2023", 1)
        assert parser_log.messages(logging.WARNING) == []

    @pytest.mark.parametrize(
        "deposited",
        [
            "<year/>",
            # The counter reads the *stripped* text, as the three arms one
            # class down do. Read raw, this row counts a year the document
            # never deposited and the WARNING above claims a loss that did not
            # happen — a line in the channel `_audit_parse`'s own rule reserves
            # for content that really is missing. The sibling class added its
            # three whitespace rows on exactly this ground and this arm was
            # left without one (PR #274's review).
            "<year>\n    </year>",
        ],
        ids=["self-closing", "whitespace-only"],
    )
    def test_an_empty_refused_date_is_not_counted(self, deposited):
        """An empty ``<year/>`` states no year, so refusing it costs nothing.

        ``<elocation-id>``'s own empty rule, one arm over: a counter that
        counts a value the document never deposited reports a loss that did
        not happen.
        """
        meta = f"""
    <title-group><article-title>An article</article-title></title-group>
    <pub-date pub-type="pmc-release">{deposited}</pub-date>
    <pub-date pub-type="epub"><year>2023</year></pub-date>"""

        handler = JATSParser(_article_with_meta(meta))._run_parser()

        assert (handler.year, handler.non_publication_years_refused) == ("2023", 0)

    def test_a_refusal_after_a_year_was_found_is_not_counted(self):
        """The counter's documented meaning: refusals made *before* one was found.

        The arm short-circuits on ``not self.year``, so a refused date
        deposited after an accepted one is never reached. Behaviourally
        equivalent today — the WARNING is gated on ``not handler.year``, so a
        wider counter would still report nothing — but the counter's comment
        states this meaning and both other counter assertions put the refused
        date first, so widening the arm survived the whole module (PR #274's
        review).
        """
        meta = """
    <title-group><article-title>An article</article-title></title-group>
    <pub-date pub-type="epub"><year>2023</year></pub-date>
    <pub-date pub-type="pmc-release"><year>2025</year></pub-date>"""

        handler = JATSParser(_article_with_meta(meta))._run_parser()

        assert (handler.year, handler.non_publication_years_refused) == ("2023", 0)


class TestAnEmptyRepeatedValueKeepsTheOneBeforeIt:
    """Issue #272: the article's last-writer arms wrote an empty element too.

    ``<fpage>``, ``<volume>`` and ``<issue>`` are last writer, and they wrote
    unconditionally — so an empty second element blanked a good value, the
    shape the nested-article suppression's own comment names as the defect it
    exists to stop. ``<lpage>`` has always refused an empty value and
    ``<elocation-id>`` was given the same guard when it was written (#265).

    ``<article-meta>`` admits one of each, so this is invalid markup: a
    routing tally measured **0 of the 8,118** served articles of
    ``PMC10030002_PMC10040000.xml.gz`` and **0 of the 97,909** archive ones of
    ``oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26.tar.gz``, and the diff
    against ``main`` moves ``volume``, ``issue`` and ``pages`` in 0 articles
    of all four artifacts. A direction rather than a population — and the same
    diff, run against the commit before PR #263, reports 2 ``volume`` and 2
    ``pages`` moves on the served artifact, so the instrument that measures
    those zeroes can see a move in exactly these fields.
    """

    @pytest.mark.parametrize(
        ("field", "deposited", "expected"),
        [
            ("volume", "<volume>12</volume><volume/>", "12"),
            ("issue", "<issue>3</issue><issue/>", "3"),
            ("pages", "<fpage>100</fpage><fpage/>", "100"),
            ("pages", "<fpage>100</fpage><lpage>101</lpage><fpage/>", "100-101"),
            # The empty repeat states no first page, so it opens no range and
            # closes none either: the <lpage> after it still completes the
            # range the *real* <fpage> opened, and `100-201` is the only range
            # this document states. The row below, where an <lpage> had already
            # closed that range, is what it must not be confused with — every
            # fixture put the <lpage> ahead of the empty <fpage/> or stopped at
            # it, so the flag's behaviour after one was unobserved (PR #274's
            # review).
            ("pages", "<fpage>100</fpage><fpage/><lpage>201</lpage>", "100-201"),
            ("elocation_id", "<elocation-id>e1</elocation-id><elocation-id/>", "e1"),
            # The guards read the *stripped* text, so a repeat carrying only
            # whitespace states no value either. Every fixture above uses the
            # self-closing spelling, and reading the raw text instead passed
            # the whole module until these rows existed.
            ("volume", "<volume>12</volume><volume>\n    </volume>", "12"),
            ("issue", "<issue>3</issue><issue>\n    </issue>", "3"),
            ("pages", "<fpage>100</fpage><fpage>\n    </fpage>", "100"),
        ],
        ids=[
            "volume",
            "issue",
            "fpage",
            "fpage-after-a-range",
            "fpage-before-a-last-page",
            "elocation-id",
            "whitespace-only-volume",
            "whitespace-only-issue",
            "whitespace-only-fpage",
        ],
    )
    def test_an_empty_second_element_does_not_blank_the_value(self, field, deposited, expected):
        """Each arm keeps what the article deposited."""
        meta = f"""
    <title-group><article-title>An article</article-title></title-group>
    <pub-date pub-type="epub"><year>2024</year></pub-date>
    {deposited}"""

        article = JATSParser(_article_with_meta(meta)).parse()

        assert getattr(article, field) == expected

    @pytest.mark.parametrize(
        "deposited",
        [
            # The shape PR #269's successor review found: refusing the empty
            # <fpage> keeps `pages` non-empty, which is the condition the
            # <lpage> append used to need — so the range this module calls one
            # no document states came back through #272's own guard.
            "<fpage>100</fpage><lpage>101</lpage><fpage/><lpage>201</lpage>",
            # The same value by the pre-existing route, a doubled <lpage>.
            "<fpage>100</fpage><lpage>101</lpage><lpage>201</lpage>",
        ],
        ids=["through-an-empty-fpage", "a-doubled-lpage"],
    )
    def test_a_second_last_page_does_not_extend_a_closed_range(self, deposited):
        """An ``<lpage>`` completes the range its own ``<fpage>`` opened.

        ``self.pages`` being non-empty says only that *some* page value is
        stored, so the arm appended to a range another ``<lpage>`` had already
        closed — ``100-101-201``, which `docs/DECISIONS.md` names as a range
        no document states. Both shapes are invalid markup (``<article-meta>``
        admits one ``<fpage>`` and one ``<lpage>``) measured 0 on all four
        artifacts, so this pins a direction; a corruption is worse than the
        blank the second shape's neighbour used to leave.
        """
        meta = f"""
    <title-group><article-title>An article</article-title></title-group>
    <pub-date pub-type="epub"><year>2024</year></pub-date>
    {deposited}"""

        article = JATSParser(_article_with_meta(meta)).parse()

        assert article.pages == "100-101"

    @pytest.mark.parametrize(
        ("deposited", "expected"),
        [
            ("<fpage>100</fpage><lpage>101</lpage><lpage>201</lpage>", 1),
            ("<fpage>100</fpage><lpage>101</lpage><fpage/><lpage>201</lpage>", 1),
            # No <fpage> at all, which `main` refused just as silently through
            # the `self.pages` guard this flag replaced.
            ("<lpage>101</lpage>", 1),
            # An empty <lpage/> deposits no page number, so it reads nothing
            # and counts nothing — every sibling counter's rule.
            ("<lpage/>", 0),
        ],
        ids=["a-doubled-lpage", "through-an-empty-fpage", "no-first-page", "an-empty-lpage"],
    )
    def test_a_last_page_that_completes_no_range_is_counted_and_reported(
        self, parser_log, deposited, expected
    ):
        """The refusal's own cost, counted and reported once per article.

        ``<lpage>`` is not in ``_INLINE_ELEMENTS``, so a refused value reaches
        no field, no buffer and — until this counter — no log line at any
        level, where the refused ``<elocation-id>`` part beside it is at least
        still in a ``<mixed-citation>``'s ``citation``. That is the drop this
        module's own rule calls one that earns a line rather than excusing
        one, and the manual's *"every drop is counted"* says so in as many
        words (PR #274's review). Wholly prospective: **0 of the 8,118 served
        articles of ``PMC10030002_PMC10040000.xml.gz`` and 0 of the 97,909
        archive ones of ``oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26``**.
        """
        meta = f"""
    <title-group><article-title>An article</article-title></title-group>
    <pub-date pub-type="epub"><year>2024</year></pub-date>
    {deposited}"""

        handler = JATSParser(_article_with_meta(meta))._run_parser()

        assert handler.last_pages_dropped == expected
        # Matched on the whole line including the count, and the empty row is
        # asserted against *any* such line rather than against one carrying
        # its own zero — a bare `"0 <lpage>"` test passes while a line with
        # the wrong count fires.
        lines = [m for m in parser_log.messages(logging.WARNING) if "completed no page range" in m]
        if expected:
            assert [m for m in lines if f"{expected} <lpage> value(s) completed no page range" in m]
        else:
            assert lines == []

    def test_a_foreign_first_page_does_not_open_the_articles_range(self):
        """The flag is set by the article's own ``<fpage>`` and no other.

        ``main``'s guard was ``self.pages``, which a ``<related-article>``'s
        ``<fpage>`` could not set either — so the owner test protected this
        incidentally and the new flag had to inherit it explicitly. Setting the
        flag from any ``<fpage>`` survived the whole module and stored
        ``-101``, the corrupt value
        ``test_the_lpage_arm_appends_only_a_real_last_page_to_a_first`` exists
        to prevent, reached by a route it cannot see (PR #274's review).
        """
        meta = """
    <title-group><article-title>An article</article-title></title-group>
    <related-article related-article-type="companion"><fpage>9</fpage></related-article>
    <lpage>101</lpage>"""

        handler = JATSParser(_article_with_meta(meta))._run_parser()

        assert (handler.pages, handler.last_pages_dropped) == ("", 1)

    def test_a_first_page_carrying_a_hyphen_still_opens_one_range(self):
        """The flag is state, not a shape test on ``self.pages``.

        Nothing separated it from *any* predicate derived from the stored
        value — ``self.pages and "-" not in self.pages`` passed both rows
        above (PR #274's review). A first page that itself carries a hyphen is
        the document that tells them apart, and it is a real spelling: an
        article numbered ``100-1`` within its issue.
        """
        meta = """
    <title-group><article-title>An article</article-title></title-group>
    <fpage>100-1</fpage><lpage>100-9</lpage>"""

        article = JATSParser(_article_with_meta(meta)).parse()

        assert article.pages == "100-1-100-9"

    @pytest.mark.parametrize(
        ("field", "deposited", "expected"),
        [
            ("volume", "<volume>12</volume><volume>13</volume>", "13"),
            ("issue", "<issue>3</issue><issue>4</issue>", "4"),
            ("pages", "<fpage>100</fpage><fpage>200</fpage>", "200"),
            # A second <fpage> opens a new range, so its own <lpage> closes it.
            (
                "pages",
                "<fpage>100</fpage><lpage>101</lpage><fpage>200</fpage><lpage>201</lpage>",
                "200-201",
            ),
        ],
        ids=["volume", "issue", "fpage", "fpage-reopens-a-range"],
    )
    def test_a_non_empty_second_element_still_wins(self, field, deposited, expected):
        """The guard is about emptiness, not about repetition.

        Last writer is what these arms are, and the ``<fpage>`` half of it is
        argued at the arm: ``and not self.pages`` stored ``100-101-201`` for a
        doubled range, a value no document states.
        """
        meta = f"""
    <title-group><article-title>An article</article-title></title-group>
    <pub-date pub-type="epub"><year>2024</year></pub-date>
    {deposited}"""

        article = JATSParser(_article_with_meta(meta)).parse()

        assert getattr(article, field) == expected


def _article_citing(citation: str) -> bytes:
    """A minimal article whose one reference is ``citation``, verbatim."""
    return f"""<?xml version="1.0"?>
<article>
  <front><article-meta>{_OWN_META}</article-meta></front>
  <body><sec><title>Results</title><p>Body prose.</p></sec></body>
  <back><ref-list><ref id="r1">{citation}</ref></ref-list></back>
</article>""".encode()


class TestAnElocationIdIsTheLocatorWhereThereIsNoPageRange:
    """``<elocation-id>`` is JATS's electronic locator, in place of a page range.

    Nothing read it, so an article paginated that way stored no locator at
    all — 4,869 of the 8,118 served articles of
    ``PMC10030002_PMC10040000.xml.gz`` and 81,934 of the 97,909 of
    ``oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26.tar.gz`` — and the
    journal line ``FullTextService`` caches read ``J 12(3) (2024)`` (issue
    #265). The reference half is larger: 8,457 served and 406,213 archive
    references store one and no ``<fpage>``, and ``formatted_citation`` and
    the rendered reference list printed no locator for 8,457 and 406,211 of
    them — the other 2, whose locator is all they tag, printing ``citation``,
    which carries it.

    It is a field of its own and **not folded into** ``pages``, which a
    downstream reads as a page range; ``e0123456`` is not one. The
    ``<article-meta>`` model admits a page range *or* an ``<elocation-id>``,
    never both, and no article in either artifact deposits both — but a
    citation may, and there neither is reliably the locator, so a rendered
    locator prints the page range where there is one, as it did before.
    """

    def test_the_articles_elocation_id_is_stored_and_rendered(self):
        """Issue #265's own reproduction, and the journal line it caches."""
        meta = """
    <title-group><article-title>An article</article-title></title-group>
    <pub-date pub-type="epub"><year>2024</year></pub-date>
    <volume>12</volume><issue>3</issue><elocation-id>e0123456</elocation-id>"""

        article, html = JATSParser(
            _article_with_meta(meta, journal_meta="<journal-title>J</journal-title>")
        ).parse_with_html()

        assert (article.volume, article.pages, article.elocation_id) == ("12", "", "e0123456")
        assert '<p class="journal-info"><em>J</em> 12(3): e0123456 (2024)</p>' in html

    def test_an_elocation_id_is_read_without_its_surrounding_whitespace(self):
        meta = "<volume>7</volume><elocation-id>\n      e42\n    </elocation-id>"

        article = JATSParser(_article_with_meta(meta)).parse()

        assert article.elocation_id == "e42"

    def test_a_references_elocation_id_is_read_without_its_surrounding_whitespace(self):
        """The reference branch strips each part before joining it."""
        citation = (
            "<element-citation><source>J</source><volume>3</volume>"
            "<elocation-id>\n  e8\n</elocation-id><elocation-id> 1 </elocation-id>"
            "</element-citation>"
        )

        reference = JATSParser(_article_citing(citation)).parse().references[0]

        assert reference.elocation_id == "e81"

    @pytest.mark.parametrize(
        ("second", "expected"),
        [("<elocation-id>e2</elocation-id>", "e2"), ("<elocation-id> </elocation-id>", "e1")],
        ids=["a-second-locator", "an-empty-second-element"],
    )
    def test_the_articles_last_elocation_id_is_kept(self, second, expected):
        """Last writer, as the ``<fpage>``, ``<volume>`` and ``<issue>`` arms.

        Invalid markup — ``<article-meta>`` admits one ``<elocation-id>`` —
        and no article in the four artifacts deposits two, so this pins the
        family's rule rather than a population. It is deliberately *not* the
        reference branch's join: that rule answers a split measured only in
        citations, and joining two values no measured article shows adjacent
        would store a locator neither states. An empty one states no locator
        and does not blank the one before it, the ``<lpage>`` arm's guard,
        where those three arms still would (PR #269's review; #272).
        """
        meta = f"<volume>7</volume><elocation-id>e1</elocation-id>{second}"

        article = JATSParser(_article_with_meta(meta)).parse()

        assert article.elocation_id == expected

    def test_a_page_range_is_rendered_ahead_of_an_elocation_id(self):
        """Both deposited — invalid in ``<article-meta>``, and measured at 0.

        Each field keeps what the document states, and the rendered line
        prints the one locator a citation prints: the page range.
        """
        meta = (
            '<pub-date pub-type="epub"><year>2024</year></pub-date>'
            "<volume>12</volume><fpage>100</fpage><lpage>101</lpage>"
            "<elocation-id>e5</elocation-id>"
        )

        article, html = JATSParser(
            _article_with_meta(meta, journal_meta="<journal-title>J</journal-title>")
        ).parse_with_html()

        assert (article.pages, article.elocation_id) == ("100-101", "e5")
        assert '<p class="journal-info"><em>J</em> 12: 100-101 (2024)</p>' in html

    @pytest.mark.parametrize(
        ("locator", "rendered"),
        [
            ("<elocation-id>e42</elocation-id>", "e42"),
            ("<fpage>100</fpage><lpage>101</lpage>", "100-101"),
            # An issue alone precedes it, so the separator stays.
            ("<issue>3</issue><elocation-id>e5</elocation-id>", "(3): e5"),
        ],
        ids=["elocation-id", "page-range", "issue-only"],
    )
    def test_a_locator_with_no_volume_or_issue_is_printed_bare(self, locator, rendered):
        """No ``: `` separating the locator from nothing.

        The journal line prefixed a locator with ``: `` whatever preceded it,
        so an article carrying no ``<volume>`` or ``<issue>`` rendered
        ``<em>J</em> : 100-101 (2024)``. Storing the ``<elocation-id>`` would
        have spread that to 10 of the 8,118 served articles, and it is the bare
        form ``formatted_citation`` already gives a reference.
        """
        meta = f'<pub-date pub-type="epub"><year>2024</year></pub-date>{locator}'

        html = JATSParser(
            _article_with_meta(meta, journal_meta="<journal-title>J</journal-title>")
        ).to_html()

        assert f'<p class="journal-info"><em>J</em> {rendered} (2024)</p>' in html

    def test_a_review_rounds_elocation_id_leaves_the_articles_alone(self):
        """A ``<sub-article>``'s ``<front>`` matches the article's owner path.

        The round's own characters never arrive, so what would leak without the
        nested-article suppression is an *empty* value blanking the article's.
        Two protections stand in the way since PR #269's review — the
        suppression, and the arm refusing an empty value — so this pins their
        conjunction; the suppression alone is pinned for the fields beside it.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <title-group><article-title>An article</article-title></title-group>
    <volume>5</volume><elocation-id>e1</elocation-id>
  </article-meta></front>
  <body><sec><title>Results</title><p>Body prose.</p></sec></body>
  <sub-article article-type="reviewer-report">
    <front><article-meta><volume>9</volume><elocation-id>e9</elocation-id></article-meta></front>
    <body><p>Reviewer prose.</p></body>
  </sub-article>
</article>"""

        article = JATSParser(data).parse()

        assert article.elocation_id == "e1"

    def test_an_element_citations_elocation_id_reaches_the_reference(self):
        """The commoner spelling: 248,148 of the 406,553 archive references carrying one.

        Carrying one in their *first* citation element, the part a
        reference's structured fields are read from.
        """
        citation = (
            '<element-citation publication-type="journal">'
            "<source>PLoS One</source><year>2020</year>"
            "<volume>15</volume><issue>3</issue><elocation-id>e0230000</elocation-id>"
            "</element-citation>"
        )

        article, html = JATSParser(_article_citing(citation)).parse_with_html()

        reference = article.references[0]
        assert (reference.first_page, reference.elocation_id) == ("", "e0230000")
        assert reference.formatted_citation == "PLoS One. (2020). 15(3):e0230000"
        assert "(2020). 15(3):e0230000</li>" in html

    def test_a_references_page_range_is_rendered_ahead_of_its_elocation_id(self):
        """The reference list keeps printing what it printed before the field.

        A citation depositing both is 92 served and 340 archive references,
        and neither element is reliably the locator there (see
        ``JATSReferenceInfo.elocation_id``), so the rendered item is unchanged.
        """
        citation = (
            "<element-citation><source>Accid Anal Prev</source><volume>109</volume>"
            "<fpage>123</fpage><lpage>31</lpage>"
            "<elocation-id>S0001-4575(17)30300-X</elocation-id></element-citation>"
        )

        article, html = JATSParser(_article_citing(citation)).parse_with_html()

        assert article.references[0].elocation_id == "S0001-4575(17)30300-X"
        assert "<em>Accid Anal Prev</em>. 109:123-31</li>" in html

    def test_a_lone_elocation_id_does_not_displace_the_deposited_citation(self):
        """A locator alone is not a citation, and the deposited string says more.

        Both renderers used to fall back to ``citation`` only where *no*
        structured component would print, so a ``<mixed-citation>`` whose one
        tagged child is an ``<elocation-id>`` rendered that child alone once it
        was read — and in ``PMC12019704`` (2 archive references) the depositor
        put a *title* there, so the access date and URL left the cached HTML.
        Issue #265 made the rule for the locator and #268 generalised it to
        every lone component; this fixture is the locator's case of it.
        """
        citation = (
            '<mixed-citation publication-type="miscellaneous">'
            "<elocation-id>Population of England and Wales</elocation-id>, "
            "Accessed September 9, 2021, "
            '<ext-link ext-link-type="uri">https://example.org/latest</ext-link>.'
            "</mixed-citation>"
        )
        deposited = (
            "Population of England and Wales, Accessed September 9, 2021, "
            "https://example.org/latest."
        )

        article, html = JATSParser(_article_citing(citation)).parse_with_html()

        reference = article.references[0]
        # Read, so it is the one-component rule and not a missed read that keeps
        # the deposited string.
        assert reference.elocation_id == "Population of England and Wales"
        assert reference.citation == deposited
        assert reference.formatted_citation == deposited
        assert f'<li id="ref-r1">{deposited}</li>' in html

    def test_an_issue_beside_a_lone_elocation_id_does_not_displace_the_citation(self):
        """An ``<issue>`` is printed only after a ``<volume>``, so the locator is still alone.

        The rule's first cut counted the issue as a component, so this
        reference rendered ``e7`` in both renderers where ``main`` rendered the
        whole deposited string (PR #269's review). Measured at 0 references in
        the four artifacts, so a direction. There is no list to keep in step
        since #268 — each renderer counts the parts it built — but the
        distinction the list got wrong is the same one the count depends on,
        so the fixture stays.
        """
        citation = (
            "<mixed-citation>Report series, no. <issue>3</issue>, item "
            "<elocation-id>e7</elocation-id>. https://example.org/r.</mixed-citation>"
        )
        deposited = "Report series, no. 3, item e7. https://example.org/r."

        article, html = JATSParser(_article_citing(citation)).parse_with_html()

        reference = article.references[0]
        assert (reference.issue, reference.elocation_id) == ("3", "e7")
        assert reference.formatted_citation == deposited
        assert f'<li id="ref-r1">{deposited}</li>' in html

    def test_a_lone_year_does_not_displace_the_deposited_citation(self):
        """The same rule one component over, which is issue #268.

        ``PMC12000051`` cites an IRENA report whose ``<mixed-citation>`` tags
        its year and nothing else, so the reference list printed ``(2023)`` and
        the report's name, publisher and URL left the cached HTML. Measured at
        74 served and 3,567 archive references for the year alone; 828 and
        15,748 over all six components.
        """
        citation = (
            "<mixed-citation>IRENA. Energizing health: accelerating electricity "
            "access in health-care facilities. (<year>2023</year>). Available at: "
            '<ext-link ext-link-type="uri">https://example.org/r</ext-link>.'
            "</mixed-citation>"
        )
        deposited = (
            "IRENA. Energizing health: accelerating electricity access in health-care "
            "facilities. (2023). Available at: https://example.org/r."
        )

        article, html = JATSParser(_article_citing(citation)).parse_with_html()

        reference = article.references[0]
        # Read, so it is the rule and not a missed read that keeps the deposit.
        assert (reference.year, reference.citation) == ("2023", deposited)
        assert reference.formatted_citation == deposited
        assert f'<li id="ref-r1">{deposited}</li>' in html

    def test_a_lone_author_list_does_not_displace_the_deposited_citation(self):
        """The largest of the six moved populations, pinned end to end.

        ``PMC12000049`` cites an IFR statistical report whose
        ``<mixed-citation>`` tags its ``<person-group>`` and nothing else, so
        the reference list printed ``C Müller, N Kutzbach`` for a work it then
        never named. An author list alone is 443 of the 828 served references
        that move and 5,510 of the 15,748 archive ones — the majority of the
        served half — where the two other round-trip fixtures here cover the
        two *smallest* rows (a year, 74 / 3,567, and a locator, 9 / 55).

        It also shows what the deposit costs where it wins: ``<surname>`` and
        ``<given-names>`` are adjacent with nothing between them, so the
        deposited string carries the run-together form ``citation``'s own
        docstring documents (issue #146), and the rendering trades a tidy
        author list for naming the work at all. That is the trade #268 chose,
        and it is measured rather than incidental.
        """
        citation = (
            "<mixed-citation><person-group person-group-type='author'>"
            "<name><surname>Müller</surname><given-names>C</given-names></name>"
            "<name><surname>Kutzbach</surname><given-names>N</given-names></name>"
            "</person-group>. World Robotics 2023 - Industrial Robots. IFR "
            "Statistical Department, VDMA Services GmbH.</mixed-citation>"
        )
        deposited = (
            "MüllerCKutzbachN. World Robotics 2023 - Industrial Robots. "
            "IFR Statistical Department, VDMA Services GmbH."
        )

        article, html = JATSParser(_article_citing(citation)).parse_with_html()

        reference = article.references[0]
        # Read, so it is the rule and not a missed read that keeps the deposit.
        assert reference.authors == ["C Müller", "N Kutzbach"]
        assert reference.citation == deposited
        assert reference.formatted_citation == deposited
        assert f'<li id="ref-r1">{deposited}</li>' in html

    def test_a_second_component_earns_the_structured_rendering(self):
        """#268 is a count, so the reference beside it has to still render structured.

        Without this the rule could refuse every ``<mixed-citation>`` and the
        test above would not notice.
        """
        citation = (
            "<mixed-citation><source>J Small Trials</source>, (<year>2023</year>). "
            "Available at: https://example.org/r.</mixed-citation>"
        )

        article, html = JATSParser(_article_citing(citation)).parse_with_html()

        assert article.references[0].formatted_citation == "J Small Trials. (2023)"
        assert '<li id="ref-r1"><em>J Small Trials</em>. (2023)</li>' in html

    def test_an_element_citations_lone_elocation_id_is_rendered(self):
        """No ``citation`` to defer to, so both renderers print the locator.

        An ``<element-citation>`` writes no ``citation``; the reference list
        rendered an empty item when its fallback's ``citation`` half was
        dropped, and nothing caught it (PR #269's review).
        """
        citation = "<element-citation><elocation-id>e7</elocation-id></element-citation>"

        article, html = JATSParser(_article_citing(citation)).parse_with_html()

        assert article.references[0].formatted_citation == "e7"
        assert '<li id="ref-r1">e7</li>' in html

    def test_an_elocation_id_in_another_work_in_prose_stays_in_the_prose(self):
        """Its text lands where it landed before the arm existed.

        JATS admits ``<related-article>`` in a ``<p>``, and a related article
        may carry an ``<elocation-id>``. Taking a buffer of its own would have
        deleted the locator from the sentence; it merges back, so it is the
        sentence's wherever no arm reads it. Measured at 0 in the served and
        archive artifacts and ``PMC000xxxxxx``, so this pins a direction.
        """
        body = (
            "<sec><title>Notes</title><p>See <related-article>PLoS One 15: "
            "<elocation-id>e1</elocation-id></related-article> for the data.</p></sec>"
        )

        article = JATSParser(_article_with_body(body)).parse()

        assert article.body_sections[0].paragraphs == ["See PLoS One 15: e1 for the data."]

    def test_both_renderers_escape_a_locator(self):
        """A publisher-supplied string reaching the HTML ``FullTextService`` caches."""
        meta = (
            '<pub-date pub-type="epub"><year>2024</year></pub-date>'
            "<elocation-id>e&lt;1&amp;2</elocation-id>"
        )
        data = _article_with_meta(
            meta,
            journal_meta="<journal-title>J</journal-title>",
            after_meta="",
        ).replace(
            b"</body>",
            b"</body><back><ref-list><ref id='r1'><element-citation><source>S</source>"
            b"<elocation-id>e&lt;3&amp;4</elocation-id></element-citation></ref></ref-list></back>",
        )

        html = JATSParser(data).to_html()

        assert '<p class="journal-info"><em>J</em> e&lt;1&amp;2 (2024)</p>' in html
        assert "<em>S</em>. e&lt;3&amp;4</li>" in html

    def test_a_mixed_citation_keeps_its_elocation_id_in_the_citation_string(self):
        """The field is filled, and the printed string keeps the locator too.

        Every descendant of a ``<mixed-citation>`` is the citation's text
        (issue #146), so an ``<elocation-id>`` that takes a buffer of its own
        has to merge it back or delete itself from ``citation``.
        """
        citation = (
            "<mixed-citation><source>Sci Data</source> <volume>1</volume>: "
            "<elocation-id>140020</elocation-id>.</mixed-citation>"
        )

        reference = JATSParser(_article_citing(citation)).parse().references[0]

        assert (reference.citation, reference.elocation_id) == ("Sci Data 1: 140020.", "140020")

    @pytest.mark.parametrize(
        ("locator", "expected"),
        [
            # PMC12077229's own deposit: one locator split across four
            # adjacent elements, which `citation` prints as one word.
            (
                "<elocation-id>e8</elocation-id><elocation-id>1</elocation-id>"
                "<elocation-id>72</elocation-id><elocation-id>1</elocation-id>",
                "e81721",
            ),
            # PMC12104920's: the same locator deposited twice (in an
            # <element-citation> there; both spellings are run here).
            ("<elocation-id>i5239</elocation-id><elocation-id>i5239</elocation-id>", "i5239"),
            # A part that is a suffix of the whole so far is still a part: only
            # a repeat of the *whole* is skipped.
            ("<elocation-id>e1</elocation-id><elocation-id>1</elocation-id>", "e11"),
            # A part's inner whitespace is its own, and in both sides of the test.
            ("<elocation-id>quiz 380</elocation-id><elocation-id>-1</elocation-id>", "quiz 380-1"),
        ],
        ids=[
            "split-across-elements",
            "repeated",
            "part-repeating-a-suffix",
            "part-with-inner-whitespace",
        ],
    )
    @pytest.mark.parametrize(
        "template",
        [
            "<mixed-citation><source>Elife</source>. <volume>11</volume>:{}.</mixed-citation>",
            "<element-citation><source>Elife</source><volume>11</volume>{}</element-citation>",
        ],
        ids=["mixed-citation", "element-citation"],
    )
    def test_several_elocation_ids_in_one_citation_are_one_locator(
        self, template, locator, expected
    ):
        """6 of the 406,553 archive references carrying one in their first citation deposit several.

        Five split one locator across adjacent elements with nothing between
        them, and one repeats it. Last writer stored ``1`` for ``e81721``, and
        first writer ``e8`` — a wrong locator where there used to be none — so
        the parts are joined, and a part repeating the whole is not appended.
        Neither is a part the reference loses, so neither is counted.
        """
        handler = JATSParser(_article_citing(template.format(locator)))._run_parser()

        assert handler.references[0].elocation_id == expected
        assert handler.elocation_parts_dropped == 0

    @pytest.mark.parametrize(
        "citation",
        [
            # An erratum's locator printed after the reference's own.
            "<mixed-citation><source>J</source> 2020;<elocation-id>e1</elocation-id>. "
            "Erratum in: J 2021;<elocation-id>e2</elocation-id>.</mixed-citation>",
            # Whitespace is typeset text in a <mixed-citation>: `e1 e2` is two
            # locators (PR #269's review; the rule read it whitespace aside).
            "<mixed-citation><source>J</source> 2020;<elocation-id>e1</elocation-id> "
            "<elocation-id>e2</elocation-id>.</mixed-citation>",
            # The same space, deposited inside the first part's element.
            "<mixed-citation><source>J</source> 2020;<elocation-id>e1 </elocation-id>"
            "<elocation-id>e2</elocation-id>.</mixed-citation>",
            # The joined text printed *earlier* in the citation is not adjacency:
            # the buffer has to end with it.
            "<mixed-citation>See e1e2 in <source>J</source>;<elocation-id>e1</elocation-id>; "
            "<elocation-id>e2</elocation-id>.</mixed-citation>",
        ],
        ids=["erratum", "a-space-between", "a-space-inside-the-first", "joined-text-earlier"],
    )
    def test_a_second_locator_the_citation_prints_apart_is_not_joined(self, citation, parser_log):
        """Only a part adjacent to the previous one continues it.

        A citation printing a second locator apart from its own is two locators,
        and joining them stores ``e1e2``, which no document states. Measured at
        0 non-adjacent multi-locator citations in the archive's 97,909 articles,
        so this pins a direction: the first is kept, as the structured fields
        keep a ``<ref>``'s first citation part (#149) — and, since the second
        is then in no structured field, counted and reported once per article.
        """
        handler = JATSParser(_article_citing(citation))._run_parser()

        assert handler.references[0].elocation_id == "e1"
        assert handler.elocation_parts_dropped == 1
        assert [
            m for m in parser_log.messages(logging.WARNING) if "did not continue the reference" in m
        ] == [
            "JATS parse of 10.1000/own: 1 <elocation-id> part(s) did not continue the "
            "reference's own locator and were not stored in its elocation_id, which keeps "
            "the first (issue #265)"
        ]

    def test_whitespace_after_the_last_part_does_not_part_it_from_the_one_before(self):
        """The closing part's own trailing whitespace follows the whole locator.

        ``e81 `` is printed as one run, so only whitespace *between* the parts
        — or text — parts them in a ``<mixed-citation>``.
        """
        citation = (
            "<mixed-citation><source>J</source>:<elocation-id>e8</elocation-id>"
            "<elocation-id>1\n</elocation-id>.</mixed-citation>"
        )

        handler = JATSParser(_article_citing(citation))._run_parser()

        assert handler.references[0].elocation_id == "e81"
        assert handler.elocation_parts_dropped == 0

    def test_an_element_citations_indentation_does_not_part_its_locators(self):
        """Whitespace between an ``<element-citation>``'s children is insignificant.

        Element-only content authored no string, so indentation cannot say two
        parts were printed apart; only a close between them can (the test
        below), and a pretty-printed deposit of a split locator is joined.
        """
        citation = (
            "<element-citation>\n  <source>J</source>\n  <elocation-id>e8</elocation-id>\n"
            "  <elocation-id>1</elocation-id>\n</element-citation>"
        )

        handler = JATSParser(_article_citing(citation))._run_parser()

        assert handler.references[0].elocation_id == "e81"
        assert handler.elocation_parts_dropped == 0

    @pytest.mark.parametrize(
        "template",
        [
            "<mixed-citation><source>J</source>:{}.</mixed-citation>",
            "<element-citation><source>J</source>{}</element-citation>",
        ],
        ids=["mixed-citation", "element-citation"],
    )
    def test_a_child_inside_a_part_does_not_part_it_from_the_one_before(self, template):
        """``<elocation-id>`` is text only in the Tag Library, but a child is well-formed.

        Every other element's close parts two locators, and a ``<sup>`` closing
        *inside* the second part would have parted it from the first — storing
        ``e8`` where the citation prints ``e81721`` (PR #269's review). 0 such
        children in the served and archive artifacts, so a direction.
        """
        locator = "<elocation-id>e8</elocation-id><elocation-id><sup>1</sup>721</elocation-id>"

        reference = JATSParser(_article_citing(template.format(locator))).parse().references[0]

        assert reference.elocation_id == "e81721"

    def test_an_empty_part_does_not_rejoin_two_locators_an_element_parted(self):
        """An empty ``<elocation-id/>`` reads nothing, so it re-arms nothing.

        Here the ``<source>`` close parts ``e1`` from ``e2``; an empty part
        between them letting the next one continue would store ``e1e2`` in an
        ``<element-citation>``, whose buffer shows no ``<source>`` text.
        """
        citation = (
            "<element-citation><elocation-id>e1</elocation-id><source>J</source>"
            "<elocation-id/><elocation-id>e2</elocation-id></element-citation>"
        )

        handler = JATSParser(_article_citing(citation))._run_parser()

        assert handler.references[0].elocation_id == "e1"
        assert handler.elocation_parts_dropped == 1

    def test_an_element_between_two_locators_in_an_element_citation_parts_them(self):
        """The spelling whose buffer cannot show what lies between the parts.

        An ``<element-citation>`` prints nothing, and a child that takes a
        buffer of its own and does not merge it back (``<source>``, here)
        leaves no trace in the citation's, so a test on that buffer alone read
        these two locators as adjacent and stored ``e1e2``. Any element closing
        between two parts parts them. 1 multi-locator ``<element-citation>`` was
        measured in the archive, a repeat, so this pins a direction.
        """
        citation = (
            "<element-citation><elocation-id>e1</elocation-id><source>J</source>"
            "<elocation-id>e2</elocation-id></element-citation>"
        )

        handler = JATSParser(_article_citing(citation))._run_parser()

        assert handler.references[0].elocation_id == "e1"
        assert handler.elocation_parts_dropped == 1

    @pytest.mark.parametrize(
        ("own", "expected"),
        [("", ""), ("<elocation-id>e1</elocation-id>", "e1")],
        ids=["no-locator-of-its-own", "after-its-own-locator"],
    )
    def test_a_related_works_locator_inside_a_citation_is_not_the_references(self, own, expected):
        """JATS 1.3 admits ``<related-object>`` inside both citation elements.

        Every ``<elocation-id>`` read from a reference's first citation element
        in the served and archive artifacts (8,549 and 406,553 references) is
        a direct child of it, so the parent test is exact on the data and keeps
        a related work's locator off the reference. 0 nested ones were
        measured, so this pins a direction.
        """
        citation = (
            f"<element-citation><source>J</source>{own}"
            "<related-object>Erratum <elocation-id>e9</elocation-id></related-object>"
            "</element-citation>"
        )

        reference = JATSParser(_article_citing(citation)).parse().references[0]

        assert reference.elocation_id == expected

    @pytest.mark.parametrize(
        ("first_part", "expected"),
        [
            ("<elocation-id>e1</elocation-id>", "e1"),
            ("<fpage>5</fpage>", ""),
        ],
        ids=["first-part-carries-one", "first-part-is-paginated"],
    )
    def test_only_the_first_citation_part_fills_the_elocation_id(self, first_part, expected):
        """A ``<ref>`` of several citation elements keeps its first part's fields (#149).

        Its structured fields are the first part's alone, so a later part's
        ``<elocation-id>`` neither overwrites the first's nor stands in where
        the first is paginated.
        """
        citation = (
            f"<mixed-citation><source>A</source> <volume>1</volume>:{first_part}</mixed-citation>"
            "<mixed-citation><source>B</source> <volume>2</volume>:"
            "<elocation-id>e2</elocation-id></mixed-citation>"
        )

        reference = JATSParser(_article_citing(citation)).parse().references[0]

        assert reference.elocation_id == expected


_FUNDING_GROUP = """
    <funding-group>
      <award-group><funding-source>NIH</funding-source><award-id>R01</award-id></award-group>
      <funding-statement>This work was supported by the NIH.</funding-statement>
    </funding-group>"""


class TestAFundingStatementReachesTheArticle:
    """A ``<funding-statement>`` is stored and rendered (issue #257).

    It had no arm and accumulated nowhere, so its text reached the root
    buffer nothing reads: the funding disclosure was in no field of
    ``JATSArticle`` and not in the HTML ``FullTextService`` caches, with no
    counter and no line. Issue #230's front-matter routing could not see it,
    because a statement is not a ``<p>`` (1 of 42,611 archive statements holds
    one). Over the 8,118 served articles of ``PMC10030002_PMC10040000.xml.gz``
    no statement reached the article in 1,337 of the 1,367 carrying one, and
    over the 97,909 archive articles of
    ``oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26`` in 41,260 of 42,295.

    **Modelled, not routed as prose** — the maintainer's choice once the
    numbers were in. Routed, it would have joined the front-matter run-on
    rendered under ``<h2>Abstract</h2>`` (#279) and reached no field a
    downstream could address.
    """

    def test_the_issues_reproduction_is_stored_and_rendered(self):
        article, html = JATSParser(_article_with_meta(_FUNDING_GROUP)).parse_with_html()

        assert article.funding_statements == ["This work was supported by the NIH."]
        assert (
            '<section class="funding">\n<h2>Funding</h2>\n'
            "<p>This work was supported by the NIH.</p>\n</section>"
        ) in html

    def test_a_statement_in_a_support_group_is_the_articles_own(self):
        """JATS 1.3 admits ``<funding-group>`` inside ``<support-group>`` too.

        68 of the 1,390 served statements and 543 of the 42,611 archive ones
        are deposited that way, so leaving the wrapper out loses real ones.
        """
        meta = f"<support-group>{_FUNDING_GROUP}</support-group>"

        article = JATSParser(_article_with_meta(meta)).parse()

        assert article.funding_statements == ["This work was supported by the NIH."]

    def test_several_statements_are_kept_in_document_order(self):
        meta = """
    <funding-group>
      <funding-statement>First.</funding-statement>
      <funding-statement>Second.</funding-statement>
    </funding-group>
    <support-group><funding-group>
      <funding-statement>Third.</funding-statement>
    </funding-group></support-group>"""

        article, html = JATSParser(_article_with_meta(meta)).parse_with_html()

        assert article.funding_statements == ["First.", "Second.", "Third."]
        assert "<p>First.</p>\n<p>Second.</p>\n<p>Third.</p>" in html

    def test_inline_markup_keeps_its_text_and_the_whitespace_is_normalised(self):
        """Its children's text is the statement's, as it is a ``<p>``'s.

        A value reaching a public field is normalised, as ``<attrib>``'s and
        ``<term>``'s are, so a depositor's line break does not reach it.
        """
        meta = """<funding-group><funding-statement>Supported by the
        <italic>NIH</italic> (grant <bold>R01</bold>) &amp;
        H<sub>2</sub>O.</funding-statement></funding-group>"""

        article = JATSParser(_article_with_meta(meta)).parse()

        assert article.funding_statements == ["Supported by the NIH (grant R01) & H2O."]

    def test_the_statement_does_not_also_reach_the_prose(self):
        """One place, so the statement is not rendered twice.

        The ``<award-group>`` text beside it is still read by nothing, so it
        must not surface either: a structured award is not modelled here.
        """
        article, html = JATSParser(_article_with_meta(_FUNDING_GROUP)).parse_with_html()

        paragraphs = [p for section in article.body_sections for p in section.paragraphs]
        assert paragraphs == ["Body prose."]
        assert html.count("supported by the NIH") == 1
        assert "R01" not in html

    def test_an_empty_statement_stores_nothing_and_renders_no_heading(self):
        meta = "<funding-group><funding-statement>  </funding-statement></funding-group>"

        article, html = JATSParser(_article_with_meta(meta)).parse_with_html()

        assert article.funding_statements == []
        assert "Funding" not in html

    def test_an_article_without_one_renders_no_heading(self):
        article, html = JATSParser(_article_with_meta("")).parse_with_html()

        assert article.funding_statements == []
        assert "Funding" not in html

    def test_a_review_rounds_statement_is_not_the_articles(self):
        """``<front-stub>`` is the third container the Tag Library names.

        It is a nested article's, and the suppression keeps it off this one.
        """
        doc = _article_with_meta(
            "",
            after_meta="",
        ).replace(
            b"</body>",
            b"</body><sub-article><front-stub><funding-group>"
            b"<funding-statement>The reviewer was paid.</funding-statement>"
            b"</funding-group></front-stub></sub-article>",
        )

        article, html = JATSParser(doc).parse_with_html()

        assert article.funding_statements == []
        assert "reviewer" not in html

    def test_the_statement_is_escaped(self):
        meta = (
            "<funding-group><funding-statement>Grant &lt;A&amp;B&gt;"
            "</funding-statement></funding-group>"
        )

        article, html = JATSParser(_article_with_meta(meta)).parse_with_html()

        assert article.funding_statements == ["Grant <A&B>"]
        assert "<p>Grant &lt;A&amp;B&gt;</p>" in html

    def test_the_section_follows_the_body_and_precedes_the_exhibits(self):
        """Beside the back matter's own declarations, which end the body.

        Back-matter prose — an ``<ack>``, a competing-interest ``<fn-group>``
        (issue #224) — is the last of ``body_sections``, so the funding
        disclosure is rendered after it and ahead of the figures, tables and
        references, where the declarations it sits beside in print are.
        """
        doc = _article_with_meta(_FUNDING_GROUP).replace(
            b"</body>",
            b'</body><back><ack><p>We thank X.</p></ack><ref-list><ref id="r1">'
            b"<mixed-citation>A ref.</mixed-citation></ref></ref-list></back>",
        )
        doc = doc.replace(
            b"<p>Body prose.</p>",
            b'<p>Body prose.</p><fig id="f1"><label>Figure 1</label>'
            b"<caption><p>Cap.</p></caption></fig>",
        )

        html = JATSParser(doc).to_html()

        positions = [
            html.index(marker)
            for marker in (
                "<p>Body prose.</p>",
                "<p>We thank X.</p>",
                "<h2>Funding</h2>",
                "<h2>Figures</h2>",
                "<h2>References</h2>",
            )
        ]
        assert positions == sorted(positions)


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
        """``<back>`` prose is kept, and still does not make an article a body.

        The first half of this test's original claim — *"only ``<body>`` gets
        an implicit section"* — was overturned by issue #224: loose ``<back>``
        prose is where funding acknowledgements and competing-interest
        statements live, and dropping it blinded a reader to declarations the
        article did make. What survives is the assertion, which is the half
        that matters: ``body_paragraph_count`` is incremented for ``<body>``
        alone, so a document carrying nothing but front matter and back matter
        is still body-less and ``FullTextService`` still holds it back. A
        session finding this should read this comment rather than restore the
        old reading.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Back only</article-title>
  </title-group></article-meta></front>
  <back><p>Loose acknowledgement text.</p></back>
</article>"""
        article = JATSParser(data).parse()
        assert article.has_body is False
        assert [p for s in article.body_sections for p in s.paragraphs] == [
            "Loose acknowledgement text."
        ]

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


class TestJATSParserUnsectionedBackMatter:
    """``<sec>`` is optional inside ``<back>`` too, and the prose is not spare.

    ``<ack>``, ``<notes>``, ``<fn-group>``, ``<app>``, ``<glossary>`` and
    ``<bio>`` routinely hold a ``<p>`` directly, and that is where funding
    acknowledgements and competing-interest statements live.
    ``_append_prose``'s unsectioned branch was gated on ``in_body`` alone, so
    every one of them was dropped — issue #224, found by a JATS parity check
    against the Swift port, whose own comment names the same consequence.

    **The population is the largest this module has measured.** Instrumented
    at ``_append_prose`` over the 8,118 served articles of Europe PMC's named
    OA package ``PMC10030002_PMC10040000.xml.gz``, **5,990 (73.8%) gain at
    least one**, and they are 40,342 paragraphs and 5.91 MB of prose. By the
    ``<back>`` child that owns them: ``<fn-group>`` 13,650 (in 3,925
    articles), ``<glossary>`` 10,693 (723), ``<notes>`` 10,286 (2,241),
    ``<ack>`` 4,892 (4,359), ``<app-group>`` 618 (99), ``<bio>`` 203 (43).
    Neither corpus is committed here, so these are quoted from the named
    artifact rather than re-derived by a test — but every row **is** an input
    under test, in
    :meth:`test_every_measured_back_container_reaches_the_article`, because a
    table quoted in six files and driven by four cases is the shape
    ``TestTheStatedCountsAreWhatTheCorpusHolds`` exists to break.

    Two things the widening deliberately does not do, each with its own test
    below: it does not touch ``body_paragraph_count``, and it does not take
    ``<ref-list>``.
    """

    BACK_MATTER = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Back matter</article-title>
  </title-group></article-meta></front>
  <body><sec><title>Methods</title><p>We did the thing.</p></sec></body>
  <back>
    <ack><title>Acknowledgements</title>
      <p>This work was funded by grant XYZ from the Example Foundation.</p></ack>
    <fn-group><fn fn-type="COI-statement">
      <p>The authors declare no competing interests.</p></fn></fn-group>
    <notes><title>Data availability</title>
      <p>Data are available from the corresponding author.</p></notes>
  </back>
</article>"""

    def test_the_back_matter_shape_is_a_section_per_deposited_heading(self):
        """Membership tests cannot see the *shape*, and the shape is a choice.

        This asserted **one** untitled section for all three containers until
        issue #231 was taken, and it is the test that change had to redden:
        every other test in this class asserts ``"..." in paragraphs``, which
        a change to the shape passes unaltered. What it pins now is the rule
        #231 settled — a container's own deposited ``<title>`` titles the
        prose *its own element* holds — so the ``<ack>`` and the ``<notes>``
        carry the headings the publisher wrote, while the ``<fn-group>``,
        which deposits none, keeps an untitled section of its own rather than
        rendering under *Acknowledgements*.

        The fixture's ``<notes>`` gained a ``<title>`` with that change: two
        titled containers around one untitled one is what makes the boundary
        visible, and before it every container here was interchangeable.
        """
        article = JATSParser(self.BACK_MATTER).parse()

        assert [(s.title, s.paragraphs) for s in article.body_sections] == [
            ("Methods", ["We did the thing."]),
            (
                "Acknowledgements",
                ["This work was funded by grant XYZ from the Example Foundation."],
            ),
            ("", ["The authors declare no competing interests."]),
            ("Data availability", ["Data are available from the corresponding author."]),
        ]

    def test_an_acknowledgement_reaches_the_article(self):
        article = JATSParser(self.BACK_MATTER).parse()
        paragraphs = [p for s in article.body_sections for p in s.paragraphs]

        assert "This work was funded by grant XYZ from the Example Foundation." in paragraphs

    def test_a_competing_interests_footnote_reaches_the_article(self):
        """``<fn-group><fn>`` is the commonest of the shapes — 13,650 paragraphs
        in 3,925 of the 8,118 served articles, against ``<ack>``'s 4,892."""
        article = JATSParser(self.BACK_MATTER).parse()
        paragraphs = [p for s in article.body_sections for p in s.paragraphs]

        assert "The authors declare no competing interests." in paragraphs

    def test_a_note_reaches_the_article(self):
        article = JATSParser(self.BACK_MATTER).parse()
        paragraphs = [p for s in article.body_sections for p in s.paragraphs]

        assert "Data are available from the corresponding author." in paragraphs

    def test_back_matter_prose_renders(self):
        html = JATSParser(self.BACK_MATTER).to_html()

        assert "The authors declare no competing interests." in html

    def test_back_matter_is_its_own_section_after_the_body(self):
        """Two implicit sections, not one.

        ``</body>`` flushes before ``<back>`` opens in any DTD-valid document,
        and nothing here validates against a DTD — a well-formed ``<back>``
        nested *inside* a ``<body>`` merges the two, which is why the claim is
        scoped rather than written as "never". What makes the ordinary case
        structural instead of incidental is a slot per container; see
        :class:`TestTheBodySlotCannotBeEmptiedByTheBackFlush`.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Both</article-title>
  </title-group></article-meta></front>
  <body><p>Loose body prose.</p></body>
  <back><ack><p>Loose back prose.</p></ack></back>
</article>"""
        article = JATSParser(data).parse()

        assert [tuple(s.paragraphs) for s in article.body_sections] == [
            ("Loose body prose.",),
            ("Loose back prose.",),
        ]

    def test_a_back_section_keeps_its_own_prose(self):
        """A ``<sec>`` inside ``<back>`` was already routed; loose prose ahead of
        it must flush first rather than fold into it."""
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Order</article-title>
  </title-group></article-meta></front>
  <back>
    <ack><p>Loose prose before the section.</p></ack>
    <app-group><app><sec><title>Appendix A</title>
      <p>Sectioned appendix prose.</p></sec></app></app-group>
  </back>
</article>"""
        article = JATSParser(data).parse()

        assert [(s.title, tuple(s.paragraphs)) for s in article.body_sections] == [
            ("", ("Loose prose before the section.",)),
            ("Appendix A", ("Sectioned appendix prose.",)),
        ]

    def test_back_matter_alone_is_still_not_a_body(self):
        """The counter and the section list answer different questions.

        ``has_body`` gates caching in ``FullTextService`` — a body-less JATS
        document is held back so the tier chain keeps looking for the real
        article — so back matter must never satisfy it. The ``<body>`` here
        carries only whitespace, which opens no section of its own.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>No body</article-title>
  </title-group></article-meta></front>
  <body><p>   </p></body>
  <back><ack><p>Funded by the Example Foundation.</p></ack></back>
</article>"""
        article = JATSParser(data).parse()

        assert article.has_body is False
        assert [p for s in article.body_sections for p in s.paragraphs] == [
            "Funded by the Example Foundation."
        ]

    def test_a_reference_list_keeps_its_apparatus_out_of_the_prose(self):
        """``<ref-list>`` is the one refusal, and it is a misfiling rule.

        A ``<ref>``'s ``<note>`` and a ``<ref-list>``'s own ``<p>`` are
        bibliography apparatus, not article prose: sampled from the same
        package they read *"Faculty Opinions Recommendation"* ten times over,
        *"Papers of special note have been highlighted as: ..."*, and bare DOI
        fragments. Routed into ``body_sections`` they would be appended to the
        article as paragraphs the publisher never wrote there — a corruption
        rather than a blank, which is this module's own reason for preferring
        the blank (#116, #162). Issue #150 is what puts a note-only ``<ref>``
        where it belongs; routing it here would leave it misfiled *and* hide
        that issue's symptom.

        Measured at 163 paragraphs in 39 of the 8,118 served articles, 0.40%
        of the 40,505 the unsectioned branch is offered, so the refusal costs
        little and is the one place this module and the Swift port
        deliberately differ. It is also **reported**, at WARNING, once per
        article — see :class:`TestARefusedApparatusParagraphIsReported`.

        The rule is scoped to this branch: a ``<ref-list>`` under an open
        ``<sec>``, or one in ``<body>``, keeps its apparatus. Both are
        pre-existing and measure 0 of 8,118 served and 1 of 97,909 archive
        articles, so "the one refusal" is the scope of a rule and not a claim
        that no apparatus ever reaches the article.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Refs</article-title>
  </title-group></article-meta></front>
  <body><sec><title>Methods</title><p>We did the thing.</p></sec></body>
  <back>
    <ref-list>
      <p>Papers of special note have been highlighted as: of interest.</p>
      <ref id="r1"><mixed-citation>Smith J. A paper. Journal. 2020.</mixed-citation>
        <note><p>Faculty Opinions Recommendation</p></note></ref>
    </ref-list>
    <ack><p>Funded by the Example Foundation.</p></ack>
  </back>
</article>"""
        article = JATSParser(data).parse()
        paragraphs = [p for s in article.body_sections for p in s.paragraphs]

        # The <ack> beside the list is the positive control, and it is not
        # decoration: asserting only that the apparatus is absent is satisfied
        # by #224 never having been made, and by a refusal that gives up on
        # every later <back> child once a <ref-list> has been seen. Both pass
        # the bare form of this test; neither passes this one.
        assert paragraphs == ["We did the thing.", "Funded by the Example Foundation."]

    def test_a_nested_reference_list_is_refused_to_its_end(self):
        """The refusal is an ancestor test on ``element_stack``, not a flag.

        JATS lets a ``<ref-list>`` hold another, and ``in_ref_list`` is a bare
        boolean the inner close clears — the shape #115 was — so a flag would
        re-admit the outer list's remaining apparatus. Nothing else here
        depends on that bug being fixed.

        The ``<notes>`` after the outer list is the positive control: without
        it the assertion is *"no back prose reaches the article"*, which is
        also what #224 not having been made looks like.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Nested refs</article-title>
  </title-group></article-meta></front>
  <back>
    <ref-list><title>References</title>
      <ref-list><title>Primary</title>
        <ref id="r1"><mixed-citation>Smith J. 2020.</mixed-citation></ref></ref-list>
      <p>Apparatus after the inner list closed.</p>
    </ref-list>
    <notes><p>Kept after the nested list.</p></notes>
  </back>
</article>"""
        article = JATSParser(data).parse()

        assert [p for s in article.body_sections for p in s.paragraphs] == [
            "Kept after the nested list."
        ]

    def test_furniture_in_back_matter_stays_out_of_the_prose(self):
        """An exhibit is legal in ``<back>``, and its internals reach the same
        branch. A cell's ``<p>`` is already in the rendered table and a
        caption belongs to the figure, so neither may become back prose —
        the ``in_figure``/``in_table_wrap`` test that keeps them apart in
        ``<body>`` is the same one, and this pins that it covers ``<back>``.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Back furniture</article-title>
  </title-group></article-meta></front>
  <back>
    <app-group><app>
      <p>Real appendix prose.</p>
      <fig id="f1"><label>Figure A1</label>
        <caption><p>A caption for the appendix figure.</p></caption>
        <graphic xlink:href="fa1.jpg"/></fig>
    </app></app-group>
  </back>
</article>"""
        article = JATSParser(data).parse()

        assert [p for s in article.body_sections for p in s.paragraphs] == ["Real appendix prose."]
        assert [(f.label, f.caption) for f in article.figures] == [
            ("Figure A1", "A caption for the appendix figure.")
        ]

    def test_an_empty_back_paragraph_opens_no_section(self):
        """Whitespace must not manufacture an untitled section, for the reason
        a whitespace-only ``<body>`` stays body-less.

        The ``<notes>`` is the positive control, and it also pins that the
        blank is *dropped* rather than kept as an empty paragraph: one section
        holding one paragraph is a stronger claim than no section at all, and
        the latter is what #224 unmade looks like.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Blank back</article-title>
  </title-group></article-meta></front>
  <back><ack><p>   </p></ack><notes><p>Real note.</p></notes></back>
</article>"""
        article = JATSParser(data).parse()

        assert [(s.title, s.paragraphs) for s in article.body_sections] == [("", ["Real note."])]

    @pytest.mark.parametrize(
        ("container", "markup", "expected"),
        [
            (
                "ack",
                "<ack><p>Funded by the Example Foundation.</p></ack>",
                "Funded by the Example Foundation.",
            ),
            (
                "fn-group",
                "<fn-group><fn fn-type='COI-statement'>"
                "<p>No competing interests.</p></fn></fn-group>",
                "No competing interests.",
            ),
            (
                "notes",
                "<notes><p>Data are available on request.</p></notes>",
                "Data are available on request.",
            ),
            (
                "glossary",
                "<glossary><def-list><def-item><term>BMI</term>"
                "<def><p>body mass index</p></def></def-item></def-list></glossary>",
                # Reversed by issue #228, which was filed from this very
                # container's population: the term was read and discarded, so
                # this row asserted a definition with no word defined. A
                # session finding the change should read that issue rather
                # than restore the assertion.
                "BMI — body mass index",
            ),
            (
                "app-group",
                "<app-group><app><p>Appendix prose.</p></app></app-group>",
                "Appendix prose.",
            ),
            ("bio", "<bio><p>The author is a clinician.</p></bio>", "The author is a clinician."),
        ],
    )
    def test_every_measured_back_container_reaches_the_article(self, container, markup, expected):
        """The class's own population table, as inputs rather than as prose.

        Six ``<back>`` children were measured and five of them are named in
        the routing's own comment; only four were exercised, and ``<glossary>``
        — the third-largest, whose prose arrives through a
        ``<def-list><def-item><def>`` chain no other test here traverses — was
        in neither list. A table quoted in six files and driven by four cases
        is the shape ``TestTheStatedCountsAreWhatTheCorpusHolds`` exists to
        break one module over.

        ``<app>`` is deliberately the bare form here. The appendix test above
        wraps its prose in a ``<sec>``, which takes the *sectioned* branch and
        so exercises none of this.
        """
        data = f"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Back {container}</article-title>
  </title-group></article-meta></front>
  <body><sec><title>Methods</title><p>We did the thing.</p></sec></body>
  <back>{markup}</back>
</article>""".encode()

        article = JATSParser(data).parse()

        assert [p for s in article.body_sections for p in s.paragraphs] == [
            "We did the thing.",
            expected,
        ]

    def test_a_reviewers_back_matter_is_not_the_articles(self):
        """A ``<sub-article>``'s own ``<back>`` is a nested article's, not this one's.

        Issue #110's whole shape, on the branch #224 widened. PLOS deposits
        each peer-review round as a ``<sub-article>``, and before this change
        the unsectioned branch could not reach a ``<back>`` at all; now it
        can, and a round's acknowledgements and competing-interest statement
        are exactly what sits in one. The string below is not invented: a
        reviewer's *"the reviewers declare no competing interests"* was read
        as the paper's own disclosure one package over.

        Suppression is structural, so this passes today. It is pinned because
        the routing it depends on is the one this issue moved.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Reviewed</article-title>
  </title-group></article-meta></front>
  <body><sec><title>Methods</title><p>We did the thing.</p></sec></body>
  <back><ack><p>The authors thank the funders.</p></ack></back>
  <sub-article article-type="referee-report">
    <front-stub><title-group><article-title>Round 1</article-title></title-group></front-stub>
    <body><p>The manuscript is sound.</p></body>
    <back>
      <ack><p>REVIEWER ACKNOWLEDGEMENT LEAK.</p></ack>
      <fn-group><fn><p>The reviewers declare no competing interests.</p></fn></fn-group>
    </back>
  </sub-article>
</article>"""
        article, html = JATSParser(data).parse_with_html()
        paragraphs = [p for s in article.body_sections for p in s.paragraphs]

        assert paragraphs == ["We did the thing.", "The authors thank the funders."]
        assert article.suppressed_nested_articles == 1
        # The rendered half too, since that is what `FullTextService` caches
        # and so the form in which a leak would become permanent.
        assert "REVIEWER ACKNOWLEDGEMENT LEAK" not in html
        assert "reviewers declare no competing interests" not in html


class TestJATSParserFrontMatterProse:
    """Prose in ``<front>`` is the article's too, and it reached nothing.

    ``_append_prose``'s unsectioned branch admitted ``<body>`` and, since issue
    #224, ``<back>``; everything in ``<front>`` fell past it with no counter
    and no line (issue #230). That is where JAMA deposits *"Funding/Support"*
    and *"Role of the Funder/Sponsor"* as bare ``<author-notes><p>``, where
    ``<fn fn-type="COI-statement">`` lives, and where PLOS puts its data
    availability ``<notes>`` — the material #224 routed when a publisher puts
    it in ``<back>``, so identical markup meant two things depending on which
    end of the article held it.

    **Routed in document order, with no special case**, the user's choice once
    the numbers were in: a slot of its own, flushed at ``</front>`` and ahead of
    any ``<sec>``, so front matter lands ahead of the body in ``body_sections``
    and renders just after the abstract. A ``<trans-abstract>`` follows the
    same path — it is sometimes the only English abstract an article carries.

    **Measured at the drop, with the parser's own predicates**, every run
    checked against a before/after fingerprint of every destination (0
    mismatches), and a ``<p>`` in a table cell excluded since ``characters()``
    files the cell: 9,328 runs in 3,350 of the 8,118 served articles of
    ``PMC10030002_PMC10040000.xml.gz`` (41.3%, 1.08 million characters), and
    114,519 in 46,737 of the 97,909 of
    ``oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26.tar.gz`` (47.7%, 12.1
    million). By owner, served / archive: ``<author-notes>`` 6,280 /
    81,810 (9,865 archive ``COI-statement`` runs in 9,645 articles),
    ``<notes>`` 833 / 13,988, ``<def-list>`` 1,441 / 9,280, ``<funding-group>``
    304 / 5,328, ``<trans-abstract>`` 318 / 3,059, ``<title-group>`` 73 / 538,
    ``<contrib-group>`` 73 / 516, ``<fn-group>`` 6 / 0. Every row is an input
    under test in :meth:`test_every_measured_front_container_reaches_the_article`.

    **A ``<sec>`` in front matter was already filed, and filed empty.** It
    pushes a builder like any other and its close appends it to
    ``body_sections``, while its prose fell past the conjunction that asked for
    ``<body>`` or ``<back>`` — so the rendered article carried a heading with
    nothing under it: 263 served and 3,099 archive, every one titled and every
    one empty (``<trans-abstract>`` 2,276, ``<bio>`` 504, ``<notes>`` 319 in the
    archive). A heading with its content dropped is worse than a blank.

    ``fn-type="edited-by"`` editorial boilerplate alone is 2,443 of the 6,280
    served ``<author-notes>`` runs (38.9%) and 41,431 of the 81,810 archive
    ones (50.6%) — routed all the same: ``fn-type`` is an attribute
    vocabulary, and this module has refused to decide by one everywhere else.
    """

    FRONT_MATTER = b"""<?xml version="1.0"?>
<article>
  <front>
    <article-meta>
      <title-group><article-title>Front matter</article-title></title-group>
      <author-notes>
        <fn fn-type="COI-statement"><p>AB is an employee of Acme Pharma.</p></fn>
        <p>Funding/Support: This study was funded by the Example Foundation.</p>
      </author-notes>
      <abstract><p>We studied a thing.</p></abstract>
      <kwd-group><title>Keywords</title><kwd>alpha</kwd></kwd-group>
    </article-meta>
    <notes><p>Data are available from the corresponding author.</p></notes>
  </front>
  <body><sec><title>Methods</title><p>We did the thing.</p></sec></body>
</article>"""

    def test_the_whole_front_matter_shape_is_one_untitled_section_ahead_of_the_body(self):
        """The position is the decision, so it is pinned as a shape.

        One untitled section, first, holding the prose of every front-matter
        container in document order — across the ``<abstract>`` between them,
        which files elsewhere and flushes nothing. A change that put front
        matter after the body, gave it a heading, or split it per container is
        a change to *this*, and every membership test below passes it.

        **The fixture carries a** ``<kwd-group><title>Keywords</title>`` **for
        that reason** (PR #280's review). Its heading is admitted by issue
        #231's gate and heads nothing this module routes, and while the
        recovery flushed on reading a heading it split this run in two — 70 of
        the 71 served articles the lazy flush changes carry a ``<kwd-group>``
        heading — with this test green, because the fixture held no element
        that did it. The flush is lazy now, and this is the
        test that says so.
        """
        article = JATSParser(self.FRONT_MATTER).parse()

        assert [(s.title, s.paragraphs) for s in article.body_sections] == [
            (
                "",
                [
                    "AB is an employee of Acme Pharma.",
                    "Funding/Support: This study was funded by the Example Foundation.",
                    "Data are available from the corresponding author.",
                ],
            ),
            ("Methods", ["We did the thing."]),
        ]

    def test_front_matter_does_not_reach_the_abstract(self):
        """The ``<abstract>`` sits between two front-matter containers here, and
        keeps only its own prose."""
        article = JATSParser(self.FRONT_MATTER).parse()

        assert [(s.title, s.content) for s in article.abstract_sections] == [
            ("", "We studied a thing.")
        ]

    def test_front_matter_prose_renders_between_the_abstract_and_the_body(self):
        html = JATSParser(self.FRONT_MATTER).to_html()

        abstract = html.index("We studied a thing.")
        statement = html.index("AB is an employee of Acme Pharma.")
        methods = html.index("<h2>Methods</h2>")
        assert abstract < statement < methods

    def test_front_matter_renders_under_the_abstract_heading_until_279_decides(self):
        """Pinned, not endorsed: the markup a fix for issue #279 must change on purpose.

        An untitled section gets no heading (#30), so the front section's
        paragraphs follow the abstract's own under ``<h2>Abstract</h2>`` and
        read as part of it in the HTML ``FullTextService`` caches —
        ``abstract_sections`` itself stays clean. An unsectioned ``<body>``
        already did the same on ``main``; the test-coverage review of issue
        #230 measured the front-matter half. It asserts the exact block between
        the two headings: the ordering test above passes whether or not a
        separator is ever added, and this one does not.

        **Issue #231 was taken and deliberately left this standing**, which is
        why the name moved rather than the assertion. #231's answer is to
        recover the heading the container *deposited*, and front matter almost
        rarely deposits one — ``<author-notes>`` in 25 of the 2,444 served
        blocks carrying prose — and the front element that deposits one most,
        ``<kwd-group>``, heads no routable prose; so the run-on needs a
        rendering answer instead. That is #279. This fixture's front matter is
        the untitled kind, so the block below is what #231 leaves.
        """
        html = JATSParser(self.FRONT_MATTER).to_html()

        start = html.index("<h2>Abstract</h2>")
        end = html.index("<h2>Methods</h2>")
        assert html[start:end].split("\n") == [
            "<h2>Abstract</h2>",
            "<p>We studied a thing.</p>",
            "<p>AB is an employee of Acme Pharma.</p>",
            "<p>Funding/Support: This study was funded by the Example Foundation.</p>",
            "<p>Data are available from the corresponding author.</p>",
            "",
        ]

    def test_front_matter_alone_is_still_not_a_body(self):
        """``has_body`` asks about ``<body>``, and front matter does not answer it.

        It gates caching in ``FullTextService``: a body-less document is held
        back so the tier chain keeps looking. A medRxiv-style document made of
        front matter and no body prose, here carrying author notes, must still
        read as one.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <title-group><article-title>No body</article-title></title-group>
    <author-notes><fn><p>These authors contributed equally.</p></fn></author-notes>
  </article-meta></front>
  <body><p>   </p></body>
</article>"""
        article = JATSParser(data).parse()

        assert article.has_body is False
        assert [p for s in article.body_sections for p in s.paragraphs] == [
            "These authors contributed equally."
        ]

    def test_a_front_matter_section_alone_is_still_not_a_body(self):
        """The sectioned branch counts ``<body>`` paragraphs in its own right,
        so the unsectioned fixture above cannot see it being widened."""
        data = b"""<?xml version="1.0"?>
<article>
  <front>
    <article-meta><title-group><article-title>No body</article-title></title-group></article-meta>
    <notes><sec><title>Data availability</title><p>Data are available.</p></sec></notes>
  </front>
  <body><p>   </p></body>
</article>"""
        article = JATSParser(data).parse()

        assert article.has_body is False
        assert [(s.title, s.paragraphs) for s in article.body_sections] == [
            ("Data availability", ["Data are available."])
        ]

    def test_a_front_matter_section_keeps_its_own_prose(self):
        """The empty heading, filled — and loose prose ahead of it flushes first.

        A ``<sec>`` in ``<front><notes>`` was already appended to
        ``body_sections`` with its title and no paragraphs: 319 archive
        ``<notes>`` sections, in 316 articles, rendered a heading with nothing
        under it.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front>
    <article-meta>
      <title-group><article-title>Front sec</article-title></title-group>
      <author-notes><fn><p>Loose note before the section.</p></fn></author-notes>
    </article-meta>
    <notes><sec><title>Data availability</title>
      <p>Data are deposited in the Example Archive.</p></sec></notes>
  </front>
  <body><sec><title>Methods</title><p>We did the thing.</p></sec></body>
</article>"""
        article = JATSParser(data).parse()

        assert [(s.title, s.paragraphs) for s in article.body_sections] == [
            ("", ["Loose note before the section."]),
            ("Data availability", ["Data are deposited in the Example Archive."]),
            ("Methods", ["We did the thing."]),
        ]

    def test_a_translated_abstract_is_routed_like_other_front_matter(self):
        """No special case: its sections stop being empty headings, and it does
        not join ``abstract_sections``.

        2,276 archive ``<trans-abstract>`` sections were each a heading with
        nothing under it. It is sometimes the English version of a
        non-English abstract, so it is kept rather than refused as a
        duplicate, and it is kept out of ``abstract_sections`` because nothing
        there says which language an entry is in.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <title-group><article-title>Translated</article-title></title-group>
    <abstract><p>We studied a thing.</p></abstract>
    <trans-abstract xml:lang="fr"><title>Resume</title>
      <sec><title>Objectif</title><p>Nous avons etudie une chose.</p></sec>
    </trans-abstract>
  </article-meta></front>
  <body><sec><title>Methods</title><p>We did the thing.</p></sec></body>
</article>"""
        article = JATSParser(data).parse()

        assert [(s.title, s.content) for s in article.abstract_sections] == [
            ("", "We studied a thing.")
        ]
        assert [(s.title, s.paragraphs) for s in article.body_sections] == [
            ("Objectif", ["Nous avons etudie une chose."]),
            ("Methods", ["We did the thing."]),
        ]

    def test_a_front_matter_definition_carries_its_term(self, parser_log):
        """The measured population ``definition_terms_dropped`` was sized by.

        #228's term fold spends a term only on prose that is accounted for, and
        front matter was the position where the definition reached nothing:
        1,441 of the 1,444 served terms that counter reported. Routed, the
        term folds and the counter has nothing to say.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front>
    <article-meta><title-group><article-title>Front defs</article-title>
    </title-group></article-meta>
    <notes><def-list>
      <def-item><term>BMI</term><def><p>body mass index</p></def></def-item>
    </def-list></notes>
  </front>
  <body><sec><title>M</title><p>Body.</p></sec></body>
</article>"""
        article = JATSParser(data).parse()

        assert [p for s in article.body_sections for p in s.paragraphs] == [
            "BMI — body mass index",
            "Body.",
        ]
        assert not [m for m in parser_log.messages(logging.WARNING) if "term(s)" in m]

    def test_a_front_matter_formula_reaches_its_section(self, parser_log):
        """``formulas_dropped`` asks the same predicate, so it follows the routing."""
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Front formula</article-title>
  </title-group>
  <notes><sec><title>Model</title>
    <disp-formula><tex-math>\\begin{document}$$s = 1$$\\end{document}</tex-math>
    </disp-formula></sec></notes>
  </article-meta></front>
  <body><sec><title>M</title><p>Body.</p></sec></body>
</article>"""
        article = JATSParser(data).parse()

        assert [(s.title, s.paragraphs) for s in article.body_sections] == [
            ("Model", ["$$s = 1$$"]),
            ("M", ["Body."]),
        ]
        assert not [m for m in parser_log.messages(logging.WARNING) if "formula(s)" in m]

    def test_a_front_matter_licence_paragraph_is_still_declined(self):
        """The object-metadata refusal becomes load-bearing here.

        All 19 archive ``<license><p>`` sit in ``<article-meta>``, where they
        fell past every branch whatever the refusal said (issues #241, #248).
        Routing front matter would file an article's licence among its
        front-matter paragraphs without it.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <title-group><article-title>Licensed</article-title></title-group>
    <permissions><license><p>Licensed CC-BY.</p></license></permissions>
    <author-notes><fn><p>A real note.</p></fn></author-notes>
  </article-meta></front>
  <body><sec><title>M</title><p>Body.</p></sec></body>
</article>"""
        article = JATSParser(data).parse()

        assert [p for s in article.body_sections for p in s.paragraphs] == ["A real note.", "Body."]

    def test_a_front_matter_reference_list_keeps_its_apparatus_out(self, parser_log):
        """The ``<ref-list>`` refusal applies in ``<front>`` too.

        ``<front>`` admits ``<notes>`` and ``<notes>`` admits ``<ref-list>``,
        so the bibliography apparatus #224 refuses in ``<back>`` can arrive
        here — and routing front matter without the refusal filed *"Faculty
        Opinions Recommendation"* as an article paragraph. A first cut said
        JATS admits no bibliography in ``<front>``, which is false (PR review).
        0 of the 8,118 served and 0 of the 97,909 archive articles carry one,
        so this pins a direction.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front>
    <article-meta><title-group><article-title>Front refs</article-title>
    </title-group></article-meta>
    <notes><ref-list>
      <p>Papers of special note have been highlighted.</p>
      <ref id="r1"><mixed-citation>Smith J. A paper. 2020.</mixed-citation>
        <note><p>Faculty Opinions Recommendation</p></note></ref>
    </ref-list>
    <p>Data are available on request.</p></notes>
  </front>
  <body><sec><title>M</title><p>Body.</p></sec></body>
</article>"""
        article = JATSParser(data).parse()

        # The note beside the list is the positive control: a refusal that
        # gave up on all front matter would pass the absence assertion alone.
        assert [(s.title, s.paragraphs) for s in article.body_sections] == [
            ("", ["Data are available on request."]),
            ("M", ["Body."]),
        ]
        warnings = parser_log.messages(logging.WARNING)
        assert any("2 <ref-list> item(s) were refused" in m for m in warnings), warnings

    def test_front_body_and_back_are_three_sections_in_document_order(self):
        """A slot per container, one container further out."""
        data = b"""<?xml version="1.0"?>
<article>
  <front>
    <article-meta><title-group><article-title>Three</article-title></title-group></article-meta>
    <notes><p>Loose front prose.</p></notes>
  </front>
  <body><p>Loose body prose.</p></body>
  <back><ack><p>Loose back prose.</p></ack></back>
</article>"""
        article = JATSParser(data).parse()

        assert [(s.title, s.paragraphs) for s in article.body_sections] == [
            ("", ["Loose front prose."]),
            ("", ["Loose body prose."]),
            ("", ["Loose back prose."]),
        ]

    def test_loose_front_prose_between_and_after_front_sections_keeps_its_place(self):
        """Document order inside front matter, on a well-formed document.

        Every other fixture here puts loose front prose only *ahead* of a
        front ``<sec>``, so a flush that put the front slot at the head of
        ``body_sections`` — a plausible reading of "front matter goes ahead of
        the body" — or one skipped when a later front ``<sec>`` opens survived
        everything but the DTD-invalid nesting tests, whose own docstrings
        say document order is not what they protect (PR #256's review).
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front>
    <article-meta>
      <title-group><article-title>Interleaved</article-title></title-group>
      <author-notes><fn><p>Before.</p></fn></author-notes>
    </article-meta>
    <notes>
      <sec><title>Data availability</title><p>Deposited.</p></sec>
      <p>Between.</p>
      <sec><title>Competing interests</title><p>None.</p></sec>
      <p>After.</p>
    </notes>
  </front>
  <body><sec><title>Methods</title><p>We did the thing.</p></sec></body>
</article>"""
        article = JATSParser(data).parse()

        assert [(s.title, s.paragraphs) for s in article.body_sections] == [
            ("", ["Before."]),
            ("Data availability", ["Deposited."]),
            ("", ["Between."]),
            ("Competing interests", ["None."]),
            ("", ["After."]),
            ("Methods", ["We did the thing."]),
        ]

    @pytest.mark.parametrize(
        ("title_group_extra", "article_meta", "front", "expected"),
        [
            pytest.param(
                "",
                "<author-notes><fn fn-type='COI-statement'>"
                "<p>No competing interests.</p></fn></author-notes>",
                "",
                "No competing interests.",
                id="author-notes/fn",
            ),
            pytest.param(
                "",
                "<author-notes><p>Role of the Funder/Sponsor: none.</p></author-notes>",
                "",
                "Role of the Funder/Sponsor: none.",
                id="author-notes/p",
            ),
            pytest.param(
                "",
                "",
                "<notes><p>Data are available on request.</p></notes>",
                "Data are available on request.",
                id="notes",
            ),
            pytest.param(
                "",
                "",
                "<def-list><def-item><term>BMI</term>"
                "<def><p>body mass index</p></def></def-item></def-list>",
                "BMI — body mass index",
                id="def-list",
            ),
            pytest.param(
                "",
                "<funding-group><open-access><p>Open Access funding enabled.</p>"
                "</open-access></funding-group>",
                "",
                "Open Access funding enabled.",
                id="funding-group",
            ),
            pytest.param(
                "",
                "<trans-abstract xml:lang='de'><p>Wir untersuchten etwas.</p></trans-abstract>",
                "",
                "Wir untersuchten etwas.",
                id="trans-abstract",
            ),
            pytest.param(
                "<fn-group><fn><p>Electronic supplementary information available.</p>"
                "</fn></fn-group>",
                "",
                "",
                "Electronic supplementary information available.",
                id="title-group",
            ),
            pytest.param(
                "",
                "<contrib-group><contrib contrib-type='author'>"
                "<name><surname>Smith</surname><given-names>J</given-names></name>"
                "<bio><p>The author is a clinician.</p></bio></contrib></contrib-group>",
                "",
                "The author is a clinician.",
                id="contrib-group",
            ),
            pytest.param(
                "",
                "",
                "<fn-group><fn><p>These authors contributed equally.</p></fn></fn-group>",
                "These authors contributed equally.",
                id="fn-group",
            ),
        ],
    )
    def test_every_measured_front_container_reaches_the_article(
        self, title_group_extra, article_meta, front, expected
    ):
        """The class's population table, as inputs rather than as prose.

        ``<title-group>`` is the one row whose markup lives in the title group
        itself, so its prose arrives through ``title_group_extra``.
        """
        title_group = (
            f"<title-group><article-title>Front</article-title>{title_group_extra}</title-group>"
        )
        data = f"""<?xml version="1.0"?>
<article>
  <front><article-meta>{title_group}{article_meta}</article-meta>{front}</front>
  <body><sec><title>Methods</title><p>We did the thing.</p></sec></body>
</article>""".encode()

        article = JATSParser(data).parse()

        assert article.title == "Front"
        assert [p for s in article.body_sections for p in s.paragraphs] == [
            expected,
            "We did the thing.",
        ]


class TestAContainersOwnHeadingReachesItsSection:
    """A heading the publisher deposited is recovered, not invented (issue #231).

    Issue #224 routed unsectioned ``<back>`` prose and issue #230 ``<front>``
    prose, and both arrived as **one untitled section per container**: an
    ``<ack>``, an ``<fn-group>`` and a ``<glossary>`` concatenated with no
    heading between them, in the HTML ``FullTextService`` caches — back
    matter after the body, front matter straight after the abstract — where a
    reader cannot tell them from each other or, in front, from the abstract.
    The container's own ``<title>`` was dropped by the ``<title>`` owner rule
    (#125, #130) — rightly, since an ``<ack>`` is not a ``<sec>`` and nothing
    must let it *rename* an enclosing section — and nothing put it anywhere
    else.

    **The rule is that a recovered heading owns its section, and the section
    ends when prose arrives under a different heading** (the flush is lazy,
    see below). So it is not an enumeration
    of container elements, which is the thing #116's and #125's rules are both
    about being unable to complete by inspection; the document says where a
    block begins by heading it. An element depositing no heading opens no
    section of its own, which is what leaves loose ``<body>`` prose — two bare
    ``<p>`` children of ``<body>`` — as the single untitled section it has
    always been.

    **It invents nothing.** Issues #116 and #162 both refused to *derive* a
    value — a footnote marker from a position, a figure number from an index —
    and this is their opposite: the publisher wrote
    ``<title>Acknowledgements</title>`` and bmlib was throwing it away. What
    an element deposits no heading for still gets none.

    **Measured on both named artifacts**, by an instrumented handler recording
    every run that takes ``_append_prose``'s unsectioned branch on ``main``,
    joined per article by occurrence index to a walk of the same bytes asking
    whether that run's **block** — the direct child of ``<body>``/``<back>``/
    ``<front>``, ``<article-meta>`` being a wrapper — deposits a ``<title>``,
    each block counted once. Served (``PMC10030002_PMC10040000.xml.gz``, 8,118
    articles) / archive
    (``oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26.tar.gz``, 97,909):
    ``<back>`` **11,857 of 17,384 blocks (68.2%)** / 220,491 of 298,656
    (73.8%) deposit a heading, ``<front>`` 475 of 3,743 (12.7%) / 7,196 of
    53,722 (13.4%). ``<body>``'s unsectioned prose is mostly bare ``<p>`` with
    no block to head it; the blocks there that head themselves are
    ``<def-list>`` elements, 5 served and 967 archive. The first round pooled all
    three into one share over a denominator counting a pseudo-block per run of
    loose ``<body>`` prose, which PR #280's review could not re-derive; these
    are per container and say their unit. The headings recovered lead with
    *Acknowledgements*, *Competing interests*, *Funding*, *Acknowledgments*,
    *Author contributions*, *Data availability* and *Abbreviations* on the
    served artifact: the disclosures a reader most needs told apart.

    **Front matter is the half this does not reach**, and the numbers say so
    rather than the prose: ``<author-notes>`` deposits a heading in 25 of
    2,444 served blocks, and the front element depositing one most often —
    ``<kwd-group>``, *Keywords* — heads no routable prose at all. So front
    prose still follows the abstract's paragraphs under ``<h2>Abstract</h2>``
    with no heading of its own, in 2,899 served articles: issue #279, which
    wants a rendering answer rather than this one.

    **The flush is lazy** since PR #280's review: a heading's frame does
    nothing until prose is routed under it, and a section ends when prose
    arrives under a *different* frame. The first design flushed on reading a
    heading, which cut untitled runs around every heading that titled nothing
    — 71 served articles, 0 of them visible in the HTML — and the tests below
    that pinned it are reversed rather than deleted.
    """

    BACK_MATTER = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Back matter</article-title>
  </title-group></article-meta></front>
  <body><sec><title>Methods</title><p>We did the thing.</p></sec></body>
  <back>
    <ack><title>Acknowledgements</title>
      <p>This work was funded by grant XYZ from the Example Foundation.</p></ack>
    <fn-group><fn fn-type="COI-statement">
      <p>The authors declare no competing interests.</p></fn></fn-group>
    <notes><title>Data availability</title>
      <p>Data are available from the corresponding author.</p></notes>
  </back>
</article>"""

    def test_a_recovered_heading_renders_as_a_heading(self):
        """``html_content`` is what ``FullTextService`` caches, so this is the
        half a reader sees."""
        html = JATSParser(self.BACK_MATTER).to_html()

        # Exact, so each heading is shown to precede its own prose. The COI
        # note's untitled section renders no heading of its own, which is
        # #279's rendering question in back matter and is pinned as today's.
        assert html.endswith(
            "<h2>Acknowledgements</h2>\n"
            "<p>This work was funded by grant XYZ from the Example Foundation.</p>\n"
            "<p>The authors declare no competing interests.</p>\n"
            "<h2>Data availability</h2>\n"
            "<p>Data are available from the corresponding author.</p>"
        )

    def test_an_untitled_container_does_not_inherit_the_heading_before_it(self):
        """The boundary is prose arriving under a different frame — here the
        untitled ``<fn-group>``'s, once the ``<ack>``'s has popped.

        Without it the COI note lands under *Acknowledgements*, which is a
        wrong heading where the alternative is none — and it is the failure
        this module refuses everywhere else it has been caught (#116, #162).
        """
        article = JATSParser(self.BACK_MATTER).parse()

        assert [(s.title, s.paragraphs) for s in article.body_sections] == [
            ("Methods", ["We did the thing."]),
            (
                "Acknowledgements",
                ["This work was funded by grant XYZ from the Example Foundation."],
            ),
            ("", ["The authors declare no competing interests."]),
            ("Data availability", ["Data are available from the corresponding author."]),
        ]

    def test_an_untitled_container_before_a_titled_one_flushes_first(self):
        """The other order: prose already pending must not acquire the heading
        that arrives after it."""
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Order</article-title>
  </title-group></article-meta></front>
  <back>
    <fn-group><fn><p>Untitled note.</p></fn></fn-group>
    <ack><title>Acknowledgements</title><p>Thanks.</p></ack>
  </back>
</article>"""
        article = JATSParser(data).parse()

        assert [(s.title, s.paragraphs) for s in article.body_sections] == [
            ("", ["Untitled note."]),
            ("Acknowledgements", ["Thanks."]),
        ]

    def test_two_appendices_keep_their_own_headings(self):
        """The rule is not "a direct child of ``<back>``", and this is why.

        Both ``<app>`` elements sit inside one ``<app-group>``, which deposits
        no heading of its own — none of the 95 served ones carrying prose do.
        Keyed on the
        container's direct child they would share one untitled section;
        keyed on the deposited heading each keeps the name the publisher gave
        it.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Apps</article-title>
  </title-group></article-meta></front>
  <back><app-group>
    <app><title>Appendix A</title><p>First appendix.</p></app>
    <app><title>Appendix B</title><p>Second appendix.</p></app>
  </app-group></back>
</article>"""
        article = JATSParser(data).parse()

        assert [(s.title, s.paragraphs) for s in article.body_sections] == [
            ("Appendix A", ["First appendix."]),
            ("Appendix B", ["Second appendix."]),
        ]

    def test_a_reference_lists_heading_is_not_recovered(self):
        """``<ref-list>``'s prose is refused as bibliography apparatus
        (#224), so its heading must not title a section either.

        Recovering it would put a *References* heading on whatever prose
        happened to follow — the apparatus itself never reaching the article —
        which is the misfiling that refusal exists to prevent, arriving
        through the heading instead of through the prose.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Refs</article-title>
  </title-group></article-meta></front>
  <back>
    <ref-list><title>References</title>
      <p>Papers of special note have been highlighted.</p></ref-list>
    <ack><p>Thanks.</p></ack>
  </back>
</article>"""
        article = JATSParser(data).parse()

        assert [(s.title, s.paragraphs) for s in article.body_sections] == [("", ["Thanks."])]

    def test_a_sectioned_containers_heading_is_still_dropped(self):
        """The scope boundary, and it is issue #240's population on the other
        side of it.

        With a ``<sec>`` open the prose reaches that section, not an implicit
        one, and an ``<fn-group>``'s heading there must still not rename it
        (#125). #231 is scoped to *unsectioned* matter, so this parse is
        unchanged — asserted rather than assumed. Under the lazy flush the
        gate's ``section_stack`` term is a recorded equivalent here (the prose
        goes to the ``<sec>`` and the frame titles nothing), so this pins the
        behaviour and not that term.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Sectioned</article-title>
  </title-group></article-meta></front>
  <body><sec><title>Additional information</title>
    <fn-group><title>Competing interests</title><fn><p>None.</p></fn></fn-group>
  </sec></body>
</article>"""
        article = JATSParser(data).parse()

        assert [(s.title, s.paragraphs) for s in article.body_sections] == [
            ("Additional information", ["None."])
        ]

    def test_loose_body_prose_is_still_one_untitled_section(self):
        """Nothing deposits a heading here, so nothing changes.

        This is what the rule buys by keying on the heading rather than on
        the container's children: a bare ``<p>`` *is* a direct child of
        ``<body>``, so a per-child boundary would make one section per
        paragraph.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Loose</article-title>
  </title-group></article-meta></front>
  <body><p>One.</p><p>Two.</p></body>
</article>"""
        article = JATSParser(data).parse()

        assert [(s.title, s.paragraphs) for s in article.body_sections] == [("", ["One.", "Two."])]

    def test_a_heading_whose_element_deposits_no_prose_titles_nothing(self):
        """The pending heading is owned by its element and dies with it.

        Left pending it would title the next container's prose — the
        inherited-heading failure one step removed, and the shape a single
        slot produces where a stack does not.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Empty</article-title>
  </title-group></article-meta></front>
  <back>
    <ack><title>Acknowledgements</title></ack>
    <fn-group><fn><p>A note that is not an acknowledgement.</p></fn></fn-group>
  </back>
</article>"""
        article = JATSParser(data).parse()

        assert [(s.title, s.paragraphs) for s in article.body_sections] == [
            ("", ["A note that is not an acknowledgement."])
        ]

    def test_a_heading_that_titles_nothing_ends_nothing(self):
        """Reversed, not deleted: this pinned the opposite until PR #280's review.

        It asserted two untitled sections here — a container that deposits a
        heading and no routable prose still cut the run it sat in — and called
        that the behaviour to want. The review measured what it cost: the same
        rule split a front-matter run around a ``<kwd-group>`` carrying
        *Keywords*, and a boundary between two untitled sections renders
        nothing, so ``body_sections`` moved where no reader could see a reason.
        Diffed per article, eager against lazy, over the 8,118 served articles:
        71 articles, 71 boundaries removed, **0** moving the HTML. The flush is
        lazy now: a section ends when prose arrives under a *different* heading,
        so a heading that titles nothing ends nothing and the two runs stay
        the one section they were before #231.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Between</article-title>
  </title-group></article-meta></front>
  <back>
    <fn-group><fn><p>First note.</p></fn></fn-group>
    <ack><title>Acknowledgements</title></ack>
    <notes><p>Second note.</p></notes>
  </back>
</article>"""
        article = JATSParser(data).parse()

        assert [(s.title, s.paragraphs) for s in article.body_sections] == [
            ("", ["First note.", "Second note."]),
        ]

    def test_a_keyword_groups_heading_does_not_split_front_matter(self):
        """The shape that made the flush lazy, and the commonest one.

        ``<kwd-group><title>Keywords</title>`` is admitted — it sits in
        ``<front>`` with no section open — and heads nothing this module
        routes, keywords being modelled nowhere. Flushing on *reading* a
        heading cut the front-matter run around it in two. Diffed per article
        over the 8,118 served articles of ``PMC10030002_PMC10040000.xml.gz``,
        the lazy flush changes ``body_sections`` in 71 — 70 of them carrying a
        ``<kwd-group>`` heading — and the HTML in none, the boundary it removes
        being one between two untitled sections. (A review figure of 382
        articles was quoted here first; it does not reproduce, and 71 is the
        diff.) Under the lazy flush the run is one section, as on ``main``.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front>
    <article-meta>
      <title-group><article-title>Keyworded</article-title></title-group>
      <author-notes><p>Funding: NIH.</p></author-notes>
      <abstract><p>Abstract text.</p></abstract>
      <kwd-group><title>Keywords</title><kwd>alpha</kwd></kwd-group>
    </article-meta>
    <notes><p>Data availability statement.</p></notes>
  </front>
  <body><sec><title>Methods</title><p>We did the thing.</p></sec></body>
</article>"""
        article = JATSParser(data).parse()

        assert [(s.title, s.paragraphs) for s in article.body_sections] == [
            ("", ["Funding: NIH.", "Data availability statement."]),
            ("Methods", ["We did the thing."]),
        ]

    def test_a_nested_elements_own_heading_does_not_repeat_the_containers(self):
        """The other shape the eager flush got wrong, and it would be visible.

        A ``<supplementary-material>`` depositing a heading and no prose, inside
        an ``<ack>`` with prose either side, cut the acknowledgement in two —
        and both halves took *Acknowledgements*, so the cached HTML carried the
        heading twice with nothing between them. The frame is compared by
        identity, and the ``<ack>``'s frame is innermost again once the inner
        one pops, so the second run joins the first.

        **Measured 0 on both artifacts**, so this pins a direction (the one
        archive article a markup walk flags, PMC12176339, parses identically
        under both designs). The
        first draft of this docstring attributed the review's "9 articles, 10
        pairs" of newly adjacent duplicate headings to this shape; the eager
        and lazy designs give *identical* adjacent-duplicate counts over all
        8,118 served articles, so none of those pairs is this shape — they are
        sibling containers each depositing the same heading, which both designs
        keep apart on purpose
        (``test_two_containers_depositing_one_heading_stay_two_sections``).
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Once</article-title>
  </title-group></article-meta></front>
  <back><ack><title>Acknowledgements</title><p>Thanks to X.</p>
    <supplementary-material><title>Data S1</title></supplementary-material>
    <p>And to Y.</p></ack></back>
</article>"""
        article = JATSParser(data).parse()

        assert [(s.title, s.paragraphs) for s in article.body_sections] == [
            ("Acknowledgements", ["Thanks to X.", "And to Y."]),
        ]
        assert JATSParser(data).to_html().count("<h2>Acknowledgements</h2>") == 1

    def test_two_containers_depositing_one_heading_stay_two_sections(self):
        """Identity, not value: what a string comparison would have merged.

        Two sibling ``<notes>`` each headed *Notes* are two deposited blocks.
        Compared by value, the second's prose would find the same title
        innermost and join the first section; compared by identity it finds a
        different frame and opens its own. The comparison is written ``is`` and
        ``_HeadingFrame`` is ``eq=False``, so the natural ``==`` would be right
        too — two independent protections, each alone an equivalent mutant,
        and this test is what kills a mutant breaking both.

        **It is a real population, and a visible one**: adjacent sections
        carrying the same heading rise from 13 to 22 served articles (21 to 31
        pairs) against ``main`` — *Author Contributions*, *Author Present
        Address*, *Data availability statement* — and **all ten new pairs are
        this shape**: classified on the branch, every one is two sibling
        ``<notes>`` at the same depth, and the other 21 are the ``<sec>`` pairs
        ``main`` already had (PR #280's review). The HTML prints the heading twice because the
        document deposited it twice, over two blocks; merging them would claim
        one block where the publisher wrote two.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Twice</article-title>
  </title-group></article-meta></front>
  <back>
    <notes><title>Notes</title><p>First block.</p></notes>
    <notes><title>Notes</title><p>Second block.</p></notes>
  </back>
</article>"""
        article = JATSParser(data).parse()

        assert [(s.title, s.paragraphs) for s in article.body_sections] == [
            ("Notes", ["First block."]),
            ("Notes", ["Second block."]),
        ]

    def test_an_unsectioned_bodys_container_heading_titles_its_section(self):
        """The ``<body>`` slot, which nothing pinned (PR #280's review).

        The three slots each opened their own section, and the ``<body>``
        copy could stop taking the heading with the whole suite green while
        its two siblings were pinned. They share one helper now, and this is
        the population the ``<body>`` row measures: an unsectioned body whose
        ``<def-list>`` deposits its own heading.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Glossed</article-title>
  </title-group></article-meta></front>
  <body><p>The trial ran from 2019.</p>
    <def-list><title>Abbreviations</title>
      <def-item><term>BMI</term><def><p>body mass index</p></def></def-item></def-list>
    <p>Follow-up was complete.</p></body>
</article>"""
        article = JATSParser(data).parse()

        assert [(s.title, s.paragraphs) for s in article.body_sections] == [
            ("", ["The trial ran from 2019."]),
            ("Abbreviations", ["BMI — body mass index"]),
            ("", ["Follow-up was complete."]),
        ]
        assert article.has_body is True
        assert "<h2>Abbreviations</h2>" in JATSParser(data).to_html()

    def test_an_abstracts_own_heading_never_titles_front_matter(self, parser_log):
        """The one position where this recovery could produce a wrong value.

        ``in_abstract`` is one boolean over possibly-nested ``<abstract>``
        elements, so an ``<abstract>`` inside a figure within the article's own
        abstract (#249's shape) clears it while the outer one is still open.
        The abstract's remaining prose then falls to the front implicit section
        — pre-existing, and untitled — and a gate reading the flag admitted the
        abstract's next heading over it, putting *Conclusions* on abstract
        prose in the cached HTML (PR #280's review). The gate reads the element
        stack, which cannot go stale; measured 0 of 8,118 served and 0 of
        97,909 archive articles, so this pins a direction.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Stale</article-title></title-group>
    <abstract>
      <p>Real abstract A.</p>
      <fig id="f1"><abstract><p>graphical</p></abstract></fig>
      <title>Conclusions</title><p>Real abstract B.</p>
    </abstract>
  </article-meta></front>
  <body><sec><title>Methods</title><p>We did the thing.</p></sec></body>
</article>"""
        article = JATSParser(data).parse()

        # Exact rather than "Conclusions not in": a regression giving the
        # prose a *different* wrong heading, or losing it, must fail too.
        # "Real abstract A." is erased by the stale flag itself, which is
        # #249 and is not this test's subject.
        assert [(s.title, s.paragraphs) for s in article.body_sections] == [
            ("", ["Real abstract B."]),
            ("Methods", ["We did the thing."]),
        ]
        assert "<h2>Conclusions</h2>" not in JATSParser(data).to_html()

    def test_a_front_containers_heading_renders_ahead_of_the_body(self):
        """``html_content`` for a *front* recovery, which nothing asserted.

        Every HTML test of this class used back matter. A front container's
        heading is where the recovery meets #279's run-on under
        ``<h2>Abstract</h2>``: where the publisher did deposit one, the prose
        after the abstract is headed and no longer reads as abstract text.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front>
    <article-meta><title-group><article-title>Front</article-title></title-group>
      <abstract><p>We studied a thing.</p></abstract>
    </article-meta>
    <notes><title>Data availability</title><p>Data are available.</p></notes>
  </front>
  <body><sec><title>Methods</title><p>We did the thing.</p></sec></body>
</article>"""
        html = JATSParser(data).to_html()

        start = html.index("<h2>Abstract</h2>")
        end = html.index("<h2>Methods</h2>")
        assert html[start:end].split("\n") == [
            "<h2>Abstract</h2>",
            "<p>We studied a thing.</p>",
            "<h2>Data availability</h2>",
            "<p>Data are available.</p>",
            "",
        ]

    def test_a_front_containers_heading_titles_its_own_section(self):
        """Front matter takes the same rule — 12.7% of its served blocks carrying
        prose deposit a heading, but the same markup must not mean two things by
        position, which
        is the argument #224 and #230 both turn on."""
        data = b"""<?xml version="1.0"?>
<article>
  <front>
    <article-meta><title-group><article-title>Front</article-title></title-group>
      <abstract><p>We studied a thing.</p></abstract>
    </article-meta>
    <notes><title>Data availability</title><p>Data are available.</p></notes>
  </front>
  <body><sec><title>Methods</title><p>We did the thing.</p></sec></body>
</article>"""
        article = JATSParser(data).parse()

        assert [(s.title, s.paragraphs) for s in article.body_sections] == [
            ("Data availability", ["Data are available."]),
            ("Methods", ["We did the thing."]),
        ]

    def test_an_exhibit_footnote_blocks_heading_is_still_counted_not_recovered(self):
        """#238's arm keeps its population, in the one position both could reach.

        A ``<table-wrap-foot>``'s or exhibit ``<fn-group>``'s heading is
        refused by the owner rule and counted; it is not an unsectioned
        container's, and recovering it would title a section with a heading
        belonging to a table. The fixture is **unsectioned back matter**, which
        is what makes it a test of the ordering: with a ``<sec>`` open the
        recovery's own ``section_stack`` term refuses first and #238's arm is
        never contested (the first cut of this fixture was sectioned and so
        tested #238 alone, PR #280's review). Here the ``<notes>`` is a
        container #231 recovers headings for.

        **Two independent protections, and this test kills only their joint
        mutant**: #238's arm takes the exhibit's heading before this gate is
        asked, and the gate's own float term refuses it anyway. Moving the gate
        ahead of #238's arm survives the whole suite, as does deleting the
        float term; doing both is killed here alone (PR #280's second review)
        — the ``eq=False`` / ``is`` shape on ``_HeadingFrame``.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Exhibit</article-title>
  </title-group></article-meta></front>
  <back><notes><p>Loose note.</p>
    <table-wrap id="t1"><label>Table 1</label>
      <table><tbody><tr><td>12.3</td></tr></tbody></table>
      <table-wrap-foot><fn-group><title>Notes</title>
        <fn><p>Adjusted for age.</p></fn></fn-group></table-wrap-foot>
    </table-wrap>
    <p>Another loose note.</p></notes></back>
</article>"""
        handler = JATSParser(data)._run_parser()
        article = JATSParser(data).parse()

        assert handler.footnote_headings_dropped == 1
        assert [(s.title, s.paragraphs) for s in article.body_sections] == [
            ("", ["Loose note.", "Another loose note."])
        ]
        assert article.tables[0].footnotes == ["Adjusted for age."]

    def test_an_enclosing_heading_resumes_after_a_nested_section(self):
        """The heading is a stack, and a ``<sec>`` inside the container does not
        end it.

        A ``<sec>`` flushes the pending implicit section on its way in, so the
        prose after it opens a new one — which must carry the heading the
        container is still under, not none. Both runs really are under
        *Acknowledgements*; the repetition is what the document says.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Resume</article-title>
  </title-group></article-meta></front>
  <back><ack><title>Acknowledgements</title><p>Thanks.</p>
    <sec><title>Sub</title><p>Sub prose.</p></sec>
    <p>After the section.</p>
  </ack></back>
</article>"""
        article = JATSParser(data).parse()

        assert [(s.title, s.paragraphs) for s in article.body_sections] == [
            ("Acknowledgements", ["Thanks."]),
            ("Sub", ["Sub prose."]),
            ("Acknowledgements", ["After the section."]),
        ]

    def test_a_nested_heading_does_not_outlive_its_own_element(self):
        """The inner heading titles only what its element holds, and the outer
        one resumes — :class:`_HeadingFrame`'s reason for being a stack.

        Held as a single slot the ``</def-list>`` would clear the glossary's
        heading, and the prose still to come under it would arrive untitled.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Nest</article-title>
  </title-group></article-meta></front>
  <back><glossary><title>Abbreviations</title><p>Lead prose.</p>
    <def-list><title>Set one</title>
      <def-item><term>BMI</term><def><p>body mass index</p></def></def-item></def-list>
    <p>Tail prose.</p>
  </glossary></back>
</article>"""
        article = JATSParser(data).parse()

        assert [(s.title, s.paragraphs) for s in article.body_sections] == [
            ("Abbreviations", ["Lead prose."]),
            ("Set one", ["BMI — body mass index"]),
            ("Abbreviations", ["Tail prose."]),
        ]

    def test_a_nested_articles_heading_never_titles_the_hosts_prose(self):
        """A review round's ``<ack>`` heading is not this article's.

        The push sits behind ``endElement``'s suppression guard and the pop
        behind the same one, so the two stay balanced across the region. Left
        unbalanced, *Reviewer thanks* would title the host article's next run
        of unsectioned prose — a reviewer's heading over the paper's own
        competing-interest note, which is #110's failure through a new field.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Host</article-title>
  </title-group></article-meta></front>
  <body><sec><title>Methods</title><p>Host prose.</p></sec></body>
  <back>
    <sub-article><back><ack><title>Reviewer thanks</title>
      <p>Reviewer prose.</p></ack></back></sub-article>
    <fn-group><fn><p>Host note.</p></fn></fn-group>
  </back>
</article>"""
        article = JATSParser(data).parse()

        assert [(s.title, s.paragraphs) for s in article.body_sections] == [
            ("Methods", ["Host prose."]),
            ("", ["Host note."]),
        ]

    def test_a_back_level_heading_covers_what_no_container_heads(self):
        """``<back>`` may carry a heading of its own, and it is valid markup.

        The JATS 1.3 Tag Library lists ``<back>`` among the elements a
        ``<title>`` may be contained in, and ``<body>`` and ``<front>`` among
        those it may not — so this is the one container whose *own* heading is
        reachable, and the one whose arm both flushes and clears its flag. The
        inner ``<ack>`` heading still wins for the prose its own element holds;
        what the back-level heading covers is the run no container heads.

        It does **not** pin the pop running *after* the name-keyed arms, and
        nothing does: the ``</back>`` arm flushes with the heading already on
        the builder, so moving the pop to the top of ``endElement`` survives the
        whole suite (PR #280's second review). The code records that order as
        deciding nothing today and staying right if an arm is added.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Backish</article-title>
  </title-group></article-meta></front>
  <back><title>Back Matter</title>
    <ack><title>Acknowledgements</title><p>Thanks.</p></ack>
    <fn-group><fn><p>No competing interests.</p></fn></fn-group>
  </back>
</article>"""
        handler = JATSParser(data)._run_parser()

        assert [(s.title, s.paragraphs) for s in handler.body_sections] == [
            ("Acknowledgements", ["Thanks."]),
            ("Back Matter", ["No competing interests."]),
        ]
        assert handler.heading_stack == []

    def test_an_empty_heading_is_not_recovered(self):
        """An empty ``<title/>`` deposits nothing, so nothing is recovered —
        every sibling counter and slot in this module takes the same line.

        This pins the behaviour, not the guard: with a single untitled run an
        empty frame is invisible, so removing ``_recover_container_heading``'s
        ``if not title`` survives here and is killed by
        ``test_an_empty_heading_does_not_end_the_pending_section``.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Empty</article-title>
  </title-group></article-meta></front>
  <back><ack><title>   </title><p>Thanks.</p></ack></back>
</article>"""
        article = JATSParser(data).parse()

        assert [(s.title, s.paragraphs) for s in article.body_sections] == [("", ["Thanks."])]

    def test_a_translated_abstracts_heading_titles_its_own_section(self):
        """``<trans-abstract>`` routes as front-matter prose (#230) rather than
        into ``abstract_sections``, so its own heading is a container's and is
        recovered like any other — 32 of the 47 served blocks carrying prose
        deposit one (45 of 84 counted as elements)."""
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Trans</article-title></title-group>
    <abstract><p>English abstract.</p></abstract>
    <trans-abstract xml:lang="fr"><title>Resume</title><p>Resume francais.</p></trans-abstract>
  </article-meta></front>
  <body><sec><title>Methods</title><p>We did the thing.</p></sec></body>
</article>"""
        article = JATSParser(data).parse()

        assert [(s.title, s.content) for s in article.abstract_sections] == [
            ("", "English abstract.")
        ]
        assert [(s.title, s.paragraphs) for s in article.body_sections] == [
            ("Resume", ["Resume francais."]),
            ("Methods", ["We did the thing."]),
        ]

    # -- The gate's refusals. Each asserts a run that is *not* split. They were
    # -- written, after the first mutation sweep, to separate each guard from
    # -- its own mutant while the recovery flushed on *reading* a heading: a
    # -- guard wrongly admitting one was visible as a boundary even where the
    # -- heading titled nothing. The flush is lazy since PR #280's review, and
    # -- a heading that titles nothing now leaves no trace — so the float,
    # -- `<ref-list>` and declined-metadata terms are recorded equivalents (see
    # -- `_heading_is_its_containers_own`) and those three tests pin the
    # -- behaviour, not the term. The empty-heading one still separates its
    # -- guard: an empty frame would be a *different* frame, and the next run
    # -- would open a section of its own under it.

    PENDING_THEN = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Pending</article-title>
  </title-group></article-meta></front>
  <back>%s</back>
</article>"""

    def test_a_heading_inside_a_float_does_not_end_the_pending_section(self):
        """The run around a float is one run, whatever the float heads.

        A ``<list>``'s heading inside a ``<table-wrap-foot>`` is not a
        container's — the block's own is #238's and counted there. Admitted,
        it once ended the section the surrounding ``<notes>`` prose was
        collecting into; under the lazy flush it would title nothing and end
        nothing, since no prose inside a float reaches an implicit section. So
        this pins the behaviour, and the float term is a recorded equivalent.
        """
        data = self.PENDING_THEN % (
            b"""<notes><p>Pending.</p>
      <table-wrap id="t1"><table><tbody><tr><td>1</td></tr></tbody></table>
        <table-wrap-foot><list><title>Listed</title>
          <list-item><p>Item.</p></list-item></list></table-wrap-foot></table-wrap>
      <p>After.</p></notes>"""
        )
        article = JATSParser(data).parse()

        assert [(s.title, s.paragraphs) for s in article.body_sections] == [
            ("", ["Pending.", "After."])
        ]

    def test_a_reference_lists_heading_does_not_end_the_pending_section(self):
        """The run around a bibliography is one run.

        *References* never titles anything — the list's own prose is refused as
        apparatus (#224) and its element closes before any routable prose.
        Admitted, it once cut the surrounding back matter in two at the point
        the bibliography started; under the lazy flush it would end nothing,
        so this pins the behaviour and the ``<ref-list>`` term is a recorded
        equivalent.
        """
        data = self.PENDING_THEN % (
            b"""<notes><p>Pending.</p></notes>
    <ref-list><title>References</title>
      <ref id="r1"><mixed-citation>A ref.</mixed-citation></ref></ref-list>
    <ack><p>After.</p></ack>"""
        )
        article = JATSParser(data).parse()

        assert [(s.title, s.paragraphs) for s in article.body_sections] == [
            ("", ["Pending.", "After."])
        ]

    def test_an_empty_heading_does_not_end_the_pending_section(self):
        """The empty-heading guard, and the one of these that still separates.

        ``test_an_empty_heading_is_not_recovered`` above asserts the title
        stays empty, which an empty heading does *anyway* once recovered — so
        that test passes with the guard removed. This one does not: a frame
        holding ``""`` is still a frame, and a *different* one, so the run
        after it would open a section of its own and split the notes in two.
        The guard lives in ``_recover_container_heading`` since PR #280's
        review — the one writer of a frame, and now the only protection.
        """
        data = self.PENDING_THEN % (
            b"""<notes><p>Pending.</p></notes>
    <ack><title>  </title><p>After.</p></ack>"""
        )
        article = JATSParser(data).parse()

        assert [(s.title, s.paragraphs) for s in article.body_sections] == [
            ("", ["Pending.", "After."])
        ]

    def test_a_declined_metadatas_heading_does_not_end_the_pending_section(self):
        """The run around an object's metadata is one run.

        An object's ``<long-desc>`` is declined as metadata wherever its text
        would otherwise weld into prose (#241, #248), and its heading is
        declined with it. Not JATS-legal, and no ``_NON_PROSE_METADATA`` member
        admits a ``<title>`` in either artifact (PR #280's review); under the
        lazy flush an admitted heading here would title nothing and end
        nothing, so this pins the behaviour and the term is a recorded
        equivalent.
        """
        data = self.PENDING_THEN % (
            b"""<notes><p>Pending.</p>
      <long-desc><title>Described</title></long-desc>
      <p>After.</p></notes>"""
        )
        article = JATSParser(data).parse()

        assert [(s.title, s.paragraphs) for s in article.body_sections] == [
            ("", ["Pending.", "After."])
        ]

    def test_a_second_heading_for_one_element_replaces_the_first(self):
        """One element, one close, so one frame — or the audit ERRORs.

        JATS admits a single ``<title>`` per container and **0 of 173,994
        served and 0 of 2,465,840 archive elements carrying one carry two**, so
        this pins a direction rather than a population. Stacked instead of
        replaced, the second frame outlives the element and is reported by
        ``open_container_headings`` — which the autouse ``parser_log`` fixture
        turns into a failure for every test in this module.
        """
        data = self.PENDING_THEN % (
            b"""<ack><title>First</title><title>Second</title><p>Thanks.</p></ack>"""
        )
        handler = JATSParser(data)._run_parser()

        assert [(s.title, s.paragraphs) for s in handler.body_sections] == [("Second", ["Thanks."])]
        assert handler.heading_stack == []

    def test_a_glossary_keeps_the_heading_its_definitions_are_under(self):
        """Issue #231's own worked example, with #228's fold in place.

        The definitions arrived as orphan prose under no heading; they now
        arrive under the one the publisher wrote. 669 of the 726 served
        ``<glossary>`` blocks carrying prose deposit it (686 of 743 counted as
        elements, PR #280's review).
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Gloss</article-title>
  </title-group></article-meta></front>
  <back><glossary><title>Abbreviations</title><def-list>
    <def-item><term>BMI</term><def><p>body mass index</p></def></def-item>
    <def-item><term>CI</term><def><p>confidence interval</p></def></def-item>
  </def-list></glossary></back>
</article>"""
        article = JATSParser(data).parse()

        assert [(s.title, s.paragraphs) for s in article.body_sections] == [
            ("Abbreviations", ["BMI — body mass index", "CI — confidence interval"])
        ]


class TestTheBodySlotCannotBeEmptiedByTheBackFlush:
    """A slot per container, because one slot hides a defect in the other.

    ``</body>`` and ``</back>`` each flush unsectioned prose. Held in one
    slot, a ``</body>`` flush that failed would leave its prose pending, the
    ``<back>`` that follows would append to the same builder, and ``</back>``
    would emit the pair as one section — the article silently losing the
    boundary between its body and its acknowledgements, with nothing stranded
    for the end-of-parse audit to report. That is not a hypothetical cost:
    at least 73.8% of the served corpus carries a ``<back>`` — that is the
    share gaining prose, so the true share is higher — and almost every document
    would mask it.
    """

    ARTICLE = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Two containers</article-title>
  </title-group></article-meta></front>
  <body><p>Loose body prose.</p></body>
  <back><ack><p>We thank the funders.</p></ack></back>
</article>"""

    def test_the_two_containers_stay_two_sections(self):
        """The ordinary case, and the positive control for the two below."""
        article = JATSParser(self.ARTICLE).parse()

        assert [(s.title, s.paragraphs) for s in article.body_sections] == [
            ("", ["Loose body prose."]),
            ("", ["We thank the funders."]),
        ]

    def test_a_back_inside_a_body_reads_as_the_body_throughout(self):
        """Both flags set at once, and ``_append_prose`` resolves it to the body.

        DTD-invalid but well-formed, so expat admits it. ``_append_prose``
        tests ``in_body`` first, so both paragraphs land in the body slot and
        the article reads as one body section.

        **This shape does not discriminate the flush's branch order** — the
        back slot is empty either way, so swapping the branches changes
        nothing here. The shape that does is the mirror one, in
        ``test_a_body_inside_a_back_does_not_let_the_back_branch_steal_the_flush``
        below. Kept because the two methods agreeing is the claim the flush's
        own docstring makes, and this is the half of it that is about
        ``_append_prose``.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Nested back</article-title>
  </title-group></article-meta></front>
  <body><p>Loose body prose.</p>
    <back><ack><p>We thank the funders.</p></ack></back>
  </body>
</article>"""

        article = JATSParser(data).parse()

        # One section, because both paragraphs went to the body slot — and
        # `has_body` agrees, `in_body` being what `_append_prose` asked.
        assert [(s.title, s.paragraphs) for s in article.body_sections] == [
            ("", ["Loose body prose.", "We thank the funders."])
        ]
        assert article.has_body

    def test_a_body_inside_a_back_does_not_let_the_back_branch_steal_the_flush(self):
        """The shape the flush's ``<body>``-first order is actually for.

        Here the back slot fills *before* ``<body>`` opens, so at ``</body>``
        both flags are set **and both slots hold prose** — the only state in
        which the branch order can decide anything. Testing ``in_back`` first
        would empty the back slot at ``</body>``, leaving ``</back>`` nothing
        to flush and the body slot stranded: the body's prose lost outright,
        with the audit reporting it. Testing ``in_body`` first gives each
        container's close its own slot and loses nothing.

        DTD-invalid, so this is a claim about not compounding a malformed
        document rather than about any deposit. Document order is not
        preserved for it, and that is not what the order is protecting.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Nested body</article-title>
  </title-group></article-meta></front>
  <back><ack><p>Back prose.</p></ack>
    <body><p>Body prose.</p></body>
  </back>
</article>"""

        article = JATSParser(data).parse()

        assert [(s.title, s.paragraphs) for s in article.body_sections] == [
            ("", ["Body prose."]),
            ("", ["Back prose."]),
        ]

    def test_a_missing_body_flush_is_not_laundered_by_the_back_flush(self, monkeypatch, parser_log):
        """The defect the second slot exists to keep visible.

        ``</body>``'s flush is suppressed and ``</back>``'s left alone. With
        one shared slot the article emitted a single welded section and the
        audit said nothing; with a slot each, the body's is stranded and
        reported, and the back matter still lands correctly on its own.
        """
        parser_log.expect_errors()
        original = _JATSHandler._flush_implicit_section

        def only_for_back(self):
            if self.in_body:
                return
            original(self)

        monkeypatch.setattr(_JATSHandler, "_flush_implicit_section", only_for_back)

        article = JATSParser(self.ARTICLE).parse()

        assert [(s.title, s.paragraphs) for s in article.body_sections] == [
            ("", ["We thank the funders."])
        ]
        assert any("implicit_body_section" in m for m in parser_log.messages(logging.ERROR))

    def test_a_missing_front_flush_is_not_laundered_by_the_body_flush(
        self, monkeypatch, parser_log
    ):
        """The same defect one container further out (issue #230).

        ``</front>``'s flush is suppressed. Sharing the body's slot, the front
        matter would ride into ``<body>`` and ``</body>`` would emit one welded
        section with the audit silent; with a slot of its own it is stranded
        and reported, and the body lands on its own.
        """
        parser_log.expect_errors()
        original = _JATSHandler._flush_implicit_section

        def not_for_front(self):
            if self.in_front:
                return
            original(self)

        monkeypatch.setattr(_JATSHandler, "_flush_implicit_section", not_for_front)
        data = b"""<?xml version="1.0"?>
<article>
  <front>
    <article-meta><title-group><article-title>Two</article-title></title-group></article-meta>
    <notes><p>Loose front prose.</p></notes>
  </front>
  <body><p>Loose body prose.</p></body>
</article>"""

        article = JATSParser(data).parse()

        assert [(s.title, s.paragraphs) for s in article.body_sections] == [
            ("", ["Loose body prose."])
        ]
        assert any("implicit_front_section" in m for m in parser_log.messages(logging.ERROR))

    def test_a_body_inside_a_front_does_not_let_the_front_branch_steal_the_flush(self):
        """``<body>`` is tested ahead of ``<front>`` in the flush, for the reason it
        is tested ahead of ``<back>``.

        The front slot fills before ``<body>`` opens, so at ``</body>`` both
        flags are set and both slots hold prose — the one state where the order
        decides. Testing ``in_front`` first empties the front slot at
        ``</body>`` and strands the body's, which the autouse ``parser_log``
        fixture fails on. DTD-invalid, so document order is not what is
        protected.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front>
    <article-meta><title-group><article-title>Nested</article-title></title-group></article-meta>
    <notes><p>Front prose.</p></notes>
    <body><p>Body prose.</p></body>
  </front>
</article>"""

        article = JATSParser(data).parse()

        assert [(s.title, s.paragraphs) for s in article.body_sections] == [
            ("", ["Body prose."]),
            ("", ["Front prose."]),
        ]

    def test_a_back_inside_a_front_does_not_let_the_front_branch_steal_the_flush(self):
        """The back-versus-front half of the same order, which the body shape
        above cannot see: there ``in_back`` is never set.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front>
    <article-meta><title-group><article-title>Nested</article-title></title-group></article-meta>
    <notes><p>Front prose.</p></notes>
    <back><ack><p>Back prose.</p></ack></back>
  </front>
  <body><sec><title>M</title><p>Body.</p></sec></body>
</article>"""

        article = JATSParser(data).parse()

        assert [(s.title, s.paragraphs) for s in article.body_sections] == [
            ("", ["Back prose."]),
            ("", ["Front prose."]),
            ("M", ["Body."]),
        ]

    def test_a_back_inside_a_front_keeps_its_reference_list_refusal(self, parser_log):
        """A nesting does not lose the refusal.

        Written to pin the predicate's back-before-front order, when
        ``_unsectioned_prose_is_the_articles`` answered ``in_front`` with no
        ``<ref-list>`` test — on the premise that JATS admits no bibliography
        in ``<front>``, which is false (``<notes>`` admits a ``<ref-list>``;
        PR review). Back and front now apply one rule, so the order is
        equivalent and this pins only that a ``<back>`` nested in a ``<front>``
        keeps its refusal; the front shape itself is
        :meth:`TestJATSParserFrontMatterProse.test_a_front_matter_reference_list_keeps_its_apparatus_out`.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front>
    <article-meta><title-group><article-title>Nested</article-title></title-group></article-meta>
    <back>
      <ref-list><p>Papers of special note have been highlighted.</p></ref-list>
      <ack><p>Funded by the Example Foundation.</p></ack>
    </back>
  </front>
  <body><sec><title>M</title><p>Body.</p></sec></body>
</article>"""

        article = JATSParser(data).parse()

        assert [(s.title, s.paragraphs) for s in article.body_sections] == [
            ("", ["Funded by the Example Foundation."]),
            ("M", ["Body."]),
        ]
        warnings = parser_log.messages(logging.WARNING)
        assert any("1 <ref-list> item(s) were refused" in m for m in warnings), warnings

    def test_back_matter_survives_its_own_flush_order(self, monkeypatch, parser_log):
        """``</back>`` must flush before it clears ``in_back``.

        ``_flush_implicit_section`` picks its slot from the very flags those
        arms clear, so the ordering is load-bearing rather than decorative —
        which it was not when one slot was emptied unconditionally, and the
        comment claiming otherwise outlived the code twice.

        **This test documents what the wrong order costs; it does not detect
        it.** The emulation clears the flag in a wrapper round ``endElement``,
        so on the real mutant — the two source lines swapped — the wrapper is
        idempotent and this passes either way. Verified. What kills that
        mutant is every ordinary fixture in
        ``TestJATSParserUnsectionedBackMatter``, each of which loses the prose
        it asserts. Named here because the source comment used to name *this*
        test as the killer, which is the module's own "a rule enforced by
        prose is not enforced" one level down, and a reader deleting those
        fixtures on the strength of this one would leave the ordering
        unguarded.
        """
        parser_log.expect_errors()
        original = _JATSHandler.endElement

        def clear_before_flush(self, name):
            if name == "back":
                self.in_back = False
            original(self, name)

        monkeypatch.setattr(_JATSHandler, "endElement", clear_before_flush)

        article = JATSParser(self.ARTICLE).parse()

        assert [(s.title, s.paragraphs) for s in article.body_sections] == [
            ("", ["Loose body prose."])
        ]
        assert any("implicit_back_section" in m for m in parser_log.messages(logging.ERROR))


class TestARefusedApparatusParagraphIsReported:
    """A refusal this module *chose* is the drop no reader can otherwise see.

    ``rejected_spans`` (#129) and ``formulas_dropped`` (#177) both settled the
    rule: count it, and report it once per article at WARNING. The
    ``<ref-list>`` refusal is a stronger case than either, being named and
    argued rather than incidental — and issue #150 is the downstream that
    cannot learn the content existed without a line.

    WARNING and not ERROR, because a publisher's deposit reaches it, so it
    cannot spend the audit's *"an ERROR means bmlib is wrong"* contract.
    """

    def test_a_refused_reference_list_paragraph_warns(self, parser_log):
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Refs</article-title>
  </title-group></article-meta></front>
  <body><sec><title>M</title><p>Body.</p></sec></body>
  <back><ref-list>
    <p>Papers of special note have been highlighted as: of interest.</p>
    <ref id="r1"><mixed-citation>Smith J. 2020.</mixed-citation>
      <note><p>Faculty Opinions Recommendation</p></note></ref>
  </ref-list></back>
</article>"""

        JATSParser(data).parse()

        warnings = parser_log.messages(logging.WARNING)
        assert any("2 <ref-list> item(s) were refused" in m for m in warnings), warnings

    def test_an_article_that_refuses_nothing_is_quiet(self, parser_log):
        """The negative control: a counter that always fires reports nothing."""
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Quiet</article-title>
  </title-group></article-meta></front>
  <body><sec><title>M</title><p>Body.</p></sec></body>
  <back><ack><p>Funded by the Example Foundation.</p></ack></back>
</article>"""

        JATSParser(data).parse()

        assert not [m for m in parser_log.messages(logging.WARNING) if "refused" in m]

    def test_prose_outside_every_container_is_not_counted_as_a_refusal(self, parser_log):
        """The arm is gated on the refusal, never a bare ``else``.

        A ``<p>`` in a ``<floats-group>``'s ``<boxed-text>`` sits in none of
        ``<front>``, ``<body>`` or ``<back>``, so it falls past the same branch
        and is dropped just as silently — but nothing decided that (issue
        #253), and pooling it would report prose this module never considered
        as prose it refused.

        This is the mutant the refusal tests cannot see: widening the arm to
        ``elif text:`` passes them all, and only this test and
        ``test_a_floats_group_section_reaches_the_refusal_predicate`` redden.
        (An earlier draft said "every other test in the module", which was
        false before this fixture moved too.) The fixture was a
        ``<front><author-notes>`` statement until issue #230 routed front
        matter. Besides the refused apparatus the arm exists for, what reaches
        this ``elif`` outside a float is all under a ``<floats-group>``: 30
        runs in 9 of the 8,118 served articles (28 in 8 a ``<boxed-text>``'s, 2
        in 1 a ``<table-wrap-group>`` caption's) and 925 in 192 of the 97,909
        archive ones (894 in 184 a ``<boxed-text>``'s, 31 in 8 a
        ``<fig-group>`` caption's).
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <title-group><article-title>Floats</article-title></title-group>
  </article-meta></front>
  <body><sec><title>M</title><p>Body.</p></sec></body>
  <floats-group><boxed-text><p>A panel nothing routes.</p></boxed-text></floats-group>
</article>"""

        article = JATSParser(data).parse()

        # Dropped — a routing gap of its own, not a refusal.
        assert [p for s in article.body_sections for p in s.paragraphs] == ["Body."]
        # Never as a refusal, which would put words in this module's mouth.
        assert not [m for m in parser_log.messages(logging.WARNING) if "refused" in m]

    def test_a_refused_formula_is_not_reported_as_a_routing_gap(self, parser_log):
        """A decision must not print as the gap issue #177 is still open on.

        ``_prose_reaches_output`` is False for both, so before the split a
        ``<disp-formula>`` inside a refused ``<ref-list>`` incremented
        ``formulas_dropped`` and emitted *"reached no section, caption or
        cell"* — sending a reader after a routing gap this module chose not to
        have, and inflating the counter whose remaining population is what
        #177 is sized by.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Refs</article-title>
  </title-group></article-meta></front>
  <body><sec><title>M</title><p>Body.</p></sec></body>
  <back><ref-list><ref id="r1"><note>
    <disp-formula><tex-math>\\begin{document}$$s = 1$$\\end{document}</tex-math></disp-formula>
  </note></ref></ref-list></back>
</article>"""

        JATSParser(data).parse()

        warnings = parser_log.messages(logging.WARNING)
        # The **count**, not the substring. Asserting presence alone is what
        # let a first cut increment at both the formula arm and
        # `_append_prose` and report this one formula as two.
        assert any("1 <ref-list> item(s) were refused" in m for m in warnings), warnings
        assert not any("reached no section, caption, cell or footnote" in m for m in warnings), (
            warnings
        )

    def test_a_float_inside_a_refused_list_is_still_the_float_gap(self, parser_log):
        """Where both causes apply, the float wins, and that is not arbitrary.

        A ``<disp-formula>`` inside a ``<fig>`` with no ``<caption>`` open is
        dropped by the float branch whatever encloses the figure — delete the
        ``<ref-list>`` rule entirely and this formula is still lost — so the
        refusal is not what costs the article anything here, and reporting it
        as one would move a live #177 case out of the counter #177 is sized
        by.

        This is the only guard of the three ``_prose_is_refused_apparatus``
        carries that changes an answer. ``in_abstract`` cannot decide —
        ``_prose_reaches_output`` answers ``True`` for it one branch earlier
        unless a float is open too, and then the float guard wins. But
        ``section_stack`` *is* reachable non-empty, which an earlier draft of
        this docstring denied: a ``<sec>`` inside a ``<floats-group>``'s
        ``<boxed-text>`` leaves it loaded while ``in_front``, ``in_body`` and
        ``in_back`` are all ``False``. It costs nothing only because
        ``in_back`` and ``in_front`` are both ``False`` there too, the two flags
        the predicate's final line asks. See
        ``test_a_floats_group_section_reaches_the_refusal_predicate``.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Refs</article-title>
  </title-group></article-meta></front>
  <body><sec><title>M</title><p>Body.</p></sec></body>
  <back><ref-list><ref id="r1"><fig id="f1">
    <disp-formula><tex-math>\\begin{document}$$s = 1$$\\end{document}</tex-math></disp-formula>
  </fig></ref></ref-list></back>
</article>"""

        JATSParser(data).parse()

        warnings = parser_log.messages(logging.WARNING)
        assert any("1 display formula(s) were rendered" in m for m in warnings), warnings
        assert not any("bibliography apparatus" in m for m in warnings), warnings

    def test_a_floats_group_section_reaches_the_refusal_predicate(self, monkeypatch, parser_log):
        """The guard an earlier docstring called unreachable.

        ``<boxed-text>`` admits ``sec*``, and one in a ``<floats-group>`` sits
        in none of the three containers, so a ``<sec>`` there loads
        ``section_stack`` while ``in_front``, ``in_body`` and ``in_back`` are
        all ``False``. ``_prose_reaches_output``'s section branch is a
        *conjunction* of those, so it does not answer ``True``, and the
        ``<disp-formula>`` arm calls ``_prose_is_refused_apparatus`` with the
        stack loaded. The answer is still right — nothing was refused, the
        formula is the routing gap — but the guard is reached, so it must not
        be deleted as dead.

        The fixture was a ``<sec>`` in ``<front><notes>`` until issue #230 put
        ``in_front`` into that conjunction, which files the formula and no
        longer asks this predicate at all.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Floats sec</article-title>
  </title-group></article-meta></front>
  <body><sec><title>M</title><p>Body.</p></sec></body>
  <floats-group><boxed-text><sec><title>Box</title>
    <disp-formula><tex-math>\\begin{document}$$s = 1$$\\end{document}</tex-math>
    </disp-formula></sec></boxed-text></floats-group>
</article>"""

        seen: list[tuple[bool, bool, bool, bool]] = []
        original = _JATSHandler._prose_is_refused_apparatus

        def spy(self):
            seen.append((bool(self.section_stack), self.in_front, self.in_body, self.in_back))
            return original(self)

        monkeypatch.setattr(_JATSHandler, "_prose_is_refused_apparatus", spy)

        JATSParser(data).parse()

        # The claim itself: reached, with the stack loaded and no container
        # flag set. Asserting only the WARNING below would pass whether or not
        # the guard were ever reached.
        assert (True, False, False, False) in seen, seen

        warnings = parser_log.messages(logging.WARNING)
        assert any("1 display formula(s) were rendered" in m for m in warnings), warnings
        assert not any("bibliography apparatus" in m for m in warnings), warnings

    @staticmethod
    def _sectioned_reference_list(container: str, inner: str) -> bytes:
        """An article whose ``<ref-list>`` sits under a ``<sec>`` in ``container``."""
        notes = f"<sec><title>Notes</title><ref-list>{inner}</ref-list></sec>"
        front = back = ""
        if container == "back":
            back = f"<back>{notes}</back>"
        else:
            front = f"<notes>{notes}</notes>"
        return f"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Refs</article-title>
  </title-group></article-meta>{front}</front>
  <body><sec><title>M</title><p>Body.</p></sec></body>
  {back}
</article>""".encode()

    @staticmethod
    def _in_document_order(
        container: str, notes: tuple[str, list[str]]
    ) -> list[tuple[str, list[str]]]:
        body = ("M", ["Body."])
        return [notes, body] if container == "front" else [body, notes]

    @pytest.mark.parametrize("container", ["back", "front"])
    def test_a_formula_under_a_sectioned_reference_list_is_not_reported_dropped(
        self, container, parser_log
    ):
        """``in_back`` and ``in_front`` both decide ``_prose_reaches_output``'s
        section conjunction.

        A ``<ref-list>`` under a back or front ``<sec>`` keeps its apparatus,
        so ``_append_prose`` files this formula into the section. The
        predicate's final line would refuse it, so without the container's flag
        in the conjunction the formula arm reports a loss that did not happen.
        Only ``in_body`` in the same conjunction is answered by that final line
        too, which is why it is the one of the three no test can pin.

        The front row was missing, and ``in_front`` recorded as an equivalent
        mutant in seven places, from the commit that put ``<front>`` under the
        ``<ref-list>`` rule until PR #256's review found the mutant passing
        every test in this module.
        """
        data = self._sectioned_reference_list(
            container,
            '<ref id="r1"><note><disp-formula><tex-math>'
            "\\begin{document}$$s = 1$$\\end{document}"
            "</tex-math></disp-formula></note></ref>",
        )

        article = JATSParser(data).parse()

        assert [(s.title, s.paragraphs) for s in article.body_sections] == (
            self._in_document_order(container, ("Notes", ["$$s = 1$$"]))
        )
        warnings = parser_log.messages(logging.WARNING)
        assert not any("display formula(s) were rendered" in m for m in warnings), warnings

    @pytest.mark.parametrize("container", ["back", "front"])
    @pytest.mark.parametrize(
        ("inner", "filed", "warning"),
        [
            pytest.param(
                "<disp-quote><p>Quoted.</p><attrib>(P2, CP)</attrib></disp-quote>",
                ["Quoted.", "(P2, CP)"],
                "attribution(s) were read and filed nowhere",
                id="attrib",
            ),
            pytest.param(
                "<def-list><def-item><term>BMI</term>"
                "<def><p>body mass index</p></def></def-item></def-list>",
                ["BMI — body mass index"],
                "term(s) were read and reached no",
                id="term",
            ),
        ],
    )
    def test_prose_under_a_sectioned_reference_list_is_filed_whole(
        self, container, inner, filed, warning, parser_log
    ):
        """The same conjunction, where a wrong answer loses content.

        The formula test above sees only a false WARNING, because the formula
        arm files first and asks afterwards. An ``<attrib>`` and a definition
        term ask *before* anything is filed, so without the container's flag
        the attribution is dropped outright and the term does not fold —
        content missing from a section that is otherwise filed.
        """
        data = self._sectioned_reference_list(container, inner)

        article = JATSParser(data).parse()

        assert [(s.title, s.paragraphs) for s in article.body_sections] == (
            self._in_document_order(container, ("Notes", filed))
        )
        warnings = parser_log.messages(logging.WARNING)
        assert not any(warning in m for m in warnings), warnings

    def test_a_reference_list_in_the_body_keeps_its_apparatus(self, parser_log):
        """The refusal is scoped to `<back>`, and the scope is a claim.

        ``in_body`` answers first in ``_unsectioned_prose_is_the_articles``,
        so a ``<ref-list>`` in an unsectioned ``<body>`` keeps its apparatus
        and counts towards ``body_paragraph_count``. Pre-existing and measured
        near-empty (0 of 8,118 served), but `docs/DECISIONS.md` publishes it
        as the rule's scope, so a later "simplification" that unified the two
        would move stored output with nothing red.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Body refs</article-title>
  </title-group></article-meta></front>
  <body><ref-list><p>Papers of special note.</p></ref-list></body>
</article>"""

        article = JATSParser(data).parse()

        assert [p for s in article.body_sections for p in s.paragraphs] == [
            "Papers of special note."
        ]
        assert article.has_body
        assert not [m for m in parser_log.messages(logging.WARNING) if "refused" in m]

    def test_a_reference_list_under_an_open_section_keeps_its_apparatus(self, parser_log):
        """The other half of the same scope, on the `<back>` side.

        Prose under an open ``<sec>`` never reaches the predicate at all, so a
        ``<ref-list>`` inside a ``<back>`` ``<sec>`` is not refused — 0 of
        8,118 served and 1 of 97,909 archive articles.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Sec refs</article-title>
  </title-group></article-meta></front>
  <body><sec><title>M</title><p>Body.</p></sec></body>
  <back><sec><title>Extras</title>
    <ref-list><p>Papers of special note.</p></ref-list></sec></back>
</article>"""

        article = JATSParser(data).parse()

        assert [(s.title, s.paragraphs) for s in article.body_sections] == [
            ("M", ["Body."]),
            ("Extras", ["Papers of special note."]),
        ]
        assert not [m for m in parser_log.messages(logging.WARNING) if "refused" in m]


class TestADefinitionCarriesTheTermItDefines:
    """Issue 228 — a ``<def-list>``'s ``<term>`` reached no handler at all.

    ``<def-item>`` pairs a ``<term>`` with a ``<def>``, and the ``<def>``'s
    ``<p>`` routes as ordinary prose while the term's buffer was popped and
    discarded — so an abbreviations list rendered as *"messenger RNA / odds
    ratio / reverse-transcriptase polymerase chain reaction"*, definitions
    with no words defined. Pre-existing in ``<body>`` and multiplied in
    ``<back>`` by issue 224's routing.

    **The term is folded into the definition's own paragraph** rather than
    modelled. That is what this module already does with a ``<list>``, whose
    ``<list-item>`` buffer is discarded and whose ``<p>`` routes alone, and it
    is the shape issue #124 proposes for a footnote marker — so one answer
    serves three containers instead of three models. It costs no public field
    and no ``to_dict`` change.

    Measured over two named public artifacts: 14,186 ``<def-item>`` in 965 of
    the 8,118 served articles of Europe PMC's
    ``PMC10030002_PMC10040000.xml.gz``, and 153,256 in 9,813 of the 97,909
    archive articles of ``oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26.tar.gz``
    — every one of them carrying exactly one ``<term>``, and every ``<term>``
    in both artifacts a direct child of a ``<def-item>``.
    """

    def test_a_body_definition_list_keeps_its_terms(self):
        """The issue's own reproduction, in a body ``<sec>``."""
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Abbrev</article-title>
  </title-group></article-meta></front>
  <body><sec><title>Abbreviations</title>
    <def-list>
      <def-item><term>mRNA</term><def><p>messenger RNA</p></def></def-item>
      <def-item><term>OR</term><def><p>odds ratio</p></def></def-item>
    </def-list>
  </sec></body>
</article>"""

        article, html = JATSParser(data).parse_with_html()

        assert [p for s in article.body_sections for p in s.paragraphs] == [
            "mRNA — messenger RNA",
            "OR — odds ratio",
        ]
        assert "mRNA — messenger RNA" in html

    def test_a_glossary_definition_keeps_its_term(self):
        """The ``<back>`` shape, which is where issue 224 put the population.

        ``<glossary>`` is the third-largest container that routing reaches.
        Its section's heading is a separate question and not this test's: it
        was dropped until issue #231, and since then the glossary's own
        *Abbreviations* heads it — pinned by
        ``test_a_glossary_keeps_the_heading_its_definitions_are_under``, which
        uses the same shape.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Glossary</article-title>
  </title-group></article-meta></front>
  <body><sec><title>Methods</title><p>We did the thing.</p></sec></body>
  <back><glossary><title>Abbreviations</title><def-list>
    <def-item><term>BMI</term><def><p>body mass index</p></def></def-item>
    <def-item><term>CI</term><def><p>confidence interval</p></def></def-item>
  </def-list></glossary></back>
</article>"""

        article = JATSParser(data).parse()

        assert [p for s in article.body_sections for p in s.paragraphs] == [
            "We did the thing.",
            "BMI — body mass index",
            "CI — confidence interval",
        ]

    def test_a_wrapped_term_is_normalised_and_not_merely_stripped(self):
        """``'J.\\nTan'`` reached a public field once already (issue #146).

        A ``<term>`` may wrap across source lines like anything else, so the
        pending value is whitespace-normalised rather than end-stripped.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Wrap</article-title>
  </title-group></article-meta></front>
  <body><sec><title>A</title><def-list><def-item>
    <term>RT-PCR
      assay</term><def><p>reverse-transcriptase polymerase chain reaction</p></def>
  </def-item></def-list></sec></body>
</article>"""

        article = JATSParser(data).parse()

        assert [p for s in article.body_sections for p in s.paragraphs] == [
            "RT-PCR assay — reverse-transcriptase polymerase chain reaction",
        ]

    def test_a_terms_own_markup_reaches_the_paragraph(self):
        """A term is prose, so its inline markup is flattened as prose is.

        ``<sub>``/``<italic>`` inside a ``<term>`` merge into the term's own
        buffer, which is what makes ``H2O`` arrive whole rather than as its
        first text node — the ``_text()`` truncation one package over.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Markup</article-title>
  </title-group></article-meta></front>
  <body><sec><title>A</title><def-list><def-item>
    <term>H<sub>2</sub>O</term><def><p>water</p></def>
  </def-item></def-list></sec></body>
</article>"""

        article = JATSParser(data).parse()

        assert [p for s in article.body_sections for p in s.paragraphs] == ["H2O — water"]

    def test_a_definition_of_several_paragraphs_takes_the_term_once(self):
        """The term prefixes the first paragraph, not every one of them.

        1 ``<def-item>`` of 14,186 served carries two ``<def>`` and 2 carry a
        ``<def>`` running to more than one paragraph, so this is a floor
        rather than a rate — and repeating the term would read as two
        definitions of one word.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Long</article-title>
  </title-group></article-meta></front>
  <body><sec><title>A</title><def-list><def-item>
    <term>ITT</term><def><p>intention to treat.</p><p>All randomised.</p></def>
  </def-item></def-list></sec></body>
</article>"""

        article = JATSParser(data).parse()

        assert [p for s in article.body_sections for p in s.paragraphs] == [
            "ITT — intention to treat.",
            "All randomised.",
        ]

    def test_an_empty_term_adds_no_separator(self):
        """9 of 14,186 served terms and 7 of 153,256 archive ones hold nothing.

        A bare ``" — water"`` is worse than the definition alone: it asserts a
        word was defined and shows none, which is #162's invented-number rule
        one element family over.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Empty</article-title>
  </title-group></article-meta></front>
  <body><sec><title>A</title><def-list><def-item>
    <term/><def><p>water</p></def>
  </def-item></def-list></sec></body>
</article>"""

        article = JATSParser(data).parse()

        assert [p for s in article.body_sections for p in s.paragraphs] == ["water"]

    def test_a_nested_definition_list_keeps_each_term_with_its_own_definition(self):
        """A ``<def>`` admits a ``<def-list>``, so ``<def-item>`` nests.

        Held as one slot the inner term overwrote the outer one and the inner
        close cleared it — #115's defect one element family over — so the
        pending term is a stack and the outer item's own definition, arriving
        after the nested list, still gets its term.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Nested</article-title>
  </title-group></article-meta></front>
  <body><sec><title>A</title><def-list><def-item>
    <term>outer</term>
    <def>
      <def-list><def-item><term>inner</term><def><p>inner sense</p></def></def-item></def-list>
      <p>outer sense</p>
    </def>
  </def-item></def-list></sec></body>
</article>"""

        article = JATSParser(data).parse()

        assert [p for s in article.body_sections for p in s.paragraphs] == [
            "inner — inner sense",
            "outer — outer sense",
        ]

    def test_a_term_outside_a_definition_item_prefixes_nothing(self):
        """The parent test, which is this module's rule for a ``<label>``.

        Read from the ambient *"is a definition list open?"*, a ``<term>``
        deposited anywhere else would prefix the next paragraph to arrive with
        a word that defines nothing in it. 0 of the 14,186 served ``<term>``
        and 0 of the 153,256 archive ones have a parent other than
        ``<def-item>``, so this pins a direction rather than a population.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Loose</article-title>
  </title-group></article-meta></front>
  <body><sec><title>A</title>
    <def-list><term>loose</term><def-item><def><p>a definition</p></def></def-item></def-list>
    <p>Ordinary prose.</p>
  </sec></body>
</article>"""

        article = JATSParser(data).parse()

        assert [p for s in article.body_sections for p in s.paragraphs] == [
            "a definition",
            "Ordinary prose.",
        ]

    def test_a_term_deposited_inside_an_open_item_prefixes_nothing(self):
        """The parent test's own mutant, which the loose-``<term>`` case cannot reach.

        A ``<term>`` *before* any ``<def-item>`` leaves the stack empty, so
        dropping the parent test is inert for it — mutation-measured, the
        whole suite green. What separates the guard from its mutant is a
        ``<term>`` with some other parent while an item is open: read from the
        ambient stack it displaces the real term, counts it as dropped, and
        renames the definition.

        Constructed rather than drawn. 14,186 of 14,186 ``<term>`` in the
        served bundle have a ``<def-item>`` parent, and the bundle deposits no
        ``<index-term>`` at all, so what this pins is that a deposit no draw
        has shown cannot corrupt the article — the ``<label>`` rule's own
        argument (#116), where the corruption *was* the measured population.

        The inner word is **counted**, not silently lost. ``<term>``
        accumulates a buffer and is not inline, so ``index`` is read and
        discarded whatever this arm decides — pre-existing, and invisible
        until the drop counter was scoped to reach it (PR #236's review). The
        definition keeps the parent test's answer and the reader is told a
        word went missing, which is the pair the counter exists to deliver.
        """
        handler = _run_handler(
            b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Inner term</article-title>
  </title-group></article-meta></front>
  <body><sec><title>A</title><def-list><def-item>
    <term>BMI</term><def><p>body mass <term>index</term></p></def>
  </def-item></def-list></sec></body>
</article>"""
        )

        assert [p for s in handler.body_sections for p in s.paragraphs] == ["BMI — body mass"]
        assert handler.definition_terms_dropped == 1

    def test_an_empty_paragraph_does_not_spend_the_term(self):
        """An empty ``<p>`` is a paragraph the document deposited, and takes no term.

        Several tests here pin the empty string a ``<sec>``'s empty ``<p>``
        appends, so the definition's own empty paragraph must keep that shape
        — and the term must survive to the paragraph that says something.
        Spent on the empty one it renders as ``"BMI — "``, a word defined by
        nothing, which is #162's invented-value rule again.
        """
        handler = _run_handler(
            b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Empty first</article-title>
  </title-group></article-meta></front>
  <body><sec><title>A</title><def-list><def-item>
    <term>BMI</term><def><p/><p>body mass index</p></def>
  </def-item></def-list></sec></body>
</article>"""
        )

        assert [p for s in handler.body_sections for p in s.paragraphs] == [
            "",
            "BMI — body mass index",
        ]
        assert handler.definition_terms_dropped == 0

    def test_a_reviewers_definition_term_is_not_this_articles(self):
        """A ``<sub-article>``'s definition list is a nested article's (#110).

        The frame is pushed after ``startElement``'s suppression return and
        popped under the matching guard, so the two stay balanced across a
        skipped region. **What an imbalance costs here is a stranded frame,
        not a stolen word**: the ``<term>`` arm sits behind the same
        suppression guard, so a reviewer's term is never *read* — hoisting the
        push above the return leaves a ``None`` frame that masks the host's
        own pending term and suppresses its folds for the rest of the
        document. So the stack is asserted directly; reading the host's
        paragraphs alone left the guard pinned only by the audit's ERROR at
        teardown (PR #236's review).

        The nested article is deposited **before** ``<body>`` — out of the
        usual order, and well-formed — so there is host prose after the
        suppressed region for a stranded frame to reach.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Host</article-title>
  </title-group></article-meta></front>
  <sub-article article-type="peer-review">
    <body><sec><title>Review</title><def-list><def-item>
      <term>REV</term><def><p>reviewer term</p></def>
    </def-item></def-list></sec></body>
  </sub-article>
  <body><sec><title>Results</title>
    <def-list><def-item><term>HOST</term><def><p>host sense</p></def></def-item></def-list>
    <p>Host prose.</p>
  </sec></body>
</article>"""

        article = JATSParser(data).parse()
        handler = JATSParser(data)._run_parser()

        assert [p for s in article.body_sections for p in s.paragraphs] == [
            "HOST — host sense",
            "Host prose.",
        ]
        assert handler.def_item_stack == []
        assert handler.definition_terms_dropped == 0

    def test_a_definition_inside_a_caption_stays_in_the_caption(self):
        """``_append_prose``'s first branch, and the prefix runs before all five.

        A ``<p>`` may carry a ``<def-list>``, so a figure legend can hold one.
        Prefixing inside ``_append_prose`` rather than at the ``<p>`` arm is
        what makes one rule serve the caption, the abstract, a section, the
        unsectioned branch and the ``<ref-list>`` refusal — the reason that
        method exists at all (issue #147).
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Cap</article-title>
  </title-group></article-meta></front>
  <body><sec><title>A</title>
    <fig id="f1"><label>Figure 1</label><caption><p><def-list><def-item>
      <term>SD</term><def><p>standard deviation</p></def>
    </def-item></def-list></p></caption><graphic xlink:href="f1.jpg"/></fig>
    <p>Prose after the figure.</p>
  </sec></body>
</article>"""

        article = JATSParser(data).parse()

        assert article.figures[0].caption == "SD — standard deviation"
        assert [p for s in article.body_sections for p in s.paragraphs] == [
            "Prose after the figure."
        ]

    def test_a_float_inside_a_definition_does_not_take_its_term(self):
        """The exhibit's caption is not this definition (PR #236's review).

        JATS admits a ``<fig>`` inside a ``<def>``, and its ``<caption>`` is
        the first prose to reach output while the item is open — so the fold
        spent the word on ``JATSFigureInfo.caption``, a public field
        ``to_html`` renders, and left the definition without it. The mirror
        image of the test above, which is why the two sit together: there the
        exhibit opened *before* the item and the fold is correct.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Float</article-title>
  </title-group></article-meta></front>
  <body><sec><title>A</title>
    <def-list><def-item><term>BMI</term><def>
      <fig id="f1"><caption><p>A legend.</p></caption><graphic xlink:href="f1.jpg"/></fig>
      <p>body mass index</p>
    </def></def-item></def-list>
  </sec></body>
</article>"""

        article = JATSParser(data).parse()
        handler = JATSParser(data)._run_parser()

        assert article.figures[0].caption == "A legend."
        assert [p for s in article.body_sections for p in s.paragraphs] == ["BMI — body mass index"]
        assert handler.definition_terms_dropped == 0

    def test_a_table_inside_a_definition_does_not_take_its_term(self):
        """The same rule for the other exhibit, which nests by its own route.

        ``exhibit_depth`` sums both stacks, so a test naming only ``<fig>``
        would leave the ``<table-wrap>`` half free to regress on its own.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Tbl</article-title>
  </title-group></article-meta></front>
  <body><sec><title>A</title>
    <def-list><def-item><term>BMI</term><def>
      <table-wrap id="t1"><caption><p>Table legend.</p></caption>
        <table><tr><td>x</td></tr></table></table-wrap>
      <p>body mass index</p>
    </def></def-item></def-list>
  </sec></body>
</article>"""

        article = JATSParser(data).parse()

        assert article.tables[0].caption == "Table legend."
        assert [p for s in article.body_sections for p in s.paragraphs] == ["BMI — body mass index"]

    def test_a_definition_that_is_only_a_float_counts_its_term(self, parser_log):
        """Refusing the fold must not become a silent drop.

        The term stays pending rather than being spent on the caption, so the
        ``</def-item>`` arm is what has to report it — otherwise the scope
        test trades a wrong value for the missing one nobody is told about.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>OnlyFloat</article-title>
  </title-group></article-meta></front>
  <body><sec><title>A</title>
    <def-list><def-item><term>BMI</term><def>
      <fig id="f1"><caption><p>Only legend.</p></caption><graphic xlink:href="f1.jpg"/></fig>
    </def></def-item></def-list>
    <p>Body.</p>
  </sec></body>
</article>"""

        article = JATSParser(data).parse()
        handler = JATSParser(data)._run_parser()

        assert article.figures[0].caption == "Only legend."
        assert [p for s in article.body_sections for p in s.paragraphs] == ["Body."]
        assert handler.definition_terms_dropped == 1
        assert any("1 <def-list> term(s)" in m for m in parser_log.messages(logging.WARNING))

    def test_a_definition_in_an_abstract_carries_its_term(self):
        """The abstract is the fifth destination, and it was the unpinned one.

        ``abstract_sections`` is rendered into the HTML ``FullTextService``
        caches, and JATS admits a ``<def-list>`` directly in an ``<abstract>``
        — so a mutant excluding the abstract from the spend gate lost the word
        and passed the whole suite (PR #236's review).
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Abs</article-title>
  </title-group>
  <abstract><p>Lead.</p><def-list><def-item>
    <term>BMI</term><def><p>body mass index</p></def>
  </def-item></def-list></abstract></article-meta></front>
  <body><sec><title>A</title><p>Body.</p></sec></body>
</article>"""

        article = JATSParser(data).parse()

        assert "BMI — body mass index" in " ".join(s.content for s in article.abstract_sections)

    def test_a_definition_in_a_float_with_no_caption_is_counted(self, parser_log):
        """A float position with no destination, and its mutant was silent.

        Prose inside a float but outside a ``<caption>`` is dropped as exhibit
        furniture. Widening the spend gate to consume the term there left
        ``definition_terms_dropped`` at zero with no line at all — the counter
        reading zero over a population it exists to size.

        **The fixture is narrower than it was, and issue #124 is why.** It
        used to deposit the list in a ``<table-wrap-foot><fn>``, on the reading
        that a float's non-caption prose reaches nothing — true when it was
        written, and the position it named is now a *destination*: an
        exhibit's footnote is the exhibit's own content and is collected. That
        moved 66 of the 1,510 served drops and 926 of the 10,394 archive ones
        out of this counter, re-measured then at 1,444 and 9,468 (and at 3 and
        23 since issue #230 routed front matter).
        What is left inside a float is prose in neither a caption nor a
        footnote, which is what this deposits now. A session
        finding this comment should not restore the old fixture — it would
        assert a drop this parser no longer makes.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Furn</article-title>
  </title-group></article-meta></front>
  <body><sec><title>A</title>
    <fig id="f1"><graphic xlink:href="f1.jpg"/>
      <def-list><def-item>
        <term>BMI</term><def><p>body mass index</p></def>
      </def-item></def-list></fig>
    <p>Body.</p>
  </sec></body>
</article>"""

        handler = JATSParser(data)._run_parser()

        assert handler.definition_terms_dropped == 1
        assert any("1 <def-list> term(s)" in m for m in parser_log.messages(logging.WARNING))

    @pytest.mark.parametrize(
        ("position", "body"),
        [
            (
                "section",
                b"<body><sec><title>A</title>%s<p>After.</p></sec></body>",
            ),
            (
                "unsectioned body",
                b"<body>%s<p>After.</p></body>",
            ),
            (
                "back matter",
                b"<body><sec><title>A</title><p>B.</p></sec></body>"
                b"<back><ack>%s<p>After.</p></ack></back>",
            ),
            (
                "figure caption",
                b"<body><sec><title>A</title>"
                b'<fig id="f1"><caption><p>%s</p></caption>'
                b'<graphic xlink:href="f1.jpg"/></fig><p>After.</p></sec></body>',
            ),
            (
                "reference list",
                b"<body><sec><title>A</title><p>B.</p></sec></body>"
                b"<back><ref-list>%s</ref-list></back>",
            ),
            (
                "front matter",
                b"<body><sec><title>A</title><p>B.</p></sec></body>",
            ),
            (
                "floats group",
                b"<body><sec><title>A</title><p>B.</p></sec></body>"
                b"<floats-group><boxed-text>%s<p>After.</p></boxed-text></floats-group>",
            ),
            (
                "float with no caption",
                b"<body><sec><title>A</title>"
                b'<fig id="f1"><graphic xlink:href="f1.jpg"/>%s</fig>'
                b"<p>After.</p></sec></body>",
            ),
            (
                "exhibit footnote",
                b"<body><sec><title>A</title>"
                b'<table-wrap id="t1"><table><tr><td>x</td></tr></table>'
                b"<table-wrap-foot><fn>%s</fn></table-wrap-foot></table-wrap>"
                b"<p>After.</p></sec></body>",
            ),
        ],
    )
    def test_a_term_is_consumed_only_where_it_is_accounted_for(self, position, body):
        """The rule the two-predicate gate exists for, over every routing position.

        ``_prose_reaches_output`` mirrors ``_append_prose``'s filing branches
        and nothing mechanises the pair, so a branch added to one and not the
        other silently spends the word on a paragraph that is then dropped —
        with ``definition_terms_dropped`` reading zero over exactly the
        population it exists to size (PR #236's review). Driving every
        position through one invariant is what turns that from prose into a
        test: **a term this parser read is either visible in the article or
        counted, never neither and never both.**

        Two rows fail if the gate is widened to consume where nothing files,
        ``floats group`` and ``float with no caption`` (and so do
        ``test_a_definition_in_a_float_with_no_caption_is_counted`` and
        ``test_a_term_whose_definition_reaches_nothing_is_counted``): a
        ``<boxed-text>`` in ``<floats-group>`` sits in none of ``<front>``,
        ``<body>`` or ``<back>``, so its prose falls past every branch with no
        counter of its own (issue #253). The ``front matter`` row was the
        non-float one until issue #230 routed front matter, which turned it into
        a visibility check — kept, since a front-matter definition list was
        1,441 of the 1,444 terms the counter reported over the served artifact.
        """
        definitions = (
            b"<def-list><def-item><term>BMI</term>"
            b"<def><p>body mass index</p></def></def-item></def-list>"
        )
        if position == "front matter":
            front = (
                b"<front><article-meta><title-group>"
                b"<article-title>Inv</article-title></title-group>"
                + definitions
                + b"</article-meta></front>"
            )
            rendered_body = body
        else:
            front = (
                b"<front><article-meta><title-group>"
                b"<article-title>Inv</article-title></title-group>"
                b"</article-meta></front>"
            )
            rendered_body = body % definitions

        data = b'<?xml version="1.0"?><article>' + front + rendered_body + b"</article>"

        handler = JATSParser(data)._run_parser()
        article = JATSParser(data).parse()

        # An exhibit's `footnotes` is a destination since issue #124, so the
        # visibility half has to read it. Left out, the `exhibit footnote` row
        # reports a term that *is* in the article as an unaccounted loss —
        # which is the same failure this test exists to catch, made by the
        # test rather than by the code.
        rendered = " ".join(
            [p for s in article.body_sections for p in s.paragraphs]
            + [s.content for s in article.abstract_sections]
            + [f.caption for f in article.figures]
            + [t.caption for t in article.tables]
            + [note for f in article.figures for note in f.footnotes]
            + [note for t in article.tables for note in t.footnotes]
        )
        visible = "BMI — body mass index" in rendered
        # Either counter accounts for the word. The `<ref-list>` row is why
        # both are named: there the term *is* folded, into a paragraph the
        # refusal then discards and counts as apparatus — one loss, one count,
        # which is the rule PR #232's review had to correct for a
        # `<disp-formula>`. Naming only the term counter reports that row as
        # an unaccounted loss and would push a future reader into
        # double-counting it.
        counted = bool(handler.definition_terms_dropped) or bool(handler.refused_apparatus_prose)

        assert visible != counted, (
            f"{position}: visible={visible} "
            f"dropped={handler.definition_terms_dropped} "
            f"refused={handler.refused_apparatus_prose} — a term must be "
            "either folded into the article or counted by exactly one "
            "counter, never neither and never both"
        )

    def test_prose_after_an_unconsumed_term_does_not_take_it(self, parser_log):
        """The pop is what stops a pending term reaching the next paragraph.

        Every other fixture here either consumes the term or ends at the
        close, which leaves the pop pinned only by the audit's ERROR at
        teardown — the "both edges" rule: a fixture that stops at the close
        cannot see a frame that fails to go off.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Edge</article-title>
  </title-group></article-meta></front>
  <body><sec><title>A</title>
    <def-list><def-item><term>orphan</term></def-item></def-list>
    <p>Ordinary prose.</p>
  </sec></body>
</article>"""

        article = JATSParser(data).parse()
        handler = JATSParser(data)._run_parser()

        assert [p for s in article.body_sections for p in s.paragraphs] == ["Ordinary prose."]
        assert handler.def_item_stack == []
        assert handler.definition_terms_dropped == 1
        assert any("1 <def-list> term(s)" in m for m in parser_log.messages(logging.WARNING))


class TestATermThatCouldNotBeFiledIsReported:
    """The residue issue 228's own comment asks for a line for.

    Folding the term into the definition's paragraph files it wherever that
    paragraph routes — and where the paragraph routes *nowhere*, the pair is
    lost together and the term's loss is the half no reader could otherwise
    see. Counted and reported once per article at WARNING, the granularity and
    the level ``rejected_spans`` settled for #129, ``formulas_dropped`` for
    #177 and ``refused_apparatus_prose`` for issue 224.

    **The counter is scoped to a ``<term>``, and the shared
    label-or-term counter that comment proposes is refused on measurement.**
    An unfiled ``<label>`` — one whose owner is not a formula, a ``<fig>``, a
    ``<table-wrap>`` or a ``<ref>`` — reaches 6,225 of the 8,118 served
    articles (76.7%) and 86,516 of the 97,909 archive ones (88.4%), where each
    of this counter's three siblings fires on a small minority. A line on
    three articles in four is noise, and the owners divide into at least four
    separate questions: an ``<aff>``'s marker (23,077 served, or 25,332
    counting ``<corresp>`` with it — one row, two scopes), a numbered
    ``<sec>``'s own number (19,462), a ``<list-item>``'s bullet (7,351) and a
    footnote marker (5,891, which is #124's). Filed with the owner table
    rather than pooled here.
    """

    def test_a_term_whose_definition_reaches_nothing_is_counted(self, parser_log):
        """A definition that falls past every branch loses its term with it.

        A ``<p>`` in a ``<floats-group>``'s ``<boxed-text>`` sits in none of
        ``<front>``, ``<body>`` or ``<back>``, so ``_append_prose`` files it
        nowhere (issue #253), and what this counter adds is that the term's half is not
        silent. The fixture was a ``<front><notes>`` definition list — 1,441
        of the 1,444 terms the counter reported over the served artifact —
        until issue #230 routed front matter, and **a counter whose measured
        population a fix takes needs a test of its own or it goes vacuous the
        same day**, which is this test's whole reason for moving rather than
        flipping.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front>
    <article-meta><title-group><article-title>Floats defs</article-title>
    </title-group></article-meta>
  </front>
  <body><sec><title>M</title><p>Body.</p></sec></body>
  <floats-group><boxed-text><def-list>
    <def-item><term>BMI</term><def><p>body mass index</p></def></def-item>
    <def-item><term>CI</term><def><p>confidence interval</p></def></def-item>
  </def-list></boxed-text></floats-group>
</article>"""

        article = JATSParser(data).parse()

        assert [p for s in article.body_sections for p in s.paragraphs] == ["Body."]
        warnings = parser_log.messages(logging.WARNING)
        assert any("2 <def-list> term(s)" in m for m in warnings), warnings

    def test_a_term_with_no_definition_at_all_is_counted(self):
        """3 of 14,186 served ``<def-item>`` carry no ``<def>``.

        Nothing routes, so nothing can carry the prefix — and the term is
        still content the document deposited and this parser read.
        """
        handler = _run_handler(
            b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>No def</article-title>
  </title-group></article-meta></front>
  <body><sec><title>A</title><def-list>
    <def-item><term>orphan</term></def-item>
  </def-list></sec></body>
</article>"""
        )

        assert handler.definition_terms_dropped == 1

    def test_a_second_term_in_one_item_does_not_silently_replace_the_first(self):
        """Bare last-wins with no line is the #116/#143 class of defect.

        JATS's own content model admits one ``<term>`` per ``<def-item>`` and
        0 of the 14,186 served items deposit two, so this pins a direction and
        not a population — but a rule that depends on the model being what
        someone remembered is the rule this module keeps being caught by.
        """
        handler = _run_handler(
            b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Two terms</article-title>
  </title-group></article-meta></front>
  <body><sec><title>A</title><def-list>
    <def-item><term>first</term><term>second</term><def><p>a sense</p></def></def-item>
  </def-list></sec></body>
</article>"""
        )

        assert handler.definition_terms_dropped == 1
        assert [p for s in handler.body_sections for p in s.paragraphs] == ["second — a sense"]

    def test_a_filed_term_is_not_counted(self, parser_log):
        """The negative control: a counter that always fires reports nothing."""
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Filed</article-title>
  </title-group></article-meta></front>
  <body><sec><title>A</title><def-list>
    <def-item><term>BMI</term><def><p>body mass index</p></def></def-item>
  </def-list></sec></body>
</article>"""

        handler = _run_handler(data)

        assert handler.definition_terms_dropped == 0
        assert not [m for m in parser_log.messages(logging.WARNING) if "term(s)" in m]

    def test_a_refused_apparatus_definition_is_reported_once(self, parser_log):
        """One drop, one count — the rule PR #232's review had to correct.

        A ``<def-list>`` inside a ``<ref-list>`` is refused as bibliography
        apparatus, which already has a line. The prefix runs *before* the
        routing, so the term goes into the refused string rather than into
        this counter: reporting both would size one loss twice, which is
        exactly what ``refused_apparatus_prose`` was caught doing for a
        ``<disp-formula>``.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Refs</article-title>
  </title-group></article-meta></front>
  <body><sec><title>M</title><p>Body.</p></sec></body>
  <back><ref-list><def-list>
    <def-item><term>cf.</term><def><p>compare</p></def></def-item>
  </def-list></ref-list></back>
</article>"""

        handler = _run_handler(data)

        assert handler.refused_apparatus_prose == 1
        assert handler.definition_terms_dropped == 0
        warnings = parser_log.messages(logging.WARNING)
        assert any("1 <ref-list> item(s) were refused" in m for m in warnings), warnings
        assert not [m for m in warnings if "term(s)" in m]

    def test_an_empty_term_that_files_nowhere_is_not_counted(self):
        """The counter says a *word* was lost, so it may not count nothing.

        An empty ``<term>`` adds no prefix wherever it lands, so counting one
        whose definition files nowhere would report a loss the document never
        deposited — the *"report what you checked"* rule, and the difference
        between this counter and a walk over ``<term>`` elements. The fixture
        sat in front matter until issue #230 routed it, which filed the
        definition and left the name false and the mutant storing ``""`` as a
        term unkilled here; a ``<floats-group>``'s ``<boxed-text>`` still files
        nowhere (issue #253).
        """
        handler = _run_handler(
            b"""<?xml version="1.0"?>
<article>
  <front>
    <article-meta><title-group><article-title>Empty floats</article-title>
    </title-group></article-meta>
  </front>
  <body><sec><title>M</title><p>Body.</p></sec></body>
  <floats-group><boxed-text><def-list><def-item><term/>
    <def><p>body mass index</p></def></def-item></def-list></boxed-text></floats-group>
</article>"""
        )

        assert handler.definition_terms_dropped == 0


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


class TestAnUndividedContributorName:
    """The two spellings of a name bmlib extracted from neither (#120, #140).

    ``_AuthorBuilder.build()`` refused anything without a ``<surname>`` and the
    call site dropped it without a word, so a ``<collab>`` consortium author
    vanished (34 of the 1,025 open-access articles drawn in the PR #118
    review lost at least one contributor; that draw counted ``<contrib>``
    elements carrying no ``<surname>``, a set both spellings share) and a
    ``<contrib-group>`` built from ``<string-name>`` parsed to *zero* authors —
    a well-formed empty list, which reads as "this article credits nobody"
    rather than as a parser that looked in the wrong place.

    Both are held verbatim in a field of their own; see
    :class:`~bmlib.fulltext.models.JATSAuthorInfo` for why they are not folded
    into ``surname``.
    """

    COLLAB_BESIDE_A_PERSON = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <title-group><article-title>A consortium paper</article-title></title-group>
    <contrib-group content-type="author">
      <contrib><name><surname>Real</surname><given-names>A</given-names></name></contrib>
      <contrib><collab>the INHERIT Trial Group</collab></contrib>
    </contrib-group>
  </article-meta></front>
</article>"""

    COLLAB_WITH_A_MEMBER_ROSTER = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <title-group><article-title>A consortium and its members</article-title></title-group>
    <contrib-group content-type="author">
      <contrib><collab>the INHERIT Trial Group
        <contrib-group>
          <contrib><name><surname>Member</surname><given-names>Bo</given-names></name></contrib>
          <contrib><name><surname>Other</surname><given-names>Cy</given-names></name></contrib>
        </contrib-group>
      </collab></contrib>
      <contrib><name><surname>After</surname><given-names>Di</given-names></name></contrib>
    </contrib-group>
  </article-meta></front>
  <body><sec><title>Results</title><p>Prose after the roster.</p></sec></body>
</article>"""

    COLLAB_WITH_A_NON_AUTHOR_ROSTER = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <title-group><article-title>A consortium and its editors</article-title></title-group>
    <contrib-group content-type="author">
      <contrib><collab>the INHERIT Trial Group
        <contrib-group content-type="editor">
          <contrib><name><surname>Editor</surname><given-names>Ed</given-names></name></contrib>
        </contrib-group>
      </collab></contrib>
      <contrib><name><surname>After</surname><given-names>Di</given-names></name></contrib>
    </contrib-group>
  </article-meta></front>
  <body><sec><title>Results</title><p>Prose after the editor roster.</p></sec></body>
</article>"""

    #: A consortium whose ``<collab>`` carries *only* a roster, so the outer
    #: ``<contrib>`` names nobody and gives its slot back while the member it
    #: encloses has already filled one. The only shape in which
    #: ``del author_slots[slot]`` differs from ``pop()`` and from
    #: ``del author_slots[-1]``: every other nameless ``<contrib>`` is last,
    #: where all three truncate the same entry.
    CONSORTIUM_NAMING_NOBODY = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <title-group><article-title>A roster with no consortium name</article-title></title-group>
    <contrib-group content-type="author">
      <contrib><collab>
        <contrib-group>
          <contrib><name><surname>Member</surname><given-names>Bo</given-names></name></contrib>
        </contrib-group>
      </collab></contrib>
      <contrib><name><surname>After</surname><given-names>Di</given-names></name></contrib>
    </contrib-group>
  </article-meta></front>
  <body><sec><title>Results</title><p>Prose after the roster.</p></sec></body>
</article>"""

    #: A roster whose members are named undivided. ``<string-name>`` merges its
    #: text back into its parent so a ``<mixed-citation>`` keeps the name it
    #: prints inline — and the nearest accumulating ancestor of a roster member
    #: is the enclosing ``<collab>``, so without the ``<contrib>`` test the
    #: members were appended to the consortium's own name.
    UNDIVIDED_ROSTER_MEMBERS = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <title-group><article-title>A consortium of undivided names</article-title></title-group>
    <contrib-group content-type="author">
      <contrib><collab>the INHERIT Trial Group
        <contrib-group>
          <contrib><string-name>Jane Q Smith</string-name></contrib>
          <contrib><string-name>Ahmed Al-Rashid</string-name></contrib>
        </contrib-group>
      </collab></contrib>
    </contrib-group>
  </article-meta></front>
  <body><sec><title>Results</title><p>Prose after the roster.</p></sec></body>
</article>"""

    #: A contributor carrying given names and no surname, and a
    #: ``<string-name>`` that divides into given names alone. Neither is
    #: reached by a ``surname``-only predicate.
    GIVEN_NAMES_WITHOUT_A_SURNAME = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <title-group><article-title>A mononym</article-title></title-group>
    <contrib-group content-type="author">
      <contrib><name><given-names>Prince</given-names></name></contrib>
      <contrib><string-name><given-names>Cher</given-names></string-name></contrib>
    </contrib-group>
  </article-meta></front>
</article>"""

    #: JATS 1.2 names a contributor with ``<on-behalf-of>`` too. bmlib does not
    #: extract it, so this article has no authors — the point is that the
    #: detector must not then certify it as naming nobody.
    ON_BEHALF_OF_ONLY = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <article-id pub-id-type="pmc">PMC7654321</article-id>
    <title-group><article-title>An attribution and no name</article-title></title-group>
    <contrib-group content-type="author">
      <contrib><on-behalf-of>the XYZ Group</on-behalf-of></contrib>
    </contrib-group>
  </article-meta></front>
</article>"""

    #: Two cited authors named with a *divided* ``<string-name>`` — JATS lets
    #: it carry ``<surname>`` and ``<given-names>`` children, and the element's
    #: own buffer then holds only the punctuation between them.
    DIVIDED_STRING_NAMES_IN_A_CITATION = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <title-group><article-title>Citing two divided names</article-title></title-group>
    <contrib-group content-type="author">
      <contrib><name><surname>Real</surname><given-names>A</given-names></name></contrib>
    </contrib-group>
  </article-meta></front>
  <back><ref-list>
    <ref id="R1"><element-citation>\
<person-group person-group-type="author">\
<string-name><surname>Smith</surname>, <given-names>J</given-names></string-name>\
<string-name><surname>Jones</surname>, <given-names>A</given-names></string-name>\
</person-group><article-title>A cited paper</article-title></element-citation></ref>
  </ref-list></back>
</article>"""

    #: A collaboration cited as a direct child of ``<mixed-citation>``, outside
    #: any ``<person-group>``.
    COLLAB_IN_A_CITATION = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <title-group><article-title>Citing a collaboration</article-title></title-group>
    <contrib-group content-type="author">
      <contrib><name><surname>Real</surname><given-names>A</given-names></name></contrib>
    </contrib-group>
  </article-meta></front>
  <back><ref-list>
    <ref id="R1"><mixed-citation><collab>the WHO Study Group</collab>. \
<article-title>A cited paper</article-title>. <year>2020</year>.</mixed-citation></ref>
  </ref-list></back>
</article>"""

    STRING_NAMES_ONLY = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <title-group><article-title>Undivided names</article-title></title-group>
    <contrib-group content-type="author">
      <contrib><string-name>Jane Q Smith</string-name></contrib>
      <contrib><string-name>Ahmed Al-Rashid</string-name></contrib>
    </contrib-group>
  </article-meta></front>
</article>"""

    STRUCTURED_STRING_NAME = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <title-group><article-title>A divided string-name</article-title></title-group>
    <contrib-group content-type="author">
      <contrib><string-name><surname>Smith</surname>, \
<given-names>Jane Q</given-names></string-name></contrib>
    </contrib-group>
  </article-meta></front>
</article>"""

    PERSON_ON_BEHALF_OF_A_GROUP = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <title-group><article-title>On behalf of</article-title></title-group>
    <contrib-group content-type="author">
      <contrib>
        <name><surname>Smith</surname><given-names>Jane</given-names></name>
        <collab>on behalf of the Y Group</collab>
      </contrib>
    </contrib-group>
  </article-meta></front>
</article>"""

    NAMELESS_CONTRIB = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <article-id pub-id-type="pmc">PMC9000001</article-id>
    <title-group><article-title>A contributor with no name at all</article-title></title-group>
    <contrib-group content-type="author">
      <contrib><name><surname>Real</surname><given-names>A</given-names></name></contrib>
      <contrib><xref ref-type="aff" rid="aff1"/></contrib>
    </contrib-group>
  </article-meta></front>
</article>"""

    STRING_NAME_IN_A_CITATION = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <title-group><article-title>Citing an undivided name</article-title></title-group>
    <contrib-group content-type="author">
      <contrib><name><surname>Real</surname><given-names>A</given-names></name></contrib>
    </contrib-group>
  </article-meta></front>
  <back><ref-list>
    <ref id="R1"><mixed-citation><string-name>Smith J</string-name>. \
<article-title>A cited paper</article-title>. <source>J Test</source>. <year>2020</year>.\
</mixed-citation></ref>
    <ref id="R2"><mixed-citation>\
<person-group person-group-type="author"><string-name>Doe A</string-name></person-group>. \
<article-title>Another cited paper</article-title>.</mixed-citation></ref>
  </ref-list></back>
</article>"""

    @staticmethod
    def _names(data: bytes) -> list[str]:
        return [a.full_name for a in JATSParser(data).parse().authors]

    def test_a_collaboration_is_collected_beside_a_person(self):
        assert self._names(self.COLLAB_BESIDE_A_PERSON) == ["A Real", "the INHERIT Trial Group"]

    def test_a_collaboration_lands_in_its_own_field(self):
        authors = JATSParser(self.COLLAB_BESIDE_A_PERSON).parse().authors

        assert authors[1].collab == "the INHERIT Trial Group"
        assert authors[1].surname == ""
        assert authors[1].string_name == ""

    def test_a_collaboration_is_listed_before_the_members_it_encloses(self):
        """Document order, which append-at-close would invert.

        A ``<collab>`` may carry a ``<contrib-group>`` of its own members, so
        the enclosing ``<contrib>`` closes *after* every one of them. Appending
        at the end tag would list the consortium last — behind contributors it
        contains — so the slot is reserved where the ``<contrib>`` opened and
        filled where it closed, exactly as an exhibit's is (#115).
        """
        assert self._names(self.COLLAB_WITH_A_MEMBER_ROSTER) == [
            "the INHERIT Trial Group",
            "Bo Member",
            "Cy Other",
            "Di After",
        ]

    def test_a_contributor_after_a_nested_roster_is_still_collected(self):
        """The other edge: the nested close must not strand the outer contrib.

        Held as a single slot and a stored flag, the inner ``<contrib>``
        elements overwrote the outer builder and cleared ``in_contrib`` before
        ``</collab>`` was reached — so the consortium was lost even with a
        field to put it in. A fixture that stops at the roster cannot see this;
        the ``After`` contributor and the body prose below are what pin the
        stack unwinding to the right depth.
        """
        article = JATSParser(self.COLLAB_WITH_A_MEMBER_ROSTER).parse()

        assert article.authors[-1].full_name == "Di After"
        assert article.body_sections[0].paragraphs == ["Prose after the roster."]

    def test_a_nested_contributor_bmlib_does_not_collect_leaves_the_frame_alone(self):
        """The ``None`` frames, which two separate one-line edits get wrong.

        A ``<contrib>`` bmlib is *not* collecting still pushes a frame, and
        ``current_author`` reads the top of the stack rather than the nearest
        entry that happens to hold a builder. Skip the push and this editor's
        end tag pops the consortium's own frame, building it before
        ``</collab>`` has written its name; walk past the ``None`` instead and
        the editor's ``<surname>`` is written into the consortium's builder and
        wins the rendering. Both leave the article with the wrong contributor
        and neither is visible in a fixture whose nesting is all one role.

        The contributor *after* the roster is the other edge: a ``None`` frame
        has to pop as well as push, and a fixture stopping at ``</collab>``
        pins only the push. The body prose is the same test for the text
        buffers the roster opened.
        """
        article = JATSParser(self.COLLAB_WITH_A_NON_AUTHOR_ROSTER).parse()

        assert [a.full_name for a in article.authors] == [
            "the INHERIT Trial Group",
            "Di After",
        ]
        assert article.body_sections[0].paragraphs == ["Prose after the editor roster."]

    def test_an_undivided_personal_name_is_collected(self):
        assert self._names(self.STRING_NAMES_ONLY) == ["Jane Q Smith", "Ahmed Al-Rashid"]

    def test_an_undivided_personal_name_is_not_split(self):
        """Verbatim, and in the field that says it is undivided.

        Splitting means deciding about particles and name order, which is
        assumed rather than measured — and a caller cannot tell a guess from a
        deposit once it is sitting in ``surname``.
        """
        authors = JATSParser(self.STRING_NAMES_ONLY).parse().authors

        assert authors[1].string_name == "Ahmed Al-Rashid"
        assert authors[1].surname == ""
        assert authors[1].given_names == ""

    def test_a_string_name_with_structured_children_keeps_using_them(self):
        """JATS permits ``<string-name>`` to divide, and where it does it wins.

        The undivided field is filled only when no ``<surname>`` arrived, or
        this deposit would put the comma between the two children into it.
        """
        author = JATSParser(self.STRUCTURED_STRING_NAME).parse().authors[0]

        assert (author.surname, author.given_names) == ("Smith", "Jane Q")
        assert author.string_name == ""

    def test_a_person_on_behalf_of_a_group_is_one_contributor(self):
        article = JATSParser(self.PERSON_ON_BEHALF_OF_A_GROUP).parse()

        assert [a.full_name for a in article.authors] == ["Jane Smith"]
        assert article.authors[0].collab == "on behalf of the Y Group"

    def test_a_contributor_with_no_name_at_all_is_dropped_and_said_so(self, parser_log):
        """#120's "no log, no counter" half.

        Nothing can be built from a ``<contrib>`` carrying none of the three
        spellings, so it is still dropped — but silently dropping it is what
        made the two spellings above invisible for as long as they were.
        """
        article = JATSParser(self.NAMELESS_CONTRIB).parse()

        assert [a.full_name for a in article.authors] == ["A Real"]
        assert any(
            "yielded no name bmlib could read" in m for m in parser_log.messages(logging.WARNING)
        )

    def test_a_collaboration_reaches_the_rendered_html(self):
        """The half that persists: ``FullTextService`` caches this HTML.

        ``JATSArticle.authors`` reaches no other bmlib path, so the author line
        in ``to_html()`` is where a dropped consortium was actually costing a
        downstream something.
        """
        html = JATSParser(self.COLLAB_BESIDE_A_PERSON).to_html()

        assert "the INHERIT Trial Group" in html

    def test_a_cited_string_name_reaches_the_reference_authors(self):
        """The same spelling one branch over, where ``<collab>`` already worked."""
        references = JATSParser(self.STRING_NAME_IN_A_CITATION).parse().references

        assert references[1].authors == ["Doe A"]

    def test_a_cited_string_name_stays_in_the_citation_string(self):
        """Reading the element must not remove its text from the citation.

        The two halves of how ``<string-name>`` is now handled fail different
        assertions. It **accumulates a buffer of its own**, so
        ``</string-name>`` reads its own text rather than whatever the
        ancestor's buffer happened to hold — without that, the test above
        collects the enclosing ``<person-group>``'s accumulated prose. And it
        **merges that buffer back into its parent**, because a
        ``<mixed-citation>`` may print a bare ``<string-name>`` as part of the
        citation it renders: accumulating without merging silently deletes the
        author from every such reference, which is a regression this fix would
        otherwise have introduced while closing #140.
        """
        references = JATSParser(self.STRING_NAME_IN_A_CITATION).parse().references

        assert references[0].citation.startswith("Smith J")

    def test_a_cited_name_outside_a_person_group_is_still_a_cited_author(self):
        """The two undivided spellings are gated alike, on the whole citation.

        JATS admits either as a direct child of ``<mixed-citation>`` and
        ``<element-citation>``. Gated on ``in_ref_person_group`` — a strict
        subset of ``in_ref_citation``, which is what ``<collab>`` uses — a
        ``<string-name>`` sitting in the markup produced an empty ``authors``
        with nothing logged, which is the failure direction #120 and #140 are
        about.
        """
        references = JATSParser(self.STRING_NAME_IN_A_CITATION).parse().references

        assert references[0].authors == ["Smith J"]

    def test_a_cited_collaboration_reaches_the_reference_authors(self):
        """The spelling ``<string-name>``'s reference branch is modelled on.

        Asserted rather than assumed: the claim that ``<collab>`` "already
        worked" here was made in prose by the fix beside it and by nothing
        else, so deleting this branch outright was a green change.
        """
        references = JATSParser(self.COLLAB_IN_A_CITATION).parse().references

        assert references[0].authors == ["the WHO Study Group"]

    def test_a_cited_collaboration_stays_in_the_citation_string(self):
        """The other edge of the merge, for the other undivided spelling.

        ``<collab>`` accumulated a buffer without being inline, so its text was
        taken from the citation and never returned — the exact defect the
        ``<string-name>`` entry beside it was added to avoid, one line up in
        the same set, costing every consortium-authored reference its author.
        """
        references = JATSParser(self.COLLAB_IN_A_CITATION).parse().references

        assert references[0].citation.startswith("the WHO Study Group")

    def test_a_divided_cited_name_adds_no_punctuation_author(self):
        """A ``<string-name>``'s own buffer is not a name when it divides.

        Its ``<surname>`` and ``<given-names>`` children route through their
        own arms, so the element's buffer holds only what sits between them —
        a comma. Appending that put a bare ``","`` in ``authors``, *ahead* of
        the name itself, and rendered it into the reference list bmlib caches.
        """
        references = JATSParser(self.DIVIDED_STRING_NAMES_IN_A_CITATION).parse().references

        assert references[0].authors == ["J Smith", "A Jones"]

    def test_two_divided_cited_names_do_not_collapse_onto_the_last(self):
        """The same close has to *flush*, not merely refrain from appending.

        Only ``</name>`` and ``</person-group>`` finish a pending cited author,
        and neither closes between two adjacent ``<string-name>`` — so the
        first one's parts were overwritten by the second's and that author was
        lost outright, silently and independently of the punctuation above.
        """
        references = JATSParser(self.DIVIDED_STRING_NAMES_IN_A_CITATION).parse().references

        assert "J Smith" in references[0].authors

    def test_a_roster_of_undivided_names_leaves_the_consortium_alone(self):
        """An undivided name inside a ``<contrib>`` belongs to that contributor.

        ``<string-name>`` merges its buffer back into its parent so a
        ``<mixed-citation>`` keeps a name it prints inline. The nearest
        accumulating ancestor of a roster member is the enclosing
        ``<collab>``, so an unconditional merge appended every member to the
        consortium's own name — *"the INHERIT Trial GroupJane Q SmithAhmed
        Al-Rashid"* — silently, in the very shape #120 exists to collect.
        """
        authors = JATSParser(self.UNDIVIDED_ROSTER_MEMBERS).parse().authors

        assert [a.full_name for a in authors] == [
            "the INHERIT Trial Group",
            "Jane Q Smith",
            "Ahmed Al-Rashid",
        ]
        assert authors[0].collab == "the INHERIT Trial Group"

    def test_a_roster_member_survives_a_consortium_that_names_nobody(self):
        """The give-back deletes *its own* slot, not the last one.

        A ``<contrib>`` naming nobody that **encloses** one that does is the
        only shape in which ``del author_slots[slot]`` differs from ``pop()``
        or ``del author_slots[-1]``: everywhere else the nameless contributor
        is last. Both of those edits took the member's filled slot instead,
        losing the contributor and leaving the audit to ERROR on a document
        bmlib had read correctly.
        """
        article = JATSParser(self.CONSORTIUM_NAMING_NOBODY).parse()

        assert [a.full_name for a in article.authors] == ["Bo Member", "Di After"]

    def test_a_contributor_named_only_by_given_names_is_collected(self):
        """A mononym has no ``<surname>``, and neither does half of ``build()``.

        Dropped, the article is *also* certified author-less, because the
        ``<front>`` counter reads ``<surname>``, ``<collab>``,
        ``<string-name>`` and ``<on-behalf-of>`` — not ``<given-names>``. That
        is #121's silence with a green suite.
        """
        assert self._names(self.GIVEN_NAMES_WITHOUT_A_SURNAME) == ["Prince", "Cher"]

    def test_a_string_name_that_divides_into_given_names_alone_stays_structured(self):
        """The second half of the "did a structured name arrive?" guard.

        Tested against ``surname`` alone the guard short-circuits, and a
        ``<string-name>`` carrying only ``<given-names>`` puts its leftover
        buffer into ``string_name`` beside the name it already divided into.
        """
        author = JATSParser(self.GIVEN_NAMES_WITHOUT_A_SURNAME).parse().authors[1]

        assert author.given_names == "Cher"
        assert author.string_name == ""

    def test_a_contributor_named_only_by_on_behalf_of_is_not_certified_authorless(self, parser_log):
        """A fourth spelling, and the detector must not conclude past it.

        bmlib does not extract ``<on-behalf-of>``, so this article has no
        authors either way. What #120 and #140 cost was the *quiet* branch
        being reached — an article whose only contributor was named in a
        spelling bmlib did not read, reported as naming nobody. Counting the
        spelling is what keeps the loud branch loud while extraction waits.
        """
        article = JATSParser(self.ON_BEHALF_OF_ONLY).parse()

        assert article.authors == []
        assert any(
            "named" in m and "contributor(s)" in m for m in parser_log.messages(logging.WARNING)
        )


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
        sees ``JATSParser``; it makes the same rule on the raw string.)

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
    loses its number wherever it is rendered or cross-referenced, and an
    overwritten label is not inert either — the marker is rendered as the
    table's own number, so the symptom is a *wrong* number rather than a
    blank. Since #162 the renderer invents nothing for an exhibit carrying no
    label of its own, which makes mis-routing the only remaining route to an
    invented number.
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


class TestAnExhibitFootnoteReachesTheExhibit:
    """A ``<table-wrap-foot>``'s prose is the table's, and the marker with it.

    ``JATSFigureInfo`` and ``JATSTableInfo`` had no ``footnotes`` field and
    nothing collected one, so a ``<table-wrap-foot><fn>``'s prose reached
    neither the rendered table, nor its caption, nor the article body: the
    ``<p>`` handler dropped it as exhibit internals, which is right for a cell
    and wrong for a note (issue #124). Table footnotes carry the abbreviation
    expansions without which the cells are unreadable, and the per-table
    funding and disclosure notes ``bmlib.transparency`` scans for.

    **The marker is kept and folded into the prose**, ``"a — Adjusted for
    age."``, rather than dropped. ``<sup>`` is an inline element flattened
    into the surrounding cell, so the rendered body still reads ``12.3a`` and
    with two footnotes the mapping back is otherwise unrecoverable — a
    reference to nothing, which is #116's *"a swallowed marker is not a
    blank"* one element down. It is the shape issue #228 settled one
    container over: fold the marker into the prose it belongs to rather than
    grow a second public model for it.

    Measured over Europe PMC's ``PMC10030002_PMC10040000.xml.gz`` (8,118
    served articles, the rendition ``FullTextService`` feeds this parser):
    this parser files **16,935 notes in 3,707 articles (45.7%) and 2.37 MB of
    prose**, of which 3,102 carry a marker. That is the routing tally, taken
    at ``append_footnote``; the markup survey's 16,947 over-counts by 12 where
    a note deposits a ``<def-list>`` inside a ``<p>``, and is quoted only for
    the separator, which is a question about the deposit (PR #237's review).
    """

    def test_a_table_footnote_reaches_the_table(self):
        """Issue #124's own fixture, and the assertion it prints as False."""
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="T1"><label>Table 1.</label>
        <caption><p>Outcomes.</p></caption>
        <table><tbody><tr><td>12.3<sup>a</sup></td></tr></tbody></table>
        <table-wrap-foot>
          <fn id="fn1"><label>a</label>
            <p>Adjusted for age. Funded by NHMRC grant 123.</p></fn>
        </table-wrap-foot>
      </table-wrap>
    </sec>""")
        ).parse()

        assert [t.footnotes for t in article.tables] == [
            ["a — Adjusted for age. Funded by NHMRC grant 123."]
        ]

    def test_the_caption_is_still_only_the_caption(self):
        """The note must not arrive by widening what a caption collects.

        Both reach ``_append_prose`` through the same exhibit branch, so a fix
        that merely stopped dropping footnote prose would put the funding note
        in the field a renderer prints under the table's number.
        """
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="T1"><label>Table 1.</label>
        <caption><p>Outcomes.</p></caption>
        <table><tbody><tr><td>12.3</td></tr></tbody></table>
        <table-wrap-foot><fn><label>a</label><p>Adjusted for age.</p></fn></table-wrap-foot>
      </table-wrap>
    </sec>""")
        ).parse()

        assert [t.caption for t in article.tables] == ["Outcomes."]

    def test_each_footnote_keeps_its_own_marker(self):
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="T1"><label>Table 1.</label>
        <table><tbody><tr><td>12.3</td></tr></tbody></table>
        <table-wrap-foot>
          <fn><label>a</label><p>Adjusted for age.</p></fn>
          <fn><label>b</label><p>Two patients excluded.</p></fn>
        </table-wrap-foot>
      </table-wrap>
    </sec>""")
        ).parse()

        assert [t.footnotes for t in article.tables] == [
            ["a — Adjusted for age.", "b — Two patients excluded."]
        ]

    def test_a_note_of_several_paragraphs_is_marked_once(self):
        """``append_footnote``'s documented contract, asserted directly.

        The marker is spent by the *first* paragraph of its own ``<fn>``, so a
        continuation arrives unmarked — which is where a reader expects the
        marker and what the sibling Swift port does. Until this fixture the
        rule was pinned only *through* the counter: a mutant repeating the
        marker was caught because it left the slot dirty and tripped the
        dropped-marker WARNING, not because anything read the stored value
        (PR #237's review).
        """
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="T1"><label>Table 1.</label>
        <table><tbody><tr><td>12.3</td></tr></tbody></table>
        <table-wrap-foot>
          <fn><label>a</label><p>Adjusted for age.</p><p>Two excluded.</p></fn>
        </table-wrap-foot>
      </table-wrap>
    </sec>""")
        ).parse()

        assert [t.footnotes for t in article.tables] == [["a — Adjusted for age.", "Two excluded."]]

    def test_an_unmarked_footnote_carries_no_separator(self):
        """No marker, no fold — never a leading ``" — "`` over nothing."""
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="T1"><label>Table 1.</label>
        <table><tbody><tr><td>12.3</td></tr></tbody></table>
        <table-wrap-foot><fn><p>Adjusted for age.</p></fn></table-wrap-foot>
      </table-wrap>
    </sec>""")
        ).parse()

        assert [t.footnotes for t in article.tables] == [["Adjusted for age."]]

    def test_a_general_note_outside_any_fn_is_collected_too(self):
        """A ``<p>`` directly in ``<table-wrap-foot>``, which is why the
        predicate names the container and not only ``<fn>``.

        It is what publishers deposit for the note that follows the last
        marked footnote, and it is not rare: 5,901 such paragraphs in 1,386 of
        the 8,118 served articles (17.1%).
        """
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="T1"><label>Table 1.</label>
        <table><tbody><tr><td>12.3</td></tr></tbody></table>
        <table-wrap-foot>
          <fn><label>a</label><p>Adjusted for age.</p></fn>
          <p>Values are mean (SD).</p>
        </table-wrap-foot>
      </table-wrap>
    </sec>""")
        ).parse()

        assert [t.footnotes for t in article.tables] == [
            ["a — Adjusted for age.", "Values are mean (SD)."]
        ]

    def test_a_footnote_group_inside_the_foot_is_collected(self):
        """``<fn-group>`` is the third container, and JATS admits it here."""
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="T1"><label>Table 1.</label>
        <table><tbody><tr><td>12.3</td></tr></tbody></table>
        <table-wrap-foot><fn-group><label>Notes</label>
          <fn><label>a</label><p>Adjusted for age.</p></fn></fn-group></table-wrap-foot>
      </table-wrap>
    </sec>""")
        ).parse()

        assert [t.footnotes for t in article.tables] == [["a — Adjusted for age."]]
        assert [t.label for t in article.tables] == ["Table 1."]

    def test_a_footnote_groups_own_heading_is_not_a_marker(self):
        """The marker's parent test, and its mutant is inert without this.

        A ``<fn-group>`` carries a heading of its own — "Notes",
        "Abbreviations" — as a ``<label>``, and that is no more a note's marker
        than it is the table's number (which is what #116 settled). Widening
        the parent test to admit ``<fn-group>`` survives every other fixture
        here, because a note carrying its *own* ``<label>`` overwrites the
        leaked heading before any prose arrives. Only a note with no marker of
        its own separates the two.
        """
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="T1"><label>Table 1.</label>
        <table><tbody><tr><td>12.3</td></tr></tbody></table>
        <table-wrap-foot><fn-group><label>Notes</label>
          <fn><p>Adjusted for age.</p></fn></fn-group></table-wrap-foot>
      </table-wrap>
    </sec>""")
        ).parse()

        assert [t.footnotes for t in article.tables] == [["Adjusted for age."]]

    def test_a_figures_footnote_group_needs_no_foot_wrapper(self):
        """Why ``<fn-group>`` is a member in its own right.

        JATS admits one directly in a ``<fig>``, where there is no
        ``<table-wrap-foot>`` to be found by — so leaving the group to the foot
        element passes every table fixture and loses the figure's notes.
        """
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <fig id="f1"><label>Figure 1.</label>
        <fn-group><fn><label>*</label><p>Scale bar 10um.</p></fn></fn-group>
      </fig>
    </sec>""")
        ).parse()

        assert [f.footnotes for f in article.figures] == [["* — Scale bar 10um."]]

    def test_a_loose_paragraph_in_a_figures_footnote_group_is_collected(self):
        """The shape that makes ``<fn-group>`` a member in its own right.

        JATS models it ``(label?, title?, (fn|p)+)``, so a ``<p>`` may sit
        directly in the group. Inside a ``<table-wrap>`` the foot element
        above it still answers, and inside a ``<fig>`` with a ``<fn>`` in it
        the note answers — so only a loose paragraph in a figure's group has
        neither, which is what separates this membership from its own mutant.
        """
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <fig id="f1"><label>Figure 1.</label>
        <fn-group><p>Values are mean (SD).</p></fn-group>
      </fig>
    </sec>""")
        ).parse()

        assert [f.footnotes for f in article.figures] == [["Values are mean (SD)."]]

    def test_an_empty_paragraph_does_not_spend_the_marker(self):
        """``keep_empty=True`` appends an empty ``<p>`` a document deposited,
        and folding a marker onto it would spend the word on a paragraph that
        says nothing — leaving the note that follows unmarked. The same edge
        #228's own fold needed, one container over.
        """
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="T1"><label>Table 1.</label>
        <table><tbody><tr><td>12.3</td></tr></tbody></table>
        <table-wrap-foot><fn><label>a</label><p></p><p>Adjusted for age.</p></fn></table-wrap-foot>
      </table-wrap>
    </sec>""")
        ).parse()

        assert [t.footnotes for t in article.tables] == [["a — Adjusted for age."]]

    def test_a_figures_footnote_reaches_the_figure(self):
        """JATS admits ``<fn>`` directly in ``<fig>``, with no foot wrapper.

        The served rendition deposits almost none — 2 notes in 8,118
        articles against 16,933 on the table side — so this pins a direction
        and not a population, and the shared holder is what keeps the two
        exhibits from drifting apart while one of them is unexercised.
        """
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <fig id="f1"><label>Figure 1.</label>
        <caption><p>A figure.</p></caption>
        <fn><label>*</label><p>Scale bar 10um.</p></fn>
      </fig>
    </sec>""")
        ).parse()

        assert [f.footnotes for f in article.figures] == [["* — Scale bar 10um."]]
        assert [f.caption for f in article.figures] == ["A figure."]


class TestAFootnoteBelongsToTheExhibitThatEnclosesIt:
    """Which exhibit a footnote is filed on is an *ancestor* question.

    The Swift port routed this on a parser-wide footnote depth and the
    counter still stood at the outer table's depth while an inner
    ``<table-wrap>`` was being parsed, so the inner table's own cell ``<p>``
    took the footnote branch and was filed as a footnote — rendered twice,
    once in the cell and once below it (bmlibrarian_lite#173). bmlib walks
    outward from the closing element and answers with whichever it meets
    first, so an exhibit met before any footnote container ends the walk.

    Both halves are load-bearing. Stopping at the exhibit keeps a
    ``<back><fn-group><fn>`` — prose belonging to no exhibit — out of one.
    Requiring the footnote *before* the exhibit keeps an exhibit nested inside
    another's footnote from inheriting it.

    Neither corpus deposits the nesting: 0 exhibits open inside another's
    footnote across the 8,118 served articles. So these pin a direction rather
    than a population, which is what the module already says of the nesting
    rules beside them.
    """

    def test_a_nested_tables_cells_are_not_the_outer_tables_footnotes(self):
        """The exact shape the sibling port's depth counter got wrong.

        The cells must contain ``<p>``: bare ``<td>`` text never reaches the
        branch under test.
        """
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="T1"><label>Table 1.</label>
        <table><tbody><tr><td>12.3</td></tr></tbody></table>
        <table-wrap-foot><fn><label>a</label>
          <p>Adjusted for age.</p>
          <table-wrap id="T2"><label>Table 2.</label>
            <table><tbody><tr><td><p>Inner cell.</p></td></tr></tbody></table>
          </table-wrap>
        </fn></table-wrap-foot>
      </table-wrap>
    </sec>""")
        ).parse()

        by_label = {t.label: t.footnotes for t in article.tables}
        assert by_label == {"Table 1.": ["a — Adjusted for age."], "Table 2.": []}

    def test_a_figure_inside_a_footnote_does_not_inherit_it(self):
        """Its caption is its own and it collects no footnote of the table's."""
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="T1"><label>Table 1.</label>
        <table><tbody><tr><td>12.3</td></tr></tbody></table>
        <table-wrap-foot><fn><label>a</label>
          <fig id="ffn"><label>Figure S1.</label>
            <caption><p>A figure inside a footnote.</p></caption></fig>
        </fn></table-wrap-foot>
      </table-wrap>
    </sec>""")
        ).parse()

        assert [f.footnotes for f in article.figures] == [[]]
        assert [f.caption for f in article.figures] == ["A figure inside a footnote."]

    def test_a_nested_figures_own_prose_is_not_the_outer_tables_note(self):
        """The exhibit must *end* the walk, not merely fail to claim it.

        Continuing past a ``<fig>`` met with no container seen lets the walk
        reach the outer ``<fn>`` and file the nested figure's own prose as the
        table's note. The sibling test using a ``<caption>`` cannot pin this
        since the caption branch is asked first and never consults the walk —
        so the fixture deposits a bare ``<p>`` in the ``<fig>`` instead. That
        prose reaches nothing either way (issue #177's float shape); what is
        under test is that it does not reach the *wrong* exhibit.
        """
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="T1"><label>Table 1.</label>
        <table><tbody><tr><td>12.3</td></tr></tbody></table>
        <table-wrap-foot><fn><label>a</label>
          <fig id="ffn"><label>Figure S1.</label><p>Figure prose.</p></fig>
          <p>Adjusted for age.</p>
        </fn></table-wrap-foot>
      </table-wrap>
    </sec>""")
        ).parse()

        assert [t.footnotes for t in article.tables] == [["a — Adjusted for age."]]
        assert [f.footnotes for f in article.figures] == [[]]

    def test_the_outer_footnote_resumes_after_the_nested_exhibit_closes(self):
        """The walk answers per element, so the outer note is still the outer
        table's once the inner exhibit has closed."""
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="T1"><label>Table 1.</label>
        <table><tbody><tr><td>12.3</td></tr></tbody></table>
        <table-wrap-foot><fn><label>a</label>
          <fig id="ffn"><label>Figure S1.</label></fig>
          <p>Adjusted for age.</p>
        </fn></table-wrap-foot>
      </table-wrap>
    </sec>""")
        ).parse()

        assert [t.footnotes for t in article.tables] == [["a — Adjusted for age."]]

    def test_a_back_matter_footnote_belongs_to_no_exhibit(self):
        """``<back><fn-group><fn>`` is the article's, and #224 routes it there."""
        article = JATSParser(
            b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>T</article-title>
  </title-group></article-meta></front>
  <body><sec><title>Methods</title><p>Prose.</p></sec>
    <fig id="f1"><label>Figure 1.</label></fig></body>
  <back><fn-group><fn><label>1</label><p>Competing interests: none.</p></fn></fn-group></back>
</article>"""
        ).parse()

        assert [f.footnotes for f in article.figures] == [[]]
        assert "Competing interests: none." in [
            p for s in article.body_sections for p in s.paragraphs
        ]

    def test_an_unmodelled_captions_legend_is_not_the_tables_note(self):
        """The one shape where the two destinations overlap, and why the
        caption is asked first.

        A ``<fig>`` or ``<table-wrap>`` opened inside a footnote ends the owner
        walk on its own, so an overlap needs a caption-carrying element this
        module does *not* model — a ``<supplementary-material>`` in an
        ``<fn>``. Its legend describes the supplementary file and not the
        table, so filing it as the table's note is a wrong value where the
        alternative is a blank, and ``_append_caption_text``'s standing rule
        already says text whose caption has no modelled owner belongs to
        nobody.

        Measured 0 in the 8,118 served articles, so it pins a direction and
        moves nothing stored.
        """
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="T1"><label>Table 1.</label>
        <table><tbody><tr><td>12.3</td></tr></tbody></table>
        <table-wrap-foot><fn><label>a</label>
          <supplementary-material id="s1"><caption><p>Source data.</p></caption>
          </supplementary-material>
          <p>Adjusted for age.</p>
        </fn></table-wrap-foot>
      </table-wrap>
    </sec>""")
        ).parse()

        assert [t.footnotes for t in article.tables] == [["a — Adjusted for age."]]
        assert [t.caption for t in article.tables] == [""]

    def test_a_cells_prose_is_not_a_footnote(self):
        """The invariant the exhibit branch existed for: furniture stays out.

        ``characters()`` already collects a cell into the rendered table, so a
        cell reaching ``footnotes`` renders the same text twice.
        """
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="T1"><label>Table 1.</label>
        <table><tbody><tr><td><p>A cell.</p></td></tr></tbody></table>
      </table-wrap>
    </sec>""")
        ).parse()

        assert [t.footnotes for t in article.tables] == [[]]
        assert "A cell." in article.tables[0].html_content

    def test_a_footnote_inside_a_cell_is_not_the_tables_note(self):
        """A cell ends the owner walk, so the note is not rendered twice.

        JATS admits an ``<fn>`` inside a ``<td>``. Without the cell arm the
        walk sets its container flag on that ``<fn>`` and carries on outward
        past the cell to the ``<table-wrap>`` — while ``characters()`` has
        *already* delivered the same text to ``append_cell_text``, which is
        gated on ``in_cell`` alone. The note would then appear in the cell and
        again in the footnote block, which is bmlibrarian_lite#173's own
        symptom reached by a different route and the exact invariant the
        neighbouring test pins for a bare cell ``<p>``.

        Measured 0 of 8,118 served and 0 of 97,909 archive articles, so this
        pins a direction rather than a population (PR #237's review).
        """
        article, html = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="T1"><label>Table 1.</label>
        <table><tbody><tr>
          <td>12.3<fn id="f1"><label>a</label><p>Adjusted for age.</p></fn></td>
        </tr></tbody></table>
      </table-wrap>
    </sec>""")
        ).parse_with_html()

        assert [t.footnotes for t in article.tables] == [[]]
        assert html.count("Adjusted for age.") == 1

    def test_a_nested_tables_own_note_is_not_the_outer_tables(self):
        """Ownership is the *innermost* exhibit, which is the walk's headline.

        The docstring argues that a parser-wide footnote depth cannot answer a
        question about the innermost exhibit and the walk can. That property is
        carried by ``current_table`` reading the top of the stack, and until
        this fixture nothing pinned it: taking the *outermost* instead passed
        the whole suite while moving the inner note onto the outer table and
        emptying the inner one (PR #237's review).
        """
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="T1"><label>Table 1.</label>
        <table><tbody><tr><td>1</td></tr></tbody></table>
        <table-wrap-foot><fn><label>a</label>
          <table-wrap id="T2"><label>Table 2.</label>
            <table><tbody><tr><td>2</td></tr></tbody></table>
            <table-wrap-foot><fn><label>b</label>
              <p>Inner note.</p></fn></table-wrap-foot>
          </table-wrap>
          <p>Outer note.</p></fn></table-wrap-foot>
      </table-wrap>
    </sec>""")
        ).parse()

        assert {t.id: t.footnotes for t in article.tables} == {
            "T1": ["a — Outer note."],
            "T2": ["b — Inner note."],
        }

    def test_a_figure_in_a_tables_footnote_keeps_its_own_note(self):
        """Which *kind* of exhibit the walk met decides, not which is open.

        Both stacks are non-empty here, so a walk that answered with the wrong
        one would file the figure's note on the table and leave the figure
        empty — and it would take the outer table's marker with it. Nothing
        pinned that either: every other fixture decides ownership with one
        exhibit type open (PR #237's review).
        """
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="T1"><label>Table 1.</label>
        <table><tbody><tr><td>1</td></tr></tbody></table>
        <table-wrap-foot><fn><label>a</label>
          <fig id="F1"><label>Figure S1.</label>
            <fn><label>*</label><p>Figure note.</p></fn>
          </fig>
          <p>Outer note.</p></fn></table-wrap-foot>
      </table-wrap>
    </sec>""")
        ).parse()

        assert [t.footnotes for t in article.tables] == [["a — Outer note."]]
        assert [f.footnotes for f in article.figures] == [["* — Figure note."]]

    def test_a_footnote_only_body_still_reports_no_body(self):
        """A note is the exhibit's content, and an exhibit is not a body.

        ``has_body`` is what stops ``FullTextService`` caching a document that
        is front matter plus exhibits and going no further down the tier
        chain, so a note counting towards it would end the chain on an article
        with no prose. 45.7% of the served corpus now flows through this
        branch and nothing pinned the counter (PR #237's review).
        """
        article = JATSParser(
            _article_with_body("""
    <table-wrap id="T1"><label>Table 1.</label>
      <table><tbody><tr><td>1</td></tr></tbody></table>
      <table-wrap-foot><fn><label>a</label>
        <p>Adjusted for age.</p></fn></table-wrap-foot>
    </table-wrap>""")
        ).parse()

        assert [t.footnotes for t in article.tables] == [["a — Adjusted for age."]]
        assert article.has_body is False


class TestAFootnoteMarkerThatCouldNotBeFiledIsReported:
    """A marker read with no prose to fold it into is counted, never carried.

    ``pending_footnote_label`` is spent by the first paragraph of its own
    ``<fn>``. Left pending it would prefix whatever footnote prose arrived
    next — the marker of one note printed on another, which is #228's own
    hazard one container over and a *wrong* value where the alternative is a
    blank. So ``</fn>`` gives an unspent marker back and counts it, and a
    second ``<label>`` in one ``<fn>`` counts the marker it displaces.

    **The counter is wholly prospective and this docstring says so**, because
    an earlier draft did not and disagreed with the CHANGELOG: 1 of the 10,763
    ``<fn>`` inside an exhibit in the 8,118 served articles carries no prose,
    and 11 of 137,735 in the archive — and every one of those twelve carries
    no marker either, so the counter reads **0** over both artifacts. The
    displacement half measures **0 of 8,118 served and 0 of 97,909 archive**
    ``<fn>`` carrying two ``<label>``. A direction, not a rate (PR #237's
    review).
    """

    def test_a_marker_with_no_prose_does_not_reach_the_next_footnote(self):
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="T1"><label>Table 1.</label>
        <table><tbody><tr><td>12.3</td></tr></tbody></table>
        <table-wrap-foot>
          <fn><label>a</label></fn>
          <fn><p>Two patients excluded.</p></fn>
        </table-wrap-foot>
      </table-wrap>
    </sec>""")
        ).parse()

        # The second note deposits no marker of its own **on purpose**: giving
        # it one hides *this* leak behind the give-back, since its `</label>`
        # would reach a slot `</fn>` had already emptied and nothing would
        # distinguish the two arms. Counting the marker without giving it back
        # then passes — a survivor of exactly the shape #228's `<term>` guard
        # had.
        assert [t.footnotes for t in article.tables] == [["Two patients excluded."]]

    def test_a_second_label_does_not_overwrite_the_first_marker_in_silence(self, parser_log):
        """A displaced marker is counted, never overwritten (PR #237's review).

        JATS models ``<fn>`` as ``(label?, …)``, so a second ``<label>`` is
        invalid and *not* ill-formed — expat validates no content model. Bare
        last-wins put the second marker on the first note's prose with nothing
        counted, which is the ``<term>`` arm's own defect one container over
        and what its comment calls *"a rule resting on a remembered content
        model"*.
        """
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="T1"><label>Table 1.</label>
        <table><tbody><tr><td>12.3</td></tr></tbody></table>
        <table-wrap-foot>
          <fn><label>a</label><label>b</label><p>Adjusted.</p></fn>
        </table-wrap-foot>
      </table-wrap>
    </sec>""")
        ).parse()

        assert [t.footnotes for t in article.tables] == [["b — Adjusted."]]
        assert any("1 footnote marker(s)" in m for m in parser_log.messages(logging.WARNING))

    def test_an_empty_label_does_not_erase_a_marker_in_silence(self, parser_log):
        """``""`` is the slot's absent spelling, so an empty ``<label>`` is the
        one route by which a marker could vanish with no line at all.

        Erased rather than displaced, the note renders **unmarked** while the
        body still reads ``12.3a`` — the dangling reference the whole feature
        exists to prevent — and ``</fn>`` finds nothing to give back, so the
        give-back arm cannot report it either. It is counted at the write.
        """
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="T1"><label>Table 1.</label>
        <table><tbody><tr><td>12.3</td></tr></tbody></table>
        <table-wrap-foot>
          <fn><label>a</label><label></label><p>Adjusted.</p></fn>
        </table-wrap-foot>
      </table-wrap>
    </sec>""")
        ).parse()

        assert [t.footnotes for t in article.tables] == [["Adjusted."]]
        assert any("1 footnote marker(s)" in m for m in parser_log.messages(logging.WARNING))

    def test_a_lone_empty_label_costs_nothing(self, parser_log):
        """The negative control for the guard above, and the deposit that is
        actually measured: 3 served and 11 archive ``<fn>`` carry an empty
        ``<label>``, none of them beside a real marker, so displacing nothing
        must report nothing.
        """
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="T1"><label>Table 1.</label>
        <table><tbody><tr><td>12.3</td></tr></tbody></table>
        <table-wrap-foot><fn><label></label><p>Adjusted.</p></fn></table-wrap-foot>
      </table-wrap>
    </sec>""")
        ).parse()

        assert [t.footnotes for t in article.tables] == [["Adjusted."]]
        assert not [m for m in parser_log.messages(logging.WARNING) if "footnote marker" in m]

    def test_the_dropped_marker_is_counted_and_reported_once(self, parser_log):
        """One WARNING per article carrying the count, the ``rejected_spans``
        granularity — a deposit reaches this, so it is not the audit's ERROR.
        """
        JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="T1"><label>Table 1.</label>
        <table><tbody><tr><td>12.3</td></tr></tbody></table>
        <table-wrap-foot>
          <fn><label>a</label></fn>
          <fn><label>b</label></fn>
        </table-wrap-foot>
      </table-wrap>
    </sec>""")
        ).parse()

        warnings = [m for m in parser_log.messages(logging.WARNING) if "footnote marker" in m]
        assert len(warnings) == 1
        assert "2 footnote marker(s)" in warnings[0]

    def test_a_figures_dropped_marker_is_counted_too(self, parser_log):
        """Why ``</fn>`` asks the walk with ``including_self=True``.

        The closing element *is* the footnote container, and a ``<fig><fn>``
        has no other — so the strict slice used for prose answers ``None``
        here and the figure side's unspent marker goes uncounted. Every table
        fixture passes either way, ``<table-wrap-foot>`` still being an
        ancestor of the closing ``<fn>``.
        """
        JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <fig id="f1"><label>Figure 1.</label><fn><label>*</label></fn></fig>
    </sec>""")
        ).parse()

        assert any("1 footnote marker(s)" in m for m in parser_log.messages(logging.WARNING))

    def test_a_footnote_that_was_filed_reports_nothing(self, parser_log):
        """The negative control: a marker that was spent is not a loss."""
        JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="T1"><label>Table 1.</label>
        <table><tbody><tr><td>12.3</td></tr></tbody></table>
        <table-wrap-foot><fn><label>a</label><p>Adjusted.</p></fn></table-wrap-foot>
      </table-wrap>
    </sec>""")
        ).parse()

        assert not [m for m in parser_log.messages(logging.WARNING) if "footnote marker" in m]


class TestAnExhibitFootnoteBlocksHeadingIsCounted:
    """A ``<table-wrap-foot>``'s or exhibit ``<fn-group>``'s own ``<title>`` is
    dropped by the ``<title>`` owner rule (#125, #130) — correctly, bmlib
    modelling no container that carries one — and since #124 made the block
    a destination, that drop was one of the two things in it leaving no
    trace, beside the block's own ``<label>``, which is #235's (issue #238).
    The drop stays; it is counted and reported once per article at WARNING,
    the ``refused_apparatus_prose`` rule for a loss this module argued for.

    **Two guards, and each keeps a different population out.** The parent
    test keeps out a ``<list><title>`` inside a note, dropped by the same
    rule wherever the list sits; the owner walk keeps out every
    ``<fn-group>`` heading belonging to no exhibit — an unsectioned
    ``<back>``'s, which issue #231 now *recovers* as that container's own
    section heading, and a sectioned one in ``<body>`` or ``<back>``, which is
    still dropped and is #240's. Both are pinned below, the first by asserting
    the heading it now carries. And an
    empty ``<title/>`` costs nothing: nothing was read, so nothing is
    missing, the rule every sibling counter makes for an empty deposit.

    **Measured by the counter itself over both artifacts**: 0 of the 8,118
    served articles of ``PMC10030002_PMC10040000.xml.gz``, and 7 headings in
    4 of the 97,909 archive articles of
    ``oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26`` — so it pins a direction
    on the rendition bmlib is fed and a population on the archive. Every
    archive heading is a ``<table-wrap-foot>``'s, reading "Note", "Note:" or
    "Fontes:", none of them empty; the issue's own 8 was an unscoped
    whole-document walk.
    """

    def test_a_table_foots_heading_is_dropped_and_counted(self, parser_log):
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="T1"><label>Table 1.</label>
        <table><tbody><tr><td>12.3</td></tr></tbody></table>
        <table-wrap-foot><title>Note:</title>
          <fn><label>a</label><p>A note.</p></fn>
        </table-wrap-foot>
      </table-wrap>
    </sec>""")
        ).parse()

        assert [t.footnotes for t in article.tables] == [["a — A note."]]
        assert [s.title for s in article.body_sections] == ["Results"]
        assert article.tables[0].caption == ""
        warnings = [m for m in parser_log.messages(logging.WARNING) if "footnote block" in m]
        assert len(warnings) == 1
        assert "1 heading(s) of an exhibit's footnote block" in warnings[0]

    def test_a_figures_fn_group_heading_is_counted_too(self, parser_log):
        """The figure side: ``<fn-group>`` is the only member of
        ``_EXHIBIT_FOOTNOTE_BLOCKS`` a ``<fig>`` could hold, there being no
        foot element — a claim about bmlib's sets, not about what JATS admits
        (see the ``_EXHIBIT_FOOTNOTE_CONTAINERS`` comment)."""
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <fig id="f1"><label>Figure 1.</label>
        <fn-group><title>Notes</title><p>A loose note.</p></fn-group>
      </fig>
    </sec>""")
        ).parse()

        assert [f.footnotes for f in article.figures] == [["A loose note."]]
        assert any(
            "1 heading(s) of an exhibit's footnote block" in m
            for m in parser_log.messages(logging.WARNING)
        )

    def test_a_group_nested_in_the_foot_counts_its_heading_once(self, parser_log):
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="T1"><label>Table 1.</label>
        <table><tbody><tr><td>12.3</td></tr></tbody></table>
        <table-wrap-foot><fn-group><title>Abbreviations</title>
          <fn><p>BMI, body mass index.</p></fn>
        </fn-group></table-wrap-foot>
      </table-wrap>
    </sec>""")
        ).parse()

        assert [t.footnotes for t in article.tables] == [["BMI, body mass index."]]
        warnings = [m for m in parser_log.messages(logging.WARNING) if "footnote block" in m]
        assert len(warnings) == 1
        assert "1 heading(s)" in warnings[0]

    def test_two_headings_are_reported_once_with_their_count(self, parser_log):
        """One WARNING per article carrying the count — ``rejected_spans``'
        granularity, and the count is asserted rather than the line's
        presence, which is the rule #224's own tests had to learn."""
        JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="T1"><label>Table 1.</label>
        <table><tbody><tr><td>1</td></tr></tbody></table>
        <table-wrap-foot><title>Note</title><fn><p>One.</p></fn></table-wrap-foot>
      </table-wrap>
      <table-wrap id="T2"><label>Table 2.</label>
        <table><tbody><tr><td>2</td></tr></tbody></table>
        <table-wrap-foot><title>Note</title><fn><p>Two.</p></fn></table-wrap-foot>
      </table-wrap>
    </sec>""")
        ).parse()

        warnings = [m for m in parser_log.messages(logging.WARNING) if "footnote block" in m]
        assert len(warnings) == 1
        assert "2 heading(s) of an exhibit's footnote block" in warnings[0]

    def test_a_back_fn_groups_heading_is_not_this_counter(self, parser_log):
        """A ``<back><fn-group><title>`` is a *container's* heading, and not
        this counter's. It belongs to no exhibit, and unsectioned it is issue
        #231's population, which that issue **recovers** as the container's
        own section heading rather than dropping — so counting it here would
        report as lost a heading that is sitting in ``body_sections``. This
        fixture kills a mutant dropping the owner walk.

        It asserted ``paragraphs`` alone until PR #280's review, and its
        docstring said the heading was dropped: #231 changed the answer and the
        test stayed green because it never looked at the field that moved,
        which is this repository's "a negative assertion pins a defect" rule
        from the other side. It asserts the title now.
        """
        article = JATSParser(b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <article-id pub-id-type="pmc">PMC1234567</article-id>
    <title-group><article-title>Real article</article-title></title-group>
  </article-meta></front>
  <body><sec><title>Results</title><p>Prose.</p></sec></body>
  <back><fn-group><title>Notes</title><fn><p>A competing interest.</p></fn></fn-group></back>
</article>""").parse()

        assert [(s.title, s.paragraphs) for s in article.body_sections] == [
            ("Results", ["Prose."]),
            ("Notes", ["A competing interest."]),
        ]
        assert not [m for m in parser_log.messages(logging.WARNING) if "footnote block" in m]

    def test_a_sectioned_fn_groups_heading_is_not_this_counter_either(self, parser_log):
        """The walk excludes every ``<fn-group>`` outside an exhibit, and the
        sectioned one is not #231's population — that issue is scoped to
        *unsectioned* back matter. A ``<sec><fn-group><title>Competing
        interests</title>`` is #125's own shape, measured there at 12 titles
        in 3 of 997 served articles, and it is dropped with no counter and no
        line: issue #240, filed by PR #239's review. This pins the direction
        so the next reader knows the silence is a filed gap and not an
        oversight of this counter's."""
        article = JATSParser(
            _article_with_body("""
    <sec><title>Additional information</title>
      <fn-group><title>Competing interests</title><fn><p>None declared.</p></fn></fn-group>
    </sec>""")
        ).parse()

        assert [(s.title, s.paragraphs) for s in article.body_sections] == [
            ("Additional information", ["None declared."])
        ]
        assert not [m for m in parser_log.messages(logging.WARNING) if "footnote block" in m]

    def test_an_empty_heading_costs_nothing(self, parser_log):
        """``<title/>`` deposits no heading, so nothing was read and nothing
        is missing — ``offer_graphic``'s rule for an empty href and
        ``hold_footnote_label``'s for an empty marker. Counted, the line would
        state a loss that did not happen (PR #239's review)."""
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="T1"><label>Table 1.</label>
        <table><tbody><tr><td>12.3</td></tr></tbody></table>
        <table-wrap-foot><title/><fn><label>a</label><p>A note.</p></fn></table-wrap-foot>
      </table-wrap>
    </sec>""")
        ).parse()

        assert [t.footnotes for t in article.tables] == [["a — A note."]]
        assert not [m for m in parser_log.messages(logging.WARNING) if "footnote block" in m]

    def test_a_heading_inside_an_abstracts_exhibit_is_counted(self, parser_log):
        """The abstract branch declines a ``<title>`` while an exhibit is open
        (the guard #125 kept there), so a ``<table-wrap-foot><title>`` in a
        graphical abstract falls through to this arm and is counted — the
        only trace of that drop, ``abstract_sections`` being the half
        ``FullTextService`` caches. The abstract stays unsplit; the counter
        is what this fixture adds to the pre-existing one in
        ``TestATitleIsRoutedByItsOwner``."""
        article = JATSParser(b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <article-id pub-id-type="pmc">PMC1234567</article-id>
    <title-group><article-title>Graphical</article-title></title-group>
    <abstract>
      <p>Background and results.</p>
      <table-wrap id="t1"><label>Table 1</label>
        <table-wrap-foot><title>Notes</title><fn><p>a footnote</p></fn></table-wrap-foot>
      </table-wrap>
      <p>Conclusions follow.</p>
    </abstract>
  </article-meta></front>
  <body><sec><title>Results</title><p>Prose.</p></sec></body>
</article>""").parse()

        assert [(s.title, s.content) for s in article.abstract_sections] == [
            ("", "Background and results. Conclusions follow.")
        ]
        assert [t.footnotes for t in article.tables] == [["a footnote"]]
        warnings = [m for m in parser_log.messages(logging.WARNING) if "footnote block" in m]
        assert len(warnings) == 1
        assert "1 heading(s)" in warnings[0]

    def test_a_heading_inside_a_nested_article_is_not_this_articles(self, parser_log):
        """Every handler is suppressed inside a ``<sub-article>`` (#110), and
        an arm added later has to be too — the precedent
        ``test_a_formula_inside_a_nested_article_is_not_this_articles`` sets,
        with both of this issue's counters in one region."""
        article = JATSParser(b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <article-id pub-id-type="pmc">PMC1234567</article-id>
    <title-group><article-title>Real article</article-title></title-group>
  </article-meta></front>
  <body><sec><title>Results</title><p>Ours.</p></sec></body>
  <sub-article article-type="reviewer-report">
    <front-stub><title-group><article-title>Review</article-title></title-group></front-stub>
    <body><sec><title>R</title>
      <table-wrap id="T9"><table><tbody><tr><td>1</td></tr></tbody></table>
        <table-wrap-foot><title>Note:</title>
          <fn><p>Theirs <graphic xlink:href="theirs.gif"/></p></fn>
        </table-wrap-foot>
      </table-wrap>
    </sec></body>
  </sub-article>
</article>""").parse()

        assert [p for s in article.body_sections for p in s.paragraphs] == ["Ours."]
        assert article.tables == []
        assert not [m for m in parser_log.messages(logging.WARNING) if "(issue #238)" in m]

    def test_both_counters_in_one_article_print_two_lines(self, parser_log):
        """The two audit blocks are independent ``if``s. Chained as an
        ``elif``, the image line vanishes whenever a heading was counted —
        a mutant that survived every fixture holding one counter at a time
        (PR #239's review)."""
        JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="T1"><label>Table 1.</label>
        <table><tbody><tr><td>12.3</td></tr></tbody></table>
        <table-wrap-foot><title>Note:</title>
          <fn><p>Key: <graphic xlink:href="key.gif"/></p></fn>
        </table-wrap-foot>
      </table-wrap>
    </sec>""")
        ).parse()

        headings = [m for m in parser_log.messages(logging.WARNING) if "footnote block" in m]
        images = [
            m for m in parser_log.messages(logging.WARNING) if "exhibit's footnote matter" in m
        ]
        assert len(headings) == 1 and "1 heading(s)" in headings[0]
        assert len(images) == 1 and "1 graphic deposit(s)" in images[0]

    def test_the_block_set_is_a_subset_of_the_container_set(self):
        """A member of ``_EXHIBIT_FOOTNOTE_BLOCKS`` outside
        ``_EXHIBIT_FOOTNOTE_CONTAINERS`` could never set ``saw_container``
        in the owner walk, so the heading arm would silently go dead for it.
        The relation is load-bearing and was unasserted (PR #239's review).
        The two sets are told apart only by input JATS does not admit — an
        ``<fn><title>`` — so the split is documentary, and no well-formed
        fixture can pin it."""
        assert (
            jats_parser_module._EXHIBIT_FOOTNOTE_BLOCKS
            <= jats_parser_module._EXHIBIT_FOOTNOTE_CONTAINERS
        )

    def test_a_lists_title_inside_a_note_is_not_the_blocks_heading(self, parser_log):
        """The counter is keyed on the block's own ``<title>`` — parent
        ``<table-wrap-foot>`` or ``<fn-group>`` — and not on every ``<title>``
        in footnote matter. A ``<list><title>`` inside a note is dropped by the
        same owner rule wherever the list sits, in body prose as in a note, so
        counting it only here would key a counter on the wrong scope.
        """
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="T1"><label>Table 1.</label>
        <table><tbody><tr><td>12.3</td></tr></tbody></table>
        <table-wrap-foot><fn><p>See:</p>
          <list><title>Items</title><list-item><p>First.</p></list-item></list>
        </fn></table-wrap-foot>
      </table-wrap>
    </sec>""")
        ).parse()

        assert [t.footnotes for t in article.tables] == [["See:", "First."]]
        assert not [m for m in parser_log.messages(logging.WARNING) if "footnote block" in m]


class TestAGraphicInAnExhibitsFootnoteIsCounted:
    """A ``<graphic>`` owned by an exhibit's footnote matter is dropped by
    ``_graphic_owner``'s opacity rule (#127) — correctly, since keeping the
    transparent set short is what stops a nested supplement's image being
    donated to the figure enclosing it — and it was the other thing in the
    block leaving no trace (issue #238). Counted and reported once per article
    at WARNING; the image is still filed on nothing.

    **Scoped to an owner that is the footnote matter itself** — ``<fn>``, the
    ``<table-wrap-foot>`` or an ``<fn-group>`` — because the served bundle's
    one footnote-matter image is an ``<inline-formula>``'s, which is issue
    #175's population (a formula deposited as an image) and would be pooled
    into this one by an ancestor test. Every owner outside the three is
    #244's residual.

    **The unit is the deposit, and the line says so.** An ``<alternatives>``
    pair is one image in two encodings, transparent to ``_graphic_owner``,
    and reaches the arm twice; the counter counts ``<graphic>`` elements —
    the unit the deposit survey counts, and the one `footnote_markers_dropped`
    is commensurable with — so the line reports *graphic deposits* rather
    than claiming two images are missing (PR #239's review). An href-less
    ``<graphic/>`` costs nothing, ``offer_graphic``'s own rule.

    **Measured by the counter itself**: 0 of 8,118 served articles, and 7
    deposits in 4 of the 97,909 archive ones. The deposit survey behind the
    scope reads 319 formula-owned and 3 ``<boxed-text>``-owned images in
    that footnote matter against the 7 owned by an ``<fn>``.
    """

    def test_a_notes_image_is_dropped_and_counted(self, parser_log):
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="T1"><label>Table 1.</label>
        <table><tbody><tr><td>12.3</td></tr></tbody></table>
        <table-wrap-foot>
          <fn><label>a</label><p>Marked thus: <graphic xlink:href="mark.gif"/></p></fn>
        </table-wrap-foot>
      </table-wrap>
    </sec>""")
        ).parse()

        assert article.tables[0].graphic_url is None
        assert [t.footnotes for t in article.tables] == [["a — Marked thus:"]]
        warnings = [
            m for m in parser_log.messages(logging.WARNING) if "exhibit's footnote matter" in m
        ]
        assert len(warnings) == 1
        assert "1 graphic deposit(s) in an exhibit's footnote matter" in warnings[0]

    def test_a_figures_footnote_image_is_counted_too(self, parser_log):
        """The figure carries no image of its own, so a note's image donated
        to it would show as ``graphic_url`` — with an own image both rank
        FULL and the figure's wins either way, which is why the first cut's
        ``== "f1.jpg"`` could not see a donation (PR #239's review)."""
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <fig id="f1"><label>Figure 1.</label>
        <fn><p>Key: <graphic xlink:href="key.gif"/></p></fn>
      </fig>
    </sec>""")
        ).parse()

        assert article.figures[0].graphic_url is None
        assert any(
            "1 graphic deposit(s) in an exhibit's footnote matter" in m
            for m in parser_log.messages(logging.WARNING)
        )

    def test_the_loose_general_notes_image_is_counted(self, parser_log):
        """The ``<p>`` after the last marked note is footnote matter too, and
        its image's owner is the ``<table-wrap-foot>`` itself."""
        JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="T1"><label>Table 1.</label>
        <table><tbody><tr><td>12.3</td></tr></tbody></table>
        <table-wrap-foot><p>Legend: <graphic xlink:href="legend.gif"/></p></table-wrap-foot>
      </table-wrap>
    </sec>""")
        ).parse()

        assert any(
            "1 graphic deposit(s) in an exhibit's footnote matter" in m
            for m in parser_log.messages(logging.WARNING)
        )

    def test_two_images_are_reported_once_with_their_count(self, parser_log):
        JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="T1"><label>Table 1.</label>
        <table><tbody><tr><td>12.3</td></tr></tbody></table>
        <table-wrap-foot>
          <fn><p>One <graphic xlink:href="a.gif"/></p></fn>
          <fn><p>Two <graphic xlink:href="b.gif"/></p></fn>
        </table-wrap-foot>
      </table-wrap>
    </sec>""")
        ).parse()

        warnings = [
            m for m in parser_log.messages(logging.WARNING) if "exhibit's footnote matter" in m
        ]
        assert len(warnings) == 1
        assert "2 graphic deposit(s) in an exhibit's footnote matter" in warnings[0]

    def test_a_fn_groups_own_loose_image_is_counted(self, parser_log):
        """``<fn-group>`` as the image's *direct* owner — the loose ``<p>`` in
        a figure's group, the one shape where neither ``<fn>`` nor
        ``<table-wrap-foot>`` is in the path. A mutant excluding that member
        as an owner survived every other fixture (PR #239's review). Measures
        0 in both artifacts, so a direction."""
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <fig id="f1"><label>Figure 1.</label>
        <fn-group><p>Key: <graphic xlink:href="key.gif"/></p></fn-group>
      </fig>
    </sec>""")
        ).parse()

        assert article.figures[0].graphic_url is None
        assert [f.footnotes for f in article.figures] == [["Key:"]]
        warnings = [
            m for m in parser_log.messages(logging.WARNING) if "exhibit's footnote matter" in m
        ]
        assert len(warnings) == 1
        assert "1 graphic deposit(s)" in warnings[0]

    def test_an_href_less_deposit_costs_nothing(self, parser_log):
        """``<graphic/>`` with no href references nothing, so nothing is
        missing — ``offer_graphic`` ignores the same deposit for an exhibit's
        own slot. Counted, the line would report an image that never
        existed (PR #239's review)."""
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="T1"><label>Table 1.</label>
        <table><tbody><tr><td>12.3</td></tr></tbody></table>
        <table-wrap-foot><fn><p>Mark: <graphic/></p></fn></table-wrap-foot>
      </table-wrap>
    </sec>""")
        ).parse()

        assert [t.footnotes for t in article.tables] == [["Mark:"]]
        assert not [
            m for m in parser_log.messages(logging.WARNING) if "exhibit's footnote matter" in m
        ]

    def test_an_alternatives_pair_is_two_deposits_of_one_image(self, parser_log):
        """``<alternatives>`` is transparent to ``_graphic_owner``, so both
        encodings reach the arm and the counter reads 2 — the unit the
        deposit survey counts. The line names that unit; it does not say two
        images are missing, since the module treats the pair as one image
        everywhere else (PR #239's review)."""
        JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="T1"><label>Table 1.</label>
        <table><tbody><tr><td>12.3</td></tr></tbody></table>
        <table-wrap-foot><fn><p>Key:
          <alternatives><graphic xlink:href="k.tif"/><graphic xlink:href="k.jpg"/></alternatives>
        </p></fn></table-wrap-foot>
      </table-wrap>
    </sec>""")
        ).parse()

        warnings = [
            m for m in parser_log.messages(logging.WARNING) if "exhibit's footnote matter" in m
        ]
        assert len(warnings) == 1
        assert "2 graphic deposit(s) in an exhibit's footnote matter" in warnings[0]
        assert "image(s) they encode" in warnings[0]

    def test_an_exhibit_opened_inside_a_note_keeps_its_own_image(self, parser_log):
        """A ``<fig>`` inside a note files its own image: its ``<graphic>``'s
        owner is ``fig``, so the ``owner == "fig"`` branch above the counter
        arm takes it and the arm is never reached — the ``elif`` order, not
        the owner walk, is what this fixture pins (a mutant dropping the walk
        passes it, PR #239's review). Nesting measures 0 in both artifacts;
        the direction is what is pinned."""
        article = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="T1"><label>Table 1.</label>
        <table><tbody><tr><td>12.3</td></tr></tbody></table>
        <table-wrap-foot><fn><p>See
          <fig id="f9"><graphic xlink:href="own.jpg"/></fig>
        </p></fn></table-wrap-foot>
      </table-wrap>
    </sec>""")
        ).parse()

        assert [f.graphic_url for f in article.figures] == ["own.jpg"]
        assert not [
            m for m in parser_log.messages(logging.WARNING) if "exhibit's footnote matter" in m
        ]

    def test_a_formulas_image_inside_a_note_is_not_this_counter(self, parser_log):
        """The one footnote-matter image the served bundle deposits: a
        ``<graphic>`` inside an ``<inline-formula>``, whose owner is the
        formula. That is issue #175's population and stays out of this one."""
        JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="T1"><label>Table 1.</label>
        <table><tbody><tr><td>12.3</td></tr></tbody></table>
        <table-wrap-foot><fn><p>Where
          <inline-formula><graphic xlink:href="eq.gif"/></inline-formula>
        </p></fn></table-wrap-foot>
      </table-wrap>
    </sec>""")
        ).parse()

        assert not [
            m for m in parser_log.messages(logging.WARNING) if "exhibit's footnote matter" in m
        ]

    def test_a_cells_footnote_image_is_the_cells(self, parser_log):
        """A cell ends the owner walk, so an ``<fn>`` inside a ``<td>`` is
        not exhibit footnote matter and its image is not counted here."""
        JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="T1"><label>Table 1.</label>
        <table><tbody><tr>
          <td>12.3<fn><p><graphic xlink:href="c.gif"/></p></fn></td>
        </tr></tbody></table>
      </table-wrap>
    </sec>""")
        ).parse()

        assert not [
            m for m in parser_log.messages(logging.WARNING) if "exhibit's footnote matter" in m
        ]

    def test_a_back_footnotes_image_is_not_this_counter(self, parser_log):
        """Belongs to no exhibit, so it falls off the end of the ``<graphic>``
        chain uncounted — issue #244's residual, not this counter's — the
        same pooling refusal the heading counter makes."""
        JATSParser(b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <article-id pub-id-type="pmc">PMC1234567</article-id>
    <title-group><article-title>Real article</article-title></title-group>
  </article-meta></front>
  <body><sec><title>Results</title><p>Prose.</p></sec></body>
  <back><fn-group><fn><p>Signed <graphic xlink:href="sig.gif"/></p></fn></fn-group></back>
</article>""").parse()

        assert not [
            m for m in parser_log.messages(logging.WARNING) if "exhibit's footnote matter" in m
        ]


class TestRenderingAnExhibitsFootnotes:
    """``to_html`` prints the notes where the publisher prints them.

    A block after the exhibit rather than folded into the caption: caption and
    footnote stay distinguishable in the string ``FullTextService`` caches,
    which is the only way most consumers ever see either — the service
    discards the ``JATSArticle``.
    """

    def test_a_tables_footnotes_are_rendered_after_the_table(self):
        html = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="T1"><label>Table 1.</label>
        <caption><p>Outcomes.</p></caption>
        <table><tbody><tr><td>12.3</td></tr></tbody></table>
        <table-wrap-foot><fn><label>a</label><p>Adjusted for age.</p></fn></table-wrap-foot>
      </table-wrap>
    </sec>""")
        ).to_html()

        assert '<div class="fn-group">' in html
        assert "<p>a — Adjusted for age.</p>" in html
        assert html.index("<table>") < html.index('<div class="fn-group">')

    def test_a_figures_footnotes_are_rendered_after_its_caption(self):
        html = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <fig id="f1"><label>Figure 1.</label>
        <caption><p>A figure.</p></caption>
        <fn><label>*</label><p>Scale bar 10um.</p></fn>
      </fig>
    </sec>""")
        ).to_html()

        assert html.index("</figcaption>") < html.index('<div class="fn-group">')
        # And *inside* the <figure>, not after it. Asserting only the lower
        # bound let the block move past `</figure>`, where it is no longer the
        # figure's at all and, with two figures, sits between them
        # (PR #237's review).
        assert html.index('<div class="fn-group">') < html.index("</figure>")
        assert "<p>* — Scale bar 10um.</p>" in html

    def test_an_exhibit_with_no_footnotes_renders_no_block(self):
        """Nothing is emitted over an empty list — the ``<figcaption>`` rule."""
        html = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <fig id="f1"><label>Figure 1.</label><caption><p>A figure.</p></caption></fig>
    </sec>""")
        ).to_html()

        assert "fn-group" not in html

    def test_the_note_is_escaped_like_every_other_deposit(self):
        html = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="T1"><label>Table 1.</label>
        <table><tbody><tr><td>1</td></tr></tbody></table>
        <table-wrap-foot><fn><p>Funded by A &amp; B &lt;grant&gt;.</p></fn></table-wrap-foot>
      </table-wrap>
    </sec>""")
        ).to_html()

        assert "<p>Funded by A &amp; B &lt;grant&gt;.</p>" in html


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


class TestAnUnlabelledExhibitIsNotGivenANumber:
    """``to_html`` invented ``Figure {i + 1}`` / ``Table {i + 1}`` for an
    exhibit the publisher deposited without a ``<label>``.

    That is #116's own symptom reached from the other side — a number the
    document does not carry, which any cross-reference in the prose then
    contradicts — and it is the *majority* reading of what #162 filed as a
    violated premise.

    **Measured on the committed recent corpus, and derivable from two
    first-generation counters**: 7,058 exhibits carry 6,937 direct-child
    ``<label>`` elements, so **121 exhibits in 83 of 997 articles (1.7% and
    8.3%) carry none** and were given one. The redrawn back-filled window
    measures 0 of 627. Both kinds are reached: at least 7 of the 121 are a
    ``<fig>`` and at least 11 a ``<table-wrap>``, taken from the articles whose
    rows hold none of the other kind.

    The seven articles #162 names are the subset of those 121 that *contain*
    some other element's ``<label>`` — the difference between the two counters,
    6,944 - 6,937 — which is exactly the set a descendant-search fallback would
    fire on. Fetched from Europe PMC (2026-09-02), all seven are a
    ``<table-wrap>`` carrying no ``<label>`` and no ``<caption>``, and every
    label below them is a ``<table-wrap-foot><fn>`` marker (``*``, ``**``, and
    the empty string) or a ``<list-item>`` bullet inside a table cell (``1.``,
    ``-``, ``•``). Four are deposited under ids their publisher reserves for an
    unnumbered table — ``array1``, ``array2``, ``utbl0001``. So the shapes
    below are that live spot-check written down: a descendant search would have
    corrupted 7 of 7, which is #116 verbatim, and the parent rule loses nothing
    on any of them.
    """

    def test_a_table_without_a_label_gets_no_invented_number(self):
        html = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="utbl0001">
        <table><tbody><tr><td>12.3</td></tr></tbody></table>
      </table-wrap>
    </sec>""")
        ).to_html()

        assert "Table 1" not in html
        assert 'id="utbl0001"' in html

    def test_a_figure_without_a_label_gets_no_invented_number(self):
        html = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <fig id="f1">
        <caption><p>A caption the publisher did write.</p></caption>
        <graphic xlink:href="f1.jpg"/>
      </fig>
    </sec>""")
        ).to_html()

        assert "Figure 1" not in html
        # Asserted in its rendered form. The bare substring is also present in
        # the <img> `alt`, so it passed while a mutant dropped the whole
        # <figcaption> — an assertion satisfied by a second home is not
        # pinning the branch it was written for.
        assert "<p>A caption the publisher did write.</p>" in html

    def test_a_labelled_exhibit_still_renders_its_own_number(self):
        """The old half of the condition, so dropping the label is not the fix."""
        html = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <fig id="f1"><label>Fig. 3</label>
        <caption><p>A caption.</p></caption></fig>
      <table-wrap id="t1"><label>Table IV</label>
        <table><tbody><tr><td>1</td></tr></tbody></table></table-wrap>
    </sec>""")
        ).to_html()

        assert "Fig. 3" in html
        assert "Table IV" in html

    def test_a_footnote_marker_below_it_does_not_become_the_tables_number(self):
        """PMC12011025's shape: ``*`` and ``**`` in a ``<table-wrap-foot>``.

        A descendant-search fallback fires here and reads ``*`` as the number.
        """
        html = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="t0005A">
        <table><tbody><tr><td>12.3</td></tr></tbody></table>
        <table-wrap-foot>
          <fn><label>*</label><p>Adjusted for age.</p></fn>
          <fn><label>**</label><p>Adjusted for sex.</p></fn>
        </table-wrap-foot>
      </table-wrap>
    </sec>""")
        ).to_html()

        assert "Table 1" not in html
        assert "<h3>*</h3>" not in html
        assert "<h3>**</h3>" not in html

    def test_a_list_bullet_in_a_cell_does_not_become_the_tables_number(self):
        """PMC12154067's shape: ``<list-item>`` bullets inside a ``<td>``."""
        html = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="array1">
        <table><tbody><tr><td>
          <list><list-item><label>-</label><p>First point.</p></list-item>
                <list-item><label>-</label><p>Second point.</p></list-item></list>
        </td></tr></tbody></table>
      </table-wrap>
    </sec>""")
        ).to_html()

        assert "Table 1" not in html
        assert "<h3>-</h3>" not in html

    def test_an_unlabelled_exhibit_does_not_shift_a_labelled_siblings_number(self):
        """The invented number was the *index*, so it collided with a real one.

        Two figures, the first unlabelled: it was rendered ``Figure 1`` and so
        was the second, which is what the publisher actually calls that one. A
        reader following a cross-reference to "Figure 1" found two of them.
        """
        html = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <fig id="unnumbered"><caption><p>A schematic.</p></caption></fig>
      <fig id="f1"><label>Figure 1</label><caption><p>The real one.</p></caption></fig>
    </sec>""")
        ).to_html()

        assert html.count("<strong>Figure 1</strong>") == 1

    def test_an_unlabelled_figures_image_is_not_described_by_an_invented_number(self):
        """``alt`` carried the same invented number, read out as authoritative.

        It falls back to the caption, which is text the document does carry.
        """
        html = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <fig id="f1">
        <caption><p>Study flow diagram.</p></caption>
        <graphic xlink:href="f1.jpg"/>
      </fig>
    </sec>""")
        ).to_html()

        assert 'alt="Study flow diagram."' in html
        assert "Figure 1" not in html

    def test_an_unlabelled_uncaptioned_figures_image_has_an_empty_alt(self):
        """Nothing in the document describes it, so nothing is asserted."""
        html = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <fig id="f1"><graphic xlink:href="f1.jpg"/></fig>
    </sec>""")
        ).to_html()

        assert 'alt=""' in html
        assert "Figure 1" not in html

    def test_a_figure_with_nothing_to_say_emits_no_empty_figcaption(self):
        """The element was unconditional only because the number always filled it."""
        html = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <fig id="f1"><graphic xlink:href="f1.jpg"/></fig>
    </sec>""")
        ).to_html()

        assert "<figcaption>" not in html
        assert 'id="f1"' in html

    def test_an_image_only_tables_alt_is_not_an_invented_number_either(self):
        """#127's shape without a label: the <img> is the whole table."""
        html = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="array1">
        <caption><p>Baseline characteristics.</p></caption>
        <graphic xlink:href="t1.jpg"/>
      </table-wrap>
    </sec>""")
        ).to_html()

        assert 'alt="Baseline characteristics."' in html
        assert "Table 1" not in html

    def test_an_image_only_table_with_no_caption_asserts_nothing_in_its_alt(self):
        """The branch the test above cannot reach: neither label nor caption.

        This is where an invented number would still have been read out, and it
        is the shape #127 measured — a ``<table-wrap>`` whose only content is
        the image, so the ``alt`` is the only text a screen reader is given.
        """
        html = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="array1"><graphic xlink:href="t1.jpg"/></table-wrap>
    </sec>""")
        ).to_html()

        assert 'alt=""' in html
        assert "Table 1" not in html

    # THE CONDITIONALS THIS FIX INTRODUCED, PINNED ON BOTH EDGES.
    #
    # Removing the invented number replaced two unconditional writes with five
    # conditionals, and the tests above pin only that no number appears. Three
    # first-order mutants of those conditionals survived the whole file — each
    # verified by mutation, `__pycache__` cleared between runs — so what
    # follows is the other edge of each: that the branch still emits what the
    # deposit *does* carry. `A number is not invented` and `a label is still
    # rendered` are two claims, and the suite asserted only the first.

    def test_a_labelled_uncaptioned_figure_still_renders_its_figcaption(self):
        """The left disjunct of the ``<figcaption>`` guard, unpinned until now.

        Every fixture above that carries a ``<label>`` also carries a
        ``<caption>``, so ``if fig.label or fig.caption:`` narrowed to
        ``if fig.caption:`` — or to ``and`` — passed the entire suite while
        dropping the legend of every label-only figure. That is the opposite
        failure from the one #162 fixes and a silent one, the same class as
        #116 and #125.

        The empty ``<p>`` assertion is the second edge: ``if fig.caption:``
        widened to ``if True:`` emits one for every uncaptioned figure.
        """
        html = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <fig id="f1"><label>Fig. 3</label><graphic xlink:href="f1.jpg"/></fig>
    </sec>""")
        ).to_html()

        assert "<figcaption>" in html
        assert "<strong>Fig. 3</strong>" in html
        assert "<p></p>" not in html

    def test_an_unlabelled_captioned_figure_emits_no_empty_heading(self):
        """``if fig.label:`` widened to ``if True:`` survived the suite.

        It emits ``<strong></strong>`` on every unlabelled figure — a blank
        where the deposit says nothing, which is not what "renders no number"
        means and is invisible to a `"Figure 1" not in html` assertion.
        """
        html = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <fig id="f1"><caption><p>Study flow diagram.</p></caption></fig>
    </sec>""")
        ).to_html()

        assert "<figcaption>" in html
        assert "<p>Study flow diagram.</p>" in html
        assert "<strong>" not in html

    def test_a_labelled_figures_image_is_described_by_its_own_label(self):
        """The figure counterpart of ``test_the_label_is_the_images_alt_text``.

        The table side pinned both orders of its ``alt`` fallback; the figure
        side pinned neither, so ``fig.label or fig.caption`` collapsing to
        ``fig.caption`` — or inverting to ``fig.caption or fig.label`` — passed
        the suite. The label is the better alternative text where both exist,
        because the caption is already rendered beside the image.
        """
        html = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <fig id="f1"><label>Fig. 3</label>
        <caption><p>Study flow diagram.</p></caption>
        <graphic xlink:href="f1.jpg"/></fig>
    </sec>""")
        ).to_html()

        assert 'alt="Fig. 3"' in html

    def test_an_unlabelled_table_emits_no_empty_heading(self):
        """``if tbl.label:`` narrowed to ``is not None:`` survived the suite.

        ``JATSTableInfo.label`` defaults to the empty string rather than
        ``None``, so the identity test is always true and every one of the 121
        unlabelled exhibits renders ``<h3></h3>``. The tests above assert only
        that ``Table 1`` is absent, which an empty heading satisfies.
        """
        html = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="utbl0001">
        <table><tbody><tr><td>12.3</td></tr></tbody></table>
      </table-wrap>
    </sec>""")
        ).to_html()

        assert "<h3>" not in html
        assert "12.3" in html

    def test_a_content_free_exhibit_still_carries_its_anchor(self):
        """An exhibit with nothing in it renders an empty container, on purpose.

        Neither ``</fig>`` nor ``</table-wrap>`` filters an exhibit carrying no
        label, caption, graphic or rows, and before #162 the invented number
        always gave the container something to hold. It is kept rather than
        skipped because the ``id`` is what an ``<xref>`` in the prose targets,
        so dropping the element would break a link the document does make.
        Pinned so a later "emit no empty elements" tidy-up cannot take the
        anchor with it.
        """
        html = JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <p>See <xref ref-type="table" rid="tA">the table</xref>.</p>
      <fig id="fA"/>
      <table-wrap id="tA"/>
    </sec>""")
        ).to_html()

        assert 'id="fA"' in html
        assert 'id="tA"' in html
        assert "Figure 1" not in html
        assert "Table 1" not in html


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
        """Where routing by owner works hardest.

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

    def test_an_exhibits_title_inside_an_abstract_does_not_split_it(self):
        """A graphical abstract's exhibit must not reach the abstract branch.

        JATS admits ``<fig>`` and ``<table-wrap>`` in an ``<abstract>``, and
        the guard that used to open the whole ``<title>`` arm — ``if
        self.in_figure or self.in_table_wrap:`` — swallowed every title inside
        one. Routing by parent replaced that arm, so without an explicit
        exhibit test a ``<table-wrap-foot><fn-group><title>`` flushes the
        pending abstract section and installs itself as the next heading,
        splitting the abstract and re-attributing the prose after it.

        The same failure as #125 one branch over, and the worse half of it:
        ``abstract_sections`` is rendered into the HTML ``FullTextService``
        caches, where ``body_sections`` reaches no bmlib path at all. The
        population measures empty — 44 exhibits inside an ``<abstract>`` over
        the two committed draws, none carrying a ``<title>``.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Graphical</article-title>
  </title-group>
  <abstract>
    <p>Background and results.</p>
    <table-wrap id="t1"><label>Table 1</label>
      <table-wrap-foot><fn-group><title>Notes</title>
        <fn><p>a footnote</p></fn></fn-group></table-wrap-foot>
    </table-wrap>
    <p>Conclusions follow.</p>
  </abstract>
  </article-meta></front>
  <body><sec><title>Results</title><p>We measured the thing.</p></sec></body>
</article>"""
        article = JATSParser(data).parse()

        assert [(s.title, s.content) for s in article.abstract_sections] == [
            ("", "Background and results. Conclusions follow.")
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

    **Neither of #123's populations measures empty any longer**, and the
    denominators that framed them are gone with the draws they came from (0
    of 1,550 and 0 of 288 were the two 300-article draws #138 replaced). The
    committed recent corpus holds **6 nested ``<caption>`` of 8,111**, and
    **6 inside an exhibit owned by a ``<supplementary-material>``** rather
    than by the exhibit enclosing them — both counts one eLife article,
    ``PMC12143881``, depositing its figure supplements that way. The
    back-filled window contributes to neither, holding no ``<caption>`` at
    all, so its zeroes are an absent denominator. These fixtures stay
    hand-built because the shape is one publisher's house style rather than
    because nothing deposits it. The
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
        """The rule must keep what the retired `_innermost_exhibit` delivered:
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

    def test_a_container_inside_a_caption_keeps_its_own_title(self):
        """The owner test, at the one place an ambient test still passes.

        Every other fixture here has the ``<title>`` arrive with no caption
        open, so ``if parent == "caption"`` and ``if self.caption_stack`` agree
        on all of them and the ambient form survives the whole suite —
        mutation-verified. The discriminating shape is a ``<title>`` arriving
        *while* a caption is open and owned by something else, which is not
        hypothetical: the committed recent corpus measures **411 such titles
        in 104 of 997 articles**, owned by a ``<caption>`` (387), a
        ``<def-list>`` (12) and an ``<fn-group>`` (12). The ``<list>``-owned
        example this used to cite (13 titles in PMC7135044) came from a
        back-filled draw #138 replaced; that article is in neither corpus and
        the redrawn back-filled window carries no renaming title at all, so
        it is historical and not re-derivable.

        A ``<list>``'s own heading welded into a figure legend is the reason
        this needs pinning rather than noting — a legend is prose, so one
        extra phrase in it is invisible.
        """
        data = b"""<?xml version="1.0"?>
<article xmlns:xlink="http://www.w3.org/1999/xlink">
  <front><article-meta><title-group><article-title>Keyed</article-title>
  </title-group></article-meta></front>
  <body>
    <sec><title>Results</title>
      <fig id="f1"><label>Figure 1</label>
        <caption><title>Study flow.</title>
          <p>Panels are ordered.</p>
          <list list-type="simple"><title>Panel key</title>
            <list-item><p>A, control.</p></list-item></list>
        </caption>
        <graphic xlink:href="f1.jpg"/></fig>
      <p>Prose after the figure.</p>
    </sec>
  </body>
</article>"""
        article = JATSParser(data).parse()

        assert article.figures[0].caption == "Study flow. Panels are ordered. A, control."
        assert article.body_sections[0].title == "Results"
        assert article.body_sections[0].paragraphs == ["Prose after the figure."]

    def test_a_tables_caption_lead_reaches_the_table(self):
        """The ``<caption><title>`` lead, on the side no assertion covered.

        ``if parent == "caption" and self.in_figure`` survives the whole suite
        otherwise: every ``tables[…].caption`` assertion in this file uses a
        ``<p>``-only caption, so a ``<table-wrap>``'s lead sentence could be
        dropped silently. #135's figure/table asymmetry, reproduced on
        captions.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Lead</article-title>
  </title-group></article-meta></front>
  <body>
    <sec><title>Results</title>
      <table-wrap id="t1"><label>Table 1.</label>
        <caption><title>Commonly asked questions.</title><p>Responses by group.</p></caption>
        <table><tbody><tr><td>12.3</td></tr></tbody></table></table-wrap>
    </sec>
  </body>
</article>"""
        article = JATSParser(data).parse()

        assert article.tables[0].caption == "Commonly asked questions. Responses by group."

    def test_an_unmodelled_owner_inside_a_table_donates_nothing(self):
        """An unowned caption, with a ``<table-wrap>`` open rather than a ``<fig>``.

        The three existing unmodelled-owner fixtures all have a figure open
        and no table, so ``current_table`` is ``None`` throughout and
        ``_exhibit_named`` returning it for an unknown parent is
        indistinguishable from returning ``None`` — mutation-verified.

        The ``<table-wrap-foot>`` prose after ``</caption>`` is the off-edge
        half: it pins the pop going *off* on the table side, which is where
        PR #126's two survivors hid.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Source data</article-title>
  </title-group></article-meta></front>
  <body>
    <sec><title>Results</title>
      <table-wrap id="t1"><label>Table 1.</label>
        <caption><p>Baseline characteristics.</p></caption>
        <table><tbody><tr><td>12.3</td></tr></tbody></table>
        <table-wrap-foot><fn><p>CI, confidence interval.</p></fn></table-wrap-foot>
        <supplementary-material id="sd1"><label>Table 1-source data 1.</label>
          <caption><title>Raw counts.</title><p>Numbers behind the table.</p></caption>
        </supplementary-material>
      </table-wrap>
      <p>Prose after the table.</p>
    </sec>
  </body>
</article>"""
        article = JATSParser(data).parse()

        assert article.tables[0].caption == "Baseline characteristics."
        assert article.tables[0].label == "Table 1."
        assert article.body_sections[0].paragraphs == ["Prose after the table."]


class TestACrossReferenceToAnExhibitBecomesALink:
    """A ``<xref>`` pointing at a figure or table is *replaced*, not merged.

    ``<xref>`` is inline, so its text ordinarily merges back into the
    paragraph. For ``ref-type="fig"`` and ``ref-type="table"`` the close
    instead appends ``[text](#rid)`` itself, which :func:`_convert_inline_links`
    renders as an anchor — so the merge has to be *suppressed* for exactly
    those, or the label is emitted twice: ``Figure 1[Figure 1](#f1)``.

    Written while generalising the merge rule for issue #146, which made that
    suppression a condition on a larger expression. Nothing pinned it before:
    dropping ``not is_fig_table_xref`` altogether passed the whole suite.
    """

    #: All four accepted ``ref-type`` spellings, then one that is not. The
    #: ``<contrib-group>`` is there so the parse does not land in the
    #: zero-author detector's quiet branch, as every neighbouring reference
    #: fixture already ensures.
    PROSE_WITH_CROSS_REFERENCES = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <title-group><article-title>An article that cross-references</article-title></title-group>
    <contrib-group content-type="author">
      <contrib><name><surname>Real</surname><given-names>A</given-names></name></contrib>
    </contrib-group>
  </article-meta></front>
  <body><sec><title>Results</title>
    <p>As shown in <xref ref-type="fig" rid="f1">Figure 1</xref> the effect holds.</p>
    <p>See <xref ref-type="table" rid="t1">Table 2</xref> too.</p>
    <p>Also <xref ref-type="figure" rid="f2">Figure 2</xref> and
<xref ref-type="table-wrap" rid="t2">Table 3</xref>.</p>
    <p>And <xref ref-type="bibr" rid="R1">3</xref> as well.</p>
  </sec></body>
</article>"""

    def test_an_exhibit_reference_is_emitted_once(self):
        """Merged *and* rewritten, the label appears twice in the prose."""
        article = JATSParser(self.PROSE_WITH_CROSS_REFERENCES).parse()

        assert article.body_sections[0].paragraphs[:2] == [
            "As shown in [Figure 1](#f1) the effect holds.",
            "See [Table 2](#t1) too.",
        ]

    def test_the_lenient_spellings_are_rewritten_too(self):
        """``figure`` and ``table-wrap`` are in the tuple; nothing exercised them.

        Neither is in JATS's suggested ``ref-type`` list, so both are
        lenient-parse defence — but deleting them from
        ``is_fig_table_xref``'s tuple survived the whole suite, which is the
        state a defensive member must not be left in.
        """
        article = JATSParser(self.PROSE_WITH_CROSS_REFERENCES).parse()

        assert article.body_sections[0].paragraphs[2] == (
            "Also [Figure 2](#f2) and [Table 3](#t2)."
        )

    def test_a_reference_of_any_other_type_keeps_its_own_text(self):
        """Only the exhibit types are rewritten; a citation marker is prose."""
        article = JATSParser(self.PROSE_WITH_CROSS_REFERENCES).parse()

        assert article.body_sections[0].paragraphs[3] == "And 3 as well."

    def test_the_link_reaches_the_rendered_html_as_an_anchor(self):
        """The markdown form is an intermediate; the anchor is what is cached."""
        html = JATSParser(self.PROSE_WITH_CROSS_REFERENCES).to_html()

        assert '<a href="#f1">Figure 1</a>' in html
        assert "[Figure 1](#f1)" not in html


class TestAMixedCitationKeepsTheTextItPrints:
    """``<mixed-citation>`` is mixed content, so its descendants' text is its
    own — issue #146.

    Every child that accumulates a buffer without being inline had its text
    *taken and not returned*, so the string bmlib rendered from the buffer was
    whatever direct character data was left: the punctuation between the
    children. ``<person-group>``, ``<article-title>``, ``<source>``,
    ``<year>``, ``<volume>``, ``<issue>``, ``<fpage>``, ``<lpage>`` and
    ``<pub-id>`` are all in that state, which is the whole of a standard NLM
    deposit — it rendered as ``'. . . ;():-. doi: .'``.

    The structured fields were *almost* always right, and the exception is
    :class:`TestACitedNameOutsideAPersonGroupIsStillAName` below — the
    ``<surname>``/``<given-names>`` arms are gated on ``in_ref_person_group``,
    so a cited ``<string-name>`` deposited outside one had no arm fire at all
    and the merge is what recovers it. PR #141 fixed exactly this shape for
    ``<collab>`` and ``<string-name>`` by making them inline, and the two
    tests that pin it live in :class:`TestAnUndividedContributorName`.
    Membership of
    ``_INLINE_ELEMENTS`` is the wrong instrument here: it is a property of the
    *element*, and these elements carry text that must not merge outside a
    citation — ``<article-title>`` in ``<article-meta>`` is the article's own
    title, and merging it would put the title into whatever buffer happened to
    be open. So the rule is a property of the *context*: inside a
    ``<mixed-citation>``, every descendant's text belongs to the citation.
    """

    #: The standard NLM journal deposit — every field marked up, with the
    #: publisher's own punctuation between the elements.
    NLM_JOURNAL_DEPOSIT = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <title-group><article-title>An article that cites</article-title></title-group>
    <contrib-group content-type="author">
      <contrib><name><surname>Real</surname><given-names>A</given-names></name></contrib>
    </contrib-group>
  </article-meta></front>
  <back><ref-list>
    <ref id="R1"><mixed-citation publication-type="journal">\
<person-group person-group-type="author">\
<name><surname>Smith</surname>, <given-names>J</given-names></name>, \
<name><surname>Doe</surname>, <given-names>A</given-names></name>\
</person-group>. <article-title>An observed effect</article-title>. \
<source>J Med</source>. <year>2020</year>;<volume>10</volume>(<issue>2</issue>):\
<fpage>100</fpage>-<lpage>109</lpage>. doi: <pub-id pub-id-type="doi">10.1/xyz</pub-id>.\
</mixed-citation></ref>
  </ref-list></back>
</article>"""

    #: The same reference deposited as ``<element-citation>``, whose content
    #: model is element-only: there is no publisher-authored punctuation to
    #: recover, so there is no string to rebuild.
    #:
    #: ``<edition>``, ``<publisher-loc>``, ``<publisher-name>`` and
    #: ``<comment>`` are the point of this fixture and not decoration. None is
    #: in ``_TEXT_ACCUMULATING``, so none ever took a buffer to withhold —
    #: their characters go straight to whatever is open, which here is the
    #: citation's own buffer. Built from accumulating children alone (as the
    #: first cut of this fixture was), the assertion below passes whatever the
    #: close arm does, and the run-together word the docstrings all cite as the
    #: reason for the exclusion was exactly what the parser produced.
    ELEMENT_CITATION_DEPOSIT = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <title-group><article-title>An article that cites</article-title></title-group>
    <contrib-group content-type="author">
      <contrib><name><surname>Real</surname><given-names>A</given-names></name></contrib>
    </contrib-group>
  </article-meta></front>
  <back><ref-list>
    <ref id="R1"><element-citation publication-type="book">
      <person-group person-group-type="author">
        <name><surname>Smith</surname><given-names>J</given-names></name>
      </person-group>
      <article-title>An observed effect</article-title>
      <source>J Med</source>
      <edition>3rd ed</edition>
      <publisher-loc>Amsterdam</publisher-loc>
      <publisher-name>Elsevier</publisher-name>
      <year>2020</year>
      <comment>Epub ahead of print</comment>
    </element-citation></ref>
  </ref-list></back>
</article>"""

    #: One ``<ref>``, both spellings, ``<mixed-citation>`` first. JATS admits
    #: this — as bare siblings and inside ``<citation-alternatives>`` — and the
    #: close arm used to write on both branches, so the second deposit decided
    #: the answer.
    BOTH_SPELLINGS_MIXED_FIRST = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <title-group><article-title>An article that cites</article-title></title-group>
    <contrib-group content-type="author">
      <contrib><name><surname>Real</surname><given-names>A</given-names></name></contrib>
    </contrib-group>
  </article-meta></front>
  <back><ref-list>
    <ref id="R1"><citation-alternatives>\
<mixed-citation><source>J Med</source>. <year>2020</year>.</mixed-citation>
      <element-citation><source>J Med</source>
        <year>2020</year>
      </element-citation>
    </citation-alternatives></ref>
  </ref-list></back>
</article>"""

    def test_the_citation_string_is_what_the_publisher_typeset(self):
        """The whole reference, in document order, punctuation included."""
        article = JATSParser(self.NLM_JOURNAL_DEPOSIT).parse()

        assert article.references[0].citation == (
            "Smith, J, Doe, A. An observed effect. J Med. 2020;10(2):100-109. doi: 10.1/xyz."
        )

    def test_reading_a_child_still_fills_its_own_structured_field(self):
        """Merging the text back must not disturb what the close reads.

        ``_pop_text_buffer`` returns the element's own text and appends a copy
        to the parent, so the structured read is unaffected — but it is the
        half a caller relies on, and the two are one argument apart.
        """
        reference = JATSParser(self.NLM_JOURNAL_DEPOSIT).parse().references[0]

        assert reference.authors == ["J Smith", "A Doe"]
        assert reference.article_title == "An observed effect"
        assert reference.source == "J Med"
        assert reference.year == "2020"
        assert reference.doi == "10.1/xyz"

    def test_a_nested_name_reaches_the_citation_through_its_person_group(self):
        """The merge has to compose, or only the outermost child comes back.

        ``<surname>`` merges into ``<person-group>`` and ``<person-group>``
        into ``<mixed-citation>``; a rule applied only to the citation's direct
        children would keep the comma the ``<name>`` prints and drop the name
        itself.
        """
        citation = JATSParser(self.NLM_JOURNAL_DEPOSIT).parse().references[0].citation

        assert citation.startswith("Smith, J, Doe, A.")

    def test_an_element_citation_is_not_run_together(self):
        """Element-only content authored no string, so none is invented.

        Whitespace between an ``<element-citation>``'s children is
        insignificant by the content model, so concatenating them yields
        either a run-together word or a sequence whose separators are the
        depositor's indentation. Assembling a reference from the structured
        fields is a citation-style decision, and ``formatted_citation`` is
        where it is made.

        Excluding the element from the merge was necessary and not
        sufficient: a child bmlib does not accumulate never withheld a buffer
        to begin with, so ``'3rd ed Amsterdam Elsevier Epub ahead of print'``
        reached this field until the close arm stopped writing it.
        """
        reference = JATSParser(self.ELEMENT_CITATION_DEPOSIT).parse().references[0]

        assert reference.citation == ""
        assert reference.article_title == "An observed effect"

    def test_the_structured_fields_survive_the_empty_citation(self):
        """Refusing the string must not cost the fields it was built beside."""
        reference = JATSParser(self.ELEMENT_CITATION_DEPOSIT).parse().references[0]

        assert reference.authors == ["J Smith"]
        assert reference.source == "J Med"
        assert reference.year == "2020"

    def test_a_mixed_citation_wins_over_an_element_citation_beside_it(self):
        """A ``<ref>`` may carry both, and order must not decide the answer.

        Both closes used to assign, so the later element won. With a
        ``<mixed-citation>`` first — legal, and what
        ``<citation-alternatives>`` is for — the ``<element-citation>``'s
        (correctly) empty string wiped the one the publisher did typeset.
        That cost nothing before #146, when both were punctuation; afterwards
        it discards the recovered reference, and an empty ``citation`` is now
        a deliberate value, so nothing distinguishes the loss from the rule.
        """
        reference = JATSParser(self.BOTH_SPELLINGS_MIXED_FIRST).parse().references[0]

        assert reference.citation == "J Med. 2020."


class TestACitedNameOutsideAPersonGroupIsStillAName:
    """A cited ``<string-name>`` with no ``<person-group>`` around it — #146.

    The ``<surname>`` and ``<given-names>`` arms are both gated on
    ``in_ref_person_group``, so in this shape neither fires: nothing sets
    ``current_author_surname``, the ``<string-name>`` arm takes its
    ``elif text:`` branch, and before #146 the element's own buffer held only
    the whitespace between two children whose text had been taken. The result
    was ``authors == []`` — or, where the deposit put punctuation between the
    children, entries like ``[',', ',', ',']``.

    Merging the children back is what fills the buffer, so this is a
    *structured* field the mixed-content rule moves, not only the rendered
    string. Measured over 880 local PMC articles: 502 references in 14
    articles, 61 of which previously held punctuation-only entries.

    **And the value has to be normalised, not merely stripped.** ``text`` is
    end-stripped, so a deposit that puts each child on its own line — Wiley's,
    which is where this was found — yielded the literal ``'J.\\nTan'``, a line
    break mid-name, in a public list and in the HTML ``FullTextService``
    caches. Every other author on that list is built by
    ``finish_current_author()``, which joins with a single space.
    """

    WILEY_CITED_NAMES = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <title-group><article-title>An article that cites</article-title></title-group>
    <contrib-group content-type="author">
      <contrib><name><surname>Real</surname><given-names>A</given-names></name></contrib>
    </contrib-group>
  </article-meta></front>
  <back><ref-list>
    <ref id="R1"><mixed-citation publication-type="journal">
<string-name name-style="western">
<given-names>J.</given-names>
<surname>Tan</surname>
</string-name>, <string-name name-style="western">
<given-names>L. M.</given-names>
<surname>Almeida</surname>
</string-name>, <article-title>Updating the diagnosis</article-title>.
</mixed-citation></ref>
  </ref-list></back>
</article>"""

    #: The same hazard one element over: a cited ``<collab>`` whose name the
    #: publisher wrapped across lines.
    WRAPPED_COLLABORATION = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <title-group><article-title>An article that cites</article-title></title-group>
    <contrib-group content-type="author">
      <contrib><name><surname>Real</surname><given-names>A</given-names></name></contrib>
    </contrib-group>
  </article-meta></front>
  <back><ref-list>
    <ref id="R1"><mixed-citation><collab>the INHERIT
      Trial Group</collab>. <article-title>A cited paper</article-title>.
</mixed-citation></ref>
  </ref-list></back>
</article>"""

    def test_the_name_is_collected_at_all(self):
        """Neither child's arm fires here, so the merge is the only route."""
        reference = JATSParser(self.WILEY_CITED_NAMES).parse().references[0]

        assert reference.authors == ["J. Tan", "L. M. Almeida"]

    def test_the_name_carries_no_line_break(self):
        """A raw buffer reaches a public list, so it is normalised on the way."""
        reference = JATSParser(self.WILEY_CITED_NAMES).parse().references[0]

        assert not any("\n" in author for author in reference.authors)

    def test_a_wrapped_collaboration_is_normalised_too(self):
        """The ``<collab>`` arm appends a raw buffer for the same reason."""
        reference = JATSParser(self.WRAPPED_COLLABORATION).parse().references[0]

        assert reference.authors == ["the INHERIT Trial Group"]

    def test_the_normalised_name_is_what_reaches_the_rendered_html(self):
        """The half that persists: ``FullTextService`` caches this HTML."""
        html = JATSParser(self.WILEY_CITED_NAMES).to_html()

        assert "J. Tan, L. M. Almeida" in html


class TestARefCarryingSeveralCitationsKeepsThemAll:
    """One ``<ref>``, several citation elements — issue #149.

    JATS admits several, and both close arms assigned
    :attr:`JATSReferenceInfo.citation` unconditionally, so every part but the
    last was discarded. The structured fields did the opposite: scalars were
    last-wins while ``authors`` *accumulated*, so one reference reported a
    byline welded from several different works.

    Measured over 880 local PMC articles: **216 such references in 21
    articles, and not one uses ``<citation-alternatives>``** — every case is
    bare siblings, so this is never "the same reference deposited twice". Two
    shapes, and both are a single bibliography entry as the publisher prints
    it, which is why bmlib still emits one ``JATSReferenceInfo`` per ``<ref>``:

    * **149 with each part labelled** — RSC's ``(a)``/``(b)``/``(c)``: several
      distinct works under one bibliography number.
    * **61 unlabelled** — one reference *split*, its tail (a URL, an
      ``[Online]. Available:`` note) deposited as a second element.

    The second shape is what rules out emitting one reference per part: it
    would split a single work into a work plus a bare URL.

    **The parts are joined with nothing between them**, because that is what
    the deposit has: the character data between consecutive citation elements
    is empty in **586 of 586** occurrences. Each part's *raw* text is kept and
    the whole is normalised once at ``</ref>`` — the module's "strip once, at
    the outermost call" rule — which is what preserves the space in front of
    ``(b)`` while not inventing one in front of ``, [Online]``.
    """

    #: RSC's multi-part reference: one bibliography number, several works,
    #: each part carrying its own ``<label>``.
    RSC_MULTIPART_REFERENCE = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <title-group><article-title>An article that cites</article-title></title-group>
    <contrib-group content-type="author">
      <contrib><name><surname>Real</surname><given-names>A</given-names></name></contrib>
    </contrib-group>
  </article-meta></front>
  <back><ref-list>
    <ref id="cit1"><label>1</label>\
<mixed-citation id="cit1a"><label> (a) </label>\
<named-content content-type="citation-string">Jackson E. R. Curr Top Med Chem 2012;12:706.\
</named-content></mixed-citation>\
<mixed-citation id="cit1b"><label> (b) </label>\
<named-content content-type="citation-string">Nowack B. Water Res 2003;37:2533.\
</named-content></mixed-citation></ref>
  </ref-list></back>
</article>"""

    #: One reference whose tail was deposited as a second element. The tail
    #: opens with punctuation, so a join that inserts a space is visibly wrong.
    SPLIT_REFERENCE_WITH_URL_TAIL = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <title-group><article-title>An article that cites</article-title></title-group>
    <contrib-group content-type="author">
      <contrib><name><surname>Real</surname><given-names>A</given-names></name></contrib>
    </contrib-group>
  </article-meta></front>
  <back><ref-list>
    <ref id="cit7"><mixed-citation><source>Sensors</source>. <year>2019</year>;19:3977\
</mixed-citation><mixed-citation>, [Online]. Available: https://example.org/3977\
</mixed-citation></ref>
  </ref-list></back>
</article>"""

    #: The whole of the measured label population: the ``<ref>`` carries no
    #: ``<label>`` of its own, and each part carries a marker. 158 references
    #: in 14 of 880 local PMC articles; nought where a real reference label
    #: was overwritten.
    MULTIPART_WITH_NO_REFERENCE_LABEL = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <title-group><article-title>An article that cites</article-title></title-group>
    <contrib-group content-type="author">
      <contrib><name><surname>Real</surname><given-names>A</given-names></name></contrib>
    </contrib-group>
  </article-meta></front>
  <back><ref-list>
    <ref id="cit3"><mixed-citation><label> (a) </label>Jackson E. R. 2012.\
</mixed-citation><mixed-citation><label> (b) </label>Nowack B. 2003.\
</mixed-citation></ref>
  </ref-list></back>
</article>"""

    #: Two works, each fully marked up. This is the shape that welded a byline:
    #: `authors` accumulated across both parts.
    TWO_WORKS_EACH_MARKED_UP = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <title-group><article-title>An article that cites</article-title></title-group>
    <contrib-group content-type="author">
      <contrib><name><surname>Real</surname><given-names>A</given-names></name></contrib>
    </contrib-group>
  </article-meta></front>
  <back><ref-list>
    <ref id="cit2"><mixed-citation>\
<person-group><name><surname>Ricci</surname>, <given-names>A</given-names></name></person-group>. \
<article-title>The first work</article-title>. <year>2019</year>.\
</mixed-citation><mixed-citation> \
<person-group><name><surname>Clark</surname>, <given-names>J</given-names></name></person-group>. \
<article-title>The second work</article-title>. <year>2021</year>.\
</mixed-citation></ref>
  </ref-list></back>
</article>"""

    def test_every_part_reaches_the_citation_string(self):
        """Last-wins discarded all but the final part."""
        citation = JATSParser(self.RSC_MULTIPART_REFERENCE).parse().references[0].citation

        assert citation == (
            "(a) Jackson E. R. Curr Top Med Chem 2012;12:706. (b) Nowack B. Water Res 2003;37:2533."
        )

    def test_a_split_reference_is_rejoined_without_an_invented_space(self):
        """The deposit has nothing between the parts, so neither does the join."""
        citation = JATSParser(self.SPLIT_REFERENCE_WITH_URL_TAIL).parse().references[0].citation

        assert citation == "Sensors. 2019;19:3977, [Online]. Available: https://example.org/3977"

    def test_the_structured_fields_come_from_the_first_part(self):
        """Assembling across parts welds a byline no publisher deposited."""
        reference = JATSParser(self.TWO_WORKS_EACH_MARKED_UP).parse().references[0]

        assert reference.authors == ["A Ricci"]
        assert reference.article_title == "The first work"
        assert reference.year == "2019"

    def test_the_later_parts_are_still_in_the_string(self):
        """First-wins narrows the fields; it must not discard the deposit."""
        citation = JATSParser(self.TWO_WORKS_EACH_MARKED_UP).parse().references[0].citation

        assert "The first work" in citation
        assert "The second work" in citation

    def test_a_reference_keeps_its_own_label(self):
        """A part's marker is not the reference's number — #116 one family over.

        The reference branch of the ``<label>`` arm was gated on the ambient
        ``in_ref`` flag, which is exactly what the comment above it argues
        against: a ``<label>`` belongs to the element enclosing it, and JATS
        spells it as a direct child. So each part's ``(a)``/``(b)`` marker
        overwrote the reference's own number, last one winning.
        """
        reference = JATSParser(self.RSC_MULTIPART_REFERENCE).parse().references[0]

        assert reference.label == "1"

    def test_a_reference_with_no_label_of_its_own_gets_none(self):
        """The invented value is the failure #116 names, not a blank one.

        Measured: 158 references in 14 of 880 local PMC articles carry no
        ``<label>`` of their own and had a part's marker supplied as one.
        Nought carry a real label that a deeper one overwrote — so the whole
        population is a fabricated number, and the marker is in ``citation``
        where the publisher put it.
        """
        reference = JATSParser(self.MULTIPART_WITH_NO_REFERENCE_LABEL).parse().references[0]

        assert reference.label == ""
        assert reference.citation.startswith("(a) ")

    def test_a_single_citation_reference_is_unaffected(self):
        """The join must be identity for the overwhelmingly common shape."""
        reference = (
            JATSParser(TestAMixedCitationKeepsTheTextItPrints.NLM_JOURNAL_DEPOSIT)
            .parse()
            .references[0]
        )

        assert reference.citation == (
            "Smith, J, Doe, A. An observed effect. J Med. 2020;10(2):100-109. doi: 10.1/xyz."
        )
        assert reference.authors == ["J Smith", "A Doe"]


class TestAnUndividedNameInProseStaysInTheProse:
    """Why ``<collab>``/``<string-name>`` stay in ``_INLINE_ELEMENTS`` — #146.

    #120 and #140 put them there so a ``<mixed-citation>`` printing either
    keeps the name in the citation string it renders. #146's ancestor test
    merges *every* descendant of a citation, which subsumes that reason
    entirely: after it, deleting both entries passes the whole suite and
    changes the rendered HTML of none of 880 local PMC articles, where before
    it failed two tests in :class:`TestAnUndividedContributorName`.

    What the membership still stands for is a name printed anywhere else —
    a paragraph, a section title — where nothing else merges it back and the
    name would simply be deleted from the surrounding text. This test pins
    that, so the entries cannot go quietly vacuous a second time.

    **The population is unmeasured**, and deliberately stated as such: JATS's
    parent lists for these two elements are contributor and citation
    contexts, so a publisher may never deposit this shape. The test asserts
    the *rule* the membership encodes, not a rate.
    """

    UNDIVIDED_NAME_IN_BODY_PROSE = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <title-group><article-title>An article naming a group in prose</article-title></title-group>
    <contrib-group content-type="author">
      <contrib><name><surname>Real</surname><given-names>A</given-names></name></contrib>
    </contrib-group>
  </article-meta></front>
  <body><sec><title>Methods</title>
    <p>Recruitment was run by <collab>the INHERIT Trial Group</collab> throughout.</p>
    <p>Analysis was checked by <string-name>Jane Q Smith</string-name> before release.</p>
  </sec></body>
</article>"""

    def test_a_collaboration_named_in_prose_is_not_deleted_from_it(self):
        """Accumulating without merging removes the name from the paragraph."""
        article = JATSParser(self.UNDIVIDED_NAME_IN_BODY_PROSE).parse()

        assert article.body_sections[0].paragraphs[0] == (
            "Recruitment was run by the INHERIT Trial Group throughout."
        )

    def test_an_undivided_person_named_in_prose_is_not_deleted_either(self):
        """``<string-name>`` is in the set for the same reason ``<collab>`` is."""
        article = JATSParser(self.UNDIVIDED_NAME_IN_BODY_PROSE).parse()

        assert article.body_sections[0].paragraphs[1] == (
            "Analysis was checked by Jane Q Smith before release."
        )


class TestAFormulaReachesTheProseThatContainsIt:
    """A formula's text is emitted once, by the formula element — issue #147.

    Two constructs lost content, and they are the same defect as #146 one
    context over: a text-accumulating child that is not inline has its text
    *taken and not returned*. ``<tex-math>`` is such a child, so an inline
    formula rendered as the sentence with a hole in it; and ``<disp-formula>``
    accumulates with no handler at all, so a display equation — its LaTeX, its
    MathML *and* the ``(1)`` that prose cross-references — was popped and
    discarded.

    The rule is **choose one rendition, at the formula element**, never "merge
    every child". Measured over the committed recent corpus, 1,087 formulas
    carry a LaTeX *and* a MathML encoding of the same expression, so a rule
    that merged both would print every one of them twice; over the
    ``PMC012xxxxxx`` baseline package the same population is 188,473, of which
    4,377 deposit the MathML *first*, so no streaming "first wins" rule works
    either and the encodings have to be held until the formula closes.

    MathML needs no membership of any set here, which is the whole reason this
    change is small: it does not accumulate, so its leaf text is already
    sitting in the formula's own buffer. LaTeX wins where a ``<tex-math>``
    arrived; the buffer serves otherwise — and *"otherwise"* covers the
    flattened MathML, a formula deposited as ordinary ``<italic>``/``<sub>``
    markup (71 of the 141 encoding-less display formulas in the 880-article
    served draw), and a MathML deposit whose namespace is bound to some prefix
    other than ``mml``, which therefore keeps exactly today's behaviour rather
    than depending on a literal prefix match the way #128 does.

    **The LaTeX is a whole document, not an expression.** 99.9% of 4,422
    sampled ``<tex-math>`` deposits in that package are
    ``\\documentclass[12pt]{minimal}`` … ``\\begin{document}`` … , so merging
    the element's text raw would inject some 300 characters of ``\\usepackage``
    lines per formula — worse than the drop it replaces. Every one of the 7,769
    sampled *document-wrapped* deposits carries exactly one
    ``\\begin{document}``/``\\end{document}`` pair — a count of the wrapped
    ones and not of all of them. 96.0% of those bodies are ``$$…$$`` and 3.7%
    ``$…$``, so the rule takes the body and keeps the depositor's own
    delimiters, adding its own only where there are none. Confirmed on the
    rendition the parser is actually fed: 147 of 147 ``<tex-math>`` in two
    live-fetched Europe PMC articles (PMC12000231, PMC12044768, 2026-09-02)
    have the same shape.
    """

    #: The issue's own inline example. The deposit is what a publisher really
    #: sends — a whole LaTeX document — so the fixture is the preamble as well
    #: as the expression.
    INLINE_LATEX = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>T</article-title></title-group>
  </article-meta></front>
  <body><sec><title>Methods</title>
    <p>The model is <inline-formula><alternatives>\
<tex-math id="M1">\\documentclass[12pt]{minimal}
\\usepackage{amsmath}
\\begin{document}$y = mx + b$\\end{document}</tex-math>\
</alternatives></inline-formula> throughout.</p>
  </sec></body>
</article>"""

    #: The issue's own display example: a numbered equation between two
    #: paragraphs, deposited as a block child of the section.
    DISPLAY_LATEX = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>T</article-title></title-group>
  </article-meta></front>
  <body><sec><title>Results</title>
    <p>Before.</p>
    <disp-formula id="e1"><label>(1)</label>\
<tex-math>\\documentclass[12pt]{minimal}\\begin{document}$$E = mc^2$$\\end{document}</tex-math>\
</disp-formula>
    <p>After.</p>
  </sec></body>
</article>"""

    def _sections(self, xml: bytes):
        return JATSParser(xml).parse().body_sections

    def test_an_inline_formula_reaches_the_sentence_that_contains_it(self):
        """The hole in the sentence is what #147 was filed for."""
        sections = self._sections(self.INLINE_LATEX)

        assert sections[0].paragraphs == ["The model is $y = mx + b$ throughout."]

    def test_a_display_formula_becomes_its_own_paragraph_with_its_number(self):
        """1,459 of 1,915 display formulas in the committed corpus carry a
        ``<label>``, so prose cross-referencing "(1)" is the common case."""
        sections = self._sections(self.DISPLAY_LATEX)

        assert sections[0].paragraphs == ["Before.", "(1) $$E = mc^2$$", "After."]

    def test_the_preamble_the_publisher_wrapped_it_in_does_not_reach_the_prose(self):
        """The half that makes a raw merge worse than the drop it replaces."""
        prose = " ".join(self._sections(self.DISPLAY_LATEX)[0].paragraphs)

        assert "documentclass" not in prose
        assert "usepackage" not in prose
        assert "begin{document}" not in prose

    def test_the_depositors_own_delimiters_are_not_doubled(self):
        """96.0% of bodies already carry ``$$…$$``; wrapping again gives
        ``$$$$…$$$$``, which no reader and no renderer wants."""
        prose = " ".join(self._sections(self.DISPLAY_LATEX)[0].paragraphs)

        assert "$$$$" not in prose
        assert prose.count("$$") == 2

    def test_an_undelimited_body_is_delimited_by_its_context(self):
        """0.04% of bodies carry no delimiter of their own, and the display
        and inline spellings differ, so the context supplies it."""
        xml = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>T</article-title></title-group>
  </article-meta></front>
  <body><sec><title>M</title>
    <p>Inline <inline-formula>\
<tex-math>\\begin{document}a+b\\end{document}</tex-math></inline-formula>.</p>
    <disp-formula><tex-math>\\begin{document}c+d\\end{document}</tex-math></disp-formula>
  </sec></body>
</article>"""
        paragraphs = self._sections(xml)[0].paragraphs

        assert paragraphs == ["Inline $a+b$.", "$$c+d$$"]

    def test_a_deposit_that_is_not_a_whole_document_is_used_as_it_stands(self):
        """3 of 4,422 sampled deposits carry no ``\\begin{document}`` wrapper.

        Splitting on a marker that is not there must not empty the formula.
        """
        xml = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>T</article-title></title-group>
  </article-meta></front>
  <body><sec><title>M</title>
    <disp-formula><tex-math>x = 1</tex-math></disp-formula>
  </sec></body>
</article>"""

        assert self._sections(xml)[0].paragraphs == ["$$x = 1$$"]

    def test_a_formula_carrying_both_encodings_is_emitted_once(self):
        """1,087 formulas in the committed recent corpus, 188,473 in the
        package. Merging every child would print each of them twice."""
        xml = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>T</article-title></title-group>
  </article-meta></front>
  <body><sec><title>M</title>
    <disp-formula><label>(2)</label><alternatives>\
<tex-math>\\begin{document}$$E = mc^2$$\\end{document}</tex-math>\
<mml:math xmlns:mml="http://www.w3.org/1998/Math/MathML"><mml:mi>E</mml:mi><mml:mo>=</mml:mo>\
<mml:mi>m</mml:mi><mml:msup><mml:mi>c</mml:mi><mml:mn>2</mml:mn></mml:msup></mml:math>\
</alternatives></disp-formula>
  </sec></body>
</article>"""

        assert self._sections(xml)[0].paragraphs == ["(2) $$E = mc^2$$"]

    def test_the_choice_does_not_depend_on_the_order_they_were_deposited(self):
        """4,377 of the package's 188,473 both-encoding formulas put the
        MathML first, so a streaming "first wins" rule picks differently for
        2.3% of them. The frame holds both until the formula closes."""
        xml = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>T</article-title></title-group>
  </article-meta></front>
  <body><sec><title>M</title>
    <disp-formula><alternatives>\
<mml:math xmlns:mml="http://www.w3.org/1998/Math/MathML"><mml:mi>E</mml:mi></mml:math>\
<tex-math>\\begin{document}$$E = mc^2$$\\end{document}</tex-math>\
</alternatives></disp-formula>
  </sec></body>
</article>"""

        assert self._sections(xml)[0].paragraphs == ["$$E = mc^2$$"]

    def test_a_mathml_only_display_formula_reaches_the_prose(self):
        """MathML dominates LaTeX 10,202 to 1,398 in the committed corpus, and
        a display formula dropped its MathML as surely as its LaTeX."""
        xml = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>T</article-title></title-group>
  </article-meta></front>
  <body><sec><title>M</title>
    <disp-formula><label>(3)</label>\
<mml:math xmlns:mml="http://www.w3.org/1998/Math/MathML"><mml:mi>y</mml:mi><mml:mo>=</mml:mo>\
<mml:mi>x</mml:mi></mml:math></disp-formula>
  </sec></body>
</article>"""

        assert self._sections(xml)[0].paragraphs == ["(3) y=x"]

    def test_a_display_formula_deposited_as_plain_markup_reaches_the_prose(self):
        """71 of the 141 encoding-less display formulas in the 880-article
        served draw hold the equation as ``<italic>``/``<sub>``/``<sup>``
        alone — no MathML, no LaTeX, and no encoding choice to make."""
        xml = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>T</article-title></title-group>
  </article-meta></front>
  <body><sec><title>M</title>
    <disp-formula><label>(4)</label><italic>C</italic><sub>max</sub> = 12</disp-formula>
  </sec></body>
</article>"""

        assert self._sections(xml)[0].paragraphs == ["(4) Cmax = 12"]

    def test_a_formula_deposited_as_an_image_alone_emits_nothing(self):
        """140 of the committed corpus's 1,915 display formulas are a
        ``<graphic>`` and a ``<label>``. No text-taking rule recovers those,
        and #162's rule is that nothing is invented for them — so no empty
        paragraph, and no paragraph holding a bare number."""
        xml = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>T</article-title></title-group>
  </article-meta></front>
  <body><sec><title>M</title>
    <p>Before.</p>
    <disp-formula id="e5"><label>(5)</label>\
<graphic xlink:href="eq5.jpg" xmlns:xlink="http://www.w3.org/1999/xlink"/></disp-formula>
    <p>After.</p>
  </sec></body>
</article>"""

        assert self._sections(xml)[0].paragraphs == ["Before.", "After."]

    def test_a_display_formula_inside_a_paragraph_stays_inside_it(self):
        """The routing half. A large minority of display formulas sit inside
        a ``<p>`` — 714 of 1,915 (37.3%) in the committed recent corpus and 201
        of 654 in the 880-article served draw, against 116,623 of 150,598
        (77.4%) over the *archive* bytes the served rendition disagrees with.
        Emitted as its own paragraph, every one of them would be appended
        *ahead* of the paragraph it interrupts, because the enclosing ``<p>``
        has not closed yet."""
        xml = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>T</article-title></title-group>
  </article-meta></front>
  <body><sec><title>M</title>
    <p>We fitted <disp-formula><tex-math>\\begin{document}$$y = a$$\\end{document}</tex-math>\
</disp-formula> to the data.</p>
  </sec></body>
</article>"""

        assert self._sections(xml)[0].paragraphs == ["We fitted $$y = a$$ to the data."]

    def test_a_merged_display_formula_carries_no_number(self):
        """A number in front of an expression mid-sentence is read as part of
        it. Measured over the 880-article local corpus, printing it gave
        ``'as shown in eqn (2):2 τ = kn'`` — where ``2 τ`` is a coefficient
        the deposit does not contain. The equation number is printed where the
        equation stands apart, which is where it can be told from the maths."""
        xml = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>T</article-title></title-group>
  </article-meta></front>
  <body><sec><title>M</title>
    <p>as shown in eqn (2):<disp-formula><label>2</label>\
<tex-math>\\begin{document}$$\\tau = k$$\\end{document}</tex-math></disp-formula></p>
  </sec></body>
</article>"""

        assert self._sections(xml)[0].paragraphs == ["as shown in eqn (2): $$\\tau = k$$"]

    def test_two_merged_display_formulas_do_not_weld_together(self):
        """A display formula is a block, so the deposit puts nothing between
        it and its neighbours — the markup relies on the line break it is
        rendered with. Merged verbatim, the local corpus produced
        ``'NH3 + H2O → NH4+ + OH−2 Al3+ + 3OH− → Al(OH)33 Al(OH)3'``: two
        equations and their numbers run into one string."""
        xml = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>T</article-title></title-group>
  </article-meta></front>
  <body><sec><title>M</title>
    <p>the following reactions:<disp-formula><label>1</label>\
<mml:math xmlns:mml="http://www.w3.org/1998/Math/MathML"><mml:mi>a</mml:mi></mml:math>\
</disp-formula><disp-formula><label>2</label>\
<mml:math xmlns:mml="http://www.w3.org/1998/Math/MathML"><mml:mi>b</mml:mi></mml:math>\
</disp-formula></p>
  </sec></body>
</article>"""

        assert self._sections(xml)[0].paragraphs == ["the following reactions: a b"]

    def test_an_inline_formula_is_merged_with_no_spacing_of_its_own(self):
        """The other half of that rule: an inline formula is *in* the line, so
        the deposit's own spacing around it is already right and adding to it
        would put a space before the full stop."""
        xml = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>T</article-title></title-group>
  </article-meta></front>
  <body><sec><title>M</title>
    <p>Let <inline-formula>\
<tex-math>\\begin{document}$x$\\end{document}</tex-math></inline-formula>.</p>
  </sec></body>
</article>"""

        assert self._sections(xml)[0].paragraphs == ["Let $x$."]

    def test_an_inline_formula_keeps_the_spacing_the_deposit_gave_it(self):
        """The module's own rule, already written for ``_text_with_formatting``:
        a run's edge whitespace is re-emitted outside its markers. Elsevier
        deposits ``<inline-formula> k </inline-formula>mer``, putting the
        separation *inside* the element, so normalising without re-emitting it
        welded ``'EndMatrix represents'`` and ``'-minus 0.505'`` into single
        words over the local corpus. #147 is about formulas that were dropped;
        text that already reached the prose keeps its spacing."""
        xml = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>T</article-title></title-group>
  </article-meta></front>
  <body><sec><title>M</title>
    <p>the value<inline-formula> \
<mml:math xmlns:mml="http://www.w3.org/1998/Math/MathML"><mml:mi>k</mml:mi></mml:math> \
</inline-formula>mer content.</p>
  </sec></body>
</article>"""

        # Deliberately no space in the prose either side: the separation this
        # asserts is the deposit's own, inside the element, and a fixture that
        # spaced it outside would pass whatever the rule does.
        assert self._sections(xml)[0].paragraphs == ["the value k mer content."]

    def test_an_inline_formula_the_deposit_did_not_space_is_not_spaced(self):
        """The other edge of the same rule, and the one that makes it a rule
        rather than a blanket space: ``<inline-formula>`` written tight against
        its neighbours means the publisher wanted them tight."""
        xml = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>T</article-title></title-group>
  </article-meta></front>
  <body><sec><title>M</title>
    <p>the value<inline-formula>\
<mml:math xmlns:mml="http://www.w3.org/1998/Math/MathML"><mml:mi>k</mml:mi></mml:math>\
</inline-formula>mer content.</p>
  </sec></body>
</article>"""

        assert self._sections(xml)[0].paragraphs == ["the valuekmer content."]

    def test_a_display_formula_in_a_block_container_stands_on_its_own(self):
        """The other side of the same test: a ``<disp-quote>`` accumulates
        nothing, so there is no prose for the formula to join — 50 of the
        served draw's 654 are deposited that way, and 33,270 of the package's
        sit directly in a ``<sec>``."""
        xml = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>T</article-title></title-group>
  </article-meta></front>
  <body><sec><title>M</title>
    <disp-quote><disp-formula><tex-math>\\begin{document}$$q = 1$$\\end{document}</tex-math>\
</disp-formula></disp-quote>
  </sec></body>
</article>"""

        assert self._sections(xml)[0].paragraphs == ["$$q = 1$$"]

    def test_a_formula_in_a_table_cell_stays_in_the_cell(self):
        """80,918 inline formulas in the package sit in a ``<td>`` and 10,413
        in a ``<th>``; 281 display formulas sit in a ``<td>``. A cell's text is
        flowing text, so the formula joins it rather than being promoted to a
        paragraph of the section that holds the table."""
        xml = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>T</article-title></title-group>
  </article-meta></front>
  <body><sec><title>M</title>
    <table-wrap id="t1"><label>Table 1</label>
      <table><tbody><tr><td>ratio <inline-formula>\
<tex-math>\\begin{document}$r$\\end{document}</tex-math></inline-formula></td></tr></tbody></table>
    </table-wrap>
    <p>Prose.</p>
  </sec></body>
</article>"""
        article = JATSParser(xml).parse()
        cell = article.tables[0].html_content

        assert article.body_sections[0].paragraphs == ["Prose."]
        assert "<td>ratio $r$</td>" in cell
        # The half that was a live corruption rather than a missing value: a
        # cell collects its text from characters() and not from a buffer, so
        # the LaTeX *document* reached the rendered table verbatim. 24,476
        # <tex-math> in 856 of the package's 97,909 articles sit in a cell.
        assert "documentclass" not in cell
        assert "begin{document}" not in cell
        # And exactly once — the formula's text is withheld from the cell so
        # that its rendition can replace it, not join it.
        assert cell.count("$r$") == 1

    def test_mathml_outside_any_formula_element_still_reaches_the_prose(self):
        """36,969 ``mml:math`` in the package sit outside any formula element
        — 30,961 in a ``<p>`` alone. They flow through today because MathML
        accumulates no buffer, and this change deliberately gives it none, so
        that path is untouched."""
        xml = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>T</article-title></title-group>
  </article-meta></front>
  <body><sec><title>M</title>
    <p>The value <mml:math xmlns:mml="http://www.w3.org/1998/Math/MathML">\
<mml:mi>k</mml:mi></mml:math> is fixed.</p>
  </sec></body>
</article>"""

        assert self._sections(xml)[0].paragraphs == ["The value k is fixed."]

    def test_a_tex_math_outside_any_formula_element_is_not_deleted(self):
        """5 in the package sit directly in a ``<p>``. With no frame to stash
        into, the expression is merged where it was deposited rather than
        being taken and dropped — the failure this issue is about."""
        xml = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>T</article-title></title-group>
  </article-meta></front>
  <body><sec><title>M</title>
    <p>Let <tex-math>\\begin{document}$z$\\end{document}</tex-math> denote it.</p>
  </sec></body>
</article>"""

        assert self._sections(xml)[0].paragraphs == ["Let $z$ denote it."]

    def test_a_formulas_label_is_not_the_enclosing_exhibits_number(self):
        """#116, from the side this change could have reopened. A
        ``<disp-formula>``'s ``(1)`` is one of the four labels the retired
        depth counter mis-assigned to the exhibit around it."""
        xml = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>T</article-title></title-group>
  </article-meta></front>
  <body><sec><title>M</title>
    <fig id="f1"><label>Figure 1</label>
      <caption><p>A caption.</p></caption>
      <disp-formula><label>(1)</label>\
<tex-math>\\begin{document}$$w = 2$$\\end{document}</tex-math></disp-formula>
      <graphic xlink:href="f1.jpg" xmlns:xlink="http://www.w3.org/1999/xlink"/>
    </fig>
  </sec></body>
</article>"""
        article = JATSParser(xml).parse()

        assert article.figures[0].label == "Figure 1"

    def test_a_formula_in_a_citation_reaches_the_citation_string_once(self):
        """The path CLAUDE.md records as unexercised — 0 of 10,671
        ``<mixed-citation>`` across 227 articles carry a formula. #146's
        ancestor test merges every descendant of a citation, so without the
        formula arm the LaTeX would arrive there raw, preamble and all, and an
        ``<alternatives>`` pair would arrive twice."""
        xml = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>T</article-title></title-group>
  </article-meta></front>
  <back><ref-list>
    <ref id="R1"><mixed-citation><source>J Med</source>. On <inline-formula>\
<tex-math>\\documentclass{minimal}\\begin{document}$k$\\end{document}</tex-math>\
</inline-formula>.</mixed-citation></ref>
  </ref-list></back>
</article>"""
        reference = JATSParser(xml).parse().references[0]

        assert reference.citation == "J Med. On $k$."

    def test_a_footnote_marker_inside_a_formula_is_not_the_equation_number(self):
        """#116's own shape, one element in. The ``<label>`` a formula reads is
        the one whose *parent* it is — an ambient "is a formula open?" test
        would take a ``<fn>``'s ``a``/``*`` marker exactly as the retired depth
        counter took it for the exhibit enclosing it."""
        xml = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>T</article-title></title-group>
  </article-meta></front>
  <body><sec><title>M</title>
    <disp-formula><label>(7)</label>\
<tex-math>\\begin{document}$$p = 0.05$$\\end{document}</tex-math>\
<fn><label>*</label><p>two-sided</p></fn></disp-formula>
  </sec></body>
</article>"""

        paragraphs = self._sections(xml)[0].paragraphs

        assert "(7) $$p = 0.05$$" in paragraphs
        assert not any(par.startswith("*") for par in paragraphs)

    def test_a_formula_in_a_citation_with_no_formula_element_is_rendered_once(self):
        """Why ``<tex-math>`` is in ``_FORMULA_PARTS`` and not left to
        ``_INLINE_ELEMENTS``. Inside a ``<mixed-citation>`` #146's ancestor
        test merges *every* accumulating descendant, so a ``<tex-math>``
        deposited there with no formula element around it would be merged raw
        — preamble and all — **and** rendered by its own arm, printing the
        expression twice."""
        xml = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>T</article-title></title-group>
  </article-meta></front>
  <back><ref-list>
    <ref id="R1"><mixed-citation><source>J Med</source>. On \
<tex-math>\\documentclass{minimal}\\begin{document}$q$\\end{document}</tex-math>.\
</mixed-citation></ref>
  </ref-list></back>
</article>"""
        citation = JATSParser(xml).parse().references[0].citation

        assert citation == "J Med. On $q$."

    def test_a_nested_formula_in_a_cell_is_not_printed_twice(self):
        """The cell takes the *outermost* formula's rendition alone: the inner
        one's text is already inside the outer's buffer, so offering both
        would print the expression twice in the rendered table."""
        xml = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>T</article-title></title-group>
  </article-meta></front>
  <body><sec><title>M</title>
    <table-wrap id="t1"><label>Table 1</label>
      <table><tbody><tr><td><disp-formula>where <inline-formula>\
<tex-math>\\begin{document}$n$\\end{document}</tex-math></inline-formula></disp-formula>\
</td></tr></tbody></table>
    </table-wrap>
  </sec></body>
</article>"""
        cell = JATSParser(xml).parse().tables[0].html_content

        assert cell.count("$n$") == 1
        assert "<td>where $n$</td>" in cell

    def test_a_display_formula_counts_as_body_prose(self):
        """``has_body`` is what holds a body-less deposit back from the cache,
        and an article whose section is one equation does have a body."""
        xml = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>T</article-title></title-group>
  </article-meta></front>
  <body><sec><title>M</title>
    <disp-formula><tex-math>\\begin{document}$$y = a$$\\end{document}</tex-math></disp-formula>
  </sec></body>
</article>"""

        assert JATSParser(xml).parse().has_body is True

    def test_an_empty_formula_adds_no_paragraph(self):
        """A ``<disp-formula>`` holding nothing at all must not open a
        paragraph, for the reason an empty ``<p>`` does not open a section."""
        xml = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>T</article-title></title-group>
  </article-meta></front>
  <body><sec><title>M</title>
    <p>Only this.</p>
    <disp-formula id="e9"/>
  </sec></body>
</article>"""

        assert self._sections(xml)[0].paragraphs == ["Only this."]

    def test_a_nested_formula_contributes_to_the_one_that_encloses_it(self):
        """Formulas nest — 21 inline formulas inside a display formula in the
        880-article draw — so the frames are a stack, per #115. The inner
        emission lands in the outer formula's own buffer, which the outer then
        emits because it has no LaTeX of its own."""
        xml = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>T</article-title></title-group>
  </article-meta></front>
  <body><sec><title>M</title>
    <disp-formula><label>(6)</label>where <inline-formula>\
<tex-math>\\begin{document}$n$\\end{document}</tex-math></inline-formula> is fixed</disp-formula>
  </sec></body>
</article>"""

        assert self._sections(xml)[0].paragraphs == ["(6) where $n$ is fixed"]

    def test_a_formula_inside_a_nested_article_is_not_this_articles(self):
        """Every handler is suppressed inside a ``<sub-article>`` (#110), and
        a formula arm added later has to be too — the reason the suppression
        is tested before every branch rather than at each one."""
        xml = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>T</article-title></title-group>
  </article-meta></front>
  <body><sec><title>M</title><p>Ours.</p></sec></body>
  <sub-article article-type="reviewer-report">
    <front-stub><title-group><article-title>Review</article-title></title-group></front-stub>
    <body><sec><title>R</title>
      <disp-formula><label>(1)</label>\
<tex-math>\\begin{document}$$reviewer = 1$$\\end{document}</tex-math></disp-formula>
    </sec></body>
  </sub-article>
</article>"""
        article = JATSParser(xml).parse()

        assert [p for s in article.body_sections for p in s.paragraphs] == ["Ours."]


class TestTheFormulaRulesTheReviewCorrected:
    """Issue #147's second round — what the review of PR #176 found.

    Six defects and five unpinned rules, each of which survived the first
    thirty tests. They are grouped here rather than folded into the class
    above because what they have in common is *how they were missed*: every
    one is a rule the docstrings stated and no fixture exercised, which is
    this module's standing failure mode and worth being able to read in one
    place. Three moved stored values (the cell's equation number, the inline
    formula's delimiters, an empty ``<tex-math>``), and three were latent.
    """

    def test_a_display_formula_in_a_cell_keeps_its_number(self):
        """A cell is a slot, not a sentence — see ``_TABLE_CELL_ELEMENTS``.

        The regression this pins: ``characters()`` used to deliver the label
        to the cell, and withholding the formula's text took the number with
        it. Measured over the 40 labelled display formulas sitting in a cell,
        every one is a cell whose whole content is the number and the
        equation — PMC12164272's Table 2 is a reaction-number column whose
        rows the body prose cross-references by number.
        """
        xml = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>T</article-title></title-group>
  </article-meta></front>
  <body><sec><title>M</title>
    <table-wrap id="t1"><label>Table 1</label>
      <table><tbody>
        <tr><td><disp-formula><label>(10)</label>\
<tex-math>\\begin{document}$$a+b=c$$\\end{document}</tex-math></disp-formula></td></tr>
      </tbody></table>
    </table-wrap>
  </sec></body>
</article>"""
        article = JATSParser(xml).parse()

        assert "<td>(10) $$a+b=c$$</td>" in article.tables[0].html_content
        # And the preamble still does not reach it — the number comes back
        # without the corruption it used to arrive beside.
        assert "documentclass" not in article.tables[0].html_content

    def test_a_merged_display_formula_in_prose_still_carries_no_number(self):
        """The cell rule must not widen into the sentence rule it sits beside.

        ``'as shown in eqn (2):2 τ = kn'`` is what printing it here produced —
        the label read as a coefficient. Pinned alongside the cell case so a
        future edit cannot satisfy one by breaking the other.
        """
        xml = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>T</article-title></title-group>
  </article-meta></front>
  <body><sec><title>M</title><p>As shown in eqn (2):\
<disp-formula><label>2</label>\
<tex-math>\\begin{document}$$\\tau = kn$$\\end{document}</tex-math></disp-formula>\
the rate follows.</p></sec></body>
</article>"""
        article = JATSParser(xml).parse()

        assert article.body_sections[0].paragraphs == [
            "As shown in eqn (2): $$\\tau = kn$$ the rate follows."
        ]

    def test_an_empty_tex_math_does_not_suppress_the_encoding_beside_it(self):
        """``frame.latex`` was tested for presence rather than for a rendition.

        An empty or preamble-only ``<tex-math>`` short-circuited the buffer
        that held the MathML flattening, so a formula carrying content
        rendered as nothing.
        """
        xml = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>T</article-title></title-group>
  </article-meta></front>
  <body><sec><title>M</title><p>Before <inline-formula><tex-math>   </tex-math>\
<italic>V</italic><sub>max</sub></inline-formula> after.</p></sec></body>
</article>"""
        article = JATSParser(xml).parse()

        assert article.body_sections[0].paragraphs == ["Before Vmax after."]

    def test_an_inline_formula_does_not_emit_display_delimiters(self):
        """98.6% of 20,251 inline ``<tex-math>`` bodies carry ``$$…$$``.

        Inline formulas cannot genuinely be 98.6% display math, so that pair
        is the ``minimal``-documentclass converter's artifact and not a claim
        about context. Left verbatim it rendered ``'×'`` as ``'$$\\times$$'``
        inside a figure caption.
        """
        xml = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>T</article-title></title-group>
  </article-meta></front>
  <body><sec><title>M</title><p>at <inline-formula>\
<tex-math>\\begin{document}$$\\times$$\\end{document}</tex-math></inline-formula> here</p>
  </sec></body>
</article>"""
        article = JATSParser(xml).parse()

        assert article.body_sections[0].paragraphs == ["at $\\times$ here"]

    def test_a_display_formula_keeps_an_inline_pair_the_depositor_wrote(self):
        """The re-delimiting rule is one-directional, and this is the half it
        does not touch: a display delimiter inside a sentence is wrong markup,
        an inline one on a formula standing alone merely under-styles it."""
        xml = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>T</article-title></title-group>
  </article-meta></front>
  <body><sec><title>M</title>\
<disp-formula><tex-math>\\begin{document}$x=1$\\end{document}</tex-math></disp-formula>
  </sec></body>
</article>"""
        article = JATSParser(xml).parse()

        assert article.body_sections[0].paragraphs == ["$x=1$"]

    def test_a_body_carrying_several_delimited_runs_is_left_alone(self):
        """Its outer characters are not one pair around one expression, so
        stripping them would corrupt it into ``$$a$ + $b$$``."""
        xml = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>T</article-title></title-group>
  </article-meta></front>
  <body><sec><title>M</title><p>see <inline-formula>\
<tex-math>\\begin{document}$a$ + $b$\\end{document}</tex-math></inline-formula> there</p>
  </sec></body>
</article>"""
        article = JATSParser(xml).parse()

        assert article.body_sections[0].paragraphs == ["see $a$ + $b$ there"]

    def test_several_tex_math_deposits_render_the_expression_once(self):
        """``<alternatives>`` holds alternative encodings of one expression.

        Joining them printed it twice — the outcome ``_FORMULA_ELEMENTS`` says
        the design exists to prevent, contradicted three comments away. The
        population measures 0 in both corpora, so this pins a rule rather than
        a behaviour anyone has observed.
        """
        xml = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>T</article-title></title-group>
  </article-meta></front>
  <body><sec><title>M</title>
    <disp-formula><label>(1)</label><alternatives>\
<tex-math>\\begin{document}$$E = mc^2$$\\end{document}</tex-math>\
<tex-math>\\begin{document}$$E = m c^{2}$$\\end{document}</tex-math></alternatives></disp-formula>
  </sec></body>
</article>"""
        article = JATSParser(xml).parse()

        assert article.body_sections[0].paragraphs == ["(1) $$E = mc^2$$"]

    def test_a_deposit_missing_its_closing_marker_does_not_leak_the_preamble(self):
        """The two document markers are read independently, because requiring
        both let a truncated deposit fall through to the bare-expression path
        — which then delimited the preamble and doubled the pair, both of the
        failures this function exists to prevent, in one string."""
        xml = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>T</article-title></title-group>
  </article-meta></front>
  <body><sec><title>M</title>
    <disp-formula><tex-math>\\documentclass[12pt]{minimal}\\usepackage{amsmath}\
\\begin{document}$$E = mc^2$$</tex-math></disp-formula>
  </sec></body>
</article>"""
        article = JATSParser(xml).parse()

        assert article.body_sections[0].paragraphs == ["$$E = mc^2$$"]
        assert "documentclass" not in article.body_sections[0].paragraphs[0]
        assert "$$$$" not in article.body_sections[0].paragraphs[0]

    def test_a_body_opening_an_environment_is_not_delimited(self):
        """``$$\\begin{equation}…`` is not valid LaTeX: the environment
        establishes its own math mode. 0.2% of deposits."""
        xml = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>T</article-title></title-group>
  </article-meta></front>
  <body><sec><title>M</title>
    <disp-formula><tex-math>\\begin{document}\\begin{aligned}x &amp;= y\\end{aligned}\
\\end{document}</tex-math></disp-formula>
  </sec></body>
</article>"""
        article = JATSParser(xml).parse()

        assert article.body_sections[0].paragraphs == ["\\begin{aligned}x &= y\\end{aligned}"]

    def test_the_bracket_delimiters_are_recognised(self):
        """``\\[…\\]`` and ``\\(…\\)`` were in ``_LATEX_DELIMITERS`` with
        nothing exercising either — deleting both left the suite green. The
        population measures 0 of 10,193 sampled deposits, so this pins the
        membership rather than a shape anyone has met: ``\\[…\\]`` is a
        display pair and is re-spelled inline, ``\\(…\\)`` is already inline
        and is kept.
        """
        xml = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>T</article-title></title-group>
  </article-meta></front>
  <body><sec><title>M</title><p>a <inline-formula>\
<tex-math>\\begin{document}\\[u\\]\\end{document}</tex-math></inline-formula> b \
<inline-formula><tex-math>\\begin{document}\\(v\\)\\end{document}</tex-math></inline-formula> c</p>
  </sec></body>
</article>"""
        article = JATSParser(xml).parse()

        assert article.body_sections[0].paragraphs == ["a $u$ b \\(v\\) c"]

    def test_a_formula_in_an_abstract_reaches_the_abstract(self):
        """``abstract_sections`` is rendered into the HTML ``FullTextService``
        caches while ``body_sections`` reaches no bmlib path at all, so this
        is the branch of ``_append_prose`` that matters most and the one no
        fixture exercised."""
        xml = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>T</article-title></title-group>
    <abstract><p>We used <inline-formula>\
<tex-math>\\begin{document}$$x$$\\end{document}</tex-math></inline-formula>.</p></abstract>
  </article-meta></front>
  <body><sec><title>M</title><p>Prose.</p></sec></body>
</article>"""
        article = JATSParser(xml).parse()

        assert article.abstract_sections[0].content == "We used $x$."

    def test_a_formula_that_holds_nothing_leaves_no_space_behind(self):
        """``_normalize_whitespace`` collapses a run to one space rather than
        deleting it, so padding an empty rendition welds a gap into the word
        it sits inside. The guard that prevents it was unpinned: an
        image-only formula is 140 of 1,915 display formulas, so the shape is
        the common one rather than a contrivance."""
        xml = b"""<?xml version="1.0"?>
<article xmlns:xlink="http://www.w3.org/1999/xlink">
  <front><article-meta><title-group><article-title>T</article-title></title-group>
  </article-meta></front>
  <body><sec><title>M</title>
    <p>k<inline-formula> </inline-formula>mer here.</p>
    <table-wrap id="t1"><table><tbody><tr><td>a<inline-formula>\
<graphic xlink:href="e.png"/></inline-formula>b</td></tr></tbody></table></table-wrap>
  </sec></body>
</article>"""
        article = JATSParser(xml).parse()

        assert article.body_sections[0].paragraphs == ["kmer here."]
        assert "<td>ab</td>" in article.tables[0].html_content

    def test_a_cell_takes_its_text_back_after_the_formula_closes(self):
        """The hold-back has two edges and only one was pinned.

        ``characters()`` withholds every cell's text while a formula is open.
        Every fixture put the formula last in a one-cell, one-row table, so a
        mutant that never cleared the hold — a sticky flag in place of the
        stack test — blanked every later cell in the table and stayed green
        across the whole suite. That is permanent: ``html_content`` is what
        ``FullTextService`` caches.
        """
        xml = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>T</article-title></title-group>
  </article-meta></front>
  <body><sec><title>M</title>
    <table-wrap id="t1"><table><tbody>
      <tr><td>ratio <inline-formula>\
<tex-math>\\begin{document}$r$\\end{document}</tex-math></inline-formula> total</td>\
<td>second cell</td></tr>
      <tr><td>row two a</td><td>row two b</td></tr>
    </tbody></table></table-wrap>
    <p>After the table.</p>
  </sec></body>
</article>"""
        article = JATSParser(xml).parse()
        cell = article.tables[0].html_content

        # The rest of the formula's own cell, the cell beside it, and every
        # cell of the row after it.
        assert "<td>ratio $r$ total</td>" in cell
        assert "<td>second cell</td>" in cell
        assert "<td>row two a</td>" in cell
        assert "<td>row two b</td>" in cell
        assert article.body_sections[0].paragraphs == ["After the table."]

    def test_a_formula_in_unsectioned_back_matter_now_reaches_the_article(self, parser_log):
        """This test asserted the reverse, and issue #224 overturned it.

        A standalone ``<disp-formula>`` in an unsectioned ``<back>`` — 192 in
        23 of the package's 97,909 articles — used to be built and then lost,
        counted by ``formulas_dropped`` and reported at WARNING. #177 filed
        that containment and named the remedy it deliberately did not take:
        *"giving ``<back>`` prose an implicit section the way ``<body>`` has
        one"*. That is exactly what #224 did, for the prose the same branch
        was dropping, so the larger half of #177 is answered by it and the
        formula rides along. A session finding this should read this comment
        rather than restore the old assertion.

        What is left of ``formulas_dropped`` is the shape below, which is why
        the counter is not now dead.
        """
        xml = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>T</article-title></title-group>
  </article-meta></front>
  <body><sec><title>M</title><p>Prose.</p></sec></body>
  <back><app-group><app id="a1"><disp-formula><label>(A1)</label>\
<tex-math>\\begin{document}$$s = 1$$\\end{document}</tex-math></disp-formula>\
</app></app-group></back>
</article>"""
        article = JATSParser(xml).parse()

        assert [p for s in article.body_sections for p in s.paragraphs] == [
            "Prose.",
            "(A1) $$s = 1$$",
        ]
        assert not [m for m in parser_log.messages(logging.WARNING) if "reached no section" in m]

    def test_a_rendered_formula_that_reaches_nowhere_is_reported(self, parser_log):
        """The half of issue #177 that #224 did not reach.

        Inside a ``<fig>`` with no ``<caption>`` open, the merge allow-list
        sends a formula under an unlisted wrapper — ``<disp-formula-group>``
        here — to the paragraph path, which routes to ``_append_caption_text``
        and drops it, while ``characters()`` has already withheld it from any
        cell. Measured **0** in both corpora, so this half is latent and the
        test is what keeps the counter from going quietly vacuous now that
        #224 has taken its only measured population.

        WARNING and not ERROR: a publisher's deposit reaches this one, so it
        cannot spend the audit's "an ERROR means bmlib is wrong" contract.
        """
        xml = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>T</article-title></title-group>
  </article-meta></front>
  <body><sec><title>M</title><p>Prose.</p>
    <fig id="f1"><disp-formula-group>\
<disp-formula><tex-math>\\begin{document}$$s = 1$$\\end{document}</tex-math>\
</disp-formula></disp-formula-group></fig>
  </sec></body>
</article>"""
        article = JATSParser(xml).parse()

        assert [p for s in article.body_sections for p in s.paragraphs] == ["Prose."]
        assert any(
            "1 display formula(s) were rendered but reached no section, caption, "
            "cell or footnote" in message
            for message in parser_log.messages(logging.WARNING)
        )

    def test_a_formula_in_an_exhibit_footnote_reaches_the_footnote(self, parser_log):
        """``_prose_reaches_output`` mirrors both destinations, not just one.

        A footnote is a place prose is filed since issue #124, and this arm
        asks that predicate before it counts. Mirroring only the caption half
        would report a formula that *did* reach the article as a routing gap —
        a line claiming a loss that did not happen, which is the counter's own
        contract read backwards.
        """
        xml = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>T</article-title></title-group>
  </article-meta></front>
  <body><sec><title>M</title><p>Prose.</p>
    <table-wrap id="t1"><label>Table 1.</label>
      <table><tbody><tr><td>1</td></tr></tbody></table>
      <table-wrap-foot><fn><label>a</label><disp-formula-group>\
<disp-formula><tex-math>\\begin{document}$$s = 1$$\\end{document}</tex-math>\
</disp-formula></disp-formula-group></fn></table-wrap-foot>
    </table-wrap>
  </sec></body>
</article>"""
        article = JATSParser(xml).parse()

        assert [t.footnotes for t in article.tables] == [["a — $$s = 1$$"]]
        assert not [m for m in parser_log.messages(logging.WARNING) if "reached no section" in m]

    def test_the_mirror_asks_the_caption_before_the_footnote(self, parser_log):
        """The predicate mirrors ``_append_prose``'s two branches *in order*.

        The test above kills the deletion of the footnote half and not its
        reordering, which is the rule the implementation comment states. Under
        the reversed order this formula — in an unmodelled caption that is
        itself inside a footnote — is reported as reaching the footnote, so
        the counter reads 0 and the line vanishes while the rendition really is
        dropped: a counter silently under-reporting a real loss, which is the
        same contract read backwards one direction over (PR #237's review).
        """
        xml = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>T</article-title></title-group>
  </article-meta></front>
  <body><sec><title>M</title><p>Prose.</p>
    <table-wrap id="t1"><label>Table 1.</label>
      <table><tbody><tr><td>1</td></tr></tbody></table>
      <table-wrap-foot><fn><label>a</label>
        <supplementary-material id="s1"><caption>\
<disp-formula><tex-math>\\begin{document}$$s = 1$$\\end{document}</tex-math>\
</disp-formula></caption></supplementary-material>
        <p>Adjusted.</p></fn></table-wrap-foot>
    </table-wrap>
  </sec></body>
</article>"""
        article = JATSParser(xml).parse()

        assert [t.footnotes for t in article.tables] == [["a — Adjusted."]]
        assert any(
            "1 display formula(s) were rendered but reached no" in message
            for message in parser_log.messages(logging.WARNING)
        )

    def test_a_formula_that_reaches_its_section_is_not_reported(self, parser_log):
        """The negative control the counter needs: a rule that fires on every
        standalone formula would report every one that is fine — the majority
        on the served rendition, where a <sec> is the commoner parent."""
        xml = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>T</article-title></title-group>
  </article-meta></front>
  <body><sec><title>M</title>\
<disp-formula><tex-math>\\begin{document}$$s = 1$$\\end{document}</tex-math></disp-formula>
  </sec></body>
</article>"""
        article = JATSParser(xml).parse()

        assert article.body_sections[0].paragraphs == ["$$s = 1$$"]
        assert not [m for m in parser_log.messages(logging.WARNING) if "reached no section" in m]


def _article_with_sec(body: bytes) -> bytes:
    """Wrap ``body`` in one titled body ``<sec>`` of a minimal article."""
    return (
        b'<?xml version="1.0"?>\n<article xmlns:xlink="http://www.w3.org/1999/xlink">'
        b"<front><article-meta><title-group><article-title>T</article-title>"
        b"</title-group></article-meta></front>"
        b"<body><sec><title>R</title>" + body + b"</sec></body></article>"
    )


class TestACellsTextIsTheCellsOwn:
    """A cell lends its text to no buffer around it — issue #243.

    ``characters()`` delivered every cell's text to the open buffer *as well
    as* to the cell, so a ``<table-wrap>`` deposited inside a ``<p>`` — legal
    JATS, and 7,248 such deposits sit in 2,237 of the 8,118 served articles of
    ``PMC10030002_PMC10040000.xml.gz`` — stored ``'Before12.3after.'``: the
    table's numbers spliced into a sentence the publisher never wrote that
    way, in ``body_sections`` and in the HTML ``FullTextService`` caches. A
    **wrong value** where a blank was the alternative, which is #116's and #162's own
    preference and what puts this ahead of the drops beside it.

    **The hold is the cell's own text buffer, not a test in
    ``characters()``.** The issue proposed the latter, mirroring the formula
    hold one line up, and it reaches two of the four routes: raw character
    data, and an inline run merging back (``<italic>``, ``<sup>``). It leaves
    the other two, and makes one of them *worse* — an ``<xref>`` builds its
    link from the buffer the hold would have emptied, and the arm's own
    ``text or "Figure"`` fallback then fires, so the paragraph gains
    ``'[Figure](#f1)'`` in place of ``'[Fig 1](#f1)'``: an **invented** label,
    which is #162's own symptom and worse than the blank it replaces. And
    enumerating the arms that merge is the kind of list #116 established
    cannot be completed by inspection, so the argument is about the fifth
    route nobody has found rather than about these four.
    ``td``/``th`` join ``_TEXT_ACCUMULATING`` instead: the cell takes a buffer
    at its open, every child that merges back merges into *that*, and the
    close pops it and reads nothing but its emptiness (``cell_text_dropped``,
    #245). The cell itself is unaffected, filling ``current_cell_text`` from
    ``characters()`` directly.

    Membership needs one exclusion of its own, and
    ``test_a_cell_under_a_mixed_citation_does_not_join_the_citation`` is why:
    ``_inside_mixed_citation()`` was the single path left by which a cell's
    buffer could still merge, so the pop carries ``not is_cell`` beside the
    terms ``_FORMULA_PARTS`` and ``_UNDIVIDED_NAME_ELEMENTS`` already earn.

    The paragraph then reads ``'Beforeafter.'`` — **of the cells** — which is
    the ``<fig>`` shape's own answer, and that shape has always read
    ``'Alphaomega.'`` since a ``<caption>`` takes a buffer and never merged
    one back. Two things it does not clean up, so the claim is not read wider:
    spacing round a merged block is #147's open question, and an
    ``<alt-text>``, ``<attrib>``, ``<long-desc>``, ``<object-id>``,
    ``<copyright-statement>`` or ``<copyright-year>`` accumulated nowhere and
    still welded into the sentence — 537 of those 8,118 articles, issue #248
    beside #241. Neither was touched here; the second is answered since by
    ``TestAnObjectsMetadataIsNotProse`` and ``TestAnAttributionIsRouted``.
    """

    #: The issue's own fixture, verbatim.
    INLINE_TABLE = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>T</article-title></title-group>
  </article-meta></front>
  <body><sec><title>R</title>
    <p>Before<table-wrap id="T1"><table><tbody><tr><td>12.3</td></tr></tbody></table>\
</table-wrap>after.</p>
  </sec></body>
</article>"""

    def _article(self, body: bytes):
        return JATSParser(_article_with_sec(body)).parse()

    def test_the_paragraph_around_an_inline_table_keeps_only_its_own_text(self):
        """The issue's own reproduction: ``'Before12.3after.'`` on ``main``."""
        article = JATSParser(self.INLINE_TABLE).parse()

        assert article.body_sections[0].paragraphs == ["Beforeafter."]

    def test_the_cell_keeps_the_text_the_hold_withholds(self):
        """The negative control: a hold that emptied the cell would pass the
        assertion above and lose the table, which is the larger loss."""
        article = JATSParser(self.INLINE_TABLE).parse()

        assert "<td>12.3</td>" in article.tables[0].html_content

    def test_a_formatted_run_in_a_cell_does_not_reach_the_paragraph(self):
        """``<italic>`` accumulates and merges back, so the hold has to reach
        the merge and not only the raw character data."""
        article = self._article(
            b"<p>Before<table-wrap id='T1'><table><tbody><tr><td>a<italic>b</italic>c"
            b"</td></tr></tbody></table></table-wrap>after.</p>"
        )

        assert article.body_sections[0].paragraphs == ["Beforeafter."]
        assert "<td>abc</td>" in article.tables[0].html_content

    def test_a_cross_reference_in_a_cell_does_not_reach_the_paragraph(self):
        """The route that separates this fix from the issue's own remedy.

        ``<xref>`` does not merge its text — it *replaces* it with a link
        built from the popped buffer — so a hold inside ``characters()`` would
        leave ``'[Figure](#f1)'`` in the sentence, the arm's own
        ``text or "Figure"`` fallback firing on the emptied buffer: a wrong
        value replaced by an *invented* one, which is worse. Pinned with the
        link's own text, so a mutant emptying the buffer instead of isolating
        it is visible here and nowhere else.
        """
        article = self._article(
            b"<p>Before<table-wrap id='T1'><table><tbody><tr><td>"
            b"<xref ref-type='fig' rid='f1'>Fig 1</xref></td></tr></tbody></table>"
            b"</table-wrap>after.</p>"
        )

        assert article.body_sections[0].paragraphs == ["Beforeafter."]
        assert "<td>Fig 1</td>" in article.tables[0].html_content

    def test_a_formula_in_a_cell_does_not_reach_the_paragraph(self):
        """The fourth route: the formula arm appends its chosen rendition to
        the open buffer itself (#147), which ``characters()`` never sees."""
        article = self._article(
            b"<p>Before<table-wrap id='T1'><table><tbody><tr><td>"
            b"<inline-formula><tex-math>$x$</tex-math></inline-formula>"
            b"</td></tr></tbody></table></table-wrap>after.</p>"
        )

        assert article.body_sections[0].paragraphs == ["Beforeafter."]
        assert "<td>$x$</td>" in article.tables[0].html_content

    def test_a_header_cell_is_held_back_like_a_body_cell(self):
        """``<th>`` fills the same builder by the same route."""
        article = self._article(
            b"<p>Before<table-wrap id='T1'><table><thead><tr><th>Dose</th></tr></thead>"
            b"<tbody><tr><td>12.3</td></tr></tbody></table></table-wrap>after.</p>"
        )

        assert article.body_sections[0].paragraphs == ["Beforeafter."]
        assert "<th>Dose</th>" in article.tables[0].html_content

    def test_a_table_inside_a_footnote_does_not_lend_its_cells_to_the_note(self):
        """The second destination, and the one #124 made reachable.

        An exhibit opened inside another's footnote is 0 of 8,118 served and 0
        of 97,909 archive articles, so this pins a direction — but the note is
        a public field the same ``_append_text`` fills, and #124's own walk
        already refuses the *note* to the inner table for the mirror reason.
        """
        article = self._article(
            b"<table-wrap id='T0'><table><tbody><tr><td>x</td></tr></tbody></table>"
            b"<table-wrap-foot><fn><label>a</label><p>See"
            b"<table-wrap id='T1'><table><tbody><tr><td>12.3</td></tr></tbody></table>"
            b"</table-wrap></p></fn></table-wrap-foot></table-wrap>"
        )

        outer = next(t for t in article.tables if t.id == "T0")
        assert outer.footnotes == ["a — See"]

    def test_a_block_table_leaves_the_paragraphs_around_it_untouched(self):
        """The ordinary deposit, where the enclosing buffer is a ``<sec>``'s
        and nothing reads it — so the hold must move nothing here."""
        article = self._article(
            b"<p>Para.</p><table-wrap id='T1'><table><tbody><tr><td>12.3</td></tr>"
            b"</tbody></table></table-wrap><p>Next.</p>"
        )

        assert article.body_sections[0].paragraphs == ["Para.", "Next."]
        assert "<td>12.3</td>" in article.tables[0].html_content

    def test_a_cell_under_a_mixed_citation_does_not_join_the_citation(self, parser_log):
        """The one path by which a cell's buffer could still have merged.

        ``td``/``th`` are not in ``_INLINE_ELEMENTS``, so
        ``_inside_mixed_citation()`` was the whole of it — and that helper is
        a bare ancestor test, deliberately, because mixed content is inherited
        down the entire subtree (#146). A ``<table-wrap>`` or ``<array>``
        below a ``<mixed-citation>`` therefore merged the cell into
        ``JATSReferenceInfo.citation`` exactly as before the fix, while
        ``append_cell_text`` *also* filled the cell — #243's own splice in a
        public field, and the doubled rendition ``_FORMULA_PARTS`` earns its
        own term to prevent. For the unmodelled half it was worse than a
        leak: ``cell_text_dropped`` reported content missing from the article
        that was sitting in a public list.

        ``_FORMULA_PARTS`` and ``_UNDIVIDED_NAME_ELEMENTS`` each state their
        exclusion rather than leaning on the inline set, and this is the third
        of the same shape. Measured 0 cells under a ``<mixed-citation>`` over
        both named artifacts, counted the way the parser routes (suppressed
        regions skipped): 0 of 1,407,638 cells in the 8,118 served articles of
        ``PMC10030002_PMC10040000.xml.gz``, and 0 of 19,651,769 in the 97,909
        of ``oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26``. So it pins a
        direction and not a population. Found in the review of PR #246.
        """
        xml = (
            b'<?xml version="1.0"?>\n<article><front><article-meta><title-group>'
            b"<article-title>T</article-title></title-group></article-meta></front>"
            b"<body><sec><title>R</title><p>x</p></sec></body>"
            b"<back><ref-list><ref id='R1'><mixed-citation>Smith J. "
            b"<source>J Med</source>. <array><tbody><tr><td>CELL</td></tr></tbody></array>"
            b" 2020.</mixed-citation></ref></ref-list></back></article>"
        )
        article = JATSParser(xml).parse()
        handler = JATSParser(xml)._run_parser()

        assert article.references[0].citation == "Smith J. J Med. 2020."
        # And the counter's claim is then true: the cell reached nothing.
        assert handler.cell_text_dropped == 1

    def test_a_modelled_cell_under_a_mixed_citation_keeps_its_text_once(self):
        """The other half, where a builder *is* open.

        Merging would put the cell in the citation string as well as in the
        table, so the text would be stored twice and the citation would read
        as though the publisher had typeset the number into it.
        """
        xml = (
            b'<?xml version="1.0"?>\n<article><front><article-meta><title-group>'
            b"<article-title>T</article-title></title-group></article-meta></front>"
            b"<body><sec><title>R</title><p>x</p></sec></body>"
            b"<back><ref-list><ref id='R1'><mixed-citation>Smith J. "
            b"<source>J Med</source>. <table-wrap id='T1'><table><tbody><tr>"
            b"<td>LEAK</td></tr></tbody></table></table-wrap>"
            b" 2020.</mixed-citation></ref></ref-list></back></article>"
        )
        article = JATSParser(xml).parse()

        assert article.references[0].citation == "Smith J. J Med. 2020."
        assert "<td>LEAK</td>" in article.tables[0].html_content


class TestACellThatReachesNoTableIsCounted:
    """Cell text with no table to receive it leaves a line — issue #245.

    ``<array>`` is JATS's *non-floating* tabular structure: ``<tbody>``,
    ``<tr>`` and ``<td>`` with no ``<table-wrap>`` and no ``<table>`` above
    them — defined by the wrapper's absence and not a ``<table>``'s, which is
    what the arm tests, JATS admitting a ``<table>`` inside an ``<array>``.
    bmlib models none of it, so no ``_TableBuilder`` is open and
    ``append_cell_text`` has nowhere to put the text.

    Until #243 that text still reached the buffer above — the enclosing
    ``<sec>``'s, where it was discarded, or the enclosing ``<p>``'s, where it
    was spliced into the sentence as a run-together string the publisher never
    wrote. Isolating the cell's buffer makes the loss *total* for the second
    shape as well, which is the right direction by this module's own standing
    preference (a blank beats a wrong value, #116 and #162) and is exactly the
    kind of drop it counts rather than excuses.

    **Measured over two named public artifacts**: 355 cells in 8 of the 8,118
    served articles of ``PMC10030002_PMC10040000.xml.gz``, of which 173 in 3
    articles sit inside a ``<p>`` and so were visible as corrupt prose, the
    other 182 in 5 sitting in a ``<glossary>`` where the text was already
    being discarded; and 248,720 in 6,726 of the 97,909 archive articles of
    ``oa_comm_xml.PMC012xxxxxx.baseline.2025-06-26``. Every one is an
    ``<array>``'s. So the counter reports a **pre-existing silent loss** in
    five of those eight served articles and a newly-total one in three.

    **It is keyed on no builder being open, which is narrower than "no table
    received this cell".** An ``<array>`` inside an *open* ``<table-wrap>``
    routes into that builder and takes the silent branch, splicing a phantom
    row into a real table — pre-existing, measured 0 on both artifacts, and
    filed as #247 rather than fixed here.

    The unit is the **cell**, never the character — the rule PR #239's review
    made for #238's image counter — and an empty cell costs nothing, which is
    every sibling counter's rule.
    """

    ARRAY_IN_PROSE = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>T</article-title></title-group>
  </article-meta></front>
  <body><sec><title>R</title>
    <p>Before<array><tbody><tr><td>12.3</td><td>45.6</td></tr></tbody></array>after.</p>
  </sec></body>
</article>"""

    def test_the_cells_of_an_unmodelled_array_are_counted(self, parser_log):
        handler = JATSParser(self.ARRAY_IN_PROSE)._run_parser()

        assert handler.cell_text_dropped == 2

    def test_the_count_is_reported_once_for_the_article(self, parser_log):
        """The number is asserted, not merely the line — #224's counter
        over-reported through a green CI because nothing read the figure."""
        JATSParser(self.ARRAY_IN_PROSE).parse()

        lines = [m for m in parser_log.messages(logging.WARNING) if "reached no table" in m]
        assert len(lines) == 1
        assert "2 table cell(s)" in lines[0]

    def test_an_empty_cell_costs_nothing(self, parser_log):
        """A cell that carried nothing cannot have lost anything, so a line
        here would state a loss that did not happen.

        **This fixture is load-bearing three times over**, which its first
        docstring did not say. It is the sole killer of the mutant counting
        the *unstripped* buffer (``elif element_text:``), of the one counting
        every unmodelled cell (``else:``), and — because with ``td`` out of
        ``_TEXT_ACCUMULATING`` the popped buffer would be the ``<p>``'s and
        the count would go to 2 — it is what shows the counter reads the
        cell's **own** buffer rather than the one enclosing it.
        """
        xml = self.ARRAY_IN_PROSE.replace(b"<td>12.3</td><td>45.6</td>", b"<td></td><td> </td>")
        handler = JATSParser(xml)._run_parser()

        assert handler.cell_text_dropped == 0
        assert not [m for m in parser_log.messages(logging.WARNING) if "reached no table" in m]

    def test_a_header_cell_that_reaches_no_table_is_counted(self, parser_log):
        """``<th>`` takes the same branch, and nothing pinned it.

        ``elif text and name == "td":`` survived the whole file: every
        ``<array>`` fixture here deposits body cells only, and
        ``TestACellsTextIsTheCellsOwn``'s ``<th>`` case is about a *modelled*
        table, where the counter never fires. Found in the review of PR #246.
        """
        xml = self.ARRAY_IN_PROSE.replace(
            b"<array><tbody>", b"<array><thead><tr><th>Dose</th></tr></thead><tbody>"
        )
        handler = JATSParser(xml)._run_parser()

        assert handler.cell_text_dropped == 3

    def test_an_array_outside_a_paragraph_is_counted(self, parser_log):
        """The majority of the measured population, and it had no fixture.

        Of the 355 cells this counter finds in the served bundle, 173 sit in
        a ``<p>`` — where the loss was visible as spliced prose — and the
        other **182, in 5 of the 8 articles, sit in a ``<glossary>``**, where
        the text was already reaching a buffer nobody reads. That second half
        is the counter's whole stated reason for existing (*"a loss that is
        pre-existing and was silent"*) and every fixture in this class put the
        array in a ``<p>``: ``elif text and len(self.text_stack) > 2:``
        survived the file. Found in the review of PR #246.
        """
        xml = self.ARRAY_IN_PROSE.replace(
            b"<p>Before<array><tbody><tr><td>12.3</td><td>45.6</td></tr></tbody></array>after.</p>",
            b"<p>Para.</p><array><tbody><tr><td>12.3</td><td>45.6</td></tr></tbody></array>"
            b"<p>Next.</p>",
        )
        article = JATSParser(xml).parse()
        handler = JATSParser(xml)._run_parser()

        assert handler.cell_text_dropped == 2
        # The prose either side is untouched: the cells reached the <sec>'s
        # buffer before #243 and reach their own now, and neither is read.
        assert article.body_sections[0].paragraphs == ["Para.", "Next."]

    def test_an_array_outside_the_body_is_counted(self, parser_log):
        """``elif text and self.in_body:`` survived the file too.

        Every fixture here sits in ``<body>``. The arm is gated on no routing
        flag and must not become so: a cell reaching no table has lost its
        content wherever it was deposited, and #224 made back matter a
        destination for everything around it.
        """
        array = b"<array><tbody><tr><td>12.3</td></tr></tbody></array>"
        xml = self.ARRAY_IN_PROSE.replace(
            b"</article-meta></front>",
            b"<abstract><p>A" + array + b"B</p></abstract></article-meta></front>",
        ).replace(b"</body>", b"</body><back><ack><p>C" + array + b"D</p></ack></back>")
        handler = JATSParser(xml)._run_parser()

        assert handler.cell_text_dropped == 4

    def test_this_line_survives_beside_another_counters_line(self, parser_log):
        """The audit blocks are independent ``if``s. Chained as an ``elif`` of
        the block above it, this line vanishes whenever that counter also
        fired — the mutant that survived every fixture holding one counter at
        a time, in PR #239's review one issue back."""
        JATSParser(
            _article_with_body("""
    <sec><title>Results</title>
      <table-wrap id="T1"><label>Table 1.</label>
        <table><tbody><tr><td>12.3</td></tr></tbody></table>
        <table-wrap-foot><fn><p>Key: <graphic xlink:href="key.gif"/></p></fn></table-wrap-foot>
      </table-wrap>
      <p>Also<array><tbody><tr><td>7.1</td></tr></tbody></array>here.</p>
    </sec>""")
        ).parse()

        images = [
            m for m in parser_log.messages(logging.WARNING) if "exhibit's footnote matter" in m
        ]
        cells = [m for m in parser_log.messages(logging.WARNING) if "reached no table" in m]
        assert len(images) == 1
        assert len(cells) == 1 and "1 table cell(s)" in cells[0]

    def test_a_modelled_tables_cell_is_not_counted(self, parser_log):
        """The negative control. A cell inside a ``<table-wrap>`` is filed by
        the builder, so a counter that fired on every cell would report the
        whole corpus — 1,407,638 cells in 5,153 of 8,118 served articles."""
        xml = self.ARRAY_IN_PROSE.replace(
            b"<array><tbody><tr><td>12.3</td><td>45.6</td></tr></tbody></array>",
            b"<table-wrap id='T1'><table><tbody><tr><td>12.3</td><td>45.6</td></tr>"
            b"</tbody></table></table-wrap>",
        )
        handler = JATSParser(xml)._run_parser()

        assert handler.cell_text_dropped == 0
        assert not [m for m in parser_log.messages(logging.WARNING) if "reached no table" in m]


class TestAnObjectsMetadataIsNotProse:
    """An object's non-prose metadata reaches no paragraph and no cell — #241, #248.

    ``<alt-text>``, ``<long-desc>``, ``<object-id>`` and ``<permissions>``
    accumulated nowhere and had no arm, so ``characters()`` appended their text
    to whatever buffer was open above the object. For a ``<fig>`` or
    ``<table-wrap>`` deposited inside a ``<p>`` that buffer is the sentence:
    ``'BeforeTable 2after.'``, and Elsevier's house style put two
    ``<alt-text>`` values into PMC10030262 as ``'…in Tables 2.Table 2Table 3'``.
    Measured over the 8,118 served articles of
    ``PMC10030002_PMC10040000.xml.gz``, 4,018 ``<alt-text>`` elements in 522
    articles land in a ``<p>``'s buffer and 67 (in 9) in a table cell; the
    ``<alt-text>`` values are ``"Fig. 1"``, ``"Table 2"``, ``"Image 1"``,
    ``"Multimedia component 1"`` and a figure's DOI, and an archive
    ``<permissions>`` reads ``"© 2024 WILEY-VCH GmbH"``. A **wrong value** where
    a blank is the alternative.

    **Three routes, so three guards, and each test below names its route.**
    Membership of ``_TEXT_ACCUMULATING`` isolates the buffer, which answers
    every child that *merges* — raw character data and an inline run alike —
    the argument #243 made for a cell. It does not answer a child that
    *routes* on its own: a ``<p>`` inside a ``<license>`` goes through
    ``_append_prose`` whatever buffer it sits in, so that method refuses prose
    under this metadata, and ``_prose_reaches_output`` mirrors the refusal so
    the predicate does not answer ``True`` where nothing is filed (on its own
    an equivalent mutant; see the two tests naming it). And a cell is filled
    from ``characters()`` and from the formula arm directly, bypassing every
    buffer, so both reach the cell through one helper that holds this text
    back.

    **Under an ``<xref>`` or a ``<mixed-citation>`` the metadata is kept, on
    every route.** Each claims its descendants' text — an image that *is* a
    cross-reference keeps its label, and a citation is what it prints — and
    the first cut kept it at the buffer pop alone, so a cell still lost it
    (PR #250's review). And a formula's image text alternative is kept as
    that formula's rendition of last resort.
    """

    def test_the_issues_own_inline_table_keeps_only_the_sentence(self):
        """#248's reproduction: ``'BeforeTable 2after.'`` on ``main``."""
        article = JATSParser(
            _article_with_sec(
                b"<p>Before<table-wrap id='T1'><alt-text>Table 2</alt-text>"
                b"<table><tbody><tr><td>12.3</td></tr></tbody></table></table-wrap>after.</p>"
            )
        ).parse()

        assert article.body_sections[0].paragraphs == ["Beforeafter."]
        assert "<td>12.3</td>" in article.tables[0].html_content

    def test_the_issues_own_graphic_keeps_only_the_sentence(self):
        """#241's reproduction, the ``<graphic>`` owner one element down."""
        article = JATSParser(
            _article_with_sec(
                b"<p>Alt <graphic xlink:href='g.gif'><alt-text>ALT</alt-text></graphic> end.</p>"
            )
        ).parse()

        assert article.body_sections[0].paragraphs == ["Alt end."]

    @pytest.mark.parametrize(
        "metadata",
        [
            b"<alt-text>Fig. 1</alt-text>",
            b"<long-desc>A bar graph with four bars.</long-desc>",
            b"<object-id pub-id-type='doi'>10.1371/journal.pone.0283013.g001</object-id>",
            b"<permissions><copyright-statement>\xc2\xa9 2023 Getty Images</copyright-statement>"
            b"<copyright-year>2023</copyright-year></permissions>",
        ],
        ids=["alt-text", "long-desc", "object-id", "permissions"],
    )
    def test_no_member_welds_into_the_sentence_around_an_inline_figure(self, metadata):
        """Each member separately, so a set missing one of them is visible."""
        article = JATSParser(
            _article_with_sec(
                b"<p>Before<fig id='f1'><caption><p>Cap.</p></caption>"
                + metadata
                + b"<graphic xlink:href='f1.gif'/></fig>after.</p>"
            )
        ).parse()

        assert article.body_sections[0].paragraphs == ["Beforeafter."]
        assert article.figures[0].caption == "Cap."

    def test_a_formatted_run_inside_the_metadata_stays_with_it(self):
        """The merge route: an inline child merges into the metadata's own
        buffer, not the sentence's — the ``<italic>`` case #243 pinned for a
        cell. A ``<license-p>`` accumulates nothing, so its ``<ext-link>``
        reaches the ``<permissions>`` buffer."""
        article = JATSParser(
            _article_with_sec(
                b"<p>Before<fig id='f1'><long-desc>A <italic>bar</italic> graph</long-desc>"
                b"<permissions><license><license-p>CC <ext-link>by</ext-link></license-p>"
                b"</license></permissions><graphic xlink:href='f1.gif'/></fig>after.</p>"
            )
        ).parse()

        assert article.body_sections[0].paragraphs == ["Beforeafter."]

    def test_an_image_in_an_exhibits_footnote_does_not_describe_itself_in_the_note(self):
        """#241's second reproduction: the note stored ``'a — Marked thus: A
        dagger'`` while #238's WARNING said the image was filed nowhere."""
        article = JATSParser(
            _article_with_sec(
                b"<table-wrap id='T1'><table><tbody><tr><td>12.3a</td></tr></tbody></table>"
                b"<table-wrap-foot><fn><label>a</label><p>Marked thus: "
                b"<graphic xlink:href='mark.gif'><alt-text>A dagger</alt-text></graphic>"
                b"</p></fn></table-wrap-foot></table-wrap>"
            )
        ).parse()

        assert article.tables[0].footnotes == ["a — Marked thus:"]

    @pytest.mark.parametrize(
        "metadata",
        [
            b"<alt-text>Image 1</alt-text>",
            b"<long-desc>A dagger</long-desc>",
            b"<object-id>10.1/x.i001</object-id>",
            b"<permissions><copyright-statement>WILEY-VCH</copyright-statement></permissions>",
        ],
        ids=["alt-text", "long-desc", "object-id", "permissions"],
    )
    def test_an_images_metadata_does_not_reach_the_cell(self, metadata):
        """The cell route, which no buffer reaches: ``characters()`` fills the
        cell directly, so accumulating alone left ``'12.3Image 1'`` in the
        rendered table — 67 served elements in 9 articles, 462 archive ones in
        49.

        Every member separately, because membership of
        ``_NON_PROSE_METADATA`` is a second decision beside membership of
        ``_TEXT_ACCUMULATING``: dropping ``long-desc`` or ``object-id`` from the
        named set alone survived the sentence-level tests, which the buffer
        still answered.

        The cell's own text *after* the image is the other edge: a hold that
        stayed on until ``</td>`` would pass a fixture ending at the image.
        """
        article = JATSParser(
            _article_with_sec(
                b"<table-wrap id='T1'><table><tbody><tr><td>12.3"
                b"<inline-graphic xlink:href='i1.gif'>"
                + metadata
                + b"</inline-graphic>b</td></tr></tbody></table></table-wrap>"
            )
        ).parse()

        assert "<td>12.3b</td>" in article.tables[0].html_content

    def test_a_formula_inside_the_metadata_does_not_reach_the_cell(self):
        """The cell's second route: the formula arm appends its rendition to
        the cell itself (#147), never through ``characters()``. JATS admits no
        formula in an ``<alt-text>``, so this is well-formed and invalid — it
        pins that the two routes share one hold rather than a population."""
        article = JATSParser(
            _article_with_sec(
                b"<table-wrap id='T1'><table><tbody><tr><td>12.3"
                b"<inline-graphic xlink:href='i1.gif'><alt-text>"
                b"<inline-formula><tex-math>$x$</tex-math></inline-formula>"
                b"</alt-text></inline-graphic></td></tr></tbody></table></table-wrap>"
            )
        ).parse()

        assert "<td>12.3</td>" in article.tables[0].html_content

    def test_the_cells_own_text_and_formula_still_reach_it(self):
        """The negative control for both cell routes: a hold that emptied
        every cell would pass the two tests above."""
        article = JATSParser(
            _article_with_sec(
                b"<table-wrap id='T1'><table><tbody><tr><td>12.3"
                b"<inline-formula><tex-math>$x$</tex-math></inline-formula></td></tr>"
                b"</tbody></table></table-wrap>"
            )
        ).parse()

        assert "<td>12.3$x$</td>" in article.tables[0].html_content

    @pytest.mark.parametrize(
        ("opening", "closing"),
        [
            (b"<permissions><license>", b"</license></permissions>"),
            (b"<alt-text>", b"</alt-text>"),
            (b"<long-desc>", b"</long-desc>"),
            (b"<object-id>", b"</object-id>"),
        ],
        ids=["permissions", "alt-text", "long-desc", "object-id"],
    )
    def test_a_paragraph_inside_the_metadata_is_not_filed_as_prose(self, opening, closing):
        """The routing route. ``<license>`` was modelled ``(p)+`` before JATS
        spelled it ``<license-p>``, and a ``<p>`` goes through its own arm
        whatever buffer surrounds it — so a ``<graphic>`` standing in a
        section filed its licence as the section's first paragraph. All 19
        such ``<p>`` in the archive artifact sit in ``<article-meta>``, where
        the paragraph fell past every branch anyway until issue #230 routed
        front matter — so this fixture pins the section direction, and
        :meth:`TestJATSParserFrontMatterProse.test_a_front_matter_licence_paragraph_is_still_declined`
        pins the population.

        Every member, though JATS admits a ``<p>`` in only the first: the
        refusal is keyed on the set, and narrowing it to ``<permissions>``
        survived the whole file while one member was the only fixture (PR
        review).
        """
        article = JATSParser(
            _article_with_sec(
                b"<graphic xlink:href='g.gif'>"
                + opening
                + b"<p>Licensed CC-BY.</p>"
                + closing
                + b"</graphic><p>Next.</p>"
            )
        ).parse()

        assert article.body_sections[0].paragraphs == ["Next."]

    def test_a_paragraph_inside_the_metadata_is_not_filed_as_a_footnote(self):
        """The same refusal at the exhibit's second destination, #124's."""
        article = JATSParser(
            _article_with_sec(
                b"<table-wrap id='T1'><table><tbody><tr><td>12.3</td></tr></tbody></table>"
                b"<table-wrap-foot><fn><p>Real note.</p></fn><graphic xlink:href='g.gif'>"
                b"<permissions><license><p>Licensed.</p></license></permissions></graphic>"
                b"</table-wrap-foot></table-wrap>"
            )
        ).parse()

        assert article.tables[0].footnotes == ["Real note."]

    def test_a_definition_term_is_not_spent_on_a_refused_paragraph(self):
        """Spent on the licence paragraph ``_append_prose`` then declines, the
        term would leave the definition with no word defined — #228's own
        defect, reached through this fix.

        **Two independent protections, so either alone is an equivalent
        mutant.** The refusal runs ahead of the fold, and even placed after it
        ``_prose_reaches_output`` answers ``False`` inside the metadata, which
        is what stops the fold spending the term. Breaking both reddens this.
        """
        article = JATSParser(
            _article_with_sec(
                b"<def-list><def-item><term>BMI</term><def>"
                b"<graphic xlink:href='g.gif'><permissions><license><p>Licensed.</p>"
                b"</license></permissions></graphic><p>body mass index</p>"
                b"</def></def-item></def-list>"
            )
        ).parse()

        assert article.body_sections[0].paragraphs == ["BMI — body mass index"]

    @pytest.mark.parametrize(
        "container",
        [(b"", b""), (b"<fig id='f1'>", b"</fig>")],
        ids=["in-a-section", "in-a-float"],
    )
    def test_a_display_formula_inside_the_metadata_is_declined_and_not_reported(
        self, parser_log, container
    ):
        """A licence is declined, not lost, so ``formulas_dropped`` must not
        claim it. **For this counter the subtraction in the formula arm is the
        protection**, and it needs both shapes: in a section the mirror is what
        makes ``_prose_reaches_output`` answer ``False`` at all, so removing the
        mirror *and* the subtraction together read 0 there and survived the
        file (PR review); in a float or in ``<front>`` the predicate answers
        ``False`` whatever the mirror says, so only the subtraction stands
        between the formula and a WARNING claiming a loss. Invalid JATS and
        well-formed, so a direction and no population."""
        opening, closing = container
        handler = JATSParser(
            _article_with_sec(
                opening
                + b"<graphic xlink:href='g.gif'><permissions><license>"
                + b"<disp-formula><tex-math>$$s = 1$$</tex-math></disp-formula>"
                + b"</license></permissions></graphic>"
                + closing
                + b"<p>Next.</p>"
            )
        )._run_parser()

        assert handler.body_sections[0].paragraphs == ["Next."]
        assert handler.formulas_dropped == 0
        assert not [m for m in parser_log.messages(logging.WARNING) if "reached no section" in m]

    @pytest.mark.parametrize(
        ("ref_type", "rid", "label"),
        [("fig", "f1", b"Figure 1"), ("table", "t1", b"Table 1")],
        ids=["figure", "table"],
    )
    def test_an_images_text_alternative_still_labels_a_cross_reference(self, ref_type, rid, label):
        """Kept outside a citation, here. An ``<xref>`` replaces its text with a
        link label and invents ``"Figure"`` or ``"Table"`` for an empty one, so
        isolating the ``<alt-text>`` of an image that *is* the reference turned
        ``[Figure 1](#f1)`` into ``[Figure](#f1)`` — an invented label, #162's
        symptom. Inside an ``<xref>`` the metadata merges as before. Both
        ``ref-type`` arms, because each has its own fallback and a rule keyed
        on the figure alone passed the figure fixture (PR #250's review). Found
        by PR review; no member lands in an ``<xref>`` in either artifact."""
        article = JATSParser(
            _article_with_sec(
                f"<p>See <xref ref-type='{ref_type}' rid='{rid}'>".encode()
                + b"<inline-graphic xlink:href='i.gif'><alt-text>"
                + label
                + b"</alt-text></inline-graphic></xref> here.</p>"
            )
        ).parse()

        assert article.body_sections[0].paragraphs == [f"See [{label.decode()}](#{rid}) here."]

    def test_an_images_text_alternative_still_labels_a_cross_reference_in_a_cell(self):
        """The same exception on the cell route, which no buffer reaches. The
        first cut held the text back there whatever enclosed it, so ``main``'s
        ``See Figure 1`` rendered as ``See`` while four documents said the
        parse under an ``<xref>`` was exactly ``main``'s (PR #250's review)."""
        article = JATSParser(
            _article_with_sec(
                b"<table-wrap id='T1'><table><tbody><tr><td>See "
                b"<xref ref-type='fig' rid='f1'><inline-graphic xlink:href='i.gif'>"
                b"<alt-text>Figure 1</alt-text></inline-graphic></xref></td></tr></tbody>"
                b"</table></table-wrap>"
            )
        ).parse()

        assert "<td>See Figure 1</td>" in article.tables[0].html_content

    def test_an_object_id_in_a_citation_in_a_cell_is_still_printed(self):
        """The citation's half of the cell exception."""
        article = JATSParser(
            _article_with_sec(
                b"<table-wrap id='T1'><table><tbody><tr><td><mixed-citation>Smith "
                b"<object-id>OID-7</object-id> 2020</mixed-citation></td></tr></tbody>"
                b"</table></table-wrap>"
            )
        ).parse()

        assert "<td>Smith OID-7 2020</td>" in article.tables[0].html_content

    def test_a_cross_reference_inside_the_metadata_is_declined_with_it(self):
        """The walk from the root decides: a claimer *inside* declined
        metadata claims nothing. Asked as two independent tests — "is a member
        open?" and "is a claimer open?" — the ``<xref>`` would put its text into
        the cell although the ``<alt-text>`` around it is declined."""
        article = JATSParser(
            _article_with_sec(
                b"<table-wrap id='T1'><table><tbody><tr><td>A"
                b"<inline-graphic xlink:href='i.gif'><alt-text>x "
                b"<xref ref-type='bibr' rid='b1'>y</xref></alt-text></inline-graphic>B"
                b"</td></tr></tbody></table></table-wrap>"
            )
        ).parse()

        assert "<td>AB</td>" in article.tables[0].html_content

    @pytest.mark.parametrize(
        ("formula", "expected"),
        [
            (
                b"<p>where <inline-formula><inline-graphic xlink:href='a.gif'>"
                b"<alt-text>alpha</alt-text></inline-graphic></inline-formula> is the rate.</p>",
                ["where alpha is the rate."],
            ),
            (
                b"<p>Text.</p><disp-formula id='e1'><label>(1)</label>"
                b"<graphic xlink:href='e.gif'><alt-text>E =\n   mc2</alt-text></graphic>"
                b"</disp-formula><p>After.</p>",
                ["Text.", "(1) E = mc2", "After."],
            ),
            (
                b"<p>where <inline-formula><alternatives><inline-graphic xlink:href='a.gif'>"
                b"<alt-text>alpha</alt-text></inline-graphic><inline-graphic xlink:href='a.tif'>"
                b"<alt-text>ALPHA</alt-text></inline-graphic></alternatives></inline-formula>"
                b" is.</p>",
                ["where alpha is."],
            ),
        ],
        ids=["inline", "display-labelled", "first-of-alternatives"],
    )
    def test_a_formulas_image_text_alternative_is_its_rendition(self, formula, expected):
        """An image-only formula whose deposit spells it out keeps that text.
        Declining every ``<alt-text>`` took it out of the formula's buffer, so
        the inline one read ``'where is the rate.'`` and the labelled display
        one rendered as nothing, its ``(1)`` with it — both the text ``main``
        printed (PR #250's review). 0 in both artifacts.

        The display fixture wraps its text, because a standalone formula is
        routed as its own paragraph and nothing normalises it after this; and
        the first of two encodings of one image wins, ``latex``'s rule."""
        article = JATSParser(_article_with_sec(formula)).parse()

        assert article.body_sections[0].paragraphs == expected

    def test_a_formulas_own_encoding_wins_over_its_images_text_alternative(self):
        """Last resort, not a second rendition: a MathML flattening is the
        formula's text, and merging the image's ``<alt-text>`` beside it
        printed one expression twice, ``'where xalpha is.'``, as ``main``
        did."""
        article = JATSParser(
            _article_with_sec(
                b"<p>where <inline-formula><mml:math xmlns:mml='http://www.w3.org/1998/Math/MathML'>"
                b"<mml:mi>x</mml:mi></mml:math><inline-graphic xlink:href='a.gif'>"
                b"<alt-text>alpha</alt-text></inline-graphic></inline-formula> is.</p>"
            )
        ).parse()

        assert article.body_sections[0].paragraphs == ["where x is."]

    def test_a_block_figure_leaves_the_paragraphs_around_it_untouched(self):
        """The ordinary deposit, whose metadata reached a ``<sec>``'s unread
        buffer on ``main`` — so the change must move nothing here."""
        article = JATSParser(
            _article_with_sec(
                b"<p>Para.</p><fig id='f1'><label>Figure 1</label><caption><p>Cap.</p></caption>"
                b"<alt-text>Fig. 1</alt-text><object-id>10.1/x.g001</object-id>"
                b"<graphic xlink:href='f1.gif'/></fig><p>Next.</p>"
            )
        ).parse()

        assert article.body_sections[0].paragraphs == ["Para.", "Next."]
        assert article.figures[0].label == "Figure 1"
        assert article.figures[0].caption == "Cap."
        assert article.figures[0].graphic_url == "f1.gif"

    def test_an_object_id_in_a_mixed_citation_is_still_printed(self):
        """The citation rule is #146's and this change leaves it alone: every
        descendant of a ``<mixed-citation>`` is the citation's text, so
        membership of ``_TEXT_ACCUMULATING`` merges it back there exactly as
        ``characters()`` delivered it before. 0 of these metadata elements sit
        under a ``<mixed-citation>`` in either named artifact."""
        xml = (
            b'<?xml version="1.0"?>\n<article><front><article-meta><title-group>'
            b"<article-title>T</article-title></title-group></article-meta></front>"
            b"<body><sec><title>R</title><p>x</p></sec></body>"
            b"<back><ref-list><ref id='R1'><mixed-citation>Smith J. <source>J Med</source>. "
            b"<object-id>OID-7</object-id> 2020.</mixed-citation></ref></ref-list></back>"
            b"</article>"
        )

        assert JATSParser(xml).parse().references[0].citation == "Smith J. J Med. OID-7 2020."


class TestAnAttributionIsRouted:
    """An ``<attrib>`` is printed content, and it is filed where a reader sees it.

    Unlike the four elements above, an attribution is typeset: an interview
    quote's ``"(P2, CP)"``, a figure's ``"Source: Authors' elaboration."``, a
    table's abbreviation list. It too accumulated nowhere, so it welded into
    the sentence where its owner stood in a ``<p>`` (1,331 quote attributions
    in 94 of the archive artifact's 97,909 articles) and, where its owner stood
    in a ``<sec>``, reached that section's unread buffer and was **lost with no
    line** — 3,844 of its 5,266 quote attributions, in 217 articles, the
    majority. Discarding it, the issues' own remedy, would have made that loss
    total.

    So it routes as a ``<p>`` does — a quote's attribution becomes a paragraph
    after the quote, one inside ``<table-wrap-foot>`` a table note — with one
    addition decided by what it credits (#116's parent test, walked past a
    ``<graphic>``): an exhibit's attribution, or its image's, is filed among
    that exhibit's footnotes, which render below it, because routed as a
    ``<p>`` it would reach neither destination ``_append_prose`` offers inside
    a float. Four positions take it first — under an ``<xref>`` or a
    ``<mixed-citation>`` it is that element's text, under declined metadata it
    is declined, and in a cell it is the cell's — and it differs from a ``<p>``
    in three further ways: an empty one adds nothing, it spends no pending
    marker or term (unless it is the whole of its note), and prose that
    reaches nothing is counted (``attributions_dropped``). Each has a test
    below.
    """

    def test_a_quotes_attribution_follows_the_quote(self):
        """The lost-in-silence half: a quote standing in its section."""
        article = JATSParser(
            _article_with_sec(
                b"<p>Intro.</p><disp-quote><p>It hurt.</p><attrib>(P2, CP)</attrib>"
                b"</disp-quote><p>Next.</p>"
            )
        ).parse()

        assert article.body_sections[0].paragraphs == ["Intro.", "It hurt.", "(P2, CP)", "Next."]

    def test_a_quotes_attribution_does_not_weld_into_the_sentence_around_it(self):
        """The welded half. The quote's own paragraph already lands ahead of
        the one it interrupts (#147's shape for a block in a ``<p>``), and the
        attribution follows it there."""
        article = JATSParser(
            _article_with_sec(
                b"<p>They said <disp-quote><p>It hurt.</p><attrib>(P2)</attrib></disp-quote>"
                b" often.</p>"
            )
        ).parse()

        assert article.body_sections[0].paragraphs == ["It hurt.", "(P2)", "They said often."]

    def test_a_figures_own_attribution_is_its_note(self):
        """Routed as a ``<p>`` this reaches no destination — ``in_figure``,
        no caption open, no footnote container above — which is why the parent
        test decides it first."""
        article = JATSParser(
            _article_with_sec(
                b"<fig id='f1'><label>Figure 1</label><caption><p>Cap.</p></caption>"
                b"<graphic xlink:href='f1.gif'/><attrib>Source: WHO.</attrib></fig><p>Next.</p>"
            )
        ).parse()

        assert article.figures[0].footnotes == ["Source: WHO."]
        assert article.figures[0].caption == "Cap."
        assert article.body_sections[0].paragraphs == ["Next."]

    def test_a_tables_own_attribution_follows_its_marked_notes(self):
        """JATS puts ``(attrib | permissions)*`` after ``<table-wrap-foot>``,
        so document order lists it last."""
        article = JATSParser(
            _article_with_sec(
                b"<table-wrap id='T1'><table><tbody><tr><td>12.3a</td></tr></tbody></table>"
                b"<table-wrap-foot><fn><label>a</label><p>Adjusted.</p></fn></table-wrap-foot>"
                b"<attrib>Source: Registry.</attrib></table-wrap>"
            )
        ).parse()

        assert article.tables[0].footnotes == ["a — Adjusted.", "Source: Registry."]

    def test_an_attribution_in_a_table_nested_in_a_figure_is_the_tables(self):
        """The parent test and not an ambient "is a figure open?": inside a
        ``<fig>`` the ambient flag is true for every descendant, and it would
        file the nested table's note as the figure's. A ``<table-wrap>``
        directly in a ``<fig>`` is deposited once in the served artifact."""
        article = JATSParser(
            _article_with_sec(
                b"<fig id='f1'><caption><p>Cap.</p></caption><graphic xlink:href='f1.gif'/>"
                b"<table-wrap id='T1'><table><tbody><tr><td>12.3</td></tr></tbody></table>"
                b"<attrib>Source: Registry.</attrib></table-wrap></fig>"
            )
        ).parse()

        assert article.tables[0].footnotes == ["Source: Registry."]
        assert article.figures[0].footnotes == []

    def test_an_attribution_in_a_table_foot_is_a_table_note(self):
        """Reached through ``_append_prose`` and #124's owner walk, not the
        parent test: its parent is the foot."""
        article = JATSParser(
            _article_with_sec(
                b"<table-wrap id='T1'><table><tbody><tr><td>12.3</td></tr></tbody></table>"
                b"<table-wrap-foot><attrib>Fonte: Autoria pr\xc3\xb3pria.</attrib>"
                b"</table-wrap-foot></table-wrap>"
            )
        ).parse()

        assert article.tables[0].footnotes == ["Fonte: Autoria própria."]

    def test_an_inline_figures_attribution_leaves_the_sentence(self):
        """The welded exhibit half: the note is kept and the sentence is clean."""
        article = JATSParser(
            _article_with_sec(
                b"<p>Before<fig id='f1'><graphic xlink:href='f1.gif'/>"
                b"<attrib><italic>Abbreviations</italic>: BMI, body mass index.</attrib>"
                b"</fig>after.</p>"
            )
        ).parse()

        assert article.body_sections[0].paragraphs == ["Beforeafter."]
        assert article.figures[0].footnotes == ["Abbreviations: BMI, body mass index."]

    def test_an_attribution_wrapped_across_lines_is_normalised(self):
        """Filed through a public field, so a depositor's line break does not
        survive into it (the ``'J.\\nTan'`` lesson of #146)."""
        article = JATSParser(
            _article_with_sec(
                b"<fig id='f1'><graphic xlink:href='f1.gif'/><attrib>Source:\n   WHO.</attrib>"
                b"</fig>"
            )
        ).parse()

        assert article.figures[0].footnotes == ["Source: WHO."]

    def test_a_tables_attribution_wrapped_across_lines_is_normalised(self):
        """The table branch is a separate call; filing ``element_text`` there
        passed every other fixture (PR #250's review)."""
        article = JATSParser(
            _article_with_sec(
                b"<table-wrap id='T1'><table><tbody><tr><td>1</td></tr></tbody></table>"
                b"<attrib>Source:\n   Registry.</attrib></table-wrap>"
            )
        ).parse()

        assert article.tables[0].footnotes == ["Source: Registry."]

    def test_a_quotes_attribution_wrapped_across_lines_is_normalised(self):
        """The same rule on the prose route, which is a separate call."""
        article = JATSParser(
            _article_with_sec(b"<disp-quote><p>Q.</p><attrib>(P2,\n      CP)</attrib></disp-quote>")
        ).parse()

        assert article.body_sections[0].paragraphs == ["Q.", "(P2, CP)"]

    def test_an_empty_attribution_files_nothing(self):
        """An empty deposit costs nothing, at either destination."""
        article = JATSParser(
            _article_with_sec(
                b"<disp-quote><p>Q.</p><attrib> </attrib></disp-quote>"
                b"<fig id='f1'><graphic xlink:href='f1.gif'/><attrib/></fig>"
            )
        ).parse()

        assert article.body_sections[0].paragraphs == ["Q."]
        assert article.figures[0].footnotes == []

    def test_a_quote_in_a_cell_keeps_its_attribution_in_the_cell_only(self):
        """The negative control for the cell hold: an attribution is the
        cell's content, so it is not held back from the cell — and routed as
        a ``<p>`` it is dropped from prose, as a cell's ``<p>`` is."""
        article = JATSParser(
            _article_with_sec(
                b"<p>Para.</p><table-wrap id='T1'><table><tbody><tr><td>"
                b"<disp-quote><p>Q.</p><attrib>(P2)</attrib></disp-quote>"
                b"</td></tr></tbody></table></table-wrap>"
            )
        ).parse()

        assert "(P2)" in article.tables[0].html_content
        assert article.tables[0].footnotes == []
        assert article.body_sections[0].paragraphs == ["Para."]

    def test_an_attribution_owned_by_an_unmodelled_element_routes_as_its_paragraph(self):
        """Wherever a ``<p>`` would go: a ``<supplementary-material>``'s own
        caption prose is filed as the section's (#137's shape), and its
        attribution follows it."""
        article = JATSParser(
            _article_with_sec(
                b"<supplementary-material id='S1'><caption><p>Table S1.</p></caption>"
                b"<attrib>Source: X.</attrib></supplementary-material>"
            )
        ).parse()

        assert article.body_sections[0].paragraphs == ["Table S1.", "Source: X."]

    def test_an_image_credit_does_not_take_a_notes_marker(self):
        """An ``<attrib>`` inside a ``<graphic>`` inside a note's ``<p>``
        closes before that ``<p>``, so routed as prose it arrived first and
        took the marker: ``['a — Credit: X.', 'Adjusted for age.']``, the body's
        ``12.3a`` pointing at the credit. It files without spending the
        marker. Found by PR review; 0 in both artifacts, 12 of the 13 archive
        ``<graphic><attrib>`` sitting in a ``<bio>`` paragraph and 1 directly
        in an unsectioned ``<body>``."""
        article = JATSParser(
            _article_with_sec(
                b"<table-wrap id='T1'><table><tbody><tr><td>12.3a</td></tr></tbody></table>"
                b"<table-wrap-foot><fn><label>a</label><p><graphic xlink:href='m.gif'>"
                b"<attrib>Credit: X.</attrib></graphic>Adjusted for age.</p></fn>"
                b"</table-wrap-foot></table-wrap>"
            )
        ).parse()

        assert article.tables[0].footnotes == ["Credit: X.", "a — Adjusted for age."]

    def test_an_image_credit_that_is_the_whole_note_takes_its_marker(self, parser_log):
        """Where the note deposits nothing beside the image, the credit *is*
        the note. Leaving the marker for prose that never came stored
        ``['Photo: Getty.']`` against a body reading ``12.3b``, and #124's
        WARNING said bmlib filed no prose for a note it had just filed — where
        ``main`` stored ``'b — Photo: Getty.'`` (PR #250's review)."""
        handler = JATSParser(
            _article_with_sec(
                b"<table-wrap id='T1'><table><tbody><tr><td>12.3b</td></tr></tbody></table>"
                b"<table-wrap-foot><fn><label>b</label><p><graphic xlink:href='m.gif'>"
                b"<attrib>Photo: Getty.</attrib></graphic></p></fn></table-wrap-foot>"
                b"</table-wrap>"
            )
        )._run_parser()

        assert handler.build_tables()[0].footnotes == ["b — Photo: Getty."]
        assert handler.footnote_markers_dropped == 0
        assert not [
            m for m in parser_log.messages(logging.WARNING) if "footnote marker(s) were read" in m
        ]

    def test_the_first_of_several_credits_takes_the_marker(self):
        """The marker labels the note's first entry, where a reader looks for
        it — the rule a note deposited as several paragraphs already follows."""
        article = JATSParser(
            _article_with_sec(
                b"<table-wrap id='T1'><table><tbody><tr><td>12.3b</td></tr></tbody></table>"
                b"<table-wrap-foot><fn><label>b</label><p><graphic xlink:href='m.gif'>"
                b"<attrib>Photo: A.</attrib></graphic><graphic xlink:href='n.gif'>"
                b"<attrib>Photo: B.</attrib></graphic></p></fn></table-wrap-foot></table-wrap>"
            )
        ).parse()

        assert article.tables[0].footnotes == ["b — Photo: A.", "Photo: B."]

    def test_a_marker_after_its_notes_prose_is_not_folded_into_an_earlier_credit(self):
        """The credit's slot is released once prose spends a marker, so a
        second ``<label>`` in the same ``<fn>`` — invalid, well-formed — is not
        folded into the credit filed under the first. It is counted as the
        unspent marker it is."""
        handler = JATSParser(
            _article_with_sec(
                b"<table-wrap id='T1'><table><tbody><tr><td>12.3a</td></tr></tbody></table>"
                b"<table-wrap-foot><fn><label>a</label><p><graphic xlink:href='m.gif'>"
                b"<attrib>Credit: X.</attrib></graphic>Adjusted.</p><label>b</label></fn>"
                b"</table-wrap-foot></table-wrap>"
            )
        )._run_parser()

        assert handler.build_tables()[0].footnotes == ["Credit: X.", "a — Adjusted."]
        assert handler.footnote_markers_dropped == 1

    def test_an_image_credit_does_not_take_a_definitions_term(self):
        """The same run with a ``<term>`` pending: ``'BMI — Credit: X.'``
        would say BMI means the credit."""
        article = JATSParser(
            _article_with_sec(
                b"<def-list><def-item><term>BMI</term><def><p><graphic xlink:href='g.gif'>"
                b"<attrib>Credit: X.</attrib></graphic>body mass index</p></def></def-item>"
                b"</def-list>"
            )
        ).parse()

        assert article.body_sections[0].paragraphs == ["Credit: X.", "BMI — body mass index"]

    def test_a_figures_image_credit_is_the_figures_note(self):
        """An ``<attrib>`` on the figure's own ``<graphic>`` credits the
        figure's image. Its parent is the ``<graphic>``, so a bare parent test
        dropped it inside the float with nothing counted; the owner is walked
        past the ``<graphic>`` as ``_graphic_owner`` walks. Found by PR review."""
        article = JATSParser(
            _article_with_sec(
                b"<p>Before<fig id='f1'><graphic xlink:href='f1.gif'>"
                b"<attrib>Credit: Shutterstock.</attrib></graphic></fig>after.</p>"
            )
        ).parse()

        assert article.figures[0].footnotes == ["Credit: Shutterstock."]
        assert article.body_sections[0].paragraphs == ["Beforeafter."]

    @pytest.mark.parametrize(
        ("exhibit", "attribute"),
        [
            (
                b"<fig id='f1'><alternatives><graphic xlink:href='a.tif'>"
                b"<attrib>Credit: X.</attrib></graphic><graphic xlink:href='a.jpg'/>"
                b"</alternatives></fig>",
                "figures",
            ),
            (
                b"<table-wrap id='T1'><alternatives><graphic xlink:href='a.gif'>"
                b"<attrib>Credit: X.</attrib></graphic><table><tbody><tr><td>1</td></tr>"
                b"</tbody></table></alternatives></table-wrap>",
                "tables",
            ),
        ],
        ids=["figure", "table"],
    )
    def test_an_image_credit_walks_past_the_wrappers_to_its_exhibit(self, exhibit, attribute):
        """``_graphic_owner``'s transparent wrappers, walked from the credit.
        Ignoring them sent the credit to ``_append_prose``, which files nothing
        inside a float — and the mutant survived the file (PR #250's review)."""
        article = JATSParser(_article_with_sec(exhibit + b"<p>Next.</p>")).parse()

        assert getattr(article, attribute)[0].footnotes == ["Credit: X."]
        assert article.body_sections[0].paragraphs == ["Next."]

    def test_a_figure_in_a_cell_files_its_own_attribution(self):
        """The exhibit is asked before the cell, so a ``<fig>`` deposited in a
        ``<td>`` keeps its credit as its note. ``characters()`` puts it in the
        cell as well, as it does the figure's caption."""
        article = JATSParser(
            _article_with_sec(
                b"<table-wrap id='T1'><table><tbody><tr><td><fig id='f1'>"
                b"<graphic xlink:href='f.gif'/><attrib>Credit: X.</attrib></fig></td></tr>"
                b"</tbody></table></table-wrap>"
            )
        ).parse()

        assert article.figures[0].footnotes == ["Credit: X."]
        assert article.tables[0].footnotes == []

    def test_an_unmodelled_owners_attribution_in_a_figure_in_a_cell_stays_in_the_cell(
        self, parser_log
    ):
        """``characters()`` offers text to the innermost open *table*, so a
        ``<fig>`` in a ``<td>`` puts everything it holds into the cell. The
        cell walk therefore ends at a ``<table-wrap>`` and not at a ``<fig>``:
        ending at both counted this attribution as one that reached nothing
        while the cell carried it (found by mutation)."""
        handler = JATSParser(
            _article_with_sec(
                b"<table-wrap id='T1'><table><tbody><tr><td><fig id='f1'>"
                b"<graphic xlink:href='f.gif'/><supplementary-material>"
                b"<attrib>Source: SUPP.</attrib></supplementary-material></fig></td></tr>"
                b"</tbody></table></table-wrap>"
            )
        )._run_parser()

        assert "Source: SUPP." in handler.build_tables()[0].html_content
        assert handler.build_figures()[0].footnotes == []
        assert handler.attributions_dropped == 0

    def test_an_attribution_in_a_table_nested_in_a_cell_is_the_inner_tables_note(self):
        """The other edge of the cell walk: a ``<table-wrap>`` opened inside a
        cell is the innermost table then, and its foot's attribution is its
        note. A walk that did not end there left it to the outer cell's
        buffer, which renders nothing — lost with no line."""
        article = JATSParser(
            _article_with_sec(
                b"<table-wrap id='T1'><table><tbody><tr><td><table-wrap id='T2'><table><tbody>"
                b"<tr><td>1</td></tr></tbody></table><table-wrap-foot>"
                b"<attrib>Source: inner.</attrib></table-wrap-foot></table-wrap></td></tr>"
                b"</tbody></table></table-wrap>"
            )
        ).parse()

        notes = {table.id: table.footnotes for table in article.tables}
        assert notes == {"T1": [], "T2": ["Source: inner."]}

    def test_an_attribution_in_a_cross_reference_labels_it(self):
        """Claimed by the ``<xref>``, as an ``<alt-text>`` is. Routed, it became
        a paragraph of its own and left the label empty for ``"Figure"`` to be
        invented — ``['Fig 1', 'See [Figure](#f1) here.']``. Invalid JATS and
        well-formed; found by PR #250's review."""
        article = JATSParser(
            _article_with_sec(
                b"<p>See <xref ref-type='fig' rid='f1'><attrib>Fig 1</attrib></xref> here.</p>"
            )
        ).parse()

        assert article.body_sections[0].paragraphs == ["See [Fig 1](#f1) here."]

    def test_an_attribution_in_an_arrays_cell_is_that_cells_loss(self, parser_log):
        """An ``<array>``'s cell text is dropped and counted (#245), and an
        attribution in the cell is part of it. Routed as a paragraph it filed
        ``'(P)'`` while ``cell_text_dropped`` fell to zero, one cell's content
        half counted and half filed (PR #250's review)."""
        handler = JATSParser(
            _article_with_sec(
                b"<p>Before<array><tbody><tr><td><attrib>(P)</attrib></td></tr></tbody>"
                b"</array>after.</p>"
            )
        )._run_parser()

        assert handler.body_sections[0].paragraphs == ["Beforeafter."]
        assert handler.cell_text_dropped == 1
        assert handler.attributions_dropped == 0

    def test_an_attribution_inside_declined_metadata_is_declined_with_it(self, parser_log):
        """Not filed, and not counted as a loss: declined metadata is counted
        nowhere, and ``_prose_reaches_output`` answering ``False`` there must
        not read as an attribution that reached nothing."""
        handler = JATSParser(
            _article_with_sec(
                b"<p>A<inline-graphic xlink:href='i.gif'><alt-text>x<attrib>Cr</attrib>"
                b"</alt-text></inline-graphic>B</p>"
            )
        )._run_parser()

        assert handler.body_sections[0].paragraphs == ["AB"]
        assert handler.attributions_dropped == 0

    def test_an_attribution_that_reaches_nothing_is_counted(self, parser_log):
        """Owned by an element bmlib does not model, inside a float, an
        attribution reaches no destination — where on ``main`` it welded into
        the sentence around the figure. A blank is the module's preference,
        and a blank it argues for earns a line (PR #250's review)."""
        handler = JATSParser(
            _article_with_sec(
                b"<p>Before<fig id='f1'><graphic xlink:href='f.gif'/><supplementary-material>"
                b"<attrib>Source: SUPP.</attrib></supplementary-material></fig>after.</p>"
            )
        )._run_parser()

        assert handler.body_sections[0].paragraphs == ["Beforeafter."]
        assert handler.attributions_dropped == 1
        lines = [
            m
            for m in parser_log.messages(logging.WARNING)
            if "attribution(s) were read and filed nowhere" in m
        ]
        assert len(lines) == 1 and ": 1 attribution(s)" in lines[0]

    @pytest.mark.parametrize(
        ("xml", "where"),
        [
            (
                b"<article><front><article-meta><title-group><article-title>T</article-title>"
                b"</title-group><abstract><p>Abs.</p><disp-quote><p>Q.</p><attrib>(P1)</attrib>"
                b"</disp-quote></abstract></article-meta></front><body><p>x</p></body></article>",
                "abstract",
            ),
            (
                b"<article><front><article-meta><title-group><article-title>T</article-title>"
                b"</title-group></article-meta></front><body><p>x</p></body><back><ack>"
                b"<disp-quote><p>Q.</p><attrib>(P1)</attrib></disp-quote></ack></back></article>",
                "back",
            ),
            (
                b"<article><front><article-meta><title-group><article-title>T</article-title>"
                b"</title-group></article-meta></front><body><p>x</p></body><back><ref-list>"
                b"<ref id='R1'><element-citation><attrib>(P1)</attrib></element-citation></ref>"
                b"</ref-list></back></article>",
                "refused",
            ),
        ],
        ids=["abstract", "unsectioned-back", "refused-apparatus"],
    )
    def test_an_attribution_that_is_filed_or_refused_is_not_counted_as_lost(
        self, parser_log, xml, where
    ):
        """The counter's negative control, over the destinations a ``<p>`` has
        outside a section: each is filed or refused by a rule with its own
        count, so none is an attribution that reached nothing."""
        handler = JATSParser(xml)._run_parser()

        assert handler.attributions_dropped == 0
        if where == "abstract":
            assert "(P1)" in handler.abstract_sections[0].content
        elif where == "back":
            assert "(P1)" in [p for s in handler.body_sections for p in s.paragraphs]
        else:
            assert handler.refused_apparatus_prose == 1

    def test_an_attribution_in_a_mixed_citation_is_filed_once(self, parser_log):
        """#146 merges it into the citation, so routing it as well stored it
        twice — or, in a back ``<ref-list>``, counted a refusal of text that
        was filed. Found by PR review; 0 in both artifacts."""
        xml = (
            b'<?xml version="1.0"?>\n<article><front><article-meta><title-group>'
            b"<article-title>T</article-title></title-group></article-meta></front>"
            b"<body><sec><title>R</title><p>x</p></sec></body>"
            b"<back><ref-list><ref id='R1'><mixed-citation>Smith J. <source>J Med</source>. "
            b"<attrib>Cited by permission.</attrib></mixed-citation></ref></ref-list></back>"
            b"</article>"
        )
        handler = JATSParser(xml)._run_parser()

        assert handler.references[0].citation == "Smith J. J Med. Cited by permission."
        assert [p for s in handler.body_sections for p in s.paragraphs] == ["x"]
        assert handler.refused_apparatus_prose == 0

    def test_an_attribution_is_rendered_below_its_figure(self):
        """``FullTextService`` caches the HTML, so the note has to reach it —
        in the figure's own footnote block, after its caption, and not as a
        section paragraph, which a bare substring test would also accept."""
        html = JATSParser(
            _article_with_sec(
                b"<fig id='f1'><label>Figure 1</label><caption><p>Cap.</p></caption>"
                b"<graphic xlink:href='f1.gif'/><attrib>Source: WHO.</attrib></fig>"
            )
        ).to_html()

        assert (
            '</figcaption>\n  <div class="fn-group">\n    <p>Source: WHO.</p>\n  </div>\n</figure>'
            in html
        )
        assert html.count("Source: WHO.") == 1


class TestAnUnparseableSpanCostsOneCellAndNotTheArticle:
    """``colspan`` is CDATA, so a value ``int()`` refuses must not raise — #129.

    ``startElement`` read the span with a bare ``int()``. A ``ValueError``
    raised inside a SAX callback propagates out of :meth:`JATSParser.parse`,
    and every call site in ``fulltext/service.py`` sits under a tier-level
    ``except Exception`` logging at DEBUG — so one malformed attribute on one
    cell cost the whole article, and the tier chain then reported it as
    *unavailable from that source*, which is a far larger claim than "this
    table has a bad span".

    A cell spanning one column instead of two is a cosmetic defect in one
    table. Losing the article is not.
    """

    def test_a_non_numeric_colspan_does_not_raise(self):
        data = _table_containing(
            "<table><tbody><tr><td colspan='two'>12.3</td></tr></tbody></table>"
        )

        article = JATSParser(data).parse()

        assert article.tables[0].label == "Table 1."

    def test_a_non_numeric_colspan_yields_a_single_column_cell(self):
        """The fallback is 1, not "drop the cell" and not "keep the raw text".

        Asserted on the rendered markup rather than on the absence of an
        exception: a fallback that emitted ``colspan="two"`` into the HTML, or
        that swallowed the cell entirely, also raises nothing.
        """
        data = _table_containing(
            "<table><tbody><tr><td colspan='two'>12.3</td><td>4.5</td></tr></tbody></table>"
        )

        html = JATSParser(data).parse().tables[0].html_content

        assert html.count("<td>") == 2
        assert "two" not in html
        assert "12.3" in html and "4.5" in html

    def test_a_non_numeric_colspan_is_named_at_debug(self, parser_log):
        """DEBUG rather than silence, and the *value* rather than the fact.

        The assertion names the value because a bare "colspan" substring
        matches nothing this line uniquely owns — the reject would pass
        against a line reading "ignoring colspan".
        """
        data = _table_containing(
            "<table><tbody><tr><th colspan='1.5'>Group</th></tr></tbody></table>"
        )

        JATSParser(data).parse()

        assert any("'1.5'" in message for message in parser_log.messages())

    def test_an_empty_colspan_is_not_reported(self, parser_log):
        """``colspan=""`` is an absent value, not a malformed one.

        The ``or "1"`` ahead of the ``int()`` predates the fallback and now
        looks redundant — remove it and an empty attribute reaches the
        ``except`` and still yields one column. What it buys is silence:
        without it every ``colspan=""`` in a corpus reports itself as
        unparseable, and DEBUG stops distinguishing the values worth looking
        at. Mutation-verified — this is the only test that removal fails.
        """
        data = _table_containing("<table><tbody><tr><td colspan=''>12.3</td></tr></tbody></table>")

        html = JATSParser(data).parse().tables[0].html_content

        assert html.count("<td>") == 1
        assert not [m for m in parser_log.messages() if "colspan" in m]

    def test_a_well_formed_colspan_still_spans(self):
        """The negative control: the fallback must not swallow a good value.

        A span is rendered as repeated cells rather than as a ``colspan``
        attribute — ``end_cell`` appends ``colspan - 1`` empty ones — so the
        two cases are told apart by the cell *count*, which is also why the
        test above can assert one cell for a value that will not parse.
        """
        data = _table_containing("<table><tbody><tr><td colspan='2'>12.3</td></tr></tbody></table>")

        html = JATSParser(data).parse().tables[0].html_content

        assert html.count("<td>") == 2


def _drop_end_tag(monkeypatch, tag: str) -> None:
    """Make ``_JATSHandler`` never see one closing tag.

    ``expat`` rejects an unbalanced *document* before ``parse()`` returns, so
    no input can reach the end-of-parse audit — it fires only when the
    handler is wrong. Swallowing one ``endElement`` call is the smallest
    faithful stand-in for that class of defect.

    It is a stand-in and not a re-enactment: #115, #123 and #130 would each
    have unwound *clean*, which is exactly why they went undetected. The audit
    is prospective for that class — see ``_parse_audit``'s module docstring.
    """
    original = _JATSHandler.endElement

    def patched(self, name):
        if name == tag:
            return
        original(self, name)

    monkeypatch.setattr(_JATSHandler, "endElement", patched)


#: One ``<contrib>``, declared an editor's, so bmlib pushes a frame for it and
#: reserves no author slot. The separator between ``open_contribs`` and
#: ``unfilled_author_slots``.
_EDITOR_ONLY_ARTICLE = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <article-id pub-id-type="pmc">PMC1234567</article-id>
    <title-group><article-title>An editor and no author</article-title></title-group>
    <contrib-group content-type="editor">
      <contrib><name><surname>Adeyemi</surname><given-names>K</given-names></name></contrib>
    </contrib-group>
  </article-meta></front>
</article>"""


_AUDITED_ARTICLE = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <article-id pub-id-type="pmc">PMC1234567</article-id>
    <title-group><article-title>Real article</article-title></title-group>
    <pub-date pub-type="epub"><year>2024</year></pub-date>
    <contrib-group content-type="author">
      <contrib><name><surname>Adeyemi</surname><given-names>K</given-names></name></contrib>
    </contrib-group>
    <abstract><p>Background and results.</p></abstract>
  </article-meta></front>
  <body>
    <sec><title>Results</title>
      <p>Body prose.</p>
      <fig id="f1"><label>Figure 1.</label>
        <caption><p>A caption.</p></caption>
        <graphic xlink:href="f1.jpg"/>
      </fig>
      <table-wrap id="t1"><label>Table 1.</label>
        <table><tbody><tr><td>12.3</td></tr></tbody></table>
      </table-wrap>
    </sec>
  </body>
</article>"""


#: The audited article with a display formula in it, for the one stack
#: ``_AUDITED_ARTICLE`` does not exercise.
_AUDITED_FORMULA_ARTICLE = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <article-id pub-id-type="pmc">PMC1234567</article-id>
    <title-group><article-title>Real article</article-title></title-group>
    <contrib-group content-type="author">
      <contrib><name><surname>Adeyemi</surname><given-names>K</given-names></name></contrib>
    </contrib-group>
  </article-meta></front>
  <body>
    <sec><title>Results</title>
      <p>Body prose.</p>
      <disp-formula id="e1"><label>(1)</label>\
<tex-math>\\begin{document}$$E = mc^2$$\\end{document}</tex-math></disp-formula>
    </sec>
  </body>
</article>"""


class TestTheParseIsAuditedWhenItEnds:
    """An unbalanced handler must not fail silently — issue #134.

    Every stack and counter on ``_JATSHandler`` decides where content is
    *routed*, and ``_run_parser()`` returned the handler without looking at
    any of them. A parse ending with one unbalanced produced a thin article,
    an article missing its last sections, or an article whose remaining prose
    was filed as caption text, and said nothing.

    These are black-box: the document is well-formed and the *handler* is made
    to drop one closing tag, which is the only shape of defect that can reach
    the audit. Asserting through the real parser rather than on
    ``unwind_state()`` directly is what pins the capture as well as the
    predicate — a struct that agreed with a capture agreeing with nothing
    would satisfy ``test_parse_audit.py`` in full.
    """

    def test_a_figure_left_open_is_reported_at_error(self, monkeypatch, parser_log):
        parser_log.expect_errors()
        _drop_end_tag(monkeypatch, "fig")

        JATSParser(_AUDITED_ARTICLE).parse()

        assert any("<fig> still open" in m for m in parser_log.messages(logging.ERROR))

    def test_a_section_left_open_is_reported(self, monkeypatch, parser_log):
        parser_log.expect_errors()
        _drop_end_tag(monkeypatch, "sec")

        JATSParser(_AUDITED_ARTICLE).parse()

        assert any("<sec> still open" in m for m in parser_log.messages(logging.ERROR))

    def test_a_table_left_open_is_reported(self, monkeypatch, parser_log):
        parser_log.expect_errors()
        _drop_end_tag(monkeypatch, "table-wrap")

        JATSParser(_AUDITED_ARTICLE).parse()

        assert any("<table-wrap> still open" in m for m in parser_log.messages(logging.ERROR))

    def test_a_caption_left_open_is_reported(self, monkeypatch, parser_log):
        parser_log.expect_errors()
        _drop_end_tag(monkeypatch, "caption")

        JATSParser(_AUDITED_ARTICLE).parse()

        assert any("<caption> still open" in m for m in parser_log.messages(logging.ERROR))

    def test_a_contrib_group_left_open_is_reported(self, monkeypatch, parser_log):
        parser_log.expect_errors()
        _drop_end_tag(monkeypatch, "contrib-group")

        JATSParser(_AUDITED_ARTICLE).parse()

        assert any("<contrib-group> still open" in m for m in parser_log.messages(logging.ERROR))

    def test_a_formula_left_open_is_reported(self, monkeypatch, parser_log):
        """The stack #147 added, captured from the handler rather than posed.

        Its own fixture because ``_AUDITED_ARTICLE`` carries no formula, and
        adding one there would make every other test in this class depend on
        an element none of them is about.
        """
        parser_log.expect_errors()
        _drop_end_tag(monkeypatch, "disp-formula")

        JATSParser(_AUDITED_FORMULA_ARTICLE).parse()

        assert any(
            "<inline-formula>/<disp-formula> still open" in m
            for m in parser_log.messages(logging.ERROR)
        )

    def test_a_leftover_text_buffer_is_reported(self, monkeypatch, parser_log):
        """``<p>`` accumulates its own buffer, so a dropped ``</p>`` strands one."""
        parser_log.expect_errors()
        _drop_end_tag(monkeypatch, "p")

        JATSParser(_AUDITED_ARTICLE).parse()

        assert any("text buffer" in m for m in parser_log.messages(logging.ERROR))

    def test_a_stuck_routing_flag_is_reported(self, monkeypatch, parser_log):
        """``<abstract>`` sets a flag rather than pushing a stack."""
        parser_log.expect_errors()
        _drop_end_tag(monkeypatch, "abstract")

        JATSParser(_AUDITED_ARTICLE).parse()

        assert any("in_abstract" in m for m in parser_log.messages(logging.ERROR))

    def test_the_element_stack_is_reported_by_name(self, monkeypatch, parser_log):
        """The residue is named, not counted.

        The outermost tag is dropped rather than an inner one because
        ``endElement`` pops ``element_stack`` blindly: swallow ``</fig>`` and
        the next close pops ``fig`` in its place, so the stack ends holding
        the *outermost* element either way. Which is itself worth knowing —
        the names an imbalance leaves behind identify the depth it happened
        at, not the element that caused it.
        """
        parser_log.expect_errors()
        _drop_end_tag(monkeypatch, "article")

        JATSParser(_AUDITED_ARTICLE).parse()

        assert any(
            "element stack not unwound (article)" in message
            for message in parser_log.messages(logging.ERROR)
        )

    def test_the_diagnostic_names_the_article(self, monkeypatch, parser_log):
        """An ERROR with no identity is unactionable in a bulk sync.

        The parse that produced it is one of thousands, and the operator's
        next question is always *which article*.
        """
        parser_log.expect_errors()
        _drop_end_tag(monkeypatch, "fig")

        JATSParser(_AUDITED_ARTICLE).parse()

        errors = parser_log.messages(logging.ERROR)
        # `all` over an empty list is vacuously true, and this test passed
        # against the unaudited parser until the emptiness check was added.
        assert errors
        assert all("PMC1234567" in message for message in errors)

    def test_a_well_formed_document_is_audited_and_says_nothing(self, parser_log):
        """The negative control, and the one the whole module leans on.

        ``parser_log`` fails any test in this file that provokes an ERROR, so
        every other fixture here is already a false-positive check. This one
        states the claim outright rather than leaving it implicit in the
        absence of a failure.
        """
        JATSParser(_AUDITED_ARTICLE).parse()

        assert parser_log.messages(logging.ERROR) == []


class TestEveryEntryPointIsAudited:
    """``_run_parser()`` is the one place ``parse``, ``to_html`` and
    ``parse_with_html`` all funnel through, which is why the audit sits there
    rather than in ``parse()``. This pins that claim: give ``to_html`` its own
    parse path later and these fail.

    Each asserts the *specific* diagnostic rather than "some ERROR happened",
    which a second unrelated imbalance would have satisfied just as well.
    """

    def test_run_parser_itself_audits(self, monkeypatch, parser_log):
        """The design claim, which the two below cannot make.

        ``to_html`` and ``parse_with_html`` both delegate to ``parse()``, so
        they hold equally if the audit is moved out of ``_run_parser`` and
        into ``parse`` — mutation-confirmed green. What is actually claimed is
        that the *funnel* audits, so the funnel is called directly.
        """
        parser_log.expect_errors()
        _drop_end_tag(monkeypatch, "fig")

        JATSParser(_AUDITED_ARTICLE)._run_parser()

        assert any("<fig> still open" in m for m in parser_log.messages(logging.ERROR))

    def test_to_html_is_audited(self, monkeypatch, parser_log):
        parser_log.expect_errors()
        _drop_end_tag(monkeypatch, "fig")

        JATSParser(_AUDITED_ARTICLE).to_html()

        assert any("<fig> still open" in m for m in parser_log.messages(logging.ERROR))

    def test_parse_with_html_is_audited(self, monkeypatch, parser_log):
        parser_log.expect_errors()
        _drop_end_tag(monkeypatch, "fig")

        JATSParser(_AUDITED_ARTICLE).parse_with_html()

        assert any("<fig> still open" in m for m in parser_log.messages(logging.ERROR))

    def test_the_audit_runs_exactly_once_per_entry_point(self, monkeypatch, parser_log):
        """``parse_with_html`` builds one handler and must not audit twice.

        A duplicated line in a bulk sync reads as two broken articles.
        """
        parser_log.expect_errors()
        _drop_end_tag(monkeypatch, "fig")

        JATSParser(_AUDITED_ARTICLE).parse_with_html()

        assert len([m for m in parser_log.messages(logging.ERROR) if "<fig> still open" in m]) == 1


def _article_with_front(front: str) -> bytes:
    """Wrap ``front`` markup in a minimal well-formed JATS article."""
    return f"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <article-id pub-id-type="pmc">PMC1234567</article-id>
    <title-group><article-title>Real article</article-title></title-group>
{front}
  </article-meta></front>
  <body><sec><title>Results</title><p>Body prose.</p></sec></body>
</article>""".encode()


class TestAZeroAuthorParseIsNotSilent:
    """A parse yielding no authors reports itself — issue #121.

    ``_build_html`` has ``if h.authors:`` with no ``else``, and
    ``FullTextService`` caches the result, so the correct answer and the
    catastrophic one were the same empty list rendered the same way and
    persisted to disk. Issue #111 dropped every author from 57% of
    open-access articles and survived undetected until it was found from
    *outside* bmlib, while porting the parser to Swift. Nothing here ever
    said a word.

    **WARNING, not ERROR.** Unlike the end-of-parse audit beside it, this
    branch can fire on a well-formed document that bmlib parsed correctly:
    #121's own 1,025-article measurement found exactly one such article after
    #111 was fixed — ``PMC12803704``, an ``article-type="correction"`` that is
    genuinely author-less and still carries surnames in its ``<front>``. So
    the claim is "look at this", not "bmlib is wrong", and ERROR keeps meaning
    only the second.
    """

    def test_front_surnames_with_no_authors_warn(self, parser_log):
        data = _article_with_front(
            '<contrib-group content-type="editor">'
            "<contrib><name><surname>Okafor</surname></name></contrib>"
            "</contrib-group>"
        )

        article = JATSParser(data).parse()

        assert article.authors == []
        assert any("no authors" in m for m in parser_log.messages(logging.WARNING))

    def test_the_warning_counts_the_contributors_and_names_the_article(self, parser_log):
        """The count is what separates a near miss from a wholesale drop."""
        data = _article_with_front(
            '<contrib-group content-type="editor">'
            "<contrib><name><surname>Okafor</surname></name></contrib>"
            "<contrib><name><surname>Lindqvist</surname></name></contrib>"
            "</contrib-group>"
        )

        JATSParser(data).parse()

        warnings = parser_log.messages(logging.WARNING)
        assert warnings
        assert all("PMC1234567" in message for message in warnings)
        assert any("named 2 contributor(s)" in message for message in warnings)

    def test_an_article_carrying_no_front_surname_does_not_warn(self, parser_log):
        """The genuinely author-less article, which is not a defect claim.

        This is the distinction the counter exists for. Without it the
        detector fires on every correction notice, and a warning that fires on
        the correct answer is a warning nobody reads.
        """
        data = _article_with_front("")

        article = JATSParser(data).parse()

        assert article.authors == []
        assert parser_log.messages(logging.WARNING) == []

    def test_a_reference_surname_does_not_count(self, parser_log):
        """``<back>`` is full of surnames, and none of them is a contributor.

        Counted document-wide, every author-less article with a bibliography
        would look like a parser defect — which is why the counter is gated on
        ``in_front`` rather than on the element name alone.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <article-id pub-id-type="pmc">PMC1234567</article-id>
    <title-group><article-title>Correction</article-title></title-group>
  </article-meta></front>
  <body><sec><title>Results</title><p>Body prose.</p></sec></body>
  <back><ref-list><ref id="r1"><element-citation>
    <person-group><name><surname>Marchetti</surname></name></person-group>
  </element-citation></ref></ref-list></back>
</article>"""

        JATSParser(data).parse()

        assert parser_log.messages(logging.WARNING) == []

    def test_an_article_with_authors_says_nothing(self, parser_log):
        """The negative control: the detector must be silent on the good case."""
        data = _article_with_front(
            '<contrib-group content-type="author">'
            "<contrib><name><surname>Adeyemi</surname></name></contrib>"
            "</contrib-group>"
        )

        article = JATSParser(data).parse()

        assert len(article.authors) == 1
        assert parser_log.messages(logging.WARNING) == []

    def test_the_counter_survives_the_routing_decision_it_watches(self, monkeypatch, parser_log):
        """#111 itself: the contrib is real, and the role test rejects it.

        This is the discriminating case, and the reason the counter is keyed
        on ``in_front`` — a structural fact — rather than on ``in_contrib``,
        which is set only once ``_is_author_contrib`` has said yes. Keyed on
        the routing decision, the counter goes to zero in exactly the
        situation it exists to detect, and the detector reports the
        catastrophic parse as a genuinely author-less article. Every other
        fixture in this class passes either way.
        """
        monkeypatch.setattr(_JATSHandler, "_is_author_contrib", lambda self, contrib_type: False)
        data = _article_with_front(
            '<contrib-group content-type="author">'
            "<contrib><name><surname>Adeyemi</surname></name></contrib>"
            "</contrib-group>"
        )

        article = JATSParser(data).parse()

        assert article.authors == []
        assert any("no authors" in m for m in parser_log.messages(logging.WARNING))


class TestTheAuditNetIsComplete:
    """The "add a flag to the handler, add it here" rule, mechanised — #134.

    ``_ROUTING_FLAGS`` is a tuple of attribute *names*, so nothing but prose
    kept it in step with the handler. Both halves of that were live defects
    when this class was written: ``implicit_body_section`` was missing from
    the net (a whole unsectioned ``<body>`` could vanish with the audit
    silent), and when this class was written all but one of the names then
    listed could be deleted with the suite
    still green.
    """

    #: Handler attributes that are *outputs* or bookkeeping, not routing
    #: state, so a non-clean value at end of parse costs nothing.
    _NOT_ROUTING = frozenset(
        {
            "_locator",
            "abstract_sections",
            "attributions_dropped",
            "body_paragraph_count",
            "body_sections",
            "cell_text_dropped",
            "contribs_naming_nobody",
            "definition_terms_dropped",
            "doi",
            "doi_is_typed",
            "elocation_id",
            "elocation_parts_dropped",
            "footnote_graphics_dropped",
            "footnote_headings_dropped",
            "footnote_markers_dropped",
            "formulas_dropped",
            "front_contributor_name_count",
            "funding_statements",
            "issue",
            "journal",
            "last_pages_dropped",
            "non_publication_years_refused",
            "page_range_awaits_last_page",
            "pages",
            "pmc_id",
            "pmid",
            "references",
            "refused_apparatus_prose",
            "rejected_spans",
            "suppressed_nested_articles",
            "title",
            "volume",
            "year",
        }
    )

    #: Routing state read by a *dedicated* ``ParseUnwindState`` field rather
    #: than through the grouped ``stuck_flags``.
    _AUDITED_AS_A_STACK = frozenset(
        {
            "author_slots",
            "caption_stack",
            "contrib_stack",
            "contrib_group_stack",
            "def_item_stack",
            "element_stack",
            "figure_slots",
            "figure_stack",
            "formula_stack",
            "heading_stack",
            "nested_article_depth",
            "section_stack",
            "table_slots",
            "table_stack",
            "text_stack",
        }
    )

    #: Deliberately excluded, each because ``</abstract>`` flushes without
    #: clearing — only a *subsequent* ``<abstract>`` open clears — so both are
    #: non-empty at the end of every article carrying a titled abstract.
    #: Auditing either would fire on almost every real document.
    _DELIBERATELY_EXCLUDED = frozenset({"current_abstract_text", "current_abstract_title"})

    def test_the_audit_covers_every_routing_flag(self):
        """A flag added to the handler and not to the net is a hole in it.

        The exclusions above are named individually, so adding a flag makes
        this fail rather than silently widening the blind spot — which is the
        whole failure mode ``_parse_audit`` exists to end, one level up.
        """
        handler = _JATSHandler()
        accounted = (
            set(_JATSHandler._ROUTING_FLAGS)
            | self._NOT_ROUTING
            | self._AUDITED_AS_A_STACK
            | self._DELIBERATELY_EXCLUDED
        )

        unaccounted = sorted(set(vars(handler)) - accounted)

        assert not unaccounted, (
            f"handler attributes reaching neither the audit nor a named exclusion: {unaccounted}"
        )

    def test_every_routing_flag_names_a_real_attribute(self):
        """``getattr`` has no default, so a stale name raises on every parse.

        That failure is loud rather than silent — it reddens this whole module
        — but it lands in ``service.py``'s tier-level ``except Exception`` in
        production, which loses the article. Naming it here says which flag.
        """
        handler = _JATSHandler()

        missing = [name for name in _JATSHandler._ROUTING_FLAGS if not hasattr(handler, name)]

        assert not missing, f"_ROUTING_FLAGS names attributes the handler does not have: {missing}"

    def test_a_clean_parse_leaves_every_audited_flag_falsy(self):
        """The false-positive half: every listed flag must clear on its own.

        A flag that is legitimately truthy at end of parse would make the
        audit ERROR on ordinary articles — which is exactly why the two
        abstract fields are excluded, and the trap that caught the first
        draft.
        """
        handler = _run_handler(_AUDITED_ARTICLE)

        stuck = [name for name in _JATSHandler._ROUTING_FLAGS if getattr(handler, name)]

        assert not stuck, f"a well-formed document left these set: {stuck}"


def _run_handler(data: bytes) -> _JATSHandler:
    """Parse ``data`` and hand back the handler, for capture-side assertions."""
    return JATSParser(data)._run_parser()


_NESTED_ARTICLE_DOCUMENT = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <article-id pub-id-type="pmc">PMC2222222</article-id>
    <title-group><article-title>Host article</article-title></title-group>
    <contrib-group content-type="author">
      <contrib><name><surname>Nakamura</surname></name></contrib>
    </contrib-group>
  </article-meta></front>
  <body><sec><title>Results</title><p>Host prose.</p></sec></body>
  <sub-article article-type="peer-review">
    <front-stub><title-group>
      <article-title>Review round 1</article-title>
    </title-group>
    <!-- A nested article's own date (issue #261). Neither the open nor the
         close reaches the handler, so the declared type is never read and
         never stranded; set above the suppression it would leave the slot
         set at end of parse for every article carrying a dated review round,
         and the audit would ERROR on a parse bmlib got right. -->
    <pub-date pub-type="nihms-submitted"><year>2019</year></pub-date>
    </front-stub>
    <body><sec><title>Reviewer 1</title><p>Reviewer prose.</p></sec></body>
  </sub-article>
  <response response-type="author-comment">
    <!-- The set is two elements, and every fixture named only the first.
         `<response>` admits `<front-stub>` alone, so its own dated reply is
         the second half of the suppression's population (PR #274's review). -->
    <front-stub><pub-date pub-type="pmc-release"><year>2021</year></pub-date></front-stub>
    <body><sec><title>Reply</title><p>Author prose.</p></sec></body>
  </response>
</article>"""


#: A definition list whose term is filed, for the audit's capture half and its
#: false-positive control.
_DEFINITION_LIST_DOCUMENT = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <title-group><article-title>Glossary article</article-title></title-group>
  </article-meta></front>
  <body><sec><title>Abbreviations</title><def-list>
    <def-item><term>BMI</term><def><p>body mass index</p></def></def-item>
  </def-list></sec></body>
</article>"""


_CONTAINER_HEADING_DOCUMENT = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <title-group><article-title>Acknowledged article</article-title></title-group>
  </article-meta></front>
  <body><sec><title>Methods</title><p>We did the thing.</p></sec></body>
  <back><ack><title>Acknowledgements</title>
    <list><list-item><p>Thanks.</p></list-item></list></ack>
    <notes><p>A later note.</p></notes></back>
</article>"""


class TestTheAuditCapturesWhatItReports:
    """The capture half, which the pure tests in ``test_parse_audit.py`` cannot reach.

    ``unwind_state()`` maps handler state onto ``ParseUnwindState``. A field
    hardcoded to its clean value there is an imbalance the predicates would
    describe perfectly and never be handed — and three fields were in exactly
    that position: ``nested_article_depth`` and both slot counts could each be
    pinned to zero with the whole suite green.
    """

    def test_a_nested_article_left_open_is_captured(self, monkeypatch, parser_log):
        """The imbalance that costs the *rest of the document*, not just its own content.

        While ``nested_article_depth`` is above zero every handler is
        suppressed, so an unbalanced ``<sub-article>`` discards everything
        after it — which is why its diagnostic is ordered first.
        """
        parser_log.expect_errors()
        _drop_end_tag(monkeypatch, "sub-article")

        JATSParser(_NESTED_ARTICLE_DOCUMENT).parse()

        assert any(
            "<sub-article>/<response> still open" in m for m in parser_log.messages(logging.ERROR)
        )

    def test_a_definition_item_left_open_is_captured(self, monkeypatch, parser_log):
        """``len(def_item_stack)`` reaching the struct, which no pure test can see.

        Hardcoded to zero there, the diagnostic beside it would describe the
        imbalance perfectly and never be handed one — the position three
        fields were already in when this class was written.
        """
        parser_log.expect_errors()
        _drop_end_tag(monkeypatch, "def-item")

        JATSParser(_DEFINITION_LIST_DOCUMENT).parse()

        assert any("<def-item> still open" in m for m in parser_log.messages(logging.ERROR))

    def test_a_container_heading_left_open_is_captured(self, monkeypatch, parser_log):
        """``len(heading_stack)`` reaching the struct, which no pure test sees.

        Hardcoded to zero in ``unwind_state()`` the diagnostic beside it would
        describe the imbalance perfectly and never be handed one — the position
        three fields were already in when this class was written, and the #231
        mutation sweep put this one there too until this test existed.

        **Stranding a heading frame takes more than one dropped close, and the
        count here is measured, not derived.** A frame pops at the first close
        where ``len(element_stack) == owner_depth``; a swallowed close leaves
        one element too many and every later close pops one, so the walk down
        to the root still passes through the owner's depth and the frame pops
        *late* rather than never. Over this fixture, dropping ``</list-item>``,
        ``</list>`` or ``</ack>`` alone strands nothing; ``</p>`` with
        ``</list-item>`` strands it — ``</p>`` being swallowed twice, once in
        the list and once in the ``<notes>`` — and so does the three below.
        The first draft of this docstring gave a general rule ("the residual
        must exceed the owner's depth") that the measurement refutes (PR
        #280's review), so it states the count and not a rule.
        """
        parser_log.expect_errors()
        for tag in ("p", "list-item", "list"):
            _drop_end_tag(monkeypatch, tag)

        JATSParser(_CONTAINER_HEADING_DOCUMENT).parse()

        assert any(
            "container heading(s) still open" in m for m in parser_log.messages(logging.ERROR)
        )

    def test_a_heading_popped_one_container_late_is_reported_by_the_element_stack(
        self, monkeypatch, parser_log
    ):
        """The likelier failure, which ``open_container_headings`` cannot see.

        One dropped ``</ack>`` does not strand the frame: it pops at
        ``</back>`` instead, one container late, so the ``<notes>`` prose opens
        under *Acknowledgements* — a **wrong** heading in a public field, the
        direction this module refuses. The heading field reports nothing,
        having nothing left open; what reports it is ``open_elements``, which
        sees the ``<ack>`` still on the element stack. So the net does catch
        it, at ERROR, attributed to the stack rather than to the heading —
        which is what ``open_container_headings``' docstring says and what PR
        #280's review needed pinned, a first reading having taken the audit to
        be silent here.
        """
        parser_log.expect_errors()
        _drop_end_tag(monkeypatch, "ack")

        handler = JATSParser(_CONTAINER_HEADING_DOCUMENT)._run_parser()

        assert [(s.title, s.paragraphs) for s in handler.body_sections][-1] == (
            "Acknowledgements",
            ["Thanks.", "A later note."],
        )
        errors = parser_log.messages(logging.ERROR)
        assert not [m for m in errors if "container heading(s) still open" in m]
        assert any("element stack not unwound" in m for m in errors)

    def test_a_balanced_container_heading_is_silent(self):
        """The negative control: 4,783 of 8,118 served articles recover one.

        A predicate firing on an ordinary acknowledgement would put the audit
        into the ERROR channel for more than half the corpus.
        """
        handler = _run_handler(_CONTAINER_HEADING_DOCUMENT)

        assert unwind_diagnostics(handler.unwind_state()) == []
        assert [(s.title, s.paragraphs) for s in handler.body_sections] == [
            ("Methods", ["We did the thing."]),
            ("Acknowledgements", ["Thanks."]),
            ("", ["A later note."]),
        ]

    def test_a_balanced_definition_list_is_silent(self):
        """The negative control: 965 of 8,118 served articles carry one.

        A predicate firing on an ordinary definition list would put the audit
        into the ERROR channel for one article in eight, which is what the
        autouse ``parser_log`` fixture makes every other fixture here check
        for free.
        """
        handler = _run_handler(_DEFINITION_LIST_DOCUMENT)

        assert unwind_diagnostics(handler.unwind_state()) == []
        assert handler.definition_terms_dropped == 0

    def test_a_balanced_nested_article_is_silent(self):
        """The negative control: suppression itself must not read as an imbalance.

        ``<sub-article>`` is suppressed on its *opening* tag too, so the push
        and pop of every stack have to stay paired across the suppressed
        region. If they did not, every PLOS peer-review deposit would ERROR.
        The fixture carries one of each member of the set, since the two are
        suppressed by one rule and only ``<sub-article>`` was ever deposited
        here (PR #274's review).
        """
        handler = _run_handler(_NESTED_ARTICLE_DOCUMENT)

        assert unwind_diagnostics(handler.unwind_state()) == []
        assert handler.suppressed_nested_articles == 2

    def test_a_contrib_left_open_is_captured(self, monkeypatch, parser_log):
        """The frame half of the contributor pair.

        A stranded frame is not merely one lost contributor: ``current_author``
        is derived from the top of the stack, so every ``<surname>``,
        ``<collab>`` and ``<string-name>`` read after the imbalance is written
        into the stranded builder rather than into the contributor that
        actually carries it.
        """
        parser_log.expect_errors()
        _drop_end_tag(monkeypatch, "contrib")

        article = JATSParser(_AUDITED_ARTICLE).parse()

        assert any("<contrib> still open" in m for m in parser_log.messages(logging.ERROR))
        # The filter is what keeps the reservation from putting a `None` in a
        # `list[JATSAuthorInfo]`. Deleting it is otherwise a green change: the
        # only document that can produce a hole is this one.
        assert article.authors == []

    def test_an_unfilled_author_slot_is_captured(self, monkeypatch, parser_log):
        """``build_authors()`` drops the hole without a word — the audit must not."""
        parser_log.expect_errors()
        _drop_end_tag(monkeypatch, "contrib")

        JATSParser(_AUDITED_ARTICLE).parse()

        assert any("author slot(s)" in m for m in parser_log.messages(logging.ERROR))

    def test_an_open_non_author_contrib_is_a_frame_and_not_a_slot(self, monkeypatch, parser_log):
        """The two contributor fields are counted separately because they diverge.

        ``_AUDITED_ARTICLE`` carries one ``<contrib>``, so dropping its end tag
        moves both numbers together and either field could be computed from
        the other with the suite still green. A **non-author** ``<contrib>``
        pushes a frame and reserves no slot, which is the shape that tells them
        apart — and the one the docstring's claim rests on.
        """
        parser_log.expect_errors()
        _drop_end_tag(monkeypatch, "contrib")

        JATSParser(_EDITOR_ONLY_ARTICLE).parse()

        errors = parser_log.messages(logging.ERROR)
        assert any("<contrib> still open" in m for m in errors)
        assert not any("author slot(s)" in m for m in errors)

    def test_a_contributor_naming_nobody_is_not_an_unfilled_slot(self, parser_log):
        """The false-positive half, and the reason the reservation is given back.

        A ``<contrib>`` carrying no name at all — ``<anonymous/>``, or one
        holding only an ``<xref>`` — is well-formed JATS that builds no author.
        Left reserved, its slot would make the audit ERROR on a document bmlib
        read exactly right, which is the one thing an ERROR here must never
        mean.
        """
        data = _article_with_front(
            '<contrib-group content-type="author">'
            "<contrib><name><surname>Real</surname></name></contrib>"
            "<contrib><anonymous/></contrib>"
            "</contrib-group>"
        )

        article = JATSParser(data).parse()

        assert [a.full_name for a in article.authors] == ["Real"]
        assert not parser_log.messages(logging.ERROR)

    def test_an_unfilled_figure_slot_is_captured(self, monkeypatch, parser_log):
        """``build_figures()`` drops the hole without a word — the audit must not.

        The existing open-``<fig>`` test leaves a slot unfilled incidentally
        but asserts only on the stack, so ``unfilled_figure_slots`` could be
        hardcoded to 0 with the suite green.
        """
        parser_log.expect_errors()
        _drop_end_tag(monkeypatch, "fig")

        JATSParser(_AUDITED_ARTICLE).parse()

        assert any("figure slot(s)" in m for m in parser_log.messages(logging.ERROR))

    def test_an_unfilled_table_slot_is_captured(self, monkeypatch, parser_log):
        parser_log.expect_errors()
        _drop_end_tag(monkeypatch, "table-wrap")

        JATSParser(_AUDITED_ARTICLE).parse()

        assert any("table slot(s)" in m for m in parser_log.messages(logging.ERROR))

    def test_a_stranded_implicit_body_section_is_reported(self, monkeypatch, parser_log):
        """Unsectioned ``<body>`` prose is single-slot routing state — #134's hole.

        ``implicit_body_section`` holds loose ``<body>`` paragraphs until
        ``</body>`` flushes them. Left stranded the article loses that prose
        outright, and ``has_body`` stays ``True`` because
        ``body_paragraph_count`` already counted it — so the model shows
        nothing wrong either. It was missing from ``_ROUTING_FLAGS``, covered
        only by ``in_body`` being cleared on the adjacent line.
        """
        parser_log.expect_errors()
        monkeypatch.setattr(_JATSHandler, "_flush_implicit_section", lambda self: None)
        data = _article_with_body("<p>Loose prose with no sec.</p>")

        article = JATSParser(data).parse()

        assert article.body_sections == []
        assert any("implicit_body_section" in m for m in parser_log.messages(logging.ERROR))

    def test_a_stranded_implicit_back_section_is_reported(self, monkeypatch, parser_log):
        """The second slot is audited in its own right, not through the first.

        Issue #224 gave ``<back>`` an implicit section too. Sharing one slot
        with ``<body>`` would have made this shape invisible in the other
        direction — see
        :meth:`TestTheBodySlotCannotBeEmptiedByTheBackFlush.test_a_missing_body_flush_is_not_laundered_by_the_back_flush`
        — so each container's slot is stranded, and reported, on its own.
        """
        parser_log.expect_errors()
        monkeypatch.setattr(_JATSHandler, "_flush_implicit_section", lambda self: None)
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Acks</article-title>
  </title-group></article-meta></front>
  <body><sec><title>Methods</title><p>We did the thing.</p></sec></body>
  <back><ack><p>Funded by the Example Foundation.</p></ack></back>
</article>"""

        article = JATSParser(data).parse()

        assert [p for s in article.body_sections for p in s.paragraphs] == ["We did the thing."]
        assert any("implicit_back_section" in m for m in parser_log.messages(logging.ERROR))

    def test_a_stranded_implicit_front_section_is_reported(self, monkeypatch, parser_log):
        """The third slot, audited in its own right (issue #230).

        Front matter is flushed at ``</front>``, and the article's own
        ``<body>`` flushes nothing that could stand in for it — so a front slot
        left stranded is prose lost with nothing else in the parse to say so.
        """
        parser_log.expect_errors()
        monkeypatch.setattr(_JATSHandler, "_flush_implicit_section", lambda self: None)
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta><title-group><article-title>Notes</article-title>
  </title-group>
  <author-notes><fn><p>These authors contributed equally.</p></fn></author-notes>
  </article-meta></front>
  <body><sec><title>Methods</title><p>We did the thing.</p></sec></body>
</article>"""

        article = JATSParser(data).parse()

        assert [p for s in article.body_sections for p in s.paragraphs] == ["We did the thing."]
        assert any("implicit_front_section" in m for m in parser_log.messages(logging.ERROR))


class TestACorrectParseNeverLogsAnError:
    """ERROR means "bmlib is wrong", and the audit's whole design rests on it.

    A predicate that fires on a document bmlib handled correctly makes every
    other ERROR unreadable, which is the failure the audit exists to end one
    level up. The autouse ``parser_log`` fixture is the general guard; these
    are the shapes that were found firing.
    """

    def test_an_article_id_outside_article_meta_is_not_a_defect_claim(self):
        """``current_article_id_type`` was set unconditionally and cleared conditionally.

        An ``<article-id>`` outside ``<article-meta>``/``<front>`` is
        JATS-invalid, but this parser is deliberately lenient about invalid
        markup, and the stray id is correctly ignored — the article parses
        perfectly. Before the clear was dedented, the audit still reported it
        as a stuck routing flag, which is a false accusation twice over: the
        parse was right, and a stale value mis-routes nothing because the next
        ``<article-id>`` open overwrites it.

        The autouse fixture is what fails this test if the ERROR returns; the
        assertions below pin that the parse really was correct, so a future
        "fix" that suppresses the ERROR by breaking the parse fails too.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <article-id pub-id-type="pmc">PMC3333333</article-id>
    <title-group><article-title>Ordinary paper</article-title></title-group>
    <contrib-group content-type="author">
      <contrib><name><surname>Sorensen</surname></name></contrib>
    </contrib-group>
  </article-meta></front>
  <body><sec><title>Intro</title><p>Body prose.</p></sec></body>
  <back><article-id pub-id-type="doi">10.1/stray</article-id></back>
</article>"""

        article = JATSParser(data).parse()

        assert article.pmc_id == "PMC3333333"
        assert article.title == "Ordinary paper"
        assert [s.title for s in article.body_sections] == ["Intro"]
        # The stray id is outside <article-meta>, so it is ignored rather than
        # believed — the point is that ignoring it is also silent.
        assert article.doi == ""


class TestTheZeroAuthorDetectorReadsEverySpelling:
    """ "No ``<surname>``" is not "no contributor" — the quiet branch's claim.

    JATS models a ``<contrib>``'s name as
    ``(name | string-name | collab | anonymous | …)``. When this counter was
    written bmlib extracted only ``<name>``, so counting surnames alone put the
    other two into the DEBUG branch and reported them as *genuinely*
    author-less — a positive claim their evidence never supported, and for
    ``<string-name>`` it meant **every** author of the article.

    Both are extracted now (#120, #140), so neither reaches the detector by
    that route any more. The counter is not narrowed to match: it counts the
    spelling and not the extraction, which is what keeps it able to report the
    *next* contributor bmlib fails to collect — a role it does not read, a
    spelling nobody has filed yet, or a routing regression in the arms that now
    do the collecting.
    """

    def test_a_string_name_contributor_is_extracted_rather_than_reported(self, parser_log):
        """The spelling that lost 100% of an article's authors (#140)."""
        data = _article_with_front(
            '<contrib-group content-type="author">'
            "<contrib><string-name>Jane Q Smith</string-name></contrib>"
            "<contrib><string-name>Ahmed Al-Rashid</string-name></contrib>"
            "</contrib-group>"
        )

        article = JATSParser(data).parse()

        assert [a.full_name for a in article.authors] == ["Jane Q Smith", "Ahmed Al-Rashid"]
        assert not parser_log.messages(logging.WARNING)

    def test_a_collab_only_article_is_extracted_rather_than_reported(self, parser_log):
        """The consortium article (#120), which reached the quiet branch too.

        It got there for a different reason — a ``<collab>`` carries no
        ``<surname>`` at all — and was equally certified author-less.
        """
        data = _article_with_front(
            '<contrib-group content-type="author">'
            "<contrib><collab>The CONSORT Group</collab></contrib>"
            "</contrib-group>"
        )

        article = JATSParser(data).parse()

        assert [a.full_name for a in article.authors] == ["The CONSORT Group"]
        assert not parser_log.messages(logging.WARNING)

    def test_an_uncollected_contributor_still_counts_in_every_spelling(self, parser_log):
        """The counter's remaining job, and why it was not narrowed to ``<name>``.

        Extraction closed the two routes that made this counter urgent, but a
        contributor can still fail to be collected — here because the group
        declares a role bmlib does not read as authorship. All three spellings
        have to keep counting, or the detector goes quiet again for exactly the
        articles whose authors went somewhere unexpected.
        """
        data = _article_with_front(
            '<contrib-group content-type="editor">'
            "<contrib><name><surname>Okafor</surname></name></contrib>"
            "<contrib><string-name>Jane Q Smith</string-name></contrib>"
            "<contrib><collab>The CONSORT Group</collab></contrib>"
            "</contrib-group>"
        )

        article = JATSParser(data).parse()

        assert article.authors == []
        assert any("named 3 contributor(s)" in m for m in parser_log.messages(logging.WARNING))

    def test_the_quiet_branch_reports_its_evidence_not_a_conclusion(self, parser_log):
        """The DEBUG half of "says which kind it is", which was deletable green.

        An operator grepping after a bulk sync has to be able to tell
        "checked, named nobody" from "not checked at all", so the line names
        the article and every spelling it looked for — including
        ``<on-behalf-of>``, which bmlib counts and does not extract.
        """
        data = _article_with_front("<abstract><p>No contributors at all.</p></abstract>")

        article = JATSParser(data).parse()

        assert article.authors == []
        assert not parser_log.messages(logging.WARNING)
        debug = parser_log.messages(logging.DEBUG)
        expected = "named no contributor via <surname>, <string-name>, <collab> or <on-behalf-of>"
        assert any("PMC1234567" in m and expected in m for m in debug)

    def test_a_nested_article_s_contributors_are_not_counted(self, parser_log):
        """A suppressed ``<sub-article>``'s ``<front>`` must not rescue the count.

        The counter is gated on ``in_front``, and the suppression returns
        above the branch that sets it, so nested contributors are excluded for
        free. That is asserted rather than assumed: a reorder there turns
        every peer-review deposit over an author-less article into a spurious
        WARNING, which is the "warning nobody reads" outcome the quiet branch
        exists to protect.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <article-id pub-id-type="pmc">PMC4444444</article-id>
    <title-group><article-title>Author-less correction</article-title></title-group>
  </article-meta></front>
  <body><sec><title>Notice</title><p>Correction prose.</p></sec></body>
  <sub-article article-type="peer-review">
    <front-stub>
      <contrib-group content-type="author">
        <contrib><name><surname>Reviewer</surname></name></contrib>
      </contrib-group>
    </front-stub>
    <body><p>Reviewer prose.</p></body>
  </sub-article>
</article>"""

        article = JATSParser(data).parse()

        assert article.authors == []
        assert not parser_log.messages(logging.WARNING)

    def test_a_reference_contributor_is_not_counted(self, parser_log):
        """``<back>`` is excluded — a bibliography names people who are not contributors.

        Counted document-wide, every author-less article carrying references
        would read as a defect.
        """
        data = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <article-id pub-id-type="pmc">PMC5555555</article-id>
    <title-group><article-title>Author-less notice</article-title></title-group>
  </article-meta></front>
  <body><sec><title>Notice</title><p>Prose.</p></sec></body>
  <back><ref-list><ref><element-citation>
    <person-group><name><surname>Chowdhury</surname></name></person-group>
    <collab>A Cited Consortium</collab>
  </element-citation></ref></ref-list></back>
</article>"""

        article = JATSParser(data).parse()

        assert article.authors == []
        assert not parser_log.messages(logging.WARNING)


class TestARefusedSpanIsBoundedAndReported:
    """Both ends of ``colspan``, and what a refusal actually costs — #129.

    The first cut of #129 bounded the value ``int()`` *refuses* and left the
    value it accepts unbounded, and justified DEBUG on the grounds that a
    dropped span is "a cosmetic defect in one table". Neither held.
    """

    def test_a_refused_span_shifts_every_later_cell_in_its_row(self):
        """Why the report is a WARNING: this is wrong data, not a wrong width.

        ``_build_html_table`` fixes the column count from the *first* row and
        ``_pad_row`` pads short rows at the *end*, so a span rendered as 1
        instead of 2 does not blank a cell — it slides the rest of the row one
        column left. The values below land under the wrong headings, which no
        reader of the rendered table can see.
        """
        data = _table_containing(
            "<table>"
            "<thead><tr><th>Group</th><th>n</th><th>Mean</th><th>SD</th></tr></thead>"
            "<tbody><tr><td colspan='two'>Treatment arm</td><td>42</td><td>7.1</td></tr></tbody>"
            "</table>"
        )

        html = JATSParser(data).to_html()

        body = re.search(r"<tbody>.*?</tbody>", html, re.S).group(0)
        cells = re.findall(r"<td[^>]*>(.*?)</td>", body, re.S)
        # The document says Mean=42, SD=7.1. Under the headings above, this is
        # n=42, Mean=7.1, SD=blank — the corruption the WARNING reports.
        assert cells == ["Treatment arm", "42", "7.1", ""]

    def test_a_refused_span_is_reported_once_per_article_at_warning(self, parser_log):
        """Once per article, not once per cell — a wide table drowned the channel.

        WARNING and not ERROR because a publisher's deposit reaches this one,
        unlike the end-of-parse audit; reporting it at ERROR would spend the
        "an ERROR here means bmlib is wrong" contract the audit depends on.
        """
        row = "".join(f"<td colspan='x{i}'>{i}</td>" for i in range(4))
        data = _table_containing(f"<table><tbody><tr>{row}</tr></tbody></table>")

        JATSParser(data).parse()

        warnings = parser_log.messages(logging.WARNING)
        assert len(warnings) == 1
        assert "4 table cell(s)" in warnings[0]
        assert "one column left" in warnings[0]

    def test_a_span_beyond_the_bound_is_refused_rather_than_materialised(self, parser_log):
        """The half that reintroduced #129, in the shape the fix did not cover.

        ``end_cell`` appends ``colspan - 1`` empty strings, so an accepted
        eight-digit span costs hundreds of megabytes of rendered HTML — which
        ``FullTextService`` then caches — or a ``MemoryError`` out of the SAX
        callback, which the tier chain reports as the article being
        unavailable from that source. That is #129 verbatim, and
        ``MemoryError`` is not a ``_BUG_TYPES`` member, so nothing says so.
        """
        data = _table_containing(
            "<table><tbody><tr><td colspan='20000000'>x</td></tr></tbody></table>"
        )

        html = JATSParser(data).to_html()

        assert len(html) < 100_000
        assert any("exceeds the 1000-column bound" in m for m in parser_log.messages())
        assert any("1 table cell(s)" in m for m in parser_log.messages(logging.WARNING))

    def test_a_span_inside_the_bound_is_still_honoured(self, parser_log):
        """The negative control: the bound must not refuse a real wide table."""
        data = _table_containing("<table><tbody><tr><td colspan='1000'>x</td></tr></tbody></table>")

        html = JATSParser(data).to_html()

        assert html.count("<td") == 1000
        assert not parser_log.messages(logging.WARNING)

    def test_a_well_formed_document_reports_no_span(self, parser_log):
        """No refusal, no line — the channel stays readable."""
        data = _table_containing(
            "<table><tbody><tr><td colspan='2'>12.3</td><td>4.5</td></tr></tbody></table>"
        )

        JATSParser(data).parse()

        assert not [m for m in parser_log.messages(logging.WARNING) if "colspan" in m]


class TestTheDiagnosticNamesTheArticle:
    """An ERROR carrying no identity is unactionable in a bulk sync — #134.

    ``describe_article()`` falls back through the identifiers in the order a
    reader can act on them, then the title, then a fixed string. Only the
    ``pmc_id`` rung was pinned, so collapsing the other four into the generic
    fallback was green — and the title rung matters most, because it is the
    one that fires for a document carrying no identifier at all, which is the
    parse most likely to be broken.
    """

    @staticmethod
    def _describe(front: str) -> str:
        handler = _JATSHandler()
        data = f"""<?xml version="1.0"?>
<article><front><article-meta>{front}</article-meta></front>
<body><sec><title>S</title><p>p</p></sec></body></article>""".encode()
        handler = _run_handler(data)
        return handler.describe_article()

    def test_a_pmc_id_is_preferred(self):
        described = self._describe(
            '<article-id pub-id-type="pmc">PMC9999999</article-id>'
            '<article-id pub-id-type="doi">10.1/x</article-id>'
            "<title-group><article-title>T</article-title></title-group>"
        )

        assert described == "PMC9999999"

    def test_a_doi_is_used_when_there_is_no_pmc_id(self):
        described = self._describe(
            '<article-id pub-id-type="doi">10.1/only-doi</article-id>'
            "<title-group><article-title>T</article-title></title-group>"
        )

        assert described == "10.1/only-doi"

    def test_a_pmid_is_used_when_there_is_neither(self):
        described = self._describe(
            '<article-id pub-id-type="pmid">31234567</article-id>'
            "<title-group><article-title>T</article-title></title-group>"
        )

        assert described == "31234567"

    def test_the_title_is_used_when_no_identifier_was_deposited(self):
        described = self._describe(
            "<title-group><article-title>A paper carrying no id</article-title></title-group>"
        )

        assert described == "'A paper carrying no id'"

    def test_a_document_with_neither_still_names_itself(self):
        """The last rung. A line naming nothing beats no line at all."""
        described = self._describe("")

        assert described == "an article carrying no identifier or title"


#: The three names :meth:`_JATSHandler.endElement` binds to the popped text
#: buffer. The invariant #151 mechanises is written about ``text`` alone;
#: ``normalized_text`` and ``element_text`` are the same buffer one line
#: either side of it, and an arm consuming either directly is the same
#: hazard. So the net is deliberately wider than the prose it enforces, which
#: is the safe direction for a net.
_BUFFER_NAMES = frozenset({"text", "normalized_text", "element_text"})

#: The buffer's *other* spellings, and the net is keyed on all of them
#: because the invariant is about the base buffer being consulted at all —
#: not about three local identifiers. ``self.current_text`` returns
#: ``text_stack[-1]`` directly, and ``endElement``'s own preamble reads
#: ``element_text = self.current_text`` for a non-accumulating element, so
#: the two are the *same value* by construction: an arm reading either is
#: byte-identically the hazard. Keyed on the locals alone, the review of #151
#: found ``elif name in ("institution", "addr-line"):
#: self.collab_address = self.current_text.strip()`` — #142's own arm, in the
#: spelling an implementer reasoning "the ``<collab>`` buffer is already
#: open" would reach for — passing the whole suite green.
_BUFFER_ATTRIBUTES = frozenset({"current_text", "text_stack"})

#: The method that pops the buffer. Calling it a second time in an arm takes
#: and clears ``text_stack[0]`` — the base buffer the invariant is entirely
#: about — so a call is a read like any other.
_BUFFER_METHODS = frozenset({"_pop_text_buffer"})

#: The accumulating set the *synthetic* controls below are judged against —
#: their own, never the parser's. A control asserting that ``<institution>``
#: does not accumulate is asserting something #142 is entitled to change, and
#: judging it against the real ``_TEXT_ACCUMULATING`` would redden seven tests
#: the day it does, each for the opposite of the reason it was written
#: (measured, by adding ``institution`` and ``addr-line`` here and running the
#: class). The real handler is judged against the real set; nothing else is.
_SYNTHETIC_ACCUMULATING = frozenset({"surname", "collab", "source"})


#: Every element whose ``endElement`` arm consumes the text buffer, as the
#: walk currently resolves them. This is the positive control's inventory —
#: the thing that notices a read leaving the method — so it is a measurement
#: of the handler and must be re-measured, never edited to make a test pass.
#: It is read as a floor, so an arm legitimately *added* needs no edit here;
#: only an arm whose read stops being visible does. ``td``/``th`` are that
#: legitimate addition (#245's counter tests the popped buffer for emptiness),
#: and are listed so the inventory stays the *whole* measurement its own
#: docstring claims rather than drifting into a sample of it.
#:
#: **It had drifted anyway, and a floor cannot notice that.** Re-measured
#: 2026-09-13 for issues #241/#248, whose ``<attrib>`` arm reads the buffer:
#: the walk also saw ``disp-formula``, ``inline-formula``, ``tex-math`` (#147)
#: and ``term`` (#228), read by arms added since the inventory was taken and
#: never listed. Twenty-six elements then; re-measured the same day after PR
#: #250's review gave ``<alt-text>`` an arm (a formula's image text is its
#: rendition of last resort), twenty-seven. Re-measured 2026-09-15 after issue
#: #265 gave ``<elocation-id>`` an arm, twenty-eight, that element the only
#: addition. Re-measure, never hand-edit.
_ELEMENTS_WHOSE_ARMS_READ_THE_BUFFER = frozenset(
    {
        "alt-text",
        "article-id",
        "article-title",
        "attrib",
        "collab",
        "disp-formula",
        "elocation-id",
        "inline-formula",
        "fpage",
        "given-names",
        "issue",
        "journal-title",
        "label",
        "lpage",
        "mixed-citation",
        "p",
        "pub-id",
        "source",
        "string-name",
        "surname",
        "td",
        "term",
        "tex-math",
        "th",
        "title",
        "volume",
        "xref",
        "year",
    }
)


@dataclass(frozen=True)
class _BufferRead:
    """One read of the text buffer inside ``endElement``."""

    #: How the buffer was spelled: one of :data:`_BUFFER_NAMES`, or the
    #: ``self.<attr>`` / ``self.<method>()`` form for the other spellings.
    read: str
    line: int
    #: Element names that can reach the read, or ``None`` where nothing above
    #: it constrains ``name`` — which means *every* element, not "none".
    elements: frozenset[str] | None
    #: Guards that do mention ``name`` and that the walker could not read.
    #: Non-empty is a failure in its own right: a walk that quietly skips what
    #: it cannot classify is the vacuous green #151 exists to prevent.
    unreadable_guards: tuple[str, ...]
    #: Is this read part of a statement that *binds* one of the buffer names?
    #: Such a statement is the method's preamble rather than one of its arms,
    #: which is what keeps ``element_text = self._pop_text_buffer(...)`` — a
    #: read reaching every accumulating element — out of the positive
    #: control's inventory of arms that *consume* the buffer.
    binds_a_buffer: bool
    #: Is this read part of a statement that merely *re-shapes* the buffer
    #: rather than consuming it? Strictly stronger than
    #: :attr:`binds_a_buffer`; see :func:`_is_plumbing_statement`.
    is_plumbing: bool
    #: Is there any guard at all above the read? Paired with
    #: :attr:`is_plumbing` this is what identifies the method's preamble: the
    #: statements that bind ``element_text``, ``text`` and ``normalized_text``
    #: stand at the top of the method body under no guard, and are the only
    #: unguarded reads the rule allows.
    guarded: bool


def _elements_admitted(
    test: ast.expr, namespace: Mapping[str, object]
) -> tuple[frozenset[str] | None, tuple[str, ...]]:
    """Which element names does ``test`` admit, and what could not be read?

    Returns ``(elements, unreadable)``. ``elements`` is ``None`` where the
    test places no constraint on ``name`` at all — a guard like
    ``if self.nested_article_depth:`` narrows reachability in some other
    dimension, and treating it as no constraint keeps the reported element
    set *wider* than reality, which can only over-report.
    """
    if isinstance(test, ast.Compare) and _is_the_element_name(test.left):
        if len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq):
            comparator = test.comparators[0]
            if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                return frozenset({comparator.value}), ()
        if len(test.ops) == 1 and isinstance(test.ops[0], ast.In):
            admitted = _string_collection(test.comparators[0], namespace)
            if admitted is not None:
                return admitted, ()
        return None, (ast.unparse(test),)

    if isinstance(test, ast.BoolOp):
        elements: frozenset[str] | None = None
        unreadable: list[str] = []
        unconstrained = False
        for operand in test.values:
            operand_elements, operand_unreadable = _elements_admitted(operand, namespace)
            unreadable.extend(operand_unreadable)
            if isinstance(test.op, ast.And):
                # An operand that does not constrain ``name`` simply does not
                # narrow the set; the rest still do.
                if operand_elements is not None:
                    elements = operand_elements if elements is None else elements & operand_elements
            elif operand_elements is None:
                # ``or`` is only as narrow as its widest branch, so one
                # unconstraining operand makes the whole test unconstraining.
                unconstrained = True
            else:
                elements = operand_elements if elements is None else elements | operand_elements
        # The loop runs to the end even once ``or`` is known to be
        # unconstraining, because an unreadable guard in a *later* operand is
        # a finding in its own right and returning early dropped it: the
        # verdict then fell back to "reachable for every element", which
        # the plumbing exemption can absorb, so the drop turned a fail-closed
        # reading into a green one. Order-dependent silence is exactly what
        # this walk exists not to have.
        return (None if unconstrained else elements), tuple(unreadable)

    if _mentions_the_element_name(test):
        # It talks about ``name`` in a shape this walker does not read — a
        # negated test, a call, a walrus. Reporting it is the point: the walk
        # must demand a decision rather than pass over what it cannot judge.
        return None, (ast.unparse(test),)

    return None, ()


def _is_the_element_name(node: ast.expr) -> bool:
    """Is ``node`` the ``name`` parameter ``endElement`` dispatches on?"""
    return isinstance(node, ast.Name) and node.id == "name"


def _mentions_the_element_name(node: ast.AST) -> bool:
    return any(_is_the_element_name(child) for child in ast.walk(node))


def _string_collection(node: ast.expr, namespace: Mapping[str, object]) -> frozenset[str] | None:
    """Read a literal or module-level collection of element names, or give up."""
    if isinstance(node, ast.Tuple | ast.List | ast.Set):
        if all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in node.elts):
            return frozenset(e.value for e in node.elts)  # type: ignore[attr-defined]
        return None
    if isinstance(node, ast.Name):
        resolved = namespace.get(node.id)
        if isinstance(resolved, frozenset | set | tuple | list) and all(
            isinstance(member, str) for member in resolved
        ):
            return frozenset(resolved)
    return None


def _is_plumbing_statement(node: ast.stmt) -> bool:
    """Does ``node`` merely re-shape the buffer, rather than consume it?

    Two conditions, and the second is what the review of #151 added. The
    statement must **bind** a buffer name — tuple and starred targets are
    unpacked, so rewriting the preamble as ``text, normalized_text =
    element_text.strip(), _normalize_whitespace(...)`` stays plumbing instead
    of being reported as two violations — and it must **hand the buffer to no
    method on the handler**.

    Binding alone was too weak, and in the direction that loses a violation.
    ``text = self._collab_child(name, text)`` wedged above the dispatch chain
    binds a buffer, stands under no guard, and runs for *every* element there
    is — a per-element hook is the natural way to add handling without
    disturbing a forty-branch ``elif`` chain, and it is precisely how #142
    might be written. Passing the buffer to ``self`` is how it gets stashed,
    so a statement that does so is consuming it whatever it also assigns to.
    ``element_text.strip()`` and ``_normalize_whitespace(element_text)`` call
    nothing on ``self`` and stay exempt; ``self._pop_text_buffer(...)`` does,
    but it is guarded by ``_TEXT_ACCUMULATING`` and cleared by containment
    long before this predicate is consulted.
    """
    if not _binds_a_buffer(node):
        return False
    return not any(_is_a_call_on_the_handler(child) for child in ast.walk(node))


def _binds_a_buffer(node: ast.stmt) -> bool:
    """Does ``node`` assign to one of the buffer names?"""
    targets: list[ast.expr] = []
    if isinstance(node, ast.Assign):
        targets = list(node.targets)
    elif isinstance(node, ast.AnnAssign | ast.AugAssign):
        targets = [node.target]
    return any(_binds_a_buffer_name(target) for target in targets)


def _is_a_call_on_the_handler(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and _is_self_attribute(node.func)
    )


def _binds_a_buffer_name(target: ast.expr) -> bool:
    if isinstance(target, ast.Tuple | ast.List):
        return any(_binds_a_buffer_name(element) for element in target.elts)
    if isinstance(target, ast.Starred):
        return _binds_a_buffer_name(target.value)
    return isinstance(target, ast.Name) and target.id in _BUFFER_NAMES


def _buffer_reads_in_end_element(
    source: str, *, namespace: Mapping[str, object]
) -> list[_BufferRead]:
    """Every read of the text buffer in ``endElement``, with what reaches it.

    Raises rather than returning nothing when the method cannot be found: a
    walker that answers "no reads" to a renamed or restructured method is the
    failure mode this whole class exists to rule out.
    """
    module = ast.parse(source)
    handler = next(
        (n for n in module.body if isinstance(n, ast.ClassDef) and n.name == "_JATSHandler"),
        None,
    )
    if handler is None:
        raise AssertionError("no _JATSHandler class in the source handed to the walker")
    method = next(
        (n for n in handler.body if isinstance(n, ast.FunctionDef) and n.name == "endElement"),
        None,
    )
    if method is None:
        raise AssertionError("_JATSHandler has no endElement for the walker to read")

    reads: list[_BufferRead] = []

    def record(
        spelling: str,
        node: ast.AST,
        guards: tuple[tuple[frozenset[str] | None, tuple[str, ...]], ...],
        statement: ast.stmt | None,
    ) -> None:
        elements, unreadable = _combine(guards)
        reads.append(
            _BufferRead(
                read=spelling,
                line=getattr(node, "lineno", 0),
                elements=elements,
                unreadable_guards=unreadable,
                binds_a_buffer=statement is not None and _binds_a_buffer(statement),
                is_plumbing=statement is not None and _is_plumbing_statement(statement),
                guarded=bool(guards),
            )
        )

    def walk(
        node: ast.AST,
        guards: tuple[tuple[frozenset[str] | None, tuple[str, ...]], ...],
        statement: ast.stmt | None,
    ) -> None:
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id in _BUFFER_NAMES:
                record(node.id, node, guards, statement)
            return
        if isinstance(node, ast.Call) and _is_a_buffer_call(node):
            # ``self._pop_text_buffer(...)``. Recorded and then walked into,
            # since its keyword arguments are ordinary expressions that may
            # themselves read a buffer.
            record(f"self.{_attribute_name(node.func)}()", node, guards, statement)
            for child in ast.iter_child_nodes(node):
                walk(child, guards, statement)
            return
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
            if _is_self_attribute(node) and node.attr in _BUFFER_ATTRIBUTES:
                record(f"self.{node.attr}", node, guards, statement)
                return
        if isinstance(node, ast.If):
            # A read in the *body* is guarded by the test. A read in the test
            # itself is credited with the guards above it plus, for an
            # ``and``, the operands to its *left* — which is exactly what
            # short-circuit evaluation guarantees, so it narrows without
            # giving anything up. Crediting the outer guards alone reported
            # ``elif name == "journal-title" and text:`` as reachable for
            # every element: a false accusation carrying the message that a
            # load-bearing invariant is broken, whose only remedy would be to
            # restructure correct code. The ``orelse`` keeps the outer guards
            # rather than gaining ``not test``: an ``elif`` chain lives there,
            # and declining to narrow by negation over-reports in the safe
            # direction.
            walk_test(node.test, guards)
            constraint = _elements_admitted(node.test, namespace)
            for child in node.body:
                walk(child, (*guards, constraint), None)
            for child in node.orelse:
                walk(child, guards, None)
            return
        if isinstance(node, ast.stmt):
            statement = node
        for child in ast.iter_child_nodes(node):
            walk(child, guards, statement)

    def walk_test(
        test: ast.expr,
        guards: tuple[tuple[frozenset[str] | None, tuple[str, ...]], ...],
    ) -> None:
        """Walk a guard expression, narrowing across ``and`` as it goes.

        Only ``and`` narrows: ``or`` reaches its right-hand operand precisely
        when the left one was *false*, so the left constrains nothing there
        and the outer guards are all that may be credited.
        """
        if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
            narrowed = guards
            for operand in test.values:
                walk_test(operand, narrowed)
                narrowed = (*narrowed, _elements_admitted(operand, namespace))
            return
        walk(test, guards, None)

    for child in method.body:
        walk(child, (), None)
    return reads


def _attribute_name(node: ast.expr) -> str:
    return node.attr if isinstance(node, ast.Attribute) else ast.unparse(node)


def _is_self_attribute(node: ast.Attribute) -> bool:
    return isinstance(node.value, ast.Name) and node.value.id == "self"


def _is_a_buffer_call(node: ast.AST) -> bool:
    """Is ``node`` a call to one of :data:`_BUFFER_METHODS` on ``self``?"""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and _is_self_attribute(node.func)
        and node.func.attr in _BUFFER_METHODS
    )


def _combine(
    guards: tuple[tuple[frozenset[str] | None, tuple[str, ...]], ...],
) -> tuple[frozenset[str] | None, tuple[str, ...]]:
    elements: frozenset[str] | None = None
    unreadable: list[str] = []
    for guard_elements, guard_unreadable in guards:
        unreadable.extend(guard_unreadable)
        if guard_elements is not None:
            elements = guard_elements if elements is None else elements & guard_elements
    return elements, tuple(unreadable)


#: The line ``_synthetic_handler`` places the caller's arm on. Named rather
#: than written into each expected message, so adding a line to the preamble
#: is a one-line correction here instead of four opaque off-by-one diffs.
_SYNTHETIC_ARM_LINE = 11


def _synthetic_handler(arm: str) -> str:
    """A minimal ``endElement`` carrying the real one's preamble plus ``arm``.

    The preamble is what makes the controls honest: it holds every shape the
    real walk meets before the dispatch chain — the two statements binding
    ``text`` and ``normalized_text`` under no guard, and both spellings of the
    buffer the preamble itself reads (``self._pop_text_buffer()`` and
    ``self.current_text``). A control that omitted them would exercise a shape
    the real walk never sees.

    The accumulating guard is spelled as a *literal* rather than as the real
    module's ``_TEXT_ACCUMULATING``, because these sources are walked with an
    empty namespace: a name the walker cannot resolve is — correctly — an
    unreadable guard, which would bury every control's own finding under one
    from the preamble. The members are :data:`_SYNTHETIC_ACCUMULATING`'s, so
    the controls stay judged against their own set and never the parser's.

    Keep the preamble ten lines long, or update :data:`_SYNTHETIC_ARM_LINE`.
    """
    accumulating = ", ".join(repr(element) for element in sorted(_SYNTHETIC_ACCUMULATING))
    return (
        "class _JATSHandler:\n"
        "    def endElement(self, name):\n"
        f"        if name in ({accumulating}):\n"
        "            element_text = self._pop_text_buffer()\n"
        "        else:\n"
        "            element_text = self.current_text\n"
        "        text = element_text.strip()\n"
        "        normalized_text = _normalize_whitespace(element_text)\n"
        '        if name == "surname":\n'
        "            self.surname = text\n"
        f"{arm}"
    )


class TestOnlyAnAccumulatingElementReadsTheBuffer:
    """``_inside_mixed_citation``'s prospective half, mechanised — #151.

    That helper keeps its strict-ancestor slice (``element_stack[:-1]``) as
    *prospective* and argues it is harmless with a whole-method claim: no arm
    of :meth:`_JATSHandler.endElement` reads ``text`` for an element outside
    ``_TEXT_ACCUMULATING``, so the base buffer is written and never consulted.

    Nothing tied the two together. The claim is a property of a 500-line
    method asserted in one helper's docstring 300 lines away, and the next
    queued issue is exactly the shape that breaks it — #142 wants a
    ``<collab>``'s ``<institution>``/``<addr-line>`` children read, and
    neither is in ``_TEXT_ACCUMULATING``. Adding such an arm would not fail a
    test; it would quietly make a paragraph false while the code around it
    still relied on the reasoning.

    ``TestTheAuditNetIsComplete`` is the precedent — "a rule enforced by prose
    is not enforced" — and it caught a routing flag shipping missing from the
    net it was supposed to be in.

    **The net is keyed on the buffer, not on three identifiers.** The claim it
    stands for is that the base buffer is never *consulted*, and the buffer
    has five spellings: the three locals in :data:`_BUFFER_NAMES`, plus
    ``self.current_text`` and ``self._pop_text_buffer()``. Keyed on the locals
    alone, the review of #151 wrote #142's own arm in the second spelling and
    passed the whole suite — so which of two synonymous forms the implementer
    reached for decided whether the guard fired. See
    :data:`_BUFFER_ATTRIBUTES`.

    Two things the walk cannot see, stated so a green is not read as more than
    it is. It covers ``endElement``'s **own body**: an arm that delegates to a
    helper takes its read out of reach, which is what
    ``test_the_walk_still_finds_the_arms_it_is_meant_to_be_reading`` exists to
    notice. And it reasons about *reachability*, never about values — a read
    it reports as reachable for an element may still be dead in practice.
    """

    def test_an_arm_reading_a_buffer_it_does_not_own_is_reported(self):
        """The teeth control, in #142's own shape.

        A walk that finds nothing passes, so the walker has to be shown
        failing on the change it exists to catch before its green on the real
        file means anything.
        """
        source = _synthetic_handler(
            '        elif name == "institution":\n            self.x = text\n'
        )

        reads = _buffer_reads_in_end_element(source, namespace={})

        offending = [read for read in reads if read.elements == frozenset({"institution"})]
        assert offending, f"the walker did not see the <institution> arm at all: {reads}"
        assert offending[0].read == "text"
        assert self._findings(offending, _SYNTHETIC_ACCUMULATING) == [
            f"text at line {_SYNTHETIC_ARM_LINE + 1}: "
            "read for an element that does not accumulate: ['institution']"
        ]

    def test_a_guard_the_walker_cannot_read_is_reported_rather_than_skipped(self):
        """The other half of the teeth, and the one that decides vacuity.

        The issue asked for this walk and said in the same breath that getting
        the arm identification wrong "gives a test that passes vacuously". A
        guard mentioning ``name`` in a shape the walker does not read must
        therefore fail the suite and demand a decision, never be passed over
        as though it had been judged harmless.
        """
        source = _synthetic_handler(
            "        elif _decide(name):\n            self.x = normalized_text\n"
        )

        findings = self._findings(
            _buffer_reads_in_end_element(source, namespace={}), _SYNTHETIC_ACCUMULATING
        )

        assert findings == [
            f"normalized_text at line {_SYNTHETIC_ARM_LINE + 1}: "
            "guard the walker cannot read: _decide(name)"
        ]

    def test_an_arm_admitting_a_named_set_of_elements_is_read_through_it(self):
        """#142's likeliest shape, and the walker's one unexercised resolution.

        A guard is as often ``name in _SOME_SET`` as ``name == "x"``. The
        resolution itself is exercised against the real module on every run —
        ``endElement``'s own ``name in _TEXT_ACCUMULATING`` and ``name in
        _NESTED_ARTICLE_ELEMENTS`` both resolve through it — but neither of
        those arms *consumes* a buffer, so what nothing exercised until this
        control is a set-guarded arm that does. Refusing to resolve the set
        would make such an arm *unreadable* rather than a violation, which is
        still a failure, but it would stop the walk saying which elements are
        the problem.
        """
        source = _synthetic_handler(
            "        elif name in _ADDRESS_ELEMENTS:\n            self.x = text\n"
        )

        findings = self._findings(
            _buffer_reads_in_end_element(
                source, namespace={"_ADDRESS_ELEMENTS": frozenset({"institution", "addr-line"})}
            ),
            _SYNTHETIC_ACCUMULATING,
        )

        assert findings == [
            f"text at line {_SYNTHETIC_ARM_LINE + 1}: "
            "read for an element that does not accumulate: ['addr-line', 'institution']"
        ]

    def test_an_and_guard_keeps_the_element_test_its_other_half_does_not_make(self):
        """``and`` narrows, and a branch about something else must not erase it.

        ``endElement``'s own ``name in _NESTED_ARTICLE_ELEMENTS and
        self.nested_article_depth`` is this shape. Dropping the element half
        because the other branch says nothing about ``name`` would leave the
        arm unconstrained, reporting it as reachable for every element — a
        false accusation rather than a miss, but one that would bury the real
        findings under noise the first time such an arm read the buffer.
        """
        source = _synthetic_handler(
            '        elif name == "institution" and self.in_address:\n            self.x = text\n'
        )

        reads = _buffer_reads_in_end_element(source, namespace={})

        in_the_arm = [read for read in reads if read.line == _SYNTHETIC_ARM_LINE + 1]
        assert [read.elements for read in in_the_arm] == [frozenset({"institution"})]

    def test_a_buffer_reached_through_the_handler_is_still_a_read(self):
        """#142's other spelling, and the one the first net missed entirely.

        ``self.current_text`` returns ``text_stack[-1]``, and the preamble
        binds ``element_text`` from it for every non-accumulating element — so
        an arm reading it consults the base buffer just as surely as one
        reading ``text``. Keyed on the three locals alone, this arm passed the
        whole suite green while making ``_inside_mixed_citation``'s slice
        load-bearing: parsing a ``<back>`` whose ``<ref-list>`` is followed by
        an ``<institution>`` put the citation into the institution's text.

        An implementer reasoning "the ``<collab>`` buffer is already open, so
        ``current_text`` has what I want" writes exactly this, which made
        whether #142 was caught turn on which of two synonymous spellings the
        author happened to type.
        """
        source = _synthetic_handler(
            '        elif name == "institution":\n'
            "            self.collab_address = self.current_text.strip()\n"
        )

        findings = self._findings(
            _buffer_reads_in_end_element(source, namespace={}), _SYNTHETIC_ACCUMULATING
        )

        assert findings == [
            f"self.current_text at line {_SYNTHETIC_ARM_LINE + 1}: "
            "read for an element that does not accumulate: ['institution']"
        ]

    def test_popping_the_buffer_a_second_time_is_a_read(self):
        """The third spelling, which takes the base buffer rather than reading it.

        With one buffer on the stack ``_pop_text_buffer()`` returns *and
        clears* ``text_stack[0]``, so an arm calling it is not merely
        consulting the buffer the invariant is about — it is emptying it.
        """
        source = _synthetic_handler(
            '        elif name == "institution":\n'
            "            self.collab_address = self._pop_text_buffer()\n"
        )

        findings = self._findings(
            _buffer_reads_in_end_element(source, namespace={}), _SYNTHETIC_ACCUMULATING
        )

        assert findings == [
            f"self._pop_text_buffer() at line {_SYNTHETIC_ARM_LINE + 1}: "
            "read for an element that does not accumulate: ['institution']"
        ]

    def test_a_per_element_hook_in_the_preamble_is_not_plumbing(self):
        """The exemption's teeth, and why binding alone was too weak.

        A one-line hook sitting outside the dispatch chain is the natural way
        to add per-element handling without disturbing a forty-branch
        ``elif``, and it reads the buffer for *every* element there is.
        Recognised as plumbing merely because it assigns to ``text``, it was
        silently allowed — the one shape inside the method that produced no
        finding at all. :func:`_is_plumbing_statement` now asks what the
        statement *does*: handing the buffer to a method on the handler is how
        it gets stashed, whatever else the statement also assigns to.
        """
        source = _synthetic_handler("        text = self._collab_child(name, text)\n")

        findings = self._findings(
            _buffer_reads_in_end_element(source, namespace={}), _SYNTHETIC_ACCUMULATING
        )

        assert findings == [
            f"text at line {_SYNTHETIC_ARM_LINE}: reachable for every element, and is not plumbing"
        ]

    def test_the_preamble_stays_plumbing_when_it_binds_two_names_at_once(self):
        """The other side of that exemption: an innocent rewrite must stay quiet.

        ``text, normalized_text = element_text.strip(), _normalize_whitespace(
        element_text)`` is the same two plumbing statements written as one.
        Reading only bare ``Name`` targets reported it as two violations —
        a false accusation whose only remedy is to un-refactor correct code.
        """
        source = (
            "class _JATSHandler:\n"
            "    def endElement(self, name):\n"
            "        element_text = self.current_text\n"
            "        text, normalized_text = element_text.strip(), element_text\n"
            '        if name == "surname":\n'
            "            self.surname = text\n"
        )

        findings = self._findings(
            _buffer_reads_in_end_element(source, namespace={}), _SYNTHETIC_ACCUMULATING
        )

        assert findings == []

    def test_an_and_guard_narrows_a_read_in_the_test_itself(self):
        """Short-circuiting guarantees it, so the walk may bank it.

        ``elif name == "journal-title" and text:`` reaches its second operand
        only when the first was true, so the read really is guarded. Crediting
        a test-position read with the outer guards alone reported it as
        reachable for every element — under a message announcing that a
        load-bearing invariant had broken, whose only remedy would have been
        to restructure correct code. The real handler is one line-edit away
        from this shape: ``elif self._owned_by(*_ARTICLE_META) and self.pages
        and text:`` escapes only because its ``name`` test sits on an
        enclosing ``if``.

        ``or`` gets no such treatment, and must not: its right-hand operand is
        reached precisely when the left was *false*.
        """
        source = _synthetic_handler(
            '        elif name == "institution" and text:\n            self.x = text\n'
        )

        reads = _buffer_reads_in_end_element(source, namespace={})

        in_the_test = [read for read in reads if read.line == _SYNTHETIC_ARM_LINE]
        assert [read.elements for read in in_the_test] == [frozenset({"institution"})]
        assert self._findings(reads, _SYNTHETIC_ACCUMULATING) == [
            f"text at line {_SYNTHETIC_ARM_LINE}: "
            "read for an element that does not accumulate: ['institution']",
            f"text at line {_SYNTHETIC_ARM_LINE + 1}: "
            "read for an element that does not accumulate: ['institution']",
        ]

    def test_an_unreadable_guard_in_a_later_or_branch_is_still_reported(self):
        """Order must not decide whether the walk demands a decision.

        ``or`` stops narrowing at its first unconstraining operand, and the
        walk used to *return* there — dropping any unreadable guard to its
        right. The verdict then fell back to "reachable for every element",
        which the plumbing exemption can absorb, so the pair
        ``elif self.in_address or _decide(name): text = self._x(text)`` went
        silent while the same two operands the other way round reported.
        """
        source = _synthetic_handler(
            "        elif self.in_address or _decide(name):\n            self.x = text\n"
        )

        findings = self._findings(
            _buffer_reads_in_end_element(source, namespace={}), _SYNTHETIC_ACCUMULATING
        )

        assert findings == [
            f"text at line {_SYNTHETIC_ARM_LINE + 1}: guard the walker cannot read: _decide(name)"
        ]

    def test_two_element_tests_joined_by_and_are_intersected(self):
        """``and`` narrows, and one constraining operand does not prove it.

        The control above pairs a ``name`` test with a branch about something
        else, so ``elements`` is set from the single constraining operand and
        intersection and union agree. Mutating ``&`` to ``|`` there survived
        the whole class. Two ``name`` tests is the shape that tells them
        apart: an arm reachable only for ``<collab>`` would be credited with
        ``<institution>`` as well, which is a false accusation.
        """
        source = _synthetic_handler(
            '        elif name == "collab" and name in ("collab", "institution"):\n'
            "            self.x = text\n"
        )

        reads = _buffer_reads_in_end_element(source, namespace={})

        in_the_arm = [read for read in reads if read.line == _SYNTHETIC_ARM_LINE + 1]
        assert [read.elements for read in in_the_arm] == [frozenset({"collab"})]
        assert self._findings(in_the_arm, _SYNTHETIC_ACCUMULATING) == []

    def test_a_guarded_rebinding_of_the_buffer_is_not_plumbing(self):
        """The preamble is unguarded, and that half of the test is load-bearing.

        A statement that binds a buffer under a guard saying nothing about
        ``name`` is not the method's plumbing — it is an arm that happens to
        assign to ``normalized_text``, and it reads the buffer for every
        element the guard admits, which is all of them. Dropping the
        ``guarded`` half of the exemption survived every other test here.
        """
        source = _synthetic_handler(
            "        elif self.in_address:\n            normalized_text = text.upper()\n"
        )

        findings = self._findings(
            _buffer_reads_in_end_element(source, namespace={}), _SYNTHETIC_ACCUMULATING
        )

        assert findings == [
            f"text at line {_SYNTHETIC_ARM_LINE + 1}: "
            "reachable for every element, and is not plumbing"
        ]

    def test_an_or_guard_is_only_as_narrow_as_its_widest_branch(self):
        """``or`` widens, and one unconstraining branch removes the guard.

        ``name == "collab" or self.in_address`` reaches the read for *any*
        element the second branch admits, so crediting it with ``{collab}``
        would certify an arm the walker never judged — the under-reporting
        direction. Two name branches do compose into their union. No arm in
        ``endElement`` is spelled either way today, so this control is the
        only thing exercising the rule.
        """
        source = _synthetic_handler(
            '        elif name == "collab" or self.in_address:\n'
            "            self.x = text\n"
            '        elif name == "source" or name == "institution":\n'
            "            self.y = normalized_text\n"
        )

        reads = _buffer_reads_in_end_element(source, namespace={})

        by_line = {read.line: read for read in reads}
        assert by_line[_SYNTHETIC_ARM_LINE + 1].elements is None
        assert by_line[_SYNTHETIC_ARM_LINE + 3].elements == frozenset({"source", "institution"})
        assert self._findings(reads, _SYNTHETIC_ACCUMULATING) == [
            f"text at line {_SYNTHETIC_ARM_LINE + 1}: "
            "reachable for every element, and is not plumbing",
            f"normalized_text at line {_SYNTHETIC_ARM_LINE + 3}: "
            "read for an element that does not accumulate: ['institution']",
        ]

    def test_an_arm_mixing_accumulating_and_other_elements_is_still_reported(self):
        """One admitted element that does not accumulate is enough.

        The likeliest way to break the invariant is not a new arm but an
        element added to an existing one — ``name in ("collab", ...)`` gaining
        ``"institution"``, which is #142 almost exactly. Asking whether the
        arm's elements *overlap* ``_TEXT_ACCUMULATING`` rather than whether
        they are *contained* by it passes such an arm silently, and it is the
        one slip in this class that loses a violation rather than inventing
        one.
        """
        source = _synthetic_handler(
            '        elif name in ("collab", "institution"):\n            self.x = text\n'
        )

        findings = self._findings(
            _buffer_reads_in_end_element(source, namespace={}), _SYNTHETIC_ACCUMULATING
        )

        assert findings == [
            f"text at line {_SYNTHETIC_ARM_LINE + 1}: "
            "read for an element that does not accumulate: ['institution']"
        ]

    def test_nested_guards_narrow_the_element_set_rather_than_widening_it(self):
        """Guards compose by intersection, and the direction is load-bearing.

        Two ``name`` tests above one read admit only what both admit. Widening
        instead — the obvious slip, since every other combination in this
        walker is a union — would report an element the read cannot be reached
        for, which is a false accusation of a defect the module does not have.

        The real handler nests, and in the one arm this whole invariant is
        about: ``elif name in ("mixed-citation", "element-citation"):``
        encloses ``if name == "mixed-citation":``, whose body appends
        ``element_text`` to the reference's citation parts. The walk resolves
        that read to ``{mixed-citation}`` by exactly this intersection.
        Widening would credit it with ``element-citation`` as well — harmless
        only because both happen to accumulate, and this control is what says
        the walk is not leaning on that coincidence.
        """
        source = _synthetic_handler(
            '        elif name == "collab":\n'
            '            if name in ("collab", "institution"):\n'
            "                self.x = text\n"
        )

        reads = _buffer_reads_in_end_element(source, namespace={})

        nested = [read for read in reads if read.line == _SYNTHETIC_ARM_LINE + 2]
        assert [read.elements for read in nested] == [frozenset({"collab"})]
        assert self._findings(nested, _SYNTHETIC_ACCUMULATING) == []

    def test_a_read_no_guard_constrains_is_reported(self):
        """An arm need not be in the chain to break the invariant.

        A ``text``-reading statement added *after* the dispatch chain runs for
        every element there is, so "the walker found no matching arm" cannot
        mean "nothing to report". The preamble's binds are the only unguarded
        reads the rule allows, and they are recognised by what they *do* —
        see :func:`_is_plumbing_statement` — rather than by their line.
        """
        source = _synthetic_handler("        self.trailing = text\n")

        findings = self._findings(
            _buffer_reads_in_end_element(source, namespace={}), _SYNTHETIC_ACCUMULATING
        )

        assert findings == [
            f"text at line {_SYNTHETIC_ARM_LINE}: reachable for every element, and is not plumbing"
        ]

    def test_no_arm_reads_a_buffer_for_an_element_that_does_not_accumulate(self):
        """The invariant itself, over the real handler.

        A failure here is not a broken test: it is the docstring of
        ``_inside_mixed_citation`` having become false. Either the new arm's
        element belongs in ``_TEXT_ACCUMULATING``, or the strict-ancestor
        slice has stopped being prospective and the citation's own buffer now
        escapes into whatever encloses the ``<ref-list>``.
        """
        findings = self._findings(self._reads_in_the_real_handler(), _TEXT_ACCUMULATING)

        assert not findings, (
            "endElement no longer honours _inside_mixed_citation's claim:\n" + "\n".join(findings)
        )

    def test_the_walk_still_finds_the_arms_it_is_meant_to_be_reading(self):
        """The positive control, and the reason a green above means anything.

        ``endElement`` dispatches through one forty-branch ``if``/``elif``
        chain. Move an arm's read out into a helper — ``elif name in
        ("source", "institution"): self._end_scalar_field(name)`` — and the
        walk sees nothing there while the test above stays green, which is
        the shape of a net that has quietly stopped being one. The walk covers
        this method's own body and cannot follow a call out of it, so the
        inventory below is what notices the arm leaving.

        **The whole inventory, not a canary.** It was six names while the walk
        saw nineteen, so thirty-eight of the fifty-three reads the inventory is
        built from could have left without a word — and a *partial* extraction, one arm at a
        time, is a likelier refactor than the wholesale kind.

        Containment and deliberately not equality, because this control's job
        is to notice a read *disappearing*. An arm appearing is the invariant
        test's business one method up: a new consumer of an element that does
        not accumulate is what that test reports, and a new consumer of one
        that does is the legitimate shape of #142, which must stay green
        without anyone editing an inventory to let it through.

        A wholesale restructure fails loudly by a different route and needs no
        help from this test: a ``match`` statement leaves every read
        unconstrained, so each one is reported as reachable for every element.

        Reads inside a statement that *binds* a buffer are excluded, being the
        preamble rather than an arm — otherwise ``element_text =
        self._pop_text_buffer(...)``, guarded by ``_TEXT_ACCUMULATING``, would
        enter the inventory as the whole accumulating set, most of whose
        members no arm consumes.
        """
        reads = self._reads_in_the_real_handler()

        seen = {
            element
            for read in reads
            if read.elements is not None and not read.binds_a_buffer
            for element in read.elements
        }

        # The preamble's own read is guarded by ``_TEXT_ACCUMULATING``, so
        # counting it would make ``seen`` that whole set — and containment
        # would then be satisfied by the preamble alone however many arms had
        # lost their reads. ``<abstract>`` accumulates and no arm consumes it,
        # which makes it the witness that the exclusion is still in force.
        assert "abstract" not in seen, (
            "the inventory has been polluted by a read that only binds the "
            "buffer, so it can no longer notice an arm's read disappearing"
        )

        missing = sorted(_ELEMENTS_WHOSE_ARMS_READ_THE_BUFFER - seen)

        assert not missing, (
            f"the walk no longer sees these buffer-reading arms: {missing} — "
            "either endElement's shape has changed and the walker has not "
            "followed it, or the arm's read has moved out into a helper, "
            "where nothing here can see it"
        )

    def test_a_walker_that_cannot_find_the_method_says_so(self):
        """The linchpin: "no reads" must never be an answer the walk can give.

        A walker that quietly returned ``[]`` for a method it could not locate
        leaves four of these twenty green — measured — and one of the four
        is ``test_no_arm_reads_a_buffer_for_an_element_that_does_not_accumulate``,
        the invariant itself, which asserts that the finding list is empty.
        The other three are this test's sibling, the control asserting the
        preamble stays plumbing, and this one. Rename the method, split the
        class, and this is what fails instead.
        """
        source = _synthetic_handler("").replace("endElement", "end_element")

        with pytest.raises(AssertionError, match="no endElement"):
            _buffer_reads_in_end_element(source, namespace={})

    def test_a_walker_that_cannot_find_the_class_says_so(self):
        """The same linchpin one level out, for a handler that has been split."""
        source = _synthetic_handler("").replace("class _JATSHandler", "class _OtherHandler")

        with pytest.raises(AssertionError, match="no _JATSHandler class"):
            _buffer_reads_in_end_element(source, namespace={})

    @staticmethod
    def _reads_in_the_real_handler() -> list[_BufferRead]:
        source = Path(jats_parser_module.__file__).read_text(encoding="utf-8")
        return _buffer_reads_in_end_element(source, namespace=vars(jats_parser_module))

    @staticmethod
    def _findings(reads: list[_BufferRead], accumulating: frozenset[str]) -> list[str]:
        """Every read that breaks the invariant, or that the walk could not judge.

        ``accumulating`` is passed rather than defaulted so a control cannot
        silently be judged against the parser's own set. The synthetic ones
        must not be: they assert that ``<institution>`` does not accumulate,
        which is exactly what #142 may legitimately change, and seven of them
        would then fail for the opposite of the reason they were written.

        Three verdicts and only one of them is silence. A read the walker
        could not classify is a finding in its own right — that is the whole
        difference between this and a walk that reports what it happens to
        understand.

        The one silence is narrow on purpose: a read reachable for every
        element is passed over only when it *binds* a buffer **and** stands
        under no guard at all, which is the two plumbing statements at the top
        of the method and nothing else. Exempting on the binding alone let an
        arm under a guard that says nothing about ``name`` — or a
        per-element hook folded into the preamble — read the base buffer for
        every element in silence.
        """
        findings = []
        for read in reads:
            where = f"{read.read} at line {read.line}"
            if read.unreadable_guards:
                findings.append(
                    f"{where}: guard the walker cannot read: " + "; ".join(read.unreadable_guards)
                )
            elif read.elements is None:
                if read.guarded or not read.is_plumbing:
                    findings.append(f"{where}: reachable for every element, and is not plumbing")
            elif not read.elements <= accumulating:
                outside = sorted(read.elements - accumulating)
                findings.append(f"{where}: read for an element that does not accumulate: {outside}")
        return findings
