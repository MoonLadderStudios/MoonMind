"""Execution lifecycle for the deployment update tool."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping, Protocol

from .deployment_tools import (
    DEPLOYMENT_UPDATE_TOOL_NAME,
    DEPLOYMENT_UPDATE_TOOL_VERSION,
)
from .tool_plan_contracts import ToolFailure, ToolResult

DEPLOYMENT_RUNNER_MODES = frozenset(
    {"privileged_worker", "ephemeral_updater_container"}
)
DEPLOYMENT_UPDATE_MODES = frozenset({"changed_services", "force_recreate"})
DEPLOYMENT_UPDATE_STACKS = frozenset({"moonmind"})
DEPLOYMENT_FINAL_STATUSES = frozenset({"SUCCEEDED", "FAILED", "PARTIALLY_VERIFIED"})
_REDACTED = "[REDACTED]"
_SENSITIVE_KEY_PATTERN = re.compile(
    r"("
    r"token|secret|password|passwd|credential|authorization|"
    r"auth[_-]?header|api[_-]?key|registry[_-]?password"
    r")",
    re.IGNORECASE,
)
_SENSITIVE_VALUE_PATTERN = re.compile(
    r"(bearer\s+[A-Za-z0-9._~+/=-]+|"
    r"(?:token|password|passwd|secret)=[^ \t\n\r,;&\"']+)",
    re.IGNORECASE,
)


class DesiredStateStore(Protocol):
    async def persist(self, payload: Mapping[str, Any]) -> str:
        """Persist desired deployment state and return a store reference."""


class EvidenceWriter(Protocol):
    async def write(self, kind: str, payload: Mapping[str, Any]) -> str:
        """Write structured deployment evidence and return an artifact ref."""


@dataclass(frozen=True, slots=True)
class ComposeCommandPlan:
    runner_mode: str
    pull_args: tuple[str, ...]
    up_args: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ComposeVerification:
    succeeded: bool
    updated_services: tuple[str, ...]
    running_services: tuple[Mapping[str, Any], ...]
    details: Mapping[str, Any]
    status: str | None = None


class ComposeRunner(Protocol):
    async def capture_state(self, *, stack: str, phase: str) -> Mapping[str, Any]:
        """Capture before/after state for a deployment stack."""

    async def pull(self, *, stack: str, command: tuple[str, ...]) -> Mapping[str, Any]:
        """Run the pull command."""

    async def up(self, *, stack: str, command: tuple[str, ...]) -> Mapping[str, Any]:
        """Run the up command."""

    async def verify(
        self,
        *,
        stack: str,
        requested_image: str,
        resolved_digest: str | None,
    ) -> ComposeVerification:
        """Verify the requested desired state is running."""


class DeploymentUpdateLockManager:
    """Nonblocking per-stack lock manager for deployment updates."""

    def __init__(self) -> None:
        self._guard = asyncio.Lock()
        self._held: set[str] = set()

    async def acquire(self, stack: str) -> "DeploymentUpdateLockLease":
        normalized = _required_string(stack, "stack")
        async with self._guard:
            if normalized in self._held:
                raise ToolFailure(
                    error_code="DEPLOYMENT_LOCKED",
                    message=(
                        "Deployment update for stack "
                        f"'{normalized}' is already running."
                    ),
                    retryable=False,
                    details={"stack": normalized},
                )
            self._held.add(normalized)
        return DeploymentUpdateLockLease(self, normalized)

    async def _release(self, stack: str) -> None:
        async with self._guard:
            self._held.discard(stack)


@dataclass(slots=True)
class DeploymentUpdateLockLease:
    _manager: DeploymentUpdateLockManager
    stack: str
    _released: bool = False

    async def release(self) -> None:
        if self._released:
            return
        self._released = True
        await self._manager._release(self.stack)

    async def __aenter__(self) -> "DeploymentUpdateLockLease":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.release()


class InMemoryDesiredStateStore:
    """Deterministic desired-state store for hermetic execution tests."""

    def __init__(self) -> None:
        self.records: list[Mapping[str, Any]] = []

    async def persist(self, payload: Mapping[str, Any]) -> str:
        record = dict(payload)
        self.records.append(record)
        return _stable_ref("desired-state", record)


class InMemoryEvidenceWriter:
    """Deterministic evidence writer for hermetic execution tests."""

    def __init__(self) -> None:
        self.records: list[tuple[str, Mapping[str, Any]]] = []

    async def write(self, kind: str, payload: Mapping[str, Any]) -> str:
        record = dict(payload)
        self.records.append((kind, record))
        return _stable_ref(kind, record)


class DisabledComposeRunner:
    """Fail-closed runner used when deployment-control infrastructure is absent."""

    async def capture_state(self, *, stack: str, phase: str) -> Mapping[str, Any]:
        raise ToolFailure(
            error_code="POLICY_VIOLATION",
            message="Deployment update runner is not configured for this worker.",
            retryable=False,
            details={"stack": stack, "phase": phase},
        )

    async def pull(self, *, stack: str, command: tuple[str, ...]) -> Mapping[str, Any]:
        raise ToolFailure(
            error_code="POLICY_VIOLATION",
            message="Deployment update runner is not configured for this worker.",
            retryable=False,
            details={"stack": stack, "command": list(command)},
        )

    async def up(self, *, stack: str, command: tuple[str, ...]) -> Mapping[str, Any]:
        raise ToolFailure(
            error_code="POLICY_VIOLATION",
            message="Deployment update runner is not configured for this worker.",
            retryable=False,
            details={"stack": stack, "command": list(command)},
        )

    async def verify(
        self,
        *,
        stack: str,
        requested_image: str,
        resolved_digest: str | None,
    ) -> ComposeVerification:
        raise ToolFailure(
            error_code="POLICY_VIOLATION",
            message="Deployment update runner is not configured for this worker.",
            retryable=False,
            details={
                "stack": stack,
                "requested_image": requested_image,
                "resolved_digest": resolved_digest,
            },
        )


@dataclass(slots=True)
class DeploymentUpdateExecutor:
    lock_manager: DeploymentUpdateLockManager
    desired_state_store: DesiredStateStore
    evidence_writer: EvidenceWriter
    runner: ComposeRunner

    async def execute(
        self,
        inputs: Mapping[str, Any],
        context: Mapping[str, Any] | None = None,
    ) -> ToolResult:
        context = dict(context or {})
        progress_events: list[dict[str, str]] = []
        _add_progress(progress_events, "QUEUED", "Deployment update queued.")
        _add_progress(
            progress_events, "VALIDATING", "Validating deployment update input."
        )
        parsed = _parse_inputs(inputs)
        command_plan = build_compose_command_plan(
            mode=parsed["mode"],
            remove_orphans=parsed["removeOrphans"],
            wait=parsed["wait"],
            runner_mode=str(
                context.get("deployment_runner_mode") or "privileged_worker"
            ),
        )
        source_run_id = str(
            context.get("source_run_id")
            or context.get("idempotency_key")
            or context.get("workflow_id")
            or ""
        ).strip() or None
        operator = str(
            context.get("operator") or context.get("principal") or ""
        ).strip()
        operator_role = str(
            context.get("operator_role") or context.get("principal_role") or ""
        ).strip()
        workflow_id = str(context.get("workflow_id") or "").strip() or None
        task_id = (
            str(context.get("task_id") or context.get("workflow_task_id") or "").strip()
            or None
        )
        requested_image = _requested_image(parsed)
        resolved_digest = parsed["image"].get("resolvedDigest")
        started_at = _utc_now()
        before_ref: str | None = None
        after_ref: str | None = None
        command_ref: str | None = None
        verification_ref: str | None = None
        verification: ComposeVerification | None = None
        final_status: str | None = None
        failure_reason: str | None = None
        command_log: dict[str, Any] = {
            "runnerMode": command_plan.runner_mode,
            "pull": {"command": list(command_plan.pull_args)},
            "up": {"command": list(command_plan.up_args)},
        }

        def audit_snapshot(*, completed: bool = False) -> dict[str, Any]:
            return _compact_mapping(
                {
                    "runId": source_run_id,
                    "workflowId": workflow_id,
                    "taskId": task_id,
                    "stack": parsed["stack"],
                    "operator": operator or None,
                    "operatorRole": operator_role or None,
                    "reason": parsed["reason"],
                    "requestedImage": requested_image,
                    "resolvedDigest": resolved_digest,
                    "mode": parsed["mode"],
                    "options": {
                        "removeOrphans": parsed["removeOrphans"],
                        "wait": parsed["wait"],
                    },
                    "startedAt": started_at,
                    "completedAt": _utc_now() if completed else None,
                    "finalStatus": final_status,
                    "failureReason": failure_reason,
                }
            )

        async def write_evidence(kind: str, payload: Mapping[str, Any]) -> str:
            enriched = dict(payload)
            enriched["audit"] = audit_snapshot(completed=final_status is not None)
            return await self.evidence_writer.write(kind, _redact_sensitive(enriched))

        _add_progress(
            progress_events, "LOCK_WAITING", "Waiting for deployment update lock."
        )
        async with await self.lock_manager.acquire(parsed["stack"]):
            try:
                _add_progress(
                    progress_events,
                    "CAPTURING_BEFORE_STATE",
                    "Capturing current deployment state.",
                )
                before_state = await self.runner.capture_state(
                    stack=parsed["stack"], phase="before"
                )
                before_ref = await write_evidence("before-state", before_state)

                _add_progress(
                    progress_events,
                    "PERSISTING_DESIRED_STATE",
                    "Persisting requested deployment state.",
                )
                desired_payload = {
                    "stack": parsed["stack"],
                    "imageRepository": parsed["image"]["repository"],
                    "requestedReference": parsed["image"]["reference"],
                    "resolvedDigest": resolved_digest,
                    "reason": parsed["reason"],
                    "operator": operator or None,
                    "createdAt": _utc_now(),
                    "sourceRunId": source_run_id,
                }
                await self.desired_state_store.persist(desired_payload)

                _add_progress(
                    progress_events, "PULLING_IMAGES", "Pulling requested images."
                )
                pull_result = await self.runner.pull(
                    stack=parsed["stack"], command=command_plan.pull_args
                )
                command_log["pull"]["result"] = pull_result
                _ensure_command_succeeded("pull", pull_result)

                _add_progress(
                    progress_events,
                    "RECREATING_SERVICES",
                    "Recreating deployment services.",
                )
                up_result = await self.runner.up(
                    stack=parsed["stack"], command=command_plan.up_args
                )
                command_log["up"]["result"] = up_result
                _ensure_command_succeeded("up", up_result)
                command_ref = await write_evidence("command-log", command_log)

                _add_progress(progress_events, "VERIFYING", "Verifying deployed state.")
                verification = await self.runner.verify(
                    stack=parsed["stack"],
                    requested_image=requested_image,
                    resolved_digest=resolved_digest,
                )
                final_status = _verification_final_status(verification)
                if final_status != "SUCCEEDED":
                    failure_reason = _verification_failure_reason(verification)
                verification_ref = await write_evidence(
                    "verification",
                    {
                        "succeeded": verification.succeeded,
                        "status": final_status,
                        "details": dict(verification.details),
                        "requestedImage": requested_image,
                        "resolvedDigest": resolved_digest,
                    },
                )
            except Exception as exc:
                final_status = "FAILED"
                failure_reason = failure_reason or _failure_reason(exc)
                _record_command_exception(command_log, exc)
                raise
            finally:
                if (
                    command_ref is None
                    and ("result" in command_log["pull"] or "error" in command_log)
                ):
                    command_ref = await write_evidence("command-log", command_log)
                if before_ref is not None:
                    _add_progress(
                        progress_events,
                        "CAPTURING_AFTER_STATE",
                        "Capturing deployment state after update.",
                    )
                    after_state = await self.runner.capture_state(
                        stack=parsed["stack"], phase="after"
                    )
                    after_ref = await write_evidence("after-state", after_state)

        if verification is None:
            raise ToolFailure(
                error_code="DEPLOYMENT_FAILED",
                message="Deployment update did not complete verification.",
                retryable=False,
                details={"stack": parsed["stack"]},
            )
        if final_status is None:
            final_status = _verification_final_status(verification)
        if after_ref is None:
            raise ToolFailure(
                error_code="DEPLOYMENT_EVIDENCE_INCOMPLETE",
                message="Deployment update completed without after-state evidence.",
                retryable=False,
                details={"stack": parsed["stack"]},
            )
        if command_ref is None:
            raise ToolFailure(
                error_code="DEPLOYMENT_EVIDENCE_INCOMPLETE",
                message="Deployment update completed without command-log evidence.",
                retryable=False,
                details={"stack": parsed["stack"]},
            )
        if verification_ref is None:
            raise ToolFailure(
                error_code="DEPLOYMENT_EVIDENCE_INCOMPLETE",
                message="Deployment update completed without verification evidence.",
                retryable=False,
                details={"stack": parsed["stack"]},
            )

        terminal_message = _terminal_progress_message(final_status)
        _add_progress(progress_events, final_status, terminal_message)
        outputs = {
            "status": final_status,
            "stack": parsed["stack"],
            "requestedImage": requested_image,
            "resolvedDigest": resolved_digest,
            "updatedServices": list(verification.updated_services),
            "runningServices": [
                dict(service) for service in verification.running_services
            ],
            "beforeStateArtifactRef": before_ref,
            "afterStateArtifactRef": after_ref,
            "commandLogArtifactRef": command_ref,
            "verificationArtifactRef": verification_ref,
            "audit": _redact_sensitive(audit_snapshot(completed=True)),
        }
        return ToolResult(
            status="COMPLETED" if final_status == "SUCCEEDED" else "FAILED",
            outputs=outputs,
            progress={
                "percent": 100,
                "state": final_status,
                "message": terminal_message,
                "events": progress_events,
            },
        )


def _add_progress(events: list[dict[str, str]], state: str, message: str) -> None:
    events.append({"state": state, "message": message})


def _verification_final_status(verification: ComposeVerification) -> str:
    explicit = str(verification.status or "").strip().upper()
    if explicit:
        if explicit not in DEPLOYMENT_FINAL_STATUSES:
            raise ToolFailure(
                error_code="DEPLOYMENT_VERIFICATION_INVALID",
                message=f"Unsupported deployment verification status '{explicit}'.",
                retryable=False,
                details={"status": explicit},
            )
        if verification.succeeded and explicit != "SUCCEEDED":
            raise ToolFailure(
                error_code="DEPLOYMENT_VERIFICATION_INVALID",
                message="Deployment verification status conflicts with success flag.",
                retryable=False,
                details={"status": explicit, "succeeded": verification.succeeded},
            )
        if explicit == "SUCCEEDED" and not verification.succeeded:
            raise ToolFailure(
                error_code="DEPLOYMENT_VERIFICATION_INVALID",
                message=(
                    "Deployment verification success status conflicts "
                    "with success flag."
                ),
                retryable=False,
                details={"status": explicit, "succeeded": verification.succeeded},
            )
        return explicit
    return "SUCCEEDED" if verification.succeeded else "FAILED"


def _verification_failure_reason(verification: ComposeVerification) -> str | None:
    details = dict(verification.details)
    for key in ("failureReason", "failure_reason", "message", "reason"):
        value = details.get(key)
        if value:
            return str(value)
    failed_checks = details.get("failedChecks") or details.get("failed_checks")
    if failed_checks:
        return f"Verification checks failed: {failed_checks}"
    if not verification.succeeded:
        return "Deployment verification did not prove desired state."
    return None


def _failure_reason(exc: Exception) -> str:
    if isinstance(exc, ToolFailure):
        return exc.message
    return str(exc)


def _terminal_progress_message(status: str) -> str:
    if status == "SUCCEEDED":
        return "Deployment update succeeded."
    if status == "PARTIALLY_VERIFIED":
        return "Deployment update partially verified."
    return "Deployment update failed."


def _compact_mapping(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None}


def _redact_sensitive(value: Any, key: str | None = None) -> Any:
    if key and _SENSITIVE_KEY_PATTERN.search(key):
        return _REDACTED
    if isinstance(value, Mapping):
        return {str(k): _redact_sensitive(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_sensitive(item, key) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_sensitive(item, key) for item in value)
    if isinstance(value, str):
        return _SENSITIVE_VALUE_PATTERN.sub(_REDACTED, value)
    return value


def build_compose_command_plan(
    *,
    mode: str,
    remove_orphans: bool,
    wait: bool,
    runner_mode: str,
) -> ComposeCommandPlan:
    normalized_mode = _required_string(mode, "mode")
    if normalized_mode not in DEPLOYMENT_UPDATE_MODES:
        raise ToolFailure(
            error_code="INVALID_INPUT",
            message=f"Unsupported deployment update mode '{normalized_mode}'.",
            retryable=False,
            details={"mode": normalized_mode},
        )
    normalized_runner = _required_string(runner_mode, "deployment_runner_mode")
    if normalized_runner not in DEPLOYMENT_RUNNER_MODES:
        raise ToolFailure(
            error_code="POLICY_VIOLATION",
            message=f"Unsupported deployment runner mode '{normalized_runner}'.",
            retryable=False,
            details={"runner_mode": normalized_runner},
        )

    up_args = ["docker", "compose", "up", "-d"]
    if normalized_mode == "force_recreate":
        up_args.append("--force-recreate")
    if remove_orphans:
        up_args.append("--remove-orphans")
    if wait:
        up_args.append("--wait")

    return ComposeCommandPlan(
        runner_mode=normalized_runner,
        pull_args=(
            "docker",
            "compose",
            "pull",
            "--policy",
            "always",
            "--ignore-buildable",
        ),
        up_args=tuple(up_args),
    )


def build_deployment_update_handler(
    executor: DeploymentUpdateExecutor | None = None,
):
    resolved_executor = executor or DeploymentUpdateExecutor(
        lock_manager=DeploymentUpdateLockManager(),
        desired_state_store=InMemoryDesiredStateStore(),
        evidence_writer=InMemoryEvidenceWriter(),
        runner=DisabledComposeRunner(),
    )

    async def _handler(
        inputs: Mapping[str, Any], context: Mapping[str, Any] | None = None
    ) -> ToolResult:
        context_executor = None
        if isinstance(context, Mapping):
            candidate = context.get("deployment_update_executor")
            if isinstance(candidate, DeploymentUpdateExecutor):
                context_executor = candidate
        return await (context_executor or resolved_executor).execute(inputs, context)

    return _handler


def register_deployment_update_tool_handler(
    dispatcher: Any,
    *,
    executor: DeploymentUpdateExecutor | None = None,
) -> None:
    dispatcher.register_skill(
        skill_name=DEPLOYMENT_UPDATE_TOOL_NAME,
        version=DEPLOYMENT_UPDATE_TOOL_VERSION,
        handler=build_deployment_update_handler(executor),
    )


def _parse_inputs(inputs: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(inputs, Mapping):
        raise ToolFailure(
            "INVALID_INPUT", "Deployment inputs must be an object.", False
        )
    forbidden = {"command", "composeFile", "hostPath", "updaterRunnerImage"}
    found_forbidden = sorted(forbidden.intersection(inputs.keys()))
    if found_forbidden:
        raise ToolFailure(
            error_code="INVALID_INPUT",
            message="Deployment update inputs contain forbidden fields.",
            retryable=False,
            details={"fields": found_forbidden},
        )

    image = inputs.get("image")
    if not isinstance(image, Mapping):
        raise ToolFailure("INVALID_INPUT", "Deployment image must be an object.", False)

    stack = _required_string(inputs.get("stack"), "stack")
    if stack not in DEPLOYMENT_UPDATE_STACKS:
        raise ToolFailure(
            error_code="INVALID_INPUT",
            message=f"Unsupported deployment stack '{stack}'.",
            retryable=False,
            details={
                "stack": stack,
                "allowed_stacks": sorted(DEPLOYMENT_UPDATE_STACKS),
            },
        )

    parsed = {
        "stack": stack,
        "image": {
            "repository": _required_string(image.get("repository"), "image.repository"),
            "reference": _required_string(image.get("reference"), "image.reference"),
        },
        "mode": str(inputs.get("mode") or "changed_services").strip(),
        "removeOrphans": _optional_bool(inputs, "removeOrphans", default=False),
        "wait": _optional_bool(inputs, "wait", default=True),
        "reason": _required_string(inputs.get("reason"), "reason"),
    }
    resolved_digest = image.get("resolvedDigest")
    if resolved_digest is not None and str(resolved_digest).strip():
        parsed["image"]["resolvedDigest"] = str(resolved_digest).strip()
    return parsed


def _requested_image(parsed: Mapping[str, Any]) -> str:
    image = parsed["image"]
    assert isinstance(image, Mapping)
    reference = str(image["reference"])
    separator = "@" if reference.startswith("sha256:") else ":"
    return f"{image['repository']}{separator}{reference}"


def _required_string(value: Any, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ToolFailure(
            error_code="INVALID_INPUT",
            message=f"{field_name} is required.",
            retryable=False,
            details={"field": field_name},
        )
    return normalized


def _optional_bool(
    inputs: Mapping[str, Any], field_name: str, *, default: bool
) -> bool:
    if field_name not in inputs:
        return default
    value = inputs[field_name]
    if not isinstance(value, bool):
        raise ToolFailure(
            error_code="INVALID_INPUT",
            message=f"{field_name} must be a boolean.",
            retryable=False,
            details={"field": field_name, "value_type": type(value).__name__},
        )
    return value


def _ensure_command_succeeded(phase: str, result: Mapping[str, Any]) -> None:
    if not isinstance(result, Mapping):
        raise ToolFailure(
            error_code="DEPLOYMENT_COMMAND_FAILED",
            message=f"Deployment {phase} command returned an invalid result.",
            retryable=False,
            details={"phase": phase, "result_type": type(result).__name__},
        )
    for key in ("exitCode", "exit_code", "returncode"):
        if key in result:
            try:
                code = int(result[key])
            except (TypeError, ValueError) as exc:
                raise ToolFailure(
                    error_code="DEPLOYMENT_COMMAND_FAILED",
                    message=(
                        f"Deployment {phase} command returned a non-numeric exit code."
                    ),
                    retryable=False,
                    details={"phase": phase, "field": key, "value": result[key]},
                ) from exc
            if code != 0:
                raise ToolFailure(
                    error_code="DEPLOYMENT_COMMAND_FAILED",
                    message=f"Deployment {phase} command failed with exit code {code}.",
                    retryable=False,
                    details={"phase": phase, "exit_code": code, "result": dict(result)},
                )
            return
    for key in ("ok", "success", "succeeded"):
        if key in result:
            if result[key] is not True:
                raise ToolFailure(
                    error_code="DEPLOYMENT_COMMAND_FAILED",
                    message=f"Deployment {phase} command reported failure.",
                    retryable=False,
                    details={"phase": phase, "field": key, "result": dict(result)},
                )
            return
    status = str(result.get("status") or "").strip().lower()
    if status:
        if status not in {"completed", "succeeded", "success", "ok"}:
            raise ToolFailure(
                error_code="DEPLOYMENT_COMMAND_FAILED",
                message=f"Deployment {phase} command reported status '{status}'.",
                retryable=False,
                details={"phase": phase, "status": status, "result": dict(result)},
            )
        return
    raise ToolFailure(
        error_code="DEPLOYMENT_COMMAND_FAILED",
        message=f"Deployment {phase} command result did not include a success signal.",
        retryable=False,
        details={"phase": phase, "result": dict(result)},
    )


def _record_command_exception(command_log: dict[str, Any], exc: Exception) -> None:
    if "error" in command_log:
        return
    if isinstance(exc, ToolFailure):
        command_log["error"] = exc.to_payload()
    else:
        command_log["error"] = {
            "error_code": "DEPLOYMENT_COMMAND_EXCEPTION",
            "message": str(exc),
            "retryable": False,
            "details": {"type": type(exc).__name__},
        }


def _stable_ref(kind: str, payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    digest = hashlib.sha256(kind.encode("utf-8") + b"\0" + encoded).hexdigest()
    return f"art:sha256:{digest}"


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


__all__ = [
    "ComposeCommandPlan",
    "ComposeVerification",
    "DeploymentUpdateExecutor",
    "DeploymentUpdateLockManager",
    "DEPLOYMENT_RUNNER_MODES",
    "DEPLOYMENT_UPDATE_STACKS",
    "DEPLOYMENT_UPDATE_MODES",
    "DisabledComposeRunner",
    "InMemoryDesiredStateStore",
    "InMemoryEvidenceWriter",
    "build_compose_command_plan",
    "build_deployment_update_handler",
    "register_deployment_update_tool_handler",
]
