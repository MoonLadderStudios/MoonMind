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
    manager_slot_wait_health,
    manager_slot_wait_health_suffix,
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
    assert out["queue_ordered"] is True
    assert out["queue_fresh"] is True
    assert out["next_check"] is None
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
    assert out["queue_ordered"] is False
    assert out["queue_fresh"] is False
    assert out["next_check"] is None
    assert out["revision"] == 4


# R3: glue boundary through the real _inspected_provider_slot_wait entrypoint.


def _mock_workflow_identity(monkeypatch, workflow_id: str = "agent-run-1") -> None:
    from types import SimpleNamespace

    monkeypatch.setattr(
        agent_run_module.workflow,
        "info",
        lambda: SimpleNamespace(workflow_id=workflow_id),
    )


@pytest.mark.asyncio
async def test_inspected_slot_wait_glue_maps_scope_cooldown(monkeypatch) -> None:
    """R3: inspection -> mapping -> structured through the real glue entrypoint."""
    _enable_canonical(monkeypatch)
    _mock_workflow_identity(monkeypatch)

    async def _fake_manager_state(self, **kwargs):
        profile = dict(_base_manager_state()["requested_profile"])
        profile["scope_known"] = True
        profile["capacity_scope"] = {
            "cooldown_until": "2026-09-14T09:00:00+00:00"
        }
        return _base_manager_state(requested_profile=profile)

    monkeypatch.setattr(
        MoonMindAgentRun,
        "_manager_state_for_slot_wait",
        _fake_manager_state,
    )
    wf = MoonMindAgentRun()
    observation = await wf._inspected_provider_slot_wait(
        manager_id="manager-1",
        runtime_id="codex_cli",
        request=_make_request(),
    )
    assert observation["reason"].startswith("provider_cooldown")
    assert observation["cooldown_until"] == "2026-09-14T09:00:00+00:00"
    assert observation["queue_position"] == 2
    assert observation["revision"] == 7
    assert observation["profile_ref"] == "p1"


@pytest.mark.asyncio
async def test_inspected_slot_wait_glue_missing_profile_never_substitutes(
    monkeypatch,
) -> None:
    """R3: explicit missing profile stays validation with unknown structure."""
    _enable_canonical(monkeypatch)
    _mock_workflow_identity(monkeypatch)

    async def _fake_manager_state(self, **kwargs):
        return _base_manager_state(requested_profile_missing=True)

    monkeypatch.setattr(
        MoonMindAgentRun,
        "_manager_state_for_slot_wait",
        _fake_manager_state,
    )
    wf = MoonMindAgentRun()
    observation = await wf._inspected_provider_slot_wait(
        manager_id="manager-1",
        runtime_id="codex_cli",
        request=_make_request(),
    )
    assert observation["reason"].startswith("awaiting_provider_validation")
    # Missing identity never borrows another account's deadline/position.
    assert observation["cooldown_until"] is None


@pytest.mark.asyncio
async def test_inspected_slot_wait_glue_inspection_failure_stays_unknown(
    monkeypatch,
) -> None:
    """R3: failed inspection falls back to generic reason with unknown fields."""
    _enable_canonical(monkeypatch)
    _mock_workflow_identity(monkeypatch)

    async def _boom(self, **kwargs):
        raise RuntimeError("manager unavailable")

    monkeypatch.setattr(
        MoonMindAgentRun, "_manager_state_for_slot_wait", _boom
    )
    wf = MoonMindAgentRun()
    observation = await wf._inspected_provider_slot_wait(
        manager_id="manager-1",
        runtime_id="codex_cli",
        request=_make_request(),
    )
    assert observation["reason"].startswith("awaiting_provider_capacity")
    assert observation["cooldown_until"] is None
    assert observation["queue_position"] is None
    assert observation["revision"] is None


