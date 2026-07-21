"""Final embedded-mode acceptance report contract for issue #3425."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Mapping

from moonmind.omnigent.conformance import ConformanceContractError, assert_secret_free

SCHEMA_VERSION = "moonmind.omnigent.embedded-acceptance/v1"
EVIDENCE_SCHEMA_VERSION = "moonmind.omnigent.embedded-acceptance-evidence/v1"
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


def _passed_evidence(
    item: Any,
    *,
    label: str,
    claim: str,
    evidence_objects: Mapping[str, Any],
    identities: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(item, Mapping) or item.get("status") != "passed":
        raise ConformanceContractError(f"{label} did not pass")
    refs = item.get("evidenceRefs")
    if not isinstance(refs, list) or not refs or not all(
        isinstance(ref, str) and ref.strip() for ref in refs
    ):
        raise ConformanceContractError(f"{label} requires durable evidence refs")
    for ref in refs:
        evidence = evidence_objects.get(ref)
        if not isinstance(evidence, Mapping):
            raise ConformanceContractError(f"{label} evidence ref is unresolved: {ref}")
        if evidence.get("schemaVersion") != EVIDENCE_SCHEMA_VERSION:
            raise ConformanceContractError(f"{label} evidence has an unsupported schema")
        if evidence.get("claim") != claim or evidence.get("status") != "passed":
            raise ConformanceContractError(f"{label} evidence does not prove its claim")
        if evidence.get("identities") != identities:
            raise ConformanceContractError(f"{label} evidence is for different identities")
        cases = evidence.get("cases")
        if not isinstance(cases, Mapping) or not cases or any(
            not isinstance(case, Mapping)
            or case.get("status") != "passed"
            or not isinstance(case.get("evidenceRefs"), list)
            or not case["evidenceRefs"]
            or not all(
                isinstance(case_ref, str) and case_ref.strip()
                for case_ref in case["evidenceRefs"]
            )
            for case in cases.values()
        ):
            raise ConformanceContractError(f"{label} evidence cases are incomplete")
        if evidence.get("secretScan") != "passed" or evidence.get("cleanup") != "passed":
            raise ConformanceContractError(f"{label} evidence failed safety checks")
        if (
            not isinstance(evidence.get("generatedAt"), str)
            or not evidence["generatedAt"].strip()
            or not isinstance(evidence.get("producer"), str)
            or not evidence["producer"].strip()
        ):
            raise ConformanceContractError(f"{label} evidence lacks provenance")
        assert_secret_free(evidence)
    return dict(item)


def build_embedded_acceptance_report(source: Mapping[str, Any]) -> dict[str, Any]:
    """Validate all controlling lanes and return the publishable report.

    Live/provider execution may produce the input in separate jobs, but no
    partial, skipped, mutable-image, or unattested result can become the final
    support gate.
    """
    identities = source.get("identities")
    if not isinstance(identities, Mapping):
        raise ConformanceContractError("acceptance identities are required")
    required_strings = (
        "moonmindCommit",
        "moonmindBuild",
        "profileVersion",
        "protocolVersion",
    )
    if any(
        not isinstance(identities.get(key), str) or not identities[key].strip()
        for key in required_strings
    ):
        raise ConformanceContractError(
            "complete build, profile, and protocol identities are required"
        )
    images = identities.get("images")
    if not isinstance(images, Mapping) or any(
        not isinstance(images.get(role), str) or not _DIGEST_REF.fullmatch(images[role])
        for role in ("server", "host")
    ):
        raise ConformanceContractError(
            "published server and unchanged host images must be digest-pinned"
        )

    evidence_objects = source.get("evidenceObjects")
    if not isinstance(evidence_objects, Mapping):
        raise ConformanceContractError("resolved acceptance evidence objects are required")
    prerequisites = source.get("prerequisites")
    if not isinstance(prerequisites, Mapping):
        raise ConformanceContractError("embedded prerequisites are required")
    accepted_prerequisites = {
        issue: _passed_evidence(
            prerequisites.get(issue),
            label=f"prerequisite #{issue}",
            claim=f"prerequisite:{issue}",
            evidence_objects=evidence_objects,
            identities=identities,
        )
        for issue in REQUIRED_PREREQUISITES
    }

    sections = source.get("sections")
    if not isinstance(sections, Mapping):
        raise ConformanceContractError("embedded acceptance sections are required")
    accepted_sections = {
        section: _passed_evidence(
            sections.get(section),
            label=f"section {section}",
            claim=f"section:{section}",
            evidence_objects=evidence_objects,
            identities=identities,
        )
        for section in REQUIRED_SECTIONS
    }

    cleanup = _passed_evidence(
        source.get("cleanup"),
        label="cleanup",
        claim="cleanup",
        evidence_objects=evidence_objects,
        identities=identities,
    )
    if (
        cleanup.get("historicalEvidencePreserved") is not True
        or cleanup.get("leasesReleased") is not True
    ):
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
