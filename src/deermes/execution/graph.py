from __future__ import annotations

from dataclasses import dataclass, field


TASK_PENDING = 'pending'
TASK_IN_PROGRESS = 'in_progress'
TASK_COMPLETED = 'completed'
TASK_BLOCKED = 'blocked'
TASK_STATUSES = {TASK_PENDING, TASK_IN_PROGRESS, TASK_COMPLETED, TASK_BLOCKED}


@dataclass(slots=True)
class ExecutionTask:
    id: str
    title: str
    summary: str = ''
    done_when: str = ''
    tool_hints: tuple[str, ...] = field(default_factory=tuple)
    children: list['ExecutionTask'] = field(default_factory=list)
    status: str = TASK_PENDING
    notes: list[str] = field(default_factory=list)

    def is_leaf(self) -> bool:
        return not self.children

    def latest_note(self) -> str:
        return self.notes[-1].strip() if self.notes else ''


@dataclass(slots=True)
class ExecutionPlan:
    goal: str
    summary: str = ''
    deliverable: str = ''
    tasks: list[ExecutionTask] = field(default_factory=list)

    def all_tasks(self) -> list[ExecutionTask]:
        collected: list[ExecutionTask] = []
        for task in self.tasks:
            collected.extend(_walk_task(task))
        return collected

    def leaf_tasks(self) -> list[ExecutionTask]:
        return [task for task in self.all_tasks() if task.is_leaf()]

    def find_task(self, task_id: str) -> ExecutionTask | None:
        needle = task_id.strip()
        if not needle:
            return None
        for task in self.all_tasks():
            if task.id == needle:
                return task
        return None

    def next_actionable_task(self) -> ExecutionTask | None:
        self.refresh_statuses()
        in_progress = self._first_leaf_with_status(TASK_IN_PROGRESS)
        if in_progress is not None:
            return in_progress
        return self._first_leaf_with_status(TASK_PENDING)

    def has_actionable_tasks(self) -> bool:
        return self.next_actionable_task() is not None

    def is_complete(self) -> bool:
        leaves = self.leaf_tasks()
        return bool(leaves) and all(task.status == TASK_COMPLETED for task in leaves)

    def unresolved_tasks(self) -> list[ExecutionTask]:
        return [task for task in self.leaf_tasks() if task.status != TASK_COMPLETED]

    def mark_task(self, task_id: str, status: str, note: str = '') -> bool:
        task = self.find_task(task_id)
        if task is None:
            return False
        normalized = normalize_task_status(status)
        changed = task.status != normalized
        task.status = normalized
        text = note.strip()
        if text and (not task.notes or task.notes[-1] != text):
            task.notes.append(text)
            changed = True
        self.refresh_statuses()
        return changed

    def add_note(self, task_id: str, note: str) -> bool:
        task = self.find_task(task_id)
        if task is None:
            return False
        text = note.strip()
        if not text or (task.notes and task.notes[-1] == text):
            return False
        task.notes.append(text)
        return True

    def refresh_statuses(self) -> None:
        for task in self.tasks:
            _refresh_task(task)

    def render_tree(self, include_notes: bool = True) -> str:
        lines: list[str] = []
        if self.summary.strip():
            lines.append(f'Summary: {self.summary.strip()}')
        if self.deliverable.strip():
            lines.append(f'Deliverable: {self.deliverable.strip()}')
        if self.summary.strip() or self.deliverable.strip():
            lines.append('')
        if not self.tasks:
            return chr(10).join(lines + ['(no tasks)']).strip()
        for task in self.tasks:
            lines.extend(_render_task(task, depth=0, include_notes=include_notes))
        return chr(10).join(lines).strip()

    def _first_leaf_with_status(self, status: str) -> ExecutionTask | None:
        for task in self.tasks:
            match = _find_first_leaf_with_status(task, status)
            if match is not None:
                return match
        return None


def normalize_task_status(value: str) -> str:
    status = value.strip().lower()
    if status not in TASK_STATUSES:
        return TASK_PENDING
    return status


def _walk_task(task: ExecutionTask) -> list[ExecutionTask]:
    items = [task]
    for child in task.children:
        items.extend(_walk_task(child))
    return items


def _find_first_leaf_with_status(task: ExecutionTask, status: str) -> ExecutionTask | None:
    if task.is_leaf():
        return task if task.status == status else None
    for child in task.children:
        match = _find_first_leaf_with_status(child, status)
        if match is not None:
            return match
    return None


def _refresh_task(task: ExecutionTask) -> str:
    task.status = normalize_task_status(task.status)
    if task.is_leaf():
        return task.status

    child_statuses = [_refresh_task(child) for child in task.children]
    if child_statuses and all(status == TASK_COMPLETED for status in child_statuses):
        task.status = TASK_COMPLETED
    elif any(status == TASK_IN_PROGRESS for status in child_statuses):
        task.status = TASK_IN_PROGRESS
    elif any(status == TASK_PENDING for status in child_statuses):
        task.status = TASK_IN_PROGRESS if task.status == TASK_IN_PROGRESS else TASK_PENDING
    elif child_statuses and all(status == TASK_BLOCKED for status in child_statuses):
        task.status = TASK_BLOCKED
    else:
        task.status = TASK_PENDING
    return task.status


def _render_task(task: ExecutionTask, depth: int, include_notes: bool) -> list[str]:
    indent = '  ' * depth
    marker = {
        TASK_PENDING: '[ ]',
        TASK_IN_PROGRESS: '[~]',
        TASK_COMPLETED: '[x]',
        TASK_BLOCKED: '[!]',
    }.get(task.status, '[ ]')
    title = task.title.strip() or task.id
    detail = f' :: {task.summary.strip()}' if task.summary.strip() else ''
    lines = [f'{indent}{marker} {title} ({task.id}){detail}']
    if include_notes:
        note = task.latest_note()
        if note:
            lines.append(f'{indent}  note: {note}')
    for child in task.children:
        lines.extend(_render_task(child, depth + 1, include_notes=include_notes))
    return lines
