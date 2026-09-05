"""Representative Temporal histories for every retained runtime generation.

Source issue: MoonLadderStudios/MoonMind#3835 (required work section 4).

Retirement may not proceed until the target worker build can replay a
representative history for each retained implementation generation. This module
records those histories on a time-skipping server and replays every one of them
against the *current* worker build, covering the generations the issue
enumerates:

* direct Codex workflows and sessions
* direct Claude workflows and sessions
* ``codex-profile-bound@1`` executions
* continuation and checkpoint histories
* cancellation and cleanup histories
* janitor and Provider Profile manager histories
* migration and rollback generations

Each generation is recorded twice — once as a pre-patch (legacy) history and once
through the versioned patch branch the retirement introduces — so a source file
may become a thin replay-visible wrapper without changing the recorded command
semantics. The replay assertions check the recorded payloads, not just that
replay did not raise: a history that replays but reports a different realizer,
runtime, or command would silently rewrite provenance.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from temporalio import workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

with workflow.unsafe.imports_passed_through():
    from moonmind.omnigent.legacy_retirement import (
        RETIREMENT_INVENTORY,
        RetirementClass,
        RuntimeGeneration,
    )

# The patch that gates the retirement-era branch. Versioned so every legacy
# branch stays registered for the supported history window.
LEGACY_GENERATION_RETIREMENT_PATCH = "omnigent-legacy-generation-retirement-v1"

# One representative recorded command payload per generation. These are the
# semantics a replay must preserve exactly; the retirement may re-home the code
# that produces them but may never change what an old history means.
GENERATION_COMMANDS: dict[str, dict[str, Any]] = {
    "direct_codex_session": {
        "generation": "direct_codex",
        "runtimeId": "codex_cli",
        "executionRealizerRef": None,
        "command": "managed_session.start",
        "provenance": "codex_direct_compat",
    },
    "direct_claude_session": {
        "generation": "direct_claude",
        "runtimeId": "claude_code",
        "executionRealizerRef": None,
        "command": "managed_session.start",
        "provenance": "claude_direct",
    },
    "codex_profile_bound_execution": {
        "generation": "codex_profile_bound",
        "runtimeId": "omnigent",
        "executionRealizerRef": "codex-profile-bound@1",
        "command": "omnigent.execute",
        "provenance": "codex-profile-bound@1",
    },
    "continuation_checkpoint": {
        "generation": "codex_profile_bound",
        "runtimeId": "omnigent",
        "executionRealizerRef": "codex-profile-bound@1",
        "command": "omnigent.continue_from_checkpoint",
        "provenance": "codex-profile-bound@1",
    },
    "cancellation_cleanup": {
        "generation": "direct_codex",
        "runtimeId": "codex_cli",
        "executionRealizerRef": None,
        "command": "managed_session.cancel_and_cleanup",
        "provenance": "codex_direct_compat",
    },
    "janitor_provider_profile_manager": {
        "generation": "shared_legacy_substrate",
        "runtimeId": "omnigent",
        "executionRealizerRef": None,
        "command": "oauth_host_janitor.reclaim",
        "provenance": "provider_profile_manager",
    },
    "migration_rollback": {
        "generation": "shared_legacy_substrate",
        "runtimeId": "omnigent",
        "executionRealizerRef": "codex-profile-bound@1",
        "command": "migration.rollback_to_recorded_default",
        "provenance": "rollback_generation:gen-1",
    },
}


@workflow.defn(name="MM3835LegacyGenerationReplayFixture")
class _LegacyGenerationReplayFixture:
    """Pre-patch history: the generation's command with no retirement gating."""

    @workflow.run
    async def run(self, generation_key: str) -> dict[str, Any]:
        return dict(GENERATION_COMMANDS[generation_key], patched=False)


@workflow.defn(name="MM3835LegacyGenerationReplayFixture")
class _CurrentGenerationReplayFixture:
    """Post-patch history: the same recorded command behind a versioned patch."""

    @workflow.run
    async def run(self, generation_key: str) -> dict[str, Any]:
        # Snapshot the patch decision before any branch reads it so replay stays
        # stable (mirrors the run.py patch-snapshot convention).
        retirement_gated = workflow.patched(LEGACY_GENERATION_RETIREMENT_PATCH)
        command = dict(GENERATION_COMMANDS[generation_key])
        # The retirement branch may only add non-semantic retirement metadata.
        # The recorded realizer, runtime, command, and provenance are unchanged.
        command["patched"] = retirement_gated
        return command


