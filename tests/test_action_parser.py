from deermes.runtime.loop import parse_agent_action


def test_parse_agent_action_from_json_fence() -> None:
    action = parse_agent_action(
        '```json\n{"kind":"tool","tool_name":"read_file","tool_input":"README.md","reasoning":"need details"}\n```'
    )
    assert action.kind == 'tool'
    assert action.tool_name == 'read_file'
    assert action.tool_input == 'README.md'


def test_parse_agent_action_falls_back_to_final_text() -> None:
    action = parse_agent_action('plain answer')
    assert action.kind == 'final'
    assert action.response == 'plain answer'
