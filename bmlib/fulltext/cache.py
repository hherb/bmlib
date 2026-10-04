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

Caches retrieved full-text content on disk, organised into ``pdfs/``,
``html/`` and ``abstracts/`` subdirectories under a user-configurable root.  The default
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

The two *rendered* entries — ``html/`` and ``abstracts/``, which hold
:meth:`~bmlib.fulltext.jats_parser.JATSParser.to_html` output — open with a
one-line stamp naming the renderer that wrote them
(``<!-- bmlib-fulltext-renderer: N -->``), and one written by an older renderer
reads as absent (#172). A PDF is the publisher's bytes, not a rendering, and is
not stamped.
"""

from __future__ import annotations

import hashlib
import logging
import os
import platform
import re
import shutil
from pathlib import Path

from bmlib._atomic import atomic_write
from bmlib.fulltext.jats_parser import RENDERER_VERSION

logger = logging.getLogger(__name__)

PDF_MAGIC_BYTES = b"%PDF"

# Identifiers made up solely of these characters are used as filenames
# verbatim; anything else (a raw DOI contains "/") is sanitized first.
_SAFE_IDENTIFIER_RE = re.compile(r"[\w.\-]+")

# Ceiling on the readable part of a cache filename. The whole name has to fit
# in NAME_MAX (255 on ext4 and APFS) *with room to spare*, because the name
# actually created first is :func:`~bmlib._atomic.atomic_write`'s temporary
# one, which adds 38 characters — that helper's docstring states the same
# figure, so a change to the temporary name's shape has to be carried here.
# Without this cap a long identifier is not merely un-cacheable — it fails a
# write that a bare ``write_text`` would have completed, and that
# per-article fault then trips ``FullTextService``'s once-per-service
# "nothing is being cached" warning, which is both untrue and permanently
# silences the directory-wide fault the warning exists to report. 160 leaves
# the longest name this module can build at 214 characters.
_MAX_PREFIX_CHARS = 160

# The longest key :func:`sanitize_identifier` returns: the prefix, ``_`` and a
# 10-character digest. This, not :data:`_MAX_PREFIX_CHARS`, is the bound the
# pass-through in :func:`_safe_filename` needs. Bounded at the prefix alone,
# every key the service computed for a raw identifier of 150 characters or
# more was hashed a second time, so the file written was not the documented
# ``sanitize_identifier(identifier)`` and a lookup by that key missed it
# (#309). The 214 above was always computed over this length.
_MAX_KEY_CHARS = _MAX_PREFIX_CHARS + 11

# The first line of every rendered entry. An HTML comment, so a reader opening
# the file directly still has a valid document; a line of its own, so it is
# found without parsing anything. The spelling is an on-disk format — the Rust
# port reads the same layout — and the tests state it literally.
_STAMP_PREFIX = "<!-- bmlib-fulltext-renderer: "
_STAMP_SUFFIX = " -->"
_STAMP_RE = re.compile(re.escape(_STAMP_PREFIX) + r"(\d+)" + re.escape(_STAMP_SUFFIX))


def _stamped(html: str) -> bytes:
    """*html* as a rendered entry is written: the current stamp, then the HTML."""
    return f"{_STAMP_PREFIX}{RENDERER_VERSION}{_STAMP_SUFFIX}\n{html}".encode()


def _split_stamp(text: str) -> tuple[int | None, str]:
    """Return the renderer version a rendered entry names, and its HTML.

    ``None`` for an entry with no readable stamp on its first line — every
    entry a bmlib before the stamp wrote, and anything else that is not
    exactly the stamp — which is older than any stamped version.
    """
    first, newline, rest = text.partition("\n")
    match = _STAMP_RE.fullmatch(first)
    if match is None or not newline:
        return None, text
    return int(match.group(1)), rest


def _is_stale(version: int | None) -> bool:
    """Whether an entry stamped *version* predates the running renderer.

    Older only, never merely different: an entry from a *newer* renderer is
    served. Two bmlib versions sharing a cache directory — or this library and
    a port that lags it — would otherwise each read the other's entries as
    stale and replace them, re-fetching for ever; under "older" they converge
    on the newer rendering.
    """
    return version is None or version < RENDERER_VERSION


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
    :data:`_MAX_KEY_CHARS` — which is the length of the longest key
    :func:`sanitize_identifier` returns, so that every such key passes
    through and is never hashed twice.
    """
    if _SAFE_IDENTIFIER_RE.fullmatch(identifier) and len(identifier) <= _MAX_KEY_CHARS:
        return identifier
    return sanitize_identifier(identifier)


def _is_readable(path: Path) -> bool:
    """Report whether a cache entry can still be read back.

    Read the way the entry's own getter reads it, since the two ways an entry
    goes bad surface differently: an HTML file (or an abstract, which is
    HTML too) truncated mid-multibyte-sequence
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
        # ``abstracts/`` is created by the first save_abstract(), not here: a
        # cache an earlier bmlib built has no such directory, and creating it
        # at construction would make a read-only cache that serves hits today
        # raise instead.

    @property
    def _pdf_dir(self) -> Path:
        return self.cache_dir / "pdfs"

    @property
    def _html_dir(self) -> Path:
        return self.cache_dir / "html"

    @property
    def _abstract_dir(self) -> Path:
        return self.cache_dir / "abstracts"

    def _entries(self, identifier: str) -> tuple[Path, Path, Path]:
        """Every path an entry for *identifier* can occupy, HTML first."""
        name = _safe_filename(identifier)
        return (
            self._html_dir / f"{name}.html",
            self._pdf_dir / f"{name}.pdf",
            self._abstract_dir / f"{name}.html",
        )

    def _rendered_entries(self, identifier: str) -> tuple[Path, Path]:
        """The two stamped entries for *identifier*: the HTML, then the abstract."""
        html, _pdf, abstract = self._entries(identifier)
        return html, abstract

    @staticmethod
    def _read_rendered(path: Path) -> str | None:
        """Read a rendered entry back, or ``None`` if absent or stale.

        Raises:
            OSError: If the entry exists and cannot be read.
            UnicodeDecodeError: If it cannot be decoded.
        """
        if not path.exists():
            return None
        version, html = _split_stamp(path.read_text(encoding="utf-8"))
        if _is_stale(version):
            logger.debug("Cache entry %s was written by renderer %s; not served", path, version)
            return None
        return html

    # --- PDF operations -----------------------------------------------------

    def save_pdf(self, data: bytes, identifier: str) -> str | None:
        """Save PDF data if it passes magic-byte validation.

        The file is published atomically, so a write that fails partway leaves
        no half-written PDF behind — see :func:`~bmlib._atomic.atomic_write`.

        Returns the file path on success, or ``None`` if the data is not a
        valid PDF.

        The rejection is logged at DEBUG, not WARNING: ``FullTextService``
        owns reporting this outcome and does so once per ``(tier, cause)``,
        because the population it happens in was measured at 64.3% — an
        Unpaywall URL that resolves to a landing page rather than a PDF is
        ordinary, not exceptional. A WARNING here defeated that one-shot,
        emitting a line per article for the very cause the measurement
        selected the one-shot for. A direct caller still has the ``None``.

        Raises:
            OSError: if the write itself fails — a full disk, a read-only
                directory. A bare ``write_bytes`` under delayed allocation
                returned a path in exactly that case and left a truncated
                file, so this is a real change for a direct caller;
                ``FullTextService`` catches it and reports it.
        """
        if len(data) < len(PDF_MAGIC_BYTES) or data[: len(PDF_MAGIC_BYTES)] != PDF_MAGIC_BYTES:
            logger.debug("Rejected non-PDF data for %s", identifier)
            return None
        path = self._pdf_dir / f"{_safe_filename(identifier)}.pdf"
        atomic_write(path, data)
        logger.info("Cached PDF for %s (%d bytes)", identifier, len(data))
        return str(path)

    def get_pdf(self, identifier: str) -> str | None:
        """Return the cached PDF file path, or ``None`` if not cached.

        The entry is opened before its path is returned, as :meth:`get_html`
        reads its own. Testing only that the path exists returned a directory
        standing where the PDF should be as a cached PDF (#309): the service's
        extraction swallowed the failure, so nothing reached the guard that
        quarantines an unreadable entry, and the same bogus hit was served on
        every later run.

        Raises:
            OSError: If an entry exists and cannot be opened — a directory in
                its place, wrong permissions, an I/O fault. The magic bytes
                are not checked: :meth:`save_pdf` validated them and
                published the file atomically.
        """
        path = self._pdf_dir / f"{_safe_filename(identifier)}.pdf"
        if not path.exists():
            return None
        with path.open("rb"):
            pass
        return str(path)

    # --- HTML operations ----------------------------------------------------

    def save_html(self, html: str, identifier: str) -> str:
        """Save parsed HTML full text to the cache.

        The file is published atomically, so a write that fails partway
        leaves no half-written article behind — see :func:`~bmlib._atomic.atomic_write`.
        It opens with the current renderer stamp (see the module docstring),
        which :meth:`get_html` strips again.

        Returns the file path.

        Raises:
            OSError: if the write itself fails — a full disk, a read-only
                directory. A bare ``write_text`` under delayed allocation
                returned a path in exactly that case and left a truncated
                file, so this is a real change for a direct caller;
                ``FullTextService`` catches it and reports it.
        """
        path = self._html_dir / f"{_safe_filename(identifier)}.html"
        atomic_write(path, _stamped(html))
        logger.info("Cached HTML for %s (%d chars)", identifier, len(html))
        return str(path)

    def get_html(self, identifier: str) -> str | None:
        """Return the cached HTML content, or ``None`` if not cached.

        An entry written by an older renderer, or carrying no stamp, is
        ``None`` too (#172): it decodes cleanly and looks right, which is why
        it was served for ever before the stamp existed. It is left on disk;
        :meth:`discard_stale` removes it.

        Raises:
            OSError: If an entry exists and cannot be read.
            UnicodeDecodeError: If it cannot be decoded.
        """
        return self._read_rendered(self._html_dir / f"{_safe_filename(identifier)}.html")

    # --- Abstract operations ------------------------------------------------

    def save_abstract(self, html: str, identifier: str) -> str:
        """Save the abstract a cached PDF was returned with.

        :class:`~bmlib.fulltext.service.FullTextService` holds a body-less
        JATS rendering back as a last resort and pairs it with a PDF that
        yields no text; it saves the abstract here whenever a PDF was cached
        with one held back, whether or not the PDF yielded text that time.
        Once the PDF is cached the retrieval chain never runs again for that
        identifier, so without this entry every later hit that yields no text
        lost the abstract the first call returned (#305). It lives in a directory
        of its own because ``html/`` is served as full text, and it is read
        only beside a cached PDF — never as a hit on its own.

        Published atomically, like the other two entries, and stamped with
        the renderer like the HTML entry. The directory is
        created here rather than at construction, so a cache built by an
        earlier bmlib — possibly read-only — still constructs.

        Returns:
            The file path.

        Raises:
            OSError: If the directory cannot be created or the write fails.
        """
        path = self._abstract_dir / f"{_safe_filename(identifier)}.html"
        self._abstract_dir.mkdir(exist_ok=True)
        atomic_write(path, _stamped(html))
        logger.info("Cached the abstract for %s (%d chars)", identifier, len(html))
        return str(path)

    def get_abstract(self, identifier: str) -> str | None:
        """Return the abstract cached beside a PDF, or ``None`` if there is none.

        Stale exactly as :meth:`get_html` is: one written by an older
        renderer is ``None``.

        Raises:
            OSError: If an entry exists and cannot be read.
            UnicodeDecodeError: If it cannot be decoded — the two ways
                :meth:`get_html` fails, so the service's read guard moves it
                aside in the same way.
        """
        return self._read_rendered(self._abstract_dir / f"{_safe_filename(identifier)}.html")

    # --- Shared operations --------------------------------------------------

    def discard_stale(self, identifier: str) -> list[str]:
        """Remove every rendered entry for *identifier* an older renderer wrote.

        Reading such an entry as absent is not enough on its own, which is why
        this exists beside :meth:`get_html`. ``FullTextService`` consults the
        HTML entry, then the PDF, then — only on a PDF hit that yields no text
        — the abstract, so a stale HTML entry beside a cached PDF would fall
        through to the PDF for good, and a stale abstract beside a PDF hit
        would never be rendered again at all, a PDF hit ending the retrieval
        chain. The service therefore discards and treats the article as a
        miss; once these are gone nothing stale is left, so it happens once.

        Deleted, not moved aside as :meth:`quarantine` moves a corrupt entry:
        a corrupt entry is evidence of a fault, while a stale one is an
        ordinary rendering of an ordinary document, and nothing in bmlib will
        serve it again. A current or *newer* entry beside a stale one is left
        alone, as is the PDF, which is not a rendering.

        Each removal is logged at INFO, the level of a cache hit: after an
        upgrade every rendered entry is stale, and this is the line saying why
        the corpus is being fetched again.

        Returns:
            The paths removed, HTML first.

        Raises:
            OSError: If an entry exists and cannot be read.
            UnicodeDecodeError: If one cannot be decoded. An undecodable entry
                is :meth:`quarantine`'s case, and reading it as stale would
                delete the bytes that path keeps; raised exactly as
                :meth:`get_html` raises, it reaches the service's read guard
                and is moved aside instead.
        """
        removed: list[str] = []
        for path in self._rendered_entries(identifier):
            if not path.exists():
                continue
            version, _ = _split_stamp(path.read_text(encoding="utf-8"))
            if not _is_stale(version):
                continue
            _remove(path)
            logger.info(
                "Discarded the cache entry %s: written by %s, older than %s; "
                "it will be re-fetched.",
                path,
                "a bmlib that predates the stamp" if version is None else f"renderer {version}",
                RENDERER_VERSION,
            )
            removed.append(str(path))
        return removed

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
        for path in self._entries(identifier):
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
        """Delete all cached files for *identifier* (HTML, PDF and abstract)."""
        for path in self._entries(identifier):
            _remove(path)

    def clear(self) -> None:
        """Remove all cached files, including quarantined and temporary ones.

        A subdirectory that is absent is skipped: ``abstracts/`` exists only
        once something has been saved there, so an older cache lacks it.
        Every subdirectory is skipped alike, where a missing ``pdfs/`` or
        ``html/`` used to raise ``FileNotFoundError``.
        """
        for directory in (self._pdf_dir, self._html_dir, self._abstract_dir):
            if not directory.is_dir():
                continue
            for path in directory.iterdir():
                _remove(path)
        logger.info("Cleared full-text cache at %s", self.cache_dir)
