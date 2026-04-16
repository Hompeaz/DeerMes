from __future__ import annotations

import json
from pathlib import Path
from tempfile import NamedTemporaryFile

from deermes.security import ToolInvocation
from deermes.tools.base import ArtifactRecord, Tool, ToolResult


class ReadFileTool(Tool):
    name = 'read_file'
    description = 'Read a UTF-8 text file from disk.'

    def __init__(self, root: Path) -> None:
        self.root = root

    def describe_invocation(self, input_text: str) -> ToolInvocation:
        path = _resolve_input_path(self.root, input_text)
        return ToolInvocation(
            tool_name=self.name,
            action='read',
            summary=f'Read the file at {path}.',
            path=path,
            target_display=str(path),
        )

    def invoke(self, input_text: str) -> ToolResult:
        path = _resolve_input_path(self.root, input_text)
        if not path.exists():
            raise FileNotFoundError(f'No such file or directory: {path}')
        if not path.is_file():
            raise IsADirectoryError(f'Expected a file path but got: {path}')
        return ToolResult(
            tool_name=self.name,
            output_text=path.read_text(encoding='utf-8'),
            artifacts=(
                ArtifactRecord(
                    kind='file_read',
                    tool_name=self.name,
                    path=str(path),
                    summary=f'Read {path.name}',
                    verified=True,
                ),
            ),
        )


class WriteNoteTool(Tool):
    name = 'write_note'
    description = 'Write a UTF-8 note into the project .deermes directory.'

    def __init__(self, root: Path) -> None:
        self.root = root / '.deermes' / 'notes'
        self.root.mkdir(parents=True, exist_ok=True)

    def describe_invocation(self, input_text: str) -> ToolInvocation:
        filename, _, _content = input_text.partition('\n')
        target = (self.root / filename.strip()).resolve()
        return ToolInvocation(
            tool_name=self.name,
            action='write',
            summary=f'Write a note to {target}.',
            path=target,
            target_display=str(target),
        )

    def invoke(self, input_text: str) -> ToolResult:
        filename, _, content = input_text.partition('\n')
        target = (self.root / filename.strip()).resolve()
        target.write_text(content.lstrip('\n'), encoding='utf-8')
        return ToolResult(
            tool_name=self.name,
            output_text=str(target),
            artifacts=(
                ArtifactRecord(
                    kind='file_write',
                    tool_name=self.name,
                    path=str(target),
                    summary='Wrote note file',
                    verified=target.exists(),
                ),
            ),
        )


class CreateFileTool(Tool):
    name = 'create_file'
    description = 'Create a new UTF-8 text file. Tool input: JSON {"path": "...", "content": "..."}.'

    def __init__(self, root: Path) -> None:
        self.root = root

    def describe_invocation(self, input_text: str) -> ToolInvocation:
        payload = _parse_path_content_payload(input_text)
        target = _resolve_input_path(self.root, payload['path'])
        return ToolInvocation(
            tool_name=self.name,
            action='write',
            summary=f'Create a new file at {target}.',
            path=target,
            target_display=str(target),
        )

    def invoke(self, input_text: str) -> ToolResult:
        payload = _parse_path_content_payload(input_text)
        target = _resolve_input_path(self.root, payload['path'])
        if target.exists():
            raise FileExistsError(f'File already exists: {target}')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload['content'], encoding='utf-8')
        return ToolResult(
            tool_name=self.name,
            output_text=str(target),
            artifacts=(
                ArtifactRecord(
                    kind='file_write',
                    tool_name=self.name,
                    path=str(target),
                    summary='Created file',
                    verified=target.exists(),
                ),
            ),
        )


class WriteFileAtomicTool(Tool):
    name = 'write_file_atomic'
    description = 'Write a UTF-8 text file atomically. Tool input: JSON {"path": "...", "content": "..."}.'

    def __init__(self, root: Path) -> None:
        self.root = root

    def describe_invocation(self, input_text: str) -> ToolInvocation:
        payload = _parse_path_content_payload(input_text)
        target = _resolve_input_path(self.root, payload['path'])
        return ToolInvocation(
            tool_name=self.name,
            action='write',
            summary=f'Atomically write the file at {target}.',
            path=target,
            target_display=str(target),
        )

    def invoke(self, input_text: str) -> ToolResult:
        payload = _parse_path_content_payload(input_text)
        target = _resolve_input_path(self.root, payload['path'])
        target.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile('w', encoding='utf-8', dir=str(target.parent), delete=False) as handle:
            handle.write(payload['content'])
            temp_path = Path(handle.name)
        temp_path.replace(target)
        return ToolResult(
            tool_name=self.name,
            output_text=str(target),
            artifacts=(
                ArtifactRecord(
                    kind='file_write',
                    tool_name=self.name,
                    path=str(target),
                    summary='Atomically wrote file',
                    verified=target.exists(),
                ),
            ),
        )


