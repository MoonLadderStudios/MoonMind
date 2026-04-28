"""System operation command services for Settings -> Operations."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.api.schemas import (
    QueueSystemMetadataModel,
    WorkerPauseAuditEventModel,
    WorkerPauseAuditListModel,
    WorkerPauseMetricsModel,
    WorkerPauseSnapshotResponse,
)
from api_service.db.models import SettingsAuditEvent, SettingsOverride


_DEFAULT_SUBJECT_ID = UUID("00000000-0000-0000-0000-000000000000")
_WORKER_STATE_KEY = "operations.workers.pause_state"
_WORKER_AUDIT_KEY = "operations.workers"


class SystemOperationValidationError(ValueError):
    """Raised when a system operation command is invalid."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class SystemOperationUnavailableError(RuntimeError):
    """Raised when the operational subsystem cannot process a command."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class WorkerOperationCommand(BaseModel):
    """Typed worker pause/resume command accepted by the system operations API."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    action: str
    mode: str | None = None
    reason: str | None = None
    confirmation: str | None = None
    force_resume: bool = Field(False, alias="forceResume")


class _QueueSystemMetadata:
    def __init__(
        self,
        *,
        workers_paused: bool,
        mode: str | None,
        reason: str | None,
        version: int,
        requested_by_user_id: UUID | None,
        requested_at: datetime | None,
        updated_at: datetime | None,
    ) -> None:
        self.workers_paused = workers_paused
        self.mode = mode
        self.reason = reason
        self.version = version
        self.requested_by_user_id = requested_by_user_id
        self.requested_at = requested_at
        self.updated_at = updated_at


