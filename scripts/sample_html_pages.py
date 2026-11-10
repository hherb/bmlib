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

"""Measure real article pages against ``canonical_from_html``'s limits (#726).

The HTML converter refuses a page by three limits: the Rust port's element
budget (``max_elements(tags)``, ``ELEMENTS_PER_TAG`` per ``<`` plus
``HTML_ELEMENT_ALLOWANCE``, #723/#726/#656), the ``<`` cap ``HTML_MAX_TAGS``
(#656, which replaced #654's length cap of 2,000,000 code points, still
reported here as ``over_length_cap``), and Python's ``HTML_PARSE_MAX_BYTES``
held to the parse child's memory (#725). For each page this reports the
numbers those limits are read against:

* ``code_points`` (a leading byte-order mark dropped) and ``lt``, its ``<``s;
* ``elements``, the elements in markupever's tree. That is the number the Rust
  sink counts as *created*: the two were compared on every page of the first
  two draws (187 of 187 equal) and on #723's own shape (1,684 and 3,724), so
  this script does not need the Rust port. It does not import ``max_elements``
  either: a sampler does not import the judgement it measures;
* ``python``, what ``canonical_from_html`` answered (``ok`` or the refusal's
  type, a ``ValueError``; any other error, a missing extra or a child that
  failed or was killed, makes the page ``unmeasured``), its ``text_chars``,
  ``seconds`` (the call's wall time, the child's start included), and
  ``child_peak_rss``, the most resident memory any child of the one process
  that converted the page held, which is bmlib's parse child
  (``getrusage(RUSAGE_CHILDREN)``; bytes on macOS, kilobytes on Linux,
  converted to bytes here);
* ``data_uri_chars``, the code points in base64 ``data:`` URIs (#656: a saved
  page carries its images and fonts that way, and they hold no ``<``);
* ``uncapped``, for a page refused by ``HTML_MAX_TAGS``: what the parse
  beneath the cap answered (``ok`` or the refusal's type, as for ``python``),
  under the child's own memory and time limits. That is the parse a lifted cap
  would run, and its text, elements, time and peak are the row's. (#656
  weighed #654's length cap by ``over_length_cap`` on pages that converted; no
  committed row is over the ``<`` cap, so none carries ``uncapped``.)

**The population is pages served to a plain HTTP client**, which is what an
app fetching a ``web_url`` gets. Three strata: a PubMed record's DOI landing
page by publication year (``pubmed-<year>``), the same for records PubMed
marks ``free full text`` (``oa-<year>``), and the original bytes of archived
1998-2007 full-text pages from the Wayback Machine (``legacy-<host>``), the
``<font>``-era markup #726 worried about. PMC's own article pages are not a
stratum: they served a reCAPTCHA page to 52 of 56 requests in the first draw.

An answer that is not a page is sorted, never counted as a page:

* ``not_served``: the server's own answer to such a client (a 403, a 404, a
  202, a 200 that is not HTML). A finding about the population, so it stays
  in the stratum's denominator and is reported, apart from the pages.
* ``unmeasured``: a probe that could not be made (a transport error, a 5xx,
  a 429/503 still throttled after retries). It enters no denominator; past
  ``UNMEASURED_SHARE_ERROR_THRESHOLD`` of a stratum's targets the stratum
  reports ERROR with no figures, is written to ``*.unreportable.json``, and
  the exit code is non-zero. A stratum whose draw failed is ERROR too.

A page the converter accepted is not thereby an article: interstitials and
challenge pages are pages too. ``text_chars >= ARTICLE_TEXT_CHARS`` marks the
article-like ones, and the summary gives both; a page the converter refused,
which has no text to judge, is counted with the articles.

Run (the pages are cached in ``--pages-dir`` and never committed)::

    uv run python scripts/sample_html_pages.py --pages-dir /tmp/html-pages \\
        --out tests/data/html_page_shapes.json

**Saved pages** (#656) are measured from a directory of pages a browser saved
with their resources inline, one per DOI target of a fetched corpus, named as
``fetch`` names a page (``<stratum>-<id>.html``). The saving is done outside
this script, by SingleFile (the format Zotero's snapshots use), and
``--saved-how`` records how; a target with no saved file is a probe not made.
A browser's save does not say what the server answered, so no saved page is
``not_served``, and an error or challenge page falls below
``ARTICLE_TEXT_CHARS``::

    uv run python scripts/sample_html_pages.py --saved-dir /tmp/saved \\
        --saved-how "single-file-cli 2.19.0, Chrome 157, defaults" \\
        --out tests/data/html_saved_page_shapes.json

The draw is seeded (``--seed``), but the population moves (PubMed grows, a
publisher changes its page), so a re-run re-measures rather than reproduces.
"""

