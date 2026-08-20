from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ato.brain.messages import Message, Role
from ato.brain.structured import StructuredOutputSpec
from ato.exceptions import StructuredOutputError
from ato.providers.deepseek import DeepSeekProvider

CONTACT_SPEC = StructuredOutputSpec(
    name="contact",
    description="Extract the requested contact information as JSON.",
    schema={
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "age": {"type": "integer"},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["name", "age"],
        "additionalProperties": False,
    },
)


def test_structured_spec_validates_nested_json_types() -> None:
    value = {"name": "Sam", "age": 30, "tags": ["developer"]}
    assert CONTACT_SPEC.validate(value) is value

    with pytest.raises(StructuredOutputError, match="Expected integer"):
        CONTACT_SPEC.validate({"name": "Sam", "age": "30"})
    with pytest.raises(StructuredOutputError, match="Unexpected fields"):
        CONTACT_SPEC.validate({"name": "Sam", "age": 30, "secret": True})


def test_deepseek_generates_and_validates_structured_output() -> None:
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(message=SimpleNamespace(content='{"name":"Sam","age":30,"tags":[]}'))
        ]
    )
    create = Mock(return_value=response)
    provider = DeepSeekProvider("test-key", "test-model")
    provider._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    result = provider.generate_structured([Message(Role.USER, "Sam is 30")], CONTACT_SPEC)

    assert result == {"name": "Sam", "age": 30, "tags": []}
    request = create.call_args.kwargs
    assert request["response_format"] == {"type": "json_object"}
    assert request["stream"] is False
    assert request["max_tokens"] == 4096
    assert "JSON" in request["messages"][0]["content"]


@pytest.mark.parametrize("content", ["", "not-json", "[]", '{"name":"Sam"}'])
def test_deepseek_rejects_invalid_structured_output(content: str) -> None:
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
    provider = DeepSeekProvider("test-key", "test-model")
    provider._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=Mock(return_value=response)))
    )

    with pytest.raises(StructuredOutputError):
        provider.generate_structured([Message(Role.USER, "extract")], CONTACT_SPEC)
