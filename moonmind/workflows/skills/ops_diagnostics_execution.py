"""Read-only ops diagnosis execution for remediation workflows."""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass, replace
from typing import Any, Mapping, Protocol, Sequence

from moonmind.utils.logging import redact_sensitive_payload

from .deployment_execution import (
    DEPLOYMENT_FINAL_STATUSES,
    HostDockerComposeRunner,
    InMemoryEvidenceWriter,
    TemporalDeploymentEvidenceWriter,
    _compact_mapping,
    _ensure_command_succeeded,
    _execution_ref_from_context,
    _parse_json_records,
    _redact_sensitive,
    _tail_text,
    _utc_now,
)
from .deployment_tools import OPS_DIAGNOSE_STACK_TOOL_NAME
from .tool_plan_contracts import ToolFailure, ToolResult

OPS_DIAGNOSIS_ARTIFACT_TYPE = "remediation.ops_diagnosis"
OPS_DIAGNOSIS_STACKS = frozenset({"moonmind"})
OPS_DIAGNOSIS_INCLUDES = frozenset(
    {
        "compose_ps",
        "compose_images",
        "container_health",
        "container_inspect_summary",
        "recent_logs",
        "api_health",
        "worker_health",
        "temporal_connectivity",
        "artifact_store_health",
        "disk_memory_cpu",
    }
)
DEFAULT_OPS_DIAGNOSIS_INCLUDES = (
    "compose_ps",
    "container_health",
    "recent_logs",
    "api_health",
    "worker_health",
    "temporal_connectivity",
)
DEFAULT_MOONMIND_SERVICES = frozenset(
    {
        "api",
        "postgres",
        "temporal",
        "temporal-ui",
        "temporal-worker-artifacts",
        "temporal-worker-agent-runtime",
        "temporal-worker-deployment-control",
        "temporal-worker-integrations",
        "temporal-worker-llm",
        "temporal-worker-sandbox",
        "temporal-worker-workflow",
        "temporal-worker-workflow-merge-automation",
        "qdrant",
        "minio",
        "docker-proxy",
    }
)
OPS_DIAGNOSIS_TAIL_LINES_MIN = 50
OPS_DIAGNOSIS_TAIL_LINES_MAX = 1000
OPS_DIAGNOSIS_TAIL_LINES_DEFAULT = 300


class OpsDiagnosisEvidenceWriter(Protocol):
    async def write(self, kind: str, payload: Mapping[str, Any]) -> str:
        """Write redacted diagnosis evidence and return an artifact ref."""


class OpsDiagnosisRunner(Protocol):
    async def collect(
        self,
        *,
        stack: str,
        include: str,
        services: tuple[str, ...],
        tail_lines: int,
    ) -> Mapping[str, Any]:
        """Collect one allowlisted evidence class using fixed read-only commands."""


@dataclass(frozen=True, slots=True)
class TemporalOpsDiagnosisEvidenceWriter(TemporalDeploymentEvidenceWriter):
    """Ops diagnosis evidence writer backed by Temporal artifacts."""

    async def write(self, kind: str, payload: Mapping[str, Any]) -> str:
        encoded = (
            json.dumps(payload, sort_keys=True, default=str, indent=2) + "\n"
        ).encode("utf-8")
        artifact, _upload = await self.artifact_service.create(
            principal=self.principal,
            content_type="application/json",
            link=self.execution_ref,
            metadata_json={
                "artifact_type": OPS_DIAGNOSIS_ARTIFACT_TYPE,
                "artifactType": OPS_DIAGNOSIS_ARTIFACT_TYPE,
                "artifactClass": OPS_DIAGNOSIS_ARTIFACT_TYPE,
                "opsDiagnosisKind": kind,
            },
        )
        completed = await self.artifact_service.write_complete(
            artifact_id=artifact.artifact_id,
            principal=self.principal,
            payload=encoded,
            content_type="application/json",
        )
        return str(getattr(completed, "artifact_id", artifact.artifact_id))


