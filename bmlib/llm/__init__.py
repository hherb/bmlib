"""LLM abstraction layer — unified interface across providers.

Usage::

    from bmlib.llm import LLMClient, LLMMessage

    client = LLMClient()
    response = client.chat(
        messages=[LLMMessage(role="user", content="Hello")],
        model="ollama:medgemma4B_it_q8",
    )
"""

from bmlib.llm.data_types import LLMMessage, LLMResponse
from bmlib.llm.client import LLMClient, get_llm_client, reset_llm_client
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
