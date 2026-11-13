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

"""A shared folder of research papers that several apps read and write.

Identifiers, source kinds, installations, folder names, and reading a library
(scan, find, group, read). Rust's ``bmlib::library`` is its twin, held
to it by ``tests/fixtures/library_cases.json``.
"""

from __future__ import annotations

from bmlib.library.constants import (
    CORRUPT_SUFFIX,
    DIGEST_PREFIX_HEX,
    ICLOUD_PLACEHOLDER,
    IGNORED_NAMES,
    LIBRARY_FORMAT,
    LIBRARY_VERSION,
    MARKER_FILE,
    MAX_AUTHOR_CHARS_IN_NAME,
    MAX_FOLDER_NAME_BYTES,
    MAX_LABEL_CHARS,
    MAX_NAME_SUFFIX,
    MAX_TITLE_CHARS_IN_NAME,
    PAPERS_DIR,
    RECORD_FORMAT,
    RECORD_VERSION,
    RECORDS_DIR,
)
from bmlib.library.errors import (
    BadIdentifier,
    BadInstallation,
    BadName,
    DigestMismatch,
    FileClash,
    Io,
    LibraryError,
    Missing,
    NewerLibrary,
    NewerRecord,
    NotALibrary,
    NotDownloaded,
    ReadOnly,
    Unreachable,
)
from bmlib.library.files import sha256_hex
from bmlib.library.ids import (
    Installation,
    PaperIds,
    PaperInfo,
    SourceKind,
    canonical_ids,
)
from bmlib.library.library import Library, open_library
from bmlib.library.names import folder_name
from bmlib.library.records import (
    FileEntry,
    Record,
    RecordProblem,
    parse_record,
    render_record,
)
from bmlib.library.scan import (
    FileRef,
    PaperFolder,
    Problem,
    Scan,
    find,
    find_digest,
    group,
)

__all__ = [
    "sha256_hex",
    "group",
    "find_digest",
    "find",
    "Scan",
    "Problem",
    "PaperFolder",
    "FileRef",
    "BadIdentifier",
    "BadInstallation",
    "BadName",
    "CORRUPT_SUFFIX",
    "DIGEST_PREFIX_HEX",
    "DigestMismatch",
    "FileClash",
    "ICLOUD_PLACEHOLDER",
    "IGNORED_NAMES",
    "FileEntry",
    "Installation",
    "Io",
    "LIBRARY_FORMAT",
    "LIBRARY_VERSION",
    "Library",
    "LibraryError",
    "MARKER_FILE",
    "MAX_AUTHOR_CHARS_IN_NAME",
    "MAX_FOLDER_NAME_BYTES",
    "MAX_LABEL_CHARS",
    "MAX_NAME_SUFFIX",
    "MAX_TITLE_CHARS_IN_NAME",
    "Missing",
    "NewerLibrary",
    "NewerRecord",
    "NotALibrary",
    "NotDownloaded",
    "PAPERS_DIR",
    "PaperIds",
    "PaperInfo",
    "RECORDS_DIR",
    "Record",
    "RecordProblem",
    "RECORD_FORMAT",
    "RECORD_VERSION",
    "ReadOnly",
    "SourceKind",
    "Unreachable",
    "canonical_ids",
    "folder_name",
    "open_library",
    "parse_record",
    "render_record",
]