class SystemOperationsService:
    """Validate and apply Settings-visible system operation commands."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        temporal_service: object | None = None,
        now: datetime | None = None,
    ) -> None:
        self._session = session
        self._temporal_service = temporal_service
        self._now = now

    async def snapshot(
        self,
        *,
        signal_status: str | None = None,
        failure_reason: str | None = None,
    ) -> WorkerPauseSnapshotResponse:
        state = await self._load_state()
        audit = await self._latest_audit()
        return WorkerPauseSnapshotResponse(
            system=QueueSystemMetadataModel.from_service_metadata(state),
            metrics=WorkerPauseMetricsModel(
                queued=0,
                running=0,
                staleRunning=0,
                isDrained=True,
                metricsSource="temporal",
            ),
            audit=WorkerPauseAuditListModel(latest=audit),
            signalStatus=failure_reason or signal_status,
        )

    async def submit(
        self,
        command: WorkerOperationCommand,
        *,
        actor_user_id: UUID | str | None,
    ) -> WorkerPauseSnapshotResponse:
        normalized = self._validate_command(command)
        signal_status = await self._invoke_subsystem(normalized)
        state = await self._persist_state(normalized, actor_user_id=actor_user_id)
        await self._persist_audit(
            normalized,
            actor_user_id=actor_user_id,
            status="succeeded",
            signal_status=signal_status,
            state=state,
        )
        await self._session.commit()
        return await self.snapshot(signal_status="succeeded")

    def _validate_command(self, command: WorkerOperationCommand) -> WorkerOperationCommand:
        action = str(command.action or "").strip().lower()
        reason = str(command.reason or "").strip()
        mode = str(command.mode or "").strip().lower() or None
        confirmation = str(command.confirmation or "").strip()
        if action not in {"pause", "resume"}:
            raise SystemOperationValidationError(
                "worker_operation_invalid",
                "Worker operation action is invalid.",
            )
        if not reason:
            raise SystemOperationValidationError(
                "worker_operation_reason_required",
                "Worker operation reason is required.",
            )
        if action == "pause":
            if mode not in {"drain", "quiesce"}:
                raise SystemOperationValidationError(
                    "worker_operation_invalid",
                    "Worker pause mode is invalid.",
                )
            if not confirmation:
                raise SystemOperationValidationError(
                    "worker_operation_confirmation_required",
                    "Worker pause confirmation is required.",
                )
        if action == "resume":
            if mode is not None:
                raise SystemOperationValidationError(
                    "worker_operation_invalid",
                    "Worker resume does not accept a pause mode.",
                )
            if command.force_resume and not confirmation:
                raise SystemOperationValidationError(
                    "worker_operation_confirmation_required",
                    "Forced worker resume confirmation is required.",
                )
        return WorkerOperationCommand(
            action=action,
            mode=mode,
            reason=reason,
            confirmation=confirmation or None,
            force_resume=bool(command.force_resume),
        )

    async def _invoke_subsystem(self, command: WorkerOperationCommand) -> str:
        try:
            if command.action == "pause" and command.mode == "quiesce":
                sender = getattr(self._temporal_service, "send_quiesce_pause_signal", None)
                if callable(sender):
                    count = await sender()
                    return f"succeeded:{count}"
            if command.action == "resume":
                sender = getattr(
                    self._temporal_service,
                    "send_resume_signal",
                    None,
                ) or getattr(
                    self._temporal_service,
                    "send_quiesce_resume_signal",
                    None,
                )
                if callable(sender):
                    count = await sender()
                    return f"succeeded:{count}"
        except Exception as exc:  # pragma: no cover - defensive sanitization
            raise SystemOperationUnavailableError(
                "worker_operation_unavailable",
                "Worker operation subsystem is unavailable.",
            ) from exc
        return "succeeded"

    async def _persist_state(
        self,
        command: WorkerOperationCommand,
        *,
        actor_user_id: UUID | str | None,
    ) -> _QueueSystemMetadata:
        now = self._timestamp()
        current = await self._state_row()
        current_payload = (
            dict(current.value_json)
            if current is not None and isinstance(current.value_json, dict)
            else {}
        )
        next_version = int(current_payload.get("version") or 0) + 1
        actor_uuid = self._uuid_or_none(actor_user_id)
        payload: dict[str, Any] = {
            "workersPaused": command.action == "pause",
            "mode": command.mode if command.action == "pause" else None,
            "reason": command.reason,
            "version": next_version,
            "requestedByUserId": str(actor_uuid) if actor_uuid else None,
            "requestedAt": now.isoformat(),
            "updatedAt": now.isoformat(),
        }
        if current is None:
            self._session.add(
                SettingsOverride(
                    scope="workspace",
                    workspace_id=_DEFAULT_SUBJECT_ID,
                    user_id=_DEFAULT_SUBJECT_ID,
                    key=_WORKER_STATE_KEY,
                    value_json=payload,
                    schema_version=1,
                    value_version=next_version,
                    created_by=actor_uuid,
                    updated_by=actor_uuid,
                )
            )
        else:
            current.value_json = payload
            current.value_version = next_version
            current.updated_by = actor_uuid
        return self._metadata_from_payload(payload)

    async def _persist_audit(
        self,
        command: WorkerOperationCommand,
        *,
        actor_user_id: UUID | str | None,
        status: str,
        signal_status: str,
        state: _QueueSystemMetadata,
    ) -> None:
        actor_uuid = self._uuid_or_none(actor_user_id)
        self._session.add(
            SettingsAuditEvent(
                event_type="operation_invoked",
                key=_WORKER_AUDIT_KEY,
                scope="system",
                workspace_id=_DEFAULT_SUBJECT_ID,
                user_id=_DEFAULT_SUBJECT_ID,
                actor_user_id=actor_uuid,
                old_value_json=None,
                new_value_json={
                    "action": command.action,
                    "target": "workers",
                    "mode": command.mode,
                    "status": status,
                    "signalStatus": signal_status,
                    "requestedState": "paused" if state.workers_paused else "running",
                    "idempotencyKey": self._idempotency_key(command, actor_uuid),
                },
                redacted=False,
                reason=command.reason,
            )
        )

    async def _load_state(self) -> _QueueSystemMetadata:
        row = await self._state_row()
        if row is None or not isinstance(row.value_json, dict):
            now = self._timestamp()
            return _QueueSystemMetadata(
                workers_paused=False,
                mode=None,
                reason="Normal operation",
                version=1,
                requested_by_user_id=None,
                requested_at=None,
                updated_at=now,
            )
        return self._metadata_from_payload(row.value_json)

    async def _state_row(self) -> SettingsOverride | None:
        result = await self._session.execute(
            select(SettingsOverride).where(
                SettingsOverride.scope == "workspace",
                SettingsOverride.workspace_id == _DEFAULT_SUBJECT_ID,
                SettingsOverride.user_id == _DEFAULT_SUBJECT_ID,
                SettingsOverride.key == _WORKER_STATE_KEY,
            )
        )
        return result.scalar_one_or_none()

    async def _latest_audit(self) -> list[WorkerPauseAuditEventModel]:
        result = await self._session.execute(
            select(SettingsAuditEvent)
            .where(SettingsAuditEvent.key == _WORKER_AUDIT_KEY)
            .order_by(desc(SettingsAuditEvent.created_at))
            .limit(10)
        )
        events: list[WorkerPauseAuditEventModel] = []
        for row in result.scalars():
            payload = row.new_value_json if isinstance(row.new_value_json, dict) else {}
            action = str(payload.get("action") or "").strip().lower()
            if action not in {"pause", "resume"}:
                continue
            mode = str(payload.get("mode") or "").strip().lower() or None
            if mode not in {"drain", "quiesce", None}:
                mode = None
            events.append(
                WorkerPauseAuditEventModel(
                    id=row.id,
                    action=action,
                    mode=mode,
                    reason=row.reason,
                    actorUserId=row.actor_user_id,
                    createdAt=row.created_at,
                )
            )
        return events

    def _metadata_from_payload(self, payload: dict[str, Any]) -> _QueueSystemMetadata:
        return _QueueSystemMetadata(
            workers_paused=bool(payload.get("workersPaused")),
            mode=self._mode_or_none(payload.get("mode")),
            reason=str(payload.get("reason") or "").strip() or None,
            version=max(1, int(payload.get("version") or 1)),
            requested_by_user_id=self._uuid_or_none(payload.get("requestedByUserId")),
            requested_at=self._datetime_or_none(payload.get("requestedAt")),
            updated_at=self._datetime_or_none(payload.get("updatedAt")) or self._timestamp(),
        )

    def _timestamp(self) -> datetime:
        return self._now or datetime.now(timezone.utc)

    @staticmethod
    def _mode_or_none(value: object) -> str | None:
        text = str(value or "").strip().lower()
        return text if text in {"drain", "quiesce"} else None

    @staticmethod
    def _uuid_or_none(value: object) -> UUID | None:
        if isinstance(value, UUID):
            return value
        if value is None:
            return None
        try:
            return UUID(str(value))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _datetime_or_none(value: object) -> datetime | None:
        if isinstance(value, datetime):
            return value
        if value is None:
            return None
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            return None

    @staticmethod
    def _idempotency_key(command: WorkerOperationCommand, actor_user_id: UUID | None) -> str:
        return "|".join(
            [
                "worker-operation",
                command.action,
                command.mode or "none",
                str(actor_user_id or "anonymous"),
                command.reason or "",
            ]
        )[:128]
