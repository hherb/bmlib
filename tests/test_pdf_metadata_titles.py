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

"""The measured half of issue #56, against ``tests/data/pdf_metadata_titles.json``.

That corpus is real PDFs from Europe PMC and bioRxiv, collected by
``scripts/sample_pdf_metadata_titles.py`` and labelled against the title each
source's own record states — ``match``, ``truncated``, ``unrelated`` or
``absent``. What is asserted here is the ship rule the design fixed **before**
the corpus was collected:

1. corroboration wrongly rejects at most **1%** of ``match`` rows;
2. it rejects at least **80%** of ``unrelated`` rows.

Floors, not the observed values: a test written to whatever a run happened to
produce pins an accident rather than a requirement.

Offline — the corpus is committed, so no test here touches the network. Re-run
the sampler when the rule or the reject-list changes; that is what keeps the
numbers answerable to the records rather than to memory.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from bmlib.fulltext._titles import accepted_metadata_title
from bmlib.fulltext.models import TextBlock
from bmlib.fulltext.segmenter import SectionSegmenter

_FIXTURE = Path(__file__).resolve().parent / "data" / "pdf_metadata_titles.json"

# `wilson` comes from the sampler's own helpers rather than being restated
# here. Both ship rules below are *threshold* rules, and `_sampling.wilson`'s
# docstring is the argument for why a threshold needs an interval: a point
# estimate over 126 rows cannot settle a 1% ceiling. Computed rather than
# written down, so a re-sampled corpus moves the reported bound with it
# instead of leaving a stale number in a docstring.
_HELPERS = Path(__file__).resolve().parent.parent / "scripts" / "_sampling.py"
_spec = importlib.util.spec_from_file_location("bmlib_sampling_helpers_metrics", _HELPERS)
if _spec is None or _spec.loader is None:  # pragma: no cover - the module is in-tree
    raise ImportError(f"cannot load the sampling helpers from {_HELPERS}")
_helpers = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _helpers
_spec.loader.exec_module(_helpers)
wilson = _helpers.wilson


def _rows() -> list[dict[str, Any]]:
    """Every row of the committed corpus.

    No ``skipif`` guard on a missing file: the corpus is committed, and a
    metric test that skips itself when its evidence goes missing is the
    failure this repo keeps having to design against — a guard that cannot
    fail reads exactly like a guard that passes. If the fixture is gone, that
    is a broken checkout and every test here should say so loudly.
    """
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _bucket(name: str) -> list[dict[str, Any]]:
    return [row for row in _rows() if row["bucket"] == name]


def _metadata(row: dict[str, Any]) -> dict[str, Any]:
    """The metadata dict the converter would have produced for this PDF."""
    return {
        "title": row["metadata_title"],
        "creator": row["creator"],
        "producer": row["producer"],
        "file_path": row["file_name"],
    }


def _page_one_text(row: dict[str, Any]) -> str:
    return "\n".join(line["text"] for line in row["page_one_lines"])


def _accepted(row: dict[str, Any]) -> str | None:
    return accepted_metadata_title(_metadata(row), _page_one_text(row))


def _blocks_of(row: dict[str, Any]) -> list[TextBlock]:
    """Rebuild page 1's lines, in the order ``extract_blocks`` returned them."""
    return [
        TextBlock(
            text=line["text"],
            page_num=0,
            font_size=line["size"],
            font_name="",
            is_bold=line["bold"],
            is_italic=False,
            x=0.0,
            y=line["y"],
            width=0.0,
            height=12.0,
        )
        for line in row["page_one_lines"]
    ]


def _is_truncated(row: dict[str, Any]) -> bool:
    """Whether the fixture holds fewer of page 1's lines than the PDF had.

    A rejected row that was truncated is **inconclusive offline**: the rule
    saw less of page 1 here than it would in production, so its rejection may
    be an artefact of the cap rather than a property of the rule.
    """
    return row["page_one_line_count"] > len(row["page_one_lines"])


