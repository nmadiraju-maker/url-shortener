"""Agent registry (dependency injection point; swap in LLM-backed agents here)."""
from __future__ import annotations

from .base import Agent
from .design import DesignAgent, MigrationAgent
from .development import DevelopmentAgent
from .docs import DocsAgent, StaticDocsAgent
from .impact import ImpactAnalysisAgent
from .qa import QAAgent
from .release import ReleaseAgent
from .requirements import RequirementsAgent
from .review import ReviewAgent
from .security import SecurityAgent


def default_agents() -> dict[str, Agent]:
    agents: list[Agent] = [RequirementsAgent(), DesignAgent(), MigrationAgent(), ImpactAnalysisAgent(),
                           DevelopmentAgent(), ReviewAgent(), QAAgent(), SecurityAgent(), DocsAgent(),
                           StaticDocsAgent(), ReleaseAgent()]
    return {a.name: a for a in agents}
