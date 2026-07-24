from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from temporalio.exceptions import CancelledError

from moonmind.workflows.executions.control_stop_continuation import (
    ControlStopContinuationContract,
    ControlStopContinuationWorkflowInput,
)
from moonmind.workflows.temporal.workflows.control_stop_continuation import (
    MoonMindControlStopContinuationWorkflow,
)
from tests.unit.workflows.executions.test_control_stop_continuation import _payload


def _contract(**budget_overrides: int) -> ControlStopContinuationContract:
    payload = _payload()
    payload["continuationBudget"].update(budget_overrides)
    return ControlStopContinuationContract.model_validate(payload)


def _workflow_input(contract: ControlStopContinuationContract) -> dict:
    return ControlStopContinuationWorkflowInput.initial(contract).model_dump(
        by_alias=True,
        mode="json",
    )


def _locator(contract: ControlStopContinuationContract) -> dict:
    return {
        "kind": "managed_runtime",
        "runtimeId": "codex_cli",
        "agentRunId": contract.destination_workspace_id,
        "relativePath": "repo",
    }


def _restore(contract: ControlStopContinuationContract) -> dict:
    return {
        "schemaVersion": "v1",
        "status": "succeeded",
        "checkpointRef": contract.checkpoint_ref,
        "destinationWorkspaceLocator": _locator(contract),
        "restorationEvidenceRef": "artifact://restore/evidence",
        "restorationEvidenceDigest": f"sha256:{'e' * 64}",
        "baseCommit": contract.workspace_base_commit,
        "restoredEntryCount": 2,
        "restoredBytes": 20,
        "gitStatusDigest": f"sha256:{'f' * 64}",
        "idempotencyKey": f"{contract.destination_workflow_id}:restore",
    }


def _remediation(*, failed: bool = False) -> dict:
    return {
        "outputRefs": ["artifact://remediation/result"],
        "summary": "Remediation attempt completed.",
        "failureClass": "execution_error" if failed else None,
        "metadata": {
            "omnigentSessionId": "session-7",
            "externalStateRef": "artifact://omnigent/state",
        },
    }


def _capture(contract: ControlStopContinuationContract, attempt: int) -> dict:
    return {
        "schemaVersion": "v1",
        "status": "captured",
        "checkpointKind": "worktree_archive",
        "workspace": {
            "kind": "worktree_archive",
            "baseCommit": contract.workspace_base_commit,
            "archiveRef": f"artifact://workspace/C{attempt}",
            "archiveDigest": f"sha256:{str(attempt % 10) * 64}",
            "manifestRef": f"artifact://workspace/C{attempt}/manifest",
            "manifestDigest": f"sha256:{str((attempt + 1) % 10) * 64}",
        },
        "sourceWorkspaceLocator": _locator(contract),
        "idempotencyKey": (
            f"{contract.destination_workflow_id}:remediation:{attempt}:capture"
        ),
    }


def _verification(
    attempt: int,
    *,
    verdict: str,
    progress: bool,
    remaining_work: bool = True,
) -> dict:
    return {
        "outputRefs": [f"artifact://gate/{attempt}"],
        "summary": f"Verifier returned {verdict}.",
        "metadata": {
            "normalizedStatus": "completed",
            "omnigentSessionId": f"verify-session-{attempt}",
            "controlStopVerification": {
                "verdict": verdict,
                "verificationRef": f"artifact://gate/{attempt}",
                "remainingWorkRef": (
                    f"artifact://remaining/{attempt}" if remaining_work else None
                ),
                "progress": progress,
                "progressEvidenceRef": (
                    f"artifact://progress/{attempt}" if progress else None
                ),
            },
        },
    }


