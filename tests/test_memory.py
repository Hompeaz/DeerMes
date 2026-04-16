from pathlib import Path

from deermes.learning.memory import MemoryEntry, MemoryStore


def test_memory_store_round_trip(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.jsonl")
    store.append(MemoryEntry(kind="reflection", summary="hello", detail="world"))
    items = store.recent()
    assert len(items) == 1
    assert items[0].summary == "hello"
