"""Tests for ProviderProfileManager auto-start in MoonMind.AgentRun.

The ``_ensure_manager_and_signal`` call in the managed-agent slot
acquisition path must use ``request_slot=True`` so that the auto-start
fallback fires when the ProviderProfileManager workflow doesn't exist.
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
        when the ProviderProfileManager is missing."""
        source = textwrap.dedent(inspect.getsource(MoonMindAgentRun.run))
        tree = ast.parse(source)

        found_request_slot_call = False
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

            # Find the request_slot keyword arg
            request_slot_kw = [
                kw for kw in node.keywords if kw.arg == "request_slot"
            ]
            assert len(request_slot_kw) == 1, (
                "_ensure_manager_and_signal must have exactly one request_slot kwarg"
            )
            kw_value = request_slot_kw[0].value
            if isinstance(kw_value, ast.Constant) and kw_value.value is True:
                found_request_slot_call = True

        assert found_request_slot_call, (
            "Could not find a _ensure_manager_and_signal(..., request_slot=True) "
            "call in MoonMindAgentRun.run"
        )

    def test_profile_sync_happens_before_slot_request_on_new_patch_path(self):
        """New managed runs must refresh manager profiles before requesting a slot."""
        source = textwrap.dedent(inspect.getsource(MoonMindAgentRun.run))
        tree = ast.parse(source)

        patched_block: ast.If | None = None
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            test = node.test
            if not (
                isinstance(test, ast.Call)
                and isinstance(test.func, ast.Attribute)
                and test.func.attr == "patched"
                and test.args
                and isinstance(test.args[0], ast.Name)
                and test.args[0].id == "SYNC_PROFILES_BEFORE_SLOT_REQUEST_PATCH_ID"
            ):
                continue
            patched_block = node
            break

        assert patched_block is not None, (
            "MoonMindAgentRun.run must patch-gate sync-before-slot ordering"
        )

        sync_line = None
        request_line = None
        for statement in patched_block.body:
            nodes = ast.walk(statement)
            for node in nodes:
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not isinstance(func, ast.Attribute):
                    continue
                if func.attr == "_sync_manager_profiles":
                    sync_line = node.lineno
                if func.attr != "_ensure_manager_and_signal":
                    continue
                request_slot_kw = [
                    kw for kw in node.keywords if kw.arg == "request_slot"
                ]
                if (
                    request_slot_kw
                    and isinstance(request_slot_kw[0].value, ast.Constant)
                    and request_slot_kw[0].value.value is True
                ):
                    request_line = node.lineno
            if sync_line is not None and request_line is not None:
                break

        assert sync_line is not None, "Patched path must call _sync_manager_profiles"
        assert request_line is not None, (
            "Patched path must request a slot through _ensure_manager_and_signal"
        )
        assert sync_line < request_line, (
            "Provider profiles must sync before request_slot to avoid stale "
            "manager assignments."
        )

    def test_ensure_manager_without_slot_starts_manager_activity(self):
        """The helper must auto-start the manager before the pre-slot sync signal."""
        source = textwrap.dedent(
            inspect.getsource(MoonMindAgentRun._ensure_manager_and_signal)
        )
        tree = ast.parse(source)

        found_no_slot_branch = False
        found_ensure_activity = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            test = node.test
            if not (
                isinstance(test, ast.UnaryOp)
                and isinstance(test.op, ast.Not)
                and isinstance(test.operand, ast.Name)
                and test.operand.id == "request_slot"
            ):
                continue
            found_no_slot_branch = True
            for child in ast.walk(node):
                if not isinstance(child, ast.Constant):
                    continue
                if child.value == "provider_profile.ensure_manager":
                    found_ensure_activity = True
                    break

        assert found_no_slot_branch, (
            "_ensure_manager_and_signal must handle request_slot=False"
        )
        assert found_ensure_activity, (
            "request_slot=False must call provider_profile.ensure_manager before "
            "returning a manager handle."
        )

    def test_no_bare_request_slot_signal_after_ensure_manager(self):
        """There must be no bare ``manager_handle.signal("request_slot", ...)``
        between ``_ensure_manager_and_signal`` and ``slot_assigned_event``.
        All request_slot signals must go through the auto-start wrapper."""
        source = textwrap.dedent(inspect.getsource(MoonMindAgentRun.run))
        tree = ast.parse(source)

        # Collect line numbers of key landmarks and bare signal calls.
        ensure_manager_line = None
        slot_wait_line = None
        bare_signal_lines: list[int] = []

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute):
                continue

            # Landmark: self._ensure_manager_and_signal(...)
            if func.attr == "_ensure_manager_and_signal" and ensure_manager_line is None:
                ensure_manager_line = node.lineno

            # Landmark: ...slot_assigned_event.is_set()
            if func.attr == "is_set" and isinstance(func.value, ast.Attribute):
                if "slot_assigned_event" in ast.dump(func.value):
                    slot_wait_line = node.lineno

            # Detect any .signal("request_slot", ...) call
            if func.attr == "signal" and node.args:
                first_arg = node.args[0]
                if isinstance(first_arg, ast.Constant) and first_arg.value == "request_slot":
                    bare_signal_lines.append(node.lineno)

        assert ensure_manager_line is not None, (
            "Could not find _ensure_manager_and_signal call in run()"
        )
        assert slot_wait_line is not None, (
            "Could not find slot_assigned_event.is_set call in run()"
        )

        # Any bare .signal("request_slot") between the two landmarks is a violation.
        violations = [
            ln for ln in bare_signal_lines
            if ensure_manager_line < ln < slot_wait_line
        ]
        assert len(violations) == 0, (
            f"Found bare .signal('request_slot', ...) calls at lines {violations} "
            f"between _ensure_manager_and_signal (line {ensure_manager_line}) and "
            f"slot_assigned_event wait (line {slot_wait_line}). "
            f"All request_slot signals must go through _ensure_manager_and_signal "
            f"for auto-start fallback."
        )

    def test_ensure_manager_propagates_exact_execution_profile_ref(self):
        """Managed runs with an explicit profile must pass it into slot acquisition."""
        source = textwrap.dedent(inspect.getsource(MoonMindAgentRun.run))
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (
                isinstance(func, ast.Attribute)
                and func.attr == "_ensure_manager_and_signal"
            ):
                continue

            execution_profile_kw = [
                kw for kw in node.keywords if kw.arg == "execution_profile_ref"
            ]
            assert len(execution_profile_kw) == 1, (
                "_ensure_manager_and_signal must receive execution_profile_ref "
                "so exact provider-profile selections are preserved."
            )
            kw_value = execution_profile_kw[0].value
            assert isinstance(kw_value, ast.Attribute), (
                "execution_profile_ref should be forwarded from request.execution_profile_ref"
            )
            assert kw_value.attr == "execution_profile_ref"
            return

        raise AssertionError(
            "Could not find _ensure_manager_and_signal call in MoonMindAgentRun.run"
        )

    def test_manager_signal_payload_carries_execution_profile_ref(self):
        """The auto-start helper must include execution_profile_ref in request_slot payloads."""
        source = textwrap.dedent(
            inspect.getsource(MoonMindAgentRun._ensure_manager_and_signal)
        )
        tree = ast.parse(source)

        found_guard = False
        found_payload_write = False
        for node in ast.walk(tree):
            if isinstance(node, ast.If):
                test = node.test
                if isinstance(test, ast.Name) and test.id == "execution_profile_ref":
                    found_guard = True
                    for child in node.body:
                        if not isinstance(child, ast.Assign):
                            continue
                        for target in child.targets:
                            if not isinstance(target, ast.Subscript):
                                continue
                            if not (
                                isinstance(target.value, ast.Name)
                                and target.value.id == "signal_payload"
                            ):
                                continue
                            slice_node = target.slice
                            if isinstance(slice_node, ast.Constant) and slice_node.value == "execution_profile_ref":
                                found_payload_write = True
        assert found_guard, (
            "_ensure_manager_and_signal must guard on execution_profile_ref "
            "before shaping the request_slot payload."
        )
        assert found_payload_write, (
            "_ensure_manager_and_signal must include signal_payload['execution_profile_ref'] "
            "so the ProviderProfileManager can honor exact profile selections."
        )
