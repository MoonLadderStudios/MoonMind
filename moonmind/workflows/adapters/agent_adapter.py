"""Shared adapter contract for true agent runtime integrations."""

from __future__ import annotations

from typing import Protocol

from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunHandle,
    AgentRunResult,
    AgentRunStatus,
)

class AgentAdapter(Protocol):
    """Provider-neutral lifecycle interface for true agent runtimes."""

    async def start(self, request: AgentExecutionRequest) -> AgentRunHandle:
        """Start one run and return a normalized run handle."""

    async def status(self, run_id: str) -> AgentRunStatus:
        """Read normalized status for one run."""

    async def fetch_result(self, run_id: str) -> AgentRunResult:
        """Fetch normalized result payload for one run."""

    async def cancel(self, run_id: str) -> AgentRunStatus:
        """Cancel one run and return normalized post-cancel status."""

__all__ = ["AgentAdapter"]
