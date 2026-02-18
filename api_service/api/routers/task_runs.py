"""REST router for live task-run handoff controls."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.api.routers.agent_queue import (
    _WorkerRequestAuth,
    _require_worker_auth,
    _to_http_exception,
)
from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from api_service.db.models import User
from moonmind.schemas.agent_queue_models import (
    GrantTaskRunLiveSessionWriteRequest,
    JobModel,
    RevokeTaskRunLiveSessionRequest,
    TaskRunControlEventModel,
    TaskRunControlRequest,
    TaskRunLiveSessionModel,
    TaskRunLiveSessionResponse,
    TaskRunLiveSessionWriteGrantResponse,
    TaskRunOperatorMessageRequest,
    WorkerReportTaskRunLiveSessionRequest,
)
from moonmind.workflows import get_agent_queue_repository
from moonmind.workflows.agent_queue.service import AgentQueueService

router = APIRouter(prefix="/api/task-runs", tags=["task-runs"])


async def _get_service(
    session: AsyncSession = Depends(get_async_session),
) -> AgentQueueService:
    return AgentQueueService(get_agent_queue_repository(session))


@router.post(
    "/{task_run_id}/live-session",
    response_model=TaskRunLiveSessionResponse,
)
async def create_live_session(
    *,
    task_run_id: UUID,
    service: AgentQueueService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> TaskRunLiveSessionResponse:
    """Idempotently create/enable live task-run session tracking."""

    try:
        live = await service.create_live_session(
            task_run_id=task_run_id,
            actor_user_id=getattr(user, "id", None),
        )
    except Exception as exc:  # pragma: no cover - thin mapping layer
        raise _to_http_exception(exc) from exc
    return TaskRunLiveSessionResponse(
        session=TaskRunLiveSessionModel.model_validate(live),
    )


@router.get(
    "/{task_run_id}/live-session",
    response_model=TaskRunLiveSessionResponse,
)
async def get_live_session(
    *,
    task_run_id: UUID,
    service: AgentQueueService = Depends(_get_service),
    _user: User = Depends(get_current_user()),
) -> TaskRunLiveSessionResponse:
    """Fetch current live session state and RO attach metadata."""

    try:
        live = await service.get_live_session(task_run_id=task_run_id)
    except Exception as exc:  # pragma: no cover - thin mapping layer
        raise _to_http_exception(exc) from exc

    if live is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "live_session_not_found",
                "message": "Live session is not enabled for this task run.",
            },
        )
    return TaskRunLiveSessionResponse(
        session=TaskRunLiveSessionModel.model_validate(live),
    )


@router.post(
    "/{task_run_id}/live-session/grant-write",
    response_model=TaskRunLiveSessionWriteGrantResponse,
)
async def grant_live_session_write(
    *,
    task_run_id: UUID,
    payload: GrantTaskRunLiveSessionWriteRequest = Body(
        default_factory=GrantTaskRunLiveSessionWriteRequest
    ),
    service: AgentQueueService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> TaskRunLiveSessionWriteGrantResponse:
    """Return a temporary RW attach grant and audit the reveal."""

    try:
        grant = await service.grant_live_session_write(
            task_run_id=task_run_id,
            actor_user_id=getattr(user, "id", None),
            ttl_minutes=payload.ttl_minutes,
        )
    except Exception as exc:  # pragma: no cover - thin mapping layer
        raise _to_http_exception(exc) from exc
    return TaskRunLiveSessionWriteGrantResponse(
        session=TaskRunLiveSessionModel.model_validate(grant.session),
        attach_rw=grant.attach_rw,
        web_rw=grant.web_rw,
        granted_until=grant.granted_until,
    )


@router.post(
    "/{task_run_id}/live-session/revoke",
    response_model=TaskRunLiveSessionResponse,
)
async def revoke_live_session(
    *,
    task_run_id: UUID,
    payload: RevokeTaskRunLiveSessionRequest = Body(
        default_factory=RevokeTaskRunLiveSessionRequest
    ),
    service: AgentQueueService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> TaskRunLiveSessionResponse:
    """Force-revoke one live task-run session."""

    try:
        live = await service.revoke_live_session(
            task_run_id=task_run_id,
            actor_user_id=getattr(user, "id", None),
            reason=payload.reason,
        )
    except Exception as exc:  # pragma: no cover - thin mapping layer
        raise _to_http_exception(exc) from exc
    return TaskRunLiveSessionResponse(
        session=TaskRunLiveSessionModel.model_validate(live),
    )


@router.post("/{task_run_id}/control", response_model=JobModel)
async def apply_control_action(
    *,
    task_run_id: UUID,
    payload: TaskRunControlRequest,
    service: AgentQueueService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> JobModel:
    """Apply pause/resume/takeover controls to a task run."""

    try:
        job = await service.apply_control_action(
            task_run_id=task_run_id,
            actor_user_id=getattr(user, "id", None),
            action=payload.action,
        )
    except Exception as exc:  # pragma: no cover - thin mapping layer
        raise _to_http_exception(exc) from exc
    return JobModel.model_validate(job)


@router.post(
    "/{task_run_id}/operator-messages",
    response_model=TaskRunControlEventModel,
    status_code=status.HTTP_201_CREATED,
)
async def append_operator_message(
    *,
    task_run_id: UUID,
    payload: TaskRunOperatorMessageRequest,
    service: AgentQueueService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> TaskRunControlEventModel:
    """Persist an operator message and append it to run event stream."""

    try:
        event = await service.append_operator_message(
            task_run_id=task_run_id,
            actor_user_id=getattr(user, "id", None),
            message=payload.message,
        )
    except Exception as exc:  # pragma: no cover - thin mapping layer
        raise _to_http_exception(exc) from exc
    return TaskRunControlEventModel.model_validate(event)


@router.post(
    "/{task_run_id}/live-session/report",
    response_model=TaskRunLiveSessionResponse,
)
async def report_live_session(
    *,
    task_run_id: UUID,
    payload: WorkerReportTaskRunLiveSessionRequest,
    auth: _WorkerRequestAuth = Depends(_require_worker_auth),
    service: AgentQueueService = Depends(_get_service),
) -> TaskRunLiveSessionResponse:
    """Worker-authenticated live-session report/update hook."""

    if (
        auth.auth_source == "worker_token"
        and auth.worker_id
        and payload.worker_id != auth.worker_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "worker_not_authorized",
                "message": "Worker is not authorized for this action.",
            },
        )

    try:
        live = await service.report_live_session(
            task_run_id=task_run_id,
            worker_id=payload.worker_id,
            worker_hostname=payload.worker_hostname,
            status=payload.status,
            provider=payload.provider,
            attach_ro=payload.attach_ro,
            attach_rw=payload.attach_rw,
            web_ro=payload.web_ro,
            web_rw=payload.web_rw,
            tmate_session_name=payload.tmate_session_name,
            tmate_socket_path=payload.tmate_socket_path,
            expires_at=payload.expires_at,
            error_message=payload.error_message,
        )
    except Exception as exc:  # pragma: no cover - thin mapping layer
        raise _to_http_exception(exc) from exc
    return TaskRunLiveSessionResponse(
        session=TaskRunLiveSessionModel.model_validate(live),
    )


@router.post(
    "/{task_run_id}/live-session/heartbeat",
    response_model=TaskRunLiveSessionResponse,
)
async def heartbeat_live_session(
    *,
    task_run_id: UUID,
    worker_id: str = Body(..., embed=True, alias="workerId"),
    auth: _WorkerRequestAuth = Depends(_require_worker_auth),
    service: AgentQueueService = Depends(_get_service),
) -> TaskRunLiveSessionResponse:
    """Worker-authenticated heartbeat updater for live sessions."""

    if auth.auth_source == "worker_token" and auth.worker_id and worker_id != auth.worker_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "worker_not_authorized",
                "message": "Worker is not authorized for this action.",
            },
        )
    try:
        live = await service.heartbeat_live_session(
            task_run_id=task_run_id,
            worker_id=worker_id,
        )
    except Exception as exc:  # pragma: no cover - thin mapping layer
        raise _to_http_exception(exc) from exc
    return TaskRunLiveSessionResponse(
        session=TaskRunLiveSessionModel.model_validate(live),
    )
