from __future__ import annotations

import pytest

from moonmind.config.settings import settings
from moonmind.workflows.tasks.routing import (
    TemporalSubmitDisabledError,
    get_routing_target_for_task,
)

# --- T004: Always returns "temporal" when submit_enabled=True ---

@pytest.mark.parametrize(
    ("is_manifest", "is_run"),
    [
        (True, False),
        (False, True),
        (False, False),
        (True, True),
    ],
    ids=["manifest", "run", "default", "both"],
)
def test_routing_always_returns_temporal(
    monkeypatch: pytest.MonkeyPatch,
    is_manifest: bool,
    is_run: bool,
) -> None:
    """All task types route to Temporal when submit is enabled."""
    monkeypatch.setattr(
        settings.temporal_dashboard,
        "submit_enabled",
        True,
        raising=False,
    )
    assert (
        get_routing_target_for_task(is_manifest=is_manifest, is_run=is_run)
        == "temporal"
    )

def test_routing_ignores_task_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """task_payload is accepted for API stability but has no effect."""
    monkeypatch.setattr(
        settings.temporal_dashboard, "submit_enabled", True, raising=False
    )
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
            task_payload={"task": {"proposeTasks": False}},
        )
        == "temporal"
    )
    assert (
        get_routing_target_for_task(
            is_run=True,
            task_payload=None,
        )
        == "temporal"
    )

# --- T005: Raises TemporalSubmitDisabledError when submit_enabled=False ---

def test_routing_raises_when_submit_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """submit_enabled=False must fail fast, not fall back to queue."""
    monkeypatch.setattr(
        settings.temporal_dashboard,
        "submit_enabled",
        False,
        raising=False,
    )
    with pytest.raises(TemporalSubmitDisabledError, match="legacy queue.*no longer supported"):
        get_routing_target_for_task(is_manifest=True)

def test_routing_raises_for_run_when_submit_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run submissions also fail fast when submit disabled."""
    monkeypatch.setattr(
        settings.temporal_dashboard,
        "submit_enabled",
        False,
        raising=False,
    )
    with pytest.raises(TemporalSubmitDisabledError, match="legacy queue.*no longer supported"):
        get_routing_target_for_task(is_run=True)

def test_routing_raises_for_default_when_submit_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even default routing (no manifest/run flags) fails fast."""
    monkeypatch.setattr(
        settings.temporal_dashboard,
        "submit_enabled",
        False,
        raising=False,
    )
    with pytest.raises(TemporalSubmitDisabledError, match="legacy queue.*no longer supported"):
        get_routing_target_for_task()