def test_inspected_slot_wait_compatible_with_previous_compact_shape(
    monkeypatch,
) -> None:
    """R3: previous compact payloads (no scope/revision fields) still map."""
    _enable_canonical(monkeypatch)
    wf = MoonMindAgentRun()
    legacy_state = {
        "running": True,
        "inspection_succeeded": True,
        "pending_requests_ordered": True,
        "requester_queue_position": 1,
    }
    reason = wf._build_manager_slot_waiting_reason(
        runtime_id="codex_cli",
        request=_make_request(),
        manager_state=legacy_state,
    )
    assert reason.startswith("awaiting_provider_capacity")
    structured = structured_manager_slot_wait(legacy_state)
    assert structured == {
        "cooldown_until": None,
        "queue_position": 1,
        "queue_ordered": True,
        "queue_fresh": False,
        "next_check": None,
        "revision": None,
    }


# R4: pipeline-level parent-signal behavior.


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


def _patch_canonical_signal(monkeypatch) -> list[tuple[str, str]]:
    monkeypatch.setattr(
        MoonMindAgentRun,
        "_workflow_patch_enabled",
        lambda self, patch_id: True,
    )
    calls: list[tuple[str, str]] = []

    async def _fake_signal(self, parent_info, new_state, reason):
        calls.append((new_state, reason))

    monkeypatch.setattr(
        MoonMindAgentRun, "_signal_parent_child_state_changed", _fake_signal
    )
    return calls


@pytest.mark.asyncio
async def test_signal_pipeline_dedupes_deadline_extends_and_separates_age(
    monkeypatch,
) -> None:
    """R4: pipeline-level _signal_provider_slot_wait with a parent-signal mock."""
    calls = _patch_canonical_signal(monkeypatch)
    wf = MoonMindAgentRun()
    parent = object()
    base = {
        "runtime_id": "codex_cli",
        "requester_workflow_id": "agent-run-1",
        "profile_ref": "p1",
    }
    first = await wf._signal_provider_slot_wait(
        parent, **base, reason="awaiting_provider_capacity", revision=7
    )
    assert first is not None and first["transition"] == "wait_entered"
    assert len(calls) == 1
    entered_at = wf._provider_wait_entered_at
    assert entered_at is not None
    # Identical poll: no signal, no event.
    assert (
        await wf._signal_provider_slot_wait(
            parent, **base, reason="awaiting_provider_capacity", revision=7
        )
        is None
    )
    assert len(calls) == 1
    # Reason change signals again.
    changed = await wf._signal_provider_slot_wait(
        parent, **base, reason="provider_cooldown",
        cooldown_until="2026-09-14T08:00:00+00:00", revision=8,
    )
    assert changed is not None and changed["transition"] == "reason_changed"
    assert len(calls) == 2
    # Same reason, extended deadline signals as deadline-extended.
    extended = await wf._signal_provider_slot_wait(
        parent, **base, reason="provider_cooldown",
        cooldown_until="2026-09-14T09:00:00+00:00", revision=9,
    )
    assert extended is not None and extended["transition"] == "deadline_extended"
    assert len(calls) == 3
    # Stale revision sends nothing and never reorders.
    assert (
        await wf._signal_provider_slot_wait(
            parent, **base, reason="awaiting_provider_capacity", revision=7
        )
        is None
    )
    assert len(calls) == 3
    # Retarget to another profile sends nothing.
    assert (
        await wf._signal_provider_slot_wait(
            parent,
            runtime_id="codex_cli",
            requester_workflow_id="agent-run-1",
            profile_ref="other",
            reason="provider_cooldown",
            revision=10,
        )
        is None
    )
    assert len(calls) == 3
    # Grant closes the wait: current age separates from cumulative wait.
    before_cumulative = float(wf._provider_wait_cumulative_seconds)
    granted = wf._record_provider_wait_observation(
        **_record_kwargs(
            reason="provider_cooldown",
            cooldown_until="2026-09-14T09:00:00+00:00",
            revision=10,
            granted=True,
            now_iso="2026-09-14T07:10:00+00:00",
        )
    )
    assert granted is not None and granted["transition"] == "grant_resume"
    assert wf._provider_wait_entered_at is None
    assert float(wf._provider_wait_cumulative_seconds) >= before_cumulative
    # Late observations after completion never reopen or re-signal.
    assert (
        await wf._signal_provider_slot_wait(
            parent, **base, reason="awaiting_provider_capacity", revision=11
        )
        is None
    )
    assert len(calls) == 3


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


