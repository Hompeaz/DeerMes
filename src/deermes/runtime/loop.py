from __future__ import annotations

from collections.abc import Callable
import json
from dataclasses import dataclass, field

from deermes.execution.graph import (
    TASK_BLOCKED,
    TASK_COMPLETED,
    TASK_IN_PROGRESS,
    TASK_PENDING,
    ExecutionPlan,
    ExecutionTask,
    normalize_task_status,
)
from deermes.providers.base import ModelProvider
from deermes.runtime.runlog import RunRecorder
from deermes.tools.base import ToolRegistry


@dataclass(slots=True)
class AgentAction:
    kind: str
    task_id: str = ''
    status: str = ''
    note: str = ''
    tool_name: str = ''
    tool_input: str = ''
    response: str = ''
    reasoning: str = ''


@dataclass(slots=True)
class AgentLoopState:
    goal: str
    observations: list[str] = field(default_factory=list)
    actions: list[AgentAction] = field(default_factory=list)


class AgentLoop:
    def __init__(
        self,
        provider: ModelProvider,
        tools: ToolRegistry,
        safety_action_cap: int = 32,
        stall_limit: int = 5,
        run_recorder: RunRecorder | None = None,
    ) -> None:
        self.provider = provider
        self.tools = tools
        self.safety_action_cap = max(4, safety_action_cap)
        self.stall_limit = max(2, stall_limit)
        self.run_recorder = run_recorder

    def run(
        self,
        system_prompt: str,
        goal: str,
        plan: ExecutionPlan,
        bootstrap_observations: list[str],
        event_callback: Callable[[str], None] | None = None,
    ) -> tuple[str, list[str], list[AgentAction], ExecutionPlan]:
        state = AgentLoopState(goal=goal, observations=list(bootstrap_observations))
        plan.refresh_statuses()
        if self.run_recorder is not None:
            self.run_recorder.record_plan(plan.render_tree(), summary=plan.summary, deliverable=plan.deliverable)
        _emit(event_callback, 'Planner todo tree:\n' + plan.render_tree())

        decision_count = 0
        stalled_turns = 0

        while True:
            if plan.is_complete():
                return self._finalize(system_prompt, state, plan, event_callback, completion_reason='All todo items are complete.')

            current_task = plan.next_actionable_task()
            if current_task is None:
                return self._finalize(
                    system_prompt,
                    state,
                    plan,
                    event_callback,
                    completion_reason='No actionable todo items remain.',
                )

            if current_task.status == TASK_PENDING:
                plan.mark_task(current_task.id, TASK_IN_PROGRESS, 'Started.')
                if self.run_recorder is not None:
                    self.run_recorder.record_task_status(current_task.id, current_task.title, TASK_IN_PROGRESS, 'Started.')
                _emit(event_callback, f'Started task: {current_task.title} ({current_task.id}).')
                _emit(event_callback, 'Todo status:\n' + plan.render_tree())

            if decision_count >= self.safety_action_cap:
                plan.mark_task(current_task.id, TASK_BLOCKED, 'Execution paused after hitting the internal safety cap.')
                if self.run_recorder is not None:
                    self.run_recorder.record_task_status(current_task.id, current_task.title, TASK_BLOCKED, 'Execution paused after hitting the internal safety cap.')
                _emit(event_callback, 'Safety stop reached before the todo list was fully completed.')
                _emit(event_callback, 'Todo status:\n' + plan.render_tree())
                return self._finalize(
                    system_prompt,
                    state,
                    plan,
                    event_callback,
                    completion_reason='Execution hit the internal safety cap before full completion.',
                )

            decision_count += 1
            _emit(event_callback, f'Working task: {current_task.title} ({current_task.id}).')
            provider_response = self.provider.complete(
                system_prompt=system_prompt,
                user_prompt=self._build_user_prompt(state, plan, current_task),
            )
            action = parse_agent_action(provider_response.text)
            state.actions.append(action)

            if action.reasoning.strip():
                _emit(event_callback, f'Decision note: {action.reasoning.strip()}')

            progress_made = False
            if action.kind == 'task_update':
                progress_made = self._apply_task_update(plan, action, current_task, event_callback)
            elif action.kind == 'tool' and action.tool_name and action.tool_input:
                progress_made = self._apply_tool_action(state, plan, action, current_task, event_callback)
            elif action.kind == 'final' and action.response.strip():
                if plan.is_complete():
                    _emit(event_callback, 'Model produced the final response after completing the todo list.')
                    return action.response.strip(), state.observations, state.actions, plan
                progress_made = self._apply_implicit_completion(plan, current_task, action, event_callback)
                if plan.is_complete():
                    _emit(event_callback, 'Todo list completed while handling the model final response.')
                    return action.response.strip(), state.observations, state.actions, plan
                _emit(event_callback, 'Model attempted to finalize before the todo list was complete; continuing execution.')
            elif provider_response.text.strip():
                note = _preview_text(provider_response.text, limit=240)
                progress_made = plan.add_note(current_task.id, f'Model note: {note}')
                _emit(event_callback, 'Model returned non-JSON output before completion; stored it as a task note.')
            else:
                _emit(event_callback, 'Model returned no actionable output; continuing.')

            stalled_turns = 0 if progress_made else stalled_turns + 1
            if stalled_turns >= self.stall_limit:
                note = 'Execution stalled after repeated non-progress turns.'
                plan.mark_task(current_task.id, TASK_BLOCKED, note)
                if self.run_recorder is not None:
                    self.run_recorder.record_task_status(current_task.id, current_task.title, TASK_BLOCKED, note)
                _emit(event_callback, f'Task blocked: {current_task.title} ({current_task.id}). {note}')
                _emit(event_callback, 'Todo status:\n' + plan.render_tree())
                stalled_turns = 0

    def _build_user_prompt(self, state: AgentLoopState, plan: ExecutionPlan, current_task: ExecutionTask) -> str:
        observations = '\n\n'.join(state.observations[-10:]) if state.observations else 'No observations yet.'
        action_history = '\n'.join(
            f'- {item.kind} {item.task_id or item.tool_name or item.response[:80]}' for item in state.actions[-8:]
        ) or 'No actions yet.'
        tool_descriptions = self.tools.describe()
        current_task_text = '\n'.join([
            f'Current task id: {current_task.id}',
            f'Current task title: {current_task.title}',
            f'Current task summary: {current_task.summary or "(none)"}',
            f'Current task done_when: {current_task.done_when or "(none)"}',
            'Current task notes:\n' + ('\n'.join(f'- {note}' for note in current_task.notes[-4:]) if current_task.notes else 'No notes yet.'),
        ])
        rules = [
            '- Use kind=tool when the current task needs more evidence or file access.',
            '- Use kind=task_update to mark a task as in_progress, completed, or blocked.',
            '- Use kind=final only after every todo item is completed or there is no actionable work left.',
            '- Do not skip status updates. When a task is done, explicitly return kind=task_update with status=completed.',
            '- Prefer finishing the current leaf task before moving to another task.',
            '- Treat tool_error observations as actionable feedback. Correct obvious path, quoting, and command mistakes when the fix is concrete.',
            '- Do not repeat the exact same failing tool input unless you have new evidence that it should now work.',
            '- Keep notes brief and evidence-backed.',
        ]
        return '\n\n'.join([
            'You are in a DeerMes task execution loop driven by a todo tree.',
            f'Goal:\n{state.goal.strip()}',
            'Todo tree:\n' + plan.render_tree(),
            current_task_text,
            f'Available tools:\n{tool_descriptions}',
            f'Observations so far:\n{observations}',
            f'Previous actions:\n{action_history}',
            'Return JSON only with this schema:',
            '{"kind":"tool|task_update|final","task_id":"optional task id","status":"pending|in_progress|completed|blocked","note":"brief note for task_update","tool_name":"optional","tool_input":"optional","response":"only for final","reasoning":"brief"}',
            'Rules:',
            '\n'.join(rules),
        ])

    def _apply_task_update(
        self,
        plan: ExecutionPlan,
        action: AgentAction,
        current_task: ExecutionTask,
        event_callback: Callable[[str], None] | None,
    ) -> bool:
        target_id = action.task_id or current_task.id
        target = plan.find_task(target_id)
        if target is None:
            _emit(event_callback, f'Task update referenced unknown task `{target_id}`.')
            return False
        status = normalize_task_status(action.status or TASK_COMPLETED)
        note = action.note.strip() or action.response.strip() or action.reasoning.strip()
        changed = plan.mark_task(target_id, status, note)
        if changed and self.run_recorder is not None:
            self.run_recorder.record_task_status(target_id, target.title, status, note)
        suffix = f' Note: {note}' if note else ''
        _emit(event_callback, f'Task {status}: {target.title} ({target.id}).{suffix}')
        _emit(event_callback, 'Todo status:\n' + plan.render_tree())
        return changed

    def _apply_tool_action(
        self,
        state: AgentLoopState,
        plan: ExecutionPlan,
        action: AgentAction,
        current_task: ExecutionTask,
        event_callback: Callable[[str], None] | None,
    ) -> bool:
        input_preview = _preview_text(action.tool_input)
        _emit(event_callback, f'Using tool `{action.tool_name}` for task `{current_task.id}` with input `{input_preview}`.')
        if self.run_recorder is not None:
            self.run_recorder.record_tool_invocation(action.tool_name, current_task.id, input_preview)
        result = self.tools.invoke(action.tool_name, action.tool_input)
        if self.run_recorder is not None:
            self.run_recorder.record_tool_result(action.tool_name, current_task.id, result)
        state.observations.append(f'[{current_task.id}] {result.as_observation(limit=1600)}')
        if result.ok:
            plan.add_note(current_task.id, f'{result.tool_name} succeeded.')
            _emit(event_callback, f'Tool `{result.tool_name}` completed: {_preview_text(result.output_text)}')
            return True
        note = f'{result.error_type or "ToolError"}: {_preview_text(result.output_text)}'
        plan.add_note(current_task.id, note)
        _emit(event_callback, f'Tool `{result.tool_name}` returned {result.error_type or "ToolError"}: {_preview_text(result.output_text)}')
        return False

    def _apply_implicit_completion(
        self,
        plan: ExecutionPlan,
        current_task: ExecutionTask,
        action: AgentAction,
        event_callback: Callable[[str], None] | None,
    ) -> bool:
        note = action.reasoning.strip() or _preview_text(action.response, limit=240)
        changed = plan.mark_task(current_task.id, TASK_COMPLETED, note or 'Completed while preparing the final response.')
        if changed and self.run_recorder is not None:
            self.run_recorder.record_task_status(current_task.id, current_task.title, TASK_COMPLETED, note or 'Completed while preparing the final response.')
        if changed:
            _emit(event_callback, f'Implicitly completed task {current_task.title} ({current_task.id}) from the model response.')
            _emit(event_callback, 'Todo status:\n' + plan.render_tree())
        return changed

    def _finalize(
        self,
        system_prompt: str,
        state: AgentLoopState,
        plan: ExecutionPlan,
        event_callback: Callable[[str], None] | None,
        completion_reason: str,
    ) -> tuple[str, list[str], list[AgentAction], ExecutionPlan]:
        _emit(event_callback, f'Finalizing run. {completion_reason}')
        observations = '\n\n'.join(state.observations[-12:]) if state.observations else 'No observations gathered.'
        unresolved = plan.unresolved_tasks()
        unresolved_text = '\n'.join(
            f'- {task.title} ({task.id}) [{task.status}]' for task in unresolved
        ) if unresolved else 'None.'
        user_prompt = '\n\n'.join([
            'You are preparing the final DeerMes answer.',
            f'Goal:\n{state.goal.strip()}',
            'Todo status:\n' + plan.render_tree(),
            f'Completion reason:\n{completion_reason}',
            f'Unresolved tasks:\n{unresolved_text}',
            f'Observations:\n{observations}',
            'Return JSON only with this schema:',
            '{"kind":"final","response":"direct final answer grounded in the completed tasks and observations","reasoning":"brief"}',
            'Rules:',
            '- If unresolved tasks remain, say so briefly and explain the blocker or missing evidence.',
            '- Otherwise answer directly and concretely.',
            '- Do not call tools.',
        ])
        provider_response = self.provider.complete(system_prompt=system_prompt, user_prompt=user_prompt)
        action = parse_agent_action(provider_response.text)
        state.actions.append(action)
        if action.response.strip():
            _emit(event_callback, 'Final answer completed.')
            return action.response.strip(), state.observations, state.actions, plan
        if provider_response.text.strip():
            _emit(event_callback, 'Final answer returned non-JSON output; using it directly.')
            return provider_response.text.strip(), state.observations, state.actions, plan
        fallback = 'Unable to produce a final answer from the available observations.'
        _emit(event_callback, fallback)
        return fallback, state.observations, state.actions, plan


def parse_agent_action(text: str) -> AgentAction:
    payload = _extract_json_object(text)
    if payload is None:
        return AgentAction(kind='final', response=text.strip())

    return AgentAction(
        kind=str(payload.get('kind', 'final')).strip() or 'final',
        task_id=str(payload.get('task_id', '')).strip(),
        status=str(payload.get('status', '')).strip(),
        note=str(payload.get('note', '')).strip(),
        tool_name=str(payload.get('tool_name', '')).strip(),
        tool_input=str(payload.get('tool_input', '')).strip(),
        response=str(payload.get('response', '')).strip(),
        reasoning=str(payload.get('reasoning', '')).strip(),
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


def _emit(event_callback: Callable[[str], None] | None, message: str) -> None:
    if event_callback is None:
        return
    text = message.strip()
    if text:
        event_callback(text)


def _preview_text(text: str, limit: int = 120) -> str:
    value = ' '.join(text.strip().split())
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + '...'
