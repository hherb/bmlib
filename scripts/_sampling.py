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

"""Sampling helpers shared by the live runners in this directory.

The scripts in ``scripts/`` measure real API populations to settle questions
bmlib's code then encodes — a log level, an allow-list, a heuristic. Three
concerns recur in every one of them and are answered here once:

* **Pacing** per host, not globally (:func:`_make_pacer`).
* **Throttling** honoured through a ``Retry-After`` clamped at *both* ends
  (:func:`_throttle_delay`).
* **Intervals** rather than point estimates, wherever a rule has a threshold
  in it (:func:`wilson`).

Each rule was learned the expensive way — ``sample_free_pdf_urls.py``'s first
live run sampled one host 300 times in 300 seconds and measured its own
throttling rather than the population it was aiming at. They live here so that
a second sampler inherits the lesson instead of re-learning it.

Not a package: ``scripts/`` has no ``__init__.py``, and these modules are run
as ``uv run python scripts/<name>.py``, which puts this directory on
``sys.path`` as ``sys.path[0]``. The test files that load a sampler by path
insert it explicitly.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

# A rate computed from probes that got through despite heavy throttling is
# not a random sample of the population: the ones that got through are the
# *early* ones, made before the host started refusing, and the later attempts
# it would have refused are exactly the ones missing from the sample. Past
# this share of unmeasured attempts, a summary reports ERROR instead of a
# number that looks precise but is not evidence of anything.
UNMEASURED_SHARE_ERROR_THRESHOLD = 0.20
# Retry budget for a throttled (429/503) request, and the backoff used when
# the server gives no usable Retry-After. Index 0 is the wait before the 2nd
# attempt, index 1 the wait before the 3rd.
MAX_PROBE_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = (2.0, 4.0)
# Both ends of `Retry-After` are clamped, because both ends lose the run. A
# negative parses fine and makes time.sleep raise; an hour — routine from a
# CDN rate limiter or a 503 maintenance window — is honoured silently, and
# since nothing prints until every population has finished, the operator sees
# a process producing no output, kills it, and loses the same data the
# zero-clamp exists to protect. A host asking for longer than this does not
# want to be sampled now, which is what `measured=False` is for.
MAX_RETRY_AFTER_SECONDS = 60.0


def is_probeable(url: str) -> bool:
    """Whether *url* is one bmlib would actually fetch.

    The URLs come from third-party JSON — Europe PMC's ``fullTextUrlList`` and
    Unpaywall's locations — so the scheme is not bmlib's to assume. A
    ``file://`` or ``ftp://`` URL is not a *download failure*; counting it as
    one would put a scheme bmlib never fetches into the rate that sets a log
    level. The same reasoning as ``_normalise_base_url`` in the Ollama
    provider, for the same class of input.
    """
    return urlsplit(url).scheme in ("http", "https")


def _sleep_for(seconds: float) -> None:
    """Sleep for *seconds*. Separated so tests can stub it out."""
    time.sleep(seconds)


def _retry_after_seconds(resp: Any) -> int | None:
    """Parse a ``Retry-After`` header's integer-seconds form.

    Args:
        resp: The throttled response.

    Returns:
        The number of seconds to wait, or ``None`` when the header is
        absent, an HTTP-date, or otherwise not a bare integer — the caller
        falls back to exponential backoff in that case. Handling the
        HTTP-date form is not worth it here: it is rare on a 429/503 in
        practice, and a wrong guess only costs one extra backoff step, not a
        wrong measurement.
    """
    headers = getattr(resp, "headers", None)
    if not headers:
        return None
    value = headers.get("Retry-After")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _throttle_delay(resp: Any, attempt: int) -> float:
    """Seconds to wait before retrying a throttled request, clamped at both ends.

    Args:
        resp: The 429/503 response, read for its ``Retry-After``.
        attempt: Which attempt just failed, 1-based, indexing the backoff.

    Returns:
        The wait, honouring ``Retry-After`` when it parses and falling back to
        the backoff otherwise, bounded to ``[0, MAX_RETRY_AFTER_SECONDS]``.
        The header is remote input and this sleep sits *outside* the ``try``
        that wraps the request, so an unclamped value is not a slow retry but
        a lost run — see :data:`MAX_RETRY_AFTER_SECONDS`.
    """
    retry_after = _retry_after_seconds(resp)
    fallback = RETRY_BACKOFF_SECONDS[attempt - 1]
    wanted = float(retry_after if retry_after is not None else fallback)
    return min(MAX_RETRY_AFTER_SECONDS, max(0.0, wanted))


def _make_pacer(
    interval: float, clock: Callable[[], float] = time.monotonic
) -> Callable[[str], None]:
    """Build a function that paces requests to a minimum interval *per host*.

    A global pause (run 1's approach) punishes every host for one host's
    throttling — 300 requests to Europe PMC at one request per second is what
    triggered its 429s, and pausing bioRxiv in lockstep with it bought
    nothing. Tracking the last request time per host instead lets a
    cooperative host go at its own pace while a throttling one gets slowed
    down on its own.

    Args:
        interval: Minimum seconds between two requests to the same host.
        clock: Source of the current time, injected so tests can drive it
            without a real clock or a real sleep.

    Returns:
        A function ``pace(url)`` that sleeps only as long as *url*'s host
        still needs to have waited *interval* seconds since its last request
        through this same pacer.
    """
    last_request: dict[str, float] = {}

    def pace(url: str) -> None:
        host = urlsplit(url).netloc
        now = clock()
        last = last_request.get(host)
        if last is None:
            last_request[host] = now
            return
        remaining = interval - (now - last)
        if remaining > 0:
            _sleep_for(remaining)
            last_request[host] = now + remaining
        else:
            last_request[host] = now

    return pace


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """A Wilson score interval for *k* failures in *n* attempts.

    An interval rather than a point estimate because issue #68's rule has a
    threshold in it (5%), and a point estimate near that threshold would
    misrepresent what the sample settles: 15 failures in 300 is exactly 5.0%
    and its interval runs from 3.1% to 8.1%.

    Raises:
        ValueError: If *n* is zero. There is no interval over no attempts, and
            returning ``(0.0, 0.0)`` would print as a perfect score.
    """
    if n <= 0:
        raise ValueError("no attempts to compute an interval over")
    p = k / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return max(0.0, centre - half), min(1.0, centre + half)
