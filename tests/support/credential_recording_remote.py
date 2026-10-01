"""A loopback HTTPS Git remote that records which credential reached it.

Real ``git`` and ``gh`` only hand credentials to an ``https`` remote, so
credential-precedence tests need TLS to observe the transport rather than a
configuration read. The remote challenges every request with Basic auth and
records each ``Authorization`` header; tests assert which synthetic secret
arrived. No provider is contacted.
"""

from __future__ import annotations

import datetime
import ipaddress
import ssl
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Iterator

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


def _write_self_signed_loopback_certificate(directory: Path) -> tuple[Path, Path]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    certificate_path = directory / "remote-cert.pem"
    key_path = directory / "remote-key.pem"
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return certificate_path, key_path


class RecordingRemote:
    def __init__(self, *, url: str, host: str, ca_file: Path) -> None:
        self.url = url
        self.host = host
        self.ca_file = ca_file
        self.authorization_headers: list[str] = []


@contextmanager
def credential_recording_remote(
    directory: Path, *, redirect_to: str | None = None
) -> Iterator[RecordingRemote]:
    """Serve the remote; ``redirect_to`` answers every request with a redirect
    to that base URL plus the requested path instead of a challenge."""

    directory.mkdir(parents=True, exist_ok=True)
    certificate_path, key_path = _write_self_signed_loopback_certificate(directory)
    recorded: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            recorded.extend(self.headers.get_all("Authorization") or [])
            if redirect_to is not None:
                self.send_response(302)
                self.send_header("Location", f"{redirect_to}{self.path}")
            else:
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="recording-remote"')
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate_path, key_path)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host = f"127.0.0.1:{server.server_port}"
    remote = RecordingRemote(url=f"https://{host}", host=host, ca_file=certificate_path)
    remote.authorization_headers = recorded
    try:
        yield remote
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


__all__ = ["RecordingRemote", "credential_recording_remote"]
