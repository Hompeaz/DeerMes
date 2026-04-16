from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


RUNTIME_CONFIG_FILENAME = 'deermes.runtime.json'


@dataclass(slots=True)
class ToolSpec:
    name: str
    enabled: bool = True
    options: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class AgentSettings:
    project_root: Path
    memory_path: Path
    context_filenames: tuple[str, ...] = ('AGENTS.md', 'SOUL.md', '.cursorrules')
    reflection_enabled: bool = True
    provider_name: str = 'echo'
    model_name: str = 'deermes-dev'
    provider_timeout_sec: int = 600
    session_context_char_limit: int = 3500
    planner_max_depth: int = 3
    planner_max_children: int = 5
    execution_safety_action_cap: int = 32
    execution_stall_limit: int = 5
    auto_reflect_tags: tuple[str, ...] = field(default_factory=lambda: ('summary', 'pattern', 'followup'))
    tool_specs: tuple[ToolSpec, ...] = field(default_factory=tuple)

    @classmethod
    def for_project(cls, project_root: Path) -> 'AgentSettings':
        state_dir = project_root / '.deermes'
        default_tools = (
            ToolSpec(name='find_files', options={'limit': 200}),
            ToolSpec(name='read_file'),
            ToolSpec(name='create_file'),
            ToolSpec(name='write_file_atomic'),
            ToolSpec(name='patch_file'),
            ToolSpec(name='write_note'),
            ToolSpec(name='shell'),
        )
        settings = cls(
            project_root=project_root,
            memory_path=state_dir / 'memory.jsonl',
            tool_specs=default_tools,
        )
        settings.apply_overrides(_load_runtime_overrides(project_root / RUNTIME_CONFIG_FILENAME))
        return settings

    def apply_overrides(self, payload: dict[str, object]) -> None:
        if not payload:
            return
        if 'provider_timeout_sec' in payload:
            self.provider_timeout_sec = max(30, int(payload['provider_timeout_sec']))
        if 'session_context_char_limit' in payload:
            self.session_context_char_limit = max(500, int(payload['session_context_char_limit']))
        if 'planner_max_depth' in payload:
            self.planner_max_depth = max(1, int(payload['planner_max_depth']))
        if 'planner_max_children' in payload:
            self.planner_max_children = max(1, int(payload['planner_max_children']))
        if 'execution_safety_action_cap' in payload:
            self.execution_safety_action_cap = max(4, int(payload['execution_safety_action_cap']))
        if 'execution_stall_limit' in payload:
            self.execution_stall_limit = max(2, int(payload['execution_stall_limit']))


def _load_runtime_overrides(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload
