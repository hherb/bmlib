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
        """The outcome, whichever guard delivers it. Today that is the
        backstop — zero words is fewer than three — not the dedicated
        ``if not wanted``; the test below is the one that isolates it."""
        assert accepted_metadata_title({"title": "### ###"}, _PAGE) is None

    def test_the_empty_normalisation_guard_stands_on_its_own(self, monkeypatch) -> None:
        """The one case only the ``if not wanted`` guard covers.

        It is masked twice: the backstop rejects a zero-word title first, and
        anchoring would reject it afterwards anyway — two spaces cannot occur
        in a page joined by single ones. Neither helps when the *page* is also
        empty: that takes the unrunnable-check path, which hands the title
        straight back, so an image-only scan whose ``/Title`` is "###" would
        be titled "###".

        The backstop is neutralised here so the guard is exercised alone;
        without that it sits at 0% coverage, reading exactly like the dead
        code a tidying pass deletes.
        """
        import bmlib.fulltext._titles as titles_module

        monkeypatch.setattr(titles_module, "looks_like_junk", lambda title, metadata: False)
        assert accepted_metadata_title({"title": "### ###"}, "") is None
        assert accepted_metadata_title({"title": "—•—"}, "   \n  ") is None

    def test_a_punctuation_only_title_is_rejected_on_an_empty_page(self) -> None:
        """The same case with nothing neutralised — the behaviour a caller
        actually gets, whichever guard delivers it."""
        assert accepted_metadata_title({"title": "### ###"}, "") is None

    def test_a_page_with_no_text_accepts_the_metadata_title(self) -> None:
        """An image-only scan makes corroboration a test that *cannot be run*,
        not one that failed — the same distinction the samplers draw between
        an unmeasured probe and a failed one. Rejecting here would blank the
        title of every scanned paper, whose metadata is the only title signal
        that exists."""
        title = "Effects of aspirin on outcomes"
        assert accepted_metadata_title({"title": title}, "") == title
        assert accepted_metadata_title({"title": title}, "   \n \n ") == title


class TestCorroborationIsAnchoredToWholeTokens:
    """Containment compares token sequences, not raw substrings.

    ``normalise`` exists to reduce both sides to tokens; an unanchored
    ``in`` test throws those boundaries away and matches inside a word. Every
    case below is a false *accept* — the direction issue #56 exists to
    prevent — and each one passed before the padding went in.
    """

    def test_a_title_starting_inside_a_page_word_is_rejected(self) -> None:
        """No page token is "art"; the match ran inside "heart"."""
        page = "A heart in medicine and other essays\nJane Smith"
        assert accepted_metadata_title({"title": "art in medicine"}, page) is None

    def test_a_title_ending_inside_a_page_word_is_rejected(self) -> None:
        page = "On the study of foobar\nJane Smith"
        assert accepted_metadata_title({"title": "the study of foo"}, page) is None

    def test_a_metadata_title_truncated_mid_word_is_rejected(self) -> None:
        """The shape that matters. Producers truncate ``/Title`` routinely —
        Word's first-line heuristic, typesetter job tickets capped at 32 or 64
        characters — and an unanchored test returned the fragment verbatim
        *and* with full confidence, so it beat the font-size fallback that
        would have recovered the complete line off the page.

        The corpus holds no such row, so this class is unmeasured rather than
        shown safe; that is why it is pinned here.
        """
        page = "Effects of aspirin on cardiovascular outcomes\nJane Smith"
        cut = "Effects of aspirin on cardiovascul"
        assert accepted_metadata_title({"title": cut}, page) is None

    def test_a_whole_token_prefix_of_the_page_title_is_still_accepted(self) -> None:
        """The negative control: anchoring must reject only *partial tokens*.
        A title that is a whole-token prefix of what page 1 prints — a
        subtitle dropped from the metadata — is corroborated exactly as
        before, and this test fails if the padding is written as an equality
        or a startswith."""
        page = "Effects of aspirin on outcomes: a randomised trial\nJane Smith"
        title = "Effects of aspirin on outcomes"
        assert accepted_metadata_title({"title": title}, page) == title

    def test_a_title_at_the_very_start_and_end_of_the_page_is_accepted(self) -> None:
        """The padding must not cost the boundary cases it brackets: a page
        whose entire text *is* the title has no space on either side of it."""
        title = "Effects of aspirin on outcomes"
        assert accepted_metadata_title({"title": title}, title) == title


