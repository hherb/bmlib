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
* Windows: ``~/AppData/Local/bmlib/fulltext_cache``, falling back to
  ``~/.cache/bmlib/fulltext_cache`` when that directory does not exist

Every one of those is built from ``Path.home()``; no environment variable is
read, so neither ``XDG_CACHE_HOME`` nor ``%LOCALAPPDATA%`` is honoured. That
matters beyond pedantry: ``Path.home()`` raises ``RuntimeError`` — not
``OSError`` — where there is no ``HOME`` and no passwd entry, which is why
``service._default_cache()`` catches both. Pass ``cache_dir`` to skip the
call entirely.
"""

from __future__ import annotations

import hashlib
import logging
import os
import platform
import re
import shutil
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

PDF_MAGIC_BYTES = b"%PDF"

# Identifiers made up solely of these characters are used as filenames
# verbatim; anything else (a raw DOI contains "/") is sanitized first.
_SAFE_IDENTIFIER_RE = re.compile(r"[\w.\-]+")

# Ceiling on the readable part of a cache filename. The whole name has to fit
# in NAME_MAX (255 on ext4 and APFS) *with room to spare*, because the name
# actually created first is :func:`_atomic_write`'s temporary one, which adds
# 38 characters. Without this cap a long identifier is not merely un-cacheable
# — it fails a write that a bare ``write_text`` would have completed, and that
# per-article fault then trips ``FullTextService``'s once-per-service
# "nothing is being cached" warning, which is both untrue and permanently
# silences the directory-wide fault the warning exists to report. 160 leaves
# the longest name this module can build at 214 characters.
_MAX_PREFIX_CHARS = 160


def sanitize_identifier(raw: str) -> str:
    """Turn a DOI or other identifier into a safe, collision-free filename.

    A readable prefix is kept for debuggability, but because many distinct
    identifiers sanitise to the same string (every character outside
    ``[\\w.\\-]`` maps to ``_``), a short hash of the *raw* identifier is
    appended so two different identifiers can never share a cache file.

    The prefix is truncated to :data:`_MAX_PREFIX_CHARS`; it is only there to
    be read, and the hash — taken over the *whole* raw identifier — is what
    carries the collision guarantee, so shortening it costs nothing.
    """
    safe = re.sub(r"[^\w.\-]", "_", raw)[:_MAX_PREFIX_CHARS]
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

    An over-long identifier is sanitized even when its characters are safe,
    since the pass-through is what would otherwise carry it past
    :data:`_MAX_PREFIX_CHARS`.
    """
    if _SAFE_IDENTIFIER_RE.fullmatch(identifier) and len(identifier) <= _MAX_PREFIX_CHARS:
        return identifier
    return sanitize_identifier(identifier)


def _atomic_write(path: Path, data: bytes) -> None:
    """Write *data* to *path* so no partial file is ever visible under it.

    The bytes go to a uniquely-named temporary file beside the target — in the
    target's own directory, so the two are always on one filesystem — and are
    published with :func:`os.replace`, which is atomic within a filesystem.
    A write that fails partway therefore leaves the cache untouched — either
    the previous entry or nothing — instead of a truncated file that decodes
    perfectly and is served as a complete article forever after.

    Four details are load-bearing:

    * **The fsync is not durability theatre.** Under delayed allocation the
      ``write(2)`` that ``flush()`` issues *returns success* on a disk that is
      about to fill; the blocks are allocated at writeback and ENOSPC is
      reported to userspace only at ``fsync``. Without it ``os.replace``
      publishes a file whose blocks were never written. The ``flush()`` is
      needed too, and for a different reason: ``os.fsync`` acts on the
      descriptor, so anything still sitting in Python's ``BufferedWriter``
      would not be covered by it.
    * **The temporary name carries a UUID**, and the reason is not that two
      processes would interleave into one file — ``O_EXCL`` below already
      prevents that. It is that the loser of that race runs the cleanup
      handler, which would ``unlink`` the *winner's* in-flight temporary file;
      the winner's ``os.replace`` then fails with ``FileNotFoundError`` and
      both processes end up having cached nothing. The name is dot-prefixed so
      a leftover from a killed process is unobtrusive;
      :meth:`FullTextCache.clear` removes them.
    * **The mode is 0666 filtered by the umask** — byte for byte what an
      ordinary ``write_bytes`` requests, and deliberately not
      :func:`tempfile.mkstemp`'s 0600. A cache directory shared between users
      otherwise breaks silently: the second user cannot read what the first
      cached. Requesting 0644 here would be the same bug in miniature, since
      it drops the group-write bit that a umask of 002 grants.
    * **The cleanup must not speak over the error it is cleaning up after.**
      ``missing_ok=True`` covers only ``ENOENT``; an unlink that fails for any
      other reason (a read-only mount reports ``EROFS`` before it even looks
      the name up) would otherwise replace the original exception, and that
      exception is what ``FullTextService`` interpolates into the one warning
      an operator ever sees — reporting a full disk as a permissions problem.

    Raises:
        OSError: whatever the underlying write raised. :meth:`save_html` and
            :meth:`save_pdf` propagate it; both of ``FullTextService``'s cache
            writes already report a failed write, and a caller that cannot
            write is better told than left believing the article was cached.
    """
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    # O_BINARY is Windows-only and absent elsewhere. Without it os.open hands
    # the flags to the CRT, which defaults to text mode, and os.fdopen(fd,
    # "wb") cannot undo that — only io.FileIO's path-opening branch adds the
    # flag, which is why the bare write_bytes this replaced was binary-safe.
    # Every LF in a cached PDF would otherwise be written as CRLF.
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(tmp, flags, 0o666)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            logger.debug("Could not remove the temporary cache file %s", tmp, exc_info=True)
        raise


