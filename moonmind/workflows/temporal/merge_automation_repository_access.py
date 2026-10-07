"""Consume the parent plan's repository authority for trusted review operations."""

from collections.abc import Mapping
from contextlib import asynccontextmanager
from typing import Any


@asynccontextmanager
async def merge_automation_repository_token(
    authority: Mapping[str, Any], *, repository: str, operation: str
):
    """Acquire only the frozen collaboration selection; never use ambient auth."""
    from temporalio import activity

    from api_service.db.base import async_session_maker
    from moonmind.omnigent.bridge_artifacts import TemporalOmnigentArtifactGateway
    from moonmind.omnigent.host_services.github_credentials import (
        OmnigentGithubCredentialService,
    )
    from moonmind.schemas.agent_runtime_models import OmnigentExecutionPlanBinding
    from moonmind.workflows.executions.repository_contract import normalize_endpoint
    from moonmind.workflows.temporal.activities.omnigent_session_activities import (
        _load_verified_execution_plan,
    )

    if not isinstance(authority, Mapping):
        raise ValueError("review-only requires admitted repository authority")
    owner = str(authority.get("executionOwner") or "").strip()
    if not owner or operation not in {"read", "review_request"}:
        raise ValueError("review-only repository authority is incomplete")
    binding = OmnigentExecutionPlanBinding.model_validate(
        authority.get("parentExecutionPlan")
    )
    execution = activity.info()
    if owner != execution.workflow_id or not execution.workflow_run_id:
        raise ValueError("review authority conflicts with its executing workflow")
    principal = f"workflow:{execution.workflow_id}"
    plan = await _load_verified_execution_plan(
        binding, workflow_id=execution.workflow_id, run_id=execution.workflow_run_id
    )
    credentials = OmnigentGithubCredentialService(
        None,
        session_factory=async_session_maker,
        artifact_gateway=TemporalOmnigentArtifactGateway(
            async_session_maker, principal=principal
        ),
    )
    identity = await credentials.admitted_repository_identity(
        plan=plan,
        request=None,
        role="collaboration",
        operation=operation,
        repository=repository,
    )
    # This gate admits github.com PR URLs. Do not send a different host's
    # credentials to the GitHub.com-only review adapter.
    if normalize_endpoint(identity.endpoint) != "https://github.com":
        raise ValueError("review target host conflicts with admitted repository")
    acquired = await credentials.acquire_repository_use(
        plan=plan,
        request=None,
        role="collaboration",
        operation=operation,
        repository=repository,
        execution_owner=owner,
    )
    if acquired is None:
        raise ValueError("review-only requires authenticated collaboration authority")
    try:
        yield acquired.credential.use_now(lambda raw: raw.decode("utf-8"))
    finally:
        acquired.credential.clear()
