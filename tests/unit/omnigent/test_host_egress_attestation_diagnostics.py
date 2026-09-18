"""A refused host launch must name why the egress attestation failed.

The 2026-09-17 scheduled GitHub-issue runs failed with
``OMNIGENT_HOST_LAUNCH_FAILED: Admitted Omnigent execution-plan dispatch failed:
restricted-egress backend attestation failed``. The underlying check knows
exactly which invariant broke (unhealthy gateway, stale live config, wrong
attachment), but the service discarded that reason, so the only operator-visible
diagnosis required reading Temporal history. Keep the original reason in the
raised message.
"""

from __future__ import annotations

import pytest

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.host_services.egress import OmnigentEgressService
from moonmind.security.egress import OMNIGENT_EGRESS_PROFILE


class _LaunchPolicy:
    def __init__(self, egress_policy_ref: str) -> None:
        self.network = {"egressPolicyRef": egress_policy_ref}


class _Backend:
    async def run(self, args, check=False):  # noqa: ANN001, ARG002
        return 0, "", ""


class _Artifacts:
    async def write_json(self, **_kwargs):
        return "artifact://unused"


@pytest.mark.asyncio
async def test_attestation_failure_names_the_failing_invariant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reason = "restricted-egress gateway is not healthy"

    async def _fail(**_kwargs):
        raise RuntimeError(reason)

    monkeypatch.setattr(
        "moonmind.omnigent.host_services.egress.attest_docker_egress", _fail
    )
    service = OmnigentEgressService(backend=_Backend(), artifacts=_Artifacts())

    with pytest.raises(HarnessPlatformError) as excinfo:
        await service.attest(
            request=object(),
            launch_policy=_LaunchPolicy(OMNIGENT_EGRESS_PROFILE.ref),
        )

    assert excinfo.value.code == HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED
    assert reason in str(excinfo.value)
    assert excinfo.value.__cause__ is not None


@pytest.mark.asyncio
async def test_unexpected_profile_still_reports_the_selected_reference() -> None:
    service = OmnigentEgressService(backend=_Backend(), artifacts=_Artifacts())

    with pytest.raises(HarnessPlatformError) as excinfo:
        await service.attest(
            request=object(),
            launch_policy=_LaunchPolicy("some-other-profile@1"),
        )

    message = str(excinfo.value)
    assert excinfo.value.code == HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED
    assert "some-other-profile@1" in message
    assert OMNIGENT_EGRESS_PROFILE.ref in message