class TestTheRuleMeetsTheFloorsItShippedOn:
    def test_a_good_title_is_almost_never_rejected(self) -> None:
        """Ship rule 1, the cost side: ≤1% of ``match`` rows wrongly rejected.

        Rows whose stored page 1 was truncated **and** whose title the rule
        then failed to find are counted separately rather than as failures:
        the rule sees the whole page in production and only 40 lines here, so
        such a row is a question this fixture cannot answer. They are pinned
        as a minority below, so the exclusion cannot quietly grow to swallow
        the measurement.
        """
        rows = _bucket("match")
        rejected = [row for row in rows if _accepted(row) is None]
        wrong = [row for row in rejected if not _is_truncated(row)]
        conclusive = [row for row in rows if not (_is_truncated(row) and _accepted(row) is None)]
        assert conclusive, "no conclusive match rows to measure"
        rate = len(wrong) / len(conclusive)
        _lo, hi = wilson(len(wrong), len(conclusive))
        assert rate <= 0.01, (
            f"{len(wrong)}/{len(conclusive)} = {rate:.1%} good titles rejected "
            f"(95% CI upper bound {hi:.1%}): {[row['id'] for row in wrong]}"
        )

    def test_what_the_corpus_actually_settles_about_rule_one(self) -> None:
        """Rule 1 is a *threshold* rule, and the corpus does not settle it at
        the threshold it names.

        0 wrong rejections in 126 conclusive rows is a point estimate of 0%,
        but its 95% upper bound is about 3% — roughly triple the "ceiling 1%"
        printed beside it. A reader takes the corpus to have established ≤1%
        when it has established ≤3%.

        This is stated rather than enforced: the ship rule is what it is, and
        moving a threshold is a decision, not a test edit. What the test
        prevents is the interval quietly getting *worse* — a shrunken or
        thinned corpus would widen it, and the assertion catches that even
        though the point estimate would still read 0%.
        """
        rows = _bucket("match")
        conclusive = [row for row in rows if not (_is_truncated(row) and _accepted(row) is None)]
        wrong = [row for row in conclusive if _accepted(row) is None]
        _lo, hi = wilson(len(wrong), len(conclusive))
        assert hi <= 0.03, (
            f"the corpus now bounds the wrong-rejection rate at {hi:.1%}, not ~3% — "
            f"{len(wrong)}/{len(conclusive)} conclusive rows"
        )

    def test_the_inconclusive_rows_are_a_minority(self) -> None:
        """The control on the exclusion above. The sampler's own threshold —
        a population more than a fifth unmeasured is not a population — is the
        same rule applied to the same kind of gap."""
        rows = _bucket("match")
        inconclusive = [row for row in rows if _is_truncated(row) and _accepted(row) is None]
        assert len(inconclusive) / len(rows) <= 0.20

    def test_most_junk_is_rejected(self) -> None:
        """Ship rule 2, the benefit side: ≥80% of ``unrelated`` rows rejected.

        Truncation cuts the other way here — a junk title printed beyond the
        stored lines would be rejected offline and accepted in production — so
        the accepted ones are listed on failure, and the truncated share of
        this bucket is reported by the test below.
        """
        rows = _bucket("unrelated")
        rejected = [row for row in rows if _accepted(row) is None]
        rate = len(rejected) / len(rows)
        lo, _hi = wilson(len(rejected), len(rows))
        assert rate >= 0.80, (
            f"only {len(rejected)}/{len(rows)} = {rate:.1%} junk titles rejected "
            f"(95% CI lower bound {lo:.1%}); "
            f"accepted: {[row['metadata_title'] for row in rows if _accepted(row)]}"
        )

    def test_rule_two_is_settled_at_its_threshold(self) -> None:
        """Unlike rule 1, this one the corpus does settle: the 95% lower bound
        on 34 of 35 junk rejected clears the 80% floor, so the floor holds at
        confidence and not merely on the point estimate."""
        rows = _bucket("unrelated")
        rejected = [row for row in rows if _accepted(row) is None]
        lo, _hi = wilson(len(rejected), len(rows))
        assert lo >= 0.80, f"the floor no longer holds at 95%: lower bound {lo:.1%}"

    def test_the_corpus_still_holds_both_populations(self) -> None:
        """The control on the two rules. A fixture thinned to three rows a
        bucket would satisfy both while measuring nothing, and one that lost a
        bucket entirely would divide by zero rather than fail informatively.
        """
        assert len(_bucket("match")) >= 50
        assert len(_bucket("unrelated")) >= 5

    def test_both_sources_are_represented(self) -> None:
        """The two populations answer different questions — publisher-typeset
        PDFs measure wrong rejections, author-submitted ones carry the junk —
        so a corpus from one source alone cannot settle the rule."""
        sources = {row["source"] for row in _rows()}
        assert len(sources) >= 2, sources


class TestTheSegmenterDeliversWhatTheRuleDecides:
    """Both tests below are ``for`` + ``continue``, so each needs a control on
    its own loop having run.

    Confirmed by mutation: with the rule stubbed to reject everything the
    first test passed over **zero** rows, and with it stubbed to accept
    everything the second did — each reporting success for having checked
    nothing. The sibling floor tests would catch either extreme, but these two
    are the only place the segmenter↔rule wiring is checked across real rows,
    and a test that cannot fail is not the guard it appears to be.
    """

    def test_an_accepted_title_is_the_title_the_segmenter_returns(self) -> None:
        """The wiring rather than the rule: whatever ``accepted_metadata_title``
        returns for a row, ``_extract_title`` must return it too rather than
        some other candidate off the page."""
        checked = 0
        for row in _rows():
            accepted = _accepted(row)
            if accepted is None:
                continue
            title = SectionSegmenter()._extract_title(
                _blocks_of(row), _metadata(row), row["median_font_size"]
            )
            assert title == accepted, row["id"]
            checked += 1
        assert checked >= 50, f"only {checked} accepted rows to check the wiring against"

    def test_a_rejection_never_leaves_the_junk_as_the_title(self) -> None:
        """The point of the fix. Whatever the fallback produces for a rejected
        row — a large-font line, or nothing — it must not be the junk."""
        checked = 0
        for row in _bucket("unrelated"):
            if _accepted(row) is not None:
                continue
            title = SectionSegmenter()._extract_title(
                _blocks_of(row), _metadata(row), row["median_font_size"]
            )
            assert title != row["metadata_title"], row["id"]
            checked += 1
        assert checked >= 25, f"only {checked} rejected junk rows to check"
