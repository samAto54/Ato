import json

import pytest

from ato.exceptions import ToolError
from ato.security.permissions import PermissionManager
from ato.tools import build_phase3_registry


def _approved_registry(tmp_path):
    return build_phase3_registry(tmp_path, PermissionManager(lambda request: True))


def test_numeric_python_executes_with_separate_output_and_safety_labels(tmp_path) -> None:
    registry = _approved_registry(tmp_path)

    result = json.loads(
        registry.execute(
            "execute_python_calculation",
            {"code": "radius = 5\narea = 3.14159 * radius ** 2\nprint(round(area, 2))"},
        )
    )

    assert result["exit_code"] == 0
    assert result["stdout"].strip() == "78.54"
    assert result["stderr"] == ""
    assert result["isolated_interpreter"] is True
    assert result["os_sandbox"] is False


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("import os", "Import"),
        ("open(1)", "approved numeric and print calls"),
        ("print((1).__class__)", "Attribute"),
        ("values = [1, 2]", "List"),
        ("for item in range(2):\n    print(item)", "For"),
        ("print = 3", "reserved names"),
        ("print(2 ** 101)", "Power exponent"),
    ],
)
def test_numeric_python_rejects_unsafe_or_unbounded_syntax(tmp_path, code, message) -> None:
    registry = _approved_registry(tmp_path)

    with pytest.raises(ToolError, match=message):
        registry.execute("execute_python_calculation", {"code": code})


def test_numeric_python_requires_critical_confirmation(tmp_path) -> None:
    seen = []
    registry = build_phase3_registry(
        tmp_path,
        PermissionManager(lambda request: seen.append(request) or False),
    )

    with pytest.raises(ToolError, match="Permission denied"):
        registry.execute("execute_python_calculation", {"code": "print(2 + 2)"})

    assert seen[0].level.value == "CRITICAL"


def test_numeric_python_runtime_error_is_reported_without_tool_crash(tmp_path) -> None:
    registry = _approved_registry(tmp_path)

    result = json.loads(
        registry.execute("execute_python_calculation", {"code": "print(1 / 0)"})
    )

    assert result["exit_code"] != 0
    assert result["stdout"] == ""
    assert "ZeroDivisionError" in result["stderr"]
