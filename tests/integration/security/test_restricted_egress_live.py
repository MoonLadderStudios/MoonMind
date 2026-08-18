"""Production-shaped restricted-egress network conformance for #3625.

Set ``MOONMIND_RUN_EGRESS_CONFORMANCE=1`` after starting the normal Compose
deployment. Also provide operator-controlled, profile-approved fixtures through
``MOONMIND_EGRESS_CNAME_DNS_NAME`` and ``MOONMIND_EGRESS_MIXED_DNS_NAME``;
the latter must resolve to public and prohibited non-global addresses in one
answer set. The suite creates only short-lived, labelled probe containers and
does not create or remove deployment networks or gateways.
"""

from __future__ import annotations

import ipaddress
import json
import os
import shlex
import socket
import subprocess
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from moonmind.omnigent.bridge_artifacts import LocalOmnigentArtifactGateway
from moonmind.omnigent.execution_profiles import compile_effective_launch
from moonmind.omnigent.oauth_host_runtime import OmnigentOAuthHostRuntime
from moonmind.security.egress import (
    DEFAULT_EGRESS_PROFILE,
    EGRESS_NETWORK_REF,
    OMNIGENT_EGRESS_PROFILE,
    OMNIGENT_PROXY_URL,
    PROXY_URL,
    attest_docker_egress,
)
from moonmind.security.egress_conformance_evidence import (
    parse_and_verify_conformance_evidence,
)
from moonmind.security.outbound_scan import scan_outbound_text
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AuthVolumeRef,
    CredentialMountRef,
    OmnigentHostLease,
    OmnigentOAuthHostBinding,
)
from moonmind.schemas.container_job_models import (
    AuxiliaryOutcome,
    ContainerJobActivityRequest,
    ContainerJobWorkflowInput,
)
from moonmind.schemas.workload_models import RunnerProfile, WorkloadRequest
from moonmind.workflows.temporal.container_job_backend import (
    DockerContainerJobBackend,
)
from moonmind.workloads.docker_launcher import DockerWorkloadLauncher
from moonmind.workloads.registry import RunnerProfileRegistry

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

    attachments = [
        (f"moonmind-test-egress-dns-{uuid.uuid4().hex[:10]}", network)
        for network in (EGRESS_NETWORK_REF, OMNIGENT_EGRESS_PROFILE.network_ref)
        for _ in range(2)
    ]
    try:
        for name, network in attachments:
            result = _docker(
                "run",
                "-d",
                "--name",
                name,
                "--label",
                "moonmind.test.scope=MoonLadderStudios/MoonMind#3625",
                "--network",
                network,
                "--network-alias",
                "api.github.com",
                "alpine:3.21",
                "sleep",
                "120",
            )
            assert result.returncode == 0, result.stderr[-1000:]
        yield
    finally:
        for name, _network in attachments:
            _docker("rm", "--force", name)


