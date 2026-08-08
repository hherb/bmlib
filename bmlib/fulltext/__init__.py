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

"""Full-text retrieval and JATS XML parsing for biomedical literature.

Everything here is importable with core bmlib alone. Only
:class:`FullTextService` needs the ``bmlib[fulltext]`` extra, and it is
resolved on first use — see :func:`__getattr__` below.
"""

from typing import TYPE_CHECKING, Any

from bmlib.fulltext.cache import FullTextCache
from bmlib.fulltext.jats_parser import JATSParser
from bmlib.fulltext.models import (
    ContentKind,
    FullTextResult,
    FullTextSourceEntry,
    JATSAbstractSection,
    JATSArticle,
    JATSAuthorInfo,
    JATSBodySection,
    JATSFigureInfo,
    JATSReferenceInfo,
    JATSTableInfo,
    Section,
    SectionType,
    SegmentedDocument,
    TextBlock,
)
from bmlib.fulltext.pdf_converter import (
    ConversionResult,
    LayoutExtractor,
    PDFConverter,
    PyMuPDFConverter,
    get_converter,
    list_converters,
    render_html,
)
from bmlib.fulltext.segmenter import SectionSegmenter

if TYPE_CHECKING:  # Names a type checker needs eagerly; see __getattr__ below.
    from bmlib.fulltext.service import FullTextError, FullTextService

#: Names living in ``service``, resolved on first access.
_LAZY_EXPORTS = frozenset({"FullTextError", "FullTextService"})


def __getattr__(name: str) -> Any:
    """Resolve the retrieval service on first use (:pep:`562`).

    Every other module in this package runs on the standard library — the
    JATS parser, the pure-dataclass models, the PDF converter (which loads
    PyMuPDF lazily) and the section segmenter. Only ``service`` makes an HTTP
    request, and re-exporting it eagerly gated all of them behind ``httpx``:
    importing a submodule imports its parent package first, so ``pip install
    bmlib`` left ten modules across two packages raising a bare
    ``ModuleNotFoundError`` — including :class:`SectionSegmenter`, which is
    documented as standalone, and the three publication fetchers, which merely
    borrow one dataclass from ``models``.

    Deferring keeps the claim true of the package and not merely of the
    modules, exactly as ``bmlib.context_processor`` does for its LLM-backed
    half.
    """
    if name in _LAZY_EXPORTS:
        from bmlib.fulltext import service

        return getattr(service, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """List the lazy exports too, which the default would omit."""
    return sorted(__all__)


__all__ = [
    "ContentKind",
    "ConversionResult",
    "FullTextCache",
    "FullTextError",
    "FullTextResult",
    "FullTextService",
    "FullTextSourceEntry",
    "JATSAbstractSection",
    "JATSArticle",
    "JATSAuthorInfo",
    "JATSBodySection",
    "JATSFigureInfo",
    "JATSParser",
    "JATSReferenceInfo",
    "JATSTableInfo",
    "LayoutExtractor",
    "PDFConverter",
    "PyMuPDFConverter",
    "Section",
    "SectionSegmenter",
    "SectionType",
    "SegmentedDocument",
    "TextBlock",
    "get_converter",
    "list_converters",
    "render_html",
]
