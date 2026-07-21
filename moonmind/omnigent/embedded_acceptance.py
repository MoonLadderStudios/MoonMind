"""Final embedded-mode acceptance report contract for issue #3425."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from moonmind.omnigent.conformance import ConformanceContractError, assert_secret_free

SCHEMA_VERSION = "moonmind.omnigent.embedded-acceptance/v1"
EVIDENCE_SCHEMA_VERSION = "moonmind.omnigent.embedded-acceptance-evidence/v1"
CASE_EVIDENCE_SCHEMA_VERSION = "moonmind.omnigent.embedded-acceptance-case-evidence/v1"
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


def _active_evidence(item: Mapping[str, Any], *, label: str, now: datetime) -> None:
    try:
        generated_at = datetime.fromisoformat(str(item["generatedAt"]).replace("Z", "+00:00"))
        expires_at = datetime.fromisoformat(str(item["expiresAt"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError) as exc:
        raise ConformanceContractError(f"{label} has invalid generation or expiry time") from exc
    if generated_at.tzinfo is None or expires_at.tzinfo is None:
        raise ConformanceContractError(f"{label} timestamps must include a timezone")
    if generated_at > now or expires_at <= generated_at or expires_at <= now:
        raise ConformanceContractError(f"{label} is not within its validity period")
    if item.get("revokedAt") is not None:
        raise ConformanceContractError(f"{label} is revoked")
    if item.get("supersededBy") is not None:
        raise ConformanceContractError(f"{label} is superseded")


def _passed_case_evidence(
    evidence: Any,
    *,
    label: str,
    claim: str,
    case_name: str,
    identities: Mapping[str, Any],
    now: datetime,
) -> None:
    if not isinstance(evidence, Mapping):
        raise ConformanceContractError(f"{label} evidence ref is unresolved")
    if evidence.get("schemaVersion") != CASE_EVIDENCE_SCHEMA_VERSION:
        raise ConformanceContractError(f"{label} evidence has an unsupported schema")
    if (
        evidence.get("claim") != claim
        or evidence.get("case") != case_name
        or evidence.get("status") != "passed"
    ):
        raise ConformanceContractError(f"{label} evidence does not prove its case")
    if evidence.get("identities") != identities:
        raise ConformanceContractError(f"{label} evidence is for different identities")
    if evidence.get("secretScan") != "passed" or evidence.get("cleanup") != "passed":
        raise ConformanceContractError(f"{label} evidence failed safety checks")
    channel_refs = evidence.get("evidenceRefs")
    if not isinstance(channel_refs, list) or not channel_refs or not all(
        isinstance(ref, str) and ref.strip() for ref in channel_refs
    ):
        raise ConformanceContractError(f"{label} evidence lacks channel refs")
    if not isinstance(evidence.get("producer"), str) or not evidence["producer"].strip():
        raise ConformanceContractError(f"{label} evidence lacks provenance")
    _active_evidence(evidence, label=f"{label} evidence", now=now)
    assert_secret_free(evidence)


def _passed_evidence(
    item: Any,
    *,
    label: str,
    claim: str,
    evidence_objects: Mapping[str, Any],
    identities: Mapping[str, Any],
    now: datetime,
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
        _active_evidence(evidence, label=f"{label} evidence", now=now)
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
        for case_name, case in cases.items():
            for case_ref in case["evidenceRefs"]:
                _passed_case_evidence(
                    evidence_objects.get(case_ref),
                    label=f"{label} case {case_name}",
                    claim=claim,
                    case_name=case_name,
                    identities=identities,
                    now=now,
                )
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


def build_embedded_acceptance_report(
    source: Mapping[str, Any],
    *,
    now: datetime | None = None,
    expected_commit: str | None = None,
    evidence_resolver: Callable[[str], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Validate all controlling lanes and return the publishable report.

    Live/provider execution may produce the input in separate jobs, but no
    partial, skipped, mutable-image, or unattested result can become the final
    support gate.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ConformanceContractError("acceptance validation time must include a timezone")
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
    if expected_commit is not None and identities["moonmindCommit"] != expected_commit:
        raise ConformanceContractError("acceptance evidence is for a different commit")
    images = identities.get("images")
    if not isinstance(images, Mapping) or any(
        not isinstance(images.get(role), str) or not _DIGEST_REF.fullmatch(images[role])
        for role in ("server", "host")
    ):
        raise ConformanceContractError(
            "published server and unchanged host images must be digest-pinned"
        )

    if evidence_resolver is None:
        evidence_objects = source.get("evidenceObjects")
        if not isinstance(evidence_objects, Mapping):
            raise ConformanceContractError("resolved acceptance evidence objects are required")
    else:
        refs: set[str] = set()
        for collection in (source.get("prerequisites"), source.get("sections")):
            if isinstance(collection, Mapping):
                for item in collection.values():
                    if isinstance(item, Mapping):
                        refs.update(item.get("evidenceRefs") or [])
        cleanup_source = source.get("cleanup")
        if isinstance(cleanup_source, Mapping):
            refs.update(cleanup_source.get("evidenceRefs") or [])
        evidence_objects = {}
        pending = list(refs)
        while pending:
            ref = pending.pop()
            if ref in evidence_objects:
                continue
            resolved = dict(evidence_resolver(ref))
            evidence_objects[ref] = resolved
            cases = resolved.get("cases")
            if isinstance(cases, Mapping):
                for case in cases.values():
                    if isinstance(case, Mapping):
                        pending.extend(case.get("evidenceRefs") or [])
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
            now=now,
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
            now=now,
        )
        for section in REQUIRED_SECTIONS
    }

    cleanup = _passed_evidence(
        source.get("cleanup"),
        label="cleanup",
        claim="cleanup",
        evidence_objects=evidence_objects,
        identities=identities,
        now=now,
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
        "generatedAt": now.isoformat(),
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
