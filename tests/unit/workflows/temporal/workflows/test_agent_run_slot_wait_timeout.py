"""Tests for provider-profile slot waiting behavior in MoonMind.AgentRun.

The awaiting state (waiting for a provider-profile slot) must not consume the
execution timeout budget.  ``overall_start`` is reset after slot acquisition
so the full timeout is available for actual execution.

The slot wait uses a timeout+retry loop. Recovery may ensure and re-request the
manager, but it must never terminate the singleton while credential consumers
may still be active.
"""

import inspect
import textwrap

from moonmind.workflows.temporal.workflows.agent_run import (
    ACCURATE_SLOT_WAIT_REASON_PATCH_ID,
    MANAGER_SLOT_WAIT_INSPECTION_PATCH_ID,
    NON_DESTRUCTIVE_SLOT_WAIT_RECOVERY_PATCH_ID,
    MoonMindAgentRun,
    RunStatus,
    _SLOT_WAIT_TIMEOUT_SECONDS,
    _SLOT_WAIT_MAX_RECOVERY_ATTEMPTS,
)

class TestSlotWaitRetryBehavior:
    """Verify the slot-wait code path uses a timeout+retry loop."""

    def test_slot_wait_has_timeout_in_patched_path(self):
        """The patched ``wait_condition`` call for slot assignment must have
        a timeout so that a stuck manager triggers bounded recovery."""
        import ast

        source = textwrap.dedent(inspect.getsource(MoonMindAgentRun.run))
        tree = ast.parse(source)

        # Find all calls to workflow.wait_condition where the lambda checks
        # slot_assigned_event.is_set.
        found_with_timeout = False
        found_without_timeout = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (
                isinstance(func, ast.Attribute)
                and func.attr == "wait_condition"
            ):
                continue
            if not node.args:
                continue
            first_arg = node.args[0]
            if not isinstance(first_arg, ast.Lambda):
                continue
            source_segment = ast.dump(first_arg)
            if "slot_assigned_event" not in source_segment:
                continue

            timeout_kwargs = [
                kw for kw in node.keywords if kw.arg == "timeout"
            ]
            if timeout_kwargs:
                found_with_timeout = True
            else:
                found_without_timeout = True

        assert found_with_timeout, (
            "The patched slot-wait path must use wait_condition with a timeout "
            "to detect and recover from an unavailable manager"
        )
        # The legacy (unpatched) path should still have no timeout
        assert found_without_timeout, (
            "The legacy (unpatched) slot-wait path must remain for replay "
            "compatibility"
        )

    def test_overall_start_reset_after_slot_acquisition(self):
        """After the slot-wait ``wait_condition``, the code must reset
        ``overall_start`` so that execution timeout starts fresh."""
        source = inspect.getsource(MoonMindAgentRun.run)
        lines = source.splitlines()

        # Find the slot_assigned_event wait_condition region.
        # Look for the last occurrence (closest to the actual wait exit).
        wait_indices = []
        for i, line in enumerate(lines):
            if "slot_assigned_event" in line and "is_set" in line:
                wait_indices.append(i)
        assert wait_indices, "Could not find slot_assigned_event is_set"

        # Look for overall_start reset within 20 lines of the last wait
        reset_found = False
        for wait_idx in wait_indices:
            for line in lines[wait_idx + 1 : wait_idx + 21]:
                if "overall_start" in line and "workflow.now()" in line:
                    reset_found = True
                    break
        assert reset_found, (
            "overall_start must be reset to workflow.now() shortly after "
            "the slot_assigned_event wait_condition so that awaiting time "
            "does not count against the execution timeout"
        )

    def test_pause_gate_blocks_launch_after_slot_acquisition(self):
        """A paused AgentRun must not cross from slot assignment into launch."""
        source = inspect.getsource(MoonMindAgentRun.run)
        slot_wait_index = source.index("slot_assigned_event.is_set()")
        pause_gate_index = source.rindex(
            "await workflow.wait_condition(lambda: not self._paused)"
        )
        launch_index = source.index("self.run_status = RunStatus.launching")

        assert slot_wait_index < pause_gate_index < launch_index

    def test_pause_gate_runs_before_slot_acquisition(self):
        """A paused AgentRun should not acquire a provider slot before resume."""
        source = inspect.getsource(MoonMindAgentRun.run)
        pre_slot_pause_index = source.index(
            "await workflow.wait_condition(lambda: not self._paused)"
        )
        slot_request_index = source.index("request_slot=True")

        assert pre_slot_pause_index < slot_request_index

    def test_overall_start_reset_after_paused_slot_wait(self):
        """Paused time after slot assignment must not consume execution budget."""
        source = inspect.getsource(MoonMindAgentRun.run)
        pause_gate_index = source.rindex(
            "await workflow.wait_condition(lambda: not self._paused)"
        )
        reset_index = source.index("overall_start = workflow.now()", pause_gate_index)
        launch_index = source.index("self.run_status = RunStatus.launching")

        assert pause_gate_index < reset_index < launch_index

    def test_pause_resume_update_toggles_agent_run_overlay(self):
        run = MoonMindAgentRun()
        run.run_status = RunStatus.awaiting_slot

        run.pause()
        assert run._paused is True

        run.resume()
        assert run._paused is False

    def test_slot_wait_constants_are_sane(self):
        """Verify the slot wait constants have reasonable values."""
        assert NON_DESTRUCTIVE_SLOT_WAIT_RECOVERY_PATCH_ID.endswith(
            "non-destructive-slot-wait-recovery-v1"
        )
        assert _SLOT_WAIT_TIMEOUT_SECONDS >= 60, (
            "Slot wait timeout should be at least 60 seconds to allow for "
            "normal manager processing delays"
        )
        assert _SLOT_WAIT_MAX_RECOVERY_ATTEMPTS >= 1, (
            "Must allow at least one manager recovery attempt"
        )
        assert _SLOT_WAIT_MAX_RECOVERY_ATTEMPTS <= 10, (
            "Too many recovery attempts would delay failure detection"
        )

    def test_recover_and_request_slot_method_exists(self):
        """The recovery helper must preserve singleton lease authority."""
        assert hasattr(MoonMindAgentRun, "_recover_and_request_slot"), (
            "MoonMindAgentRun must have _recover_and_request_slot for bounded "
            "non-destructive slot recovery"
        )

    def test_slot_timeout_inspects_manager_before_reset(self):
        """A responsive manager means the run is waiting on capacity, not a reset."""
        source = inspect.getsource(MoonMindAgentRun.run)

        assert "MANAGER_SLOT_WAIT_INSPECTION_PATCH_ID" in source
        assert (
            MANAGER_SLOT_WAIT_INSPECTION_PATCH_ID
            == "agent-run-slot-wait-manager-inspection-v1"
        )
        assert "provider_profile.manager_state" in inspect.getsource(
            MoonMindAgentRun._manager_state_for_slot_wait
        )
        assert source.index("_manager_state_for_slot_wait") < source.index(
            "_recover_and_request_slot"
        )
        assert "re-requesting without reset" in source
        assert "NON_DESTRUCTIVE_SLOT_WAIT_RECOVERY_PATCH_ID" in source

    def test_accurate_initial_wait_reason_is_replay_gated(self):
        source = inspect.getsource(MoonMindAgentRun.run)

        assert (
            ACCURATE_SLOT_WAIT_REASON_PATCH_ID
            == "agent-run-accurate-slot-wait-reason-v1"
        )
        assert "workflow.patched(ACCURATE_SLOT_WAIT_REASON_PATCH_ID)" in source
        assert "execution_profile_ref=request.execution_profile_ref" in source

    def test_runtime_selection_refreshes_wait_reason_after_inspection(self):
        source = inspect.getsource(MoonMindAgentRun.run)

        assert "refresh_waiting_reason = True" in source
        assert "not self.runtime_selection_updated_event.is_set()" in source
        assert "_inspected_provider_slot_waiting_reason" in source

    def test_recover_and_request_slot_uses_ensure_signal_fallback(self):
        """Recovery must tolerate the fresh-manager signal race without reset."""
        source = inspect.getsource(MoonMindAgentRun._recover_and_request_slot)

        assert "_ensure_manager_and_signal" in source
        assert "manager_handle.signal(\"request_slot\"" not in source
        assert "provider_profile.reset_manager" not in source

    def test_slot_timeout_probe_failure_falls_back_to_safe_recovery(self):
        """Inspection ambiguity must not authorize destructive manager reset."""
        source = inspect.getsource(MoonMindAgentRun.run)

        assert "except CancelledError:" in source
        assert "using non-destructive recovery" in source
        assert 'manager_state = {"running": False}' in source
        assert "_recover_and_request_slot" in source

    def test_healthy_manager_does_not_duplicate_pending_request(self):
        """If this workflow is already pending, timeout recovery must not re-signal."""
        source = inspect.getsource(MoonMindAgentRun.run)
        pending_index = source.index('manager_state.get("requester_pending") is True')
        rerequest_index = source.index("re-requesting without reset")

        assert pending_index < rerequest_index
        assert "continuing without reset or duplicate request" in source
        assert "requester_workflow_id=workflow.info().workflow_id" in source
