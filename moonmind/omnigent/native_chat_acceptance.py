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
DURABLE_REF_SCHEMA_VERSION = (
    "moonmind.omnigent.native-chat-acceptance-durable-ref/v1"
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

# Exact controlling inventory.  A lane producer may split these cases across
# multiple evidence objects, but it cannot replace them with a generic
# self-asserted case.  The names intentionally describe product-observable
# outcomes, not implementation helpers, so the same contract is usable by the
# deterministic browser lane and the protected stock-image lane.
REQUIRED_CASES: dict[str, tuple[str, ...]] = {
    SCENARIO_DETERMINISTIC_JOURNEY: (
        "workflow-detail-chat-selection", "opaque-binding-resolution",
        "embedded-native-app-load", "native-composer-message", "queue-and-steer",
        "tools-and-reasoning", "approval", "resources", "terminal",
        "subagents-and-tasks", "reconnect", "terminal-transition",
        "post-cleanup-reload", "diagnostics-and-evidence", "linked-continuation",
    ),
    SCENARIO_BINDING_AUTHORIZATION: (
        "owner", "authorized-shared-viewer", "read-only-viewer",
        "approval-only-caller", "unauthorized-caller", "unknown-binding",
        "expired-binding", "revoked-binding", "cross-workflow-binding",
        "path-session-substitution", "query-session-substitution",
        "body-session-substitution", "header-session-substitution",
        "sse-cursor-substitution", "websocket-frame-substitution",
        "endpoint-substitution", "host-substitution", "runner-substitution",
        "environment-substitution", "workspace-substitution",
        "terminal-substitution", "profile-substitution", "model-substitution",
        "effort-substitution", "goal-substitution", "credential-substitution",
        "http-live-authorization-change", "sse-live-authorization-change",
        "websocket-live-authorization-change", "deleted-workflow",
        "archived-workflow", "cleaned-session", "non-enumerating-response",
    ),
    SCENARIO_CREDENTIAL_ISOLATION: (
        "no-direct-upstream-browser-request", "no-upstream-secret-in-browser-state",
        "no-moonmind-authority-upstream", "allowlisted-forward-headers-only",
        "redirect-contained", "error-body-contained", "download-contained",
        "websocket-contained", "service-worker-contained",
        "full-page-scoped-single-sign-on",
    ),
    SCENARIO_CAPABILITY_POLICY: (
        "pinned-model", "pinned-effort", "approval-authority-and-state",
        "transcript-does-not-grant-mutation", "active-versus-terminal",
        "hidden-control-direct-api-denied", "stale-agent-profile",
        "stale-provider-generation", "stale-policy-digest",
        "stale-launch-snapshot", "stale-session-epoch", "stale-turn",
        "stale-elicitation", "duplicate-mutation", "delivery-unknown-reconciliation",
        "unsupported-control-unavailable",
    ),
    SCENARIO_OUTBOUND_SCAN: (
        "clean-message", "secret-like-message", "queued-message", "steered-message",
        "slash-command-arguments", "approval-response", "reply-and-quote",
        "text-attachment", "upload-metadata", "changed-idempotency-payload",
        "unknown-payload", "malformed-payload", "compressed-payload", "binary-payload",
        "oversized-payload", "uninspectable-payload", "scanner-unavailable",
        "scanner-error", "scanner-timeout", "blocked-zero-upstream-side-effects",
        "redacted-diagnostics-only",
    ),
    SCENARIO_NATIVE_UI_TRANSPORTS: (
        "spa-assets", "deep-link", "refresh", "embedded-mode", "full-page-mode",
        "transcript", "composer", "queue", "tool-and-reasoning-view", "approvals",
        "files-and-diffs", "upload-and-download", "terminals", "agents", "tasks",
        "browser-pane-capability", "http", "sse", "websocket", "pty", "multipart",
        "binary", "reconnect", "liveness", "mobile-responsive", "keyboard-shortcuts",
        "focus-transitions", "screen-reader-semantics", "reduced-motion",
        "large-session", "csp-frame-cors-csrf-origin-cookie-cache-service-worker",
        "route-version-drift",
    ),
    SCENARIO_DIAGNOSTIC_FALLBACK: (
        "native-ui-unavailable", "unsupported-runtime", "failed-before-stream",
        "retention-gap", "schema-incompatibility", "direct-runtime-provenance",
        "host-removed", "upstream-server-unavailable", "no-custom-composer",
        "terminal-ui-read-only", "terminal-server-read-only",
        "captured-evidence-links",
    ),
    SCENARIO_TERMINAL_CONTINUATION: (
        "terminal-read-only", "captured-evidence-survives-cleanup",
        "linked-continuation-new-work", "source-session-not-mutated",
    ),
    SCENARIO_PROTECTED_STOCK_IMAGE: (
        "normal-ui-workflow-selection", "native-scoped-chat-open",
        "real-bounded-message", "real-transcript-tool-resource",
        "real-approval-or-control", "terminal-and-mutation-harvest",
        "canonical-cleanup-and-release", "durable-replay-after-removal",
        "linked-continuation-without-session-reuse", "no-custom-host",
        "no-manual-provider-session", "no-direct-upstream-login", "no-silent-fallback",
    ),
    SCENARIO_EVIDENCE_DURABILITY: (
        "all-refs-resolve-after-cleanup", "all-retained-evidence-secret-scanned",
        "historical-diagnostics-preserved", "leases-released",
    ),
    SCENARIO_TELEMETRY_ROLLOUT: (
        "binding-readiness-signals", "transport-and-upstream-signals",
        "authorization-capability-scan-signals", "mutation-and-reconciliation-signals",
        "fallback-replay-continuation-signals", "identity-free-bounded-labels",
        "default-pre-proof-read-only", "validated-canary-admission",
        "rollback-preserves-diagnostics", "temporary-flag-retirement",
    ),
}
REQUIRED_CLEANUP_CASES: tuple[str, ...] = (
    "live-resources-removed", "historical-evidence-preserved",
    "leases-released", "provider-profile-released-last",
    "post-cleanup-ref-resolution",
)

TRUSTED_LANE_PRODUCERS: dict[str, str] = {
    "deterministic": "github-actions:omnigent-native-chat-deterministic",
    "protected_live": "github-actions:omnigent-native-chat-protected-live",
}
TRUSTED_REPORT_PRODUCER = "github-actions:omnigent-native-chat-acceptance"

_DURABLE_REF_KINDS = frozenset({
    "artifact", "event", "diagnostic", "mutation_audit", "screenshot",
    "cleanup", "lease_release", "secret_scan", "profile", "launch_policy",
    "effective_launch", "provider_profile", "network_manifest", "terminal",
    "continuation", "browser_trace", "test_result",
})

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


def _durable_ref(
    ref: Any,
    *,
    label: str,
    evidence_objects: Mapping[str, Any],
    identities: Mapping[str, Any],
    lane: str,
    now: datetime,
    expected_kind: str | None = None,
) -> Mapping[str, Any]:
    if not isinstance(ref, str) or not ref.startswith("artifact://"):
        raise ConformanceContractError(f"{label} must be an artifact ref")
    item = evidence_objects.get(ref)
    if not isinstance(item, Mapping):
        raise ConformanceContractError(f"{label} is unresolved: {ref}")
    if item.get("schemaVersion") != DURABLE_REF_SCHEMA_VERSION:
        raise ConformanceContractError(f"{label} has an unsupported durable-ref schema")
    kind = item.get("kind")
    if kind not in _DURABLE_REF_KINDS or (expected_kind and kind != expected_kind):
        raise ConformanceContractError(f"{label} has the wrong evidence kind")
    if item.get("status") != "passed" or item.get("retainedAfterCleanup") is not True:
        raise ConformanceContractError(f"{label} is not durable after cleanup")
    if item.get("identities") != identities or item.get("lane") != lane:
        raise ConformanceContractError(f"{label} is for different lane identities")
    if item.get("producer") != TRUSTED_LANE_PRODUCERS[lane]:
        raise ConformanceContractError(f"{label} lacks trusted lane provenance")
    digest = item.get("sha256")
    if not isinstance(digest, str) or not _BARE_DIGEST.fullmatch(digest):
        raise ConformanceContractError(f"{label} lacks a content digest")
    if (
        not isinstance(item.get("contentType"), str)
        or not item["contentType"].strip()
        or not isinstance(item.get("sizeBytes"), int)
        or item["sizeBytes"] < 0
    ):
        raise ConformanceContractError(f"{label} lacks bounded content metadata")
    _active_evidence(item, label=label, now=now)
    assert_secret_free(item)
    return item


def _passed_case_evidence(
    evidence: Any,
    *,
    label: str,
    claim: str,
    case_name: str,
    identities: Mapping[str, Any],
    evidence_objects: Mapping[str, Any],
    lane: str,
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
    outcome = evidence.get("outcome")
    boundary_tests = evidence.get("boundaryTests")
    if (
        not isinstance(outcome, Mapping)
        or outcome.get("result") != "passed"
        or outcome.get("authorizationDecision")
        not in {"allowed", "denied", "not_applicable"}
        or not isinstance(outcome.get("upstreamSideEffectCount"), int)
        or not isinstance(outcome.get("expectedUpstreamSideEffectCount"), int)
        or outcome["upstreamSideEffectCount"]
        != outcome["expectedUpstreamSideEffectCount"]
        or outcome.get("durableAfterCleanup") is not True
        or not isinstance(boundary_tests, list)
        or not boundary_tests
        or not all(
            isinstance(test, str) and ":" in test and test.strip()
            for test in boundary_tests
        )
    ):
        raise ConformanceContractError(f"{label} evidence lacks a controlling outcome")
    channel_refs = evidence.get("evidenceRefs")
    if (
        not isinstance(channel_refs, list)
        or not channel_refs
        or not all(isinstance(ref, str) and ref.strip() for ref in channel_refs)
    ):
        raise ConformanceContractError(f"{label} evidence lacks channel refs")
    if not isinstance(evidence.get("producer"), str) or not evidence["producer"].strip():
        raise ConformanceContractError(f"{label} evidence lacks provenance")
    if evidence.get("producer") != TRUSTED_LANE_PRODUCERS[lane] or evidence.get("lane") != lane:
        raise ConformanceContractError(f"{label} evidence lacks trusted lane provenance")
    for channel_ref in channel_refs:
        _durable_ref(
            channel_ref,
            label=f"{label} channel ref",
            evidence_objects=evidence_objects,
            identities=identities,
            lane=lane,
            now=now,
        )
    _durable_ref(
        evidence.get("auditRef"), label=f"{label} audit ref",
        evidence_objects=evidence_objects, identities=identities, lane=lane,
        now=now, expected_kind="mutation_audit",
    )
    _durable_ref(
        evidence.get("cleanupRef"), label=f"{label} cleanup ref",
        evidence_objects=evidence_objects, identities=identities, lane=lane,
        now=now, expected_kind="cleanup",
    )
    _durable_ref(
        evidence.get("secretScanRef"), label=f"{label} secret-scan ref",
        evidence_objects=evidence_objects, identities=identities, lane=lane,
        now=now, expected_kind="secret_scan",
    )
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
        if evidence.get("lane") != expected_lane:
            raise ConformanceContractError(f"{label} evidence is in the wrong lane")
        if evidence.get("producer") != TRUSTED_LANE_PRODUCERS[expected_lane]:
            raise ConformanceContractError(f"{label} evidence lacks trusted lane provenance")
        for channel_ref in evidence.get("evidenceRefs") or ():
            _durable_ref(
                channel_ref,
                label=f"{label} channel ref",
                evidence_objects=evidence_objects,
                identities=identities,
                lane=expected_lane,
                now=now,
            )
        cases = evidence.get("cases")
        required_cases = set(
            REQUIRED_CLEANUP_CASES
            if claim == "cleanup"
            else REQUIRED_CASES.get(claim.removeprefix("scenario:"), ())
        )
        if (
            not isinstance(cases, Mapping)
            or set(cases) != required_cases
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
            raise ConformanceContractError(f"{label} evidence case inventory is incomplete")
        for case_name, case in cases.items():
            for case_ref in case["evidenceRefs"]:
                _passed_case_evidence(
                    evidence_objects.get(case_ref),
                    label=f"{label} case {case_name}",
                    claim=claim,
                    case_name=case_name,
                    identities=identities,
                    evidence_objects=evidence_objects,
                    lane=expected_lane,
                    now=now,
                )
        _durable_ref(
            evidence.get("cleanupRef"), label=f"{label} cleanup ref",
            evidence_objects=evidence_objects, identities=identities,
            lane=expected_lane, now=now, expected_kind="cleanup",
        )
        _durable_ref(
            evidence.get("secretScanRef"), label=f"{label} secret-scan ref",
            evidence_objects=evidence_objects, identities=identities,
            lane=expected_lane, now=now, expected_kind="secret_scan",
        )
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
        not isinstance(profile_refs.get(key), str)
        or not profile_refs[key].startswith("artifact://")
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
            refs.update(cleanup_source.get("preservedEvidenceRefs") or [])
            refs.update(cleanup_source.get("releasedLeaseRefs") or [])
        profile_source = source.get("profilePolicyRefs")
        if isinstance(profile_source, Mapping):
            refs.update(str(value) for value in profile_source.values())
        scan_source = source.get("secretScan")
        if isinstance(scan_source, Mapping):
            refs.update(scan_source.get("evidenceRefs") or [])
            refs.update(scan_source.get("scannedRefs") or [])
        evidence_objects = {}
        pending = list(refs)
        while pending:
            ref = pending.pop()
            if ref in evidence_objects:
                continue
            resolved = dict(evidence_resolver(ref))
            evidence_objects[ref] = resolved
            pending.extend(resolved.get("evidenceRefs") or [])
            for key in ("auditRef", "cleanupRef", "secretScanRef"):
                nested = resolved.get(key)
                if isinstance(nested, str):
                    pending.append(nested)
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
        expected_lane=LANE_PROTECTED_LIVE,
    )
    if (
        cleanup.get("historicalEvidencePreserved") is not True
        or cleanup.get("leasesReleased") is not True
    ):
        raise ConformanceContractError("cleanup must preserve history and release leases")

    profile_kinds = {
        "profileRef": "profile",
        "launchPolicyRef": "launch_policy",
        "effectiveLaunchSnapshotRef": "effective_launch",
        "providerProfileRef": "provider_profile",
    }
    for key, kind in profile_kinds.items():
        _durable_ref(
            profile_refs[key], label=f"profile policy ref {key}",
            evidence_objects=evidence_objects, identities=identities,
            lane=LANE_PROTECTED_LIVE, now=now, expected_kind=kind,
        )

    secret_scan = source.get("secretScan")
    if (
        not isinstance(secret_scan, Mapping)
        or secret_scan.get("status") != "passed"
        or not isinstance(secret_scan.get("evidenceRefs"), list)
        or not secret_scan["evidenceRefs"]
        or not isinstance(secret_scan.get("scannedRefs"), list)
    ):
        raise ConformanceContractError(
            "the retained-evidence secret scan must pass"
        )
    scan_refs = set(secret_scan["evidenceRefs"])
    for ref in scan_refs:
        item = evidence_objects.get(ref)
        lane = item.get("lane") if isinstance(item, Mapping) else None
        if lane not in TRUSTED_LANE_PRODUCERS:
            raise ConformanceContractError("retained-evidence scan lacks trusted lane")
        resolved_scan = _durable_ref(
            ref, label="retained-evidence secret scan ref",
            evidence_objects=evidence_objects, identities=identities,
            lane=lane, now=now, expected_kind="secret_scan",
        )
        lane_retained_refs = {
            retained_ref
            for retained_ref, retained_item in evidence_objects.items()
            if isinstance(retained_item, Mapping)
            and retained_item.get("lane") == lane
            and retained_item.get("kind") != "secret_scan"
        }
        if (
            resolved_scan.get("scanCompletedAfterCleanup") is not True
            or resolved_scan.get("secretFindings") != 0
            or set(resolved_scan.get("scannedRefs") or ()) != lane_retained_refs
        ):
            raise ConformanceContractError(
                "retained-evidence scan does not attest its complete lane"
            )
    retained_refs = {
        ref for ref, item in evidence_objects.items()
        if not isinstance(item, Mapping) or item.get("kind") != "secret_scan"
    }
    if set(secret_scan["scannedRefs"]) != retained_refs:
        raise ConformanceContractError(
            "retained-evidence secret scan does not cover every evidence ref"
        )
    preserved_refs = cleanup.get("preservedEvidenceRefs")
    released_refs = cleanup.get("releasedLeaseRefs")
    if not isinstance(preserved_refs, list) or set(preserved_refs) != retained_refs:
        raise ConformanceContractError("cleanup does not preserve every evidence ref")
    if not isinstance(released_refs, list) or not released_refs:
        raise ConformanceContractError("cleanup lacks durable lease-release refs")
    for ref in released_refs:
        _durable_ref(
            ref, label="cleanup lease-release ref", evidence_objects=evidence_objects,
            identities=identities, lane=LANE_PROTECTED_LIVE, now=now,
            expected_kind="lease_release",
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
        # Keep the complete, already validated object graph in the published
        # artifact.  The digest-bound runtime loader replays this validator at
        # the interactive authority boundary; a shallow hand-authored report
        # with copied status strings must never admit traffic.
        "evidenceObjects": {
            str(ref): dict(item) if isinstance(item, Mapping) else item
            for ref, item in evidence_objects.items()
        },
    }
    if report["producer"] != TRUSTED_REPORT_PRODUCER:
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
    "DURABLE_REF_SCHEMA_VERSION",
    "EVIDENCE_SCHEMA_VERSION",
    "ISSUE",
    "LANE_DETERMINISTIC",
    "LANE_PROTECTED_LIVE",
    "REQUIRED_CONTRACT_VERSIONS",
    "REQUIRED_CASES",
    "REQUIRED_CLEANUP_CASES",
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
    "TRUSTED_LANE_PRODUCERS",
    "TRUSTED_REPORT_PRODUCER",
    "build_native_chat_acceptance_report",
]