class DisabledOpsDiagnosisRunner:
    """Fail-closed runner used when deployment-control diagnostics are unavailable."""

    async def collect(
        self,
        *,
        stack: str,
        include: str,
        services: tuple[str, ...],
        tail_lines: int,
    ) -> Mapping[str, Any]:
        raise ToolFailure(
            error_code="POLICY_VIOLATION",
            message="Ops diagnosis runner is not configured for this worker.",
            retryable=False,
            details={
                "stack": stack,
                "include": include,
                "failureClass": "policy_violation",
            },
        )


class HostDockerComposeOpsDiagnosisRunner(HostDockerComposeRunner):
    """Read-only Docker Compose diagnosis runner for deployment-control workers."""

    async def collect(
        self,
        *,
        stack: str,
        include: str,
        services: tuple[str, ...],
        tail_lines: int,
    ) -> Mapping[str, Any]:
        if include == "compose_ps":
            return {"services": await self._run_compose_json(("ps", "--format", "json"))}
        if include == "compose_images":
            return {
                "images": await self._run_compose_json(("images", "--format", "json"))
            }
        if include == "container_health":
            return await self._container_health(services=services)
        if include == "container_inspect_summary":
            return await self._container_inspect_summary(services=services)
        if include == "recent_logs":
            return await self._recent_logs(services=services, tail_lines=tail_lines)
        if include == "api_health":
            return await self._run_service_command(
                "api_health", ("docker", "compose", "ps", "--format", "json", "api")
            )
        if include == "worker_health":
            return await self._run_service_command(
                "worker_health",
                ("docker", "compose", "ps", "--format", "json", *services),
            )
        if include == "temporal_connectivity":
            return await self._run_service_command(
                "temporal_connectivity",
                ("docker", "compose", "ps", "--format", "json", "temporal"),
            )
        if include == "artifact_store_health":
            return await self._run_service_command(
                "artifact_store_health",
                ("docker", "compose", "ps", "--format", "json", "minio"),
            )
        if include == "disk_memory_cpu":
            return await self._docker_system_df()
        raise ToolFailure(
            error_code="INVALID_INPUT",
            message=f"Unsupported ops diagnosis include '{include}'.",
            retryable=False,
            details={"include": include, "failureClass": "invalid_input"},
        )

    async def _recent_logs(
        self, *, services: tuple[str, ...], tail_lines: int
    ) -> Mapping[str, Any]:
        command = ("docker", "compose", "logs", "--no-color", "--tail", str(tail_lines), *services)
        result = await self._run_compose_command(
            command,
            max_stdout_chars=tail_lines * 500,
            max_stderr_chars=2000,
        )
        return {"tailLines": tail_lines, "services": list(services), "result": result}

    async def _container_health(self, *, services: tuple[str, ...]) -> Mapping[str, Any]:
        records = []
        ps = await self._run_compose_json(("ps", "--format", "json", *services))
        for item in ps:
            name = str(
                item.get("Name") or item.get("ContainerName") or item.get("ID") or ""
            ).strip()
            service = str(item.get("Service") or item.get("Name") or "").strip()
            state = str(item.get("State") or item.get("Status") or "").strip()
            health = str(item.get("Health") or item.get("health") or "").strip()
            records.append(
                _compact_mapping(
                    {
                        "service": service or None,
                        "container": name or None,
                        "state": state or None,
                        "health": health or None,
                    }
                )
            )
        return {"containers": records}

    async def _container_inspect_summary(
        self, *, services: tuple[str, ...]
    ) -> Mapping[str, Any]:
        ps = await self._run_compose_json(("ps", "--format", "json", *services))

        async def inspect_one(item: Mapping[str, Any]) -> Mapping[str, Any] | None:
            container = str(item.get("ID") or item.get("Name") or "").strip()
            service = str(item.get("Service") or item.get("Name") or "").strip()
            if not container:
                return None
            try:
                result = await self._run_docker_json(
                    ("docker", "inspect", container),
                    failure_class="container_inspect_failure",
                )
            except Exception as exc:
                return _compact_mapping(
                    {
                        "service": service or None,
                        "container": container,
                        "status": "FAILED",
                        "reason": _redact_sensitive(_failure_reason(exc)),
                    }
                )
            inspect = result[0] if result else {}
            state = inspect.get("State") if isinstance(inspect, Mapping) else {}
            config = inspect.get("Config") if isinstance(inspect, Mapping) else {}
            return _compact_mapping(
                {
                    "service": service or None,
                    "container": container,
                    "status": "SUCCEEDED",
                    "state": state if isinstance(state, Mapping) else None,
                    "image": (
                        config.get("Image") if isinstance(config, Mapping) else None
                    ),
                    "restartCount": inspect.get("RestartCount")
                    if isinstance(inspect, Mapping)
                    else None,
                }
            )

        inspected = await asyncio.gather(*(inspect_one(item) for item in ps))
        summaries = [item for item in inspected if item is not None]
        return {"containers": summaries}

    async def _docker_system_df(self) -> Mapping[str, Any]:
        return {
            "dockerSystemDf": await self._run_docker_json(
                ("docker", "system", "df", "--format", "json"),
                failure_class="docker_system_df_failure",
            )
        }

    async def _run_service_command(
        self, kind: str, command: Sequence[str]
    ) -> Mapping[str, Any]:
        result = await self._run_compose_command(
            command,
            max_stdout_chars=8000,
            max_stderr_chars=2000,
        )
        _ensure_command_succeeded(kind, result)
        return {"kind": kind, "result": result}

    async def _run_docker_json(
        self, command: Sequence[str], *, failure_class: str
    ) -> list[Mapping[str, Any]]:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(self._local_dir()),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self.command_timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()
            raise ToolFailure(
                error_code="DEPLOYMENT_COMMAND_FAILED",
                message="Ops diagnosis Docker command timed out.",
                retryable=False,
                details={
                    "command": list(command),
                    "timeoutSeconds": self.command_timeout_seconds,
                    "failureClass": failure_class,
                },
            ) from exc
        if process.returncode != 0:
            raise ToolFailure(
                error_code="DEPLOYMENT_COMMAND_FAILED",
                message="Ops diagnosis Docker command failed.",
                retryable=False,
                details={
                    "command": list(command),
                    "exitCode": process.returncode,
                    "stderr": _tail_text(stderr, max_chars=2000),
                    "failureClass": failure_class,
                },
            )
        text = stdout.decode("utf-8", errors="replace").strip()
        if not text:
            return []
        try:
            return _parse_json_records(text)
        except json.JSONDecodeError as exc:
            raise ToolFailure(
                error_code="DEPLOYMENT_COMMAND_FAILED",
                message="Ops diagnosis Docker command returned invalid JSON.",
                retryable=False,
                details={
                    "command": list(command),
                    "stdout": _tail_text(stdout, max_chars=2000),
                    "failureClass": failure_class,
                },
            ) from exc


