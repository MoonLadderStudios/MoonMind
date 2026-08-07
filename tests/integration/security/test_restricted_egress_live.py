"""Production-shaped restricted-egress network conformance for #3516.

Set ``MOONMIND_RUN_EGRESS_CONFORMANCE=1`` after starting the normal Compose
deployment. The suite creates only short-lived, labelled probe containers and
does not create or remove deployment networks or gateways.
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from moonmind.omnigent.oauth_host_runtime import OmnigentOAuthHostRuntime
from moonmind.security.egress import (
    DEFAULT_EGRESS_PROFILE,
    EGRESS_NETWORK_REF,
    OMNIGENT_EGRESS_PROFILE,
    PROXY_URL,
    attest_docker_egress,
)
from moonmind.security.egress_conformance_evidence import (
    parse_and_verify_conformance_evidence,
)
from moonmind.schemas.container_job_models import (
    AuxiliaryOutcome,
    ContainerJobActivityRequest,
    ContainerJobWorkflowInput,
)
from moonmind.workflows.temporal.container_job_backend import (
    DockerContainerJobBackend,
)
from moonmind.workloads.docker_launcher import DockerWorkloadLauncher

pytestmark = [pytest.mark.integration]


def _docker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=45, check=False
    )


@pytest.fixture(scope="module", autouse=True)
def require_live_conformance() -> None:
    if os.getenv("MOONMIND_RUN_EGRESS_CONFORMANCE") != "1":
        pytest.skip("set MOONMIND_RUN_EGRESS_CONFORMANCE=1 for live Docker proof")
    if _docker("version", "--format", "{{.Server.Version}}").returncode:
        pytest.fail("Docker daemon required for restricted-egress conformance")


@pytest.fixture(scope="module")
def rebound_allowed_name() -> None:
    """Shadow an approved name with prohibited internal DNS answers."""

    names = [f"moonmind-test-egress-dns-{uuid.uuid4().hex[:10]}" for _ in range(2)]
    try:
        for name in names:
            result = _docker(
                "run",
                "-d",
                "--name",
                name,
                "--label",
                "moonmind.test.scope=MoonLadderStudios/MoonMind#3516",
                "--network",
                EGRESS_NETWORK_REF,
                "--network-alias",
                "api.github.com",
                "alpine:3.21",
                "sleep",
                "120",
            )
            assert result.returncode == 0, result.stderr[-1000:]
        yield
    finally:
        for name in names:
            _docker("rm", "--force", name)


def _probe(
    url: str,
    *,
    proxy: bool = True,
    environment: tuple[str, ...] = (),
    curl_args: tuple[str, ...] = (),
    docker_args: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    args = [
        "run",
        "--rm",
        "--name",
        f"moonmind-test-egress-{uuid.uuid4().hex[:10]}",
        "--label",
        "moonmind.test.scope=MoonLadderStudios/MoonMind#3516",
        "--network",
        EGRESS_NETWORK_REF,
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
    ]
    args += list(docker_args)
    if proxy:
        args += [
            "--env",
            f"HTTP_PROXY={PROXY_URL}",
            "--env",
            f"HTTPS_PROXY={PROXY_URL}",
            "--env",
            f"http_proxy={PROXY_URL}",
            "--env",
            f"https_proxy={PROXY_URL}",
            "--env",
            "NO_PROXY=",
            "--env",
            "no_proxy=",
        ]
    for value in environment:
        args += ["--env", value]
    args += [
        "curlimages/curl:8.12.1",
        "-sS",
        "--max-time",
        "12",
        *curl_args,
        url,
    ]
    return _docker(*args)


async def _live_runner(args):
    result = _docker(*args)
    return result.returncode, result.stdout.encode(), result.stderr.encode()


def _live_backend_request(workspace: Path, *, command: list[str]):
    job_id = f"container-job:{uuid.uuid4().hex}"
    return ContainerJobActivityRequest.model_validate(
        {
            "jobId": job_id,
            "ownershipToken": f"{job_id}:v1",
            "request": {
                "idempotencyKey": f"egress-live:{uuid.uuid4().hex}",
                "source": {"source": "workflow", "workflowId": "egress-live"},
                "spec": {
                    "image": "alpine:3.21",
                    "workspaceRef": {"kind": "external_state", "artifactRef": "probe"},
                    "command": command,
                    "networkMode": "bridge",
                    "resources": {"cpuMillis": 100, "memoryMiB": 128, "pids": 32},
                    "timeoutSeconds": 30,
                },
            },
            "resolvedWorkspaceRef": str(workspace),
            "resolvedImageRef": "alpine:3.21",
        }
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://api.github.com/",
        "https://github.com/",
        "https://api.openai.com/",
        "https://storage.googleapis.com/",
    ],
)
def test_approved_provider_source_artifact_and_retrieval_endpoints_connect(
    url: str,
) -> None:
    # HTTP authorization responses are acceptable: this probe proves the TLS
    # destination is reachable without putting provider credentials in tests.
    result = _probe(url, curl_args=("--output", "/dev/null"))
    assert result.returncode == 0, result.stderr[-1000:]


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/",
        "https://0.0.0.1/",
        "https://10.0.0.1/",
        "https://100.64.0.1/",
        "https://169.254.169.254/",
        "https://172.17.0.1/",
        "https://192.168.0.1/",
        "https://224.0.0.1/",
        "https://127.0.0.1/",
        "https://[::1]/",
        "https://[fc00::1]/",
        "https://[fe80::1]/",
        "https://[ff02::1]/",
        "https://[::ffff:127.0.0.1]/",
        "https://host.docker.internal/",
        "https://temporal-internal/",
    ],
)
def test_forbidden_destinations_fail_through_gateway(url: str) -> None:
    docker_args = (
        ("--add-host", "host.docker.internal:host-gateway")
        if "host.docker.internal" in url
        else ()
    )
    assert _probe(url, docker_args=docker_args).returncode != 0


def test_rebound_and_mixed_internal_answers_for_allowed_name_are_denied(
    rebound_allowed_name: None,
) -> None:
    assert _probe("https://api.github.com/").returncode != 0


def test_direct_route_is_absent_even_without_proxy_environment() -> None:
    assert _probe("https://api.github.com/", proxy=False).returncode != 0


@pytest.mark.parametrize("bypass", ["NO_PROXY=*", "no_proxy=github.com"])
def test_proxy_bypass_environment_cannot_restore_a_direct_route(bypass: str) -> None:
    assert _probe("https://github.com/", environment=(bypass,)).returncode != 0


@pytest.mark.parametrize("method", ["TRACE", "OPTIONS"])
def test_alternate_proxy_methods_are_rejected(method: str) -> None:
    assert _probe(
        "http://github.com/", curl_args=("--request", method)
    ).returncode != 0


def test_connect_tunnel_to_unapproved_authority_is_rejected() -> None:
    # HTTPS through an HTTP proxy uses CONNECT. Verbose output is bounded to the
    # assertion failure and proves the gateway, rather than DNS or the client,
    # rejected the tunnel authority.
    result = _probe(
        "https://example.com/",
        curl_args=("--verbose", "--output", "/dev/null"),
    )
    assert result.returncode != 0
    assert "CONNECT tunnel failed" in result.stderr or "403" in result.stderr


def test_redirect_to_an_unapproved_destination_is_rejected() -> None:
    # Google's reviewed redirect endpoint points at a destination outside the
    # profile. Curl follows it, proving policy is re-applied to each hop.
    result = _probe(
        "https://www.google.com/url?q=https%3A%2F%2Fexample.com%2F",
        curl_args=("--location", "--fail", "--output", "/dev/null"),
    )
    assert result.returncode != 0


def test_workload_has_no_docker_socket_for_child_container_bypass() -> None:
    result = _docker(
        "run",
        "--rm",
        "--network",
        EGRESS_NETWORK_REF,
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "alpine:3.21",
        "sh",
        "-c",
        "test ! -S /var/run/docker.sock",
    )
    assert result.returncode == 0, result.stderr[-1000:]


def test_probe_runtime_is_unprivileged_without_route_or_device_authority() -> None:
    result = _docker(
        "run",
        "--rm",
        "--network",
        EGRESS_NETWORK_REF,
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "alpine:3.21",
        "sh",
        "-c",
        "test \"$(sed -n 's/^CapEff:[[:space:]]*//p' /proc/self/status)\" = "
        "\"0000000000000000\"; test ! -e /dev/net/tun; "
        "ip route add 198.51.100.0/24 via 127.0.0.1 >/dev/null 2>&1 && exit 1; "
        "exit 0",
    )
    assert result.returncode == 0, result.stderr[-1000:]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("networkMode", "host"),
        ("network", "attacker-network"),
        ("privileged", True),
        ("capAdd", ["NET_ADMIN"]),
        ("devices", ["/dev/net/tun"]),
        ("extraHosts", ["metadata.internal:169.254.169.254"]),
        ("routes", ["0.0.0.0/0 via 172.17.0.1"]),
        ("secondaryNetworks", ["bridge"]),
        ("helperLaunch", {"networkMode": "host"}),
    ],
)
def test_trusted_launch_contract_rejects_caller_bypass_authority(
    field: str, value: object
) -> None:
    spec = {
        "image": "curlimages/curl:8.12.1",
        "workspaceRef": {"kind": "external_state", "artifactRef": "probe"},
        "command": ["--version"],
        "networkMode": "bridge",
        "resources": {"cpuMillis": 100, "memoryMiB": 128, "pids": 32},
        "timeoutSeconds": 30,
        field: value,
    }
    with pytest.raises(ValueError):
        ContainerJobWorkflowInput.model_validate(
            {
                "jobId": f"container-job:{uuid.uuid4().hex}",
                "request": {
                    "idempotencyKey": f"egress-conformance:{field}",
                    "source": {"source": "workflow", "workflowId": "egress-live"},
                    "spec": spec,
                },
            }
        )


@pytest.mark.parametrize(
    "bypass_args",
    [
        ["--network", "host"],
        ["--network", EGRESS_NETWORK_REF, "--network", "bridge"],
        ["--privileged"],
        ["--cap-add", "NET_ADMIN"],
        ["--device", "/dev/net/tun"],
        ["--add-host", "metadata.internal:host-gateway"],
        ["--sysctl", "net.ipv4.ip_forward=1"],
    ],
)
def test_final_trusted_docker_boundary_rejects_bypass_flags(
    bypass_args: list[str],
) -> None:
    with pytest.raises(RuntimeError):
        DockerContainerJobBackend._reject_forbidden_launch_args(
            ["create", *bypass_args], expected_network=EGRESS_NETWORK_REF
        )


@pytest.mark.asyncio
async def test_live_gateway_attestation_matches_current_profile_state() -> None:
    async def runner(args):
        result = _docker(*args)
        return result.returncode, result.stdout.encode(), result.stderr.encode()

    attestation = await attest_docker_egress(
        runner=runner,
        profile=DEFAULT_EGRESS_PROFILE,
        backend_ref="live-conformance",
    )
    assert attestation.profile_ref == DEFAULT_EGRESS_PROFILE.ref
    assert attestation.profile_digest == DEFAULT_EGRESS_PROFILE.digest


@pytest.mark.asyncio
async def test_live_gateway_attestation_rejects_stale_profile_rotation() -> None:
    stale = DEFAULT_EGRESS_PROFILE.model_copy(
        update={"version": DEFAULT_EGRESS_PROFILE.version + 1}
    )

    async def runner(_args):
        raise AssertionError("unapproved profiles must fail before Docker access")

    with pytest.raises(RuntimeError, match="profile is not approved"):
        await attest_docker_egress(
            runner=runner,
            profile=stale,
            backend_ref="live-conformance",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    ["missing backend capability", "untrusted remote daemon"],
)
async def test_attestation_fails_closed_when_selected_daemon_cannot_prove_policy(
    failure: str,
) -> None:
    async def runner(_args):
        return 1, b"", failure.encode()

    with pytest.raises(RuntimeError, match="network is unavailable"):
        await attest_docker_egress(
            runner=runner,
            profile=DEFAULT_EGRESS_PROFILE,
            backend_ref="live-conformance",
        )


@pytest.mark.asyncio
async def test_generic_container_job_crosses_its_live_trusted_launch_adapter(
    tmp_path: Path,
) -> None:
    """Use the real Container Job adapter, including attestation and evidence."""

    published: dict[str, bytes] = {}

    async def publish(_request, name, data):
        published[name] = data
        return f"artifact:{name}"

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        command_runner=_live_runner,
        evidence_publisher=publish,
    )
    request = _live_backend_request(tmp_path, command=["sleep", "120"])
    created = await backend.create_container(request)
    request.container_ref = created.container_ref
    request.egress_attestation_ref = created.diagnostics_ref
    request.publication = AuxiliaryOutcome(
        state="succeeded", diagnosticsRef="artifact:runtime-diagnostics"
    )
    try:
        inspected = _docker(
            "inspect",
            "--format",
            "{{json .NetworkSettings.Networks}}",
            created.container_ref,
        )
        assert inspected.returncode == 0, inspected.stderr[-1000:]
        assert set(json.loads(inspected.stdout)) == {EGRESS_NETWORK_REF}
        evidence = parse_and_verify_conformance_evidence(
            published[f"{request.job_id}-egress-attestation.json"],
            location="egress-attestation",
        )
        assert evidence["attestation"]["validationResult"] == "passed"
        assert evidence["attestation"]["healthResult"] == "healthy"
        assert evidence["attachmentIdentity"] == created.container_ref
    finally:
        await backend.remove_container(request)
        await backend.cleanup(request)

    # One versioned artifact per required row remains independently resolvable
    # and digest-checkable after the live workload is gone (#3625).
    attestation = parse_and_verify_conformance_evidence(
        published[f"{request.job_id}-egress-attestation.json"],
        location="egress-attestation",
    )
    lifecycle = parse_and_verify_conformance_evidence(
        published[f"{request.job_id}-egress-lifecycle.json"],
        location="egress-lifecycle",
    )
    assert attestation["attestation"]["profileRef"] == DEFAULT_EGRESS_PROFILE.ref
    assert lifecycle["cleanupResult"] == "succeeded"


@pytest.mark.asyncio
async def test_managed_helper_crosses_its_live_trusted_attestation_boundary() -> None:
    """Exercise the real managed-helper adapter instead of relabelling raw Docker."""

    launcher = DockerWorkloadLauncher()
    request = SimpleNamespace(
        profile=SimpleNamespace(network_policy="restricted_egress")
    )
    attestation = await launcher._attest_egress_before_launch(request)
    assert attestation is not None
    assert attestation.validation_state == "attested"
    assert attestation.network_ref == EGRESS_NETWORK_REF
    assert attestation.backend_ref == "docker-workload-launcher"


@pytest.mark.asyncio
@pytest.mark.parametrize("host_mode", ["static_compose", "on_demand_docker"])
async def test_omnigent_modes_cross_the_real_host_runtime_attestation_boundary(
    host_mode: str,
) -> None:
    """Prove both Omnigent modes enter the host adapter's trusted boundary."""

    runtime = OmnigentOAuthHostRuntime(client=SimpleNamespace())
    attestation = await runtime._attest_egress(
        {"hostMode": host_mode, "networkRef": OMNIGENT_EGRESS_PROFILE.network_ref}
    )
    assert attestation.validation_state == "attested"
    assert attestation.profile_ref == OMNIGENT_EGRESS_PROFILE.ref
    assert attestation.network_ref == OMNIGENT_EGRESS_PROFILE.network_ref
    assert attestation.backend_ref == "omnigent-host-runtime"