def test_provider_wait_survives_worker_restart_replay_and_continue_as_new() -> None:
    """R6: instance-state replay fixture without a second store.

    Simulates worker restart/reconnect/refresh and Continue-As-New by
    serializing the durable workflow instance fields through JSON (the same
    shape Temporal replays) into a fresh workflow instance, then proving the
    recorded observation is reused verbatim with no invented transitions.
    """
    import copy
    import json

    wf = MoonMindAgentRun()
    entered = wf._record_provider_wait_observation(
        **_record_kwargs(now_iso="2026-09-14T07:00:00+00:00")
    )
    assert entered is not None
    # Worker restart: durable fields survive a serialize/deserialize round-trip.
    snapshot = json.loads(
        json.dumps(
            {
                "state": wf._provider_wait_state,
                "entered_at": wf._provider_wait_entered_at,
                "cumulative_seconds": wf._provider_wait_cumulative_seconds,
            }
        )
    )
    restarted = MoonMindAgentRun()
    restarted._provider_wait_state = copy.deepcopy(snapshot["state"])
    restarted._provider_wait_entered_at = snapshot["entered_at"]
    restarted._provider_wait_cumulative_seconds = snapshot["cumulative_seconds"]
    assert restarted._reuse_recorded_provider_wait() == entered
    # Continue-As-New reuses recorded observations; a new snapshot cannot
    # reconstruct unobserved past transitions.
    continued = MoonMindAgentRun()
    continued._provider_wait_state = copy.deepcopy(snapshot["state"])
    continued._provider_wait_entered_at = snapshot["entered_at"]
    continued._provider_wait_cumulative_seconds = snapshot["cumulative_seconds"]
    assert continued._record_provider_wait_observation(**_record_kwargs()) is None
    assert continued._reuse_recorded_provider_wait() == entered
    # Retained-history reads return copies; mutating the copy cannot poison
    # the recorded observation or duplicate execution.
    leaked = continued._reuse_recorded_provider_wait()
    assert leaked is not None
    leaked["reason"] = "tampered"
    assert continued._provider_wait_state != leaked
    assert continued._reuse_recorded_provider_wait() == entered


def test_provider_wait_public_transition_matrix_denies_cross_owner() -> None:
    """R7: allowed/denied matrix at the progress boundary.

    The same workflow/profile identity may extend its wait; a different
    requester, a different profile, or any secret-bearing payload is denied
    (returns None) and the public transition carries only safe fields.
    """
    wf = MoonMindAgentRun()
    entered = wf._record_provider_wait_observation(**_record_kwargs())
    assert entered is not None
    allowed_keys = {
        "wait_id", "revision", "reason", "cooldown_until",
        "queue_position", "queue_ordered", "queue_fresh", "next_check",
        "transition", "completed",
    }
    assert set(entered.keys()) <= allowed_keys
    assert "lease" not in str(entered) and "credential" not in str(entered).lower()
    # Same owner, advanced revision: allowed.
    advanced = wf._record_provider_wait_observation(
        **_record_kwargs(reason="provider_cooldown", revision=8)
    )
    assert advanced is not None
    # Cross-owner requester: denied.
    assert (
        wf._record_provider_wait_observation(
            **_record_kwargs(
                requester_workflow_id="someone-else", revision=9
            )
        )
        is None
    )
    # Cross-profile observation: denied, cannot retarget display.
    assert (
        wf._record_provider_wait_observation(
            **_record_kwargs(profile_ref="p2", revision=9)
        )
        is None
    )
    # Cached reuse after revocation stays a copy: mutating it cannot widen
    # authority or reveal other identities.
    cached = wf._reuse_recorded_provider_wait()
    assert cached is not None
    assert set(cached.keys()) <= allowed_keys
    assert "someone-else" not in str(cached)


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


