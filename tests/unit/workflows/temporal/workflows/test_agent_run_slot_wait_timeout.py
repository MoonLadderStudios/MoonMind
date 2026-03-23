"""Tests for auth profile slot waiting behavior in MoonMind.AgentRun.

The awaiting state (waiting for an auth profile slot) must not consume the
execution timeout budget.  ``overall_start`` is reset after slot acquisition
so the full timeout is available for actual execution.
"""

import inspect
import textwrap

import pytest

from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun


class TestSlotWaitDoesNotConsumeTimeoutBudget:
    """Verify that the slot-wait code path resets ``overall_start``."""

    def test_wait_condition_has_no_timeout(self):
        """The ``wait_condition`` call for slot assignment must not pass a
        timeout argument, so that workflows wait indefinitely for a slot."""
        import ast

        source = textwrap.dedent(inspect.getsource(MoonMindAgentRun.run))
        tree = ast.parse(source)

        # Find all calls to workflow.wait_condition where the lambda checks
        # slot_assigned_event.is_set.
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            # Match workflow.wait_condition(...)
            if not (
                isinstance(func, ast.Attribute)
                and func.attr == "wait_condition"
            ):
                continue
            # Check if first arg is a lambda referencing slot_assigned_event
            if not node.args:
                continue
            first_arg = node.args[0]
            if not isinstance(first_arg, ast.Lambda):
                continue
            source_segment = ast.dump(first_arg)
            if "slot_assigned_event" not in source_segment:
                continue

            # This is the slot-wait call.  It must NOT have a timeout kwarg.
            timeout_kwargs = [
                kw for kw in node.keywords if kw.arg == "timeout"
            ]
            assert len(timeout_kwargs) == 0, (
                "wait_condition for slot_assigned_event must not have a "
                "timeout — awaiting should wait indefinitely"
            )

    def test_overall_start_reset_after_slot_acquisition(self):
        """After the slot-wait ``wait_condition``, the code must reset
        ``overall_start`` so that execution timeout starts fresh."""
        source = inspect.getsource(MoonMindAgentRun.run)
        lines = source.splitlines()

        # Find the slot_assigned_event wait_condition region.
        # The wait_condition call and the lambda may span multiple lines.
        wait_idx = None
        for i, line in enumerate(lines):
            if "slot_assigned_event" in line and "is_set" in line:
                wait_idx = i
                break
        assert wait_idx is not None, "Could not find slot_assigned_event is_set"

        # Look for overall_start reset within the next 10 lines
        reset_found = False
        for line in lines[wait_idx + 1 : wait_idx + 11]:
            if "overall_start" in line and "workflow.now()" in line:
                reset_found = True
                break
        assert reset_found, (
            "overall_start must be reset to workflow.now() shortly after "
            "the slot_assigned_event wait_condition so that awaiting time "
            "does not count against the execution timeout"
        )
