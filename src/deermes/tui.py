from __future__ import annotations

import curses
import locale
import queue
import re
import shlex
import threading
import traceback
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from deermes.chat import ChatMessage, ChatSessionStore, build_session_context, extract_assistant_text
from deermes.providers import (
    ModelDescriptor,
    SUPPORTED_PROVIDER_NAMES,
    build_provider,
    default_api_key_env_for_provider,
    default_base_url_for_provider,
)
from deermes.runtime import build_deerflow_runtime, build_runtime
from deermes.security import ApprovalRequest, PermissionManager


SPINNER_FRAMES = ('|', '/', '-', '\\')
MIN_WIDTH = 48
MIN_HEIGHT = 14
SIDEBAR_MIN_WIDTH = 30
SIDEBAR_TRIGGER_WIDTH = 110
INPUT_PROMPT = '> '
INPUT_CONTINUATION = '  '


@dataclass(slots=True)
class ChatUIConfig:
    project_root: Path
    mode: str
    provider_name: str
    model_name: str
    base_url: str | None
    api_key_env: str | None = None
    session_name: str = 'default'
    history_limit: int = 8
    permission_profile: str | None = None
    request_timeout_sec: int | None = None
    session_context_char_limit: int | None = None


@dataclass(slots=True)
class PendingApproval:
    request: ApprovalRequest
    response_queue: queue.Queue


@dataclass(slots=True)
class ComposerLine:
    start: int
    end: int
    prefix: str
    prefix_width: int
    text: str

    @property
    def display(self) -> str:
        return f'{self.prefix}{self.text}'


@dataclass(slots=True)
class ComposerLayout:
    lines: list[ComposerLine]
    cursor_line: int
    cursor_col: int


@dataclass(slots=True)
class StyledSegment:
    text: str
    attr: int


@dataclass(slots=True)
class StyledRow:
    segments: list[StyledSegment]


