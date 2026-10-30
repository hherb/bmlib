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

"""One paper's record by identifier: PubMed first, OpenAlex always, Europe PMC for a preprint.

:func:`lookup_record` takes any of a PMID, a DOI, a PMC ID and a Europe PMC
preprint accession (``PPR`` + digits) and returns the paper's
:class:`~bmlib.publications.models.FetchedRecord`, ready to hand to
:meth:`~bmlib.fulltext.service.FullTextService.fetch_fulltext`. The design and
the maintainer's decisions are ``docs/superpowers/specs/2026-10-30-record-lookup-design.md``;
in short:

- **PubMed** (one efetch by PMID, the PMID the caller's or OpenAlex's) supplies
  the record when it holds the paper.
- **OpenAlex** is always asked, by the first of PMID, PMC ID and DOI: where
  PubMed has the paper it adds the identifiers PubMed's record lacks and its
  open-access locations, since every one the record misses is a tier of the
  full-text chain that never runs; otherwise its work is the record.
- **Europe PMC** is asked only for a ``PPR`` accession, which neither of the
  others knows; its record supplies the DOI the other two are asked by.
- **A preprint OpenAlex merged with its journal version** — its PMID leads to
  a PubMed record carrying another DOI — returns the preprint's own record,
  with the journal version's identifiers in :attr:`RecordLookup.published_version`
  and not in the record.

Every source's outcome is reported apart (:class:`SourceOutcome`), so an
outage never reads as "no such paper", and one source failing still returns
the others' record. Nothing is retried. Rust's ``bmlib::publications::lookup``
is held to the same results by ``tests/fixtures/record_lookup.json`` — on the
inputs it covers: Python's ``json`` also accepts ``NaN``, an escaped lone
surrogate and deeper nesting than serde_json's 128 levels, so such a body is
``unreadable`` in Rust alone (#572's direction; no draw has seen one).
"""

from __future__ import annotations

import dataclasses
import json
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import quote

from bmlib.publications.fetchers.europepmc import EUROPEPMC_REST_BASE, normalize_europepmc
from bmlib.publications.fetchers.openalex import _normalize as normalize_openalex
from bmlib.publications.fetchers.pubmed import EFETCH_URL, _parse_article_xml
from bmlib.publications.models import FetchedRecord
from bmlib.publications.storage import _normalize_doi

logger = logging.getLogger(__name__)

OPENALEX_WORKS_URL = "https://api.openalex.org/works"
EUROPEPMC_SEARCH_URL = f"{EUROPEPMC_REST_BASE}/search"

#: Raised by the client, these can only mean bmlib is wrong, so they propagate
#: rather than read as a source not answering. Restated, as ``fulltext`` and
#: ``transparency`` each restate it, and held to agreeing with theirs by a test.
_BUG_TYPES: tuple[type[BaseException], ...] = (
    TypeError,
    AttributeError,
    NameError,
    KeyError,
    IndexError,
)

_PMC_ID = re.compile(r"(?:PMC)?([0-9]+)", re.IGNORECASE)
_PPR_ID = re.compile(r"PPR([0-9]+)", re.IGNORECASE)
_PMID = re.compile(r"[0-9]+")


class SourceOutcome(StrEnum):
    """What one source made of the lookup."""

    FOUND = "found"
    #: The source's own "not held": OpenAlex's 404, PubMed's empty set,
    #: Europe PMC's empty result list.
    NOT_FOUND = "not_found"
    #: No answer: no connection, a timeout, any other non-200. Worth a retry.
    REQUEST_FAILED = "request_failed"
    #: A 200 whose body bmlib cannot read.
    UNREADABLE = "unreadable"
    #: Served, but not the paper asked about.
    OTHER_RECORD = "other_record"
    #: The PMID is a Bookshelf chapter, which the PubMed parser reads nothing from.
    BOOK_RECORD = "book_record"
    #: Nothing to ask by: PubMed with no PMID, Europe PMC with no PPR accession.
    NOT_ASKED = "not_asked"


