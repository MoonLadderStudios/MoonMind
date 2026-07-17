"""Versioned Omnigent bridge conformance evidence contracts.

Source issue: MoonLadderStudios/MoonMind#3368.

This module deliberately contains no provider semantics.  It validates the
portable profile and the evidence emitted by fake, stock-image, Compose, and
on-demand runners so all hosts publish one comparable terminal contract.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

PROFILE_VERSION = "moonmind.omnigent.conformance/v1"
REPORT_VERSION = "moonmind.omnigent.conformance-report/v1"
SUPPORTED_FIXTURE_VERSION = "moonmind.omnigent.fixture/v1"

_DIGEST_REF = re.compile(r"^.+@sha256:[0-9a-f]{64}$")
_SECRET = re.compile(
    r"(?:ghp_|github_pat_|AIza|ATATT|AKIA|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"(?i:token|password|authorization)\s*[:=]\s*[^\s,;]+)"
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
        return {
            "caseId": self.case_id,
            "status": self.status,
            "evidenceRefs": list(self.evidence_refs),
            "diagnostics": [dict(item) for item in self.diagnostics],
        }


def load_profile(path: Path) -> dict[str, Any]:
    profile = json.loads(path.read_text(encoding="utf-8"))
    if profile.get("profileVersion") != PROFILE_VERSION:
        raise ConformanceContractError(
            f"unsupported conformance profile: {profile.get('profileVersion')!r}"
        )
    cases = profile.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ConformanceContractError("conformance profile must declare cases")
    ids = [case.get("id") for case in cases if isinstance(case, dict)]
    if len(ids) != len(cases) or len(set(ids)) != len(ids):
        raise ConformanceContractError("conformance case ids must be present and unique")
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
) -> dict[str, Any]:
    if profile.get("profileVersion") != PROFILE_VERSION:
        raise ConformanceContractError("report profile version is unsupported")
    require_pinned_images(images)
    results = [case.as_dict() for case in cases]
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
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "images": dict(images),
        "hostArchitecture": host_architecture,
        "authMode": auth_mode,
        "capabilities": sorted(set(capabilities)),
        "cases": results,
        "summary": {
            status: sum(case["status"] == status for case in results)
            for status in ("passed", "failed", "skipped")
        },
    }
    assert_secret_free(report)
    return report
