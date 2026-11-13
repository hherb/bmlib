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

"""The full-text service reading the shared library before its cache and its chain (design
§4.5). Rust's ``tests/service_library.rs`` runs the same six cases."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bmlib.fulltext.cache import FullTextCache
from bmlib.fulltext.jats_parser import JATSParser
from bmlib.fulltext.library_store import LibraryStore, paper_info
from bmlib.fulltext.service import LIBRARY_SOURCE, FullTextService, _sanitize_identifier
from bmlib.library import (
    FileRef,
    Installation,
    Library,
    PaperIds,
    PaperInfo,
    SourceKind,
    canonical_ids,
    open_library,
)

_SAMPLE = (Path(__file__).parent / "fixtures" / "sample_article.xml").read_bytes()
# The sample's own PMID, and part of the title its rendering carries.
_SAMPLE_PMID = "34567890"
_SAMPLE_TITLE = "Effects of Novel Compound X"
_ME = Installation.new("studyanalyzer", "aaaa", "Mac")
_OTHER = Installation.new("medicalfactchecker", "bbbb", "Mac")


def _library(tmp_path: Path) -> Library:
    """The library at ``tmp_path/lib``, made on first use."""
    (tmp_path / "lib").mkdir(exist_ok=True)
    return open_library(tmp_path / "lib")


def _sample_info(ids: PaperIds) -> PaperInfo:
    """The sample JATS's paper as another app would save it, under *ids*."""
    return paper_info(ids, JATSParser(_SAMPLE).parse())


def _saved_by_other(tmp_path: Path, paper: PaperInfo, data: bytes, kind: SourceKind) -> FileRef:
    """Another installation saves *data* of *kind* for *paper* in the library at
    ``tmp_path/lib``."""
    return _library(tmp_path).save(_OTHER, paper, data, kind, "test")


def _service(tmp_path: Path) -> FullTextService:
    """A service reading the library at ``tmp_path/lib`` as ``_ME``, with an empty cache at
    ``tmp_path/cache`` (used only by a request with an ``identifier``)."""
    return FullTextService(
        email="test@example.com",
        cache=FullTextCache(cache_dir=tmp_path / "cache"),
        library=_library(tmp_path),
        installation=_ME,
    )


def _not_found(url: str, **_: object) -> MagicMock:
    """Every request answered 404."""
    return MagicMock(status_code=404, content=b"", text="")


def test_jats_in_library_ends_the_lookup(tmp_path: Path) -> None:
    jats = _saved_by_other(
        tmp_path, _sample_info(canonical_ids(_SAMPLE_PMID)), _SAMPLE, SourceKind.JATS
    )
    service = _service(tmp_path)
    with patch.object(service, "_http_get", side_effect=AssertionError("no request")) as get:
        result = service.fetch_fulltext(pmid=_SAMPLE_PMID)
    get.assert_not_called()
    assert result.source == LIBRARY_SOURCE
    assert result.html is not None and _SAMPLE_TITLE in result.html
    assert result.jats_xml == _SAMPLE
    assert result.library_files == [jats]
    assert result.library_failure is None
    assert result.file_path is None
    # This installation's record now lists the JATS.
    folder = next(f for f in _library(tmp_path).scan().folders if f.folder == jats.folder)
    assert any(
        r.installation.app == "studyanalyzer" and any(e.sha256 == jats.sha256 for e in r.files)
        for r in folder.records
    )


def test_jats_and_pdf_in_library(tmp_path: Path) -> None:
    paper = _sample_info(canonical_ids(_SAMPLE_PMID))
    jats = _saved_by_other(tmp_path, paper, _SAMPLE, SourceKind.JATS)
    pdf = _saved_by_other(tmp_path, paper, b"%PDF-1.4 sample", SourceKind.PDF)
    service = _service(tmp_path)
    with patch.object(service, "_http_get", side_effect=AssertionError("no request")) as get:
        result = service.fetch_fulltext(pmid=_SAMPLE_PMID)
    get.assert_not_called()
    assert result.source == LIBRARY_SOURCE
    assert result.file_path == str(LibraryStore(_library(tmp_path), _ME).path_of(pdf))
    assert result.library_files == [jats, pdf]


def test_tampered_jats_is_not_served(tmp_path: Path) -> None:
    jats = _saved_by_other(
        tmp_path, _sample_info(canonical_ids(_SAMPLE_PMID)), _SAMPLE, SourceKind.JATS
    )
    LibraryStore(_library(tmp_path), _ME).path_of(jats).write_bytes(b"<article/>")
    service = _service(tmp_path)
    # Europe PMC's XML for the PMC ID is the chain's first request.
    served = MagicMock(status_code=200, content=_SAMPLE)
    with patch.object(service, "_http_get", return_value=served) as get:
        result = service.fetch_fulltext(pmid=_SAMPLE_PMID, pmc_id="PMC7614751")
    assert result.source == "europepmc"
    assert get.call_count == 1
    assert result.library_failure is not None and "digest" in result.library_failure
    assert result.library_files == []


