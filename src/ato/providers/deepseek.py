"""DeepSeek implementation of Ato's provider-neutral LLM interface."""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from typing import Any

from openai import APIConnectionError, APIError, AuthenticationError, OpenAI, RateLimitError

from ato.brain.messages import Message
from ato.brain.structured import StructuredOutputSpec
from ato.exceptions import LLMError, StructuredOutputError, ToolError
from ato.tools.registry import ToolRegistry

MAX_STRUCTURED_OUTPUT_CHARS = 100_000
STRUCTURED_OUTPUT_MAX_TOKENS = 4_096

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
            {"role": message.role.value, "content": message.content} for message in messages
        ]
        user_request = next(
            (message.content for message in reversed(messages) if message.role.value == "user"),
            None,
        )

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
                result = self._execute_tool_call(
                    tools,
                    call.function.name,
                    call.function.arguments,
                    user_request,
                )
                conversation.append({"role": "tool", "tool_call_id": call.id, "content": result})

    def stream(
        self,
        messages: Sequence[Message],
        tools: ToolRegistry | None = None,
    ) -> Iterator[str]:
        """Stream text while retaining the bounded tool-call loop."""
        conversation: list[dict[str, Any]] = [
            {"role": message.role.value, "content": message.content} for message in messages
        ]
        user_request = next(
            (message.content for message in reversed(messages) if message.role.value == "user"),
            None,
        )
        tool_rounds = 0

        while True:
            calls: dict[int, dict[str, Any]] = {}
            mode: str | None = None
            produced_text = False
            try:
                chunks = self._create_completion(conversation, tools, stream=True)
                for chunk in chunks:
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    delta_calls = getattr(delta, "tool_calls", None) or []
                    content = getattr(delta, "content", None)
                    if delta_calls:
                        if mode == "text":
                            raise LLMError("DeepSeek mixed text and tool calls in one stream.")
                        mode = "tools"
                        self._accumulate_tool_deltas(calls, delta_calls)
                    if content:
                        if mode == "tools":
                            raise LLMError("DeepSeek mixed text and tool calls in one stream.")
                        mode = "text"
                        produced_text = True
                        yield content
            except (AuthenticationError, RateLimitError, APIConnectionError, APIError) as exc:
                self._raise_provider_error(exc)

            if mode == "text":
                if not produced_text:
                    raise LLMError("DeepSeek returned no text response.")
                return
            if not calls:
                raise LLMError("DeepSeek returned an empty response stream.")
            if tools is None:
                raise LLMError("DeepSeek requested a tool when no registry was available.")
            if tool_rounds >= self._max_tool_rounds:
                raise LLMError("Ato stopped after reaching the tool execution limit.")
            tool_rounds += 1

            tool_calls = [calls[index] for index in sorted(calls)]
            conversation.append({"role": "assistant", "content": None, "tool_calls": tool_calls})
            for call in tool_calls:
                function = call["function"]
                result = self._execute_tool_call(
                    tools, function["name"], function["arguments"], user_request
                )
                conversation.append({"role": "tool", "tool_call_id": call["id"], "content": result})

    def generate_structured(
        self,
        messages: Sequence[Message],
        spec: StructuredOutputSpec,
    ) -> dict[str, Any]:
        """Generate one JSON object and validate it independently of the provider."""
        conversation = [{"role": "system", "content": spec.prompt_instruction()}]
        conversation.extend(
            [{"role": message.role.value, "content": message.content} for message in messages]
        )
        response = self._create_completion(
            conversation,
            tools=None,
            response_format={"type": "json_object"},
            max_tokens=STRUCTURED_OUTPUT_MAX_TOKENS,
        )
        content = (response.choices[0].message.content or "").strip()
        if not content:
            raise StructuredOutputError("DeepSeek returned empty structured output.")
        if len(content) > MAX_STRUCTURED_OUTPUT_CHARS:
            raise StructuredOutputError("DeepSeek structured output exceeded the size limit.")
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise StructuredOutputError("DeepSeek returned invalid JSON output.") from exc
        return spec.validate(parsed)

    def _create_completion(
        self,
        conversation: list[dict[str, Any]],
        tools: ToolRegistry | None,
        stream: bool = False,
        response_format: dict[str, str] | None = None,
        max_tokens: int | None = None,
    ) -> Any:
        request: dict[str, Any] = {
            "model": self._model,
            "messages": conversation,
            "stream": stream,
        }
        if tools is not None and tools.api_definitions():
            request["tools"] = tools.api_definitions()
        if response_format is not None:
            request["response_format"] = response_format
        if max_tokens is not None:
            request["max_tokens"] = max_tokens

        try:
            return self._client.chat.completions.create(**request)
        except (AuthenticationError, RateLimitError, APIConnectionError, APIError) as exc:
            self._raise_provider_error(exc)

    @staticmethod
    def _accumulate_tool_deltas(calls: dict[int, dict[str, Any]], deltas: Sequence[Any]) -> None:
        for delta in deltas:
            entry = calls.setdefault(
                delta.index,
                {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
            )
            entry["id"] += getattr(delta, "id", None) or ""
            function = getattr(delta, "function", None)
            if function is not None:
                entry["function"]["name"] += getattr(function, "name", None) or ""
                entry["function"]["arguments"] += getattr(function, "arguments", None) or ""

    @staticmethod
    def _raise_provider_error(exc: Exception) -> None:
        if isinstance(exc, AuthenticationError):
            raise LLMError("DeepSeek rejected the API key. Check DEEPSEEK_API_KEY.") from exc
        if isinstance(exc, RateLimitError):
            raise LLMError("DeepSeek rate limit reached. Wait briefly and try again.") from exc
        if isinstance(exc, APIConnectionError):
            raise LLMError(
                "Could not connect to DeepSeek. Check your internet connection."
            ) from exc
        raise LLMError(f"DeepSeek request failed: {exc}") from exc

    @staticmethod
    def _execute_tool_call(
        registry: ToolRegistry,
        name: str,
        raw_arguments: str,
        user_request: str | None,
    ) -> str:
        try:
            arguments = json.loads(raw_arguments)
            if not isinstance(arguments, dict):
                raise ToolError("Tool arguments must be a JSON object.")
            return registry.execute(name, arguments, user_request=user_request)
        except (json.JSONDecodeError, ToolError) as exc:
            return f"Tool error: {exc}"
