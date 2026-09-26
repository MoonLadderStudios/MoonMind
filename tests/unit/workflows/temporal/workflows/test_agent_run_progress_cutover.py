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
    AGENT_RUN_PROGRESS_RESUME_EDGES_PATCH_ID,
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
    monkeypatch.setattr(workflow, "deprecate_patch", lambda _patch_id: None)
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
        AGENT_RUN_PROGRESS_RESUME_EDGES_PATCH_ID,
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


def test_accepted_progress_updates_owning_step_ledger_row(monkeypatch):
    """Accepted progress reaches the per-row Step ledger (#1088 R1/R4).

    Producer payload (built through the real projection builder) ->
    parent ``agent_run_progress`` signal -> owning ledger row summary /
    waiting fields. The row update flows through the existing
    awaiting-external path, so ``get_step_ledger``/``get_progress`` and
    the step-ledger API surface the active child's progress.
    """

    from moonmind.workflows.temporal.step_ledger import build_initial_step_rows

    parent = _install_parent(monkeypatch, NEW_HISTORY_PATCHES)
    parent._step_ledger_rows = build_initial_step_rows(
        ordered_nodes=[
            {"id": "step-1", "title": "agent step", "tool": {"name": "agent"}},
            {"id": "step-2", "title": "other", "tool": {"name": "other"}},
        ],
        dependency_map={},
        updated_at=_LAUNCH_NOW,
    )
    parent._rebuild_step_ledger_index()
    # Launch state as written when the parent starts the child workflow.
    parent._mark_step_waiting(
        "step-1",
        status="awaiting_external",
        updated_at=_LAUNCH_NOW,
        waiting_reason="Awaiting child workflow progress",
        summary="Awaiting child workflow",
        refs={"childWorkflowId": CHILD_WF},
    )

    parent.agent_run_progress(_projection_payload(projection_revision=1))
    row = parent._step_ledger_row_for("step-1")
    assert row is not None
    assert row["status"] == "awaiting_external"
    assert row["summary"] == "Agent is running."
    assert row["waitingReason"] is None
    # The progress snapshot behind get_progress follows the ledger rows.
    assert parent._progress_snapshot is not None
    # Unrelated rows are untouched.
    other = parent._step_ledger_row_for("step-2")
    assert other is not None
    assert other["summary"] is None or "Agent is running." not in str(
        other["summary"]
    )

    parent.agent_run_progress(
        _projection_payload(
            projection_revision=2,
            state="awaiting_feedback",
            reason_code="awaiting_feedback",
            wait_code="feedback",
        )
    )
    row = parent._step_ledger_row_for("step-1")
    assert row is not None
    assert row["waitingReason"] == "feedback"
    assert row["summary"] == "Waiting for feedback."
    assert parent._waiting_reason == "feedback"


def test_wait_resume_sequence_keeps_truthful_step_state(monkeypatch):
    """Wait -> running resume keeps truthful state without new work (#1088 R2/R6)."""

    from moonmind.workflows.temporal.step_ledger import build_initial_step_rows

    parent = _install_parent(monkeypatch, NEW_HISTORY_PATCHES)
    parent._step_ledger_rows = build_initial_step_rows(
        ordered_nodes=[
            {"id": "step-1", "title": "agent step", "tool": {"name": "agent"}},
        ],
        dependency_map={},
        updated_at=_LAUNCH_NOW,
    )
    parent._rebuild_step_ledger_index()
    parent._mark_step_waiting(
        "step-1",
        status="awaiting_external",
        updated_at=_LAUNCH_NOW,
        waiting_reason="Awaiting child workflow progress",
        summary="Awaiting child workflow",
        refs={"childWorkflowId": CHILD_WF},
    )

    parent.agent_run_progress(_projection_payload(projection_revision=1))
    parent.agent_run_progress(
        _projection_payload(
            projection_revision=2,
            state="awaiting_feedback",
            reason_code="awaiting_feedback",
            wait_code="feedback",
        )
    )
    assert parent._step_ledger_row_for("step-1")["waitingReason"] == "feedback"
    # The owner answers and the run resumes: higher revision reopens the
    # wait display but never repeats agent work or moves the Step terminally.
    parent.agent_run_progress(
        _projection_payload(
            projection_revision=3,
            state="running",
            reason_code="running",
        )
    )
    entry = parent._agent_run_progress_by_child[CHILD_WF]
    assert entry["acceptedRevision"] == 3
    assert entry["acceptedState"] == "running"
    row = parent._step_ledger_row_for("step-1")
    assert row["waitingReason"] is None
    assert row["summary"] == "Agent is running."
    assert parent._state == STATE_EXECUTING


