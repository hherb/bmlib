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

"""One canonical text per paper, built from JATS, Markdown or plain text.

See :mod:`bmlib.fulltext.canonical.model` for the layout and the offset unit.
"""

from bmlib.fulltext.canonical.jats import (
    JATS_CANONICAL_VERSION,
    canonical_from_jats,
    strip_links,
)
from bmlib.fulltext.canonical.model import (
    BLOCK_SEPARATOR,
    CELL_SEPARATOR,
    TOP_LEVEL,
    BlockDraft,
    BlockKind,
    CanonicalBlock,
    CanonicalSection,
    CanonicalText,
    SectionDraft,
    SourceFormat,
    TextSpan,
    assemble,
    is_white_space,
    normalise_whitespace,
)
from bmlib.fulltext.canonical.plain import (
    PLAIN_TEXT_VERSION,
    canonical_from_plain_text,
    split_lines,
)

__all__ = [
    "BLOCK_SEPARATOR",
    "CELL_SEPARATOR",
    "JATS_CANONICAL_VERSION",
    "PLAIN_TEXT_VERSION",
    "TOP_LEVEL",
    "BlockDraft",
    "BlockKind",
    "CanonicalBlock",
    "CanonicalSection",
    "CanonicalText",
    "SectionDraft",
    "SourceFormat",
    "TextSpan",
    "assemble",
    "canonical_from_jats",
    "canonical_from_plain_text",
    "is_white_space",
    "normalise_whitespace",
    "split_lines",
    "strip_links",
]
