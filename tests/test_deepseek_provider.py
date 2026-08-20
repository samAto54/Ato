from types import SimpleNamespace
from unittest.mock import Mock

from ato.brain.messages import Message, Role
from ato.providers.deepseek import DeepSeekProvider


def test_provider_maps_messages_to_deepseek_chat_api() -> None:
    provider = DeepSeekProvider("test-key", "test-model")
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="Hello!", tool_calls=None))]
    )
    create = Mock(return_value=response)
    provider._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    result = provider.generate([Message(Role.SYSTEM, "Be helpful."), Message(Role.USER, "Hello")])
    assert result == "Hello!"
    create.assert_called_once_with(
        model="test-model",
        messages=[
            {"role": "system", "content": "Be helpful."},
            {"role": "user", "content": "Hello"},
        ],
        stream=False,
    )


def test_provider_executes_tool_and_returns_final_answer() -> None:
    from ato.tools.registry import ToolRegistry, ToolSpec

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="echo_value",
            description="Echo a value.",
            parameters={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            handler=lambda arguments: str(arguments["value"]),
        )
    )
    tool_call = SimpleNamespace(
        id="call-1",
        function=SimpleNamespace(name="echo_value", arguments='{"value": "safe"}'),
    )
    first = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[tool_call]))]
    )
    second = SimpleNamespace(
        choices=[
            SimpleNamespace(message=SimpleNamespace(content="The value is safe.", tool_calls=None))
        ]
    )
    provider = DeepSeekProvider("test-key", "test-model")
    create = Mock(side_effect=[first, second])
    provider._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    result = provider.generate([Message(Role.USER, "Use the tool")], tools=registry)

    assert result == "The value is safe."
    second_messages = create.call_args_list[1].kwargs["messages"]
    assert second_messages[-1] == {
        "role": "tool",
        "tool_call_id": "call-1",
        "content": "safe",
    }
    assert create.call_args_list[0].kwargs["tools"] == registry.api_definitions()


def test_provider_streams_text_fragments() -> None:
    chunks = [
        SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content="Hello", tool_calls=None))]
        ),
        SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content=" Sam", tool_calls=None))]
        ),
    ]
    provider = DeepSeekProvider("test-key", "test-model")
    create = Mock(return_value=iter(chunks))
    provider._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    assert list(provider.stream([Message(Role.USER, "Hi")])) == ["Hello", " Sam"]
    assert create.call_args.kwargs["stream"] is True


def test_provider_streaming_reassembles_and_executes_tool_calls() -> None:
    from ato.tools.registry import ToolRegistry, ToolSpec

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="echo_value",
            description="Echo.",
            parameters={"type": "object", "properties": {"value": {"type": "string"}}},
            handler=lambda arguments: str(arguments["value"]),
        )
    )
    tool_chunks = [
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(
                        content=None,
                        tool_calls=[
                            SimpleNamespace(
                                index=0,
                                id="call-1",
                                function=SimpleNamespace(name="echo_value", arguments='{"value":'),
                            )
                        ],
                    )
                )
            ]
        ),
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(
                        content=None,
                        tool_calls=[
                            SimpleNamespace(
                                index=0,
                                id=None,
                                function=SimpleNamespace(name=None, arguments=' "safe"}'),
                            )
                        ],
                    )
                )
            ]
        ),
    ]
    text_chunks = [
        SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content="Safe.", tool_calls=None))]
        )
    ]
    provider = DeepSeekProvider("test-key", "test-model")
    create = Mock(side_effect=[iter(tool_chunks), iter(text_chunks)])
    provider._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    assert list(provider.stream([Message(Role.USER, "Use tool")], registry)) == ["Safe."]
    assert create.call_args_list[1].kwargs["messages"][-1]["content"] == "safe"
