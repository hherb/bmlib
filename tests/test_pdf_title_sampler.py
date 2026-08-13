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

"""Tests for ``scripts/sample_pdf_metadata_titles.py``.

The script is a live runner — it downloads real PDFs from Europe PMC and
bioRxiv — but the corpus it writes is the evidence for issue #56's acceptance
rule, so the labelling has to be trustworthy offline. Two properties are
pinned here.

**A PDF that could not be sampled is never a finding.** A 429 surviving its
retries, a transport exception, a non-200 or a file PyMuPDF cannot open is
*unmeasured*: excluded from every denominator, and reported as ERROR rather
than as a rate once it eats more than a fifth of a population. A zero junk
rate is what a healthy population looks like, and a dead host must not read as
one — the rule ``sample_free_pdf_urls.py`` already lives by.

**The buckets label against ground truth, not against the rule under test.**
The sampler never runs the acceptance rule; if it did, the corpus could only
ever confirm whatever the rule already believed.

No network, no real sleeping: every test drives the script through a stubbed
client, and any test that exercises a retry path stubs ``_sleep_for``.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "sample_pdf_metadata_titles.py"
_spec = importlib.util.spec_from_file_location("bmlib_pdf_title_sampler", _PATH)
if _spec is None or _spec.loader is None:  # pragma: no cover - the script is in-tree
    raise ImportError(f"cannot load the sampler from {_PATH}")
sampler = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = sampler
# The sampler does `from _sampling import …`, and `scripts/` is not a package.
# Running the script puts that directory on sys.path as sys.path[0]; loading it
# by path here does not, so insert it explicitly.
if str(_PATH.parent) not in sys.path:
    sys.path.insert(0, str(_PATH.parent))
_spec.loader.exec_module(sampler)

_HAS_FITZ = importlib.util.find_spec("fitz") is not None

_RECORD_TITLE = "Effects of aspirin on cardiovascular outcomes"
_BODY_SIZE = 10
_TITLE_SIZE = 18


class _Resp:
    """A minimal stand-in for an httpx response."""

    def __init__(
        self,
        status_code: int,
        content: bytes = b"%PDF-1.7 ...",
        headers: dict[str, str] | None = None,
        payload: object = None,
    ) -> None:
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}
        self._payload = payload

    def json(self) -> object:
        return self._payload


class _Client:
    """Answers each URL from a script, or raises what the script holds."""

    def __init__(self, answers: dict[str, object]) -> None:
        self.answers = answers
        self.seen: list[str] = []

    def get(self, url: str, **kwargs: object) -> _Resp:
        self.seen.append(url)
        answer = self.answers[url]
        if isinstance(answer, Exception):
            raise answer
        assert isinstance(answer, _Resp)
        return answer


class _SequencedClient:
    """Answers successive ``get`` calls from a fixed list, in order.

    Used for retry tests, where the same URL must answer differently on
    successive attempts (429, then 200) — ``_Client`` holds one fixed answer
    per URL.
    """

    def __init__(self, answers: list[object]) -> None:
        self._answers = list(answers)
        self.seen: list[str] = []

    def get(self, url: str, **kwargs: object) -> _Resp:
        self.seen.append(url)
        answer = self._answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        assert isinstance(answer, _Resp)
        return answer


def _make_pdf(metadata_title: str) -> bytes:
    """A two-page PDF: a large-font title line, body text, and metadata."""
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Effects of aspirin on cardiovascular", fontsize=_TITLE_SIZE)
    page.insert_text((72, 96), "outcomes", fontsize=_TITLE_SIZE)
    page.insert_text((72, 130), "Jane Smith, John Doe", fontsize=_BODY_SIZE)
    for i in range(12):
        page.insert_text((72, 160 + i * 14), f"Body line {i} of the abstract.", fontsize=_BODY_SIZE)
    second = doc.new_page()
    for i in range(12):
        second.insert_text((72, 72 + i * 14), f"Methods line {i}.", fontsize=_BODY_SIZE)
    doc.set_metadata({"title": metadata_title})
    body = doc.tobytes()
    doc.close()
    return bytes(body)


class TestTheBucketsLabelAgainstTheRecordTitle:
    """Ground truth is the record title the API already states, so every row
    self-labels — no hand-labelling pass, unlike tests/data/funder_names.json."""

    def test_an_exact_title_is_a_match(self) -> None:
        assert sampler.classify(_RECORD_TITLE, _RECORD_TITLE) == "match"

    def test_case_and_punctuation_drift_is_still_a_match(self) -> None:
        """Metadata routinely drops the terminal period and re-cases."""
        assert sampler.classify(_RECORD_TITLE.upper() + ".", _RECORD_TITLE) == "match"

    def test_a_prefix_of_the_record_title_is_truncated_not_junk(self) -> None:
        assert sampler.classify("Effects of aspirin", _RECORD_TITLE) == "truncated"

    def test_a_word_processor_filename_is_unrelated(self) -> None:
        assert sampler.classify("Microsoft Word - manuscript.docx", _RECORD_TITLE) == "unrelated"

    def test_a_blank_metadata_title_is_absent_not_unrelated(self) -> None:
        """The falsy case already falls through to the font heuristic; it is
        not what #56 is about, and counting it as junk would inflate the rate
        the fix is measured against."""
        assert sampler.classify("   ", _RECORD_TITLE) == "absent"

    def test_a_title_longer_than_the_record_title_is_not_truncated(self) -> None:
        """ "Truncated" means *shorter*. A metadata title that extends the
        record title is a different string, not a prefix of it."""
        assert sampler.classify(_RECORD_TITLE + " in adults", _RECORD_TITLE) == "unrelated"


class TestAPDFThatCouldNotBeSampledIsNeverAFinding:
    """Unlike sample_free_pdf_urls.py, a failed download here is not a
    finding: this script measures *titles*, so a PDF it never got is a row it
    cannot label, not a bad title."""

    def test_a_persistent_429_is_unmeasured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sampler, "_sleep_for", lambda seconds: None)
        client = _Client({"https://e/a.pdf": _Resp(429)})
        assert sampler.download(client, "https://e/a.pdf", lambda url: None) == (None, False)

    def test_a_429_that_clears_on_retry_is_measured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sampler, "_sleep_for", lambda seconds: None)
        client = _SequencedClient([_Resp(429), _Resp(200, b"%PDF-1.7 body")])
        body, measured = sampler.download(client, "https://e/a.pdf", lambda url: None)
        assert body == b"%PDF-1.7 body"
        assert measured is True

    def test_a_transport_exception_is_unmeasured(self) -> None:
        client = _Client({"https://e/a.pdf": OSError("boom")})
        assert sampler.download(client, "https://e/a.pdf", lambda url: None) == (None, False)

    def test_a_non_200_is_unmeasured(self) -> None:
        client = _Client({"https://e/a.pdf": _Resp(404)})
        assert sampler.download(client, "https://e/a.pdf", lambda url: None) == (None, False)

    def test_a_body_that_is_not_a_pdf_is_unmeasured(self) -> None:
        """An Unpaywall-style landing page. Measured over 28 probes in #68's
        run, half of that population's bodies were HTML."""
        client = _Client({"https://e/a.pdf": _Resp(200, b"<!DOCTYPE html>")})
        assert sampler.download(client, "https://e/a.pdf", lambda url: None) == (None, False)

    def test_an_oversized_body_is_unmeasured_not_parsed(self) -> None:
        big = b"%PDF-1.7" + b"x" * (sampler.MAX_PDF_BYTES + 1)
        client = _Client({"https://e/a.pdf": _Resp(200, big)})
        assert sampler.download(client, "https://e/a.pdf", lambda url: None) == (None, False)


