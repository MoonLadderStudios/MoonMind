"""Unit tests for the typed AgentRun progress projection (#1088).

Covers the contract (REQ-C1..C8) and acceptance (REQ-A1..A9) surface of
``moonmind.schemas.agent_run_progress`` hermetically: schema strictness,
redaction, emitter/reducer determinism, ordering across retries and
Continue-As-New, Step mapping, repair boundaries, journey parity, replay,
and architecture guards.
"""

import copy

import pytest
from pydantic import ValidationError

from moonmind.schemas.agent_run_progress import (
    AGENT_RUN_PROGRESS_PATCH_ID,
    AGENT_RUN_PROGRESS_RETIREMENT_INVENTORY,
    AGENT_RUN_PROGRESS_SCHEMA_VERSION,
    AGENT_RUN_PROGRESS_SIGNAL_NAME,
    CLASSIFIED_CHILD_TO_PARENT_LIFECYCLE_SIGNALS,
    FORBIDDEN_PROGRESS_INFRASTRUCTURE,
    MAX_PROGRESS_SUMMARY_CHARS,
    PROGRESS_REPAIR_ACTIVITY,
    AgentRunProgressEmitter,
    AgentRunProgressProjection,
    RolloverSnapshot,
    apply_agent_run_progress,
    assert_classified_child_signal,
    build_progress_projection,
    build_progress_repair_read,
    child_source_generation,
    coerce_legacy_progress_triple,
    new_progress_parent_state,
    note_successor_generation,
    progress_step_logical_id_for_child,
    projection_digest,
    reduce_progress_to_step,
    seal_terminal_result,
    use_agent_run_progress_projection,
    validate_diagnostic_artifact_access,
)


def _payload(**overrides):
    base = {
        "schemaVersion": AGENT_RUN_PROGRESS_SCHEMA_VERSION,
        "agentRunWorkflowId": "child-wf-1",
        "agentRunRunId": "child-run-A",
        "sourceWorkflowId": "parent-wf-1",
        "sourceRunId": "parent-run-1",
        "stepExecutionId": "parent-wf-1:parent-run-1:step:execution:1",
        "attemptIndex": 0,
        "sourceGeneration": "child-wf-1",
        "projectionRevision": 1,
        "state": "running",
        "reasonCode": "running",
        "waitCode": "none",
        "attentionRequired": False,
    }
    base.update(overrides)
    return base


def _parent():
    return new_progress_parent_state(
        expected_agent_run_workflow_id="child-wf-1",
        expected_step_execution_id="parent-wf-1:parent-run-1:step:execution:1",
    )


# --- REQ-A1: schema, size limits, redaction, forbidden fields, refs --------

ALL_CANONICAL_STATES = [
    "queued",
    "awaiting_slot",
    "launching",
    "running",
    "awaiting_callback",
    "awaiting_feedback",
    "awaiting_approval",
    "intervention_requested",
    "collecting_results",
    "completed",
    "failed",
    "canceled",
    "timed_out",
]


def test_schema_accepts_every_canonical_state_with_matching_reason():
    for state in ALL_CANONICAL_STATES:
        reason = "terminal" if state in {"completed", "failed", "canceled", "timed_out"} else (
            "awaiting_provider_capacity"
            if state == "awaiting_slot"
            else ("launching" if state == "launching" else ("running" if state == "running" else "none"))
        )
        # awaiting_callback/feedback/approval map to their own codes when set
        if state in {"awaiting_callback", "awaiting_feedback", "awaiting_approval"}:
            reason = state
        elif state in {"intervention_requested", "collecting_results"}:
            reason = "awaiting_feedback" if state == "intervention_requested" else "collecting_results"
        model = AgentRunProgressProjection.model_validate(
            _payload(state=state, reasonCode=reason)
        )
        assert model.state == state
        assert model.schema_version == AGENT_RUN_PROGRESS_SCHEMA_VERSION


def test_schema_rejects_status_response_and_foreign_enums():
    with pytest.raises(ValidationError):
        AgentRunProgressProjection.model_validate(
            _payload(state="in_progress")  # provider-style, not AgentRunState
        )
    with pytest.raises(ValidationError):
        AgentRunProgressProjection.model_validate(_payload(state="merged"))


