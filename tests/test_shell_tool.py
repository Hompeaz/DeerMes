from deermes.tools.shell import ShellTool


def test_shell_tool_blocks_disallowed_command(tmp_path) -> None:
    tool = ShellTool(str(tmp_path))
    result = tool.invoke('python3 -V')
    assert 'command not allowed' in result.output_text
