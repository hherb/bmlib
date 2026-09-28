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

"""The ``User-Agent`` every HTTP request bmlib makes carries.

Private to bmlib, like :mod:`bmlib._atomic`, and at the top level for the
same reason: two packages make requests to remotes that judge a caller by
this header, and a value written in two places is two things free to drift
apart (issue #196). ``transparency`` extracted its own into one function for
issue #194 — ClinicalTrials.gov's edge refused bmlib's identification for a
whole release — while ``publications.sync`` went on building the refused
shape inline, where nothing pinned it and no instrument probed it.

Depends on the standard library alone. ``httpx`` is an optional dependency
of both callers, so its version is passed in rather than imported here.
"""

from __future__ import annotations

from bmlib import __version__


def user_agent(email: str, httpx_version: str) -> str:
    """Return bmlib's ``User-Agent`` header value.

    **The trailing ``python-httpx`` token is load-bearing and is not
    decoration** (issue #194). ClinicalTrials.gov's edge refuses bmlib's
    identification with a bare 134-byte ``403 Forbidden`` page. Measured
    2026-09-06 against ``/api/v2/studies/{nct}?fields=hasResults``: of
    thirteen header shapes, the five carrying ``python-httpx`` — including the
    token appended *after* bmlib's own identification — served 200, while
    ``curl``, ``python-requests``, ``Python-urllib``, ``Go-http-client``,
    ``PostmanRuntime`` and a browser string were all refused. So it is an
    allow-list on that one token, and its position does not matter.

    The token is **appended to** bmlib's identification rather than replacing
    it: CrossRef, OpenAlex and NCBI all ask a caller to say who it is and
    where to write, and answering ``python-httpx`` alone would trade one API's
    policy for the others'. It is not a fiction either — every caller sends
    through httpx, so this says what httpx would have said about itself.

    This is a live-only property that **no unit test can hold**.
    ``scripts/sample_api_failures.py`` presents this exact value to CrossRef,
    Europe PMC, OpenAlex, PubMed's ``efetch`` and ClinicalTrials.gov; the
    remotes only ``publications.sync`` reaches — PubMed's ``esearch`` and
    bioRxiv/medRxiv — are probed by nothing, so run that sampler, and look at
    a sync's first day, before touching this string.

    Args:
        email: The caller's contact address.
        httpx_version: ``httpx.__version__``, passed in because httpx is an
            optional dependency this module does not import.

    Returns:
        The header value.
    """
    return f"bmlib/{__version__} (mailto:{email}) python-httpx/{httpx_version}"
