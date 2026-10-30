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

"""One Europe PMC search record as a :class:`FetchedRecord`.

Not a sync fetcher: :func:`bmlib.publications.lookup.lookup_record` asks Europe
PMC for a preprint accession (``PPR`` + digits), which neither PubMed nor
OpenAlex knows, and this reads the ``resultType=core`` hit it gets back. Every
field is read type-checked, so a value of the wrong type costs that field and
not the record (the OpenAlex normaliser's rule; Rust's port reads alike).
"""

from __future__ import annotations

import re
from typing import Any

from bmlib.fulltext.models import FullTextSourceEntry
from bmlib.publications.fetchers.pubmed import _escape_markdown
from bmlib.publications.models import FetchedRecord

#: Restated, as ``transparency`` and ``fulltext`` each keep their own literal.
EUROPEPMC_REST_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"

# `fullTextXML` addresses a PMC or PPR accession, never a PMID (#188).
_PPR_ID = re.compile(r"PPR[0-9]+")
_PMC_ID = re.compile(r"PMC[0-9]+")

# The abstract is JATS-like markup that is not XML — the measured record
# carries a raw `&` — so tags are stripped by rule rather than parsed.
_BLOCK_TAG = re.compile(r"</?(?:title|p)\b[^>]*>")
_ANY_TAG = re.compile(r"<[^>]*>")
_ENTITY = re.compile(r"&(lt|gt|amp|quot|apos|#[0-9]+|#x[0-9A-Fa-f]+);")
_NAMED = {"lt": "<", "gt": ">", "amp": "&", "quot": '"', "apos": "'"}
# ASCII whitespace only: Python's `\s` and `str.strip` also take U+001C-U+001F,
# which Rust's do not (#557's divergence), and the two must agree.
_ASCII_SPACE = " \t\n\r\f\v"
_SPACE = re.compile(f"[{_ASCII_SPACE}]+")


def _object(value: Any, key: str) -> dict[str, Any]:
    """``value[key]`` when both are JSON objects, else ``{}``."""
    if not isinstance(value, dict):
        return {}
    found = value.get(key)
    return found if isinstance(found, dict) else {}


def _string(value: Any, key: str) -> str | None:
    """``value[key]`` when it is a non-empty string, else ``None``."""
    if not isinstance(value, dict):
        return None
    found = value.get(key)
    return found if isinstance(found, str) and found else None


def _list(value: Any, key: str) -> list[Any]:
    """``value[key]`` when it is a JSON array, else ``[]``."""
    if not isinstance(value, dict):
        return []
    found = value.get(key)
    return found if isinstance(found, list) else []


def _entity(match: re.Match[str]) -> str:
    """One XML entity or character reference, decoded; an unusable one kept."""
    name = match.group(1)
    if name in _NAMED:
        return _NAMED[name]
    code = int(name[2:], 16) if name.startswith("#x") else int(name[1:])
    # A surrogate or a value past U+10FFFF names no character (Rust's
    # `char::from_u32` refuses both), so it is kept as written.
    if code > 0x10FFFF or 0xD800 <= code <= 0xDFFF:
        return match.group(0)
    return chr(code)


def _abstract(text: str | None) -> str | None:
    """Europe PMC's ``abstractText`` as Markdown paragraphs, or ``None``.

    Each ``<title>`` and ``<p>`` is a paragraph; every other tag is removed,
    the five XML entities and numeric references decoded (any other entity is
    kept as written), whitespace collapsed, and the prose escaped as the PubMed
    parser escapes it.
    """
    if text is None:
        return None
    blocks = []
    for block in _BLOCK_TAG.split(text):
        plain = _SPACE.sub(" ", _ENTITY.sub(_entity, _ANY_TAG.sub("", block))).strip(_ASCII_SPACE)
        if plain:
            blocks.append(_escape_markdown(plain))
    return "\n\n".join(blocks) or None


def _author(author: Any) -> str | None:
    """``"Last, First"`` as the PubMed parser writes it, else the full name."""
    last = _string(author, "lastName")
    first = _string(author, "firstName")
    if last and first:
        return f"{last}, {first}"
    return _string(author, "fullName") or last


def normalize_europepmc(raw: dict[str, Any]) -> FetchedRecord:
    """Convert one Europe PMC ``resultType=core`` search hit to a record.

    ``source="europepmc"``. A ``PPR`` accession is kept in
    ``extras["ppr_id"]``, and a record Europe PMC holds the text of
    (``inEPMC`` ``"Y"``) gains an ``xml`` source for its ``fullTextXML``,
    addressed by its PMC ID, else its ``PPR`` accession — the only way
    :class:`~bmlib.fulltext.service.FullTextService` reaches a preprint's text,
    since its PMC tiers refuse a ``PPR``.
    """
    record_id = _string(raw, "id")
    ppr_id = record_id if record_id and _PPR_ID.fullmatch(record_id) else None
    pmcid = _string(raw, "pmcid")
    pmc_id = pmcid if pmcid and _PMC_ID.fullmatch(pmcid) else None
    is_open_access = _string(raw, "isOpenAccess") == "Y"

    fulltext_sources: list[FullTextSourceEntry] = []
    address = pmc_id or ppr_id
    if address and _string(raw, "inEPMC") == "Y":
        fulltext_sources.append(
            FullTextSourceEntry(
                url=f"{EUROPEPMC_REST_BASE}/{address}/fullTextXML",
                format="xml",
                source="europepmc",
                open_access=is_open_access,
            )
        )

    authors = [
        name for name in (_author(a) for a in _list(_object(raw, "authorList"), "author")) if name
    ]
    journal = _string(_object(_object(raw, "journalInfo"), "journal"), "title") or _string(
        _object(raw, "bookOrReportDetails"), "publisher"
    )
    publication_types = [
        t for t in _list(_object(raw, "pubTypeList"), "pubType") if isinstance(t, str) and t
    ]
    abstract_raw = raw.get("abstractText") if isinstance(raw, dict) else None

    return FetchedRecord(
        title=_string(raw, "title") or "",
        source="europepmc",
        doi=_string(raw, "doi"),
        pmid=_string(raw, "pmid"),
        pmc_id=pmc_id,
        abstract=_abstract(abstract_raw if isinstance(abstract_raw, str) else None),
        authors=authors,
        journal=journal,
        publication_date=_string(raw, "firstPublicationDate"),
        publication_types=publication_types,
        is_open_access=is_open_access,
        license=_string(raw, "license"),
        fulltext_sources=fulltext_sources,
        extras={"ppr_id": ppr_id} if ppr_id else {},
    )
