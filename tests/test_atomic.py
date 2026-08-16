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

"""Tests for bmlib._atomic.

The four details :func:`atomic_write` calls load-bearing are pinned at its
two call sites — ``test_fulltext_cache.py::TestWritesAreAtomic`` and
``test_templates.py::TestInstallingDefaultsIsAtomic`` — because that is where
the behaviour is delivered and where a regression would be felt. What is here
instead is the small set of guarantees that belong to *the helper itself*,
which no call site can see: an invariant a second module's arithmetic depends
on, and two failure paths whose only observable effect is on the exception
that comes back out.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from bmlib._atomic import atomic_write

TEMP_NAME_OVERHEAD = 38
"""What ``atomic_write`` adds to a target's filename, per its docstring."""


def _staged_name(target: Path, monkeypatch) -> str:
    """Return the temporary filename ``atomic_write`` publishes *target* from."""
    seen: list[str] = []
    real_replace = os.replace

    def watch(src, dst):
        seen.append(Path(src).name)
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", watch)
    atomic_write(target, b"payload")
    assert seen, "nothing was published through os.replace"
    return seen[0]


class TestTheTemporaryNameFitsTheBudgetCallersReserve:
    """``fulltext.cache._MAX_PREFIX_CHARS`` is arithmetic over this figure.

    That constant caps a cache filename so the temporary name built from it
    still fits in ``NAME_MAX``, and both it and :func:`atomic_write` state the
    overhead as 38. Nothing over in ``fulltext`` can catch them drifting
    apart: the longest name that module builds is 214 characters against a
    255 limit, so 41 characters of growth pass its guard test unnoticed.
    """

    def test_the_temporary_name_adds_exactly_the_documented_overhead(self, tmp_path, monkeypatch):
        staged = _staged_name(tmp_path / "target.pdf", monkeypatch)
        assert len(staged) == len("target.pdf") + TEMP_NAME_OVERHEAD

    def test_the_overhead_does_not_depend_on_the_target_name(self, tmp_path, monkeypatch):
        """It is a constant, not a ratio — which is what makes a cap possible."""
        short = _staged_name(tmp_path / "a", monkeypatch)
        long = _staged_name(tmp_path / ("b" * 100), monkeypatch)
        assert len(short) - len("a") == len(long) - len("b" * 100) == TEMP_NAME_OVERHEAD

    def test_the_temporary_name_is_hidden(self, tmp_path, monkeypatch):
        """A leftover from a ``SIGKILL`` is unobtrusive rather than alarming.

        Nothing sweeps the ones written outside the full-text cache, so the
        one thing that can be promised about them is that an ordinary ``ls``
        does not show them.
        """
        assert _staged_name(tmp_path / "target.pdf", monkeypatch).startswith(".")


class TestTheFailureNamesTheFileTheCallerAskedFor:
    """The write fails on a name the caller never chose and that is then gone.

    ``FullTextService`` interpolates ``str(exc)`` into the single warning it
    emits for a failed cache write, and ``str`` of an ``OSError`` is built
    from ``filename``. Left alone, that warning names a temporary file the
    cleanup has already unlinked, so an operator who greps for it finds
    nothing on disk.
    """

    def test_a_full_disk_names_the_target(self, tmp_path, monkeypatch):
        """The ``fsync`` path carries no filename of its own at all."""

        def no_space(fd: int) -> None:
            raise OSError(errno.ENOSPC, "No space left on device")

        monkeypatch.setattr(os, "fsync", no_space)
        target = tmp_path / "article.html"

        with pytest.raises(OSError) as caught:
            atomic_write(target, b"payload")

        assert caught.value.errno == errno.ENOSPC
        assert caught.value.filename == str(target)
        assert str(target) in str(caught.value)

    def test_an_unwritable_directory_names_the_target_not_the_temporary_file(self, tmp_path):
        """Here the OS *does* supply a filename — the temporary one."""
        readonly = tmp_path / "readonly"
        readonly.mkdir()
        readonly.chmod(0o555)
        target = readonly / "article.html"
        try:
            with pytest.raises(OSError) as caught:
                atomic_write(target, b"payload")
        finally:
            readonly.chmod(0o755)

        assert caught.value.filename == str(target)
        assert ".tmp" not in str(caught.value)

    def test_a_failed_publish_keeps_both_names(self, tmp_path, monkeypatch):
        """``os.replace`` raises the two-file form, which already names the target.

        Rewriting ``filename`` there would overwrite the source of the rename
        with its destination and report ``'x' -> 'x'``, so that case is left
        alone — which is what the ``filename2 is None`` test is for.
        """
        target = tmp_path / "article.html"

        def cross_device(src, dst):
            raise OSError(errno.EXDEV, "Invalid cross-device link", str(src), None, str(dst))

        monkeypatch.setattr(os, "replace", cross_device)
        with pytest.raises(OSError) as caught:
            atomic_write(target, b"payload")

        assert caught.value.filename2 == str(target)
        assert caught.value.filename != caught.value.filename2

    def test_a_bug_in_the_caller_is_not_dressed_up_as_a_write_failure(self, tmp_path):
        """Only ``OSError`` is re-pointed; a ``TypeError`` passes through as itself.

        ``FullTextService`` reports a swallowed bmlib defect differently from
        a full disk (issue #72), and it tells them apart by exception type.
        """
        with pytest.raises(TypeError):
            atomic_write(tmp_path / "article.html", "not bytes")  # type: ignore[arg-type]

        assert list(tmp_path.iterdir()) == []


def test_the_descriptor_is_closed_when_the_file_object_cannot_be_built(tmp_path, monkeypatch):
    """``os.fdopen`` adopts the descriptor only once it succeeds.

    Realistically this is ``MemoryError`` and nothing else, but the leak it
    would cause is unbounded and silent, and closing it costs one line. The
    fault has to be injected because no reachable input produces it.
    """
    opened: list[int] = []
    closed: list[int] = []
    real_open, real_close = os.open, os.close

    def record_open(*args, **kwargs):
        fd = real_open(*args, **kwargs)
        opened.append(fd)
        return fd

    def record_close(fd: int) -> None:
        closed.append(fd)
        real_close(fd)

    def cannot_allocate(fd, mode):
        raise MemoryError

    monkeypatch.setattr(os, "open", record_open)
    monkeypatch.setattr(os, "close", record_close)
    monkeypatch.setattr(os, "fdopen", cannot_allocate)

    with pytest.raises(MemoryError):
        atomic_write(tmp_path / "article.html", b"payload")

    assert opened, "the temporary file was never opened"
    assert closed == opened
    # The cleanup still ran, so nothing is left staged either.
    assert list(tmp_path.iterdir()) == []
