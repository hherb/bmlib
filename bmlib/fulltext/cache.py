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

import hashlib
import logging
import os
import platform
import re
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

PDF_MAGIC_BYTES = b"%PDF"

# Identifiers made up solely of these characters are used as filenames
# verbatim; anything else (a raw DOI contains "/") is sanitized first.
_SAFE_IDENTIFIER_RE = re.compile(r"[\w.\-]+")


def sanitize_identifier(raw: str) -> str:
    """Turn a DOI or other identifier into a safe, collision-free filename.

    A readable prefix is kept for debuggability, but because many distinct
    identifiers sanitise to the same string (every character outside
    ``[\\w.\\-]`` maps to ``_``), a short hash of the *raw* identifier is
    appended so two different identifiers can never share a cache file.
    """
    safe = re.sub(r"[^\w.\-]", "_", raw)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]
    return f"{safe}_{digest}"


def _safe_filename(identifier: str) -> str:
    """Return *identifier* if it is already filename-safe, else sanitize it.

    Already-safe identifiers (e.g. those pre-sanitized by
    :class:`~bmlib.fulltext.service.FullTextService`) pass through unchanged
    so existing cache files remain addressable; raw identifiers containing
    path separators or other unsafe characters are sanitized here as a
    defense in depth, so a direct caller passing a raw DOI cannot write
    outside the cache directory.
    """
    if _SAFE_IDENTIFIER_RE.fullmatch(identifier):
        return identifier
    return sanitize_identifier(identifier)


def _atomic_write(path: Path, data: bytes) -> None:
    """Write *data* to *path* so no partial file is ever visible under it.

    The bytes go to a uniquely-named temporary file beside the target and are
    published with :func:`os.replace`, which is atomic within a filesystem.
    A write that fails partway therefore leaves the cache untouched — either
    the previous entry or nothing — instead of a truncated file that decodes
    perfectly and is served as a complete article forever after.

    Three details are load-bearing:

    * **The flush is not durability theatre.** Under delayed allocation a
      ``write()`` that will fail for want of space returns success, and the
      error surfaces at flush time; without ``fsync`` here, ``os.replace``
      would publish a file whose blocks were never written.
    * **The temporary name carries a UUID**, so two processes caching the
      same article cannot interleave into one temp file — which would
      manufacture exactly the corruption this function exists to prevent.
      It is dot-prefixed so a leftover from a killed process is unobtrusive;
      :meth:`FullTextCache.clear` removes them.
    * **The mode is 0644 filtered by the umask**, as an ordinary write would
      be, rather than :func:`tempfile.mkstemp`'s 0600. A cache directory
      shared between users otherwise breaks silently: the second user cannot
      read what the first cached.

    Raises:
        OSError: whatever the underlying write raised. Both call sites in
            :class:`~bmlib.fulltext.service.FullTextService` already report a
            failed cache write, and a caller that cannot write is better told
            than left believing the article was cached.
    """
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


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
        path = self._pdf_dir / f"{_safe_filename(identifier)}.pdf"
        _atomic_write(path, data)
        logger.info("Cached PDF for %s (%d bytes)", identifier, len(data))
        return str(path)

    def get_pdf(self, identifier: str) -> str | None:
        """Return the cached PDF file path, or ``None`` if not cached."""
        path = self._pdf_dir / f"{_safe_filename(identifier)}.pdf"
        return str(path) if path.exists() else None

    # --- HTML operations ----------------------------------------------------

    def save_html(self, html: str, identifier: str) -> str:
        """Save parsed HTML full text to the cache.

        The file is published atomically, so a write that fails partway
        leaves no half-written article behind — see :func:`_atomic_write`.

        Returns the file path.
        """
        path = self._html_dir / f"{_safe_filename(identifier)}.html"
        _atomic_write(path, html.encode("utf-8"))
        logger.info("Cached HTML for %s (%d chars)", identifier, len(html))
        return str(path)

    def get_html(self, identifier: str) -> str | None:
        """Return the cached HTML content, or ``None`` if not cached."""
        path = self._html_dir / f"{_safe_filename(identifier)}.html"
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    # --- Shared operations --------------------------------------------------

    def delete(self, identifier: str) -> None:
        """Delete all cached files for *identifier* (PDF and HTML)."""
        name = _safe_filename(identifier)
        for ext, directory in [(".pdf", self._pdf_dir), (".html", self._html_dir)]:
            path = directory / f"{name}{ext}"
            path.unlink(missing_ok=True)

    def clear(self) -> None:
        """Remove all cached files."""
        for directory in (self._pdf_dir, self._html_dir):
            for path in directory.iterdir():
                if path.is_file():
                    path.unlink()
        logger.info("Cleared full-text cache at %s", self.cache_dir)
