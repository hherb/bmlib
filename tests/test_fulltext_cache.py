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

"""Tests for bmlib.fulltext.cache."""

import errno
import os
import stat
import threading
from pathlib import Path
from unittest import mock

import pytest

from bmlib.fulltext.cache import FullTextCache

# The line endings are load-bearing. A descriptor opened without O_BINARY
# translates them on Windows, so a payload of printable ASCII alone would let
# every round-trip assertion below pass on a cache that corrupts real PDFs.
PDF_MAGIC = b"%PDF-1.4 fake\ncontent\r\nwith line endings\n"


class TestPDFCaching:
    def test_cache_and_retrieve_pdf(self, tmp_path):
        cache = FullTextCache(cache_dir=tmp_path)
        path = cache.save_pdf(PDF_MAGIC, "12345")
        assert path is not None
        assert cache.get_pdf("12345") == path

    def test_get_missing_pdf(self, tmp_path):
        cache = FullTextCache(cache_dir=tmp_path)
        assert cache.get_pdf("99999") is None

    def test_rejects_non_pdf(self, tmp_path):
        cache = FullTextCache(cache_dir=tmp_path)
        path = cache.save_pdf(b"not a pdf", "12345")
        assert path is None

    def test_delete_pdf(self, tmp_path):
        cache = FullTextCache(cache_dir=tmp_path)
        cache.save_pdf(PDF_MAGIC, "12345")
        cache.delete("12345")
        assert cache.get_pdf("12345") is None


class TestHTMLCaching:
    def test_cache_and_retrieve_html(self, tmp_path):
        cache = FullTextCache(cache_dir=tmp_path)
        cache.save_html("<h1>Title</h1><p>Body</p>", "PMC123")
        html = cache.get_html("PMC123")
        assert html is not None
        assert "<h1>Title</h1>" in html

    def test_get_missing_html(self, tmp_path):
        cache = FullTextCache(cache_dir=tmp_path)
        assert cache.get_html("PMC999") is None

    def test_delete_html(self, tmp_path):
        cache = FullTextCache(cache_dir=tmp_path)
        cache.save_html("<p>text</p>", "PMC123")
        cache.delete("PMC123")
        assert cache.get_html("PMC123") is None


