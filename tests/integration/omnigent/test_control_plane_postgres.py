"""PostgreSQL coverage for the decisive Omnigent control-plane invariants.

Source: MoonLadderStudios/MoonMind#3703 ([Omnigent control plane 2/11]).

The uniqueness and concurrency cases that decide whether a second canonical
chat authority can be created must be proven on PostgreSQL, not only SQLite,
because the "fail closed rather than pick the newest row" guarantee depends on
the real unique-index concurrency behaviour. Runs an ephemeral local cluster
with no manually managed test service (mirrors the checkpoint-branch migration
integration test).
"""

from __future__ import annotations

import asyncio

import pytest
from api_service.db.models import (
    OmnigentExecutionPlanRecord,
    OmnigentRuntimeBindingRecord,
)
from moonmind.omnigent.control_plane import (
    ConflictingSessionAuthorityError,
    ControlPlaneOutcome,
    FencingConflictError,
    FencingScope,
    RevisionConflictError,
)

# The ephemeral-PostgreSQL provisioning (``control_plane_postgres_url``) and the
# ``pg_store`` binding live in ``conftest.py`` so they are shared with the
# fault-injection replay binding in ``test_control_plane_faultlab.py``.

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


@pytest.mark.asyncio
async def test_postgres_scope_uniqueness_fails_closed(pg_store) -> None:
    async with pg_store.transaction() as repos:
        await repos.sessions.create(
            session_id="s1",
            moonmind_workflow_id="wf-1",
            provider="codex",
            provider_session_ref="psess-1",
        )
    with pytest.raises(ConflictingSessionAuthorityError):
        async with pg_store.transaction() as repos:
            await repos.sessions.create(
                session_id="s2",
                moonmind_workflow_id="wf-1",
                provider="codex",
                provider_session_ref="psess-1",
            )


@pytest.mark.asyncio
async def test_postgres_session_keeps_one_stable_runtime_binding_identity(
    pg_store,
) -> None:
    """MoonLadderStudios/MoonMind#3701 authority handoff is DB-enforced."""

    plan_ref = "omnigent-execution-plan:sha256:" + "3" * 64
    binding_ref = "omnigent-runtime-binding:sha256:" + "4" * 64
    alternate_host_ref = "omnigent-runtime-binding:sha256:" + "6" * 64
    async with pg_store._session_factory() as session:
        session.add(
            OmnigentExecutionPlanRecord(
                plan_ref=plan_ref,
                schema_version="moonmind.omnigent-execution-plan.v1",
                payload_json={},
                harness_id="opencode-native",
                harness_implementation_ref="core:omnigent@1",
                host_class_ref="omnigent-opencode@1",
                launch_policy_ref="omnigent-on-demand@1",
                execution_realizer_ref="generic-omnigent-host@1",
            )
        )
        for ref in (binding_ref, alternate_host_ref):
            session.add(
                OmnigentRuntimeBindingRecord(
                    runtime_binding_ref=ref,
                    binding_id=ref,
                    latest_snapshot_ref=ref,
                    execution_plan_ref=plan_ref,
                    state="credentials_acquired",
                    provider_leases_json={},
                )
            )
        await session.commit()

    async with pg_store.transaction() as repos:
        created = await repos.sessions.create(
            session_id="s-authority",
            moonmind_workflow_id="wf-authority",
            provider="omnigent",
            execution_plan_ref=plan_ref,
        )
        credentials = await repos.sessions.bind_runtime_authority(
            "s-authority",
            expected_revision=created.revision,
            expected_fencing_generation=created.fencing_generation,
            execution_plan_ref=plan_ref,
            runtime_binding_ref=binding_ref,
        )
        host = await repos.sessions.bind_runtime_authority(
            "s-authority",
            expected_revision=credentials.revision,
            expected_fencing_generation=credentials.fencing_generation,
            execution_plan_ref=plan_ref,
            runtime_binding_ref=binding_ref,
        )

    assert host.runtime_binding_ref == binding_ref
    with pytest.raises(ConflictingSessionAuthorityError):
        async with pg_store.transaction() as repos:
            await repos.sessions.bind_runtime_authority(
                "s-authority",
                expected_revision=host.revision,
                expected_fencing_generation=host.fencing_generation,
                execution_plan_ref=plan_ref,
                runtime_binding_ref=alternate_host_ref,
            )


