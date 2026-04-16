# DeerMes Architecture

## Source Inspirations

### DeerFlow-Inspired

The following design choices are intentionally borrowed from DeerFlow's explicit workflow model:

- orchestration is separate from tool execution
- the planner produces a concrete execution plan
- the runtime can later expand into specialized workers
- tools are first-class and inspectable

In DeerMes, this maps to:

- `deermes.execution.graph`
- `deermes.execution.planner`
- `deermes.runtime.app`
- `deermes.tools.*`

### Hermes-Inspired

The following design choices are intentionally borrowed from Hermes Agent's persistent-context model:

- project context files should be loaded automatically
- agent behavior should accumulate reusable memory
- operator preference and style should remain stable across sessions
- reflection should turn runs into future context

In DeerMes, this maps to:

- `deermes.learning.context`
- `deermes.learning.memory`
- `deermes.learning.reflection`
- future `profile` and `skill` modules

## Current Runtime Shape

1. Load project context
2. Load recent memory
3. Produce a deterministic first-pass plan
4. Run tool-bearing steps
5. Ask a provider for synthesis
6. Persist a reflection record

## Planned Evolution

### Phase 1

- Replace deterministic planner with model-backed planner
- Add Ollama provider
- Add safer shell policy and scoped filesystem tools

### Phase 2

- Split runtime into supervisor + specialized workers
- Add task handoff and branch merge
- Add memory retrieval by tags and embeddings

### Phase 3

- Add long-horizon skill learning
- Add session replay and evaluation
- Add web or terminal UI for supervision

## Open Design Decisions

These decisions are still open and should be confirmed before heavy implementation:

- Should DeerMes stay single-process first, or become multi-agent immediately?
- Should learning be file-first, vector-first, or hybrid?
- Should the first production provider target Ollama, OpenAI-compatible APIs, or both equally?
- Should tools be permissive by default or policy-gated by profile?
