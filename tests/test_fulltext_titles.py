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

"""Tests for ``bmlib.fulltext._titles`` — issue #56.

A junk metadata title has one property every shape of it shares, whether or
not anyone sampled that shape: it is not printed in the document. So the rule
is not "does this look like junk" but "does the document itself say this",
and what these tests pin is the normalisation that decides when two spellings
of a title are the same string, plus the two cases where the question cannot
be asked at all.

The measured half — which junk shapes survive corroboration, and at what rate
— lives in ``tests/test_pdf_metadata_titles.py`` against the committed
corpus. These are the unit cases.
"""

from __future__ import annotations

from bmlib.fulltext._titles import accepted_metadata_title, looks_like_junk, normalise

_PAGE = "Effects of aspirin on outcomes\nJane Smith, John Doe\nAbstract\nWe studied 400 adults."


class TestTheNormaliserAbsorbsTypesettingNotMeaning:
    def test_case_and_terminal_period_do_not_separate_a_title_from_itself(self) -> None:
        assert normalise("Effects of Aspirin.") == normalise("effects of aspirin")

    def test_an_en_dash_and_a_hyphen_normalise_alike(self) -> None:
        assert normalise("dose–response") == normalise("dose-response")

    def test_diacritics_fold(self) -> None:
        assert normalise("thérapie") == normalise("therapie")

    def test_a_ligature_is_decomposed(self) -> None:
        """PyMuPDF usually decodes these, but not always, and a title that
        differs from its printed form by one glyph is still the same title."""
        assert normalise("eﬃcacy") == normalise("efficacy")

    def test_a_title_wrapped_across_lines_joins_on_a_space(self) -> None:
        assert normalise("Randomised\ncontrolled trial") == "randomised controlled trial"

    def test_a_hyphen_at_a_line_break_is_closed_up_not_spaced(self) -> None:
        """Typesetting hyphenation. Joining on a space instead leaves
        ``con trolled``, the metadata title is then not contained in the page,
        and a perfectly good title is rejected with nothing explaining it."""
        assert normalise("Randomised con-\ntrolled trial") == "randomised controlled trial"

    def test_a_real_hyphenated_word_keeps_its_two_tokens(self) -> None:
        """The line-break rule must not reach an ordinary hyphen: closing up
        ``dose-response`` mid-line would make it a token the page never has.
        """
        assert normalise("dose-response study") == "dose response study"

    def test_punctuation_and_runs_of_space_collapse(self) -> None:
        assert normalise("  Aspirin:   a  review!  ") == "aspirin a review"

    def test_text_with_no_word_characters_normalises_to_nothing(self) -> None:
        assert normalise("### —— ###") == ""


class TestAMetadataTitleIsBelievedOnlyWhereTheDocumentSaysIt:
    def test_a_title_printed_on_page_one_is_accepted(self) -> None:
        """The negative control. Without it, a rule that rejects everything
        passes every other test in this class."""
        title = "Effects of aspirin on outcomes"
        assert accepted_metadata_title({"title": title}, _PAGE) == title

    def test_a_title_the_page_never_mentions_is_rejected(self) -> None:
        assert accepted_metadata_title({"title": "Microsoft Word - ms.docx"}, _PAGE) is None

    def test_the_title_is_returned_as_given_not_as_normalised(self) -> None:
        """The normalised form is the comparison, not the value: a caller
        wants the title the document carries, capitals and punctuation
        included."""
        assert (
            accepted_metadata_title({"title": "  Effects of Aspirin on Outcomes.  "}, _PAGE)
            == "Effects of Aspirin on Outcomes."
        )

    def test_a_title_typeset_across_two_lines_is_still_corroborated(self) -> None:
        page = "Effects of aspirin\non outcomes\nJane Smith"
        title = "Effects of aspirin on outcomes"
        assert accepted_metadata_title({"title": title}, page) == title

    def test_a_blank_title_is_rejected(self) -> None:
        assert accepted_metadata_title({"title": "   "}, _PAGE) is None

    def test_a_missing_title_key_is_rejected(self) -> None:
        assert accepted_metadata_title({}, _PAGE) is None

    def test_a_title_with_no_word_characters_is_rejected(self) -> None:
        """``normalise`` reduces it to ``""``, and ``""`` is contained in
        every page — so without this guard the emptiest possible title would
        be corroborated by any document at all."""
        assert accepted_metadata_title({"title": "### ###"}, _PAGE) is None

    def test_a_page_with_no_text_accepts_the_metadata_title(self) -> None:
        """An image-only scan makes corroboration a test that *cannot be run*,
        not one that failed — the same distinction the samplers draw between
        an unmeasured probe and a failed one. Rejecting here would blank the
        title of every scanned paper, whose metadata is the only title signal
        that exists."""
        title = "Effects of aspirin on outcomes"
        assert accepted_metadata_title({"title": title}, "") == title
        assert accepted_metadata_title({"title": title}, "   \n \n ") == title


class TestTheOneBackstopMemberTheCorpusEarned:
    """A title of fewer than three words is not an article title.

    Measured over 181 real PDFs: it rejects one junk title corroboration
    accepted — "Nepal Journ", a journal name truncated mid-word in a running
    header, which page 1 really does print — and rejects no row whose metadata
    title matched its record. The shortest genuine title measured is five
    words.
    """

    def test_the_measured_survivor_is_rejected(self) -> None:
        """The row that earned the member its place: junk that page 1 prints,
        so corroboration alone accepts it."""
        page = "Nepal Journal of Medical Sciences\nEffects of aspirin on outcomes\nJane Smith"
        assert accepted_metadata_title({"title": "Nepal Journ"}, page) is None

    def test_a_bare_job_number_is_junk(self) -> None:
        assert looks_like_junk("52561798", {}) is True

    def test_a_two_word_title_is_junk(self) -> None:
        assert looks_like_junk("Layout 1", {}) is True

    def test_a_three_word_title_is_not(self) -> None:
        """The threshold sits below every genuine title in the corpus, whose
        shortest is five words — not tuned up against the junk."""
        assert looks_like_junk("Aspirin in stroke", {}) is False

    def test_a_real_title_is_not_junk(self) -> None:
        """The negative control on the backstop itself."""
        assert looks_like_junk("Effects of aspirin on outcomes", {}) is False

    def test_the_backstop_is_consulted_before_the_page(self, monkeypatch) -> None:
        import bmlib.fulltext._titles as titles_module

        monkeypatch.setattr(titles_module, "looks_like_junk", lambda title, metadata: True)
        # Both a page that prints the title and a page with no text at all:
        # neither may rescue a title the backstop has rejected.
        metadata = {"title": "Effects of aspirin"}
        assert accepted_metadata_title(metadata, "Effects of aspirin") is None
        assert accepted_metadata_title(metadata, "") is None