def _patch_runtime(monkeypatch, contract, execute) -> None:
    info = type(
        "Info",
        (),
        {"workflow_id": contract.destination_workflow_id, "run_id": "destination-run"},
    )()
    monkeypatch.setattr(
        "moonmind.workflows.temporal.workflows.control_stop_continuation.workflow.info",
        lambda: info,
    )
    monkeypatch.setattr(
        "moonmind.workflows.temporal.workflows.control_stop_continuation.workflow.execute_activity",
        execute,
    )


@pytest.mark.asyncio
async def test_workflow_restores_then_runs_profile_bound_loop_to_acceptance(
    monkeypatch,
) -> None:
    contract = _contract()
    execute = AsyncMock(
        side_effect=[
            _restore(contract),
            _remediation(),
            _capture(contract, 7),
            _verification(
                7,
                verdict="FULLY_IMPLEMENTED",
                progress=True,
                remaining_work=False,
            ),
        ]
    )
    _patch_runtime(monkeypatch, contract, execute)

    result = await MoonMindControlStopContinuationWorkflow().run(
        _workflow_input(contract)
    )

    assert result["status"] == "accepted"
    assert result["nextSemanticOperation"] == "publication_gate"
    assert result["candidateState"] == "accepted_complete"
    assert result["latestWorkspaceHeadRef"] == "artifact://workspace/C7"
    assert result["continuationBudget"]["consumedAttempts"] == 1
    assert result["skippedSideEffects"][0]["disposition"] == "already_performed"
    assert result["hostSessionLifecycle"]["activityCleanupCompleted"] is True
    assert result["hostSessionLifecycle"]["remediationOmnigentSessionId"] == (
        "session-7"
    )
    assert result["hostSessionLifecycle"]["verificationOmnigentSessionId"] == (
        "verify-session-7"
    )
    assert [call.args[0] for call in execute.await_args_list] == [
        "agent_runtime.restore_workspace_checkpoint",
        "integration.omnigent.profile_bound_execute",
        "agent_runtime.capture_workspace_checkpoint",
        "integration.omnigent.profile_bound_execute",
    ]
    assert execute.await_args_list[1].args[1]["instructionRef"] == (
        contract.remaining_work_ref
    )
    assert (
        execute.await_args_list[3].args[1]["parameters"]["omnigent"]["prompt"][
            "instructionRef"
        ]
        == contract.verification_instruction_ref
    )


