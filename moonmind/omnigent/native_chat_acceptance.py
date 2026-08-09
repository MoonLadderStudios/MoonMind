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
SCENARIO_EVIDENCE_VERSION = "moonmind.omnigent.native-chat-scenario-evidence/v1"
PRODUCER_VERSION = "moonmind.omnigent.native-chat-producer/v1"
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
REQUIRED_FEATURES = (
    "spa-assets", "deep-link-refresh", "embedded-mode", "full-page-mode",
    "transcript", "composer", "queue-steer", "tools-reasoning", "approvals",
    "files-diffs", "uploads-downloads", "terminals", "agents-tasks",
    "browser-pane", "multipart-binary", "reconnect-liveness",
)
REQUIRED_ACCESSIBILITY = (
    "mobile-responsive", "keyboard-shortcuts", "focus-transitions",
    "screen-reader", "reduced-motion", "large-session",
)
REQUIRED_SECURITY_CONTROLS = (
    "csp", "frame", "cors", "csrf", "origin", "cookie", "cache",
    "service-worker", "route-version-drift",
)
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

_LANE_PRODUCERS = {
    "protected-stock-image-journey": "protected-stock-image",
}
_PRODUCER_COMMAND = ["node", "tools/run_omnigent_native_chat_journey.mjs"]

# These are observable cases, not prose assertions.  Producers must emit every
# case with a terminal outcome and objective counters captured at the decisive
# browser/proxy/provider boundary.
REQUIRED_SCENARIOS: dict[str, tuple[str, ...]] = {
    "browser-product-journey": (
        "workflow-detail-chat", "binding-resolution", "embedded-native-ui",
        "normal-message", "queue-steer", "approval", "resources", "terminal",
        "agents-tasks", "reconnect", "terminal-cleanup-replay", "diagnostics",
        "linked-continuation",
    ),
    "authority-isolation": (
        "owner", "shared-viewer", "read-only-viewer", "approver-only",
        "unauthorized", "unknown-binding", "expired-binding", "revoked-binding",
        "cross-workflow-binding", "path-substitution", "query-substitution",
        "body-substitution", "header-substitution", "sse-cursor-substitution",
        "websocket-frame-substitution", "launch-authority-substitution",
        "authorization-change-http", "authorization-change-sse",
        "authorization-change-websocket", "archived-workflow", "cleaned-session",
        "non-enumerating-response",
    ),
    "browser-network-isolation": (
        "no-direct-upstream", "no-upstream-secret-in-browser",
        "no-moonmind-secret-upstream", "allowlisted-headers-only", "redirect",
        "error-body", "download", "websocket", "service-worker", "full-page-sso",
    ),
    "immutable-capability-policy": (
        "pinned-model-effort", "approval-authority-state", "read-only-controls",
        "active-terminal-controls", "direct-api-denial", "stale-profile",
        "stale-provider-generation", "stale-policy", "stale-launch-snapshot",
        "stale-session-epoch", "stale-turn", "stale-elicitation",
        "duplicate-mutation", "delivery-unknown-reconciliation",
        "unsupported-control",
    ),
    "high-security-outbound-scan": (
        "clean-message", "secret-message", "queued-steered", "slash-command",
        "approval-text", "reply-quote", "text-attachment", "upload-metadata",
        "idempotency-payload-change", "unknown-payload", "malformed-payload",
        "compressed-payload", "binary-payload", "oversized-payload",
        "uninspectable-payload", "scanner-unavailable", "scanner-error",
        "scanner-timeout",
    ),
    "native-ui-and-transports": REQUIRED_FEATURES + REQUIRED_ACCESSIBILITY,
    "terminal-fallback-continuation": (
        "native-ui-unavailable", "unsupported-runtime", "failed-before-stream",
        "retention-gap", "schema-incompatibility", "direct-runtime-history",
    ),
    "protected-stock-image-journey": ("stock-codex-product-path",),
    "retained-evidence-and-cleanup": (
        "refs-after-cleanup", "secret-scan-retained-bytes", "mutation-audit",
    ),
    "readiness-telemetry-rollout": (
        "bounded-metrics", "readiness-consumption", "canary", "rollback",
        "temporary-flag-retirement",
    ),
}


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


def _validate_evidence_record(root: Path, record: Mapping[str, Any], label: str) -> None:
    """Require a resolvable, digest-bound, secret-scanned evidence record."""

    ref = record.get("evidenceRef")
    path = _resolve_ref(root, ref)
    digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    if record.get("sha256") != digest:
        raise ConformanceContractError(f"{label} evidence digest mismatch")
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ConformanceContractError(f"{label} evidence is not secret-scannable text") from exc
    assert_secret_free(content)


