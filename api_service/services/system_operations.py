"""System operation command services for Settings -> Operations."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.api.schemas import (
    OperationCommandDescriptorModel,
    QueueSystemMetadataModel,
    WorkerPauseAuditEventModel,
    WorkerPauseAuditListModel,
    WorkerPauseMetricsModel,
    WorkerPauseSnapshotResponse,
)
from api_service.db.models import SettingsAuditEvent, SettingsOverride
from moonmind.schemas.workflow_control_models import WorkflowControlBatch


_DEFAULT_SUBJECT_ID = UUID("00000000-0000-0000-0000-000000000000")
_WORKER_STATE_KEY = "operations.workers.pause_state"
_WORKER_AUDIT_KEY = "operations.workers"
_LOG = logging.getLogger(__name__)


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
    idempotency_key: str = Field(..., alias="idempotencyKey")
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
        control = await self._reconcile_control()
        state = await self._load_state()
        metrics = await self._load_metrics()
        audit = await self._latest_audit()
        return WorkerPauseSnapshotResponse(
            system=QueueSystemMetadataModel.from_service_metadata(state),
            metrics=metrics,
            commands=self._command_descriptors(state),
            audit=WorkerPauseAuditListModel(latest=audit),
            signalStatus=failure_reason or (control.status if control else signal_status or (audit[0].signal_status if audit else None)),
            control=control,
        )

    async def submit(
        self,
        command: WorkerOperationCommand,
        *,
        actor_user_id: UUID | str | None,
    ) -> WorkerPauseSnapshotResponse:
        normalized = self._validate_command(command)
        actor_uuid = self._uuid_or_none(actor_user_id)
        idempotency_key = self._idempotency_key(normalized)
        existing_audit = await self._audit_event_by_idempotency_key(idempotency_key)
        if existing_audit is not None:
            payload = (
                existing_audit.new_value_json
                if isinstance(existing_audit.new_value_json, dict)
                else {}
            )
            existing_fingerprint = payload.get("commandFingerprint")
            if existing_fingerprint != self._command_fingerprint(normalized):
                raise SystemOperationValidationError(
                    "worker_operation_idempotency_conflict",
                    "Worker operation idempotency key was already used for a "
                    "different command.",
                )
            return await self.snapshot(
                signal_status=str(payload.get("signalStatus") or "succeeded")
            )

        state = await self._persist_state(normalized, actor_user_id=actor_uuid)
        await self._persist_audit(
            normalized, actor_user_id=actor_uuid,
            status="succeeded" if normalized.mode == "drain" else "requested",
            signal_status="succeeded" if normalized.mode == "drain" else "requested", state=state,
            idempotency_key=idempotency_key,
        )
        # Persist intent and its retry identity before any Temporal side effect.
        await self._session.commit()
        return await self.snapshot()

    async def _reconcile_control(self) -> WorkflowControlBatch | None:
        state_row = await self._state_row()
        current = dict(state_row.value_json or {}) if state_row is not None else {}
        request_id = current.get("controlRequestId")
        if not request_id:
            return None
        row = await self._audit_event_by_idempotency_key(request_id)
        if row is None or not isinstance(row.new_value_json, dict):
            return None
        payload = dict(row.new_value_json)
        raw = payload.get("control")
        if not raw:
            return None  # Historical acceptance counts cannot become safe-point evidence.
        batch = WorkflowControlBatch.model_validate(raw)
        if batch.status == "succeeded":
            return batch
        sender = getattr(self._temporal_service, "send_quiesce_pause_signal" if batch.action == "Pause" else "send_quiesce_resume_signal", None)
        if not callable(sender):
            return batch
        async def persist(progress):
            merged = await self._persist_control_progress(row.id, progress)
            # The adapter may be iterating this target list. Preserve its object
            # identities so later observations remain part of the same batch.
            confirmed = {target.update_id: target for target in merged.targets}
            for target in batch.targets:
                previous = confirmed.get(target.update_id)
                if previous is not None:
                    target.state, target.reason = previous.state, previous.reason
            batch.enumerated = merged.enumerated
            batch.enumeration_error = merged.enumeration_error

        try:
            observed = await sender(request_id=batch.request_id, batch=batch, on_progress=persist)
            return observed if isinstance(observed, WorkflowControlBatch) else batch
        except Exception:
            # A transport observation whose persistence failed is not durable
            # completion. Reload committed evidence before presenting the result.
            _LOG.warning("Worker control reconciliation unavailable")
            await self._session.rollback()
            persisted = await self._audit_event_by_idempotency_key(request_id)
            return WorkflowControlBatch.model_validate(persisted.new_value_json["control"])

    async def _persist_control_progress(
        self, audit_id: UUID, progress: WorkflowControlBatch,
    ) -> WorkflowControlBatch:
        """Serialize observers and retain already-confirmed target outcomes."""
        row = await self._session.get(
            SettingsAuditEvent, audit_id, with_for_update=True, populate_existing=True,
        )
        if row is None:
            raise ValueError("Control audit owner is missing")
        stored = WorkflowControlBatch.model_validate(row.new_value_json["control"])
        if (stored.request_id, stored.action, stored.generation) != (
            progress.request_id, progress.action, progress.generation,
        ):
            raise ValueError("Control progress has a different request authority")
        merged = progress.model_copy(deep=True)
        if stored.enumerated:
            identities = lambda batch: {
                (target.workflow_id, target.run_id, target.update_id)
                for target in batch.targets
            }
            if not progress.enumerated:
                merged = stored
            elif identities(stored) != identities(progress):
                raise ValueError("Enumerated control targets are immutable")
            else:
                prior = {target.update_id: target for target in stored.targets}
                for index, target in enumerate(merged.targets):
                    previous = prior[target.update_id]
                    if previous.state in {"safe_point", "resumed", "failed"} or (
                        target.state == "requested" and previous.state != "requested"
                    ):
                        merged.targets[index] = previous
        row.new_value_json = {
            **dict(row.new_value_json), "control": merged.model_dump(by_alias=True),
            "status": merged.status, "resultStatus": merged.status,
            "signalStatus": merged.status,
        }
        await self._session.commit()
        return merged

    def _validate_command(
        self, command: WorkerOperationCommand
    ) -> WorkerOperationCommand:
        action = str(command.action or "").strip().lower()
        reason = str(command.reason or "").strip()
        mode = str(command.mode or "").strip().lower() or None
        confirmation = str(command.confirmation or "").strip()
        idempotency_key = str(command.idempotency_key or "").strip()
        if action not in {"pause", "resume"}:
            raise SystemOperationValidationError(
                "worker_operation_invalid",
                "Worker operation action is invalid.",
            )
        if not idempotency_key:
            raise SystemOperationValidationError(
                "worker_operation_idempotency_required",
                "Worker operation idempotency key is required.",
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
            idempotencyKey=idempotency_key,
            force_resume=bool(command.force_resume),
        )

    async def _persist_state(
        self,
        command: WorkerOperationCommand,
        *,
        actor_user_id: UUID | str | None,
    ) -> _QueueSystemMetadata:
        now = self._timestamp()
        current = await self._state_row(lock=True)
        current_payload = (
            dict(current.value_json)
            if current is not None and isinstance(current.value_json, dict)
            else {}
        )
        next_version = int(current_payload.get("version") or 0) + 1
        actor_uuid = self._uuid_or_none(actor_user_id)
        payload: dict[str, Any] = {
            "workersPaused": command.action == "pause",
            "controlRequestId": self._idempotency_key(command),
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
        idempotency_key: str,
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
                    "resultStatus": status,
                    "signalStatus": signal_status,
                    "requestedState": "paused" if state.workers_paused else "running",
                    "idempotencyKey": idempotency_key,
                    "commandFingerprint": self._command_fingerprint(command),
                    "control": (
                        WorkflowControlBatch(requestId=idempotency_key, action="Pause" if command.action == "pause" else "Resume", generation=state.version).model_dump(by_alias=True)
                        if command.mode == "quiesce" or command.action == "resume" else None
                    ),
                },
                redacted=False,
                reason=command.reason,
            )
        )

    async def _audit_event_by_idempotency_key(
        self, idempotency_key: str
    ) -> SettingsAuditEvent | None:
        result = await self._session.execute(
            select(SettingsAuditEvent).where(
                SettingsAuditEvent.key == _WORKER_AUDIT_KEY,
                SettingsAuditEvent.new_value_json["idempotencyKey"].as_string() == idempotency_key,
            )
        )
        return result.scalar_one_or_none()

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

    async def _load_metrics(self) -> WorkerPauseMetricsModel:
        def unavailable() -> WorkerPauseMetricsModel:
            return WorkerPauseMetricsModel(
                queued=0,
                running=0,
                staleRunning=0,
                isDrained=False,
                metricsSource="unavailable",
            )

        reader = getattr(self._temporal_service, "get_drain_metrics", None)
        if not callable(reader):
            return unavailable()
        try:
            raw = await reader()
            if not isinstance(raw, Mapping):
                return unavailable()
            queued = max(0, int(raw.get("queued") or 0))
            running = max(0, int(raw.get("running") or 0))
            stale_running = max(0, int(raw.get("stale_running") or 0))
        except Exception:
            _LOG.warning("Failed to load worker pause drain metrics", exc_info=True)
            return unavailable()
        return WorkerPauseMetricsModel(
            queued=queued,
            running=running,
            staleRunning=stale_running,
            isDrained=(queued + running + stale_running) == 0,
            metricsSource="temporal",
        )

    async def _state_row(self, *, lock: bool = False) -> SettingsOverride | None:
        statement = select(SettingsOverride).where(
            SettingsOverride.scope == "workspace",
            SettingsOverride.workspace_id == _DEFAULT_SUBJECT_ID,
            SettingsOverride.user_id == _DEFAULT_SUBJECT_ID,
            SettingsOverride.key == _WORKER_STATE_KEY,
        ).execution_options(populate_existing=True)
        if lock:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
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
                    target=str(payload.get("target") or "workers"),
                    mode=mode,
                    reason=row.reason,
                    actorUserId=row.actor_user_id,
                    resultStatus=str(
                        payload.get("resultStatus") or payload.get("status") or ""
                    )
                    or None,
                    signalStatus=str(payload.get("signalStatus") or "") or None,
                    idempotencyKey=str(payload.get("idempotencyKey") or "") or None,
                    createdAt=row.created_at,
                )
            )
        return events

    def _command_descriptors(
        self, state: _QueueSystemMetadata
    ) -> list[OperationCommandDescriptorModel]:
        return [
            OperationCommandDescriptorModel(
                id="pause-workers",
                label="Pause Workers",
                target="workers",
                impact="Blocks new submissions and optionally requests workflow safe points.",
                requiresConfirmation=True,
                requiredPermission="operations.invoke",
                available=not state.workers_paused,
                unavailableReason=(
                    "Submission admission is already paused." if state.workers_paused else None
                ),
                rollbackAction="resume-workers",
            ),
            OperationCommandDescriptorModel(
                id="resume-workers",
                label="Resume Workers",
                target="workers",
                impact="Reopens submission admission and requests workflow resumption.",
                requiresConfirmation=False,
                requiredPermission="operations.invoke",
                available=state.workers_paused,
                unavailableReason=(
                    None if state.workers_paused else "Submission admission is already open."
                ),
                rollbackAction=None,
            ),
            OperationCommandDescriptorModel(
                id="drain-queue",
                label="Drain Queue",
                target="queue",
                impact="Blocks new submissions while existing work continues.",
                requiresConfirmation=True,
                requiredPermission="operations.invoke",
                available=not state.workers_paused,
                unavailableReason=(
                    "Submission admission is already paused." if state.workers_paused else None
                ),
                rollbackAction="resume-workers",
            ),
            OperationCommandDescriptorModel(
                id="quiesce-runtime-family",
                label="Quiesce Runtime Family",
                target="runtime-family",
                impact="Blocks new submissions and requests confirmed workflow safe points.",
                requiresConfirmation=True,
                requiredPermission="operations.invoke",
                available=not state.workers_paused,
                unavailableReason=(
                    "Submission admission is already paused." if state.workers_paused else None
                ),
                rollbackAction="resume-workers",
            ),
            OperationCommandDescriptorModel(
                id="enable-maintenance-mode",
                label="Enable Maintenance Mode",
                target="scheduler",
                impact="Prevents normal launch scheduling while maintenance is active.",
                requiresConfirmation=True,
                requiredPermission="operations.invoke",
                available=False,
                unavailableReason="Maintenance mode subsystem is not connected.",
                rollbackAction=None,
            ),
            OperationCommandDescriptorModel(
                id="disable-launch-scheduling",
                label="Disable Launch Scheduling",
                target="scheduler",
                impact="Prevents new scheduled launches.",
                requiresConfirmation=True,
                requiredPermission="operations.invoke",
                available=False,
                unavailableReason=(
                    "Launch scheduler command subsystem is not connected."
                ),
                rollbackAction=None,
            ),
            OperationCommandDescriptorModel(
                id="update-operational-reason",
                label="Update Operational Reason",
                target="operations",
                impact="Updates operator-visible reason text for the current state.",
                requiresConfirmation=False,
                requiredPermission="operations.invoke",
                available=False,
                unavailableReason=(
                    "Operational reason command subsystem is not connected."
                ),
                rollbackAction=None,
            ),
            OperationCommandDescriptorModel(
                id="set-operational-banner",
                label="Set Operational Banner",
                target="operations",
                impact="Shows a temporary operator banner in the dashboard.",
                requiresConfirmation=False,
                requiredPermission="operations.invoke",
                available=False,
                unavailableReason="Operational banner subsystem is not connected.",
                rollbackAction=None,
            ),
        ]

    def _metadata_from_payload(self, payload: dict[str, Any]) -> _QueueSystemMetadata:
        return _QueueSystemMetadata(
            workers_paused=bool(payload.get("workersPaused")),
            mode=self._mode_or_none(payload.get("mode")),
            reason=str(payload.get("reason") or "").strip() or None,
            version=max(1, int(payload.get("version") or 1)),
            requested_by_user_id=self._uuid_or_none(payload.get("requestedByUserId")),
            requested_at=self._datetime_or_none(payload.get("requestedAt")),
            updated_at=(
                self._datetime_or_none(payload.get("updatedAt")) or self._timestamp()
            ),
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
    def _idempotency_key(command: WorkerOperationCommand) -> str:
        return command.idempotency_key[:128]

    @staticmethod
    def _command_fingerprint(command: WorkerOperationCommand) -> str:
        return "|".join(["workers", command.action, command.mode or "none"])