from __future__ import annotations

import argparse
import json
import platform
import random
import re
import resource
import subprocess
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path
from time import perf_counter
from typing import Any

from _sampling import (
    MAX_PROBE_ATTEMPTS,
    UNMEASURED_SHARE_ERROR_THRESHOLD,
    _make_pacer,
    _sleep_for,
    _throttle_delay,
)

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
CDX = "https://web.archive.org/cdx/search/cdx"
YEARS = (1995, 2005, 2012, 2018, 2024)
OA_YEARS = (2005, 2012, 2018, 2024)
#: HighWire-era full-text URL prefixes, 1998-2007, as the Wayback Machine holds them.
LEGACY_PREFIXES = (
    "bmj.com/cgi/content/full/",
    "content.nejm.org/cgi/content/full/",
    "jama.ama-assn.org/cgi/content/full/",
    "www.pnas.org/cgi/content/full/",
    "www.jcb.org/cgi/content/full/",
    "circ.ahajournals.org/cgi/content/full/",
    "www.biomedcentral.com/1471-2458/",
    "www.thelancet.com/journals/lancet/article/",
)
#: Records PubMed's esearch can page to; a draw is uniform within them.
ESEARCH_MAX_START = 9999
#: Canonical-text characters from which a page reads as an article rather than
#: an interstitial or a challenge page. A reporting split, not a rule bmlib uses.
ARTICLE_TEXT_CHARS = 2000
#: #654's length cap and the ``<`` cap #656 put in its place, each reported as
#: how many pages exceed it (restated, not imported: the sampler measures them).
LENGTH_CAP = 2_000_000
LT_CAP = LENGTH_CAP // 3
THROTTLED = (429, 503)
#: A base64 ``data:`` URI, as a saved page carries an image or a font.
DATA_URI = re.compile(r"data:[\w/+.-]*(?:;[\w=-]+)*;base64,[A-Za-z0-9+/=]*")
#: The fetched corpus whose DOI targets a saved-page run measures.
FETCHED_CORPUS = Path("tests/data/html_page_shapes.json")
USER_AGENT = "bmlib HTML page sampler (issue 726; +https://github.com/hherb/bmlib)"


def draw_pubmed(
    get: Callable[[str], Any], rng: random.Random, year: int, *, free_only: bool, n: int
) -> list[dict[str, str]]:
    """Draw *n* PubMed journal articles of *year*, one random record per search.

    Returns a target per record with a DOI: ``{"stratum", "id", "url"}``, the
    URL being its ``doi.org`` link. Raises whatever the searches raise; the
    caller marks the stratum ERROR.
    """
    term = f"{year}[pdat] AND journal article[pt]" + (
        " AND free full text[sb]" if free_only else ""
    )
    found = get(f"{EUTILS}/esearch.fcgi?db=pubmed&retmode=json&retmax=0&term={term}").json()
    count = int(found["esearchresult"]["count"])
    pmids: list[str] = []
    for _ in range(n * 3):  # a repeat or an empty answer costs a draw, not the loop
        if len(pmids) == n or count == 0:
            break
        start = rng.randrange(min(count, ESEARCH_MAX_START))
        page = get(
            f"{EUTILS}/esearch.fcgi?db=pubmed&retmode=json&retmax=1&retstart={start}&term={term}"
        ).json()
        pmids += [p for p in page["esearchresult"]["idlist"] if p not in pmids]
    if not pmids:
        return []
    summary = get(f"{EUTILS}/esummary.fcgi?db=pubmed&retmode=json&id={','.join(pmids)}").json()
    stratum = f"{'oa' if free_only else 'pubmed'}-{year}"
    targets = []
    for pmid in pmids:
        ids = {a["idtype"]: a["value"] for a in summary["result"][pmid].get("articleids", [])}
        if ids.get("doi"):
            targets.append({"stratum": stratum, "id": pmid, "url": f"https://doi.org/{ids['doi']}"})
    return targets


def draw_wayback(
    get: Callable[[str], Any], rng: random.Random, prefix: str, n: int
) -> list[dict[str, str]]:
    """Draw *n* archived 1998-2007 HTML captures under *prefix*, as raw-bytes URLs.

    The ``id_`` flag asks the Wayback Machine for the page as captured, without
    its own toolbar and rewritten links.
    """
    query = (
        f"{CDX}?url={prefix}&matchType=prefix&from=1998&to=2007&filter=statuscode:200"
        "&filter=mimetype:text/html&collapse=urlkey&limit=500&output=json"
    )
    captures = get(query).json()[1:]  # row 0 is the header
    picked = rng.sample(captures, min(n, len(captures)))
    stratum = f"legacy-{prefix.split('/')[0]}"
    return [
        {"stratum": stratum, "id": c[1], "url": f"https://web.archive.org/web/{c[1]}id_/{c[2]}"}
        for c in picked
    ]