@dataclass(frozen=True)
class SourceReport:
    """One source's outcome and a short message naming what happened."""

    outcome: SourceOutcome
    message: str

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dictionary."""
        return {"outcome": self.outcome.value, "message": self.message}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceReport:
        """Deserialise from :meth:`to_dict`; ``ValueError`` for anything else."""
        if not isinstance(data, dict) or set(data) != {"outcome", "message"}:
            raise ValueError("not a SourceReport.to_dict() record")
        if not isinstance(data["message"], str):
            raise ValueError("a SourceReport message is text")
        return cls(SourceOutcome(data["outcome"]), data["message"])


@dataclass(frozen=True)
class PublishedVersion:
    """The journal version of a preprint, named beside the preprint's record."""

    pmid: str | None
    doi: str | None
    pmc_id: str | None


@dataclass(frozen=True)
class RecordLookup:
    """What :func:`lookup_record` found, and what each source made of it."""

    record: FetchedRecord | None
    published_version: PublishedVersion | None
    pubmed: SourceReport
    openalex: SourceReport
    europepmc: SourceReport

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dictionary."""
        return {
            "record": self.record.to_dict() if self.record else None,
            "published_version": (
                dataclasses.asdict(self.published_version) if self.published_version else None
            ),
            "pubmed": self.pubmed.to_dict(),
            "openalex": self.openalex.to_dict(),
            "europepmc": self.europepmc.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RecordLookup:
        """Deserialise from :meth:`to_dict`.

        Raises:
            ValueError: *data* is not a record :meth:`to_dict` wrote.
        """
        keys = {"record", "published_version", "pubmed", "openalex", "europepmc"}
        if not isinstance(data, dict) or set(data) != keys:
            raise ValueError("not a RecordLookup.to_dict() record")
        published = data["published_version"]
        if published is not None:
            if not isinstance(published, dict) or set(published) != {"pmid", "doi", "pmc_id"}:
                raise ValueError("not a PublishedVersion record")
            published = PublishedVersion(**published)
        return cls(
            record=None if data["record"] is None else FetchedRecord.from_dict(data["record"]),
            published_version=published,
            pubmed=SourceReport.from_dict(data["pubmed"]),
            openalex=SourceReport.from_dict(data["openalex"]),
            europepmc=SourceReport.from_dict(data["europepmc"]),
        )


# ---------------------------------------------------------------------------
# The caller's identifiers
# ---------------------------------------------------------------------------


def _caller_text(value: object, argument: str) -> str | None:
    """*value* stripped, ``None`` for none or blank; a non-text value raises."""
    if value is None:
        return None
    if not issubclass(type(value), str):
        raise TypeError(f"{argument} must be a str, not {type(value).__name__}")
    return str.strip(value) or None  # type: ignore[arg-type]


def _lookup_pmid(pmid: object) -> str | None:
    """The caller's PMID: ASCII digits, not all zeros, sent without leading zeros.

    ``transparency``'s caller rule for text (#470, #474), restated and held to
    agreeing with it by a test.
    """
    text = _caller_text(pmid, "pmid")
    if text is None:
        return None
    if not _PMID.fullmatch(text) or not text.strip("0"):
        raise ValueError(f"pmid {pmid!r} is not ASCII digits, or is all zeros")
    return text.lstrip("0")


def _lookup_pmc_id(pmc_id: object) -> str | None:
    """The caller's PMC ID as ``PMC`` + ASCII digits, its prefix optional and case-folded."""
    text = _caller_text(pmc_id, "pmc_id")
    if text is None:
        return None
    match = _PMC_ID.fullmatch(text)
    if not match:
        raise ValueError(f"pmc_id {pmc_id!r} is not PMC followed by ASCII digits")
    return f"PMC{match.group(1)}"


def _lookup_ppr_id(ppr_id: object) -> str | None:
    """The caller's Europe PMC preprint accession as ``PPR`` + ASCII digits."""
    text = _caller_text(ppr_id, "ppr_id")
    if text is None:
        return None
    match = _PPR_ID.fullmatch(text)
    if not match:
        raise ValueError(f"ppr_id {ppr_id!r} is not PPR followed by ASCII digits")
    return f"PPR{match.group(1)}"


def _lookup_doi(doi: object) -> str | None:
    """The caller's DOI, prefix-stripped and case-folded; no shape check (#475)."""
    return _normalize_doi(_caller_text(doi, "doi"))


# ---------------------------------------------------------------------------
# The three sources
# ---------------------------------------------------------------------------

_QUIET = (SourceOutcome.FOUND, SourceOutcome.NOT_FOUND, SourceOutcome.NOT_ASKED)


