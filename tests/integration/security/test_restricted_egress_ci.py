"""Hermetic restricted-egress boundary coverage for required CI (#3516).

The live conformance suite in ``test_restricted_egress_live.py`` proves the
production behavior against a real Docker daemon and Squid gateway, but it is
opt-in (``MOONMIND_RUN_EGRESS_CONFORMANCE=1``) and therefore never runs in the
required ``integration_ci`` selection. Because this change modifies the Docker
launch, cleanup, and runtime-diagnostics boundaries, this suite exercises those
exact production code paths through the real backend using an in-process Docker
command runner (no daemon, no credentials, no Compose), so the highest-risk
egress seams are covered on every pull request. The live suite remains
supplemental for full network conformance.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from moonmind.schemas.container_job_models import (
    AuxiliaryOutcome,
    ContainerJobActivityRequest,
)
from moonmind.security.egress import (
    DEFAULT_EGRESS_PROFILE,
    EGRESS_CONFIG_DIGEST,
    EGRESS_NETWORK_REF,
    EGRESS_PROFILE_SET_DIGEST,
    ENFORCER_IMPLEMENTATION,
    OMNIGENT_EGRESS_NETWORK_REF,
    PROXY_URL,
)
from moonmind.security.egress_conformance_evidence import (
    EgressEvidenceDigestError,
    parse_and_verify_conformance_evidence,
)
from moonmind.workflows.temporal.container_job_backend import (
    DockerContainerJobBackend,
)

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]

JOB_ID = "container-job:00112233445566778899aabbccddeeff"


def _request(tmp_path, **spec_overrides) -> ContainerJobActivityRequest:
    (tmp_path / "art_workspace").mkdir(exist_ok=True)
    spec = {
        "image": "python:3.13",
        "workspaceRef": {"kind": "sandbox", "workspaceId": "art_workspace"},
        "command": ["python", "-V"],
        "resources": {"cpuMillis": 1000, "memoryMiB": 512},
        "timeoutSeconds": 60,
    }
    spec.update(spec_overrides)
    payload = {
        "jobId": JOB_ID,
        "ownershipToken": f"{JOB_ID}:v1",
        "request": {
            "idempotencyKey": "issue-3516-ci",
            "source": {"source": "workflow", "workflowId": "mm:3516"},
            "spec": spec,
        },
        "resolvedWorkspaceRef": str(tmp_path / "art_workspace"),
        "resolvedImageRef": "sha256:" + "a" * 64,
    }
    return ContainerJobActivityRequest.model_validate(payload)


def _healthy_gateway_inspect() -> bytes:
    return json.dumps(
        {
            "labels": {
                "moonmind.egress.profile-set-digest": EGRESS_PROFILE_SET_DIGEST,
                "moonmind.egress.enforcer": ENFORCER_IMPLEMENTATION,
                "moonmind.egress.config-digest": EGRESS_CONFIG_DIGEST,
            },
            "networks": {
                EGRESS_NETWORK_REF: {},
                "moonmind_sandbox-egress-network": {},
                OMNIGENT_EGRESS_NETWORK_REF: {},
                "local-network": {},
            },
            "image": "sha256:gateway-image",
            "health": "healthy",
        }
    ).encode()


@pytest.mark.asyncio
async def test_bridge_launch_is_gated_by_network_attestation(tmp_path) -> None:
    """A non-internal restricted network fails closed before any create call."""

    commands: list[tuple[str, ...]] = []

    async def runner(args):
        args = tuple(args)
        commands.append(args)
        if args[:2] == ("network", "inspect"):
            return 0, b'{"Internal":false,"EnableIPv6":false}', b""
        return 0, b"", b""

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path, command_runner=runner
    )
    with pytest.raises(RuntimeError, match="not internal"):
        await backend.create_container(_request(tmp_path, networkMode="bridge"))
    assert not any(command[0] == "create" for command in commands)


@pytest.mark.asyncio
async def test_attested_bridge_launch_uses_restricted_network_and_proxy(
    tmp_path,
) -> None:
    commands: list[tuple[str, ...]] = []

    async def runner(args):
        args = tuple(args)
        commands.append(args)
        if args[:2] == ("network", "inspect"):
            return 0, b'{"Internal":true,"EnableIPv6":false}', b""
        if args[0] == "inspect" and "NetworkSettings.Networks" in args[2]:
            return 0, _healthy_gateway_inspect(), b""
        if args[:2] == ("exec", DEFAULT_EGRESS_PROFILE.gateway_ref):
            return 0, (
                EGRESS_CONFIG_DIGEST.removeprefix("sha256:")
                + "  /etc/squid/squid.conf\n"
            ).encode(), b""
        if args[:3] == ("inspect", "--format", "{{json .Config.Labels}}"):
            return 1, b"", b"no such container"
        return 0, b"", b""

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path, command_runner=runner
    )
    await backend.create_container(_request(tmp_path, networkMode="bridge"))

    create = next(command for command in commands if command[0] == "create")
    assert create[create.index("--network") + 1] == EGRESS_NETWORK_REF
    assert f"HTTPS_PROXY={PROXY_URL}" in create
    assert "NO_PROXY=" in create


@pytest.mark.asyncio
async def test_runtime_evidence_rejects_secondary_network(tmp_path) -> None:
    async def runner(args):
        if args[:3] == ("inspect", "--format", "{{json .NetworkSettings.Networks}}"):
            payload = {
                EGRESS_NETWORK_REF: {"IPAddress": "172.31.0.9"},
                "attacker-bridge": {"IPAddress": "10.0.0.9"},
            }
            return 0, json.dumps(payload).encode(), b""
        raise AssertionError(args)

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path, command_runner=runner
    )
    request = _request(tmp_path, networkMode="bridge")
    request.container_ref = "owned-workload"
    with pytest.raises(RuntimeError, match="sole approved network"):
        await backend._runtime_egress_evidence(request)


@pytest.mark.asyncio
async def test_runtime_evidence_scopes_denials_and_counts_full(tmp_path) -> None:
    start = datetime(2026, 8, 4, 0, 0, 30, tzinfo=UTC)
    finish = datetime(2026, 8, 4, 0, 5, 0, tzinfo=UTC)
    prior = start.timestamp() - 120
    inside = start.timestamp() + 5
    lines = [
        f"{prior} 2 172.31.0.9 TCP_DENIED/403 0 CONNECT prior.invalid:443/ "
        "- HIER_NONE/- text/html"
    ]
    lines += [
        f"{inside} 2 172.31.0.9 TCP_DENIED/403 0 CONNECT blocked{i}.invalid:443/ "
        "- HIER_NONE/- text/html"
        for i in range(22)
    ]
    access_log = ("\n".join(lines) + "\n").encode()

    async def runner(args):
        if args[:3] == ("inspect", "--format", "{{json .NetworkSettings.Networks}}"):
            return 0, json.dumps(
                {EGRESS_NETWORK_REF: {"IPAddress": "172.31.0.9"}}
            ).encode(), b""
        if args[:3] == ("exec", DEFAULT_EGRESS_PROFILE.gateway_ref, "tail"):
            return 0, access_log, b""
        raise AssertionError(args)

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path, command_runner=runner
    )
    request = _request(tmp_path, networkMode="bridge")
    request.container_ref = "owned-workload"
    request.started_at = start
    request.finished_at = finish

    evidence = await backend._runtime_egress_evidence(request)

    assert evidence is not None
    assert evidence["deniedConnectionCount"] == 22
    assert len(evidence["denialDiagnostics"]) == 20
    assert all("prior.invalid" not in d for d in evidence["denialDiagnostics"])


@pytest.mark.asyncio
async def test_cleanup_publishes_terminal_lifecycle_evidence(tmp_path) -> None:
    published: list[tuple[str, dict]] = []

    async def runner(args):
        if args[:2] == ("ps", "-aq"):
            return 0, b"", b""
        raise AssertionError(args)

    async def publish(_request, name, data):
        published.append((name, json.loads(data)))
        return f"artifact:{name}"

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path, command_runner=runner, evidence_publisher=publish
    )
    request = _request(tmp_path, networkMode="bridge")
    request.container_ref = "owned-workload"
    request.publication = AuxiliaryOutcome(
        state="succeeded", diagnosticsRef="artifact:runtime-diagnostics"
    )
    request.egress_attestation_ref = "artifact:launch-attestation"

    result = await backend.cleanup(request)

    assert result.cleanup_succeeded is True
    assert published[0][1]["cleanupResult"] == "succeeded"
    assert published[0][1]["launchAttestationRef"] == "artifact:launch-attestation"


@pytest.mark.asyncio
async def test_launch_and_lifecycle_evidence_is_digest_bound_and_resolvable(
    tmp_path,
) -> None:
    """Per-row egress evidence survives cleanup as tamper-evident, secret-clean.

    MoonLadderStudios/MoonMind#3625. The launch-attestation and lifecycle
    artifacts published through the real Container Job backend must remain
    independently resolvable and digest-checkable after the workload is gone.
    """

    published: dict[str, bytes] = {}

    async def runner(args):
        args = tuple(args)
        if args[:2] == ("network", "inspect"):
            return 0, b'{"Internal":true,"EnableIPv6":false}', b""
        if args[0] == "inspect" and args[-1] == DEFAULT_EGRESS_PROFILE.gateway_ref:
            return 0, _healthy_gateway_inspect(), b""
        if args[:3] == (
            "exec",
            DEFAULT_EGRESS_PROFILE.gateway_ref,
            "sha256sum",
        ):
            return 0, (
                EGRESS_CONFIG_DIGEST.removeprefix("sha256:")
                + "  /etc/squid/squid.conf\n"
            ).encode(), b""
        if args[:3] == ("exec", DEFAULT_EGRESS_PROFILE.gateway_ref, "tail"):
            return 0, b"", b""
        if args[:3] == ("inspect", "--format", "{{json .Config.Labels}}"):
            return 1, b"", b"no such container"
        if args[0] == "inspect" and "NetworkSettings.Networks" in args[2]:
            launch = json.loads(published[f"{JOB_ID}-egress-attestation.json"])
            payload = {
                "labels": {
                    "moonmind.egress.profile": DEFAULT_EGRESS_PROFILE.ref,
                    "moonmind.egress.profile_digest": DEFAULT_EGRESS_PROFILE.digest,
                    "moonmind.egress.applied_rule_digest": launch["attestation"][
                        "appliedRuleDigest"
                    ],
                },
                "networks": {
                    EGRESS_NETWORK_REF: {
                        "NetworkID": "ci-restricted-network-id",
                        "EndpointID": "ci-container-job-endpoint-id",
                        "IPAddress": "172.31.0.9",
                    }
                },
                "image": "sha256:" + "a" * 64,
            }
            return 0, json.dumps(payload).encode(), b""
        if args[:3] == ("image", "inspect", "--format"):
            return 0, b'"amd64"', b""
        if args[:2] == ("info", "--format"):
            return 0, str(8 * 1024**3).encode(), b""
        if args[:2] == ("ps", "--all"):
            return 0, b"", b""
        if args[:2] == ("ps", "-aq"):
            return 0, b"", b""
        return 0, b"", b""

    async def publish(_request, name, data):
        published[name] = data
        return f"artifact:{name}"

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path, command_runner=runner, evidence_publisher=publish
    )
    request = _request(tmp_path, networkMode="bridge")
    created = await backend.create_container(request)
    request.container_ref = created.container_ref
    request.egress_attestation_ref = created.diagnostics_ref
    started = await backend.start_container(request)
    request.egress_attestation_ref = started.diagnostics_ref
    request.publication = AuxiliaryOutcome(
        state="succeeded", diagnosticsRef="artifact:runtime-diagnostics"
    )

    await backend.cleanup(request)

    attestation_name = f"{JOB_ID}-egress-attestation.json"
    lifecycle_name = f"{JOB_ID}-egress-lifecycle.json"
    assert attestation_name in published
    assert lifecycle_name in published

    # A resolver reads each artifact back after cleanup and re-verifies it.
    attestation = parse_and_verify_conformance_evidence(
        published[attestation_name], location="egress-attestation"
    )
    lifecycle = parse_and_verify_conformance_evidence(
        published[lifecycle_name], location="egress-lifecycle"
    )
    assert attestation["attestation"]["profileRef"] == DEFAULT_EGRESS_PROFILE.ref
    assert attestation["evidenceStage"] == "running"
    assert attestation["networkIdentity"] == "ci-restricted-network-id"
    assert attestation["endpointIdentity"] == "ci-container-job-endpoint-id"
    assert attestation["workloadImageDigest"] == "sha256:" + "a" * 64
    assert attestation["architecture"] == "amd64"
    assert lifecycle["cleanupResult"] == "succeeded"
    assert lifecycle["launchAttestationRef"] == started.diagnostics_ref

    # Tampering with the resolved body after cleanup is detected by the digest.
    tampered = json.dumps(
        {**lifecycle, "cleanupResult": "failed"}
    ).encode()
    with pytest.raises(EgressEvidenceDigestError):
        parse_and_verify_conformance_evidence(tampered, location="egress-lifecycle")
