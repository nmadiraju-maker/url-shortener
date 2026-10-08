"""Control-flow exceptions used by agents and the engine."""
from __future__ import annotations


class TransientError(Exception):
    """Retryable infrastructure/tooling failure (e.g. flaky runner, timeout)."""


class AgentError(Exception):
    """Non-retryable agent failure; engine tries the fallback agent, then fails the stage."""


class GraphError(ValueError):
    """Invalid stage graph (cycle, unknown dependency, duplicate id)."""
