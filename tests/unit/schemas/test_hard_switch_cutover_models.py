from __future__ import annotations

from moonmind.schemas.hard_switch_cutover_models import (
    ContractCutoverStrategy,
    CutoverContractCategory,
    EnvironmentCutoverDecision,
    HardSwitchCutoverRecord,
)


def _valid_payload() -> dict[str, object]:
    return {
        "releaseName": "workflow-language-hard-switch",
        "coordinatedRelease": True,
        "affectedContracts": {
            "workflows": [
                {"name": "MoonMind.UserWorkflow", "strategy": "new_workflow_type"}
            ],
            "activityPayloads": [
                {
                    "name": "RunAgentActivityInput",
                    "strategy": "worker_task_queue_split",
                }
            ],
            "signals": [
                {"name": "RecoverFromFailedStep", "strategy": "version_marker"}
            ],
            "updates": [
                {"name": "RequestRerun", "strategy": "version_marker"}
            ],
        },
        "workerRouting": {
            "previousWorkerBuild": "task-contracts",
            "previousTaskQueue": "mm.workflow",
            "newWorkerBuild": "workflow-contracts",
            "newTaskQueue": "mm.workflow.v2",
            "newStartsBoundary": "renamed_contract_worker",
            "singleBuildServesBothShapes": False,
            "versionBoundary": "MoonMind.UserWorkflow.v2",
        },
        "environmentDecisions": [
            {
                "environment": "production",
                "decision": "drain",
                "recordRef": "release://workflow-language-hard-switch/prod",
            }
        ],
        "releaseNotes": {
            "text": (
                "MoonMind no longer exposes Tasks as a product/runtime concept. "
                "No compatibility redirects or aliases are kept."
            ),
            "recordRef": "release://workflow-language-hard-switch/notes",
        },
        "compatibilityMode": "none",
    }


def test_hard_switch_cutover_record_parses_aliases_and_enums() -> None:
    record = HardSwitchCutoverRecord.model_validate(_valid_payload())

    assert record.release_name == "workflow-language-hard-switch"
    assert record.coordinated_release is True
    assert (
        record.affected_contracts.activity_payloads[0].category
        == CutoverContractCategory.ACTIVITY_PAYLOAD
    )
    assert (
        record.affected_contracts.activity_payloads[0].strategy
        == ContractCutoverStrategy.WORKER_TASK_QUEUE_SPLIT
    )
    assert (
        record.environment_decisions[0].decision
        == EnvironmentCutoverDecision.DRAIN
    )


def test_hard_switch_cutover_record_serializes_camel_case_boundary() -> None:
    record = HardSwitchCutoverRecord.model_validate(_valid_payload())

    payload = record.model_dump(by_alias=True)

    assert payload["releaseName"] == "workflow-language-hard-switch"
    assert "activityPayloads" in payload["affectedContracts"]
    assert payload["workerRouting"]["newStartsBoundary"] == "renamed_contract_worker"
    assert payload["environmentDecisions"][0]["recordRef"].startswith("release://")
