"""Versioned Omnigent bridge conformance evidence contracts.

Source issue: MoonLadderStudios/MoonMind#3419.

This module deliberately contains no provider semantics.  It validates the
portable profile and the evidence emitted by fake, stock-image, Compose, and
on-demand runners so all hosts publish one comparable terminal contract.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

PROFILE_VERSION = "moonmind.omnigent.conformance/v4"
PROFILE_SHA256 = "be46833cb7f93399cbaa34f1d02c749bab21ab2fa81efdca968744dd19767887"
REPORT_VERSION = "moonmind.omnigent.conformance-report/v1"
SUPPORTED_FIXTURE_VERSION = "moonmind.omnigent.fixture/v1"

_DIGEST_REF = re.compile(r"^.+@sha256:[0-9a-f]{64}$")
_SECRET = re.compile(
    r"(?:ghp_|github_pat_|AIza|ATATT|AKIA|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"(?i:token|password|authorization)\s*[:=]\s*[^\s,;]+)"
)
REQUIRED_EVIDENCE_CHANNELS = (
    "logs",
    "temporalHistory",
    "screenshots",
    "archives",
)


class ConformanceContractError(ValueError):
    """Raised when conformance evidence cannot safely be accepted."""


@dataclass(frozen=True, slots=True)
class CaseResult:
    case_id: str
    status: str
    evidence_refs: tuple[str, ...]
    diagnostics: tuple[Mapping[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        if self.status not in {"passed", "failed", "skipped"}:
            raise ConformanceContractError(f"invalid case status: {self.status}")
        if not self.case_id.strip():
            raise ConformanceContractError("case_id is required")
        if not self.evidence_refs:
            raise ConformanceContractError(
                f"case {self.case_id!r} must include at least one evidence ref"
            )
        if any(not ref.strip() for ref in self.evidence_refs):
            raise ConformanceContractError(
                f"case {self.case_id!r} contains an empty evidence ref"
            )
        return {
            "caseId": self.case_id,
            "status": self.status,
            "evidenceRefs": list(self.evidence_refs),
            "diagnostics": [dict(item) for item in self.diagnostics],
        }


def load_profile(path: Path) -> dict[str, Any]:
    raw_profile = path.read_bytes()
    profile = json.loads(raw_profile)
    if not isinstance(profile, dict):
        raise ConformanceContractError("conformance profile must be an object")
    if profile.get("profileVersion") != PROFILE_VERSION:
        raise ConformanceContractError(
            f"unsupported conformance profile: {profile.get('profileVersion')!r}"
        )
    cases = profile.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ConformanceContractError("conformance profile must declare cases")
    if not all(
        isinstance(case, dict)
        and isinstance(case.get("id"), str)
        and case["id"].strip()
        for case in cases
    ):
        raise ConformanceContractError(
            "conformance case ids must be present, non-empty strings"
        )
    ids = [case["id"] for case in cases]
    if len(set(ids)) != len(ids):
        raise ConformanceContractError("conformance case ids must be unique")
    digest = hashlib.sha256(raw_profile).hexdigest()
    if digest != PROFILE_SHA256:
        raise ConformanceContractError(
            "conformance profile does not match the canonical inventory"
        )
    profile["profileSha256"] = digest
    return profile


def validate_fixture(fixture: Mapping[str, Any]) -> str:
    """Return the declared future-version behavior for a versioned fixture."""
    version = fixture.get("schemaVersion")
    provenance = fixture.get("provenance")
    if not isinstance(provenance, Mapping) or not provenance.get("source"):
        raise ConformanceContractError("fixture provenance.source is required")
    if version == SUPPORTED_FIXTURE_VERSION:
        return "accepted"
    expectation = fixture.get("unknownVersionExpectation")
    if expectation not in {"fail", "degrade"}:
        raise ConformanceContractError(
            "unknown fixture versions require an explicit fail/degrade expectation"
        )
    return str(expectation)


def require_pinned_images(images: Mapping[str, str]) -> None:
    for role in ("server", "host"):
        ref = images.get(role, "")
        if not _DIGEST_REF.fullmatch(ref):
            raise ConformanceContractError(
                f"stock {role} image must be pinned by immutable sha256 digest"
            )


def assert_secret_free(evidence: Any) -> None:
    if isinstance(evidence, Mapping):
        for key, value in evidence.items():
            if str(key).strip().lower() in {"token", "password", "authorization"}:
                raise ConformanceContractError("secret-like material detected in evidence")
            assert_secret_free(value)
        return
    if isinstance(evidence, (list, tuple)):
        for value in evidence:
            assert_secret_free(value)
        return
    serialized = json.dumps(evidence, sort_keys=True, default=str)
    if _SECRET.search(serialized):
        raise ConformanceContractError("secret-like material detected in evidence")


def build_report(
    *,
    profile: Mapping[str, Any],
    images: Mapping[str, str],
    host_architecture: str,
    auth_mode: str,
    capabilities: Iterable[str],
    cases: Iterable[CaseResult],
    protocol_version: str,
    evidence_scans: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    if (
        profile.get("profileVersion") != PROFILE_VERSION
        or profile.get("profileSha256") != PROFILE_SHA256
    ):
        raise ConformanceContractError("report profile is not the canonical inventory")
    require_pinned_images(images)
    if (
        not isinstance(host_architecture, str)
        or not host_architecture.strip()
        or not isinstance(auth_mode, str)
        or not auth_mode.strip()
        or not isinstance(protocol_version, str)
        or not protocol_version.strip()
    ):
        raise ConformanceContractError(
            "host architecture, auth mode, and protocol version are required"
        )
    missing_channels = sorted(set(REQUIRED_EVIDENCE_CHANNELS) - set(evidence_scans))
    if missing_channels:
        raise ConformanceContractError(
            f"missing evidence-channel secret scans: {missing_channels}"
        )
    for channel in REQUIRED_EVIDENCE_CHANNELS:
        scan = evidence_scans[channel]
        evidence_ref = scan.get("evidenceRef")
        if (
            scan.get("status") != "passed"
            or not isinstance(evidence_ref, str)
            or not evidence_ref.strip()
        ):
            raise ConformanceContractError(
                f"evidence-channel secret scan did not pass: {channel}"
            )
    results = [case.as_dict() for case in cases]
    observed_ids = [case["caseId"] for case in results]
    if len(set(observed_ids)) != len(observed_ids):
        raise ConformanceContractError("report contains duplicate case results")
    declared = {case["id"] for case in profile["cases"]}
    observed = {case["caseId"] for case in results}
    if declared != observed:
        missing = sorted(declared - observed)
        extra = sorted(observed - declared)
        raise ConformanceContractError(
            f"report case coverage mismatch; missing={missing}, extra={extra}"
        )
    report = {
        "schemaVersion": REPORT_VERSION,
        "profileVersion": PROFILE_VERSION,
        "profileSha256": PROFILE_SHA256,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "images": dict(images),
        "hostArchitecture": host_architecture,
        "authMode": auth_mode,
        "protocolVersion": protocol_version,
        "capabilities": sorted(set(capabilities)),
        "evidenceScans": {key: dict(value) for key, value in evidence_scans.items()},
        "cases": results,
        "summary": {
            status: sum(case["status"] == status for case in results)
            for status in ("passed", "failed", "skipped")
        },
    }
    assert_secret_free(report)
    return report
