from __future__ import annotations

import json
from pathlib import Path

import pytest

from moonmind.config.settings import settings
from moonmind.workflows.temporal import hard_switch_cutover
from moonmind.workflows.temporal.activity_catalog import (
    WORKFLOW_FLEET,
    WORKFLOW_TASK_QUEUE,
    build_default_activity_catalog,
    get_workflow_task_queue,
)
from moonmind.workflows.temporal.client import TemporalClientAdapter
from moonmind.workflows.temporal.hard_switch_cutover import (
    HardSwitchCutoverError,
    LEGACY_USER_WORKFLOW_TYPE,
    RENAMED_USER_WORKFLOW_TYPE,
    resolve_user_workflow_start_contract,
)
from moonmind.workflows.temporal.workers import (
    describe_configured_worker,
    list_registered_workflow_types_for_settings,
)
from moonmind.workflows.temporal.workflows.run import (
    MoonMindRunWorkflow,
    MoonMindUserWorkflow,
)


def _write_release_notes(tmp_path: Path) -> Path:
    path = tmp_path / "MM-730-release-notes.md"
    path.write_text(
        "MoonMind no longer exposes Tasks as a product/runtime concept. "
        "Use Workflow Execution, workflowId, runId, and Step Execution.\n\n"
        "Compatibility redirects and task-shaped aliases are not kept.\n",
        encoding="utf-8",
    )
    return path


def _write_cutover_record(tmp_path: Path, *, release_notes_path: Path) -> Path:
    path = tmp_path / "MM-730-cutover.json"
    path.write_text(
        json.dumps(
            {
                "jiraIssueKey": "MM-730",
                "releaseMode": "coordinated_branch_release",
                "legacyWorkflowType": "MoonMind.Run",
                "newWorkflowType": "MoonMind.UserWorkflow",
                "releaseNotesPath": str(release_notes_path),
                "environments": [
                    {
                        "name": "ci",
                        "decision": "drain",
                        "recordedAt": "2026-05-24T00:00:00Z",
                    }
                ],
                "affectedContracts": [
                    {
                        "kind": "workflow",
                        "owner": "MoonMind.UserWorkflow",
                        "strategy": "Use the renamed workflow type after cutover.",
                    },
                    {
                        "kind": "activity",
                        "owner": "MoonMind.UserWorkflow activities",
                        "strategy": "Use renamed activity payloads after cutover.",
                    },
                    {
                        "kind": "signal",
                        "owner": "MoonMind.UserWorkflow signals",
                        "strategy": "Use renamed signal shapes after cutover.",
                    },
                    {
                        "kind": "update",
                        "owner": "MoonMind.UserWorkflow updates",
                        "strategy": "Use renamed update shapes after cutover.",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _renamed_contract_settings(tmp_path: Path):
    release_notes_path = _write_release_notes(tmp_path)
    cutover_record_path = _write_cutover_record(
        tmp_path,
        release_notes_path=release_notes_path,
    )
    return settings.temporal.model_copy(
        update={
            "user_workflow_contract_mode": "renamed_contract",
            "workflow_task_queue": "mm.workflow",
            "user_workflow_v2_task_queue": "mm.workflow.user.v2",
            "user_workflow_cutover_record_path": str(cutover_record_path),
            "user_workflow_release_notes_path": str(release_notes_path),
        }
    )


def test_current_build_rejects_legacy_run_contract_mode() -> None:
    temporal_settings = settings.temporal.model_copy(
        update={
            "user_workflow_contract_mode": "legacy_run",
            "workflow_task_queue": "mm.workflow",
        }
    )

    with pytest.raises(HardSwitchCutoverError, match="renamed_contract"):
        resolve_user_workflow_start_contract(temporal_settings)


def test_renamed_contract_requires_cutover_record() -> None:
    temporal_settings = settings.temporal.model_copy(
        update={
            "user_workflow_contract_mode": "renamed_contract",
            "user_workflow_cutover_record_path": None,
            "user_workflow_release_notes_path": None,
        }
    )

    with pytest.raises(HardSwitchCutoverError, match="CUTOVER_RECORD_PATH"):
        resolve_user_workflow_start_contract(temporal_settings)


def test_renamed_contract_routes_new_starts_to_distinct_queue(tmp_path: Path) -> None:
    temporal_settings = _renamed_contract_settings(tmp_path)

    contract = resolve_user_workflow_start_contract(temporal_settings)

    assert contract.workflow_type == RENAMED_USER_WORKFLOW_TYPE
    assert contract.task_queue == "mm.workflow.user.v2"
    assert contract.contract_mode == "renamed_contract"


def test_workflow_task_queue_constant_stays_replay_stable_while_start_queue_is_lazy(
    tmp_path: Path,
) -> None:
    temporal_settings = _renamed_contract_settings(tmp_path)

    assert WORKFLOW_TASK_QUEUE == "mm.workflow"
    assert get_workflow_task_queue(temporal_settings) == "mm.workflow.user.v2"


def test_renamed_contract_resolution_caches_cutover_file_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    temporal_settings = _renamed_contract_settings(tmp_path)
    hard_switch_cutover._resolve_renamed_user_workflow_start_contract.cache_clear()
    load_count = 0
    original_load_json_mapping = hard_switch_cutover._load_json_mapping

    def counting_load_json_mapping(path: Path):
        nonlocal load_count
        load_count += 1
        return original_load_json_mapping(path)

    monkeypatch.setattr(
        hard_switch_cutover,
        "_load_json_mapping",
        counting_load_json_mapping,
    )

    first = resolve_user_workflow_start_contract(temporal_settings)
    second = resolve_user_workflow_start_contract(temporal_settings)

    assert first == second
    assert load_count == 1


def test_worker_registration_serves_only_one_user_workflow_type(tmp_path: Path) -> None:
    renamed_settings = _renamed_contract_settings(tmp_path)

    renamed_types = list_registered_workflow_types_for_settings(renamed_settings)

    assert RENAMED_USER_WORKFLOW_TYPE in renamed_types
    assert LEGACY_USER_WORKFLOW_TYPE not in renamed_types


def test_renamed_contract_workflow_fleet_polls_start_queue(tmp_path: Path) -> None:
    temporal_settings = _renamed_contract_settings(tmp_path)
    catalog = build_default_activity_catalog(temporal_settings)
    topology = describe_configured_worker(
        temporal_settings=temporal_settings.model_copy(
            update={"worker_fleet": WORKFLOW_FLEET}
        ),
        catalog=catalog,
    )

    assert topology.task_queues == ("mm.workflow.user.v2",)
    assert (
        catalog.resolve_activity("integration.resolve_adapter_metadata").task_queue
        == "mm.workflow.user.v2"
    )


def test_client_routes_renamed_user_workflow_to_v2_queue(tmp_path: Path, monkeypatch) -> None:
    temporal_settings = _renamed_contract_settings(tmp_path)
    monkeypatch.setattr(settings, "temporal", temporal_settings)

    adapter = TemporalClientAdapter()

    assert adapter._get_task_queue(RENAMED_USER_WORKFLOW_TYPE) == "mm.workflow.user.v2"


def test_renamed_user_workflow_accepts_legacy_dependency_snapshots() -> None:
    assert MoonMindRunWorkflow()._supported_dependency_workflow_types() == frozenset(
        {RENAMED_USER_WORKFLOW_TYPE}
    )
    assert MoonMindUserWorkflow()._supported_dependency_workflow_types() == frozenset(
        {RENAMED_USER_WORKFLOW_TYPE}
    )
