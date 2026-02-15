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

"""Publications module — models, schema, storage, and sync for biomedical publications."""

from bmlib.publications.fetchers.registry import (
    get_fetcher,
    get_source,
    list_sources,
    register_source,
    source_names,
)
from bmlib.publications.models import (
    DownloadDay,
    FetchedRecord,
    FetchResult,
    FullTextSource,
    Publication,
    SourceDescriptor,
    SourceParam,
    SyncProgress,
    SyncReport,
)
from bmlib.publications.schema import ensure_schema
from bmlib.publications.storage import (
    add_fulltext_source,
    get_publication_by_doi,
    get_publication_by_pmid,
    store_publication,
)
from bmlib.publications.sync import sync

__all__ = [
    "sync",
    "SyncReport",
    "Publication",
    "FetchedRecord",
    "FullTextSource",
    "DownloadDay",
    "SyncProgress",
    "FetchResult",
    "SourceDescriptor",
    "SourceParam",
    "get_fetcher",
    "get_source",
    "list_sources",
    "register_source",
    "source_names",
    "store_publication",
    "get_publication_by_doi",
    "get_publication_by_pmid",
    "add_fulltext_source",
    "ensure_schema",
]
