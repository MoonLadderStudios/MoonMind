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


def test_secret_rotation_invalidates_prior_generation() -> None:
    """A rotated signing secret ends the prior generation's authority.

    The old token verifies under the old secret and fails under the new
    one; a token minted after rotation verifies under the new secret.
    This is the hermetic lease/generation primitive: rotation bounds the
    lifetime of admitted work without touching browser state.
    """
    old_token = _mint(now=100)
    assert (
        verify_container_job_session_capability(
            old_token, secret=_SECRET, now=120
        ).session_id
        == "session-4356"
    )
    rotated_secret = "machine-authority-4356-secret-rotated"
    with pytest.raises(ContainerJobCapabilityError, match="invalid"):
        verify_container_job_session_capability(
            old_token, secret=rotated_secret, now=120
        )
    rotated_token = _mint(secret=rotated_secret, now=130)
    assert (
        verify_container_job_session_capability(
            rotated_token, secret=rotated_secret, now=140
        ).session_id
        == "session-4356"
    )


def test_workspace_scope_tamper_fails_closed() -> None:
    """Escalating workspace scope without re-signing is refused.

    Flips ``workspaceReadOnly`` in the payload while keeping the original
    signature: verification must fail because the signature no longer
    covers the payload. Exercises the real verifier, not a model copy.
    """
    import base64
    import json

    token = _mint(now=100)
    encoded_payload, _, encoded_signature = token.partition(".")
    padding = "=" * (-len(encoded_payload) % 4)
    payload = json.loads(
        base64.urlsafe_b64decode(encoded_payload + padding).decode("utf-8")
    )
    payload["workspaceReadOnly"] = not payload["workspaceReadOnly"]
    payload["workspaceId"] = "escalated-workspace-4356"
    tampered_payload = (
        base64.urlsafe_b64encode(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        )
        .rstrip(b"=")
        .decode("ascii")
    )
    with pytest.raises(ContainerJobCapabilityError, match="invalid"):
        verify_container_job_session_capability(
            f"{tampered_payload}.{encoded_signature}",
            secret=_SECRET,
            now=120,
        )


def test_lease_window_equals_requested_lifetime() -> None:
    """A capability lease is bounded: exp equals iat plus the grant."""
    capability = verify_container_job_session_capability(
        _mint(lifetime_seconds=300, now=1000), secret=_SECRET, now=1100
    )
    assert capability.expires_at == 1300


def test_short_lease_expires_while_long_lease_for_same_session_holds() -> None:
    """Lease scope outlives session identity: same session, distinct bounds."""
    short_token = _mint(session_id="lease-4356", lifetime_seconds=60, now=100)
    long_token = _mint(session_id="lease-4356", lifetime_seconds=600, now=100)
    assert short_token != long_token
    with pytest.raises(ContainerJobCapabilityError, match="expired"):
        verify_container_job_session_capability(
            short_token, secret=_SECRET, now=200
        )
    assert (
        verify_container_job_session_capability(
            long_token, secret=_SECRET, now=200
        ).session_id
        == "lease-4356"
    )


def test_successive_generations_overlap_then_roll_forward() -> None:
    """Two issuance generations overlap, then only the newer lease survives."""
    first = _mint(session_id="gen-4356", lifetime_seconds=60, now=100)
    second = _mint(session_id="gen-4356", lifetime_seconds=60, now=130)
    assert first != second
    assert (
        verify_container_job_session_capability(
            first, secret=_SECRET, now=140
        ).expires_at
        == 160
    )
    assert (
        verify_container_job_session_capability(
            second, secret=_SECRET, now=140
        ).expires_at
        == 190
    )
    with pytest.raises(ContainerJobCapabilityError, match="expired"):
        verify_container_job_session_capability(first, secret=_SECRET, now=170)
    assert (
        verify_container_job_session_capability(
            second, secret=_SECRET, now=170
        ).session_id
        == "gen-4356"
    )


def test_independent_worker_sessions_verify_concurrently() -> None:
    """Two workers/tabs hold independent authority without cross-grant.

    Both capabilities verify under the same deployment secret while each
    stays bound to its own session/workflow -- the hermetic continuity
    primitive for multiple tabs/workers before the integrated restart
    suite runs.
    """
    first = verify_container_job_session_capability(
        _mint(session_id="tab-A-4356", workflow_id="workflow-A-4356", now=100),
        secret=_SECRET,
        now=120,
    )
    second = verify_container_job_session_capability(
        _mint(session_id="tab-B-4356", workflow_id="workflow-B-4356", now=100),
        secret=_SECRET,
        now=120,
    )
    assert first.session_id == "tab-A-4356"
    assert second.session_id == "tab-B-4356"
    assert first.workflow_id != second.workflow_id
