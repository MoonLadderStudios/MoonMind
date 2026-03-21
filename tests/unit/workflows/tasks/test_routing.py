from __future__ import annotations

import pytest

from moonmind.config.settings import settings
from moonmind.workflows.tasks.routing import get_routing_target_for_task


@pytest.mark.parametrize(
    ("submit_enabled", "expected"),
    [
        (False, "queue"),
        (True, "temporal"),
    ],
)
def test_manifest_routing_follows_submit_flag(
    monkeypatch: pytest.MonkeyPatch,
    submit_enabled: bool,
    expected: str,
) -> None:
    monkeypatch.setattr(
        settings.temporal_dashboard,
        "submit_enabled",
        submit_enabled,
        raising=False,
    )

    assert get_routing_target_for_task(is_manifest=True) == expected


def test_run_routing_uses_temporal_when_proposals_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.temporal_dashboard, "submit_enabled", True, raising=False)
    monkeypatch.setattr(settings.workflow, "enable_task_proposals", True, raising=False)

    assert (
        get_routing_target_for_task(
            is_run=True,
            task_payload={"task": {"proposeTasks": True}},
        )
        == "temporal"
    )
    assert (
        get_routing_target_for_task(
            is_run=True,
            task_payload={"task": {"proposeTasks": "yes"}},
        )
        == "temporal"
    )


def test_run_routing_prefers_temporal_when_proposals_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.temporal_dashboard, "submit_enabled", True, raising=False)
    monkeypatch.setattr(settings.workflow, "enable_task_proposals", True, raising=False)

    assert (
        get_routing_target_for_task(
            is_run=True,
            task_payload={"task": {"proposeTasks": False}},
        )
        == "temporal"
    )
    assert (
        get_routing_target_for_task(
            is_run=True,
            task_payload={"task": {"proposeTasks": "off"}},
        )
        == "temporal"
    )


def test_run_routing_uses_default_proposal_flag_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.temporal_dashboard, "submit_enabled", True, raising=False)
    monkeypatch.setattr(settings.workflow, "enable_task_proposals", True, raising=False)
    assert get_routing_target_for_task(is_run=True, task_payload={"task": {}}) == "temporal"

    monkeypatch.setattr(settings.workflow, "enable_task_proposals", False, raising=False)
    assert (
        get_routing_target_for_task(
            is_run=True,
            task_payload={"task": {}},
        )
        == "temporal"
    )
