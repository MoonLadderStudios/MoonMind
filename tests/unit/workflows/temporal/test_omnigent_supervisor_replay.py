"""MoonLadderStudios/MoonMind#3712 supervisor-migration replay-safety tests.

These prove that introducing ``MoonMind.OmnigentSession`` supervisor admission
behind a Temporal patch keeps pre-patch (legacy) histories valid on the target
worker build, and that a new-generation admission never reinterprets an
already-admitted workflow. The other representative production histories
(UserWorkflow / AgentRun / remediation / checkpoint) are already covered by
``test_run_replayer.py``; this file adds the supervisor-migration scenario the
migration introduces.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from temporalio import workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

with workflow.unsafe.imports_passed_through():
    from moonmind.omnigent.session_supervisor_admission import (
        SupervisorAdmissionRequest,
        SupervisorReadiness,
        SupervisorRolloutPolicy,
        evaluate_supervisor_admission,
    )

# The patch that gates the migration to supervisor admission. Versioned so the
# legacy branch stays registered for the retention window.
SUPERVISOR_ADMISSION_REPLAY_PATCH = "omnigent-session-supervisor-admission-v1"


def _replay_policy() -> SupervisorRolloutPolicy:
    return SupervisorRolloutPolicy(
        enabled=True,
        shadow=False,
        generation="gen-1",
        allowed_owner_ids=frozenset({"owner-1"}),
    )


def _replay_readiness() -> SupervisorReadiness:
    return SupervisorReadiness(
        deploymentGeneration="gen-1",
        supervisorWorkflowRegistered=True,
        compiledIntentReady=True,
        canonicalSchemaReady=True,
        exactArtifactConformancePassed=True,
        providerCapabilityReady=True,
        runtimeCapabilityReady=True,
        rollbackSupportActive=True,
        historicalReadSupportActive=True,
    )


def _replay_request() -> SupervisorAdmissionRequest:
    return SupervisorAdmissionRequest(
        ownerId="owner-1",
        executionProfileRef="exec-1",
        launchPolicyRef="launch-1",
        providerProfileId="profile-1",
    )


@workflow.defn(name="MM3712OmnigentSupervisorAdmissionReplayFixture")
class _LegacySupervisorAdmissionReplayFixture:
    """Pre-patch history: no supervisor admission gating existed."""

    @workflow.run
    async def run(self) -> dict[str, Any]:
        return {"admitted": False, "mode": "legacy"}


@workflow.defn(name="MM3712OmnigentSupervisorAdmissionReplayFixture")
class _CurrentSupervisorAdmissionReplayFixture:
    """Post-patch history: admission is computed deterministically and frozen."""

    @workflow.run
    async def run(self) -> dict[str, Any]:
        # Snapshot the patch decision at workflow start, before any branch, so
        # replay stays stable (mirrors the run.py patch-snapshot convention).
        use_supervisor = workflow.patched(SUPERVISOR_ADMISSION_REPLAY_PATCH)
        if not use_supervisor:
            return {"admitted": False, "mode": "legacy"}
        snapshot = evaluate_supervisor_admission(
            policy=_replay_policy(),
            readiness=_replay_readiness(),
            request=_replay_request(),
        )
        return {"admitted": snapshot.admitted, "mode": snapshot.mode}


@pytest.mark.asyncio
async def test_supervisor_admission_pre_and_post_patch_histories_replay() -> None:
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-mm3712-supervisor-legacy",
            workflows=[_LegacySupervisorAdmissionReplayFixture],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            legacy = await env.client.start_workflow(
                _LegacySupervisorAdmissionReplayFixture.run,
                id="test-mm3712-supervisor-legacy",
                task_queue="test-mm3712-supervisor-legacy",
            )
            assert await legacy.result() == {"admitted": False, "mode": "legacy"}
            legacy_history = await legacy.fetch_history()

        async with Worker(
            env.client,
            task_queue="test-mm3712-supervisor-current",
            workflows=[_CurrentSupervisorAdmissionReplayFixture],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            current = await env.client.start_workflow(
                _CurrentSupervisorAdmissionReplayFixture.run,
                id="test-mm3712-supervisor-current",
                task_queue="test-mm3712-supervisor-current",
            )
            assert await current.result() == {"admitted": True, "mode": "live"}
            current_history = await current.fetch_history()

    # The target worker build must keep BOTH histories replay-safe: the legacy
    # (pre-patch) history keeps the old meaning, and the current history uses the
    # gated supervisor-admission path.
    replayer = Replayer(
        workflows=[_CurrentSupervisorAdmissionReplayFixture],
        workflow_runner=UnsandboxedWorkflowRunner(),
    )
    await replayer.replay_workflow(legacy_history)
    await replayer.replay_workflow(current_history)


def test_supervisor_admission_replay_patch_is_versioned_and_snapshotted() -> None:
    # A versioned patch keeps the legacy activity/workflow route registered for
    # the retention window; the decision is snapshotted before any branch.
    assert SUPERVISOR_ADMISSION_REPLAY_PATCH.endswith("-v1")
    source = inspect.getsource(_CurrentSupervisorAdmissionReplayFixture.run)
    # The workflow gates on the versioned patch constant exactly once, snapshotted
    # before any branch reads it.
    assert source.count("SUPERVISOR_ADMISSION_REPLAY_PATCH") == 1
    assert source.index("workflow.patched(") < source.index("if not use_supervisor")


def test_new_generation_does_not_reinterpret_admitted_snapshot() -> None:
    # An already-admitted workflow carries its immutable snapshot. Re-evaluating
    # under a later generation yields a mismatch but never mutates the original
    # decision — the replay-safety guarantee for admitted sessions.
    admitted = evaluate_supervisor_admission(
        policy=_replay_policy(),
        readiness=_replay_readiness(),
        request=_replay_request(),
    )
    assert admitted.admitted is True
    assert admitted.generation == "gen-1"

    next_generation = SupervisorRolloutPolicy(
        enabled=True, shadow=False, generation="gen-2", allowed_owner_ids=frozenset({"owner-1"})
    )
    re_evaluated = evaluate_supervisor_admission(
        policy=next_generation,
        readiness=_replay_readiness(),  # still deployment_generation == gen-1
        request=_replay_request(),
    )
    assert re_evaluated.reason_code == "deployment_generation_mismatch"
    # The original admitted snapshot is unchanged (frozen model).
    assert admitted.generation == "gen-1"
    assert admitted.admitted is True


# --- #3707 submit_authorized_continuation turnSource cutover ------------------


def _pre_cutover_signal():
    """A payload as it exists in an in-flight history from before #3707.

    The field did not exist, so the key is absent -- not empty. This is exactly
    what a replayed signal delivers to the current worker build.
    """

    from moonmind.schemas.omnigent_session_models import OmnigentSessionSignal

    return OmnigentSessionSignal.model_validate(
        {
            "requestId": "mm-pre-cutover-1",
            "turnAttemptId": "turn-pre-cutover-1",
            "instructionRef": "artifact://instructions/pre-cutover",
        }
    )


def test_pre_cutover_continuation_signal_replays_with_a_deterministic_source() -> None:
    """In-flight safety: a signal without turnSource must not wedge the run.

    #3707 made ``turnSource`` mandatory for new submissions. A signal already
    recorded in a ``MoonMind.OmnigentSession`` history predates the field, and a
    replayed signal handler that raised on it would fail the workflow task
    forever for a run that was legitimately admitted. The handler therefore
    resolves the one deterministic pre-cutover source -- the same value migration
    ``358_omnigent_turn_source`` gives the rows those signals produced -- and
    queues exactly one intent carrying it.
    """

    from moonmind.omnigent.turn_contracts import PRE_CUTOVER_SIGNAL_TURN_SOURCE
    from moonmind.workflows.temporal.workflows.omnigent_session import (
        MoonMindOmnigentSessionWorkflow,
    )

    supervisor = MoonMindOmnigentSessionWorkflow()
    before = supervisor._turn_attempt_count

    supervisor.submit_authorized_turn(_pre_cutover_signal())

    assert supervisor._turn_attempt_count == before + 1
    assert len(supervisor._pending_signal_intents) == 1
    intent = supervisor._pending_signal_intents[0]
    assert intent["kind"] == "submit_authorized_continuation"
    assert intent["payload"]["turnSource"] == PRE_CUTOVER_SIGNAL_TURN_SOURCE.value
    assert intent["payload"]["turnAttemptId"] == "turn-pre-cutover-1"


def test_post_cutover_signal_keeps_its_admitted_source_and_fails_closed() -> None:
    """A present source is never substituted, and an unknown one is refused."""

    from pydantic import ValidationError

    from moonmind.schemas.omnigent_session_models import OmnigentSessionSignal
    from moonmind.workflows.temporal.workflows.omnigent_session import (
        MoonMindOmnigentSessionWorkflow,
    )

    supervisor = MoonMindOmnigentSessionWorkflow()
    supervisor.submit_authorized_turn(
        OmnigentSessionSignal.model_validate(
            {
                "requestId": "mm-post-cutover-1",
                "turnAttemptId": "turn-post-cutover-1",
                "instructionRef": "artifact://instructions/post-cutover",
                "turnSource": "remediation",
            }
        )
    )
    assert (
        supervisor._pending_signal_intents[0]["payload"]["turnSource"]
        == "remediation"
    )

    # The compatibility path is only for an *absent* field: the closed
    # vocabulary still fails closed for anything else, including the pre-#3707
    # free-form lineage value the column used to hold.
    with pytest.raises(ValidationError, match="Unknown Omnigent turn source"):
        OmnigentSessionSignal.model_validate(
            {
                "requestId": "mm-bad-1",
                "turnAttemptId": "turn-bad-1",
                "instructionRef": "artifact://instructions/bad",
                "turnSource": "continuation",
            }
        )


def test_missing_turn_identity_still_fails_the_continuation_signal() -> None:
    """Compatibility widened one field only; the rest stays fail-closed."""

    from moonmind.schemas.omnigent_session_models import OmnigentSessionSignal
    from moonmind.workflows.temporal.workflows.omnigent_session import (
        MoonMindOmnigentSessionWorkflow,
    )

    supervisor = MoonMindOmnigentSessionWorkflow()
    with pytest.raises(ValueError, match="turnAttemptId and instructionRef"):
        supervisor.submit_authorized_turn(
            OmnigentSessionSignal.model_validate({"requestId": "mm-empty-1"})
        )


@pytest.mark.asyncio
async def test_pre_cutover_continuation_signal_persists_the_mapped_source(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Activity that owns durable authority applies the same mapping.

    Signal handling and durable persistence are two boundaries; a replayed
    pre-cutover signal must survive both. This drives the real
    ``omnigent.persist_signal_intents`` Activity against a real control-plane
    store and asserts the turn row records the mapped source rather than failing
    the Activity forever.
    """

    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from api_service.db.models import Base
    from moonmind.omnigent.control_plane import OmnigentControlPlaneStore
    from moonmind.omnigent.turn_contracts import PRE_CUTOVER_SIGNAL_TURN_SOURCE
    from moonmind.workflows.temporal.activities import (
        omnigent_session_activities as activities,
    )

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/replay_signals.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("api_service.db.base.async_session_maker", factory)

    store = OmnigentControlPlaneStore(factory)
    session, _turn = await store.establish_session(
        session_id="oms_replay",
        moonmind_workflow_id="mm:w-replay",
        provider="omnigent",
        provider_session_ref="prov-replay",
        chat_binding_id="cb-replay",
        first_turn_attempt_id="oms_replay-t0",
        first_turn_idempotency_key="oms_replay-idem-0",
    )

    payload = _pre_cutover_signal().model_dump(
        mode="json", by_alias=True, exclude_none=True
    )
    assert "turnSource" not in payload

    result = await activities.omnigent_persist_signal_intents_activity(
        {
            "sessionId": "oms_replay",
            "compiledExecutionIntentRef": "artifact://intent/replay",
            "compiledExecutionIntentDigest": "sha256:" + "a" * 64,
            "expectedRevision": session.revision,
            "fencingGeneration": session.fencing_generation,
            "signals": [
                {"kind": "submit_authorized_continuation", "payload": payload}
            ],
        }
    )
    assert result["appliedIntentCount"] == 1

    async with store.transaction() as repos:
        turns = await repos.turn_attempts.list_for_session("oms_replay")
    replayed = [item for item in turns if item.turn_attempt_id == "turn-pre-cutover-1"]
    assert len(replayed) == 1
    assert replayed[0].turn_source == PRE_CUTOVER_SIGNAL_TURN_SOURCE.value

    # Redelivery of the same pre-cutover signal is recognized as already
    # applied. This is the property the mapping has to preserve: the row the old
    # code wrote (``lineage_kind='continuation'``, migrated to
    # ``repository_continuation``) is the same source the replayed signal
    # resolves to, so the idempotency check still matches and no duplicate turn
    # is created across the cutover.
    async with store.transaction() as repos:
        current = await repos.sessions.get("oms_replay")
    again = await activities.omnigent_persist_signal_intents_activity(
        {
            "sessionId": "oms_replay",
            "compiledExecutionIntentRef": "artifact://intent/replay",
            "compiledExecutionIntentDigest": "sha256:" + "a" * 64,
            "expectedRevision": current.revision,
            "fencingGeneration": current.fencing_generation,
            "signals": [
                {"kind": "submit_authorized_continuation", "payload": payload}
            ],
        }
    )
    assert again["appliedIntentCount"] == 1
    async with store.transaction() as repos:
        after = await repos.turn_attempts.list_for_session("oms_replay")
    assert len(after) == len(turns)
    await engine.dispose()
