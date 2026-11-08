"""Admitted TLS inspection never guesses negative server capability."""

import pytest

from moonmind.security.tls_assessment_runner import classify_probe, probe_command
from moonmind.security.tls_assessments import TLSAssessment


def test_missing_proxy_prevents_execution():
    with pytest.raises(ValueError, match="proxy"):
        probe_command(
            TLSAssessment(origin="https://example.com", egress_policy_ref="tls"),
            "TLSv1.2",
            None,
            {},
        )


def test_exact_connect_and_sni():
    a = TLSAssessment(origin="https://example.com:8443", egress_policy_ref="tls")
    cmd = probe_command(
        a, "TLSv1.2", None, {"MOONMIND_EGRESS_PROXY": "http://gateway:3128"}
    )
    assert cmd[cmd.index("-connect") + 1] == "example.com:8443"
    assert cmd[cmd.index("-servername") + 1] == "example.com"
    assert cmd[cmd.index("-proxy") + 1] == "gateway:3128"
    assert "-tls1_2" in cmd


def test_client_inability_is_not_server_refusal():
    assert (
        classify_probe(
            "40AB:error:0A0000BF:SSL routines:tls_setup_handshake:no protocols available:ssl/statem.c:1:",
            1,
            "succeeded",
            False,
        )
        == "unsupported_client"
    )
    assert (
        classify_probe(
            "40AB:error:0A00042E:SSL routines:ssl3_read_bytes:tlsv1 alert protocol version:ssl/record.c:1:",
            1,
            "succeeded",
            False,
        )
        == "server_refused"
    )
    assert (
        classify_probe("handshake failure", 1, "succeeded", False)
        == "blocked_handshake"
    )
    assert classify_probe("", None, "timed_out", False) == "transient_failure"


def test_https_only_no_caller_sni_or_cipher_expression():
    for kwargs in (
        {"origin": "http://example.com"},
        {"serverName": "other.example"},
        {"ciphers": ["ALL:@SECLEVEL=0"]},
    ):
        with pytest.raises(ValueError):
            TLSAssessment.model_validate(
                {"origin": "https://example.com", "egressPolicyRef": "tls", **kwargs}
            )


from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from moonmind.security.repository_scanner_runner import BoundedProcesses
from moonmind.security.tls_assessment_runner import inspect_tls
from moonmind.security.tls_assessments import (
    summarize_tls_assessment,
    validate_tls_execution,
)

NOW = datetime(2026, 6, 1, tzinfo=UTC)


