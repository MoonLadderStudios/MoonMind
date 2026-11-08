"""Actual HTTP(S), SDK, subprocess and browser connections to compiled policy.

The Docker journey separately proves real launch and route isolation. These
loopback fixtures exercise Squid and client adapters without a Docker socket.
"""

from __future__ import annotations

import os
import pwd
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
import requests

from moonmind.security.egress import bounded_denial_diagnostics, denied_connection_count
from moonmind.security.egress_policy import ScopedEgressPolicy, tool_proxy_options
from moonmind.security.scoped_egress import render_scoped_proxy

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


class FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "http://unrelated.example.test:8080/")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<html><body>admitted-scope-fixture</body></html>")

    def log_message(self, *args):
        pass


def fixture_certificate(root):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "scope.example.test")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
        .not_valid_after(datetime.now(UTC) + timedelta(hours=1))
        .sign(key, hashes.SHA256())
    )
    key_path, cert_path = root / "fixture.key", root / "fixture.crt"
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path


@contextmanager
def fixture_servers(root, *, bind="127.0.0.1", http_port=0, https_port=0, events=None):
    clear = ThreadingHTTPServer((bind, http_port), FixtureHandler)
    tls = ThreadingHTTPServer((bind, https_port), FixtureHandler)
    cert, key = fixture_certificate(root)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(cert, key)
    if events is not None:
        ctx.set_servername_callback(lambda _socket, name, _context: events.append(name))
    tls.socket = ctx.wrap_socket(tls.socket, server_side=True)
    threads = [
        threading.Thread(target=s.serve_forever, daemon=True) for s in (clear, tls)
    ]
    for t in threads:
        t.start()
    try:
        yield clear.server_port, tls.server_port
    finally:
        for server in (clear, tls):
            server.shutdown()
            server.server_close()
        for t in threads:
            t.join(timeout=5)


