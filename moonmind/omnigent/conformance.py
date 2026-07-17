"""Versioned evidence contract for Omnigent bridge conformance.

The contract deliberately contains metadata and artifact references, never OAuth
homes, tokens, headers, or unbounded provider responses.  It is shared by fake
server, stock-image, static Compose, and on-demand provider suites.

Source: MoonLadderStudios/MoonMind#3368.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

PROFILE_SCHEMA_VERSION = "1.0"
REPORT_SCHEMA_VERSION = "1.0"
PROFILE_NAME = "moonmind.omnigent-bridge.codex"

_SECRET_PATTERNS = (
    re.compile(r"(?i)authorization[\"']?\s*[:=]"),
    re.compile(r"(?i)(?:token|password|client_secret)[\"']?\s*[:=]"),
    re.compile(r"(?:ghp_|github_pat_|AIza|ATATT|AKIA)[A-Za-z0-9_\-]+"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


class ConformanceContractError(ValueError):
    """A conformance profile or report violates the supported contract."""


@dataclass(frozen=True, slots=True)
class VersionDisposition:
    supported: bool
    behavior: str
    reason: str | None = None


def evaluate_version(version: object, *, critical: bool) -> VersionDisposition:
    """Return the required fail/degrade behavior for a profile section version."""
    text = str(version or "").strip()
    if text == PROFILE_SCHEMA_VERSION:
        return VersionDisposition(True, "accept")
    behavior = "fail" if critical else "degrade"
    return VersionDisposition(
        False,
        behavior,
        f"unsupported Omnigent conformance schema version {text or '<missing>'}",
    )


def load_profile(path: str | Path) -> dict[str, Any]:
    profile = json.loads(Path(path).read_text(encoding="utf-8"))
    disposition = evaluate_version(profile.get("schemaVersion"), critical=True)
    if not disposition.supported:
        raise ConformanceContractError(disposition.reason)
    if profile.get("profile") != PROFILE_NAME:
        raise ConformanceContractError("unexpected Omnigent conformance profile")
    provenance = profile.get("provenance")
    if not isinstance(provenance, Mapping) or not provenance.get("issue"):
        raise ConformanceContractError("profile provenance.issue is required")
    cases = profile.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ConformanceContractError("profile cases must be a non-empty list")
    return profile


def assert_secret_free(value: object) -> None:
    """Reject credential-shaped content before it enters archived evidence."""
    serialized = json.dumps(value, sort_keys=True, default=str)
    for pattern in _SECRET_PATTERNS:
        if pattern.search(serialized):
            raise ConformanceContractError("secret-like material in conformance evidence")


def build_report(
    *,
    profile: Mapping[str, Any],
    mode: str,
    images: Sequence[Mapping[str, str]],
    results: Sequence[Mapping[str, Any]],
    evidence_refs: Sequence[str] = (),
) -> dict[str, Any]:
    """Build a bounded machine-readable report suitable for CI artifacts."""
    expected = {str(case["id"]) for case in profile["cases"]}
    observed = {str(result.get("caseId", "")) for result in results}
    unknown = observed - expected
    if unknown:
        raise ConformanceContractError(f"report contains unknown cases: {sorted(unknown)}")
    report = {
        "schemaVersion": REPORT_SCHEMA_VERSION,
        "profile": profile["profile"],
        "profileVersion": profile["schemaVersion"],
        "provenance": profile["provenance"],
        "mode": mode,
        "images": list(images),
        "cases": list(results),
        "evidenceRefs": list(evidence_refs),
        "summary": {
            status: sum(result.get("status") == status for result in results)
            for status in ("passed", "failed", "skipped")
        },
    }
    assert_secret_free(report)
    return report
