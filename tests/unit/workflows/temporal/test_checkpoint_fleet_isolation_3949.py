"""Verify workflow-fleet isolation and artifacts-fleet cutover.

MoonLadderStudios/MoonMind#3949: new checkpoint persistence already moved to
the artifacts fleet in #4032 via the ``checkpoint-branch-artifact-fleet-v1``
patch. This module proves the surviving production wiring, separates new-only
from compatibility capability needs, pins the drain-gated retirement contract,
and exercises ordering/failure containment without removing the retained
compatibility registration prematurely.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
from temporalio import activity

from moonmind.workflows.temporal.activity_catalog import (
    AGENT_RUNTIME_FLEET,
    ARTIFACTS_FLEET,
    ARTIFACTS_TASK_QUEUE,
    INTEGRATIONS_FLEET,
    LLM_FLEET,
    SANDBOX_FLEET,
    WORKFLOW_FLEET,
    build_default_activity_catalog,
)
from moonmind.workflows.temporal.activity_runtime import build_activity_bindings
from moonmind.workflows.temporal.workflow_registry import (
    CHECKPOINT_BRANCH_PERSISTENCE_ACTIVITY_TYPES,
    WORKFLOW_FLEET_NEW_ONLY_HELPER_ACTIVITY_TYPES,
    checkpoint_branch_activity_handlers,
    checkpoint_branch_persistence_contract,
    workflow_fleet_activity_handlers,
    workflow_fleet_capability_inventory,
)
from moonmind.workflows.temporal.workers import (
    build_worker_activity_bindings,
    build_worker_topology,
)


def _handler_names(handlers) -> set[str]:
    return {
        activity._Definition.must_from_callable(handler).name for handler in handlers
    }


def test_new_write_contract_pins_patch_and_artifacts_fleet():
    contract = checkpoint_branch_persistence_contract()

    assert (
        contract["patch_marker"] == "checkpoint-branch-artifact-fleet-v1"
    )
    assert contract["activity_types"] == CHECKPOINT_BRANCH_PERSISTENCE_ACTIVITY_TYPES
    assert contract["new_write"]["fleet"] == ARTIFACTS_FLEET
    assert contract["new_write"]["capability_class"] == "artifacts"
    # Fixture replay is compatibility evidence, never drain evidence.
    assert "not deployed-drain proof" in contract["drain_owner"]["fixture_role"]
    # Removal is explicitly not due while compatibility handlers are retained.
    assert contract["removal_blocked"] is True


def test_persistence_route_options_select_artifacts_queue_only_when_patched(
    monkeypatch,
):
    import moonmind.workflows.temporal.workflows.checkpoint_branch_turn as turn_module

    instance = turn_module.MoonMindCheckpointBranchTurnWorkflow()

    monkeypatch.setattr(
        turn_module.workflow, "patched", lambda _patch_id: True
    )
    assert instance._persistence_route_options() == {
        "task_queue": ARTIFACTS_TASK_QUEUE
    }

    monkeypatch.setattr(
        turn_module.workflow, "patched", lambda _patch_id: False
    )
    assert instance._persistence_route_options() == {}


@pytest.mark.asyncio
async def test_persist_terminal_schedules_artifacts_queue_with_stable_identity(
    monkeypatch,
):
    """Terminal + rejection fallback share queue, timeouts, retry, operation shape."""
    import moonmind.workflows.temporal.workflows.checkpoint_branch_turn as turn_module
    from moonmind.schemas.agent_runtime_models import AgentRunResult

    captured: list[tuple[str, dict, dict]] = []

    async def fake_execute_activity(
        activity_name: str, payload: object, **kwargs: object
    ):
        captured.append((activity_name, dict(payload), dict(kwargs)))
        if activity_name.endswith("persist_terminal"):
            raise RuntimeError("retention boundary rejected evidence")
        return {"deliveryOutcome": "blocked", "status": "blocked"}

    monkeypatch.setattr(
        turn_module.workflow, "patched", lambda _patch_id: True
    )
    monkeypatch.setattr(
        turn_module.workflow, "execute_activity", fake_execute_activity
    )

    instance = turn_module.MoonMindCheckpointBranchTurnWorkflow()
    payload = {
        "workflowId": "wf-3949",
        "branchId": "cbr-3949",
        "branchTurnId": "turn-3949",
        "principal": "tester",
        "sourceNamespace": "default",
        "sourceRunId": "run-3949",
    }
    result = AgentRunResult(summary="ok")

    outcome = await instance._persist_terminal(
        payload, result=result, outcome="succeeded"
    )

    names = [name for name, _payload, _kwargs in captured]
    assert names == [
        "checkpoint_branch.turn.persist_terminal",
        "checkpoint_branch.turn.persist_terminal_rejection",
    ]
    for _name, _payload, kwargs in captured:
        # New histories schedule the artifacts queue, never the workflow queue.
        assert kwargs["task_queue"] == ARTIFACTS_TASK_QUEUE
        assert kwargs["start_to_close_timeout"] == timedelta(minutes=2)
        assert kwargs["schedule_to_close_timeout"] == timedelta(minutes=5)
        assert kwargs["retry_policy"] is turn_module._RETRY
        assert turn_module._RETRY.maximum_attempts == 3
    # Rejection fallback carries only the digest, never rejected values.
    _name, rejection_payload, _kwargs = captured[1]
    assert set(rejection_payload) == {
        "workflowId",
        "branchId",
        "branchTurnId",
        "principal",
        "sourceNamespace",
        "sourceRunId",
        "requestedOutcome",
        "terminalPayloadDigest",
    }
    assert rejection_payload["terminalPayloadDigest"].startswith("sha256:")
    assert outcome["deliveryOutcome"] == "blocked"


@pytest.mark.asyncio
async def test_persist_terminal_keeps_recorded_queue_when_unpatched(monkeypatch):
    import moonmind.workflows.temporal.workflows.checkpoint_branch_turn as turn_module
    from moonmind.schemas.agent_runtime_models import AgentRunResult

    captured: list[tuple[str, dict]] = []

    async def fake_execute_activity(
        activity_name: str, payload: object, **kwargs: object
    ):
        captured.append((activity_name, dict(kwargs)))
        return {"status": "ok"}

    monkeypatch.setattr(
        turn_module.workflow, "patched", lambda _patch_id: False
    )
    monkeypatch.setattr(
        turn_module.workflow, "execute_activity", fake_execute_activity
    )

    instance = turn_module.MoonMindCheckpointBranchTurnWorkflow()
    await instance._persist_terminal(
        {
            "workflowId": "wf-3949",
            "branchId": "cbr-3949",
            "branchTurnId": "turn-3949",
            "principal": "tester",
            "sourceNamespace": "default",
            "sourceRunId": "run-3949",
        },
        result=AgentRunResult(summary="ok"),
        outcome="failed",
    )

    assert captured
    for _name, kwargs in captured:
        assert "task_queue" not in kwargs


def test_catalog_and_artifacts_worker_register_same_handlers_with_deps():
    """Catalog entries, artifacts bindings, and operation identity must agree."""
    from moonmind.workflows.temporal.activity_catalog import TemporalActivityCatalog

    catalog = build_default_activity_catalog()
    handlers = checkpoint_branch_activity_handlers()
    assert _handler_names(handlers) == set(
        CHECKPOINT_BRANCH_PERSISTENCE_ACTIVITY_TYPES
    )

    for activity_type in CHECKPOINT_BRANCH_PERSISTENCE_ACTIVITY_TYPES:
        route = catalog.resolve_activity(activity_type)
        assert route.fleet == ARTIFACTS_FLEET
        assert route.task_queue == ARTIFACTS_TASK_QUEUE
        assert route.capability_class == "artifacts"
        # Stable operation identity: timeouts, retry, generation-safe retries.
        assert route.timeouts.start_to_close_seconds > 0
        assert (
            route.timeouts.schedule_to_close_seconds
            >= route.timeouts.start_to_close_seconds
        )
        assert route.retries.max_attempts >= 1

    focused = TemporalActivityCatalog(
        activities=tuple(
            item
            for item in catalog.activities
            if item.activity_type in CHECKPOINT_BRANCH_PERSISTENCE_ACTIVITY_TYPES
        ),
        fleets=catalog.fleets,
    )
    bindings = build_activity_bindings(focused, fleets=[ARTIFACTS_FLEET])
    assert {binding.handler for binding in bindings} == set(handlers)
    assert all(binding.task_queue == ARTIFACTS_TASK_QUEUE for binding in bindings)
    assert all(binding.fleet == ARTIFACTS_FLEET for binding in bindings)

    # The same implementations serve the retained workflow-queue path.
    retained = workflow_fleet_activity_handlers()
    assert set(handlers).issubset(set(retained))
    # No capable fleet other than artifacts binds these activities.
    for fleet in (LLM_FLEET, SANDBOX_FLEET, INTEGRATIONS_FLEET, AGENT_RUNTIME_FLEET):
        assert build_activity_bindings(focused, fleets=[fleet]) == ()


def test_mark_running_and_terminal_share_one_route_helper():
    """All persistence call sites must spread the same route options."""
    import inspect

    import moonmind.workflows.temporal.workflows.checkpoint_branch_turn as turn_module

    source = inspect.getsource(turn_module.MoonMindCheckpointBranchTurnWorkflow)
    # mark_running (run) and the shared _persist_terminal helper (terminal
    # success/failure/cancellation/rejection paths) both spread the
    # patch-gated helper; the rejection fallback reuses the same
    # activity_options, and the step_checkpoint.create_v2 write already
    # targets the artifacts queue directly.
    assert source.count("_persistence_route_options()") >= 2
    assert "task_queue=ARTIFACTS_TASK_QUEUE" in source


def test_capability_inventory_separates_new_only_from_compatibility():
    inventory = workflow_fleet_capability_inventory()

    assert tuple(inventory["new_only_helpers"]["activity_types"]) == (
        WORKFLOW_FLEET_NEW_ONLY_HELPER_ACTIVITY_TYPES
    )
    assert inventory["new_only_helpers"]["capability_class"] == "workflow"
    assert inventory["retained_compatibility_handlers"]["capability_class"] == (
        "artifacts"
    )
    assert "queue separation is not privilege separation" in inventory["boundary"][
        "note"
    ]

    catalog = build_default_activity_catalog()
    # Only the adapter-metadata helper is a catalog route; the other three
    # helpers are registry-bound workflow-queue activities (narrow exception).
    route = catalog.resolve_activity("integration.resolve_adapter_metadata")
    assert route.fleet == WORKFLOW_FLEET
    assert route.capability_class == "workflow"
    registry_names = _handler_names(workflow_fleet_activity_handlers())
    for activity_type in WORKFLOW_FLEET_NEW_ONLY_HELPER_ACTIVITY_TYPES:
        assert activity_type in registry_names

    workflow_topology = build_worker_topology(fleet=WORKFLOW_FLEET)
    assert workflow_topology.capabilities == ("workflow",)
    assert workflow_topology.privileges == ("temporal",)
    assert workflow_topology.required_secrets == ()
    assert "artifacts" in workflow_topology.forbidden_capabilities
    assert "docker_workload" in workflow_topology.forbidden_capabilities

    artifacts_topology = build_worker_topology(fleet=ARTIFACTS_FLEET)
    assert set(CHECKPOINT_BRANCH_PERSISTENCE_ACTIVITY_TYPES) <= set(
        artifacts_topology.activity_types
    )
    assert artifacts_topology.capabilities == ("artifacts",)
    assert "artifact_store" in artifacts_topology.privileges
    # The workflow fleet intentionally carries no side-effecting bindings via
    # the activity-fleet binding path; its helpers are registry-bound.
    assert build_worker_activity_bindings(fleet=WORKFLOW_FLEET) == ()


def test_workflow_and_artifacts_topologies_state_real_permission_boundary():
    workflow_topology = build_worker_topology(fleet=WORKFLOW_FLEET)
    artifacts_topology = build_worker_topology(fleet=ARTIFACTS_FLEET)

    assert workflow_topology.service_name == "temporal-worker-workflow"
    assert artifacts_topology.service_name == "temporal-worker-artifacts"
    assert workflow_topology.egress_policy == "temporal-only"
    assert artifacts_topology.egress_policy == "artifact-store-only"
    assert set(workflow_topology.task_queues).isdisjoint(
        set(artifacts_topology.task_queues)
    )
    assert ARTIFACTS_TASK_QUEUE in artifacts_topology.task_queues
    # Bounded concurrency on both fleets so saturation on one lane cannot be
    # mistaken for privilege separation; control/cleanup progress is retained
    # by queue separation plus independent activity budgets.
    assert workflow_topology.concurrency_limit is not None
    assert artifacts_topology.concurrency_limit is not None


def test_retirement_contract_keeps_compatibility_until_drain_is_verified():
    contract = checkpoint_branch_persistence_contract()
    retained_names = _handler_names(workflow_fleet_activity_handlers())

    for activity_type in CHECKPOINT_BRANCH_PERSISTENCE_ACTIVITY_TYPES:
        assert activity_type in retained_names

    # Pre-cutover fixtures prove replay compatibility: no patch marker and the
    # recorded workflow-queue scheduling behavior.
    fixtures = (
        Path(__file__).resolve().parents[3]
        / "fixtures/temporal/checkpoint_before_artifacts_fleet"
    )
    for scenario in ("success", "canceled", "rejected"):
        data = json.loads((fixtures / f"{scenario}.json").read_text())
        assert contract["patch_marker"] not in json.dumps(data)

    # Drain must use deployment probes, not fixture replay or a task-queue name.
    assert "Visibility" in contract["drain_owner"]["retained_histories"]
    assert "worker-versioning" in contract["drain_owner"]["retained_histories"]
    assert contract["removal_gate"].startswith("delete retained workflow-queue")


def test_unexpected_io_is_denied_by_fleet_capability_contract():
    """Workflow-only processing must not admit I/O capabilities by name."""
    workflow_topology = build_worker_topology(fleet=WORKFLOW_FLEET)

    for forbidden in (
        "artifacts",
        "llm",
        "sandbox",
        "agent_runtime",
        "docker_workload",
    ):
        assert forbidden in workflow_topology.forbidden_capabilities
    assert workflow_topology.required_secrets == ()
    assert workflow_topology.privileges == ("temporal",)


@pytest.fixture
def _ordering_branch_payload():
    return {
        "branchId": "cbr-3949",
        "source": {
            "workflowId": "wf-3949",
            "runId": "run-3949",
            "logicalStepId": "implement",
            "sourceExecutionOrdinal": 2,
            "checkpointBoundary": "after_execution",
            "checkpointRef": "artifact://checkpoint/after",
            "checkpointDigest": "sha256:checkpoint",
        },
        "label": "Isolation ordering probe",
        "workspacePolicy": "apply_previous_execution_diff_to_clean_baseline",
        "runtimeContextPolicy": "fresh_agent_run",
        "gitRepository": "repo://moonmind",
        "gitBaseBranch": "main",
        "gitBaseCommit": "abc123",
        "gitWorkBranch": "mm/wf-3949/implement/cbr-3949-probe",
        "createdBy": "MM-3949",
    }


@pytest.mark.asyncio
async def test_duplicate_terminal_delivery_is_idempotent(tmp_path, _ordering_branch_payload):
    """Duplicate persistence delivery cannot overwrite newer evidence."""
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from api_service.db.models import (
        Base,
        TemporalExecutionCanonicalRecord,
        TemporalWorkflowType,
    )
    from api_service.services.checkpoint_branch_service import CheckpointBranchService

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/iso-3949.db")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as session:
            session.add(
                TemporalExecutionCanonicalRecord(
                    workflow_id="wf-3949",
                    run_id="run-3949",
                    workflow_type=TemporalWorkflowType.USER_WORKFLOW,
                    entry="api",
                )
            )
            await session.commit()
            service = CheckpointBranchService(session)
            graph = await service.create_branch_graph(
                {
                    **_ordering_branch_payload,
                    "instructionRef": "artifact://instructions/root",
                    "instructionDigest": "sha256:root",
                    "idempotencyKey": "MM-3949:cbr-3949:create",
                }
            )
            turn_id = graph.turns[0].branch_turn_id
            first = await service.finalize_turn_execution(
                workflow_id="wf-3949",
                branch_id="cbr-3949",
                branch_turn_id=turn_id,
                outcome="succeeded",
                agent_result_ref="artifact://agent-result/1",
                diagnostics_ref="artifact://diagnostics/1",
                checkpoint_ref="artifact://checkpoint/1",
                checkpoint_digest="sha256:checkpoint-1",
                provider_session_id="session-1",
                terminal_ref="artifact://terminal/1",
                output_refs=["artifact://output/1"],
                terminal_disposition="delivered",
            )
            await session.commit()
            completed_at = first.completed_at

            # Exact duplicate replays the same durable turn without mutation.
            replayed = await service.finalize_turn_execution(
                workflow_id="wf-3949",
                branch_id="cbr-3949",
                branch_turn_id=turn_id,
                outcome="succeeded",
                agent_result_ref="artifact://agent-result/1",
                diagnostics_ref="artifact://diagnostics/1",
                checkpoint_ref="artifact://checkpoint/1",
                checkpoint_digest="sha256:checkpoint-1",
                provider_session_id="session-1",
                terminal_ref="artifact://terminal/1",
                output_refs=["artifact://output/1"],
                terminal_disposition="delivered",
            )
            assert replayed.completed_at == completed_at
            assert replayed.diagnostics["agentResultRef"] == (
                "artifact://agent-result/1"
            )

            # Stale/conflicting delivery is rejected and preserves the first
            # evidence instead of overwriting it or releasing cleanup early.
            with pytest.raises(ValueError, match="immutable terminal field"):
                await service.finalize_turn_execution(
                    workflow_id="wf-3949",
                    branch_id="cbr-3949",
                    branch_turn_id=turn_id,
                    outcome="failed",
                    agent_result_ref="artifact://agent-result/changed",
                    diagnostics_ref="artifact://diagnostics/1",
                    checkpoint_ref="artifact://checkpoint/1",
                    checkpoint_digest="sha256:checkpoint-1",
                    provider_session_id="session-1",
                )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_failed_persistence_handoff_stays_visible(tmp_path, _ordering_branch_payload):
    """A missing branch is a visible failure, never a silent terminal write."""
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from api_service.db.models import Base
    from api_service.services.checkpoint_branch_service import CheckpointBranchService

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/iso-3949-fail.db")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as session:
            service = CheckpointBranchService(session)
            with pytest.raises(ValueError, match="checkpoint branch not found"):
                await service.finalize_turn_execution(
                    workflow_id="wf-missing",
                    branch_id="cbr-missing",
                    branch_turn_id="turn-missing",
                    outcome="succeeded",
                    agent_result_ref="artifact://agent-result/1",
                    diagnostics_ref="artifact://diagnostics/1",
                )
    finally:
        await engine.dispose()
