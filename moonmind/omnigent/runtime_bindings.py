"""Stable, CAS-fenced runtime-binding aggregate for generic hosts."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)


class RuntimeBindingState(StrEnum):
    planned = "planned"
    credentials_acquired = "credentials_acquired"
    credentials_materialized = "credentials_materialized"
    host_allocating = "host_allocating"
    host_ready = "host_ready"
    session_creating = "session_creating"
    session_active = "session_active"
    draining = "draining"
    cleanup_pending = "cleanup_pending"
    cleaned = "cleaned"
    failed = "failed"


_ALLOWED_TRANSITIONS: dict[RuntimeBindingState, set[RuntimeBindingState]] = {
    RuntimeBindingState.planned: {
        RuntimeBindingState.credentials_acquired,
        RuntimeBindingState.failed,
    },
    RuntimeBindingState.credentials_acquired: {
        RuntimeBindingState.credentials_materialized,
        RuntimeBindingState.cleanup_pending,
        RuntimeBindingState.failed,
    },
    RuntimeBindingState.credentials_materialized: {
        RuntimeBindingState.host_allocating,
        RuntimeBindingState.cleanup_pending,
        RuntimeBindingState.failed,
    },
    RuntimeBindingState.host_allocating: {
        RuntimeBindingState.host_ready,
        RuntimeBindingState.cleanup_pending,
        RuntimeBindingState.failed,
    },
    RuntimeBindingState.host_ready: {
        RuntimeBindingState.session_creating,
        RuntimeBindingState.cleanup_pending,
        RuntimeBindingState.failed,
    },
    RuntimeBindingState.session_creating: {
        RuntimeBindingState.session_active,
        RuntimeBindingState.cleanup_pending,
        RuntimeBindingState.failed,
    },
    RuntimeBindingState.session_active: {
        RuntimeBindingState.draining,
        RuntimeBindingState.cleanup_pending,
        RuntimeBindingState.failed,
    },
    RuntimeBindingState.draining: {
        RuntimeBindingState.cleanup_pending,
        RuntimeBindingState.failed,
    },
    RuntimeBindingState.cleanup_pending: {
        RuntimeBindingState.cleaned,
        RuntimeBindingState.failed,
    },
    RuntimeBindingState.failed: {
        RuntimeBindingState.cleanup_pending,
        RuntimeBindingState.cleaned,
    },
    RuntimeBindingState.cleaned: set(),
}


class StableRuntimeBinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schemaVersion: str = Field(
        "moonmind.omnigent-runtime-binding.v2", alias="schemaVersion"
    )
    bindingId: str = Field(alias="bindingId")
    latestSnapshotRef: str = Field(alias="latestSnapshotRef")
    executionPlanRef: str = Field(alias="executionPlanRef")
    revision: int = Field(ge=1)
    fencingGeneration: int = Field(alias="fencingGeneration", ge=1)
    state: RuntimeBindingState
    providerLeases: dict[str, dict[str, Any]] = Field(alias="providerLeases")
    credentialRuntimeHandles: dict[str, dict[str, Any]] = Field(
        default_factory=dict, alias="credentialRuntimeHandles"
    )
    hostBindingRef: str | None = Field(None, alias="hostBindingRef")
    hostLeaseRef: str | None = Field(None, alias="hostLeaseRef")
    hostLeaseGeneration: int | None = Field(None, alias="hostLeaseGeneration")
    omnigentHostId: str | None = Field(None, alias="omnigentHostId")
    omnigentSessionId: str | None = Field(None, alias="omnigentSessionId")
    attestationRefs: dict[str, str] = Field(
        default_factory=dict, alias="attestationRefs"
    )
    cleanupAuthorityRefs: tuple[str | dict[str, Any], ...] = Field(
        default_factory=tuple, alias="cleanupAuthorityRefs"
    )
    failureCode: str | None = Field(None, alias="failureCode")
    terminalResult: dict[str, Any] | None = Field(None, alias="terminalResult")
    heartbeatAt: datetime | None = Field(None, alias="heartbeatAt")

    @model_validator(mode="after")
    def validate_authority(self, info: ValidationInfo) -> "StableRuntimeBinding":
        if not self.bindingId.startswith("omnigent-runtime-binding:"):
            raise ValueError("bindingId is invalid")
        if not self.executionPlanRef.startswith("omnigent-execution-plan:sha256:"):
            raise ValueError("executionPlanRef is invalid")
        payload = self.model_dump(
            by_alias=True, mode="json", exclude={"latestSnapshotRef"}
        )
        if not (info.context or {}).get("skip_snapshot_validation"):
            expected = _snapshot_ref(payload)
            if self.latestSnapshotRef != expected:
                raise ValueError("latestSnapshotRef digest mismatch")
        for key in _walk_keys(payload):
            if _is_forbidden_secret_key(key):
                raise ValueError(
                    f"runtime binding contains forbidden secret-bearing key {key}"
                )
        return self


def _walk_keys(value: Any):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _walk_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_keys(item)


_SAFE_TOKEN_METRIC_KEYS = frozenset(
    {
        "cachedinputtokens",
        "inputtokens",
        "maxtokens",
        "outputtokens",
        "reasoningtokens",
        "tokencount",
        "tokens",
        "tokenusage",
        "totaltokens",
    }
)


def _is_forbidden_secret_key(key: str) -> bool:
    normalized = "".join(character for character in key.lower() if character.isalnum())
    if normalized in _SAFE_TOKEN_METRIC_KEYS:
        return False
    if normalized.endswith("ref") or normalized.endswith("refs"):
        return False
    if normalized in {"key", "apikey", "authorization", "credential", "credentials"}:
        return True
    return any(
        fragment in normalized
        for fragment in (
            "accesstoken",
            "authtoken",
            "bearertoken",
            "password",
            "privatekey",
            "refreshtoken",
            "secret",
        )
    )


def _snapshot_ref(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str
    ).encode()
    return (
        "omnigent-runtime-binding-snapshot:sha256:"
        + hashlib.sha256(encoded).hexdigest()
    )


def stable_binding_id(*, execution_plan_ref: str, idempotency_key: str) -> str:
    encoded = f"{execution_plan_ref}\0{idempotency_key}".encode("utf-8")
    return "omnigent-runtime-binding:sha256:" + hashlib.sha256(encoded).hexdigest()


def _binding(data: dict[str, Any]) -> StableRuntimeBinding:
    payload = {
        "schemaVersion": "moonmind.omnigent-runtime-binding.v2",
        "credentialRuntimeHandles": {},
        "hostBindingRef": None,
        "hostLeaseRef": None,
        "hostLeaseGeneration": None,
        "omnigentHostId": None,
        "omnigentSessionId": None,
        "attestationRefs": {},
        "cleanupAuthorityRefs": [],
        "failureCode": None,
        "terminalResult": None,
        "heartbeatAt": None,
        **dict(data),
    }
    payload["latestSnapshotRef"] = "pending"
    normalized = StableRuntimeBinding.model_validate(
        payload, context={"skip_snapshot_validation": True}
    ).model_dump(by_alias=True, mode="json")
    normalized.pop("latestSnapshotRef", None)
    normalized["latestSnapshotRef"] = _snapshot_ref(normalized)
    return StableRuntimeBinding.model_validate(normalized)


def create_stable_runtime_binding(
    *,
    execution_plan_ref: str,
    idempotency_key: str,
    provider_leases: dict[str, dict[str, Any]],
) -> StableRuntimeBinding:
    return _binding(
        {
            "bindingId": stable_binding_id(
                execution_plan_ref=execution_plan_ref,
                idempotency_key=idempotency_key,
            ),
            "executionPlanRef": execution_plan_ref,
            "revision": 1,
            "fencingGeneration": 1,
            "state": RuntimeBindingState.credentials_acquired,
            "providerLeases": provider_leases,
            "credentialRuntimeHandles": {},
            "attestationRefs": {},
            "cleanupAuthorityRefs": [],
            "heartbeatAt": datetime.now(UTC),
        }
    )


def evolve_binding(
    existing: StableRuntimeBinding,
    *,
    expected_revision: int,
    expected_fencing_generation: int,
    state: RuntimeBindingState | None = None,
    updates: dict[str, Any] | None = None,
    increment_fence: bool = False,
) -> StableRuntimeBinding:
    if (
        existing.revision != expected_revision
        or existing.fencingGeneration != expected_fencing_generation
    ):
        raise HarnessPlatformError(
            "runtime binding CAS fence does not match",
            code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
        )
    target = state or existing.state
    if target != existing.state and target not in _ALLOWED_TRANSITIONS[existing.state]:
        raise HarnessPlatformError(
            f"invalid runtime binding transition {existing.state} -> {target}",
            code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
        )
    data = existing.model_dump(by_alias=True, mode="json")
    immutable = {
        "bindingId": data["bindingId"],
        "executionPlanRef": data["executionPlanRef"],
        "providerLeases": data["providerLeases"],
    }
    if existing.terminalResult is not None:
        immutable["terminalResult"] = data["terminalResult"]
    data.update(dict(updates or {}))
    for key, value in immutable.items():
        if data.get(key) != value:
            raise HarnessPlatformError(
                f"runtime binding update attempted to mutate immutable {key}",
                code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
            )
    data["revision"] = existing.revision + 1
    data["fencingGeneration"] = existing.fencingGeneration + (
        1 if increment_fence else 0
    )
    data["state"] = target.value
    data["heartbeatAt"] = datetime.now(UTC)
    return _binding(data)


class InMemoryStableRuntimeBindingStore:
    def __init__(self) -> None:
        self._bindings: dict[str, StableRuntimeBinding] = {}

    async def get(self, binding_id: str) -> StableRuntimeBinding | None:
        return self._bindings.get(binding_id)

    async def create_initial(
        self,
        *,
        execution_plan_ref: str,
        idempotency_key: str,
        provider_leases: dict[str, dict[str, Any]],
    ) -> StableRuntimeBinding:
        binding = create_stable_runtime_binding(
            execution_plan_ref=execution_plan_ref,
            idempotency_key=idempotency_key,
            provider_leases=provider_leases,
        )
        existing = self._bindings.get(binding.bindingId)
        if existing is not None:
            if (
                existing.executionPlanRef != execution_plan_ref
                or existing.providerLeases != provider_leases
            ):
                raise HarnessPlatformError(
                    "runtime binding create conflict",
                    code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
                )
            return existing
        self._bindings[binding.bindingId] = binding
        return binding

    async def update(
        self,
        binding_id: str,
        *,
        expected_revision: int,
        expected_fencing_generation: int,
        state: RuntimeBindingState | None = None,
        updates: dict[str, Any] | None = None,
        increment_fence: bool = False,
    ) -> StableRuntimeBinding:
        existing = self._bindings.get(binding_id)
        if existing is None:
            raise HarnessPlatformError(
                "runtime binding does not exist",
                code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
            )
        updated = evolve_binding(
            existing,
            expected_revision=expected_revision,
            expected_fencing_generation=expected_fencing_generation,
            state=state,
            updates=updates,
            increment_fence=increment_fence,
        )
        self._bindings[binding_id] = updated
        return updated

    async def list_recoverable(
        self, *, stale_before: datetime
    ) -> tuple[StableRuntimeBinding, ...]:
        terminal = {RuntimeBindingState.cleaned}
        return tuple(
            binding
            for binding in self._bindings.values()
            if binding.state not in terminal
            and (binding.heartbeatAt is None or binding.heartbeatAt < stale_before)
        )


class DbRuntimeBindingStore:
    """DB implementation using stable identity and revision/fence CAS."""

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _from_row(row: Any) -> StableRuntimeBinding:
        binding = _binding(
            {
                "bindingId": row.binding_id,
                "executionPlanRef": row.execution_plan_ref,
                "revision": row.revision,
                "fencingGeneration": row.fencing_generation,
                "state": row.state,
                "providerLeases": row.provider_leases_json,
                "credentialRuntimeHandles": row.credential_runtime_handles_json,
                "hostBindingRef": row.host_binding_ref,
                "hostLeaseRef": row.host_lease_ref,
                "hostLeaseGeneration": row.host_lease_generation,
                "omnigentHostId": row.omnigent_host_id,
                "omnigentSessionId": row.session_id,
                "attestationRefs": row.attestation_refs_json,
                "cleanupAuthorityRefs": row.cleanup_authority_refs_json,
                "failureCode": row.failure_code,
                "terminalResult": row.terminal_result_json,
                "heartbeatAt": row.heartbeat_at,
            }
        )
        if binding.latestSnapshotRef != row.latest_snapshot_ref:
            raise HarnessPlatformError(
                "persisted runtime binding snapshot digest does not match its payload",
                code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
            )
        return binding

    async def get(self, binding_id: str) -> StableRuntimeBinding | None:
        from api_service.db.models import OmnigentRuntimeBindingRecord

        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(OmnigentRuntimeBindingRecord).where(
                        OmnigentRuntimeBindingRecord.binding_id == binding_id
                    )
                )
            ).scalar_one_or_none()
            return self._from_row(row) if row is not None else None

    async def create_initial(
        self,
        *,
        execution_plan_ref: str,
        idempotency_key: str,
        provider_leases: dict[str, dict[str, Any]],
    ) -> StableRuntimeBinding:
        from api_service.db.models import OmnigentRuntimeBindingRecord

        binding = create_stable_runtime_binding(
            execution_plan_ref=execution_plan_ref,
            idempotency_key=idempotency_key,
            provider_leases=provider_leases,
        )
        existing = await self.get(binding.bindingId)
        if existing is not None:
            if (
                existing.executionPlanRef != binding.executionPlanRef
                or existing.providerLeases != binding.providerLeases
            ):
                raise HarnessPlatformError(
                    "runtime binding create conflict",
                    code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
                )
            return existing
        async with self._session_factory() as session:
            session.add(
                OmnigentRuntimeBindingRecord(
                    runtime_binding_ref=binding.latestSnapshotRef,
                    binding_id=binding.bindingId,
                    latest_snapshot_ref=binding.latestSnapshotRef,
                    execution_plan_ref=binding.executionPlanRef,
                    revision=binding.revision,
                    fencing_generation=binding.fencingGeneration,
                    state=binding.state.value,
                    heartbeat_at=binding.heartbeatAt,
                    provider_leases_json=binding.providerLeases,
                    credential_runtime_handles_json={},
                    attestation_refs_json={},
                    cleanup_authority_refs_json=[],
                    terminal_result_json=binding.terminalResult,
                )
            )
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raced = await self.get(binding.bindingId)
                if raced is not None:
                    return raced
                raise HarnessPlatformError(
                    "runtime binding create conflict",
                    code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
                ) from exc
        return binding

    async def update(
        self,
        binding_id: str,
        *,
        expected_revision: int,
        expected_fencing_generation: int,
        state: RuntimeBindingState | None = None,
        updates: dict[str, Any] | None = None,
        increment_fence: bool = False,
    ) -> StableRuntimeBinding:
        from api_service.db.models import OmnigentRuntimeBindingRecord

        existing = await self.get(binding_id)
        if existing is None:
            raise HarnessPlatformError(
                "runtime binding does not exist",
                code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
            )
        evolved = evolve_binding(
            existing,
            expected_revision=expected_revision,
            expected_fencing_generation=expected_fencing_generation,
            state=state,
            updates=updates,
            increment_fence=increment_fence,
        )
        values = {
            "latest_snapshot_ref": evolved.latestSnapshotRef,
            "revision": evolved.revision,
            "fencing_generation": evolved.fencingGeneration,
            "state": evolved.state.value,
            "failure_code": evolved.failureCode,
            "terminal_result_json": evolved.terminalResult,
            "heartbeat_at": evolved.heartbeatAt,
            "credential_runtime_handles_json": evolved.credentialRuntimeHandles,
            "host_binding_ref": evolved.hostBindingRef,
            "host_lease_ref": evolved.hostLeaseRef,
            "host_lease_generation": evolved.hostLeaseGeneration,
            "omnigent_host_id": evolved.omnigentHostId,
            "session_id": evolved.omnigentSessionId,
            "attestation_refs_json": evolved.attestationRefs,
            "cleanup_authority_refs_json": list(evolved.cleanupAuthorityRefs),
        }
        async with self._session_factory() as session:
            result = await session.execute(
                update(OmnigentRuntimeBindingRecord)
                .where(
                    OmnigentRuntimeBindingRecord.binding_id == binding_id,
                    OmnigentRuntimeBindingRecord.revision == expected_revision,
                    OmnigentRuntimeBindingRecord.fencing_generation
                    == expected_fencing_generation,
                )
                .values(**values)
            )
            if result.rowcount != 1:
                await session.rollback()
                raise HarnessPlatformError(
                    "runtime binding CAS update conflict",
                    code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
                )
            await session.commit()
        return evolved

    async def list_recoverable(
        self, *, stale_before: datetime
    ) -> tuple[StableRuntimeBinding, ...]:
        from api_service.db.models import OmnigentRuntimeBindingRecord

        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(OmnigentRuntimeBindingRecord).where(
                            OmnigentRuntimeBindingRecord.state
                            != RuntimeBindingState.cleaned.value,
                            OmnigentRuntimeBindingRecord.binding_id.like(
                                "omnigent-runtime-binding:sha256:%"
                            ),
                            (
                                OmnigentRuntimeBindingRecord.heartbeat_at.is_(None)
                                | (
                                    OmnigentRuntimeBindingRecord.heartbeat_at
                                    < stale_before
                                )
                            ),
                        )
                    )
                )
                .scalars()
                .all()
            )
            return tuple(self._from_row(row) for row in rows)


class RuntimeBindingSessionAuthoritySink:
    def __init__(self, store: Any, binding: StableRuntimeBinding) -> None:
        self._store = store
        self.binding = binding
        self._lock = asyncio.Lock()

    async def session_created(self, session_id: str) -> None:
        async with self._lock:
            if self.binding.omnigentSessionId:
                if self.binding.omnigentSessionId != session_id:
                    raise HarnessPlatformError(
                        "runtime binding is already committed to a different session",
                        code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
                    )
                return
            self.binding = await self._store.update(
                self.binding.bindingId,
                expected_revision=self.binding.revision,
                expected_fencing_generation=self.binding.fencingGeneration,
                state=RuntimeBindingState.session_active,
                updates={"omnigentSessionId": session_id},
            )

    async def heartbeat(self) -> StableRuntimeBinding:
        async with self._lock:
            self.binding = await self._store.update(
                self.binding.bindingId,
                expected_revision=self.binding.revision,
                expected_fencing_generation=self.binding.fencingGeneration,
            )
            return self.binding


__all__ = [
    "DbRuntimeBindingStore",
    "InMemoryStableRuntimeBindingStore",
    "RuntimeBindingSessionAuthoritySink",
    "RuntimeBindingState",
    "StableRuntimeBinding",
    "create_stable_runtime_binding",
    "evolve_binding",
    "stable_binding_id",
]
