"""Production-shaped restricted-egress network conformance for #3516.

Set ``MOONMIND_RUN_EGRESS_CONFORMANCE=1`` after starting the normal Compose
deployment. The suite creates only short-lived, labelled probe containers and
does not create or remove deployment networks or gateways.
"""

from __future__ import annotations

import os
import subprocess
import uuid

import pytest

from moonmind.security.egress import (
    DEFAULT_EGRESS_PROFILE,
    EGRESS_NETWORK_REF,
    PROXY_URL,
    attest_docker_egress,
)

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
