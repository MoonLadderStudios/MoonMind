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
    ) -> OmnigentExecutionPlanEnvelope:
        raise NotImplementedError


class OmnigentRuntimeBindingStore(Protocol):
    async def get(self, runtime_binding_ref: str) -> OmnigentRuntimeBinding | None: pass  # noqa

    async def get_current_state(
        self, execution_plan_ref: str, execution_scope_ref: str
    ) -> "RuntimeBindingStoreState | None": pass  # noqa

    async def get_state_for_host_lease(
        self, host_lease_ref: str
    ) -> "RuntimeBindingStoreState | None": pass  # noqa

    async def get_current_state_for_execution_scope(
        self, execution_scope_ref: str
    ) -> "RuntimeBindingStoreState | None": pass  # noqa

    async def create_initial(
        self,
        *,
        execution_plan_ref: str,
        execution_scope_ref: str,
        provider_leases: dict[str, dict[str, Any]],
    ) -> OmnigentRuntimeBinding: pass  # noqa

    async def reconcile_provider_leases(
        self,
        runtime_binding_ref: str,
        *,
        provider_leases: dict[str, dict[str, Any]],
        expected_revision: int,
        expected_fencing_generation: int,
    ) -> OmnigentRuntimeBinding: pass  # noqa

    async def mark_cleanup_complete(
        self,
        runtime_binding_ref: str,
        *,
        expected_revision: int,
        expected_fencing_generation: int,
    ) -> OmnigentRuntimeBinding: pass  # noqa

    async def update_with_host(
        self,
        runtime_binding_ref: str,
        *,
        host_binding_ref: str,
        host_lease_ref: str,
        host_lease_generation: int,
        omnigent_host_id: str,
        host_harness_attestation_ref: str,
        exact_host_capability_decision_ref: str,
        workspace_resolution_ref: str,
        model_option_attestation_ref: str,
        skill_delivery_attestation_ref: str,
        cleanup_authority_refs: list[str],
        expected_revision: int,
        expected_fencing_generation: int,
    ) -> OmnigentRuntimeBinding: pass  # noqa

    async def update_with_session(
        self,
        runtime_binding_ref: str,
        *,
        omnigent_session_id: str,
        omnigent_runner_ref: str | None,
        chat_binding_ref: str,
        expected_revision: int,
        expected_fencing_generation: int,
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


@dataclass(frozen=True)
class RuntimeBindingStoreState:
    binding: OmnigentRuntimeBinding
    revision: int
    fencing_generation: int
    state: str
    execution_scope_ref: str


class InMemoryRuntimeBindingStore:
    """Staged, fenced runtime binding store (in-memory)."""

    def __init__(self) -> None:
        self._bindings: dict[str, OmnigentRuntimeBinding] = {}
        # Also index by planRef for lookup
        self._by_plan: dict[str, list[str]] = {}
        self._state: dict[str, RuntimeBindingStoreState] = {}
        self._current_by_scope: dict[tuple[str, str], str] = {}

    async def get(self, runtime_binding_ref: str) -> OmnigentRuntimeBinding | None:
        return self._bindings.get(runtime_binding_ref)

    async def create_initial(
        self,
        *,
        execution_plan_ref: str,
        execution_scope_ref: str,
        provider_leases: dict[str, dict[str, Any]],
        host_binding_ref: str | None = None,
        host_lease_ref: str | None = None,
        host_lease_generation: int | None = None,
        omnigent_host_id: str | None = None,
    ) -> OmnigentRuntimeBinding:
        if not str(execution_scope_ref or "").strip():
            raise ValueError("execution_scope_ref is required")
        binding = create_runtime_binding(
            executionPlanRef=execution_plan_ref,
            executionScopeRef=execution_scope_ref,
            providerLeases=provider_leases,
            hostBindingRef=host_binding_ref,
            hostLeaseRef=host_lease_ref,
            hostLeaseGeneration=host_lease_generation,
            omnigentHostId=omnigent_host_id,
        )
        current = await self.get_current_state(
            execution_plan_ref, execution_scope_ref
        )
        if current is not None:
            if current.binding.providerLeases != binding.providerLeases:
                raise HarnessPlatformError(
                    "execution plan already has different acquired generations",
                    code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
                )
            return current.binding
        # Immutable core cannot be mutated after creation
        self._bindings[binding.runtimeBindingRef] = binding
        self._by_plan.setdefault(execution_plan_ref, []).append(binding.runtimeBindingRef)
        self._state[binding.runtimeBindingRef] = RuntimeBindingStoreState(
            binding=binding,
            revision=1,
            fencing_generation=1,
            state="credentials_acquired",
            execution_scope_ref=execution_scope_ref,
        )
        self._current_by_scope[(execution_plan_ref, execution_scope_ref)] = (
            binding.runtimeBindingRef
        )
        return binding

    async def get_state(
        self, runtime_binding_ref: str
    ) -> RuntimeBindingStoreState | None:
        return self._state.get(runtime_binding_ref)

    async def get_current_state(
        self, execution_plan_ref: str, execution_scope_ref: str
    ) -> RuntimeBindingStoreState | None:
        current_ref = self._current_by_scope.get(
            (execution_plan_ref, execution_scope_ref)
        )
        return self._state.get(current_ref) if current_ref is not None else None

    async def get_state_for_host_lease(
        self, host_lease_ref: str
    ) -> RuntimeBindingStoreState | None:
        matches = [
            state
            for state in self._state.values()
            if state.binding.hostLeaseRef == host_lease_ref
            and self._current_by_scope.get(
                (
                    state.binding.executionPlanRef,
                    state.execution_scope_ref,
                )
            )
            == state.binding.runtimeBindingRef
        ]
        if len(matches) > 1:
            raise HarnessPlatformError(
                "host lease has conflicting runtime binding authority",
                code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
            )
        return matches[0] if matches else None

    async def get_current_state_for_execution_scope(
        self, execution_scope_ref: str
    ) -> RuntimeBindingStoreState | None:
        matches = [
            state
            for (_plan_ref, scope_ref), binding_ref in self._current_by_scope.items()
            if scope_ref == execution_scope_ref
            and (state := self._state.get(binding_ref)) is not None
        ]
        if len(matches) > 1:
            raise HarnessPlatformError(
                "execution scope has conflicting runtime binding authority",
                code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
            )
        return matches[0] if matches else None

    def _require_current(
        self,
        runtime_binding_ref: str,
        *,
        expected_revision: int,
        expected_fencing_generation: int,
    ) -> RuntimeBindingStoreState:
        current = self._state.get(runtime_binding_ref)
        if current is None or (
            self._current_by_scope.get(
                (
                    current.binding.executionPlanRef,
                    current.execution_scope_ref,
                )
            )
            != runtime_binding_ref
        ):
            raise HarnessPlatformError(
                f"runtime binding {runtime_binding_ref} is stale or unavailable",
                code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
            )
        if (
            current.revision != expected_revision
            or current.fencing_generation != expected_fencing_generation
        ):
            raise HarnessPlatformError(
                "runtime binding revision or fencing generation conflict",
                code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
            )
        return current

    def _advance(
        self,
        current: RuntimeBindingStoreState,
        updated: OmnigentRuntimeBinding,
        state: str,
        *,
        advance_fencing_generation: bool = False,
    ) -> OmnigentRuntimeBinding:
        next_state = RuntimeBindingStoreState(
            binding=updated,
            revision=current.revision + 1,
            fencing_generation=(
                current.fencing_generation + 1
                if advance_fencing_generation
                else current.fencing_generation
            ),
            state=state,
            execution_scope_ref=current.execution_scope_ref,
        )
        self._bindings[updated.runtimeBindingRef] = updated
        self._state[updated.runtimeBindingRef] = next_state
        self._current_by_scope[
            (updated.executionPlanRef, current.execution_scope_ref)
        ] = updated.runtimeBindingRef
        return updated

    async def reconcile_provider_leases(
        self,
        runtime_binding_ref: str,
        *,
        provider_leases: dict[str, dict[str, Any]],
        expected_revision: int,
        expected_fencing_generation: int,
    ) -> OmnigentRuntimeBinding:
        """Replace acquired live authority and fence every former owner."""

        current = self._require_current(
            runtime_binding_ref,
            expected_revision=expected_revision,
            expected_fencing_generation=expected_fencing_generation,
        )
        replacement = create_runtime_binding(
            executionPlanRef=current.binding.executionPlanRef,
            executionScopeRef=current.binding.executionScopeRef,
            providerLeases=provider_leases,
        )
        if replacement.providerLeases == current.binding.providerLeases:
            return current.binding
        return self._advance(
            current,
            replacement,
            "credentials_acquired",
            advance_fencing_generation=True,
        )

    async def mark_cleanup_complete(
        self,
        runtime_binding_ref: str,
        *,
        expected_revision: int,
        expected_fencing_generation: int,
    ) -> OmnigentRuntimeBinding:
        current = self._require_current(
            runtime_binding_ref,
            expected_revision=expected_revision,
            expected_fencing_generation=expected_fencing_generation,
        )
        return self._advance(current, current.binding, "cleanup_complete")

    async def update_with_host(
        self,
        runtime_binding_ref: str,
        *,
        host_binding_ref: str,
        host_lease_ref: str,
        host_lease_generation: int,
        omnigent_host_id: str,
        host_harness_attestation_ref: str,
        exact_host_capability_decision_ref: str,
        workspace_resolution_ref: str,
        model_option_attestation_ref: str,
        skill_delivery_attestation_ref: str,
        cleanup_authority_refs: list[str],
        expected_revision: int,
        expected_fencing_generation: int,
    ) -> OmnigentRuntimeBinding:
        current = self._require_current(
            runtime_binding_ref,
            expected_revision=expected_revision,
            expected_fencing_generation=expected_fencing_generation,
        )
        existing = current.binding
        replacing_host = bool(
            existing.hostBindingRef is not None
            and (
                existing.hostBindingRef != host_binding_ref
                or existing.hostLeaseRef != host_lease_ref
                or existing.hostLeaseGeneration != host_lease_generation
                or existing.omnigentHostId != omnigent_host_id
            )
        )
        # Cannot mutate plan decisions or acquired generations; only add host info
        # Re-create with new host fields but preserve providerLeases (immutable)
        updated = create_runtime_binding(
            executionPlanRef=existing.executionPlanRef,
            executionScopeRef=existing.executionScopeRef,
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
            omnigentRunnerRef=(
                None if replacing_host else existing.omnigentRunnerRef
            ),
            omnigentSessionId=(
                None if replacing_host else existing.omnigentSessionId
            ),
            chatBindingRef=(None if replacing_host else existing.chatBindingRef),
            cleanupAuthorityRefs=cleanup_authority_refs,
        )
        # New ref differs because host fields are part of digest; we store under new ref
        # but also keep old for history; return new
        return self._advance(
            current,
            updated,
            "host_attested",
            advance_fencing_generation=replacing_host,
        )

    async def update_with_session(
        self,
        runtime_binding_ref: str,
        *,
        omnigent_session_id: str,
        omnigent_runner_ref: str | None,
        chat_binding_ref: str,
        expected_revision: int,
        expected_fencing_generation: int,
    ) -> OmnigentRuntimeBinding:
        current = self._require_current(
            runtime_binding_ref,
            expected_revision=expected_revision,
            expected_fencing_generation=expected_fencing_generation,
        )
        existing = current.binding
        updated = create_runtime_binding(
            executionPlanRef=existing.executionPlanRef,
            executionScopeRef=existing.executionScopeRef,
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
            omnigentRunnerRef=omnigent_runner_ref,
            omnigentSessionId=omnigent_session_id,
            chatBindingRef=chat_binding_ref,
            cleanupAuthorityRefs=list(existing.cleanupAuthorityRefs),
        )
        return self._advance(current, updated, "session_bound")


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
                # Parse before comparison so optional v1 fields added after an
                # older plan was persisted do not manufacture a conflict.
                existing_envelope = OmnigentExecutionPlanEnvelope.model_validate(
                    {
                        "schemaVersion": existing.schema_version,
                        "planRef": existing.plan_ref,
                        "payload": existing.payload_json,
                    }
                )
                if existing_envelope != envelope:
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


class SessionExecutionPlanStore:
    """Persist a plan in an existing API transaction before scheduling."""

    def __init__(self, session: Any) -> None:
        self._session = session

    async def load(self, plan_ref: str) -> OmnigentExecutionPlanEnvelope | None:
        from api_service.db.models import OmnigentExecutionPlanRecord

        record = await self._session.get(OmnigentExecutionPlanRecord, plan_ref)
        if record is None:
            return None
        return OmnigentExecutionPlanEnvelope.model_validate(
            {
                "schemaVersion": record.schema_version,
                "planRef": record.plan_ref,
                "payload": record.payload_json,
            }
        )

    async def persist(
        self, envelope: OmnigentExecutionPlanEnvelope
    ) -> OmnigentExecutionPlanEnvelope:
        from api_service.db.models import OmnigentExecutionPlanRecord

        verify_execution_plan_envelope(envelope)
        existing = await self._session.get(
            OmnigentExecutionPlanRecord, envelope.planRef
        )
        payload = envelope.payload.model_dump(by_alias=True, mode="json")
        if existing is not None:
            existing_envelope = OmnigentExecutionPlanEnvelope.model_validate(
                {
                    "schemaVersion": existing.schema_version,
                    "planRef": existing.plan_ref,
                    "payload": existing.payload_json,
                }
            )
            if existing_envelope != envelope:
                raise HarnessPlatformError(
                    f"execution plan conflict for {envelope.planRef}",
                    code=(
                        HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT
                    ),
                )
            return envelope
        self._session.add(
            OmnigentExecutionPlanRecord(
                plan_ref=envelope.planRef,
                schema_version=envelope.schemaVersion,
                payload_json=payload,
                agent_profile_snapshot_ref=(
                    envelope.payload.agentProfileSnapshotRef
                ),
                credential_binding_set_ref=(
                    envelope.payload.credentialBindingSetRef
                ),
                harness_id=envelope.payload.harnessId,
                harness_implementation_ref=(
                    envelope.payload.harnessImplementationRef
                ),
                host_class_ref=envelope.payload.hostClassRef,
                launch_policy_ref=envelope.payload.launchPolicyRef,
                execution_realizer_ref=envelope.payload.executionRealizerRef,
                support_combination_key=(
                    envelope.payload.supportCombinationKey
                ),
            )
        )
        await self._session.flush()
        return envelope

    async def load_or_compile(
        self,
        *,
        compile_fn: Any,
        compile_kwargs: dict[str, Any],
    ) -> OmnigentExecutionPlanEnvelope:
        envelope = compile_fn(**compile_kwargs)
        existing = await self.load(envelope.planRef)
        if existing is not None:
            return existing
        return await self.persist(envelope)


class DbRuntimeBindingStore:
    """Revision-checked and fenced persistence for live runtime authority."""

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _binding_from_record(record: Any) -> OmnigentRuntimeBinding:
        attestations = dict(record.attestation_refs_json or {})
        return create_runtime_binding(
            executionPlanRef=record.execution_plan_ref,
            executionScopeRef=record.execution_scope_ref,
            providerLeases=dict(record.provider_leases_json or {}),
            hostBindingRef=record.host_binding_ref,
            hostLeaseRef=record.host_lease_ref,
            hostLeaseGeneration=record.host_lease_generation,
            omnigentHostId=record.omnigent_host_id,
            hostHarnessAttestationRef=attestations.get(
                "hostHarnessAttestationRef"
            ),
            exactHostCapabilityDecisionRef=attestations.get(
                "exactHostCapabilityDecisionRef"
            ),
            workspaceResolutionRef=attestations.get("workspaceResolutionRef"),
            modelOptionAttestationRef=attestations.get(
                "modelOptionAttestationRef"
            ),
            skillDeliveryAttestationRef=attestations.get(
                "skillDeliveryAttestationRef"
            ),
            omnigentRunnerRef=record.runner_ref,
            omnigentSessionId=record.session_id,
            chatBindingRef=record.chat_binding_ref,
            cleanupAuthorityRefs=list(record.cleanup_authority_refs_json or []),
        )

    async def get(self, runtime_binding_ref: str) -> OmnigentRuntimeBinding | None:
        state = await self.get_state(runtime_binding_ref)
        return state.binding if state is not None else None

    async def get_state(
        self, runtime_binding_ref: str
    ) -> RuntimeBindingStoreState | None:
        from api_service.db.models import OmnigentRuntimeBindingRecord

        async with self._session_factory() as session:
            record = await session.get(
                OmnigentRuntimeBindingRecord, runtime_binding_ref
            )
            if record is None:
                return None
            binding = self._binding_from_record(record)
            if binding.runtimeBindingRef != record.runtime_binding_ref:
                raise HarnessPlatformError(
                    "persisted runtime binding digest mismatch",
                    code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
                )
            return RuntimeBindingStoreState(
                binding=binding,
                revision=int(record.revision),
                fencing_generation=int(record.fencing_generation),
                state=str(record.state),
                execution_scope_ref=str(record.execution_scope_ref or ""),
            )

    async def get_current_state(
        self, execution_plan_ref: str, execution_scope_ref: str
    ) -> RuntimeBindingStoreState | None:
        from api_service.db.models import OmnigentRuntimeBindingRecord
        from sqlalchemy import select

        async with self._session_factory() as session:
            record = await session.scalar(
                select(OmnigentRuntimeBindingRecord)
                .where(
                    OmnigentRuntimeBindingRecord.execution_plan_ref
                    == execution_plan_ref,
                    OmnigentRuntimeBindingRecord.execution_scope_ref
                    == execution_scope_ref,
                )
                .order_by(
                    OmnigentRuntimeBindingRecord.revision.desc(),
                    OmnigentRuntimeBindingRecord.updated_at.desc(),
                )
                .limit(1)
            )
            if record is None:
                return None
            binding = self._binding_from_record(record)
            if binding.runtimeBindingRef != record.runtime_binding_ref:
                raise HarnessPlatformError(
                    "persisted runtime binding digest mismatch",
                    code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
                )
            return RuntimeBindingStoreState(
                binding=binding,
                revision=int(record.revision),
                fencing_generation=int(record.fencing_generation),
                state=str(record.state),
                execution_scope_ref=str(record.execution_scope_ref or ""),
            )

    async def get_state_for_host_lease(
        self, host_lease_ref: str
    ) -> RuntimeBindingStoreState | None:
        from api_service.db.models import OmnigentRuntimeBindingRecord
        from sqlalchemy import select

        async with self._session_factory() as session:
            records = list(
                (
                    await session.scalars(
                        select(OmnigentRuntimeBindingRecord).where(
                            OmnigentRuntimeBindingRecord.host_lease_ref
                            == host_lease_ref
                        )
                    )
                ).all()
            )
        if len(records) > 1:
            raise HarnessPlatformError(
                "host lease has conflicting runtime binding authority",
                code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
            )
        if not records:
            return None
        record = records[0]
        binding = self._binding_from_record(record)
        if binding.runtimeBindingRef != record.runtime_binding_ref:
            raise HarnessPlatformError(
                "persisted runtime binding digest mismatch",
                code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
            )
        return RuntimeBindingStoreState(
            binding=binding,
            revision=int(record.revision),
            fencing_generation=int(record.fencing_generation),
            state=str(record.state),
            execution_scope_ref=str(record.execution_scope_ref or ""),
        )

    async def get_current_state_for_execution_scope(
        self, execution_scope_ref: str
    ) -> RuntimeBindingStoreState | None:
        from api_service.db.models import OmnigentRuntimeBindingRecord
        from sqlalchemy import select

        async with self._session_factory() as session:
            records = list(
                (
                    await session.scalars(
                        select(OmnigentRuntimeBindingRecord).where(
                            OmnigentRuntimeBindingRecord.execution_scope_ref
                            == execution_scope_ref
                        )
                    )
                ).all()
            )
        if len(records) > 1:
            raise HarnessPlatformError(
                "execution scope has conflicting runtime binding authority",
                code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
            )
        if not records:
            return None
        record = records[0]
        binding = self._binding_from_record(record)
        if binding.runtimeBindingRef != record.runtime_binding_ref:
            raise HarnessPlatformError(
                "persisted runtime binding digest mismatch",
                code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
            )
        return RuntimeBindingStoreState(
            binding=binding,
            revision=int(record.revision),
            fencing_generation=int(record.fencing_generation),
            state=str(record.state),
            execution_scope_ref=str(record.execution_scope_ref or ""),
        )

    async def create_initial(
        self,
        *,
        execution_plan_ref: str,
        execution_scope_ref: str,
        provider_leases: dict[str, dict[str, Any]],
    ) -> OmnigentRuntimeBinding:
        from api_service.db.models import (
            OmnigentExecutionPlanRecord,
            OmnigentRuntimeBindingRecord,
        )
        from sqlalchemy import select

        async with self._session_factory() as session:
            if not str(execution_scope_ref or "").strip():
                raise ValueError("execution_scope_ref is required")
            binding = create_runtime_binding(
                executionPlanRef=execution_plan_ref,
                executionScopeRef=execution_scope_ref,
                providerLeases=provider_leases,
            )
            # Serialize the one-current-binding-per-execution decision on the
            # immutable plan authority. Different executions intentionally
            # reuse the same plan while owning independent live bindings.
            plan = await session.scalar(
                select(OmnigentExecutionPlanRecord)
                .where(
                    OmnigentExecutionPlanRecord.plan_ref
                    == execution_plan_ref
                )
                .with_for_update()
            )
            if plan is None:
                raise HarnessPlatformError(
                    f"execution plan {execution_plan_ref} is unavailable",
                    code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
                )
            existing = await session.scalar(
                select(OmnigentRuntimeBindingRecord)
                .where(
                    OmnigentRuntimeBindingRecord.execution_plan_ref
                    == execution_plan_ref,
                    OmnigentRuntimeBindingRecord.execution_scope_ref
                    == execution_scope_ref,
                )
                .order_by(OmnigentRuntimeBindingRecord.updated_at.desc())
                .limit(1)
            )
            if existing is not None:
                current = self._binding_from_record(existing)
                if current.providerLeases != binding.providerLeases:
                    raise HarnessPlatformError(
                        "execution plan already has different acquired generations",
                        code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
                    )
                return current
            record = OmnigentRuntimeBindingRecord(
                runtime_binding_ref=binding.runtimeBindingRef,
                binding_id=binding.runtimeBindingRef,
                latest_snapshot_ref=binding.runtimeBindingRef,
                execution_plan_ref=execution_plan_ref,
                execution_scope_ref=execution_scope_ref,
                revision=1,
                fencing_generation=1,
                state="credentials_acquired",
                provider_leases_json={
                    slot: lease.model_dump(mode="json", by_alias=True)
                    for slot, lease in binding.providerLeases.items()
                },
                credential_runtime_handles_json={
                    slot: lease.credentialRuntimeRef
                    for slot, lease in binding.providerLeases.items()
                },
                attestation_refs_json={},
                cleanup_authority_refs_json=[],
            )
            session.add(record)
            await session.commit()
        return binding

    async def _advance(
        self,
        runtime_binding_ref: str,
        *,
        expected_revision: int,
        expected_fencing_generation: int,
        update: Any,
        state: str,
        advance_fencing_generation: bool = False,
    ) -> OmnigentRuntimeBinding:
        from api_service.db.models import OmnigentRuntimeBindingRecord
        from sqlalchemy import select

        async with self._session_factory() as session:
            record = await session.scalar(
                select(OmnigentRuntimeBindingRecord)
                .where(
                    OmnigentRuntimeBindingRecord.runtime_binding_ref
                    == runtime_binding_ref
                )
                .with_for_update()
            )
            if record is None:
                raise HarnessPlatformError(
                    f"runtime binding {runtime_binding_ref} is stale or unavailable",
                    code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
                )
            if (
                int(record.revision) != expected_revision
                or int(record.fencing_generation) != expected_fencing_generation
            ):
                raise HarnessPlatformError(
                    "runtime binding revision or fencing generation conflict",
                    code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
                )
            current = self._binding_from_record(record)
            updated = update(current)
            record.runtime_binding_ref = updated.runtimeBindingRef
            record.latest_snapshot_ref = updated.runtimeBindingRef
            record.revision = int(record.revision) + 1
            if advance_fencing_generation:
                record.fencing_generation = int(record.fencing_generation) + 1
            record.state = state
            record.host_binding_ref = updated.hostBindingRef
            record.host_lease_ref = updated.hostLeaseRef
            record.host_lease_generation = updated.hostLeaseGeneration
            record.omnigent_host_id = updated.omnigentHostId
            record.runner_ref = updated.omnigentRunnerRef
            record.session_id = updated.omnigentSessionId
            record.chat_binding_ref = updated.chatBindingRef
            record.provider_leases_json = {
                slot: lease.model_dump(mode="json", by_alias=True)
                for slot, lease in updated.providerLeases.items()
            }
            record.credential_runtime_handles_json = {
                slot: lease.credentialRuntimeRef
                for slot, lease in updated.providerLeases.items()
            }
            record.attestation_refs_json = {
                key: value
                for key, value in {
                    "hostHarnessAttestationRef": updated.hostHarnessAttestationRef,
                    "exactHostCapabilityDecisionRef": (
                        updated.exactHostCapabilityDecisionRef
                    ),
                    "workspaceResolutionRef": updated.workspaceResolutionRef,
                    "modelOptionAttestationRef": updated.modelOptionAttestationRef,
                    "skillDeliveryAttestationRef": (
                        updated.skillDeliveryAttestationRef
                    ),
                }.items()
                if value is not None
            }
            record.cleanup_authority_refs_json = list(
                updated.cleanupAuthorityRefs
            )
            await session.commit()
            return updated

    async def reconcile_provider_leases(
        self,
        runtime_binding_ref: str,
        *,
        provider_leases: dict[str, dict[str, Any]],
        expected_revision: int,
        expected_fencing_generation: int,
    ) -> OmnigentRuntimeBinding:
        """CAS a replacement lease set and fence all prior live owners."""

        def update(current: OmnigentRuntimeBinding) -> OmnigentRuntimeBinding:
            return create_runtime_binding(
                executionPlanRef=current.executionPlanRef,
                executionScopeRef=current.executionScopeRef,
                providerLeases=provider_leases,
            )

        current = await self.get_state(runtime_binding_ref)
        if current is None:
            raise HarnessPlatformError(
                f"runtime binding {runtime_binding_ref} is stale or unavailable",
                code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
            )
        replacement = update(current.binding)
        if replacement.providerLeases == current.binding.providerLeases:
            if (
                current.revision != expected_revision
                or current.fencing_generation != expected_fencing_generation
            ):
                raise HarnessPlatformError(
                    "runtime binding revision or fencing generation conflict",
                    code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
                )
            return current.binding
        return await self._advance(
            runtime_binding_ref,
            expected_revision=expected_revision,
            expected_fencing_generation=expected_fencing_generation,
            update=update,
            state="credentials_acquired",
            advance_fencing_generation=True,
        )

    async def mark_cleanup_complete(
        self,
        runtime_binding_ref: str,
        *,
        expected_revision: int,
        expected_fencing_generation: int,
    ) -> OmnigentRuntimeBinding:
        return await self._advance(
            runtime_binding_ref,
            expected_revision=expected_revision,
            expected_fencing_generation=expected_fencing_generation,
            update=lambda current: current,
            state="cleanup_complete",
        )

    async def update_with_host(
        self,
        runtime_binding_ref: str,
        *,
        host_binding_ref: str,
        host_lease_ref: str,
        host_lease_generation: int,
        omnigent_host_id: str,
        host_harness_attestation_ref: str,
        exact_host_capability_decision_ref: str,
        workspace_resolution_ref: str,
        model_option_attestation_ref: str,
        skill_delivery_attestation_ref: str,
        cleanup_authority_refs: list[str],
        expected_revision: int,
        expected_fencing_generation: int,
    ) -> OmnigentRuntimeBinding:
        current_state = await self.get_state(runtime_binding_ref)
        if current_state is None:
            raise HarnessPlatformError(
                f"runtime binding {runtime_binding_ref} is stale or unavailable",
                code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
            )
        current_binding = current_state.binding
        replacing_host = bool(
            current_binding.hostBindingRef is not None
            and (
                current_binding.hostBindingRef != host_binding_ref
                or current_binding.hostLeaseRef != host_lease_ref
                or current_binding.hostLeaseGeneration != host_lease_generation
                or current_binding.omnigentHostId != omnigent_host_id
            )
        )

        def update(current: OmnigentRuntimeBinding) -> OmnigentRuntimeBinding:
            return create_runtime_binding(
                executionPlanRef=current.executionPlanRef,
                executionScopeRef=current.executionScopeRef,
                providerLeases={
                    slot: lease.model_dump(mode="json", by_alias=True)
                    for slot, lease in current.providerLeases.items()
                },
                hostBindingRef=host_binding_ref,
                hostLeaseRef=host_lease_ref,
                hostLeaseGeneration=host_lease_generation,
                omnigentHostId=omnigent_host_id,
                hostHarnessAttestationRef=host_harness_attestation_ref,
                exactHostCapabilityDecisionRef=(
                    exact_host_capability_decision_ref
                ),
                workspaceResolutionRef=workspace_resolution_ref,
                modelOptionAttestationRef=model_option_attestation_ref,
                skillDeliveryAttestationRef=skill_delivery_attestation_ref,
                omnigentRunnerRef=(
                    None if replacing_host else current.omnigentRunnerRef
                ),
                omnigentSessionId=(
                    None if replacing_host else current.omnigentSessionId
                ),
                chatBindingRef=(
                    None if replacing_host else current.chatBindingRef
                ),
                cleanupAuthorityRefs=cleanup_authority_refs,
            )

        return await self._advance(
            runtime_binding_ref,
            expected_revision=expected_revision,
            expected_fencing_generation=expected_fencing_generation,
            update=update,
            state="host_attested",
            advance_fencing_generation=replacing_host,
        )

    async def update_with_session(
        self,
        runtime_binding_ref: str,
        *,
        omnigent_session_id: str,
        omnigent_runner_ref: str | None,
        chat_binding_ref: str,
        expected_revision: int,
        expected_fencing_generation: int,
    ) -> OmnigentRuntimeBinding:
        def update(current: OmnigentRuntimeBinding) -> OmnigentRuntimeBinding:
            return create_runtime_binding(
                executionPlanRef=current.executionPlanRef,
                executionScopeRef=current.executionScopeRef,
                providerLeases={
                    slot: lease.model_dump(mode="json", by_alias=True)
                    for slot, lease in current.providerLeases.items()
                },
                hostBindingRef=current.hostBindingRef,
                hostLeaseRef=current.hostLeaseRef,
                hostLeaseGeneration=current.hostLeaseGeneration,
                omnigentHostId=current.omnigentHostId,
                hostHarnessAttestationRef=current.hostHarnessAttestationRef,
                exactHostCapabilityDecisionRef=(
                    current.exactHostCapabilityDecisionRef
                ),
                workspaceResolutionRef=current.workspaceResolutionRef,
                modelOptionAttestationRef=current.modelOptionAttestationRef,
                skillDeliveryAttestationRef=current.skillDeliveryAttestationRef,
                omnigentRunnerRef=omnigent_runner_ref,
                omnigentSessionId=omnigent_session_id,
                chatBindingRef=chat_binding_ref,
                cleanupAuthorityRefs=list(current.cleanupAuthorityRefs),
            )

        return await self._advance(
            runtime_binding_ref,
            expected_revision=expected_revision,
            expected_fencing_generation=expected_fencing_generation,
            update=update,
            state="session_bound",
        )
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
    "DbRuntimeBindingStore",
    "ExecutionPlanUsageIdentity",
    "InMemoryExecutionPlanStore",
    "InMemoryExecutionPlanUsageStore",
    "InMemoryRuntimeBindingStore",
    "OmnigentExecutionPlanStore",
    "OmnigentExecutionPlanUsageStore",
    "OmnigentRuntimeBindingStore",
    "RuntimeBindingStoreState",
    "SessionExecutionPlanStore",
    "execution_request_digest",
]
