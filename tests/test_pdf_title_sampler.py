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
from datetime import date, timedelta
from pathlib import Path
from typing import Any

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


def _noop(url: str) -> None:
    """A pacer that does not pace, for tests measuring something else."""


def _make_pdf_with_page_one_lines(count: int, first_line: str | None = None) -> bytes:
    """A one-page PDF with *count* distinct lines on page 1.

    The other fixture emits 15, comfortably inside ``PAGE_ONE_LINES_KEPT``, so
    nothing built on it can exercise the cap or the truncation flag.
    """
    import fitz

    lines = [
        first_line if (i == 0 and first_line is not None) else f"Page one line {i}."
        for i in range(count)
    ]
    # Wide enough for the longest line to fit: `insert_text` clips at the page
    # edge, which would truncate by geometry and hide the cap under test.
    width = max(612.0, 40 + max(len(line) for line in lines) * _BODY_SIZE * 0.7)
    doc = fitz.open()
    page = doc.new_page(width=width, height=max(792, 40 + count * 12))
    for i, text in enumerate(lines):
        page.insert_text((36, 30 + i * 12), text, fontsize=_BODY_SIZE)
    doc.set_metadata({"title": _RECORD_TITLE})
    body = doc.tobytes()
    doc.close()
    return bytes(body)


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
        assert sampler.download(client, "https://e/a.pdf", lambda url: None) == (
            None,
            False,
            "throttled",
        )

    def test_a_429_that_clears_on_retry_is_measured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sampler, "_sleep_for", lambda seconds: None)
        client = _SequencedClient([_Resp(429), _Resp(200, b"%PDF-1.7 body")])
        body, measured, cause = sampler.download(client, "https://e/a.pdf", lambda url: None)
        assert body == b"%PDF-1.7 body"
        assert measured is True
        assert cause == ""

    def test_a_transport_exception_is_unmeasured(self) -> None:
        client = _Client({"https://e/a.pdf": OSError("boom")})
        assert sampler.download(client, "https://e/a.pdf", lambda url: None) == (
            None,
            False,
            "transport-OSError",
        )

    def test_a_non_200_is_unmeasured(self) -> None:
        client = _Client({"https://e/a.pdf": _Resp(404)})
        assert sampler.download(client, "https://e/a.pdf", lambda url: None) == (
            None,
            False,
            "http-404",
        )

    def test_a_body_that_is_not_a_pdf_is_unmeasured(self) -> None:
        """An Unpaywall-style landing page. Measured over 28 probes in #68's
        run, half of that population's bodies were HTML."""
        client = _Client({"https://e/a.pdf": _Resp(200, b"<!DOCTYPE html>")})
        assert sampler.download(client, "https://e/a.pdf", lambda url: None) == (
            None,
            False,
            "not-a-pdf",
        )

    def test_an_oversized_body_is_unmeasured_not_parsed(self) -> None:
        big = b"%PDF-1.7" + b"x" * (sampler.MAX_PDF_BYTES + 1)
        client = _Client({"https://e/a.pdf": _Resp(200, big)})
        assert sampler.download(client, "https://e/a.pdf", lambda url: None) == (
            None,
            False,
            "oversized",
        )

    def test_every_unmeasured_cause_is_distinguishable(self) -> None:
        """The point of recording a cause at all: a resume that cannot tell a
        dead network from a dead link cannot tell whether re-running helps.
        Distinct slugs are what makes that decidable, so a refactor that
        collapsed two of them must fail here."""
        causes = {
            sampler.download(_Client({"https://e/a.pdf": _Resp(404)}), "https://e/a.pdf", _noop)[2],
            sampler.download(
                _Client({"https://e/a.pdf": _Resp(200, b"<html>")}), "https://e/a.pdf", _noop
            )[2],
            sampler.download(
                _Client({"https://e/a.pdf": OSError("boom")}), "https://e/a.pdf", _noop
            )[2],
        }
        assert causes == {"http-404", "not-a-pdf", "transport-OSError"}


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
        when it is an absence of a line.

        Asserted as whole rendered rows, not as bare substrings: every bucket
        name also appears in this module's own prose, and a ``summarise`` that
        printed labels with no counts passed the substring form.
        """
        rows = [{"bucket": "match"}] * 9 + [{"bucket": "unrelated"}]
        lines = sampler.summarise("europepmc", rows, unmeasured=0)
        assert lines[1:] == [
            f"    {'match':<10} {9:>4}  {0.9:>6.1%}",
            f"    {'truncated':<10} {0:>4}  {0.0:>6.1%}",
            f"    {'unrelated':<10} {1:>4}  {0.1:>6.1%}",
            f"    {'absent':<10} {0:>4}  {0.0:>6.1%}",
        ]


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

    def test_an_untruncated_page_one_reports_its_full_count(self, tmp_path: Path) -> None:
        """The fixture fits inside the cap, so the two numbers agree — and
        that is exactly why it cannot pin the truncation flag. The test below
        does that, on a page built to overflow."""
        row = sampler.row_from_pdf(
            _make_pdf(_RECORD_TITLE), "europepmc", "PMC1", _RECORD_TITLE, "a.pdf", tmp_path
        )
        assert row is not None
        assert row["page_one_line_count"] == len(row["page_one_lines"])

    def test_a_truncated_page_one_says_how_much_it_held(self, tmp_path: Path) -> None:
        """A row whose title fell outside the cap is not evidence about a rule
        that sees whole pages, so the count has to be recoverable rather than
        silently scoring as a rejection that never happened.

        ``tests/test_pdf_metadata_titles.py::_is_truncated`` derives exactly
        this comparison and uses it to **exclude rows from the wrong-rejection
        denominator** — 200 of the 235 committed rows are truncated — so a
        count that silently equalled the stored length would let real
        rejections vanish behind a passing 0.00%. Every other fixture here
        fits inside the cap, which left both the slice and the count able to
        be deleted with the suite still green.
        """
        overflowing = _make_pdf_with_page_one_lines(sampler.PAGE_ONE_LINES_KEPT + 20)
        row = sampler.row_from_pdf(
            overflowing, "europepmc", "PMC1", _RECORD_TITLE, "a.pdf", tmp_path
        )
        assert row is not None
        assert len(row["page_one_lines"]) == sampler.PAGE_ONE_LINES_KEPT
        assert row["page_one_line_count"] > len(row["page_one_lines"])
        assert row["page_one_line_count"] >= sampler.PAGE_ONE_LINES_KEPT + 20

    def test_a_stored_line_is_capped_in_length(self, tmp_path: Path) -> None:
        """``MAX_LINE_CHARS`` had the same shape of gap: every fixture line is
        short, so the slice was a no-op and the assertion compared against the
        converter's untruncated text."""
        long_line = "x" * (sampler.MAX_LINE_CHARS + 50)
        pdf = _make_pdf_with_page_one_lines(3, first_line=long_line)
        row = sampler.row_from_pdf(pdf, "europepmc", "PMC1", _RECORD_TITLE, "a.pdf", tmp_path)
        assert row is not None
        assert max(len(line["text"]) for line in row["page_one_lines"]) == sampler.MAX_LINE_CHARS

    def test_a_row_carries_only_page_one(self, tmp_path: Path) -> None:
        """Page 2's lines would corroborate a title page 1 never printed.

        The ``not any`` below passes on an empty list, so the non-empty guard
        is what makes it a test — the fixture's page 2 really does carry
        "Methods line" for it to have excluded.
        """
        row = sampler.row_from_pdf(
            _make_pdf(_RECORD_TITLE), "europepmc", "PMC1", _RECORD_TITLE, "a.pdf", tmp_path
        )
        assert row is not None
        assert row["page_one_lines"], "no stored lines, so the exclusion below checks nothing"
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
    """The stubs here journal what they collect, as a real walk does: `main`
    tallies from the journal, so a stub that only *returned* rows would be
    testing a code path that cannot happen."""

    @staticmethod
    def _walk(entries: list[dict[str, Any]]):
        def walk(client, target, context, **kwargs):
            for entry in entries:
                sampler.append_row(context.journal, entry)
            return [], 0

        return walk

    def test_an_errored_population_exits_non_zero(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        failures = [{"unmeasured": True, "source": "europepmc", "id": f"e{i}"} for i in range(40)]
        good = [{"source": "biorxiv", "id": f"b{i}", "bucket": "match"} for i in range(10)]
        monkeypatch.setattr(sampler, "sample_europepmc_rows", self._walk(failures))
        monkeypatch.setattr(sampler, "sample_biorxiv_rows", self._walk(good))
        monkeypatch.setattr(sys, "argv", ["s", "-o", str(tmp_path / "out.json"), "--no-pdf-cache"])
        assert sampler.main() != 0

    def test_two_healthy_populations_exit_zero(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        europe = [{"source": "europepmc", "id": f"e{i}", "bucket": "match"} for i in range(10)]
        biorxiv = [{"source": "biorxiv", "id": f"b{i}", "bucket": "match"} for i in range(10)]
        monkeypatch.setattr(sampler, "sample_europepmc_rows", self._walk(europe))
        monkeypatch.setattr(sampler, "sample_biorxiv_rows", self._walk(biorxiv))
        monkeypatch.setattr(sys, "argv", ["s", "-o", str(tmp_path / "out.json"), "--no-pdf-cache"])
        assert sampler.main() == 0
        assert len(json.loads((tmp_path / "out.json").read_text())) == 20

    def test_an_unreportable_run_does_not_replace_the_corpus(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The ERROR line and the exit code were the only defence, and the
        write happened before either. ``bmlib/fulltext/_titles.py`` names this
        file as the evidence its rule was measured on and tells the next
        maintainer to re-run the sampler before touching the reject-list — so
        a run too throttled to report must not become the corpus that is then
        read as if it had been. The prior corpus survives untouched, and the
        thin one lands beside it under a name that says what it is.
        """
        out = tmp_path / "out.json"
        previous = [{"source": "europepmc", "id": "kept", "bucket": "match"}]
        out.write_text(json.dumps(previous))

        failures = [{"unmeasured": True, "source": "europepmc", "id": f"e{i}"} for i in range(40)]
        good = [{"source": "biorxiv", "id": f"b{i}", "bucket": "match"} for i in range(10)]
        monkeypatch.setattr(sampler, "sample_europepmc_rows", self._walk(failures))
        monkeypatch.setattr(sampler, "sample_biorxiv_rows", self._walk(good))
        monkeypatch.setattr(sys, "argv", ["s", "-o", str(out), "--no-pdf-cache"])

        assert sampler.main() != 0
        assert json.loads(out.read_text()) == previous
        assert json.loads((tmp_path / "out.unreportable.json").read_text())

    def test_a_reportable_run_writes_no_sidecar(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The control on the test above: the sidecar must be the exceptional
        path, not somewhere every run also writes."""
        europe = [{"source": "europepmc", "id": f"e{i}", "bucket": "match"} for i in range(10)]
        biorxiv = [{"source": "biorxiv", "id": f"b{i}", "bucket": "match"} for i in range(10)]
        monkeypatch.setattr(sampler, "sample_europepmc_rows", self._walk(europe))
        monkeypatch.setattr(sampler, "sample_biorxiv_rows", self._walk(biorxiv))
        monkeypatch.setattr(sys, "argv", ["s", "-o", str(tmp_path / "out.json"), "--no-pdf-cache"])

        assert sampler.main() == 0
        assert not (tmp_path / "out.unreportable.json").exists()


class TestOnlyURLsBMLibWouldFetchEnterAPopulation:
    """``is_probeable``'s *use* in this sampler was unguarded: dropping the
    call from the Europe PMC walk left the suite green.

    Worse here than in the sibling sampler, which only counts a URL — this one
    writes the bytes to disk and hands them to PyMuPDF, so a ``file://`` URL
    out of third-party JSON would be read off the local filesystem and parsed.
    """

    def _payload(self, url: str) -> dict[str, object]:
        return {
            "resultList": {
                "result": [
                    {
                        "id": "PMC1",
                        "title": _RECORD_TITLE,
                        "fullTextUrlList": {
                            "fullTextUrl": [
                                {"documentStyle": "pdf", "availabilityCode": "OA", "url": url}
                            ]
                        },
                    }
                ]
            },
            "nextCursorMark": "",
        }

    def _run(self, url: str, tmp_path: Path) -> list[str]:
        fetched: list[str] = []

        class _Recording:
            def get(self, request_url: str, **kwargs: object) -> _Resp:
                if request_url == sampler.EUROPE_PMC_SEARCH:
                    return _Resp(200, payload=self_payload)
                fetched.append(request_url)
                return _Resp(404)

        self_payload = self._payload(url)
        context = sampler.RunContext(
            pace=lambda host: None, journal=tmp_path / "j.jsonl", seen=set(), cache=None, workers=1
        )
        sampler.sample_europepmc_rows(_Recording(), 5, context)
        return fetched

    def test_a_file_url_is_never_fetched(self, tmp_path: Path) -> None:
        assert self._run("file:///etc/passwd", tmp_path) == []

    def test_an_http_url_is(self, tmp_path: Path) -> None:
        """The negative control: the skip above is the scheme, not the
        fixture failing to produce a candidate at all."""
        assert self._run("https://e/a.pdf?pdf=render", tmp_path) == ["https://e/a.pdf?pdf=render"]


@pytest.mark.skipif(not _HAS_FITZ, reason="PyMuPDF not installed")
class TestAProbeThatCouldNotBeMadeIsNeverAFinding:
    """``process_candidates`` is the only place a failed fetch becomes an
    unmeasured count *and* a journal entry, and every ``main()`` test above
    patches the walks that call it out of the run.

    Deleting the whole ``unmeasured`` branch left the suite green, which means
    a run 90% throttled would have printed a confident distribution over the
    surviving 10% and exited 0 — the ERROR rule could never fire. These drive
    the real function over a stubbed transport instead.
    """

    @pytest.fixture(autouse=True)
    def _no_real_backoff(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A 503 is retried with backoff, so these would otherwise spend a
        real minute asleep to assert something about accounting."""
        monkeypatch.setattr(sampler, "_sleep_for", lambda seconds: None)

    @staticmethod
    def _context(tmp_path: Path) -> Any:
        return sampler.RunContext(
            pace=lambda host: None,
            journal=tmp_path / "journal.jsonl",
            seen=set(),
            cache=None,
            workers=1,
        )

    def _candidates(self, n: int) -> list[Any]:
        return [
            sampler.Candidate(
                source="europepmc",
                identifier=f"PMC{i}",
                record_title=_RECORD_TITLE,
                url=f"https://example.org/{i}.pdf",
            )
            for i in range(n)
        ]

    def test_a_failed_fetch_counts_as_unmeasured_and_is_journalled(self, tmp_path: Path) -> None:
        candidates = self._candidates(3)
        client = _Client({c.url: _Resp(503) for c in candidates})
        context = self._context(tmp_path)

        rows, unmeasured = sampler.process_candidates(client, candidates, context, tmp_path)

        assert rows == []
        assert unmeasured == 3
        journalled = sampler.load_partial(context.journal)
        assert [entry.get("unmeasured") for entry in journalled] == [True, True, True]
        assert {entry["id"] for entry in journalled} == {"PMC0", "PMC1", "PMC2"}

    def test_a_mixed_batch_counts_each_side_once(self, tmp_path: Path) -> None:
        """The denominator the ERROR rule divides by. A batch that loses some
        rows must report both numbers, or the share is computed against a
        total that never saw the failures."""
        candidates = self._candidates(4)
        answers = {c.url: _Resp(503) for c in candidates[:3]}
        answers[candidates[3].url] = _Resp(200, content=_make_pdf(_RECORD_TITLE))
        context = self._context(tmp_path)

        rows, unmeasured = sampler.process_candidates(
            _Client(answers), candidates, context, tmp_path
        )

        assert len(rows) == 1
        assert unmeasured == 3
        assert rows[0]["id"] == "PMC3"

    def test_the_unmeasured_share_reaches_the_error_rule(self, tmp_path: Path) -> None:
        """End to end through the accounting: a batch this thin must summarise
        as ERROR. This is the assertion that fails if the branch is deleted —
        `rows`/`unmeasured` alone could be re-derived, the ERROR line cannot."""
        candidates = self._candidates(5)
        answers = {c.url: _Resp(503) for c in candidates[:4]}
        answers[candidates[4].url] = _Resp(200, content=_make_pdf(_RECORD_TITLE))

        rows, unmeasured = sampler.process_candidates(
            _Client(answers), candidates, self._context(tmp_path), tmp_path
        )
        lines = sampler.summarise("europepmc", rows, unmeasured)

        assert any("ERROR" in line for line in lines), lines

    def test_a_healthy_batch_is_not_an_error(self, tmp_path: Path) -> None:
        """The negative control: the ERROR above must come from the failures,
        not from the batch being small."""
        candidates = self._candidates(5)
        answers = {c.url: _Resp(200, content=_make_pdf(_RECORD_TITLE)) for c in candidates}

        rows, unmeasured = sampler.process_candidates(
            _Client(answers), candidates, self._context(tmp_path), tmp_path
        )

        assert unmeasured == 0
        assert len(rows) == 5
        assert not any("ERROR" in line for line in sampler.summarise("europepmc", rows, unmeasured))


class TestASummaryCannotQuietlyDropARow:
    """``Counter[missing]`` is 0, so a bucket outside ``_BUCKETS`` vanished
    from the table while still counting in ``len(rows)`` — the percentages
    stopped summing to 100 and a dead member printed ``0  0.0%``,
    indistinguishable from a shape that never occurred."""

    def test_an_unknown_bucket_is_printed_rather_than_dropped(self) -> None:
        rows = [{"bucket": "match"}] * 3 + [{"bucket": "recategorised"}]
        lines = sampler.summarise("europepmc", rows, 0)
        assert any("unclassified" in line and "recategorised" in line for line in lines), lines

    def test_the_printed_counts_account_for_every_row(self) -> None:
        """The property the table is supposed to have, stated directly."""
        rows = [{"bucket": "match"}] * 3 + [{"bucket": "drifted"}] * 2
        lines = sampler.summarise("europepmc", rows, 0)
        counted = sum(int(line.split()[1]) for line in lines[1:])
        assert counted == len(rows)

    def test_a_clean_run_prints_no_unclassified_row(self) -> None:
        """The negative control: the row above must come from the drift, not
        appear on every summary."""
        lines = sampler.summarise("europepmc", [{"bucket": "match"}] * 3, 0)
        assert not any("unclassified" in line for line in lines)


class TestACorruptJournalLineCostsOneRow:
    """``load_partial`` promises that a bad line costs one row rather than the
    file. Its guard caught ``JSONDecodeError`` only, so a line that parsed to a
    non-object reached ``row.get`` and killed the resume with an
    ``AttributeError`` naming neither file nor line — leaving deleting the
    journal as the only way out, which loses every good row."""

    def test_a_line_that_is_valid_json_but_not_an_object_is_skipped(self, tmp_path: Path) -> None:
        journal = tmp_path / "j.jsonl"
        journal.write_text(
            '{"source": "europepmc", "id": "a", "bucket": "match"}\nnull\n'
            '{"source": "europepmc", "id": "b", "bucket": "match"}\n'
        )
        rows = sampler.load_partial(journal)
        assert [row["id"] for row in rows] == ["a", "b"]

    def test_the_surviving_rows_still_drive_the_resume(self, tmp_path: Path) -> None:
        """The point of skipping rather than raising: the functions that read
        the journal must run, not just the loader."""
        journal = tmp_path / "j.jsonl"
        journal.write_text('3\n{"source": "europepmc", "id": "a", "bucket": "match"}\n[]\n')
        rows = sampler.load_partial(journal)
        assert sampler.already_seen(rows) == {"a"}
        populations = sampler.tally_previous(rows)
        assert [row["id"] for row in populations["europepmc"].rows] == ["a"]

    def test_a_truncated_final_line_still_costs_only_itself(self, tmp_path: Path) -> None:
        journal = tmp_path / "j.jsonl"
        journal.write_text('{"source": "europepmc", "id": "a", "bucket": "match"}\n{"source": "eu')
        assert [row["id"] for row in sampler.load_partial(journal)] == ["a"]

    def test_the_message_names_the_line(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A bad line in the *middle* of a journal is not a truncated write and
        means something else is wrong, so the report has to be locatable. The
        assertion is the file:line prefix rather than "skipping", which both
        branches emit and which would pass against either."""
        journal = tmp_path / "j.jsonl"
        journal.write_text('{"source": "e", "id": "a", "bucket": "match"}\nnull\n')
        capsys.readouterr()

        sampler.load_partial(journal)

        assert f"{journal}:2:" in capsys.readouterr().err


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
        body, measured, cause = sampler.fetch_pdf(
            client, "https://e/a.pdf", "PMC1", lambda url: None, cache
        )
        assert body == b"%PDF-1.7 cached"
        assert measured is True
        assert cause == ""
        assert client.seen == []

    def test_a_downloaded_pdf_is_kept_for_the_next_run(self, tmp_path: Path) -> None:
        from bmlib.fulltext.cache import FullTextCache

        cache = FullTextCache(cache_dir=tmp_path / "cache")
        client = _Client({"https://e/a.pdf": _Resp(200, b"%PDF-1.7 fresh")})
        body, measured, cause = sampler.fetch_pdf(
            client, "https://e/a.pdf", "PMC1", lambda url: None, cache
        )
        assert (body, measured, cause) == (b"%PDF-1.7 fresh", True, "")
        assert cache.get_pdf("PMC1") is not None

    def test_no_cache_still_downloads(self, tmp_path: Path) -> None:
        """The cache is an optimisation, not a precondition."""
        client = _Client({"https://e/a.pdf": _Resp(200, b"%PDF-1.7 fresh")})
        assert sampler.fetch_pdf(client, "https://e/a.pdf", "PMC1", lambda url: None, None) == (
            b"%PDF-1.7 fresh",
            True,
            "",
        )

    def test_a_failed_download_caches_nothing(self, tmp_path: Path) -> None:
        from bmlib.fulltext.cache import FullTextCache

        cache = FullTextCache(cache_dir=tmp_path / "cache")
        client = _Client({"https://e/a.pdf": _Resp(404)})
        assert sampler.fetch_pdf(client, "https://e/a.pdf", "PMC1", lambda url: None, cache) == (
            None,
            False,
            "http-404",
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


@pytest.mark.skipif(not _HAS_FITZ, reason="PyMuPDF not installed")
class TestTheRecordTitleIsCleanedBeforeItIsGroundTruth:
    """Europe PMC returns titles with their markup **escaped**, so a title
    reading `<i>MET</i> alterations` arrives as `&lt;i&gt;MET&lt;/i&gt;
    alterations`. Tokenised raw it becomes `lt i gt met lt i gt`, and a PDF
    whose metadata title is a *perfect* match gets labelled `unrelated`.

    That is the worst direction for this corpus to be wrong in: corroboration
    accepts such a title — the PDF really does print it — so the row would
    count as junk the rule failed to reject, and depress the measured
    junk-rejection rate with titles that were never junk. Two of the first ten
    rows of a live run were affected.
    """

    def test_escaped_markup_in_the_record_title_is_not_junk(self, tmp_path: Path) -> None:
        row = sampler.row_from_pdf(
            _make_pdf("Effects of aspirin on cardiovascular outcomes"),
            "europepmc",
            "PMC1",
            "Effects of &lt;i&gt;aspirin&lt;/i&gt; on cardiovascular outcomes",
            "a.pdf",
            tmp_path,
        )
        assert row is not None
        assert row["bucket"] == "match"

    def test_raw_markup_is_stripped_too(self, tmp_path: Path) -> None:
        """bioRxiv serves the unescaped form of the same thing."""
        row = sampler.row_from_pdf(
            _make_pdf("Effects of aspirin on cardiovascular outcomes"),
            "biorxiv",
            "10.1101/1",
            "Effects of <i>aspirin</i> on cardiovascular outcomes",
            "a.pdf",
            tmp_path,
        )
        assert row is not None
        assert row["bucket"] == "match"

    def test_the_stored_record_title_is_the_cleaned_one(self, tmp_path: Path) -> None:
        """The fixture is read by a human deciding what the backstop needs;
        an entity-mangled title there is a trap for that reader too."""
        row = sampler.row_from_pdf(
            _make_pdf("Effects of aspirin on cardiovascular outcomes"),
            "europepmc",
            "PMC1",
            "Effects of &lt;i&gt;aspirin&lt;/i&gt; on cardiovascular outcomes",
            "a.pdf",
            tmp_path,
        )
        assert row is not None
        assert row["record_title"] == "Effects of aspirin on cardiovascular outcomes"

    def test_an_ampersand_survives_as_itself(self, tmp_path: Path) -> None:
        """`&amp;` is an entity too, but `Cancer &amp; Metabolism` is a real
        title fragment — unescaping must not be mistaken for tag-stripping."""
        assert sampler.clean_record_title("Trials &amp; Tribulations") == "Trials & Tribulations"


class TestAnUnmeasuredAttemptStaysReachableAsTheWindowSlides:
    """The bioRxiv walk covers ``[today-30, today-49]``, recomputed from
    ``date.today()`` every run — so it slides a day per calendar day and after
    20 days shares nothing with the window that produced the journal.

    ``already_seen`` deliberately leaves an unmeasured attempt open to retry,
    but the walk could no longer *offer* it, so the entry fossilised: it kept
    counting against the population's unmeasured share forever, and the only
    escape was deleting the journal and losing every good row with it. The
    posting day on each attempt is what makes the retry reachable again.
    """

    def test_a_day_holding_an_unmeasured_attempt_is_revisited(self) -> None:
        entries = [
            {"unmeasured": True, "source": "biorxiv", "id": "a", "day": "2026-05-02"},
            {"unmeasured": True, "source": "biorxiv", "id": "b", "day": "2026-05-04"},
        ]
        assert sampler.days_to_revisit(entries, "biorxiv") == [
            date(2026, 5, 4),
            date(2026, 5, 2),
        ]

    def test_a_day_whose_attempt_succeeded_is_not_revisited(self) -> None:
        """Last outcome wins here as everywhere: an id that failed and was
        then collected owes nothing."""
        entries = [
            {"unmeasured": True, "source": "biorxiv", "id": "a", "day": "2026-05-02"},
            {"source": "biorxiv", "id": "a", "day": "2026-05-02", "bucket": "match"},
        ]
        assert sampler.days_to_revisit(entries, "biorxiv") == []

    def test_only_the_named_source_is_revisited(self) -> None:
        """medrxiv and biorxiv share the walk but not their journals' days."""
        entries = [{"unmeasured": True, "source": "medrxiv", "id": "a", "day": "2026-05-02"}]
        assert sampler.days_to_revisit(entries, "biorxiv") == []
        assert sampler.days_to_revisit(entries, "medrxiv") == [date(2026, 5, 2)]

    def test_europepmc_needs_no_day_and_gets_none(self) -> None:
        """Its walk restarts from cursor ``*`` and re-offers the same hits, so
        an entry with no day is not a gap — it is a source that never had the
        problem."""
        entries = [{"unmeasured": True, "source": "europepmc", "id": "PMC1"}]
        assert sampler.days_to_revisit(entries, "europepmc") == []

    def test_the_revisited_days_are_walked_in_addition_to_the_fresh_window(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Revisits must not be drawn from ``BIORXIV_DAYS_TO_WALK``: retrying
        old work is not allowed to cost the run its budget for new work. That
        separation is the whole reason the day is recorded per attempt instead
        of the window being pinned."""
        walked: list[object] = []

        def fetch(client: object, day: object, on_record: object, server: str) -> None:
            walked.append(day)

        monkeypatch.setattr(sampler, "fetch_biorxiv", fetch)
        context = sampler.RunContext(
            pace=lambda host: None, journal=tmp_path / "j.jsonl", seen=set(), workers=1
        )
        old = date(2020, 1, 5)

        sampler.sample_biorxiv_rows(object(), 10, context, revisit_days=[old])

        assert walked[0] == old, "the owed day is walked first"
        assert len(walked) == sampler.BIORXIV_DAYS_TO_WALK + 1
        assert old not in walked[1:], "the fresh window is unchanged by the revisit"

    def test_a_day_already_in_the_fresh_window_is_not_walked_twice(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        walked: list[object] = []
        monkeypatch.setattr(
            sampler,
            "fetch_biorxiv",
            lambda client, day, on_record, server: walked.append(day),
        )
        context = sampler.RunContext(
            pace=lambda host: None, journal=tmp_path / "j.jsonl", seen=set(), workers=1
        )
        inside = date.today() - timedelta(days=31)

        sampler.sample_biorxiv_rows(object(), 10, context, revisit_days=[inside])

        assert walked.count(inside) == 1
        assert len(walked) == sampler.BIORXIV_DAYS_TO_WALK


class TestARetryGivesUpWithoutForgetting:
    """The revisit set would otherwise grow monotonically: a day holding
    permanently dead URLs is re-fetched and re-downloaded on every run
    thereafter, forever.

    Retirement bounds that tail. What it must *not* do is make the failure
    disappear — a retired attempt is still a probe that could not be made, and
    dropping it from the count is the silent-loss failure this accounting
    exists to prevent.
    """

    def _entry(self, attempts: int) -> dict[str, object]:
        return {
            "unmeasured": True,
            "source": "biorxiv",
            "id": "a",
            "day": "2026-05-02",
            "attempts": attempts,
            "cause": "http-404",
        }

    def test_an_entry_with_retries_left_is_offered_again(self) -> None:
        entry = self._entry(sampler.MAX_UNMEASURED_ATTEMPTS - 1)
        assert sampler.is_retired(entry) is False
        assert sampler.already_seen([entry]) == set()
        assert sampler.days_to_revisit([entry], "biorxiv") == [date(2026, 5, 2)]

    def test_an_exhausted_entry_stops_being_offered(self) -> None:
        entry = self._entry(sampler.MAX_UNMEASURED_ATTEMPTS)
        assert sampler.is_retired(entry) is True
        assert sampler.already_seen([entry]) == {"a"}
        assert sampler.days_to_revisit([entry], "biorxiv") == []

    def test_an_exhausted_entry_is_still_counted_as_unmeasured(self) -> None:
        """The line between bounding the work and forgetting the failure. A
        retired attempt leaves the retry queue and stays in the denominator."""
        population = sampler.tally_previous([self._entry(sampler.MAX_UNMEASURED_ATTEMPTS)])
        assert population["biorxiv"] == sampler.Population([], 1, 1)

    def test_an_exhausted_entry_still_makes_a_thin_population_an_error(self) -> None:
        """The consequence that matters: retiring attempts must not be a way
        for an unreportable population to become reportable."""
        spent = self._entry(sampler.MAX_UNMEASURED_ATTEMPTS)
        entries = [{**spent, "id": f"x{i}"} for i in range(9)]
        population = sampler.tally_previous([*entries, {"source": "biorxiv", "id": "ok"}])
        lines = sampler.summarise(
            "biorxiv",
            population["biorxiv"].rows,
            population["biorxiv"].unmeasured,
            population["biorxiv"].persistent,
        )
        assert any("ERROR" in line for line in lines), lines

    def test_the_summary_names_how_many_were_retried_out(self) -> None:
        """ "We stopped trying" and "not tried yet" call for different actions,
        and only the first is a reason to go and look at the URLs."""
        lines = sampler.summarise("biorxiv", [{"bucket": "match"}] * 20, 3, 2)
        assert "2 of them retried out" in lines[0]

    def test_a_population_with_no_retired_attempts_says_nothing_about_them(self) -> None:
        """The negative control on the clause above."""
        lines = sampler.summarise("biorxiv", [{"bucket": "match"}] * 20, 3, 0)
        assert "retried out" not in lines[0]

    def test_the_attempt_counter_rises_across_runs(self, tmp_path: Path) -> None:
        """Each failure stamps the next attempt number, so a retry that keeps
        failing is visibly a retry rather than looking like a fresh fault."""
        # The loop below is sized from the constant, so an absurd value would
        # hang the suite rather than fail it. Bounded first: a retry budget
        # this large is not a configuration, it is a mistake.
        assert 1 < sampler.MAX_UNMEASURED_ATTEMPTS <= 10

        journal = tmp_path / "j.jsonl"
        candidate = sampler.Candidate(
            "biorxiv", "a", _RECORD_TITLE, "https://e/a.pdf", "2026-05-02"
        )
        client = _Client({"https://e/a.pdf": _Resp(404)})

        stamped = []
        for _ in range(sampler.MAX_UNMEASURED_ATTEMPTS):
            entries = sampler.load_partial(journal)
            context = sampler.RunContext(
                pace=lambda host: None,
                journal=journal,
                seen=sampler.already_seen(entries),
                workers=1,
                attempts=sampler.unmeasured_attempts(entries),
            )
            sampler.process_candidates(client, [candidate], context, tmp_path)
            stamped.append(sampler.load_partial(journal)[-1]["attempts"])

        assert stamped == [1, 2, 3]
        assert sampler.already_seen(sampler.load_partial(journal)) == {"a"}

    def test_the_marker_records_why_it_could_not_be_measured(self, tmp_path: Path) -> None:
        """A resume that cannot tell a dead network from a dead link cannot
        tell whether re-running will help."""
        journal = tmp_path / "j.jsonl"
        context = sampler.RunContext(pace=lambda host: None, journal=journal, seen=set(), workers=1)
        candidate = sampler.Candidate(
            "biorxiv", "a", _RECORD_TITLE, "https://e/a.pdf", "2026-05-02"
        )

        sampler.process_candidates(
            _Client({"https://e/a.pdf": _Resp(200, b"<!DOCTYPE html>")}),
            [candidate],
            context,
            tmp_path,
        )

        marker = sampler.load_partial(journal)[-1]
        assert marker["cause"] == "not-a-pdf"
        assert marker["day"] == "2026-05-02"


class TestATransientFailureIsNotRememberedForever:
    """The first live run lost 40 of 153 attempts to local DNS failures and
    read timeouts — a fault on this machine, not a property of the population.

    Treating those as settled would have carried that fault into the
    population's permanent record: at the 150-row target the unmeasured share
    would still have read 40/190 = 21%, over the threshold that makes a
    population unreportable, however many good rows followed it.
    """

    def test_an_unmeasured_attempt_is_open_to_a_retry(self) -> None:
        entries = [
            {"source": "europepmc", "id": "PMC1", "bucket": "match"},
            {"unmeasured": True, "source": "europepmc", "id": "PMC2"},
        ]
        assert sampler.already_seen(entries) == {"PMC1"}

    def test_a_retry_that_succeeds_stops_counting_as_unmeasured(self) -> None:
        """Both entries are real attempts on the same article, so counting
        both would charge the population for a failure that was retried away.
        """
        entries = [
            {"unmeasured": True, "source": "europepmc", "id": "PMC1"},
            {"source": "europepmc", "id": "PMC1", "bucket": "match"},
        ]
        population = sampler.tally_previous(entries)["europepmc"]
        rows, unmeasured = population.rows, population.unmeasured
        assert [row["id"] for row in rows] == ["PMC1"]
        assert unmeasured == 0

    def test_a_retry_that_fails_again_is_still_counted_once(self) -> None:
        entries = [
            {"unmeasured": True, "source": "europepmc", "id": "PMC1"},
            {"unmeasured": True, "source": "europepmc", "id": "PMC1"},
        ]
        assert sampler.tally_previous(entries)["europepmc"] == sampler.Population([], 1, 0)

    def test_a_failure_never_retried_still_counts(self) -> None:
        """The property the last-outcome rule must not cost: an id that only
        ever failed is exactly what the unmeasured share exists to report."""
        entries = [
            {"source": "europepmc", "id": "PMC1", "bucket": "match"},
            {"unmeasured": True, "source": "europepmc", "id": "PMC2"},
        ]
        population = sampler.tally_previous(entries)["europepmc"]
        rows, unmeasured = population.rows, population.unmeasured
        assert len(rows) == 1
        assert unmeasured == 1


class TestARetriedFailureIsNotCountedTwice:
    """The bug the live numbers exposed: Europe PMC's unmeasured count *rose*
    across a run that retried 25 of its failures successfully.

    ``main`` tallied the previous journal, then added the fresh rows on top —
    so an id that was unmeasured and had just succeeded appeared in both
    halves, once as a failure and once as a row. The population's own health
    metric was the thing being corrupted, which is the metric that decides
    whether the corpus may be reported at all.
    """

    def test_a_success_replaces_the_earlier_failure_in_the_tally(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        output = tmp_path / "corpus.json"
        journal = sampler._journal_path(output)
        sampler.append_row(journal, {"unmeasured": True, "source": "europepmc", "id": "PMC1"})

        def retry_it(client, target, context):
            # What a real walk does on a retry: the id is not in `seen`, so it
            # is fetched again, and this time it lands.
            assert "PMC1" not in context.seen
            sampler.append_row(
                context.journal, {"source": "europepmc", "id": "PMC1", "bucket": "match"}
            )
            return [], 0

        monkeypatch.setattr(sampler, "sample_europepmc_rows", retry_it)
        monkeypatch.setattr(sampler, "sample_biorxiv_rows", lambda *a, **k: ([], 0))
        monkeypatch.setattr(
            sys, "argv", ["s", "-o", str(output), "--target", "5", "--no-pdf-cache"]
        )
        printed: list[str] = []
        monkeypatch.setattr("builtins.print", lambda *a, **k: printed.append(" ".join(map(str, a))))
        sampler.main()

        text = "\n".join(printed)
        assert "1 rows (0 unmeasured" in text, text
        assert [row["id"] for row in json.loads(output.read_text())] == ["PMC1"]
