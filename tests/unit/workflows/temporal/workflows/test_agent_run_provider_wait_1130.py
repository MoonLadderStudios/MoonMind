"""MoonLadderStudios/MoonMind#1130: provider-wait workflow-boundary tests.

Covers the verifier's remaining work at the real AgentRun boundary:

- R3: ``_build_manager_slot_waiting_reason`` branches (missing, disabled,
  maintenance, cleanup, profile/scope cooldown, unknown) through the actual
  method with the canonical patch enabled.
- R4: ``_record_provider_wait_observation`` pipeline wiring (dedup, ordering,
  grant/cancel race, late previous-attempt results, current vs cumulative
  wait, completed-work protection).
- R6: ``_reuse_recorded_provider_wait`` reconnect/refresh reuse (no invented
  transitions, no retarget, no duplicated execution).
- R7: public-payload redaction (no lease/fence/credential/host handles, no
  other users' identities).
"""

import pytest

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.workflows import agent_run as agent_run_module
from moonmind.workflows.temporal.workflows.agent_run import (
    CANONICAL_WAITING_STATE_PATCH_ID,
    MoonMindAgentRun,
    structured_manager_slot_wait,
)


def _make_request(profile_ref: str | None = "p1") -> AgentExecutionRequest:
    kwargs: dict[str, object] = {
        "agentKind": "managed",
        "agentId": "codex_cli",
        "correlationId": "run-1",
        "idempotencyKey": "run-1:step-1",
    }
    if profile_ref is not None:
        kwargs["executionProfileRef"] = profile_ref
    return AgentExecutionRequest(**kwargs)  # type: ignore[arg-type]


def _enable_canonical(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_run_module.workflow,
        "patched",
        lambda patch_id: patch_id == CANONICAL_WAITING_STATE_PATCH_ID,
    )


def _base_manager_state(**overrides):
    state: dict[str, object] = {
        "running": True,
        "inspection_succeeded": True,
        "pending_requests_ordered": True,
        "requester_queue_position": 2,
        "event_count": 7,
        "requested_profile_missing": False,
        "requested_profile": {
            "profile_id": "p1",
            "enabled": True,
            "launch_ready": True,
            "cooldown_until": None,
            "maintenance_waiters": 0,
            "maintenance_waiter_position": None,
            "requester_cleanup_requested": False,
            "requester_unresolved_release": False,
            "profile_cleanup_pending_count": 0,
            "profile_unresolved_release_count": 0,
            "scope_known": False,
            "capacity_scope": None,
        },
    }
    state.update(overrides)
    return state


# R3: mapping branches at the real boundary.


def test_manager_slot_wait_missing_profile_is_validation(monkeypatch) -> None:
    _enable_canonical(monkeypatch)
    wf = MoonMindAgentRun()
    state = _base_manager_state(requested_profile_missing=True)
    reason = wf._build_manager_slot_waiting_reason(
        runtime_id="codex_cli", request=_make_request(), manager_state=state
    )
    assert reason.startswith("awaiting_provider_validation")


def test_manager_slot_wait_disabled_profile_is_validation(monkeypatch) -> None:
    _enable_canonical(monkeypatch)
    wf = MoonMindAgentRun()
    profile = dict(_base_manager_state()["requested_profile"])
    profile["enabled"] = False
    state = _base_manager_state(requested_profile=profile)
    reason = wf._build_manager_slot_waiting_reason(
        runtime_id="codex_cli", request=_make_request(), manager_state=state
    )
    assert reason.startswith("awaiting_provider_validation")


def test_manager_slot_wait_maintenance_position(monkeypatch) -> None:
    _enable_canonical(monkeypatch)
    wf = MoonMindAgentRun()
    profile = dict(_base_manager_state()["requested_profile"])
    profile["maintenance_waiter_position"] = 1
    state = _base_manager_state(requested_profile=profile)
    reason = wf._build_manager_slot_waiting_reason(
        runtime_id="codex_cli", request=_make_request(), manager_state=state
    )
    assert reason.startswith("awaiting_profile_maintenance")


