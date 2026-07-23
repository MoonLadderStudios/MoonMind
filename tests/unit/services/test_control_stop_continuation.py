from copy import deepcopy

import pytest

from moonmind.services.control_stop_continuation import (
    ControlStopContinuationReservation,
    ControlStopSourceEvidence,
    admit_control_stop_continuation,
)
from moonmind.workflows.executions.control_stop_continuation import (
    ContinuationBudgetGrant,
    ControlStopContinuationError,
)
from tests.unit.workflows.executions.test_control_stop_continuation import _payload


def _evidence(payload: dict) -> ControlStopSourceEvidence:
    refs = {
        payload["gateResultRef"]: payload["gateResultDigest"],
        payload["remainingWorkRef"]: payload["remainingWorkDigest"],
        payload["workspaceHeadRef"]: payload["workspaceHeadDigest"],
        payload["workspaceManifestRef"]: payload["workspaceManifestDigest"],
        payload["taskInputSnapshotRef"]: payload["taskInputSnapshotDigest"],
        payload["planRef"]: payload["planDigest"],
        payload["lane"]["launchPolicyRef"]: payload["lane"]["launchPolicyDigest"],
        payload["lane"]["capabilitySnapshotRef"]: payload["lane"][
            "capabilitySnapshotDigest"
        ],
        payload["lane"]["effectiveLaunchSnapshotRef"]: payload["lane"][
            "effectiveLaunchSnapshotDigest"
        ],
    }
    return ControlStopSourceEvidence(
        contract_payload=payload,
        artifact_digests=refs,
        current_deployment_generation=payload["deploymentGeneration"],
        deployment_promoted=True,
    )


class _Repository:
    def __init__(self, evidence: ControlStopSourceEvidence) -> None:
        self.evidence = evidence
        self.reservations = {}
        self.reserve_calls = 0

    async def load_source_evidence(self, **_):
        return self.evidence

    async def reserve_destination(self, *, contract):
        self.reserve_calls += 1
        created = contract.destination_workflow_id not in self.reservations
        reservation = ControlStopContinuationReservation(
            destination_workflow_id=contract.destination_workflow_id,
            source_workflow_id=contract.source_workflow_id,
            source_run_id=contract.source_run_id,
            control_stop_id=contract.control_stop_id,
            workspace_head_ref=contract.workspace_head_ref,
            remaining_work_ref=contract.remaining_work_ref,
            created=created,
        )
        self.reservations.setdefault(contract.destination_workflow_id, reservation)
        return reservation


class _Starter:
    def __init__(self) -> None:
        self.workflow_ids = []

    async def start_or_reconcile(self, *, workflow_id, input_payload):
        assert input_payload["deploymentPromoted"] is True
        self.workflow_ids.append(workflow_id)
        return "existing-or-new-run"


def _grant(payload: dict) -> ContinuationBudgetGrant:
    return ContinuationBudgetGrant.model_validate(payload["continuationBudget"])


async def _admit(*, repository, starter, payload):
    return await admit_control_stop_continuation(
        source_workflow_id="source-workflow",
        source_run_id="source-run",
        control_stop_id="verify:control-stop:6",
        continuation_budget=_grant(payload),
        instruction_changes_ref=payload.get("instructionChangesRef"),
        instruction_changes_digest=payload.get("instructionChangesDigest"),
        repository=repository,
        starter=starter,
    )


@pytest.mark.asyncio
async def test_duplicate_admission_reconciles_one_destination() -> None:
    payload = _payload()
    repository = _Repository(_evidence(payload))
    starter = _Starter()

    first = await _admit(repository=repository, starter=starter, payload=payload)
    second = await _admit(repository=repository, starter=starter, payload=payload)

    assert first.destination_workflow_id == second.destination_workflow_id
    assert len(repository.reservations) == 1
    assert starter.workflow_ids == [first.destination_workflow_id] * 2


@pytest.mark.asyncio
async def test_stale_evidence_fails_before_reservation_or_start() -> None:
    payload = _payload()
    evidence = _evidence(payload)
    stale_digests = dict(evidence.artifact_digests)
    stale_digests[payload["remainingWorkRef"]] = "stale"
    repository = _Repository(
        ControlStopSourceEvidence(
            contract_payload=deepcopy(payload),
            artifact_digests=stale_digests,
            current_deployment_generation=payload["deploymentGeneration"],
            deployment_promoted=True,
        )
    )
    starter = _Starter()

    with pytest.raises(ControlStopContinuationError, match="missing or stale"):
        await _admit(repository=repository, starter=starter, payload=payload)

    assert repository.reserve_calls == 0
    assert starter.workflow_ids == []


@pytest.mark.asyncio
async def test_unpromoted_generation_fails_before_mutation() -> None:
    payload = _payload()
    evidence = _evidence(payload)
    repository = _Repository(
        ControlStopSourceEvidence(
            contract_payload=payload,
            artifact_digests=evidence.artifact_digests,
            current_deployment_generation="new-unpromoted-generation",
            deployment_promoted=True,
        )
    )

    with pytest.raises(ControlStopContinuationError, match="currently promoted"):
        await _admit(repository=repository, starter=_Starter(), payload=payload)

    assert repository.reserve_calls == 0


@pytest.mark.asyncio
async def test_budget_mismatch_fails_before_reservation_or_start() -> None:
    payload = _payload()
    repository = _Repository(_evidence(payload))
    starter = _Starter()
    requested = deepcopy(payload)
    requested["continuationBudget"]["maxAttempts"] = 3

    with pytest.raises(ControlStopContinuationError, match="authorized frozen grant"):
        await _admit(repository=repository, starter=starter, payload=requested)

    assert repository.reserve_calls == 0
    assert starter.workflow_ids == []


@pytest.mark.asyncio
async def test_instruction_change_mismatch_fails_before_mutation() -> None:
    payload = _payload()
    repository = _Repository(_evidence(payload))
    starter = _Starter()

    with pytest.raises(ControlStopContinuationError, match="instruction changes"):
        await admit_control_stop_continuation(
            source_workflow_id="source-workflow",
            source_run_id="source-run",
            control_stop_id="verify:control-stop:6",
            continuation_budget=_grant(payload),
            instruction_changes_ref="artifact://instructions/new",
            instruction_changes_digest="sha256:new",
            repository=repository,
            starter=starter,
        )

    assert repository.reserve_calls == 0
    assert starter.workflow_ids == []
