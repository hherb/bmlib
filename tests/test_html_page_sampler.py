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

"""Tests for ``scripts/sample_html_pages.py`` (#726), offline.

What is pinned: the element count is the number the Rust sink counts as
created (its figures on #723's shape, measured with the sink, are the
expectations here); an answer that is not a page is never counted as one; a
probe not made enters no denominator, and a stratum that lost too many reports
ERROR with no figures; and the committed corpus's summary is what its rows
compute to.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "sample_html_pages.py"
_spec = importlib.util.spec_from_file_location("bmlib_html_page_sampler", _PATH)
if _spec is None or _spec.loader is None:  # pragma: no cover - the script is in-tree
    raise ImportError(f"cannot load the sampler from {_PATH}")
sampler = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = sampler
if str(_PATH.parent) not in sys.path:  # the sampler imports `_sampling` from scripts/
    sys.path.insert(0, str(_PATH.parent))
_spec.loader.exec_module(sampler)

CORPUS = Path(__file__).resolve().parent / "data" / "html_page_shapes.json"
SAVED_CORPUS = CORPUS.with_name("html_saved_page_shapes.json")


def rebuilding(n: int) -> str:
    """#723's shape: *n* formatting elements left open in a block, then *n* blocks of text."""
    return "<div>" + "".join(f"<b id={k}>" for k in range(n)) + "</div>" + "<div>x</div>" * n


class _Resp:
    def __init__(
        self, status: int, text: str = "", content_type: str = "text/html", payload: Any = None
    ) -> None:
        self.status_code = status
        self.text = text
        self.headers = {"content-type": content_type}
        self.url = "https://example.org/final"
        self._payload = payload

    def json(self) -> Any:
        return self._payload


def _answers(*answers: Any) -> Any:
    queue = list(answers)

    def get(url: str) -> Any:
        answer = queue.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    return get