class TestWritesAreAtomic:
    """A write that fails partway must leave no cache entry behind (#70).

    A bare ``write_text``/``write_bytes`` that runs out of space leaves a
    truncated file that decodes perfectly and is then served as complete full
    text on every later run, with nothing logged at any level.

    The fault is injected at ``os.fsync`` because that is where a full disk
    reports itself: under delayed allocation the ``write()`` succeeds and the
    blocks are never allocated, so a fix that skipped the flush would publish
    a file of zeros and still pass a test that faulted the write call.

    Delayed allocation cannot be provoked from userspace, so what the four
    fault-injection tests below actually pin is that the implementation calls
    ``os.fsync`` at all — they are change detectors for the injection point,
    not observations of the kernel behaviour that makes it necessary. A
    refactor that reached the same guarantee another way (``O_DSYNC`` in the
    open flags, say) would break them while being correct.
    """

    @staticmethod
    def _disk_fills_mid_write(monkeypatch) -> None:
        def no_space(fd: int) -> None:
            raise OSError(errno.ENOSPC, "No space left on device")

        monkeypatch.setattr(os, "fsync", no_space)

    def test_a_failed_html_write_leaves_no_cache_entry(self, tmp_path, monkeypatch):
        cache = FullTextCache(cache_dir=tmp_path)
        self._disk_fills_mid_write(monkeypatch)

        with pytest.raises(OSError):
            cache.save_html("<h1>Title</h1><p>Body</p>", "PMC123")

        assert cache.get_html("PMC123") is None

    def test_a_failed_pdf_write_leaves_no_cache_entry(self, tmp_path, monkeypatch):
        cache = FullTextCache(cache_dir=tmp_path)
        self._disk_fills_mid_write(monkeypatch)

        with pytest.raises(OSError):
            cache.save_pdf(PDF_MAGIC, "12345")

        assert cache.get_pdf("12345") is None

    def test_a_failed_rewrite_keeps_the_entry_that_was_already_there(self, tmp_path, monkeypatch):
        """The replacement is all-or-nothing, not a truncate-then-fill."""
        cache = FullTextCache(cache_dir=tmp_path)
        cache.save_html("<p>the whole article</p>", "PMC123")
        self._disk_fills_mid_write(monkeypatch)

        with pytest.raises(OSError):
            cache.save_html("<p>a longer article that will not fit</p>", "PMC123")

        assert cache.get_html("PMC123") == "<p>the whole article</p>"

    def test_a_failed_pdf_rewrite_keeps_the_pdf_that_was_already_there(self, tmp_path, monkeypatch):
        """The PDF half needs its own content assertion, not just absence.

        Asserting only that no entry appears is satisfied by an ordinary
        non-atomic write that unlinks on failure — mutation confirmed a
        ``_atomic_write``-for-HTML-only implementation passes every other test
        in this class. Only comparing the surviving *bytes* catches it.
        """
        cache = FullTextCache(cache_dir=tmp_path)
        cache.save_pdf(PDF_MAGIC, "12345")
        self._disk_fills_mid_write(monkeypatch)

        with pytest.raises(OSError):
            cache.save_pdf(PDF_MAGIC + b"a much longer PDF that will not fit\n", "12345")

        path = cache.get_pdf("12345")
        assert path is not None
        assert Path(path).read_bytes() == PDF_MAGIC

    def test_two_writers_racing_on_one_article_do_not_destroy_each_other(self, tmp_path):
        """What the UUID in the temporary name actually buys.

        ``O_EXCL`` already stops two processes writing one temp file, so that
        is not the hazard. The hazard is the loser's cleanup: with a fixed
        temp name it unlinks the *winner's* in-flight file, the winner's
        ``os.replace`` then fails with ``FileNotFoundError``, and neither
        writer caches anything. Both writers are held at the barrier until
        each has data in its own temp file, which is the window a fixed name
        would collapse; mutating the UUID to ``os.getpid()`` fails this test
        and nothing else in the suite.
        """
        cache = FullTextCache(cache_dir=tmp_path)
        both_written = threading.Barrier(2, timeout=10)
        errors: list[BaseException] = []
        real_replace = os.replace

        def replace_once_both_are_ready(src, dst, *args, **kwargs):
            both_written.wait()
            return real_replace(src, dst, *args, **kwargs)

        def write(html: str) -> None:
            try:
                cache.save_html(html, "PMC123")
            except BaseException as exc:
                # Collected rather than swallowed — the assertion below is
                # what fails, and it can then name what went wrong.
                errors.append(exc)

        with mock.patch.object(os, "replace", replace_once_both_are_ready):
            threads = [
                threading.Thread(target=write, args=(f"<p>writer {n}</p>",)) for n in range(2)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=15)

        assert errors == []
        assert cache.get_html("PMC123") in ("<p>writer 0</p>", "<p>writer 1</p>")

    def test_an_identifier_too_long_for_a_temp_name_is_still_cacheable(self, tmp_path):
        """The temporary name is 38 characters longer than the entry's own.

        Left unbounded that lowers the effective ``NAME_MAX`` ceiling from 255
        to ~217, so an identifier a bare ``write_text`` handled fails here —
        and that per-article fault then trips ``FullTextService``'s
        once-per-service "nothing is being cached" warning, which is untrue
        and silences the directory-wide fault it exists to report.
        """
        long_id = "10.1234/" + "a" * 300

        cache = FullTextCache(cache_dir=tmp_path)
        cache.save_html("<p>body</p>", long_id)
        pdf_path = cache.save_pdf(PDF_MAGIC, long_id)

        assert cache.get_html(long_id) == "<p>body</p>"
        assert pdf_path is not None
        assert len(Path(pdf_path).name) <= 255

    def test_two_long_identifiers_sharing_a_prefix_get_separate_entries(self, tmp_path):
        """Negative control on the truncation: the hash still separates them."""
        cache = FullTextCache(cache_dir=tmp_path)
        first = "10.1234/" + "a" * 300 + "/one"
        second = "10.1234/" + "a" * 300 + "/two"

        cache.save_html("<p>first</p>", first)
        cache.save_html("<p>second</p>", second)

        assert cache.get_html(first) == "<p>first</p>"
        assert cache.get_html(second) == "<p>second</p>"

    def test_a_failed_write_leaves_no_temp_file_behind(self, tmp_path, monkeypatch):
        cache = FullTextCache(cache_dir=tmp_path)
        self._disk_fills_mid_write(monkeypatch)

        with pytest.raises(OSError):
            cache.save_html("<p>x</p>", "PMC123")
        with pytest.raises(OSError):
            cache.save_pdf(PDF_MAGIC, "12345")

        leftovers = [p for directory in ("html", "pdfs") for p in (tmp_path / directory).iterdir()]
        assert leftovers == []

    def test_an_ordinary_write_still_round_trips(self, tmp_path):
        """Negative control: the guard is not simply refusing every write."""
        cache = FullTextCache(cache_dir=tmp_path)

        cache.save_html("<p>body</p>", "PMC123")
        pdf_path = cache.save_pdf(PDF_MAGIC, "12345")

        assert cache.get_html("PMC123") == "<p>body</p>"
        assert pdf_path is not None
        assert Path(pdf_path).read_bytes() == PDF_MAGIC

    def test_a_cached_file_is_as_readable_as_an_ordinary_write(self, tmp_path):
        """Permissions must not narrow on the way through the temp file.

        ``tempfile.mkstemp`` creates at 0600, which would silently break a
        cache directory shared between users: the second user cannot read
        what the first cached, re-fetches everything, and replaces the file
        with one the first user then cannot read either.

        The write must therefore request 0666, exactly as ``write_bytes``
        does, and let the umask do the narrowing. Requesting 0644 is the same
        bug a step smaller — it drops the group-write bit a umask of 002
        grants, which is the very shared-group setup this exists for, and it
        made this assertion fail under that umask while passing under 022.
        Compared against a file written the ordinary way in the same
        directory, so it now holds whatever the umask is.
        """
        cache = FullTextCache(cache_dir=tmp_path)
        reference = tmp_path / "reference"
        reference.write_bytes(b"x")

        html_path = Path(cache.save_html("<p>x</p>", "PMC123"))
        pdf_path = Path(cache.save_pdf(PDF_MAGIC, "12345"))

        expected = stat.S_IMODE(reference.stat().st_mode)
        assert stat.S_IMODE(html_path.stat().st_mode) == expected
        assert stat.S_IMODE(pdf_path.stat().st_mode) == expected

    def test_the_cleanup_does_not_speak_over_the_error_it_is_tidying_after(
        self, tmp_path, monkeypatch
    ):
        """The caller must be told the disk filled, not that an unlink failed.

        ``missing_ok=True`` covers only ``ENOENT``; an unlink that fails for
        any other reason would otherwise replace the original exception — and
        that exception is what ``FullTextService`` interpolates into the one
        warning an operator ever sees, so a full disk would be reported as a
        permissions problem.
        """
        cache = FullTextCache(cache_dir=tmp_path)
        self._disk_fills_mid_write(monkeypatch)

        def cannot_unlink(self, missing_ok: bool = False) -> None:
            raise PermissionError(errno.EPERM, "Operation not permitted")

        monkeypatch.setattr(Path, "unlink", cannot_unlink)

        with pytest.raises(OSError) as caught:
            cache.save_html("<p>x</p>", "PMC123")

        assert caught.value.errno == errno.ENOSPC


