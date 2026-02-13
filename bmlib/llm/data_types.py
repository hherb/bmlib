"""Data types for LLM communication.

Provides type-safe dataclasses for messages and responses used across
all providers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional


@dataclass
class LLMMessage:
    """A message in an LLM conversation.

    Attributes:
        role: The role of the message sender (system, user, or assistant).
        content: The text content of the message.
    """

    role: Literal["system", "user", "assistant"]
    content: str


@dataclass
class LLMResponse:
    """Response from an LLM request.

    Attributes:
        content: The text response from the model.
        model: The model that generated the response.
        input_tokens: Number of input tokens used.
        output_tokens: Number of output tokens generated.
        total_tokens: Total tokens used (input + output).
        stop_reason: Why the model stopped generating.
    """

    content: str
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    stop_reason: Optional[str] = None

    def __post_init__(self) -> None:
        if self.total_tokens == 0:
            self.total_tokens = self.input_tokens + self.output_tokens
