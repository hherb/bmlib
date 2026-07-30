#!/usr/bin/env python3
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

"""Sample real funder names from CrossRef and PubMed for issue #36.

`bmlib.transparency` decides `industry_funding_detected` by matching keywords
against two structured funder-name corpora — CrossRef ``funder[].name`` and
PubMed ``<Grant><Agency>``. That flag feeds a HIGH-risk rule, and HIGH applies
a quality-tier downgrade, so a false positive costs more than a false negative.
Changing the matcher therefore has to be *measured* against real names rather
than argued from examples.

This is a **live runner**, not part of the pytest suite — it makes network
requests, in the established pattern of ``scripts/smoke_test_tool_calling.py``.
The suite consumes only its committed, hand-labelled output
(``tests/data/funder_names.json``), so the tests stay offline.

Usage::

    uv run python scripts/sample_funder_names.py            # both sources
    uv run python scripts/sample_funder_names.py --target 400
    uv run python scripts/sample_funder_names.py --source crossref
    uv run python scripts/sample_funder_names.py -o /tmp/raw.json

Environment:
    BMLIB_CONTACT_EMAIL  Contact address for CrossRef's polite pool and NCBI's
                         ``email`` parameter. Both APIs ask for one, and
                         CrossRef gives politely-identified callers better
                         service. Defaults to a generic address with a warning.
    NCBI_API_KEY         Optional. Moves the E-utilities requests from the
                         keyless 3/s per-IP bucket to the key's 10/s one.

Writes ``{"crossref": [...], "pubmed": [...]}`` — unique names, sorted, with
the raw spelling preserved. Sorted output so that re-running the script
produces a reviewable diff rather than a reshuffle.

Exits 0 on success, 1 if a source yielded nothing, 2 on a configuration
problem.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

try:
    import httpx
except ImportError:  # pragma: no cover - live runner, not covered by tests
    sys.stderr.write("This script needs httpx. Install with: uv pip install 'bmlib[all]'\n")
    raise SystemExit(2) from None

CROSSREF_WORKS = "https://api.crossref.org/works"
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

# NCBI allows 3 requests/second without a key and 10 with one. Pace to just
# inside each, since a 429 here wastes the whole run.
NCBI_INTERVAL_KEYLESS = 0.35
NCBI_INTERVAL_WITH_KEY = 0.11

# CrossRef publishes no hard rate limit for the polite pool; one request every
# half second is well inside what it asks for.
CROSSREF_INTERVAL = 0.5

CROSSREF_ROWS = 100
PUBMED_ESEARCH_RETMAX = 200
PUBMED_EFETCH_BATCH = 100

# Backstop on either walk, so a server that keeps answering without ever
# yielding a new name cannot loop indefinitely.
MAX_REQUESTS = 200

# A broad, recent slice of the literature. There is no "has grant support"
# search filter to narrow this with, so the run takes whatever fraction of
# each batch happens to carry a <GrantList> — which is the point: filtering on
# a grant-related publication type (e.g. "Research Support, N.I.H.") would
# skew the corpus towards government funders and flatter the matcher's
# precision on exactly the names it is most likely to get wrong.
PUBMED_QUERY = "2025:2026[dp] AND hasabstract"

DEFAULT_TARGET = 400
DEFAULT_OUTPUT = Path("tests/data/funder_names.raw.json")


def _contact_email() -> str:
    """The address sent to both APIs, or a generic default with a warning."""
    email = os.environ.get("BMLIB_CONTACT_EMAIL", "").strip()
    if email:
        return email
    fallback = "bmlib@example.org"
    sys.stderr.write(
        f"WARNING: BMLIB_CONTACT_EMAIL is unset; using {fallback}. "
        "Set it to your own address — CrossRef's polite pool and NCBI both "
        "ask callers to identify themselves.\n"
    )
    return fallback


def sample_crossref(client: httpx.Client, target: int, email: str) -> list[str]:
    """Harvest unique CrossRef ``funder[].name`` values.

    Uses cursor paging (``cursor=*``), which is CrossRef's supported way to
    walk more rows than the offset limit allows.

    **The walk ends on an empty page, never on an unchanged cursor.**
    ``next-cursor`` is an Elasticsearch scroll id: CrossRef hands back the
    *same* string on every page after the first and advances the scroll
    server-side, so treating a repeated cursor as exhaustion stops the walk
    after two pages — which is what capped an earlier run of this script at
    159 names instead of the 400 it asked for.

    Args:
        client: An open HTTP client.
        target: Stop once this many unique names have been seen.
        email: Contact address for the polite pool.

    Returns:
        The unique names, sorted, original spelling preserved.
    """
    seen: dict[str, None] = {}
    cursor = "*"
    requests = 0

    while len(seen) < target and requests < MAX_REQUESTS:
        response = client.get(
            CROSSREF_WORKS,
            params={
                "filter": "has-funder:true",
                "select": "funder",
                "rows": CROSSREF_ROWS,
                "cursor": cursor,
                "mailto": email,
            },
        )
        response.raise_for_status()
        message = response.json().get("message", {})
        items = message.get("items", [])
        requests += 1
        if not items:
            break

        for item in items:
            for funder in item.get("funder", []) or []:
                name = (funder.get("name") or "").strip()
                if name:
                    seen.setdefault(name, None)

        next_cursor = message.get("next-cursor")
        if not next_cursor:
            break
        cursor = next_cursor
        sys.stderr.write(f"  crossref: {len(seen)} unique names after {requests} requests\n")
        time.sleep(CROSSREF_INTERVAL)

    return sorted(seen)


def sample_pubmed(client: httpx.Client, target: int, email: str, api_key: str) -> list[str]:
    """Harvest unique PubMed ``<Grant><Agency>`` values.

    Searches a broad recent slice, then walks the result set in ``efetch``
    batches, keeping the agency strings from whichever records carry a
    ``<GrantList>``.

    Args:
        client: An open HTTP client.
        target: Stop once this many unique agencies have been seen.
        email: Contact address for NCBI's ``email`` parameter.
        api_key: NCBI API key, or ``""`` for the keyless rate bucket.

    Returns:
        The unique agency names, sorted, original spelling preserved.
    """
    interval = NCBI_INTERVAL_WITH_KEY if api_key else NCBI_INTERVAL_KEYLESS
    common = {"db": "pubmed", "tool": "bmlib", "email": email}
    if api_key:
        common["api_key"] = api_key

    seen: dict[str, None] = {}
    retstart = 0

    while len(seen) < target:
        search = client.get(
            f"{EUTILS}/esearch.fcgi",
            params={
                **common,
                "term": PUBMED_QUERY,
                "retmax": PUBMED_ESEARCH_RETMAX,
                "retstart": retstart,
                "retmode": "json",
                "sort": "date",
            },
        )
        search.raise_for_status()
        pmids = search.json().get("esearchresult", {}).get("idlist", [])
        if not pmids:
            break
        time.sleep(interval)

        for start in range(0, len(pmids), PUBMED_EFETCH_BATCH):
            batch = pmids[start : start + PUBMED_EFETCH_BATCH]
            fetch = client.get(
                f"{EUTILS}/efetch.fcgi",
                params={**common, "id": ",".join(batch), "retmode": "xml"},
            )
            fetch.raise_for_status()
            for agency in _iter_agencies(fetch.text):
                seen.setdefault(agency, None)
            walked = retstart + start
            sys.stderr.write(f"  pubmed: {len(seen)} unique agencies after {walked} ids\n")
            time.sleep(interval)
            if len(seen) >= target:
                break

        retstart += len(pmids)

    return sorted(seen)


def _iter_agencies(xml_text: str):
    """Yield every non-blank ``<Grant><Agency>`` string in an efetch payload.

    A malformed payload yields nothing rather than aborting the run: one bad
    batch out of dozens should not cost the whole sample.
    """
    try:
        root = ET.fromstring(xml_text)  # noqa: S314 - NCBI payload, no entities requested
    except ET.ParseError as exc:
        sys.stderr.write(f"  pubmed: skipping unparseable batch ({exc})\n")
        return
    for agency in root.iter("Agency"):
        text = (agency.text or "").strip()
        if text:
            yield text


def main(argv: list[str] | None = None) -> int:
    """Sample both sources and write the raw names to disk."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--target",
        type=int,
        default=DEFAULT_TARGET,
        help=f"Unique names to collect per source (default {DEFAULT_TARGET}).",
    )
    parser.add_argument(
        "--source",
        choices=("crossref", "pubmed", "both"),
        default="both",
        help="Which corpus to sample (default both).",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Where to write the raw names (default {DEFAULT_OUTPUT}).",
    )
    args = parser.parse_args(argv)

    email = _contact_email()
    api_key = os.environ.get("NCBI_API_KEY", "").strip()
    result: dict[str, list[str]] = {}

    headers = {"User-Agent": f"bmlib-funder-sampler (+mailto:{email})"}
    with httpx.Client(timeout=30.0, headers=headers, follow_redirects=True) as client:
        if args.source in ("crossref", "both"):
            sys.stderr.write("Sampling CrossRef funder names…\n")
            result["crossref"] = sample_crossref(client, args.target, email)
        if args.source in ("pubmed", "both"):
            sys.stderr.write("Sampling PubMed grant agencies…\n")
            result["pubmed"] = sample_pubmed(client, args.target, email, api_key)

    empty = [source for source, names in result.items() if not names]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")

    for source, names in result.items():
        sys.stderr.write(f"{source}: {len(names)} unique names\n")
    sys.stderr.write(f"Wrote {args.output}\n")

    if empty:
        sys.stderr.write(f"ERROR: no names harvested from {', '.join(empty)}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
