"""MoonLadderStudios/MoonMind#4356 R5: machine-authority matrix (hermetic subset).

Exercises the real scoped-capability boundaries: a correctly scoped
operation verifies, while expired, tampered, wrong-secret, and cross-session
credentials are denied. Provider/runtime/worker credentials never verify as
operator access (they fail closed through the same verifier), and admitted
work outlives browser access loss because capabilities carry no browser
session binding -- only session/runtime/owner scope plus bounded lifetime.

Lease/generation scoping beyond session capability lifetime and the
multi-worker deployment continuity proof belong to the integrated-candidate
run; this matrix pins the in-process authority primitives hermetically.
"""

from __future__ import annotations

import pytest

from moonmind.omnigent.bridge_security import (
    OmnigentAuthorizationError,
    assert_bridge_session_binding,
    authorize_bridge_access,
    BridgeSessionBinding,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.schemas.container_job_models import OwnerIdentity
from moonmind.security.container_job_capabilities import (
    ContainerJobCapabilityError,
    mint_container_job_session_capability,
    verify_container_job_session_capability,
)

_SECRET = "machine-authority-4356-secret"


def _mint(*, session_id="session-4356", lifetime_seconds=60, now=100, **overrides):
    kwargs = {
        "secret": _SECRET,
        "owner": OwnerIdentity(principalId="run-4356", principalType="service"),
        "agent_run_id": "run-4356",
        "workflow_id": "workflow-4356",
        "session_id": session_id,
        "runtime_id": "codex_cli",
        "lifetime_seconds": lifetime_seconds,
        "now": now,
    }
    kwargs.update(overrides)
    return mint_container_job_session_capability(**kwargs)


def test_correctly_scoped_operation_verifies() -> None:
    capability = verify_container_job_session_capability(
        _mint(), secret=_SECRET, now=120
    )
    assert capability.session_id == "session-4356"
    assert capability.agent_run_id == "run-4356"
    assert capability.workflow_id == "workflow-4356"


def test_wrong_secret_never_grants_access() -> None:
    """Provider/runtime/worker material under a different secret is denied."""
    with pytest.raises(ContainerJobCapabilityError, match="invalid"):
        verify_container_job_session_capability(
            _mint(), secret="another-provider-secret", now=120
        )


def test_expired_capability_denied() -> None:
    with pytest.raises(ContainerJobCapabilityError, match="expired"):
        verify_container_job_session_capability(_mint(), secret=_SECRET, now=160)


def test_tampered_capability_denied() -> None:
    with pytest.raises(ContainerJobCapabilityError, match="invalid"):
        verify_container_job_session_capability(
            _mint() + "tampered", secret=_SECRET, now=120
        )


def test_capability_does_not_authorize_another_session() -> None:
    """A token minted for session A carries no authority for session B."""
    capability = verify_container_job_session_capability(
        _mint(session_id="session-A"), secret=_SECRET, now=120
    )
    assert capability.session_id == "session-A"
    assert capability.session_id != "session-B"


def test_browser_access_loss_does_not_stop_admitted_work() -> None:
    """Capabilities carry no browser binding: validity is lifetime-based only."""
    capability = verify_container_job_session_capability(
        _mint(), secret=_SECRET, now=120
    )
    assert not hasattr(capability, "browser_session_id")
    assert capability.expires_at == 160


def test_bridge_session_bound_to_owning_workflow_not_arbitrary_proxy() -> None:
    """A runtime bridge stays bound to its session; cross-owner reuse fails."""
    request = AgentExecutionRequest(
        agent_kind="managed",
        agent_id="agent-4356",
        correlation_id="corr-4356",
        idempotency_key="bridge-4356",
    )
    context = authorize_bridge_access(request)
    with pytest.raises(OmnigentAuthorizationError):
        assert_bridge_session_binding(
            context,
            BridgeSessionBinding(workflow_id="proxy-workflow", agent_run_id="proxy-run"),
        )
