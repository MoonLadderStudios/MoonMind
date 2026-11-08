"""Bounded HTTPS TLS observations on the existing admitted ContainerJob path."""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field

from moonmind.schemas.repository_scanner_models import Contract, TLSAssessment

TLS_OUTPUT_FILES = {"execution": "execution.json", "summary": "summary.json"}
INTERPRETATION = (
    "TLS evidence describes sampled HTTPS handshakes through admitted CONNECT egress. "
    "It does not cover every backend, native TLS services or STARTTLS. Client inability "
    "and failed handshakes do not establish that a server protocol is disabled."
)


def sanitize_openssl_version(value: str) -> str:
    """Retain a bounded OpenSSL release line, including its library version.

    Accept the documented binary/library diagnostic, not arbitrary colon-bearing
    text or multiline process output. Unknown formats remain explicit gaps.
    """
    import re

    release = r"[0-9][A-Za-z0-9.+_-]{0,63}(?: [0-9]{1,2} [A-Za-z]{3} [0-9]{4})?"
    pattern = rf"OpenSSL {release}(?: \(Library: OpenSSL {release}\))?"
    return (
        value if len(value) <= 256 and re.fullmatch(pattern, value) else "unavailable"
    )


def build_tls_assessment_job(
    assessment: TLSAssessment, *, image_source_ref="repository-security"
) -> dict:
    return {
        "imageSourceRef": image_source_ref,
        "tlsAssessment": assessment.model_dump(mode="json", by_alias=True),
        "inputAttachments": (
            [
                {
                    "name": "tls-trust",
                    "artifactRef": assessment.trust_artifact_ref,
                    "fileName": "ca.pem",
                    "maxBytes": 1048576,
                }
            ]
            if assessment.trust_artifact_ref
            else []
        ),
        "entrypoint": ["python"],
        "command": [
            "-I",
            "-m",
            "moonmind.security.tls_assessment_runner",
            assessment.model_dump_json(by_alias=True),
            f"/outputs/{assessment.output_directory}",
        ],
        "networkMode": "bridge",
        "egressPolicyRef": assessment.egress_policy_ref,
        "workdir": "/workspace",
        "resources": {"cpuMillis": 1000, "memoryMiB": 1024, "pids": 64},
        "timeoutSeconds": assessment.timeout_seconds + 15,
        "outputs": assessment.outputs(),
    }


def validate_tls_assessment_job(spec) -> None:
    a = getattr(spec, "tls_assessment", None)
    if a is None:
        return
    if any(
        getattr(spec, field, None) is not None
        for field in (
            "repository_scan",
            "feed_refresh",
            "image_assessment",
            "web_assessment",
            "cloud_assessment",
            "workload_profile",
        )
    ):
        raise ValueError("TLS assessment is its own workload")
    if not spec.image_source_ref or spec.input_attachments is None:
        raise ValueError(
            "TLS assessment requires a deployment image and isolated inputs"
        )
    canonical = build_tls_assessment_job(a, image_source_ref=spec.image_source_ref)
    for field, key in (
        ("command", "command"),
        ("entrypoint", "entrypoint"),
        ("network_mode", "networkMode"),
        ("egress_policy_ref", "egressPolicyRef"),
        ("timeout_seconds", "timeoutSeconds"),
        ("workdir", "workdir"),
    ):
        if getattr(spec, field) != canonical[key]:
            raise ValueError("TLS workload differs from its admission")
    if spec.resources != type(spec.resources).model_validate(canonical["resources"]):
        raise ValueError("TLS resource bounds differ from admission")
    if (
        spec.environment
        or spec.caches
        or spec.service_ref
        or spec.registry_credential_ref
    ):
        raise ValueError("TLS assessment does not admit extra environment or mounts")
    for field, key in (
        ("input_attachments", "inputAttachments"),
        ("outputs", "outputs"),
    ):
        items = getattr(spec, field)
        model = type(items[0]) if items else None
        expected = [
            model.model_validate(v).model_dump(by_alias=True) if model else v
            for v in canonical[key]
        ]
        if [v.model_dump(by_alias=True) for v in items] != expected:
            raise ValueError("TLS mounts differ from their admission")


def tls_policy_admits(policy, assessment: TLSAssessment) -> bool:
    u = urlsplit(assessment.origin)
    return bool(policy.destinations) and all(
        (d.host, d.port, d.protocol, d.purpose)
        == (u.hostname, u.port or 443, "https", "live-target")
        for d in policy.destinations
    )


