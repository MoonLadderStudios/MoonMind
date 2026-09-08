"""Canonical user-workflow start-contract tests (MM-730 hard switch complete).

MoonLadderStudios/MoonMind#3951: startup resolves the ``MoonMind.UserWorkflow``
type and v2 Task Queue from queue settings alone. No cutover record or
release-note prose is consulted; stale cutover environment fails fast at
settings validation. The workflow fleet keeps polling the legacy replay queue
until retained histories drain, and ``moonmind.statuses.compat`` remains the
historical-read loader for ``no_changes``/``NO_CHANGES`` outcomes.
"""

from __future__ import annotations

import pytest

from moonmind.config.settings import TemporalSettings, settings
from moonmind.workflows.temporal.activity_catalog import (
    WORKFLOW_FLEET,
    WORKFLOW_TASK_QUEUE,
    build_default_activity_catalog,
    get_workflow_poll_task_queues,
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


def _renamed_contract_settings():
    return settings.temporal.model_copy(
        update={
            "user_workflow_contract_mode": "renamed_contract",
            "workflow_task_queue": "mm.workflow",
            "user_workflow_v2_task_queue": "mm.workflow.user.v2",
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


def test_renamed_contract_resolves_without_cutover_files(tmp_path) -> None:
    """Startup must not depend on migration prose or sample evidence."""

    temporal_settings = _renamed_contract_settings()
    missing_record = tmp_path / "absent-cutover-record.json"
    missing_notes = tmp_path / "absent-release-notes.md"
    assert not missing_record.exists()
    assert not missing_notes.exists()

    contract = resolve_user_workflow_start_contract(temporal_settings)

    assert contract.workflow_type == RENAMED_USER_WORKFLOW_TYPE
    assert contract.task_queue == "mm.workflow.user.v2"
    assert contract.contract_mode == "renamed_contract"


def test_renamed_contract_requires_distinct_queue() -> None:
    temporal_settings = _renamed_contract_settings().model_copy(
        update={"user_workflow_v2_task_queue": "mm.workflow"}
    )

    with pytest.raises(HardSwitchCutoverError, match="distinct"):
        resolve_user_workflow_start_contract(temporal_settings)


def test_stale_cutover_environment_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    """Removed cutover paths must error instead of being silently ignored."""

    monkeypatch.setenv(
        "TEMPORAL_USER_WORKFLOW_CUTOVER_RECORD_PATH",
        "docs/ReleaseNotes/MM-730-hard-switch-cutover.json",
    )
    with pytest.raises(ValueError, match="CUTOVER_RECORD_PATH.*obsolete"):
        TemporalSettings()


def test_stale_release_notes_environment_fails_fast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "TEMPORAL_USER_WORKFLOW_RELEASE_NOTES_PATH",
        "docs/ReleaseNotes/MM-730-hard-switch-cutover.md",
    )
    with pytest.raises(ValueError, match="RELEASE_NOTES_PATH.*obsolete"):
        TemporalSettings()


def test_renamed_contract_routes_new_starts_to_distinct_queue() -> None:
    temporal_settings = _renamed_contract_settings()

    contract = resolve_user_workflow_start_contract(temporal_settings)

    assert contract.workflow_type == RENAMED_USER_WORKFLOW_TYPE
    assert contract.task_queue == "mm.workflow.user.v2"
    assert contract.contract_mode == "renamed_contract"


def test_workflow_task_queue_constant_stays_replay_stable_while_start_queue_is_lazy() -> (
    None
):
    temporal_settings = _renamed_contract_settings()

    assert WORKFLOW_TASK_QUEUE == "mm.workflow"
    assert get_workflow_task_queue(temporal_settings) == "mm.workflow.user.v2"
    assert get_workflow_poll_task_queues(temporal_settings) == (
        "mm.workflow.user.v2",
        "mm.workflow",
    )


def test_workflow_poll_task_queues_ignore_missing_replay_queue() -> None:
    temporal_settings = _renamed_contract_settings().model_copy(
        update={"workflow_task_queue": None}
    )

    assert get_workflow_poll_task_queues(temporal_settings) == ("mm.workflow.user.v2",)


def test_merge_automation_workflow_fleet_does_not_poll_user_replay_queue() -> None:
    temporal_settings = _renamed_contract_settings().model_copy(
        update={
            "user_workflow_v2_task_queue": "mm.workflow.merge_automation",
            "merge_automation_workflow_task_queue": "mm.workflow.merge_automation",
        }
    )

    assert get_workflow_poll_task_queues(temporal_settings) == (
        "mm.workflow.merge_automation",
    )


def test_worker_registration_serves_only_one_user_workflow_type() -> None:
    renamed_settings = _renamed_contract_settings()

    renamed_types = list_registered_workflow_types_for_settings(renamed_settings)

    assert RENAMED_USER_WORKFLOW_TYPE in renamed_types
    assert LEGACY_USER_WORKFLOW_TYPE not in renamed_types


def test_renamed_contract_workflow_fleet_polls_start_and_replay_queues() -> None:
    temporal_settings = _renamed_contract_settings()
    catalog = build_default_activity_catalog(temporal_settings)
    topology = describe_configured_worker(
        temporal_settings=temporal_settings.model_copy(
            update={"worker_fleet": WORKFLOW_FLEET}
        ),
        catalog=catalog,
    )

    assert topology.task_queues == ("mm.workflow.user.v2", "mm.workflow")
    assert (
        catalog.resolve_activity("integration.resolve_adapter_metadata").task_queue
        == "mm.workflow.user.v2"
    )


def test_client_routes_renamed_user_workflow_to_v2_queue(monkeypatch) -> None:
    temporal_settings = _renamed_contract_settings()
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