@pytest.mark.asyncio
async def test_postgres_chat_binding_uniqueness_fails_closed(pg_store) -> None:
    async with pg_store.transaction() as repos:
        await repos.sessions.create(
            session_id="s1",
            moonmind_workflow_id="wf-1",
            provider="codex",
            provider_session_ref="psess-1",
            chat_binding_id="cb-shared",
        )
    with pytest.raises(ConflictingSessionAuthorityError):
        async with pg_store.transaction() as repos:
            await repos.sessions.create(
                session_id="s2",
                moonmind_workflow_id="wf-2",
                provider="codex",
                provider_session_ref="psess-2",
                chat_binding_id="cb-shared",
            )


@pytest.mark.asyncio
async def test_postgres_concurrent_scope_creation_admits_one_authority(pg_store) -> None:
    async def _create(session_id: str):
        async with pg_store.transaction() as repos:
            return await repos.sessions.create(
                session_id=session_id,
                moonmind_workflow_id="wf-1",
                provider="codex",
                provider_session_ref="psess-1",
            )

    results = await asyncio.gather(
        _create("s-a"), _create("s-b"), return_exceptions=True
    )
    successes = [r for r in results if not isinstance(r, Exception)]
    conflicts = [r for r in results if isinstance(r, ConflictingSessionAuthorityError)]
    # Exactly one canonical authority is admitted; the loser fails closed rather
    # than superseding the winner.
    assert len(successes) == 1
    assert len(conflicts) == 1

    async with pg_store.transaction() as repos:
        winner = successes[0]
        stored = await repos.sessions.get_by_scope("wf-1", "psess-1")
    assert stored is not None
    assert stored.session_id == winner.session_id


@pytest.mark.asyncio
async def test_postgres_concurrent_fenced_update_admits_one_writer(pg_store) -> None:
    # Two reconcilers loading the same revision must not both commit: the
    # revision fence is enforced by a real row lock, so exactly one write lands
    # and the loser fails closed rather than overwriting the winner's cursor.
    async with pg_store.transaction() as repos:
        created = await repos.sessions.create(
            session_id="s1",
            moonmind_workflow_id="wf-1",
            provider="codex",
            provider_session_ref="psess-1",
        )
    base_revision = created.revision

    async def _advance(cursor: str):
        async with pg_store.transaction() as repos:
            return await repos.sessions.update_lifecycle(
                "s1",
                provider_event_cursor=cursor,
                expected_revision=base_revision,
                expected_fencing_generation=0,
            )

    results = await asyncio.gather(
        _advance("cursor-a"), _advance("cursor-b"), return_exceptions=True
    )
    successes = [r for r in results if not isinstance(r, Exception)]
    conflicts = [r for r in results if isinstance(r, RevisionConflictError)]
    assert len(successes) == 1
    assert len(conflicts) == 1

    async with pg_store.transaction() as repos:
        stored = await repos.sessions.get("s1")
    assert stored.revision == base_revision + 1
    assert stored.provider_event_cursor == successes[0].provider_event_cursor