def test_schema_rejects_arbitrary_metadata_and_provider_fields():
    forbidden = {
        "metadata": {"progress": 1},
        "providerSessionId": "sess-1",
        "turnId": "turn-1",
        "host": "worker-1",
        "dockerContainer": "abc",
        "workspace": "/work/x",
        "profileLease": "lease-1",
        "credentialGeneration": 3,
        "credentialHandle": "h",
        "chatBinding": "b",
        "rawEvents": [],
        "runId": "provider-run-1",
    }
    with pytest.raises(ValidationError):
        AgentRunProgressProjection.model_validate(_payload(**forbidden))


def test_summary_is_redacted_and_bounded():
    long_text = "x" * (MAX_PROGRESS_SUMMARY_CHARS + 100)
    model = AgentRunProgressProjection.model_validate(_payload(summary=long_text))
    assert model.summary is not None
    assert len(model.summary) <= MAX_PROGRESS_SUMMARY_CHARS
    assert model.summary.endswith("...")

    model = AgentRunProgressProjection.model_validate(
        _payload(summary="failed with token=supersecret-value here")
    )
    assert model.summary is not None
    assert "supersecret-value" not in model.summary
    assert "[REDACTED]" in model.summary


@pytest.mark.parametrize(
    "summary",
    [
        "progress for session_id abc",
        "turn_id 123 finished",
        "located at docker://abc123",
        "credential_handle h-1 leaked",
        "chat_binding owned",
        "api_key material",
    ],
)
def test_summary_rejects_authority_smuggling(summary):
    with pytest.raises(ValidationError):
        AgentRunProgressProjection.model_validate(_payload(summary=summary))


def test_diagnostic_ref_must_be_opaque_and_validated_at_read_boundary():
    good = AgentRunProgressProjection.model_validate(
        _payload(diagnosticArtifactRef="artifact://diagnostics/run-1")
    )
    assert good.diagnostic_artifact_ref == "artifact://diagnostics/run-1"

    for bad in ["/abs/path.json", "../escape.json", "https://host/x", "plain-name"]:
        with pytest.raises(ValidationError):
            AgentRunProgressProjection.model_validate(
                _payload(diagnosticArtifactRef=bad)
            )

    accessible = {"artifact://diagnostics/run-1": "sha256:abc"}
    assert (
        validate_diagnostic_artifact_access(
            "artifact://diagnostics/run-1",
            scope="scope-1",
            expected_digest="sha256:abc",
            accessible_refs=accessible,
        )
        == "artifact://diagnostics/run-1"
    )
    # Possession alone is not authorization.
    with pytest.raises(ValueError):
        validate_diagnostic_artifact_access(
            "artifact://diagnostics/other",
            scope="scope-1",
            expected_digest=None,
            accessible_refs=accessible,
        )
    with pytest.raises(ValueError):
        validate_diagnostic_artifact_access(
            "artifact://diagnostics/run-1",
            scope="scope-1",
            expected_digest="sha256:wrong",
            accessible_refs=accessible,
        )


# --- REQ-A2: duplicate / conflict / gap / order / identity / spoof --------

def test_duplicate_conflict_gap_and_stale():
    state = _parent()
    first = _payload(projectionRevision=1, summary="one")
    assert apply_agent_run_progress(state, first).disposition == "accepted"

    assert apply_agent_run_progress(state, copy.deepcopy(first)).disposition == "duplicate"

    conflicted = dict(first, summary="different")
    assert apply_agent_run_progress(state, conflicted).disposition == "conflict"
    # Conflict cannot overwrite accepted state.
    assert state["acceptedDigest"] == projection_digest(
        AgentRunProgressProjection.model_validate(first).canonical_dict()
    )

    # Gaps need no replay of every omitted event.
    assert (
        apply_agent_run_progress(state, _payload(projectionRevision=3)).disposition
        == "accepted"
    )
    # Older revisions are ignored.
    assert (
        apply_agent_run_progress(state, _payload(projectionRevision=2)).disposition
        == "stale"
    )