class TestAnUnmeasuredPopulationPrintsErrorNotAZero:
    """The rule sample_free_pdf_urls.py established: the rows that got through
    heavy throttling are the *early* ones, not a random sample."""

    def test_a_fifth_unmeasured_still_reports(self) -> None:
        lines = sampler.summarise("europepmc", [{"bucket": "match"}] * 8, unmeasured=2)
        assert not any("ERROR" in line for line in lines)

    def test_more_than_a_fifth_unmeasured_is_an_error_with_no_rate(self) -> None:
        lines = sampler.summarise("europepmc", [{"bucket": "match"}] * 7, unmeasured=3)
        assert any("ERROR" in line for line in lines)
        assert "%" not in "\n".join(lines)

    def test_a_population_with_no_rows_is_an_error(self) -> None:
        assert any("ERROR" in line for line in sampler.summarise("biorxiv", [], unmeasured=0))

    def test_a_reported_population_names_every_bucket(self) -> None:
        """A bucket printed only when non-empty reads as an absence of junk
        when it is an absence of a line."""
        rows = [{"bucket": "match"}] * 9 + [{"bucket": "unrelated"}]
        text = "\n".join(sampler.summarise("europepmc", rows, unmeasured=0))
        for bucket in ("match", "truncated", "unrelated", "absent"):
            assert bucket in text


