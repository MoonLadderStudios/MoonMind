"""Fail-closed operator remediation release evidence for GitHub issue #3626.

The catalog in this module is the authority for row ownership and requirements.
Evidence producers may report observations, but cannot invent rows or assert the
combined release result.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol
from urllib.parse import unquote, urlparse

MATRIX_VERSION = "moonmind.operator-remediation-matrix/v1"
EVIDENCE_SCHEMA_VERSION = "moonmind.operator-remediation-evidence/v1"
COMBINED_SCHEMA_VERSION = "moonmind.operator-remediation-release/v1"
MAX_EVIDENCE_AGE_SECONDS = 7 * 24 * 60 * 60
MAX_REFERENCED_EVIDENCE_BYTES = 10 * 1024 * 1024
OBSERVATION_SCHEMA_VERSION = "moonmind.operator-remediation-observation/v1"
EvidenceResolver = Callable[[str], bytes]


class ArtifactWriter(Protocol):
    """Narrow artifact boundary used by production scenario activities."""

    async def create(self, **kwargs: Any) -> tuple[Any, Any]: ...
    async def write_complete(self, **kwargs: Any) -> Any: ...
    async def read(self, **kwargs: Any) -> tuple[Any, bytes]: ...

REQUIRED_EVIDENCE_FIELDS = (
    "authoredRequest", "immutableInput", "identities", "contextEvidence",
    "authorityEvidence", "actionEvidence", "verificationEvidence", "repair", "prevention",
    "publication", "terminalHarvest", "cleanup", "images", "architecture",
    "timings", "thresholds", "secretScan",
)


@dataclass(frozen=True, slots=True)
class RemediationRow:
    row_id: str
    owner: str
    gate: str
    action_capability: str
    verification_capability: str
    authority: str
    ui_journey: str = "workflow-detail.remediate.normal-create"
    evidence_kind: str = "operator-remediation-observation"
    evidence_schema: str = EVIDENCE_SCHEMA_VERSION
    host_modes: tuple[str, ...] = ("on_demand",)
    architectures: tuple[str, ...] = ("amd64", "arm64")
    target_provenance: str = "moonmind_temporal"
    remediation_provenance: str = "codex_via_omnigent"
    max_duration_seconds: int = 1800


def _row(row_id: str, owner: str, gate: str, action: str = "none", verification: str = "artifact") -> RemediationRow:
    return RemediationRow(row_id, owner, gate, action, verification, "profile_policy_egress_bound")


# Stable IDs intentionally mirror every scenario named by MoonLadderStudios/MoonMind#3626.
REQUIRED_ROW_CATALOG = (
    _row("diagnosis.observe-only", "remediation-context", "manual_diagnosis"),
    _row("diagnosis.partial-history-degraded", "remediation-context", "manual_diagnosis"),
    _row("diagnosis.active-follow-reconnect", "remediation-context", "manual_diagnosis"),
    _row("diagnosis.evidence-unauthorized-denied", "remediation-context", "manual_diagnosis", verification="non_disclosure"),
    _row("recovery.resume-success", "workflow-recovery", "manual_mutation", "resume", "target_state"),
    _row("recovery.resume-unavailable-stale", "workflow-recovery", "manual_mutation", "resume", "denial"),
    _row("branch.corrected-instruction", "checkpoint-branch", "manual_mutation", "checkpoint_branch", "repository"),
    _row("branch.changed-authority", "checkpoint-branch", "manual_mutation", "checkpoint_branch", "immutable_input"),
    _row("branch.cumulative-attempts", "checkpoint-branch", "manual_mutation", "checkpoint_branch", "workspace"),
    _row("branch.no-progress-escalation", "checkpoint-branch", "manual_mutation", "checkpoint_branch", "bounded_escalation"),
    _row("action.low-medium-allowed", "remediation-actions", "manual_mutation", "typed_action", "target_state"),
    _row("approval.approved-verified", "remediation-approvals", "manual_mutation", "approval_gated_action", "target_state"),
    _row("approval.invalid-states-denied", "remediation-approvals", "manual_mutation", "approval_gated_action", "denial"),
    _row("approval.high-risk-reviewer", "remediation-approvals", "manual_mutation", "high_risk_action", "reviewer_authority"),
    _row("authority.stale-generations-denied", "remediation-actions", "manual_mutation", "typed_action", "freshness"),
    _row("conflict.mutation-lock-diagnosis-parallel", "remediation-actions", "manual_mutation", "typed_action", "lock"),
    _row("action.retry-idempotency", "remediation-actions", "manual_mutation", "typed_action", "idempotency"),
    _row("session.control-paths", "omnigent-runtime", "manual_mutation", "session_control", "session_state"),
    _row("host.profile-lease-reconciliation", "omnigent-runtime", "manual_mutation", "host_reconcile", "lease"),
    _row("host.helper-restart-reap", "omnigent-runtime", "manual_mutation", "helper_reconcile", "target_linkage"),
    _row("cleanup.targeted-janitor-release-last", "omnigent-runtime", "manual_mutation", "cleanup", "resource_absence"),
    _row("verification.resolved", "remediation-verification", "manual_mutation", "typed_action", "resolved"),
    _row("verification.no-change", "remediation-verification", "manual_mutation", "typed_action", "no_change"),
    _row("verification.failure-outcomes", "remediation-verification", "manual_mutation", "typed_action", "failure_taxonomy"),
    _row("prevention.repair-failed-pr", "remediation-publication", "manual_mutation", "publish_prevention", "repository"),
    _row("prevention.repair-success-analysis", "remediation-publication", "manual_mutation", "publish_prevention", "separate_outcomes"),
    _row("prevention.verification-failed", "remediation-publication", "manual_mutation", "publish_prevention", "target_not_repaired"),
    _row("reliability.phase-cancellation", "remediation-orchestration", "autonomous_rollout", "cancel", "all_phases"),
    _row("reliability.restart-replay", "remediation-orchestration", "autonomous_rollout", "replay", "all_phases"),
    _row("reliability.duplicate-suppression", "remediation-orchestration", "autonomous_rollout", "idempotency", "all_identities"),
    RemediationRow("host.static-lifecycle", "omnigent-runtime", "autonomous_rollout", "host_lifecycle", "resource_absence", "profile_policy_egress_bound", host_modes=("static",)),
    _row("host.on-demand-lifecycle", "omnigent-runtime", "autonomous_rollout", "host_lifecycle", "resource_absence"),
    _row("security.restricted-egress", "restricted-egress", "autonomous_rollout", "egress", "attestation"),
    _row("security.prohibited-authority", "security-boundary", "autonomous_rollout", "none", "denial"),
    _row("security.secret-safety", "security-boundary", "autonomous_rollout", "none", "secret_scan"),
)

ROW_CATALOG = {row.row_id: row for row in REQUIRED_ROW_CATALOG}


class RemediationGateError(ValueError):
    pass


def catalog_document() -> dict[str, Any]:
    """Return the versioned machine-readable catalog used by producers and UI."""
    return {"matrixVersion": MATRIX_VERSION, "rows": [
        {"rowId": r.row_id, "owner": r.owner, "targetRuntimeProvenance": r.target_provenance,
         "remediationRuntimeProvenance": r.remediation_provenance, "hostModes": list(r.host_modes),
         "architectures": list(r.architectures), "actionCapability": r.action_capability,
         "verificationCapability": r.verification_capability, "authority": r.authority,
         "uiJourney": r.ui_journey, "evidenceKind": r.evidence_kind,
         "evidenceSchema": r.evidence_schema, "thresholds": {"observedResult": "passed", "maxDurationSeconds": r.max_duration_seconds},
         "gates": r.gate} for r in REQUIRED_ROW_CATALOG]}


def _resolve_evidence_ref(ref: Any, *, resolve_ref: EvidenceResolver | None = None) -> bytes:
    if not isinstance(ref, Mapping) or set(ref) < {"ref", "sha256", "contentType"}:
        raise RemediationGateError("evidence refs require ref, sha256, and contentType")
    if ref["contentType"] != "application/json":
        raise RemediationGateError("typed remediation evidence must be application/json")
    ref_value = str(ref["ref"])
    parsed = urlparse(ref_value)
    try:
        if resolve_ref is not None:
            content = resolve_ref(ref_value)
        elif parsed.scheme == "file":
            content = Path(unquote(parsed.path)).read_bytes()
        else:
            raise RemediationGateError(
                "durable evidence requires a server-owned artifact resolver"
            )
    except (OSError, KeyError) as exc:
        raise RemediationGateError("evidence ref did not resolve") from exc
    if not isinstance(content, bytes):
        raise RemediationGateError("evidence resolver must return bytes")
    if not content or len(content) > MAX_REFERENCED_EVIDENCE_BYTES:
        raise RemediationGateError("evidence ref is empty or unbounded")
    if hashlib.sha256(content).hexdigest() != ref["sha256"]:
        raise RemediationGateError("evidence ref digest mismatch")
    return content


def _validate_observation_content(content: bytes, *, row_id: str, evidence_class: str) -> None:
    try:
        observation = json.loads(content)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RemediationGateError("evidence content is not valid JSON") from exc
    if not isinstance(observation, Mapping):
        raise RemediationGateError("evidence content must be an object")
    if (
        observation.get("schemaVersion") != OBSERVATION_SCHEMA_VERSION
        or observation.get("rowId") != row_id
        or observation.get("evidenceClass") != evidence_class
    ):
        raise RemediationGateError("evidence schema or lineage mismatch")
    if observation.get("observed") is not True:
        raise RemediationGateError("evidence does not contain an authoritative observation")


def validate_row_artifact(
    payload: Any,
    *,
    now: datetime | None = None,
    resolve_ref: EvidenceResolver | None = None,
) -> str:
    if not isinstance(payload, Mapping) or payload.get("schemaVersion") != EVIDENCE_SCHEMA_VERSION:
        raise RemediationGateError("unsupported remediation evidence schema")
    row_id = payload.get("rowId")
    row = ROW_CATALOG.get(row_id)
    if row is None:
        raise RemediationGateError("unknown remediation row")
    if payload.get("owner") != row.owner:
        raise RemediationGateError("row owner is not authoritative")
    if payload.get("evidenceKind") != row.evidence_kind:
        raise RemediationGateError("row evidence kind mismatch")
    if payload.get("observedResult") != "passed":
        raise RemediationGateError("row did not observe a passing result")
    if payload.get("targetRuntimeProvenance") != row.target_provenance or payload.get("remediationRuntimeProvenance") != row.remediation_provenance:
        raise RemediationGateError("runtime provenance mismatch")
    if payload.get("hostMode") not in row.host_modes or payload.get("architecture") not in row.architectures:
        raise RemediationGateError("unsupported host mode or architecture")
    if payload.get("actionCapability") != row.action_capability or payload.get("verificationCapability") != row.verification_capability:
        raise RemediationGateError("required capability mismatch")
    if payload.get("authority") != row.authority or payload.get("uiJourney") != row.ui_journey:
        raise RemediationGateError("authority or normal UI journey mismatch")
    missing = [field for field in REQUIRED_EVIDENCE_FIELDS if field not in payload]
    if missing:
        raise RemediationGateError(f"incomplete row evidence: {missing}")
    if payload.get("liveResourcesGone") is not True:
        raise RemediationGateError("live resources must be gone before validation")
    created = payload.get("generatedAt")
    try:
        created_at = datetime.fromisoformat(created).astimezone(timezone.utc)
    except (TypeError, ValueError) as exc:
        raise RemediationGateError("generatedAt must be an ISO timestamp") from exc
    age = ((now or datetime.now(timezone.utc)) - created_at).total_seconds()
    if age < 0 or age > MAX_EVIDENCE_AGE_SECONDS:
        raise RemediationGateError("row evidence is stale")
    timings = payload["timings"]
    if not isinstance(timings, Mapping) or not isinstance(timings.get("durationSeconds"), (int, float)) or timings["durationSeconds"] > row.max_duration_seconds:
        raise RemediationGateError("row duration exceeds threshold")
    thresholds = payload["thresholds"]
    if not isinstance(thresholds, Mapping) or not thresholds or any(value is not True for value in thresholds.values()):
        raise RemediationGateError("row thresholds did not all pass")
    secret_scan = payload["secretScan"]
    if not isinstance(secret_scan, Mapping) or secret_scan.get("status") != "passed" or secret_scan.get("prohibitedAuthorityFound") is not False:
        raise RemediationGateError("secret and prohibited-authority scan did not pass")
    for field in REQUIRED_EVIDENCE_FIELDS:
        if field in {"timings", "thresholds", "secretScan", "architecture"}:
            continue
        content = _resolve_evidence_ref(payload[field], resolve_ref=resolve_ref)
        _validate_observation_content(content, row_id=row_id, evidence_class=field)
    return row_id


def build_combined_matrix(
    *,
    artifact_paths: Iterable[Path | Mapping[str, Any]],
    release_inputs: Mapping[str, Any],
    now: datetime | None = None,
    resolve_ref: EvidenceResolver | None = None,
) -> dict[str, Any]:
    """Generate release status solely from complete, validated observed rows."""
    rows: dict[str, dict[str, str]] = {}
    for supplied in artifact_paths:
        if isinstance(supplied, Mapping):
            content = _resolve_evidence_ref(supplied, resolve_ref=resolve_ref)
            source_ref = str(supplied["ref"])
        else:
            path = supplied.resolve()
            content = path.read_bytes()
            source_ref = path.as_uri()
        payload = json.loads(content)
        row_id = validate_row_artifact(payload, now=now, resolve_ref=resolve_ref)
        if row_id in rows:
            raise RemediationGateError(f"duplicate observed row: {row_id}")
        rows[row_id] = {"ref": source_ref, "sha256": hashlib.sha256(content).hexdigest()}
    missing = sorted(set(ROW_CATALOG) - set(rows))
    if missing:
        raise RemediationGateError(f"incomplete remediation matrix; missingRows={missing}")
    if release_inputs.get("immutable") is not True or not release_inputs.get("version"):
        raise RemediationGateError("immutable versioned release inputs are required")
    return {"schemaVersion": COMBINED_SCHEMA_VERSION, "matrixVersion": MATRIX_VERSION,
            "issue": "MoonLadderStudios/MoonMind#3626", "status": "supported",
            "autonomousMutationAllowed": True, "releaseInputs": dict(release_inputs),
            "rows": rows, "generatedAt": (now or datetime.now(timezone.utc)).isoformat()}


def release_status(
    *,
    artifact_paths: Iterable[Path | Mapping[str, Any]],
    release_inputs: Mapping[str, Any],
    now: datetime | None = None,
    resolve_ref: EvidenceResolver | None = None,
) -> dict[str, Any]:
    """Return an operator projection; malformed/incomplete evidence fails closed."""
    try:
        return build_combined_matrix(
            artifact_paths=artifact_paths,
            release_inputs=release_inputs,
            now=now,
            resolve_ref=resolve_ref,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, RemediationGateError) as exc:
        return {"schemaVersion": COMBINED_SCHEMA_VERSION, "matrixVersion": MATRIX_VERSION,
                "issue": "MoonLadderStudios/MoonMind#3626", "status": "blocked",
                "autonomousMutationAllowed": False, "blockers": [str(exc)]}


def allows_autonomous_mutation(status: Any) -> bool:
    """Accept only a complete combined projection, never a caller pass boolean."""
    if not isinstance(status, Mapping):
        return False
    release_inputs = status.get("releaseInputs")
    rows = status.get("rows")
    return bool(
        status.get("schemaVersion") == COMBINED_SCHEMA_VERSION
        and status.get("matrixVersion") == MATRIX_VERSION
        and status.get("issue") == "MoonLadderStudios/MoonMind#3626"
        and status.get("status") == "supported"
        and status.get("autonomousMutationAllowed") is True
        and isinstance(release_inputs, Mapping)
        and release_inputs.get("immutable") is True
        and bool(release_inputs.get("version"))
        and isinstance(rows, Mapping)
        and set(rows) == set(ROW_CATALOG)
        and all(
            isinstance(observation, Mapping)
            and set(observation) >= {"ref", "sha256"}
            for observation in rows.values()
        )
    )


def _artifact_uri(artifact_id: str) -> str:
    return f"artifact://temporal/{artifact_id}"


def artifact_id_from_uri(ref: str) -> str:
    parsed = urlparse(ref)
    if parsed.scheme != "artifact" or parsed.netloc != "temporal":
        raise RemediationGateError("unsupported remediation artifact ref")
    artifact_id = parsed.path.lstrip("/")
    if not artifact_id:
        raise RemediationGateError("remediation artifact ref has no artifact id")
    return artifact_id


async def _persist_json(
    service: ArtifactWriter,
    *,
    principal: str,
    payload: Mapping[str, Any],
    kind: str,
    row_id: str | None = None,
) -> dict[str, str]:
    """Persist immutable typed evidence through the canonical artifact service."""
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    metadata = {"producer": "operator_remediation_gate", "kind": kind}
    if row_id:
        metadata["rowId"] = row_id
    artifact, _ = await service.create(
        principal=principal,
        content_type="application/json",
        size_bytes=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
        metadata_json=metadata,
    )
    await service.write_complete(
        artifact_id=artifact.artifact_id,
        principal=principal,
        payload=body,
        content_type="application/json",
    )
    return {"ref": _artifact_uri(artifact.artifact_id), "sha256": hashlib.sha256(body).hexdigest(),
            "contentType": "application/json"}


async def persist_observed_row(
    service: ArtifactWriter,
    *,
    principal: str,
    row_id: str,
    observations: Mapping[str, Mapping[str, Any]],
    result: Mapping[str, Any],
) -> dict[str, str]:
    """Production row producer: persist observations first, then the typed row.

    Scenario activities supply observed values, never ownership or pass authority.
    Catalog-owned identity and capability fields are injected here.
    """
    row = ROW_CATALOG.get(row_id)
    if row is None:
        raise RemediationGateError("unknown remediation row")
    required_refs = [field for field in REQUIRED_EVIDENCE_FIELDS
                     if field not in {"timings", "thresholds", "secretScan", "architecture"}]
    if set(observations) != set(required_refs):
        raise RemediationGateError("scenario producer must supply every evidence class")
    refs: dict[str, Any] = {}
    for evidence_class in required_refs:
        observation = {"schemaVersion": OBSERVATION_SCHEMA_VERSION, "rowId": row_id,
                       "evidenceClass": evidence_class, "observed": True,
                       "value": dict(observations[evidence_class])}
        refs[evidence_class] = await _persist_json(
            service, principal=principal, payload=observation,
            kind="operator-remediation-observation", row_id=row_id,
        )
    generated_at = result.get("generatedAt") or datetime.now(timezone.utc).isoformat()
    payload: dict[str, Any] = {
        "schemaVersion": EVIDENCE_SCHEMA_VERSION, "rowId": row_id, "owner": row.owner,
        "observedResult": result.get("observedResult"), "generatedAt": generated_at,
        "evidenceKind": row.evidence_kind, "targetRuntimeProvenance": row.target_provenance,
        "remediationRuntimeProvenance": row.remediation_provenance,
        "hostMode": result.get("hostMode"), "architecture": result.get("architecture"),
        "actionCapability": row.action_capability,
        "verificationCapability": row.verification_capability, "authority": row.authority,
        "uiJourney": row.ui_journey, "liveResourcesGone": result.get("liveResourcesGone"),
        "timings": result.get("timings"), "thresholds": result.get("thresholds"),
        "secretScan": result.get("secretScan"), **refs,
    }
    # Validate the complete in-memory shape before publishing the authoritative row.
    bodies: dict[str, bytes] = {}
    for evidence_class, ref in refs.items():
        bodies[ref["ref"]] = json.dumps(
            {"schemaVersion": OBSERVATION_SCHEMA_VERSION, "rowId": row_id,
             "evidenceClass": evidence_class, "observed": True,
             "value": dict(observations[evidence_class])},
            sort_keys=True, separators=(",", ":"),
        ).encode()
    validate_row_artifact(payload, resolve_ref=bodies.__getitem__)
    return await _persist_json(service, principal=principal, payload=payload,
                               kind="operator-remediation-row", row_id=row_id)


async def publish_release_projection(
    service: ArtifactWriter,
    *,
    principal: str,
    row_refs: Iterable[Mapping[str, Any]],
    release_inputs: Mapping[str, Any],
    now: datetime | None = None,
) -> dict[str, str]:
    """Resolve durable rows after cleanup and publish the combined projection."""
    cache: dict[str, bytes] = {}

    async def load(ref: str) -> bytes:
        if ref not in cache:
            _, cache[ref] = await service.read(
                artifact_id=artifact_id_from_uri(ref), principal=principal,
                allow_restricted_raw=True,
            )
        return cache[ref]

    supplied = [dict(ref) for ref in row_refs]
    for ref in supplied:
        await load(str(ref["ref"]))
    # Row validation resolves nested refs synchronously, so preload those refs.
    for ref in supplied:
        row_payload = json.loads(cache[str(ref["ref"])])
        for field in REQUIRED_EVIDENCE_FIELDS:
            value = row_payload.get(field)
            if isinstance(value, Mapping) and "ref" in value:
                await load(str(value["ref"]))
    projection = build_combined_matrix(
        artifact_paths=supplied, release_inputs=release_inputs, now=now,
        resolve_ref=cache.__getitem__,
    )
    return await _persist_json(service, principal=principal, payload=projection,
                               kind="operator-remediation-release")
