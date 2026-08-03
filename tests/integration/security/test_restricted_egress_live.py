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

from moonmind.security.egress import EGRESS_NETWORK_REF, PROXY_URL

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


def _probe(url: str, *, proxy: bool = True) -> subprocess.CompletedProcess[str]:
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
    if proxy:
        args += ["--env", f"HTTPS_PROXY={PROXY_URL}", "--env", "NO_PROXY="]
    args += ["curlimages/curl:8.12.1", "-fsS", "--max-time", "12", url]
    return _docker(*args)


def test_approved_https_destination_is_reachable_through_gateway() -> None:
    result = _probe("https://api.github.com/")
    assert result.returncode == 0, result.stderr[-1000:]


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/",
        "https://169.254.169.254/",
        "https://127.0.0.1/",
        "https://[::1]/",
    ],
)
def test_forbidden_destinations_fail_through_gateway(url: str) -> None:
    assert _probe(url).returncode != 0


def test_direct_route_is_absent_even_without_proxy_environment() -> None:
    assert _probe("https://api.github.com/", proxy=False).returncode != 0
