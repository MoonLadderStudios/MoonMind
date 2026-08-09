"""Evidence-gated Codex-through-Omnigent rollout contract.

Source issue: MoonLadderStudios/MoonMind#3518.

The contract intentionally decides promotion only. Runtime selection remains an
explicit authored value; a denied promotion never causes a direct-Codex
fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import IntEnum
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import unquote, urlparse

from moonmind.omnigent.conformance import (
    PROFILE_SHA256,
    PROFILE_VERSION,
    REQUIRED_EVIDENCE_CHANNELS,
    ConformanceContractError,
    require_pinned_images,
)
from moonmind.omnigent.native_chat_acceptance import (
    build_native_chat_acceptance_report,
)
from moonmind.workflows.executions.runtime_defaults import normalize_runtime_id

CUTOVER_POLICY_VERSION = "moonmind.codex-omnigent-cutover/v1"
# Phase 6 is a build property, not a live-artifact assertion. The retirement
# change that actually removes direct launch/UI/configuration ownership must set
# this to its versioned removal manifest only after absence guards pass.
DIRECT_LAUNCH_REMOVAL_VERSION: str | None = None
MAX_EVIDENCE_AGE_SECONDS = 7 * 24 * 60 * 60
REQUIRED_TELEMETRY_GROUPS = (
    "launchReadiness",
    "stageLatency",
    "reconnectReplay",
    "controls",
    "failures",
    "artifactCapture",
    "checkpointResumeBranch",
    "remediationRag",
    "cleanupJanitor",
    "runtimeSelection",
    "secretRedaction",
    "policyReadinessDenials",
)
REQUIRED_EVIDENCE_KINDS = (
    "submissionMatrix",
    "historicalReads",
    "temporalReplay",
    "capacityOwnership",
    "secretScan",
    "releaseMetadata",
)

# Machine-readable support-matrix contract (MoonLadderStudios/MoonMind#3564).
# Each protected row is owned by exactly one evidence kind and observed on a
# bounded set of host modes and runtime provenances. Support is proven only by
# an artifact that carries the observed lifecycle result for its owned rows, not
# by a caller-supplied row list or a bare pass boolean.
MATRIX_VERSION = "codex-omnigent-support-matrix/v1"
ARTIFACT_SCHEMA_VERSION = "moonmind.codex-omnigent-cutover-artifact/v2"
ALLOWED_HOST_MODES = ("static", "on_demand")


@dataclass(frozen=True, slots=True)
class MatrixRow:
    """One protected support row bound to its owning evidence kind."""

    row_id: str
    kind: str
    host_modes: tuple[str, ...] = ALLOWED_HOST_MODES
    provenance: tuple[str, ...] = ("omnigent",)


REQUIRED_ROW_CATALOG: tuple[MatrixRow, ...] = (
    MatrixRow("oauth-profile.static", "capacityOwnership", ("static",)),
    MatrixRow("oauth-profile.on-demand", "capacityOwnership", ("on_demand",)),
    MatrixRow("bridge.stock-proxy", "submissionMatrix"),
    MatrixRow("host.static", "submissionMatrix", ("static",)),
    MatrixRow("host.on-demand", "submissionMatrix", ("on_demand",)),
    MatrixRow("submission.create", "submissionMatrix"),
    MatrixRow("submission.edit", "submissionMatrix"),
    MatrixRow("submission.rerun", "submissionMatrix"),
    MatrixRow("submission.schedule", "submissionMatrix"),
    MatrixRow("submission.preset", "submissionMatrix"),
    MatrixRow("repository.read", "submissionMatrix"),
    MatrixRow("repository.mutate-publish", "submissionMatrix"),
    MatrixRow("workflow-detail.live-replay-resources-controls", "submissionMatrix"),
    MatrixRow("lifecycle.cancel-timeout-failure-cleanup-janitor", "submissionMatrix"),
    MatrixRow("checkpoint.capture-reattach-restore-branch", "temporalReplay"),
    MatrixRow("remediation.operator-autonomous-gate", "secretScan"),
    MatrixRow("rag.initial-follow-up", "releaseMetadata"),
    MatrixRow("policy-agent-profile.persistence-ui", "releaseMetadata"),
    MatrixRow("egress.enforced", "secretScan"),
    MatrixRow("release.images-architecture-upstream-license", "releaseMetadata"),
    MatrixRow(
        "direct-runtime.historical-read-fallback",
        "historicalReads",
        ALLOWED_HOST_MODES,
        ("codex_direct_compat",),
    ),
)

# Stable row IDs are the machine-readable form of the canonical v1 matrix.
REQUIRED_MATRIX_ROWS = tuple(row.row_id for row in REQUIRED_ROW_CATALOG)
ROW_CATALOG: dict[str, MatrixRow] = {row.row_id: row for row in REQUIRED_ROW_CATALOG}


class CutoverMatrixError(ValueError):
    """Raised when a protected artifact fails observed-evidence row binding."""


def _validate_row_secret_scan(secret_scan: Any, *, row_id: str) -> None:
    """Bind a row to validated per-channel secret-scan evidence.

    A self-asserted scalar (for example ``"clean"``) is never sufficient. Every
    required evidence channel must record a passing scan bound to a resolvable
    ``evidenceRef``, mirroring the conformance report contract in
    ``moonmind.omnigent.conformance.build_report``. A missing channel, a
    non-passing status, or an absent ref fails closed.
    """

    if not isinstance(secret_scan, Mapping):
        raise CutoverMatrixError(
            f"row {row_id!r} secret scan must carry per-channel evidence"
        )
    missing = set(REQUIRED_EVIDENCE_CHANNELS) - set(secret_scan)
    if missing:
        raise CutoverMatrixError(
            f"row {row_id!r} secret scan is missing channels: {sorted(missing)}"
        )
    for channel in REQUIRED_EVIDENCE_CHANNELS:
        result = secret_scan.get(channel)
        evidence_ref = (
            result.get("evidenceRef") if isinstance(result, Mapping) else None
        )
        if (
            not isinstance(result, Mapping)
            or result.get("status") != "passed"
            or not isinstance(evidence_ref, str)
            or not evidence_ref.strip()
        ):
            raise CutoverMatrixError(
                f"row {row_id!r} secret scan channel {channel!r} lacks passing "
                "evidence"
            )


def validate_matrix_artifact(
    payload: Any,
    *,
    expected_kind: str | None,
    images: Any,
    architectures: Any,
    profile_version: Any,
    profile_sha256: Any,
    policy_version: Any,
    agent_profile_version: Any,
) -> tuple[str, frozenset[str]]:
    """Bind one protected artifact to the rows it independently proves.

    Returns the owning evidence kind and the frozen set of rows it proves.
    Raises :class:`CutoverMatrixError` when identity, ownership, host mode,
    runtime provenance, immutable images, architecture, conformance profile,
    launch-policy or agent-profile version, observed lifecycle result, or the
    raw-channel secret scan cannot be independently confirmed. A caller-supplied
    row name or a bare ``passed`` flag is never sufficient.
    """

    if not isinstance(payload, Mapping):
        raise CutoverMatrixError("artifact is not an object")
    if payload.get("schemaVersion") != ARTIFACT_SCHEMA_VERSION:
        raise CutoverMatrixError("unsupported artifact schema")
    kind = payload.get("kind")
    if not isinstance(kind, str) or kind not in REQUIRED_EVIDENCE_KINDS:
        raise CutoverMatrixError("unsupported evidence kind")
    if expected_kind is not None and kind != expected_kind:
        raise CutoverMatrixError("evidence kind does not match manifest")
    producer = payload.get("producerVersion")
    if not isinstance(producer, str) or not producer.strip():
        raise CutoverMatrixError("producer version is required")
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows:
        raise CutoverMatrixError("artifact declares no observed rows")

    if not isinstance(images, Mapping):
        raise CutoverMatrixError("release images are required")
    try:
        require_pinned_images(images)
    except ConformanceContractError as exc:
        raise CutoverMatrixError("release images must be immutable") from exc
    architecture_set = (
        {item for item in architectures if isinstance(item, str) and item.strip()}
        if isinstance(architectures, list)
        else set()
    )
    if not architecture_set:
        raise CutoverMatrixError("release architectures are required")
    if not isinstance(profile_version, str) or not isinstance(profile_sha256, str):
        raise CutoverMatrixError("conformance profile evidence is required")
    if not isinstance(policy_version, str) or not policy_version.strip():
        raise CutoverMatrixError("launch policy version is required")
    if not isinstance(agent_profile_version, str) or not agent_profile_version.strip():
        raise CutoverMatrixError("agent profile version is required")

    observed: dict[str, set[str]] = {}
    for entry in rows:
        if not isinstance(entry, Mapping):
            raise CutoverMatrixError("observed row is not an object")
        row_id = entry.get("row")
        row = ROW_CATALOG.get(row_id) if isinstance(row_id, str) else None
        if row is None:
            raise CutoverMatrixError(f"unknown protected row: {row_id!r}")
        if row.kind != kind:
            raise CutoverMatrixError(
                f"row {row_id!r} is not owned by evidence kind {kind!r}"
            )
        if entry.get("observedResult") != "passed":
            raise CutoverMatrixError(f"row {row_id!r} did not observe a passing result")
        if entry.get("hostMode") not in row.host_modes:
            raise CutoverMatrixError(f"row {row_id!r} has an unsupported host mode")
        if entry.get("runtimeProvenance") not in row.provenance:
            raise CutoverMatrixError(
                f"row {row_id!r} has unexpected runtime provenance"
            )
        _validate_row_secret_scan(entry.get("secretScan"), row_id=row_id)
        architecture = entry.get("architecture")
        if architecture not in architecture_set:
            raise CutoverMatrixError(
                f"row {row_id!r} was not observed on a released architecture"
            )
        if architecture in observed.get(row_id, set()):
            raise CutoverMatrixError(
                f"row {row_id!r} observed more than once on architecture "
                f"{architecture!r}"
            )
        row_images = entry.get("images")
        if not isinstance(row_images, Mapping) or dict(row_images) != dict(images):
            raise CutoverMatrixError(
                f"row {row_id!r} images are not the released immutable digests"
            )
        if (
            entry.get("profileVersion") != profile_version
            or entry.get("profileSha256") != profile_sha256
        ):
            raise CutoverMatrixError(
                f"row {row_id!r} is not the canonical conformance profile"
            )
        if entry.get("launchPolicyVersion") != policy_version:
            raise CutoverMatrixError(f"row {row_id!r} launch policy version mismatch")
        if entry.get("agentProfileVersion") != agent_profile_version:
            raise CutoverMatrixError(f"row {row_id!r} agent profile version mismatch")
        observed.setdefault(row_id, set()).add(architecture)

    # Every released architecture requires its own live evidence for each owned
    # row. Membership in the release list is not enough: a row observed on only
    # one of several released architectures leaves the others unproven, so the
    # row is not counted as supported until the full per-row/architecture cross
    # product is present.
    for row_id, seen_architectures in observed.items():
        missing_architectures = architecture_set - seen_architectures
        if missing_architectures:
            raise CutoverMatrixError(
                f"row {row_id!r} was not observed on every released architecture: "
                f"{sorted(missing_architectures)}"
            )
    return kind, frozenset(observed)


class CutoverPhase(IntEnum):
    OPT_IN = 1
    CREATE_DEFAULT = 2
    SCHEDULE_DEFAULT = 3
    BROAD_DEFAULT = 4
    DIRECT_LAUNCH_DISABLED = 5
    DIRECT_LAUNCH_REMOVED = 6


def configured_phase(*, env: Mapping[str, Any] | None = None) -> CutoverPhase:
    """Resolve the versioned deployment phase; invalid values fail closed."""

    values = os.environ if env is None else env
    raw = str(values.get("MOONMIND_CODEX_OMNIGENT_CUTOVER_PHASE", "opt_in"))
    normalized = raw.strip().upper().replace("-", "_")
    try:
        return CutoverPhase[normalized]
    except KeyError as exc:
        raise ValueError(f"unsupported Codex Omnigent cutover phase: {raw!r}") from exc


def deployed_phase(*, env: Mapping[str, Any] | None = None) -> CutoverPhase:
    """Resolve the durable phase currently deployed by the operator."""

    values = os.environ if env is None else env
    raw = str(values.get("MOONMIND_CODEX_OMNIGENT_DEPLOYED_PHASE", "opt_in"))
    normalized = raw.strip().upper().replace("-", "_")
    try:
        return CutoverPhase[normalized]
    except KeyError as exc:
        raise ValueError(
            f"unsupported deployed Codex Omnigent cutover phase: {raw!r}"
        ) from exc


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    allowed: bool
    current_phase: CutoverPhase
    requested_phase: CutoverPhase
    blockers: tuple[str, ...]
    policy_version: str = CUTOVER_POLICY_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "policyVersion": self.policy_version,
            "allowed": self.allowed,
            "currentPhase": self.current_phase.name.lower(),
            "requestedPhase": self.requested_phase.name.lower(),
            "blockers": list(self.blockers),
        }


@dataclass(frozen=True, slots=True)
class EffectivePhase:
    """Authoritative, fail-closed release status used by every launch boundary."""

    configured_phase: CutoverPhase
    deployed_phase: CutoverPhase
    phase: CutoverPhase
    evidence_ref: str | None
    evidence: Mapping[str, Any] | None
    blockers: tuple[str, ...]
    policy_version: str = CUTOVER_POLICY_VERSION

    def as_dict(self) -> dict[str, Any]:
        evidence = self.evidence or {}
        images = evidence.get("images")
        architectures = evidence.get("architectures")
        thresholds = evidence.get("thresholds")
        evidence_refs = evidence.get("evidenceRefs")
        evidence_sha256 = (
            hashlib.sha256(
                json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            if evidence
            else None
        )
        generated_at = evidence.get("generatedAt")
        expires_at: str | None = None
        if isinstance(generated_at, str):
            try:
                generated = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
                expires_at = datetime.fromtimestamp(
                    generated.timestamp() + MAX_EVIDENCE_AGE_SECONDS,
                    tz=timezone.utc,
                ).isoformat()
            except ValueError:
                # Malformed generation timestamps are represented by the
                # promotion blockers and intentionally have no expiry value.
                pass
        return {
            "policyVersion": self.policy_version,
            "configuredPhase": self.configured_phase.name.lower(),
            "deployedPhase": self.deployed_phase.name.lower(),
            "phase": self.phase.name.lower(),
            "promotionAllowed": not self.blockers,
            "evidenceRef": self.evidence_ref,
            "evidenceSha256": evidence_sha256,
            "generatedAt": generated_at,
            "expiresAt": expires_at,
            "profileVersion": evidence.get("profileVersion"),
            "profileSha256": evidence.get("profileSha256"),
            "launchPolicyVersion": evidence.get("launchPolicyVersion"),
            "agentProfileVersion": evidence.get("agentProfileVersion"),
            "matrixVersion": evidence.get("matrixVersion"),
            "matrixRows": (
                list(evidence["matrixRows"])
                if isinstance(evidence.get("matrixRows"), list)
                else []
            ),
            "images": dict(images) if isinstance(images, Mapping) else {},
            "architectures": (
                list(architectures) if isinstance(architectures, list) else []
            ),
            "thresholds": (
                dict(thresholds) if isinstance(thresholds, Mapping) else {}
            ),
            "evidenceRefs": (
                list(evidence_refs) if isinstance(evidence_refs, list) else []
            ),
            "blockers": list(self.blockers),
            "directLaunchAllowed": self.phase
            < CutoverPhase.DIRECT_LAUNCH_DISABLED,
        }


@dataclass(frozen=True, slots=True)
class RuntimeSelection:
    """Immutable evidence explaining one cutover-aware runtime choice."""

    runtime_id: str
    authored: bool
    fallback_reason: str | None
    phase: CutoverPhase
    evidence_ref: str | None = None
    evidence_sha256: str | None = None
    policy_version: str = CUTOVER_POLICY_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "policyVersion": self.policy_version,
            "phase": self.phase.name.lower(),
            "runtimeId": self.runtime_id,
            "authored": self.authored,
            "fallbackReason": self.fallback_reason,
            "evidenceRef": self.evidence_ref,
            "evidenceSha256": self.evidence_sha256,
        }


def select_runtime(
    *,
    authored_runtime: str | None,
    configured_default: str,
    phase: CutoverPhase,
    submission_kind: str = "create",
    release_status: EffectivePhase | None = None,
) -> RuntimeSelection:
    """Apply rollout defaults without ever rewriting an explicit selection.

    Create/edit/rerun defaults advance at phase 2; schedule and preset defaults
    advance at phase 3.  Explicit direct launch is rejected from phase 5.  This
    helper never performs automatic fallback: callers must persist the returned
    evidence on the run before launch.
    """

    explicit = str(authored_runtime or "").strip().lower()
    if explicit:
        explicit = normalize_runtime_id(explicit)
        if explicit == "codex_cli" and phase >= CutoverPhase.DIRECT_LAUNCH_DISABLED:
            raise ValueError("codex_direct_launch_disabled_by_cutover_phase")
        return RuntimeSelection(
            explicit,
            True,
            None,
            phase,
            (
                release_status.evidence_ref
                if release_status and not release_status.blockers
                else None
            ),
            (
                release_status.as_dict()["evidenceSha256"]
                if release_status and not release_status.blockers
                else None
            ),
        )

    default = str(configured_default or "codex_cli").strip().lower()
    threshold = {
        "create": CutoverPhase.CREATE_DEFAULT,
        "schedule": CutoverPhase.SCHEDULE_DEFAULT,
        "preset": CutoverPhase.SCHEDULE_DEFAULT,
    }.get(submission_kind, CutoverPhase.BROAD_DEFAULT)
    selected = "omnigent" if default == "codex_cli" and phase >= threshold else default
    return RuntimeSelection(
        selected,
        False,
        None,
        phase,
        (
            release_status.evidence_ref
            if release_status and not release_status.blockers
            else None
        ),
        (
            release_status.as_dict()["evidenceSha256"]
            if release_status and not release_status.blockers
            else None
        ),
    )


def evaluate_promotion(
    *,
    current_phase: CutoverPhase,
    requested_phase: CutoverPhase,
    evidence: Mapping[str, Any] | None,
    now: datetime | None = None,
) -> PromotionDecision:
    """Fail closed unless the next phase has fresh, complete live evidence.

    Promotion is deliberately one phase at a time. Rollback to an earlier phase
    is always allowed because it does not rewrite persisted runtime identity.
    """

    if requested_phase <= current_phase:
        if (
            requested_phase is CutoverPhase.DIRECT_LAUNCH_REMOVED
            and DIRECT_LAUNCH_REMOVAL_VERSION is None
        ):
            return PromotionDecision(
                False,
                current_phase,
                requested_phase,
                ("direct_launch_retirement_not_built",),
            )
        return PromotionDecision(True, current_phase, requested_phase, ())

    blockers: list[str] = []
    if requested_phase != current_phase + 1:
        blockers.append("promotion_must_advance_one_phase")
    if not evidence:
        blockers.append("live_conformance_evidence_missing")
        return PromotionDecision(False, current_phase, requested_phase, tuple(blockers))

    if evidence.get("schemaVersion") != CUTOVER_POLICY_VERSION:
        blockers.append("unsupported_evidence_version")
    generated_at = evidence.get("generatedAt")
    try:
        generated = datetime.fromisoformat(str(generated_at).replace("Z", "+00:00"))
        if generated.tzinfo is None:
            raise ValueError
        age = ((now or datetime.now(timezone.utc)) - generated).total_seconds()
        if age < 0 or age > MAX_EVIDENCE_AGE_SECONDS:
            blockers.append("live_conformance_evidence_stale")
    except (TypeError, ValueError):
        blockers.append("live_conformance_evidence_timestamp_invalid")

    required_true = (
        "profilePolicyReady",
        "allRequiredCasesPassed",
        "secretScansPassed",
        "temporalReplayPassed",
        "historicalReadsPassed",
        "capacitySingleOwnerPassed",
    )
    blockers.extend(
        f"{key}_required" for key in required_true if evidence.get(key) is not True
    )
    thresholds = evidence.get("thresholds")
    threshold_results = (
        thresholds.get("results") if isinstance(thresholds, Mapping) else None
    )
    if (
        not isinstance(thresholds, Mapping)
        or thresholds.get("withinLimits") is not True
        or not isinstance(threshold_results, Mapping)
        or not threshold_results
        or any(result is not True for result in threshold_results.values())
    ):
        blockers.append("rollback_threshold_exceeded_or_missing")
    if (
        evidence.get("profileVersion") != PROFILE_VERSION
        or evidence.get("profileSha256") != PROFILE_SHA256
    ):
        blockers.append("canonical_conformance_profile_required")
    policy_version = evidence.get("launchPolicyVersion")
    if not isinstance(policy_version, str) or not policy_version.strip():
        blockers.append("launch_policy_version_required")
    agent_profile_version = evidence.get("agentProfileVersion")
    if not isinstance(agent_profile_version, str) or not agent_profile_version.strip():
        blockers.append("agent_profile_version_required")
    if evidence.get("matrixVersion") != MATRIX_VERSION:
        blockers.append("unsupported_support_matrix_version")
    declared_rows = evidence.get("matrixRows")
    if not isinstance(declared_rows, list) or {
        row for row in declared_rows if isinstance(row, str)
    } != set(REQUIRED_MATRIX_ROWS):
        blockers.append("matrix_row_coverage_incomplete")
    images = evidence.get("images")
    try:
        if not isinstance(images, Mapping):
            raise ConformanceContractError("release images are required")
        require_pinned_images(images)
    except ConformanceContractError:
        blockers.append("immutable_release_images_required")
    architectures = evidence.get("architectures")
    if not isinstance(architectures, list) or not architectures or any(
        not isinstance(item, str) or not item.strip() for item in architectures
    ):
        blockers.append("tested_architectures_required")
    telemetry = evidence.get("telemetry")
    if not isinstance(telemetry, Mapping) or any(
        not isinstance(telemetry.get(group), Mapping)
        or not telemetry[group]
        for group in REQUIRED_TELEMETRY_GROUPS
    ):
        blockers.append("migration_telemetry_required")
    refs = evidence.get("evidenceRefs")
    if not isinstance(refs, list) or not refs or any(
        not isinstance(ref, str) or not ref.strip() for ref in refs
    ):
        blockers.append("independently_resolvable_evidence_refs_required")
    manifest = evidence.get("evidenceManifest")
    if not isinstance(manifest, list) or not manifest:
        blockers.append("provenance_bound_evidence_manifest_required")
    else:
        manifest_refs: set[str] = set()
        manifest_kinds: set[str] = set()
        valid_manifest = True
        for item in manifest:
            if not isinstance(item, Mapping):
                valid_manifest = False
                continue
            ref = item.get("ref")
            kind = item.get("kind")
            digest = item.get("sha256")
            if (
                not isinstance(ref, str)
                or not ref.strip()
                or not isinstance(kind, str)
                or not kind.strip()
                or not isinstance(digest, str)
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
            ):
                valid_manifest = False
                continue
            if ref in manifest_refs:
                valid_manifest = False
            manifest_refs.add(ref)
            manifest_kinds.add(kind)
        if (
            not valid_manifest
            or not isinstance(refs, list)
            or set(refs) != manifest_refs
        ):
            blockers.append("provenance_bound_evidence_manifest_invalid")
        missing_kinds = set(REQUIRED_EVIDENCE_KINDS) - manifest_kinds
        if missing_kinds:
            blockers.append("complete_evidence_kind_coverage_required")
    if requested_phase is CutoverPhase.DIRECT_LAUNCH_REMOVED:
        if DIRECT_LAUNCH_REMOVAL_VERSION is None:
            blockers.append("direct_launch_retirement_not_built")
        retirement_assertions = (
            "directLaunchCodeRemoved",
            "directLaunchUiRemoved",
            "directLaunchConfigRemoved",
            "duplicateCapacityOwnershipRemoved",
        )
        blockers.extend(
            f"{key}_required"
            for key in retirement_assertions
            if evidence.get(key) is not True
        )
        retirement_refs = evidence.get("retirementEvidenceRefs")
        if not isinstance(retirement_refs, list) or not retirement_refs or any(
            not isinstance(ref, str) or not ref.strip() for ref in retirement_refs
        ):
            blockers.append("retirement_evidence_refs_required")

    return PromotionDecision(
        not blockers, current_phase, requested_phase, tuple(dict.fromkeys(blockers))
    )


def _evidence_path(ref: str) -> Path:
    """Resolve only deployment-local evidence; remote refs are not launch authority."""

    parsed = urlparse(ref)
    if parsed.scheme == "file":
        if parsed.netloc not in {"", "localhost"}:
            raise ValueError("conformance_evidence_ref_not_local")
        return Path(unquote(parsed.path))
    if parsed.scheme:
        raise ValueError("conformance_evidence_ref_not_local")
    return Path(ref)


def _verify_manifest_artifacts(
    evidence: Mapping[str, Any],
    *,
    evidence_document_path: Path,
) -> tuple[str, ...]:
    """Resolve every manifest ref locally, bind its bytes to its digest, and
    re-validate the observed per-row evidence it carries.

    Digest integrity alone is not support. Each artifact is re-parsed and its
    owned rows re-validated against the promotion document's declared images,
    architectures, conformance profile, launch-policy, and agent-profile
    versions, so a syntactically complete promotion document whose artifacts
    are self-asserted, mismatched, or incomplete fails closed here.
    """

    manifest = evidence.get("evidenceManifest")
    if not isinstance(manifest, list) or not manifest:
        return ()

    blockers: list[str] = []
    base = evidence_document_path.resolve().parent
    images = evidence.get("images")
    architectures = evidence.get("architectures")
    profile_version = evidence.get("profileVersion")
    profile_sha256 = evidence.get("profileSha256")
    policy_version = evidence.get("launchPolicyVersion")
    agent_profile_version = evidence.get("agentProfileVersion")
    observed_rows: set[str] = set()
    seen_kinds: set[str] = set()
    ownership_conflict = False
    split_kind = False
    for item in manifest:
        if not isinstance(item, Mapping):
            continue
        ref = item.get("ref")
        expected = item.get("sha256")
        kind = item.get("kind")
        if not isinstance(ref, str) or not isinstance(expected, str):
            continue
        try:
            artifact_path = _evidence_path(ref)
            if not artifact_path.is_absolute():
                artifact_path = base / artifact_path
            content = artifact_path.read_bytes()
        except (OSError, ValueError):
            blockers.append("evidence_manifest_ref_unreadable")
            continue
        if hashlib.sha256(content).hexdigest() != expected:
            blockers.append("evidence_manifest_digest_mismatch")
            continue
        try:
            payload = json.loads(content)
            artifact_kind, rows = validate_matrix_artifact(
                payload,
                expected_kind=kind if isinstance(kind, str) else None,
                images=images,
                architectures=architectures,
                profile_version=profile_version,
                profile_sha256=profile_sha256,
                policy_version=policy_version,
                agent_profile_version=agent_profile_version,
            )
        except (json.JSONDecodeError, UnicodeError, CutoverMatrixError):
            blockers.append("evidence_row_binding_invalid")
            continue
        # The canonical contract binds each evidence kind to exactly one
        # artifact. A hand-authored or mutated promotion document can otherwise
        # splice partial results from separate runs into two artifacts that
        # share a kind but own disjoint rows; row overlap alone would not catch
        # that. Reject the duplicate kind before unioning its rows so split
        # coverage can never authorize a phase.
        if artifact_kind in seen_kinds:
            split_kind = True
            continue
        seen_kinds.add(artifact_kind)
        if observed_rows & rows:
            ownership_conflict = True
        observed_rows |= rows
    if split_kind:
        blockers.append("split_evidence_kind_rejected")
    if ownership_conflict:
        blockers.append("matrix_row_ownership_conflict")
    if observed_rows != set(REQUIRED_MATRIX_ROWS):
        blockers.append("matrix_row_coverage_incomplete")
    return tuple(dict.fromkeys(blockers))


def effective_phase(
    *,
    env: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> EffectivePhase:
    """Load protected release evidence and authorize the configured phase.

    Phase one is the immutable fail-closed baseline. Later phases require a
    local evidence document mounted into the API/worker deployment. Merely
    setting the desired phase never changes execution defaults.
    """

    values = os.environ if env is None else env
    requested = configured_phase(env=values)
    current = deployed_phase(env=values)
    raw_ref = str(
        values.get("MOONMIND_CODEX_OMNIGENT_CONFORMANCE_EVIDENCE_REF", "")
    ).strip()
    ref = raw_ref or None
    if (
        requested is CutoverPhase.DIRECT_LAUNCH_REMOVED
        and DIRECT_LAUNCH_REMOVAL_VERSION is None
    ):
        return EffectivePhase(
            requested,
            current,
            current,
            None,
            None,
            ("direct_launch_retirement_not_built",),
        )
    if requested <= current:
        return EffectivePhase(requested, current, requested, ref, None, ())

    blockers: list[str] = []
    evidence: Mapping[str, Any] | None = None
    evidence_document_path: Path | None = None
    if not ref:
        blockers.append("live_conformance_evidence_missing")
    else:
        try:
            evidence_document_path = _evidence_path(ref)
            payload = json.loads(evidence_document_path.read_text(encoding="utf-8"))
            if not isinstance(payload, Mapping):
                raise ValueError("conformance_evidence_not_object")
            evidence = payload
        except FileNotFoundError:
            blockers.append("live_conformance_evidence_unreadable")
        except (OSError, json.JSONDecodeError, UnicodeError, ValueError) as exc:
            blockers.append(str(exc) or "live_conformance_evidence_unreadable")

    if evidence is not None:
        authorized = str(evidence.get("authorizedPhase") or "").strip().upper()
        if authorized != requested.name:
            blockers.append("evidence_authorized_phase_mismatch")
        evidence_current = str(evidence.get("currentPhase") or "").strip().upper()
        if evidence_current != current.name:
            blockers.append("evidence_current_phase_mismatch")
        decision = evaluate_promotion(
            current_phase=current,
            requested_phase=requested,
            evidence=evidence,
            now=now,
        )
        blockers.extend(decision.blockers)
        if evidence_document_path is not None:
            blockers.extend(
                _verify_manifest_artifacts(
                    evidence,
                    evidence_document_path=evidence_document_path,
                )
            )

    # Native Workflow Chat becomes primary at BROAD_DEFAULT.  Its controlling
    # #3642 report is a separate authority from the older runtime-conformance
    # matrix, so both must be fresh and complete before this handoff.
    if requested >= CutoverPhase.BROAD_DEFAULT:
        native_ref = str(
            values.get("MOONMIND_OMNIGENT_NATIVE_CHAT_ACCEPTANCE_REF", "")
        ).strip()
        native_root = str(
            values.get("MOONMIND_OMNIGENT_NATIVE_CHAT_ACCEPTANCE_EVIDENCE_ROOT", "")
        ).strip()
        expected_commit = str(values.get("MOONMIND_BUILD_COMMIT", "")).strip()
        if not native_ref or not native_root or not expected_commit:
            blockers.append("native_chat_acceptance_evidence_missing")
        else:
            try:
                native_path = _evidence_path(native_ref)
                native_source = json.loads(native_path.read_text(encoding="utf-8"))
                if not isinstance(native_source, Mapping):
                    raise ValueError("native_chat_acceptance_not_object")
                build_native_chat_acceptance_report(
                    native_source,
                    evidence_root=_evidence_path(native_root),
                    expected_commit=expected_commit,
                    now=now,
                )
            except (OSError, ValueError, json.JSONDecodeError, UnicodeError,
                    ConformanceContractError):
                blockers.append("native_chat_acceptance_evidence_invalid")

    unique_blockers = tuple(dict.fromkeys(blockers))
    return EffectivePhase(
        configured_phase=requested,
        deployed_phase=current,
        phase=current if unique_blockers else requested,
        evidence_ref=ref,
        evidence=evidence,
        blockers=unique_blockers,
    )


__all__ = [
    "CUTOVER_POLICY_VERSION",
    "DIRECT_LAUNCH_REMOVAL_VERSION",
    "MAX_EVIDENCE_AGE_SECONDS",
    "REQUIRED_EVIDENCE_KINDS",
    "REQUIRED_TELEMETRY_GROUPS",
    "MATRIX_VERSION",
    "ARTIFACT_SCHEMA_VERSION",
    "ALLOWED_HOST_MODES",
    "REQUIRED_ROW_CATALOG",
    "REQUIRED_MATRIX_ROWS",
    "ROW_CATALOG",
    "MatrixRow",
    "CutoverMatrixError",
    "validate_matrix_artifact",
    "CutoverPhase",
    "EffectivePhase",
    "configured_phase",
    "deployed_phase",
    "effective_phase",
    "PromotionDecision",
    "RuntimeSelection",
    "evaluate_promotion",
    "select_runtime",
]