def _probe(
    url: str,
    *,
    proxy: bool = True,
    network_ref: str = EGRESS_NETWORK_REF,
    proxy_url: str = PROXY_URL,
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
        "moonmind.test.scope=MoonLadderStudios/MoonMind#3625",
        "--network",
        network_ref,
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
    ]
    args += list(docker_args)
    if proxy:
        args += [
            "--env",
            f"HTTP_PROXY={proxy_url}",
            "--env",
            f"HTTPS_PROXY={proxy_url}",
            "--env",
            f"http_proxy={proxy_url}",
            "--env",
            f"https_proxy={proxy_url}",
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


def test_omnigent_proxy_allows_only_scoped_execution_fanout_routes() -> None:
    """The portable fan-out path reaches the API without broad control access."""

    create = _probe(
        "http://api:8000/api/executions",
        network_ref=OMNIGENT_EGRESS_PROFILE.network_ref,
        proxy_url=OMNIGENT_PROXY_URL,
        curl_args=(
            "-o",
            "/dev/null",
            "-w",
            "%{http_code}",
            "-X",
            "POST",
            "-H",
            "Content-Type: application/json",
            "--data",
            "{}",
        ),
    )
    assert create.returncode == 0, create.stderr[-1000:]
    assert create.stdout in {"401", "422"}

    describe = _probe(
        "http://api:8000/api/executions/mm%3Aegress-probe-not-found",
        network_ref=OMNIGENT_EGRESS_PROFILE.network_ref,
        proxy_url=OMNIGENT_PROXY_URL,
        curl_args=("-o", "/dev/null", "-w", "%{http_code}"),
    )
    assert describe.returncode == 0, describe.stderr[-1000:]
    assert describe.stdout in {"401", "404"}

    for method, path in (
        ("GET", "/api/executions"),
        ("POST", "/api/executions/mm%3Aegress-probe/cancel"),
    ):
        denied = _probe(
            f"http://api:8000{path}",
            network_ref=OMNIGENT_EGRESS_PROFILE.network_ref,
            proxy_url=OMNIGENT_PROXY_URL,
            curl_args=(
                "-o",
                "/dev/null",
                "-w",
                "%{http_code}",
                "-X",
                method,
            ),
        )
        assert denied.returncode == 0, denied.stderr[-1000:]
        assert denied.stdout == "403"


def _exec_sh(container_name: str, script: str) -> subprocess.CompletedProcess[str]:
    return _docker("exec", container_name, "sh", "-c", script)


def _container_url_probe(
    container_name: str,
    url: str,
    *,
    accept_http_error: bool = False,
    environment_prefix: str = "",
) -> subprocess.CompletedProcess[str]:
    script = (
        "import urllib.request; "
        + (
            "import urllib.error; "
            "\ntry: urllib.request.urlopen(%r, timeout=12).read(1)"
            "\nexcept urllib.error.HTTPError: pass" % url
            if accept_http_error
            else "urllib.request.urlopen(%r, timeout=12).read(1)" % url
        )
    )
    command = f"{environment_prefix} python3 -c {shlex.quote(script)}".strip()
    return _exec_sh(container_name, command)


def _controlled_dns_names() -> tuple[str, str]:
    """Require genuine CNAME and mixed-address fixtures for live proof.

    The mixed-answer name must be an operator-controlled, security-reviewed
    hostname already covered by the immutable profile. A Docker network alias
    is intentionally insufficient: embedded DNS may shadow public resolution
    instead of returning one mixed answer set.
    """

    cname_name = os.getenv("MOONMIND_EGRESS_CNAME_DNS_NAME", "").strip()
    if not cname_name:
        pytest.fail(
            "MOONMIND_EGRESS_CNAME_DNS_NAME is required for an "
            "operator-controlled CNAME live row"
        )
    reviewed_suffixes = {
        destination.dns_name
        for destination in DEFAULT_EGRESS_PROFILE.destinations
    }
    if not any(
        cname_name == suffix or cname_name.endswith(f".{suffix}")
        for suffix in reviewed_suffixes
    ):
        pytest.fail("CNAME fixture is outside the reviewed egress profile")
    try:
        canonical, aliases, cname_addresses = socket.gethostbyname_ex(cname_name)
    except OSError as exc:
        pytest.fail(f"CNAME conformance name cannot be resolved: {exc}")
    if not cname_addresses or (not aliases and canonical == cname_name):
        pytest.fail(
            "MOONMIND_EGRESS_CNAME_DNS_NAME must currently return a genuine "
            "CNAME chain"
        )

    mixed_name = os.getenv("MOONMIND_EGRESS_MIXED_DNS_NAME", "").strip()
    if not mixed_name:
        pytest.fail(
            "MOONMIND_EGRESS_MIXED_DNS_NAME is required for a controlled "
            "public/private mixed-answer live row"
        )
    if not any(
        mixed_name == suffix or mixed_name.endswith(f".{suffix}")
        for suffix in reviewed_suffixes
    ):
        pytest.fail("mixed-answer fixture is outside the reviewed egress profile")
    try:
        _canonical, _aliases, mixed_addresses = socket.gethostbyname_ex(mixed_name)
        parsed_addresses = [ipaddress.ip_address(item) for item in mixed_addresses]
    except (OSError, ValueError) as exc:
        pytest.fail(f"mixed-answer conformance name cannot be resolved: {exc}")
    if not any(address.is_global for address in parsed_addresses) or not any(
        not address.is_global for address in parsed_addresses
    ):
        pytest.fail(
            "MOONMIND_EGRESS_MIXED_DNS_NAME must return both public and "
            "prohibited non-global addresses in one answer set"
        )
    return cname_name, mixed_name


def _assert_running_container_policy(container_name: str, *, network_ref: str) -> None:
    """Probe the exact production-created attachment, not a surrogate container."""

    inspected = _docker(
        "inspect",
        "--format",
        '{{json .NetworkSettings.Networks}}\n{{json .HostConfig}}',
        container_name,
    )
    assert inspected.returncode == 0, inspected.stderr[-1000:]
    networks_raw, host_config_raw = inspected.stdout.splitlines()[:2]
    assert set(json.loads(networks_raw)) == {network_ref}
    host_config = json.loads(host_config_raw)
    assert host_config["NetworkMode"] == network_ref
    assert host_config["Privileged"] is False
    assert not host_config.get("CapAdd")
    assert not host_config.get("Devices")
    assert not host_config.get("Dns")
    assert not host_config.get("ExtraHosts")

    python_available = _exec_sh(container_name, "command -v python3 >/dev/null")
    assert python_available.returncode == 0, python_available.stderr[-1000:]
    cname_name, mixed_name = _controlled_dns_names()
    for url in (
        "https://api.github.com/",
        "https://github.com/",
        f"https://{cname_name}/",
        "https://api.openai.com/",
        "https://storage.googleapis.com/",
    ):
        result = _container_url_probe(
            container_name, url, accept_http_error=True
        )
        assert result.returncode == 0, result.stderr[-1000:]

    rebound_names = [
        f"moonmind-test-owned-rebind-{uuid.uuid4().hex[:10]}" for _ in range(2)
    ]
    try:
        for name in rebound_names:
            result = _docker(
                "run",
                "-d",
                "--name",
                name,
                "--label",
                "moonmind.test.scope=MoonLadderStudios/MoonMind#3625",
                "--network",
                network_ref,
                "--network-alias",
                "rebind.openai.com",
                "alpine:3.21",
                "sleep",
                "120",
            )
            assert result.returncode == 0, result.stderr[-1000:]
        rebound = _container_url_probe(
            container_name, "https://rebind.openai.com/"
        )
        assert rebound.returncode != 0
    finally:
        for name in rebound_names:
            _docker("rm", "--force", name)

    for url in (
        "https://example.com/",
        "https://0.0.0.1/",
        "https://1.1.1.1/",
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
        f"https://{mixed_name}/",
    ):
        result = _container_url_probe(container_name, url)
        assert result.returncode != 0

    no_proxy = _container_url_probe(
        container_name,
        "https://github.com/",
        environment_prefix=(
            "env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy "
            "-u NO_PROXY -u no_proxy"
        ),
    )
    assert no_proxy.returncode != 0
    bypass = _container_url_probe(
        container_name,
        "https://github.com/",
        environment_prefix="NO_PROXY='*' no_proxy='*'",
    )
    assert bypass.returncode != 0
    redirected = _container_url_probe(
        container_name,
        "https://www.google.com/url?q=https%3A%2F%2Fexample.com%2F",
    )
    assert redirected.returncode != 0
    hardening = _exec_sh(
        container_name,
        "test \"$(sed -n 's/^CapEff:[[:space:]]*//p' /proc/self/status)\" = "
        "\"0000000000000000\"; test ! -e /dev/net/tun; "
        "test ! -S /var/run/docker.sock; ! command -v docker >/dev/null 2>&1; "
        "if command -v ip >/dev/null 2>&1; then "
        "! ip route add 198.51.100.0/24 via 127.0.0.1 >/dev/null 2>&1; fi",
    )
    assert hardening.returncode == 0, hardening.stderr[-1000:]
    raw_socket = _exec_sh(
        container_name,
        "command -v python3 >/dev/null 2>&1; "
        "! python3 -c 'import socket; "
        "socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_TCP)'",
    )
    assert raw_socket.returncode == 0, raw_socket.stderr[-1000:]
    child_launch = _exec_sh(
        container_name,
        "test ! -S /var/run/docker.sock; "
        "! docker run --rm --network host alpine:3.21 true >/dev/null 2>&1",
    )
    assert child_launch.returncode == 0, child_launch.stderr[-1000:]
    child_spec = {
        "image": "alpine:3.21",
        "workspaceRef": {"kind": "external_state", "artifactRef": "probe"},
        "command": ["true"],
        "networkMode": "host",
        "resources": {"cpuMillis": 100, "memoryMiB": 64, "pids": 16},
        "timeoutSeconds": 30,
    }
    write_spec = (
        "import pathlib; "
        f"pathlib.Path('/tmp/mm-child-bypass.json').write_text({json.dumps(json.dumps(child_spec))})"
    )
    child_tool_launch = _exec_sh(
        container_name,
        "if command -v moonmind >/dev/null 2>&1; then "
        f"python3 -c {shlex.quote(write_spec)}; "
        "! moonmind container run --spec /tmp/mm-child-bypass.json "
        "--request-id egress-child-bypass >/dev/null 2>&1; "
        "rm -f /tmp/mm-child-bypass.json; fi",
    )
    assert child_tool_launch.returncode == 0, child_tool_launch.stderr[-1000:]


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
                    "image": "python:3.12-alpine",
                    "workspaceRef": {"kind": "external_state", "artifactRef": "probe"},
                    "entrypoint": ["sh", "-c"],
                    "command": [" ".join(command)],
                    "networkMode": "bridge",
                    "resources": {"cpuMillis": 100, "memoryMiB": 128, "pids": 32},
                    "timeoutSeconds": 30,
                },
            },
            "resolvedWorkspaceRef": str(workspace),
        }
    )