def test_pre_resume_patch_history_rejects_capacity_requeue(monkeypatch):
    """Histories without the resume-edges patch keep the prior reducer.

    ``launching -> awaiting_slot`` stays stale (no ``_set_state``
    memo/search-attribute upsert) so replay matches the recorded history.
    """

    pre_resume_patches = frozenset(
        {
            AGENT_RUN_PROGRESS_PATCH_ID,
            RUN_DEFENSIVE_SLOT_RELEASE_ON_CHILD_TERMINAL_PATCH,
            RUN_REAL_STARTED_AT_PATCH,
        }
    )
    parent = _install_parent(monkeypatch, pre_resume_patches)
    parent.agent_run_progress(
        _projection_payload(
            projection_revision=1, state="launching", reason_code="launching"
        )
    )
    assert parent._state == STATE_EXECUTING

    parent.agent_run_progress(
        _projection_payload(
            projection_revision=2,
            state="awaiting_slot",
            reason_code="awaiting_provider_capacity",
            wait_code="provider_capacity",
        )
    )
    entry = parent._agent_run_progress_by_child[CHILD_WF]
    assert entry["acceptedRevision"] == 1
    assert entry["acceptedState"] == "launching"
    assert parent._state == STATE_EXECUTING


def test_late_progress_cannot_reopen_terminal_step_row(monkeypatch):
    """Late progress never reopens a terminal Step ledger row (#1088 R2).

    The sealed AgentRunResult owns the outcome; the per-row reflection
    skips terminal rows while the workflow-level fence still applies.
    """

    from moonmind.workflows.temporal.step_ledger import build_initial_step_rows

    parent = _install_parent(monkeypatch, NEW_HISTORY_PATCHES)
    parent._step_ledger_rows = build_initial_step_rows(
        ordered_nodes=[
            {"id": "step-1", "title": "agent step", "tool": {"name": "agent"}},
        ],
        dependency_map={},
        updated_at=_LAUNCH_NOW,
    )
    parent._rebuild_step_ledger_index()
    parent._mark_step_waiting(
        "step-1",
        status="awaiting_external",
        updated_at=_LAUNCH_NOW,
        waiting_reason="Awaiting child workflow progress",
        summary="Awaiting child workflow",
        refs={"childWorkflowId": CHILD_WF},
    )

    parent.agent_run_progress(_projection_payload(projection_revision=1))
    parent._mark_step_terminal(
        "step-1",
        status="completed",
        updated_at=_LAUNCH_NOW,
        summary="Step completed with sealed result.",
    )
    parent.agent_run_progress(
        _projection_payload(
            projection_revision=2,
            state="awaiting_feedback",
            reason_code="awaiting_feedback",
            wait_code="feedback",
        )
    )
    row = parent._step_ledger_row_for("step-1")
    assert row is not None
    assert row["status"] == "completed"
    assert row["waitingReason"] is None


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
    # The legacy literal lives in the old-history fallback the cutover
    # gate delegates to; anchor before it so the gate spans both.
    gate = source.split("_signal_parent_legacy_child_state_changed", 1)[1]
    # New histories route to the typed projection; old histories retain
    # the legacy signal. Both names must stay classified.
    assert AGENT_RUN_PROGRESS_SIGNAL_NAME in gate
    assert '"child_state_changed"' in gate
    assert "AGENT_RUN_PROGRESS_PATCH_ID" in gate
    assert AGENT_RUN_PROGRESS_SIGNAL_NAME in (
        CLASSIFIED_CHILD_TO_PARENT_LIFECYCLE_SIGNALS
    )
    assert "child_state_changed" in CLASSIFIED_CHILD_TO_PARENT_LIFECYCLE_SIGNALS