def test_manager_slot_wait_cleanup_pending(monkeypatch) -> None:
    _enable_canonical(monkeypatch)
    wf = MoonMindAgentRun()
    profile = dict(_base_manager_state()["requested_profile"])
    profile["requester_cleanup_requested"] = True
    state = _base_manager_state(requested_profile=profile)
    reason = wf._build_manager_slot_waiting_reason(
        runtime_id="codex_cli", request=_make_request(), manager_state=state
    )
    assert reason.startswith("cleanup_pending")


def test_manager_slot_wait_profile_cooldown(monkeypatch) -> None:
    _enable_canonical(monkeypatch)
    wf = MoonMindAgentRun()
    profile = dict(_base_manager_state()["requested_profile"])
    profile["cooldown_until"] = "2026-09-14T08:00:00+00:00"
    state = _base_manager_state(requested_profile=profile)
    reason = wf._build_manager_slot_waiting_reason(
        runtime_id="codex_cli", request=_make_request(), manager_state=state
    )
    assert reason.startswith("provider_cooldown")


def test_manager_slot_wait_scope_cooldown(monkeypatch) -> None:
    _enable_canonical(monkeypatch)
    wf = MoonMindAgentRun()
    profile = dict(_base_manager_state()["requested_profile"])
    profile["scope_known"] = True
    profile["capacity_scope"] = {"cooldown_until": "2026-09-14T09:00:00+00:00"}
    state = _base_manager_state(requested_profile=profile)
    reason = wf._build_manager_slot_waiting_reason(
        runtime_id="codex_cli", request=_make_request(), manager_state=state
    )
    assert reason.startswith("provider_cooldown")


def test_manager_slot_wait_unknown_is_capacity_without_fabrication(
    monkeypatch,
) -> None:
    _enable_canonical(monkeypatch)
    wf = MoonMindAgentRun()
    reason = wf._build_manager_slot_waiting_reason(
        runtime_id="codex_cli",
        request=_make_request(),
        manager_state=_base_manager_state(),
    )
    assert reason.startswith("awaiting_provider_capacity")
    assert "lease" not in reason and "credential" not in reason


def test_structured_manager_slot_wait_projects_only_safe_fields() -> None:
    profile = {
        "cooldown_until": "",
        "scope_known": True,
        "capacity_scope": {"cooldown_until": "2026-09-14T09:00:00+00:00"},
    }
    out = structured_manager_slot_wait(
        {
            "pending_requests_ordered": True,
            "requester_queue_position": 3,
            "event_count": 11,
            "requested_profile": profile,
        }
    )
    assert out["cooldown_until"] == "2026-09-14T09:00:00+00:00"
    assert out["queue_position"] == 3
    assert out["revision"] == 11


def test_structured_manager_slot_wait_unordered_hides_position() -> None:
    out = structured_manager_slot_wait(
        {
            "pending_requests_ordered": False,
            "requester_queue_position": 3,
            "event_count": 4,
            "requested_profile": {"cooldown_until": None},
        }
    )
    assert out["queue_position"] is None
    assert out["revision"] == 4


# R4: pipeline wiring through the workflow instance owner.


def _record_kwargs(**overrides):
    kwargs: dict[str, object] = {
        "runtime_id": "codex_cli",
        "requester_workflow_id": "agent-run-1",
        "profile_ref": "p1",
        "reason": "awaiting_provider_capacity",
        "revision": 7,
    }
    kwargs.update(overrides)
    return kwargs


def test_record_dedupes_identical_polls_and_orders_revisions() -> None:
    wf = MoonMindAgentRun()
    entered = wf._record_provider_wait_observation(**_record_kwargs())
    assert entered is not None and entered["transition"] == "wait_entered"
    assert wf._provider_wait_entered_at is not None
    # Identical poll: no new event.
    assert wf._record_provider_wait_observation(**_record_kwargs()) is None
    # Reason change advances.
    changed = wf._record_provider_wait_observation(
        **_record_kwargs(reason="provider_cooldown", revision=8)
    )
    assert changed is not None and changed["transition"] == "reason_changed"
    # Stale revision never reorders.
    assert (
        wf._record_provider_wait_observation(
            **_record_kwargs(reason="awaiting_provider_capacity", revision=7)
        )
        is None
    )


