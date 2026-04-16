from pathlib import Path

from deermes.config import ToolSpec
from deermes.tools.factory import ToolFactory


def test_tool_factory_builds_registry_with_enabled_tools(tmp_path: Path) -> None:
    specs = (
        ToolSpec(name='find_files'),
        ToolSpec(name='shell', enabled=False),
        ToolSpec(name='read_file'),
    )
    registry = ToolFactory(tmp_path).build_registry(specs)
    assert registry.has('find_files')
    assert registry.has('read_file')
    assert not registry.has('shell')
