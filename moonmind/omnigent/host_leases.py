"""Durable host binding and fenced lease authority for generic hosts."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any, NoReturn, Protocol

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)


class HostLeaseAuthority(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    leaseRef: str = Field(alias="leaseRef")
    bindingRef: str = Field(alias="bindingRef")
    runtimeBindingId: str = Field(alias="runtimeBindingId")
    hostClassRef: str = Field(alias="hostClassRef")
    generation: int = Field(ge=1)
    launchGeneration: int = Field(alias="launchGeneration", ge=1)
    status: str
    omnigentHostId: str | None = Field(None, alias="omnigentHostId")
    cleanupHandle: dict[str, Any] | None = Field(None, alias="cleanupHandle")
    heartbeatAt: datetime | None = Field(None, alias="heartbeatAt")
    expiresAt: datetime | None = Field(None, alias="expiresAt")


class OmnigentHostLeaseRepository(Protocol):
    async def acquire(self, **kwargs: Any) -> HostLeaseAuthority:
        raise NotImplementedError

    async def mark_ready(self, lease_ref: str, **kwargs: Any) -> HostLeaseAuthority:
        raise NotImplementedError

    async def record_launch(
        self, lease_ref: str, **kwargs: Any
    ) -> HostLeaseAuthority:
        raise NotImplementedError

    async def claim_cleanup(
        self, lease_ref: str, **kwargs: Any
    ) -> HostLeaseAuthority:
        raise NotImplementedError

    async def mark_cleaned(
        self, lease_ref: str, **kwargs: Any
    ) -> HostLeaseAuthority:
        raise NotImplementedError

    async def heartbeat(
        self, lease_ref: str, **kwargs: Any
    ) -> HostLeaseAuthority:
        raise NotImplementedError


def _identity(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}:sha256:{digest}"


def generic_host_lease_ref(
    *, runtime_binding_id: str, host_class_ref: str
) -> str:
    """Return the deterministic host-lease identity for one runtime binding.

    MoonLadderStudios/MoonMind#3880 requirement 5: the pre-Activity host
    admission read and the transactional allocation must name the same
    reservation, so both derive it here rather than each computing its own.
    """

    return _identity("omnigent-host-lease", runtime_binding_id, host_class_ref)


def _conflict(message: str) -> NoReturn:
    raise HarnessPlatformError(
        message,
        code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
    )


class InMemoryOmnigentHostLeaseRepository:
    def __init__(self) -> None:
        self._leases: dict[str, HostLeaseAuthority] = {}

    async def acquire(
        self,
        *,
        execution_plan_ref: str,
        runtime_binding_id: str,
        host_class_ref: str,
        launch_policy_ref: str,
        harness_id: str,
        harness_implementation_ref: str,
        provider_profile_refs: tuple[str, ...],
        ttl_seconds: int = 3600,
    ) -> HostLeaseAuthority:
        del (
            launch_policy_ref,
            harness_id,
            harness_implementation_ref,
            provider_profile_refs,
        )
        binding_ref = _identity("omnigent-host-binding", execution_plan_ref)
        lease_ref = generic_host_lease_ref(
            runtime_binding_id=runtime_binding_id, host_class_ref=host_class_ref
        )
        existing = self._leases.get(lease_ref)
        if existing is not None:
            if (
                existing.bindingRef != binding_ref
                or existing.runtimeBindingId != runtime_binding_id
                or existing.hostClassRef != host_class_ref
            ):
                _conflict("host lease idempotency conflict")
            return existing
        lease = HostLeaseAuthority.model_validate(
            {
                "leaseRef": lease_ref,
                "bindingRef": binding_ref,
                "runtimeBindingId": runtime_binding_id,
                "hostClassRef": host_class_ref,
                "generation": 1,
                "launchGeneration": 1,
                "status": "allocating",
                "heartbeatAt": datetime.now(UTC),
                "expiresAt": datetime.now(UTC) + timedelta(seconds=ttl_seconds),
            }
        )
        self._leases[lease_ref] = lease
        return lease

    async def get(self, lease_ref: str) -> HostLeaseAuthority | None:
        return self._leases.get(lease_ref)

    async def list_recoverable(
        self, *, stale_before: datetime
    ) -> tuple[HostLeaseAuthority, ...]:
        now = datetime.now(UTC)
        return tuple(
            lease
            for lease in self._leases.values()
            if lease.status in {"allocating", "ready", "cleanup_pending"}
            and lease.expiresAt is not None
            and (lease.expiresAt < now or lease.expiresAt < stale_before)
        )

    async def mark_ready(
        self,
        lease_ref: str,
        *,
        expected_generation: int,
        omnigent_host_id: str,
        cleanup_handle: dict[str, Any],
    ) -> HostLeaseAuthority:
        return self._transition(
            lease_ref,
            expected_generation=expected_generation,
            expected_statuses={"allocating"},
            status="ready",
            omnigentHostId=omnigent_host_id,
            cleanupHandle=cleanup_handle,
        )

    async def record_launch(
        self,
        lease_ref: str,
        *,
        expected_generation: int,
        cleanup_handle: dict[str, Any],
    ) -> HostLeaseAuthority:
        return self._transition(
            lease_ref,
            expected_generation=expected_generation,
            expected_statuses={"allocating"},
            status="allocating",
            cleanupHandle=cleanup_handle,
        )

    async def claim_cleanup(
        self, lease_ref: str, *, expected_generation: int
    ) -> HostLeaseAuthority:
        return self._transition(
            lease_ref,
            expected_generation=expected_generation,
            expected_statuses={"allocating", "ready", "cleanup_pending"},
            status="cleanup_pending",
            generation=expected_generation + 1,
        )

    async def mark_cleaned(
        self, lease_ref: str, *, expected_generation: int
    ) -> HostLeaseAuthority:
        return self._transition(
            lease_ref,
            expected_generation=expected_generation,
            expected_statuses={"cleanup_pending"},
            status="cleaned",
        )

    async def heartbeat(
        self,
        lease_ref: str,
        *,
        expected_generation: int,
        ttl_seconds: int = 900,
    ) -> HostLeaseAuthority:
        now = datetime.now(UTC)
        existing = self._leases.get(lease_ref)
        if existing is None:
            _conflict("host lease heartbeat authority does not exist")
        return self._transition(
            lease_ref,
            expected_generation=expected_generation,
            expected_statuses={"allocating", "ready"},
            status=existing.status,
            heartbeatAt=now,
            expiresAt=now + timedelta(seconds=ttl_seconds),
        )

    def _transition(
        self,
        lease_ref: str,
        *,
        expected_generation: int,
        expected_statuses: set[str],
        status: str,
        **updates: Any,
    ) -> HostLeaseAuthority:
        existing = self._leases.get(lease_ref)
        if (
            existing is None
            or existing.generation != expected_generation
            or existing.status not in expected_statuses
        ):
            _conflict("host lease CAS fence does not match")
        data = existing.model_dump(by_alias=True, mode="json")
        data.update(updates)
        data["status"] = status
        updated = HostLeaseAuthority.model_validate(data)
        self._leases[lease_ref] = updated
        return updated

class DbOmnigentHostLeaseRepository:
    def __init__(
        self, session_factory: Any, *, capacity_admission: Any | None = None
    ) -> None:
        self._session_factory = session_factory
        # MoonLadderStudios/MoonMind#3878: aggregate host and cold-launch
        # limits are only real if the count and the durable reservation are one
        # serialized operation. Read-only pre-checks elsewhere are for waiting;
        # this is where admission is enforced.
        self._capacity_admission = capacity_admission

    @staticmethod
    def _from_row(row: Any) -> HostLeaseAuthority:
        return HostLeaseAuthority.model_validate(
            {
                "leaseRef": row.lease_id,
                "bindingRef": row.binding_id,
                "runtimeBindingId": row.runtime_binding_id,
                "hostClassRef": row.host_class_ref,
                "generation": row.generation,
                "launchGeneration": row.host_lease_generation,
                "status": row.status,
                "omnigentHostId": row.omnigent_host_id,
                "cleanupHandle": row.cleanup_handle_json,
                "heartbeatAt": row.heartbeat_at,
                "expiresAt": row.expires_at,
            }
        )

    async def get(self, lease_ref: str) -> HostLeaseAuthority | None:
        from api_service.db.models import OmnigentHostLeaseRecordV2

        async with self._session_factory() as session:
            row = await session.get(OmnigentHostLeaseRecordV2, lease_ref)
            return self._from_row(row) if row is not None else None

    async def acquire(self, **kwargs: Any) -> HostLeaseAuthority:
        from api_service.db.models import (
            OmnigentHostBindingRecordV2,
            OmnigentHostLeaseRecordV2,
        )

        execution_plan_ref = str(kwargs["execution_plan_ref"])
        runtime_binding_id = str(kwargs["runtime_binding_id"])
        host_class_ref = str(kwargs["host_class_ref"])
        binding_ref = _identity("omnigent-host-binding", execution_plan_ref)
        lease_ref = generic_host_lease_ref(
            runtime_binding_id=runtime_binding_id, host_class_ref=host_class_ref
        )
        existing = await self.get(lease_ref)
        if existing is not None:
            if existing.runtimeBindingId != runtime_binding_id:
                _conflict("host lease idempotency conflict")
            return existing
        expires_at = datetime.now(UTC) + timedelta(
            seconds=int(kwargs.get("ttl_seconds", 3600))
        )
        async with self._session_factory() as session:
            if self._capacity_admission is not None:
                decision = await self._capacity_admission.evaluate_within(session)
                if not decision.admitted:
                    raise HarnessPlatformError(
                        decision.waiting_reason,
                        code=(
                            HarnessPlatformFailure
                            .OMNIGENT_HOST_CAPACITY_UNAVAILABLE
                        ),
                    )
            binding = await session.get(OmnigentHostBindingRecordV2, binding_ref)
            if binding is None:
                session.add(
                    OmnigentHostBindingRecordV2(
                        binding_id=binding_ref,
                        host_class_ref=host_class_ref,
                        launch_policy_ref=str(kwargs["launch_policy_ref"]),
                        harness_id=str(kwargs["harness_id"]),
                        harness_implementation_ref=str(
                            kwargs["harness_implementation_ref"]
                        ),
                        execution_plan_ref=execution_plan_ref,
                        provider_profile_refs_json=list(
                            kwargs["provider_profile_refs"]
                        ),
                    )
                )
            session.add(
                OmnigentHostLeaseRecordV2(
                    lease_id=lease_ref,
                    binding_id=binding_ref,
                    runtime_binding_id=runtime_binding_id,
                    host_class_ref=host_class_ref,
                    generation=1,
                    status="allocating",
                    host_lease_generation=1,
                    heartbeat_at=datetime.now(UTC),
                    expires_at=expires_at,
                )
            )
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raced = await self.get(lease_ref)
                if raced is not None:
                    return raced
                raise HarnessPlatformError(
                    "host lease persistence conflict",
                    code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
                ) from exc
        created = await self.get(lease_ref)
        assert created is not None
        return created

    async def mark_ready(self, lease_ref: str, **kwargs: Any) -> HostLeaseAuthority:
        return await self._db_transition(
            lease_ref,
            expected_generation=int(kwargs["expected_generation"]),
            expected_statuses=("allocating",),
            values={
                "status": "ready",
                "omnigent_host_id": str(kwargs["omnigent_host_id"]),
                "cleanup_handle_json": dict(kwargs["cleanup_handle"]),
            },
        )

    async def record_launch(self, lease_ref: str, **kwargs: Any) -> HostLeaseAuthority:
        return await self._db_transition(
            lease_ref,
            expected_generation=int(kwargs["expected_generation"]),
            expected_statuses=("allocating",),
            values={"cleanup_handle_json": dict(kwargs["cleanup_handle"])},
        )

    async def claim_cleanup(self, lease_ref: str, **kwargs: Any) -> HostLeaseAuthority:
        expected = int(kwargs["expected_generation"])
        return await self._db_transition(
            lease_ref,
            expected_generation=expected,
            expected_statuses=("allocating", "ready", "cleanup_pending"),
            values={"status": "cleanup_pending", "generation": expected + 1},
        )

    async def mark_cleaned(self, lease_ref: str, **kwargs: Any) -> HostLeaseAuthority:
        return await self._db_transition(
            lease_ref,
            expected_generation=int(kwargs["expected_generation"]),
            expected_statuses=("cleanup_pending",),
            values={"status": "cleaned"},
        )

    async def heartbeat(self, lease_ref: str, **kwargs: Any) -> HostLeaseAuthority:
        now = datetime.now(UTC)
        return await self._db_transition(
            lease_ref,
            expected_generation=int(kwargs["expected_generation"]),
            expected_statuses=("allocating", "ready"),
            values={
                "heartbeat_at": now,
                "expires_at": now
                + timedelta(seconds=int(kwargs.get("ttl_seconds", 900))),
            },
        )

    async def _db_transition(
        self,
        lease_ref: str,
        *,
        expected_generation: int,
        expected_statuses: tuple[str, ...],
        values: dict[str, Any],
    ) -> HostLeaseAuthority:
        from api_service.db.models import OmnigentHostLeaseRecordV2

        values = {**values, "heartbeat_at": datetime.now(UTC)}
        async with self._session_factory() as session:
            result = await session.execute(
                update(OmnigentHostLeaseRecordV2)
                .where(
                    OmnigentHostLeaseRecordV2.lease_id == lease_ref,
                    OmnigentHostLeaseRecordV2.generation == expected_generation,
                    OmnigentHostLeaseRecordV2.status.in_(expected_statuses),
                )
                .values(**values)
            )
            if result.rowcount != 1:
                await session.rollback()
                _conflict("host lease CAS fence does not match")
            await session.commit()
        updated = await self.get(lease_ref)
        assert updated is not None
        return updated

    async def list_recoverable(
        self, *, stale_before: datetime
    ) -> tuple[HostLeaseAuthority, ...]:
        from api_service.db.models import OmnigentHostLeaseRecordV2

        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(OmnigentHostLeaseRecordV2).where(
                            OmnigentHostLeaseRecordV2.status.in_(
                                ("allocating", "ready", "cleanup_pending")
                            ),
                            (
                                (
                                    OmnigentHostLeaseRecordV2.expires_at
                                    < datetime.now(UTC)
                                )
                                | (
                                    OmnigentHostLeaseRecordV2.heartbeat_at
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


__all__ = [
    "DbOmnigentHostLeaseRepository",
    "generic_host_lease_ref",
    "HostLeaseAuthority",
    "InMemoryOmnigentHostLeaseRepository",
    "OmnigentHostLeaseRepository",
]
