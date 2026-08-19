"""PostgreSQL coverage for the decisive Omnigent control-plane invariants.

Source: MoonLadderStudios/MoonMind#3703 ([Omnigent control plane 2/11]) and
MoonLadderStudios/MoonMind#3707 ([Omnigent control plane 6/11]).

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
from moonmind.omnigent.control_plane import (
    CleanupFenceError,
    ConflictingSessionAuthorityError,
    ControlPlaneOutcome,
    FencingConflictError,
    FencingScope,
    ImmutableSessionDimensions,
    OmnigentTurnService,
    RecoveryEvidence,
    RevisionConflictError,
    TurnIdempotencyConflictError,
    TurnSourceKind,
    TurnSubmissionRequest,
)
from moonmind.workflows.temporal.activities.omnigent_activities import (
    _checkpoint_recovery_dimensions,
)

# The ephemeral-PostgreSQL provisioning (``control_plane_postgres_url``) and the
# ``pg_store`` binding live in ``conftest.py`` so they are shared with the
# fault-injection replay binding in ``test_control_plane_faultlab.py``.

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("changed_field", "changed_value", "expected_dimension"),
    [
        ("repository", "MoonLadderStudios/Other", "repository"),
        ("workspaceRef", "workspace-intent:sha256:changed", "workspace_ref"),
    ],
)
async def test_activity_compiled_recovery_changes_require_branch_at_store_boundary(
    pg_store, changed_field, changed_value, expected_dimension
) -> None:
    """The production Activity compiler feeds the persisted canonical gate."""

    service = OmnigentTurnService(pg_store)
    source = {
        "provider": "omnigent",
        "instructionDigest": "sha256:instructions",
        "runtimeId": "omnigent",
        "model": "gpt-5.6",
        "effort": "high",
        "compatibilityProfile": "omnigent-bridge-v1",
        "providerProfileId": "profile-1",
        "launchPolicyRef": "policy-1",
        "imageManifestRef": "image-1",
        "compatibilityRef": "compatibility-1",
        "repository": "MoonLadderStudios/MoonMind",
        "repositoryBranch": "main",
        "workspaceRef": "workspace-intent:sha256:source",
        "publishMode": "none",
        "skillRef": "remediate-issue",
        "runtimeAuthorityRef": "launch-1",
        "intentDigest": "sha256:intent",
    }
    source_dimensions = _checkpoint_recovery_dimensions(
        {"immutableRequested": source}
    )
    await service.establish_session(
        TurnSubmissionRequest(
            session_id=f"recovery-{expected_dimension}",
            source_kind=TurnSourceKind.INITIAL,
            caller_id="workflow-activity",
            idempotency_key=f"recovery-initial-{expected_dimension}",
            instruction_digest="sha256:instructions",
            requested_dimensions=source_dimensions,
        ),
        moonmind_workflow_id=f"wf-recovery-{expected_dimension}",
        provider="omnigent",
        new_session_id=f"recovery-{expected_dimension}",
        compatibility_profile=source_dimensions.compatibility_profile,
        provider_profile_id=source_dimensions.provider_profile_id,
        compatibility_ref=source_dimensions.compatibility_ref,
        image_manifest_ref=source_dimensions.image_manifest_ref,
    )
    requested = {**source, changed_field: changed_value}
    intent_dimensions = _checkpoint_recovery_dimensions(
        {"immutableRequested": requested}
    )
    decision = await service.decide_session_recovery(
        f"recovery-{expected_dimension}",
        recovery_idempotency_key=f"recover-{expected_dimension}",
        intent_dimensions=intent_dimensions,
        live_authority=RecoveryEvidence(
            intent_dimensions=intent_dimensions,
            session_dimensions=source_dimensions,
            provider_profile_lease_current=True,
            host_available=True,
            provider_session_reachable=True,
            cursor_present=True,
            first_message_consistent=True,
            credential_generation_current=True,
            workspace_artifact_valid=True,
            session_evidence_valid=True,
        ),
    )

    assert decision.mode.value == "branch_required"
    assert decision.changed_dimensions == (expected_dimension,)


@pytest.mark.asyncio
async def test_static_host_authority_resolution_fences_active_chat_turn(
    pg_store,
) -> None:
    """Forced static cleanup consumes real session/turn repositories."""

    service = OmnigentTurnService(pg_store)
    dimensions = ImmutableSessionDimensions(
        provider="omnigent",
        provider_profile_id="profile-static",
        repository="MoonLadderStudios/MoonMind",
    )
    initial = await service.establish_session(
        TurnSubmissionRequest(
            session_id="static-session",
            source_kind=TurnSourceKind.INITIAL,
            caller_id="workflow-static",
            idempotency_key="static-initial",
            instruction_digest="digest-static-initial",
            requested_dimensions=dimensions,
        ),
        moonmind_workflow_id="wf-static",
        provider="omnigent",
        new_session_id="static-session",
        provider_profile_id="profile-static",
        host_binding_ref="binding-static",
    )
    await service.record_turn_delivery(
        initial.turn_attempt.idempotency_key,
        outcome=ControlPlaneOutcome.APPLIED,
    )
    await service.record_turn_terminal(
        initial.turn_attempt.idempotency_key,
        terminal_state="completed",
    )
    await service.submit_reuse_turn(
        TurnSubmissionRequest(
            session_id="static-session",
            source_kind=TurnSourceKind.WORKFLOW_CHAT,
            caller_id="workflow-static",
            idempotency_key="static-chat-active",
            instruction_digest="digest-static-chat",
            requested_dimensions=dimensions,
        )
    )

    async with pg_store.transaction() as repos:
        sessions = await repos.sessions.list_for_host_authority(
            host_binding_ref="binding-static",
            provider_profile_id="profile-static",
        )
    assert [session.session_id for session in sessions] == ["static-session"]
    with pytest.raises(CleanupFenceError):
        await service.claim_cleanup(
            "static-session",
            owner_class="oauth_host_janitor",
            claim_token="force-static",
        )


@pytest.mark.asyncio
async def test_postgres_continuations_share_canonical_session_and_chat_binding(
    pg_store,
) -> None:
    """Every message is a turn; attempt completion never closes the session."""

    service = OmnigentTurnService(pg_store)
    dimensions = ImmutableSessionDimensions(
        provider="codex", repository="MoonLadderStudios/MoonMind"
    )
    initial = TurnSubmissionRequest(
        session_id="s1",
        source_kind=TurnSourceKind.INITIAL,
        caller_id="workflow-1",
        idempotency_key="turn-initial",
        instruction_digest="digest-initial",
        requested_dimensions=dimensions,
    )
    established = await service.establish_session(
        initial,
        moonmind_workflow_id="wf-1",
        provider="codex",
        new_session_id="s1",
        new_chat_binding_id="chat-1",
        provider_session_ref="provider-session-1",
    )
    await service.record_turn_delivery(
        established.turn_attempt.idempotency_key,
        outcome=ControlPlaneOutcome.APPLIED,
    )
    await service.record_turn_terminal(
        established.turn_attempt.idempotency_key,
        terminal_state="completed",
        terminal_evidence_ref="art://initial-terminal",
    )

    for source_kind, key in (
        (TurnSourceKind.REPOSITORY_CONTINUATION, "turn-continuation"),
        (TurnSourceKind.WORKFLOW_CHAT, "turn-chat"),
    ):
        outcome = await service.submit_reuse_turn(
            TurnSubmissionRequest(
                session_id="s1",
                source_kind=source_kind,
                caller_id="workflow-1",
                idempotency_key=key,
                instruction_digest=f"digest-{key}",
                requested_dimensions=dimensions,
            )
        )
        assert outcome.session.chat_binding_id == "chat-1"
        await service.record_turn_delivery(
            key, outcome=ControlPlaneOutcome.APPLIED
        )
        await service.record_turn_terminal(
            key,
            terminal_state="completed",
            terminal_evidence_ref=f"art://{key}-terminal",
        )

    async with pg_store.transaction() as repos:
        sessions = await repos.sessions.list_for_workflow("wf-1")
        turns = await repos.turn_attempts.list_for_session("s1")
        decisions = await repos.decisions.list_for_session("s1")
    assert len(sessions) == 1
    assert sessions[0].provider_session_ref == "provider-session-1"
    assert sessions[0].chat_binding_id == "chat-1"
    assert sessions[0].is_terminal is False
    assert len(turns) == 3
    assert all(turn.is_terminal for turn in turns)
    assert {decision.decision_code for decision in decisions} == {
        "turn_submission:initial",
        "turn_submission:repository_continuation",
        "turn_submission:workflow_chat",
    }

    await service.mark_session_terminal(
        "s1",
        terminal_state="completed",
        terminal_evidence_ref="art://session-terminal",
    )
    capability = await service.resolve_chat_capability(
        "chat-1", caller_authorized=True
    )
    assert capability.read_only is True
    assert capability.historical_read_available is True


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
async def test_postgres_cleanup_loses_to_accepted_chat_turn(pg_store) -> None:
    """The production cleanup repository cannot race an admitted new turn."""

    service = OmnigentTurnService(pg_store)
    await service.establish_session(
        TurnSubmissionRequest(
            session_id="s1",
            source_kind=TurnSourceKind.INITIAL,
            caller_id="workflow-1",
            idempotency_key="initial",
            instruction_digest="digest-initial",
        ),
        moonmind_workflow_id="wf-1",
        provider="codex",
        new_session_id="s1",
        new_chat_binding_id="chat-1",
    )
    await service.record_turn_terminal("initial", terminal_state="completed")
    chat = await service.submit_reuse_turn(
        TurnSubmissionRequest(
            session_id="s1",
            source_kind=TurnSourceKind.WORKFLOW_CHAT,
            caller_id="workflow-chat",
            idempotency_key="chat-turn",
            instruction_digest="digest-chat",
        )
    )

    with pytest.raises(CleanupFenceError, match="active_turn"):
        await service.claim_cleanup(
            "s1", owner_class="oauth_host_janitor", claim_token="janitor:s1"
        )

    await service.record_turn_terminal(
        chat.turn_attempt.idempotency_key, terminal_state="completed"
    )
    claimed = await service.claim_cleanup(
        "s1", owner_class="oauth_host_janitor", claim_token="janitor:s1"
    )
    assert claimed.outcome is ControlPlaneOutcome.APPLIED


@pytest.mark.asyncio
async def test_postgres_recovery_decision_is_durably_idempotent(pg_store) -> None:
    service = OmnigentTurnService(pg_store)
    dimensions = ImmutableSessionDimensions(
        provider="omnigent",
        runtime_id="omnigent",
        model="gpt-5.6",
        effort="high",
        provider_profile_id="profile-1",
        policy_ref="policy-1",
        branch="main",
        publication_mode="none",
        instruction_digest="sha256:instructions",
    )
    await service.establish_session(
        TurnSubmissionRequest(
            session_id="s1",
            source_kind=TurnSourceKind.INITIAL,
            caller_id="workflow-1",
            idempotency_key="initial",
            instruction_digest="turn-digest",
            requested_dimensions=dimensions,
        ),
        moonmind_workflow_id="wf-1",
        provider="omnigent",
        new_session_id="s1",
        provider_profile_id="profile-1",
    )
    evidence = RecoveryEvidence(
        intent_dimensions=dimensions,
        session_dimensions=dimensions,
        workspace_artifact_valid=True,
        session_evidence_valid=True,
    )
    first, second = await asyncio.gather(
        service.decide_session_recovery(
            "s1",
            recovery_idempotency_key="recovery-1",
            intent_dimensions=dimensions,
            live_authority=evidence,
        ),
        service.decide_session_recovery(
            "s1",
            recovery_idempotency_key="recovery-1",
            intent_dimensions=dimensions,
            live_authority=evidence,
        ),
    )
    assert first == second
    async with pg_store.transaction() as repos:
        decisions = await repos.decisions.list_for_session("s1")
    assert [
        decision.decision_code
        for decision in decisions
        if decision.decision_code.startswith("checkpoint_recovery:")
    ] == ["checkpoint_recovery:cold_restore"]

    with pytest.raises(TurnIdempotencyConflictError):
        await service.decide_session_recovery(
            "s1",
            recovery_idempotency_key="recovery-1",
            intent_dimensions=ImmutableSessionDimensions(
                **{
                    **dimensions.__dict__,
                    "branch": "changed-branch",
                }
            ),
            live_authority=evidence,
        )


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
