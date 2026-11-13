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

"""The shared library-module contract: canonical identifiers, installations and folder names.

Asserts against ``tests/fixtures/library_cases.json``, the file Rust's
``rust/bmlib/tests/library_contract.rs`` asserts against.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

import pytest

from bmlib.library import (
    CORRUPT_SUFFIX,
    MAX_FOLDER_NAME_BYTES,
    BadIdentifier,
    BadInstallation,
    BadName,
    DigestMismatch,
    FileRef,
    Installation,
    Library,
    LibraryError,
    Missing,
    NewerLibrary,
    NotALibrary,
    NotDownloaded,
    PaperIds,
    PaperInfo,
    RecordProblem,
    SourceKind,
    Unreachable,
    canonical_ids,
    find,
    find_digest,
    folder_name,
    folder_name_numbered,
    group,
    open_library,
    parse_record,
    render_record,
    sha256_hex,
)

_FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "library_cases.json").read_text(encoding="utf-8")
)


def _cases(section: str) -> list[Any]:
    return [pytest.param(c, id=c["name"]) for c in _FIXTURE[section]]


@pytest.mark.parametrize("case", _cases("canonical_ids"))
def test_canonical_ids(case: dict[str, Any]) -> None:
    args = (case.get("pmid"), case.get("doi"), case.get("pmcid"))
    if "error" in case:
        with pytest.raises(BadIdentifier) as err:
            canonical_ids(*args)
        assert err.value.field == case["error"]
    else:
        assert canonical_ids(*args) == PaperIds(**case["expect"])


@pytest.mark.parametrize("case", _cases("installations"))
def test_installations(case: dict[str, Any]) -> None:
    if "error" in case:
        with pytest.raises(BadInstallation) as err:
            Installation.new(case["app"], case["id"], case["label"])
        assert err.value.field == case["error"]
    else:
        inst = Installation.new(case["app"], case["id"], case["label"])
        assert inst.record_file_name() == case["expect"]


def _info(case: dict[str, Any]) -> PaperInfo:
    return PaperInfo(
        ids=PaperIds(pmid=case["pmid"], doi=case["doi"], pmcid=case["pmcid"]),
        title=case["title"],
        year=case["year"],
        first_author=case["first_author"],
    )


def _name(case: dict[str, Any]) -> str:
    """The case's name: numbered when it has an ``n``, else the plain one."""
    if "n" in case:
        return folder_name_numbered(_info(case), case["n"])
    return folder_name(_info(case))


@pytest.mark.parametrize("case", _cases("folder_names"))
def test_folder_names(case: dict[str, Any]) -> None:
    assert _name(case) == case["expect"]


@pytest.mark.parametrize("case", _cases("folder_names"))
def test_folder_name_is_at_most_150_bytes(case: dict[str, Any]) -> None:
    assert MAX_FOLDER_NAME_BYTES == 150
    assert len(_name(case).encode("utf-8")) <= MAX_FOLDER_NAME_BYTES


def test_source_kinds_round_trip() -> None:
    for kind, name, ext in [
        (SourceKind.PDF, "pdf", "pdf"),
        (SourceKind.JATS, "jats", "xml"),
        (SourceKind.HTML, "html", "html"),
        (SourceKind.MARKDOWN, "markdown", "md"),
        (SourceKind.TEXT, "text", "txt"),
    ]:
        assert kind.value == name
        assert kind.extension() == ext
        assert SourceKind.parse(name) is kind
    assert SourceKind.parse("PDF") is None
    assert CORRUPT_SUFFIX == ".corrupt"


def test_merging_fills_gaps_and_names_conflicts() -> None:
    a = canonical_ids("1", None, "PMC5")
    b = canonical_ids("2", "10.1/x", "5")
    assert a.shares_any(b)
    merged, conflicts = a.merged(b)
    assert (merged.pmid, merged.doi) == ("1", "10.1/x")
    assert conflicts == ["pmid"]
    assert not a.shares_any(PaperIds())
    assert PaperIds().is_empty()


