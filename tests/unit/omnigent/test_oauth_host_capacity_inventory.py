"""Exercise janitor capacity inventory against real persisted lease rows."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    ManagedAgentProviderProfile,
    OmnigentOAuthHostLeaseRecord,
    ProviderCredentialSource,
    ProviderProfileSlotLease,
    RuntimeMaterializationMode,
)
from moonmind.omnigent.oauth_hosts import OmnigentOAuthHostRepository


@pytest.mark.asyncio
async def test_terminal_capacity_inventory_excludes_release_tombstones(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/inventory.db")
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    now = datetime.now(UTC)
    cases = [
        # Expired held leases still consume capacity until confirmed cleanup.
        ("stopped-held", "stopped", "held"),
        ("failed-held", "failed", "held"),
        ("stopped-cleanup", "stopped", "cleanup_requested"),
        ("failed-cleanup", "failed", "cleanup_requested"),
        ("stopped-released", "stopped", "released"),
        ("failed-released", "failed", "released"),
        ("active-released", "draining", "released"),
        ("active-held", "ready", "held"),
        ("missing-provider", "stopped", None),
    ]
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        repository = OmnigentOAuthHostRepository(factory)
        for key, host_state, provider_state in cases:
            async with factory() as session:
                session.add(
                    ManagedAgentProviderProfile(
                        profile_id=key,
                        runtime_id="codex_cli",
                        provider_id="openai",
                        credential_source=ProviderCredentialSource.OAUTH_VOLUME,
                        runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
                        volume_ref="codex_auth_volume",
                        volume_mount_path="/home/app/.codex",
                        credential_generation=1,
                        enabled=True,
                    )
                )
                await session.commit()
            binding = await repository.create_or_update_static_binding(
                profile_id=key, endpoint_ref="default", static_host_id=f"host-{key}"
            )
            async with factory() as session:
                session.add(
                    OmnigentOAuthHostLeaseRecord(
                        lease_id=key,
                        provider_profile_id=key,
                        provider_lease_id=f"provider-{key}",
                        binding_ref=binding.binding_ref,
                        credential_generation=1,
                        holder_workflow_id=f"workflow-{key}",
                        idempotency_key=key,
                        lease_purpose="execution_direct",
                        status=host_state,
                        acquired_at=now - timedelta(hours=2),
                        last_heartbeat_at=now - timedelta(hours=1),
                        expires_at=now - timedelta(minutes=1),
                    )
                )
                if provider_state is not None:
                    session.add(
                        ProviderProfileSlotLease(
                            runtime_id="codex_cli",
                            workflow_id=f"workflow-{key}",
                            profile_id=key,
                            lease_id=f"provider-{key}",
                            lease_state=provider_state,
                            expires_at=now - timedelta(minutes=1),
                            released_at=now if provider_state == "released" else None,
                        )
                    )
                await session.commit()
        # Lease IDs are runtime-scoped. A different profile's live lease must
        # not resurrect the released host through a coincidentally equal ID.
        async with factory() as session:
            session.add(
                ProviderProfileSlotLease(
                    runtime_id="claude_code",
                    workflow_id="other-workflow",
                    profile_id="other-profile",
                    lease_id="provider-stopped-released",
                    lease_state="held",
                )
            )
            await session.commit()
        terminal = (
            await repository.list_terminal_host_leases_with_active_provider_capacity()
        )
        assert {lease.lease_id for lease in terminal} == {
            "stopped-held",
            "failed-held",
            "stopped-cleanup",
            "failed-cleanup",
        }
        assert len(terminal) == 4
        # Active host cleanup remains independent of provider tombstones.
        active = await repository.list_active_host_leases()
        assert {lease.lease_id for lease in active} == {
            "active-held",
            "active-released",
        }
        # Historical rows remain available for independent orphan reconciliation.
        assert await repository.get_host_lease("stopped-released") is not None

        # A coordinator can confirm physical cleanup/release before the final
        # runtime-binding UPDATE fails. Reconcile that durable row independently.
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from sqlalchemy import event
        from sqlalchemy.exc import OperationalError

        from api_service.db.models import OmnigentExecutionPlanRecord
        from moonmind.omnigent.harness_platform.stores import DbRuntimeBindingStore
        from moonmind.omnigent.oauth_host_janitor import OmnigentOAuthHostJanitor

        plan_ref = "omnigent-execution-plan:sha256:" + "2" * 64
        async with factory() as session:
            session.add(
                OmnigentExecutionPlanRecord(
                    plan_ref=plan_ref,
                    schema_version="moonmind.omnigent-execution-plan-envelope.v1",
                    payload_json={},
                    harness_id="codex-native",
                    harness_implementation_ref="omnigent-harness-implementation:sha256:"
                    + "3" * 64,
                    host_class_ref="codex@1",
                    launch_policy_ref="codex-on-demand@1",
                    execution_realizer_ref="codex-profile-bound@1",
                )
            )
            row = await session.get(OmnigentOAuthHostLeaseRecord, "stopped-released")
            row.cleanup_completed_at = now
            row.omnigent_host_id = "stopped-host"
            binding_ref = row.binding_ref
            await session.commit()
        runtime_store = DbRuntimeBindingStore(factory)
        initial = await runtime_store.create_initial(
            execution_plan_ref=plan_ref,
            execution_scope_ref="workflow-stopped-released",
            provider_leases={
                "primary-model": {
                    "providerProfileRef": "stopped-released",
                    "providerLeaseRef": "provider-stopped-released",
                    "credentialGeneration": 1,
                    "credentialRuntimeRef": "credential-runtime:stopped-released:1",
                }
            },
        )
        state = await runtime_store.get_state(initial.runtimeBindingRef)
        hosted = await runtime_store.update_with_host(
            initial.runtimeBindingRef,
            host_binding_ref=binding_ref,
            host_lease_ref="stopped-released",
            host_lease_generation=1,
            omnigent_host_id="stopped-host",
            host_harness_attestation_ref="art-host",
            exact_host_capability_decision_ref="art-caps",
            workspace_resolution_ref="art-workspace",
            model_option_attestation_ref="art-model",
            skill_delivery_attestation_ref="art-skill",
            cleanup_authority_refs=[],
            expected_revision=state.revision,
            expected_fencing_generation=state.fencing_generation,
        )
        fail_updates = True

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def fail_binding_write(
            _connection, _cursor, statement, parameters, _context, _many
        ):
            if fail_updates and statement.startswith(
                "UPDATE omnigent_runtime_bindings"
            ):
                raise OperationalError(
                    statement, parameters, RuntimeError("database unavailable")
                )

        state = await runtime_store.get_state(hosted.runtimeBindingRef)
        with pytest.raises(OperationalError):
            await runtime_store.mark_cleanup_complete(
                hosted.runtimeBindingRef,
                expected_revision=state.revision,
                expected_fencing_generation=state.fencing_generation,
            )
        runtime = SimpleNamespace(list_managed_containers=AsyncMock(return_value=[]))
        # A profile-filtered pass must not act on unrelated active hosts.
        run_store = SimpleNamespace(
            cleanup_required_host_lease_refs=AsyncMock(return_value=set()),
            record_terminal_cleanup=AsyncMock(),
        )
        janitor = OmnigentOAuthHostJanitor(
            repository=repository,
            runtime=runtime,
            client=SimpleNamespace(),
            runtime_binding_store=runtime_store,
            run_store=run_store,
        )
        first = await janitor.run(profile_id="stopped-released")
        assert first["status"] == "degraded"
        assert (
            await runtime_store.get_state(hosted.runtimeBindingRef)
        ).state == "host_attested"
        fail_updates = False
        second = await janitor.run(profile_id="stopped-released")
        assert second["status"] == "completed"
        assert (
            await runtime_store.get_state(hosted.runtimeBindingRef)
        ).state == "cleanup_complete"
        # No host/session RPC or capacity release was needed; the original
        # release receipt must not be rewritten as held for an absent claim.
        run_store.record_terminal_cleanup.assert_not_awaited()
        assert (
            await repository.list_terminal_host_leases_with_pending_binding_cleanup()
            == []
        )
        # A successor owns new provider/binding authority, so its incomplete
        # credentials-only row must not re-admit the old released host.
        completed = await runtime_store.get_state(hosted.runtimeBindingRef)
        replacement = await runtime_store.reconcile_provider_leases(
            hosted.runtimeBindingRef,
            provider_leases={
                "primary-model": {
                    "providerProfileRef": "stopped-released",
                    "providerLeaseRef": "replacement-provider",
                    "credentialGeneration": 1,
                    "credentialRuntimeRef": "credential-runtime:replacement:1",
                }
            },
            expected_revision=completed.revision,
            expected_fencing_generation=completed.fencing_generation,
        )
        assert (
            await repository.list_terminal_host_leases_with_pending_binding_cleanup()
            == []
        )
        # Even a stale inventory snapshot cannot complete the replacement.
        replacement_before = await runtime_store.get_state(
            replacement.runtimeBindingRef
        )
        repository.list_terminal_host_leases_with_pending_binding_cleanup = AsyncMock(
            return_value=[await repository.get_host_lease("stopped-released")]
        )
        fenced = await janitor.run(profile_id="stopped-released")
        assert fenced["status"] == "degraded"
        assert fenced["actions"][0]["errorCode"] == "ValueError"
        replacement_after = await runtime_store.get_state(replacement.runtimeBindingRef)
        assert replacement_after.revision == replacement_before.revision
        assert replacement_after.state == replacement_before.state
    finally:
        await engine.dispose()
