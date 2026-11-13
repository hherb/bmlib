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

"""The shared library as the full-text service's second store.

The library is keyed by the request's ``pmid``, ``doi`` and ``pmc_id``. Each is
made canonical on its own, so one bad value costs only that identifier, not the
whole key.
"""

from __future__ import annotations

from dataclasses import dataclass

from bmlib.library import BadIdentifier, Installation, Library, PaperIds, canonical_ids


def library_ids(
    pmid: str | None, doi: str | None, pmc_id: str | None
) -> tuple[PaperIds, list[str]]:
    """The request's identifiers as the library's canonical ones, and the fields refused.

    Each of *pmid*, *doi* and *pmc_id* goes alone through :func:`canonical_ids` (a blank one is
    simply absent). A field it refuses is left out of the result and its name (``"pmid"``,
    ``"doi"`` or ``"pmc_id"``, the request's parameter names) is returned in the list, in that
    order. This function only reports; the caller logs the warning.
    """
    picked: dict[str, str | None] = {}
    dropped: list[str] = []
    for field, key, args in (
        ("pmid", "pmid", (pmid, None, None)),
        ("doi", "doi", (None, doi, None)),
        ("pmc_id", "pmcid", (None, None, pmc_id)),
    ):
        try:
            picked[key] = getattr(canonical_ids(*args), key)
        except BadIdentifier:
            dropped.append(field)
    return PaperIds(**picked), dropped


@dataclass(frozen=True)
class LibraryStore:
    """The shared library and the installation that writes to it."""

    library: Library
    who: Installation