class TLSAssessmentSummary(Contract):
    schema_version: Literal["tls-assessment/v1"] = "tls-assessment/v1"
    origin: str
    status: Literal["complete", "partial", "failed"]
    evidence_ref: str
    settings: dict
    execution: dict
    resolution: dict = Field(default_factory=dict)
    findings: list[dict] = Field(default_factory=list)
    unperformed_checks: list[str] = Field(default_factory=list)
    comparison_key: str
    interpretation: str = INTERPRETATION
    limitations: list[str] = Field(
        default_factory=lambda: [
            "Incomplete backend/load-balancer coverage",
            "Native TLS and STARTTLS are not inspected",
        ]
    )


def summarize_tls_assessment(
    assessment: TLSAssessment,
    execution: dict,
    evidence_ref: str,
    *,
    resolution: dict | None = None,
) -> TLSAssessmentSummary:
    from moonmind.security.assessments import content_key

    if execution:
        execution = validate_tls_execution(assessment, execution)
    probes = execution.get("probes", [])
    expected = [
        (p, c) for p in assessment.protocols for c in [None, *assessment.ciphers]
    ]
    performed = {
        (p.get("protocol"), p.get("cipher"))
        for p in probes
        if p.get("status") == "server_refused"
        or (
            p.get("status") == "observed"
            and p.get("trustResult") in {"trusted", "untrusted"}
        )
    }
    unperformed = [
        f"{p}/{c or 'default'}" for p, c in expected if (p, c) not in performed
    ]
    findings = []
    for probe in probes:
        if probe.get("status") != "observed":
            continue
        for code in probe.get("findings", []):
            findings.append(
                {
                    "code": code,
                    "protocol": probe["protocol"],
                    "cipher": probe.get("cipher"),
                    "evidenceRef": evidence_ref,
                }
            )
    settings = assessment.model_dump(mode="json", by_alias=True)
    identity = {
        "settings": settings,
        "trust": execution.get("trust"),
        "resolution": resolution or {},
        "tool": execution.get("tool"),
        "chains": [p.get("certificates", []) for p in probes],
        "assessedAt": execution.get("startedAt"),
    }
    limitations = [
        "Incomplete backend/load-balancer coverage",
        "Native TLS and STARTTLS are not inspected",
    ]
    if execution.get("tool", {}).get("version") in {None, "unavailable"}:
        limitations.append("OpenSSL version could not be recorded")
    return TLSAssessmentSummary(
        origin=assessment.origin,
        status=(
            "failed" if not execution else "partial" if unperformed else "complete"
        ),
        evidence_ref=evidence_ref,
        settings=settings,
        execution=execution,
        resolution=resolution or {},
        findings=findings,
        unperformed_checks=unperformed,
        comparison_key=content_key(identity),
        limitations=limitations,
    )


