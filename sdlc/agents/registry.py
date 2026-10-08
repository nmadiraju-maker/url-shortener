"""Agent registry (dependency injection point; swap in LLM-backed agents here)."""
from __future__ import annotations

from ..llm import client_from_env
from .base import Agent
from .design import DesignAgent, MigrationAgent
from .development import REPO_ROOT, DevelopmentAgent
from .docs import DocsAgent, StaticDocsAgent
from .impact import ImpactAnalysisAgent
from .qa import QAAgent
from .release import ReleaseAgent
from .requirements import RequirementsAgent
from .requirements_llm import LLMRequirementsAgent
from .review import ReviewAgent
from .security import SecurityAgent


def default_agents() -> dict[str, Agent]:
    llm = client_from_env(REPO_ROOT / "scenarios" / "cassettes" / "requirements.json")
    agents: list[Agent] = [RequirementsAgent(), LLMRequirementsAgent(llm), DesignAgent(), MigrationAgent(),
                           ImpactAnalysisAgent(), DevelopmentAgent(), ReviewAgent(), QAAgent(), SecurityAgent(),
                           DocsAgent(), StaticDocsAgent(), ReleaseAgent()]
    return {a.name: a for a in agents}
