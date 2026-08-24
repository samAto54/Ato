import json

from ato.ui.activity import MAX_ACTIVITY_EVENTS, AuditActivityReader


def write_events(path, events) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )


def test_activity_reader_returns_newest_privacy_reduced_events(tmp_path) -> None:
    path = tmp_path / "audit.jsonl"
    write_events(
        path,
        [
            {
                "time": "2026-08-24T10:00:00+00:00",
                "tool": "search_files",
                "permission": "LOW",
                "decision": "ALLOW",
                "arguments": {"query": "private needle"},
                "result": {"status": "success"},
                "error": None,
            },
            {
                "time": "2026-08-24T10:01:00+00:00",
                "tool": "record_microphone",
                "permission": "CRITICAL",
                "decision": "DENY",
                "arguments": {"token": "secret-value"},
                "result": None,
                "error": "User denied permission: secret-value",
            },
        ],
    )
    events = AuditActivityReader(path).recent()
    assert [event.tool for event in events] == ["record_microphone", "search_files"]
    rendered = "\n".join(event.display() for event in events)
    assert "ERROR RECORDED" in rendered
    assert "private needle" not in rendered
    assert "secret-value" not in rendered


def test_activity_reader_skips_malformed_lines_and_bounds_count(tmp_path) -> None:
    path = tmp_path / "audit.jsonl"
    events = [
        {
            "time": f"event-{index}",
            "tool": "tool",
            "permission": "LOW",
            "decision": "ALLOW",
            "result": {},
            "error": None,
        }
        for index in range(MAX_ACTIVITY_EVENTS + 10)
    ]
    write_events(path, events)
    with path.open("a", encoding="utf-8") as stream:
        stream.write("not-json\n")
    recent = AuditActivityReader(path).recent()
    assert len(recent) == MAX_ACTIVITY_EVENTS - 1
    assert recent[0].time == f"event-{MAX_ACTIVITY_EVENTS + 9}"


def test_activity_reader_handles_missing_file(tmp_path) -> None:
    assert AuditActivityReader(tmp_path / "missing.jsonl").recent() == ()
