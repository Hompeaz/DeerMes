from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class DeerflowRoleSpec:
    name: str
    purpose: str
    tool_names: tuple[str, ...]


def default_deerflow_roles() -> tuple[DeerflowRoleSpec, ...]:
    return (
        DeerflowRoleSpec(
            name='planner',
            purpose='Break the user goal into a short execution brief, identify likely evidence targets, and define what the researcher should verify.',
            tool_names=(),
        ),
        DeerflowRoleSpec(
            name='researcher',
            purpose='Inspect the project, gather relevant evidence, and read files when observations are insufficient.',
            tool_names=('find_files', 'read_file'),
        ),
        DeerflowRoleSpec(
            name='synthesizer',
            purpose='Turn the gathered evidence into a concise, implementation-oriented final answer.',
            tool_names=(),
        ),
    )
