"""Machine-readable acceptance report contract for native Workflow Chat.

MoonLadderStudios/MoonMind#3642 §9 (the controlling gate). This is the single
versioned, fail-closed report that gates making native Workflow Chat primary. It
is only ever emitted with ``status == "passed"`` when *every* required scenario
row passes and resolves to durable, current, secret-free, image-pinned evidence.

The gate spans two lanes and requires both:

* the **deterministic** browser-to-fake-server lane, which drives the real
  MoonMind binding, facade, capability policy, outbound scan, native-UI serving,
  fallback, and evidence boundaries against a controllable fake Omnigent server
  (buildable hermetically in CI); and
* the **protected-live** lane, which runs the journey against immutable stock
  Omnigent server/host images with a real enrolled Provider Profile and proves
  every evidence ref resolves after the live resources are removed.

Implementation PRs and lower-level bridge/unit tests are necessary but never
sufficient: a row is accepted only with resolved evidence that carries the same
build/commit/contract identities the report claims, so partial, skipped,
mutable-image, stale, revoked, or unattested input can never become the gate.

This mirrors the fail-closed structure of
:mod:`moonmind.omnigent.embedded_acceptance` (issue #3425) — deliberately, so
the two publication gates read the same way — but carries the native-chat
scenario rows, the versioned route/feature compatibility manifest digest, the
three (server/ui/host) image digests, and the safe identity/profile refs this
journey produces.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from moonmind.omnigent.conformance import ConformanceContractError, assert_secret_free

SCHEMA_VERSION = "moonmind.omnigent.native-chat-acceptance/v1"
EVIDENCE_SCHEMA_VERSION = "moonmind.omnigent.native-chat-acceptance-evidence/v1"
CASE_EVIDENCE_SCHEMA_VERSION = (
    "moonmind.omnigent.native-chat-acceptance-case-evidence/v1"
)
ISSUE = "MoonLadderStudios/MoonMind#3642"

# The controlling scenario rows. Each maps 1:1 to an acceptance criterion in the
# brief (the twelfth criterion — "a machine-readable passing report gates
# production readiness" — is this report itself, not a row).
SCENARIO_DETERMINISTIC_JOURNEY = "deterministic-browser-journey"
SCENARIO_BINDING_AUTHORIZATION = "binding-authorization-isolation"
SCENARIO_CREDENTIAL_ISOLATION = "credential-browser-isolation"
SCENARIO_CAPABILITY_POLICY = "capability-policy-immutability"
SCENARIO_OUTBOUND_SCAN = "high-security-outbound-scan"
SCENARIO_NATIVE_UI_TRANSPORTS = "native-ui-and-transports"
SCENARIO_DIAGNOSTIC_FALLBACK = "diagnostic-fallback"
SCENARIO_TERMINAL_CONTINUATION = "terminal-and-continuation"
SCENARIO_PROTECTED_STOCK_IMAGE = "protected-stock-image-journey"
SCENARIO_EVIDENCE_DURABILITY = "evidence-durability-and-secret-scan"
SCENARIO_TELEMETRY_ROLLOUT = "telemetry-and-rollout"

REQUIRED_SCENARIOS: tuple[str, ...] = (
    SCENARIO_DETERMINISTIC_JOURNEY,
    SCENARIO_BINDING_AUTHORIZATION,
    SCENARIO_CREDENTIAL_ISOLATION,
    SCENARIO_CAPABILITY_POLICY,
    SCENARIO_OUTBOUND_SCAN,
    SCENARIO_NATIVE_UI_TRANSPORTS,
    SCENARIO_DIAGNOSTIC_FALLBACK,
    SCENARIO_TERMINAL_CONTINUATION,
    SCENARIO_PROTECTED_STOCK_IMAGE,
    SCENARIO_EVIDENCE_DURABILITY,
    SCENARIO_TELEMETRY_ROLLOUT,
)

# The lane each scenario is proven in. Both lanes are required for the gate; the
# lane is recorded so downstream verification can tell hermetic deterministic
# evidence apart from protected stock-image evidence.
LANE_DETERMINISTIC = "deterministic"
LANE_PROTECTED_LIVE = "protected_live"
SCENARIO_LANES: dict[str, str] = {
    SCENARIO_DETERMINISTIC_JOURNEY: LANE_DETERMINISTIC,
    SCENARIO_BINDING_AUTHORIZATION: LANE_DETERMINISTIC,
    SCENARIO_CREDENTIAL_ISOLATION: LANE_DETERMINISTIC,
    SCENARIO_CAPABILITY_POLICY: LANE_DETERMINISTIC,
    SCENARIO_OUTBOUND_SCAN: LANE_DETERMINISTIC,
    SCENARIO_NATIVE_UI_TRANSPORTS: LANE_DETERMINISTIC,
    SCENARIO_DIAGNOSTIC_FALLBACK: LANE_DETERMINISTIC,
    SCENARIO_TELEMETRY_ROLLOUT: LANE_DETERMINISTIC,
    SCENARIO_TERMINAL_CONTINUATION: LANE_PROTECTED_LIVE,
    SCENARIO_PROTECTED_STOCK_IMAGE: LANE_PROTECTED_LIVE,
    SCENARIO_EVIDENCE_DURABILITY: LANE_PROTECTED_LIVE,
}

# Required contract-version identity keys the evidence must pin. These name the
# exact runtime-neutral contracts the native-chat journey exercised so a report
# cannot be reused across an incompatible facade/UI/scan contract change.
REQUIRED_CONTRACT_VERSIONS: tuple[str, ...] = (
    "nativeUiBootstrap",
    "nativeUiRouteFeature",
    "outboundScan",
    "telemetry",
)

# Safe, opaque identities the report must carry (never provider/host/credential
# identities). They are secret-scanned like everything else.
REQUIRED_SAFE_IDENTITIES: tuple[str, ...] = (
    "workflowRef",
    "runRef",
    "stepRef",
    "agentRunRef",
    "bindingRef",
)
REQUIRED_PROFILE_REFS: tuple[str, ...] = (
    "profileRef",
    "launchPolicyRef",
    "effectiveLaunchSnapshotRef",
    "providerProfileRef",
)

_DIGEST_REF = re.compile(r"^.+@sha256:[0-9a-f]{64}$")
_BARE_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def _active_evidence(item: Mapping[str, Any], *, label: str, now: datetime) -> None:
    try:
        generated_at = datetime.fromisoformat(
            str(item["generatedAt"]).replace("Z", "+00:00")
        )
        expires_at = datetime.fromisoformat(
            str(item["expiresAt"]).replace("Z", "+00:00")
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ConformanceContractError(
            f"{label} has invalid generation or expiry time"
        ) from exc
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
    if (
        not isinstance(channel_refs, list)
        or not channel_refs
        or not all(isinstance(ref, str) and ref.strip() for ref in channel_refs)
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
    expected_lane: str | None = None,
) -> dict[str, Any]:
    if not isinstance(item, Mapping) or item.get("status") != "passed":
        raise ConformanceContractError(f"{label} did not pass")
    if expected_lane is not None and item.get("lane") != expected_lane:
        raise ConformanceContractError(
            f"{label} is not attested in the {expected_lane} lane"
        )
    refs = item.get("evidenceRefs")
    if (
        not isinstance(refs, list)
        or not refs
        or not all(isinstance(ref, str) and ref.strip() for ref in refs)
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
            raise ConformanceContractError(
                f"{label} evidence is for different identities"
            )
        _active_evidence(evidence, label=f"{label} evidence", now=now)
        cases = evidence.get("cases")
        if (
            not isinstance(cases, Mapping)
            or not cases
            or any(
                not isinstance(case, Mapping)
                or case.get("status") != "passed"
                or not isinstance(case.get("evidenceRefs"), list)
                or not case["evidenceRefs"]
                or not all(
                    isinstance(case_ref, str) and case_ref.strip()
                    for case_ref in case["evidenceRefs"]
                )
                for case in cases.values()
            )
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


def _validate_identities(
    source: Mapping[str, Any], *, expected_commit: str | None
) -> dict[str, Any]:
    identities = source.get("identities")
    if not isinstance(identities, Mapping):
        raise ConformanceContractError("acceptance identities are required")
    if any(
        not isinstance(identities.get(key), str) or not identities[key].strip()
        for key in ("moonmindCommit", "moonmindBuild", "hostArchitecture")
    ):
        raise ConformanceContractError(
            "complete build, commit, and host-architecture identities are required"
        )
    if expected_commit is not None and identities["moonmindCommit"] != expected_commit:
        raise ConformanceContractError("acceptance evidence is for a different commit")
    contract_versions = identities.get("contractVersions")
    if not isinstance(contract_versions, Mapping) or any(
        not isinstance(contract_versions.get(key), str)
        or not contract_versions[key].strip()
        for key in REQUIRED_CONTRACT_VERSIONS
    ):
        raise ConformanceContractError(
            "native-chat contract versions must be pinned"
        )
    images = identities.get("images")
    if not isinstance(images, Mapping) or any(
        not isinstance(images.get(role), str) or not _DIGEST_REF.fullmatch(images[role])
        for role in ("server", "ui", "host")
    ):
        raise ConformanceContractError(
            "stock server, native UI, and host images must be digest-pinned"
        )
    manifest_digest = identities.get("compatibilityManifestDigest")
    if not isinstance(manifest_digest, str) or not _BARE_DIGEST.fullmatch(
        manifest_digest
    ):
        raise ConformanceContractError(
            "a sha256 route/feature compatibility manifest digest is required"
        )
    return dict(identities)


def _validate_safe_refs(source: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    safe_identities = source.get("safeIdentities")
    if not isinstance(safe_identities, Mapping) or any(
        not isinstance(safe_identities.get(key), str) or not safe_identities[key].strip()
        for key in REQUIRED_SAFE_IDENTITIES
    ):
        raise ConformanceContractError(
            "safe workflow/run/step/agentRun/binding identities are required"
        )
    profile_refs = source.get("profilePolicyRefs")
    if not isinstance(profile_refs, Mapping) or any(
        not isinstance(profile_refs.get(key), str) or not profile_refs[key].strip()
        for key in REQUIRED_PROFILE_REFS
    ):
        raise ConformanceContractError(
            "safe profile/policy/effective-launch/provider-profile refs are required"
        )
    return dict(safe_identities), dict(profile_refs)


def build_native_chat_acceptance_report(
    source: Mapping[str, Any],
    *,
    now: datetime | None = None,
    expected_commit: str | None = None,
    evidence_resolver: Callable[[str], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Validate every controlling row and return the publishable gate report.

    Deterministic (browser-to-fake-server) and protected-live (stock-image)
    lanes may be produced by separate jobs, but no partial, skipped,
    mutable-image, stale, or unattested result can become the final rollout
    gate: every required scenario must pass in its expected lane with resolved,
    current, secret-free evidence bound to the same identities.
    """

    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ConformanceContractError("acceptance validation time must include a timezone")

    identities = _validate_identities(source, expected_commit=expected_commit)
    safe_identities, profile_refs = _validate_safe_refs(source)

    if evidence_resolver is None:
        evidence_objects = source.get("evidenceObjects")
        if not isinstance(evidence_objects, Mapping):
            raise ConformanceContractError(
                "resolved acceptance evidence objects are required"
            )
    else:
        refs: set[str] = set()
        scenarios_source = source.get("scenarios")
        if isinstance(scenarios_source, Mapping):
            for item in scenarios_source.values():
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

    scenarios = source.get("scenarios")
    if not isinstance(scenarios, Mapping):
        raise ConformanceContractError("native-chat acceptance scenarios are required")
    accepted_scenarios = {
        scenario: {
            **_passed_evidence(
                scenarios.get(scenario),
                label=f"scenario {scenario}",
                claim=f"scenario:{scenario}",
                evidence_objects=evidence_objects,
                identities=identities,
                now=now,
                expected_lane=SCENARIO_LANES[scenario],
            ),
            "lane": SCENARIO_LANES[scenario],
        }
        for scenario in REQUIRED_SCENARIOS
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

    secret_scan = source.get("secretScan")
    if not isinstance(secret_scan, Mapping) or secret_scan.get("status") != "passed":
        raise ConformanceContractError(
            "the retained-evidence secret scan must pass"
        )

    generated_at = now.isoformat()
    report = {
        "schemaVersion": SCHEMA_VERSION,
        "issue": ISSUE,
        "status": "passed",
        "generatedAt": generated_at,
        "expiresAt": source.get("expiresAt"),
        "supersedes": source.get("supersedes"),
        "producer": source.get("producer"),
        "identities": identities,
        "safeIdentities": safe_identities,
        "profilePolicyRefs": profile_refs,
        "lanes": {
            LANE_DETERMINISTIC: sorted(
                name for name, lane in SCENARIO_LANES.items()
                if lane == LANE_DETERMINISTIC
            ),
            LANE_PROTECTED_LIVE: sorted(
                name for name, lane in SCENARIO_LANES.items()
                if lane == LANE_PROTECTED_LIVE
            ),
        },
        "scenarios": accepted_scenarios,
        "cleanup": cleanup,
        "secretScan": dict(secret_scan),
    }
    if not isinstance(report["producer"], str) or not report["producer"].strip():
        raise ConformanceContractError("trusted workflow producer identity is required")
    expires_at = report["expiresAt"]
    if not isinstance(expires_at, str) or not expires_at.strip():
        raise ConformanceContractError("acceptance report requires an expiry")
    try:
        parsed_expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ConformanceContractError("acceptance report expiry is malformed") from exc
    if parsed_expiry.tzinfo is None or parsed_expiry <= now:
        raise ConformanceContractError("acceptance report is already expired")
    assert_secret_free(report)
    return report


__all__ = [
    "CASE_EVIDENCE_SCHEMA_VERSION",
    "EVIDENCE_SCHEMA_VERSION",
    "ISSUE",
    "LANE_DETERMINISTIC",
    "LANE_PROTECTED_LIVE",
    "REQUIRED_CONTRACT_VERSIONS",
    "REQUIRED_PROFILE_REFS",
    "REQUIRED_SAFE_IDENTITIES",
    "REQUIRED_SCENARIOS",
    "SCENARIO_BINDING_AUTHORIZATION",
    "SCENARIO_CAPABILITY_POLICY",
    "SCENARIO_CREDENTIAL_ISOLATION",
    "SCENARIO_DETERMINISTIC_JOURNEY",
    "SCENARIO_DIAGNOSTIC_FALLBACK",
    "SCENARIO_EVIDENCE_DURABILITY",
    "SCENARIO_LANES",
    "SCENARIO_NATIVE_UI_TRANSPORTS",
    "SCENARIO_OUTBOUND_SCAN",
    "SCENARIO_PROTECTED_STOCK_IMAGE",
    "SCENARIO_TELEMETRY_ROLLOUT",
    "SCENARIO_TERMINAL_CONTINUATION",
    "SCHEMA_VERSION",
    "build_native_chat_acceptance_report",
]