class DeerMesChatUI:
    def __init__(self, config: ChatUIConfig) -> None:
        self.config = config
        self.session_store = ChatSessionStore(config.project_root, config.session_name)
        self.log_dir = config.project_root / '.deermes' / 'logs'
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.messages = self.session_store.load()
        self.input_buffer = ''
        self.cursor_index = 0
        self.transcript_scroll = 0
        self.status = 'Ready. /help shows commands.'
        self.result_queue: queue.Queue = queue.Queue()
        self.busy = False
        self.worker: threading.Thread | None = None
        self.pending_approval: PendingApproval | None = None
        self._status_tick = 0
        self._colors_ready = False
        self.permission_manager = PermissionManager.load(config.project_root, requested_profile=config.permission_profile)
        self.runtime = self._build_runtime()
        if not self.messages:
            self._append_ephemeral('system', 'DeerMes TUI ready. Enter a message to start, or /help for commands.')

    def run(self, stdscr) -> None:
        curses.noecho()
        curses.cbreak()
        stdscr.keypad(True)
        stdscr.timeout(120)
        try:
            curses.mousemask(curses.ALL_MOUSE_EVENTS)
            curses.mouseinterval(0)
        except curses.error:
            pass
        self._setup_colors()
        try:
            curses.curs_set(1)
        except curses.error:
            pass

        while True:
            self._drain_results()
            self._draw(stdscr)
            try:
                key = stdscr.get_wch()
            except curses.error:
                continue

            if key == curses.KEY_RESIZE:
                continue
            if key == curses.KEY_MOUSE:
                self._handle_mouse()
                continue
            if key in (curses.KEY_PPAGE, curses.KEY_NPAGE):
                self._scroll_transcript(key)
                continue
            if key in (curses.KEY_LEFT, curses.KEY_RIGHT, curses.KEY_UP, curses.KEY_DOWN):
                self._move_cursor(key, stdscr)
                continue
            if key in (curses.KEY_HOME, curses.KEY_END):
                self._move_cursor_to_edge(key, stdscr)
                continue
            if key in (curses.KEY_BACKSPACE, 127, 8):
                self._delete_backward()
                continue
            if key == curses.KEY_DC:
                self._delete_forward()
                continue
            if key in (curses.KEY_ENTER, 10, 13):
                if self._submit_buffer() == 'quit':
                    return
                continue

            if isinstance(key, str):
                if key in ('\x03', '\x04'):
                    return
                if key in ('\n', '\r'):
                    if self._submit_buffer() == 'quit':
                        return
                    continue
                if key in ('\x7f', '\b', '\x08'):
                    self._delete_backward()
                    continue
                if key == '\x01':
                    self.cursor_index = 0
                    continue
                if key == '\x05':
                    self.cursor_index = len(self.input_buffer)
                    continue
                if key == '\x0b':
                    self.input_buffer = self.input_buffer[:self.cursor_index]
                    continue
                if key == '\x15':
                    self.input_buffer = ''
                    self.cursor_index = 0
                    continue
                if key == '\x17':
                    self._delete_previous_word()
                    continue
                if key.isprintable() or key == '\t' or key == ' ':
                    self._insert_text(key)
                continue

    def _setup_colors(self) -> None:
        if self._colors_ready or not curses.has_colors():
            self._colors_ready = True
            return
        curses.start_color()
        try:
            curses.use_default_colors()
        except curses.error:
            pass
        curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_CYAN)
        curses.init_pair(2, curses.COLOR_CYAN, -1)
        curses.init_pair(3, curses.COLOR_GREEN, -1)
        curses.init_pair(4, curses.COLOR_WHITE, -1)
        curses.init_pair(5, curses.COLOR_YELLOW, -1)
        curses.init_pair(6, curses.COLOR_RED, -1)
        curses.init_pair(7, curses.COLOR_MAGENTA, -1)
        if getattr(curses, 'COLORS', 0) >= 256:
            curses.init_pair(8, 244, -1)
            curses.init_pair(9, 75, -1)
            curses.init_pair(10, 120, -1)
            curses.init_pair(11, 214, -1)
        else:
            curses.init_pair(8, curses.COLOR_WHITE, -1)
            curses.init_pair(9, curses.COLOR_BLUE, -1)
            curses.init_pair(10, curses.COLOR_GREEN, -1)
            curses.init_pair(11, curses.COLOR_YELLOW, -1)
        self._colors_ready = True

    def _attr(self, name: str) -> int:
        italic = getattr(curses, 'A_ITALIC', 0)
        try:
            has_colors = curses.has_colors()
        except curses.error:
            has_colors = False

        if not has_colors:
            mapping = {
                'title': curses.A_REVERSE,
                'meta': curses.A_BOLD,
                'user': curses.A_BOLD,
                'assistant': curses.A_NORMAL,
                'trace': curses.A_DIM | italic,
                'error': curses.A_BOLD,
                'approval': curses.A_BOLD,
                'section': curses.A_BOLD,
                'status': curses.A_NORMAL,
                'heading': curses.A_BOLD,
                'md_bold': curses.A_BOLD,
                'inline_code': curses.A_DIM,
                'code_text': curses.A_NORMAL,
                'code_command': curses.A_BOLD,
                'code_flag': curses.A_BOLD,
                'code_string': curses.A_DIM,
                'code_comment': curses.A_DIM | italic,
                'code_number': curses.A_BOLD,
                'code_operator': curses.A_BOLD,
                'code_variable': curses.A_BOLD,
                'code_fence': curses.A_DIM,
            }
            return mapping.get(name, 0)
        mapping = {
            'title': curses.color_pair(1) | curses.A_BOLD,
            'meta': curses.color_pair(2) | curses.A_BOLD,
            'user': curses.color_pair(3) | curses.A_BOLD,
            'assistant': curses.color_pair(4),
            'trace': curses.color_pair(8) | curses.A_DIM | italic,
            'error': curses.color_pair(6) | curses.A_BOLD,
            'approval': curses.color_pair(7) | curses.A_BOLD,
            'section': curses.color_pair(9) | curses.A_BOLD,
            'status': curses.color_pair(2),
            'heading': curses.color_pair(9) | curses.A_BOLD,
            'md_bold': curses.color_pair(10) | curses.A_BOLD,
            'inline_code': curses.color_pair(11) | curses.A_BOLD,
            'code_text': curses.color_pair(4),
            'code_command': curses.color_pair(9) | curses.A_BOLD,
            'code_flag': curses.color_pair(11) | curses.A_BOLD,
            'code_string': curses.color_pair(10),
            'code_comment': curses.color_pair(8) | curses.A_DIM | italic,
            'code_number': curses.color_pair(7),
            'code_operator': curses.color_pair(6) | curses.A_BOLD,
            'code_variable': curses.color_pair(2),
            'code_fence': curses.color_pair(8) | curses.A_DIM,
        }
        return mapping.get(name, 0)

    def _build_runtime(self):
        builder = build_runtime if self.config.mode == 'single-agent' else build_deerflow_runtime
        runtime = builder(
            self.config.project_root,
            provider_name=self.config.provider_name,
            model_name=self.config.model_name,
            base_url=self.config.base_url,
            permission_profile=self.config.permission_profile,
            api_key_env=self.config.api_key_env,
            approval_callback=self._request_approval,
            request_timeout_sec=self.config.request_timeout_sec,
            session_context_char_limit=self.config.session_context_char_limit,
        )
        self.permission_manager = runtime.permission_manager
        self.config.provider_name = runtime.settings.provider_name
        self.config.model_name = runtime.settings.model_name
        self.config.permission_profile = runtime.permission_manager.active_profile_name
        self.config.request_timeout_sec = runtime.settings.provider_timeout_sec
        self.config.session_context_char_limit = runtime.settings.session_context_char_limit
        return runtime

    def _insert_text(self, text: str) -> None:
        self.input_buffer = self.input_buffer[:self.cursor_index] + text + self.input_buffer[self.cursor_index:]
        self.cursor_index += len(text)

    def _delete_backward(self) -> None:
        if self.cursor_index <= 0:
            return
        self.input_buffer = self.input_buffer[: self.cursor_index - 1] + self.input_buffer[self.cursor_index:]
        self.cursor_index -= 1

    def _delete_forward(self) -> None:
        if self.cursor_index >= len(self.input_buffer):
            return
        self.input_buffer = self.input_buffer[:self.cursor_index] + self.input_buffer[self.cursor_index + 1:]

    def _delete_previous_word(self) -> None:
        if self.cursor_index <= 0:
            return
        head = self.input_buffer[:self.cursor_index]
        trimmed = head.rstrip()
        if not trimmed:
            self.input_buffer = self.input_buffer[self.cursor_index:]
            self.cursor_index = 0
            return
        cut = len(trimmed)
        while cut > 0 and not trimmed[cut - 1].isspace():
            cut -= 1
        self.input_buffer = self.input_buffer[:cut] + self.input_buffer[self.cursor_index:]
        self.cursor_index = cut

    def _move_cursor(self, key: int, stdscr) -> None:
        if key == curses.KEY_LEFT:
            if self.cursor_index > 0:
                self.cursor_index -= 1
            return
        if key == curses.KEY_RIGHT:
            if self.cursor_index < len(self.input_buffer):
                self.cursor_index += 1
            return

        width = self._composer_content_width(stdscr.getmaxyx()[1])
        layout = _layout_composer_text(self.input_buffer, self.cursor_index, width)
        target_line = layout.cursor_line - 1 if key == curses.KEY_UP else layout.cursor_line + 1
        if target_line < 0 or target_line >= len(layout.lines):
            return
        self.cursor_index = _index_for_column(self.input_buffer, layout.lines[target_line], layout.cursor_col)

    def _move_cursor_to_edge(self, key: int, stdscr) -> None:
        width = self._composer_content_width(stdscr.getmaxyx()[1])
        layout = _layout_composer_text(self.input_buffer, self.cursor_index, width)
        line = layout.lines[layout.cursor_line]
        self.cursor_index = line.start if key == curses.KEY_HOME else line.end

    def _scroll_transcript(self, key: int) -> None:
        delta = 8 if key == curses.KEY_PPAGE else -8
        self._scroll_transcript_steps(delta)

    def _scroll_transcript_steps(self, delta: int) -> None:
        self.transcript_scroll = max(0, self.transcript_scroll + delta)

    def _handle_mouse(self) -> None:
        try:
            _device_id, _x, _y, _z, state = curses.getmouse()
        except curses.error:
            return

        wheel_up_mask = (
            getattr(curses, 'BUTTON4_PRESSED', 0)
            | getattr(curses, 'BUTTON4_RELEASED', 0)
            | getattr(curses, 'BUTTON4_CLICKED', 0)
            | getattr(curses, 'BUTTON4_DOUBLE_CLICKED', 0)
            | getattr(curses, 'BUTTON4_TRIPLE_CLICKED', 0)
        )
        wheel_down_mask = (
            getattr(curses, 'BUTTON5_PRESSED', 0)
            | getattr(curses, 'BUTTON5_RELEASED', 0)
            | getattr(curses, 'BUTTON5_CLICKED', 0)
            | getattr(curses, 'BUTTON5_DOUBLE_CLICKED', 0)
            | getattr(curses, 'BUTTON5_TRIPLE_CLICKED', 0)
        )
        if state & wheel_up_mask:
            self._scroll_transcript_steps(3)
        elif state & wheel_down_mask:
            self._scroll_transcript_steps(-3)

    def _submit_buffer(self) -> str | None:
        text = self.input_buffer.strip()
        if not text:
            return None

        self.input_buffer = ''
        self.cursor_index = 0
        self.transcript_scroll = 0

        if self.pending_approval is not None:
            return self._handle_approval_response(text)

        if self.busy:
            self.status = 'A run is still active. Wait for it to finish before sending the next message.'
            return None

        if text.startswith('/'):
            return self._handle_command(text)

        session_history = [item for item in self.messages if item.role in {'user', 'assistant'}]
        session_context = build_session_context(session_history, history_limit=self.config.history_limit, char_limit=self.config.session_context_char_limit or 3500)
        self._append_persistent('user', text)
        self.busy = True
        self.status = (
            f'Running {self.config.mode} with {self.config.provider_name}/{self.config.model_name} '
            f'under profile {self.config.permission_profile}...'
        )

        def worker() -> None:
            try:
                raw_output = self.runtime.run(text, session_context=session_context, event_callback=self._emit_event)
                assistant_text = extract_assistant_text(raw_output) or raw_output.strip() or '(empty response)'
                run_summary = getattr(self.runtime, 'last_run_summary', None)
                self.result_queue.put(('result', assistant_text, raw_output, run_summary))
            except Exception as exc:
                self.result_queue.put(('error', str(exc), traceback.format_exc()))

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()
        return None

    def _handle_command(self, command_text: str) -> str | None:
        try:
            parts = shlex.split(command_text)
        except ValueError as exc:
            self.status = f'Command parse error: {exc}'
            return None

        command = parts[0].lower()
        args = parts[1:]

        if command in {'/quit', '/q', '/exit'}:
            return 'quit'
        if command == '/help':
            self._append_ephemeral('system', '\n'.join([
                'Commands:',
                '/help',
                '/quit',
                '/mode single-agent|deerflow',
                '/provider PROVIDER_NAME',
                '/models [PROVIDER_NAME]',
                '/model MODEL_NAME',
                '/model PROVIDER MODEL_NAME',
                '/base-url URL',
                '/profile PROFILE_NAME',
                '/permissions',
                '/session SESSION_NAME',
                '/history N',
                '/timeout SEC',
                '/context-limit N',
                '/raw',
                '/run',
                '/artifacts',
                '/approve',
                '/deny',
                'Editor: arrows move cursor, Home/End move on wrapped line, PgUp/PgDn or mouse wheel scroll transcript.',
            ]))
            self.status = 'Displayed help.'
            return None
        if command == '/mode' and len(args) == 1:
            if args[0] not in {'single-agent', 'deerflow'}:
                self.status = 'Unsupported mode.'
                return None
            self.config.mode = args[0]
            self.runtime = self._build_runtime()
            self.status = f'Mode switched to {self.config.mode}.'
            return None
        if command == '/provider' and len(args) == 1:
            if args[0] not in set(SUPPORTED_PROVIDER_NAMES):
                self.status = 'Unsupported provider.'
                return None
            previous_provider = self.config.provider_name
            previous_model = self.config.model_name
            previous_base_url = self.config.base_url
            previous_api_key_env = self.config.api_key_env
            self._apply_provider_defaults(args[0])
            try:
                self.runtime = self._build_runtime()
            except Exception as exc:
                self.config.provider_name = previous_provider
                self.config.model_name = previous_model
                self.config.base_url = previous_base_url
                self.config.api_key_env = previous_api_key_env
                self.status = f'Provider switch failed: {exc}'
                return None
            self.status = f'Provider switched to {self.config.provider_name}.'
            return None
        if command == '/models':
            provider_name = args[0] if args else self.config.provider_name
            if provider_name not in set(SUPPORTED_PROVIDER_NAMES):
                self.status = 'Unsupported provider.'
                return None
            try:
                provider = self._catalog_provider(provider_name)
                models = provider.list_models()
            except Exception as exc:
                self.status = f'Failed to load models for {provider_name}: {exc}'
                return None
            self._append_ephemeral('system', self._render_model_catalog(provider_name, models))
            self.status = f'Displayed models for {provider_name}.'
            return None
        if command == '/model':
            if not args:
                try:
                    provider = self._catalog_provider(self.config.provider_name)
                    models = provider.list_models()
                except Exception as exc:
                    self.status = f'Failed to load models for {self.config.provider_name}: {exc}'
                    return None
                self._append_ephemeral('system', self._render_model_catalog(self.config.provider_name, models))
                self.status = f'Current model: {self.config.provider_name}/{self.config.model_name}'
                return None

            provider_name = self.config.provider_name
            model_name = ' '.join(args)
            if len(args) >= 2 and args[0] in set(SUPPORTED_PROVIDER_NAMES):
                provider_name = args[0]
                model_name = ' '.join(args[1:])
            previous_provider = self.config.provider_name
            previous_model = self.config.model_name
            previous_base_url = self.config.base_url
            previous_api_key_env = self.config.api_key_env
            if provider_name != self.config.provider_name:
                self._apply_provider_defaults(provider_name, reset_model=False)
            self.config.provider_name = provider_name
            self.config.model_name = model_name
            try:
                self.runtime = self._build_runtime()
            except Exception as exc:
                self.config.provider_name = previous_provider
                self.config.model_name = previous_model
                self.config.base_url = previous_base_url
                self.config.api_key_env = previous_api_key_env
                self.status = f'Model switch failed: {exc}'
                return None
            self._append_ephemeral('system', f'Active model set to {self.config.provider_name}/{self.config.model_name}.')
            self.status = f'Model switched to {self.config.provider_name}/{self.config.model_name}.'
            return None
        if command == '/base-url' and args:
            previous_base_url = self.config.base_url
            self.config.base_url = args[0]
            try:
                self.runtime = self._build_runtime()
            except Exception as exc:
                self.config.base_url = previous_base_url
                self.status = f'Base URL update failed: {exc}'
                return None
            self.status = f'Base URL set to {self.config.base_url}.'
            return None
        if command == '/profile':
            if len(args) != 1:
                self.status = f'Current permission profile: {self.config.permission_profile}'
                return None
            self.config.permission_profile = args[0]
            try:
                self.runtime = self._build_runtime()
            except ValueError as exc:
                self.status = str(exc)
                self.config.permission_profile = self.permission_manager.active_profile_name
                return None
            self.status = f'Permission profile switched to {self.config.permission_profile}.'
            return None
        if command == '/permissions':
            self._append_ephemeral('system', '\n'.join([
                f'Permission config: {self.permission_manager.config_path}',
                *self.permission_manager.profile_summaries(),
            ]))
            self.status = 'Displayed permission profiles.'
            return None
        if command == '/session' and len(args) == 1:
            self.session_store = ChatSessionStore(self.config.project_root, args[0])
            self.config.session_name = self.session_store.session_name
            self.messages = self.session_store.load()
            self.transcript_scroll = 0
            if not self.messages:
                self._append_ephemeral('system', f'Switched to empty session {self.config.session_name}.')
            self.status = f'Session switched to {self.config.session_name}.'
            return None
        if command == '/history' and len(args) == 1:
            try:
                value = max(1, int(args[0]))
            except ValueError:
                self.status = 'History must be an integer.'
                return None
            self.config.history_limit = value
            self.status = f'History limit set to {self.config.history_limit}.'
            return None
        if command == '/timeout' and len(args) == 1:
            try:
                value = max(30, int(args[0]))
            except ValueError:
                self.status = 'Timeout must be an integer number of seconds.'
                return None
            previous_timeout = self.config.request_timeout_sec
            self.config.request_timeout_sec = value
            try:
                self.runtime = self._build_runtime()
            except Exception as exc:
                self.config.request_timeout_sec = previous_timeout
                self.status = f'Timeout update failed: {exc}'
                return None
            self.status = f'Provider timeout set to {self.config.request_timeout_sec}s.'
            return None
        if command == '/context-limit' and len(args) == 1:
            try:
                value = max(500, int(args[0]))
            except ValueError:
                self.status = 'Context limit must be an integer number of characters.'
                return None
            previous_limit = self.config.session_context_char_limit
            self.config.session_context_char_limit = value
            try:
                self.runtime = self._build_runtime()
            except Exception as exc:
                self.config.session_context_char_limit = previous_limit
                self.status = f'Context limit update failed: {exc}'
                return None
            self.status = f'Context char limit set to {self.config.session_context_char_limit}.'
            return None
        if command == '/raw':
            for message in reversed(self.messages):
                raw_output = str(message.metadata.get('raw_output', '')).strip()
                if message.role == 'assistant' and raw_output:
                    self._append_ephemeral('system', raw_output)
                    self.status = 'Displayed raw output for the last assistant message.'
                    return None
            self.status = 'No raw assistant output is available yet.'
            return None
        if command == '/run':
            metadata = self._latest_assistant_run_metadata()
            if not metadata:
                self.status = 'No run metadata is available yet.'
                return None
            self._append_ephemeral('system', '\n'.join([
                f"Run ID: {metadata.get('run_id', '(unknown)')}",
                f"Ledger: {metadata.get('run_path', '(unknown)')}",
                f"Grounded: {metadata.get('grounded', False)}",
            ]))
            self.status = 'Displayed run metadata for the last assistant message.'
            return None
        if command == '/artifacts':
            metadata = self._latest_assistant_run_metadata()
            if not metadata:
                self.status = 'No artifact metadata is available yet.'
                return None
            artifacts = metadata.get('artifacts', [])
            if not artifacts:
                self._append_ephemeral('system', 'No artifacts were recorded for the last run.')
                self.status = 'Displayed artifact summary.'
                return None
            lines = [f"Artifacts for run {metadata.get('run_id', '(unknown)')}:" ]
            for item in artifacts:
                kind = str(item.get('kind', '')).strip()
                path_value = str(item.get('path', '')).strip()
                summary = str(item.get('summary', '')).strip()
                verified = bool(item.get('verified', False))
                target = path_value or summary or '(no target)'
                label = f'- {kind}: {target}'
                if kind.startswith('file_'):
                    label += f" [verified={verified}]"
                lines.append(label)
            self._append_ephemeral('system', '\n'.join(lines))
            self.status = 'Displayed artifact summary.'
            return None

        self.status = 'Unknown command. Use /help.'
        return None

    def _handle_approval_response(self, text: str) -> str | None:
        if self.pending_approval is None:
            return None

        value = text.strip().lower()
        if value in {'/approve', 'approve', 'yes', 'y'}:
            approved = True
            label = 'Approved'
        elif value in {'/deny', 'deny', 'no', 'n'}:
            approved = False
            label = 'Denied'
        else:
            self.status = 'Approval pending. Type /approve or /deny.'
            return None

        pending = self.pending_approval
        pending.response_queue.put(approved)
        self.pending_approval = None
        self._append_persistent('system', f'{label}: {pending.request.summary}', metadata={'approval': True})
        self.status = f'{label}. Run resumed.'
        return None

    def _request_approval(self, request: ApprovalRequest) -> bool:
        response_queue: queue.Queue = queue.Queue(maxsize=1)
        self.result_queue.put(('approval', request, response_queue))
        return bool(response_queue.get())

    def _catalog_provider(self, provider_name: str):
        model_name = self.config.model_name if provider_name == self.config.provider_name else self._default_model_name_for_provider(provider_name)
        base_url = self.config.base_url if provider_name == self.config.provider_name else default_base_url_for_provider(provider_name)
        api_key_env = self.config.api_key_env if provider_name == self.config.provider_name else default_api_key_env_for_provider(provider_name)
        return build_provider(
            provider_name=provider_name,
            model_name=model_name,
            base_url=base_url,
            api_key_env=api_key_env,
        )

    def _default_model_name_for_provider(self, provider_name: str) -> str:
        if provider_name == 'echo':
            return 'deermes-dev'
        if provider_name == 'ollama':
            return ''
        return ''

    def _apply_provider_defaults(self, provider_name: str, reset_model: bool = True) -> None:
        self.config.provider_name = provider_name
        self.config.base_url = default_base_url_for_provider(provider_name)
        self.config.api_key_env = default_api_key_env_for_provider(provider_name)
        if reset_model:
            self.config.model_name = self._default_model_name_for_provider(provider_name)

    def _render_model_catalog(self, provider_name: str, models: list[ModelDescriptor]) -> str:
        lines = [f'Provider: {provider_name}']
        if not models:
            lines.append('No models reported by the provider.')
            return '\n'.join(lines)

        for item in models:
            markers: list[str] = []
            if item.name == self.config.model_name and provider_name == self.config.provider_name:
                markers.append('active')
            if item.loaded:
                markers.append('loaded')
            marker_text = f" [{' | '.join(markers)}]" if markers else ''
            details: list[str] = []
            parameter_size = item.metadata.get('parameter_size', '').strip()
            quantization = item.metadata.get('quantization_level', '').strip()
            if parameter_size:
                details.append(parameter_size)
            if quantization:
                details.append(quantization)
            detail_text = f" ({', '.join(details)})" if details else ''
            lines.append(f'- {item.name}{marker_text}{detail_text}')
        lines.append('Use /model MODEL_NAME or /model PROVIDER MODEL_NAME to switch.')
        return '\n'.join(lines)

    def _emit_event(self, message: str) -> None:
        self.result_queue.put(('event', message))

    def _drain_results(self) -> None:
        while True:
            try:
                item = self.result_queue.get_nowait()
            except queue.Empty:
                return

            kind = item[0]
            if kind == 'event':
                message = str(item[1])
                if _is_persistent_error_event(message):
                    self._append_persistent('system', message, metadata={
                        'trace': True,
                        'error': True,
                        'mode': self.config.mode,
                        'provider': self.config.provider_name,
                        'model': self.config.model_name,
                        'permission_profile': self.config.permission_profile,
                    })
                else:
                    self._append_ephemeral('system', message, metadata={'trace': True})
                progress_message = self._progress_message_from_event(message)
                if progress_message is not None:
                    self._append_progress_message(progress_message, source_event=message)
                self.status = message
            elif kind == 'approval':
                request = item[1]
                response_queue = item[2]
                self.pending_approval = PendingApproval(request=request, response_queue=response_queue)
                self._append_persistent('system', request.render(), metadata={'approval': True})
                self.status = 'Approval pending. Type /approve or /deny.'
            elif kind == 'result':
                assistant_text = str(item[1])
                raw_output = str(item[2])
                run_summary = item[3] if len(item) > 3 else None
                metadata = {
                    'raw_output': raw_output,
                    'mode': self.config.mode,
                    'provider': self.config.provider_name,
                    'model': self.config.model_name,
                    'permission_profile': self.config.permission_profile,
                }
                if run_summary is not None:
                    metadata.update({
                        'run_id': run_summary.run_id,
                        'run_path': str(run_summary.ledger_path),
                        'grounded': run_summary.grounded,
                        'artifacts': [artifact.to_payload() for artifact in run_summary.artifacts],
                    })
                self._append_persistent('assistant', assistant_text, metadata=metadata)
                self.busy = False
                self.status = 'Run completed.'
                self.transcript_scroll = 0
            elif kind == 'error':
                error_message = 'Run failed: ' + str(item[1])
                traceback_text = str(item[2])
                self._append_persistent('system', error_message, metadata={
                    'error': True,
                    'traceback': traceback_text,
                    'mode': self.config.mode,
                    'provider': self.config.provider_name,
                    'model': self.config.model_name,
                    'permission_profile': self.config.permission_profile,
                })
                self._write_error_log(error_message, traceback_text)
                self.busy = False
                self.pending_approval = None
                self.status = 'Run failed.'
                self.transcript_scroll = 0

    def _latest_assistant_run_metadata(self) -> dict[str, object]:
        for message in reversed(self.messages):
            if message.role != 'assistant':
                continue
            if 'run_id' in message.metadata or 'artifacts' in message.metadata:
                return dict(message.metadata)
        return {}

    def _runtime_metadata(self) -> dict[str, object]:
        return {
            'mode': self.config.mode,
            'provider': self.config.provider_name,
            'model': self.config.model_name,
            'permission_profile': self.config.permission_profile,
        }

    def _append_progress_message(self, content: str, source_event: str) -> None:
        text = content.strip()
        if not text:
            return
        if self.messages:
            last = self.messages[-1]
            if last.role == 'assistant' and last.metadata.get('progress') and last.content.strip() == text:
                return
        self._append_persistent('assistant', text, metadata={
            **self._runtime_metadata(),
            'progress': True,
            'source_event': source_event,
        })

    def _progress_message_from_event(self, message: str) -> str | None:
        text = message.strip()
        if not text or _is_persistent_error_event(text):
            return None
        if text.startswith('Planner todo tree:\n'):
            tree = text.split('\n', 1)[1].strip()
            return f'Current todo list:\n\n{tree}' if tree else None
        if text == 'Planner: generating the execution todo tree.':
            return "I'm drafting the execution plan first."
        if text == 'Planner: generating a structured brief.':
            return "I'm drafting the task brief first."
        if text.startswith('Started task: '):
            title = _extract_task_title(text[len('Started task: '):])
            return f'Starting: {title}' if title else None
        if text.startswith('Task completed: '):
            title, note = _parse_task_status_event(text[len('Task completed: '):])
            if not title:
                return None
            lines = [f'Completed: {title}']
            if note:
                lines.append(f'Note: {note}')
            return '\n'.join(lines)
        if text.startswith('Task blocked: '):
            title, note = _parse_blocked_task_event(text[len('Task blocked: '):])
            if not title:
                return None
            lines = [f'Blocked: {title}']
            if note:
                lines.append(f'Reason: {note}')
            return '\n'.join(lines)
        if text == 'Researcher: collecting evidence.':
            return 'Starting evidence collection and verification.'
        if text == 'Researcher: produced an interim conclusion.':
            return 'Research is complete. Preparing the interim conclusion.'
        if text == 'Synthesizer: preparing the final response.':
            return 'Preparing the final response.'
        if text.startswith('Finalizing run.'):
            return 'The main steps are complete. Preparing the final response.'
        return None

    def _append_persistent(self, role: str, content: str, metadata: dict[str, object] | None = None) -> None:
        message = ChatMessage(role=role, content=content, metadata=metadata or {})
        self.messages.append(message)
        self.session_store.append(message)

    def _append_ephemeral(self, role: str, content: str, metadata: dict[str, object] | None = None) -> None:
        self.messages.append(ChatMessage(role=role, content=content, metadata={'ephemeral': True, **(metadata or {})}))

    def _write_error_log(self, error_message: str, traceback_text: str) -> None:
        target = self.log_dir / f'{self.config.session_name}.log'
        with target.open('a', encoding='utf-8') as handle:
            handle.write(error_message + '\n')
            handle.write(traceback_text.rstrip() + '\n\n')

    def _draw(self, stdscr) -> None:
        stdscr.erase()
        height, width = stdscr.getmaxyx()
        if width < MIN_WIDTH or height < MIN_HEIGHT:
            self._safe_add(stdscr, 0, 0, f'DeerMes needs at least {MIN_WIDTH}x{MIN_HEIGHT}. Current: {width}x{height}', width, self._attr('error'))
            stdscr.refresh()
            return

        self._draw_header(stdscr, width)

        composer_inner_h = max(3, min(6, max(3, height // 4)))
        composer_h = composer_inner_h + 2
        body_top = 2
        body_h = max(6, height - body_top - composer_h)
        composer_y = body_top + body_h

        sidebar_w = SIDEBAR_MIN_WIDTH if width >= SIDEBAR_TRIGGER_WIDTH else 0
        gap = 1 if sidebar_w else 0
        transcript_w = width - sidebar_w - gap
        if transcript_w < 28:
            sidebar_w = 0
            gap = 0
            transcript_w = width

        self._draw_transcript_panel(stdscr, body_top, 0, body_h, transcript_w)
        if sidebar_w:
            self._draw_sidebar_panel(stdscr, body_top, transcript_w + gap, body_h, sidebar_w)
        self._draw_composer_panel(stdscr, composer_y, 0, composer_h, width)
        stdscr.refresh()

    def _draw_header(self, stdscr, width: int) -> None:
        title = f'DeerMes  session:{self.config.session_name}  mode:{self.config.mode}'
        runtime = f'{self.config.provider_name}/{self.config.model_name}'
        if self.busy and self.pending_approval is None:
            frame = SPINNER_FRAMES[self._status_tick % len(SPINNER_FRAMES)]
            self._status_tick += 1
            runtime = f'{frame} {runtime}'
        self._safe_add(stdscr, 0, 0, title, width, self._attr('title'))
        status = f'{runtime}  profile:{self.config.permission_profile}  history:{self.config.history_limit}'
        self._safe_add(stdscr, 1, 0, _trim_to_width(status + '  |  ' + self.status, width), width, self._attr('meta'))

    def _draw_transcript_panel(self, stdscr, y: int, x: int, h: int, w: int) -> None:
        self._draw_box(stdscr, y, x, h, w, 'Conversation', self._attr('meta'))
        rows = self._transcript_rows(max(w - 2, 10))
        content_h = max(h - 2, 1)
        max_scroll = max(len(rows) - content_h, 0)
        self.transcript_scroll = min(self.transcript_scroll, max_scroll)
        start = max(len(rows) - content_h - self.transcript_scroll, 0)
        visible = rows[start:start + content_h]
        for index, row in enumerate(visible):
            self._safe_add_segments(stdscr, y + 1 + index, x + 1, row.segments, max(w - 2, 1))

    def _draw_sidebar_panel(self, stdscr, y: int, x: int, h: int, w: int) -> None:
        self._draw_box(stdscr, y, x, h, w, 'Context', self._attr('meta'))
        content_w = max(w - 2, 10)
        rows = self._sidebar_rows(content_w)
        for index, (text, attr) in enumerate(rows[: max(h - 2, 0)]):
            self._safe_add(stdscr, y + 1 + index, x + 1, text, content_w, attr)

    def _draw_composer_panel(self, stdscr, y: int, x: int, h: int, w: int) -> None:
        self._draw_box(stdscr, y, x, h, w, 'Compose', self._attr('meta'))
        content_w = self._composer_content_width(w)
        layout = _layout_composer_text(self.input_buffer, self.cursor_index, content_w)
        content_h = max(h - 2, 1)
        start = _visible_window(len(layout.lines), content_h, layout.cursor_line)
        visible = layout.lines[start:start + content_h]

        for index, line in enumerate(visible):
            self._safe_add(stdscr, y + 1 + index, x + 1, line.display, content_w, self._attr('assistant') | curses.A_BOLD)

        cursor_line = layout.lines[layout.cursor_line]
        visible_cursor_y = y + 1 + (layout.cursor_line - start)
        visible_cursor_x = x + 1 + min(cursor_line.prefix_width + layout.cursor_col, max(content_w - 1, 0))
        try:
            stdscr.move(visible_cursor_y, visible_cursor_x)
        except curses.error:
            pass

    def _transcript_rows(self, width: int) -> list[StyledRow]:
        rows: list[StyledRow] = []
        for message in self.messages:
            prefix = self._prefix_for_message(message)
            prefix_attr = self._prefix_attr(message)
            prefix_width = _text_cell_width(prefix) + 1
            content_width = max(width - prefix_width, 10)
            content_rows = self._render_message_rows(message, content_width)
            rows.extend(_attach_prefix(prefix, prefix_attr, content_rows))
            rows.append(StyledRow([StyledSegment('', 0)]))
        return rows or [StyledRow([StyledSegment('', 0)])]

    def _sidebar_rows(self, width: int) -> list[tuple[str, int]]:
        rows: list[tuple[str, int]] = []
        rows.extend(self._section_rows('Runtime', [
            f'Mode: {self.config.mode}',
            f'Provider: {self.config.provider_name}',
            f'Model: {self.config.model_name}',
            f'Profile: {self.config.permission_profile}',
            f'Base URL: {self.config.base_url or "default"}',
            f'Timeout: {self.config.request_timeout_sec or "default"}s',
            f'Context chars: {self.config.session_context_char_limit or "default"}',
        ], width))
        rows.extend(self._section_rows('Session', [
            f'Messages: {len(self.messages)}',
            f'Busy: {"yes" if self.busy else "no"}',
            f'Approval: {"pending" if self.pending_approval else "none"}',
            f'Scroll: {self.transcript_scroll}',
        ], width))
        rows.extend(self._section_rows('Shortcuts', [
            'Enter send',
            'Arrows move cursor',
            'Home/End move on wrapped line',
            'PgUp/PgDn or mouse wheel scroll transcript',
            '/models and /model switch backend',
        ], width))

        recent = self._recent_activity(5)
        if recent:
            rows.extend(self._section_rows('Recent Activity', recent, width))
        return rows

    def _section_rows(self, title: str, entries: list[str], width: int) -> list[tuple[str, int]]:
        rows: list[tuple[str, int]] = [(title, self._attr('section'))]
        for entry in entries:
            wrapped = _wrap_display_text(entry, max(width, 10), preserve_trailing=False)
            if not wrapped:
                wrapped = ['']
            for index, line in enumerate(wrapped):
                leader = '- ' if index == 0 else '  '
                rows.append((f'{leader}{line}', 0))
        rows.append(('', 0))
        return rows

    def _recent_activity(self, limit: int) -> list[str]:
        items: list[str] = []
        for message in reversed(self.messages):
            if message.metadata.get('trace') or message.metadata.get('error') or message.metadata.get('approval'):
                text = ' '.join(message.content.strip().split())
                if text:
                    items.append(text)
            if len(items) >= limit:
                break
        return list(reversed(items))

    def _message_attr(self, message: ChatMessage) -> int:
        if message.role == 'user':
            return self._attr('user')
        if message.role == 'assistant':
            return self._attr('assistant')
        if message.metadata.get('approval'):
            return self._attr('approval')
        if message.metadata.get('error'):
            return self._attr('error')
        if message.metadata.get('trace'):
            return self._attr('trace')
        return self._attr('meta')

    def _prefix_attr(self, message: ChatMessage) -> int:
        if message.role == 'assistant':
            return self._attr('meta')
        return self._message_attr(message)

    def _render_message_rows(self, message: ChatMessage, width: int) -> list[StyledRow]:
        width = max(width, 10)
        base_attr = self._message_attr(message)
        if message.role == 'assistant':
            return _render_markdown_rows(
                message.content,
                width,
                normal_attr=self._attr('assistant'),
                bold_attr=self._attr('md_bold'),
                heading_attr=self._attr('heading'),
                inline_code_attr=self._attr('inline_code'),
                code_theme={
                    'text': self._attr('code_text'),
                    'command': self._attr('code_command'),
                    'flag': self._attr('code_flag'),
                    'string': self._attr('code_string'),
                    'comment': self._attr('code_comment'),
                    'number': self._attr('code_number'),
                    'operator': self._attr('code_operator'),
                    'variable': self._attr('code_variable'),
                    'fence': self._attr('code_fence'),
                },
            )
        return _render_plain_rows(message.content, width, base_attr)

    def _prefix_for_message(self, message: ChatMessage) -> str:
        if message.role == 'user':
            return '[you]'
        if message.role == 'assistant':
            return '[ai ]'
        if message.metadata.get('approval'):
            return '[ok?]'
        if message.metadata.get('error'):
            return '[err]'
        if message.metadata.get('trace'):
            return '[log]'
        return '[sys]'

    def _composer_content_width(self, total_width: int) -> int:
        return max(total_width - 2, 10)

    def _draw_box(self, stdscr, y: int, x: int, h: int, w: int, title: str, attr: int) -> None:
        if h < 2 or w < 2:
            return
        try:
            stdscr.addch(y, x, curses.ACS_ULCORNER, attr)
            stdscr.addch(y, x + w - 1, curses.ACS_URCORNER, attr)
            stdscr.addch(y + h - 1, x, curses.ACS_LLCORNER, attr)
            stdscr.addch(y + h - 1, x + w - 1, curses.ACS_LRCORNER, attr)
            for dx in range(1, w - 1):
                stdscr.addch(y, x + dx, curses.ACS_HLINE, attr)
                stdscr.addch(y + h - 1, x + dx, curses.ACS_HLINE, attr)
            for dy in range(1, h - 1):
                stdscr.addch(y + dy, x, curses.ACS_VLINE, attr)
                stdscr.addch(y + dy, x + w - 1, curses.ACS_VLINE, attr)
        except curses.error:
            pass
        if title and w > 6:
            self._safe_add(stdscr, y, x + 2, f' {title} ', max(w - 4, 1), attr)

    def _safe_add(self, stdscr, y: int, x: int, text: str, width: int, attr: int) -> None:
        if y < 0 or x < 0 or width <= 0:
            return
        visible = _trim_to_width(text, width)
        padded = visible + ' ' * max(width - _text_cell_width(visible), 0)
        try:
            stdscr.addstr(y, x, padded, attr)
        except curses.error:
            pass

    def _safe_add_segments(self, stdscr, y: int, x: int, segments: list[StyledSegment], width: int) -> None:
        if y < 0 or x < 0 or width <= 0:
            return
        used = 0
        for segment in segments:
            remaining = width - used
            if remaining <= 0:
                break
            visible = _trim_to_width(segment.text, remaining)
            if not visible:
                continue
            try:
                stdscr.addstr(y, x + used, visible, segment.attr)
            except curses.error:
                pass
            used += _text_cell_width(visible)
        if used < width:
            try:
                stdscr.addstr(y, x + used, ' ' * (width - used))
            except curses.error:
                pass


def run_chat_ui(
    project_root: Path,
    mode: str,
    provider_name: str,
    model_name: str,
    base_url: str | None,
    api_key_env: str | None = None,
    session_name: str = 'default',
    history_limit: int = 8,
    permission_profile: str | None = None,
    request_timeout_sec: int | None = None,
    session_context_char_limit: int | None = None,
) -> None:
    try:
        locale.setlocale(locale.LC_ALL, '')
    except locale.Error:
        pass

    config = ChatUIConfig(
        project_root=project_root,
        mode=mode,
        provider_name=provider_name,
        model_name=model_name,
        base_url=base_url,
        api_key_env=api_key_env,
        session_name=session_name,
        history_limit=max(1, history_limit),
        permission_profile=permission_profile,
        request_timeout_sec=request_timeout_sec,
        session_context_char_limit=session_context_char_limit,
    )

    def _runner(stdscr) -> None:
        app = DeerMesChatUI(config)
        app.run(stdscr)

    curses.wrapper(_runner)


def _wrap_prefixed(prefix: str, content: str, width: int) -> list[str]:
    available = max(width - _text_cell_width(prefix) - 1, 10)
    wrapped = _wrap_display_text(content or '', available, preserve_trailing=False)
    if not wrapped:
        return [f'{prefix} ']

    lines: list[str] = []
    indent = ' ' * _text_cell_width(prefix)
    for index, line in enumerate(wrapped):
        leader = prefix if index == 0 else indent
        lines.append(f'{leader} {line}')
    return lines


def _render_plain_rows(text: str, width: int, attr: int) -> list[StyledRow]:
    source_lines = text.splitlines() or ['']
    rows: list[StyledRow] = []
    for source_line in source_lines:
        wrapped = _wrap_styled_segments([StyledSegment(source_line, attr)], width)
        rows.extend(wrapped or [StyledRow([StyledSegment('', attr)])])
    return rows or [StyledRow([StyledSegment('', attr)])]


def _render_markdown_rows(
    text: str,
    width: int,
    normal_attr: int,
    bold_attr: int,
    heading_attr: int,
    inline_code_attr: int,
    code_theme: dict[str, int],
) -> list[StyledRow]:
    rows: list[StyledRow] = []
    in_code_block = False
    code_lang = ''

    for source_line in text.splitlines() or ['']:
        stripped = source_line.strip()
        if stripped.startswith('```'):
            if in_code_block:
                rows.extend(_wrap_styled_segments([StyledSegment('```', code_theme['fence'])], width))
                in_code_block = False
                code_lang = ''
            else:
                code_lang = stripped[3:].strip().lower()
                fence_label = f'```{code_lang}' if code_lang else '```'
                rows.extend(_wrap_styled_segments([StyledSegment(fence_label, code_theme['fence'])], width))
                in_code_block = True
            continue

        if in_code_block:
            highlighted = _highlight_code_line(source_line, code_lang, code_theme)
            rows.extend(_wrap_styled_segments(highlighted, width))
            continue

        heading_match = re.match(r'^(#{1,6})\s+(.*)$', source_line)
        if heading_match:
            title = heading_match.group(2).strip() or source_line.strip()
            rows.extend(_wrap_styled_segments([StyledSegment(title, heading_attr)], width))
            continue

        inline_segments = _parse_inline_markdown(source_line, normal_attr, bold_attr, inline_code_attr)
        rows.extend(_wrap_styled_segments(inline_segments, width))

    return rows or [StyledRow([StyledSegment('', normal_attr)])]


def _parse_inline_markdown(text: str, normal_attr: int, bold_attr: int, inline_code_attr: int) -> list[StyledSegment]:
    if not text:
        return [StyledSegment('', normal_attr)]
    segments: list[StyledSegment] = []
    parts = re.split(r'(\*\*.*?\*\*|`[^`]+`)', text)
    for part in parts:
        if not part:
            continue
        if part.startswith('**') and part.endswith('**') and len(part) >= 4:
            segments.append(StyledSegment(part[2:-2], bold_attr))
        elif part.startswith('`') and part.endswith('`') and len(part) >= 2:
            segments.append(StyledSegment(part[1:-1], inline_code_attr))
        else:
            segments.append(StyledSegment(part, normal_attr))
    return segments or [StyledSegment('', normal_attr)]


def _highlight_code_line(line: str, lang: str, code_theme: dict[str, int]) -> list[StyledSegment]:
    shell_langs = {'', 'sh', 'shell', 'bash', 'zsh', 'console'}
    if lang in shell_langs:
        return _highlight_shell_line(line, code_theme)
    return [StyledSegment(line, code_theme['text'])]


def _highlight_shell_line(line: str, code_theme: dict[str, int]) -> list[StyledSegment]:
    if not line:
        return [StyledSegment('', code_theme['text'])]
    if line.lstrip().startswith('#'):
        return [StyledSegment(line, code_theme['comment'])]

    token_re = re.compile(r"""(\s+|"[^"]*"|'[^']*'|\$\{?[A-Za-z_][A-Za-z0-9_]*\}?|--?[A-Za-z0-9._:/-]+|#[^\n]*|\b\d+\b|[|&;<>]+|[^\s]+)""")
    parts = token_re.findall(line)
    segments: list[StyledSegment] = []
    command_painted = False
    for part in parts:
        if not part:
            continue
        attr = code_theme['text']
        if part.isspace():
            attr = code_theme['text']
        elif part.startswith('#'):
            attr = code_theme['comment']
        elif part[:1] in {'"', "'"}:
            attr = code_theme['string']
        elif part.startswith('$'):
            attr = code_theme['variable']
        elif re.fullmatch(r'--?[A-Za-z0-9._:/-]+', part):
            attr = code_theme['flag'] if command_painted else code_theme['command']
            command_painted = True
        elif re.fullmatch(r'[|&;<>]+', part):
            attr = code_theme['operator']
            if any(char in part for char in {'|', '&', ';'}):
                command_painted = False
        elif part.isdigit():
            attr = code_theme['number']
        else:
            if not command_painted and not part.startswith('-'):
                attr = code_theme['command']
                command_painted = True
        segments.append(StyledSegment(part, attr))
    return segments or [StyledSegment(line, code_theme['text'])]


def _attach_prefix(prefix: str, prefix_attr: int, rows: list[StyledRow]) -> list[StyledRow]:
    prefix_first = f'{prefix} '
    prefix_rest = ' ' * _text_cell_width(prefix_first)
    attached: list[StyledRow] = []
    for index, row in enumerate(rows):
        lead = prefix_first if index == 0 else prefix_rest
        segments = [StyledSegment(lead, prefix_attr)]
        segments.extend(row.segments)
        attached.append(StyledRow(segments))
    return attached or [StyledRow([StyledSegment(prefix_first, prefix_attr)])]


def _wrap_styled_segments(segments: list[StyledSegment], width: int) -> list[StyledRow]:
    width = max(width, 1)
    rows: list[list[StyledSegment]] = []
    current: list[StyledSegment] = []
    current_width = 0

    for segment in segments:
        for char in segment.text:
            if char == '\n':
                rows.append(current or [StyledSegment('', segment.attr)])
                current = []
                current_width = 0
                continue
            char_width = _char_cell_width(char)
            if current and current_width + char_width > width:
                rows.append(current)
                current = []
                current_width = 0
            if current and current[-1].attr == segment.attr:
                current[-1].text += char
            else:
                current.append(StyledSegment(char, segment.attr))
            current_width += char_width

    if current or not rows:
        rows.append(current or [StyledSegment('', 0)])
    return [StyledRow(line) for line in rows]


def _layout_composer_text(text: str, cursor_index: int, width: int) -> ComposerLayout:
    width = max(width, 4)
    cursor_index = max(0, min(cursor_index, len(text)))
    lines: list[ComposerLine] = []

    if not text:
        line = ComposerLine(start=0, end=0, prefix=INPUT_PROMPT, prefix_width=_text_cell_width(INPUT_PROMPT), text='')
        return ComposerLayout(lines=[line], cursor_line=0, cursor_col=0)

    position = 0
    first_line = True
    while position < len(text):
        prefix = INPUT_PROMPT if first_line else INPUT_CONTINUATION
        prefix_width = _text_cell_width(prefix)
        capacity = max(width - prefix_width, 1)
        start = position
        current = ''
        current_width = 0

        while position < len(text):
            char = text[position]
            if char == '\n':
                lines.append(ComposerLine(start=start, end=position, prefix=prefix, prefix_width=prefix_width, text=current))
                position += 1
                first_line = False
                if position == len(text):
                    trailing_prefix = INPUT_CONTINUATION
                    trailing_width = _text_cell_width(trailing_prefix)
                    lines.append(ComposerLine(start=position, end=position, prefix=trailing_prefix, prefix_width=trailing_width, text=''))
                break

            piece = '    ' if char == '\t' else char
            piece_width = _text_cell_width(piece)
            if current and current_width + piece_width > capacity:
                lines.append(ComposerLine(start=start, end=position, prefix=prefix, prefix_width=prefix_width, text=current))
                first_line = False
                break

            current += piece
            current_width += piece_width
            position += 1
        else:
            lines.append(ComposerLine(start=start, end=position, prefix=prefix, prefix_width=prefix_width, text=current))
            break

    if not lines:
        line = ComposerLine(start=0, end=0, prefix=INPUT_PROMPT, prefix_width=_text_cell_width(INPUT_PROMPT), text='')
        return ComposerLayout(lines=[line], cursor_line=0, cursor_col=0)

    cursor_line = len(lines) - 1
    cursor_col = _segment_cell_width(text, lines[-1].start, lines[-1].end)
    for index, line in enumerate(lines):
        next_start = lines[index + 1].start if index + 1 < len(lines) else None
        if cursor_index < line.end:
            cursor_line = index
            cursor_col = _segment_cell_width(text, line.start, cursor_index)
            break
        if cursor_index == line.end:
            cursor_line = index
            cursor_col = _segment_cell_width(text, line.start, line.end)
            break
        if next_start is not None and cursor_index < next_start:
            cursor_line = index
            cursor_col = _segment_cell_width(text, line.start, line.end)
            break

    return ComposerLayout(lines=lines, cursor_line=cursor_line, cursor_col=cursor_col)


def _index_for_column(text: str, line: ComposerLine, target_col: int) -> int:
    if target_col <= 0:
        return line.start
    used = 0
    index = line.start
    while index < line.end:
        char = text[index]
        width = _text_cell_width('    ' if char == '\t' else char)
        next_used = used + width
        if target_col < next_used:
            return index if target_col - used < next_used - target_col else index + 1
        if target_col == next_used:
            return index + 1
        used = next_used
        index += 1
    return line.end


def _segment_cell_width(text: str, start: int, end: int) -> int:
    segment = []
    for char in text[start:end]:
        if char == '\t':
            segment.append('    ')
        elif char not in {'\n', '\r'}:
            segment.append(char)
    return _text_cell_width(''.join(segment))


def _visible_window(total: int, size: int, focus: int) -> int:
    if total <= size:
        return 0
    if focus < size:
        return 0
    if focus >= total - 1:
        return total - size
    half = size // 2
    start = max(focus - half, 0)
    return min(start, total - size)


def _wrap_display_text(text: str, width: int, preserve_trailing: bool) -> list[str]:
    width = max(width, 1)
    lines: list[str] = []
    current = ''
    current_width = 0

    for char in text:
        if char == '\n':
            lines.append(current if preserve_trailing else current.rstrip())
            current = ''
            current_width = 0
            continue

        chunk = '    ' if char == '\t' else char
        for item in chunk:
            item_width = _char_cell_width(item)
            if current and current_width + item_width > width:
                lines.append(current if preserve_trailing else current.rstrip())
                current = ''
                current_width = 0
            current += item
            current_width += item_width

    if current or not lines:
        lines.append(current if preserve_trailing else current.rstrip())
    return lines


def _trim_to_width(text: str, width: int) -> str:
    output: list[str] = []
    used = 0
    for char in text:
        char_width = _char_cell_width(char)
        if used + char_width > width:
            break
        output.append(char)
        used += char_width
    return ''.join(output)


def _text_cell_width(text: str) -> int:
    return sum(_char_cell_width(char) for char in text)


def _char_cell_width(char: str) -> int:
    if char == '\t':
        return 4
    if char in {'\n', '\r'}:
        return 0
    if unicodedata.combining(char):
        return 0
    return 2 if unicodedata.east_asian_width(char) in {'W', 'F'} else 1


def _is_persistent_error_event(message: str) -> bool:
    lowered = message.lower()
    return ' tool ' in lowered and ' returned ' in lowered


def _extract_task_title(payload: str) -> str:
    body = payload.strip()
    if body.endswith('.'):
        body = body[:-1].rstrip()
    if ' (' not in body or not body.endswith(')'):
        return body
    title, suffix = body.rsplit(' (', 1)
    if suffix and ')' not in suffix[:-1]:
        return title.strip()
    return body


def _parse_task_status_event(payload: str) -> tuple[str, str]:
    main, separator, note = payload.partition('. Note: ')
    title = _extract_task_title(main)
    detail = note.strip() if separator else ''
    return title, detail


def _parse_blocked_task_event(payload: str) -> tuple[str, str]:
    main, separator, note = payload.partition('. ')
    if not separator:
        return _extract_task_title(payload), ''
    return _extract_task_title(main), note.strip()