def _validate_scenario_evidence(
    root: Path,
    record: Mapping[str, Any],
    *,
    lane: str,
    scenario_id: str,
) -> None:
    """Validate objective observations instead of trusting result booleans.

    The scenario payload is emitted by a repository-owned runner after it has
    observed the decisive browser/proxy/provider boundary.  Side-effect counts
    are derived from the captured request inventory and therefore cannot drift
    from the referenced bytes.
    """

    _validate_evidence_record(root, record, f"scenario {scenario_id}")
    path = _resolve_ref(root, str(record["evidenceRef"]))
    try:
        evidence = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConformanceContractError(
            f"scenario {scenario_id} evidence is not structured JSON"
        ) from exc
    producer = evidence.get("producer")
    expected_kind = _LANE_PRODUCERS.get(lane, "deterministic-browser-fake-server")
    if (
        evidence.get("schemaVersion") != SCENARIO_EVIDENCE_VERSION
        or evidence.get("lane") != lane
        or evidence.get("scenarioId") != scenario_id
        or not isinstance(producer, Mapping)
        or producer.get("schemaVersion") != PRODUCER_VERSION
        or producer.get("kind") != expected_kind
        or producer.get("exitCode") != 0
        or producer.get("command") != _PRODUCER_COMMAND
    ):
        raise ConformanceContractError(
            f"scenario {scenario_id} lacks trusted producer provenance"
        )
    assertions = evidence.get("observedAssertions")
    requests = evidence.get("upstreamRequests")
    if (
        not isinstance(assertions, list)
        or not assertions
        or any(not isinstance(item, str) or not item for item in assertions)
        or not isinstance(requests, list)
        or any(not isinstance(item, Mapping) for item in requests)
        or record.get("upstreamSideEffects") != len(requests)
    ):
        raise ConformanceContractError(
            f"scenario {scenario_id} observations do not prove their outcome"
        )
    assert_secret_free(evidence)


