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

"""Jinja2-based template loader with directory fallback.

Resolution order when rendering ``engine.render("scoring.txt", ...)``:

1. ``<user_dir>/scoring.txt`` — user's customised version
2. ``<default_dir>/scoring.txt`` — package-shipped default

This lets users override any prompt without touching installed code.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from jinja2 import BaseLoader, Environment, TemplateNotFound

from bmlib._atomic import atomic_write

logger = logging.getLogger(__name__)


class _FallbackLoader(BaseLoader):
    """Jinja2 loader that checks user dir first, then default dir."""

    def __init__(
        self,
        user_dir: Path | None = None,
        default_dir: Path | None = None,
    ) -> None:
        self.user_dir = user_dir
        self.default_dir = default_dir

    def get_source(
        self,
        environment: Environment,
        template: str,
    ) -> tuple[str, str, Callable[[], bool]]:
        for directory in (self.user_dir, self.default_dir):
            if directory is None:
                continue
            path = directory / template
            if path.is_file():
                source = path.read_text(encoding="utf-8")
                mtime = path.stat().st_mtime
                return source, str(path), lambda: path.stat().st_mtime == mtime
        raise TemplateNotFound(template)


class TemplateEngine:
    """Load and render Jinja2 prompt templates from disk.

    Args:
        user_dir: User override directory (checked first).
        default_dir: Package default directory (fallback).
    """

    def __init__(
        self,
        user_dir: Path | None = None,
        default_dir: Path | None = None,
    ) -> None:
        self.user_dir = Path(user_dir).expanduser() if user_dir else None
        self.default_dir = Path(default_dir).expanduser() if default_dir else None
        self._env = Environment(
            loader=_FallbackLoader(self.user_dir, self.default_dir),
            keep_trailing_newline=True,
            autoescape=False,  # Templates are plain-text prompts, not HTML
        )

    def render(self, template_name: str, **variables: Any) -> str:
        """Render a template file with the given variables.

        Raises ``jinja2.TemplateNotFound`` if the template does not
        exist in either directory.
        """
        tmpl = self._env.get_template(template_name)
        return tmpl.render(**variables)

    def has_template(self, template_name: str) -> bool:
        """Check whether a template exists in either directory."""
        try:
            self._env.get_template(template_name)
            return True
        except TemplateNotFound:
            return False

    def install_defaults(self) -> None:
        """Copy all default templates to the user directory.

        Skips templates that already exist in the user directory.

        Each copy is published atomically, and that is what makes the skip
        correct rather than merely well-intentioned (issue #73). A bare write
        interrupted partway — a full disk, a killed process — leaves a
        truncated template that ``dest.exists()`` then reports as installed,
        so it is never repaired; Jinja2 renders whatever survived, which is
        not a ``TemplateNotFound`` but a prompt missing its second half, sent
        to a model with nothing logged. Writing through
        :func:`~bmlib._atomic.atomic_write` means a faulted copy publishes
        nothing, so the next call installs it.

        The copy is byte for byte. Reading text and writing it back is not a
        copy: ``read_text`` applies universal newlines and ``write_text``
        translates back through ``os.linesep``, so the installed file's line
        endings need not be the bundled file's. A prompt reaches a model
        verbatim, so "install the default" has to mean the default.

        Raises:
            OSError: from the first copy that fails, leaving the templates
                after it uninstalled. Deliberately propagated rather than
                collected: the next call installs whatever is still missing,
                so the loop is self-repairing, and a caller who cannot write
                is better told than left believing the templates are there.
        """
        if self.user_dir is None or self.default_dir is None:
            return
        if not self.default_dir.is_dir():
            return

        self.user_dir.mkdir(parents=True, exist_ok=True)
        for src in self.default_dir.iterdir():
            if src.is_file() and src.suffix in (".txt", ".j2", ".jinja2"):
                dest = self.user_dir / src.name
                if not dest.exists():
                    atomic_write(dest, src.read_bytes())
                    logger.info("Installed default template: %s", dest)
