from deermes.execution.deerflow.supervisor import DeerflowSupervisor, parse_planner_brief
from deermes.tools.base import ToolRegistry


class DummyProvider:
    def complete(self, system_prompt: str, user_prompt: str):
        class R:
            text = '{"summary":"inspect runtime and docs","evidence_targets":["src/deermes/runtime/app.py","docs/ARCHITECTURE.md"],"questions":["How is runtime assembled?"],"deliverable":"three engineering steps"}'
        return R()


def test_parse_planner_brief_from_json() -> None:
    brief = parse_planner_brief('{"summary":"inspect runtime","evidence_targets":["src/deermes/runtime/app.py"],"questions":["how does it work"],"deliverable":"steps"}')
    assert brief.summary == 'inspect runtime'
    assert brief.evidence_targets == ['src/deermes/runtime/app.py']
    assert brief.questions == ['how does it work']
    assert brief.deliverable == 'steps'


def test_supervisor_plan_uses_structured_brief() -> None:
    supervisor = DeerflowSupervisor(provider=DummyProvider(), tools=ToolRegistry())
    brief = supervisor._plan('system', supervisor._role('planner'), 'goal', [])
    assert brief.summary == 'inspect runtime and docs'
    assert brief.evidence_targets == ['src/deermes/runtime/app.py', 'docs/ARCHITECTURE.md']
