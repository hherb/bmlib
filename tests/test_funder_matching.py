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

"""Tests for industry-funder name matching (issue #36).

``industry_funding_detected`` feeds a HIGH-risk rule, and HIGH applies a
quality-tier downgrade, so a false positive costs more than a false negative.
The corpus test at the bottom is what keeps that honest: it measures the
matcher against real CrossRef and PubMed names sampled by
``scripts/sample_funder_names.py`` and hand-labelled.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bmlib.transparency.analyzer import _is_industry_funder

CORPUS_PATH = Path(__file__).parent / "data" / "funder_names.json"

# The floors this PR established, one notch below the measured figures so an
# unrelated refactor does not have to move them, but close enough that a real
# regression trips. Measured on 2026-07-30 against the committed corpus:
# precision 0.917, recall 0.324. The substring matcher this replaced scored
# precision 0.400, recall 0.176 on the same names.
MIN_PRECISION = 0.90
MIN_RECALL = 0.30

# What the previous matcher scored, kept as the thing the new one must beat.
PREVIOUS_PRECISION = 0.400
PREVIOUS_RECALL = 0.176


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
        # "pharma" as a substring scored 3 TP / 5 FP: it reached "Pharmacy",
        # "Pharmacology" and "Pharmacogenetics", all academic. Narrowing to
        # "pharmaceutic" kept every true positive and dropped four of the five.
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
        # Laboratory" is a Chinese state-lab form that appeared 8 times in the
        # corpus, none of them commercial.
        assert not _is_industry_funder("Guangdong Provincial Key Laboratory of Oral Diseases")

    def test_plural_laboratories_is_industry(self):
        # 1 TP / 0 FP, so it earned its place — though note the residual risk
        # the corpus happened not to contain: "Sandia National Laboratories".
        assert _is_industry_funder("Dr. Reddy's Laboratories, Hyderabad, India")

    def test_co_is_not_an_org_token(self):
        # 4 TP / 1 FP — rejected. It collides with the English prefix. The
        # true positives it would have caught all carry "Ltd" as well.
        assert not _is_industry_funder("project co-sponsored by province and ministry")

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

    def test_it_beats_the_matcher_it_replaced(self):
        # The ship rule from the design: gain recall without losing precision.
        # Stated as a test so the comparison is re-runnable rather than a claim
        # in a commit message.
        tp, fp, fn = self._scored()
        assert tp / (tp + fp) > PREVIOUS_PRECISION
        assert tp / (tp + fn) > PREVIOUS_RECALL

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
