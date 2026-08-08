from __future__ import annotations

import pytest

from moonmind.schemas.container_job_models import OwnerIdentity
from moonmind.security.container_job_capabilities import (
    ContainerJobCapabilityError,
    mint_container_job_session_capability,
    verify_container_job_session_capability,
)


def _token(*, now: int = 100) -> str:
    return mint_container_job_session_capability(
        secret="test-secret",
        owner=OwnerIdentity(principalId="user-1", principalType="user"),
        agent_run_id="run-1",
        workflow_id="workflow-1",
        step_id="step-1",
        session_id="session-1",
        runtime_id="codex_cli",
        lifetime_seconds=60,
        now=now,
    )


def test_capability_round_trips_scoped_owner_and_session() -> None:
    capability = verify_container_job_session_capability(
        _token(), secret="test-secret", now=120
    )

    assert capability.owner == OwnerIdentity(
        principalId="user-1", principalType="user"
    )
    assert capability.agent_run_id == "run-1"
    assert capability.workflow_id == "workflow-1"
    assert capability.step_id == "step-1"
    assert capability.session_id == "session-1"
    assert capability.runtime_id == "codex_cli"
    assert capability.source_kind == "managed_session"
    assert capability.workspace_kind == "managed_runtime"
    assert capability.workspace_id == "run-1"
    assert capability.workspace_relative_path == "repo"
    assert capability.expires_at == 160


def test_capability_round_trips_omnigent_sandbox_authority() -> None:
    token = mint_container_job_session_capability(
        secret="test-secret",
        owner=OwnerIdentity(principalId="run-2", principalType="service"),
        agent_run_id="run-2",
        workflow_id="workflow-2",
        step_id="step-2",
        session_id="host-lease-2",
        runtime_id="codex_cli",
        source_kind="omnigent",
        workspace_kind="sandbox",
        workspace_id="sandbox-2",
        workspace_relative_path=".",
        lifetime_seconds=60,
        now=100,
    )

    capability = verify_container_job_session_capability(
        token, secret="test-secret", now=120
    )

    assert capability.source_kind == "omnigent"
    assert capability.workflow_id == "workflow-2"
    assert capability.step_id == "step-2"
    assert capability.workspace_kind == "sandbox"
    assert capability.workspace_id == "sandbox-2"
    assert capability.workspace_relative_path == "."


def test_capability_rejects_tampering() -> None:
    token = _token()

    with pytest.raises(ContainerJobCapabilityError, match="invalid"):
        verify_container_job_session_capability(
            token + "changed", secret="test-secret", now=120
        )


def test_capability_rejects_expiry() -> None:
    with pytest.raises(ContainerJobCapabilityError, match="expired"):
        verify_container_job_session_capability(
            _token(), secret="test-secret", now=160
        )
