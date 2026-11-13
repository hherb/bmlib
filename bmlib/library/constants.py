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

"""The library's fixed names and limits (the twin of Rust's ``bmlib::library`` constants)."""

from __future__ import annotations

LIBRARY_FORMAT = "bmlib-library"
"""The ``format`` value of a library's marker file."""
LIBRARY_VERSION = 1
"""The library layout version this code reads and writes."""
RECORD_FORMAT = "bmlib-library-record"
"""The ``format`` value of a paper's per-installation record."""
RECORD_VERSION = 1
"""The record layout version this code reads and writes."""
MARKER_FILE = "bmlib-library.json"
"""The marker file that makes a folder a library."""
PAPERS_DIR = "papers"
"""The folder holding one sub-folder per paper."""
RECORDS_DIR = "records"
"""The folder inside a paper's folder holding one record per installation."""
DIGEST_PREFIX_HEX = 12
"""How many hex digits of a file's digest go into a name that must be unique."""
MAX_FOLDER_NAME_BYTES = 150
"""The longest paper-folder name, in UTF-8 bytes."""
MAX_TITLE_CHARS_IN_NAME = 60
"""The most characters of a title that go into a folder name."""
MAX_AUTHOR_CHARS_IN_NAME = 40
"""The most characters of the first author that go into a folder name."""
MAX_NAME_SUFFIX = 1000
"""The highest `` (n)`` suffix tried when a folder name is taken."""
MAX_LABEL_CHARS = 200
"""The most characters in an installation's label."""
IGNORED_NAMES = (".DS_Store", ".localized", "desktop.ini", "Thumbs.db")
"""File names a scan ignores (as does any name starting with ``.``)."""
ICLOUD_PLACEHOLDER = ".{name}.icloud"
"""The pattern of iCloud's placeholder for an online-only file."""
CORRUPT_SUFFIX = ".corrupt"
"""The suffix a quarantined file carries; Rust reuses ``fulltext::CORRUPT_SUFFIX``.

Defined here because Python's fulltext cache spells it inline.
"""
