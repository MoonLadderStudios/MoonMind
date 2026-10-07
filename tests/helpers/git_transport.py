"""Synthetic local GitHub HTTPS transport for real Git/gh credential tests.

A loopback HTTPS proxy terminates ``CONNECT github.com:443`` with a test CA and
serves a bare repository over Git's dumb HTTP protocol. Every request's
``Authorization`` and other headers are recorded, so a test can prove which
credential a real ``git`` process actually sent, without network access or a
live account.
"""

from __future__ import annotations

import base64
import datetime
import http.server
import socketserver
import ssl
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator
from urllib.parse import urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


def basic_authorization(token: str) -> str:
    raw = f"x-access-token:{token}".encode()
    return "Basic " + base64.b64encode(raw).decode()


def _write_certificates(directory: Path, host: str) -> tuple[Path, Path, Path]:
    now = datetime.datetime.now(datetime.timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "MoonMind test CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), False
        )
        .sign(ca_key, hashes.SHA256())
    )
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)]))
        .issuer_name(ca_name)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    ca_path = directory / "ca.pem"
    cert_path = directory / "leaf.pem"
    key_path = directory / "leaf-key.pem"
    ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    cert_path.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        leaf_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return ca_path, cert_path, key_path


@dataclass
class RecordedRequest:
    path: str
    headers: dict[str, str]

    @property
    def authorization(self) -> str | None:
        return self.headers.get("authorization")


@dataclass
class SyntheticGithubTransport:
    """Loopback GitHub HTTPS endpoint reachable through ``proxy_url``."""

    repository_root: Path
    ca_path: Path
    proxy_url: str
    required_token: str | None
    requests: list[RecordedRequest] = field(default_factory=list)

    def sent_authorizations(self) -> list[str]:
        return [item.authorization for item in self.requests if item.authorization]

    def header_values(self) -> list[str]:
        return [value for item in self.requests for value in item.headers.values()]


def _dumb_http_handler(transport: SyntheticGithubTransport):
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args) -> None:  # pragma: no cover - quiet
            return

        def do_GET(self) -> None:  # noqa: N802 - http.server API
            transport.requests.append(
                RecordedRequest(
                    path=self.path,
                    headers={key.lower(): value for key, value in self.headers.items()},
                )
            )
            required = transport.required_token
            if required is not None and self.headers.get(
                "Authorization"
            ) != basic_authorization(required):
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="GitHub"')
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            relative = urlsplit(self.path).path.lstrip("/")
            target = (transport.repository_root / relative).resolve()
            if (
                not target.is_relative_to(transport.repository_root.resolve())
                or not target.is_file()
            ):
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def _connect_proxy_handler(
    transport: SyntheticGithubTransport, context: ssl.SSLContext
):
    handler = _dumb_http_handler(transport)

    class Proxy(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            self.request.settimeout(10)
            stream = self.request.makefile("rb")
            request_line = stream.readline().decode("latin-1")
            while stream.readline() not in (b"\r\n", b"\n", b""):
                pass
            method, _, rest = request_line.partition(" ")
            if method != "CONNECT" or not rest.startswith("github.com:443 "):
                self.request.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\n")
                return
            self.request.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            tls = context.wrap_socket(self.request, server_side=True)
            try:
                handler(tls, self.client_address, self.server)
            except (ssl.SSLError, ConnectionError, OSError):
                return
            finally:
                try:
                    tls.close()
                except OSError:
                    pass

    return Proxy


def build_bare_repository(root: Path, *, name: str = "owner/repo.git") -> Path:
    """Create ``root/name`` with one commit on ``main``, served as dumb HTTP."""

    work = root / "seed"
    work.mkdir(parents=True)
    git = ["git", "-c", "init.defaultBranch=main"]
    subprocess.run([*git, "init", "-q", str(work)], check=True)
    (work / "README.md").write_text("synthetic transport\n")
    subprocess.run(["git", "-C", str(work), "add", "README.md"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(work),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-q",
            "-m",
            "seed",
        ],
        check=True,
    )
    bare = root / "served" / name
    bare.parent.mkdir(parents=True)
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True)
    subprocess.run(["git", "-C", str(bare), "repack", "-q", "-a", "-d"], check=True)
    subprocess.run(["git", "-C", str(bare), "update-server-info"], check=True)
    return root / "served"


def start_synthetic_github(
    directory: Path, *, required_token: str | None
) -> Iterator[SyntheticGithubTransport]:
    """Serve a synthetic ``https://github.com/owner/repo.git`` until closed."""

    directory.mkdir(parents=True, exist_ok=True)
    served = build_bare_repository(directory / "repository")
    ca_path, cert_path, key_path = _write_certificates(directory, "github.com")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)

    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = True
        block_on_close = False
        allow_reuse_address = True

    transport = SyntheticGithubTransport(
        repository_root=served,
        ca_path=ca_path,
        proxy_url="",
        required_token=required_token,
    )
    server = Server(("127.0.0.1", 0), _connect_proxy_handler(transport, context))
    transport.proxy_url = "http://127.0.0.1:%d" % server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield transport
    finally:
        server.shutdown()
        server.server_close()


def write_proxy_git_shim(directory: Path, transport: SyntheticGithubTransport) -> Path:
    """Return a PATH directory whose ``git`` routes through the transport.

    The shim adds only transport routing and trust (proxy and CA); credentials,
    helpers, and headers remain whatever the production caller configured.
    """

    import shutil

    real_git = shutil.which("git")
    assert real_git is not None
    shim_dir = directory / "git-shim"
    shim_dir.mkdir(parents=True, exist_ok=True)
    shim = shim_dir / "git"
    shim.write_text(
        "#!/bin/sh\n"
        f"exec {real_git} -c http.proxy={transport.proxy_url} "
        f'-c http.sslCAInfo={transport.ca_path} "$@"\n'
    )
    shim.chmod(0o755)
    return shim_dir


__all__ = [
    "RecordedRequest",
    "SyntheticGithubTransport",
    "basic_authorization",
    "build_bare_repository",
    "start_synthetic_github",
    "write_proxy_git_shim",
]
