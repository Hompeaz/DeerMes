from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path


SESSION_NAME_RE = re.compile(r'[^a-zA-Z0-9._-]+')


def utc_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class ChatMessage:
    role: str
    content: str
    created_at: str = field(default_factory=utc_iso)
    metadata: dict[str, object] = field(default_factory=dict)


class ChatSessionStore:
    def __init__(self, project_root: Path, session_name: str = 'default') -> None:
        self.project_root = project_root
        self.session_name = sanitize_session_name(session_name)
        self.path = project_root / '.deermes' / 'sessions' / f'{self.session_name}.jsonl'
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> list[ChatMessage]:
        if not self.path.exists():
            return []
        messages: list[ChatMessage] = []
        for row in self.path.read_text(encoding='utf-8').splitlines():
            if not row.strip():
                continue
            payload = json.loads(row)
            messages.append(ChatMessage(**payload))
        return messages

    def append(self, message: ChatMessage) -> None:
        with self.path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(asdict(message), ensure_ascii=True) + '\n')


def sanitize_session_name(value: str) -> str:
    cleaned = SESSION_NAME_RE.sub('-', value.strip())
    cleaned = cleaned.strip('-.')
    return cleaned or 'default'


def _include_in_context(message: ChatMessage) -> bool:
    if message.role not in {'user', 'assistant'}:
        return False
    metadata = message.metadata or {}
    if metadata.get('trace') or metadata.get('error') or metadata.get('approval') or metadata.get('progress'):
        return False
    return True


def build_session_context(messages: list[ChatMessage], history_limit: int = 8, char_limit: int = 6000) -> str:
    selected = [message for message in messages if _include_in_context(message)][-history_limit:]
    if not selected:
        return ''

    lines: list[str] = []
    remaining = char_limit
    for message in selected:
        label = message.role.capitalize()
        content = message.content.strip()
        if not content:
            continue
        block = f'{label}:\n{content}'
        if len(block) > remaining:
            block = block[: max(remaining - 3, 0)].rstrip() + '...'
        if not block.strip():
            break
        lines.append(block)
        remaining -= len(block) + 2
        if remaining <= 0:
            break

    if not lines:
        return ''

    return '\n\n'.join([
        'Conversation context from the current DeerMes terminal session:',
        *lines,
        'Use this context only when it helps answer the latest user message.',
    ])


def extract_assistant_text(raw_output: str) -> str:
    text = raw_output.strip()
    if not text:
        return ''
    marker = 'Draft Response:'
    if marker in text:
        return text.split(marker, 1)[1].strip()
    return text