@pytest.mark.skipif(not _HAS_FITZ, reason="PyMuPDF not installed")
class TestTheFixtureRowCarriesWhatTheMetricTestNeeds:
    def test_a_row_carries_the_document_median_not_the_title_pages(self, tmp_path: Path) -> None:
        """The metric test re-runs _extract_title, whose _TITLE_SIZE_RATIO
        compares a candidate against the *document's* body median. Recomputing
        one from 20 stored title-page lines would compare headings against
        headings."""
        row = sampler.row_from_pdf(
            _make_pdf(_RECORD_TITLE), "europepmc", "PMC1", _RECORD_TITLE, "a.pdf", tmp_path
        )
        assert row is not None
        assert row["median_font_size"] == pytest.approx(float(_BODY_SIZE), abs=0.5)

    def test_page_one_lines_keep_the_converters_own_order(self, tmp_path: Path) -> None:
        """Document order as ``extract_blocks`` reports it, not sorted by y.

        The segmenter consumes that order, so the fixture has to preserve it —
        and a real PDF does not hand its lines back top-to-bottom anyway: the
        first live row's stored blocks began with the page-footer "1" at
        y=779, above the title at y=149.
        """
        pdf = _make_pdf(_RECORD_TITLE)
        row = sampler.row_from_pdf(pdf, "europepmc", "PMC1", _RECORD_TITLE, "a.pdf", tmp_path)
        assert row is not None
        assert 0 < len(row["page_one_lines"]) <= sampler.PAGE_ONE_LINES_KEPT

        path = tmp_path / "reference.pdf"
        path.write_bytes(pdf)
        blocks = sampler.PyMuPDFConverter().extract_blocks(path)
        expected = [b.text for b in blocks if b.page_num == 0][: sampler.PAGE_ONE_LINES_KEPT]
        assert [line["text"] for line in row["page_one_lines"]] == expected

    def test_a_truncated_page_one_says_how_much_it_held(self, tmp_path: Path) -> None:
        """A row whose title fell outside the cap is not evidence about a rule
        that sees whole pages, so the count has to be recoverable rather than
        silently scoring as a rejection that never happened."""
        row = sampler.row_from_pdf(
            _make_pdf(_RECORD_TITLE), "europepmc", "PMC1", _RECORD_TITLE, "a.pdf", tmp_path
        )
        assert row is not None
        assert row["page_one_line_count"] >= len(row["page_one_lines"])

    def test_a_row_carries_only_page_one(self, tmp_path: Path) -> None:
        """Page 2's lines would corroborate a title page 1 never printed."""
        row = sampler.row_from_pdf(
            _make_pdf(_RECORD_TITLE), "europepmc", "PMC1", _RECORD_TITLE, "a.pdf", tmp_path
        )
        assert row is not None
        assert not any("Methods line" in line["text"] for line in row["page_one_lines"])

    def test_the_row_is_labelled_and_keeps_the_verbatim_metadata_title(
        self, tmp_path: Path
    ) -> None:
        row = sampler.row_from_pdf(
            _make_pdf("Microsoft Word - ms.docx"),
            "biorxiv",
            "10.1101/2026.01.01.123456",
            _RECORD_TITLE,
            "ms.pdf",
            tmp_path,
        )
        assert row is not None
        assert row["metadata_title"] == "Microsoft Word - ms.docx"
        assert row["bucket"] == "unrelated"
        assert row["record_title"] == _RECORD_TITLE
        assert row["file_name"] == "ms.pdf"

    def test_a_pdf_pymupdf_cannot_read_is_unmeasured_not_an_empty_row(self, tmp_path: Path) -> None:
        """An empty row would enter the corpus as a document with no title
        page — indistinguishable from a real image-only scan, which is a case
        the rule treats specially."""
        assert (
            sampler.row_from_pdf(
                b"%PDF-1.7 truncated", "europepmc", "PMC1", _RECORD_TITLE, "a.pdf", tmp_path
            )
            is None
        )

    def test_the_temp_file_does_not_outlive_the_row(self, tmp_path: Path) -> None:
        """300 PDFs at a few MB each; the run must not leave them behind."""
        sampler.row_from_pdf(
            _make_pdf(_RECORD_TITLE), "europepmc", "PMC1", _RECORD_TITLE, "a.pdf", tmp_path
        )
        assert list(tmp_path.iterdir()) == []


