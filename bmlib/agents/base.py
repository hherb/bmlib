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

"""Base agent class for LLM-powered tasks.

Provides shared infrastructure for agents that call LLMs:
- Model/provider resolution from externally-supplied configuration
- Helper methods for building messages
- JSON response parsing

Unlike the bmlibrarian_lite ``LiteBaseAgent``, this class does **not**
read config from a hardcoded path.  The calling application passes in
the model string and LLM client explicitly.

Usage::

    class ScoringAgent(BaseAgent):
        def score(self, title: str, abstract: str, interests: list[str]) -> dict:
            prompt = self.render_template("scoring.txt", ...)
            response = self.chat(
                [self.system_msg("You are ..."), self.user_msg(prompt)],
                json_mode=True,
            )
            return self.parse_json(response.content)
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from bmlib.llm import LLMClient, LLMMessage, LLMResponse
from bmlib.templates import TemplateEngine

logger = logging.getLogger(__name__)


class BaseAgent:
    """Base class for LLM-powered agents.

    Args:
        llm: The LLM client to use.
        model: Full model string (``"provider:model_name"``).
        template_engine: Template engine for loading prompt files.
        temperature: Default sampling temperature.
        max_tokens: Default max tokens.
    """

    def __init__(
        self,
        llm: LLMClient,
        model: str,
        template_engine: TemplateEngine | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> None:
        self.llm = llm
        self.model = model
        self.templates = template_engine
        self.temperature = temperature
        self.max_tokens = max_tokens

    # --- Message helpers ---

    @staticmethod
    def system_msg(content: str) -> LLMMessage:
        return LLMMessage(role="system", content=content)

    @staticmethod
    def user_msg(content: str) -> LLMMessage:
        return LLMMessage(role="user", content=content)

    @staticmethod
    def assistant_msg(content: str) -> LLMMessage:
        return LLMMessage(role="assistant", content=content)

    # --- LLM interaction ---

    def chat(
        self,
        messages: list[LLMMessage],
        *,
        json_mode: bool = False,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: object,
    ) -> LLMResponse:
        """Send a chat request through the LLM client."""
        return self.llm.chat(
            messages=messages,
            model=self.model,
            temperature=temperature if temperature is not None else self.temperature,
            max_tokens=max_tokens if max_tokens is not None else self.max_tokens,
            json_mode=json_mode,
            **kwargs,
        )

    # --- Template rendering ---

    def render_template(self, template_name: str, **variables: Any) -> str:
        """Render a prompt template.  Raises if no template engine configured."""
        if self.templates is None:
            raise RuntimeError(
                f"No template engine configured — cannot render {template_name!r}"
            )
        return self.templates.render(template_name, **variables)

    # --- JSON parsing ---

    @staticmethod
    def parse_json(text: str) -> dict:
        """Extract and parse JSON from LLM response text.

        Handles responses wrapped in markdown code blocks.
        """
        # Try direct parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try extracting from code block
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                pass

        # Try finding a JSON object
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        raise ValueError(f"Could not parse JSON from LLM response: {text[:200]!r}")