@pytest.mark.parametrize("case", _cases("records"))
def test_records(case: dict[str, Any]) -> None:
    if case["expect"] == "ok":
        rec = parse_record(case["text"])
        assert len(rec.files) == case["files"]
        # The same bytes Rust renders: the fixture holds the expected text.
        assert render_record(rec) == case["rendered"]
        assert parse_record(render_record(rec)) == rec
        return
    with pytest.raises(RecordProblem) as err:
        parse_record(case["text"])
    assert err.value.kind == case["expect"]
    if case["expect"] == "newer":
        assert err.value.version == case["version"]
    else:
        assert err.value.reason


@pytest.mark.parametrize("case", _cases("open"))
def test_open(case: dict[str, Any], tmp_path: Path) -> None:
    root = tmp_path / "root"
    if case["entries"] is not None:
        root.mkdir()
        for e in case["entries"]:
            if "dir" in e:
                (root / e["dir"]).mkdir()
            else:
                (root / e["file"]).write_text(e["text"], encoding="utf-8")
    expect = case["expect"]
    if expect in ("created", "opened"):
        lib = open_library(root)
        assert lib.root == root
        assert (root / "papers").is_dir()
        if "marker" in case:
            assert (root / "bmlib-library.json").read_text(encoding="utf-8") == case["marker"]
        return
    kinds: dict[str, type[LibraryError]] = {
        "newer": NewerLibrary,
        "not_a_library": NotALibrary,
        "unreachable": Unreachable,
    }
    with pytest.raises(kinds[expect]) as err:
        open_library(root)
    if expect == "newer":
        assert err.value.version == case["version"]  # type: ignore[attr-defined]


_SAMPLE = Path(__file__).parent / "fixtures" / "library" / "sample"


@pytest.fixture
def sample(tmp_path: Path) -> Library:
    """The sample library copied to a temporary directory, so nothing writes into the fixture."""
    root = tmp_path / "sample"
    shutil.copytree(_SAMPLE, root)
    return open_library(root)


def test_scan_matches_the_fixture(sample: Library) -> None:
    scan = sample.scan()
    want = _FIXTURE["scan"][0]["expect"]
    got_folders = [
        {
            "folder": f.folder,
            "ids": {"pmid": f.ids().pmid, "doi": f.ids().doi, "pmcid": f.ids().pmcid},
            "records": [
                {
                    "app": r.installation.app,
                    "id": r.installation.id,
                    "files": [e.name for e in r.files],
                }
                for r in f.records
            ],
            "files_on_disk": list(f.files_on_disk),
            "used_by": {
                e.sha256: [f"{i.app}-{i.id}" for i in f.used_by(e.sha256)]
                for r in f.records
                for e in r.files
            },
        }
        for f in scan.folders
    ]
    assert got_folders == want["folders"]
    got_problems = []
    for p in scan.problems:
        d: dict[str, Any] = {"kind": p.kind}
        for key in ("folder", "file", "name"):
            if getattr(p, key) is not None:
                d[key] = getattr(p, key)
        if p.kind == "newer_record":
            d["version"] = p.version
        else:
            assert p.version is None
        if p.kind == "unreadable_record":
            assert p.reason
        got_problems.append(d)
    assert got_problems == want["problems"]


@pytest.mark.parametrize("case", _cases("find"))
def test_find_matches_the_fixture(case: dict[str, Any], sample: Library) -> None:
    ids = canonical_ids(case.get("pmid"), case.get("doi"), case.get("pmcid"))
    hits = find(sample.scan(), ids)
    assert [h.folder for h in hits] == case["expect"]


@pytest.mark.parametrize("case", _cases("find_digest"))
def test_find_digest_matches_the_fixture(case: dict[str, Any], sample: Library) -> None:
    got = find_digest(sample.scan(), case["sha256"])
    want = case["expect"]
    if want is None:
        assert got is None
    else:
        assert got == FileRef(
            want["folder"], want["name"], case["sha256"], SourceKind(want["kind"])
        )