async def _acquire_live_backend_image(
    backend: DockerContainerJobBackend,
    request: ContainerJobActivityRequest,
) -> None:
    acquired = await backend.acquire_image(request)
    assert acquired.resolved_image_ref
    assert acquired.image_observation is not None
    assert acquired.image_observation.resolved_digest
    request.resolved_image_ref = acquired.resolved_image_ref
    request.image_observation = acquired.image_observation


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
        ("dns", ["8.8.8.8"]),
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
        "image": "python:3.12-alpine",
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
        ["--dns", "8.8.8.8"],
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
    await _acquire_live_backend_image(backend, request)
    created = await backend.create_container(request)
    request.container_ref = created.container_ref
    request.egress_attestation_ref = created.diagnostics_ref
    request.publication = AuxiliaryOutcome(
        state="succeeded", diagnosticsRef="artifact:runtime-diagnostics"
    )
    try:
        request.started_at = datetime.now(UTC)
        started = await backend.start_container(request)
        request.egress_attestation_ref = started.diagnostics_ref
        _assert_running_container_policy(
            created.container_ref, network_ref=EGRESS_NETWORK_REF
        )
        evidence = parse_and_verify_conformance_evidence(
            published[f"{request.job_id}-egress-attestation.json"],
            location="egress-attestation",
        )
        assert evidence["attestation"]["validationResult"] == "passed"
        assert evidence["attestation"]["healthResult"] == "healthy"
        assert evidence["attachmentIdentity"] == created.container_ref
        assert evidence["evidenceStage"] == "running"
        assert evidence["networkIdentity"]
        assert evidence["endpointIdentity"]
        assert evidence["resolvedImageRef"] == request.resolved_image_ref
        assert evidence["workloadImageDigest"] == request.resolved_image_ref
        assert evidence["imageObservation"]["resolvedDigest"]
        assert evidence["architecture"] in {"amd64", "arm64"}
        request.finished_at = datetime.now(UTC)
        request.terminal_state = "succeeded"
        published_result = await backend.publish_evidence(request)
        assert published_result.diagnostics_ref
        request.publication = AuxiliaryOutcome(
            state="succeeded", diagnosticsRef=published_result.diagnostics_ref
        )
        diagnostics = json.loads(
            published[f"{request.job_id}-diagnostics.json"]
        )
        runtime_egress = diagnostics["egressEvidence"]
        assert runtime_egress["deniedConnectionCount"] > 0
        assert runtime_egress["denialDiagnostics"]
        assert runtime_egress["launchAttestationRef"] == (
            request.egress_attestation_ref
        )
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
    assert lifecycle["launchAttestationRef"] == request.egress_attestation_ref
    for name, payload in published.items():
        scan = scan_outbound_text(
            payload.decode("utf-8", errors="replace"),
            location=f"live-container-job:{name}",
            high_security_mode=True,
        )
        assert scan.allowed, scan.sanitized_diagnostics


