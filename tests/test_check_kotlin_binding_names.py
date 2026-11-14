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

"""``scripts/check_kotlin_binding_names.py``: a declared name that is also imported."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "check_kotlin_binding_names.py"
_spec = importlib.util.spec_from_file_location("check_kotlin_binding_names", _PATH)
assert _spec is not None and _spec.loader is not None
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)

_BINDING = """package org.bmlib.ffi

import com.sun.jna.Library
import com.sun.jna.Pointer
import com.sun.jna.ptr.*

open class {name}: Disposable, AutoCloseable, {name}Interface {{
}}
public object FfiConverterType{name}: FfiConverter<{name}, Long> {{
}}
data class PaperIds (
)
"""


def test_a_class_named_after_an_import_is_a_clash():
    assert check.clashes(_BINDING.format(name="Library")) == ["Library"]


def test_a_renamed_class_is_clean():
    assert check.clashes(_BINDING.format(name="PaperLibrary")) == []


def test_main_exits_one_on_a_clash_and_zero_when_clean(tmp_path):
    bad, good = tmp_path / "bad.kt", tmp_path / "good.kt"
    bad.write_text(_BINDING.format(name="Pointer"), encoding="utf-8")
    good.write_text(_BINDING.format(name="PaperLibrary"), encoding="utf-8")
    assert check.main([str(bad)]) == 1
    assert check.main([str(good)]) == 0
    assert check.main([]) == 2