def test_canonical_reason_carries_authoritative_cooldown_fragment() -> None:
    """R5: canonical cooldown observations reach the parent signal string."""
    from moonmind.workflows.temporal.workflows.agent_run import (
        canonical_waiting_reason,
    )

    rendered = canonical_waiting_reason(
        "provider_cooldown",
        queue_position=2,
        cooldown_until="2026-09-14T08:00:00+00:00",
    )
    assert rendered.startswith("provider_cooldown")
    assert "queue_position=2" in rendered
    assert "cooldown_until=2026-09-14T08:00:00+00:00" in rendered
    # Missing deadline stays missing: never invented, never "immediate".
    assert "cooldown_until" not in canonical_waiting_reason(
        "awaiting_provider_capacity", queue_position=2
    )


def test_canonical_reason_rejects_malicious_cooldown_fragment() -> None:
    """R5/R7: hostile deadlines never widen the parent payload."""
    from moonmind.workflows.temporal.workflows.agent_run import (
        canonical_waiting_reason,
    )

    for hostile in (
        "2026-09-14T08:00:00+00:00; lease=abc",
        "soon, now",
        "in 5 minutes",
        "x" * 65,
        "",
    ):
        rendered = canonical_waiting_reason(
            "provider_cooldown", cooldown_until=hostile
        )
        assert "lease" not in rendered
        assert rendered == "provider_cooldown" or "cooldown_until=" not in rendered


def test_manager_slot_wait_canonical_carries_scope_cooldown_fragment(
    monkeypatch,
) -> None:
    """R5: scope-cooldown mapping embeds the deadline in the signal string."""
    _enable_canonical(monkeypatch)
    wf = MoonMindAgentRun()
    profile = dict(_base_manager_state()["requested_profile"])
    profile["scope_known"] = True
    profile["capacity_scope"] = {"cooldown_until": "2026-09-14T09:00:00+00:00"}
    state = _base_manager_state(requested_profile=profile)
    reason = wf._build_manager_slot_waiting_reason(
        runtime_id="codex_cli",
        request=_make_request(),
        manager_state=state,
    )
    assert reason.startswith("provider_cooldown")
    assert "cooldown_until=2026-09-14T09:00:00+00:00" in reason


@pytest.mark.asyncio
async def test_signal_forwards_structured_fragments_to_parent(
    monkeypatch,
) -> None:
    """R5: structured cooldown/queue reach the parent via the reason string.

    The two-arg child_state_changed signal is preserved for replay; the
    observed fields travel as safe fragments the Workflow Detail parsers
    already understand. No ETA is implied here.
    """
    calls = _patch_canonical_signal(monkeypatch)
    wf = MoonMindAgentRun()
    parent = object()
    transition = await wf._signal_provider_slot_wait(
        parent,
        runtime_id="codex_cli",
        requester_workflow_id="agent-run-1",
        profile_ref="p1",
        reason="provider_cooldown",
        cooldown_until="2026-09-14T08:00:00+00:00",
        queue_position=3,
        revision=7,
    )
    assert transition is not None and transition["transition"] == "wait_entered"
    assert len(calls) == 1
    new_state, parent_reason = calls[0]
    assert new_state == "awaiting_slot"
    assert "queue_position=3" in parent_reason
    assert "cooldown_until=2026-09-14T08:00:00+00:00" in parent_reason
    assert "ETA" not in parent_reason and "eta" not in parent_reason.lower()


def test_replay_compat_old_reason_without_fragments_stays_valid() -> None:
    """R6: histories recorded before the cooldown fragment still replay.

    Old canonical reasons carry no cooldown fragment; the record/reuse path
    treats missing structure as unknown rather than failing or fabricating.
    """
    wf = MoonMindAgentRun()
    entered = wf._record_provider_wait_observation(
        **_record_kwargs(reason="provider_cooldown", revision=None)
    )
    assert entered is not None and entered["transition"] == "wait_entered"
    assert entered["cooldown_until"] is None
    assert entered["queue_position"] is None
    assert wf._reuse_recorded_provider_wait() == entered


