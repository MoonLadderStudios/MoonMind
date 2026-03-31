"""Contracts for tools, artifacts, and DAG plans.

These models implement the runtime contracts described in
``docs/Skills/SkillAndPlanContracts.md``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Mapping

ARTIFACT_REF_PREFIX = "art:sha256:"
REGISTRY_DIGEST_PREFIX = "reg:sha256:"
SUPPORTED_PLAN_VERSIONS = frozenset({"1.0"})
SUPPORTED_FAILURE_MODES = frozenset({"FAIL_FAST", "CONTINUE"})
TOOL_RESULT_STATUSES = frozenset({"COMPLETED", "FAILED", "CANCELLED"})
SKILL_RESULT_STATUSES = TOOL_RESULT_STATUSES  # backward-compat alias
REVIEW_VERDICTS = frozenset({"PASS", "FAIL", "INCONCLUSIVE"})
DEFAULT_SKIP_TOOL_TYPES = ("repo.publish", "codex.execute")
EXPLICIT_BINDING_REASONS = frozenset(
    {"stronger_isolation", "specialized_credentials", "clearer_routing"}
)
_DEFAULT_ACTIVITY_TYPE = "mm.tool.execute"
_LEGACY_DEFAULT_ACTIVITY_TYPE = "mm.skill.execute"
OBSERVABILITY_OUTCOMES = frozenset({"completed", "failed", "canceled", "partial"})
TOOL_FAILURE_CODES = frozenset(
    {
        "INVALID_INPUT",
        "PERMISSION_DENIED",
        "NOT_FOUND",
        "CONFLICT",
        "RATE_LIMITED",
        "TRANSIENT",
        "TIMEOUT",
        "EXTERNAL_FAILED",
        "CANCELLED",
        "INTERNAL",
    }
)
SKILL_FAILURE_CODES = TOOL_FAILURE_CODES  # backward-compat alias
TEMPORAL_ARTIFACT_ID_PATTERN = re.compile(r"^art_[0-9A-HJKMNP-TV-Z]{26}$")


class ContractValidationError(ValueError):
    """Raised when contract payloads fail validation."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _ensure_positive_int(value: int, *, field_name: str) -> int:
    if value <= 0:
        raise ContractValidationError(
            "invalid_policy", f"{field_name} must be greater than zero"
        )
    return value