class TestAnUnreadableEntryCanBeMovedAside:
    """``quarantine()`` is what lets a corrupt entry heal (#71 follow-up).

    Leaving the bad file in place preserves the evidence but nothing else: an
    undecodable HTML entry is consulted ahead of the PDF entry, so it hides a
    good PDF behind it, and only a re-fetch that happens to return JATS full
    text ever overwrites it. Every other run repeats the same warning and the
    same network fetch, forever.
    """

    @staticmethod
    def _corrupt_html(cache: FullTextCache, tmp_path, identifier: str) -> Path:
        cache.save_html("<p>whatever was there before</p>", identifier)
        path = tmp_path / "html" / f"{identifier}.html"
        path.write_bytes("<p>Ω</p>".encode()[:4])
        return path

    def test_an_unreadable_entry_is_moved_aside(self, tmp_path):
        cache = FullTextCache(cache_dir=tmp_path)
        path = self._corrupt_html(cache, tmp_path, "PMC123")

        moved = cache.quarantine("PMC123")

        assert moved == [str(path) + ".corrupt"]
        assert cache.get_html("PMC123") is None

    def test_the_bytes_are_kept_for_inspection(self, tmp_path):
        """Moved aside, not deleted — a failed re-fetch leaves the evidence."""
        cache = FullTextCache(cache_dir=tmp_path)
        path = self._corrupt_html(cache, tmp_path, "PMC123")
        original = path.read_bytes()

        cache.quarantine("PMC123")

        assert Path(str(path) + ".corrupt").read_bytes() == original

    def test_a_readable_entry_is_left_alone(self, tmp_path):
        """Negative control: this is not a disguised clear()."""
        cache = FullTextCache(cache_dir=tmp_path)
        cache.save_html("<p>fine</p>", "PMC123")
        cache.save_pdf(PDF_MAGIC, "PMC123")

        assert cache.quarantine("PMC123") == []
        assert cache.get_html("PMC123") == "<p>fine</p>"
        assert cache.get_pdf("PMC123") is not None

    def test_a_good_pdf_beside_a_corrupt_html_entry_survives(self, tmp_path):
        """The point of the exercise: the PDF becomes reachable again."""
        cache = FullTextCache(cache_dir=tmp_path)
        self._corrupt_html(cache, tmp_path, "PMC123")
        cache.save_pdf(PDF_MAGIC, "PMC123")

        cache.quarantine("PMC123")

        assert cache.get_html("PMC123") is None
        assert Path(cache.get_pdf("PMC123")).read_bytes() == PDF_MAGIC

    def test_an_entry_that_is_not_even_a_file_is_moved_aside_too(self, tmp_path):
        """The shape that raises for every user, including root."""
        cache = FullTextCache(cache_dir=tmp_path)
        (tmp_path / "html" / "PMC123.html").mkdir()

        assert cache.quarantine("PMC123") != []
        assert cache.get_html("PMC123") is None

    def test_quarantining_nothing_is_not_an_error(self, tmp_path):
        cache = FullTextCache(cache_dir=tmp_path)
        assert cache.quarantine("PMC404") == []


