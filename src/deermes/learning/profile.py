from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class AgentProfile:
    source: str
    content: str


class ProfileLoader:
    def __init__(self, root: Path) -> None:
        self.root = root

    def load(self) -> list[AgentProfile]:
        candidates = [
            self.root / '.deermes' / 'profile.md',
            self.root / 'SOUL.md',
        ]
        profiles: list[AgentProfile] = []
        for path in candidates:
            if path.exists() and path.is_file():
                profiles.append(AgentProfile(source=str(path), content=path.read_text(encoding='utf-8')))
        return profiles
