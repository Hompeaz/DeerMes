from .loop import AgentAction, AgentLoop, AgentLoopState, parse_agent_action


def build_runtime(*args, **kwargs):
    from .app import build_runtime as _build_runtime

    return _build_runtime(*args, **kwargs)


def build_deerflow_runtime(*args, **kwargs):
    from .deerflow_app import build_deerflow_runtime as _build_deerflow_runtime

    return _build_deerflow_runtime(*args, **kwargs)


__all__ = [
    'AgentAction',
    'AgentLoop',
    'AgentLoopState',
    'build_runtime',
    'build_deerflow_runtime',
    'parse_agent_action',
]
