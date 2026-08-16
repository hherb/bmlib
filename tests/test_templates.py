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

"""Tests for bmlib.templates."""

from __future__ import annotations

import errno
import os

import pytest
from jinja2 import TemplateNotFound

from bmlib.templates import TemplateEngine


def test_render_from_default_dir(tmp_path):
    default_dir = tmp_path / "defaults"
    default_dir.mkdir()
    (default_dir / "test.txt").write_text("Hello {{ name }}!")

    engine = TemplateEngine(default_dir=default_dir)
    assert engine.render("test.txt", name="World") == "Hello World!"


def test_user_dir_overrides_default(tmp_path):
    default_dir = tmp_path / "defaults"
    default_dir.mkdir()
    (default_dir / "test.txt").write_text("default: {{ x }}")

    user_dir = tmp_path / "user"
    user_dir.mkdir()
    (user_dir / "test.txt").write_text("custom: {{ x }}")

    engine = TemplateEngine(user_dir=user_dir, default_dir=default_dir)
    assert engine.render("test.txt", x="val") == "custom: val"


def test_fallback_to_default(tmp_path):
    default_dir = tmp_path / "defaults"
    default_dir.mkdir()
    (default_dir / "only_default.txt").write_text("from default")

    user_dir = tmp_path / "user"
    user_dir.mkdir()

    engine = TemplateEngine(user_dir=user_dir, default_dir=default_dir)
    assert engine.render("only_default.txt") == "from default"


def test_missing_template_raises(tmp_path):
    engine = TemplateEngine(default_dir=tmp_path)
    with pytest.raises(TemplateNotFound):
        engine.render("nonexistent.txt")


def test_has_template(tmp_path):
    default_dir = tmp_path / "defaults"
    default_dir.mkdir()
    (default_dir / "exists.txt").write_text("yes")

    engine = TemplateEngine(default_dir=default_dir)
    assert engine.has_template("exists.txt")
    assert not engine.has_template("nope.txt")


def test_jinja_conditionals(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    (d / "cond.txt").write_text("{% if include_methods %}Methods: {{ methods }}{% endif %}")

    engine = TemplateEngine(default_dir=d)
    assert engine.render("cond.txt", include_methods=True, methods="RCT") == "Methods: RCT"
    assert engine.render("cond.txt", include_methods=False, methods="RCT") == ""


def test_jinja_loops(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    (d / "loop.txt").write_text("{% for item in items %}- {{ item }}\n{% endfor %}")

    engine = TemplateEngine(default_dir=d)
    result = engine.render("loop.txt", items=["a", "b", "c"])
    assert "- a" in result
    assert "- c" in result


def test_install_defaults(tmp_path):
    default_dir = tmp_path / "defaults"
    default_dir.mkdir()
    (default_dir / "a.txt").write_text("alpha")
    (default_dir / "b.txt").write_text("beta")

    user_dir = tmp_path / "user"
    # User dir doesn't exist yet — install_defaults should create it
    engine = TemplateEngine(user_dir=user_dir, default_dir=default_dir)
    engine.install_defaults()

    assert (user_dir / "a.txt").read_text() == "alpha"
    assert (user_dir / "b.txt").read_text() == "beta"

    # Existing files are not overwritten
    (user_dir / "a.txt").write_text("modified")
    engine.install_defaults()
    assert (user_dir / "a.txt").read_text() == "modified"


class TestInstallingDefaultsIsAtomic:
    """Issue #73 — a copy interrupted partway must leave nothing behind.

    ``install_defaults()`` skips any template the user directory already
    holds, so a truncated file is not merely wrong once: it is wrong
    permanently. Jinja2 renders whatever survived, which is not a
    ``TemplateNotFound`` but a prompt missing its second half, sent to a
    model with nothing logged.
    """

    @staticmethod
    def _disk_fills_mid_write(monkeypatch) -> None:
        """Fail the way a full disk really does — at ``fsync``, not at ``write``.

        Under delayed allocation the ``write(2)`` returns success and ENOSPC
        reaches userspace only when the blocks are allocated, which is why the
        ``fsync`` is load-bearing rather than durability theatre. Faulting
        ``write`` instead would let an implementation that omits the ``fsync``
        pass. Same fault ``tests/test_fulltext_cache.py`` injects for #70.
        """

        def no_space(fd: int) -> None:
            raise OSError(errno.ENOSPC, "No space left on device")

        monkeypatch.setattr(os, "fsync", no_space)

    def test_a_faulted_copy_installs_on_the_next_call_instead_of_never(self, tmp_path, monkeypatch):
        """The regression #73 names, end to end.

        With the bare ``write_text`` this replaced, the interrupted copy is
        left on disk truncated, ``dest.exists()`` is then true, and every
        later call skips it — so the second half of this test is the half
        that fails: the template is never repaired.
        """
        default_dir = tmp_path / "defaults"
        default_dir.mkdir()
        (default_dir / "scoring.txt").write_text("first half\nsecond half\n")

        user_dir = tmp_path / "user"
        engine = TemplateEngine(user_dir=user_dir, default_dir=default_dir)

        self._disk_fills_mid_write(monkeypatch)
        with pytest.raises(OSError):
            engine.install_defaults()

        # Nothing at all, not merely no *complete* template: a leftover
        # temporary file in the user directory would also be a defect, and
        # asserting only on ``scoring.txt`` would not see it.
        assert list(user_dir.iterdir()) == []

        monkeypatch.undo()
        engine.install_defaults()
        assert (user_dir / "scoring.txt").read_text() == "first half\nsecond half\n"

    def test_a_template_is_copied_byte_for_byte(self, tmp_path):
        """Pins the byte copy against the decode/re-encode round trip.

        Reading text and writing it back is not a copy: ``read_text`` applies
        universal newlines and ``write_text`` translates back through
        ``os.linesep``, so a CRLF template arrives as LF here and — on
        Windows — an LF one arrives as CRLF. A prompt is sent to a model
        verbatim, so "install the default" has to mean the default. Without
        this test, reverting to ``read_text``/``write_text`` passes the whole
        suite.
        """
        default_dir = tmp_path / "defaults"
        default_dir.mkdir()
        verbatim = b"line one\r\nline two\r\nno trailing newline"
        (default_dir / "crlf.txt").write_bytes(verbatim)

        user_dir = tmp_path / "user"
        TemplateEngine(user_dir=user_dir, default_dir=default_dir).install_defaults()

        assert (user_dir / "crlf.txt").read_bytes() == verbatim
