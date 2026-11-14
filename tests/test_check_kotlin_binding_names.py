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

import pytest

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "check_kotlin_binding_names.py"
_spec = importlib.util.spec_from_file_location("check_kotlin_binding_names", _PATH)
assert _spec is not None and _spec.loader is not None
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)

# The import block and declaration shapes of the binding UniFFI 0.32.2 generates
# for bmlib-ffi, verbatim but shortened; `{decl}` is replaced by each test.
_BINDING = """@file:Suppress("NAME_SHADOWING")

package org.bmlib.ffi

// Common helper code.
import com.sun.jna.Library
import com.sun.jna.IntegerType
import com.sun.jna.Native
import com.sun.jna.Pointer
import com.sun.jna.Structure
import com.sun.jna.Callback
import com.sun.jna.ptr.*
import java.nio.ByteBuffer
import android.os.Build
import androidx.annotation.RequiresApi

/**
 * the class Library is JNA's; import com.sun.jna.Native is too
 */
@Structure.FieldOrder("capacity", "len", "data")
open class RustBuffer : Structure() {{
    class ByValue: RustBuffer(), Structure.ByValue
}}
sealed class LibraryException: kotlin.Exception() {{
    class Pointer(
        val `reason`: kotlin.String
        ) : LibraryException() {{
    }}
}}
{decl}
"""

_CLEAN = _BINDING.format(
    decl="open class PaperLibrary: Disposable, AutoCloseable, PaperLibraryInterface\n{\n}"
)


@pytest.mark.parametrize(
    ("decl", "name"),
    [
        ("open class Library: Disposable, AutoCloseable, LibraryInterface", "Library"),
        ("data class Structure (", "Structure"),
        ("enum class Native {", "Native"),
        ("sealed class Callback {", "Callback"),
        ("sealed class Build: kotlin.Exception() {", "Build"),
        ("public interface Pointer {", "Pointer"),
        ("public object Library: FfiConverter<Library, Long> {", "Library"),
        ("internal open class IntegerType(", "IntegerType"),
        ("internal object RequiresApi {", "RequiresApi"),
        ("public typealias ByteBuffer = kotlin.String", "ByteBuffer"),
        ("private class Pointer(", "Pointer"),
    ],
)
def test_each_declaration_shape_named_after_an_import_is_a_clash(decl, name):
    assert check.clashes(_BINDING.format(decl=decl)) == [name]


@pytest.mark.parametrize("name", ["List", "Map", "String", "Unit"])
def test_a_name_kotlin_imports_by_default_is_a_clash(name):
    assert check.clashes(_BINDING.format(decl=f"data class {name} (")) == [name]


def test_nested_names_comments_and_star_imports_are_not_clashes():
    # The nested `Pointer`, the KDoc's `class Library`, and `IntByReference`
    # from `com.sun.jna.ptr.*` all lose to the binding's own names.
    assert check.clashes(_CLEAN + "data class IntByReference (\n)\n") == []


def test_an_alias_is_imported_by_its_alias():
    source = "import a.b.Foo as Bar\nclass Foo\nclass Bar\n"
    assert check.clashes(source) == ["Bar"]


def test_clashes_are_sorted():
    source = _BINDING.format(decl="class Pointer\nclass Library\n")
    assert check.clashes(source) == ["Library", "Pointer"]


def test_main_names_the_clash_and_exits_one(tmp_path, capsys):
    bad = tmp_path / "bad.kt"
    bad.write_text(_BINDING.format(decl="open class Pointer"), encoding="utf-8")
    assert check.main([str(bad)]) == 1
    assert f"{bad}: declares 'Pointer', which it also imports" in capsys.readouterr().err


def test_main_exits_zero_when_clean(tmp_path, capsys):
    good = tmp_path / "good.kt"
    good.write_text(_CLEAN, encoding="utf-8")
    assert check.main([str(good)]) == 0
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("text", ["", "not kotlin at all\n", "import a.b.C\n", "class C\n"])
def test_main_refuses_a_file_it_cannot_read_as_a_binding(tmp_path, capsys, text):
    unread = tmp_path / "unread.kt"
    unread.write_text(text, encoding="utf-8")
    assert check.main([str(unread)]) == 2
    assert "no import or no declaration recognised" in capsys.readouterr().err


def test_main_exits_two_on_misuse(tmp_path, capsys):
    assert check.main([]) == 2
    assert check.main(["a.kt", "b.kt"]) == 2
    assert check.main([str(tmp_path / "missing.kt")]) == 2
    assert "cannot read" in capsys.readouterr().err
