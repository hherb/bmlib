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

"""The shared library-module contract: canonical identifiers, installations and folder names.

Asserts against ``tests/fixtures/library_cases.json``, the file Rust's
``rust/bmlib/tests/library_contract.rs`` asserts against.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bmlib.library import (
    CORRUPT_SUFFIX,
    MAX_FOLDER_NAME_BYTES,
    BadIdentifier,
    BadInstallation,
    Installation,
    LibraryError,
    NewerLibrary,
    NotALibrary,
    PaperIds,
    PaperInfo,
    RecordProblem,
    SourceKind,
    Unreachable,
    canonical_ids,
    folder_name,
    open_library,
    parse_record,
    render_record,
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


@pytest.mark.parametrize("case", _cases("folder_names"))
def test_folder_names(case: dict[str, Any]) -> None:
    assert folder_name(_info(case)) == case["expect"]


@pytest.mark.parametrize("case", _cases("folder_names"))
def test_folder_name_is_at_most_150_bytes(case: dict[str, Any]) -> None:
    assert MAX_FOLDER_NAME_BYTES == 150
    assert len(folder_name(_info(case)).encode("utf-8")) <= MAX_FOLDER_NAME_BYTES


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
