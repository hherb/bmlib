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

"""The context windows bmlib knows by heart.

:data:`KNOWN_CONTEXT_WINDOWS` maps ``(provider, model)`` to a window in
tokens. Every row is a figure read off the provider's own model page;
``tests/fixtures/context_window.json`` is the record of each row's source and
read date, and both this table and the Rust port
(``bmlib::llm::context_window``) are tested equal to it. Keys match exactly:
a dated snapshot id or a different case is *unknown*, not guessed, because a
wrong window silently truncates or overflows a prompt.
"""

from __future__ import annotations

from collections.abc import Mapping

KNOWN_CONTEXT_WINDOWS: Mapping[tuple[str, str], int] = {
    ("openai", "gpt-6-astra"): 1_050_000,
    ("openai", "gpt-6.1-sol"): 1_050_000,
    ("openai", "gpt-6.1-terra"): 1_050_000,
    ("openai", "gpt-6-luna"): 1_050_000,
    ("openai", "gpt-4o-mini"): 128_000,
}


def known_context_window(provider: str, model: str) -> int | None:
    """Return the context window (tokens) for an exact ``(provider, model)``.

    Returns ``None`` when the pair is not in :data:`KNOWN_CONTEXT_WINDOWS`;
    there is no prefix, case-folding or suffix matching.
    """
    return KNOWN_CONTEXT_WINDOWS.get((provider, model))
