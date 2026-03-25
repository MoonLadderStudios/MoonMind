"""Tests for auth profile slot waiting behavior in MoonMind.AgentRun.

The awaiting state (waiting for an auth profile slot) must not consume the
execution timeout budget.  ``overall_start`` is reset after slot acquisition
so the full timeout is available for actual execution.

The slot wait uses a timeout+retry loop: if the manager doesn't respond within
``_SLOT_WAIT_TIMEOUT_SECONDS``, the AgentRun resets the manager and re-requests.
"""

import inspect
import textwrap

from moonmind.workflows.temporal.workflows.agent_run import (
    MoonMindAgentRun,
    _SLOT_WAIT_TIMEOUT_SECONDS,
    _SLOT_WAIT_MAX_RESETS,
)


class TestSlotWaitRetryBehavior:
    """Verify the slot-wait code path uses a timeout+retry loop."""

    def test_slot_wait_has_timeout_in_patched_path(self):
        """The patched ``wait_condition`` call for slot assignment must have
        a timeout so that a stuck manager triggers a reset."""
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
            "to detect and recover from a stuck manager"
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

    def test_slot_wait_constants_are_sane(self):
        """Verify the slot wait constants have reasonable values."""
        assert _SLOT_WAIT_TIMEOUT_SECONDS >= 60, (
            "Slot wait timeout should be at least 60 seconds to allow for "
            "normal manager processing delays"
        )
        assert _SLOT_WAIT_MAX_RESETS >= 1, (
            "Must allow at least one manager reset attempt"
        )
        assert _SLOT_WAIT_MAX_RESETS <= 10, (
            "Too many reset attempts would delay failure detection"
        )

    def test_reset_and_request_slot_method_exists(self):
        """The _reset_and_request_slot helper must exist for the retry loop."""
        assert hasattr(MoonMindAgentRun, "_reset_and_request_slot"), (
            "MoonMindAgentRun must have _reset_and_request_slot method "
            "for resetting a stuck manager during slot acquisition"
        )