@dataclass(slots=True)
class OpsStackDiagnosisExecutor:
    evidence_writer: OpsDiagnosisEvidenceWriter
    runner: OpsDiagnosisRunner

    async def execute(
        self,
        inputs: Mapping[str, Any],
        context: Mapping[str, Any] | None = None,
    ) -> ToolResult:
        context = dict(context or {})
        _require_remediation_policy(context)
        parsed = _parse_diagnosis_inputs(inputs)
        started_at = _utc_now()
        findings: list[dict[str, Any]] = []
        evidence: dict[str, Any] = {}
        artifact_ref: str | None = None

        for include in parsed["include"]:
            try:
                payload = await self.runner.collect(
                    stack=parsed["stack"],
                    include=include,
                    services=parsed["services"],
                    tail_lines=parsed["tailLines"],
                )
            except Exception as exc:
                findings.append(_finding_for_failure(include, exc))
                evidence[include] = {
                    "status": "FAILED",
                    "reason": _redact_sensitive(_failure_reason(exc)),
                }
                continue
            redacted = _redact_ops_diagnosis_payload(payload)
            evidence[include] = {"status": "SUCCEEDED", "payload": redacted}
            findings.extend(_findings_for_evidence(include, redacted))

        succeeded_count = sum(
            1 for item in evidence.values() if item.get("status") == "SUCCEEDED"
        )
        if succeeded_count == 0:
            status = "FAILED"
        elif succeeded_count == len(evidence):
            status = "SUCCEEDED"
        else:
            status = "PARTIALLY_VERIFIED"
        if status not in DEPLOYMENT_FINAL_STATUSES:
            status = "FAILED"

        diagnosis_payload = _redact_ops_diagnosis_payload(
            {
                "schemaVersion": "v1",
                "artifactType": OPS_DIAGNOSIS_ARTIFACT_TYPE,
                "toolName": OPS_DIAGNOSE_STACK_TOOL_NAME,
                "status": status,
                "stack": parsed["stack"],
                "include": list(parsed["include"]),
                "services": list(parsed["services"]),
                "tailLines": parsed["tailLines"],
                "targetWorkflowId": parsed.get("targetWorkflowId"),
                "remediationWorkflowId": parsed.get("remediationWorkflowId"),
                "reason": parsed["reason"],
                "startedAt": started_at,
                "completedAt": _utc_now(),
                "evidence": evidence,
                "findings": findings,
            }
        )
        artifact_ref = await self.evidence_writer.write("diagnosis", diagnosis_payload)
        summary = _summary(status=status, evidence=evidence, findings=findings)
        outputs = {
            "status": status,
            "stack": parsed["stack"],
            "summary": summary,
            "findings": _findings_with_ref(findings, artifact_ref),
            "artifactRefs": {"diagnosis": artifact_ref},
        }
        return ToolResult(
            status="COMPLETED" if status == "SUCCEEDED" else "FAILED",
            outputs=outputs,
            progress={
                "percent": 100,
                "state": status,
                "message": summary,
            },
        )


