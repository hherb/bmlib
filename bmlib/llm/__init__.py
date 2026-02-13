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

"""LLM abstraction layer — unified interface across providers.

Usage::

    from bmlib.llm import LLMClient, LLMMessage

    client = LLMClient()
    response = client.chat(
        messages=[LLMMessage(role="user", content="Hello")],
        model="ollama:medgemma4B_it_q8",
    )
"""

from bmlib.llm.client import LLMClient, get_llm_client, reset_llm_client
from bmlib.llm.data_types import LLMMessage, LLMResponse
from bmlib.llm.token_tracker import TokenTracker, get_token_tracker, reset_token_tracker

__all__ = [
    "LLMClient",
    "LLMMessage",
    "LLMResponse",
    "TokenTracker",
    "get_llm_client",
    "get_token_tracker",
    "reset_llm_client",
    "reset_token_tracker",
]