def make_certificates(*, hostname="example.com", expired=False, common_name=None):
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Local TLS fixture")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(NOW - timedelta(days=365))
        .not_valid_after(NOW + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf = (
        x509.CertificateBuilder()
        .subject_name(
            x509.Name(
                [x509.NameAttribute(NameOID.COMMON_NAME, common_name or hostname)]
            )
        )
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(NOW - timedelta(days=10))
        .not_valid_after(NOW + timedelta(days=-1 if expired else 10))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    return leaf.public_bytes(serialization.Encoding.PEM), ca.public_bytes(
        serialization.Encoding.PEM
    )


class FixtureProcesses(BoundedProcesses):
    """No network: real offline OpenSSL verifies generated fixture chains."""

    def __init__(self, chain, *, responses=None):
        super().__init__(20, 2 * 1024 * 1024)
        self.chain = chain
        self.responses = list(responses or [])
        self.commands = []

    def run(self, argv, **kwargs):
        self.commands.append(argv)
        if argv[1] == "s_client":
            response = self.responses.pop(0) if self.responses else "observed"
            if response == "observed":
                kwargs["capture"].write_bytes(
                    b"CONNECTED(00000003)\n---\nCertificate chain\n"
                    + self.chain
                    + b"---\nServer certificate\nsubject=CN=fixture\nissuer=CN=fixture\n---\n"
                    + (
                        "\nNew, "
                        + {
                            "-tls1": "TLSv1.0",
                            "-tls1_1": "TLSv1.1",
                            "-tls1_2": "TLSv1.2",
                            "-tls1_3": "TLSv1.3",
                        }[next(v for v in argv if v.startswith("-tls"))]
                        + ", Cipher is ECDHE-RSA-AES256-GCM-SHA384\nMaster-Key: NEVER-RETAIN-SESSION-SECRET\n"
                    ).encode()
                )
                kwargs["errors"].write_bytes(b"")
                return 0, "succeeded"
            kwargs["capture"].write_bytes(b"")
            kwargs["errors"].write_text(
                "40AB:error:0A0000BF:SSL routines:fixture:"
                + response
                + ":ssl/statem.c:1:\n"
            )
            return 1, "succeeded"
        return super().run(argv, **kwargs)


@pytest.mark.parametrize(
    "hostname,expired,trusted,expected",
    [
        ("example.com", False, True, []),
        (
            "example.com",
            True,
            True,
            ["certificate_expired", "certificate_trust_failed"],
        ),
        ("wrong.example", False, True, ["hostname_mismatch"]),
        ("example.com", False, False, ["certificate_trust_failed"]),
    ],
)
def test_local_certificate_fixtures(tmp_path, hostname, expired, trusted, expected):
    leaf, ca = make_certificates(hostname=hostname, expired=expired)
    (tmp_path / "tls-trust").mkdir()
    (tmp_path / "tls-trust" / "ca.pem").write_bytes(
        ca if trusted else make_certificates()[1]
    )
    a = TLSAssessment(
        origin="https://example.com",
        egress_policy_ref="tls",
        protocols=["TLSv1.2"],
        trust_artifact_ref="art_ca",
    )
    processes = FixtureProcesses(leaf + ca)
    result = inspect_tls(
        a,
        tmp_path / "out",
        inputs=tmp_path,
        assessed_at=NOW,
        environ={
            "MOONMIND_EGRESS_PROXY": "http://gateway:3128",
            "PATH": "/usr/bin:/bin",
        },
        processes=processes,
    )
    assert result["probes"][0]["status"] == "observed"
    assert result["probes"][0]["findings"] == expected
    assert result["probes"][0]["trustResult"] == (
        "untrusted" if expired or not trusted else "trusted"
    )
    assert result["trust"]["certificatesPem"]
    assert len(result["probes"][0]["certificates"]) == 2
    assert validate_tls_execution(a, result) == result
    summary = summarize_tls_assessment(a, result, "art_evidence")
    assert summary.status == "complete"
    assert not summary.unperformed_checks
    assert (tmp_path / "out" / "execution.json").exists()
    assert (
        "NEVER-RETAIN-SESSION-SECRET"
        not in (tmp_path / "out" / "execution.json").read_text()
    )


def test_partial_probes_retain_success_and_client_limitations(tmp_path):
    leaf, ca = make_certificates()
    (tmp_path / "tls-trust").mkdir()
    (tmp_path / "tls-trust" / "ca.pem").write_bytes(ca)
    a = TLSAssessment(
        origin="https://example.com",
        egress_policy_ref="tls",
        protocols=["TLSv1", "TLSv1.2"],
        trust_artifact_ref="art_ca",
        max_requests_per_second=10,
    )
    result = inspect_tls(
        a,
        tmp_path / "out",
        inputs=tmp_path,
        assessed_at=NOW,
        environ={
            "MOONMIND_EGRESS_PROXY": "http://gateway:3128",
            "PATH": "/usr/bin:/bin",
        },
        processes=FixtureProcesses(
            leaf + ca, responses=["no protocols available", "observed"]
        ),
    )
    summary = summarize_tls_assessment(a, result, "art_native")
    assert summary.status == "partial"
    assert summary.unperformed_checks == ["TLSv1/default"]
    assert result["probes"][1]["certificates"]
    assert not summary.findings
    key = summary.comparison_key
    assert (
        summarize_tls_assessment(
            a, result, "art_native", resolution={"snapshotDigest": "changed"}
        ).comparison_key
        != key
    )
    result["probes"][0]["unexpectedSecret"] = "not publishable"
    with pytest.raises(ValueError, match="unexpected fields"):
        validate_tls_execution(a, result)


def test_private_keys_never_become_trust_evidence(tmp_path):
    (tmp_path / "tls-trust").mkdir()
    (tmp_path / "tls-trust" / "ca.pem").write_bytes(
        make_certificates()[1]
        + b"\n-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----"
    )
    a = TLSAssessment(
        origin="https://example.com",
        egress_policy_ref="tls",
        trust_artifact_ref="art_ca",
    )
    processes = FixtureProcesses(b"")
    result = inspect_tls(
        a,
        tmp_path / "out",
        inputs=tmp_path,
        assessed_at=NOW,
        environ={"MOONMIND_EGRESS_PROXY": "http://gateway:3128"},
        processes=processes,
    )
    assert result["errors"] == ["trust_source_unavailable_or_invalid"]
    assert not processes.commands
    assert b"PRIVATE KEY" not in (tmp_path / "out" / "execution.json").read_bytes()


def test_system_bundle_symlink_and_operator_symlink_boundary(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from moonmind.security.tls_assessment_runner import _trust_material

    bundle = tmp_path / "bundle.pem"
    bundle.write_bytes(make_certificates()[1])
    link = tmp_path / "cert.pem"
    link.symlink_to(bundle)
    monkeypatch.setattr(
        "ssl.get_default_verify_paths", lambda: SimpleNamespace(cafile=str(link))
    )
    a = TLSAssessment(origin="https://example.com", egress_policy_ref="tls")
    trust, raw = _trust_material(a, tmp_path)
    assert trust["source"] == "system" and raw
    (tmp_path / "tls-trust").mkdir()
    (tmp_path / "tls-trust" / "ca.pem").symlink_to(bundle)
    a.trust_artifact_ref = "art_ca"
    with pytest.raises(ValueError, match="trust_source_unavailable"):
        _trust_material(a, tmp_path)


def test_weaker_protocol_is_observed_not_assumed(tmp_path):
    leaf, ca = make_certificates()
    (tmp_path / "tls-trust").mkdir()
    (tmp_path / "tls-trust" / "ca.pem").write_bytes(ca)
    a = TLSAssessment(
        origin="https://example.com",
        egress_policy_ref="tls",
        protocols=["TLSv1"],
        trust_artifact_ref="art_ca",
    )
    result = inspect_tls(
        a,
        tmp_path / "out",
        inputs=tmp_path,
        assessed_at=NOW,
        environ={
            "MOONMIND_EGRESS_PROXY": "http://gateway:3128",
            "PATH": "/usr/bin:/bin",
        },
        processes=FixtureProcesses(leaf + ca),
    )
    assert result["probes"][0]["selectedProtocol"] == "TLSv1"
    assert result["probes"][0]["status"] == "observed"
    assert validate_tls_execution(a, result) == result
    assert result["probes"][0]["findings"] == ["legacy_protocol_accepted"]
    assert (
        summarize_tls_assessment(a, result, "art_native").findings[0]["code"]
        == "legacy_protocol_accepted"
    )


def test_no_proxy_does_not_start_even_version_process(tmp_path):
    a = TLSAssessment(origin="https://example.com", egress_policy_ref="tls")
    processes = FixtureProcesses(b"")
    with pytest.raises(ValueError, match="proxy"):
        inspect_tls(a, tmp_path, environ={}, processes=processes)
    assert not processes.commands


def test_missing_execution_enumerates_every_selected_check():
    a = TLSAssessment(
        origin="https://example.com", egress_policy_ref="tls", ciphers=["AES256-SHA"]
    )
    summary = summarize_tls_assessment(a, {}, "unavailable")
    assert summary.status == "failed"
    assert summary.unperformed_checks == [
        "TLSv1.2/default",
        "TLSv1.2/AES256-SHA",
        "TLSv1.3/default",
        "TLSv1.3/AES256-SHA",
    ]
    assert not summary.findings


def test_policy_requires_exact_only_https_endpoint():
    from moonmind.security.egress_policy import ScopedEgressPolicy
    from moonmind.security.tls_assessments import tls_policy_admits

    a = TLSAssessment(origin="https://example.com:8443", egress_policy_ref="tls")

    def policy(host="example.com", port=8443, protocol="https"):
        return ScopedEgressPolicy.model_validate(
            {
                "policyRef": "tls",
                "allowedWorkflowIds": ["wf"],
                "allowedImageSourceRefs": ["repository-security"],
                "destinations": [
                    {
                        "host": host,
                        "port": port,
                        "protocol": protocol,
                        "purpose": "live-target",
                    }
                ],
            }
        )

    assert tls_policy_admits(policy(), a)
    assert not tls_policy_admits(policy(port=443), a)
    assert not tls_policy_admits(policy(host="other.example"), a)
    assert not tls_policy_admits(policy(protocol="http"), a)


def test_invalid_trust_digest_cannot_be_published(tmp_path):
    leaf, ca = make_certificates()
    (tmp_path / "tls-trust").mkdir()
    (tmp_path / "tls-trust" / "ca.pem").write_bytes(ca)
    a = TLSAssessment(
        origin="https://example.com",
        egress_policy_ref="tls",
        protocols=["TLSv1.2"],
        trust_artifact_ref="art_ca",
    )
    result = inspect_tls(
        a,
        tmp_path / "out",
        inputs=tmp_path,
        assessed_at=NOW,
        environ={
            "MOONMIND_EGRESS_PROXY": "http://gateway:3128",
            "PATH": "/usr/bin:/bin",
        },
        processes=FixtureProcesses(leaf + ca),
    )
    result["trust"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="digest"):
        validate_tls_execution(a, result)


@pytest.mark.parametrize(
    "version",
    [
        "OpenSSL 3.5.7 9 Jun 2026 (Library: OpenSSL 3.5.7 9 Jun 2026)",
        "OpenSSL 1.1.1w 11 Sep 2023",
    ],
)
def test_real_openssl_version_is_retained(tmp_path, version):
    leaf, ca = make_certificates()
    (tmp_path / "tls-trust").mkdir()
    (tmp_path / "tls-trust" / "ca.pem").write_bytes(ca)
    a = TLSAssessment(
        origin="https://example.com",
        egress_policy_ref="tls",
        protocols=["TLSv1.2"],
        trust_artifact_ref="art_ca",
    )

    class VersionProcesses(FixtureProcesses):
        def run(self, argv, **kwargs):
            if argv == ["openssl", "version"]:
                kwargs["capture"].write_text(version + "\n")
                kwargs["errors"].write_bytes(b"")
                return 0, "succeeded"
            return super().run(argv, **kwargs)

    result = inspect_tls(
        a,
        tmp_path / "out",
        inputs=tmp_path,
        assessed_at=NOW,
        environ={
            "MOONMIND_EGRESS_PROXY": "http://gateway:3128",
            "PATH": "/usr/bin:/bin",
        },
        processes=VersionProcesses(leaf + ca),
    )
    assert result["tool"]["version"] == version
    assert validate_tls_execution(a, result) == result


@pytest.mark.parametrize(
    "text",
    [
        "OpenSSL 3.5.7\nsecret: injected",
        "OpenSSL 3.5.7\x1b[31m",
        "OpenSSL 3.5.7 (Library: secret)",
        "OpenSSL 3.5.7 token: secret",
        "OpenSSL " + "3" * 300,
    ],
)
def test_openssl_version_rejects_injected_output(text):
    from moonmind.security.tls_assessments import sanitize_openssl_version

    assert sanitize_openssl_version(text) == "unavailable"


def test_missing_tool_version_is_an_explicit_limitation():
    a = TLSAssessment(origin="https://example.com", egress_policy_ref="tls")
    summary = summarize_tls_assessment(a, {}, "unavailable")
    assert "OpenSSL version could not be recorded" in summary.limitations


def test_hostile_certificate_diagnostics_cannot_hide_retained_findings(tmp_path):
    leaf, ca = make_certificates(
        hostname="wrong.example",
        expired=True,
        common_name="no protocols available, Cipher is NONE, New, TLSv1.3",
    )
    (tmp_path / "tls-trust").mkdir()
    (tmp_path / "tls-trust" / "ca.pem").write_bytes(ca)
    a = TLSAssessment(
        origin="https://example.com",
        egress_policy_ref="tls",
        protocols=["TLSv1.2"],
        trust_artifact_ref="art_ca",
    )

    class HostileNameProcesses(FixtureProcesses):
        def run(self, argv, **kwargs):
            result = super().run(argv, **kwargs)
            if argv[1] == "s_client":
                original = kwargs["capture"].read_bytes()
                kwargs["capture"].write_bytes(
                    b'subject=CN = "no protocols available, Cipher is NONE, New, TLSv1.3"\nissuer=CN = tlsv1 alert protocol version\n'
                    + original
                    + b"New, TLSv1.3, Cipher is FAKE\n40AB:error:0A0000BF:SSL routines:fixture:no protocols available:ssl/statem.c:1:\n"
                    + leaf
                    + ca
                )
                kwargs["errors"].write_bytes(
                    b"depth=0 CN = no cipher match\nverify error:num=10:certificate has expired\n"
                )
            return result

    execution = inspect_tls(
        a,
        tmp_path / "out",
        inputs=tmp_path,
        assessed_at=NOW,
        environ={
            "MOONMIND_EGRESS_PROXY": "http://gateway:3128",
            "PATH": "/usr/bin:/bin",
        },
        processes=HostileNameProcesses(leaf + ca),
    )
    assert execution["probes"][0]["status"] == "observed"
    assert execution["probes"][0]["selectedProtocol"] == "TLSv1.2"
    assert execution["probes"][0]["selectedCipher"] == "ECDHE-RSA-AES256-GCM-SHA384"
    assert len(execution["probes"][0]["certificates"]) == 2
    summary = summarize_tls_assessment(a, execution, "art_native")
    assert {f["code"] for f in summary.findings} == {
        "certificate_expired",
        "hostname_mismatch",
        "certificate_trust_failed",
    }


@pytest.mark.parametrize(
    "name",
    [
        "no protocols available",
        "tlsv1 alert protocol version",
        "Connection refused",
        "40AB:error:0A0000BF:SSL routines:tls_setup_handshake:no protocols available:ssl/statem.c:1:",
    ],
)
def test_certificate_names_cannot_forge_failed_probe_diagnostics(name):
    assert (
        classify_probe(
            f"subject=CN = {name}\nissuer=CN = {name}\ndepth=0 CN = {name}",
            1,
            "succeeded",
            False,
        )
        == "blocked_handshake"
    )


def test_failed_native_summary_cannot_be_replaced_by_application_output():
    from moonmind.security.tls_assessment_runner import native_handshake

    text = (
        b"Certificate chain\n-----BEGIN CERTIFICATE-----\nQQ==\n-----END CERTIFICATE-----\n---\n"
        b"subject=CN=New, TLSv1.2, Cipher is FAKE\n"
        b"New, (NONE), Cipher is (NONE)\n"
        b"New, TLSv1.2, Cipher is FAKE\n"
    )
    _chain, protocol, cipher = native_handshake(text)
    assert protocol is None and cipher is None
