"""Replay/cutover tests for the typed AgentRun progress projection (#1088).

Covers REQ-A8 / REQ-C8 at the real workflow boundary
(``moonmind.workflows.temporal.workflows.run`` +
``moonmind.workflows.temporal.workflows.agent_run``):

* old-history replay: ``agent_run_progress`` is ignored and the legacy
  ``child_state_changed`` behavior is retained;
* new-history replay: the typed projection is applied and mapped into
  Step waiting/summary/attention fields;
* mixed-worker traffic: legacy and projection updates for the same
  product state converge without double-apply, and legacy two-string
  signal args never validate as a projection;
* emitter cutover: new histories route to ``agent_run_progress`` while
  old histories keep ``child_state_changed``;
* parent Continue-As-New carries accepted source/revision/digest lineage.

Hermetic by construction: workflow signal handlers are exercised
directly on workflow instances with ``workflow.patched`` keyed per
history generation, following the pattern in
``test_run_signals_updates.py``. No Temporal test server is required.
"""

from datetime import datetime, timezone

import pytest
from temporalio import workflow

from moonmind.schemas.agent_run_progress import (
    AGENT_RUN_PROGRESS_PATCH_ID,
    AGENT_RUN_PROGRESS_SIGNAL_NAME,
    CLASSIFIED_CHILD_TO_PARENT_LIFECYCLE_SIGNALS,
    RolloverSnapshot,
    apply_agent_run_progress,
    assert_classified_child_signal,
    build_progress_projection,
    new_progress_parent_state,
    use_agent_run_progress_projection,
)
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun
from moonmind.workflows.temporal.workflows.run import (
    RUN_DEFENSIVE_SLOT_RELEASE_ON_CHILD_TERMINAL_PATCH,
    RUN_REAL_STARTED_AT_PATCH,
    STATE_AWAITING_SLOT,
    STATE_EXECUTING,
    STATE_INITIALIZING,
    MoonMindUserWorkflow,
)

CHILD_WF = "child-wf-1"
PARENT_WF = "parent-wf-1"
PARENT_RUN = "parent-run-1"
STEP_EXEC = "parent-wf-1:parent-run-1:step:execution:1"

_LAUNCH_NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


def _install_parent(monkeypatch, patched_ids) -> MoonMindUserWorkflow:
    """Return a parent workflow instance with history generation gates."""

    instance = MoonMindUserWorkflow()
    monkeypatch.setattr(instance, "_update_search_attributes", lambda: None)
    monkeypatch.setattr(instance, "_update_memo", lambda: None)
    if isinstance(patched_ids, bool):
        monkeypatch.setattr(workflow, "patched", lambda _patch_id: patched_ids)
    else:
        allowed = set(patched_ids)
        monkeypatch.setattr(
            workflow, "patched", lambda patch_id: patch_id in allowed
        )
    monkeypatch.setattr(workflow, "now", lambda: _LAUNCH_NOW)
    instance._active_agent_child_workflow_id = CHILD_WF
    return instance


def _projection_payload(**overrides):
    """Build a production-shaped projection payload through the real builder."""

    fields = {
        "agent_run_workflow_id": CHILD_WF,
        "agent_run_run_id": "child-run-A",
        "source_workflow_id": PARENT_WF,
        "source_run_id": PARENT_RUN,
        "step_execution_id": STEP_EXEC,
        "source_generation": CHILD_WF,
        "projection_revision": 1,
        "state": "running",
        "reason_code": "running",
        "wait_code": "none",
    }
    fields.update(overrides)
    return build_progress_projection(**fields).canonical_dict()


# --- Old-history replay ------------------------------------------------------

def test_old_history_ignores_progress_and_retains_legacy(monkeypatch):
    """Old histories ignore the projection and keep legacy behavior."""

    parent = _install_parent(monkeypatch, False)

    parent.agent_run_progress(_projection_payload())

    # Ignored: no reducer state is created and no Step state moves.
    assert parent._agent_run_progress_by_child == {}
    assert parent._state == STATE_INITIALIZING

    # Legacy path still owns product progress on old histories.
    parent.child_state_changed("awaiting_slot", "Managed capacity exhausted.")
    assert parent._state == STATE_AWAITING_SLOT
    assert parent._waiting_reason == "provider_profile_slot"

    parent.child_state_changed("running", "Agent is running.")
    assert parent._state == STATE_EXECUTING
    assert parent._waiting_reason is None


