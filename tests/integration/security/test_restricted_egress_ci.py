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

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from moonmind.schemas.container_job_models import (
    AuxiliaryOutcome,
    ContainerJobActivityRequest,
)
from moonmind.security.egress import (
    CONTROL_PLANE_NETWORK_REF,
    DEFAULT_EGRESS_PROFILE,
    EGRESS_CONFIG_DIGEST,
    EGRESS_FILE_DIGESTS,
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
from moonmind.workflows.temporal.container_job_backend import DockerContainerJobBackend

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]

JOB_ID = "container-job:00112233445566778899aabbccddeeff"


@pytest.fixture
def deployment_policy(tmp_path, monkeypatch):
    from moonmind.security import egress

    root = tmp_path / "deployment"
    source = root / "moonmind/security/egress.py"
    source.parent.mkdir(parents=True)
    shutil.copyfile(egress.__file__, source)
    policy = root / "docker/sandbox-egress-proxy"
    shutil.copytree(egress.EGRESS_POLICY_DIRECTORY, policy)
    (policy / "omnigent-provider-domains.txt").write_text(
        "openrouter.ai\napi.future-provider.com\n"
    )
    spec = importlib.util.spec_from_file_location("deployment_egress", source)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    live = tmp_path / "loaded"
    result = subprocess.run(
        ["sh", str(policy / "policy.sh"), "prepare", str(policy), str(live)],
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    return module, policy, live


@pytest.mark.parametrize(
    "provider_data", [None, "", "openrouter.ai\napi.future-provider.com\n"]
)
def test_image_policy_survives_old_checkout_and_restart(
    tmp_path, monkeypatch, provider_data
):
    from moonmind.security import egress

    image = tmp_path / "image"
    bundled = image / "opt/moonmind-egress"
    shutil.copytree(egress.EGRESS_POLICY_DIRECTORY, bundled)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "squid.conf").write_text("old checkout config\n")
    if provider_data is not None:
        (checkout / "omnigent-provider-domains.txt").write_text(provider_data)
    monkeypatch.setenv("MOONMIND_EGRESS_POLICY_DIRECTORY", str(checkout))
    for attempt in range(2):
        source = image / f"release-{attempt}/moonmind/security/egress.py"
        source.parent.mkdir(parents=True)
        shutil.copyfile(egress.__file__, source)
        spec = importlib.util.spec_from_file_location(f"image_egress_{attempt}", source)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, module)
        spec.loader.exec_module(module)
        live = tmp_path / f"loaded-{attempt}"
        result = subprocess.run(
            ["sh", str(bundled / "policy.sh"), "prepare", str(checkout), str(live)],
            capture_output=True,
        )
        assert result.returncode == 0, result.stderr
        assert (live / "squid.conf").read_bytes() == (
            bundled / "squid.conf"
        ).read_bytes()
        assert (live / "omnigent-provider-domains.txt").read_text() == (
            provider_data or ""
        )
        expected = set((provider_data or "").splitlines())
        assert {d.dns_name for d in module.OMNIGENT_EGRESS_PROFILE.destinations} - {
            d.dns_name for d in module.DEFAULT_EGRESS_PROFILE.destinations
        } == expected
        assert module.EGRESS_FILE_DIGESTS["omnigent-provider-domains.txt"] == (
            "sha256:" + hashlib.sha256((provider_data or "").encode()).hexdigest()
        )
    assert not (checkout / "policy.sh").exists()
    assert (checkout / "squid.conf").read_text() == "old checkout config\n"


