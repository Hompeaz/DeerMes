from __future__ import annotations

from dataclasses import dataclass

from deermes.learning.memory import MemoryEntry, tokenize


@dataclass(slots=True)
class ReflectionEngine:
    def reflect(self, goal: str, final_output: str, tool_notes: list[str]) -> list[MemoryEntry]:
        if 'Draft Response:' in final_output:
            draft = final_output.split('Draft Response:', 1)[1].strip()
            if not draft:
                return []

        summary = final_output.strip().splitlines()[0] if final_output.strip() else goal
        detail = '\n'.join([
            f'goal={goal.strip()}',
            'observations=' + ' | '.join(tool_notes[:5]) if tool_notes else 'observations=none',
            'result=' + final_output.strip()[:500],
        ])
        tags = sorted(tokenize(goal))[:12]
        return [
            MemoryEntry(kind='reflection', summary=summary[:160], detail=detail, tags=tags)
        ]
