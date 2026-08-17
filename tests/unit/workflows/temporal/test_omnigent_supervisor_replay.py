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
