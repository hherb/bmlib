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

"""Fail when a UniFFI Kotlin binding declares a name it also imports.

Every Kotlin binding UniFFI generates imports ``com.sun.jna.Library``,
``Pointer``, ``Structure`` and others by their simple names. In Kotlin an
explicit import wins over a declaration of the same package, so an exported type
with one of those names compiles in Rust and Swift and breaks the Android build:
the FFI's ``Library`` did, and is ``PaperLibrary`` for that reason.

A declaration also beats Kotlin's default imports, which the binding uses bare
(``List<…>``, ``String``), so those names count as imported too. A star import
(``com.sun.jna.ptr.*``) is not checked: the binding uses none of its names. An
alias counts by its alias.

Usage: ``python scripts/check_kotlin_binding_names.py <bmlib_ffi.kt>``; exit 1
names each clash, exit 2 is misuse or a file with no import or no declaration
recognised (a UniFFI layout this script no longer reads).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_IMPORT = re.compile(r"^import\s+[\w.]+\.(\w+)(?:\s+as\s+(\w+))?\s*;?\s*$", re.MULTILINE)
# The default-imported names the UniFFI 0.32.2 binding for bmlib-ffi uses bare.
# shortcut: measured once from that binding; re-measure on a UniFFI upgrade.
_DEFAULT_IMPORTS = frozenset(
    {
        "Any", "ArrayList", "AutoCloseable", "Boolean", "Byte", "ByteArray", "Charsets",
        "Double", "Float", "IllegalStateException", "IndexOutOfBoundsException", "Int",
        "Iterable", "JvmField", "List", "Long", "Map", "Runnable", "RuntimeException",
        "Short", "String", "Suppress", "Synchronized", "System", "Throwable", "Throws",
        "UInt", "ULong", "UShort", "Unit",
    }
)  # fmt: skip
# Top level only: a nested class's name shadows an import inside its parent alone.
_DECLARATION = re.compile(
    r"^(?:(?:public|internal|private|open|data|enum|sealed|abstract|value|annotation|fun)\s+)*"
    r"(?:class|interface|object|typealias)\s+`?(\w+)",
    re.MULTILINE,
)


def imported_names(source: str) -> set[str]:
    """The simple names *source* imports explicitly (an alias by its alias)."""
    return {alias or name for name, alias in _IMPORT.findall(source)}


def declared_names(source: str) -> set[str]:
    """The names *source* declares at the top level."""
    return set(_DECLARATION.findall(source))


def clashes(source: str) -> list[str]:
    """The top-level names *source* declares and imports, explicitly or by default, sorted."""
    return sorted((imported_names(source) | _DEFAULT_IMPORTS) & declared_names(source))


def main(argv: list[str]) -> int:
    """Check the binding named by *argv*; 0 when clean, 1 on a clash, 2 on misuse."""
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        source = Path(argv[0]).read_text(encoding="utf-8")
    except OSError as error:
        print(f"{argv[0]}: cannot read: {error}", file=sys.stderr)
        return 2
    # An empty set would make every check pass: refuse to judge instead.
    if not imported_names(source) or not declared_names(source):
        print(f"{argv[0]}: no import or no declaration recognised", file=sys.stderr)
        return 2
    found = clashes(source)
    for name in found:
        print(f"{argv[0]}: declares {name!r}, which it also imports", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