def _is_readable(path: Path) -> bool:
    """Report whether a cache entry can still be read back.

    Read the way the entry's own getter reads it, since the two ways an entry
    goes bad surface differently: an HTML file truncated mid-multibyte-sequence
    opens perfectly and fails on the *decode*, while an entry the process
    cannot get at at all — wrong permissions, an I/O fault, a directory
    standing where the file should be — fails on the open.
    """
    try:
        if path.suffix == ".html":
            path.read_text(encoding="utf-8")
        else:
            with path.open("rb"):
                pass
    except (OSError, UnicodeDecodeError):
        return False
    return True


def _remove(path: Path) -> None:
    """Remove a cache entry, whatever shape it turned out to be.

    ``unlink`` alone is not enough. An entry is normally a regular file, but
    the cache is a directory on a filesystem other things can touch, and an
    entry that is *not* a file is precisely the corrupt case an operator needs
    to clear: the old ``if path.is_file()`` in :meth:`FullTextCache.clear`
    skipped it silently while :meth:`FullTextCache.delete` raised on it, so
    both of the documented ways to remove a bad entry failed on the same one.
    """
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
        return
    path.unlink(missing_ok=True)


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
        ``~/AppData/Local/bmlib/fulltext_cache`` (Windows) — see
        :func:`_default_cache_dir`.

    Raises
    ------
    OSError
        If any of the three directories cannot be created — a file standing
        where one should be, a read-only parent, a full disk.
    RuntimeError
        From ``Path.home()`` when ``cache_dir`` is omitted and no home
        directory can be determined.

    Notes
    -----
    Both are raised, deliberately, rather than degraded: a caller who
    constructs a cache asked for one specifically, and an object whose every
    method then failed one at a time would be worse than failing once here.
    ``FullTextService`` does degrade when it builds this default itself —
    ``service._default_cache()`` enumerates what these raise, so a fourth
    ``mkdir`` here wants a matching edit there.
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

        The file is published atomically, so a write that fails partway leaves
        no half-written PDF behind — see :func:`_atomic_write`.

        Returns the file path on success, or ``None`` if the data is not a
        valid PDF.

        Raises:
            OSError: if the write itself fails — a full disk, a read-only
                directory. A bare ``write_bytes`` under delayed allocation
                returned a path in exactly that case and left a truncated
                file, so this is a real change for a direct caller;
                ``FullTextService`` catches it and reports it.
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

        Raises:
            OSError: if the write itself fails — a full disk, a read-only
                directory. A bare ``write_text`` under delayed allocation
                returned a path in exactly that case and left a truncated
                file, so this is a real change for a direct caller;
                ``FullTextService`` catches it and reports it.
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

    def quarantine(self, identifier: str) -> list[str]:
        """Move any unreadable entry for *identifier* out of the lookup path.

        An entry corrupted by something outside bmlib is not deleted — a
        failed re-fetch should leave the evidence — but leaving it *in place*
        is not viable either. A cached HTML file that cannot be decoded is
        consulted before the PDF, so it hides a perfectly good PDF entry
        behind it, and every later run repeats the same warning and the same
        network fetch, forever. Renaming it aside satisfies both: the next
        lookup is a clean miss the retrieval chain can heal, and the bytes are
        still there under a ``.corrupt`` suffix for an operator to inspect.
        :meth:`clear` sweeps them up.

        Only entries that genuinely fail to read are moved; a readable one
        beside a corrupt one is left alone. Best-effort throughout — this runs
        while another failure is already being handled, so a rename that
        cannot proceed must not become the error the caller sees.

        Returns:
            The paths moved aside, in the order they were checked.
        """
        moved: list[str] = []
        name = _safe_filename(identifier)
        for path in (self._html_dir / f"{name}.html", self._pdf_dir / f"{name}.pdf"):
            if not path.exists() or _is_readable(path):
                continue
            aside = path.with_name(f"{path.name}.corrupt")
            try:
                os.replace(path, aside)
            except OSError:
                logger.debug("Could not move the unreadable entry %s aside", path, exc_info=True)
                continue
            logger.warning("Moved the unreadable cache entry %s aside to %s", path, aside)
            moved.append(str(aside))
        return moved

    def delete(self, identifier: str) -> None:
        """Delete all cached files for *identifier* (PDF and HTML)."""
        name = _safe_filename(identifier)
        for ext, directory in [(".pdf", self._pdf_dir), (".html", self._html_dir)]:
            _remove(directory / f"{name}{ext}")

    def clear(self) -> None:
        """Remove all cached files, including quarantined and temporary ones."""
        for directory in (self._pdf_dir, self._html_dir):
            for path in directory.iterdir():
                _remove(path)
        logger.info("Cleared full-text cache at %s", self.cache_dir)
