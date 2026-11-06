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

"""Reading a provider's model listing (#680).

:func:`read_model_listing` is pure and shared with the Rust port
(``bmlib::llm::read_model_listing``) through
``tests/fixtures/model_listing.json``. Rust's ``LlmClient::list_models``
fetches the pages and reads each with it; Python's providers list through
their SDKs (:meth:`bmlib.llm.client.LLMClient.list_models`), so here the
reader is the contract alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

ANTHROPIC_LISTING_PAGE_SIZE = 1000
"""How many models Anthropic is asked for per page: its maximum (the default
is 20, so a listing without paging would be cut short)."""

MAX_LISTING_PAGES = 100
"""The most pages one listing follows before it is refused as runaway paging;
a listing past it is an error, never a list cut short."""

GEMINI_MODEL_PREFIX = "models/"
"""The prefix Gemini's native API puts on a model id (``models/gemini-…``).
Stripped from a listed id, since a chat names the model without it and a
context-window lookup puts the id into a URL path."""


class ModelListingError(RuntimeError):
    """A body that is not a provider's model listing (Rust's
    ``ListModelsError::Malformed``)."""


@dataclass(frozen=True)
class ModelListingPage:
    """One page of a provider's model listing.

    Attributes:
        ids: The model ids on this page, in the provider's order.
        next_after: The cursor of the next page (Anthropic's ``last_id``
            while ``has_more``), or ``None`` when this page is the last.
    """

    ids: list[str] = field(default_factory=list)
    next_after: str | None = None


def read_model_listing(provider: str, body: Any) -> ModelListingPage:
    """Read one page of *provider*'s model listing out of the JSON it returned.

    Ollama's ``/api/tags`` lists ``models[].model``; every other provider
    lists ``data[].id`` (OpenAI, DeepSeek, Mistral and Gemini's
    OpenAI-compatible endpoint, and Anthropic, which alone pages:
    ``has_more`` and ``last_id``). A Gemini id loses a leading
    :data:`GEMINI_MODEL_PREFIX`.

    Args:
        provider: The registry name (``"ollama"``, ``"anthropic"``, …).
        body: The decoded JSON body.

    Returns:
        The page's ids and the next page's cursor.

    Raises:
        ModelListingError: The body is not of that shape: not an object, no
            list, an entry without a non-empty string id, or Anthropic's
            ``has_more`` without a ``last_id``. Never an empty list in place
            of a body it could not read.
    """

    def malformed(what: str) -> ModelListingError:
        return ModelListingError(f"{provider}'s model listing {what}")

    list_key, id_key = ("models", "model") if provider == "ollama" else ("data", "id")
    entries = body.get(list_key) if isinstance(body, dict) else None
    if not isinstance(entries, list):
        raise malformed(f'has no "{list_key}" list')
    ids: list[str] = []
    for entry in entries:
        model_id = entry.get(id_key) if isinstance(entry, dict) else None
        if isinstance(model_id, str) and provider == "gemini":
            model_id = model_id.removeprefix(GEMINI_MODEL_PREFIX)
        if not isinstance(model_id, str) or not model_id:
            raise malformed(f'has an entry without a "{id_key}"')
        ids.append(model_id)
    next_after = None
    if provider == "anthropic" and body.get("has_more") is True:
        last = body.get("last_id")
        if not isinstance(last, str) or not last:
            raise malformed('says it has more but gives no "last_id"')
        next_after = last
    return ModelListingPage(ids=ids, next_after=next_after)