@pytest.mark.parametrize(
    "selection", ["omitted", "blank", "explicit-default", "custom"]
)
def test_compose_policy_selection_reaches_gateway_and_consumers(
    tmp_path, monkeypatch, selection
):
    from moonmind.security import egress

    root = Path(__file__).resolve().parents[3]
    project = tmp_path / "project"
    project.mkdir()
    shutil.copyfile(root / "docker-compose.yaml", project / "docker-compose.yaml")
    default_policy = project / "docker/sandbox-egress-proxy"
    shutil.copytree(root / "docker/sandbox-egress-proxy", default_policy)
    policy = default_policy
    if selection == "custom":
        policy = tmp_path / "custom policy"
        policy.mkdir()
        (policy / "omnigent-provider-domains.txt").write_text("openrouter.ai\n")
    value = "" if selection == "blank" else str(policy)
    (project / ".env").write_text(
        "" if selection == "omitted" else f"MOONMIND_EGRESS_POLICY_DIRECTORY={value}\n"
    )
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--project-name",
            "moonmind-test-egress-policy",
            "--project-directory",
            str(project),
            "--env-file",
            str(project / ".env"),
            "-f",
            str(project / "docker-compose.yaml"),
            "config",
            "--format",
            "json",
        ],
        env={key: os.environ[key] for key in ("PATH", "HOME") if key in os.environ},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    services = json.loads(result.stdout)["services"]
    consumers = [
        "api",
        "omnigent-runtime-bootstrap",
        "init-db",
        "sandbox-egress-proxy",
        "temporal-worker-workflow",
        "temporal-worker-artifacts",
        "temporal-worker-llm",
        "temporal-worker-sandbox",
        "temporal-worker-agent-runtime",
        "temporal-worker-deployment-control",
        "temporal-worker-integrations",
    ]
    target = (
        "/app/docker/sandbox-egress-proxy"
        if selection in ("omitted", "blank")
        else str(policy)
    )
    for name in consumers:
        service = services[name]
        assert (
            service.get("environment", {}).get("MOONMIND_EGRESS_POLICY_DIRECTORY")
            == target
        ), name
        mount = next(item for item in service["volumes"] if item["target"] == target)
        assert mount["type"] == "bind"
        assert mount["source"] == str(policy)
        assert mount["read_only"] is True

    monkeypatch.setenv("MOONMIND_EGRESS_POLICY_DIRECTORY", str(policy))
    spec = importlib.util.spec_from_file_location(
        "compose_policy_egress", egress.__file__
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    for attempt in range(2):
        live = tmp_path / f"live-{attempt}"
        prepared = subprocess.run(
            [
                "sh",
                str(root / "docker/sandbox-egress-proxy/policy.sh"),
                "prepare",
                "",
                str(live),
            ],
            capture_output=True,
        )
        assert prepared.returncode == 0, prepared.stderr
        assert module.EGRESS_FILE_DIGESTS["omnigent-provider-domains.txt"] == (
            "sha256:"
            + hashlib.sha256(
                (live / "omnigent-provider-domains.txt").read_bytes()
            ).hexdigest()
        )
        extra = {d.dns_name for d in module.OMNIGENT_EGRESS_PROFILE.destinations} - {
            d.dns_name for d in module.DEFAULT_EGRESS_PROFILE.destinations
        }
        assert extra == ({"openrouter.ai"} if selection == "custom" else set())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tamper",
    [
        None,
        "mounted-main",
        "mounted-extra",
        "loaded-main",
        "loaded-extra",
        "missing-extra",
    ],
)
async def test_deployment_data_drives_runtime_and_proxy_attestation(
    deployment_policy, tamper
):
    module, policy, live = deployment_policy
    destinations = module.OMNIGENT_EGRESS_PROFILE.destinations
    assert {d.dns_name for d in destinations} - {
        d.dns_name for d in module.DEFAULT_EGRESS_PROFILE.destinations
    } == {"openrouter.ai", "api.future-provider.com"}
    assert {d.dns_name for d in destinations} >= set(
        (live / "omnigent-provider-domains.txt").read_text().splitlines()
    )
    assert all(d.ports == (443,) for d in destinations)
    if tamper:
        directory = policy if tamper.startswith("mounted") else live
        target = directory / (
            "squid.conf" if tamper.endswith("main") else "omnigent-provider-domains.txt"
        )
        target.chmod(0o644)
        if tamper == "missing-extra":
            target.unlink()
        else:
            target.write_text(target.read_text() + "other-provider.com\n")

    async def runner(args):
        if args[0] == "network":
            return 0, b'{"Internal":true,"EnableIPv6":false}', b""
        if args[0] == "inspect":
            return (
                0,
                json.dumps(
                    {
                        "labels": {
                            "moonmind.egress.enforcer": module.ENFORCER_IMPLEMENTATION
                        },
                        "networks": dict.fromkeys(
                            module._EXPECTED_GATEWAY_NETWORKS, {}
                        ),
                        "health": "healthy",
                        "image": "sha256:gateway-image",
                    }
                ).encode(),
                b"",
            )
        assert args[2] == "sha256sum"
        rows = []
        for name in args[3:]:
            directory = policy if name.startswith("/etc/squid/") else live
            path = directory / Path(name).name
            if not path.exists():
                return 1, b"", b"missing policy"
            rows.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {name}\n")
        return 0, "".join(rows).encode(), b""

    if tamper:
        with pytest.raises(RuntimeError, match="config"):
            await module.attest_docker_egress(
                runner=runner,
                profile=module.OMNIGENT_EGRESS_PROFILE,
                backend_ref="test",
            )
    else:
        evidence = await module.attest_docker_egress(
            runner=runner, profile=module.OMNIGENT_EGRESS_PROFILE, backend_ref="test"
        )
        assert evidence.profile_digest == module.OMNIGENT_EGRESS_PROFILE.digest
        assert evidence.config_digest != EGRESS_CONFIG_DIGEST


