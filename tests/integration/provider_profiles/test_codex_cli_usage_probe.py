"""Qualify the installed Codex CLI behind the usage probe (MoonLadderStudios/MoonMind#5364).

The real ``codex app-server`` runs the probe's read and recovery against a
local TLS server that plays the ChatGPT backend and the OAuth token endpoint.
Nothing leaves the machine and no real credentials are used. The test proves
the CLI's own lifecycle: an expired access token is renewed once and the
rotated refresh token persists in the enrolled home, a rejected token is
renewed only by the recovery action, and no model request is made.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import ssl
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from moonmind.provider_profiles.codex_account_usage import (
    PROBE_SOURCE,
    parse_probe_output,
)

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]

CODEX = shutil.which("codex")
RENEWED = "renewed"
RESET = 1795500000


def _jwt(claims: dict) -> str:
    def part(value: dict) -> str:
        return (
            base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()
        )

    return f"{part({'alg': 'none', 'typ': 'JWT'})}.{part(claims)}.sig"


def _id_token(now: int) -> str:
    return _jwt(
        {
            "email": "person@example.com",
            "exp": now + 3600,
            "iat": now,
            "https://api.openai.com/auth": {
                "chatgpt_plan_type": "plus",
                "chatgpt_account_id": "acct-1111",
                "chatgpt_user_id": "user-1",
            },
        }
    )


def _tls(directory: Path) -> tuple[Path, ssl.SSLContext]:
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
        .sign(ca_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    leaf = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")]))
        .issuer_name(ca_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(hours=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(__import__("ipaddress").ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    ca_path = directory / "ca.pem"
    leaf_path, key_path = directory / "leaf.pem", directory / "leaf.key"
    ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    leaf_path.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(leaf_path, key_path)
    return ca_path, context


class FakeChatGPT:
    """The backend routes ``account/read`` and ``account/rateLimits/read`` use."""

    def __init__(self, tls: ssl.SSLContext, *, refresh_accepted: bool, valid: str):
        self.refresh_accepted = refresh_accepted
        self.valid = {valid}
        self.calls: list[tuple[str, str]] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _reply(self, status: int, body: dict) -> None:
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                path = self.path.split("?", 1)[0]
                fake.calls.append(("GET", path))
                token = self.headers.get("authorization", "").removeprefix("Bearer ")
                if path.endswith("/wham/accounts/check"):
                    return self._reply(200, fake.accounts())
                if path.endswith("/wham/usage"):
                    if token not in fake.valid:
                        return self._reply(401, {"error": {"code": "token_expired"}})
                    return self._reply(200, fake.usage())
                return self._reply(404, {})

            def do_POST(self):
                self.rfile.read(int(self.headers.get("content-length") or 0))
                path = self.path.split("?", 1)[0]
                fake.calls.append(("POST", path))
                if path != "/oauth/token":
                    return self._reply(404, {})
                if not fake.refresh_accepted:
                    return self._reply(400, {"error": "invalid_grant"})
                now = int(time.time())
                renewed = _jwt({"exp": now + 3600, "iat": now, "n": RENEWED})
                fake.valid.add(renewed)
                return self._reply(
                    200,
                    {
                        "id_token": _id_token(now),
                        "access_token": renewed,
                        "refresh_token": "refresh-rotated",
                    },
                )

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.socket = tls.wrap_socket(self.server.socket, server_side=True)
        self.origin = f"https://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def accounts(self) -> dict:
        return {
            "accounts": [
                {
                    "id": "acct-1111",
                    "name": "Example Workspace",
                    "plan_type": "plus",
                    "structure": "workspace",
                    "workspace_backend_origin": self.origin,
                    "account_routing_override": "NO_CONSTRAINT",
                }
            ]
        }

    @staticmethod
    def usage() -> dict:
        window = {"limit_window_seconds": 604800, "reset_after_seconds": 60}
        return {
            "plan_type": "plus",
            "account_id": "acct-1111",
            "rate_limit": {
                "allowed": True,
                "limit_reached": False,
                "primary_window": {
                    "used_percent": 12,
                    "limit_window_seconds": 18000,
                    "reset_after_seconds": 60,
                    "reset_at": RESET - 3600,
                },
                "secondary_window": {**window, "used_percent": 40, "reset_at": RESET},
            },
            "credits": {"has_credits": False, "unlimited": False, "balance": "0"},
        }

    def close(self) -> None:
        self.server.shutdown()


@pytest.fixture
def codex_home(tmp_path):
    if CODEX is None:
        pytest.skip("codex CLI is not installed on this test path")
    ca, tls = _tls(tmp_path)
    servers: list[FakeChatGPT] = []

    def start(*, expired: bool, refresh_accepted: bool = True, login: bool = True):
        now = int(time.time())
        access = _jwt({"exp": now - 60 if expired else now + 3600, "iat": now - 7200})
        fake = FakeChatGPT(
            tls, refresh_accepted=refresh_accepted, valid="" if expired else access
        )
        servers.append(fake)
        home = tmp_path / f"home-{len(servers)}"
        home.mkdir()
        (home / "config.toml").write_text(
            f'chatgpt_base_url = "{fake.origin}/backend-api/"\n'
        )
        if login:
            (home / "auth.json").write_text(
                json.dumps(
                    {
                        "OPENAI_API_KEY": None,
                        "tokens": {
                            "id_token": _id_token(now),
                            "access_token": access,
                            "refresh_token": "refresh-original",
                            "account_id": "acct-1111",
                        },
                        "last_refresh": "2026-11-18T00:00:00Z",
                    }
                )
            )
        env = {
            key: value
            for key, value in os.environ.items()
            if key.lower() not in {"http_proxy", "https_proxy", "all_proxy"}
        }
        env.update(
            CODEX_CA_CERTIFICATE=str(ca),
            SSL_CERT_FILE=str(ca),
            CODEX_REFRESH_TOKEN_URL_OVERRIDE=f"{fake.origin}/oauth/token",
            NO_PROXY="127.0.0.1,localhost",
            no_proxy="127.0.0.1,localhost",
        )

        def probe(mode: str) -> dict:
            completed = subprocess.run(
                [sys.executable, "-c", PROBE_SOURCE, mode, str(home)],
                env=env,
                capture_output=True,
                timeout=120,
                check=False,
            )
            assert completed.returncode == 0, completed.stderr
            return json.loads(completed.stdout.decode().strip().splitlines()[-1])

        def stored_refresh() -> str | None:
            path = home / "auth.json"
            if not path.exists():
                return None
            return json.loads(path.read_text())["tokens"]["refresh_token"]

        return fake, probe, stored_refresh

    yield start
    for server in servers:
        server.close()


def _model_calls(fake: FakeChatGPT) -> list:
    return [call for call in fake.calls if "/responses" in call[1]]


def test_read_reports_identity_and_weekly_allowance_without_renewal(
    codex_home,
) -> None:
    fake, probe, stored_refresh = codex_home(expired=False)

    output = probe("read")

    assert output["outcome"] == "observed", output
    report = parse_probe_output(0, json.dumps(output).encode()).report
    assert report.usage.primary_window_key == "codex.weekly"
    weekly = {w.key: w for w in report.usage.windows}["codex.weekly"]
    assert weekly.used_percent == 40
    assert weekly.reset_at == datetime.fromtimestamp(RESET, UTC)
    assert report.account_id == "acct-1111"
    assert report.identity.email == "person@example.com"
    assert report.identity.account_id == "acct-1111"
    assert ("POST", "/oauth/token") not in fake.calls
    assert stored_refresh() == "refresh-original"
    assert _model_calls(fake) == []


def test_an_expired_token_is_renewed_once_and_the_rotation_persists(
    codex_home,
) -> None:
    fake, probe, stored_refresh = codex_home(expired=True)

    output = probe("read")

    assert output["outcome"] == "observed", output
    assert fake.calls.count(("POST", "/oauth/token")) == 1
    assert stored_refresh() == "refresh-rotated"
    assert _model_calls(fake) == []


def test_a_rejected_token_is_renewed_only_by_the_recovery_action(codex_home) -> None:
    fake, probe, stored_refresh = codex_home(expired=False)
    fake.valid.clear()

    assert probe("read") == {"v": 1, "outcome": "recovery_required"}
    assert ("POST", "/oauth/token") not in fake.calls

    output = probe("recover")

    assert output["outcome"] == "observed", output
    assert fake.calls.count(("POST", "/oauth/token")) == 1
    assert stored_refresh() == "refresh-rotated"


def test_a_rejected_refresh_token_is_reported_without_a_retry_loop(codex_home) -> None:
    fake, probe, stored_refresh = codex_home(expired=True, refresh_accepted=False)

    assert probe("read") == {"v": 1, "outcome": "recovery_required"}
    renewals = fake.calls.count(("POST", "/oauth/token"))
    output = probe("recover")

    assert output == {
        "v": 1,
        "outcome": "failed",
        "failure": "credential_refresh_unavailable",
    }
    # One bounded native attempt per action; the stored login is untouched.
    assert renewals >= 1
    assert fake.calls.count(("POST", "/oauth/token")) <= 2 * renewals + 1
    assert stored_refresh() == "refresh-original"


def test_a_home_without_a_login_requires_reconnect(codex_home) -> None:
    fake, probe, _stored_refresh = codex_home(expired=False, login=False)

    assert probe("read") == {
        "v": 1,
        "outcome": "failed",
        "failure": "reconnect_required",
    }
    assert _model_calls(fake) == []
