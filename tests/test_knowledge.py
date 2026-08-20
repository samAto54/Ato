import pytest

from ato.exceptions import MemoryStoreError
from ato.knowledge import SqliteKnowledgeStore


def test_knowledge_ingests_retrieves_updates_and_removes(tmp_path) -> None:
    source = tmp_path / "guide.md"
    source.write_text("# Ghana\nAccra is the capital of Ghana.\n", encoding="utf-8")
    store = SqliteKnowledgeStore(tmp_path / "data" / "knowledge.db", tmp_path)

    document = store.ingest("guide.md")
    duplicate = store.ingest("guide.md")
    results = store.search("What is Ghana's capital?")

    assert duplicate == document
    assert results and "Accra" in results[0].content
    assert "guide.md" in results[0].source
    assert store.list_documents() == (document,)

    source.write_text("# Ghana\nKumasi is a major Ghanaian city.\n", encoding="utf-8")
    updated = store.ingest("guide.md")
    assert updated.id == document.id
    assert store.search("Kumasi")
    assert store.remove_document(document.id) is True
    assert store.search("Kumasi") == ()


@pytest.mark.parametrize("path", [".env", "data/file.txt", "unsupported.pdf", "../outside.txt"])
def test_knowledge_rejects_unsafe_or_unsupported_paths(tmp_path, path: str) -> None:
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    target = tmp_path / path
    if ".." not in path:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("text", encoding="utf-8")
    store = SqliteKnowledgeStore(tmp_path / "knowledge.db", tmp_path)

    with pytest.raises(MemoryStoreError):
        store.ingest(path)


def test_knowledge_rejects_likely_secrets(tmp_path) -> None:
    (tmp_path / "notes.txt").write_text("api_key=do-not-ingest", encoding="utf-8")
    store = SqliteKnowledgeStore(tmp_path / "knowledge.db", tmp_path)

    with pytest.raises(MemoryStoreError, match="secret"):
        store.ingest("notes.txt")