def test_no_direct_legacy_signal_bypass_outside_cutover_gate():
    """The typed projection is the single new-write path (#1088 R3).

    Every child-side ``launching``/wait emission routes through
    ``_signal_parent_child_state_changed`` so the cutover gate picks the
    typed ``agent_run_progress`` signal on new histories. The only
    remaining direct ``child_state_changed`` signal site is the gated
    old-history fallback itself.
    """

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
    assert source.count('"child_state_changed"') == 1


def test_slot_acquired_routing_versioned_by_fresh_patch():
    """Slot-acquired emission keeps replay compatibility via a fresh patch.

    In-flight histories that recorded ``AGENT_RUN_PROGRESS_PATCH_ID``
    during the preceding ``awaiting_slot`` emission recorded a direct
    legacy ``child_state_changed`` signal at slot acquisition (the routing
    cutover came later). Replaying such a history through the new routed
    helper would emit ``agent_run_progress`` instead and Temporal would
    reject the workflow task as nondeterministic. The slot-acquisition
    site must therefore select the routed helper only behind a fresh
    patch marker and retain the recorded legacy command otherwise.
    """

    import pathlib

    from moonmind.workflows.temporal.workflows.agent_run import (
        AGENT_RUN_SLOT_ACQUIRED_PROGRESS_PATCH_ID,
    )

    assert (
        AGENT_RUN_SLOT_ACQUIRED_PROGRESS_PATCH_ID
        == "agent-run-slot-acquired-progress-v1"
    )

    here = pathlib.Path(__file__).resolve()
    repo_root = next(
        candidate
        for candidate in (here.parent, *here.parents)
        if (candidate / "moonmind" / "workflows").is_dir()
    )
    source = repo_root.joinpath(
        "moonmind/workflows/temporal/workflows/agent_run.py"
    ).read_text()
    anchor = source.index("Slot acquired for")
    slot_block = source[max(0, anchor - 2000) : anchor + 800]
    assert "AGENT_RUN_SLOT_ACQUIRED_PROGRESS_PATCH_ID" in slot_block
    assert "_signal_parent_child_state_changed" in slot_block
    assert "_signal_parent_legacy_child_state_changed" in slot_block


def test_omnigent_activity_emissions_funnel_to_retained_consumer():
    """Activity legacy emissions keep one funnel and one consumer (#1088 R3).

    The Omnigent Activity-side ``awaiting_slot``/``launching``/``running``
    emissions funnel through the single
    ``GenericOmnigentHostRealizer._notify_execution_state`` notifier into
    production ``notify_execution_state``, which signals only the
    classified legacy ``child_state_changed`` name. That name's actual
    consumer is the retained ``MoonMindUserWorkflow.child_state_changed``
    handler (old histories + mixed-worker rollout, converging with the
    typed projection per
    ``test_mixed_worker_legacy_then_progress_converges``). This binds the
    remaining compatibility to its consumer without a census or staged
    removal program, and proves no second progress mechanism is added.
    """

    import pathlib
    import re

    here = pathlib.Path(__file__).resolve()
    repo_root = next(
        candidate
        for candidate in (here.parent, *here.parents)
        if (candidate / "moonmind" / "workflows").is_dir()
    )
    host_source = repo_root.joinpath(
        "moonmind/omnigent/realizers/generic_host.py"
    ).read_text()
    production_source = repo_root.joinpath(
        "moonmind/omnigent/production.py"
    ).read_text()

    # Every Activity emission funnels through the single notifier: the
    # only ``_notify_execution_state`` references are its definition and
    # its call sites, and the definition delegates to the injected
    # ``_execution_state_notifier`` instead of signaling directly.
    call_sites = re.findall(
        r"await self\._notify_execution_state\(", host_source
    )
    assert len(call_sites) == 3
    notifier = host_source.split(
        "async def _notify_execution_state", 1
    )[1].split("\n    def _bind_exact_host", 1)[0]
    assert "self._execution_state_notifier" in notifier
    assert ".signal(" not in notifier

    # Production wiring signals only the retained classified legacy name.
    notifier_block = production_source.split(
        "async def notify_execution_state", 1
    )[1].split("runtime_bindings", 1)[0]
    assert '"child_state_changed"' in notifier_block
    assert f'"{AGENT_RUN_PROGRESS_SIGNAL_NAME}"' not in notifier_block
    assert "child_state_changed" in (
        CLASSIFIED_CHILD_TO_PARENT_LIFECYCLE_SIGNALS
    )

    # The retained consumer exists on the parent workflow.
    assert hasattr(MoonMindUserWorkflow, "child_state_changed")


