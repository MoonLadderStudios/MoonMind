"""Realizer protocol (Phase 1).

A realizer has a versioned ref (`codex-profile-bound@1`, `generic-omnigent-host@1`)
and owns side-effects for one execution plan. The Temporal activity selects the
realizer via persisted plan's executionRealizerRef – no harness branches in
the activity itself.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

# Re-export for convenience
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.omnigent.harness_platform.execution_plan import OmnigentExecutionPlanEnvelope


@runtime_checkable
class OmnigentExecutionRealizer(Protocol):
    ref: str

    async def execute(
        self,
        request: AgentExecutionRequest,
        plan: OmnigentExecutionPlanEnvelope,
    ) -> AgentRunResult:
        """Execute one workflow run via this realizer's host + session driver."""

    async def reconcile(
        self,
        plan_ref: str,
        runtime_binding_ref: str | None,
        *,
        command_authority: dict[str, object],
    ) -> None:
        """Reconcile janitor/cleanup for a plan's runtime binding."""
