from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class ContextFile:
    path: Path
    content: str


@dataclass(slots=True)
class ContextBundle:
    files: list[ContextFile] = field(default_factory=list)

    def render(self) -> str:
        if not self.files:
            return ""
        parts: list[str] = []
        for item in self.files:
            parts.append(f"# {item.path}\n{item.content.strip()}")
        return "\n\n".join(parts).strip()


class ContextLoader:
    def __init__(self, root: Path, filenames: tuple[str, ...]) -> None:
        self.root = root
        self.filenames = filenames

    def load(self) -> ContextBundle:
        matches: list[ContextFile] = []
        for name in self.filenames:
            if name == "SOUL.md":
                direct = self.root / name
                if direct.exists():
                    matches.append(ContextFile(path=direct, content=direct.read_text(encoding="utf-8")))
                continue

            for path in sorted(self.root.rglob(name)):
                if path.is_file():
                    matches.append(ContextFile(path=path, content=path.read_text(encoding="utf-8")))
        return ContextBundle(files=matches)
