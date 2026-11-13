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

"""The request's identifiers as library identifiers, from
``tests/fixtures/fulltext_library_ids.json`` — the file Rust's
``tests/library_store.rs`` asserts against."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bmlib.fulltext.library_store import library_ids

_FIXTURE = Path(__file__).parent / "fixtures" / "fulltext_library_ids.json"
_CASES: list[dict[str, Any]] = json.loads(_FIXTURE.read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", _CASES, ids=[c["name"] for c in _CASES])
def test_library_ids_match_the_fixture(case: dict[str, Any]) -> None:
    ids, dropped = library_ids(case["pmid"], case["doi"], case["pmc_id"])
    assert {"pmid": ids.pmid, "doi": ids.doi, "pmcid": ids.pmcid} == case["ids"]
    assert dropped == case["dropped"]


# ---- Task 2: finding, keeping and noting sources ----

import dataclasses  # noqa: E402

from bmlib.fulltext.jats_parser import JATSParser  # noqa: E402
from bmlib.fulltext.library_store import (  # noqa: E402
    LibraryStore,
    LibraryStoreFailure,
    paper_info,
)
from bmlib.library import (  # noqa: E402
    Installation,
    PaperIds,
    PaperInfo,
    SourceKind,
    canonical_ids,
    open_library,
    parse_record,
    render_record,
)

_SAMPLE = Path(__file__).parent / "fixtures" / "sample_article.xml"
_ME = Installation.new("studyanalyzer", "aaaa", "Mac")


def _store(tmp_path: Path) -> LibraryStore:
    return LibraryStore(open_library(tmp_path), _ME)


def _info(pmid: str | None = None, doi: str | None = None) -> PaperInfo:
    return PaperInfo(canonical_ids(pmid, doi, None), title="A title")


def test_find_source_is_none_for_an_unknown_paper(tmp_path: Path) -> None:
    assert _store(tmp_path).find_source(canonical_ids("1"), SourceKind.JATS) is None


def test_keep_then_find_source_round_trips_by_doi_and_by_linked_pmid(tmp_path: Path) -> None:
    store = _store(tmp_path)
    by_doi = _info(doi="10.1/x")
    store.keep(by_doi, b"<a/>", SourceKind.JATS, "t")
    # A second save links the PMID to the same folder.
    store.keep(_info("5", "10.1/x"), b"%PDF-1", SourceKind.PDF, "t")
    held = store.find_source(by_doi.ids, SourceKind.JATS)
    assert held is not None and held.bytes == b"<a/>"
    held = store.find_source(canonical_ids("5"), SourceKind.JATS)
    assert held is not None and held.bytes == b"<a/>"
    assert store.find_source(canonical_ids("5"), SourceKind.HTML) is None


def test_find_source_returns_the_later_of_two_pdfs(tmp_path: Path) -> None:
    store = _store(tmp_path)
    paper = _info("7")
    old = store.keep(paper, b"%PDF-old", SourceKind.PDF, "t")
    new = store.keep(paper, b"%PDF-new", SourceKind.PDF, "t")
    # Two saves in one second tie on the clock, so set the times by hand.
    path = tmp_path / "papers" / new.folder / "records" / _ME.record_file_name()
    rec = parse_record(path.read_text(encoding="utf-8"))
    files = tuple(
        dataclasses.replace(
            f, saved_at="2026-02-01T00:00:00Z" if f.sha256 == new.sha256 else "2026-01-01T00:00:00Z"
        )
        for f in rec.files
    )
    path.write_text(render_record(dataclasses.replace(rec, files=files)), encoding="utf-8")
    held = store.find_source(paper.ids, SourceKind.PDF)
    assert held is not None
    assert held.file.sha256 == new.sha256 != old.sha256


def test_a_file_overwritten_on_disk_is_an_error_naming_the_digest(tmp_path: Path) -> None:
    store = _store(tmp_path)
    paper = _info("8")
    file = store.keep(paper, b"%PDF-1", SourceKind.PDF, "t")
    store.path_of(file).write_bytes(b"other")
    with pytest.raises(LibraryStoreFailure) as e:
        store.find_source(paper.ids, SourceKind.PDF)
    assert "digest" in str(e.value)
    assert "pmid=8" in str(e.value)
    assert file.name in str(e.value)


def test_an_unreadable_root_is_an_error(tmp_path: Path) -> None:
    import shutil

    (tmp_path / "lib").mkdir()
    store = _store(tmp_path / "lib")
    shutil.rmtree(tmp_path / "lib")
    with pytest.raises(LibraryStoreFailure):
        store.find_source(canonical_ids("1"), SourceKind.PDF)


def test_note_use_adds_this_installation_once(tmp_path: Path) -> None:
    store = _store(tmp_path)
    other = Installation.new("medicalfactchecker", "bbbb", "Mac")
    paper = _info("9")
    file = store.library.save(other, paper, b"%PDF-1", SourceKind.PDF, "t")
    mine = tmp_path / "papers" / file.folder / "records" / _ME.record_file_name()
    assert not mine.exists()
    store.note_use(paper, file)
    first = mine.read_bytes()
    store.note_use(paper, file)
    assert mine.read_bytes() == first
    assert len(store.library.scan().folders[0].used_by(file.sha256)) == 2


def test_path_of_reads_back_the_bytes_and_refuses_a_bad_component(tmp_path: Path) -> None:
    store = _store(tmp_path)
    file = store.keep(_info("3"), b"%PDF-3", SourceKind.PDF, "t")
    assert store.path_of(file).read_bytes() == b"%PDF-3"
    with pytest.raises(LibraryStoreFailure):
        store.path_of(dataclasses.replace(file, name="../x"))


def test_paper_info_comes_from_the_jats() -> None:
    article = JATSParser(_SAMPLE.read_bytes()).parse()
    got = paper_info(canonical_ids("111"), article)
    assert got.title == article.title
    assert got.year == 2024
    assert got.first_author == "Smith"
    # The request's PMID wins; the JATS fills the DOI and PMCID.
    assert got.ids == PaperIds(pmid="111", doi="10.1234/jbr.2024.001", pmcid="PMC7614751")
    assert paper_info(PaperIds(), None) == PaperInfo(PaperIds())
