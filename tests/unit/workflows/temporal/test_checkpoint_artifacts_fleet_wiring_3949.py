"""Verify checkpoint artifacts-fleet wiring and isolation bounds (MoonMind#3949).

New checkpoint persistence already routes to the artifacts fleet behind
``checkpoint-branch-artifact-fleet-v1`` (#4032). The workflow-queue handlers
remain only for pre-cutover replay/in-flight compatibility and must not be
removed before their consumers drain. These tests prove the surviving
production wiring, inventory capabilities, gate compat removal on drain
evidence, and pin ordering/failure-containment — without a Temporal server,
database, or deployment probe.

Fixture replay (``test_checkpoint_queue_replay.py``) proves history
compatibility only; it is not deployed-drainage evidence.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

from temporalio import activity

from moonmind.workflows.temporal.activity_catalog import (
    AGENT_RUNTIME_FLEET,
    ARTIFACTS_FLEET,
    ARTIFACTS_TASK_QUEUE,
    INTEGRATIONS_FLEET,
    LLM_FLEET,
    SANDBOX_FLEET,
    build_default_activity_catalog,
)
from moonmind.workflows.temporal.activity_runtime import build_activity_bindings
from moonmind.workflows.temporal.workflow_registry import (
    checkpoint_branch_activity_handlers,
    workflow_fleet_activity_handlers,
)

# No explicit shard mark: tests/conftest.py owns every test under
# tests/unit/workflows/temporal/ to the temporal-boundary shard. Do not add
# pytest.mark.unit_fast here; it conflicts with that ownership.

WORKFLOW_SRC = (
    Path(__file__).resolve().parents[4]
    / "moonmind/workflows/temporal/workflows/checkpoint_branch_turn.py"
)
AGENT_RUN_SRC = (
    Path(__file__).resolve().parents[4]
    / "moonmind/workflows/temporal/workflows/agent_run.py"
)
REGISTRY_SRC = (
    Path(__file__).resolve().parents[4]
    / "moonmind/workflows/temporal/workflow_registry.py"
)
TOPOLOGY_DOC = (
    Path(__file__).resolve().parents[4]
    / "docs/Temporal/ActivityCatalogAndWorkerTopology.md"
)
PRE_CUTOVER_FIXTURES = (
    Path(__file__).resolve().parents[3]
    / "fixtures/temporal/checkpoint_before_artifacts_fleet"
)

PERSISTENCE_TYPES = (
    "checkpoint_branch.turn.mark_running",
    "checkpoint_branch.turn.persist_terminal",
    "checkpoint_branch.turn.persist_terminal_rejection",
)

HELPER_TYPES = (
    "integration.resolve_adapter_metadata",
    "integration.get_activity_route",
    "integration.resolve_external_adapter",
    "integration.external_adapter_execution_style",
)


def _handler_names(handlers) -> set[str]:
    return {
        activity._Definition.must_from_callable(handler).name
        for handler in handlers
    }


# --- 1. Surviving production wiring ----------------------------------------


def test_patch_constant_selects_artifacts_queue_only_when_patched():
    from moonmind.workflows.temporal.workflows import checkpoint_branch_turn as mod

    assert (
        mod.CHECKPOINT_BRANCH_ARTIFACT_FLEET_PATCH
        == "checkpoint-branch-artifact-fleet-v1"
    )
    source = WORKFLOW_SRC.read_text()
    assert "def _persistence_route_options" in source
    assert '"task_queue": ARTIFACTS_TASK_QUEUE' in source
    # Unpatched histories keep their recorded queue behavior (empty override).
    block = source.split("def _persistence_route_options")[1].split("def ")[0]
    assert "return {}" in block


def test_all_persistence_scheduling_carries_route_options_via_ast():
    """Every checkpoint persistence schedule must honor the patch routing."""
    tree = ast.parse(WORKFLOW_SRC.read_text())
    scheduled: dict[str, list[ast.Call]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr == "execute_activity"
        ):
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        name = node.args[0].value
        if isinstance(name, str) and name.startswith("checkpoint_branch.turn."):
            scheduled.setdefault(name, []).append(node)
    assert set(scheduled) == set(PERSISTENCE_TYPES), scheduled.keys()
    for name, calls in scheduled.items():
        for call in calls:
            star_star = [
                kw.value
                for kw in call.keywords
                if kw.arg is None  # **kwargs spread
            ]
            as_source = ast.dump(call)
            # Either the direct route-options spread or the derived
            # activity_options dict built from it.
            assert (
                "_persistence_route_options" in as_source
                or "activity_options" in as_source
            ), f"{name} schedules without patch route options"
            assert star_star, f"{name} must spread route options"


def test_stable_operation_identity_timeouts_and_retry():
    """Operation IDs, timeouts, and retry survive the queue move unchanged."""
    from moonmind.workflows.temporal.workflows import checkpoint_branch_turn as mod

    assert mod._RETRY.maximum_attempts == 3
    catalog = build_default_activity_catalog()
    expected_timeouts = {
        "checkpoint_branch.turn.mark_running": (60, 180),
        "checkpoint_branch.turn.persist_terminal": (120, 300),
        "checkpoint_branch.turn.persist_terminal_rejection": (120, 300),
    }
    for activity_type, (start_close, schedule_close) in expected_timeouts.items():
        route = catalog.resolve_activity(activity_type)
        assert route.timeouts.start_to_close_seconds == start_close
        assert route.timeouts.schedule_to_close_seconds == schedule_close
        assert route.retries.max_attempts >= 3
    source = WORKFLOW_SRC.read_text()
    assert "checkpoint_branch.turn.mark_running" in source
    assert "checkpoint_branch.turn.persist_terminal" in source
    assert "checkpoint_branch.turn.persist_terminal_rejection" in source
    assert "terminalPayloadDigest" in source
    assert "requestedOutcome" in source


def test_catalog_routes_all_new_persistence_to_artifacts_fleet():
    catalog = build_default_activity_catalog()
    for activity_type in PERSISTENCE_TYPES:
        route = catalog.resolve_activity(activity_type)
        assert route.fleet == ARTIFACTS_FLEET
        assert route.task_queue == ARTIFACTS_TASK_QUEUE
        item = next(
            item
            for item in catalog.activities
            if item.activity_type == activity_type
        )
        assert item.capability_class == "artifacts"


def test_artifacts_worker_binds_the_same_handler_objects():
    catalog = build_default_activity_catalog()
    handlers = checkpoint_branch_activity_handlers()
    from moonmind.workflows.temporal.activity_catalog import TemporalActivityCatalog

    focused = TemporalActivityCatalog(
        activities=tuple(
            item for item in catalog.activities if item.activity_type in PERSISTENCE_TYPES
        ),
        fleets=catalog.fleets,
    )
    bindings = build_activity_bindings(focused, fleets=[ARTIFACTS_FLEET])
    assert {binding.handler for binding in bindings} == set(handlers)
    assert all(binding.task_queue == ARTIFACTS_TASK_QUEUE for binding in bindings)
    assert all(binding.fleet == ARTIFACTS_FLEET for binding in bindings)
    for fleet in (LLM_FLEET, SANDBOX_FLEET, INTEGRATIONS_FLEET, AGENT_RUNTIME_FLEET):
        assert build_activity_bindings(focused, fleets=[fleet]) == ()


def test_workflow_fleet_retention_is_marked_compatibility_only():
    handlers = workflow_fleet_activity_handlers()
    assert set(checkpoint_branch_activity_handlers()).issubset(set(handlers))
    doc = workflow_fleet_activity_handlers.__doc__ or ""
    assert "replay/in-flight compatibility" in doc
    source = REGISTRY_SRC.read_text()
    assert "No new calls" in source


# --- 2. Capability inventory ------------------------------------------------


def test_workflow_fleet_composition_is_four_helpers_plus_three_persistence():
    names = _handler_names(workflow_fleet_activity_handlers())
    assert names == set(HELPER_TYPES) | set(PERSISTENCE_TYPES)


def test_capability_inventory_separates_db_handlers_from_metadata_helpers():
    checkpoint_src = WORKFLOW_SRC.read_text()
    assert "async_session_maker" in checkpoint_src
    assert "CheckpointBranchService" in checkpoint_src
    # Each persistence implementation opens a DB session directly.
    assert checkpoint_src.count("async with async_session_maker()") >= 3
    helper_src = AGENT_RUN_SRC.read_text()
    assert "async_session_maker" not in helper_src
    assert "CheckpointBranchService" not in helper_src


# --- 3. History/retirement contract -----------------------------------------


def test_pre_cutover_fixtures_record_workflow_queue_without_patch_marker():
    for scenario in ("success", "canceled", "rejected"):
        data = json.loads((PRE_CUTOVER_FIXTURES / f"{scenario}.json").read_text())
        assert "checkpoint-branch-artifact-fleet-v1" not in json.dumps(data)
        scheduled = [
            event["activityTaskScheduledEventAttributes"]
            for event in data["events"]
            if "activityTaskScheduledEventAttributes" in event
            and event["activityTaskScheduledEventAttributes"]["activityType"][
                "name"
            ].startswith("checkpoint_branch.turn.")
        ]
        assert scheduled, scenario
        assert all(
            item["taskQueue"]["name"].startswith("checkpoint-branch-turn-")
            for item in scheduled
        ), scenario


def test_compat_removal_stays_gated_on_drain_not_on_fixture_replay():
    source = REGISTRY_SRC.read_text()
    assert "drain" in source
    # New writes route to artifacts while compat stays on the workflow queue:
    # fixture replay therefore cannot stand in for deployed drainage.
    catalog = build_default_activity_catalog()
    for activity_type in PERSISTENCE_TYPES:
        assert (
            catalog.resolve_activity(activity_type).task_queue
            == ARTIFACTS_TASK_QUEUE
        )


# --- 4. Ordering and failure containment ------------------------------------


def test_terminal_persistence_is_fenced_and_digest_guarded():
    source = WORKFLOW_SRC.read_text()
    # Both terminal writers fence on the locked turn execution.
    assert source.count("lock_turn_execution") >= 2
    assert 're.fullmatch(r"sha256:[0-9a-f]{64}"' in source
    # Rejection fallback carries the digest and the same route options.
    fallback = source.split("terminal_payload_digest = _sha256")[1].split(
        "async def _persist_cancellation_terminal"
    )[0]
    assert "persist_terminal_rejection" in fallback
    assert "requestedOutcome" in fallback
    assert "terminalPayloadDigest" in fallback
    assert "activity_options" in fallback


def test_cancellation_uses_abandon_shield_and_terminalizes():
    source = WORKFLOW_SRC.read_text()
    assert "CHECKPOINT_BRANCH_CANCELLATION_TERMINAL_PATCH" in source
    assert "ActivityCancellationType.ABANDON" in source
    assert "asyncio.shield(terminal_task)" in source
    assert "_persist_cancellation_terminal" in source


def test_idempotent_capture_checkpoint_keys_and_service_ownership():
    source = WORKFLOW_SRC.read_text()
    assert ":capture:after_execution" in source
    assert ":checkpoint:after_execution" in source
    from api_service.services import checkpoint_branch_service as svc

    assert hasattr(svc, "build_branch_turn_launch_idempotency_key")
    assert hasattr(svc.CheckpointBranchService, "mark_turn_running")
    assert hasattr(svc.CheckpointBranchService, "lock_turn_execution")


def test_failed_handoff_never_authors_a_second_mutator():
    source = WORKFLOW_SRC.read_text()
    assert "if terminal_handoff_started:\n                raise" in source
    assert "terminal_evidence_blocked" in source


# --- 5. Permission boundary and saturation ----------------------------------


def test_topology_docs_distinguish_routing_compat_and_final_state():
    doc = TOPOLOGY_DOC.read_text()
    assert "no new calls route there" in doc
    assert "replay/in-flight compatibility" in doc
    assert "queue separation is not privilege separation" in doc.lower()
    assert "checkpoint-branch-artifact-fleet-v1" in doc or "artifacts fleet" in doc.lower()


def test_retry_timeout_budgets_retain_progress_under_saturation():
    catalog = build_default_activity_catalog()
    for activity_type in PERSISTENCE_TYPES:
        route = catalog.resolve_activity(activity_type)
        assert route.retries.max_attempts >= 3
        assert 0 < route.timeouts.start_to_close_seconds <= 300
        assert 0 < route.timeouts.schedule_to_close_seconds <= 600


# --- 6. Command type and cancellation/retry routing (MoonMind#3949 scope 1) ---
#
# The persistence calls are regular Temporal Activities: the recorded
# command must be ActivityTaskScheduled (``workflow.execute_activity``),
# never a local-activity invocation, on every path including the
# cancellation shield and the mark-running retry entry.


def test_persistence_never_schedules_local_activities():
    source = WORKFLOW_SRC.read_text()
    assert "execute_local_activity(" not in source
    assert "start_local_activity(" not in source
    assert "workflow.start_activity(" not in source


def test_every_persistence_site_is_an_activity_command_with_route_options():
    """All three persistence call sites honor the patch routing.

    ``mark_running`` (run entry, retried on handoff failure),
    ``persist_terminal`` and the ``persist_terminal_rejection`` fallback
    (both inside ``_persist_terminal``) schedule ``execute_activity`` with
    the route-options spread. Cancellation reaches the same path through
    ``_persist_cancellation_terminal`` -> ``_persist_terminal``.
    """
    tree = ast.parse(WORKFLOW_SRC.read_text())
    scheduled: dict[str, list[tuple[str, ast.Call]]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.walk(node):
                if not isinstance(child, ast.Call):
                    continue
                func = child.func
                if not (
                    isinstance(func, ast.Attribute)
                    and func.attr == "execute_activity"
                ):
                    continue
                if not child.args or not isinstance(child.args[0], ast.Constant):
                    continue
                name = child.args[0].value
                if isinstance(name, str) and name.startswith(
                    "checkpoint_branch.turn."
                ):
                    scheduled.setdefault(name, []).append((node.name, child))
    assert set(scheduled) == set(PERSISTENCE_TYPES), scheduled.keys()
    assert len(scheduled["checkpoint_branch.turn.mark_running"]) == 1
    assert len(scheduled["checkpoint_branch.turn.persist_terminal"]) == 1
    assert len(scheduled["checkpoint_branch.turn.persist_terminal_rejection"]) == 1
    for name, sites in scheduled.items():
        for enclosing, call in sites:
            assert enclosing in ("run", "_persist_terminal"), (
                f"{name} scheduled outside the routed paths"
            )
            as_source = ast.dump(call)
            assert "_persistence_route_options" in as_source or (
                "activity_options" in as_source
            ), f"{name} schedules without patch route options"


def test_cancellation_terminalizes_through_the_routed_persist_path():
    source = WORKFLOW_SRC.read_text()
    assert "async def _persist_cancellation_terminal" in source
    # The shielded cancellation task delegates to _persist_terminal, which
    # owns the route options and the rejection fallback; cancellation never
    # schedules persistence directly and so cannot bypass the fleet queue.
    block = source.split("async def _persist_cancellation_terminal")[1].split(
        "@workflow.run"
    )[0]
    assert "self._persist_terminal(" in block
    assert "execute_activity" not in block
    assert "ActivityCancellationType.ABANDON" in source
    assert "asyncio.shield(terminal_task)" in source
