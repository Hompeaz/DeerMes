from pathlib import Path

from deermes.learning.memory import MemoryEntry, MemoryStore


def test_memory_store_search_prefers_overlap(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / 'memory.jsonl')
    store.append(MemoryEntry(kind='reflection', summary='Ollama provider fix', detail='provider and local model integration', tags=['ollama', 'provider']))
    store.append(MemoryEntry(kind='reflection', summary='UI polish', detail='frontend styling', tags=['ui']))

    items = store.search('improve ollama provider integration', limit=2)

    assert items
    assert items[0].summary == 'Ollama provider fix'