def _ensure_non_empty(value: str, *, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ContractValidationError(
            "invalid_contract", f"{field_name} cannot be blank"
        )
    return normalized


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    """Opaque artifact reference passed between workflow steps and activities."""

    artifact_ref: str
    content_type: str
    bytes: int
    created_at: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        artifact_ref = _ensure_non_empty(self.artifact_ref, field_name="artifact_ref")
        if not artifact_ref.startswith(ARTIFACT_REF_PREFIX):
            raise ContractValidationError(
                "invalid_artifact_ref",
                f"artifact_ref must start with '{ARTIFACT_REF_PREFIX}'",
            )
        _ensure_non_empty(self.content_type, field_name="content_type")
        if self.bytes < 0:
            raise ContractValidationError("invalid_artifact", "bytes must be >= 0")

        try:
            datetime.fromisoformat(self.created_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ContractValidationError(
                "invalid_artifact", "created_at must be an ISO-8601 timestamp"
            ) from exc

    @classmethod
    def create(
        cls,
        *,
        artifact_ref: str,
        content_type: str,
        bytes: int,
        metadata: Mapping[str, Any] | None = None,
        created_at: datetime | None = None,
    ) -> "ArtifactRef":
        timestamp = (created_at or datetime.now(tz=UTC)).replace(microsecond=0)
        return cls(
            artifact_ref=artifact_ref,
            content_type=content_type,
            bytes=bytes,
            created_at=timestamp.isoformat().replace("+00:00", "Z"),
            metadata=dict(metadata or {}),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "artifact_ref": self.artifact_ref,
            "content_type": self.content_type,
            "bytes": self.bytes,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class ToolPolicyTimeouts:
    """Activity timeout policy defaults for one tool definition."""

    start_to_close_seconds: int
    schedule_to_close_seconds: int

    def __post_init__(self) -> None:
        _ensure_positive_int(
            self.start_to_close_seconds,
            field_name="policies.timeouts.start_to_close_seconds",
        )
        _ensure_positive_int(
            self.schedule_to_close_seconds,
            field_name="policies.timeouts.schedule_to_close_seconds",
        )
        if self.schedule_to_close_seconds < self.start_to_close_seconds:
            raise ContractValidationError(
                "invalid_policy",
                "policies.timeouts.schedule_to_close_seconds must be >= start_to_close_seconds",
            )

    def to_payload(self) -> dict[str, int]:
        return {
            "start_to_close_seconds": self.start_to_close_seconds,
            "schedule_to_close_seconds": self.schedule_to_close_seconds,
        }


@dataclass(frozen=True, slots=True)
class ToolPolicyRetries:
    """Retry policy defaults for one tool definition."""

    max_attempts: int
    backoff: str = "exponential"
    non_retryable_error_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _ensure_positive_int(
            self.max_attempts, field_name="policies.retries.max_attempts"
        )
        _ensure_non_empty(self.backoff, field_name="policies.retries.backoff")
        for code in self.non_retryable_error_codes:
            _ensure_non_empty(code, field_name="non_retryable_error_codes[]")

    def to_payload(self) -> dict[str, Any]:
        return {
            "max_attempts": self.max_attempts,
            "backoff": self.backoff,
            "non_retryable_error_codes": list(self.non_retryable_error_codes),
        }


@dataclass(frozen=True, slots=True)
class ToolPolicies:
    """Default execution policies for a tool definition."""

    timeouts: ToolPolicyTimeouts
    retries: ToolPolicyRetries

    def to_payload(self) -> dict[str, Any]:
        return {
            "timeouts": self.timeouts.to_payload(),
            "retries": self.retries.to_payload(),
        }


@dataclass(frozen=True, slots=True)
class ToolExecutorBinding:
    """Execution binding of a skill to activity type and selector mode."""

    activity_type: str
    selector_mode: str = "by_capability"
    explicit_binding_reason: str | None = None

    def __post_init__(self) -> None:
        _ensure_non_empty(self.activity_type, field_name="executor.activity_type")
        _ensure_non_empty(self.selector_mode, field_name="executor.selector.mode")
        if self.activity_type in (
            _DEFAULT_ACTIVITY_TYPE,
            _LEGACY_DEFAULT_ACTIVITY_TYPE,
        ):
            # Both the current and legacy default activity types are free bindings.
            if self.explicit_binding_reason is not None:
                raise ContractValidationError(
                    "invalid_contract",
                    "executor.binding_reason is only valid for explicit activity bindings",
                )
            return
        if self.explicit_binding_reason not in EXPLICIT_BINDING_REASONS:
            raise ContractValidationError(
                "invalid_contract",
                "executor.binding_reason must be one of "
                f"{sorted(EXPLICIT_BINDING_REASONS)} for explicit activity bindings",
            )

    def to_payload(self) -> dict[str, Any]:
        payload = {
            "activity_type": self.activity_type,
            "selector": {"mode": self.selector_mode},
        }
        if self.explicit_binding_reason is not None:
            payload["binding_reason"] = self.explicit_binding_reason
        return payload


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """Validated skill contract stored in the registry snapshot."""

    name: str
    version: str
    description: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    executor: ToolExecutorBinding
    required_capabilities: tuple[str, ...]
    policies: ToolPolicies
    allowed_roles: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _ensure_non_empty(self.name, field_name="name")
        _ensure_non_empty(self.version, field_name="version")
        if not isinstance(self.input_schema, Mapping):
            raise ContractValidationError(
                "invalid_contract", "inputs.schema must be an object"
            )
        if not isinstance(self.output_schema, Mapping):
            raise ContractValidationError(
                "invalid_contract", "outputs.schema must be an object"
            )
        if not self.required_capabilities:
            raise ContractValidationError(
                "invalid_contract",
                "requirements.capabilities must include at least one capability",
            )

    @property
    def key(self) -> tuple[str, str]:
        return self.name, self.version

    def to_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "inputs": {"schema": dict(self.input_schema)},
            "outputs": {"schema": dict(self.output_schema)},
            "executor": self.executor.to_payload(),
            "requirements": {"capabilities": list(self.required_capabilities)},
            "policies": self.policies.to_payload(),
            "security": {"allowed_roles": list(self.allowed_roles)},
        }


@dataclass(frozen=True, slots=True)
class StepSkillSelectorExact:
    name: str
    version: str | None = None

    def __post_init__(self) -> None:
        _ensure_non_empty(self.name, field_name="node.skills.include[].name")

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"name": self.name}
        if self.version is not None:
            payload["version"] = self.version
        return payload


@dataclass(frozen=True, slots=True)
class StepSkillSelectors:
    sets: tuple[str, ...] = ()
    include: tuple[StepSkillSelectorExact, ...] = ()
    exclude: tuple[str, ...] = ()
    materialization_mode: str | None = None

    def __post_init__(self) -> None:
        if self.materialization_mode is not None:
            if self.materialization_mode not in {"hybrid", "remote", "local", "none"}:
                raise ContractValidationError(
                    "invalid_plan",
                    "node.skills.materializationMode must be hybrid, remote, local, or none"
                )

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if self.sets:
            payload["sets"] = list(self.sets)
        if self.include:
            payload["include"] = [inc.to_payload() for inc in self.include]
        if self.exclude:
            payload["exclude"] = list(self.exclude)
        if self.materialization_mode is not None:
            payload["materializationMode"] = self.materialization_mode
        return payload


@dataclass(frozen=True, slots=True)
class Step:
    """Plan node invocation of a tool contract (skill or agent_runtime)."""

    id: str
    skill_name: str
    skill_version: str
    inputs: Mapping[str, Any]
    options: Mapping[str, Any] = field(default_factory=dict)
    tool_type: str = "skill"
    skills: StepSkillSelectors | None = None

    def __post_init__(self) -> None:
        _ensure_non_empty(self.id, field_name="node.id")
        _ensure_non_empty(self.skill_name, field_name="node.tool.name")
        if self.tool_type == "skill":
            _ensure_non_empty(self.skill_version, field_name="node.tool.version")
        if not isinstance(self.inputs, Mapping):
            raise ContractValidationError(
                "invalid_plan", "node.inputs must be an object"
            )
        if not isinstance(self.options, Mapping):
            raise ContractValidationError(
                "invalid_plan", "node.options must be an object"
            )

    @property
    def skill_key(self) -> tuple[str, str]:
        return self.skill_name, self.skill_version

    @property
    def tool_name(self) -> str:
        return self.skill_name

    @property
    def tool_version(self) -> str:
        return self.skill_version

    @property
    def tool_key(self) -> tuple[str, str]:
        return self.skill_key

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "tool": {
                "type": self.tool_type,
                "name": self.skill_name,
            },
            "inputs": dict(self.inputs),
        }
        if self.skill_version:
            payload["tool"]["version"] = self.skill_version
        if self.tool_type == "skill":
            payload["skill"] = {"name": self.skill_name, "version": self.skill_version}
        if self.options:
            payload["options"] = dict(self.options)
        if self.skills is not None:
            payload["skills"] = self.skills.to_payload()
        return payload


