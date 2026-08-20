"""Allowlisted tool definitions, validation, and execution."""

from __future__ import annotations

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

        python_types = {"string": str, "boolean": bool, "integer": int, "array": list}
        for key, value in arguments.items():
            expected_name = properties[key].get("type")
            expected_type = python_types.get(expected_name)
            if expected_type is not None and not isinstance(value, expected_type):
                raise ToolError(f"Argument {key} for {tool.name} must be {expected_name}.")
            if expected_name == "array":
                item_type_name = properties[key].get("items", {}).get("type")
                item_type = python_types.get(item_type_name)
                if item_type is not None and any(not isinstance(item, item_type) for item in value):
                    raise ToolError(
                        f"Every item in {key} for {tool.name} must be {item_type_name}."
                    )