def test_wrong_child_step_and_spoofed_source_rejected():
    state = _parent()
    assert (
        apply_agent_run_progress(
            state, _payload(agentRunWorkflowId="intruder-wf")
        ).disposition
        == "wrong_identity"
    )
    assert (
        apply_agent_run_progress(
            state, _payload(stepExecutionId="other:step:9")
        ).disposition
        == "wrong_identity"
    )
    # First contact from the expected child binds the generation; a later
    # message with a generation the parent never established is rejected,
    # so an unrelated caller cannot fabricate a successor.
    assert (
        apply_agent_run_progress(state, _payload(projectionRevision=1)).disposition
        == "accepted"
    )
    assert (
        apply_agent_run_progress(
            state,
            _payload(projectionRevision=2, sourceGeneration="spoofed-gen"),
        ).disposition
        == "old_generation"
    )


def test_invalid_input_yields_bounded_diagnostics():
    state = _parent()
    outcome = apply_agent_run_progress(state, {"not": "a projection"})
    assert outcome.disposition == "invalid"
    assert outcome.diagnostics
    # Parent state untouched.
    assert state["acceptedRevision"] == 0


def test_domain_invalid_regression_rejected_but_repeated_phases_pass():
    state = _parent()
    assert (
        apply_agent_run_progress(
            state,
            _payload(
                projectionRevision=1, state="collecting_results",
                reasonCode="collecting_results", waitCode="evidence",
            ),
        ).disposition
        == "accepted"
    )
    # collecting_results -> launching is a domain-invalid regression.
    assert (
        apply_agent_run_progress(
            state,
            _payload(
                projectionRevision=2, state="launching", reasonCode="launching"
            ),
        ).disposition
        == "stale"
    )
    # Legitimate repeated feedback phases share a rank and pass.
    state2 = _parent()
    apply_agent_run_progress(
        state2,
        _payload(
            projectionRevision=1, state="awaiting_feedback",
            reasonCode="awaiting_feedback", waitCode="feedback",
        ),
    )
    assert (
        apply_agent_run_progress(
            state2,
            _payload(
                projectionRevision=2, state="awaiting_approval",
                reasonCode="awaiting_approval", waitCode="approval",
            ),
        ).disposition
        == "accepted"
    )


# --- #1088 R6: legitimate wait/resume and capacity-requeue movement --------

def test_wait_resume_back_to_running_accepted():
    """Real producers resume running after a wait is answered (#1088 R6).

    running -> awaiting_feedback -> running is the actual owner-allowed
    resume path (feedback answered, deployment readiness restored); it
    must not be rejected as a domain-invalid regression.
    """

    state = _parent()
    assert (
        apply_agent_run_progress(
            state,
            _payload(
                projectionRevision=1, state="running", reasonCode="running"
            ),
        ).disposition
        == "accepted"
    )
    assert (
        apply_agent_run_progress(
            state,
            _payload(
                projectionRevision=2, state="awaiting_feedback",
                reasonCode="awaiting_feedback", waitCode="feedback",
            ),
        ).disposition
        == "accepted"
    )
    assert (
        apply_agent_run_progress(
            state,
            _payload(
                projectionRevision=3, state="running", reasonCode="running"
            ),
        ).disposition
        == "accepted"
    )
    assert state["acceptedState"] == "running"
    assert state["acceptedRevision"] == 3


def test_awaiting_callback_resume_to_launching_accepted():
    """awaiting_callback -> launching is the readiness-recovery path."""

    state = _parent()
    assert (
        apply_agent_run_progress(
            state,
            _payload(
                projectionRevision=1, state="awaiting_callback",
                reasonCode="awaiting_callback", waitCode="callback",
            ),
        ).disposition
        == "accepted"
    )
    assert (
        apply_agent_run_progress(
            state,
            _payload(
                projectionRevision=2, state="launching", reasonCode="launching"
            ),
        ).disposition
        == "accepted"
    )


def test_launching_capacity_requeue_to_awaiting_slot_accepted():
    """launching -> awaiting_slot is the capacity-requeue path (#1088 R6).

    Capacity the ledger took back between admission and allocation
    returns the run to durable waiting under the same owner instead of
    failing valid work.
    """

    state = _parent()
    assert (
        apply_agent_run_progress(
            state,
            _payload(
                projectionRevision=1, state="launching", reasonCode="launching"
            ),
        ).disposition
        == "accepted"
    )
    assert (
        apply_agent_run_progress(
            state,
            _payload(
                projectionRevision=2, state="awaiting_slot",
                reasonCode="awaiting_provider_capacity",
                waitCode="provider_capacity",
            ),
        ).disposition
        == "accepted"
    )


