from types import SimpleNamespace
from unittest.mock import Mock

from ato.brain.messages import Message, Role
from ato.providers.deepseek import DeepSeekProvider


def test_provider_maps_messages_to_deepseek_chat_api() -> None:
    provider = DeepSeekProvider("test-key", "test-model")
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="Hello!"))]
    )
    create = Mock(return_value=response)
    provider._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    result = provider.generate([
        Message(Role.SYSTEM, "Be helpful."), Message(Role.USER, "Hello")
    ])
    assert result == "Hello!"
    create.assert_called_once_with(
        model="test-model",
        messages=[
            {"role": "system", "content": "Be helpful."},
            {"role": "user", "content": "Hello"},
        ],
        stream=False,
    )