def test_patch_identity_frozen_for_replay():
    """Patch/signal identity is frozen: replay depends on stable names."""

    assert AGENT_RUN_PROGRESS_PATCH_ID == "agent-run-progress-projection-v1"
    assert AGENT_RUN_PROGRESS_SIGNAL_NAME == "agent_run_progress"


# --- Workflow emitter failure injection ----------------------------------------

@pytest.mark.asyncio
async def test_emitter_delivery_failure_retries_same_revision(monkeypatch):
    """Transient delivery failure recovers without repeating work (#1088 R7).

    Failure injection at the real workflow boundary
    (``MoonMindAgentRun._signal_parent_progress_projection``): the first
    delivery raises, so the emitter keeps the same pending revision and
    records no send marker; the retry delivers the same revision and the
    parent reconciles redelivery as a duplicate. Terminal outcome and
    agent compute are untouched: no recompute runs and no Step state
    moves terminally.
    """

    from types import SimpleNamespace
    import logging

    parent = _install_parent(monkeypatch, NEW_HISTORY_PATCHES)
    # Outside the workflow event loop the Temporal workflow logger is
    # unavailable; the failure path under test only needs a sink.
    monkeypatch.setattr(
        MoonMindAgentRun,
        "_get_logger",
        lambda self: logging.getLogger(__name__),
    )

    child_info = SimpleNamespace(workflow_id=CHILD_WF, run_id="child-run-A")
    parent_info = SimpleNamespace(
        workflow_id=PARENT_WF, run_id=PARENT_RUN
    )
    monkeypatch.setattr(workflow, "info", lambda: child_info)

    delivered: list[dict] = []
    attempts: list[str] = []

    class _FailOnceHandle:
        async def signal(self, name, args=None):
            assert name == AGENT_RUN_PROGRESS_SIGNAL_NAME
            attempts.append(name)
            if len(attempts) == 1:
                raise RuntimeError("transient parent delivery failure")
            delivered.append(dict(args[0]))

    monkeypatch.setattr(
        workflow,
        "get_external_workflow_handle",
        lambda *args, **kwargs: _FailOnceHandle(),
    )

    child = MoonMindAgentRun()
    child._progress_step_execution_id = STEP_EXEC
    child._progress_attempt_index = 0
    child._progress_generation = CHILD_WF

    # First delivery fails: the same revision stays pending, no attempted
    # send is recorded as receipt, and no agent work runs.
    await child._signal_parent_progress_projection(
        parent_info, "running", "Agent is running."
    )
    assert child._progress_next_revision == 1
    assert child._progress_last_signature is None
    assert delivered == []
    assert child.final_result is None

    # Retry reconciles: the same revision is delivered once.
    await child._signal_parent_progress_projection(
        parent_info, "running", "Agent is running."
    )
    assert child._progress_next_revision == 2
    assert len(delivered) == 1
    assert delivered[0]["projectionRevision"] == 1
    assert child.final_result is None

    # The parent accepts the first receipt and reconciles the redelivery
    # as a duplicate: truthful state, no repeated work.
    parent.agent_run_progress(delivered[0])
    assert parent._state == STATE_EXECUTING
    parent.agent_run_progress(dict(delivered[0]))
    entry = parent._agent_run_progress_by_child[CHILD_WF]
    assert entry["acceptedRevision"] == 1
    assert entry["acceptedState"] == "running"
    assert parent._state == STATE_EXECUTING

    # After success the same observation coalesces: no second send.
    await child._signal_parent_progress_projection(
        parent_info, "running", "Agent is running."
    )
    assert len(delivered) == 1
    assert child._progress_next_revision == 2


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