def test_fresh_instance_reuse_after_revocation_is_empty() -> None:
    """R7: a fresh instance (logged-out/revoked cache) exposes no wait."""
    wf = MoonMindAgentRun()
    assert wf._reuse_recorded_provider_wait() is None
    # Recording then discarding state (revocation) leaves nothing cached.
    assert wf._record_provider_wait_observation(**_record_kwargs()) is not None
    revoked = MoonMindAgentRun()
    assert revoked._reuse_recorded_provider_wait() is None


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


@pytest.mark.asyncio
async def test_wait_signal_hides_other_identities_and_secrets_end_to_end(
    monkeypatch,
) -> None:
    """R7: hostile manager state never leaks past the parent signal boundary.

    Exercises the real glue entrypoint plus the parent-signal pipeline with
    another user's queue entry, secret-bearing lease metadata, and an
    authoritative cooldown present: the parent reason carries the deadline
    and ordered position but no identities, handles, or secrets.
    """
    _enable_canonical(monkeypatch)
    _mock_workflow_identity(monkeypatch)
    calls = _patch_canonical_signal(monkeypatch)

    async def _fake_manager_state(self, **kwargs):
        profile = dict(_base_manager_state()["requested_profile"])
        profile["cooldown_until"] = "2026-09-14T08:00:00+00:00"
        profile["current_leases"] = ["agent-run-1", "someone-else"]
        profile["lease_metadata"] = {
            "agent-run-1": {"credential": "sekret", "fencingGeneration": 2},
            "someone-else": {"credential": "other-sekret"},
        }
        state = _base_manager_state(requested_profile=profile)
        state["pending_requests"] = [
            {"requester_workflow_id": "agent-run-1"},
            {"requester_workflow_id": "someone-else"},
        ]
        return state

    monkeypatch.setattr(
        MoonMindAgentRun,
        "_manager_state_for_slot_wait",
        _fake_manager_state,
    )
    wf = MoonMindAgentRun()
    observation = await wf._inspected_provider_slot_wait(
        manager_id="manager-1",
        runtime_id="codex_cli",
        request=_make_request(),
    )
    assert observation["cooldown_until"] == "2026-09-14T08:00:00+00:00"
    transition = await wf._signal_provider_slot_wait(
        object(),
        runtime_id="codex_cli",
        requester_workflow_id="agent-run-1",
        profile_ref=str(observation.get("profile_ref") or ""),
        reason=str(observation.get("reason") or ""),
        cooldown_until=observation.get("cooldown_until"),
        queue_position=observation.get("queue_position"),
        queue_ordered=observation.get("queue_ordered")
        if isinstance(observation.get("queue_ordered"), bool)
        else None,
        queue_fresh=observation.get("queue_fresh")
        if isinstance(observation.get("queue_fresh"), bool)
        else None,
        next_check=observation.get("next_check"),
        revision=observation.get("revision"),
    )
    assert transition is not None
    assert len(calls) == 1
    _, parent_reason = calls[0]
    assert "cooldown_until=2026-09-14T08:00:00+00:00" in parent_reason
    assert "someone-else" not in parent_reason
    assert "sekret" not in parent_reason
    assert "credential" not in parent_reason.lower()
    assert "lease" not in parent_reason.lower()
    assert "fence" not in parent_reason.lower()
    assert "host" not in parent_reason.lower()


