from __future__ import annotations

import pytest

from moonmind.schemas.container_job_models import OwnerIdentity
from moonmind.security.container_job_capabilities import (
    mint_container_job_session_capability,
)
from moonmind.security.execution_fanout_capabilities import (
    ExecutionFanoutCapabilityError,
    mint_execution_fanout_capability,
    verify_execution_fanout_capability,
)


def _mint(*, now: int = 100) -> str:
    return mint_execution_fanout_capability(
        secret="test-secret",
        parent_workflow_id="mm:parent",
        agent_run_id="agent-run-1",
        step_id="step-1",
        session_id="session-1",
        runtime_id="codex_cli",
        source_kind="omnigent",
        lifetime_seconds=60,
        now=now,
    )


def test_execution_fanout_capability_round_trips_bounded_authority() -> None:
    capability = verify_execution_fanout_capability(
        _mint(), secret="test-secret", now=120
    )

    assert capability.parent_workflow_id == "mm:parent"
    assert capability.agent_run_id == "agent-run-1"
    assert capability.step_id == "step-1"
    assert capability.session_id == "session-1"
    assert capability.runtime_id == "codex_cli"
    assert capability.source_kind == "omnigent"
    assert capability.expires_at == 160


def test_execution_fanout_capability_rejects_tampering_and_expiry() -> None:
    token = _mint()
    with pytest.raises(ExecutionFanoutCapabilityError, match="invalid"):
        verify_execution_fanout_capability(token + "x", secret="test-secret", now=120)
    with pytest.raises(ExecutionFanoutCapabilityError, match="expired"):
        verify_execution_fanout_capability(token, secret="test-secret", now=160)


def test_container_job_bearer_cannot_authorize_execution_fanout() -> None:
    container_token = mint_container_job_session_capability(
        secret="test-secret",
        owner=OwnerIdentity(principalId="agent-run-1", principalType="service"),
        agent_run_id="agent-run-1",
        workflow_id="mm:parent",
        session_id="session-1",
        runtime_id="codex_cli",
        lifetime_seconds=60,
        now=100,
    )

    with pytest.raises(ExecutionFanoutCapabilityError, match="unsupported"):
        verify_execution_fanout_capability(
            container_token, secret="test-secret", now=120
        )
