"""Restricted-egress validation for generic Omnigent hosts."""

from __future__ import annotations

from typing import Any

from moonmind.omnigent.bridge_artifacts import OmnigentArtifactGateway
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.harness_platform.host_classes import LaunchPolicy
from moonmind.omnigent.host_services.docker_backend import DockerCommandBackend
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.security.egress import OMNIGENT_EGRESS_PROFILE, attest_docker_egress


class OmnigentEgressService:
    def __init__(
        self, *, backend: DockerCommandBackend, artifacts: OmnigentArtifactGateway
    ) -> None:
        self._backend = backend
        self._artifacts = artifacts

    async def attest(
        self,
        *,
        request: AgentExecutionRequest,
        launch_policy: LaunchPolicy,
    ) -> dict[str, Any]:
        expected = str(launch_policy.network.get("egressPolicyRef") or "")
        if expected != OMNIGENT_EGRESS_PROFILE.ref:
            raise HarnessPlatformError(
                "launch policy does not select the deployment Omnigent egress profile",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )

        async def runner(args):
            code, out, err = await self._backend.run(["docker", *args], check=False)
            return code, out.encode(), err.encode()

        try:
            attestation = await attest_docker_egress(
                runner=runner,
                profile=OMNIGENT_EGRESS_PROFILE,
                backend_ref="generic-omnigent-host",
            )
        except RuntimeError as exc:
            raise HarnessPlatformError(
                "restricted-egress backend attestation failed",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            ) from exc
        evidence = attestation.model_dump(by_alias=True, mode="json")
        evidence_ref = await self._artifacts.write_json(
            request=request,
            name="generic-host-egress-attestation.json",
            payload=evidence,
            link_type="evidence.host_egress",
        )
        return {
            **evidence,
            "attestationRef": evidence_ref,
            "cleanupRef": f"egress-cleanup:{attestation.profile_digest}",
        }


__all__ = ["OmnigentEgressService"]
