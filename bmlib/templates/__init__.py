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

"""Jinja2 template engine for prompt files.

Loads templates from a user-configurable directory with fallback to a
directory of defaults.  Supports full Jinja2 syntax (conditionals, loops,
filters).

Both directories belong to the caller — bmlib ships no templates of its own,
so ``default_dir`` is the downstream's own prompt directory and never a path
inside this package.

Usage::

    from bmlib.templates import TemplateEngine

    engine = TemplateEngine(
        user_dir=Path("~/.myapp/prompts"),
        default_dir=Path(__file__).parent / "prompts",  # the app's own
    )
    rendered = engine.render("scoring.txt", title="...", abstract="...")
"""

from bmlib.templates.engine import TemplateEngine

__all__ = ["TemplateEngine"]