def fetch(get: Callable[[str], Any], target: dict[str, str], pages_dir: Path) -> dict[str, Any]:
    """Fetch one target and sort the answer: ``page``, ``not_served`` or ``unmeasured``.

    A page is saved to *pages_dir* and named in the row's ``file``.
    """
    row: dict[str, Any] = dict(target)
    for attempt in range(1, MAX_PROBE_ATTEMPTS + 1):
        try:
            resp = get(target["url"])
        except Exception as exc:  # noqa: BLE001 - any transport failure is a probe not made
            return row | {"outcome": "unmeasured", "error": f"{type(exc).__name__}: {exc}"}
        if resp.status_code in THROTTLED and attempt < MAX_PROBE_ATTEMPTS:
            _sleep_for(_throttle_delay(resp, attempt))
            continue
        break
    content_type = resp.headers.get("content-type", "")
    row |= {"status": resp.status_code, "final_url": str(resp.url), "content_type": content_type}
    if resp.status_code in THROTTLED or resp.status_code >= 500:
        return row | {"outcome": "unmeasured"}
    if resp.status_code != 200 or "html" not in content_type.lower():
        return row | {"outcome": "not_served"}
    name = f"{target['stratum']}-{target['id']}.html".replace("/", "_")
    (pages_dir / name).write_text(resp.text, encoding="utf-8")
    return row | {"outcome": "page", "file": name}


def measure_text(text: str) -> dict[str, Any]:
    """The numbers one page is read against, in this process.

    Converted first with ``canonical_from_html``, whose limits bound the
    memory; the tree is counted only for a page it, or for a page refused by
    its ``<`` cap the parse beneath that cap, accepted.
    """
    from bmlib.fulltext.canonical import HtmlPageTooLargeError, canonical_from_html

    # The parse a lifted `<` cap would run, with the limits it would run under.
    from bmlib.fulltext.canonical.html import (
        _CHILD_SECONDS,
        HTML_PARSE_MAX_BYTES,
        _convert_in_child,
    )

    page = text.removeprefix("﻿")
    row: dict[str, Any] = {
        "code_points": len(page),
        "lt": page.count("<"),
        "data_uri_chars": sum(len(m) for m in DATA_URI.findall(page)),
    }
    started = perf_counter()
    try:
        canonical = canonical_from_html(text)
        row["python"] = "ok"
    except HtmlPageTooLargeError:
        row["python"] = "HtmlPageTooLargeError"
        try:
            canonical = _convert_in_child(text, HTML_PARSE_MAX_BYTES, _CHILD_SECONDS)
        except ValueError as exc:  # a refusal is the measurement; anything else is not
            return row | {"uncapped": type(exc).__name__, "elements": None, "text_chars": None}
        row["uncapped"] = "ok"
    except ValueError as exc:  # a refusal is the measurement; a failed child is not
        return row | {"python": type(exc).__name__, "elements": None, "text_chars": None}
    row["seconds"] = round(perf_counter() - started, 3)
    import markupever

    tree = markupever.parse(page, markupever.HtmlOptions())
    elements = sum(1 for n in tree.root().descendants() if isinstance(n, markupever.dom.Element))
    return row | {"elements": elements, "text_chars": len(canonical.text)}


def _peak_child_rss() -> int:
    """The most resident memory any finished child of this process held, in bytes."""
    peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return peak if sys.platform == "darwin" else peak * 1024


