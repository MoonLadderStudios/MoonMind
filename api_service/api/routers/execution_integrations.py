"""Integration monitoring callback router."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.api.routers.executions import _serialize_execution
from api_service.db.base import get_async_session
from api_service.db.models import TemporalArtifactRedactionLevel
from moonmind.config.settings import settings
from moonmind.schemas.temporal_models import ExecutionModel, IntegrationCallbackRequest
from moonmind.workflows.temporal import (
    ExecutionRef,
    TemporalArtifactRepository,
    TemporalArtifactService,
    TemporalExecutionNotFoundError,
    TemporalExecutionService,
    TemporalExecutionValidationError,
)

router = APIRouter(prefix="/api/integrations", tags=["integrations"])
_DEFAULT_CALLBACK_MAX_PAYLOAD_BYTES = 64 * 1024
_DEFAULT_CALLBACK_RATE_LIMIT = 30
_DEFAULT_CALLBACK_RATE_LIMIT_WINDOW_SECONDS = 60


@dataclass(slots=True)
class _CallbackRateLimitBucket:
    timestamps: deque[float]
    window_seconds: int


@dataclass(frozen=True, slots=True)
class _CallbackProfile:
    normalized_name: str
    expected_token: str | None
    max_payload_bytes: int
    rate_limit_per_window: int
    rate_limit_window_seconds: int
    capture_artifacts: bool


class _CallbackRateLimiter:
    """Simple in-process callback limiter for burst protection."""

    def __init__(self) -> None:
        self._buckets: dict[str, _CallbackRateLimitBucket] = {}

    def _prune_bucket(
        self, bucket: _CallbackRateLimitBucket, *, now: float
    ) -> deque[float]:
        window_floor = now - bucket.window_seconds
        while bucket.timestamps and bucket.timestamps[0] < window_floor:
            bucket.timestamps.popleft()
        return bucket.timestamps

    def _evict_idle_buckets(self, *, now: float) -> None:
        for key, bucket in list(self._buckets.items()):
            if self._prune_bucket(bucket, now=now):
                continue
            self._buckets.pop(key, None)

    def allow(self, *, key: str, limit: int, window_seconds: int) -> bool:
        now = time.monotonic()
        self._evict_idle_buckets(now=now)
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = _CallbackRateLimitBucket(
                timestamps=deque(),
                window_seconds=window_seconds,
            )
            self._buckets[key] = bucket
        else:
            bucket.window_seconds = window_seconds
        timestamps = self._prune_bucket(bucket, now=now)
        if len(timestamps) >= limit:
            return False
        timestamps.append(now)
        return True


_callback_rate_limiter = _CallbackRateLimiter()


def _bearer_token(authorization_header: str | None) -> str | None:
    raw = str(authorization_header or "").strip()
    if not raw:
        return None
    scheme, _, token = raw.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def _callback_profile(integration_name: str) -> _CallbackProfile:
    normalized = str(integration_name or "").strip().lower()
    if normalized == "jules":
        token = str(settings.jules.jules_callback_token or "").strip() or None
        return _CallbackProfile(
            normalized_name=normalized,
            expected_token=token,
            max_payload_bytes=int(settings.jules.jules_callback_max_payload_bytes),
            rate_limit_per_window=int(
                settings.jules.jules_callback_rate_limit_per_window
            ),
            rate_limit_window_seconds=int(
                settings.jules.jules_callback_rate_limit_window_seconds
            ),
            capture_artifacts=bool(
                settings.jules.jules_callback_artifact_capture_enabled
            ),
        )
    return _CallbackProfile(
        normalized_name=normalized,
        expected_token=None,
        max_payload_bytes=_DEFAULT_CALLBACK_MAX_PAYLOAD_BYTES,
        rate_limit_per_window=_DEFAULT_CALLBACK_RATE_LIMIT,
        rate_limit_window_seconds=_DEFAULT_CALLBACK_RATE_LIMIT_WINDOW_SECONDS,
        capture_artifacts=False,
    )


async def _validate_callback_request(
    *,
    request: Request,
    integration_name: str,
    integration_token_header: str | None,
    authorization_header: str | None,
) -> bytes:
    profile = _callback_profile(integration_name)
    expected_token = profile.expected_token
    presented_token = integration_token_header or _bearer_token(authorization_header)
    if expected_token and presented_token != expected_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "integration_callback_unauthorized",
                "message": f"Callback authorization failed for '{integration_name}'.",
            },
        )

    payload_bytes = await request.body()
    max_payload_bytes = profile.max_payload_bytes
    if len(payload_bytes) > max_payload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail={
                "code": "integration_callback_payload_too_large",
                "message": (
                    f"Callback payload exceeded {max_payload_bytes} bytes for "
                    f"'{integration_name}'."
                ),
            },
        )
    limit = profile.rate_limit_per_window
    window_seconds = profile.rate_limit_window_seconds
    bucket_key = f"{profile.normalized_name}:callbacks"
    if not _callback_rate_limiter.allow(
        key=bucket_key,
        limit=limit,
        window_seconds=window_seconds,
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "integration_callback_rate_limited",
                "message": (
                    f"Callback rate limit exceeded for '{integration_name}'. "
                    "Retry later."
                ),
            },
        )
    return payload_bytes


async def _get_service(
    session: AsyncSession = Depends(get_async_session),
) -> TemporalExecutionService:
    return TemporalExecutionService(
        session,
        namespace=settings.temporal.namespace,
        integration_task_queue=settings.temporal.activity_integrations_task_queue,
        integration_poll_initial_seconds=(
            settings.temporal.integration_poll_initial_seconds
        ),
        integration_poll_max_seconds=settings.temporal.integration_poll_max_seconds,
        integration_poll_jitter_ratio=settings.temporal.integration_poll_jitter_ratio,
        run_continue_as_new_step_threshold=(
            settings.temporal.run_continue_as_new_step_threshold
        ),
        run_continue_as_new_wait_cycle_threshold=(
            settings.temporal.run_continue_as_new_wait_cycle_threshold
        ),
        manifest_continue_as_new_phase_threshold=(
            settings.temporal.manifest_continue_as_new_phase_threshold
        ),
    )


@router.post(
    "/{integration_name}/callbacks/{callback_correlation_key}",
    response_model=ExecutionModel,
    status_code=status.HTTP_202_ACCEPTED,
)
async def ingest_integration_callback(
    request: Request,
    integration_name: str,
    callback_correlation_key: str,
    payload: IntegrationCallbackRequest,
    session: AsyncSession = Depends(get_async_session),
    integration_token_header: str | None = Header(
        None, alias="X-MoonMind-Integration-Token"
    ),
    authorization_header: str | None = Header(None, alias="Authorization"),
    service: TemporalExecutionService = Depends(_get_service),
) -> ExecutionModel:
    payload_bytes = await _validate_callback_request(
        request=request,
        integration_name=integration_name,
        integration_token_header=integration_token_header,
        authorization_header=authorization_header,
    )

    try:
        _correlation, target_record = await service.resolve_integration_callback_target(
            integration_name=integration_name,
            callback_correlation_key=callback_correlation_key,
        )
        payload_artifact_ref = payload.payload_artifact_ref
        if (
            payload_artifact_ref is None
            and _callback_profile(integration_name).capture_artifacts
        ):
            artifact_service = TemporalArtifactService(
                TemporalArtifactRepository(session)
            )
            event_type = str(payload.event_type).strip()
            artifact_ref = await artifact_service.write_integration_event_artifact(
                principal="service:integration-callback",
                execution=ExecutionRef(
                    namespace=target_record.namespace,
                    workflow_id=target_record.workflow_id,
                    run_id=target_record.run_id,
                ),
                integration_name=str(integration_name).strip().lower(),
                correlation_id=(target_record.integration_state or {}).get(
                    "correlation_id", callback_correlation_key
                ),
                payload=payload_bytes,
                content_type="application/json",
                event_type=event_type,
                metadata_json={
                    "callback_correlation_key": callback_correlation_key,
                },
                redaction_level=TemporalArtifactRedactionLevel.RESTRICTED,
            )
            payload_artifact_ref = artifact_ref.artifact_id

        record = await service.ingest_integration_callback(
            integration_name=integration_name,
            callback_correlation_key=callback_correlation_key,
            payload=payload.model_dump(
                by_alias=False,
                exclude_none=True,
                exclude={"payload_artifact_ref"},
            ),
            payload_artifact_ref=payload_artifact_ref,
        )
    except TemporalExecutionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "integration_callback_not_found",
                "message": str(exc),
            },
        ) from exc
    except TemporalExecutionValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "integration_callback_rejected",
                "message": str(exc),
            },
        ) from exc

    return _serialize_execution(record)


__all__ = ["router"]
