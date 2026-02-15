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

"""Source fetcher registry.

Fetchers are registered by source name and lazily discovered on first access.
New fetchers can be registered at runtime via :func:`register_source`.

All registered fetchers share a uniform calling convention::

    fetcher(client, target_date, *, on_record, on_progress=None, **config)

Where ``on_record`` receives :class:`~bmlib.publications.models.FetchedRecord`
instances and ``**config`` carries source-specific parameters (api_key, email, etc.).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from bmlib.publications.models import SourceDescriptor, SourceParam

# Registry: source name -> (descriptor, fetcher_func)
_REGISTRY: dict[str, tuple[SourceDescriptor, Callable[..., Any]]] = {}


def register_source(
    descriptor: SourceDescriptor,
    fetcher: Callable[..., Any],
) -> None:
    """Register a fetcher function under a source name."""
    _REGISTRY[descriptor.name] = (descriptor, fetcher)


def list_sources() -> list[SourceDescriptor]:
    """Return descriptors for all registered sources."""
    _ensure_builtins()
    return [desc for desc, _ in _REGISTRY.values()]


def get_source(name: str) -> tuple[SourceDescriptor, Callable[..., Any]]:
    """Return the (descriptor, fetcher) tuple for a source.

    Raises :class:`ValueError` if the source is not registered.
    """
    _ensure_builtins()
    entry = _REGISTRY.get(name)
    if entry is None:
        raise ValueError(
            f"Unknown source {name!r}. Available: {sorted(_REGISTRY.keys())}"
        )
    return entry


def get_fetcher(name: str) -> Callable[..., Any]:
    """Return the fetcher callable for a source."""
    _, fetcher = get_source(name)
    return fetcher


def source_names() -> list[str]:
    """Return names of all registered sources."""
    _ensure_builtins()
    return list(_REGISTRY.keys())


# ---------------------------------------------------------------------------
# Lazy built-in registration
# ---------------------------------------------------------------------------


def _ensure_builtins() -> None:
    """Lazily register built-in fetchers on first access."""
    if _REGISTRY:
        return
    _register_builtins()


def _register_builtins() -> None:
    """Register all built-in source fetchers."""
    from bmlib.publications.fetchers.biorxiv import fetch_biorxiv
    from bmlib.publications.fetchers.openalex import fetch_openalex
    from bmlib.publications.fetchers.pubmed import fetch_pubmed

    register_source(
        SourceDescriptor(
            name="pubmed",
            display_name="PubMed",
            description="NCBI PubMed biomedical literature database",
            params=[
                SourceParam("api_key", "NCBI API key for higher rate limits", secret=True),
            ],
        ),
        fetch_pubmed,
    )

    register_source(
        SourceDescriptor(
            name="biorxiv",
            display_name="bioRxiv",
            description="Preprint server for biology",
            params=[
                SourceParam("api_key", "API key (reserved)", secret=True),
            ],
        ),
        lambda client, target_date, *, on_record, on_progress=None, **config: fetch_biorxiv(
            client, target_date, on_record=on_record, on_progress=on_progress,
            server="biorxiv", api_key=config.get("api_key"),
        ),
    )

    register_source(
        SourceDescriptor(
            name="medrxiv",
            display_name="medRxiv",
            description="Preprint server for health sciences",
            params=[
                SourceParam("api_key", "API key (reserved)", secret=True),
            ],
        ),
        lambda client, target_date, *, on_record, on_progress=None, **config: fetch_biorxiv(
            client, target_date, on_record=on_record, on_progress=on_progress,
            server="medrxiv", api_key=config.get("api_key"),
        ),
    )

    register_source(
        SourceDescriptor(
            name="openalex",
            display_name="OpenAlex",
            description="Open catalog of scholarly works, authors, and institutions",
            params=[
                SourceParam("email", "Contact email for polite API access", required=True),
                SourceParam("api_key", "OpenAlex API key for premium access", secret=True),
            ],
        ),
        fetch_openalex,
    )
