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
import time
from typing import Any

from bmlib.llm import LLMClient, LLMMessage, LLMResponse
from bmlib.templates import TemplateEngine

logger = logging.getLogger(__name__)

# Provider stop_reason values that mean the output hit the max_tokens ceiling:
# Anthropic reports "max_tokens", OpenAI-compatible providers "length".
_TRUNCATION_STOP_REASONS = ("max_tokens", "length")


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

    def chat_json(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        max_retries: int = 3,
        **kwargs: object,
    ) -> dict:
        """Chat with JSON mode, retry on empty/unparseable responses.

        Combines :meth:`chat` with :meth:`parse_json` and exponential
        backoff retry.  Empty responses are treated as transport/model
        errors (WARNING).  Unparseable responses are logged at ERROR
        with the full model output for diagnosis.

        A response that stopped because it hit the ``max_tokens``
        ceiling is reported as truncation, not "unparseable response".
        At temperature 0 the retry is provably futile (greedy sampling
        reproduces the identical truncation), so it raises immediately;
        at temperature > 0 a retry may sample a shorter completion that
        fits, so truncation still gets the normal retries, but the
        final error names the real cause.

        Returns the parsed dict.  Raises :class:`ValueError` on
        truncation or after all retries are exhausted.
        """
        last_error: str | None = None
        for attempt in range(max_retries):
            if attempt > 0:
                delay = 2 ** (attempt - 1)  # 1s, 2s, 4s …
                logger.warning(
                    "Retry %d/%d after %.0fs (previous: %s)",
                    attempt + 1, max_retries, delay, last_error,
                )
                time.sleep(delay)

            response = self.chat(
                messages,
                json_mode=True,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            )

            content = response.content.strip()

            if response.stop_reason in _TRUNCATION_STOP_REASONS:
                parsed = self._try_parse(content)
                if parsed is not None:
                    # The JSON happens to be complete despite hitting the
                    # ceiling — usable as-is.
                    return parsed
                budget = max_tokens if max_tokens is not None else self.max_tokens
                truncated = (
                    f"response truncated at max_tokens={budget} "
                    f"(stop_reason={response.stop_reason!r}) — raise max_tokens "
                    "or request less output"
                )
                logger.error(
                    "LLM response truncated (attempt %d/%d), full response: %s",
                    attempt + 1, max_retries, content,
                )
                effective_temperature = (
                    temperature if temperature is not None else self.temperature
                )
                if effective_temperature == 0.0:
                    # Greedy sampling reproduces the identical truncation;
                    # retrying only pays for it again.
                    raise ValueError(truncated) from None
                # A retry at temperature > 0 may sample a shorter completion
                # that fits.
                last_error = truncated
                continue

            if not content:
                last_error = "empty response from model"
                logger.warning(
                    "LLM returned empty response (attempt %d/%d)",
                    attempt + 1, max_retries,
                )
                continue

            try:
                return self.parse_json(content)
            except ValueError:
                last_error = "unparseable response"
                logger.error(
                    "LLM returned unparseable response (attempt %d/%d), "
                    "full response: %s",
                    attempt + 1, max_retries, content,
                )
                continue

        raise ValueError(f"Failed after {max_retries} attempts: {last_error}")

    # --- Template rendering ---

    def render_template(self, template_name: str, **variables: Any) -> str:
        """Render a prompt template.  Raises if no template engine configured."""
        if self.templates is None:
            raise RuntimeError(
                f"No template engine configured — cannot render {template_name!r}"
            )
        return self.templates.render(template_name, **variables)

    # --- JSON parsing ---

    @classmethod
    def _try_parse(cls, text: str) -> dict | None:
        """:meth:`parse_json`, but ``None`` instead of ``ValueError`` on failure."""
        if not text:
            return None
        try:
            return cls.parse_json(text)
        except ValueError:
            return None

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
