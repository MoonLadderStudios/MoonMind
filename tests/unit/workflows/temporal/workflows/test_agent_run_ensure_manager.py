"""Tests for AuthProfileManager auto-start in MoonMind.AgentRun.

The ``_ensure_manager_and_signal`` call in the managed-agent slot
acquisition path must use ``request_slot=True`` so that the auto-start
fallback fires when the AuthProfileManager workflow doesn't exist.
A bare ``manager_handle.signal("request_slot", ...)`` bypasses this
fallback and causes ExternalWorkflowExecutionNotFound crashes.
"""

import ast
import inspect
import textwrap

from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun


class TestEnsureManagerAutoStart:
    """Verify request_slot is routed through the auto-start fallback."""

    def test_ensure_manager_called_with_request_slot_true(self):
        """``_ensure_manager_and_signal`` must be called with
        ``request_slot=True`` so the auto-start fallback is triggered
        when the AuthProfileManager is missing."""
        source = textwrap.dedent(inspect.getsource(MoonMindAgentRun.run))
        tree = ast.parse(source)

        found_ensure_manager_call = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            # Match self._ensure_manager_and_signal(...)
            if not (
                isinstance(func, ast.Attribute)
                and func.attr == "_ensure_manager_and_signal"
            ):
                continue
            found_ensure_manager_call = True

            # Find the request_slot keyword arg
            request_slot_kw = [
                kw for kw in node.keywords if kw.arg == "request_slot"
            ]
            assert len(request_slot_kw) == 1, (
                "_ensure_manager_and_signal must have exactly one request_slot kwarg"
            )
            kw_value = request_slot_kw[0].value
            # Must be True (ast.Constant with value True)
            assert isinstance(kw_value, ast.Constant) and kw_value.value is True, (
                "_ensure_manager_and_signal must be called with request_slot=True "
                "so the auto-start fallback fires when the AuthProfileManager "
                "doesn't exist. Using request_slot=False bypasses auto-start "
                "and causes ExternalWorkflowExecutionNotFound crashes."
            )

        assert found_ensure_manager_call, (
            "Could not find _ensure_manager_and_signal call in MoonMindAgentRun.run"
        )

    def test_no_bare_request_slot_signal_after_ensure_manager(self):
        """There must be no bare ``manager_handle.signal("request_slot", ...)``
        between ``_ensure_manager_and_signal`` and ``slot_assigned_event``.
        All request_slot signals must go through the auto-start wrapper."""
        source = inspect.getsource(MoonMindAgentRun.run)
        lines = source.splitlines()

        # Find _ensure_manager_and_signal call
        ensure_idx = None
        slot_wait_idx = None
        for i, line in enumerate(lines):
            if "_ensure_manager_and_signal" in line and ensure_idx is None:
                ensure_idx = i
            if "slot_assigned_event" in line and "is_set" in line:
                slot_wait_idx = i
                break

        assert ensure_idx is not None, (
            "Could not find _ensure_manager_and_signal in run()"
        )
        assert slot_wait_idx is not None, (
            "Could not find slot_assigned_event.is_set in run()"
        )

        # Check that no bare request_slot signal exists between the two
        region = lines[ensure_idx:slot_wait_idx]
        bare_signals = [
            line for line in region
            if '"request_slot"' in line and "signal" in line
            and "_ensure_manager_and_signal" not in line
        ]
        assert len(bare_signals) == 0, (
            f"Found bare manager_handle.signal('request_slot', ...) between "
            f"_ensure_manager_and_signal and slot_assigned_event wait. "
            f"All request_slot signals must go through _ensure_manager_and_signal "
            f"for auto-start fallback. Offending lines: {bare_signals}"
        )
