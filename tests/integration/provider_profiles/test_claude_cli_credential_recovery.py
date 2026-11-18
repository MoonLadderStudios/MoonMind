"""Qualify the installed Claude Code CLI's idle credential recovery (#5365).

The real pinned ``claude`` binary runs the probe's recovery action against a
local TLS-intercepting proxy that plays ``platform.claude.com`` and
``api.anthropic.com``. Nothing leaves the machine and no real credentials are
used. The test proves the CLI's own lifecycle: one refresh grant, an
atomically persisted rotation (or a cleared rejected token), and no model
request. It then proves the probe reads usage once with the renewed token.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import ssl
import stat
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from moonmind.provider_profiles import claude_account_usage_probe as probe
from moonmind.provider_profiles.claude_account_usage import parse_probe_output

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]

CLAUDE = shutil.which("claude")
HOSTS = ("platform.claude.com", "api.anthropic.com", "claude.ai")
RENEWED = "fake-access-renewed"


def _certificates(directory: Path) -> tuple[Path, ssl.SSLContext]:
    now = datetime.now(UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "moonmind test ca")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(hours=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_cert_sign=True,
                crl_sign=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    leaf = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, HOSTS[0])]))
        .issuer_name(ca_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(hours=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(host) for host in HOSTS]),
            critical=False,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    ca_path, leaf_path, key_path = (
        directory / "ca.pem",
        directory / "leaf.pem",
        directory / "leaf.key",
    )
    ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    leaf_path.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.load_cert_chain(leaf_path, key_path)
    return ca_path, context


class FakeAnthropic:
    """CONNECT proxy that terminates TLS and answers the OAuth routes."""

    def __init__(self, tls: ssl.SSLContext, *, refresh_accepted: bool) -> None:
        self.tls = tls
        self.refresh_accepted = refresh_accepted
        self.requests: list[dict] = []

    def answer(self, host: str, method: str, path: str, headers: dict) -> tuple:
        bearer = headers.get("authorization")
        if host == "platform.claude.com" and path.startswith("/v1/oauth/token"):
            if not self.refresh_accepted:
                return 400, {
                    "error": "invalid_grant",
                    "error_description": "Refresh token not found or invalid",
                }
            return 200, {
                "access_token": RENEWED,
                "refresh_token": "fake-refresh-rotated",
                "expires_in": 28800,
                "scope": "user:inference user:profile",
                "token_type": "Bearer",
            }
        route = path.split("?", 1)[0]
        if host != "api.anthropic.com" or route not in (
            probe.USAGE_PATH,
            probe.PROFILE_PATH,
        ):
            return 404, {}
        if bearer != f"Bearer {RENEWED}":
            return 401, {"type": "error", "error": {"type": "authentication_error"}}
        if route == probe.USAGE_PATH:
            return 200, {
                "seven_day": {"utilization": 42, "resets_at": "2026-11-20T09:00:00Z"}
            }
        return 200, {
            "account": {"uuid": "acct-1", "email": "person@example.com"},
            "organization": {"uuid": "org-1", "name": "Workspace"},
        }

    async def _serve(self, reader, writer, host: str) -> None:
        while line := await reader.readline():
            method, path, _ = line.decode().split(" ", 2)
            headers = {}
            while (header := await reader.readline()) not in (b"\r\n", b""):
                name, value = header.decode().split(":", 1)
                headers[name.strip().lower()] = value.strip()
            length = int(headers.get("content-length") or 0)
            body = await reader.readexactly(length) if length else b""
            self.requests.append(
                {"host": host, "method": method, "path": path, "body": body.decode()}
            )
            status, payload = self.answer(host, method, path, headers)
            data = json.dumps(payload).encode()
            writer.write(
                f"HTTP/1.1 {status} X\r\ncontent-type: application/json\r\n"
                f"content-length: {len(data)}\r\n\r\n".encode() + data
            )
            await writer.drain()

    async def connect(self, reader, writer) -> None:
        request = (await reader.readline()).decode().split()
        while (await reader.readline()) not in (b"\r\n", b""):
            pass
        try:
            if len(request) < 2 or request[0] != "CONNECT":
                return
            host = request[1].split(":")[0]
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await writer.drain()
            await writer.start_tls(self.tls)
            await self._serve(reader, writer, host)
        except (OSError, ssl.SSLError, ValueError, asyncio.IncompleteReadError):
            # The CLI may drop or abort a connection mid-exchange. The tests
            # assert on the recorded requests, not on connection teardown.
            pass
        finally:
            writer.close()


def _cli_version() -> str:
    return subprocess.run(
        [CLAUDE, "--version"], capture_output=True, text=True, timeout=60, check=False
    ).stdout.strip()


@contextlib.asynccontextmanager
async def _provider(tmp_path, monkeypatch, *, refresh_accepted: bool):
    ca_path, tls = _certificates(tmp_path)
    fake = FakeAnthropic(tls, refresh_accepted=refresh_accepted)
    server = await asyncio.start_server(fake.connect, "127.0.0.1", 0)
    proxy = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.setenv(name, proxy)
    for name in ("NO_PROXY", "no_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NODE_EXTRA_CA_CERTS", str(ca_path))
    monkeypatch.setenv("SSL_CERT_FILE", str(ca_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    async with server:
        yield fake


def _expired_home(tmp_path: Path) -> Path:
    home = tmp_path / ".claude"
    home.mkdir()
    credentials = home / ".credentials.json"
    credentials.write_text(
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": "fake-access-expired",
                    "refreshToken": "fake-refresh-original",
                    "expiresAt": int((time.time() - 3600) * 1000),
                    "scopes": ["user:inference", "user:profile"],
                    "subscriptionType": "max",
                }
            }
        )
    )
    credentials.chmod(0o600)
    return home


def _assert_no_model_request(fake: FakeAnthropic) -> None:
    assert not [
        request
        for request in fake.requests
        if "/v1/messages" in request["path"] or "/v1/complete" in request["path"]
    ]


@pytest.mark.skipif(CLAUDE is None, reason="Claude Code CLI is not installed")
async def test_idle_expiry_is_renewed_and_persisted_by_the_cli(
    tmp_path, monkeypatch
) -> None:
    home = _expired_home(tmp_path)
    async with _provider(tmp_path, monkeypatch, refresh_accepted=True) as fake:
        result = await asyncio.to_thread(probe.recover, str(home), command=CLAUDE)

    assert result["outcome"] == "observed", (_cli_version(), fake.requests)
    assert result["recovery"] == "renewed"
    refreshes = [r for r in fake.requests if r["path"].startswith("/v1/oauth/token")]
    assert len(refreshes) == 1
    assert json.loads(refreshes[0]["body"])["grant_type"] == "refresh_token"
    stored_path = home / ".credentials.json"
    stored = json.loads(stored_path.read_text())["claudeAiOauth"]
    assert stored["accessToken"] == RENEWED
    assert stored["refreshToken"] == "fake-refresh-rotated"
    assert stored["expiresAt"] > time.time() * 1000
    assert stat.S_IMODE(os.stat(stored_path).st_mode) == 0o600
    _assert_no_model_request(fake)
    # The probe read usage and identity once, with the renewed token.
    report = parse_probe_output(0, json.dumps({"v": 1, **result}).encode()).report
    assert report.account_id == "acct-1"
    assert report.usage.windows[0].used_percent == 42.0
    assert "fake-access" not in json.dumps(result)


@pytest.mark.skipif(CLAUDE is None, reason="Claude Code CLI is not installed")
async def test_a_rejected_refresh_token_is_cleared_once_and_requires_reconnect(
    tmp_path, monkeypatch
) -> None:
    home = _expired_home(tmp_path)
    async with _provider(tmp_path, monkeypatch, refresh_accepted=False) as fake:
        result = await asyncio.to_thread(probe.recover, str(home), command=CLAUDE)

    assert result == {
        "outcome": "failed",
        "failure": "reconnect_required",
        "recovery": "rejected",
    }, (_cli_version(), fake.requests)
    # One refresh attempt; the dead refresh token is never retried.
    assert (
        len([r for r in fake.requests if r["path"].startswith("/v1/oauth/token")]) == 1
    )
    stored = json.loads((home / ".credentials.json").read_text())["claudeAiOauth"]
    assert stored["refreshToken"] == "" and stored["accessToken"] == ""
    _assert_no_model_request(fake)
