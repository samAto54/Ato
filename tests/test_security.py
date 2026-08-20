import json

import pytest

from ato.exceptions import ToolError
from ato.security import (
    AuditLogger,
    PermissionDecision,
    PermissionLevel,
    PermissionManager,
    PermissionRequest,
)
from ato.tools import ToolRegistry, ToolSpec


def test_permission_manager_allows_low_and_denies_protected_without_handler() -> None:
    manager = PermissionManager()

    low = PermissionRequest("read", PermissionLevel.LOW, {})
    medium = PermissionRequest("write", PermissionLevel.MEDIUM, {})

    assert manager.authorize(low) is PermissionDecision.ALLOW
    assert manager.authorize(medium) is PermissionDecision.DENY


def test_permission_manager_uses_confirmation_handler() -> None:
    seen: list[PermissionRequest] = []
    manager = PermissionManager(lambda request: not seen.append(request))
    request = PermissionRequest("create_file", PermissionLevel.MEDIUM, {"path": "note.txt"})

    assert manager.authorize(request) is PermissionDecision.ALLOW
    assert seen == [request]


def test_denied_tool_is_not_executed_and_is_audited(tmp_path) -> None:
    executed = False

    def protected_handler(arguments) -> str:
        nonlocal executed
        executed = True
        return str(arguments)

    audit_path = tmp_path / "audit.jsonl"
    registry = ToolRegistry(
        permission_manager=PermissionManager(lambda request: False),
        audit_logger=AuditLogger(audit_path),
    )
    registry.register(
        ToolSpec(
            name="protected",
            description="Protected test tool.",
            parameters={"type": "object", "properties": {}},
            handler=protected_handler,
            permission=PermissionLevel.HIGH,
        )
    )

    with pytest.raises(ToolError, match="Permission denied"):
        registry.execute("protected", {}, user_request="Do something protected")

    assert executed is False
    event = json.loads(audit_path.read_text(encoding="utf-8"))
    assert event["tool"] == "protected"
    assert event["permission"] == "HIGH"
    assert event["decision"] == "DENY"
    assert event["user_request"] == "Do something protected"


def test_successful_tool_audit_redacts_secrets_and_limits_results(tmp_path) -> None:
    audit_path = tmp_path / "audit.jsonl"
    registry = ToolRegistry(audit_logger=AuditLogger(audit_path))
    registry.register(
        ToolSpec(
            name="safe_read",
            description="Safe test read.",
            parameters={
                "type": "object",
                "properties": {"api_key": {"type": "string"}},
                "required": ["api_key"],
            },
            handler=lambda arguments: "x" * 3_000,
        )
    )

    registry.execute(
        "safe_read",
        {"api_key": "must-not-appear"},
        user_request="Use api_key=also-secret and token=another-secret",
    )

    event = json.loads(audit_path.read_text(encoding="utf-8"))
    assert event["arguments"]["api_key"] == "[REDACTED]"
    assert "must-not-appear" not in audit_path.read_text(encoding="utf-8")
    assert "also-secret" not in audit_path.read_text(encoding="utf-8")
    assert "another-secret" not in audit_path.read_text(encoding="utf-8")
    assert event["decision"] == "ALLOW"
    assert event["result"]["status"] == "success"
    assert event["result"]["characters"] == 3_000
    assert len(event["result"]["sha256"]) == 64


def test_audit_summarizes_write_payloads_and_sanitizes_other_strings(tmp_path) -> None:
    audit_path = tmp_path / "audit.jsonl"
    logger = AuditLogger(audit_path)
    content = "code with api_key=must-not-be-logged"

    logger.record(
        user_request="write it",
        tool_name="create_text_file",
        arguments={"path": "note.txt", "content": content, "note": "token=hidden"},
        permission=PermissionLevel.HIGH,
        decision=PermissionDecision.ALLOW,
        result="created",
    )

    event = json.loads(audit_path.read_text(encoding="utf-8"))
    assert event["arguments"]["content"]["characters"] == len(content)
    assert len(event["arguments"]["content"]["sha256"]) == 64
    assert "must-not-be-logged" not in audit_path.read_text(encoding="utf-8")
    assert "hidden" not in audit_path.read_text(encoding="utf-8")

    confirmation = AuditLogger.confirmation_view({"path": "note.txt", "content": content})
    assert confirmation["content"]["characters"] == len(content)
    assert "must-not-be-logged" not in confirmation["content"]["preview"]
