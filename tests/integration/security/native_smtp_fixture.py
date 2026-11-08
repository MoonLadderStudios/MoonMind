"""Disposable immediate-greeting SMTP/STARTTLS target, never a mail relay."""

from __future__ import annotations

import asyncio
import json
import ssl
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path


def certificate(directory):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "native.example.test")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
        .not_valid_after(datetime.now(UTC) + timedelta(hours=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("native.example.test")]),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(key, hashes.SHA256())
    )
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    cert_path, key_path = directory / "native-ca.pem", directory / "native-key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)
    return cert_path, key_path


class SMTPFixture:
    def __init__(self, cert, key, evidence=None):
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(cert, key)
        self.evidence = Path(evidence) if evidence else None
        self.counts = {
            "connections": 0,
            "starttls": 0,
            "noop": 0,
            "commands": [],
            "bytesReceived": 0,
            "bytesSent": 0,
        }
        self.writers = set()
        self.persist()

    def persist(self):
        if self.evidence:
            temporary = self.evidence.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.counts))
            temporary.replace(self.evidence)

    async def handle(self, reader, writer):
        self.writers.add(writer)
        self.counts["connections"] += 1
        secure = False

        async def reply(data):
            writer.write(data)
            await writer.drain()
            self.counts["bytesSent"] += len(data)
            self.persist()

        try:
            # Deliberately no greeting delay: actual clients must tolerate the
            # banner in the same packet as the proxy's CONNECT response.
            await reply(b"220 native.example.test ESMTP fixture\r\n")
            while line := await reader.readline():
                command = line.split(None, 1)[0].decode("ascii").upper()
                self.counts["commands"].append(command)
                self.counts["bytesReceived"] += len(line)
                self.persist()
                if command in {"EHLO", "HELO"}:
                    await reply(
                        b"250-native.example.test\r\n250 STARTTLS\r\n"
                        if not secure
                        else b"250 native.example.test\r\n"
                    )
                elif command == "STARTTLS" and not secure:
                    await reply(b"220 Begin TLS\r\n")
                    await writer.start_tls(self.context)
                    secure = True
                    self.counts["starttls"] += 1
                elif command == "NOOP" and secure:
                    self.counts["noop"] += 1
                    await reply(b"250 observed\r\n")
                elif command == "QUIT":
                    await reply(b"221 goodbye\r\n")
                    break
                else:
                    await reply(b"550 fixture only permits TLS observation\r\n")
        except (OSError, ValueError, asyncio.IncompleteReadError):
            pass
        finally:
            self.persist()
            writer.close()
            self.writers.discard(writer)
            try:
                await writer.wait_closed()
            except OSError:
                pass

    async def close(self):
        for writer in tuple(self.writers):
            writer.close()


def curl_command(proxy, cert, port=2525):
    return [
        "curl",
        "--silent",
        "--show-error",
        "--max-time",
        "8",
        "--noproxy",
        "",
        "--proxy",
        proxy,
        "--proxytunnel",
        "--user-agent",
        "",
        "--ssl-reqd",
        "--cacert",
        str(cert),
        "--request",
        "NOOP",
        f"smtp://native.example.test:{port}",
    ]


async def main():
    directory = Path(sys.argv[1])
    cert, key = certificate(directory)
    fixture = SMTPFixture(cert, key, directory / "counts.json")
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 2525
    server = await asyncio.start_server(fixture.handle, "0.0.0.0", port)
    (directory / "ready").write_text("ready")
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