def _report(source: str, outcome: SourceOutcome, message: str) -> SourceReport:
    """A report, logged: a source that could not be used at WARNING, a Bookshelf
    record and the source's own answers at DEBUG (#218's rule for a book)."""
    level = (
        logging.DEBUG
        if outcome in _QUIET or outcome is SourceOutcome.BOOK_RECORD
        else logging.WARNING
    )
    logger.log(
        level, "lookup_record: %s: %s%s", source, outcome.value, f" ({message})" if message else ""
    )
    return SourceReport(outcome, message)


def _get(client: Any, source: str, url: str, params: dict[str, Any]) -> Any | SourceReport:
    """The response, or a ``REQUEST_FAILED`` report when the client raised.

    The status is the caller's to judge: OpenAlex's 404 is an answer.
    """
    try:
        response = client.get(url, params=params)
    except _BUG_TYPES:
        raise
    except Exception as exc:
        return _report(source, SourceOutcome.REQUEST_FAILED, f"{type(exc).__name__}: {exc}")
    return response


def _json_object(source: str, text: str) -> dict[str, Any] | SourceReport:
    """The body as a JSON object, or an ``UNREADABLE`` report."""
    try:
        body = json.loads(text)
    except (ValueError, RecursionError):
        return _report(source, SourceOutcome.UNREADABLE, "the body is not JSON")
    if not isinstance(body, dict):
        return _report(
            source,
            SourceOutcome.UNREADABLE,
            f"the body is a {_json_type(body)}, not a JSON object",
        )
    return body


def _json_type(value: object) -> str:
    """The JSON type of a decoded value, as JSON names it."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return "list"


def _ask_europepmc(client: Any, ppr_id: str) -> tuple[SourceReport, FetchedRecord | None]:
    """Europe PMC's record for a preprint accession."""
    source = "europepmc"
    params = {"query": f"EXT_ID:{ppr_id} AND SRC:PPR", "resultType": "core", "format": "json"}
    response = _get(client, source, EUROPEPMC_SEARCH_URL, params)
    if isinstance(response, SourceReport):
        return response, None
    if response.status_code != 200:
        return _report(source, SourceOutcome.REQUEST_FAILED, f"HTTP {response.status_code}"), None
    body = _json_object(source, response.text)
    if isinstance(body, SourceReport):
        return body, None
    result_list = body.get("resultList")
    results = result_list.get("result") if isinstance(result_list, dict) else None
    if not isinstance(results, list):
        message = "the body carries no resultList.result list"
        return _report(source, SourceOutcome.UNREADABLE, message), None
    if not results:
        message = f"Europe PMC holds no record for {ppr_id}"
        return _report(source, SourceOutcome.NOT_FOUND, message), None
    first = results[0]
    if not isinstance(first, dict):
        message = "the first result is not a JSON object"
        return _report(source, SourceOutcome.UNREADABLE, message), None
    if first.get("id") != ppr_id:
        message = f"Europe PMC answered with {first.get('id')!r}, not {ppr_id}"
        return _report(source, SourceOutcome.OTHER_RECORD, message), None
    return _report(source, SourceOutcome.FOUND, ""), normalize_europepmc(first)


def _ask_openalex(
    client: Any,
    kind: str,
    value: str,
    email: str,
    api_key: str | None,
) -> tuple[SourceReport, FetchedRecord | None]:
    """OpenAlex's work for one identifier, checked to carry that identifier."""
    source = "openalex"
    key = f"{kind}:{quote(value, safe='/:') if kind == 'doi' else value}"
    params: dict[str, Any] = {"mailto": email}
    if api_key:
        params["api_key"] = api_key
    response = _get(client, source, f"{OPENALEX_WORKS_URL}/{key}", params)
    if isinstance(response, SourceReport):
        return response, None
    if response.status_code == 404:
        message = f"OpenAlex holds no work for {kind}:{value}"
        return _report(source, SourceOutcome.NOT_FOUND, message), None
    if response.status_code != 200:
        return _report(source, SourceOutcome.REQUEST_FAILED, f"HTTP {response.status_code}"), None
    body = _json_object(source, response.text)
    if isinstance(body, SourceReport):
        return body, None
    record = normalize_openalex(body)
    carried = {
        "pmid": record.pmid,
        "pmcid": record.pmc_id,
        "doi": _normalize_doi(record.doi),
    }[kind]
    if carried != value:
        message = f"OpenAlex's work for {kind}:{value} carries {kind} {carried!r}"
        return _report(source, SourceOutcome.OTHER_RECORD, message), None
    return _report(source, SourceOutcome.FOUND, ""), record


