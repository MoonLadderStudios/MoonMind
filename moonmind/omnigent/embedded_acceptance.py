"""Final embedded-mode acceptance report contract for issue #3425."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Mapping

from moonmind.omnigent.conformance import ConformanceContractError, assert_secret_free

SCHEMA_VERSION = "moonmind.omnigent.embedded-acceptance/v1"
REQUIRED_PREREQUISITES = ("3417", "3420", "3421", "3422", "3423", "3424")
REQUIRED_SECTIONS = (
    "evidence-validation",
    "mode-transition-rollback",
    "mixed-mode-history",
    "stock-host-codex-oauth",
    "restart-failure-recovery",
    "proxy-embedded-parity",
    "security-isolation",
    "hostile-input-bounds",
    "secret-scan",
    "cleanup-post-removal-replay",
)
_DIGEST_REF = re.compile(r"^.+@sha256:[0-9a-f]{64}$")


def _passed_evidence(item: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(item, Mapping) or item.get("status") != "passed":
        raise ConformanceContractError(f"{label} did not pass")
    refs = item.get("evidenceRefs")
    if not isinstance(refs, list) or not refs or not all(
        isinstance(ref, str) and ref.strip() for ref in refs
    ):
        raise ConformanceContractError(f"{label} requires durable evidence refs")
    return dict(item)


def build_embedded_acceptance_report(source: Mapping[str, Any]) -> dict[str, Any]:
    """Validate all controlling lanes and return the publishable report.

    Live/provider execution may produce the input in separate jobs, but no
    partial, skipped, mutable-image, or unattested result can become the final
    support gate.
    """
    prerequisites = source.get("prerequisites")
    if not isinstance(prerequisites, Mapping):
        raise ConformanceContractError("embedded prerequisites are required")
    accepted_prerequisites = {
        issue: _passed_evidence(prerequisites.get(issue), label=f"prerequisite #{issue}")
        for issue in REQUIRED_PREREQUISITES
    }

    sections = source.get("sections")
    if not isinstance(sections, Mapping):
        raise ConformanceContractError("embedded acceptance sections are required")
    accepted_sections = {
        section: _passed_evidence(sections.get(section), label=f"section {section}")
        for section in REQUIRED_SECTIONS
    }

    identities = source.get("identities")
    if not isinstance(identities, Mapping):
        raise ConformanceContractError("acceptance identities are required")
    required_strings = ("moonmindCommit", "moonmindBuild", "profileVersion", "protocolVersion")
    if any(not isinstance(identities.get(key), str) or not identities[key].strip() for key in required_strings):
        raise ConformanceContractError("complete build, profile, and protocol identities are required")
    images = identities.get("images")
    if not isinstance(images, Mapping) or any(
        not isinstance(images.get(role), str) or not _DIGEST_REF.fullmatch(images[role])
        for role in ("server", "host")
    ):
        raise ConformanceContractError("published server and unchanged host images must be digest-pinned")

    cleanup = _passed_evidence(source.get("cleanup"), label="cleanup")
    if cleanup.get("historicalEvidencePreserved") is not True or cleanup.get("leasesReleased") is not True:
        raise ConformanceContractError("cleanup must preserve history and release leases")

    report = {
        "schemaVersion": SCHEMA_VERSION,
        "issue": "MoonLadderStudios/MoonMind#3425",
        "status": "passed",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "producer": source.get("producer"),
        "identities": dict(identities),
        "prerequisites": accepted_prerequisites,
        "sections": accepted_sections,
        "cleanup": cleanup,
    }
    if not isinstance(report["producer"], str) or not report["producer"].strip():
        raise ConformanceContractError("trusted workflow producer identity is required")
    assert_secret_free(report)
    return report