@pytest.mark.parametrize(
    "value",
    [
        "api",
        ".provider.com",
        "*.provider.com",
        "127.0.0.1",
        "169.254.169.254",
        "::1",
        "provider.internal",
        "provider.com:443",
        "provider.com/",
        "provider.com\nhttp_access allow all",
        "provider.123",
        "provider.com\tother.com",
    ],
)
def test_proxy_startup_rejects_invalid_deployment_destinations(
    deployment_policy, value
):
    module, policy, live = deployment_policy
    (policy / "omnigent-provider-domains.txt").write_text(value + "\n")
    with pytest.raises(ValueError):
        module.load_omnigent_provider_destinations(
            policy / "omnigent-provider-domains.txt"
        )
    result = subprocess.run(
        [
            "sh",
            str(policy / "policy.sh"),
            "prepare",
            str(policy),
            str(live.parent / "invalid"),
        ],
        capture_output=True,
    )
    assert result.returncode != 0


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
                CONTROL_PLANE_NETWORK_REF: {},
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

    backend = DockerContainerJobBackend(workspace_root=tmp_path, command_runner=runner)
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
            return (
                0,
                "".join(
                    f"{EGRESS_FILE_DIGESTS[path.rsplit('/', 1)[-1]].removeprefix('sha256:')}  {path}\n"
                    for path in args[3:]
                ).encode(),
                b"",
            )
        if args[:3] == ("inspect", "--format", "{{json .Config.Labels}}"):
            return 1, b"", b"no such container"
        return 0, b"", b""

    backend = DockerContainerJobBackend(workspace_root=tmp_path, command_runner=runner)
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

    backend = DockerContainerJobBackend(workspace_root=tmp_path, command_runner=runner)
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
            return (
                0,
                json.dumps({EGRESS_NETWORK_REF: {"IPAddress": "172.31.0.9"}}).encode(),
                b"",
            )
        if args[:3] == ("exec", DEFAULT_EGRESS_PROFILE.gateway_ref, "cat"):
            return 0, access_log, b""
        raise AssertionError(args)

    backend = DockerContainerJobBackend(workspace_root=tmp_path, command_runner=runner)
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
            return (
                0,
                "".join(
                    f"{EGRESS_FILE_DIGESTS[path.rsplit('/', 1)[-1]].removeprefix('sha256:')}  {path}\n"
                    for path in args[3:]
                ).encode(),
                b"",
            )
        if args[:3] == ("exec", DEFAULT_EGRESS_PROFILE.gateway_ref, "cat"):
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
                "imageRef": "sha256:" + "a" * 64,
                "image": "sha256:" + "a" * 64,
            }
            return 0, json.dumps(payload).encode(), b""
        if args[:3] == ("image", "inspect", "--format"):
            return 0, b'"amd64"', b""
        if args[:2] == ("info", "--format"):
            # MoonLadderStudios/MoonMind#3881: the shared machine budget is
            # probed from the memory total *and* the CPU count.
            return 0, f"{8 * 1024**3}\t8".encode(), b""
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
    assert attestation["workloadImageRef"] == "sha256:" + "a" * 64
    assert attestation["architecture"] == "amd64"
    assert lifecycle["cleanupResult"] == "succeeded"
    assert lifecycle["launchAttestationRef"] == started.diagnostics_ref

    # Tampering with the resolved body after cleanup is detected by the digest.
    tampered = json.dumps({**lifecycle, "cleanupResult": "failed"}).encode()
    with pytest.raises(EgressEvidenceDigestError):
        parse_and_verify_conformance_evidence(tampered, location="egress-lifecycle")