def test_true_regression_still_rejected_after_resume_rules():
    """Only owner-allowed resume edges pass; real regressions stay stale."""

    state = _parent()
    assert (
        apply_agent_run_progress(
            state,
            _payload(
                projectionRevision=1, state="collecting_results",
                reasonCode="collecting_results", waitCode="evidence",
            ),
        ).disposition
        == "accepted"
    )
    # Evidence collection never legitimately returns to launch or run.
    assert (
        apply_agent_run_progress(
            state,
            _payload(
                projectionRevision=2, state="launching", reasonCode="launching"
            ),
        ).disposition
        == "stale"
    )
    assert (
        apply_agent_run_progress(
            state,
            _payload(
                projectionRevision=2, state="running", reasonCode="running"
            ),
        ).disposition
        == "stale"
    )
    # running never returns to queued.
    state2 = _parent()
    apply_agent_run_progress(
        state2,
        _payload(projectionRevision=1, state="running", reasonCode="running"),
    )
    assert (
        apply_agent_run_progress(
            state2, _payload(projectionRevision=2, state="queued")
        ).disposition
        == "stale"
    )


def test_launching_requeue_rejected_without_resume_edges_patch():
    """Old histories retain the prior reducer for replay compatibility.

    The ``launching -> awaiting_slot`` capacity-requeue edge (and the other
    resume edges) was added after the progress cutover. Histories recorded
    while the reducer rejected that edge must keep rejecting it: accepting
    it on replay would emit memo/search-attribute upsert commands absent
    from the recorded history and wedge the workflow nondeterministically.
    New histories opt in through
    ``AGENT_RUN_PROGRESS_RESUME_EDGES_PATCH_ID``.
    """

    from moonmind.schemas.agent_run_progress import (
        AGENT_RUN_PROGRESS_RESUME_EDGES_PATCH_ID,
    )

    assert (
        AGENT_RUN_PROGRESS_RESUME_EDGES_PATCH_ID
        == "agent-run-progress-resume-edges-v1"
    )
    state = _parent()
    assert (
        apply_agent_run_progress(
            state,
            _payload(
                projectionRevision=1, state="launching", reasonCode="launching"
            ),
        ).disposition
        == "accepted"
    )
    assert (
        apply_agent_run_progress(
            state,
            _payload(
                projectionRevision=2, state="awaiting_slot",
                reasonCode="awaiting_provider_capacity",
                waitCode="provider_capacity",
            ),
            enable_resume_edges=False,
        ).disposition
        == "stale"
    )


def test_wait_resume_rejected_without_resume_edges_patch():
    """Old histories reject wait -> running resume edges as before."""

    state = _parent()
    assert (
        apply_agent_run_progress(
            state,
            _payload(
                projectionRevision=1, state="awaiting_feedback",
                reasonCode="awaiting_feedback", waitCode="feedback",
            ),
        ).disposition
        == "accepted"
    )
    assert (
        apply_agent_run_progress(
            state,
            _payload(
                projectionRevision=2, state="running", reasonCode="running"
            ),
            enable_resume_edges=False,
        ).disposition
        == "stale"
    )


# --- REQ-A3: retries, replacement, Continue-As-New, delayed old runs ------

def test_bounded_pending_observation_before_run_identity_established():
    state = _parent()
    early = _payload(projectionRevision=1, agentRunRunId=None)
    del early["agentRunRunId"]
    assert apply_agent_run_progress(state, early).disposition == "accepted"
    assert state["acceptedSourceGeneration"] == "child-wf-1"


def test_child_retry_new_run_adopts_and_old_run_cannot_reopen():
    state = _parent()
    assert apply_agent_run_progress(state, _payload(projectionRevision=1)).disposition == "accepted"
    # Same generation, new Temporal run (child retry): adopts the baseline.
    assert (
        apply_agent_run_progress(
            state, _payload(projectionRevision=1, agentRunRunId="child-run-B")
        ).disposition
        == "accepted"
    )
    # Delayed message from the superseded run cannot reopen state.
    assert (
        apply_agent_run_progress(
            state,
            _payload(projectionRevision=5, agentRunRunId="child-run-A"),
        ).disposition
        == "old_generation"
    )