def build_ops_diagnose_stack_handler(
    executor: OpsStackDiagnosisExecutor | None = None,
):
    resolved_executor = executor or OpsStackDiagnosisExecutor(
        evidence_writer=InMemoryEvidenceWriter(),
        runner=DisabledOpsDiagnosisRunner(),
    )

    async def _handler(
        inputs: Mapping[str, Any], context: Mapping[str, Any] | None = None
    ) -> ToolResult:
        context = dict(context or {})
        context_executor = None
        candidate = context.get("ops_diagnosis_executor")
        if isinstance(candidate, OpsStackDiagnosisExecutor):
            context_executor = candidate
        active_executor = context_executor or resolved_executor
        artifact_service = context.get("temporal_artifact_service")
        if artifact_service is not None and context_executor is None:
            active_executor = replace(
                active_executor,
                evidence_writer=TemporalOpsDiagnosisEvidenceWriter(
                    artifact_service=artifact_service,
                    principal=str(
                        context.get("deployment_evidence_principal")
                        or "system:deployment"
                    ),
                    execution_ref=_execution_ref_from_context(context),
                ),
            )
        return await active_executor.execute(inputs, context)

    return _handler


def register_ops_diagnose_stack_tool_handler(
    dispatcher: Any,
    *,
    executor: OpsStackDiagnosisExecutor | None = None,
) -> None:
    dispatcher.register_skill(
        skill_name=OPS_DIAGNOSE_STACK_TOOL_NAME,
        handler=build_ops_diagnose_stack_handler(executor),
    )