class TestTheElementCountIsRustsCreatedCount:
    """markupever's tree holds every element html5ever created, as Rust's sink counts them."""

    @pytest.mark.parametrize(
        ("page", "created"),
        [
            ("<p>hello", 4),  # html, head, body, p
            (rebuilding(40), 1684),  # Rust's sink, measured: 1,684 (Rust refuses it since #656)
            (rebuilding(60), 3724),  # Rust's sink, measured: 3,724 (Rust refuses it)
        ],
        ids=["plain", "rebuilding-40", "rebuilding-60"],
    )
    def test_the_counts_match_the_sinks(self, page: str, created: int) -> None:
        row = sampler.measure_text(page)
        assert row["elements"] == created
        assert row["python"] == "ok"

    def test_lengths_are_code_points_without_the_leading_mark(self) -> None:
        row = sampler.measure_text("﻿<p>é</p>")
        assert (row["code_points"], row["lt"]) == (8, 2)

    def test_a_refused_page_is_reported_and_not_counted(self) -> None:
        row = sampler.measure_text("<p>\ud800")
        assert row == {
            "code_points": 4,
            "lt": 1,
            "data_uri_chars": 0,
            "python": "ValueError",
            "elements": None,
            "text_chars": None,
        }

    def test_a_page_long_with_data_uris_is_converted(self) -> None:
        image = "data:image/png;base64," + "A" * sampler.LENGTH_CAP
        row = sampler.measure_text(f'<p><img src="{image}">text</p>')
        assert (row["python"], row["data_uri_chars"]) == ("ok", len(image))
        assert (row["elements"], row["text_chars"]) == (5, 4)  # html head body p img; "text"
        assert row["seconds"] > 0
        assert "uncapped" not in row

    def test_a_page_past_the_tag_cap_is_measured_beneath_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from bmlib.fulltext.canonical import html

        monkeypatch.setattr(html, "HTML_MAX_TAGS", 1)
        row = sampler.measure_text("<p>a<p>b")
        assert (row["python"], row["uncapped"]) == ("HtmlPageTooLargeError", "ok")
        assert (row["elements"], row["text_chars"]) == (5, 4)  # html head body p p; "a\n\nb"

    def test_a_page_refused_beneath_the_tag_cap_is_reported(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from bmlib.fulltext.canonical import html

        def refuse(page: str, max_bytes: int, seconds: float) -> None:
            raise html.HtmlParseMemoryError(max_bytes + 1, len(page), max_bytes)

        monkeypatch.setattr(html, "HTML_MAX_TAGS", 1)
        monkeypatch.setattr(html, "_convert_in_child", refuse)
        assert sampler.measure_text("<p>a<p>b") == {
            "code_points": 8,
            "lt": 2,
            "data_uri_chars": 0,
            "python": "HtmlPageTooLargeError",
            "uncapped": "HtmlParseMemoryError",
            "elements": None,
            "text_chars": None,
        }

    @pytest.mark.parametrize("capped", [False, True], ids=["converting", "beneath-the-cap"])
    def test_a_failed_child_is_not_a_refusal(
        self, capped: bool, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from bmlib.fulltext.canonical import html

        def fail(page: str, max_bytes: int, seconds: float) -> None:
            raise RuntimeError("the HTML parse process failed (killed by SIGKILL)")

        if capped:
            monkeypatch.setattr(html, "HTML_MAX_TAGS", 1)
        monkeypatch.setattr(html, "_convert_in_child", fail)
        with pytest.raises(RuntimeError, match="killed by SIGKILL"):
            sampler.measure_text("<p>a<p>b")  # so measure_file's child fails: unmeasured

    def test_a_data_uri_is_counted_whole(self) -> None:
        page = (
            '<img src="data:image/svg+xml;charset=utf-8;base64,PHN2Zz4=">'
            "<style>@font-face{src:url(data:font/woff2;base64,d09GMgAB)}</style>"
            '<a href="data:,plain">x</a>'  # not base64, so not a saved resource
        )
        row = sampler.measure_text(page)
        assert row["data_uri_chars"] == len(
            "data:image/svg+xml;charset=utf-8;base64,PHN2Zz4="
        ) + len("data:font/woff2;base64,d09GMgAB")

    def test_a_page_is_measured_in_its_own_interpreter(self, tmp_path: Path) -> None:
        page = tmp_path / "p.html"
        page.write_text(rebuilding(40), encoding="utf-8")
        row = sampler.measure_file(page)
        assert row["elements"] == 1684
        assert row["child_peak_rss"] > 1 << 20  # bmlib's parse child ran and was read


class TestAnAnswerIsSortedNotCounted:
    TARGET = {"stratum": "oa-2024", "id": "1", "url": "https://doi.org/10.1/x"}

    @pytest.fixture(autouse=True)
    def _no_sleep(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sampler, "_sleep_for", lambda seconds: None)

    def test_an_html_page_is_saved(self, tmp_path: Path) -> None:
        row = sampler.fetch(_answers(_Resp(200, "<p>x")), self.TARGET, tmp_path)
        assert row["outcome"] == "page"
        assert (tmp_path / row["file"]).read_text(encoding="utf-8") == "<p>x"

    @pytest.mark.parametrize(
        "resp",
        [_Resp(403), _Resp(404), _Resp(202), _Resp(200, "%PDF", "application/pdf")],
        ids=["403", "404", "202", "pdf"],
    )
    def test_the_servers_own_answer_is_not_served(self, resp: _Resp, tmp_path: Path) -> None:
        row = sampler.fetch(_answers(resp), self.TARGET, tmp_path)
        assert row["outcome"] == "not_served"
        assert not list(tmp_path.iterdir())

    @pytest.mark.parametrize(
        "answers",
        [
            [_Resp(429)] * 3,
            [_Resp(503)] * 3,
            [_Resp(500)],
            [OSError("connection reset")],
        ],
        ids=["429", "503", "500", "transport"],
    )
    def test_a_probe_not_made_is_unmeasured(self, answers: list[Any], tmp_path: Path) -> None:
        assert sampler.fetch(_answers(*answers), self.TARGET, tmp_path)["outcome"] == "unmeasured"

    def test_a_throttled_probe_is_retried(self, tmp_path: Path) -> None:
        row = sampler.fetch(_answers(_Resp(429), _Resp(200, "<p>x")), self.TARGET, tmp_path)
        assert row["outcome"] == "page"


def _page(stratum: str, **numbers: Any) -> dict[str, Any]:
    base = {"code_points": 1000, "lt": 50, "elements": 20, "python": "ok", "text_chars": 3000}
    return {"stratum": stratum, "outcome": "page", "child_peak_rss": 30 << 20} | base | numbers


class TestTheSummaryReportsOnlyWhatWasMeasured:
    def test_a_stratum_past_the_threshold_is_error_without_figures(self) -> None:
        rows = [_page("a"), _page("a"), _page("a"), {"stratum": "a", "outcome": "unmeasured"}]
        summary = sampler.summarise(rows, {})
        assert summary["strata"]["a"] == {
            "targets": 4,
            "unmeasured": 1,
            "not_served": 0,
            "status": "ERROR",
        }
        assert summary["all_pages"] == {
            "pages": 0,
            "python_refused": 0,
            "over_length_cap": 0,
            "over_lt_cap": 0,
        }

    def test_at_the_threshold_the_stratum_reports(self) -> None:
        rows = [_page("a")] * 3 + [{"stratum": "a", "outcome": "not_served"}]
        rows += [{"stratum": "a", "outcome": "unmeasured"}]  # 1 of 5 = 20%, not past it
        entry = sampler.summarise(rows, {})["strata"]["a"]
        assert (entry["status"], entry["pages"], entry["not_served"]) == ("ok", 3, 1)

    def test_a_failed_draw_is_error(self) -> None:
        summary = sampler.summarise([], {"legacy-x": "ReadTimeout: timed out"})
        assert summary["strata"]["legacy-x"]["status"] == "ERROR"

    def test_the_figures(self) -> None:
        rows = [
            _page("a", elements=10, code_points=1000, lt=40),
            _page("a", elements=30, code_points=1000, lt=20, text_chars=10),
            _page("a", elements=None, python="HtmlPageTooLargeError", code_points=3_000_000),
        ]
        summary = sampler.summarise(rows, {})
        assert summary["all_pages"]["pages"] == 3
        assert summary["all_pages"]["python_refused"] == 1
        assert summary["all_pages"]["over_length_cap"] == 1
        assert summary["all_pages"]["elements_per_code_point"] == [0.01, 0.03, 0.03, 0.03, 0.03]
        assert summary["all_pages"]["most_elements_beyond_lt"] == 10
        assert summary["article_pages"]["pages"] == 2  # the 10-character page is not an article


class TestSavedPages:
    """A saved-page run measures each DOI target's saved file, and only those."""

    TARGETS = [
        {"stratum": "oa-2024", "id": "1", "url": "https://doi.org/10.1/a", "outcome": "page"},
        {"stratum": "pubmed-1995", "id": "2", "url": "https://doi.org/10.1/b"},
        {"stratum": "legacy-bmj.com", "id": "2003", "url": "https://web.archive.org/x"},
    ]

    def test_a_target_saved_is_measured_and_one_not_is_unmeasured(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "oa-2024-1.html").write_text("<p>x", encoding="utf-8")
        (tmp_path / "legacy-bmj.com-2003.html").write_text("<p>x", encoding="utf-8")
        monkeypatch.setattr(sampler, "measure_file", lambda path: {"measured": path.name})
        rows = sampler.saved_rows(tmp_path, self.TARGETS)
        assert rows == [
            {
                "stratum": "oa-2024",
                "id": "1",
                "url": "https://doi.org/10.1/a",
                "outcome": "page",
                "file": "oa-2024-1.html",
                "measured": "oa-2024-1.html",
            },
            {
                "stratum": "pubmed-1995",
                "id": "2",
                "url": "https://doi.org/10.1/b",
                "outcome": "unmeasured",
                "error": "no saved page",
            },
        ]  # the archived stratum is not a saved-page target

    def test_the_figures_say_what_a_lifted_cap_would_still_refuse(self) -> None:
        rows = [
            _page("a", data_uri_chars=0, seconds=0.1),
            _page(
                "a",
                python="HtmlPageTooLargeError",
                uncapped="ok",
                code_points=3_000_000,
                data_uri_chars=2_400_000,
                seconds=0.9,
            ),
            _page(
                "a",
                python="HtmlPageTooLargeError",
                uncapped="HtmlParseMemoryError",
                elements=None,
                text_chars=None,
                code_points=4_000_000,
                data_uri_chars=0,
            ),
        ]
        summary = sampler.summarise(rows, {})
        figures = summary["all_pages"]
        assert (figures["python_refused"], figures["refused_uncapped"]) == (2, 1)
        assert figures["data_uri_share"] == [0.0, 0.0, 0.8, 0.8, 0.8]
        assert figures["seconds"] == [0.1, 0.9, 0.9, 0.9, 0.9]
        # The page refused beneath the cap has no text to judge, and is not dropped from these.
        articles = summary["article_pages"]
        assert (articles["pages"], articles["refused_uncapped"]) == (3, 1)

    def test_a_page_whose_measurement_failed_is_unmeasured(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import subprocess

        (tmp_path / "oa-2024-1.html").write_text("<p>x", encoding="utf-8")

        def killed(path: Path) -> dict[str, Any]:
            raise subprocess.CalledProcessError(-9, ["python"], output="", stderr="")

        monkeypatch.setattr(sampler, "measure_file", killed)
        [row, _] = sampler.saved_rows(tmp_path, self.TARGETS)
        assert (row["outcome"], row["error"]) == ("unmeasured", "exit -9: ")

    @pytest.mark.parametrize(
        "argv",
        [
            ["--saved-how", "x"],  # the default --out is the fetched corpus
            ["--out", "{tmp}/saved.json"],  # no --saved-how
            ["--saved-how", "x", "--targets", "{tmp}/t.json", "--out", "{tmp}/./t.json"],
            ["--saved-how", "x", "--out", "{tmp}/fetched.json"],  # FETCHED_CORPUS, absolute
        ],
        ids=["default-out", "no-how", "out-is-targets", "out-is-fetched-absolute"],
    )
    def test_a_saved_run_needs_its_own_out_and_how(
        self, argv: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # In tmp_path, with a stand-in for the committed corpus: a guard that lets one through
        # must not overwrite the real file.
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sampler, "FETCHED_CORPUS", Path("fetched.json"))
        monkeypatch.setattr(sampler, "measure_file", lambda path: pytest.fail("measured"))
        (tmp_path / "fetched.json").write_text('{"rows": []}', encoding="utf-8")
        (tmp_path / "t.json").write_text('{"rows": []}', encoding="utf-8")
        argv = [a.format(tmp=tmp_path) for a in argv]
        with pytest.raises(SystemExit):
            sampler.main(["--saved-dir", str(tmp_path), *argv])
        assert (tmp_path / "fetched.json").read_text(encoding="utf-8") == '{"rows": []}'

    def test_a_saved_run_writes_its_corpus(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        saved, targets, out = tmp_path / "saved", tmp_path / "t.json", tmp_path / "out.json"
        saved.mkdir()
        (saved / "oa-2024-1.html").write_text("<p>x", encoding="utf-8")
        targets.write_text(json.dumps({"rows": self.TARGETS[:1]}), encoding="utf-8")
        measured = _page("oa-2024", data_uri_chars=0, seconds=0.1)
        numbers = {k: v for k, v in measured.items() if k not in ("stratum", "outcome")}
        monkeypatch.setattr(sampler, "measure_file", lambda path: numbers)
        argv = ["--saved-dir", str(saved), "--saved-how", "x", "--targets", str(targets)]
        assert sampler.main([*argv, "--out", str(out)]) == 0
        corpus = json.loads(out.read_text(encoding="utf-8"))
        assert corpus["how"] == (
            "saved: x; then uv run python scripts/sample_html_pages.py --saved-dir DIR "
            f"--targets {targets}"
        )
        row = {k: v for k, v in self.TARGETS[0].items() if k != "outcome"}
        assert corpus["rows"] == [row | {"outcome": "page", "file": "oa-2024-1.html"} | numbers]
        assert corpus["summary"] == json.loads(json.dumps(sampler.summarise(corpus["rows"], {})))
        assert corpus["summary"]["all_pages"]["pages"] == 1


def test_the_samplers_tag_cap_is_bmlibs() -> None:
    """Restated, not imported (a sampler does not import what it measures), and held equal."""
    from bmlib.fulltext.canonical.html import HTML_MAX_TAGS

    assert sampler.LT_CAP == HTML_MAX_TAGS


class TestTheDraws:
    def test_pubmed_draws_dois(self) -> None:
        get = _answers(
            _Resp(200, payload={"esearchresult": {"count": "5"}}),
            _Resp(200, payload={"esearchresult": {"idlist": ["11"]}}),
            _Resp(200, payload={"esearchresult": {"idlist": ["11"]}}),  # a repeat costs a draw
            _Resp(200, payload={"esearchresult": {"idlist": ["22"]}}),
            _Resp(
                200,
                payload={
                    "result": {
                        "11": {"articleids": [{"idtype": "doi", "value": "10.1/a"}]},
                        "22": {"articleids": [{"idtype": "pubmed", "value": "22"}]},
                    }
                },
            ),
        )
        import random

        targets = sampler.draw_pubmed(get, random.Random(0), 2024, free_only=True, n=2)
        assert targets == [{"stratum": "oa-2024", "id": "11", "url": "https://doi.org/10.1/a"}]

    def test_wayback_asks_for_the_bytes_as_captured(self) -> None:
        get = _answers(
            _Resp(200, payload=[["urlkey", "timestamp", "original"], ["k", "2003", "http://x/a"]])
        )
        import random

        targets = sampler.draw_wayback(get, random.Random(0), "x/cgi/", 5)
        assert targets == [
            {
                "stratum": "legacy-x",
                "id": "2003",
                "url": "https://web.archive.org/web/2003id_/http://x/a",
            }
        ]


@pytest.mark.parametrize("path", [CORPUS, SAVED_CORPUS], ids=["fetched", "saved"])
def test_the_committed_summary_is_what_its_rows_compute(path: Path) -> None:
    corpus = json.loads(path.read_text(encoding="utf-8"))
    failed = {
        name: entry["draw_error"]
        for name, entry in corpus["summary"]["strata"].items()
        if "draw_error" in entry
    }
    assert json.loads(json.dumps(sampler.summarise(corpus["rows"], failed))) == corpus["summary"]