def test_successor_generation_established_before_acceptance():
    state = _parent()
    apply_agent_run_progress(state, _payload(projectionRevision=1))
    note_successor_generation(state, new_generation="child-wf-2")
    # The old generation is now superseded.
    assert (
        apply_agent_run_progress(state, _payload(projectionRevision=2)).disposition
        == "old_generation"
    )
    adopted = _payload(
        projectionRevision=1, sourceGeneration="child-wf-2",
        agentRunWorkflowId="child-wf-1",
    )
    # Fence still enforced: generation adoption never bypasses identity.
    evil = dict(adopted, agentRunWorkflowId="intruder-wf")
    assert apply_agent_run_progress(state, evil).disposition == "wrong_identity"


def test_rollover_persists_lineage():
    state = _parent()
    apply_agent_run_progress(state, _payload(projectionRevision=2))
    snapshot = RolloverSnapshot.capture(state)
    restored = new_progress_parent_state(
        expected_agent_run_workflow_id="child-wf-1",
        expected_step_execution_id="parent-wf-1:parent-run-1:step:execution:1",
    )
    snapshot.restore(restored)
    assert restored["acceptedRevision"] == 2
    assert restored["acceptedSourceGeneration"] == "child-wf-1"
    assert (
        apply_agent_run_progress(restored, _payload(projectionRevision=1)).disposition
        == "stale"
    )
    assert (
        apply_agent_run_progress(restored, _payload(projectionRevision=3)).disposition
        == "accepted"
    )


def test_progress_step_lookup_finds_owning_step_row():
    """Accepted progress resolves its owning Step row by child fence (#1088 R4)."""

    rows = [
        {
            "logicalStepId": "step-1",
            "refs": {"childWorkflowId": "child-wf-1"},
        },
        {
            "logicalStepId": "step-2",
            "refs": {"childWorkflowId": "child-wf-9"},
        },
        {"logicalStepId": "step-3"},
    ]
    assert progress_step_logical_id_for_child(rows, "child-wf-1") == "step-1"
    assert progress_step_logical_id_for_child(rows, "child-wf-9") == "step-2"
    assert progress_step_logical_id_for_child(rows, "unknown-wf") is None
    assert progress_step_logical_id_for_child([], "child-wf-1") is None


def test_emitter_retry_reconciles_as_duplicate_without_repeating_work():
    """Same-revision retry after accepted delivery is a duplicate (#1088 R7).

    The emitter keeps the pending revision until positive delivery; the
    parent reconciles a redelivered revision as a duplicate, so transient
    delivery failure recovers through the existing bounded path without
    rerunning agent work.
    """

    from moonmind.schemas.agent_run_progress import AgentRunProgressEmitter

    emitter = AgentRunProgressEmitter(
        agent_run_workflow_id="child-wf-1",
        source_workflow_id="parent-wf-1",
        source_run_id="parent-run-1",
        step_execution_id="parent-wf-1:parent-run-1:step:execution:1",
        source_generation="child-wf-1",
        agent_run_run_id="child-run-A",
    )
    first = emitter.build(state="running", reason_code="running")
    assert first["projectionRevision"] == 1
    emitter.mark_delivered()
    changed = emitter.build(
        state="awaiting_feedback", reason_code="awaiting_feedback",
        wait_code="feedback",
    )
    # Delivery fails: the same revision stays pending for a bounded retry.
    retry = emitter.retry_pending()
    assert retry is not None
    assert retry["projectionRevision"] == changed["projectionRevision"]

    state = _parent()
    assert apply_agent_run_progress(state, changed).disposition == "accepted"
    # The retry reconciles as a duplicate: no repeated work, same state.
    assert apply_agent_run_progress(state, retry).disposition == "duplicate"
    assert state["acceptedState"] == "awaiting_feedback"


# --- REQ-A4: Step mapping without provider/harness branches ---------------

def test_single_reducer_maps_all_waits_and_states():
    cases = [
        ("awaiting_slot", "awaiting_provider_capacity", "provider_capacity", "provider_capacity"),
        ("launching", "launching", "none", None),
        ("running", "running", "none", None),
        ("awaiting_feedback", "awaiting_feedback", "feedback", "feedback"),
        ("awaiting_approval", "awaiting_approval", "approval", "approval"),
        ("awaiting_callback", "awaiting_callback", "callback", "callback"),
        ("collecting_results", "collecting_results", "evidence", "evidence"),
    ]
    for state, reason, wait, expected_waiting in cases:
        view = reduce_progress_to_step(
            {"state": state, "waitCode": wait, "summary": None,
             "attentionRequired": False}
        )
        assert view.waiting_reason == expected_waiting, state
        assert view.summary, state
    # Terminal states never wait.
    for state in ("completed", "failed", "canceled", "timed_out"):
        view = reduce_progress_to_step(
            {"state": state, "waitCode": "none", "summary": None,
             "attentionRequired": False}
        )
        assert view.waiting_reason is None


