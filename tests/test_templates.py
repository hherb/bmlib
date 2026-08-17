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
import logging
import os
from pathlib import Path

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


def test_a_default_dir_that_is_not_a_directory_is_reported(tmp_path, caplog):
    """A mistyped ``default_dir`` must not read as "installed successfully".

    The method returns without doing anything, which is right — there is
    nothing to install — but silently, which is the shape of the bug #73
    fixed. The unset case below stays at DEBUG because configuring neither
    directory is a legitimate way to use the engine.
    """
    engine = TemplateEngine(user_dir=tmp_path / "user", default_dir=tmp_path / "typo")

    with caplog.at_level(logging.WARNING, logger="bmlib.templates.engine"):
        engine.install_defaults()

    # The tail of the line, not just "is not a directory": the substring has
    # to be unique to the line under test or the assertion can pass on
    # someone else's output.
    assert "so there are none to install" in caplog.text
    assert not (tmp_path / "user").exists()

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="bmlib.templates.engine"):
        TemplateEngine(user_dir=tmp_path / "user").install_defaults()
    assert caplog.text == ""


def test_only_prompt_suffixes_are_installed(tmp_path):
    """The suffix tuple is a contract, not an internal detail.

    bmlib ships no templates, so ``default_dir`` is always the caller's own
    directory — and a downstream that keeps a README, or a directory of
    fixtures, beside its prompts must not have them copied into the user's
    prompt directory. ``fixtures.txt`` is a *directory* on purpose: it is the
    only case in which ``is_file()`` does any work.
    """
    default_dir = tmp_path / "defaults"
    default_dir.mkdir()
    (default_dir / "a.txt").write_text("alpha")
    (default_dir / "b.j2").write_text("bravo")
    (default_dir / "c.jinja2").write_text("charlie")
    (default_dir / "README.md").write_text("not a prompt")
    (default_dir / "fixtures.txt").mkdir()

    user_dir = tmp_path / "user"
    TemplateEngine(user_dir=user_dir, default_dir=default_dir).install_defaults()

    assert [p.name for p in sorted(user_dir.iterdir())] == ["a.txt", "b.j2", "c.jinja2"]


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
        with pytest.raises(OSError) as caught:
            engine.install_defaults()

        # Named, not merely "an OSError": ``os.fsync`` is patched process-wide
        # for the duration, so any unrelated OSError on the path would satisfy
        # a bare ``pytest.raises``. Its sibling in test_fulltext_cache.py
        # asserts the same errno for the same reason.
        assert caught.value.errno == errno.ENOSPC
        # And it names the template, not the temporary file it was staged
        # through — that name is gone by the time the caller sees it.
        assert caught.value.filename == str(user_dir / "scoring.txt")

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
        ``os.linesep``. This fixture is CRLF, so the round trip corrupts it
        **here, on POSIX** — the corruption is not Windows-only, and
        ``default_dir`` belongs to the caller, so CRLF in it is ordinary. What
        is pinned is fidelity of the installed *file*; it is deliberately not
        a claim about what a model receives, since ``_FallbackLoader`` reads
        with ``read_text`` too. Without this test, reverting to
        ``read_text``/``write_text`` passes the whole suite.
        """
        default_dir = tmp_path / "defaults"
        default_dir.mkdir()
        verbatim = b"line one\r\nline two\r\nno trailing newline"
        (default_dir / "crlf.txt").write_bytes(verbatim)

        user_dir = tmp_path / "user"
        TemplateEngine(user_dir=user_dir, default_dir=default_dir).install_defaults()

        assert (user_dir / "crlf.txt").read_bytes() == verbatim

    def test_the_destination_never_exists_until_it_is_complete(self, tmp_path, monkeypatch):
        """Pins the *publish*, not merely the tidy-up.

        A plain ``open(dest, "wb")`` that unlinks on failure passes every
        other test in this class — verified by mutation. With nothing to
        overwrite, a cleaned-up in-place write and an atomic publish are
        indistinguishable *after the fact*; they differ only while the bytes
        are in flight, and only that difference survives ``SIGKILL``, which
        is half of the scenario #73 names and the half no error injection can
        reach. So this looks at the directory at the one instant they differ.
        """
        default_dir = tmp_path / "defaults"
        default_dir.mkdir()
        (default_dir / "scoring.txt").write_text("first half\nsecond half\n")

        user_dir = tmp_path / "user"
        seen: dict[str, bool] = {}
        real_replace = os.replace

        def watch(src, dst):
            seen["target_absent"] = not Path(dst).exists()
            seen["staged_under_another_name"] = Path(src).name != Path(dst).name
            real_replace(src, dst)

        monkeypatch.setattr(os, "replace", watch)
        TemplateEngine(user_dir=user_dir, default_dir=default_dir).install_defaults()

        assert seen, "the template was never published through os.replace"
        assert seen["target_absent"]
        assert seen["staged_under_another_name"]
        assert (user_dir / "scoring.txt").read_text() == "first half\nsecond half\n"

    def test_a_faulted_copy_leaves_the_ones_already_installed_alone(self, tmp_path, monkeypatch):
        """The self-repairing loop, which a single-template fixture cannot see.

        ``install_defaults()`` documents — publicly, in
        ``docs/manual/templates.md`` — that it aborts on the first failure
        and that calling it again installs whatever is still missing. Both of
        the tests above install one template, so an implementation that
        collected the successes and rolled them *all* back on failure, the
        exact opposite of the documented contract, passed them both.
        """
        default_dir = tmp_path / "defaults"
        default_dir.mkdir()
        for name, body in (("a.txt", "alpha\n"), ("b.txt", "beta\n"), ("c.txt", "gamma\n")):
            (default_dir / name).write_text(body)

        user_dir = tmp_path / "user"
        engine = TemplateEngine(user_dir=user_dir, default_dir=default_dir)

        real_fsync = os.fsync
        calls: list[int] = []

        def fills_on_the_second_template(fd: int) -> None:
            calls.append(fd)
            if len(calls) == 2:
                raise OSError(errno.ENOSPC, "No space left on device")
            real_fsync(fd)

        monkeypatch.setattr(os, "fsync", fills_on_the_second_template)
        with pytest.raises(OSError):
            engine.install_defaults()

        # The scan is sorted, so this is reproducible: a.txt got in, b.txt
        # faulted and published nothing, c.txt was never reached.
        assert [p.name for p in sorted(user_dir.iterdir())] == ["a.txt"]
        assert (user_dir / "a.txt").read_text() == "alpha\n"

        monkeypatch.undo()
        engine.install_defaults()
        assert [p.name for p in sorted(user_dir.iterdir())] == ["a.txt", "b.txt", "c.txt"]

    def test_a_dangling_symlink_is_left_alone_and_reported(self, tmp_path, caplog):
        """A user's symlink into an unmounted volume must survive the install.

        ``exists()`` follows symlinks, so a dangling one reads as absent —
        and ``os.replace`` publishes over *the link*, not over the file it
        points at. The ``write_text`` this replaced wrote *through* the link
        and raised ``FileNotFoundError``, so the old code was loud and
        harmless here and the atomic publish is quiet and destructive: the
        user's prompt is gone, the default is in its place, and the loudest
        thing said about it is an ``INFO`` line reading exactly like an
        ordinary first install.
        """
        default_dir = tmp_path / "defaults"
        default_dir.mkdir()
        (default_dir / "scoring.txt").write_text("the default\n")

        user_dir = tmp_path / "user"
        user_dir.mkdir()
        unmounted = tmp_path / "volume" / "scoring.txt"
        (user_dir / "scoring.txt").symlink_to(unmounted)

        with caplog.at_level(logging.WARNING, logger="bmlib.templates.engine"):
            TemplateEngine(user_dir=user_dir, default_dir=default_dir).install_defaults()

        assert (user_dir / "scoring.txt").is_symlink()
        assert (user_dir / "scoring.txt").readlink() == unmounted
        assert "its target is missing" in caplog.text

        # And when the volume comes back it is the user's own prompt that
        # renders — which is the whole point of not having overwritten it.
        unmounted.parent.mkdir()
        unmounted.write_text("the user's own\n")
        assert (user_dir / "scoring.txt").read_text() == "the user's own\n"
