from __future__ import annotations

import json
import re
from dataclasses import dataclass

from deermes.execution.graph import ExecutionPlan, ExecutionTask
from deermes.providers.base import ModelProvider


@dataclass(slots=True)
class PlannerSettings:
    max_depth: int = 3
    max_children: int = 5


class DeterministicPlanner:
    def __init__(self, provider: ModelProvider, settings: PlannerSettings | None = None) -> None:
        self.provider = provider
        self.settings = settings or PlannerSettings()

    def create_plan(
        self,
        system_prompt: str,
        goal: str,
        observations: list[str] | None = None,
    ) -> ExecutionPlan:
        prompt = self._build_planner_prompt(goal, observations or [])
        response = self.provider.complete(system_prompt=system_prompt, user_prompt=prompt).text.strip()
        plan = parse_execution_plan(response, goal=goal)
        if plan.tasks:
            return _clamp_plan(plan, settings=self.settings)
        return _fallback_plan(goal)

    def _build_planner_prompt(self, goal: str, observations: list[str]) -> str:
        observation_text = '\n\n'.join(observations[-6:]) if observations else 'No observations yet.'
        return '\n\n'.join([
            'You are the DeerMes task planner.',
            f'Goal:\n{goal.strip()}',
            f'Current observations:\n{observation_text}',
            'Return JSON only with this schema:',
            '{"summary":"short summary","deliverable":"what the run should produce","tasks":[{"id":"snake_case_id","title":"task title","summary":"why this task matters","done_when":"observable completion signal","tool_hints":["optional tool or file hint"],"children":[...same schema...]}]}',
            'Rules:',
            '- Build a todo list that is hierarchical when the task has natural substeps.',
            f'- Use at most {self.settings.max_children} top-level tasks and at most {self.settings.max_children} children per task.',
            f'- Use at most {self.settings.max_depth} levels total including the root task level.',
            '- Each leaf task must be small enough to mark as complete based on evidence.',
            '- Prefer explicit file names, commands, or outputs inside done_when when they matter.',
            '- Do not include redundant meta tasks unless they are required for completion.',
        ])


def parse_execution_plan(text: str, goal: str) -> ExecutionPlan:
    payload = _extract_json_object(text)
    if not payload:
        return ExecutionPlan(goal=goal.strip(), summary='', deliverable='', tasks=[])

    tasks_payload = payload.get('tasks', [])
    tasks = [_parse_task(item, fallback_prefix='task') for item in tasks_payload if isinstance(item, dict)]
    tasks = [task for task in tasks if task is not None]
    return ExecutionPlan(
        goal=goal.strip(),
        summary=str(payload.get('summary', '')).strip(),
        deliverable=str(payload.get('deliverable', '')).strip(),
        tasks=tasks,
    )


def _parse_task(payload: dict[str, object], fallback_prefix: str) -> ExecutionTask | None:
    title = str(payload.get('title', '')).strip()
    identifier = _normalize_task_id(str(payload.get('id', '')).strip() or title or fallback_prefix)
    if not title:
        title = identifier.replace('_', ' ').strip().title() or fallback_prefix.replace('_', ' ').title()
    children_payload = payload.get('children', [])
    children = []
    if isinstance(children_payload, list):
        for index, item in enumerate(children_payload, start=1):
            if not isinstance(item, dict):
                continue
            child = _parse_task(item, fallback_prefix=f'{identifier}_{index}')
            if child is not None:
                children.append(child)
    tool_hints_payload = payload.get('tool_hints', [])
    tool_hints = tuple(
        str(item).strip() for item in tool_hints_payload if str(item).strip()
    ) if isinstance(tool_hints_payload, list) else tuple()
    return ExecutionTask(
        id=identifier,
        title=title,
        summary=str(payload.get('summary', '')).strip(),
        done_when=str(payload.get('done_when', '')).strip(),
        tool_hints=tool_hints,
        children=children,
    )


def _normalize_task_id(value: str) -> str:
    cleaned = re.sub(r'[^a-zA-Z0-9]+', '_', value.strip().lower()).strip('_')
    return cleaned or 'task'


def _clamp_plan(plan: ExecutionPlan, settings: PlannerSettings) -> ExecutionPlan:
    return ExecutionPlan(
        goal=plan.goal,
        summary=plan.summary,
        deliverable=plan.deliverable,
        tasks=[_clamp_task(task, depth=1, settings=settings) for task in plan.tasks[: settings.max_children]],
    )


def _clamp_task(task: ExecutionTask, depth: int, settings: PlannerSettings) -> ExecutionTask:
    children: list[ExecutionTask] = []
    if depth < settings.max_depth:
        children = [
            _clamp_task(child, depth=depth + 1, settings=settings)
            for child in task.children[: settings.max_children]
        ]
    return ExecutionTask(
        id=task.id,
        title=task.title,
        summary=task.summary,
        done_when=task.done_when,
        tool_hints=task.tool_hints,
        children=children,
        status=task.status,
        notes=list(task.notes),
    )


def _fallback_plan(goal: str) -> ExecutionPlan:
    return ExecutionPlan(
        goal=goal.strip(),
        summary='Understand the request, gather only the required evidence, complete the work, and verify the result.',
        deliverable='A direct final answer or concrete artifact that satisfies the user request.',
        tasks=[
            ExecutionTask(
                id='understand_request',
                title='Understand the request and constraints',
                summary='Confirm the exact output, constraints, and important references.',
                done_when='The task scope and constraints are explicit.',
            ),
            ExecutionTask(
                id='gather_required_evidence',
                title='Gather the required evidence',
                summary='Inspect only the files, commands, or references needed to perform the task.',
                done_when='All required source material or environment evidence has been checked.',
            ),
            ExecutionTask(
                id='execute_and_verify',
                title='Execute the work and verify completion',
                summary='Perform the requested work, then verify that the requested artifact or answer is complete.',
                done_when='The requested output exists and has been verified against the user goal.',
            ),
        ],
    )


def _extract_json_object(text: str) -> dict | None:
    stripped = text.strip()
    candidates = [stripped]

    if '```' in stripped:
        for part in stripped.split('```'):
            part = part.strip()
            if not part:
                continue
            if part.startswith('json'):
                part = part[4:].strip()
            candidates.append(part)

    for candidate in candidates:
        start = candidate.find('{')
        end = candidate.rfind('}')
        if start == -1 or end == -1 or end <= start:
            continue
        chunk = candidate[start:end + 1]
        try:
            data = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None
