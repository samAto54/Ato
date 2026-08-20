"""DeepSeek implementation of Ato's provider-neutral LLM interface."""

from __future__ import annotations

from collections.abc import Sequence

from openai import APIConnectionError, APIError, AuthenticationError, OpenAI, RateLimitError

from ato.brain.messages import Message
from ato.exceptions import LLMError


class DeepSeekProvider:
    """Generate Ato responses through DeepSeek's OpenAI-compatible API."""

    def __init__(self, api_key: str, model: str) -> None:
        self._client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
        self._model = model

    def generate(self, messages: Sequence[Message]) -> str:
        conversation = [
            {"role": message.role.value, "content": message.content}
            for message in messages
        ]

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=conversation,
                stream=False,
            )
        except AuthenticationError as exc:
            raise LLMError("DeepSeek rejected the API key. Check DEEPSEEK_API_KEY.") from exc
        except RateLimitError as exc:
            raise LLMError("DeepSeek rate limit reached. Wait briefly and try again.") from exc
        except APIConnectionError as exc:
            message = "Could not connect to DeepSeek. Check your internet connection."
            raise LLMError(message) from exc
        except APIError as exc:
            raise LLMError(f"DeepSeek request failed: {exc}") from exc

        text = (response.choices[0].message.content or "").strip()
        if not text:
            raise LLMError("DeepSeek returned no text response.")
        return text
