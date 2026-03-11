"""Standalone enum definitions shared between ORM models and Pydantic schemas.

Keeping enums here breaks the circular import that arises when Pydantic schema
modules (e.g. ``moonmind.schemas.workflow_models``) need to reference the same
enum types used by ``api_service.db.models``.

Import diagram (post-fix):
    api_service.db.enums          ← no upstream project deps
    api_service.db.models         → api_service.db.enums
    moonmind.schemas.workflow_models → api_service.db.enums
"""

from __future__ import annotations

import enum

# ---------------------------------------------------------------------------
# Orchestrator enums
# ---------------------------------------------------------------------------


class OrchestratorRunStatus(str, enum.Enum):
    """Lifecycle states tracked for orchestrator runs."""

    PENDING = "pending"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class OrchestratorRunPriority(str, enum.Enum):
    """Execution priority for orchestrator runs."""

    NORMAL = "normal"
    HIGH = "high"


class OrchestratorPlanStep(str, enum.Enum):
    """Supported steps inside an orchestrator ActionPlan."""

    ANALYZE = "analyze"
    PATCH = "patch"
    BUILD = "build"
    RESTART = "restart"
    VERIFY = "verify"
    ROLLBACK = "rollback"


class OrchestratorPlanStepStatus(str, enum.Enum):
    """Statuses describing plan step execution progress."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class OrchestratorPlanOrigin(str, enum.Enum):
    """Source responsible for generating an ActionPlan."""

    OPERATOR = "operator"
    LLM = "llm"
    SYSTEM = "system"


class OrchestratorApprovalRequirement(str, enum.Enum):
    """Approval enforcement options for protected services."""

    NONE = "none"
    PRE_RUN = "pre-run"
    PRE_VERIFY = "pre-verify"


class OrchestratorRunArtifactType(str, enum.Enum):
    """Classifications for artifacts stored per orchestrator run."""

    PATCH_DIFF = "patch_diff"
    BUILD_LOG = "build_log"
    VERIFY_LOG = "verify_log"
    ROLLBACK_LOG = "rollback_log"
    METRICS = "metrics"
    PLAN_SNAPSHOT = "plan_snapshot"


class OrchestratorTaskState(str, enum.Enum):
    """Celery state transitions recorded for orchestrator steps."""

    PENDING = "PENDING"
    STARTED = "STARTED"
    RETRY = "RETRY"
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"


class OrchestratorTaskStepStatus(str, enum.Enum):
    """Status values persisted for orchestrator task runtime steps."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


# ---------------------------------------------------------------------------
# Spec workflow enums
# ---------------------------------------------------------------------------


class SpecWorkflowRunStatus(str, enum.Enum):
    """Lifecycle states tracked for Spec workflow runs."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    NO_WORK = "no_work"
    CANCELLED = "cancelled"
    RETRYING = "retrying"


class SpecWorkflowRunPhase(str, enum.Enum):
    """High-level phase executed by the Spec workflow chain."""

    DISCOVER = "discover"
    SUBMIT = "submit"
    APPLY = "apply"
    PUBLISH = "publish"
    COMPLETE = "complete"


class SpecWorkflowTaskStatus(str, enum.Enum):
    """Execution state tracked for each workflow task."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class SpecWorkflowTaskName(str, enum.Enum):
    """Supported Celery task identifiers for the chain."""

    DISCOVER = "discover"
    SUBMIT = "submit"
    APPLY = "apply"
    PUBLISH = "publish"
    FINALIZE = "finalize"
    RETRY_HOOK = "retry-hook"


class WorkflowArtifactType(str, enum.Enum):
    """Artifacts captured while the Spec workflow executes."""

    CODEX_LOGS = "codex_logs"
    CODEX_PATCH = "codex_patch"
    GH_PUSH_LOG = "gh_push_log"
    GH_PR_RESPONSE = "gh_pr_response"
    APPLY_OUTPUT = "apply_output"
    PR_PAYLOAD = "pr_payload"
    RETRY_CONTEXT = "retry_context"


