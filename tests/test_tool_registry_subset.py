from deermes.tools.base import ToolRegistry
from deermes.tools.filesystem import FindFilesTool, ReadFileTool


def test_tool_registry_subset(tmp_path) -> None:
    registry = ToolRegistry()
    registry.register(FindFilesTool(tmp_path))
    registry.register(ReadFileTool(tmp_path))

    subset = registry.subset(('read_file',))

    assert subset.has('read_file')
    assert not subset.has('find_files')
