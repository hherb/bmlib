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

"""Tests for ``scripts/sample_databank_names.py``.

The script itself is a live runner — it measures PubMed — but the reading it
prints is a maintainer's evidence for changing ``_TRIAL_REGISTRY_NAMES`` or
``_DEPOSITION_DATABANK_LEVELS``, so the table has to be trustworthy offline. What is
pinned here is the property that makes it so: **a request that failed never
prints as a finding.** A zero count and an ``unclassified`` are what a set
member gone dead and a vocabulary drift look like, and NCBI returning nothing
must not be readable as either.

No network: every test drives the script through a stubbed ``_get``, in the
mocked-HTTP pattern the rest of the suite uses.
"""

from __future__ import annotations

import importlib.util
import sys
import urllib.error
from collections import Counter
from pathlib import Path

import pytest

from bmlib.transparency.analyzer import _DEPOSITION_DATABANK_LEVELS, _TRIAL_REGISTRY_NAMES

# `scripts/` is not a package — it holds runnable tools, not importable modules
# — so the module is loaded by path rather than imported. Executing it is safe:
# everything below `if __name__ == "__main__"` stays unrun.
_SAMPLER_PATH = Path(__file__).resolve().parent.parent / "scripts" / "sample_databank_names.py"
_spec = importlib.util.spec_from_file_location("bmlib_databank_sampler", _SAMPLER_PATH)
if _spec is None or _spec.loader is None:  # pragma: no cover - the script is in-tree
    raise ImportError(f"cannot load the databank sampler from {_SAMPLER_PATH}")
sampler = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sampler)


# ---- Payload builders ----


def _esearch(count: int, ids: tuple[str, ...] = ()) -> str:
    """An esearch response reporting *count* records and *ids* as its sample."""
    id_list = "".join(f"<Id>{i}</Id>" for i in ids)
    return f"<eSearchResult><Count>{count}</Count><IdList>{id_list}</IdList></eSearchResult>"


def _efetch(*names: str) -> str:
    """An efetch response for one record listing *names* as its databanks."""
    banks = "".join(f"<DataBank><DataBankName>{n}</DataBankName></DataBank>" for n in names)
    return (
        "<PubmedArticleSet><PubmedArticle><MedlineCitation><Article>"
        f"<DataBankList>{banks}</DataBankList>"
        "</Article></MedlineCitation></PubmedArticle></PubmedArticleSet>"
    )


class _FakeEUtils:
    """Stands in for ``_get``: answers esearch per candidate name, efetch once.

    A name with no entry in *esearch* answers "no records", which keeps the
    candidates a test does not care about from cluttering its assertions. A
    value of ``None`` is a failed request, which is what most of these tests
    are about.
    """

    def __init__(self, esearch: dict[str, str | None], efetch: str | None = None) -> None:
        self.esearch = esearch
        self.efetch = efetch

    def __call__(self, url: str, params: dict[str, str]) -> str | None:
        if url == sampler.ESEARCH:
            for name, body in self.esearch.items():
                if params["term"] == f'"{name}"[si]':
                    return body
            return _esearch(0)
        return self.efetch


def _run(monkeypatch, capsys, names: tuple[str, ...], fake: _FakeEUtils) -> str:
    """Run ``main()`` over *names* against *fake*, returning what it printed."""
    monkeypatch.setattr(sampler, "NLM_DATABANK_NAMES", names)
    monkeypatch.setattr(sampler, "_get", fake)
    monkeypatch.setattr(sys, "argv", ["sample_databank_names.py", "--email", "t@example.org"])
    assert sampler.main() == 0
    return capsys.readouterr().out


def _row(out: str, name: str) -> str:
    """Return the printed row for *name*; the candidate is the first column."""
    for line in out.splitlines():
        if line[:34].strip() == name:
            return line
    raise AssertionError(f"no row for {name!r} in:\n{out}")