@pytest.mark.asyncio
async def test_managed_helper_crosses_start_helper_and_survives_cleanup(
    tmp_path: Path,
) -> None:
    """Launch and stop through the production helper owner, then resolve evidence."""

    workspace_root = tmp_path / "helper-workspace"
    artifacts_dir = workspace_root / "artifacts"
    artifacts_dir.mkdir(parents=True)
    volume_name = f"moonmind-egress-helper-{uuid.uuid4().hex[:12]}"
    created = _docker("volume", "create", volume_name)
    assert created.returncode == 0, created.stderr[-1000:]
    profile = {
        "id": "egress-live-helper",
        "kind": "bounded_service",
        "image": "python:3.12-alpine",
        "entrypoint": ["sh", "-c"],
        "commandWrapper": [],
        "workdirTemplate": str(workspace_root),
        "requiredMounts": [
            {"type": "volume", "source": volume_name, "target": str(workspace_root)}
        ],
        "envAllowlist": [],
        "networkPolicy": "restricted_egress",
        "resources": {"cpu": "0.25", "memory": "64m"},
        "timeoutSeconds": 30,
        "maxTimeoutSeconds": 30,
        "maxConcurrency": 1,
        "helperTtlSeconds": 120,
        "maxHelperTtlSeconds": 120,
        "readinessProbe": {
            "type": "exec",
            "command": ["python3", "-c", "import urllib.request"],
            "intervalSeconds": 1,
            "timeoutSeconds": 5,
            "retries": 3,
        },
        "cleanup": {"removeContainerOnExit": True, "killGraceSeconds": 1},
        "devicePolicy": {"mode": "none"},
    }
    registry = RunnerProfileRegistry(
        [RunnerProfile.model_validate(profile)],
        workspace_root=workspace_root,
        allowed_image_registries=["docker.io"],
    )
    request = registry.validate_request(
        WorkloadRequest.model_validate(
            {
                "profileId": "egress-live-helper",
                "agentRunId": "egress-live",
                "stepId": uuid.uuid4().hex[:12],
                "attempt": 1,
                "toolName": "container.start_helper",
                "repoDir": str(workspace_root),
                "artifactsDir": str(artifacts_dir),
                "command": ["sleep 120"],
                "ttlSeconds": 120,
            }
        )
    )
    launcher = DockerWorkloadLauncher()
    try:
        started = await launcher.start_helper(request)
        assert started.status == "ready"
        helper_name = request.container_name
        _assert_running_container_policy(
            helper_name, network_ref=EGRESS_NETWORK_REF
        )
        attached_authority = parse_and_verify_conformance_evidence(
            Path(started.output_refs["security.egress.authority"]).read_bytes(),
            location="managed-helper-attached-authority",
        )
        assert attached_authority["state"] == "attached"
        assert attached_authority["leaseAuthority"]["state"] == "held"

        stopped = await launcher.stop_helper(request)
        assert stopped.status == "stopped"
        assert _docker("inspect", helper_name).returncode != 0
        resolved = parse_and_verify_conformance_evidence(
            Path(stopped.output_refs["security.egress"]).read_bytes(),
            location="managed-helper-egress",
        )
        assert resolved["conformanceRow"] == "managed_helper"
        assert resolved["profileRef"] == DEFAULT_EGRESS_PROFILE.ref
        assert resolved["cleanupResult"] == "succeeded"
        assert resolved["reconciliationResult"] == "succeeded"
        terminal_authority = parse_and_verify_conformance_evidence(
            Path(stopped.output_refs["security.egress.authority"]).read_bytes(),
            location="managed-helper-terminal-authority",
        )
        assert terminal_authority["state"] == "stopped"
        assert terminal_authority["leaseAuthority"]["state"] == "released"
        assert terminal_authority["hostMode"] == "managed_helper"
        assert terminal_authority["workloadClass"] == "managed_helper"
        assert terminal_authority["runtimeProvenance"] == (
            "docker_workload_launcher/docker-engine"
        )
        assert terminal_authority["deniedConnectionCount"] > 0
        assert terminal_authority["denialDiagnostics"]
        # Both lifecycle artifacts remain digest-resolvable after the helper is
        # absent; neither path is a live-container authority.
        assert parse_and_verify_conformance_evidence(
            Path(started.output_refs["security.egress.authority"]).read_bytes(),
            location="managed-helper-attached-authority-after-cleanup",
        )["state"] == "attached"
    finally:
        _docker("rm", "--force", request.container_name)
        _docker("volume", "rm", "--force", volume_name)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "host_mode",
    [
        "static_compose",
        "on_demand_docker",
    ],
)
async def test_omnigent_modes_cross_the_real_host_runtime_attestation_boundary(
    host_mode: str,
    tmp_path: Path,
) -> None:
    """Cross ``prepare_host`` while leaving launch and attachment owners real."""

    for variable in ("OMNIGENT_IMAGE_REF", "OMNIGENT_HOST_IMAGE_REF"):
        if not os.getenv(variable, "").strip():
            pytest.fail(f"{variable} immutable image is required for live proof")
    now = datetime.now(UTC)
    on_demand = host_mode == "on_demand_docker"
    container_name = f"mm-host-egress-{uuid.uuid4().hex[:12]}"
    binding = OmnigentOAuthHostBinding(
        bindingRef="omnigent-oauth:egress-live",
        providerProfileId="egress-live",
        endpointRef="default",
        harness="codex-native",
        credentialMountRef=CredentialMountRef(
            authVolumeRef=AuthVolumeRef(
                providerProfileId="egress-live",
                runtimeId="codex_cli",
                providerId="openai",
                volumeRef="codex_auth_volume",
                credentialGeneration=1,
                ownerUserId="egress-live",
            ),
            targetPath="/home/app/.codex",
            runtimeUid=1000,
            runtimeGid=1000,
        ),
        staticHostId=None if on_demand else "egress-live-static",
        hostLaunchProfileRef="codex-oauth-v1" if on_demand else None,
    )
    lease = OmnigentHostLease(
        leaseId=f"egress-live-{uuid.uuid4().hex}",
        providerProfileId="egress-live",
        providerLeaseId=f"provider-egress-live-{uuid.uuid4().hex}",
        bindingRef=binding.binding_ref,
        credentialGeneration=1,
        omnigentHostId=None if on_demand else "egress-live-static",
        containerName=container_name if on_demand else None,
        status="ready",
        acquiredAt=now,
        lastHeartbeatAt=now,
        expiresAt=now + timedelta(minutes=5),
    )
    launch = compile_effective_launch(
        profile_ref="omnigent-codex@1",
        policy_ref="codex-on-demand@1" if on_demand else "codex-static@1",
        provider_profile_id="egress-live",
    )
    workspace = tmp_path / "workspace"
    skills = tmp_path / "skills"
    workspace.mkdir()
    skills.mkdir()
    runtime = OmnigentOAuthHostRuntime(client=object(), workspace_root=tmp_path)
    runtime._prepare_skill_projection = AsyncMock(  # type: ignore[method-assign]
        return_value=skills
    )
    runtime._prepare_workspace = AsyncMock(  # type: ignore[method-assign]
        return_value=workspace
    )
    runtime._align_workspace_ownership = MagicMock()  # type: ignore[method-assign]
    runtime._resolve_daemon_workspace_root = AsyncMock(  # type: ignore[method-assign]
        return_value=None
    )
    runtime._resolve_exact_host = AsyncMock(  # type: ignore[method-assign]
        return_value={"id": "egress-live-host", "harnesses": ["codex-native"]}
    )
    runtime._preflight_mounted_tools = AsyncMock(  # type: ignore[method-assign]
        return_value={"status": "not_required", "boundaries": []}
    )
    evidence_request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef="omnigent-codex@1",
        correlationId=f"egress-live:{host_mode}:normal",
        idempotencyKey=f"egress-live:{uuid.uuid4().hex}",
    )
    artifact_gateway = LocalOmnigentArtifactGateway(
        root=tmp_path / "protected-egress-evidence"
    )
    static_was_running = False
    evidence: dict[str, object] | None = None
    launch_evidence_ref: str | None = None
    terminal_evidence_ref: str | None = None
    attachment_identity = ""
    if not on_demand:
        prior = _docker(
            "compose",
            "-f",
            "docker-compose.yaml",
            "--profile",
            "omnigent-host-codex",
            "ps",
            "-q",
            "omnigent-host-codex",
        )
        if prior.returncode == 0 and prior.stdout.strip():
            state = _docker(
                "inspect", "--format", "{{.State.Running}}", prior.stdout.strip()
            )
            static_was_running = (
                state.returncode == 0 and state.stdout.strip() == "true"
            )
        if static_was_running:
            pytest.fail(
                "static conformance row requires a test-owned host; stop the "
                "pre-existing omnigent-host-codex service first"
            )

    try:
        preflight = await runtime.prepare_host(
            binding=binding,
            host_lease=lease,
            workspace_key=f"egress-live:{host_mode}",
            workspace_locator={"kind": "sandbox", "workspaceId": "egress-live"},
            current_workflow_id="egress-live",
            current_step_execution_id=f"egress-live:{host_mode}",
            effective_launch=launch,
            artifact_gateway=artifact_gateway,
            evidence_request=evidence_request,
        )
        evidence = preflight["egressAttestation"]
        launch_evidence_ref = preflight["egressEvidenceRef"]
        assert evidence["validationResult"] == "passed"
        assert evidence["profileRef"] == OMNIGENT_EGRESS_PROFILE.ref
        assert evidence["networkRef"] == OMNIGENT_EGRESS_PROFILE.network_ref
        assert evidence["backendRef"] == "omnigent-host-runtime"
        assert evidence["networkIdentity"]
        assert evidence["endpointIdentity"]
        assert evidence["workloadImageDigest"].startswith("sha256:")
        assert evidence["architecture"] in {"amd64", "arm64"}
        assert evidence["serverImageRefObserved"] == launch["serverImageRef"]
        assert str(evidence["serverImageDigest"]).startswith("sha256:")
        assert evidence["serverArchitecture"] in {"amd64", "arm64"}
        identity = str(evidence["attachmentIdentity"])
        attachment_identity = identity
        _assert_running_container_policy(
            identity, network_ref=OMNIGENT_EGRESS_PROFILE.network_ref
        )

    finally:
        if on_demand:
            cleanup = await runtime.stop_host(
                binding=binding,
                host_lease=lease,
                effective_launch=launch,
                egress_evidence=evidence,
                launch_evidence_ref=launch_evidence_ref,
                evidence_request=evidence_request,
                artifact_gateway=artifact_gateway,
            )
            terminal_evidence_ref = cleanup.get("evidenceRef")
        else:
            cleanup = await runtime.stop_host(
                binding=binding,
                host_lease=lease,
                effective_launch=launch,
                egress_evidence=evidence,
                launch_evidence_ref=launch_evidence_ref,
                evidence_request=evidence_request,
                artifact_gateway=artifact_gateway,
            )
            terminal_evidence_ref = cleanup.get("evidenceRef")

    assert evidence is not None
    assert launch_evidence_ref is not None
    launched = parse_and_verify_conformance_evidence(
        await artifact_gateway.read_bytes(launch_evidence_ref),
        location=f"omnigent-{host_mode}-launch",
    )
    expected_row = host_mode
    assert launched["conformanceRow"] == expected_row
    assert launched["state"] == "launched"
    assert launched["cleanupResult"] == "pending"
    assert launched["attachmentIdentity"] == attachment_identity
    if on_demand:
        assert _docker("inspect", attachment_identity).returncode != 0
    assert terminal_evidence_ref is not None
    terminal = parse_and_verify_conformance_evidence(
        await artifact_gateway.read_bytes(terminal_evidence_ref),
        location=f"omnigent-{host_mode}-terminal",
    )
    assert terminal["conformanceRow"] == expected_row
    assert terminal["state"] == "terminal"
    assert terminal["launchEvidenceRef"] == launch_evidence_ref
    assert terminal["cleanupResult"] in {
        "succeeded",
        "drained_owned_static_host",
    }
    assert terminal["reconciliationResult"] == "succeeded"
    assert terminal["deniedConnectionCount"] > 0
    assert terminal["denialDiagnostics"]


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
    await _acquire_live_backend_image(backend, request)
    created = await backend.create_container(request)
    request.container_ref = created.container_ref
    request.egress_attestation_ref = created.diagnostics_ref
    try:
        started = await backend.start_container(request)
        request.egress_attestation_ref = started.diagnostics_ref
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
        assert evidence["launchAttestationRef"] == started.diagnostics_ref
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
    await _acquire_live_backend_image(backend, request)

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