@pytest.mark.asyncio
async def test_trusted_boundary_rejects_falsified_gateway_attestation() -> None:
    """A reachable network cannot substitute for exact trusted gateway state."""

    async def falsifying_runner(args):
        code, stdout, stderr = await _live_runner(args)
        if args[0] == "inspect" and "NetworkSettings.Networks" in args[2]:
            payload = json.loads(stdout)
            payload["labels"]["moonmind.egress.config-digest"] = "sha256:" + "0" * 64
            stdout = json.dumps(payload).encode()
        return code, stdout, stderr

    with pytest.raises(RuntimeError, match="config label is stale"):
        await attest_docker_egress(
            runner=falsifying_runner,
            profile=DEFAULT_EGRESS_PROFILE,
            backend_ref="live-conformance:falsified",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("interruption", ["cancellation", "timeout", "worker-crash"])
async def test_interrupted_container_job_removes_owned_attachment_and_publishes_lifecycle(
    tmp_path: Path, interruption: str
) -> None:
    """Replay terminal interruption shapes through the real Docker boundary."""

    published: dict[str, dict] = {}

    async def publish(_request, name, data):
        path = tmp_path / name.replace(":", "-")
        path.write_bytes(data)
        published[name] = json.loads(path.read_text())
        return f"file:{path}"

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        command_runner=_live_runner,
        evidence_publisher=publish,
    )
    request = _live_backend_request(tmp_path, command=["sleep", "120"])
    created = await backend.create_container(request)
    request.container_ref = created.container_ref
    request.egress_attestation_ref = created.diagnostics_ref
    try:
        await backend.start_container(request)
        action = {
            "cancellation": ("stop", "--time", "1"),
            "timeout": ("stop", "--time", "0"),
            "worker-crash": ("kill",),
        }[interruption]
        result = _docker(*action, request.container_ref)
        assert result.returncode == 0, result.stderr[-1000:]
        request.publication = AuxiliaryOutcome(
            state="succeeded", diagnosticsRef=f"artifact:runtime-{interruption}"
        )
        await backend.remove_container(request)
        lifecycle = await backend.cleanup(request)
        assert lifecycle.cleanup_succeeded is True
        evidence = published[f"{request.job_id}-egress-lifecycle.json"]
        assert evidence["cleanupResult"] == "succeeded"
        assert evidence["reconciliationResult"] == "succeeded"
        assert evidence["launchAttestationRef"] == created.diagnostics_ref
        assert evidence["workloadAttachmentIdentity"] == request.container_ref
        assert _docker("inspect", request.container_ref).returncode != 0
    finally:
        _docker("rm", "--force", request.container_ref)


