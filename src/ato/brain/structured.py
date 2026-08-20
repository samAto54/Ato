"""Provider-neutral structured-output contracts and validation."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ato.exceptions import StructuredOutputError

MAX_SCHEMA_CHARS = 50_000


@dataclass(frozen=True, slots=True)
class StructuredOutputSpec:
    """A named JSON output contract supplied to an LLM provider."""

    name: str
    description: str
    schema: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.description.strip():
            raise ValueError("Structured output name and description cannot be empty.")
        if self.schema.get("type") != "object":
            raise ValueError("Structured output root schema must have type object.")
        if len(json.dumps(dict(self.schema), ensure_ascii=False)) > MAX_SCHEMA_CHARS:
            raise ValueError(f"Structured output schema exceeds {MAX_SCHEMA_CHARS} characters.")

    def prompt_instruction(self) -> str:
        """Build the explicit JSON instruction required by DeepSeek JSON mode."""
        return (
            f"Return only one JSON object for {self.name}. {self.description}\n"
            "The JSON must match this schema exactly:\n"
            f"{json.dumps(dict(self.schema), ensure_ascii=False, sort_keys=True)}"
        )

    def validate(self, value: Any) -> dict[str, Any]:
        """Validate a parsed value against Ato's supported JSON Schema subset."""
        _validate_value(value, self.schema, "$")
        if not isinstance(value, dict):
            raise StructuredOutputError("Structured output root must be a JSON object.")
        return value


def _validate_value(value: Any, schema: Mapping[str, Any], path: str) -> None:
    if not isinstance(schema, Mapping):
        raise StructuredOutputError(f"Invalid schema at {path}.")
    expected = schema.get("type")
    type_checks = {
        "object": lambda item: isinstance(item, dict),
        "array": lambda item: isinstance(item, list),
        "string": lambda item: isinstance(item, str),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        "boolean": lambda item: isinstance(item, bool),
        "null": lambda item: item is None,
    }
    if expected not in type_checks:
        raise StructuredOutputError(f"Unsupported schema type at {path}: {expected!r}.")
    if not type_checks[expected](value):
        raise StructuredOutputError(f"Expected {expected} at {path}.")
    if "enum" in schema:
        allowed = schema["enum"]
        if not isinstance(allowed, list):
            raise StructuredOutputError(f"Enum schema at {path} must be a list.")
        if value not in allowed:
            raise StructuredOutputError(f"Value at {path} is not in the allowed enum.")

    if expected == "object":
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if not isinstance(properties, Mapping) or not isinstance(required, list):
            raise StructuredOutputError(f"Invalid object schema at {path}.")
        missing = set(required) - set(value)
        if missing:
            raise StructuredOutputError(f"Missing required fields at {path}: {sorted(missing)}.")
        unknown = set(value) - set(properties)
        if schema.get("additionalProperties") is False and unknown:
            raise StructuredOutputError(f"Unexpected fields at {path}: {sorted(unknown)}.")
        for key, child in value.items():
            if key in properties:
                _validate_value(child, properties[key], f"{path}.{key}")
    elif expected == "array":
        item_schema = schema.get("items")
        if not isinstance(item_schema, Mapping):
            raise StructuredOutputError(f"Array schema at {path} requires items.")
        for index, item in enumerate(value):
            _validate_value(item, item_schema, f"{path}[{index}]")
