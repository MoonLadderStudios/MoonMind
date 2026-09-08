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
from moonmind.omnigent.workspace_intent import (
    authored_connection_ref,
    authored_required_capabilities,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.schemas.container_job_models import OwnerIdentity
from moonmind.schemas.workspace_locator_models import SandboxWorkspaceLocator
from moonmind.security.container_job_capabilities import (
    mint_container_job_session_capability,
)
from moonmind.security.execution_fanout_capabilities import (
    EXECUTION_FANOUT_REQUIRED_CAPABILITY,
    ExecutionFanoutCapabilityError,
    mint_execution_fanout_capability,
    require_execution_fanout_authorization,
)


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
        workspace_attachment: Mapping[str, Any] | None = None,
    ) -> Mapping[str, str]:
        required_capabilities = authored_required_capabilities(request)
        needs_fanout = EXECUTION_FANOUT_REQUIRED_CAPABILITY in required_capabilities
        needs_containers = "docker" in required_capabilities
        if not needs_fanout and not needs_containers:
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
                "runtime capability identity is incomplete",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )
        environment = {
            "MOONMIND_URL": self._moonmind_url,
            "MOONMIND_AGENT_RUN_ID": step_execution_id,
            "MOONMIND_TASK_WORKFLOW_ID": workflow_id,
            "MOONMIND_STEP_ID": step_execution_id,
            "MOONMIND_RUNTIME_ID": runtime_id,
        }
        if needs_containers:
            try:
                locator = SandboxWorkspaceLocator.model_validate(
                    (request.workspace_spec or {}).get("workspaceLocator")
                )
            except ValueError as exc:
                raise HarnessPlatformError(
                    "container jobs require an authoritative sandbox workspace locator",
                    code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
                ) from exc
            access_mode = (workspace_attachment or {}).get("accessMode")
            if not isinstance(access_mode, str) or access_mode not in {
                "read-only",
                "read-write",
            }:
                raise HarnessPlatformError(
                    "container jobs require authoritative workspace access mode",
                    code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
                )
            environment.update(
                {
                    "MOONMIND_CONTAINER_JOBS_MCP_URL": self._moonmind_url.rstrip("/")
                    + "/mcp/container",
                    "MOONMIND_CONTAINER_JOBS_BEARER_TOKEN": mint_container_job_session_capability(
                        secret=self._signing_secret,
                        owner=OwnerIdentity(
                            principalId=step_execution_id, principalType="service"
                        ),
                        agent_run_id=step_execution_id,
                        workflow_id=workflow_id,
                        step_id=step_execution_id,
                        session_id=host_lease_ref,
                        runtime_id=runtime_id,
                        source_kind="omnigent",
                        workspace_kind="sandbox",
                        workspace_id=locator.workspace_id,
                        workspace_relative_path=locator.relative_path,
                        workspace_read_only=access_mode == "read-only",
                        lifetime_seconds=int(launch_policy.limits["timeoutSeconds"]),
                    ),
                    "MOONMIND_CONTAINER_JOBS_SOURCE_KIND": "omnigent",
                    "MOONMIND_CONTAINER_JOBS_SESSION_ID": host_lease_ref,
                    "MOONMIND_CONTAINER_JOBS_WORKSPACE_KIND": "sandbox",
                    "MOONMIND_CONTAINER_JOBS_WORKSPACE_ID": locator.workspace_id,
                    "MOONMIND_CONTAINER_JOBS_WORKSPACE_RELATIVE_PATH": locator.relative_path,
                }
            )
        if needs_fanout:
            environment["MOONMIND_EXECUTION_FANOUT_BEARER_TOKEN"] = (
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
            )
        repository_connection_ref = authored_connection_ref(request)
        if repository_connection_ref:
            environment["MOONMIND_REPOSITORY_CONNECTION_REF"] = (
                repository_connection_ref
            )
        return environment


__all__ = ["OmnigentRuntimeEnvironmentService"]