@pytest.mark.asyncio
async def test_partial_setup_and_failed_cleanup_are_owned_and_reconcilable(
    tmp_path: Path,
) -> None:
    published: dict[str, dict] = {}

    async def publish(_request, name, data):
        published[name] = json.loads(data)
        return f"artifact:{name}"

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        command_runner=_live_runner,
        evidence_publisher=publish,
    )
    request = _live_backend_request(tmp_path, command=["sleep", "120"])

    # No attachment exists after a partial setup failure, and cleanup records
    # that exact absence without claiming authority over deployment resources.
    partial = await backend.cleanup(request)
    assert partial.cleanup_succeeded is True
    assert (
        published[f"{request.job_id}-egress-lifecycle.json"]["cleanupResult"]
        == "succeeded"
    )

    created = await backend.create_container(request)
    request.container_ref = created.container_ref
    request.egress_attestation_ref = created.diagnostics_ref
    try:
        failed = await backend.cleanup(request)
        assert failed.cleanup_succeeded is False
        failure = published[f"{request.job_id}-egress-lifecycle.json"]
        assert failure["failureCode"] == "attachment_still_present"
        assert failure["cleanupResult"] == "failed"
        assert failure["reconciliationResult"] == "failed"

        await backend.remove_container(request)
        recovered = await backend.cleanup(request)
        assert recovered.cleanup_succeeded is True
        final = published[f"{request.job_id}-egress-lifecycle.json"]
        assert final["cleanupResult"] == "succeeded"
        assert final["reconciliationResult"] == "succeeded"
    finally:
        _docker("rm", "--force", request.container_ref)