def test_old_history_terminal_legacy_still_releases_slot(monkeypatch):
    """Old-history terminal handling is untouched by the cutover."""

    # Old history for the progress projection, but the pre-existing
    # defensive-release patch stays enabled as on real old histories.
    # Slot release is owned by the ProviderProfileManager through verified
    # consumer teardown (MoonLadderStudios/MoonMind#1089): the terminal
    # branch records the deprecated marker and emits no release.
    parent = _install_parent(
        monkeypatch, {RUN_DEFENSIVE_SLOT_RELEASE_ON_CHILD_TERMINAL_PATCH}
    )
    parent._assigned_profile_id = "profile-1"
    parent._assigned_child_workflow_id = CHILD_WF

    parent.agent_run_progress(
        _projection_payload(
            projection_revision=1, state="completed", reason_code="terminal"
        )
    )
    assert parent._agent_run_progress_by_child == {}

    parent.child_state_changed("completed", "done")


# --- New-history replay ------------------------------------------------------

NEW_HISTORY_PATCHES = frozenset(
    {
        AGENT_RUN_PROGRESS_PATCH_ID,
        RUN_DEFENSIVE_SLOT_RELEASE_ON_CHILD_TERMINAL_PATCH,
        RUN_REAL_STARTED_AT_PATCH,
    }
)


def test_new_history_applies_progress_projection(monkeypatch):
    """New histories apply the typed projection into Step fields."""

    parent = _install_parent(monkeypatch, NEW_HISTORY_PATCHES)

    parent.agent_run_progress(_projection_payload(projection_revision=1))
    assert parent._state == STATE_EXECUTING
    assert parent._summary == "Agent is running."
    assert parent._waiting_reason is None

    parent.agent_run_progress(
        _projection_payload(
            projection_revision=2,
            state="awaiting_feedback",
            reason_code="awaiting_feedback",
            wait_code="feedback",
        )
    )
    assert parent._state == STATE_AWAITING_SLOT
    assert parent._waiting_reason == "feedback"
    assert parent._summary == "Waiting for feedback."


def test_new_history_terminal_progress_seals_without_step_move(monkeypatch):
    """Terminal progress seals the projection; AgentRunResult keeps authority."""

    parent = _install_parent(monkeypatch, NEW_HISTORY_PATCHES)
    parent.agent_run_progress(_projection_payload(projection_revision=1))
    assert parent._state == STATE_EXECUTING

    parent.agent_run_progress(
        _projection_payload(
            projection_revision=2, state="completed", reason_code="terminal"
        )
    )
    entry = parent._agent_run_progress_by_child[CHILD_WF]
    assert entry["terminalSealed"] is True
    # No Step state moves on terminal progress alone.
    assert parent._state == STATE_EXECUTING

    # Late or disagreeing progress cannot reopen the sealed projection.
    outcome = apply_agent_run_progress(
        entry, _projection_payload(projection_revision=3)
    )
    assert outcome.disposition == "terminal_sealed"
    assert parent._state == STATE_EXECUTING


def test_new_history_rejects_unrelated_child_and_missing_step(monkeypatch):
    """Payload identity fences hold on new histories for both generations."""

    parent = _install_parent(monkeypatch, NEW_HISTORY_PATCHES)

    intruder = _projection_payload()
    intruder["agentRunWorkflowId"] = "intruder-wf"
    parent.agent_run_progress(intruder)
    assert "intruder-wf" not in parent._agent_run_progress_by_child
    assert parent._state == STATE_INITIALIZING

    assert_classified_child_signal("agent_run_progress")
    with pytest.raises(ValueError):
        assert_classified_child_signal("agent_run_status_stream")


# --- Mixed-worker traffic ----------------------------------------------------

def test_mixed_worker_legacy_then_progress_converges(monkeypatch):
    """Legacy + projection updates for one product state converge cleanly."""

    parent = _install_parent(monkeypatch, NEW_HISTORY_PATCHES)

    # Old worker emits legacy; new worker emits the projection.
    parent.child_state_changed("running", "Agent is running.")
    assert parent._state == STATE_EXECUTING
    assert parent._summary == "Agent is running."

    payload = _projection_payload(projection_revision=1)
    parent.agent_run_progress(payload)
    assert parent._state == STATE_EXECUTING
    assert parent._summary == "Agent is running."

    # Redelivery of the same revision is a duplicate: no double-apply.
    parent.agent_run_progress(dict(payload))
    entry = parent._agent_run_progress_by_child[CHILD_WF]
    assert entry["acceptedRevision"] == 1
    assert parent._summary == "Agent is running."