class TestTheExitStatusAgreesWithWhatWasPrinted:
    def test_an_errored_population_exits_non_zero(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(sampler, "sample_europepmc_rows", lambda *a, **k: ([], 40))
        monkeypatch.setattr(
            sampler, "sample_biorxiv_rows", lambda *a, **k: ([{"bucket": "match"}] * 10, 0)
        )
        monkeypatch.setattr(sys, "argv", ["s", "-o", str(tmp_path / "out.json")])
        assert sampler.main() != 0

    def test_two_healthy_populations_exit_zero(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        rows = [{"bucket": "match", "source": "x", "id": "1"}] * 10
        monkeypatch.setattr(sampler, "sample_europepmc_rows", lambda *a, **k: (rows, 0))
        monkeypatch.setattr(sampler, "sample_biorxiv_rows", lambda *a, **k: (rows, 0))
        monkeypatch.setattr(sys, "argv", ["s", "-o", str(tmp_path / "out.json")])
        assert sampler.main() == 0
        assert (tmp_path / "out.json").exists()


class TestARunSurvivesBeingInterrupted:
    """A three-hour run that keeps nothing until the end is a run you cannot
    stop, and one crash or one closed laptop costs every row. Rows are
    appended as they land, and a later run resumes from them."""

    def test_a_row_is_appended_as_soon_as_it_lands(self, tmp_path: Path) -> None:
        partial = tmp_path / "corpus.partial.jsonl"
        sampler.append_row(partial, {"source": "biorxiv", "id": "a", "bucket": "match"})
        sampler.append_row(partial, {"source": "biorxiv", "id": "b", "bucket": "unrelated"})
        assert [row["id"] for row in sampler.load_partial(partial)] == ["a", "b"]

    def test_a_missing_partial_file_resumes_from_nothing(self, tmp_path: Path) -> None:
        assert sampler.load_partial(tmp_path / "absent.jsonl") == []

    def test_a_half_written_last_line_does_not_lose_the_rest(self, tmp_path: Path) -> None:
        """What a kill mid-write actually leaves behind. Refusing to parse the
        file at all would turn a truncated final row into the loss of every
        row before it — the failure this whole mechanism exists to prevent."""
        partial = tmp_path / "corpus.partial.jsonl"
        sampler.append_row(partial, {"source": "biorxiv", "id": "a", "bucket": "match"})
        with partial.open("a", encoding="utf-8") as handle:
            handle.write('{"source": "biorxiv", "id": "b", "buck')
        assert [row["id"] for row in sampler.load_partial(partial)] == ["a"]

    def test_ids_already_collected_are_not_fetched_again(self) -> None:
        assert sampler.already_seen([{"id": "a"}, {"id": "b"}]) == {"a", "b"}


class TestAPDFIsDownloadedOnceAcrossRuns:
    """The expensive half of a run is the transfer, so a resumed run must not
    pay for it twice."""

    def test_a_cached_pdf_is_reused_without_a_request(self, tmp_path: Path) -> None:
        from bmlib.fulltext.cache import FullTextCache

        cache = FullTextCache(cache_dir=tmp_path / "cache")
        cache.save_pdf(b"%PDF-1.7 cached", "PMC1")
        client = _Client({})  # any request would raise KeyError
        body, measured = sampler.fetch_pdf(
            client, "https://e/a.pdf", "PMC1", lambda url: None, cache
        )
        assert body == b"%PDF-1.7 cached"
        assert measured is True
        assert client.seen == []

    def test_a_downloaded_pdf_is_kept_for_the_next_run(self, tmp_path: Path) -> None:
        from bmlib.fulltext.cache import FullTextCache

        cache = FullTextCache(cache_dir=tmp_path / "cache")
        client = _Client({"https://e/a.pdf": _Resp(200, b"%PDF-1.7 fresh")})
        body, measured = sampler.fetch_pdf(
            client, "https://e/a.pdf", "PMC1", lambda url: None, cache
        )
        assert (body, measured) == (b"%PDF-1.7 fresh", True)
        assert cache.get_pdf("PMC1") is not None

    def test_no_cache_still_downloads(self, tmp_path: Path) -> None:
        """The cache is an optimisation, not a precondition."""
        client = _Client({"https://e/a.pdf": _Resp(200, b"%PDF-1.7 fresh")})
        assert sampler.fetch_pdf(client, "https://e/a.pdf", "PMC1", lambda url: None, None) == (
            b"%PDF-1.7 fresh",
            True,
        )

    def test_a_failed_download_caches_nothing(self, tmp_path: Path) -> None:
        from bmlib.fulltext.cache import FullTextCache

        cache = FullTextCache(cache_dir=tmp_path / "cache")
        client = _Client({"https://e/a.pdf": _Resp(404)})
        assert sampler.fetch_pdf(client, "https://e/a.pdf", "PMC1", lambda url: None, cache) == (
            None,
            False,
        )
        assert cache.get_pdf("PMC1") is None


class TestAResumedRunTopsUpRatherThanRestarting:
    def test_it_asks_only_for_the_rows_it_still_needs(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        output = tmp_path / "corpus.json"
        journal = sampler._journal_path(output)
        for i in range(4):
            sampler.append_row(journal, {"source": "europepmc", "id": f"PMC{i}", "bucket": "match"})
        asked: list[int] = []

        def fake_europepmc(client, target, context):
            asked.append(target)
            return [], 0

        monkeypatch.setattr(sampler, "sample_europepmc_rows", fake_europepmc)
        monkeypatch.setattr(sampler, "sample_biorxiv_rows", lambda *a, **k: ([], 0))
        monkeypatch.setattr(
            sys, "argv", ["s", "-o", str(output), "--target", "10", "--no-pdf-cache"]
        )
        sampler.main()
        assert asked == [6]

    def test_the_rows_already_held_reach_the_final_corpus(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The point of resuming: three hours of downloads must not evaporate
        because the run was interrupted before it wrote its output."""
        output = tmp_path / "corpus.json"
        journal = sampler._journal_path(output)
        sampler.append_row(journal, {"source": "europepmc", "id": "PMC1", "bucket": "match"})
        monkeypatch.setattr(sampler, "sample_europepmc_rows", lambda *a, **k: ([], 0))
        monkeypatch.setattr(sampler, "sample_biorxiv_rows", lambda *a, **k: ([], 0))
        monkeypatch.setattr(sys, "argv", ["s", "-o", str(output), "--no-pdf-cache"])
        sampler.main()
        assert [row["id"] for row in json.loads(output.read_text())] == ["PMC1"]

    def test_an_identifier_already_attempted_is_not_fetched_again(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        output = tmp_path / "corpus.json"
        journal = sampler._journal_path(output)
        sampler.append_row(journal, {"source": "europepmc", "id": "PMC1", "bucket": "match"})
        seen: list[set[str]] = []
        monkeypatch.setattr(
            sampler, "sample_europepmc_rows", lambda c, t, ctx: (seen.append(ctx.seen), ([], 0))[1]
        )
        monkeypatch.setattr(sampler, "sample_biorxiv_rows", lambda *a, **k: ([], 0))
        monkeypatch.setattr(sys, "argv", ["s", "-o", str(output), "--no-pdf-cache"])
        sampler.main()
        assert seen == [{"PMC1"}]

    def test_a_failed_attempt_is_remembered_so_the_share_survives_a_resume(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A resumed run that inherited only its successes would recompute the
        unmeasured share over a denominator missing its failures — and the
        ERROR rule would then pass by having forgotten."""
        output = tmp_path / "corpus.json"
        journal = sampler._journal_path(output)
        sampler.append_row(journal, {"source": "europepmc", "id": "PMC1", "bucket": "match"})
        for i in range(9):
            sampler.append_row(journal, {"unmeasured": True, "source": "europepmc", "id": f"x{i}"})
        printed: list[str] = []
        monkeypatch.setattr(sampler, "sample_europepmc_rows", lambda *a, **k: ([], 0))
        monkeypatch.setattr(sampler, "sample_biorxiv_rows", lambda *a, **k: ([], 0))
        monkeypatch.setattr(sys, "argv", ["s", "-o", str(output), "--no-pdf-cache"])
        monkeypatch.setattr("builtins.print", lambda *a, **k: printed.append(" ".join(map(str, a))))
        assert sampler.main() != 0
        assert any("ERROR" in line for line in printed)
