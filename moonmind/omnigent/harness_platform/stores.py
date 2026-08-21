"""Durable stores for execution plans and runtime bindings (Phase 1).

Provides both DB-backed and in-memory implementations so unit tests remain
hermetic while production uses SQLAlchemy. The stores enforce:

- Plan is immutable, digest-addressed, secret-free, persisted before leases
- Runtime binding is staged, fenced, immutable core (planRef + generations)
- Retries load the same plan via planRef
- Workflow input cannot author executionRealizerRef (trusted planner only)
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Protocol

from moonmind.omnigent.harness_platform.execution_plan import (
    OmnigentExecutionPlanEnvelope,
    verify_execution_plan_envelope,
)
from moonmind.omnigent.harness_platform.runtime_binding import (
    OmnigentRuntimeBinding,
    create_runtime_binding,
)
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)


class OmnigentExecutionPlanStore(Protocol):
    async def load(self, plan_ref: str) -> OmnigentExecutionPlanEnvelope | None: pass  # noqa

    async def load_or_compile(
        self,
        *,
        compile_fn: Any,
        compile_kwargs: dict[str, Any],
    ) -> OmnigentExecutionPlanEnvelope: pass  # noqa

    async def persist(self, envelope: OmnigentExecutionPlanEnvelope) -> OmnigentExecutionPlanEnvelope: pass  # noqa


@dataclass(frozen=True)
class ExecutionPlanUsageIdentity:
    workflow_id: str
    step_execution_id: str
    idempotency_key: str

    def usage_id(self) -> str:
        canonical = "\0".join(
            (self.workflow_id, self.step_execution_id, self.idempotency_key)
        )
        return (
            "omnigent-plan-usage:sha256:"
            + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        )


def execution_request_digest(request_payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        request_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


class OmnigentExecutionPlanUsageStore(Protocol):
    async def load_or_bind(
        self,
        *,
        identity: ExecutionPlanUsageIdentity,
        request_payload: dict[str, Any],
        compile_fn: Any,
    ) -> OmnigentExecutionPlanEnvelope: ...


class OmnigentRuntimeBindingStore(Protocol):
    async def get(self, runtime_binding_ref: str) -> OmnigentRuntimeBinding | None: pass  # noqa

    async def create_initial(
        self,
        *,
        execution_plan_ref: str,
        provider_leases: dict[str, dict[str, Any]],
        credential_handles: dict[str, dict[str, Any]] | None = None,
    ) -> OmnigentRuntimeBinding: pass  # noqa

    async def latest_for_plan(
        self, execution_plan_ref: str
    ) -> OmnigentRuntimeBinding | None: pass  # noqa

    async def update_with_host(
        self,
        runtime_binding_ref: str,
        *,
        host_binding_ref: str,
        host_lease_ref: str,
        host_lease_generation: int,
        omnigent_host_id: str,
        host_harness_attestation_ref: str | None = None,
        exact_host_capability_decision_ref: str | None = None,
        workspace_resolution_ref: str | None = None,
        model_option_attestation_ref: str | None = None,
        skill_delivery_attestation_ref: str | None = None,
    ) -> OmnigentRuntimeBinding: pass  # noqa

    async def update_with_session(
        self,
        runtime_binding_ref: str,
        *,
        omnigent_session_id: str,
    ) -> OmnigentRuntimeBinding: pass  # noqa


class InMemoryExecutionPlanStore:
    """Hermetic store for tests and local dev without DB."""

    def __init__(self) -> None:
        self._plans: dict[str, OmnigentExecutionPlanEnvelope] = {}

    async def load(self, plan_ref: str) -> OmnigentExecutionPlanEnvelope | None:
        return self._plans.get(plan_ref)

    async def persist(self, envelope: OmnigentExecutionPlanEnvelope) -> OmnigentExecutionPlanEnvelope:
        # Verify envelope before persist (fail closed on digest mismatch)
        verify_execution_plan_envelope(envelope)
        # Secret-free check already enforced by execution_plan model validators
        existing = self._plans.get(envelope.planRef)
        if existing is not None and existing != envelope:
            raise HarnessPlatformError(
                f"execution plan conflict for {envelope.planRef}",
                code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
            )
        self._plans[envelope.planRef] = envelope
        return envelope

    async def load_or_compile(
        self,
        *,
        compile_fn: Any,
        compile_kwargs: dict[str, Any],
    ) -> OmnigentExecutionPlanEnvelope:
        # Workflow input must not author executionRealizerRef – strip so trusted planner selects
        compile_kwargs.pop("execution_realizer_ref", None)
        compile_kwargs.pop("executionRealizerRef", None)
        envelope: OmnigentExecutionPlanEnvelope = compile_fn(**compile_kwargs)
        existing = self._plans.get(envelope.planRef)
        if existing is not None:
            return existing
        return await self.persist(envelope)


class InMemoryExecutionPlanUsageStore:
    """Retry-stable identity binding used only by hermetic tests."""

    def __init__(self, plan_store: OmnigentExecutionPlanStore) -> None:
        self._plan_store = plan_store
        self._usages: dict[str, tuple[str, str]] = {}

    async def load_or_bind(
        self,
        *,
        identity: ExecutionPlanUsageIdentity,
        request_payload: dict[str, Any],
        compile_fn: Any,
    ) -> OmnigentExecutionPlanEnvelope:
        request_digest = execution_request_digest(request_payload)
        existing = self._usages.get(identity.idempotency_key)
        if existing is not None:
            previous_digest, plan_ref = existing
            if previous_digest != request_digest:
                raise HarnessPlatformError(
                    "idempotency key is already bound to a different Omnigent request",
                    code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
                )
            plan = await self._plan_store.load(plan_ref)
            if plan is None:
                raise HarnessPlatformError(
                    "execution-plan usage references a missing immutable plan",
                    code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
                )
            return plan
        envelope = await compile_fn()
        envelope = await self._plan_store.persist(envelope)
        self._usages[identity.idempotency_key] = (request_digest, envelope.planRef)
        return envelope


class InMemoryRuntimeBindingStore:
    """Staged, fenced runtime binding store (in-memory)."""

    def __init__(self) -> None:
        self._bindings: dict[str, OmnigentRuntimeBinding] = {}
        # Also index by planRef for lookup
        self._by_plan: dict[str, list[str]] = {}

    async def get(self, runtime_binding_ref: str) -> OmnigentRuntimeBinding | None:
        return self._bindings.get(runtime_binding_ref)

    async def create_initial(
        self,
        *,
        execution_plan_ref: str,
        provider_leases: dict[str, dict[str, Any]],
        credential_handles: dict[str, dict[str, Any]] | None = None,
    ) -> OmnigentRuntimeBinding:
        existing = await self.latest_for_plan(execution_plan_ref)
        if existing is not None:
            expected = {
                slot: value.model_dump(by_alias=True, mode="json")
                for slot, value in existing.providerLeases.items()
            }
            if expected != provider_leases:
                raise HarnessPlatformError(
                    "runtime binding provider lease authority changed on retry",
                    code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
                )
            expected_cleanup_refs = tuple(
                sorted(
                    str(handle.get("cleanupRef"))
                    for handle in (credential_handles or {}).values()
                    if handle.get("cleanupRef")
                )
            )
            if existing.cleanupAuthorityRefs != expected_cleanup_refs:
                raise HarnessPlatformError(
                    "runtime binding cleanup authority changed on retry",
                    code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
                )
            return existing
        binding = create_runtime_binding(
            executionPlanRef=execution_plan_ref,
            providerLeases=provider_leases,
            cleanupAuthorityRefs=sorted(
                str(handle.get("cleanupRef"))
                for handle in (credential_handles or {}).values()
                if handle.get("cleanupRef")
            ),
        )
        # Immutable core cannot be mutated after creation
        self._bindings[binding.runtimeBindingRef] = binding
        self._by_plan.setdefault(execution_plan_ref, []).append(binding.runtimeBindingRef)
        return binding

    async def latest_for_plan(
        self, execution_plan_ref: str
    ) -> OmnigentRuntimeBinding | None:
        refs = self._by_plan.get(execution_plan_ref) or []
        return self._bindings.get(refs[-1]) if refs else None

    async def update_with_host(
        self,
        runtime_binding_ref: str,
        *,
        host_binding_ref: str,
        host_lease_ref: str,
        host_lease_generation: int,
        omnigent_host_id: str,
        host_harness_attestation_ref: str | None = None,
        exact_host_capability_decision_ref: str | None = None,
        workspace_resolution_ref: str | None = None,
        model_option_attestation_ref: str | None = None,
        skill_delivery_attestation_ref: str | None = None,
    ) -> OmnigentRuntimeBinding:
        existing = self._bindings.get(runtime_binding_ref)
        if existing is None:
            raise HarnessPlatformError(
                f"runtime binding {runtime_binding_ref} not found",
                code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
            )
        # Cannot mutate plan decisions or acquired generations; only add host info
        # Re-create with new host fields but preserve providerLeases (immutable)
        updated = create_runtime_binding(
            executionPlanRef=existing.executionPlanRef,
            providerLeases={k: v.model_dump(by_alias=True, mode="json") for k, v in existing.providerLeases.items()},
            hostBindingRef=host_binding_ref,
            hostLeaseRef=host_lease_ref,
            hostLeaseGeneration=host_lease_generation,
            omnigentHostId=omnigent_host_id,
            hostHarnessAttestationRef=host_harness_attestation_ref,
            exactHostCapabilityDecisionRef=exact_host_capability_decision_ref,
            workspaceResolutionRef=workspace_resolution_ref,
            modelOptionAttestationRef=model_option_attestation_ref,
            skillDeliveryAttestationRef=skill_delivery_attestation_ref,
            omnigentSessionId=existing.omnigentSessionId,
            cleanupAuthorityRefs=list(existing.cleanupAuthorityRefs),
        )
        # New ref differs because host fields are part of digest; we store under new ref
        # but also keep old for history; return new
        self._bindings[updated.runtimeBindingRef] = updated
        self._by_plan.setdefault(existing.executionPlanRef, []).append(
            updated.runtimeBindingRef
        )
        # Do not delete old; keep for audit
        return updated

    async def update_with_session(
        self,
        runtime_binding_ref: str,
        *,
        omnigent_session_id: str,
    ) -> OmnigentRuntimeBinding:
        existing = self._bindings.get(runtime_binding_ref)
        if existing is None:
            raise HarnessPlatformError(
                f"runtime binding {runtime_binding_ref} not found",
                code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
            )
        updated = create_runtime_binding(
            executionPlanRef=existing.executionPlanRef,
            providerLeases={k: v.model_dump(by_alias=True, mode="json") for k, v in existing.providerLeases.items()},
            hostBindingRef=existing.hostBindingRef,
            hostLeaseRef=existing.hostLeaseRef,
            hostLeaseGeneration=existing.hostLeaseGeneration,
            omnigentHostId=existing.omnigentHostId,
            hostHarnessAttestationRef=existing.hostHarnessAttestationRef,
            exactHostCapabilityDecisionRef=existing.exactHostCapabilityDecisionRef,
            workspaceResolutionRef=existing.workspaceResolutionRef,
            modelOptionAttestationRef=existing.modelOptionAttestationRef,
            skillDeliveryAttestationRef=existing.skillDeliveryAttestationRef,
            omnigentSessionId=omnigent_session_id,
            cleanupAuthorityRefs=list(existing.cleanupAuthorityRefs),
        )
        self._bindings[updated.runtimeBindingRef] = updated
        self._by_plan.setdefault(existing.executionPlanRef, []).append(
            updated.runtimeBindingRef
        )
        return updated


# DB-backed implementations (thin wrappers around models) – used in production

class DbExecutionPlanStore:
    """DB-backed store using OmnigentExecutionPlanRecord."""

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def load(self, plan_ref: str) -> OmnigentExecutionPlanEnvelope | None:
        from api_service.db.models import OmnigentExecutionPlanRecord

        async with self._session_factory() as session:
            record = await session.get(OmnigentExecutionPlanRecord, plan_ref)
            if record is None:
                return None
            return OmnigentExecutionPlanEnvelope.model_validate(
                {"schemaVersion": record.schema_version, "planRef": record.plan_ref, "payload": record.payload_json}
            )

    async def persist(self, envelope: OmnigentExecutionPlanEnvelope) -> OmnigentExecutionPlanEnvelope:
        from api_service.db.models import OmnigentExecutionPlanRecord
        from sqlalchemy.exc import IntegrityError

        verify_execution_plan_envelope(envelope)
        async with self._session_factory() as session:
            existing = await session.get(OmnigentExecutionPlanRecord, envelope.planRef)
            if existing is not None:
                # Verify existing payload matches
                if existing.payload_json != envelope.payload.model_dump(by_alias=True, mode="json"):
                    raise HarnessPlatformError(
                        f"execution plan conflict for {envelope.planRef}",
                        code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
                    )
                return envelope
            record = OmnigentExecutionPlanRecord(
                plan_ref=envelope.planRef,
                schema_version=envelope.schemaVersion,
                payload_json=envelope.payload.model_dump(by_alias=True, mode="json"),
                agent_profile_snapshot_ref=envelope.payload.agentProfileSnapshotRef,
                credential_binding_set_ref=envelope.payload.credentialBindingSetRef,
                harness_id=envelope.payload.harnessId,
                harness_implementation_ref=envelope.payload.harnessImplementationRef,
                host_class_ref=envelope.payload.hostClassRef,
                launch_policy_ref=envelope.payload.launchPolicyRef,
                execution_realizer_ref=envelope.payload.executionRealizerRef,
                support_combination_key=envelope.payload.supportCombinationKey,
            )
            session.add(record)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                # Race: another worker persisted same plan
                loaded = await self.load(envelope.planRef)
                if loaded is not None:
                    return loaded
                raise HarnessPlatformError(
                    f"failed to persist execution plan: {exc}",
                    code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
                ) from exc
            return envelope

    @staticmethod
    async def persist_in_session(
        session: Any,
        envelope: OmnigentExecutionPlanEnvelope,
    ) -> OmnigentExecutionPlanEnvelope:
        """Flush a plan inside an API caller's admission transaction.

        The caller commits the execution record and plan together before
        Temporal start.  This avoids a window in which a workflow can launch
        without the immutable authority it names.
        """

        from api_service.db.models import OmnigentExecutionPlanRecord

        verify_execution_plan_envelope(envelope)
        existing = await session.get(OmnigentExecutionPlanRecord, envelope.planRef)
        payload = envelope.payload.model_dump(by_alias=True, mode="json")
        if existing is not None:
            if existing.payload_json != payload:
                raise HarnessPlatformError(
                    f"execution plan conflict for {envelope.planRef}",
                    code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
                )
            return envelope
        session.add(
            OmnigentExecutionPlanRecord(
                plan_ref=envelope.planRef,
                schema_version=envelope.schemaVersion,
                payload_json=payload,
                agent_profile_snapshot_ref=envelope.payload.agentProfileSnapshotRef,
                credential_binding_set_ref=envelope.payload.credentialBindingSetRef,
                harness_id=envelope.payload.harnessId,
                harness_implementation_ref=envelope.payload.harnessImplementationRef,
                host_class_ref=envelope.payload.hostClassRef,
                launch_policy_ref=envelope.payload.launchPolicyRef,
                execution_realizer_ref=envelope.payload.executionRealizerRef,
                support_combination_key=envelope.payload.supportCombinationKey,
            )
        )
        await session.flush()
        return envelope

    async def load_or_compile(
        self,
        *,
        compile_fn: Any,
        compile_kwargs: dict[str, Any],
    ) -> OmnigentExecutionPlanEnvelope:
        envelope: OmnigentExecutionPlanEnvelope = compile_fn(**compile_kwargs)
        existing = await self.load(envelope.planRef)
        if existing is not None:
            return existing
        return await self.persist(envelope)


class DbExecutionPlanUsageStore:
    """Atomically persist and bind the first plan selected for an execution."""

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def load_or_bind(
        self,
        *,
        identity: ExecutionPlanUsageIdentity,
        request_payload: dict[str, Any],
        compile_fn: Any,
    ) -> OmnigentExecutionPlanEnvelope:
        from sqlalchemy import select
        from sqlalchemy.exc import IntegrityError

        from api_service.db.models import (
            OmnigentExecutionPlanRecord,
            OmnigentExecutionPlanUsageRecord,
        )

        request_digest = execution_request_digest(request_payload)

        async def load_existing() -> OmnigentExecutionPlanEnvelope | None:
            async with self._session_factory() as read_session:
                usage = (
                    await read_session.execute(
                        select(OmnigentExecutionPlanUsageRecord).where(
                            OmnigentExecutionPlanUsageRecord.idempotency_key
                            == identity.idempotency_key
                        )
                    )
                ).scalar_one_or_none()
                if usage is None:
                    return None
                if usage.request_digest != request_digest:
                    raise HarnessPlatformError(
                        "idempotency key is already bound to a different Omnigent request",
                        code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
                    )
                plan = await read_session.get(
                    OmnigentExecutionPlanRecord, usage.plan_ref
                )
                if plan is None:
                    raise HarnessPlatformError(
                        "execution-plan usage references a missing immutable plan",
                        code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
                    )
                return OmnigentExecutionPlanEnvelope.model_validate(
                    {
                        "schemaVersion": plan.schema_version,
                        "planRef": plan.plan_ref,
                        "payload": plan.payload_json,
                    }
                )

        found = await load_existing()
        if found is not None:
            return found

        envelope: OmnigentExecutionPlanEnvelope = await compile_fn()
        verify_execution_plan_envelope(envelope)
        async with self._session_factory() as session:
            plan = await session.get(OmnigentExecutionPlanRecord, envelope.planRef)
            if plan is None:
                payload = envelope.payload
                session.add(
                    OmnigentExecutionPlanRecord(
                        plan_ref=envelope.planRef,
                        schema_version=envelope.schemaVersion,
                        payload_json=payload.model_dump(by_alias=True, mode="json"),
                        agent_profile_snapshot_ref=payload.agentProfileSnapshotRef,
                        credential_binding_set_ref=payload.credentialBindingSetRef,
                        harness_id=payload.harnessId,
                        harness_implementation_ref=payload.harnessImplementationRef,
                        host_class_ref=payload.hostClassRef,
                        launch_policy_ref=payload.launchPolicyRef,
                        execution_realizer_ref=payload.executionRealizerRef,
                        support_combination_key=payload.supportCombinationKey,
                    )
                )
            session.add(
                OmnigentExecutionPlanUsageRecord(
                    usage_id=identity.usage_id(),
                    workflow_id=identity.workflow_id,
                    step_execution_id=identity.step_execution_id,
                    idempotency_key=identity.idempotency_key,
                    plan_ref=envelope.planRef,
                    request_digest=request_digest,
                )
            )
            try:
                await session.commit()
                return envelope
            except IntegrityError as exc:
                await session.rollback()
                raced = await load_existing()
                if raced is not None:
                    return raced
                raise HarnessPlatformError(
                    "failed to bind immutable execution plan usage",
                    code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
                ) from exc


__all__ = [
    "DbExecutionPlanStore",
    "DbExecutionPlanUsageStore",
    "ExecutionPlanUsageIdentity",
    "InMemoryExecutionPlanStore",
    "InMemoryExecutionPlanUsageStore",
    "InMemoryRuntimeBindingStore",
    "OmnigentExecutionPlanStore",
    "OmnigentExecutionPlanUsageStore",
    "OmnigentRuntimeBindingStore",
    "execution_request_digest",
]
