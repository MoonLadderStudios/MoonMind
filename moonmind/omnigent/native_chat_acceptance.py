"""Fail-closed protected acceptance contract for native Workflow Chat.

MoonLadderStudios/MoonMind#3642 requires one report that binds deterministic
browser evidence and a protected stock-image observation.  This module only
validates observed evidence; it cannot turn configured, skipped, or synthetic
results into release authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from moonmind.omnigent.conformance import ConformanceContractError, assert_secret_free

SCHEMA_VERSION = "moonmind.omnigent.native-chat-acceptance/v1"
OBSERVATION_VERSION = "moonmind.omnigent.native-chat-observation/v1"
COMPATIBILITY_VERSION = "moonmind.omnigent.native-chat-compatibility/v1"
ISSUE = "MoonLadderStudios/MoonMind#3642"

REQUIRED_LANES = (
    "browser-product-journey",
    "authority-isolation",
    "browser-network-isolation",
    "immutable-capability-policy",
    "high-security-outbound-scan",
    "native-ui-and-transports",
    "terminal-fallback-continuation",
    "protected-stock-image-journey",
    "retained-evidence-and-cleanup",
    "readiness-telemetry-rollout",
)
REQUIRED_TRANSPORTS = ("http", "sse", "websocket", "terminal", "resource")
REQUIRED_CHANNELS = (
    "artifacts", "events", "diagnostics", "mutationAudit", "screenshots"
)
REQUIRED_TELEMETRY = (
    "bindingResolution", "nativeUiCompatibility", "transportOutcomes",
    "authorizationDenials", "capabilityDenials", "securityScanOutcomes",
    "nativeUiLifecycle", "mutationOutcomes", "diagnosticFallback",
    "terminalReplay", "continuationCreation", "upstreamHealth",
)
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def _time(value: Any, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ConformanceContractError(f"{label} is invalid") from exc
    if parsed.tzinfo is None:
        raise ConformanceContractError(f"{label} must include a timezone")
    return parsed


def _resolve_ref(root: Path, ref: str) -> Path:
    if not ref.startswith("artifact://"):
        raise ConformanceContractError("evidence refs must use artifact://")
    relative = ref.removeprefix("artifact://")
    candidate = (root / relative).resolve()
    if root != candidate and root not in candidate.parents:
        raise ConformanceContractError("evidence ref escapes the evidence root")
    if not candidate.is_file():
        raise ConformanceContractError(f"evidence ref is unresolved: {ref}")
    return candidate


def build_native_chat_acceptance_report(
    source: Mapping[str, Any], *, evidence_root: Path, expected_commit: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate and assemble the sole #3642 production-readiness artifact."""

    now = now or datetime.now(timezone.utc)
    root = evidence_root.resolve()
    identity = source.get("identity")
    if not isinstance(identity, Mapping):
        raise ConformanceContractError("release identity is required")
    commit = identity.get("moonmindCommit")
    if not isinstance(commit, str) or not commit:
        raise ConformanceContractError("MoonMind commit is required")
    if expected_commit is not None and commit != expected_commit:
        raise ConformanceContractError("acceptance evidence is for a different commit")
    for name in ("serverImageDigest", "uiImageDigest", "hostImageDigest",
                 "profileDigest", "policyDigest", "compatibilityManifestDigest"):
        if not isinstance(identity.get(name), str) or not _DIGEST.fullmatch(identity[name]):
            raise ConformanceContractError(f"{name} must be an immutable SHA-256 digest")
    if not isinstance(identity.get("architecture"), str) or not identity["architecture"]:
        raise ConformanceContractError("architecture is required")

    generated = _time(source.get("generatedAt"), "generatedAt")
    expires = _time(source.get("expiresAt"), "expiresAt")
    if generated > now or expires <= now or expires <= generated:
        raise ConformanceContractError("acceptance evidence is outside its validity period")
    if source.get("supersededBy") is not None:
        raise ConformanceContractError("acceptance evidence is superseded")

    lanes = source.get("lanes")
    if not isinstance(lanes, Mapping) or set(lanes) != set(REQUIRED_LANES):
        raise ConformanceContractError("complete native Chat lane inventory is required")
    resolved: dict[str, Any] = {}
    for lane_name in REQUIRED_LANES:
        lane = lanes[lane_name]
        if not isinstance(lane, Mapping) or lane.get("status") != "passed":
            raise ConformanceContractError(f"lane {lane_name} did not pass")
        ref = lane.get("evidenceRef")
        path = _resolve_ref(root, ref)
        observation = json.loads(path.read_text(encoding="utf-8"))
        if (observation.get("schemaVersion") != OBSERVATION_VERSION
                or observation.get("lane") != lane_name
                or observation.get("status") != "passed"
                or observation.get("identity") != identity):
            raise ConformanceContractError(f"lane {lane_name} evidence is not bound to this release")
        digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        if lane.get("sha256") != digest:
            raise ConformanceContractError(f"lane {lane_name} evidence digest mismatch")
        assert_secret_free(observation)
        resolved[lane_name] = {"evidenceRef": ref, "sha256": digest}

    compatibility_ref = source.get("compatibilityManifestRef")
    compatibility_path = _resolve_ref(root, compatibility_ref)
    compatibility = json.loads(compatibility_path.read_text(encoding="utf-8"))
    if compatibility.get("schemaVersion") != COMPATIBILITY_VERSION:
        raise ConformanceContractError("native route/feature compatibility manifest is invalid")
    actual_compatibility_digest = "sha256:" + hashlib.sha256(compatibility_path.read_bytes()).hexdigest()
    if actual_compatibility_digest != identity["compatibilityManifestDigest"]:
        raise ConformanceContractError("compatibility manifest digest mismatch")
    if set(compatibility.get("transports", {})) != set(REQUIRED_TRANSPORTS) or any(
        value != "passed" for value in compatibility["transports"].values()
    ):
        raise ConformanceContractError("all claimed native transports must pass")

    refs = source.get("retainedEvidence")
    if not isinstance(refs, Mapping) or set(refs) != set(REQUIRED_CHANNELS):
        raise ConformanceContractError("complete retained evidence channels are required")
    for ref in refs.values():
        _resolve_ref(root, ref)
    if source.get("retainedEvidenceSecretScan") != "passed":
        raise ConformanceContractError("all retained evidence must pass secret scanning")
    telemetry = source.get("telemetry")
    if not isinstance(telemetry, Mapping) or set(telemetry) != set(REQUIRED_TELEMETRY):
        raise ConformanceContractError("complete bounded native Chat telemetry is required")
    if any(not isinstance(value, Mapping) or not value for value in telemetry.values()):
        raise ConformanceContractError("native Chat telemetry groups must be populated")
    rollout = source.get("rollout")
    required_rollout = ("canaryPolicy", "disableInteractiveChat", "historicalReads",
                        "noRuntimeFallback", "temporaryFlagRetirement")
    if not isinstance(rollout, Mapping) or any(rollout.get(key) is not True for key in required_rollout):
        raise ConformanceContractError("rollout and rollback evidence is incomplete")

    report = {
        "schemaVersion": SCHEMA_VERSION, "issue": ISSUE, "status": "passed",
        "generatedAt": generated.isoformat(), "expiresAt": expires.isoformat(),
        "supersedes": source.get("supersedes"), "producer": source.get("producer"),
        "identity": dict(identity), "lanes": resolved,
        "compatibilityManifestRef": compatibility_ref,
        "retainedEvidence": dict(refs), "retainedEvidenceSecretScan": "passed",
        "telemetry": dict(telemetry), "rollout": dict(rollout),
    }
    if not isinstance(report["producer"], str) or not report["producer"]:
        raise ConformanceContractError("trusted producer identity is required")
    assert_secret_free(report)
    return report
