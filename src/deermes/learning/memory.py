from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path


TOKEN_RE = re.compile(r'[a-zA-Z0-9_:-]+')


def utc_iso() -> str:
    return datetime.now(UTC).isoformat()


def tokenize(text: str) -> set[str]:
    return {item.lower() for item in TOKEN_RE.findall(text)}


@dataclass(slots=True)
class MemoryEntry:
    kind: str
    summary: str
    detail: str
    tags: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_iso)


class MemoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, entry: MemoryEntry) -> None:
        with self.path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(asdict(entry), ensure_ascii=True) + '\n')

    def recent(self, limit: int = 10) -> list[MemoryEntry]:
        if not self.path.exists():
            return []
        rows = self.path.read_text(encoding='utf-8').splitlines()
        items = [json.loads(row) for row in rows[-limit:] if row.strip()]
        return [MemoryEntry(**item) for item in items]

    def search(self, query: str, limit: int = 5) -> list[MemoryEntry]:
        query_tokens = tokenize(query)
        if not query_tokens:
            return self.recent(limit=limit)

        scored: list[tuple[int, MemoryEntry]] = []
        for entry in self.recent(limit=200):
            entry_tokens = tokenize(entry.summary + ' ' + entry.detail + ' ' + ' '.join(entry.tags))
            score = len(query_tokens & entry_tokens)
            if score > 0:
                scored.append((score, entry))

        scored.sort(key=lambda item: (item[0], item[1].created_at), reverse=True)
        return [entry for _, entry in scored[:limit]]