def test_record_rejects_retarget_and_protects_completed() -> None:
    wf = MoonMindAgentRun()
    entered = wf._record_provider_wait_observation(**_record_kwargs())
    assert entered is not None
    # Cross-attempt observation for another profile cannot retarget display.
    assert (
        wf._record_provider_wait_observation(
            **_record_kwargs(profile_ref="other", revision=8)
        )
        is None
    )
    # Grant closes the wait and separates cumulative from current age.
    granted = wf._record_provider_wait_observation(
        **_record_kwargs(
            reason="provider_cooldown",
            revision=9,
            granted=True,
            now_iso="2026-09-14T07:10:00+00:00",
        )
    )
    assert granted is not None and granted["transition"] == "grant_resume"
    assert wf._provider_wait_entered_at is None
    # Late observations cannot reopen completed work.
    assert (
        wf._record_provider_wait_observation(
            **_record_kwargs(reason="awaiting_provider_capacity", revision=10)
        )
        is None
    )


def test_record_cancellation_closes_wait() -> None:
    wf = MoonMindAgentRun()
    assert wf._record_provider_wait_observation(**_record_kwargs()) is not None
    canceled = wf._record_provider_wait_observation(
        **_record_kwargs(revision=8, canceled=True)
    )
    assert canceled is not None and canceled["transition"] == "cancellation"
    assert (
        wf._record_provider_wait_observation(**_record_kwargs(revision=9)) is None
    )


# R6: reuse without inventing history.


def test_reuse_returns_recorded_observation_without_new_transition() -> None:
    wf = MoonMindAgentRun()
    assert wf._reuse_recorded_provider_wait() is None
    entered = wf._record_provider_wait_observation(**_record_kwargs())
    assert entered is not None
    # Reconnect/refresh reuses the same recorded observation.
    first = wf._reuse_recorded_provider_wait()
    second = wf._reuse_recorded_provider_wait()
    assert first == entered and second == entered
    assert first is not entered  # callers get a copy
    # Reuse never invents a past transition or duplicates execution.
    assert wf._provider_wait_state == entered


# R7: redaction at the public boundary.


def test_canonical_reason_never_carries_handles() -> None:
    from moonmind.workflows.temporal.workflows.agent_run import (
        canonical_waiting_reason,
    )

    for hostile in (
        "mm:lease-abc:host-1",
        "credential:xyz",
        "fence:9",
        "someone-else-queue-entry",
        "",
    ):
        rendered = canonical_waiting_reason(hostile, queue_position=2)
        assert "lease" not in rendered
        assert "credential" not in rendered
        assert "fence" not in rendered
        assert "someone-else" not in rendered
        assert rendered.startswith("awaiting_provider_capacity")


@pytest.mark.asyncio
async def test_compact_inspection_hides_other_identities_and_secrets(monkeypatch) -> None:
    from types import SimpleNamespace

    from moonmind.workflows.temporal.artifacts import TemporalArtifactActivities

    class _Handle:
        async def describe(self):
            return SimpleNamespace(status=SimpleNamespace(name="RUNNING"))

        async def query(self, query_name):
            assert query_name == "get_state"
            return {
                "profiles": {
                    "p1": {
                        "profile_id": "p1",
                        "current_leases": ["agent-run-1"],
                        "lease_metadata": {
                            "agent-run-1": {
                                "credential": "sekret",
                                "fencingGeneration": 2,
                            }
                        },
                        "enabled": True,
                    }
                },
                "pending_requests": [
                    {
                        "requester_workflow_id": "agent-run-1",
                        "execution_profile_ref": "p1",
                    },
                    {
                        "requester_workflow_id": "someone-else",
                        "execution_profile_ref": "p1",
                    },
                ],
                "pending_requests_ordered": True,
                "event_count": 3,
            }

    class _Client:
        def get_workflow_handle(self, workflow_id):
            return _Handle()

    class _Adapter:
        async def get_client(self):
            return _Client()

    monkeypatch.setattr(
        "moonmind.workflows.temporal.client.TemporalClientAdapter",
        _Adapter,
    )
    result = await TemporalArtifactActivities(
        object()
    ).provider_profile_manager_state(
        runtime_id="codex_cli",
        requester_workflow_id="agent-run-1",
        execution_profile_ref="p1",
    )
    serialized = str(result)
    assert "someone-else" not in serialized
    assert "sekret" not in serialized
    assert "lease_metadata" not in serialized
    assert "credential" not in serialized.lower()