def test_jats_of_another_paper_is_not_served(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # The record claims PMID 999; the JATS itself says 34567890.
    _saved_by_other(tmp_path, _sample_info(canonical_ids("999")), _SAMPLE, SourceKind.JATS)
    service = _service(tmp_path)
    with (
        caplog.at_level(logging.WARNING, logger="bmlib.fulltext.service"),
        patch.object(service, "_http_get", side_effect=_not_found) as get,
    ):
        result = service.fetch_fulltext(pmid="999")
    assert get.call_count > 0, "the chain ran"
    assert result.source != LIBRARY_SOURCE
    assert result.library_failure is None
    assert any(
        r.levelno == logging.WARNING and "999" in r.getMessage() and _SAMPLE_PMID in r.getMessage()
        for r in caplog.records
    ), caplog.text


def _cached(service: FullTextService, identifier: str) -> None:
    """Put HTML for *identifier* in *service*'s cache."""
    assert service.cache is not None
    service.cache.save_html("<h1>Cached</h1>", _sanitize_identifier(identifier))


def test_identifier_only_request_skips_the_library(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _cached(service, "10.1/test")
    # Unreachable: a library looked up would set library_failure.
    shutil.rmtree(tmp_path / "lib")
    with patch.object(service, "_http_get", side_effect=AssertionError("no request")) as get:
        result = service.fetch_fulltext(identifier="10.1/test")
    get.assert_not_called()
    assert result.source == "cached"
    assert result.library_failure is None


def test_unreachable_library_falls_back_to_the_cache(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    service = _service(tmp_path)
    _cached(service, "10.1/test")
    shutil.rmtree(tmp_path / "lib")
    with (
        caplog.at_level(logging.WARNING, logger="bmlib.fulltext.service"),
        patch.object(service, "_http_get", side_effect=AssertionError("no request")) as get,
    ):
        result = service.fetch_fulltext(pmid=_SAMPLE_PMID, identifier="10.1/test")
    get.assert_not_called()
    assert result.source == "cached"
    assert result.library_failure is not None
    assert any("library" in r.getMessage() for r in caplog.records), caplog.text


def test_library_and_installation_come_together(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="installation"):
        FullTextService(
            email="e@x",
            cache=FullTextCache(cache_dir=tmp_path),
            library=_library(tmp_path),
        )
    with pytest.raises(ValueError, match="library"):
        FullTextService(email="e@x", cache=FullTextCache(cache_dir=tmp_path), installation=_ME)


def test_a_pmc_id_given_as_the_pmid_reads_the_library(tmp_path: Path) -> None:
    ids = canonical_ids(None, None, "PMC7614751")
    _saved_by_other(tmp_path, _sample_info(ids), _SAMPLE, SourceKind.JATS)
    service = _service(tmp_path)
    with patch.object(service, "_http_get", side_effect=AssertionError("no request")) as get:
        result = service.fetch_fulltext(pmid=" PMC7614751 ")
    get.assert_not_called()
    assert result.source == LIBRARY_SOURCE


def test_a_jats_the_parser_raises_on_is_not_served(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _saved_by_other(tmp_path, _sample_info(canonical_ids(_SAMPLE_PMID)), _SAMPLE, SourceKind.JATS)
    service = _service(tmp_path)
    with (
        caplog.at_level(logging.WARNING, logger="bmlib.fulltext.service"),
        patch(
            "bmlib.fulltext.service.JATSParser.parse_with_html", side_effect=RuntimeError("boom")
        ),
        patch.object(service, "_http_get", side_effect=_not_found) as get,
    ):
        result = service.fetch_fulltext(pmid=_SAMPLE_PMID)
    assert get.call_count > 0, "the chain ran"
    assert result.source != LIBRARY_SOURCE
    assert result.library_failure is None
    assert any(
        "RuntimeError" in r.getMessage() and "boom" in r.getMessage() for r in caplog.records
    ), caplog.text


def _holds_a_library_pdf(caplog: pytest.LogCaptureFixture) -> bool:
    """Whether the service logged that it holds a library PDF for the chain."""
    return any(r.getMessage().startswith("Holding the library's PDF") for r in caplog.records)


def test_the_pdf_beside_a_jats_of_another_paper_is_not_held(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    paper = _sample_info(canonical_ids("999"))
    _saved_by_other(tmp_path, paper, _SAMPLE, SourceKind.JATS)
    _saved_by_other(tmp_path, paper, b"%PDF-1.4 sample", SourceKind.PDF)
    service = _service(tmp_path)
    with (
        caplog.at_level(logging.INFO, logger="bmlib.fulltext.service"),
        patch.object(service, "_http_get", side_effect=_not_found),
    ):
        result = service.fetch_fulltext(pmid="999")
    assert result.source != LIBRARY_SOURCE
    assert not _holds_a_library_pdf(caplog), caplog.text


def test_a_library_pdf_without_jats_is_held_for_the_chain(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _saved_by_other(
        tmp_path, _sample_info(canonical_ids("999")), b"%PDF-1.4 sample", SourceKind.PDF
    )
    service = _service(tmp_path)
    with (
        caplog.at_level(logging.INFO, logger="bmlib.fulltext.service"),
        patch.object(service, "_http_get", side_effect=_not_found),
    ):
        service.fetch_fulltext(pmid="999")
    assert _holds_a_library_pdf(caplog), caplog.text