def test_manager_slot_wait_unordered_queue_hides_position_in_reason(
    monkeypatch,
) -> None:
    """R5: queue position requires ordered current evidence.

    An unordered pending queue never lends its index as a display
    position in the canonical reason.
    """
    _enable_canonical(monkeypatch)
    wf = MoonMindAgentRun()
    state = _base_manager_state(
        pending_requests_ordered=False,
        requester_queue_position=2,
    )
    reason = wf._build_manager_slot_waiting_reason(
        runtime_id="codex_cli",
        request=_make_request(),
        manager_state=state,
    )
    assert "queue_position" not in reason
    ordered_state = _base_manager_state(
        pending_requests_ordered=True,
        requester_queue_position=2,
    )
    ordered_reason = wf._build_manager_slot_waiting_reason(
        runtime_id="codex_cli",
        request=_make_request(),
        manager_state=ordered_state,
    )
    assert "queue_position=2" in ordered_reason


def test_record_carries_authoritative_ordered_fresh_flags() -> None:
    """R5: ordered/fresh attestations travel with the transition.

    Identical polls carrying the same attestation dedupe; an attestation
    change emits one bounded reason_changed event.
    """
    wf = MoonMindAgentRun()
    entered = wf._record_provider_wait_observation(
        **_record_kwargs(queue_position=2, queue_ordered=True, queue_fresh=True)
    )
    assert entered is not None and entered["transition"] == "wait_entered"
    assert entered["queue_ordered"] is True
    assert entered["queue_fresh"] is True
    assert entered["next_check"] is None
    # Identical poll with the same attestation: no new event.
    assert (
        wf._record_provider_wait_observation(
            **_record_kwargs(
                queue_position=2, queue_ordered=True, queue_fresh=True
            )
        )
        is None
    )
    # Attestation loss (ordered snapshot gone stale) is a real change.
    changed = wf._record_provider_wait_observation(
        **_record_kwargs(
            queue_position=None, queue_ordered=False, queue_fresh=False
        )
    )
    assert changed is not None and changed["transition"] == "reason_changed"
    assert changed["queue_ordered"] is False
    # Unknown stays unknown: a poll without attestation after an
    # unattested wait still dedupes.
    fresh_wf = MoonMindAgentRun()
    assert fresh_wf._record_provider_wait_observation(**_record_kwargs()) is not None
    assert fresh_wf._record_provider_wait_observation(**_record_kwargs()) is None


@pytest.mark.asyncio
async def test_signal_forwards_ordered_fresh_and_entered_fragments(
    monkeypatch,
) -> None:
    """R5: authoritative flags, entry time, and next-check reach the parent.

    The two-arg signal is preserved; observed fields travel as safe
    fragments. No ETA is ever implied.
    """
    calls = _patch_canonical_signal(monkeypatch)
    wf = MoonMindAgentRun()
    parent = object()
    transition = await wf._signal_provider_slot_wait(
        parent,
        runtime_id="codex_cli",
        requester_workflow_id="agent-run-1",
        profile_ref="p1",
        reason="awaiting_provider_capacity",
        queue_position=2,
        queue_ordered=True,
        queue_fresh=True,
        next_check="2026-09-14T07:01:00+00:00",
        revision=7,
    )
    assert transition is not None and transition["transition"] == "wait_entered"
    assert len(calls) == 1
    new_state, parent_reason = calls[0]
    assert new_state == "awaiting_slot"
    assert "queue_position=2" in parent_reason
    assert "queue_ordered=1" in parent_reason
    assert "queue_fresh=1" in parent_reason
    assert "next_check=2026-09-14T07:01:00+00:00" in parent_reason
    assert "wait_entered_at=" in parent_reason
    assert "ETA" not in parent_reason and "eta" not in parent_reason.lower()
    # Unattested observations carry no flag fragments and invent none.
    second_wf = MoonMindAgentRun()
    await second_wf._signal_provider_slot_wait(
        parent,
        runtime_id="codex_cli",
        requester_workflow_id="agent-run-2",
        profile_ref="p1",
        reason="awaiting_provider_capacity",
        revision=7,
    )
    assert len(calls) == 2
    _, unattested_reason = calls[1]
    assert "queue_ordered=" not in unattested_reason
    assert "queue_fresh=" not in unattested_reason
    assert "next_check=" not in unattested_reason


