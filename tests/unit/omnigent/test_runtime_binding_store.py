from __future__ import annotations

import hashlib
import json

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base, OmnigentExecutionPlanRecord
from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.harness_platform.runtime_binding import (
    OmnigentRuntimeBinding,
)
from moonmind.omnigent.harness_platform.stores import (
    DbRuntimeBindingStore,
    InMemoryRuntimeBindingStore,
    RuntimeBindingStoreState,
)
from moonmind.workflows.temporal.activities.omnigent_session_activities import (
    _project_runtime_binding_to_execution,
)


@pytest_asyncio.fixture()
async def session_factory(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/runtime-binding.db"
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield maker
    await engine.dispose()


@pytest.mark.asyncio
async def test_runtime_binding_is_created_after_generation_and_stale_writer_is_fenced() -> None:
    store = InMemoryRuntimeBindingStore()
    plan_ref = "omnigent-execution-plan:sha256:" + "1" * 64
    initial = await store.create_initial(
        execution_plan_ref=plan_ref,
        execution_scope_ref="workflow-1",
        provider_leases={
            "primary-model": {
                "providerProfileRef": "provider-1",
                "providerLeaseRef": "lease-1",
                "credentialGeneration": 7,
                "credentialRuntimeRef": "credential-runtime:lease-1:7",
            }
        },
    )
    state = await store.get_state(initial.runtimeBindingRef)
    assert state is not None
    assert initial.hostBindingRef is None
    assert initial.providerLeases["primary-model"].credentialGeneration == 7

    rotated = await store.reconcile_provider_leases(
        initial.runtimeBindingRef,
        provider_leases={
            "primary-model": {
                "providerProfileRef": "provider-1",
                "providerLeaseRef": "lease-rotated",
                "credentialGeneration": 8,
                "credentialRuntimeRef": "credential-runtime:lease-rotated:8",
            }
        },
        expected_revision=state.revision,
        expected_fencing_generation=state.fencing_generation,
    )
    rotated_state = await store.get_state(rotated.runtimeBindingRef)
    assert rotated_state is not None
    assert rotated_state.revision == 2
    assert rotated_state.fencing_generation == 2
    assert rotated.hostBindingRef is None
    assert rotated.providerLeases["primary-model"].credentialGeneration == 8

    with pytest.raises(HarnessPlatformError, match="stale|unavailable"):
        await store.update_with_host(
            initial.runtimeBindingRef,
            host_binding_ref="host-binding-old-owner",
            host_lease_ref="host-lease-old-owner",
            host_lease_generation=2,
            omnigent_host_id="host-old-owner",
            host_harness_attestation_ref="art_host_old_owner",
            exact_host_capability_decision_ref="art_caps_old_owner",
            workspace_resolution_ref="art_workspace_old_owner",
            model_option_attestation_ref="art_models_old_owner",
            skill_delivery_attestation_ref="art_skills_old_owner",
            cleanup_authority_refs=["art_cleanup_old_owner"],
            expected_revision=state.revision,
            expected_fencing_generation=state.fencing_generation,
        )

    with pytest.raises(HarnessPlatformError, match="fencing generation conflict"):
        await store.update_with_host(
            rotated.runtimeBindingRef,
            host_binding_ref="host-binding-unfenced",
            host_lease_ref="host-lease-unfenced",
            host_lease_generation=2,
            omnigent_host_id="host-unfenced",
            host_harness_attestation_ref="art_host_unfenced",
            exact_host_capability_decision_ref="art_caps_unfenced",
            workspace_resolution_ref="art_workspace_unfenced",
            model_option_attestation_ref="art_models_unfenced",
            skill_delivery_attestation_ref="art_skills_unfenced",
            cleanup_authority_refs=["art_cleanup_unfenced"],
            expected_revision=rotated_state.revision,
            expected_fencing_generation=rotated_state.fencing_generation + 1,
        )

    hosted = await store.update_with_host(
        rotated.runtimeBindingRef,
        host_binding_ref="host-binding-1",
        host_lease_ref="host-lease-1",
        host_lease_generation=3,
        omnigent_host_id="host-1",
        host_harness_attestation_ref="art_host_1",
        exact_host_capability_decision_ref="art_caps_1",
        workspace_resolution_ref="art_workspace_1",
        model_option_attestation_ref="art_models_1",
        skill_delivery_attestation_ref="art_skills_1",
        cleanup_authority_refs=["art_cleanup_1"],
        expected_revision=rotated_state.revision,
        expected_fencing_generation=rotated_state.fencing_generation,
    )
    assert hosted.runtimeBindingRef != initial.runtimeBindingRef

    with pytest.raises(HarnessPlatformError, match="stale|unavailable"):
        await store.update_with_host(
            initial.runtimeBindingRef,
            host_binding_ref="host-binding-stale",
            host_lease_ref="host-lease-stale",
            host_lease_generation=2,
            omnigent_host_id="host-stale",
            host_harness_attestation_ref="art_host_stale",
            exact_host_capability_decision_ref="art_caps_stale",
            workspace_resolution_ref="art_workspace_stale",
            model_option_attestation_ref="art_models_stale",
            skill_delivery_attestation_ref="art_skills_stale",
            cleanup_authority_refs=["art_cleanup_stale"],
            expected_revision=state.revision,
            expected_fencing_generation=state.fencing_generation,
        )

    hosted_state = await store.get_state(hosted.runtimeBindingRef)
    assert hosted_state is not None
    session_bound = await store.update_with_session(
        hosted.runtimeBindingRef,
        omnigent_session_id="session-1",
        omnigent_runner_ref="runner-1",
        chat_binding_ref="chat-1",
        expected_revision=hosted_state.revision,
        expected_fencing_generation=hosted_state.fencing_generation,
    )
    assert session_bound.omnigentSessionId == "session-1"
    assert session_bound.omnigentRunnerRef == "runner-1"
    assert session_bound.chatBindingRef == "chat-1"


def test_runtime_binding_v1_without_runner_or_chat_fields_keeps_its_digest() -> None:
    """Persisted v1 bindings remain replay-safe after adding session authority."""

    raw = {
        "schemaVersion": "moonmind.omnigent-runtime-binding.v1",
        "executionPlanRef": "omnigent-execution-plan:sha256:" + "9" * 64,
        "providerLeases": {
            "primary-model": {
                "providerProfileRef": "provider-legacy",
                "providerLeaseRef": "lease-legacy",
                "credentialGeneration": 3,
                "credentialRuntimeRef": "credential-runtime:lease-legacy:3",
            }
        },
        "hostBindingRef": None,
        "hostLeaseRef": None,
        "hostLeaseGeneration": None,
        "omnigentHostId": None,
        "hostHarnessAttestationRef": None,
        "exactHostCapabilityDecisionRef": None,
        "workspaceResolutionRef": None,
        "modelOptionAttestationRef": None,
        "skillDeliveryAttestationRef": None,
        "omnigentSessionId": None,
        "cleanupAuthorityRefs": [],
    }
    canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"))
    raw["runtimeBindingRef"] = (
        "omnigent-runtime-binding:sha256:"
        + hashlib.sha256(canonical.encode()).hexdigest()
    )

    parsed = OmnigentRuntimeBinding.model_validate(raw)

    assert parsed.runtimeBindingRef == raw["runtimeBindingRef"]
    assert parsed.omnigentRunnerRef is None
    assert parsed.chatBindingRef is None


@pytest.mark.asyncio
async def test_db_runtime_binding_store_advances_digest_and_rejects_stale_ref(
    session_factory,
) -> None:
    plan_ref = "omnigent-execution-plan:sha256:" + "2" * 64
    async with session_factory() as session:
        session.add(
            OmnigentExecutionPlanRecord(
                plan_ref=plan_ref,
                schema_version="moonmind.omnigent-execution-plan-envelope.v1",
                payload_json={},
                harness_id="opencode-native",
                harness_implementation_ref=(
                    "omnigent-harness-implementation:sha256:" + "3" * 64
                ),
                host_class_ref="omnigent-opencode@1",
                launch_policy_ref="opencode-on-demand@1",
                execution_realizer_ref="generic-omnigent-host@1",
            )
        )
        await session.commit()

    store = DbRuntimeBindingStore(session_factory)
    initial = await store.create_initial(
        execution_plan_ref=plan_ref,
        execution_scope_ref="workflow-opencode",
        provider_leases={
            "primary-model": {
                "providerProfileRef": "provider-opencode",
                "providerLeaseRef": "lease-opencode",
                "credentialGeneration": 11,
                "credentialRuntimeRef": "credential-runtime:lease-opencode:11",
            }
        },
    )
    initial_state = await store.get_state(initial.runtimeBindingRef)
    assert initial_state is not None
    hosted = await store.update_with_host(
        initial.runtimeBindingRef,
        host_binding_ref="host-binding-opencode",
        host_lease_ref="host-lease-opencode",
        host_lease_generation=4,
        omnigent_host_id="host-opencode",
        host_harness_attestation_ref="art_host_opencode",
        exact_host_capability_decision_ref="art_caps_opencode",
        workspace_resolution_ref="art_workspace_opencode",
        model_option_attestation_ref="art_models_opencode",
        skill_delivery_attestation_ref="art_skills_opencode",
        cleanup_authority_refs=["art_cleanup_opencode"],
        expected_revision=initial_state.revision,
        expected_fencing_generation=initial_state.fencing_generation,
    )
    assert await store.get_state(initial.runtimeBindingRef) is None
    hosted_state = await store.get_state(hosted.runtimeBindingRef)
    assert hosted_state is not None
    assert hosted_state.revision == 2
    assert hosted_state.binding.hostHarnessAttestationRef == "art_host_opencode"
    assert hosted_state.binding.modelOptionAttestationRef == "art_models_opencode"
    assert hosted_state.binding.cleanupAuthorityRefs == ("art_cleanup_opencode",)

    with pytest.raises(HarnessPlatformError, match="stale|unavailable"):
        await store.update_with_session(
            initial.runtimeBindingRef,
            omnigent_session_id="stale-session",
            omnigent_runner_ref=None,
            chat_binding_ref="stale-chat",
            expected_revision=initial_state.revision,
            expected_fencing_generation=initial_state.fencing_generation,
        )


@pytest.mark.asyncio
async def test_db_runtime_binding_store_reuses_plan_across_execution_scopes_and_reconciles_rotation(
    session_factory,
) -> None:
    """Reruns/occurrences share a plan, never their mutable live authority."""

    plan_ref = "omnigent-execution-plan:sha256:" + "7" * 64
    async with session_factory() as session:
        session.add(
            OmnigentExecutionPlanRecord(
                plan_ref=plan_ref,
                schema_version="moonmind.omnigent-execution-plan-envelope.v1",
                payload_json={},
                harness_id="opencode-native",
                harness_implementation_ref=(
                    "omnigent-harness-implementation:sha256:" + "8" * 64
                ),
                host_class_ref="omnigent-opencode@1",
                launch_policy_ref="opencode-on-demand@1",
                execution_realizer_ref="generic-omnigent-host@1",
            )
        )
        await session.commit()

    def leases(generation: int, scope: str) -> dict[str, dict[str, object]]:
        return {
            "primary-model": {
                "providerProfileRef": "provider-opencode",
                "providerLeaseRef": f"lease-{scope}-{generation}",
                "credentialGeneration": generation,
                "credentialRuntimeRef": (
                    f"credential-runtime:lease-{scope}-{generation}:{generation}"
                ),
            }
        }

    store = DbRuntimeBindingStore(session_factory)
    first = await store.create_initial(
        execution_plan_ref=plan_ref,
        execution_scope_ref="rerun-1",
        provider_leases=leases(1, "rerun-1"),
    )
    continuation = await store.create_initial(
        execution_plan_ref=plan_ref,
        execution_scope_ref="linked-continuation-1",
        provider_leases=leases(2, "linked-continuation-1"),
    )
    occurrence = await store.create_initial(
        execution_plan_ref=plan_ref,
        execution_scope_ref="recurring-occurrence-1",
        provider_leases=leases(3, "recurring-occurrence-1"),
    )
    next_occurrence = await store.create_initial(
        execution_plan_ref=plan_ref,
        execution_scope_ref="recurring-occurrence-2",
        provider_leases=leases(4, "recurring-occurrence-2"),
    )
    assert len(
        {
            first.runtimeBindingRef,
            continuation.runtimeBindingRef,
            occurrence.runtimeBindingRef,
            next_occurrence.runtimeBindingRef,
        }
    ) == 4
    first_state = await store.get_current_state(plan_ref, "rerun-1")
    occurrence_state = await store.get_current_state(
        plan_ref, "recurring-occurrence-1"
    )
    assert first_state is not None and occurrence_state is not None
    assert first_state.revision == occurrence_state.revision == 1

    rotated = await store.reconcile_provider_leases(
        first.runtimeBindingRef,
        provider_leases=leases(3, "rerun-1"),
        expected_revision=first_state.revision,
        expected_fencing_generation=first_state.fencing_generation,
    )
    rotated_state = await store.get_current_state(plan_ref, "rerun-1")
    assert rotated_state is not None
    assert rotated_state.binding == rotated
    assert rotated_state.revision == 2
    assert rotated_state.fencing_generation == 2
    assert await store.get_state(first.runtimeBindingRef) is None
    assert (
        await store.get_current_state(plan_ref, "recurring-occurrence-1")
    ) == occurrence_state


@pytest.mark.asyncio
async def test_host_replacement_advances_fence_and_rejects_delayed_old_owner() -> None:
    store = InMemoryRuntimeBindingStore()
    initial = await store.create_initial(
        execution_plan_ref="omnigent-execution-plan:sha256:" + "6" * 64,
        execution_scope_ref="workflow-host-replacement",
        provider_leases={
            "primary-model": {
                "providerProfileRef": "provider-1",
                "providerLeaseRef": "lease-1",
                "credentialGeneration": 1,
                "credentialRuntimeRef": "credential-runtime:lease-1:1",
            }
        },
    )
    initial_state = await store.get_state(initial.runtimeBindingRef)
    assert initial_state is not None
    hosted = await store.update_with_host(
        initial.runtimeBindingRef,
        host_binding_ref="host-binding-1",
        host_lease_ref="host-lease-1",
        host_lease_generation=1,
        omnigent_host_id="host-1",
        host_harness_attestation_ref="art-host-1",
        exact_host_capability_decision_ref="art-caps-1",
        workspace_resolution_ref="art-workspace-1",
        model_option_attestation_ref="art-model-1",
        skill_delivery_attestation_ref="art-skill-1",
        cleanup_authority_refs=["art-cleanup-1"],
        expected_revision=initial_state.revision,
        expected_fencing_generation=initial_state.fencing_generation,
    )
    hosted_state = await store.get_state(hosted.runtimeBindingRef)
    assert hosted_state is not None
    replacement = await store.update_with_host(
        hosted.runtimeBindingRef,
        host_binding_ref="host-binding-2",
        host_lease_ref="host-lease-2",
        host_lease_generation=2,
        omnigent_host_id="host-2",
        host_harness_attestation_ref="art-host-2",
        exact_host_capability_decision_ref="art-caps-2",
        workspace_resolution_ref="art-workspace-2",
        model_option_attestation_ref="art-model-2",
        skill_delivery_attestation_ref="art-skill-2",
        cleanup_authority_refs=["art-cleanup-2"],
        expected_revision=hosted_state.revision,
        expected_fencing_generation=hosted_state.fencing_generation,
    )
    replacement_state = await store.get_state(replacement.runtimeBindingRef)
    assert replacement_state is not None
    assert replacement_state.fencing_generation == 2
    assert replacement.omnigentSessionId is None
    with pytest.raises(HarnessPlatformError, match="stale|unavailable"):
        await store.mark_cleanup_complete(
            hosted.runtimeBindingRef,
            expected_revision=hosted_state.revision,
            expected_fencing_generation=hosted_state.fencing_generation,
        )


@pytest.mark.asyncio
async def test_runtime_binding_projection_is_safe_and_monotonic(monkeypatch) -> None:
    from api_service.db import base as db_base

    execution_rows = {
        "TemporalExecutionCanonicalRecord": type(
            "Execution", (), {"memo": {}}
        )(),
        "TemporalExecutionRecord": type("Execution", (), {"memo": {}})(),
    }

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, model, workflow_id):
            assert workflow_id == "workflow-opencode"
            return execution_rows[model.__name__]

        async def commit(self):
            return None

    monkeypatch.setattr(db_base, "async_session_maker", lambda: FakeSession())
    store = InMemoryRuntimeBindingStore()
    binding = await store.create_initial(
        execution_plan_ref="omnigent-execution-plan:sha256:" + "4" * 64,
        execution_scope_ref="workflow-opencode",
        provider_leases={
            "primary-model": {
                "providerProfileRef": "provider-opencode",
                "providerLeaseRef": "lease-opencode",
                "credentialGeneration": 12,
                "credentialRuntimeRef": "credential-runtime:lease-opencode:12",
            }
        },
    )
    state = await store.get_state(binding.runtimeBindingRef)
    assert state is not None

    await _project_runtime_binding_to_execution(
        workflow_id="workflow-opencode", state=state
    )
    for execution in execution_rows.values():
        assert execution.memo == {
            "omnigent_runtime_binding_ref": binding.runtimeBindingRef,
            "omnigent_runtime_binding_revision": 1,
            "omnigent_runtime_binding_fencing_generation": 1,
            "omnigent_runtime_binding_state": "credentials_acquired",
        }

    stale_state = RuntimeBindingStoreState(
        binding=binding,
        revision=0,
        fencing_generation=1,
        state="credentials_acquired",
        execution_scope_ref="workflow-opencode",
    )
    with pytest.raises(ValueError, match="ahead of authority"):
        await _project_runtime_binding_to_execution(
            workflow_id="workflow-opencode", state=stale_state
        )
