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

"""The shared record-lookup contract.

The same file Rust's ``rust/bmlib/tests/record_lookup.rs`` replays. It is
written by ``scripts/make_record_lookup_fixture.py``, which must still build
it, so a change to ``lookup_record`` that moves a result reddens this until the
fixture is regenerated — and then reddens Rust until the port follows.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from bmlib.publications.fetchers.pubmed import EFETCH_URL
from bmlib.publications.lookup import SourceOutcome

FIXTURE = Path(__file__).parent / "fixtures" / "record_lookup.json"
CONTRACT: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "make_record_lookup_fixture.py"


def _generator() -> Any:
    spec = importlib.util.spec_from_file_location("bmlib_make_record_lookup", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_generator_still_builds_the_fixture() -> None:
    assert _generator().build_contract() == CONTRACT


def test_every_outcome_appears_at_every_source_that_can_give_it() -> None:
    seen: dict[str, set[str]] = {"pubmed": set(), "openalex": set(), "europepmc": set()}
    for case in CONTRACT["cases"]:
        result = case["expected"].get("result")
        if result:
            for source, outcomes in seen.items():
                outcomes.add(result[source]["outcome"])
    every = {outcome.value for outcome in SourceOutcome}
    assert seen["pubmed"] == every
    assert seen["openalex"] == every - {"book_record"}
    assert seen["europepmc"] == every - {"book_record"}


def test_every_pubmed_body_carries_the_ncbi_doctype() -> None:
    # Rust's XML parser refuses a DTD unless told otherwise, and every live
    # efetch answer carries one (the defect `parse_ncbi_xml` fixed).
    for case in CONTRACT["cases"]:
        requests = case["expected"]["requests"]
        for request, response in zip(requests, case["responses"], strict=False):
            body = response.get("body", "")
            if request["url"] == EFETCH_URL and body.startswith("<?xml"):
                assert "<!DOCTYPE PubmedArticleSet PUBLIC" in body, case["name"]


def test_the_error_cases_make_no_request() -> None:
    errors = [case for case in CONTRACT["cases"] if "error" in case["expected"]]
    assert len(errors) >= 5
    assert all(case["expected"]["requests"] == [] for case in errors)