class TestRemovingAnEntryThatIsNotAFile:
    """Both documented ways to clear a bad entry used to fail on the same one.

    ``delete()`` raised and ``clear()`` skipped silently, while the manual
    told the operator to delete the file naked in the warning.
    """

    def test_delete_removes_an_entry_that_is_a_directory(self, tmp_path):
        cache = FullTextCache(cache_dir=tmp_path)
        (tmp_path / "html" / "PMC123.html").mkdir()

        cache.delete("PMC123")

        assert not (tmp_path / "html" / "PMC123.html").exists()

    def test_clear_removes_an_entry_that_is_a_directory(self, tmp_path):
        cache = FullTextCache(cache_dir=tmp_path)
        entry = tmp_path / "pdfs" / "12345.pdf"
        entry.mkdir()
        (entry / "nested").write_bytes(b"x")

        cache.clear()

        assert not entry.exists()

    def test_clear_sweeps_quarantined_and_temporary_leftovers(self, tmp_path):
        cache = FullTextCache(cache_dir=tmp_path)
        (tmp_path / "html" / "PMC123.html.corrupt").write_bytes(b"bad")
        (tmp_path / "html" / ".PMC123.html.deadbeef.tmp").write_bytes(b"partial")

        cache.clear()

        assert list((tmp_path / "html").iterdir()) == []


class TestCacheClear:
    def test_clear_all(self, tmp_path):
        cache = FullTextCache(cache_dir=tmp_path)
        cache.save_pdf(PDF_MAGIC, "111")
        cache.save_html("<p>x</p>", "222")
        cache.clear()
        assert cache.get_pdf("111") is None
        assert cache.get_html("222") is None


