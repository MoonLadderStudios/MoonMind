"""Profile-bound Omnigent OAuth host persistence and lifecycle boundary.

This module owns durable binding/host-lease state and validates every database
load against the Provider Profile and portable ``CredentialMountRef``. Runtime
launchers consume only the trusted records produced here; user-authored mount
names never cross this boundary.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from api_service.db.models import (
    ManagedAgentProviderProfile,
    OmnigentOAuthHostBindingRecord,
    OmnigentOAuthHostLeaseRecord,
)
from moonmind.provider_profiles.oauth_policy import is_codex_oauth_profile
from moonmind.schemas.agent_runtime_models import (
    AuthVolumeRef,
    CredentialMountRef,
    OmnigentOAuthHostBinding,
    OmnigentHostLease,
)
from moonmind.utils.logging import redact_sensitive_text


ACTIVE_HOST_STATES = frozenset(
    {"allocating", "starting", "ready", "assigned", "draining"}
)
TERMINAL_HOST_STATES = frozenset({"stopped", "failed"})


class OmnigentOAuthHostError(RuntimeError):
    code = "OMNIGENT_OAUTH_HOST_ERROR"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(redact_sensitive_text(message)[:512])
        if code:
            self.code = code


class HostPreflightFailure(str, Enum):
    VOLUME_MISSING = "CODEX_OAUTH_VOLUME_MISSING"
    VOLUME_NOT_WRITABLE = "CODEX_OAUTH_VOLUME_NOT_WRITABLE"
    LOGIN_STATUS_FAILED = "CODEX_OAUTH_LOGIN_STATUS_FAILED"
    COMPETING_CREDENTIAL = "CODEX_OAUTH_COMPETING_CREDENTIAL_PRESENT"
    GENERATION_STALE = "CODEX_OAUTH_GENERATION_STALE"
    HOST_NOT_REGISTERED = "OMNIGENT_HOST_NOT_REGISTERED"
    HARNESS_UNAVAILABLE = "OMNIGENT_CODEX_HARNESS_UNAVAILABLE"
    BINDING_MISMATCH = "OMNIGENT_HOST_BINDING_MISMATCH"


_TRANSITIONS: dict[str, frozenset[str]] = {
    "allocating": frozenset({"starting", "failed", "draining"}),
    "starting": frozenset({"ready", "failed", "draining"}),
    "ready": frozenset({"assigned", "draining", "failed"}),
    "assigned": frozenset({"draining", "failed"}),
    "draining": frozenset({"stopped", "failed"}),
    "stopped": frozenset(),
    "failed": frozenset({"draining", "stopped"}),
}


def deterministic_host_lease_id(provider_lease_id: str) -> str:
    digest = hashlib.sha256(provider_lease_id.encode("utf-8")).hexdigest()[:32]
    return f"ohl_{digest}"


def deterministic_host_container_name(host_lease_id: str) -> str:
    safe = "".join(ch for ch in host_lease_id.lower() if ch.isalnum() or ch == "-")
    return f"mm-omnigent-host-{safe[:40]}"


class OmnigentOAuthHostRepository:
    """SQLAlchemy repository with validated loads and CAS transitions."""

    def __init__(self, session_factory: Callable[[], Any]) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _mount_from_profile(
        profile: ManagedAgentProviderProfile,
    ) -> CredentialMountRef:
        if not is_codex_oauth_profile(
            runtime_id=profile.runtime_id,
            credential_source=profile.credential_source,
            materialization_mode=profile.runtime_materialization_mode,
        ):
            raise OmnigentOAuthHostError(
                "profile is not a Codex OAuth profile",
                code=HostPreflightFailure.BINDING_MISMATCH.value,
            )
        if profile.provider_id != "openai":
            raise OmnigentOAuthHostError(
                "Codex OAuth host requires provider_id=openai",
                code=HostPreflightFailure.BINDING_MISMATCH.value,
            )
        if profile.volume_mount_path != "/home/app/.codex" or not profile.volume_ref:
            raise OmnigentOAuthHostError(
                "Codex OAuth profile mount contract is invalid",
                code=HostPreflightFailure.BINDING_MISMATCH.value,
            )
        return CredentialMountRef(
            authVolumeRef=AuthVolumeRef(
                providerProfileId=profile.profile_id,
                runtimeId="codex_cli",
                providerId="openai",
                volumeRef=profile.volume_ref,
                credentialGeneration=profile.credential_generation,
                ownerUserId=str(profile.owner_user_id or "system"),
            ),
            targetPath="/home/app/.codex",
            accessMode="read_write",
            runtimeUid=1000,
            runtimeGid=1000,
        )

    @classmethod
    def _binding_model(
        cls,
        record: OmnigentOAuthHostBindingRecord,
        profile: ManagedAgentProviderProfile,
    ) -> OmnigentOAuthHostBinding:
        mount = CredentialMountRef.model_validate(record.credential_mount_template_json)
        expected = cls._mount_from_profile(profile)
        if mount != expected:
            raise OmnigentOAuthHostError(
                "host binding credential mount differs from the Provider Profile",
                code=HostPreflightFailure.BINDING_MISMATCH.value,
            )
        if record.harness != "codex-native":
            raise OmnigentOAuthHostError(
                "host binding does not advertise codex-native",
                code=HostPreflightFailure.HARNESS_UNAVAILABLE.value,
            )
        return OmnigentOAuthHostBinding(
            bindingRef=record.binding_ref,
            providerProfileId=record.provider_profile_id,
            endpointRef=record.endpoint_ref,
            harness=record.harness,
            credentialMountRef=mount,
            maxHosts=1,
            maxSessionsPerHost=1,
            staticHostId=record.static_host_id,
            hostLaunchProfileRef=record.host_launch_profile_ref,
            executionProfileRef=record.execution_profile_ref,
            launchPolicyRef=record.launch_policy_ref,
            effectiveLaunchSnapshot=record.effective_launch_snapshot_json,
        )

    async def get_binding_for_profile(
        self, profile_id: str
    ) -> OmnigentOAuthHostBinding | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(OmnigentOAuthHostBindingRecord, ManagedAgentProviderProfile)
                .join(
                    ManagedAgentProviderProfile,
                    ManagedAgentProviderProfile.profile_id
                    == OmnigentOAuthHostBindingRecord.provider_profile_id,
                )
                .where(OmnigentOAuthHostBindingRecord.provider_profile_id == profile_id)
            )
            row = result.first()
            return None if row is None else self._binding_model(row[0], row[1])

    async def create_or_update_static_binding(
        self,
        *,
        profile_id: str,
        endpoint_ref: str,
        static_host_id: str | None = None,
        host_launch_profile_ref: str | None = None,
        execution_profile_ref: str | None = None,
        launch_policy_ref: str | None = None,
        effective_launch_snapshot: dict[str, Any] | None = None,
    ) -> OmnigentOAuthHostBinding:
        if static_host_id and host_launch_profile_ref:
            raise ValueError(
                "static_host_id and host_launch_profile_ref are mutually exclusive"
            )
        async with self._session_factory() as session:
            profile = await session.get(ManagedAgentProviderProfile, profile_id)
            if profile is None:
                raise OmnigentOAuthHostError("Provider Profile does not exist")
            mount = self._mount_from_profile(profile)
            result = await session.execute(
                select(OmnigentOAuthHostBindingRecord).where(
                    OmnigentOAuthHostBindingRecord.provider_profile_id == profile_id
                )
            )
            record = result.scalar_one_or_none()
            if record is None:
                record = OmnigentOAuthHostBindingRecord(
                    binding_ref=f"omnigent-oauth:{profile_id}",
                    provider_profile_id=profile_id,
                    endpoint_ref=endpoint_ref,
                    harness="codex-native",
                    credential_mount_template_json=mount.model_dump(
                        by_alias=True, mode="json"
                    ),
                    static_host_id=static_host_id,
                    host_launch_profile_ref=host_launch_profile_ref,
                    execution_profile_ref=execution_profile_ref,
                    launch_policy_ref=launch_policy_ref,
                    effective_launch_snapshot_json=effective_launch_snapshot,
                )
                session.add(record)
            else:
                record.endpoint_ref = endpoint_ref
                record.harness = "codex-native"
                record.credential_mount_template_json = mount.model_dump(
                    by_alias=True, mode="json"
                )
                record.static_host_id = static_host_id
                record.host_launch_profile_ref = host_launch_profile_ref
                if effective_launch_snapshot is not None:
                    record.execution_profile_ref = execution_profile_ref
                    record.launch_policy_ref = launch_policy_ref
                    record.effective_launch_snapshot_json = effective_launch_snapshot
            await session.commit()
            await session.refresh(record)
            return self._binding_model(record, profile)

    async def validate_binding(self, binding_ref: str) -> OmnigentOAuthHostBinding:
        async with self._session_factory() as session:
            result = await session.execute(
                select(OmnigentOAuthHostBindingRecord, ManagedAgentProviderProfile)
                .join(
                    ManagedAgentProviderProfile,
                    ManagedAgentProviderProfile.profile_id
                    == OmnigentOAuthHostBindingRecord.provider_profile_id,
                )
                .where(OmnigentOAuthHostBindingRecord.binding_ref == binding_ref)
            )
            row = result.first()
            if row is None:
                raise OmnigentOAuthHostError("host binding does not exist")
            return self._binding_model(row[0], row[1])

    async def refresh_binding_generation(
        self, profile_id: str
    ) -> OmnigentOAuthHostBinding | None:
        """Refresh a binding's safe mount ref after verified reconnect."""

        async with self._session_factory() as session:
            profile = await session.get(ManagedAgentProviderProfile, profile_id)
            record = (
                await session.execute(
                    select(OmnigentOAuthHostBindingRecord).where(
                        OmnigentOAuthHostBindingRecord.provider_profile_id == profile_id
                    )
                )
            ).scalar_one_or_none()
            if profile is None or record is None:
                return None
            record.credential_mount_template_json = self._mount_from_profile(
                profile
            ).model_dump(by_alias=True, mode="json")
            await session.commit()
            await session.refresh(record)
            return self._binding_model(record, profile)

    async def create_or_get_host_lease(
        self,
        *,
        binding: OmnigentOAuthHostBinding,
        provider_lease_id: str,
        holder_workflow_id: str,
        agent_run_id: str | None,
        idempotency_key: str,
        lease_purpose: str = "execution_omnigent",
        ttl_seconds: int = 5400,
    ) -> OmnigentHostLease:
        now = datetime.now(UTC)
        lease_id = deterministic_host_lease_id(provider_lease_id)
        mount = binding.credential_mount_ref
        async with self._session_factory() as session:
            existing = await session.get(OmnigentOAuthHostLeaseRecord, lease_id)
            if existing is None:
                existing = (
                    await session.execute(
                        select(OmnigentOAuthHostLeaseRecord).where(
                            OmnigentOAuthHostLeaseRecord.idempotency_key
                            == idempotency_key
                        )
                    )
                ).scalar_one_or_none()
            if existing is not None:
                if (
                    existing.provider_profile_id != binding.provider_profile_id
                    or existing.provider_lease_id != provider_lease_id
                    or existing.binding_ref != binding.binding_ref
                ):
                    raise OmnigentOAuthHostError(
                        "idempotency key is already bound to another host lease"
                    )
                if existing.effective_launch_snapshot_json != binding.effective_launch_snapshot:
                    raise OmnigentOAuthHostError(
                        "retry launch snapshot does not match the durable host lease",
                        code="OMNIGENT_LAUNCH_SNAPSHOT_CONFLICT",
                    )
                return self._lease_model(existing)
            record = OmnigentOAuthHostLeaseRecord(
                lease_id=lease_id,
                provider_profile_id=binding.provider_profile_id,
                provider_lease_id=provider_lease_id,
                binding_ref=binding.binding_ref,
                credential_generation=mount.auth_volume_ref.credential_generation,
                holder_workflow_id=holder_workflow_id,
                agent_run_id=agent_run_id,
                idempotency_key=idempotency_key,
                lease_purpose=lease_purpose,
                effective_launch_snapshot_json=binding.effective_launch_snapshot,
                container_name=(
                    deterministic_host_container_name(lease_id)
                    if binding.host_launch_profile_ref
                    else None
                ),
                status="allocating",
                acquired_at=now,
                last_heartbeat_at=now,
                expires_at=now + timedelta(seconds=max(60, ttl_seconds)),
            )
            session.add(record)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                winner = (
                    await session.execute(
                        select(OmnigentOAuthHostLeaseRecord).where(
                            OmnigentOAuthHostLeaseRecord.idempotency_key
                            == idempotency_key
                        )
                    )
                ).scalar_one_or_none()
                if winner is None:
                    raise
                record = winner
            await session.refresh(record)
            return self._lease_model(record)

    @staticmethod
    def _lease_model(record: OmnigentOAuthHostLeaseRecord) -> OmnigentHostLease:
        return OmnigentHostLease(
            leaseId=record.lease_id,
            providerProfileId=record.provider_profile_id,
            providerLeaseId=record.provider_lease_id,
            bindingRef=record.binding_ref,
            credentialGeneration=record.credential_generation,
            containerId=record.container_id,
            containerName=record.container_name,
            omnigentHostId=record.omnigent_host_id,
            omnigentSessionId=record.omnigent_session_id,
            bridgeSessionId=record.bridge_session_id,
            effectiveLaunchSnapshot=record.effective_launch_snapshot_json,
            status=record.status,
            acquiredAt=record.acquired_at,
            lastHeartbeatAt=record.last_heartbeat_at,
            expiresAt=record.expires_at,
        )

    async def transition_host_lease(
        self,
        lease_id: str,
        *,
        expected_status: str,
        new_status: str,
        fields: Mapping[str, Any] | None = None,
    ) -> OmnigentHostLease:
        if new_status not in _TRANSITIONS.get(expected_status, frozenset()):
            raise ValueError(
                f"invalid host lease transition {expected_status}->{new_status}"
            )
        values = dict(fields or {})
        now = datetime.now(UTC)
        values.update(status=new_status, last_heartbeat_at=now)
        timestamp_field = {
            "ready": "ready_at",
            "assigned": "assigned_at",
            "draining": "draining_at",
            "stopped": "stopped_at",
        }.get(new_status)
        if timestamp_field:
            values.setdefault(timestamp_field, now)
        async with self._session_factory() as session:
            result = await session.execute(
                update(OmnigentOAuthHostLeaseRecord)
                .where(
                    OmnigentOAuthHostLeaseRecord.lease_id == lease_id,
                    OmnigentOAuthHostLeaseRecord.status == expected_status,
                )
                .values(**values)
            )
            if result.rowcount != 1:
                raise OmnigentOAuthHostError("host lease state changed concurrently")
            await session.commit()
            record = await session.get(OmnigentOAuthHostLeaseRecord, lease_id)
            assert record is not None
            return self._lease_model(record)

    async def heartbeat_host_lease(
        self, lease_id: str, *, ttl_seconds: int = 5400
    ) -> OmnigentHostLease:
        now = datetime.now(UTC)
        async with self._session_factory() as session:
            record = await session.get(OmnigentOAuthHostLeaseRecord, lease_id)
            if record is None or record.status not in ACTIVE_HOST_STATES:
                raise OmnigentOAuthHostError("active host lease does not exist")
            record.last_heartbeat_at = now
            record.expires_at = now + timedelta(seconds=max(60, ttl_seconds))
            await session.commit()
            await session.refresh(record)
            return self._lease_model(record)

    async def restart_host_lease(
        self, lease_id: str, *, ttl_seconds: int = 5400
    ) -> OmnigentHostLease:
        """Reuse the same idempotent lease identity after terminal cleanup."""

        now = datetime.now(UTC)
        async with self._session_factory() as session:
            record = await session.get(OmnigentOAuthHostLeaseRecord, lease_id)
            if record is None or record.status not in TERMINAL_HOST_STATES:
                raise OmnigentOAuthHostError("terminal host lease does not exist")
            record.status = "starting"
            record.last_heartbeat_at = now
            record.expires_at = now + timedelta(seconds=max(60, ttl_seconds))
            record.error_code = None
            record.error_summary = None
            record.cleanup_completed_at = None
            record.container_id = None
            record.omnigent_host_id = None
            record.omnigent_session_id = None
            record.bridge_session_id = None
            record.host_auth_profile_id = None
            record.host_auth_generation = None
            record.ready_at = None
            record.assigned_at = None
            record.draining_at = None
            record.stopped_at = None
            await session.commit()
            await session.refresh(record)
            return self._lease_model(record)

    async def mark_host_lease_failed(
        self, lease_id: str, *, code: str, summary: str
    ) -> OmnigentHostLease:
        async with self._session_factory() as session:
            record = await session.get(OmnigentOAuthHostLeaseRecord, lease_id)
            if record is None:
                raise OmnigentOAuthHostError("host lease does not exist")
            record.status = "failed"
            record.error_code = code[:96]
            record.error_summary = redact_sensitive_text(summary)[:512]
            record.last_heartbeat_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(record)
            return self._lease_model(record)

    async def mark_host_lease_stopped(
        self, lease_id: str, *, cleanup_completed: bool = True
    ) -> OmnigentHostLease:
        now = datetime.now(UTC)
        async with self._session_factory() as session:
            record = await session.get(OmnigentOAuthHostLeaseRecord, lease_id)
            if record is None:
                raise OmnigentOAuthHostError("host lease does not exist")
            record.status = "stopped"
            record.stopped_at = record.stopped_at or now
            record.last_heartbeat_at = now
            if cleanup_completed:
                record.cleanup_completed_at = now
            await session.commit()
            await session.refresh(record)
            return self._lease_model(record)

    async def list_expired_host_leases(
        self, *, now: datetime | None = None
    ) -> list[OmnigentHostLease]:
        cutoff = now or datetime.now(UTC)
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(OmnigentOAuthHostLeaseRecord).where(
                        OmnigentOAuthHostLeaseRecord.status.in_(ACTIVE_HOST_STATES),
                        OmnigentOAuthHostLeaseRecord.expires_at <= cutoff,
                    )
                )
            ).scalars()
            return [self._lease_model(row) for row in rows]

    async def list_active_host_leases_for_profile(
        self, profile_id: str
    ) -> list[OmnigentHostLease]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(OmnigentOAuthHostLeaseRecord).where(
                        OmnigentOAuthHostLeaseRecord.provider_profile_id == profile_id,
                        OmnigentOAuthHostLeaseRecord.status.in_(ACTIVE_HOST_STATES),
                    )
                )
            ).scalars()
            return [self._lease_model(row) for row in rows]

    async def list_active_host_leases(self) -> list[OmnigentHostLease]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(OmnigentOAuthHostLeaseRecord).where(
                        OmnigentOAuthHostLeaseRecord.status.in_(ACTIVE_HOST_STATES)
                    )
                )
            ).scalars()
            return [self._lease_model(row) for row in rows]

    async def mark_generation_stale(
        self, *, profile_id: str, credential_generation: int
    ) -> list[str]:
        async with self._session_factory() as session:
            rows = list(
                (
                    await session.execute(
                        select(OmnigentOAuthHostLeaseRecord).where(
                            OmnigentOAuthHostLeaseRecord.provider_profile_id
                            == profile_id,
                            OmnigentOAuthHostLeaseRecord.status.in_(ACTIVE_HOST_STATES),
                            OmnigentOAuthHostLeaseRecord.credential_generation
                            != credential_generation,
                        )
                    )
                ).scalars()
            )
            for row in rows:
                row.status = "failed"
                row.error_code = HostPreflightFailure.GENERATION_STALE.value
                row.error_summary = "credential generation changed"
            await session.commit()
            return [row.lease_id for row in rows]