class TestGet:
    """The one place a network failure becomes a value the rest of the script reads."""

    def test_a_body_is_decoded(self, monkeypatch):
        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return b"<eSearchResult/>"

        monkeypatch.setattr(sampler.time, "sleep", lambda _: None)
        monkeypatch.setattr(sampler.urllib.request, "urlopen", lambda url, timeout: _Response())
        assert sampler._get(sampler.ESEARCH, {"term": "x"}) == "<eSearchResult/>"

    def test_a_transport_failure_is_reported_as_none(self, monkeypatch):
        # Swallowed here so one unreachable name does not abort a 40-name run —
        # which is exactly why every caller has to distinguish None downstream.
        def _boom(url, timeout):
            raise urllib.error.URLError("unreachable")

        monkeypatch.setattr(sampler.time, "sleep", lambda _: None)
        monkeypatch.setattr(sampler.urllib.request, "urlopen", _boom)
        assert sampler._get(sampler.ESEARCH, {"term": "x"}) is None


class TestRecordCountAndIds:
    """A count is evidence about a set member; a failure must not pose as one."""

    def test_the_count_and_a_sample_of_ids_are_read(self, monkeypatch):
        monkeypatch.setattr(sampler, "_get", _FakeEUtils({"GENBANK": _esearch(205864, ("1", "2"))}))
        assert sampler._record_count_and_ids("GENBANK", {}) == (205864, ["1", "2"])

    def test_an_unreachable_esearch_is_not_zero_records(self, monkeypatch):
        # 0 is what a member that has become dead weight looks like, and the
        # script's stated use is deciding whether to drop such a member.
        monkeypatch.setattr(sampler, "_get", _FakeEUtils({"GENBANK": None}))
        count, ids = sampler._record_count_and_ids("GENBANK", {})
        assert count == -1
        assert ids == []

    def test_a_response_carrying_no_count_is_not_zero_records(self, monkeypatch):
        # NCBI answers 200 with an error document rather than an HTTP error, so
        # the transport succeeds and only the missing <Count> gives it away.
        monkeypatch.setattr(
            sampler,
            "_get",
            _FakeEUtils({"GENBANK": "<eSearchResult><ERROR>overloaded</ERROR></eSearchResult>"}),
        )
        assert sampler._record_count_and_ids("GENBANK", {})[0] == -1


class TestSpellings:
    """``None`` means unread; an empty counter means read and carrying nothing."""

    def test_the_literal_spellings_are_counted(self, monkeypatch):
        monkeypatch.setattr(sampler, "_get", _FakeEUtils({}, efetch=_efetch("GenBank", "PDB")))
        assert sampler._spellings(["1"], {}) == Counter({"GenBank": 1, "PDB": 1})

    def test_nothing_to_fetch_is_an_empty_reading_not_a_failure(self, monkeypatch):
        # A name with no records has nothing to sample. That is a completed
        # measurement, and the caller still classifies it by its own spelling.
        monkeypatch.setattr(sampler, "_get", _FakeEUtils({}, efetch=None))
        assert sampler._spellings([], {}) == Counter()

    def test_an_unreachable_efetch_is_not_an_empty_reading(self, monkeypatch):
        monkeypatch.setattr(sampler, "_get", _FakeEUtils({}, efetch=None))
        assert sampler._spellings(["1"], {}) is None

    def test_an_unparsable_response_is_not_an_empty_reading(self, monkeypatch):
        monkeypatch.setattr(sampler, "_get", _FakeEUtils({}, efetch="<PubmedArticleSet><trunc"))
        assert sampler._spellings(["1"], {}) is None


class TestCandidates:
    """Whatever bmlib matches has to be something the script reports on."""

    def test_every_name_bmlib_matches_is_measured(self):
        # The blind spot worth preventing: a name in one of the two sets that
        # NLM's table does not carry would otherwise be matched in production
        # and never measured here.
        matched = _TRIAL_REGISTRY_NAMES | frozenset(_DEPOSITION_DATABANK_LEVELS)
        measured = {name.lower() for name in sampler._candidates(matched)}
        assert matched <= measured

    def test_a_name_nlm_and_bmlib_both_carry_is_measured_once(self):
        # The sets are folded in by difference, not appended wholesale, so
        # NLM's spelling stays the one candidate for the name.
        candidates = sampler._candidates(
            _TRIAL_REGISTRY_NAMES | frozenset(_DEPOSITION_DATABANK_LEVELS)
        )
        lowered = [name.lower() for name in candidates]
        assert len(lowered) == len(set(lowered))