def test_legacy_mapping_table_is_total_and_branch_free():
    for legacy in (
        "queued", "awaiting_slot", "launching", "running",
        "awaiting_callback", "awaiting_feedback", "awaiting_approval",
        "intervention_requested", "collecting_results",
        "completed", "failed", "canceled", "cancelled", "timed_out",
    ):
        state, reason, wait = coerce_legacy_progress_triple(legacy)
        # Every mapped triple validates as a real projection.
        build_progress_projection(
            agent_run_workflow_id="child-wf-1",
            source_workflow_id="parent-wf-1",
            source_run_id="parent-run-1",
            step_execution_id="step-1",
            source_generation="child-wf-1",
            projection_revision=1,
            state=state,
            reason_code=reason,
            wait_code=wait,
        )
    with pytest.raises(ValueError):
        coerce_legacy_progress_triple("provider_specific_weird_state")


# --- REQ-A5: terminal authority --------------------------------------------

def test_terminal_result_seals_over_late_progress():
    state = _parent()
    apply_agent_run_progress(state, _payload(projectionRevision=1))
    seal_terminal_result(state, status="completed")
    outcome = apply_agent_run_progress(state, _payload(projectionRevision=2))
    assert outcome.disposition == "terminal_sealed"


def test_termination_without_final_progress_keeps_result_authoritative():
    state = _parent()
    # No progress ever arrived; sealing with the actual result still governs.
    seal_terminal_result(state, status="failed")
    assert state["terminalSealed"] is True
    assert state["terminalStatus"] == "failed"
    outcome = apply_agent_run_progress(
        state,
        _payload(
            projectionRevision=1, state="running", reasonCode="running"
        ),
    )
    assert outcome.disposition == "terminal_sealed"


def test_terminal_progress_disagreement_cannot_reopen():
    state = _parent()
    apply_agent_run_progress(
        state,
        _payload(
            projectionRevision=1, state="completed", reasonCode="terminal"
        ),
    )
    late = apply_agent_run_progress(
        state,
        _payload(
            projectionRevision=2, state="running", reasonCode="running"
        ),
    )
    assert late.disposition in {"terminal_sealed", "stale"}


# --- REQ-A6: delivery overhead, retry, closure, repair ---------------------

def test_emitter_coalesces_and_retries_same_revision():
    emitter = AgentRunProgressEmitter(
        agent_run_workflow_id="child-wf-1",
        source_workflow_id="parent-wf-1",
        source_run_id="parent-run-1",
        step_execution_id="step-1",
        source_generation="child-wf-1",
        agent_run_run_id="child-run-A",
    )
    first = emitter.build(state="running", reason_code="running")
    assert emitter.should_emit(first) is True
    emitter.mark_delivered()
    # Repeated observation with no meaningful change: suppressed.
    repeat = emitter.build(state="running", reason_code="running")
    assert repeat["projectionRevision"] == 2
    assert emitter.should_emit(repeat) is False
    # Meaningful change emits at the pending revision; a delivery failure
    # keeps the same revision for a bounded retry.
    changed = emitter.build(
        state="awaiting_feedback", reason_code="awaiting_feedback",
        wait_code="feedback",
    )
    assert emitter.should_emit(changed) is True
    assert emitter.pending_revision == 2
    retry = emitter.retry_pending()
    assert retry is not None
    assert retry["projectionRevision"] == 2
    assert projection_digest(retry) == projection_digest(changed)
    emitter.mark_delivered()
    assert emitter.next_revision() == 3
    assert emitter.retry_pending() is None


def test_repair_read_uses_existing_activity_boundary_only():
    read = build_progress_repair_read(
        agent_run_workflow_id="child-wf-1", step_execution_id="step-1"
    )
    assert read["activity"] == PROGRESS_REPAIR_ACTIVITY
    assert read["purpose"] == "agent-run-progress-display-repair"