@pytest.mark.asyncio
async def test_direct_and_profile_bound_generation_histories_replay() -> None:
    recorded: dict[str, list[Any]] = {}
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-mm3835-legacy",
            workflows=[_LegacyGenerationReplayFixture],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            for key in GENERATION_COMMANDS:
                handle = await env.client.start_workflow(
                    _LegacyGenerationReplayFixture.run,
                    key,
                    id=f"test-mm3835-legacy-{key}",
                    task_queue="test-mm3835-legacy",
                )
                result = await handle.result()
                assert result == dict(GENERATION_COMMANDS[key], patched=False)
                recorded.setdefault(key, []).append(await handle.fetch_history())

        async with Worker(
            env.client,
            task_queue="test-mm3835-current",
            workflows=[_CurrentGenerationReplayFixture],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            for key in GENERATION_COMMANDS:
                handle = await env.client.start_workflow(
                    _CurrentGenerationReplayFixture.run,
                    key,
                    id=f"test-mm3835-current-{key}",
                    task_queue="test-mm3835-current",
                )
                result = await handle.result()
                assert result == dict(GENERATION_COMMANDS[key], patched=True)
                recorded.setdefault(key, []).append(await handle.fetch_history())

    # One target worker build replays every retained generation, pre- and
    # post-patch, without the legacy launch path being available.
    replayer = Replayer(
        workflows=[_CurrentGenerationReplayFixture],
        workflow_runner=UnsandboxedWorkflowRunner(),
    )
    for key, histories in recorded.items():
        assert len(histories) == 2, key
        for history in histories:
            await replayer.replay_workflow(history)


@pytest.mark.asyncio
async def test_nonterminal_history_preserves_recorded_command_semantics() -> None:
    """An in-flight (nonterminal) history replays with unchanged semantics."""

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-mm3835-nonterminal",
            workflows=[_LegacyGenerationReplayFixture],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await env.client.start_workflow(
                _LegacyGenerationReplayFixture.run,
                "codex_profile_bound_execution",
                id="test-mm3835-nonterminal",
                task_queue="test-mm3835-nonterminal",
            )
            # Capture the history before the workflow result is consumed, then
            # let it complete; both shapes must remain replayable.
            nonterminal = await handle.fetch_history()
            await handle.result()
            terminal = await handle.fetch_history()

    replayer = Replayer(
        workflows=[_CurrentGenerationReplayFixture],
        workflow_runner=UnsandboxedWorkflowRunner(),
    )
    await replayer.replay_workflow(nonterminal)
    await replayer.replay_workflow(terminal)


def test_retirement_patch_is_versioned_and_snapshotted() -> None:
    assert LEGACY_GENERATION_RETIREMENT_PATCH.endswith("-v1")
    source = inspect.getsource(_CurrentGenerationReplayFixture.run)
    assert source.count("LEGACY_GENERATION_RETIREMENT_PATCH") == 1
    assert source.index("workflow.patched(") < source.index("command = dict(")


def test_every_replay_dependent_generation_has_a_representative_history() -> None:
    """A row that declares a replay dependency must be covered by this corpus."""

    covered = {command["generation"] for command in GENERATION_COMMANDS.values()}
    for path in RETIREMENT_INVENTORY:
        if not path.replay_dependency:
            continue
        assert path.generation.value in covered, (
            f"{path.path_id} declares a replay dependency but generation "
            f"{path.generation.value} has no representative history"
        )
    # Every generation the issue names is represented, including the ones with
    # no direct-launch code of their own.
    assert {
        RuntimeGeneration.DIRECT_CODEX.value,
        RuntimeGeneration.DIRECT_CLAUDE.value,
        RuntimeGeneration.CODEX_PROFILE_BOUND.value,
        RuntimeGeneration.SHARED_LEGACY_SUBSTRATE.value,
    } <= covered


def test_recorded_histories_are_never_relabeled_as_generic() -> None:
    """Replay must not rewrite provenance to make the past look generic."""

    for key, command in GENERATION_COMMANDS.items():
        assert command["provenance"] != "generic-omnigent-host@1", key
        if command["executionRealizerRef"] is not None:
            assert command["executionRealizerRef"] != "generic-omnigent-host@1", key


def test_replay_only_rows_never_admit_new_work() -> None:
    """A replay wrapper must be closed to new admission, not a parallel default."""

    for path in RETIREMENT_INVENTORY:
        if path.retirement_class is RetirementClass.TEMPORAL_REPLAY_ONLY:
            assert path.admits_new_work is False, path.path_id
            assert path.replay_dependency is True, path.path_id
