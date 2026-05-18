"""
base_agent.py — Shared agentic loop for all Navi SDK agents.

Each agent subclass provides its system prompt and tool set.
The loop runs until the model issues an end_turn, executing
tool calls automatically in between.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import anthropic

from .tools import execute_tool

logger = logging.getLogger(__name__)


class BaseAgent:
    """
    Wraps the Anthropic messages API in an agentic loop.

    Subclasses must define:
      - SYSTEM_PROMPT: str
      - TOOLS: list[dict]   (Anthropic tool schemas)
      - MODEL: str
    """

    SYSTEM_PROMPT: str = ""
    TOOLS: list[dict] = []
    MODEL: str = "claude-opus-4-6"
    MAX_TOKENS: int = 8096
    MAX_ITERATIONS: int = 50  # safety cap on tool-call rounds

    def __init__(self) -> None:
        self.client = anthropic.Anthropic()
        self.messages: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def run(self, task: str) -> str:
        """
        Run the agent on a task string.
        Returns the final text response from the model.
        """
        logger.info("[%s] Starting task: %s", self.__class__.__name__, task[:120])
        self.messages = [{"role": "user", "content": task}]

        for iteration in range(self.MAX_ITERATIONS):
            response = self.client.messages.create(
                model=self.MODEL,
                max_tokens=self.MAX_TOKENS,
                system=self.SYSTEM_PROMPT,
                tools=self.TOOLS,
                messages=self.messages,
            )

            # Append assistant turn
            self.messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn":
                return self._extract_text(response.content)

            if response.stop_reason == "tool_use":
                tool_results = self._handle_tool_calls(response.content)
                self.messages.append({"role": "user", "content": tool_results})
                continue

            # Unexpected stop reason — surface it
            logger.warning(
                "[%s] Unexpected stop_reason: %s",
                self.__class__.__name__,
                response.stop_reason,
            )
            break

        raise RuntimeError(
            f"{self.__class__.__name__} exceeded MAX_ITERATIONS ({self.MAX_ITERATIONS}) "
            "without reaching end_turn."
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _handle_tool_calls(
        self, content: list[Any]
    ) -> list[dict[str, Any]]:
        """Execute all tool_use blocks and return tool_result messages."""
        results = []
        for block in content:
            if block.type != "tool_use":
                continue
            logger.info(
                "[%s] Tool call: %s(%s)",
                self.__class__.__name__,
                block.name,
                json.dumps(block.input)[:200],
            )
            result = execute_tool(block.name, block.input)
            logger.debug("[%s] Tool result: %s", self.__class__.__name__, str(result)[:300])
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )
        return results

    @staticmethod
    def _extract_text(content: list[Any]) -> str:
        """Extract the final text from an assistant message content list."""
        parts = [block.text for block in content if hasattr(block, "text")]
        return "\n".join(parts).strip()
