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
explicit import wins over a class of the same package, so an exported type with
one of those names compiles in Rust and Swift and breaks the Android build: the
FFI's ``Library`` did, and is ``PaperLibrary`` for that reason.

Usage: ``python scripts/check_kotlin_binding_names.py <bmlib_ffi.kt>``; exit 1
names each clash.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_IMPORT = re.compile(r"^import\s+[\w.]+\.(\w+)\s*$", re.MULTILINE)
_DECLARATION = re.compile(
    r"^(?:public\s+|internal\s+)?(?:open\s+|data\s+|enum\s+|sealed\s+|abstract\s+)*"
    r"(?:class|interface|object)\s+`?(\w+)",
    re.MULTILINE,
)


def clashes(source: str) -> list[str]:
    """The top-level names *source* both imports and declares, sorted."""
    imported = set(_IMPORT.findall(source))
    return sorted(imported & set(_DECLARATION.findall(source)))


def main(argv: list[str]) -> int:
    """Check the binding named by *argv*; 0 when clean, 1 on a clash, 2 on misuse."""
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    found = clashes(Path(argv[0]).read_text(encoding="utf-8"))
    for name in found:
        print(f"{argv[0]}: declares {name!r}, which it also imports", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