@pytest.mark.asyncio
async def test_additional_work_retries_from_latest_candidate_without_replaying_restore(
    monkeypatch,
) -> None:
    contract = _contract(maxAttempts=3, maxConsecutiveNoProgressAttempts=2)
    execute = AsyncMock(
        side_effect=[
            _restore(contract),
            _remediation(),
            _capture(contract, 7),
            _verification(7, verdict="ADDITIONAL_WORK_NEEDED", progress=True),
            _remediation(),
            _capture(contract, 8),
            _verification(
                8,
                verdict="FULLY_IMPLEMENTED",
                progress=True,
                remaining_work=False,
            ),
        ]
    )
    _patch_runtime(monkeypatch, contract, execute)

    result = await MoonMindControlStopContinuationWorkflow().run(
        _workflow_input(contract)
    )

    assert result["status"] == "accepted"
    assert result["continuationBudget"]["consumedAttempts"] == 2
    assert [attempt["attemptOrdinal"] for attempt in result["attempts"]] == [7, 8]
    second_remediation = execute.await_args_list[4].args[1]
    assert second_remediation["instructionRef"] == "artifact://remaining/7"
    assert "artifact://workspace/C7" in second_remediation["inputRefs"]
    assert (
        sum(
            call.args[0] == "agent_runtime.restore_workspace_checkpoint"
            for call in execute.await_args_list
        )
        == 1
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("max_attempts", "max_no_progress", "progress", "reason"),
    [
        (1, 1, True, "continuation_attempt_budget_exhausted"),
        (3, 1, False, "continuation_no_progress_budget_exhausted"),
    ],
)
async def test_workflow_stops_at_frozen_budget_boundary(
    monkeypatch,
    max_attempts,
    max_no_progress,
    progress,
    reason,
) -> None:
    contract = _contract(
        maxAttempts=max_attempts,
        maxConsecutiveNoProgressAttempts=max_no_progress,
    )
    execute = AsyncMock(
        side_effect=[
            _restore(contract),
            _remediation(),
            _capture(contract, 7),
            _verification(
                7,
                verdict="ADDITIONAL_WORK_NEEDED",
                progress=progress,
            ),
        ]
    )
    _patch_runtime(monkeypatch, contract, execute)

    result = await MoonMindControlStopContinuationWorkflow().run(
        _workflow_input(contract)
    )

    assert result["status"] == "control_stop"
    assert result["reasonCode"] == reason
    assert result["latestWorkspaceHeadRef"] == "artifact://workspace/C7"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("verdict", "status"),
    [
        ("BLOCKED", "blocked"),
        ("ENVIRONMENT_CONTAMINATED_BY_SKILL_PROJECTION", "blocked"),
        ("FAILED_UNRECOVERABLE", "failed"),
    ],
)
async def test_terminal_verifier_outcomes_preserve_candidate(
    monkeypatch,
    verdict,
    status,
) -> None:
    contract = _contract()
    execute = AsyncMock(
        side_effect=[
            _restore(contract),
            _remediation(),
            _capture(contract, 7),
            _verification(7, verdict=verdict, progress=False),
        ]
    )
    _patch_runtime(monkeypatch, contract, execute)

    result = await MoonMindControlStopContinuationWorkflow().run(
        _workflow_input(contract)
    )

    assert result["status"] == status
    assert result["reasonCode"] == verdict
    assert result["latestWorkspaceHeadRef"] == "artifact://workspace/C7"
    assert result["attempts"][0]["verificationRef"] == "artifact://gate/7"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("side_effect", "failure_phase"),
    [
        ([RuntimeError("restore")], "restoration"),
        (
            [
                "RESTORE",
                RuntimeError("remediation"),
            ],
            "remediation",
        ),
        (
            [
                "RESTORE",
                "REMEDIATION",
                RuntimeError("capture"),
            ],
            "candidate_capture",
        ),
        (
            [
                "RESTORE",
                "REMEDIATION",
                "CAPTURE",
                RuntimeError("verification"),
            ],
            "verification",
        ),
    ],
)
async def test_activity_failures_are_phase_specific_and_do_not_fallback(
    monkeypatch,
    side_effect,
    failure_phase,
) -> None:
    contract = _contract()
    replacements = {
        "RESTORE": _restore(contract),
        "REMEDIATION": _remediation(),
        "CAPTURE": _capture(contract, 7),
    }
    effects = [replacements.get(item, item) for item in side_effect]
    execute = AsyncMock(side_effect=effects)
    _patch_runtime(monkeypatch, contract, execute)

    result = await MoonMindControlStopContinuationWorkflow().run(
        _workflow_input(contract)
    )

    assert result["status"] == "failed"
    assert result["failurePhase"] == failure_phase
    activity_types = [call.args[0] for call in execute.await_args_list]
    assert set(activity_types) <= {
        "agent_runtime.restore_workspace_checkpoint",
        "integration.omnigent.profile_bound_execute",
        "agent_runtime.capture_workspace_checkpoint",
    }


@pytest.mark.asyncio
async def test_cancellation_after_capture_preserves_latest_candidate(
    monkeypatch,
) -> None:
    contract = _contract()
    execute = AsyncMock(
        side_effect=[
            _restore(contract),
            _remediation(),
            _capture(contract, 7),
            CancelledError(),
        ]
    )
    _patch_runtime(monkeypatch, contract, execute)

    continuation = MoonMindControlStopContinuationWorkflow()
    with pytest.raises(CancelledError):
        await continuation.run(_workflow_input(contract))
    result = continuation.continuation_state()

    assert result["status"] == "canceled"
    assert result["failurePhase"] == "verification"
    assert result["latestWorkspaceHeadRef"] == "artifact://workspace/C7"
    assert result["continuationBudget"]["consumedAttempts"] == 0