def validate_preflight_result(
    *,
    result: Mapping[str, Any],
    binding: OmnigentOAuthHostBinding,
    host_lease: OmnigentHostLease,
) -> dict[str, Any]:
    """Fail closed unless destination-host evidence matches the exact binding."""

    expected = binding.credential_mount_ref.auth_volume_ref
    checks = {
        "providerProfileId": binding.provider_profile_id,
        "runtimeId": "codex_cli",
        "providerId": "openai",
        "credentialGeneration": host_lease.credential_generation,
        "mountPath": "/home/app/.codex",
        "runtimeUid": 1000,
        "runtimeGid": 1000,
        "harness": "codex-native",
        "competingCredentialsPresent": False,
        "loginStatus": "authenticated",
    }
    for key, value in checks.items():
        if result.get(key) != value:
            code = (
                HostPreflightFailure.GENERATION_STALE.value
                if key == "credentialGeneration"
                else HostPreflightFailure.BINDING_MISMATCH.value
            )
            raise OmnigentOAuthHostError(
                f"host preflight field {key} did not match expected binding",
                code=code,
            )
    host_id = str(result.get("hostId") or "").strip()
    expected_host_id = binding.static_host_id or host_lease.omnigent_host_id
    if not host_id or (expected_host_id and host_id != expected_host_id):
        raise OmnigentOAuthHostError(
            "registered host ID did not match binding",
            code=HostPreflightFailure.BINDING_MISMATCH.value,
        )
    return {
        "status": "ready",
        **checks,
        "hostId": host_id,
        "volumeRef": expected.volume_ref,
    }


__all__ = [
    "ACTIVE_HOST_STATES",
    "HostPreflightFailure",
    "OmnigentOAuthHostError",
    "OmnigentOAuthHostRepository",
    "deterministic_host_container_name",
    "deterministic_host_lease_id",
    "validate_preflight_result",
]