class TestTable:
    """The printed reading — what a maintainer actually acts on."""

    def test_a_measured_archive_is_read_as_a_deposition(self, monkeypatch, capsys):
        out = _run(
            monkeypatch,
            capsys,
            ("GENBANK",),
            _FakeEUtils({"GENBANK": _esearch(205864, ("1",))}, efetch=_efetch("GenBank")),
        )
        row = _row(out, "GENBANK")
        assert "205864" in row
        assert "GenBank" in row  # the publisher's spelling, not the candidate's
        assert row.endswith("data deposition → full_open")

    def test_a_controlled_access_repository_reports_its_own_level(self, monkeypatch, capsys):
        # `_DEPOSITION_DATABANK_LEVELS` is a mapping, not a membership test:
        # dbGaP's deposit is real but gated behind a Data Access Committee, so
        # it establishes `on_request` where the rest establish `full_open`. A
        # yes/no column could not show that mapping changing.
        out = _run(
            monkeypatch,
            capsys,
            ("dbGaP",),
            _FakeEUtils({"dbGaP": _esearch(276, ("1",))}, efetch=_efetch("dbGaP")),
        )
        assert _row(out, "dbGaP").endswith("data deposition → on_request")

    def test_a_punctuation_variant_spelling_still_matches(self, monkeypatch, capsys):
        # NLM's table says "UMIN CTR" and the records say "UMIN-CTR". bmlib
        # matches the records, so the script has to reconcile the two or report
        # a registry it recognises as unclassified.
        out = _run(
            monkeypatch,
            capsys,
            ("UMIN CTR",),
            _FakeEUtils({"UMIN CTR": _esearch(1309, ("1",))}, efetch=_efetch("UMIN-CTR")),
        )
        row = _row(out, "UMIN CTR")
        assert "UMIN-CTR" in row
        assert row.endswith("registration")

    def test_an_esearch_failure_prints_error_rather_than_zero(self, monkeypatch, capsys):
        out = _run(monkeypatch, capsys, ("GENBANK",), _FakeEUtils({"GENBANK": None}))
        row = _row(out, "GENBANK")
        assert "ERROR" in row
        assert "0" not in row

    def test_an_efetch_failure_is_not_printed_as_drift(self, monkeypatch, capsys):
        # The defect this replaced: with no spellings to read, the fallback
        # classified by the candidate's own name and printed "-" for the
        # spelling — a row indistinguishable from a genuine reading, and for a
        # non-member indistinguishable from the drift the script hunts for.
        out = _run(
            monkeypatch,
            capsys,
            ("GENBANK",),
            _FakeEUtils({"GENBANK": _esearch(205864, ("1",))}, efetch=None),
        )
        row = _row(out, "GENBANK")
        assert "205864" in row  # the count was measured and is still reported
        assert "ERROR" in row
        assert row.endswith("not measured")
        assert "data deposition" not in row

    def test_a_name_with_no_records_is_classified_by_its_own_spelling(self, monkeypatch, capsys):
        # REPEC has no PubMed records, so there is no spelling to read. Falling
        # back to the candidate's own name is right *here* — the measurement
        # completed — which is what the unread case must not be able to borrow.
        out = _run(monkeypatch, capsys, ("REPEC",), _FakeEUtils({"REPEC": _esearch(0)}))
        row = _row(out, "REPEC")
        assert row.endswith("registration")

    def test_a_name_in_neither_set_is_reported_as_unclassified(self, monkeypatch, capsys):
        # The drift signal: records exist under a name bmlib credits as
        # neither, which is how a repository NLM added shows up.
        out = _run(
            monkeypatch,
            capsys,
            ("SomeNewBank",),
            _FakeEUtils({"SomeNewBank": _esearch(42, ("1",))}, efetch=_efetch("SomeNewBank")),
        )
        assert _row(out, "SomeNewBank").endswith("unclassified")


@pytest.mark.parametrize("name", ["OMIM", "RefSeq", "PubChem-Compound"])
def test_a_curated_database_is_measured_but_credited_as_neither(name, monkeypatch, capsys):
    # These are on NLM's list and deliberately in neither set. They must stay
    # *candidates* — dropping them would hide the count that justifies the
    # exclusion — while still reading as unclassified.
    assert name in sampler.NLM_DATABANK_NAMES
    out = _run(
        monkeypatch, capsys, (name,), _FakeEUtils({name: _esearch(4159, ("1",))}, _efetch(name))
    )
    assert _row(out, name).endswith("unclassified")