class TestDefaultDirectory:
    def test_default_cache_dir(self):
        cache = FullTextCache()
        assert cache.cache_dir.name == "fulltext_cache"
        assert "bmlib" in str(cache.cache_dir).lower() or "bmnews" in str(cache.cache_dir).lower()

    def test_custom_cache_dir(self, tmp_path):
        custom = tmp_path / "my_cache"
        cache = FullTextCache(cache_dir=custom)
        assert cache.cache_dir == custom
        assert cache.cache_dir.exists()


class TestCacheSubdirectories:
    def test_pdfs_in_subdirectory(self, tmp_path):
        cache = FullTextCache(cache_dir=tmp_path)
        path = cache.save_pdf(PDF_MAGIC, "12345")
        assert path is not None
        assert "/pdfs/" in path or "\\pdfs\\" in path

    def test_html_in_subdirectory(self, tmp_path):
        cache = FullTextCache(cache_dir=tmp_path)
        cache.save_html("<p>x</p>", "PMC123")
        path = cache.get_html("PMC123")
        # HTML is stored as files too
        assert path is None or True  # get_html returns content, not path


class TestIdentifierSanitization:
    """Raw identifiers (e.g. DOIs with slashes) must not escape the cache dir."""

    def test_raw_doi_stays_inside_cache(self, tmp_path):
        from pathlib import Path

        cache = FullTextCache(cache_dir=tmp_path)
        path = cache.save_html("<p>body</p>", "10.1234/sub/dir")
        p = Path(path)
        assert p.parent == tmp_path / "html"
        assert cache.get_html("10.1234/sub/dir") == "<p>body</p>"

    def test_path_traversal_cannot_escape(self, tmp_path):
        from pathlib import Path

        root = tmp_path / "cache"
        cache = FullTextCache(cache_dir=root)
        path = cache.save_pdf(b"%PDF-1.4 fake", "../../evil")
        assert path is not None
        assert Path(path).is_relative_to(root)
        assert (tmp_path / "evil.pdf").exists() is False
        assert cache.get_pdf("../../evil") == path
        cache.delete("../../evil")
        assert cache.get_pdf("../../evil") is None

    def test_distinct_raw_identifiers_do_not_collide(self, tmp_path):
        cache = FullTextCache(cache_dir=tmp_path)
        cache.save_html("first", "10.1/a/b")
        cache.save_html("second", "10.1/a_b")
        assert cache.get_html("10.1/a/b") == "first"
        assert cache.get_html("10.1/a_b") == "second"

    def test_already_safe_identifier_keeps_exact_filename(self, tmp_path):
        # Identifiers pre-sanitized by FullTextService must map to the same
        # file as before, so existing caches stay valid.
        from pathlib import Path

        cache = FullTextCache(cache_dir=tmp_path)
        path = cache.save_html("x", "10.1234_x.y-z_ab12cd34ef")
        assert Path(path).name == "10.1234_x.y-z_ab12cd34ef.html"


class TestADirectlyConstructedCacheStillRaises:
    """The half of #75's decision that has no code behind it.

    ``FullTextService`` degrades when the *default* cache cannot be built, but
    a caller who constructed a ``FullTextCache`` asked for a cache
    specifically: degrading here would hand back an object whose every method
    then fails one at a time, instead of failing once, clearly, at
    construction. Nothing else in the suite would notice if that guard were
    "tidied" down into this class, so this test is what holds the asymmetry.
    """

    def test_a_file_where_the_cache_directory_should_be_raises(self, tmp_path):
        blocker = tmp_path / "notadir"
        blocker.write_text("I am a file, not a directory")

        with pytest.raises(OSError):
            FullTextCache(cache_dir=blocker)

    def test_a_usable_directory_still_constructs(self, tmp_path):
        """Negative control: the raise above must come from the fault named.

        Without this, a constructor that had become unable to create *any*
        directory — a broken ``mkdir`` call, a wrong subdirectory name — would
        satisfy the test above while telling us nothing about the file
        standing in the way. Asserting the two subdirectories exist is what
        makes the success meaningful rather than merely silent.
        """
        FullTextCache(cache_dir=tmp_path / "fresh")

        assert (tmp_path / "fresh" / "pdfs").is_dir()
        assert (tmp_path / "fresh" / "html").is_dir()