@pytest.mark.asyncio
async def test_signal_rejects_malicious_next_check_fragment(
    monkeypatch,
) -> None:
    """R5/R7: hostile next-check values never widen the parent payload."""
    calls = _patch_canonical_signal(monkeypatch)
    wf = MoonMindAgentRun()
    parent = object()
    for hostile in (
        "2026-09-14T07:01:00+00:00; lease=abc",
        "soon, now",
        "in 5 minutes",
        "x" * 65,
    ):
        transition = await wf._signal_provider_slot_wait(
            parent,
            runtime_id="codex_cli",
            requester_workflow_id="agent-run-1",
            profile_ref="p1",
            reason="awaiting_provider_capacity",
            next_check=hostile,
            revision=7,
        )
        # The first hostile poll records; repeats dedupe.
        assert len(calls) >= 1
        _, parent_reason = calls[-1]
        assert "lease" not in parent_reason
        assert "next_check=" not in parent_reason
        assert transition is None or "ETA" not in str(transition)


def test_replay_compat_pre_flag_history_upgrades_without_fabrication() -> None:
    """R6: histories recorded before ordered/fresh flags still replay.

    A recorded wait without flag keys reuses verbatim; unattested polls
    dedupe against it; one bounded reason_changed persists newly observed
    attestation, and identical polls dedupe afterwards. JSON round-trips
    (worker restart / Continue-As-New shape) preserve the flags.
    """
    import copy
    import json

    wf = MoonMindAgentRun()
    entered = wf._record_provider_wait_observation(**_record_kwargs())
    assert entered is not None
    assert entered["queue_ordered"] is None
    assert entered["queue_fresh"] is None
    assert entered["next_check"] is None
    # Retained-history reads reuse the recorded observation verbatim.
    assert wf._reuse_recorded_provider_wait() == entered
    # Unattested polls dedupe against pre-flag history.
    assert wf._record_provider_wait_observation(**_record_kwargs()) is None
    # Newly observed attestation emits exactly one bounded transition.
    attested = wf._record_provider_wait_observation(
        **_record_kwargs(queue_position=2, queue_ordered=True, queue_fresh=True)
    )
    assert attested is not None and attested["transition"] == "reason_changed"
    assert wf._record_provider_wait_observation(
        **_record_kwargs(queue_position=2, queue_ordered=True, queue_fresh=True)
    ) is None
    # Worker restart / Continue-As-New: flags survive the round-trip and
    # reuse verbatim with no invented transitions.
    snapshot = json.loads(
        json.dumps(
            {
                "state": wf._provider_wait_state,
                "entered_at": wf._provider_wait_entered_at,
                "cumulative_seconds": wf._provider_wait_cumulative_seconds,
            }
        )
    )
    restarted = MoonMindAgentRun()
    restarted._provider_wait_state = copy.deepcopy(snapshot["state"])
    restarted._provider_wait_entered_at = snapshot["entered_at"]
    restarted._provider_wait_cumulative_seconds = snapshot["cumulative_seconds"]
    assert restarted._reuse_recorded_provider_wait() == wf._provider_wait_state
    assert (
        restarted._record_provider_wait_observation(
            **_record_kwargs(
                queue_position=2, queue_ordered=True, queue_fresh=True
            )
        )
        is None
    )


# MoonLadderStudios/MoonMind#4363: an unqueryable singleton names itself.


def _wedged_manager_state(**overrides):
    state = {
        "running": True,
        "inspection_succeeded": False,
        "workflow_id": "provider-profile-manager:opencode",
        "runtime_id": "opencode",
        "status": "RUNNING",
        "inspection_status": "RPC_ERROR_FAILED_PRECONDITION",
        "error": "Unable to query workflow due to Workflow Task in failed state.",
    }
    state.update(overrides)
    return state