@dataclass(frozen=True, slots=True)
class ToolFailure(Exception):
    """Normalized failure envelope for skill execution."""

    error_code: str
    message: str
    retryable: bool
    details: Mapping[str, Any] = field(default_factory=dict)
    cause: "ToolFailure | None" = None

    def __post_init__(self) -> None:
        _ensure_non_empty(self.error_code, field_name="error_code")
        _ensure_non_empty(self.message, field_name="message")

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "error_code": self.error_code,
            "message": self.message,
            "retryable": bool(self.retryable),
            "details": dict(self.details),
        }
        if self.cause is not None:
            payload["cause"] = self.cause.to_payload()
        return payload


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Structured result for one skill invocation."""

    status: str
    outputs: Mapping[str, Any] = field(default_factory=dict)
    output_artifacts: tuple[ArtifactRef, ...] = ()
    progress: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in TOOL_RESULT_STATUSES:
            raise ContractValidationError(
                "invalid_result",
                f"status must be one of {sorted(TOOL_RESULT_STATUSES)}",
            )
        if not isinstance(self.outputs, Mapping):
            raise ContractValidationError("invalid_result", "outputs must be an object")
        if not isinstance(self.progress, Mapping):
            raise ContractValidationError(
                "invalid_result", "progress must be an object"
            )

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": self.status,
            "outputs": dict(self.outputs),
            "progress": dict(self.progress),
        }
        if self.output_artifacts:
            payload["output_artifacts"] = [
                artifact.to_payload() for artifact in self.output_artifacts
            ]
        return payload


@dataclass(frozen=True, slots=True)
class ActivityInvocationEnvelope:
    """Shared business envelope for one side-effecting activity request."""

    correlation_id: str
    idempotency_key: str | None = None
    input_refs: tuple[str, ...] = ()
    parameters: Mapping[str, Any] = field(default_factory=dict)
    side_effecting: bool = True

    def __post_init__(self) -> None:
        _ensure_non_empty(self.correlation_id, field_name="correlation_id")
        if self.side_effecting:
            _ensure_non_empty(
                str(self.idempotency_key or ""), field_name="idempotency_key"
            )
        for ref in self.input_refs:
            _ensure_non_empty(str(ref), field_name="input_refs[]")
        if not isinstance(self.parameters, Mapping):
            raise ContractValidationError(
                "invalid_contract", "parameters must be an object"
            )

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "correlation_id": self.correlation_id,
            "input_refs": list(self.input_refs),
            "parameters": dict(self.parameters),
        }
        if self.idempotency_key is not None:
            payload["idempotency_key"] = self.idempotency_key
        return payload


@dataclass(frozen=True, slots=True)
class CompactActivityResult:
    """Compact response envelope for activity outputs and summaries."""

    output_refs: tuple[str, ...] = ()
    summary: Mapping[str, Any] = field(default_factory=dict)
    metrics: Mapping[str, Any] | None = None
    diagnostics_ref: str | None = None

    def __post_init__(self) -> None:
        for ref in self.output_refs:
            _ensure_non_empty(str(ref), field_name="output_refs[]")
        if not isinstance(self.summary, Mapping):
            raise ContractValidationError("invalid_result", "summary must be an object")
        if self.metrics is not None and not isinstance(self.metrics, Mapping):
            raise ContractValidationError("invalid_result", "metrics must be an object")
        if self.diagnostics_ref is not None:
            _ensure_non_empty(self.diagnostics_ref, field_name="diagnostics_ref")

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "output_refs": list(self.output_refs),
            "summary": dict(self.summary),
        }
        if self.metrics is not None:
            payload["metrics"] = dict(self.metrics)
        if self.diagnostics_ref is not None:
            payload["diagnostics_ref"] = self.diagnostics_ref
        return payload


@dataclass(frozen=True, slots=True)
class ActivityExecutionContext:
    """Runtime-derived Temporal metadata for one activity attempt."""

    workflow_id: str
    run_id: str
    activity_id: str
    attempt: int
    task_queue: str

    def __post_init__(self) -> None:
        _ensure_non_empty(self.workflow_id, field_name="workflow_id")
        _ensure_non_empty(self.run_id, field_name="run_id")
        _ensure_non_empty(self.activity_id, field_name="activity_id")
        _ensure_positive_int(self.attempt, field_name="attempt")
        _ensure_non_empty(self.task_queue, field_name="task_queue")

    def to_payload(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "activity_id": self.activity_id,
            "attempt": self.attempt,
            "task_queue": self.task_queue,
        }


@dataclass(frozen=True, slots=True)
class ObservabilitySummary:
    """Structured operator-facing summary for one activity outcome."""

    workflow_id: str
    run_id: str
    activity_type: str
    activity_id: str
    attempt: int
    correlation_id: str
    idempotency_key_hash: str
    outcome: str
    diagnostics_ref: str | None = None
    metrics_dimensions: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _ensure_non_empty(self.workflow_id, field_name="workflow_id")
        _ensure_non_empty(self.run_id, field_name="run_id")
        _ensure_non_empty(self.activity_type, field_name="activity_type")
        _ensure_non_empty(self.activity_id, field_name="activity_id")
        _ensure_positive_int(self.attempt, field_name="attempt")
        _ensure_non_empty(self.correlation_id, field_name="correlation_id")
        _ensure_non_empty(self.idempotency_key_hash, field_name="idempotency_key_hash")
        if self.outcome not in OBSERVABILITY_OUTCOMES:
            raise ContractValidationError(
                "invalid_result",
                f"outcome must be one of {sorted(OBSERVABILITY_OUTCOMES)}",
            )
        if self.diagnostics_ref is not None:
            _ensure_non_empty(self.diagnostics_ref, field_name="diagnostics_ref")
        if not isinstance(self.metrics_dimensions, Mapping):
            raise ContractValidationError(
                "invalid_result", "metrics_dimensions must be an object"
            )

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "activity_type": self.activity_type,
            "activity_id": self.activity_id,
            "attempt": self.attempt,
            "correlation_id": self.correlation_id,
            "idempotency_key_hash": self.idempotency_key_hash,
            "outcome": self.outcome,
            "metrics_dimensions": dict(self.metrics_dimensions),
        }
        if self.diagnostics_ref is not None:
            payload["diagnostics_ref"] = self.diagnostics_ref
        return payload


@dataclass(frozen=True, slots=True)
class PlanRegistrySnapshot:
    """Pinned registry snapshot metadata referenced by a plan."""

    digest: str
    artifact_ref: str

    def __post_init__(self) -> None:
        digest = _ensure_non_empty(
            self.digest, field_name="metadata.registry_snapshot.digest"
        )
        if not digest.startswith(REGISTRY_DIGEST_PREFIX):
            raise ContractValidationError(
                "invalid_plan",
                f"registry snapshot digest must start with '{REGISTRY_DIGEST_PREFIX}'",
            )
        artifact_ref = _ensure_non_empty(
            self.artifact_ref,
            field_name="metadata.registry_snapshot.artifact_ref",
        )
        if not (
            artifact_ref.startswith(ARTIFACT_REF_PREFIX)
            or artifact_ref.startswith("artifact://")
            or TEMPORAL_ARTIFACT_ID_PATTERN.fullmatch(artifact_ref)
        ):
            raise ContractValidationError(
                "invalid_plan",
                "registry snapshot artifact_ref must be a supported artifact locator",
            )

    def to_payload(self) -> dict[str, str]:
        return {"digest": self.digest, "artifact_ref": self.artifact_ref}


@dataclass(frozen=True, slots=True)
class PlanMetadata:
    """Human/context metadata for a plan artifact."""

    title: str
    created_at: str
    registry_snapshot: PlanRegistrySnapshot

    def __post_init__(self) -> None:
        _ensure_non_empty(self.title, field_name="metadata.title")
        try:
            datetime.fromisoformat(self.created_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ContractValidationError(
                "invalid_plan", "metadata.created_at must be an ISO-8601 timestamp"
            ) from exc

    def to_payload(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "created_at": self.created_at,
            "registry_snapshot": self.registry_snapshot.to_payload(),
        }


@dataclass(frozen=True, slots=True)
class ApprovalPolicyPolicy:
    """Per-plan approval policy configuration."""

    enabled: bool = False
    max_review_attempts: int = 2
    reviewer_model: str = "default"
    review_timeout_seconds: int = 120
    skip_tool_types: tuple[str, ...] = DEFAULT_SKIP_TOOL_TYPES

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ContractValidationError(
                "invalid_policy", "approval_policy.enabled must be a boolean"
            )
        if self.max_review_attempts < 0:
            raise ContractValidationError(
                "invalid_policy",
                "approval_policy.max_review_attempts must be >= 0",
            )
        if self.review_timeout_seconds <= 0:
            raise ContractValidationError(
                "invalid_policy",
                "approval_policy.review_timeout_seconds must be > 0",
            )

    def to_payload(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "max_review_attempts": self.max_review_attempts,
            "reviewer_model": self.reviewer_model,
            "review_timeout_seconds": self.review_timeout_seconds,
            "skip_tool_types": list(self.skip_tool_types),
        }


@dataclass(frozen=True, slots=True)
class PlanPolicy:
    """Execution policy for a plan."""

    failure_mode: str = "FAIL_FAST"
    max_concurrency: int = 1
    approval_policy: ApprovalPolicyPolicy | None = None

    def __post_init__(self) -> None:
        if self.failure_mode not in SUPPORTED_FAILURE_MODES:
            raise ContractValidationError(
                "invalid_plan",
                f"policy.failure_mode must be one of {sorted(SUPPORTED_FAILURE_MODES)}",
            )
        _ensure_positive_int(self.max_concurrency, field_name="policy.max_concurrency")

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "failure_mode": self.failure_mode,
            "max_concurrency": self.max_concurrency,
        }
        if self.approval_policy is not None and self.approval_policy.enabled:
            payload["approval_policy"] = self.approval_policy.to_payload()
        return payload


@dataclass(frozen=True, slots=True)
class PlanEdge:
    """Directed dependency edge between plan nodes."""

    from_node: str
    to_node: str

    def __post_init__(self) -> None:
        _ensure_non_empty(self.from_node, field_name="edge.from")
        _ensure_non_empty(self.to_node, field_name="edge.to")

    def to_payload(self) -> dict[str, str]:
        return {"from": self.from_node, "to": self.to_node}


@dataclass(frozen=True, slots=True)
class PlanDefinition:
    """Validated DAG-first plan payload."""

    plan_version: str
    metadata: PlanMetadata
    policy: PlanPolicy
    nodes: tuple[Step, ...]
    edges: tuple[PlanEdge, ...]

    def __post_init__(self) -> None:
        if self.plan_version not in SUPPORTED_PLAN_VERSIONS:
            raise ContractValidationError(
                "invalid_plan",
                f"Unsupported plan_version '{self.plan_version}'",
            )
        if not self.nodes:
            raise ContractValidationError(
                "invalid_plan", "plan must define at least one node"
            )

    def to_payload(self) -> dict[str, Any]:
        return {
            "plan_version": self.plan_version,
            "metadata": self.metadata.to_payload(),
            "policy": self.policy.to_payload(),
            "nodes": [node.to_payload() for node in self.nodes],
            "edges": [edge.to_payload() for edge in self.edges],
        }


def parse_step(payload: Mapping[str, Any]) -> Step:
    """Parse one plan node payload into ``Step``."""

    tool_payload = payload.get("tool")
    skill_payload = payload.get("skill")
    node_payload: Mapping[str, Any] | None = None
    tool_type = "skill"

    if isinstance(tool_payload, Mapping):
        tool_type = str(
            tool_payload.get("type") or tool_payload.get("kind") or "skill"
        ).strip().lower()
        if tool_type not in ("skill", "agent_runtime"):
            raise ContractValidationError(
                "invalid_plan",
                f"node.tool.type must be 'skill' or 'agent_runtime', got '{tool_type}'",
            )
        node_payload = tool_payload
    elif isinstance(skill_payload, Mapping):
        node_payload = skill_payload
    else:
        raise ContractValidationError(
            "invalid_plan", "node.tool must be an object (node.skill is legacy alias)"
        )

    inputs = payload.get("inputs")
    if not isinstance(inputs, Mapping):
        # Legacy compatibility: allow inline inputs under tool.inputs or skill.args.
        inline_inputs = node_payload.get("inputs")
        if not isinstance(inline_inputs, Mapping):
            inline_inputs = node_payload.get("args")
        inputs = inline_inputs if isinstance(inline_inputs, Mapping) else {}

    skills_raw = payload.get("skills")
    parsed_skills: StepSkillSelectors | None = None
    if skills_raw is not None:
        if not isinstance(skills_raw, Mapping):
            raise ContractValidationError("invalid_plan", "node.skills must be an object")

        sets = skills_raw.get("sets")
        if sets is not None and not isinstance(sets, list):
            raise ContractValidationError("invalid_plan", "node.skills.sets must be a list")

        exclude = skills_raw.get("exclude")
        if exclude is not None and not isinstance(exclude, list):
            raise ContractValidationError("invalid_plan", "node.skills.exclude must be a list")

        include = skills_raw.get("include")
        if include is not None and not isinstance(include, list):
            raise ContractValidationError("invalid_plan", "node.skills.include must be a list")

        parsed_include = []
        if include:
            for item in include:
                if not isinstance(item, Mapping):
                    raise ContractValidationError("invalid_plan", "node.skills.include entries must be objects")
                name = str(item.get("name") or "").strip()
                if not name:
                    raise ContractValidationError("invalid_plan", "node.skills.include[].name is required")
                version = item.get("version")
                parsed_include.append(
                    StepSkillSelectorExact(
                        name=name,
                        version=str(version).strip() if version else None
                    )
                )

        mat_mode = skills_raw.get("materializationMode")

        parsed_skills = StepSkillSelectors(
            sets=tuple(str(s).strip() for s in sets if str(s).strip()) if sets else (),
            include=tuple(parsed_include),
            exclude=tuple(str(s).strip() for s in exclude if str(s).strip()) if exclude else (),
            materialization_mode=str(mat_mode).strip().lower() if mat_mode else None
        )

    return Step(
        id=str(payload.get("id") or "").strip(),
        skill_name=str(node_payload.get("name") or node_payload.get("id") or "").strip(),
        skill_version=str(node_payload.get("version") or "").strip(),
        inputs=inputs,
        options=(
            payload.get("options")
            if isinstance(payload.get("options"), Mapping)
            else {}
        ),
        tool_type=tool_type,
        skills=parsed_skills,
    )


def parse_plan_definition(payload: Mapping[str, Any]) -> PlanDefinition:
    """Parse untrusted plan payload into a validated ``PlanDefinition``."""

    if not isinstance(payload, Mapping):
        raise ContractValidationError("invalid_plan", "plan payload must be an object")

    metadata_raw = payload.get("metadata")
    if not isinstance(metadata_raw, Mapping):
        raise ContractValidationError("invalid_plan", "metadata must be an object")

    snapshot_raw = metadata_raw.get("registry_snapshot")
    if not isinstance(snapshot_raw, Mapping):
        raise ContractValidationError(
            "invalid_plan", "metadata.registry_snapshot must be an object"
        )

    policy_raw = payload.get("policy")
    if not isinstance(policy_raw, Mapping):
        raise ContractValidationError("invalid_plan", "policy must be an object")

    nodes_raw = payload.get("nodes")
    edges_raw = payload.get("edges", [])
    if not isinstance(nodes_raw, list):
        raise ContractValidationError("invalid_plan", "nodes must be an array")
    if not isinstance(edges_raw, list):
        raise ContractValidationError("invalid_plan", "edges must be an array")

    nodes = tuple(parse_step(node) for node in nodes_raw)

    parsed_edges: list[PlanEdge] = []
    for edge in edges_raw:
        if not isinstance(edge, Mapping):
            raise ContractValidationError(
                "invalid_plan", "edge entries must be objects"
            )
        parsed_edges.append(
            PlanEdge(
                from_node=str(edge.get("from") or "").strip(),
                to_node=str(edge.get("to") or "").strip(),
            )
        )

    metadata = PlanMetadata(
        title=str(metadata_raw.get("title") or "").strip(),
        created_at=str(metadata_raw.get("created_at") or "").strip(),
        registry_snapshot=PlanRegistrySnapshot(
            digest=str(snapshot_raw.get("digest") or "").strip(),
            artifact_ref=str(snapshot_raw.get("artifact_ref") or "").strip(),
        ),
    )

    approval_policy_raw = policy_raw.get("approval_policy")
    if isinstance(approval_policy_raw, Mapping):
        skip_raw = approval_policy_raw.get("skip_tool_types", [])
        if not isinstance(skip_raw, list):
            skip_raw = []
        approval_policy: ApprovalPolicyPolicy | None = ApprovalPolicyPolicy(
            enabled=bool(approval_policy_raw.get("enabled", False)),
            max_review_attempts=(
                int(raw_attempts)
                if (raw_attempts := approval_policy_raw.get("max_review_attempts")) is not None
                else 2
            ),
            reviewer_model=str(
                approval_policy_raw.get("reviewer_model") or "default"
            ).strip(),
            review_timeout_seconds=(
                int(raw_timeout)
                if (raw_timeout := approval_policy_raw.get("review_timeout_seconds")) is not None
                else 120
            ),
            skip_tool_types=tuple(
                str(t).strip() for t in skip_raw if str(t).strip()
            ) if skip_raw else DEFAULT_SKIP_TOOL_TYPES,
        )
    else:
        approval_policy = None  # Plan did not specify approval_policy

    policy = PlanPolicy(
        failure_mode=str(policy_raw.get("failure_mode") or "FAIL_FAST").strip(),
        max_concurrency=int(policy_raw.get("max_concurrency") or 1),
        approval_policy=approval_policy,
    )

    return PlanDefinition(
        plan_version=str(payload.get("plan_version") or "").strip(),
        metadata=metadata,
        policy=policy,
        nodes=nodes,
        edges=tuple(parsed_edges),
    )


def parse_tool_definition(payload: Mapping[str, Any]) -> ToolDefinition:
    """Parse and validate a registry tool definition payload."""

    if not isinstance(payload, Mapping):
        raise ContractValidationError(
            "invalid_registry", "Tool definition entry must be an object"
        )

    tool_type = str(payload.get("type") or "skill").strip().lower()
    if tool_type and tool_type != "skill":
        raise ContractValidationError(
            "invalid_registry",
            "Tool definition type must be 'skill' for the current runtime contract",
        )

    inputs = payload.get("inputs")
    outputs = payload.get("outputs")
    executor = payload.get("executor")
    requirements = payload.get("requirements")
    policies = payload.get("policies")
    security = payload.get("security")

    if not isinstance(inputs, Mapping) or not isinstance(inputs.get("schema"), Mapping):
        raise ContractValidationError("invalid_registry", "inputs.schema is required")
    if not isinstance(outputs, Mapping) or not isinstance(
        outputs.get("schema"), Mapping
    ):
        raise ContractValidationError("invalid_registry", "outputs.schema is required")
    if not isinstance(executor, Mapping):
        raise ContractValidationError("invalid_registry", "executor is required")
    if not isinstance(requirements, Mapping):
        raise ContractValidationError("invalid_registry", "requirements is required")
    if not isinstance(policies, Mapping):
        raise ContractValidationError("invalid_registry", "policies is required")

    timeout_payload = policies.get("timeouts")
    retry_payload = policies.get("retries")
    if not isinstance(timeout_payload, Mapping):
        raise ContractValidationError(
            "invalid_registry", "policies.timeouts is required"
        )
    if not isinstance(retry_payload, Mapping):
        raise ContractValidationError(
            "invalid_registry", "policies.retries is required"
        )

    caps_raw = requirements.get("capabilities")
    if not isinstance(caps_raw, list) or not caps_raw:
        raise ContractValidationError(
            "invalid_registry", "requirements.capabilities must be a non-empty array"
        )

    allowed_roles: tuple[str, ...] = ()
    if isinstance(security, Mapping):
        roles = security.get("allowed_roles")
        if isinstance(roles, list):
            allowed_roles = tuple(
                str(role).strip() for role in roles if str(role).strip()
            )

    non_retryable = retry_payload.get("non_retryable_error_codes", [])
    if not isinstance(non_retryable, list):
        raise ContractValidationError(
            "invalid_registry",
            "policies.retries.non_retryable_error_codes must be an array",
        )

    return ToolDefinition(
        name=str(payload.get("name") or "").strip(),
        version=str(payload.get("version") or "").strip(),
        description=str(payload.get("description") or "").strip(),
        input_schema=dict(inputs["schema"]),
        output_schema=dict(outputs["schema"]),
        executor=ToolExecutorBinding(
            activity_type=str(executor.get("activity_type") or "").strip(),
            selector_mode=str(
                (
                    executor.get("selector")
                    if isinstance(executor.get("selector"), Mapping)
                    else {}
                ).get("mode")
                or "by_capability"
            ).strip(),
            explicit_binding_reason=(
                str(executor.get("binding_reason") or "").strip() or None
            ),
        ),
        required_capabilities=tuple(
            str(capability).strip()
            for capability in caps_raw
            if str(capability).strip()
        ),
        policies=ToolPolicies(
            timeouts=ToolPolicyTimeouts(
                start_to_close_seconds=int(
                    timeout_payload.get("start_to_close_seconds") or 0
                ),
                schedule_to_close_seconds=int(
                    timeout_payload.get("schedule_to_close_seconds") or 0
                ),
            ),
            retries=ToolPolicyRetries(
                max_attempts=int(retry_payload.get("max_attempts") or 0),
                backoff=str(retry_payload.get("backoff") or "exponential").strip(),
                non_retryable_error_codes=tuple(
                    str(code).strip() for code in non_retryable if str(code).strip()
                ),
            ),
        ),
        allowed_roles=allowed_roles,
    )


# Backward-compatible aliases — existing consumers can keep importing Skill*
SkillPolicyTimeouts = ToolPolicyTimeouts
SkillPolicyRetries = ToolPolicyRetries
SkillPolicies = ToolPolicies

__all__ = [
    "ARTIFACT_REF_PREFIX",
    "REGISTRY_DIGEST_PREFIX",
    "DEFAULT_SKIP_TOOL_TYPES",
    "REVIEW_VERDICTS",
    "SUPPORTED_PLAN_VERSIONS",
    "SUPPORTED_FAILURE_MODES",
    "TOOL_FAILURE_CODES",
    "TOOL_RESULT_STATUSES",
    "SKILL_FAILURE_CODES",
    "SKILL_RESULT_STATUSES",
    "ArtifactRef",
    "ActivityExecutionContext",
    "ActivityInvocationEnvelope",
    "CompactActivityResult",
    "ContractValidationError",
    "EXPLICIT_BINDING_REASONS",
    "OBSERVABILITY_OUTCOMES",
    "ObservabilitySummary",
    "PlanDefinition",
    "PlanEdge",
    "PlanMetadata",
    "PlanPolicy",
    "PlanRegistrySnapshot",
    "ApprovalPolicyPolicy",
    "ToolDefinition",
    "ToolExecutorBinding",
    "ToolFailure",
    "Step",
    "ToolPolicies",
    "ToolPolicyRetries",
    "ToolPolicyTimeouts",
    "SkillPolicies",
    "SkillPolicyRetries",
    "SkillPolicyTimeouts",
    "ToolResult",
    "parse_plan_definition",
    "parse_tool_definition",
    "parse_step",
]
