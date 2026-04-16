from __future__ import annotations

from dataclasses import dataclass

from deermes.tools.base import ArtifactRecord


@dataclass(slots=True)
class Reporter:
    def build(
        self,
        goal: str,
        tool_outputs: list[str],
        provider_text: str,
        plan_text: str = '',
        artifacts: tuple[ArtifactRecord, ...] = (),
        run_id: str = '',
    ) -> str:
        sections = [f'Goal: {goal}']
        if run_id.strip():
            sections.append(f'Run ID: {run_id.strip()}')
        if plan_text.strip():
            sections.append('Execution Todo:\n' + plan_text.strip())
        if artifacts:
            sections.append('Artifacts:\n' + '\n'.join(f'- {item.as_text()}' for item in artifacts))
        if tool_outputs:
            sections.append('Tool Observations:\n' + '\n'.join(f'- {item}' for item in tool_outputs))
        sections.append('Draft Response:\n' + provider_text.strip())
        return '\n\n'.join(sections).strip()