def measure_file(path: Path) -> dict[str, Any]:
    """Measure one page in a fresh interpreter, so its child's peak is its own."""
    done = subprocess.run(
        [sys.executable, __file__, "--measure-one", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(done.stdout)


def _quantiles(values: list[float]) -> list[float]:
    """Minimum, median, 90th, 99th percentile and maximum (nearest rank)."""
    ordered = sorted(values)
    last = len(ordered) - 1
    return [ordered[min(last, int(p * last + 0.5))] for p in (0, 0.5, 0.9, 0.99, 1)]


def _figures(pages: list[dict[str, Any]]) -> dict[str, Any]:
    """The figures over *pages*, every one measured."""
    parsed = [p for p in pages if p["elements"] is not None]
    figures: dict[str, Any] = {
        "pages": len(pages),
        "python_refused": sum(p["python"] != "ok" for p in pages),
        "over_length_cap": sum(p["code_points"] > LENGTH_CAP for p in pages),
        "over_lt_cap": sum(p["lt"] > LT_CAP for p in pages),
    }
    if parsed:
        figures |= {
            "code_points": _quantiles([p["code_points"] for p in parsed]),
            "lt": _quantiles([p["lt"] for p in parsed]),
            "elements_per_code_point": [
                round(x, 4) for x in _quantiles([p["elements"] / p["code_points"] for p in parsed])
            ],
            "most_elements_beyond_lt": max(p["elements"] - p["lt"] for p in parsed),
            "child_peak_rss_mb": max(p["child_peak_rss"] for p in parsed) / 2**20,
        }
    # Figures over fields a row carries since #656; a corpus measured before has none.
    if pages and all("data_uri_chars" in p for p in pages):
        figures["refused_uncapped"] = sum(
            p["python"] != "ok" and p.get("uncapped") != "ok" for p in pages
        )
        figures["data_uri_share"] = [
            round(x, 4) for x in _quantiles([p["data_uri_chars"] / p["code_points"] for p in pages])
        ]
    if parsed and all("seconds" in p for p in parsed):
        figures["seconds"] = _quantiles([p["seconds"] for p in parsed])
    return figures


def summarise(rows: list[dict[str, Any]], failed_draws: dict[str, str]) -> dict[str, Any]:
    """Per stratum, and over every reportable stratum: the outcomes and the figures."""
    strata: dict[str, Any] = {
        name: {"status": "ERROR", "draw_error": e} for name, e in failed_draws.items()
    }
    reportable: list[dict[str, Any]] = []
    for name in sorted({r["stratum"] for r in rows}):
        mine = [r for r in rows if r["stratum"] == name]
        unmeasured = sum(r["outcome"] == "unmeasured" for r in mine)
        entry: dict[str, Any] = {
            "targets": len(mine),
            "unmeasured": unmeasured,
            "not_served": sum(r["outcome"] == "not_served" for r in mine),
        }
        if unmeasured / len(mine) > UNMEASURED_SHARE_ERROR_THRESHOLD:
            strata[name] = entry | {"status": "ERROR"}
            continue
        pages = [r for r in mine if r["outcome"] == "page"]
        reportable += pages
        strata[name] = entry | {"status": "ok"} | _figures(pages)
    # A refused page has no text to judge it by, so it is kept with the articles, not dropped.
    articles = [
        p for p in reportable if p["text_chars"] is None or p["text_chars"] >= ARTICLE_TEXT_CHARS
    ]
    return {
        "strata": strata,
        "all_pages": _figures(reportable),
        "article_pages": _figures(articles),
    }


def main(argv: list[str] | None = None) -> int:
    """Draw, fetch, measure and write the corpus; non-zero if any stratum is ERROR."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--pages-dir", type=Path)
    parser.add_argument("--saved-dir", type=Path, help="measure saved pages instead (#656)")
    parser.add_argument("--saved-how", help="how the saved pages were saved, recorded verbatim")
    parser.add_argument("--targets", type=Path, default=FETCHED_CORPUS)
    parser.add_argument("--out", type=Path, default=FETCHED_CORPUS)
    parser.add_argument("--per-stratum", type=int, default=20)
    parser.add_argument("--seed", type=int, default=726)
    parser.add_argument("--measure-one", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.measure_one:  # the per-page child of measure_file
        row = measure_text(args.measure_one.read_text(encoding="utf-8"))
        print(json.dumps(row | {"child_peak_rss": _peak_child_rss()}))
        return 0
    if args.saved_dir is not None:
        if not args.saved_how or args.out.resolve() in {
            FETCHED_CORPUS.resolve(),
            args.targets.resolve(),
        }:
            parser.error("--saved-dir needs --saved-how and an --out of its own")
        return measure_saved(args.saved_dir, args.targets, args.out, args.saved_how)
    if args.pages_dir is None:
        parser.error("--pages-dir is required")
    import httpx

    args.pages_dir.mkdir(parents=True, exist_ok=True)
    client = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=90.0, follow_redirects=True)
    pace = _make_pacer(1.0, overrides={"web.archive.org": 3.0, "eutils.ncbi.nlm.nih.gov": 0.4})

    def get(url: str) -> Any:
        pace(url)
        return client.get(url)

    rng = random.Random(args.seed)
    draws: list[tuple[str, Callable[[], list[dict[str, str]]]]] = [
        (f"pubmed-{y}", lambda y=y: draw_pubmed(get, rng, y, free_only=False, n=args.per_stratum))
        for y in YEARS
    ]
    draws += [
        (f"oa-{y}", lambda y=y: draw_pubmed(get, rng, y, free_only=True, n=args.per_stratum))
        for y in OA_YEARS
    ]
    draws += [
        (f"legacy-{p.split('/')[0]}", lambda p=p: draw_wayback(get, rng, p, args.per_stratum // 2))
        for p in LEGACY_PREFIXES
    ]
    targets: list[dict[str, str]] = []
    failed: dict[str, str] = {}
    for name, draw in draws:
        try:
            drawn = draw()
        except Exception as exc:  # noqa: BLE001 - a failed draw is an ERROR stratum, not a crash
            drawn, failed[name] = [], f"{type(exc).__name__}: {exc}"
        if not drawn and name not in failed:
            failed[name] = "the draw found no targets"
        targets += drawn
        print(f"drew {name}: {len(targets)} targets so far", file=sys.stderr)
    rows = []
    for target in targets:
        row = fetch(get, target, args.pages_dir)
        if row["outcome"] == "page":
            try:
                row |= measure_file(args.pages_dir / row["file"])
            except subprocess.CalledProcessError as exc:  # one page must not cost the run
                row |= {
                    "outcome": "unmeasured",
                    "error": f"exit {exc.returncode}: {exc.stderr[-500:]}",
                }
        rows.append(row)
        print(row["outcome"], row.get("status"), row["url"][:80], file=sys.stderr)
    how = (
        "uv run python scripts/sample_html_pages.py --pages-dir DIR "
        f"--per-stratum {args.per_stratum} --seed {args.seed}"
    )
    return write_corpus(rows, failed, how, args.out)


def saved_rows(saved_dir: Path, targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Measure each DOI target's saved page; a target with none saved is unmeasured.

    The archived strata are left out: saved today, their pages would ask for
    their images from hosts that no longer serve them.
    """
    rows = []
    for target in targets:
        if target["stratum"].startswith("legacy-"):
            continue
        row: dict[str, Any] = {k: target[k] for k in ("stratum", "id", "url")}
        name = f"{target['stratum']}-{target['id']}.html".replace("/", "_")
        if not (saved_dir / name).is_file():
            rows.append(row | {"outcome": "unmeasured", "error": "no saved page"})
            continue
        try:
            row |= {"outcome": "page", "file": name} | measure_file(saved_dir / name)
        except subprocess.CalledProcessError as exc:  # one page must not cost the run
            row |= {"outcome": "unmeasured", "error": f"exit {exc.returncode}: {exc.stderr[-500:]}"}
        rows.append(row)
        print(row["outcome"], row.get("code_points"), row["url"][:80], file=sys.stderr)
    return rows


def measure_saved(saved_dir: Path, targets_path: Path, out: Path, how: str) -> int:
    """Measure the saved pages of *targets_path*'s DOI targets and write the corpus."""
    targets = json.loads(targets_path.read_text(encoding="utf-8"))["rows"]
    rows = saved_rows(saved_dir, targets)
    how = (
        f"saved: {how}; then uv run python scripts/sample_html_pages.py --saved-dir DIR "
        f"--targets {targets_path}"
    )
    return write_corpus(rows, {}, how, out)


def write_corpus(rows: list[dict[str, Any]], failed: dict[str, str], how: str, out: Path) -> int:
    """Summarise *rows*, write the corpus to *out*, print it; non-zero if any stratum is ERROR."""
    summary = summarise(rows, failed)
    corpus = {
        "measured": date.today().isoformat(),
        "how": how,
        "platform": f"{platform.system()} {platform.machine()}, Python {platform.python_version()}",
        "summary": summary,
        "rows": rows,
    }
    out.write_text(json.dumps(corpus, indent=1) + "\n", encoding="utf-8")
    errors = {k: v for k, v in summary["strata"].items() if v["status"] == "ERROR"}
    for name, entry in sorted(summary["strata"].items()):
        rest = {k: v for k, v in entry.items() if k != "status"}
        print(f"{name:40} {entry['status']:6} {json.dumps(rest)}")
    print("all pages:", json.dumps(summary["all_pages"]))
    print("article pages:", json.dumps(summary["article_pages"]))
    if errors:
        unreportable = out.with_suffix(".unreportable.json")
        unreportable.write_text(json.dumps(errors, indent=1) + "\n", encoding="utf-8")
        print(
            f"ERROR: {len(errors)} strata unreportable, written to {unreportable}", file=sys.stderr
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
