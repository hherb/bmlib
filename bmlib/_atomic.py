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

"""Publish a file so no partial version of it is ever visible.

Private to bmlib — the leading underscore is on the module, so the one name
it exports does not carry a second one. Nothing here is part of the public
API and no release note covers it.

It lives at the top level rather than inside the one package that first
needed it because two packages now write user-visible files, and the four
details :func:`atomic_write` documents were each earned by the review of
issue #70 — the kind of knowledge that must not exist in two copies free to
drift apart. ``scripts/_sampling.py`` is the same move for the samplers'
pacing rules.

Depends on the standard library alone, so importing it costs a caller
nothing and adds no extra to any dependency group.
"""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)


def atomic_write(path: Path, data: bytes) -> None:
    """Write *data* to *path* so no partial file is ever visible under it.

    The bytes go to a uniquely-named temporary file beside the target — in the
    target's own directory, so the two are always on one filesystem — and are
    published with :func:`os.replace`, which is atomic within a filesystem.
    A write that fails partway therefore leaves the target untouched — either
    the previous version or nothing — instead of a truncated file that reads
    back perfectly and is trusted forever after.

    "Atomic" here is about *visibility*, not crash durability. The data is
    fsync'd before the rename is issued, so there is no ordering in which the
    rename survives a crash and the data does not; but the containing
    directory is not fsync'd, so the rename itself can be lost. That is the
    safe direction for both callers — the target is then simply absent, which
    each treats as a miss and repairs — and closing the window would cost a
    directory sync per write to fix something neither caller can observe. A
    caller that must survive a crash with the file *present* has to fsync the
    directory itself.

    :func:`os.replace` replaces whatever is at *path*, **including a symlink**
    — the link itself, not the file it points at. A caller for whom a symlink
    there is a user's deliberate indirection has to look for one before
    calling; :meth:`~bmlib.templates.engine.TemplateEngine.install_defaults`
    does, and its reason is worth reading before a third caller is added.

    That temporary name is **38 characters longer** than the target's, so a
    caller building filenames from unbounded input has to leave room for it
    inside ``NAME_MAX``; ``fulltext.cache._MAX_PREFIX_CHARS`` is the cap that
    does, and it states the same figure. That cap has 41 characters of slack,
    so growing the affix would not fail its test — ``test_atomic.py`` asserts
    the 38 directly, which is the only guard that sees the two drift apart.

    Five details are load-bearing:

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
      both processes end up having written nothing. The name is dot-prefixed
      so a leftover from a killed process — this function unlinks its own on
      any failure, but cannot on ``SIGKILL`` or a power loss — is
      unobtrusive. Nothing sweeps them centrally:
      :meth:`~bmlib.fulltext.cache.FullTextCache.clear` removes the ones
      under the cache, and a caller writing elsewhere either tidies up or
      accepts that a hidden file may be left behind. That is safe for
      ``templates``, whose only directory scan reads the *default*
      directory, not the one written to.
    * **The mode is 0666 filtered by the umask** — byte for byte what an
      ordinary ``write_bytes`` requests, and deliberately not
      :func:`tempfile.mkstemp`'s 0600. A directory shared between users
      otherwise breaks silently: the second user cannot read what the first
      wrote. Requesting 0644 here would be the same bug in miniature, since
      it drops the group-write bit that a umask of 002 grants.
    * **The cleanup must not speak over the error it is cleaning up after.**
      ``missing_ok=True`` covers only ``ENOENT``; an unlink that fails for any
      other reason (a read-only mount reports ``EROFS`` before it even looks
      the name up) would otherwise replace the original exception, and that
      exception is what ``FullTextService`` interpolates into the one warning
      an operator ever sees — reporting a full disk as a permissions problem.
      (The ``templates`` caller simply propagates, so there the cost is a
      wrong traceback rather than a wrong warning.)
    * **The failure names the file the caller asked for.** The syscall that
      fails operates on the *temporary* name, so that is what lands in
      ``OSError.filename`` — a path the caller never chose and that the
      cleanup above has just removed, so an operator who greps for it finds
      nothing on disk. At ``fsync``, the failure this function is built
      around, there is no filename at all. Both cases are re-pointed at
      *path*, which is the honest answer to "what could not be written". The
      two-file form :func:`os.replace` raises already names the target in
      ``filename2`` and is left alone, which is what ``filename2 is None``
      tests for. This is not cosmetic: ``str(exc)`` is built from
      ``filename``, and ``str(exc)`` is what ``FullTextService``
      interpolates into that one warning.

    Raises:
        OSError: whatever the underlying write raised, re-pointed at *path*
            as above. Every caller propagates it; a caller that cannot write
            is better told than left believing the file is there.
    """
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    # O_BINARY is Windows-only and absent elsewhere. Without it os.open hands
    # the flags to the CRT, which defaults to text mode, and no mode string
    # passed to os.fdopen can undo that — only msvcrt.setmode could, at the
    # cost of a Windows-only import. io.FileIO adds the flag in its
    # path-opening branch and not in the branch that adopts a descriptor,
    # which is why an ordinary Path.write_bytes is binary-safe and a raw
    # os.open is not: every LF in a PDF would be written as CRLF. The CI
    # matrix is Linux-only, where the getattr is 0, so nothing here exercises
    # this — test_a_template_is_copied_byte_for_byte would catch it on
    # Windows.
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(tmp, flags, 0o666)
        try:
            handle = os.fdopen(fd, "wb")
        except BaseException:
            # os.fdopen adopts the descriptor only once it succeeds, so a
            # failure here leaves it open with nothing left holding it.
            os.close(fd)
            raise
        with handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException as exc:
        if isinstance(exc, OSError) and exc.filename2 is None:
            exc.filename = str(path)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            logger.debug("Could not remove the temporary file %s", tmp, exc_info=True)
        raise
