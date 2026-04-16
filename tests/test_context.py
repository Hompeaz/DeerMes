from pathlib import Path

from deermes.learning.context import ContextLoader


def test_context_loader_reads_supported_files(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("agent instructions", encoding="utf-8")
    (tmp_path / "SOUL.md").write_text("agent persona", encoding="utf-8")
    bundle = ContextLoader(tmp_path, ("AGENTS.md", "SOUL.md")).load()
    assert len(bundle.files) == 2
    assert "agent instructions" in bundle.render()
