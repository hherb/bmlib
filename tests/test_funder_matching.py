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

"""Tests for industry-funder name matching (issues #36 and #112).

``industry_funding_detected`` feeds a HIGH-risk rule, and HIGH applies a
quality-tier downgrade, so a false positive costs more than a false negative.
The corpus test at the bottom is what keeps that honest: it measures the
matcher against real CrossRef and PubMed names sampled by
``scripts/sample_funder_names.py`` and hand-labelled.

Issue #112 added the second half of that. The matcher's comments state a
measurement as the reason for each token, and those measurements are what the
next edit gets checked against — but nothing checked *them*, and six were
wrong. ``TestTheStatedCountsAreWhatTheCorpusHolds`` parses the rows out of
``analyzer.py`` and re-derives every one, so the justification is now as
answerable to the corpus as the behaviour is.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from bmlib.transparency import analyzer
from bmlib.transparency.analyzer import _is_industry_funder

CORPUS_PATH = Path(__file__).parent / "data" / "funder_names.json"

# The floors #36 established, one notch below the measured reading so an
# unrelated refactor does not have to move them, but close enough that a real
# regression trips. The reading itself is deliberately *not* restated here —
# it lives in ``TestTheStatedCountsAreWhatTheCorpusHolds`` as an exact
# ``(tp, fp, fn)``, because all four figures that used to sit on this line
# were wrong (#112). ``0.917 / 0.324`` for this matcher and ``0.400 / 0.176``
# for the one it replaced are self-consistent with each other and with a
# corpus holding 34 industry names; the committed corpus holds 30, and reads
# ``0.909 / 0.333`` and ``0.357 / 0.167``.
MIN_PRECISION = 0.90
MIN_RECALL = 0.30

# ``_INDUSTRY_KEYWORDS`` as it stood at ``be456a2^``, the commit issue #36
# replaced it in. Kept as the list rather than as two float constants so that
# "it must beat what it replaced" is re-derived from the corpus every run: the
# constants it replaces could not be, and were stale from the day they landed.
PRE_36_KEYWORDS = (
    "pharma",
    "biotech",
    "therapeutics",
    "inc.",
    "corp.",
    "ltd.",
    "gmbh",
    "laboratories",
)


class TestOrgSuffixWordBoundaries:
    """Org suffixes match as words, with or without their trailing dot.

    The old list tested substrings, so ``"inc."`` had to keep its dot to avoid
    matching ``"Lincoln"`` — and therefore missed the NLM-normalised
    ``"Pfizer Inc"``.
    """

    def test_inc_with_a_dot(self):
        assert _is_industry_funder("Genentech Inc.")

    def test_inc_without_a_dot(self):
        # The defect in #36: always true of CrossRef names, and the PubMed
        # <Grant><Agency> corpus drops the punctuation more often still.
        assert _is_industry_funder("Pfizer Inc")

    def test_inc_uppercased(self):
        assert _is_industry_funder("PFIZER INC")

    def test_corp_without_a_dot(self):
        assert _is_industry_funder("Amgen Corp")

    def test_ltd_without_a_dot(self):
        assert _is_industry_funder("Takeda Ltd")

    def test_gmbh(self):
        assert _is_industry_funder("Boehringer Ingelheim GmbH")

    def test_lincoln_is_not_a_company(self):
        # What the trailing dot existed to prevent.
        assert not _is_industry_funder("Lincoln University")

    def test_vincent_is_not_a_company(self):
        assert not _is_industry_funder("St Vincent's Hospital")

    def test_province_is_not_a_company(self):
        assert not _is_industry_funder("Science and Technology Department of Hunan Province")

    def test_provincial_is_not_a_company(self):
        assert not _is_industry_funder("Provincial Health Services Authority")


class TestStemsStillMatchInsideWords:
    """The stems exist to match *inside* longer words — that must survive.

    Applying ``\\b…\\b`` uniformly, as #36's one-line framing suggests, would
    trade a punctuation false negative for a much larger stem false negative.
    This is the test that pins why the list is split in two.
    """

    def test_pharmaceutical(self):
        assert _is_industry_funder("Janssen Pharmaceutica NV")

    def test_pharmaceuticals(self):
        assert _is_industry_funder("Vertex Pharmaceuticals")

    def test_therapeutics(self):
        assert _is_industry_funder("Alnylam Therapeutics")

    def test_bare_pharma(self):
        # Retained as the safe residue of the old "pharma" substring: no corpus
        # evidence either way, but a standalone "Pharma" in a funder name is a
        # company, and this cannot match more than the substring it replaced.
        assert _is_industry_funder("Novartis Pharma AG")


class TestCorpusDrivenNarrowings:
    """Cases the labelled corpus decided, against the intuitive answer.

    Each of these looks like a regression until you read the numbers. They are
    pinned so a later session does not "fix" them back.
    """

    def test_pharmacy_is_not_industry(self):
        # "pharma" as a substring scored 3 TP / 5 FP. The five are "Pharmacy"
        # three times, "Pharmacogenetics", and a "Pharmaceutical Workers" name
        # — *not* the "Pharmacology" this comment used to claim, which appears
        # nowhere in the corpus, and not "all academic" either, since the one
        # "pharmaceutic" inherits is neither (#112). Narrowing kept every true
        # positive and dropped four of the five.
        assert not _is_industry_funder("Faculty of Pharmacy of the University of Montreal")

    def test_pharmacogenetics_is_not_industry(self):
        assert not _is_industry_funder("Pharmacogenetics and Medicines Optimisation Network")

    def test_biotechnology_alone_is_not_industry(self):
        # The surprising one. As a substring "biotech" scored 0 TP / 4 FP in the
        # corpus: its only hits were "Department of Biotechnology" (an Indian
        # ministry) and "Biotechnology and Biological Sciences Research
        # Council" (a UK research council). "Biotechnology" names a *field*, so
        # public bodies use it freely — it is not evidence of a commercial
        # funder. Matching it cost four false positives and bought nothing.
        assert not _is_industry_funder("Department of Biotechnology, Ministry of Science")
        assert not _is_industry_funder("Biotechnology and Biological Sciences Research Council")

    def test_bare_biotech_is_still_industry(self):
        # The safe residue: as a whole word it is a company name.
        assert _is_industry_funder("Acme Biotech")

    def test_a_singular_key_laboratory_is_not_industry(self):
        # "laboratories" is deliberately the plural only. The singular "Key
        # Laboratory" is a Chinese state-lab form, appearing twice in the
        # committed corpus and neither time commercial. The "8 times" this
        # comment used to claim is not re-derivable from the repo (#112).
        assert not _is_industry_funder("Guangdong Provincial Key Laboratory of Oral Diseases")

    def test_plural_laboratories_is_industry(self):
        # 1 TP / 0 FP, so it earned its place — though note the residual risk
        # the corpus happened not to contain: "Sandia National Laboratories".
        assert _is_industry_funder("Dr. Reddy's Laboratories, Hyderabad, India")

    def test_co_is_not_an_org_token(self):
        # 4 TP / **0** FP — rejected all the same. The corpus holds no
        # collision at all, so the false positive this comment used to record
        # was never measured (#112); the string below is synthetic, and the
        # risk it stands for is real but invisible to this corpus. Nor do the
        # true positives all carry "Ltd": "Merck & Co.; Merck Sharp & Dohme"
        # carries no other token, and is a false negative today because of it.
        assert not _is_industry_funder("project co-sponsored by province and ministry")
        assert not _is_industry_funder("Merck & Co.; Merck Sharp & Dohme")

    def test_corporation_is_not_an_org_token(self):
        # 1 TP / 1 FP — rejected. US non-profits use "Corporation" too, so it
        # cannot separate them. Costs "Invitae Corporation".
        assert not _is_industry_funder("Research Corporation for Science Advancement")

    def test_spelled_out_suffixes_earned_inclusion(self):
        # 1 TP / 0 FP each. "Inc" and "Ltd" do not reach these: `\binc\b`
        # needs a boundary after "inc", and "Incorporated" continues with "o".
        assert _is_industry_funder("Treatment Technologies and Insights, Incorporated")
        assert _is_industry_funder("ImmVira Co., Limited")

    def test_llc_earned_inclusion(self):
        # 2 TP / 0 FP.
        assert _is_industry_funder("Cardinal Health, LLC")
        assert _is_industry_funder("NanOlogy, LLC")


class TestReservedSuffixesAreKeptWithoutCorpusEvidence:
    """Rule 2 of the membership rules, applied to every token that satisfies it.

    Issue #112: ``plc`` and ``pty`` were excluded for scoring 0 TP while
    ``corp`` and ``gmbh`` were kept on exactly that score, so the stated rule
    ("0 TP, nothing earned") was not the rule being applied. The rule actually
    applied is that a legally reserved incorporation suffix is evidence in
    itself — no public body may use the form — and once written down it admits
    these two as well.
    """

    def test_plc_is_industry(self):
        # The form UK-listed pharma reports under, and the one the Swift port
        # already kept for this reason.
        assert _is_industry_funder("GSK plc")

    def test_pty_is_industry(self):
        assert _is_industry_funder("Sirtex Medical Pty")

    def test_neither_appears_in_the_corpus_at_all(self):
        """Why the asymmetry could sit unnoticed: admitting them moves nothing.

        Not a restatement of the two rows in the comment — this is the reason
        a measured rule could not have decided them either way, so the tokens
        had to be decided by the rule and not by the corpus.
        """
        for token in ("plc", "pty"):
            assert TestTheStatedCountsAreWhatTheCorpusHolds._score_token(token, "word") == (0, 0)


class TestNonIndustryNamesAreNotFlagged:
    def test_a_government_agency(self):
        assert not _is_industry_funder("National Institutes of Health")

    def test_a_research_council(self):
        assert not _is_industry_funder("Deutsche Forschungsgemeinschaft")

    def test_a_charity(self):
        assert not _is_industry_funder("American Heart Association")

    def test_an_empty_name(self):
        assert not _is_industry_funder("")


class TestAgainstTheLabelledCorpus:
    """The measurement that justifies every choice above.

    Runs offline against the committed fixture — the sampling that produced it
    is a live runner (``scripts/sample_funder_names.py``) kept out of the
    suite, per the repo's no-network-in-tests rule.
    """

    @staticmethod
    def _scored() -> tuple[int, int, int]:
        """Return ``(tp, fp, fn)`` over the non-ambiguous corpus entries."""
        corpus = json.loads(CORPUS_PATH.read_text())
        tp = fp = fn = 0
        for entry in corpus["entries"]:
            if entry["label"] == "ambiguous":
                # Kept in the file with a reason, excluded from the numbers —
                # scoring an undecidable name would only add noise.
                continue
            is_industry = entry["label"] == "industry"
            flagged = _is_industry_funder(entry["name"])
            if flagged and is_industry:
                tp += 1
            elif flagged:
                fp += 1
            elif is_industry:
                fn += 1
        return tp, fp, fn

    def test_the_corpus_is_present_and_labelled(self):
        corpus = json.loads(CORPUS_PATH.read_text())
        labels = {entry["label"] for entry in corpus["entries"]}
        assert labels <= {"industry", "not_industry", "ambiguous"}
        assert sum(1 for e in corpus["entries"] if e["label"] == "industry") >= 25
        assert all(e["source"] in {"crossref", "pubmed", "both"} for e in corpus["entries"])
        # Every ambiguous entry carries its reason rather than being dropped.
        assert all(e.get("reason") for e in corpus["entries"] if e["label"] == "ambiguous")

    def test_precision_meets_the_floor(self):
        tp, fp, _ = self._scored()
        precision = tp / (tp + fp)
        assert precision >= MIN_PRECISION, f"precision fell to {precision:.3f}"

    def test_recall_meets_the_floor(self):
        tp, _, fn = self._scored()
        recall = tp / (tp + fn)
        assert recall >= MIN_RECALL, f"recall fell to {recall:.3f}"

    @staticmethod
    def _scored_with_the_pre_36_matcher() -> tuple[int, int, int]:
        """Return ``(tp, fp, fn)`` for the substring matcher #36 replaced.

        Scored over the same entries by the same rule, so the comparison below
        is one measurement against another rather than against a constant
        somebody wrote down once.
        """
        corpus = json.loads(CORPUS_PATH.read_text())
        tp = fp = fn = 0
        for entry in corpus["entries"]:
            if entry["label"] == "ambiguous":
                continue
            is_industry = entry["label"] == "industry"
            flagged = any(keyword in entry["name"].lower() for keyword in PRE_36_KEYWORDS)
            if flagged and is_industry:
                tp += 1
            elif flagged:
                fp += 1
            elif is_industry:
                fn += 1
        return tp, fp, fn

    def test_it_beats_the_matcher_it_replaced(self):
        # The ship rule from the design: gain recall without losing precision.
        # Both sides are now derived, so a redrawn corpus moves them together
        # instead of leaving the older half stale — which is exactly what the
        # two constants this replaced had done (#112).
        old_tp, old_fp, old_fn = self._scored_with_the_pre_36_matcher()
        tp, fp, fn = self._scored()
        assert tp / (tp + fp) > old_tp / (old_tp + old_fp)
        assert tp / (tp + fn) > old_tp / (old_tp + old_fn)

    @pytest.mark.parametrize(
        "name",
        [
            "Pfizer",
            "Roche",
            "AbbVie",
            "Bristol Myers Squibb",
            "Teva",
        ],
    )
    def test_the_recall_ceiling_is_bare_brand_names(self, name):
        """Documents *why* recall is 0.32 and not higher.

        Most missed industry funders are bare brand names carrying no legal
        suffix and no field word. No keyword list can reach them — it would
        take a company-name gazetteer, which is a different feature with its
        own false-positive profile. Pinned so the low recall figure is read as
        a known ceiling rather than an unnoticed defect.
        """
        assert not _is_industry_funder(name)


class TestTheStatedCountsAreWhatTheCorpusHolds:
    """Every count the matcher's comments cite has to re-derive from the corpus.

    Issue #112. The comments in ``bmlib/transparency/analyzer.py`` state a
    measurement as the reason for each token's inclusion and exclusion, and
    those measurements are what the next edit will be checked against — but
    nothing checked them. Six were wrong, and not by drift: the corpus has one
    commit and the matcher was byte-identical, so they were taken against a
    revision that was never committed. They were internally coherent, which is
    why they survived — ``0.917 = 11/12`` and ``0.324 = 11/34`` describe one
    corpus holding 34 industry names where the committed one holds 30.

    So the counts are written in a canonical row and this class re-derives
    every one of them. A comment cannot compute; the next best thing is a test
    that fails when the corpus moves under it, and that names the row to fix.

    The precedent is ``test_jats_exhibit_sampler.py``'s
    ``TestTheCitedPopulationsAreWhatTheCorporaHold``, one step further on: it
    hard-codes the cited figure in the test, so the *comment* can still drift
    from it. Here the comment itself is the input.
    """

    SOURCE = Path(analyzer.__file__)
    MANUAL = Path(__file__).resolve().parents[1] / "docs" / "manual" / "transparency.md"

    #: ``#   "token"   kind   N TP / M FP   — reason``. The reason wraps onto
    #: continuation lines, which simply do not match.
    CLAIM_RE = re.compile(
        r'^#\s+"(?P<token>[a-z]+)"\s+(?P<kind>stem|word)\s+'
        r"(?P<tp>\d+) TP / (?P<fp>\d+) FP\b"
    )

    # Every token the block is expected to account for, as a floor rather than
    # an equality: a later row may be added without editing this list, but a
    # row silently vanishing — taking its claim out of the check with it — is
    # what this catches. Sized to the whole inventory, not to a canary
    # sample: #151's positive control was six names against nineteen arms, so
    # thirty-eight of its fifty-three reads could have left without a word.
    EXPECTED_CLAIMS = frozenset(
        {
            ("pharmaceutic", "stem"),
            ("therapeutics", "stem"),
            ("laboratories", "stem"),
            ("pharma", "stem"),
            ("biotech", "stem"),
            ("pharma", "word"),
            ("biotech", "word"),
            ("incorporated", "word"),
            ("inc", "word"),
            ("corp", "word"),
            ("limited", "word"),
            ("ltd", "word"),
            ("gmbh", "word"),
            ("llc", "word"),
            ("plc", "word"),
            ("pty", "word"),
            ("co", "word"),
            ("corporation", "word"),
            ("ag", "word"),
            ("bv", "word"),
            ("nv", "word"),
            ("sa", "word"),
            ("ab", "word"),
            ("labs", "word"),
        }
    )

    @classmethod
    def _claims(cls) -> dict[tuple[str, str], tuple[int, int]]:
        """Return ``{(token, kind): (tp, fp)}`` as the module's comments state it.

        Fails closed in every direction. An unreadable source, a table that
        has moved or been reformatted out of recognition, and a token claimed
        twice for one kind all raise rather than returning a smaller dict —
        "I found nothing" must never be an answer this can give, or one
        reformat turns the whole class green at once.
        """
        text = cls.SOURCE.read_text(encoding="utf-8")
        claims: dict[tuple[str, str], tuple[int, int]] = {}
        for line in text.splitlines():
            match = cls.CLAIM_RE.match(line)
            if match is None:
                continue
            key = (match["token"], match["kind"])
            if key in claims:
                raise AssertionError(f"{key} is claimed twice; one of the two is unchecked")
            claims[key] = (int(match["tp"]), int(match["fp"]))
        if not claims:
            raise AssertionError(
                f"no count rows found in {cls.SOURCE.name} — the table has moved or "
                "been reformatted, so nothing below is checking anything"
            )
        return claims

    @staticmethod
    def _entries() -> list[dict[str, str]]:
        """The non-ambiguous corpus entries, the population every count is over."""
        corpus = json.loads(CORPUS_PATH.read_text())
        return [entry for entry in corpus["entries"] if entry["label"] != "ambiguous"]

    @staticmethod
    def _matches(token: str, kind: str, name: str) -> bool:
        """Report whether one token hits one name, the way its kind is matched.

        Mirrors the module's own two rules — a stem is a lowercased substring,
        a word is ``\\b…\\b`` case-insensitively — deliberately rather than
        by importing ``_INDUSTRY_WORD_RE``, which is one union over the whole
        tuple and so cannot answer for a single token.
        ``test_the_scorer_agrees_with_the_matcher_it_mirrors`` is what stops
        this second copy drifting from the first.
        """
        if kind == "stem":
            return token in name.lower()
        return re.search(r"\b(?:" + token + r")\b", name, re.IGNORECASE) is not None

    @classmethod
    def _score_token(cls, token: str, kind: str) -> tuple[int, int]:
        """Return ``(tp, fp)`` for one token over the non-ambiguous corpus."""
        tp = fp = 0
        for entry in cls._entries():
            if cls._matches(token, kind, entry["name"]):
                if entry["label"] == "industry":
                    tp += 1
                else:
                    fp += 1
        return tp, fp

    def test_the_table_accounts_for_every_token_in_use(self):
        """A token cannot enter either tuple without bringing its counts.

        The direction that matters: an undocumented token is one whose
        justification was never measured, which is how ``plc``/``pty`` came to
        be excluded on a rule four kept tokens do not satisfy either.
        """
        claims = self._claims()
        missing = {(stem, "stem") for stem in analyzer._INDUSTRY_STEMS} - set(claims)
        missing |= {(word, "word") for word in analyzer._INDUSTRY_WORDS} - set(claims)
        assert not missing, f"tokens in use with no stated counts: {sorted(missing)}"

    def test_the_table_still_holds_every_claim_it_was_built_from(self):
        """The positive control: a row may be added, but none may quietly go."""
        assert self.EXPECTED_CLAIMS <= set(self._claims())

    @pytest.mark.parametrize("key", sorted(EXPECTED_CLAIMS))
    def test_a_stated_count_reproduces(self, key):
        token, kind = key
        claims = self._claims()
        assert claims[key] == self._score_token(token, kind), (
            f'the row for "{token}" as a {kind} disagrees with the corpus'
        )

    def test_no_row_at_all_disagrees_with_the_corpus(self):
        """Including a row for a token ``EXPECTED_CLAIMS`` has never heard of.

        The parametrised test above names the row it fails on, which is what a
        reader wants, but it can only check rows it was told about — so a row
        added with an invented count would sit in the table unread, which is
        the very shape #112 is about. A token cannot enter the table on a
        number nobody measured any more than it can enter the tuple without
        one.
        """
        wrong = {
            key: (stated, self._score_token(*key))
            for key, stated in self._claims().items()
            if stated != self._score_token(*key)
        }
        assert not wrong, f"rows disagreeing with the corpus, stated vs measured: {wrong}"

    def test_the_scorer_agrees_with_the_matcher_it_mirrors(self):
        """The instrument's own control: per-token scoring must be the matcher.

        Without this the class could reproduce every row of a table that
        describes some other predicate — the mirror is hand-written, so it is
        exactly the kind of second copy that drifts.
        """
        in_use = [(stem, "stem") for stem in analyzer._INDUSTRY_STEMS]
        in_use += [(word, "word") for word in analyzer._INDUSTRY_WORDS]
        for entry in self._entries():
            name = entry["name"]
            by_parts = any(self._matches(token, kind, name) for token, kind in in_use)
            assert by_parts == _is_industry_funder(name), name

    def test_the_headline_figures_reproduce(self):
        """The four numbers that were wrong, derived rather than restated.

        ``MIN_PRECISION``/``MIN_RECALL`` above are floors and stay floors —
        this is the exact reading they are one notch below, and the only place
        in the repo that states it.
        """
        assert TestAgainstTheLabelledCorpus._scored() == (10, 1, 20)

    def test_the_replaced_matchers_reading_reproduces(self):
        """The other half of the headline: 0.357 / 0.167, not 0.400 / 0.176.

        Both readings are cited in the ``MIN_PRECISION`` comment as the record
        of what was wrong, so both are pinned — a figure kept as a cautionary
        tale goes stale exactly as the figure it warns about did.
        """
        assert TestAgainstTheLabelledCorpus._scored_with_the_pre_36_matcher() == (5, 9, 25)

    #: ``| Substring (before) | 0.400 | 0.176 |``, bold markers optional.
    MANUAL_ROW_RE = re.compile(
        r"^\|\s*(?P<matcher>Substring \(before\)|Split \(now\))\s*\|"
        r"\s*\**(?P<precision>[01]\.\d+)\**\s*\|\s*\**(?P<recall>[01]\.\d+)\**\s*\|"
    )

    def test_the_manuals_headline_table_reproduces(self):
        """The same two figures a third time — and this is the copy read.

        Issue #112 names ``analyzer.py``, but every one of its stale figures
        had been copied into ``docs/manual/transparency.md`` as well, and that
        is the copy a downstream consults. The guard written on one branch is
        the guard the others need.
        """
        rows: dict[str, tuple[float, float]] = {}
        for line in self.MANUAL.read_text(encoding="utf-8").splitlines():
            match = self.MANUAL_ROW_RE.match(line)
            if match is not None:
                rows[match["matcher"]] = (float(match["precision"]), float(match["recall"]))
        assert set(rows) == {"Substring (before)", "Split (now)"}, (
            f"the manual's headline table has moved or been renamed: found {sorted(rows)}"
        )

        old_tp, old_fp, old_fn = TestAgainstTheLabelledCorpus._scored_with_the_pre_36_matcher()
        tp, fp, fn = TestAgainstTheLabelledCorpus._scored()
        assert rows["Substring (before)"] == (
            round(old_tp / (old_tp + old_fp), 3),
            round(old_tp / (old_tp + old_fn), 3),
        )
        assert rows["Split (now)"] == (round(tp / (tp + fp), 3), round(tp / (tp + fn), 3))
