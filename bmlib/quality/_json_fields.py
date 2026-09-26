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

"""Read a JSON value as the type a field is annotated with, or not at all.

Private to :mod:`bmlib.quality`; it imports nothing but the standard library
and :mod:`bmlib.quality.data_models`, which imports nothing from bmlib, so any
module in the package can use it without a cycle.  Every reader of a model's
JSON reply in the package — the Tier 2 classifier, the Tier 3 assessor, the
Cochrane assessor and the Cochrane section models — goes through these, so
the rule is stated once (issues #295, #310, #317-#320).

The rule: **absent, ``null`` and wrong-typed are the same answer — unstated.**
``data.get(k, default)`` returns its default only for an *absent* key, so a
key present with ``null`` handed the reader ``None`` — and the Tier 3 and
Cochrane prompts tell the model to answer ``null`` for what the text does not
report, while Tier 2's offers it for two of its four fields.
A wrong type is the same case one step further: ``int(True)`` is 1, so a
boolean became a measured sample size, and ``float(True)`` is 1.0, the most
confident answer there is.  ``bool`` is excluded from every numeric reader for
that reason, and a non-finite float is refused because it is no measurement
(``json.loads`` accepts ``NaN`` and ``Infinity``).

A refused value that was *present* is logged at DEBUG, naming the field.  Not
higher: no draw of model replies exists to say how often a small model sends
``"45 participants"`` for a count, and the reader this replaced dropped that
value in silence, so a WARNING would be a level set without a population.

The same package-independence rule as ``transparency``'s ``_json_*`` coercers
applies: those are restated there, not imported, and these are restated here.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from bmlib.quality.data_models import STUDY_DESIGN_MAPPING, StudyDesign

logger = logging.getLogger(__name__)


def _refused(field: str | None, value: object) -> None:
    """Log a present value that is not the type its field holds."""
    if field is not None and value is not None:
        logger.debug("Reading %s as unstated: the answer was a %s", field, type(value).__name__)


def as_dict(value: object, field: str | None = None) -> dict[str, Any]:
    """Return *value* when it is a JSON object, an empty dict otherwise.

    A model that answers ``null`` or a bare string for a whole section must
    not take the assessment down with it; the section's reader then supplies
    its own defaults.
    """
    if isinstance(value, dict):
        return value
    _refused(field, value)
    return {}


def as_text(value: object, field: str | None = None) -> str | None:
    """Return *value* when it is a string, verbatim; ``None`` otherwise.

    A number is not stringified: ``5`` in an ``evidence_level`` is a level
    nobody named, and ``"5"`` would compare unequal to every real one while
    reading as an answer.
    """
    if isinstance(value, str):
        return value
    _refused(field, value)
    return None


def text_or(value: object, default: str, field: str | None = None) -> str:
    """Return *value* when it is a string, *default* otherwise.

    An empty string is kept: ``data.get(k, default)``, which the round-trip
    readers used, kept it too, so a stored ``""`` still reads back as ``""``.
    A reader that wants empty to mean unstated writes ``as_text(...) or
    default`` instead, as the Cochrane assessor does for the model's reply.
    """
    text = as_text(value, field)
    return default if text is None else text


def as_int(value: object, field: str | None = None) -> int | None:
    """Return *value* as an ``int``, or ``None``.

    A float is truncated and a string parsed with ``int()``, which is what
    the readers this replaced did.  A ``bool`` is refused, although Python
    counts it an ``int``; so is a non-finite float, since ``int(inf)`` raises
    ``OverflowError`` — outside the ``(ValueError, TypeError)`` those readers
    caught.
    """
    result: int | None = None
    if isinstance(value, bool):
        result = None
    elif isinstance(value, int):
        result = value
    elif isinstance(value, float):
        result = int(value) if math.isfinite(value) else None
    elif isinstance(value, str):
        try:
            result = int(value)
        except ValueError:
            result = None
    if result is None:
        _refused(field, value)
    return result


def as_float(value: object, field: str | None = None) -> float | None:
    """Return *value* as a finite ``float``, or ``None``.

    A numeric string is parsed, as ``float()`` always did here; a ``bool``
    and anything non-finite are refused.
    """
    result: float | None = None
    if isinstance(value, bool):
        result = None
    elif isinstance(value, (int, float)):
        result = float(value)
    elif isinstance(value, str):
        try:
            result = float(value)
        except ValueError:
            result = None
    if result is not None and not math.isfinite(result):
        result = None
    if result is None:
        _refused(field, value)
    return result


def as_bool(value: object, field: str | None = None) -> bool | None:
    """Return *value* when it is a JSON boolean, ``None`` otherwise.

    ``"true"`` and ``1`` are refused: ``bool("no")`` is ``True``, so coercing
    a string inverts the answer rather than losing it.
    """
    if isinstance(value, bool):
        return value
    _refused(field, value)
    return None


def as_str_list(value: object, field: str | None = None) -> list[str] | None:
    """Return the string members of a JSON array, or ``None`` for a non-array.

    A non-string member is dropped rather than stringified: a number in a
    list of limitations states no limitation.  The caller chooses whether a
    non-array reads as ``None`` or as an empty list.
    """
    if not isinstance(value, list):
        _refused(field, value)
        return None
    kept = [item for item in value if isinstance(item, str)]
    if len(kept) != len(value) and field is not None:
        logger.debug("Dropped %d non-string member(s) of %s", len(value) - len(kept), field)
    return kept


def as_int_map(value: object, field: str | None = None) -> dict[str, int] | None:
    """Return the entries of a JSON object whose values read as counts.

    Each value goes through :func:`as_int`; an entry that does not read is
    dropped.  A non-object is ``None``.
    """
    if not isinstance(value, dict):
        _refused(field, value)
        return None
    counts: dict[str, int] = {}
    for key, raw in value.items():
        count = as_int(raw)
        if isinstance(key, str) and count is not None:
            counts[key] = count
    if len(counts) != len(value) and field is not None:
        logger.debug("Dropped %d unreadable entr(ies) of %s", len(value) - len(counts), field)
    return counts


def as_design(value: object, field: str | None = None) -> StudyDesign:
    """Map a model's ``study_design`` answer onto :class:`StudyDesign`.

    Case and surrounding space are folded, as they always were.  A ``null``
    (which the Tier 3 prompt permits for anything unclear) and a non-string
    read as :attr:`StudyDesign.UNKNOWN`, where ``.lower()`` on them raised
    (#295).
    """
    text = as_text(value, field)
    if text is None:
        return StudyDesign.UNKNOWN
    return STUDY_DESIGN_MAPPING.get(text.lower().strip(), StudyDesign.UNKNOWN)
