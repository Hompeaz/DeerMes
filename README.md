# DeerMes

DeerMes is a general-purpose AI agent project that combines a DeerFlow-inspired execution layer with a Hermes-inspired learning layer.

## Direction

- Execution layer: coordinator, planner, tool-aware runtime, final synthesis.
- Learning layer: context files, persistent memory, reflection, operator profile.
- Runtime target: local models via Ollama, Anthropic via the native Messages API, or major OpenAI-compatible endpoints.
- Interaction layer: one-shot task runs and a terminal chat UI.

## Current Scaffold

- Python-first backend under `src/deermes`
- Single-agent and DeerFlow-style execution modes
- Context loading for `AGENTS.md`, `SOUL.md`, `.cursorrules`
- JSONL memory store plus chat session transcripts
- Tool registry with shell and filesystem tools
- Provider abstraction with `echo`, `ollama`, `anthropic`, and major OpenAI-compatible providers
- `curses` terminal chat UI with persistent sessions
- Config-driven permission profiles with sandbox roots and approval gates

## Quick Start

Initialize DeerMes once with your preferred workspace, provider, model, and permission profile:

```bash
deermes init
```

Then you can inspect the active defaults:

```bash
deermes doctor
```

Run a one-shot task with the saved defaults:

```bash
deermes run "Inspect this repository and summarize the next engineering actions."
```

## Terminal Chat UI

```bash
deermes tui
```

You can still override anything at launch time:

```bash
deermes tui --project-root ~/code/deermes --mode deerflow --provider ollama --model gemma4:31b-it-bf16 --base-url http://127.0.0.1:11435
```

Useful commands inside the TUI:

- `/help`
- `/quit`
- `/mode single-agent|deerflow`
- `/provider PROVIDER_NAME`
- `/model MODEL_NAME`
- `/base-url URL`
- `/profile PROFILE_NAME`
- `/permissions`
- `/approve`
- `/deny`
- `/session SESSION_NAME`
- `/history N`
- `/raw`
- `/run`
- `/artifacts`

Session transcripts are stored under `.deermes/sessions/`.

DeerMes also ships a repo-local launcher at [bin/deermes](/home/hompeaz/code/deermes/bin/deermes). If `~/.local/bin` is on your `PATH`, you can symlink it there and use `deermes tui` directly.

## Control Commands

DeerMes now uses a user-level control config, separate from per-project runtime and permission files.

Common commands:

- `deermes init`: create or update the user control config
- `deermes doctor`: show the active workspace, provider profile, model, and permission defaults
- `deermes config show`: show the current control config summary
- `deermes config profiles`: list provider profiles
- `deermes config set provider-profile NAME`: switch the active provider profile
- `deermes config set project-root PATH`: change the default workspace
- `deermes config set permission-profile PROFILE`: change the default permission profile
- `deermes models`: list models for the selected provider

Supported provider choices in `deermes init` are:

- `ollama`
- `anthropic`
- `openai-api`
- `openrouter`
- `gemini`
- `groq`
- `together`
- `fireworks`
- `deepseek`
- `xai`
- `perplexity`
- `lmstudio`
- `custom-openai-compatible`
- `echo`

`OpenAI OAuth` is not implemented yet; DeerMes currently supports API-key based providers plus local gateways. For other OpenAI-compatible servers such as LiteLLM or vLLM, use `custom-openai-compatible`.

## Permission Profiles

Permission profiles are stored in [deermes.permissions.json](/home/hompeaz/code/deermes/deermes.permissions.json).
The runtime loads the `default_profile` unless you pass `--permission-profile` or switch profiles inside the TUI with `/profile`.

Each profile can define:

- `read_roots`: paths the agent can read without leaving the sandbox.
- `write_roots`: paths the agent can write inside.
- `allow_shell`: whether shell access is enabled at all.
- `allowed_commands`: the shell command allowlist. Use `"*"` to allow any command.
- `approval_required_for`: actions that require interactive approval.

Supported approval tokens are:

- `read`
- `read_outside_roots`
- `write`
- `write_outside_roots`
- `shell`

The config supports `{project_root}` and `{home}` path placeholders.
You can add, remove, or rename profiles freely as long as `default_profile` points to an existing entry.

## Architecture Notes

This scaffold intentionally separates orchestration from learning state.
That is the core fusion between DeerFlow and Hermes:

- DeerFlow contributes explicit workflow decomposition.
- Hermes contributes persistent, reusable agent context.

The next implementation step after this scaffold should be to tighten researcher stopping criteria and tool selection inside the DeerFlow path.

## License

DeerMes is released under the MIT License. See `LICENSE` and `THIRD_PARTY_NOTICES.md` for project licensing and upstream attribution.