@pytest.mark.asyncio
async def test_postgres_superseded_fencing_generation_is_fenced(pg_store) -> None:
    # A replacement supervisor acquires a strictly newer session-supervisor
    # generation. The former owner, still presenting the old generation, is
    # fenced out even though it presents a matching revision at load time.
    async with pg_store.transaction() as repos:
        await repos.sessions.create(
            session_id="s1",
            moonmind_workflow_id="wf-1",
            provider="codex",
            provider_session_ref="psess-1",
        )
    async with pg_store.transaction() as repos:
        current = await repos.sessions.get("s1")
        superseded = await repos.sessions.acquire_fencing_generation(
            "s1",
            FencingScope.SESSION_SUPERVISOR,
            expected_revision=current.revision,
        )
    assert superseded.fencing_generation == 1

    # The former owner presents generation 0 and is fenced (not a lost update).
    with pytest.raises(FencingConflictError):
        async with pg_store.transaction() as repos:
            await repos.sessions.update_lifecycle(
                "s1",
                expected_revision=superseded.revision,
                expected_fencing_generation=0,
                observed_state="running",
            )
    # The current owner (generation 1) still writes.
    async with pg_store.transaction() as repos:
        applied = await repos.sessions.update_lifecycle(
            "s1",
            expected_revision=superseded.revision,
            expected_fencing_generation=1,
            observed_state="running",
        )
    assert applied.observed_state == "running"


@pytest.mark.asyncio
async def test_postgres_two_janitors_cannot_both_claim_cleanup(pg_store) -> None:
    async with pg_store.transaction() as repos:
        await repos.sessions.create(
            session_id="s1",
            moonmind_workflow_id="wf-1",
            provider="codex",
            provider_session_ref="psess-1",
        )

    async def _claim(token):
        async with pg_store.transaction() as repos:
            return await repos.cleanup.claim_cleanup(
                "s1", owner_class="janitor", claim_token=token
            )

    results = await asyncio.gather(
        _claim("janitor-a"), _claim("janitor-b"), return_exceptions=True
    )
    outcomes = [r.outcome for r in results if not isinstance(r, Exception)]
    # Exactly one janitor claims; the other (distinct claim token) observes the
    # live claim and is refused.
    assert outcomes.count(ControlPlaneOutcome.APPLIED) == 1
    assert outcomes.count(ControlPlaneOutcome.NOT_OWNER) == 1


@pytest.mark.asyncio
async def test_postgres_former_janitor_cannot_release_replacement_lease(pg_store) -> None:
    # A janitor claims cleanup fenced against host lease generation 1. The host
    # lease is then renewed to generation 2. The former janitor's completion is
    # fenced: it cannot release a resource that now belongs to a newer lease.
    async with pg_store.transaction() as repos:
        await repos.sessions.create(
            session_id="s1",
            moonmind_workflow_id="wf-1",
            provider="codex",
            provider_session_ref="psess-1",
        )
        current = await repos.sessions.get("s1")
        await repos.sessions.acquire_fencing_generation(
            "s1", FencingScope.HOST_LEASE, expected_revision=current.revision
        )
        # The claim derives its host fence from the live session (generation 1).
        claim = await repos.cleanup.claim_cleanup(
            "s1", owner_class="janitor", claim_token="janitor-a"
        )
    assert claim.outcome is ControlPlaneOutcome.APPLIED
    assert claim.record.fenced_host_generation == 1

    # Host lease is renewed (a replacement generation).
    async with pg_store.transaction() as repos:
        current = await repos.sessions.get("s1")
        await repos.sessions.acquire_fencing_generation(
            "s1", FencingScope.HOST_LEASE, expected_revision=current.revision
        )

    async with pg_store.transaction() as repos:
        completion = await repos.cleanup.complete_cleanup(
            "s1",
            generation=claim.record.generation,
            owner_class="janitor",
            claim_token="janitor-a",
            session_repository=repos.sessions,
        )
    assert completion.outcome is ControlPlaneOutcome.FENCING_CONFLICT

    # The fenced-out claim is not stuck forever: a janitor fenced against the
    # current lease takes it over (advancing the generation) and completes.
    async with pg_store.transaction() as repos:
        takeover = await repos.cleanup.claim_cleanup(
            "s1", owner_class="janitor", claim_token="janitor-b"
        )
        assert takeover.outcome is ControlPlaneOutcome.APPLIED
        assert takeover.record.generation == claim.record.generation + 1
        completed = await repos.cleanup.complete_cleanup(
            "s1",
            generation=takeover.record.generation,
            owner_class="janitor",
            claim_token="janitor-b",
            session_repository=repos.sessions,
        )
    assert completed.outcome is ControlPlaneOutcome.APPLIED
    assert completed.record.state == "complete"


