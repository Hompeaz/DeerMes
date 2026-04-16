from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class RunMessage:
    role: str
    content: str
    created_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class ToolObservation:
    tool_name: str
    input_text: str
    output_text: str
    created_at: datetime = field(default_factory=utc_now)
