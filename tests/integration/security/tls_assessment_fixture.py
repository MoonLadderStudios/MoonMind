"""Local-only certificate/CONNECT fixtures for real TLS adapter qualification.

The gateway below is only the upstream-image fixture: the integration Squid
journey separately exercises the production compiled enforcement policy.
"""

from __future__ import annotations

import json
import os
import select
import socket
import socketserver
import ssl
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

CLOCK = datetime(2026, 6, 1, tzinfo=UTC)
HOST = "tls.example.test"


def certificates(root: Path, *, hostname=HOST, expired=False, valid_at=None):
    """Root/intermediate/leaf chain with a controlled validity interval."""
    root.mkdir(parents=True, exist_ok=True)
    keys = [
        rsa.generate_private_key(public_exponent=65537, key_size=2048) for _ in range(3)
    ]
    names = [
        x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, v)])
        for v in ("Fixture root", "Fixture intermediate", hostname)
    ]
    certs = []
    for index in range(3):
        signer = max(0, index - 1)
        builder = (
            x509.CertificateBuilder()
            .subject_name(names[index])
            .issuer_name(names[signer])
            .public_key(keys[index].public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(min(CLOCK, valid_at or CLOCK) - timedelta(days=30))
            .not_valid_after(
                (CLOCK - timedelta(days=1))
                if expired and index == 2
                else max(CLOCK, valid_at or CLOCK) + timedelta(days=30)
            )
            .add_extension(
                x509.BasicConstraints(
                    ca=index < 2, path_length=1 - index if index < 2 else None
                ),
                critical=True,
            )
        )
        if index == 2:
            builder = builder.add_extension(
                x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False
            )
        certs.append(builder.sign(keys[signer], hashes.SHA256()))
    encode = lambda c: c.public_bytes(serialization.Encoding.PEM)
    (root / "ca.pem").write_bytes(encode(certs[0]))
    (root / "chain.pem").write_bytes(encode(certs[2]) + encode(certs[1]))
    (root / "key.pem").write_bytes(
        keys[2].private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    (root / "key.pem").chmod(0o600)
    return root / "chain.pem", root / "key.pem", root / "ca.pem"


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


@contextmanager
def tls_server(
    root: Path, *, hostname=HOST, expired=False, legacy=False, existing=False
):
    cert, key, ca = (
        (root / "chain.pem", root / "key.pem", root / "ca.pem")
        if existing
        else certificates(root, hostname=hostname, expired=expired)
    )
    events = []
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1 if legacy else ssl.TLSVersion.TLSv1_2
    context.maximum_version = ssl.TLSVersion.TLSv1 if legacy else ssl.TLSVersion.TLSv1_3
    if legacy:
        # Fixture only: expose an old server without relaxing the client.
        context.set_ciphers("ALL:@SECLEVEL=0")
    context.load_cert_chain(cert, key)
    context.set_servername_callback(
        lambda _socket, name, _context: events.append({"sni": name})
    )

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(5)
            try:
                with context.wrap_socket(self.request, server_side=True) as peer:
                    events.append(
                        {"handshake": peer.version(), "cipher": peer.cipher()[0]}
                    )
                    # Wait for the client's EOF. No HTTP/application exchange.
                    peer.recv(1)
            except (OSError, ssl.SSLError):
                events.append({"handshake": "failed"})

    server = _Server(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], ca, events
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


@contextmanager
def connect_gateway(port: int, *, bind="127.0.0.1", listen_port=3128):
    """Fixture-only exact CONNECT relay. No DNS or ambient proxy lookup."""
    events = []
    authority = f"{HOST}:{port}"

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(5)
            header = b""
            while b"\r\n\r\n" not in header and len(header) < 8192:
                part = self.request.recv(1)
                if not part:
                    return
                header += part
            first = header.split(b"\r\n", 1)[0].decode("ascii", errors="replace")
            pieces = first.split(" ")
            allowed = len(pieces) == 3 and pieces[:2] == ["CONNECT", authority]
            events.append({"request": first, "allowed": allowed})
            if not allowed:
                self.request.sendall(
                    b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n"
                )
                return
            with socket.create_connection(("127.0.0.1", port), timeout=5) as upstream:
                self.request.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                while True:
                    ready, _, _ = select.select([self.request, upstream], [], [], 5)
                    if not ready:
                        return
                    for source in ready:
                        data = source.recv(65536)
                        if not data:
                            return
                        (upstream if source is self.request else self.request).sendall(
                            data
                        )

    server = _Server((bind, listen_port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield events
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def qualify(output: Path):
    from moonmind.schemas.repository_scanner_models import TLSAssessment
    from moonmind.security.tls_assessment_runner import inspect_tls
    from moonmind.security.tls_assessments import (
        build_tls_assessment_job,
        sanitize_openssl_version,
        summarize_tls_assessment,
    )

    output.mkdir(parents=True, exist_ok=True)
    results = {}
    for scenario in (
        "valid",
        "expired",
        "wrong-host",
        "untrusted",
        "private-ca",
        "legacy",
    ):
        root = output / scenario
        with (
            tempfile.TemporaryDirectory(prefix="moonmind-tls-fixture-") as private,
            tls_server(
                Path("/qualification/private-fixture")
                if scenario == "private-ca"
                else Path(private),
                existing=scenario == "private-ca",
                hostname="wrong.example.test" if scenario == "wrong-host" else HOST,
                expired=scenario == "expired",
                legacy=scenario == "legacy",
            ) as (port, ca, server_events),
            connect_gateway(port) as gateway_events,
        ):
            inputs = root / "inputs"
            (inputs / "tls-trust").mkdir(parents=True)
            (inputs / "tls-trust" / "ca.pem").write_bytes(ca.read_bytes())
            a = TLSAssessment(
                origin=f"https://{HOST}:{port}",
                egress_policy_ref="fixture",
                protocols=["TLSv1"] if scenario == "legacy" else ["TLSv1.2", "TLSv1.3"],
                trust_artifact_ref=None if scenario == "untrusted" else "art_fixtureca",
                max_requests_per_second=10,
            )
            env = {
                **os.environ,
                "MOONMIND_EGRESS_PROXY": "http://127.0.0.1:3128",
                "HTTP_PROXY": "http://hostile:1",
                "HTTPS_PROXY": "http://hostile:1",
                "ALL_PROXY": "http://hostile:1",
                "NO_PROXY": "*",
            }
            execution = inspect_tls(
                a, root / "result", inputs=inputs, environ=env, assessed_at=CLOCK
            )
            summary = summarize_tls_assessment(a, execution, "art_fixture")
            assert execution["tool"]["version"] not in {None, "unavailable"}
            probes = execution["probes"]
            assert probes
            if scenario == "legacy":
                assert probes[0]["status"] in {
                    "observed",
                    "unsupported_client",
                    "blocked_handshake",
                    "server_refused",
                }
                # A genuinely negotiated server handshake must never become a
                # blocked result merely because the diagnostic spells TLSv1.0.
                if any(
                    e.get("handshake") in {"TLSv1", "TLSv1.0"} for e in server_events
                ):
                    assert probes[0]["status"] == "observed", probes[0]
                if probes[0]["status"] == "observed":
                    assert "legacy_protocol_accepted" in probes[0]["findings"]
                else:
                    assert not summary.findings
            else:
                assert all(p["status"] == "observed" for p in probes), probes
                expected = {
                    "expired": "certificate_expired",
                    "wrong-host": "hostname_mismatch",
                    "untrusted": "certificate_trust_failed",
                }.get(scenario)
                assert all(len(p["certificates"]) == 2 for p in probes)
                if expected:
                    assert all(expected in p["findings"] for p in probes)
                else:
                    assert not summary.findings and all(
                        p["trustResult"] == "trusted" for p in probes
                    )
            # Same adapter, hostile ambient configuration, disallowed authority.
            before = sum("sni" in e for e in server_events)
            for origin in (
                f"https://other.example.test:{port}",
                f"https://{HOST}:{port + 1}",
            ):
                denied = a.model_copy(
                    update={"origin": origin, "protocols": ["TLSv1.2"]}
                )
                blocked = inspect_tls(
                    denied,
                    root / ("deny-host" if "other" in origin else "deny-port"),
                    inputs=inputs,
                    environ=env,
                    assessed_at=CLOCK,
                )
                assert blocked["probes"][0]["status"] == "blocked_handshake"
            assert sum("sni" in e for e in server_events) == before
            assert all(e["sni"] == HOST for e in server_events if "sni" in e)
            assert sum(not e["allowed"] for e in gateway_events) == 2
            assert all(
                e["request"].startswith(f"CONNECT {HOST}:{port} ")
                for e in gateway_events
                if e["allowed"]
            )
            if scenario != "legacy":
                assert any(
                    e.get("handshake") in {"TLSv1.2", "TLSv1.3"} for e in server_events
                )
            if scenario in {"valid", "private-ca"}:
                # Real canonical module CLI, no injected runner function.
                cli = a.model_copy(
                    update={
                        "trust_artifact_ref": "art_fixtureca"
                        if scenario == "private-ca"
                        else None,
                        "protocols": ["TLSv1.2"],
                    }
                )
                job = build_tls_assessment_job(cli)
                command = [*job["entrypoint"], *job["command"]]
                command[-1] = str(root / "cli")
                done = subprocess.run(
                    command, env=env, timeout=30, check=False, capture_output=True
                )
                assert done.returncode == 0, done.stderr.decode()
                saved = json.loads((root / "cli" / "execution.json").read_text())
                assert saved["tool"]["version"] not in {None, "unavailable"}
                assert (
                    sanitize_openssl_version(saved["tool"]["version"])
                    == saved["tool"]["version"]
                )
                assert saved["probes"][0]["status"] == "observed"
                assert saved["probes"][0]["trustResult"] == (
                    "trusted" if scenario == "private-ca" else "untrusted"
                )
                if scenario == "private-ca":
                    assert saved["trust"]["source"] == "operator"
                    assert saved["trust"]["artifactRef"] == "art_fixtureca"
                    assert saved["trust"]["sha256"] == execution["trust"]["sha256"]
                    try:
                        fd = os.open("/inputs/tls-trust/ca.pem", os.O_WRONLY)
                    except OSError:
                        pass
                    else:
                        os.close(fd)
                        raise AssertionError("private trust mount must be read-only")
            results[scenario] = {
                "summary": summary.model_dump(mode="json", by_alias=True),
                "gatewayEvents": list(gateway_events),
                "serverEvents": list(server_events),
            }
    (output / "qualification.json").write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    import sys

    qualify(Path(sys.argv[1]))