class PatchFileTool(Tool):
    name = 'patch_file'
    description = 'Patch an existing UTF-8 text file. Tool input: JSON {"path": "...", "search": "...", "replace": "..."}.'

    def __init__(self, root: Path) -> None:
        self.root = root

    def describe_invocation(self, input_text: str) -> ToolInvocation:
        payload = _parse_patch_payload(input_text)
        target = _resolve_input_path(self.root, payload['path'])
        return ToolInvocation(
            tool_name=self.name,
            action='write',
            summary=f'Patch the file at {target}.',
            path=target,
            target_display=str(target),
        )

    def invoke(self, input_text: str) -> ToolResult:
        payload = _parse_patch_payload(input_text)
        target = _resolve_input_path(self.root, payload['path'])
        if not target.exists():
            raise FileNotFoundError(f'No such file or directory: {target}')
        if not target.is_file():
            raise IsADirectoryError(f'Expected a file path but got: {target}')
        original = target.read_text(encoding='utf-8')
        search = payload['search']
        replace = payload['replace']
        if search not in original:
            raise ValueError(f'Search block not found in {target}')
        updated = original.replace(search, replace, 1)
        target.write_text(updated, encoding='utf-8')
        return ToolResult(
            tool_name=self.name,
            output_text=str(target),
            artifacts=(
                ArtifactRecord(
                    kind='file_write',
                    tool_name=self.name,
                    path=str(target),
                    summary='Patched file',
                    verified=target.exists(),
                ),
            ),
        )


class FindFilesTool(Tool):
    name = 'find_files'
    description = 'List files under a directory without shell features.'

    def __init__(self, root: Path, limit: int = 200) -> None:
        self.root = root
        self.limit = limit
        self.skip_parts = {'.git', '.venv', '__pycache__'}

    def describe_invocation(self, input_text: str) -> ToolInvocation:
        base = _resolve_input_path(self.root, input_text or '.')
        return ToolInvocation(
            tool_name=self.name,
            action='read',
            summary=f'List files under {base}.',
            path=base,
            target_display=str(base),
        )

    def invoke(self, input_text: str) -> ToolResult:
        base = _resolve_input_path(self.root, input_text or '.')
        if not base.exists():
            raise FileNotFoundError(f'No such file or directory: {base}')
        if base.is_file():
            return ToolResult(
                tool_name=self.name,
                output_text=_display_path(self.root, base),
                artifacts=(
                    ArtifactRecord(
                        kind='file_list',
                        tool_name=self.name,
                        path=str(base),
                        summary='Listed one file target',
                        verified=True,
                    ),
                ),
            )

        items: list[str] = []
        for path in sorted(base.rglob('*')):
            if any(part in self.skip_parts for part in path.parts):
                continue
            if path.is_file():
                items.append(_display_path(self.root, path))
            if len(items) >= self.limit:
                break
        return ToolResult(
            tool_name=self.name,
            output_text='\n'.join(items),
            artifacts=(
                ArtifactRecord(
                    kind='file_list',
                    tool_name=self.name,
                    path=str(base),
                    summary=f'Listed up to {self.limit} files',
                    verified=True,
                ),
            ),
        )


def _parse_path_content_payload(input_text: str) -> dict[str, str]:
    payload = _parse_json_payload(input_text)
    path = str(payload.get('path', '')).strip()
    if not path:
        raise ValueError('Expected `path` in tool input payload.')
    if 'content' not in payload:
        raise ValueError('Expected `content` in tool input payload.')
    return {'path': path, 'content': str(payload.get('content', ''))}


def _parse_patch_payload(input_text: str) -> dict[str, str]:
    payload = _parse_json_payload(input_text)
    path = str(payload.get('path', '')).strip()
    search = str(payload.get('search', ''))
    replace = str(payload.get('replace', ''))
    if not path:
        raise ValueError('Expected `path` in patch payload.')
    if not search:
        raise ValueError('Expected non-empty `search` in patch payload.')
    return {'path': path, 'search': search, 'replace': replace}


def _parse_json_payload(input_text: str) -> dict[str, object]:
    stripped = input_text.strip()
    if not stripped:
        raise ValueError('Expected JSON tool input but received empty input.')
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValueError(f'Expected JSON tool input: {exc}') from exc
    if not isinstance(payload, dict):
        raise ValueError('Expected JSON object tool input.')
    return payload


def _resolve_input_path(root: Path, raw_value: str) -> Path:
    value = raw_value.strip() or '.'
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return (root / candidate).resolve()


def _display_path(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)
