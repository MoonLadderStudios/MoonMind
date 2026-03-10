from typing import Any
from fastapi import HTTPException, status
from pydantic import ValidationError

from api_service.db.models import User
from moonmind.schemas.agent_queue_models import CreateJobRequest
from moonmind.workflows.temporal.service import TemporalExecutionService
from moonmind.schemas.temporal_models import ExecutionModel
from api_service.db.models import TemporalExecutionRecord

def _invalid_task_request(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={
            "code": "invalid_execution_request",
            "message": message,
        },
    )

def _coerce_string_list(val: Any, *, field_name: str) -> list[str]:
    if val is None:
        return []
    if isinstance(val, str):
        return [val]
    if isinstance(val, list) and all(isinstance(i, str) for i in val):
        return val
    raise _invalid_task_request(f"{field_name} must be a JSON array of strings.")

def _coerce_step_count(val: Any) -> int | None:
    if val is None:
        return None
    try:
        parsed = int(val)
        if parsed > 0:
            return parsed
        return None
    except (ValueError, TypeError):
        return None

def _coerce_artifact_ref(val: Any) -> str | None:
    if not val:
        return None
    parsed = str(val).strip()
    return parsed if parsed else None

_MAX_TASK_TITLE_LENGTH = 100
_MAX_TASK_SUMMARY_LENGTH = 500
_TASK_SUMMARY_ELLIPSIS = "..."

def _derive_task_title(task_payload: dict[str, Any]) -> str | None:
    title = str(task_payload.get("title") or "").strip()
    if title:
        return title[:_MAX_TASK_TITLE_LENGTH]
    instructions = str(task_payload.get("instructions") or "").strip()
    if not instructions:
        return None
    first_line = instructions.splitlines()[0].strip()
    if not first_line:
        return None
    return first_line[:_MAX_TASK_TITLE_LENGTH]

def _derive_task_summary(
    task_payload: dict[str, Any], input_artifact_ref: str | None
) -> str:
    instructions = str(task_payload.get("instructions") or "").strip()
    if instructions:
        normalized = " ".join(instructions.split())
        if len(normalized) > _MAX_TASK_SUMMARY_LENGTH:
            preview_length = _MAX_TASK_SUMMARY_LENGTH - len(_TASK_SUMMARY_ELLIPSIS)
            return f"{normalized[:preview_length]}{_TASK_SUMMARY_ELLIPSIS}"
        return normalized
    if input_artifact_ref:
        return f"Task instructions stored in artifact {input_artifact_ref}."
    return "Execution initialized."

async def create_execution_from_task_request(
    *,
    request: CreateJobRequest,
    service: TemporalExecutionService,
    user: User,
) -> TemporalExecutionRecord:
    if str(request.type).strip().lower() != "task":
        raise _invalid_task_request(
            "Only task-shaped submit requests can be mapped to Temporal executions."
        )

    payload = request.payload if isinstance(request.payload, dict) else {}
    task_payload = payload.get("task") if isinstance(payload.get("task"), dict) else {}
    if not task_payload:
        raise _invalid_task_request(
            "Task-shaped Temporal submit requests require payload.task."
        )

    required_capabilities = _coerce_string_list(
        payload.get("requiredCapabilities"),
        field_name="payload.requiredCapabilities",
    )
    step_count = _coerce_step_count(task_payload.get("steps"))

    repository = str(payload.get("repository") or "").strip() or None
    integration = (
        str(
            payload.get("integration")
            or (payload.get("metadata") or {}).get("integration")
            or ""
        ).strip()
        or None
    )
    input_artifact_ref = _coerce_artifact_ref(
        task_payload.get("inputArtifactRef") or payload.get("inputArtifactRef")
    )
    plan_artifact_ref = _coerce_artifact_ref(
        task_payload.get("planArtifactRef") or payload.get("planArtifactRef")
    )
    manifest_artifact_ref = _coerce_artifact_ref(
        task_payload.get("manifestArtifactRef") or payload.get("manifestArtifactRef")
    )
    runtime_payload = (
        task_payload.get("runtime")
        if isinstance(task_payload.get("runtime"), dict)
        else {}
    )
    initial_parameters = {
        "requestType": request.type,
        "repository": repository,
        "requiredCapabilities": required_capabilities,
        "affinityKey": request.affinity_key,
        "priority": request.priority,
        "task": {
            "title": _derive_task_title(task_payload),
            "summary": _derive_task_summary(task_payload, input_artifact_ref),
            "instructions": str(task_payload.get("instructions") or "").strip()
            or None,
            "stepCount": step_count,
            "runtime": runtime_payload,
        },
        "metadata": {
            **(payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}),
            "integration": integration,
            "originalQueuePayload": payload,
        },
    }

    record = await service.create_execution(
        workflow_type="MoonMind.Run",
        owner_id=user.id,
        owner_type="user",
        title=_derive_task_title(task_payload) or "Untitled task",
        input_artifact_ref=input_artifact_ref,
        plan_artifact_ref=plan_artifact_ref,
        manifest_artifact_ref=manifest_artifact_ref,
        failure_policy=None,
        initial_parameters=initial_parameters,
        idempotency_key=request.affinity_key,
    )
    return record
