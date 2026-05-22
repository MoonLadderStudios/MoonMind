from __future__ import annotations

import pytest

from moonmind.schemas.hard_switch_cutover_models import HardSwitchCutoverRecord
from moonmind.services.hard_switch_cutover import validate_hard_switch_cutover

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


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
            },
            {
                "environment": "staging",
                "decision": "terminate_restart",
                "recordRef": "release://workflow-language-hard-switch/staging",
            },
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


def test_complete_mm730_cutover_record_passes_release_gate() -> None:
    record = HardSwitchCutoverRecord.model_validate(_valid_payload())

    result = validate_hard_switch_cutover(record)

    assert result.ready is True
    assert result.findings == []


def test_incomplete_mm730_cutover_record_reports_all_release_blockers() -> None:
    payload = _valid_payload()
    payload["coordinatedRelease"] = False
    payload["affectedContracts"] = {
        "workflows": [],
        "activityPayloads": [],
        "signals": [],
        "updates": [],
    }
    payload["workerRouting"] = {
        "previousWorkerBuild": "same-build",
        "previousTaskQueue": "mm.workflow",
        "newWorkerBuild": "same-build",
        "newTaskQueue": "mm.workflow",
        "newStartsBoundary": "same_worker",
        "singleBuildServesBothShapes": True,
        "versionBoundary": "",
    }
    payload["environmentDecisions"] = [
        {"environment": "production", "decision": "drain", "recordRef": ""}
    ]
    payload["releaseNotes"] = {
        "text": "Workflow Execution release.",
        "recordRef": "",
    }
    payload["compatibilityMode"] = "alias"

    result = validate_hard_switch_cutover(
        HardSwitchCutoverRecord.model_validate(payload)
    )
    codes = {finding.code for finding in result.findings}

    assert result.ready is False
    assert {
        "CUTOVER_MISSING_WORKFLOW_STRATEGY",
        "CUTOVER_MISSING_ACTIVITY_PAYLOAD_STRATEGY",
        "CUTOVER_MISSING_SIGNAL_STRATEGY",
        "CUTOVER_MISSING_UPDATE_STRATEGY",
        "CUTOVER_WORKER_SINGLE_BUILD_BOTH_SHAPES",
        "CUTOVER_NEW_STARTS_BOUNDARY_MISSING",
        "CUTOVER_ENVIRONMENT_DECISION_RECORD_MISSING",
        "CUTOVER_RELEASE_NOTES_TASK_REMOVAL_MISSING",
        "CUTOVER_RELEASE_NOTES_NO_ALIAS_MISSING",
        "CUTOVER_RELEASE_NOTES_RECORD_MISSING",
        "CUTOVER_HIDDEN_COMPATIBILITY_LAYER",
        "CUTOVER_NOT_COORDINATED_RELEASE",
    }.issubset(codes)
