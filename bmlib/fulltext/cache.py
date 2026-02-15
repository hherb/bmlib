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

"""Local cache for downloaded full-text articles (PDFs and HTML).

Caches retrieved full-text content on disk, organised into ``pdfs/`` and
``html/`` subdirectories under a user-configurable root.  The default
location follows the XDG convention:

* macOS: ``~/Library/Caches/bmlib/fulltext_cache``
* Linux: ``~/.cache/bmlib/fulltext_cache``
* Windows: ``%LOCALAPPDATA%/bmlib/fulltext_cache``
"""

from __future__ import annotations

import logging
import platform
from pathlib import Path

logger = logging.getLogger(__name__)

PDF_MAGIC_BYTES = b"%PDF"


def _default_cache_dir() -> Path:
    """Return a platform-appropriate default cache directory."""
    system = platform.system()
    if system == "Darwin":
        base = Path.home() / "Library" / "Caches"
    elif system == "Windows":
        local = Path.home() / "AppData" / "Local"
        base = local if local.exists() else Path.home() / ".cache"
    else:
        # Linux / other — follow XDG_CACHE_HOME
        xdg = Path.home() / ".cache"
        base = xdg
    return base / "bmlib" / "fulltext_cache"


class FullTextCache:
    """Disk cache for downloaded PDFs and parsed HTML full texts.

    Parameters
    ----------
    cache_dir:
        Root directory for cached files.  Defaults to a platform-appropriate
        location under ``~/Library/Caches/bmlib/fulltext_cache`` (macOS),
        ``~/.cache/bmlib/fulltext_cache`` (Linux), or
        ``%LOCALAPPDATA%/bmlib/fulltext_cache`` (Windows).
    """

    def __init__(self, cache_dir: str | Path | None = None) -> None:
        if cache_dir is None:
            self.cache_dir = _default_cache_dir()
        else:
            self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._pdf_dir.mkdir(parents=True, exist_ok=True)
        self._html_dir.mkdir(parents=True, exist_ok=True)

    @property
    def _pdf_dir(self) -> Path:
        return self.cache_dir / "pdfs"

    @property
    def _html_dir(self) -> Path:
        return self.cache_dir / "html"

    # --- PDF operations -----------------------------------------------------

    def save_pdf(self, data: bytes, identifier: str) -> str | None:
        """Save PDF data if it passes magic-byte validation.

        Returns the file path on success, or ``None`` if the data is not a
        valid PDF.
        """
        if len(data) < len(PDF_MAGIC_BYTES) or data[: len(PDF_MAGIC_BYTES)] != PDF_MAGIC_BYTES:
            logger.warning("Rejected non-PDF data for %s", identifier)
            return None
        path = self._pdf_dir / f"{identifier}.pdf"
        path.write_bytes(data)
        logger.info("Cached PDF for %s (%d bytes)", identifier, len(data))
        return str(path)

    def get_pdf(self, identifier: str) -> str | None:
        """Return the cached PDF file path, or ``None`` if not cached."""
        path = self._pdf_dir / f"{identifier}.pdf"
        return str(path) if path.exists() else None

    # --- HTML operations ----------------------------------------------------

    def save_html(self, html: str, identifier: str) -> str:
        """Save parsed HTML full text to the cache.

        Returns the file path.
        """
        path = self._html_dir / f"{identifier}.html"
        path.write_text(html, encoding="utf-8")
        logger.info("Cached HTML for %s (%d chars)", identifier, len(html))
        return str(path)

    def get_html(self, identifier: str) -> str | None:
        """Return the cached HTML content, or ``None`` if not cached."""
        path = self._html_dir / f"{identifier}.html"
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    # --- Shared operations --------------------------------------------------

    def delete(self, identifier: str) -> None:
        """Delete all cached files for *identifier* (PDF and HTML)."""
        for ext, directory in [(".pdf", self._pdf_dir), (".html", self._html_dir)]:
            path = directory / f"{identifier}{ext}"
            path.unlink(missing_ok=True)

    def clear(self) -> None:
        """Remove all cached files."""
        for directory in (self._pdf_dir, self._html_dir):
            for path in directory.iterdir():
                if path.is_file():
                    path.unlink()
        logger.info("Cleared full-text cache at %s", self.cache_dir)