def test_no_second_event_store_or_background_service():
    import pathlib
    import re

    source = pathlib.Path(__file__).parent.parent.parent.parent.joinpath(
        "moonmind/schemas/agent_run_progress.py"
    ).read_text()
    # The guard tuple names the forbidden infrastructure so architecture
    # tests can reference it; nothing else may introduce it.
    scrubbed = re.sub(
        r"FORBIDDEN_PROGRESS_INFRASTRUCTURE[^)]*\)",
        "",
        source,
        flags=re.DOTALL,
    )
    for forbidden in FORBIDDEN_PROGRESS_INFRASTRUCTURE:
        assert forbidden not in scrubbed


# --- REQ-A7: journey parity, explicit rejection ----------------------------

def test_cutover_gating_old_vs_new():
    assert use_agent_run_progress_projection(False) is False
    assert use_agent_run_progress_projection(True) is True


def test_unsupported_combinations_fail_explicitly():
    with pytest.raises(ValidationError):
        build_progress_projection(
            agent_run_workflow_id="child-wf-1",
            source_workflow_id="parent-wf-1",
            source_run_id="parent-run-1",
            step_execution_id="step-1",
            source_generation="child-wf-1",
            projection_revision=0,  # revisions are 1-based
            state="running",
            reason_code="running",
        )
    with pytest.raises(ValidationError):
        build_progress_projection(
            agent_run_workflow_id="child-wf-1",
            source_workflow_id="parent-wf-1",
            source_run_id="parent-run-1",
            step_execution_id="step-1",
            source_generation="child-wf-1",
            projection_revision=1,
            state="running",
            reason_code="terminal",  # terminal reason on live state
        )


def test_child_source_generation_is_workflow_owned():
    assert child_source_generation("child-wf-1") == "child-wf-1"
    with pytest.raises(ValueError):
        child_source_generation("  ")


# --- REQ-A8: replay and mixed workers ---------------------------------------

def test_legacy_untyped_signal_args_are_not_valid_progress():
    state = _parent()
    # The old `child_state_changed(new_state, reason)` two-string shape
    # never validates as a projection: no double-apply across generations.
    outcome = apply_agent_run_progress(state, ["running", "Agent is running."])
    assert outcome.disposition == "invalid"


def test_patch_identity_is_stable():
    assert AGENT_RUN_PROGRESS_PATCH_ID == "agent-run-progress-projection-v1"
    assert AGENT_RUN_PROGRESS_SIGNAL_NAME == "agent_run_progress"
    assert AGENT_RUN_PROGRESS_SIGNAL_NAME in (
        CLASSIFIED_CHILD_TO_PARENT_LIFECYCLE_SIGNALS
    )
    assert "child_state_changed" in CLASSIFIED_CHILD_TO_PARENT_LIFECYCLE_SIGNALS


def test_revision_digest_binding_is_immutable():
    payload = AgentRunProgressProjection.model_validate(_payload()).canonical_dict()
    assert projection_digest(payload) == projection_digest(copy.deepcopy(payload))
    mutated = dict(payload, summary="other")
    assert projection_digest(mutated) != projection_digest(payload)


# --- REQ-A9: docs and architecture separation -------------------------------

def test_architecture_rejects_unclassified_lifecycle_signals():
    assert (
        assert_classified_child_signal("agent_run_progress")
        == "agent_run_progress"
    )
    # Legitimate control/replay messages pass.
    assert_classified_child_signal("completion_signal")
    assert_classified_child_signal("managed_session_bound")
    with pytest.raises(ValueError):
        assert_classified_child_signal("agent_run_status_stream")
    with pytest.raises(ValueError):
        assert_classified_child_signal("omnigent_event_bus")


def test_retirement_inventory_keeps_compat_and_control_classified():
    inventory = " ".join(AGENT_RUN_PROGRESS_RETIREMENT_INVENTORY)
    assert "child_state_changed" in inventory
    assert "continuation" in inventory
    assert "cleanup" in inventory


def test_module_preserves_authority_separation_in_prose():
    import pathlib

    source = pathlib.Path(__file__).parent.parent.parent.parent.joinpath(
        "moonmind/schemas/agent_run_progress.py"
    ).read_text()
    assert "AgentRunResult" in source
    assert "terminal authority" in source
    assert "timeline" in source
