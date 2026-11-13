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

"""Unit tests for ``bmlib.library.scan`` that need no fixture tree (Rust's ``scan::tests``)."""

from __future__ import annotations

from bmlib.library import (
    Installation,
    PaperFolder,
    PaperInfo,
    Record,
    Scan,
    canonical_ids,
    find_digest,
    group,
)


def _folder(
    name: str, pmid: str | None, doi: str | None, *more: tuple[str | None, str | None]
) -> PaperFolder:
    recs = [
        Record(
            Installation.new("studyanalyzer", f"a{i}", "Mac"),
            PaperInfo(canonical_ids(p, d, None)),
            (),
        )
        for i, (p, d) in enumerate([(pmid, doi), *more])
    ]
    return PaperFolder(name, tuple(recs), ())


def test_group_joins_transitively_and_keeps_scan_order() -> None:
    scan = Scan(
        (
            _folder("a", "1", None),
            _folder("b", "2", None),
            _folder("c", "1", "10.1/x"),
            _folder("d", None, "10.1/x"),
        ),
        (),
    )
    assert group(scan) == [[0, 2, 3], [1]]


def test_group_keeps_folders_without_identifiers_apart() -> None:
    scan = Scan((_folder("a", None, None), _folder("b", None, None)), ())
    assert group(scan) == [[0], [1]]


def test_ids_first_record_wins_a_conflict() -> None:
    ids = _folder("a", "1", None, ("2", "10.1/x")).ids()
    assert (ids.pmid, ids.doi) == ("1", "10.1/x")


def test_find_digest_refuses_a_malformed_digest() -> None:
    scan = Scan((), ())
    assert find_digest(scan, "ABC") is None
    assert find_digest(scan, "A" * 64) is None
