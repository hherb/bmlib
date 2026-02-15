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
        data = _load_fixture("sample_article.xml")
        article = JATSParser(data).parse()
        assert article.doi != ""


class TestJATSParserAbstract:
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