@pytest.mark.parametrize("case", _cases("group"))
def test_group_matches_the_fixture(case: dict[str, Any], sample: Library) -> None:
    assert group(sample.scan()) == case["expect"]


@pytest.mark.parametrize("case", _cases("read"))
def test_read_matches_the_fixture(case: dict[str, Any], sample: Library) -> None:
    f = case["file"]
    ref = FileRef(f["folder"], f["name"], f["sha256"], SourceKind(f["kind"]))
    expect = case["expect"]
    if expect == "ok":
        assert sha256_hex(sample.read(ref)) == case["bytes_sha256"]
        return
    kinds: dict[str, type[LibraryError]] = {
        "digest_mismatch": DigestMismatch,
        "not_downloaded": NotDownloaded,
        "missing": Missing,
        "bad_name": BadName,
    }
    with pytest.raises(kinds[expect]) as err:
        sample.read(ref)
    if expect == "digest_mismatch":
        assert err.value.expected == case["expected"]  # type: ignore[attr-defined]
        assert err.value.found == case["found"]  # type: ignore[attr-defined]


def test_scan_never_writes_into_the_library(sample: Library) -> None:
    before = sorted(str(p) for p in sample.root.rglob("*"))
    sample.scan()
    assert sorted(str(p) for p in sample.root.rglob("*")) == before


_SAVED_AT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def _tree(root: Path) -> list[str]:
    """Every file and folder under *root* (folders with a trailing slash), sorted."""
    return sorted(
        p.relative_to(root).as_posix() + ("/" if p.is_dir() else "") for p in root.rglob("*")
    )


def _paper_info(p: dict[str, Any]) -> PaperInfo:
    return PaperInfo(
        canonical_ids(p.get("pmid"), p.get("doi"), p.get("pmcid")),
        p.get("title"),
        p.get("year"),
        p.get("first_author"),
    )


@pytest.mark.parametrize("case", _cases("save_scenarios"))
def test_save_scenarios_match_the_fixture(case: dict[str, Any], tmp_path: Path) -> None:
    (tmp_path / "lib").mkdir()
    lib = open_library(tmp_path / "lib")
    saved: list[FileRef | None] = []
    for step in case["steps"]:
        op = step["op"]
        saved.append(None)
        if op == "save":
            who = Installation.new(step["app"], step["id"], "Test Mac")
            saved[-1] = lib.save(
                who,
                _paper_info(step["paper"]),
                step["text"].encode(),
                SourceKind(step["kind"]),
                step["origin"],
            )
        elif op == "release":
            ref = saved[step["of"]]
            assert ref is not None
            lib.release(Installation.new(step["app"], step["id"], "Test Mac"), ref)
        elif op == "remove_file":
            ref = saved[step["of"]]
            assert ref is not None
            lib.remove_file(ref)
        elif op == "remove_folder":
            if step.get("expect") == "bad_name":
                with pytest.raises(BadName):
                    lib.remove_folder(step["folder"])
            else:
                lib.remove_folder(step["folder"])
        elif op == "read":
            ref = saved[step["of"]]
            assert ref is not None
            with pytest.raises(Missing):
                lib.read(ref)
        elif op == "used_by":
            ref = saved[step["of"]]
            assert ref is not None
            users = sorted(
                f"{i.app}-{i.id}"
                for f in lib.scan().folders
                if f.folder == ref.folder
                for i in f.used_by(ref.sha256)
            )
            assert users == step["expect"]
        else:
            raise AssertionError(op)
    want = case["expect"]
    assert _tree(lib.root) == want["paths"]
    for rel, record in want["records"].items():
        got = json.loads((lib.root / rel).read_text(encoding="utf-8"))
        for entry in got["files"]:
            assert _SAVED_AT.fullmatch(entry.pop("saved_at"))
        assert got == record, rel


def test_numbered_one_is_the_plain_name() -> None:
    info = _info(_FIXTURE["folder_names"][0])
    assert folder_name_numbered(info, 1) == folder_name(info)
