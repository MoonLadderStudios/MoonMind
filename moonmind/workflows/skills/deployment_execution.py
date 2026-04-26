"""Execution lifecycle for the deployment update tool."""

from __future__ import annotations

import asyncio
import hashlib
import json
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
                    message=f"Deployment update for stack '{normalized}' is already running.",
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
        parsed = _parse_inputs(inputs)
        command_plan = build_compose_command_plan(
            mode=parsed["mode"],
            remove_orphans=parsed["removeOrphans"],
            wait=parsed["wait"],
            runner_mode=str(context.get("deployment_runner_mode") or "privileged_worker"),
        )
        source_run_id = str(
            context.get("source_run_id")
            or context.get("idempotency_key")
            or context.get("workflow_id")
            or ""
        ).strip() or None
        operator = str(context.get("operator") or context.get("principal") or "").strip()
        requested_image = _requested_image(parsed)
        resolved_digest = parsed["image"].get("resolvedDigest")

        async with await self.lock_manager.acquire(parsed["stack"]):
            before_state = await self.runner.capture_state(
                stack=parsed["stack"], phase="before"
            )
            before_ref = await self.evidence_writer.write("before-state", before_state)

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
            desired_state_ref = await self.desired_state_store.persist(desired_payload)

            pull_result = await self.runner.pull(
                stack=parsed["stack"], command=command_plan.pull_args
            )
            up_result = await self.runner.up(
                stack=parsed["stack"], command=command_plan.up_args
            )
            command_ref = await self.evidence_writer.write(
                "command-log",
                {
                    "runnerMode": command_plan.runner_mode,
                    "pull": {"command": list(command_plan.pull_args), "result": pull_result},
                    "up": {"command": list(command_plan.up_args), "result": up_result},
                },
            )

            verification = await self.runner.verify(
                stack=parsed["stack"],
                requested_image=requested_image,
                resolved_digest=resolved_digest,
            )
            verification_ref = await self.evidence_writer.write(
                "verification",
                {
                    "succeeded": verification.succeeded,
                    "details": dict(verification.details),
                    "requestedImage": requested_image,
                    "resolvedDigest": resolved_digest,
                },
            )
            after_state = await self.runner.capture_state(
                stack=parsed["stack"], phase="after"
            )
            after_ref = await self.evidence_writer.write("after-state", after_state)

        outputs = {
            "status": "SUCCEEDED" if verification.succeeded else "FAILED",
            "stack": parsed["stack"],
            "requestedImage": requested_image,
            "resolvedDigest": resolved_digest,
            "updatedServices": list(verification.updated_services),
            "runningServices": [dict(service) for service in verification.running_services],
            "beforeStateArtifactRef": before_ref,
            "afterStateArtifactRef": after_ref,
            "commandLogArtifactRef": command_ref,
            "verificationArtifactRef": verification_ref,
            "desiredStateRef": desired_state_ref,
        }
        return ToolResult(
            status="COMPLETED" if verification.succeeded else "FAILED",
            outputs=outputs,
            progress={"percent": 100},
        )


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
        raise ToolFailure("INVALID_INPUT", "Deployment inputs must be an object.", False)
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

    parsed = {
        "stack": _required_string(inputs.get("stack"), "stack"),
        "image": {
            "repository": _required_string(image.get("repository"), "image.repository"),
            "reference": _required_string(image.get("reference"), "image.reference"),
        },
        "mode": str(inputs.get("mode") or "changed_services").strip(),
        "removeOrphans": bool(inputs.get("removeOrphans", False)),
        "wait": bool(inputs.get("wait", True)),
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
    "DEPLOYMENT_UPDATE_MODES",
    "DisabledComposeRunner",
    "InMemoryDesiredStateStore",
    "InMemoryEvidenceWriter",
    "build_compose_command_plan",
    "build_deployment_update_handler",
    "register_deployment_update_tool_handler",
]
