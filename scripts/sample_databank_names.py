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

"""Measure PubMed's ``<DataBankName>`` vocabulary against bmlib's two sets.

`bmlib.transparency` reads PubMed's ``<DataBankList>`` twice over:
:data:`~bmlib.transparency.analyzer._TRIAL_REGISTRY_NAMES` establishes trial
registration, and :data:`~bmlib.transparency.analyzer._DATA_ARCHIVE_NAMES`
establishes data deposition. Both are allowlists, so a name in neither is
credited as neither — deliberately, so that a gap under-credits rather than
scoring an unknown databank as open data.

That makes three things load-bearing and measurable, which is what this script
reports for every candidate name:

1. **How many PubMed records carry it** — a member earning nothing is dead
   weight, and a non-member with volume is a gap worth closing.
2. **The exact spelling in the XML**, which is not always NLM's table spelling:
   the table says ``UMIN CTR`` and records say ``UMIN-CTR``. bmlib matches the
   records.
3. **How bmlib currently classifies it**, so a drift shows up as a line
   reading ``unclassified`` with a non-zero count.

Run it before changing either set:

    uv run python scripts/sample_databank_names.py --email you@example.org

Candidates come from NLM's published databank-source list
(https://www.nlm.nih.gov/bsd/medline_databank_source.html), plus anything the
two sets already name. Add a name to ``NLM_DATABANK_NAMES`` when NLM does.
Companion to ``scripts/sample_funder_names.py``.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter

ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
# NCBI's unauthenticated ceiling is 3 requests/second; stay under it.
REQUEST_INTERVAL_SECONDS = 0.4
# Records fetched per name to read the spelling off the XML. The name is a
# controlled value, so a handful settles it.
SPELLING_SAMPLE = 20

# NLM's published databank sources, verbatim, registries first.
NLM_DATABANK_NAMES = (
    "ANZCTR",
    "ChiCTR",
    "CRiS",
    "ClinicalTrials.gov",
    "CTRI",
    "DRKS",
    "EudraCT",
    "IRCT",
    "ISRCTN",
    "JapicCTI",
    "JMACCT",
    "JPRN",
    "NTR",
    "PACTR",
    "ReBec",
    "REPEC",
    "RPCEC",
    "SLCTR",
    "TCTR",
    "UMIN CTR",
    "BioProject",
    "dbGaP",
    "dbSNP",
    "dbVar",
    "Dryad",
    "figshare",
    "GDB",
    "GENBANK",
    "GEO",
    "OMIM",
    "PDB",
    "PIR",
    "PubChem-BioAssay",
    "PubChem-Compound",
    "PubChem-Substance",
    "RefSeq",
    "SRA",
    "SWISSPROT",
    "UniMES",
    "UniParc",
    "UniProtKB",
    "UniRef",
)


def _get(url: str, params: dict[str, str]) -> str | None:
    """Issue one paced E-utilities request; return the body, or None on failure."""
    time.sleep(REQUEST_INTERVAL_SECONDS)
    query = urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(f"{url}?{query}", timeout=60) as resp:  # noqa: S310
            return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"  request failed: {e}", file=sys.stderr)
        return None


def _record_count_and_ids(name: str, base: dict[str, str]) -> tuple[int, list[str]]:
    """Return how many PubMed records name *name* as a databank, and a sample of ids."""
    body = _get(
        ESEARCH,
        {**base, "db": "pubmed", "term": f'"{name}"[si]', "retmax": str(SPELLING_SAMPLE)},
    )
    if body is None:
        return -1, []
    count = re.search(r"<Count>(\d+)</Count>", body)
    return (int(count.group(1)) if count else 0), re.findall(r"<Id>(\d+)</Id>", body)


def _spellings(pmids: list[str], base: dict[str, str]) -> Counter[str]:
    """Count the literal ``<DataBankName>`` strings carried by *pmids*."""
    if not pmids:
        return Counter()
    body = _get(EFETCH, {**base, "db": "pubmed", "id": ",".join(pmids), "retmode": "xml"})
    if body is None:
        return Counter()
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        print(f"  unparsable efetch response: {e}", file=sys.stderr)
        return Counter()
    return Counter(
        (el.text or "").strip()
        for el in root.findall(".//Article/DataBankList/DataBank/DataBankName")
        if (el.text or "").strip()
    )


def main() -> int:
    """Measure every candidate name and print the table."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True, help="Contact address NCBI asks callers for.")
    parser.add_argument("--api-key", default=None, help="Optional NCBI API key.")
    args = parser.parse_args()

    base = {"tool": "bmlib", "email": args.email}
    if args.api_key:
        base["api_key"] = args.api_key

    from bmlib.transparency.analyzer import _DATA_ARCHIVE_NAMES, _TRIAL_REGISTRY_NAMES

    candidates = list(NLM_DATABANK_NAMES)
    known = {name.lower() for name in candidates}
    candidates += sorted((_TRIAL_REGISTRY_NAMES | _DATA_ARCHIVE_NAMES) - known)

    print(f"{'candidate':<34} {'records':>8}  {'spelling in XML':<20} bmlib reads it as")
    for name in candidates:
        count, pmids = _record_count_and_ids(name, base)
        if count < 0:
            print(f"{name:<34} {'ERROR':>8}")
            continue
        seen = _spellings(pmids, base)
        # The sample is fetched by name, so it also carries the *other* banks
        # those records list; keep only spellings that match this candidate
        # once punctuation and case are set aside, which is how PubMed's index
        # matched it in the first place.
        wanted = re.sub(r"[^a-z0-9]", "", name.lower())
        matching = [s for s in seen if re.sub(r"[^a-z0-9]", "", s.lower()) == wanted]
        spelling = ", ".join(sorted(matching)) or "-"

        lowered = {s.lower() for s in matching} or {name.lower()}
        if lowered & _TRIAL_REGISTRY_NAMES:
            kind = "registration"
        elif lowered & _DATA_ARCHIVE_NAMES:
            kind = "data deposition"
        else:
            kind = "unclassified" if count else "unclassified (unused)"
        print(f"{name:<34} {count:>8}  {spelling:<20} {kind}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