@pytest.mark.asyncio
async def test_postgres_command_claimed_and_executed_once(pg_store) -> None:
    # One logical command is claimed and executed once under concurrent activity
    # retries: exactly one claimer gets execution authority.
    async with pg_store.transaction() as repos:
        await repos.sessions.create(
            session_id="s1",
            moonmind_workflow_id="wf-1",
            provider="codex",
            provider_session_ref="psess-1",
        )
        await repos.commands.record(
            command_id="c1",
            session_id="s1",
            command_type="ensure_host",
            idempotency_key="cmd-1",
            payload_digest="digest",
        )

    async def _claim(token):
        async with pg_store.transaction() as repos:
            return await repos.commands.claim_command(
                "c1", owner_class="supervisor", claim_token=token
            )

    results = await asyncio.gather(
        _claim("worker-a"), _claim("worker-b"), return_exceptions=True
    )
    outcomes = [r.outcome for r in results if not isinstance(r, Exception)]
    # Exactly one worker wins execution authority; the racing worker (distinct
    # claim token) is refused rather than granted a false success.
    assert outcomes.count(ControlPlaneOutcome.APPLIED) == 1
    assert outcomes.count(ControlPlaneOutcome.NOT_OWNER) == 1


@pytest.mark.asyncio
async def test_postgres_delayed_frontier_after_new_epoch_is_retained(pg_store) -> None:
    # A delayed event/callback from an older provider epoch cannot regress the
    # frontier after a newer supervisor generation is acquired; it is fenced and
    # retained as an observation instead of overwriting current state.
    async with pg_store.transaction() as repos:
        created = await repos.sessions.create(
            session_id="s1",
            moonmind_workflow_id="wf-1",
            provider="codex",
            provider_session_ref="psess-1",
        )
        await repos.sessions.update_lifecycle(
            "s1",
            expected_revision=created.revision,
            expected_fencing_generation=0,
            provider_event_cursor="cursor-2",
        )
    async with pg_store.transaction() as repos:
        current = await repos.sessions.get("s1")
        await repos.sessions.acquire_fencing_generation(
            "s1", FencingScope.SESSION_SUPERVISOR, expected_revision=current.revision
        )

    async with pg_store.transaction() as repos:
        current = await repos.sessions.get("s1")
        # A stale-epoch callback presents the old generation 0.
        result = await repos.sessions.advance_observation_frontier(
            "s1",
            expected_revision=current.revision,
            expected_fencing_generation=0,
            provider_event_cursor="cursor-1",
        )
    assert result.outcome is ControlPlaneOutcome.FENCING_CONFLICT

    async with pg_store.transaction() as repos:
        stored = await repos.sessions.get("s1")
    # The durable frontier was not regressed by the delayed event.
    assert stored.provider_event_cursor == "cursor-2"


@pytest.mark.asyncio
async def test_postgres_null_provider_session_scopes_are_distinct(pg_store) -> None:
    # Two unattached canonical rows in one workflow do not collide (NULLs are
    # distinct under the unique index), so pre-attachment rows stay independent.
    async with pg_store.transaction() as repos:
        await repos.sessions.create(
            session_id="s1", moonmind_workflow_id="wf-1", provider="codex"
        )
        await repos.sessions.create(
            session_id="s2", moonmind_workflow_id="wf-1", provider="codex"
        )
        listed = await repos.turn_attempts.list_for_session("s1")
    assert listed == []
