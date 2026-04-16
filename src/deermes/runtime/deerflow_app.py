from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from deermes.config import AgentSettings
from deermes.execution import Reporter
from deermes.execution.deerflow.supervisor import DeerflowSupervisor
from deermes.learning import ContextLoader, MemoryStore, ProfileLoader, ReflectionEngine
from deermes.providers import build_provider
from deermes.runtime.runlog import RunRecorder, RunSummary, ground_final_response
from deermes.security import ApprovalRequest, PermissionManager
from deermes.tools import ToolFactory, ToolRegistry


@dataclass(slots=True)
class DeerflowRuntime:
    settings: AgentSettings
    reporter: Reporter
    context_loader: ContextLoader
    profile_loader: ProfileLoader
    memory_store: MemoryStore
    reflection_engine: ReflectionEngine
    provider: object
    tools: ToolRegistry
    permission_manager: PermissionManager
    last_run_summary: RunSummary | None = field(default=None)

    def run(
        self,
        goal: str,
        session_context: str = '',
        event_callback: Callable[[str], None] | None = None,
    ) -> str:
        task_goal = self._compose_task_goal(goal, session_context)
        run_recorder = RunRecorder(
            project_root=self.settings.project_root,
            mode='deerflow',
            provider_name=self.settings.provider_name,
            model_name=self.settings.model_name,
            goal=goal,
        )
        _emit(event_callback, 'Loading context, profile, relevant memory, and permission policy.')
        context = self.context_loader.load()
        profiles = self.profile_loader.load()
        relevant_memories = self.memory_store.search(goal, limit=5)
        run_recorder.record_learning_inputs(
            profile_sources=[item.source for item in profiles],
            memory_count=len(relevant_memories),
            context_loaded=bool(context.render().strip()),
        )

        system_prompt = self._build_system_prompt(context.render(), profiles, relevant_memories)
        supervisor = DeerflowSupervisor(
            provider=self.provider,
            tools=self.tools,
            safety_action_cap=self.settings.execution_safety_action_cap,
            stall_limit=self.settings.execution_stall_limit,
        )
        result = supervisor.run(
            system_prompt=system_prompt,
            goal=task_goal,
            bootstrap_observations=[],
            event_callback=event_callback,
            run_recorder=run_recorder,
        )

        handoff_summary = '\n'.join(f'- {item.role}: {item.brief[:160]}' for item in result.handoffs)
        observations = list(result.observations)
        if handoff_summary:
            observations.append('handoffs:\n' + handoff_summary)
            run_recorder.record_event(
                'plan_finalized',
                {
                    'plan_text': handoff_summary,
                    'summary': handoff_summary,
                    'deliverable': '',
                },
            )
            run_recorder.plan_text = handoff_summary

        run_summary = run_recorder.summary()
        grounded_response, grounded = ground_final_response(result.final_response, run_summary.artifacts)
        run_recorder.record_final_response(result.final_response, grounded_response, grounded)
        run_summary = run_recorder.summary()
        final_output = self.reporter.build(
            goal,
            observations,
            grounded_response,
            plan_text=handoff_summary,
            artifacts=run_summary.artifacts,
            run_id=run_summary.run_id,
        )

        if self.settings.reflection_enabled:
            _emit(event_callback, 'Writing reflection memory for this run.')
            entries = list(self.reflection_engine.reflect(goal, final_output, observations))
            for entry in entries:
                self.memory_store.append(entry)
            run_recorder.record_reflection(len(entries))

        run_recorder.record_run_finished()
        self.last_run_summary = run_recorder.summary()
        _emit(event_callback, 'Run finished.')
        return final_output

    def _compose_task_goal(self, goal: str, session_context: str) -> str:
        if not session_context.strip():
            return goal.strip()
        return '\n\n'.join([
            'Latest user message:',
            goal.strip(),
            session_context.strip(),
            'Task:',
            'Answer the latest user message while preserving relevant context from the ongoing chat session.',
        ])

    def _build_system_prompt(self, context_text: str, profiles: list, relevant_memories: list) -> str:
        memory_text = '\n'.join(f'- [{entry.kind}] {entry.summary}' for entry in relevant_memories)
        profile_text = '\n\n'.join(f'# {item.source}\n{item.content.strip()}' for item in profiles)
        parts = [
            'You are DeerMes running in DeerFlow-inspired execution mode.',
            'Use role separation: planner, researcher, synthesizer.',
            'Favor clear task handoff, evidence-backed conclusions, and explicit task completion.',
            'Do not lose the persistent-memory behavior of DeerMes.',
            'Only claim files were created or modified when the runtime has verified write artifacts.',
            'Respect the active permission policy. If a requested tool action is likely to require approval, justify it clearly and avoid unnecessary escalation.',
            'Permission policy:\n' + self.permission_manager.describe_for_prompt(),
        ]
        if profile_text:
            parts.append('Loaded profile:\n' + profile_text[:3000])
        if context_text:
            parts.append('Loaded context:\n' + context_text[:4000])
        if memory_text:
            parts.append('Relevant memories:\n' + memory_text)
        return '\n\n'.join(parts)


def build_deerflow_runtime(
    project_root: Path,
    provider_name: str = 'echo',
    model_name: str = 'deermes-dev',
    base_url: str | None = None,
    permission_profile: str | None = None,
    approval_callback: Callable[[ApprovalRequest], bool] | None = None,
    api_key_env: str | None = None,
    request_timeout_sec: int | None = None,
    session_context_char_limit: int | None = None,
) -> DeerflowRuntime:
    settings = AgentSettings.for_project(project_root)
    settings.provider_name = provider_name
    if request_timeout_sec is not None:
        settings.provider_timeout_sec = max(30, int(request_timeout_sec))
    if session_context_char_limit is not None:
        settings.session_context_char_limit = max(500, int(session_context_char_limit))

    permission_manager = PermissionManager.load(project_root, requested_profile=permission_profile)
    tools = ToolFactory(
        project_root,
        permission_manager=permission_manager,
        approval_callback=approval_callback,
    ).build_registry(settings.tool_specs)

    provider = build_provider(
        provider_name=provider_name,
        model_name=model_name,
        base_url=base_url,
        timeout_sec=settings.provider_timeout_sec,
        api_key_env=api_key_env,
    )
    settings.model_name = getattr(provider, 'model_name', model_name)

    return DeerflowRuntime(
        settings=settings,
        reporter=Reporter(),
        context_loader=ContextLoader(project_root, settings.context_filenames),
        profile_loader=ProfileLoader(project_root),
        memory_store=MemoryStore(settings.memory_path),
        reflection_engine=ReflectionEngine(),
        provider=provider,
        tools=tools,
        permission_manager=permission_manager,
    )


def _emit(event_callback: Callable[[str], None] | None, message: str) -> None:
    if event_callback is None:
        return
    text = message.strip()
    if text:
        event_callback(text)
