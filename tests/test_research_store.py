import json

import pytest

from ato.exceptions import ToolError
from ato.research import SqliteResearchStore
from ato.security.permissions import PermissionManager
from ato.tools import build_phase3_registry
from ato.tools.research import WebResearchCoordinator


def _result() -> dict:
    return {
        "query": "Ato research",
        "provider": "fake",
        "content_trust": "untrusted_external",
        "sources": [{"source_id": "S1", "source_url": "https://one.example/"}],
        "evidence_map": [
            {
                "evidence_id": "S1-E1",
                "source_id": "S1",
                "source_url": "https://one.example/",
                "passage": "Evidence <script>alert(1)</script> ![track](https://tracker.example)",
            }
        ],
        "potential_disagreements": [],
        "failures": [],
        "report_assessment": {
            "coverage": "limited",
            "successful_sources": 1,
            "uncertainty_flags": ["single_independent_host"],
        },
    }


def test_research_store_persists_lists_and_renders_bounded_markdown(tmp_path) -> None:
    path = tmp_path / "data" / "research.db"
    store = SqliteResearchStore(path)
    saved = store.save("Ato research", _result())

    restored = SqliteResearchStore(path)
    assert restored.list_sessions() == (saved,)
    report = restored.render_markdown(saved.id)

    assert report is not None
    assert "# Ato Research Report 1" in report
    assert "**Coverage:** limited" in report
    assert "Source: https://one.example/" in report
    assert "&lt;script&gt;" in report
    assert "\\!\\[track\\]" in report
    assert "untrusted external evidence" in report
    assert restored.render_markdown(999) is None


def test_coordinator_returns_persisted_session_id(tmp_path) -> None:
    class Searcher:
        def search(self, query: str, count: int = 5) -> str:
            del count
            return json.dumps(
                {
                    "query": query,
                    "provider": "fake",
                    "results": [
                        {
                            "title": "One",
                            "url": "https://one.example/",
                            "description": "Ato evidence",
                        }
                    ],
                }
            )

    def fetcher(url: str) -> str:
        return json.dumps(
            {
                "source_url": url,
                "title": "One",
                "text": "Ato evidence is available.",
                "truncated": False,
                "document_type": "webpage",
                "pages": None,
            }
        )

    store = SqliteResearchStore(tmp_path / "research.db")
    result = json.loads(WebResearchCoordinator(Searcher(), fetcher, store).research("Ato", 1))

    assert result["session_id"] == 1
    assert store.list_sessions()[0].query == "Ato"


def test_research_tools_list_and_export_without_overwriting(tmp_path) -> None:
    store = SqliteResearchStore(tmp_path / "data" / "research.db")
    session = store.save("Ato research", _result())

    class Searcher:
        def search(self, query: str, count: int = 5) -> str:
            del query, count
            return json.dumps({"results": []})

    registry = build_phase3_registry(
        tmp_path,
        PermissionManager(lambda request: True),
        web_searcher=Searcher(),
        research_store=store,
    )

    listing = json.loads(registry.execute("list_research_sessions", {}))
    (tmp_path / "reports").mkdir()
    exported = json.loads(
        registry.execute(
            "export_research_report",
            {"session_id": session.id, "path": "reports/research.md"},
        )
    )

    assert listing["sessions"][0]["id"] == session.id
    assert exported["path"] == "reports/research.md"
    assert (tmp_path / "reports" / "research.md").is_file()
    with pytest.raises(ToolError, match="never overwrites"):
        registry.execute(
            "export_research_report",
            {"session_id": session.id, "path": "reports/research.md"},
        )


def test_research_export_requires_high_confirmation(tmp_path) -> None:
    store = SqliteResearchStore(tmp_path / "research.db")
    session = store.save("Ato research", _result())

    class Searcher:
        def search(self, query: str, count: int = 5) -> str:
            del query, count
            return json.dumps({"results": []})

    registry = build_phase3_registry(tmp_path, web_searcher=Searcher(), research_store=store)

    with pytest.raises(ToolError, match="Permission denied"):
        registry.execute(
            "export_research_report",
            {"session_id": session.id, "path": "report.md"},
        )
    assert not (tmp_path / "report.md").exists()
