"""Allowlisted tool definitions, validation, and execution."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ato.exceptions import ToolError

ToolHandler = Callable[[Mapping[str, Any]], str]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """One approved tool and its JSON-schema-compatible input contract."""

    name: str
    description: str
    parameters: Mapping[str, Any]
    handler: ToolHandler

    def api_definition(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": dict(self.parameters),
            },
        }


class ToolRegistry:
    """Store and execute only explicitly registered tools."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, tool: ToolSpec) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def api_definitions(self) -> list[dict[str, Any]]:
        return [tool.api_definition() for tool in self._tools.values()]

    def execute(self, name: str, arguments: Mapping[str, Any]) -> str:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolError(f"Tool is not registered: {name}")
        self._validate_arguments(tool, arguments)
        try:
            return tool.handler(arguments)
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(f"Tool failed safely: {name}") from exc

    @staticmethod
    def _validate_arguments(tool: ToolSpec, arguments: Mapping[str, Any]) -> None:
        if not isinstance(arguments, Mapping):
            raise ToolError(f"Arguments for {tool.name} must be an object.")

        schema = tool.parameters
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        unknown = set(arguments) - set(properties)
        if unknown:
            raise ToolError(f"Unexpected arguments for {tool.name}: {sorted(unknown)}")
        missing = set(required) - set(arguments)
        if missing:
            raise ToolError(f"Missing arguments for {tool.name}: {sorted(missing)}")

        python_types = {"string": str, "boolean": bool, "integer": int}
        for key, value in arguments.items():
            expected_name = properties[key].get("type")
            expected_type = python_types.get(expected_name)
            if expected_type is not None and not isinstance(value, expected_type):
                raise ToolError(f"Argument {key} for {tool.name} must be {expected_name}.")