def _ask_pubmed(
    client: Any, pmid: str, api_key: str | None
) -> tuple[SourceReport, FetchedRecord | None]:
    """PubMed's record for a PMID: the ``<PubmedArticle>`` whose own PMID it is (#474)."""
    source = "pubmed"
    params: dict[str, Any] = {"db": "pubmed", "id": pmid, "retmode": "xml"}
    if api_key:
        params["api_key"] = api_key
    response = _get(client, source, EFETCH_URL, params)
    if isinstance(response, SourceReport):
        return response, None
    if response.status_code != 200:
        return _report(source, SourceOutcome.REQUEST_FAILED, f"HTTP {response.status_code}"), None
    try:
        root = ET.fromstring(response.text)
    except (ET.ParseError, ValueError):
        return _report(source, SourceOutcome.UNREADABLE, "the body is not XML"), None
    if root.tag != "PubmedArticleSet":
        message = f"efetch answered with an <{root.tag}> root, not <PubmedArticleSet>"
        return _report(source, SourceOutcome.UNREADABLE, message), None
    if len(root) == 0:
        message = f"PubMed holds no record for PMID {pmid}"
        return _report(source, SourceOutcome.NOT_FOUND, message), None
    for child in root:
        if child.tag == "PubmedArticle" and child.findtext("MedlineCitation/PMID") == pmid:
            return _report(source, SourceOutcome.FOUND, ""), _parse_article_xml(child)
        if child.tag == "PubmedBookArticle" and child.findtext("BookDocument/PMID") == pmid:
            message = f"PMID {pmid} is a Bookshelf record"
            return _report(source, SourceOutcome.BOOK_RECORD, message), None
    message = f"efetch served no record for PMID {pmid}"
    return _report(source, SourceOutcome.OTHER_RECORD, message), None


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

_IDENTIFIERS = ("pmid", "doi", "pmc_id")


def _same(field: str, a: str, b: str) -> bool:
    """Whether two spellings of an identifier name the same thing."""
    if field == "doi":
        return _normalize_doi(a) == _normalize_doi(b)
    return a.upper() == b.upper()


def _assemble(
    base: FetchedRecord,
    others: list[FetchedRecord],
    caller: dict[str, str | None],
    ppr_id: str | None,
) -> FetchedRecord:
    """*base* with each empty identifier filled — the caller's first, then the
    others' — and the others' full-text sources it lacks appended."""
    record = dataclasses.replace(
        base, fulltext_sources=list(base.fulltext_sources), extras=dict(base.extras)
    )
    for field, given in caller.items():
        if given is None:
            continue
        held = getattr(record, field)
        if held is None:
            setattr(record, field, given)
        elif not _same(field, held, given):
            logger.warning(
                "lookup_record: the caller's %s %r differs from the record's %r; "
                "keeping the record's",
                field,
                given,
                held,
            )
    seen = {entry.url for entry in record.fulltext_sources}
    for other in others:
        for field in _IDENTIFIERS:
            if getattr(record, field) is None and getattr(other, field):
                setattr(record, field, getattr(other, field))
        for entry in other.fulltext_sources:
            if entry.url not in seen:
                seen.add(entry.url)
                record.fulltext_sources.append(entry)
        record.is_open_access = record.is_open_access or other.is_open_access
    if ppr_id:
        record.extras["ppr_id"] = ppr_id
    return record


