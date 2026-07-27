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
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

PROFILE_VERSION = "moonmind.omnigent.conformance/v4"
PROFILE_SHA256 = "4098a93e74fb354d2a557e900ea85d3d34ca4957a02f91a529daa276ee7b3a1b"
REPORT_VERSION = "moonmind.omnigent.conformance-report/v1"
ACCEPTANCE_VERSION = "moonmind.omnigent.product-acceptance/v1"
BROWSER_EVIDENCE_VERSION = "moonmind.omnigent.live-evidence/v1"
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


def validate_acceptance_manifest(
    manifest: Mapping[str, Any],
    *,
    now: datetime | None = None,
    expected_commit: str | None = None,
    required_rows: Iterable[str] = (),
    evidence_root: Path | None = None,
) -> None:
    """Fail closed unless a #3508 release manifest is current and immutable."""
    if manifest.get("schemaVersion") != ACCEPTANCE_VERSION:
        raise ConformanceContractError("acceptance manifest schema is missing or malformed")
    if (
        manifest.get("issue") != "MoonLadderStudios/MoonMind#3508"
        or manifest.get("parentIssue") != "MoonLadderStudios/MoonMind#3448"
        or manifest.get("status") != "passed"
    ):
        raise ConformanceContractError("acceptance manifest is not a passing #3508 artifact")
    try:
        generated_at = datetime.fromisoformat(
            str(manifest["generatedAt"]).replace("Z", "+00:00")
        )
        expires_at = datetime.fromisoformat(
            str(manifest["expiresAt"]).replace("Z", "+00:00")
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ConformanceContractError(
            "acceptance manifest validity period is missing or malformed"
        ) from exc
    if generated_at.tzinfo is None or expires_at.tzinfo is None:
        raise ConformanceContractError("acceptance manifest validity period needs timezone")
    observed_at = now or datetime.now(timezone.utc)
    if expires_at <= generated_at or expires_at <= observed_at:
        raise ConformanceContractError("acceptance manifest is expired")
    images = manifest.get("images")
    if not isinstance(images, Mapping):
        raise ConformanceContractError("acceptance manifest images are missing")
    for key in ("serverDigest", "hostDigest"):
        digest = images.get(key)
        if (
            not isinstance(digest, str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
        ):
            raise ConformanceContractError(
                "acceptance manifest contains a mutable or malformed image"
            )
    if not manifest.get("browserEvidence") or not manifest.get("reports"):
        raise ConformanceContractError("acceptance manifest evidence is missing")
    if expected_commit is not None and manifest.get("sourceCommit") != expected_commit:
        raise ConformanceContractError(
            "acceptance manifest was produced for a different source commit"
        )
    rows = manifest.get("browserRows")
    required = set(required_rows)
    if required and (
        not isinstance(rows, Mapping)
        or set(rows) != required
        or any(
            not isinstance(row, Mapping) or row.get("status") != "passed"
            for row in rows.values()
        )
    ):
        raise ConformanceContractError(
            "acceptance manifest does not contain every passing browser row"
        )
    if evidence_root is None:
        raise ConformanceContractError(
            "acceptance manifest evidence must be independently resolved"
        )

    browser = _resolve_acceptance_json(str(manifest["browserEvidence"]), evidence_root)
    if (
        browser.get("schemaVersion") != BROWSER_EVIDENCE_VERSION
        or browser.get("issue") != manifest.get("issue")
        or browser.get("parentIssue") != manifest.get("parentIssue")
        or browser.get("rows") != rows
    ):
        raise ConformanceContractError(
            "acceptance browser evidence is malformed or does not bind the manifest rows"
        )
    report_refs = manifest["reports"]
    if not isinstance(report_refs, list) or not report_refs:
        raise ConformanceContractError("acceptance manifest reports are malformed")
    reports = [_resolve_acceptance_json(str(ref), evidence_root) for ref in report_refs]
    if any(report.get("schemaVersion") != REPORT_VERSION for report in reports):
        raise ConformanceContractError("acceptance manifest references a malformed report")
    for name, row in rows.items():
        assertions = row.get("assertions") if isinstance(row, Mapping) else None
        authority = row.get("authorityChain") if isinstance(row, Mapping) else None
        evidence_refs = row.get("evidenceRefs") if isinstance(row, Mapping) else None
        if (
            not isinstance(assertions, Mapping)
            or not assertions
            or any(value is not True for value in assertions.values())
            or not isinstance(authority, Mapping)
            or any(not value for value in authority.values())
            or not isinstance(evidence_refs, list)
            or not evidence_refs
        ):
            raise ConformanceContractError(
                f"acceptance browser row {name!r} lacks controlling evidence"
            )
    secret_scan = manifest.get("secretScan")
    if not isinstance(secret_scan, Mapping) or secret_scan.get("status") != "passed":
        raise ConformanceContractError("acceptance manifest secret scan did not pass")
    assert_secret_free(manifest)
    assert_secret_free(browser)
    for report in reports:
        assert_secret_free(report)


def _resolve_acceptance_json(ref: str, evidence_root: Path) -> dict[str, Any]:
    """Resolve only HTTPS or paths confined to the downloaded run artifact."""
    parsed = urllib.parse.urlparse(ref)
    try:
        if parsed.scheme == "https":
            with urllib.request.urlopen(ref, timeout=10) as response:
                raw = response.read()
        elif parsed.scheme in {"", "file"}:
            candidate = Path(urllib.request.url2pathname(parsed.path))
            if not candidate.is_absolute():
                candidate = evidence_root / candidate
            candidate = candidate.resolve()
            root = evidence_root.resolve()
            if candidate != root and root not in candidate.parents:
                raise ConformanceContractError(
                    "acceptance evidence path escapes its run artifact"
                )
            raw = candidate.read_bytes()
        else:
            raise ConformanceContractError(
                f"unsupported acceptance evidence ref scheme: {parsed.scheme}"
            )
        payload = json.loads(raw)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise ConformanceContractError(
            f"acceptance evidence ref is unresolved or malformed: {ref}"
        ) from exc
    if not isinstance(payload, dict):
        raise ConformanceContractError("acceptance evidence document must be an object")
    return payload


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
