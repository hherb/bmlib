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

"""Golden-output tests for the four citation-style formatters."""

from __future__ import annotations

from dataclasses import replace

from bmlib.citations.formatter import (
    APAFormatter,
    ChicagoFormatter,
    CitationFormatter,
    HarvardFormatter,
    VancouverFormatter,
)
from bmlib.citations.models import CitationStyle, DocumentMetadata, FormattedReference

METADATA = DocumentMetadata(
    document_id=42,
    title="Title of the article",
    authors=["John Smith", "Anna Johnson", "Brian Williams"],
    journal="Journal Name",
    year=2023,
    volume="45",
    issue="2",
    pages="123-134",
    doi="10.1234/example",
)


class TestVancouver:
    def test_full_reference(self):
        assert VancouverFormatter().format_reference(METADATA, number=1) == (
            "1. Smith J, Johnson A, Williams B. Title of the article. "
            "*Journal Name*. 2023;45(2):123-134. doi:10.1234/example"
        )

    def test_inline_is_the_number(self):
        assert VancouverFormatter().format_inline_citation(METADATA, number=3) == "[3]"

    def test_inline_without_a_number_falls_back_to_the_document_id(self):
        assert VancouverFormatter().format_inline_citation(METADATA) == "[42]"

    def test_seven_authors_truncate_to_et_al(self):
        metadata = replace(METADATA, authors=[f"Given{i} Surname{i}" for i in range(7)])
        reference = VancouverFormatter().format_reference(metadata)
        assert reference.startswith(
            "Surname0 G, Surname1 G, Surname2 G, Surname3 G, Surname4 G, Surname5 G, et al."
        )
        assert "Surname6" not in reference

    def test_inverted_author_names_format_identically(self):
        metadata = replace(METADATA, authors=["Smith, John", "Johnson, Anna"])
        assert VancouverFormatter().format_reference(metadata).startswith("Smith J, Johnson A.")

    def test_pmid_is_the_doi_fallback(self):
        metadata = replace(METADATA, doi=None, pmid="12345678")
        assert VancouverFormatter().format_reference(metadata).endswith("PMID:12345678")

    def test_without_a_journal_the_journal_block_is_absent(self):
        metadata = replace(METADATA, journal=None)
        assert VancouverFormatter().format_reference(metadata, number=1) == (
            "1. Smith J, Johnson A, Williams B. Title of the article. doi:10.1234/example"
        )

    def test_no_authors_reads_unknown(self):
        metadata = replace(METADATA, authors=[])
        assert VancouverFormatter().format_reference(metadata).startswith("Unknown author.")


class TestAPA:
    def test_full_reference(self):
        assert APAFormatter().format_reference(METADATA) == (
            "Smith, J., Johnson, A., & Williams, B. (2023) Title of the article. "
            "*Journal Name*, *45*(2), 123-134. https://doi.org/10.1234/example"
        )

    def test_apa_references_never_double_the_period(self):
        # Regression: upstream appended "." to an author block already ending
        # with an initial's period, so every such reference read "…, J..".
        for authors in (["John Smith"], ["John Smith", "Anna Johnson"]):
            reference = APAFormatter().format_reference(replace(METADATA, authors=authors))
            assert ".." not in reference

    def test_eight_authors_elide_the_middle(self):
        metadata = replace(METADATA, authors=[f"Given{i} Surname{i}" for i in range(8)])
        reference = APAFormatter().format_reference(metadata)
        assert "Surname6" not in reference
        assert "..., & Surname7, G." in reference

    def test_inline_shapes(self):
        formatter = APAFormatter()
        assert formatter.format_inline_citation(METADATA) == "(Smith et al., 2023)"
        two = replace(METADATA, authors=["John Smith", "Anna Johnson"])
        assert formatter.format_inline_citation(two) == "(Smith & Johnson, 2023)"
        one = replace(METADATA, authors=["John Smith"])
        assert formatter.format_inline_citation(one) == "(Smith, 2023)"

    def test_a_missing_year_is_nd(self):
        metadata = replace(METADATA, year=None, authors=["John Smith"])
        assert "(n.d.)" in APAFormatter().format_reference(metadata)
        assert APAFormatter().format_inline_citation(metadata) == "(Smith, n.d.)"


class TestHarvard:
    def test_full_reference(self):
        assert HarvardFormatter().format_reference(METADATA) == (
            "Smith, J., Johnson, A. and Williams, B. (2023) 'Title of the article', "
            "*Journal Name*, 45(2), pp. 123-134. doi: 10.1234/example."
        )

    def test_inline_two_authors_use_and(self):
        two = replace(METADATA, authors=["John Smith", "Anna Johnson"])
        assert HarvardFormatter().format_inline_citation(two) == "(Smith and Johnson, 2023)"

    def test_seven_authors_truncate_to_et_al(self):
        metadata = replace(METADATA, authors=[f"Given{i} Surname{i}" for i in range(7)])
        reference = HarvardFormatter().format_reference(metadata)
        assert " and et al. (2023)" in reference
        assert "Surname6" not in reference


class TestChicago:
    def test_full_reference(self):
        assert ChicagoFormatter().format_reference(METADATA) == (
            'Smith, John, Anna Johnson, and Brian Williams. 2023. "Title of the article." '
            "*Journal Name* 45 (2): 123-134. https://doi.org/10.1234/example."
        )

    def test_only_the_first_author_is_inverted(self):
        two = replace(METADATA, authors=["Smith, John", "Johnson, Anna"])
        assert ChicagoFormatter().format_reference(two).startswith("Smith, John, and Anna Johnson.")

    def test_an_initialed_chicago_author_does_not_double_the_period(self):
        one = replace(METADATA, authors=["Smith, John A."])
        assert ".." not in ChicagoFormatter().format_reference(one).split("2023")[0]

    def test_inline_shapes(self):
        formatter = ChicagoFormatter()
        assert formatter.format_inline_citation(METADATA) == "(Smith et al. 2023)"
        two = replace(METADATA, authors=["John Smith", "Anna Johnson"])
        assert formatter.format_inline_citation(two) == "(Smith and Johnson 2023)"
        one = replace(METADATA, authors=["John Smith"])
        assert formatter.format_inline_citation(one) == "(Smith 2023)"


class TestCitationFormatterFacade:
    def test_the_default_style_is_vancouver(self):
        assert CitationFormatter().style is CitationStyle.VANCOUVER

    def test_the_style_is_switchable(self):
        formatter = CitationFormatter(CitationStyle.VANCOUVER)
        assert formatter.format_inline_citation(METADATA, number=1) == "[1]"
        formatter.style = CitationStyle.APA
        assert formatter.format_inline_citation(METADATA) == "(Smith et al., 2023)"

    def test_reference_list_markdown(self):
        references = [
            FormattedReference(number=1, document_id=5, formatted_text="1. X."),
            FormattedReference(number=2, document_id=6, formatted_text="2. Y."),
        ]
        assert CitationFormatter().format_reference_list(references) == (
            "\n---\n\n## References\n\n1. X.\n\n2. Y.\n"
        )

    def test_every_style_is_available_and_described(self):
        styles = CitationFormatter.get_available_styles()
        assert styles == [
            CitationStyle.VANCOUVER,
            CitationStyle.APA,
            CitationStyle.HARVARD,
            CitationStyle.CHICAGO,
        ]
        for style in styles:
            assert CitationFormatter.get_style_description(style)
