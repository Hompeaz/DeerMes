# Third-Party Notices

DeerMes is an original project that combines a DeerFlow-style execution layer with a Hermes-style learning layer. DeerMes is released under the MIT License.

## License Posture

- DeerMes is distributed under the MIT License in this repository.
- DeerMes currently uses DeerFlow and Hermes Agent primarily as architectural and behavioral references.
- When DeerMes directly vendors, copies, or adapts upstream source code from those projects, the affected files must retain the upstream MIT copyright and permission notice.
- This notice file documents the current upstream references and is intended to travel with future open-source releases.

## Referenced Projects

### DeerFlow

- Project: https://github.com/bytedance/deer-flow
- License: MIT
- Usage in DeerMes:
  - workflow decomposition ideas
  - supervisor / planner / researcher / synthesizer role split
  - deep-research style execution layering

### Hermes Agent

- Project: https://github.com/nousresearch/hermes-agent
- Docs: https://hermes-agent.nousresearch.com/
- License: MIT
- Usage in DeerMes:
  - persistent memory and profile-oriented learning
  - reflection and long-term context design
  - tool-using single-agent loop concepts

## Current Attribution Boundary

At the time of this notice, DeerMes keeps its own source tree and does not wholesale vendor either upstream project. The runtime, tool registry, TUI, and integration code are maintained in this repository. If that boundary changes, the specific copied or adapted files should be marked inline with upstream attribution as well as in this notice.
