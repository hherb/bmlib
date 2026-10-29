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

"""Every name a package exports resolves.

``from bmlib.quality import *`` raised ``AttributeError`` once, because a
re-sorted ``__all__`` turned two comments into string members; nothing caught
it.  This walks every package so the mistake cannot return silently anywhere.
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest

import bmlib

PACKAGES = sorted(
    ["bmlib"]
    + [name for _, name, is_pkg in pkgutil.walk_packages(bmlib.__path__, "bmlib.") if is_pkg]
)


@pytest.mark.parametrize("package", PACKAGES)
def test_every_name_in_all_resolves(package: str) -> None:
    module = importlib.import_module(package)
    missing = [name for name in getattr(module, "__all__", ()) if not hasattr(module, name)]

    assert missing == []


def test_the_walk_found_the_packages() -> None:
    """Anti-vacuity: a walk that found nothing would pass every case above."""
    assert "bmlib.quality" in PACKAGES
    assert len(PACKAGES) >= 10
