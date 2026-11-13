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

The pure, file-system-free foundation: identifiers, source kinds,
installations and folder names. Rust's ``bmlib::library`` is its twin, held
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
from bmlib.library.ids import (
    Installation,
    PaperIds,
    PaperInfo,
    SourceKind,
    canonical_ids,
)
from bmlib.library.names import folder_name
from bmlib.library.records import (
    FileEntry,
    Record,
    RecordProblem,
    parse_record,
    render_record,
)

__all__ = [
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
    "parse_record",
    "render_record",
]
