from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from deermes.tools.base import ArtifactRecord, ToolResult


def utc_iso() -> str:
    return datetime.now(UTC).isoformat()


def new_run_id() -> str:
    timestamp = datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')
    return f'{timestamp}-{uuid4().hex[:8]}'


@dataclass(slots=True)
class RunEvent:
    event_type: str
    payload: dict[str, object]
    created_at: str = field(default_factory=utc_iso)


@dataclass(slots=True)
class RunSummary:
    run_id: str
    ledger_path: Path
    artifacts: tuple[ArtifactRecord, ...] = field(default_factory=tuple)
    final_response: str = ''
    grounded_final_response: str = ''
    grounded: bool = True
    mode: str = ''
    plan_text: str = ''

    def verified_write_artifacts(self) -> tuple[ArtifactRecord, ...]:
        return tuple(item for item in self.artifacts if item.kind == 'file_write' and item.verified)

    def artifact_lines(self) -> list[str]:
        return [item.as_text() for item in self.artifacts]


class RunRecorder:
    def __init__(self, project_root: Path, mode: str, provider_name: str, model_name: str, goal: str) -> None:
        state_dir = project_root / '.deermes' / 'runs'
        state_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = new_run_id()
        self.ledger_path = state_dir / f'{self.run_id}.jsonl'
        self.mode = mode
        self.provider_name = provider_name
        self.model_name = model_name
        self.goal = goal
        self.plan_text = ''
        self.final_response = ''
        self.grounded_final_response = ''
        self.grounded = True
        self._artifacts: list[ArtifactRecord] = []
        self.record_event('run_started', {
            'goal': goal,
            'mode': mode,
            'provider': provider_name,
            'model': model_name,
        })

    def record_event(self, event_type: str, payload: dict[str, object]) -> None:
        event = RunEvent(event_type=event_type, payload=payload)
        with self.ledger_path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(asdict(event), ensure_ascii=True) + '\n')

    def record_learning_inputs(self, profile_sources: list[str], memory_count: int, context_loaded: bool) -> None:
        self.record_event('learning_loaded', {
            'profile_sources': profile_sources,
            'memory_count': memory_count,
            'context_loaded': context_loaded,
        })

    def record_plan(self, plan_text: str, summary: str = '', deliverable: str = '') -> None:
        self.plan_text = plan_text.strip()
        self.record_event('plan_recorded', {
            'summary': summary,
            'deliverable': deliverable,
            'plan_text': self.plan_text,
        })

    def record_handoff(self, role: str, brief: str, evidence_targets: list[str] | None = None) -> None:
        self.record_event('handoff_recorded', {
            'role': role,
            'brief': brief,
            'evidence_targets': list(evidence_targets or []),
        })

    def record_task_status(self, task_id: str, title: str, status: str, note: str = '') -> None:
        self.record_event('task_status', {
            'task_id': task_id,
            'title': title,
            'status': status,
            'note': note,
        })

    def record_tool_invocation(self, tool_name: str, task_id: str, input_preview: str) -> None:
        self.record_event('tool_invocation', {
            'tool_name': tool_name,
            'task_id': task_id,
            'input_preview': input_preview,
        })

    def record_tool_result(self, tool_name: str, task_id: str, result: ToolResult) -> None:
        self.record_event('tool_result', {
            'tool_name': tool_name,
            'task_id': task_id,
            'ok': result.ok,
            'error_type': result.error_type,
            'output_preview': _preview_text(result.output_text, limit=240),
            'artifacts': [item.to_payload() for item in result.artifacts],
        })
        if result.artifacts:
            self._artifacts.extend(result.artifacts)

    def record_reflection(self, entries_written: int) -> None:
        self.record_event('reflection_written', {'entries_written': entries_written})

    def record_final_response(self, final_response: str, grounded_final_response: str, grounded: bool) -> None:
        self.final_response = final_response
        self.grounded_final_response = grounded_final_response
        self.grounded = grounded
        self.record_event('final_response', {
            'grounded': grounded,
            'final_response': final_response,
            'grounded_final_response': grounded_final_response,
            'verified_write_paths': [item.path for item in self.verified_write_artifacts()],
        })

    def record_run_finished(self) -> None:
        self.record_event('run_finished', {
            'artifact_count': len(self._artifacts),
            'grounded': self.grounded,
        })

    def verified_write_artifacts(self) -> tuple[ArtifactRecord, ...]:
        return tuple(item for item in self._artifacts if item.kind == 'file_write' and item.verified)

    def summary(self) -> RunSummary:
        return RunSummary(
            run_id=self.run_id,
            ledger_path=self.ledger_path,
            artifacts=tuple(self._artifacts),
            final_response=self.final_response,
            grounded_final_response=self.grounded_final_response,
            grounded=self.grounded,
            mode=self.mode,
            plan_text=self.plan_text,
        )


def ground_final_response(response: str, artifacts: tuple[ArtifactRecord, ...]) -> tuple[str, bool]:
    text = response.strip()
    verified_writes = [item for item in artifacts if item.kind == 'file_write' and item.verified]
    if verified_writes:
        lines = [text] if text else []
        lines.append('')
        lines.append('Verified write artifacts:')
        for artifact in verified_writes:
            label = artifact.path or artifact.summary or '(unknown path)'
            lines.append(f'- {label}')
        return '\n'.join(lines).strip(), True

    if _contains_unverified_write_claim(text):
        return (
            'Unverified write claim blocked.\n'
            'No file creation or modification was verified in this run.\n'
            'Review the recorded artifacts and tool results before treating any write claim as real.'
        ), False

    return text, True


def _contains_unverified_write_claim(text: str) -> bool:
    lowered = text.lower()
    if not lowered:
        return False
    write_markers = ('wrote', 'written', 'saved', 'created', 'generated', 'updated', 'modified', 'patched')
    file_markers = (' file', ' path', '.py', '.ts', '.js', '.json', '.md', '.txt', '.ass', '.srt', '/')
    return any(marker in lowered for marker in write_markers) and any(marker in lowered for marker in file_markers)


def _preview_text(text: str, limit: int = 120) -> str:
    value = ' '.join(text.strip().split())
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + '...'
