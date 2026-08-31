"""Lease-scoped capability construction for generic Omnigent hosts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from moonmind.omnigent.harness_platform.execution_plan import (
    OmnigentExecutionPlanEnvelope,
)
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.harness_platform.host_classes import LaunchPolicy
from moonmind.omnigent.workspace_intent import authored_required_capabilities
from moonmind.security.execution_fanout_capabilities import (
    EXECUTION_FANOUT_REQUIRED_CAPABILITY,
    ExecutionFanoutCapabilityError,
    mint_execution_fanout_capability,
    require_execution_fanout_authorization,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest


class OmnigentRuntimeEnvironmentService:
    """Mint only capabilities authorized for one immutable host lease."""

    def __init__(self, *, moonmind_url: str, signing_secret: str) -> None:
        self._moonmind_url = moonmind_url.strip()
        self._signing_secret = signing_secret

    @staticmethod
    def _execution_fanout_authorization(
        request: AgentExecutionRequest,
    ) -> Mapping[str, Any] | None:
        step_execution = request.step_execution
        if step_execution is None:
            return None
        policy = step_execution.skill_source_policy
        if "executionFanout" not in policy:
            return None
        evidence = policy.get("executionFanout")
        if not isinstance(evidence, Mapping):
            raise HarnessPlatformError(
                "execution fan-out authorization evidence is malformed",
                code="authorization_denied",
            )
        return evidence

    def build(
        self,
        *,
        request: AgentExecutionRequest,
        plan: OmnigentExecutionPlanEnvelope,
        host_lease_ref: str,
        launch_policy: LaunchPolicy,
    ) -> Mapping[str, str]:
        required_capabilities = authored_required_capabilities(request)
        if EXECUTION_FANOUT_REQUIRED_CAPABILITY not in required_capabilities:
            return {}
        try:
            require_execution_fanout_authorization(
                required_capabilities,
                self._execution_fanout_authorization(request),
            )
        except ExecutionFanoutCapabilityError as exc:
            raise HarnessPlatformError(str(exc), code="authorization_denied") from exc
        step_execution = request.step_execution
        workflow_id = (
            str(step_execution.workflow_id or "").strip()
            if step_execution is not None
            else str(request.correlation_id or "").strip()
        )
        step_execution_id = (
            str(step_execution.step_execution_id or "").strip()
            if step_execution is not None
            else str(request.correlation_id or "").strip()
        )
        runtime_id = str(plan.payload.harnessId or "").strip()
        if (
            not workflow_id
            or not step_execution_id
            or not runtime_id
            or not self._moonmind_url
        ):
            raise HarnessPlatformError(
                "execution fan-out runtime identity is incomplete",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )
        return {
            "MOONMIND_URL": self._moonmind_url,
            "MOONMIND_AGENT_RUN_ID": step_execution_id,
            "MOONMIND_TASK_WORKFLOW_ID": workflow_id,
            "MOONMIND_STEP_ID": step_execution_id,
            "MOONMIND_RUNTIME_ID": runtime_id,
            "MOONMIND_EXECUTION_FANOUT_BEARER_TOKEN": (
                mint_execution_fanout_capability(
                    secret=self._signing_secret,
                    parent_workflow_id=workflow_id,
                    agent_run_id=step_execution_id,
                    step_id=step_execution_id,
                    session_id=host_lease_ref,
                    runtime_id=runtime_id,
                    source_kind="omnigent",
                    lifetime_seconds=int(launch_policy.limits["timeoutSeconds"]),
                )
            ),
        }


__all__ = ["OmnigentRuntimeEnvironmentService"]
