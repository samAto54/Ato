"""DeepSeek implementation of Ato's provider-neutral LLM interface."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from openai import APIConnectionError, APIError, AuthenticationError, OpenAI, RateLimitError

from ato.brain.messages import Message
from ato.exceptions import LLMError, ToolError
from ato.tools.registry import ToolRegistry


class DeepSeekProvider:
    """Generate Ato responses through DeepSeek's OpenAI-compatible API."""

    def __init__(self, api_key: str, model: str, max_tool_rounds: int = 4) -> None:
        if max_tool_rounds < 1:
            raise ValueError("max_tool_rounds must be at least 1.")
        self._client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
        self._model = model
        self._max_tool_rounds = max_tool_rounds

    def generate(
        self,
        messages: Sequence[Message],
        tools: ToolRegistry | None = None,
    ) -> str:
        conversation: list[dict[str, Any]] = [
            {"role": message.role.value, "content": message.content}
            for message in messages
        ]

        tool_rounds = 0
        while True:
            response = self._create_completion(conversation, tools)
            message = response.choices[0].message
            tool_calls = message.tool_calls or []
            if not tool_calls:
                text = (message.content or "").strip()
                if not text:
                    raise LLMError("DeepSeek returned no text response.")
                return text
            if tools is None:
                raise LLMError("DeepSeek requested a tool when no registry was available.")
            if tool_rounds >= self._max_tool_rounds:
                raise LLMError("Ato stopped after reaching the tool execution limit.")
            tool_rounds += 1

            conversation.append(
                {
                    "role": "assistant",
                    "content": message.content,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.function.name,
                                "arguments": call.function.arguments,
                            },
                        }
                        for call in tool_calls
                    ],
                }
            )
            for call in tool_calls:
                result = self._execute_tool_call(tools, call.function.name, call.function.arguments)
                conversation.append(
                    {"role": "tool", "tool_call_id": call.id, "content": result}
                )
    def _create_completion(
        self,
        conversation: list[dict[str, Any]],
        tools: ToolRegistry | None,
    ) -> Any:
        request: dict[str, Any] = {
            "model": self._model,
            "messages": conversation,
            "stream": False,
        }
        if tools is not None and tools.api_definitions():
            request["tools"] = tools.api_definitions()

        try:
            return self._client.chat.completions.create(**request)
        except AuthenticationError as exc:
            raise LLMError("DeepSeek rejected the API key. Check DEEPSEEK_API_KEY.") from exc
        except RateLimitError as exc:
            raise LLMError("DeepSeek rate limit reached. Wait briefly and try again.") from exc
        except APIConnectionError as exc:
            message = "Could not connect to DeepSeek. Check your internet connection."
            raise LLMError(message) from exc
        except APIError as exc:
            raise LLMError(f"DeepSeek request failed: {exc}") from exc

    @staticmethod
    def _execute_tool_call(registry: ToolRegistry, name: str, raw_arguments: str) -> str:
        try:
            arguments = json.loads(raw_arguments)
            if not isinstance(arguments, dict):
                raise ToolError("Tool arguments must be a JSON object.")
            return registry.execute(name, arguments)
        except (json.JSONDecodeError, ToolError) as exc:
            return f"Tool error: {exc}"