# ---------------------------------------------------------------------------
# Codex / GitHub credential enums
# ---------------------------------------------------------------------------


class CodexPreflightStatus(str, enum.Enum):
    """Codex login verification result stored on a run."""

    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


class CodexCredentialStatus(str, enum.Enum):
    """Result of Codex credential validation."""

    VALID = "valid"
    INVALID = "invalid"
    EXPIRES_SOON = "expires_soon"


class GitHubCredentialStatus(str, enum.Enum):
    """Result of GitHub credential validation."""

    VALID = "valid"
    INVALID = "invalid"
    SCOPE_MISSING = "scope_missing"


class CodexWorkerShardStatus(str, enum.Enum):
    """Health state of a Codex worker shard."""

    ACTIVE = "active"
    DRAINING = "draining"
    OFFLINE = "offline"


class CodexAuthVolumeStatus(str, enum.Enum):
    """Auth state of a Codex auth volume."""

    READY = "ready"
    NEEDS_AUTH = "needs_auth"
    ERROR = "error"


# ---------------------------------------------------------------------------
# Spec Automation enums
# ---------------------------------------------------------------------------


class SpecAutomationPhase(str, enum.Enum):
    """Execution phase within a Spec Automation run."""

    PREPARE_JOB = "prepare_job"
    START_JOB_CONTAINER = "start_job_container"
    GIT_CLONE = "git_clone"
    SPECIFY = "speckit_specify"
    PLAN = "speckit_plan"
    TASKS = "speckit_tasks"
    ANALYZE = "speckit_analyze"
    IMPLEMENT = "speckit_implement"
    # Backward-compatible aliases for persisted values and legacy clients.
    SPECKIT_SPECIFY = SPECIFY
    SPECKIT_PLAN = PLAN
    SPECKIT_TASKS = TASKS
    SPECKIT_ANALYZE = ANALYZE
    SPECKIT_IMPLEMENT = IMPLEMENT
    COMMIT_PUSH = "commit_push"
    OPEN_PR = "open_pr"
    CLEANUP = "cleanup"


class SpecAutomationRunStatus(str, enum.Enum):
    """Overall lifecycle status for a Spec Automation run."""

    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    NO_CHANGES = "no_changes"


class SpecAutomationTaskStatus(str, enum.Enum):
    """Per-phase task status for Spec Automation."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    RETRYING = "retrying"


class SpecAutomationArtifactType(str, enum.Enum):
    """Artifact classifications for Spec Automation runs."""

    STDOUT_LOG = "stdout_log"
    STDERR_LOG = "stderr_log"
    DIFF_SUMMARY = "diff_summary"
    COMMIT_STATUS = "commit_status"
    METRICS_SNAPSHOT = "metrics_snapshot"
    ENVIRONMENT_INFO = "environment_info"


__all__ = [
    "OrchestratorRunStatus",
    "OrchestratorRunPriority",
    "OrchestratorPlanStep",
    "OrchestratorPlanStepStatus",
    "OrchestratorPlanOrigin",
    "OrchestratorApprovalRequirement",
    "OrchestratorRunArtifactType",
    "OrchestratorTaskState",
    "OrchestratorTaskStepStatus",
    "SpecWorkflowRunStatus",
    "SpecWorkflowRunPhase",
    "SpecWorkflowTaskStatus",
    "SpecWorkflowTaskName",
    "WorkflowArtifactType",
    "CodexPreflightStatus",
    "CodexCredentialStatus",
    "GitHubCredentialStatus",
    "CodexWorkerShardStatus",
    "CodexAuthVolumeStatus",
    "SpecAutomationPhase",
    "SpecAutomationRunStatus",
    "SpecAutomationTaskStatus",
    "SpecAutomationArtifactType",
]
