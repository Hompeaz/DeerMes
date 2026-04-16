from __future__ import annotations

from collections.abc import Callable
import json
from dataclasses import dataclass, field

from deermes.execution.deerflow.roles import DeerflowRoleSpec, default_deerflow_roles
from deermes.execution.graph import ExecutionPlan, ExecutionTask
from deermes.runtime.loop import AgentLoop
from deermes.runtime.runlog import RunRecorder
from deermes.tools.base import ToolRegistry


@dataclass(slots=True)
class DeerflowPlannerBrief:
    summary: str
    evidence_targets: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    deliverable: str = ''


@dataclass(slots=True)
class DeerflowHandoff:
    role: str
    brief: str
    evidence_targets: list[str] = field(default_factory=list)
    observations: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DeerflowRunResult:
    final_response: str
    observations: list[str]
    handoffs: list[DeerflowHandoff] = field(default_factory=list)


class DeerflowSupervisor:
    def __init__(self, provider, tools: ToolRegistry, safety_action_cap: int = 32, stall_limit: int = 5) -> None:
        self.provider = provider
        self.tools = tools
        self.safety_action_cap = max(4, safety_action_cap)
        self.stall_limit = max(2, stall_limit)
        self.roles = default_deerflow_roles()

    def run(
        self,
        system_prompt: str,
        goal: str,
        bootstrap_observations: list[str],
        event_callback: Callable[[str], None] | None = None,
        run_recorder: RunRecorder | None = None,
    ) -> DeerflowRunResult:
        planner = self._role('planner')
        researcher = self._role('researcher')
        synthesizer = self._role('synthesizer')
        handoffs: list[DeerflowHandoff] = []

        _emit(event_callback, 'Planner: generating a structured brief.')
        plan_brief = self._plan(system_prompt, planner, goal, bootstrap_observations)
        _emit(event_callback, f'Planner summary: {plan_brief.summary}')
        if plan_brief.evidence_targets:
            _emit(event_callback, 'Planner evidence targets: ' + ', '.join(plan_brief.evidence_targets[:4]))
        if plan_brief.questions:
            _emit(event_callback, 'Planner questions: ' + '; '.join(plan_brief.questions[:3]))
        planner_handoff = DeerflowHandoff(
            role=planner.name,
            brief=plan_brief.summary,
            evidence_targets=list(plan_brief.evidence_targets),
            observations=list(bootstrap_observations),
        )
        handoffs.append(planner_handoff)
        if run_recorder is not None:
            run_recorder.record_handoff(planner_handoff.role, planner_handoff.brief, planner_handoff.evidence_targets)

        researcher_tools = self.tools.subset(researcher.tool_names)
        research_plan = self._build_research_plan(plan_brief)
        research_goal = '\n\n'.join([
            f'You are the {researcher.name}. {researcher.purpose}',
            f'Original goal:\n{goal}',
            f'Planner summary:\n{plan_brief.summary}',
            'Evidence targets:\n' + ('\n'.join(f'- {item}' for item in plan_brief.evidence_targets) if plan_brief.evidence_targets else 'None specified.'),
            'Questions to answer:\n' + ('\n'.join(f'- {item}' for item in plan_brief.questions) if plan_brief.questions else 'None specified.'),
            'Use tools only when they help complete the research todo list.',
        ])
        _emit(event_callback, 'Researcher: collecting evidence.')
        researcher_loop = AgentLoop(
            provider=self.provider,
            tools=researcher_tools,
            safety_action_cap=self.safety_action_cap,
            stall_limit=self.stall_limit,
            run_recorder=run_recorder,
        )
        research_response, observations, _actions, final_research_plan = researcher_loop.run(
            system_prompt=system_prompt,
            goal=research_goal,
            plan=research_plan,
            bootstrap_observations=bootstrap_observations,
            event_callback=_prefixed_callback(event_callback, 'Researcher'),
        )
        _emit(event_callback, 'Researcher: produced an interim conclusion.')
        researcher_handoff = DeerflowHandoff(
            role=researcher.name,
            brief=research_response,
            evidence_targets=list(plan_brief.evidence_targets),
            observations=list(observations) + ['research_todo:\n' + final_research_plan.render_tree()],
        )
        handoffs.append(researcher_handoff)
        if run_recorder is not None:
            run_recorder.record_handoff(researcher_handoff.role, researcher_handoff.brief, researcher_handoff.evidence_targets)

        final_prompt = '\n\n'.join([
            f'You are the {synthesizer.name}. {synthesizer.purpose}',
            f'Original user goal:\n{goal}',
            f'Planner summary:\n{plan_brief.summary}',
            'Required deliverable:\n' + (plan_brief.deliverable or 'A concise implementation-oriented final answer.'),
            'Questions answered by research:\n' + ('\n'.join(f'- {item}' for item in plan_brief.questions) if plan_brief.questions else 'No explicit questions.'),
            'Research todo status:\n' + final_research_plan.render_tree(),
            'Research observations:\n' + ('\n\n'.join(observations) if observations else 'No observations.'),
            'Researcher interim conclusion:\n' + research_response,
            'Task:\nProduce the final answer directly. Prefer concrete implementation steps and explicit file-level suggestions.',
        ])
        _emit(event_callback, 'Synthesizer: preparing the final response.')
        final_response = self.provider.complete(system_prompt=system_prompt, user_prompt=final_prompt).text.strip()
        if not final_response:
            final_response = research_response.strip()
        _emit(event_callback, 'Synthesizer: final response completed.')
        synthesizer_handoff = DeerflowHandoff(
            role=synthesizer.name,
            brief=final_response,
            evidence_targets=list(plan_brief.evidence_targets),
            observations=list(observations) + ['research_todo:\n' + final_research_plan.render_tree()],
        )
        handoffs.append(synthesizer_handoff)
        if run_recorder is not None:
            run_recorder.record_handoff(synthesizer_handoff.role, synthesizer_handoff.brief, synthesizer_handoff.evidence_targets)
        return DeerflowRunResult(final_response=final_response, observations=observations, handoffs=handoffs)

    def _build_research_plan(self, brief: DeerflowPlannerBrief) -> ExecutionPlan:
        tasks: list[ExecutionTask] = []
        if brief.evidence_targets:
            tasks.append(
                ExecutionTask(
                    id='check_evidence_targets',
                    title='Check planner evidence targets',
                    summary='Inspect the files or modules named by the planner.',
                    children=[
                        ExecutionTask(
                            id=f'evidence_{index}',
                            title=target,
                            summary='Verify the target directly in source or output.',
                            done_when='Relevant evidence from this target has been checked.',
                        )
                        for index, target in enumerate(brief.evidence_targets, start=1)
                    ],
                )
            )
        if brief.questions:
            tasks.append(
                ExecutionTask(
                    id='answer_planner_questions',
                    title='Answer planner verification questions',
                    summary='Collect enough evidence to answer the planner questions.',
                    children=[
                        ExecutionTask(
                            id=f'question_{index}',
                            title=question,
                            summary='Resolve this research question with evidence.',
                            done_when='The question is answered with direct evidence.',
                        )
                        for index, question in enumerate(brief.questions, start=1)
                    ],
                )
            )
        tasks.append(
            ExecutionTask(
                id='summarize_findings',
                title='Summarize research findings',
                summary='Condense the checked evidence into an interim conclusion for the synthesizer.',
                done_when='A concise evidence-backed research conclusion is available.',
            )
        )
        return ExecutionPlan(
            goal=brief.summary or 'Research the planner brief.',
            summary=brief.summary,
            deliverable=brief.deliverable or 'Research notes for the synthesizer.',
            tasks=tasks,
        )

    def _plan(self, system_prompt: str, planner: DeerflowRoleSpec, goal: str, bootstrap_observations: list[str]) -> DeerflowPlannerBrief:
        prompt = '\n\n'.join([
            f'You are the {planner.name}. {planner.purpose}',
            f'Goal:\n{goal}',
            'Current observations:\n' + ('\n\n'.join(bootstrap_observations) if bootstrap_observations else 'No observations.'),
            'Return JSON only with this schema:',
            '{"summary":"short brief","evidence_targets":["path or module"],"questions":["question to verify"],"deliverable":"final answer shape"}',
            'Rules:',
            '- Keep the summary short and concrete.',
            '- Evidence targets should prefer file paths or modules when possible.',
            '- Questions should be phrased as things the researcher must verify.',
        ])
        response = self.provider.complete(system_prompt=system_prompt, user_prompt=prompt).text.strip()
        return parse_planner_brief(response)

    def _role(self, name: str) -> DeerflowRoleSpec:
        for role in self.roles:
            if role.name == name:
                return role
        raise KeyError(name)


def parse_planner_brief(text: str) -> DeerflowPlannerBrief:
    data = _extract_json_object(text)
    if not data:
        return DeerflowPlannerBrief(
            summary=text.strip() or 'Inspect the most relevant source files and produce concrete implementation-oriented recommendations.',
            evidence_targets=[],
            questions=[],
            deliverable='A concise implementation-oriented final answer.',
        )

    evidence_targets = [str(item).strip() for item in data.get('evidence_targets', []) if str(item).strip()]
    questions = [str(item).strip() for item in data.get('questions', []) if str(item).strip()]
    return DeerflowPlannerBrief(
        summary=str(data.get('summary', '')).strip() or 'Inspect the most relevant source files and produce concrete recommendations.',
        evidence_targets=evidence_targets[:8],
        questions=questions[:8],
        deliverable=str(data.get('deliverable', '')).strip(),
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


def _prefixed_callback(event_callback: Callable[[str], None] | None, prefix: str) -> Callable[[str], None] | None:
    if event_callback is None:
        return None

    def _inner(message: str) -> None:
        _emit(event_callback, f'{prefix}: {message}')

    return _inner
