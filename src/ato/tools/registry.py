"""Allowlisted tool definitions, validation, and execution."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ato.exceptions import ToolError
from ato.security.audit import AuditLogger
from ato.security.permissions import (
    PermissionDecision,
    PermissionLevel,
    PermissionManager,
    PermissionRequest,
)

ToolHandler = Callable[[Mapping[str, Any]], str]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """One approved tool and its JSON-schema-compatible input contract."""

    name: str
    description: str
    parameters: Mapping[str, Any]
    handler: ToolHandler
    permission: PermissionLevel = PermissionLevel.LOW

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

    def __init__(
        self,
        permission_manager: PermissionManager | None = None,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._permission_manager = permission_manager or PermissionManager()
        self._audit_logger = audit_logger

    def register(self, tool: ToolSpec) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def api_definitions(self) -> list[dict[str, Any]]:
        return [tool.api_definition() for tool in self._tools.values()]

    def has_tool(self, name: str) -> bool:
        """Return whether one exact allowlisted tool is currently available."""
        return name in self._tools

    def execute(
        self,
        name: str,
        arguments: Mapping[str, Any],
        user_request: str | None = None,
    ) -> str:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolError(f"Tool is not registered: {name}")
        self._validate_arguments(tool, arguments)
        if self._audit_logger is not None:
            self._audit_logger.ensure_ready()
        request = PermissionRequest(name, tool.permission, arguments, user_request)
        decision = self._permission_manager.authorize(request)
        if decision is PermissionDecision.DENY:
            self._audit(tool, arguments, user_request, decision, error="User denied permission.")
            raise ToolError(f"Permission denied for tool: {name}")
        try:
            result = tool.handler(arguments)
        except ToolError as exc:
            self._audit(tool, arguments, user_request, decision, error=str(exc))
            raise
        except Exception as exc:
            self._audit(tool, arguments, user_request, decision, error="Tool failed safely.")
            raise ToolError(f"Tool failed safely: {name}") from exc
        self._audit(tool, arguments, user_request, decision, result=result)
        return result

    def _audit(
        self,
        tool: ToolSpec,
        arguments: Mapping[str, Any],
        user_request: str | None,
        decision: PermissionDecision,
        result: str | None = None,
        error: str | None = None,
    ) -> None:
        if self._audit_logger is not None:
            self._audit_logger.record(
                user_request=user_request,
                tool_name=tool.name,
                arguments=arguments,
                permission=tool.permission,
                decision=decision,
                result=result,
                error=error,
            )

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

        for key, value in arguments.items():
            _validate_schema_value(tool.name, key, value, properties[key])


def _validate_schema_value(
    tool_name: str, path: str, value: Any, schema: Mapping[str, Any]
) -> None:
    expected_name = schema.get("type")
    valid_type = True
    if expected_name == "string":
        valid_type = isinstance(value, str)
    elif expected_name == "boolean":
        valid_type = isinstance(value, bool)
    elif expected_name == "integer":
        valid_type = isinstance(value, int) and not isinstance(value, bool)
    elif expected_name == "number":
        valid_type = isinstance(value, (int, float)) and not isinstance(value, bool)
    elif expected_name == "array":
        valid_type = isinstance(value, list)
    elif expected_name == "object":
        valid_type = isinstance(value, Mapping)
    if not valid_type:
        raise ToolError(f"Argument {path} for {tool_name} must be {expected_name}.")

    if "enum" in schema and value not in schema["enum"]:
        raise ToolError(f"Argument {path} for {tool_name} is not an allowed value.")

    if isinstance(value, str):
        if "minLength" in schema and len(value) < int(schema["minLength"]):
            raise ToolError(f"Argument {path} for {tool_name} is too short.")
        if "maxLength" in schema and len(value) > int(schema["maxLength"]):
            raise ToolError(f"Argument {path} for {tool_name} is too long.")

    if expected_name in {"integer", "number"}:
        if "minimum" in schema and value < schema["minimum"]:
            raise ToolError(f"Argument {path} for {tool_name} is below the minimum.")
        if "maximum" in schema and value > schema["maximum"]:
            raise ToolError(f"Argument {path} for {tool_name} exceeds the maximum.")

    if isinstance(value, list):
        if "minItems" in schema and len(value) < int(schema["minItems"]):
            raise ToolError(f"Argument {path} for {tool_name} has too few items.")
        if "maxItems" in schema and len(value) > int(schema["maxItems"]):
            raise ToolError(f"Argument {path} for {tool_name} has too many items.")
        if schema.get("uniqueItems") and len({_json_safe_key(item) for item in value}) != len(
            value
        ):
            raise ToolError(f"Argument {path} for {tool_name} must contain unique items.")
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, item in enumerate(value):
                _validate_schema_value(tool_name, f"{path}[{index}]", item, item_schema)

    if isinstance(value, Mapping) and expected_name == "object":
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        unknown = set(value) - set(properties)
        if unknown and schema.get("additionalProperties") is False:
            raise ToolError(f"Argument {path} for {tool_name} has unexpected fields.")
        missing = set(required) - set(value)
        if missing:
            raise ToolError(f"Argument {path} for {tool_name} is missing required fields.")
        for key, item in value.items():
            if key in properties:
                _validate_schema_value(tool_name, f"{path}.{key}", item, properties[key])


def _json_safe_key(value: Any) -> str:
    """Return a stable comparison key for JSON-compatible array items."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