@pytest.mark.parametrize("adapter", ["sdk", "chromium", "tls"])
def test_actual_squid_clients_preserve_exact_scope(tmp_path, adapter):
    squid, chromium = shutil.which("squid"), shutil.which("chromium")
    assert squid, "test-runtime must supply the proxy fixture"
    if adapter == "chromium":
        assert chromium, "test-runtime must supply the browser fixture"
    proxy_address = {"sdk": "127.0.0.2", "chromium": "127.0.0.3", "tls": "127.0.0.4"}[
        adapter
    ]
    tls_events = []
    with (
        fixture_servers(tmp_path, events=tls_events) as (http_port, https_port),
        tempfile.TemporaryDirectory(prefix="moonmind-test-scoped-proxy-") as proxy_root,
    ):
        proxy_path = Path(proxy_root)
        proxy_identity = {}
        if os.geteuid() == 0:
            # Compose runs pytest as root. Keep Squid's files outside pytest's
            # private root so its unprivileged process can read and write them.
            account = pwd.getpwnam("proxy")
            os.chown(proxy_path, account.pw_uid, account.pw_gid)
            proxy_identity = {
                "user": account.pw_uid,
                "group": account.pw_gid,
                "extra_groups": [],
            }
        policy = ScopedEgressPolicy.model_validate(
            {
                "policyRef": "connection-fixtures",
                "allowedWorkflowIds": ["fixtures"],
                "allowedImageSourceRefs": ["fixtures"],
                "destinations": [
                    {
                        "host": "feeds.example.test",
                        "purpose": "tool-data",
                        "protocol": "https",
                        "port": https_port,
                    },
                    {
                        "host": "cloud.example.test",
                        "purpose": "provider-api",
                        "protocol": "https",
                        "port": https_port,
                    },
                    {
                        "host": "registry.example.test",
                        "purpose": "registry-api",
                        "protocol": "https",
                        "port": https_port,
                    },
                    {
                        "host": "lab.example.test",
                        "purpose": "live-target",
                        "protocol": "http",
                        "port": http_port,
                    },
                ],
            }
        )
        # Loopback substitutes only for fixture transport. Public/private
        # admission and all-answer validation have their own boundary tests.
        config, hosts = render_scoped_proxy(
            policy,
            {d.host: ("127.0.0.1",) for d in policy.destinations},
            listen_address=proxy_address,
            client_address="127.0.0.1",
        )
        host_path, log_path = proxy_path / "hosts", proxy_path / "access.log"
        host_path.write_bytes(hosts)
        host_path.chmod(0o444)
        config = (
            config.replace(b"/tmp/moonmind-hosts", str(host_path).encode())
            .replace(b"/tmp/moonmind-access.log", str(log_path).encode())
            .replace(b"/tmp/moonmind-squid.pid", str(proxy_path / "squid.pid").encode())
            .replace(b"/tmp/moonmind-cache.log", str(proxy_path / "cache.log").encode())
        )
        conf = proxy_path / "squid.conf"
        conf.write_bytes(config)
        conf.chmod(0o444)
        errors = tmp_path / "proxy.stderr"
        with errors.open("wb") as stderr:
            proxy = subprocess.Popen(
                [squid, "-NYC", "-d", "1", "-f", str(conf)],
                stdout=subprocess.DEVNULL,
                stderr=stderr,
                **proxy_identity,
            )
            try:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    if proxy.poll() is not None:
                        pytest.fail(
                            f"fixture proxy exited {proxy.returncode}: "
                            + errors.read_text()[-4000:]
                        )
                    try:
                        with socket.create_connection(
                            (proxy_address, 3128), timeout=0.1
                        ):
                            break
                    except OSError:
                        time.sleep(0.05)
                else:
                    pytest.fail(
                        "fixture proxy did not listen: " + errors.read_text()[-4000:]
                    )
                env = {
                    **os.environ,
                    "MOONMIND_EGRESS_PROXY": f"http://{proxy_address}:3128",
                    "NO_PROXY": "*",
                    "HTTPS_PROXY": "http://unrelated:1",
                }
                with httpx.Client(
                    **tool_proxy_options("httpx", environ=env), verify=False, timeout=5
                ) as client:
                    for name in ("feeds", "cloud", "registry"):
                        assert (
                            "admitted-scope-fixture"
                            in client.get(
                                f"https://{name}.example.test:{https_port}/"
                            ).text
                        )
                    assert (
                        client.get(f"http://lab.example.test:{http_port}/").status_code
                        == 200
                    )
                    for url in (
                        f"http://unrelated.example.test:{http_port}/",
                        f"http://127.0.0.1:{http_port}/",
                        f"http://lab.example.test:{https_port}/",
                    ):
                        assert client.get(url).status_code == 403
                    with pytest.raises(httpx.ProxyError):
                        client.get(f"https://unrelated.example.test:{https_port}/")
                    response = client.get(
                        f"http://lab.example.test:{http_port}/redirect",
                        follow_redirects=True,
                    )
                    assert response.status_code == 403
                with requests.Session() as client:
                    client.trust_env = False
                    assert (
                        client.get(
                            f"https://feeds.example.test:{https_port}/",
                            verify=False,
                            timeout=5,
                            **tool_proxy_options("requests", environ=env),
                        ).status_code
                        == 200
                    )
                child = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        "import os,httpx; from moonmind.security.egress_policy import tool_proxy_options; print(httpx.get(os.environ['FIXTURE_URL'],verify=False,timeout=5,**tool_proxy_options('httpx',environ=os.environ)).status_code)",
                    ],
                    env={
                        **env,
                        "FIXTURE_URL": f"https://cloud.example.test:{https_port}/",
                    },
                    capture_output=True,
                    check=False,
                    timeout=15,
                )
                assert child.returncode == 0 and child.stdout.strip() == b"200", (
                    child.stderr.decode()
                )
                if adapter == "tls":
                    from moonmind.schemas.repository_scanner_models import TLSAssessment
                    from moonmind.security.tls_assessment_runner import inspect_tls
                    from moonmind.security.tls_assessments import (
                        summarize_tls_assessment,
                    )

                    assessment = TLSAssessment(
                        origin=f"https://feeds.example.test:{https_port}",
                        egress_policy_ref="connection-fixtures",
                        protocols=["TLSv1.2", "TLSv1.3"],
                        ciphers=["NOT-A-REAL-CIPHER"],
                        max_requests_per_second=10,
                        timeout_seconds=30,
                    )
                    execution = inspect_tls(assessment, tmp_path / "tls", environ=env)
                    summary = summarize_tls_assessment(
                        assessment, execution, "art_fixture"
                    )
                    assert execution["probes"][0]["status"] == "observed"
                    assert execution["probes"][0]["certificates"]
                    assert execution["probes"][0]["trustResult"] == "untrusted"
                    assert execution["probes"][1]["status"] == "unsupported_client"
                    assert summary.status == "partial" and summary.unperformed_checks
                    assert "feeds.example.test" in tls_events
                    before_denials = len(tls_events)
                    for index, origin in enumerate(
                        (
                            f"https://unrelated.example.test:{https_port}",
                            f"https://feeds.example.test:{http_port}",
                        )
                    ):
                        denied = assessment.model_copy(
                            update={
                                "origin": origin,
                                "protocols": ["TLSv1.2"],
                                "ciphers": [],
                            }
                        )
                        blocked = inspect_tls(
                            denied, tmp_path / f"tls-denied-{index}", environ=env
                        )
                        assert blocked["probes"][0]["status"] == "blocked_handshake"
                        assert not blocked["probes"][0]["certificates"]
                    assert len(tls_events) == before_denials
                if adapter == "chromium":
                    browser = subprocess.run(
                        [
                            chromium,
                            "--headless",
                            "--no-sandbox",
                            "--disable-dev-shm-usage",
                            "--disable-background-networking",
                            "--no-first-run",
                            f"--user-data-dir={tmp_path / 'browser'}",
                            *tool_proxy_options("chromium", environ=env)["args"],
                            "--dump-dom",
                            f"http://lab.example.test:{http_port}/",
                        ],
                        capture_output=True,
                        check=False,
                        timeout=30,
                    )
                    evidence_dir = Path("artifacts/integration-ci/scoped-egress")
                    evidence_dir.mkdir(parents=True, exist_ok=True)
                    (evidence_dir / "browser.stderr").write_bytes(browser.stderr)
                    assert (
                        browser.returncode == 0
                        and b"admitted-scope-fixture" in browser.stdout
                    ), browser.stderr.decode()[-12000:]
            finally:
                proxy.terminate()
                try:
                    proxy.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proxy.kill()
                    proxy.wait(timeout=5)
        access = log_path.read_bytes()
        if adapter == "tls":
            assert f"CONNECT feeds.example.test:{https_port}".encode() in access
            assert f"CONNECT unrelated.example.test:{https_port}".encode() in access
            assert f"CONNECT feeds.example.test:{http_port}".encode() in access
        assert denied_connection_count(access, client_address="127.0.0.1") >= 5
        assert (
            1
            <= len(bounded_denial_diagnostics(access, client_address="127.0.0.1"))
            <= 20
        )


