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

"""Source fetchers for publication data."""

from bmlib.publications.fetchers.registry import (
    get_fetcher,
    get_source,
    list_sources,
    register_source,
    source_names,
)

# Backward compat: kept as a constant for code that reads it at module level.
ALL_SOURCES = ["pubmed", "biorxiv", "medrxiv", "openalex"]

__all__ = [
    "ALL_SOURCES",
    "get_fetcher",
    "get_source",
    "list_sources",
    "register_source",
    "source_names",
]
