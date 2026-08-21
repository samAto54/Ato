"""Bounded SQLite persistence and Markdown rendering for research sessions."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ato.exceptions import ResearchStoreError

MAX_RESEARCH_SESSIONS = 1_000
MAX_SESSION_JSON_CHARS = 20_000
MAX_LIST_SESSIONS = 100
MAX_REPORT_CHARS = 50_000


@dataclass(frozen=True, slots=True)
class ResearchSessionRecord:
    id: int
    query: str
    created_at: str
    successful_sources: int
    coverage: str


class SqliteResearchStore:
    """Persist bounded structured research results outside conversation history."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._initialize()

    def save(self, query: str, result: dict[str, Any]) -> ResearchSessionRecord:
        payload = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        if len(payload) > MAX_SESSION_JSON_CHARS:
            raise ResearchStoreError("Research session exceeds the persistence limit.")
        assessment = _assessment(result)
        successful_sources = int(assessment.get("successful_sources", 0))
        coverage = str(assessment.get("coverage", "none"))
        created_at = datetime.now(UTC).isoformat()
        try:
            with self._connect() as connection:
                count = connection.execute("SELECT COUNT(*) FROM research_sessions").fetchone()[0]
                if count >= MAX_RESEARCH_SESSIONS:
                    raise ResearchStoreError("Research session storage has reached its limit.")
                cursor = connection.execute(
                    "INSERT INTO research_sessions(query, created_at, payload) VALUES (?, ?, ?)",
                    (query, created_at, payload),
                )
                session_id = int(cursor.lastrowid)
        except ResearchStoreError:
            raise
        except sqlite3.Error as exc:
            raise ResearchStoreError("Research session could not be saved.") from exc
        return ResearchSessionRecord(
            session_id, query, created_at, successful_sources, coverage
        )

    def list_sessions(self, limit: int = 20) -> tuple[ResearchSessionRecord, ...]:
        if not 1 <= limit <= MAX_LIST_SESSIONS:
            raise ValueError(f"limit must be between 1 and {MAX_LIST_SESSIONS}.")
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT id, query, created_at, payload FROM research_sessions "
                    "ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise ResearchStoreError("Research sessions could not be listed.") from exc
        records = []
        for row in rows:
            payload = _parse_payload(str(row[3]))
            assessment = _assessment(payload)
            records.append(
                ResearchSessionRecord(
                    int(row[0]),
                    str(row[1]),
                    str(row[2]),
                    int(assessment.get("successful_sources", 0)),
                    str(assessment.get("coverage", "none")),
                )
            )
        return tuple(records)

    def render_markdown(self, session_id: int) -> str | None:
        if session_id < 1:
            raise ValueError("session_id must be positive.")
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT query, created_at, payload FROM research_sessions WHERE id = ?",
                    (session_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise ResearchStoreError("Research session could not be loaded.") from exc
        if row is None:
            return None
        report = _render_report(session_id, str(row[0]), str(row[1]), _parse_payload(str(row[2])))
        if len(report) > MAX_REPORT_CHARS:
            raise ResearchStoreError("Rendered research report exceeds the export limit.")
        return report

    def _initialize(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS research_sessions ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, query TEXT NOT NULL, "
                    "created_at TEXT NOT NULL, payload TEXT NOT NULL)"
                )
        except (OSError, sqlite3.Error) as exc:
            raise ResearchStoreError("Research session storage could not be initialized.") from exc

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=5)


def _parse_payload(value: str) -> dict[str, Any]:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ResearchStoreError("Stored research session contains invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise ResearchStoreError("Stored research session has an invalid structure.")
    return payload


def _assessment(payload: dict[str, Any]) -> dict[str, Any]:
    assessment = payload.get("report_assessment", {})
    if not isinstance(assessment, dict):
        raise ResearchStoreError("Stored research assessment has an invalid structure.")
    return assessment


def _render_report(session_id: int, query: str, created_at: str, result: dict[str, Any]) -> str:
    assessment = _assessment(result)
    lines = [
        f"# Ato Research Report {session_id}",
        "",
        f"**Query:** {_safe_inline(query)}",
        f"**Created:** {_safe_inline(created_at)}",
        f"**Coverage:** {_safe_inline(str(assessment.get('coverage', 'none')))}",
        "",
        "> This report contains untrusted external evidence, not verified conclusions.",
        "> Any synthesis beyond the quoted evidence must be labelled as inference.",
        "",
        "## Evidence",
        "",
    ]
    evidence = result.get("evidence_map", [])
    if not evidence:
        lines.append("No query-relevant evidence passages were extracted.")
    for item in evidence:
        lines.extend(
            [
                f"### {_safe_inline(str(item.get('evidence_id', 'unknown')))}",
                "",
                f"Source: {_safe_inline(str(item.get('source_url', '')))}",
                "",
                *_quote(str(item.get("passage", ""))),
                "",
            ]
        )
    lines.extend(["## Uncertainty and gaps", ""])
    flags = assessment.get("uncertainty_flags", [])
    lines.extend(f"- {_safe_inline(str(flag))}" for flag in flags)
    if not flags:
        lines.append(
            "- No automatic uncertainty flags were raised; source review is still required."
        )
    failures = result.get("failures", [])
    for failure in failures:
        lines.append(f"- Fetch failed: {_safe_inline(str(failure.get('source_url', '')))}")
    lines.extend(["", "## Potential disagreements", ""])
    disagreements = result.get("potential_disagreements", [])
    if not disagreements:
        lines.append("No automatic numeric disagreement hints were raised.")
    for hint in disagreements:
        evidence_ids = ", ".join(str(value) for value in hint.get("evidence_ids", []))
        lines.append(f"- Review {_safe_inline(evidence_ids)} (heuristic hint, not proof).")
    return "\n".join(lines).strip() + "\n"


def _safe_inline(value: str) -> str:
    return " ".join(_escape_markdown(value).split())


def _quote(value: str) -> list[str]:
    safe = _escape_markdown(value)
    return [f"> {line}" if line else ">" for line in safe.splitlines()]


def _escape_markdown(value: str) -> str:
    safe = value.replace("<", "&lt;").replace(">", "&gt;").replace("\\", "\\\\")
    for character in "`*_[]#!|":
        safe = safe.replace(character, f"\\{character}")
    return safe