@pytest.mark.asyncio
async def test_profile_failure_and_unbound_verifier_ref_fail_closed(
    monkeypatch,
) -> None:
    contract = _contract()
    remediation_failure = AsyncMock(
        side_effect=[_restore(contract), _remediation(failed=True)]
    )
    _patch_runtime(monkeypatch, contract, remediation_failure)
    failed = await MoonMindControlStopContinuationWorkflow().run(
        _workflow_input(contract)
    )
    assert failed["status"] == "failed"
    assert failed["failurePhase"] == "remediation"
    assert failed["reasonCode"] == "execution_error"

    unbound_verification = _verification(
        7,
        verdict="FULLY_IMPLEMENTED",
        progress=True,
        remaining_work=False,
    )
    unbound_verification["outputRefs"] = ["artifact://different-verifier"]
    execute = AsyncMock(
        side_effect=[
            _restore(contract),
            _remediation(),
            _capture(contract, 7),
            unbound_verification,
        ]
    )
    _patch_runtime(monkeypatch, contract, execute)
    rejected = await MoonMindControlStopContinuationWorkflow().run(
        _workflow_input(contract)
    )
    assert rejected["status"] == "failed"
    assert rejected["failurePhase"] == "verification"
    assert rejected["reasonCode"] == "CONTROL_STOP_VERIFICATION_REF_MISMATCH"
    assert rejected["latestWorkspaceHeadRef"] == "artifact://workspace/C7"


@pytest.mark.asyncio
async def test_continue_as_new_transfers_full_state_and_does_not_restore_again(
    monkeypatch,
) -> None:
    contract_payload = _payload()
    contract_payload["continuationBudget"]["maxAttempts"] = 2
    contract_payload["continuationBudget"]["maxConsecutiveNoProgressAttempts"] = 2
    contract_payload["continueAsNewAfterAttempts"] = 1
    contract = ControlStopContinuationContract.model_validate(contract_payload)
    execute = AsyncMock(
        side_effect=[
            _restore(contract),
            _remediation(),
            _capture(contract, 7),
            _verification(7, verdict="ADDITIONAL_WORK_NEEDED", progress=True),
        ]
    )
    _patch_runtime(monkeypatch, contract, execute)
    carried: dict = {}

    def _capture_continue_as_new(payload):
        carried.update(deepcopy(payload))
        raise RuntimeError("continue-as-new-issued")

    monkeypatch.setattr(
        "moonmind.workflows.temporal.workflows.control_stop_continuation.workflow.continue_as_new",
        _capture_continue_as_new,
    )
    with pytest.raises(RuntimeError, match="continue-as-new-issued"):
        await MoonMindControlStopContinuationWorkflow().run(_workflow_input(contract))

    state = carried["state"]
    assert state["continueAsNewCount"] == 1
    assert state["continuationBudget"]["consumedAttempts"] == 1
    assert state["latestWorkspaceHeadRef"] == "artifact://workspace/C7"

    resumed_execute = AsyncMock(
        side_effect=[
            _remediation(),
            _capture(contract, 8),
            _verification(
                8,
                verdict="FULLY_IMPLEMENTED",
                progress=True,
                remaining_work=False,
            ),
        ]
    )
    _patch_runtime(monkeypatch, contract, resumed_execute)
    result = await MoonMindControlStopContinuationWorkflow().run(carried)

    assert result["status"] == "accepted"
    assert result["continueAsNewCount"] == 1
    assert result["continuationBudget"]["consumedAttempts"] == 2
    assert resumed_execute.await_args_list[0].args[0] == (
        "integration.omnigent.profile_bound_execute"
    )