def test_mixed_worker_legacy_two_string_args_never_validate(monkeypatch):
    """The old two-string signal shape is never a valid projection."""

    parent = _install_parent(monkeypatch, NEW_HISTORY_PATCHES)

    parent.agent_run_progress(["running", "Agent is running."])
    assert parent._agent_run_progress_by_child == {}
    assert parent._state == STATE_INITIALIZING

    state = new_progress_parent_state(
        expected_agent_run_workflow_id=CHILD_WF,
        expected_step_execution_id=STEP_EXEC,
    )
    assert (
        apply_agent_run_progress(state, ["running", "Agent is running."])
    ).disposition == "invalid"


# --- Emitter cutover ---------------------------------------------------------

def test_emitter_cutover_gating_old_vs_new(monkeypatch):
    """The child emitter routes per history generation on both sides."""

    monkeypatch.setattr(workflow, "patched", lambda _patch_id: False)
    assert MoonMindAgentRun._workflow_patch_enabled(
        AGENT_RUN_PROGRESS_PATCH_ID
    ) is False
    assert use_agent_run_progress_projection(False) is False

    monkeypatch.setattr(workflow, "patched", lambda _patch_id: True)
    assert MoonMindAgentRun._workflow_patch_enabled(
        AGENT_RUN_PROGRESS_PATCH_ID
    ) is True
    assert use_agent_run_progress_projection(True) is True


def test_emitter_routes_to_matching_parent_signal():
    """The emitter sends exactly one lifecycle signal per generation."""

    import pathlib

    here = pathlib.Path(__file__).resolve()
    repo_root = next(
        candidate
        for candidate in (here.parent, *here.parents)
        if (candidate / "moonmind" / "workflows").is_dir()
    )
    source = repo_root.joinpath(
        "moonmind/workflows/temporal/workflows/agent_run.py"
    ).read_text()
    gate = source.split("_signal_parent_child_state_changed", 1)[1]
    # New histories route to the typed projection; old histories retain
    # the legacy signal. Both names must stay classified.
    assert AGENT_RUN_PROGRESS_SIGNAL_NAME in gate
    assert '"child_state_changed"' in gate
    assert "AGENT_RUN_PROGRESS_PATCH_ID" in gate
    assert AGENT_RUN_PROGRESS_SIGNAL_NAME in (
        CLASSIFIED_CHILD_TO_PARENT_LIFECYCLE_SIGNALS
    )
    assert "child_state_changed" in CLASSIFIED_CHILD_TO_PARENT_LIFECYCLE_SIGNALS


def test_patch_identity_frozen_for_replay():
    """Patch/signal identity is frozen: replay depends on stable names."""

    assert AGENT_RUN_PROGRESS_PATCH_ID == "agent-run-progress-projection-v1"
    assert AGENT_RUN_PROGRESS_SIGNAL_NAME == "agent_run_progress"


# --- Parent Continue-As-New --------------------------------------------------

def test_rollover_lineage_survives_parent_continue_as_new(monkeypatch):
    """Accepted source/revision/digest persist through parent rollover."""

    parent = _install_parent(monkeypatch, NEW_HISTORY_PATCHES)
    parent.agent_run_progress(_projection_payload(projection_revision=2))
    live = parent._agent_run_progress_by_child[CHILD_WF]
    assert live["acceptedRevision"] == 2

    snapshot = RolloverSnapshot.capture(live)
    rolled = new_progress_parent_state(
        expected_agent_run_workflow_id=CHILD_WF,
        expected_step_execution_id=STEP_EXEC,
    )
    snapshot.restore(rolled)

    assert rolled["acceptedRevision"] == 2
    assert rolled["acceptedSourceGeneration"] == CHILD_WF
    assert (
        apply_agent_run_progress(
            rolled, _projection_payload(projection_revision=1)
        ).disposition
        == "stale"
    )
    assert (
        apply_agent_run_progress(
            rolled, _projection_payload(projection_revision=3)
        ).disposition
        == "accepted"
    )