def validate_tls_execution(assessment: TLSAssessment, execution: dict) -> dict:
    """Rebuild public evidence, rejecting injected keys, probes and private PEM.

    Native stdout is never accepted here. A worker may publish only this finite
    schema, and certificate identity is recomputed from retained DER bytes.
    """
    import hashlib
    import json
    import re
    from datetime import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization

    if len(json.dumps(execution).encode()) > 8 * 1024 * 1024:
        raise ValueError("TLS execution exceeds evidence bound")

    def exact(value, keys):
        if not isinstance(value, dict) or set(value) != set(keys.split()):
            raise ValueError("TLS evidence has unexpected fields")

    def instant(value, nullable=False):
        if value is None and nullable:
            return
        date = datetime.fromisoformat(value)
        if date.tzinfo is None:
            raise ValueError("TLS evidence needs an aware timestamp")

    def pem(value):
        if not isinstance(value, str) or len(value) > 1048576:
            raise ValueError("TLS certificate bound exceeded")
        pattern = r"-----BEGIN CERTIFICATE-----\s+[A-Za-z0-9+/=\r\n]+-----END CERTIFICATE-----"
        certs = re.findall(pattern, value)
        if not certs or re.sub(pattern, "", value).strip():
            raise ValueError("TLS evidence must contain only public certificates")
        return [x509.load_pem_x509_certificate(c.encode()) for c in certs]

    exact(
        execution,
        "origin startedAt finishedAt tool trust probes errors resolution settings",
    )
    if execution["origin"] != assessment.origin or execution[
        "settings"
    ] != assessment.model_dump(mode="json", by_alias=True):
        raise ValueError("TLS execution differs from admission")
    instant(execution["startedAt"])
    instant(execution["finishedAt"], nullable=True)
    exact(execution["tool"], "name version")
    version = execution["tool"]["version"]
    if execution["tool"]["name"] != "OpenSSL" or (
        version is not None
        and version != "unavailable"
        and sanitize_openssl_version(version) != version
    ):
        raise ValueError("TLS tool evidence is invalid")
    if execution["resolution"] != {"status": "proxy_pinned", "backendAddress": None}:
        raise ValueError("TLS scanner cannot assert proxy backend resolution")
    if not isinstance(execution["errors"], list) or any(
        e not in {"trust_source_unavailable_or_invalid", "evidence_bound_exceeded"}
        for e in execution["errors"]
    ):
        raise ValueError("TLS errors are not public diagnostic codes")
    trust = execution["trust"]
    if trust:
        exact(trust, "source artifactRef sha256 certificatesPem")
        if (
            trust["source"]
            != ("operator" if assessment.trust_artifact_ref else "system")
            or trust["artifactRef"] != assessment.trust_artifact_ref
        ):
            raise ValueError("TLS trust source differs from admission")
        clean = b"\n".join(
            c.public_bytes(serialization.Encoding.PEM)
            for c in pem(trust["certificatesPem"])
        )
        if hashlib.sha256(clean).hexdigest() != trust["sha256"]:
            raise ValueError("TLS trust digest does not match its certificates")
    probes = execution["probes"]
    if not isinstance(probes, list) or len(probes) > len(assessment.protocols) * (
        len(assessment.ciphers) + 1
    ):
        raise ValueError("TLS probe bound exceeded")
    seen = set()
    for probe in probes:
        exact(
            probe,
            "protocol cipher status startedAt finishedAt selectedCipher selectedProtocol certificates hostnameMatch trustResult findings",
        )
        key = (probe["protocol"], probe["cipher"])
        if (
            key in seen
            or key[0] not in assessment.protocols
            or key[1] not in [None, *assessment.ciphers]
        ):
            raise ValueError("TLS probe differs from admission")
        seen.add(key)
        instant(probe["startedAt"])
        instant(probe["finishedAt"])
        if probe["status"] not in {
            "observed",
            "unsupported_client",
            "server_refused",
            "transient_failure",
            "blocked_handshake",
            "incomplete_evidence",
        }:
            raise ValueError("TLS probe status is invalid")
        if probe["selectedProtocol"] not in {
            None,
            "TLSv1",
            "TLSv1.1",
            "TLSv1.2",
            "TLSv1.3",
        }:
            raise ValueError("TLS selected protocol is invalid")
        if (
            probe["status"] == "observed"
            and probe["selectedProtocol"] != probe["protocol"]
        ):
            raise ValueError("TLS selected protocol differs from probe")
        if probe["selectedCipher"] is not None and not re.fullmatch(
            r"[A-Za-z0-9_-]{1,128}", probe["selectedCipher"]
        ):
            raise ValueError("TLS selected cipher is invalid")
        if probe["trustResult"] not in {
            "trusted",
            "untrusted",
            "incomplete",
            "not_evaluated",
        } or probe["hostnameMatch"] not in (True, False, None):
            raise ValueError("TLS trust result is invalid")
        if not isinstance(probe["findings"], list) or any(
            v
            not in {
                "certificate_expired",
                "certificate_not_yet_valid",
                "hostname_mismatch",
                "certificate_trust_failed",
                "legacy_protocol_accepted",
            }
            for v in probe["findings"]
        ):
            raise ValueError("TLS finding is invalid")
        certificates = probe["certificates"]
        if not isinstance(certificates, list) or len(certificates) > 32:
            raise ValueError("TLS certificate chain exceeds bound")
        for certificate in certificates:
            exact(certificate, "pem sha256 notBefore notAfter validAtAssessment")
            certs = pem(certificate["pem"])
            if len(certs) != 1:
                raise ValueError("TLS chain entry is not one certificate")
            cert = certs[0]
            if (
                certificate["sha256"] != cert.fingerprint(hashes.SHA256()).hex()
                or certificate["notBefore"] != cert.not_valid_before_utc.isoformat()
                or certificate["notAfter"] != cert.not_valid_after_utc.isoformat()
            ):
                raise ValueError("TLS certificate identity differs from evidence")
            assessed = datetime.fromisoformat(execution["startedAt"])
            if certificate["validAtAssessment"] is not (
                cert.not_valid_before_utc <= assessed <= cert.not_valid_after_utc
            ):
                raise ValueError("TLS certificate validity differs from clock")
    return execution