def _parse_diagnosis_inputs(inputs: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(inputs, Mapping):
        raise ToolFailure(
            "INVALID_INPUT",
            "Ops diagnosis inputs must be an object.",
            False,
            details={"failureClass": "invalid_input"},
        )
    forbidden = {
        "command",
        "dockerCommand",
        "shell",
        "composeFile",
        "hostPath",
        "path",
        "exec",
    }
    found_forbidden = sorted(forbidden.intersection(inputs.keys()))
    if found_forbidden:
        raise ToolFailure(
            error_code="INVALID_INPUT",
            message="Ops diagnosis inputs contain forbidden fields.",
            retryable=False,
            details={"fields": found_forbidden, "failureClass": "invalid_input"},
        )
    stack = _required_string(inputs.get("stack"), "stack")
    if stack not in OPS_DIAGNOSIS_STACKS:
        raise ToolFailure(
            error_code="INVALID_INPUT",
            message=f"Unsupported ops diagnosis stack '{stack}'.",
            retryable=False,
            details={"stack": stack, "failureClass": "invalid_input"},
        )
    reason = _required_string(inputs.get("reason"), "reason")
    if len(reason) > 1000:
        raise ToolFailure(
            error_code="INVALID_INPUT",
            message="Ops diagnosis reason must be at most 1000 characters.",
            retryable=False,
            details={"failureClass": "invalid_input"},
        )
    include = _parse_include(inputs.get("include"))
    services = _parse_services(inputs.get("services"))
    tail_lines = _parse_tail_lines(inputs.get("tailLines"))
    return {
        "stack": stack,
        "reason": reason,
        "include": include,
        "services": services,
        "tailLines": tail_lines,
        "targetWorkflowId": _optional_string(inputs.get("targetWorkflowId")),
        "remediationWorkflowId": _optional_string(inputs.get("remediationWorkflowId")),
    }


def _require_remediation_policy(context: Mapping[str, Any]) -> None:
    remediation = context.get("remediation")
    remediation_policy = context.get("remediation_policy") or context.get(
        "remediationPolicy"
    )
    is_remediation = bool(context.get("is_remediation_workflow")) or isinstance(
        remediation, Mapping
    )
    policy_allows = bool(context.get("remediation_policy_allows_ops_diagnostics"))
    if isinstance(remediation_policy, Mapping):
        policy_allows = policy_allows or bool(
            remediation_policy.get("allowOpsDiagnostics")
            or remediation_policy.get("opsDiagnosticsAllowed")
        )
    if not is_remediation:
        raise ToolFailure(
            error_code="PERMISSION_DENIED",
            message="Ops diagnosis is only available to remediation workflows.",
            retryable=False,
            details={"failureClass": "permission_denied"},
        )
    if not policy_allows:
        raise ToolFailure(
            error_code="PERMISSION_DENIED",
            message="Remediation policy does not allow ops diagnostics.",
            retryable=False,
            details={"failureClass": "permission_denied"},
        )


def _parse_include(value: Any) -> tuple[str, ...]:
    if value is None:
        return DEFAULT_OPS_DIAGNOSIS_INCLUDES
    if not isinstance(value, list | tuple):
        raise ToolFailure(
            "INVALID_INPUT",
            "Ops diagnosis include must be an array.",
            False,
            details={"failureClass": "invalid_input"},
        )
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        include = _required_string(item, "include")
        if include not in OPS_DIAGNOSIS_INCLUDES:
            raise ToolFailure(
                error_code="INVALID_INPUT",
                message=f"Unsupported ops diagnosis include '{include}'.",
                retryable=False,
                details={"include": include, "failureClass": "invalid_input"},
            )
        if include not in seen:
            normalized.append(include)
            seen.add(include)
    return tuple(normalized or DEFAULT_OPS_DIAGNOSIS_INCLUDES)


def _parse_services(value: Any) -> tuple[str, ...]:
    if value is None or value == []:
        return tuple(sorted(DEFAULT_MOONMIND_SERVICES))
    if not isinstance(value, list | tuple):
        raise ToolFailure(
            "INVALID_INPUT",
            "Ops diagnosis services must be an array.",
            False,
            details={"failureClass": "invalid_input"},
        )
    services: list[str] = []
    seen: set[str] = set()
    for item in value:
        service = _required_string(item, "services[]")
        if service not in DEFAULT_MOONMIND_SERVICES:
            raise ToolFailure(
                error_code="INVALID_INPUT",
                message=f"Unsupported MoonMind service '{service}'.",
                retryable=False,
                details={"service": service, "failureClass": "invalid_input"},
            )
        if service not in seen:
            services.append(service)
            seen.add(service)
    return tuple(services)


def _parse_tail_lines(value: Any) -> int:
    if value is None:
        return OPS_DIAGNOSIS_TAIL_LINES_DEFAULT
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolFailure(
            "INVALID_INPUT",
            "Ops diagnosis tailLines must be an integer.",
            False,
            details={"failureClass": "invalid_input"},
        )
    if value < OPS_DIAGNOSIS_TAIL_LINES_MIN or value > OPS_DIAGNOSIS_TAIL_LINES_MAX:
        raise ToolFailure(
            error_code="INVALID_INPUT",
            message=(
                "Ops diagnosis tailLines must be between "
                f"{OPS_DIAGNOSIS_TAIL_LINES_MIN} and "
                f"{OPS_DIAGNOSIS_TAIL_LINES_MAX}."
            ),
            retryable=False,
            details={"tailLines": value, "failureClass": "invalid_input"},
        )
    return value


def _required_string(value: Any, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ToolFailure(
            "INVALID_INPUT",
            f"Ops diagnosis {field_name} is required.",
            False,
            details={"failureClass": "invalid_input"},
        )
    return normalized


def _optional_string(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _failure_reason(exc: Exception) -> str:
    if isinstance(exc, ToolFailure):
        return exc.message
    return str(exc) or exc.__class__.__name__


def _redact_ops_diagnosis_payload(payload: Any) -> Any:
    return redact_sensitive_payload(_redact_sensitive(payload))


def _finding_for_failure(include: str, exc: Exception) -> dict[str, Any]:
    return {
        "kind": include,
        "severity": "error",
        "message": _redact_sensitive(_failure_reason(exc)),
    }


def _findings_for_evidence(include: str, payload: Any) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if include == "container_health" and isinstance(payload, Mapping):
        for item in payload.get("containers") or []:
            if not isinstance(item, Mapping):
                continue
            state = str(item.get("state") or "").lower()
            health = str(item.get("health") or "").lower()
            service = str(item.get("service") or "").strip() or None
            if state and "running" not in state:
                findings.append(
                    {
                        "kind": "container_health",
                        "severity": "error",
                        "message": f"Service {service or 'unknown'} is not running.",
                        "service": service,
                    }
                )
            elif health and health not in {"healthy", "none"}:
                findings.append(
                    {
                        "kind": "container_health",
                        "severity": "warning",
                        "message": (
                            f"Service {service or 'unknown'} health is {health}."
                        ),
                        "service": service,
                    }
                )
    return findings


def _findings_with_ref(
    findings: list[dict[str, Any]], artifact_ref: str
) -> list[dict[str, Any]]:
    return [
        {**_compact_mapping(finding), "evidenceRef": artifact_ref}
        for finding in findings
    ]


def _summary(
    *,
    status: str,
    evidence: Mapping[str, Mapping[str, Any]],
    findings: Sequence[Mapping[str, Any]],
) -> str:
    succeeded = sum(1 for item in evidence.values() if item.get("status") == "SUCCEEDED")
    failed = sum(1 for item in evidence.values() if item.get("status") == "FAILED")
    errors = sum(1 for item in findings if item.get("severity") == "error")
    warnings = sum(1 for item in findings if item.get("severity") == "warning")
    return (
        f"Ops diagnosis {status}: {succeeded} evidence class(es) collected, "
        f"{failed} failed, {errors} error finding(s), {warnings} warning finding(s)."
    )


__all__ = [
    "DEFAULT_OPS_DIAGNOSIS_INCLUDES",
    "OPS_DIAGNOSIS_ARTIFACT_TYPE",
    "OPS_DIAGNOSIS_INCLUDES",
    "OPS_DIAGNOSIS_STACKS",
    "OpsStackDiagnosisExecutor",
    "DisabledOpsDiagnosisRunner",
    "HostDockerComposeOpsDiagnosisRunner",
    "TemporalOpsDiagnosisEvidenceWriter",
    "build_ops_diagnose_stack_handler",
    "register_ops_diagnose_stack_tool_handler",
]