def test_manager_slot_wait_health_names_unqueryable_singleton() -> None:
    health = manager_slot_wait_health(_wedged_manager_state())

    assert health == {
        "workflow_id": "provider-profile-manager:opencode",
        "inspection_status": "RPC_ERROR_FAILED_PRECONDITION",
        "error": "Unable to query workflow due to Workflow Task in failed state.",
    }


def test_manager_slot_wait_health_absent_unless_wedge_signature() -> None:
    assert manager_slot_wait_health(_base_manager_state()) is None
    assert manager_slot_wait_health(_wedged_manager_state(running=False)) is None
    assert (
        manager_slot_wait_health(_wedged_manager_state(inspection_succeeded=True))
        is None
    )
    assert manager_slot_wait_health("not-a-mapping") is None


def test_manager_slot_wait_health_bounds_error_excerpt() -> None:
    health = manager_slot_wait_health(_wedged_manager_state(error="x" * 1000))

    assert health is not None
    assert len(health["error"]) <= 320
    assert health["error"].endswith("...[truncated]")


def test_manager_slot_wait_health_suffix_empty_when_healthy() -> None:
    assert manager_slot_wait_health_suffix(_base_manager_state()) == ""
    suffix = manager_slot_wait_health_suffix(_wedged_manager_state())
    assert "provider-profile-manager:opencode" in suffix
    assert "RPC_ERROR_FAILED_PRECONDITION" in suffix
    assert "lease" not in suffix and "credential" not in suffix


@pytest.mark.asyncio
async def test_inspected_wait_reason_names_unqueryable_manager(monkeypatch) -> None:
    """The parent progress summary names the wedged singleton, not just capacity."""
    _enable_canonical(monkeypatch)
    _mock_workflow_identity(monkeypatch)

    async def _fake_manager_state(self, **kwargs):
        return _wedged_manager_state()

    monkeypatch.setattr(
        MoonMindAgentRun,
        "_manager_state_for_slot_wait",
        _fake_manager_state,
    )
    wf = MoonMindAgentRun()
    reason = await wf._inspected_provider_slot_waiting_reason(
        manager_id="provider-profile-manager:opencode",
        runtime_id="opencode",
        request=_make_request(),
    )
    assert reason.startswith("awaiting_provider_capacity")
    assert "provider-profile-manager:opencode" in reason
    assert "RPC_ERROR_FAILED_PRECONDITION" in reason


@pytest.mark.asyncio
async def test_inspected_slot_wait_observation_carries_manager_health(
    monkeypatch,
) -> None:
    """The structured wait observation carries bounded manager health."""
    _enable_canonical(monkeypatch)
    _mock_workflow_identity(monkeypatch)

    async def _fake_manager_state(self, **kwargs):
        return _wedged_manager_state()

    monkeypatch.setattr(
        MoonMindAgentRun,
        "_manager_state_for_slot_wait",
        _fake_manager_state,
    )
    wf = MoonMindAgentRun()
    observation = await wf._inspected_provider_slot_wait(
        manager_id="provider-profile-manager:opencode",
        runtime_id="opencode",
        request=_make_request(),
    )
    assert observation["reason"].startswith("awaiting_provider_capacity")
    assert observation["manager_health"] == {
        "workflow_id": "provider-profile-manager:opencode",
        "inspection_status": "RPC_ERROR_FAILED_PRECONDITION",
        "error": "Unable to query workflow due to Workflow Task in failed state.",
    }


@pytest.mark.asyncio
async def test_inspected_slot_wait_observation_health_absent_when_healthy(
    monkeypatch,
) -> None:
    _enable_canonical(monkeypatch)
    _mock_workflow_identity(monkeypatch)

    async def _fake_manager_state(self, **kwargs):
        return _base_manager_state()

    monkeypatch.setattr(
        MoonMindAgentRun,
        "_manager_state_for_slot_wait",
        _fake_manager_state,
    )
    wf = MoonMindAgentRun()
    observation = await wf._inspected_provider_slot_wait(
        manager_id="manager-1",
        runtime_id="codex_cli",
        request=_make_request(),
    )
    assert observation["manager_health"] is None
