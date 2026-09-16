from __future__ import annotations

import pytest

from moonmind.config.settings import settings
from moonmind.workflows.executions.routing import (
    TemporalSubmitDisabledError,
    get_routing_target_for_workflow,
)

# --- T004: Always returns "temporal" when submit_enabled=True ---

# MoonLadderStudios/MoonMind#4190: the Manifest product is retired, so the
# routing helper no longer accepts a Manifest-only flag. Routing is uniform.
def test_routing_signature_has_no_manifest_flag() -> None:
    """Retirement regression: no Manifest-only routing parameter remains."""
    import inspect

    params = inspect.signature(get_routing_target_for_workflow).parameters
    assert "is_manifest" not in params
    assert "manifest" not in params


@pytest.mark.parametrize(
    "is_run",
    [True, False],
    ids=["run", "default"],
)
def test_routing_always_returns_temporal(
    monkeypatch: pytest.MonkeyPatch,
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
        get_routing_target_for_workflow(is_run=is_run)
        == "temporal"
    )

def test_routing_ignores_task_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """task_payload is accepted for API stability but has no effect."""
    monkeypatch.setattr(
        settings.temporal_dashboard, "submit_enabled", True, raising=False
    )
    assert (
        get_routing_target_for_workflow(
            is_run=True,
            task_payload={"task": {"priority": 3}},
        )
        == "temporal"
    )
    assert (
        get_routing_target_for_workflow(
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
        get_routing_target_for_workflow(is_run=True)

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
        get_routing_target_for_workflow(is_run=True)

def test_routing_raises_for_default_when_submit_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even default routing (no flags) fails fast."""
    monkeypatch.setattr(
        settings.temporal_dashboard,
        "submit_enabled",
        False,
        raising=False,
    )
    with pytest.raises(TemporalSubmitDisabledError, match="legacy queue.*no longer supported"):
        get_routing_target_for_workflow()
