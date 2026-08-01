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

A row the script could not measure says ``ERROR`` rather than printing a zero
or an ``unclassified``: both of those are what a genuine finding looks like,
and a transient NCBI failure must not be readable as one.

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
    """Issue one paced E-utilities request; return the body, or None on failure.

    Deliberately ``urllib`` rather than the ``httpx`` its companion
    ``scripts/sample_funder_names.py`` guards an import for: this script wants
    nothing httpx offers, and staying on the standard library keeps it runnable
    without the optional extras installed. The ``urlopen`` hazards CLAUDE.md
    documents for ``llm/providers/ollama.py`` do not reach here — the host is a
    module constant over HTTPS, so no ``file://`` URL is reachable, and no
    credential rides in a header for a redirect to leak.
    """
    time.sleep(REQUEST_INTERVAL_SECONDS)
    query = urllib.parse.urlencode(params)
    try:
        # The URL is a module constant; only the query is built here.
        with urllib.request.urlopen(f"{url}?{query}", timeout=60) as resp:  # noqa: S310
            return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"  request failed: {e}", file=sys.stderr)
        return None


def _record_count_and_ids(name: str, base: dict[str, str]) -> tuple[int, list[str]]:
    """Return how many PubMed records name *name* as a databank, and a sample of ids.

    A count of ``-1`` means the question could not be asked. The caller must
    not print that as a measurement: ``0`` is exactly what a set member that
    has become dead weight looks like.
    """
    body = _get(
        ESEARCH,
        {**base, "db": "pubmed", "term": f'"{name}"[si]', "retmax": str(SPELLING_SAMPLE)},
    )
    if body is None:
        return -1, []
    count = re.search(r"<Count>(\d+)</Count>", body)
    if count is None:
        # A 200 carrying no <Count> is an NCBI error page, not an empty result
        # set — reporting it as 0 would read as "this member earns nothing".
        print(f"  no <Count> in esearch response for {name!r}", file=sys.stderr)
        return -1, []
    return int(count.group(1)), re.findall(r"<Id>(\d+)</Id>", body)


def _spellings(pmids: list[str], base: dict[str, str]) -> Counter[str] | None:
    """Count the literal ``<DataBankName>`` strings carried by *pmids*.

    ``None`` means the records could not be read, which is not the same as
    reading them and finding no names — the caller classifies by the spellings
    it sees, and on an empty counter falls back to the candidate's own
    spelling. Collapsing the two would print a classification nothing measured.
    """
    if not pmids:
        return Counter()
    body = _get(EFETCH, {**base, "db": "pubmed", "id": ",".join(pmids), "retmode": "xml"})
    if body is None:
        return None
    try:
        root = ET.fromstring(body)  # noqa: S314 - NCBI payload, no entities requested
    except ET.ParseError as e:
        print(f"  unparsable efetch response: {e}", file=sys.stderr)
        return None
    return Counter(
        (el.text or "").strip()
        for el in root.findall(".//Article/DataBankList/DataBank/DataBankName")
        if (el.text or "").strip()
    )


def _candidates(matched: frozenset[str]) -> list[str]:
    """Every name to measure: NLM's published list, plus anything *matched* it omits.

    *matched* is the union of bmlib's two databank sets, folded in rather than
    assumed to be covered by ``NLM_DATABANK_NAMES``: a set member missing from
    the table would be a name bmlib matches and nobody measures, which is the
    one blind spot this script exists to not have.
    """
    names = list(NLM_DATABANK_NAMES)
    known = {name.lower() for name in names}
    return names + sorted(matched - known)


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

    candidates = _candidates(_TRIAL_REGISTRY_NAMES | _DATA_ARCHIVE_NAMES)

    print(f"{'candidate':<34} {'records':>8}  {'spelling in XML':<20} bmlib reads it as")
    for name in candidates:
        count, pmids = _record_count_and_ids(name, base)
        if count < 0:
            print(f"{name:<34} {'ERROR':>8}")
            continue
        seen = _spellings(pmids, base)
        if seen is None:
            # The count is real but the classification would not be: with no
            # spellings to read, the fallback below reports the candidate's own
            # name back, which would claim a reading the sample never gave.
            print(f"{name:<34} {count:>8}  {'ERROR':<20} not measured")
            continue
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