if __name__ == "__main__":
    import tempfile

    with (
        tempfile.TemporaryDirectory(prefix="moonmind-test-target-") as root,
        fixture_servers(Path(root), bind="0.0.0.0", http_port=8080, https_port=8443),
    ):
        threading.Event().wait()


@pytest.mark.parametrize(
    "hostname,expired",
    [
        ("tls.example.test", False),
        ("tls.example.test", True),
        ("wrong.example.test", False),
    ],
)
def test_tls_fixture_chain_and_controlled_clock(tmp_path, hostname, expired):
    """Pure qualification of the same certificate fixture used inside the image."""
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    from moonmind.security.tls_assessment_runner import certificate_evidence
    from tests.integration.security.tls_assessment_fixture import (
        CLOCK,
        HOST,
        certificates,
    )

    chain, _key, ca = certificates(tmp_path, hostname=hostname, expired=expired)
    evidence, findings, matched = certificate_evidence(chain.read_bytes(), HOST, CLOCK)
    assert len(evidence) == 2
    assert matched is (hostname == HOST)
    assert ("certificate_expired" in findings) is expired
    parsed = x509.load_pem_x509_certificates(chain.read_bytes())
    leaf, intermediate = tmp_path / "leaf.pem", tmp_path / "intermediate.pem"
    leaf.write_bytes(parsed[0].public_bytes(serialization.Encoding.PEM))
    intermediate.write_bytes(parsed[1].public_bytes(serialization.Encoding.PEM))
    verified = subprocess.run(
        [
            "openssl",
            "verify",
            "-CAfile",
            str(ca),
            "-untrusted",
            str(intermediate),
            "-attime",
            str(int(CLOCK.timestamp())),
            str(leaf),
        ],
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert (verified.returncode == 0) is (not expired), verified.stderr.decode()
