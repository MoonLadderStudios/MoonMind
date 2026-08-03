"""Versioned admission contract for autonomous remediation acceptance evidence."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

REMEDIATION_ACCEPTANCE_SCHEMA_VERSION = "moonmind.remediation-acceptance-matrix.v1"
REQUIRED_REMEDIATION_SCENARIOS = frozenset(
    {
        "diagnosis_only",
        "evidence_gated_resume",
        "checkpoint_branch",
        "approval_denied_and_gated",
        "stale_target_approval_or_lock",
        "interrupt_cancel_cleanup",
        "unsuccessful_repair_escalation",
        "cumulative_multi_attempt",
        "prevention_change",
        "missing_historical_evidence",
        "cancellation_and_worker_restart",
    }
)
REQUIRED_REMEDIATION_METRICS = frozenset(
    {
        "action_rate",
        "repeated_failure",
        "lock_conflict",
        "denial",
        "escalation",
        "unverified_mutation",
    }
)
FORBIDDEN_REMEDIATION_AUTHORITIES = frozenset(
    {
        "host_shell",
        "docker_daemon",
        "raw_sql",
        "storage_key",
        "secret_read",
        "redaction_bypass",
    }
)
REQUIRED_SCENARIO_AUDIT_FIELDS = frozenset(
    {
        "targetIdentityRef",
        "actionPolicyRef",
        "idempotencyRef",
        "actorRef",
        "approvalRef",
        "lockRef",
        "beforeStateRef",
        "afterStateRef",
        "verificationRef",
        "cleanupRef",
        "remainingWorkRef",
    }
)


def remediation_acceptance_digest(payload: Mapping[str, Any]) -> str:
    canonical = dict(payload)
    canonical.pop("contentDigest", None)
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def validate_remediation_acceptance_result(
    payload: Mapping[str, Any] | None,
) -> tuple[bool, str]:
    """Validate immutable production-shaped evidence before opening admission."""

    if not isinstance(payload, Mapping):
        return False, "acceptance_result_missing"
    if payload.get("schemaVersion") != REMEDIATION_ACCEPTANCE_SCHEMA_VERSION:
        return False, "acceptance_schema_unsupported"
    if payload.get("status") != "passed":
        return False, "acceptance_matrix_not_passing"
    if payload.get("contentDigest") != remediation_acceptance_digest(payload):
        return False, "acceptance_content_digest_mismatch"
    for field in (
        "actionPolicyVersion",
        "approvalPolicyVersion",
        "buildRevision",
        "operator",
        "completedAt",
    ):
        if not str(payload.get(field) or "").strip():
            return False, f"acceptance_{field}_missing"
    environment = payload.get("environment")
    if (
        not isinstance(environment, Mapping)
        or environment.get("productionShaped") is not True
    ):
        return False, "acceptance_environment_not_production_shaped"
    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, Sequence) or isinstance(scenarios, (str, bytes)):
        return False, "acceptance_scenarios_missing"
    by_id = {
        str(item.get("id") or ""): item
        for item in scenarios
        if isinstance(item, Mapping)
    }
    if not REQUIRED_REMEDIATION_SCENARIOS.issubset(by_id):
        return False, "acceptance_scenarios_incomplete"
    for scenario_id in REQUIRED_REMEDIATION_SCENARIOS:
        scenario = by_id[scenario_id]
        refs = scenario.get("artifactRefs")
        granted = scenario.get("grantedAuthorities")
        audit = scenario.get("audit")
        if (
            scenario.get("status") != "passed"
            or not isinstance(refs, Sequence)
            or not refs
        ):
            return False, f"acceptance_scenario_invalid:{scenario_id}"
        if not isinstance(
            audit, Mapping
        ) or not REQUIRED_SCENARIO_AUDIT_FIELDS.issubset(
            key for key, value in audit.items() if str(value or "").strip()
        ):
            return False, f"acceptance_audit_incomplete:{scenario_id}"
        if not isinstance(granted, Sequence) or isinstance(granted, (str, bytes)):
            return False, f"acceptance_authority_evidence_missing:{scenario_id}"
        if FORBIDDEN_REMEDIATION_AUTHORITIES.intersection(map(str, granted)):
            return False, f"acceptance_forbidden_authority:{scenario_id}"
    telemetry = payload.get("telemetry")
    if not isinstance(telemetry, Mapping):
        return False, "acceptance_telemetry_missing"
    metrics = {str(item) for item in telemetry.get("metrics", [])}
    alerts = {str(item) for item in telemetry.get("alerts", [])}
    if not REQUIRED_REMEDIATION_METRICS.issubset(metrics):
        return False, "acceptance_metrics_incomplete"
    if not REQUIRED_REMEDIATION_METRICS.issubset(alerts):
        return False, "acceptance_alerts_incomplete"
    return True, "acceptance_gate_passed"
