"""One bounded, proxy-only OpenSSL TLS inspection; no direct fallback.

Unverified handshakes capture the presented chain. Trust is evaluated offline
against that same chain at the recorded assessment time, never a second peer.
Only parsed evidence is published; session keys and raw diagnostics stay private.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import ssl
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization

from moonmind.security.egress_policy import tool_proxy_options
from moonmind.security.repository_scanner_runner import BoundedProcesses
from moonmind.security.tls_assessments import TLSAssessment, sanitize_openssl_version

_FLAGS = {
    "TLSv1": "-tls1",
    "TLSv1.1": "-tls1_1",
    "TLSv1.2": "-tls1_2",
    "TLSv1.3": "-tls1_3",
}
_PEM = re.compile(
    rb"-----BEGIN CERTIFICATE-----\s+[A-Za-z0-9+/=\r\n]+-----END CERTIFICATE-----"
)
MAX_BYTES = 2 * 1024 * 1024


def probe_command(
    a: TLSAssessment, protocol: str, cipher: str | None, environ: dict
) -> list[str]:
    proxy = tool_proxy_options("httpx", environ=environ)["proxy"]
    u = urlsplit(a.origin)
    if protocol not in a.protocols or (cipher is not None and cipher not in a.ciphers):
        raise ValueError("unadmitted TLS probe")
    cmd = [
        "openssl",
        "s_client",
        "-proxy",
        proxy.removeprefix("http://"),
        "-connect",
        f"{u.hostname}:{u.port or 443}",
        "-servername",
        u.hostname,
        "-showcerts",
        "-nameopt",
        "RFC2253",
        _FLAGS[protocol],
    ]
    if cipher:
        cmd.extend(["-ciphersuites" if protocol == "TLSv1.3" else "-cipher", cipher])
    return cmd


def classify_probe(text: str, code: int | None, state: str, negotiated: bool) -> str:
    # A confirmed native negotiation wins over subsequent EOF, timeouts and
    # peer-controlled names. Only OpenSSL's error-stack reason field supplies
    # failure classification; source paths, data fields and DNs are not reasons.
    if negotiated:
        return "observed"
    if state in {"timed_out", "canceled"}:
        return "transient_failure"
    reasons = {
        match.group(1).lower()
        for match in re.finditer(
            r"^[0-9A-Fa-f]+:error:[0-9A-Fa-f]+:[^:\r\n]*:[^:\r\n]*:([^:\r\n]*):",
            text,
            re.MULTILINE,
        )
    }
    client_reasons = {
        "no protocols available",
        "no ciphers available",
        "no cipher match",
        "unsupported protocol",
        "library has no ciphers",
        "legacy sigalg disallowed",
        "no suitable signature algorithm",
        "ee key too small",
        "ca md too weak",
        "dh key too small",
        "unsafe legacy renegotiation disabled",
    }
    if reasons & client_reasons or re.search(
        r"^s_client: Unknown option: -[A-Za-z0-9_]+$", text, re.MULTILINE
    ):
        return "unsupported_client"
    if "tlsv1 alert protocol version" in reasons:
        return "server_refused"
    if (
        reasons
        & {
            "connection refused",
            "connection timed out",
            "temporary failure",
            "name or service not known",
        }
        or re.search(r"^connect:errno=[0-9]+$", text, re.MULTILINE)
        or state == "failed"
    ):
        return "transient_failure"
    return "blocked_handshake"


def native_handshake(stdout: bytes) -> tuple[bytes, str | None, str | None]:
    """Read native chain/summary before application output, never peer text.

    RFC2253 name rendering escapes newlines, so a DN cannot create these native
    section boundaries. Session data/application output after the first summary
    cannot add a chain entry or replace a negotiated protocol/cipher.
    """
    text = stdout.decode(errors="replace")
    section = re.search(
        r"^Certificate chain\r?\n(.*?)^---\r?$", text, re.MULTILINE | re.DOTALL
    )
    if section is None:
        return b"", None, None
    line = re.search(r"^New, [^\r\n]*", text[section.end() :], re.MULTILINE)
    summary = re.fullmatch(
        r"New, (TLSv1(?:\.[0-3])?), Cipher is ([A-Za-z0-9_-]+)",
        line.group(0) if line else "",
    )
    if summary is None:
        return section.group(1).encode(), None, None
    protocol, cipher = summary.groups()
    if protocol == "TLSv1.0":
        protocol = "TLSv1"
    return (
        section.group(1).encode(),
        protocol,
        None if cipher in {"NONE", "0000"} else cipher,
    )


def certificate_evidence(
    raw: bytes, host: str, assessed_at: datetime
) -> tuple[list[dict], list[str], bool | None]:
    chain, findings = [], []
    hostname_match = None
    certificates = _PEM.findall(raw)
    if len(certificates) > 32 or len(raw) >= MAX_BYTES:
        raise ValueError("TLS certificate evidence bound exceeded")
    for index, pem in enumerate(certificates):
        cert = x509.load_pem_x509_certificate(pem)
        before, after = cert.not_valid_before_utc, cert.not_valid_after_utc
        valid = before <= assessed_at <= after
        chain.append(
            {
                "pem": cert.public_bytes(serialization.Encoding.PEM).decode(),
                "sha256": cert.fingerprint(hashes.SHA256()).hex(),
                "notBefore": before.isoformat(),
                "notAfter": after.isoformat(),
                "validAtAssessment": valid,
            }
        )
        if not valid:
            findings.append(
                "certificate_expired"
                if assessed_at > after
                else "certificate_not_yet_valid"
            )
        if index == 0:
            try:
                sans = cert.extensions.get_extension_for_class(
                    x509.SubjectAlternativeName
                ).value.get_values_for_type(x509.DNSName)
            except x509.ExtensionNotFound:
                sans = []
            # Modern HTTPS identity requires SAN; no common-name fallback.
            hostname_match = any(
                name.lower() == host.lower()
                or (
                    name.startswith("*.")
                    and name.count("*") == 1
                    and len(name.split(".")) >= 3
                    and len(name.split(".")) == len(host.split("."))
                    and name[2:].lower() == host.partition(".")[2].lower()
                )
                for name in sans
            )
            if not hostname_match:
                findings.append("hostname_mismatch")
    return chain, list(dict.fromkeys(findings)), hostname_match


def _write(path: Path, value: dict) -> None:
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, sort_keys=True))
    temp.replace(path)


def _read(path: Path) -> bytes:
    return path.read_bytes()[:MAX_BYTES] if path.exists() else b""


def _trust_material(a: TLSAssessment, inputs: Path) -> tuple[dict, bytes | None]:
    if not a.trust_artifact_ref:
        # Retain the actual default CA bundle, not merely the machine path.
        defaults = ssl.get_default_verify_paths()
        path = Path(defaults.cafile) if defaults.cafile else None
        source = "system"
    else:
        path = inputs / "tls-trust" / "ca.pem"
        source = "operator"
    if (
        path is None
        or (source == "operator" and path.is_symlink())
        or not path.is_file()
        or path.stat().st_size > 1048576
    ):
        raise ValueError("trust_source_unavailable")
    raw = path.read_bytes()
    certs = _PEM.findall(raw)
    remainder = _PEM.sub(b"", raw).strip()
    if not certs or (source == "operator" and remainder):
        raise ValueError("trust_source_must_contain_only_public_certificates")
    # Parse before executing. Never preserve private keys or arbitrary input.
    clean = b"\n".join(
        x509.load_pem_x509_certificate(p).public_bytes(serialization.Encoding.PEM)
        for p in certs
    )
    return {
        "source": source,
        "artifactRef": a.trust_artifact_ref,
        "sha256": hashlib.sha256(clean).hexdigest(),
        "certificatesPem": clean.decode(),
    }, clean


def inspect_tls(
    a: TLSAssessment,
    output: Path,
    *,
    inputs: Path = Path("/inputs"),
    environ: dict | None = None,
    assessed_at: datetime | None = None,
    processes=None,
) -> dict:
    environ = dict(os.environ if environ is None else environ)
    # Fail closed before any process, including capability/version probing.
    probe_command(a, a.protocols[0], None, environ)
    assessed_at = assessed_at or datetime.now(UTC)
    if assessed_at.tzinfo is None:
        raise ValueError("assessment clock must be timezone aware")
    output.mkdir(parents=True, exist_ok=True)
    execution = {
        "origin": a.origin,
        "startedAt": assessed_at.isoformat(),
        "finishedAt": None,
        "tool": {"name": "OpenSSL", "version": None},
        "trust": {},
        "probes": [],
        "errors": [],
        "resolution": {"status": "proxy_pinned", "backendAddress": None},
        "settings": a.model_dump(mode="json", by_alias=True),
    }
    processes = processes or BoundedProcesses(a.timeout_seconds, MAX_BYTES)
    env = {
        "PATH": environ.get("PATH", "/usr/bin:/bin"),
        "LC_ALL": "C",
        "HOME": "/nonexistent",
        "OPENSSL_CONF": "/dev/null",
    }
    with tempfile.TemporaryDirectory(prefix="moonmind-tls-") as temp:
        root = Path(temp)
        capture, errors = root / "stdout", root / "stderr"
        try:
            trust, trust_bytes = _trust_material(a, inputs)
        except (ValueError, OSError):
            execution["errors"].append("trust_source_unavailable_or_invalid")
            _write(output / "execution.json", execution)
            return execution
        execution["trust"] = trust
        ca = root / "trust.pem"
        ca.write_bytes(trust_bytes)
        processes.run(
            ["openssl", "version"], cwd=root, env=env, capture=capture, errors=errors
        )
        version = _read(capture).decode(errors="replace").strip()
        execution["tool"]["version"] = sanitize_openssl_version(version)
        _write(output / "execution.json", execution)
        next_attempt = time.monotonic()
        for protocol in a.protocols:
            for cipher in [None, *a.ciphers]:
                if processes.stopped():
                    break
                delay = next_attempt - time.monotonic()
                if delay > 0:
                    time.sleep(
                        min(delay, max(0, processes.deadline - time.monotonic()))
                    )
                if processes.stopped():
                    break
                next_attempt = time.monotonic() + 1 / a.max_requests_per_second
                start = datetime.now(UTC).isoformat()
                # Each probe has its own cap so one stalled handshake cannot
                # consume the whole selected scan; all share the outer deadline.
                outer_deadline = processes.deadline
                processes.deadline = min(outer_deadline, time.monotonic() + 10)
                try:
                    code, state = processes.run(
                        probe_command(a, protocol, cipher, environ),
                        cwd=root,
                        env=env,
                        capture=capture,
                        errors=errors,
                    )
                finally:
                    processes.deadline = outer_deadline
                stdout, stderr = _read(capture), _read(errors)
                chain_output, selected_protocol, selected_cipher = native_handshake(
                    stdout
                )
                negotiated = bool(
                    selected_cipher
                    and _PEM.search(chain_output)
                    and selected_protocol == protocol
                )
                status = classify_probe(
                    stderr.decode(errors="replace"), code, state, negotiated
                )
                probe = {
                    "protocol": protocol,
                    "cipher": cipher,
                    "status": status,
                    "startedAt": start,
                    "finishedAt": datetime.now(UTC).isoformat(),
                    "selectedCipher": selected_cipher,
                    "selectedProtocol": selected_protocol,
                    "certificates": [],
                    "hostnameMatch": None,
                    "trustResult": "not_evaluated",
                    "findings": [],
                }
                try:
                    chain, findings, hostname = certificate_evidence(
                        chain_output, urlsplit(a.origin).hostname, assessed_at
                    )
                    probe.update(
                        certificates=chain, hostnameMatch=hostname, findings=findings
                    )
                    if chain:
                        (root / "leaf.pem").write_text(chain[0]["pem"])
                        args = [
                            "openssl",
                            "verify",
                            "-no-CApath",
                            "-no-CAstore",
                            "-CAfile",
                            str(ca),
                            "-purpose",
                            "sslserver",
                            "-attime",
                            str(int(assessed_at.timestamp())),
                        ]
                        if len(chain) > 1:
                            (root / "chain.pem").write_text(
                                "".join(c["pem"] for c in chain[1:])
                            )
                            args += ["-untrusted", str(root / "chain.pem")]
                        args += [str(root / "leaf.pem")]
                        verify_code, verify_state = processes.run(
                            args, cwd=root, env=env, capture=capture, errors=errors
                        )
                        probe["trustResult"] = (
                            "trusted"
                            if verify_code == 0 and verify_state == "succeeded"
                            else "untrusted"
                            if verify_code == 2 and verify_state == "succeeded"
                            else "incomplete"
                        )
                        if probe["trustResult"] == "untrusted":
                            probe["findings"].append("certificate_trust_failed")
                    if status == "observed" and protocol in {"TLSv1", "TLSv1.1"}:
                        probe["findings"].append("legacy_protocol_accepted")
                except ValueError:
                    probe["status"] = "incomplete_evidence"
                execution["probes"].append(probe)
                if len(json.dumps(execution).encode()) > 8 * 1024 * 1024 - 1024:
                    execution["probes"].pop()
                    execution["errors"].append("evidence_bound_exceeded")
                    processes.cancel()
                _write(output / "execution.json", execution)
        execution["finishedAt"] = datetime.now(UTC).isoformat()
        _write(output / "execution.json", execution)
    return execution


def main() -> int:
    a = TLSAssessment.model_validate_json(sys.argv[1])
    processes = BoundedProcesses(a.timeout_seconds, MAX_BYTES)
    signal.signal(signal.SIGTERM, processes.cancel)
    signal.signal(signal.SIGINT, processes.cancel)
    try:
        inspect_tls(a, Path(sys.argv[2]), processes=processes)
    except (ValueError, OSError):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