def assemble_native_chat_acceptance_input(
    producer_root: Path, *, output_root: Path
) -> dict[str, Any]:
    """Assemble validator input from complete repository-producer evidence.

    The producer directory is an interchange contract, not a source of trusted
    booleans.  Every required scenario must already exist as its own objective
    scenario-evidence document.  This function only validates provenance,
    copies no bytes, derives digest-bound lane observations, and wires the
    remaining evidence records from ``manifest.json``.
    """

    producer_root = producer_root.resolve()
    output_root = output_root.resolve()
    manifest_path = producer_root / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConformanceContractError("producer manifest is unreadable") from exc
    if not isinstance(manifest, Mapping):
        raise ConformanceContractError("producer manifest must be an object")
    identity = manifest.get("identity")
    if not isinstance(identity, Mapping):
        raise ConformanceContractError("producer manifest identity is required")

    scenario_root = producer_root / "scenarios"
    lanes: dict[str, Any] = {}
    output_root.mkdir(parents=True, exist_ok=True)
    lane_root = output_root / "lanes"
    lane_root.mkdir(parents=True, exist_ok=True)
    for lane, required_scenarios in REQUIRED_SCENARIOS.items():
        scenarios: list[dict[str, Any]] = []
        for scenario_id in required_scenarios:
            path = scenario_root / lane / f"{scenario_id}.json"
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ConformanceContractError(
                    f"producer scenario is unreadable: {lane}/{scenario_id}"
                ) from exc
            relative = path.relative_to(output_root) if output_root in path.parents else None
            if relative is None:
                raise ConformanceContractError(
                    "producer scenarios must be contained by the evidence root"
                )
            record = {
                "id": scenario_id,
                "outcome": "passed",
                "upstreamSideEffects": len(payload.get("upstreamRequests", []))
                if isinstance(payload.get("upstreamRequests"), list) else -1,
                "evidenceRef": f"artifact://{relative.as_posix()}",
                "sha256": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            _validate_scenario_evidence(
                output_root, record, lane=lane, scenario_id=scenario_id
            )
            scenarios.append(record)
        lane_payload = {
            "schemaVersion": OBSERVATION_VERSION,
            "lane": lane,
            "status": "passed",
            "identity": dict(identity),
            "scenarios": scenarios,
        }
        lane_path = lane_root / f"{lane}.json"
        lane_path.write_text(
            json.dumps(lane_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        lanes[lane] = {
            "status": "passed",
            "evidenceRef": f"artifact://lanes/{lane}.json",
            "sha256": "sha256:" + hashlib.sha256(lane_path.read_bytes()).hexdigest(),
        }

    assembled = dict(manifest)
    assembled.pop("schemaVersion", None)
    assembled["lanes"] = lanes
    return assembled


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
        scenarios = observation.get("scenarios")
        required = REQUIRED_SCENARIOS[lane_name]
        if not isinstance(scenarios, list) or {
            item.get("id") for item in scenarios if isinstance(item, Mapping)
        } != set(required) or len(scenarios) != len(required):
            raise ConformanceContractError(f"lane {lane_name} scenario inventory is incomplete")
        for scenario in scenarios:
            if (not isinstance(scenario, Mapping)
                    or scenario.get("outcome") != "passed"
                    or not isinstance(scenario.get("upstreamSideEffects"), int)
                    or scenario["upstreamSideEffects"] < 0):
                raise ConformanceContractError(f"lane {lane_name} has an unproven scenario")
            _validate_scenario_evidence(
                root, scenario, lane=lane_name, scenario_id=str(scenario["id"])
            )
        digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        if lane.get("sha256") != digest:
            raise ConformanceContractError(f"lane {lane_name} evidence digest mismatch")
        assert_secret_free(observation)
        resolved[lane_name] = {
            "status": "passed", "evidenceRef": ref, "sha256": digest
        }

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
    for key, required in (("features", REQUIRED_FEATURES),
                          ("accessibility", REQUIRED_ACCESSIBILITY),
                          ("securityControls", REQUIRED_SECURITY_CONTROLS)):
        values = compatibility.get(key)
        if not isinstance(values, Mapping) or set(values) != set(required) or any(
            value != "passed" for value in values.values()
        ):
            raise ConformanceContractError(f"complete native {key} compatibility is required")

    refs = source.get("retainedEvidence")
    if not isinstance(refs, Mapping) or set(refs) != set(REQUIRED_CHANNELS):
        raise ConformanceContractError("complete retained evidence channels are required")
    retained: dict[str, Any] = {}
    for channel, entry in refs.items():
        if not isinstance(entry, Mapping) or entry.get("kind") != channel:
            raise ConformanceContractError(f"retained evidence {channel} has the wrong kind")
        _validate_evidence_record(root, entry, f"retained evidence {channel}")
        digest = entry["sha256"]
        retained[channel] = {"evidenceRef": entry["evidenceRef"], "kind": channel,
                             "sha256": digest}
    telemetry = source.get("telemetry")
    if not isinstance(telemetry, Mapping) or set(telemetry) != set(REQUIRED_TELEMETRY):
        raise ConformanceContractError("complete bounded native Chat telemetry is required")
    if any(not isinstance(value, Mapping)
           or not isinstance(value.get("sampleCount"), int)
           or value["sampleCount"] <= 0
           or value.get("identityLabels") != []
           for value in telemetry.values()):
        raise ConformanceContractError("native Chat telemetry must be observed and identity-safe")
    for name, value in telemetry.items():
        _validate_evidence_record(root, value, f"telemetry {name}")
    rollout = source.get("rollout")
    required_rollout = ("canaryPolicy", "disableInteractiveChat", "historicalReads",
                        "noRuntimeFallback", "temporaryFlagRetirement")
    if not isinstance(rollout, Mapping) or any(
        not isinstance(rollout.get(key), Mapping)
        or rollout[key].get("outcome") != "passed"
        for key in required_rollout
    ):
        raise ConformanceContractError("rollout and rollback evidence is incomplete")
    for name, value in rollout.items():
        _validate_evidence_record(root, value, f"rollout {name}")

    report = {
        "schemaVersion": SCHEMA_VERSION, "issue": ISSUE, "status": "passed",
        "generatedAt": generated.isoformat(), "expiresAt": expires.isoformat(),
        "supersedes": source.get("supersedes"), "producer": source.get("producer"),
        "identity": dict(identity), "lanes": resolved,
        "compatibilityManifestRef": compatibility_ref,
        "retainedEvidence": retained,
        "telemetry": dict(telemetry), "rollout": dict(rollout),
    }
    if not isinstance(report["producer"], str) or not report["producer"]:
        raise ConformanceContractError("trusted producer identity is required")
    assert_secret_free(report)
    return report
