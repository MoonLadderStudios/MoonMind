"""MoonLadderStudios/MoonMind#4356 R5: machine-authority lease bounds (integration).

Hermetic lease/generation coverage under the ``integration_ci`` resource
boundary using the real scoped-capability verifier with synthetic secrets
only: leases carry explicit bounded windows, short leases expire while a
longer lease for the same session still holds, and a rotated signing
secret ends the prior generation. Multi-worker deployment continuity on
the integrated candidate remains cohort-owned.
"""

from __future__ import annotations

import pytest

from moonmind.schemas.container_job_models import OwnerIdentity
from moonmind.security.container_job_capabilities import (
    ContainerJobCapabilityError,
    mint_container_job_session_capability,
    verify_container_job_session_capability,
)

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]

_SECRET = "machine-authority-4356-integration-secret"


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


def test_lease_window_is_bounded_per_grant() -> None:
    capability = verify_container_job_session_capability(
        _mint(lifetime_seconds=300, now=1000), secret=_SECRET, now=1100
    )
    assert capability.expires_at == 1300


def test_short_lease_expires_while_long_lease_holds() -> None:
    short_token = _mint(session_id="lease-4356", lifetime_seconds=60, now=100)
    long_token = _mint(session_id="lease-4356", lifetime_seconds=600, now=100)
    with pytest.raises(ContainerJobCapabilityError, match="expired"):
        verify_container_job_session_capability(short_token, secret=_SECRET, now=200)
    assert (
        verify_container_job_session_capability(long_token, secret=_SECRET, now=200)
    ).session_id == "lease-4356"


def test_rotated_secret_ends_prior_generation() -> None:
    old_token = _mint(now=100)
    rotated_secret = "machine-authority-4356-integration-secret-rotated"
    with pytest.raises(ContainerJobCapabilityError, match="invalid"):
        verify_container_job_session_capability(old_token, secret=rotated_secret, now=120)
    rotated_token = _mint(secret=rotated_secret, now=130)
    assert (
        verify_container_job_session_capability(
            rotated_token, secret=rotated_secret, now=140
        ).session_id
        == "session-4356"
    )