class TestTheOneBackstopMember:
    """A title of fewer than three words is not an article title.

    Measured over the 235 real PDFs in ``tests/data/pdf_metadata_titles.json``:
    it rejects no row whose metadata title matched its record, and the shortest
    genuine title measured is five words.

    It is kept as **defence-in-depth, not as a member the corpus still earns**.
    The one row that admitted it — "Nepal Journ" — is now rejected by anchored
    containment on its own, so the threshold rescues nothing corroboration does
    not already reject. What it still covers is a short but *complete* junk
    string page 1 prints, which anchoring cannot catch; the corpus shows no
    such row, so that cover is argued rather than measured. See the constant's
    own comment in ``bmlib/fulltext/_titles.py``.
    """

    def test_the_row_that_admitted_the_member_is_rejected(self) -> None:
        """The row the threshold was earned on. It is now over-determined —
        anchored containment rejects "Nepal Journ" too, since "journ" is not
        the whole token "journal" — so this pins the outcome, not the member.
        ``test_the_backstop_alone_rejects_a_short_printed_title`` below is the
        one that isolates the member itself.
        """
        page = "Nepal Journal of Medical Sciences\nEffects of aspirin on outcomes\nJane Smith"
        assert accepted_metadata_title({"title": "Nepal Journ"}, page) is None

    def test_the_backstop_alone_rejects_a_short_printed_title(self) -> None:
        """The cover anchoring does *not* provide, and the only case that
        isolates this member: a short junk string page 1 prints in full, as
        whole tokens. Corroboration accepts it — the page really does say
        "Layout 1" — so the threshold is the only thing rejecting it.

        Delete ``_MIN_TITLE_WORDS`` and this is the test that fails.
        """
        page = "Layout 1\nEffects of aspirin on outcomes\nJane Smith"
        assert accepted_metadata_title({"title": "Layout 1"}, page) is None

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


class TestALineNumberedManuscriptStillCorroborates:
    """Preprint servers number the lines of a submitted manuscript, and
    PyMuPDF reports each number as its own line — *between* the lines of the
    title it sits beside.

    Measured over 130 matched rows, this was the only wrongly rejected title,
    and it is a whole class of document rather than one file: every
    line-numbered preprint whose title wraps.
    """

    def test_a_title_split_by_line_numbers_is_accepted(self) -> None:
        page = (
            "Coordinated leaf hydraulic thresholds maintain virtually null\n"
            "1\n"
            "stomatal safety margins in poplar despite genetic variation\n"
            "2\n"
            "and nutrient-induced phenotypic plasticity\n"
            "3\n"
            "Authors: D Chassagnaud, L Bezon\n"
        )
        title = (
            "Coordinated leaf hydraulic thresholds maintain virtually null stomatal "
            "safety margins in poplar despite genetic variation and nutrient-induced "
            "phenotypic plasticity"
        )
        assert accepted_metadata_title({"title": title}, page) == title

    def test_a_hyphenated_word_split_across_a_numbered_break_still_closes_up(self) -> None:
        """The two rules have to compose: the line number is removed first,
        and the hyphen then meets the break it was always adjacent to."""
        page = "Randomised con-\n7\ntrolled trial\n"
        assert accepted_metadata_title({"title": "Randomised controlled trial"}, page) == (
            "Randomised controlled trial"
        )

    def test_a_number_inside_a_line_is_not_stripped(self) -> None:
        """Only a line that is *nothing but* a number goes. "Phase 3 trial of
        BNT162b2" must keep every token it has."""
        page = "Phase 3 trial of BNT162b2\nJane Smith\n"
        assert accepted_metadata_title({"title": "Phase 3 trial of BNT162b2"}, page) == (
            "Phase 3 trial of BNT162b2"
        )

    def test_a_page_of_only_line_numbers_still_reads_as_having_no_text(self) -> None:
        """Stripping must not turn a page into an empty one that then accepts
        an uncorroborated title by the image-only-scan rule... which is
        exactly what it does, and is correct: a page whose entire extractable
        content is line numbers carries no title to check against either."""
        assert accepted_metadata_title({"title": "Effects of aspirin"}, "1\n2\n3\n") == (
            "Effects of aspirin"
        )
