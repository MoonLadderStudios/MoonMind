from __future__ import annotations

from moonmind.schemas.hard_switch_cutover_models import HardSwitchCutoverRecord
from moonmind.services.hard_switch_cutover import validate_hard_switch_cutover


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
                "decision": "pause_resume",
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


def _record(payload: dict[str, object] | None = None) -> HardSwitchCutoverRecord:
    return HardSwitchCutoverRecord.model_validate(payload or _valid_payload())


def _codes(payload: dict[str, object]) -> list[str]:
    result = validate_hard_switch_cutover(_record(payload))
    return [finding.code for finding in result.findings]


def test_complete_cutover_record_is_ready() -> None:
    result = validate_hard_switch_cutover(_record())

    assert result.ready is True
    assert result.findings == []


def test_missing_contract_category_reports_deterministic_findings() -> None:
    payload = _valid_payload()
    payload["affectedContracts"] = {
        "workflows": [],
        "activityPayloads": [],
        "signals": [],
        "updates": [],
    }

    result = validate_hard_switch_cutover(_record(payload))

    assert result.ready is False
    assert [finding.code for finding in result.findings] == [
        "CUTOVER_MISSING_ACTIVITY_PAYLOAD_STRATEGY",
        "CUTOVER_MISSING_SIGNAL_STRATEGY",
        "CUTOVER_MISSING_UPDATE_STRATEGY",
        "CUTOVER_MISSING_WORKFLOW_STRATEGY",
    ]
    assert {finding.source for finding in result.findings} == {"DESIGN-REQ-019"}


def test_single_worker_build_serving_both_shapes_requires_version_boundary() -> None:
    payload = _valid_payload()
    payload["workerRouting"] = {
        "previousWorkerBuild": "same-build",
        "previousTaskQueue": "mm.workflow",
        "newWorkerBuild": "same-build",
        "newTaskQueue": "mm.workflow",
        "newStartsBoundary": "renamed_contract_worker",
        "singleBuildServesBothShapes": True,
        "versionBoundary": "",
    }

    assert "CUTOVER_WORKER_SINGLE_BUILD_BOTH_SHAPES" in _codes(payload)


def test_new_starts_must_have_post_cutover_boundary() -> None:
    payload = _valid_payload()
    payload["workerRouting"] = {
        **payload["workerRouting"],  # type: ignore[index]
        "newStartsBoundary": "same_worker",
    }

    assert "CUTOVER_NEW_STARTS_BOUNDARY_MISSING" in _codes(payload)


def test_environment_decisions_and_coordinated_release_are_required() -> None:
    payload = _valid_payload()
    payload["coordinatedRelease"] = False
    payload["environmentDecisions"] = [
        {"environment": "production", "decision": "drain", "recordRef": ""}
    ]

    codes = _codes(payload)

    assert "CUTOVER_NOT_COORDINATED_RELEASE" in codes
    assert "CUTOVER_ENVIRONMENT_DECISION_RECORD_MISSING" in codes


def test_release_notes_must_state_task_removal_and_no_aliases() -> None:
    payload = _valid_payload()
    payload["releaseNotes"] = {
        "text": "Workflow Execution is the new product entity.",
        "recordRef": "release://workflow-language-hard-switch/notes",
    }

    codes = _codes(payload)

    assert "CUTOVER_RELEASE_NOTES_TASK_REMOVAL_MISSING" in codes
    assert "CUTOVER_RELEASE_NOTES_NO_ALIAS_MISSING" in codes


def test_hidden_translation_layers_fail_readiness() -> None:
    payload = _valid_payload()
    payload["compatibilityMode"] = "translation_layer"

    result = validate_hard_switch_cutover(_record(payload))

    assert result.ready is False
    assert [finding.code for finding in result.findings] == [
        "CUTOVER_HIDDEN_COMPATIBILITY_LAYER"
    ]
    assert result.findings[0].source == "DESIGN-REQ-022"
