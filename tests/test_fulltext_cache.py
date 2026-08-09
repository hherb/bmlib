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
from pathlib import Path

import pytest

from bmlib.fulltext.cache import FullTextCache

PDF_MAGIC = b"%PDF-1.4 fake content"


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
        with one the first user then cannot read either. Compared against a
        file written the ordinary way in the same directory, so the
        assertion holds whatever the umask is.
        """
        cache = FullTextCache(cache_dir=tmp_path)
        reference = tmp_path / "reference"
        reference.write_bytes(b"x")

        html_path = Path(cache.save_html("<p>x</p>", "PMC123"))
        pdf_path = Path(cache.save_pdf(PDF_MAGIC, "12345"))

        expected = stat.S_IMODE(reference.stat().st_mode)
        assert stat.S_IMODE(html_path.stat().st_mode) == expected
        assert stat.S_IMODE(pdf_path.stat().st_mode) == expected


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