def lookup_record(
    client: Any,
    *,
    pmid: str | None = None,
    doi: str | None = None,
    pmc_id: str | None = None,
    ppr_id: str | None = None,
    email: str,
    ncbi_api_key: str | None = None,
    openalex_api_key: str | None = None,
) -> RecordLookup:
    """Look up one paper's record by any of its identifiers.

    Args:
        client: An httpx-style client (``client.get(url, params=...)``
            returning a response with ``status_code`` and ``text``). Its
            timeouts and headers are the caller's.
        pmid: A PMID: ASCII digits, not all zeros, whitespace stripped.
        doi: A DOI, with or without a ``doi:`` / ``https://doi.org/`` prefix;
            case-folded, its shape not checked (#475).
        pmc_id: A PMC ID, its ``PMC`` prefix optional and case-folded.
        ppr_id: A Europe PMC preprint accession (``PPR`` + digits).
        email: OpenAlex's polite-pool contact (``mailto``).
        ncbi_api_key: Sent with the efetch when given.
        openalex_api_key: Sent to OpenAlex when given.

    Returns:
        The record (``None`` when no source has the paper), the journal
        version of a preprint OpenAlex merged with it, and each source's
        outcome. The lookup's identity is the first given of PMID, PMC ID,
        PPR and DOI; the caller's other identifiers fill only empty fields,
        a disagreement being logged at WARNING and the record's kept.

    Raises:
        ValueError: An identifier is malformed, or none is given (a blank one
            is none). Raised before any request.
        TypeError: An identifier is not text; or the client raised a type
            that can only mean bmlib is wrong.
    """
    caller = {
        "pmid": _lookup_pmid(pmid),
        "doi": _lookup_doi(doi),
        "pmc_id": _lookup_pmc_id(pmc_id),
    }
    ppr = _lookup_ppr_id(ppr_id)
    if not any(caller.values()) and ppr is None:
        raise ValueError("lookup_record needs a PMID, DOI, PMC ID or PPR accession")

    if ppr:
        epmc_report, epmc = _ask_europepmc(client, ppr)
    else:
        epmc_report, epmc = _report("europepmc", SourceOutcome.NOT_ASKED, "no PPR accession"), None

    lookup_doi = caller["doi"] or (_normalize_doi(epmc.doi) if epmc else None)
    key = (
        ("pmid", caller["pmid"])
        if caller["pmid"]
        else ("pmcid", caller["pmc_id"])
        if caller["pmc_id"]
        else ("doi", lookup_doi)
        if lookup_doi
        else None
    )
    if key and key[1]:
        oa_report, oa = _ask_openalex(client, key[0], key[1], email, openalex_api_key)
    else:
        message = "no PMID, PMC ID or DOI"
        oa_report, oa = _report("openalex", SourceOutcome.NOT_ASKED, message), None

    oa_pmid = oa.pmid if oa and oa.pmid and _PMID.fullmatch(oa.pmid) else None
    pubmed_pmid = caller["pmid"] or (oa_pmid.lstrip("0") or None if oa_pmid else None)
    if pubmed_pmid:
        pm_report, pm = _ask_pubmed(client, pubmed_pmid, ncbi_api_key)
    else:
        pm_report, pm = _report("pubmed", SourceOutcome.NOT_ASKED, "no PMID"), None

    published: PublishedVersion | None = None
    is_published_version = (
        caller["pmid"] is None
        and caller["pmc_id"] is None
        and lookup_doi is not None
        and oa is not None
        and pm is not None
        and pm.doi is not None
        and _normalize_doi(pm.doi) != lookup_doi
    )
    if is_published_version:
        assert oa is not None and pm is not None  # narrowed for mypy
        published = PublishedVersion(pm.pmid, pm.doi, pm.pmc_id or oa.pmc_id)
        pm_report = _report(
            "pubmed",
            SourceOutcome.FOUND,
            f"PMID {pm.pmid} is a published version (DOI {pm.doi}), returned as published_version",
        )
        preprint = dataclasses.replace(
            oa,
            pmid=None,
            pmc_id=None,
            fulltext_sources=[e for e in oa.fulltext_sources if e.version == "preprint"],
        )
        candidates: list[FetchedRecord | None] = [epmc, preprint] if epmc else [preprint]
    else:
        if oa is not None and pm is None and caller["pmid"] is None and caller["pmc_id"] is None:
            # Asked by a DOI, OpenAlex's PMID and PMC ID may be a merged journal
            # version's, and only PubMed's record — its DOI compared above — can
            # tell; unconfirmed, they stay out, so an outage cannot decide which
            # version the record names (PR review). The locations are kept.
            oa = dataclasses.replace(oa, pmid=None, pmc_id=None)
        candidates = [pm, epmc, oa]
    found = [r for r in candidates if r is not None]
    record = _assemble(found[0], found[1:], caller, ppr) if found else None

    return RecordLookup(
        record=record,
        published_version=published,
        pubmed=pm_report,
        openalex=oa_report,
        europepmc=epmc_report,
    )
